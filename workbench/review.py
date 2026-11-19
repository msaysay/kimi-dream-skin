"""复盘：按《内容数据分析方法论》的六层结构出一份周复盘，再（可选）交给大模型写成报告。

顺序固定：定位检查 → 数据校验 → 五段漏斗（找本周唯一要改的那一段）→ 单条判定 → 四个判断 → 结构矩阵 → 上期验收 → 评论区信号 → 下一步。
"""
import json
import time
import urllib.error
import urllib.request

from . import analytics, config, method


def predict_accuracy(con, data, days):
    from . import predict
    return predict.accuracy(con, analytics.build(con) if days else data, days)


def bench_times(p):
    from .benchmarks import times
    return times(p)

SYSTEM_PROMPT = """你是短视频运营复盘顾问，严格按用户给的方法论执行，不自造标签、不改口径。
规则：
1. 三个平台的绝对量和比率不能相加、不能横向比数值，只能比结构（占比）和同平台内的排名。
2. 比较只在同一快照档位内进行（D1/D3/D7）；cmp_slot 为「最新」的作品是非同龄数据，结论要打折扣。
3. 流量力与转化力分开看；判定只用给出的五类（双强型 / 泛流量型 / 垂直型 / 低效型 / 过渡型）。
4. 五段漏斗从 S1 往下读，第一个「偏弱」的段就是本周唯一要改的事；一周只改一个变量。
5. 状态异常（flag 非空）的作品不下结论，只进待重测。
6. 样本少于 3 条的分组只能当假设。数据不支持的判断要写「数据不足」，不许编数字。
输出中文 Markdown，章节：一句话结论 / 本周唯一要改的那一段（附动作）/ 单条判定与动作 / 四个判断（流量集中与复利、结尾钩与转化、开头与完播、需要核对的数据）/ 下一步三条选题方向（每条写：母题×原型×开头钩×结尾钩×时长档、只改了哪个变量、目标指标、验收口径）。"""


def chat(messages, settings=None, max_tokens=None):
    """调用 OpenAI 兼容接口（Kimi、DeepSeek、通义等都支持）。"""
    s = settings or config.load_settings()
    if not (s["ai_api_key"] and s["ai_model"] and s["ai_base_url"]):
        raise RuntimeError("还没有配置大模型：请在「设置」页填写接口地址、密钥和模型名")
    body = {"model": s["ai_model"], "messages": messages}
    if max_tokens:
        body["max_tokens"] = max_tokens
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


def pct(x, d=2):
    return "—" if x is None else f"{x * 100:.{d}f}%"


def fmt(n):
    if n is None:
        return "—"
    return f"{n / 10000:.1f} 万" if n >= 10000 else f"{n:,.0f}"


def clean(title, n=24):
    import re
    t = re.sub(r"#\S+", "", title or "").strip() or "（无标题）"
    return t[:n] + ("…" if len(t) > n else "")


def summarize(data, plan=(), accepted=(), questions=(), bench=None, predictions=None):
    """压缩成复盘摘要（也是发给大模型的内容）。"""
    names = data["platforms"]
    posts = []
    for w in data["works"]:
        for p in w["posts"]:
            posts.append({"title": clean(p["title"], 40), "platform": names[p["platform"]], "account": p["account"],
                          "cmp_slot": p["cmp_slot"], "age_days": round(p["age_days"] or 0, 1), "flag": p["flag"],
                          "tags": p["tags"], **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in p["m"].items()},
                          "flow": p["flow"], "conv": p["conv"], "total": p["total"], "verdict": p["verdict"],
                          "first_red": (p["first_red"] or {}).get("key"), "first_day_share": p["first_day_share"],
                          "rec_share": p["rec_share"], "social_share": p["social_share"], "intent_comments": p["intent"]})
    return {"days": data["days"], "profile_gaps": data["profile_gaps"], "checks": data["checks"],
            "benchmark_hits": [{"platform": p["platform"], "account": p["bench_name"], "title": clean(p["title"], 40), "interactions": p["inter"],
                                "times_account_median": round(p["ratio"], 1) if p.get("ratio") else None, "pasted": bool(p.get("pasted")), "tags": p["tags"]} for p in (bench or {}).get("hits", [])[:8]],
            "platform_summary": {names[k]: v for k, v in data["summary"].items()}, "posts": posts,
            "matrix": data["matrix"], "last_plan_acceptance": list(accepted), "comment_questions": list(questions)[:10],
            "plan_candidates": [{k: c[k] for k in ("priority", "title", "tags", "change", "acceptance")} for c in plan],
            **({"prediction_accuracy": predictions} if predictions and predictions.get("sources") else {})}


