"""
storygraph.ui_qt.wheel_guard
============================
**滚轮防误触**：可编辑组合框没被「点进去」时，不许被滚轮悄悄换值。

用户 2026-10-29（原话）：

> 不要让滚轮上下滑动调整背景，音乐和语音这三个东西……他说太容易误触了！！！

为什么不靠 Qt 默认行为：这三个格是**可编辑** `QComboBox`（背景图名 / bgm id /
语音名），卡片又放在滚动区里。用户滚页面时鼠标正从字段上经过，滚轮一转值就
被换掉 —— 而且这类字段是自由文本，改错了在画面上不一定立刻看得出来，事后
也很难想起"我什么时候改的它"。所以这里把规则钉死：

  · **点进去（焦点在组合框或它的输入行编辑上）** → 滚轮照常换值（有意操作）；
  · 否则 → 不换值，并把这一格滚轮**转交给父级滚动区** —— 滚页面照常滚。

转交是显式的（`_scroll_area()` + `sendEvent`），不赌 Qt 的事件冒泡：
冒泡失败的话表现为"鼠标停在这一格上页面就滚不动"，那等于把误触换成了卡手。
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QAbstractScrollArea, QComboBox

from storygraph.core.safeguard import swallow


class WheelSafeComboBox(QComboBox):
    """
    只有**已经点进去**的组合框才响应滚轮。

    用法与 `QComboBox` 完全一样（子类只多一条滚轮规则）；卡片里
    「背景 / 音乐 / 语音」三格用它，见 `main_window.BeatCard._asset_row`。
    """

    #: 父级滚动区里找不到可滚的（内容没超出）时，也别吃掉事件 ——
    #: 交回给 Qt 的默认处理（它会继续冒泡）。
    def _focused(self) -> bool:
        """焦点在这格身上吗（含可编辑组合框内部那个行编辑）。"""
        if self.hasFocus():
            return True
        try:
            edit = self.lineEdit()
        except Exception as e:                # noqa: BLE001 - 组合框/底层对象被销毁
            swallow('ui_qt.wheel_guard.focused', e)
            edit = None
        return bool(edit is not None and edit.hasFocus())

    @staticmethod
    def _scroll_area(widget):
        """往上找第一个 `QAbstractScrollArea`（卡片的滚动区）。"""
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def wheelEvent(self, event):              # noqa: N802 - Qt 命名
        if self._focused():
            super().wheelEvent(event)
            return
        scroll = self._scroll_area(self)
        if scroll is not None:
            bar = scroll.verticalScrollBar()
            if bar is not None and bar.maximum() > bar.minimum():
                # 直接转给滚动区**视口**：它自己按滚轮量滚，不需要换算像素。
                # （同一个事件对象可以再发一次；sendEvent 不持有它。）
                QCoreApplication.sendEvent(scroll.viewport(), event)
                event.accept()
                return
        event.ignore()                        # 没滚动区可转：不换值即可


__all__ = ['WheelSafeComboBox']
