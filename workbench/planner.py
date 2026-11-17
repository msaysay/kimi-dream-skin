"""下一步创作计划：把诊断结论翻译成带验收口径的选题（方法论第 7 章）。

规则先定「形状」（母题 × 原型 × 开头钩 × 结尾钩 × 时长档，只改一个变量），再交给 AI 把形状写成具体标题。
判据顺序固定：先补短板且可复用（P0），再换壳扩母题（P1），最后低成本试探和待重测（P2）。
"""
import datetime
import json
import re
import time

from . import analytics, config, contents, method, review
from .db import rows

QUESTION = re.compile(r"[?？]|怎么|如何|哪里|哪儿|多少|能不能|可不可以|是不是|有没有|求|为什么|啥|什么")
# 垂直型「换壳」：把工具 / 流程装进更泛的母题
SHELL = {"AI 制作流程拆解": "AI 与职场商业应用", "AI 工具实测": "AI 与职场商业应用",
         "AI 行业人物与公司故事": "AI 概念科普", "AI 概念科普": "AI 与职场商业应用",
         "AI 与职场商业应用": "个人实践与复盘", "个人实践与复盘": "AI 与职场商业应用"}


def week_label(ts=None):
    d = datetime.date.fromtimestamp(ts or time.time())
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_days(ts=None):
    d = datetime.date.fromtimestamp(ts or time.time())
    monday = d - datetime.timedelta(days=d.weekday())
    return [monday + datetime.timedelta(days=i) for i in range(7)]


def pct(x, d=2):
    return "—" if x is None else f"{x * 100:.{d}f}%"


def target(metric, value, slot="D3"):
    return {"metric": metric, "value": round(value, 4), "slot": slot}


def short(title, n=18):
    t = re.sub(r"#\S+", "", title or "").strip()
    return (t[:n] + "…") if len(t) > n else t


def best_post(work):
    scored = [p for p in work["posts"] if p["total"] is not None and not p["flag"]]
    return max(scored, key=lambda p: p["total"]) if scored else None


