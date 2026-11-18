"""本地网页服务：只监听 127.0.0.1，给浏览器里的工作台界面提供数据和操作接口。"""
import json
import mimetypes
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import agent, analytics, runner, cal, hot, precheck, benchmarks, comments, config, covers, contents, db, demo, guide, importer, jobs, method, planner, playbook, tikhub

ACCOUNT_RE = re.compile(r"^[\w一-鿿-]{1,30}$")

PICK_SCRIPT = """
import json, sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
kinds = {'video': [('视频', '*.mp4 *.mov *.mkv *.avi *.flv *.webm'), ('所有文件', '*.*')],
         'image': [('图片', '*.jpg *.jpeg *.png *.webp'), ('所有文件', '*.*')],
         'xlsx': [('Excel 表格', '*.xlsx'), ('所有文件', '*.*')]}[sys.argv[1]]
if sys.argv[2] == 'many':
    files = list(filedialog.askopenfilenames(filetypes=kinds))
else:
    one = filedialog.askopenfilename(filetypes=kinds)
    files = [one] if one else []
print(json.dumps(files))
"""


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def state(con, worker):
    accounts = db.rows(con, "SELECT * FROM accounts ORDER BY platform, account")
    for a in accounts:
        a["has_login"] = a["status"] == "demo" or config.cookie_file(a["platform"], a["account"]).exists()
        a["posts"] = con.execute("SELECT COUNT(*) FROM posts WHERE platform=? AND account=?", (a["platform"], a["account"])).fetchone()[0]
    job_rows = db.rows(con, "SELECT id, type, params, status, created_at, started_at, finished_at, substr(log, -600) AS tail FROM jobs ORDER BY id DESC LIMIT 40")
    checks = {c["job_id"]: c for c in db.rows(con, "SELECT job_id, status, note FROM publish_checks")}
    for j in job_rows:
        j["params"] = json.loads(j["params"])
        if j["type"] == "publish" and j["id"] in checks:  # 发布后回读的结果
            j["check"] = {"status": checks[j["id"]]["status"], "note": checks[j["id"]]["note"]}
    running = auto_running(con)
    return {"accounts": accounts, "jobs": job_rows, "settings": config.public_settings(), "sau_installed": config.sau_installed(),
            "platforms": config.PLATFORMS, "demo_account": config.DEMO_ACCOUNT, "busy": worker.current is not None,
            "has_data": con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] > 0,
            "agent_pending": [{"id": t["id"], "kind": t["kind"], "ref": t["ref"], "running": t["id"] in running}
                              for t in db.rows(con, "SELECT id, kind, ref FROM agent_tasks WHERE status='pending'")],
            "agent_api": bool(agent.auto_name()), "agent_auto": agent.auto_name(),
            "hot_fit": con.execute("SELECT COUNT(*) FROM hot_topics WHERE fit='高' AND state='new' AND content_id IS NULL AND fetched_at>=?",
                                   (jobs.now_ms() - 2 * 86400000,)).fetchone()[0]}


def request_ai(con, kind, ref, params=None):
    """建一条 AI 待办；本机有 AI 助手（或配了模型接口）就顺手排一个任务让工作台自己做，否则等 AI 助手来领。"""
    task_id = agent.create(con, kind, ref, params)
    if agent.auto_name():
        queue_auto(con, [task_id])
    return task_id


def auto_running(con):
    """正在排队或正在由工作台处理的待办编号。"""
    busy = set()
    for j in db.rows(con, "SELECT params FROM jobs WHERE type='agent_auto' AND status IN ('pending','running')"):
        busy.update(json.loads(j["params"]).get("tasks", []))
    return busy


def queue_auto(con, task_ids):
    """排一个 AI 处理任务；已经在排队或正在处理的待办不重复排。"""
    busy = auto_running(con)
    todo = [t for t in task_ids if t not in busy]
    return jobs.enqueue(con, "agent_auto", {"tasks": todo}) if todo else None


def require_account(con, platform, account):
    if platform not in config.PLATFORMS:
        raise ApiError("不认识的平台")
    if not con.execute("SELECT 1 FROM accounts WHERE platform=? AND account=?", (platform, account)).fetchone():
        raise ApiError("账号不存在")
    if account == config.DEMO_ACCOUNT:
        raise ApiError("示例账号只用来预览界面，不能登录、发布或采集")


