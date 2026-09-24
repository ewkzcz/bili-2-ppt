#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把文档里 mermaid 块的配色统一到当前主题。

色板只维护在 [references/mermaid-style.md](../references/mermaid-style.md) 一处：
主题声明那行、以及七个角色的 `classDef` 色值都从那份表里读，本脚本只负责把它们
刷到目标文件上。换主题时不用去逐个块里改颜色——那样必然改漏，最后两份交付物
各留一套颜色。

用法：
    python3 retint_mermaid.py 文件或目录 [更多…] [--check]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


STYLE_FILE = Path(__file__).resolve().parent.parent / "references" / "mermaid-style.md"

# mermaid 的主题声明行：%%{init: ...}%%
INIT_LINE = re.compile(r"^%%\{init:.*?\}%%$", re.M)
# classDef 角色名 fill:#xxx,stroke:#xxx,color:#xxx（通用角色与语义角色都能刷）
CLASSDEF_LINE = re.compile(
    r"^(\s*)classDef\s+([A-Za-z_]\w*)\s+fill:#[0-9A-Fa-f]{3,8}\s*,\s*stroke:#[0-9A-Fa-f]{3,8}"
    r"\s*,\s*color:#[0-9A-Fa-f]{3,8}",
    re.M,
)


def load_palette() -> tuple[str, dict[str, tuple[str, str, str]]]:
    """从 mermaid-style.md 读权威色板：主题声明 + 七个角色的三色。"""
    text = STYLE_FILE.read_text(encoding="utf-8")
    declaration = INIT_LINE.search(text)
    if not declaration:
        raise SystemExit(f"{STYLE_FILE} 里找不到主题声明行")

    roles: dict[str, tuple[str, str, str]] = {}
    row = re.compile(
        r"\|\s*`([A-Za-z_]\w*)`\s*\|[^|]*\|\s*`(#[0-9A-Fa-f]{6})`\s*\|\s*`(#[0-9A-Fa-f]{6})`"
        r"\s*\|\s*`(#[0-9A-Fa-f]{6})`\s*\|"
    )
    for match in row.finditer(text):
        roles[match.group(1)] = (match.group(2), match.group(3), match.group(4))
    if not roles:
        raise SystemExit(f"{STYLE_FILE} 里解析不出角色配色表")
    return declaration.group(0), roles


def retint(text: str, declaration: str, roles: dict[str, tuple[str, str, str]]) -> str:
    """把主题声明与角色色值刷成权威色板。"""
    text = INIT_LINE.sub(lambda _: declaration, text)

    def replace_role(match: re.Match) -> str:
        name = match.group(2)
        if name not in roles:
            return match.group(0)
        fill, stroke, color = roles[name]
        return f"{match.group(1)}classDef {name} fill:{fill},stroke:{stroke},color:{color}"

    return CLASSDEF_LINE.sub(replace_role, text)


def targets(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(path.rglob("*.md")) + sorted(path.rglob("*.py")))
        elif path.is_file():
            found.append(path)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="按当前主题刷新文档里 mermaid 块的配色")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--check", action="store_true", help="只报告哪些文件需要刷新")
    args = parser.parse_args()

    declaration, roles = load_palette()
    changed = 0
    for path in targets(args.paths):
        original = path.read_text(encoding="utf-8")
        if "%{init:" not in original:
            continue
        result = retint(original, declaration, roles)
        if result == original:
            continue
        changed += 1
        if args.check:
            print(f"需要刷新：{path}")
            continue
        path.write_text(result, encoding="utf-8")
        print(f"已刷新：{path}")

    if args.check and changed:
        return 1
    print(f"配色已统一（角色 {len(roles)} 个，改动 {changed} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
