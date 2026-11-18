"""AI 待办：工作台里所有要大模型做的事（出稿、AI 复盘、对标拆解、细化计划标题）都排成待办，
由你正在用的 AI 助手（Claude Code、Codex、WorkBuddy……）领走去做，做完写回来。工作台自己不调模型、不用配 API。

助手那边的用法见仓库根目录的 AGENTS.md：
    ning agent todo            看有哪些待办
    ning agent brief <编号>    打印完整任务说明（含输出格式）
    ning agent submit <编号>   读 data/agent/task-<编号>.md，校验后写回
    ning agent new "选题"      直接给一个选题出稿（建内容 + 打印说明，一步）
工作台也能自己处理这些待办：本机装了 Claude Code / Codex / CodeBuddy 并登录过，就直接调用它们（见 runner.py），
用的是你自己在这些产品里的额度；或者在「设置 → AI → 高级」里配一个模型接口。
"""
import json
import re
import time

from . import config, contents, method
from .db import rows

KINDS = {"script": "出稿", "review": "AI 复盘", "bench": "拆解对标爆款", "titles": "把选题方向写成标题", "hot": "挑适合做的热点"}
TAG_LABELS = {name: field for field, (name, _) in method.TAG_FIELDS.items()}


def now_ms():
    return int(time.time() * 1000)


def task_file(task_id):
    d = config.DATA / "agent"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"task-{task_id}.md"


def create(con, kind, ref, params=None):
    """建一条待办；同一对象已经有未完成的同类待办就直接返回它。"""
    if kind not in KINDS:
        raise ValueError("不认识的待办类型")
    ref = str(ref)
    old = rows(con, "SELECT id FROM agent_tasks WHERE kind=? AND ref=? AND status='pending'", kind, ref)
    if old:
        return old[0]["id"]
    with con:
        return con.execute("INSERT INTO agent_tasks (kind, ref, params, created_at) VALUES (?,?,?,?)",
                           (kind, ref, json.dumps(params or {}, ensure_ascii=False), now_ms())).lastrowid


def label(con, t):
    p = json.loads(t["params"] or "{}")
    if t["kind"] == "script":
        c = rows(con, "SELECT title FROM contents WHERE id=?", int(t["ref"]))
        return f"出稿：{c[0]['title'] if c else '（内容已删除）'}"
    if t["kind"] == "review":
        return f"AI 复盘：{'近 ' + str(p.get('days')) + ' 天' if p.get('days') else '全部数据'}"
    if t["kind"] == "bench":
        pf, pid = t["ref"].split("|", 1)
        b = rows(con, "SELECT title FROM bench_posts WHERE platform=? AND post_id=?", pf, pid)
        return f"拆解对标爆款：{(b[0]['title'] if b else pid)[:30]}"
    if t["kind"] == "hot":
        return f"从 {len(p.get('ids') or [])} 个热点里挑适合做的"
    return f"把 {len(t['ref'].split(','))} 条选题方向写成具体标题"


def pending(con):
    out = rows(con, "SELECT * FROM agent_tasks WHERE status='pending' ORDER BY id")
    for t in out:
        t["label"] = label(con, t)
    return out


