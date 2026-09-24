#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查 Markdown 里的 mermaid 块有没有被正文污染。

交付物里最常见的一类坏图不是语法写错，而是**正文被吃进了图里**：写图时忘了收尾，
后面那段解释文字就落进了围栏内，mermaid 解析器读到它不是语法，整张图直接报
「Lexical error on line N. Unrecognized text.」。渲染端只给出图内相对行号和一小段
被截断的文本，很难倒推到文件里的哪一行，所以在这里先拦一道。

判定分两种：

- **首行不是图表声明**：围栏打开了，但第一行就不是 `flowchart` 之类，说明围栏位置错了；
- **正文混进块内**：某一行既不是 mermaid 语句，也不落在允许自由文本的区间里。

允许自由文本的区间是 mermaid 语法本身规定的，不是放水：

- `stateDiagram` 的 `note ... end note`；
- `sequenceDiagram` 的 `Note over/left of/right of`；
- `gantt` 的 `title` / `dateFormat` / `axisFormat` / `section` 等配置行。

知识博客文章、八股模拟面试两份文档的校验共用这一份，口径只在一处维护。
"""

from __future__ import annotations

import re


# 图表首行声明，出现在围栏打开后的第一条有效语句上
DIAGRAM_DECL = re.compile(
    r"^(flowchart|graph|sequenceDiagram|stateDiagram-v2|stateDiagram|classDiagram"
    r"|erDiagram|gantt|pie|journey|mindmap|timeline|quadrantChart|gitGraph|block-beta"
    r"|sankey-beta|xychart-beta|packet-beta|architecture-beta)\b"
)

# 语句级关键字，这些开头的行一律当作合法语句
STATEMENT_KW = re.compile(
    r"^(classDef|class|style|linkStyle|click|subgraph|end|direction|participant|actor"
    r"|activate|deactivate|loop|alt|else|opt|par|and|rect|autonumber|note|state"
    r"|accTitle|accDescr)\b"
)

# mermaid 的结构符号。正文散文一般一个都不含，这是区分语句与散文的主要依据。
# 虚线箭头既有连写的 `-.->`，也有拆开带标签的 `-. "标签" .->`，两种都要认，
# 否则带标签的虚线会被误判成正文。
STRUCT_PUNCT = re.compile(
    r"(-->|==>|->>|-->>|<<--|<--|---|-\.->|-\.-|-\.|\.->|==|\[|\]|\(|\)|\{|\}|\||&|;)"
)

# 冒号只在少数图表类型里是语句的一部分：gantt 的任务行 `任务 :a1, 0, 10`、
# pie 的 `"标签" : 40`、类图与 ER 图的成员行。流程图和时序图不用裸冒号，
# 所以对它们不放开——否则「8 nodes. Good. Maybe simplify: ...」这种草稿行会被漏掉。
COLON_KINDS = {"gantt", "pie", "journey", "classDiagram", "erDiagram", "quadrantChart"}

# 各图表类型里允许写自由文本的区间
GANTT_FREE = re.compile(
    r"^(title|dateFormat|axisFormat|section|excludes|includes|todayMarker|tickInterval|weekday|inclusive)\b"
)
SEQ_NOTE = re.compile(r"^Note\s+(over|left of|right of)\b")
STATE_NOTE_OPEN = re.compile(r"^note\s+(left of|right of)\b")
STATE_NOTE_CLOSE = re.compile(r"^end note\b")


def _iter_mermaid_blocks(text: str):
    """逐个吐出 (围栏起始行号, 块内各行)。行号从 1 开始，指文件里的绝对行号。"""
    lines = text.split("\n")
    inside = False
    start = 0
    body: list[str] = []
    for index, line in enumerate(lines, 1):
        stripped = line.strip()
        if not inside:
            if stripped.startswith("```mermaid"):
                inside, start, body = True, index, []
            continue
        if stripped.startswith("```"):
            inside = False
            yield start, body
            continue
        body.append(line)
    if inside:
        # 围栏没闭合，收尾时也报出去，交给调用方判断
        yield start, body


def find_mermaid_problems(text: str) -> list[tuple[int, str]]:
    """返回 [(文件行号, 问题描述)]，没有问题时返回空列表。"""
    problems: list[tuple[int, str]] = []

    for start, body in _iter_mermaid_blocks(text):
        kind = ""
        # 跳过 %%{init:...}%% 这类前置指令，它们不算图表声明
        effective = [ln for ln in body if ln.strip() and not ln.strip().startswith("%%")]
        if not effective:
            problems.append((start, "mermaid 块是空的"))
            continue

        first = effective[0].strip()
        match = DIAGRAM_DECL.match(first)
        if not match:
            problems.append(
                (start + 1, f"mermaid 块的首行不是图表声明，而是「{first[:40]}」")
            )
            continue
        kind = match.group(1)

        state_note_region = False  # 是否处在 stateDiagram 的 note ... end note 里
        for offset, raw in enumerate(body):
            line = raw.strip()
            lineno = start + offset + 1
            if not line or line.startswith("%%"):
                continue
            if DIAGRAM_DECL.match(line) and offset <= 1:
                continue

            # stateDiagram 的 note 区间：区间内的多行文本由语法允许
            if kind.startswith("stateDiagram"):
                if state_note_region:
                    if STATE_NOTE_CLOSE.match(line):
                        state_note_region = False
                    continue
                if STATE_NOTE_OPEN.match(line):
                    state_note_region = True
                    continue

            if STATE_NOTE_CLOSE.match(line) or STATEMENT_KW.match(line):
                continue
            if kind == "gantt" and GANTT_FREE.match(line):
                continue
            if kind == "sequenceDiagram" and SEQ_NOTE.match(line):
                continue
            if STRUCT_PUNCT.search(line):
                continue
            if kind in COLON_KINDS and ":" in line:
                continue

            problems.append(
                (lineno, f"正文混进了 mermaid 块（{kind}），这行不是 mermaid 语句：「{line[:50]}」")
            )

    return problems


def lint_markdown(path) -> list[tuple[int, str]]:
    """读文件并返回其中的 mermaid 问题清单。"""
    return find_mermaid_problems(path.read_text(encoding="utf-8"))
