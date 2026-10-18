# -*- coding: utf-8 -*-
"""
第四轮攻击：空引号废掉的是**哪一格**，以及废掉之后谁接住
================================================================================

用户 2026-10-18 的观察：
    一格勾了"可有多个"时，`show lan "" base_swimsuit happy` 会变成
    服装=空、表情=空、两个词都晾着 —— 因为多值格"一个都不吃"免费。

实测下来这条有两层，**第一层更贵**：

  第一层（P0）**dead 的归属是代价选出来的，不是位置定的**。
     `assign_tokens` 先跑一遍 `_spans`，看哪一格"吃到了空引号"（`broken`）。
     但空引号对**任何**格都免费（`_tok_cost(EMPTY_TOKEN) == COST_OWN`），
     多值格还能"不吃"（也是 0）。于是 DP 会把空引号**推给下一格**：

         只「服装」multi、表情必填：
             修之前 broken=[3]（表情），服装=base_swimsuit ← **值整体左移一格**
             现在   broken=[2]（服装），表情=base_swimsuit  ← 位置说了算

     人写的是"服装空、表情=base_swimsuit"，工具却显示"服装=base_swimsuit、
     表情空"。`dead` 的注释假设"空引号不许跳过，所以一定落到轮到的那一格"——
     漏了"多值格可以不吃，把空引号让给下一格"这一条。

  第二层（P1）**dead 格的候选词对别人仍按"候选外"计价**。
     必填格：吃 5 vs 不吃(7+3=10) → 接住；
     多值格：吃 5 vs 不吃(0+3=3)  → 不接。
     于是"按位置接住"这条设计意图只在必填格成立，多值格把两个词一起晾着。

两处修法都不动整张代价表、也不动"多值格空着合法"这条口径：

  A. `solve` 开头：`tokens[ti] == EMPTY_TOKEN` 时，这一格**唯一**的选择是
     吃掉它（位置决定 dead，DP 没有选择权）。约 6 行。
  B. `_tok_cost`：只属于 dead 格的候选词 → `COST_UNKNOWN(2)`，不是 `COST_OFFLIST(5)`。
     约 4 行。

实测 A+B 落地之后，三种 multi 组合都收敛到"服装=''、表情=base_swimsuit、happy 多余"，
与"都不 multi"的参照系逐格相同（探针 `tools/probe_dead_offlist.py`）。

⚠ 仍然不用 `xfail`。红=缺，绿=护栏。

跑法::

    python -m pytest tests/test_cut_dead_positions.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P                        # noqa: E402

SHOW = {'kind': 'keyword', 'text': 'show'}


def build(*, role_multi=False, outfit_multi=False, face_multi=False):
    return [SHOW,
            {'kind': 'variable', 'column': '角色', 'options': ['lan'],
             'multi': role_multi},
            {'kind': 'choice', 'column': '服装',
             'options': ['base_swimsuit'], 'multi': outfit_multi},
            {'kind': 'choice', 'column': '表情', 'options': ['happy'],
             'multi': face_multi}]


def _run(pattern, line):
    slots = P.parse_pattern(pattern)
    return P.assign_tokens(P.tokenize(line, slots), slots)


MULTI_SHAPES = [
    pytest.param(dict(outfit_multi=True), id='只有服装multi'),
    pytest.param(dict(outfit_multi=True, face_multi=True),
                 id='服装+表情都multi'),
    pytest.param(dict(role_multi=True, outfit_multi=True, face_multi=True),
                 id='三格全multi'),
]


# ===========================================================================
#  第一层：dead 的归属必须由**位置**决定
# ===========================================================================

@pytest.mark.parametrize('shape', MULTI_SHAPES)
def test_空引号必须废掉它位置上那一格(shape):
    """
    `show lan "" base_swimsuit happy`：空引号在第 3 个词的位置，
    对应「服装」（第 3 格）。所以被废掉的必须是服装：

        服装 = ''（这一行它没有值）
        表情 = base_swimsuit（按位置接住）
        happy = 多出来的

    修之前只有"都不 multi"能做到。多值格参与时（这就是本轮要修的）：

        只「服装」multi、表情必填 → `broken=[3]`：空引号被推给**表情**，
        服装吃掉了 `base_swimsuit` —— 人写的"服装空"变成了"服装=base_swimsuit"，
        **值整体左移一格**。这比"两个词都晾着"更糟：它报的格子是对的，
        值却全错位，而且 `broken` 还指着一个无辜的格子。

    修法（`_spans` 的 `solve`）：轮到取值格而下一个词是空引号时，这一格
    只有"吃掉它"一个分支 —— `dead` 由位置定死，三种 multi 形状逐格一致。
    """
    got = _run(build(**shape), 'show lan "" base_swimsuit happy')
    assert got['broken'] == [2], (got['broken'], got['values'])
    assert got['values'].get(2) == '', got['values']


def test_三种multi形状与参照系逐格相同():
    """
    同一行在 `multi` 的不同组合下必须给出**同一组格子值**。

    这正是这一轮的病灶：勾一个"可有多个"，同一句话的意思就变了
    （服装=base_swimsuit、表情空 vs 服装空、表情=base_swimsuit）。
    勾选项只能影响"能吃几个"，**不能影响哪一格死**。
    """
    ref = _run(build(), 'show lan "" base_swimsuit happy')
    for shape in (dict(outfit_multi=True),
                  dict(outfit_multi=True, face_multi=True),
                  dict(role_multi=True, outfit_multi=True, face_multi=True)):
        got = _run(build(**shape), 'show lan "" base_swimsuit happy')
        assert got['values'] == ref['values'], (shape, got['values'],
                                                ref['values'])
        assert got['leftover'] == ref['leftover'], (shape, got['leftover'],
                                                    ref['leftover'])
        assert got['broken'] == ref['broken'], (shape, got['broken'],
                                                ref['broken'])


def test_多值格不许把空引号让给下一格():
    """
    同一条规矩，用"多值格能不吃"这个具体口子说一遍：

        多值格空着 = 0；吃下空引号 = 0 → 平局。
        `offer` 的规则是"同价取先试"，而"不吃"排在最后试 ——
        于是看起来该吃。但**吃空引号之后**那一格就死了、不能再吃后面的
        `base_swimsuit`（`base_swimsuit` 要 5 点），而不吃空引号、让表情吃它，
        服装还能白拿 `base_swimsuit`（0）→ 总价更低。

    所以不是"先试顺序"的问题，是**DP 有权选择谁死**。
    修法：空引号落下时，这一格没有别的分支可走。
    """
    got = _run(build(outfit_multi=True), 'show lan "" base_swimsuit happy')
    assert 2 in got['broken'], got['broken']
    assert 3 not in got['broken'], got['broken']


# ===========================================================================
#  第二层：dead 格的候选词，对别人是"没人要的自由词"
# ===========================================================================

def test_死格的候选词对别人是自由文本_不是候选外():
    """
    白盒：`base_swimsuit` 属于死掉的「服装」。

    `_tok_cost` 修之前对它返回 `COST_OFFLIST(5)`（"候选外的词"），因为
    `foreign` 已经把 dead 减掉了，于是落到最后一档。可它明明**已经没人要**了。

    期望 `COST_UNKNOWN(2)`：
        多值格吃它 = 2 < 空着 + 晾着(3) → 接住；
        必填格吃它 = 2 < 空着(7) + 晾着(3) → 接住。
    """
    slots = P.parse_pattern(build(outfit_multi=True))
    owners = P._owners_of(slots, {})
    assert owners['base_swimsuit'] == {2}, owners['base_swimsuit']
    cost = P._tok_cost('base_swimsuit', 3, {'happy'}, owners, False, dead={2})
    assert cost == P.COST_UNKNOWN, (
        f'死格的候选词被算成 {cost}（COST_OFFLIST={P.COST_OFFLIST}）——'
        f' 多值格会宁可空着（0）也不接（{P.COST_OFFLIST} > 晾着 {P.SKIP_WORD}）')


def test_空着的那一格死了_后面那格要按位置接住():
    """
    「服装」被空引号废掉之后，`base_swimsuit` 是它的候选，已经没人要。
    后面那格（表情，多值）应该按位置接住它 —— 而不是"两词都晾着"。

    这条与上面那条是同一件事的正反面：白盒钉代价，这里钉结果。
    """
    got = _run(build(outfit_multi=True, face_multi=True),
               'show lan "" base_swimsuit happy')
    assert got['values'].get(3) == 'base_swimsuit', (got['values'],
                                                    got['leftover'])
    assert got['leftover'] == ['happy'], got['leftover']


# ===========================================================================
#  护栏
# ===========================================================================

def test_都不multi时的参照行为不许变():
    """
    这是"目标形状"的参照系（当前已经对）：空引号废服装、表情接住、
    happy 多余。上面两条修完，多值格必须与它一致。
    """
    got = _run(build(), 'show lan "" base_swimsuit happy')
    assert got['broken'] == [2], got['broken']
    assert got['values'].get(2) == '', got['values']
    assert got['values'].get(3) == 'base_swimsuit', got['values']
    assert got['leftover'] == ['happy'], got['leftover']


def test_多值格没有自己的词时仍然允许空着():
    """
    口径护栏：`show lan happy` 里服装（multi）没有属于自己的词 → 空着，
    不许因为修"接住"就顺手把 `happy` 抢进服装。
    """
    pattern = [SHOW,
               {'kind': 'variable', 'column': '角色', 'options': ['lan']},
               {'kind': 'choice', 'column': '服装',
                'options': ['base_swimsuit'], 'multi': True},
               {'kind': 'choice', 'column': '表情', 'options': ['happy']}]
    got = _run(pattern, 'show lan happy')
    assert got['values'].get(2, '') == '', got['values']
    assert got['values'].get(3) == 'happy', got['values']
    assert got['exact'] is True, got


def test_没有空引号的行一个数都不许动():
    """修 dead 相关逻辑时最容易误伤的：普通行、垃圾词、变量格抢词。"""
    plain = _run(build(outfit_multi=True), 'show lan base_swimsuit happy')
    assert plain['values'].get(2) == 'base_swimsuit', plain['values']
    assert plain['values'].get(3) == 'happy', plain['values']
    assert plain['exact'] is True, plain

    junk = _run(build(outfit_multi=True, face_multi=True),
                'show lan base_swimsuit happy junkword')
    assert junk['leftover'] == ['junkword'], junk

    guard = P.parse_pattern([
        {'kind': 'variable', 'column': '人物'},
        {'kind': 'choice', 'column': '表情', 'options': ['happy', 'sad'],
         'multi': True},
        {'kind': 'optional', 'column': '位置', 'text': 'at'}])
    mypose = P.assign_tokens(['happy', 'sad', 'at', 'left'], guard)
    assert mypose['values'] == {0: 'happy', 1: 'sad', 2: 'at left'}, mypose
