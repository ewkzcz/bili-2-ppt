#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「点一下出一个元素」的入场动画注入 pptx。

python-pptx 没有动画 API——动画只存在于幻灯片 XML 的 <p:timing> 节点里，
所以这一步不走库，直接改包：解压 pptx，给每张幻灯片写一段 timing，再压回去。

调用方（建 deck 的脚本）用形状名声明动画顺序，本脚本读完就把名字洗掉：

    anim-1            第 1 次点击时出现，默认淡入
    anim-1:fade       同上，显式指定效果
    anim-2:wipe       第 2 次点击时出现，擦除
    anim-2:appear     第 2 次点击时出现，直接闪现

同一个组号写几个形状，它们就在同一次点击里一起出现（先出现的那个是 clickEffect，
其余是 withEffect）。分组号必须是 1..N 连续，否则报错。

洗名字是必须的：形状名会出现在 PowerPoint 的选择窗格里，带着 anim-1 这种标记
等于把内部处理痕迹留给了读者。

用法：
    python3 inject_animations.py deck.pptx
    python3 inject_animations.py deck.pptx --out deck-anim.pptx \\
        --effect fade --duration 500
    python3 inject_animations.py deck.pptx --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

from lxml import etree


P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
NSMAP = {"p": P_NS, "a": A_NS}

SLIDE_NUM_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$", re.IGNORECASE)
ANIM_NAME_RE = re.compile(r"^anim-(\d+)(?::([a-z]+))?$", re.IGNORECASE)

# 效果表。只收这三种：学习笔记 deck 要的是「点一下多一块」，不是特效表演。
# presetID / presetClass / presetSubtype 三个值必须和 PowerPoint 自己写出来的一致，
# 否则 WPS 和 PowerPoint 会认不出来，动画在放映时静默失效。
EFFECTS = {
    # 直接出现：只有可见性切换，没有过渡
    "appear": {"preset_id": 1, "subtype": 0, "filter": None},
    # 淡入：最稳的默认值，两个编辑器都支持得最好
    "fade": {"preset_id": 10, "subtype": 0, "filter": "fade"},
    # 从左擦除：适合「流程往右推进」这类画面
    "wipe": {"preset_id": 22, "subtype": 4, "filter": "wipe(left)"},
}

DEFAULT_EFFECT = "fade"
DEFAULT_DURATION_MS = 500


def qname(prefix: str, tag: str) -> str:
    """拼出带命名空间的标签名。"""
    return f"{{{NSMAP[prefix]}}}{tag}"


def el(prefix: str, tag: str, **attrs: str):
    """建一个元素，属性按传入顺序写出去。"""
    node = etree.Element(qname(prefix, tag))
    for key, value in attrs.items():
        node.set(key, str(value))
    return node


class IdGen:
    """timing 里每个 cTn 都要有唯一 id，从 1 开始发号。"""

    def __init__(self, start: int = 1) -> None:
        self._next = start

    def take(self) -> int:
        value = self._next
        self._next += 1
        return value


def collect_animations(slide_root, default_effect: str = DEFAULT_EFFECT) -> list[dict]:
    """读出这张幻灯片上声明了动画的形状，按组号排序。

    返回 [{group, effect, spid, name}]，同名同组即同一次点击。
    """
    found: list[dict] = []
    for c_nv_pr in slide_root.iter(qname("p", "cNvPr")):
        name = (c_nv_pr.get("name") or "").strip()
        match = ANIM_NAME_RE.match(name)
        if not match:
            continue
        group = int(match.group(1))
        effect = (match.group(2) or default_effect).lower()
        if effect not in EFFECTS:
            raise ValueError(
                f"形状 {name!r} 用了未知效果 {effect!r}，可选：{', '.join(EFFECTS)}"
            )
        shape_id = c_nv_pr.get("id")
        if not shape_id:
            raise ValueError(f"形状 {name!r} 没有 id，无法定位动画目标")
        found.append(
            {"group": group, "effect": effect, "spid": int(shape_id), "name": name}
        )

    found.sort(key=lambda item: (item["group"], item["spid"]))

    groups = [item["group"] for item in found]
    expected = list(range(1, len(set(groups)) + 1))
    if sorted(set(groups)) != expected:
        raise ValueError(
            f"动画组号必须是 1..{len(expected)} 的连续整数，实际是 {sorted(set(groups))}"
        )
    return found


