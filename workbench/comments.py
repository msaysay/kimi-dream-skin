"""评论区：收评论、按规则生成回复、（可选）自动发出去。

这是转化路径的第 4 跳「关注 → 私域」：结尾说「评论区打 skill 领」，观众打了，这里自动回复领取方式；
同时把评论里的提问挑出来，喂给下一周的选题计划（答疑型）。

设计参考了 3441293738/creatorhub 的思路（规则 + 模板库 + 同义替换 + 先出草稿 + 每日上限），代码是重写的。
默认只生成草稿，你点「发送」或在规则里打开「自动发送」才会真的发；小红书永远只出草稿（自动化风控最严）。
抓评论和发回复都依赖各平台创作者后台的页面，没有公开文档，平台改版后可能需要调整。
"""
import hashlib
import json
import os
import random
import re
import time

from . import collect, config, method, review
from .db import rows
from .planner import QUESTION

COMMENT_PAGES = {
    "douyin": os.environ.get("WORKBENCH_COMMENT_DOUYIN", collect.URLS["douyin"] + "/creator-micro/interaction/comment-management"),
    "channels": os.environ.get("WORKBENCH_COMMENT_CHANNELS", collect.URLS["channels"] + "/platform/comment"),
}
AUTO_SEND_PLATFORMS = ("douyin", "channels")
SPINTAX = re.compile(r"\{([^{}|]+(?:\|[^{}|]*)+)\}")


def now_ms():
    return int(time.time() * 1000)


# ---------- 规则 ----------
def default_rule(profile):
    kw = profile.get("asset_keyword") or "skill"
    return {"name": f"评论区打「{kw}」领资料", "platforms": "douyin,channels,xhs", "keywords": kw, "exclude": "",
            "templates": [
                "{收到|安排上了|来啦}～{asset}在{path}，{按步骤来就能跑通|有卡住的地方随时问}",
                "{已经准备好了|这就给你}，去{path}领{asset}{，记得先关注|}{～|！}",
                "{nick}{收到|好嘞}，{asset}放在{path}了{，有问题评论区接着问|}",
            ], "use_ai": 0, "auto_send": 0, "daily_cap": 30, "enabled": 1, "is_intent": 1}


def ensure_default_rule(con):
    if not rows(con, "SELECT 1 FROM comment_rules LIMIT 1"):
        save_rule(con, default_rule(method.load_profile()))


