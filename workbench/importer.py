"""导入《三平台内容数据工作台.xlsx》：把手工整理的「01 底表」、「04 内容结构标签」和「08 流量结构与长尾」读进数据库。

这样在自动采集跑起来之前抄录的历史数据也能进复盘；之后自动采集的数据会追加新的快照，不会覆盖。
"""
import datetime
from difflib import SequenceMatcher

from . import analytics, config, contents, method
from .db import rows

PLATFORM_NAMES = {"视频号": "channels", "shipinhao": "channels", "channels": "channels", "抖音": "douyin", "douyin": "douyin",
                  "小红书": "xhs", "xhs": "xhs", "xiaohongshu": "xhs"}
# 底表的来源列 -> 工作台里的来源名
SOURCE_COLS = {"推荐": "推荐", "朋友": "朋友♡", "分享来": "分享", "订阅号消息": "订阅号消息", "主页来": "主页", "其他": "其他"}
FLAG_WORDS = ("仅自己可见", "已删除", "不可采信", "私密")


def ms(v):
    if isinstance(v, datetime.datetime):
        return int(v.timestamp() * 1000)
    if isinstance(v, str) and v.strip():
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
            try:
                return int(datetime.datetime.strptime(v.strip(), fmt).timestamp() * 1000)
            except ValueError:
                pass
    return None


