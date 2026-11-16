"""复盘：先按规则汇总出一份摘要，再（可选）交给大模型写成复盘报告。"""
import json
import time
import urllib.error
import urllib.request
from collections import Counter

from . import analytics, config
from .analytics import STAGES

ADVICE = {s[0]: s[4] for s in STAGES}

SYSTEM_PROMPT = """你是一位短视频运营复盘顾问。用户会给你一份 JSON，里面是他自己在抖音、小红书、视频号的账号近期数据。
数据说明：
- rel_* 是该指标相对这个账号自己平时中位数的倍数，1.2 表示比平时高 20%，null 表示数据不足无法比较。
- weak_stage 是漏斗（点进来→留下来→看下去→看完→有反应→转化）里最先低于平时八成的那一步。
- 比率类指标都是百分数。视频号的 exit2s_rate 实际是「快速划走率」，口径和另外两个平台不同。
请用中文写一份复盘，Markdown 格式，包含：
1. 一段话结论（这段时间整体怎么样，最值得注意的一件事）
2. 做得好的内容及可复用的原因（引用具体作品和数字）
3. 最需要改的 2-3 个问题，每个问题给出下一条视频可以直接照做的改法
4. 三个平台之间的差异，以及各平台接下来的侧重点
5. 下周的 3 条具体行动
只根据给出的数据下结论。数据不足以支持的判断要明说「数据不足」，不要编造数字。"""


def pct(rel):
    return None if rel is None else round((rel - 1) * 100)


def summarize(dataset):
    """把看板数据压缩成复盘用的摘要（也是发给大模型的内容）。"""
    names = dataset["platforms"]
    works = []
    for w in dataset["works"]:
        posts = []
        for p in w["posts"]:
            L = p["latest"]
            posts.append({
                "platform": names[p["platform"]], "account": p["account"], "views": L.get("views"),
                "rel_views": p["rel"].get("views"), "finish_rate": L.get("finish_rate"), "rel_finish_rate": p["rel"].get("finish_rate"),
                "exit2s_rate": L.get("exit2s_rate"), "cover_click_rate": L.get("cover_click_rate"),
                "interaction_rate": p["derived"].get("interaction_rate"), "rel_interaction_rate": p["rel"].get("interaction_rate"),
                "new_fans": L.get("new_fans"), "fans_per_k": p["derived"].get("fans_per_k"),
                "weak_stage": (p.get("weak_stage") or {}).get("name"),
                "top_source": p["sources"][0][0] if p["sources"] else None,
            })
        rels = [x["rel_views"] for x in posts if x["rel_views"]]
        works.append({"title": w["title"], "date": time.strftime("%Y-%m-%d", time.localtime((w["first_post_time"] or 0) / 1000)),
                      "duration_sec": w["duration_sec"], "total_views": w["total_views"], "total_new_fans": w["total_new_fans"],
                      "best_platform": names.get(w["best_platform"]), "avg_rel_views": sum(rels) / len(rels) if rels else None,
                      "posts": posts})
    weak = {}
    for w in dataset["works"]:
        for p in w["posts"]:
            c = weak.setdefault(names[p["platform"]], Counter())
            c["总数"] += 1
            if p.get("weak_stage"):
                c[p["weak_stage"]["key"]] += 1
    return {"days": dataset["days"], "totals": dataset["totals"],
            "by_platform": {names[k]: v for k, v in dataset["by_platform"].items()},
            "weak_stage_counts": {k: dict(v) for k, v in weak.items()},
            "best_platform_counts": dict(Counter(w["best_platform"] for w in works if w["best_platform"])),
            "works": works}


def fmt(n):
    if n is None:
        return "—"
    return f"{n / 10000:.1f} 万" if n >= 10000 else f"{n:.0f}"


def describe(work):
    parts = []
    for p in work["posts"]:
        rel = pct(p["rel_views"])
        text = f"{p['platform']} {fmt(p['views'])} 播放"
        if rel is not None:
            text += f"（比平时{'高' if rel >= 0 else '低'} {abs(rel)}%）"
        parts.append(text)
    return f"**{work['title'] or '（无标题）'}**（{work['date']}）：" + "；".join(parts)


