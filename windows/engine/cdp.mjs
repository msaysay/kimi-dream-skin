/**
 * cdp.mjs —— 轻量 Chrome DevTools Protocol (CDP) 客户端
 *
 * 零依赖：使用 Node >= 22 内置的全局 fetch 与 WebSocket。
 * 功能：
 *   1. listTargets(port)      —— 通过 HTTP /json 发现页面 target
 *   2. CDPSession             —— WebSocket 会话，支持命令调用与事件订阅
 *
 * 安全说明：本模块只连接 127.0.0.1 上的调试端口，不做任何远程访问。
 */

/** 通过 HTTP 接口列出所有调试 target */
export async function listTargets(port, { retries = 20, intervalMs = 500 } = {}) {
  const url = `http://127.0.0.1:${port}/json`;
  let lastErr;
  for (let i = 0; i < retries; i++) {
    try {
      const res = await fetch(url);
      if (res.ok) return await res.json();
      lastErr = new Error(`HTTP ${res.status}`);
    } catch (err) {
      lastErr = err;
    }
    await sleep(intervalMs);
  }
  throw new Error(`无法连接调试端口 ${port}（${retries} 次重试后放弃）: ${lastErr}`);
}

/**
 * 在 target 列表中挑选"主窗口页面"。
 * Kimi 桌面版的主界面是 app://localhost/kimi-agent.html；
 * 找不到时退而求其次取第一个 type === 'page' 的 target。
 */
export function pickMainPage(targets, { urlPattern } = {}) {
  const pages = targets.filter((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  if (pages.length === 0) throw new Error('没有发现任何 page 类型的 target');
  if (urlPattern) {
    const hit = pages.find((t) => t.url.includes(urlPattern));
    if (hit) return hit;
  }
  // 默认优先 kimi-agent 主界面
  const main = pages.find((t) => t.url.includes('kimi-agent.html'));
  return main || pages[0];
}

/** 一个 target 的 CDP WebSocket 会话 */
export class CDPSession {
  #ws;
  #nextId = 1;
  #pending = new Map(); // id -> {resolve, reject}
  #listeners = new Map(); // method -> Set<fn>

  static async connect(webSocketDebuggerUrl, { timeoutMs = 15000 } = {}) {
    const ws = new WebSocket(webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('WebSocket 连接超时')), timeoutMs);
      ws.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
      ws.addEventListener('error', (e) => { clearTimeout(timer); reject(new Error('WebSocket 连接失败')); }, { once: true });
    });
    return new CDPSession(ws);
  }

  constructor(ws) {
    this.#ws = ws;
    ws.addEventListener('message', (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      if (msg.id !== undefined) {
        const p = this.#pending.get(msg.id);
        if (!p) return;
        this.#pending.delete(msg.id);
        if (msg.error) p.reject(new Error(`CDP ${p.method} 失败: ${msg.error.message}`));
        else p.resolve(msg.result);
      } else if (msg.method) {
        const set = this.#listeners.get(msg.method);
        if (set) for (const fn of set) fn(msg.params);
        const any = this.#listeners.get('*');
        if (any) for (const fn of any) fn(msg);
      }
    });
  }

  /** 发送 CDP 命令，返回 result */
  send(method, params = {}, { timeoutMs = 30000 } = {}) {
    const id = this.#nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id);
        reject(new Error(`CDP ${method} 超时`));
      }, timeoutMs);
      this.#pending.set(id, {
        method,
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      this.#ws.send(JSON.stringify({ id, method, params }));
    });
  }

  /** 订阅 CDP 事件；method 为 '*' 时接收所有事件。返回取消订阅函数。 */
  on(method, fn) {
    if (!this.#listeners.has(method)) this.#listeners.set(method, new Set());
    this.#listeners.get(method).add(fn);
    return () => this.#listeners.get(method)?.delete(fn);
  }

  close() {
    try { this.#ws.close(); } catch { /* 忽略 */ }
  }
}

export function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
