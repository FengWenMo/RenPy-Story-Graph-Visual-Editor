"""
tests/test_stage_snapshot_scene_at.py
=====================================
舞台快照里的**背景变换 / 图层变换**（2026-10-30，⑦ P0）。

为什么单独钉：Ren'Py 里 `scene bg X at bgfit` 的 `at` 不是一个"本拍事件"，
而是**持续状态** —— 背景 displayable 一直带着这个 transform，直到下一次
`scene`。从前快照只把 `scene` 图名记进 state、`scene_at` 完全没进，
于是预览/卡片只有在"写 scene 的那一拍"知道 fit，后面几百拍全丢（真机
`chapter1`：按引擎语义应有 320 拍带 fit，当时只有 13 拍）。

同样，`show cg cg01 at cg38402160` 的 `at` 从前只出现在「本拍变化」文字里，
图层状态只有图名 —— 预览永远按原图贴左上。

判据（契约 §1.6）：
  · state 增加 `scene_at: str`（持续）与 `layer_at: dict[tag, at]`
    （show 时记、hide 时清）；
  · 只加**内部键**，显示列/变化文字不变；
  · 老调用方缺键不炸（`_initial_state` / `stage_exit_state`）。
"""

from __future__ import annotations

from storygraph.story.beat_model import Beat
from storygraph.story.stage_snapshot import (_initial_state, snapshot_rows,
                                             stage_exit_state,
                                             stage_snapshots)


def _beat(seq=1, **kw):
    return Beat(seq=seq, label='t', **kw)


class TestSceneAtPersists:
    def test_scene_at_is_recorded_and_inherited(self):
        """第 1 拍 `scene bg X at bgfit`，第 2/3 拍没写 scene → 仍然带 fit。"""
        beats = [
            _beat(1, text='一', scene='bg bg7_0', scene_at='bgfit'),
            _beat(2, text='二'),
            _beat(3, text='三'),
        ]
        rows = snapshot_rows(beats)
        assert [r['state']['scene_at'] for r in rows] == \
            ['bgfit', 'bgfit', 'bgfit']
        assert [r['scene'] for r in rows] == ['bg bg7_0'] * 3

    def test_new_scene_without_at_clears_the_previous_transform(self):
        """`scene bg Y`（没 at）= 引擎新 displayable，不带上一张的 fit。"""
        beats = [
            _beat(1, scene='bg bg7_0', scene_at='bgfit'),
            _beat(2, scene='bg bg8_0'),
        ]
        rows = snapshot_rows(beats)
        assert rows[0]['state']['scene_at'] == 'bgfit'
        assert rows[1]['state']['scene_at'] == ''
        assert rows[1]['scene'] == 'bg bg8_0'

    def test_new_scene_with_its_own_at_replaces_the_previous_one(self):
        beats = [
            _beat(1, scene='bg bg7_0', scene_at='bgfit'),
            _beat(2, scene='bg bg8_0', scene_at='bgnearfit'),
        ]
        rows = snapshot_rows(beats)
        assert rows[0]['state']['scene_at'] == 'bgfit'
        assert rows[1]['state']['scene_at'] == 'bgnearfit'

    def test_show_background_with_at_updates_scene_and_scene_at(self):
        """`show bg rooftop at bgfit` 也是换背景（tag == scene tag）。"""
        beats = [
            _beat(1, scene='bg rooftop'),
            _beat(2, show='bg rooftop@bgfit'),
            _beat(3, text='继承'),
        ]
        rows = snapshot_rows(beats)
        assert rows[1]['scene'] == 'bg rooftop'
        assert rows[1]['state']['scene_at'] == 'bgfit'
        assert rows[2]['state']['scene_at'] == 'bgfit'
        # show 换背景不该让"台上站着 bg"
        assert rows[1]['actors'] == ''
        assert rows[1]['state']['actors'] == []

    def test_hiding_the_background_clears_scene_at(self):
        beats = [
            _beat(1, scene='bg bg7_0', scene_at='bgfit'),
            _beat(2, hide='bg'),
        ]
        rows = snapshot_rows(beats)
        assert rows[1]['scene'] == ''
        assert rows[1]['state']['scene_at'] == ''


