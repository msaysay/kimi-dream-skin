"""内容：一条选题从「计划 → 创作 → 发布 → 数据验收」的全过程。

两套创作工作流：
- 选题直出：选题 → 定结构 → 写脚本 → AI 全自动生成 → 成片检查 → 发布 → 数据验收
- 口播剪辑：选题 → 定结构 → 口播提纲 → 录口播 → AI 剪口播（组件 + Skill）→ 成片检查 → 发布 → 数据验收
阶段和检查项可以在「设置 → 创作工作流」里改，存进 data/workflows.json。
"""
import json
import time
from difflib import SequenceMatcher

from . import analytics, config, method
from .db import rows

COMMON_HEAD = [
    ("选题", ["过定位闸：「我是谁 / 我给谁 / 凭什么是我」三句都成立", "过用户闸：卡点确实是这个任务",
              "过路径闸：结尾钩指向下一跳，而不是笼统的「关注我」"]),
    ("定结构", ["四层标签在发布前打完（母题 / 原型 / 三段钩子 / 形式参数）",
                "和上一批已验证的配置比，只改了一个变量", "母题与原型匹配，没有踩「不要搭配」"]),
]
COMMON_TAIL = [
    ("成片检查", ["第一帧兑现开头钩", "最后 3 秒有一个具体的下一步动作（领什么、去哪领、下一集是什么）",
                  "时长落在选定的时长档", "四段各用一句话说得出来"]),
    ("发布", ["三个平台分别改结尾：抖音不强求关注，视频号给下一步，小红书标题正文写进搜索词",
              "评论区领取关键词的自动回复规则已开启", "发布 2 小时内回看一次公开状态"]),
    ("数据验收", ["D1 快照已到", "D3 快照对照目标指标写结论", "没达标就换假设，不要把同一条再做一遍"]),
]
DEFAULT_WORKFLOWS = {
    "topic": {"name": "选题直出", "desc": "选题定好后，AI 全自动生成成片（不露脸 / 数字人）",
              "stages": COMMON_HEAD + [
                  ("写脚本", ["按原型骨架把四段任务分配到时间码", "开头 3 秒那句话、结尾 3 秒那个动作逐条重写"]),
                  ("AI 生成", ["配音、画面 / 数字人、字幕由全自动流程生成", "生成后先自己看一遍，删掉 AI 腔的句子"]),
              ] + COMMON_TAIL},
    "talk": {"name": "口播剪辑", "desc": "先录口播，再用剪辑组件和 Skill 让 AI 剪成片",
             "stages": COMMON_HEAD + [
                 ("口播提纲", ["按原型骨架写提纲，开头一句和结尾一句逐字写死", "标出需要插画面 / 组件的位置"]),
                 ("录口播", ["一镜到底，第一句不满意就重录", "环境安静，收音清楚"]),
                 ("AI 剪口播", ["用剪辑组件和 Skill 去口误、加字幕、插画面", "检查节奏：每 5–8 秒一个新信息点"]),
             ] + COMMON_TAIL},
}
DONE_STAGES = ("数据验收",)


def load_workflows():
    f = config.DATA / "workflows.json"
    if f.exists():
        try:
            saved = json.loads(f.read_text(encoding="utf-8"))
            if all(k in saved and saved[k].get("stages") for k in DEFAULT_WORKFLOWS):
                return saved
        except (ValueError, OSError):
            pass
    return {k: {"name": v["name"], "desc": v["desc"], "stages": [[s, list(items)] for s, items in v["stages"]]}
            for k, v in DEFAULT_WORKFLOWS.items()}


def save_workflows_text(texts):
    """每个工作流一段文字：一行一个阶段，「阶段名：检查项1；检查项2」。"""
    wfs = load_workflows()
    for key, text in texts.items():
        if key not in wfs:
            continue
        stages = []
        for line in str(text).splitlines():
            line = line.strip()
            if not line:
                continue
            name, _, items = line.replace(":", "：").partition("：")
            stages.append([name.strip()[:20], [i.strip() for i in items.replace(";", "；").split("；") if i.strip()]])
        if len(stages) >= 2:
            wfs[key]["stages"] = stages
    config.ensure_dirs()
    (config.DATA / "workflows.json").write_text(json.dumps(wfs, ensure_ascii=False, indent=2), encoding="utf-8")
    return wfs


