import json
import random
import shutil
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import FAKE_PORT, free_port
from workbench import analytics, collect, comments, config, contents, db, demo, importer, ingest, jobs, method, planner, review, server

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
    for w in data["works"]:  # 同一个作品里每个平台最多一条，且示例作品都带结构标签
        assert len({p["platform"] for p in w["posts"]}) == len(w["posts"])
        assert w["tags"].get("archetype")
    posts = [p for w in data["works"] for p in w["posts"]]
    assert all(p["verdict"] in method.VERDICTS for p in posts)
    assert all(len(p["funnel"]) == 5 and p["flow"] is not None for p in posts)
    # 三次快照形成增长曲线，且能取到 D1 快照
    assert all(len(p["history"]) >= 2 for p in posts if (p["age_days"] or 0) > 2)
    assert any("D1" in p["slots"] for p in posts)
    assert data["matrix"]["archetype"] and data["matrix"]["hook_end"]


def test_relative_needs_three_posts(con):
    demo.make_raw()
    lst = config.DATA / "raw" / "xhs" / config.DEMO_ACCOUNT / "_list.json"
    d = json.loads(lst.read_text(encoding="utf-8"))
    d["notes"] = d["notes"][:2]
    lst.write_text(json.dumps(d), encoding="utf-8")
    assert ingest.ingest(con, "xhs", config.DEMO_ACCOUNT) == 2
    data = analytics.build(con)
    posts = [p for w in data["works"] for p in w["posts"]]
    assert all(p["rel"]["views"] is None for p in posts)


def test_ingest_skips_malformed_post_but_keeps_others(con):
    demo.make_raw()
    raw = config.DATA / "raw" / "douyin" / config.DEMO_ACCOUNT
    first = json.loads((raw / "_list.json").read_text(encoding="utf-8"))["items"][0]["id"]
    (raw / f"{first}.json").write_text('{"item_summarize": {"item_list": "格式变了"}}', encoding="utf-8")
    messages = []
    saved = ingest.ingest(con, "douyin", config.DEMO_ACCOUNT, log=messages.append)
    assert saved == sum("douyin" in w[-1] for w in demo.WORKS) - 1
    assert len(messages) == 1 and first in messages[0]


# ---------- 方法论：用一份和《三平台内容数据工作台》同格式的表格验证评分口径 ----------
# 数字取自方法论文档 2026-11-10 至 11-16 的五条视频号作品，标题换成了中性的「作品 A–E」
SAMPLE = [  # 作品ID, 标题, 发布时间, 时长, 播放, 完播率, 平均时长, 点赞, 评论, 分享, 关注, 来源(推荐,朋友,分享来,订阅号,主页,其他), 原型, 开头钩, 结尾钩, 备注
    ("a", "作品A 不露脸流程", "2026-11-16 10:51:00", 28, 1827, 0.1034, 16.84, 85, 9, 52, 37, None, "观点论证型", "反常识断言", "资产给予", ""),
    ("b", "作品B 组件开源", "2026-11-15 16:47:00", 31, 1265, 0.1187, 15.9, 37, 18, 38, 7, (309, 39, 180, 3, 29, 44), "清单型", "结果奇观", "资产给予", ""),
    ("c", "作品C 自己拆自己", "2026-11-14 21:15:00", 42, 4066, 0.1389, 25.82, 118, 23, 141, 46, (1049, 321, 474, 20, 50, 246), "教程型", "反常识断言", "资产给予", ""),
    ("d", "作品D 人物故事", "2026-11-14 01:44:00", 110, 10151, 0.1969, 94.67, 142, 4, 46, 28, (6598, 267, 768, 483, 123, 1731), "故事型", "悬念提问", "无", ""),
    ("e", "作品E 效果演示", "2026-11-11 15:16:00", 22, 773, 0.0944, 15.95, 22, 2, 7, 2, (592, 64, 31, 11, 26, 43), "演示型", "结果奇观", "无", "采集时仅自己可见，播放不可采信"),
]
EXPECTED = {"a": (64.9, "垂直型"), "b": (63.5, "垂直型"), "c": (73.9, "双强型"), "d": (67.6, "泛流量型"), "e": (42.1, None)}


