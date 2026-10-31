"""
tests/test_chapter_first.py
===========================
**跨章继承的一行怎么改**：在本章第一拍**补一条 `show`**（2026-10-31）。

这一份钉住的是"这条路的语义与边界"，不是"UI 好不好看"：

  ① 落点恒为**第 0 拍**（背景/图层从本章第一拍就在台上）—— 不是"最近的一拍"，
     也不是"你这一拍"；
  ② 产物是**多一行** `show 图 at 变换`：不删行、不改别的条目、不写 `scene`
     （`scene` 清台，见 `_bg_show_edit` 的文档）；
  ③ 接不住的落点**明说原因**（条件 / 菜单块 / ATL 块 / 同 tag 已 show /
     「原样」）—— 绝不静默换个拍、也不静默跳过；
  ④ 两条出口同一份判断：尺寸面板（`plan_size_edit`）与演出预览
     （`_plan_preview_at`）都按 `source < 0` 走这一条；
  ⑤ 前提是真的：只有帧**知道**那一行（`initial_state` 从上一章接过来）才会
     出现 `source == -1` 的行 —— 不传就是"这一拍没有背景"，不许假装。

真机现状（`tools/_probe_entry_real.py`，两个项目）：
  · **卡片这条路径从前没有入口状态**（单文件、`initial_state=None`）：真机
    1013 个有背景的拍里 `scene_src < 0` 的是 **0 个** —— 这个入口一段
    时间按不到（`test_without_an_entry_state_there_is_no_such_row` 把它钉住）；
  · 2026-10-31 接线之后（`tests/test_stage_entry.py`）：会话把
    `pipeline.continuity` 算出的入口状态喂进 `build_frame_at(...)`，
    送进去的帧就有了 `source == -1` 的背景行 —— 这个入口**真的按得到**了；
  · 跨章体检那边确有其事：`sr_general` 有 6 处"背景漏章/第 1 句台词时还是上一章"
    （chapter1_1→1_2、chapter1_4→2_1、2_1→2_1_1~4）。
"""

from __future__ import annotations

import pytest

from storygraph.app.stage import (MODE_CHAPTER_FIRST, StageFrame, build_frame_at,
                                  build_frames, is_unknown_source,
                                  plan_at_edit, plan_size_edit)
from storygraph.app.stage_render import size_rows
from storygraph.story.atl import parse_transforms
from storygraph.story.beat_model import Beat
from storygraph.story.stage_snapshot import stage_snapshots, stage_exit_state

TRANSFORMS = '''transform bgfit:
    fit "cover"

transform half:
    zoom 0.5

transform cg38402160:
    zoom 0.75
'''


@pytest.fixture(scope='module')
def transforms():
    return parse_transforms(TRANSFORMS)


def _chapter():
    """4 拍：本章从没写过背景（上一章留下的），第 3 拍另写了一条 CG。"""
    return [
        Beat(seq=1, label='t', line_id='t.0001', text='第一句'),
        Beat(seq=2, label='t', line_id='t.0002', text='第二句'),
        Beat(seq=3, label='t', line_id='t.0003', text='第三句',
             show='cg cg1@half'),
        Beat(seq=4, label='t', line_id='t.0004', text='第四句'),
    ]


def _plan(beats, index=2, *, target='scene', tag='', image='bg bg7_0',
          transform='bgfit', **kw):
    return plan_at_edit(beats, index, target=target, tag=tag,
                        transform=transform, transform_index=kw.pop(
                            'transform_index', {}), mode=MODE_CHAPTER_FIRST,
                        scene_image=image, **kw)


