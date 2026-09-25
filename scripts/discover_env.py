#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""搜索本机已有的 Python 环境、ASR 后端、已下载的模型和 ffmpeg，找到就复用。

调用方先走固定路径（显式参数、BILI_PYTHON / BILI_ASR_PYTHON 环境变量、当前解释器、PATH 上的 ffmpeg），
固定路径不满足时才调这里兜底。全程自动，不向用户提问。

不写死任何机器上的安装路径，全部向工具本身查询（macOS / Linux / Windows 通用）：

- Python 环境：PATH 上的 python、conda 登记表与 `conda env list`、`pyenv root`、`$WORKON_HOME`、
  `pipx environment`、`uv tool dir` 与 `uv python list`、Windows 的 `py -0p`，以及当前目录上一级三层、
  用户目录五层以内带 pyvenv.cfg 的虚拟环境（跳过 node_modules、.git 等）；
- 已下载的模型：HuggingFace（HF_HUB_CACHE / HF_HOME）、ModelScope（MODELSCOPE_CACHE）、
  whisper / faster-whisper 各库默认的缓存目录（遵循 XDG_CACHE_HOME）；
- ffmpeg：PATH、`brew --prefix`，以及已有环境里 imageio-ffmpeg 自带的二进制。

结果缓存在 <系统临时目录>/bili-2-ppt/env.json，24 小时内复用，`--refresh` 强制重扫。

用法：
    python3 discover_env.py              # 打印摘要
    python3 discover_env.py --json       # 打印完整结果
    python3 discover_env.py --need tools # 只打印可用于建 PPT / 截图的 Python 路径
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


CACHE_PATH = Path(tempfile.gettempdir()) / "bili-2-ppt" / "env.json"
CACHE_TTL_SECONDS = 24 * 3600
PROBE_TIMEOUT_SECONDS = 90

IS_WINDOWS = os.name == "nt"
HOME = Path.home()

# 探测的模块：四种 ASR 后端、torch，以及建 PPT / 截图要用的库
PROBE_MODULES = ["qwen_asr", "faster_whisper", "whisper", "funasr", "torch", "pptx", "PIL", "lxml", "websocket",
                 "certifi"]
BACKEND_MODULE = {
    "qwen3-asr": "qwen_asr",
    "faster-whisper": "faster_whisper",
    "openai-whisper": "whisper",
    "funasr": "funasr",
}
# 中文与其他语言的后端优先级，与 extract_bilibili.resolve_asr_backend 的固定路径顺序一致
RANK_ZH = ["qwen3-asr", "funasr", "faster-whisper", "openai-whisper"]
RANK_OTHER = ["faster-whisper", "openai-whisper", "funasr", "qwen3-asr"]
DEVICE_RANK = {"cuda": 0, "mps": 1, "cpu": 2}
# 按用途声明依赖：(要能导入的模块, 对应的 pip 包)。tools 是两者合起来，一个环境同时能截图和建 PPT
NEEDS = {
    "capture": (("PIL", "websocket", "certifi"), ("Pillow", "websocket-client", "certifi")),
    "pptx": (("pptx", "PIL", "lxml"), ("python-pptx", "Pillow", "lxml")),
}
NEEDS["tools"] = (tuple(dict.fromkeys(NEEDS["capture"][0] + NEEDS["pptx"][0])),
                  tuple(dict.fromkeys(NEEDS["capture"][1] + NEEDS["pptx"][1])))

PROBE_CODE = r"""
import importlib.util, json, sys
mods = %s
found = {m: importlib.util.find_spec(m) is not None for m in mods}
device = "cpu"
if found.get("torch"):
    try:
        import torch
        if torch.cuda.is_available():
            device = "cuda"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            device = "mps"
    except Exception:
        pass
print(json.dumps({"version": sys.version.split()[0], "prefix": sys.prefix, "modules": found, "device": device}))
""" % json.dumps(PROBE_MODULES)


# ── Python 环境 ─────────────────────────────────────────────────────────

def _bin(env: Path) -> list[Path]:
    """一个环境根目录下的解释器位置。"""
    if IS_WINDOWS:
        return [env / "python.exe", env / "Scripts" / "python.exe"]
    return [env / "bin" / "python3", env / "bin" / "python"]


def _run(cmd: list[str]) -> str:
    """跑一个查询命令，失败返回空串。"""
    exe = shutil.which(cmd[0])
    if not exe:
        return ""
    try:
        return subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""


