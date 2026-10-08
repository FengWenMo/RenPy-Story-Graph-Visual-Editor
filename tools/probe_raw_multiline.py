#!/usr/bin/env python
"""探针：多行 raw 语句（续行）的缩进丢在哪一层。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy

SRC = ('label a:\n'
       '    show ling happy\n'
       '    show screen prompt("再来", act=[\n'
       '        Call("again"),\n'
       '    ])\n'
       '    hide ling\n'
       '    return\n')


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='probe_raw_multi_'))
    f = tmp / 'ch.rpy'
    f.write_text(SRC, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    for s in pr.statements:
        print(f'语句 kind={s.kind!r} payload={s.payload!r}')
    beats = beats_from_parse_result(pr)
    for i, b in enumerate(beats):
        print(f'拍 {i}: raw={b.raw!r}')
        print(f'    ordered={getattr(b, "ordered", None)!r}')
        print(f'    op_order={b.op_order!r}')
        print(f'    entries={b.code_lines_with_kinds()!r}')
        print(f'    plan={b.unified_plan()!r}')
    print(render_beats_to_rpy(beats))
    print('--- 过表 ---')
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel
    xl = tmp / 'ch.xlsx'
    export_beats_to_excel(beats, xl)
    back = import_beats_from_excel(xl)
    for i, b in enumerate(back):
        print(f'拍 {i}: raw={b.raw!r} hide={b.hide!r} show={b.show!r}')
        print(f'    stage_order={b.stage_order!r} op_order={b.op_order!r}')
        print(f'    entries={b.code_lines_with_kinds()!r}')
        print(f'    plan={b.unified_plan()!r}')
    print(render_beats_to_rpy(back))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
