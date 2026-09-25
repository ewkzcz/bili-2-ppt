#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""识别一页 pptx 用到了哪些「特殊样式」：文字描边、渐变字、半透明字、固定行距、箭头、虚线、表格……

read_pptx.py 用它给模版列出特殊样式清单，validate_deck.py 用它核对 deck 有没有把模版的特殊样式复刻出来。
新增一种要核对的样式，只在 EFFECTS 里加一行。
"""

from __future__ import annotations

from pptx.oxml.ns import qn


def _has_fill(element) -> bool:
    return element is not None and element.find(qn("a:noFill")) is None


def _text_outline(root) -> bool:
    return any(_has_fill(r_pr.find(qn("a:ln"))) for r_pr in root.iter(qn("a:rPr")))


def _text_gradient(root) -> bool:
    return any(r_pr.find(qn("a:gradFill")) is not None for r_pr in root.iter(qn("a:rPr")))


def _text_alpha(root) -> bool:
    for r_pr in root.iter(qn("a:rPr")):
        fill = r_pr.find(qn("a:solidFill"))
        if fill is not None and fill.find(".//" + qn("a:alpha")) is not None:
            return True
    return False


def _shape_fill(tag):
    def check(root) -> bool:
        return any(sp_pr.find(qn(tag)) is not None for sp_pr in root.iter(qn("p:spPr")))
    return check


def _fill_alpha(root) -> bool:
    for sp_pr in root.iter(qn("p:spPr")):
        fill = sp_pr.find(qn("a:solidFill"))
        if fill is not None and fill.find(".//" + qn("a:alpha")) is not None:
            return True
    return False


def _arrow(root) -> bool:
    return any(end.get("type", "none") != "none"
               for tag in ("a:headEnd", "a:tailEnd") for end in root.iter(qn(tag)))


def _dash(root) -> bool:
    return any(dash.get("val") != "solid" for dash in root.iter(qn("a:prstDash")))


def _rotation(root) -> bool:
    return any(xfrm.get("rot") not in (None, "0") for xfrm in root.iter(qn("a:xfrm")))


def _fixed_leading(root) -> bool:
    return any(spacing.find(qn("a:spcPts")) is not None for spacing in root.iter(qn("a:lnSpc")))


def _background_image(root) -> bool:
    bg = root.find(".//" + qn("p:bgPr"))
    return bg is not None and bg.find(".//" + qn("a:blip")) is not None


def _background_gradient(root) -> bool:
    bg = root.find(".//" + qn("p:bgPr"))
    return bg is not None and bg.find(qn("a:gradFill")) is not None


def _any(tag):
    def check(root) -> bool:
        return root.find(".//" + qn(tag)) is not None
    return check


# (名称, 识别函数, 是否为模版视觉签名, deck_kit 里的写法)
# 视觉签名：模版用了、deck 一处都没用，多半是样式表漏抄了，validate_deck.py 报错；
# 其余取决于内容（有没有表格、编号步骤），缺了只提示。
EFFECTS = (
    ("文字描边", _text_outline, True, '文字样式 outline={"color", "pt"}'),
    ("渐变文字", _text_gradient, True, '文字样式 gradient={"angle", "stops"}'),
    ("半透明文字", _text_alpha, True, "文字样式 alpha=不透明度%"),
    ("文字高亮底色", _any("a:highlight"), True, "文字样式 highlight"),
    ("固定行距", _fixed_leading, False, "文字样式 leading=行距 pt"),
    ("形状渐变填充", _shape_fill("a:gradFill"), True, "形状样式 gradient"),
    ("半透明填充", _fill_alpha, True, "形状样式 alpha"),
    ("投影", _any("a:outerShdw"), True, "形状样式 shadow"),
    ("虚线", _dash, True, '形状样式 dash=True 或 "sysDot" 等；line(dash=)'),
    ("旋转", _rotation, True, "形状样式 rotation"),
    ("背景图", _background_image, True, "background(image=)"),
    ("背景渐变", _background_gradient, True, "background(gradient=)"),
    ("箭头", _arrow, False, "line(tail=/head=)"),
    ("编号列表", _any("a:buAutoNum"), False, 'bullets(bullet={"auto": "arabicPeriod"})'),
    ("项目符号", _any("a:buChar"), False, "bullets(bullet=)"),
    ("段后距", _any("a:spcAft"), False, "文字样式 space_after"),
    ("表格", _any("a:tbl"), False, "table()"),
)


def effects_of(root) -> set[str]:
    """一页（或任意 XML 节点）用到的特殊样式名称。"""
    return {name for name, check, _, _ in EFFECTS if check(root)}


def signature(name: str) -> bool:
    return next(sig for effect, _, sig, _ in EFFECTS if effect == name)


def how_to(name: str) -> str:
    return next(way for effect, _, _, way in EFFECTS if effect == name)
