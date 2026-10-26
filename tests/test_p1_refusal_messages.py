# -*- coding: utf-8 -*-
"""
P1 讨论②③：**拒因说人话**（2026-10-26）—— 行为面不动，只考文案。

路线（`docs/P1讨论_路线敲定_2026-10-26.md`）：
  · ② 重排被拒：点名具体行 + 为什么危险 + 两条绕路；**永不放宽**重排判据
    （空隙里那行 `$ renpy.pause` 跟谁走？答不上就不许动）；
  · ③ 条件改动：说清"去哪里导"（build/renpy），不能只说"走全量"。

判据只考**文案要素**（现象 / 危险 / 绕路 / 去处），不考行为 —— 行为面
"该拒的还是拒"由既有 `test_p0_round4` 等面守住。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story import patcher as P                      # noqa: E402


# =====================================================================
#  ② 重排拒因：点名 + 危险 + 绕路
# =====================================================================

class _B:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestReorderRefusalMessage:
    def test_gap_line_message_names_danger_and_workarounds(self):
        """非块头的空隙行：说清"改相对顺序、diff 看不出"，给绕路。"""
        from storygraph.story.patcher import _plan_reorders
        # 内存顺序倒置（先 e"1"(src4) 后 e"2"(src2)）→ 触发重排；
        # 两槽位之间夹一行 `$ pause`（认不出、非空非注释）→ 必须拒
        orig = ['label a:', '    e "2"', '    $ renpy.pause(hard=True)',
                '    e "1"']
        raw_gen = {0: ['e "1"'], 1: ['e "2"']}
        blocks = [
            (0, 4, 4, ['e "1"'], _B(kind='say', text='1', label='a',
                                    src_first=4, src_last=4)),
            (1, 2, 2, ['e "2"'], _B(kind='say', text='2', label='a',
                                    src_first=2, src_last=2)),
        ]
        plans, reasons = _plan_reorders(blocks, orig, raw_gen, {0: True, 1: True})
        assert plans == [], f'空隙里有 $ pause 的换序不许被计划：{plans}'
        assert reasons, '必须给出拒因'
        msg = ' '.join(str(r) for r in reasons)
        assert 'renpy.pause' in msg or '认不出的行' in msg, msg
        assert '相对顺序' in msg or '改剧情' in msg, msg
        assert '新插一张卡' in msg or '清掉' in msg, msg

    def test_cross_label_message_says_full_rewrite(self):
        from storygraph.story.patcher import _plan_reorders
        orig = ['label a:', '    e "2"', 'label b:', '    e "1"']
        raw_gen = {0: ['e "1"'], 1: ['e "2"']}
        blocks = [
            (0, 4, 4, ['e "1"'], _B(kind='say', text='1', label='a',
                                    src_first=4, src_last=4)),
            (1, 2, 2, ['e "2"'], _B(kind='say', text='2', label='a',
                                    src_first=2, src_last=2)),
        ]
        plans, reasons = _plan_reorders(blocks, orig, raw_gen, {0: True, 1: True})
        assert plans == []
        msg = ' '.join(str(r) for r in reasons)
        assert 'label' in msg, msg


# =====================================================================
#  ③ 条件拒因：说清"去哪里导"
# =====================================================================

class TestConditionRefusalMessage:
    def _bad(self):
        class _B:
            def __init__(self, **kw):
                self.__dict__.update(kw)
        return _B(condition='flag', nest_path='', src_first=2,
                  label='a', speaker='e', text='台词')

    def test_generic_fallback_names_the_export_destination(self):
        got = P._condition_hint([(self._bad(), 'flag2', 'flag')], '')
        assert 'build/renpy' in got, got
        assert '全量' in got, got

    def test_elif_says_chain_head_is_incremental(self):
        class _B:
            def __init__(self, **kw):
                self.__dict__.update(kw)
        original = 'label a:\n    if a:\n        e "1"\n    elif b:\n        e "2"\n'
        b = _B(condition='(b) and not (a)', nest_path='', src_first=5)
        got = P._condition_hint([(b, 'x', 'y')], original)
        assert 'elif' in got, got
        assert '链头' in got, got

    def test_nested_says_accumulated_condition(self):
        class _B:
            def __init__(self, **kw):
                self.__dict__.update(kw)
        b = _B(condition='flag', nest_path='if:1>A', src_first=2)
        got = P._condition_hint([(b, 'x', 'y')], '')
        assert '嵌' in got or '外层' in got, got
