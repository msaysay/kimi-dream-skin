"""打法库：让工作台越用越准。

每次验收之后，自动把「改了什么 → 有没有用」记成打法，跨周累积：
- 实验打法：计划里每条选题都只改一个变量（比如结尾钩 无 → 关注理由），验收达成算一次有效，未达成算一次无效。
- 结构打法：同一平台里某个标签值（原型、开头钩、结尾钩……）至少 3 条作品后，关注率和完播率相对平台中位数的高低。
- 对标复刻：照对标爆款结构做的选题，验收成没成。
打法到 2 次以上才下结论：有效 / 无效 / 验证中。你可以手动确认或否决，也可以补一句笔记。
这些结论会写进出稿说明（优先用有效的、避开无效的），也会影响下周计划怎么选结尾钩。
另外：样本够了之后，按你自己的分位数建议新的阈值（方法论：P50 为良好、P90 为优秀）。
"""
import statistics
import time

from . import analytics, config, contents, method
from .db import rows

FIELD_NAMES = {**{k: v[0] for k, v in method.TAG_FIELDS.items()}, "series": "系列续集", "benchmark": "对标复刻"}
WIN, LOSE = ("达成",), ("未达成",)


def now_ms():
    return int(time.time() * 1000)


def judge(wins, losses):
    n = wins + losses
    if n < 2:
        return "验证中"
    rate = wins / n
    return "有效" if rate >= 0.6 else "无效" if rate <= 0.34 else "验证中"


def experiment_lessons(con, data=None):
    data = data or analytics.build(con)
    by_key = {(p["platform"], p["post_id"]): p for w in data["works"] for p in w["posts"]}
    items = contents.list_all(con, True)
    out = {}
    for c in items:
        exp = c.get("experiment")
        if not exp or not exp.get("field"):
            continue
        r = contents.evaluate(c, by_key)
        overall = "部分达成" if r["overall"] == "部分达成" else r["overall"]
        if overall not in WIN + LOSE + ("部分达成",):
            continue
        key = f"exp|{exp['field']}|{exp.get('to')}"
        L = out.setdefault(key, {"key": key, "kind": "实验", "field": exp["field"], "value": exp.get("to"), "wins": 0, "losses": 0,
                                 "partial": 0, "evidence": []})
        if overall in WIN:
            L["wins"] += 1
        elif overall in LOSE:
            L["losses"] += 1
        else:
            L["partial"] += 1
        L["evidence"].append({"content_id": c["id"], "title": c["title"], "result": overall, "conclusion": c.get("conclusion") or "",
                              "detail": "；".join(f"{config.PLATFORMS[x['platform']]['name']}{method.METRIC_NAMES.get(x['metric'], x['metric'])}"
                                                 f"{'达标' if x['state'] == 'met' else '未达标'}" for x in r["results"] if x["state"] in ("met", "unmet"))})
    for L in out.values():
        what = FIELD_NAMES.get(L["field"], L["field"])
        L["title"] = {"series": "双强结构做成系列续集", "benchmark": f"照对标爆款结构复刻（{L['value'] or '原型未标'}）"}.get(
            L["field"], f"{what} 改成「{L['value']}」")
        L["status"] = judge(L["wins"] + 0.5 * L["partial"], L["losses"] + 0.5 * L["partial"])
        L["summary"] = f"做过 {L['wins'] + L['losses'] + L['partial']} 次：达成 {L['wins']}，部分达成 {L['partial']}，未达成 {L['losses']}"
    return list(out.values())


def structure_lessons(data):
    """同一平台内，某个标签值的作品（≥3 条）关注率、完播率相对平台中位数。"""
    out = []
    posts = [p for w in data["works"] for p in w["posts"] if not p["flag"]]
    for pf in config.PLATFORMS:
        group = [p for p in posts if p["platform"] == pf]
        if len(group) < 5:
            continue
        med = {m: statistics.median([p["m"][m] for p in group if p["m"].get(m)] or [0]) for m in ("follow_rate", "finish_rate")}
        for field in ("archetype", "hook_open", "hook_end", "motif", "duration_bin"):
            values = {}
            for p in group:
                v = p["tags"].get(field)
                if v:
                    values.setdefault(v, []).append(p)
            for v, ps in values.items():
                if len(ps) < 3:
                    continue
                rel = {m: (statistics.median([p["m"][m] for p in ps if p["m"].get(m)] or [0]) / med[m]) if med[m] else None
                       for m in med}
                fr = rel["follow_rate"]
                status = "有效" if fr and fr >= 1.2 else "无效" if fr and fr <= 0.8 else "验证中"
                out.append({"key": f"struct|{pf}|{field}|{v}", "kind": "结构", "field": field, "value": v, "platform": pf,
                            "title": f"{config.PLATFORMS[pf]['name']} · {FIELD_NAMES[field]}：{v}", "status": status, "n": len(ps),
                            "summary": (f"{len(ps)} 条作品，关注率是平台中位数的 {fr:.1f} 倍" + (f"，完播率 {rel['finish_rate']:.1f} 倍" if rel["finish_rate"] else "")
                                        + ("（样本少，先当参考）" if len(ps) < 5 else "")) if fr else f"{len(ps)} 条作品",
                            "evidence": [{"title": p["title"][:30]} for p in ps[:5]]})
    return out