class TestAppendsOneLineAtTheFirstBeat:
    def test_the_landing_is_beat_zero_not_the_beat_you_clicked(
            self, transforms):
        beats = _chapter()
        edit = _plan(beats, 2, transform_index=transforms)
        assert edit.ok, edit.reason
        assert edit.beat_index == 0
        assert edit.requested_index == 2
        assert edit.inserted is True

    def test_it_adds_one_item_and_keeps_the_dialogue(self, transforms):
        beats = _chapter()
        edit = _plan(beats, 2, transform_index=transforms)
        assert edit.before == '' and edit.after == 'bg bg7_0@bgfit'
        assert edit.apply()
        assert beats[0].show == 'bg bg7_0@bgfit'
        assert beats[0].text == '第一句'          # 台词一个字不动
        assert beats[1].show == '' and beats[2].show == 'cg cg1@half'
        assert len(beats) == 4                   # 不多不少一拍

    def test_an_existing_show_is_kept_as_is(self, transforms):
        """第 1 拍已经有 show 时：补在末尾，已有条目逐字不动。"""
        beats = _chapter()
        beats[0].show = 'cg cg0@half'
        edit = _plan(beats, 2, transform_index=transforms)
        assert edit.ok, edit.reason
        assert edit.after == 'cg cg0@half; bg bg7_0@bgfit'
        assert edit.apply()
        assert beats[0].show == 'cg cg0@half; bg bg7_0@bgfit'

    def test_the_generated_script_gains_exactly_one_line(self, transforms):
        """确认框里看到的 diff 不是"字段变了"，是"脚本多了一行"。"""
        beats = _chapter()
        edit = _plan(beats, 2, transform_index=transforms)
        assert edit.ok, edit.reason
        before = edit.rpy_before.splitlines()
        after = edit.rpy_after.splitlines()
        added = [line for line in after if line not in before]
        removed = [line for line in before if line not in after]
        assert added == ['show bg bg7_0 at bgfit']
        assert removed == []
        # 而且排在第一句台词之前（舞台指令在台词前）
        assert after.index('show bg bg7_0 at bgfit') < after.index('"第一句"')

    def test_the_token_goes_in_when_op_order_points_at_items(self, transforms):
        """`op_order` 有逐条令牌时，新条目必须补 `show#N`，否则快照看不见它。"""
        beats = _chapter()
        beats[0].show = 'cg cg0@half'
        beats[0].op_order = 'show#0,code#0'
        edit = _plan(beats, 2, transform_index=transforms)
        assert edit.ok, edit.reason
        assert edit.token_field == 'op_order'
        assert edit.extra_token == 'show#1'
        assert edit.apply()
        assert beats[0].op_order == 'show#0,code#0,show#1'
        assert edit.revert()
        assert beats[0].op_order == 'show#0,code#0'
        assert beats[0].show == 'cg cg0@half'

    def test_verb_level_tokens_need_no_extra_token(self, transforms):
        beats = _chapter()
        beats[0].show = 'cg cg0@half'
        beats[0].op_order = 'show,code'
        edit = _plan(beats, 2, transform_index=transforms)
        assert edit.ok, edit.reason
        assert (edit.token_field, edit.extra_token) == ('', '')

    def test_revert_puts_everything_back(self, transforms):
        beats = _chapter()
        edit = _plan(beats, 2, transform_index=transforms)
        assert edit.apply()
        assert beats[0].show == 'bg bg7_0@bgfit'
        assert edit.revert()
        assert beats[0].show == ''

    def test_the_effect_reaches_the_beat_you_were_looking_at(self, transforms):
        """
        补在第 0 拍，**你在第 3 拍看到的尺寸**必须真的变了（跨层判据）。

        这一条治的就是"改完没生效"：只在字段上改对不算数，要让舞台快照在
        你点的那一拍给出同一个值、并指出出处是第 0 拍。
        """
        beats = _chapter()
        edit = _plan(beats, 2, transform_index=transforms)
        assert edit.apply()
        rows = stage_snapshots(beats)
        assert rows[0]['state']['scene_src'] == 0
        assert rows[2]['state']['scene_src'] == 0
        assert rows[2]['state']['scene_at'] == 'bgfit'

    def test_affects_starts_at_the_first_beat(self, transforms):
        beats = _chapter()
        edit = _plan(beats, 2, transform_index=transforms)
        assert edit.affects[0] == 0
        assert edit.affects[1] >= 2

    def test_affects_stops_at_the_next_rewrite_of_the_same_background(
            self, transforms):
        """
        "影响第 1–2 拍"要停在第 3 拍那条 `show bg … at half` 之前。

        `_affected_range` 只在 `tag` 非空时才把 `show bg X at …` 算作"覆盖" ——
        传空串会把区间一路撑到章末，确认框里那个数字就成了撒谎。
        """
        beats = _chapter()
        beats[2].show = 'bg bg7_0@half'
        edit = _plan(beats, 2, transform_index=transforms)
        assert edit.ok, edit.reason
        assert edit.affects == (0, 1)

    def test_the_confirm_text_says_it_is_an_addition(self, transforms):
        beats = _chapter()
        edit = _plan(beats, 2, transform_index=transforms)
        text = edit.diff_text()
        assert '补一条 show' in text
        assert '不改上一章' in text and '不清立绘' in text
        # 位置要说清：补到第 1 拍（你在第 3 拍点的）
        assert '补到第 1 拍' in text
        assert '你在第 3 拍点的' in text

    def test_the_row_label_does_not_say_edit(self, transforms):
        beats = _chapter()
        edit = _plan(beats, 2, transform_index=transforms)
        assert '补一条' in edit.target_label
        assert '改写' not in edit.target_label


