"""
tests/test_stage_at_source.py
=============================
**"改尺寸"必须回到写它的那一拍**（2026-10-31，⑦ P2 的真缺口）。

用户原话：
> 针对 bg 和 cg 不能改像素的问题……就是之前那个超清像素导入导致的错位

真机实测（`sr_general` chapter1_1~1_4，1014 帧）：
  · 背景可见 1013 帧，其中 **972 帧**不是本拍写的 `scene`；
  · CG/UI 图层可见 487 帧，**476 帧**不是本拍写的 `show`；
  · 而 `plan_at_edit` 从前只认本拍字段 → 那 972 帧全部回
    `这一拍没有背景（没有 scene）`。入口在 **96% 的拍里按不下去**。

根因不是"没入口"，是**尺寸是持续状态，写回却挂在"这一拍自己写的那一行"上**。
本文件钉住四件事：
  ① 快照记出处（`scene_src` / `layer_src`），0 是合法值；
  ② 帧把出处带出来；
  ③ 计划改**来源那一拍**（不是本拍），并说清"改的是第几拍"；
  ④ 影响区间（Unity Prefab 那条"override 必须能看到影响面"）。
不知道出处时（跨章继承 / 手工构造的帧）**退回老行为，不猜**。
"""

from __future__ import annotations

import pytest

from storygraph.app.stage import StageFrame, build_frames, plan_at_edit
from storygraph.story.beat_model import Beat
from storygraph.story.stage_snapshot import stage_snapshots

TRANSFORMS = '''transform bgfit:
    fit "cover"

transform half:
    zoom 0.5
'''


from storygraph.story.atl import parse_transforms            # noqa: E402


@pytest.fixture(scope='module')
def transforms():
    return parse_transforms(TRANSFORMS)


def _beats():
    """
    6 拍：背景在第 0 拍设、CG 在第 2 拍 show，之后全是继承。

    第 4 拍换景（`scene bg wide at bgfit`）—— 两个来源都在它之前被覆盖。
    """
    return [
        Beat(seq=1, label='t', text='', scene='bg big'),
        Beat(seq=2, label='t', text=''),
        Beat(seq=3, label='t', text='', show='cg cg4k'),
        Beat(seq=4, label='t', text=''),
        Beat(seq=5, label='t', text='', scene='bg wide', scene_at='bgfit'),
        Beat(seq=6, label='t', text=''),
    ]


class TestSnapshotRecordsSource:
    def test_scene_source_is_the_beat_that_wrote_it(self):
        rows = stage_snapshots(_beats())
        assert [r['state']['scene_src'] for r in rows] == [0, 0, 0, 0, 4, 4]

    def test_layer_source_is_the_beat_that_showed_it(self):
        """
        出处跟着**图层还在不在**走：第 4 拍 `scene` 清台（Ren'Py 官方：
        "removes all displayables from the master layer"），CG 一起没了 ——
        出处也就没了（2026-10-31 修：从前只清立绘，图层在产物里已经没了、
        预览还继续画，真机 61 个 scene 拍里 40 个是这种"看到的 ≠ 生成的"）。
        """
        rows = stage_snapshots(_beats())
        assert [r['state']['layer_src'].get('cg') for r in rows] == \
            [None, None, 2, 2, None, None]

    def test_zero_is_a_real_source_not_unknown(self):
        """第 0 拍写的 = 0。别用 `int(x or -1)` 把 0 说成"不知道"。"""
        rows = stage_snapshots(_beats())
        assert rows[0]['state']['scene_src'] == 0
        assert rows[1]['state']['scene_src'] == 0

    def test_hide_clears_the_layer_source(self):
        beats = [
            Beat(seq=1, label='t', text='', show='cg cg4k'),
            Beat(seq=2, label='t', text=''),
            Beat(seq=3, label='t', text='', hide='cg'),
            Beat(seq=4, label='t', text=''),
        ]
        rows = stage_snapshots(beats)
        assert rows[0]['state']['layer_src'].get('cg') == 0
        assert rows[2]['state']['layer_src'].get('cg') is None

    def test_show_bg_also_counts_as_the_scene_source(self):
        """`show bg X` 在快照里就是换背景（`_layer_kind` = scene）。"""
        beats = [
            Beat(seq=1, label='t', text='', scene='bg big'),
            Beat(seq=2, label='t', text='', show='bg big2 at bgfit'),
            Beat(seq=3, label='t', text=''),
        ]
        rows = stage_snapshots(beats)
        assert rows[1]['state']['scene'] == 'bg big2'
        assert rows[1]['state']['scene_src'] == 1
        assert rows[2]['state']['scene_src'] == 1


