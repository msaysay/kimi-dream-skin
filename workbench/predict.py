"""数据预测：发出去之前先猜，数据回来再对答案。猜错的地方，就是对「文案怎么变成结果」理解不够的地方。

每条选题有三份预测，都必须在发布之前做完：
- 基线：不看稿子，只按这个账号平时的水平猜（同平台、同原型作品的中位数和上下四分位）。它是要被打败的对手。
- AI：读稿子，给每个指标一个区间和一个点，并写出 3–5 条假设（稿子里哪一句会让哪个指标高于或低于平时）。
- 你的直觉（可选）：播放和关注率会比平时高、差不多还是低。

数据到了（默认 D3 档）自动对答案：区间有没有罩住、偏差多大、方向对不对、哪条假设被推翻。
每次复盘都会看准确度：AI 读稿子有没有比「按平时猜」更准、系统性地高估了什么、哪类假设总是错，再给出改进计划。
过去的偏差会写进下一次预测的说明里，所以预测会被自己的错误校准。
"""
import json
import re
import statistics
import time

from . import analytics, config, contents, method
from .db import rows

METRICS = ["views", "finish_rate", "follow_rate", "share_rate", "comment_rate"]
GUESS = {"higher": "高于平时", "same": "差不多", "lower": "低于平时"}
DIRECTIONS = ("高于基线", "低于基线")
LATE_MS = 2 * 3600 * 1000  # 发布两小时之后才做的预测不算数（已经能看到数据了）
BAND = 0.2                 # 「差不多」：和基线相差 20% 以内


def now_ms():
    return int(time.time() * 1000)


def fmt(metric, v):
    if v is None:
        return "—"
    return f"{v:,.0f}" if metric == "views" else f"{v * 100:.2f}%"


def quantiles(values):
    """[下四分位, 中位数, 上四分位]；样本太少就用最小、中位、最大。"""
    v = sorted(values)
    if len(v) < 4:
        return [v[0], statistics.median(v), v[-1]]
    q = statistics.quantiles(v, n=4, method="inclusive")
    return [q[0], q[1], q[2]]


def platforms_with_history(data):
    return [pf for pf in config.PLATFORMS if any(p["platform"] == pf and not p["flag"] and p["m"].get("views")
                                                 for w in data["works"] for p in w["posts"])]


def baseline(content, data):
    """不看稿子的预测：{平台: {指标: [低, 中, 高], "n": 参照作品数, "group": 参照的是哪一组}}。"""
    out = {}
    posts = [p for w in data["works"] for p in w["posts"] if not p["flag"] and p["m"].get("views")
             and p.get("content_id") != content.get("id")]
    for pf in config.PLATFORMS:
        mine = [p for p in posts if p["platform"] == pf]
        if not mine:
            continue
        same = [p for p in mine if content.get("archetype") and p["tags"].get("archetype") == content["archetype"]]
        group, label = (same, f"同为{content['archetype']}的 {len(same)} 条") if len(same) >= 3 else (mine, f"这个平台最近的 {len(mine)} 条")
        row = {"n": len(group), "group": label}
        for m in METRICS:
            vals = [p["m"][m] for p in group if p["m"].get(m) is not None]
            if vals:
                row[m] = quantiles(vals)
        out[pf] = row
    return out


def save(con, content_id, source, data):
    with con:
        con.execute("DELETE FROM predictions WHERE content_id=? AND source=?", (int(content_id), source))
        con.execute("INSERT INTO predictions (content_id, source, made_at, data) VALUES (?,?,?,?)",
                    (int(content_id), source, now_ms(), json.dumps(data, ensure_ascii=False)))


def load(con, content_id):
    out = {}
    for r in rows(con, "SELECT * FROM predictions WHERE content_id=?", int(content_id)):
        out[r["source"]] = {"made_at": r["made_at"], "data": json.loads(r["data"])}
    return out


def published(con, content_id):
    return bool(rows(con, "SELECT 1 FROM content_posts WHERE content_id=? LIMIT 1", int(content_id)))


def ensure_baseline(con, content_id, data=None):
    """发布前把基线定下来（已经发出去的不再改）。没有历史数据时返回 None。"""
    have = load(con, content_id)
    if published(con, content_id):
        return have.get("baseline", {}).get("data")
    base = baseline(contents.get(con, content_id), data or analytics.build(con))
    if base:
        save(con, content_id, "baseline", base)
    return base or None