def brief(con, task_id, direct=False):
    t = get(con, task_id)
    p = json.loads(t["params"] or "{}")
    if t["kind"] == "script":
        text = contents.script_brief(con, contents.get(con, int(t["ref"])))
    elif t["kind"] == "review":
        from . import review
        from . import playbook
        text = ("# 任务：写一份周复盘\n\n" + review.ai_brief(review.gather(con, p.get("days") or None))
                + "\n\n## 输出格式\n直接输出 Markdown 复盘全文。" + playbook.INSIGHT_ASK)
    elif t["kind"] == "bench":
        from . import benchmarks
        pf, pid = t["ref"].split("|", 1)
        text = ("# 任务：拆解一条对标爆款\n\n" + benchmarks.analyze_brief(con, pf, pid) + "\n\n## 输出格式（开头的 --- 块会被程序读取）\n"
                "```\n---\n母题: <字典里的值>\n原型: <字典里的值>\n开头钩: <字典里的值>\n中段留人: <字典里的值>\n结尾钩: <字典里的值>\n时长档: <字典里的值>\n---\n"
                "## 开头怎么抓人\n…\n## 结构怎么推进\n…\n## 结尾留了什么动作\n…\n## 为什么能爆（结合数据）\n…\n## 我们怎么借用（2 个符合账号定位的选题）\n…\n```")
    elif t["kind"] == "hot":
        from . import hot
        text = hot.judge_brief(con, p.get("ids") or [])
    else:
        items = [contents.get(con, int(i)) for i in t["ref"].split(",")]
        brief_items = [{"编号": c["id"], "方向": c["title"], "母题": c["motif"], "原型": c["archetype"], "开头钩": c["hook_open"],
                        "结尾钩": c["hook_end"], "依据": c["basis"]} for c in items]
        text = ("# 任务：把本周的选题方向写成具体可拍的标题\n\n结构已经由数据决定，不许改。每条写一个 30 字以内的选题标题，符合账号定位、兑现开头钩。\n\n"
                + method.profile_text(method.load_profile()) + "\n\n" + json.dumps(brief_items, ensure_ascii=False, indent=1)
                + "\n\n## 输出格式\n每行一条：`编号｜标题`，不要别的内容。")
    if direct:  # 工作台直接调用助手：结果直接输出，由工作台保存
        return text + "\n\n---\n直接输出结果全文（按上面的输出格式），不要解释，不要调用工具，不要写文件。"
    path = task_file(task_id)
    return (f"{text}\n\n---\n写完后把结果存成 UTF-8 文件：{path}\n然后运行：ning agent submit {task_id}\n"
            "（程序会校验格式；报错就按提示改了再交。）")


def get(con, task_id):
    t = rows(con, "SELECT * FROM agent_tasks WHERE id=?", int(task_id))
    if not t:
        raise ValueError(f"没有编号为 {task_id} 的待办")
    return t[0]