def rule_report(data, plan=(), notes=(), accepted=(), questions=(), bench=None, lessons=(), predictions=None):
    """不依赖大模型的规则版复盘。"""
    names = data["platforms"]
    days = data["days"]
    L = [f"# {'近 ' + str(days) + ' 天' if days else '全部数据'}复盘", ""]
    shown = [p for w in data["works"] for p in w["posts"]]
    if not shown:
        L.append("这段时间没有自己的作品数据。先在「账号」页登录并采集一次数据。")
        if bench and bench["hits"]:  # 刚起步还没数据时，对标爆款是最快的选题来源
            L += ["", "## 对标信号", ""] + [f"- {names[p['platform']]}「{p['bench_name']}」：{clean(p['title'], 30)}，{bench_times(p)}"
                                           for p in bench["hits"][:8]]
            L.append("\n到「对标」页拆解这些爆款，拆解过的会进下周计划。")
        return "\n".join(L)

    L += ["## 0 定位检查", ""]
    if data["profile_gaps"]:
        L.append(f"- 定位与用户卡还有 {len(data['profile_gaps'])} 项没填或待确认：{'、'.join(data['profile_gaps'])}。"
                 "定位不落纸，漏斗再准也只会优化已经拍出来的东西（「设置 → 定位与用户卡」）。")
    else:
        L.append("- 定位与用户卡已填完，选题先过三闸再进计划。")

    L += ["", "## 1 数据校验（条数 / 合计 / 极值）", ""]
    for pf, c in data["checks"].items():
        L.append(f"- {names[pf]}：{c['count']} 条，合计播放 {fmt(c['views'])}、评论 {fmt(c['comments'])}、分享 {fmt(c['shares'])}、"
                 f"关注 {fmt(c['follows'])}，单条最高 {fmt(c['max_views'])}。" + (f" ⚠ {'；'.join(c['issues'])}" if c["issues"] else " 对得上后台再往下看。"))
    flagged = [p for p in shown if p["flag"]]
    if flagged:
        L.append("- 状态异常、不进结论（只进待重测）：" + "；".join(f"{clean(p['title'])}（{p['flag']}）" for p in flagged))
    nonsame = [p for p in shown if p["cmp_slot"] == "最新" and not p["flag"]]
    if nonsame:
        L.append(f"- 有 {len(nonsame)} 条没有 {data['params']['compare_slot']} 快照，用的是最新值（非同龄），比较时打折扣。")

    L += ["", "## 2 五段漏斗：本周唯一要改的那一段", ""]
    for pf, s in data["summary"].items():
        segs = "　".join(f"{x['key']} {x['name']} {x['level']}" for x in s["funnel"])
        L.append(f"- {names[pf]}（{s['posts']} 条，中位数）：{segs}")
        if s["first_red"]:
            L.append(f"  - 最先掉队：**{s['first_red']['key']} {s['first_red']['name']}**。{s['first_red']['meaning']}。本周只改这一件事：{s['first_red']['action']}")
        else:
            L.append("  - S1–S4 没有段落低于及格线。保持结构，按计划做续作。")
        if s["funnel"][4]["level"] == "无数据":
            L.append("  - S5 转化没有数据：第 4 跳（关注 → 私域）还没布。结尾说「评论区打关键词领」，"
                     "再在「评论」页开启自动回复规则，领取意向率才算得出来。")

    L += ["", "## 3 单条判定（流量力 / 转化力分开算）", ""]
    for p in sorted(shown, key=lambda p: -(p["total"] or 0)):
        if p["flag"]:
            continue
        v = p["verdict"] or "数据不足"
        act = method.VERDICTS.get(v, ("", ""))[0]
        red = f"；最先掉队 {p['first_red']['key']} {p['first_red']['name']}" if p["first_red"] else ""
        L.append(f"- {names[p['platform']]}「{clean(p['title'])}」：流量力 {p['flow']} / 转化力 {p['conv']} / 综合 {p['total']} → **{v}**{red}。{act}")

    L += ["", "## 4 四个判断", ""]
    for pf, s in data["summary"].items():
        group = [p for p in shown if p["platform"] == pf and not p["flag"]]
        top = max(group, key=lambda p: p["cmp"].get("views") or 0)
        line = f"- {names[pf]} · 流量集中：最高一条「{clean(top['title'])}」占 {pct(s['top_share'], 1)}，中位播放 {fmt(s['median_views'])}"
        if top["first_day_share"] is not None:
            line += f"，首日占累计 {pct(top['first_day_share'], 1)}"
        compounding = [p for p in group if p["d2_over_d1"] and p["d2_over_d1"] > 1]
        line += "。" + (f"有复利（次日反超首日）：{'、'.join(clean(p['title'], 12) for p in compounding)}。" if compounding else "没有作品次日反超首日，播放是一次性的。")
        L.append(line)
        if s["hooked_n"] and s["unhooked_n"] and s["nohook_follow"]:
            ratio = s["hook_follow"] / s["nohook_follow"] if s["nohook_follow"] else None
            L.append(f"- {names[pf]} · 结尾钩与转化：有结尾钩的 {s['hooked_n']} 条合计关注率 {pct(s['hook_follow'])}，无钩的 {s['unhooked_n']} 条 {pct(s['nohook_follow'])}"
                     + (f"，相差 {ratio:.1f} 倍" if ratio else "") + "。这是相关不是因果，但加结尾钩几乎不增加成本。")
        finish = [(p["m"]["finish_rate"], p["m"]["avg_view_sec"], p) for p in group if p["m"]["finish_rate"] is not None]
        if len(finish) >= 2:
            best = max(finish, key=lambda x: x[0])
            L.append(f"- {names[pf]} · 开头与完播：完播最高的是「{clean(best[2]['title'])}」{pct(best[0])}（平均播放 {best[1] or '—'} 秒）。"
                     "完播率和平均播放时长不同步时，短板在开头而不在时长；要定位到秒需要留存曲线。")
    if flagged:
        L.append("- 需要核对的数据：" + "；".join(f"{clean(p['title'])}（{p['flag']}{'，' + p['flag_note'] if p['flag_note'] else ''}）" for p in flagged))

    L += ["", "## 5 结构-表现矩阵（条数为 1 的只能当假设）", ""]
    any_cell = False
    for field, title in (("archetype", "按原型"), ("hook_end", "按结尾钩")):
        for pf, cells in data["matrix"][field].items():
            for value, c in sorted(cells.items(), key=lambda x: -(x[1]["follow_rate"] or 0)):
                any_cell = True
                L.append(f"- {names[pf]} · {title} · {value}：{c['n']} 条，平均播放 {fmt(c['avg_views'])}，完播 {pct(c['finish_rate'])}，"
                         f"关注率 {pct(c['follow_rate'])}，分享率 {pct(c['share_rate'])}{'（假设）' if c['hypothesis'] else ''}")
    if not any_cell:
        L.append("- 作品还没有结构标签，矩阵算不出来。在「看板」里点作品给它补标签（以后在「创作」里发布前就打好）。")

    if accepted:
        L += ["", "## 6 上期计划验收", ""]
        for a in accepted:
            L.append(f"- 「{a['title']}」：{a['overall']}" + (f"（{a['detail']}）" if a.get("detail") else ""))

    if questions:
        L += ["", "## 7 评论区信号", ""]
        for q, n, _ in list(questions)[:5]:
            L.append(f"- {n} 条类似提问：{q[:40]}")

    if bench and bench["hits"]:
        L += ["", "## 7b 对标信号", ""]
        for p in bench["hits"][:5]:
            L.append(f"- {names[p['platform']]}「{p['bench_name']}」：{clean(p['title'], 30)}，互动 {fmt(p['inter'])}，"
                     f"{bench_times(p)}" + (f"，结构 {' × '.join(v for v in (p['tags'].get('archetype'), p['tags'].get('hook_open'), p['tags'].get('hook_end')) if v)}" if p["tags"] else "，未拆解"))
        st = bench["structure"]
        if st["tagged"]:
            top = lambda field: "、".join(f"{v}（{n}）" for v, n in st[field][:3]) or "—"
            L.append(f"- 拆解过的 {st['tagged']} 条爆款里，原型最多的是 {top('archetype')}；开头钩 {top('hook_open')}；结尾钩 {top('hook_end')}。"
                     "对标号验证过的结构，复刻的风险最低。")
    settled = [x for x in lessons if x["status"] in ("有效", "无效")]
    if settled:
        L += ["", "## 7c 打法库（越用越准：验证过的打法会影响下周计划和出稿）", ""]
        L += [f"- 【{x['status']}】{x['title']}：{x['summary']}" for x in settled[:8]]
    if predictions is not None:
        from . import predict
        L += ["", "## 7d 预测准确度（发布前猜的，和实际数据对答案）", ""] + predict.report_lines(predictions, names)
    L += ["", "## 8 下一步创作计划", ""]
    for c in plan:
        t = c["tags"]
        shape = " × ".join(x for x in (t.get("motif"), t.get("archetype"), t.get("hook_open"), t.get("hook_end"), t.get("duration_bin")) if x)
        L.append(f"- **{c['priority']}** {c['title']}　{shape}。{c['change']}。验收：{c['acceptance']}")
    for n in notes:
        L.append(f"- {n}")
    if plan:
        L.append("\n点「计划」页的「生成本周计划」，这些选题会进入创作流程，发布后自动验收。")
    return "\n".join(L)


