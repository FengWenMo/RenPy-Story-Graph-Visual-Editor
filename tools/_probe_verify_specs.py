# -*- coding: utf-8 -*-
"""
验 `verify_show_specs`：**故意塞几条错序的**，看引擎怎么说。

小项目里只声明 `attribute happy` / `clothes2`，资源用 8×8 的假图。
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')
from storygraph.assets.engine_images import verify_show_specs      # noqa: E402

SDK = r'D:\software\renpy-8.4.1-sdk'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64

DECL = """\
layeredimage lan:
    image_format 'images/characters/lan/{image}.png'
    group outfit:
        attribute clothes2:
            'clothes2'
    group expressions:
        attribute happy default:
            'happy'
        attribute angry:
            'angry'

transform left:
    xalign 0.0

transform dissolve_out:
    alpha 0.0
"""

CASES = [
    # —— 应该**能跑**的 ——
    'lan happy',
    'lan clothes2 happy',
    'lan happy clothes2',                       # 顺序反，但**语义等价**
    'lan happy at left',
    'lan clothes2 happy at left with dissolve',
    'lan happy@left',
    # —— **真的跑不了**的（属性跑到 with 后面 / 语法乱）——
    'lan happy with dissolve clothes2',
    'lan happy at left with dissolve angry',
    'lan happy at',
]


def main() -> int:
    td = Path(tempfile.mkdtemp(prefix='sg_vs_'))
    try:
        root = td / 'tiny'
        g = root / 'game'
        d = g / 'images' / 'characters' / 'lan'
        d.mkdir(parents=True)
        for n in ('happy', 'angry', 'clothes2'):
            (d / f'{n}.png').write_bytes(PNG)
        (g / 'script.rpy').write_text(
            DECL + '\nlabel start:\n    return\n', encoding='utf-8')

        got = verify_show_specs(str(root), CASES, sdk=SDK, timeout=600)
        print(f"ok={got['ok']} 条数={got.get('count')} reason={got['reason']!r}")
        print(f"\n**跑不了**（{len(got['fatal'])} 条）:")
        for row in got['fatal']:
            print(f"  [{row['line']}] {row['spec']:42} {row['why'][:90]}")
        print(f"\n名字不存在（{len(got['unknown_image'])} 条）:")
        for row in got['unknown_image']:
            print(f"  [{row['line']}] {row['spec']:42} {row['why'][:90]}")
        bad = {r['spec'] for r in got['fatal']}
        expect_good = set(CASES[:6])
        expect_bad = set(CASES[6:])
        print(f"\n合法的那 6 条里被误判的: {sorted(bad & expect_good) or '无 ✓'}")
        print(f"错序的 3 条里抓到了: {sorted(bad & expect_bad) or '一条都没抓到 ✗'}")
        return 0
    finally:
        shutil.rmtree(td, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
