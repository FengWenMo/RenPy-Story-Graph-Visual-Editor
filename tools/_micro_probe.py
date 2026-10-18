# -*- coding: utf-8 -*-
"""小样本矩阵：每个 case 单独跑 解析→拍→导出，只看 diff。"""
from __future__ import annotations

import difflib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_statements
from storygraph.story.script_table import render_beats_to_rpy
from storygraph.story.statement_registry import (
    annotate_spec_statements, clear_cache, registry_for_parse_result)

RULES = {
    'statements': [
        {'name': 'sfx_mix', 'label': '多轨音效',
         'pattern': [
             {'kind': 'keyword', 'text': 'sfx_mix', 'column': '动作名'},
             {'kind': 'choice', 'column': '声道', 'choices': ['se', 'se2'],
              'multi': True},
             {'kind': 'variable', 'column': '文件'},
             {'kind': 'optional', 'text': 'gain', 'column': '音量'},
         ]},
    ]
}

CASES = {
    'scene_with': '    scene bg room with fade\n    e "hi"\n',
    'show_with': '    show e happy with dissolve\n    e "hi"\n',
    'two_stage_with': ('    scene bg room with Fade(1, 0, 1)\n'
                       '    show e happy with dissolve\n'
                       '    e "hi"\n'),
    'say_then_window': '    e "hi"\n    window hide\n    e "bye"\n',
    'say_then_py': '    e "hi"\n    $ x = 1\n    e "bye"\n',
    'custom_then_code': ('    sfx_mix se "a.ogg"\n'
                         '    $ renpy.pause(0.5)\n'
                         '    e "hi"\n'),
    'audio_bare': '    play music "a.ogg"\n    e "hi"\n',
    'audio_volume': '    play sound "s.ogg" volume 0.5\n    e "hi"\n',
    'voice_bare': '    voice "v.ogg"\n    e "hi"\n',
    'pause_hard': '    pause 1.0 hard\n    e "hi"\n',
    'at_transform': '    show e at Transform(zoom=1.2, alpha=.5)\n    e "hi"\n',
    'inline_tags': '    e "{color=#f00}红{/color}字"\n',
    'with_bare': '    with fade\n    e "hi"\n',
    'hide_with': '    hide e with dissolve\n    e "hi"\n',
    'stop_fade': '    stop music fadeout 1.0\n    e "hi"\n',
    'queue': '    queue music "b.ogg" loop\n    e "hi"\n',
    'show_at_with': '    show e happy at left with dissolve\n    e "hi"\n',
    'scene_at_with': '    scene bg room at bgfit with fade\n    e "hi"\n',
    'hide_with_plus_show_with': ('    hide e with dissolve\n'
                                '    show f happy with fade\n'
                                '    e "hi"\n'),
    'show_behind': ('    show f happy behind e at left zorder 2 '
                    'onlayer master\n    e "hi"\n'),
    'show_as': '    show e happy as alias1 at center\n    e "hi"\n',
    'voice_sustain': '    voice sustain\n    e "hi"\n',
    'pause_no_arg': '    pause\n    e "hi"\n',
    'with_none': '    with None\n    e "hi"\n',
    'play_var': '    play music current_bgm fadeout 1.0\n    e "hi"\n',
}


def run(name: str, body: str) -> None:
    clear_cache()
    tmp = Path(tempfile.mkdtemp(prefix='m_'))
    root = tmp / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.rules.json').write_text(
        json.dumps(RULES, ensure_ascii=False), encoding='utf-8')
    src = root / 'game' / 'ch.rpy'
    src.write_text('label ch:\n' + body, encoding='utf-8')
    res = ParseResultBuilder().build(RenPyParser().parse(src))
    reg = registry_for_parse_result(res)
    annotate_spec_statements(res.statements, reg)
    beats = beats_from_statements(res.statements, labels=res.statement_labels)
    out = render_beats_to_rpy(beats, label_name='ch')
    body_out = [l for l in out.splitlines()
                if l.strip() and not l.startswith('label ')
                and not l.startswith('#')]
    body_in = [l for l in body.splitlines() if l.strip()]
    diffs = list(difflib.unified_diff(body_in, body_out, 'in', 'out',
                                      lineterm='', n=0))
    print(f'--- {name}: {"OK" if not diffs else "DIFF"}')
    for line in diffs[2:]:
        print('   ', line)


def main() -> int:
    for name, body in CASES.items():
        run(name, body)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
