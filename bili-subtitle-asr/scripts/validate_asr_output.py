#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 Bilibili 字幕/ASR 定时 JSON 的时间轴和来源字段。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="校验定时字幕或 ASR JSON")
    parser.add_argument("json_path", type=Path)
    parser.add_argument("--duration", type=float, required=True)
    args = parser.parse_args()

    data = json.loads(args.json_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if data.get("source") not in {"page-subtitle", "browser-ai-subtitle", "audio-asr", "opus-article"}:
        errors.append("source 必须明确区分页面字幕、网页 AI 字幕、音频 ASR 或图文")
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        errors.append("segments 为空或不是数组")
        segments = []
    previous_end = -1.0
    for index, segment in enumerate(segments, start=1):
        start, end = float(segment.get("start", -1)), float(segment.get("end", -1))
        if start < 0 or end <= start:
            errors.append(f"第 {index} 段时间无效：{start} -> {end}")
        if start + 0.5 < previous_end:
            errors.append(f"第 {index} 段与上一段严重倒序或重叠")
        if not str(segment.get("text", "")).strip():
            errors.append(f"第 {index} 段文本为空")
        previous_end = max(previous_end, end)
    if segments and previous_end < args.duration * 0.8:
        errors.append(f"时间轴只覆盖到 {previous_end:.1f}s，低于目标时长的 80%")
    if errors:
        print("校验失败：")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print(f"校验通过：{args.json_path}，segments={len(segments)}，covered_until={previous_end:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
