"""用已登录的浏览器打开三个平台的创作者后台，把后台页面自己加载的数据存到 data/raw/。

只读取你自己账号的数据，不发帖、不修改任何东西。登录态复用 social-auto-upload 扫码后保存的文件，
所以每个平台只需要扫一次码。接口路径和抓取顺序参考了 JaelChen/video-data-dashboard（MIT）。

这些接口没有公开文档，平台改版后这里可能需要跟着调整。
"""
import json
import os
import random
import re
import shutil
import time
from urllib.parse import parse_qs, urlparse

from . import config

URLS = {
    "xhs": os.environ.get("WORKBENCH_URL_XHS", "https://creator.xiaohongshu.com"),
    "douyin": os.environ.get("WORKBENCH_URL_DOUYIN", "https://creator.douyin.com"),
    "channels": os.environ.get("WORKBENCH_URL_CHANNELS", "https://channels.weixin.qq.com"),
}
# 测试时把等待时间缩短
WAIT = float(os.environ.get("WORKBENCH_WAIT", "1"))


class NotLoggedIn(Exception):
    pass


class Capture:
    """记下页面发出的请求和收到的响应，之后按网址取出 JSON。"""

    def __init__(self, page):
        self.page = page
        self.responses = []
        self.requests = []
        page.on("response", lambda r: self.responses.append(r))
        page.on("request", lambda r: self.requests.append(r))

    def clear(self):
        self.responses.clear()
        self.requests.clear()

    def wait(self, pattern, timeout_ms, what="responses"):
        deadline = time.time() + timeout_ms * WAIT / 1000
        while time.time() < deadline:
            if any(re.search(pattern, r.url) for r in getattr(self, what)):
                return True
            self.page.wait_for_timeout(250)
        return False

    def take(self, pattern):
        out = []
        for r in list(self.responses):
            if re.search(pattern, r.url):
                try:
                    out.append((r.url, r.json()))
                except Exception:
                    pass  # 响应体不是 JSON，或页面已经跳走
        return out


def pause(page, ms):
    page.wait_for_timeout(int(ms * WAIT))


def click_text(page, text, timeout_ms):
    """在页面和所有内嵌框架里找到文字并点击。"""
    deadline = time.time() + timeout_ms * WAIT / 1000
    while time.time() < deadline:
        for frame in page.frames:
            try:
                loc = frame.get_by_text(text, exact=True)
                if loc.count():
                    loc.first.click(timeout=2000)
                    return True
            except Exception:
                pass
        page.wait_for_timeout(400)
    return False


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def collect_xhs(page, cap, out, limit, log, details=True):
    base = URLS["xhs"]
    page.goto(base + "/statistics/data-analysis", wait_until="domcontentloaded")
    got = cap.wait(r"datacenter/note/analyze/list", 20000)
    pause(page, 2000)
    notes, seen = [], set()
    for _, j in cap.take(r"datacenter/note/analyze/list"):
        for n in (j.get("data") or {}).get("note_infos") or []:
            if n.get("id") and n["id"] not in seen:
                seen.add(n["id"])
                notes.append(n)
    if not got:
        raise NotLoggedIn("没拿到小红书笔记列表，登录可能已过期，请在「账号」页重新扫码登录")
    fetched_at = int(time.time() * 1000)
    notes = notes[:limit]
    write(out / "_list.json", {"fetchedAt": fetched_at, "notes": notes})
    for n in notes if details else []:
        cap.clear()
        page.goto(f"{base}/statistics/note-detail?noteId={n['id']}", wait_until="domcontentloaded")
        cap.wait(r"datacenter/note/base", 15000)
        pause(page, 3000)
        detail = {"fetchedAt": fetched_at}
        for url, j in cap.take(r"datacenter/note/(base|audience|analyze/audience)"):
            key = url.split("/datacenter/note/")[1].split("?")[0].strip("/").replace("/", "_")
            detail[key] = j
        write(out / f"{n['id']}.json", detail)
        log(f"小红书 {n.get('title') or n['id']}：{', '.join(k for k in detail if k != 'fetchedAt') or '没拿到详情'}")
        pause(page, random.randint(800, 2000))
    return len(notes)


