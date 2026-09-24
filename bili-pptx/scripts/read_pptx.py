#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一份 pptx 的设计元数据整理成可读清单：主题配色与字体、每页背景、每个形状的位置与样式、每段文字的字样。

读模版原件时用它代替逐个翻 XML：数值与 XML 一致（坐标英寸、字号 pt、字距 spc 为 1/100 pt、
圆角换算成英寸），看完再按内容自己构图。

用法：
    read_pptx.py <原件.pptx>                 # 全部页
    read_pptx.py <原件.pptx> --slides 1,3,4  # 只列这些页（主题与全篇字体、颜色用量照常汇总）
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from pptx import Presentation
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.ns import qn

EMU = 914400


def _color(parent) -> str:
    """一个填充 / 描边节点下的颜色与不透明度，如 1558E8 或 FFFFFF@62%。"""
    if parent is None:
        return ""
    clr = parent.find(".//" + qn("a:srgbClr"))
    if clr is None:
        scheme = parent.find(".//" + qn("a:schemeClr"))
        return f"scheme:{scheme.get('val')}" if scheme is not None else ""
    alpha = clr.find(qn("a:alpha"))
    return clr.get("val") + (f"@{int(alpha.get('val')) // 1000}%" if alpha is not None else "")


def _shape_style(shape) -> str:
    """形状的填充、渐变、描边、圆角、投影摘要。"""
    sp_pr = shape._element.find(qn("p:spPr"))
    if sp_pr is None:
        return ""
    parts = []
    fill = _color(sp_pr.find(qn("a:solidFill")))
    if fill:
        parts.append(f"fill={fill}")
    grad = sp_pr.find(qn("a:gradFill"))
    if grad is not None:
        stops = " ".join(f"{int(gs.get('pos')) // 1000}%:{_color(gs)}" for gs in grad.iter(qn("a:gs")))
        lin = grad.find(qn("a:lin"))
        kind = f"lin{int(lin.get('ang', 0)) // 60000}°" if lin is not None else "radial"
        parts.append(f"gradient({kind} {stops})")
    ln = sp_pr.find(qn("a:ln"))
    if ln is not None and ln.find(qn("a:noFill")) is None:
        line = _color(ln.find(qn("a:solidFill")))
        if line:
            dash = ln.find(qn("a:prstDash"))
            parts.append(f"line={line}/{int(ln.get('w', '12700')) / 12700:g}pt"
                         + (f" dash={dash.get('val')}" if dash is not None else ""))
    shadow = sp_pr.find(".//" + qn("a:outerShdw"))
    if shadow is not None:
        parts.append(f"shadow(blur={int(shadow.get('blurRad', 0)) / EMU:.3f} "
                     f"dist={int(shadow.get('dist', 0)) / EMU:.3f} "
                     f"dir={int(shadow.get('dir', 0)) // 60000} {_color(shadow)})")
    geom = sp_pr.find(qn("a:prstGeom"))
    if geom is not None and geom.get("prst") not in (None, "rect"):
        adj = geom.find(".//" + qn("a:gd"))
        if geom.get("prst") == "roundRect":
            ratio = int(adj.get("fmla").split()[-1]) / 100000 if adj is not None else 0.16667
            parts.append(f"roundRect(r={min(shape.width, shape.height) / EMU * ratio:.3f})")
        else:
            parts.append(geom.get("prst"))
    return " ".join(parts)


def _run_style(run) -> str:
    """一个文字片段的字样：字号、粗斜体、颜色、西文 / 东亚字体、字距、高亮。"""
    r_pr = run._r.find(qn("a:rPr"))
    if r_pr is None:
        return "(继承)"
    latin, ea = r_pr.find(qn("a:latin")), r_pr.find(qn("a:ea"))
    highlight = r_pr.find(qn("a:highlight"))
    flags = ("b" if r_pr.get("b") == "1" else "") + ("i" if r_pr.get("i") == "1" else "")
    return (f"{int(r_pr.get('sz', '0')) / 100:g}pt{' ' + flags if flags else ''} "
            f"{_color(r_pr.find(qn('a:solidFill'))) or '-'} "
            f"{latin.get('typeface') if latin is not None else '-'}/"
            f"{ea.get('typeface') if ea is not None else '-'}"
            f"{' spc=' + r_pr.get('spc') if r_pr.get('spc') else ''}"
            f"{' highlight=' + _color(highlight) if highlight is not None else ''}")


