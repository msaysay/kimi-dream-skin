/**
 * probe.mjs —— DOM 结构探测器
 *
 * 用途：连接 Kimi 调试端口，探测主窗口页面的 DOM 结构（侧栏 / 输入框 / 消息列表等），
 *       帮助主题 CSS 的选择器命中真实节点。Kimi 更新后选择器失效时，重新运行本工具即可。
 *
 * 用法： node probe.mjs --port 9333 [--json]
 */
import { listTargets, pickMainPage, CDPSession } from './cdp.mjs';

const args = parseArgs(process.argv.slice(2));
if (!args.port) {
  console.error('用法: node probe.mjs --port <调试端口> [--json]');
  process.exit(1);
}

const PROBE_EXPR = `(() => {
  // 输出一个节点的概要信息（标签 + id + class + 关键属性）
  const brief = (el) => {
    if (!el || el.nodeType !== 1) return null;
    const cls = (el.className && el.className.baseVal !== undefined)
      ? el.className.baseVal : (el.className || '');
    return {
      tag: el.tagName.toLowerCase(),
      id: el.id || undefined,
      class: String(cls).slice(0, 160) || undefined,
      role: el.getAttribute('role') || undefined,
      children: el.children.length,
    };
  };
  // 递归收集前 N 层结构
  const tree = (el, depth, maxDepth = 4) => {
    const node = brief(el);
    if (!node || depth >= maxDepth) return node;
    const kids = [];
    for (const c of Array.from(el.children).slice(0, 8)) {
      kids.push(tree(c, depth + 1, maxDepth));
    }
    if (kids.length) node.kids = kids;
    return node;
  };
  // 常见关键区域探测：输入框、按钮、导航、滚动容器
  const inputs = Array.from(document.querySelectorAll('textarea, [contenteditable="true"]'))
    .map((el) => ({ ...brief(el), placeholder: el.getAttribute('placeholder') || el.dataset?.placeholder }));
  const buttons = Array.from(document.querySelectorAll('button')).slice(0, 15)
    .map((el) => ({ ...brief(el), text: (el.textContent || '').trim().slice(0, 20) }));
  const scrollables = Array.from(document.querySelectorAll('*'))
    .filter((el) => {
      const s = getComputedStyle(el);
      return (s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 50;
    }).slice(0, 10).map(brief);
  const classes = new Set();
  document.querySelectorAll('[class]').forEach((el) => {
    String(el.className?.baseVal ?? el.className).split(/\\s+/).forEach((c) => {
      if (c && !/[\\[\\](){}.:#>+~]/.test(c)) classes.add(c);
    });
  });
  return {
    url: location.href,
    title: document.title,
    colorScheme: getComputedStyle(document.documentElement).colorScheme,
    bodyTree: tree(document.body, 0, 4),
    inputs,
    buttons,
    scrollables,
    classCount: classes.size,
    classSample: Array.from(classes).sort().slice(0, 300),
  };
})()`;

const targets = await listTargets(Number(args.port));
const page = pickMainPage(targets);
console.error(`[probe] 目标页面: ${page.title} | ${page.url}`);

const session = await CDPSession.connect(page.webSocketDebuggerUrl);
await session.send('Runtime.enable');
const { result } = await session.send('Runtime.evaluate', {
  expression: PROBE_EXPR,
  returnByValue: true,
  awaitPromise: false,
});
session.close();

if (args.json) {
  console.log(JSON.stringify(result.value, null, 2));
} else {
  const v = result.value;
  console.log('URL:', v.url);
  console.log('标题:', v.title);
  console.log('colorScheme:', v.colorScheme);
  console.log('\n== body 结构（4 层）==');
  printTree(v.bodyTree, 0);
  console.log('\n== 输入框 ==');
  console.log(JSON.stringify(v.inputs, null, 2));
  console.log('\n== 按钮（前 15 个）==');
  for (const b of v.buttons) console.log(`  <${b.tag}> class="${b.class || ''}" text="${b.text || ''}"`);
  console.log('\n== 可滚动容器 ==');
  for (const s of v.scrollables) console.log(`  <${s.tag}> class="${s.class || ''}"`);
  console.log(`\n== class 总数: ${v.classCount}，样本如下 ==`);
  console.log(v.classSample.join(' '));
}

function printTree(node, depth) {
  if (!node) return;
  const indent = '  '.repeat(depth);
  console.log(`${indent}<${node.tag}${node.id ? '#' + node.id : ''}> class="${node.class || ''}"${node.role ? ' role=' + node.role : ''} (${node.children} 子)`);
  for (const k of node.kids || []) printTree(k, depth + 1);
}

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
