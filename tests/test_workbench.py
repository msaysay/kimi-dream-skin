import json
import shutil
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import FAKE_PORT, free_port
from workbench import analytics, collect, config, db, demo, ingest, jobs, review, server

import fake_platforms

SNAPSHOT_COLS = [c for c in db.SNAP_COLS]


@pytest.fixture()
def con():
    shutil.rmtree(config.DATA, ignore_errors=True)
    c = db.connect()
    yield c
    c.close()


def latest_metrics(con, account):
    """每条作品的基本信息和最新指标，不含采集时间。"""
    out = {}
    for p in db.rows(con, "SELECT * FROM posts WHERE account=?", account):
        snap = db.rows(con, "SELECT * FROM snapshots WHERE platform=? AND post_id=? ORDER BY fetched_at DESC LIMIT 1", p["platform"], p["post_id"])[0]
        extra = {t: db.rows(con, f"SELECT * FROM {t} WHERE platform=? AND post_id=? ORDER BY 1,2,3,4", p["platform"], p["post_id"])
                 for t in ("retention", "traffic_source", "audience", "trend")}
        out[(p["platform"], p["post_id"])] = ({k: v for k, v in p.items() if k != "account"}, {c: snap[c] for c in SNAPSHOT_COLS}, extra)
    return out


def test_demo_data_flows_through_ingest_and_analytics(con):
    demo.load_demo(con)
    data = analytics.build(con, 30)
    assert data["totals"]["works"] == len(demo.WORKS)
    assert data["totals"]["posts"] == sum(len(w[-1]) for w in demo.WORKS)
    for w in data["works"]:  # 同一个作品里每个平台最多一条
        assert len({p["platform"] for p in w["posts"]}) == len(w["posts"])
    by_title = {w["title"]: w for w in data["works"]}
    # 这条示例作品的开头被设成很弱：抖音和小红书应该诊断出卡在「点进来」
    weak = by_title["一周备餐：五十块钱吃七天"]
    assert {p["platform"]: p["weak_stage"]["key"] for p in weak["posts"] if p["platform"] != "channels"} == {"douyin": "click", "xhs": "click"}
    # 这条全面好于平时：没有掉队环节，播放高于平时
    strong = by_title["我为什么不再推荐这款网红锅"]
    assert all(p["weak_stage"] is None and p["rel"]["views"] > 1.5 for p in strong["posts"])
    # 三次快照形成增长曲线
    assert all(len(p["history"]) == 3 for p in strong["posts"])
    assert [h["views"] for h in strong["posts"][0]["history"]] == sorted(h["views"] for h in strong["posts"][0]["history"])


def test_relative_needs_three_posts(con):
    demo.make_raw()
    lst = config.DATA / "raw" / "xhs" / config.DEMO_ACCOUNT / "_list.json"
    d = json.loads(lst.read_text(encoding="utf-8"))
    d["notes"] = d["notes"][:2]
    lst.write_text(json.dumps(d), encoding="utf-8")
    assert ingest.ingest(con, "xhs", config.DEMO_ACCOUNT) == 2
    data = analytics.build(con)
    posts = [p for w in data["works"] for p in w["posts"]]
    assert all(p["rel"]["views"] is None and p["weak_stage"] is None for p in posts)


def test_ingest_skips_malformed_post_but_keeps_others(con):
    demo.make_raw()
    raw = config.DATA / "raw" / "douyin" / config.DEMO_ACCOUNT
    first = json.loads((raw / "_list.json").read_text(encoding="utf-8"))["items"][0]["id"]
    (raw / f"{first}.json").write_text('{"item_summarize": {"item_list": "格式变了"}}', encoding="utf-8")
    messages = []
    saved = ingest.ingest(con, "douyin", config.DEMO_ACCOUNT, log=messages.append)
    assert saved == sum("douyin" in w[-1] for w in demo.WORKS) - 1
    assert len(messages) == 1 and first in messages[0]


def test_rule_review_and_summary(con):
    demo.load_demo(con)
    summary = review.summarize(analytics.build(con, 30))
    json.dumps(summary)  # 要能原样发给大模型
    text = review.rule_report(summary)
    for heading in ("## 总览", "## 表现最好的作品", "## 最需要回看的作品", "## 平台对比"):
        assert heading in text
    assert "我为什么不再推荐这款网红锅" in text.split("## 最需要回看的作品")[0]
    assert "没有作品数据" in review.rule_report(review.summarize(analytics.build(con, 0.0001)))


