"""
tests/test_at_merge.py
======================
**合并回源**（2026-10-31，队列第 5 项）：把本景后面那几处单独改写的 `at`
改成和源一样。

为什么要有这一步：`story.override_advice` 只报数 —— "还有 2 处单独改了 CG 的
尺寸，改源只到第 943 拍有效"。人看完只能一处一处手改。这一步补上后半程，
但它是 **L4**（`docs/超清CG与BG_零侵入导入_2026-10-31.md` §5.5：动用户的
`show` 行 → confirm、永远、逐处），所以判据分五层钉：

  ① `set_item_at` —— 改写`show`字段里**那一条**的唯一实现：改 `@at`、
     **绝不新增/删除子句**、复杂子句与 expression 拒绝、ATL 冒号不许吃；
  ② `source_at_of` —— 目标值是**源那一行自己写着的**，不是"当前这一拍
     看到的"（那一拍可能已经在某处改写之后了）；
  ③ `plan_at_merge` —— 处数与 `override_advice` **同一个来源**（steps +
     skipped == advice.count）、改不了的带原因**不静默跳过**；
  ④ `apply(selected=…)` / `revert` —— 只写勾上的；撤销逐字还原；
  ⑤ **跨层一致性**：合并之后，舞台快照里本景每一拍的背景 at 都等于源的值
     （这一条才是"改完真的整景一样"）。

依据：`docs/超清CG与BG_零侵入导入_2026-10-31.md` §5.5 与 §八。
"""

from __future__ import annotations

import pytest

from storygraph.story.at_merge import (MergePlan, MergeSkip, MergeStep,
                                       plan_at_merge, source_at_of)
from storygraph.story.beat_model import Beat
from storygraph.story.beat_stage_parse import (AT_BLOCKED, AT_COMPLEX,
                                                AT_MISSING, AT_NOOP, AT_OK,
                                                set_item_at, show_part_info)
from storygraph.story.override_advice import override_advice
from storygraph.story.stage_snapshot import stage_snapshots


def _beats(*rows):
    """`{'scene': …, 'show': …}` 简写 → Beat 列表（拍号 = 下标）。"""
    out = []
    for spec in rows:
        kw = {}
        for key in ('scene', 'scene_at', 'show', 'hide', 'at', 'transform'):
            if key in spec:
                kw[key] = spec[key]
        out.append(Beat(**kw))
    return out


# ==================================================================
#  ① 改写一条 show 的 at（唯一实现）
# ==================================================================