def save_rule(con, r):
    keywords = ",".join(k.strip() for k in re.split(r"[,，\s]+", r.get("keywords") or "") if k.strip())
    if not keywords:
        raise ValueError("至少填一个触发关键词")
    templates = [t.strip() for t in (r.get("templates") or []) if t and t.strip()]
    if not templates and not r.get("use_ai"):
        raise ValueError("至少写一条回复模板，或者打开「大模型生成」")
    platforms = ",".join(p for p in re.split(r"[,，\s]+", r.get("platforms") or "") if p in config.PLATFORMS) or ",".join(config.PLATFORMS)
    vals = (str(r.get("name") or "自动回复")[:40], platforms, keywords, str(r.get("exclude") or ""), json.dumps(templates, ensure_ascii=False),
            int(bool(r.get("use_ai"))), int(bool(r.get("auto_send"))), max(1, min(200, int(r.get("daily_cap") or 20))),
            int(bool(r.get("enabled", 1))), int(bool(r.get("is_intent"))))
    with con:
        if r.get("id"):
            con.execute("""UPDATE comment_rules SET name=?, platforms=?, keywords=?, exclude=?, templates=?, use_ai=?, auto_send=?,
                           daily_cap=?, enabled=?, is_intent=? WHERE id=?""", (*vals, int(r["id"])))
            return int(r["id"])
        return con.execute("""INSERT INTO comment_rules (name, platforms, keywords, exclude, templates, use_ai, auto_send, daily_cap,
                              enabled, is_intent, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (*vals, now_ms())).lastrowid


def list_rules(con):
    out = rows(con, "SELECT * FROM comment_rules ORDER BY id")
    for r in out:
        r["templates"] = json.loads(r["templates"] or "[]")
    return out


def match(rule, platform, text):
    if not rule["enabled"] or platform not in rule["platforms"].split(","):
        return False
    low = (text or "").lower()
    if any(w and w.lower() in low for w in re.split(r"[,，\s]+", rule["exclude"] or "")):
        return False
    return any(k.lower() in low for k in rule["keywords"].split(",") if k)


def render(template, nick, profile, rnd=random):
    """展开 {甲|乙|丙} 同义替换并填变量：每条回复都不一样，平台对同一句话反复刷屏很敏感。"""
    text = template
    for _ in range(30):
        m = SPINTAX.search(text)
        if not m:
            break
        text = text[:m.start()] + rnd.choice(m.group(1).split("|")) + text[m.end():]
    text = (text.replace("{nick}", f"@{nick} " if nick else "").replace("{asset}", profile.get("asset_desc", "").replace("（待确认）", ""))
            .replace("{path}", profile.get("asset_path", "").replace("（待确认）", "")).replace("{kw}", profile.get("asset_keyword", "")))
    return re.sub(r"\s{2,}", " ", text).strip()[:200]


def ai_reply(comment, profile):
    prompt = (f"你在替账号「{profile.get('account')}」回复自己作品下的一条评论。\n"
              f"作品：{comment.get('post_title') or ''}\n评论：{comment['text']}\n"
              f"如果对方想领资料：资料是 {profile.get('asset_desc')}，领取方式是 {profile.get('asset_path')}。\n"
              "要求：口语化、30 字以内、不要引号和解释；不要留微信号、手机号或外链；回答不了就请对方说具体卡在哪一步。")
    text = review.chat([{"role": "user", "content": prompt}], max_tokens=120).strip().strip('"“”')
    if not text:
        raise RuntimeError("大模型返回空回复")
    return text[:200]


def is_question(text):
    return bool(QUESTION.search(text or ""))


def process(con, log=print):
    """给新评论匹配规则、生成回复草稿；打开了自动发送的排进发送队列。返回 (草稿数, 待发送数)。"""
    profile = method.load_profile()
    rules = list_rules(con)
    today = int(time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)) * 1000)
    sent_today = {r["rule_id"]: r["n"] for r in rows(con, "SELECT rule_id, COUNT(*) AS n FROM comments WHERE replied_at>=? GROUP BY rule_id", today)}
    drafted = queued = 0
    own = (profile.get("account") or "").strip()
    for c in rows(con, "SELECT * FROM comments WHERE state='new' ORDER BY ctime"):
        if own and c["nick"] == own:
            with con:
                con.execute("UPDATE comments SET state='self' WHERE platform=? AND comment_id=?", (c["platform"], c["comment_id"]))
            continue
        rule = next((r for r in rules if match(r, c["platform"], c["text"])), None)
        if rule is None:
            continue  # 没命中规则的留在「待处理」，提问会进下周选题
        reply = ""
        if rule["use_ai"]:
            try:
                reply = ai_reply(c, profile)
            except Exception as e:
                log(f"大模型生成回复失败，改用模板：{e}")
        if not reply and rule["templates"]:
            reply = render(random.choice(rule["templates"]), c["nick"], profile)
        if not reply:
            continue
        auto = rule["auto_send"] and c["platform"] in AUTO_SEND_PLATFORMS and sent_today.get(rule["id"], 0) + queued < rule["daily_cap"]
        state = "queued" if auto else "draft"
        with con:
            con.execute("UPDATE comments SET state=?, rule_id=?, reply=? WHERE platform=? AND comment_id=?",
                        (state, rule["id"], reply, c["platform"], c["comment_id"]))
        queued += auto
        drafted += not auto
    return drafted, queued


def upsert(con, platform, account, items):
    """写入抓到或粘贴的评论；已经存在的不覆盖状态。返回新增条数。"""
    new = 0
    with con:
        for it in items:
            if not it.get("text"):
                continue
            cid = str(it.get("comment_id") or hashlib.md5(f"{it.get('post_id')}|{it.get('nick')}|{it['text']}".encode()).hexdigest()[:16])
            cur = con.execute("""INSERT OR IGNORE INTO comments (platform, comment_id, account, post_id, post_title, nick, text, ctime, fetched_at, is_question)
                                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
                              (platform, cid, account, str(it.get("post_id") or ""), it.get("post_title") or "", it.get("nick") or "",
                               it["text"][:1000], it.get("ctime") or now_ms(), now_ms(), int(is_question(it["text"]))))
            new += cur.rowcount
            if it.get("post_title") and it.get("post_id"):
                con.execute("UPDATE comments SET post_title=? WHERE platform=? AND post_id=? AND (post_title='' OR post_title IS NULL)",
                            (it["post_title"], platform, str(it["post_id"])))
    return new


