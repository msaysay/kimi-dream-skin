"""生成虚构的示例数据，格式和真实采集到的原始数据一致，用来预览界面和做测试。"""
import json
import math
import random
import shutil
import time

from . import config, db, ingest

WORKS = [
    # 标题, 时长(秒), 几天前发布, 开头吸引力, 中段留存, 互动意愿, 涨粉能力, 发布平台
    ("三个动作改善圆肩驼背，每天五分钟", 48, 2, 1.25, 1.1, 1.2, 1.3, ("douyin", "xhs", "channels")),
    ("一周备餐：五十块钱吃七天", 95, 5, 0.7, 1.0, 1.1, 0.9, ("douyin", "xhs", "channels")),
    ("新手买相机最容易踩的四个坑", 132, 8, 1.0, 0.65, 0.9, 1.0, ("douyin", "xhs")),
    ("出租屋改造：两百块换一面墙", 61, 11, 1.1, 1.05, 0.55, 0.8, ("douyin", "xhs", "channels")),
    ("我为什么不再推荐这款网红锅", 77, 14, 1.4, 1.2, 1.5, 1.1, ("douyin", "xhs", "channels")),
    ("周末一个人去爬山的完整路线", 154, 18, 0.9, 0.8, 1.0, 0.6, ("douyin", "channels")),
    ("打工人午休十分钟拉伸跟练", 600, 21, 1.0, 0.9, 1.0, 1.2, ("douyin", "xhs", "channels")),
    ("租房合同里一定要看的三行字", 52, 25, 1.15, 1.0, 1.3, 1.4, ("douyin", "xhs", "channels")),
    ("用手机拍出干净画面的布光方法", 88, 28, 0.85, 1.1, 0.8, 1.0, ("xhs", "channels")),
]
# 各平台的体量和基础水平不同
BASE = {"douyin": {"views": 18000, "hook": 0.62, "finish": 0.24, "click": 0.11},
        "xhs": {"views": 5200, "hook": 0.70, "finish": 0.30, "click": 0.14},
        "channels": {"views": 2600, "hook": 0.66, "finish": 0.27, "click": None}}
TAGS = ["生活记录", "干货分享", "新手必看"]


