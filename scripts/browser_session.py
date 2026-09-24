#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复用本机浏览器会话的公共实现，供字幕子技能和画面子技能共用。

两条子技能都需要一个「已经登录 B站、且不抢前台焦点」的浏览器，逻辑完全一样，
所以放在调度器这一层，两个子技能都用它，各自不再重复实现一遍。

会话按下面的优先级选取：

1. 显式指定的调试端口；
2. 本机正在运行、且自己开着调试端口的浏览器——直接连上去，不另开窗口；
3. 带 B站 登录态的配置文件：浏览器没在跑就直接用；正在跑就把它克隆到缓存目录再启动
   （克隆能带上登录 Cookie，且完全不动用户原来的浏览器）；
4. 都没有才退回临时配置文件（未登录，能力和画质都受限，调用方要如实记录）。

启动一律静默：窗口放到屏幕外，并关掉被遮挡时的渲染降频。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path


# 浏览器清单：显示名、配置文件根目录、可执行文件在各平台下的常见位置
BROWSER_SPECS = [
    (
        "Chrome",
        Path.home() / "Library/Application Support/Google/Chrome",
        ["Google Chrome.app"],
    ),
    (
        "Edge",
        Path.home() / "Library/Application Support/Microsoft Edge",
        ["Microsoft Edge.app"],
    ),
    (
        "Brave",
        Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser",
        ["Brave Browser.app"],
    ),
    (
        "Chromium",
        Path.home() / "Library/Application Support/Chromium",
        ["Chromium.app"],
    ),
]

# 可执行文件可能装在 /Applications，也可能在外置卷或用户目录下
APP_SEARCH_DIRS = [
    Path("/Applications"),
    Path.home() / "Applications",
]


def _app_search_dirs() -> list[Path]:
    """把外置卷上的应用程序目录也纳进来。"""
    dirs = list(APP_SEARCH_DIRS)
    volumes = Path("/Volumes")
    if volumes.exists():
        for volume in volumes.iterdir():
            candidate = volume / "Applications"
            if candidate.is_dir():
                dirs.append(candidate)
    return dirs


def find_executable(app_bundles: list[str]) -> str | None:
    """在常见应用程序目录里找浏览器主程序。"""
    for directory in _app_search_dirs():
        for bundle in app_bundles:
            app = directory / bundle
            if not app.exists():
                continue
            macos = app / "Contents/MacOS"
            if not macos.is_dir():
                continue
            for entry in sorted(macos.iterdir()):
                if entry.is_file() and entry.name == app.stem:
                    return str(entry)
            # 主程序名和包名不完全一致时，取可执行位最大的那个
            candidates = [p for p in macos.iterdir() if p.is_file()]
            if candidates:
                return str(max(candidates, key=lambda p: p.stat().st_size))
    return None


def has_bilibili_login(profile_dir: Path) -> bool:
    """配置文件里有没有 B站 登录 Cookie。只看有没有，不读取也不打印其值。"""
    db = profile_dir / "Cookies"
    if not db.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{db}?immutable=1", uri=True, timeout=2)
        try:
            row = conn.execute(
                "select count(*) from cookies where host_key like '%bilibili%' and name='SESSDATA'"
            ).fetchone()
            return bool(row and row[0])
        finally:
            conn.close()
    except Exception:
        return False


def discover_profiles() -> list[dict]:
    """列出本机所有浏览器配置文件，标出可执行文件路径和是否有 B站 登录态。"""
    found = []
    for name, root, bundles in BROWSER_SPECS:
        if not root.exists():
            continue
        executable = find_executable(bundles)
        for profile in [root / "Default"] + sorted(root.glob("Profile *")):
            if not (profile / "Cookies").exists():
                continue
            found.append({
                "browser": name,
                "root": root,
                "profile": profile,
                "executable": executable,
                "logged_in": has_bilibili_login(profile),
            })
    return found


def browser_running(executable: str | None) -> bool:
    """该浏览器是否已经在运行。"""
    if not executable:
        return False
    try:
        out = subprocess.run(
            ["pgrep", "-f", Path(executable).name], capture_output=True, text=True
        )
        return bool(out.stdout.strip())
    except Exception:
        return False


def clone_profile(profile: dict, dest_root: Path) -> Path:
    """把带登录态的配置文件克隆一份出来用，不动用户原来的浏览器。

    Cookie 的解密盐存在 `Local State` 里，密文在 `Default/Cookies` 里，
    两者必须一起复制，登录态才能在新实例里解出来。整个配置文件目录可能有好几 GB，
    这里只取启动和登录必需的几个文件。
    """
    if dest_root.exists():
        shutil.rmtree(dest_root)
    (dest_root / "Default").mkdir(parents=True, exist_ok=True)
    shutil.copy2(profile["root"] / "Local State", dest_root / "Local State")
    for name in ("Cookies", "Preferences", "Secure Preferences"):
        source = profile["profile"] / name
        if source.exists():
            shutil.copy2(source, dest_root / "Default" / name)
    return dest_root


def read_active_port(root: Path) -> int | None:
    """读取浏览器自己写下的调试端口；文件存在就说明它正开着调试端口。"""
    handle = root / "DevToolsActivePort"
    if not handle.exists():
        return None
    try:
        first = handle.read_text(encoding="utf-8").splitlines()[0].strip()
        return int(first) if first.isdigit() else None
    except Exception:
        return None


