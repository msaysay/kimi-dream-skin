/**
 * inject.mjs —— Kimi Dream Skin CDP 注入引擎
 *
 * 原理：Kimi 桌面版是 Electron 应用，以 --remote-debugging-port 启动后，
 *       通过 CDP 向渲染页面注入主题 CSS 与一个标记 <meta>，不改 app.asar、不碰签名。
 *
 * 用法：
 *   node inject.mjs --port 9333 --theme dream            注入一次后退出
 *   node inject.mjs --port 9333 --theme cyber --watch    驻留：新页面/跳转后自动补注入
 *   node inject.mjs --port 9333 --theme ink --url kimi-agent.html   只注入匹配页面
 *
 * 主题文件来自 ../themes/<theme>.css
 */
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { listTargets, pickMainPage, CDPSession, sleep } from './cdp.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const args = parseArgs(process.argv.slice(2));

if (!args.port || (!args.theme && !args.remove)) {
  console.error('用法: node inject.mjs --port <端口> --theme <dream|cyber|ink|...> [--watch] [--url <片段>] [--all-pages]');
  console.error('      node inject.mjs --port <端口> --remove [--url <片段>]   移除当前页面中的皮肤');
  process.exit(1);
}

/** 移除模式：删除当前文档中的 <style> 与标记 <meta>（新文档注入脚本随页面关闭失效） */
if (args.remove) {
  const REMOVE_EXPR = `(() => {
    document.getElementById('kimi-dream-skin-style')?.remove();
    document.querySelector('meta[name="kimi-dream-skin"]')?.remove();
    return 'kds:removed';
  })()`;
  const targets = await listTargets(Number(args.port));
  const pages = args['all-pages']
    ? targets.filter((t) => t.type === 'page' && t.webSocketDebuggerUrl)
    : [pickMainPage(targets, { urlPattern: args.url })];
  for (const p of pages) {
    const session = await CDPSession.connect(p.webSocketDebuggerUrl);
    try {
      await session.send('Runtime.evaluate', { expression: REMOVE_EXPR, returnByValue: true });
      console.log(`[kds] 已移除皮肤 -> ${p.title || p.url}`);
    } finally { session.close(); }
  }
  console.log('[kds] 提示：彻底恢复请重启 Kimi（皮肤只存在于内存，重启即原生）。');
  process.exit(0);
}

const cssPath = path.join(__dirname, '..', 'themes', `${args.theme}.css`);
let css;
try {
  css = await readFile(cssPath, 'utf8');
} catch {
  console.error(`[kds] 找不到主题文件: ${cssPath}`);
  process.exit(1);
}

// 追加 Chat 页面（kimi.com 网页视图）的共享皮肤，复用主题里的 --kds-* 变量
try {
  css += '\n\n' + await readFile(path.join(__dirname, '..', 'themes', 'chat.css'), 'utf8');
} catch { /* chat.css 缺失时跳过，不影响 Work 界面 */ }

// 把 CSS 里的本地图片引用 url("assets/xxx.jpg") 内联为 data URI，
// 这样注入是纯内存操作，页面上不需要额外的本地服务器或文件访问权限
css = await inlineAssets(css, path.dirname(cssPath));

