#!/usr/bin/env python
"""探针：独立 `with X` 过表之后到底丢在哪一层（缺口 4 定位）。"""
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

SRC = ('label t:\n'
       '    show ling happy\n'
       '    hide jc\n'
       '    with dissolve\n'
       '    return\n')


def dump(tag, beats):
    print(f'--- {tag}')
    for b in beats:
        print(f'  raw={b.raw!r} code={b.code!r}')
        print(f'  ordered={getattr(b, "ordered", None)!r}')
        print(f'  stage_order={b.stage_order!r} op_order={b.op_order!r}')
        print(f'  with_seq_items={b.with_seq_items()!r}')
        print(f'  raw_without={b.raw_without_seq_with()!r}')
        print(f'  op_tokens_full={b.op_tokens_full()!r}')
    print('  → 产物：', [ln.strip() for ln in render_beats_to_rpy(beats).splitlines()
                          if ln.strip() and not ln.startswith('#')])


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='probe_with_seq_'))
    f = tmp / 'ch.rpy'
    f.write_text(SRC, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    dump('解析', beats)
    xl = tmp / 'ch.xlsx'
    export_beats_to_excel(beats, xl)
    back = import_beats_from_excel(xl)
    dump('过表', back)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
