"use strict";
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const PF = ["douyin", "xhs", "channels"];
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const S = {
  page: "dashboard", state: null, data: null, days: 30, reviews: [], openJob: null, jobLog: "",
  form: { kind: "video", files: "", cover: "", title: "", desc: "", tags: "", schedule: "", headless: false, targets: {}, drafts: {} },
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
const pct = (v, d = 1) => (v == null ? "—" : v.toFixed(d) + "%");
const sec = (v) => (v == null ? "—" : v >= 60 ? `${Math.floor(v / 60)} 分 ${Math.round(v % 60)} 秒` : v.toFixed(1) + " 秒");
const day = (ms) => (ms ? new Date(ms).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" }) : "—");
const dt = (ms) => (ms ? new Date(ms).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
const pfName = (k) => S.state.platforms[k].name;
const pfDot = (k) => `<span class="dot ${k}"></span>`;
function rel(r, lowerBetter) {
  if (r == null) return "";
  const d = Math.round((r - 1) * 100);
  if (Math.abs(d) < 5) return `<span class="rel"><span class="flat">●</span> 与平时持平</span>`;
  const good = lowerBetter ? d < 0 : d > 0;
  return `<span class="rel"><span class="${good ? "up" : "down"}">${d > 0 ? "▲" : "▼"}</span> 比平时${d > 0 ? "高" : "低"} ${Math.abs(d)}%</span>`;
}
const cleanTitle = (t) => (t || "").replace(/#\S+/g, "").trim() || t || "（无标题）";
const JOB_NAME = { login: "扫码登录", check: "检查登录", publish: "发布", collect: "采集数据", review: "生成复盘" };
const STATUS_NAME = { pending: "排队中", running: "进行中", done: "完成", failed: "失败", cancelled: "已取消" };
const ACC_STATUS = { ok: "已登录", expired: "登录过期", unknown: "未登录", demo: "示例" };
function jobTitle(j) {
  const p = j.params || {};
  const who = p.platform ? `${pfName(p.platform)}「${p.account}」` : "";
  if (j.type === "publish") return `发布到${who}：${p.title || ""}`;
  if (j.type === "review") return `生成${p.ai ? " AI " : "规则"}复盘`;
  return `${JOB_NAME[j.type] || j.type} ${who}`;
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
    else if (!line.trim()) close();
    else { close(); html += `<p>${inline(line)}</p>`; }
  }
  close();
  return html;
}

// ---------- 数据看板 ----------
async function loadData() { S.data = await api(`/api/data?days=${S.days}`); }

function dashboard() {
  const d = S.data, st = S.state;
  const lastCollect = Math.max(0, ...st.accounts.map((a) => a.last_collect || 0));
  const head = `<div class="head"><h1>数据看板</h1>
    <div class="seg">${[[7, "近 7 天"], [30, "近 30 天"], [90, "近 90 天"], [0, "全部"]].map(([v, t]) => `<button data-act="days" data-v="${v}" class="${S.days === v ? "on" : ""}">${t}</button>`).join("")}</div>
    <div class="grow"></div><span class="sub">${lastCollect ? "上次采集 " + dt(lastCollect) : ""}</span>
    <button data-act="collect-all">立即采集</button></div>`;
  if (!st.has_data) {
    return head + `<div class="card empty"><p>还没有数据。先到「账号」页添加账号、扫码登录，然后点「立即采集」。</p>
      <p>想先看看界面长什么样，可以载入一份虚构的示例数据。</p><button class="primary" data-act="demo-load">载入示例数据</button></div>`;
  }
  const demo = st.accounts.some((a) => a.status === "demo");
  const banner = demo ? `<div class="banner">当前包含虚构的示例数据，作品和数字都不是真的。<button class="small" data-act="demo-clear" style="margin-left:10px">清除示例数据</button></div>` : "";
  const kpi = (label, key) => `<div class="card kpi"><div class="label">${label}</div><div class="num">${num(d.totals[key])}</div>
    <div class="split">${PF.map((p) => `<span>${pfDot(p)}${pfName(p)} ${num(d.by_platform[p][key])}</span>`).join("")}</div></div>`;
  const cell = (w, p) => {
    const post = w.posts.find((x) => x.platform === p);
    if (!post) return `<td class="sub">未发布</td>`;
    return `<td><div class="num-cell">${num(post.latest.views)}${w.best_platform === p ? ` <span class="tag best">✓ 最佳</span>` : ""}</div>
      ${rel(post.rel.views)}${post.weak_stage ? `<div><span class="tag weak">✕ 卡在「${post.weak_stage.name}」</span></div>` : ""}</td>`;
  };
  const rowsHtml = d.works.map((w, i) => `<tr class="click" data-act="open-work" data-i="${i}">
    <td class="title-cell"><div class="t" title="${esc(w.title)}">${esc(cleanTitle(w.title))}</div><div class="sub">${day(w.first_post_time)} 发布${w.duration_sec ? " · " + sec(w.duration_sec) : ""}</div></td>
    ${PF.map((p) => cell(w, p)).join("")}
    <td class="num-cell">${num(w.total_views)}</td><td class="num-cell">${num(w.total_new_fans)}</td></tr>`).join("");
  return head + banner + `<div class="grid k4"><div class="card kpi"><div class="label">作品</div><div class="num">${d.totals.works}</div>
      <div class="split"><span>共 ${d.totals.posts} 条发布</span></div></div>
    ${kpi("播放", "views")}${kpi("互动（赞评藏转）", "interactions")}${kpi("涨粉", "new_fans")}</div>
    <div class="card"><h2>作品表现 <span class="sub" style="font-weight:400">每格是该平台的播放量；「比平时」是和这个账号自己的中位数相比。点一行看诊断。</span></h2>
    <div class="table-wrap"><table><thead><tr><th>作品</th>${PF.map((p) => `<th>${pfDot(p)}${pfName(p)}</th>`).join("")}<th>合计播放</th><th>合计涨粉</th></tr></thead>
    <tbody>${rowsHtml || `<tr><td colspan="6" class="sub">这段时间没有作品</td></tr>`}</tbody></table></div></div>`;
}

const METRICS = [
  ["播放", (p) => num(p.latest.views), "views"], ["封面点击率", (p) => pct(p.latest.cover_click_rate), "cover_click_rate"],
  ["2 秒跳出率", (p) => pct(p.latest.exit2s_rate), "exit2s_rate", true], ["5 秒完播率", (p) => pct(p.latest.finish5s_rate), "finish5s_rate"],
  ["完播率", (p) => pct(p.latest.finish_rate), "finish_rate"], ["平均观看", (p) => sec(p.latest.avg_view_sec)],
  ["互动率", (p) => pct(p.derived.interaction_rate, 2), "interaction_rate"], ["点赞", (p) => num(p.latest.likes)], ["评论", (p) => num(p.latest.comments)],
  ["收藏", (p) => num(p.latest.collects)], ["分享", (p) => num(p.latest.shares)], ["涨粉", (p) => num(p.latest.new_fans), "new_fans"],
  ["每千次播放涨粉", (p) => (p.derived.fans_per_k == null ? "—" : p.derived.fans_per_k.toFixed(2)), "fans_per_k"],
];
const STAGE_TEXT = { weak: "✕ 掉队", strong: "✓ 突出", ok: "● 正常", unknown: "— 无数据" };

function workDrawer(w) {
  const posts = w.posts;
  const stageValue = (s) => (s.value == null ? "—" : s.metric === "fans_per_k" ? s.value.toFixed(2) : pct(s.value));
  const stages = `<div class="stages"><div></div>${S.data.stages.map((s) => `<div class="hd">${s.name}</div>`).join("")}
    ${posts.map((p) => `<div class="pf">${pfDot(p.platform)}${pfName(p.platform)}</div>` + p.stages.map((s) => `<div class="stage ${s.status}"><div class="v">${stageValue(s)}</div><div class="s">${STAGE_TEXT[s.status]}</div></div>`).join("")).join("")}</div>`;
  const advice = posts.filter((p) => p.weak_stage).map((p) => `<li>${pfDot(p.platform)}<b>${pfName(p.platform)}最先掉队的是「${p.weak_stage.name}」</b>：${esc(p.weak_stage.advice)}</li>`).join("");
  const table = `<table><thead><tr><th>指标</th>${posts.map((p) => `<th>${pfDot(p.platform)}${pfName(p.platform)}<span class="sub">「${esc(p.account)}」</span></th>`).join("")}</tr></thead><tbody>
    ${METRICS.map(([name, fn, key, lower]) => `<tr><td>${name}</td>${posts.map((p) => `<td class="num-cell">${fn(p)} ${key ? rel(p.rel[key], lower) : ""}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  const hasRet = posts.some((p) => p.retention.length);
  const hasSrc = posts.filter((p) => p.sources.length);
  return `<div class="panel"><div class="head"><div class="grow"><h1>${esc(cleanTitle(w.title))}</h1>
      <div class="sub">${dt(w.first_post_time)} 首发${w.duration_sec ? " · 时长 " + sec(w.duration_sec) : ""}</div></div><button data-act="close-drawer">关闭</button></div>
    <div class="card"><h2>漏斗诊断 <span class="sub" style="font-weight:400">每一步和这个账号平时的水平比，低于八成算掉队</span></h2>${stages}
      ${advice ? `<ul class="advice">${advice}</ul>` : `<p class="sub" style="margin:12px 0 0">没有明显掉队的环节${posts.some((p) => p.stages.every((s) => s.status === "unknown")) ? "（同账号不到 3 条作品时无法比较）" : ""}。</p>`}
      ${posts.some((p) => p.platform === "channels") ? `<p class="hint">视频号的「留下来」用的是后台的快速划走率，口径和另外两个平台的 2 秒跳出率不完全一样。</p>` : ""}</div>
    ${hasRet ? `<div class="card"><h2>留存曲线 <span class="sub" style="font-weight:400">看到第几秒还剩多少观众</span></h2><div id="ch-ret" class="chart"></div></div>` : ""}
    <div class="card"><h2>各平台指标</h2><div class="table-wrap">${table}</div></div>
    ${hasSrc.length ? `<div class="card"><h2>流量来源</h2><div class="grid k${Math.min(3, hasSrc.length)}" style="margin:0">${hasSrc.map((p) => `<div><div class="sub">${pfDot(p.platform)}${pfName(p.platform)}</div><div id="ch-src-${p.platform}" class="chart short"></div></div>`).join("")}</div></div>` : ""}
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

// ---------- 发布 ----------
function realAccounts() { return S.state.accounts.filter((a) => a.status !== "demo"); }
function publishPage() {
  const f = S.form, accs = realAccounts();
  const isNote = f.kind === "note";
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
  <div class="grid k2"><div class="card">
    <label class="field"><span>内容类型</span><div class="seg"><button data-act="kind" data-v="video" class="${isNote ? "" : "on"}">视频</button><button data-act="kind" data-v="note" class="${isNote ? "on" : ""}">图文</button></div></label>
    <label class="field"><span>${isNote ? "图片文件（每行一个完整路径）" : "视频文件（完整路径）"}</span>
      <div class="row"><div class="grow">${isNote ? `<textarea data-f="files" style="min-height:70px">${esc(f.files)}</textarea>` : `<input data-f="files" value="${esc(f.files)}" placeholder="例如 D:\\视频\\成片.mp4">`}</div>
      <button data-act="pick" data-kind="${isNote ? "image" : "video"}" data-many="${isNote ? 1 : ""}" data-to="files">选择文件</button></div></label>
    ${isNote ? "" : `<label class="field"><span>封面（可选）</span><div class="row"><div class="grow"><input data-f="cover" value="${esc(f.cover)}"></div><button data-act="pick" data-kind="image" data-to="cover">选择图片</button></div></label>`}
    <label class="field"><span>标题</span><input data-f="title" value="${esc(f.title)}" maxlength="100"></label>
    <label class="field"><span>${isNote ? "正文" : "简介"}</span><textarea data-f="desc">${esc(f.desc)}</textarea></label>
    <label class="field"><span>话题（用逗号或空格分开，不用写 #）</span><input data-f="tags" value="${esc(f.tags)}"></label>
  </div><div class="card">
    <label class="field"><span>发布到</span><div class="targets">${targets}</div></label>
    <label class="field"><span>定时发布（可选，留空就是立即发布；由平台在这个时间自动发出，不需要一直开着电脑）</span><input type="datetime-local" data-f="schedule" value="${esc(f.schedule)}"></label>
    <label class="field"><input type="checkbox" data-f="headless" ${f.headless ? "checked" : ""}>后台运行，不显示浏览器窗口</label>
    <button class="primary" data-act="publish">开始发布</button>
    <p class="hint">发布时会依次打开各平台的发布页自动填写并提交，同一时间只跑一个。进度在下方和「任务」页查看。</p>
  </div></div>
  <div class="card"><h2>最近的发布</h2><div id="live-publish"></div></div>`;
}
function jobsTable(list, empty) {
  if (!list.length) return `<div class="sub">${empty}</div>`;
  return `<table><tbody>${list.map((j) => `<tr><td style="width:110px"><span class="status ${j.status}">${STATUS_NAME[j.status]}</span></td>
    <td>${esc(jobTitle(j))}${j.params.schedule ? ` <span class="tag">定时 ${esc(j.params.schedule.replace("T", " "))}</span>` : ""}${j.params.draft ? ` <span class="tag">草稿</span>` : ""}${j.params.auto ? ` <span class="tag">自动</span>` : ""}
      ${S.openJob === j.id ? `<pre class="log">${esc(S.jobLog || "（没有输出）")}</pre>` : j.status === "failed" || j.status === "running" ? `<div class="hint">${esc((j.tail || "").trim().split("\n").pop())}</div>` : ""}</td>
    <td class="sub num-cell" style="width:110px">${dt(j.created_at)}</td>
    <td style="width:150px;text-align:right"><button class="small" data-act="job-log" data-id="${j.id}">${S.openJob === j.id ? "收起" : "日志"}</button>
      ${j.status === "pending" || j.status === "running" ? `<button class="small danger" data-act="job-cancel" data-id="${j.id}">取消</button>` : ""}</td></tr>`).join("")}</tbody></table>`;
}

// ---------- 复盘 ----------
function reviewPage() {
  const aiReady = S.state.settings.ai_api_key_set && S.state.settings.ai_model;
  const running = S.state.jobs.some((j) => j.type === "review" && (j.status === "pending" || j.status === "running"));
  return `<div class="head"><h1>复盘</h1><div class="grow"></div>
    <select id="rv-days" style="width:auto"><option value="7">近 7 天</option><option value="30">近 30 天</option><option value="90">近 90 天</option></select>
    <button data-act="review" data-ai="">规则复盘</button><button class="primary" data-act="review" data-ai="1" ${aiReady ? "" : "disabled title='先在设置页配置大模型'"}>AI 复盘</button></div>
  ${aiReady ? "" : `<div class="banner">AI 复盘需要先在「设置」页填写大模型的接口地址、密钥和模型名。规则复盘不需要。</div>`}
  ${running ? `<div class="card sub">正在生成复盘…</div>` : ""}
  ${S.reviews.map((r) => `<div class="card"><div class="row" style="margin-bottom:10px"><span class="tag">${r.kind === "ai" ? "AI 复盘" : "规则复盘"}</span><span class="sub">${dt(r.created_at)} 生成</span><span class="grow"></span><button class="small danger" data-act="review-del" data-id="${r.id}">删除</button></div><div class="md">${md(r.content)}</div></div>`).join("")
    || (running ? "" : `<div class="card empty"><p>还没有复盘。选一个时间范围，点右上角生成。</p></div>`)}`;
}

// ---------- 账号 ----------
function accountsPage() {
  return `<div class="head"><h1>账号</h1></div>
  ${S.state.sau_installed ? "" : `<div class="banner">还没有安装发布引擎，登录和发布暂时不能用。请先关闭工作台，双击运行「安装.cmd」，再重新启动。</div>`}
  <div class="card"><h2>添加账号</h2><div class="row">
    <select id="acc-platform" style="width:140px">${PF.map((p) => `<option value="${p}">${pfName(p)}</option>`).join("")}</select>
    <div class="grow"><input id="acc-name" placeholder="给这个账号起个备注名，例如：主号" maxlength="30"></div><button class="primary" data-act="acc-add">添加</button></div>
    <p class="hint" style="margin-bottom:0">备注名只是你自己区分账号用的。添加后点「扫码登录」，会弹出浏览器窗口，用手机扫码即可。登录一次，发布和采集都能用。</p></div>
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
const jobsPage = () => `<div class="head"><h1>任务</h1><span class="sub">登录、发布、采集都在这里排队，一次只执行一个</span></div><div class="card"><div id="live-jobs"></div></div>`;
function settingsPage() {
  const s = S.state.settings;
  const hasDemo = S.state.accounts.some((a) => a.status === "demo");
  return `<div class="head"><h1>设置</h1></div><div class="grid k2"><div class="card"><h2>数据采集</h2>
    <label class="field"><input type="checkbox" id="st-auto" ${s.auto_collect ? "checked" : ""}>工作台开着时每天自动采集一次</label>
    <label class="field"><span>每天几点之后采集（0-23）</span><input type="number" id="st-hour" min="0" max="23" value="${s.collect_hour}"></label>
    <label class="field"><span>每个账号每次最多采集多少条作品</span><input type="number" id="st-max" min="1" max="100" value="${s.max_posts}"></label>
    <label class="field"><input type="checkbox" id="st-headless" ${s.headless ? "checked" : ""}>采集时不显示浏览器窗口（采集总失败时可以取消勾选，看看页面停在哪）</label>
    <label class="field"><span>使用本机的 Chrome（可选，填 chrome.exe 的完整路径；留空用自带浏览器）</span><input id="st-chrome" value="${esc(s.chrome_path)}"></label>
  </div><div class="card"><h2>AI 复盘用的大模型</h2>
    <p class="hint">支持所有兼容 OpenAI 接口的服务，例如 Kimi、DeepSeek、通义千问。密钥只保存在这台电脑的 data/settings.json 里；生成 AI 复盘时，作品标题和数据会发给你填的这个服务。</p>
    <label class="field"><span>接口地址</span><input id="st-url" value="${esc(s.ai_base_url)}"></label>
    <label class="field"><span>模型名</span><input id="st-model" value="${esc(s.ai_model)}" placeholder="填你的服务商提供的模型名"></label>
    <label class="field"><span>密钥${s.ai_api_key_set ? "（已保存，留空表示不修改）" : ""}</span><input id="st-key" type="password" autocomplete="off"></label>
  </div></div><div class="row"><button class="primary" data-act="save-settings">保存设置</button><span class="grow"></span>
    ${hasDemo ? `<button data-act="demo-clear">清除示例数据</button>` : `<button data-act="demo-load">载入示例数据</button>`}</div>`;
}

// ---------- 渲染与轮询 ----------
const PAGES = { dashboard, publish: publishPage, review: reviewPage, accounts: accountsPage, jobs: jobsPage, settings: settingsPage };
function live() {
  const set = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
  set("live-publish", jobsTable(S.state.jobs.filter((j) => j.type === "publish").slice(0, 10), "还没有发布记录。"));
  set("live-accounts", accountsTable());
  set("live-jobs", jobsTable(S.state.jobs, "还没有任务。"));
  const active = S.state.jobs.filter((j) => j.status === "pending" || j.status === "running").length;
  const badge = $("#job-badge"); badge.hidden = !active; badge.textContent = active;
}
function render() {
  document.querySelectorAll(".side a").forEach((a) => a.classList.toggle("active", a.dataset.page === S.page));
  $("#main").innerHTML = PAGES[S.page]();
  live();
}
async function go(page) {
  S.page = page;
  if (page === "dashboard") await loadData();
  if (page === "review") S.reviews = (await api("/api/reviews")).reviews;
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
    // 有任务刚结束：刷新受影响的页面（发布和设置页有正在填的表单，不整页重画）
    if (changed && S.page !== "publish" && S.page !== "settings") await go(S.page);
    live();
  } catch (e) { /* 服务暂时没响应，下次再试 */ }
  const active = S.state && S.state.jobs.some((j) => j.status === "pending" || j.status === "running");
  clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, active ? 1500 : 6000);
}

// ---------- 事件 ----------
const acc = (el) => ({ platform: el.dataset.platform, account: el.dataset.account });
const ACTIONS = {
  days: async (el) => { S.days = +el.dataset.v; await go("dashboard"); },
  "collect-all": async () => { await api("/api/collect", {}); toast("已开始采集，进度见「任务」页"); await refresh(); },
  "demo-load": async () => { await api("/api/demo", { action: "load" }); await refresh(); await go(S.page); },
  "demo-clear": async () => { if (!confirm("清除所有示例数据？你自己的数据不受影响。")) return; await api("/api/demo", { action: "clear" }); await refresh(); await go(S.page); },
  "open-work": (el) => { const w = S.data.works[+el.dataset.i]; const d = $("#drawer"); d.innerHTML = workDrawer(w); d.hidden = false; drawCharts(w); },
  "close-drawer": () => { $("#drawer").hidden = true; },
  kind: (el) => { S.form.kind = el.dataset.v; S.form.files = ""; render(); },
  pick: async (el) => {
    const { files } = await api("/api/pick", { kind: el.dataset.kind, many: !!el.dataset.many });
    if (files.length) { S.form[el.dataset.to] = files.join("\n"); render(); }
  },
  publish: async () => {
    const f = S.form;
    const targets = Object.keys(f.targets).filter((k) => f.targets[k]).map((k) => { const [platform, ...rest] = k.split("/"); return { platform, account: rest.join("/"), draft: !!f.drafts[k] }; });
    const names = targets.map((t) => `${pfName(t.platform)}「${t.account}」${t.draft ? "（存草稿）" : ""}`).join("、");
    if (targets.length && !confirm(`确认${f.schedule ? "定时 " + f.schedule.replace("T", " ") + " " : "立即"}发布到：${names}？`)) return;
    await api("/api/publish", { kind: f.kind, files: f.files.split("\n"), cover: f.cover, title: f.title, desc: f.desc, tags: f.tags, schedule: f.schedule, headless: f.headless, targets });
    toast("已加入发布队列"); await refresh();
  },
  "job-log": async (el) => { const id = +el.dataset.id; S.openJob = S.openJob === id ? null : id; S.jobLog = S.openJob ? (await api("/api/jobs/" + id)).log : ""; live(); },
  "job-cancel": async (el) => { await api(`/api/jobs/${el.dataset.id}/cancel`, {}); await refresh(); },
  review: async (el) => { await api("/api/review", { days: +$("#rv-days").value, ai: !!el.dataset.ai }); toast("正在生成复盘…"); await refresh(); render(); },
  "review-del": async (el) => { await api("/api/reviews/delete", { id: +el.dataset.id }); await go("review"); },
  "acc-add": async () => { await api("/api/accounts/add", { platform: $("#acc-platform").value, account: $("#acc-name").value }); $("#acc-name").value = ""; await refresh(); },
  "acc-login": async (el) => { await api("/api/accounts/login", acc(el)); toast("马上会弹出浏览器窗口，请用手机扫码"); await refresh(); },
  "acc-check": async (el) => { await api("/api/accounts/check", acc(el)); await refresh(); },
  "acc-collect": async (el) => { await api("/api/collect", acc(el)); toast("已开始采集"); await refresh(); },
  "acc-del": async (el) => { if (!confirm(`删除账号「${el.dataset.account}」？它的登录状态和已采集的数据都会删掉。`)) return; await api("/api/accounts/delete", acc(el)); await refresh(); },
  "save-settings": async () => {
    await api("/api/settings", { auto_collect: $("#st-auto").checked, collect_hour: +$("#st-hour").value, max_posts: +$("#st-max").value, headless: $("#st-headless").checked,
      chrome_path: $("#st-chrome").value, ai_base_url: $("#st-url").value, ai_model: $("#st-model").value, ai_api_key: $("#st-key").value });
    toast("设置已保存"); await refresh(); render();
  },
};
async function refresh() {
  S.state = await api("/api/state"); live();
  clearTimeout(pollTimer); pollTimer = setTimeout(poll, 1000);  // 刚发起了操作，尽快再查一次进度
}
document.addEventListener("click", (e) => {
  const nav = e.target.closest(".side a[data-page]");
  if (nav) return act(go)(nav.dataset.page);
  if (e.target.id === "drawer") return ACTIONS["close-drawer"]();
  const el = e.target.closest("[data-act]");
  if (el && ACTIONS[el.dataset.act]) { if (el.tagName === "BUTTON") e.preventDefault(); act(ACTIONS[el.dataset.act])(el); }
});
document.addEventListener("input", (e) => {
  const t = e.target;
  if (t.dataset.f) S.form[t.dataset.f] = t.type === "checkbox" ? t.checked : t.value;
  if (t.dataset.target) S.form.targets[t.dataset.target] = t.checked;
  if (t.dataset.draft) S.form.drafts[t.dataset.draft] = t.checked;
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer").hidden = true; });

(async () => {
  try { S.state = await api("/api/state"); await go("dashboard"); } catch (e) { $("#main").innerHTML = `<div class="card empty">${esc(e.message)}</div>`; }
  poll();
})();