def port_alive(port: int) -> bool:
    """调试端口是否可用。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2):
            return True
    except Exception:
        return False


def wait_for_debugger(port: int, timeout: float = 30.0) -> None:
    """等调试端口就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_alive(port):
            return
        time.sleep(0.4)
    raise RuntimeError(f"浏览器调试端口 {port} 未在 {timeout:.0f} 秒内就绪")


def launch_silently(executable: str, port: int, profile_dir: Path, window_size: str) -> subprocess.Popen:
    """静默启动浏览器：窗口停在屏幕外，并关闭被遮挡时的渲染降频。"""
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        executable,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        f"--window-size={window_size}",
        # 放到屏幕外，避免弹到前台抢注意力
        "--window-position=-32000,-32000",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        "--disable-infobars",
        "--autoplay-policy=no-user-gesture-required",
        # 窗口被遮挡或不在屏幕上时浏览器会降频甚至停渲染，截出来会是空白
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-features=CalculateNativeWinOcclusion",
        "about:blank",
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _list_targets(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _open_target(port: int) -> None:
    """让浏览器新开一个标签页；刚启动时页面目标可能还没建出来。"""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT"
    )
    with urllib.request.urlopen(request, timeout=5):
        pass


def page_websocket(port: int, timeout: float = 20.0) -> str:
    """取页面目标的 WebSocket 调试地址；没有页面就自己开一个。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            pages = [t for t in _list_targets(port) if t.get("type") == "page"]
        except Exception:
            pages = []
        if pages:
            # 优先挑一个真正的网页，避免连到浏览器内部的空白页
            real = [t for t in pages if not str(t.get("url", "")).startswith("devtools://")]
            return (real or pages)[0]["webSocketDebuggerUrl"]
        try:
            _open_target(port)
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("浏览器里没有可用的页面目标")


@dataclass
class Session:
    """一个可用的浏览器会话。"""

    ws_url: str
    port: int
    note: str
    logged_in: bool
    started_by_us: bool
    pid: int | None = None

    def stop(self) -> None:
        """只关本次自己拉起来的实例；复用别人的浏览器时一个都不动。"""
        if not self.started_by_us:
            return
        try:
            # 模式串本身以 -- 开头，不加 -- 分隔符会被 pkill 当成自己的长选项
            subprocess.run(
                ["pkill", "-f", "--", f"--remote-debugging-port={self.port}"],
                capture_output=True,
            )
        except Exception:
            pass


def resolve_session(
    *,
    attach_port: int | None = None,
    prefer_login: bool = True,
    port: int = 9333,
    profile_dir: Path | None = None,
    clone_dir: Path | None = None,
    window_size: str = "1920,1080",
    executable: str | None = None,
    quiet: bool = False,
) -> Session:
    """按优先级选一个浏览器会话。

    prefer_login 为真时，会优先使用带 B站 登录态的配置文件；正在运行的浏览器
    无法直接复用其配置文件，这时把它克隆到 clone_dir 再启动，不打断用户。
    """
    def log(message: str) -> None:
        if not quiet:
            print(message, file=sys.stderr)

    # 一、显式指定的端口
    if attach_port:
        if not port_alive(attach_port):
            raise RuntimeError(f"指定的调试端口 {attach_port} 没有响应")
        return Session(page_websocket(attach_port), attach_port,
                       f"复用调试端口 {attach_port}", True, False)

    profiles = discover_profiles()

    # 二、本机已有开着调试端口的浏览器，直接连上去，不另开窗口
    for item in profiles:
        active = read_active_port(item["root"])
        if active and port_alive(active):
            note = f"复用正在运行的{item['browser']}（{'已' if item['logged_in'] else '未'}登录 B站）"
            return Session(page_websocket(active), active, note, item["logged_in"], False)

    logged = [p for p in profiles if p["logged_in"]] if prefer_login else []

    # 三、有登录态可用
    if logged:
        chosen = logged[0]
        if not browser_running(chosen["executable"]):
            launch_silently(chosen["executable"], port, chosen["profile"], window_size)
            wait_for_debugger(port)
            return Session(page_websocket(port), port,
                           f"使用已登录 B站 的{chosen['browser']}配置文件", True, True)
        if chosen["executable"] and clone_dir:
            # 浏览器正在运行，配置文件被锁住；克隆一份出来用，不打断用户
            clone_profile(chosen, clone_dir)
            launch_silently(chosen["executable"], port, clone_dir, window_size)
            wait_for_debugger(port)
            return Session(page_websocket(port), port,
                           f"克隆{chosen['browser']}的已登录配置文件（未打断原浏览器）", True, True)
        log(f"提示：{chosen['browser']} 有登录态但正在运行，且未提供克隆目录；改用临时配置文件")

    # 四、退回临时配置文件
    target_exe = executable or next(
        (p["executable"] for p in profiles if p["executable"]),
        None,
    ) or next((find_executable(bundles) for _, _, bundles in BROWSER_SPECS
               if find_executable(bundles)), None)
    if not target_exe:
        raise RuntimeError("未找到可用的浏览器可执行文件，请用 --browser 指定")

    temp_profile = profile_dir or (Path.home() / ".cache" / "bili-browser-profile")
    if not port_alive(port):
        launch_silently(target_exe, port, temp_profile, window_size)
        wait_for_debugger(port)
        return Session(page_websocket(port), port,
                       "使用临时配置文件（本机未找到可用的 B站 登录态）", False, True)
    return Session(page_websocket(port), port, "复用临时配置文件上已开的调试端口", False, False)