class TestItRefusesOutLoud:
    """接不住的落点：**每一句都说原因**（绝不静默换拍 / 跳过）。"""

    def test_never_writes_scene(self, transforms):
        """这条路**不会**生成 `scene` 两个字（那会清台）。"""
        beats = _chapter()
        edit = _plan(beats, 2, transform_index=transforms)
        assert edit.ok, edit.reason
        assert 'scene' not in edit.after

    def test_as_is_has_nothing_to_remove(self, transforms):
        beats = _chapter()
        edit = plan_at_edit(beats, 2, target='scene', transform='',
                            transform_index=transforms,
                            mode=MODE_CHAPTER_FIRST, scene_image='bg bg7_0')
        assert not edit.ok
        assert '「原样」补不了' in edit.reason
        assert '上一章' in edit.reason

    def test_a_conditional_first_beat_is_refused(self, transforms):
        beats = _chapter()
        beats[0].condition = 'if flag'
        edit = _plan(beats, 2, transform_index=transforms)
        assert not edit.ok
        assert '带条件' in edit.reason and 'if flag' in edit.reason
        assert edit.beat_index == 0            # 说清是**哪一拍**接不住

    def test_a_first_beat_inside_a_block_is_refused(self, transforms):
        beats = _chapter()
        beats[0].nest_path = '0/1'
        edit = _plan(beats, 2, transform_index=transforms)
        assert not edit.ok
        assert '菜单/循环' in edit.reason

    def test_an_atl_block_on_the_first_beat_is_refused(self, transforms):
        beats = _chapter()
        beats[0].show = 'cg cg0@half:'
        edit = _plan(beats, 2, transform_index=transforms)
        assert not edit.ok
        assert 'ATL 块' in edit.reason

    def test_the_same_tag_twice_in_the_first_beat_is_refused(self, transforms):
        beats = _chapter()
        beats[0].show = 'bg bg9_1@half'
        edit = _plan(beats, 2, transform_index=transforms)
        assert not edit.ok
        assert '已经 show 过' in edit.reason

    def test_a_missing_image_is_refused(self, transforms):
        beats = _chapter()
        edit = _plan(beats, 2, transform_index=transforms, image='')
        assert not edit.ok
        assert '没有图名' in edit.reason

    def test_an_at_inside_the_image_name_is_refused(self, transforms):
        beats = _chapter()
        edit = _plan(beats, 2, transform_index=transforms,
                     image='bg bg7_0@half')
        assert not edit.ok
        assert '`@`' in edit.reason

    def test_a_layer_row_whose_spec_disagrees_with_the_tag_is_refused(
            self, transforms):
        beats = _chapter()
        edit = _plan(beats, 2, target='layer', tag='ui', image='cg cg1',
                     transform_index=transforms)
        assert not edit.ok
        assert '对不上' in edit.reason

    def test_an_unknown_transform_is_refused_before_anything_else(
            self, transforms):
        beats = _chapter()
        edit = _plan(beats, 2, transform='nope', transform_index=transforms)
        assert not edit.ok
        assert '没有 transform' in edit.reason

    def test_a_known_source_on_this_route_is_refused(self, transforms):
        """接线错了（出处知道却走"补一条"）要大声拒绝，不能静默多一条 show。"""
        beats = _chapter()
        edit = _plan(beats, 2, transform_index=transforms, source_index=2)
        assert not edit.ok
        assert '出处是第 3 拍' in edit.reason
        assert beats[0].show == ''


