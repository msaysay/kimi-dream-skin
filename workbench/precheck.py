"""发布前检查：把会被平台拦下、会限流、或者会泄露信息的问题，在点「发布」之前拦住。

block 必须改了才能发；warn 只是提醒，确认后可以照发。泄露检查参考了 ZJU-REAL/Easel（Apache-2.0）的 content_guard。
"""
import json
import re
import time
from pathlib import Path

from . import config
from .db import rows

# 平台规则：标题上限、简介上限、话题数上限。发布引擎会把抖音视频标题截到 30 字；视频号的短标题由引擎从标题里截 7–15 字
LIMITS = {
    "douyin": {"title": 30, "note_title": 20, "desc": 1000, "tags": 5},
    "xhs": {"title": 20, "note_title": 20, "desc": 1000, "tags": 10},
    "channels": {"title": 60, "desc": 1000, "tags": 10},
}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".flv"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

# 导流：微信号、手机号、外链、二维码。三个平台都会因此限流，结尾只写站内动作（评论区打关键词、去主页置顶领）
DIVERT = [
    (r"(?i)(微信|薇信|威信|v信|vx|wx|weixin|wechat|➕v|加v|\+v)\s*(号|id)?\s*[:：]?\s*[a-z][a-z0-9_-]{5,19}", "微信号", "block"),
    (r"(?i)(加我?|添加)\s*(微信|薇信|v信|vx|wx)", "引导加微信", "block"),
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "手机号", "block"),
    (r"(?i)https?://\S+|www\.[a-z0-9-]+\.[a-z]{2,}", "外部链接", "block"),
    (r"(?i)(扫码加|加群|二维码|关注公众号|qq群|q群|企微|淘宝搜|拼多多搜)", "站外引导", "warn"),
]
# 泄露：密钥、令牌、本机路径、内网地址
SECRETS = [
    (r"\bsk-[A-Za-z0-9_-]{16,}", "API 密钥"),
    (r"\b(ghp|gho|github_pat)_[A-Za-z0-9_]{20,}", "GitHub 令牌"),
    (r"\bAKIA[0-9A-Z]{16}\b", "云服务密钥"),
    (r"(?i)\b(api[_-]?key|secret|token|password|passwd|密钥|密码)\s*[:=：]\s*\S{8,}", "密钥或密码"),
    (r"(?i)bearer\s+[a-z0-9._-]{20,}", "访问令牌"),
]
PATHS = [
    (r"(?i)\b[a-z]:\\(users|documents and settings)\\[^\s\\]+", "本机路径"),
    (r"/(home|Users)/[A-Za-z0-9._-]+/", "本机路径"),
    (r"\b(127\.0\.0\.1|localhost|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)(:\d+)?\b", "内网地址"),
]
# 绝对化用语：广告法和平台审核都盯这些词
ABSOLUTE = r"(全网第一|全网最|史上最|世界第一|国家级|100%\s*(有效|成功|赚)|稳赚|包赚|保证赚|永久有效|绝对有效|零风险)"


def split_tags(tags):
    return [t.strip().lstrip("#") for t in re.split(r"[,，、\s]+", tags or "") if t.strip().lstrip("#")]


