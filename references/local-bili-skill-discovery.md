# Bilibili 字幕与音频提取能力

本说明对应 `bili-2-ppt/bili-subtitle-asr` 子技能。该子技能自包含视频、合集分集、opus/article、网页 AI 字幕和本地音频 ASR 所需的调用说明与脚本，运行时不依赖外部技能目录。

## 能力边界

- 输入支持 Bilibili 视频链接、BVID、分集参数，以及 opus/article 链接。
- 视频链路先尝试页面字幕；页面字幕不可用时，可选择网页 AI 字幕或音频 ASR。
- 音频 ASR 支持 `faster-whisper`、`openai-whisper` 和 `Qwen3-ASR`，结果包含带时间戳的 JSON 与纯文本字幕。
- 支持音频分段、逐段识别、时间偏移合并、结果校验和按分集归档。
- opus/article 链路输出正文、图片、代码块和证据 JSONL，供后续知识文档生成使用。
- 归档范围限定为当前任务材料，其他目录不属于该子技能的输入。

## 成品入口

```text
bili-2-ppt/bili-subtitle-asr/
├── SKILL.md
├── scripts/
│   ├── run_bili_note.py
│   ├── extract_bilibili.py
│   ├── extract_bilibili_opus.py
│   ├── fetch_browser_ai_subtitles.py
│   ├── run_qwen_asr.py
│   ├── asr_segments.py
│   ├── process_video_part.py
│   ├── archive_bili_materials.py
│   ├── validate_asr_output.py
│   └── check_environment.py
└── tests/
```

- `run_bili_note.py`：统一入口，按输入类型选择视频或 opus/article 路线。
- `extract_bilibili.py`：视频元数据、分 P、普通字幕、音频获取与本地 ASR。
- `fetch_browser_ai_subtitles.py`：在用户已登录的浏览器环境中获取网页 AI 字幕。
- `asr_segments.py`：分段音频、逐段识别并合并全局时间戳。
- `process_video_part.py`：处理单个分集的提取、ASR、校验和材料归档。
- `archive_bili_materials.py`：将当前任务的字幕、音频、元数据和识别结果整理为可复用材料。
- `validate_asr_output.py`：检查字幕 JSON 的时间轴、文本和分段结构。
- `check_environment.py`：检查 Python、FFmpeg 和可选 ASR 后端是否可用，并列出本机可复用的环境与模型。

## 输出约定

- 视频元数据：标题、UP 主、分集、时长、封面和接口响应摘要。
- 字幕结果：带时间戳的 JSON、纯文本 `.txt`，以及来源和 ASR 后端信息。
- 分集材料：按分集编号组织，文件名保持稳定，便于后续合并合集知识文档。
- opus/article：Markdown 正文、资源清单和证据 JSONL。

## 运行依赖

- 必需：Python 3、网络访问、FFmpeg/FFprobe。
- 可选：`yt-dlp`、`faster-whisper`、`openai-whisper`、`qwen-asr`，以及对应模型权重。
  当前解释器没有时，自动搜索并复用本机已有的 ASR 环境与已下载的模型（见 `scripts/discover_env.py`）。
- 网页 AI 字幕需要用户自己的已登录浏览器会话；不会把浏览器会话写入技能目录。

详细参数、接口字段和失败处理约定见同目录的 `SKILL.md` 与 `references/asr-workflow.md`。
