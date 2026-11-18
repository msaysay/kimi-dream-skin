"""热点雷达：把几个平台的热榜和你订阅的 RSS 收到一处，让 AI 按定位卡挑出适合你做的，一键转成选题。

热榜来自免费的第三方聚合接口（没有官方接口），随时可能失效，所以每个榜都配了备用地址，全部失败只影响这一个榜；
RSS 是你自己加的订阅源，最稳。做法参考了 ZJU-REAL/Easel（Apache-2.0）的热点雷达。
"""
import json
import re
import time
import urllib.request
from xml.etree import ElementTree

from . import config, contents, method
from .db import rows

# 榜单 -> (名字, 依次尝试的接口地址)
BUILTIN = {
    "weibo": ("微博热搜", ["https://v2.xxapi.cn/api/weibohot", "https://60s.viki.moe/v2/weibo"]),
    "douyin": ("抖音热点", ["https://v2.xxapi.cn/api/douyinhot", "https://60s.viki.moe/v2/douyin"]),
    "baidu": ("百度热搜", ["https://v2.xxapi.cn/api/baiduhot", "https://60s.viki.moe/v2/baidu/hot"]),
    "bilibili": ("B 站热搜", ["https://v2.xxapi.cn/api/bilibilihot", "https://60s.viki.moe/v2/bili"]),
}
FITS = ["高", "中", "低", "不碰"]
KEEP_DAYS = 7
MAX_BYTES = 4 * 1024 * 1024
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
      "Accept": "application/json, application/xml, text/xml, */*"}


def now_ms():
    return int(time.time() * 1000)


def http_get(url, timeout=15):
    if not re.match(r"^https?://", url or ""):
        raise ValueError("地址要以 http:// 或 https:// 开头")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        raw = r.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("返回的内容太大")
    return raw


def pick(d, keys):
    if isinstance(d, dict):
        for k in keys:
            v = d.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return str(v)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def heat_text(v):
    """把热度统一成好读的写法：12031447 -> 1203万。"""
    if re.fullmatch(r"\d{5,}", v or ""):
        n = int(v)
        return f"{n / 1e8:.1f}亿" if n >= 1e8 else f"{n // 10000}万"
    return v or ""


def parse_board(obj):
    """各家热榜接口的返回格式不一样：这里只认「一个列表，每项有标题」。"""
    data = obj.get("data") if isinstance(obj, dict) else None
    if isinstance(data, dict):
        data = data.get("data") or data.get("list") or []
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, dict):
            continue
        title = pick(item, ("title", "Title", "word", "name", "keyword"))
        if not title:
            continue
        out.append({"title": title[:120], "url": pick(item, ("url", "Url", "link", "mobil_url")),
                    "heat": heat_text(pick(item, ("hot", "hot_value", "HotValue", "hotValue", "num")))})
    return out


def parse_rss(raw):
    """RSS 2.0 / RSS 1.0 / Atom：只取标题和链接。"""
    root = ElementTree.fromstring(raw)
    local = lambda el: el.tag.rsplit("}", 1)[-1].lower()
    out = []
    for el in root.iter():
        if local(el) not in ("item", "entry"):
            continue
        title, link = "", ""
        for c in el:
            name = local(c)
            if name == "title" and not title:
                title = re.sub(r"\s+", " ", "".join(c.itertext())).strip()
            elif name == "link" and not link:
                link = (c.get("href") or c.text or "").strip()
        if title:
            out.append({"title": title[:120], "url": link, "heat": ""})
    return out[:40]


def fetch_board(key):
    last = None
    for url in BUILTIN[key][1]:
        try:
            items = parse_board(json.loads(http_get(url).decode("utf-8")))
            if items:
                return items[:50]
            last = "返回里没有内容"
        except Exception as e:  # 一个地址不行就试下一个
            last = f"{type(e).__name__}: {e}"
    raise RuntimeError(f"接口暂时不可用（{last}）")


def sources(con):
    """所有来源：内置热榜（按设置开关）+ 你加的 RSS。"""
    on = {s.strip() for s in config.load_settings()["hot_boards"].split(",") if s.strip()}
    out = [{"key": k, "name": name, "kind": "board", "enabled": k in on} for k, (name, _) in BUILTIN.items()]
    for r in rows(con, "SELECT * FROM hot_sources ORDER BY id"):
        out.append({"key": f"rss{r['id']}", "id": r["id"], "name": r["name"], "kind": "rss", "url": r["url"], "enabled": bool(r["enabled"]),
                    "last_error": r["last_error"], "last_fetch": r["last_fetch"]})
    return out


