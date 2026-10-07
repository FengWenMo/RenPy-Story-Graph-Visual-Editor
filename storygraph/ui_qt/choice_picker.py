"""
storygraph.ui_qt.choice_picker
==============================
**可搜索的候选选择器** —— 右键 chip 时弹它。

为什么需要（用户 2026-10-05）：

> 既然变换如此多，那为什么不加个右键列表筛选跟搜索功能呢？

`SpriteCardWidget` 的 chip 是"点一下换下一个"——候选少时够用，而真实项目的
位置候选有 **151 个**（146 个自定义 transform + 内置位置），点 40 次才轮到
`i11`，那不叫能用。这里给一个"输入即筛 + 列表点选"的弹窗：

  · `filter_choices(choices, query)` —— **纯函数**（可单测，不碰 Qt）；
  · `ChoicePickerDialog` / `choose_from(...)` —— 真弹窗（`None` = 用户取消）。

过滤口径：去空白、**忽略大小写**、**子串匹配**（`i1` 能筛出 `i11`/`bi11`）；
查询为空 → 全给。刻意不做模糊打分：候选就是"要写进脚本的名字"，
排序必须稳定可预期（按传入顺序），否则用户找东西的手感每次都变。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QVBoxLayout)

__all__ = ['ChoicePickerDialog', 'choose_from', 'filter_choices']


def filter_choices(choices, query: str = '') -> list:
    """
    候选 → 过滤后的候选（**纯函数**）。

    · 查询为空（或只有空白）→ 原样返回（顺序不动）；
    · 否则忽略大小写做**子串**匹配，保持原顺序。

    为什么是子串而不是模糊打分：候选就是"脚本里要写的那个名字"，
    顺序稳定比"聪明"重要 —— 每次筛出来的次序都变，用户的手指就记不住位置。
    """
    items = [str(c) for c in (choices or []) if str(c).strip()]
    q = str(query or '').strip().lower()
    if not q:
        return items
    return [c for c in items if q in c.lower()]


class ChoicePickerDialog(QDialog):
    """一行筛选框 + 一个列表；双击/回车即选定。"""

    def __init__(self, parent=None, title: str = '选择', choices=None,
                 current: str = ''):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(360, 460)
        self._all = [str(c) for c in (choices or [])]
        self.value = ''

        lay = QVBoxLayout(self)
        self._hint = QLabel(f'共 {len(self._all)} 个候选 —— 输入即筛，'
                            f'双击选定（Esc 取消）')
        self._hint.setProperty('role', 'caption')
        self._hint.setWordWrap(True)
        lay.addWidget(self._hint)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText('筛选…（例：i1）')
        self._filter.textChanged.connect(self._refill)
        lay.addWidget(self._filter)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._accept_item)
        lay.addWidget(self._list, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_current)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self._refill('')
        self._select(current)

    # ------------------------------------------------------------------

    def _refill(self, query: str):
        self._list.clear()
        for name in filter_choices(self._all, query):
            QListWidgetItem(name, self._list)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._hint.setText(f'{self._list.count()} / {len(self._all)} 个候选'
                           f' —— 双击选定（Esc 取消）')

    def _select(self, current: str):
        cur = str(current or '').strip()
        if not cur:
            return
        for row in range(self._list.count()):
            if self._list.item(row).text() == cur:
                self._list.setCurrentRow(row)
                return

    def _accept_item(self, item):
        if item is not None:
            self.value = item.text()
            self.accept()

    def _accept_current(self):
        item = self._list.currentItem()
        if item is not None:
            self.value = item.text()
            self.accept()

    def keyPressEvent(self, event):
        """回车 = 选定（列表有焦点时 QListWidget 自己也会处理双击）。"""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._accept_current()
            return
        super().keyPressEvent(event)


def choose_from(parent, title: str, choices, current: str = '') -> str:
    """弹选择器；返回选中的名字（取消 → `''`）。"""
    dlg = ChoicePickerDialog(parent, title=title, choices=choices,
                             current=current)
    if dlg.exec() == QDialog.Accepted:
        return dlg.value
    return ''