def curve(duration, hook, mid):
    """每秒留存（0-1）：开头两秒快速掉到 hook，之后按 mid 决定的速度缓慢下降。"""
    out = []
    for t in range(0, int(duration) + 1):
        early = 1 - (1 - hook) * min(t, 2) / 2
        late = math.exp(-max(t - 2, 0) / (duration * 0.55 * mid))
        out.append(max(0.02, early * late))
    return out


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def clock(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def make_raw(now=None, growth=1.0, account=config.DEMO_ACCOUNT, root=None, fetched=None):
    """写出三个平台的原始数据。fetched 早于 now、growth<1 用来模拟前几天采到的数据，形成增长曲线。"""
    now = now or time.time()
    root = root or config.DATA / "raw"
    fetched = fetched or now
    fetched_at = int(fetched * 1000)
    lists = {"xhs": [], "douyin": [], "channels": []}
    for i, (title, duration, days_ago, hook_k, mid_k, react_k, fans_k, platforms) in enumerate(WORKS):
        for platform in platforms:
            rng = random.Random(f"{i}-{platform}")
            base = BASE[platform]
            post_ts = int(now - days_ago * 86400 - rng.randint(1, 6) * 3600)
            if post_ts > fetched:
                continue  # 这次快照时还没发布
            age = min(1.0, ((fetched - post_ts) / 86400 + 0.5) / 4)  # 刚发的作品数据还没跑完
            views = int(base["views"] * hook_k * mid_k * rng.uniform(0.75, 1.35) * age * growth)
            hook = min(0.95, base["hook"] * hook_k * rng.uniform(0.95, 1.05))
            ret = curve(duration, hook, mid_k * rng.uniform(0.9, 1.1))
            finish = min(0.9, ret[-1] * 1.6)
            likes = int(views * 0.045 * react_k * rng.uniform(0.8, 1.2))
            comments, collects, shares = int(likes * 0.12), int(likes * 0.35), int(likes * 0.15)
            fans = int(views * 0.004 * fans_k * rng.uniform(0.8, 1.2))
            avg_sec = sum(ret) / len(ret) * duration
            click = base["click"] and base["click"] * hook_k * rng.uniform(0.9, 1.1)
            pid = f"demo-{platform}-{i}"
            text = f"{title} " + " ".join("#" + t for t in TAGS[: 2 + i % 2])
            daily = [int(views * w) for w in (0.5, 0.25, 0.12, 0.08, 0.05)][: days_ago + 1]
            if platform == "xhs":
                lists["xhs"].append({"id": pid, "title": title, "post_time": post_ts * 1000, "type": 2})
                write(root / "xhs" / account / f"{pid}.json", {
                    "fetchedAt": fetched_at,
                    "base": {"data": {"impl_count": int(views / click), "view_count": views, "cover_click_rate": round(click * 100, 1),
                                      "like_count": likes, "comment_count": comments, "collect_count": int(collects * 2.2), "share_count": shares,
                                      "rise_fans_count": fans, "view_time_avg_with_double": round(avg_sec, 1),
                                      "full_view_rate": round(finish * 100, 1), "finish5s_rate": round(ret[min(5, duration)] * 100, 1),
                                      "exit_view2s_rate": round((1 - ret[2]) * 100, 1),
                                      "note_info": {"tags": [{"name": t} for t in TAGS[: 2 + i % 2]]},
                                      "hour": {"view_list": [{"date": (post_ts + h * 3600) * 1000, "count": int(views * 0.5 * math.exp(-h / 6) / 6)} for h in range(24)]}}},
                    "analyze_audience_trend": {"data": {"trend_list": [{"date": t, "count": round(v * 100, 1)} for t, v in enumerate(ret)]}},
                    "audience_source": {"data": {"source": [{"title": k, "value_with_double": v} for k, v in
                                                            (("首页推荐", 58.0), ("搜索", 24.5), ("个人主页", 9.5), ("关注页", 5.0), ("其他", 3.0))]}},
                    "audience_source_detail": {"data": {
                        "gender": [{"title": "女性", "value": 72}, {"title": "男性", "value": 28}],
                        "age": [{"title": k, "value": v} for k, v in (("18-24", 31), ("25-34", 44), ("35-44", 18), ("45+", 7))],
                        "city": [{"title": k, "value": v} for k, v in (("上海", 12), ("北京", 10), ("广州", 8), ("杭州", 7), ("成都", 6))],
                        "interest": [{"title": k, "value": v} for k, v in (("美食", 22), ("家居", 18), ("健身", 15))]}},
                })
            elif platform == "douyin":
                lists["douyin"].append({"id": pid, "description": text, "create_time": post_ts, "video_info": {"duration": duration * 1000}})
                step = max(1, duration // 60)
                points = lambda vals: [{"key": f"{t // 60:02d}:{t % 60:02d}", "value": round(vals[t], 4)} for t in range(0, duration + 1, step)]
                write(root / "douyin" / account / f"{pid}.json", {
                    "fetchedAt": fetched_at,
                    "item_summarize": {"item_list": [{
                        "statistics": {"play_count": views, "digg_count": likes, "comment_count": comments, "collect_count": collects, "share_count": shares},
                        "summarize_data": {"cover_click_ratio": round(click, 4), "new_fans_count": fans, "play_avg_time": round(avg_sec, 1),
                                           "play_finish_ratio": round(finish, 4), "home_page_view_count": int(fans * 6)},
                        "cha_list": [{"cha_name": t} for t in TAGS[: 2 + i % 2]]}]},
                    "analysis_data_center": {"analysis_trend": {"current_item": points(ret), "similar_author": points(curve(duration, base["hook"], 1.0))}},
                    "play_source": {"play_source": [{"key": k, "value": v} for k, v in
                                                    (("homepage_hot", 0.71), ("search", 0.09), ("follow", 0.08), ("homepage", 0.07), ("other", 0.05))]},
                    "item_portrait": {"gender": {"ratio_list": [{"key": "female", "value": 0.55}, {"key": "male", "value": 0.45}]},
                                      "age": {"ratio_list": [{"key": k, "value": v} for k, v in (("18-23", 0.22), ("24-30", 0.38), ("31-40", 0.27), ("41+", 0.13))]},
                                      "province": {"ratio_list": [{"key": k, "value": v} for k, v in (("广东", 0.14), ("江苏", 0.09), ("浙江", 0.08), ("山东", 0.07))]}},
                    "trend_view_count_2": {"trend_map": {"view_count": {"0": [{"date_time": clock(post_ts + h * 3600), "value": int(views * 0.5 * math.exp(-h / 5) / 5)} for h in range(24)]}}},
                    "trend_subscribe_count_1": {"trend_map": {"subscribe_count": {"0": [{"date_time": clock(post_ts + d * 86400), "value": int(fans * w)} for d, w in enumerate((0.5, 0.25, 0.12, 0.08, 0.05)[: days_ago + 1])]}}},
                })
            else:
                lists["channels"].append({"objectId": pid, "desc": {"description": text, "media": [{"videoPlayLen": duration}]}, "createTime": post_ts,
                                          "readCount": views, "likeCount": likes, "favCount": int(likes * 0.4), "commentCount": comments,
                                          "forwardCount": int(shares * 2.5), "followCount": fans, "avgPlayTimeSec": round(avg_sec, 1),
                                          "fullPlayRate": round(finish, 4), "fastFlipRate": round(1 - ret[2], 4)})
                start = post_ts - (post_ts + 8 * 3600) % 86400
                secs = sorted({0, 3, max(4, duration // 4), duration // 2, duration * 3 // 4, duration})
                write(root / "channels" / account / f"{pid}.json", {
                    "fetchedAt": fetched_at, "by_source_start": start,
                    "lost_rate": {"items": [{"sec": t, "rate": round(ret[t], 4)} for t in secs]},
                    "by_source": {"feedData": [{"totalData": {"browse": [str(v) for v in daily]},
                                                "dataByTabtype": [{"tabType": k, "data": {"browse": [str(int(v * w)) for v in daily]}} for k, w in
                                                                  ((4, 0.46), (8, 0.27), (3, 0.14), (6, 0.09), (20, 0.04))]}]},
                })
    write(root / "xhs" / account / "_list.json", {"fetchedAt": fetched_at, "notes": lists["xhs"]})
    write(root / "douyin" / account / "_list.json", {"fetchedAt": fetched_at, "items": lists["douyin"]})
    write(root / "channels" / account / "_list.json", {"fetchedAt": fetched_at, "list": lists["channels"]})


def load_demo(con):
    """写入三天的示例快照（数据量逐天增长）。"""
    clear_demo(con)
    now = time.time()
    for days_back, growth in ((2, 0.62), (1, 0.85), (0, 1.0)):
        make_raw(now, growth, fetched=now - days_back * 86400)
        for platform in config.PLATFORMS:
            ingest.ingest(con, platform, config.DEMO_ACCOUNT)
    with con:
        for platform in config.PLATFORMS:
            con.execute("INSERT OR REPLACE INTO accounts (platform, account, status, last_collect) VALUES (?,?,?,?)",
                        (platform, config.DEMO_ACCOUNT, "demo", int(now * 1000)))


def clear_demo(con):
    with con:
        for platform in config.PLATFORMS:
            db.delete_account_data(con, platform, config.DEMO_ACCOUNT)
            shutil.rmtree(config.DATA / "raw" / platform / config.DEMO_ACCOUNT, ignore_errors=True)
        con.execute("DELETE FROM reviews WHERE kind='demo'")
