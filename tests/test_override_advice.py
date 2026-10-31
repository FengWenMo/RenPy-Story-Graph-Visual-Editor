"""
tests/test_override_advice.py
=============================
**本景还有几处"单独改写"**（2026-10-31，队列第 4 项）。

为什么必须钉死：尺寸面板改的是**源**那一行（尺寸是持续状态），可本景后面
只要有谁又 `show` 了一次同一个对象，Ren'Py 就重建 displayable —— 前面那行
`at` 从那一刻起不再生效。用户改完看的是当下那一拍，于是"改好了"和"其实只
改到第 12 拍"长得一模一样。

分五层钉：

  ① `story.override_advice.override_map` —— 边界（下一次 scene / hide /
     同一拍里 show 与 hide 的先后）、背景三种 tag 同桶、图层按 tag 分桶；
  ② `override_advice` 的**档位**（`none` / `hint` / `notice`）+ 算不出来时
     **降档到 hint**（文档 §5.5：不许把"不知道"说成"没有"）；
  ③ 措辞：**报的数字 == 算出的处数**，`new_at` 给了就说"哪几处会盖回"；
  ④ `size_rows` / `AtEdit` 两条接线（面板那句 + 确认框那句）；
  ⑤ **跨层一致性**：数出来的每一拍，在舞台快照里确实把该对象的 at 改成了
     那个值 —— 这一条才是"报的数字 == 实际的数字"。

依据：`docs/超清CG与BG_零侵入导入_2026-10-31.md` §5.5（档位 / 上浮 / 防误报）
与 §八（判据）。
"""

from __future__ import annotations

import pytest

from storygraph.story.beat_model import Beat
from storygraph.story.beat_stage_parse import item_at
from storygraph.story.override_advice import (HINT, NONE, NOTICE, NOTICE_AT,
                                              override_advice, override_map)
from storygraph.story.stage_snapshot import stage_snapshots


def _beats(*rows):
    """`(scene/show/hide, scene_at/at)` 简写 → Beat 列表（拍号 = 下标）。"""
    out = []
    for spec in rows:
        kw = {}
        for key in ('scene', 'scene_at', 'show', 'hide', 'at', 'transform'):
            if key in spec:
                kw[key] = spec[key]
        out.append(Beat(**kw))
    return out


#: 一景 8 拍：第 0 拍 scene（无 at）+ 第 3/5/6 拍各写一次 `show bg … at half`
SCENE_THREE = _beats(
    {'scene': 'bg bg7_0'},
    {}, {},
    {'show': 'bg bg7_0 at half'},
    {},
    {'show': 'bg bg7_0 at half'},
    {'show': 'bg bg7_0 at half'},
    {},
)


# ==================================================================
#  ① 扫描边界
# ==================================================================

class TestOverrideMap:
    def test_sites_are_the_beats_that_rewrite_the_background(self):
        table = override_map(SCENE_THREE, 0)
        assert [s.index for s in table['scene']] == [3, 5, 6]
        assert all(s.at == 'half' for s in table['scene'])

    def test_the_source_beat_itself_is_not_a_site(self):
        """第 0 拍自己就是"源"—— 它不属于"后面还有几处"。"""
        assert 0 not in [s.index for s in override_map(SCENE_THREE, 0)['scene']]

    def test_a_new_scene_stops_the_scan(self):
        beats = _beats({'scene': 'bg a'}, {}, {'show': 'bg a at half'},
                       {'scene': 'bg b'}, {'show': 'bg b at half'})
        assert [s.index for s in override_map(beats, 0)['scene']] == [2]

    def test_three_background_spellings_share_one_bucket(self):
        """`bg` / `background` / `bg_` 是同一个图层（§5.5 那条"同一批行"）。"""
        beats = _beats({'scene': 'bg a'}, {'show': 'background a at half'},
                       {'show': 'bg_ a at half'})
        table = override_map(beats, 0)
        assert list(table) == ['scene']
        assert [s.index for s in table['scene']] == [1, 2]

    def test_layers_are_bucketed_by_their_own_tag(self):
        beats = _beats({'scene': 'bg a'}, {'show': 'cg cg1 at x'},
                       {'show': 'ui bar at y'}, {'show': 'cg cg1 at z'})
        table = override_map(beats, 0)
        assert [s.index for s in table['cg']] == [1, 3]
        assert [s.index for s in table['ui']] == [2]
        assert 'scene' not in table

    def test_hide_is_neither_a_site_nor_a_boundary(self):
        """撤下之后再 `show` 一次，仍是另一处单独改写（`hide` 不改变口径）。"""
        beats = _beats({'show': 'cg cg1 at x'}, {'show': 'cg cg1 at y'},
                       {'hide': 'cg'}, {'show': 'cg cg1 at z'})
        assert [s.index for s in override_map(beats, 0)['cg']] == [1, 3]

    def test_show_and_hide_in_the_same_beat_still_counts_the_show(self):
        """同一拍 `hide` + `show`：这个 show 确实重写过它，算一处。"""
        beats = _beats({'show': 'cg cg1 at x'}, {},
                       {'hide': 'cg', 'show': 'cg cg1 at y',
                        'op_order': 'hide,show'})
        assert [s.index for s in override_map(beats, 0)['cg']] == [2]

    def test_start_filters_earlier_beats(self):
        beats = _beats({'show': 'cg cg1 at x'}, {'show': 'cg cg1 at y'},
                       {'show': 'cg cg1 at z'})
        assert [s.index for s in override_map(beats, 1)['cg']] == [2]


