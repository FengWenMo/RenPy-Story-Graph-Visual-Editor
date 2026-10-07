#!/usr/bin/env python
"""临时探针：CST 真迹是解析时那一份，还是挂载时又读了一遍盘？"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.core import cst
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer

SRC = (
    '# 文件头注释\n'
    '\n'
    'label a:\n'
    '    scene bg room\n'
    '\n'
    '    "第一句"\n'
    '    return\n'
)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='cstsnap_'))
    f = tmp / 'a.rpy'
    f.write_text(SRC, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    print('语句数：', len(pr.statements))
    for st in pr.statements:
        sp = cst.span_of(st)
        print(f'  {st.kind:8} src={st.source}  span={sp.to_dict() if sp else None}'
              f'  raw={cst.raw_text_of(st)!r}')
    before = {cst.span_of(st).start: cst.raw_text_of(st) for st in pr.statements}
    f.write_text('label z:\n    "换了一份"\n', encoding='utf-8')
    after = {}
    for st in pr.statements:
        sp = cst.span_of(st)
        after[sp.start if sp else None] = cst.statement_text(st)
    print('before keys:', sorted(before))
    print('after  keys:', sorted(after))
    print('相同？', before == after)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
