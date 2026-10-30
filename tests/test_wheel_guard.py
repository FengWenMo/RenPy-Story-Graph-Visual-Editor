"""
tests/test_wheel_guard.py
=========================
**滚轮永远不许改值**（两轮反馈叠出来的规则）。

2026-10-29：

> 不要让滚轮上下滑动调整背景，音乐和语音这三个东西……他说太容易误触了！！！

2026-10-30：

> 滚动会导致选择切换！！！不要这个！

第二轮说明"有焦点就放行"这条口子不能留 —— 用户刚点过「说话人」，焦点还在它
身上，滚页面时鼠标划过它，人就换了。实测（offscreen Qt）更糟：**普通可编辑
`QComboBox` 连焦点都不需要**，滚轮直接改值，还把这一格滚轮吃掉（页面不滚）。

所以这里钉死：

  1) 滚轮**绝不改值**（有没有焦点都一样）；
  2) 那一格滚轮要转交给外面那个滚动区（页面照常滚，不许卡手）；
  3) 唯一放行的是"候选列表已经弹开"（那时滚的是列表本身）；
  4) 兜底是 `QApplication` 上的全局守卫 —— 裸 `QComboBox` 也跑不掉。
"""

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (QApplication, QComboBox, QScrollArea, QVBoxLayout,
                               QWidget)

from storygraph.ui_qt import wheel_guard as WG
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


def _scrolling_panel(combo):
    """一个可滚动的面板，里面放一格组合框（复刻卡片的结构）。"""
    area = QScrollArea()
    area.setWidgetResizable(True)
    body = QWidget()
    area.setWidget(body)
    lay = QVBoxLayout(body)
    lay.addWidget(combo)
    # 撑高内容：滚动条必须真的可滚，否则"转交给滚动区"判不出来
    lay.addWidget(QWidget())
    body.setMinimumHeight(2400)
    area.resize(320, 160)
    area.show()
    QApplication.processEvents()
    assert area.verticalScrollBar().maximum() > 0, '滚动条没撑开，测不出来'
    return area, combo


@pytest.fixture
def scroll_with_combo(qapp):
    combo = WheelSafeComboBox()
    combo.setEditable(True)
    combo.addItems(VALUES)
    return _scrolling_panel(combo)


@pytest.fixture
def scroll_with_plain_combo(qapp):
    """**裸** `QComboBox`（用户卡片上「说话人」原来的样子）。"""
    combo = QComboBox()
    combo.setEditable(True)
    combo.addItems(VALUES)
    return _scrolling_panel(combo)


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


def test_a_focused_combo_still_must_not_change(scroll_with_combo, qapp):
    """
    ③ **点进去之后也不许换值**（2026-10-30 用户："滚动会导致选择切换"）。

    老规则"有焦点就放行"正是这次复发的原因：刚点过说话人 → 焦点还在 →
    滚页面时划过它就换了人。
    """
    area, combo = scroll_with_combo
    combo.setFocus(Qt.FocusReason.OtherFocusReason)
    qapp.processEvents()
    combo.setCurrentIndex(1)
    before = combo.currentIndex()
    bar = area.verticalScrollBar()
    start = bar.value()
    _send(combo, _wheel_event(combo))
    assert combo.currentIndex() == before, \
        f'滚轮把点过的字段改了：{before} → {combo.currentIndex()}'
    assert bar.value() > start, '有焦点的那格把滚轮吃掉，页面滚不动'


def test_the_card_uses_the_guard_for_every_value_field(qapp):
    """卡片上「说话人 / 背景 / 音乐 / 语音」四格都必须是防护过的组合框。"""
    from storygraph.ui_qt.main_window import BeatCardEditor

    panel = BeatCardEditor()
    try:
        for name in ('Speaker', 'Scene', 'BGM', 'Voice'):
            w = panel._widgets[name]
            assert isinstance(w, WheelSafeComboBox), (
                f'{name} 那格还是裸 QComboBox —— 滚轮误触会复发')
    finally:
        panel.setParent(None)
        panel.deleteLater()


# ---------------------------------------------------------------------------
#  兜底：全局守卫（漏网的裸 QComboBox 也跑不掉）
# ---------------------------------------------------------------------------

def test_a_plain_combo_is_guarded_by_the_app_filter(scroll_with_plain_combo,
                                                    qapp):
    """
    **裸** `QComboBox`（谁忘了换子类）也不许被滚轮改值 —— 而且页面要滚。

    这条是"说话人那格"的直接判据：它是可编辑组合框，实测**无焦点**时
    滚轮就会换人，还吃掉滚动。
    """
    area, combo = scroll_with_plain_combo
    guard = WG.ensure_installed(qapp)
    assert guard is not None
    combo.clearFocus()
    qapp.processEvents()
    combo.setCurrentIndex(1)
    before = combo.currentIndex()
    bar = area.verticalScrollBar()
    start = bar.value()
    _send(combo, _wheel_event(combo))
    assert combo.currentIndex() == before, \
        f'裸组合框被滚轮改了值：{before} → {combo.currentIndex()}'
    assert bar.value() > start, '裸组合框把滚轮吃掉，页面滚不动'


def test_the_guard_is_installed_only_once(qapp):
    """幂等：多个窗口（测试里很常见）不会叠出多个过滤器。"""
    first = WG.ensure_installed(qapp)
    second = WG.ensure_installed(qapp)
    assert first is second
    assert getattr(qapp, '_storygraph_wheel_guard', None) is first


def test_the_open_popup_is_left_alone(qapp, monkeypatch):
    """
    候选列表弹开时滚的是列表本身 —— 那种滚轮要放行（否则用户没法翻候选人）。
    """
    combo = QComboBox()
    combo.addItems(VALUES)
    guard = WG.ensure_installed(qapp)
    monkeypatch.setattr(WG, '_popup_open', lambda c: True)
    ev = _wheel_event(combo)
    assert guard.eventFilter(combo, ev) is False, \
        '候选列表开着时滚轮被拦了（翻不了列表）'
    combo.setParent(None)
    combo.deleteLater()