class TestFrameCarriesSource:
    def test_frames_expose_scene_and_layer_source(self):
        frames = build_frames(_beats())
        assert frames[3].scene_src == 0
        assert frames[3].layer_src['cg'] == 2
        assert frames[4].scene_src == 4
        # 第 4 拍 `scene` 之后 CG 已经不在台上（引擎语义）→ 出处也没有了
        assert 'cg' not in frames[5].layer_src
        assert frames[5].layers == ''

    def test_manual_frame_defaults_to_unknown(self):
        frame = StageFrame(index=0, line_id='t.0001', label='t', speaker='',
                           text='', scene='bg big')
        assert frame.scene_src == -1
        assert frame.layer_src == {}


class TestPlanWritesTheSourceBeat:
    def test_scene_edit_from_an_inheriting_beat_targets_the_source(
            self, transforms):
        beats = _beats()
        edit = plan_at_edit(beats, 3, target='scene', transform='bgfit',
                            transform_index=transforms, source_index=0)
        assert edit.ok, edit.reason
        assert edit.beat_index == 0          # 改的是第 0 拍那一行
        assert edit.requested_index == 3     # 你在第 3 拍点的
        assert edit.before == '' and edit.after == 'bgfit'
        assert edit.apply()
        assert beats[0].scene_at == 'bgfit'
        assert beats[3].scene_at == ''       # 本拍一个字都没动
        assert edit.revert()
        assert beats[0].scene_at == ''

    def test_layer_edit_from_an_inheriting_beat_targets_the_source(
            self, transforms):
        beats = _beats()
        edit = plan_at_edit(beats, 5, target='layer', tag='cg',
                            transform='half', transform_index=transforms,
                            source_index=2)
        assert edit.ok, edit.reason
        assert edit.beat_index == 2
        assert edit.after == 'cg cg4k@half'
        assert edit.apply()
        assert beats[2].show == 'cg cg4k@half'
        assert beats[5].show == ''

    def test_unknown_source_keeps_the_old_refusal(self, transforms):
        """没有出处（跨章继承 / 手工帧）→ 不猜，还是拒绝（话更具体了）。

        2026-10-31 尺寸面板之后，那句"这一拍没有背景"换成
        "找不到最初写它的那一拍（多半是上一章继承来的），要改请用「换景」"
        —— 判据没变：**拒绝，且不给假拍号**。
        """
        beats = _beats()
        edit = plan_at_edit(beats, 3, target='scene', transform='bgfit',
                            transform_index=transforms)
        assert not edit.ok
        assert '找不到最初写它的那一拍' in edit.reason
        assert '换景' in edit.reason

    def test_beat_that_writes_its_own_scene_needs_no_source(self, transforms):
        beats = _beats()
        edit = plan_at_edit(beats, 4, target='scene', transform='half',
                            transform_index=transforms, source_index=0)
        assert edit.ok, edit.reason
        assert edit.beat_index == 4          # 本拍有 scene → 改本拍

    def test_complex_clause_at_the_source_is_still_refused(self, transforms):
        """回退到来源拍**不等于**放过复杂子句 —— 那是那一行的性质。"""
        beats = [
            Beat(seq=1, label='t', text='', show='cg cg01 at half behind ye'),
            Beat(seq=2, label='t', text=''),
        ]
        edit = plan_at_edit(beats, 1, target='layer', tag='cg',
                            transform='bgfit', transform_index=transforms,
                            source_index=0)
        assert not edit.ok
        assert '复杂子句' in edit.reason

    def test_diff_text_says_which_beat_and_how_many_frames(self, transforms):
        beats = _beats()
        edit = plan_at_edit(beats, 3, target='scene', transform='bgfit',
                            transform_index=transforms, source_index=0)
        text = edit.diff_text()
        assert '改的是第 1 拍' in text
        assert '第 1–4 拍' in text           # 第 0..3 拍 → 人话 1–4
        assert '共 4 拍' in text