def workflows_text():
    return {k: "\n".join(f"{s}：{'；'.join(items)}" for s, items in v["stages"]) for k, v in load_workflows().items()}


def stage_names(workflow):
    return [s for s, _ in load_workflows()[workflow]["stages"]]


def pick_workflow(tags):
    """真人出镜、观点和答疑类走口播剪辑，其余默认选题直出。"""
    if tags.get("presence") == "真人" or tags.get("archetype") in ("观点论证型", "答疑型"):
        return "talk"
    return "topic"


FIELDS = ["title", "workflow", "stage", "priority", "motif", "archetype", "hook_open", "hook_mid", "hook_end", "duration_bin",
          "presence", "rhythm", "cover_type", "basis", "acceptance", "week", "schedule_date", "script", "notes", "source", "verdict_from",
          "conclusion", "pub_desc", "pub_tags"]


def now_ms():
    return int(time.time() * 1000)


def save(con, data):
    """新建或更新一条内容。标签只接受封闭字典里的值。"""
    data = dict(data)
    for f, (_, values) in method.TAG_FIELDS.items():
        if data.get(f) and data[f] not in values:
            raise ValueError(f"{method.TAG_FIELDS[f][0]}「{data[f]}」不在字典里")
    if data.get("id") and not data.get("workflow"):  # 更新时沿用原来的工作流，不要按标签重新猜
        old = rows(con, "SELECT workflow FROM contents WHERE id=?", int(data["id"]))
        data["workflow"] = old[0]["workflow"] if old else None
    wf = data.get("workflow") or pick_workflow(data)
    if wf not in load_workflows():
        raise ValueError("不认识的工作流")
    data["workflow"] = wf
    if data.get("stage") and data["stage"] not in stage_names(wf):
        data["stage"] = stage_names(wf)[0]
    if "targets" in data:
        data["targets"] = json.dumps([t for t in data["targets"] if t.get("metric") in method.METRIC_NAMES], ensure_ascii=False)
    if "checklist" in data:
        data["checklist"] = json.dumps(data["checklist"], ensure_ascii=False)
    if "experiment" in data and not isinstance(data["experiment"], str):
        data["experiment"] = json.dumps(data["experiment"], ensure_ascii=False) if data["experiment"] else None
    cols = [f for f in FIELDS + ["targets", "checklist", "experiment"] if f in data]
    with con:
        if data.get("id"):
            con.execute(f"UPDATE contents SET {', '.join(c + '=?' for c in cols)}, updated_at=? WHERE id=?",
                        [data[c] for c in cols] + [now_ms(), int(data["id"])])
            return int(data["id"])
        if not (data.get("title") or "").strip():
            raise ValueError("选题不能为空")
        data.setdefault("stage", stage_names(wf)[0])
        cols = [f for f in FIELDS + ["targets", "checklist", "experiment"] if f in data]
        cur = con.execute(f"INSERT INTO contents ({', '.join(cols)}, created_at, updated_at) VALUES ({', '.join('?' * len(cols))}, ?, ?)",
                          [data[c] for c in cols] + [now_ms(), now_ms()])
        return cur.lastrowid


def get(con, content_id):
    found = rows(con, "SELECT * FROM contents WHERE id=?", content_id)
    if not found:
        raise ValueError("没有这条内容")
    return decode(found[0])


def decode(c):
    c["targets"] = json.loads(c.get("targets") or "[]")
    c["checklist"] = json.loads(c.get("checklist") or "{}")
    c["experiment"] = json.loads(c["experiment"]) if c.get("experiment") else None
    return c


def list_all(con, include_archived=False):
    items = [decode(c) for c in rows(con, f"SELECT * FROM contents {'' if include_archived else 'WHERE archived=0'} ORDER BY id DESC")]
    links = {}
    for r in rows(con, "SELECT cp.content_id, cp.platform, cp.post_id, p.title, p.account FROM content_posts cp LEFT JOIN posts p USING (platform, post_id)"):
        links.setdefault(r["content_id"], []).append(r)
    for c in items:
        c["posts"] = links.get(c["id"], [])
    return items


