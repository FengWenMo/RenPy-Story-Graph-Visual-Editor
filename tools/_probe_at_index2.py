# -*- coding: utf-8 -*-
"""`@N` 的两种真实形状：固定字格在格子里 / 可选段在格子里。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P            # noqa: E402
from storygraph.story import spec_args as SA            # noqa: E402


def fold(pat, headers, row):
    slots = P.parse_pattern(pat)
    values = dict(headers)
    values['__pattern__'] = slots
    return SA.fold_dynamic_columns(row, values)


# ① 固定字在格子里、它自己没有列（最自然的写法）
PAT1 = [{'kind': 'keyword', 'text': 'swim_suit'},
        {'kind': 'variable', 'column': '人物', 'options': ['lyj', 'ling']},
        {'kind': 'choice', 'column': '服装', 'options': ['泳装', '校服']}]
slots = P.parse_pattern(PAT1)
print('① [kw swim_suit][人物][服装]')
for i, s in enumerate(PAT1):
    print(f'   slot{i} {s["kind"]:<8} -> walk 号 {SA._pattern_slot_index_for(slots, "x@%d" % i)}')
ROW1 = 'swim_suit lyj "泳装" mood=happy'
for name, val in (('人物', 'lyj'), ('服装', '校服')):
    walk = 1 + [s.get('column') for s in PAT1[1:]].index(name)
    h = SA.dynamic_column_name('swim_suit', name, walk)
    print(f'   {h:<22} = {val:<4} -> {fold(PAT1, {h: val}, ROW1)!r}')

# ② 可选段在格子里（walk 号 ≠ 格子下标）
PAT2 = [{'kind': 'keyword', 'text': 'mycue'},
        {'kind': 'variable', 'column': '人物', 'options': ['lan']},
        {'kind': 'optional', 'column': '位置', 'text': 'at',
         'options': ['left', 'right']},
        {'kind': 'choice', 'column': '表情', 'options': ['happy', 'sad']}]
slots2 = P.parse_pattern(PAT2)
print('\n② [kw mycue][人物][optional at][表情]')
print('   walk 0..3 -> 格子下标',
      [SA._pattern_slot_index_for(slots2, f'x@{n}') for n in range(3)])
row = 'mycue lan at left happy'
print('   改表情（@2）->', repr(fold(PAT2, {'mycue·表情@2': 'sad'}, row)))
print('   改人物（@1）->', repr(fold(PAT2, {'mycue·人物@1': 'ling'}, row)))
print('   原样       ->', repr(fold(PAT2, {'mycue·表情@2': 'happy'}, row)))