class TestOverrideWritesShowNeverScene:
    """
    「从这一拍起」= 只写 `show`。

    Ren'Py 官方：`scene` **移除该层全部 displayable**（清台），`show` 只替换
    同 tag。写错这一个字，玩家会看到台上的人突然消失 —— 用户 2026-10-31
    亲自纠正过这一条（"scene 不是改属性，是清台"）。
    """

    def _beats(self):
        return [
            Beat(seq=1, label='t', text='', scene='bg big'),
            Beat(seq=2, label='t', text='', show='ling happy'),
            Beat(seq=3, label='t', text=''),
        ]

    def test_override_plan_is_a_show_not_a_scene(self, transforms):
        beats = self._beats()
        edit = plan_at_edit(beats, 2, target='scene', transform='bgfit',
                            transform_index=transforms, mode='override',
                            scene_image='bg big')
        assert edit.ok, edit.reason
        assert edit.field == 'show'
        assert edit.after == 'bg big@bgfit'
        assert 'scene' not in edit.after
        assert 'show bg big at bgfit' in edit.rpy_after
        assert 'scene bg big' not in edit.rpy_after

    def test_override_keeps_the_sprites_on_stage(self, transforms):
        """改完尺寸，台上那个立绘还在（这正是 show 和 scene 的区别）。"""
        beats = self._beats()
        before = build_frames(beats)[2]
        assert before.scene == 'bg big'
        assert [s.image for s in before.sprites] == ['ling happy']

        edit = plan_at_edit(beats, 2, target='scene', transform='bgfit',
                            transform_index=transforms, mode='override',
                            scene_image='bg big')
        assert edit.ok, edit.reason
        assert edit.apply()

        after = build_frames(beats)[2]
        assert after.scene == 'bg big'
        assert after.scene_at == 'bgfit'          # 尺寸确实变了
        assert [s.image for s in after.sprites] == ['ling happy']   # 人还在
        assert 'scene' not in beats[2].show

    def test_a_scene_at_that_beat_would_clear_them(self):
        """反面对照：同一拍写成 scene，立绘就没了（所以工具不生成它）。"""
        beats = [
            Beat(seq=1, label='t', text='', scene='bg big'),
            Beat(seq=2, label='t', text='', show='ling happy'),
            Beat(seq=3, label='t', text='', scene='bg big', scene_at='bgfit'),
        ]
        frame = build_frames(beats)[2]
        assert frame.sprites == []          # scene = 清台
        assert frame.scene_at == 'bgfit'

    def test_override_adds_a_show_at_this_beat(self, transforms):
        """本拍没有背景 show → 在**这一拍**新写一条（前面那条不动）。"""
        beats = [
            Beat(seq=1, label='t', text='', scene='bg big'),
            Beat(seq=2, label='t', text='', show='bg big@half'),
            Beat(seq=3, label='t', text=''),
        ]
        edit = plan_at_edit(beats, 2, target='scene', transform='bgfit',
                            transform_index=transforms, mode='override',
                            scene_image='bg big')
        assert edit.ok, edit.reason
        assert edit.beat_index == 2
        assert edit.after == 'bg big@bgfit'
        assert edit.apply()
        assert beats[2].show == 'bg big@bgfit'
        assert beats[1].show == 'bg big@half'      # 前面那条没被碰

    def test_override_replaces_this_beats_own_show_at(self, transforms):
        beats = [
            Beat(seq=1, label='t', text='', scene='bg big'),
            Beat(seq=2, label='t', text='', show='bg big@half'),
        ]
        edit = plan_at_edit(beats, 1, target='scene', transform='bgfit',
                            transform_index=transforms, mode='override',
                            scene_image='bg big')
        assert edit.ok, edit.reason
        assert edit.beat_index == 1
        assert edit.before == 'bg big@half'
        assert edit.after == 'bg big@bgfit'

    def test_override_refuses_complex_show(self, transforms):
        beats = [
            Beat(seq=1, label='t', text='', scene='bg big'),
            Beat(seq=2, label='t', text='', show='bg big at half behind ye'),
        ]
        edit = plan_at_edit(beats, 1, target='scene', transform='bgfit',
                            transform_index=transforms, mode='override',
                            scene_image='bg big')
        assert not edit.ok
        assert '复杂子句' in edit.reason

    def test_override_on_a_scene_beat_edits_that_scene_line(self, transforms):
        """本拍自己写了 scene → 直接改这一行的 scene_at（不新增 show）。"""
        beats = self._beats()
        beats[2].scene = 'bg wide'
        edit = plan_at_edit(beats, 2, target='scene', transform='bgfit',
                            transform_index=transforms, mode='override',
                            scene_image='bg wide')
        assert edit.ok, edit.reason
        assert edit.field == 'scene_at'
        assert edit.beat_index == 2

    def test_override_appends_the_show_token_when_the_beat_has_item_tokens(
            self, transforms):
        """
        真机实测的那一坑（chapter1_1 第 134 拍）：`op_order='show#0,code#0'`
        时快照只应用令牌点到的第 0 条；追加的第二条 show 没有令牌 →
        快照看不见它（渲染器会补出来）→ "脚本改了，画面没动"。
        """
        beats = [
            Beat(seq=1, label='t', text='', scene='bg big'),
            Beat(seq=2, label='t', text='', show='ling happy',
                 op_order='show#0,code#0', stage_order='show#0'),
            Beat(seq=3, label='t', text=''),
        ]
        edit = plan_at_edit(beats, 1, target='scene', transform='bgfit',
                            transform_index=transforms, mode='override',
                            scene_image='bg big')
        assert edit.ok, edit.reason
        assert edit.token_field == 'op_order'
        assert edit.extra_token == 'show#1'
        assert edit.apply()
        assert beats[1].op_order == 'show#0,code#0,show#1'
        frame = build_frames(beats)[1]
        assert frame.scene_at == 'bgfit'                 # 快照真的看见了
        assert [s.image for s in frame.sprites] == ['ling happy']
        assert edit.revert()
        assert beats[1].op_order == 'show#0,code#0'      # 逐字还原
        assert beats[1].show == 'ling happy'

    def test_verb_level_tokens_need_no_extra_token(self, transforms):
        beats = [
            Beat(seq=1, label='t', text='', scene='bg big'),
            Beat(seq=2, label='t', text='', show='ling happy',
                 op_order='show,code', stage_order='show'),
        ]
        edit = plan_at_edit(beats, 1, target='scene', transform='bgfit',
                            transform_index=transforms, mode='override',
                            scene_image='bg big')
        assert edit.ok, edit.reason
        assert edit.extra_token == ''


