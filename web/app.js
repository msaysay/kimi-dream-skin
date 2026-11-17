"use strict";
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const PF = ["douyin", "xhs", "channels"];
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const S = {
  page: "today", state: null, method: null, data: null, days: 30, reviews: [], contents: [], comments: null,
  openJob: null, jobLog: "", commentTab: "todo", editing: null, editRule: null, bench: null, benchDays: 30, benchCands: null, benchTab: "hits",
  form: { kind: "video", files: "", cover: "", cover_sec: "", cover_focus: [0.5, 0.4], cover_mode: "auto", title: "", desc: "", tags: "", schedule: "", headless: false, targets: {}, drafts: {}, content_id: null },
  coverPreview: null,
};

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const json = await res.json().catch(() => ({ error: "服务没有响应" }));
  if (!res.ok || json.error) throw new Error(json.error || "请求失败");
  return json;
}
let toastTimer;
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg; el.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (el.hidden = true), 4000);
}
const act = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message); } };

// ---------- 格式化 ----------
function num(v) {
  if (v == null) return "—";
  if (Math.abs(v) >= 10000) return (v / 10000).toFixed(1) + " 万";
  return Math.round(v).toLocaleString("zh-CN");
}
const pct = (v, d = 2) => (v == null ? "—" : (v * 100).toFixed(d) + "%");
const sec = (v) => (v == null ? "—" : v >= 60 ? `${Math.floor(v / 60)} 分 ${Math.round(v % 60)} 秒` : v.toFixed(1) + " 秒");
const day = (ms) => (ms ? new Date(ms).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" }) : "—");
const dt = (ms) => (ms ? new Date(ms).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
const pfName = (k) => S.state.platforms[k].name;
const pfDot = (k) => `<span class="dot ${k}"></span>`;
const cleanTitle = (t) => (t || "").replace(/#\S+/g, "").trim() || t || "（无标题）";
const metricName = (m) => S.method.metric_names[m] || m;
const fmtMetric = (m, v) => (v == null ? "—" : m === "views" ? num(v) : m === "avg_view_sec" ? sec(v) : pct(v));
function rel(r) {
  if (r == null) return "";
  const d = Math.round((r - 1) * 100);
  if (Math.abs(d) < 5) return `<span class="rel"><span class="flat">●</span> 与平时持平</span>`;
  return `<span class="rel"><span class="${d > 0 ? "up" : "down"}">${d > 0 ? "▲" : "▼"}</span> 比平时${d > 0 ? "高" : "低"} ${Math.abs(d)}%</span>`;
}
const VERDICT_CLASS = { "双强型": "good", "泛流量型": "warn", "垂直型": "info", "低效型": "bad", "过渡型": "" };
const verdictTag = (v) => (v ? `<span class="tag v-${VERDICT_CLASS[v] || ""}">${{ good: "✓", warn: "▲", info: "◆", bad: "✕" }[VERDICT_CLASS[v]] || "●"} ${v}</span>` : "");
const LEVEL_ICON = { "优秀": "✓", "良好": "●", "及格": "○", "偏弱": "✕", "无数据": "—" };
const JOB_NAME = { login: "扫码登录", check: "检查登录", publish: "发布", collect: "采集数据", review: "生成复盘", plan: "生成选题计划",
  refine: "AI 细化选题标题", ai_script: "AI 写稿", comments: "抓评论", comment_send: "发送评论回复", bench: "刷新对标账号", bench_analyze: "AI 拆解对标爆款" };
const STATUS_NAME = { pending: "排队中", running: "进行中", done: "完成", failed: "失败", cancelled: "已取消" };
const ACC_STATUS = { ok: "已登录", expired: "登录过期", unknown: "未登录", demo: "示例" };
function jobTitle(j) {
  const p = j.params || {};
  const who = p.platform ? `${pfName(p.platform)}「${p.account}」` : "";
  if (j.type === "publish") return `发布到${who}：${p.title || ""}`;
  if (j.type === "review") return `生成${p.ai ? " AI " : "规则"}复盘`;
  return `${JOB_NAME[j.type] || j.type} ${who}`;
}
// AI 的活儿交给你正在用的 AI 助手（Claude Code / Codex / WorkBuddy）：按钮总是可用，点了就排进「AI 待办」
const aiReady = () => true;
const ASK = "处理 NingLoop 的 AI 待办";
const pendingFor = (kind, ref) => (S.state.agent_pending || []).find((t) => t.kind === kind && String(t.ref) === String(ref));
function agentHint(task) {
  if (!task) return "";
  if (S.state.agent_api) return `<div class="banner">已交给配置的模型接口处理（待办 #${task.id}），好了会自动填进来。</div>`;
  return `<div class="banner ai">已排进 AI 待办 #${task.id}。在 Claude Code / Codex / WorkBuddy 里打开 NingLoop 文件夹，说「<b>${ASK}</b>」，写好会自动填进来。
    <div class="row" style="margin-top:8px"><button class="small" data-act="copy-ask">复制这句话</button>
    <button class="small" data-act="agent-copy" data-id="${task.id}">复制任务说明（发给任何 AI 都行）</button>
    <button class="small" data-act="agent-paste" data-id="${task.id}">粘贴 AI 的结果</button></div></div>`;
}

// ---------- 极简 Markdown ----------
function md(text) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  let html = "", list = null;
  const close = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const line of (text || "").split("\n")) {
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)/))) { close(); html += `<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`; }
    else if ((m = line.match(/^\s*[-*]\s+(.*)/))) { if (list !== "ul") { close(); html += "<ul>"; list = "ul"; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = line.match(/^\s*\d+[.、]\s*(.*)/))) { if (list !== "ol") { close(); html += "<ol>"; list = "ol"; } html += `<li>${inline(m[1])}</li>`; }
    else if (/^-{3,}$/.test(line.trim())) { close(); html += "<hr>"; }
    else if (!line.trim()) close();
    else { close(); html += `<p>${inline(line)}</p>`; }
  }
  close();
  return html;
}

