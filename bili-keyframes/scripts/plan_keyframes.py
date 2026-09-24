#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按视频时长生成可供可见播放器跳转的关键画面计划。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def sample_count(duration: float) -> int:
    """使用与关键帧流程一致的分段密度，长视频不按原速播放。"""
    if duration <= 180:
        return 4
    if duration <= 600:
        return 6
    if duration <= 1200:
        return 8
    if duration <= 1800:
        return 10
    if duration <= 2400:
        return 13
    if duration <= 3600:
        return 16
    return 20


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 Bilibili 关键画面跳转计划")
    parser.add_argument("metadata", type=Path, help="包含 episodes 数组的 metadata.json")
    parser.add_argument("--output", type=Path, default=Path("keyframe_plan.json"))
    args = parser.parse_args()

    data = json.loads(args.metadata.read_text(encoding="utf-8"))
    # Bilibili 接口通常把分集列表放在 data.pages，把 videos 作为数量；兼容两种常见归档形态。
    episodes = data.get("episodes") or data.get("pages") or data.get("videos")
    if not isinstance(episodes, list) and isinstance(data.get("data"), dict):
        episodes = data["data"].get("episodes") or data["data"].get("pages")
    episodes = episodes if isinstance(episodes, list) else []
    plan = []
    for index, episode in enumerate(episodes, start=1):
        duration = float(episode.get("duration", 0))
        count = sample_count(duration)
        times = [duration * (0.03 + 0.94 * i / max(count - 1, 1)) for i in range(count)]
        raw_part = episode.get("page", episode.get("part", index))
        part_number = int(raw_part) if str(raw_part).isdigit() else index
        title = episode.get("title") or episode.get("part", "")
        plan.append({"part": part_number, "title": title, "duration": duration, "times": [round(t, 2) for t in times]})

    result = {"method": "visible-player-progress-seek", "video_download": False, "ocr": False, "episodes": plan}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