class TestPlanSizeEditRoutes:
    """尺寸面板的那一行 → 计划（`source < 0` 就是跨章继承）。"""

    def _row(self, source, kind='scene', tag='', spec='bg bg7_0'):
        return {'index': 0, 'kind': kind, 'tag': tag, 'spec': spec,
                'source': source, 'label': '背景', 'at': '', 'natural': None,
                'items': [], 'why': ''}

    def test_an_unknown_source_goes_to_the_chapter_first_route(
            self, transforms):
        beats = _chapter()
        edit = plan_size_edit(beats, 2, self._row(-1), key='pick',
                              at='bgfit', transform_index=transforms)
        assert edit.ok, edit.reason
        assert edit.inserted is True
        assert edit.beat_index == 0
        assert beats[0].show == ''             # 只算不写

    def test_a_known_source_still_edits_that_line(self, transforms):
        beats = _chapter()
        beats[1].scene = 'bg bg7_0'
        beats[1].scene_at = 'half'
        edit = plan_size_edit(beats, 2, self._row(1), key='pick',
                              at='bgfit', transform_index=transforms)
        assert edit.ok, edit.reason
        assert edit.inserted is False
        assert edit.beat_index == 1

    def test_beat_zero_as_a_source_is_not_unknown(self, transforms):
        """`0` 是合法出处 —— 别把它当成"上一章继承来的"补一条重复的。"""
        beats = _chapter()
        beats[0].scene = 'bg bg7_0'
        beats[0].scene_at = 'half'
        edit = plan_size_edit(beats, 2, self._row(0), key='pick',
                              at='bgfit', transform_index=transforms)
        assert edit.ok, edit.reason
        assert edit.inserted is False
        assert edit.beat_index == 0
        assert edit.after == 'bgfit'

    def test_as_is_on_a_cross_chapter_row_is_refused(self, transforms):
        beats = _chapter()
        edit = plan_size_edit(beats, 2, self._row(-1), key='as_is',
                              transform_index=transforms)
        assert not edit.ok
        assert '「原样」补不了' in edit.reason

    @pytest.mark.parametrize('value,unknown', [
        (0, False), (3, False), (-1, True), (None, True), ('x', True)])
    def test_unknown_source_predicate(self, value, unknown):
        assert is_unknown_source(value) is unknown


class TestThePremiseIsReal:
    """
    这个入口**只有**在帧知道"这一行是上一章留下的"时才出现。

    没有 `initial_state` 时，卡片看到的是"本章没有背景"（`scene=''`）——
    没有行、也就没有按钮。所以这个入口现在是"待接线"的：`build_frame_at`
    / `build_frames` 收 `initial_state`，喂进去的就是跨章那一行。
    """

    def test_without_an_entry_state_there_is_no_such_row(self):
        """
        没有入口状态时，卡片看到的是"本章没有背景"——**没有 scene 行**。

        这正是"这个入口现在按不到"的原因：`size_rows` 只给台上看得见的对象
        开行，而单文件快照不知道上一章留了什么。（CG 行仍在：那是本章自己
        `show` 的，出处是第 2 拍。）
        """
        beats = _chapter()
        frame = build_frame_at(beats, 2)
        assert frame.scene == ''
        assert frame.scene_src == -1
        rows = size_rows(frame)
        assert [r['kind'] for r in rows] == ['layer']
        assert rows[0]['source'] == 2

    def test_an_entry_state_makes_the_row_visible_and_sourceless(self):
        beats = _chapter()
        entry = {'scene': 'bg bg1_2', 'scene_at': 'half',
                 'actors': [['ling happy', 'i11']], 'layers': {},
                 'layer_at': {}}
        frame = build_frame_at(beats, 2, initial_state=entry)
        assert frame.scene == 'bg bg1_2'
        assert frame.scene_at == 'half'
        assert frame.scene_src == -1
        rows = size_rows(frame)
        assert rows and rows[0]['kind'] == 'scene'
        assert rows[0]['source'] == -1
        assert '本章第一拍' in rows[0]['why'] and '补一条' in rows[0]['why']
        # **两件事都说**：图解析不出来（这是单测没有项目根）也不许把
        # "这一行是上一章留下的"吞掉。
        assert '找不到这张图' in rows[0]['why']

    def test_an_entry_state_also_carries_layers_and_actors(self):
        beats = _chapter()
        entry = {'scene': '', 'layers': {'cg': 'cg cg9'},
                 'layer_at': {'cg': 'cg38402160'},
                 'actors': [['ling happy', '']]}
        frames = build_frames(beats, initial_state=entry)
        assert frames[0].scene_src == -1
        assert frames[0].layers == 'cg cg9'
        assert frames[0].layer_src.get('cg') is None       # 出处不知道
        assert 'cg' in (frames[0].layers_at or {})

    def test_the_previous_chapters_exit_state_is_what_gets_passed(self):
        """上一章出口状态 → 下一章入口（这就是那 6 处"背景漏章"的链条）。"""
        prev = [Beat(seq=1, label='p', text='', scene='bg bg1_2',
                     scene_at='half')]
        exit_state = stage_exit_state(prev)
        assert exit_state['scene'] == 'bg bg1_2'
        assert exit_state['scene_at'] == 'half'
        frame = build_frame_at(_chapter(), 0, initial_state=exit_state)
        assert frame.scene == 'bg bg1_2'

    def test_the_insert_rewrites_what_the_frame_shows(self, transforms):
        """补完之后**同一帧**（同一入口状态）看到的 at 就是新的值。"""
        beats = _chapter()
        entry = {'scene': 'bg bg1_2', 'scene_at': 'half'}
        edit = plan_at_edit(beats, 2, target='scene', transform='bgfit',
                            transform_index=transforms,
                            mode=MODE_CHAPTER_FIRST,
                            scene_image='bg bg1_2')
        assert edit.ok, edit.reason
        assert edit.apply()
        after = build_frame_at(beats, 2, initial_state=entry)
        assert after.scene_at == 'bgfit'
        assert after.scene_src == 0


