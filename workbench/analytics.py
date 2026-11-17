"""把数据库里的数据按《内容数据分析方法论》算成可以行动的结构。

- 同龄对比：每条作品取发布后第 1/3/7 天的快照（D1/D3/D7），默认用 D3 比较，没有就用最新值并标出「非同龄」
- 五段漏斗 S1–S5：流量 / 留存 / 互动 / 关系 / 转化，从上往下第一个低于及格线的就是本周唯一要改的
- 双引擎：流量力（播放规模分 + 留人分）与转化力（关注 0.5、分享 0.3、评论 0.2）分开算，再判四类
- 状态异常（仅自己可见、删除等）的作品不进评分和结论，只进待重测
跨平台作品配对和「和自己的中位数比」改编自 JaelChen/video-data-dashboard（MIT）。
"""
import re
import statistics
import time
from difflib import SequenceMatcher

from . import config, method
from .db import rows

DAY = 86400 * 1000
SLOT_WINDOWS = {"D1": (0.5, 1.75), "D3": (2.25, 3.75), "D7": (6.0, 8.0)}


def pick_slots(post_time, history):
    """在快照里找最接近发布后第 1/3/7 天的那一个。"""
    out = {}
    for slot, (lo, hi) in SLOT_WINDOWS.items():
        target = int(slot[1:])
        best = None
        for snap in history:
            age = (snap["fetched_at"] - post_time) / DAY
            if lo <= age <= hi and (best is None or abs(age - target) < abs((best["fetched_at"] - post_time) / DAY - target)):
                best = snap
        if best:
            out[slot] = best
    return out


def rates(snap, intent):
    """统一口径：比率一律用小数，分母都是该平台后台的播放次数。"""
    views = snap.get("views")
    div = lambda x: (x / views) if views and x is not None else None
    finish = snap.get("finish_rate")
    return {
        "views": views,
        "finish_rate": None if finish is None else finish / 100,
        "avg_view_sec": snap.get("avg_view_sec"),
        "follow_rate": div(snap.get("new_fans")),
        "share_rate": div(snap.get("shares")),
        "comment_rate": div(snap.get("comments")),
        "intent_rate": div(intent),
    }


def grade(value, lines, scores):
    """按 (优秀线, 良好线, 及格线) 给分。返回 (分数, 档位)。"""
    if value is None:
        return None, "无数据"
    for line, score, name in zip(lines, scores, ("优秀", "良好", "及格")):
        if value >= line:
            return score, name
    return scores[3], "偏弱"


def verdict(flow, conv, p):
    v = p["verdict"]
    if flow is None or conv is None:
        return None
    if flow >= v["high"] and conv >= v["high"]:
        return "双强型"
    if flow >= v["high"] and conv < v["conv_low"]:
        return "泛流量型"
    if flow < v["flow_low"] and conv >= v["high"]:
        return "垂直型"
    if flow < v["flow_low"] and conv < v["conv_low"]:
        return "低效型"
    return "过渡型"


def score_post(post, params):
    th = params["thresholds"].get(post["platform"]) or next(iter(params["thresholds"].values()))
    sc = params["scores"]
    m = post["m"]
    segs, first_red = [], None
    for key, name, metric, meaning, action in method.FUNNEL:
        value = m.get(metric)
        if metric == "intent_rate" and not post["comments_tracked"]:
            value = None
        score, level = grade(value, th[metric], sc.get(metric, sc["default"]))
        if key == "S3":  # 互动看分享率和评论率，取好的那个：有人转或有人聊都算产生了对话
            c_score, c_level = grade(m.get("comment_rate"), th["comment_rate"], sc.get("comment_rate", sc["default"]))
            if c_score is not None and (score is None or c_score > score):
                metric, value, score, level = "comment_rate", m.get("comment_rate"), c_score, c_level
        segs.append({"key": key, "name": name, "metric": metric, "value": value, "level": level, "score": score})
        if level == "偏弱" and first_red is None:
            first_red = {"key": key, "name": name, "meaning": meaning, "action": action}
    s = {x["metric"]: x["score"] for x in segs}
    flow = (s["views"] + s["finish_rate"]) / 2 if s["views"] is not None and s["finish_rate"] is not None else None
    cw = params["conv_weights"]
    conv_parts = {k: grade(m.get(k), th[k], sc.get(k, sc["default"]))[0] for k in cw}
    conv = round(sum(cw[k] * conv_parts[k] for k in cw), 1) if all(v is not None for v in conv_parts.values()) else None
    fw = params["flow_weight"]
    post["funnel"], post["first_red"] = segs, first_red
    post["flow"] = None if flow is None else round(flow, 1)
    post["conv"] = conv
    post["total"] = round(fw * flow + (1 - fw) * conv, 1) if flow is not None and conv is not None else None
    post["verdict"] = None if post["flag"] else verdict(post["flow"], post["conv"], params)


