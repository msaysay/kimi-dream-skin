"""命令行入口：python -m workbench [serve|collect|demo|review]"""
import argparse
import sys
import time

from . import collect, comments, config, contents, db, demo, ingest, review, server


def collect_one(con, platform, account):
    name = f"{config.PLATFORMS[platform]['name']}「{account}」"
    try:
        count = collect.run(platform, account)
        saved = ingest.ingest(con, platform, account)
        with con:
            con.execute("UPDATE accounts SET status='ok', last_collect=?, last_error=NULL WHERE platform=? AND account=?",
                        (int(time.time() * 1000), platform, account))
        linked = contents.auto_link(con)
        print(f"{name} 采集完成：{count} 条作品，入库 {saved} 条" + (f"，关联回 {linked} 条计划内容" if linked else ""))
        return True
    except collect.NotLoggedIn as e:
        with con:
            con.execute("UPDATE accounts SET status='expired', last_error=? WHERE platform=? AND account=?", (str(e), platform, account))
        print(f"{name} 采集失败：{e}")
    except Exception as e:
        with con:
            con.execute("UPDATE accounts SET last_error=? WHERE platform=? AND account=?", (f"{type(e).__name__}: {e}"[:500], platform, account))
        print(f"{name} 采集失败：{type(e).__name__}: {e}")
    return False


def agent_cli(con, args):
    from . import agent
    try:
        if args.action == "todo":
            tasks = agent.pending(con)
            if not tasks:
                print("没有 AI 待办。")
            for t in tasks:
                print(f"[{t['id']}] {t['label']}")
            if tasks:
                print(f"\n逐条处理：ning agent brief <编号> → 按说明写结果 → ning agent submit <编号>")
            return 0
        if args.action in ("new", "script"):
            if not args.arg:
                print("请给出选题" if args.action == "new" else "请给出内容编号")
                return 1
            if args.action == "new":
                wf = "talk" if args.talk else "topic"
                cid = contents.save(con, {"title": args.arg.strip()[:80], "workflow": wf, "source": "manual"})
            else:
                cid = contents.get(con, int(args.arg))["id"]
            task_id = agent.create(con, "script", cid)
            print(agent.brief(con, task_id))
            return 0
        if not args.arg:
            print("请给出待办编号")
            return 1
        if args.action == "brief":
            print(agent.brief(con, int(args.arg)))
            return 0
        if args.action == "cancel":
            with con:
                con.execute("UPDATE agent_tasks SET status='cancelled' WHERE id=? AND status='pending'", (int(args.arg),))
            print("已取消")
            return 0
        path = args.file or str(agent.task_file(int(args.arg)))
        try:
            text = open(path, encoding="utf-8-sig").read()
        except OSError:
            print(f"读不到结果文件：{path}")
            return 1
        print(agent.submit(con, int(args.arg), text))
        return 0
    except ValueError as e:
        print(f"没通过：{e}")
        return 1


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # Windows 终端默认不是 UTF-8
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="workbench", description="NingLoop（宁环）创作闭环工作台")
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("serve", help="启动工作台（默认）")
    p.add_argument("--port", type=int)
    p.add_argument("--no-browser", action="store_true")
    p = sub.add_parser("collect", help="采集数据；不指定账号就采集所有已登录的账号")
    p.add_argument("--platform", choices=list(config.PLATFORMS))
    p.add_argument("--account")
    sub.add_parser("demo", help="载入示例数据")
    for name, text in (("comments", "抓评论并按规则生成回复"), ("send-comments", "发送排队中的评论回复（实验性）")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--platform", choices=list(config.PLATFORMS), required=True)
        p.add_argument("--account", required=True)
    p = sub.add_parser("agent", help="给 AI 助手用：领 AI 待办、拿任务说明、交结果")
    p.add_argument("action", choices=["todo", "brief", "submit", "new", "script", "cancel"])
    p.add_argument("arg", nargs="?", help="待办编号；new 时是选题；script 时是内容编号")
    p.add_argument("--file", help="submit 时读取的结果文件（默认 data/agent/task-<编号>.md）")
    p.add_argument("--talk", action="store_true", help="new 时走口播剪辑工作流")
    p = sub.add_parser("review", help="生成复盘并打印")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--ai", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd in (None, "serve"):
        server.serve(getattr(args, "port", None), not getattr(args, "no_browser", False))
        return 0
    con = db.connect()
    if args.cmd == "collect":
        if args.platform and args.account:
            with con:
                con.execute("INSERT OR IGNORE INTO accounts (platform, account) VALUES (?,?)", (args.platform, args.account))
            targets = [(args.platform, args.account)]
        else:
            targets = [(a["platform"], a["account"]) for a in db.rows(con, "SELECT platform, account FROM accounts WHERE account<>?", config.DEMO_ACCOUNT)
                       if config.cookie_file(a["platform"], a["account"]).exists()]
            if not targets:
                print("没有已登录的账号")
                return 1
        results = [collect_one(con, *t) for t in targets]
        return 0 if all(results) else 1
    if args.cmd == "comments":
        comments.ensure_default_rule(con)
        try:
            items = comments.fetch(args.platform, args.account)
        except Exception as e:
            print(f"抓评论失败：{e}")
            return 1
        new = comments.upsert(con, args.platform, args.account, items)
        drafted, queued = comments.process(con)
        print(f"新评论 {new} 条；生成回复草稿 {drafted} 条，排队自动发送 {queued} 条")
        return 0
    if args.cmd == "send-comments":
        todo = db.rows(con, "SELECT * FROM comments WHERE platform=? AND account=? AND state='queued' ORDER BY ctime LIMIT 20",
                       args.platform, args.account)
        if not todo:
            print("没有待发送的回复")
            return 0
        try:
            results = comments.send(args.platform, args.account, todo)
        except Exception as e:
            results = [(c["comment_id"], False, str(e)[:200]) for c in todo]
            print(f"发送失败：{e}")
        comments.mark_sent(con, args.platform, results)
        ok = sum(1 for r in results if r[1])
        print(f"成功 {ok} 条，失败 {len(results) - ok} 条（失败的已退回草稿，可以复制后手动回复）")
        return 0 if ok == len(results) else 1
    if args.cmd == "agent":
        return agent_cli(con, args)
    if args.cmd == "demo":
        demo.load_demo(con)
        print("示例数据已载入")
        return 0
    if args.cmd == "review":
        review_id = review.create(con, args.days or None, args.ai)
        print(db.rows(con, "SELECT content FROM reviews WHERE id=?", review_id)[0]["content"])
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
