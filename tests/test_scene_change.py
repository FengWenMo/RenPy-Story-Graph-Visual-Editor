"""
tests/test_scene_change.py
==========================
**显式「换景」**（`scene`，2026-10-31）—— 只有人明确按下才写，确认框写清代价。

用户拍板的原话（三轮讨论的收口）：

> 换景（`scene`）会清台。只有在人**明确选"换景"**时才写，确认框写清
> "会清掉台上 N 个立绘"。

三层判据：

  ① `app.stage.plan_scene_change` —— 计划本身：写**哪一拍**、动**哪几个**
     字段、会清掉什么、什么时候拒绝；
  ② `app.stage.SceneEdit.apply/revert` —— 内存里的拍改得像不像，
     `scene` 令牌的位置对不对（补在**最前面**，否则 `show` 完立刻被清台）；
  ③ UI —— 尺寸面板只给**背景行**画「换景」，窗口层挑图 → 一张确认框
     （正文里必须出现"会清掉台上 N 个立绘"）→ 写内存 → 刷新卡片。

⚠ 这里**不测**"scene 清台"的引擎语义（`tests/test_stage_snapshot_scene_at.py`
的 `TestSceneClearsTheWholeMasterLayer`）—— 那是快照层的事，这里只是消费它。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from storygraph.app.stage import SceneEdit, plan_scene_change      # noqa: E402
from storygraph.story.atl import parse_transforms                   # noqa: E402
from storygraph.story.beat_model import Beat                        # noqa: E402
from storygraph.story.beat_render import render_beat_to_rpy         # noqa: E402
from storygraph.story.stage_snapshot import stage_snapshots         # noqa: E402

TRANSFORMS = '''transform bgfit:
    fit "cover"

transform half:
    zoom 0.5
'''


@pytest.fixture(scope='module')
def transforms():
    return parse_transforms(TRANSFORMS)


def _beats():
    """
    4 拍：第 0 拍写背景，第 1 拍上台一个人，第 2/3 拍只是继承。

    在**第 3 拍**换景 = 新写一条 `scene`（本来没有）→ 台上的人会被清掉。
    """
    return [
        Beat(seq=1, label='t', text='', scene='bg big'),
        Beat(seq=2, label='t', text='', show='ling happy'),
        Beat(seq=3, label='t', text='一'),
        Beat(seq=4, label='t', text='二'),
    ]


# ==================================================================
#  ① 计划：写哪一拍 / 动哪些字段 / 什么时候拒绝
# ==================================================================

class TestPlanBasics:
    def test_a_beat_without_scene_gets_a_new_one(self, transforms):
        beats = _beats()
        edit = plan_scene_change(beats, 3, 'bg wide', at='bgfit',
                                 transform_index=transforms)
        assert edit.ok, edit.reason
        assert edit.added is True
        assert edit.beat_index == 3          # 换景是**事件**：写请求的那一拍
        assert edit.scene_before == '' and edit.scene_at_before == ''
        assert edit.apply()
        assert beats[3].scene == 'bg wide'
        assert beats[3].scene_at == 'bgfit'
        assert beats[0].scene == 'bg big'    # 出处那一拍一个字都没动

    def test_apply_then_revert_is_byte_exact(self, transforms):
        beats = _beats()
        snapshot = [(b.scene, b.scene_at, b.show, b.op_order) for b in beats]
        edit = plan_scene_change(beats, 3, 'bg wide', at='bgfit',
                                 transform_index=transforms)
        assert edit.apply()
        assert beat_tuple(beats[3]) != snapshot[3]
        assert edit.revert()
        assert [beat_tuple(b) for b in beats] == snapshot

    def test_replacing_an_existing_scene_keeps_it_a_scene_beat(self, transforms):
        beats = _beats()
        edit = plan_scene_change(beats, 0, 'bg wide', at='bgfit',
                                 transform_index=transforms)
        assert edit.ok, edit.reason
        assert edit.added is False
        assert edit.apply()
        assert beats[0].scene == 'bg wide' and beats[0].scene_at == 'bgfit'

    def test_an_empty_image_is_refused(self, transforms):
        edit = plan_scene_change(_beats(), 3, '   ',
                                 transform_index=transforms)
        assert not edit.ok and '没有选新的背景图' in edit.reason

    def test_a_compact_image_name_is_refused(self, transforms):
        edit = plan_scene_change(_beats(), 3, 'bg wide@bgfit',
                                 transform_index=transforms)
        assert not edit.ok and '`@`' in edit.reason

    def test_an_unknown_transform_is_refused(self, transforms):
        edit = plan_scene_change(_beats(), 3, 'bg wide', at='nolike',
                                 transform_index=transforms)
        assert not edit.ok
        assert '项目里没有 transform' in edit.reason

    def test_the_same_scene_and_at_is_refused(self, transforms):
        beats = _beats()
        beats[2].scene, beats[2].scene_at = 'bg wide', 'bgfit'
        edit = plan_scene_change(beats, 2, 'bg wide', at='bgfit',
                                 transform_index=transforms)
        assert not edit.ok and '已经是这一景了' in edit.reason

    def test_out_of_range_index_is_refused(self, transforms):
        edit = plan_scene_change(_beats(), 9, 'bg wide',
                                 transform_index=transforms)
        assert not edit.ok and '索引越界' in edit.reason

    def test_a_no_op_scene_is_refused(self, transforms):
        """台上没人、背景又是同一张 → 这条 scene 什么都不会改，别写。"""
        beats = [Beat(seq=1, label='t', text='', scene='bg big'),
                 Beat(seq=2, label='t', text='')]
        edit = plan_scene_change(beats, 1, 'bg big',
                                 transform_index=transforms)
        assert not edit.ok and '什么都不会改' in edit.reason

    def test_the_same_background_with_people_is_flagged_as_clear_only(
            self, transforms):
        """
        挑的还是同一张背景、只是这一拍没写 scene → 这条 scene 唯一的作用
        就是**清台**。确认框必须这么说（别让人以为图变了）。
        """
        beats = [
            Beat(seq=1, label='t', text='', scene='bg big'),
            Beat(seq=2, label='t', text='', show='ling happy'),
            Beat(seq=3, label='t', text=''),
        ]
        edit = plan_scene_change(beats, 2, 'bg big', project_root=None,
                                 transform_index=transforms)
        assert edit.ok, edit.reason
        assert edit.same_image is True
        text = edit.diff_text()
        assert '同一张' in text and '把台上清空' in text

    def test_an_unknown_image_in_a_real_project_is_refused(self, tmp_path):
        """项目里没有这张图 → 拒绝（写进脚本 = 引擎报 NameError）。"""
        from _ui_project import PNG_1PX
        root = tmp_path / 'proj'
        img = root / 'game' / 'images' / 'bg' / 'wide.png'
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(PNG_1PX)
        beats = _beats()
        edit = plan_scene_change(beats, 3, 'bg wide', project_root=root,
                                 transform_index={})
        assert edit.ok, edit.reason          # 磁盘上有这张图 → 认得
        edit2 = plan_scene_change(beats, 3, 'bg nosuch', project_root=root,
                                  transform_index={})
        assert not edit2.ok
        assert '找不到这张图' in edit2.reason and 'NameError' in edit2.reason


def beat_tuple(beat):
    return (beat.scene, beat.scene_at, beat.show, beat.op_order)


# ==================================================================
#  ② 会清掉什么 / 顺序令牌 / 同一拍的 show
# ==================================================================

class TestCleared:
    def test_the_count_comes_from_the_stage_before_this_beat(self, transforms):
        edit = plan_scene_change(_beats(), 3, 'bg wide',
                                 transform_index=transforms)
        assert edit.cleared_sprites == ('ling happy',)
        assert edit.cleared_layers == ()
        assert '会清掉台上 1 个立绘：ling happy' in edit.diff_text()

    def test_cg_layers_are_counted_too(self, transforms):
        """`scene` 清的是整个 master 层 —— CG/UI 也会没（引擎语义）。"""
        beats = [
            Beat(seq=1, label='t', text='', scene='bg big'),
            Beat(seq=2, label='t', text='', show='cg cg01; ling happy'),
            Beat(seq=3, label='t', text=''),
        ]
        edit = plan_scene_change(beats, 2, 'bg wide',
                                 transform_index=transforms)
        assert edit.cleared_sprites == ('ling happy',)
        assert edit.cleared_layers == ('cg cg01',)
        text = edit.diff_text()
        assert '1 个立绘' in text and '1 个 CG/UI 图层' in text
        # 写完之后快照里真的没人了（不是"确认框说清台、预览还留着"）
        assert edit.apply()
        state = stage_snapshots(beats)[2]['state']
        assert state['actors'] == [] and state['layers'] == {}

    def test_nobody_on_stage_says_so_instead_of_a_zero(self, transforms):
        beats = [Beat(seq=1, label='t', text='', scene='bg big'),
                 Beat(seq=2, label='t', text='')]
        edit = plan_scene_change(beats, 1, 'bg wide',
                                 transform_index=transforms)
        assert edit.cleared_sprites == ()
        assert '台上没有立绘' in edit.diff_text()

    def test_a_same_beat_show_is_not_counted_as_cleared(self, transforms):
        """
        同一拍里写的 `show` 排在（新插入的）scene **之后** —— 它不会被清掉，
        所以不该算进"会清掉 N 个立绘"。写完之后快照里它确实还在。
        """
        beats = [
            Beat(seq=1, label='t', text='', scene='bg big'),
            Beat(seq=2, label='t', text='', show='ling happy'),
        ]
        edit = plan_scene_change(beats, 1, 'bg wide',
                                 transform_index=transforms)
        assert edit.cleared_sprites == ()
        assert edit.apply()
        state = stage_snapshots(beats)[1]['state']
        assert state['actors'] == [['ling happy', '']]

    def test_shows_before_an_existing_scene_are_flagged(self, transforms):
        """作者自己把 show 写在 scene 前面（`show A / scene B`）→ 提醒一句。"""
        beats = [
            Beat(seq=1, label='t', text='', scene='bg old'),
            Beat(seq=2, label='t', text='', show='ling happy', scene='bg big',
                  op_order='show#0,scene#0'),
        ]
        edit = plan_scene_change(beats, 1, 'bg wide',
                                 transform_index=transforms)
        assert edit.shows_before_scene is True
        assert '排在 scene 之前' in edit.diff_text()


class TestOrderTokens:
    def test_the_scene_token_goes_to_the_front(self, transforms):
        """
        `op_order='show#0,code#0'` 的拍上新写 scene → 令牌必须补在**最前面**。

        不补的话 `op_tokens_full()` 会把缺的 verb 补在**末尾**（`show → code
        → scene`）——产物是"人刚上台又被清掉"，与换景的意图相反。
        """
        beats = [
            Beat(seq=1, label='t', text='', scene='bg big'),
            Beat(seq=2, label='t', text='', show='ling happy',
                  op_order='show#0,code#0'),
        ]
        beats[1].code = '$ x = 1'
        edit = plan_scene_change(beats, 1, 'bg wide',
                                 transform_index=transforms)
        assert edit.order_field == 'op_order'
        assert edit.order_after == 'scene,show#0,code#0'
        assert edit.apply()
        lines = render_beat_to_rpy(beats[1])
        assert lines.index('scene bg wide') < lines.index('show ling happy')

    def test_stage_order_only_gets_the_token_too(self, transforms):
        beats = [Beat(seq=1, label='t', text='', show='ling happy',
                      stage_order='show')]
        edit = plan_scene_change(beats, 0, 'bg wide',
                                 transform_index=transforms)
        assert edit.order_field == 'stage_order'
        assert edit.order_after == 'scene,show'
        assert edit.apply()
        lines = render_beat_to_rpy(beats[0])
        assert lines.index('scene bg wide') < lines.index('show ling happy')

    def test_no_order_information_needs_no_token(self, transforms):
        """两张顺序列都空 → 渲染器本来就先 scene；不硬塞一个令牌。"""
        beats = _beats()
        edit = plan_scene_change(beats, 3, 'bg wide',
                                 transform_index=transforms)
        assert edit.order_field == ''
        assert edit.apply()
        assert render_beat_to_rpy(beats[3])[0] == 'scene bg wide'

    def test_an_existing_scene_token_is_not_duplicated(self, transforms):
        beats = [Beat(seq=1, label='t', text='', scene='bg big',
                      op_order='scene')]
        edit = plan_scene_change(beats, 0, 'bg wide',
                                 transform_index=transforms)
        assert edit.order_field == ''
        assert edit.apply()
        assert beats[0].op_order == 'scene'


class TestSameBeatBackgroundShow:
    def test_a_simple_show_of_the_same_tag_is_removed(self, transforms):
        """
        同一拍里 `show bg big` 排在 scene 之后 → 会把新背景盖回旧那张。
        换景把它去掉（并在确认框里说清）。
        """
        beats = [Beat(seq=1, label='t', text='', show='bg big',
                      op_order='show#0')]
        edit = plan_scene_change(beats, 0, 'bg wide',
                                 transform_index=transforms)
        assert edit.ok, edit.reason
        assert edit.removed_show == 'bg big'
        assert edit.show_after == ''
        assert '`show bg big` 会去掉' in edit.diff_text()
        assert edit.apply()
        assert beats[0].show == ''
        assert render_beat_to_rpy(beats[0]) == ['scene bg wide']

    def test_other_tags_are_kept(self, transforms):
        beats = [Beat(seq=1, label='t', text='', show='cg cg01; bg big',
                      op_order='show#0,show#1')]
        edit = plan_scene_change(beats, 0, 'bg wide',
                                 transform_index=transforms)
        assert edit.removed_show == 'bg big'
        assert edit.show_after == 'cg cg01'
        assert edit.apply()
        assert [ln for ln in render_beat_to_rpy(beats[0])
                if ln.startswith('show ')] == ['show cg cg01']

    def test_a_complex_background_show_is_refused(self, transforms):
        beats = [Beat(seq=1, label='t', text='',
                      show='bg big behind ling', op_order='show#0')]
        edit = plan_scene_change(beats, 0, 'bg wide',
                                 transform_index=transforms)
        assert not edit.ok
        assert '复杂子句' in edit.reason

    def test_the_other_background_tag_spelling_counts_as_the_same_layer(
            self, transforms):
        """
        `show background old` + `scene bg new` 写的是**同一个图层**（词表
        `NON_ACTOR_TAGS` 里 `bg / background / bg_` 三种都收）。只比字符串
        会漏掉它 → 那条 show 排在 scene 后面，产物里的背景还是旧那张。
        """
        beats = [Beat(seq=1, label='t', text='', show='background old',
                      op_order='show#0')]
        edit = plan_scene_change(beats, 0, 'bg wide',
                                 transform_index=transforms)
        assert edit.ok, edit.reason
        assert edit.removed_show == 'background old'
        assert edit.show_after == ''


# ==================================================================
#  ③ 确认框正文 / 影响区间
# ==================================================================

class TestDiffText:
    def test_the_body_says_what_will_be_cleared_and_shows_the_new_line(
            self, transforms):
        beats = _beats()
        edit = plan_scene_change(beats, 3, 'bg wide', at='bgfit',
                                 transform_index=transforms)
        text = edit.diff_text()
        assert '第 4 拍' in text
        assert '新写一条 scene（会清台）' in text
        assert '会清掉台上 1 个立绘：ling happy' in text
        assert '+scene bg wide at bgfit' in text
        assert '保留' in text and 'show' in text

    def test_the_affected_range_stops_at_the_next_scene(self, transforms):
        beats = [
            Beat(seq=1, label='t', text=''),
            Beat(seq=2, label='t', text=''),
            Beat(seq=3, label='t', text='', scene='bg next'),
            Beat(seq=4, label='t', text=''),
        ]
        edit = plan_scene_change(beats, 0, 'bg wide',
                                 transform_index=transforms)
        assert edit.affects == (0, 1)
        assert '影响：第 1–2 拍（共 2 拍）' in edit.diff_text()

    def test_planning_does_not_touch_the_beat(self, transforms):
        beats = _beats()
        before = [beat_tuple(b) for b in beats]
        plan_scene_change(beats, 3, 'bg wide', at='bgfit',
                          transform_index=transforms).diff_text()
        assert [beat_tuple(b) for b in beats] == before


# ==================================================================
#  ④ UI：面板只给背景行画「换景」，窗口层是一张说清代价的确认框
# ==================================================================

pytest.importorskip('PySide6')

from PySide6.QtWidgets import QMessageBox                     # noqa: E402

from storygraph.ui_qt import card_actions as CA               # noqa: E402
from storygraph.ui_qt import main_window as MW                # noqa: E402
from storygraph.ui_qt.size_panel import SizePanel             # noqa: E402


def _rows():
    return [
        {'index': 0, 'kind': 'scene', 'tag': '', 'spec': 'bg big',
         'label': '背景', 'at': '', 'source': 3, 'natural': (800, 600),
         'note': '背景 bg big 800×600 → 屏幕 400×300（第 4 拍设的）',
         'items': [
             {'key': 'as_is', 'at': '', 'text': '原样（已经是原样）',
              'ok': False, 'why': '这一行本来就没有 at', 'current': True},
             {'key': 'cover', 'at': 'bgfit', 'text': '铺满 → bgfit',
              'ok': True, 'why': '', 'current': False},
         ], 'why': ''},
        {'index': 1, 'kind': 'layer', 'tag': 'cg', 'spec': 'cg cg1',
         'label': 'CG', 'at': '', 'source': 1, 'natural': (1600, 600),
         'note': 'CG cg cg1', 'items': [
             {'key': 'as_is', 'at': '', 'text': '原样', 'ok': True,
              'why': '', 'current': True},
         ], 'why': ''},
    ]


class TestPanelSceneButton:
    def test_only_the_background_row_has_the_button(self, qapp):
        panel = SizePanel()
        panel.set_rows(_rows())
        panel.show()
        try:
            qapp.processEvents()
            assert panel.slots()[0]['swap'].isVisibleTo(panel) is True
            assert panel.slots()[1]['swap'].isVisibleTo(panel) is False
        finally:
            panel.close()

    def test_the_request_carries_the_current_image_and_selected_preset(
            self, qapp):
        panel = SizePanel()
        panel.set_rows(_rows())
        panel.slots()[0]['combo'].setCurrentIndex(1)      # 铺满 → bgfit
        req = panel.scene_request(0)
        assert req['kind'] == 'scene' and req['spec'] == 'bg big'
        assert req['at'] == 'bgfit' and req['generate'] is False

    def test_the_selection_is_used_even_when_it_cannot_be_written(self, qapp):
        """
        「原样（已经是原样）」对**现在这张图**不可写，但对**新背景**完全合法
        ——换景不拿那个 ok 当门槛（否则最常见的一条反而点不动）。
        """
        panel = SizePanel()
        panel.set_rows(_rows())
        assert panel.slots()[0]['combo'].currentIndex() == 0   # 原样（不可写）
        assert panel.request(0) is None
        req = panel.scene_request(0)
        assert req is not None and req['at'] == '' and req['key'] == 'as_is'

    def test_a_layer_row_has_no_scene_request(self, qapp):
        panel = SizePanel()
        panel.set_rows(_rows())
        assert panel.scene_request(1) is None

    def test_clicking_emits_the_scene_request(self, qapp):
        panel = SizePanel()
        panel.set_rows(_rows())
        got = []
        panel.scene_requested.connect(got.append)
        panel.slots()[0]['swap'].click()
        assert len(got) == 1 and got[0]['kind'] == 'scene'
        assert got[0]['spec'] == 'bg big'


def _yes(*_a, **_k):
    return QMessageBox.StandardButton.Yes


def _no(*_a, **_k):
    return QMessageBox.StandardButton.No


def _beat_with_sprites(win) -> int:
    """找一个"不为空、台上有人、本来没有 scene"的拍（换景会真的清台）。"""
    from storygraph.app.stage import build_frames
    frames = build_frames(list(win._session.beats))
    for i, f in enumerate(frames):
        beat = win._session.beats[i]
        if f.sprites and not str(getattr(beat, 'scene', '') or '').strip():
            return i
    raise AssertionError('这一章没有"台上有人、本拍没写 scene"的拍')


class TestWindowSceneFlow:
    def test_swapping_the_background_writes_a_scene_and_clears_the_stage(
            self, ui_window, monkeypatch):
        win = ui_window
        i = _beat_with_sprites(win)
        win._show_index(i)
        asked = {}

        def _ask(_win, req):
            asked.update(req or {})
            return 'bg rooftop'

        monkeypatch.setattr(CA, '_ask_scene_image', _ask)
        bodies = []

        def _confirm(_parent, title, body, *_a, **_k):
            bodies.append((title, body))
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(CA.QMessageBox, 'question', staticmethod(_confirm))
        panel = win._panel.size_panel()
        assert panel.rows(), '这一拍没有尺寸行'
        spec_before = panel.rows()[0]['spec']
        panel.slots()[0]['swap'].click()
        beat = win._session.beats[i]
        assert beat.scene == 'bg rooftop'
        assert asked.get('spec') == spec_before
        assert bodies, '没有弹确认框'
        title, body = bodies[-1]
        assert '换景' in title
        assert '会清掉台上' in body and '个立绘' in body
        assert 'show' in body          # 要保留立绘该走哪条路，写在框里
        # 台账与控制台都说清了代价
        assert '[换景]' in win._console.toPlainText()

    def test_cancel_changes_nothing(self, ui_window, monkeypatch):
        win = ui_window
        i = _beat_with_sprites(win)
        win._show_index(i)
        monkeypatch.setattr(CA, '_ask_scene_image',
                            lambda _w, _r: 'bg rooftop')
        monkeypatch.setattr(CA.QMessageBox, 'question', staticmethod(_no))
        before = str(getattr(win._session.beats[i], 'scene', '') or '')
        win._panel.size_panel().slots()[0]['swap'].click()
        assert str(win._session.beats[i].scene or '') == before

    def test_cancelling_the_picker_does_nothing_and_says_so(
            self, ui_window, monkeypatch):
        win = ui_window
        i = _beat_with_sprites(win)
        win._show_index(i)
        monkeypatch.setattr(CA, '_ask_scene_image', lambda _w, _r: '')
        before = str(getattr(win._session.beats[i], 'scene', '') or '')
        win._panel.size_panel().slots()[0]['swap'].click()
        assert str(win._session.beats[i].scene or '') == before
        assert '已取消' in win._status_bar.currentMessage()

    def test_a_refused_plan_is_reported_not_swallowed(self, ui_window,
                                                      monkeypatch):
        """挑回来的图项目里没有 → 计划拒绝，控制台点名（不是静默什么也没发生）。"""
        win = ui_window
        i = _beat_with_sprites(win)
        win._show_index(i)
        monkeypatch.setattr(CA, '_ask_scene_image',
                            lambda _w, _r: 'bg nosuchimage')
        monkeypatch.setattr(CA.QMessageBox, 'question', staticmethod(_yes))
        win._panel.size_panel().slots()[0]['swap'].click()
        assert '换景没做' in win._console.toPlainText()
        assert '找不到这张图' in win._console.toPlainText()


# ==================================================================
#  ⑤ 卡片「背景」那一格：另一条会写出 `scene` 的路，不许静默
# ==================================================================

class TestCardSceneCell:
    def test_typing_a_background_where_there_was_none_is_called_out(
            self, ui_window):
        """
        「背景」一格填上图 = 新写一条 `scene`（从"没有背景"变成"有背景"）。
        数据照常保存（可撤销），但控制台/状态栏必须点名被带走的立绘 ——
        卡片上看不见那些人（是上一拍 show 的），不说就只剩"人突然没了"。
        """
        win = ui_window
        i = _beat_with_sprites(win)
        win._show_index(i)
        win._panel._set_value('Scene', 'bg rooftop')
        win.save_current_beat()
        assert win._session.beats[i].scene == 'bg rooftop'
        console = win._console.toPlainText()
        assert '[换景]' in console and '个立绘' in console
        assert 'scene' in win._status_bar.currentMessage()

    def test_an_empty_stage_gets_no_warning_at_all(self, ui_window):
        """台上没人 → 一句话都不说（新建一景是最常见的情形，别制造噪音）。"""
        from storygraph.ui_qt import window_card as WC
        win = ui_window
        before = win._console.toPlainText()
        assert WC.note_scene_added(win, 0, 'bg rooftop') == ''
        assert win._console.toPlainText() == before

    def test_a_replaced_background_is_not_a_new_scene(self, ui_window):
        """第 0 拍本来就有 scene（只是换图）→ 不是"新写一条"，不点名。"""
        win = ui_window
        assert str(getattr(win._session.beats[0], 'scene', '') or '').strip()
        win._show_index(0)
        win._panel._set_value('Scene', 'bg rooftop')
        win.save_current_beat()
        assert win._session.beats[0].scene == 'bg rooftop'
        assert '[换景]' not in win._console.toPlainText()