def check(con, body):
    """body 是发布表单。返回 [{level, platform, msg}]，block 在前。"""
    issues = []
    add = lambda level, msg, platform=None: issues.append({"level": level, "platform": platform, "msg": msg})
    kind = body.get("kind") or "video"
    targets = [t for t in body.get("targets") or [] if t.get("platform") in config.PLATFORMS]
    files = [f.strip().strip('"') for f in body.get("files") or [] if f and f.strip()]
    desc, tags = body.get("desc") or "", split_tags(body.get("tags"))
    titles = {t["platform"]: (t.get("title") or body.get("title") or "").strip() for t in targets}
    text = "\n".join([body.get("title") or "", desc, " ".join(tags), *titles.values()])

    if not targets:
        add("block", "还没有勾选要发布的账号")
    if not files:
        add("block", "还没有选择要发布的文件")
    for f in files:
        ext = Path(f).suffix.lower()
        if not Path(f).is_file():
            add("block", f"找不到文件：{f}")
        elif kind == "video" and ext not in VIDEO_EXT:
            add("block", f"这不像视频文件：{Path(f).name}。图片请把内容类型切到「图文」")
        elif kind == "note" and ext not in IMAGE_EXT:
            add("block", f"图文只能发图片：{Path(f).name}")
    if kind == "video" and len(files) > 1:
        add("warn", f"选了 {len(files)} 个文件，视频只会发第一个")

    for label, found in [(n, re.search(p, text)) for p, n in SECRETS]:
        if found:
            add("block", f"文案里有疑似{label}：「{found.group(0)[:12]}…」。发出去就收不回来了，请删掉")
    for label, found in [(n, re.search(p, text)) for p, n in PATHS]:
        if found:
            add("warn", f"文案里有{label}：「{found.group(0)[:30]}」，确认是不是从别处粘过来的")
    for label, level, found in [(n, lv, re.search(p, text)) for p, n, lv in DIVERT]:
        if found:
            add(level, f"文案里有{label}：「{found.group(0)[:20]}」。平台会限流甚至下架，结尾只写站内动作（评论区打关键词、去主页置顶领）")
    m = re.search(ABSOLUTE, text)
    if m:
        add("warn", f"有绝对化用语「{m.group(0)}」，容易被审核拦下，建议换个说法")

    for t in targets:
        pf, name, title, L = t["platform"], config.PLATFORMS[t["platform"]]["name"], titles[t["platform"]], LIMITS[t["platform"]]
        if not title:
            add("block", "标题不能为空", pf)
            continue
        cap = L.get("note_title", L["title"]) if kind == "note" else L["title"]
        if len(title) > cap:
            if pf == "xhs" or kind == "note":
                add("block", f"{name}标题最多 {cap} 字，现在 {len(title)} 字。可以在右边给这个平台单独填一个短标题", pf)
            elif pf == "douyin":
                add("warn", f"抖音标题最多 {cap} 字，现在 {len(title)} 字，超出的部分会被截掉：「…{title[cap - 6:cap]}」后面的看不到", pf)
        if pf == "channels" and len(title) < 6:
            add("warn", "视频号的短标题要 6–16 字，现在太短，引擎会自动补齐，建议自己写一个", pf)
        if len(desc) > L["desc"]:
            add("block" if pf == "xhs" else "warn", f"{name}简介最多 {L['desc']} 字，现在 {len(desc)} 字", pf)
        if len(tags) > L["tags"]:
            add("warn", f"{name}建议最多 {L['tags']} 个话题，现在 {len(tags)} 个，多了会分散推荐", pf)
        if kind == "note" and not config.PLATFORMS[pf]["note"]:
            add("block", f"{name}不支持图文发布", pf)
        # 同一平台 7 天内发过同样的标题：多半是重复点了发布
        for j in rows(con, "SELECT params FROM jobs WHERE type='publish' AND status IN ('done','pending','running') AND created_at>=?",
                      int(time.time() * 1000) - 7 * 86400000):
            old = json.loads(j["params"])
            if (old.get("platform"), old.get("account"), old.get("title")) == (pf, t.get("account"), title):
                add("warn", f"{name}「{t.get('account')}」最近 7 天已经发过同样的标题，确认不是重复发布", pf)
                break

    if not tags:
        add("warn", "没有填话题。话题是平台判断内容分类的线索，建议写 3–5 个")
    if kind == "video" and not (body.get("cover") or "").strip() and body.get("cover_sec") in (None, ""):
        add("warn", "没有设封面，平台会自己挑一帧。封面决定点击率，建议在上面选一张图")
    if not body.get("content_id"):
        add("warn", "没有关联选题：发出去之后不会自动验收，打法库也学不到这一条。可以从「创作」里的选题点「去发布」")
    sched = body.get("schedule") or ""
    if sched:
        try:
            ts = time.mktime(time.strptime(sched.replace("T", " ")[:16], "%Y-%m-%d %H:%M"))
            if ts < time.time() + 5 * 60:
                add("block", "定时发布的时间已经过了（或不到 5 分钟），请改一个晚一点的时间，或者清空改成立即发布")
        except ValueError:
            add("block", "定时发布的时间格式不对")
    return sorted(issues, key=lambda i: i["level"] != "block")
