"""假的创作者后台：页面像真实后台一样自己发请求加载数据，用来测试采集流程。

接口路径和真实后台一致，返回的内容来自示例数据。没带登录 cookie 时只给一个登录页。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PAGE = "<!doctype html><meta charset=utf-8><body><div id=app></div><script>%s</script>"
GET = "const get = (u) => fetch(u).then((r) => r.json());"
PAGES = {
    "/statistics/data-analysis": GET + "get('/api/galaxy/creator/datacenter/note/analyze/list?type=0&page_size=10&page_num=1');",
    "/statistics/note-detail": GET + """const id = new URLSearchParams(location.search).get('noteId');
        for (const p of ['base', 'analyze/audience/trend', 'audience/source', 'audience/source/detail'])
          get('/api/galaxy/creator/datacenter/note/' + p + '?note_id=' + id);""",
    "/creator-micro/data-center/content": GET + "get('/web/api/creator/item/list?count=10');",
    "/creator-micro/work-management/work-detail": GET + """const id = location.pathname.split('/').pop();
        const api = '/janus/douyin/creator/';
        get(api + 'data/item/summarize?item_ids=' + id);
        const tabs = {'流量分析': ['pc/realtime/analysis/data_center', 'data/item/play/source', 'data/item_analysis/metrics_trend?metrics=view_count&time_unit=2'],
                      '观众分析': ['data/fans/item/portrait', 'data/item_analysis/metrics_trend?metrics=subscribe_count&time_unit=1']};
        for (const [name, urls] of Object.entries(tabs)) {
          const b = document.createElement('div'); b.textContent = name;
          b.onclick = () => urls.forEach((u) => get(api + u + (u.includes('?') ? '&' : '?') + 'item_id=' + id));
          document.body.append(b);
        }""",
    "/platform/statistic/post": """const b = document.createElement('div'); b.textContent = '单篇视频';
        b.onclick = () => fetch('/micro/statistic/cgi-bin/mmfinderassistant-bin/statistic/post_list?_rid=abc123', {method: 'POST',
          headers: {'Content-Type': 'application/json', 'X-WECHAT-UIN': '42'},
          body: JSON.stringify({pageSize: 10, currentPage: 1, reqScene: 7, scene: 7, timestamp: '1'})});
        setTimeout(() => document.body.append(b), 100);""",
}


def make_handler(raw, account):
    def load(platform, name):
        return json.loads((raw / platform / account / f"{name}.json").read_text(encoding="utf-8"))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body, kind="application/json"):
            data = (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def logged_in(self):
            return "session=ok" in (self.headers.get("Cookie") or "")

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            page = next((js for prefix, js in PAGES.items() if url.path.startswith(prefix)), None)
            if page is not None:
                return self.reply(PAGE % (page if self.logged_in() else "document.body.textContent = '请扫码登录'"), "text/html")
            if not self.logged_in():
                return self.reply({"code": -100, "msg": "登录已过期"})
            if "/datacenter/note/" in url.path:
                if url.path.endswith("analyze/list"):
                    return self.reply({"data": {"note_infos": load("xhs", "_list")["notes"]}})
                key = url.path.split("/datacenter/note/")[1].replace("/", "_")
                return self.reply(load("xhs", q["note_id"][0])[key])
            if url.path.endswith("creator/item/list"):
                return self.reply({"items": load("douyin", "_list")["items"]})
            if "/janus/douyin/creator/" in url.path:
                detail = load("douyin", (q.get("item_id") or q.get("item_ids"))[0])
                if "metrics_trend" in url.path:
                    return self.reply(detail[f"trend_{q['metrics'][0]}_{q['time_unit'][0]}"])
                return self.reply(detail["_".join(url.path.strip("/").split("/")[-2:])])
            self.reply({})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            name = urlparse(self.path).path.rsplit("/", 1)[1]
            # 真实后台会校验页面带的头和参数；这里要求采集器原样带回来
            if not self.logged_in() or self.headers.get("X-WECHAT-UIN") != "42" or "_rid=abc123" not in self.path or body.get("reqScene") != 7:
                return self.reply({"errCode": 300334, "errMsg": "not login"})
            if name == "post_list":
                return self.reply({"errCode": 0, "data": {"list": load("channels", "_list")["list"]}})
            detail = load("channels", body["feedId"])
            key = {"get_feed_lost_rate": "lost_rate", "feed_aggreagate_data_by_tab_type": "by_source"}[name]
            self.reply({"errCode": 0, "data": detail[key]})

    return Handler


def start(port, raw, account):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(raw, account))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
