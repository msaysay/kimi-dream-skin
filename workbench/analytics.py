"""把数据库里的数据整理成看板和复盘用的结构：相对表现、漏斗诊断、跨平台作品配对。

配对和「和自己的中位数比」的做法改编自 JaelChen/video-data-dashboard（MIT）。
"""
import re
import statistics
import time
from difflib import SequenceMatcher

from . import config
from .db import rows

LATEST_METRICS = ["views", "cover_click_rate", "finish5s_rate", "exit2s_rate", "finish_rate", "new_fans"]
DERIVED_METRICS = ["interaction_rate", "share_rate", "fans_per_k", "view_ratio"]

# 漏斗的六步：(key, 名称, 指标, 是否越低越好, 这一步掉队时的改进方向)
STAGES = [
    ("click", "点进来", "cover_click_rate", False, "封面和标题没让人想点。换一个更直白的封面，把最吸引人的信息放进标题前半句。"),
    ("stay", "留下来", "exit2s_rate", True, "开头两秒就被划走。把结论或最强的画面放到第一秒，删掉寒暄和片头。"),
    ("watch", "看下去", "finish5s_rate", False, "前五秒没留住人。开头直接说看完能得到什么，尽快进入正题。"),
    ("finish", "看完", "finish_rate", False, "中后段流失多。对照留存曲线找到下滑最陡的位置，压缩或删掉那一段。"),
    ("react", "有反应", "interaction_rate", False, "看完了但没有互动。结尾加一个具体的提问或行动引导。"),
    ("convert", "转化", "fans_per_k", False, "有播放但不涨粉。在视频里说明关注你能持续得到什么，并检查主页能不能承接。"),
]
WEAK, STRONG = 0.8, 1.2


def value(post, metric):
    return post["latest"].get(metric) if metric in LATEST_METRICS else post["derived"].get(metric)


def load_posts(con):
    posts = rows(con, "SELECT * FROM posts")
    for p in posts:
        k = (p["platform"], p["post_id"])
        p["latest"] = (rows(con, "SELECT * FROM snapshots WHERE platform=? AND post_id=? ORDER BY fetched_at DESC LIMIT 1", *k) or [{}])[0]
        p["history"] = rows(con, "SELECT fetched_at, views, new_fans FROM snapshots WHERE platform=? AND post_id=? ORDER BY fetched_at", *k)
        rets = rows(con, "SELECT series, sec, pct FROM retention WHERE platform=? AND post_id=? ORDER BY sec", *k)
        p["retention"] = [[r["sec"], r["pct"]] for r in rets if r["series"] == "self"]
        p["benchmark"] = [[r["sec"], r["pct"]] for r in rets if r["series"] == "benchmark"]
        p["sources"] = [[r["source"], r["share_pct"]] for r in
                        rows(con, "SELECT source, share_pct FROM traffic_source WHERE platform=? AND post_id=? ORDER BY share_pct DESC", *k)]
        p["audience"] = {}
        for r in rows(con, "SELECT dim, label, pct FROM audience WHERE platform=? AND post_id=?", *k):
            p["audience"].setdefault(r["dim"], []).append([r["label"], r["pct"]])
        p["trend"] = {}
        for r in rows(con, "SELECT grain, metric, ts, value FROM trend WHERE platform=? AND post_id=? ORDER BY ts", *k):
            p["trend"].setdefault(f"{r['grain']}_{r['metric']}", []).append([r["ts"], r["value"]])
        L = p["latest"]
        views = L.get("views")
        inter = sum(L.get(c) or 0 for c in ("likes", "comments", "collects", "shares"))
        p["interactions"] = inter
        p["derived"] = {
            "interaction_rate": inter / views * 100 if views else None,
            "share_rate": (L.get("shares") or 0) / views * 100 if views else None,
            "fans_per_k": L["new_fans"] / views * 1000 if views and L.get("new_fans") is not None else None,
            "view_ratio": L["avg_view_sec"] / p["duration_sec"] * 100 if L.get("avg_view_sec") and p.get("duration_sec") else None,
        }
    return posts


def add_relative(posts):
    """每个指标除以同一个账号自己的中位数：1.2 表示比平时高 20%。同账号不到 3 条就不比。"""
    baseline = {}
    for key in {(p["platform"], p["account"]) for p in posts}:
        group = [p for p in posts if (p["platform"], p["account"]) == key]
        for m in LATEST_METRICS + DERIVED_METRICS:
            vals = [value(p, m) for p in group if value(p, m)]
            baseline[(*key, m)] = statistics.median(vals) if len(vals) >= 3 else None
    for p in posts:
        p["rel"] = {}
        for m in LATEST_METRICS + DERIVED_METRICS:
            base, v = baseline[(p["platform"], p["account"], m)], value(p, m)
            p["rel"][m] = v / base if v is not None and base else None
    return {f"{pf}/{acc}/{m}": v for (pf, acc, m), v in baseline.items()}