def add_rss(con, name, url):
    url = (url or "").strip()
    items = parse_rss(http_get(url))  # 加之前先试读一次，读不了当场告诉用户
    if not items:
        raise ValueError("这个地址里没读到条目，确认它是 RSS / Atom 订阅地址")
    with con:
        cur = con.execute("INSERT INTO hot_sources (name, url, added_at) VALUES (?,?,?)", ((name or "").strip()[:30] or url[:30], url, now_ms()))
    store(con, f"rss{cur.lastrowid}", items)
    return cur.lastrowid


def store(con, source, items):
    """把这一次读到的榜单存下来：还在榜上的更新名次和热度，新上榜的记下首次出现时间。"""
    ts = now_ms()
    with con:
        for rank, it in enumerate(items, 1):
            con.execute("""INSERT INTO hot_topics (source, title, url, heat, rank, first_seen, fetched_at) VALUES (?,?,?,?,?,?,?)
                           ON CONFLICT (source, title) DO UPDATE SET url=excluded.url, heat=excluded.heat, rank=excluded.rank, fetched_at=excluded.fetched_at""",
                        (source, it["title"], it.get("url") or "", it.get("heat") or "", rank, ts, ts))
        # 掉榜一周、没被选中的清掉
        con.execute("DELETE FROM hot_topics WHERE fetched_at<? AND content_id IS NULL", (ts - KEEP_DAYS * 86400000,))
    return ts


def refresh(con, log=print):
    """刷新所有开着的来源，返回 (成功的来源数, 新上榜的条数)。"""
    ok, before = 0, {(r["source"], r["title"]) for r in rows(con, "SELECT source, title FROM hot_topics")}
    for s in sources(con):
        if not s["enabled"]:
            continue
        try:
            items = fetch_board(s["key"]) if s["kind"] == "board" else parse_rss(http_get(s["url"]))
            store(con, s["key"], items)
            ok += 1
            log(f"{s['name']}：{len(items)} 条")
            if s["kind"] == "rss":
                with con:
                    con.execute("UPDATE hot_sources SET last_fetch=?, last_error=NULL WHERE id=?", (now_ms(), s["id"]))
        except Exception as e:
            log(f"{s['name']}：没读到（{e}）")
            if s["kind"] == "rss":
                with con:
                    con.execute("UPDATE hot_sources SET last_error=? WHERE id=?", (str(e)[:200], s["id"]))
    with con:
        con.execute("INSERT OR REPLACE INTO kv (k, v) VALUES ('hot_refreshed', ?)", (str(now_ms()),))
    new = sum(1 for r in rows(con, "SELECT source, title FROM hot_topics") if (r["source"], r["title"]) not in before)
    return ok, new


def view(con):
    """热点页要的数据：每个来源当前在榜的条目（AI 判过的排前面），加上适合度统计。"""
    srcs = sources(con)
    names = {s["key"]: s["name"] for s in srcs}
    groups = []
    for s in srcs:
        if not s["enabled"]:
            continue
        latest = rows(con, "SELECT MAX(fetched_at) AS t FROM hot_topics WHERE source=?", s["key"])[0]["t"]
        items = rows(con, "SELECT * FROM hot_topics WHERE source=? AND fetched_at=? ORDER BY rank", s["key"], latest) if latest else []
        groups.append(dict(s, fetched_at=latest, items=items))
    picked = rows(con, "SELECT * FROM hot_topics WHERE fit IN ('高','中') AND state<>'ignored' ORDER BY fit='高' DESC, judged_at DESC, rank LIMIT 30")
    for p in picked:
        p["source_name"] = names.get(p["source"], p["source"])
    last = rows(con, "SELECT v FROM kv WHERE k='hot_refreshed'")
    return {"groups": groups, "picked": picked, "sources": srcs, "refreshed_at": int(last[0]["v"]) if last else None,
            "unjudged": rows(con, "SELECT COUNT(*) AS n FROM hot_topics WHERE fit IS NULL AND fetched_at>=?", now_ms() - 86400000)[0]["n"]}


# ---------- 交给 AI：按定位卡判断哪些适合做 ----------
def unjudged(con, limit=160):
    """还没判过、最近一天还在榜上的热点（每个来源取靠前的）。"""
    return rows(con, "SELECT rowid AS id, * FROM hot_topics WHERE fit IS NULL AND fetched_at>=? ORDER BY rank, fetched_at DESC LIMIT ?",
                now_ms() - 86400000, limit)