class TestSetItemAt:
    def test_it_rewrites_the_at_of_that_item(self):
        res = set_item_at('cg cg1@half', 'cg', 'fit')
        assert res.status == AT_OK
        assert res.text == 'cg cg1@fit'
        assert res.before_at == 'half'

    def test_empty_at_strips_the_item_at(self):
        """「原样」= 去掉 `@at`（不是写一个空 transform）。"""
        res = set_item_at('cg cg1@half', 'cg', '')
        assert res.status == AT_OK
        assert res.text == 'cg cg1'
        assert res.before_at == 'half'

    def test_adding_an_at_to_a_bare_item(self):
        res = set_item_at('cg cg1', 'cg', 'half')
        assert res.status == AT_OK and res.text == 'cg cg1@half'
        assert res.before_at == ''

    def test_the_other_items_are_left_verbatim(self):
        res = set_item_at('bg a@x; cg cg1@half; ui bar', 'cg', 'fit')
        assert res.text == 'bg a@x; cg cg1@fit; ui bar'

    def test_the_last_show_of_the_same_tag_wins(self):
        """同一个 tag 写了两条：Ren'Py 侧后 show 的赢 —— 改的也必须是那一条。"""
        res = set_item_at('cg cg1@a; cg cg2@b', 'cg', 'fit')
        assert res.text == 'cg cg1@a; cg cg2@fit'

    def test_it_never_adds_or_removes_clauses(self):
        """合并是"对齐写法"，不是"给用户加/减语句"。"""
        for text in ('cg cg1@half', 'bg a@x; cg cg1@half; ui bar',
                     'cg cg1', 'ui bar; cg cg1@z; bg a@y'):
            before = len([p for p in text.split(';')])
            res = set_item_at(text, 'cg', 'fit')
            after = len([p for p in res.text.split(';')])
            assert after == before, (text, res)

    def test_a_full_clause_is_refused_not_guessed(self):
        res = set_item_at('cg cg1 at half', 'cg', 'fit')
        assert res.status == AT_COMPLEX
        assert res.text == 'cg cg1 at half'         # 原文一个字不动

    def test_a_trailing_colon_for_an_atl_block_survives(self):
        """`show cg cg1@half:` 后面跟着 ATL 块 —— 冒号丢了会变成无主缩进。"""
        res = set_item_at('cg cg1@half:', 'cg', 'fit')
        assert res.status == AT_OK
        assert res.text == 'cg cg1@fit:'
        res = set_item_at('cg cg1@half:', 'cg', '')
        assert res.text == 'cg cg1:'
        assert show_part_info('cg cg1@half:')['block'] is True

    def test_a_missing_object_is_reported(self):
        res = set_item_at('ui bar', 'cg', 'fit')
        assert res.status == AT_MISSING
        assert res.text == 'ui bar'

    def test_the_same_value_is_a_noop(self):
        res = set_item_at('cg cg1@half', 'cg', 'half')
        assert res.status == AT_NOOP and res.text == 'cg cg1@half'

    def test_no_text_or_no_tag_is_missing(self):
        assert set_item_at('', 'cg', 'fit').status == AT_MISSING
        assert set_item_at('cg cg1@half', '', 'fit').status == AT_MISSING

    def test_a_beat_level_at_blocks_clearing_to_as_is(self):
        """
        拍级 `at` / `transform` 会顶替 item 的空 at（`item_at` 的优先级）：
        要"原样"时只改 item 改不动 —— 必须报 blocked，不能报成功。
        """
        beat = Beat(show='cg cg1', at='half')
        assert set_item_at('cg cg1', 'cg', '', beat).status == AT_BLOCKED
        # 反向：要写入一个值时不受影响（item 的 @at 压过拍级）
        res = set_item_at('cg cg1', 'cg', 'fit', beat)
        assert res.status == AT_OK and res.text == 'cg cg1@fit'


# ==================================================================
#  ② 目标值 = 源那一行自己写着的
# ==================================================================

class TestSourceAt:
    def test_a_scene_line_uses_its_scene_at(self):
        beats = _beats({'scene': 'bg a', 'scene_at': 'fit'})
        assert source_at_of(beats, 0, kind='scene') == 'fit'

    def test_a_show_background_uses_that_items_at(self):
        beats = _beats({'scene': 'bg a', 'scene_at': 'fit'})
        beats.append(Beat(show='bg a@half'))
        assert source_at_of(beats, 1, kind='scene') == 'half'

    def test_the_three_background_spellings_are_one_layer(self):
        beats = _beats({'show': 'background old@half'})
        assert source_at_of(beats, 0, kind='scene') == 'half'

    def test_a_layer_uses_its_own_tag(self):
        beats = _beats({'show': 'cg cg1@closeup; ui bar@x'})
        assert source_at_of(beats, 0, kind='layer', tag='cg') == 'closeup'
        assert source_at_of(beats, 0, kind='layer', tag='ui') == 'x'

    def test_out_of_range_answers_nothing(self):
        beats = _beats({})
        assert source_at_of(beats, -1) == ''
        assert source_at_of(beats, 9) == ''


# ==================================================================
#  ③ 计划：与 override_advice 同一个来源
# ==================================================================

#: 源在第 1 拍写 `bg a@fit`；第 4/6 拍又 show 了两次（`half` / `half`）。
SCENE = _beats(
    {'scene': 'bg a', 'scene_at': 'fit'},
    {},
    {},
    {},
    {'show': 'bg a@half'},
    {},
    {'show': 'bg a@half'},
    {},
)


