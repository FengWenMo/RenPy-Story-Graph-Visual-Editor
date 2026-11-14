# -*- coding: utf-8 -*-
"""
「✂ 句中换表情」的前端那半（2026-11-14）
========================================

会话那半（`split_say_at` 拆出什么、导出成什么、怎么撤）钉在
`tests/test_say_split_mid_sentence.py`。这里只钉**界面上看得见的事**：

  1. 按钮**只在对白 / 旁白 / 接续卡上出现**（控制流卡、语句卡上不出现 ——
     那些卡没有"一句台词"可断）；台词从空变成有字时它跟着出现；
  2. **两步**：按一下进"选断开点"，在台词里点那个字，再按一下才真断 ——
     用户第一版报的原话是"点了一点反应都没有！也没有让我精确到哪个字"，
     所以这里逐条钉"每一次点击都有一个看得见的结果"；
  3. 断出来的就是那个字：前半 `{nw}`、后半进「接续」卡，窗口停在那一张上；
  4. 取消 / 换卡 = 一个字都不动，而且**不会**拿上一张卡的位置去断下一张。

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


def _say_card(panel, text='前半后半'):
    """摆成"一张有台词的对白卡"（窗口里 `_show_index` 做的就是这两步）。"""
    panel.set_text(text)
    panel.set_sentence_context('say', text, '')


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

    def test_台词从空变成有字_按钮跟着出现(self, panel):
        """载入时是空台词（按钮隐藏），用户打字之后它得自己出来 ——
        不然人得"存一下再翻回来"才看得到它。"""
        panel.set_sentence_context('say', '', '')
        assert panel._split_btn.isHidden()
        panel.set_text('现在有字了')
        assert not panel._split_btn.isHidden()


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
#  ③ 两步：先选断开点，再真断（用户 2026-11-14 报"点了没反应"）
# ---------------------------------------------------------------------------

class TestPickTheSplitPoint:

    def test_第一次点击只进选择模式_不断开(self, panel):
        _say_card(panel)
        got = []
        panel.split_requested.connect(got.append)
        panel._split_btn.click()

        assert got == [], '第一步不该真断'
        assert panel.is_picking_split_point()
        assert not panel._split_cancel.isHidden(), '要有「取消」这条退路'
        assert not panel._split_preview.isHidden(), '要点亮预览 —— 否则看着还是没反应'
        assert panel._split_btn.text() == panel.SPLIT_BTN_CONFIRM

    def test_预览点名断在哪个字前面_并给出两半(self, panel):
        _say_card(panel)
        panel._split_btn.click()
        _put_cursor(panel, 2)

        preview = panel._split_preview.text()
        assert '「后」' in preview, '要精确到字：断在「后」前面'
        assert '前半{nw}' in preview
        assert '后半' in preview

    def test_光标在句首时不猜_并说清怎么选(self, panel):
        _say_card(panel)
        panel._split_btn.click()
        _put_cursor(panel, 0)

        preview = panel._split_preview.text()
        assert not panel._split_btn.isEnabled(), '选不出断开点就不该能确认'
        assert '第 1 步' in preview
        assert '断不出两半' in preview

    def test_第二次点击才真断_位置就是那个字(self, panel):
        _say_card(panel)
        got = []
        panel.split_requested.connect(got.append)
        panel._split_btn.click()
        _put_cursor(panel, 2)
        panel._split_btn.click()

        assert got == [2]
        assert not panel.is_picking_split_point()

    def test_选中一段时从选区起点断(self, panel):
        _say_card(panel)
        cursor = panel._text.textCursor()
        cursor.setPosition(2)
        cursor.setPosition(4, QTextCursor.KeepAnchor)
        panel._text.setTextCursor(cursor)
        got = []
        panel.split_requested.connect(got.append)
        panel._split_btn.click()
        panel._split_btn.click()

        assert got == [2]

    def test_取消就什么都不动(self, panel):
        _say_card(panel)
        got = []
        panel.split_requested.connect(got.append)
        panel._split_btn.click()
        _put_cursor(panel, 2)
        panel._split_cancel.click()

        assert got == []
        assert not panel.is_picking_split_point()
        assert panel._split_cancel.isHidden()
        assert panel._split_btn.text() == panel.SPLIT_BTN_TEXT
        assert panel._split_btn.isEnabled()


# ---------------------------------------------------------------------------
#  ④ 每一次点击都得有看得见的结果（"点了没反应"这一类，逐条堵死）
# ---------------------------------------------------------------------------

class TestNoSilentNoReaction:

    def test_条件块里先说清理由_而不是只往状态栏扔一句(self, panel):
        """会话会拒绝这种卡（拆了会多出同条件的 `if` 块），
        但拒绝的话必须**摆在按钮下面** —— 屏幕上看不见就等于按钮是死的。"""
        panel.set_text('前半后半')
        panel.set_sentence_context(
            'say', '前半后半', '', blocked='这句话在条件块里，拆开会让块结构变样。')
        panel._split_btn.click()

        assert not panel.is_picking_split_point()
        assert not panel._sentence_label.isHidden()
        assert '条件块' in panel._sentence_label.text()

    def test_取消之后按钮还是能按的_不是灰死按钮(self, panel):
        """选择模式里"光标在句首"时确认键是灰的；取消之后**必须恢复** ——
        不然用户下一步面对的又是一颗点不动的按钮（这一轮要修的就是这个）。"""
        _say_card(panel)
        panel._split_btn.click()
        _put_cursor(panel, 0)
        assert not panel._split_btn.isEnabled(), '句首选不出断开点，这时候是灰的'

        panel._split_cancel.click()
        assert panel._split_btn.isEnabled()
        panel._split_btn.click()                 # 再按一下还能进选择模式
        assert panel.is_picking_split_point()

    def test_换到别的卡_选择模式自动退出(self, panel):
        """不退的话，第二次点击会拿着**上一张卡**的位置断**这一张**的句子。"""
        _say_card(panel)
        panel._split_btn.click()
        assert panel.is_picking_split_point()

        panel.set_sentence_context('flow', '', '')
        assert not panel.is_picking_split_point()
        assert panel._split_cancel.isHidden()

        got = []
        panel.split_requested.connect(got.append)
        panel._split_btn.click()          # 控制流卡上按钮是隐藏的；点了也不能断
        assert got == []

    def test_断不成时卡片里也会说话(self, ui_window, monkeypatch):
        """会话拒绝时那句话必须回到卡片上 —— 只进状态栏等于"点了没反应"。"""
        session = ui_window._session
        idx = next(i for i, b in enumerate(session.beats)
                   if str(getattr(b, 'kind', '') or '') == 'say'
                   and str(getattr(b, 'text', '') or '').strip())
        ui_window._show_index(idx)
        monkeypatch.setattr(
            session, 'split_say_at',
            lambda *a, **k: {'ok': False, 'error': '这句在条件块里，拆不了', 'index': -1})

        panel = ui_window._panel
        panel.set_text('前半后半')
        panel._split_btn.click()
        _put_cursor(panel, 2)
        panel._split_btn.click()

        assert not panel._sentence_label.isHidden()
        assert '条件块' in panel._sentence_label.text()


# ---------------------------------------------------------------------------
#  ⑤ 窗口真的用它：断出来的就是那个字，停在接续卡上
# ---------------------------------------------------------------------------

class TestWindowUsesThePickedPoint:

    def _first_say(self, ui_window):
        session = ui_window._session
        idx = next(i for i, b in enumerate(session.beats)
                   if str(getattr(b, 'kind', '') or '') == 'say'
                   and str(getattr(b, 'text', '') or '').strip())
        ui_window._show_index(idx)
        return session, idx

    def test_窗口上两步断句_停在接续卡上(self, ui_window):
        session, idx = self._first_say(ui_window)
        before = len(session.beats)
        panel = ui_window._panel
        panel.set_text('前半后半')

        panel._split_btn.click()          # 第一步：进选择模式
        assert len(session.beats) == before, '点一下不该立刻拆'
        _put_cursor(panel, 2)
        panel._split_btn.click()          # 第二步：真断

        assert len(session.beats) == before + 1, '应该正好多出一张「接续」卡'
        assert session.beats[idx].text == '前半{nw}'
        assert session.beats[idx + 1].kind == 'extend'
        assert session.beats[idx + 1].text == '后半'
        assert ui_window._loaded_idx == idx + 1, '应该停在接续卡上（下一步改表情）'
        assert not ui_window._panel.is_picking_split_point()

    def test_窗口上取消_一拍都不多(self, ui_window):
        session, idx = self._first_say(ui_window)
        before = len(session.beats)
        panel = ui_window._panel
        panel.set_text('前半后半')
        panel._split_btn.click()
        panel._split_cancel.click()

        assert len(session.beats) == before
        assert session.beats[idx].text != '前半{nw}'
        assert ui_window._loaded_idx == idx

    def test_窗口的撤销能把这句话收回去(self, ui_window):
        """点错了一键退回：撤销走结构撤销栈，**一步**回到"拆之前那一整句"。

        ⚠ 退回去的是**拆之前**那半句（也就是用户在台词框里刚改完的样子），
        不是源文件里的原句 —— "断开"只是这一下动作，不该连带把打字也撤掉。
        """
        session, idx = self._first_say(ui_window)
        before = len(session.beats)

        panel = ui_window._panel
        panel.set_text('前半后半')
        panel._split_btn.click()
        _put_cursor(panel, 2)
        panel._split_btn.click()
        assert len(session.beats) == before + 1

        ui_window.restore_card()
        assert len(session.beats) == before
        assert session.beats[idx].text == '前半后半'
        assert '{nw}' not in session.beats[idx].text
        assert all(getattr(b, 'kind') != 'extend' for b in session.beats)
