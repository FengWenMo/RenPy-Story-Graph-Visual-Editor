"""
storygraph.ui_qt.validation_list
================================
**校验结果的可点清单**：每条问题都能带你到那一拍。

为什么要有它（2026-10-31，UX 建议 ③ 的落地）
---------------------------------------------
「校验当前章节」从前只把结果写进控制台：

    [校验] chapter3_1.rpy：3 个错误 / 12 条提醒
        · [E305] 引用了不存在的图 `bg ghost`
        · ...

问题不是"信息不够"，是**能说出问题在哪儿，却带不了路** ——
用户拿着 `chapter3_1.0042` 这串坐标，得自己在 266 张卡片里翻。

所以这里只做一件事：**把 `Issue.where` 变成一次双击**。

三条硬规矩
----------
  · **认不出坐标就不给按钮**。没有 `where` 或解析不出拍号的条目，
    双击不跳、也不显示"定位"入口 —— 一个点了没反应的按钮比没有更糟。
  · **不猜**。`where` 只认两种形态：`line_id` 精确相等、`label.seq`
    （`seq` 必须是纯数字）。其余一律算"无位置"，并把原文显示出来
    让用户自己看。
  · **不替上层决定跳不跳得动**。这里只发信号，真正的定位在窗口层
    （`window_graph_ops._locate_issue`），因为它才拿得到 beats。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from storygraph.ui_qt import theme
from storygraph.ui_qt import tokens as T


def issue_fields(issue) -> dict:
    """`Issue`（dict 或对象）→ 统一的五个字段。

    校验层返回的是 `dataclass Issue`，但历史上也有 dict 形态的调用方，
    两边都兼容 —— **不在这里猜类型**，取不到就给空串。
    """
    if isinstance(issue, dict):
        get = issue.get
        return {
            'level': str(get('level') or ''),
            'code': str(get('code') or ''),
            'severity': str(get('severity') or ''),
            'message': str(get('message') or ''),
            'where': str(get('where') or ''),
            'hint': str(get('hint') or ''),
        }
    return {
        'level': str(getattr(issue, 'level', '') or ''),
        'code': str(getattr(issue, 'code', '') or ''),
        'severity': str(getattr(issue, 'severity', '') or ''),
        'message': str(getattr(issue, 'message', '') or ''),
        'where': str(getattr(issue, 'where', '') or ''),
        'hint': str(getattr(issue, 'hint', '') or ''),
    }


class ValidationListDialog(QDialog):
    """
    校验结果清单。双击有位置的条目 → 发 `issue_activated(fields)`。

    `fields` 就是 `issue_fields()` 的那个 dict，窗口层拿它去定位；
    定位失败由窗口层给状态提示，**本对话框不做任何跳转判断**。
    """

    issue_activated = Signal(dict)

    def __init__(self, parent=None, errors=None, warnings=None,
                 title: str = '校验结果'):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(860, 600)
        theme.apply_to(self)

        errors = [issue_fields(i) for i in (errors or ())]
        warnings = [issue_fields(i) for i in (warnings or ())]

        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])

        head = QLabel(f'{len(errors)} 个错误 · {len(warnings)} 条提醒')
        head.setProperty('role', 'h3')
        root.addWidget(head)

        tip = QLabel('双击任意一条 → 跳到那一拍。'
                     '右侧写「无位置」的条目跳不了（校验本身没给坐标）。')
        tip.setProperty('role', 'note')
        tip.setWordWrap(True)
        root.addWidget(tip)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(['级别 · 说明', '位置'])
        self._tree.setRootIsDecorated(False)
        self._tree.setColumnWidth(0, 640)
        self._tree.itemDoubleClicked.connect(self._on_double)
        root.addWidget(self._tree, 1)

        self._status = QLabel('')
        self._status.setProperty('role', 'note')
        root.addWidget(self._status)

        bar = QHBoxLayout()
        copy = QPushButton('复制全部')
        copy.clicked.connect(self._copy)
        bar.addWidget(copy)
        bar.addStretch(1)
        close = QPushButton('关闭')
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        root.addLayout(bar)

        self._fill(errors, warnings)
        if not errors and not warnings:
            self._status.setText('没有发现问题 —— 这一章的校验是干净的。')

    # -- 内部 ----------------------------------------------------------

    def _fill(self, errors, warnings):
        for group, items in (('错误', errors), ('提醒', warnings)):
            if not items:
                continue
            top = QTreeWidgetItem([f'{group}（{len(items)}）', ''])
            top.setFirstColumnSpanned(True)
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            self._tree.addTopLevelItem(top)
            for it in items:
                # ⚠ 必须 `addChild`（挂到分组标题下），不是 `addTopLevelItem`：
                # 后者会让"错误/提醒"两个标题变成孤儿行，所有条目平铺 ——
                # 分组只是一行字，折叠和复制都跟着错。
                self._add(top, it)

    def _add(self, parent, it: dict) -> None:
        tag = it['code'] or it['level']
        text = f'[{tag}] {it["message"]}' if tag else it['message']
        where = it['where']
        # **只有解析得出的坐标才写出来**；写不出来的明说"无位置"，
        # 不给人一个点了不动的入口。
        item = QTreeWidgetItem([text, where or '无位置'])
        item.setData(0, Qt.UserRole, it)
        if not where:
            # 灰一点，和"能跳"的行区分开；颜色只做视觉提示，
            # **真正的判据是 `where` 为空**（下面双击时用同一个判据）。
            item.setForeground(1, QBrush(QColor(theme.t('text.tertiary'))))
            item.setToolTip(0, '校验没有给出位置 —— 这条只能人工找。')
        if it['hint']:
            item.setToolTip(0, f'{it["message"]}\n\n怎么修：{it["hint"]}')
        parent.addChild(item)

    def _on_double(self, item, _column):
        it = item.data(0, Qt.UserRole)
        if not it:
            return                       # 分组标题行
        if not it['where']:
            self._status.setText('这条没有位置信息，跳不过去。')
            return
        self.issue_activated.emit(it)

    def _copy(self):
        lines = []
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            lines.append(top.text(0))
            for j in range(top.childCount()):
                c = top.child(j)
                lines.append(f'  {c.text(0)}   [{c.text(1)}]')
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText('\n'.join(lines))
        self._status.setText('已复制到剪贴板。')