def last_acceptance(con):
    from . import contents, planner
    week = planner.week_label(time.time() - 7 * 86400)
    items = [c for c in contents.list_all(con, True) if c["week"] == week and c["source"] == "plan"]
    out = []
    for c in contents.with_acceptance(con, items):
        r = c["acceptance_result"]
        detail = "；".join(f"{config.PLATFORMS[x['platform']]['name']} {method.METRIC_NAMES[x['metric']]} "
                          f"{pct(x['value']) if x['metric'] != 'views' else fmt(x['value'])}（目标 {x['target']}）"
                          for x in r["results"] if x["value"] is not None)
        out.append({"title": c["title"], "overall": r["overall"], "detail": detail})
    return out


def gather(con, days):
    """复盘要用的全部材料：数据、计划候选、上期验收、评论提问、对标信号。"""
    from . import benchmarks, planner
    data = analytics.build(con, days)
    questions = planner.top_questions(con, days or 3650)
    from . import playbook
    prefs = {f: playbook.preferred(con, f) for f in ("hook_end", "motif")}
    plan, notes = planner.candidates(data, questions, prefs)
    bench_items, bench_notes = benchmarks.plan_candidates(con, days or 30)
    return {"data": data, "plan": plan + bench_items, "notes": notes + bench_notes, "accepted": last_acceptance(con),
            "questions": questions, "bench": benchmarks.load(con, days or 30), "lessons": playbook.lessons(con), "insights": playbook.insights(con, "kept"),
            "predictions": predict_accuracy(con, data, days)}