def api_post(con, worker, path, body):
    if path == "/api/accounts/add":
        platform, account = body.get("platform"), (body.get("account") or "").strip()
        if platform not in config.PLATFORMS:
            raise ApiError("请选择平台")
        if not ACCOUNT_RE.match(account) or account == config.DEMO_ACCOUNT:
            raise ApiError("账号备注名只能用中文、字母、数字、下划线和短横线，最多 30 个字")
        with con:
            con.execute("INSERT OR IGNORE INTO accounts (platform, account) VALUES (?,?)", (platform, account))
        return {"ok": True}
    if path == "/api/accounts/delete":
        require_account(con, body.get("platform"), body.get("account"))
        with con:
            db.delete_account_data(con, body["platform"], body["account"])
        config.cookie_file(body["platform"], body["account"]).unlink(missing_ok=True)
        return {"ok": True}
    if path in ("/api/accounts/login", "/api/accounts/check"):
        require_account(con, body.get("platform"), body.get("account"))
        return {"job": jobs.enqueue(con, path.rsplit("/", 1)[1], {"platform": body["platform"], "account": body["account"]})}
    if path == "/api/collect":
        targets = db.rows(con, "SELECT platform, account FROM accounts WHERE account<>?", config.DEMO_ACCOUNT)
        if body.get("platform"):
            targets = [t for t in targets if t["platform"] == body["platform"] and t["account"] == body.get("account")]
        targets = [t for t in targets if config.cookie_file(t["platform"], t["account"]).exists()]
        if not targets:
            raise ApiError("没有已登录的账号：请先在「账号」页添加账号并扫码登录")
        return {"jobs": [jobs.enqueue(con, "collect", t) for t in targets]}
    if path == "/api/publish":
        targets = body.get("targets") or []
        if not targets:
            raise ApiError("请至少勾选一个要发布的账号")
        files = [f.strip().strip('"') for f in body.get("files") or [] if f and f.strip()]
        cover = (body.get("cover") or "").strip().strip('"')
        for f in files + ([cover] if cover else []):
            if not Path(f).is_file():
                raise ApiError(f"找不到文件：{f}")
        blocks = [i for i in precheck.check(con, body) if i["level"] == "block"]
        if blocks:
            raise ApiError("发布前检查没通过：" + "；".join((config.PLATFORMS[i["platform"]]["name"] + "：" if i["platform"] else "") + i["msg"] for i in blocks[:3]))
        made = None
        if (body.get("kind") or "video") == "video" and (cover or body.get("cover_sec") not in (None, "")):
            try:  # 一张原图按每个平台的规格各生成一份
                made = covers.build(cover or None, files[0] if files else None, body.get("cover_sec") if not cover else None,
                                    body.get("cover_focus") or (0.5, 0.4), body.get("cover_mode") or "auto",
                                    sorted({t.get("platform") for t in targets if t.get("platform") in config.PLATFORMS}),
                                    (body.get("cover_wide") or "").strip().strip('"') or None, body.get("cover_focus2") or (0.5, 0.5))
            except (covers.CoverError, ValueError, TypeError) as e:
                raise ApiError(f"封面：{e}")
        queued = []
        for t in targets:
            require_account(con, t.get("platform"), t.get("account"))
            p = {"platform": t["platform"], "account": t["account"], "kind": body.get("kind") or "video", "files": files,
                 "covers": covers.for_platform(made, t["platform"]) if made else {},
                 "title": (t.get("title") or body.get("title") or "").strip(), "desc": body.get("desc") or "", "tags": body.get("tags") or "",
                 "schedule": body.get("schedule") or "", "draft": bool(t.get("draft")), "headless": bool(body.get("headless")),
                 "content_id": int(body["content_id"]) if body.get("content_id") else None}
            try:
                jobs.publish_args(p)
            except ValueError as e:
                raise ApiError(f"{config.PLATFORMS[t['platform']]['name']}：{e}")
            queued.append(p)
        if body.get("content_id"):
            with con:
                con.execute("UPDATE contents SET stage='发布', updated_at=? WHERE id=?", (jobs.now_ms(), int(body["content_id"])))
        return {"jobs": [jobs.enqueue(con, "publish", p) for p in queued]}
    if path == "/api/publish/check":
        return {"issues": precheck.check(con, body)}
    # ---- 热点雷达 ----
    if path == "/api/hot/refresh":
        return {"job": jobs.enqueue(con, "hot", {"judge": bool(body.get("judge"))})}
    if path == "/api/hot/judge":
        ids = [int(i) for i in body.get("ids") or []] or [r["id"] for r in hot.unjudged(con)]
        if not ids:
            raise ApiError("没有还没判断过的热点，先刷新一下热榜")
        return {"task": request_ai(con, "hot", f"hot-{jobs.now_ms()}", {"ids": ids})}
    if path == "/api/hot/to_content":
        try:
            return {"id": hot.to_content(con, int(body["id"]), body.get("title"))}
        except ValueError as e:
            raise ApiError(str(e))
    if path == "/api/hot/ignore":
        with con:
            con.execute("UPDATE hot_topics SET state='ignored' WHERE id=?", (int(body["id"]),))
        return {"ok": True}
    if path == "/api/hot/boards":
        on = [k for k in body.get("boards") or [] if k in hot.BUILTIN]
        config.save_settings({"hot_boards": ",".join(on)})
        return {"ok": True}
    if path == "/api/hot/rss/add":
        try:
            return {"id": hot.add_rss(con, body.get("name"), body.get("url"))}
        except Exception as e:
            raise ApiError(f"读不了这个订阅地址：{e}")
    if path == "/api/hot/rss/delete":
        with con:
            con.execute("DELETE FROM hot_topics WHERE source=? AND content_id IS NULL", (f"rss{int(body['id'])}",))
            con.execute("DELETE FROM hot_sources WHERE id=?", (int(body["id"]),))
        return {"ok": True}
    # ---- 日历 ----
    if path == "/api/calendar/schedule":
        date = (body.get("date") or "").strip()
        if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise ApiError("日期格式不对")
        contents.save(con, {"id": int(body["id"]), "schedule_date": date or None})
        return {"ok": True}
    if path == "/api/calendar/event":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", body.get("date") or "") or not (body.get("title") or "").strip():
            raise ApiError("要填日期和名字")
        with con:
            con.execute("INSERT INTO cal_events (date, title, note) VALUES (?,?,?)", (body["date"], body["title"].strip()[:30], (body.get("note") or "")[:200]))
        return {"ok": True}
    if path == "/api/calendar/event/delete":
        with con:
            con.execute("DELETE FROM cal_events WHERE id=?", (int(body["id"]),))
        return {"ok": True}
    if path == "/api/review":
        days = int(body.get("days") or 0)
        if body.get("ai"):
            return {"task": request_ai(con, "review", f"review-{days}-{jobs.now_ms() // 3600000}", {"days": days})}
        return {"job": jobs.enqueue(con, "review", {"days": days})}
    if path == "/api/reviews/delete":
        with con:
            con.execute("DELETE FROM insights WHERE source=? AND status='proposed'", (f"review:{body.get('id')}",))
            con.execute("DELETE FROM reviews WHERE id=?", (body.get("id"),))
        return {"ok": True}
    if path == "/api/settings":
        for key in ("ai_api_key", "tikhub_api_key"):
            if body.get(key) == "":
                body.pop(key)  # 留空表示不改
        config.save_settings(body)
        return {"ok": True}
    if path == "/api/demo":
        demo.load_demo(con) if body.get("action") == "load" else demo.clear_demo(con)
        return {"ok": True}
    if path == "/api/cover/preview":
        image = (body.get("cover") or "").strip().strip('"')
        video = ((body.get("files") or [""])[0] or "").strip().strip('"')
        try:
            r = covers.build(image or None, video or None, body.get("cover_sec") if not image else None,
                             body.get("cover_focus") or (0.5, 0.4), body.get("cover_mode") or "auto", body.get("platforms") or None,
                             (body.get("cover_wide") or "").strip().strip('"') or None, body.get("cover_focus2") or (0.5, 0.5))
        except (covers.CoverError, ValueError, TypeError) as e:
            raise ApiError(str(e))
        for it in r["items"]:
            it["url"] = f"/api/cover/file?d={r['dir']}&f={Path(it['path']).name}"
            it["name"] = config.PLATFORMS[it["platform"]]["name"]
        r["source"]["url"] = f"/api/cover/file?d={r['dir']}&f=source.jpg"
        if r["source2"]:
            r["source2"]["url"] = f"/api/cover/file?d={r['dir']}&f=source2.jpg"
        return r
    if path == "/api/pick":
        kind = body.get("kind") if body.get("kind") in ("image", "xlsx") else "video"
        try:
            out = subprocess.run([sys.executable, "-c", PICK_SCRIPT, kind, "many" if body.get("many") else "one"],
                                 capture_output=True, text=True, encoding="utf-8", timeout=600)
            return {"files": json.loads(out.stdout.strip().splitlines()[-1])}
        except Exception:
            raise ApiError("打不开文件选择窗口，请直接把文件的完整路径粘贴到输入框里")
    # ---- 定位卡、复盘参数、工作流 ----
    if path == "/api/profile":
        return method.save_profile(body)
    if path == "/api/params":
        return method.save_params(body)
    if path == "/api/workflows":
        contents.save_workflows_text(body)
        return {"ok": True}
    if path == "/api/import":
        try:
            return importer.import_workbook(con, (body.get("path") or "").strip().strip('"'), body.get("account") or "")
        except (ValueError, FileNotFoundError) as e:
            raise ApiError(str(e))
    if path == "/api/posts/flag":
        with con:
            if body.get("flag"):
                con.execute("INSERT OR REPLACE INTO post_flags VALUES (?,?,?,?)", (body["platform"], body["post_id"], body["flag"], body.get("note") or ""))
            else:
                con.execute("DELETE FROM post_flags WHERE platform=? AND post_id=?", (body["platform"], body["post_id"]))
        return {"ok": True}
    if path == "/api/posts/tags":
        try:
            return {"id": contents.backfill(con, body["platform"], body["post_id"], body.get("tags") or {})}
        except ValueError as e:
            raise ApiError(str(e))
    # ---- 计划与内容 ----
    if path == "/api/plan":
        return {"job": jobs.enqueue(con, "plan", {"days": int(body.get("days") or 14)})}
    if path == "/api/plan/refine":
        ids = [int(i) for i in body.get("ids") or []]
        if not ids:
            raise ApiError("没有要细化的选题")
        task_id = planner.refine_titles(con, ids)
        if agent.auto_name():
            queue_auto(con, [task_id])
        return {"task": task_id}
    if path == "/api/contents/save":
        try:
            return {"id": contents.save(con, body)}
        except ValueError as e:
            raise ApiError(str(e))
    if path == "/api/contents/link":
        contents.link(con, int(body["content_id"]), body["platform"], body["post_id"])
        return {"ok": True}
    if path == "/api/contents/unlink":
        contents.unlink(con, body["platform"], body["post_id"])
        return {"ok": True}
    if path == "/api/contents/archive":
        with con:
            con.execute("UPDATE contents SET archived=? WHERE id=?", (int(bool(body.get("archived", True))), int(body["id"])))
        return {"ok": True}
    if path == "/api/contents/scaffold":
        c = contents.get(con, int(body["id"]))
        text = contents.scaffold(c, method.load_profile())
        contents.save(con, {"id": c["id"], "script": text if not c["script"] else c["script"] + "\n\n---\n" + text})
        return {"ok": True}
    if path == "/api/contents/ai":  # 一键出稿
        ids = [int(i) for i in (body.get("ids") or [body.get("id")]) if i]
        if not ids:
            raise ApiError("没有要出稿的选题")
        return {"tasks": [request_ai(con, "script", i) for i in ids]}
    # ---- 打法库与验收结论 ----
    if path == "/api/playbook/note":
        try:
            playbook.set_note(con, body["key"], body.get("verdict"), body.get("note"))
        except ValueError as e:
            raise ApiError(str(e))
        return {"ok": True}
    if path == "/api/insights/decide":
        try:
            playbook.decide_insight(con, body["id"], body.get("status"))
        except ValueError as e:
            raise ApiError(str(e))
        return {"ok": True}
    if path == "/api/insights/add":
        try:
            return {"id": playbook.add_insight(con, body.get("text"), body.get("basis"))}
        except ValueError as e:
            raise ApiError(str(e))
    if path == "/api/insights/delete":
        with con:
            con.execute("DELETE FROM insights WHERE id=?", (int(body["id"]),))
        return {"ok": True}
    if path == "/api/bench/paste":
        try:
            pf, pid = benchmarks.paste(con, body.get("platform"), body.get("text"), body.get("url") or "", body.get("author") or "", body.get("stats"))
        except ValueError as e:
            raise ApiError(str(e))
        return {"platform": pf, "post_id": pid, "task": request_ai(con, "bench", f"{pf}|{pid}")}
    if path == "/api/playbook/calibrate":
        try:
            playbook.apply_calibration(con, body.get("platform"))
        except ValueError as e:
            raise ApiError(str(e))
        return {"ok": True}
    if path == "/api/contents/conclude":
        contents.save(con, {"id": int(body["id"]), "conclusion": (body.get("conclusion") or "").strip()[:500] or "已看过"})
        return {"ok": True}
    # ---- 对标账号 ----
    if path == "/api/bench/resolve":
        if body.get("platform") not in config.PLATFORMS:
            raise ApiError("请选择平台")
        try:
            return {"candidates": tikhub.resolve(body["platform"], body.get("text") or "")}
        except tikhub.TikHubError as e:
            raise ApiError(str(e))
    if path == "/api/bench/add":
        c = body.get("candidate") or {}
        if not c.get("uid"):
            raise ApiError("缺少账号")
        bid = benchmarks.add(con, body["platform"], c["uid"], c.get("name"), c.get("followers"), c.get("avatar"), c.get("note"), body.get("grp"))
        return {"id": bid, "job": jobs.enqueue(con, "bench", {"bench_id": bid})}
    if path == "/api/bench/refresh":
        if not config.load_settings()["tikhub_api_key"]:
            raise ApiError("还没有配置 TikHub：请在「设置 → 对标账号」里填 API Key")
        return {"job": jobs.enqueue(con, "bench", {"bench_id": body.get("bench_id")})}
    if path == "/api/bench/update":
        with con:
            con.execute("UPDATE benchmarks SET enabled=COALESCE(?, enabled), grp=COALESCE(?, grp) WHERE id=?",
                        (None if body.get("enabled") is None else int(bool(body["enabled"])), body.get("grp"), int(body["id"])))
        return {"ok": True}
    if path == "/api/bench/delete":
        benchmarks.remove(con, int(body["id"]))
        return {"ok": True}
    if path == "/api/bench/mark":
        try:
            benchmarks.mark(con, body["platform"], body["post_id"], body.get("status"), body.get("tags"), body.get("analysis"))
        except ValueError as e:
            raise ApiError(str(e))
        return {"ok": True}
    if path == "/api/bench/analyze":
        posts = [f'{x["platform"]}|{x["post_id"]}' for x in body.get("posts") or []]
        if not posts:
            raise ApiError("没有要拆解的作品")
        return {"tasks": [request_ai(con, "bench", ref) for ref in posts]}
    if path == "/api/agent/cancel":
        with con:
            con.execute("UPDATE agent_tasks SET status='cancelled', done_at=? WHERE id=? AND status='pending'", (jobs.now_ms(), int(body["id"])))
        return {"ok": True}
    if path == "/api/agent/run":  # 让工作台现在就处理这些待办（之前没处理成的也可以再试）
        if not agent.auto_name():
            raise ApiError("本机没找到 Claude Code / Codex / CodeBuddy，也没配模型接口。可以到「设置 → AI」看看")
        ids = [int(i) for i in body.get("ids") or []] or [t["id"] for t in agent.pending(con)]
        return {"job": queue_auto(con, ids)}
    if path == "/api/ai/test":
        key = body.get("runner") or None
        try:
            return {"reply": runner.test(None if key in (None, "auto") else key), "name": agent.auto_name()}
        except RuntimeError as e:
            raise ApiError(str(e))
    if path == "/api/agent/submit":  # 也可以把 AI 写好的结果粘贴进来
        try:
            return {"note": agent.submit(con, int(body["id"]), body.get("text") or "")}
        except ValueError as e:
            raise ApiError(str(e))
    if path == "/api/bench/to_content":
        try:
            return {"id": benchmarks.to_content(con, body["platform"], body["post_id"])}
        except ValueError as e:
            raise ApiError(str(e))
    # ---- 评论 ----
    if path == "/api/comments/fetch":
        require_account(con, body.get("platform"), body.get("account"))
        if not config.cookie_file(body["platform"], body["account"]).exists():
            raise ApiError("这个账号还没登录")
        return {"job": jobs.enqueue(con, "comments", {"platform": body["platform"], "account": body["account"]})}
    if path == "/api/comments/import":
        if body.get("platform") not in config.PLATFORMS:
            raise ApiError("请选择平台")
        comments.ensure_default_rule(con)
        post = db.rows(con, "SELECT account FROM posts WHERE platform=? AND post_id=?", body["platform"], body.get("post_id") or "")
        new = comments.import_text(con, body["platform"], post[0]["account"] if post else "", body.get("post_id") or "", body.get("text") or "")
        drafted, _ = comments.process(con)
        return {"new": new, "drafted": drafted}
    if path == "/api/comments/process":
        comments.ensure_default_rule(con)
        with con:
            con.execute("UPDATE comments SET state='new', reply=NULL, rule_id=NULL WHERE state='draft'")
        drafted, queued = comments.process(con)
        return {"drafted": drafted, "queued": queued}
    if path == "/api/comments/update":
        state = body.get("state")
        if state not in ("draft", "queued", "replied", "ignored", "new"):
            raise ApiError("状态不对")
        if state == "queued" and body["platform"] not in comments.AUTO_SEND_PLATFORMS:
            raise ApiError("小红书不自动发送，请复制回复后手动发")
        with con:
            con.execute("UPDATE comments SET state=?, reply=COALESCE(?, reply), replied_at=CASE WHEN ?='replied' THEN ? ELSE replied_at END "
                        "WHERE platform=? AND comment_id=?", (state, body.get("reply"), state, jobs.now_ms(), body["platform"], body["comment_id"]))
        return {"ok": True}
    if path == "/api/comments/send":
        targets = db.rows(con, "SELECT DISTINCT platform, account FROM comments WHERE state='queued' AND account<>''")
        if not targets:
            raise ApiError("没有排队待发送的回复")
        return {"jobs": [jobs.enqueue(con, "comment_send", t) for t in targets]}
    if path == "/api/rules/save":
        try:
            return {"id": comments.save_rule(con, body)}
        except ValueError as e:
            raise ApiError(str(e))
    if path == "/api/rules/delete":
        with con:
            con.execute("DELETE FROM comment_rules WHERE id=?", (int(body["id"]),))
        return {"ok": True}
    m = re.fullmatch(r"/api/jobs/(\d+)/cancel", path)
    if m:
        job_id = int(m.group(1))
        worker.cancel(job_id)
        with con:
            con.execute("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=? AND status='pending'", (jobs.now_ms(), job_id))
        return {"ok": True}
    raise ApiError("没有这个接口", 404)