def build_effect_node(
    ids: IdGen, spid: int, effect: str, duration_ms: int, *, primary: bool
):
    """给一个形状建入场效果节点。

    primary=True 表示它是这次点击里第一个动的元素，触发方式是 clickEffect；
    同一组里其余的用 withEffect，跟着第一个一起动。
    """
    spec = EFFECTS[effect]
    outer = el("p", "cTn")
    outer.set(
        "id",
        str(ids.take()),
    )
    # 这三个属性是 PowerPoint 识别预设效果的依据，缺一个就退化成「自定义动画」
    outer.set("presetID", str(spec["preset_id"]))
    outer.set("presetClass", "entr")
    outer.set("presetSubtype", str(spec["subtype"]))
    outer.set("fill", "hold")
    outer.set("grpId", "0")
    outer.set("nodeType", "clickEffect" if primary else "withEffect")

    cond_list = el("p", "stCondLst")
    cond = el("p", "cond")
    cond.set("delay", "0")
    cond_list.append(cond)
    outer.append(cond_list)

    children = el("p", "childTnLst")

    # 第一段：把可见性切到 visible。appear 效果只靠这一段，没有过渡。
    visibility = el("p", "set")
    behavior = el("p", "cBhvr")
    inner_tn = el("p", "cTn")
    inner_tn.set("id", str(ids.take()))
    inner_tn.set("dur", "1")
    inner_tn.set("fill", "hold")
    inner_cond_list = el("p", "stCondLst")
    inner_cond = el("p", "cond")
    inner_cond.set("delay", "0")
    inner_cond_list.append(inner_cond)
    inner_tn.append(inner_cond_list)
    behavior.append(inner_tn)

    target = el("p", "tgtEl")
    sp_target = el("p", "spTgt")
    sp_target.set("spid", str(spid))
    target.append(sp_target)
    behavior.append(target)

    attr_names = el("p", "attrNameLst")
    attr_name = el("p", "attrName")
    attr_name.text = "style.visibility"
    attr_names.append(attr_name)
    behavior.append(attr_names)

    visibility.append(behavior)
    to_node = el("p", "to")
    str_val = el("p", "strVal")
    str_val.set("val", "visible")
    to_node.append(str_val)
    visibility.append(to_node)
    children.append(visibility)

    # 第二段：过渡效果。appear 没有这一段。
    if spec["filter"]:
        anim_effect = el("p", "animEffect")
        anim_effect.set("transition", "in")
        anim_effect.set("filter", spec["filter"])
        effect_behavior = el("p", "cBhvr")
        effect_tn = el("p", "cTn")
        effect_tn.set("id", str(ids.take()))
        effect_tn.set("dur", str(duration_ms))
        effect_behavior.append(effect_tn)
        effect_target = el("p", "tgtEl")
        effect_sp_target = el("p", "spTgt")
        effect_sp_target.set("spid", str(spid))
        effect_target.append(effect_sp_target)
        effect_behavior.append(effect_target)
        anim_effect.append(effect_behavior)
        children.append(anim_effect)

    outer.append(children)

    wrapper = el("p", "par")
    wrapper.append(outer)
    return wrapper


