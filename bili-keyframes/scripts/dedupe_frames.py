#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对可见播放器截图做画面去重，产出保留清单、去重日志和补采样提示。

判定用灰度 96×54 缩略图的平均像素差，纯 Pillow 实现，不依赖 numpy。
连续重复画面保留的是最后一帧：采集到的重复画面里，最后一帧才是画面定格、内容完整的那一张，
第一帧可能停在淡入或代码刚敲到一半的中间态。

需要 Pillow。
"""

from __future__ import annotations

import argparse
import bisect
import json
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageChops, ImageStat


SIGNATURE_SIZE = (96, 54)
# 默认阈值。旧默认 9 会把版式相近、内容不同的幻灯片（同一模版的相邻页）合并掉；
# 实测 2–3 才与人眼判断一致，宁可多留几张近似的，也不要吞掉不同的画面。
DEFAULT_THRESHOLD = 3.0
# 缩略图总览：每张缩略图的尺寸与每行张数
SHEET_THUMB = (240, 135)
SHEET_COLUMNS = 8
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
FRAME_LIST_KEYS = ("frames", "captures", "records", "entries", "logs", "keyframes")
TIME_KEYS = ("actual_time", "requested_time", "time", "seconds", "timestamp")


def frame_signature(image: Image.Image, crop: tuple[int, int, int, int] | None = None) -> Image.Image:
    """把画面压成灰度缩略图，作为变化检测的签名。"""
    if crop is not None:
        x, y, width, height = crop
        image = image.crop((x, y, x + width, y + height))
    return image.convert("L").resize(SIGNATURE_SIZE, Image.Resampling.BILINEAR)


def frame_distance(left: Image.Image, right: Image.Image) -> float:
    """返回两张签名的平均像素差。"""
    return float(ImageStat.Stat(ImageChops.difference(left, right)).mean[0])


def iter_records(payload: Any) -> Iterable[dict[str, Any]]:
    """兼容 manifest 的常见归档形态，取出帧记录。"""
    if isinstance(payload, list):
        for item in payload:
            yield item if isinstance(item, dict) else {"file": item}
        return
    if not isinstance(payload, dict):
        return
    for key in FRAME_LIST_KEYS:
        for item in payload.get(key) or []:
            yield item if isinstance(item, dict) else {"file": item}
    for episode in payload.get("episodes") or []:
        if not isinstance(episode, dict):
            continue
        inherited = {key: value for key, value in episode.items() if key not in FRAME_LIST_KEYS and key != "episodes"}
        for key in FRAME_LIST_KEYS:
            for item in episode.get(key) or []:
                yield {**inherited, **(item if isinstance(item, dict) else {"file": item})}


def load_frame_records(source: Path) -> list[dict[str, Any]]:
    """从帧目录、manifest.json 或 capture_log.jsonl 读出帧记录。"""
    if source.is_dir():
        # 采集默认存 PNG，但从别处恢复的画面常常是 JPEG，两种都要认
        paths = [
            path
            for path in source.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ]
        return [{"file": str(path)} for path in sorted(paths)]
    text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return list(iter_records(json.loads(text)))


def infer_part(path: Path) -> int | None:
    """从路径里的 P05 这类目录名推断分集号。"""
    for segment in reversed(path.parts[:-1]):
        if len(segment) > 1 and segment[0] in "Pp" and segment[1:].isdigit():
            return int(segment[1:])
    return None


def infer_time(path: Path) -> float | None:
    """从 00707.png 这类文件名推断秒数。"""
    digits = "".join(character for character in path.stem if character.isdigit())
    return float(digits) if digits else None


def build_entry(record: dict[str, Any], source: Path, fallback_part: int, crop: tuple[int, int, int, int] | None) -> dict[str, Any]:
    raw_path = Path(str(record.get("file") or record.get("path") or ""))
    path = raw_path if raw_path.is_absolute() or raw_path.exists() else source.parent / raw_path
    with Image.open(path) as image:
        signature = frame_signature(image, crop)
    part = record.get("part", record.get("page", record.get("episode")))
    time_value = next((float(record[key]) for key in TIME_KEYS if isinstance(record.get(key), (int, float))), None)
    if time_value is None:
        time_value = infer_time(path)
    return {
        "part": int(part) if str(part).isdigit() else (infer_part(path) or fallback_part),
        "time": time_value,
        "file": str(path),
        "signature": signature,
    }


def group_runs(entries: list[dict[str, Any]], threshold: float) -> list[list[int]]:
    """把画面切成「同一视觉状态」的连续段。

    相邻帧比较负责切断硬切页；同时用「与本段首帧的累积差异」兜底，
    否则缓慢平移或渐显会被整体判成重复，只留下最后一帧而丢掉过程信息。
    """
    runs: list[list[int]] = [[0]]
    for index in range(1, len(entries)):
        step = frame_distance(entries[index - 1]["signature"], entries[index]["signature"])
        drift = frame_distance(entries[runs[-1][0]]["signature"], entries[index]["signature"])
        if step < threshold and drift < threshold:
            runs[-1].append(index)
        else:
            runs.append([index])
    return runs


def collect_hints(kept_entries: list[dict[str, Any]], max_gap: float) -> list[dict[str, Any]]:
    """相邻保留画面跨度超过 max_gap 时，给出区间中点的补采样建议。"""
    hints: list[dict[str, Any]] = []
    timed = [entry for entry in kept_entries if entry["time"] is not None]
    for before, after in zip(timed, timed[1:]):
        gap = after["time"] - before["time"]
        if gap <= max_gap:
            continue
        hints.append(
            {
                "part": before["part"],
                "from_time": before["time"],
                "to_time": after["time"],
                "gap": round(gap, 2),
                "suggest_time": round((before["time"] + after["time"]) / 2, 2),
                "before_file": before["file"],
                "after_file": after["file"],
            }
        )
    return hints


def dedupe_part(entries: list[dict[str, Any]], threshold: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回该分集的保留帧和被折进保留帧的重复帧。"""
    entries = sorted(entries, key=lambda entry: (entry["time"] if entry["time"] is not None else float("inf"), entry["file"]))
    runs = group_runs(entries, threshold)
    kept_indexes = [run[-1] for run in runs]
    # run_start 记下这张画面最早出现的时间：保留的是一段的最后一帧，画面其实从段首就在屏幕上，
    # 知识树按字幕时间段分派画面时要用整段区间。
    kept = [{**entries[run[-1]], "run_start": entries[run[0]]["time"]} for run in runs]
    dropped: list[dict[str, Any]] = []
    for run in runs:
        folded_into = entries[run[-1]]
        for index in run[:-1]:
            entry = entries[index]
            position = bisect.bisect_left(kept_indexes, index)
            dropped.append(
                {
                    "part": entry["part"],
                    "time": entry["time"],
                    "file": entry["file"],
                    "folded_into": folded_into["file"],
                    "kept_before": entries[kept_indexes[position - 1]]["file"] if position > 0 else None,
                    "kept_after": entries[kept_indexes[position]]["file"] if position < len(kept_indexes) else None,
                    "distance_prev_frame": round(frame_distance(entries[index - 1]["signature"], entry["signature"]), 3) if index > 0 else None,
                    "distance_run_start": round(frame_distance(entries[run[0]]["signature"], entry["signature"]), 3),
                    "reason": "visual-duplicate",
                }
            )
    return kept, dropped