def api_get(con, worker, path, query):
    if path == "/api/state":
        return state(con, worker)
    if path == "/api/data":
        days = int((query.get("days") or ["0"])[0])
        return analytics.build(con, days or None)
    if path == "/api/method":
        return {"tags": {k: {"name": v[0], "values": v[1]} for k, v in method.TAG_FIELDS.items()},
                "motifs": method.MOTIFS, "archetypes": method.ARCHETYPES, "hooks_open": method.HOOKS_OPEN, "hooks_mid": method.HOOKS_MID,
                "hooks_end": method.HOOKS_END, "combos": method.COMBOS, "skeletons": method.SKELETONS, "segments": method.SEGMENTS,
                "verdicts": method.VERDICTS, "jumps": method.JUMPS, "roles": method.PLATFORM_ROLES, "metric_names": method.METRIC_NAMES,
                "profile_fields": method.PROFILE_FIELDS, "profile": method.load_profile(), "params": method.load_params(),
                "workflows": contents.load_workflows(), "workflows_text": contents.workflows_text(), "week": planner.week_label()}
    if path == "/api/bench":
        days = int((query.get("days") or ["30"])[0])
        d = benchmarks.load(con, days)
        d.pop("posts_by_key")
        all_posts = db.rows(con, "SELECT COUNT(*) AS n FROM bench_posts")[0]["n"]
        return {**d, "usage": benchmarks.usage(con), "daily_cost": benchmarks.daily_cost(con), "post_total": all_posts,
                "prices": tikhub.PRICES, "status_names": benchmarks.STATUS}
    if path == "/api/today":
        return guide.today_view(con)
    if path == "/api/playbook":
        return {"lessons": playbook.lessons(con), "calibration": playbook.calibration(con), "field_names": playbook.FIELD_NAMES,
                "insights": playbook.insights(con)}
    if path == "/api/agent":
        tasks = agent.pending(con)
        done = db.rows(con, "SELECT id, kind, note, done_at FROM agent_tasks WHERE status='done' ORDER BY done_at DESC LIMIT 5")
        running = auto_running(con)
        for t in tasks:
            t["running"] = t["id"] in running
        return {"pending": tasks, "recent": done, "auto": agent.auto_name(), "kinds": agent.KINDS}
    if path == "/api/hot":
        return hot.view(con)
    if path == "/api/calendar":
        return cal.month(con, (query.get("month") or [""])[0])
    if path == "/api/publish/checks":
        return {"checks": db.rows(con, "SELECT * FROM publish_checks ORDER BY checked_at DESC LIMIT 30")}
    if path == "/api/ai/runners":
        return {"found": runner.detect(), "chosen": runner.chosen()[1], "all": [{"key": k, "name": v["name"]} for k, v in runner.RUNNERS.items()]}
    m = re.fullmatch(r"/api/agent/(\d+)/brief", path)
    if m:
        try:
            return {"brief": agent.brief(con, int(m.group(1)))}
        except ValueError as e:
            raise ApiError(str(e), 404)
    if path == "/api/contents":
        return {"contents": contents.with_acceptance(con, contents.list_all(con, bool(query.get("all"))))}
    if path == "/api/comments":
        comments.ensure_default_rule(con)
        out = db.rows(con, "SELECT * FROM comments ORDER BY COALESCE(ctime, fetched_at) DESC LIMIT 500")
        return {"comments": out, "rules": comments.list_rules(con), "questions": planner.top_questions(con, 30)[:10],
                "auto_send_platforms": comments.AUTO_SEND_PLATFORMS}
    if path == "/api/reviews":
        return {"reviews": db.rows(con, "SELECT * FROM reviews ORDER BY id DESC LIMIT 30"), "insights": playbook.insights(con)}
    m = re.fullmatch(r"/api/jobs/(\d+)", path)
    if m:
        found = db.rows(con, "SELECT * FROM jobs WHERE id=?", int(m.group(1)))
        if not found:
            raise ApiError("没有这个任务", 404)
        found[0]["params"] = json.loads(found[0]["params"])
        return found[0]
    raise ApiError("没有这个接口", 404)


