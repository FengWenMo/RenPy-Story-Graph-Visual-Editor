"""
拍内顺序差异的**最小取证**：同一个 Beat（`hide ye` + `show … behind ye` +
`$ persistent…`）在"解析来的"与"过表来的"两种身份下，渲染出的行序不一样。

为什么单独一个探针：`render_beat_to_rpy` 的顺序由
`beat.order_tokens` + `_stage_native_show`（原生真迹的影子）共同决定，
后者是**运行时属性**（不入表）。这一支把两者的取值都打出来，
避免下一次又靠猜。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
NEEDLE = 'cg02_2'


def _beats(path: Path):
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    return beats_from_parse_result(pr)


def _hit(b) -> bool:
    blob = ' '.join(str(getattr(b, f, '') or '') for f in
                    ('show', 'hide', 'stage', 'raw', 'code'))
    return NEEDLE in blob


def _inspect(tag: str, b) -> None:
    from storygraph.story.beat_model import show_item_part, hide_item_part
    from storygraph.story.beat_render import (_stage_lines,
                                              render_beat_to_rpy)
    from storygraph.story.beat_model import (_split_hide_items,
                                             _split_show_items)
    print(f'  {tag}:')
    print(f'      _from_script          = {getattr(b, "_from_script", None)!r}')
    print(f'      stage                 = {b.stage!r}')
    print(f'      show                  = {b.show!r}')
    print(f'      hide                  = {b.hide!r}')
    print(f'      order_tokens          = {b.order_tokens!r}')
    print(f'      stage_order / op_order= {b.stage_order!r} / {b.op_order!r}')
    print(f'      _stage_native_show    = '
          f'{getattr(b, "_stage_native_show", None)!r}')
    print(f'      _stage_native_hide    = '
          f'{getattr(b, "_stage_native_hide", None)!r}')
    print(f'      _stage_native_scene   = '
          f'{getattr(b, "_stage_native_scene", None)!r}')
    items = _split_show_items(b.show)
    print(f'      split_show_items      = {items!r}')
    print(f'      show_item_part(每项)  = '
          f'{[show_item_part(i) for i in items]!r}')
    print(f'      hide_item_part(每项)  = '
          f'{[hide_item_part(i) for i in _split_hide_items(b.hide)]!r}')
    print(f'      _stage_lines()        = {_stage_lines(b, None)!r}')
    print(f'      render_beat_to_rpy    = '
          f'{[ln.strip() for ln in render_beat_to_rpy(b)]!r}')


def main() -> int:
    f = next(REAL.rglob('chapter1_2.rpy'), None)
    if f is None:
        print('(no chapter1_2.rpy)')
        return 0
    import tempfile
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel

    print('=' * 74)
    beats = _beats(f)
    hit = next(i for i, b in enumerate(beats) if _hit(b))
    _inspect(f'R0[{hit}]（解析来的）', beats[hit])
    with tempfile.TemporaryDirectory(prefix='order_') as td:
        xl = Path(td) / 'c.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)
        hit2 = next(i for i, b in enumerate(back) if _hit(b))
        _inspect(f'R2[{hit2}]（过表来的）', back[hit2])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
