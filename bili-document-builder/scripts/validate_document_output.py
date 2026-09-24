#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查两份 Markdown 交付物的结构、字段齐全度和交付物无感约束，不评价内容本身。

两种文档形态（知识博客文章 / 八股模拟面试）的规范见
bili-document-builder/references/ 下的 blog-template.md 和 qa-template.md。
这里只按结构模式校验，不假设任何具体主题，换题材可以直接复用。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# 交付物无感约束的词表在 bili-2-ppt/references/delivery-banlist.json，
# 三份交付物的校验脚本共用同一份——口径只在一处维护，不会各写一套慢慢跑偏。
BANLIST_PATH = Path(__file__).resolve().parents[2] / "references" / "delivery-banlist.json"

# mermaid 块的结构检查同样三份共用，放在 bili-2-ppt/scripts/ 下。
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from mermaid_lint import find_mermaid_problems  # noqa: E402
from layout_rules import check_document as check_layout_rules  # noqa: E402


def load_banlist() -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """读出 (blocked, advisory) 两组正则。"""
    if not BANLIST_PATH.is_file():
        raise SystemExit(f"找不到禁用词表：{BANLIST_PATH}")
    data = json.loads(BANLIST_PATH.read_text(encoding="utf-8"))
    to_pairs = lambda items: tuple((pattern, label) for pattern, label in items)
    return to_pairs(data["blocked"]), to_pairs(data["advisory"])


BLOCKED_TRACES, ADVISORY_TRACES = load_banlist()

QA_REQUIRED_SECTION = "简要回答"
QA_OPTIONAL_SECTIONS = ("详细问答", "相关知识")
CN_NUMERALS = "一二三四五六七八九十"