class TestLayerAt:
    def test_layer_at_is_recorded_on_show_and_cleared_on_hide(self):
        beats = [
            _beat(1, scene='bg room'),
            _beat(2, show='cg cg01@cg38402160'),
            _beat(3, text='继承'),
            _beat(4, hide='cg'),
        ]
        rows = stage_snapshots(beats)
        assert [r['state']['layer_at'] for r in rows] == [
            {}, {'cg': 'cg38402160'}, {'cg': 'cg38402160'}, {}]
        assert [r['layers'] for r in rows] == ['', 'cg cg01', 'cg cg01', '']

    def test_layer_without_at_is_an_empty_string_not_missing(self):
        beats = [_beat(1, show='ui msg_dialogue')]
        rows = snapshot_rows(beats)
        assert rows[0]['state']['layer_at'] == {'ui': ''}

    def test_re_show_updates_the_transform(self):
        """同一张图再次 show（新 at）→ 引擎会重新套 transform。"""
        beats = [
            _beat(1, show='cg cg01@cg38402160'),
            _beat(2, show='cg cg01@bgfit'),
        ]
        rows = snapshot_rows(beats)
        assert rows[1]['state']['layers'] == {'cg': 'cg cg01'}
        assert rows[1]['state']['layer_at'] == {'cg': 'bgfit'}

    def test_actors_are_not_polluted_by_layer_shows(self):
        beats = [
            _beat(1, scene='bg room', show='cg cg01@cg38402160'),
            _beat(2, show='ling happy@left'),
        ]
        rows = snapshot_rows(beats)
        assert rows[0]['state']['actors'] == []
        assert rows[1]['state']['actors'] == [['ling happy', 'left']]
        assert rows[1]['state']['layer_at'] == {'cg': 'cg38402160'}


class TestSceneClearsTheWholeMasterLayer:
    """
    `scene` 清的是**整个 master 层**，不只是立绘（2026-10-31 修）。

    Ren'Py 官方措辞："The scene statement removes all displayables from the
    master layer, and then shows an image on that layer." —— 所以
    `show cg …` 的 CG、`show ui …` 的 HUD 和立绘一样，被 `scene` 清掉。

    从前快照只清 `actors`、`layers` 原样留着 → **产物里已经没了的 CG，
    预览/卡片还在画**。真机 `sr_general` 61 个 `scene` 拍里 **40 个**是这样。
    """

    def test_scene_clears_cg_layers_too(self):
        beats = [
            _beat(1, scene='bg room', show='cg cg01@cg38402160'),
            _beat(2, text='CG 还挂着'),
            _beat(3, scene='bg wide'),            # 换景 → CG 没了
            _beat(4, text='继承'),
        ]
        rows = stage_snapshots(beats)
        assert [r['layers'] for r in rows] == \
            ['cg cg01', 'cg cg01', '', '']
        assert [r['state']['layer_at'] for r in rows] == \
            [{'cg': 'cg38402160'}, {'cg': 'cg38402160'}, {}, {}]
        assert 'layer_src' not in rows[2]['state'] or \
            rows[2]['state']['layer_src'].get('cg') is None

    def test_the_change_line_says_the_layers_went_away(self):
        beats = [
            _beat(1, show='cg cg01'),
            _beat(2, scene='bg room'),
        ]
        rows = stage_snapshots(beats)
        text = rows[1]['display_changes']
        assert '清空图层（1 个）' in text, text
        assert '清空立绘' not in text            # 那一拍台上没有立绘

    def test_shows_after_the_scene_in_the_same_beat_survive(self):
        """同一拍 `scene` 之后又 show 的 CG 当然还在（顺序就是语义）。"""
        beats = [
            _beat(1, show='cg old'),
            _beat(2, scene='bg room', show='cg new'),
        ]
        rows = stage_snapshots(beats)
        assert rows[1]['state']['layers'] == {'cg': 'cg new'}
        assert rows[1]['layers'] == 'cg new'

    def test_a_show_before_the_scene_in_the_same_beat_is_cleared(self):
        beats = [
            _beat(1, scene='bg room'),
            _beat(2, show='cg cg01', scene='bg wide',
                  op_order='show#0,scene#0'),
        ]
        rows = stage_snapshots(beats)
        assert rows[0]['state']['layers'] == {}
        assert rows[1]['state']['layers'] == {}


