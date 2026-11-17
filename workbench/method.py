"""复盘方法论：《宁少AI进化论 · 三平台内容数据分析方法论》里的字典、阈值、判定规则和决策表。

这里只放「规矩」，不放计算。所有标签都是封闭列表：交给 AI 执行时不允许自造同义词。
阈值和权重是起始假设，可在「设置 → 复盘参数」里改，改动存进 data/method.json。
"""
import json

from . import config

# ---------- 四层标签（封闭字典） ----------
MOTIFS = {  # L1 母题：按「观众看完能拿走什么」区分
    "AI 工具实测": "拿一个工具跑真任务，给出「能不能用、用在哪」的结论",
    "AI 制作流程拆解": "把一个成品的制作过程拆成有序步骤，交付可复用流程",
    "AI 行业人物与公司故事": "以人物或公司的一次关键转折为主线，串联行业变化",
    "AI 概念科普": "解释一个被误用的术语或机制，纠正既有认知",
    "AI 与职场商业应用": "把工具装进一个具体岗位的任务链，如周报、提案、剪辑",
    "个人实践与复盘": "自己的一次实验、失败与修正，带时间与数字",
}
ARCHETYPES = {  # L2 原型：叙述骨架
    "故事型": ("一个对象 → 一次冲突 → 一个转折 → 一个结果", "只有播放没有关注，结尾无承接"),
    "教程型": ("一个任务 → 步骤 1 至 5 → 交付物", "步骤超过五个，完播断崖；评论都在问某一步"),
    "清单型": ("一个量级数字 → 逐项陈列 → 收敛到 Top 3", "只罗列不排序，评论率上不去"),
    "演示型": ("结果先行 → 过程回演 → 前后对比", "无承接时两头都低；只有奇观没有方法"),
    "观点论证型": ("反常识主张 → 两个论据 → 结论或行动", "只有观点没有证据，评论变成吵架"),
    "对比型": ("A 与 B 同条件各跑一次 → 差异归因 → 选择建议", "条件不同却当对照，结论不成立"),
    "答疑型": ("一个真实提问 → 拆因 → 给方案", "问题太窄，播放天花板低"),
}
HOOKS_OPEN = {  # L3-a 开头钩（0–3 秒），对完播负责
    "反常识断言": "先否掉一个普遍做法，再说自己的判断，多为「不是……而是……」",
    "结果奇观": "把最强的画面或数字放在第一帧，不做铺垫",
    "身份代入": "点名一群人，让对方在前 3 秒确认「说的是我」",
    "悬念提问": "抛出一个当场没有答案的问题",
    "利益承诺": "明确说看完能得到什么，承诺必须兑现",
}
HOOKS_MID = {  # L3-b 中段留人，对完播与分享负责
    "步骤承诺": "先说一共几步，让人知道还剩多少",
    "信息密度": "每 5 至 8 秒一个可记的新信息点",
    "案例连爆": "连续给三个以上同类案例，用密度代替论证",
    "进度可视": "明确报进度，例如「第三步马上出结果」",
    "无结构": "靠素材本身推进，不做任何留人设计",
}
HOOKS_END = {  # L3-c 结尾钩（最后 3 秒），对关注率与私域负责
    "资产给予": "给可带走的东西：模板、组件、Skill、清单",
    "关注理由": "说明下一条为什么值得看，常与栏目化绑定",
    "行动指令": "指向一个具体动作：评论区关键词、置顶链接",
    "系列预告": "明确下一集内容与时间",
    "引导私域": "指向企微、群或主页，通常与资产给予叠加",
    "无": "不做任何收尾，停在内容本身",
}
DURATION_BINS = ["<30s", "30–60s", "60–120s", ">120s"]
PRESENCE = ["不露脸", "数字人", "真人"]
RHYTHM = ["快切", "中速", "慢叙"]
COVERS = ["大字", "人脸", "成果截图"]

TAG_FIELDS = {  # 内容字段 -> (中文名, 可选值)
    "motif": ("母题", list(MOTIFS)),
    "archetype": ("原型", list(ARCHETYPES)),
    "hook_open": ("开头钩", list(HOOKS_OPEN)),
    "hook_mid": ("中段留人", list(HOOKS_MID)),
    "hook_end": ("结尾钩", list(HOOKS_END)),
    "duration_bin": ("时长档", DURATION_BINS),
    "presence": ("出镜", PRESENCE),
    "rhythm": ("节奏", RHYTHM),
    "cover_type": ("封面", COVERS),
}