async function inlineAssets(cssText, themeDir) {
  const re = /url\((['"]?)(assets\/[^'")]+)\1\)/g;
  const jobs = [];
  cssText.replace(re, (m, q, rel) => { jobs.push(rel); return m; });
  for (const rel of [...new Set(jobs)]) {
    const file = path.join(themeDir, rel);
    try {
      const buf = await readFile(file);
      const mime = rel.endsWith('.png') ? 'image/png' : rel.endsWith('.webp') ? 'image/webp' : 'image/jpeg';
      const uri = `url("data:${mime};base64,${buf.toString('base64')}")`;
      cssText = cssText.split(`url("assets/${rel.slice(7)}")`).join(uri)
                       .split(`url('${rel}')`).join(uri)
                       .split(`url("${rel}")`).join(uri)
                       .split(`url(${rel})`).join(uri);
      console.log(`[kds] 已内联背景图 ${rel} (${Math.round(buf.length / 1024)} KB)`);
    } catch {
      console.error(`[kds] 警告：背景图不存在，跳过内联: ${file}`);
    }
  }
  return cssText;
}

/** 生成注入到页面里的 JS：写入 <style> 与标记 <meta>，幂等可重复执行 */
function buildInjectScript(themeName, cssText) {
  return `(() => {
    const ID = 'kimi-dream-skin-style';
    let style = document.getElementById(ID);
    if (!style) {
      style = document.createElement('style');
      style.id = ID;
      (document.head || document.documentElement).appendChild(style);
    }
    style.textContent = ${JSON.stringify(cssText)};
    let meta = document.querySelector('meta[name="kimi-dream-skin"]');
    if (!meta) {
      meta = document.createElement('meta');
      meta.name = 'kimi-dream-skin';
      (document.head || document.documentElement).appendChild(meta);
    }
    meta.content = ${JSON.stringify(themeName)};
    meta.setAttribute('data-injected-at', new Date().toISOString());
    return 'kds:ok:' + ${JSON.stringify(themeName)};
  })()`;
}

const MARKER_CHECK = `!!document.querySelector('meta[name="kimi-dream-skin"]')`;

/** 向单个页面 target 注入（当前文档立即生效 + 后续新文档自动生效） */
async function injectIntoPage(target, { silent = false } = {}) {
  const session = await CDPSession.connect(target.webSocketDebuggerUrl);
  try {
    await session.send('Page.enable');
    // 1) 注册"每个新文档加载前执行"的脚本 —— 页面跳转/刷新后主题仍在
    await session.send('Page.addScriptToEvaluateOnNewDocument', {
      source: buildInjectScript(args.theme, css),
    });
    // 2) 当前文档立即注入
    const { result, exceptionDetails } = await session.send('Runtime.evaluate', {
      expression: buildInjectScript(args.theme, css),
      returnByValue: true,
    });
    if (exceptionDetails) throw new Error(exceptionDetails.text || '注入脚本执行异常');
    if (!silent) console.log(`[kds] 已注入 "${args.theme}" -> ${target.title || target.url} (${result.value})`);
    return true;
  } finally {
    session.close();
  }
}

async function run() {
  const targets = await listTargets(Number(args.port));
  let pages;
  if (args['all-pages']) {
    pages = targets.filter((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  } else if (args.url) {
    pages = [pickMainPage(targets, { urlPattern: args.url })];
  } else {
    // 默认覆盖 Work 主界面 + Chat 网页视图（kimi.com），排除火山引擎内部页面
    pages = targets.filter((t) => t.type === 'page' && t.webSocketDebuggerUrl
      && (t.url.includes('kimi-agent.html') || t.url.includes('kimi.com')));
    if (!pages.length) pages = [pickMainPage(targets)];
  }
  for (const p of pages) await injectIntoPage(p);

  if (args.watch) {
    // 驻留模式：轮询 target 列表，对尚未带标记的页面补注入（覆盖新窗口/新标签，如 Chat 视图按需创建）
    console.log('[kds] 驻留模式已开启（每 2 秒巡检一次，Ctrl+C 退出）');
    for (;;) {
      await sleep(2000);
      let list;
      try { list = await listTargets(Number(args.port), { retries: 1, intervalMs: 200 }); }
      catch { console.log('[kds] 调试端口已断开，退出驻留。'); break; }
      const candidates = args['all-pages']
        ? list.filter((t) => t.type === 'page' && t.webSocketDebuggerUrl)
        : args.url
          ? [pickMainPage(list, { urlPattern: args.url })]
          : list.filter((t) => t.type === 'page' && t.webSocketDebuggerUrl
              && (t.url.includes('kimi-agent.html') || t.url.includes('kimi.com')));
      for (const t of candidates) {
        const session = await CDPSession.connect(t.webSocketDebuggerUrl).catch(() => null);
        if (!session) continue;
        try {
          const { result } = await session.send('Runtime.evaluate', { expression: MARKER_CHECK, returnByValue: true });
          const { result: themeNow } = await session.send('Runtime.evaluate', {
            expression: `document.querySelector('meta[name="kimi-dream-skin"]')?.content || ''`,
            returnByValue: true,
          });
          if (!result.value || themeNow.value !== args.theme) {
            await injectIntoPage(t);
          }
        } catch { /* 页面可能正在销毁，下一轮再试 */ }
        finally { session.close(); }
      }
    }
  }
}

run().catch((err) => {
  console.error(`[kds] 注入失败: ${err.message}`);
  process.exit(1);
});

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith('--')) {
      const key = argv[i].slice(2);
      if (i + 1 < argv.length && !argv[i + 1].startsWith('--')) out[key] = argv[++i];
      else out[key] = true;
    }
  }
  return out;
}