# ==================================================================
#  ② 档位（文档 §5.5：档位给下限，后果决定上浮）
# ==================================================================

class TestLevels:
    def test_no_override_is_silence(self):
        advice = override_advice(_beats({'scene': 'bg a'}, {}, {}), 0)
        assert advice.level == NONE
        assert advice.count == 0
        assert advice.text() == '' and advice.short() == ''

    @pytest.mark.parametrize('n,level', [(1, HINT), (2, HINT), (3, NOTICE)])
    def test_level_follows_the_count(self, n, level):
        beats = _beats({'scene': 'bg a'}, *[{'show': 'bg a at half'}] * n)
        advice = override_advice(beats, 0)
        assert advice.count == n
        assert advice.level == level

    def test_notice_threshold_is_three(self):
        assert NOTICE_AT == 3

    def test_only_beats_after_the_source_count(self):
        beats = _beats({'show': 'bg a at half'}, {'scene': 'bg a'},
                       {'show': 'bg a at half'})
        advice = override_advice(beats, 1)
        assert advice.beats == (2,)

    def test_two_sites_are_a_hint_not_a_notice(self):
        beats = _beats({'scene': 'bg a'}, {'show': 'bg a at half'},
                       {'show': 'bg a at half'})
        assert override_advice(beats, 0).level == HINT

    # ---------- 算不出来：降档，但不许沉默 ----------

    def test_a_missing_beat_table_is_a_hint_that_says_so(self):
        advice = override_advice(None, 0)
        assert advice.level == HINT and advice.unknown is True
        assert '统计不出' in advice.short()

    def test_an_out_of_range_source_is_unknown_not_zero(self):
        advice = override_advice(_beats({}), 9)
        assert advice.unknown is True and advice.level == HINT

    def test_a_cross_chapter_source_says_nothing_and_never_claims_zero(self):
        """
        `source = -1`（上一章继承来的）—— 这一行**本来就改不了**，
        所以没有"改源被盖回"这回事；但也不许冒充"0 处"（`reason` 留着）。
        """
        advice = override_advice(_beats({'show': 'bg a at half'}), -1)
        assert advice.level == NONE and advice.count == 0
        assert advice.reason and advice.text() == ''


# ==================================================================
#  ③ 措辞（报的数字 == 算出的处数；给了 new_at 就说谁会盖回）
# ==================================================================

