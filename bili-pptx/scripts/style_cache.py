#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按模版缓存 deck_style.py，同一套模版不用每次重读原件、重写样式表。

缓存放在 <系统临时目录>/bili-2-ppt/styles/<模版名>/，和模版原件（pptx、html）与 deck_kit.py 的
指纹绑定：三者任何一个变了，缓存自动失效，照常重读模版。

    # 读模版之前先试缓存：命中就把 deck_style.py 复制进 $WORK/deck/，跳过「看原件 / 读元数据 / 写样式表」
    python3 style_cache.py restore Cryo_Academic --to $WORK/deck/

    # 没命中：照常读模版、写好 deck_style.py 后存进缓存；建页中改过样式表的，交付前再存一次
    python3 style_cache.py save Cryo_Academic --from $WORK/deck/

退出码：restore 命中 0、没命中 1。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REFERENCES = SCRIPTS.parent / "references"
CACHE_ROOT = Path(tempfile.gettempdir()) / "bili-2-ppt" / "styles"
CACHED = ("deck_style.py",)


def fingerprint(template: str) -> dict[str, str]:
    sources = {
        "pptx": REFERENCES / f"{template}.pptx",
        "html": REFERENCES / f"{template}.html",
        "deck_kit": SCRIPTS / "deck_kit.py",
    }
    missing = [str(path) for path in sources.values() if not path.is_file()]
    if missing:
        raise SystemExit(f"找不到模版文件：{', '.join(missing)}")
    return {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in sources.items()}


def restore(template: str, target: Path, cache_root: Path) -> int:
    cache = cache_root / template
    meta_path = cache / "fingerprint.json"
    if not meta_path.is_file() or not all((cache / name).is_file() for name in CACHED):
        print(f"没有 {template} 的样式缓存，照常读模版")
        return 1
    if json.loads(meta_path.read_text(encoding="utf-8")) != fingerprint(template):
        print(f"{template} 的模版原件或 deck_kit.py 改过，缓存失效，照常读模版")
        return 1
    target.mkdir(parents=True, exist_ok=True)
    for name in CACHED:
        shutil.copy2(cache / name, target / name)
    print(f"命中 {template} 的样式缓存，已复制到 {target}；跳过看原件、读元数据、写样式表")
    return 0


def save(template: str, source: Path, cache_root: Path) -> int:
    missing = [name for name in CACHED if not (source / name).is_file()]
    if missing:
        raise SystemExit(f"{source} 里没有 {', '.join(missing)}，写好样式表再存")
    cache = cache_root / template
    cache.mkdir(parents=True, exist_ok=True)
    for name in CACHED:
        shutil.copy2(source / name, cache / name)
    (cache / "fingerprint.json").write_text(json.dumps(fingerprint(template), indent=2) + "\n", encoding="utf-8")
    print(f"已缓存 {template} 的样式表到 {cache}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按模版缓存 deck_style.py")
    parser.add_argument("action", choices=("restore", "save"))
    parser.add_argument("template", help="模版名，即 references/ 下 pptx 与 html 的文件名")
    parser.add_argument("--to", type=Path, help="restore：复制到哪个目录（一般是 $WORK/deck/）")
    parser.add_argument("--from", dest="source", type=Path, help="save：从哪个目录取 deck_style.py")
    parser.add_argument("--cache-root", type=Path, default=CACHE_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.action == "restore":
        if not args.to:
            parser.error("restore 需要 --to")
        return restore(args.template, args.to, args.cache_root)
    if not args.source:
        parser.error("save 需要 --from")
    return save(args.template, args.source, args.cache_root)


if __name__ == "__main__":
    raise SystemExit(main())