def collect_douyin(page, cap, out, limit, log, details=True):
    base = URLS["douyin"]
    page.goto(base + "/creator-micro/data-center/content", wait_until="domcontentloaded")
    got = cap.wait(r"web/api/creator/item/list", 20000)
    pause(page, 2000)
    for _ in range(3 if details else 0):  # 往下滚几屏，让列表多加载几页
        page.mouse.wheel(0, 4000)
        pause(page, 1200)
    items, seen = [], set()
    for _, j in cap.take(r"web/api/creator/item/list"):
        for it in j.get("items") or []:
            if it.get("id") and it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
    if not got:
        raise NotLoggedIn("没拿到抖音作品列表，登录可能已过期，请在「账号」页重新扫码登录")
    fetched_at = int(time.time() * 1000)
    items = items[:limit]
    write(out / "_list.json", {"fetchedAt": fetched_at, "items": items})
    pattern = r"data/item/summarize|realtime/analysis/data_center|item/play/source|fans/item/portrait|item_analysis/metrics_trend"
    for it in items if details else []:
        cap.clear()
        page.goto(f"{base}/creator-micro/work-management/work-detail/{it['id']}?enter_from=item_data", wait_until="domcontentloaded")
        cap.wait(r"data/item/summarize", 15000)
        pause(page, 2500)
        for tab in ("流量分析", "观众分析"):
            if click_text(page, tab, 1500):
                pause(page, 2500)
        detail = {"fetchedAt": fetched_at}
        for url, j in cap.take(pattern):
            if "metrics_trend" in url:
                q = parse_qs(urlparse(url).query)
                key = f"trend_{(q.get('metrics') or [''])[0]}_{(q.get('time_unit') or [''])[0]}"
            else:
                key = "_".join([s for s in urlparse(url).path.split("/") if s][-2:])
            detail[key] = j
        write(out / f"{it['id']}.json", detail)
        log(f"抖音 {(it.get('description') or it['id'])[:20]}：{', '.join(k for k in detail if k != 'fetchedAt') or '没拿到详情'}")
        pause(page, random.randint(800, 2000))
    return len(items)


FETCH_JS = """async ({url, headers, body}) => {
  const r = await fetch(url, {method: 'POST', headers, body, credentials: 'include'});
  return await r.text();
}"""


def collect_channels(page, cap, out, limit, log, details=True):
    base = URLS["channels"]
    page.goto(base + "/platform/statistic/post", wait_until="domcontentloaded")
    if not click_text(page, "单篇视频", 25000):
        raise NotLoggedIn("视频号助手没有登录或登录已过期，请在「账号」页重新扫码登录")
    cap.wait(r"statistic/post_list", 15000, what="requests")
    req = next((r for r in reversed(cap.requests) if "statistic/post_list" in r.url and r.post_data), None)
    if req is None:
        raise NotLoggedIn("没拿到视频号作品列表请求，请在「账号」页重新扫码登录后再试")
    template = json.loads(req.post_data)
    headers = {k: v for k, v in req.headers.items() if re.match(r"(x-|finger|content-type)", k, re.I)}
    api = req.url.split("statistic/post_list")[0] + "statistic/"
    query = req.url.split("?", 1)[1] if "?" in req.url else ""
    frame = req.frame

    def call(name, body):
        payload = dict(template, **body, timestamp=str(int(time.time() * 1000)))
        text = frame.evaluate(FETCH_JS, {"url": f"{api}{name}?{query}", "headers": headers, "body": json.dumps(payload)})
        j = json.loads(text)
        if j.get("errCode") != 0:
            raise RuntimeError(f"{name} 返回 {j.get('errCode')} {j.get('errMsg')}")
        return j.get("data") or {}

    now = int(time.time())
    day0 = now - (now + 8 * 3600) % 86400  # 北京时间今天零点
    posts = (call("post_list", {"pageSize": 50, "currentPage": 1, "startTime": day0 - 30 * 86400, "endTime": day0 + 86400}).get("list") or [])[:limit]
    fetched_at = int(time.time() * 1000)
    write(out / "_list.json", {"fetchedAt": fetched_at, "list": posts})
    for p in posts if details else []:
        detail = {"fetchedAt": fetched_at}
        # 单条详情拿不到不影响列表里的完播率等指标，所以只记下错误
        try:
            detail["lost_rate"] = call("get_feed_lost_rate", {"feedId": p["objectId"]})
        except Exception as e:
            detail["lost_rate_err"] = str(e)
        try:
            start = p["createTime"] - (p["createTime"] + 8 * 3600) % 86400
            detail["by_source"] = call("feed_aggreagate_data_by_tab_type",
                                       {"feedId": p["objectId"], "startTs": str(start), "endTs": str(day0), "interval": 3})
            detail["by_source_start"] = start
        except Exception as e:
            detail["by_source_err"] = str(e)
        write(out / f"{p['objectId']}.json", detail)
        log(f"视频号 {((p.get('desc') or {}).get('description') or p['objectId'])[:20]}：{', '.join(k for k in detail if k != 'fetchedAt')}")
        pause(page, random.randint(500, 1200))
    return len(posts)


