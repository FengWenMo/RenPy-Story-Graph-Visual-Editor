"""目录级归类审计 —— "这个项目的图，工具到底把它们分成了什么"。

    python tools/probe_asset_categories.py D:\\千连万理\\sr_general
    python tools/probe_asset_categories.py <项目根> --files   # 逐文件

为什么要它（2026-11-02 用户："你再手动过一遍真实项目，看看它到底都分错了哪些"）：
归类规则是**启发式**，真实项目的目录名又乱七八糟，光看代码看不出结论 ——
必须把"每个目录被判成什么"摊开，人一眼扫过去才知道哪里错了。本工具只读、
只打印，不改任何表。

输出（默认按目录汇总）：

    game/images/nbg      n=42   background:42    tag='bg':42

`--files` 则逐文件列一行（找"同一目录里就它一个不对劲"时用）。
"""
from __future__ import annotations

import argparse
import os
from collections import Counter, defaultdict
from pathlib import Path

from storygraph.assets.asset_registry import (_detect_category,
                                              _detect_image_tag)
from storygraph.assets.asset_registry_constants import (_SKIP_SCAN_DIRS,
                                                       SUPPORTED_IMAGE_EXT)


def _scan_root(root: Path) -> Path:
    """项目根 → 真正放图的那一层（`<root>/game`，没有就用根目录）。"""
    game = root / 'game'
    return game if game.is_dir() else root


def audit(root: Path, *, per_file: bool = False) -> int:
    base = _scan_root(root)
    if not base.is_dir():
        print(f'没有这个目录：{base}')
        return 2
    buckets = defaultdict(lambda: {'n': 0, 'cat': Counter(),
                                   'tags': Counter()})
    totals: Counter = Counter()
    for cur, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d.lower() not in _SKIP_SCAN_DIRS]
        for name in sorted(files):
            p = Path(cur) / name
            if p.suffix.lower() not in SUPPORTED_IMAGE_EXT:
                continue
            rel = p.relative_to(root.parent)
            cat = _detect_category(rel)
            tag = _detect_image_tag(p, rel, cat)
            bucket = str(rel.parent).replace('\\', '/')
            info = buckets[bucket]
            info['n'] += 1
            info['cat'][cat] += 1
            info['tags'][tag] += 1
            totals[cat] += 1
            if per_file:
                print('%-46s %-11s %r' % (rel.as_posix(), cat, tag))
    if per_file:
        print()
        print('合计:', dict(totals))
        return 0
    for key in sorted(buckets):
        info = buckets[key]
        cats = ', '.join('%s:%d' % kv for kv in info['cat'].most_common())
        tags = ', '.join('%r:%d' % kv for kv in info['tags'].most_common(4))
        print('%-44s n=%-4d %-32s tag=%s' % (key, info['n'], cats, tags))
    print()
    print('目录 %d 个 / 图片 %d 张' % (len(buckets), sum(totals.values())))
    print('合计:', dict(totals))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='按目录审计图片归类（只读）')
    ap.add_argument('project_root', help='Ren 项目根（里面有 game/）')
    ap.add_argument('--files', action='store_true', help='逐文件打印')
    args = ap.parse_args(argv)
    return audit(Path(args.project_root), per_file=args.files)


if __name__ == '__main__':
    raise SystemExit(main())
