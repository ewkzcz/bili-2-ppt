#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复用本机已登录浏览器，取 Bilibili 网页 AI 字幕。

只用浏览器会话本身，不读取、不打印、不复制任何 Cookie：脚本让页面自己调用
播放器用的那个接口（`/x/player/wbi/v2`），再把页面请求到的字幕 JSON 交回来。

浏览器一律静默后台运行、优先复用已登录 B站 的配置文件，具体优先级见
[bili-2-ppt/scripts/browser_session.py](../../scripts/browser_session.py)。

用法：

    python3 scripts/fetch_browser_ai_subtitles.py \\
      --bvid BVxxxx --out ./tmp_bili_extract --parts all
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# 共用实现放在调度器那一层：两个子技能都要「登录态 + 静默后台」这套逻辑，
# 各写一份会出现两套实现，所以统一从这里导入。
_SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_SKILL_ROOT / "scripts"))

from browser_session import resolve_session  # noqa: E402


def safe_slug(value: str) -> str:
    """把语言标识整理成能直接当文件名用的短串。"""
    cleaned = re.sub(r"[^\w一-鿿.-]+", "_", value, flags=re.UNICODE).strip("_")
    return cleaned[:40] or "subtitle"


def srt_timestamp(seconds: float) -> str:
    """秒转 SRT 时间戳。"""
    millis = int(round(seconds * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def run_cdp_eval(ws_url: str, js: str, navigate: str | None = None, wait_ms: int = 9000) -> dict:
    """把一段 JS 送到页面里执行，拿回 JSON 结果。"""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("未找到 node，无法驱动浏览器调试协议")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(js)
        js_path = handle.name
    cmd = [
        node,
        str(_SKILL_ROOT / "scripts" / "cdp_eval.mjs"),
        "--ws", ws_url,
        "--js-file", js_path,
        "--wait-ms", str(wait_ms),
        "--timeout-ms", "120000",
    ]
    if navigate:
        cmd.extend(["--navigate", navigate])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        Path(js_path).unlink(missing_ok=True)
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:
        raise RuntimeError(f"页面执行没有返回可解析的结果：{proc.stdout[-400:]} {proc.stderr[-400:]}") from exc
    if not payload.get("ok"):
        raise RuntimeError(f"页面执行失败：{payload.get('error')}")
    return payload.get("value") or {}


def probe_pages(ws_url: str, bvid: str, navigate: str) -> dict:
    """取分集列表，并逐集探测字幕接口。"""
    js = f"""
(async () => {{
  const bvid = {json.dumps(bvid)};
  const view = await fetch('https://api.bilibili.com/x/web-interface/view?bvid=' + bvid,
                           {{credentials: 'include'}}).then(r => r.json());
  if (view.code !== 0) return {{ ok: false, message: view.message }};
  const pages = view.data.pages || [];
  const results = [];
  for (const p of pages) {{
    const ep = 'https://api.bilibili.com/x/player/wbi/v2?bvid=' + encodeURIComponent(bvid)
             + '&cid=' + encodeURIComponent(p.cid);
    const j = await fetch(ep, {{credentials: 'include'}}).then(r => r.json());
    const subs = (j.data && j.data.subtitle && j.data.subtitle.subtitles) || [];
    results.push({{
      page: p.page,
      cid: p.cid,
      part: p.part,
      duration: p.duration,
      code: j.code,
      need_login_subtitle: j.data ? j.data.need_login_subtitle : null,
      subtitles: subs.map(s => ({{
        lan: s.lan, lan_doc: s.lan_doc, ai_status: s.ai_status, type: s.type,
        id_str: s.id_str, subtitle_url: s.subtitle_url || ''
      }})),
    }});
  }}
  return {{ ok: true, bvid: bvid, title: view.data.title, pages: results }};
}})()
""".strip()
    return run_cdp_eval(ws_url, js, navigate=navigate)


def fetch_subtitle_body(ws_url: str, url: str) -> dict:
    """在页面里请求字幕正文，避免直接请求时缺 Referer 被拒。"""
    js = f"""
(async () => {{
  const raw = {json.dumps(url)};
  const target = raw.startsWith('//') ? ('https:' + raw) : raw;
  const res = await fetch(target);
  const text = await res.text();
  try {{
    return {{ ok: true, status: res.status, body: JSON.parse(text) }};
  }} catch (e) {{
    return {{ ok: false, status: res.status, head: text.slice(0, 200) }};
  }}
}})()
""".strip()
    return run_cdp_eval(ws_url, js)


def write_subtitle_outputs(payload: dict, out_dir: Path, stem: str, lang: str, duration: float) -> dict:
    """按与页面字幕一致的命名写原始 JSON、纯文本、SRT 和定时分段。"""
    body = payload.get("body") or []
    slug = safe_slug(lang)
    json_path = out_dir / f"{stem}_{slug}.subtitle.json"
    txt_path = out_dir / f"{stem}_{slug}.txt"
    srt_path = out_dir / f"{stem}_{slug}.srt"
    segment_path = out_dir / f"{stem}_{slug}.json"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    segments = []
    for item in body:
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        segments.append({
            "start": round(float(item.get("from") or 0), 3),
            "end": round(float(item.get("to") or 0), 3),
            "text": text,
        })

    segment_path.write_text(
        json.dumps(
            {
                "source": "browser-ai-subtitle",
                "bvid": payload.get("bvid"),
                "lan": lang,
                "duration": duration,
                "segments": segments,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    txt_path.write_text(
        "\n".join(segment["text"] for segment in segments).rstrip() + "\n",
        encoding="utf-8",
    )

    srt_lines = []
    for index, segment in enumerate(segments, 1):
        srt_lines.extend([
            str(index),
            f"{srt_timestamp(segment['start'])} --> {srt_timestamp(segment['end'])}",
            segment["text"],
            "",
        ])
    srt_path.write_text("\n".join(srt_lines).rstrip() + "\n", encoding="utf-8")

    return {
        "json": str(json_path),
        "txt": str(txt_path),
        "srt": str(srt_path),
        "segments_json": str(segment_path),
        "segment_count": len(segments),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="复用已登录浏览器取网页 AI 字幕")
    parser.add_argument("--bvid", required=True, help="视频 BVID")
    parser.add_argument("--out", required=True, type=Path, help="输出目录")
    parser.add_argument("--page", type=int, default=1, help="分集页码，决定打开哪个播放页")
    parser.add_argument("--attach-port", type=int, default=None, help="复用这个调试端口上的浏览器")
    parser.add_argument("--port", type=int, default=9333, help="自起实例使用的调试端口")
    parser.add_argument("--browser", help="浏览器可执行文件路径")
    parser.add_argument("--profile-dir", type=Path,
                        default=Path.home() / ".cache" / "bili-browser-profile",
                        help="临时配置文件目录（未找到登录态时使用）")
    parser.add_argument("--clone-dir", type=Path,
                        default=Path.home() / ".cache" / "bili-browser-clone",
                        help="克隆已登录配置文件时的落地目录")
    parser.add_argument("--no-login", action="store_true",
                        help="不优先使用登录态配置文件，直接用临时配置文件")
    parser.add_argument("--keep-browser", action="store_true", help="结束后保留自起的浏览器")
    args = parser.parse_args()

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    video_url = f"https://www.bilibili.com/video/{args.bvid}/"
    if args.page > 1:
        video_url += f"?p={args.page}"

    session = resolve_session(
        attach_port=args.attach_port,
        prefer_login=not args.no_login,
        port=args.port,
        profile_dir=args.profile_dir,
        clone_dir=args.clone_dir,
        executable=args.browser,
    )
    print(f"浏览器会话：{session.note}", file=sys.stderr)
    if not session.logged_in:
        print("提示：当前会话没有 B站 登录态，AI 字幕接口很可能返回空列表", file=sys.stderr)

    try:
        probed = probe_pages(session.ws_url, args.bvid, video_url)
        if not probed.get("ok"):
            raise RuntimeError(f"取分集与字幕探测失败：{probed.get('message')}")

        (out_dir / "subtitle_probe.json").write_text(
            json.dumps(probed.get("pages") or [], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        manifest = []
        url_manifest = {
            "bvid": args.bvid,
            "title": probed.get("title"),
            "referer": video_url,
            "login": session.logged_in,
            "session_note": session.note,
            "results": probed.get("pages") or [],
        }
        (out_dir / "browser_ai_subtitle_urls.json").write_text(
            json.dumps(url_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        for page_info in probed.get("pages") or []:
            page = int(page_info.get("page") or 1)
            cid = page_info.get("cid")
            duration = float(page_info.get("duration") or 0)
            for sub in page_info.get("subtitles") or []:
                url = sub.get("subtitle_url") or ""
                if not url:
                    continue
                body = fetch_subtitle_body(session.ws_url, url)
                if not body.get("ok"):
                    print(f"第 {page} 集 {sub.get('lan')} 字幕取正文失败：{body}", file=sys.stderr)
                    continue
                payload = dict(body.get("body") or {})
                payload["bvid"] = args.bvid
                files = write_subtitle_outputs(
                    payload, out_dir, f"p{page:02d}_{cid}", sub.get("lan") or "zh", duration,
                )
                manifest.append({
                    "page": page,
                    "cid": cid,
                    "lan": sub.get("lan"),
                    "lan_doc": sub.get("lan_doc"),
                    "source": "browser-ai-subtitle",
                    "subtitle_url": url,
                    **files,
                })
                print(f"第 {page} 集 {sub.get('lan')}：{files['segment_count']} 段", file=sys.stderr)

        (out_dir / "subtitle_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(str(out_dir / "subtitle_manifest.json"))
        return 0 if manifest else 1
    finally:
        if not args.keep_browser:
            session.stop()


if __name__ == "__main__":
    raise SystemExit(main())