def traffic(post):
    src = {name: pct for name, pct in post["sources"]}
    total = sum(src.values()) or None
    group = lambda g: (sum(v for k, v in src.items() if k in method.SOURCE_GROUPS[g]) / total) if total else None
    post["rec_share"], post["social_share"], post["search_share"] = group("recommend"), group("social"), group("search")
    # 首日占比：有逐日播放就用逐日；只有逐小时就取前 24 小时
    day = post["trend"].get("day_views") or []
    hour = post["trend"].get("hour_views") or []
    total_views = post["latest"].get("views")
    post["d1_views"] = post["d2_views"] = None
    if day:
        post["d1_views"] = day[0][1]
        post["d2_views"] = day[1][1] if len(day) > 1 else None
    elif hour and post["post_time"]:
        post["d1_views"] = sum(v for ts, v in hour if ts < post["post_time"] + DAY)
    post["first_day_share"] = post["d1_views"] / total_views if post["d1_views"] is not None and total_views else None
    post["d2_over_d1"] = post["d2_views"] / post["d1_views"] if post["d2_views"] is not None and post["d1_views"] else None


def intent_counts(con, profile):
    """每条作品下「领取关键词」评论的条数，作为第 4 跳（关注 → 私域）的意向信号。"""
    kw = (profile.get("asset_keyword") or "").strip().lower()
    intent_rules = [r["id"] for r in rows(con, "SELECT id FROM comment_rules WHERE is_intent=1")]
    counts, tracked = {}, set()
    for c in rows(con, "SELECT platform, post_id, text, rule_id FROM comments"):
        key = (c["platform"], c["post_id"])
        tracked.add(key)
        if (kw and kw in (c["text"] or "").lower()) or (c["rule_id"] in intent_rules):
            counts[key] = counts.get(key, 0) + 1
    return counts, tracked


def load_posts(con):
    profile = method.load_profile()
    intents, tracked = intent_counts(con, profile)
    flags = {(r["platform"], r["post_id"]): r for r in rows(con, "SELECT * FROM post_flags")}
    tags = {(r["platform"], r["post_id"]): r for r in rows(con, """
        SELECT cp.platform, cp.post_id, c.id AS content_id, c.motif, c.archetype, c.hook_open, c.hook_mid, c.hook_end,
               c.duration_bin, c.presence, c.workflow, c.title AS content_title
        FROM content_posts cp JOIN contents c ON c.id = cp.content_id""")}
    posts = rows(con, "SELECT * FROM posts")
    for p in posts:
        k = (p["platform"], p["post_id"])
        p["history"] = rows(con, "SELECT * FROM snapshots WHERE platform=? AND post_id=? ORDER BY fetched_at", *k)
        p["latest"] = p["history"][-1] if p["history"] else {}
        rets = rows(con, "SELECT series, sec, pct FROM retention WHERE platform=? AND post_id=? ORDER BY sec", *k)
        p["retention"] = [[r["sec"], r["pct"]] for r in rets if r["series"] == "self"]
        p["benchmark"] = [[r["sec"], r["pct"]] for r in rets if r["series"] == "benchmark"]
        p["sources"] = [[r["source"], r["share_pct"]] for r in
                        rows(con, "SELECT source, share_pct FROM traffic_source WHERE platform=? AND post_id=? ORDER BY share_pct DESC", *k)]
        p["trend"] = {}
        for r in rows(con, "SELECT grain, metric, ts, value FROM trend WHERE platform=? AND post_id=? ORDER BY ts", *k):
            p["trend"].setdefault(f"{r['grain']}_{r['metric']}", []).append([r["ts"], r["value"]])
        p["slots"] = pick_slots(p["post_time"] or 0, p["history"])
        p["age_days"] = ((p["latest"].get("fetched_at") or 0) - (p["post_time"] or 0)) / DAY if p["latest"] else None
        p["flag"] = (flags.get(k) or {}).get("flag")
        p["flag_note"] = (flags.get(k) or {}).get("note")
        t = tags.get(k) or {}
        p["tags"] = {f: t.get(f) for f in method.TAG_FIELDS if t.get(f)}
        p["content_id"] = t.get("content_id")
        p["workflow"] = t.get("workflow")
        p["comments_tracked"] = k in tracked
        p["intent"] = intents.get(k, 0)
        L = p["latest"]
        p["interactions"] = sum(L.get(c) or 0 for c in ("likes", "comments", "collects", "shares"))
    return posts