class TestWordings:
    def test_the_number_in_the_text_is_the_number_of_sites(self):
        advice = override_advice(SCENE_THREE, 0)
        assert f'{advice.count} 处' in advice.text()
        assert f'{advice.count} 处' in advice.short()
        assert str(advice.count) in advice.short()

    def test_the_first_site_is_where_the_change_stops_working(self):
        advice = override_advice(SCENE_THREE, 0)
        assert advice.ends_at == 3
        assert '第 4 拍' in advice.short()      # 给人看的是 1 基

    def test_short_is_one_line_for_the_narrow_panel(self):
        short = override_advice(SCENE_THREE, 0).short(label='背景')
        assert '\n' not in short
        assert '第 4、6、7 拍' in short

    def test_a_notice_also_says_how_to_do_it_properly(self):
        full = override_advice(SCENE_THREE, 0).text(label='背景')
        assert '合并回源' in full
        assert '不替你删' in full               # 只报数，不代删（L4 纪律）

    def test_without_new_at_it_does_not_pretend_to_know_who_wins(self):
        full = override_advice(SCENE_THREE, 0).text(label='背景')
        assert '盖回去。' not in full or '会把这' in full

    def test_with_new_at_it_names_only_the_sites_that_differ(self):
        beats = _beats({'scene': 'bg a'}, {'show': 'bg a at half'},
                       {'show': 'bg a at bgfit'}, {'show': 'bg a at half'})
        advice = override_advice(beats, 0)
        assert [s.index for s in advice.conflicts('half')] == [2]
        assert [s.index for s in advice.agreeing('half')] == [1, 3]
        text = advice.text(new_at='half', label='背景')
        assert '第 3 拍' in text and '盖回去' in text

    def test_picking_exactly_what_is_already_there_is_good_news(self):
        beats = _beats({'scene': 'bg a'}, {'show': 'bg a at half'})
        text = override_advice(beats, 0).text(new_at='half', label='背景')
        assert '不会盖回' in text

    def test_clearing_the_at_conflicts_with_every_site_that_has_one(self):
        beats = _beats({'scene': 'bg a', 'scene_at': 'half'},
                       {'show': 'bg a at half'}, {'show': 'bg a'})
        advice = override_advice(beats, 0)
        assert [s.index for s in advice.conflicts('')] == [1]
        assert [s.index for s in advice.agreeing('')] == [2]

    def test_mixed_ats_are_reported_as_mixed(self):
        beats = _beats({'scene': 'bg a'}, {'show': 'bg a at half'},
                       {'show': 'bg a at bgfit'})
        assert override_advice(beats, 0).same_at is False
        assert '各不相同' in override_advice(beats, 0).text(label='背景')

    def test_many_sites_stop_naming_them_after_four(self):
        beats = _beats({'scene': 'bg a'}, *[{'show': 'bg a at half'}] * 7)
        short = override_advice(beats, 0).short(label='背景')
        assert '等 7 处' in short
        assert '第 2、3、4、5 拍' in short

    def test_the_layer_label_is_used_verbatim(self):
        beats = _beats({'show': 'cg cg1 at x'}, {'show': 'cg cg1 at y'})
        advice = override_advice(beats, 0, kind='layer', tag='cg')
        assert 'CG' in advice.short(label='CG')


# ==================================================================
#  ④ 两条接线：尺寸行 + 确认框
# ==================================================================

class TestWiringIntoTheSizeRow:
    def test_size_rows_attaches_the_advice_per_row(self, tmp_path):
        from PIL import Image
        pytest.importorskip('PIL')
        from storygraph.app.stage import StageFrame
        from storygraph.app.stage_render import size_rows

        root = tmp_path / 'proj'
        game = root / 'game'
        (game / 'images').mkdir(parents=True)
        (game / 'script.rpy').write_text(
            'image bg a = "images/a.png"\nlabel start:\n    return\n',
            encoding='utf-8')
        Image.new('RGBA', (400, 300), (255, 0, 0, 255)).save(
            game / 'images' / 'a.png')
        frame = StageFrame(index=0, line_id='t.0001', label='t', speaker='',
                           text='', scene='bg a', scene_src=0)
        beats = _beats({'scene': 'bg a'}, {'show': 'bg a at half'},
                       {'show': 'bg a at half'})
        rows = size_rows(frame, project_root=root, screen=(400, 300),
                         beats=beats)
        assert rows and rows[0]['advice'] is not None
        assert rows[0]['advice'].count == 2

    def test_without_beats_there_is_no_advice_at_all(self, tmp_path):
        """单测/探针只关心"行怎么算出来" → 不显示，也**不冒充 0 处**。"""
        from PIL import Image
        pytest.importorskip('PIL')
        from storygraph.app.stage import StageFrame
        from storygraph.app.stage_render import size_rows

        root = tmp_path / 'proj'
        game = root / 'game'
        (game / 'images').mkdir(parents=True)
        (game / 'script.rpy').write_text(
            'image bg a = "images/a.png"\nlabel start:\n    return\n',
            encoding='utf-8')
        Image.new('RGBA', (400, 300), (255, 0, 0, 255)).save(
            game / 'images' / 'a.png')
        frame = StageFrame(index=0, line_id='t.0001', label='t', speaker='',
                           text='', scene='bg a', scene_src=0)
        rows = size_rows(frame, project_root=root, screen=(400, 300))
        assert rows and rows[0]['advice'] is None


