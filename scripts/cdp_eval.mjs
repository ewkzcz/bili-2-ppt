// 在一个已连上的浏览器页面里执行一段 JS，把结果按 JSON 打出来。
// 供字幕子技能取网页 AI 字幕用；画面子技能有自己的分帧脚本，不复用这一个。
import { readFileSync } from 'node:fs';

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    switch (key) {
      case '--ws': out.ws = argv[i + 1]; i += 1; break;
      case '--js-file': out.jsFile = argv[i + 1]; i += 1; break;
      case '--navigate': out.navigate = argv[i + 1]; i += 1; break;
      case '--wait-ms': out.waitMs = Number(argv[i + 1]); i += 1; break;
      case '--timeout-ms': out.timeoutMs = Number(argv[i + 1]); i += 1; break;
      default: break;
    }
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const timeoutMs = Number.isFinite(args.timeoutMs) ? args.timeoutMs : 60000;
const waitMs = Number.isFinite(args.waitMs) ? args.waitMs : 8000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function connect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    ws.addEventListener('open', () => resolve(ws));
    ws.addEventListener('error', () => reject(new Error('WebSocket 连接失败')));
  });
}

function makeClient(ws) {
  let nextId = 1;
  const pending = new Map();
  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message));
      else resolve(msg.result);
    }
  });
  return (method, params = {}, timeout = 30000) => {
    const id = nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`CDP 超时: ${method}`));
      }, timeout);
      pending.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      ws.send(JSON.stringify({ id, method, params }));
    });
  };
}

async function main() {
  if (!args.ws || !args.jsFile) {
    console.error('缺少 --ws 或 --js-file');
    process.exit(2);
  }
  const ws = await connect(args.ws);
  const send = makeClient(ws);
  await send('Page.enable');
  await send('Runtime.enable');

  if (args.navigate) {
    await send('Page.navigate', { url: args.navigate });
    await sleep(waitMs);
  }

  const expression = readFileSync(args.jsFile, 'utf8');
  const result = await send(
    'Runtime.evaluate',
    { expression, awaitPromise: true, returnByValue: true },
    timeoutMs,
  );
  ws.close();

  if (result.exceptionDetails) {
    const detail = result.exceptionDetails;
    const message = (detail.exception && (detail.exception.description || detail.exception.value))
      || detail.text
      || 'unknown';
    console.log(JSON.stringify({ ok: false, error: String(message) }));
    process.exit(1);
  }
  console.log(JSON.stringify({ ok: true, value: result.result ? result.result.value : null }));
}

main().catch((err) => {
  console.log(JSON.stringify({ ok: false, error: String(err && err.message ? err.message : err) }));
  process.exit(1);
});
