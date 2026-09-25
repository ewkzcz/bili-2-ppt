#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按所选 Markdown 模版检查交付物的结构、字段齐全度和交付物无感约束，不评价内容本身。

模版是 bili-document-builder/references/templates/<模版名>/ 目录，里面两份文档：
`描述.md`（格式定位描述，frontmatter 声明结构规则）与 `案例.md`（实际产物案例）。
这里只执行描述文件里声明的规则，不写死任何一种文档形态，也不假设任何具体主题——
新增模版只需放进一个目录，不用改这个脚本。

用法：
    python3 validate_document_output.py <文件> [--template <模版名>|auto]
    python3 validate_document_output.py --list
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = SKILL_ROOT / "references" / "templates"

# 交付物无感约束的词表在 bili-2-ppt/references/delivery-banlist.json，
# 所有交付物的校验脚本共用同一份——口径只在一处维护，不会各写一套慢慢跑偏。
BANLIST_PATH = SKILL_ROOT.parent / "references" / "delivery-banlist.json"

# mermaid 块的结构检查、排版约定、模版规则读取都放在 bili-2-ppt/scripts/ 下共用。
sys.path.insert(0, str(SKILL_ROOT.parent / "scripts"))
from mermaid_lint import find_mermaid_problems  # noqa: E402
from layout_rules import check_document as check_layout_rules  # noqa: E402
from md_template_spec import TemplateSpec, list_templates, load_spec  # noqa: E402


def load_banlist() -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """读出 (blocked, advisory) 两组正则。"""
    if not BANLIST_PATH.is_file():
        raise SystemExit(f"找不到禁用词表：{BANLIST_PATH}")
    data = json.loads(BANLIST_PATH.read_text(encoding="utf-8"))
    to_pairs = lambda items: tuple((pattern, label) for pattern, label in items)
    return to_pairs(data["blocked"]), to_pairs(data["advisory"])


BLOCKED_TRACES, ADVISORY_TRACES = load_banlist()


def mask_code_fences(text: str) -> str:
    """把 ``` 围栏代码块内部的行替换成等长空白，行号与字符偏移都不变，但内容不再参与正则匹配。

    代码块里的注释（比如 `# 说明`、`# 1. 步骤`）字面上和 Markdown 标题、
    有序列表长得一样，不挖掉会被结构校验和有序列表检查误判。
    """
    masked = []
    in_fence = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            masked.append(line)
            in_fence = not in_fence
            continue
        masked.append(" " * len(line) if in_fence else line)
    return "\n".join(masked)


def collect_traces(text: str, patterns: tuple[tuple[str, str], ...]) -> list[str]:
    found = []
    for pattern, label in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            found.append(f"{label}（如「{match.group(0)}」）")
    return found


def headings_at(text: str, level: int) -> list[re.Match]:
    return list(re.finditer(rf"^{'#' * level}\s+(\S.*?)\s*$", text, re.MULTILINE))


def section_spans(text: str, titles: list[str]) -> list[tuple[int, int]]:
    """找出标题文字属于 titles 的小节范围：到下一个同级或更高级标题为止。"""
    spans = []
    for match in re.finditer(r"^(#{1,6})\s+(\S.*?)\s*$", text, re.MULTILINE):
        if match.group(2) not in titles:
            continue
        level = len(match.group(1))
        start = match.end()
        nxt = re.search(rf"^#{{1,{level}}}\s+\S", text[start:], re.MULTILINE)
        spans.append((start, start + nxt.start() if nxt else len(text)))
    return spans