def front_matter(text):
    """拆出开头的 --- 块：返回 ({字段: 值}, 正文)。允许外面包一层 ``` 代码块。"""
    text = text.strip()
    text = re.sub(r"^```\w*\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S) or re.search(r"(?m)^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        m2 = re.match(r"^\s*([^:：]+?)\s*[:：]\s*(.*)$", line)
        if m2:
            meta[m2.group(1)] = m2.group(2).strip()
    return meta, m.group(2).strip()


def parse_tags(meta):
    """把中文字段名的标签转成内部字段，并检查是不是字典里的值。"""
    tags, errors = {}, []
    for name, value in meta.items():
        field = TAG_LABELS.get(name)
        if not field or not value:
            continue
        value = value.strip().strip("「」\"'")
        allowed = method.TAG_FIELDS[field][1]
        if value in allowed:
            tags[field] = value
        else:
            errors.append(f"{name}「{value}」不在字典里，只能是：{' / '.join(allowed)}")
    return tags, errors


def submit(con, task_id, text):
    """校验并写回结果；格式不对抛 ValueError（说明哪里不对），待办保持未完成。"""
    t = get(con, task_id)
    if t["status"] != "pending":
        raise ValueError(f"待办 {task_id} 已经是「{t['status']}」状态")
    text = (text or "").strip()
    if len(text) < 6:
        raise ValueError("结果是空的")
    p = json.loads(t["params"] or "{}")
    note = ""
    if t["kind"] == "script":
        c = contents.get(con, int(t["ref"]))
        meta, body = front_matter(text)
        tags, errors = parse_tags(meta)
        if errors:
            raise ValueError("；".join(errors))
        missing = [method.TAG_FIELDS[f][0] for f in ("motif", "archetype", "hook_open", "hook_end") if not (c.get(f) or tags.get(f))]
        if missing:
            raise ValueError(f"开头的 --- 块里缺少：{'、'.join(missing)}")
        if not re.search(r"0[–-]3 ?秒|最后 ?3 ?秒", body):
            raise ValueError("正文要按四段写，至少要有「0–3 秒」和「最后 3 秒」两段")
        titles = [x.strip() for x in meta.get("标题", "").split("|") if x.strip()]
        update = {"id": c["id"], "script": body}
        if meta.get("简介"):
            update["pub_desc"] = meta["简介"][:300]
        if meta.get("话题"):
            # 话题用逗号 / 顿号分开；话题本身不带空格（平台话题里没有空格）
            update["pub_tags"] = ",".join(re.sub(r"\s+", "", t).lstrip("#") for t in re.split(r"[,，、]+", meta["话题"]) if t.strip())[:200]
        update.update({f: v for f, v in tags.items() if not c.get(f)})  # 已定的结构不让改
        if titles:
            update["notes"] = ((c.get("notes") or "") + "\n\n" if c.get("notes") else "") + "备选标题：\n" + "\n".join(f"- {x}" for x in titles)
            if c["title"].startswith("【"):  # 计划给的是方向，AI 定的第一个标题就是正式选题
                update["title"] = titles[0][:60]
                update["notes"] += f"\n原方向：{c['title']}"
        stages = contents.stage_names(c["workflow"])
        target = contents.writing_stage(c["workflow"])
        if stages.index(c["stage"]) <= stages.index(target):  # 稿子写完了，推到下一阶段
            update["stage"] = stages[min(stages.index(target) + 1, len(stages) - 1)]
        contents.save(con, update)
        note = f"稿子已写进「{update.get('title') or c['title']}」" + (f"，进入「{update['stage']}」阶段" if update.get("stage") else "")
    elif t["kind"] == "review":
        from . import review
        from . import playbook
        rid = review.save(con, p.get("days"), "ai", text)
        proposed = playbook.propose_insights(con, text, f"review:{rid}")
        note = f"复盘已保存（编号 {rid}）" + (f"，提了 {proposed} 条洞察等你确认" if proposed else "")
    elif t["kind"] == "bench":
        from . import benchmarks
        meta, body = front_matter(text)
        tags, errors = parse_tags(meta)
        if errors:
            raise ValueError("；".join(errors))
        if not tags.get("archetype"):
            raise ValueError("开头的 --- 块里至少要有「原型」")
        pf, pid = t["ref"].split("|", 1)
        benchmarks.mark(con, pf, pid, tags=tags, analysis=body)
        note = "拆解已保存"
    elif t["kind"] == "hot":
        from . import hot
        note = hot.judge_submit(con, p.get("ids") or [], text)
    else:
        ids = {int(i) for i in t["ref"].split(",")}
        done = 0
        for line in text.splitlines():
            m = re.match(r"^\s*[-*]?\s*(\d+)\s*[｜|:：]\s*(.+?)\s*$", line)
            if m and int(m.group(1)) in ids:
                c = contents.get(con, int(m.group(1)))
                contents.save(con, {"id": c["id"], "title": m.group(2)[:60],
                                    "notes": ((c["notes"] + "\n") if c["notes"] else "") + f"原方向：{c['title']}"})
                done += 1
        if not done:
            raise ValueError("没读到「编号｜标题」格式的行")
        note = f"改了 {done} 条选题标题"
    with con:
        con.execute("UPDATE agent_tasks SET status='done', done_at=?, note=? WHERE id=?", (now_ms(), note, int(task_id)))
    return note


def api_ready():
    s = config.load_settings()
    return bool(s["ai_api_key"] and s["ai_model"] and s["ai_base_url"])


def auto_name():
    """工作台能自己处理待办时，返回由谁处理（「Claude Code」「模型接口」……）；只能等助手来领时返回 None。"""
    from . import runner
    key, name = runner.chosen()
    if key:
        return name
    return "模型接口" if api_ready() else None


def run_auto(con, task_id, log=print, on_proc=None):
    """工作台自己处理一条待办：优先调用本机的 AI 助手，其次用配置的模型接口。格式不对时把报错发回去，让它改一次。"""
    from . import review, runner
    t = get(con, task_id)
    if t["status"] != "pending":
        return f"待办 {task_id} 已经处理过了"
    key, name = runner.chosen()
    if key:
        ask = lambda prompt: runner.run(prompt, key, on_proc=on_proc)
    elif api_ready():
        name = "模型接口"
        ask = lambda prompt: review.chat([{"role": "user", "content": prompt}])
    else:
        raise RuntimeError("没有可用的 AI：本机没找到 Claude Code / Codex / CodeBuddy，也没配模型接口")
    prompt = brief(con, task_id, direct=True)
    log(f"交给 {name} 处理：{label(con, t)}（一般要 1–3 分钟）")
    text = ask(prompt)
    try:
        return submit(con, task_id, text)
    except ValueError as e:
        log(f"格式不对，让它改一次：{e}")
        text = ask(prompt + f"\n\n---\n你上一次的结果没有通过检查：{e}\n请按输出格式重新输出完整结果。上一次的结果是：\n\n{text}")
        return submit(con, task_id, text)