def dedupe_across_parts(
    kept: list[dict[str, Any]], threshold: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把跨分集重复的帧折掉，每个画面只留最早出现的那一份。

    同一张幻灯片经常跨过分集边界继续挂在屏幕上，采集时会在两个分集里各留一份。
    保留最早出现的那份，是因为它离这个知识点被引入的位置最近，
    文档层把它们归到同一节时不会出现「同一张图讲两件事」。

    判定和分集内一致：灰度缩略图的平均像素差。这里不做「保留最后一张」——
    跨分集的重复不是同一段停留，没有「定格更完整」这回事。
    """
    ordered = sorted(kept, key=lambda entry: (entry["part"], entry["time"] if entry["time"] is not None else float("inf")))
    survivors: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    for entry in ordered:
        duplicate_of = None
        for candidate in survivors:
            if frame_distance(candidate["signature"], entry["signature"]) < threshold:
                duplicate_of = candidate
                break
        if duplicate_of is None:
            survivors.append(entry)
            continue
        dropped.append(
            {
                "part": entry["part"],
                "time": entry["time"],
                "file": entry["file"],
                "folded_into": duplicate_of["file"],
                "reason": "same-frame-in-another-part",
                "distance": round(frame_distance(duplicate_of["signature"], entry["signature"]), 3),
            }
        )
    return survivors, dropped


def write_contact_sheet(entries: list[dict[str, Any]], kept: list[dict[str, Any]], path: Path) -> None:
    """所有帧按时间排成网格：保留的加绿框，被合并的变暗。用来校准阈值——
    相邻两张明显不同却有一张变暗，说明阈值太高，调低后只重跑去重。"""
    from PIL import ImageDraw, ImageEnhance

    kept_files = {entry["file"] for entry in kept}
    ordered = sorted(entries, key=lambda e: (e["part"], e["time"]))
    if not ordered:
        return
    width, height = SHEET_THUMB
    label = 18
    rows = (len(ordered) + SHEET_COLUMNS - 1) // SHEET_COLUMNS
    sheet = Image.new("RGB", (SHEET_COLUMNS * width, rows * (height + label)), (32, 32, 32))
    draw = ImageDraw.Draw(sheet)
    for index, entry in enumerate(ordered):
        x, y = (index % SHEET_COLUMNS) * width, (index // SHEET_COLUMNS) * (height + label)
        try:
            with Image.open(entry["file"]) as image:
                thumb = image.convert("RGB").resize(SHEET_THUMB)
        except OSError:
            continue
        is_kept = entry["file"] in kept_files
        if not is_kept:
            thumb = ImageEnhance.Brightness(thumb).enhance(0.35)
        sheet.paste(thumb, (x, y))
        if is_kept:
            draw.rectangle([x, y, x + width - 1, y + height - 1], outline=(46, 204, 113), width=4)
        draw.text((x + 4, y + height + 2), f"P{entry['part']:02d} {entry['time']:.0f}s" + (" KEEP" if is_kept else ""),
                  fill=(230, 230, 230))
    sheet.save(path, quality=80)


def main() -> int:
    parser = argparse.ArgumentParser(description="对可见播放器截图做画面去重，并提示需要补采样的时间区间")
    parser.add_argument("source", type=Path, help="帧目录、manifest.json 或 capture_log.jsonl")
    parser.add_argument("--output-dir", type=Path, default=None, help="默认写到输入同级的 dedup/")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"平均像素差低于该值视为同一画面，默认 {DEFAULT_THRESHOLD}；看缩略图总览发现不同画面被合并就调低")
    parser.add_argument("--max-gap", type=float, default=90.0, help="相邻保留画面跨度超过该秒数时提示补采样")
    parser.add_argument(
        "--cross-threshold",
        type=float,
        default=None,
        help="跨分集去重的阈值，默认取 --threshold 的三分之一（跨集比对要比集内严得多）",
    )
    parser.add_argument("--crop", type=int, nargs=4, default=None, metavar=("X", "Y", "W", "H"), help="只比对播放器画面区域")
    args = parser.parse_args()

    output_dir = args.output_dir or ((args.source if args.source.is_dir() else args.source.parent) / "dedup")
    records = load_frame_records(args.source)
    if not records:
        print(f"没有找到可去重的帧：{args.source}")
        return 1

    crop = tuple(args.crop) if args.crop else None
    entries: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        try:
            entries.append(build_entry(record, args.source, index, crop))
        except Exception as error:  # 单帧读不出时记录并继续，避免整批失败
            errors.append({"file": str(record.get("file") or record.get("path") or ""), "error": str(error)})

    by_part: dict[int, list[dict[str, Any]]] = {}
    for entry in entries:
        by_part.setdefault(entry["part"], []).append(entry)

    output_dir.mkdir(parents=True, exist_ok=True)
    parts_report: list[dict[str, Any]] = []
    all_kept: list[dict[str, Any]] = []
    all_dropped: list[dict[str, Any]] = []
    for part in sorted(by_part):
        kept, dropped = dedupe_part(by_part[part], args.threshold)
        all_kept.extend(kept)
        all_dropped.extend(dropped)

    # 跨分集再比一次。同一张幻灯片经常跨过分集边界继续挂在屏幕上，
    # 只比分集内部的话，这些帧会各自留下一份，到文档里就是同一张图出现两次。
    #
    # 这一步的阈值必须比集内严得多。集内比的是相邻帧，本来就是同一段停留，
    # 阈值可以放宽；跨集比的是两张互不相干的画面，同样的阈值会把
    # 「两张长得像的深色 IDE 截图」也判成重复。真重复的签名距离在 2 以内，
    # 相似但不同的画面在 4 以上，默认取集内阈值的 1/3 落在两者之间。
    cross_threshold = args.cross_threshold
    if cross_threshold is None:
        cross_threshold = max(args.threshold / 3.0, 1.0)
    all_kept, cross_dropped = dedupe_across_parts(all_kept, cross_threshold)
    all_dropped.extend(cross_dropped)

    kept_by_part: dict[int, list[dict[str, Any]]] = {}
    a_part_dropped: dict[int, int] = {}
    for entry in all_kept:
        kept_by_part.setdefault(entry["part"], []).append(entry)
    for entry in all_dropped:
        a_part_dropped[entry["part"]] = a_part_dropped.get(entry["part"], 0) + 1
    for part in sorted(by_part):
        parts_report.append(
            {
                "part": part,
                "kept": [
                    {"time": entry["time"], "run_start": entry.get("run_start", entry["time"]), "file": entry["file"]}
                    for entry in kept_by_part.get(part, [])
                ],
                "kept_count": len(kept_by_part.get(part, [])),
                "dropped_count": a_part_dropped.get(part, 0),
            }
        )

    hints = collect_hints(all_kept, args.max_gap)
    write_contact_sheet(entries, all_kept, output_dir / "contact_sheet.jpg")
    (output_dir / "dedup_keep.json").write_text(
        json.dumps(
            {
                "method": "gray-thumbnail-mean-abs-diff",
                "threshold": args.threshold,
                "keep_policy": "last-frame-of-each-run",
                "parts": parts_report,
                "kept_total": len(all_kept),
                "dropped_total": len(all_dropped),
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    with (output_dir / "dedup_log.jsonl").open("w", encoding="utf-8") as handle:
        for record in all_dropped:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    (output_dir / "resample_hints.json").write_text(
        json.dumps({"max_gap": args.max_gap, "hints": hints}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    for report in parts_report:
        print(f"P{report['part']:02d} 保留 {report['kept_count']} 丢弃 {report['dropped_count']}")
    print(f"合计 保留 {len(all_kept)} 丢弃 {len(all_dropped)} 补采样建议 {len(hints)}")
    if errors:
        print(f"读取失败 {len(errors)} 帧，见 dedup_keep.json 的 errors 字段")
    print(f"缩略图总览：{output_dir / 'contact_sheet.jpg'}（绿框保留、变暗被合并，不同画面被合并就调低 --threshold）")
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
