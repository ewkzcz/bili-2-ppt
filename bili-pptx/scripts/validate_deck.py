#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查学习笔记 deck 的结构、动画声明和交付物无感约束，不评价内容本身。

看四类问题：

- **交付物无感**：正文里有没有暴露素材采集过程或工具链的词；
- **占位符残留**：模板套壳留下的 xxxx、lorem 这类没换掉的字；
- **动画声明**：anim-N 的组号是不是 1..N 连续，一页分了太多步没有；
- **排版硬伤**：形状出框、文字形状互相压住、中文没设东亚字体（会掉回宋体）；
- **字体**：排版字体里有没有出现禁止使用的黑体、宋体这类系统默认中文字体；
- **位图**：学习笔记 deck 以真实画面为主体，这里只拦「一页堆太多张、没做过取舍」的情况；
- **特殊样式复刻**：模版用到的文字描边、渐变字、固定行距、投影、虚线这类视觉签名，deck 里一处都没有就报错
  （样式表漏抄了），确属有意不用的用 --allow-missing 声明；表格、编号列表这类取决于内容的只提示。

这里查的都是从 XML 就能确定的事实。文字压线、留白不均这类只有看图才知道的，
跑 render_preview.py 出图后人工看。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pptx import Presentation

# 模版原件所在目录：deck 的配色要落在所用模版原件出现过的颜色之内
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "references"
# 当前受检 deck 所用模版原件的全部色值，main() 里按 deck 记录的模版名读出
TEMPLATE_PALETTE: set[str] = set()
# 当前受检 deck 所用模版原件用到的全部字体名
TEMPLATE_FONTS: set[str] = set()
# 模版原件用到的特殊样式 → 出现的页
TEMPLATE_EFFECTS: dict[str, list[int]] = {}
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.oxml.ns import qn
from pptx.util import Emu

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pptx_effects import effects_of, how_to, signature  # noqa: E402


# 交付物无感约束的词表在 bili-2-ppt/references/delivery-banlist.json，
# 三份交付物的校验脚本共用同一份——口径只在一处维护，不会各写一套慢慢跑偏。
BANLIST_PATH = Path(__file__).resolve().parents[2] / "references" / "delivery-banlist.json"


def load_banlist() -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """读出 (blocked, advisory) 两组正则。"""
    if not BANLIST_PATH.is_file():
        raise SystemExit(f"找不到禁用词表：{BANLIST_PATH}")
    data = json.loads(BANLIST_PATH.read_text(encoding="utf-8"))
    to_pairs = lambda items: tuple((pattern, label) for pattern, label in items)
    return to_pairs(data["blocked"]), to_pairs(data["advisory"])


BLOCKED_TRACES, ADVISORY_TRACES = load_banlist()

PLACEHOLDER_PATTERNS = (
    (r"\bxxxx+\b", "xxxx 占位"),
    (r"\blorem\b|\bipsum\b", "lorem ipsum"),
    (r"点击编辑|单击此处添加|Click to edit", "版式占位提示"),
    # 必须带词边界：LangChain 的 `TodoListMiddleware` 和它的 `todos` 状态键是真实
    # API 名，不带边界会被当成待办标记误报。
    (r"\bTODO\b|\bFIXME\b|\bTBD\b", "待办标记"),
)

ANIM_NAME_RE = re.compile(r"^anim-(\d+)(?::([a-z]+))?$", re.IGNORECASE)
CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")

# 一页超过这个步数，说明这一页塞了太多概念，应该拆页。
MAX_STEPS_PER_SLIDE = 5

# 一页放超过这么多张画面，基本是直接堆上去了，没做取舍。
MAX_PICTURES_PER_SLIDE = 2

# 禁止使用的排版字体：黑体、宋体这类系统默认中文字体观感差，屏幕上发虚、笔画生硬。
# 所用模版原件自己用到的字体不在此列。
BANNED_FONTS = (
    "SimHei",
    "黑体",
    "SimSun",
    "宋体",
    "NSimSun",
    "新宋体",
)

# 允许超出画布的余量，单位英寸。装饰性色带稍微出血是正常版式，
# 但超出一个文字框的宽度就基本是坐标写错了。
BLEED_TOLERANCE_IN = 0.5


def iter_shapes(shapes):
    """递归展开组合形状，拿到所有叶子形状。"""
    for shape in shapes:
        if shape.shape_type == 6 and hasattr(shape, "shapes"):  # GROUP
            yield from iter_shapes(shape.shapes)
        else:
            yield shape