def lessons(con, data=None):
    data = data or analytics.build(con)
    items = experiment_lessons(con, data) + structure_lessons(data)
    notes = {r["key"]: r for r in rows(con, "SELECT * FROM lesson_notes")}
    for L in items:
        n = notes.get(L["key"]) or {}
        L["auto_status"] = L["status"]
        if n.get("verdict") in ("有效", "无效"):
            L["status"] = n["verdict"]
        L["note"] = n.get("note") or ""
        L["confirmed"] = bool(n.get("verdict"))
    order = {"有效": 0, "无效": 1, "验证中": 2}
    items.sort(key=lambda L: (order[L["status"]], L["kind"] != "实验"))
    return items


def set_note(con, key, verdict=None, note=None):
    if verdict not in (None, "", "有效", "无效"):
        raise ValueError("结论只能是有效或无效")
    old = (rows(con, "SELECT * FROM lesson_notes WHERE key=?", key) or [{}])[0]
    with con:
        con.execute("INSERT OR REPLACE INTO lesson_notes VALUES (?,?,?,?)",
                    (key, verdict if verdict is not None else old.get("verdict"), note if note is not None else old.get("note"), now_ms()))


def preferred(con, field, data=None):
    """某个标签上，打法库判为有效 / 无效的值。"""
    good, bad = [], []
    for L in lessons(con, data):
        value = L["value"]
        if L["field"] == field and value:
            (good if L["status"] == "有效" else bad if L["status"] == "无效" else []).append(value)
    return good, bad


def brief_text(con):
    """写进出稿说明的打法摘要：数据验证过的打法，加上复盘里记下、人工确认过的经验。"""
    items = [L for L in lessons(con) if L["status"] in ("有效", "无效")]
    lines = []
    if items:
        lines.append("## 打法库（这个账号自己验证过的，优先用有效的、避开无效的）")
        for L in items[:12]:
            lines.append(f"- 【{L['status']}】{L['title']}：{L['summary']}" + (f"。备注：{L['note']}" if L["note"] else ""))
    kept = insights(con, "kept")
    if kept:
        lines += ["", "## 记下的经验（来自复盘，人工确认过；还没有被数据反复验证，当作优先考虑的做法）"]
        lines += [f"- {x['text']}" + (f"（依据：{x['basis']}）" if x["basis"] else "") for x in kept[:10]]
    return "\n".join(lines).strip()


# ---------- 复盘里的洞察：AI 提，人确认，确认后才进出稿说明 ----------
INSIGHT_HEADING = "可记入打法库的洞察"
INSIGHT_ASK = f"""

## 输出的最后一节（固定格式，程序会读取）
在复盘正文之后，加一节「## {INSIGHT_HEADING}」，最多 3 条，每条一行：`- 洞察｜依据`
- 洞察是以后还能复用的做法（以后做什么、不做什么），一句话，30 字以内；不是复述这一周的数字。
- 依据写清是哪几条作品、什么数据。样本少于 3 条的，洞察以「假设：」开头。
- 已经在「记下的经验」里的不要重复。没有值得记的就写一行「- 无」。"""


def insights(con, status=None):
    return rows(con, "SELECT * FROM insights" + (" WHERE status=?" if status else "") + " ORDER BY id DESC", *([status] if status else []))


