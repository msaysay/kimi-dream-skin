/**
 * verify.mjs —— 验证注入是否生效并截图取证
 *
 * 用法：
 *   node verify.mjs --port 9333 --out ..\..\screenshots\dream.png
 *   node verify.mjs --port 9333 --out shot.png --url kimi-agent.html
 *
 * 输出 JSON：页面标题 / URL / 注入标记 / 主题名 / 截图路径。
 */
import { writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { listTargets, pickMainPage, CDPSession } from './cdp.mjs';

const args = parseArgs(process.argv.slice(2));
if (!args.port) {
  console.error('用法: node verify.mjs --port <端口> [--out <截图路径.png>] [--url <片段>]');
  process.exit(1);
}

const targets = await listTargets(Number(args.port));
const page = pickMainPage(targets, { urlPattern: args.url });

const session = await CDPSession.connect(page.webSocketDebuggerUrl);
try {
  await session.send('Page.enable');
  const CHECK = `(() => {
    const meta = document.querySelector('meta[name="kimi-dream-skin"]');
    const style = document.getElementById('kimi-dream-skin-style');
    return {
      title: document.title,
      url: location.href,
      markerPresent: !!meta,
      theme: meta ? meta.content : null,
      injectedAt: meta ? meta.getAttribute('data-injected-at') : null,
      stylePresent: !!style,
      styleBytes: style ? style.textContent.length : 0,
      viewport: { w: innerWidth, h: innerHeight },
    };
  })()`;
  const { result } = await session.send('Runtime.evaluate', { expression: CHECK, returnByValue: true });
  const info = result.value;

  let screenshot = null;
  if (args.out) {
    // 先让页面合成一帧，避免截到透明/未绘制内容
    await session.send('Page.bringToFront').catch(() => {});
    const shot = await session.send('Page.captureScreenshot', { format: 'png' }, { timeoutMs: 60000 });
    const outPath = path.resolve(process.cwd(), args.out);
    await mkdir(path.dirname(outPath), { recursive: true });
    await writeFile(outPath, Buffer.from(shot.data, 'base64'));
    screenshot = outPath;
  }

  const report = { ...info, screenshot };
  console.log(JSON.stringify(report, null, 2));
  if (!info.markerPresent) {
    console.error('[kds] 警告：未检测到注入标记 <meta name="kimi-dream-skin">');
    process.exitCode = 2;
  }
} finally {
  session.close();
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
