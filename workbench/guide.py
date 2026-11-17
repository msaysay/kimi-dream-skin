"""「今天」：每次打开工作台，只告诉你下一步做什么。

按闭环的顺序排：准备好（定位、数据、对标、承接）→ 周一复盘出计划 → 出稿 → 制作 → 发布 → 回评论 → 验收写结论 → 拆对标。
每一步都给一个能直接点的按钮；做完了就自动消失。
"""
import datetime
import json
import time

from . import analytics, config, contents, method, planner, playbook
from .db import rows

DAY = 86400 * 1000


def now_ms():
    return int(time.time() * 1000)


def setup_steps(con, has_data):
    profile = method.load_profile()
    gaps = method.profile_gaps(profile)
    accounts = rows(con, "SELECT * FROM accounts WHERE status<>'demo'")
    logged = [a for a in accounts if config.cookie_file(a["platform"], a["account"]).exists()]
    s = config.load_settings()
    benches = rows(con, "SELECT COUNT(*) AS n FROM benchmarks")[0]["n"]
    rules = rows(con, "SELECT COUNT(*) AS n FROM comment_rules WHERE enabled=1")[0]["n"]
    return [
        {"key": "profile", "title": "确认定位与用户卡", "done": not gaps,
         "why": "定位决定「这条该不该拍」。出稿、计划都会按它来过滤选题。" + (f"还有 {len(gaps)} 项待确认。" if gaps else ""),
         "action": {"label": "去确认", "page": "settings"}},
        {"key": "data", "title": "接上数据：导入历史表格，或扫码登录账号", "done": has_data or bool(logged),
         "why": "有数据才能复盘、出计划。已有《内容数据工作台.xlsx》可以先导入。",
         "action": {"label": "去导入 / 登录", "page": "settings" if not accounts else "accounts"}},
        {"key": "login", "title": "扫码登录要发布和采集的账号", "done": bool(logged),
         "why": "登录一次，之后每天自动采集数据、自动发布、抓评论。导入的历史数据也能先用起来。", "action": {"label": "去登录", "page": "accounts"},
         "optional": True},
        {"key": "bench", "title": "添加 3–5 个对标账号", "done": benches >= 3,
         "why": "起步阶段最有效的选题来源：照对标号验证过的爆款结构做。" + ("" if s["tikhub_api_key"] else "需要先在设置里填 TikHub Key。"),
         "action": {"label": "去添加", "page": "bench" if s["tikhub_api_key"] else "settings"}, "optional": True},
        {"key": "rule", "title": "确认评论区领取关键词的自动回复", "done": bool(rules),
         "why": f"结尾说「评论区打 {profile.get('asset_keyword') or 'skill'} 领」，这里自动回复领取方式，第 4 跳才算布上。",
         "action": {"label": "去确认", "page": "comments"}, "optional": True},
    ]