# 表 9 组合规则：原型 -> (推荐母题, 首选开头钩, 默认结尾钩, 不要搭配)
COMBOS = {
    "故事型": (["AI 行业人物与公司故事"], "悬念提问", "关注理由", "清单型式罗列，会截断情节"),
    "教程型": (["AI 制作流程拆解", "AI 与职场商业应用"], "反常识断言", "资产给予", "结果奇观开头，答案提前露底"),
    "清单型": (["AI 工具实测"], "结果奇观", "资产给予", "步骤承诺，会让人等一个不存在的流程"),
    "演示型": (["AI 工具实测"], "结果奇观", "行动指令", "长篇论证，与画面节奏冲突"),
    "观点论证型": (["AI 概念科普", "个人实践与复盘"], "反常识断言", "关注理由", "先给结论再给过程，论证被削弱"),
    "对比型": (["AI 工具实测"], "悬念提问", "行动指令", "条件不同的两个对象，结论不成立"),
    "答疑型": (["AI 概念科普", "AI 与职场商业应用"], "身份代入", "系列预告", "问题太窄却给长答案，完播必掉"),
}

# 表 10 脚本骨架：四段任务「给理由 / 给结构 / 给内容 / 给下一步」
SKELETONS = {
    "教程型": ("结果先给（成品画面）", "承诺步数与总时长", "步骤 1 至 5，每步一个画面切换", "交付物与领取方式"),
    "故事型": ("冲突一帧（最反转的那句话）", "交代对象与处境", "转折展开，每 20 秒一次认知升级", "结果，或下一集预告"),
    "清单型": ("量级数字（181 个、5 个）", "说清筛选标准", "逐项陈列，每项一句结论", "收敛 Top 3 与领取方式"),
    "观点论证型": ("反常识断言", "第一个论据带数字", "第二个论据与一个反例", "结论与为什么值得关注"),
    "演示型": ("结果奇观（最终效果）", "说明用了什么、花了多久", "过程回演，关键步骤各一镜", "前后对比与可复用的提示词/方法"),
    "对比型": ("抛出对比问题", "说清对比条件（同任务同输入）", "A、B 各跑一次，差异逐项对照", "选择建议与行动指令"),
    "答疑型": ("点名提问的人群", "复述真实提问", "拆原因、给方案", "下一期答什么"),
}
SEGMENTS = ("0–3 秒 · 给理由", "3–8 秒 · 给结构", "8 秒至主体 · 给内容", "最后 3 秒 · 给下一步")

# ---------- 五段漏斗与双引擎评分（工作台 03 / 06 表） ----------
FUNNEL = [
    # key, 名称, 主指标, 掉这一层说明什么, 只改这一件事
    ("S1", "流量", "views", "算法没给量，或选题入口太窄，只有已经关注你的人才看得到",
     "换选题入口，不换结构：同一套结构换更普世的题材"),
    ("S2", "留存", "finish_rate", "前 3 秒没抓住，或中段信息密度不够",
     "只改开场：把结论、冲突或结果搬到第 1 句"),
    ("S3", "互动", "share_rate", "内容不产生对话，也不值得转给朋友——它只是「看过了」",
     "加一个可争论的判断，或加一份可以转发的清单"),
    ("S4", "关系", "follow_rate", "看完不知道你是谁，也不知道关注你有什么用",
     "只改结尾：给出关注理由 + 一份可拿走的资产"),
    ("S5", "转化", "intent_rate", "承接路径不存在、太绕，或一条视频想干三件事",
     "一条视频只留一个动作、一个入口：评论区关键词 → 自动回复 → 私域"),
]
METRIC_NAMES = {
    "views": "播放", "finish_rate": "完播率", "follow_rate": "关注率", "share_rate": "分享率",
    "comment_rate": "评论率", "intent_rate": "领取意向率", "avg_view_sec": "平均播放时长",
}

