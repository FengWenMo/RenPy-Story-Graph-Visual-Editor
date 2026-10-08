#!/usr/bin/env python
"""探针：独立 `with` 语句在拍里被记成了什么（缺口 4 第八轮定位）。"""
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
       '    e "一句"\n'
       '    with Dissolve(0.5)\n'
       '    e "两句"\n'
       '    return\n')


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='probe_bare_with_'))
    f = tmp / 'ch.rpy'
    f.write_text(SRC, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    for s in pr.statements:
        print(f'语句 kind={s.kind!r} payload={s.payload!r}')
    beats = beats_from_parse_result(pr)
    for i, b in enumerate(beats):
        print(f'拍 {i}: text={b.text!r} show={b.show!r} with_={b.with_!r}')
        print(f'    raw={b.raw!r} code={b.code!r}')
        print(f'    ordered={getattr(b, "ordered", None)!r}')
        print(f'    stage_order={b.stage_order!r} op_order={b.op_order!r}')
        print(f'    stage={b.stage!r} code_blocks={b.code_blocks!r}')
        print(f'    plan={b.unified_plan()!r}')
    print('单拍渲染：')
    from storygraph.story.beat_render import render_beat_to_rpy
    print(render_beat_to_rpy(beats[1], indent='    '))
    print('整份渲染：')
    print(render_beats_to_rpy(beats))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
