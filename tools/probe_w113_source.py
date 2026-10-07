#!/usr/bin/env python
"""探针：真机 mini_game1 里 `W113 空台词行` 落在哪一拍的头上。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')


def main() -> int:
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=discover_scripts(REAL),
                                               graph=None)
    beats = beats_from_parse_result(pr)
    for i, b in enumerate(beats):
        where = getattr(b, 'line_id', '') or f'{b.label}.{b.seq:04d}'
        if not where.startswith('mini_game1'):
            continue
        empty_say = (b.kind in ('say', 'narration') and not b.text
                     and not b.comment and not b.raw and not b.code
                     and not b.sound and not b.voice and not b.music
                     and not b.scene and not b.show and not b.hide
                     and not getattr(b, 'stage', '')
                     and not getattr(b, 'bgm', '')
                     and not getattr(b, 'sfx', ''))
        if empty_say:
            print(f'{where}  seq={b.seq} kind={b.kind!r} '
                  f'loop_header={b.loop_header!r} opaque={(b.opaque or "")[:30]!r} '
                  f'spec_head={b.spec_head!r} camera_header={b.camera_header!r}')
    print('--- 上面是会被判"空台词行"的拍 ---')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