class TestPlan:
    def test_it_rewrites_exactly_the_sites_that_differ(self):
        plan = plan_at_merge(SCENE, 0, label='背景')
        assert plan.ok
        assert plan.beats == (4, 6)
        assert all(s.after_at == 'fit' for s in plan.steps)
        assert plan.steps[0].before == 'bg a@half'
        assert plan.steps[0].after == 'bg a@fit'

    def test_the_count_is_the_same_number_the_panel_reported(self):
        """
        面板那句话里的数字 == 这里真改的处数（**同一个来源**）。

        这条最贵：报 2 处却只改 1 处，"改好了"和"其实没改完"就又长得一样了。
        """
        advice = override_advice(SCENE, 0, kind='scene',
                                 source_at='fit')
        plan = plan_at_merge(SCENE, 0, label='背景')
        assert len(plan.steps) + len(plan.skipped) == advice.count

    def test_sites_that_already_hold_the_value_are_left_alone(self):
        beats = _beats({'scene': 'bg a', 'scene_at': 'fit'},
                       {'show': 'bg a@half'},
                       {'show': 'bg a@fit'})       # 第 2 拍本来就一样
        plan = plan_at_merge(beats, 0)
        assert plan.beats == (1,)

    def test_a_complex_clause_is_skipped_with_a_reason(self):
        beats = _beats({'scene': 'bg a', 'scene_at': 'fit'},
                       {'show': 'bg a@half'},
                       {'show': 'bg a behind ye'})  # 复杂子句
        plan = plan_at_merge(beats, 0)
        assert plan.beats == (1,)
        assert plan.skipped_count == 1
        assert '高级区' in plan.skipped[0].reason
        # 两边加起来仍是 advice 报的那个数
        assert len(plan.steps) + len(plan.skipped) == override_advice(
            beats, 0, source_at='fit').count

    def test_nothing_to_merge_says_so_instead_of_nothing(self):
        beats = _beats({'scene': 'bg a', 'scene_at': 'fit'})
        plan = plan_at_merge(beats, 0)
        assert not plan.ok
        assert '没有要合并的' in plan.reason

    def test_all_sites_already_agree_says_so(self):
        beats = _beats({'scene': 'bg a', 'scene_at': 'fit'},
                       {'show': 'bg a@fit'})
        plan = plan_at_merge(beats, 0)
        assert not plan.ok
        assert '已经都写着' in plan.reason

    def test_every_site_unmergeable_says_so(self):
        beats = _beats({'scene': 'bg a', 'scene_at': 'fit'},
                       {'show': 'bg a behind ye'})
        plan = plan_at_merge(beats, 0)
        assert not plan.ok
        assert plan.skipped_count == 1
        assert '都改不了' in plan.reason

    def test_out_of_range_source_is_refused(self):
        plan = plan_at_merge(_beats({}), 5)
        assert not plan.ok and '越界' in plan.reason

    def test_a_layer_needs_a_tag(self):
        plan = plan_at_merge(_beats({'show': 'cg cg1@half'}), 0,
                             kind='layer', tag='')
        assert not plan.ok and 'tag' in plan.reason

    def test_a_source_at_containing_at_sign_is_refused(self):
        """源里的写法带 `@` = 紧凑写法，不敢照它改别的行（不猜）。"""
        beats = _beats({'scene': 'bg a'})
        beats[0].scene_at = 'x@y'
        plan = plan_at_merge(beats, 0)
        assert not plan.ok and '@' in plan.reason

    def test_it_does_not_touch_the_beats_while_planning(self):
        before = [b.show for b in SCENE]
        plan_at_merge(SCENE, 0)
        assert [b.show for b in SCENE] == before


# ==================================================================
#  ④ 只写勾上的 / 撤销逐字还原
# ==================================================================