def link(con, content_id, platform, post_id):
    with con:
        con.execute("INSERT OR REPLACE INTO content_posts VALUES (?,?,?)", (content_id, platform, post_id))


def unlink(con, platform, post_id):
    with con:
        con.execute("DELETE FROM content_posts WHERE platform=? AND post_id=?", (platform, post_id))


def backfill(con, platform, post_id, tags):
    """给已经发出去的历史作品补标签：建一条内容并关联上。"""
    post = rows(con, "SELECT * FROM posts WHERE platform=? AND post_id=?", platform, post_id)
    if not post:
        raise ValueError("没有这条作品")
    existing = rows(con, "SELECT content_id FROM content_posts WHERE platform=? AND post_id=?", platform, post_id)
    data = {k: v for k, v in tags.items() if k in method.TAG_FIELDS}
    if existing:
        data["id"] = existing[0]["content_id"]
        return save(con, data)
    data.update(title=post[0]["title"][:80], source="backfill", stage="数据验收")
    data["workflow"] = tags.get("workflow") or pick_workflow(data)
    cid = save(con, data)
    link(con, cid, platform, post_id)
    return cid


def auto_link(con):
    """发布任务带着内容编号；采集到作品后按平台、账号、标题和时间把作品关联回内容。返回新关联的条数。"""
    linked = {(r["platform"], r["post_id"]) for r in rows(con, "SELECT platform, post_id FROM content_posts")}
    count = 0
    for job in rows(con, "SELECT params, finished_at FROM jobs WHERE type='publish' AND status='done'"):
        p = json.loads(job["params"])
        if not p.get("content_id"):
            continue
        if rows(con, "SELECT 1 FROM content_posts WHERE content_id=? AND platform=?", p["content_id"], p["platform"]):
            continue
        best, score = None, 0.0
        for post in rows(con, "SELECT post_id, title, post_time FROM posts WHERE platform=? AND account=?", p["platform"], p["account"]):
            if (p["platform"], post["post_id"]) in linked:
                continue
            if (post["post_time"] or 0) < (job["finished_at"] or 0) - 2 * 86400 * 1000:
                continue
            sim = SequenceMatcher(None, analytics.norm_title(p["title"]), analytics.norm_title(post["title"])).ratio()
            if sim > score:
                best, score = post, sim
        if best and score >= 0.5:
            link(con, p["content_id"], p["platform"], best["post_id"])
            linked.add((p["platform"], best["post_id"]))
            count += 1
            with con:
                con.execute("UPDATE contents SET stage='数据验收', updated_at=? WHERE id=?", (now_ms(), p["content_id"]))
    return count


def evaluate(content, posts_by_key):
    """验收：每个目标指标在对应快照档位上达没达标。posts_by_key 来自 analytics.build。"""
    results = []
    for platform, post_id in [(l["platform"], l["post_id"]) for l in content.get("posts", [])]:
        post = posts_by_key.get((platform, post_id))
        if not post:
            continue
        for t in content["targets"]:
            slot = t.get("slot") or "D3"
            snap = post["slots"].get(slot)
            if snap is None:
                state, value = ("pending" if (post["age_days"] or 0) < int(slot[1:]) + 1 else "missing"), None
            else:
                value = analytics.rates(snap, post["intent"]).get(t["metric"])
                state = "unknown" if value is None else "met" if value >= float(t["value"]) else "unmet"
            results.append({"platform": platform, "metric": t["metric"], "target": t["value"], "slot": slot, "value": value, "state": state})
    if not results:
        overall = "未发布" if not content.get("posts") else "无目标"
    elif any(r["state"] == "pending" for r in results):
        overall = "等数据"
    elif all(r["state"] == "met" for r in results):
        overall = "达成"
    elif any(r["state"] == "met" for r in results):
        overall = "部分达成"
    elif all(r["state"] in ("missing", "unknown") for r in results):
        overall = "缺快照"
    else:
        overall = "未达成"
    return {"overall": overall, "results": results}


