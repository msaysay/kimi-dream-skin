"""内容日历：一个月里排了什么、发了什么、有哪些节点，以及哪个平台太久没发。

排期来自选题的排期日，已发来自采集到的作品，定时发布来自发布任务。节日和电商节点是内置的，也可以自己加。
做法参考了 ZJU-REAL/Easel（Apache-2.0）的内容日历。
"""
import calendar as pycal
import datetime
import json
import time

from . import config, contents
from .db import rows

# 每年固定日期的节点：(月, 日, 名字, 类型)
FIXED = [(1, 1, "元旦", "节日"), (2, 14, "情人节", "节日"), (3, 8, "妇女节", "节日"), (3, 15, "消费者权益日", "节点"), (4, 1, "愚人节", "节日"),
         (4, 23, "世界读书日", "节点"), (5, 1, "劳动节", "节日"), (5, 4, "青年节", "节日"), (5, 20, "520", "节点"), (6, 1, "儿童节", "节日"),
         (6, 7, "高考", "节点"), (6, 18, "618", "电商"), (9, 1, "开学", "节点"), (9, 10, "教师节", "节日"), (10, 1, "国庆节", "节日"),
         (10, 24, "程序员节", "节点"), (11, 11, "双 11", "电商"), (12, 12, "双 12", "电商"), (12, 24, "平安夜", "节日"), (12, 25, "圣诞节", "节日"),
         (12, 31, "跨年", "节点")]
# 农历节日换算成公历（每年不同，这里列到 2030 年）
LUNAR = {
    2026: {"除夕": "02-16", "春节": "02-17", "元宵节": "03-03", "清明": "04-05", "端午节": "06-19", "七夕": "08-19", "中秋节": "09-25", "重阳节": "10-18"},
    2027: {"除夕": "02-05", "春节": "02-06", "元宵节": "02-20", "清明": "04-05", "端午节": "06-09", "七夕": "08-08", "中秋节": "09-15", "重阳节": "10-08"},
    2028: {"除夕": "01-25", "春节": "01-26", "元宵节": "02-09", "清明": "04-04", "端午节": "05-28", "七夕": "08-26", "中秋节": "10-03", "重阳节": "10-26"},
    2029: {"除夕": "02-12", "春节": "02-13", "元宵节": "02-27", "清明": "04-04", "端午节": "06-16", "七夕": "08-16", "中秋节": "09-22", "重阳节": "10-16"},
    2030: {"除夕": "02-02", "春节": "02-03", "元宵节": "02-17", "清明": "04-05", "端午节": "06-05", "七夕": "08-05", "中秋节": "09-12", "重阳节": "10-05"},
}


def nth_weekday(year, month, weekday, n):
    """某月第 n 个星期几（weekday：周一是 0）。"""
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))
    return d


def events(con, start, end):
    """[start, end] 之间的节点：{日期: [{title, kind, id?}]}"""
    out = {}
    put = lambda d, title, kind, **kw: out.setdefault(str(d), []).append(dict(title=title, kind=kind, **kw))
    for year in range(start.year, end.year + 1):
        for m, d, title, kind in FIXED:
            put(datetime.date(year, m, d), title, kind)
        for title, md in LUNAR.get(year, {}).items():
            put(f"{year}-{md}", title, "节日")
        put(nth_weekday(year, 5, 6, 2), "母亲节", "节日")
        put(nth_weekday(year, 6, 6, 3), "父亲节", "节日")
    for e in rows(con, "SELECT * FROM cal_events WHERE date>=? AND date<=?", str(start), str(end)):
        put(e["date"], e["title"], "自定义", id=e["id"], note=e["note"])
    return {d: v for d, v in out.items() if str(start) <= d <= str(end)}


