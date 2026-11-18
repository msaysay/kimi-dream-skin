"""对标账号：盯住几个同赛道的号，找出它们的爆款，拆成结构，再拿结构来做自己的选题。

- 爆款：互动量（赞 + 评 + 转 + 藏）达到该账号最近作品中位数的 N 倍（默认 3 倍）。置顶作品不进中位数，避免老爆款抬高基线。
  各平台公开数据里的播放量多半拿不到，所以用互动量，不和自己账号的播放比。
- 拆解：给爆款打和自己作品同一套四层标签（封闭字典），可以让大模型先打一遍、写拆解，再人工改。
- 复刻：拆解过的爆款一键变成「创作」里的选题，结构标签带过去，数据依据写明出自哪个对标号、几倍。
"""
import json
import re
import statistics
import time

from . import config, contents, method, tikhub
from .db import rows

DAY = 86400 * 1000
STATUS = {"new": "未拆解", "studied": "已拆解", "used": "已复刻", "ignored": "忽略"}


def now_ms():
    return int(time.time() * 1000)


def interactions(s):
    return sum(s.get(k) or 0 for k in ("likes", "comments", "shares", "collects"))


# ---------- 账号 ----------
def add(con, platform, uid, name, followers=None, avatar=None, note="", grp=""):
    if platform not in config.PLATFORMS:
        raise ValueError("不认识的平台")
    with con:
        con.execute("""INSERT INTO benchmarks (platform, uid, name, avatar, followers, note, grp, added_at) VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(platform, uid) DO UPDATE SET name=excluded.name, followers=COALESCE(excluded.followers, followers),
                       avatar=COALESCE(excluded.avatar, avatar), grp=excluded.grp, enabled=1""",
                    (platform, uid, (name or uid)[:40], avatar, followers, (note or "")[:200], (grp or "")[:20], now_ms()))
    return rows(con, "SELECT id FROM benchmarks WHERE platform=? AND uid=?", platform, uid)[0]["id"]


def remove(con, bench_id):
    with con:
        ids = [r["post_id"] for r in rows(con, "SELECT post_id, platform FROM bench_posts WHERE bench_id=?", bench_id)]
        pf = (rows(con, "SELECT platform FROM benchmarks WHERE id=?", bench_id) or [{"platform": ""}])[0]["platform"]
        con.executemany("DELETE FROM bench_snaps WHERE platform=? AND post_id=?", [(pf, i) for i in ids])
        con.execute("DELETE FROM bench_posts WHERE bench_id=?", (bench_id,))
        con.execute("DELETE FROM benchmarks WHERE id=?", (bench_id,))


def save_posts(con, bench, posts):
    """写入一次读取的结果：作品基本信息只补不覆盖（保留拆解和状态），互动数每次追加一行快照。"""
    t = now_ms()
    with con:
        for p in posts:
            con.execute("""INSERT INTO bench_posts (platform, post_id, bench_id, title, url, cover, post_time, duration_sec, kind, is_top, first_seen)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(platform, post_id) DO UPDATE SET title=excluded.title, cover=excluded.cover, is_top=excluded.is_top,
                           duration_sec=COALESCE(excluded.duration_sec, duration_sec)""",
                        (bench["platform"], p["post_id"], bench["id"], p["title"][:500], p["url"], p["cover"], p["post_time"],
                         p["duration_sec"], p["kind"], p["is_top"], t))
            con.execute("INSERT OR REPLACE INTO bench_snaps VALUES (?,?,?,?,?,?,?,?)",
                        (bench["platform"], p["post_id"], t, p["likes"], p["comments"], p["shares"], p["collects"], p["plays"]))
        con.execute("UPDATE benchmarks SET last_fetch=?, last_error=NULL WHERE id=?", (t, bench["id"]))


