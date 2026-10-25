# -*- coding: utf-8 -*-
"""
带行号栏的多行编辑器 —— **IDE 那种左边一列数字**。

用户 2026-10-25：

    > 我是希望它能够精确到行的……就跟 IDE 那种一样，左边写个数字……
    > 不然的话我都不知道它到底在哪儿？？？想改也没法改呀……

为什么单独做一个控件（而不是给 `QPlainTextEdit` 打补丁）：

  · 行号栏必须是**同一个视口坐标系**里的兄弟控件（跟着垂直滚动走），
    用 `setViewportMargins` 把正文推开 —— 这是 Qt 的官方做法；
  · 行号要能显示**源文件里的真实行号**（`first_line`）：这一拍的
    `CodeBlock` 是从 `.rpy` 第 N 行起的，用户拿这个数字就能回编辑器里定位。
    从前卡片上只有一个 2 行的输入框，用户根本不知道自己看的是文件哪一段。

关掉自动换行（`NoWrap`）是有意的：**换行会让"一行文字"变成"三行显示"**，
行号就会和逻辑行错开 —— 那种"看着对、其实错"的数字比没有更糟。
代码块本来就该横向滚动。

⚠ 继承 `QPlainTextEdit` 是**接口契约**：`main_window._set_value` /
`_get_value` / `_is_text_widget` 全按 `isinstance(w, QPlainTextEdit)` 分派，
子类天然兼容 —— 换成别的基类会一夜之间把这三个地方全打哑（那种"界面没反应"
的 bug 在本项目出现过）。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontDatabase, QPainter
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T


class _LineGutter(QWidget):
    """行号栏本体：只负责"占位 + 把绘制交给编辑器"。"""

    def __init__(self, editor: 'CodeEditor'):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.gutter_width(), 0)

    def paintEvent(self, event):            # noqa: N802 - Qt 命名
        self._editor.paint_gutter(event)


class CodeEditor(QPlainTextEdit):
    """多行代码编辑器：左边一列行号，其余与 `QPlainTextEdit` 一致。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        #: 源文件首行号（`None` → 只显示 1/2/3…）
        self._first_line = None
        #: 源文件末行号（只用于 tooltip，不参与绘制）
        self._last_line = None
        self._gutter = _LineGutter(self)
        # 不换行：见模块 docstring（换行会让行号与逻辑行错开）
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        try:
            self.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        except Exception as e:                # noqa: BLE001 - 拿不到等宽字体也不致命
            swallow('ui_qt.code_edit.mono_font', e)
        self.blockCountChanged.connect(self._on_block_count_changed)   # noqa: SLF001
        self.updateRequest.connect(self._on_update_request)            # noqa: SLF001
        self._sync_gutter_width()

    # ---------- 对外 ----------

    def set_source_lines(self, first=None, last=None):
        """
        告诉它"这段代码在源文件里的行号范围"。

        `first` 空（新建的卡片 / 认不出源行）→ 退回 1/2/3…
        给了就把行号显示成**源文件里真实的那些行号**（用户要的就是这个）。
        """
        self._first_line = _as_int(first)
        self._last_line = _as_int(last)
        if self._first_line and self._last_line:
            self.setToolTip(f'这一拍的代码在源文件的第 {self._first_line}–'
                            f'{self._last_line} 行')
        elif self._first_line:
            self.setToolTip(f'这一拍的代码从源文件第 {self._first_line} 行开始')
        else:
            self.setToolTip('')
        self._sync_gutter_width()
        self._gutter.update()
        self.viewport().update()

    def source_lines(self):
        return self._first_line, self._last_line

    def shown_number(self, block_number: int) -> int:
        """
        第 `block_number`（0 起）显示成几？

        有源行 → 源文件里的真实行号（`first + 序号`）；
        没有 → 1/2/3…（新建的卡片、认不出源行时）。

        单独抽成一个方法是为了**可测**：绘制里那行数字就调它，
        判据不用去数像素（数像素的判据一改配色就红）。
        """
        n = int(block_number)
        return (self._first_line + n) if self._first_line else (n + 1)

    def gutter_width(self) -> int:
        """行号栏宽度：按"最长那个数字"算，翻页/增行时跟着变。"""
        lines = self.blockCount()
        start = self._first_line or 1
        digits = max(len(str(start + max(lines, 0))), 2)
        return 10 + self.fontMetrics().horizontalAdvance('9') * digits

    # ---------- 绘制 ----------

    def paint_gutter(self, event) -> None:
        painter = QPainter(self._gutter)
        try:
            painter.fillRect(event.rect(),
                             QColor(T.t('bg.surface')))
            # 右侧一条细线，把行号和正文分开（IDE 都这么做）
            painter.setPen(QColor(T.t('border.default')))
            painter.drawLine(self._gutter.width() - 1, event.rect().top(),
                             self._gutter.width() - 1, event.rect().bottom())

            block = self.firstVisibleBlock()
            number = block.blockNumber()
            top = int(self.blockBoundingGeometry(block)
                      .translated(self.contentOffset()).top())
            bottom = top + int(self.blockBoundingRect(block).height())
            width = self._gutter.width() - 8
            height = self.fontMetrics().height()
            current = self.textCursor().blockNumber()
            while block.isValid() and top <= event.rect().bottom():
                if block.isVisible() and bottom >= event.rect().top():
                    shown = self.shown_number(number)
                    # 当前行亮一点：眼在哪儿一目了然
                    painter.setPen(QColor(T.t('brand.primary')
                                          if number == current
                                          else T.t('text.tertiary')))
                    painter.drawText(0, top, width, height,
                                     int(Qt.AlignRight | Qt.AlignTop),
                                     str(shown))
                block = block.next()
                top = bottom
                bottom = top + int(self.blockBoundingRect(block).height())
                number += 1
        finally:
            painter.end()

    # ---------- Qt 内部 ----------

    def _on_block_count_changed(self, _count: int) -> None:
        self._sync_gutter_width()

    def _on_update_request(self, rect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(),
                                rect.height())
        if rect.contains(self.viewport().rect()):
            self._sync_gutter_width()

    def resizeEvent(self, event):           # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        area = self.contentsRect()
        self._gutter.setGeometry(QRect(area.left(), area.top(),
                                       self.gutter_width(), area.height()))

    def _sync_gutter_width(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)


def _as_int(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


__all__ = ['CodeEditor']
