"""
storygraph.ui_qt.status
=======================
**状态栏 + Toast**：后端通知的唯一显示落点。

设计意图（对齐用户诉求："操作说明写成按钮等弹出"）
--------------------------------------------------
主界面**不再常驻**说明文字与提示文本。信息按"要紧程度"分两层：

  · `StatusBar` —— 常驻一行：当前文件 / 拍号 / 保存状态。**不闪、不占位**；
  · `Toast`     —— 短暂的浮层：后端报告的一件事（"已导出"、"缺 3 个资源"）。
                    3 秒淡出，不打断操作，不占布局。

后端的 `storygraph.app.notify` 只管发消息；这里订阅它并显示。
换前端（tkinter / CLI）时只换订阅者，**后端一行都不用改**。
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QStatusBar)

from storygraph.ui import tokens as T

#: 各级别的停留时长（毫秒）。错误留久一点，让人来得及读。
_DURATION = {'info': 3000, 'warn': 5000, 'error': 8000}

#: 级别 → 前景色 token（走 tokens，不写死色值）
_LEVEL_COLOR = {
    'info': 'state.info',
    'warn': 'state.warning',
    'error': 'state.error',
}

#: 级别 → 前缀图标（不用图片资源，字体符号即可）
_LEVEL_ICON = {'info': '✔', 'warn': '⚠', 'error': '✕'}


class Toast(QFrame):
    """
    一行浮层提示。挂在父控件顶部居中，超时自动隐藏。

    做成"浮层"而不是"插进布局的一行"，是为了**不推动任何控件**：
    提示出现/消失时，编辑区一个像素都不动（不打断正在输入的人）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty('role', 'toast')
        self.setVisible(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(T.SPACE['sm'], T.SPACE['xs'],
                               T.SPACE['sm'], T.SPACE['xs'])
        lay.setSpacing(T.SPACE['xs'])
        self._icon = QLabel()
        self._text = QLabel()
        self._text.setWordWrap(False)
        lay.addWidget(self._icon, 0)
        lay.addWidget(self._text, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, level: str, message: str, duration: int = None):
        level = level if level in _DURATION else 'info'
        self._icon.setText(_LEVEL_ICON.get(level, 'ℹ'))
        color = T.t(_LEVEL_COLOR.get(level, 'text.secondary'))
        self._icon.setStyleSheet(f'color: {color}; font-weight: bold;')
        self._text.setText(str(message or '').replace('\n', '  ·  '))
        self._text.setStyleSheet(
            f"color: {T.t('toast.fg')}; background: transparent;")
        self.setStyleSheet(
            f"QFrame[role=\"toast\"] {{"
            f" background: {T.t('toast.bg')};"
            f" border-radius: {T.RADIUS['card']}px; }}")
        self.adjustSize()
        self._reposition()
        self.setVisible(True)
        self.raise_()
        self._timer.start(int(duration or _DURATION[level]))

    def _reposition(self):
        """浮在父控件顶部居中（父控件 resize 时由父控件再调一次）。"""
        parent = self.parentWidget()
        if parent is None:
            return
        w = min(self.sizeHint().width(), max(120, parent.width() - 40))
        self.setFixedWidth(w)
        self.adjustSize()
        self.move(max(0, (parent.width() - self.width()) // 2),
                  T.SPACE['md'])


class AppStatusBar(QStatusBar):
    """常驻状态栏：左侧一条消息，右侧"保存状态"小标签。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dirty = QLabel('')
        self._dirty.setProperty('role', 'caption')
        self.addPermanentWidget(self._dirty)

    def set_dirty(self, dirty: bool, path: str = ''):
        """
        右下角的落盘状态。

        这是用户最需要一眼看到的事实：**改动还在内存里**。
        （"改了但没导出"曾经是真实的丢稿原因。）
        """
        if dirty:
            self._dirty.setText('● 未导出')
            self._dirty.setStyleSheet(f"color: {T.t('state.warning')};")
            self._dirty.setToolTip('改动只在内存里，点「⤓ 导出 .rpy」才写文件'
                                   + (f'\n{path}' if path else ''))
        else:
            self._dirty.setText('✔ 已同步')
            self._dirty.setStyleSheet(f"color: {T.t('state.success')};")
            self._dirty.setToolTip(path or '与磁盘一致')


class Notifier:
    """
    把 `app.notify` 的消息接到状态栏 + Toast 上。

    用法：
        n = Notifier(status_bar, toast)
        n.subscribe()          # 开始接收后端通知
        n.close()              # 退订（窗口关闭时）
    """

    def __init__(self, status_bar, toast, on_message=None):
        self.status_bar = status_bar
        self.toast = toast
        self._on_message = on_message
        self._unsubscribe = None

    def subscribe(self):
        from storygraph.app import notify
        if self._unsubscribe is None:
            self._unsubscribe = notify.subscribe(self._receive)
        return self

    def _receive(self, level: str, message: str, title: str = ''):
        if self.status_bar is not None:
            try:
                self.status_bar.showMessage(str(message).replace('\n', '  '),
                                            8000)
            except Exception:                # noqa: BLE001
                pass
        if self.toast is not None:
            try:
                self.toast.show_message(level, message)
            except Exception:                # noqa: BLE001
                pass
        if self._on_message is not None:
            try:
                self._on_message(level, message, title)
            except Exception:                # noqa: BLE001
                pass

    def close(self):
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            except Exception:                # noqa: BLE001
                pass
            self._unsubscribe = None
