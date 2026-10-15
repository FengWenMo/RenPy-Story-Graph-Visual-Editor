"""
`storygraph.ui_qt.flow` —— **会折行的一行**（`FlowLayout` + 宿主控件 `FlowRow`）。
================================================================================

从 `ui_qt/param_cards.py` 抽出来单独立一个文件（2026-10-13 重写）：它现在是
**两个地方共用的基建**（字段编辑器里的候选方块、弹窗里的按钮行），
而旧文件已经被整个删掉。

## 为什么必须有它（用户实测报的）

> 前面那个选的一多呀……就把后面那个变量挤到窗口外去了！

`QHBoxLayout` 的 `minimumSize` 是**所有子控件宽度之和** —— 候选值方块一多，
这个"最小宽"就顺着 卡片 → 列表 → 弹窗 一路上传；窗口顶不到那么大时就只能裁。
折行布局的最小宽只有**最宽的那一个**，于是窗口想多窄都行。

⚠ 两条踩过的坑（都写在这里，别再踩）：
  · `QLayout.minimumSize()` **不是**"折行后的下限"：它取"最宽那一项的
    `sizeHint` 宽"。所以 `FlowRow` 的 `minimumSizeHint/sizeHint` 必须**自己算**
    （各子控件**下限**的最大值），否则 `[标题][输入框][按钮]` 那一行会报 240 px；
  · `QGroupBox` 的**标题不折行**，长标题会把整块顶宽（实测 466 px）——
    长说明要放进框内的 `QLabel`。
"""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """
    **会折行的横排布局**（Qt 官方 Flow Layout 的同款语义）。

    它是 `QLayout` 子类（不是把 `QLayoutItem` 拼起来），所以
    `card.body.getItemPosition(i)` 这类老判据拿到的是 `(0,0)` ——
    "同一格叠两个控件"在结构上就不可能出现。
    """

    def __init__(self, parent=None, margin: int = 0, spacing: int = 4):
        super().__init__(parent)
        self._items: list = []
        self._spacing = int(spacing)
        self.setContentsMargins(QMargins(margin, margin, margin, margin))

    # ---- QLayout 必须实现的四件 ----
    def addItem(self, item):                        # noqa: N802 - Qt 接口
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):                        # noqa: N802 - Qt 接口
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):                        # noqa: N802 - Qt 接口
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    # ---- 尺寸 ----
    def expandingDirections(self):                  # noqa: N802 - Qt 接口
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:            # noqa: N802 - Qt 接口
        return True

    def heightForWidth(self, width: int) -> int:    # noqa: N802 - Qt 接口
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):                    # noqa: N802 - Qt 接口
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:                    # noqa: N802 - Qt 接口
        return self.minimumSize()

    def minimumSize(self) -> QSize:                 # noqa: N802 - Qt 接口
        """**折行后**的最小尺寸：宽 = 最宽一格；高 = 一格（不是所有格之和）。"""
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def clear_widgets(self):
        """清空（`ValueChips` 用）。"""
        while self._items:
            item = self._items.pop()
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    # ---- 排版 ----
    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = eff.x(), eff.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            nx = x + hint.width()
            if nx > eff.right() + 1 and line_h > 0:
                x = eff.x()
                y += line_h + self._spacing
                nx = x + hint.width()
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = nx + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


class FlowRow(QWidget):
    """
    折行布局的**宿主控件**（`QLayout` 不是 `QWidget`，塞不进别的布局里）。

    ⚠ 它自己算 `minimumSizeHint` / `sizeHint` —— 见模块 docstring 的第一条坑。
    """

    def __init__(self, parent=None, spacing: int = 4):
        super().__init__(parent)
        self.flow = FlowLayout(self, margin=0, spacing=spacing)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

    def add(self, widget: QWidget) -> QWidget:
        self.flow.addWidget(widget)
        return widget

    def clear(self):
        self.flow.clear_widgets()
        self.updateGeometry()

    def _floor(self) -> QSize:
        w = h = 0
        for i in range(self.flow.count()):
            m = self.flow.itemAt(i).minimumSize()
            w = max(w, m.width())
            h = max(h, m.height())
        return QSize(w, h)

    def minimumSizeHint(self) -> QSize:             # noqa: N802 - Qt 接口
        return self._floor()

    def sizeHint(self) -> QSize:                    # noqa: N802 - Qt 接口
        # ⚠ 也返回下限：`sizeHint` 一大，父布局就按它分配宽度
        #   （实测：右栏被撑到 466 px，而视口只有 354 px → 横向滚动条）
        return self._floor()

    def heightForWidth(self, width: int) -> int:    # noqa: N802 - Qt 接口
        return self.flow.heightForWidth(max(1, int(width)))

    def hasHeightForWidth(self) -> bool:            # noqa: N802 - Qt 接口
        return True


__all__ = ['FlowLayout', 'FlowRow']
