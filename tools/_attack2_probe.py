# -*- coding: utf-8 -*-
"""第二轮攻击：文件级 / 块级 / 定义类写法，逐行比对 + xlsx 往返。"""
from __future__ import annotations

import difflib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.script_table import render_beats_to_rpy
from storygraph.story.statement_registry import (
    annotate_spec_statements, clear_cache, registry_for_parse_result)

RULES = {'statements': [
    {'name': 'mycue', 'label': '我的提示',
     'pattern': [
         {'kind': 'keyword', 'text': 'mycue', 'column': '动作名'},
         {'kind': 'variable', 'column': '内容'},
         {'kind': 'optional', 'text': 'intensity', 'column': '强度'},
     ]},
]}

HEAD = '''\
define e = Character("Eileen", color="#c8ffc8")
default flags = {"a": 1, "b": [2, 3]}
image bg room = "bg/room.png"
image eileen happy = im.FactorScale("e/happy.png", 0.8)
transform slide_in:
    xpos -100
    linear 0.5 xpos 0
screen hud():
    text "HP" xpos 10
style mybutton:
    background "#333"
layeredimage ling:
    always "ling base"
    group outfit:
        attribute swim if_any "ling swim"
init python:
    import math
    def helper(x):
        return math.floor(x)

label start(flag=True):
    "开场"
    mycue "闪电" intensity=3:
        target = ling
        $ renpy.pause(0.2)
    pause 1.0 hard
    call screen hud with dissolve
    show expression holo("lan base") as q at i11
    $ x = 1  # 行尾注释
    $ y = (
        1 + 2
      )
    camera at zoom:
        linear 1.0 zoom 1.2
    if flag:
        e "是"
    elif x:
        e "否"
    else:
        pass
    while x < 3:
        $ x += 1
    for i in range(3):
        "第 [i] 次"
    menu:
        set seen
        "选项" if flag:
            jump other
    translate english ch1:
        "已翻译"
    python early:
        x = 1
    return
'''

TAIL = '''
'''

CASES = {
    'defs_only': HEAD.split('label start')[0],
    'label_params': ('label start(flag=True):\n    "x"\n    return\n'),
    'custom_block': ('label ch:\n'
                     '    mycue "闪电" intensity=3:\n'
                     '        target = ling\n'
                     '        $ renpy.pause(0.2)\n'
                     '    e "之后"\n'),
    'call_screen_with': ('label ch:\n    call screen hud with dissolve\n    e "x"\n'),
    'show_expression': ('label ch:\n'
                        '    show expression holo("lan base") as q at i11\n'
                        '    e "x"\n'),
    'camera_block': ('label ch:\n    camera at zoom:\n'
                     '        linear 1.0 zoom 1.2\n    e "x"\n'),
    'multiline_py': ('label ch:\n'
                     '    $ y = (\n        1 + 2\n      )\n    e "x"\n'),
    'inline_comment': ('label ch:\n    $ x = 1  # 行尾注释\n    e "x"\n'),
    'while_for': ('label ch:\n    while x < 3:\n        $ x += 1\n'
                  '    for i in range(3):\n        "第 [i] 次"\n'),
    'menu_set': ('label ch:\n    menu:\n        set seen\n'
                 '        "选项" if flag:\n            jump other\n'),
    'translate': ('translate english ch1:\n    "已翻译"\n'),
    'python_early': ('python early:\n    x = 1\n'),
    'label_with_say': ('label ch:\n    e "hi"\n    return\n'),
}


def run(name: str, body: str) -> None:
    clear_cache()
    tmp = Path(tempfile.mkdtemp(prefix='a2_'))
    root = tmp / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.rules.json').write_text(
        json.dumps(RULES, ensure_ascii=False), encoding='utf-8')
    src = root / 'game' / 'ch.rpy'
    text = body
    src.write_text(body, encoding='utf-8')
    res = ParseResultBuilder().build(RenPyParser().parse(src))
    reg = registry_for_parse_result(res)
    annotate_spec_statements(res.statements, reg)
    beats = beats_from_parse_result(res)
    out = render_beats_to_rpy(beats, label_name=None)
    body_in = [l.rstrip() for l in body.splitlines() if l.strip()]
    body_out = [l.rstrip() for l in out.splitlines()
                if l.strip() and not l.startswith('# 由剧本表')]
    # `return` 是收尾补的，不算丢失
    if body_out and body_out[-1].strip() == 'return' and \
            (not body_in or body_in[-1].strip() != 'return'):
        body_out = body_out[:-1]
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