class TestThePanelSaysItBeforeYouPress:
    def test_a_cross_row_tooltip_promises_an_addition(self, qapp):
        from storygraph.ui_qt.size_panel import SizePanel

        panel = SizePanel()
        panel.set_rows([{'index': 0, 'kind': 'scene', 'tag': '',
                         'spec': 'bg bg1_2', 'label': '背景', 'at': 'half',
                         'source': -1, 'natural': (1920, 1080),
                         'note': '背景 bg bg1_2', 'why': '',
                         'items': [{'key': 'cover', 'at': 'bgfit',
                                    'text': '铺满 → bgfit', 'ok': True,
                                    'why': '', 'current': False,
                                    'note': ''}]}])
        btn = panel.slots()[0]['button']
        assert btn.isEnabled() is True
        tip = btn.toolTip()
        assert '本章第一拍' in tip and '补一条' in tip
        assert '不改上一章' in tip and '不清立绘' in tip
        panel.close()

    def test_a_normal_row_tooltip_still_editsthat_line(self, qapp):
        from storygraph.ui_qt.size_panel import SizePanel

        panel = SizePanel()
        panel.set_rows([{'index': 0, 'kind': 'scene', 'tag': '',
                         'spec': 'bg bg1_2', 'label': '背景', 'at': '',
                         'source': 3, 'natural': (1920, 1080),
                         'note': '背景 bg bg1_2', 'why': '',
                         'items': [{'key': 'cover', 'at': 'bgfit',
                                    'text': '铺满 → bgfit', 'ok': True,
                                    'why': '', 'current': False,
                                    'note': ''}]}])
        tip = panel.slots()[0]['button'].toolTip()
        assert '补一条' not in tip
        assert '最初写下这一行的那一拍' in tip
        panel.close()


class TestWindowExits:
    """两条出口同一份判断：卡片尺寸面板、演出预览。"""

    def test_the_card_request_lands_on_the_first_beat(self, ui_window,
                                                      monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        from storygraph.ui_qt import main_window as MW

        bodies = []

        def _yes(_parent, _title, text, *_a, **_k):
            bodies.append(text)
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(MW.QMessageBox, 'question', staticmethod(_yes))
        win = ui_window
        idx = 2
        win._show_index(idx)
        plan = MW.apply_size_from_card(win, {
            'row': 0, 'key': 'pick', 'at': 'leftish', 'kind': 'scene',
            'tag': '', 'spec': 'bg classroom', 'source': -1})
        assert plan is not None and plan.inserted is True
        beats = win._session.beats
        assert plan.beat_index == 0
        assert 'bg classroom@leftish' in beats[0].show
        assert bodies and '补一条 show' in bodies[0]

    def test_the_preview_request_goes_the_same_way(self, ui_window):
        from storygraph.ui_qt import main_window as MW

        win = ui_window
        plan = MW._plan_preview_at(win, {
            'target': 'scene', 'tag': '', 'transform': 'leftish',
            'mode': 'source', 'scene_image': 'bg classroom',
            'beat_index': 2, 'source_index': -1})
        assert plan is not None and plan.inserted is True
        assert plan.beat_index == 0

    def test_the_preview_with_a_known_source_still_edits_that_line(
            self, ui_window):
        from storygraph.ui_qt import main_window as MW

        win = ui_window
        scene_i = next(i for i, b in enumerate(win._session.beats)
                       if str(getattr(b, 'scene', '') or '').strip())
        plan = MW._plan_preview_at(win, {
            'target': 'scene', 'tag': '', 'transform': 'leftish',
            'mode': 'source', 'scene_image': 'bg classroom',
            'beat_index': 2, 'source_index': scene_i})
        assert plan is not None and plan.inserted is False
        assert plan.beat_index == scene_i