def extract_insights(text):
    """从复盘正文里取出最后那一节的洞察：[(洞察, 依据)]"""
    import re
    m = re.search(rf"(?ms)^#+\s*{INSIGHT_HEADING}\s*$(.*?)(?=^#+\s|\Z)", text or "")
    out = []
    for line in (m.group(1) if m else "").splitlines():
        line = line.strip().lstrip("-*·0123456789.、 ").strip("`").strip()
        if not line or line in ("无", "无。", "暂无"):
            continue
        parts = [p.strip() for p in re.split(r"[｜|]", line, maxsplit=1)]
        if len(parts[0]) >= 4:
            out.append((parts[0][:80], parts[1][:160] if len(parts) > 1 else ""))
    return out[:3]


def propose_insights(con, text, source):
    """把复盘里的洞察存成「待确认」；和已有的重复的不再提。返回新提了几条。"""
    have = {analytics.norm_title(x["text"]) for x in insights(con)}
    n = 0
    with con:
        for insight, basis in extract_insights(text):
            if analytics.norm_title(insight) in have:
                continue
            con.execute("INSERT INTO insights (text, basis, source, created_at) VALUES (?,?,?,?)", (insight, basis, source, now_ms()))
            have.add(analytics.norm_title(insight))
            n += 1
    return n


def add_insight(con, text, basis=""):
    text = (text or "").strip()
    if len(text) < 4:
        raise ValueError("写一句完整的话")
    with con:
        return con.execute("INSERT INTO insights (text, basis, source, status, created_at, decided_at) VALUES (?,?,?,?,?,?)",
                           (text[:80], (basis or "").strip()[:160], "manual", "kept", now_ms(), now_ms())).lastrowid


def decide_insight(con, insight_id, status):
    if status not in ("kept", "dropped", "proposed"):
        raise ValueError("状态不对")
    with con:
        con.execute("UPDATE insights SET status=?, decided_at=? WHERE id=?", (status, now_ms(), int(insight_id)))


# ---------- 阈值校准 ----------
def quantile(values, q):
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def calibration(con, data=None, min_posts=8):
    """按自己的作品分位数建议新阈值：P90 优秀、P50 良好、P25 及格。样本不足时不给建议。"""
    data = data or analytics.build(con)
    params = method.load_params()
    out = []
    posts = [p for w in data["works"] for p in w["posts"] if not p["flag"]]
    for pf in config.PLATFORMS:
        group = [p for p in posts if p["platform"] == pf]
        if len(group) < min_posts:
            if group:
                out.append({"platform": pf, "ready": False, "n": len(group), "need": min_posts})
            continue
        rows_ = []
        for metric in ("views", "finish_rate", "follow_rate", "share_rate", "comment_rate"):
            vals = [p["m"][metric] for p in group if p["m"].get(metric) is not None]
            if len(vals) < min_posts:
                continue
            new = [quantile(vals, 0.9), quantile(vals, 0.5), quantile(vals, 0.25)]
            new = [round(v) if metric == "views" else round(v, 4) for v in new]
            rows_.append({"metric": metric, "current": params["thresholds"][pf][metric], "suggested": new})
        out.append({"platform": pf, "ready": True, "n": len(group), "rows": rows_})
    return out


def apply_calibration(con, platform):
    cal = next((c for c in calibration(con) if c["platform"] == platform and c["ready"]), None)
    if not cal:
        raise ValueError("这个平台的样本还不够，暂时不能校准")
    method.save_params({"thresholds": {platform: {r["metric"]: r["suggested"] for r in cal["rows"]}}})
    return cal


# ---------- 数据在变好吗 ----------
def weekly_trend(data, weeks=6):
    """按发布周算每个平台的中位关注率、完播率、播放（同龄快照），看最近几周是不是在变好。"""
    import datetime
    out = {}
    posts = [p for w in data["works"] for p in w["posts"] if not p["flag"] and p["post_time"]]
    for pf in config.PLATFORMS:
        group = [p for p in posts if p["platform"] == pf]
        if not group:
            continue
        buckets = {}
        for p in group:
            d = datetime.date.fromtimestamp(p["post_time"] / 1000)
            y, w, _ = d.isocalendar()
            buckets.setdefault(f"{y}-W{w:02d}", []).append(p)
        series = []
        for wk in sorted(buckets)[-weeks:]:
            ps = buckets[wk]
            med = lambda m: statistics.median([p["m"][m] for p in ps if p["m"].get(m) is not None] or [0]) if any(p["m"].get(m) is not None for p in ps) else None
            series.append({"week": wk, "n": len(ps), "follow_rate": med("follow_rate"), "finish_rate": med("finish_rate"), "views": med("views")})
        out[pf] = series
    return out
