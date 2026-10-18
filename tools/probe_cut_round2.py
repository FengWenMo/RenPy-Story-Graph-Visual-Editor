# -*- coding: utf-8 -*-
"""
第二轮探针：重做后的新攻击面
================================================================================

白盒看到的新东西：
  · 词法层 `_atoms`（引号 / 脏空白）+ `_split_atom`（候选补切，小 DP）；
  · 一份 DP `_spans`（含"跳过垃圾" `SKIP_JUNK`、"吃掉拼错关键字" `MISS_KW_SKIP`）；
  · `_report` 从同一份 spans 倒推 → 报错与画面同源。

于是可疑点从"两套口径"变成了**代价之间**的关系：
  ① 末尾垃圾词：晾着要 3/词，塞进多值格 2/词 → 塞更便宜，会不会报成功？
  ② 引号里的自由文本：`_tokens` 只返回 str，`quoted` 标记丢了 —— 值写回还带引号吗？
  ③ `_span_hit` 的 `''.join`：把人写的 `ab cd` 拼成一个候选 `abcd`？
  ④ `MISS_KW_SKIP=4` vs `COST_UNKNOWN=2`：拼错的关键字会不会流进第一个值格？
  ⑤ 空引号 `""`：`if text` 判断让它**静默消失**？
  ⑥ 单字候选把中文切碎 / 长行性能。

跑法：`python tools/probe_cut_round2.py`
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P        # noqa: E402

SHOW = {'kind': 'keyword', 'text': 'show'}
ROLES = ['lan', 'ye']
OUTFIT = ['clothes2', 'base_swimsuit']
FACE = ['happy', 'sad']


def three_multi():
    return [SHOW,
            {'kind': 'variable', 'column': '角色', 'options': ROLES,
             'multi': True},
            {'kind': 'choice', 'column': '服装', 'options': OUTFIT,
             'multi': True},
            {'kind': 'choice', 'column': '表情', 'options': FACE,
             'multi': True}]


def show(title, pattern, line, **kw):
    got = P.match_line(pattern, line, **kw)
    print('=' * 76)
    print(f'### {title}')
    print(f'    line  = {line!r}')
    print(f'    ok    = {got["ok"]}')
    print(f'    cells = {[(c["column"], c["text"]) for c in got["cells"]]!r}')
    print(f'    left  = {got["leftover"]!r}')
    print(f'    reason= {got["reason"]!r}')
    return got


def main():
    print('##########  ① 垃圾词：位置决定"报错还是报成功"  ##########')
    show('①-1 垃圾在末尾（多值格可以"单独吃一个不是自己人的词"）',
         three_multi(), 'show lan base_swimsuit happy junkword')
    show('①-2 同一个垃圾放中间（对照）',
         three_multi(), 'show lan junkword base_swimsuit happy')
    show('①-3 垃圾在两个自己人的中间',
         three_multi(), 'show lan clothes2 junkword happy')
    show('①-4 垃圾在开头', three_multi(),
         'show junkword lan base_swimsuit happy')
    show('①-5 末尾两个垃圾', three_multi(),
         'show lan base_swimsuit happy junk1 junk2')

    print('\n##########  ② 引号里的自由文本：引号还在值里吗  ##########')
    free = [SHOW,
            {'kind': 'variable', 'column': '角色', 'options': ROLES},
            {'kind': 'choice', 'column': '服装', 'options': OUTFIT},
            {'kind': 'choice', 'column': '表情', 'options': FACE}]
    got = show('②-1 引号包的自由文本（候选里没有）', free,
               'show lan "my custom outfit" happy')
    print(f'    ⚠ 服装值里有没有引号：{got["cells"][2]["text"]!r}')
    show('②-2 引号里的词是候选的一部分（应当精确命中）', free,
         'show lan "base_swimsuit" happy')
    show('②-3 引号内有空格且正好是候选', free,
         'show lan "base swimsuit" happy')
    show('②-4 引号里的转义', free, 'show lan "a\\"b" happy')
    show('②-5 空引号（""）', free, 'show lan "" happy')
    show('②-6 两个引号串紧挨着', free, 'show lan "a b""c d" happy')
    show('②-7 引号没关上', free, 'show lan "base swimsuit happy')

    print('\n##########  ③ `''.join` 把两个词焊成一个候选  ##########')
    weld = [{'kind': 'choice', 'column': 'A', 'options': ['abcd']}]
    show('③-1 候选是连写的 abcd，人写的是 ab cd', weld, 'ab cd')
    weld2 = [{'kind': 'choice', 'column': 'A', 'options': ['abcd']},
             {'kind': 'choice', 'column': 'B', 'options': ['ab']}]
    show('③-2 同一行，ab 还属于另一格', weld2, 'ab cd')

    print('\n##########  ④ 拼错的关键字会不会流进第一个值格  ##########')
    show('④-1 shw（角色候选里没有）', three_multi(),
         'shw lan base_swimsuit happy')
    shw_as_role = [SHOW,
                   {'kind': 'variable', 'column': '角色',
                    'options': ['shw', 'lan'], 'multi': True},
                   {'kind': 'choice', 'column': '服装', 'options': OUTFIT,
                    'multi': True},
                   {'kind': 'choice', 'column': '表情', 'options': FACE,
                    'multi': True}]
    show('④-2 shw 正好是角色候选（平局会不会翻）', shw_as_role,
         'shw lan base_swimsuit happy')

    print('\n##########  ⑤ word_offset 的输入健壮性  ##########')
    for bad in ('x', -3, None, 2.5):
        try:
            got = P.match_line(three_multi(),
                               'show lan base_swimsuit happy',
                               word_offset=bad)
            print(f'    word_offset={bad!r} → ok={got["ok"]} reason={got["reason"]!r}')
        except Exception as exc:
            print(f'    word_offset={bad!r} → {type(exc).__name__}: {exc}')

    print('\n##########  ⑥ 中文单字候选 / 长行  ##########')
    show('⑥-1 角色候选里有单字「林」', [
        SHOW,
        {'kind': 'variable', 'column': '角色', 'options': ['林']},
        {'kind': 'choice', 'column': '服装', 'options': ['泳装']}],
        'show 林穿泳装')
    show('⑥-2 候选里有「的」「了」这种高频单字', [
        SHOW,
        {'kind': 'variable', 'column': '角色', 'options': ['小明', '的']},
        {'kind': 'choice', 'column': '服装', 'options': ['泳装', '了']}],
        'show 小明的泳装了')

    # 长行 + 大量候选：`_split_atom` 是 O(字数 × 同首字候选数)
    big = [f'候选{i}' for i in range(400)] + ['小明', '泳装', '开心']
    slots = P.parse_pattern([SHOW,
                             {'kind': 'variable', 'column': 'c1',
                              'options': big},
                             {'kind': 'choice', 'column': 'c2',
                              'options': big}])
    line = 'show ' + '小明泳装开心' * 60
    t0 = time.time()
    got = P.match_line(slots, line)
    print(f'\n    长行 720 字 × 400 候选：ok={got["ok"]} 耗时={time.time() - t0:.3f}s')


if __name__ == '__main__':
    main()
