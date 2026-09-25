#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付物 Markdown 的排版约定检查，供三份交付物的校验脚本调用。

约定本身写在模板里、由合并阶段落实（[normalize_md.py](normalize_md.py) 负责改，
[retint_mermaid.py](retint_mermaid.py) 负责刷配色）；这里负责**在交付前拦住写歪的**——
光写在模板里不算固定，能过校验才算。

三条：

1. 正文结束、下一节开始之前留 3 个空行；分类标题下直接接小标题、条目内的
   分段标题（模版的 `part_headings`）前留 1 个；标题与自己的正文之间不留空行；
2. 条目编号按分类独立编号，每个分类下从 1 重新开始（模版的 `item_pattern`）；
3. mermaid 块的配色与当前主题一致——主题声明行与七个角色的 `classDef` 都按
   [references/mermaid-style.md](../references/mermaid-style.md) 的色板走。
"""

from __future__ import annotations

import re
from pathlib import Path

from md_template_spec import TemplateSpec
from normalize_md import BLANK_AFTER_HEADING, HEADING, _fence_mask, blank_before, iter_items
from retint_mermaid import INIT_LINE, load_palette


def find_blank_line_problems(text: str, parts: frozenset[str] = frozenset()) -> list[str]:
    """正文结束、下一节开始之前要留 3 个空行；标题与正文之间不留空行。

    分类标题下面直接接小标题、条目内的分段标题，前面只该有 1 个——
    它们和上一段属于同一个单元，压 3 行等于把这个单元切碎。
    """
    lines = text.splitlines()
    inside = _fence_mask(lines)
    problems: list[str] = []
    for index, line in enumerate(lines):
        if inside[index] or not HEADING.match(line):
            continue
        before = after = 0
        cursor = index - 1
        while cursor >= 0 and lines[cursor].strip() == "":
            before += 1
            cursor -= 1
        previous = lines[cursor] if cursor >= 0 else None
        cursor = index + 1
        while cursor < len(lines) and lines[cursor].strip() == "":
            after += 1
            cursor += 1
        want_before = blank_before(line, previous, parts)
        # 下一行是标题时由那个标题的规则管，这里只查标题与正文之间
        next_is_heading = cursor < len(lines) and bool(HEADING.match(lines[cursor]))
        after_ok = cursor >= len(lines) or next_is_heading or after == BLANK_AFTER_HEADING
        if not (before == want_before and after_ok):
            problems.append(
                f"「{line.strip()[:24]}」前 {before} 后 {after} 个空行，应为前 {want_before} 后 {BLANK_AFTER_HEADING}"
            )
    return problems


def find_numbering_problems(text: str, spec: TemplateSpec) -> list[str]:
    """条目编号在每个分类下必须从 1 连续递增。"""
    if not spec.item_pattern:
        return []
    lines = text.splitlines()
    problems: list[str] = []
    for i, _, numbered, expected in iter_items(text, spec):
        if int(numbered.group(1)) != expected:
            problems.append(
                f"「{lines[i].strip()[:24]}」编号应为 {expected}，实际 {numbered.group(1)}"
            )
    return problems


def find_mermaid_palette_problems(text: str) -> list[str]:
    """mermaid 块的配色必须与当前主题一致。"""
    declaration, roles = load_palette()
    problems: list[str] = []
    for index, block in enumerate(re.findall(r"```mermaid\n(.*?)```", text, re.S), 1):
        found = INIT_LINE.search(block)
        if not found:
            problems.append(f"第 {index} 个 mermaid 块缺主题声明行")
        elif found.group(0) != declaration:
            problems.append(f"第 {index} 个 mermaid 块的主题声明与当前主题不一致")

        for match in re.finditer(r"classDef\s+([A-Za-z_]\w*)\s+fill:(#[0-9A-Fa-f]{3,8})\s*,\s*"
                                r"stroke:(#[0-9A-Fa-f]{3,8})\s*,\s*color:(#[0-9A-Fa-f]{3,8})", block):
            name = match.group(1)
            actual = (match.group(2), match.group(3), match.group(4))
            if name in roles and actual != roles[name]:
                problems.append(f"第 {index} 个 mermaid 块的 {name} 配色与当前主题不一致")
    return problems


def check_document(path: Path, spec: TemplateSpec) -> list[str]:
    """按模版声明的结构跑排版检查。"""
    text = path.read_text(encoding="utf-8")
    return (
        find_blank_line_problems(text, frozenset(spec.part_headings))
        + find_mermaid_palette_problems(text)
        + find_numbering_problems(text, spec)
    )