def iter_all_shapes(shapes):
    """递归展开，连组合形状本身一起产出。

    动画是按顶层组合的 id 挂的，只收叶子会把组合的 id 漏掉，
    于是 timing 里的引用会被误判成「引用了不存在的形状」。
    """
    for shape in shapes:
        yield shape
        if shape.shape_type == 6 and hasattr(shape, "shapes"):  # GROUP
            yield from iter_all_shapes(shape.shapes)


def shape_text(shape) -> str:
    """取一个形状里的全部文字，表格逐格取。"""
    parts: list[str] = []
    if shape.has_text_frame:
        parts.append(shape.text_frame.text)
    if getattr(shape, "has_table", False) and shape.has_table:
        for row in shape.table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(part for part in parts if part)


def slide_notes_text(slide) -> str:
    """取一页的演讲备注文字；这一页没有备注时返回空串。

    备注不在页面上，视觉自检看不到它，但放映时会出现在备注窗格、导出讲义时
    也会跟着走，所以它是交付物的一部分，必须和正文查同一套词表。
    """
    try:
        frame = slide.notes_slide.notes_text_frame
    except (AttributeError, KeyError):
        return ""
    return frame.text if frame is not None else ""


def collect_traces(text: str, patterns) -> list[str]:
    found = []
    for pattern, label in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            found.append(f"{label}（如「{match.group(0)}」）")
    return found


def missing_east_asian_font(shape) -> bool:
    """判断有没有中文文字缺了东亚字体设置。

    python-pptx 的 font.name 只写 a:latin，中文走 a:ea。
    只设了 latin 的话，中文在 PowerPoint 里会掉回宋体，和同页英文不是一个字体。
    """
    if not shape.has_text_frame:
        return False
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            if not CJK_RE.search(run.text or ""):
                continue
            r_pr = run._r.find(qn("a:rPr"))
            if r_pr is None or r_pr.find(qn("a:ea")) is None:
                return True
    return False


def banned_font_names(shape) -> list[str]:
    """取出这个形状里用到的禁止字体名。

    中文字体写在 a:ea 上、拉丁字体写在 a:latin 上，两边都要看；
    有些模板把字体写在 a:cs 上，一并收进来。
    """
    found: list[str] = []
    if not shape.has_text_frame:
        return found
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            r_pr = run._r.find(qn("a:rPr"))
            if r_pr is None:
                continue
            for tag in ("a:latin", "a:ea", "a:cs"):
                element = r_pr.find(qn(tag))
                if element is None:
                    continue
                face = element.get("typeface") or ""
                if face in BANNED_FONTS and face not in found:
                    found.append(face)
    return found


def read_timing(slide, shape_ids: set[int]) -> tuple[int, int, list[str]]:
    """从幻灯片的 <p:timing> 里读注入结果。

    读 timing 而不是读形状名，是因为 inject_animations.py 注入完会把形状名洗掉——
    名字只在注入前存在，注入后才是一份 deck 的真实状态。

    返回 (点击步数, 动画元素数, 问题列表)。
    """
    timing = slide._element.find(qn("p:timing"))
    if timing is None:
        return 0, 0, []

    clicks = 0
    elements = 0
    dangling: list[str] = []

    for c_tn in timing.iter(qn("p:cTn")):
        node_type = c_tn.get("nodeType")
        if node_type == "clickEffect":
            clicks += 1
        if c_tn.get("presetClass"):
            elements += 1

    # timing 里引用的 spid 必须真的存在于这一页，否则放映时这个效果静默失效
    for sp_tgt in timing.iter(qn("p:spTgt")):
        spid = sp_tgt.get("spid")
        if spid and int(spid) not in shape_ids:
            dangling.append(spid)

    return clicks, elements, sorted(set(dangling))


EMU_PER_PT = 12700
DEFAULT_INSET_EMU = 91440  # 文本框默认内边距 0.1 英寸
# 中日韩文字、全角标点，以及 —、「」 这类在中文字体里按全角排的符号
WIDE_CHAR_RE = re.compile(r"[\u2014\u2015\u2018-\u201f\u2026\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]")


def _char_width_em(ch: str) -> float:
    """估一个字符占多少个 em：全角字符一格，半角字符约半格。"""
    # 中文字形实际排出来比 1em 略宽一点，留 4% 余量，宁可多报不要漏报
    return 1.04 if WIDE_CHAR_RE.match(ch) else 0.56