def build_timing(animations: list[dict], duration_ms: int):
    """按组号把动画拼成一棵 <p:timing>。

    层级是 PowerPoint 固定的四层：tmRoot → mainSeq → 每次点击一个 par → 该次点击的元素。
    少一层或者顺序不对，动画会在放映时被忽略。
    """
    ids = IdGen()

    timing = el("p", "timing")
    tn_list = el("p", "tnLst")
    timing.append(tn_list)

    root_par = el("p", "par")
    root_tn = el("p", "cTn")
    root_tn.set("id", str(ids.take()))
    root_tn.set("dur", "indefinite")
    root_tn.set("restart", "never")
    root_tn.set("nodeType", "tmRoot")
    root_children = el("p", "childTnLst")
    root_tn.append(root_children)
    root_par.append(root_tn)
    tn_list.append(root_par)

    sequence = el("p", "seq")
    sequence.set("concurrent", "1")
    sequence.set("nextAc", "seek")

    seq_tn = el("p", "cTn")
    seq_tn.set("id", str(ids.take()))
    seq_tn.set("dur", "indefinite")
    seq_tn.set("nodeType", "mainSeq")
    seq_children = el("p", "childTnLst")
    sequence.append(seq_tn)

    # 按组号聚拢，一组 = 一次点击
    by_group: dict[int, list[dict]] = {}
    for item in animations:
        by_group.setdefault(item["group"], []).append(item)

    for group in sorted(by_group):
        members = by_group[group]

        click_par = el("p", "par")
        click_tn = el("p", "cTn")
        click_tn.set("id", str(ids.take()))
        click_tn.set("fill", "hold")
        # indefinite 表示「等下一次点击」，这是点击触发的关键
        click_cond_list = el("p", "stCondLst")
        click_cond = el("p", "cond")
        click_cond.set("delay", "indefinite")
        click_cond_list.append(click_cond)
        click_tn.append(click_cond_list)

        click_children = el("p", "childTnLst")
        inner_par = el("p", "par")
        inner_tn = el("p", "cTn")
        inner_tn.set("id", str(ids.take()))
        inner_tn.set("fill", "hold")
        inner_cond_list = el("p", "stCondLst")
        inner_cond = el("p", "cond")
        inner_cond.set("delay", "0")
        inner_cond_list.append(inner_cond)
        inner_tn.append(inner_cond_list)

        inner_children = el("p", "childTnLst")
        for index, item in enumerate(members):
            inner_children.append(
                build_effect_node(
                    ids,
                    item["spid"],
                    item["effect"],
                    duration_ms,
                    primary=(index == 0),
                )
            )
        inner_tn.append(inner_children)
        inner_par.append(inner_tn)
        click_children.append(inner_par)

        click_tn.append(click_children)
        click_par.append(click_tn)
        seq_children.append(click_par)

    # 上一页/下一页条件：没有这两块，放映时翻页会跳过整个动画序列
    prev_list = el("p", "prevCondLst")
    prev_cond = el("p", "cond")
    prev_cond.set("evt", "onPrev")
    prev_cond.set("delay", "0")
    prev_target = el("p", "tgtEl")
    prev_target.append(el("p", "sldTgt"))
    prev_cond.append(prev_target)
    prev_list.append(prev_cond)
    sequence.append(prev_list)

    next_list = el("p", "nextCondLst")
    next_cond = el("p", "cond")
    next_cond.set("evt", "onNext")
    next_cond.set("delay", "0")
    next_target = el("p", "tgtEl")
    next_target.append(el("p", "sldTgt"))
    next_cond.append(next_target)
    next_list.append(next_cond)
    sequence.append(next_list)

    # p:seq 的子元素顺序同样是定死的：cTn → prevCondLst → nextCondLst。
    # childTnLst 必须挂在 cTn 里面，这里最后补挂，保证它在 prevCondLst 之前的那一层。
    seq_tn.append(seq_children)

    root_children.append(sequence)
    return timing


def scrub_names(slide_root) -> int:
    """洗掉形状名里的 anim-N 标记，改成不承载任何信息的普通名字。"""
    scrubbed = 0
    for c_nv_pr in slide_root.iter(qname("p", "cNvPr")):
        name = (c_nv_pr.get("name") or "").strip()
        if ANIM_NAME_RE.match(name):
            c_nv_pr.set("name", f"Shape {c_nv_pr.get('id')}")
            scrubbed += 1
    return scrubbed


def inject_into_slide(
    slide_root, duration_ms: int, default_effect: str = DEFAULT_EFFECT
) -> tuple[int, int]:
    """给一张幻灯片注入 timing，返回 (动画形状数, 点击次数)。

    已经存在 <p:timing> 时直接替换，保证脚本可以反复跑。
    """
    animations = collect_animations(slide_root, default_effect)
    if not animations:
        return 0, 0

    # p:sld 的子元素顺序是 schema 定死的：cSld → clrMapOvr → transition → timing → extLst。
    # 插错位置 PowerPoint 会直接判定文件损坏。
    existing = slide_root.find(qname("p", "timing"))
    if existing is not None:
        slide_root.remove(existing)

    timing = build_timing(animations, duration_ms)

    ext_list = slide_root.find(qname("p", "extLst"))
    if ext_list is not None:
        ext_list.addprevious(timing)
    else:
        slide_root.append(timing)

    scrub_names(slide_root)

    click_count = len({item["group"] for item in animations})
    return len(animations), click_count


