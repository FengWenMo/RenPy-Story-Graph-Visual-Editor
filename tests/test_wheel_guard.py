"""
tests/test_wheel_guard.py
=========================
**滚轮防误触**（用户 2026-10-29）：

> 不要让滚轮上下滑动调整背景，音乐和语音这三个东西……他说太容易误触了！！！

卡片右栏是放在滚动区里的，而「背景 / 音乐 / 语音」三格是可编辑组合框 ——
滚页面时鼠标从字段上经过，值就被悄悄地换掉。这里钉三条：

  1) **没点进去**（无焦点）→ 滚轮**不许改值**；
  2) 同一格滚轮也不能"白吃"：要转交给外面那个滚动区（页面照常滚）；
  3) **点进去之后**（有焦点）→ 滚轮照常换值（那是有意操作）。
"""

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from storygraph.ui_qt.wheel_guard import WheelSafeComboBox

VALUES = ['bg 教室', 'bg 走廊', 'bg 天台']


def _wheel_event(widget, degrees: int = -120) -> QWheelEvent:
    """
    在控件中心发一次竖直滚轮（一档 = 120°）。

    **默认往下滚**（-120）：滚动区在上边界时再往上滚是"滚不动"的，
    判据会分不清"被吃掉了"和"本来就到头了"。
    """
    pos = QPointF(widget.width() / 2.0, widget.height() / 2.0)
    return QWheelEvent(pos, widget.mapToGlobal(pos), QPoint(0, 0),
                       QPoint(0, degrees), Qt.MouseButton.NoButton,
                       Qt.KeyboardModifier.NoModifier,
                       Qt.ScrollPhase.NoScrollPhase, False)


def _send(widget, event):
    QApplication.sendEvent(widget, event)


@pytest.fixture
def scroll_with_combo(qapp):
    """一个可滚动的面板，里面放一格`WheelSafeComboBox`（复刻卡片的结构）。"""
    area = QScrollArea()
    area.setWidgetResizable(True)
    body = QWidget()
    area.setWidget(body)
    lay = QVBoxLayout(body)
    combo = WheelSafeComboBox()
    combo.setEditable(True)
    combo.addItems(VALUES)
    lay.addWidget(combo)
    # 撑高内容：滚动条必须真的可滚，否则"转交给滚动区"判不出来
    lay.addWidget(QWidget())
    body.setMinimumHeight(2400)
    area.resize(320, 160)
    area.show()
    qapp.processEvents()
    assert area.verticalScrollBar().maximum() > 0, '滚动条没撑开，测不出来'
    return area, combo


def test_wheel_does_not_change_an_unfocused_combo(scroll_with_combo, qapp):
    """① 没点进去的字段，滚轮扫过去**不许改值**。"""
    area, combo = scroll_with_combo
    combo.clearFocus()
    qapp.processEvents()
    before = combo.currentText()
    _send(combo, _wheel_event(combo))
    assert combo.currentText() == before, \
        f'滚轮把没点进去的字段改了：{before!r} → {combo.currentText()!r}'


def test_unfocused_wheel_is_forwarded_to_the_scroll_area(scroll_with_combo,
                                                         qapp):
    """② 滚轮不能被"白吃"：页面照常滚（否则等于把误触换成卡手）。"""
    area, combo = scroll_with_combo
    combo.clearFocus()
    qapp.processEvents()
    bar = area.verticalScrollBar()
    start = bar.value()
    _send(combo, _wheel_event(combo))
    assert bar.value() > start, '滚轮在字段上被吃掉了，页面滚不动'


def test_wheel_changes_a_focused_combo(scroll_with_combo, qapp):
    """③ 真的点进去（有焦点）之后，滚轮照常换值 —— 那是有意操作。"""
    area, combo = scroll_with_combo
    combo.setFocus(Qt.FocusReason.OtherFocusReason)
    qapp.processEvents()
    # 从中间那档开始：在下边界继续往上滚是"没得换"，判不出实现对不对
    combo.setCurrentIndex(1)
    before = combo.currentIndex()
    _send(combo, _wheel_event(combo))
    assert combo.currentIndex() != before, \
        '点进去之后滚轮反而不管用了（那是有意操作，应当换值）'


def test_the_card_uses_the_guard_for_scene_music_voice(qapp):
    """卡片上「背景 / 音乐 / 语音」三格必须用这个组合框（不是裸 `QComboBox`）。"""
    from storygraph.ui_qt.main_window import BeatCardEditor

    panel = BeatCardEditor()
    try:
        for name in ('Scene', 'BGM', 'Voice'):
            w = panel._widgets[name]
            assert isinstance(w, WheelSafeComboBox), (
                f'{name} 那格还是裸 QComboBox —— 滚轮误触会复发')
    finally:
        panel.setParent(None)
        panel.deleteLater()
