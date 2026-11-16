"""SQLite 数据库：账号、作品、指标快照、任务、复盘。"""
import sqlite3

from . import config

SNAP_COLS = ["impressions", "views", "cover_click_rate", "likes", "comments", "collects", "shares", "new_fans",
             "avg_view_sec", "finish_rate", "finish5s_rate", "exit2s_rate", "profile_visits"]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS accounts (platform TEXT, account TEXT, status TEXT DEFAULT 'unknown', last_check INTEGER,
  last_collect INTEGER, last_error TEXT, PRIMARY KEY (platform, account));
CREATE TABLE IF NOT EXISTS posts (platform TEXT, post_id TEXT, account TEXT, kind TEXT, title TEXT, post_time INTEGER,
  duration_sec REAL, tags TEXT, PRIMARY KEY (platform, post_id));
CREATE TABLE IF NOT EXISTS snapshots (platform TEXT, post_id TEXT, fetched_at INTEGER, {", ".join(c + " REAL" for c in SNAP_COLS)},
  PRIMARY KEY (platform, post_id, fetched_at));
CREATE TABLE IF NOT EXISTS retention (platform TEXT, post_id TEXT, series TEXT, sec INTEGER, pct REAL,
  PRIMARY KEY (platform, post_id, series, sec));
CREATE TABLE IF NOT EXISTS traffic_source (platform TEXT, post_id TEXT, source TEXT, share_pct REAL,
  PRIMARY KEY (platform, post_id, source));
CREATE TABLE IF NOT EXISTS audience (platform TEXT, post_id TEXT, dim TEXT, label TEXT, pct REAL,
  PRIMARY KEY (platform, post_id, dim, label));
CREATE TABLE IF NOT EXISTS trend (platform TEXT, post_id TEXT, grain TEXT, metric TEXT, ts INTEGER, value REAL,
  PRIMARY KEY (platform, post_id, grain, metric, ts));
CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, params TEXT, status TEXT DEFAULT 'pending',
  log TEXT DEFAULT '', created_at INTEGER, started_at INTEGER, finished_at INTEGER);
CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER, days INTEGER, kind TEXT,
  content TEXT);
"""

POST_TABLES = ("posts", "snapshots", "retention", "traffic_source", "audience", "trend")


def connect():
    config.ensure_dirs()
    con = sqlite3.connect(config.DATA / "workbench.db", timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def rows(con, sql, *args):
    return [dict(r) for r in con.execute(sql, args)]


def delete_account_data(con, platform, account):
    ids = [r["post_id"] for r in con.execute("SELECT post_id FROM posts WHERE platform=? AND account=?", (platform, account))]
    for t in POST_TABLES:
        con.executemany(f"DELETE FROM {t} WHERE platform=? AND post_id=?", [(platform, i) for i in ids])
    con.execute("DELETE FROM accounts WHERE platform=? AND account=?", (platform, account))
