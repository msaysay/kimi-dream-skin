"""封面：一张原图（或视频里的一帧）按各平台的封面规格分别生成，尺寸和比例都精确匹配。

为什么要这样做：发布引擎上传封面后，平台会弹「裁剪封面」的选区，引擎只会点确认，用的是平台默认选区。
原图比例和平台要求不一样时，默认选区裁到哪是不可控的。先把图裁成和规格一模一样的比例，默认选区就是整张图。

规格（比例来自发布引擎对应的平台页面：抖音「设置竖封面」、视频号「个人主页卡片 3:4」和「动态 4:3」；
小红书默认 3:4，可在设置里改）。裁剪位置由「焦点」决定：以焦点为中心取最大的那块，默认在画面中间偏上。
原图和规格差太多（比如竖屏画面要出 4:3 横封面）时，裁剪会丢掉大半画面，自动改成「完整画面 + 模糊背景」。
"""
import hashlib
import os
from pathlib import Path

from . import config

# 平台 -> [(发布引擎的参数名, 说明, 宽, 高)]
SPECS = {
    "douyin": [("portrait", "竖封面 3:4", 1080, 1440)],
    # 视频号发表页有两个封面位：竖的是个人主页作品列表里的缩略图，横的是视频号动态里显示的那张
    "channels": [("portrait", "个人主页卡片 3:4（主页缩略图）", 1080, 1440), ("landscape", "动态封面 4:3（横版）", 1440, 1080)],
    "xhs": [("thumbnail", "封面", 1080, 1440)],
}
XHS_RATIOS = {"3:4": (1080, 1440), "1:1": (1080, 1080), "4:3": (1440, 1080), "9:16": (1080, 1920)}
FLAGS = {"portrait": "--thumbnail-portrait", "landscape": "--thumbnail-landscape", "thumbnail": "--thumbnail"}
MODES = ("auto", "crop", "pad")
KEEP_MIN = 0.6  # 自动模式：裁剪后留下的画面少于这个比例，就改用模糊背景


class CoverError(Exception):
    pass


def specs(platform):
    if platform == "xhs":
        ratio = config.load_settings().get("xhs_cover_ratio") or "3:4"
        w, h = XHS_RATIOS.get(ratio, XHS_RATIOS["3:4"])
        return [("thumbnail", f"封面 {ratio if ratio in XHS_RATIOS else '3:4'}", w, h)]
    return SPECS[platform]


def libs():
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        raise CoverError("缺少图像处理组件（opencv）：请重新运行「安装.cmd」") from e
    return cv2, np


def read_image(path):
    """读图片。不用 cv2.imread：它在 Windows 上读不了带中文的路径。"""
    cv2, np = libs()
    p = Path(path)
    if not p.is_file():
        raise CoverError(f"找不到封面图片：{path}")
    img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise CoverError(f"这个文件不是能识别的图片：{path}")
    return img