def import_text(con, platform, account, post_id, text):
    """手动粘贴：一行一条评论，可写成「昵称：内容」。"""
    post = rows(con, "SELECT title FROM posts WHERE platform=? AND post_id=?", platform, post_id)
    items = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        nick, sep, body = line.replace(":", "：").partition("：")
        if not sep or len(nick) > 20:
            nick, body = "", line
        items.append({"post_id": post_id, "post_title": post[0]["title"] if post else "", "nick": nick.strip(), "text": body.strip()})
    return upsert(con, platform, account, items)


# ---------- 浏览器：抓评论 ----------
ID_KEYS = ("cid", "comment_id", "commentId", "id")
TEXT_KEYS = ("text", "content", "commentContent", "comment_text")
POST_KEYS = ("aweme_id", "item_id", "objectId", "feedId", "object_id", "note_id")
TIME_KEYS = ("create_time", "createTime", "commentCreatetime", "create_at", "ctime")


def first(d, keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def nick_of(d):
    for k in ("user", "author", "user_info", "commentUser"):
        if isinstance(d.get(k), dict):
            n = first(d[k], ("nickname", "nick_name", "nickName", "name"))
            if n:
                return n
    return first(d, ("nickname", "nick_name", "nickName", "commentNickname", "user_name"))


def dig(obj, out, post_hint=None):
    """在任意 JSON 里找「像评论的字典」：有编号、有文字。"""
    if isinstance(obj, dict):
        pid = first(obj, POST_KEYS) or post_hint
        cid, text = first(obj, ID_KEYS), first(obj, TEXT_KEYS)
        if cid and isinstance(text, str) and (first(obj, ("cid", "comment_id", "commentId")) or nick_of(obj)):
            t = first(obj, TIME_KEYS)
            t = int(t) * (1000 if t and int(t) < 10 ** 11 else 1) if str(t or "").isdigit() else None
            out.append({"comment_id": str(cid), "text": text, "nick": nick_of(obj) or "", "post_id": str(pid or ""), "ctime": t})
        for v in obj.values():
            dig(v, out, pid)
    elif isinstance(obj, list):
        for v in obj:
            dig(v, out, post_hint)
    return out


def fetch(platform, account, log=print, state_file=None):
    """打开创作者后台的评论管理页，记下页面加载的评论接口，返回评论列表。原始响应存到 data/raw/comments/ 便于排查。"""
    if platform not in COMMENT_PAGES:
        raise RuntimeError(f"{config.PLATFORMS[platform]['name']}暂不支持自动抓评论：请在「评论」页手动粘贴")
    state_file = state_file or config.cookie_file(platform, account)
    if not state_file.exists():
        raise collect.NotLoggedIn(f"还没有登录{config.PLATFORMS[platform]['name']}账号「{account}」")
    raw_dir = config.DATA / "raw" / "comments"
    raw_dir.mkdir(parents=True, exist_ok=True)
    settings = config.load_settings()
    with collect.sync_playwright() as pw:
        launch = {"headless": bool(settings["headless"])}
        if settings["chrome_path"] or os.environ.get("WORKBENCH_CHROME"):
            launch["executable_path"] = os.environ.get("WORKBENCH_CHROME") or settings["chrome_path"]
        browser = pw.chromium.launch(**launch)
        try:
            context = browser.new_context(storage_state=str(state_file), viewport={"width": 1440, "height": 900})
            page = context.new_page()
            cap = collect.Capture(page)
            page.goto(COMMENT_PAGES[platform], wait_until="domcontentloaded")
            got = cap.wait(r"comment", 20000)
            collect.pause(page, 2000)
            for _ in range(3):
                page.mouse.wheel(0, 3000)
                collect.pause(page, 1200)
            payloads = [j for _, j in cap.take(r"comment")]
            (raw_dir / f"{platform}_{account}.json").write_text(json.dumps(payloads, ensure_ascii=False)[:5_000_000], encoding="utf-8")
            items = {}
            for j in payloads:
                for c in dig(j, []):
                    items[c["comment_id"]] = c
            if not got or not items:
                if "login" in page.url:
                    raise collect.NotLoggedIn("评论页跳到了登录页，请重新扫码登录")
                raise RuntimeError("没抓到评论：可能这几天没有新评论，或评论页改版了。原始响应已存到 data/raw/comments/")
            context.storage_state(path=str(state_file))
            log(f"抓到 {len(items)} 条评论")
            return list(items.values())
        finally:
            browser.close()


# ---------- 浏览器：发回复（实验性） ----------
FOCUSED_INPUT = """() => { const el = document.activeElement;
  return !!el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT' || el.isContentEditable); }"""


def send(platform, account, items, log=print, state_file=None):
    """在评论管理页逐条找到原评论 → 点「回复」→ 输入 → 发送。返回 [(comment_id, 成功?, 错误)]。

    这一步是实验性的：靠页面上的文字定位，不依赖具体的样式名；页面结构变了就会失败，失败的会退回草稿让你手动发。
    """
    if platform not in AUTO_SEND_PLATFORMS:
        raise RuntimeError(f"{config.PLATFORMS[platform]['name']}不自动发送回复，请复制草稿手动回复")
    state_file = state_file or config.cookie_file(platform, account)
    results = []
    settings = config.load_settings()
    with collect.sync_playwright() as pw:
        launch = {"headless": bool(os.environ.get("WORKBENCH_HEADLESS_SEND"))}  # 发回复默认显示窗口，方便你看着
        if settings["chrome_path"] or os.environ.get("WORKBENCH_CHROME"):
            launch["executable_path"] = os.environ.get("WORKBENCH_CHROME") or settings["chrome_path"]
        browser = pw.chromium.launch(**launch)
        try:
            context = browser.new_context(storage_state=str(state_file), viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.goto(COMMENT_PAGES[platform], wait_until="domcontentloaded")
            collect.pause(page, 3000)
            for i, c in enumerate(items):
                try:
                    reply_one(page, c["text"], c["reply"])
                    results.append((c["comment_id"], True, ""))
                    log(f"已回复「{c['text'][:15]}」")
                except Exception as e:
                    results.append((c["comment_id"], False, str(e)[:200]))
                    log(f"回复失败「{c['text'][:15]}」：{e}")
                if i < len(items) - 1:
                    collect.pause(page, random.randint(20000, 45000))  # 不要匀速连发
            context.storage_state(path=str(state_file))
        finally:
            browser.close()
    return results


def reply_one(page, comment_text, reply):
    snippet = comment_text.strip()[:20]
    for frame in page.frames:
        target = frame.get_by_text(snippet, exact=False)
        if not target.count():
            continue
        target = target.first
        target.scroll_into_view_if_needed(timeout=5000)
        target.hover(timeout=5000)
        # 从评论文字往外找，第一个包含「回复」按钮的容器就是这条评论
        box = target.locator("xpath=ancestor::*[.//*[normalize-space(text())='回复']][1]")
        box.get_by_text("回复", exact=True).first.click(timeout=5000)
        page.wait_for_timeout(600)
        if not frame.evaluate(FOCUSED_INPUT):
            editor = frame.locator("textarea:visible, [contenteditable=true]:visible, input[type=text]:visible").last
            editor.click(timeout=5000)
        page.keyboard.type(reply, delay=35)
        page.wait_for_timeout(500)
        # 先在这条评论的范围里找「发送 / 发布」，找不到再在整页找；最后才退到「回复」按钮（排除刚才点开回复框的那个）
        send_re = re.compile(r"^\s*(发送|发布)\s*$")
        for scope, pattern, need in ((box, send_re, 1), (frame, send_re, 1), (box, re.compile(r"^\s*回复\s*$"), 2)):
            button = scope.get_by_text(pattern)
            n = button.count()
            if n >= need:
                button.nth(n - 1).click(timeout=5000)
                break
        else:
            raise RuntimeError("回复框打开了，但没找到发送按钮")
        page.wait_for_timeout(1500)
        return
    raise RuntimeError("页面上没找到这条评论（可能被折叠、删除，或评论页改版）")


def mark_sent(con, platform, results):
    with con:
        for cid, ok, err in results:
            if ok:
                con.execute("UPDATE comments SET state='replied', replied_at=?, error=NULL WHERE platform=? AND comment_id=?", (now_ms(), platform, cid))
            else:
                con.execute("UPDATE comments SET state='draft', error=? WHERE platform=? AND comment_id=?", (err, platform, cid))