def mask_code_fences(text: str) -> str:
    """把 ``` 围栏代码块内部的行替换成等长空行，行号不变但内容不再参与正则匹配。

    代码块里的注释（比如 `# 说明`、`# 1. 步骤`）字面上和 Markdown 标题、
    有序列表长得一样，不挖掉会被结构校验和有序列表检查误判。
    """
    lines = text.split("\n")
    masked = []
    in_fence = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```"):
            masked.append(line)
            in_fence = not in_fence
            continue
        masked.append("" if in_fence else line)
    return "\n".join(masked)


def collect_traces(text: str, patterns: tuple[tuple[str, str], ...]) -> list[str]:
    found = []
    for pattern, label in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            found.append(f"{label}（如「{match.group(0)}」）")
    return found


def check_ordered_lists(text: str, errors: list[str], *, allow_in_detailed_qa: bool = False) -> None:
    """正文的步骤与要点列表一律用 `-`；题号写在标题里不算列表编号。

    八股模拟面试的「详细问答」小节允许用有序列表写枚举清点式的事实（"分几个区域"
    这类），其余位置（简要回答、相关知识、知识博客文章全篇）依然禁止。
    """
    masked = mask_code_fences(text)
    if not allow_in_detailed_qa:
        hit = re.search(r"^\s*\d+\.\s+\S", masked, re.MULTILINE)
        if hit:
            errors.append(f"正文出现有序列表（应改用 `-`）：{hit.group(0).strip()}")
        return

    # 找出每个「#### 详细问答」小节的范围（到下一个 <=4 级标题为止），范围外才检查
    spans = []
    for match in re.finditer(r"^####\s+详细问答\s*$", masked, re.MULTILINE):
        start = match.end()
        next_heading = re.search(r"^#{1,4}\s+\S", masked[start:], re.MULTILINE)
        end = start + next_heading.start() if next_heading else len(masked)
        spans.append((start, end))

    def inside_detailed_qa(pos: int) -> bool:
        return any(start <= pos < end for start, end in spans)

    for hit in re.finditer(r"^\s*\d+\.\s+\S.*$", masked, re.MULTILINE):
        if not inside_detailed_qa(hit.start()):
            errors.append(f"正文出现有序列表（应改用 `-`，仅「详细问答」内允许枚举用途）：{hit.group(0).strip()}")
            break


# 开篇正文的上限：它只是个引入，铺陈开等于把后面的内容提前讲一遍
OPENING_MAX_CHARS = 200
OPENING_MAX_PARAS = 2


def check_opening(text: str, title_level: int, first_section: str, errors: list) -> None:
    """检查文档标题与第一个分类之间那段开篇正文。

    开篇只讲知识本身，两段以内、200 字以内，且不配图——
    「学习路线」这类导览、以及描述本文档排版格式的说明，都不该出现在交付物里。
    """
    head = "#" * title_level
    m = re.search(rf"^{head}\s+\S.*$", text, re.MULTILINE)
    if not m:
        return
    nxt = re.search(rf"^{first_section}\s+\S", text[m.end():], re.MULTILINE)
    body = text[m.end(): m.end() + nxt.start()] if nxt else text[m.end():]

    if re.search(r"^!\[", body, re.MULTILINE) or "```" in body:
        errors.append("开篇不配图：图和代码块留到正文里，开篇只用文字点题")

    paras = [p for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    if len(paras) > OPENING_MAX_PARAS:
        errors.append(
            f"开篇 {len(paras)} 段，超过 {OPENING_MAX_PARAS} 段——它只是个引入，正文自会展开"
        )
    chars = len(re.sub(r"\s", "", body))
    if chars > OPENING_MAX_CHARS:
        errors.append(
            f"开篇 {chars} 字，超过 {OPENING_MAX_CHARS} 字——压到两段以内，只点出问题和核心矛盾"
        )


def check_blog(text_raw: str, errors: list[str]) -> None:
    """知识博客文章：章节按知识点与概念编排。只查层级模式，不限定题材。"""
    text = mask_code_fences(text_raw)
    if not re.search(r"^##\s+\S", text, re.MULTILINE):
        errors.append("知识博客文章缺少唯一的 `## {主题}` 文档标题")
    if len(re.findall(r"^##\s+\S", text, re.MULTILINE)) > 1:
        errors.append("知识博客文章的 `##` 应只有一个（文档标题），分类请用 `###`")
    # 开篇只讲知识本身，直接挂在文档标题下面，不另起小标题；
    # 「学习路线」这类导览讲的是文档怎么用，不是知识，一律不排
    stray = [
        title
        for title in re.findall(r"^###\s+(.+?)\s*$", text, re.MULTILINE)
        if not re.match(rf"^[{CN_NUMERALS}]+、\S", title)
    ]
    if stray:
        errors.append(
            f"`###` 只能装 `一、二、三…` 编号的分类，出现了导览式标题：{stray}"
        )

    categories = re.findall(rf"^###\s+([{CN_NUMERALS}]+)、\S", text, re.MULTILINE)
    if len(categories) < 3:
        errors.append(f"知识博客文章分类过少（{len(categories)} 个），应为 4～8 个 `### 一、` 形式的分类")

    if not re.search(r"^####\s+\d+、\S", text, re.MULTILINE) and not categories:
        errors.append("知识博客文章的小节应使用 `#### 1、` 层级")
    if re.search(r"^#+\s*(复习清单|自测清单)", text, re.MULTILINE):
        errors.append("知识博客文章不写结尾的复习清单——它不含新知识点，只白占篇幅")


def check_qa(text: str, errors: list[str]) -> None:
    """八股模拟面试：`##` 分类、`###` 问题，每题必须有标签行和「简要回答」，
    「详细问答」「相关知识」按需选用，不强制。

    标题计数用屏蔽了代码围栏的文本（代码注释 `# 说明` 不能被当成一级标题）；
    切分问题块和块内内容检查仍用原文，标题位置在屏蔽前后完全一致，
    但块内容不能被误挖空。
    """
    masked = mask_code_fences(text)
    if len(re.findall(r"^#\s+\S", masked, re.MULTILINE)) != 1:
        errors.append("八股模拟面试应有唯一的 `# {主题} 八股模拟面试` 文档标题")
    if not re.search(r"^##\s+\S", masked, re.MULTILINE):
        errors.append("八股模拟面试缺少 `##` 分类层级")
    if re.search(r"^#+\s*(自测清单|复习清单)", masked, re.MULTILINE):
        errors.append("八股模拟面试不写结尾的自测清单——清单只是把考点再列一遍，不含新内容")
    questions = re.split(r"^###\s+", text, flags=re.MULTILINE)[1:]
    if not questions:
        errors.append("八股模拟面试没有解析到 `###` 形式的问题")
        return
    for index, block in enumerate(questions, start=1):
        title = block.splitlines()[0].strip() if block.strip() else f"第 {index} 题"
        if not re.search(r"^>\s*标签：", block, re.MULTILINE):
            errors.append(f"「{title}」缺少标签元信息行（`> 标签：...`）")
        if f"#### {QA_REQUIRED_SECTION}" not in block:
            errors.append(f"「{title}」缺少「{QA_REQUIRED_SECTION}」段")
        for section in QA_OPTIONAL_SECTIONS:
            # 可选段，只在出现时顺带检查不是空标题占位
            heading = f"#### {section}"
            if heading in block:
                after = block.split(heading, 1)[1]
                next_heading = re.search(r"^#{1,4}\s+\S", after, re.MULTILINE)
                body = after[: next_heading.start()] if next_heading else after
                if not body.strip():
                    errors.append(f"「{title}」的「{section}」是空小节，应删掉而不是留白占位")


def detect_mode(text: str) -> str:
    if re.search(r"^####\s+简要回答\s*$", text, re.MULTILINE):
        return "qa"
    return "blog"


def main() -> int:
    parser = argparse.ArgumentParser(description="校验知识博客文章 / 八股模拟面试文档")
    parser.add_argument("document", type=Path)
    parser.add_argument("--mode", choices=("blog", "qa", "auto"), default="auto")
    args = parser.parse_args()
    text = args.document.read_text(encoding="utf-8")
    errors: list[str] = []

    if args.document.suffix.lower() != ".md":
        errors.append("这两份交付物都是 Markdown")

    mode = args.mode if args.mode != "auto" else detect_mode(text)
    check_opening(mask_code_fences(text), 1 if mode == "qa" else 2,
                  "##" if mode == "qa" else "###", errors)
    if mode == "qa":
        check_qa(text, errors)
    else:
        check_blog(text, errors)
    check_ordered_lists(text, errors, allow_in_detailed_qa=(mode == "qa"))

    # 图要查原始文本，不能用 mask_code_fences 挖空后的文本——围栏本身就是要查的对象
    for lineno, message in find_mermaid_problems(text):
        errors.append(f"第 {lineno} 行：{message}")

    for item in collect_traces(text, BLOCKED_TRACES):
        errors.append(f"交付物不能出现暴露来源的内容：{item}")

    # 排版约定：标题空行、问答题号、mermaid 配色——写歪了在这里拦下
    for problem in check_layout_rules(args.document, qa=(mode == "qa")):
        errors.append(f"排版约定：{problem}")

    if errors:
        print("校验失败：")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"校验通过：{args.document}（模式：{mode}）")
    advisory = collect_traces(text, ADVISORY_TRACES)
    if advisory:
        print("提示（可能是正常用词，人工确认）：")
        for item in advisory:
            print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