def rule_report(summary):
    """不依赖大模型的规则版复盘。"""
    stage_name = {s[0]: s[1] for s in STAGES}
    t, days = summary["totals"], summary["days"]
    lines = [f"# {'近 ' + str(days) + ' 天' if days else '全部数据'}复盘", ""]
    if not summary["works"]:
        return "\n".join(lines + ["这段时间没有作品数据。先在「账号」页登录并采集一次数据。"])
    lines += ["## 总览", "",
              f"- 共 {t['works']} 个作品、{t['posts']} 条发布，总播放 {fmt(t['views'])}，互动 {fmt(t['interactions'])}，涨粉 {fmt(t['new_fans'])}。"]
    for name, v in summary["by_platform"].items():
        if v["posts"]:
            lines.append(f"- {name}：{v['posts']} 条，播放 {fmt(v['views'])}，互动 {fmt(v['interactions'])}，涨粉 {fmt(v['new_fans'])}。")
    ranked = sorted([w for w in summary["works"] if w["avg_rel_views"]], key=lambda w: -w["avg_rel_views"])
    if ranked:
        lines += ["", "## 表现最好的作品", ""] + [f"{i}. {describe(w)}" for i, w in enumerate(ranked[:3], 1)]
        worst = [w for w in ranked[::-1][:3] if w["avg_rel_views"] < 1 and w not in ranked[:3]]
        if worst:
            lines += ["", "## 最需要回看的作品", ""]
            for i, w in enumerate(worst, 1):
                stuck = sorted({p["weak_stage"] for p in w["posts"] if p["weak_stage"]})
                lines.append(f"{i}. {describe(w)}" + (f"。卡在：{'、'.join(stuck)}" if stuck else ""))
    else:
        lines += ["", "每个账号至少要有 3 条作品的数据，才能算出「和自己平时比」的相对表现。目前数据还不够。"]
    common = []
    for name, counts in summary["weak_stage_counts"].items():
        total = counts.get("总数", 0)
        for key, n in sorted(((k, v) for k, v in counts.items() if k != "总数"), key=lambda x: -x[1]):
            if total >= 3 and n / total >= 0.4:
                common.append(f"- {name}：{total} 条里有 {n} 条卡在「{stage_name[key]}」。{ADVICE[key]}")
    if common:
        lines += ["", "## 反复出现的问题", ""] + common
    if summary["best_platform_counts"]:
        lines += ["", "## 平台对比", "",
                  "同一个作品发到多个平台时，相对各自平时水平表现最好的平台：" +
                  "，".join(f"{k} {v} 次" for k, v in sorted(summary["best_platform_counts"].items(), key=lambda x: -x[1])) + "。"]
    return "\n".join(lines)


def ai_report(summary, settings=None):
    """调用 OpenAI 兼容接口（Kimi、DeepSeek、通义等都支持）生成复盘报告。"""
    s = settings or config.load_settings()
    if not (s["ai_api_key"] and s["ai_model"] and s["ai_base_url"]):
        raise RuntimeError("还没有配置大模型：请在「设置」页填写接口地址、密钥和模型名")
    body = {"model": s["ai_model"], "messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(summary, ensure_ascii=False)}]}
    req = urllib.request.Request(s["ai_base_url"].rstrip("/") + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + s["ai_api_key"]})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            reply = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"大模型接口返回 {e.code}：{e.read().decode('utf-8', 'replace')[:300]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"连不上大模型接口：{e.reason}") from e
    try:
        return reply["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"大模型返回的格式不对：{json.dumps(reply, ensure_ascii=False)[:300]}") from e


def create(con, days, use_ai):
    summary = summarize(analytics.build(con, days))
    content = ai_report(summary) if use_ai else rule_report(summary)
    with con:
        cur = con.execute("INSERT INTO reviews (created_at, days, kind, content) VALUES (?,?,?,?)",
                          (int(time.time() * 1000), days or 0, "ai" if use_ai else "rule", content))
    return cur.lastrowid
