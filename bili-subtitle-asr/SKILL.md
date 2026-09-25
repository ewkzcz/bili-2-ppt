---
name: bili-subtitle-asr
description: Extract Bilibili video, collection-part, and opus/article materials into readable Markdown and timestamped subtitle data, using public subtitles first and local audio ASR as fallback. Use for Bilibili links, BV IDs, subtitle extraction, transcript generation, or audio-only transcription.
---

# Bilibili 字幕与音频 ASR

本技能只生成可核查的 Bilibili 原始材料：视频元数据、分集信息、页面字幕、网页 AI 字幕、音频 ASR、opus/article 正文和证据文件。它不负责关键帧截图、PDF 排版或最终文档。

## 输入与路线

- 视频或合集链接、BVID：按 `P` 分集提取元数据和字幕。
- opus/article/dynamic 链接：提取正文、图片清单、代码块和证据 JSONL，不走音频 ASR。
- 视频文件禁止下载；音频只用于 ASR 临时处理。

视频字幕按以下优先级处理：

1. 探测播放器普通字幕并下载可用轨道；
2. 探测结果存在 `ai-zh` 但没有 `subtitle_url` 时，**优先复用本机已登录 B站 的浏览器会话**
   获取网页 AI 字幕：能连上正开着调试端口的浏览器就连上去，没有就静默起一个后台实例——
   全程不弹到前台抢焦点。本机找不到任何 B站 登录态时，直接走第 3 条，不要为了字幕
   去开一个干净的未登录浏览器；
3. 页面字幕不可用且需要完整转写时，仅获取音频并运行本地 ASR。

## 脚本入口

所有脚本都在本技能的 `scripts/` 目录中，调用时使用相对本文件的目录：

```bash
SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
```

### 统一入口

```bash
$PYTHON "$SKILL_DIR/scripts/run_bili_note.py" \
  "https://www.bilibili.com/video/BVxxxx/" \
  --work-dir ./tmp_bili_extract \
  --archive-dir ./archive/BVxxxx_title \
  --parts all
```

统一入口支持视频、BVID、opus/article 和 dynamic；`--comments` 获取评论，`--no-download-images` 跳过图文图片，`--browser-target` 指定网页 AI 字幕的浏览器目标，`--dry-run` 只输出计划。

### 视频元数据、字幕和音频

```bash
$PYTHON "$SKILL_DIR/scripts/extract_bilibili.py" BVxxxx \
  --out ./tmp_bili_extract \
  --parts all \
  --download-subtitles
```

页面字幕不可用时：

```bash
$PYTHON "$SKILL_DIR/scripts/extract_bilibili.py" BVxxxx \
  --out ./tmp_bili_extract \
  --parts "1,10,38" \
  --download-audio \
  --transcribe \
  --audio-source auto \
  --asr-backend auto \
  --asr-language zh
```

核心参数：

- `--parts key|all|1,10,38`：选择关键分集、全部分集或指定页码；
- `--download-subtitles`：下载普通字幕轨道；
- `--download-audio`：只获取音频并转换为 WAV；
- `--audio-source auto|bilibili-api|yt-dlp`：选择音频来源；
- `--cookies-from-browser chrome|edge`：在用户已授权登录态下供 yt-dlp 使用；
- `--transcribe`：对已获取的 WAV 执行 ASR；
- `--asr-backend auto|qwen3-asr|faster-whisper|funasr|openai-whisper`：选择 ASR 后端；
- `--asr-model`、`--asr-device`、`--asr-compute-type`：模型、设备和计算类型；
- `--asr-language zh`：ASR 语言；
- `--qwen-python`、`--qwen-device-map`、`--qwen-dtype`、`--qwen-chunk-seconds`：Qwen3-ASR 设置；
- `--force`：重跑已有阶段并覆盖对应输出。

### 网页 AI 字幕

当 `subtitle_probe.json` 显示 `ai-zh` 且普通字幕 URL 为空时：

```bash
$PYTHON "$SKILL_DIR/scripts/fetch_browser_ai_subtitles.py" \
  --bvid BVxxxx \
  --out ./tmp_bili_extract \
  --page 1
```

脚本自己找浏览器会话，不需要外部 CDP 代理：默认静默起一个后台实例，带 B站 登录态的配置文件优先；
原浏览器正在运行时把它克隆到缓存目录再用，不打断用户。已经开着调试端口的浏览器可以直接
`--attach-port <端口>` 连上去。

它只用浏览器会话本身，不读取、不打印、不复制 Cookie——由页面自己去请求播放器接口。
输出包括字幕 URL 清单、定时字幕 JSON（`source=browser-ai-subtitle`，含 `segments`）、
TXT、SRT 和 `subtitle_manifest.json`。乱码、主题不符或时间轴异常时，标记为不可用并改走音频 ASR。

### opus/article

```bash
$PYTHON "$SKILL_DIR/scripts/extract_bilibili_opus.py" \
  "https://www.bilibili.com/opus/1194341967364882439" \
  --out ./tmp_bili_opus \
  --comments
```

输出正文 Markdown/TXT/JSONL、图片清单、规范化元数据和图文证据 JSONL；`--no-download-images` 只保存图片 URL 和清单。

### 单分集处理

```bash
$PYTHON "$SKILL_DIR/scripts/process_video_part.py" \
  BVxxxx \
  --part 1 \
  --work-dir ./tmp_bili_part_01 \
  --archive-dir ./archive/BVxxxx/part-01 \
  --download-audio \
  --transcribe \
  --asr-backend faster-whisper
```

该入口把单分集的提取、音频 ASR 和当前任务材料归档串联起来；`validate_asr_output.py` 可独立校验带时间戳 JSON。

### 分段 ASR

`asr_segments.py` 负责把长 WAV 切成单声道 16 kHz 分段，逐段调用同目录的 ASR 实现，并把每段局部时间戳加回切段起点后合并。合并结果必须满足：段序递增、`start < end`、文本非空、重叠在可接受边界内、末段时间接近音频时长。

## 输出契约

视频分集通常包含：

- `metadata.json`：BVID、CID、P、标题和时长；
- `subtitle_probe.json`：字幕探测结果；
- `subtitle_manifest.json`：字幕来源、语言、URL 和本地文件；
- 定时字幕 JSON：`segments[{start,end,text}]`；
- TXT/SRT 阅读副本；
- `audio_manifest.json`、ASR JSON 和失败日志（使用音频路线时）。

每份字幕必须在 `source`/`backend` 中区分页面字幕、网页 AI 字幕和音频 ASR。专有名词听不清、字幕乱码、接口受限或信息只存在于画面时，标记“需人工核对/需视觉证据”，禁止用标题、常识或模型猜测补齐。

## 运行依赖

- 必需：Python 3、网络访问、FFmpeg/FFprobe。
- 可选：`yt-dlp`、`faster-whisper`、`openai-whisper`、`funasr`、`qwen-asr` 及对应模型权重。
- 浏览器 AI 字幕需要本机有带 B站 登录态的浏览器配置文件；
  没有登录态时改走音频 ASR，不为此单独注册或索要账号。

同目录 `references/asr-workflow.md` 提供接口字段、音频回退和时间轴校验细则；同目录 `scripts/check_environment.py` 用于检查运行依赖。