def next_steps(con):
    """按优先级列出现在该做的事（最多 6 件）。"""
    data = analytics.build(con)
    has_data = bool(data["works"])
    week = planner.week_label()
    today = datetime.date.today()
    items = contents.with_acceptance(con, contents.list_all(con))
    steps = []
    add = lambda kind, title, why, action, count=None, urgent=False: steps.append(
        {"kind": kind, "title": title, "why": why, "action": action, "count": count, "urgent": urgent})

    setup = setup_steps(con, has_data)
    first_todo = next((x for x in setup if not x["done"] and not x.get("optional")), None)
    if first_todo:
        add("setup", first_todo["title"], first_todo["why"], first_todo["action"], urgent=True)
        if not has_data:
            return steps, setup, data

    last_collect = max([a["last_collect"] or 0 for a in rows(con, "SELECT last_collect FROM accounts WHERE status<>'demo'")] or [0])
    logged = [a for a in rows(con, "SELECT * FROM accounts WHERE status<>'demo'") if config.cookie_file(a["platform"], a["account"]).exists()]
    if logged and now_ms() - last_collect > 1.5 * DAY:
        add("collect", "采集最新数据", "超过一天没采集了，复盘和验收都要用新数据。", {"label": "立即采集", "act": "collect-all"})

    plan_items = [c for c in items if c["source"] == "plan" and c["week"] == week]
    reviewed = rows(con, "SELECT 1 FROM reviews WHERE created_at>=? LIMIT 1", int(time.mktime((today - datetime.timedelta(days=today.weekday())).timetuple()) * 1000))
    if not plan_items:
        add("plan", "复盘上周，生成本周计划", "看漏斗找本周唯一要改的那一段，把短板翻译成 2–3 条带目标的选题。",
            {"label": "生成本周计划", "act": "plan-and-review"}, urgent=True)

    agent_pending = rows(con, "SELECT COUNT(*) AS n FROM agent_tasks WHERE status='pending'")[0]["n"]
    need_script = [c for c in items if not c["archived"] and not c["script"] and c["source"] != "backfill"
                   and c["stage"] in (contents.stage_names(c["workflow"])[:3])
                   and not rows(con, "SELECT 1 FROM agent_tasks WHERE kind='script' AND ref=? AND status='pending'", str(c["id"]))]
    if need_script:
        add("script", f"给 {len(need_script)} 条选题出稿", "一键出稿：结构、标题、三平台结尾、发布文案一次出齐。",
            {"label": "全部出稿", "act": "script-all", "ids": [c["id"] for c in need_script]}, len(need_script))
    if agent_pending:
        add("agent", f"让 AI 助手处理 {agent_pending} 条 AI 待办", "在 Claude Code / Codex / WorkBuddy 里打开 NingLoop 文件夹，说「处理 NingLoop 的 AI 待办」。",
            {"label": "复制这句话", "act": "copy-ask"}, agent_pending)

    producing = []
    for c in items:
        if c["archived"] or not c["script"]:
            continue
        stages = contents.stage_names(c["workflow"])
        idx = stages.index(c["stage"]) if c["stage"] in stages else 0
        if idx < stages.index("发布") if "发布" in stages else False:
            producing.append(c)
    if producing:
        overdue = [c for c in producing if c["schedule_date"] and c["schedule_date"] <= str(today)]
        first = sorted(producing, key=lambda c: (c["schedule_date"] or "9999", c["priority"] or "P9"))[0]
        add("produce", f"制作：{first['title'][:24]}" + (f" 等 {len(producing)} 条" if len(producing) > 1 else ""),
            f"稿子已经好了，现在在「{first['stage']}」。" + (f"有 {len(overdue)} 条已经到排期日。" if overdue else "按阶段检查项一步步做。"),
            {"label": "打开这条", "content": first["id"]}, len(producing), urgent=bool(overdue))

    to_publish = [c for c in items if not c["archived"] and c["stage"] == "发布" and not c["posts"]]
    if to_publish:
        add("publish", f"发布 {len(to_publish)} 条", "发布时关联这条选题，采集到数据后会自动验收。",
            {"label": "去发布", "content": to_publish[0]["id"], "publish": True}, len(to_publish))

    pending_comments = rows(con, "SELECT COUNT(*) AS n FROM comments WHERE state IN ('draft','queued')")[0]["n"]
    if pending_comments:
        add("comments", f"确认 {pending_comments} 条评论回复", "领取关键词的回复要及时，第 4 跳靠它。", {"label": "去回复", "page": "comments"}, pending_comments)

    to_conclude = [c for c in items if c["acceptance_result"]["overall"] in ("达成", "部分达成", "未达成") and not c.get("conclusion")]
    if to_conclude:
        c = to_conclude[0]
        add("conclude", f"验收结论：{c['title'][:24]}" + (f" 等 {len(to_conclude)} 条" if len(to_conclude) > 1 else ""),
            f"数据到了：{c['acceptance_result']['overall']}。写一句结论，打法库会记下这次改动有没有用。",
            {"label": "看结果", "content": c["id"]}, len(to_conclude))

    new_hits = rows(con, """SELECT COUNT(*) AS n FROM bench_posts WHERE status='new' AND first_seen>=?""", now_ms() - 7 * DAY)[0]["n"]
    if new_hits:
        add("bench", "看看对标号这周的新作品", "爆款拆成结构，拆过的会进下周计划。", {"label": "去看对标", "page": "bench"})
    # 准备工作最先，其次是到期 / 标了优先的，其余保持闭环顺序
    steps.sort(key=lambda x: (x["kind"] != "setup", not x["urgent"]))
    return steps[:6], setup, data


def calendar(con, days=7):
    """本周（从今天起 7 天）的排期。"""
    today = datetime.date.today()
    span = [today + datetime.timedelta(days=i) for i in range(days)]
    items = [c for c in contents.list_all(con) if c["schedule_date"]]
    return [{"date": str(d), "weekday": "一二三四五六日"[d.weekday()],
             "items": [{"id": c["id"], "title": c["title"], "stage": c["stage"], "priority": c["priority"], "has_script": bool(c["script"])}
                       for c in items if c["schedule_date"] == str(d)]} for d in span]


def today_view(con):
    steps, setup, data = next_steps(con)
    lessons = playbook.lessons(con, data)
    return {"steps": steps, "setup": setup, "setup_done": all(x["done"] or x.get("optional") for x in setup),
            "calendar": calendar(con), "trend": playbook.weekly_trend(data),
            "lessons": [L for L in lessons if L["status"] in ("有效", "无效")][:5], "lesson_count": len(lessons),
            "platforms": {k: v["name"] for k, v in config.PLATFORMS.items()}, "week": planner.week_label()}
