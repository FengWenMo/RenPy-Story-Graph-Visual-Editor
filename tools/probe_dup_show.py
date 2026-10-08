#!/usr/bin/env python
"""探针：一拍里**两条同名 show** 在哪一层被合掉的（缺口 4 定位用）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy

SRC = 'label t:\n    show ling happy\n    show ling happy\n    return\n'


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='probe_dup_show_'))
    f = tmp / 'ch.rpy'
    f.write_text(SRC, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    print('语句流：')
    for s in pr.statements:
        print(f'  kind={s.kind!r} payload={s.payload!r}')
    beats = beats_from_parse_result(pr)
    print(f'拍 {len(beats)} 张：')
    for b in beats:
        print(f'  show={b.show!r}')
        print(f'  stage_order={b.stage_order!r} op_order={b.op_order!r}')
        print(f'  op_tokens_full={b.op_tokens_full()!r}')
    print('产物：')
    print(render_beats_to_rpy(beats))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