COLLECTORS = {"xhs": collect_xhs, "douyin": collect_douyin, "channels": collect_channels}


def sync_playwright():
    try:
        from patchright.sync_api import sync_playwright as sp
    except ImportError:
        from playwright.sync_api import sync_playwright as sp
    return sp()


def run(platform, account, log=print, state_file=None, details=True):
    """采集一个账号，原始数据写到 data/raw/<平台>/<账号>/，返回作品数。
    details=False 时只读作品列表（发布后核验用），写到 data/raw_list/ 下，不动上一次完整采集的数据。"""
    settings = config.load_settings()
    state_file = state_file or config.cookie_file(platform, account)
    if not state_file.exists():
        raise NotLoggedIn(f"还没有登录{config.PLATFORMS[platform]['name']}账号「{account}」，请先在「账号」页扫码登录")
    out = config.DATA / ("raw" if details else "raw_list") / platform / account
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    with sync_playwright() as pw:
        launch = {"headless": bool(settings["headless"])}
        if settings["chrome_path"]:
            launch["executable_path"] = settings["chrome_path"]
        if os.environ.get("WORKBENCH_CHROME"):
            launch["executable_path"] = os.environ["WORKBENCH_CHROME"]
        browser = pw.chromium.launch(**launch)
        try:
            context = browser.new_context(storage_state=str(state_file), viewport={"width": 1440, "height": 900})
            page = context.new_page()
            cap = Capture(page)
            count = COLLECTORS[platform](page, cap, out, int(settings["max_posts"]), log, details)
            # 采集成功说明登录有效，把刷新过的登录态存回去，延长有效期
            context.storage_state(path=str(state_file))
            return count
        finally:
            browser.close()


def list_posts(platform, account, log=print, state_file=None):
    """只读作品列表：返回 [{post_id, title, post_time(毫秒)}]，最新的在前。"""
    run(platform, account, log, state_file, details=False)
    lst = json.loads((config.DATA / "raw_list" / platform / account / "_list.json").read_text(encoding="utf-8"))
    out = []
    for it in lst.get({"xhs": "notes", "douyin": "items", "channels": "list"}[platform]) or []:
        if platform == "xhs":
            out.append({"post_id": it.get("id"), "title": it.get("title") or "", "post_time": it.get("post_time") or 0})
        elif platform == "douyin":
            out.append({"post_id": it.get("id"), "title": it.get("description") or "", "post_time": int(it.get("create_time") or 0) * 1000})
        else:
            out.append({"post_id": it.get("objectId"), "title": (it.get("desc") or {}).get("description") or "", "post_time": int(it.get("createTime") or 0) * 1000})
    return sorted([p for p in out if p["post_id"]], key=lambda p: -p["post_time"])
