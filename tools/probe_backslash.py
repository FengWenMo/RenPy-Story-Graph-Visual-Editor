#!/usr/bin/env python
"""定位：反斜杠台词在「解析 → beats → xlsx → 读回 → 渲染」哪一环被吃掉。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel
from storygraph.pipeline.compile import compile_beats

SRC = 'label a:\n    e "路径 C:\\new\\test\\x"\n'


def main() -> int:
    print('原文台词：', repr(SRC.splitlines()[1]))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        f = tmp / 'h.rpy'
        f.write_text(SRC, encoding='utf-8')
        a = IncrementalRenPyAnalyzer()
        pr, _ = a.analyze(files=[f], graph=None)
        print('\n[0] 解析出的 statement：')
        for st in getattr(pr, 'statements', []) or []:
            d = getattr(st, '__dict__', {})
            print('   ', st.kind, {k: repr(v)[:60] for k, v in d.items()})
        beats = beats_from_parse_result(pr)
        print('\n[1] beats 里的台词：')
        for b in beats:
            for fld in ('Dialogue', 'Speaker', 'Text', 'Content'):
                v = getattr(b, fld, None)
                if v:
                    print(f'    {fld} = {v!r}')
            print('    row[-1]=', repr(list(b.to_row())[-1])[:60] if hasattr(b, 'to_row') else '-')
        xl = tmp / 'h.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)
        print('\n[2] 从 xlsx 读回的台词：')
        for b in back:
            for fld in ('Dialogue', 'Speaker', 'Text', 'Content'):
                v = getattr(b, fld, None)
                if v:
                    print(f'    {fld} = {v!r}')
        out = compile_beats(back).rpy_text
        print('\n[3] 渲染产物：')
        for ln in out.splitlines():
            print('   ', repr(ln))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
