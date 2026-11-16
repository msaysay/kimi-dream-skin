"""命令行入口：python -m workbench [serve|collect|demo|review]"""
import argparse
import sys
import time

from . import collect, config, db, demo, ingest, review, server


def collect_one(con, platform, account):
    name = f"{config.PLATFORMS[platform]['name']}「{account}」"
    try:
        count = collect.run(platform, account)
        saved = ingest.ingest(con, platform, account)
        with con:
            con.execute("UPDATE accounts SET status='ok', last_collect=?, last_error=NULL WHERE platform=? AND account=?",
                        (int(time.time() * 1000), platform, account))
        print(f"{name} 采集完成：{count} 条作品，入库 {saved} 条")
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


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # Windows 终端默认不是 UTF-8
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="workbench", description="创作者工作台")
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("serve", help="启动工作台（默认）")
    p.add_argument("--port", type=int)
    p.add_argument("--no-browser", action="store_true")
    p = sub.add_parser("collect", help="采集数据；不指定账号就采集所有已登录的账号")
    p.add_argument("--platform", choices=list(config.PLATFORMS))
    p.add_argument("--account")
    sub.add_parser("demo", help="载入示例数据")
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
