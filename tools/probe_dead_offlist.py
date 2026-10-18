# -*- coding: utf-8 -*-
"""
探针：`dead`（被空引号废掉的格子）之后的"接住"为什么在 multi 上失效
================================================================================

修之前（用户 2026-10-18 观察）：
    `show lan "" base_swimsuit happy`，服装勾"可有多个" →
    服装=空、表情=空、`base_swimsuit`/`happy` 两个词都晾着。

⚠ 2026-10-18 第四轮修完：`_tok_cost` 里已经内置这条降级（只属于死格的候选
   词 → `COST_UNKNOWN`），所以下面 ① 与 ② 现在**应当逐格相同**；两者不同
   就说明代码被改回去了。这个探针留着当对照。

`assign_tokens` 的注释说：dead 格的候选词"变成没人要的，后面那一格**按位置接住**"。
但接住只在**必填格**成立：

    必填格：吃候选外的词 = COST_OFFLIST(5)；不吃 = EMPTY_SLOT(7) + 晾着(3) = 10 → 吃
    多值格：吃候选外的词 = COST_OFFLIST(5)；不吃 = 0 + 晾着(3) = 3            → 不吃

所以问题不在"多值格空着免费"这一条口径本身，而在 **dead 格的候选词对别人来说
仍然按"候选外的词"（5）计价** —— 可它明明已经没人要了。

本探针做两件事：
  ① 把现状打出来（含 cost / broken / spans）；
  ② **在内存里**把"属于 dead 格的候选词"降级成 `COST_UNKNOWN(2)`，
     看结果是否恢复"按位置接住"，并跑几条回归。

⚠ 只读 + 内存 patch，不写项目文件。
跑法：`python tools/probe_dead_offlist.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

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


def show(title, pattern, line):
    slots = P.parse_pattern(pattern)
    toks = P.tokenize(line, slots)
    got = P.assign_tokens(toks, slots)
    print(f'--- {title}')
    print(f'    tokens  = {[P._show_token(t) for t in toks]}')
    print(f'    exact={got["exact"]} cost={got["cost"]} broken={got["broken"]}')
    print(f'    values  = {got["values"]}')
    print(f'    spans   = {got["spans"]}')
    print(f'    leftover= {got["leftover"]}  unfilled={got["unfilled"]}')
    return got


def patch_dead_offlist():
    """内存 patch：属于 dead 格的候选词 → 自由文本（2），而不是候选外（5）。"""
    orig = P._tok_cost

    def patched(tok, si, allowed, owners, limited, dead=()):
        dead_set = set(dead or ())
        if tok != P.EMPTY_TOKEN and dead_set and owners.get(tok) \
                and (owners[tok] & dead_set) \
                and not (owners[tok] - {si} - dead_set):
            return P.COST_UNKNOWN
        return orig(tok, si, allowed, owners, limited, dead)

    P._tok_cost = patched
    return orig


def main():
    print('########## ① 真实行为（A+B 已落地） ##########')
    for tag, pat in [
            ('服装 multi', build(outfit_multi=True)),
            ('服装 multi + 表情 multi', build(outfit_multi=True, face_multi=True)),
            ('三格全 multi',
             build(role_multi=True, outfit_multi=True, face_multi=True)),
            ('都不 multi', build()),
    ]:
        show(tag, pat, 'show lan "" base_swimsuit happy')

    print('\n########## ② 同样的降级在内存里再打一遍（应与 ① 逐格相同） ##########')
    orig = patch_dead_offlist()
    try:
        for tag, pat in [
                ('服装 multi', build(outfit_multi=True)),
                ('服装 multi + 表情 multi',
                 build(outfit_multi=True, face_multi=True)),
                ('三格全 multi',
                 build(role_multi=True, outfit_multi=True, face_multi=True)),
                ('都不 multi', build()),
        ]:
            show(tag, pat, 'show lan "" base_swimsuit happy')

        print('\n########## ③ patch 之后的回归（这些必须没变） ##########')
        cases = [
            ('垃圾词晾着', build(outfit_multi=True),
             'show lan base_swimsuit happy junkword'),
            ('中间夹垃圾', build(outfit_multi=True),
             'show lan clothes2 junk happy'),
            ('正常行', build(), 'show lan base_swimsuit happy'),
        ]
        for tag, pat, line in cases:
            slots = P.parse_pattern(pat)
            got = P.assign_tokens(P.tokenize(line, slots), slots)
            print(f'    {tag:12s} exact={got["exact"]} values={got["values"]} '
                  f'left={got["leftover"]}')

        # 护栏：变量格抢词 / 偷词
        guard = P.parse_pattern([
            {'kind': 'variable', 'column': '人物'},
            {'kind': 'choice', 'column': '表情', 'options': ['happy', 'sad'],
             'multi': True},
            {'kind': 'optional', 'column': '位置', 'text': 'at'}])
        got = P.assign_tokens(['happy', 'sad', 'at', 'left'], guard)
        print(f'    mypose 护栏  exact={got["exact"]} values={got["values"]}')

        steal = P.parse_pattern([
            SHOW,
            {'kind': 'variable', 'column': '角色', 'options': ['lan']},
            {'kind': 'choice', 'column': '服装',
             'options': ['base_swimsuit']}])
        got = P.assign_tokens(P.tokenize('show lan lan', steal), steal)
        print(f'    偷词护栏    exact={got["exact"]} values={got["values"]}')
    finally:
        P._tok_cost = orig


if __name__ == '__main__':
    main()