DEFAULT_PARAMS = {
    # 阈值：(优秀线, 良好线, 及格线)；比率用小数。按平台分开，默认三平台相同，跑满 4 周后按自身分位数重设
    "thresholds": {pf: {
        "views": [5000, 2000, 1000],
        "finish_rate": [0.18, 0.12, 0.09],
        "follow_rate": [0.015, 0.008, 0.005],
        "share_rate": [0.03, 0.015, 0.01],
        "comment_rate": [0.012, 0.005, 0.003],
        "intent_rate": [0.01, 0.004, 0.001],
    } for pf in config.PLATFORMS},
    "scores": {"default": [90, 70, 55, 35], "views": [90, 75, 60, 40], "comment_rate": [90, 70, 50, 30]},
    "conv_weights": {"follow_rate": 0.5, "share_rate": 0.3, "comment_rate": 0.2},
    "flow_weight": 0.6,       # 综合分 = 流量权重 × 流量力 + (1 - 流量权重) × 转化力；连续两周流量力中位数 ≥70 后改 0.4
    "verdict": {"high": 70, "flow_low": 60, "conv_low": 55},
    "compare_slot": "D3",     # 同龄对比默认用发布后第 3 天的快照
    "max_p0": 3,              # 每周最多 3 条 P0，超过就无法归因
}

# 表 8：四种判定（加一个过渡区，补上原表阈值之间的空档）
VERDICTS = {
    "双强型": ("加量复制，做成系列，第 1 集置顶或进合集", "结构、母题、结尾钩都不动"),
    "泛流量型": ("只补结尾承接钩，其余不动", "开头钩、选题、节奏"),
    "垂直型": ("换壳扩曝光：换母题外壳吸引泛人群，保留原结构与结尾钩", "结尾钩、信息密度"),
    "低效型": ("停做或换原型；数据异常的先进待重测", "无"),
    "过渡型": ("落在阈值空档里：结构不动，等下一个快照档位再判", "全部"),
}

# 转化路径五跳（表 3）
JUMPS = [
    ("1 刷到→停留", "平台页内前 3 秒", "开头钩：结果奇观、身份代入", "完播率、平均播放时长"),
    ("2 停留→互动", "中段", "中段留人：步骤承诺、信息密度", "评论率、分享率"),
    ("3 互动→关注", "结尾 3 秒", "结尾钩：资产给予、关注理由", "关注率"),
    ("4 关注→私域", "简介、置顶、评论区", "行动指令：关键词 → 评论自动回复", "关键词评论数、私信/企微点击"),
    ("5 私域→付费", "私域内", "轻产品：Skill、组件包、模板", "成交与复购（按人计）"),
]
PLATFORM_ROLES = {
    "douyin": "负责第 1、2 跳：用曝光换停留。结尾别强求关注，会压低完播",
    "channels": "负责第 3 至 5 跳：用信任换承接。不给下一步等于白拿播放",
    "xhs": "负责搜索来的长尾：标题和正文写进搜索词，一条笔记几个月后还能被搜到",
}

# 来源归类：推荐 / 社交（用于推荐占比、社交占比）
SOURCE_GROUPS = {
    "recommend": {"推荐", "首页推荐", "抖音精选", "发现", "看一看"},
    "social": {"朋友", "朋友♡", "分享", "私信消息"},
    "search": {"搜索"},
}


def load_params():
    p = json.loads(json.dumps(DEFAULT_PARAMS))
    f = config.DATA / "method.json"
    if f.exists():
        try:
            saved = json.loads(f.read_text(encoding="utf-8"))
            for k, v in saved.items():
                if k == "thresholds":
                    for pf, metrics in v.items():
                        p["thresholds"].setdefault(pf, {}).update(metrics)
                elif k in p:
                    p[k] = v
        except (ValueError, OSError):
            pass
    return p


