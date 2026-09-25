#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按视频时长生成可供可见播放器跳转的关键画面计划。"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path


# 默认每隔多少秒采一张。宁可多采：重复画面由去重收拾，漏采的画面事后补不回来。
# 按固定张数分档（比如 14 分钟只采 8 张）会漏掉大半幻灯片，已废弃。
DEFAULT_INTERVAL_SECONDS = 10.0
# 避开片头第一帧和片尾自动切集的区域（秒）
EDGE_MARGIN_SECONDS = 2.0


def sample_times(duration: float, interval: float) -> list[float]:
    """按固定间隔在 [片头余量, 时长 - 片尾余量] 内均匀取点，每个间隔取中点。"""
    start, end = EDGE_MARGIN_SECONDS, max(duration - EDGE_MARGIN_SECONDS, EDGE_MARGIN_SECONDS)
    count = max(1, int((end - start) // interval) + 1)
    return [round(min(start + interval * (i + 0.5), end), 2) for i in range(count)]


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 Bilibili 关键画面跳转计划")
    parser.add_argument("metadata", type=Path, help="包含 episodes 数组的 metadata.json")
    # 中间文件默认放系统临时目录，不落进当前目录或仓库
    parser.add_argument("--output", type=Path,
                        default=Path(tempfile.gettempdir()) / "bili-2-ppt" / "keyframe_plan.json")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS,
                        help="采样间隔秒数，默认 10；画面切换特别快的可以再调小")
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
        times = sample_times(duration, args.interval)
        raw_part = episode.get("page", episode.get("part", index))
        part_number = int(raw_part) if str(raw_part).isdigit() else index
        title = episode.get("title") or episode.get("part", "")
        plan.append({"part": part_number, "title": title, "duration": duration, "times": times})

    result = {"method": "visible-player-progress-seek", "interval": args.interval, "video_download": False, "ocr": False, "episodes": plan}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