def process(
    pptx_path: Path,
    out_path: Path,
    duration_ms: int,
    default_effect: str = DEFAULT_EFFECT,
) -> dict:
    """解包 → 逐张幻灯片注入 → 重新打包。"""
    report: dict = {"slides": [], "animated_shapes": 0, "clicks": 0}

    with zipfile.ZipFile(pptx_path) as source:
        names = source.namelist()
        payload = {name: source.read(name) for name in names}
        infos = {info.filename: info for info in source.infolist()}

    slide_names = sorted(
        (name for name in names if SLIDE_NUM_RE.match(name)),
        key=lambda name: int(SLIDE_NUM_RE.match(name).group(1)),
    )
    if not slide_names:
        raise SystemExit(f"{pptx_path} 里没有找到幻灯片，这份文件可能不是 pptx")

    for name in slide_names:
        slide_number = int(SLIDE_NUM_RE.match(name).group(1))
        parser = etree.XMLParser(remove_blank_text=False)
        root = etree.fromstring(payload[name], parser)

        shapes, clicks = inject_into_slide(root, duration_ms, default_effect)
        if shapes:
            payload[name] = etree.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )
            report["slides"].append(
                {"slide": slide_number, "shapes": shapes, "clicks": clicks}
            )
            report["animated_shapes"] += shapes
            report["clicks"] += clicks

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as target:
        for name in names:
            info = infos[name]
            # 保留原压缩方式，避免把已压缩的媒体文件再压一遍
            target.writestr(info, payload[name])

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把声明在形状名里的入场动画注入 pptx",
    )
    parser.add_argument("pptx", type=Path, help="输入 pptx")
    parser.add_argument("--out", type=Path, default=None, help="输出 pptx，默认覆盖输入")
    parser.add_argument(
        "--effect",
        default=DEFAULT_EFFECT,
        choices=sorted(EFFECTS),
        help=f"形状名没写效果时用的默认效果（默认 {DEFAULT_EFFECT}）",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION_MS,
        help=f"过渡时长，毫秒（默认 {DEFAULT_DURATION_MS}）",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只报告会注入什么，不写文件"
    )
    args = parser.parse_args()

    if not args.pptx.is_file():
        raise SystemExit(f"找不到文件：{args.pptx}")

    if args.dry_run:
        with zipfile.ZipFile(args.pptx) as source:
            names = [n for n in source.namelist() if SLIDE_NUM_RE.match(n)]
            total = 0
            for name in sorted(names, key=lambda n: int(SLIDE_NUM_RE.match(n).group(1))):
                root = etree.fromstring(source.read(name))
                animations = collect_animations(root, args.effect)
                if animations:
                    groups = sorted({item["group"] for item in animations})
                    print(
                        f"{name}: {len(animations)} 个形状，"
                        f"{len(groups)} 次点击 → {groups}"
                    )
                    total += len(animations)
            print(f"合计 {total} 个动画形状")
        return 0

    out_path = args.out or args.pptx
    if out_path.resolve() == args.pptx.resolve():
        # 原地改写时先备份，注入中途失败不至于把原文件毁掉
        backup = args.pptx.with_suffix(".pptx.bak")
        shutil.copy2(args.pptx, backup)
        try:
            report = process(args.pptx, out_path, args.duration, args.effect)
        except Exception:
            shutil.copy2(backup, args.pptx)
            backup.unlink(missing_ok=True)
            raise
        backup.unlink(missing_ok=True)
    else:
        report = process(args.pptx, out_path, args.duration, args.effect)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["animated_shapes"] == 0:
        print(
            "没有找到任何动画形状——建 deck 时把要分步出现的形状命名为 anim-1、anim-2…",
            file=sys.stderr,
        )
        return 1
    print(f"已写入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
