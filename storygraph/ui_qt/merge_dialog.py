# -*- coding: utf-8 -*-
"""
storygraph.ui_qt.merge_dialog
=============================
**合并回源**的确认框：一处一个勾（2026-10-31）。

## 为什么不是 `QMessageBox`

`docs/超清CG与BG_零侵入导入_2026-10-31.md` 把这件事归在 **L4**（改用户的
`show` 行），报警下限是 **`confirm`，永远、逐处**。`QMessageBox` 只能给
"是 / 否"：一处也去不掉、一处也多不了 —— 而那几处里完全可能有作者**故意**
写的一个推近（`show cg cg01@closeup`）。合并不该把"故意的"和"忘了的"
一起抹平。

所以这里给的是**每处一个勾**的框：

  · 默认**全勾**（大多数情况就是"全都要合"）；
  · 勾掉的那处**一个字都不动**（`MergePlan.apply(selected=…)` 只写勾上的）；
  · 改不了的那几处**单独列在下面**，说清为什么 —— 绝不静默跳过。

## 为什么"全选/全不选"也要有

58 处那种规模（真机两个项目都出现过同款）用鼠标一个个点会点到手酸；而不勾
任何一个等于白点一次按钮。所以顶部给一个开关，`全不选` 之后按"确定"是
**什么都不做**（调用方会说"一处都没勾"），不是"取消"。

## 判据（`tests/test_merge_dialog.py`）

  · 勾默认全开，`chosen()` = 全部拍号；
  · 取消勾选 → `chosen()` 少一个（并且**只少那一个**）；
  · `全不选` → `chosen()` 为空；
  · 改不了的那几处**列在框里**（不是只在 plan 里躺着）；
  · 框里那句"不删行、不加行"必须在（L4 的边界，不能让人以为在删代码）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFrame,
                               QLabel, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from storygraph.core.safeguard import swallow

#: 勾选框列表最多这么高（再多就滚动）—— 别让确认框比屏幕还长。
LIST_MAX_HEIGHT = 300


class MergeDialog(QDialog):
    """「一并改成一样」的确认框（一处一个勾）。"""

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self._plan = plan
        self._boxes: list = []
        self.setWindowTitle('一并改成一样？')
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        head = QLabel(self._head_text(plan))
        head.setWordWrap(True)
        head.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(head)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        lay.addWidget(line)

        lay.addWidget(self._list_widget(plan))
        if getattr(plan, 'skipped', ()):
            warn = QLabel(self._skipped_text(plan))
            warn.setWordWrap(True)
            warn.setProperty('role', 'warn')
            warn.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(warn)

        foot = QLabel('只改这几拍的 @at：「不删行、不加行」，图名与 '
                      'behind/with/as 一个字都不动。\n'
                      '现在只改内存里的 Beat，不写盘 —— 导出时还会逐行给你看 '
                      'diff，那一步仍能反悔。')
        foot.setWordWrap(True)
        foot.setProperty('role', 'caption')
        lay.addWidget(foot)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText('改成一样')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    # ---------- 内容 ----------

    def _head_text(self, plan) -> str:
        at = str(getattr(plan, 'at', '') or '')
        value = f'「{at}」' if at else '原样（不带 at）'
        label = str(getattr(plan, 'label', '') or '')
        source = int(getattr(plan, 'source', -1))
        count = int(getattr(plan, 'count', 0))
        return (f'把本景 {count} 处单独改写的 at 改成 {value}'
                f'（源：第 {source + 1} 拍 · {label}）。\n'
                '下面每一处都可以单独去掉勾 —— 勾掉的原样不动。')

    def _list_widget(self, plan) -> QWidget:
        steps = list(getattr(plan, 'steps', ()) or ())
        host = QWidget()
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        for step in steps:
            box = QCheckBox(self._step_text(step))
            box.setChecked(True)
            box.setToolTip(str(getattr(step, 'after', '') or ''))
            box.toggled.connect(lambda _on: self._sync_toggle())
            col.addWidget(box)
            self._boxes.append(box)
        col.addStretch(1)          # 别让几行勾选框被摊到整个滚动区高度上
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(host)
        # 高度跟内容：两三处时不要留一大片空白（框看着像坏了），
        # 上百处时才封顶滚动。
        want = min(LIST_MAX_HEIGHT, 26 + 22 * len(steps))
        scroll.setMinimumHeight(want)
        scroll.setMaximumHeight(want)
        # 「全选 / 全不选」：58 处那种规模一个一个点会点到手酸
        self._toggle = QPushButton('全不选')
        self._toggle.setMaximumWidth(80)
        self._toggle.setToolTip('把下面的勾全部去掉（等于这次什么都不改）。')
        self._toggle.clicked.connect(self._toggle_all)
        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        outer.addWidget(self._toggle)
        outer.addWidget(scroll)
        return wrap

    def _step_text(self, step) -> str:
        before = str(getattr(step, 'before_at', '') or '') or '原样'
        after = str(getattr(step, 'after_at', '') or '') or '原样'
        return (f'第 {int(getattr(step, "index", -1)) + 1} 拍 · '
                f'{getattr(step, "image", "") or ""}：{before} → {after}')

    def _skipped_text(self, plan) -> str:
        lines = [f'改不了 {len(plan.skipped)} 处（这几处要你自己改）：']
        for skip in plan.skipped:
            lines.append(f'· 第 {int(getattr(skip, "index", -1)) + 1} 拍：'
                         f'{getattr(skip, "reason", "") or "改不了"}')
        return '\n'.join(lines)

    def _toggle_all(self) -> None:
        """现在一个没勾 → 全勾上；否则全去掉（并同步按钮文字）。"""
        target = not self.chosen()
        for box in self._boxes:
            box.setChecked(target)
        self._sync_toggle()

    def _sync_toggle(self) -> None:
        try:
            self._toggle.setText('全不选' if self.chosen() else '全选')
        except Exception as e:                    # noqa: BLE001 - 按钮文字事小
            swallow('ui_qt.merge_dialog.sync', e)

    # ---------- 结果 ----------

    def chosen(self) -> set:
        """勾上的拍号集合（空集 = 什么都不改）。"""
        steps = list(getattr(self._plan, 'steps', ()) or ())
        out = set()
        for box, step in zip(self._boxes, steps):
            if box.isChecked():
                out.add(int(getattr(step, 'index', -1)))
        return out

    def boxes(self) -> list:
        return list(self._boxes)


def ask_merge(plan, parent=None):
    """
    弹窗 → 勾选的拍号集合；**取消**返回 `None`（与"一个都没勾"区分开）。

    区分这两件事是刻意的：取消 = 人改主意了；全不勾 = 人看过了、决定
    一处都不动。调用方说的话不一样（前者什么都不说，后者要说"一处都没勾"）。
    """
    try:
        dialog = MergeDialog(plan, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.chosen()
    except Exception as e:                        # noqa: BLE001 - 框打不开就别改
        swallow('ui_qt.merge_dialog.ask', e)
        return None


__all__ = ['LIST_MAX_HEIGHT', 'MergeDialog', 'ask_merge']
