# 后台播放器截图契约

## 每张截图的记录

```json
{
  "part": 2,
  "title": "分集标题",
  "cid": 37334486691,
  "requested_time": 707.0,
  "actual_time": 706.8,
  "duration": 1414.0,
  "paused": true,
  "file": "frames/P02/00707.png",
  "source": "background-chrome-player",
  "ocr": false
}
```

`actual_time` 与目标相差过大、播放器仍在 seeking、时长不匹配或截图为空时，记录 `error`，不要把该图当成有效证据。

## 采样时间

将目标点放在 `[0.03D, 0.97D]` 区间内，避免片头黑屏和结尾自动切集。推荐数量：

| 分集时长 | 默认点数 |
| --- | ---: |
| ≤ 3 分钟 | 4 |
| ≤ 10 分钟 | 6 |
| ≤ 20 分钟 | 8 |
| ≤ 30 分钟 | 10 |
| ≤ 40 分钟 | 13 |
| ≤ 60 分钟 | 16 |
| 更长 | 20，按画面密度调整 |

点数不是页数上限。某段代码或流程变化很快时，在该区间加局部点；等距采样必然漏掉变化快的片段，靠 `dedup/resample_hints.json` 那一轮回流来补。

## 去重

代码和参数以 `scripts/dedupe_frames.py --help` 为准，契约层面只需要记住三件事：

- **保留策略**：连续重复画面保留最后一帧。重复画面里最后一帧才是画面定格的完整状态。
- **判定**：灰度缩略图的平均像素差，`--threshold` 以下视为同一画面。相邻差异小但整体在变的片段（缓慢平移、渐显）按累积差异切开，不会被整体吞掉。
- **比对区域**：网页静态装饰会干扰判断时用 `--crop x y w h` 只比对播放器画面。

`dedup/dedup_keep.json` 的字段：

```json
{
  "method": "gray-thumbnail-mean-abs-diff",
  "threshold": 9.0,
  "keep_policy": "last-frame-of-each-run",
  "parts": [
    {"part": 5, "kept": [{"time": 53.0, "file": "frames/P05/00053.png"}], "kept_count": 1, "dropped_count": 4}
  ],
  "kept_total": 1,
  "dropped_total": 4,
  "errors": []
}
```

`dedup/dedup_log.jsonl` 每行一个被折叠的帧：`part/time/file/folded_into/kept_before/kept_after/distance_prev_frame/distance_run_start/reason`。

`dedup/resample_hints.json` 每行一个覆盖不足的区间：`part/from_time/to_time/gap/suggest_time/before_file/after_file`。`suggest_time` 是区间中点，在这附近加点后重新采集、重新去重。

## 中间产物结构

本阶段产出的是中间产物，可以保留 `part/time/file` 溯源；交付物（学习笔记PPTX、知识博客文章 MD、八股模拟面试 MD）则完全不带这些信息。

1. 截图目录：按分集组织，文件名带时间点；
2. `capture_log.jsonl`：每张截图的完整记录，含失败点；
3. `manifest.json`：截图数量、字幕覆盖范围、未验证项；
4. `dedup/`：`dedup_keep.json`、`dedup_log.jsonl`、`resample_hints.json`。

覆盖说明要写清：失败点、文字材料缺失、未验证内容，以及被去重的区间是因为画面确实没变，还是因为没采到。

截图作为视觉证据，不把画面上看不清的字「猜写」进摘要。文档层需要重构图示时，从 `dedup_keep.json` 里的画面出发。