class TestApplyAndRevert:
    def test_apply_writes_every_step(self):
        beats = _beats({'scene': 'bg a', 'scene_at': 'fit'},
                       {'show': 'bg a@half'},
                       {'show': 'bg a@half'})
        plan = plan_at_merge(beats, 0)
        assert plan.apply()
        assert [b.show for b in beats] == [''] * 1 + ['bg a@fit'] * 2

    def test_apply_honours_the_ticks(self):
        beats = _beats({'scene': 'bg a', 'scene_at': 'fit'},
                       {'show': 'bg a@half'},
                       {'show': 'bg a@half'})
        plan = plan_at_merge(beats, 0)
        assert plan.apply(selected={1})
        assert beats[1].show == 'bg a@fit'
        assert beats[2].show == 'bg a@half'          # 勾掉的**一个字都不动**
        assert plan.beats == (1,)                    # 撤销栈只记改过的

    def test_apply_with_nothing_ticked_changes_nothing(self):
        beats = _beats({'scene': 'bg a', 'scene_at': 'fit'},
                       {'show': 'bg a@half'})
        plan = plan_at_merge(beats, 0)
        assert plan.apply(selected=set()) is False
        assert beats[1].show == 'bg a@half'

    def test_revert_restores_every_line(self):
        beats = _beats({'scene': 'bg a', 'scene_at': 'fit'},
                       {'show': 'bg a@half; ui bar'},
                       {'show': 'bg a@half'})
        before = [b.show for b in beats]
        plan = plan_at_merge(beats, 0)
        plan.apply()
        assert plan.revert()
        assert [b.show for b in beats] == before

    def test_revert_is_idempotent(self):
        beats = _beats({'scene': 'bg a', 'scene_at': 'fit'},
                       {'show': 'bg a@half'})
        plan = plan_at_merge(beats, 0)
        plan.apply()
        plan.revert()
        plan.revert()
        assert beats[1].show == 'bg a@half'


# ==================================================================
#  ⑤ 跨层一致性：合并之后整景真的一个值
# ==================================================================

class TestCrossLayer:
    def test_after_merging_every_frame_shows_the_same_at(self):
        """
        合并前：第 5 拍起背景变成 `half`；合并后：整景都是源的 `fit`。

        判据取的是**舞台快照**（渲染器看到的那个值），不是我们自己算的。
        """
        beats = _beats(
            {'scene': 'bg a', 'scene_at': 'fit'},
            {}, {},
            {'show': 'bg a@half'},
            {},
            {'show': 'bg a@half'},
        )
        ats_before = [r['state']['scene_at'] for r in stage_snapshots(beats)]
        assert ats_before[0] == 'fit' and ats_before[-1] == 'half'
        plan = plan_at_merge(beats, 0)
        assert plan.apply()
        ats_after = [r['state']['scene_at'] for r in stage_snapshots(beats)]
        assert set(ats_after) == {'fit'}

    def test_the_panel_stops_warning_after_the_merge(self):
        """
        合并后那句提示**要换口气**：不再是"⚠ 有 2 处单独改了"，而是
        "还有 2 处写着同一个 at" —— 陷阱还在（源下次再改仍只到第一处），
        但这次没踩。仍显示、不消失，也不该继续吓人。
        """
        beats = _beats({'scene': 'bg a', 'scene_at': 'fit'},
                       {'show': 'bg a@half'},
                       {'show': 'bg a@half'})
        before = override_advice(beats, 0, source_at='fit')
        assert before.agreed is False
        assert '单独改了' in before.short(label='背景')
        plan = plan_at_merge(beats, 0, label='背景')
        assert plan.apply()
        after = override_advice(beats, 0, source_at='fit')
        assert after.agreed is True
        assert after.count == 2                      # 数字没变（仍是 2 处写法）
        line = after.short(label='背景')
        assert '⚠' not in line
        assert '写着同一个 at' in line
        assert '再改源只到第 2 拍有效' in line
