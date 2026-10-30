"""
storygraph.ui_qt.wheel_guard
============================
**滚轮永远不许改值**：页面滚动时鼠标扫过组合框 / 数值框 / 滑块，
值一个都不许变，滚轮只许"滚页面"。

两轮反馈叠出来的规则：

  · 2026-10-29（原话）：

    > 不要让滚轮上下滑动调整背景，音乐和语音这三个东西……他说太容易误触了！！！

    当时只护了卡片上「背景 / 音乐 / 语音」三格，规则还是"点进去（有焦点）
    才响应滚轮"。

  · 2026-10-30（原话）：

    > 滚动会导致选择切换！！！不要这个！

    说明"有焦点就放行"这条口子留着没用 —— 用户刚点过「说话人」，
    焦点还在它身上，滚页面时鼠标划过它，人就换了。而且实测（`python` +
    offscreen Qt）：**普通可编辑 `QComboBox` 连焦点都不需要**，滚轮直接改值，
    还把这一格滚轮**吃掉**（页面滚不动）—— 用户看到的就是"滚一下，选的人
    变了，页面还没动"。

所以现在的规则只有一条，没有例外：

  · **任何**组合框 / 数值框 / 滑块，滚轮都不改值；
  · 那一格滚轮**转交给外面的滚动区**（页面照常滚，不卡手）；
  · 唯一放行的是**已经弹开的候选列表**（`QComboBox.view()` 可见时）——
    那是用户正开着列表在里头上下翻，本来就该滚。

两层落实（互为保险）：

  · `WheelSafeComboBox`：子类化的组合框，卡片上「说话人 / 背景 / 音乐 / 语音」
    直接用它（哪怕没有全局守卫，单测里也是对的）；
  · `WheelValueGuard` + `ensure_installed()`：挂在 `QApplication` 上的事件过滤器，
    把**所有**窗口里漏网的裸 `QComboBox` / `QAbstractSpinBox` / `QSlider` /
    `QDial` 一起兜住（对话框、流程编辑、标签选择……）。

转交是显式的（`_scroll_area()` + `sendEvent`），不赌 Qt 的事件冒泡：
冒泡失败的话表现为"鼠标停在这一格上页面就滚不动"，那等于把误触换成了卡手。
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSpinBox, QApplication,
                               QComboBox, QDial, QLineEdit, QSlider)

from storygraph.core.safeguard import swallow

#: 滚轮**一律**不许改值的控件类型（`QScrollBar` 是 `QAbstractSlider` 的近亲，
#: 但它是"滚动本身"，绝对不能拦，所以这里逐个点名，不写基类）。
_VALUE_WIDGETS = (QComboBox, QAbstractSpinBox, QSlider, QDial)


def _scroll_area(widget):
    """往上找第一个 `QAbstractScrollArea`（卡片 / 对话框的滚动区）。"""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            return parent
        parent = parent.parentWidget()
    return None


def _popup_open(combo) -> bool:
    """候选列表是不是正弹开着（弹开时滚轮属于"翻列表"，要放行）。"""
    try:
        view = combo.view()
    except Exception as e:                    # noqa: BLE001 - 组合框/底层对象被销毁
        swallow('ui_qt.wheel_guard.popup_open', e)
        return False
    try:
        return bool(view is not None and view.isVisible())
    except Exception as e:                    # noqa: BLE001
        swallow('ui_qt.wheel_guard.popup_visible', e)
        return False


def forward_wheel(widget, event) -> bool:
    """
    把这一格滚轮转交给外层滚动区。

    :return: True = 转交成功（事件已被消费）；False = 没有可滚的滚动区
             （调用方应当 `event.ignore()`，反正**不许改值**）。
    """
    scroll = _scroll_area(widget)
    if scroll is None:
        return False
    try:
        bar = scroll.verticalScrollBar()
    except Exception as e:                    # noqa: BLE001
        swallow('ui_qt.wheel_guard.scroll_bar', e)
        return False
    if bar is None or bar.maximum() <= bar.minimum():
        return False
    # 直接转给滚动区**视口**：它自己按滚轮量滚，不需要换算像素。
    # （同一个事件对象可以再发一次；sendEvent 不持有它。）
    QCoreApplication.sendEvent(scroll.viewport(), event)
    event.accept()
    return True


class WheelSafeComboBox(QComboBox):
    """
    滚轮不改值的组合框（**没有**"点进去就放行"的例外）。

    用法与 `QComboBox` 完全一样（子类只多一条滚轮规则）；卡片里
    「说话人 / 背景 / 音乐 / 语音」四格用它，见 `BeatCardEditor`。
    """

    def wheelEvent(self, event):              # noqa: N802 - Qt 命名
        if _popup_open(self):
            super().wheelEvent(event)
            return
        if forward_wheel(self, event):
            return
        event.ignore()                        # 没滚动区可转：不换值即可


class WheelValueGuard(QObject):
    """
    挂在 `QApplication` 上的事件过滤器：兜住**所有**漏网的改值控件。

    为什么不做成"每个控件各自防"：全项目 60 多个 `QComboBox`（对话框、
    流程编辑、资产选择……），漏一个就是一次"我什么时候把它改了"。
    过滤器只认控件类型，新加的组合框自动被护住，不需要谁记得去改。
    """

    def _guarded(self, obj):
        """事件落在"该防的控件"上吗（含组合框 / 数值框内部那个行编辑）。"""
        if isinstance(obj, _VALUE_WIDGETS):
            return obj
        if isinstance(obj, QLineEdit):
            parent = obj.parentWidget()
            if isinstance(parent, (QComboBox, QAbstractSpinBox)):
                return parent
        return None

    def eventFilter(self, obj, event):        # noqa: N802 - Qt 命名
        if event.type() != QEvent.Type.Wheel:
            return False
        target = self._guarded(obj)
        if target is None:
            return False
        if isinstance(target, QComboBox) and _popup_open(target):
            return False                      # 弹开的候选列表：正常滚
        # 吃掉事件 → 控件永远看不到这次滚轮；需要滚的话转给外面。
        if not forward_wheel(target, event):
            event.ignore()
        return True


def ensure_installed(app=None):
    """
    保证当前 `QApplication` 上装着守卫（**幂等**：同一进程只装一个）。

    多个 `MainWindow`（测试里很常见）不会叠出多个过滤器；装好之后
    再次调用直接返回同一个实例。
    """
    app = app or QApplication.instance()
    if app is None:
        return None
    existing = getattr(app, '_storygraph_wheel_guard', None)
    if existing is not None:
        return existing
    guard = WheelValueGuard(app)
    app.installEventFilter(guard)
    # 挂在 app 上，防 GC；也防"又装一个"
    app._storygraph_wheel_guard = guard
    return guard


__all__ = ['WheelSafeComboBox', 'WheelValueGuard', 'ensure_installed',
           'forward_wheel']