class TestAffectedRange:
    def test_scene_range_stops_before_the_next_scene(self, transforms):
        beats = _beats()
        edit = plan_at_edit(beats, 0, target='scene', transform='bgfit',
                            transform_index=transforms)
        assert edit.affects == (0, 3)        # 第 4 拍换景 → 到最后一个是 3

    def test_last_scene_runs_to_the_end_of_the_chapter(self, transforms):
        beats = _beats()
        edit = plan_at_edit(beats, 4, target='scene', transform='bgfit',
                            transform_index=transforms)
        assert edit.affects == (4, 5)

    def test_layer_range_stops_at_the_next_show_of_that_tag(self, transforms):
        beats = [
            Beat(seq=1, label='t', text='', show='cg cg4k'),
            Beat(seq=2, label='t', text=''),
            # 同一 tag 又 show 一次（紧凑写法，能安全改写）→ 前一段到此为止
            Beat(seq=3, label='t', text='', show='cg cg4k@half'),
            Beat(seq=4, label='t', text=''),
        ]
        edit = plan_at_edit(beats, 3, target='layer', tag='cg',
                            transform='bgfit', transform_index=transforms,
                            source_index=0)
        assert edit.ok, edit.reason
        assert edit.beat_index == 0
        assert edit.affects == (0, 1)

    def test_refused_plan_has_no_range(self, transforms):
        beats = _beats()
        edit = plan_at_edit(beats, 3, target='scene', transform='bgfit',
                            transform_index=transforms)
        assert not edit.ok and edit.affects == ()


