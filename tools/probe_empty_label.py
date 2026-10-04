#!/usr/bin/env python
"""空 label / 只有 return 的 label：解析层到底给了什么？"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result

CASES = {
    'empty_label': 'label a:\n',
    'return_only': 'label a:\n    return\n',
    'say': 'label a:\n    e "x"\n',
    'two_labels': 'label a:\n    return\nlabel b:\n    e "x"\n',
}


def main() -> int:
    for name, src in CASES.items():
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'h.rpy'
            f.write_text(src, encoding='utf-8')
            pr, _g = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
            sts = [s.kind for s in pr.statements]
            bs = beats_from_parse_result(pr)
            print(f'{name:12} stmts={sts}')
            print(f'{"":12} beats={len(bs)} '
                  f'{[(b.label, b.kind, repr(b.text)) for b in bs]}')
            print(f'{"":12} pr.labels={pr.labels}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
