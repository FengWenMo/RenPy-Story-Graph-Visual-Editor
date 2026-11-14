# -*- coding: utf-8 -*-
"""
「✂ 句中换表情」的前端那半（2026-11-14）
========================================

会话那半（`split_say_at` 拆出什么、导出成什么、怎么撤）钉在
`tests/test_say_split_mid_sentence.py`。这里只钉**界面上看得见的三件事**：

  1. 按钮**只在对白 / 旁白 / 接续卡上出现**（控制流卡、语句卡上不出现 ——
     那些卡没有"一句台词"可断）；
  2. 它把**台词框里的光标位置**交出去（不是"句尾"、也不是"整句"）；
  3. 窗口真的会用它：点一下 → 会话多出一张「接续」卡，并且**停在那一张**上
     （下一步就是改立绘表情）。

⚠ 这里必须**真的点按钮**：PySide6 里槽函数抛异常只打 stderr，界面不动
（项目记忆 §Qt 第一条坑）。只测"方法干了什么"测不出按钮是死的。
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6')

from PySide6.QtGui import QTextCursor                    # noqa: E402


@pytest.fixture
def panel(qapp):
    from storygraph.ui_qt.card_editor import BeatCardEditor

    p = BeatCardEditor()
    yield p
    p.deleteLater()


def _put_cursor(panel, pos: int):
    """把台词框的光标放到第 pos 个字符（模拟人点在那儿）。"""
    cursor = panel._text.textCursor()
    cursor.setPosition(max(0, int(pos)))
    panel._text.setTextCursor(cursor)


# ---------------------------------------------------------------------------
#  ① 按钮什么时候出现
# ---------------------------------------------------------------------------

class TestButtonVisibility:

    def test_对白卡上出现(self, panel):
        panel.set_text('蒋忱，你在这里吗……哇！')
        panel.set_sentence_context('say', '蒋忱，你在这里吗……哇！', '')
        assert not panel._split_btn.isHidden()

    def test_接续卡上也有_可以反复拆(self, panel):
        panel.set_text('哇！')
        panel.set_sentence_context('extend', '哇！', '')
        assert not panel._split_btn.isHidden()

    @pytest.mark.parametrize('kind,text', [
        ('flow', ''),            # 控制流卡
        ('menu', '选哪个？'),      # 分支卡
        ('say', ''),             # 空台词：没有"中间"可断
    ])
    def test_别的卡上没有这个按钮(self, panel, kind, text):
        panel.set_sentence_context(kind, text, '')
        assert panel._split_btn.isHidden()


# ---------------------------------------------------------------------------
#  ② 说明行：把"这句没说完 / 这张是接续"说出口
# ---------------------------------------------------------------------------

class TestCaptionSaysWhereTheSentenceGoes:

    def test_接续卡说清是同一个对话框(self, panel):
        panel.set_sentence_context('extend', '哇！', '')
        assert not panel._sentence_label.isHidden()
        assert '同一个对话框' in panel._sentence_label.text()

    def test_句末带_nw_且下一拍是接续_说清会接下去(self, panel):
        panel.set_sentence_context('say', '前半{nw}', 'extend')
        assert '接续' in panel._sentence_label.text()
        assert '注意' not in panel._sentence_label.text()

    def test_nw_后面没有接续卡_要报警(self, panel):
        """真坑：写了 `{nw}` 却没有 extend 跟着 —— 那半句会直接跳走。"""
        panel.set_sentence_context('say', '前半{nw}', 'say')
        msg = panel._sentence_label.text()
        assert '注意' in msg and '接续' in msg

    def test_普通台词卡不啰嗦(self, panel):
        panel.set_sentence_context('say', '蒋忱，你在这里吗……哇！', '')
        assert panel._sentence_label.isHidden()


# ---------------------------------------------------------------------------
#  ③ 信号带的是光标位置；窗口真的用它
# ---------------------------------------------------------------------------

class TestCursorAndWindow:

    def test_信号带光标位置(self, panel):
        panel.set_text('前半后半')
        _put_cursor(panel, 2)
        got = []
        panel.split_requested.connect(got.append)
        panel._split_btn.click()
        assert got == [2], got

    def test_选中一段时从选区起点断(self, panel):
        panel.set_text('前半后半')
        cursor = panel._text.textCursor()
        cursor.setPosition(2)
        cursor.setPosition(4, QTextCursor.KeepAnchor)
        panel._text.setTextCursor(cursor)
        got = []
        panel.split_requested.connect(got.append)
        panel._split_btn.click()
        assert got == [2], got

    def test_窗口点一下真的多出一张接续卡_并停在那张上(self, ui_window):
        session = ui_window._session
        idx = next(i for i, b in enumerate(session.beats)
                   if str(getattr(b, 'kind', '') or '') == 'say'
                   and str(getattr(b, 'text', '') or '').strip())
        ui_window._show_index(idx)
        before = len(session.beats)

        panel = ui_window._panel
        panel.set_text('前半后半')
        _put_cursor(panel, 2)
        panel._split_btn.click()          # ← 真点

        assert len(session.beats) == before + 1, '应该正好多出一张「接续」卡'
        assert session.beats[idx].text == '前半{nw}'
        assert session.beats[idx + 1].kind == 'extend'
        assert session.beats[idx + 1].text == '后半'
        assert ui_window._loaded_idx == idx + 1, '应该停在接续卡上（下一步改表情）'

    def test_窗口的撤销能把这句话收回去(self, ui_window):
        """点错了一键退回：撤销走结构撤销栈，**一步**回到"拆之前那一整句"。

        ⚠ 退回去的是**拆之前**那半句（也就是用户在台词框里刚改完的样子），
        不是源文件里的原句 —— "断开"只是这一下动作，不该连带把打字也撤掉。
        """
        session = ui_window._session
        idx = next(i for i, b in enumerate(session.beats)
                   if str(getattr(b, 'kind', '') or '') == 'say'
                   and str(getattr(b, 'text', '') or '').strip())
        ui_window._show_index(idx)
        before = len(session.beats)

        panel = ui_window._panel
        panel.set_text('前半后半')
        _put_cursor(panel, 2)
        panel._split_btn.click()
        assert len(session.beats) == before + 1

        ui_window.restore_card()
        assert len(session.beats) == before
        assert session.beats[idx].text == '前半后半'
        assert '{nw}' not in session.beats[idx].text
        assert all(getattr(b, 'kind', '') != 'extend' for b in session.beats)