# 遍历时不进入的目录：依赖包、版本库、系统缓存这类体积大又不会放虚拟环境的地方
SKIP_DIRS = {"node_modules", ".git", ".hg", ".svn", "__pycache__", "site-packages", "Library", ".Trash",
             "archive-v0", "wheels", "downloads"}


def _venvs_under(root: Path, depth: int) -> list[Path]:
    """root 下 depth 层以内的虚拟环境：以 pyvenv.cfg 识别，不按目录名猜；找到一个就不再往里钻。"""
    found: list[Path] = []
    stack = [(root, 0)]
    while stack:
        current, level = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        if any(e.name == "pyvenv.cfg" for e in entries):
            found.append(current)
            continue
        if level >= depth:
            continue
        for entry in entries:
            if entry.is_dir(follow_symlinks=False) and entry.name not in SKIP_DIRS:
                stack.append((Path(entry.path), level + 1))
    return found


def _env_roots() -> list[Path]:
    """各环境管理工具自己登记的环境根目录，全部向工具查询，不写死安装位置。"""
    roots: list[Path] = []
    # conda / mamba：所有创建过的环境都登记在 ~/.conda/environments.txt，conda 不在 PATH 上也能读到
    registry = HOME / ".conda" / "environments.txt"
    if registry.is_file():
        roots += [Path(line.strip()) for line in registry.read_text(encoding="utf-8", errors="ignore").splitlines()
                  if line.strip()]
    for tool in ("conda", "mamba", "micromamba"):
        out = _run([tool, "env", "list", "--json"])
        if out:
            try:
                roots += [Path(p) for p in json.loads(out).get("envs", [])]
            except ValueError:
                pass
    # pyenv：安装根目录向 pyenv 查询
    pyenv_root = os.environ.get("PYENV_ROOT") or _run(["pyenv", "root"]).strip()
    if pyenv_root:
        roots += list((Path(pyenv_root) / "versions").glob("*"))
    # virtualenvwrapper
    if os.environ.get("WORKON_HOME"):
        roots += list(Path(os.environ["WORKON_HOME"]).glob("*"))
    # pipx 与 uv 的工具环境
    pipx_venvs = _run(["pipx", "environment", "--value", "PIPX_LOCAL_VENVS"]).strip()
    if pipx_venvs:
        roots += list(Path(pipx_venvs).glob("*"))
    uv_tools = _run(["uv", "tool", "dir"]).strip()
    if uv_tools:
        roots += list(Path(uv_tools).glob("*"))
    # 项目虚拟环境：当前目录及其上一级（兄弟项目）三层以内、用户目录五层以内
    cwd = Path.cwd()
    roots += _venvs_under(cwd.parent, 3) + _venvs_under(HOME, 5)
    return roots


def _extra_interpreters() -> list[Path]:
    """直接给出解释器路径的来源：uv 管理的 Python、Windows 的 py 启动器。"""
    found: list[Path] = []
    for line in _run(["uv", "python", "list", "--only-installed"]).splitlines():
        parts = line.split()
        if parts and Path(parts[-1]).is_file():
            found.append(Path(parts[-1]))
    if IS_WINDOWS:
        for line in _run(["py", "-0p"]).splitlines():
            if line.strip().lower().endswith("python.exe"):
                found.append(Path(line.split()[-1]))
    return found


def candidate_pythons() -> list[str]:
    """所有候选解释器。按路径本身去重，不解析软链接——虚拟环境的 python 是指向底层解释器的软链接，
    解析后会和底层解释器合并，虚拟环境就丢了；同一环境的多个入口在探测后按 sys.prefix 合并。"""
    found: list[Path] = [Path(sys.executable)]
    for name in ("python3", "python"):
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            path = Path(directory) / (f"{name}.exe" if IS_WINDOWS else name)
            if path.is_file():
                found.append(path)
    found += _extra_interpreters()
    for root in _env_roots():
        found += [p for p in _bin(root) if p.is_file()]
    return list(dict.fromkeys(os.path.abspath(str(path)) for path in found))


def probe(python: str) -> dict | None:
    try:
        out = subprocess.run([python, "-c", PROBE_CODE], capture_output=True, text=True,
                             timeout=PROBE_TIMEOUT_SECONDS)
        data = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:
        return None
    data["python"] = python
    return data


