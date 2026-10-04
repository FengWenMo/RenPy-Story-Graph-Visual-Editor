#!/usr/bin/env python
"""最小复现：真实项目里那 4 个"整段消失"的 label，到底是哪一条语句吞掉的？

逐条加语句，看 beat 数什么时候归零。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result

CASES = {
    '只有 $':            'label a:\n    $ focusmode = False\n',
    '只有 show screen':  'label a:\n    show screen home\n',
    '只有 hide screen':  'label a:\n    hide screen home\n',
    '只有 call screen':  'label a:\n    call screen map\n',
    '只有 play music':   'label a:\n    play music "m.ogg" fadein 1.0\n',
    '只有 scene':        'label a:\n    scene overworld at zoomphoto\n',
    '只有 with':         'label a:\n    with Fade(0.2, 0.0, 0.2)\n',
    '只有 window hide':  'label a:\n    window hide\n',
    '只有 show 立绘':    'label a:\n    show startmenu\n',
    '$ + show screen':   'label a:\n    $ focusmode = False\n'
                         '    show screen home\n',
    'play + scene + with': 'label a:\n'
                           '    play music "m.ogg" fadein 1.0\n'
                           '    scene overworld at zoomphoto\n'
                           '    with Fade(0.2, 0.0, 0.2)\n',
    '原样 pomo_start': ('label a:\n'
                        '    play music "m.ogg" fadein 1.0 fadeout 1.0\n'
                        '    show startmenu \n'
                        '    with Dissolve(1.0)\n'
                        '    show screen home\n'
                        '    with Dissolve(1.0)\n'
                        '    window hide\n'
                        '    show screen home \n'
                        '    $ renpy.pause(hard=True)\n'),
    '有台词兜底':        ('label a:\n'
                         '    $ focusmode = False\n'
                         '    show screen home\n'
                         '    e "一句话"\n'),
    # —— 关键：前面还有一个带台词的 label 时，孤儿语句归谁？——
    '前置 label + 孤儿段': (
        'label prev:\n'
        '    e "前一句"\n'
        'label a:\n'
        '    $ focusmode = False\n'
        '    show screen home\n'
        '    $ renpy.pause(hard=True)\n'),
    '前置 label + 孤儿段 + 后面有台词': (
        'label prev:\n'
        '    e "前一句"\n'
        'label a:\n'
        '    $ focusmode = False\n'
        '    show screen home\n'
        'label b:\n'
        '    e "后一句"\n'),
    '孤儿段 + 后面有台词': (
        'label a:\n'
        '    $ focusmode = False\n'
        '    show screen home\n'
        'label b:\n'
        '    e "后一句"\n'),
}


def run(name: str, src: str) -> None:
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / 'h.rpy'
        f.write_text(src, encoding='utf-8')
        pr, _g = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        bs = beats_from_parse_result(pr)
        kinds = [s.kind for s in pr.statements]
        flag = '  ' if bs else '✗ '
        got = {b.label for b in bs}
        print(f'{flag}{name:26} beats={len(bs)}  label={sorted(got)}  '
              f'stmts={kinds}')


def main() -> int:
    for k, v in CASES.items():
        run(k, v)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
