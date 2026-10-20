"""
删掉一拍之前，人得**看得见要丢几行**（用户 2026-10-21）

> breakend.0027 这一拍包含 7 行代码 + 1 句台词……删这一拍就是删 8 行，
> -8+2=-6，数目完全对。所以数据是对的，问题是**人看不见**：这一拍的源码
> 写在表里「代码」那一格，而 Excel 单元格默认只有一行高，滚动时根本
> 看不出里面塞了 7 行。

判据分四层：
  ① 会话层算得对（行数 = 与导出同一套渲染出来的行数，且**不改任何状态**）；
  ② 确认框里真的写出来了（做/不做都行，但话必须说）；
  ③ 删完的控制台／状态栏带着这句话（事后回看也查得到）；
  ④ 选中的那一拍，面包屑上就有「（产物 N 行）」（不用等删除）。
"""
from __future__ import annotations

import pytest

pytest.importorskip('PySide6')

from PySide6.QtWidgets import QMessageBox                       # noqa: E402


def _window(ui_window):
    return ui_window


# ==================================================================
#  ① 会话层
# ==================================================================

class TestDeletePreview:
    def test_reports_how_many_lines_and_which_ones(self, ui_project):
        """
        「要删几行」不许猜：必须和**导出**渲染出来的行数一模一样。

        第 2 拍（`ch01.0002`）在产物里是 4 行：`voice …` / `show …` /
        `play sound …` / 那句台词。
        """
        from storygraph.app.editor import EditorSession

        s = EditorSession(project_root=ui_project)
        s.load(str(ui_project / 'game' / 'script' / 'ch01.rpy'))
        beat = s.beats[1]
        want = s.rendered_lines_of(beat)
        assert len(want) > 1, '这条判据要一拍多于 1 行的脚本'

        got = s.delete_preview(1)
        assert got['ok'], got
        assert got['count'] == 1
        assert got['lines'] == len(want), (got['lines'], len(want))
        assert got['head'], '没给出"前几行"，人还是看不见'
        assert got['head'][0] == want[0].strip()
        assert [b['line_id'] for b in got['beats']] == [beat.line_id]

    def test_preview_does_not_touch_the_session(self, ui_project):
        """预览不许有副作用：拍数、拍上的字段都不许变。"""
        from storygraph.app.editor import EditorSession

        s = EditorSession(project_root=ui_project)
        s.load(str(ui_project / 'game' / 'script' / 'ch01.rpy'))
        before = [(b.line_id, b.text, b.code, b.raw) for b in s.beats]
        for i in range(len(s.beats)):
            s.delete_preview(i)
        assert len(s.beats) == len(before)
        assert [(b.line_id, b.text, b.code, b.raw) for b in s.beats] == before

    def test_block_header_still_reports_the_block(self, ui_project):
        """
        块头（菜单/循环）带着块体时：单删被拒绝，但要顺带说"连块体一共几行"。
        """
        from storygraph.app.editor import EditorSession

        s = EditorSession(project_root=ui_project)
        s.load(str(ui_project / 'game' / 'script' / 'ch01.rpy'))
        menu_at = next(i for i, b in enumerate(s.beats)
                       if (getattr(b, 'kind', '') or '') == 'menu')
        single = s.delete_preview(menu_at)
        assert single['ok'] is False and single['can_cascade'] is True
        wide = s.delete_preview(menu_at, cascade=True)
        assert wide['ok'] is True
        assert wide['count'] >= 2, wide
        assert wide['lines'] >= 2, wide


# ==================================================================
#  ② 确认框
# ==================================================================

class TestDeleteDialogSaysIt:
    def test_the_confirm_text_names_the_lines(self, ui_window, monkeypatch):
        """
        确认框里必须写"这一拍在产物里是 N 行" + 前几行原文。

        这里让用户点「No」：**只验话说没说**，不动数据。
        """
        from storygraph.ui_qt import window_card as WC

        seen = []

        def _no(parent, title, text, *a, **k):
            seen.append(str(text))
            return QMessageBox.No

        monkeypatch.setattr(WC.QMessageBox, 'question', staticmethod(_no))
        w = _window(ui_window)
        w._set_page(w.PAGE_CARD)
        w._show_index(1)
        w.delete_card()
        assert seen, '连确认框都没弹'
        text = seen[0]
        assert '在产物里是' in text and '行' in text, text
        assert 'voice' in text or 'show' in text, f'没给出前几行原文：{text}'
        assert len(w._session) == 13, '点了「No」却删了一拍'

    def test_a_single_line_beat_does_not_get_noise(self, ui_window, monkeypatch):
        """只有一行的拍**不弹**那段警告（多数拍是这样，别制造噪音）。"""
        from storygraph.ui_qt import window_card as WC

        w = _window(ui_window)
        note = w._delete_line_note({'ok': True, 'lines': 1, 'head': ['e "hi"']})
        assert note == ''
        shown = w._delete_line_note({'ok': True, 'lines': 8,
                                     'head': ['$ renpy.block_rollback()',
                                              'hide screen hidden_timer']})
        assert '8 行' in shown and 'renpy.block_rollback' in shown
        assert '其余 6 行' in shown, shown


# ==================================================================
#  ③ 删完的日志 / 状态栏
# ==================================================================

class TestAfterDeleting:
    def test_the_log_and_status_keep_the_number(self, ui_window, monkeypatch):
        """
        删掉之后**还查得到**："产物里少了 4 行（voice v_ling_01 ／ show ling oh …）"。

        为什么不是只在确认框里说：人常常点完就忘，事后回看控制台要能对账。
        """
        from storygraph.ui_qt import window_card as WC

        monkeypatch.setattr(WC.QMessageBox, 'question',
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        w = _window(ui_window)
        w._set_page(w.PAGE_CARD)
        w._show_index(1)
        want = len(w._session.rendered_lines_of(w._session.beats[1]))
        w.delete_card()
        log = w._console.toPlainText()
        assert '产物里少了' in log, log[-400:]
        assert f'{want} 行' in log, (want, log[-400:])
        assert len(w._session) == 12


# ==================================================================
#  ④ 面包屑：不用等删除，选中就看得见
# ==================================================================

class TestCrumbHint:
    def test_the_crumb_shows_the_line_count(self, ui_window):
        w = _window(ui_window)
        w._set_page(w.PAGE_CARD)
        w._show_index(1)
        hint = w._crumb_hint.text()
        assert '产物' in hint and '行' in hint, hint

    def test_a_single_line_beat_says_nothing(self, ui_window):
        """一行拍不加括号（否则每一拍都挂个「（产物 1 行）」，纯噪音）。"""
        w = _window(ui_window)
        w._set_page(w.PAGE_CARD)
        one = next(i for i, b in enumerate(w._session.beats)
                   if len(w._session.rendered_lines_of(b)) == 1)
        w._show_index(one)
        assert '产物' not in w._crumb_hint.text()
