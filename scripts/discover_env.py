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
PROBE_MODULES = ["qwen_asr", "faster_whisper", "whisper", "funasr", "torch", "pptx", "PIL", "lxml", "websocket"]
BACKEND_MODULE = {
    "qwen3-asr": "qwen_asr",
    "faster-whisper": "faster_whisper",
    "openai-whisper": "whisper",
    "funasr": "funasr",
}
# 中文与其他语言的后端优先级，与 extract_bilibili.resolve_asr_backend 的固定路径顺序一致
RANK_ZH = ["qwen3-asr", "faster-whisper", "openai-whisper", "funasr"]
RANK_OTHER = ["faster-whisper", "openai-whisper", "funasr", "qwen3-asr"]
DEVICE_RANK = {"cuda": 0, "mps": 1, "cpu": 2}
TOOLS_MODULES = ("pptx", "PIL", "lxml")

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
    name = repo.lower()
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
    for pattern in WEIGHT_PATTERNS:
        for weight in path.glob(pattern):
            if weight.exists() and weight.stat().st_size > 0:
                return True
    return False


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
    # ModelScope 布局：{org}/{name}/ 或 models/{org}/{name}/
    ms_roots = [Path(os.environ["MODELSCOPE_CACHE"])] if os.environ.get("MODELSCOPE_CACHE") else []
    ms_roots += [CACHE_HOME / "modelscope" / "hub", CACHE_HOME / "modelscope" / "hub" / "models"]
    for root in ms_roots:
        for model_dir in root.glob("*/*"):
            repo = f"{model_dir.parent.name}/{model_dir.name}"
            backend = _backend_of(repo)
            if backend and model_dir.is_dir() and _complete(model_dir):
                models.append({"backend": backend, "name": repo, "path": str(model_dir)})
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


def best_asr(language_is_chinese: bool, backend: str | None = None, refresh: bool = False) -> dict | None:
    """选一个能用的 ASR：{backend, python, device, model}。model 是已下载模型的本地路径，没有则为 None。"""
    data = discover(refresh)
    order = [backend] if backend else (RANK_ZH if language_is_chinese else RANK_OTHER)
    for name in order:
        module = BACKEND_MODULE[name]
        envs = [env for env in data["pythons"] if env["modules"].get(module)]
        if not envs:
            continue
        env = min(envs, key=lambda e: DEVICE_RANK.get(e.get("device", "cpu"), 9))
        models = sorted((m for m in data["models"] if m["backend"] == name), key=_model_size_rank)
        return {"backend": name, "python": env["python"], "device": env.get("device", "cpu"),
                "model": models[0]["path"] if models else None}
    return None


def best_tools_python(refresh: bool = False) -> str | None:
    """能建 PPT、处理图片的解释器（python-pptx + Pillow + lxml）。"""
    for env in discover(refresh)["pythons"]:
        if all(env["modules"].get(m) for m in TOOLS_MODULES):
            return env["python"]
    return None


# 建 PPT / 截图要用、本机找不到时自动安装的依赖
TOOLS_PACKAGES = ["python-pptx", "Pillow", "lxml", "websocket-client"]


def ensure_tools_python() -> str:
    """先复用已有环境；都没有就在各库共用的缓存根下建一个共用 venv 装好依赖，之后的运行直接复用它。"""
    found = best_tools_python()
    if found:
        return found
    venv = CACHE_HOME / "bili-2-ppt" / "venv"
    python = next((p for p in _bin(venv) if p.is_file()), None)
    if python is None:
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        python = next(p for p in _bin(venv) if p.is_file())
    subprocess.run([str(python), "-m", "pip", "install", "-q", *TOOLS_PACKAGES], check=True)
    discover(refresh=True)
    return str(python)


def main() -> int:
    parser = argparse.ArgumentParser(description="搜索本机已有的 Python / ASR / 模型 / ffmpeg 并给出可复用的选择")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重新扫描")
    parser.add_argument("--json", action="store_true", help="打印完整扫描结果")
    parser.add_argument("--need", choices=("asr", "tools", "ffmpeg"), help="只打印某一项的选择结果")
    parser.add_argument("--ensure", action="store_true",
                        help="与 --need tools 同用：找不到就自动建共用 venv 装好依赖，再打印路径")
    parser.add_argument("--language", default="zh", help="ASR 语言，决定后端优先级")
    args = parser.parse_args()

    chinese = args.language.lower() in {"zh", "zh-cn", "cn", "chinese", "mandarin"}
    if args.need == "asr":
        print(json.dumps(best_asr(chinese, refresh=args.refresh), ensure_ascii=False))
        return 0
    if args.need == "tools":
        print(ensure_tools_python() if args.ensure else (best_tools_python(args.refresh) or ""))
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
    print(f"ASR 选择：{json.dumps(best_asr(chinese), ensure_ascii=False)}")
    print(f"PPT / 图片工具 Python：{best_tools_python() or '未找到'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