def with_acceptance(con, items):
    data = analytics.build(con)
    by_key = {(p["platform"], p["post_id"]): p for w in data["works"] for p in w["posts"]}
    for c in items:
        c["acceptance_result"] = evaluate(c, by_key)
    return items


# ---------- 脚本草稿 ----------
def scaffold(content, profile):
    """不依赖大模型的脚本骨架：按原型把四段任务和标签要求写成待填的提纲。"""
    arch = content.get("archetype") or "教程型"
    skel = method.SKELETONS.get(arch, method.SKELETONS["教程型"])
    talk = content.get("workflow") == "talk"
    lines = [f"# {content.get('title') or '（选题）'}", "",
             f"结构：{content.get('motif') or '母题未定'} × {arch} × 开头「{content.get('hook_open') or '未定'}」"
             f" × 中段「{content.get('hook_mid') or '未定'}」 × 结尾「{content.get('hook_end') or '未定'}」 × {content.get('duration_bin') or '时长未定'}", ""]
    for seg, task in zip(method.SEGMENTS, skel):
        lines.append(f"## {seg}")
        lines.append(f"- 任务：{task}")
        if seg.startswith("0–3"):
            hint = method.HOOKS_OPEN.get(content.get("hook_open") or "", "")
            lines.append(f"- 开头钩「{content.get('hook_open') or '未定'}」：{hint}")
            lines.append("- 第一句（逐字写死）：")
        elif seg.startswith("最后"):
            hint = method.HOOKS_END.get(content.get("hook_end") or "", "")
            lines.append(f"- 结尾钩「{content.get('hook_end') or '未定'}」：{hint}")
            if content.get("hook_end") in ("资产给予", "行动指令", "引导私域"):
                lines.append(f"- 落点：评论区打「{profile.get('asset_keyword')}」领 {profile.get('asset_desc')}，{profile.get('asset_path')}")
            lines.append("- 最后一句（逐字写死）：")
        else:
            lines.append("- 要点：")
        if talk:
            lines.append("- 画面 / 组件插入点：")
        lines.append("")
    lines += ["## 四句自检", "1. 给理由：", "2. 给结构：", "3. 给内容：", "4. 给下一步（写不出具体动作就不发）：", "",
              "## 三个平台的结尾怎么改", *[f"- {config.PLATFORMS[k]['name']}：{v}" for k, v in method.PLATFORM_ROLES.items()]]
    return "\n".join(lines)


WRITING_STAGES = ("写脚本", "口播提纲")


def writing_stage(workflow):
    names = stage_names(workflow)
    return next((s for s in names if s in WRITING_STAGES), names[min(2, len(names) - 1)])