def ai_brief(g):
    """交给 AI 写复盘时的完整说明（规则、字典、定位卡、数据摘要）。"""
    summary = summarize(g["data"], g["plan"], g["accepted"], g["questions"], g["bench"], g.get("predictions"))
    kept = g.get("insights") or []
    known = ("记下的经验（之前的复盘里确认过的，这次看看数据是否还支持）：\n" + "\n".join(f"- {x['text']}" for x in kept[:10])) if kept else ""
    return "\n\n".join([SYSTEM_PROMPT, method.dictionary_text(), method.profile_text(method.load_profile()), *([known] if known else []),
                         "数据摘要（JSON）：", json.dumps(summary, ensure_ascii=False, default=str)])


def save(con, days, kind, content):
    with con:
        cur = con.execute("INSERT INTO reviews (created_at, days, kind, content) VALUES (?,?,?,?)",
                          (int(time.time() * 1000), days or 0, kind, content))
    return cur.lastrowid


def create(con, days, use_ai=False):
    """规则复盘立即生成；AI 复盘由 AI 助手（或配置的模型接口）按 ai_brief 写，见 agent.py。"""
    g = gather(con, days)
    if use_ai:
        return save(con, days, "ai", chat([{"role": "user", "content": ai_brief(g)}]))
    return save(con, days, "rule", rule_report(g["data"], g["plan"], g["notes"], g["accepted"], g["questions"], g["bench"], g["lessons"], g["predictions"]))