def estimate_overflow(shape) -> tuple[float, float] | None:
    """估算形状里的文字排完之后有多高，返回 (估算高度, 可用高度)，单位 pt。

    只看自动换行的文本框和自动图形。估算刻意偏保守（字宽取常见值，
    行高按 1.2 倍字号乘段落行距），用来发现「字明显比框多」的情况——
    这类问题几何上看不出重叠，渲染出来却是字被下面的元素盖住。
    """
    if not shape.has_text_frame:
        return None
    frame = shape.text_frame
    text = frame.text.strip()
    if not text or shape.width is None or shape.height is None:
        return None

    def inset(value):
        return DEFAULT_INSET_EMU if value is None else value

    avail_w = (shape.width - inset(frame.margin_left) - inset(frame.margin_right)) / EMU_PER_PT
    avail_h = (shape.height - inset(frame.margin_top) - inset(frame.margin_bottom)) / EMU_PER_PT
    if avail_w <= 0 or avail_h <= 0:
        return None

    total = 0.0
    for paragraph in frame.paragraphs:
        size = None
        for run in paragraph.runs:
            if run.font.size is not None:
                size = run.font.size.pt
                break
        size = size or 18.0
        content = "".join(run.text or "" for run in paragraph.runs)
        width = sum(_char_width_em(ch) for ch in content) * size
        lines = max(1, -(-int(width) // max(int(avail_w), 1)))
        spacing = paragraph.line_spacing if isinstance(paragraph.line_spacing, float) else 1.0
        total += lines * size * 1.2 * spacing
        for extra in (paragraph.space_before, paragraph.space_after):
            if extra is not None:
                total += extra.pt
    return total, avail_h


def find_text_overflow(slide, slide_height) -> list[str]:
    """列出溢出部分会压到别的元素、或越过页面底边的文字形状。

    文字比框高多出一截本身不一定是问题——下面是空白时渲染完全正常。
    只有多出来的那一截落在别的形状上、或者掉出页面，读者才会看到字被盖住。
    """
    shapes = [s for s in iter_shapes(slide.shapes)
              if None not in (s.left, s.top, s.width, s.height)]
    hits = []
    for shape in shapes:
        result = estimate_overflow(shape)
        if not result:
            continue
        need, have = result
        if not (need > have * 1.05 and need - have > 4):
            continue
        band_top, band_bottom = _text_band(shape)
        box_top, box_bottom = shape.top, shape.top + shape.height
        spill = [(band_top, box_top), (box_bottom, band_bottom)]
        crashed = band_bottom > slide_height - int(0.25 * 914400)
        for other in shapes:
            if other is shape or crashed:
                continue
            if min(shape.left + shape.width, other.left + other.width) - max(shape.left, other.left) <= 0:
                continue
            for lo, hi in spill:
                if hi - lo > 0 and min(hi, other.top + other.height) - max(lo, other.top) > 4 * EMU_PER_PT:
                    crashed = True
                    break
        if crashed:
            label = shape.text_frame.text.strip().splitlines()[0][:14]
            hits.append(f"「{label}」")
    return hits


def _text_band(shape) -> tuple[int, int]:
    """估出形状里文字实际占的纵向区间（EMU）。

    文本框默认顶对齐，自动图形默认垂直居中；显式设了锚点就按锚点算。
    """
    from pptx.enum.text import MSO_ANCHOR

    frame = shape.text_frame
    result = estimate_overflow(shape)
    need = int(result[0] * EMU_PER_PT) if result else shape.height
    top_inset = DEFAULT_INSET_EMU if frame.margin_top is None else frame.margin_top
    bottom_inset = DEFAULT_INSET_EMU if frame.margin_bottom is None else frame.margin_bottom
    inner_top = shape.top + top_inset
    inner_bottom = shape.top + shape.height - bottom_inset

    anchor = frame.vertical_anchor
    if anchor is None:
        anchor = MSO_ANCHOR.TOP if shape.shape_type == MSO_SHAPE_TYPE.TEXT_BOX else MSO_ANCHOR.MIDDLE
    if anchor == MSO_ANCHOR.MIDDLE:
        mid = (inner_top + inner_bottom) // 2
        return mid - need // 2, mid + need // 2
    if anchor == MSO_ANCHOR.BOTTOM:
        return inner_bottom - need, inner_bottom
    return inner_top, inner_top + need


def find_text_collisions(slide) -> list[str]:
    """找出互相压住的文字形状。

    两类情况：
    - 两个带文字的形状部分交叠（交叠面积超过较小者的 8%）；
    - 一个带文字的形状整个落在另一个带文字的形状里，而外层文字实际占的那一段
      和内层形状有纵向交叠——外层的字会从内层下面透出来或被盖住。
    """
    items = []
    for shape in iter_shapes(slide.shapes):
        if not shape.has_text_frame or not shape.text_frame.text.strip():
            continue
        if None in (shape.left, shape.top, shape.width, shape.height):
            continue
        items.append(shape)

    def label(shape):
        return shape.text_frame.text.strip().splitlines()[0][:14]

    min_overlap = 6 * EMU_PER_PT
    hits = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            ax0, ay0, ax1, ay1 = a.left, a.top, a.left + a.width, a.top + a.height
            bx0, by0, bx1, by1 = b.left, b.top, b.left + b.width, b.top + b.height
            w = min(ax1, bx1) - max(ax0, bx0)
            h = min(ay1, by1) - max(ay0, by0)
            if w <= 0 or h <= 0:
                continue
            a_in_b = ax0 >= bx0 and ay0 >= by0 and ax1 <= bx1 and ay1 <= by1
            b_in_a = bx0 >= ax0 and by0 >= ay0 and bx1 <= ax1 and by1 <= ay1
            if a_in_b or b_in_a:
                outer, inner = (b, a) if a_in_b else (a, b)
                band_top, band_bottom = _text_band(outer)
                if min(band_bottom, inner.top + inner.height) - max(band_top, inner.top) > min_overlap:
                    hits.append(f"「{label(outer)}」×「{label(inner)}」")
                continue
            smaller = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
            if smaller and (w * h) / smaller >= 0.08:
                hits.append(f"「{label(a)}」×「{label(b)}」")
    return hits


def check_slide(prs, slide, index: int, errors: list, warnings: list) -> dict:
    """检查一页，返回这一页的统计。"""
    slide_no = index + 1
    texts: list[str] = []
    declared_groups: dict[int, int] = {}
    off_slide: list[str] = []
    no_ea_font: list[str] = []
    bad_font: list[str] = []
    pictures: list[str] = []
    shape_ids: set[int] = set()

    for shape in iter_all_shapes(slide.shapes):
        shape_ids.add(shape.shape_id)

    # python-pptx 不展开 mc:AlternateContent（原生公式就写在那里面），
    # 只靠上面那圈会把这类形状漏掉，挂在它们上的动画会被误判成悬空引用。
    for raw_id in re.findall(r'<p:cNvPr id="(\d+)"', slide._element.xml):
        shape_ids.add(int(raw_id))

    slide_w = Emu(prs.slide_width)
    slide_h = Emu(prs.slide_height)
    tolerance = Emu(int(BLEED_TOLERANCE_IN * 914400))

    for shape in iter_shapes(slide.shapes):
        text = shape_text(shape)
        if text.strip():
            texts.append(text)

        name = shape.name or ""
        match = ANIM_NAME_RE.match(name.strip())
        if match:
            group = int(match.group(1))
            declared_groups[group] = declared_groups.get(group, 0) + 1

        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            pictures.append(name)

        # 形状出框：左右上下任一边超出画布
        try:
            left, top = shape.left, shape.top
            width, height = shape.width, shape.height
        except (AttributeError, TypeError):
            continue
        if None in (left, top, width, height):
            continue
        # 名字带 bg- 前缀的是铺在背景上的装饰层（柔光团、底衬），允许出血到画布外；
        # 出血是这类元素的做法，不是排版事故。
        is_decoration = name.startswith("bg-")
        if not is_decoration and (
            left < -tolerance
            or top < -tolerance
            or left + width > slide_w + tolerance
            or top + height > slide_h + tolerance
        ):
            label = text.strip().splitlines()[0][:20] if text.strip() else name
            off_slide.append(f"「{label}」")

        if missing_east_asian_font(shape):
            label = text.strip().splitlines()[0][:20] if text.strip() else name
            no_ea_font.append(f"「{label}」")

        for face in banned_font_names(shape):
            if face not in bad_font and face not in TEMPLATE_FONTS:
                bad_font.append(face)

    joined = "\n".join(texts)

    for color in find_off_theme_colors(slide):
        errors.append(f"第 {slide_no} 页：出现模版原件里没有的颜色 #{color}，配色要取自模版原件")
    for trace in collect_traces(joined, BLOCKED_TRACES):
        errors.append(f"第 {slide_no} 页：出现 {trace}")
    for trace in collect_traces(joined, ADVISORY_TRACES):
        warnings.append(f"第 {slide_no} 页：出现 {trace}（确认不是素材痕迹）")
    for pattern, label in PLACEHOLDER_PATTERNS:
        match = re.search(pattern, joined, re.IGNORECASE)
        if match:
            errors.append(f"第 {slide_no} 页：残留{label}（「{match.group(0)}」）")

    # 备注和正文用同一套词表。它是整副牌里最容易漏的一块：不在页面上，
    # 靠看渲染图发现不了，只有拆开 pptx 读 notesSlide 才看得见。
    notes_text = slide_notes_text(slide)
    if notes_text.strip():
        for trace in collect_traces(notes_text, BLOCKED_TRACES):
            errors.append(f"第 {slide_no} 页备注：出现 {trace}")
        for trace in collect_traces(notes_text, ADVISORY_TRACES):
            warnings.append(f"第 {slide_no} 页备注：出现 {trace}（确认不是素材痕迹）")
        for pattern, label in PLACEHOLDER_PATTERNS:
            match = re.search(pattern, notes_text, re.IGNORECASE)
            if match:
                errors.append(f"第 {slide_no} 页备注：残留{label}（「{match.group(0)}」）")

    clicks, elements, dangling = read_timing(slide, shape_ids)

    if dangling:
        errors.append(
            f"第 {slide_no} 页：动画引用了不存在的形状 id {dangling}，放映时这些效果不会生效"
        )

    # 声明了但没注入：说明 inject_animations.py 没跑，或者跑的是另一份文件
    if declared_groups and not elements:
        errors.append(
            f"第 {slide_no} 页：形状名上还有 anim 声明（{sorted(declared_groups)}），"
            "但 timing 里没有动画——inject_animations.py 没跑"
        )
    # 注入过但名字没洗掉：内部标记留在了交付物里
    if declared_groups and elements:
        errors.append(
            f"第 {slide_no} 页：动画已注入但形状名没洗掉（{sorted(declared_groups)}）"
        )

    if clicks > MAX_STEPS_PER_SLIDE:
        warnings.append(
            f"第 {slide_no} 页：分了 {clicks} 步，"
            f"超过 {MAX_STEPS_PER_SLIDE} 步说明这一页塞太多概念，考虑拆页"
        )

    if declared_groups and sorted(declared_groups) != list(
        range(1, len(declared_groups) + 1)
    ):
        errors.append(
            f"第 {slide_no} 页：动画组号必须是 1..{len(declared_groups)} 连续整数，"
            f"实际是 {sorted(declared_groups)}"
        )

    if len(pictures) > MAX_PICTURES_PER_SLIDE:
        warnings.append(
            f"第 {slide_no} 页：放了 {len(pictures)} 张位图（{'、'.join(pictures[:3])}）。"
            "画面是页面的主体，但一页堆太多张说明没做过取舍——挑最能说明问题的一到两张，"
            "其余改用 deck_kit 的原生元素重建"
        )
    overflow = find_text_overflow(slide, slide_h)
    if overflow:
        warnings.append(f"第 {slide_no} 页：文字超出框高 {'、'.join(overflow[:3])}，溢出部分压到了别的元素或掉出页面")
    collisions = find_text_collisions(slide)
    if collisions:
        warnings.append(f"第 {slide_no} 页：文字形状互相压住 {'、'.join(collisions[:3])}")
    if off_slide:
        warnings.append(f"第 {slide_no} 页：形状超出画布 {'、'.join(off_slide)}")
    if no_ea_font:
        warnings.append(
            f"第 {slide_no} 页：中文没设东亚字体 {'、'.join(no_ea_font)}，"
            "在 PowerPoint 里会掉回宋体"
        )
    if bad_font:
        errors.append(
            f"第 {slide_no} 页：用了禁止的排版字体 {'、'.join(bad_font)}。"
            "中文排版照模版原件的字体，黑体、宋体这类系统默认中文字体观感差、屏幕上发虚"
        )

    return {
        "slide": slide_no,
        "shapes": len(list(iter_shapes(slide.shapes))),
        "steps": clicks,
        "animated_shapes": elements,
        "pictures": len(pictures),
    }


def read_template(prs) -> None:
    """按 deck 文档属性里记录的模版名，读出该模版原件出现过的全部色值与字体名。"""
    name = prs.core_properties.category
    source = TEMPLATES_DIR / f"{name}.pptx"
    if not name or not source.is_file():
        raise SystemExit(f"deck 没有记录可用的模版名（文档属性 category = {name!r}），"
                         "建 deck 时用 deck_kit.new_deck(template=...) 写入")
    for number, slide in enumerate(Presentation(str(source)).slides, 1):
        for effect in effects_of(slide._element):
            TEMPLATE_EFFECTS.setdefault(effect, []).append(number)
        xml = slide._element.xml
        TEMPLATE_PALETTE.update(v.upper() for v in re.findall(r'srgbClr val="([0-9A-Fa-f]{6})"', xml))
        TEMPLATE_FONTS.update(re.findall(r'typeface="([^"+][^"]*)"', xml))


def check_effects(prs, allowed: set[str], errors: list, warnings: list) -> None:
    """模版用到的特殊样式，deck 里至少要复刻出一处。"""
    used = set()
    for slide in prs.slides:
        used |= effects_of(slide._element)
    name = prs.core_properties.category
    for effect, pages in TEMPLATE_EFFECTS.items():
        if effect in used or effect in allowed:
            continue
        where = ",".join(map(str, pages[:8]))
        message = (f"模版 {name} 第 {where} 页用了「{effect}」，deck 里一处都没有："
                   f"样式表按 {how_to(effect)} 补上")
        if signature(effect):
            errors.append(message + "；确属有意不用的，用 --allow-missing " + effect + " 声明")
        else:
            warnings.append(message + "（本份内容用不到可忽略）")


def find_off_theme_colors(slide) -> list[str]:
    """页面上出现模版原件以外的色值，说明配色没有取自模版。纯白另外放行（相框、斑马纹会用）。"""
    allowed = TEMPLATE_PALETTE | {"FFFFFF"}
    found = {
        match.group(1).upper()
        for match in re.finditer(r'srgbClr val="([0-9A-Fa-f]{6})"', slide._element.xml)
    }
    return sorted(found - allowed)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="检查学习笔记 deck 的结构、动画声明和交付物无感约束",
    )
    parser.add_argument("pptx", type=Path, help="要检查的 pptx")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument(
        "--allow-uninjected",
        action="store_true",
        help="允许还没注入动画（此时只查文字与排版，不要求 anim 标记）",
    )
    parser.add_argument(
        "--allow-missing",
        default="",
        help="有意不复刻的模版特殊样式，逗号分隔（如 旋转,背景图）",
    )
    args = parser.parse_args()

    if not args.pptx.is_file():
        raise SystemExit(f"找不到文件：{args.pptx}")

    prs = Presentation(str(args.pptx))
    read_template(prs)
    errors: list[str] = []
    warnings: list[str] = []
    stats = [
        check_slide(prs, slide, index, errors, warnings)
        for index, slide in enumerate(prs.slides)
    ]

    check_effects(prs, {x.strip() for x in args.allow_missing.split(",") if x.strip()},
                  errors, warnings)

    animated_total = sum(item["animated_shapes"] for item in stats)
    picture_total = sum(item["pictures"] for item in stats)
    if animated_total == 0 and not args.allow_uninjected:
        errors.append(
            "整份 deck 没有任何动画。用 deck_kit 的 anim= 参数标记要分步出现的形状，"
            "再跑 inject_animations.py"
        )
    if args.json:
        print(
            json.dumps(
                {
                    "file": str(args.pptx),
                    "slides": stats,
                    "animated_shapes": animated_total,
                    "errors": errors,
                    "warnings": warnings,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            f"{args.pptx}：{len(stats)} 页，{animated_total} 个动画形状，位图 {picture_total} 张"
        )
        for item in stats:
            step_text = f"{item['steps']} 步" if item["steps"] else "无动画"
            print(f"  第 {item['slide']:>2} 页  {item['shapes']:>2} 个形状  {step_text}")
        print()
        for warning in warnings:
            print(f"提示  {warning}")
        for error in errors:
            print(f"错误  {error}")
        print()
        print(f"错误 {len(errors)} 条，提示 {len(warnings)} 条")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