class TestBackwardCompatibility:
    def test_state_keys_exist_even_when_empty(self):
        row = snapshot_rows([_beat(1, text='只有台词')])[0]
        assert row['state']['scene_at'] == ''
        assert row['state']['layer_at'] == {}

    def test_initial_state_accepts_old_dicts_without_the_new_keys(self):
        st = _initial_state({'scene': 'bg room', 'actors': [
            ['ling happy', 'left']], 'layers': {'cg': 'cg cg01'}})
        assert st['scene_at'] == ''
        assert st['layer_at'] == {}
        assert st['scene'] == 'bg room'
        assert st['actors'] == [['ling happy', 'left']]
        assert st['layers'] == {'cg': 'cg cg01'}

    def test_initial_state_can_carry_the_new_keys(self):
        st = _initial_state({'scene': 'bg room', 'scene_at': 'bgfit',
                             'layer_at': {'cg': 'cg38402160'}})
        assert st['scene_at'] == 'bgfit'
        assert st['layer_at'] == {'cg': 'cg38402160'}

    def test_stage_exit_state_does_not_crash_on_old_initial_state(self):
        init = {'scene': 'bg room', 'actors': [], 'layers': {},
                'music': '', 'voice': '', 'unknown': set()}
        out = stage_exit_state([_beat(1, text='继续')], initial_state=init)
        assert out['scene'] == 'bg room'

    def test_display_columns_are_unchanged(self):
        """只加 state 内部键：行上的显示字段不该多出 scene_at/layer_at。"""
        row = snapshot_rows([_beat(1, scene='bg room', scene_at='bgfit')])[0]
        assert 'scene_at' not in row
        assert 'layer_at' not in row
        assert row['scene'] == 'bg room'
        assert row['layers'] == ''


class TestExitStateCarriesTheNewKeys:
    """
    跨章继承（2026-10-30，P1）：`stage_exit_state` 的出口状态必须带
    `scene_at` / `layer_at`。

    为什么单钉：`_initial_state` 早就支持这两个键，但 `EXIT_STATE_KEYS`
    里没有它们 → 上一章的 `scene bg room at bgfit` / `show cg cg01 at
    cg38402160` 到下一章第一拍就丢了（背景变回原尺寸、CG 贴左上）。
    """

    def test_exit_state_carries_scene_at_and_layer_at(self):
        beats = [
            _beat(1, scene='bg room', scene_at='bgfit'),
            _beat(2, show='cg cg01 at cg38402160'),
            _beat(3, text='这一章演完'),
        ]
        out = stage_exit_state(beats)
        assert out['scene'] == 'bg room'
        assert out['scene_at'] == 'bgfit'
        assert out['layers'] == {'cg': 'cg cg01'}
        assert out['layer_at'] == {'cg': 'cg38402160'}

    def test_next_chapter_inherits_scene_at_and_layer_at(self):
        """把出口状态喂给下一章：第一拍仍带这两个值。"""
        prev = [
            _beat(1, scene='bg room', scene_at='bgfit'),
            _beat(2, show='cg cg01 at cg38402160'),
        ]
        exit_state = stage_exit_state(prev)
        nxt = [_beat(1, text='下一章第一句')]
        rows = stage_snapshots(nxt, initial_state=exit_state)
        assert rows[0]['state']['scene'] == 'bg room'
        assert rows[0]['state']['scene_at'] == 'bgfit'
        assert rows[0]['state']['layer_at'] == {'cg': 'cg38402160'}

    def test_new_keys_are_present_even_when_empty(self):
        """空也输出：结构稳定（调用方不用先 `.get` 再判键在不在）。"""
        out = stage_exit_state([])
        assert out['scene_at'] == ''
        assert out['layer_at'] == {}

    def test_empty_beats_return_entry_state_verbatim(self):
        """没有拍时原样返回入口状态（`unknown` 语义不动）。"""
        init = {'scene': 'bg room', 'scene_at': 'bgfit',
                'actors': [['ling happy', 'left']],
                'layers': {'cg': 'cg cg01'},
                'layer_at': {'cg': 'cg38402160'},
                'music': 'song1', 'voice': '',
                'unknown': {'layers'}}
        out = stage_exit_state([], initial_state=init)
        assert out['scene'] == 'bg room'
        assert out['scene_at'] == 'bgfit'
        assert out['layer_at'] == {'cg': 'cg38402160'}
        assert out['unknown'] == {'layers'}

    def test_layer_at_is_a_copy(self):
        """`layer_at` 必须是副本：调用方改出口状态不能回写入口/快照。"""
        init = {'layer_at': {'cg': 'cg38402160'}}
        out = stage_exit_state([], initial_state=init)
        out['layer_at']['ui'] = 'msg'
        assert init['layer_at'] == {'cg': 'cg38402160'}
