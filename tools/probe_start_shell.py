#!/usr/bin/env python
"""那 6 个"凭空多出 label start"的文件，里面到底有什么拍？"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy, \
    _is_file_block_carrier

PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')
NAMES = ('generated_ui.rpy', 'whackamole_images.rpy', 'plottree.rpy',
         'script0.rpy', 'script1.rpy', 'transform.rpy')


def main() -> int:
    for n in NAMES:
        f = next((p for p in (PROJECT / 'game').rglob(n)), None)
        if f is None:
            print(f'{n}: 没找到')
            continue
        pr, _g = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        beats = beats_from_parse_result(pr)
        print('=' * 66)
        print(f'{n}  stmts={len(pr.statements)}  '
              f'file_blocks={len(getattr(pr, "file_blocks", []) or [])}  '
              f'labels={list(pr.labels)}  beats={len(beats)}')
        for b in beats[:3]:
            print(f'  拍: label={b.label!r} kind={b.kind!r} '
                  f'text={b.text[:20]!r} raw={b.raw[:30]!r} '
                  f'code={b.code[:30]!r} '
                  f'stage={b.stage[:30]!r} '
                  f'file_blocks={b.file_blocks[:30]!r}')
            print(f'      carrier={_is_file_block_carrier(b)}')
        out = render_beats_to_rpy(beats)
        print('  产物前 8 行：')
        for ln in out.splitlines()[:8]:
            print('   ', ln)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
