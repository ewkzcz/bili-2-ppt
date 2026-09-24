// 通过 Chrome DevTools Protocol 在后台播放器里跳转并整幅截图。
// 只做「加载 → 网页全屏 → 隐藏弹幕 → 定位 → 暂停 → 截图」，不做去重、不做 OCR、不下载视频。
import { writeFileSync, appendFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

function parseArgs(argv) {
  const out = { webFullscreen: false, hideDanmaku: false };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const val = argv[i + 1];
    switch (key) {
      case '--ws': out.ws = val; i += 1; break;
      case '--plan': out.plan = val; i += 1; break;
      case '--out': out.out = val; i += 1; break;
      case '--log': out.log = val; i += 1; break;
      case '--url-template': out.url = val; i += 1; break;
      case '--width': out.width = Number(val); i += 1; break;
      case '--height': out.height = Number(val); i += 1; break;
      case '--settle-ms': out.settleMs = Number(val); i += 1; break;
      case '--timeout-ms': out.timeoutMs = Number(val); i += 1; break;
      case '--web-fullscreen': out.webFullscreen = true; break;
      case '--hide-danmaku': out.hideDanmaku = true; break;
      case '--session-note': out.sessionNote = val; i += 1; break;
      default: break;
    }
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
if (!args.ws || !args.plan || !args.out || !args.log) {
  console.error('缺少必需参数');
  process.exit(2);
}
if (!Number.isFinite(args.settleMs)) args.settleMs = 450;
if (!Number.isFinite(args.timeoutMs)) args.timeoutMs = 25000;
if (!Number.isFinite(args.width)) args.width = 1920;
if (!Number.isFinite(args.height)) args.height = 1080;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class CDP {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(`${msg.error.message}`));
        else resolve(msg.result);
      }
    });
  }
  send(method, params = {}, timeoutMs = 30000) {
    const id = this.nextId++;
    const payload = JSON.stringify({ id, method, params });
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP 超时: ${method}`));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      this.ws.send(payload);
    });
  }
  async eval(expression, awaitPromise = true, timeoutMs = 30000) {
    const res = await this.send(
      'Runtime.evaluate',
      { expression, awaitPromise, returnByValue: true },
      timeoutMs,
    );
    if (res.exceptionDetails) {
      const detail = res.exceptionDetails;
      const message = (detail.exception && (detail.exception.description || detail.exception.value))
        || detail.text
        || 'unknown';
      throw new Error(`页面执行失败: ${message}`);
    }
    return res.result ? res.result.value : undefined;
  }
}

function connect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    ws.addEventListener('open', () => resolve(ws));
    ws.addEventListener('error', () => reject(new Error('WebSocket 连接失败')));
  });
}

// 等待播放器加载到可以定位的状态。
async function waitForPlayer(cdp, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const state = await cdp.eval(`(() => {
      const v = document.querySelector('video');
      if (!v) return { ok: false, why: 'no-video' };
      return { ok: v.readyState >= 2 && v.duration > 0, why: 'rs' + v.readyState, duration: v.duration || 0 };
    })()`);
    if (state && state.ok) return state;
    await sleep(400);
  }
  throw new Error('播放器在超时前没有进入可定位状态');
}

// 切到网页全屏，让播放器铺满视口。播放器控件是懒渲染的，要等它出现。
async function enterWebFullscreen(cdp, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const res = await cdp.eval(`(() => {
      const btn = document.querySelector('.bpx-player-ctrl-btn.bpx-player-ctrl-web');
      if (!btn) return { ok: false, why: 'button-not-rendered' };
      btn.click();
      return { ok: true };
    })()`);
    if (res && res.ok) {
      await sleep(900);
      return { ok: true, method: 'web-fullscreen-button' };
    }
    await sleep(500);
  }
  // 控件始终没渲染出来时，把播放器容器直接撑满视口，等价于网页全屏。
  const fallback = await cdp.eval(`(() => {
    const box = document.querySelector('.bpx-player-container');
    if (!box) return { ok: false, why: 'no-player-container' };
    box.style.setProperty('position', 'fixed', 'important');
    box.style.setProperty('left', '0', 'important');
    box.style.setProperty('top', '0', 'important');
    box.style.setProperty('width', '100vw', 'important');
    box.style.setProperty('height', '100vh', 'important');
    box.style.setProperty('z-index', '2147483647', 'important');
    document.documentElement.style.setProperty('overflow', 'hidden', 'important');
    return { ok: true };
  })()`);
  await sleep(700);
  return { ...fallback, method: 'container-fill-viewport' };
}

// 关掉盖在画面上的浮层（登录提示条这类），它们是提示不是内容。
async function hideOverlays(cdp) {
  return cdp.eval(`(() => {
    const hidden = [];
    document.querySelectorAll('.bpx-player-toast-auto, .bpx-player-toast-fixed, .bpx-player-toast-wrap').forEach(el => {
      el.style.setProperty('display', 'none', 'important');
      hidden.push(el.className);
    });
    return { hidden };
  })()`);
}

// 关掉遮住画面的登录弹窗；关不掉就如实记进日志。
async function dismissLoginPopup(cdp) {
  return cdp.eval(`(() => {
    const close = document.querySelector('.bili-mini-close-icon, .login-scan-close, .bgi-close, .bili-mini-close');
    if (close) { close.click(); return { closed: true }; }
    const mask = document.querySelector('.bili-mini-mask, .bpx-player-login-mask');
    return { closed: false, has_mask: !!mask };
  })()`);
}

// 关掉弹幕层，弹幕是观众即时发言，不属于讲解内容。
async function hideDanmaku(cdp) {
  return cdp.eval(`(() => {
    const hidden = [];
    document.querySelectorAll('.bpx-player-row-dm-wrap, .bpx-player-adv-dm-wrap, .bpx-player-bas-dm-wrap, .bpx-player-cmd-dm-wrap, .bpx-player-dm-mask-wrap, .bpx-player-render-dm-wrap, .bpx-player-dm-svg-mask-wrap').forEach(el => {
      el.style.setProperty('display', 'none', 'important');
      hidden.push(el.className);
    });
    return { hidden_count: hidden.length };
  })()`);
}

// 跳到目标时间点，暂停后返回实际位置。
async function seekAndPause(cdp, targetTime) {
  return cdp.eval(`new Promise((resolve) => {
    const v = document.querySelector('video');
    if (!v) { resolve({ ok: false, why: 'no-video' }); return; }
    // 窗口被遮挡时 requestAnimationFrame 会被节流，这里只用 setTimeout 保证一定会回报结果
    const finish = () => {
      v.pause();
      setTimeout(() => {
        resolve({
          ok: true,
          current_time: v.currentTime,
          duration: v.duration,
          paused: v.paused,
          video_width: v.videoWidth,
          video_height: v.videoHeight,
        });
      }, 120);
    };
    try { v.pause(); } catch (e) { /* 暂停失败不影响定位 */ }
    const target = Math.max(0, Math.min(${targetTime}, Math.max(v.duration - 0.2, 0)));
    if (Math.abs(v.currentTime - target) < 0.25 && v.readyState >= 2) { finish(); return; }
    let done = false;
    const onSeeked = () => { if (done) return; done = true; setTimeout(finish, 180); };
    v.addEventListener('seeked', onSeeked, { once: true });
    setTimeout(onSeeked, 6000);
    v.currentTime = target;
  })`);
}

async function main() {
  const plan = JSON.parse((await import('node:fs')).readFileSync(args.plan, 'utf8'));
  const frames = plan.frames || [];
  const ws = await connect(args.ws);
  const cdp = new CDP(ws);

  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: args.width,
    height: args.height,
    deviceScaleFactor: 1,
    mobile: false,
  });

  await cdp.send('Page.navigate', { url: args.url });
  await waitForPlayer(cdp, args.timeoutMs);

  const setup = {
    part: frames.length ? frames[0].part : 1,
    url: args.url,
    session_note: args.sessionNote || null,
  };
  if (args.webFullscreen) setup.web_fullscreen = await enterWebFullscreen(cdp);
  setup.overlays = await hideOverlays(cdp);
  setup.login_popup = await dismissLoginPopup(cdp);
  if (args.hideDanmaku) setup.danmaku = await hideDanmaku(cdp);

  let failures = 0;
  for (const frame of frames) {
    const record = {
      part: frame.part,
      requested_time: frame.time,
      actual_time: null,
      duration: null,
      paused: null,
      file: null,
      source: 'background-chrome-player',
      ocr: false,
    };
    try {
      const res = await seekAndPause(cdp, frame.time);
      if (!res || !res.ok) throw new Error(res && res.why ? res.why : '定位失败');
      await sleep(args.settleMs);
      const shot = await cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true }, args.timeoutMs);
      const abs = frame.file;
      mkdirSync(dirname(abs), { recursive: true });
      writeFileSync(abs, Buffer.from(shot.data, 'base64'));
      record.actual_time = Number(res.current_time.toFixed(2));
      record.duration = Number((res.duration || 0).toFixed(2));
      record.paused = res.paused;
      record.video_width = res.video_width;
      record.video_height = res.video_height;
      record.file = abs.slice(args.out.length).replace(/^[\\/]/, '');
      if (Math.abs(record.actual_time - frame.time) > 2.5) {
        record.error = `定位偏差过大: 目标 ${frame.time} 实际 ${record.actual_time}`;
        failures += 1;
      }
    } catch (err) {
      record.error = String(err && err.message ? err.message : err);
      failures += 1;
    }
    appendFileSync(args.log, `${JSON.stringify(record)}\n`, 'utf8');
    console.log(`${record.error ? 'FAIL' : 'OK  '} ${frame.time}s -> ${record.file || '-'}${record.error ? ' ' + record.error : ''}`);
  }

  // 采集环境信息单独落一份，capture_log.jsonl 只放每张截图的记录
  writeFileSync(`${args.out}/capture_setup.json`, `${JSON.stringify(setup, null, 2)}\n`, 'utf8');
  ws.close();
  process.exit(failures === frames.length && frames.length > 0 ? 1 : 0);
}

main().catch((err) => {
  console.error(String(err && err.stack ? err.stack : err));
  process.exit(1);
});