def test_ai_review_requires_config(con):
    with pytest.raises(RuntimeError, match="还没有配置大模型"):
        review.ai_report({}, dict(config.DEFAULT_SETTINGS))


def test_publish_args():
    base = {"platform": "channels", "account": "主号", "files": ["a.mp4"], "title": "标题", "desc": "简介", "tags": "#健身，拉伸 新手"}
    assert jobs.publish_args(dict(base, draft=True, schedule="2026-12-01T20:30")) == [
        "--file", "a.mp4", "--title", "标题", "--desc", "简介", "--draft", "--tags", "健身,拉伸,新手", "--schedule", "2026-12-01 20:30", "--headed"]
    note = jobs.publish_args(dict(base, platform="xhs", kind="note", files=["1.png", "2.png"], headless=True))
    assert note == ["--images", "1.png", "2.png", "--title", "标题", "--note", "简介", "--tags", "健身,拉伸,新手", "--headless"]
    for bad, message in ((dict(base, files=[]), "没有选择"), (dict(base, title=" "), "标题不能为空"),
                         (dict(base, kind="note"), "不支持图文"), (dict(base, platform="douyin", draft=True), "不支持存草稿")):
        with pytest.raises(ValueError, match=message):
            jobs.publish_args(bad)


def test_sau_cli_accepts_our_arguments(tmp_path):
    """发布引擎装好时，确认它的命令行认得我们生成的每一个参数。"""
    if not config.sau_installed():
        pytest.skip("没有安装 social-auto-upload")
    import subprocess, sys
    video, image = tmp_path / "v.mp4", tmp_path / "c.png"
    video.write_bytes(b"0"); image.write_bytes(b"0")
    cases = [{"platform": pf, "kind": "video", "files": [str(video)], "cover": str(image), "title": "标题", "desc": "简介", "tags": "a,b",
              "schedule": "2030-01-01T20:30", "draft": pf == "channels"} for pf in config.PLATFORMS]
    cases += [{"platform": pf, "kind": "note", "files": [str(image), str(image)], "title": "标题", "desc": "正文", "tags": "a", "headless": True}
              for pf in ("douyin", "xhs")]
    script = ("import sys, json, sau_cli\nfor argv in json.loads(sys.stdin.read()):\n    sau_cli.build_parser().parse_args(argv)\nprint('ok')")
    argvs = [jobs.sau_command(c["platform"], jobs.action_for(c), "测试", jobs.publish_args(c))[2:] for c in cases]
    argvs += [jobs.sau_command(pf, "login", "测试", ["--headed"])[2:] for pf in config.PLATFORMS] + [jobs.sau_command(pf, "check", "测试")[2:] for pf in config.PLATFORMS]
    conf = config.SAU_DIR / "conf.py"
    if not conf.exists():
        shutil.copy(config.SAU_DIR / "conf.example.py", conf)
    out = subprocess.run([sys.executable, "-c", script], input=json.dumps(argvs), cwd=config.SAU_DIR, capture_output=True, text=True, encoding="utf-8")
    assert out.stdout.strip().endswith("ok"), out.stderr[-2000:]


