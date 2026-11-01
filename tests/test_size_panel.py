"""
tests/test_size_panel.py
========================
**卡片上的「尺寸」一格**（P1，2026-10-31）：面板只画表 + 递选择，计划/确认/写回在窗口层。

三层判据：

  ① `ui_qt.size_panel.SizePanel` —— 行/槽、预设下拉（不可用的条目**置灰**且
     说清为什么）、当前项打勾、「改」发出什么请求、空时的说明；
  ② 卡片（`BeatCardEditor`）—— `set_stage_size` 接得上、信号层能转出来；
  ③ 主窗口端到端（`ui_window`）—— 点「改」→ diff 确认 → 真的改到**最初写下
     那一行的那一拍**，取消则一个字节都不动。

⚠ 这里**不测**"预设怎么匹配 transform"（`tests/test_size_presets.py`）和
"行怎么算出来"（`tests/test_stage_size_rows.py`）；面板拿到的就是那两层的产物。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')

from PySide6.QtWidgets import QMessageBox                     # noqa: E402

from storygraph.ui_qt import card_actions as CA               # noqa: E402
from storygraph.ui_qt import main_window as MW                # noqa: E402
from storygraph.ui_qt.main_window import BeatCardEditor       # noqa: E402
from storygraph.ui_qt.size_panel import SizePanel             # noqa: E402


def _rows():
    """两行：背景（第 4 拍设的）+ 一个 CG 图层。"""
    return [
        {'index': 0, 'kind': 'scene', 'tag': '', 'spec': 'bg big',
         'label': '背景', 'at': '', 'source': 3, 'natural': (800, 600),
         'note': '背景 bg big 800×600 → 屏幕 400×300（无 at：游戏里会被裁切）'
                 '（第 4 拍设的）',
         'items': [
             {'key': 'as_is', 'at': '', 'text': '原样（已经是原样）',
              'ok': False, 'why': '这一行本来就没有 at', 'current': True,
              'note': '去掉这一行的 at'},
             {'key': 'cover', 'at': 'bgfit',
              'text': '铺满（裁掉多余） → bgfit（fit "cover"）',
              'ok': True, 'why': '', 'current': False,
              'note': '按比例放大到盖住屏幕'},
             {'key': 'pick', 'at': 'half', 'text': '自定义：half（zoom 0.5）',
              'ok': True, 'why': '', 'current': False, 'note': '套用已有变换'},
         ],
         'why': ''},
        {'index': 1, 'kind': 'layer', 'tag': 'cg', 'spec': 'cg cg1',
         'label': 'CG', 'at': 'bgfit', 'source': 1, 'natural': (1600, 600),
         'note': 'CG cg cg1 1600×600 → 屏幕 400×300（at bgfit（fit "cover"）：'
                 '正好铺满）',
         'items': [
             {'key': 'as_is', 'at': '', 'text': '原样（不套变换）',
              'ok': True, 'why': '', 'current': False, 'note': ''},
             {'key': 'cover', 'at': 'bgfit',
              'text': '铺满（裁掉多余） → bgfit（fit "cover"）',
              'ok': False, 'why': '已经是这个变换了', 'current': True,
              'note': ''},
         ],
         'why': ''},
    ]


# ==================================================================
#  ① 面板本身
# ==================================================================

class TestSizePanel:
    def test_rows_fill_the_slots_and_the_rest_stay_hidden(self, qapp):
        panel = SizePanel()
        panel.set_rows(_rows())
        panel.show()
        try:
            qapp.processEvents()
            slots = panel.slots()
            assert len(slots) >= 4
            assert slots[0]['widget'].isVisibleTo(panel)
            assert slots[1]['widget'].isVisibleTo(panel)
            assert not slots[2]['widget'].isVisibleTo(panel)
            assert '800×600' in slots[0]['note'].text()
            assert '第 4 拍设的' in slots[0]['note'].text()
            assert '1600×600' in slots[1]['note'].text()
        finally:
            panel.close()

    def test_empty_means_a_sentence_not_an_empty_box(self, qapp):
        panel = SizePanel()
        panel.set_rows([])
        panel.show()
        try:
            qapp.processEvents()
            assert panel.rows() == []
            assert not panel.slots()[0]['widget'].isVisibleTo(panel)
            assert '没有尺寸可调' in panel._empty.text()
            assert panel._empty.isVisibleTo(panel)
        finally:
            panel.close()

    def test_combo_lists_every_item_and_greys_the_impossible_ones(self, qapp):
        panel = SizePanel()
        panel.set_rows(_rows())
        combo = panel.slots()[0]['combo']
        assert [combo.itemText(i) for i in range(combo.count())] == \
            [i['text'] for i in _rows()[0]['items']]
        assert combo.model().item(0).isEnabled() is False      # 原样（没有 at）
        assert combo.model().item(1).isEnabled() is True
        # 不可用的那一条**自己带着原因**（hover 就能看到，不用点）
        assert '本来就没有 at' in combo.model().item(0).toolTip()

    def test_the_current_item_is_selected(self, qapp):
        panel = SizePanel()
        panel.set_rows(_rows())
        # 第 0 行当前是「原样」；第 1 行当前是 bgfit（o k=False）
        assert panel.slots()[0]['combo'].currentIndex() == 0
        assert panel.slots()[1]['combo'].currentIndex() == 1

    def test_the_button_is_disabled_when_the_current_item_cannot_be_written(
            self, qapp):
        panel = SizePanel()
        panel.set_rows(_rows())
        assert panel.slots()[0]['button'].isEnabled() is False   # 原样（已经是原样）
        panel.slots()[0]['combo'].setCurrentIndex(1)             # 铺满 → 可写
        assert panel.slots()[0]['button'].isEnabled() is True
        assert '拍号' in panel.slots()[0]['button'].toolTip()

    def test_request_carries_what_the_window_needs_and_nothing_else(self, qapp):
        panel = SizePanel()
        panel.set_rows(_rows())
        panel.slots()[0]['combo'].setCurrentIndex(1)
        req = panel.request(0)
        assert req == {'row': 0, 'key': 'cover', 'at': 'bgfit',
                       'kind': 'scene', 'tag': '', 'spec': 'bg big',
                       'source': 3, 'generate': False,
                       'natural': (800, 600), 'zoom': None,
                       'text': '铺满（裁掉多余） → bgfit（fit "cover"）'}

    def test_a_generating_item_is_flagged_and_carries_the_size(self, qapp):
        """「生成 sg_*」的条目要带 `generate=True` + 这张图的尺寸（写前查重用）。"""
        panel = SizePanel()
        rows = _rows()
        rows[0]['items'][1] = {'key': 'cover', 'at': 'sg_fit_cover',
                               'text': '铺满（裁掉多余）（生成 sg_fit_cover）',
                               'ok': True, 'why': '', 'current': False,
                               'generate': True, 'note': ''}
        panel.set_rows(rows)
        panel.slots()[0]['combo'].setCurrentIndex(1)
        req = panel.request(0)
        assert req['generate'] is True and req['at'] == 'sg_fit_cover'
        assert req['natural'] == (800, 600)

    def test_clicking_the_button_emits_the_request(self, qapp):
        panel = SizePanel()
        panel.set_rows(_rows())
        got = []
        panel.size_requested.connect(got.append)
        panel.slots()[0]['combo'].setCurrentIndex(1)
        panel.slots()[0]['button'].click()
        assert len(got) == 1 and got[0]['at'] == 'bgfit'

    def test_unwritable_selection_is_not_emitted(self, qapp):
        """「原样」在这一行不合法 → 按钮是灰的；就算硬调到它也发不出去。"""
        panel = SizePanel()
        panel.set_rows(_rows())
        got = []
        panel.size_requested.connect(got.append)
        panel.slots()[0]['combo'].setCurrentIndex(0)      # 原样（不可写）
        panel.slots()[0]['button'].click()
        assert got == []
        assert panel.request(0) is None

    def test_a_row_that_cannot_be_edited_says_why_in_the_tooltip(self, qapp):
        panel = SizePanel()
        rows = _rows()
        rows[0]['why'] = '找不到这张图（尺寸未知，只能原样或自定义）'
        panel.set_rows(rows)
        tip = panel.slots()[0]['combo'].toolTip()
        assert '尺寸未知' in tip

    def test_long_image_ids_do_not_widen_the_column(self, qapp):
        """40 字的长图名不许把面板顶宽（右栏 340 的老毛病）。"""
        panel = SizePanel()
        rows = _rows()
        rows[0]['note'] = ('背景 bg ' + 'x' * 40 + ' 800×600 → 屏幕 400×300'
                           '（无 at：游戏里会被裁切）')
        panel.set_rows(rows)
        panel.show()
        try:
            qapp.processEvents()
            assert panel.minimumSizeHint().width() <= 220, \
                panel.minimumSizeHint().width()
        finally:
            panel.close()


# ==================================================================
#  ①b 行下面那句「本景还有 N 处单独改写」（2026-10-31，只读提示）
# ==================================================================

def _advice(level='hint', count=2, unknown=False, reason=''):
    from storygraph.story.override_advice import OverrideAdvice, Site
    sites = tuple(Site(index=3 + i, at='half', image='bg big',
                       clause='bg big at half') for i in range(count))
    return OverrideAdvice(level=level, sites=sites, unknown=unknown,
                          reason=reason)


class TestAdviceLine:
    def test_no_advice_key_hides_the_line(self, qapp):
        panel = SizePanel()
        panel.set_rows(_rows())            # 老数据没有 'advice'
        assert panel.slots()[0]['advice'].isVisibleTo(panel) is False

    def test_zero_sites_is_silence(self, qapp):
        panel = SizePanel()
        rows = _rows()
        rows[0]['advice'] = _advice(level='none', count=0)
        panel.set_rows(rows)
        assert panel.slots()[0]['advice'].isVisibleTo(panel) is False

    def test_a_hint_shows_one_line_in_note_colour(self, qapp):
        panel = SizePanel()
        rows = _rows()
        rows[0]['advice'] = _advice(level='hint', count=2)
        panel.set_rows(rows)
        lab = panel.slots()[0]['advice']
        assert lab.isVisibleTo(panel) is True
        assert '本景还有 2 处' in lab.text()
        assert '\n' not in lab.text()
        assert lab.property('role') == 'note'

    def test_a_notice_is_amber_and_keeps_the_long_text_in_the_tooltip(
            self, qapp):
        panel = SizePanel()
        rows = _rows()
        rows[0]['advice'] = _advice(level='notice', count=3)
        panel.set_rows(rows)
        lab = panel.slots()[0]['advice']
        assert lab.property('role') == 'warn'
        assert '合并回源' in lab.toolTip()

    def test_unknown_says_it_could_not_count(self, qapp):
        """算不出来**不许**变成"没有说话"（文档 §5.5 防误报）。"""
        panel = SizePanel()
        rows = _rows()
        rows[0]['advice'] = _advice(level='hint', count=0, unknown=True,
                                    reason='统计时出错了')
        panel.set_rows(rows)
        lab = panel.slots()[0]['advice']
        assert lab.isVisibleTo(panel) is True
        assert '统计不出' in lab.text()

    def test_the_advice_line_does_not_widen_the_panel(self, qapp):
        panel = SizePanel()
        rows = _rows()
        rows[0]['advice'] = _advice(level='notice', count=7)
        panel.set_rows(rows)
        panel.show()
        try:
            qapp.processEvents()
            assert panel.minimumSizeHint().width() <= 220, \
                panel.minimumSizeHint().width()
        finally:
            panel.close()

    def test_the_advice_line_clears_when_the_row_changes(self, qapp):
        """翻拍之后上一行的提示不许留在新行上。"""
        panel = SizePanel()
        rows = _rows()
        rows[0]['advice'] = _advice(level='notice', count=3)
        panel.set_rows(rows)
        lab = panel.slots()[0]['advice']
        assert lab.isVisibleTo(panel) is True
        rows[0]['advice'] = _advice(level='none', count=0)
        panel.set_rows(rows)
        assert lab.isVisibleTo(panel) is False
        assert lab.text() == ''


# ==================================================================
#  ② 卡片接得上
# ==================================================================

class TestCardWiring:
    def test_set_stage_size_feeds_the_panel(self, qapp):
        panel = BeatCardEditor()
        try:
            panel.set_stage_size(_rows())
            assert len(panel.size_panel().rows()) == 2
            panel.set_stage_size([])
            assert panel.size_panel().rows() == []
        finally:
            panel.close()

    def test_a_beat_without_stage_hides_the_whole_size_block(self, qapp):
        """没有背景/图层 → 整格收起（空框 + "没有尺寸可调" 在控制流卡上是噪音）。"""
        panel = BeatCardEditor()
        try:
            panel.show()
            qapp.processEvents()
            panel.set_stage_size(_rows())
            assert panel._size_box.isVisibleTo(panel) is True
            panel.set_stage_size([])
            assert panel._size_box.isVisibleTo(panel) is False
        finally:
            panel.close()

    def test_the_card_re_emits_the_size_request(self, qapp):
        panel = BeatCardEditor()
        got = []
        panel.size_panel().size_requested.connect(got.append)
        panel.set_stage_size(_rows())
        panel.size_panel().slots()[0]['combo'].setCurrentIndex(1)
        panel.size_panel().slots()[0]['button'].click()
        assert len(got) == 1 and got[0]['key'] == 'cover'


# ==================================================================
#  ③ 端到端：点「改」真的改到那一拍
# ==================================================================

def _yes(*_a, **_k):
    return QMessageBox.StandardButton.Yes


def _no(*_a, **_k):
    return QMessageBox.StandardButton.No


def _scene_beat_index(win) -> int:
    for i, b in enumerate(win._session.beats):
        if str(getattr(b, 'scene', '') or '').strip():
            return i
    raise AssertionError('这一章没有 scene，测不了尺寸面板')


def _select_pick(win, name: str) -> bool:
    """在第 0 行（背景）的「自定义」里选中 `name` 并按下「改」。"""
    panel = win._panel.size_panel()
    rows = panel.rows()
    assert rows, '尺寸面板一行都没有'
    items = rows[0]['items']
    idx = next((i for i, it in enumerate(items)
                if it['key'] == 'pick' and it['at'] == name), -1)
    assert idx >= 0, [it['text'] for it in items]
    slot = panel.slots()[0]
    slot['combo'].setCurrentIndex(idx)
    assert slot['button'].isEnabled(), items[idx]
    slot['button'].click()
    return True


class TestApplyFromCard:
    def test_applying_a_custom_transform_writes_the_scene_line(
            self, ui_window, monkeypatch):
        win = ui_window
        monkeypatch.setattr(CA.QMessageBox, 'question', staticmethod(_yes))
        i = _scene_beat_index(win)
        win._show_index(i)
        beat = win._session.beats[i]
        before = str(getattr(beat, 'scene_at', '') or '')
        assert before == '', f'这一拍本来就有 at={before!r}，换个夹具'
        _select_pick(win, 'leftish')
        assert win._session.beats[i].scene_at == 'leftish'
        # 卡片跟着刷了：尺寸行里「原样」现在可写（说明这一行真的有 at 了）
        rows = win._panel.size_panel().rows()
        assert rows and rows[0]['at'] == 'leftish'
        as_is = next(it for it in rows[0]['items'] if it['key'] == 'as_is')
        assert as_is['ok'] is True and as_is['current'] is False

    def test_cancel_changes_nothing(self, ui_window, monkeypatch):
        win = ui_window
        monkeypatch.setattr(CA.QMessageBox, 'question', staticmethod(_no))
        i = _scene_beat_index(win)
        win._show_index(i)
        beat = win._session.beats[i]
        before = str(getattr(beat, 'scene_at', '') or '')
        _select_pick(win, 'leftish')
        assert str(win._session.beats[i].scene_at or '') == before

    def test_the_confirm_box_names_the_later_rewrites(self, ui_window,
                                                     monkeypatch):
        """
        确认框里那句"本景后面还有 1 处…会盖回去"（2026-10-31）。

        尺寸面板改的是**源**那一行；本景后面只要有谁又 `show` 了一次同一个
        对象，这次改动就从那一刻起失效。这句必须在**同一个框**里 —
        否则人看到 diff 里只有一行改好，以为整景都改好了。
        """
        from storygraph.story.beat_model import Beat

        win = ui_window
        i = _scene_beat_index(win)
        win._show_index(i)
        scene = str(win._session.beats[i].scene or '').strip()
        assert scene, '这一拍没有背景，换个夹具'
        win._session.beats.insert(
            i + 1, Beat(seq=0, label='t', text='',
                        show=f'{scene} at half'))
        body = {}

        def _capture(_parent, _title, text, *_args, **_kw):
            body['text'] = text
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(CA.QMessageBox, 'question',
                            staticmethod(_capture))
        _select_pick(win, 'leftish')
        text = body.get('text') or ''
        assert '本景后面还有 1 处' in text, text
        assert '盖回去' in text, text
        # 这句在 diff 之前（diff 里只有一行，看不出"只到第几拍"）
        assert text.index('本景后面还有') < text.index('---')

    def test_as_is_on_a_clean_line_is_refused_out_loud(self, ui_window,
                                                       monkeypatch):
        """「原样」在没有 at 的行上是不可写的：不发请求、状态栏说清。"""
        win = ui_window
        monkeypatch.setattr(CA.QMessageBox, 'question', staticmethod(_yes))
        i = _scene_beat_index(win)
        win._show_index(i)
        panel = win._panel.size_panel()
        slot = panel.slots()[0]
        slot['combo'].setCurrentIndex(0)          # 原样（已经是原样）
        assert slot['button'].isEnabled() is False
        # 直接走窗口入口（模拟"误发"）：计划会拒绝，并把原因写到控制台
        plan = MW.apply_size_from_card(win, {
            'row': 0, 'key': 'as_is', 'at': '', 'kind': 'scene', 'tag': '',
            'spec': 'bg classroom', 'source': 0})
        assert plan is None
        assert '改尺寸没做' in win._console.toPlainText()


# ==================================================================
#  ④ 「一并改成一样」—— 合并回源（2026-10-31）
# ==================================================================
#
# 面板上那个按钮只在"本景真有单独改写"时出现；点下去走的是**逐处确认**
# （`ui_qt.merge_dialog`，一处一个勾）。判据分两段：按钮的显隐与文字（面板
# 自己）、以及端到端真的只改勾上的那几拍（窗口层）。

class TestMergeButton:
    def test_without_advice_there_is_no_button(self, qapp):
        panel = SizePanel()
        panel.set_rows(_rows())                   # 老数据没有 'advice'
        assert panel.slots()[0]['merge'].isVisibleTo(panel) is False

    def test_zero_sites_hides_the_button(self, qapp):
        panel = SizePanel()
        rows = _rows()
        rows[0]['advice'] = _advice(level='none', count=0)
        panel.set_rows(rows)
        assert panel.slots()[0]['merge'].isVisibleTo(panel) is False

    def test_the_button_names_the_count_and_says_what_it_will_touch(
            self, qapp):
        panel = SizePanel()
        rows = _rows()
        rows[0]['advice'] = _advice(level='hint', count=2)
        rows[0]['source_at'] = 'fit'
        panel.set_rows(rows)
        btn = panel.slots()[0]['merge']
        assert btn.isVisibleTo(panel) is True
        assert '2 处' in btn.text()
        tip = btn.toolTip()
        assert '源' in tip and '`fit`' in tip
        # L4 的边界必须写在按下去之前：不删行、逐处确认
        assert '不删行' in tip
        assert '逐处确认' in tip

    def test_unknown_hides_the_button(self, qapp):
        """连"有几处"都不知道 → 不给一个"要改不知道几行"的按钮。"""
        panel = SizePanel()
        rows = _rows()
        rows[0]['advice'] = _advice(level='hint', count=0, unknown=True,
                                    reason='统计时出错了')
        panel.set_rows(rows)
        assert panel.slots()[0]['merge'].isVisibleTo(panel) is False

    def test_a_cross_chapter_source_hides_the_button(self, qapp):
        """源不在这一章（`source=-1`）→ 没有"回源"可谈。"""
        panel = SizePanel()
        rows = _rows()
        rows[0]['advice'] = _advice(level='hint', count=2)
        rows[0]['source'] = -1
        panel.set_rows(rows)
        assert panel.slots()[0]['merge'].isVisibleTo(panel) is False

    def test_clicking_it_emits_what_the_window_needs(self, qapp):
        panel = SizePanel()
        rows = _rows()
        rows[0]['advice'] = _advice(level='hint', count=2)
        panel.set_rows(rows)
        got = []
        panel.merge_requested.connect(got.append)
        panel.slots()[0]['merge'].click()
        assert len(got) == 1
        req = got[0]
        assert req['kind'] == 'scene' and req['tag'] == ''
        assert req['source'] == 3 and req['count'] == 2
        assert req['label'] == '背景'

    def test_sites_that_already_agree_hide_the_button(self, qapp):
        """
        那几处**已经写着源那个值** → 按钮不出现：点了也是"没有要改的"。

        （这台机器的真机上就是这样：`sr_general` 那 58 处写的都是源同一个
        `@cg38402160`，合并无事可做。给一个按下去什么都不会发生的按钮，
        比没有按钮更糟。）
        """
        from storygraph.story.override_advice import OverrideAdvice, Site

        panel = SizePanel()
        rows = _rows()
        rows[0]['source_at'] = 'fit'
        rows[0]['advice'] = OverrideAdvice(
            level='hint', source_at='fit',
            sites=tuple(Site(index=4 + i, at='fit', image='bg big')
                        for i in range(2)))
        panel.set_rows(rows)
        assert panel.slots()[0]['merge'].isVisibleTo(panel) is False
        # 提示行仍在（源下次再改还是只到那一拍），只是换了口气
        assert '写着同一个 at' in panel.slots()[0]['advice'].text()

    def test_the_button_clears_when_the_row_changes(self, qapp):
        panel = SizePanel()
        rows = _rows()
        rows[0]['advice'] = _advice(level='hint', count=2)
        panel.set_rows(rows)
        btn = panel.slots()[0]['merge']
        assert btn.isVisibleTo(panel) is True
        rows[0]['advice'] = _advice(level='none', count=0)
        panel.set_rows(rows)
        assert btn.isVisibleTo(panel) is False


class TestMergeFromCard:
    def _setup(self, win):
        """在第 i 拍背景之后插两条"又 show 了一次"的拍（`@half` = 单元格写法）。"""
        from storygraph.story.beat_model import Beat

        i = _scene_beat_index(win)
        win._show_index(i)
        scene = str(win._session.beats[i].scene or '').strip()
        assert scene, '这一拍没有背景，换个夹具'
        for k in (1, 2):
            win._session.beats.insert(
                i + k, Beat(seq=0, label='t', text='', show=f'{scene}@half'))
        win._show_index(i)
        return i, scene

    def test_merge_rewrites_exactly_the_ticked_sites(self, ui_window,
                                                     monkeypatch):
        win = ui_window
        i, scene = self._setup(win)
        monkeypatch.setattr(CA, '_confirm_merge_plan',
                            lambda parent, plan: {i + 1, i + 2})
        slot = win._panel.size_panel().slots()[0]
        assert '2 处' in slot['merge'].text(), slot['merge'].text()
        slot['merge'].click()
        # 源是"原样"（没有 at）→ 那两处也变成原样
        assert win._session.beats[i + 1].show == scene
        assert win._session.beats[i + 2].show == scene
        assert '合并回源' in win._console.toPlainText()

    def test_a_ticked_subset_leaves_the_others_alone(self, ui_window,
                                                     monkeypatch):
        win = ui_window
        i, scene = self._setup(win)
        monkeypatch.setattr(CA, '_confirm_merge_plan',
                            lambda parent, plan: {i + 1})
        win._panel.size_panel().slots()[0]['merge'].click()
        assert win._session.beats[i + 1].show == scene
        assert win._session.beats[i + 2].show == f'{scene}@half'

    def test_cancel_changes_nothing(self, ui_window, monkeypatch):
        win = ui_window
        i, scene = self._setup(win)
        monkeypatch.setattr(CA, '_confirm_merge_plan',
                            lambda parent, plan: None)
        win._panel.size_panel().slots()[0]['merge'].click()
        assert win._session.beats[i + 1].show == f'{scene}@half'
        assert win._session.beats[i + 2].show == f'{scene}@half'

    def test_ticking_nothing_says_so_instead_of_silence(self, ui_window,
                                                        monkeypatch):
        win = ui_window
        i, scene = self._setup(win)
        monkeypatch.setattr(CA, '_confirm_merge_plan',
                            lambda parent, plan: set())
        win._panel.size_panel().slots()[0]['merge'].click()
        assert win._session.beats[i + 1].show == f'{scene}@half'
        assert '一处都没勾' in win._console.toPlainText()

    def test_the_source_at_is_what_gets_written(self, ui_window, monkeypatch):
        """目标值是**源那一行自己写着的** at，不是"这一拍看到的"。"""
        win = ui_window
        i, scene = self._setup(win)
        win._session.beats[i].scene_at = 'leftish'
        win._show_index(i)
        monkeypatch.setattr(CA, '_confirm_merge_plan',
                            lambda parent, plan: {i + 1, i + 2})
        win._panel.size_panel().slots()[0]['merge'].click()
        assert win._session.beats[i + 1].show == f'{scene}@leftish'
        assert win._session.beats[i + 2].show == f'{scene}@leftish'
        # 卡片跟着刷了：这一行现在显示的是源那个值
        rows = win._panel.size_panel().rows()
        assert rows[0]['source_at'] == 'leftish'
