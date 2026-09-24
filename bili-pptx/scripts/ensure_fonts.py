#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""建 deck 之前检查模版原件用到的字体是否装在本机，缺的尝试用 Homebrew 字体包安装。

字体名从原件 pptx 的文字片段里读出；Homebrew 字体包按「font-字体名小写连字符」命名
（如 Noto Serif SC → font-noto-serif-sc）。装不上的（多为 Windows 自带字体）照原件字体名写进 deck，
由打开它的软件替换显示。

用法：
    .keyframe-venv/bin/python ensure_fonts.py Cryo_Academic
    .keyframe-venv/bin/python ensure_fonts.py PagedDaylight --check   # 只检查，不安装
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from pptx import Presentation
from pptx.oxml.ns import qn

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "references"


def template_fonts(name: str) -> list[str]:
    """模版原件里文字片段用到的全部字体名。"""
    source = TEMPLATES_DIR / f"{name}.pptx"
    found = set()
    for slide in Presentation(str(source)).slides:
        for element in slide._element.iter(qn("a:latin"), qn("a:ea")):
            if element.get("typeface") and not element.get("typeface").startswith("+"):
                found.add(element.get("typeface"))
    return sorted(found)


def installed(family: str) -> bool:
    """fontconfig 能否找到这个字体族。"""
    result = subprocess.run(["fc-list", f":family={family}", "family"], capture_output=True,
                            text=True)
    return bool(result.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="检查并安装模版原件用到的字体")
    parser.add_argument("template", help="references/ 下的模版名（<模版名>.pptx）")
    parser.add_argument("--check", action="store_true", help="只检查，不安装")
    args = parser.parse_args()

    missing = [family for family in template_fonts(args.template) if not installed(family)]
    if not missing:
        print(f"{args.template}：原件字体都已安装")
        return 0
    print(f"{args.template}：本机缺 {', '.join(missing)}")
    brew = shutil.which("brew")
    if args.check or brew is None:
        return 1
    for family in missing:
        cask = "font-" + family.lower().replace(" ", "-")
        result = subprocess.run([brew, "install", "--cask", cask], capture_output=True, text=True)
        print(f"{family}：{'已安装 ' + cask if result.returncode == 0 else '没有对应字体包，由打开软件替换显示'}")
    # 新装的字体要刷新 fontconfig 缓存，LibreOffice 渲染自检才看得到
    subprocess.run(["fc-cache", "-f"], capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
