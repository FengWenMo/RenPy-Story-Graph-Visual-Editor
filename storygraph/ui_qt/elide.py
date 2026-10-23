"""
`storygraph.ui_qt.elide` —— 一行放不下就省略的 `QLabel`（`…`），tooltip 给全文。
================================================================================

为什么单列一个模块（2026-10-23，用户报"整个界面太宽、文字跑到界面外"）
-----------------------------------------------------------------------
同一件事（单行 + 超宽省略 + tooltip 全文）在三个地方都要用：

  · 右栏属性表的**值**（`inspector._ValueLabel`，现成实现，中略）；
  · 卡片页面包屑右边那句"第 i / n 拍（产物 N 行）"（右略）；
  · 顶部模式条右边"文件 · label · 第 i 拍"（右略）。

三份各写一遍 `resizeEvent + QFontMetrics.elidedText`，迟早在其中一份漏掉
"宽度还没算完时先显示全文"那一句 —— 表现是首帧/测试里读到一串 `…`，
用户看到闪一下。所以口径只留一份。

⚠ **`QSizePolicy.Ignored` 是这儿的要害**：普通 `QLabel` 的最小宽度等于整段
文字的宽度，它会**顶着布局不让缩**。2026-10-23 实测：1400 宽时面包屑那一行
最小 1279px、而可用只有 1106px —— 最后那个「🗑 删除这张卡片」就被裁掉了。
换成 `Ignored` 之后它可以被压到 0 宽（文字画成 `…`），布局才真的能缩。
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel, QSizePolicy


class ElidedLabel(QLabel):
    """单行省略标签；`text()` 返回**省略后**的显示文本，`full_text()` 是全文。"""

    def __init__(self, text: str = '', parent=None, *,
                 mode=Qt.ElideRight, role: str = '', selectable: bool = False):
        super().__init__('', parent)
        self._mode = mode
        self._full = ''
        if role:
            self.setProperty('role', role)
        if selectable:
            self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setMinimumWidth(0)
        # 横向允许被压到 0（见模块注释：否则它会顶着整行不让缩）
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(str(text or ''))

    # -- Qt 接口 --
    def setText(self, text: str):                 # noqa: N802 - Qt 命名
        self._full = str(text or '')
        self.setToolTip(self._full)
        self._elide()

    def resizeEvent(self, event):                  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._elide()

    def minimumSizeHint(self):                     # noqa: N802 - Qt 命名
        """
        下限只留"还能看出一个字 + …"（24px），**不是整段文字的宽度**。

        QLabel 的 `minimumSizeHint()` 返回整段文字宽；即使 `sizePolicy`
        是 `Ignored`，布局仍把它当缩不下去的下限 —— 2026-10-23 实测：
        同一行里短句 131px、长句 468px，长句那份又把按钮挤出窗口。
        省略标签的语义就是"可以压到任意窄"，下限该由我们说了算。
        """
        base = super().minimumSizeHint()
        return QSize(24, base.height())

    # -- 自己用 --
    def full_text(self) -> str:
        return self._full

    def _elide(self):
        width = max(0, self.width() - 2)
        if width <= 8:
            # 布局还没算完（刚建、还没 show）：先塞全文。
            # 这里**不能**直接画 `…` —— 测试与首帧都会读到一串省略号，
            # 而它并不代表"放不下"，只代表"还没量"。
            super().setText(self._full)
            return
        metrics = QFontMetrics(self.font())
        super().setText(metrics.elidedText(self._full, self._mode, width))


__all__ = ['ElidedLabel']
