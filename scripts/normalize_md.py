#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付物 Markdown 的排版规范化：标题空行、问答题号。

两条约定写在这里，不靠人记：

1. **章节之间留 3 个空行，标题与本段正文之间不留空行**。markdown 会把连续空行折叠成
   一个，所以「3 个空行」在渲染上和 1 个没有区别；写 3 个是为了让**源文件**里一眼看出
   章节边界——一个小节结束、下一个小节开始之前是断开的三行，而标题紧贴它自己的正文。
   问答每道题里固定的「简要回答 / 详细问答 / 相关知识」是同一道题的三个部分，
   不是新章节，它们前面只留 1 个空行；标题下面直接接小标题时也只留 1 个。
   代码围栏内部不动。
2. **问答的题号按分类独立编号**。每个 `##` 分类下从 `Q1` 重新开始，
   全篇不再连续编号（不出现「二、Q5」这种跨分类接着数的情况）。

知识博客文章、八股模拟面试两份 Markdown 交付物共用这一份，口径只在一处维护。

用法：
    python3 normalize_md.py <文件> [--check]
    python3 normalize_md.py <文件> --numbering qa
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


HEADING = re.compile(r"^(#{1,6})\s+\S")
QA_HEADING = re.compile(r"^(###)\s+Q\d+\.\s*(.*)$")

# 章节之间留几个空行（写在标题之前）
BLANK_BEFORE_HEADING = 3
# 同一单元内部的分段标题前、以及标题下直接接小标题时留几个空行
BLANK_BEFORE_PART = 1
# 标题与它自己的正文之间留几个空行
BLANK_AFTER_HEADING = 0

# 问答每道题里固定的三个分段标题：属于同一道题，前面不按章节边界留空
PART_HEADINGS = frozenset({"简要回答", "详细问答", "相关知识"})


def heading_text(line: str) -> str:
    """去掉井号前缀，取标题文字。"""
    return line.lstrip("#").strip()


def blank_before(line: str, previous: str | None) -> int:
    """某个标题前应留几个空行：章节边界 3 个，题内分段与标题接标题 1 个，文首 0 个。"""
    if previous is None:
        return 0
    if HEADING.match(previous) or heading_text(line) in PART_HEADINGS:
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


def normalize_blank_lines(text: str) -> str:
    """按 blank_before 的规则给每个标题前留空行，标题与正文之间不留空行；围栏内原样保留。

    「章节之间」指的是**正文结束、下一节开始**：只有这种情况才留 3 个空行。
    分类标题下面直接接小标题（这一节自己还没有正文）、问答题内的三个分段标题，
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
            out.extend([""] * blank_before(lines[index], previous))
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


def renumber_qa(text: str) -> str:
    """问答文档：每个 `##` 分类下的题号从 Q1 重新开始。"""
    lines = text.splitlines()
    inside = _fence_mask(lines)
    counter = 0
    for i, line in enumerate(lines):
        if inside[i]:
            continue
        if line.startswith("## ") and not line.startswith("### "):
            counter = 0
            continue
        m = QA_HEADING.match(line)
        if m:
            counter += 1
            lines[i] = f"{m.group(1)} Q{counter}. {m.group(2)}".rstrip()
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="规范化交付物 Markdown 的标题空行与问答题号")
    parser.add_argument("document", type=Path)
    parser.add_argument("--numbering", choices=["none", "qa"], default="none",
                        help="qa = 按分类重新编号；none = 只规范空行")
    parser.add_argument("--in-place", action="store_true", help="直接改写文件")
    parser.add_argument("--check", action="store_true", help="只检查是否需要规范化")
    args = parser.parse_args()

    original = args.document.read_text(encoding="utf-8")
    result = normalize_blank_lines(original)
    if args.numbering == "qa":
        result = renumber_qa(result)

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
