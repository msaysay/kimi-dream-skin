"""把 data/raw/ 里各平台的原始数据整理进数据库。

每次采集给每条作品追加一行指标快照（用来看增长）；留存、流量来源、观众画像、趋势只保留最新一份。
比率一律存成百分数（10 表示 10%）。字段对应关系改编自 JaelChen/video-data-dashboard（MIT）。
"""
import json
import time

from . import config
from .db import SNAP_COLS

DOUYIN_SOURCE = {"homepage_hot": "推荐", "search": "搜索", "homepage": "个人主页", "message": "私信消息", "familiar": "朋友",
                 "yumme_vv_all": "抖音精选", "follow": "关注", "other": "其他"}
DOUYIN_GENDER = {"female": "女性", "male": "男性"}
CHANNELS_SOURCE = {3: "关注", 4: "推荐", 6: "分享", 20: "主页", 8: "朋友♡", 16: "订阅号消息", 15: "PC微信", 25: "看一看", 0: "其他"}


def num(v, scale=1):
    # 平台对还没出数据的指标返回 -1 或空
    if v is None or v == "" or v == -1:
        return None
    try:
        return float(v) * scale
    except (TypeError, ValueError):
        return None


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def data(d, key):
    return ((d.get(key) or {}).get("data")) or {}


def put_post(con, platform, account, pid, kind, title, post_time, duration, tags, snap, fetched_at,
             retention=(), benchmark=(), sources=(), audience=None, trends=()):
    con.execute("INSERT OR REPLACE INTO posts (platform, post_id, account, kind, title, post_time, duration_sec, tags) VALUES (?,?,?,?,?,?,?,?)",
                (platform, pid, account, kind, title, post_time, duration, tags))
    con.execute(f"INSERT OR REPLACE INTO snapshots VALUES (?,?,?,{','.join('?' * len(SNAP_COLS))})",
                (platform, pid, fetched_at, *[snap.get(c) for c in SNAP_COLS]))
    # 这次没抓到的部分（页面偶尔没加载完）保留上次的，不清空
    for table, got in (("retention", retention), ("traffic_source", sources), ("audience", audience), ("trend", trends)):
        if got:
            con.execute(f"DELETE FROM {table} WHERE platform=? AND post_id=?", (platform, pid))
    con.executemany("INSERT OR REPLACE INTO retention VALUES (?,?,?,?,?)",
                    [(platform, pid, "self", s, v) for s, v in retention] + [(platform, pid, "benchmark", s, v) for s, v in benchmark])
    con.executemany("INSERT OR REPLACE INTO traffic_source VALUES (?,?,?,?)", [(platform, pid, s, v) for s, v in sources if v is not None])
    for dim, items in (audience or {}).items():
        con.executemany("INSERT OR REPLACE INTO audience VALUES (?,?,?,?,?)", [(platform, pid, dim, l, v) for l, v in items if v is not None])
    con.executemany("INSERT OR REPLACE INTO trend VALUES (?,?,?,?,?,?)", [(platform, pid, *t) for t in trends])


def at(ret, sec):
    return next((v for s, v in ret if s == sec), None)


def xhs_post(con, account, n, d, fetched_at):
    b = data(d, "base")
    g = lambda k: num(b.get(k))
    is_video = n.get("type") == 2
    ret = [(x["date"], x["count"]) for x in data(d, "analyze_audience_trend").get("trend_list") or []] if is_video else []
    det = data(d, "audience_source_detail")
    info = b.get("note_info") or {}
    put_post(con, "xhs", account, n["id"], "video" if is_video else "note", n.get("title") or "", n.get("post_time"),
             ret[-1][0] if ret else None, ",".join(t.get("name", "") for t in info.get("tags") or []),
             {"impressions": g("impl_count"), "views": g("view_count"), "cover_click_rate": g("cover_click_rate"), "likes": g("like_count"),
              "comments": g("comment_count"), "collects": g("collect_count"), "shares": g("share_count"), "new_fans": g("rise_fans_count"),
              "avg_view_sec": g("view_time_avg_with_double"), "finish_rate": g("full_view_rate"), "finish5s_rate": g("finish5s_rate"),
              "exit2s_rate": g("exit_view2s_rate")},
             fetched_at, retention=ret,
             sources=[(s.get("title"), num(s.get("value_with_double"))) for s in data(d, "audience_source").get("source") or []],
             audience={dim: [(x.get("title"), num(x.get("value"))) for x in det.get(dim) or []] for dim in ("gender", "age", "city", "interest")},
             trends=[("hour", "views", x["date"], float(x.get("count_with_double", x.get("count") or 0)))
                     for x in (b.get("hour") or {}).get("view_list") or []])


def mmss(key):
    m, s = key.split(":")
    return int(m) * 60 + int(s)


