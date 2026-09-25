# Bilibili 字幕与 ASR 接口、脚本参考

## 子技能脚本

- `scripts/extract_bilibili.py`：视频元数据、分集、播放器字幕、音频获取和本地 ASR；
- `scripts/extract_bilibili_opus.py`：opus/article 正文、图片、代码块和证据 JSONL；
- `scripts/run_bili_note.py`：视频与图文统一入口、运行计划和归档；
- `scripts/fetch_browser_ai_subtitles.py`：复用本机已登录浏览器取网页 AI 字幕（自包含，
  不需要外部 CDP 代理；静默后台，浏览器正在运行就克隆配置文件）；
- `scripts/process_video_part.py`：单分集提取、ASR 和材料归档；
- `scripts/asr_segments.py`：WAV 分段、逐段识别和全局时间戳合并；
- `scripts/run_qwen_asr.py`：Qwen3-ASR 单音频调用适配器；
- `scripts/archive_bili_materials.py`：当前提取目录的材料归档；
- `scripts/validate_asr_output.py`：定时字幕 JSON 校验；
- `scripts/check_environment.py`：Python、FFmpeg 和可选 ASR 后端检查。

## 接口探测顺序

1. 通过元数据得到 BVID、CID、P 和时长；
2. 读取播放器字幕探测结果，区分普通字幕和 `ai-zh`；
3. 普通字幕 URL 可用时下载原始字幕 JSON；
4. `ai-zh` URL 为空时，通过本机已登录 B站 的浏览器会话取得页面实际请求的 AI 字幕 URL：
   运行 `scripts/fetch_browser_ai_subtitles.py --bvid <BVID> --out $WORK/subtitle`（`$WORK` 在系统临时目录下，见调度器的「目录约定」），
   由它自己选会话（复用 > 克隆已登录配置文件 > 临时配置文件）；没有登录态可复用时
   跳过这一步，直接进第 5 步；
5. 两种字幕都不可用时，执行音频-only 下载和本地 ASR——这是兜底路线，
   缺 ASR 后端或模型时需要先补齐，不要因为要下载就跳过转写。

接口字段可能随登录态和站点版本变化。最终以脚本解析结果和 `subtitle_probe.json` 为准，不凭空补齐字段。

## 音频-only 回退

`extract_bilibili.py` 支持 `--audio-source auto|bilibili-api|yt-dlp`。音频结果必须同时检查：

- `audio_manifest.json` 中的文件路径和来源；
- 音频文件大小、WAV 采样率和声道数；
- 实际时长与元数据时长是否接近；
- 下载日志是否出现截断、鉴权或转码错误。

返回成功但时长明显不足的音轨不能作为完整分集结果。

## 分段 ASR 合并

切段时使用局部时间戳，最终时间必须加上切段起点：

```text
整集 start = 分段起点 + 分段 start
整集 end   = 分段起点 + 分段 end
```

合并前后检查：

- 段序按时间递增；
- 每段 `start < end`；
- 文本非空且没有整段重复；
- 重叠仅保留可接受的 ASR 边界误差；
- 末段时间接近音频或元数据时长；
- `source`、`backend`、模型和失败原因可追溯。

## 质量标记

- 页面字幕：`source=page-subtitle`；
- 网页 AI 字幕：`source=browser-ai-subtitle`；
- 音频 ASR：`source=audio-asr`；
- opus/article：`source=opus-article`。

画面中存在而字幕和音频无法表达的信息，输出“需视觉证据”，交由画面子技能 `bili-keyframes` 处理。