class TestPreviewLabels:
    def test_inherited_background_label_says_which_beat(self):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.stage_preview import StagePreviewWindow
        frame = StageFrame(index=3, line_id='t.0004', label='t', speaker='',
                           text='', scene='bg big', layers='cg cg4k',
                           layers_at={'cg': ''}, scene_src=0,
                           layer_src={'cg': 2})
        targets = StagePreviewWindow._at_targets_of(frame)
        bg_label, bg_data = targets[0]
        assert '第 1 拍设的' in bg_label
        assert bg_data == ('scene', '', 0)
        cg_data = [d for label, d in targets if d[0] == 'layer'][0]
        assert cg_data == ('layer', 'cg', 2)

    def test_a_beat_that_writes_it_has_no_extra_suffix(self):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.stage_preview import StagePreviewWindow
        frame = StageFrame(index=0, line_id='t.0001', label='t', speaker='',
                           text='', scene='bg big', scene_src=0)
        label, data = StagePreviewWindow._at_targets_of(frame)[0]
        assert '拍设的' not in label
        assert data == ('scene', '', 0)

    def test_unknown_source_is_still_listed(self):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.stage_preview import StagePreviewWindow
        frame = StageFrame(index=0, line_id='t.0001', label='t', speaker='',
                           text='', scene='bg big', scene_src=-1)
        label, data = StagePreviewWindow._at_targets_of(frame)[0]
        assert data == ('scene', '', -1)
        # 2026-10-31：留白会让人以为按下去就改这一行 —— 实际是"本章第一拍补一条"
        assert '出处不在这章' in label

    def test_the_override_entry_is_offered_only_for_inherited_backgrounds(self):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.stage_preview import StagePreviewWindow

        def kinds(frame):
            return [d[0] for _l, d in StagePreviewWindow._at_targets_of(frame)]

        inherited = StageFrame(index=3, line_id='t.0004', label='t',
                               speaker='', text='', scene='bg big',
                               scene_src=0)
        own = StageFrame(index=0, line_id='t.0001', label='t', speaker='',
                         text='', scene='bg big', scene_src=0)
        assert 'scene_override' in kinds(inherited)
        assert 'scene_override' not in kinds(own)   # 本拍就是换景拍，改它自己

    def test_override_request_maps_to_show_mode(self):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.stage_preview import StagePreviewWindow

        class _Combo:
            def __init__(self, data):
                self._data = data

            def currentData(self):
                return self._data

        class _Win:
            frames = [StageFrame(index=3, line_id='t.0004', label='t',
                                 speaker='', text='', scene='bg big',
                                 scene_src=0)]
            idx = 0
            _at_target = _Combo(('scene_override', '', -1))
            _at_transform = _Combo('bgfit')

        req = StagePreviewWindow._at_request(_Win())
        assert req['target'] == 'scene'
        assert req['mode'] == 'override'
        assert req['scene_image'] == 'bg big'

    def test_the_request_carries_the_source_beat(self):
        """下拉里选中"背景"之后，请求里必须带上来源拍号（否则又改本拍）。"""
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.stage_preview import StagePreviewWindow

        class _Combo:
            def __init__(self, data):
                self._data = data

            def currentData(self):
                return self._data

        class _Win:
            frames = [StageFrame(index=3, line_id='t.0004', label='t',
                                 speaker='', text='', scene='bg big',
                                 scene_src=0)]
            idx = 0
            _at_target = _Combo(('scene', '', 0))
            _at_transform = _Combo('bgfit')

        req = StagePreviewWindow._at_request(_Win())
        assert req['beat_index'] == 3
        assert req['source_index'] == 0


class TestLeftoverItemsMatchTheRenderer:
    """
    令牌没点到的 show/hide 条目：渲染器会补出来，快照也必须看见。

    2026-10-31 实测的真缺口（同一类"看到的 ≠ 生成的"）：`op_order='show#0'`
    时卡片再加一个立绘，产物有两句 `show`，卡片/预览只显示一个人。
    """

    def test_an_item_added_without_a_token_still_shows_up(self):
        beat = Beat(seq=1, label='t', text='', show='ling a',
                    op_order='show#0', stage_order='show#0')
        beat.show = 'ling a; ye b'
        row = stage_snapshots([beat])[0]
        assert [s[0] for s in row['state']['actors']] == ['ling a', 'ye b']

    def test_what_the_snapshot_shows_is_what_the_renderer_emits(self):
        from storygraph.story.beat_render import render_beat_to_rpy

        beat = Beat(seq=1, label='t', text='', show='ling a',
                    op_order='show#0', stage_order='show#0')
        beat.show = 'ling a; ye b'
        row = stage_snapshots([beat])[0]
        shown = [s[0] for s in row['state']['actors']]
        emitted = [ln for ln in render_beat_to_rpy(beat) if ln.startswith('show')]
        assert len(shown) == len(emitted) == 2

    def test_leftover_hide_is_applied_too(self):
        beat = Beat(seq=1, label='t', text='', show='ling a; ye b',
                    op_order='show#0,show#1', stage_order='show#0,show#1')
        beat.hide = 'ling'
        row = stage_snapshots([beat])[0]
        assert [s[0] for s in row['state']['actors']] == ['ye b']


class TestMainWindowPassthrough:
    def test_preview_request_keeps_source_index(self, monkeypatch):
        """主窗口那条外部钩子不能把 source_index 吞掉（它是 3 行透传）。"""
        pytest.importorskip('PySide6')
        import storygraph.app.stage as stage_mod
        from storygraph.ui_qt.main_window import _plan_preview_at

        seen = {}

        def _fake_plan(*_args, **kwargs):
            seen.update(kwargs)
            return None

        monkeypatch.setattr(stage_mod, 'plan_at_edit', _fake_plan)

        class _Session:
            beats = _beats()

        class _Win:
            _session = _Session()
            _project_root = None

        _plan_preview_at(_Win(), {'target': 'scene', 'tag': '',
                                  'transform': 'bgfit', 'beat_index': 3,
                                  'source_index': 0})
        assert seen.get('source_index') == 0
        assert seen.get('target') == 'scene'
