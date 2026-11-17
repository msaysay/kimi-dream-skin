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
-- 内容：一条选题从计划、创作、发布到验收的全过程；发出去之后关联到各平台的作品
CREATE TABLE IF NOT EXISTS contents (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, workflow TEXT, stage TEXT,
  priority TEXT, motif TEXT, archetype TEXT, hook_open TEXT, hook_mid TEXT, hook_end TEXT, duration_bin TEXT,
  presence TEXT, rhythm TEXT, cover_type TEXT, basis TEXT, targets TEXT DEFAULT '[]', acceptance TEXT,
  week TEXT, schedule_date TEXT, script TEXT DEFAULT '', notes TEXT DEFAULT '', checklist TEXT DEFAULT '{{}}',
  source TEXT, verdict_from TEXT, created_at INTEGER, updated_at INTEGER, archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS content_posts (content_id INTEGER, platform TEXT, post_id TEXT, PRIMARY KEY (platform, post_id));
-- 作品状态：仅自己可见、删除等异常，不进评分和结论，只进待重测
CREATE TABLE IF NOT EXISTS post_flags (platform TEXT, post_id TEXT, flag TEXT, note TEXT, PRIMARY KEY (platform, post_id));
CREATE TABLE IF NOT EXISTS comments (platform TEXT, comment_id TEXT, account TEXT, post_id TEXT, post_title TEXT,
  nick TEXT, text TEXT, ctime INTEGER, fetched_at INTEGER, is_question INTEGER DEFAULT 0, state TEXT DEFAULT 'new',
  rule_id INTEGER, reply TEXT, replied_at INTEGER, error TEXT, PRIMARY KEY (platform, comment_id));
-- 对标账号与它们的作品（TikHub 读取的公开数据）
CREATE TABLE IF NOT EXISTS benchmarks (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT, uid TEXT, name TEXT, avatar TEXT,
  followers INTEGER, note TEXT DEFAULT '', grp TEXT DEFAULT '', enabled INTEGER DEFAULT 1, added_at INTEGER, last_fetch INTEGER,
  last_error TEXT, UNIQUE (platform, uid));
CREATE TABLE IF NOT EXISTS bench_posts (platform TEXT, post_id TEXT, bench_id INTEGER, title TEXT, url TEXT, cover TEXT,
  post_time INTEGER, duration_sec REAL, kind TEXT, is_top INTEGER DEFAULT 0, first_seen INTEGER,
  status TEXT DEFAULT 'new', tags TEXT DEFAULT '{{}}', analysis TEXT DEFAULT '', content_id INTEGER, PRIMARY KEY (platform, post_id));
CREATE TABLE IF NOT EXISTS bench_snaps (platform TEXT, post_id TEXT, fetched_at INTEGER, likes INTEGER, comments INTEGER,
  shares INTEGER, collects INTEGER, plays INTEGER, PRIMARY KEY (platform, post_id, fetched_at));
-- 打法库：用户对自动总结出来的打法的确认 / 否决 / 补充
CREATE TABLE IF NOT EXISTS lesson_notes (key TEXT PRIMARY KEY, verdict TEXT, note TEXT, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS api_calls (ts INTEGER, path TEXT, ok INTEGER, price REAL);
-- AI 待办：交给 AI 助手（Claude Code / Codex / WorkBuddy）做的事
CREATE TABLE IF NOT EXISTS agent_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, ref TEXT, params TEXT DEFAULT '{{}}',
  status TEXT DEFAULT 'pending', note TEXT, created_at INTEGER, done_at INTEGER);
CREATE TABLE IF NOT EXISTS comment_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, platforms TEXT,
  keywords TEXT, exclude TEXT DEFAULT '', templates TEXT DEFAULT '[]', use_ai INTEGER DEFAULT 0,
  auto_send INTEGER DEFAULT 0, daily_cap INTEGER DEFAULT 20, enabled INTEGER DEFAULT 1, is_intent INTEGER DEFAULT 0,
  created_at INTEGER);
"""

POST_TABLES = ("posts", "snapshots", "retention", "traffic_source", "audience", "trend", "content_posts", "post_flags", "comments")


def connect():
    config.ensure_dirs()
    con = sqlite3.connect(config.DATA / "workbench.db", timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    migrate(con)
    return con


# 老版本建的表缺的列：启动时补上，已有数据不动
COLUMNS = {"contents": {"experiment": "TEXT", "conclusion": "TEXT", "pub_desc": "TEXT", "pub_tags": "TEXT"}}


def migrate(con):
    for table, cols in COLUMNS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for col, kind in cols.items():
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
    con.commit()


def rows(con, sql, *args):
    return [dict(r) for r in con.execute(sql, args)]


def delete_account_data(con, platform, account):
    ids = [r["post_id"] for r in con.execute("SELECT post_id FROM posts WHERE platform=? AND account=?", (platform, account))]
    for t in POST_TABLES:
        con.executemany(f"DELETE FROM {t} WHERE platform=? AND post_id=?", [(platform, i) for i in ids])
    con.execute("DELETE FROM accounts WHERE platform=? AND account=?", (platform, account))