def make_handler(worker, port):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, obj, status=200):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def guard(self):
            """只接受本机页面发来的请求，防止别的网站借浏览器操作工作台。"""
            origin = self.headers.get("Origin")
            if self.headers.get("Host") not in allowed_hosts or (origin and urlparse(origin).netloc not in allowed_hosts):
                self.send_json({"error": "拒绝访问"}, 403)
                return False
            return True

        def handle_api(self, fn, *args):
            con = db.connect()
            try:
                self.send_json(fn(con, worker, *args))
            except ApiError as e:
                self.send_json({"error": str(e)}, e.status)
            except Exception as e:
                self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)
            finally:
                con.close()

        def do_GET(self):
            if not self.guard():
                return
            url = urlparse(self.path)
            if url.path == "/api/cover/file":  # 生成的封面预览图
                q = parse_qs(url.query)
                d, name = (q.get("d") or [""])[0], (q.get("f") or [""])[0]
                f = config.DATA / "covers" / d / name
                if not re.fullmatch(r"[0-9a-f]{12}", d) or not re.fullmatch(r"[\w.]+\.jpg", name) or not f.is_file():
                    return self.send_json({"error": "没有这张图"}, 404)
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            if url.path.startswith("/api/"):
                return self.handle_api(api_get, url.path, parse_qs(url.query))
            rel = "index.html" if url.path == "/" else url.path.lstrip("/")
            f = (config.WEB / rel).resolve()
            if config.WEB.resolve() not in f.parents or not f.is_file():
                return self.send_json({"error": "没有这个页面"}, 404)
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", (mimetypes.guess_type(f.name)[0] or "application/octet-stream") + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if not self.guard():
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self.send_json({"error": "请求格式不对"}, 400)
            self.handle_api(api_post, urlparse(self.path).path, body)

    return Handler


def serve(port=None, open_browser=True):
    config.ensure_dirs()
    port = port or int(config.load_settings()["port"])
    worker = jobs.Workers()
    worker.start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(worker, port))
    url = f"http://127.0.0.1:{port}"
    print(f"NingLoop（宁环）已启动：{url}\n关闭这个窗口就会停止工作台。")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