def make_workbook(path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "01_底表·统一事实表"
    head = ["平台", "作品ID", "标题/文案（原文）", "发布时间", "采集时点", "快照档位", "时长(秒)", "播放/阅读", "曝光", "完播率", "平均播放时长(秒)",
            "点赞", "收藏", "评论", "分享", "关注/涨粉", "主页访问", "私信/加微", "推荐", "朋友", "分享来", "订阅号消息", "主页来", "其他",
            "内容原型", "开头钩", "结尾钩", "备注"]
    ws.append(["01 底表"]); ws.append(["说明"]); ws.append(head)
    for pid, title, t, dur, v, fr, avg, like, com, sh, fol, src, arch, ho, he, note in SAMPLE:
        src = src or (None,) * 6
        ws.append(["视频号", pid, title, t, "2026-11-17 00:00:00", "", dur, v, None, fr, avg, like, None, com, sh, fol, None, 0, *src, arch, ho, he, note])
    tags = wb.create_sheet("04_内容结构标签")
    tags.append(["04"]); tags.append([None, "作品", "母题", "原型", "开头钩", "中段留人", "结尾钩", "时长档", "出镜"])
    motifs = {"a": "AI 制作流程拆解", "b": "AI 工具实测", "c": "AI 制作流程拆解", "d": "AI 行业人物与公司故事", "e": "AI 工具实测"}
    for row in SAMPLE:
        tags.append([None, row[1], motifs[row[0]], row[12], row[13], "信息密度", row[14], "<30s", "不露脸"])
    tail = wb.create_sheet("08_流量结构与长尾")
    tail.append(["08"]); tail.append(["作品", "首日播放 D0", "次日播放 D1", "累计播放"])
    for short, d0, d1 in (("作品B 组件开源", 606, None), ("作品C 自己拆自己", 729, 1435), ("作品D 人物故事", 9688, 283), ("作品E 效果演示", 422, 227)):
        tail.append([short, d0, d1])
    wb.save(path)


def test_import_workbook_reproduces_methodology_scores(con, tmp_path):
    make_workbook(tmp_path / "wb.xlsx")
    r = importer.import_workbook(con, str(tmp_path / "wb.xlsx"), "主号")
    assert r == {"posts": 5, "tagged": 5, "account": "主号", "flagged": 1}
    data = analytics.build(con)
    posts = {p["post_id"]: p for w in data["works"] for p in w["posts"]}
    for pid, (total, verdict) in EXPECTED.items():
        assert posts[pid]["total"] == total, pid
        assert posts[pid]["verdict"] == verdict, pid
    assert posts["e"]["flag"] == "仅自己可见"
    assert posts["c"]["tags"]["motif"] == "AI 制作流程拆解"
    # 首日占比与复利：人物故事 95.4% 首日，自己拆自己次日反超
    assert round(posts["d"]["first_day_share"], 3) == 0.954 and posts["c"]["d2_over_d1"] > 1
    assert round(posts["d"]["rec_share"], 3) == 0.662
    s = data["summary"]["channels"]
    assert round(s["hook_follow"] / s["nohook_follow"], 1) == 4.6  # 结尾钩：有钩组关注率是无钩组的 4.6 倍
    assert s["funnel"][4]["level"] == "无数据"
    # 改一下阈值，评分跟着变
    method.save_params({"thresholds": {"channels": {"views": [20000, 10000, 5000]}}})
    assert {p["post_id"]: p for w in analytics.build(con)["works"] for p in w["posts"]}["d"]["flow"] == 82.5


def test_plan_follows_decision_table(con, tmp_path):
    make_workbook(tmp_path / "wb.xlsx")
    importer.import_workbook(con, str(tmp_path / "wb.xlsx"), "主号")
    items, notes = planner.candidates(analytics.build(con))
    by_kind = {c["verdict_from"]: c for c in items}
    assert by_kind["双强型"]["priority"] == "P0" and by_kind["双强型"]["tags"]["archetype"] == "教程型"
    # 泛流量型只改结尾钩，其他标签不动
    flow = by_kind["泛流量型"]
    assert flow["priority"] == "P0" and flow["tags"]["hook_end"] != "无" and flow["tags"]["archetype"] == "故事型"
    assert flow["tags"]["hook_open"] == "悬念提问"
    # 垂直型换壳：只换母题
    shells = [c for c in items if c["verdict_from"] == "垂直型"]
    assert len(shells) == 2 and all(c["priority"] == "P1" for c in shells)
    assert by_kind["待重测"]["priority"] == "P2"
    assert by_kind["母题缺口"]["tags"]["motif"] == "AI 概念科普"
    assert sum(c["priority"] == "P0" for c in items) <= method.load_params()["max_p0"]
    created = planner.generate(con, days=0)
    assert len(created) == len(items) and planner.generate(con, days=0) == []  # 重复生成不会重复写入
    c = contents.get(con, created[0])
    assert c["targets"] and c["acceptance"] and c["week"] == planner.week_label()


def test_content_link_and_acceptance(con, tmp_path):
    make_workbook(tmp_path / "wb.xlsx")
    importer.import_workbook(con, str(tmp_path / "wb.xlsx"), "主号")
    cid = contents.save(con, {"title": "测试选题", "archetype": "教程型", "hook_end": "资产给予",
                              "targets": [{"metric": "follow_rate", "value": 0.01, "slot": "D3"}, {"metric": "views", "value": 5000, "slot": "D3"}]})
    with pytest.raises(ValueError, match="不在字典里"):
        contents.save(con, {"title": "x", "archetype": "自创原型"})
    contents.unlink(con, "channels", "c")  # 导入时它已经关联到补标签的内容，这里模拟一条新发的作品
    # 发布任务带着内容编号，采集后按标题自动关联
    with con:
        con.execute("INSERT INTO jobs (type, params, status, created_at, finished_at) VALUES ('publish', ?, 'done', 0, ?)",
                    (json.dumps({"platform": "channels", "account": "主号", "title": "作品C 自己拆自己", "content_id": cid}), 1763000000000))
    assert contents.auto_link(con) == 1
    item = contents.with_acceptance(con, [c for c in contents.list_all(con) if c["id"] == cid])[0]
    assert item["stage"] == "数据验收" and [p["post_id"] for p in item["posts"]] == ["c"]
    # 只有一个快照、发布 2 天：还没到 D3，判「缺快照」或「等数据」，不会误判
    assert item["acceptance_result"]["overall"] in ("等数据", "缺快照")
    post_time = con.execute("SELECT post_time FROM posts WHERE post_id='c'").fetchone()[0]
    with con:  # 补一个第 3 天的快照：关注率 46/4066 = 1.13% 达标，播放 4066 没到 5000
        con.execute("INSERT INTO snapshots (platform, post_id, fetched_at, views, new_fans, shares, comments, finish_rate) VALUES ('channels','c',?,4066,46,141,23,13.89)",
                    (post_time + 3 * 86400000,))
    item = contents.with_acceptance(con, [c for c in contents.list_all(con) if c["id"] == cid])[0]
    assert item["acceptance_result"]["overall"] == "部分达成"
    assert "## 0–3 秒 · 给理由" in contents.scaffold(item, method.load_profile())


def test_rule_review_and_summary(con, tmp_path):
    make_workbook(tmp_path / "wb.xlsx")
    importer.import_workbook(con, str(tmp_path / "wb.xlsx"), "主号")
    review_id = review.create(con, None, False)
    text = db.rows(con, "SELECT content FROM reviews WHERE id=?", review_id)[0]["content"]
    for heading in ("## 0 定位检查", "## 1 数据校验", "## 2 五段漏斗", "## 3 单条判定", "## 4 四个判断", "## 5 结构-表现矩阵", "## 8 下一步创作计划"):
        assert heading in text
    assert "4.6 倍" in text and "双强型" in text and "仅自己可见" in text and "S5 转化没有数据" in text
    data = analytics.build(con)
    json.dumps(review.summarize(data), default=str)  # 要能原样发给大模型
    assert "没有作品数据" in review.rule_report(analytics.build(con, 0.0001))


def test_ai_requires_config(con):
    with pytest.raises(RuntimeError, match="还没有配置大模型"):
        review.chat([{"role": "user", "content": "hi"}], dict(config.DEFAULT_SETTINGS))


def test_comment_rules_and_drafts(con):
    comments.ensure_default_rule(con)
    rule = comments.list_rules(con)[0]
    assert rule["is_intent"] and rule["keywords"] == "skill"
    profile = method.load_profile()
    text = comments.render("{收到|好嘞}，{asset}在{path}{nick}", "小王", profile, random.Random(1))
    assert "{" not in text and "@小王" in text and "待确认" not in text
    with con:
        con.execute("INSERT INTO posts (platform, post_id, account, title) VALUES ('channels', 'p1', '主号', '作品')")
        con.execute("INSERT INTO posts (platform, post_id, account, title) VALUES ('xhs', 'n1', '主号', '笔记')")
    rule_id = comments.save_rule(con, {**rule, "auto_send": True, "templates": rule["templates"]})
    assert comments.import_text(con, "channels", "主号", "p1", "小王：Skill 求一份\n阿杰：第三步的组件在哪里下载？\n做得真好") == 3
    assert comments.import_text(con, "xhs", "主号", "n1", "小美：skill") == 1
    drafted, queued = comments.process(con)
    states = {c["text"]: c for c in db.rows(con, "SELECT * FROM comments")}
    assert states["Skill 求一份"]["state"] == "queued" and states["Skill 求一份"]["rule_id"] == rule_id
    assert states["skill"]["state"] == "draft"  # 小红书永远只出草稿
    assert states["做得真好"]["state"] == "new" and not states["做得真好"]["is_question"]
    assert states["第三步的组件在哪里下载？"]["is_question"] and states["第三步的组件在哪里下载？"]["state"] == "new"
    # 「求 skill」是领取意向，不进选题需求；真正的提问才进
    assert [q[0] for q in planner.top_questions(con)] == ["第三步的组件在哪里下载？"]
    # 领取意向进入漏斗 S5
    posts = {p["post_id"]: p for w in analytics.build(con)["works"] for p in w["posts"]}
    assert posts["p1"]["intent"] == 1 and posts["p1"]["comments_tracked"]
    with pytest.raises(ValueError):
        comments.save_rule(con, {"name": "空", "keywords": " ", "templates": ["x"]})


def test_dig_finds_comments_in_unknown_shapes():
    payload = {"data": {"comments": [{"cid": "1", "text": "求 skill", "user": {"nickname": "小王"}, "aweme_id": "a1", "create_time": 1763000000},
                                     {"commentId": "2", "commentContent": "怎么领", "commentNickname": "阿杰", "commentCreatetime": "1763000001"}],
                        "objectId": "o9"}}
    found = {c["comment_id"]: c for c in comments.dig(payload, [])}
    assert found["1"]["nick"] == "小王" and found["1"]["post_id"] == "a1" and found["1"]["ctime"] == 1763000000000
    assert found["2"]["text"] == "怎么领" and found["2"]["post_id"] == "o9"


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


@pytest.mark.parametrize("platform", ["douyin", "channels"])
def test_fetch_comments_and_auto_reply_on_fake_site(platform, fake_site, con, tmp_path):
    fake_platforms.REPLIES.clear()
    f = state_file(tmp_path, True)
    items = comments.fetch(platform, "主号", log=lambda m: None, state_file=f)
    expected = {"douyin": {"dc1", "dc2"}, "channels": {"cc1"}}[platform]
    assert {c["comment_id"] for c in items} == expected
    assert comments.upsert(con, platform, "主号", items) == len(expected)
    comments.ensure_default_rule(con)
    rule = comments.list_rules(con)[0]
    comments.save_rule(con, {**rule, "auto_send": True})
    drafted, queued = comments.process(con)
    assert queued == 1  # 只有带 skill 的那条命中规则
    todo = db.rows(con, "SELECT * FROM comments WHERE state='queued'")
    results = comments.send(platform, "主号", todo, log=lambda m: None, state_file=f)
    assert results == [(todo[0]["comment_id"], True, "")]
    assert fake_platforms.REPLIES == [{"id": todo[0]["comment_id"], "text": todo[0]["reply"]}]
    comments.mark_sent(con, platform, results)
    assert db.rows(con, "SELECT state FROM comments WHERE comment_id=?", todo[0]["comment_id"])[0]["state"] == "replied"
    # 找不到的评论：失败并退回草稿，不会误发到别的评论下
    bad = [dict(todo[0], comment_id="zz", text="这条评论不存在")]
    assert comments.send(platform, "主号", bad, log=lambda m: None, state_file=f)[0][1] is False
    assert len(fake_platforms.REPLIES) == 1


def test_fetch_comments_requires_login(fake_site, tmp_path):
    with pytest.raises(Exception):
        comments.fetch("douyin", "主号", log=lambda m: None, state_file=state_file(tmp_path, False))
    with pytest.raises(RuntimeError, match="手动粘贴"):
        comments.fetch("xhs", "主号")
