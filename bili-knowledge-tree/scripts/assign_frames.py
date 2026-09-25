#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按字幕时间段把去重后的画面自动分到知识树各节，并给每节出一张缩略图总览。

主代理不再逐张看图分派：先在 notes.plan.json 的每节写上 spans（这一节对应的字幕时间段，
取自纠错稿的 start/end），再跑本脚本：

    python3 assign_frames.py --plan $WORK/plan/notes.plan.json \\
        --keep $WORK/keyframes/dedup/dedup_keep.json

它做三件事：

1. 每张保留画面在屏幕上的区间是 [run_start, time]（去重保留的是一段相同画面的最后一帧），
   和哪一节的时间段重叠最多就归哪一节，写进该节的 frames[]；
2. 区间明显跨两节的、落在所有时间段之外的，列为待复核，写进 frame_assign.json；
3. 每节出一张缩略图总览 frame_sheets/<节 id>.jpg，待复核的画面加红框。

主代理只看每节的总览删掉过渡态、无关画面，待复核的几张再单独打开看。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

THUMB = (320, 180)
COLUMNS = 4
LABEL = 20


def load_frames(keep_path: Path) -> list[dict[str, Any]]:
    data = json.loads(keep_path.read_text(encoding="utf-8"))
    frames = []
    for part in data.get("parts", []):
        for item in part.get("kept", []):
            if item.get("time") is None:
                continue
            end = float(item["time"])
            start = item.get("run_start")
            start = end if start is None else min(float(start), end)
            frames.append({"part": int(part["part"]), "time": end, "run_start": start, "file": item["file"]})
    return sorted(frames, key=lambda f: (f["part"], f["time"]))


def overlap(frame: dict[str, Any], span: dict[str, Any]) -> float:
    """画面区间与字幕时间段的重叠秒数；单点画面落在时间段内记为极小正数。"""
    if int(span.get("part", 1)) != frame["part"]:
        return 0.0
    lo, hi = max(frame["run_start"], float(span["start"])), min(frame["time"], float(span["end"]))
    if hi > lo:
        return hi - lo
    return 1e-6 if float(span["start"]) <= frame["time"] <= float(span["end"]) else 0.0


def assign(sections: list[dict[str, Any]], frames: list[dict[str, Any]], margin: float) -> tuple[dict[str, list], list]:
    by_section: dict[str, list] = {str(s["id"]): [] for s in sections}
    review: list[dict[str, Any]] = []
    for frame in frames:
        scores = []
        for section in sections:
            score = sum(overlap(frame, span) for span in section.get("spans", []))
            if score > 0:
                scores.append((score, str(section["id"])))
        scores.sort(reverse=True)
        if not scores:
            review.append({**frame, "reason": "outside-all-spans", "candidates": []})
            continue
        best = scores[0][1]
        by_section[best].append(frame)
        # 第二名也占了可观的时长，说明这张画面横跨两节，交给主代理判断
        if len(scores) > 1 and scores[1][0] >= margin:
            review.append({**frame, "reason": "spans-two-sections", "assigned": best,
                           "candidates": [{"id": sid, "seconds": round(sec, 1)} for sec, sid in scores[:3]]})
    return by_section, review


def label_of(frame: dict[str, Any]) -> str:
    t = int(frame["time"])
    return f"P{frame['part']} {t // 60:02d}:{t % 60:02d}"


def write_sheet(frames: list[dict[str, Any]], flagged: set[str], path: Path) -> None:
    from PIL import Image, ImageDraw

    rows = (len(frames) + COLUMNS - 1) // COLUMNS
    sheet = Image.new("RGB", (COLUMNS * THUMB[0], max(rows, 1) * (THUMB[1] + LABEL)), (32, 32, 32))
    draw = ImageDraw.Draw(sheet)
    for index, frame in enumerate(frames):
        x, y = (index % COLUMNS) * THUMB[0], (index // COLUMNS) * (THUMB[1] + LABEL)
        try:
            with Image.open(frame["file"]) as image:
                image.draft("RGB", (THUMB[0] * 2, THUMB[1] * 2))
                sheet.paste(image.convert("RGB").resize(THUMB), (x, y))
        except OSError:
            draw.text((x + 8, y + 8), "unreadable", fill=(255, 80, 80))
        if frame["file"] in flagged:
            draw.rectangle([x, y, x + THUMB[0] - 1, y + THUMB[1] - 1], outline=(230, 40, 40), width=4)
        draw.text((x + 6, y + THUMB[1] + 3), f"#{index + 1} {label_of(frame)}", fill=(230, 230, 230))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, quality=85)


def safe_name(value: str) -> str:
    return re.sub(r"[^\w.-]+", "_", value) or "section"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按字幕时间段把画面分到知识树各节，并出每节的缩略图总览")
    parser.add_argument("--plan", type=Path, required=True, help="notes.plan.json，每节带 spans[{part,start,end}]")
    parser.add_argument("--keep", type=Path, required=True, help="画面阶段的 dedup_keep.json")
    parser.add_argument("--out-dir", type=Path, help="总览图与 frame_assign.json 的目录，默认与 --plan 同目录")
    parser.add_argument("--margin", type=float, default=8.0,
                        help="画面在第二个小节里也停留超过这么多秒，就列为待复核（默认 8）")
    parser.add_argument("--no-sheets", action="store_true", help="只分派，不出总览图")
    args = parser.parse_args(argv)

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    sections = plan.get("sections", [])
    missing_spans = [str(s.get("id")) for s in sections if not s.get("spans")]
    if not sections or len(missing_spans) == len(sections):
        raise SystemExit("notes.plan.json 的 sections[] 里没有 spans，先按纠错稿的时间轴给每节写上 spans 再跑")

    frames = load_frames(args.keep)
    by_section, review = assign(sections, frames, args.margin)
    for section in sections:
        section["frames"] = [{"file": f["file"], "part": f["part"], "time": f["time"]}
                             for f in by_section[str(section["id"])]]
    args.plan.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    out_dir = args.out_dir or args.plan.parent
    flagged = {item["file"] for item in review}
    sheets = {}
    if not args.no_sheets:
        for section in sections:
            sid = str(section["id"])
            if by_section[sid]:
                sheet = out_dir / "frame_sheets" / f"{safe_name(sid)}.jpg"
                write_sheet(by_section[sid], flagged, sheet)
                sheets[sid] = str(sheet)
        outside = [item for item in review if item["reason"] == "outside-all-spans"]
        if outside:
            sheet = out_dir / "frame_sheets" / "_outside.jpg"
            write_sheet(outside, flagged, sheet)
            sheets["_outside"] = str(sheet)

    report = {
        "frames_total": len(frames),
        "sections": [{"id": str(s["id"]), "title": s.get("title"), "frames": len(by_section[str(s["id"])]),
                      "sheet": sheets.get(str(s["id"]))} for s in sections],
        "sections_without_spans": missing_spans,
        "review": review,
        "outside_sheet": sheets.get("_outside"),
    }
    (out_dir / "frame_assign.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"分派 {len(frames) - sum(1 for r in review if r['reason'] == 'outside-all-spans')}/{len(frames)} 张画面到 "
          f"{len(sections)} 节，待复核 {len(review)} 张，见 {out_dir / 'frame_assign.json'}")
    if missing_spans:
        print(f"这几节没写 spans，没有分到画面：{', '.join(missing_spans)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
