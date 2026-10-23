"""
`storygraph.ui_qt.inspector` —— 右栏顶上那块「属性」小表（参考界面学的）。
==========================================================================

参考界面的右栏是"检查器"：`节点 / 片段 / 预览范围 / 定位状态` 一行一个，
标签窄、值可选中复制。我们原来的右栏一上来就是卡片表单，**"我现在到底在
哪一拍、这一拍的源在文件第几行"没有任何地方显示** —— 用户想回源文件对照
时只能去数拍号。

这里只做排版（数据由窗口从 `EditorSession` 取好递进来）：

    set_rows([('文件', 'ch01.rpy'), ('label', '序章'), …])
    set_empty('还没载入章节')

值的两条规矩
------------
· **可选中、可复制**（`TextSelectableByMouse`）：`.rpy` 第 217 行这种信息
  唯一的用途就是拿去搜/贴给同事，不能选等于没用。
· **长值省略号 + tooltip 全文**：文件路径比这一栏宽得多，硬折行会把 6 行
  属性撑成 12 行 —— 省略号保留"看得出是哪份文件"的那一截，全文在悬停里。
  省略发生在**设置文本与尺寸变化时**（不在 `paintEvent` 里），
  这样选中、复制、无障碍那套 QLabel 的本职工作一件都不用重写。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from storygraph.ui_qt import tokens as T

#: 属性表一行的高度（比 QLabel 的 14px 松一点；7 行也不至于占掉半个右栏）。
ROW_HEIGHT = 20


class _ValueLabel(QLabel):
    """单行、超宽省略（中略）、可选中、tooltip 是全文。"""

    def __init__(self, text: str = '', parent=None):
        super().__init__('', parent)
        self.setProperty('role', 'note')
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setMinimumWidth(0)
        self._full = ''
        self.setText(str(text))

    # -- Qt 接口 --
    def setText(self, text: str):             # noqa: N802 - Qt 命名
        self._full = str(text)
        self.setToolTip(self._full)
        self._elide()

    def resizeEvent(self, event):             # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._elide()

    # -- 自己用 --
    def full_text(self) -> str:
        return self._full

    def _elide(self):
        metrics = QFontMetrics(self.font())
        width = max(0, self.width() - 2)
        # 宽度还没定下来（刚建、还没布局）时不要先塞一个 `…` —— 让上级
        # 先看到全文，等第一次 resize 再省略（否则测试里读到的全是"…"）。
        if width <= 8:
            return
        super().setText(metrics.elidedText(self._full, Qt.ElideMiddle, width))


class PropertyStrip(QWidget):
    """`标签 : 值` 的小表；行由 `set_rows` 整体换掉（不逐行增删）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(T.SPACE['xs'])
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(T.SPACE['sm'])
        # 行距别再压到 2px：参考界面的检查器一行一行**读得开**，
        # 而 14px 行高 + 2px 行距在 1080p 上像一坨小字（2026-10-23 截图）。
        self._grid.setVerticalSpacing(T.SPACE['xs'])
        self._grid.setColumnStretch(1, 1)
        outer.addLayout(self._grid)
        self._empty = QLabel('还没载入章节')
        self._empty.setProperty('role', 'caption')
        outer.addWidget(self._empty)
        self._rows: list = []
        self._widgets: list = []

    # ------------------------------------------------------------------
    def set_empty(self, text: str = '还没载入章节'):
        self._clear()
        self._empty.setText(str(text or '—'))
        self._empty.setVisible(True)

    def set_rows(self, rows):
        """
        `[(标签, 值), …]` —— 空值那一行**不显示**（写 `—` 是噪音，
        它和"这一拍没有说话人"看起来一样，而后者才是有信息的）。
        """
        self._clear()
        row = 0
        for spec in (rows or ()):
            try:
                label, value = str(spec[0]), str(spec[1])
            except (TypeError, IndexError):
                continue
            if not value.strip() or value.strip() == '—':
                continue
            cap = QLabel(label)
            cap.setProperty('role', 'fieldLabel')
            cap.setFixedWidth(T.LABEL_W)
            cap.setMinimumHeight(ROW_HEIGHT)
            val = _ValueLabel(value)
            val.setMinimumHeight(ROW_HEIGHT)
            self._grid.addWidget(cap, row, 0, Qt.AlignTop)
            self._grid.addWidget(val, row, 1)
            self._rows.append((label, val))
            self._widgets.extend((cap, val))
            row += 1
        self._empty.setVisible(not self._rows)
        self._empty.setText('这一拍没有可显示的属性')

    # ------------------------------------------------------------------
    def texts(self) -> dict:
        """`{标签: 值}`（测试与"复制诊断"用；不必伸手进 widget）。"""
        return {k: v.full_text() for k, v in self._rows}

    def row_count(self) -> int:
        return len(self._rows)

    def _clear(self):
        for w in self._widgets:
            w.setParent(None)
            w.deleteLater()
        self._widgets = []
        self._rows = []


__all__ = ['PropertyStrip', 'ROW_HEIGHT']