def num(v):
    if v in (None, "", "—", "-"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def find_table(ws, *headers):
    """找到同时包含这些表头的那一行，返回 (行号, {表头: 列号})。"""
    for r in range(1, min(ws.max_row, 40) + 1):
        cols = {str(c.value).strip(): c.column for c in ws[r] if c.value is not None}
        if all(h in cols for h in headers):
            return r, cols
    return None, {}


def similar(short, full):
    a, b = analytics.norm_title(short), analytics.norm_title(full)
    if not a or not b:
        return 0
    inside = sum(1 for ch in a if ch in b) / len(a)
    return max(inside, SequenceMatcher(None, a, b).ratio())


def best_match(title, candidates):
    scored = sorted(((similar(title, c["title"]), c) for c in candidates), key=lambda x: -x[0])
    return scored[0][1] if scored and scored[0][0] >= 0.6 else None


def import_workbook(con, path, account=""):
    try:
        import openpyxl
    except ImportError as e:
        raise ValueError("缺少 openpyxl：请重新运行「安装.cmd」") from e
    from pathlib import Path
    if not path or not Path(path).is_file():
        raise ValueError(f"找不到这个文件：{path or '（没填）'}。请填完整路径（例如 D:\\数据\\工作台.xlsx），或点「选择文件」")
    wb = openpyxl.load_workbook(path, data_only=True)
    base = next((ws for ws in wb.worksheets if "底表" in ws.title), None)
    if base is None:
        raise ValueError("表格里没有找到「01 底表」这张子表")
    head, cols = find_table(base, "平台", "作品ID", "发布时间")
    if not head:
        raise ValueError("「01 底表」里没有找到「平台 / 作品ID / 发布时间」这几列")
    get = lambda r, name: base.cell(r, cols[name]).value if name in cols else None
    account = account or method.load_profile().get("account") or "主号"
    imported = []
    with con:
        for r in range(head + 1, base.max_row + 1):
            platform = PLATFORM_NAMES.get(str(get(r, "平台") or "").strip())
            pid = str(get(r, "作品ID") or "").strip()
            if not platform or not pid:
                continue
            title = str(get(r, "标题/文案（原文）") or get(r, "标题") or "").strip()
            post_time, fetched = ms(get(r, "发布时间")), ms(get(r, "采集时点"))
            if not post_time or not fetched:
                continue
            finish = num(get(r, "完播率"))
            con.execute("INSERT OR IGNORE INTO accounts (platform, account, status) VALUES (?,?,?)", (platform, account, "unknown"))
            con.execute("INSERT OR REPLACE INTO posts (platform, post_id, account, kind, title, post_time, duration_sec, tags) VALUES (?,?,?,?,?,?,?,?)",
                        (platform, pid, account, "video", title, post_time, num(get(r, "时长(秒)")), ""))
            con.execute("""INSERT OR REPLACE INTO snapshots (platform, post_id, fetched_at, impressions, views, likes, collects, comments, shares,
                           new_fans, avg_view_sec, finish_rate, profile_visits) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (platform, pid, fetched, num(get(r, "曝光")), num(get(r, "播放/阅读")), num(get(r, "点赞")), num(get(r, "收藏")),
                         num(get(r, "评论")), num(get(r, "分享")), num(get(r, "关注/涨粉")), num(get(r, "平均播放时长(秒)")),
                         None if finish is None else finish * 100 if finish <= 1 else finish, num(get(r, "主页访问"))))
            src = {name: num(get(r, col)) for col, name in SOURCE_COLS.items() if num(get(r, col))}
            total = sum(src.values())
            if total:
                con.execute("DELETE FROM traffic_source WHERE platform=? AND post_id=?", (platform, pid))
                con.executemany("INSERT INTO traffic_source VALUES (?,?,?,?)", [(platform, pid, k, v / total * 100) for k, v in src.items()])
            note = str(get(r, "备注") or "")
            flag = next((w for w in FLAG_WORDS if w in note), None)
            if flag:
                con.execute("INSERT OR REPLACE INTO post_flags VALUES (?,?,?,?)", (platform, pid, flag, note[:200]))
            imported.append({"platform": platform, "post_id": pid, "title": title, "post_time": post_time,
                             "tags": {"archetype": get(r, "内容原型"), "hook_open": get(r, "开头钩"), "hook_end": get(r, "结尾钩")}})
    if not imported:
        raise ValueError("「01 底表」里没有可导入的行（需要平台、作品ID、发布时间、采集时点）")
    tag_sheet = next((ws for ws in wb.worksheets if "标签" in ws.title), None)
    if tag_sheet is not None:
        thead, tcols = find_table(tag_sheet, "作品", "母题", "原型")
        names = {"母题": "motif", "原型": "archetype", "开头钩": "hook_open", "中段留人": "hook_mid", "结尾钩": "hook_end",
                 "时长档": "duration_bin", "出镜": "presence"}
        for r in range(thead + 1, tag_sheet.max_row + 1) if thead else []:
            short = tag_sheet.cell(r, tcols["作品"]).value
            if not short:
                break
            hit = best_match(str(short), imported)
            if hit:
                for label, field in names.items():
                    if label in tcols and tag_sheet.cell(r, tcols[label]).value:
                        hit["tags"][field] = str(tag_sheet.cell(r, tcols[label]).value).strip()
    tail = next((ws for ws in wb.worksheets if "长尾" in ws.title), None)
    if tail is not None:
        dhead, dcols = find_table(tail, "作品", "首日播放 D0")
        with con:
            for r in range(dhead + 1, tail.max_row + 1) if dhead else []:
                short = tail.cell(r, dcols["作品"]).value
                if not short:
                    break
                hit = best_match(str(short), imported)
                d0 = num(tail.cell(r, dcols["首日播放 D0"]).value)
                d1 = num(tail.cell(r, dcols.get("次日播放 D1", 0)).value) if dcols.get("次日播放 D1") else None
                if hit and d0 is not None:
                    start = hit["post_time"]
                    con.execute("DELETE FROM trend WHERE platform=? AND post_id=? AND grain='day' AND metric='views'", (hit["platform"], hit["post_id"]))
                    con.executemany("INSERT INTO trend VALUES (?,?,?,?,?,?)",
                                    [(hit["platform"], hit["post_id"], "day", "views", start + i * 86400000, v) for i, v in enumerate([d0, d1]) if v is not None])
    tagged = 0
    for it in imported:
        tags = {k: v for k, v in it["tags"].items() if v and v in method.TAG_FIELDS[k][1]}
        if tags:
            contents.backfill(con, it["platform"], it["post_id"], tags)
            tagged += 1
    return {"posts": len(imported), "tagged": tagged, "account": account,
            "flagged": sum(1 for it in imported if rows(con, "SELECT 1 FROM post_flags WHERE platform=? AND post_id=?", it["platform"], it["post_id"]))}
