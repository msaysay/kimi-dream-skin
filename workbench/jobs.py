"""任务队列：登录、检查登录、发布、采集、复盘都排队，一次只跑一个（浏览器自动化不适合并行）。

登录和发布交给 social-auto-upload 的命令行；采集在单独的进程里跑，浏览器出问题不会拖垮工作台。
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time

from . import config, contents, db, method, planner, review

ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")


def now_ms():
    return int(time.time() * 1000)


def enqueue(con, kind, params):
    with con:
        cur = con.execute("INSERT INTO jobs (type, params, created_at) VALUES (?,?,?)",
                          (kind, json.dumps(params, ensure_ascii=False), now_ms()))
    return cur.lastrowid


def sau_command(platform, action, account, extra=()):
    return [sys.executable, "sau_cli.py", config.PLATFORMS[platform]["sau"], action, "--account", account, *extra]


def publish_args(p):
    """把发布表单变成 social-auto-upload 的命令行参数（不含平台、动作和账号）。"""
    platform = config.PLATFORMS[p["platform"]]
    files = [f for f in p.get("files") or [] if f]
    if not files:
        raise ValueError("没有选择要发布的文件")
    if not (p.get("title") or "").strip():
        raise ValueError("标题不能为空")
    tags = ",".join(t.strip().lstrip("#") for t in (p.get("tags") or "").replace("，", ",").replace(" ", ",").split(",") if t.strip())
    args = []
    if p.get("kind") == "note":
        if not platform["note"]:
            raise ValueError(f"{platform['name']}不支持图文发布")
        args += ["--images", *files, "--title", p["title"], "--note", p.get("desc") or ""]
    else:
        args += ["--file", files[0], "--title", p["title"], "--desc", p.get("desc") or ""]
        if p.get("cover"):
            args += ["--thumbnail", p["cover"]]
        if p.get("draft"):
            if not platform["draft"]:
                raise ValueError(f"{platform['name']}不支持存草稿")
            args.append("--draft")
    if tags:
        args += ["--tags", tags]
    if p.get("schedule"):
        args += ["--schedule", p["schedule"].replace("T", " ")[:16]]
    args.append("--headless" if p.get("headless") else "--headed")
    return args


def action_for(p):
    return "upload-note" if p.get("kind") == "note" else "upload-video"


class Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.proc = None
        self.current = None
        self.cancelled = set()
        self.last_auto_check = 0

    def cancel(self, job_id):
        self.cancelled.add(job_id)
        if self.current == job_id and self.proc and self.proc.poll() is None:
            if os.name == "nt":  # 连同它打开的浏览器一起结束
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)
            else:
                self.proc.kill()

    def log(self, con, job_id, text):
        with con:
            con.execute("UPDATE jobs SET log = log || ? WHERE id=?", (text.rstrip("\n") + "\n", job_id))

    def spawn(self, con, job_id, cmd, cwd):
        """运行子进程，把输出逐行记进任务日志，返回退出码。"""
        self.proc = subprocess.Popen(cmd, cwd=str(cwd), env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
        for line in self.proc.stdout:
            self.log(con, job_id, line)
        code = self.proc.wait()
        self.proc = None
        return code

    def sau(self, con, job_id, platform, action, account, extra=()):
        if not config.sau_installed():
            raise RuntimeError("还没有安装发布引擎 social-auto-upload：请先双击运行「安装.cmd」")
        conf = config.SAU_DIR / "conf.py"
        if not conf.exists():
            shutil.copy(config.SAU_DIR / "conf.example.py", conf)
        return self.spawn(con, job_id, sau_command(platform, action, account, extra), config.SAU_DIR)

    def set_account(self, con, platform, account, status, error=None):
        with con:
            con.execute("INSERT OR IGNORE INTO accounts (platform, account) VALUES (?,?)", (platform, account))
            con.execute("UPDATE accounts SET status=?, last_check=?, last_error=? WHERE platform=? AND account=?",
                        (status, now_ms(), error, platform, account))

    def run_job(self, con, job):
        job_id, p = job["id"], json.loads(job["params"])
        kind = job["type"]
        if kind == "login":
            self.log(con, job_id, "正在打开浏览器，请用手机扫码登录…")
            code = self.sau(con, job_id, p["platform"], "login", p["account"], ["--headed"])
            self.set_account(con, p["platform"], p["account"], "ok" if code == 0 else "expired", None if code == 0 else "登录没有完成")
            return code == 0
        if kind == "check":
            code = self.sau(con, job_id, p["platform"], "check", p["account"])
            self.set_account(con, p["platform"], p["account"], "ok" if code == 0 else "expired", None if code == 0 else "登录已过期，请重新扫码")
            return code == 0
        if kind == "publish":
            code = self.sau(con, job_id, p["platform"], action_for(p), p["account"], publish_args(p))
            if code != 0:
                self.log(con, job_id, "发布没有成功。如果上面提示 cookie 缺失或过期，请到「账号」页重新扫码登录；"
                                      "否则可以取消勾选「后台运行」再发一次，看看浏览器停在哪一步。")
            return code == 0
        if kind == "collect":
            code = self.spawn(con, job_id, [sys.executable, "-m", "workbench", "collect", "--platform", p["platform"], "--account", p["account"]],
                              config.ROOT)
            return code == 0
        if kind == "review":
            review_id = review.create(con, p.get("days") or None, bool(p.get("ai")))
            self.log(con, job_id, f"复盘已生成（编号 {review_id}）")
            return True
        if kind == "plan":
            created = planner.generate(con, p.get("days") or 14, bool(p.get("ai")), log=lambda m: self.log(con, job_id, m))
            return created is not None
        if kind == "refine":
            planner.refine_titles(con, [int(i) for i in p["ids"]], log=lambda m: self.log(con, job_id, m))
            return True
        if kind == "ai_script":
            c = contents.get(con, int(p["content_id"]))
            text = review.chat([{"role": "user", "content": contents.ai_prompt(c, method.load_profile(), p.get("kind") or "script")}])
            field = "notes" if p.get("kind") == "titles" else "script"
            sep = "\n\n---\n" if c[field] else ""
            contents.save(con, {"id": c["id"], field: (c[field] or "") + sep + text.strip()})
            self.log(con, job_id, "已写进这条内容的" + ("备注" if field == "notes" else "脚本"))
            return True
        if kind in ("comments", "comment_send"):
            action = "comments" if kind == "comments" else "send-comments"
            code = self.spawn(con, job_id, [sys.executable, "-m", "workbench", action, "--platform", p["platform"], "--account", p["account"]],
                              config.ROOT)
            return code == 0
        raise RuntimeError(f"不认识的任务类型：{kind}")

    def auto_collect(self, con):
        """工作台开着时，每天到点自动给每个已登录账号排一次采集。"""
        if time.time() - self.last_auto_check < 60:
            return
        self.last_auto_check = time.time()
        s = config.load_settings()
        lt = time.localtime()
        if not s["auto_collect"] or lt.tm_hour < int(s["collect_hour"]):
            return
        midnight = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)) * 1000)
        done = {(json.loads(j["params"]).get("platform"), json.loads(j["params"]).get("account"))
                for j in db.rows(con, "SELECT params FROM jobs WHERE type='collect' AND created_at>=?", midnight)}
        for a in db.rows(con, "SELECT platform, account FROM accounts WHERE status='ok' AND account<>?", config.DEMO_ACCOUNT):
            if (a["platform"], a["account"]) not in done:
                enqueue(con, "collect", {"platform": a["platform"], "account": a["account"], "auto": True})

    def run(self):
        con = db.connect()
        with con:  # 上次没跑完就关掉的任务标成中断
            con.execute("UPDATE jobs SET status='failed', log = log || '工作台关闭，任务中断\n', finished_at=? WHERE status='running'", (now_ms(),))
        while True:
            try:
                self.auto_collect(con)
                job = (db.rows(con, "SELECT * FROM jobs WHERE status='pending' ORDER BY id LIMIT 1") or [None])[0]
                if job is None:
                    time.sleep(1)
                    continue
                self.current = job["id"]
                with con:
                    con.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (now_ms(), job["id"]))
                try:
                    ok = False if job["id"] in self.cancelled else self.run_job(con, job)
                except Exception as e:
                    ok = False
                    self.log(con, job["id"], f"出错：{e}")
                status = "cancelled" if job["id"] in self.cancelled else "done" if ok else "failed"
                with con:
                    con.execute("UPDATE jobs SET status=?, finished_at=? WHERE id=?", (status, now_ms(), job["id"]))
                self.current = None
            except Exception as e:  # 队列本身不能因为一次异常停掉
                print("任务队列出错：", e, file=sys.stderr)
                time.sleep(3)