# ── 已下载的模型 ────────────────────────────────────────────────────────

# 各库的默认缓存根：遵循 XDG_CACHE_HOME，未设置时是 ~/.cache（这是库本身的约定，不是某台机器的路径）
CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME") or HOME / ".cache")


def _hf_hub_dirs() -> list[Path]:
    dirs = []
    if os.environ.get("HF_HUB_CACHE"):
        dirs.append(Path(os.environ["HF_HUB_CACHE"]))
    if os.environ.get("HF_HOME"):
        dirs.append(Path(os.environ["HF_HOME"]) / "hub")
    dirs.append(CACHE_HOME / "huggingface" / "hub")
    dirs.append(CACHE_HOME / "faster-whisper")
    return [d for d in dirs if d.is_dir()]


def _backend_of(repo: str) -> str | None:
    """按仓库名判断模型属于哪个后端；VAD（语音端点检测）单独归为 vad，给 funasr 切分时间戳用。"""
    name = repo.lower()
    if "fsmn_vad" in name or "fsmn-vad" in name:
        return "vad"
    if "qwen3-asr" in name:
        return "qwen3-asr"
    if "faster-whisper" in name or "faster_whisper" in name:
        return "faster-whisper"
    if "sensevoice" in name or "paraformer" in name or "funasr" in name:
        return "funasr"
    return None


WEIGHT_PATTERNS = ("*.safetensors", "*.bin", "*.pt", "*.pth", "*.onnx", "*.ckpt")


def _complete(path: Path) -> bool:
    """模型目录里必须有权重文件；只有配置、没有权重的是下载到一半的，不算。
    HuggingFace 快照里的文件是指向 blobs 的软链接，下载未完成时链接目标不存在，也不算。"""
    # 下载工具的进度文件还在，说明没下完
    if any(path.glob("*.aria2")) or any(path.glob("*.incomplete")) or any(path.glob("*.part")):
        return False
    for pattern in WEIGHT_PATTERNS:
        for weight in path.glob(pattern):
            if weight.exists() and weight.stat().st_size > 0:
                return True
    return False


