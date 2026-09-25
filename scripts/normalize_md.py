#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付物 Markdown 的排版规范化：标题空行、条目编号。

两条约定写在这里，不靠人记：

1. **章节之间留 3 个空行，标题与本段正文之间不留空行**。markdown 会把连续空行折叠成
   一个，所以「3 个空行」在渲染上和 1 个没有区别；写 3 个是为了让**源文件**里一眼看出
   章节边界——一个小节结束、下一个小节开始之前是断开的三行，而标题紧贴它自己的正文。
   条目内部固定的分段标题（模版的 `part_headings`，如八股模拟面试每道题的
   「简要回答 / 详细问答 / 相关知识」）是同一个条目的几个部分，不是新章节，
   它们前面只留 1 个空行；标题下面直接接小标题时也只留 1 个。代码围栏内部不动。
2. **条目编号按分类独立编号**。模版的 `item_pattern` 里第一个捕获组是编号，
   每个分类下从 1 重新开始，全篇不再连续编号（不出现「二、Q5」这种跨分类接着数的情况）。

所有 Markdown 模版共用这一份，模版差异只从模版描述文件的 frontmatter 读
（见 [md_template_spec.py](md_template_spec.py)）。

用法：
    python3 normalize_md.py <文件> [--check]
    python3 normalize_md.py <文件> --template <模版目录或其中的描述.md> [--in-place]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from md_template_spec import TemplateSpec, load_spec


HEADING = re.compile(r"^(#{1,6})\s+\S")

# 章节之间留几个空行（写在标题之前）
BLANK_BEFORE_HEADING = 3
# 同一单元内部的分段标题前、以及标题下直接接小标题时留几个空行
BLANK_BEFORE_PART = 1
# 标题与它自己的正文之间留几个空行
BLANK_AFTER_HEADING = 0

def heading_text(line: str) -> str:
    """去掉井号前缀，取标题文字。"""
    return line.lstrip("#").strip()


def blank_before(line: str, previous: str | None, parts: frozenset[str] = frozenset()) -> int:
    """某个标题前应留几个空行：章节边界 3 个，条目内分段与标题接标题 1 个，文首 0 个。"""
    if previous is None:
        return 0
    if HEADING.match(previous) or heading_text(line) in parts:
        return BLANK_BEFORE_PART
    return BLANK_BEFORE_HEADING


def _fence_mask(lines: list[str]) -> list[bool]:
    """标出每一行是否落在代码围栏内部（含围栏行本身）。"""
    inside = [False] * len(lines)
    open_fence = False
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            inside[i] = True
            open_fence = not open_fence
            continue
        inside[i] = open_fence
    return inside


def _prev_content_line(lines: list[str], index: int) -> str | None:
    """取某个标题之前最近的一行非空内容。"""
    cursor = index - 1
    while cursor >= 0 and lines[cursor].strip() == "":
        cursor -= 1
    return lines[cursor] if cursor >= 0 else None


def normalize_blank_lines(text: str, parts: frozenset[str] = frozenset()) -> str:
    """按 blank_before 的规则给每个标题前留空行，标题与正文之间不留空行；围栏内原样保留。

    「章节之间」指的是**正文结束、下一节开始**：只有这种情况才留 3 个空行。
    分类标题下面直接接小标题（这一节自己还没有正文）、条目内的分段标题，
    都只留 1 个——在它们前面压 3 行等于把同一个单元切碎。
    """
    lines = text.splitlines()
    inside = _fence_mask(lines)
    headings = [i for i, ln in enumerate(lines) if not inside[i] and HEADING.match(ln)]

    out: list[str] = []
    cursor = 0
    for index in headings:
        out.extend(lines[cursor:index])
        previous = _prev_content_line(lines, index)
        if out:
            while out and out[-1].strip() == "":
                out.pop()
            out.extend([""] * blank_before(lines[index], previous, parts))
        out.append(lines[index].rstrip())
        cursor = index + 1
        while cursor < len(lines) and lines[cursor].strip() == "":
            cursor += 1
        # 下一行是标题时由它自己的规则决定间隔；是正文时紧贴标题
        if cursor < len(lines) and not (not inside[cursor] and HEADING.match(lines[cursor])):
            out.extend([""] * BLANK_AFTER_HEADING)

    out.extend(lines[cursor:])
    while out and out[-1].strip() == "":
        out.pop()
    return "\n".join(out) + "\n"


def iter_items(text: str, spec: TemplateSpec):
    """逐个找出条目标题：产出 (行号, 编号捕获组的 match, 本分类内应有的编号)。"""
    lines = text.splitlines()
    inside = _fence_mask(lines)
    item_pattern = re.compile(spec.item_pattern)
    category = re.compile(rf"^{'#' * spec.category_level}\s+\S")
    item = re.compile(rf"^{'#' * spec.item_level}\s+(.*)$")
    counter = 0
    for i, line in enumerate(lines):
        if inside[i]:
            continue
        if category.match(line):
            counter = 0
            continue
        heading = item.match(line)
        if not heading:
            continue
        numbered = item_pattern.match(heading.group(1))
        if numbered and numbered.groups():
            counter += 1
            yield i, heading.start(1), numbered, counter


def renumber_items(text: str, spec: TemplateSpec) -> str:
    """每个分类下的条目编号从 1 重新开始；模版没声明带编号的条目时原样返回。"""
    if not spec.item_pattern:
        return text
    lines = text.splitlines()
    for i, offset, numbered, expected in list(iter_items(text, spec)):
        start, end = offset + numbered.start(1), offset + numbered.end(1)
        lines[i] = lines[i][:start] + str(expected) + lines[i][end:]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="规范化交付物 Markdown 的标题空行与条目编号")
    parser.add_argument("document", type=Path)
    parser.add_argument("--template", type=Path,
                        help="模版目录（或其中的 描述.md）；给了就按它的分段标题留空、按分类重排条目编号")
    parser.add_argument("--in-place", action="store_true", help="直接改写文件")
    parser.add_argument("--check", action="store_true", help="只检查是否需要规范化")
    args = parser.parse_args()

    original = args.document.read_text(encoding="utf-8")
    if args.template:
        spec = load_spec(args.template)
        result = renumber_items(normalize_blank_lines(original, frozenset(spec.part_headings)), spec)
    else:
        result = normalize_blank_lines(original)

    if args.check:
        if result == original:
            print(f"已是规范格式：{args.document}")
            return 0
        print(f"需要规范化：{args.document}", file=sys.stderr)
        return 1

    if args.in_place:
        args.document.write_text(result, encoding="utf-8")
        print(f"已规范化：{args.document}")
    else:
        sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