def save_params(patch):
    p = load_params()
    for k, v in patch.items():
        if k == "thresholds":
            for pf, metrics in v.items():
                for m, vals in metrics.items():
                    if pf in p["thresholds"] and m in p["thresholds"][pf] and len(vals) == 3:
                        p["thresholds"][pf][m] = [float(x) for x in vals]
        elif k in ("flow_weight",):
            p[k] = min(1.0, max(0.0, float(v)))
        elif k in ("max_p0",):
            p[k] = max(1, int(v))
        elif k == "compare_slot" and v in ("D1", "D3", "D7"):
            p[k] = v
    config.ensure_dirs()
    (config.DATA / "method.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ---------- 定位与用户卡（表 2），发布前必须填完 ----------
PROFILE_FIELDS = [
    ("who", "定位 · 我是谁", "一句能兑现的能力或资源"),
    ("for_whom", "定位 · 我给谁", "一个具体处境里的人或岗位，不是人群标签"),
    ("why_me", "定位 · 凭什么是我", "一个可信证据：全部开源、真实项目、连续踩坑记录"),
    ("stuck", "用户 · 卡在哪一步", "一个具体任务"),
    ("using", "用户 · 已经在用什么", "现有工具与熟练度，决定讲解起点"),
    ("scene", "用户 · 在什么场景刷到", "通勤、工位、睡前，决定时长档与信息密度"),
    ("pay", "用户 · 愿意付出什么", "关注、收藏、进群、付费四选一，决定结尾钩"),
    ("asset_keyword", "承接 · 评论区领取关键词", "观众在评论区打这个词就自动回复领取方式，例如 skill"),
    ("asset_desc", "承接 · 领取的是什么", "例如：整套 Skill + 181 个剪辑组件 + 不露脸全流程"),
    ("asset_path", "承接 · 去哪里领", "例如：主页置顶 / 私信自动回复 / 粉丝群。评论区别直接留微信号和外链"),
]
# 根据最近五条作品推断的初稿，标了「待确认」，用户改过一次后就以用户的为准
DEFAULT_PROFILE = {
    "account": "宁少AI进化论",
    "who": "用 AI 把一条视频从选题做到成片，并把整套流程、181 个剪辑组件和十几个 Skill 全部开源的人（待确认）",
    "for_whom": "想做自媒体但不想露脸、不会剪辑、每周挤不出一整天做视频的个人创作者和小团队运营（待确认）",
    "why_me": "账号里的每条视频本身就是用这套流程做的，「自己拆自己」可以当场复现，流程和组件全部开源（待确认）",
    "stuck": "有选题、有想法，但从脚本到成片要耗一整天，最卡的是剪口播（待确认）",
    "using": "会用 Kimi / 豆包 / ChatGPT 写文案，用过剪映，不会写代码、不会搭 Agent 工作流（待确认）",
    "scene": "工位午休和下班通勤，30–60 秒能看完；有用的先收藏，回家再照着做（待确认）",
    "pay": "关注 → 评论区打关键词领 Skill → 进私域 → 为 Skill / 组件包 / 模板付费（待确认）",
    "asset_keyword": "skill",
    "asset_desc": "整套 AI 剪口播 Skill + 181 个剪辑组件 + 不露脸全自动流程（待确认）",
    "asset_path": "主页置顶 / 私信（待确认）",
}


def load_profile():
    p = dict(DEFAULT_PROFILE)
    f = config.DATA / "profile.json"
    if f.exists():
        try:
            p.update(json.loads(f.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    return p


def save_profile(patch):
    p = load_profile()
    for k in ["account"] + [f[0] for f in PROFILE_FIELDS]:
        if k in patch:
            p[k] = str(patch[k]).strip()[:500]
    config.ensure_dirs()
    (config.DATA / "profile.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def profile_gaps(profile):
    """定位卡里还没填、或还带着「待确认」的字段。"""
    return [name for key, name, _ in PROFILE_FIELDS if not profile.get(key) or "待确认" in profile.get(key, "")]


def dictionary_text():
    """把封闭字典写成给大模型看的说明，避免它自造标签。"""
    lines = ["标签只能从下列值里选，不允许自造同义词："]
    for field, (name, values) in TAG_FIELDS.items():
        lines.append(f"- {name}（{field}）：{' / '.join(values)}")
    lines.append("组合规则（原型 → 推荐母题 / 首选开头钩 / 默认结尾钩 / 不要搭配）：")
    for a, (motifs, ho, he, avoid) in COMBOS.items():
        lines.append(f"- {a}：{'、'.join(motifs)} / {ho} / {he} / 不要：{avoid}")
    return "\n".join(lines)


def profile_text(profile):
    lines = [f"账号：{profile.get('account', '')}"]
    lines += [f"- {name}：{profile.get(key, '')}" for key, name, _ in PROFILE_FIELDS]
    return "\n".join(lines)