def candidates(data, comment_questions=()):
    """返回计划条目（还没写进数据库）。"""
    params = data["params"]
    out, notes = [], []
    seen_motifs = set()
    for w in data["works"]:
        flagged = [p for p in w["posts"] if p["flag"]]
        for p in flagged:
            out.append({"priority": "P2", "title": f"【待重测】{short(p['title'])}：确认公开后重发", "tags": dict(p["tags"]),
                        "basis": f"{config.PLATFORMS[p['platform']]['name']}这条采集时状态为「{p['flag']}」，数据不可采信，本轮不下结论",
                        "change": "不改结构，只重新取证", "targets": [target("finish_rate", params["thresholds"][p["platform"]]["finish_rate"][1], "D1")],
                        "acceptance": "发布 2 小时内核对公开状态；D1 看完播率与推荐占比", "verdict_from": "待重测"})
        p = best_post(w)
        if p is None:
            continue
        tags = dict(w["tags"] or p["tags"])
        if tags.get("motif"):
            seen_motifs.add(tags["motif"])
        th = params["thresholds"][p["platform"]]
        name = config.PLATFORMS[p["platform"]]["name"]
        m = p["m"]
        base = (f"{name}「{short(p['title'])}」：播放 {m['views']:.0f}、完播 {pct(m['finish_rate'])}、关注率 {pct(m['follow_rate'])}、"
                f"分享率 {pct(m['share_rate'])}；流量力 {p['flow']} / 转化力 {p['conv']} → {p['verdict']}")
        if not tags.get("archetype"):
            notes.append(f"「{short(p['title'])}」还没有结构标签，无法按结构出续作。先在「看板」里给它补标签。")
            continue
        v = p["verdict"]
        if v == "双强型":
            out.append({"priority": "P0", "title": f"【系列续集】{short(p['title'])} · 下一集", "tags": tags,
                        "basis": base + "。结构已验证，加量复制", "change": "不改结构，只换内容（做成系列）",
                        "targets": [target("follow_rate", max(th["follow_rate"][1], (m["follow_rate"] or 0) * 0.9)),
                                    target("share_rate", max(th["share_rate"][1], (m["share_rate"] or 0) * 0.8))],
                        "acceptance": "D3 关注率与分享率都不低于上一集的九成、八成即成立；第 1 集置顶或进合集", "verdict_from": v})
        elif v == "泛流量型":
            new_end = "资产给予" if tags.get("motif") in ("AI 工具实测", "AI 制作流程拆解") else method.COMBOS[tags["archetype"]][2]
            if tags.get("hook_end") == new_end:
                new_end = "关注理由" if new_end != "关注理由" else "系列预告"
            t2 = dict(tags, hook_end=new_end)
            out.append({"priority": "P0", "title": f"【同结构续作】和「{short(p['title'])}」同类的下一个选题，结尾补承接", "tags": t2,
                        "basis": base + f"。算法愿意给量，观众没留下：结尾钩「{tags.get('hook_end') or '无'}」",
                        "change": f"唯一改动：结尾钩 {tags.get('hook_end') or '无'} → {new_end}",
                        "targets": [target("follow_rate", th["follow_rate"][1]), target("finish_rate", max(th["finish_rate"][1], (m["finish_rate"] or 0) * 0.75))],
                        "acceptance": f"D3 关注率 ≥ {pct(th['follow_rate'][1])} 且完播不掉到原来的七成五以下；关注率翻倍以上即成立", "verdict_from": v})
        elif v == "垂直型":
            new_motif = SHELL.get(tags.get("motif"), "AI 与职场商业应用")
            t2 = dict(tags, motif=new_motif)
            out.append({"priority": "P1", "title": f"【换壳扩曝光】把「{short(p['title'])}」的结构装进「{new_motif}」", "tags": t2,
                        "basis": base + "。内容对人有用，但入口太窄", "change": f"唯一改动：母题 {tags.get('motif')} → {new_motif}（原型和结尾钩不动）",
                        "targets": [target("views", th["views"][1]), target("follow_rate", max(th["follow_rate"][2], (m["follow_rate"] or 0) * 0.7))],
                        "acceptance": f"D3 播放 ≥ {th['views'][1]:.0f} 且关注率守在原来的七成以上", "verdict_from": v})
        elif v == "低效型":
            notes.append(f"「{short(p['title'])}」判为低效型（流量力 {p['flow']} / 转化力 {p['conv']}）：这个原型先停做，不追加投入。")
    # 母题缺口：没做过的母题是「换壳扩曝光」最省力的入口
    used = {w["tags"].get("motif") for w in data["works"] if w["tags"]}
    for motif in method.MOTIFS:
        if motif in used or not used:
            continue
        arch = next(a for a, c in method.COMBOS.items() if motif in c[0])
        _, ho, he, _ = method.COMBOS[arch]
        th = next(iter(params["thresholds"].values()))
        out.append({"priority": "P2", "title": f"【母题试探】{motif}：{method.MOTIFS[motif]}",
                    "tags": {"motif": motif, "archetype": arch, "hook_open": ho, "hook_end": he, "duration_bin": "30–60s"},
                    "basis": f"本期「{motif}」母题 0 条，是没开发的选题来源", "change": "新母题，沿用推荐组合",
                    "targets": [target("follow_rate", th["follow_rate"][1])], "acceptance": "只看值不值得做第二条：关注率高于本期中位数才继续",
                    "verdict_from": "母题缺口"})
        break  # 一周只试一个新母题
    for q, n, motif in list(comment_questions)[:1]:
        th = next(iter(params["thresholds"].values()))
        out.append({"priority": "P2", "title": f"【评论区答疑】{q[:30]}",
                    "tags": {"motif": motif or "AI 概念科普", "archetype": "答疑型", "hook_open": "身份代入", "hook_end": "系列预告", "duration_bin": "30–60s"},
                    "basis": f"评论区有 {n} 条类似提问，需求已经出现，直接取材", "change": "新原型（答疑型），制作成本最低",
                    "targets": [target("comment_rate", th["comment_rate"][1])], "acceptance": "评论率 ≥ 良好线；看是否带动前作的主页访问",
                    "verdict_from": "评论区提问"})
    # 每周最多 max_p0 条 P0，多出来的降到 P1
    p0 = [c for c in out if c["priority"] == "P0"]
    for c in p0[params["max_p0"]:]:
        c["priority"] = "P1"
    order = {"P0": 0, "P1": 1, "P2": 2}
    out.sort(key=lambda c: order[c["priority"]])
    return out, notes