class TestWiringIntoTheConfirmBox:
    TRANSFORMS = 'transform bgfit:\n    fit "cover"\n\ntransform half:\n    zoom 0.5\n'

    @pytest.fixture()
    def transforms(self):
        from storygraph.story.atl import parse_transforms
        return parse_transforms(self.TRANSFORMS)

    def test_the_confirm_text_says_where_the_change_stops(self, transforms):
        from storygraph.app.stage import plan_at_edit
        beats = _beats({'scene': 'bg a'}, {}, {'show': 'bg a at half'},
                       {'show': 'bg a at half'}, {'show': 'bg a at half'},
                       {})
        edit = plan_at_edit(beats, 2, target='scene', transform='bgfit',
                            transform_index=transforms, source_index=0)
        assert edit.ok, edit.reason
        assert edit.advice is not None and edit.advice.count == 3
        body = edit.diff_text()
        assert '本景后面还有 3 处' in body
        # 先说会被谁盖回去，再说 diff（diff 里只有一行，看不出"只到第 3 拍"）
        assert body.index('本景后面还有') < body.index('---')

    def test_a_rejected_plan_does_not_print_advice(self, transforms):
        from storygraph.app.stage import plan_at_edit
        beats = _beats({'scene': 'bg a'}, {'show': 'bg a at half'})
        edit = plan_at_edit(beats, 1, target='scene', transform='nope',
                            transform_index=transforms, source_index=0)
        assert not edit.ok
        assert '本景后面还有' not in edit.diff_text()

    def test_no_advice_means_no_extra_sentence(self, transforms):
        from storygraph.app.stage import AtEdit
        edit = AtEdit(beat_index=0, before='', after='half', ok=None) \
            if False else AtEdit(beat_index=0, field='scene_at', before='',
                                 after='half')
        assert edit.advice_lines() == []


# ==================================================================
#  ⑤ 跨层一致性：数出来的每一拍，快照里确实把 at 改成了那个值
# ==================================================================

class TestConsistencyWithTheSnapshot:
    def test_every_background_site_really_changes_scene_at(self):
        rows = stage_snapshots(SCENE_THREE)
        for site in override_map(SCENE_THREE, 0)['scene']:
            assert rows[site.index]['state']['scene_at'] == site.at

    def test_every_layer_site_really_changes_layer_at(self):
        beats = _beats({'show': 'cg cg1 at x'}, {}, {'show': 'cg cg1 at y'},
                       {'show': 'cg cg1 at z'})
        rows = stage_snapshots(beats)
        for site in override_map(beats, 0)['cg']:
            assert rows[site.index]['state']['layer_at']['cg'] == site.at

    def test_the_scan_uses_the_same_at_as_the_snapshot(self):
        """item 自己的 at > 拍级 at > 拍级 transform —— 与快照同一份答案。"""
        beat = Beat(show='bg a', at='from_beat_at')
        assert item_at({'at': ''}, beat) == 'from_beat_at'
        assert item_at({'at': 'own'}, beat) == 'own'
        assert item_at({'at': ''}, Beat(transform='from_transform')) == \
            'from_transform'
        rows = stage_snapshots([Beat(scene='bg a'),
                                Beat(show='bg a', at='from_beat_at')])
        assert rows[1]['state']['scene_at'] == 'from_beat_at'
        sites = override_map([Beat(scene='bg a'),
                              Beat(show='bg a', at='from_beat_at')], 0)['scene']
        assert [s.at for s in sites] == ['from_beat_at']
