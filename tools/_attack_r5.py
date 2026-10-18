# -*- coding: utf-8 -*-
"""第五轮修完之后的自打探针：删格 / 多值 / 可选段带固定字 / 反复往返 / 真实头行。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P            # noqa: E402
from storygraph.story import spec_args as SA            # noqa: E402

SHOW = {'kind': 'keyword', 'text': 'show'}

CN = [SHOW,
      {'kind': 'variable', 'column': '角色', 'options': ['小明']},
      {'kind': 'choice', 'column': '服装', 'options': ['泳装', '校服']},
      {'kind': 'choice', 'column': '表情', 'options': ['开心']}]

OPT = [SHOW,
       {'kind': 'variable', 'column': '角色', 'options': ['lan']},
       {'kind': 'choice', 'column': '表情', 'options': ['happy']},
       {'kind': 'optional', 'column': '位置', 'text': 'at',
        'options': ['left', 'right']}]

MULTI = [SHOW,
         {'kind': 'variable', 'column': '角色', 'options': ['lan']},
         {'kind': 'choice', 'column': '服装', 'options': ['base_swimsuit'],
          'multi': True},
         {'kind': 'choice', 'column': '表情',
          'options': ['happy', 'sad', 'shy'], 'multi': True}]

SWIM = [{'kind': 'keyword', 'text': 'swim_suit'},
        {'kind': 'variable', 'column': '人物', 'options': ['lyj']},
        {'kind': 'choice', 'column': '服装', 'options': ['泳装', '校服']},
        {'kind': 'keyword', 'text': 'mood'},
        {'kind': 'choice', 'column': '心情', 'options': ['happy', 'sad']}]

QUOTED = [SHOW,
          {'kind': 'variable', 'column': '角色', 'options': ['lan']},
          {'kind': 'choice', 'column': '服装', 'options': ['a\\"b']},
          {'kind': 'choice', 'column': '表情', 'options': ['happy']}]

CASES = [
    ('中文连写', CN, 'show 小明泳装开心'),
    ('可选段', OPT, 'show lan happy at left'),
    ('多值格', MULTI, 'show lan base_swimsuit happy sad'),
    ('转义', QUOTED, 'show lan "a\\"b" happy'),
    ('真实头行', SWIM, 'swim_suit lyj "泳装" mood=happy'),
]


def main():
    print('--- ① 幂等：值原样写回 10 轮，字节不变 ---')
    for name, pat, line in CASES:
        slots = P.parse_pattern(pat)
        out = line
        for _ in range(10):
            values = P.assign_tokens(P.tokenize(out, slots), slots)['values']
            for i in sorted(values):
                out = SA.set_slot_value(out, slots, i, values[i])
        print(f'  {name:<8} {"OK " if out == line else "BAD"} {out!r}')

    print('\n--- ② 删一格：两侧不许粘成一个词 ---')
    slots = P.parse_pattern(CN)
    for i in (1, 2, 3):
        print(f'  删第{i}格 -> {SA.set_slot_value("show 小明泳装开心", slots, i, "")!r}')

    print('\n--- ③ 可选段：带固定字 / 不带固定字 / 清空 ---')
    slots = P.parse_pattern(OPT)
    print('  right       ->', repr(SA.set_slot_value('show lan happy at left', slots, 3, 'right')))
    print('  at right    ->', repr(SA.set_slot_value('show lan happy at left', slots, 3, 'at right')))
    print('  (空)        ->', repr(SA.set_slot_value('show lan happy at left', slots, 3, '')))
    print('  at left(原) ->', repr(SA.set_slot_value('show lan happy at left', slots, 3, 'at left')))

    print('\n--- ④ 多值格：写两个词 ---')
    slots = P.parse_pattern(MULTI)
    print('  shy sad   ->', repr(SA.set_slot_value('show lan base_swimsuit happy sad', slots, 3, 'shy sad')))
    print('  shy       ->', repr(SA.set_slot_value('show lan base_swimsuit happy sad', slots, 3, 'shy')))

    print('\n--- ⑤ 一格里没有词时不许"按值找" ---')
    slots = P.parse_pattern(MULTI)
    print('  show lan happy 的服装 ->', SA.slot_span('show lan happy', slots, 2))
    print('  show lan happy 的表情 ->', SA.slot_span('show lan happy', slots, 3))

    print('\n--- ⑥ 真实头行：逐列写回（fold_dynamic_columns，列名是有分隔符的） ---')
    pat = P.parse_pattern(SWIM)
    row = 'swim_suit lyj "泳装" mood=happy'
    for label, values in (
            ('原样  ', {'人物': 'lyj', '服装': '泳装', '心情': 'happy'}),
            ('改服装', {'人物': 'lyj', '服装': '校服', '心情': 'happy'}),
            ('改人物', {'人物': 'ling', '服装': '泳装', '心情': 'happy'}),
            ('改心情', {'人物': 'lyj', '服装': '泳装', '心情': 'sad'})):
        headers = {SA.dynamic_column_name('swim_suit', k, i): v
                   for i, (k, v) in enumerate(values.items())}
        headers['__pattern__'] = pat
        print(f'  {label} ->', repr(SA.fold_dynamic_columns(row, headers)))

    print('\n--- ⑦ show 形状（动作名不在格子里）逐列写回 ---')
    pat = P.parse_pattern([{'kind': 'variable', 'column': '人物',
                            'options': ['lan', 'ye']},
                           {'kind': 'choice', 'column': '服装',
                            'options': ['base_swimsuit', 'base_handdown']},
                           {'kind': 'choice', 'column': '表情',
                            'options': ['happy', 'sad']}])
    body = 'lan "base_swimsuit" happy'
    headers = {SA.dynamic_column_name('show', '人物', 0): 'lan',
               SA.dynamic_column_name('show', '服装', 1): 'base_handdown',
               SA.dynamic_column_name('show', '表情', 2): 'happy',
               '__pattern__': pat}
    print('  改服装 ->', repr(SA.fold_dynamic_columns(body, headers)))

    print('\n--- ⑧ 性能：12 格 × 60 个词的行，逐列写回 ---')
    import time
    big_slots = [{'kind': 'keyword', 'text': 'go'}] + [
        {'kind': 'choice', 'column': f'c{i}', 'options': [f'v{i}_{j}'
                                                          for j in range(4)]}
        for i in range(12)]
    big_slots = P.parse_pattern(big_slots)
    big_row = 'go ' + ' '.join(f'v{i}_{i % 4}' for i in range(12))
    heads = {SA.dynamic_column_name('go', f'c{i}', i): f'v{i}_{i % 4}'
             for i in range(12)}
    heads['__pattern__'] = big_slots
    t0 = time.perf_counter()
    for _ in range(50):
        SA.fold_dynamic_columns(big_row, heads)
    print(f'  50 轮 × 12 列：{(time.perf_counter() - t0) * 1000:.0f} ms')


main()
