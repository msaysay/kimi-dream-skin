"""假的 TikHub：返回结构照着 2026-11 真实接口的返回裁剪（字段名、嵌套层级一致，数字是编的）。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

KEY = "test-key"
CALLS = []
# 每个账号 8 条作品：互动大致 1,000 左右，第 3 条是 6 倍的爆款，第 1 条是置顶的老爆款（不进中位数也不算爆款）
BASE = [1000, 900, 6000, 1100, 950, 1050, 980, 1020]
NOW = 1794900000


def douyin_aweme(i, inter):
    return {"aweme_id": f"dy{i}", "desc": f"抖音作品{i} #AI", "create_time": NOW - i * 86400, "duration": 30000, "is_top": int(i == 0),
            "statistics": {"digg_count": inter - 100, "comment_count": 40, "share_count": 30, "collect_count": 30, "play_count": 0},
            "video": {"cover": {"url_list": [f"https://img/dy{i}.jpg"]}}}


def xhs_note(i, inter):
    return {"id": f"{i:024x}", "display_title": f"小红书笔记{i}", "type": "video", "create_time": NOW - i * 86400, "sticky": i == 0,
            "likes": inter - 100, "comments_count": 30, "collected_count": 50, "share_count": 20, "view_count": 0, "cursor": f"c{i}",
            "images_list": [{"url": f"https://img/x{i}.jpg"}]}


def channels_obj(i, inter):
    return {"id": 15056857243102874000 + i, "createtime": NOW - i * 86400, "nickname": "对标视频号", "stickyTime": 0,
            "likeCount": inter - 100, "commentCount": 20, "forwardCount": 40, "favCount": 40, "readCount": 0,
            "objectDesc": {"description": f"视频号作品{i}", "media": [{"videoPlayLen": 61, "coverUrl": f"https://img/c{i}.jpg", "mediaType": 4}]}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, data, code=200):
        body = json.dumps({"code": code, "message": "ok", "data": data} if code == 200 else {"code": code, "message": "bad"}).encode()
        self.send_response(200 if code == 200 else code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def route(self, body=None):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        CALLS.append((url.path, q or body))
        if self.headers.get("Authorization") != "Bearer " + KEY:
            return self.reply(None, 401)
        p = url.path
        if p == "/api/v1/douyin/web/handler_user_profile":
            return self.reply({"user": {"nickname": "对标抖音号", "follower_count": 120000, "sec_uid": q["sec_user_id"], "avatar_thumb": {"url_list": ["https://img/a.jpg"]}}})
        if p == "/api/v1/douyin/web/get_sec_user_id":
            return self.reply("MS4wLjABAAAAshortlink")
        if p == "/api/v1/douyin/web/fetch_user_post_videos":
            return self.reply({"aweme_list": [douyin_aweme(i, v) for i, v in enumerate(BASE)], "max_cursor": 123, "has_more": 0})
        if p == "/api/v1/xiaohongshu/app_v2/get_user_info":
            return self.reply({"data": {"userid": q.get("user_id") or "0" * 24, "nickname": "对标小红书号", "interactions": [{"type": "fans", "count": 5000}]}})
        if p == "/api/v1/xiaohongshu/app_v2/get_user_posted_notes":
            return self.reply({"data": {"notes": [xhs_note(i, v) for i, v in enumerate(BASE)], "has_more": False}})
        if p == "/api/v1/wechat_channels/v2/fetch_user_videos":
            return self.reply({"object": [channels_obj(i, v) for i, v in enumerate(BASE)], "lastBuffer": "", "continueFlag": 0})
        if p == "/api/v1/wechat_channels/v2/fetch_channel_id_to_username":
            return self.reply({"username": "v2_fromsph@finder"})
        if p == "/api/v1/wechat_search/v2/fetch_search_videos":
            return self.reply({"results": {"data": [{"items": [{"title": "<em class=\"highlight\">对标</em>视频号", "authInfo": "某公司",
                                                                 "jumpInfo": {"userName": "v2_searched@finder"}}]}]}})
        self.reply(None, 404)

    def do_GET(self):
        self.route()

    def do_POST(self):
        self.route(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}"))


def start(port):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
