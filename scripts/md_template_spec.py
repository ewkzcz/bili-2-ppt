#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取 Markdown 交付物模版的描述文件，取出其中的结构规则。

一个 Markdown 模版是 `bili-document-builder/references/templates/<模版名>/` 目录，里面两份文档：

- `描述.md`：格式定位描述。说明这种文档的定位与格式规范；文件头的 frontmatter 声明结构规则；
- `案例.md`：实际产物案例，一份按这个格式写好的成品。

校验、排版规范化都只认这里读出的规则，不在脚本里写死任何一种文档形态——
新增模版只需放进一对文件，不用改脚本。

frontmatter 只支持扁平的 `键: 值`，值可以是整数、`true/false`、单引号字符串（`''` 转义单引号，
反斜杠原样保留，写正则最省心）、双引号字符串（JSON 转义）、裸字符串，以及 `[a, 'b', "c"]`
形式的行内列表。以 `#` 开头的行是注释。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
DESCRIPTION_FILE = "描述.md"
CASE_FILE = "案例.md"
LIST_ITEM = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"\\]|\\.)*\"|[^,]+")


def _scalar(raw: str):
    value = raw.strip()
    if value.startswith("'") and value.endswith("'") and len(value) >= 2:
        return value[1:-1].replace("''", "'")
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return json.loads(value)
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if value in ("true", "false"):
        return value == "true"
    return value


def _value(raw: str):
    value = raw.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_scalar(item) for item in LIST_ITEM.findall(inner) if item.strip()]
    return _scalar(value)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """拆出 frontmatter 字典与正文；没有 frontmatter 时返回空字典。"""
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    data: dict = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, raw = line.partition(":")
        if not sep:
            raise ValueError(f"frontmatter 行缺少冒号：{line}")
        data[key.strip()] = _value(raw)
    return data, text[match.end():]


@dataclass
class TemplateSpec:
    """一个 Markdown 模版的结构规则。字段含义见各模版描述文件的 frontmatter。"""

    name: str
    path: Path
    description: str = ""
    # 交付文件名，`{主题}` 由调用方替换
    output: str = "{主题}-{模版名}.md"
    # 文档标题的层级：全篇只有一个，且没有比它更高的标题
    title_level: int = 1
    # 分类标题：层级、每个标题都要匹配的正则、最少个数
    category_level: int = 2
    category_pattern: str = ""
    min_categories: int = 1
    # 分类下的条目标题（小节 / 问题）：层级与正则；正则里的第一个捕获组是编号，按分类从 1 重排
    item_level: int = 3
    item_pattern: str = ""
    # 每个条目里必须出现的行（正则）与对应的中文说明，两个列表一一对应
    item_required: list[str] = field(default_factory=list)
    item_required_names: list[str] = field(default_factory=list)
    # 条目内部的固定分段标题：它们前面只留 1 个空行，出现时不能是空小节
    part_headings: list[str] = field(default_factory=list)
    # 允许出现有序列表的分段（其余位置一律用 `-`）
    ordered_list_in: list[str] = field(default_factory=list)
    # 开篇正文的上限
    opening_max_chars: int = 200
    opening_max_paras: int = 2
    # 不许出现的标题（如结尾的复习清单）
    forbidden_headings: list[str] = field(default_factory=list)
    # auto 判定时用：文档里出现这条正则即认作本模版
    detect: str = ""

    @property
    def case_path(self) -> Path:
        return self.path.with_name(CASE_FILE)

    def output_name(self, topic: str) -> str:
        return self.output.replace("{模版名}", self.name).replace("{主题}", topic)


def load_spec(path: Path) -> TemplateSpec:
    """从描述文件读规则；传模版目录也行。未声明的字段取 TemplateSpec 的默认值。"""
    if path.is_dir():
        path = path / DESCRIPTION_FILE
    data, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    name = data.pop("name", path.parent.name)
    known = TemplateSpec.__dataclass_fields__
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ValueError(f"{path} 的 frontmatter 有未知字段：{unknown}")
    spec = TemplateSpec(name=name, path=path, **data)
    if len(spec.item_required) != len(spec.item_required_names):
        raise ValueError(f"{path}：item_required 与 item_required_names 个数不一致")
    return spec


def list_templates(directory: Path) -> dict[str, Path]:
    """列出目录下的模版：每个同时有 `描述.md` 与 `案例.md` 的子目录，返回 {模版名: 描述文件}。"""
    found: dict[str, Path] = {}
    for folder in sorted(p for p in directory.iterdir() if p.is_dir()):
        if (folder / DESCRIPTION_FILE).is_file() and (folder / CASE_FILE).is_file():
            found[folder.name] = folder / DESCRIPTION_FILE
    return found
