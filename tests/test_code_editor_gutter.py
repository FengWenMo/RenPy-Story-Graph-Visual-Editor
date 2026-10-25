# -*- coding: utf-8 -*-
"""
卡片「代码块」的行号栏（用户 2026-10-25）。

    > 我是希望它能够精确到行的……就跟 IDE 那种一样，左边写个数字……
    > 不然的话我都不知道它到底在哪儿？？？想改也没法改呀……

判据（都不数像素 —— 数像素的判据一改配色就红）：

  · 行号栏真的占到左边（`viewportMargins().left() > 0`）；
  · 有源行 → 显示**源文件里的真实行号**；没有 → 1/2/3…；
  · 位数变多时栏宽跟着变（不然 999→1000 会把数字截掉）；
  · 它仍然是 `QPlainTextEdit`（`_set_value` / `_get_value` /
    `_is_text_widget` 全靠这个 isinstance 分派）；
  · `load_fields(..., source_lines=…)` 会把行号喂进去。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip('PySide6')
from PySide6.QtWidgets import QPlainTextEdit                # noqa: E402

from storygraph.ui_qt.code_edit import CodeEditor           # noqa: E402


@pytest.fixture
def editor(qapp):
    ed = CodeEditor()
    ed.setPlainText('msg.reset()\nmsg.add("SP")\nmsg.add("SFX: click")')
    return ed


class TestGutter:
    def test_it_is_still_a_plain_text_edit(self, editor):
        # 主窗口按 isinstance(QPlainTextEdit) 分派取值/设值/空格让位
        assert isinstance(editor, QPlainTextEdit)

    def test_gutter_takes_space_on_the_left(self, editor):
        assert editor.viewportMargins().left() > 0

    def test_numbers_are_one_based_without_a_source_range(self, editor):
        assert [editor.shown_number(i) for i in range(3)] == [1, 2, 3]

    def test_numbers_are_real_source_lines_when_known(self, editor):
        editor.set_source_lines(42, 49)
        assert [editor.shown_number(i) for i in range(3)] == [42, 43, 44]
        assert editor.source_lines() == (42, 49)
        assert '42' in editor.toolTip() and '49' in editor.toolTip()

    def test_zero_or_garbage_falls_back_to_counting(self, editor):
        editor.set_source_lines(0, 0)
        assert editor.shown_number(0) == 1
        editor.set_source_lines('', None)
        assert editor.shown_number(0) == 1

    def test_width_grows_with_digits(self, editor):
        editor.set_source_lines(1, 5)
        narrow = editor.gutter_width()
        editor.set_source_lines(9990, 9999)
        assert editor.gutter_width() > narrow, '四位数会顶破两字宽的栏'

    def test_repaint_does_not_explode(self, editor):
        # 真的走一遍 paint（隐式验证 QPainter 的成对 begin/end）
        editor.set_source_lines(7, 9)
        pix = editor.grab()
        assert pix.width() > 0 and pix.height() > 0

    def test_no_wrap_keeps_numbers_aligned(self, editor):
        # 换行会让"一行文字"变"三行显示" → 行号与逻辑行错开
        assert editor.lineWrapMode() == QPlainTextEdit.NoWrap


class TestWiredIntoTheCard:
    def test_load_fields_passes_the_source_range(self, qapp):
        """面板真的把这一拍的 `src_first/src_last` 喂给了行号栏。"""
        from storygraph.ui_qt.main_window import BeatCardEditor
        # 只要类上存在这个参数即可（构造整个主窗口要几百毫秒，这里不划算）
        import inspect
        sig = inspect.signature(BeatCardEditor.load_fields)
        assert 'source_lines' in sig.parameters
        assert sig.parameters['source_lines'].default is None


class TestCardPassesRealLines:
    """真把面板建起来：CodeBlock 是行号编辑器，且拿到这一拍的源行。"""

    FIELDS = {
        'Speaker': 'jc', 'Text': '台词', 'Comment': '',
        'Stage': '', 'SFX': '', 'BGM': '', 'BG': '', 'Show': '', 'Hide': '',
        'Label': 'chapter1_4', 'Kind': 'narration',
        'CodeBlock': 'msg.reset()\nmsg.add("SP")',
        'SayTail': '', 'Condition': '',
    }

    def test_codeblock_shows_source_lines(self, qapp):
        from storygraph.ui_qt.main_window import BeatCardEditor
        panel = BeatCardEditor()
        try:
            panel.load_fields(dict(self.FIELDS), [], source_lines=(128, 133))
            widget = panel._widgets.get('CodeBlock')
            assert isinstance(widget, CodeEditor), type(widget)
            assert widget.source_lines() == (128, 133)
            assert widget.shown_number(0) == 128
            # 取值/设值这条路必须照旧（主窗口按 isinstance 分派）
            assert 'msg.reset()' in panel._get_value('CodeBlock')
        finally:
            panel.deleteLater()

    def test_new_card_without_source_falls_back_to_counting(self, qapp):
        from storygraph.ui_qt.main_window import BeatCardEditor
        panel = BeatCardEditor()
        try:
            panel.load_fields(dict(self.FIELDS), [])
            widget = panel._widgets['CodeBlock']
            assert widget.shown_number(0) == 1
        finally:
            panel.deleteLater()
