#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在后台浏览器里按进度跳转截图。

只做「加载播放器、跳转、暂停、整幅截图、逐张记日志」这一件事：
不做去重（交给 dedupe_frames.py），不做 OCR，也不下载视频。

浏览器会话的选取（静默后台、优先复用已登录 B站 的实例）由调度器那一层的
[bili-2-ppt/scripts/browser_session.py](../../scripts/browser_session.py) 统一负责，
本脚本不再自己实现一套。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


_SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_SKILL_ROOT / "scripts"))

from browser_session import resolve_session  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="在后台浏览器里按计划跳转并整幅截图")
    parser.add_argument("--bvid", required=True, help="视频 BVID")
    parser.add_argument("--plan", required=True, type=Path, help="plan_keyframes.py 生成的跳转计划")
    parser.add_argument("--out", required=True, type=Path, help="截图输出根目录")
    parser.add_argument("--part", type=int, default=1, help="分集页码，决定输出子目录与播放地址")
    parser.add_argument("--attach-port", type=int, default=None,
                        help="复用这个调试端口上的浏览器，不另开窗口")
    parser.add_argument("--port", type=int, default=9334, help="自起实例使用的调试端口")
    parser.add_argument("--browser", help="浏览器可执行文件路径")
    parser.add_argument("--no-login", action="store_true",
                        help="不优先使用登录态配置文件，直接用临时配置文件")
    parser.add_argument("--profile-dir", type=Path,
                        default=Path.home() / ".cache" / "bili-browser-profile",
                        help="临时配置文件目录（未找到登录态时使用）")
    parser.add_argument("--clone-dir", type=Path,
                        default=Path.home() / ".cache" / "bili-browser-clone",
                        help="克隆已登录配置文件时的落地目录")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--settle-ms", type=int, default=450, help="跳转完成后的画面稳定等待时间")
    parser.add_argument("--timeout-ms", type=int, default=45000, help="单张截图的跳转超时")
    parser.add_argument("--keep-browser", action="store_true", help="采集结束后保留浏览器窗口")
    args = parser.parse_args(argv)

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    episodes = plan.get("episodes") or []
    episode = next((e for e in episodes if int(e.get("part", 0)) == args.part), None)
    if episode is None:
        raise SystemExit(f"跳转计划里没有第 {args.part} 集")
    times = [float(t) for t in episode.get("times") or []]
    if not times:
        raise SystemExit("跳转计划里这一集没有采样时间点")

    part_dir = args.out / f"P{args.part:02d}"
    part_dir.mkdir(parents=True, exist_ok=True)
    frames = [
        {"part": args.part, "time": t, "file": str(part_dir / f"{int(round(t)):05d}.png")}
        for t in times
    ]
    frames_plan = part_dir / "_frames.json"
    frames_plan.write_text(json.dumps({"frames": frames}, ensure_ascii=False, indent=2), encoding="utf-8")

    video_url = f"https://www.bilibili.com/video/{args.bvid}/"
    if args.part > 1:
        video_url += f"?p={args.part}"

    node = shutil.which("node")
    if not node:
        raise SystemExit("未找到 node，无法驱动浏览器调试协议")

    session = resolve_session(
        attach_port=args.attach_port,
        prefer_login=not args.no_login,
        port=args.port,
        profile_dir=args.profile_dir,
        clone_dir=args.clone_dir,
        window_size=f"{args.width},{args.height}",
        executable=args.browser,
    )
    print(f"浏览器会话：{session.note}", file=sys.stderr)

    log_path = args.out / "capture_log.jsonl"
    cmd = [
        node,
        str(Path(__file__).resolve().parent / "cdp_capture.mjs"),
        "--ws", session.ws_url,
        "--plan", str(frames_plan),
        "--out", str(args.out),
        "--log", str(log_path),
        "--url-template", video_url,
        "--width", str(args.width),
        "--height", str(args.height),
        "--settle-ms", str(args.settle_ms),
        "--timeout-ms", str(args.timeout_ms),
        "--web-fullscreen",
        "--hide-danmaku",
        "--session-note", session.note,
    ]
    try:
        code = subprocess.run(cmd).returncode
    finally:
        if not args.keep_browser:
            session.stop()

    if code != 0:
        print(f"截图未全部成功，退出码 {code}，详见 {log_path}", file=sys.stderr)
    print(str(log_path))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
