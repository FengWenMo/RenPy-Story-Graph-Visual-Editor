# -*- coding: utf-8 -*-
"""行尾注释在"非最后一条语句"上还能活下来吗。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy

CASES = {
    'stage_comments': ('label ch:\n'
                       '    scene bg room with fade  # 换景注释\n'
                       '    show e happy at left  # 站左边\n'
                       '    e "hi"\n'),
    'raw_comment': ('label ch:\n'
                    '    pause 0.5  # 等一下\n'
                    '    e "hi"\n'),
    'say_comment': ('label ch:\n'
                    '    e "hi"  # 台词注释\n'
                    '    "收尾"\n'),
    'code_comment': ('label ch:\n'
                     '    $ x = 1  # 赋值\n'
                     '    e "hi"\n'),
    'audio_comment': ('label ch:\n'
                      '    play music "a.ogg"  # 主题曲\n'
                      '    e "hi"\n'),
}


def main() -> int:
    for name, script in CASES.items():
        td = Path(tempfile.mkdtemp(prefix='cmt-'))
        root = td / 'proj'
        (root / 'game').mkdir(parents=True)
        src = root / 'game' / 'ch.rpy'
        src.write_text(script, encoding='utf-8')
        res = ParseResultBuilder().build(RenPyParser().parse(src))
        print('===', name)
        for s in res.statements:
            print('  stmt', s.kind, s.payload,
                  'inline=', repr(getattr(s, 'inline_comment', '')))
        beats = beats_from_parse_result(res)
        for i, b in enumerate(beats):
            print('  beat', i, b.kind, 'inline=', repr(b.inline_comment),
                  'raw=', repr(b.raw), 'code=', repr(b.code))
        print(render_beats_to_rpy(beats, label_name=None))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
