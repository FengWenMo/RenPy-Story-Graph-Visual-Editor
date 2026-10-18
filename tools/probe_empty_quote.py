# -*- coding: utf-8 -*-
"""
探针：空引号（`""` / `''` / `"   "`）在词法层到底留下了什么
================================================================================

现象（修之前）：`_atoms` 里 `if text: out.append(...)` —— 引号内容是空串时
**既不进词、也不记毛病**，直接 `i = j` 跳过。于是这一对引号在工具里完全不存在。

要查的不是"序号少 1"，而是它会不会造成更贵的后果：
  · 值整体**前移**（人写的 `""` 那一格空着，后面的值挤进来 → 可能报成功）；
  · 读值侧（`_claim`）也跟着前移 → 落盘写错行；
  · 词号偏移的**真实来源**（`_split_atom` 把一段拆成几个 token，比空引号更常见）。

✅ 2026-10-18 已修，现在跑这个探针看到的是**修好之后**的样子：
   空引号在词流里是 `pattern.EMPTY_TOKEN`（占位、不进值），`_atoms` 记一条
   `kind='empty'` 的毛病，`match_line` 点名它，`slot_span` 能按位置换掉它。

跑法：`python tools/probe_empty_quote.py`
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P                        # noqa: E402
from storygraph.story import spec_args as SA                        # noqa: E402

SHOW = {'kind': 'keyword', 'text': 'show'}


def pat(*, multi=False):
    return [SHOW,
            {'kind': 'variable', 'column': '角色', 'options': ['lan', '小明']},
            {'kind': 'choice', 'column': '服装',
             'options': ['base_swimsuit', '泳装'], 'multi': multi},
            {'kind': 'choice', 'column': '表情', 'options': ['happy', '开心'],
             'multi': multi}]


def run(line, *, multi=False):
    pattern = pat(multi=multi)
    slots = P.parse_pattern(pattern)
    atoms, probs = P._atoms(line)
    toks = P.tokenize(line, slots)
    got = P.match_line(pattern, line)
    claimed = SA._claim(re.sub(r'^\s*show\s+', '', line), slots[1:])
    print('-' * 76)
    print(f'line = {line!r}   （人按空格数 = {len(line.split())} 个词）')
    print(f'  atoms    = {[(a["text"], a["word"]) for a in atoms]}')
    print(f'  lex_prob = {probs}')
    print(f'  tokens   = {toks}')
    print(f'  ok       = {got["ok"]}')
    print(f'  cells    = {[(c["column"], c["text"]) for c in got["cells"]]}')
    print(f'  left     = {got["leftover"]}')
    print(f'  reason   = {got["reason"]!r}')
    print(f'  读值侧   = {claimed}')


def main():
    print('##########  ① 空引号在中间  ##########')
    run('show lan "" happy')
    run('show lan "" base_swimsuit happy')
    run('show lan "" base_swimsuit happy', multi=True)

    print('\n##########  ② 空引号在两头 / 只有它  ##########')
    run('show "" lan happy')
    run('show lan base_swimsuit ""')
    run('show lan ""')
    run('""')
    run('show "" "" happy')

    print('\n##########  ③ 空单引号 / 只有空白  ##########')
    run("show lan '' happy")
    run('show lan "   " happy')
    run('show lan "\t" happy')
    run('show lan "\u3000" happy')       # 全角空格

    print('\n##########  ④ 词号偏移的真实来源（不关空引号）  ##########')
    # 中文连写：`_split_atom` 把一段拆成 3 个 token，人按空格只数到 2 个词
    cn = [SHOW,
          {'kind': 'variable', 'column': '角色', 'options': ['小明']},
          {'kind': 'choice', 'column': '服装', 'options': ['泳装']},
          {'kind': 'choice', 'column': '表情', 'options': ['开心']}]
    line = 'show 小明泳装'
    got = P.match_line(cn, line)
    print('-' * 76)
    print(f'line = {line!r}   人按空格数 = {len(line.split())} 个词')
    print(f'  tokens = {P.tokenize(line, P.parse_pattern(cn))}')
    print(f'  reason = {got["reason"]!r}   ← 说"第几个词"时，人怎么数？')

    print('\n##########  ⑤ 空引号是不是"合法的空值"（Ren\'Py 侧事实）  ##########')
    print('  写回原文：', repr(SA.set_slot_value('show lan "" happy',
                                                P.parse_pattern(pat()), 2,
                                                'base_swimsuit')))
    print('  ⚠ `show lan "" happy` 若真的落盘，Ren\'Py 会把 "" 当成一个属性名')


if __name__ == '__main__':
    main()
