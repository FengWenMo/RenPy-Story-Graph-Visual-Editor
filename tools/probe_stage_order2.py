"""
复核 O2：相邻舞台指令的相对顺序 —— **过表之后变了**。

`tools/hell_nest_inline.py --only stage_with_adjacent` 抓到的现象：

    源码 / 直接渲染                 过一遍 Excel 表之后
    show ling happy at left        show ling happy at left
    show ling angry at right with  hide ling with fade        ← 跑到前面了
    hide ling with fade            show ling angry at right with

**收尾状态不一样**：原文最后 ling 是"被 hide 掉"（fade 淡出、不在画面上），
产物最后是"ling 显示在 right" —— 玩家看到的画面不同。这正是 `StageOrder` /
`OpOrder` 两个隐藏列**存在**的理由（"顺序变了演出就变了"）。

这个探针把两边的 `stage` / `stage_order` / `op_order` / `ordered` 摊开，
好定位是"出口没写全"还是"入口读错了"。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

SRC = '''label t:
    show ling happy at left
    show ling angry at right with dissolve
    hide ling with fade
'''


def dump(tag: str, beats: list) -> None:
    print(f'--- {tag}（{len(beats)} 拍）')
    for b in beats:
        print(f'    kind={b.kind!r} stage={b.stage!r}')
        print(f'        show={b.show!r} at={b.at!r} with={b.with_!r} '
              f'hide={b.hide!r}')
        print(f'        stage_order={b.stage_order!r} op_order={b.op_order!r} '
              f'ordered={getattr(b, "ordered", None)!r}')


SHAPES = {
    'show_show_hide': '''label t:
    show ling happy at left
    show ling angry at right with dissolve
    hide ling with fade
''',
    'show_hide': '''label t:
    show ling happy at left
    hide ling with fade
''',
    'hide_show': '''label t:
    hide ling
    show ling happy at left
''',
    'scene_show': '''label t:
    scene bg room
    show ling happy at left
''',
    'scene_show_show': '''label t:
    scene bg room
    show ling happy at left
    show jc at right
''',
    'show_show': '''label t:
    show ling happy at left
    show jc at right
''',
    'show_hide_show': '''label t:
    show ling happy
    hide ling
    show ling angry
''',
    'show_then_dialogue': '''label t:
    show ling happy at left
    e "一句话"
    hide ling
''',
}


def _roundtrip(src: str, tmp: Path, name: str) -> tuple:
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / 'ch.rpy'
    f.write_text(src, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    r1 = render_beats_to_rpy(beats)
    xl = d / 'ch.xlsx'
    export_beats_to_excel(beats, xl)
    r2 = render_beats_to_rpy(import_beats_from_excel(xl))
    return r1, r2


def _body(rpy: str) -> list:
    return [ln.strip() for ln in rpy.splitlines()
            if ln.strip() and not ln.startswith('#')
            and ln.strip() not in ('label t:', 'return')]


def survey() -> None:
    tmp = Path(tempfile.mkdtemp(prefix='probe_stage_shapes_'))
    print('\n================ 触发面：哪些形状会翻 ================')
    flipped = 0
    for name, src in SHAPES.items():
        r1, r2 = _roundtrip(src, tmp, name)
        same = r1 == r2
        if not same:
            flipped += 1
        print(f'  [{"同" if same else "翻"}] {name}')
        if not same:
            print(f'        原文/直接渲染 : {_body(r1)}')
            print(f'        过表后        : {_body(r2)}')
    print(f'  → {flipped}/{len(SHAPES)} 个形状翻序')


def main() -> int:
    survey()
    tmp = Path(tempfile.mkdtemp(prefix='probe_stage_order_'))
    f = tmp / 'ch.rpy'
    f.write_text(SRC, encoding='utf-8')

    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    dump('解析出来的拍', beats)
    r1 = render_beats_to_rpy(beats)
    print('--- 直接渲染')
    print('\n'.join('    ' + ln for ln in r1.splitlines() if ln.strip()))

    xl = tmp / 'ch.xlsx'
    export_beats_to_excel(beats, xl)
    back = import_beats_from_excel(xl)
    dump('过表读回来的拍', back)
    r2 = render_beats_to_rpy(back)
    print('--- 过表后的产物')
    print('\n'.join('    ' + ln for ln in r2.splitlines() if ln.strip()))

    print('\n--- 结论')
    print(f'    直接渲染 == 过表渲染 ? {r1 == r2}')
    print(f'    原文顺序 : show, show, hide')

    # 字段看着一样、产物却不同 → 差异一定在**没打印出来的属性**上。
    # 把两边的完整属性字典摊开（含 dataclass 字段 + 运行期动态属性）。
    print('\n--- 两边拍的全部属性差异')
    import dataclasses
    a, b2 = beats[0], back[0]
    keys = set(a.__dict__) | set(b2.__dict__)
    diffs = []
    for k in sorted(keys):
        va = getattr(a, k, '<缺>')
        vb = getattr(b2, k, '<缺>')
        if va != vb:
            diffs.append((k, va, vb))
    for k, va, vb in diffs:
        print(f'    {k}: {va!r}  →  {vb!r}')
    if not diffs:
        print('    （没有差异！那就是渲染器本身不确定 / 依赖外部状态）')
        print(f'    dataclass 字段：{[f.name for f in dataclasses.fields(a)]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