def diagnose(post):
    """按六步漏斗标出每一步相对自己平时的水平，并找出最先掉队的那一步。"""
    stages, weakest = [], None
    for key, name, metric, lower_better, advice in STAGES:
        rel, v = post["rel"].get(metric), value(post, metric)
        score = None if not rel else (1 / rel if lower_better else rel)
        status = "unknown" if score is None else "weak" if score < WEAK else "strong" if score > STRONG else "ok"
        stages.append({"key": key, "name": name, "metric": metric, "value": v, "score": score, "status": status})
        if status == "weak" and weakest is None:
            weakest = {"key": key, "name": name, "advice": advice}
    post["stages"], post["weak_stage"] = stages, weakest


def norm_title(t):
    t = re.sub(r"#\S+|\[[^\]]*\]|@\S+", "", t or "")
    return re.sub(r"[^\w一-鿿]", "", t).lower()


def group_works(posts):
    """同一条内容在各平台的发布配成一个作品：发布时间相差 4 天内，且标题相似或时长几乎一样。"""
    works = []
    for p in sorted(posts, key=lambda p: p["post_time"] or 0):
        best, score = None, 0.0
        for w in works:
            if any(x["platform"] == p["platform"] for x in w) or abs((w[0]["post_time"] or 0) - (p["post_time"] or 0)) > 4 * 86400 * 1000:
                continue
            sim = max(SequenceMatcher(None, norm_title(x["title"]), norm_title(p["title"])).ratio() for x in w)
            if p["duration_sec"] and any(x["duration_sec"] and abs(x["duration_sec"] - p["duration_sec"]) <= 2 for x in w):
                sim = max(sim, 0.6)
            if sim > score:
                best, score = w, sim
        if best is not None and score >= 0.35:
            best.append(p)
        else:
            works.append([p])
    return works


def build(con, days=None):
    posts = load_posts(con)
    baseline = add_relative(posts)
    for p in posts:
        diagnose(p)
    order = list(config.PLATFORMS)
    since = (time.time() - days * 86400) * 1000 if days else 0
    works = []
    for w in group_works(posts):
        w.sort(key=lambda p: order.index(p["platform"]))
        lead = min(w, key=lambda p: p["post_time"] or 0)
        if (lead["post_time"] or 0) < since:
            continue
        ranked = [p for p in w if p["rel"].get("views")]
        # 多平台发布时，相对各自平时水平最好的那个平台；都不如平时就不标
        best = max(ranked, key=lambda p: p["rel"]["views"]) if len(ranked) > 1 else None
        if best and best["rel"]["views"] <= 1:
            best = None
        works.append({
            "work_id": f"{lead['platform']}:{lead['post_id']}", "title": min((p["title"] or "" for p in w), key=len) or lead["title"], "first_post_time": lead["post_time"],
            "duration_sec": next((p["duration_sec"] for p in w if p["duration_sec"]), None),
            "total_views": sum(p["latest"].get("views") or 0 for p in w),
            "total_new_fans": sum(p["latest"].get("new_fans") or 0 for p in w),
            "total_interactions": sum(p["interactions"] for p in w),
            "best_platform": best["platform"] if best else None,
            "posts": w,
        })
    works.sort(key=lambda w: -(w["first_post_time"] or 0))
    shown = [p for w in works for p in w["posts"]]
    by_platform = {}
    for pf in order:
        group = [p for p in shown if p["platform"] == pf]
        by_platform[pf] = {"posts": len(group), "views": sum(p["latest"].get("views") or 0 for p in group),
                           "interactions": sum(p["interactions"] for p in group),
                           "new_fans": sum(p["latest"].get("new_fans") or 0 for p in group)}
    totals = {k: sum(v[k] for v in by_platform.values()) for k in ("posts", "views", "interactions", "new_fans")}
    totals["works"] = len(works)
    return {"generated_at": int(time.time() * 1000), "days": days, "baseline": baseline, "works": works,
            "by_platform": by_platform, "totals": totals,
            "platforms": {k: v["name"] for k, v in config.PLATFORMS.items()},
            "stages": [{"key": s[0], "name": s[1], "metric": s[2], "lower_better": s[3], "advice": s[4]} for s in STAGES]}