def script_brief(con, content):
    """出稿的完整任务说明：选题、定位、结构（没定的让 AI 按字典定）、骨架、数据依据、对标拆解、验证过的结构、输出格式。"""
    from . import analytics, benchmarks
    profile = method.load_profile()
    talk = content.get("workflow") == "talk"
    tags = {f: content.get(f) for f in method.TAG_FIELDS if content.get(f)}
    L = [f"# 任务：把这条选题写成能直接开拍的{'口播稿' if talk else '脚本'}", "",
         f"选题：{content.get('title')}",
         *(["（这是计划生成的选题「方向」：请先定一个具体、能兑现的选题，输出里「标题」的第一条会作为正式选题。）"]
           if str(content.get("title") or "").startswith("【") else []),
         f"工作流：{'口播剪辑（先录口播，再用剪辑组件和 Skill 让 AI 剪）' if talk else '选题直出（AI 全自动生成配音、画面 / 数字人、字幕）'}",
         "", "## 账号定位（不符合的地方要改选题角度，不要硬写）", method.profile_text(profile)]
    if content.get("basis"):
        L += ["", "## 为什么做这条（数据依据）", content["basis"]]
    if content.get("acceptance") or content.get("targets"):
        fmt = lambda t: f"{float(t['value']):,.0f}" if t["metric"] == "views" else f"{float(t['value']) * 100:.2f}%"
        L += ["", "## 这条要达成的目标", "；".join(f"{method.METRIC_NAMES.get(t['metric'], t['metric'])} ≥ {fmt(t)}（{t.get('slot', 'D3')}）"
                                                for t in content.get("targets") or []) + (f"。{content['acceptance']}" if content.get("acceptance") else "")]
    if content.get("source") == "benchmark" and content.get("notes"):
        L += ["", "## 对标爆款的拆解（借结构，不照抄选题）", content["notes"][:3000]]
    if tags:
        L += ["", "## 已定的结构（不许改）"] + [f"- {method.TAG_FIELDS[f][0]}：{v}" for f, v in tags.items()]
    missing = [f for f in ("motif", "archetype", "hook_open", "hook_mid", "hook_end", "duration_bin") if f not in tags]
    if missing:
        try:
            data = analytics.build(con)
            proven = []
            for field in ("archetype", "hook_end"):
                for pf, cells in data["matrix"][field].items():
                    best = max(cells.items(), key=lambda x: x[1]["follow_rate"] or 0, default=None)
                    if best and best[1]["follow_rate"]:
                        proven.append(f"自己账号 {config.PLATFORMS[pf]['name']}：{method.TAG_FIELDS[field][0]}「{best[0]}」关注率最高（{best[1]['follow_rate'] * 100:.2f}%，{best[1]['n']} 条）")
            bst = benchmarks.load(con, 30)["structure"]
            if bst["tagged"]:
                proven.append("对标爆款里最常见：" + "；".join(f"{method.TAG_FIELDS[f][0]} {bst[f][0][0]}" for f in ("archetype", "hook_open", "hook_end") if bst[f]))
        except Exception:
            proven = []
        L += ["", "## 还没定的结构：请你按字典选定（写进输出开头）", "要定：" + "、".join(method.TAG_FIELDS[f][0] for f in missing),
              method.dictionary_text()]
        if proven:
            L += ["已经验证过的结构（优先考虑）："] + [f"- {x}" for x in proven]
    from . import playbook
    pb = playbook.brief_text(con)
    if pb:
        L += ["", pb]
    arch = content.get("archetype")
    skel = method.SKELETONS.get(arch) if arch else None
    L += ["", "## 四段骨架", f"按原型「{arch}」：" if skel else "按你选定的原型（各原型的骨架如下）："]
    for a, sk in ([(arch, skel)] if skel else method.SKELETONS.items()):
        L.append(f"- {a}：" + "；".join(f"{seg} → {task}" for seg, task in zip(method.SEGMENTS, sk)))
    L += ["", "## 写作要求",
          "- 开头 3 秒那一句、结尾 3 秒那个动作逐字写死；结尾要写清领什么、去哪领、或下一集是什么。",
          f"- 结尾的领取动作只指向评论区关键词「{profile.get('asset_keyword') or 'skill'}」、主页置顶或私信；不要写微信号、手机号或外链。",
          "- " + ("口语化，像当面说话；每段后面用「画面：」标出要插的画面或剪辑组件。" if talk else "旁白适合 AI 配音，句子短；每段写清画面。"),
          "- 时长落在选定的时长档里；每 5–8 秒一个新信息点。",
          "", "## 输出格式（照抄这个结构，开头的 --- 块会被程序读取）",
          "```", "---", "母题: <字典里的值>", "原型: <字典里的值>", "开头钩: <字典里的值>", "中段留人: <字典里的值>", "结尾钩: <字典里的值>",
          "时长档: <字典里的值>", "标题: 标题1 | 标题2 | 标题3 | 标题4 | 标题5",
          "简介: <发布时的简介，60 字以内，写进搜索词>", "话题: 话题1, 话题2, 话题3（不带 #，3–5 个）", "---",
          f"## 0–3 秒 · 给理由", "…（时间码、旁白、画面）", "## 3–8 秒 · 给结构", "…", "## 8 秒至主体 · 给内容", "…", "## 最后 3 秒 · 给下一步", "…",
          "## 四句自检", "1. 给理由：… 2. 给结构：… 3. 给内容：… 4. 给下一步：…",
          "## 三个平台的结尾怎么改", *[f"- {config.PLATFORMS[k]['name']}：…（{v}）" for k, v in method.PLATFORM_ROLES.items()], "```"]
    return "\n".join(L)