def set_guess(con, content_id, guess):
    if published(con, content_id):
        raise ValueError("已经发出去了，直觉要在发布之前写")
    clean = {k: v for k, v in (guess or {}).items() if k in ("views", "follow_rate") and v in GUESS}
    save(con, content_id, "me", clean)
    return clean


# ---------- 交给 AI：读稿子做预测 ----------
def history_table(data, limit=20):
    names = data["platforms"]
    out = []
    for w in data["works"]:
        for p in w["posts"]:
            if p["flag"] or not p["m"].get("views"):
                continue
            out.append({"平台": names[p["platform"]], "标题": re.sub(r"#\S+", "", p["title"] or "").strip()[:36], "档位": p["cmp_slot"],
                        "结构": " × ".join(v for v in (p["tags"].get("archetype"), p["tags"].get("hook_open"), p["tags"].get("hook_end")) if v) or "未打标",
                        **{method.METRIC_NAMES[m]: fmt(m, p["m"].get(m)) for m in METRICS}, "判定": p["verdict"]})
    return out[:limit]


def brief(con, content_id):
    c = contents.get(con, content_id)
    if not (c.get("script") or "").strip():
        raise ValueError("这条还没有稿子，先出稿再预测")
    data = analytics.build(con)
    base = ensure_baseline(con, content_id, data)
    if not base:
        raise ValueError("还没有可参照的历史数据：先采集或导入几条自己的作品")
    names = data["platforms"]
    base_text = "\n".join(f"- {names[pf]}（参照{row['group']}）：" + "；".join(
        f"{method.METRIC_NAMES[m]} {fmt(m, row[m][0])} ~ {fmt(m, row[m][2])}，中位 {fmt(m, row[m][1])}" for m in METRICS if m in row) for pf, row in base.items())
    acc = accuracy(con, data)
    calib = ""
    if acc["sources"].get("ai", {}).get("n"):
        a = acc["sources"]["ai"]
        calib = ("## 你过去的预测准不准（先看这个，别重复同样的偏差）\n" + f"- 已对过答案的 {a['n']} 个指标里，区间罩住实际值的占 {a['range_hit'] * 100:.0f}%，方向判断对的占 "
                 + (f"{a['direction_hit'] * 100:.0f}%" if a["direction_hit"] is not None else "—") + "。\n"
                 + "".join(f"- {method.METRIC_NAMES[m]}：你的预测是实际值的 {b:.2f} 倍（大于 1 是高估）。\n" for m, b in a["bias"].items())
                 + "".join(f"- 被推翻的假设：{x['text']}（{x['title'][:16]}）\n" for x in acc["failed_assumptions"][:5]))
    from . import playbook
    tags = "、".join(f"{method.TAG_FIELDS[f][0]}「{c[f]}」" for f in ("motif", "archetype", "hook_open", "hook_mid", "hook_end", "duration_bin") if c.get(f)) or "未定"
    slot = data["params"]["compare_slot"]
    return "\n\n".join(x for x in [
        f"# 任务：预测这条视频发出去 {slot[1:]} 天后的数据",
        "你是这个账号的数据预测员。读下面这份稿子，结合这个账号自己的历史数据，预测它在每个平台的表现。\n"
        "预测的目的不是猜中数字，而是把「稿子里的哪个决定会带来什么结果」说清楚，之后好对答案。\n\n"
        "规则：\n"
        "1. 每个指标给三个数：低、预测、高。区间是你有八成把握罩住实际值的范围，不要为了显得准而收得很窄，也不要宽到没有信息量。\n"
        "2. 以基线为锚：先看基线，再根据稿子决定往上调还是往下调，调多少。没有理由就不要偏离基线太远。\n"
        "3. 写 3–5 条假设。每条必须引用稿子里的原话，指向一个指标，说明会让它高于还是低于基线。这是之后要逐条对答案的部分。\n"
        "   「高于 / 低于基线」是和基线的中位数比，不是和某一条历史作品比，而且必须和你给这个指标的预测值一致（预测值在中位数之上才能写高于）。\n"
        "4. 只能用下面给的数据。看不出来的写进区间的宽度里，不要编造行业均值。样本少时区间放宽。\n"
        "5. 三个平台的数字不能互相套用，各按各的基线。",
        method.profile_text(method.load_profile()),
        f"## 这条选题\n标题：{c['title']}\n结构：{tags}\n\n### 稿子\n{c['script'][:6000]}",
        "## 基线（不看稿子、只按平时水平的预测，下四分位 ~ 上四分位）\n" + base_text,
        "## 这个账号的历史作品（参照组）\n" + json.dumps(history_table(data), ensure_ascii=False, indent=0),
        calib, playbook.brief_text(con),
        "## 输出格式（每行一条，用「｜」分开，不要别的内容）\n"
        "```\n" + "\n".join(f"预测｜{names[pf]}｜{method.METRIC_NAMES[m]}｜低｜预测｜高" for pf in list(base)[:1] for m in METRICS if m in base[pf])
        + f"\n判定｜{names[list(base)[0]]}｜双强型\n假设｜完播率｜高于基线｜「稿子里的原话」｜为什么\n风险｜最可能让这条不及预期的一件事\n```\n"
        f"- 平台只能是：{'、'.join(names[pf] for pf in base)}，每个平台每个指标一行。\n"
        "- 播放写整数；其余指标写百分比，例如 1.25%。\n"
        f"- 判定只能是：{' / '.join(method.VERDICTS)}。\n"
        f"- 假设的指标只能是：{' / '.join(method.METRIC_NAMES[m] for m in METRICS)}；方向只能是「高于基线」或「低于基线」。"] if x)