def cadence(con):
    """每个已登录平台距离上一次发布过了几天；超过设置里的天数就算「该发了」。"""
    limit = int(config.load_settings()["cadence_days"])
    now = time.time() * 1000
    out = []
    for a in rows(con, "SELECT platform, account FROM accounts WHERE status<>'demo' ORDER BY platform"):
        last = rows(con, "SELECT MAX(post_time) AS t FROM posts WHERE platform=? AND account=?", a["platform"], a["account"])[0]["t"]
        if not last:
            continue
        days = int((now - last) // 86400000)
        out.append({"platform": a["platform"], "account": a["account"], "days": days, "last": last, "due": days >= limit})
    return out


def upcoming(con, days=14):
    """接下来两周的节点，带上「这天有没有排内容」。"""
    today = datetime.date.today()
    ev = events(con, today, today + datetime.timedelta(days=days))
    planned = {c["schedule_date"] for c in contents.list_all(con) if c["schedule_date"]}
    return [{"date": d, "in_days": (datetime.date.fromisoformat(d) - today).days, "title": e["title"], "kind": e["kind"], "planned": d in planned}
            for d in sorted(ev) for e in ev[d]]


def month(con, ym=None):
    """一个月的日历：按周排好的格子，每天带排期、已发、定时发布和节点。"""
    today = datetime.date.today()
    try:
        year, mon = (int(x) for x in (ym or "").split("-"))
        first = datetime.date(year, mon, 1)
    except ValueError:
        first = today.replace(day=1)
    weeks = pycal.Calendar(firstweekday=0).monthdatescalendar(first.year, first.month)
    start, end = weeks[0][0], weeks[-1][-1]
    t0 = int(time.mktime(start.timetuple()) * 1000)
    t1 = int(time.mktime((end + datetime.timedelta(days=1)).timetuple()) * 1000)
    day_of = lambda ms: str(datetime.date.fromtimestamp(ms / 1000))
    days = {}
    slot = lambda d: days.setdefault(d, {"planned": [], "posts": [], "scheduled": [], "events": []})
    linked = {(r["platform"], r["post_id"]): r["content_id"] for r in rows(con, "SELECT * FROM content_posts")}
    items = contents.list_all(con)
    for c in items:
        if c["schedule_date"] and str(start) <= c["schedule_date"] <= str(end):
            slot(c["schedule_date"])["planned"].append({"id": c["id"], "title": c["title"], "stage": c["stage"], "priority": c["priority"],
                                                        "has_script": bool(c["script"]), "published": bool(c["posts"])})
    for p in rows(con, "SELECT platform, post_id, account, title, post_time FROM posts WHERE post_time>=? AND post_time<? AND account<>? ORDER BY post_time",
                  t0, t1, config.DEMO_ACCOUNT):
        slot(day_of(p["post_time"]))["posts"].append({"platform": p["platform"], "title": p["title"], "content_id": linked.get((p["platform"], p["post_id"])),
                                                     "time": time.strftime("%H:%M", time.localtime(p["post_time"] / 1000))})
    for j in rows(con, "SELECT params FROM jobs WHERE type='publish' AND status='done'"):
        p = json.loads(j["params"])
        if p.get("schedule") and str(start) <= p["schedule"][:10] <= str(end) and p["schedule"].replace("T", " ")[:16] > time.strftime("%Y-%m-%d %H:%M"):
            slot(p["schedule"][:10])["scheduled"].append({"platform": p["platform"], "title": p["title"], "time": p["schedule"].replace("T", " ")[11:16],
                                                         "content_id": p.get("content_id")})
    for d, ev in events(con, start, end).items():
        slot(d)["events"] = ev
    grid = [[dict(days.get(str(d), {"planned": [], "posts": [], "scheduled": [], "events": []}), date=str(d), day=d.day,
                  in_month=d.month == first.month, today=d == today, past=d < today) for d in week] for week in weeks]
    stages_done = ("发布", "数据验收")
    unplanned = [{"id": c["id"], "title": c["title"], "stage": c["stage"], "priority": c["priority"], "has_script": bool(c["script"])}
                 for c in items if not c["archived"] and not c["schedule_date"] and not c["posts"] and c["stage"] not in stages_done and c["source"] != "backfill"]
    prev = (first - datetime.timedelta(days=1)).strftime("%Y-%m")
    nxt = (first + datetime.timedelta(days=32)).strftime("%Y-%m")
    return {"month": first.strftime("%Y-%m"), "label": f"{first.year} 年 {first.month} 月", "prev": prev, "next": nxt, "weeks": grid,
            "unplanned": unplanned[:30], "cadence": cadence(con), "upcoming": upcoming(con), "cadence_days": int(config.load_settings()["cadence_days"])}