def refresh(con, bench_id=None, log=print):
    """读取对标账号的最新作品。返回 (成功账号数, 新作品数)。"""
    pages = int(config.load_settings()["bench_pages"])
    targets = rows(con, "SELECT * FROM benchmarks WHERE enabled=1" + (" AND id=?" if bench_id else ""), *([bench_id] if bench_id else []))
    ok = new = 0
    for b in targets:
        before = con.execute("SELECT COUNT(*) FROM bench_posts WHERE bench_id=?", (b["id"],)).fetchone()[0]
        try:
            posts = tikhub.fetch_posts(b["platform"], b["uid"], pages)
            save_posts(con, b, posts)
            author = next((p.get("author") for p in posts if p.get("author")), None)
            if author and b["name"].startswith("v2_"):  # 视频号直接填 finder username 时，用作品里的作者昵称当名字
                with con:
                    con.execute("UPDATE benchmarks SET name=? WHERE id=?", (author[:40], b["id"]))
                b["name"] = author
            added = con.execute("SELECT COUNT(*) FROM bench_posts WHERE bench_id=?", (b["id"],)).fetchone()[0] - before
            new += added
            ok += 1
            log(f"{config.PLATFORMS[b['platform']]['name']}「{b['name']}」：读到 {len(posts)} 条，新增 {added} 条")
        except tikhub.TikHubError as e:
            with con:
                con.execute("UPDATE benchmarks SET last_error=? WHERE id=?", (str(e)[:300], b["id"]))
            log(f"{config.PLATFORMS[b['platform']]['name']}「{b['name']}」读取失败：{e}")
    return ok, new


# ---------- 手动粘贴：不在对标名单里的爆款，贴进来一样能拆 ----------
MANUAL_UID = "manual"


