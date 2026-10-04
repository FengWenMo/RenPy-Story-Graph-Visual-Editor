"""
真实项目的菜单选项体里，有没有"普通拍与条件拍**交错**"的情况？

`_render_menu_segments` 原本是**两趟**渲染（先把"条件是累积条件"的拍整批
铺平，再渲染"真嵌套 if"的拍）—— 交错时它会**换序**（把 `if` 挪到块体末尾）。
B 阶段二把选项体改成按源码顺序渲染，这条探针量的是：真实项目受影响吗？

实测结论（2026-10-05）：**交错 0 个** —— 所以那个改动不影响任何真实产物
（`tools/render_hash.py` 对 HEAD 逐字节一致），只把畸形/嵌套形态修对。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')


def main() -> int:
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story import beat_render as br
    from storygraph.story.beat_build import beats_from_parse_result

    files = sorted(f for f in (REAL / 'game').rglob('*.rpy')
                   if not f.name.endswith('.generated.rpy'))
    affected = []
    menus = bodies = 0
    for f in files:
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        beats = beats_from_parse_result(pr)
        if not beats:
            continue
        for kind, unit in br.split_beat_units(beats):
            if kind != 'menu':
                continue
            menus += 1
            menu_cond = (unit[0].condition or '').strip()
            for opt, body, _foreign in br._menu_segments(unit):
                if opt is None:
                    continue
                bodies += 1
                expected = br._expected_body_condition(
                    (opt.condition or '').strip(), menu_cond)
                kinds = []
                for _pos, b, why in body:
                    got = (b.condition or '').strip()
                    kinds.append('plain' if (why or not got or got == expected)
                                 else 'nested')
                seen_nested = False
                for k in kinds:
                    if k == 'nested':
                        seen_nested = True
                    elif seen_nested:
                        affected.append((f.name, opt.text, kinds))
                        break
    print(f'真实项目：菜单 {menus} 个 / 选项体 {bodies} 个')
    print(f'**"普通拍与条件拍交错"的选项体：{len(affected)} 个**')
    for name, text, kinds in affected[:8]:
        print(f'  {name} 选项 {text!r}: {kinds}')
    if not affected:
        print('  → 两趟渲染在真实项目上**没有换序**'
              '（所以"用结构列 + 按源码顺序渲染"不影响产物）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