def to_ms(text):
    return int(time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S")) * 1000)


def douyin_post(con, account, it, d, fetched_at):
    s = ((d.get("item_summarize") or {}).get("item_list") or [{}])[0]
    st, sd = s.get("statistics") or {}, s.get("summarize_data") or {}
    an = (d.get("analysis_data_center") or {}).get("analysis_trend") or {}
    ret = [(mmss(x["key"]), x["value"] * 100) for x in an.get("current_item") or []]
    bench = [(mmss(x["key"]), x["value"] * 100) for x in an.get("similar_author") or []]
    portrait = d.get("item_portrait") or {}
    ratio = lambda k: (portrait.get(k) or {}).get("ratio_list") or []
    top = lambda items, n=8: sorted(items, key=lambda x: -x[1])[:n]
    subs = (((d.get("trend_subscribe_count_1") or {}).get("trend_map") or {}).get("subscribe_count") or {}).get("0") or []
    hours = (((d.get("trend_view_count_2") or {}).get("trend_map") or {}).get("view_count") or {}).get("0") or []
    r2, r5 = at(ret, 2), at(ret, 5)
    video = it.get("video_info") or {}
    click = sd.get("cover_click_ratio")
    put_post(con, "douyin", account, it["id"], "video" if video else "note", it.get("description") or "", int(it.get("create_time") or 0) * 1000,
             num(video.get("duration"), 0.001), ",".join(c.get("cha_name", "") for c in s.get("cha_list") or []),
             {"views": num(st.get("play_count")),
              # 新作品偶尔返回大于 1 的点击率（数据未就绪），当作没有
              "cover_click_rate": num(click, 100) if (click or 0) <= 1 else None, "likes": num(st.get("digg_count")),
              "comments": num(st.get("comment_count")), "collects": num(st.get("collect_count")), "shares": num(st.get("share_count")),
              "new_fans": num(sd.get("new_fans_count")), "avg_view_sec": num(sd.get("play_avg_time")),
              "finish_rate": num(sd.get("play_finish_ratio"), 100), "profile_visits": num(sd.get("home_page_view_count")),
              # 后台的 5s 完播率 / 2s 跳出率就是留存曲线上第 5 秒、第 2 秒的值
              "finish5s_rate": r5, "exit2s_rate": None if r2 is None else 100 - r2},
             fetched_at, retention=ret, benchmark=bench,
             sources=[(DOUYIN_SOURCE.get(x["key"], x["key"]), x["value"] * 100) for x in (d.get("play_source") or {}).get("play_source") or []],
             audience={"gender": [(DOUYIN_GENDER.get(x["key"], x["key"]), x["value"] * 100) for x in ratio("gender")],
                       "age": [(x["key"], x["value"] * 100) for x in ratio("age")],
                       "city": top([(x["key"], x["value"] * 100) for x in ratio("province")])},
             trends=[("hour", "views", to_ms(x["date_time"]), float(x["value"])) for x in hours]
                    + [("day", "new_fans", to_ms(x["date_time"]), float(x["value"])) for x in subs])


def channels_post(con, account, it, d, fetched_at):
    ret = [(x["sec"], x["rate"] * 100) for x in (d.get("lost_rate") or {}).get("items") or []]
    fd = ((d.get("by_source") or {}).get("feedData") or [{}])[0]
    src = [(CHANNELS_SOURCE.get(x.get("tabType"), str(x.get("tabType"))), sum(map(float, (x.get("data") or {}).get("browse") or [])))
           for x in fd.get("dataByTabtype") or []]
    total = sum(v for _, v in src) or None
    daily = (fd.get("totalData") or {}).get("browse") or []
    start = d.get("by_source_start")
    desc = it.get("desc") or {}
    media = desc.get("media") or [{}]
    put_post(con, "channels", account, it["objectId"], "video", desc.get("description") or "", int(it.get("createTime") or 0) * 1000,
             num(media[0].get("videoPlayLen")), "",
             {"views": num(it.get("readCount")), "likes": (num(it.get("likeCount")) or 0) + (num(it.get("favCount")) or 0),
              "comments": num(it.get("commentCount")), "shares": num(it.get("forwardCount")), "new_fans": num(it.get("followCount")),
              "avg_view_sec": num(it.get("avgPlayTimeSec")), "finish_rate": num(it.get("fullPlayRate"), 100),
              # 视频号的「快速划走率」放在 2s 跳出率这一栏，界面上会标注口径不同
              "exit2s_rate": num(it.get("fastFlipRate"), 100)},
             fetched_at, retention=ret,
             sources=[(k, v / total * 100) for k, v in src] if total else [],
             trends=[("day", "views", (start + i * 86400) * 1000, float(v)) for i, v in enumerate(daily)] if start else [])


# 平台 -> (列表里的字段名, 作品 id 的字段名, 单条处理函数)
PARSERS = {"xhs": ("notes", "id", xhs_post), "douyin": ("items", "id", douyin_post), "channels": ("list", "objectId", channels_post)}


def ingest(con, platform, account, log=print):
    """读入一个账号最近一次采集的原始数据，返回成功入库的作品数。"""
    raw = config.DATA / "raw" / platform / account
    if not (raw / "_list.json").exists():
        return 0
    lst = load(raw / "_list.json")
    list_key, id_key, parse = PARSERS[platform]
    count = 0
    for item in lst.get(list_key) or []:
        pid = item.get(id_key)
        detail_file = raw / f"{pid}.json"
        try:
            detail = load(detail_file) if detail_file.exists() else {}
            with con:
                parse(con, account, item, detail, lst["fetchedAt"])
            count += 1
        except Exception as e:  # 一条作品的数据格式变了，不影响其他作品
            log(f"跳过 {pid}：数据格式和预期不一致（{type(e).__name__}: {e}）")
    return count