def paste(con, platform, text, url="", author="", stats=None):
    """贴一条爆款的文案 / 逐字稿进来。挂在一个不会被刷新的「手动粘贴」账号下，之后的拆解、复刻和监控到的爆款走同一套流程。"""
    if platform not in config.PLATFORMS:
        raise ValueError("不认识的平台")
    text = (text or "").strip()
    if len(text) < 10:
        raise ValueError("内容太短了：把标题、文案或口播逐字稿贴进来，至少十几个字")
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    title = (f"{author.strip()}：" if (author or "").strip() else "") + first[:80]
    bid = rows(con, "SELECT id FROM benchmarks WHERE platform=? AND uid=?", platform, MANUAL_UID)
    with con:
        if not bid:
            con.execute("INSERT INTO benchmarks (platform, uid, name, enabled, added_at) VALUES (?,?,?,0,?)", (platform, MANUAL_UID, "手动粘贴", now_ms()))
            bid = rows(con, "SELECT id FROM benchmarks WHERE platform=? AND uid=?", platform, MANUAL_UID)
        pid = f"m{now_ms()}"
        con.execute("""INSERT INTO bench_posts (platform, post_id, bench_id, title, url, post_time, kind, first_seen, text) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (platform, pid, bid[0]["id"], title, (url or "").strip()[:500], now_ms(), "video", now_ms(), text[:8000]))
        s = stats or {}
        num = lambda k: int(s[k]) if str(s.get(k) or "").strip().isdigit() else None
        con.execute("INSERT INTO bench_snaps VALUES (?,?,?,?,?,?,?,?)", (platform, pid, now_ms(), num("likes"), num("comments"), num("shares"), num("collects"), None))
    return platform, pid


def times(p):
    """一句话说明这条爆款的依据：监控到的写倍数，手动粘贴的照实说。"""
    if p.get("pasted"):
        return "手动粘贴的爆款" + (f"，互动 {p['inter']:,}" if p.get("inter") else "")
    return f"是该号中位数的 {p['ratio']:.1f} 倍" if p.get("ratio") else "对标作品"


# ---------- 爆款 ----------
def load(con, days=30):
    """所有对标作品（带最新互动、爆款系数、增长），以及每个账号的统计。"""
    ratio = float(config.load_settings()["bench_hit_ratio"])
    benches = {b["id"]: b for b in rows(con, "SELECT * FROM benchmarks ORDER BY id")}
    posts = rows(con, "SELECT * FROM bench_posts")
    snaps = {}
    for s in rows(con, "SELECT * FROM bench_snaps ORDER BY fetched_at"):
        snaps.setdefault((s["platform"], s["post_id"]), []).append(s)
    for p in posts:
        hist = snaps.get((p["platform"], p["post_id"])) or [{}]
        p["latest"] = hist[-1]
        p["inter"] = interactions(p["latest"])
        p["tags"] = json.loads(p["tags"] or "{}")
        # 增长：最近两次读取之间每天新增的互动
        if len(hist) >= 2 and hist[-1]["fetched_at"] > hist[-2]["fetched_at"]:
            p["growth_per_day"] = (interactions(hist[-1]) - interactions(hist[-2])) / ((hist[-1]["fetched_at"] - hist[-2]["fetched_at"]) / DAY)
        else:
            p["growth_per_day"] = None
        p["age_days"] = (now_ms() - p["post_time"]) / DAY if p["post_time"] else None
    for bid, b in benches.items():
        mine = [p for p in posts if p["bench_id"] == bid]
        base = [p["inter"] for p in mine if not p["is_top"] and p["inter"]]
        b["manual"] = b["uid"] == MANUAL_UID
        b["median"] = statistics.median(base) if len(base) >= 5 and not b["manual"] else None
        b["post_count"] = len(mine)
        for p in mine:
            p["pasted"] = b["manual"]
            p["ratio"] = p["inter"] / b["median"] if b["median"] else None
            # 手动贴进来的本来就是你认定的爆款，不用再算倍数
            p["hit"] = b["manual"] or bool(p["ratio"] and p["ratio"] >= ratio and not p["is_top"])
            p["bench_name"] = b["name"]
        b["hits"] = sum(p["hit"] for p in mine)
    since = now_ms() - days * DAY if days else 0
    hits = sorted([p for p in posts if p.get("hit") and (p["post_time"] or 0) >= since and p["status"] != "ignored"],
                  key=lambda p: (not p["pasted"] or p["status"] != "new", -(p["ratio"] or 0), -(p["first_seen"] or 0)))
    return {"benchmarks": list(benches.values()), "hits": hits, "ratio": ratio, "structure": structure(hits),
            "posts_by_key": {(p["platform"], p["post_id"]): p for p in posts}}


def structure(hits):
    """拆解过的爆款里，各个标签出现了几次：对标号验证过、值得优先试的结构。"""
    out = {}
    for field in ("motif", "archetype", "hook_open", "hook_end"):
        c = {}
        for p in hits:
            v = p["tags"].get(field)
            if v:
                c[v] = c.get(v, 0) + 1
        out[field] = sorted(c.items(), key=lambda x: -x[1])
    out["tagged"] = sum(1 for p in hits if p["tags"])
    return out


def mark(con, platform, post_id, status=None, tags=None, analysis=None):
    post = rows(con, "SELECT * FROM bench_posts WHERE platform=? AND post_id=?", platform, post_id)
    if not post:
        raise ValueError("没有这条对标作品")
    sets, vals = [], []
    if tags is not None:
        clean = {k: v for k, v in tags.items() if k in method.TAG_FIELDS and v in method.TAG_FIELDS[k][1]}
        sets.append("tags=?"); vals.append(json.dumps(clean, ensure_ascii=False))
        if clean and post[0]["status"] == "new" and status is None:
            status = "studied"
    if analysis is not None:
        sets.append("analysis=?"); vals.append(analysis)
    if status is not None:
        if status not in STATUS:
            raise ValueError("状态不对")
        sets.append("status=?"); vals.append(status)
    if sets:
        with con:
            con.execute(f"UPDATE bench_posts SET {', '.join(sets)} WHERE platform=? AND post_id=?", (*vals, platform, post_id))


ANALYZE_PROMPT = """你在帮一个短视频账号拆解对标账号的爆款。只根据给出的标题文案和数据判断，看不到画面的地方写「需看画面确认」。
先按封闭字典给这条爆款打标签，再写拆解，最后说明这个账号可以怎么借用这个结构（借结构，不照抄选题）。"""


def analyze_brief(con, platform, post_id, data=None):
    data = data or load(con, 0)
    p = data["posts_by_key"].get((platform, post_id))
    if not p:
        raise ValueError("没有这条对标作品")
    L = p["latest"]
    facts = {"平台": config.PLATFORMS[platform]["name"], "对标账号": p["bench_name"], "标题文案": p["title"][:600],
             **({"正文或口播逐字稿": p["text"][:6000]} if p.get("text") else {}),
             "时长秒": p["duration_sec"], "点赞": L.get("likes"), "评论": L.get("comments"), "转发": L.get("shares"), "收藏": L.get("collects"),
             "互动是该账号中位数的倍数": round(p["ratio"], 1) if p.get("ratio") else None, "原作品链接": p["url"]}
    return "\n\n".join([ANALYZE_PROMPT, method.dictionary_text(), method.profile_text(method.load_profile()),
                         "这条爆款：", json.dumps(facts, ensure_ascii=False, indent=1)])


def to_content(con, platform, post_id):
    """把一条拆解过的对标爆款变成自己的选题：结构标签带过去，进「创作」的选题阶段。"""
    data = load(con, 0)
    p = data["posts_by_key"].get((platform, post_id))
    if not p:
        raise ValueError("没有这条对标作品")
    if p["content_id"] and rows(con, "SELECT 1 FROM contents WHERE id=? AND archived=0", p["content_id"]):
        return p["content_id"]
    tags = dict(p["tags"])
    th = method.load_params()["thresholds"][platform]
    title = re.sub(r"#\S+", "", p["title"]).strip()[:24] or "对标爆款"
    basis = (f"对标 {config.PLATFORMS[platform]['name']}「{p['bench_name']}」爆款：互动 {p['inter']:,}，是该号中位数的 "
             f"{p['ratio']:.1f} 倍" if p["ratio"] else f"{config.PLATFORMS[platform]['name']}上手动粘贴的一条爆款" if p["pasted"]
             else f"对标 {config.PLATFORMS[platform]['name']}「{p['bench_name']}」作品")
    basis += "。借结构不借选题：选题要先过定位三闸，换成自己能兑现的内容"
    cid = contents.save(con, dict(tags, title=f"【对标复刻】借「{title}」的结构做一条", priority="P1", basis=basis, source="benchmark",
                                  targets=[{"metric": "follow_rate", "value": th["follow_rate"][1], "slot": "D3"}],
                                  acceptance="D3 关注率达到良好线即成立；同时对比原爆款的结构是否在自己账号上同样有效",
                                  notes=(p["analysis"] or ""), workflow=contents.pick_workflow(tags), verdict_from="对标爆款",
                                  experiment={"field": "benchmark", "from": None, "to": tags.get("archetype")} if tags.get("archetype") else None))
    with con:
        con.execute("UPDATE bench_posts SET content_id=?, status='used' WHERE platform=? AND post_id=?", (cid, platform, post_id))
    return cid


def plan_candidates(con, days=30, limit=2):
    """给下周计划用：最近拆解过、还没复刻的对标爆款。"""
    try:
        data = load(con, days)
    except Exception:
        return [], []
    picked = [p for p in data["hits"] if p["tags"].get("archetype") and p["status"] == "studied"][:limit]
    untagged = sum(1 for p in data["hits"] if not p["tags"] and p["status"] == "new")
    notes = [f"对标账号有 {untagged} 条爆款还没拆解，到「对标」页拆解后才能进计划。"] if untagged else []
    out = []
    for p in picked:
        th = method.load_params()["thresholds"][p["platform"]]
        short = re.sub(r"#\S+", "", p["title"]).strip()[:18]
        out.append({"priority": "P1", "title": f"【对标复刻】借「{short}」的结构",
                    "tags": dict(p["tags"]), "bench": (p["platform"], p["post_id"]),
                    "basis": f"对标「{p['bench_name']}」爆款，{times(p)}", "change": "借对标验证过的结构，换成自己能兑现的选题",
                    "exp": {"field": "benchmark", "from": None, "to": p["tags"].get("archetype")},
                    "targets": [{"metric": "follow_rate", "value": th["follow_rate"][1], "slot": "D3"}],
                    "acceptance": "D3 关注率达到良好线即成立", "verdict_from": "对标爆款"})
    return out, notes


def usage(con, days=30):
    since = now_ms() - days * DAY
    r = rows(con, "SELECT COUNT(*) AS n, COALESCE(SUM(price), 0) AS cost, COALESCE(SUM(ok), 0) AS ok FROM api_calls WHERE ts>=?", since)[0]
    return {"calls": r["n"], "ok": r["ok"], "cost": round(r["cost"], 3), "days": days}


def daily_cost(con):
    """按当前设置，每天自动刷新大约花多少钱（美元）。"""
    pages = int(config.load_settings()["bench_pages"])
    return round(sum(tikhub.PRICES[b["platform"]] * pages for b in rows(con, "SELECT platform FROM benchmarks WHERE enabled=1")), 3)
