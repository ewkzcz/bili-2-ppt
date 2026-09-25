# 依赖环境与模型下载

本技能要用到三类依赖：截图与建 PPT 的 Python 库、ASR 后端与模型、ffmpeg。
**一条命令构建**，重复运行只补缺、不重做：

```bash
python3 "$SKILL_ROOT"/scripts/discover_env.py --setup
```

它按「先复用、再补装、最后下载」的顺序做四件事，打印每一项最终用了什么：

1. 搜索本机已有的 Python 环境与已下载的模型（向 conda、pyenv、pipx、uv、`py` 启动器等工具查询，
   外加项目目录里带 `pyvenv.cfg` 的虚拟环境；不写死任何安装路径）；
2. 截图与建 PPT 的依赖：有装齐的环境就用，没有就建一个共用 venv 一次装齐，不往别的项目环境里装；
3. ASR：有本地就绪的组合就用；本地模型完整但缺后端库就补装这一个库；都没有才下载模型
   （本机连一个装了后端库的环境都没有时，建共用 ASR venv 装 `qwen-asr` 再下模型）；
4. ffmpeg：PATH 上没有就用系统包管理器（brew / winget / apt-get）装。

只想看现状不想动任何东西：`python3 "$SKILL_ROOT"/scripts/discover_env.py`。
只下某个模型：`python3 "$SKILL_ROOT"/scripts/discover_env.py --download qwen3-asr`（可选 `funasr`、`vad`、`faster-whisper`）。



## 一、ASR 有什么用什么

转写时按下面的顺序逐个尝试，前一个失败自动换下一个，全程不问用户：

| 顺序 | 条件 | 做法 |
| --- | --- | --- |
| 1 | 本地模型完整、环境里已装后端库 | 直接用，开离线模式 |
| 2 | 本地模型完整、有带 torch 的环境但缺后端库 | 补装这一个库再用 |
| 3 | 环境里有后端库、模型需要下载 | 按下面的下载策略下载 |

同一档里固定路径（`--qwen-python`、`$BILI_ASR_PYTHON`、当前解释器）排前面；中文后端优先级
Qwen3-ASR > SenseVoice（funasr）> faster-whisper > openai-whisper；GPU（CUDA / Apple MPS）优先。

**本地模型完整**的判据：目录里有权重文件（`*.safetensors` / `*.bin` / `*.pt` 等）且非空，
没有 `.aria2`、`.incomplete`、`.part` 这类下载进度文件。只有配置文件、权重是 `.incomplete` 的是下载到一半的残缺副本，
不能用——它会让加载时去联网补下载，看起来像卡死。



## 二、模型下载策略

真实下载中踩过的坑和对应做法，`download_model` 已经全部内置：

| 现象 | 原因 | 做法 |
| --- | --- | --- |
| 1.9GB 模型要下一两个小时 | 国内直连 HuggingFace 单连接只有几百 KB/s | 镜像优先：ModelScope → hf-mirror → HuggingFace；aria2c 16 连接分段下载 |
| Python / aria2c 报 `SSL handshake failure`、`unable to get local issuer certificate` | 找不到根证书 | 指定 certifi 的证书（`SSL_CERT_FILE`、aria2c 的 `--ca-certificate`） |
| 单个镜像时快时慢、偶尔挂掉 | 镜像节点不稳定 | 同一文件的多个镜像地址一起交给 aria2c，连接分摊到各镜像，一个慢了其余顶上 |
| 进度卡在 85%～99% 不动 | 尾段个别连接卡死 | `--lowest-speed-limit=50K` 断开慢连接重连；进度 120 秒没增长就杀掉 aria2c 断点续传 |
| 以为「只剩几十 MB」其实还差几百 MB | 多连接下载先把文件撑到完整大小（稀疏文件），`ls` 大小、`du` 都不是真实进度 | **只认 aria2c 自己报的进度**（`已下/总量(百分比) DL:速度`） |
| 以为 ASR 卡死（CPU 几乎为零） | 本地模型不完整，加载时在后台联网下载 | 模型完整才走本地并开离线模式（`HF_HUB_OFFLINE=1`）；不完整的先用镜像下完 |
| 下完加载报错 | 文件损坏或下错 | 每个文件核对大小和 SHA256（ModelScope 与 HF 的文件清单都带 SHA256），不一致删掉重下 |
| 没装 aria2c | —— | 退回 curl 断点续传（`-C -`、速度过低自动重试），逐个镜像试；能装就先 `brew install aria2` / `winget install aria2.aria2` |
| pip 装包超时 | 默认源慢 | 默认源失败依次换清华、阿里镜像 |

其他要点：

- **下载位置统一放 ModelScope 缓存目录**：`<缓存根>/modelscope/models/<org>--<name>/`（缓存根遵循 `XDG_CACHE_HOME`，
  默认 `~/.cache`）。下完之后所有环境、所有项目都能复用，搜索也能找到；
- **镜像顺序可以改**：`BILI_MODEL_MIRRORS=huggingface,hf-mirror` 这类写法把指定镜像提前（海外机器适用）；
  hf-mirror 的地址跟随 `HF_ENDPOINT`；
- **长下载放后台**：下载期间去做不依赖模型的准备（读后续技能、读 PPT 模版、写样式表），
  也可以先用本地已有的其他后端开始转写，不必干等；
- **汇报进度要说真实数字**：已下多少 MB / 总共多少 MB / 当前速度 / 预计剩余时间，来源是 aria2c 的输出。



## 三、手动兜底

脚本不可用时，照同样的思路手动下载（以 Qwen3-ASR 为例）：

```bash
CA="$(python3 -c 'import certifi; print(certifi.where())' 2>/dev/null || echo /etc/ssl/cert.pem)"
DEST="${XDG_CACHE_HOME:-$HOME/.cache}/modelscope/models/Qwen--Qwen3-ASR-0.6B"
aria2c --ca-certificate="$CA" -c -x 16 -s 16 -k 1M --file-allocation=none \
  --lowest-speed-limit=50K --max-tries=0 --retry-wait=2 --summary-interval=15 \
  -d "$DEST" -o model.safetensors \
  "https://modelscope.cn/models/Qwen/Qwen3-ASR-0.6B/resolve/master/model.safetensors" \
  "https://hf-mirror.com/Qwen/Qwen3-ASR-0.6B/resolve/main/model.safetensors"
```

其余小文件（配置、词表）同样下到 `$DEST`；文件清单来自
`https://modelscope.cn/api/v1/models/Qwen/Qwen3-ASR-0.6B/repo/files?Recursive=true`，
其中的 `Sha256` 用来核对：`shasum -a 256 "$DEST/model.safetensors"`。