def add_relative(posts):
    """播放、完播、关注率除以同一账号自己的中位数：1.2 表示比平时高 20%。同账号不到 3 条就不比。"""
    keys = ("views", "finish_rate", "follow_rate", "share_rate")
    for key in {(p["platform"], p["account"]) for p in posts}:
        group = [p for p in posts if (p["platform"], p["account"]) == key and not p["flag"]]
        for metric in keys:
            vals = [p["m"][metric] for p in group if p["m"].get(metric)]
            base = statistics.median(vals) if len(vals) >= 3 else None
            for p in posts:
                if (p["platform"], p["account"]) == key:
                    v = p["m"].get(metric)
                    p.setdefault("rel", {})[metric] = v / base if v is not None and base else None


def norm_title(t):
    t = re.sub(r"#\S+|\[[^\]]*\]|@\S+", "", t or "")
    return re.sub(r"[^\w一-鿿]", "", t).lower()


def group_works(posts):
    """同一条内容在各平台的发布配成一个作品：先看是否关联到同一条内容，再看发布时间相差 4 天内且标题相似或时长几乎一样。"""
    works, by_content = [], {}
    for p in sorted(posts, key=lambda p: p["post_time"] or 0):
        if p["content_id"] and p["content_id"] in by_content and all(x["platform"] != p["platform"] for x in by_content[p["content_id"]]):
            by_content[p["content_id"]].append(p)
            continue
        best, score = None, 0.0
        for w in works:
            if any(x["platform"] == p["platform"] for x in w) or abs((w[0]["post_time"] or 0) - (p["post_time"] or 0)) > 4 * DAY:
                continue
            if p["content_id"] and any(x["content_id"] and x["content_id"] != p["content_id"] for x in w):
                continue
            sim = max(SequenceMatcher(None, norm_title(x["title"]), norm_title(p["title"])).ratio() for x in w)
            if p["duration_sec"] and any(x["duration_sec"] and abs(x["duration_sec"] - p["duration_sec"]) <= 2 for x in w):
                sim = max(sim, 0.6)
            if sim > score:
                best, score = w, sim
        if best is not None and score >= 0.35:
            best.append(p)
        else:
            best = [p]
            works.append(best)
        if p["content_id"]:
            by_content.setdefault(p["content_id"], best)
    return works


def matrix(posts, field):
    """结构-表现矩阵：同一平台内按某个标签聚合。条数为 1 的行只能当假设。"""
    out = {}
    for p in posts:
        value = p["tags"].get(field)
        if not value or p["flag"]:
            continue
        cell = out.setdefault(p["platform"], {}).setdefault(value, {"n": 0, "views": 0, "follows": 0, "shares": 0, "comments": 0, "finish": []})
        L = p["cmp"]
        cell["n"] += 1
        cell["views"] += L.get("views") or 0
        cell["follows"] += L.get("new_fans") or 0
        cell["shares"] += L.get("shares") or 0
        cell["comments"] += L.get("comments") or 0
        if L.get("finish_rate") is not None:
            cell["finish"].append(L["finish_rate"] / 100)
    for pf, cells in out.items():
        for value, c in cells.items():
            v = c["views"] or None
            c.update(avg_views=c["views"] / c["n"], follow_rate=c["follows"] / v if v else None,
                     share_rate=c["shares"] / v if v else None, comment_rate=c["comments"] / v if v else None,
                     finish_rate=sum(c["finish"]) / len(c["finish"]) if c["finish"] else None, hypothesis=c["n"] < 2)
            del c["finish"]
    return out


def checks(posts):
    """导入校验：条数、合计、极值。任一项不对先停下来查口径。"""
    out = {}
    for pf in config.PLATFORMS:
        group = [p for p in posts if p["platform"] == pf]
        if not group:
            continue
        issues = []
        for p in group:
            L = p["latest"]
            if (L.get("finish_rate") or 0) > 100 or (L.get("views") is not None and L["views"] < 0):
                issues.append(f"{p['title'][:12]}：比率超过 100% 或播放为负")
            if L.get("views") == 0 and (L.get("likes") or 0) > 0:
                issues.append(f"{p['title'][:12]}：播放为 0 但有点赞，可能没采到播放")
            if L.get("views") and L.get("avg_view_sec") and p.get("duration_sec") and L["avg_view_sec"] > p["duration_sec"] * 1.05:
                issues.append(f"{p['title'][:12]}：平均播放时长超过视频时长")
        views = [p["latest"].get("views") or 0 for p in group]
        out[pf] = {"count": len(group), "views": sum(views), "max_views": max(views),
                   "comments": sum(p["latest"].get("comments") or 0 for p in group),
                   "shares": sum(p["latest"].get("shares") or 0 for p in group),
                   "follows": sum(p["latest"].get("new_fans") or 0 for p in group), "issues": issues}
    return out