def judge_brief(con, ids):
    marks = ",".join("?" * len(ids))
    items = rows(con, f"SELECT rowid AS id, source, title, heat FROM hot_topics WHERE rowid IN ({marks}) ORDER BY rank", *ids)
    names = {s["key"]: s["name"] for s in sources(con)}
    listing = "\n".join(f"{it['id']}｜{names.get(it['source'], it['source'])}｜{it['title']}" + (f"｜热度 {it['heat']}" if it["heat"] else "") for it in items)
    return ("# 任务：从今天的热点里挑出适合这个账号做的\n\n"
            "下面是今天各平台的热榜标题。请按账号定位逐条判断：这个热点，这个账号能不能借来做一条内容。\n\n"
            "判断规则：\n"
            "- 「高」：和账号的内容方向直接相关，受众会关心，账号有资格讲，而且能落到账号的承接动作上。\n"
            "- 「中」：不直接相关，但能找到一个自然的切入角度（比如用账号的方法论去拆这件事）。\n"
            "- 「低」：硬蹭才能沾边。\n"
            "- 「不碰」：时政、外交、灾难事故、涉及未成年人、未经证实的传闻、明星私生活和饭圈争议。这些一律不碰，不要给角度。\n"
            "- 宁缺毋滥：大多数热点应该是「低」或「不碰」。只看标题判断，不要编造事件细节。\n"
            "- 给「高」和「中」的写一个切入角度：一句 30 字以内、可以直接当选题的话，要体现账号自己的视角，不是复述热点。\n\n"
            + method.profile_text(method.load_profile()) + "\n\n## 热点列表（编号｜来源｜标题）\n" + listing
            + "\n\n## 输出格式\n每行一条，四段用「｜」分开，不要别的内容：\n`编号｜适合度｜切入角度｜一句理由`\n"
              "适合度只能是：高 / 中 / 低 / 不碰。「低」和「不碰」的切入角度留空，例如：`12｜低｜｜和账号方向无关`")


def judge_submit(con, ids, text):
    allowed = {int(i) for i in ids}
    done = 0
    with con:
        for line in text.splitlines():
            parts = [p.strip() for p in re.split(r"[｜|]", line.strip().strip("`").lstrip("-* "))]
            if len(parts) < 2 or not parts[0].isdigit() or int(parts[0]) not in allowed or parts[1] not in FITS:
                continue
            angle = parts[2][:60] if len(parts) > 2 and parts[1] in ("高", "中") else ""
            reason = parts[3][:120] if len(parts) > 3 else ""
            con.execute("UPDATE hot_topics SET fit=?, angle=?, reason=?, judged_at=? WHERE rowid=?", (parts[1], angle, reason, now_ms(), int(parts[0])))
            done += 1
    if not done:
        raise ValueError("没读到「编号｜适合度｜切入角度｜理由」格式的行；适合度只能是 高 / 中 / 低 / 不碰")
    good = rows(con, f"SELECT COUNT(*) AS n FROM hot_topics WHERE rowid IN ({','.join('?' * len(allowed))}) AND fit IN ('高','中')", *allowed)[0]["n"]
    return f"看了 {done} 个热点，{good} 个适合做"


# ---------- 转成选题 ----------
def to_content(con, topic_id, title=None):
    t = rows(con, "SELECT rowid AS id, * FROM hot_topics WHERE rowid=?", int(topic_id))
    if not t:
        raise ValueError("没有这条热点")
    t = t[0]
    if t["content_id"] and rows(con, "SELECT 1 FROM contents WHERE id=? AND archived=0", t["content_id"]):
        return t["content_id"]
    name = {s["key"]: s["name"] for s in sources(con)}.get(t["source"], t["source"])
    th = method.load_params()["thresholds"]["channels"]
    notes = f"热点：{t['title']}（{name}" + (f"，热度 {t['heat']}" if t["heat"] else "") + "）"
    if t["url"]:
        notes += f"\n链接：{t['url']}"
    if t["reason"]:
        notes += f"\nAI 判断：适合度{t['fit']}，{t['reason']}"
    notes += "\n\n动笔前先核实：热榜只有标题，事件的时间、数字、人物要自己查到出处再写。"
    cid = contents.save(con, {"title": (title or t["angle"] or t["title"]).strip()[:60], "priority": "P1", "source": "hot", "verdict_from": "热点",
                              "basis": f"借热点「{t['title'][:30]}」。热点有时效，建议 48 小时内发出；先过定位三闸，只借话题不改人设",
                              "targets": [{"metric": "views", "value": th["views"][1], "slot": "D3"}, {"metric": "follow_rate", "value": th["follow_rate"][0], "slot": "D3"}],
                              "acceptance": "D3 播放达到良好线说明借到了流量；关注率不低于及格线说明来的是对的人", "notes": notes})
    with con:
        con.execute("UPDATE hot_topics SET content_id=?, state='picked' WHERE rowid=?", (cid, t["id"]))
    return cid
