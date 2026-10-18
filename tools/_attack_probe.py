# -*- coding: utf-8 -*-
"""白盒攻击：把"演出里真实会出现的花样"一次性喂进 解析→拍→导出，逐行比对。"""
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

DUMP = True

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

SCRIPT = r'''label ch:
    scene bg room with fade
    show eileen happy at left
    show eileen happy at center, flip
    show eileen happy at Transform(zoom=1.2, alpha=0.5)
    show eileen happy with dissolve
    hide eileen with dissolve
    with fade
    pause
    pause 0.5
    pause 1.0 hard
    e "普通台词"
    e "{color=#ff0000}红{/color}与{i}斜{/i}"
    e "引号在标签里{color=#ff0000}\"你好\"{/color}结束"
    e "插值{size=+8}[player]{/size}" with dissolve
    e "带 id" id=hello
    play music "a.ogg" fadein 2.0 fadeout 1.0
    queue music "b.ogg"
    stop music fadeout 1.0
    play sound "s.ogg" volume 0.5
    voice "v.ogg"
    voice sustain
    sfx_mix se se2 "se/a.ogg" gain=-6
    window hide
    window show dissolve
    $ renpy.pause(1.0, hard=True)
    show screen foo
    hide screen foo
    menu:
        "选项一":
            e "一"
        "选项二 if flag":
            e "二"
    if flag:
        e "条件内"
    else:
        pass
    jump other
'''


def main() -> int:
    clear_cache()
    tmp = Path(tempfile.mkdtemp(prefix='attack_'))
    root = tmp / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.rules.json').write_text(
        json.dumps(RULES, ensure_ascii=False, indent=2), encoding='utf-8')
    src = root / 'game' / 'ch.rpy'
    src.write_text(SCRIPT, encoding='utf-8')

    res = ParseResultBuilder().build(RenPyParser().parse(src))
    reg = registry_for_parse_result(res)
    annotate_spec_statements(res.statements, reg)
    beats = beats_from_statements(res.statements, labels=res.statement_labels)
    out = render_beats_to_rpy(beats, label_name='ch')

    print('=== beats ===')
    for i, b in enumerate(beats):
        print(i, b.kind, '| head=', (getattr(b, 'spec_head', '') or '')[:60],
              '| text=', repr((b.text or '')[:40]),
              '| code=', repr((getattr(b, 'code', '') or '')[:80]),
              '| raw=', repr((getattr(b, 'raw', '') or '')[:60]))
        if DUMP:
            for k, v in sorted(vars(b).items()):
                if v not in ('', None, [], {}, 0, 0.0, False):
                    print('     ', k, '=', repr(v)[:180])

    print('\n=== out ===')
    print(out)

    orig_lines = [l.rstrip() for l in SCRIPT.splitlines()[1:]]
    out_lines = [l.rstrip() for l in out.splitlines()]
    out_body = [l for l in out_lines if not l.startswith('label ')]
    print('\n=== diff (orig -> out) ===')
    for line in difflib.unified_diff(orig_lines, out_body, 'orig', 'out',
                                     lineterm='', n=1):
        print(line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