def top_questions(con, days=14):
    """评论区里重复出现的提问（按前 8 个字粗分组）。"""
    since = (time.time() - days * 86400) * 1000
    kw = (method.load_profile().get("asset_keyword") or "").strip().lower()
    intent_rules = {r["id"] for r in rows(con, "SELECT id FROM comment_rules WHERE is_intent=1")}
    groups = {}
    for c in rows(con, """SELECT c.text, c.platform, c.post_id, c.rule_id, ct.motif FROM comments c
                          LEFT JOIN content_posts cp ON cp.platform=c.platform AND cp.post_id=c.post_id
                          LEFT JOIN contents ct ON ct.id=cp.content_id
                          WHERE c.is_question=1 AND COALESCE(c.ctime, c.fetched_at) >= ?""", since):
        if (kw and kw in (c["text"] or "").lower()) or c["rule_id"] in intent_rules:
            continue  # 「求 skill」是领取意向，不是选题需求
        key = analytics.norm_title(c["text"])[:8]
        if key:
            g = groups.setdefault(key, [c["text"], 0, c["motif"]])
            g[1] += 1
    return sorted((tuple(g) for g in groups.values()), key=lambda g: -g[1])


def generate(con, days=14, ai=False, log=print):
    """生成本周计划并写进「内容」，已有同名条目就跳过。返回新建的内容编号。"""
    data = analytics.build(con, days)
    items, notes = candidates(data, top_questions(con, days))
    week = week_label()
    today = datetime.date.today()
    ahead = [d for d in week_days() if d >= today] or [today]
    # P0 尽量隔天排开，剩下的往后填；本周排不下就排到周日
    slots = ahead[::2] + ahead[1::2]
    existing = {r["title"] for r in rows(con, "SELECT title FROM contents WHERE week=?", week)}
    created = []
    for i, c in enumerate(items):
        if c["title"] in existing:
            continue
        tags = {k: v for k, v in c["tags"].items() if k in method.TAG_FIELDS}
        cid = contents.save(con, dict(tags, title=c["title"], priority=c["priority"], basis=f"{c['basis']}。{c['change']}",
                                      targets=c["targets"], acceptance=c["acceptance"], week=week, source="plan",
                                      verdict_from=c["verdict_from"], schedule_date=str(slots[min(i, len(slots) - 1)]),
                                      workflow=contents.pick_workflow(tags)))
        created.append(cid)
    log(f"本周（{week}）新增 {len(created)} 条选题" + ("；" + "；".join(notes) if notes else ""))
    if ai and created:
        refine_titles(con, created, log)
    return created


def refine_titles(con, ids, log=print):
    """让大模型把「形状」写成具体可拍的标题。结构标签不允许改。"""
    profile = method.load_profile()
    items = [contents.get(con, i) for i in ids]
    brief = [{"id": c["id"], "方向": c["title"], "母题": c["motif"], "原型": c["archetype"], "开头钩": c["hook_open"],
              "结尾钩": c["hook_end"], "依据": c["basis"]} for c in items]
    prompt = ("下面是本周的选题方向，结构已经由数据决定，不许改动。请为每一条写一个具体可拍的选题标题（30 字以内），"
              "要符合账号定位、兑现开头钩。只输出 JSON 数组：[{\"id\": 编号, \"title\": \"标题\"}]。\n\n"
              + method.profile_text(profile) + "\n\n" + json.dumps(brief, ensure_ascii=False))
    text = review.chat([{"role": "user", "content": prompt}])
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        raise RuntimeError("大模型没有按格式返回标题")
    done = 0
    for item in json.loads(match.group(0)):
        if int(item.get("id", 0)) in ids and item.get("title"):
            c = contents.get(con, int(item["id"]))
            contents.save(con, {"id": c["id"], "title": str(item["title"])[:60],
                                "notes": (c["notes"] + "\n" if c["notes"] else "") + f"原方向：{c['title']}"})
            done += 1
    log(f"大模型细化了 {done} 条选题标题")