def check_opening(text: str, spec: TemplateSpec, errors: list[str]) -> None:
    """检查文档标题与第一个分类之间那段开篇正文。

    开篇只讲知识本身，有字数和段数上限，且不配图——
    「学习路线」这类导览、以及描述本文档排版格式的说明，都不该出现在交付物里。
    """
    title = headings_at(text, spec.title_level)
    if not title:
        return
    start = title[0].end()
    nxt = re.search(rf"^#{{1,{spec.category_level}}}\s+\S", text[start:], re.MULTILINE)
    body = text[start: start + nxt.start()] if nxt else text[start:]

    if re.search(r"^!\[", body, re.MULTILINE) or "```" in body:
        errors.append("开篇不配图：图和代码块留到正文里，开篇只用文字点题")
    paras = [p for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    if len(paras) > spec.opening_max_paras:
        errors.append(
            f"开篇 {len(paras)} 段，超过 {spec.opening_max_paras} 段——它只是个引入，正文自会展开"
        )
    chars = len(re.sub(r"\s", "", body))
    if chars > spec.opening_max_chars:
        errors.append(
            f"开篇 {chars} 字，超过 {spec.opening_max_chars} 字——只点出问题和核心矛盾"
        )


def check_structure(text: str, masked: str, spec: TemplateSpec, errors: list[str]) -> None:
    """标题层级、分类与条目的写法、条目必备内容、分段不留白、禁用标题。

    标题计数用屏蔽了代码围栏的文本（代码注释 `# 说明` 不能被当成标题）；
    条目块内容检查用原文，标题位置在屏蔽前后完全一致。
    """
    titles = headings_at(masked, spec.title_level)
    if len(titles) != 1:
        errors.append(f"应有唯一的 `{'#' * spec.title_level}` 文档标题，实际 {len(titles)} 个")
    for level in range(1, spec.title_level):
        if headings_at(masked, level):
            errors.append(f"文档标题是 `{'#' * spec.title_level}`，不应出现更高一级的 `{'#' * level}` 标题")

    categories = headings_at(masked, spec.category_level)
    if len(categories) < spec.min_categories:
        errors.append(f"分类过少（{len(categories)} 个），至少 {spec.min_categories} 个 `{'#' * spec.category_level}` 分类")
    if spec.category_pattern:
        stray = [m.group(1) for m in categories if not re.search(spec.category_pattern, m.group(1))]
        if stray:
            errors.append(f"`{'#' * spec.category_level}` 分类标题不合模版写法：{stray}")

    items = headings_at(masked, spec.item_level)
    if spec.item_pattern:
        stray = [
            m.group(1) for m in items
            if m.group(1) not in spec.part_headings and not re.search(spec.item_pattern, m.group(1))
        ]
        if stray:
            errors.append(f"`{'#' * spec.item_level}` 条目标题不合模版写法：{stray}")

    for name in spec.forbidden_headings:
        if re.search(rf"^#+\s*{re.escape(name)}", masked, re.MULTILINE):
            errors.append(f"不写「{name}」——它不含新内容，只白占篇幅")

    # 条目块：从一个条目标题到下一个同级或更高级标题
    item_heads = [m for m in items if m.group(1) not in spec.part_headings]
    for index, head in enumerate(item_heads):
        start = head.end()
        nxt = re.search(rf"^#{{1,{spec.item_level}}}\s+\S", masked[start:], re.MULTILINE)
        block = text[start: start + nxt.start()] if nxt else text[start:]
        name = head.group(1)
        for pattern, label in zip(spec.item_required, spec.item_required_names):
            if not re.search(pattern, block, re.MULTILINE):
                errors.append(f"「{name}」缺少{label}")

    for part in spec.part_headings:
        for start, end in section_spans(masked, [part]):
            if not masked[start:end].strip():
                errors.append(f"出现空的「{part}」小节，应删掉而不是留白占位")
                break


def check_ordered_lists(masked: str, spec: TemplateSpec, errors: list[str]) -> None:
    """正文的步骤与要点列表一律用 `-`；只有模版 `ordered_list_in` 声明的分段允许有序列表。"""
    spans = section_spans(masked, spec.ordered_list_in)
    for hit in re.finditer(r"^\s*\d+\.\s+\S.*$", masked, re.MULTILINE):
        if not any(start <= hit.start() < end for start, end in spans):
            allowed = f"，仅「{'、'.join(spec.ordered_list_in)}」内允许枚举用途" if spec.ordered_list_in else ""
            errors.append(f"正文出现有序列表（应改用 `-`{allowed}）：{hit.group(0).strip()}")
            break


def resolve_template(name: str, document: Path, text: str, templates: dict[str, Path]) -> TemplateSpec:
    """按名字取模版；auto 时先看文件名后缀 `-{模版名}.md`，再看各模版的 detect 正则。"""
    if name != "auto":
        if name not in templates:
            raise SystemExit(f"找不到模版「{name}」，可选：{list(templates)}")
        return load_spec(templates[name])
    stem = document.name.removesuffix(".md")
    for candidate, path in templates.items():
        if document.resolve() == path.with_name("案例.md").resolve() or stem.endswith((f"-{candidate}", f".{candidate}")):
            return load_spec(templates[candidate])
    for path in templates.values():
        spec = load_spec(path)
        if spec.detect and re.search(spec.detect, text, re.MULTILINE):
            return spec
    raise SystemExit(f"无法自动判定模版，请用 --template 指定，可选：{list(templates)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="按 Markdown 模版校验交付物文档")
    parser.add_argument("document", type=Path, nargs="?")
    parser.add_argument("--template", default="auto", help="模版名（templates/ 下的目录名），默认 auto")
    parser.add_argument("--list", action="store_true", help="列出可用模版")
    args = parser.parse_args()

    templates = list_templates(TEMPLATES_DIR)
    if args.list:
        for name, path in templates.items():
            print(f"{name}\t{load_spec(path).description}")
        return 0
    if args.document is None:
        parser.error("需要文档路径")

    text = args.document.read_text(encoding="utf-8")
    spec = resolve_template(args.template, args.document, text, templates)
    masked = mask_code_fences(text)
    errors: list[str] = []

    if args.document.suffix.lower() != ".md":
        errors.append("Markdown 交付物应为 .md 文件")

    check_opening(masked, spec, errors)
    check_structure(text, masked, spec, errors)
    check_ordered_lists(masked, spec, errors)

    # 图要查原始文本，不能用挖空后的文本——围栏本身就是要查的对象
    for lineno, message in find_mermaid_problems(text):
        errors.append(f"第 {lineno} 行：{message}")

    for item in collect_traces(text, BLOCKED_TRACES):
        errors.append(f"交付物不能出现暴露来源的内容：{item}")

    # 排版约定：标题空行、条目编号、mermaid 配色——写歪了在这里拦下
    for problem in check_layout_rules(args.document, spec):
        errors.append(f"排版约定：{problem}")

    if errors:
        print(f"校验失败（模版：{spec.name}）：")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"校验通过：{args.document}（模版：{spec.name}）")
    advisory = collect_traces(text, ADVISORY_TRACES)
    if advisory:
        print("提示（可能是正常用词，人工确认）：")
        for item in advisory:
            print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
