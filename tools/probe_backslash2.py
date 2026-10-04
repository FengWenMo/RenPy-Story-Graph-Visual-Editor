#!/usr/bin/env python
"""字符级定位：反斜杠台词在每一环到底是什么字节。"""
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

SRC = 'label a:\n    e "路径 C:\\new\\test\\x"\n'   # 字面：\ n e w \ t e s t \ x


def chars(s):
    return ' '.join(f'{c}:{ord(c)}' for c in s if not c.isalpha())


def main() -> int:
    line = SRC.splitlines()[1]
    print('[src] ', repr(line))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        f = tmp / 'h.rpy'
        f.write_text(SRC, encoding='utf-8')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        st = [s for s in pr.statements if s.kind == 'say'][0]
        t0 = st.payload.get('text')
        print('[parse]', repr(t0))

        beats = beats_from_parse_result(pr)
        b = beats[0]
        print('[beat ]', repr(getattr(b, 'text', None)),
              '| speaker=', repr(getattr(b, 'speaker', None)))
        _row = b.to_row()                       # ← 对照：这一步有没有副作用
        print('[row  ]', repr(_row[6]), '| beat.text 现在=',
              repr(b.text))

        xl = tmp / 'h.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)
        print('[xlsx ]', repr(getattr(back[0], 'text', None)))

        out = compile_beats(back).rpy_text
        print('[rpy  ]', repr(out))
        print('[逐行 ]')
        for ln in out.splitlines():
            print('        ', repr(ln))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
