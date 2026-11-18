"""发布后回读：发布引擎说「成功」不等于真的发出去了（卡审核、被平台拦下、页面改版都可能发生）。
所以发完再到创作者后台的作品列表里找一遍，按标题和时间对上才算确认；对上以后顺手把作品关联回选题。

列表更新有延迟（审核、平台数据同步），所以会隔几分钟重试几次；一直没看到就标成「未确认」，请到平台上看一眼。
做法参考了 ZJU-REAL/Easel（Apache-2.0）的发布回读。
"""
import time
from difflib import SequenceMatcher

from . import analytics, collect, config, contents
from .db import rows

# 第几次检查之前等多少秒：发完 2 分钟看一次，没看到 10 分钟后再看，再没有 30 分钟后看最后一次
DELAYS = [120, 600, 1800]


def now_ms():
    return int(time.time() * 1000)


def match(posts, title, desc, since_ms):
    """在作品列表里找刚发的这条：发布时间在这次发布之后，标题或简介对得上。"""
    want = [w for w in (analytics.norm_title(title), analytics.norm_title(desc)) if len(w) >= 4]
    best, score = None, 0.0
    for p in posts:
        if p["post_time"] and p["post_time"] < since_ms - 10 * 60000:
            continue
        got = analytics.norm_title(p["title"])
        for w in want:
            # 开头 12 个字对得上就算（平台会在后面加话题）；太短的文字容易撞上别的作品，只按相似度算
            contained = len(w) >= 6 and len(got) >= 6 and (w[:12] in got or got[:12] in w)
            s = 1.0 if contained else SequenceMatcher(None, w, got).ratio()
            if s > score:
                best, score = p, s
    return best if score >= 0.5 else None


def record(con, job_id, p, status, post_id=None, note=""):
    with con:
        con.execute("INSERT OR REPLACE INTO publish_checks (job_id, platform, account, title, content_id, status, post_id, note, checked_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (job_id, p["platform"], p["account"], p["title"], p.get("content_id"), status, post_id, note, now_ms()))


def plan(con, job_id, p):
    """发布任务成功后调用：决定要不要回读。返回要排队的核验任务参数，不需要核验时返回 None。"""
    if not config.load_settings()["verify_publish"]:
        return None
    if p.get("draft"):
        record(con, job_id, p, "skipped", note="存的是草稿，没有发出去")
        return None
    if p.get("schedule"):
        record(con, job_id, p, "scheduled", note=f"定时 {p['schedule'].replace('T', ' ')[:16]} 发布，到点后下次采集会自动关联")
        return None
    record(con, job_id, p, "waiting", note="等平台处理完再确认")
    return {"platform": p["platform"], "account": p["account"], "title": p["title"], "desc": p.get("desc") or "", "content_id": p.get("content_id"),
            "publish_job": job_id, "since": now_ms(), "attempt": 0, "not_before": now_ms() + DELAYS[0] * 1000}


def run(con, p, log=print):
    """回读一次。返回 (结果, 下一次核验的参数或 None)。结果：confirmed / waiting / unverified / login。"""
    name = config.PLATFORMS[p["platform"]]["name"]
    try:
        posts = collect.list_posts(p["platform"], p["account"], log=lambda m: None)
    except collect.NotLoggedIn as e:
        record(con, p["publish_job"], p, "login", note=str(e))
        log(f"✗ 没法确认：{e}")
        return "login", None
    except Exception as e:  # 浏览器没起来、平台页面改版等：说清楚没法确认，不当成发布失败
        record(con, p["publish_job"], p, "login", note=f"读作品列表时出错（{type(e).__name__}）")
        log(f"✗ 没法确认：读{name}的作品列表时出错（{type(e).__name__}: {str(e)[:120]}）。请到平台的作品管理里看一眼。")
        return "login", None
    hit = match(posts, p["title"], p.get("desc"), p["since"])
    if hit:
        record(con, p["publish_job"], p, "confirmed", hit["post_id"], "在作品列表里找到了")
        log(f"✓ 已确认发出：{name}的作品列表里找到了「{hit['title'][:24]}」")
        if p.get("content_id") and not rows(con, "SELECT 1 FROM content_posts WHERE content_id=? AND platform=?", p["content_id"], p["platform"]):
            contents.link(con, p["content_id"], p["platform"], hit["post_id"])
            with con:
                con.execute("UPDATE contents SET stage='数据验收', updated_at=? WHERE id=?", (now_ms(), p["content_id"]))
            log("已关联回选题，数据到了会自动验收")
        return "confirmed", None
    attempt = p.get("attempt", 0) + 1
    if attempt < len(DELAYS):
        wait = DELAYS[attempt]
        record(con, p["publish_job"], p, "waiting", note=f"作品列表里还没看到，{wait // 60} 分钟后再看")
        log(f"{name}的作品列表里还没看到（看了 {len(posts)} 条），可能还在审核，{wait // 60} 分钟后再看一次")
        return "waiting", dict(p, attempt=attempt, not_before=now_ms() + wait * 1000)
    record(con, p["publish_job"], p, "unverified", note="看了几次都没在作品列表里找到")
    log(f"✗ 未确认：{name}的作品列表里一直没看到「{p['title'][:24]}」。可能还在审核、被平台拦下，或者标题被改过。请到平台的作品管理里看一眼。")
    return "unverified", None
