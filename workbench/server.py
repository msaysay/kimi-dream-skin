"""本地网页服务：只监听 127.0.0.1，给浏览器里的工作台界面提供数据和操作接口。"""
import json
import mimetypes
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import analytics, config, db, demo, jobs

ACCOUNT_RE = re.compile(r"^[\w一-鿿-]{1,30}$")

PICK_SCRIPT = """
import json, sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
kinds = {'video': [('视频', '*.mp4 *.mov *.mkv *.avi *.flv *.webm'), ('所有文件', '*.*')],
         'image': [('图片', '*.jpg *.jpeg *.png *.webp'), ('所有文件', '*.*')]}[sys.argv[1]]
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
    for j in job_rows:
        j["params"] = json.loads(j["params"])
    return {"accounts": accounts, "jobs": job_rows, "settings": config.public_settings(), "sau_installed": config.sau_installed(),
            "platforms": config.PLATFORMS, "demo_account": config.DEMO_ACCOUNT, "busy": worker.current is not None,
            "has_data": con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] > 0}


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
        queued = []
        for t in targets:
            require_account(con, t.get("platform"), t.get("account"))
            p = {"platform": t["platform"], "account": t["account"], "kind": body.get("kind") or "video", "files": files, "cover": cover,
                 "title": (t.get("title") or body.get("title") or "").strip(), "desc": body.get("desc") or "", "tags": body.get("tags") or "",
                 "schedule": body.get("schedule") or "", "draft": bool(t.get("draft")), "headless": bool(body.get("headless"))}
            try:
                jobs.publish_args(p)
            except ValueError as e:
                raise ApiError(f"{config.PLATFORMS[t['platform']]['name']}：{e}")
            queued.append(p)
        return {"jobs": [jobs.enqueue(con, "publish", p) for p in queued]}
    if path == "/api/review":
        return {"job": jobs.enqueue(con, "review", {"days": int(body.get("days") or 0), "ai": bool(body.get("ai"))})}
    if path == "/api/reviews/delete":
        with con:
            con.execute("DELETE FROM reviews WHERE id=?", (body.get("id"),))
        return {"ok": True}
    if path == "/api/settings":
        if body.get("ai_api_key") == "":
            body.pop("ai_api_key")  # 留空表示不改
        config.save_settings(body)
        return {"ok": True}
    if path == "/api/demo":
        demo.load_demo(con) if body.get("action") == "load" else demo.clear_demo(con)
        return {"ok": True}
    if path == "/api/pick":
        kind = "image" if body.get("kind") == "image" else "video"
        try:
            out = subprocess.run([sys.executable, "-c", PICK_SCRIPT, kind, "many" if body.get("many") else "one"],
                                 capture_output=True, text=True, encoding="utf-8", timeout=600)
            return {"files": json.loads(out.stdout.strip().splitlines()[-1])}
        except Exception:
            raise ApiError("打不开文件选择窗口，请直接把文件的完整路径粘贴到输入框里")
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
    if path == "/api/reviews":
        return {"reviews": db.rows(con, "SELECT * FROM reviews ORDER BY id DESC LIMIT 30")}
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
    worker = jobs.Worker()
    worker.start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(worker, port))
    url = f"http://127.0.0.1:{port}"
    print(f"创作者工作台已启动：{url}\n关闭这个窗口就会停止工作台。")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
