"""人工发布：有的平台（小红书）对自动提交很敏感，容易判违规。这些平台工作台只负责把素材和文案备好，
由你自己在手机或浏览器里发出去，发完回来点一下「我已发布」，后面的确认、关联选题、验收照常进行。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import config
from .db import rows

# 各平台人工发布时要自己留意的事（自动发布顾不到的）
REMINDERS = {
    "xhs": ["内容是 AI 制作的：在「内容类型声明」里勾选「笔记含 AI 合成内容」",
            "结尾不要写「评论区打某某领取」这类话，小红书会当成诱导互动；改成「主页置顶有完整流程」之类的站内指引",
            "标题最多 20 字；封面用这里给的那张，已经按小红书比例裁好"],
}


def now_ms():
    return int(time.time() * 1000)


def is_manual(platform):
    return platform == "xhs" and bool(config.load_settings()["xhs_manual"])


def create(con, p):
    """把一次发布存成「待你手动发」。p 和发布任务的参数相同。"""
    cover = next(iter((p.get("covers") or {}).values()), "")
    with con:
        return con.execute("""INSERT INTO manual_posts (platform, account, content_id, kind, title, body, tags, files, cover, schedule, created_at)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                           (p["platform"], p["account"], p.get("content_id"), p.get("kind") or "video", p["title"], p.get("desc") or "",
                            p.get("tags") or "", json.dumps(p.get("files") or [], ensure_ascii=False), cover, p.get("schedule") or "", now_ms())).lastrowid


def decode(r):
    r = dict(r)
    r["files"] = json.loads(r["files"] or "[]")
    tags = [t.strip().lstrip("#") for t in r["tags"].replace("，", ",").replace("、", ",").replace(" ", ",").split(",") if t.strip()]
    r["tags_text"] = " ".join("#" + t for t in tags)
    r["reminders"] = REMINDERS.get(r["platform"], [])
    return r


def todo(con):
    return [decode(r) for r in rows(con, "SELECT * FROM manual_posts WHERE status='todo' ORDER BY id")]


def get(con, post_id):
    r = rows(con, "SELECT * FROM manual_posts WHERE id=?", int(post_id))
    if not r:
        raise ValueError("没有这条待发布的内容")
    return decode(r[0])


def finish(con, post_id, status="done"):
    """点「我已发布」或「不发了」。发了的返回核验任务的参数（到作品列表里确认并关联选题）。"""
    if status not in ("done", "skipped"):
        raise ValueError("状态不对")
    m = get(con, post_id)
    with con:
        con.execute("UPDATE manual_posts SET status=?, done_at=? WHERE id=?", (status, now_ms(), int(post_id)))
        if status == "done" and m["content_id"]:
            con.execute("UPDATE contents SET stage='发布', updated_at=? WHERE id=?", (now_ms(), m["content_id"]))
    if status != "done":
        return None
    # 核验记录用负数编号，和自动发布任务的编号错开
    return {"platform": m["platform"], "account": m["account"], "title": m["title"], "desc": m["body"], "content_id": m["content_id"],
            "publish_job": -int(post_id), "since": m["created_at"], "attempt": 0, "not_before": now_ms() + 60 * 1000}


def open_folder(con, post_id, which="video"):
    """在文件管理器里打开素材所在的文件夹（只能打开这条内容自己的文件）。"""
    m = get(con, post_id)
    path = m["cover"] if which == "cover" else (m["files"][0] if m["files"] else "")
    if not path or not Path(path).exists():
        raise ValueError("文件不在了：" + (path or "没有文件"))
    folder = str(Path(path).resolve().parent)
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(Path(path).resolve())])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(Path(path).resolve())])
    else:
        subprocess.Popen(["xdg-open", folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return folder