def short_path(path):
    """Windows 上 OpenCV 打不开带中文的视频路径时，换成系统的短路径再试。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024):
            return buf.value
    except Exception:
        pass
    return None


def video_frame(path, sec=1.0):
    """从视频里取第 sec 秒的一帧。"""
    cv2, _ = libs()
    if not Path(path).is_file():
        raise CoverError(f"找不到视频文件：{path}")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened() and short_path(path):
        cap = cv2.VideoCapture(short_path(path))
    if not cap.isOpened():
        raise CoverError("打不开这个视频来截取封面。可以把视频放到不含中文的路径下，或者另外准备一张封面图")
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(sec)) * 1000)
        ok, frame = cap.read()
        if not ok:  # 超出时长就退回第一帧
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
        if not ok:
            raise CoverError("视频里读不出画面")
        return frame
    finally:
        cap.release()


def crop_box(iw, ih, w, h, focus):
    """以焦点为中心、比例为 w:h 的最大裁剪框，超出边界就贴边。返回 (x, y, 宽, 高)。"""
    scale = min(iw / w, ih / h)
    cw, ch = w * scale, h * scale
    fx, fy = min(1.0, max(0.0, focus[0])) * iw, min(1.0, max(0.0, focus[1])) * ih
    x = min(max(fx - cw / 2, 0), iw - cw)
    y = min(max(fy - ch / 2, 0), ih - ch)
    return int(round(x)), int(round(y)), int(round(cw)), int(round(ch))


def render(img, w, h, focus=(0.5, 0.4), mode="auto"):
    """生成一张 w×h 的封面。返回 (图像, 实际用的方式, 裁剪保留的画面比例)。"""
    cv2, _ = libs()
    ih, iw = img.shape[:2]
    x, y, cw, ch = crop_box(iw, ih, w, h, focus)
    keep = (cw * ch) / (iw * ih)
    used = mode if mode in ("crop", "pad") else ("crop" if keep >= KEEP_MIN else "pad")
    cropped = cv2.resize(img[y:y + ch, x:x + cw], (w, h), interpolation=cv2.INTER_AREA if cw > w else cv2.INTER_CUBIC)
    if used == "crop":
        return cropped, used, keep
    # 完整画面居中，背景用同一张图放大模糊
    k = max(w, h) // 8 * 2 + 1
    bg = cv2.GaussianBlur(cropped, (k, k), 0)
    bg = cv2.convertScaleAbs(bg, alpha=0.6, beta=0)
    s = min(w / iw, h / ih)
    fw, fh = max(1, int(round(iw * s))), max(1, int(round(ih * s)))
    fg = cv2.resize(img, (fw, fh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    ox, oy = (w - fw) // 2, (h - fh) // 2
    bg[oy:oy + fh, ox:ox + fw] = fg
    return bg, used, keep


def write_jpg(path, img):
    cv2, _ = libs()
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise CoverError("封面图保存失败")
    buf.tofile(str(path))


def build(image=None, video=None, sec=None, focus=(0.5, 0.4), mode="auto", platforms=None, landscape_image=None, landscape_focus=(0.5, 0.5)):
    """按规格生成封面文件。返回 {"dir", "source": {...}, "items": [{platform, slot, label, w, h, path, mode, keep}]}。

    image 优先；没有 image 就从 video 的第 sec 秒截一帧。生成后把文件读回来核对尺寸，不对就报错。
    landscape_image：单独给横版封面（视频号 4:3 那张）准备的图。给了就只用它出横版，竖版仍用 image。
    """
    mode = mode if mode in MODES else "auto"
    focus = (float(focus[0]), float(focus[1]))
    if image:
        img, origin = read_image(image), f"图片 {Path(image).name}"
        stamp = Path(image).stat().st_mtime_ns
    elif video and sec is not None:
        img, origin = video_frame(video, sec), f"视频第 {float(sec):g} 秒"
        stamp = Path(video).stat().st_mtime_ns
    else:
        raise CoverError("没有封面来源：请选一张封面图，或填从视频第几秒截取")
    wide = read_image(landscape_image) if landscape_image else None
    landscape_focus = (float(landscape_focus[0]), float(landscape_focus[1]))
    platforms = [p for p in (platforms or list(SPECS)) if p in SPECS]
    wanted = [(pf, *spec) for pf in platforms for spec in specs(pf)]
    stamp2 = Path(landscape_image).stat().st_mtime_ns if landscape_image else 0
    key = hashlib.md5(repr((str(image), str(video), sec, focus, mode, stamp, wanted, str(landscape_image), landscape_focus, stamp2)).encode()).hexdigest()[:12]
    out = config.DATA / "covers" / key
    out.mkdir(parents=True, exist_ok=True)
    ih, iw = img.shape[:2]
    cv2, _ = libs()
    preview = img if max(iw, ih) <= 900 else cv2.resize(img, (int(iw * 900 / max(iw, ih)), int(ih * 900 / max(iw, ih))), interpolation=cv2.INTER_AREA)
    write_jpg(out / "source.jpg", preview)
    source2 = None
    if wide is not None:
        wh, ww = wide.shape[:2]
        small = wide if max(ww, wh) <= 900 else cv2.resize(wide, (int(ww * 900 / max(ww, wh)), int(wh * 900 / max(ww, wh))), interpolation=cv2.INTER_AREA)
        write_jpg(out / "source2.jpg", small)
        source2 = {"w": ww, "h": wh, "name": Path(landscape_image).name}
    items, done = [], {}
    for pf, slot, label, w, h in wanted:
        if (w, h) not in done:
            own = wide is not None and w > h   # 横版封面有单独的图就用它
            result, used, keep = render(wide if own else img, w, h, landscape_focus if own else focus, mode)
            path = out / f"cover_{w}x{h}.jpg"
            write_jpg(path, result)
            check = read_image(path)
            if check.shape[1] != w or check.shape[0] != h:
                raise CoverError(f"生成的封面尺寸不对：要 {w}×{h}，实际 {check.shape[1]}×{check.shape[0]}")
            done[(w, h)] = (path, used, keep, own)
        path, used, keep, own = done[(w, h)]
        items.append({"platform": pf, "slot": slot, "label": label, "w": w, "h": h, "path": str(path), "mode": used, "keep": round(keep, 3),
                      "from": "横版原图" if own else "原图"})
    return {"dir": key, "origin": origin, "source": {"w": iw, "h": ih}, "focus": list(focus), "items": items,
            "source2": source2, "focus2": list(landscape_focus)}


def for_platform(result, platform):
    """某个平台发布时要带的封面：{参数名: 文件路径}。"""
    return {it["slot"]: it["path"] for it in result["items"] if it["platform"] == platform}