// ---------- 标签选择 ----------
function tagSelect(field, value, attr) {
  const t = S.method.tags[field];
  return `<label class="field"><span>${t.name}</span><select ${attr}="${field}"><option value="">未定</option>
    ${t.values.map((v) => `<option ${v === value ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>`;
}
function tagChips(tags) {
  const order = ["motif", "archetype", "hook_open", "hook_end", "duration_bin"];
  const vals = order.map((k) => tags && tags[k]).filter(Boolean);
  return vals.length ? `<div class="chips">${vals.map((v) => `<span class="chip">${esc(v)}</span>`).join("")}</div>` : "";
}
function comboHint(tags) {
  const c = S.method.combos[tags.archetype];
  if (!c) return "";
  const [motifs, ho, he, avoid] = c;
  const warn = [];
  if (tags.motif && !motifs.includes(tags.motif)) warn.push(`「${tags.archetype}」推荐配 ${motifs.join("、")}`);
  if (tags.archetype === "教程型" && tags.hook_open === "结果奇观") warn.push("教程型配结果奇观开头会提前露底");
  if (tags.archetype === "清单型" && tags.hook_mid === "步骤承诺") warn.push("清单型配步骤承诺，会让人等一个不存在的流程");
  return `<p class="hint">组合规则：${esc(tags.archetype)} → 推荐母题 ${esc(motifs.join("、"))}，首选开头「${esc(ho)}」，默认结尾「${esc(he)}」，不要：${esc(avoid)}</p>`
    + (warn.length ? `<p class="hint warn-text">⚠ ${esc(warn.join("；"))}</p>` : "");
}

// ---------- 数据看板 ----------
async function loadData() { S.data = await api(`/api/data?days=${S.days}`); }

function funnelCard(d) {
  const rows = Object.entries(d.summary).map(([pf, s]) => `<tr><td>${pfDot(pf)}${pfName(pf)}<div class="sub">${s.posts} 条</div></td>
    ${s.funnel.map((x) => `<td><span class="lv lv-${x.level}">${LEVEL_ICON[x.level]} ${x.level}</span><div class="sub">${fmtMetric(x.metric, x.value)}</div></td>`).join("")}
    <td>${s.first_red ? `<b>${s.first_red.key} ${s.first_red.name}</b><div class="hint">${esc(s.first_red.action)}</div>` : `<span class="sub">S1–S4 都过了及格线</span>`}
    ${s.funnel[4].level === "无数据" ? `<div class="hint">S5 没数据：先在「评论」页开自动回复规则</div>` : ""}</td></tr>`).join("");
  if (!rows) return "";
  return `<div class="card"><h2>五段漏斗 <span class="sub" style="font-weight:400">各平台中位数；从 S1 往下第一个「偏弱」就是本周唯一要改的那一段</span></h2>
    <div class="table-wrap"><table><thead><tr><th>平台</th>${d.funnel.map((f) => `<th>${f.key} ${f.name}</th>`).join("")}<th>本周只改</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

function dashboard() {
  const d = S.data, st = S.state;
  const lastCollect = Math.max(0, ...st.accounts.map((a) => a.last_collect || 0));
  const head = `<div class="head"><h1>数据看板</h1>
    <div class="seg">${[[7, "近 7 天"], [30, "近 30 天"], [90, "近 90 天"], [0, "全部"]].map(([v, t]) => `<button data-act="days" data-v="${v}" class="${S.days === v ? "on" : ""}">${t}</button>`).join("")}</div>
    <div class="grow"></div><span class="sub">${lastCollect ? "上次采集 " + dt(lastCollect) : ""}</span>
    <button data-act="collect-all">立即采集</button></div>`;
  if (!st.has_data) {
    return head + `<div class="card empty"><p>还没有数据。先到「账号」页添加账号、扫码登录，然后点「立即采集」。</p>
      <p>已经有手工整理的《内容数据工作台.xlsx》，可以先导入；想先看看界面，可以载入虚构的示例数据。</p>
      <button class="primary" data-act="go" data-page="settings">导入工作台表格</button> <button data-act="demo-load">载入示例数据</button></div>`;
  }
  const demo = st.accounts.some((a) => a.status === "demo");
  const banners = (demo ? `<div class="banner">当前包含虚构的示例数据，作品和数字都不是真的。<button class="small" data-act="demo-clear" style="margin-left:10px">清除示例数据</button></div>` : "")
    + (d.profile_gaps.length ? `<div class="banner">定位与用户卡还有 ${d.profile_gaps.length} 项没填或待确认。定位决定「拍不拍」，漏斗只决定「怎么改」。<button class="small" data-act="go" data-page="settings" style="margin-left:10px">去填写</button></div>` : "");
  const kpi = (label, key) => `<div class="card kpi"><div class="label">${label}</div><div class="num">${num(d.totals[key])}</div>
    <div class="split">${PF.filter((p) => d.by_platform[p].posts).map((p) => `<span>${pfDot(p)}${pfName(p)} ${num(d.by_platform[p][key])}</span>`).join("")}</div></div>`;
  const cell = (w, p) => {
    const post = w.posts.find((x) => x.platform === p);
    if (!post) return `<td class="sub">未发布</td>`;
    return `<td><div class="num-cell">${num(post.cmp.views)}</div>${rel(post.rel.views)}
      <div>${post.flag ? `<span class="tag v-bad">⚠ ${esc(post.flag)}</span>` : verdictTag(post.verdict)}</div></td>`;
  };
  const rowsHtml = d.works.map((w, i) => `<tr class="click" data-act="open-work" data-i="${i}">
    <td class="title-cell"><div class="t" title="${esc(w.title)}">${esc(cleanTitle(w.title))}</div><div class="sub">${day(w.first_post_time)} 发布${w.duration_sec ? " · " + sec(w.duration_sec) : ""}</div>
    ${tagChips(w.tags) || `<span class="hint">未打标签</span>`}</td>
    ${PF.map((p) => cell(w, p)).join("")}</tr>`).join("");
  return head + banners + `<div class="grid k4"><div class="card kpi"><div class="label">作品</div><div class="num">${d.totals.works}</div>
      <div class="split"><span>共 ${d.totals.posts} 条发布</span></div></div>
    ${kpi("播放", "views")}${kpi("互动（赞评藏转）", "interactions")}${kpi("涨粉", "new_fans")}</div>
    ${funnelCard(d)}
    <div class="card"><h2>作品 <span class="sub" style="font-weight:400">每格是同龄快照（默认 D3）的播放和判定；点一行看评分、诊断，给作品补标签</span></h2>
    <div class="table-wrap"><table><thead><tr><th>作品</th>${PF.map((p) => `<th>${pfDot(p)}${pfName(p)}</th>`).join("")}</tr></thead>
    <tbody>${rowsHtml || `<tr><td colspan="4" class="sub">这段时间没有作品</td></tr>`}</tbody></table></div></div>`;
}

const METRICS = [
  ["播放", (p) => num(p.m.views), "views"], ["完播率", (p) => pct(p.m.finish_rate), "finish_rate"], ["平均播放时长", (p) => sec(p.m.avg_view_sec)],
  ["关注率", (p) => pct(p.m.follow_rate), "follow_rate"], ["分享率", (p) => pct(p.m.share_rate), "share_rate"], ["评论率", (p) => pct(p.m.comment_rate)],
  ["领取关键词评论", (p) => (p.comments_tracked ? p.intent + " 条" : "—")], ["推荐占比", (p) => pct(p.rec_share, 1)], ["社交占比（朋友+分享）", (p) => pct(p.social_share, 1)],
  ["首日占累计", (p) => pct(p.first_day_share, 1)], ["次日 / 首日", (p) => (p.d2_over_d1 == null ? "—" : p.d2_over_d1.toFixed(2) + (p.d2_over_d1 > 1 ? "（有复利）" : ""))],
];

function workDrawer(w) {
  const posts = w.posts;
  const tags = w.tags || {};
  const scores = posts.map((p) => `<div class="score-card">
      <div class="row"><b>${pfDot(p.platform)}${pfName(p.platform)}</b><span class="sub">「${esc(p.account)}」· ${p.cmp_slot === "最新" ? `最新快照（发布 ${(p.age_days || 0).toFixed(1)} 天，非同龄）` : p.cmp_slot + " 快照"}</span></div>
      ${p.flag ? `<p class="warn-text">⚠ 状态「${esc(p.flag)}」：不进评分和结论，只进待重测。${esc(p.flag_note || "")}</p>` : ""}
      <div class="scores"><div><div class="sub">流量力</div><div class="big">${p.flow ?? "—"}</div></div><div><div class="sub">转化力</div><div class="big">${p.conv ?? "—"}</div></div>
        <div><div class="sub">综合</div><div class="big">${p.total ?? "—"}</div></div><div><div class="sub">判定</div><div>${verdictTag(p.verdict) || "—"}</div></div></div>
      ${p.verdict ? `<p class="hint">${esc(S.data.verdicts[p.verdict][0])}；保持不变：${esc(S.data.verdicts[p.verdict][1])}</p>` : ""}
      <div class="funnel">${p.funnel.map((x) => `<div class="stage lv-${x.level}"><div class="s">${x.key} ${x.name}</div><div class="v">${fmtMetric(x.metric, x.value)}</div><div class="s">${LEVEL_ICON[x.level]} ${x.level}</div></div>`).join("")}</div>
      ${p.first_red ? `<p><b>最先掉队：${p.first_red.key} ${p.first_red.name}</b>。${esc(p.first_red.meaning)}。只改这一件事：${esc(p.first_red.action)}</p>` : ""}
      <div class="row" style="margin-top:8px"><span class="sub">作品状态</span><select data-flag="${p.platform}|${esc(p.post_id)}" style="width:auto">
        ${["", "仅自己可见", "已删除", "数据异常"].map((f) => `<option value="${f}" ${f === (p.flag || "") ? "selected" : ""}>${f || "正常"}</option>`).join("")}</select></div>
    </div>`).join("");
  const table = `<table><thead><tr><th>指标（同龄快照）</th>${posts.map((p) => `<th>${pfDot(p.platform)}${pfName(p.platform)}</th>`).join("")}</tr></thead><tbody>
    ${METRICS.map(([name, fn, key]) => `<tr><td>${name}</td>${posts.map((p) => `<td class="num-cell">${fn(p)} ${key ? rel(p.rel[key]) : ""}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  const hasRet = posts.some((p) => p.retention.length);
  const hasSrc = posts.filter((p) => p.sources.length);
  return `<div class="panel"><div class="head"><div class="grow"><h1>${esc(cleanTitle(w.title))}</h1>
      <div class="sub">${dt(w.first_post_time)} 首发${w.duration_sec ? " · 时长 " + sec(w.duration_sec) : ""}</div></div><button data-act="close-drawer">关闭</button></div>
    <div class="card"><h2>评分与诊断 <span class="sub" style="font-weight:400">流量力与转化力分开算，不合成一个总分来判断</span></h2><div class="grid" style="margin:0">${scores}</div></div>
    <div class="card"><h2>结构标签 <span class="sub" style="font-weight:400">${w.content_id ? "已打标签，改了会影响结构矩阵和下一步计划" : "历史作品补标签；以后在「创作」里发布前就打好"}</span></h2>
      <div class="grid k3" style="margin:0">${Object.keys(S.method.tags).map((f) => tagSelect(f, tags[f], "data-wtag")).join("")}</div>
      ${tags.archetype ? comboHint(tags) : ""}
      <button class="primary" data-act="save-work-tags" data-i="${S.data.works.indexOf(w)}">保存标签</button></div>
    ${hasRet ? `<div class="card"><h2>留存曲线</h2><div id="ch-ret" class="chart"></div></div>` : ""}
    <div class="card"><h2>指标与流量结构</h2><div class="table-wrap">${table}</div></div>
    ${hasSrc.length ? `<div class="card"><h2>播放来源</h2><div class="grid k${Math.min(3, hasSrc.length)}" style="margin:0">${hasSrc.map((p) => `<div><div class="sub">${pfDot(p.platform)}${pfName(p.platform)}</div><div id="ch-src-${p.platform}" class="chart short"></div></div>`).join("")}</div></div>` : ""}
    <div class="card"><h2>播放增长 <span class="sub" style="font-weight:400">每次采集记录一个点</span></h2><div id="ch-hist" class="chart short"></div></div></div>`;
}

let charts = [];
function chartBase() {
  const ink2 = css("--ink-2"), line = css("--line"), axis = css("--axis");
  return {
    textStyle: { fontFamily: "inherit", color: ink2 }, animation: false,
    grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    legend: { top: 0, left: 0, icon: "roundRect", itemWidth: 12, itemHeight: 4, textStyle: { color: ink2 } },
    tooltip: { trigger: "axis", backgroundColor: css("--surface"), borderColor: line, textStyle: { color: css("--ink") } },
    axisStyle: { axisLine: { lineStyle: { color: axis } }, axisTick: { show: false }, axisLabel: { color: css("--muted") }, splitLine: { lineStyle: { color: line } } },
  };
}
function drawCharts(w) {
  charts.forEach((c) => c.dispose()); charts = [];
  const base = chartBase(), color = (p) => css("--" + p);
  const mk = (id, opt) => { const el = document.getElementById(id); if (!el) return; const c = echarts.init(el); c.setOption(opt); charts.push(c); };
  const { axisStyle, ...common } = base;
  const series = [];
  for (const p of w.posts) {
    if (p.retention.length) series.push({ name: pfName(p.platform), type: "line", data: p.retention, showSymbol: p.retention.length < 12, symbolSize: 8, lineStyle: { width: 2 }, itemStyle: { color: color(p.platform) } });
    if (p.benchmark.length) series.push({ name: "抖音同类作者平均", type: "line", data: p.benchmark, showSymbol: false, lineStyle: { width: 2, type: "dashed" }, itemStyle: { color: css("--muted") } });
  }
  mk("ch-ret", { ...common, tooltip: { ...common.tooltip, valueFormatter: (v) => (v == null ? "—" : v.toFixed(1) + "%") },
    xAxis: { type: "value", ...axisStyle, axisLabel: { color: css("--muted"), formatter: "{value} 秒" }, splitLine: { show: false }, max: "dataMax" },
    yAxis: { type: "value", max: 100, ...axisStyle, axisLabel: { color: css("--muted"), formatter: "{value}%" } }, series });
  for (const p of w.posts.filter((x) => x.sources.length)) {
    const top = p.sources.slice(0, 6).reverse();
    mk("ch-src-" + p.platform, { ...common, legend: { show: false }, grid: { left: 8, right: 48, top: 4, bottom: 4, containLabel: true },
      tooltip: { ...common.tooltip, trigger: "item", valueFormatter: (v) => v.toFixed(1) + "%" },
      xAxis: { type: "value", show: false }, yAxis: { type: "category", data: top.map((s) => s[0]), ...axisStyle, axisLine: { show: false } },
      series: [{ type: "bar", data: top.map((s) => s[1]), barWidth: 12, itemStyle: { color: color(p.platform), borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: "right", color: css("--ink-2"), formatter: (x) => x.value.toFixed(0) + "%" } }] });
  }
  mk("ch-hist", { ...common, tooltip: { ...common.tooltip, valueFormatter: (v) => num(v) },
    xAxis: { type: "time", ...axisStyle, splitLine: { show: false } }, yAxis: { type: "value", ...axisStyle },
    series: w.posts.map((p) => ({ name: pfName(p.platform), type: "line", data: p.history.map((h) => [h.fetched_at, h.views]), symbolSize: 8, lineStyle: { width: 2 }, itemStyle: { color: color(p.platform) } })) });
}
addEventListener("resize", () => charts.forEach((c) => c.resize()));

// ---------- 计划 ----------
const ACCEPT_CLASS = { "达成": "good", "部分达成": "warn", "未达成": "bad", "等数据": "", "未发布": "", "缺快照": "warn", "无目标": "" };
function targetText(c) {
  return (c.targets || []).map((t) => `${metricName(t.metric)} ≥ ${t.metric === "views" ? num(t.value) : pct(t.value)}（${t.slot || "D3"}）`).join("，") || "—";
}
function planPage() {
  const week = S.method.week;
  const items = S.contents.filter((c) => c.source === "plan");
  const weeks = [...new Set(items.map((c) => c.week))].sort().reverse();
  const order = { P0: 0, P1: 1, P2: 2 };
  const rowsFor = (list) => list.sort((a, b) => (order[a.priority] ?? 9) - (order[b.priority] ?? 9)).map((c) => {
    const r = c.acceptance_result;
    return `<tr class="click" data-act="edit-content" data-id="${c.id}"><td><span class="tag pri-${c.priority}">${c.priority || "—"}</span></td>
      <td class="title-cell"><div class="t" title="${esc(c.title)}">${esc(c.title)}</div><div class="hint">${esc(c.basis || "")}</div></td>
      <td>${tagChips(c)}<div class="sub">${S.method.workflows[c.workflow]?.name || ""}</div></td>
      <td class="sub">${esc(c.schedule_date || "")}</td>
      <td><div>${targetText(c)}</div><div class="hint">${esc(c.acceptance || "")}</div></td>
      <td><div>${esc(c.stage)}</div><span class="tag v-${ACCEPT_CLASS[r.overall] || ""}">${esc(r.overall)}</span></td></tr>`;
  }).join("");
  const table = (list) => `<div class="table-wrap"><table><thead><tr><th>优先级</th><th>选题与数据依据</th><th>结构 / 工作流</th><th>排期</th><th>目标指标与验收口径</th><th>进度 / 验收</th></tr></thead>
    <tbody>${rowsFor(list)}</tbody></table></div>`;
  return `<div class="head"><h1>下一步创作计划</h1><span class="sub">本周 ${week}</span><div class="grow"></div>
    <button data-act="plan" data-ai="">按数据生成本周计划</button>
    <button data-act="plan-refine">把方向写成具体标题</button>
    <button class="primary" data-act="plan-scripts">本周选题全部出稿</button></div>
    <div class="card"><p style="margin:0">计划由规则从复盘里推出来：双强型 → 系列续集；泛流量型 → 同结构续作只补结尾钩；垂直型 → 换壳扩曝光；状态异常 → 待重测；
      再加一个没做过的母题、一个评论区高频提问。每周最多 ${S.method.params.max_p0} 条 P0，一条选题只改一个变量。点任意一行进入创作流程，发布后采集到数据会自动验收。</p></div>
    ${weeks.length ? weeks.map((w) => `<div class="card"><h2>${w === week ? "本周" : w}</h2>${table(items.filter((c) => c.week === w))}</div>`).join("")
      : `<div class="card empty"><p>还没有计划。先有数据（采集或导入表格），再点「按数据生成本周计划」。</p></div>`}`;
}

// ---------- 创作 ----------
function createPage() {
  const wfs = S.method.workflows;
  const items = S.contents.filter((c) => c.source !== "backfill");
  const board = (key) => {
    const stages = wfs[key].stages.map((s) => s[0]);
    return `<div class="card"><div class="row" style="margin-bottom:10px"><h2 style="margin:0">${esc(wfs[key].name)}</h2><span class="sub">${esc(wfs[key].desc)}</span>
      <span class="grow"></span><button class="small" data-act="new-content" data-wf="${key}">+ 新选题</button></div>
      <div class="kanban">${stages.map((st) => {
        const rank = { P0: 0, P1: 1, P2: 2 };
        const cards = items.filter((c) => c.workflow === key && c.stage === st)
          .sort((a, b) => (rank[a.priority] ?? 9) - (rank[b.priority] ?? 9) || String(a.schedule_date || "").localeCompare(String(b.schedule_date || "")));
        return `<div class="col"><div class="col-h">${esc(st)} <span class="sub">${cards.length || ""}</span></div>
          ${cards.map((c) => `<div class="kcard" data-act="edit-content" data-id="${c.id}"><div>${c.priority ? `<span class="tag pri-${c.priority}">${c.priority}</span> ` : ""}${esc(c.title)}</div>
            <div class="sub">${esc([c.archetype, c.hook_end].filter(Boolean).join(" · "))}${c.schedule_date ? " · " + esc(c.schedule_date.slice(5)) : ""}</div>
            ${c.stage === "数据验收" ? `<span class="tag v-${ACCEPT_CLASS[c.acceptance_result.overall] || ""}">${esc(c.acceptance_result.overall)}</span>` : ""}</div>`).join("")}</div>`;
      }).join("")}</div></div>`;
  };
  return `<div class="head"><h1>创作</h1><span class="sub">选题 → 定结构 → 稿子 → 成片 → 发布 → 数据验收，两套工作流各一块看板</span></div>${Object.keys(wfs).map(board).join("")}`;
}

function contentDrawer(c) {
  const wf = S.method.workflows[c.workflow] || S.method.workflows.topic;
  const stages = wf.stages.map((s) => s[0]);
  const stageItems = (wf.stages.find((s) => s[0] === c.stage) || [c.stage, []])[1];
  const checks = (c.checklist || {})[c.stage] || [];
  const posts = c.posts || [];
  const linkable = S.data ? S.data.works.flatMap((w) => w.posts).filter((p) => !posts.some((x) => x.platform === p.platform && x.post_id === p.post_id)) : [];
  const r = c.acceptance_result || { overall: "未发布", results: [] };
  const targets = (c.targets && c.targets.length ? c.targets : []).concat([{ metric: "", value: "", slot: "D3" }]);
  return `<div class="panel"><div class="head"><div class="grow"><h1>${c.id ? "编辑选题" : "新选题"}</h1>
      ${c.basis ? `<div class="sub">${esc(c.basis)}</div>` : ""}</div><button data-act="close-drawer">关闭</button></div>
    <div class="card"><label class="field"><span>选题</span><input data-c="title" value="${esc(c.title || "")}" maxlength="80"></label>
      <div class="grid k4" style="margin:0">
        <label class="field"><span>工作流</span><select data-c="workflow">${Object.entries(S.method.workflows).map(([k, v]) => `<option value="${k}" ${k === c.workflow ? "selected" : ""}>${esc(v.name)}</option>`).join("")}</select></label>
        <label class="field"><span>阶段</span><select data-c="stage">${stages.map((s) => `<option ${s === c.stage ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></label>
        <label class="field"><span>优先级</span><select data-c="priority">${["", "P0", "P1", "P2"].map((p) => `<option ${p === (c.priority || "") ? "selected" : ""}>${p}</option>`).join("")}</select></label>
        <label class="field"><span>排期</span><input type="date" data-c="schedule_date" value="${esc(c.schedule_date || "")}"></label></div></div>
    <div class="card"><h2>${c.workflow === "talk" ? "口播稿" : "脚本"}</h2>
      ${pendingFor("script", c.id) ? agentHint(pendingFor("script", c.id)) : `<div class="row" style="margin-bottom:8px">
        <button class="primary" data-act="content-ai">出稿</button>
        <span class="hint grow">一步出完：结构没定的会按字典定好，带 5 个备选标题、三个平台的结尾改法和自检。由你正在用的 AI 助手来写</span>
        <button class="small" data-act="content-scaffold">只要骨架</button></div>`}
      <textarea data-c="script" style="min-height:260px;font-family:inherit">${esc(c.script || "")}</textarea>
      <label class="field" style="margin-top:12px"><span>备注</span><textarea data-c="notes">${esc(c.notes || "")}</textarea></label></div>
    <div class="card"><h2>这一阶段要做到</h2>${stageItems.length ? stageItems.map((it, i) => `<label class="field"><input type="checkbox" data-check="${i}" ${checks[i] ? "checked" : ""}>${esc(it)}</label>`).join("") : `<p class="sub">这一阶段没有检查项。</p>`}
      <div class="row"><button data-act="content-next" ${stages.indexOf(c.stage) >= stages.length - 1 ? "disabled" : ""}>完成，进入下一阶段</button>
      ${c.id ? `<button data-act="content-publish">带着这条去发布</button>` : ""}</div></div>
    <div class="card"><h2>结构（发布前打标，只改一个变量）</h2><div class="grid k3" style="margin:0">${Object.keys(S.method.tags).map((f) => tagSelect(f, c[f], "data-c")).join("")}</div>
      ${c.archetype ? comboHint(c) : ""}</div>
    ${r.results.length && ["达成", "部分达成", "未达成"].includes(r.overall) ? `<div class="card"><h2>验收结论 <span class="tag v-${ACCEPT_CLASS[r.overall] || ""}">${esc(r.overall)}</span></h2>
      ${c.experiment ? `<p class="sub">这条的唯一改动：${esc(S.method.tags[c.experiment.field] ? S.method.tags[c.experiment.field].name : c.experiment.field)} → ${esc(c.experiment.to || "")}。结果会记进打法库。</p>` : ""}
      <textarea id="conclusion" style="min-height:60px" placeholder="一句话：这次学到了什么（例如：结尾改成关注理由，关注率从 0.28% 到 0.9%，有效）">${esc(c.conclusion || "")}</textarea>
      <div class="row" style="margin-top:8px"><button class="primary" data-act="content-conclude">${c.conclusion ? "更新结论" : "写进结论"}</button><button data-act="go" data-page="playbook">看打法库</button></div></div>` : ""}
    <div class="card"><h2>目标指标与验收</h2>
      ${targets.map((t, i) => `<div class="row" style="margin-bottom:8px"><select data-t="metric" data-ti="${i}" style="width:160px"><option value="">（不设）</option>
        ${["views", "finish_rate", "follow_rate", "share_rate", "comment_rate", "intent_rate"].map((m) => `<option value="${m}" ${m === t.metric ? "selected" : ""}>${metricName(m)}</option>`).join("")}</select>
        <span>≥</span><input data-t="value" data-ti="${i}" value="${t.value === "" ? "" : t.metric === "views" ? t.value : (t.value * 100).toFixed(2)}" style="width:110px"><span class="sub">${t.metric === "views" ? "次" : "%"}</span>
        <select data-t="slot" data-ti="${i}" style="width:90px">${["D1", "D3", "D7"].map((s) => `<option ${s === (t.slot || "D3") ? "selected" : ""}>${s}</option>`).join("")}</select></div>`).join("")}
      <label class="field"><span>验收口径（什么情况下算成立、什么情况下算失败）</span><textarea data-c="acceptance" style="min-height:60px">${esc(c.acceptance || "")}</textarea></label>
      ${r.results.length ? `<p><span class="tag v-${ACCEPT_CLASS[r.overall] || ""}">${esc(r.overall)}</span> ${r.results.map((x) => `${pfName(x.platform)} ${metricName(x.metric)} ${fmtMetric(x.metric, x.value)} / 目标 ${fmtMetric(x.metric, x.target)}（${x.slot}）`).join("；")}</p>` : ""}</div>
    <div class="card"><h2>发出去的作品</h2>
      ${posts.length ? posts.map((p) => `<div class="row" style="margin-bottom:6px">${pfDot(p.platform)}${pfName(p.platform)} <span class="grow">${esc(cleanTitle(p.title || p.post_id))}</span><button class="small" data-act="content-unlink" data-platform="${p.platform}" data-post="${esc(p.post_id)}">取消关联</button></div>`).join("")
        : `<p class="sub">发布并采集后会按标题自动关联；也可以手动选。</p>`}
      ${c.id && linkable.length ? `<div class="row"><select id="link-post" class="grow">${linkable.map((p) => `<option value="${p.platform}|${esc(p.post_id)}">${pfName(p.platform)} · ${esc(cleanTitle(p.title).slice(0, 40))}</option>`).join("")}</select><button data-act="content-link">关联</button></div>` : ""}</div>
    <div class="row"><button class="primary" data-act="content-save">保存</button><span class="grow"></span>${c.id ? `<button class="danger" data-act="content-archive">归档</button>` : ""}</div></div>`;
}
function readContentForm() {
  const panel = $("#drawer .panel");
  const c = { ...S.editing };
  panel.querySelectorAll("[data-c]").forEach((el) => { c[el.dataset.c] = el.value; });
  const checks = [...panel.querySelectorAll("[data-check]")].map((el) => el.checked);
  c.checklist = { ...(S.editing.checklist || {}), [S.editing.stage]: checks };
  const targets = [];
  panel.querySelectorAll("[data-t=metric]").forEach((el) => {
    const i = el.dataset.ti, metric = el.value;
    const raw = panel.querySelector(`[data-t=value][data-ti="${i}"]`).value.trim();
    if (metric && raw !== "" && !isNaN(+raw)) targets.push({ metric, value: metric === "views" ? +raw : +raw / 100, slot: panel.querySelector(`[data-t=slot][data-ti="${i}"]`).value });
  });
  c.targets = targets;
  return c;
}
function openContent(c) {
  S.editing = c;
  const d = $("#drawer"); d.innerHTML = contentDrawer(c); d.hidden = false;
}

// ---------- 评论 ----------
const COMMENT_TABS = [["todo", "待处理"], ["draft", "回复草稿"], ["queued", "排队发送"], ["replied", "已回复"], ["question", "提问"], ["all", "全部"]];
function commentsPage() {
  const C = S.comments;
  if (!C) return `<div class="card sub">加载中…</div>`;
  const filt = { todo: (c) => c.state === "new", draft: (c) => c.state === "draft", queued: (c) => c.state === "queued", replied: (c) => c.state === "replied",
    question: (c) => c.is_question && c.state !== "replied" && c.state !== "ignored", all: () => true }[S.commentTab];
  const list = C.comments.filter(filt);
  const counts = Object.fromEntries(COMMENT_TABS.map(([k]) => [k, C.comments.filter({ todo: (c) => c.state === "new", draft: (c) => c.state === "draft", queued: (c) => c.state === "queued",
    replied: (c) => c.state === "replied", question: (c) => c.is_question && c.state !== "replied" && c.state !== "ignored", all: () => true }[k]).length]));
  const logged = S.state.accounts.filter((a) => a.has_login && a.status !== "demo" && C.auto_send_platforms.includes(a.platform));
  const posts = S.data ? S.data.works.flatMap((w) => w.posts) : [];
  const rule = S.editRule;
  return `<div class="head"><h1>评论</h1><span class="sub">第 4 跳「关注 → 私域」：观众在评论区打关键词，这里按规则生成回复</span><div class="grow"></div>
      ${logged.map((a) => `<button class="small" data-act="comments-fetch" data-platform="${a.platform}" data-account="${esc(a.account)}">抓${pfName(a.platform)}「${esc(a.account)}」评论</button>`).join(" ")}
      <button class="small" data-act="comments-process">重新匹配规则</button>
      <button class="small primary" data-act="comments-send" ${counts.queued ? "" : "disabled"}>发送排队中的 ${counts.queued} 条</button></div>
    <div class="banner">默认只生成草稿：你确认后点「加入发送队列」，或复制回复手动发。自动发送是实验性的（抖音、视频号），靠页面文字定位，平台改版会失败，失败的会退回草稿。
      小红书只出草稿不自动发。回复里不要留微信号、手机号和外链，指向主页置顶或私信。</div>
    <div class="grid k2" style="align-items:start"><div>
      <div class="seg" style="margin-bottom:12px">${COMMENT_TABS.map(([k, t]) => `<button data-act="comment-tab" data-v="${k}" class="${S.commentTab === k ? "on" : ""}">${t} ${counts[k] || ""}</button>`).join("")}</div>
      ${list.length ? list.slice(0, 100).map((c) => `<div class="card comment">
        <div class="row">${pfDot(c.platform)}<b>${esc(c.nick || "（匿名）")}</b>${c.is_question ? `<span class="tag">? 提问</span>` : ""}<span class="grow"></span><span class="sub">${dt(c.ctime)}</span></div>
        <div class="sub">${esc(cleanTitle(c.post_title || "")).slice(0, 40)}</div><p style="margin:6px 0">${esc(c.text)}</p>
        ${c.error ? `<p class="warn-text">上次自动发送失败：${esc(c.error)}</p>` : ""}
        ${c.state !== "replied" && c.state !== "ignored" ? `<textarea data-reply="${c.platform}|${esc(c.comment_id)}" style="min-height:56px" placeholder="回复内容">${esc(c.reply || "")}</textarea>
        <div class="row" style="margin-top:6px"><button class="small" data-act="comment-copy" data-key="${c.platform}|${esc(c.comment_id)}">复制回复</button>
          <button class="small" data-act="comment-state" data-state="replied" data-key="${c.platform}|${esc(c.comment_id)}">标记已回复</button>
          ${C.auto_send_platforms.includes(c.platform) && c.account ? `<button class="small" data-act="comment-state" data-state="queued" data-key="${c.platform}|${esc(c.comment_id)}">加入发送队列</button>` : ""}
          <button class="small" data-act="comment-state" data-state="ignored" data-key="${c.platform}|${esc(c.comment_id)}">忽略</button></div>`
        : `<div class="sub">${c.state === "replied" ? "已回复：" + esc(c.reply || "") : "已忽略"}</div>`}</div>`).join("") : `<div class="card sub">这里没有评论。</div>`}
    </div><div>
      <div class="card"><h2>评论区高频提问 <span class="sub" style="font-weight:400">会进下周计划（答疑型）</span></h2>
        ${C.questions.length ? `<ul class="advice">${C.questions.map(([q, n]) => `<li>${n} 条 · ${esc(q)}</li>`).join("")}</ul>` : `<p class="sub">还没有识别到提问。</p>`}</div>
      <div class="card"><h2>手动粘贴评论</h2><p class="hint">小红书、或者自动抓取失败时用：从后台复制评论粘进来，一行一条，可写成「昵称：内容」。</p>
        <div class="row"><select id="imp-platform" style="width:110px">${PF.map((p) => `<option value="${p}">${pfName(p)}</option>`).join("")}</select>
          <select id="imp-post" class="grow"><option value="">（不指定作品）</option>${posts.map((p) => `<option value="${esc(p.post_id)}" data-platform="${p.platform}">${pfName(p.platform)} · ${esc(cleanTitle(p.title).slice(0, 30))}</option>`).join("")}</select></div>
        <textarea id="imp-text" style="margin-top:8px" placeholder="小王：求 skill&#10;怎么领？"></textarea><button style="margin-top:8px" data-act="comments-import">导入并匹配规则</button></div>
      <div class="card"><h2>自动回复规则</h2>
        ${C.rules.map((r) => `<div class="row rule"><span class="status ${r.enabled ? "ok" : ""}">${r.enabled ? "启用" : "停用"}</span><b>${esc(r.name)}</b>
          <span class="sub grow">关键词：${esc(r.keywords)}${r.is_intent ? " · 计入领取意向" : ""}${r.auto_send ? " · 自动发送" : ""}${r.use_ai ? " · 大模型" : ""}</span>
          <button class="small" data-act="rule-edit" data-id="${r.id}">编辑</button></div>`).join("")}
        <button class="small" data-act="rule-edit" data-id="">+ 新规则</button>
        ${rule ? `<div class="rule-form"><label class="field"><span>规则名</span><input id="r-name" value="${esc(rule.name || "")}"></label>
          <label class="field"><span>触发关键词（逗号分隔，包含任一个就触发，不分大小写）</span><input id="r-kw" value="${esc(rule.keywords || "")}"></label>
          <label class="field"><span>排除词（包含就不回）</span><input id="r-ex" value="${esc(rule.exclude || "")}"></label>
          <label class="field"><span>适用平台</span><div class="row">${PF.map((p) => `<label><input type="checkbox" class="r-pf" value="${p}" ${(rule.platforms || "douyin,xhs,channels").includes(p) ? "checked" : ""}>${pfName(p)}</label>`).join("")}</div></label>
          <label class="field"><span>回复模板（一行一条，随机选一条）。{甲|乙} 随机选一个词，{nick} 对方昵称，{asset} 领取的资料，{path} 去哪领</span>
            <textarea id="r-tpl" style="min-height:110px">${esc((rule.templates || []).join("\n"))}</textarea></label>
          <label class="field"><input type="checkbox" id="r-ai" ${rule.use_ai ? "checked" : ""}>用大模型写回复（需要在「设置 → AI → 高级」里接模型接口；没接就用模板）</label>
          <label class="field"><input type="checkbox" id="r-intent" ${rule.is_intent ? "checked" : ""}>命中的评论计入「领取意向」（漏斗 S5）</label>
          <label class="field"><input type="checkbox" id="r-auto" ${rule.auto_send ? "checked" : ""}>自动发送（实验性，只对抖音、视频号生效）</label>
          <label class="field"><span>每天最多自动发送</span><input id="r-cap" type="number" min="1" max="200" value="${rule.daily_cap || 20}" style="width:120px"></label>
          <label class="field"><input type="checkbox" id="r-on" ${rule.enabled !== 0 ? "checked" : ""}>启用</label>
          <div class="row"><button class="primary" data-act="rule-save">保存规则</button>${rule.id ? `<button class="danger" data-act="rule-delete">删除</button>` : ""}<button data-act="rule-cancel">取消</button></div></div>` : ""}
      </div></div></div>`;
}

// ---------- 今天 ----------
function todayPage() {
  const T = S.today;
  if (!T) return `<div class="card sub">加载中…</div>`;
  const stepAttrs = (a) => a.page ? `data-act="go" data-page="${a.page}"` : a.content ? `data-act="${a.publish ? "open-publish" : "open-content"}" data-id="${a.content}"`
    : `data-act="${a.act}" data-ids="${(a.ids || []).join(",")}"`;
  const steps = T.steps.length ? T.steps.map((x, i) => `<div class="step ${i === 0 ? "first" : ""} ${x.urgent ? "urgent" : ""}"><div class="n">${i + 1}</div>
      <div><div class="t">${esc(x.title)}</div><div class="sub">${esc(x.why)}</div></div>
      <button class="${i === 0 ? "primary" : ""}" ${stepAttrs(x.action)}>${esc(x.action.label)}</button></div>`).join("")
    : `<p class="sub" style="margin:0">这一轮的事都做完了。数据每天会自动采集，验收结果出来后这里会提醒你写结论。</p>`;
  const setup = T.setup_done ? "" : `<div class="card"><h2>第一次用：先把这几件事准备好</h2>${T.setup.map((x) => `<div class="setup-item">
      <span class="${x.done ? "ok" : "todo"}">${x.done ? "✓" : "○"}</span><div class="grow"><b>${esc(x.title)}</b>${x.optional ? ` <span class="hint">可选</span>` : ""}<div class="hint">${esc(x.why)}</div></div>
      ${x.done ? "" : `<button class="small" data-act="go" data-page="${x.action.page}">${esc(x.action.label)}</button>`}</div>`).join("")}</div>`;
  const today = new Date().toISOString().slice(0, 10);
  const cal = `<div class="card"><h2>接下来 7 天的排期</h2><div class="cal">${T.calendar.map((d) => `<div class="d ${d.date === today ? "today" : ""}">
      <div class="sub">周${d.weekday} ${d.date.slice(5)}</div>${d.items.map((it) => `<div class="it" data-act="open-content" data-id="${it.id}">${it.priority ? `<span class="tag pri-${it.priority}">${it.priority}</span> ` : ""}${esc(it.title.replace(/^【[^】]*】/, "").slice(0, 22))}
      <div class="hint">${esc(it.stage)}${it.has_script ? "" : " · 未出稿"}</div></div>`).join("") || `<div class="hint" style="margin-top:6px">—</div>`}</div>`).join("")}</div></div>`;
  const trendRows = Object.entries(T.trend).filter(([, s]) => s.length).map(([pf, s]) => {
    const fmt = (v, m) => v == null ? "—" : m === "views" ? num(v) : (v * 100).toFixed(m === "follow_rate" ? 2 : 1) + "%";
    const arrow = (m) => s.length > 1 && s[s.length - 1][m] != null && s[s.length - 2][m] != null ? (s[s.length - 1][m] > s[s.length - 2][m] ? `<span class="up">▲</span>` : s[s.length - 1][m] < s[s.length - 2][m] ? `<span class="down">▼</span>` : "") : "";
    return `<div style="margin-bottom:10px"><div class="sub">${pfDot(pf)}${pfName(pf)} · 按发布周的中位数（同龄快照）</div><div class="trend">
      ${["follow_rate", "finish_rate", "views"].map((m) => `<div><div class="hint">${metricName(m)} ${arrow(m)}</div><div class="row">${s.map((w) => `<div class="wk"><div class="v">${fmt(w[m], m)}</div>${w.week.slice(5)}</div>`).join("")}</div></div>`).join("")}</div></div>`;
  }).join("");
  const lessons = T.lessons.length ? T.lessons.map((L) => `<div class="row"><span class="tag v-${L.status === "有效" ? "good" : "bad"}">${L.status === "有效" ? "✓" : "✕"} ${L.status}</span><span>${esc(L.title)}</span><span class="hint">${esc(L.summary)}</span></div>`).join("")
    : `<p class="hint" style="margin:0">还没有验证出结论的打法。每条选题验收之后，「改了什么 → 有没有用」会记在这里，下一轮计划和出稿会参考。</p>`;
  return `<div class="head"><h1>今天</h1><span class="sub">本周 ${T.week} · 只看下一步</span></div>
    <div class="card"><h2>下一步</h2>${steps}</div>${setup}${cal}
    <div class="grid k2"><div class="card"><h2>数据在变好吗</h2>${trendRows || `<p class="hint" style="margin:0">有了两周以上的数据，这里会按周对比。</p>`}</div>
      <div class="card"><h2>打法库 <span class="sub" style="font-weight:400">越用越准</span></h2>${lessons}<div style="margin-top:10px"><button class="small" data-act="go" data-page="playbook">看全部打法</button></div></div></div>`;
}

// ---------- 打法库 ----------
function playbookPage() {
  const P = S.playbook;
  if (!P) return `<div class="card sub">加载中…</div>`;
  const cls = { "有效": "good", "无效": "bad", "验证中": "" };
  const list = (kind) => P.lessons.filter((L) => L.kind === kind);
  const block = (L) => `<div class="lesson"><div><span class="tag v-${cls[L.status]}">${L.status}</span>${L.confirmed ? `<div class="hint">已人工确认</div>` : ""}</div>
    <div><b>${esc(L.title)}</b><div class="sub">${esc(L.summary)}</div>
      ${L.evidence && L.evidence.length ? L.evidence.slice(0, 4).map((e) => `<div class="hint">· ${esc((e.title || "").replace(/^【[^】]*】/, "").slice(0, 24))}${e.result ? "：<b>" + esc(e.result) + "</b>" : ""}${e.conclusion ? "。" + esc(e.conclusion) : ""}</div>`).join("") : ""}
      <input class="lesson-note" data-key="${esc(L.key)}" value="${esc(L.note)}" placeholder="补一句为什么（可选），回车保存" style="margin-top:6px"></div>
    <div style="white-space:nowrap"><button class="small" data-act="lesson-verdict" data-key="${esc(L.key)}" data-v="有效">确认有效</button>
      <button class="small" data-act="lesson-verdict" data-key="${esc(L.key)}" data-v="无效">确认无效</button>
      ${L.confirmed ? `<button class="small" data-act="lesson-verdict" data-key="${esc(L.key)}" data-v="">恢复自动</button>` : ""}</div></div>`;
  const exp = list("实验"), st = list("结构");
  const cal = P.calibration.map((c) => c.ready ? `<div style="margin-bottom:12px"><div class="row"><b>${pfDot(c.platform)}${pfName(c.platform)}</b><span class="sub grow">${c.n} 条作品</span>
      <button class="small primary" data-act="calibrate" data-platform="${c.platform}">按建议更新阈值</button></div>
      <table><thead><tr><th>指标</th><th>现在（优秀 / 良好 / 及格）</th><th>建议（P90 / P50 / P25）</th></tr></thead><tbody>${c.rows.map((r) => `<tr><td>${metricName(r.metric)}</td>
      <td class="num-cell">${r.current.map((v) => fmtMetric(r.metric, v)).join(" / ")}</td><td class="num-cell">${r.suggested.map((v) => fmtMetric(r.metric, v)).join(" / ")}</td></tr>`).join("")}</tbody></table></div>`
    : `<p class="hint">${pfDot(c.platform)}${pfName(c.platform)}：现在 ${c.n} 条，攒到 ${c.need} 条后按你自己的分位数给建议。</p>`).join("");
  return `<div class="head"><h1>打法库</h1><span class="sub">每次验收都会更新：改了什么 → 有没有用。有效的会优先进计划和出稿，无效的会避开</span></div>
    <div class="card"><h2>实验打法 <span class="sub" style="font-weight:400">计划里每条只改一个变量，验收结果累积在这里；做到 2 次以上才下结论</span></h2>
      ${exp.length ? exp.map(block).join("") : `<p class="hint" style="margin:0">还没有验收过的计划选题。按计划出稿、发布，数据回来后这里会出现第一条。</p>`}</div>
    <div class="card"><h2>结构打法 <span class="sub" style="font-weight:400">同一平台里某种结构至少 3 条作品后，关注率和平台中位数比</span></h2>
      ${st.length ? st.map(block).join("") : `<p class="hint" style="margin:0">每个平台至少 5 条带标签的作品后开始统计。</p>`}</div>
    <div class="card"><h2>阈值校准 <span class="sub" style="font-weight:400">方法论：起始阈值是假设，攒够样本后按自己的分位数重设</span></h2>${cal || `<p class="hint" style="margin:0">还没有作品数据。</p>`}</div>`;
}

// ---------- 对标 ----------
const STATUS_CLASS = { new: "", studied: "info", used: "good", ignored: "" };
function benchPage() {
  const B = S.bench;
  if (!B) return `<div class="card sub">加载中…</div>`;
  const keySet = S.state.settings.tikhub_api_key_set;
  const head = `<div class="head"><h1>对标</h1><span class="sub">盯住同赛道的号，找出它们的爆款，拆成结构再拿来用</span><div class="grow"></div>
    <div class="seg">${[[7, "近 7 天"], [30, "近 30 天"], [90, "近 90 天"], [0, "全部"]].map(([v, t]) => `<button data-act="bench-days" data-v="${v}" class="${S.benchDays === v ? "on" : ""}">${t}</button>`).join("")}</div>
    <button data-act="bench-refresh" ${keySet && B.benchmarks.length ? "" : "disabled"}>刷新全部</button></div>`;
  const banner = keySet ? "" : `<div class="banner">还没有配置 TikHub：对标账号的数据要通过 TikHub 读取。<button class="small" data-act="go" data-page="settings" style="margin-left:10px">去填写 API Key</button></div>`;
  const cands = S.benchCands ? `<div class="cands"><div class="sub">选一个账号加入对标：</div>${S.benchCands.list.map((c, i) => `<div class="row cand">
      ${c.avatar ? `<img src="${esc(c.avatar)}" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}<b>${esc(c.name)}</b><span class="sub grow">${c.followers ? num(c.followers) + " 粉丝 · " : ""}${esc((c.note || "").slice(0, 40))}</span>
      <button class="small primary" data-act="bench-add" data-i="${i}">加入</button></div>`).join("")}</div>` : "";
  const add = `<div class="card"><h2>添加对标账号</h2><div class="row">
      <select id="bench-pf" style="width:110px">${PF.map((p) => `<option value="${p}">${pfName(p)}</option>`).join("")}</select>
      <div class="grow"><input id="bench-text" placeholder="抖音 / 小红书填主页链接；视频号填视频号 ID（sph 开头）、finder username 或账号名"></div>
      <input id="bench-grp" placeholder="分组（可选）" style="width:130px"><button class="primary" data-act="bench-resolve" ${keySet ? "" : "disabled"}>查找</button></div>
    <p class="hint" style="margin-bottom:0">每次读取都会在 TikHub 扣费：抖音约 $${B.prices.douyin}/次，小红书约 $${B.prices.xhs}/次，视频号 $${B.prices.channels}/次。按现在的账号，每天自动刷新约 $${B.daily_cost}；近 30 天共调用 ${B.usage.calls} 次，约 $${B.usage.cost}。</p>${cands}</div>`;
  const accounts = B.benchmarks.length ? `<div class="card"><h2>对标账号 <span class="sub" style="font-weight:400">中位互动 = 最近作品（不含置顶）赞评转藏之和的中位数，爆款 = 达到它的 ${B.ratio} 倍</span></h2>
    <div class="table-wrap"><table><thead><tr><th>账号</th><th>粉丝</th><th>作品</th><th>中位互动</th><th>爆款</th><th>上次读取</th><th></th></tr></thead><tbody>
    ${B.benchmarks.map((b) => `<tr><td>${pfDot(b.platform)}<b>${esc(b.name)}</b>${b.grp ? ` <span class="chip">${esc(b.grp)}</span>` : ""}${b.enabled ? "" : ` <span class="tag">已停用</span>`}
        ${b.last_error ? `<div class="hint warn-text">${esc(b.last_error)}</div>` : ""}</td>
      <td class="num-cell">${num(b.followers)}</td><td class="num-cell">${b.post_count}</td><td class="num-cell">${b.median == null ? `<span class="hint">不到 5 条</span>` : num(b.median)}</td>
      <td class="num-cell">${b.hits}</td><td class="sub">${dt(b.last_fetch)}</td>
      <td style="text-align:right;white-space:nowrap"><button class="small" data-act="bench-refresh" data-id="${b.id}" ${keySet ? "" : "disabled"}>刷新</button>
        <button class="small" data-act="bench-toggle" data-id="${b.id}" data-on="${b.enabled ? 0 : 1}">${b.enabled ? "停用" : "启用"}</button>
        <button class="small danger" data-act="bench-del" data-id="${b.id}" data-name="${esc(b.name)}">删除</button></td></tr>`).join("")}</tbody></table></div></div>` : "";
  const st = B.structure;
  const bars = (field, title) => `<div><div class="sub">${title}</div>${st[field].length ? st[field].slice(0, 5).map(([v, n]) => `<div class="bar-row"><span>${esc(v)}</span><span class="bar" style="width:${Math.round(n / st[field][0][1] * 100)}%"></span><span class="sub">${n}</span></div>`).join("") : `<div class="hint">—</div>`}</div>`;
  const structCard = st.tagged ? `<div class="card"><h2>爆款结构分布 <span class="sub" style="font-weight:400">拆解过的 ${st.tagged} 条爆款里各标签出现的次数，对标号验证过的结构复刻风险最低</span></h2>
    <div class="grid k4" style="margin:0">${bars("motif", "母题")}${bars("archetype", "原型")}${bars("hook_open", "开头钩")}${bars("hook_end", "结尾钩")}</div></div>` : "";
  const untagged = B.hits.filter((p) => p.status === "new").map((p) => [p.platform, p.post_id]);
  const hits = `<div class="card"><div class="row" style="margin-bottom:10px"><h2 style="margin:0">对标爆款</h2><span class="sub">${B.hits.length} 条，按爆款倍数排序</span><span class="grow"></span>
      ${untagged.length ? `<button class="small" data-act="bench-analyze-all">AI 拆解未拆解的 ${Math.min(untagged.length, 10)} 条</button>` : ""}</div>
    ${B.hits.length ? `<div class="table-wrap"><table><thead><tr><th>作品</th><th>互动</th><th>倍数</th><th>结构</th><th>状态</th></tr></thead><tbody>
    ${B.hits.map((p, i) => `<tr class="click" data-act="bench-open" data-i="${i}"><td class="title-cell"><div class="t" title="${esc(p.title)}">${esc(cleanTitle(p.title))}</div>
        <div class="sub">${pfDot(p.platform)}${esc(p.bench_name)} · ${day(p.post_time)}${p.duration_sec ? " · " + sec(p.duration_sec) : ""}</div></td>
      <td class="num-cell">${num(p.inter)}<div class="hint">赞 ${num(p.latest.likes)} · 评 ${num(p.latest.comments)} · 转 ${num(p.latest.shares)} · 藏 ${num(p.latest.collects)}</div></td>
      <td class="num-cell"><b>×${p.ratio.toFixed(1)}</b></td><td>${tagChips(p.tags) || `<span class="hint">未拆解</span>`}</td>
      <td><span class="tag v-${STATUS_CLASS[p.status]}">${B.status_names[p.status]}</span></td></tr>`).join("")}</tbody></table></div>`
      : `<p class="sub">${B.benchmarks.length ? "这段时间没有达到爆款倍数的作品。新加的账号要读到 5 条以上作品才算得出中位数。" : "先添加几个对标账号。"}</p>`}</div>`;
  return head + banner + add + accounts + structCard + hits;
}
function benchDrawer(p) {
  const L = p.latest;
  return `<div class="panel"><div class="head"><div class="grow"><h1 style="font-size:17px">${esc(cleanTitle(p.title).slice(0, 80))}</h1>
      <div class="sub">${pfDot(p.platform)}${esc(p.bench_name)} · ${dt(p.post_time)} 发布${p.duration_sec ? " · 时长 " + sec(p.duration_sec) : ""}${p.url ? ` · <a href="${esc(p.url)}" target="_blank" rel="noreferrer">打开原作品</a>` : ""}</div></div>
      <button data-act="close-drawer">关闭</button></div>
    <div class="card"><div class="row" style="align-items:flex-start">${p.cover ? `<img class="cover" src="${esc(p.cover)}" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}<div class="grow">
      <div class="scores"><div><div class="sub">互动</div><div class="big">${num(p.inter)}</div></div><div><div class="sub">爆款倍数</div><div class="big">×${p.ratio ? p.ratio.toFixed(1) : "—"}</div></div>
        <div><div class="sub">每天新增</div><div class="big">${p.growth_per_day == null ? "—" : num(p.growth_per_day)}</div></div><div><div class="sub">状态</div><div><span class="tag v-${STATUS_CLASS[p.status]}">${S.bench.status_names[p.status]}</span></div></div></div>
      <p class="hint">赞 ${num(L.likes)} · 评论 ${num(L.comments)} · 转发 ${num(L.shares)} · 收藏 ${num(L.collects)}${L.plays ? " · 播放 " + num(L.plays) : ""}</p>
      <p style="white-space:pre-wrap;margin:0">${esc(p.title)}</p></div></div></div>
    <div class="card"><h2>拆解：给它打和自己作品同一套标签</h2><div class="grid k3" style="margin:0">${["motif", "archetype", "hook_open", "hook_mid", "hook_end", "duration_bin"].map((f) => tagSelect(f, p.tags[f], "data-btag")).join("")}</div>
      ${p.tags.archetype ? comboHint(p.tags) : ""}
      <label class="field"><span>拆解笔记（开头怎么抓人、结构怎么推进、结尾留了什么动作、为什么能爆、我们怎么借）</span><textarea id="bench-analysis" style="min-height:180px">${esc(p.analysis || "")}</textarea></label>
      <div class="row"><button class="primary" data-act="bench-save">保存拆解</button>
        <button data-act="bench-analyze">AI 拆解</button>
        <span class="grow"></span><button data-act="bench-ignore">${p.status === "ignored" ? "取消忽略" : "忽略这条"}</button>
        <button class="primary" data-act="bench-use" ${p.tags.archetype ? "" : "disabled title='先拆解出原型'"}>${p.content_id ? "打开复刻选题" : "用这个结构做一条"}</button></div>
      <p class="hint">借结构不借选题：结构标签会带到「创作」里，选题要换成自己能兑现的内容，并先过定位三闸。</p></div></div>`;
}

// ---------- 复盘 ----------
function reviewPage() {
  const running = S.state.jobs.some((j) => j.type === "review" && (j.status === "pending" || j.status === "running"));
  return `<div class="head"><h1>复盘</h1><div class="grow"></div>
    <select id="rv-days" style="width:auto"><option value="7">近 7 天</option><option value="14">近 14 天</option><option value="30">近 30 天</option><option value="0">全部</option></select>
    <button data-act="review" data-ai="">规则复盘</button><button class="primary" data-act="review" data-ai="1">AI 复盘</button></div>
  ${(S.state.agent_pending || []).filter((t) => t.kind === "review").map(agentHint).join("")}
  <div class="card"><p style="margin:0">按方法论的顺序：定位检查 → 数据校验 → 五段漏斗（找本周唯一要改的那一段）→ 单条判定 → 四个判断 → 结构矩阵 → 上期计划验收 → 评论区信号 → 下一步计划。
    复盘里的下一步只是预览，去「计划」页生成后才会进入创作流程。</p></div>
  ${running ? `<div class="card sub">正在生成复盘…</div>` : ""}
  ${S.reviews.map((r) => `<div class="card"><div class="row" style="margin-bottom:10px"><span class="tag">${r.kind === "ai" ? "AI 复盘" : "规则复盘"}</span><span class="sub">${dt(r.created_at)} 生成</span><span class="grow"></span><button class="small danger" data-act="review-del" data-id="${r.id}">删除</button></div><div class="md">${md(r.content)}</div></div>`).join("")
    || (running ? "" : `<div class="card empty"><p>还没有复盘。选一个时间范围，点右上角生成。</p></div>`)}`;
}

// ---------- 发布 ----------
function realAccounts() { return S.state.accounts.filter((a) => a.status !== "demo"); }
function publishPage() {
  const f = S.form, accs = realAccounts();
  const isNote = f.kind === "note";
  const linked = f.content_id && S.contents.find((c) => c.id === f.content_id);
  const targets = accs.length ? accs.map((a) => {
    const key = a.platform + "/" + a.account, def = S.state.platforms[a.platform];
    const blocked = isNote && !def.note ? "不支持图文" : !a.has_login ? "还没登录" : "";
    return `<label class="target"><input type="checkbox" data-target="${esc(key)}" ${f.targets[key] && !blocked ? "checked" : ""} ${blocked ? "disabled" : ""}>
      ${pfDot(a.platform)}<b>${pfName(a.platform)}</b>「${esc(a.account)}」
      ${blocked ? `<span class="hint">${blocked}</span>` : ""}
      ${def.draft && !isNote ? `<span class="grow"></span><label class="hint"><input type="checkbox" data-draft="${esc(key)}" ${f.drafts[key] ? "checked" : ""}>只存草稿，不直接发</label>` : ""}
      ${a.platform === "xhs" ? `<span class="grow"></span><span class="hint">小红书对自动发布较敏感，建议保持显示浏览器窗口</span>` : ""}</label>`;
  }).join("") : `<div class="sub">还没有账号，请先到「账号」页添加并登录。</div>`;
  return `<div class="head"><h1>发布</h1></div>
  ${S.state.sau_installed ? "" : `<div class="banner">还没有安装发布引擎。请先关闭工作台，双击运行「安装.cmd」，再重新启动。</div>`}
  ${linked ? `<div class="banner">这次发布关联选题「${esc(linked.title)}」，采集到数据后会自动验收。<button class="small" data-act="publish-unlink" style="margin-left:10px">不关联</button></div>` : ""}
  <div class="grid k2"><div class="card">
    <label class="field"><span>内容类型</span><div class="seg"><button data-act="kind" data-v="video" class="${isNote ? "" : "on"}">视频</button><button data-act="kind" data-v="note" class="${isNote ? "on" : ""}">图文</button></div></label>
    <label class="field"><span>${isNote ? "图片文件（每行一个完整路径）" : "视频文件（完整路径）"}</span>
      <div class="row"><div class="grow">${isNote ? `<textarea data-f="files" style="min-height:70px">${esc(f.files)}</textarea>` : `<input data-f="files" value="${esc(f.files)}" placeholder="例如 D:\\视频\\成片.mp4">`}</div>
      <button data-act="pick" data-kind="${isNote ? "image" : "video"}" data-many="${isNote ? 1 : ""}" data-to="files">选择文件</button></div></label>
    ${isNote ? "" : coverSection(f)}
    <label class="field"><span>标题</span><input data-f="title" value="${esc(f.title)}" maxlength="100"></label>
    <label class="field"><span>${isNote ? "正文" : "简介"}</span><textarea data-f="desc">${esc(f.desc)}</textarea></label>
    <label class="field"><span>话题（用逗号或空格分开，不用写 #）</span><input data-f="tags" value="${esc(f.tags)}"></label>
  </div><div class="card">
    <label class="field"><span>发布到</span><div class="targets">${targets}</div></label>
    <p class="hint">三个平台分工不同：${Object.entries(S.method.roles).map(([k, v]) => `${pfName(k)}${esc(v)}`).join("；")}。</p>
    <label class="field"><span>定时发布（可选，留空就是立即发布；由平台在这个时间自动发出，不需要一直开着电脑）</span><input type="datetime-local" data-f="schedule" value="${esc(f.schedule)}"></label>
    <label class="field"><input type="checkbox" data-f="headless" ${f.headless ? "checked" : ""}>后台运行，不显示浏览器窗口</label>
    <button class="primary" data-act="publish">开始发布</button>
    <p class="hint">发布时会依次打开各平台的发布页自动填写并提交，同一时间只跑一个。进度在下方和「任务」页查看。</p>
  </div></div>
  <div class="card"><h2>最近的发布</h2><div id="live-publish"></div></div>`;
}
const MODE_NAME = { crop: "裁剪", pad: "完整画面 + 模糊背景" };
function coverSection(f) {
  const P = S.coverPreview;
  const preview = P ? `<div class="cover-prev"><div><div class="hint">原图 ${P.source.w}×${P.source.h}（${esc(P.origin)}）。<b>点一下画面里最重要的位置</b>，裁剪会以它为中心</div>
      <div class="focus-wrap" data-act="cover-focus"><img src="${P.source.url}"><span class="focus-dot" style="left:${P.focus[0] * 100}%;top:${P.focus[1] * 100}%"></span></div></div>
    <div class="cover-outs">${P.items.map((it) => `<div><img src="${it.url}" style="aspect-ratio:${it.w}/${it.h}"><div class="hint">${pfDot(it.platform)}${esc(it.name)} · ${esc(it.label)}<br>${it.w}×${it.h} · ${MODE_NAME[it.mode]}${it.mode === "crop" ? `，保留 ${Math.round(it.keep * 100)}% 画面` : ""}</div></div>`).join("")}</div></div>` : "";
  return `<div class="field"><span>封面（可选）：一张原图，按每个平台的规格各生成一份，尺寸精确匹配</span>
    <div class="row"><div class="grow"><input data-f="cover" value="${esc(f.cover)}" placeholder="封面图的完整路径"></div><button data-act="pick" data-kind="image" data-to="cover">选择图片</button></div>
    <div class="row" style="margin-top:8px;white-space:nowrap"><span class="hint">没有封面图时，从视频第</span><input data-f="cover_sec" value="${esc(f.cover_sec)}" style="width:64px" placeholder="秒"><span class="hint">秒截一帧</span></div>
    <div class="row" style="margin-top:8px"><select data-f="cover_mode" class="grow">${[["auto", "自动（裁掉太多就用模糊背景）"], ["crop", "总是裁剪"], ["pad", "总是完整画面 + 模糊背景"]].map(([v, t]) => `<option value="${v}" ${f.cover_mode === v ? "selected" : ""}>${t}</option>`).join("")}</select>
      <button data-act="cover-preview" style="white-space:nowrap">预览各平台封面</button></div>${preview}
    <p class="hint">抖音竖封面 3:4（1080×1440）；视频号个人主页卡片 3:4 和动态封面 4:3（1440×1080）；小红书默认 3:4，可在设置里改。不预览也可以，发布时会自动按这些规格生成。</p></div>`;
}
function jobsTable(list, empty) {
  if (!list.length) return `<div class="sub">${empty}</div>`;
  return `<table><tbody>${list.map((j) => `<tr><td style="width:110px"><span class="status ${j.status}">${STATUS_NAME[j.status]}</span></td>
    <td>${esc(jobTitle(j))}${j.params.schedule ? ` <span class="tag">定时 ${esc(j.params.schedule.replace("T", " "))}</span>` : ""}${j.params.draft ? ` <span class="tag">草稿</span>` : ""}${j.params.auto ? ` <span class="tag">自动</span>` : ""}
      ${S.openJob === j.id ? `<pre class="log">${esc(S.jobLog || "（没有输出）")}</pre>` : j.status === "failed" || j.status === "running" || (j.status === "done" && j.type === "plan") ? `<div class="hint">${esc((j.tail || "").trim().split("\n").pop())}</div>` : ""}</td>
    <td class="sub num-cell" style="width:110px">${dt(j.created_at)}</td>
    <td style="width:150px;text-align:right"><button class="small" data-act="job-log" data-id="${j.id}">${S.openJob === j.id ? "收起" : "日志"}</button>
      ${j.status === "pending" || j.status === "running" ? `<button class="small danger" data-act="job-cancel" data-id="${j.id}">取消</button>` : ""}</td></tr>`).join("")}</tbody></table>`;
}

// ---------- 账号 ----------
function accountsPage() {
  return `<div class="head"><h1>账号</h1></div>
  ${S.state.sau_installed ? "" : `<div class="banner">还没有安装发布引擎，登录和发布暂时不能用。请先关闭工作台，双击运行「安装.cmd」，再重新启动。</div>`}
  <div class="card"><h2>添加账号</h2><div class="row">
    <select id="acc-platform" style="width:140px">${PF.map((p) => `<option value="${p}">${pfName(p)}</option>`).join("")}</select>
    <div class="grow"><input id="acc-name" placeholder="给这个账号起个备注名，例如：主号" maxlength="30"></div><button class="primary" data-act="acc-add">添加</button></div>
    <p class="hint" style="margin-bottom:0">备注名只是你自己区分账号用的。添加后点「扫码登录」，会弹出浏览器窗口，用手机扫码即可。登录一次，发布、采集、抓评论都能用。</p></div>
  <div class="card"><h2>我的账号</h2><div id="live-accounts"></div></div>`;
}
function accountsTable() {
  const list = S.state.accounts;
  if (!list.length) return `<div class="sub">还没有账号。</div>`;
  return `<table><thead><tr><th>平台</th><th>备注名</th><th>登录状态</th><th>作品数</th><th>上次采集</th><th></th></tr></thead><tbody>${list.map((a) => {
    const d = `data-platform="${a.platform}" data-account="${esc(a.account)}"`;
    const status = a.status === "unknown" && a.has_login ? "ok" : a.status;
    return `<tr><td>${pfDot(a.platform)}${pfName(a.platform)}</td><td>${esc(a.account)}</td>
      <td><span class="status ${status}">${ACC_STATUS[status] || status}</span>${a.last_error ? `<div class="hint">${esc(a.last_error)}</div>` : ""}</td>
      <td class="num-cell">${a.posts}</td><td class="sub">${dt(a.last_collect)}</td>
      <td style="text-align:right;white-space:nowrap">${a.status === "demo" ? `<button class="small" data-act="demo-clear">清除示例数据</button>` :
        `<button class="small" data-act="acc-login" ${d}>扫码登录</button> <button class="small" data-act="acc-check" ${d}>检查登录</button>
         <button class="small" data-act="acc-collect" ${d}>采集</button> <button class="small danger" data-act="acc-del" ${d}>删除</button>`}</td></tr>`;
  }).join("")}</tbody></table>`;
}

// ---------- 任务 / 设置 ----------
function agentPage() {
  const A = S.agent;
  if (!A) return `<div class="card sub">加载中…</div>`;
  return `<div class="head"><h1>AI 待办</h1><span class="sub">出稿、AI 复盘、对标拆解都在这里排队，由你正在用的 AI 助手来做</span></div>
  <div class="card"><h2>怎么让 AI 助手处理</h2>
    <ol class="steps"><li>在 Claude Code、Codex 或 WorkBuddy 里打开 NingLoop 所在的文件夹（它们会读到文件夹里的 AGENTS.md / CLAUDE.md，知道怎么做）。</li>
      <li>说一句「<b>${ASK}</b>」。想直接写一条新选题，就说「用 NingLoop 写个脚本：你的选题」。</li>
      <li>它写完会自动交回来，稿子出现在「创作」里对应的选题上，复盘出现在「复盘」页。</li></ol>
    <div class="row"><button class="primary" data-act="copy-ask">复制这句话</button><span class="hint">用网页版聊天的 AI 也行：点下面的「复制任务说明」发给它，再把它的回答「粘贴结果」交回来。</span></div></div>
  <div class="card"><h2>待处理 ${A.pending.length ? A.pending.length + " 条" : ""}</h2>
    ${A.pending.length ? `<table><tbody>${A.pending.map((t) => `<tr><td style="width:60px" class="sub">#${t.id}</td><td>${esc(t.label)}</td><td class="sub" style="width:110px">${dt(t.created_at)}</td>
      <td style="text-align:right;white-space:nowrap"><button class="small" data-act="agent-copy" data-id="${t.id}">复制任务说明</button>
        <button class="small" data-act="agent-paste" data-id="${t.id}">粘贴结果</button><button class="small danger" data-act="agent-cancel" data-id="${t.id}">取消</button></td></tr>`).join("")}</tbody></table>`
      : `<p class="sub">没有待办。在「创作」里点选题的「出稿」，或在「计划」页点「本周选题全部出稿」。</p>`}</div>
  ${A.recent.length ? `<div class="card"><h2>最近完成</h2>${A.recent.map((t) => `<div class="row"><span class="status done">#${t.id}</span><span class="grow">${esc(t.note || "")}</span><span class="sub">${dt(t.done_at)}</span></div>`).join("")}</div>` : ""}`;
}
const jobsPage = () => `<div class="head"><h1>任务</h1><span class="sub">登录、发布、采集、抓评论都在这里排队，一次只执行一个</span></div><div class="card"><div id="live-jobs"></div></div>`;
function settingsPage() {
  const s = S.state.settings, M = S.method, prof = M.profile, P = M.params;
  const hasDemo = S.state.accounts.some((a) => a.status === "demo");
  const thRows = ["views", "finish_rate", "follow_rate", "share_rate", "comment_rate", "intent_rate"].map((m) => `<tr><td>${metricName(m)}</td>
    ${PF.map((pf) => `<td><div class="row">${P.thresholds[pf][m].map((v, i) => `<input data-th="${pf}|${m}|${i}" value="${m === "views" ? v : +(v * 100).toFixed(3)}" style="width:72px">`).join("")}</div></td>`).join("")}</tr>`).join("");
  return `<div class="head"><h1>设置</h1></div>
  <div class="card"><h2>定位与用户卡 <span class="sub" style="font-weight:400">发布前必须填完；这三句话的唯一用途是否定选题。带「待确认」的是我根据最近作品推断的初稿</span></h2>
    <label class="field"><span>账号名</span><input data-p="account" value="${esc(prof.account || "")}"></label>
    <div class="grid k2" style="margin:0">${M.profile_fields.map(([k, name, hint]) => `<label class="field"><span>${esc(name)}</span><textarea data-p="${k}" style="min-height:60px" placeholder="${esc(hint)}">${esc(prof[k] || "")}</textarea></label>`).join("")}</div>
    <button class="primary" data-act="save-profile">保存定位卡</button></div>
  <div class="card"><h2>复盘参数 <span class="sub" style="font-weight:400">阈值是起始假设：每格三个数依次是优秀线、良好线、及格线（比率填百分数）。累计 4 周后用自身分位数重设</span></h2>
    <div class="table-wrap"><table><thead><tr><th>指标</th>${PF.map((p) => `<th>${pfDot(p)}${pfName(p)}</th>`).join("")}</tr></thead><tbody>${thRows}</tbody></table></div>
    <div class="grid k3" style="margin:12px 0 0"><label class="field"><span>流量权重（综合分 = 权重 × 流量力 + (1 − 权重) × 转化力）</span><input id="pm-fw" value="${P.flow_weight}"></label>
      <label class="field"><span>同龄对比用哪个快照</span><select id="pm-slot">${["D1", "D3", "D7"].map((x) => `<option ${x === P.compare_slot ? "selected" : ""}>${x}</option>`).join("")}</select></label>
      <label class="field"><span>每周最多几条 P0</span><input id="pm-p0" type="number" min="1" max="10" value="${P.max_p0}"></label></div>
    <button class="primary" data-act="save-params">保存复盘参数</button></div>
  <div class="card"><h2>创作工作流 <span class="sub" style="font-weight:400">一行一个阶段，写成「阶段名：检查项1；检查项2」。按你的实际步骤改</span></h2>
    <div class="grid k2" style="margin:0">${Object.entries(M.workflows).map(([k, v]) => `<label class="field"><span>${esc(v.name)}</span><textarea data-wf="${k}" style="min-height:220px">${esc(M.workflows_text[k])}</textarea></label>`).join("")}</div>
    <button class="primary" data-act="save-workflows">保存工作流</button></div>
  <div class="grid k2"><div class="card"><h2>数据采集</h2>
    <label class="field"><input type="checkbox" id="st-auto" ${s.auto_collect ? "checked" : ""}>工作台开着时每天自动采集一次</label>
    <label class="field"><span>每天几点之后采集（0-23）</span><input type="number" id="st-hour" min="0" max="23" value="${s.collect_hour}"></label>
    <label class="field"><span>每个账号每次最多采集多少条作品</span><input type="number" id="st-max" min="1" max="100" value="${s.max_posts}"></label>
    <label class="field"><input type="checkbox" id="st-headless" ${s.headless ? "checked" : ""}>采集时不显示浏览器窗口（采集总失败时可以取消勾选，看看页面停在哪）</label>
    <label class="field"><span>小红书封面比例</span><select id="st-xhs-ratio">${["3:4", "1:1", "4:3", "9:16"].map((r) => `<option ${r === s.xhs_cover_ratio ? "selected" : ""}>${r}</option>`).join("")}</select></label>
    <label class="field"><span>使用本机的 Chrome（可选，填 chrome.exe 的完整路径；留空用自带浏览器）</span><input id="st-chrome" value="${esc(s.chrome_path)}"></label>
  </div><div class="card"><h2>AI</h2>
    <p>出稿、AI 复盘、对标拆解都交给你正在用的 AI 助手（Claude Code、Codex、WorkBuddy），<b>不需要另外配模型</b>。点了这些按钮会排进「AI 待办」，在助手里说「${ASK}」即可。</p>
    <details><summary class="hint">高级（可选）：不用 AI 助手时，自己接一个兼容 OpenAI 的模型接口，工作台会自动处理待办</summary>
    <p class="hint">例如 Kimi、DeepSeek、通义千问。密钥只保存在这台电脑的 data/settings.json 里。</p>
    <label class="field"><span>接口地址</span><input id="st-url" value="${esc(s.ai_base_url)}"></label>
    <label class="field"><span>模型名</span><input id="st-model" value="${esc(s.ai_model)}" placeholder="填你的服务商提供的模型名"></label>
    <label class="field"><span>密钥${s.ai_api_key_set ? "（已保存，留空表示不修改）" : ""}</span><input id="st-key" type="password" autocomplete="off"></label>
    </details>
  </div></div>
  <div class="card"><h2>对标账号（TikHub）</h2>
    <p class="hint">对标账号的公开作品通过 <a href="https://tikhub.io" target="_blank" rel="noreferrer">TikHub</a> 读取，按次计费。密钥只保存在这台电脑的 data/settings.json 里。</p>
    <div class="grid k3" style="margin:0"><label class="field"><span>API Key${s.tikhub_api_key_set ? "（已保存，留空表示不修改）" : ""}</span><input id="st-th-key" type="password" autocomplete="off"></label>
      <label class="field"><span>接口地址</span><select id="st-th-url">${["https://api.tikhub.io", "https://api.tikhub.dev"].map((u) => `<option ${u === s.tikhub_base_url ? "selected" : ""}>${u}</option>`).join("")}</select></label>
      <label class="field"><span>爆款倍数（互动达到该号中位数的几倍）</span><input id="st-th-ratio" type="number" min="2" max="20" value="${s.bench_hit_ratio}"></label>
      <label class="field"><span>每个账号每次读几页（每页一次计费，约 15–20 条）</span><input id="st-th-pages" type="number" min="1" max="5" value="${s.bench_pages}"></label>
      <label class="field"><input type="checkbox" id="st-th-auto" ${s.bench_auto ? "checked" : ""}>工作台开着时每天自动刷新一次</label></div>
    <p class="hint">国内网络连不上 api.tikhub.io 时换成 api.tikhub.dev。</p></div>
  <div class="row" style="margin-bottom:16px"><button class="primary" data-act="save-settings">保存采集、大模型与对标设置</button></div>
  <div class="card"><h2>数据</h2>
    <label class="field"><span>导入《内容数据工作台.xlsx》（读取「01 底表」「04 内容结构标签」「08 流量结构与长尾」）</span>
      <div class="row"><div class="grow"><input id="imp-path" placeholder="表格的完整路径"></div><button data-act="pick-xlsx">选择文件</button><button class="primary" data-act="import-xlsx">导入</button></div></label>
    ${hasDemo ? `<button data-act="demo-clear">清除示例数据</button>` : `<button data-act="demo-load">载入示例数据</button>`}</div>`;
}

// ---------- 渲染与轮询 ----------
const PAGES = { today: todayPage, playbook: playbookPage, dashboard, agent: agentPage, bench: benchPage, review: reviewPage, plan: planPage, create: createPage, comments: commentsPage, publish: publishPage, accounts: accountsPage, jobs: jobsPage, settings: settingsPage };
function live() {
  const set = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
  set("live-publish", jobsTable(S.state.jobs.filter((j) => j.type === "publish").slice(0, 10), "还没有发布记录。"));
  set("live-accounts", accountsTable());
  set("live-jobs", jobsTable(S.state.jobs, "还没有任务。"));
  const active = S.state.jobs.filter((j) => j.status === "pending" || j.status === "running").length;
  const badge = $("#job-badge"); badge.hidden = !active; badge.textContent = active;
  const ag = (S.state.agent_pending || []).length, ab = $("#agent-badge"); ab.hidden = !ag; ab.textContent = ag;
}
function render() {
  document.querySelectorAll(".side a").forEach((a) => a.classList.toggle("active", a.dataset.page === S.page));
  $("#main").innerHTML = PAGES[S.page]();
  live();
}
async function go(page) {
  S.page = page;
  if (["dashboard", "comments", "create"].includes(page) || !S.data) await loadData();
  if (page === "review") S.reviews = (await api("/api/reviews")).reviews;
  if (["plan", "create", "publish"].includes(page)) S.contents = (await api("/api/contents")).contents;
  if (page === "comments") S.comments = await api("/api/comments");
  if (page === "bench") S.bench = await api(`/api/bench?days=${S.benchDays}`);
  if (page === "agent") S.agent = await api("/api/agent");
  if (page === "today") S.today = await api("/api/today");
  if (page === "playbook") S.playbook = await api("/api/playbook");
  render();
}
let lastDone = null, pollTimer;
async function poll() {
  try {
    S.state = await api("/api/state");
    if (S.openJob) S.jobLog = (await api("/api/jobs/" + S.openJob)).log;
    const done = Math.max(0, ...S.state.jobs.filter((j) => j.finished_at).map((j) => j.finished_at));
    const changed = lastDone !== null && done > lastDone;
    lastDone = done;
    // 有任务刚结束：刷新受影响的页面（正在编辑的表单不整页重画）
    if (changed && !["publish", "settings"].includes(S.page) && $("#drawer").hidden) await go(S.page);
    else if (changed && S.benchOpen && !$("#drawer").hidden && !S.editing) await reopenBench();
    else if (changed && S.editing && !$("#drawer").hidden) {
      S.contents = (await api("/api/contents")).contents;
      const fresh = S.contents.find((c) => c.id === S.editing.id);
      if (fresh && fresh.script !== S.editing.script) { openContent(fresh); toast("稿子已更新"); }
    }
    live();
  } catch (e) { /* 服务暂时没响应，下次再试 */ }
  const active = S.state && S.state.jobs.some((j) => j.status === "pending" || j.status === "running");
  clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, active ? 1500 : 6000);
}
async function refresh() {
  S.state = await api("/api/state"); live();
  clearTimeout(pollTimer); pollTimer = setTimeout(poll, 1000);  // 刚发起了操作，尽快再查一次进度
}
async function coverPreview() {
  const f = S.form;
  const platforms = Object.keys(f.targets).filter((k) => f.targets[k]).map((k) => k.split("/")[0]);
  S.coverPreview = await api("/api/cover/preview", { cover: f.cover, files: f.files.split("\n"), cover_sec: f.cover_sec, cover_focus: f.cover_focus, cover_mode: f.cover_mode,
    platforms: platforms.length ? [...new Set(platforms)] : null });
  render();
}
async function reloadMethod() { S.method = await api("/api/method"); }
async function reopenBench() {
  S.bench = await api(`/api/bench?days=${S.benchDays}`);
  const p = S.bench.hits.find((x) => x.platform === S.benchOpen.platform && x.post_id === S.benchOpen.post_id);
  if (p) { S.benchOpen = p; $("#drawer").innerHTML = benchDrawer(p); } else { $("#drawer").hidden = true; }
  if (S.page === "bench") render();
}

// ---------- 事件 ----------
const acc = (el) => ({ platform: el.dataset.platform, account: el.dataset.account });
const keyOf = (k) => { const [platform, ...rest] = k.split("|"); return { platform, comment_id: rest.join("|") }; };
const ACTIONS = {
  go: (el) => go(el.dataset.page),
  days: async (el) => { S.days = +el.dataset.v; await go("dashboard"); },
  "collect-all": async () => { await api("/api/collect", {}); toast("已开始采集，进度见「任务」页"); await refresh(); },
  "demo-load": async () => { await api("/api/demo", { action: "load" }); await refresh(); await go("dashboard"); },
  "demo-clear": async () => { if (!confirm("清除所有示例数据？你自己的数据不受影响。")) return; await api("/api/demo", { action: "clear" }); await refresh(); await go(S.page); },
  "open-work": (el) => { const w = S.data.works[+el.dataset.i]; S.editing = null; const d = $("#drawer"); d.innerHTML = workDrawer(w); d.hidden = false; drawCharts(w); },
  "close-drawer": () => { $("#drawer").hidden = true; S.editing = null; S.benchOpen = null; },
  "save-work-tags": async (el) => {
    const w = S.data.works[+el.dataset.i];
    const tags = {}; $("#drawer").querySelectorAll("[data-wtag]").forEach((s) => { if (s.value) tags[s.dataset.wtag] = s.value; });
    const first = w.posts[0];
    const { id } = await api("/api/posts/tags", { platform: first.platform, post_id: first.post_id, tags });
    for (const p of w.posts.slice(1)) await api("/api/contents/link", { content_id: id, platform: p.platform, post_id: p.post_id });
    toast("标签已保存"); $("#drawer").hidden = true; await go("dashboard");
  },
  kind: (el) => { S.form.kind = el.dataset.v; S.form.files = ""; render(); },
  pick: async (el) => {
    const { files } = await api("/api/pick", { kind: el.dataset.kind, many: !!el.dataset.many });
    if (files.length) { S.form[el.dataset.to] = files.join("\n"); render(); }
  },
  "pick-xlsx": async () => { const { files } = await api("/api/pick", { kind: "xlsx" }); if (files.length) $("#imp-path").value = files[0]; },
  "import-xlsx": async () => {
    const r = await api("/api/import", { path: $("#imp-path").value });
    toast(`导入 ${r.posts} 条作品，其中 ${r.tagged} 条带结构标签，${r.flagged} 条状态异常`); await refresh(); await go("dashboard");
  },
  publish: async () => {
    const f = S.form;
    const targets = Object.keys(f.targets).filter((k) => f.targets[k]).map((k) => { const [platform, ...rest] = k.split("/"); return { platform, account: rest.join("/"), draft: !!f.drafts[k] }; });
    const names = targets.map((t) => `${pfName(t.platform)}「${t.account}」${t.draft ? "（存草稿）" : ""}`).join("、");
    if (targets.length && !confirm(`确认${f.schedule ? "定时 " + f.schedule.replace("T", " ") + " " : "立即"}发布到：${names}？`)) return;
    await api("/api/publish", { kind: f.kind, files: f.files.split("\n"), cover: f.cover, cover_sec: f.cover_sec, cover_focus: f.cover_focus, cover_mode: f.cover_mode, title: f.title, desc: f.desc, tags: f.tags, schedule: f.schedule, headless: f.headless, targets, content_id: f.content_id });
    toast("已加入发布队列"); await refresh();
  },
  "cover-preview": async () => { await coverPreview(); },
  "cover-focus": async (el, e) => {
    const r = el.querySelector("img").getBoundingClientRect();
    S.form.cover_focus = [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
    await coverPreview();
  },
  "publish-unlink": () => { S.form.content_id = null; render(); },
  "job-log": async (el) => { const id = +el.dataset.id; S.openJob = S.openJob === id ? null : id; S.jobLog = S.openJob ? (await api("/api/jobs/" + id)).log : ""; live(); },
  "job-cancel": async (el) => { await api(`/api/jobs/${el.dataset.id}/cancel`, {}); await refresh(); },
  review: async (el) => { await api("/api/review", { days: +$("#rv-days").value, ai: !!el.dataset.ai });
    toast(el.dataset.ai && !S.state.agent_api ? `已排进 AI 待办：在 AI 助手里说「${ASK}」` : "正在生成复盘…"); await refresh(); render(); },
  "review-del": async (el) => { await api("/api/reviews/delete", { id: +el.dataset.id }); await go("review"); },
  plan: async () => { await api("/api/plan", { days: 14 }); toast("正在生成本周计划…"); await refresh(); },
  "plan-refine": async () => {
    const ids = S.contents.filter((c) => c.source === "plan" && c.week === S.method.week && c.stage === S.method.workflows[c.workflow].stages[0][0]).map((c) => c.id);
    await api("/api/plan/refine", { ids }); toast(S.state.agent_api ? "已交给模型接口" : `已排进 AI 待办：在 AI 助手里说「${ASK}」`); await refresh();
  },
  "plan-scripts": async () => {
    const ids = S.contents.filter((c) => c.source === "plan" && c.week === S.method.week && !c.script && !c.archived).map((c) => c.id);
    if (!ids.length) return toast("本周没有还没出稿的选题");
    await api("/api/contents/ai", { ids }); toast(S.state.agent_api ? `${ids.length} 条已交给模型接口` : `${ids.length} 条已排进 AI 待办：在 AI 助手里说「${ASK}」`); await refresh(); await go("agent");
  },
  "open-content": async (el) => {
    S.contents = (await api("/api/contents")).contents;
    const c = S.contents.find((x) => x.id === +el.dataset.id); if (c) openContent(c);
  },
  "open-publish": async (el) => {
    S.contents = (await api("/api/contents")).contents;
    const c = S.contents.find((x) => x.id === +el.dataset.id); if (!c) return;
    Object.assign(S.form, { content_id: c.id, title: c.title.replace(/^【[^】]*】/, "").slice(0, 60), desc: c.pub_desc || "", tags: c.pub_tags || "" });
    await go("publish");
  },
  "plan-and-review": async () => {
    await api("/api/review", { days: 7 }); await api("/api/plan", { days: 14 });
    toast("正在复盘并生成本周计划…"); await refresh(); setTimeout(() => go("today"), 2500);
  },
  "script-all": async (el) => {
    const ids = (el.dataset.ids || "").split(",").filter(Boolean).map(Number);
    await api("/api/contents/ai", { ids }); toast(S.state.agent_api ? `${ids.length} 条已交给模型接口` : `${ids.length} 条已排进 AI 待办：在 AI 助手里说「${ASK}」`);
    await refresh(); await go("today");
  },
  "content-conclude": async () => {
    await api("/api/contents/conclude", { id: S.editing.id, conclusion: $("#conclusion").value });
    toast("结论已记下，打法库已更新"); S.contents = (await api("/api/contents")).contents; openContent(S.contents.find((x) => x.id === S.editing.id));
  },
  "lesson-verdict": async (el) => { await api("/api/playbook/note", { key: el.dataset.key, verdict: el.dataset.v }); await go("playbook"); },
  calibrate: async (el) => { if (!confirm("按你自己作品的分位数更新这个平台的阈值？之后的评分和判定都会按新阈值算。")) return;
    await api("/api/playbook/calibrate", { platform: el.dataset.platform }); await reloadMethod(); toast("阈值已更新"); await go("playbook"); },
  "copy-ask": async () => { try { await navigator.clipboard.writeText(ASK); toast("已复制，粘贴到 AI 助手里"); } catch { toast(ASK); } },
  "agent-copy": async (el) => {
    const { brief } = await api(`/api/agent/${el.dataset.id}/brief`);
    try { await navigator.clipboard.writeText(brief); toast("任务说明已复制，发给 AI 后把它的回答粘贴回来"); } catch { toast("复制失败"); }
  },
  "agent-paste": async (el) => {
    const text = prompt("把 AI 写好的结果整段粘贴进来：");
    if (!text) return;
    const { note } = await api("/api/agent/submit", { id: +el.dataset.id, text }); toast(note); await refresh(); await go(S.page);
    if (S.editing) { S.contents = (await api("/api/contents")).contents; openContent(S.contents.find((x) => x.id === S.editing.id)); }
  },
  "agent-cancel": async (el) => { await api("/api/agent/cancel", { id: +el.dataset.id }); await refresh(); await go("agent"); },
  "new-content": (el) => openContent({ workflow: el.dataset.wf, stage: S.method.workflows[el.dataset.wf].stages[0][0], targets: [], checklist: {}, posts: [], source: "manual" }),
  "edit-content": (el) => openContent(S.contents.find((c) => c.id === +el.dataset.id)),
  "content-save": async () => {
    const c = readContentForm();
    const { id } = await api("/api/contents/save", c);
    S.contents = (await api("/api/contents")).contents; toast("已保存");
    openContent(S.contents.find((x) => x.id === id)); if (S.page !== "dashboard") render();
  },
  "content-next": async () => {
    const c = readContentForm();
    const stages = S.method.workflows[c.workflow].stages.map((s) => s[0]);
    c.stage = stages[Math.min(stages.indexOf(c.stage) + 1, stages.length - 1)];
    const { id } = await api("/api/contents/save", c);
    S.contents = (await api("/api/contents")).contents;
    openContent(S.contents.find((x) => x.id === id)); render();
  },
  "content-scaffold": async () => { const c = readContentForm(); const { id } = await api("/api/contents/save", c); await api("/api/contents/scaffold", { id });
    S.contents = (await api("/api/contents")).contents; openContent(S.contents.find((x) => x.id === id)); },
  "content-ai": async () => {
    const c = readContentForm(); const { id } = await api("/api/contents/save", c); await api("/api/contents/ai", { id });
    await refresh(); S.contents = (await api("/api/contents")).contents; openContent(S.contents.find((x) => x.id === id)); if (S.page !== "dashboard") render();
  },
  "content-publish": async () => {
    const c = readContentForm(); await api("/api/contents/save", c);
    const full = S.contents.find((x) => x.id === c.id) || c;
    Object.assign(S.form, { content_id: c.id, title: c.title.replace(/^【[^】]*】/, "").slice(0, 60), desc: full.pub_desc || S.form.desc, tags: full.pub_tags || S.form.tags });
    $("#drawer").hidden = true; S.editing = null; await go("publish");
  },
  "content-link": async () => { const [platform, ...rest] = $("#link-post").value.split("|");
    await api("/api/contents/link", { content_id: S.editing.id, platform, post_id: rest.join("|") });
    S.contents = (await api("/api/contents")).contents; openContent(S.contents.find((x) => x.id === S.editing.id)); },
  "content-unlink": async (el) => { await api("/api/contents/unlink", { platform: el.dataset.platform, post_id: el.dataset.post });
    S.contents = (await api("/api/contents")).contents; openContent(S.contents.find((x) => x.id === S.editing.id)); },
  "content-archive": async () => { if (!confirm("归档这条选题？")) return; await api("/api/contents/archive", { id: S.editing.id });
    $("#drawer").hidden = true; S.editing = null; await go(S.page); },
  "comment-tab": (el) => { S.commentTab = el.dataset.v; render(); },
  "bench-days": async (el) => { S.benchDays = +el.dataset.v; await go("bench"); },
  "bench-resolve": async () => {
    toast("正在查找…");
    const r = await api("/api/bench/resolve", { platform: $("#bench-pf").value, text: $("#bench-text").value });
    S.benchCands = { platform: $("#bench-pf").value, grp: $("#bench-grp").value, list: r.candidates }; render();
  },
  "bench-add": async (el) => {
    const c = S.benchCands;
    await api("/api/bench/add", { platform: c.platform, grp: c.grp, candidate: c.list[+el.dataset.i] });
    S.benchCands = null; toast("已加入，正在读取作品…"); await refresh(); await go("bench");
  },
  "bench-refresh": async (el) => { await api("/api/bench/refresh", { bench_id: el.dataset.id ? +el.dataset.id : null }); toast("正在读取，进度见「任务」页"); await refresh(); },
  "bench-toggle": async (el) => { await api("/api/bench/update", { id: +el.dataset.id, enabled: !!+el.dataset.on }); await go("bench"); },
  "bench-del": async (el) => { if (!confirm(`删除对标账号「${el.dataset.name}」？它的作品和拆解都会删掉。`)) return; await api("/api/bench/delete", { id: +el.dataset.id }); await go("bench"); },
  "bench-open": (el) => { S.benchOpen = S.bench.hits[+el.dataset.i]; const d = $("#drawer"); d.innerHTML = benchDrawer(S.benchOpen); d.hidden = false; },
  "bench-save": async () => {
    const p = S.benchOpen, tags = {};
    $("#drawer").querySelectorAll("[data-btag]").forEach((s) => { if (s.value) tags[s.dataset.btag] = s.value; });
    await api("/api/bench/mark", { platform: p.platform, post_id: p.post_id, tags, analysis: $("#bench-analysis").value });
    toast("拆解已保存"); await reopenBench();
  },
  "bench-analyze": async () => { const p = S.benchOpen; await api("/api/bench/analyze", { posts: [{ platform: p.platform, post_id: p.post_id }] });
    toast(S.state.agent_api ? "已交给模型接口" : `已排进 AI 待办：在 AI 助手里说「${ASK}」`); await refresh(); },
  "bench-analyze-all": async () => {
    const posts = S.bench.hits.filter((p) => p.status === "new").slice(0, 10).map((p) => ({ platform: p.platform, post_id: p.post_id }));
    await api("/api/bench/analyze", { posts }); toast(S.state.agent_api ? `${posts.length} 条已交给模型接口` : `${posts.length} 条已排进 AI 待办：在 AI 助手里说「${ASK}」`); await refresh();
  },
  "bench-ignore": async () => { const p = S.benchOpen; await api("/api/bench/mark", { platform: p.platform, post_id: p.post_id, status: p.status === "ignored" ? "new" : "ignored" }); await reopenBench(); },
  "bench-use": async () => {
    const p = S.benchOpen;
    const { id } = await api("/api/bench/to_content", { platform: p.platform, post_id: p.post_id });
    S.contents = (await api("/api/contents")).contents; S.page = "create"; render();
    openContent(S.contents.find((c) => c.id === id)); toast("已放进「创作」的选题阶段");
  },
  "comments-fetch": async (el) => { await api("/api/comments/fetch", acc(el)); toast("正在抓评论…"); await refresh(); },
  "comments-process": async () => { const r = await api("/api/comments/process", {}); toast(`生成草稿 ${r.drafted} 条，排队发送 ${r.queued} 条`); await go("comments"); },
  "comments-send": async () => { if (!confirm("开始自动发送排队中的回复？会打开浏览器窗口，条与条之间会间隔几十秒。")) return;
    await api("/api/comments/send", {}); toast("开始发送，进度见「任务」页"); await refresh(); },
  "comments-import": async () => {
    const sel = $("#imp-post"), opt = sel.selectedOptions[0];
    const platform = opt && opt.dataset.platform ? opt.dataset.platform : $("#imp-platform").value;
    const r = await api("/api/comments/import", { platform, post_id: sel.value, text: $("#imp-text").value });
    toast(`导入 ${r.new} 条，生成回复草稿 ${r.drafted} 条`); await go("comments");
  },
  "comment-copy": async (el) => {
    const text = document.querySelector(`[data-reply="${CSS.escape(el.dataset.key)}"]`).value;
    try { await navigator.clipboard.writeText(text); toast("已复制，去平台粘贴回复后点「标记已回复」"); } catch { toast("复制失败，请手动选中文字复制"); }
  },
  "comment-state": async (el) => {
    const k = keyOf(el.dataset.key);
    const box = document.querySelector(`[data-reply="${CSS.escape(el.dataset.key)}"]`);
    await api("/api/comments/update", { ...k, state: el.dataset.state, reply: box ? box.value : null }); await go("comments");
  },
  "rule-edit": (el) => { S.editRule = el.dataset.id ? S.comments.rules.find((r) => r.id === +el.dataset.id) : { name: "", keywords: "", templates: [], enabled: 1, daily_cap: 20 }; render(); },
  "rule-cancel": () => { S.editRule = null; render(); },
  "rule-save": async () => {
    await api("/api/rules/save", { id: S.editRule.id, name: $("#r-name").value, keywords: $("#r-kw").value, exclude: $("#r-ex").value,
      platforms: [...document.querySelectorAll(".r-pf:checked")].map((x) => x.value).join(","), templates: $("#r-tpl").value.split("\n"),
      use_ai: $("#r-ai").checked, is_intent: $("#r-intent").checked, auto_send: $("#r-auto").checked, daily_cap: +$("#r-cap").value, enabled: $("#r-on").checked });
    S.editRule = null; toast("规则已保存"); await go("comments");
  },
  "rule-delete": async () => { if (!confirm("删除这条规则？")) return; await api("/api/rules/delete", { id: S.editRule.id }); S.editRule = null; await go("comments"); },
  "acc-add": async () => { await api("/api/accounts/add", { platform: $("#acc-platform").value, account: $("#acc-name").value }); $("#acc-name").value = ""; await refresh(); },
  "acc-login": async (el) => { await api("/api/accounts/login", acc(el)); toast("马上会弹出浏览器窗口，请用手机扫码"); await refresh(); },
  "acc-check": async (el) => { await api("/api/accounts/check", acc(el)); await refresh(); },
  "acc-collect": async (el) => { await api("/api/collect", acc(el)); toast("已开始采集"); await refresh(); },
  "acc-del": async (el) => { if (!confirm(`删除账号「${el.dataset.account}」？它的登录状态和已采集的数据都会删掉。`)) return; await api("/api/accounts/delete", acc(el)); await refresh(); },
  "save-profile": async () => {
    const body = {}; document.querySelectorAll("[data-p]").forEach((el) => { body[el.dataset.p] = el.value; });
    await api("/api/profile", body); await reloadMethod(); toast("定位卡已保存"); render();
  },
  "save-params": async () => {
    const thresholds = {};
    document.querySelectorAll("[data-th]").forEach((el) => {
      const [pf, m, i] = el.dataset.th.split("|");
      ((thresholds[pf] ||= {})[m] ||= [])[+i] = m === "views" ? +el.value : +el.value / 100;
    });
    await api("/api/params", { thresholds, flow_weight: +$("#pm-fw").value, compare_slot: $("#pm-slot").value, max_p0: +$("#pm-p0").value });
    await reloadMethod(); toast("复盘参数已保存"); render();
  },
  "save-workflows": async () => {
    const body = {}; document.querySelectorAll("[data-wf]").forEach((el) => { if (el.tagName === "TEXTAREA") body[el.dataset.wf] = el.value; });
    await api("/api/workflows", body); await reloadMethod(); toast("工作流已保存"); render();
  },
  "save-settings": async () => {
    await api("/api/settings", { auto_collect: $("#st-auto").checked, collect_hour: +$("#st-hour").value, max_posts: +$("#st-max").value, headless: $("#st-headless").checked,
      chrome_path: $("#st-chrome").value, xhs_cover_ratio: $("#st-xhs-ratio").value, ai_base_url: $("#st-url").value, ai_model: $("#st-model").value, ai_api_key: $("#st-key").value,
      tikhub_api_key: $("#st-th-key").value, tikhub_base_url: $("#st-th-url").value, bench_hit_ratio: +$("#st-th-ratio").value,
      bench_pages: +$("#st-th-pages").value, bench_auto: $("#st-th-auto").checked });
    toast("设置已保存"); await refresh(); render();
  },
};
document.addEventListener("click", (e) => {
  const nav = e.target.closest(".side a[data-page]");
  if (nav) return act(go)(nav.dataset.page);
  if (e.target.id === "drawer") return ACTIONS["close-drawer"]();
  const el = e.target.closest("[data-act]");
  if (el && ACTIONS[el.dataset.act]) { if (el.tagName === "BUTTON") e.preventDefault(); act(ACTIONS[el.dataset.act])(el, e); }
});
document.addEventListener("input", (e) => {
  const t = e.target;
  if (t.dataset.f) S.form[t.dataset.f] = t.type === "checkbox" ? t.checked : t.value;
  if (["cover", "files", "cover_sec"].includes(t.dataset.f)) S.coverPreview = null;  // 来源变了，旧预览作废
  if (t.dataset.target) S.form.targets[t.dataset.target] = t.checked;
  if (t.dataset.draft) S.form.drafts[t.dataset.draft] = t.checked;
});
document.addEventListener("change", act(async (e) => {
  const t = e.target;
  if (t.dataset.flag) {
    const [platform, ...rest] = t.dataset.flag.split("|");
    await api("/api/posts/flag", { platform, post_id: rest.join("|"), flag: t.value });
    toast(t.value ? "已标记为异常，不进评分和结论" : "已恢复正常"); await loadData();
  }
  if (S.editing && t.closest("#drawer") && (t.dataset.c === "workflow" || t.dataset.c === "archetype")) openContent(readContentForm());
}));
document.addEventListener("keydown", act(async (e) => {
  if (e.key === "Escape") ACTIONS["close-drawer"]();
  if (e.key === "Enter" && e.target.classList.contains("lesson-note")) {
    await api("/api/playbook/note", { key: e.target.dataset.key, note: e.target.value }); toast("已保存");
  }
}));

(async () => {
  try { S.state = await api("/api/state"); await reloadMethod(); await go("today"); } catch (e) { $("#main").innerHTML = `<div class="card empty">${esc(e.message)}</div>`; }
  poll();
})();