def number(text, metric):
    t = (text or "").replace(",", "").replace("，", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    if not m:
        raise ValueError(f"「{text}」不是数字")
    v = float(m.group(0))
    if metric == "views":
        return v * (10000 if "万" in t else 1)
    return v / 100 if "%" in t or v > 1 else v


def submit(con, content_id, text):
    """读 AI 的预测并保存。格式不对抛 ValueError。"""
    if published(con, content_id):
        raise ValueError("这条已经发出去了，预测要在发布之前做")
    names = {v["name"]: k for k, v in config.PLATFORMS.items()}
    metric_of = {method.METRIC_NAMES[m]: m for m in METRICS}
    base = load(con, content_id).get("baseline", {}).get("data") or {}
    out = {"platforms": {}, "verdicts": {}, "assumptions": [], "risk": ""}
    for line in text.splitlines():
        parts = [p.strip() for p in re.split(r"[｜|]", line.strip().strip("`").lstrip("-* "))]
        if len(parts) < 2:
            continue
        kind = parts[0]
        if kind == "预测" and len(parts) >= 6 and parts[1] in names and parts[2] in metric_of:
            m = metric_of[parts[2]]
            lo, mid, hi = sorted(number(x, m) for x in parts[3:6])
            out["platforms"].setdefault(names[parts[1]], {})[m] = [lo, mid, hi]
        elif kind == "判定" and len(parts) >= 3 and parts[1] in names and parts[2] in method.VERDICTS:
            out["verdicts"][names[parts[1]]] = parts[2]
        elif kind == "假设" and len(parts) >= 4 and parts[1] in metric_of and parts[2] in DIRECTIONS:
            out["assumptions"].append({"metric": metric_of[parts[1]], "direction": parts[2], "quote": parts[3].strip("「」\"")[:120],
                                       "why": parts[4][:160] if len(parts) > 4 else ""})
        elif kind == "风险":
            out["risk"] = "｜".join(parts[1:])[:200]
    if not out["platforms"]:
        raise ValueError("没读到「预测｜平台｜指标｜低｜预测｜高」格式的行")
    missing = [config.PLATFORMS[pf]["name"] for pf in base if pf not in out["platforms"]]
    if missing:
        raise ValueError(f"缺少这些平台的预测：{'、'.join(missing)}")
    for a in out["assumptions"]:  # 假设的方向要和自己给的预测值对得上，否则对答案时没法判
        sides = [(out["platforms"][pf][a["metric"]][1] > base[pf][a["metric"]][1]) == (a["direction"] == "高于基线")
                 for pf in out["platforms"] if a["metric"] in out["platforms"][pf] and a["metric"] in (base.get(pf) or {})]
        if sides and not any(sides):
            name = method.METRIC_NAMES[a["metric"]]
            raise ValueError(f"假设说{name}会{a['direction']}，但你给{name}的预测值在基线中位数的另一边。方向是和基线中位数比的，请把假设或预测值改成一致")
    if len(out["assumptions"]) < 2:
        raise ValueError("至少要写 2 条「假设｜指标｜高于基线 或 低于基线｜稿子原话｜为什么」")
    save(con, content_id, "ai", out)
    n = sum(len(v) for v in out["platforms"].values())
    return f"预测已保存：{n} 个指标，{len(out['assumptions'])} 条假设。数据回来后会自动对答案"


# ---------- 对答案 ----------
def actuals(content, data):
    """这条内容各平台在对比档位上的实际数据：{平台: {指标: 值, "post_time": …, "title": …}}。数据还没到的平台不出现。"""
    slot = data["params"]["compare_slot"]
    by_key = {(p["platform"], p["post_id"]): p for w in data["works"] for p in w["posts"]}
    out = {}
    for link in content.get("posts", []):
        p = by_key.get((link["platform"], link["post_id"]))
        if not p or p["flag"] or slot not in p["slots"]:
            continue
        m = analytics.rates(p["slots"][slot], p["intent"])
        out[p["platform"]] = {**{k: m.get(k) for k in METRICS}, "post_time": p["post_time"], "title": p["title"], "verdict": p["verdict"]}
    return out


def compare(pred, actual, base_mid):
    """一个指标的对答案结果。"""
    lo, mid, hi = pred
    r = {"low": lo, "pred": mid, "high": hi, "actual": actual, "in_range": lo <= actual <= hi,
         "ratio": (mid / actual) if actual else None, "ape": abs(mid - actual) / actual if actual else None}
    if base_mid:
        side = lambda v: 1 if v > base_mid * (1 + BAND) else -1 if v < base_mid * (1 - BAND) else 0
        r["direction_hit"] = side(mid) == side(actual)
        r["base_mid"] = base_mid
    return r


def score(con, content, data):
    """一条内容的预测对答案。还没有数据返回 None。"""
    preds = load(con, content["id"])
    act = actuals(content, data)
    if not preds or not act:
        return None
    first_post = min(a["post_time"] or 0 for a in act.values())
    base = preds.get("baseline", {}).get("data") or {}
    out = {"content_id": content["id"], "title": content["title"], "sources": {}, "assumptions": [], "guess": [], "late": []}
    for source in ("baseline", "ai"):
        p = preds.get(source)
        if not p:
            continue
        if first_post and p["made_at"] > first_post + LATE_MS:
            out["late"].append(source)
            continue
        table = p["data"] if source == "baseline" else p["data"].get("platforms", {})
        items = []
        for pf, a in act.items():
            for m in METRICS:
                if m in (table.get(pf) or {}) and a.get(m) is not None:
                    items.append({"platform": pf, "metric": m,
                                  **compare(table[pf][m], a[m], None if source == "baseline" else (base.get(pf) or {}).get(m, [None, None])[1])})
        if items:
            out["sources"][source] = items
    ai = preds.get("ai")
    if ai and "ai" not in out["late"]:
        for pf, v in (ai["data"].get("verdicts") or {}).items():
            if pf in act and act[pf].get("verdict"):
                out.setdefault("verdicts", []).append({"platform": pf, "pred": v, "actual": act[pf]["verdict"], "hit": v == act[pf]["verdict"]})
        for a in ai["data"].get("assumptions", []):
            # 一条假设在每个有数据的平台上各验一次：多数平台成立才算成立
            checks = []
            for pf, row in act.items():
                mid = (base.get(pf) or {}).get(a["metric"], [None, None])[1]
                if mid and row.get(a["metric"]) is not None:
                    checks.append((row[a["metric"]] > mid) == (a["direction"] == "高于基线"))
            if checks:
                out["assumptions"].append({**a, "held": sum(checks) * 2 > len(checks)})
    me = preds.get("me")
    if me and not (first_post and me["made_at"] > first_post + LATE_MS):
        for m, g in me["data"].items():
            for pf, row in act.items():
                mid = (base.get(pf) or {}).get(m, [None, None])[1]
                if mid and row.get(m) is not None:
                    real = "higher" if row[m] > mid * (1 + BAND) else "lower" if row[m] < mid * (1 - BAND) else "same"
                    out["guess"].append({"platform": pf, "metric": m, "guess": g, "actual": real, "hit": g == real})
    return out if out["sources"] or out["guess"] else None


def accuracy(con, data=None, days=None):
    """所有对过答案的预测汇总：各来源的命中率、偏差，被推翻的假设，偏得最远的几条，以及据此得出的改进计划。"""
    data = data or analytics.build(con)
    since = now_ms() - days * 86400000 if days else 0
    scored = []
    for c in contents.list_all(con, True):
        if not c.get("posts"):
            continue
        s = score(con, c, data)
        if s and max((a["post_time"] or 0) for a in actuals(c, data).values()) >= since:
            scored.append(s)
    sources = {}
    for source in ("baseline", "ai"):
        items = [dict(i, title=s["title"]) for s in scored for i in s["sources"].get(source, [])]
        if not items:
            continue
        dirs = [i["direction_hit"] for i in items if "direction_hit" in i]
        bias, bias_n = {}, {}
        for m in METRICS:
            ratios = [i["ratio"] for i in items if i["metric"] == m and i["ratio"]]
            if ratios:
                bias[m], bias_n[m] = statistics.median(ratios), len(ratios)
        apes = [i["ape"] for i in items if i["ape"] is not None]
        sources[source] = {"n": len(items), "range_hit": sum(i["in_range"] for i in items) / len(items),
                           "median_ape": statistics.median(apes) if apes else None,
                           "direction_hit": (sum(dirs) / len(dirs)) if dirs else None, "bias": bias, "bias_n": bias_n,
                           "worst": sorted([i for i in items if i["ape"] is not None], key=lambda i: -i["ape"])[:3]}
    assumptions = [dict(a, title=s["title"]) for s in scored for a in s["assumptions"]]
    guesses = [g for s in scored for g in s["guess"]]
    verdicts = [v for s in scored for v in s.get("verdicts", [])]
    out = {"contents": len(scored), "sources": sources,
           "assumptions": {"n": len(assumptions), "held": sum(a["held"] for a in assumptions)},
           "failed_assumptions": [{"text": f"{method.METRIC_NAMES[a['metric']]}会{a['direction']}：「{a['quote'][:40]}」", "title": a["title"], "why": a["why"]}
                                  for a in assumptions if not a["held"]],
           "guess": {"n": len(guesses), "hit": sum(g["hit"] for g in guesses)},
           "verdicts": {"n": len(verdicts), "hit": sum(v["hit"] for v in verdicts)},
           "pending": sum(1 for c in contents.list_all(con) if rows(con, "SELECT 1 FROM predictions WHERE content_id=? LIMIT 1", c["id"])) - len(scored)}
    out["plan"] = improvement_plan(out)
    return out


def improvement_plan(acc):
    """从准确度里读出该改什么：每条是「问题 → 动作」。"""
    plan = []
    ai, base = acc["sources"].get("ai"), acc["sources"].get("baseline")
    if not ai:
        return plan
    few = ai["n"] < 10
    if base and ai["median_ape"] is not None and base["median_ape"] is not None:
        if ai["median_ape"] >= base["median_ape"]:
            plan.append({"problem": f"读了稿子的预测（偏差中位数 {ai['median_ape'] * 100:.0f}%）还没有比「按平时水平猜」（{base['median_ape'] * 100:.0f}%）更准",
                         "action": "说明现在用来判断稿子的依据还解释不了结果。先看下面被推翻的假设，找出哪类判断不成立，再记进打法库。"})
        else:
            plan.append({"problem": f"读稿子的预测比基线准（偏差中位数 {ai['median_ape'] * 100:.0f}% 对 {base['median_ape'] * 100:.0f}%）",
                         "action": "稿子层面的判断有信息量。把成立次数多的那类假设记进打法库，出稿时固定下来。"})
    for m, b in ai["bias"].items():
        if (b >= 1.3 or b <= 0.77) and ai["bias_n"].get(m, 0) >= 3:  # 至少 3 次都偏向同一边才叫系统性
            plan.append({"problem": f"{method.METRIC_NAMES[m]}被系统性{'高估' if b > 1 else '低估'}：预测是实际的 {b:.2f} 倍",
                         "action": f"下次预测{method.METRIC_NAMES[m]}时先按这个倍数修正；同时检查是不是把{'开头的吸引力' if m in ('views', 'finish_rate') else '结尾承接的效果'}想得太{'好' if b > 1 else '差'}。"})
    if ai["range_hit"] < 0.5:
        plan.append({"problem": f"区间只罩住了 {ai['range_hit'] * 100:.0f}% 的实际值，过于自信", "action": "区间要放宽到有八成把握；现在的把握和实际不匹配。"})
    elif ai["range_hit"] > 0.95 and ai["n"] >= 10:
        plan.append({"problem": "区间几乎全中，可能给得太宽", "action": "区间可以收窄，逼自己说出更具体的判断。"})
    a = acc["assumptions"]
    if a["n"] >= 3 and a["held"] / a["n"] < 0.5:
        plan.append({"problem": f"{a['n']} 条假设只成立了 {a['held']} 条", "action": "对「稿子的哪句话带来什么结果」的理解偏差较大。逐条看被推翻的假设，这些就是方法论里要改的地方。"})
    if few:
        plan.append({"problem": f"目前只对过 {ai['n']} 个指标，样本很少", "action": "以上都只能当线索。每条都在发布前预测，攒到 10 条内容以上再下结论。"})
    return plan


def report_lines(acc, names):
    """规则复盘里的「预测准确度」一节。"""
    if not acc["sources"]:
        return ["- 还没有对过答案的预测。发布前在选题里点「预测数据」，数据到了这里会自动出准确度。"
                + (f"现在有 {acc['pending']} 条预测在等数据。" if acc["pending"] > 0 else "")]
    L = [f"已对过答案 {acc['contents']} 条内容" + (f"，还有 {acc['pending']} 条在等数据" if acc["pending"] > 0 else "") + "。", "",
         "| 谁的预测 | 指标数 | 区间罩住实际值 | 偏差中位数 | 方向判断对 |", "|---|---|---|---|---|"]
    label = {"baseline": "基线（按平时水平猜）", "ai": "AI 读稿子"}
    for source, s in acc["sources"].items():
        L.append(f"| {label[source]} | {s['n']} | {s['range_hit'] * 100:.0f}% | {s['median_ape'] * 100:.0f}% | "
                 + (f"{s['direction_hit'] * 100:.0f}%" if s["direction_hit"] is not None else "—") + " |")
    if acc["guess"]["n"]:
        L.append(f"| 你的直觉 | {acc['guess']['n']} | — | — | {acc['guess']['hit'] / acc['guess']['n'] * 100:.0f}% |")
    ai = acc["sources"].get("ai")
    if ai:
        if ai["bias"]:
            L += ["", "- 偏差方向：" + "；".join(f"{method.METRIC_NAMES[m]}预测是实际的 {b:.2f} 倍" for m, b in ai["bias"].items()) + "（大于 1 是高估）。"]
        for w in ai["worst"]:
            L.append(f"- 偏得最远：{names[w['platform']]}「{w['title'][:18]}」的{method.METRIC_NAMES[w['metric']]}，预测 {fmt(w['metric'], w['pred'])}，实际 {fmt(w['metric'], w['actual'])}。")
    a = acc["assumptions"]
    if a["n"]:
        L.append(f"- 假设：{a['n']} 条里成立 {a['held']} 条。" + ("被推翻的：" if acc["failed_assumptions"] else ""))
        L += [f"  - {x['text']}（{x['title'][:16]}）" for x in acc["failed_assumptions"][:5]]
    if acc["verdicts"]["n"]:
        L.append(f"- 判定类型猜对 {acc['verdicts']['hit']} / {acc['verdicts']['n']}。")
    if acc["plan"]:
        L += ["", "**问题和改进计划**", ""] + [f"- {p['problem']}。→ {p['action']}" for p in acc["plan"]]
    return L


def view(con, content, data=None):
    """选题抽屉里的预测卡片要的数据。"""
    data = data or analytics.build(con)
    preds = load(con, content["id"]) if content.get("id") else {}
    return {"baseline": preds.get("baseline"), "ai": preds.get("ai"), "me": preds.get("me"), "published": bool(content.get("posts")),
            "score": score(con, content, data) if content.get("posts") else None, "actual": actuals(content, data) if content.get("posts") else {},
            "metrics": METRICS, "slot": data["params"]["compare_slot"], "can_predict": bool(platforms_with_history(data))}
