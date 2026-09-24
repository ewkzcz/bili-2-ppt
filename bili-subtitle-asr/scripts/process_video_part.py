#!/usr/bin/env python3
"""处理单个 Bilibili 分集的字幕、音频 ASR、质量校验和归档。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
EXTRACTOR = SCRIPT_DIR / "extract_bilibili.py"
ARCHIVER = SCRIPT_DIR / "archive_bili_materials.py"


def run_command(command: list[str]) -> None:
    """执行一个阶段并在失败时保留原始退出码。"""
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="处理单个 Bilibili 分集，不下载视频文件"
    )
    parser.add_argument("source", help="Bilibili 视频链接或 BVID")
    parser.add_argument("--part", type=int, required=True, help="分集页码，从 1 开始")
    parser.add_argument("--work-dir", required=True, help="当前分集的临时工作目录")
    parser.add_argument("--archive-dir", required=True, help="当前分集的归档目录")
    parser.add_argument("--download-audio", action="store_true", help="获取音频并转为 WAV")
    parser.add_argument("--transcribe", action="store_true", help="对 WAV 执行本地 ASR")
    parser.add_argument(
        "--audio-source",
        choices=["auto", "bilibili-api", "yt-dlp"],
        default="auto",
        help="音频来源",
    )
    parser.add_argument("--cookies-from-browser", help="yt-dlp 使用的浏览器登录态名称")
    parser.add_argument("--asr-backend", default="auto", help="ASR 后端")
    parser.add_argument("--asr-model", help="ASR 模型名称")
    parser.add_argument("--asr-device", default="auto", help="ASR 设备")
    parser.add_argument("--asr-compute-type", default="auto", help="Whisper 计算类型")
    parser.add_argument("--asr-language", default="zh", help="ASR 语言")
    parser.add_argument("--qwen-python", help="Qwen ASR 使用的 Python 可执行文件")
    parser.add_argument("--qwen-device-map", default="auto", help="Qwen device_map")
    parser.add_argument("--qwen-dtype", default="auto", help="Qwen dtype")
    parser.add_argument("--qwen-chunk-seconds", type=float, default=60.0, help="Qwen 音频分块秒数")
    parser.add_argument("--force", action="store_true", help="覆盖已有阶段输出")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    work_dir = Path(args.work_dir).resolve()
    archive_dir = Path(args.archive_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    extract_command = [
        sys.executable,
        str(EXTRACTOR),
        args.source,
        "--out",
        str(work_dir),
        "--parts",
        str(args.part),
        "--download-subtitles",
        "--audio-source",
        args.audio_source,
        "--asr-backend",
        args.asr_backend,
        "--asr-device",
        args.asr_device,
        "--asr-compute-type",
        args.asr_compute_type,
        "--asr-language",
        args.asr_language,
    ]
    if args.download_audio or args.transcribe:
        extract_command.append("--download-audio")
    if args.transcribe:
        extract_command.append("--transcribe")
    if args.cookies_from_browser:
        extract_command.extend(["--cookies-from-browser", args.cookies_from_browser])
    if args.asr_model:
        extract_command.extend(["--asr-model", args.asr_model])
    if args.qwen_python:
        extract_command.extend(["--qwen-python", args.qwen_python])
    extract_command.extend(["--qwen-device-map", args.qwen_device_map])
    extract_command.extend(["--qwen-dtype", args.qwen_dtype])
    extract_command.extend(["--qwen-chunk-seconds", str(args.qwen_chunk_seconds)])
    if args.force:
        extract_command.append("--force")
    run_command(extract_command)

    # 归档脚本只接收当前提取目录，因此归档范围天然限定为本次分集。
    run_command(
        [
            sys.executable,
            str(ARCHIVER),
            "--extract-dir",
            str(work_dir),
            "--archive-dir",
            str(archive_dir),
        ]
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