def _paragraph_marks(paragraph) -> str:
    """段落的对齐、行距与项目符号。"""
    p_pr = paragraph._p.find(qn("a:pPr"))
    if p_pr is None:
        return ""
    parts = []
    if p_pr.get("algn"):
        parts.append(f"algn={p_pr.get('algn')}")
    spacing = p_pr.find(qn("a:lnSpc"))
    if spacing is not None and spacing.find(qn("a:spcPct")) is not None:
        parts.append(f"lnSpc={int(spacing.find(qn('a:spcPct')).get('val')) / 100000:g}")
    bullet = p_pr.find(qn("a:buChar"))
    if bullet is not None:
        parts.append(f"bullet={bullet.get('char')} {_color(p_pr.find(qn('a:buClr')))}")
    return " ".join(parts)


def _theme(prs) -> None:
    """主题里的配色方案与主 / 次字体。"""
    part = prs.slide_master.part.part_related_by(RT.THEME)
    root = part._element if hasattr(part, "_element") else None
    if root is None:
        from lxml import etree
        root = etree.fromstring(part.blob)
    scheme = root.find(".//" + qn("a:clrScheme"))
    if scheme is not None:
        colors = []
        for child in scheme:
            value = child.find(qn("a:srgbClr"))
            system = child.find(qn("a:sysClr"))
            colors.append(f"{child.tag.split('}')[1]}="
                          f"{value.get('val') if value is not None else system.get('lastClr')}")
        print("主题配色：", " ".join(colors))
    for tag in ("majorFont", "minorFont"):
        font = root.find(".//" + qn(f"a:{tag}"))
        if font is not None:
            print(f"主题字体 {tag}：latin={font.find(qn('a:latin')).get('typeface')} "
                  f"ea={font.find(qn('a:ea')).get('typeface')}")


def read(path: Path, slides: set[int] | None) -> None:
    """打印主题、逐页形状清单，并汇总全篇的字体与颜色用量。"""
    prs = Presentation(str(path))
    print(f"画布 {prs.slide_width / EMU:.3f} × {prs.slide_height / EMU:.3f} 英寸，共 {len(prs.slides)} 页")
    _theme(prs)
    fonts, colors = Counter(), Counter()
    for number, slide in enumerate(prs.slides, 1):
        for element in slide._element.iter(qn("a:latin"), qn("a:ea")):
            fonts[element.get("typeface")] += 1
        for element in slide._element.iter(qn("a:srgbClr")):
            colors[element.get("val")] += 1
        if slides and number not in slides:
            continue
        bg = slide._element.find(".//" + qn("p:bgPr"))
        background = ""
        if bg is not None:
            background = "图片" if bg.find(".//" + qn("a:blip")) is not None \
                else _color(bg.find(qn("a:solidFill")))
        print(f"\n== 第 {number} 页  背景 {background or '继承'}")
        for shape in slide.shapes:
            box = (f"({shape.left / EMU:.3f}, {shape.top / EMU:.3f}) "
                   f"{shape.width / EMU:.3f}×{shape.height / EMU:.3f}")
            print(f"  #{shape.shape_id:<4} {str(shape.shape_type):<16} {box:<32} {_shape_style(shape)}")
            if not (shape.has_text_frame and shape.text_frame.text.strip()):
                continue
            frame = shape.text_frame
            anchor = frame._txBody.find(qn("a:bodyPr")).get("anchor")
            if anchor:
                print(f"        anchor={anchor}")
            for paragraph in frame.paragraphs:
                marks = _paragraph_marks(paragraph)
                for run in paragraph.runs:
                    if run.text.strip():
                        print(f"        「{run.text[:40]}」 {_run_style(run)} {marks}".rstrip())
    print("\n字体用量：", ", ".join(f"{k}×{v}" for k, v in fonts.most_common(12)))
    print("颜色用量：", ", ".join(f"{k}×{v}" for k, v in colors.most_common(30)))


def main() -> int:
    parser = argparse.ArgumentParser(description="列出 pptx 的设计元数据")
    parser.add_argument("pptx", type=Path)
    parser.add_argument("--slides", default="", help="只列这些页，逗号分隔")
    args = parser.parse_args()
    wanted = {int(x) for x in args.slides.split(",") if x.strip()} or None
    read(args.pptx, wanted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