@pytest.fixture()
def api(con):
    port = free_port()

    class NoWorker:
        current = None
        def cancel(self, job_id): pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), server.make_handler(NoWorker(), port))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def call(path, body=None, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    yield call
    httpd.shutdown()


def test_api_accounts_and_publish_validation(api, con, tmp_path):
    assert api("/api/state")[1]["accounts"] == []
    assert api("/api/accounts/add", {"platform": "douyin", "account": "../x"})[0] == 400
    assert api("/api/accounts/add", {"platform": "douyin", "account": "主号"})[0] == 200
    assert [(a["platform"], a["account"], a["has_login"]) for a in api("/api/state")[1]["accounts"]] == [("douyin", "主号", False)]
    target = {"platform": "douyin", "account": "主号"}
    status, body = api("/api/publish", {"files": [str(tmp_path / "missing.mp4")], "title": "t", "targets": [target]})
    assert status == 400 and "找不到文件" in body["error"]
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0")
    assert api("/api/publish", {"files": [str(video)], "title": "", "targets": [target]})[0] == 400
    assert api("/api/publish", {"files": [str(video)], "title": "t", "targets": [{"platform": "douyin", "account": "没有这个号"}]})[0] == 400
    status, body = api("/api/publish", {"files": [str(video)], "title": "t", "targets": [target]})
    assert status == 200 and len(body["jobs"]) == 1
    job = api(f"/api/jobs/{body['jobs'][0]}")[1]
    assert job["status"] == "pending" and job["params"]["files"] == [str(video)]
    assert api("/api/collect", {})[0] == 400  # 没有已登录的账号


def test_api_rejects_other_origins(api):
    assert api("/api/demo", {"action": "load"}, origin="http://evil.example")[0] == 403
    assert api("/api/state")[1]["has_data"] is False


def test_api_demo_and_settings(api):
    assert api("/api/demo", {"action": "load"})[0] == 200
    assert api("/api/data?days=30")[1]["totals"]["works"] == len(demo.WORKS)
    assert api("/api/accounts/login", {"platform": "douyin", "account": config.DEMO_ACCOUNT})[0] == 400
    api("/api/settings", {"ai_api_key": "sk-secret", "ai_model": "m", "collect_hour": "7", "unknown": 1})
    settings = api("/api/state")[1]["settings"]
    assert settings["ai_api_key_set"] is True and "ai_api_key" not in settings and settings["collect_hour"] == 7
    api("/api/settings", {"ai_api_key": ""})
    assert config.load_settings()["ai_api_key"] == "sk-secret"
    api("/api/demo", {"action": "clear"})
    assert api("/api/state")[1]["has_data"] is False


# ---------- 采集：用真实浏览器访问假的后台站点 ----------
@pytest.fixture(scope="module")
def fake_site(tmp_path_factory):
    try:
        collect.sync_playwright
        import patchright  # noqa: F401
    except ImportError:
        pytest.importorskip("playwright")
    raw = tmp_path_factory.mktemp("fake-raw")
    demo.make_raw(root=raw, account="fake")
    httpd = fake_platforms.start(FAKE_PORT, raw, "fake")
    yield raw
    httpd.shutdown()


def state_file(tmp_path, logged_in):
    cookies = [{"name": "session", "value": "ok", "domain": "127.0.0.1", "path": "/", "expires": -1, "httpOnly": False, "secure": False, "sameSite": "Lax"}]
    f = tmp_path / "state.json"
    f.write_text(json.dumps({"cookies": cookies if logged_in else [], "origins": []}), encoding="utf-8")
    return f


@pytest.mark.parametrize("platform", list(config.PLATFORMS))
def test_collect_from_fake_site_matches_source_data(platform, fake_site, con, tmp_path):
    config.save_settings({"max_posts": 50})
    f = state_file(tmp_path, True)
    logs = []
    count = collect.run(platform, "采集测试", log=logs.append, state_file=f)
    expected_count = sum(platform in w[-1] for w in demo.WORKS)
    assert count == expected_count and len(logs) == expected_count
    assert ingest.ingest(con, platform, "采集测试") == expected_count
    got = latest_metrics(con, "采集测试")
    # 对照：把假站点背后的同一份数据直接入库
    shutil.copytree(fake_site / platform / "fake", config.DATA / "raw" / platform / "对照")
    ingest.ingest(con, platform, "对照")
    want = latest_metrics(con, "对照")
    assert got == want and len(got) == expected_count
    some = next(iter(got.values()))
    assert some[1]["views"] and some[2]["retention"] and some[2]["traffic_source"]
    assert "session" in f.read_text(encoding="utf-8")  # 登录态被存回


@pytest.mark.parametrize("platform", list(config.PLATFORMS))
def test_collect_reports_expired_login(platform, fake_site, con, tmp_path):
    with pytest.raises(collect.NotLoggedIn):
        collect.run(platform, "采集测试", log=lambda m: None, state_file=state_file(tmp_path, False))
    with pytest.raises(collect.NotLoggedIn, match="还没有登录"):
        collect.run(platform, "采集测试", state_file=tmp_path / "nope.json")