def platform_summary(posts, params):
    """每个平台：中位水平、五段漏斗的中位档位、第一个亮红灯的段、流量集中度、结尾钩效果。"""
    out = {}
    for pf in config.PLATFORMS:
        group = [p for p in posts if p["platform"] == pf and not p["flag"]]
        if not group:
            continue
        th = params["thresholds"][pf]
        med = lambda metric: statistics.median([p["m"][metric] for p in group if p["m"].get(metric) is not None] or [None]) \
            if any(p["m"].get(metric) is not None for p in group) else None
        segs, red = [], None
        for key, name, metric, meaning, action in method.FUNNEL:
            if metric == "intent_rate":  # 只算抓过评论的作品，没抓的不能当 0
                tracked = [p["m"]["intent_rate"] for p in group if p["comments_tracked"] and p["m"].get("intent_rate") is not None]
                v = statistics.median(tracked) if tracked else None
            else:
                v = med(metric)
            _, level = grade(v, th[metric], [0, 0, 0, 0])
            if key == "S3":
                cv = med("comment_rate")
                _, c_level = grade(cv, th["comment_rate"], [0, 0, 0, 0])
                rank = ["无数据", "偏弱", "及格", "良好", "优秀"]
                if rank.index(c_level) > rank.index(level):
                    metric, v, level = "comment_rate", cv, c_level
            segs.append({"key": key, "name": name, "metric": metric, "value": v, "level": level})
            if level == "偏弱" and red is None:
                red = {"key": key, "name": name, "meaning": meaning, "action": action}
        views = sorted([p["cmp"].get("views") or 0 for p in group], reverse=True)
        total = sum(views)
        hooked = [p for p in group if p["tags"].get("hook_end") and p["tags"]["hook_end"] != "无"]
        unhooked = [p for p in group if p["tags"].get("hook_end") == "无"]
        pooled = lambda ps: (sum(p["cmp"].get("new_fans") or 0 for p in ps) / (sum(p["cmp"].get("views") or 0 for p in ps) or 1)) if ps else None
        out[pf] = {"posts": len(group), "funnel": segs, "first_red": red,
                   "median_views": statistics.median(views), "top_share": views[0] / total if total else None,
                   "hook_follow": pooled(hooked), "nohook_follow": pooled(unhooked),
                   "hooked_n": len(hooked), "unhooked_n": len(unhooked),
                   "flow_median": statistics.median([p["flow"] for p in group if p["flow"] is not None] or [0])}
    return out


def build(con, days=None):
    params = method.load_params()
    posts = load_posts(con)
    for p in posts:
        slot = params["compare_slot"]
        p["cmp_slot"] = slot if slot in p["slots"] else ("最新" if p["latest"] else None)
        p["cmp"] = p["slots"].get(slot) or p["latest"]
        p["m"] = rates(p["cmp"], p["intent"])
        score_post(p, params)
        traffic(p)
    add_relative(posts)
    order = list(config.PLATFORMS)
    since = (time.time() * 1000 - days * DAY) if days else 0
    works = []
    for w in group_works(posts):
        w.sort(key=lambda p: order.index(p["platform"]))
        lead = min(w, key=lambda p: p["post_time"] or 0)
        if (lead["post_time"] or 0) < since:
            continue
        works.append({
            "work_id": f"{lead['platform']}:{lead['post_id']}",
            "title": min((p["title"] or "" for p in w), key=len) or lead["title"], "first_post_time": lead["post_time"],
            "duration_sec": next((p["duration_sec"] for p in w if p["duration_sec"]), None),
            "total_views": sum(p["latest"].get("views") or 0 for p in w),
            "total_new_fans": sum(p["latest"].get("new_fans") or 0 for p in w),
            "total_interactions": sum(p["interactions"] for p in w),
            "content_id": next((p["content_id"] for p in w if p["content_id"]), None),
            "tags": next((p["tags"] for p in w if p["tags"]), {}),
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
    profile = method.load_profile()
    return {"generated_at": int(time.time() * 1000), "days": days, "works": works, "by_platform": by_platform, "totals": totals,
            "platforms": {k: v["name"] for k, v in config.PLATFORMS.items()},
            "summary": platform_summary(shown, params), "checks": checks(shown),
            "matrix": {"archetype": matrix(shown, "archetype"), "hook_end": matrix(shown, "hook_end")},
            "funnel": [{"key": f[0], "name": f[1], "metric": f[2], "meaning": f[3], "action": f[4]} for f in method.FUNNEL],
            "verdicts": method.VERDICTS, "params": params,
            "profile_gaps": method.profile_gaps(profile), "metric_names": method.METRIC_NAMES}
