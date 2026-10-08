#!/usr/bin/env python
"""探针：`$ f()` 与 `show screen`/`pause` 的交错（代码通道）在哪一层断的。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_model import _ordered_code_lines
from storygraph.story.beat_render import render_beats_to_rpy

SRC = ('label a:\n'
       '    $ msg_add("hello")\n'
       '    show screen msg\n'
       '    pause\n'
       '    hide screen msg\n'
       '    $ msg_add("world")\n'
       '    return\n')


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='probe_interleave_'))
    f = tmp / 'ch.rpy'
    f.write_text(SRC, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    for i, b in enumerate(beats):
        print(f'拍 {i}: kind={b.kind!r} text={b.text!r}')
        print(f'  raw={b.raw!r}')
        print(f'  code={b.code!r}')
        print(f'  ordered={getattr(b, "ordered", None)!r}')
        print(f'  stage_order={b.stage_order!r} op_order={b.op_order!r}')
        print(f'  show={b.show!r} hide={b.hide!r} scene={b.scene!r}')
        print(f'  with_seq_items={b.with_seq_items()!r}')
        print(f'  raw_without={b.raw_without_seq_with()!r}')
        print(f'  op_tokens_full={b.op_tokens_full()!r}')
        print(f'  _ordered_code_lines={_ordered_code_lines(b)!r}')
        print(f'  compose_code={b.compose_code()!r}')
    print('产物：')
    print(render_beats_to_rpy(beats))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
