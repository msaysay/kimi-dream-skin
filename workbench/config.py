"""路径、平台定义和本地设置。"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("WORKBENCH_DATA") or ROOT / "data")
# 发布引擎 social-auto-upload 的目录，由 安装.cmd 下载到这里
SAU_DIR = Path(os.environ.get("WORKBENCH_SAU") or ROOT / "vendor" / "social-auto-upload")
WEB = ROOT / "web"

# key 是工作台内部用的平台名；sau 是 social-auto-upload 里的平台名
PLATFORMS = {
    "douyin": {"name": "抖音", "sau": "douyin", "note": True, "draft": False},
    "xhs": {"name": "小红书", "sau": "xiaohongshu", "note": True, "draft": False},
    "channels": {"name": "视频号", "sau": "tencent", "note": False, "draft": True},
}

DEMO_ACCOUNT = "示例账号"

DEFAULT_SETTINGS = {
    "port": 8765,
    "headless": True,        # 采集时不显示浏览器窗口
    "chrome_path": "",       # 留空用自带的 Chromium
    "auto_collect": True,    # 工作台开着时每天自动采集一次
    "collect_hour": 9,
    "max_posts": 30,         # 每个账号每次最多抓多少条作品的详情
    # AI 待办由谁处理：auto 自动找本机的 Claude Code / Codex / CodeBuddy；也可以指定其中一个、custom 用自定义命令、off 只等助手来领
    "ai_runner": "auto",
    "ai_runner_cmd": "",
    "ai_base_url": "https://api.moonshot.cn/v1",
    "ai_api_key": "",
    "ai_model": "",
    "hot_boards": "weibo,douyin,baidu,bilibili",  # 热点雷达开着的热榜
    "hot_auto": True,        # 工作台开着时每天刷新一次热榜
    "verify_publish": True,  # 发布后回读作品列表，确认真的发出去了
    "cadence_days": 4,       # 一个平台超过几天没发就提醒
    "xhs_manual": True,      # 小红书不自动提交：工作台备好素材和文案，你自己发（自动提交容易被判违规）
    "xhs_cover_ratio": "3:4",  # 小红书封面比例：3:4 / 1:1 / 4:3 / 9:16
    # 对标账号（TikHub）
    "tikhub_api_key": "",
    "tikhub_base_url": "https://api.tikhub.io",  # 国内网络可以换成 https://api.tikhub.dev
    "bench_auto": True,      # 工作台开着时每天刷新一次对标账号
    "bench_pages": 1,        # 每个账号每次读几页作品（每页一次计费）
    "bench_hit_ratio": 3,    # 互动量达到该账号中位数的几倍算爆款
}


def ensure_dirs():
    for d in (DATA, DATA / "raw", DATA / "logs"):
        d.mkdir(parents=True, exist_ok=True)


def load_settings():
    f = DATA / "settings.json"
    s = dict(DEFAULT_SETTINGS)
    if f.exists():
        try:
            s.update(json.loads(f.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    return s


def save_settings(patch):
    ensure_dirs()
    s = load_settings()
    for k, v in patch.items():
        if k not in DEFAULT_SETTINGS:
            continue
        kind = type(DEFAULT_SETTINGS[k])
        s[k] = bool(v) if kind is bool else int(v) if kind is int else str(v).strip()
    (DATA / "settings.json").write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    return s


def public_settings():
    """给前端看的设置：不回传密钥本身。"""
    s = load_settings()
    s["ai_api_key_set"] = bool(s.pop("ai_api_key"))
    s["tikhub_api_key_set"] = bool(s.pop("tikhub_api_key"))
    return s


def cookie_file(platform, account):
    """social-auto-upload 保存登录态的位置（Playwright storage_state）。"""
    return SAU_DIR / "cookies" / f"{PLATFORMS[platform]['sau']}_{account}.json"


def sau_installed():
    return (SAU_DIR / "sau_cli.py").exists()