def _weight_dirs(root: Path, depth: int) -> list[Path]:
    """root 下 depth 层以内、带完整权重的目录；找到就不再往里钻。"""
    found: list[Path] = []
    stack = [(root, 0)]
    while stack:
        current, level = stack.pop()
        if not current.is_dir():
            continue
        if level > 0 and _complete(current):
            found.append(current)
            continue
        if level < depth:
            try:
                stack += [(Path(e.path), level + 1) for e in os.scandir(current)
                          if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")]
            except OSError:
                pass
    return found


def _repo_name(path: Path, root: Path) -> str:
    parts = path.relative_to(root).parts
    for part in reversed(parts):
        if "--" in part:
            return part.replace("--", "/", 1)
    skip = {"hub", "models", "snapshots", "master"}
    named = [p for p in parts if p not in skip]
    return "/".join(named[-2:]) if len(named) >= 2 else "/".join(named)


def cached_models() -> list[dict]:
    models: list[dict] = []
    # HuggingFace 布局：models--{org}--{name}/snapshots/{hash}/
    for hub in _hf_hub_dirs():
        for repo_dir in hub.glob("models--*"):
            repo = repo_dir.name[len("models--"):].replace("--", "/")
            backend = _backend_of(repo)
            snapshots = sorted((repo_dir / "snapshots").glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
            snapshot = next((s for s in snapshots if _complete(s)), None)
            if backend and snapshot:
                models.append({"backend": backend, "name": repo, "path": str(snapshot)})
    # ModelScope：新旧版本布局不一（hub/{org}/{name}、hub/models/{org}/{name}、models/{org}--{name}/snapshots/master），
    # 递归找带完整权重的目录，仓库名取路径里最近的 {org}--{name} 或 {org}/{name}
    ms_roots = [Path(os.environ["MODELSCOPE_CACHE"])] if os.environ.get("MODELSCOPE_CACHE") else []
    ms_roots.append(CACHE_HOME / "modelscope")
    seen: set[str] = set()
    for root in ms_roots:
        for weight_dir in _weight_dirs(root, 5):
            repo = _repo_name(weight_dir, root)
            backend = _backend_of(repo)
            if backend and str(weight_dir) not in seen:
                seen.add(str(weight_dir))
                models.append({"backend": backend, "name": repo, "path": str(weight_dir)})
    # openai-whisper：~/.cache/whisper/{name}.pt
    for pt in (CACHE_HOME / "whisper").glob("*.pt"):
        models.append({"backend": "openai-whisper", "name": pt.stem, "path": str(pt)})
    return models


def _model_size_rank(model: dict) -> int:
    """同一后端有多个模型时优先用大的：large > medium > small > base > tiny，数字参数量大的优先。"""
    name = model["name"].lower()
    for rank, key in enumerate(("large", "1.7b", "medium", "small", "0.6b", "base", "tiny")):
        if key in name:
            return rank
    return 99


# ── ffmpeg ─────────────────────────────────────────────────────────────

def find_ffmpeg(pythons: list[dict] | None = None) -> str | None:
    """PATH 上有就用；没有再问 Homebrew 的安装前缀，最后用已有环境里 imageio-ffmpeg 自带的二进制。"""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    prefix = _run(["brew", "--prefix"]).strip()
    if prefix and (Path(prefix) / "bin" / "ffmpeg").is_file():
        return str(Path(prefix) / "bin" / "ffmpeg")
    for env in pythons or []:
        try:
            out = subprocess.run([env["python"], "-c", "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"],
                                 capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception:
            continue
        if out and Path(out).is_file():
            return out
    return None


# ── 汇总与选择 ──────────────────────────────────────────────────────────

def discover(refresh: bool = False) -> dict:
    """扫描并缓存；缓存未过期且其中的解释器都还在时直接复用。"""
    if not refresh and CACHE_PATH.is_file():
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            fresh = time.time() - data.get("scanned_at", 0) < CACHE_TTL_SECONDS
            if fresh and all(Path(env["python"]).exists() for env in data.get("pythons", [])):
                return data
        except Exception:
            pass
    with ThreadPoolExecutor(max_workers=8) as pool:
        probed = [env for env in pool.map(probe, candidate_pythons()) if env]
    pythons = list({env["prefix"]: env for env in reversed(probed)}.values())
    data = {
        "scanned_at": time.time(),
        "pythons": pythons,
        "models": cached_models(),
        "ffmpeg": find_ffmpeg(pythons),
    }
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return data


# 各后端对应的 pip 包：本地已有完整模型、环境缺这个库时自动补装
BACKEND_PACKAGE = {"qwen3-asr": "qwen-asr", "faster-whisper": "faster-whisper",
                   "openai-whisper": "openai-whisper", "funasr": "funasr"}


def asr_candidates(language_is_chinese: bool, backend: str | None = None,
                   fixed_pythons: list[str] | None = None, refresh: bool = False) -> list[dict]:
    """所有能跑的 ASR 组合，按「有什么用什么」排序：

    1. 本地模型完整、环境已装好后端库；
    2. 本地模型完整、有带 torch 的环境但缺后端库（install 字段给出要补装的包）；
    3. 环境已装好后端库、模型需要下载（download 为 True）。

    同一档里固定路径的解释器（显式指定 / 环境变量 / 当前解释器）排前面，再按后端优先级、GPU 优先。
    每项是 {backend, python, device, model, install, download, vad}。
    """
    data = discover(refresh)
    order = [backend] if backend else (RANK_ZH if language_is_chinese else RANK_OTHER)
    fixed = [os.path.abspath(p) for p in (fixed_pythons or []) if p]
    vad = next((m["path"] for m in data["models"] if m["backend"] == "vad"), None)
    rows = []
    for rank, name in enumerate(order):
        module = BACKEND_MODULE[name]
        models = sorted((m for m in data["models"] if m["backend"] == name), key=_model_size_rank)
        model = models[0]["path"] if models else None
        for env in data["pythons"]:
            has_module = bool(env["modules"].get(module))
            if has_module and model:
                tier, install, download = 0, None, False
            elif model and env["modules"].get("torch"):
                tier, install, download = 1, BACKEND_PACKAGE[name], False
            elif has_module:
                tier, install, download = 2, None, True
            else:
                continue
            is_fixed = os.path.abspath(env["python"]) in fixed
            rows.append(((tier, not is_fixed, rank, DEVICE_RANK.get(env.get("device", "cpu"), 9)),
                         {"backend": name, "python": env["python"], "device": env.get("device", "cpu"),
                          "model": model, "install": install, "download": download,
                          "vad": vad if name == "funasr" else None}))
    rows.sort(key=lambda r: r[0])
    # 同一后端 + 同一解释器只留排名最高的一条
    seen, result = set(), []
    for _, row in rows:
        key = (row["backend"], row["python"])
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def best_asr(language_is_chinese: bool, backend: str | None = None, refresh: bool = False) -> dict | None:
    """排名第一的 ASR 组合，没有则为 None。"""
    rows = asr_candidates(language_is_chinese, backend, [sys.executable], refresh)
    return rows[0] if rows else None


# ── 证书与镜像下载 ──────────────────────────────────────────────────────

def ca_bundle() -> str | None:
    """HTTPS 用的根证书：SSL_CERT_FILE → 任一已有环境里的 certifi → 系统证书。
    Python 与 aria2c 找不到根证书时会报 SSL 握手失败，下载前统一指给它们。"""
    if os.environ.get("SSL_CERT_FILE") and Path(os.environ["SSL_CERT_FILE"]).is_file():
        return os.environ["SSL_CERT_FILE"]
    for python in [sys.executable] + [env["python"] for env in discover()["pythons"]]:
        try:
            out = subprocess.run([python, "-c", "import certifi; print(certifi.where())"],
                                 capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:
            continue
        if out and Path(out).is_file():
            return out
    for path in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if Path(path).is_file():
            return path
    return None


# ── 模型下载：镜像、多连接、断点续传、卡住自动续传、校验 ─────────────────────
#
# 这套策略来自一次真实下载的教训（完整说明见 references/env-setup.md）：
# - 国内直连 HuggingFace 单连接只有几百 KB/s，1.9GB 要一两个小时；ModelScope / hf-mirror 与多连接能快一到两个数量级；
# - Python 与 aria2c 报 SSL 握手失败，几乎都是找不到根证书，指定 certifi 的证书即可；
# - 多连接下载会先把文件撑到完整大小（稀疏文件），文件大小、du 都不能当进度，只认 aria2c 自己报的进度；
# - 尾段常有个别连接卡死，整体进度停住；要设最低速度自动断开重连，并在进度长时间不动时杀掉续传。

# 每个后端要下载的模型，以及能下到它的镜像
DOWNLOAD_REPOS = {
    "qwen3-asr": ("Qwen/Qwen3-ASR-0.6B", ("modelscope", "hf-mirror", "huggingface")),
    "funasr": ("iic/SenseVoiceSmall", ("modelscope",)),
    "vad": ("iic/speech_fsmn_vad_zh-cn-16k-common-pytorch", ("modelscope",)),
    "faster-whisper": ("Systran/faster-whisper-small", ("hf-mirror", "huggingface")),
}
MIRROR_BASE = {
    "modelscope": "https://modelscope.cn",
    "hf-mirror": os.environ.get("HF_ENDPOINT", "https://hf-mirror.com"),
    "huggingface": "https://huggingface.co",
}
# 进度这么久没有增长就判定卡住，杀掉 aria2c 断点续传
STALL_SECONDS = 120
# 单个文件最多续传几轮
MAX_RESUME_ROUNDS = 8
# pip 默认源装不上时依次换这些镜像
PIP_MIRRORS = ("https://pypi.tuna.tsinghua.edu.cn/simple", "https://mirrors.aliyun.com/pypi/simple")


def _mirror_order(mirrors: tuple[str, ...]) -> list[str]:
    """镜像顺序可用 BILI_MODEL_MIRRORS 覆盖，如 `hf-mirror,modelscope`（海外机器可把 huggingface 提前）。"""
    custom = [m.strip() for m in os.environ.get("BILI_MODEL_MIRRORS", "").split(",") if m.strip() in MIRROR_BASE]
    return [m for m in custom if m in mirrors] + [m for m in mirrors if m not in custom] if custom else list(mirrors)


def _get_json(url: str):
    import ssl
    import urllib.request
    context = ssl.create_default_context(cafile=ca_bundle())
    with urllib.request.urlopen(url, timeout=30, context=context) as response:
        return json.loads(response.read())


def _list_files(mirror: str, repo: str) -> list[dict]:
    """文件清单：[{name, size, sha256}]，sha256 拿不到时为 None。"""
    base = MIRROR_BASE[mirror]
    if mirror == "modelscope":
        data = _get_json(f"{base}/api/v1/models/{repo}/repo/files?Recursive=true")
        return [{"name": f["Path"], "size": f.get("Size"), "sha256": f.get("Sha256") or None}
                for f in data["Data"]["Files"] if f.get("Type") != "tree"]
    data = _get_json(f"{base}/api/models/{repo}/tree/main?recursive=true")
    return [{"name": f["path"], "size": f.get("size"), "sha256": (f.get("lfs") or {}).get("oid")}
            for f in data if f.get("type") == "file"]


def _file_url(mirror: str, repo: str, name: str) -> str:
    base = MIRROR_BASE[mirror]
    if mirror == "modelscope":
        return f"{base}/models/{repo}/resolve/master/{name}"
    return f"{base}/{repo}/resolve/main/{name}"


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_ok(path: Path, meta: dict) -> bool:
    """文件已下完整：没有 aria2 进度文件、大小对得上、有 sha256 时哈希也对得上。"""
    if not path.is_file() or path.with_name(path.name + ".aria2").exists():
        return False
    if meta.get("size") is not None and path.stat().st_size != meta["size"]:
        return False
    return not meta.get("sha256") or _sha256(path) == meta["sha256"]


def _aria2(urls: list[str], dest: Path, ca: str | None) -> bool:
    """aria2c 多连接 + 多镜像同时下载同一个文件；进度停住超过 STALL_SECONDS 就杀掉续传。"""
    import re
    import threading
    for round_no in range(1, MAX_RESUME_ROUNDS + 1):
        cmd = ["aria2c", "-c", "-x", "16", "-s", "16", "-k", "1M", "--file-allocation=none",
               "--lowest-speed-limit=50K", "--max-tries=0", "--retry-wait=2", "--timeout=30",
               "--connect-timeout=15", "--summary-interval=15", "--console-log-level=warn",
               "-d", str(dest.parent), "-o", dest.name, *urls]
        if ca:
            cmd.insert(1, f"--ca-certificate={ca}")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        state = {"done": "", "changed": time.time()}

        def watch() -> None:
            for line in proc.stdout:  # type: ignore[union-attr]
                match = re.search(r"\[#\w+ ([\d.]+\w+)/([\d.]+\w+)\((\d+)%\).*?DL:([\d.]+\w+)", line)
                if match:
                    if match.group(1) != state["done"]:
                        state["done"], state["changed"] = match.group(1), time.time()
                    print(f"[download] {dest.name} {match.group(1)}/{match.group(2)}（{match.group(3)}%）"
                          f" 速度 {match.group(4)}/s", file=sys.stderr)
        thread = threading.Thread(target=watch, daemon=True)
        thread.start()
        while proc.poll() is None:
            time.sleep(5)
            if time.time() - state["changed"] > STALL_SECONDS:
                print(f"[download] 进度 {STALL_SECONDS} 秒没动，断点续传（第 {round_no} 轮）", file=sys.stderr)
                proc.kill()
                break
        proc.wait()
        if proc.returncode == 0:
            return True
    return False


def _curl(urls: list[str], dest: Path, ca: str | None) -> bool:
    """没有 aria2c 时的兜底：curl 断点续传，速度过低自动重试，逐个镜像试。"""
    for url in urls:
        cmd = ["curl", "-L", "--fail", "-C", "-", "--retry", "10", "--retry-delay", "2",
               "--speed-limit", "51200", "--speed-time", "60", "-o", str(dest), url]
        if ca:
            cmd[1:1] = ["--cacert", ca]
        for _ in range(MAX_RESUME_ROUNDS):
            if subprocess.run(cmd).returncode == 0:
                return True
    return False


def download_model(backend: str) -> str | None:
    """把某个后端的模型下到 ModelScope 缓存目录并返回本地路径；已下完整的直接复用。

    镜像按 _mirror_order 排序，列文件清单用第一个能访问的；下载时把所有镜像的地址一起交给 aria2c，
    它会把连接分摊到各镜像上，某个镜像慢或挂了其余的自动顶上。每个文件下完核对大小与 SHA256。
    """
    if backend not in DOWNLOAD_REPOS:
        return None
    repo, mirrors = DOWNLOAD_REPOS[backend]
    target = CACHE_HOME / "modelscope" / "models" / repo.replace("/", "--")
    # 已下完整的直接复用（ModelScope 有时把权重放在 snapshots/master 这类子目录里）
    existing = ([target] if _complete(target) else []) + _weight_dirs(target, 3)
    if existing:
        return str(existing[0])
    mirrors = _mirror_order(mirrors)
    files, reachable = None, []
    for mirror in mirrors:
        try:
            listed = _list_files(mirror, repo)
        except Exception as error:  # noqa: BLE001 —— 镜像不可用就换下一个
            print(f"[download] {mirror} 不可用：{error}", file=sys.stderr)
            continue
        reachable.append(mirror)
        files = files or listed
    if not files:
        print(f"[download] 所有镜像都列不出 {repo} 的文件", file=sys.stderr)
        return None
    target.mkdir(parents=True, exist_ok=True)
    ca = ca_bundle()
    total = sum(f.get("size") or 0 for f in files)
    print(f"[download] {repo}：{len(files)} 个文件，共 {total / 1024 / 1024:.0f}MB，镜像 {reachable} → {target}",
          file=sys.stderr)
    # 小文件先下，大权重最后下；已完整的跳过
    for meta in sorted(files, key=lambda f: f.get("size") or 0):
        dest = target / meta["name"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        if _file_ok(dest, meta):
            continue
        urls = [_file_url(m, repo, meta["name"]) for m in reachable]
        for attempt in range(2):
            ok = _aria2(urls, dest, ca) if shutil.which("aria2c") else _curl(urls, dest, ca)
            if ok and _file_ok(dest, meta):
                break
            # 校验不过：删掉重下一次
            dest.unlink(missing_ok=True)
            dest.with_name(dest.name + ".aria2").unlink(missing_ok=True)
        else:
            print(f"[download] {meta['name']} 下载或校验失败", file=sys.stderr)
            return None
    discover(refresh=True)
    return str(target) if _complete(target) else None


def pip_install(python: str, packages: list[str]) -> bool:
    """pip 装包：默认源装不上时依次换镜像；统一指定根证书。"""
    env = os.environ.copy()
    ca = ca_bundle()
    if ca:
        env["SSL_CERT_FILE"] = ca
    base = [python, "-m", "pip", "install", "-q", "--timeout", "30", "--retries", "2", *packages]
    for extra in ([],) + tuple(["-i", mirror] for mirror in PIP_MIRRORS):
        if subprocess.run(base + extra, env=env).returncode == 0:
            return True
    return False


def best_tools_python(need: str = "tools", refresh: bool = False) -> str | None:
    """装齐某个用途全部依赖的解释器：固定路径（BILI_PYTHON、当前解释器）优先，再看搜到的已有环境。"""
    modules = NEEDS[need][0]
    envs = discover(refresh)["pythons"]
    fixed = [os.path.abspath(p) for p in (os.environ.get("BILI_PYTHON"), sys.executable) if p]
    envs = sorted(envs, key=lambda e: os.path.abspath(e["python"]) not in fixed)
    for env in envs:
        if all(env["modules"].get(m) for m in modules):
            return env["python"]
    return None


def ensure_tools_python(need: str = "tools") -> str:
    """先复用装齐依赖的已有环境；没有就在各库共用的缓存根下建一个共用 venv，一次把截图和建 PPT 的依赖都装上。
    不往其他项目的环境里装东西，免得改动别人的环境。"""
    found = best_tools_python(need)
    if found:
        return found
    venv = CACHE_HOME / "bili-2-ppt" / "venv"
    python = next((p for p in _bin(venv) if p.is_file()), None)
    if python is None:
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        python = next(p for p in _bin(venv) if p.is_file())
    if not pip_install(str(python), list(NEEDS["tools"][1])):
        raise RuntimeError(f"给 {python} 安装工具依赖失败（默认源与镜像都试过）")
    discover(refresh=True)
    return str(python)


def setup(language_is_chinese: bool = True) -> dict:
    """一键构建依赖环境：搜索已有环境 → 补齐工具依赖 → 保证至少一个 ASR 本地就绪 → 检查 ffmpeg。
    每一步都先复用已有的，缺了才装或下载；重复运行只会补缺，不会重做。"""
    report: dict = {}
    discover(refresh=True)
    report["tools_python"] = ensure_tools_python("tools")

    rows = asr_candidates(language_is_chinese, fixed_pythons=[sys.executable])
    ready = next((r for r in rows if not r["download"]), None)
    if ready is None:
        # 没有本地就绪的 ASR：有装了后端库的环境就给它下模型；一个都没有就建共用 ASR venv 装 qwen-asr
        with_module = next((r for r in rows if r["download"]), None)
        if with_module is None:
            venv = CACHE_HOME / "bili-2-ppt" / "asr-venv"
            python = next((p for p in _bin(venv) if p.is_file()), None)
            if python is None:
                subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
                python = next(p for p in _bin(venv) if p.is_file())
            pip_install(str(python), [BACKEND_PACKAGE["qwen3-asr"]])
            discover(refresh=True)
        backend = (with_module or {}).get("backend", "qwen3-asr")
        download_model(backend)
        if backend == "funasr":
            download_model("vad")
        rows = asr_candidates(language_is_chinese, fixed_pythons=[sys.executable], refresh=True)
        ready = next((r for r in rows if not r["download"]), None)
    elif ready["backend"] == "funasr" and not ready.get("vad"):
        download_model("vad")
    report["asr"] = ready

    ffmpeg = discover()["ffmpeg"]
    if not ffmpeg:
        # 系统包管理器在就顺手装；装不了在报告里说明
        for cmd in (["brew", "install", "ffmpeg"], ["winget", "install", "-e", "--id", "Gyan.FFmpeg"],
                    ["apt-get", "install", "-y", "ffmpeg"]):
            if shutil.which(cmd[0]) and subprocess.run(cmd).returncode == 0:
                break
        ffmpeg = discover(refresh=True)["ffmpeg"]
    report["ffmpeg"] = ffmpeg
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="搜索本机已有的 Python / ASR / 模型 / ffmpeg 并给出可复用的选择")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重新扫描")
    parser.add_argument("--json", action="store_true", help="打印完整扫描结果")
    parser.add_argument("--need", choices=("asr", "tools", "capture", "pptx", "ffmpeg"),
                        help="只打印某一项的选择结果：capture 截图、pptx 建 PPT、tools 两者都要")
    parser.add_argument("--ensure", action="store_true",
                        help="与 --need tools/capture/pptx 同用：找不到就自动建共用 venv 装好依赖，再打印路径")
    parser.add_argument("--language", default="zh", help="ASR 语言，决定后端优先级")
    parser.add_argument("--download", choices=sorted(DOWNLOAD_REPOS), help="用镜像下载某个后端的模型到本地缓存")
    parser.add_argument("--setup", action="store_true",
                        help="一键构建依赖环境：复用已有环境，缺的工具依赖、ASR 模型、ffmpeg 自动补齐")
    args = parser.parse_args()

    chinese = args.language.lower() in {"zh", "zh-cn", "cn", "chinese", "mandarin"}
    if args.setup:
        report = setup(chinese)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("asr") and report.get("tools_python") else 1
    if args.download:
        path = download_model(args.download)
        print(path or "")
        return 0 if path else 1
    if args.need == "asr":
        print(json.dumps(best_asr(chinese, refresh=args.refresh), ensure_ascii=False))
        return 0
    if args.need in NEEDS:
        print(ensure_tools_python(args.need) if args.ensure else (best_tools_python(args.need, args.refresh) or ""))
        return 0
    if args.need == "ffmpeg":
        print(discover(args.refresh)["ffmpeg"] or "")
        return 0

    data = discover(args.refresh)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"扫描到 {len(data['pythons'])} 个 Python 环境，{len(data['models'])} 个已下载的 ASR 模型")
    for env in data["pythons"]:
        mods = [m for m, ok in env["modules"].items() if ok]
        if mods:
            print(f"  {env['python']}（{env['version']}，{env['device']}）：{', '.join(mods)}")
    for model in data["models"]:
        print(f"  模型 [{model['backend']}] {model['name']} → {model['path']}")
    print(f"ffmpeg：{data['ffmpeg'] or '未找到'}")
    print("ASR 候选（按使用顺序，前面的失败自动换下一个）：")
    for row in asr_candidates(chinese, fixed_pythons=[sys.executable])[:6]:
        note = "需补装 " + row["install"] if row["install"] else ("需下载模型" if row["download"] else "本地就绪")
        print(f"  {row['backend']} @ {row['python']}（{row['device']}，{note}）")
    for need, label in (("capture", "截图"), ("pptx", "建 PPT")):
        print(f"{label}用的 Python：{best_tools_python(need) or '未找到（--need ' + need + ' --ensure 会自动建共用 venv）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
