"""TikHub 接口：读取对标账号的公开作品。

只调用三类接口：解析账号、读账号资料、读作品列表。每次调用都会计费，记在 api_calls 表里，设置页能看到花了多少。
接口路径和字段按 2026-11 TikHub 的真实返回核对过（见 tests/fake_tikhub.py 里的样例结构）。
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

# 每次调用的估算价格（美元）。抖音、视频号来自 TikHub 文档 / 计价接口；小红书接口没返回价格，按经验估
PRICES = {"douyin": 0.001, "xhs": 0.002, "channels": 0.01}
TIMEOUT = 45  # 视频号接口慢，文档要求至少 30 秒，超时会扣费却拿不到结果


class TikHubError(Exception):
    pass


def settings():
    s = config.load_settings()
    if not s.get("tikhub_api_key"):
        raise TikHubError("还没有配置 TikHub：请在「设置 → 对标账号」里填 API Key")
    return s


def log_call(path, ok, price):
    from . import db
    con = db.connect()
    with con:
        con.execute("INSERT INTO api_calls (ts, path, ok, price) VALUES (?,?,?,?)", (int(time.time() * 1000), path, int(ok), price))
    con.close()


def call(path, params=None, body=None, price=0.001):
    s = settings()
    url = s["tikhub_base_url"].rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"Authorization": "Bearer " + s["tikhub_api_key"], "Content-Type": "application/json",
                                          "User-Agent": "NingLoop"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            reply = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        log_call(path, False, 0)
        text = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 403):
            raise TikHubError(f"TikHub 拒绝了请求（{e.code}）：API Key 不对，或这个 Key 没有该接口的权限") from e
        if e.code == 402:
            raise TikHubError("TikHub 余额不足，请先充值") from e
        raise TikHubError(f"TikHub 返回 {e.code}：{text}") from e
    except urllib.error.URLError as e:
        log_call(path, False, 0)
        raise TikHubError(f"连不上 TikHub：{e.reason}。国内网络可以在设置里换成 api.tikhub.dev") from e
    ok = reply.get("code") == 200
    log_call(path, ok, price if ok else 0)
    if not ok:
        raise TikHubError(f"TikHub 返回 {reply.get('code')}：{reply.get('message_zh') or reply.get('message') or ''}"[:300])
    return reply.get("data")


# ---------- 解析账号 ----------
def ms(seconds):
    try:
        return int(float(seconds)) * 1000 if seconds else None
    except (TypeError, ValueError):
        return None


def count(v):
    """平台对拿不到的数返回 0；只有确实大于 0 才算数，否则当作没有。"""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def resolve(platform, text):
    """把用户填的链接 / ID / 名称解析成账号。返回候选列表 [{uid, name, followers, avatar, note}]，通常只有一个。"""
    text = (text or "").strip()
    if not text:
        raise TikHubError("请填账号主页链接或 ID")
    if platform == "douyin":
        m = re.search(r"douyin\.com/user/([\w-]+)", text) or re.match(r"^(MS4w[\w-]+)$", text)
        if m:
            sec = m.group(1)
        elif re.search(r"https?://\S+", text):  # 分享短链
            sec = call("/api/v1/douyin/web/get_sec_user_id", {"url": re.search(r"https?://\S+", text).group(0)}, price=PRICES["douyin"])
            sec = sec if isinstance(sec, str) else (sec or {}).get("sec_user_id")
            if not sec:
                raise TikHubError("没能从这个链接解析出抖音账号")
        else:
            raise TikHubError("抖音请填主页链接（douyin.com/user/…）或分享短链")
        u = (call("/api/v1/douyin/web/handler_user_profile", {"sec_user_id": sec}, price=PRICES["douyin"]) or {}).get("user") or {}
        return [{"uid": sec, "name": u.get("nickname") or sec[:12], "followers": u.get("follower_count"),
                 "avatar": ((u.get("avatar_thumb") or {}).get("url_list") or [None])[0], "note": u.get("signature") or ""}]
    if platform == "xhs":
        m = re.search(r"/user/profile/([0-9a-f]{24})", text) or re.match(r"^([0-9a-f]{24})$", text)
        params = {"user_id": m.group(1)} if m else {"share_text": text}
        data = call("/api/v1/xiaohongshu/app_v2/get_user_info", params, price=PRICES["xhs"]) or {}
        u = data.get("data", data) if isinstance(data, dict) else {}
        uid = u.get("userid") or u.get("user_id") or u.get("id") or (m.group(1) if m else None)
        if not uid:
            raise TikHubError("没能解析出小红书账号：请填主页链接（xiaohongshu.com/user/profile/…）")
        fans = next((i.get("count") for i in u.get("interactions") or [] if i.get("type") == "fans"), None)
        return [{"uid": uid, "name": u.get("nickname") or uid[:8], "followers": fans,
                 "avatar": u.get("imageb") or u.get("images"), "note": u.get("desc") or ""}]
    if platform == "channels":
        if text.endswith("@finder"):
            return [{"uid": text, "name": text[:16] + "…", "followers": None, "avatar": None, "note": ""}]
        if re.match(r"^sph\w+$", text):
            data = call("/api/v1/wechat_channels/v2/fetch_channel_id_to_username", body={"channel_id": text}, price=PRICES["channels"]) or {}
            name = data.get("username") or data.get("finder_username") or next(iter(re.findall(r"v2_[\w-]+@finder", json.dumps(data))), None)
            if not name:
                raise TikHubError("没能把这个视频号 ID 换成账号")
            return [{"uid": name, "name": data.get("nickname") or text, "followers": None, "avatar": None, "note": ""}]
        # 按名称搜索：返回搜到的账号让用户挑
        data = call("/api/v1/wechat_search/v2/fetch_search_videos", body={"keyword": text}, price=PRICES["channels"]) or {}
        found = {}

        def walk(o):
            if isinstance(o, dict):
                user = (o.get("jumpInfo") or {}).get("userName") or (o.get("noticeParam") or {}).get("finderUsername")
                if user and user.endswith("@finder") and user not in found:
                    found[user] = {"uid": user, "name": re.sub(r"<[^>]+>", "", o.get("title") or "") or user[:16], "followers": None,
                                   "avatar": o.get("thumbUrl"), "note": o.get("authInfo") or o.get("desc") or ""}
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(data)
        if not found:
            raise TikHubError("按名称没搜到视频号账号：可以改填视频号 ID（sph 开头，在对方主页「…」里能看到）")
        return list(found.values())[:8]
    raise TikHubError("不认识的平台")


# ---------- 读作品 ----------
def norm_douyin(a):
    st = a.get("statistics") or {}
    cover = ((a.get("video") or {}).get("cover") or {}).get("url_list") or [None]
    return {"post_id": str(a.get("aweme_id")), "title": a.get("desc") or "", "url": f"https://www.douyin.com/video/{a.get('aweme_id')}",
            "cover": cover[0], "post_time": ms(a.get("create_time")), "duration_sec": (a.get("duration") or 0) / 1000 or None,
            "kind": "note" if a.get("images") else "video", "is_top": int(bool(a.get("is_top"))),
            "likes": count(st.get("digg_count")), "comments": count(st.get("comment_count")), "shares": count(st.get("share_count")),
            "collects": count(st.get("collect_count")), "plays": count(st.get("play_count"))}


def norm_xhs(n):
    imgs = n.get("images_list") or [{}]
    return {"post_id": str(n.get("id") or n.get("note_id")), "title": n.get("display_title") or n.get("title") or (n.get("desc") or "")[:60],
            "url": f"https://www.xiaohongshu.com/explore/{n.get('id') or n.get('note_id')}", "cover": imgs[0].get("url"),
            "post_time": ms(n.get("create_time")), "duration_sec": None, "kind": "video" if n.get("type") == "video" else "note",
            "is_top": int(bool(n.get("sticky"))), "likes": count(n.get("likes")), "comments": count(n.get("comments_count")),
            "shares": count(n.get("share_count")), "collects": count(n.get("collected_count")), "plays": count(n.get("view_count"))}


def norm_channels(o):
    desc = o.get("objectDesc") or {}
    media = (desc.get("media") or [{}])[0]
    return {"post_id": str(o.get("id")), "title": desc.get("description") or "", "url": None, "cover": media.get("coverUrl") or media.get("thumbUrl"),
            "post_time": ms(o.get("createtime")), "duration_sec": media.get("videoPlayLen"), "kind": "video" if media.get("mediaType") == 4 else "note",
            "is_top": int(bool(o.get("stickyTime"))), "likes": count(o.get("likeCount")), "comments": count(o.get("commentCount")),
            "shares": count(o.get("forwardCount")), "collects": count(o.get("favCount")), "plays": count(o.get("readCount")),
            "author": o.get("nickname")}


def fetch_posts(platform, uid, pages=1):
    """读一个账号最近的作品，返回规范化后的列表。pages 控制翻几页（每页一次计费）。"""
    out, cursor = [], None
    for _ in range(max(1, pages)):
        if platform == "douyin":
            data = call("/api/v1/douyin/web/fetch_user_post_videos", {"sec_user_id": uid, "max_cursor": cursor or 0, "count": 20}, price=PRICES["douyin"]) or {}
            out += [norm_douyin(a) for a in data.get("aweme_list") or []]
            cursor, more = data.get("max_cursor"), data.get("has_more")
        elif platform == "xhs":
            data = call("/api/v1/xiaohongshu/app_v2/get_user_posted_notes", {"user_id": uid, "cursor": cursor}, price=PRICES["xhs"]) or {}
            inner = data.get("data", data)
            notes = inner.get("notes") or []
            out += [norm_xhs(n) for n in notes]
            cursor, more = (notes[-1].get("cursor") if notes else None), inner.get("has_more")
        elif platform == "channels":
            data = call("/api/v1/wechat_channels/v2/fetch_user_videos", body={"username": uid, "last_buffer": cursor or ""}, price=PRICES["channels"]) or {}
            out += [norm_channels(o) for o in data.get("object") or []]
            cursor, more = data.get("lastBuffer") or data.get("last_buffer"), data.get("continueFlag")
        else:
            raise TikHubError("不认识的平台")
        if not more or not cursor:
            break
    return [p for p in out if p["post_id"] and p["post_id"] != "None"]
