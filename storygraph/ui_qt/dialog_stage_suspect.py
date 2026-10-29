# -*- coding: utf-8 -*-
"""
导出前的一句问话：这几行舞台内容看着不像语句（`ui_qt`）
========================================================================

用户 2026-10-29：

> 可以！但也要尽量检测一下！实在看着不像是代码的才加弹窗确认，询问要不要
> 补为备注！！！如果很明显是代码，那就不需要，原样放进去就行……这个判断器
> 要写好，宁错报，不要漏报

判据（谁可疑、为什么）**一行都不在这里** —— 全在
`storygraph.story.stage_suspect`。本文件只做三件事：

  1. 把可疑行**摆出来**：第几拍 / 原文 / 为什么觉得它不像语句；
  2. 让人决定：勾掉不想动的，点「移到备注」；或者「原样导出」；
  3. **不替人做决定，也不偷偷写**：点「取消」= 一个字节都不写。

## 为什么不是 `QMessageBox.question`

要一次说清"哪几拍、原文是什么、为什么"—— 三四行文本塞进 `QMessageBox`
就糊成一团，而作者需要逐条看。更重要的是本仓库踩过"模态框套模态框把测试
挂死"的坑：这里**只开一个窗口**，校验问题就地红字，不再叠框。

## 为什么默认「原样导出」而不是「移到备注」

挪走会改用户填的格子（虽然是把内容搬到另一列，一个字不丢）。工具不该
把"改用户的表"当默认动作 —— 默认 = 不动，要改得人自己点。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout,
                               QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from storygraph.ui_qt import theme
from storygraph.ui_qt import tokens as T

#: 这一格的三种去处。调用方看 `dialog.decision`。
MOVE = 'move'          # 把勾选的挪到「备注」列
KEEP = 'keep'          # 一个字不动，原样导出
CANCEL = 'cancel'      # 什么都不写


class StageSuspectDialog(QDialog):
    """
    用法（调用方 `window_export._review_stage_suspects`）::

        dlg = StageSuspectDialog(self, result)      # result 来自 scan()
        if dlg.exec() != QDialog.Accepted:
            return False                            # 取消 = 零写
        if dlg.decision == MOVE:
            move_to_comment(beats, dlg.selected())
        return True

    本类**不碰 Beat、不调用 `move_to_comment`**：它只收集"要不要挪、挪哪几条"。
    真正动手在调用方 —— 这样这一层可以脱离会话单测（`test_stage_suspect.py`）。
    """

    def __init__(self, parent, result, allow_move: bool = True):
        super().__init__(parent)
        self._result = result
        self._rows = []                 # [(QTableWidgetItem 复选框项, Suspect)]
        self.decision = KEEP
        #: 这一条路能不能"移"。
        #:
        #: `export_script`（卡片页）→ True：内存里的 beats 就是真源，挪了直接编译；
        #: `export_from_table`（从 .xlsx 导出）→ False：那份表的真源是 Excel，
        #: 工具在导出前顺手改用户的工作簿风险更大（还有别的 sheet、格式、
        #: 公式）。那边只提示"第几拍、原文是什么"，请人回 Excel 自己挪。
        self._allow_move = bool(allow_move)

        self.setWindowTitle('导出前检查 —— 这几行舞台内容看着不像语句')
        theme.apply_to(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])

        self.summary = QLabel(
            f'「舞台（高级）」里有 {len(result)} 处不像 Ren\'Py 语句。'
            f'这一列导出时会原样变成脚本里的一行 —— 如果它其实是备注，'
            f'游戏启动时会报语法错误。')
        self.summary.setObjectName('summary')
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        self.hint = QLabel(
            '勾上的移到「备注」列（那一列导出时会自动补 # 号，内容一个字不丢）；'
            '不勾的原样导出。自定义语句请先登记到项目规则，那样这里就不会再问。')
        self.hint.setObjectName('hint')
        self.hint.setWordWrap(True)
        self.hint.setProperty('role', 'note')
        root.addWidget(self.hint)

        self.table = QTableWidget(len(result), 4, self)
        self.table.setObjectName('suspects')
        self.table.setHorizontalHeaderLabels(['移到备注', '拍', '原文', '为什么'])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(3, QHeaderView.Stretch)
        for row, suspect in enumerate(result.suspects):
            check = QTableWidgetItem('')
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Checked)          # 默认全勾（但按钮默认是"原样"）
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1,
                               QTableWidgetItem(str(suspect.seq or '')))
            self.table.setItem(row, 2, QTableWidgetItem(str(suspect.text)))
            self.table.setItem(row, 3, QTableWidgetItem(str(suspect.reason)))
            self._rows.append((check, suspect))
        self.table.resizeColumnsToContents()
        self.table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.table, 1)

        # 提示：这一格里还有别的语句时，挪走可疑片段会把原文拆开 ——
        # 先说清，别让人以为"整格消失"。
        if any(s.multi for s in result.suspects):
            self.multi_note = QLabel(
                '有的格子里除了这一行还有别的语句：挪走只会拿走这一行，'
                '其余语句原地保留。')
            self.multi_note.setObjectName('multi_note')
            self.multi_note.setWordWrap(True)
            self.multi_note.setProperty('role', 'note')
            root.addWidget(self.multi_note)

        bar = QHBoxLayout()
        bar.setSpacing(T.SPACE['sm'])
        self.btn_keep = QPushButton('原样导出')
        self.btn_keep.setObjectName('btn_keep')
        self.btn_keep.setToolTip('一个字都不改，这几行照原文写进脚本')
        self.btn_keep.clicked.connect(self._on_keep)
        self.btn_cancel = QPushButton('取消导出（什么都不写）')
        self.btn_cancel.setObjectName('btn_cancel')
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_move = QPushButton('')
        self.btn_move.setObjectName('btn_move')
        self.btn_move.setProperty('role', 'primary')
        self.btn_move.clicked.connect(self._on_move)
        bar.addWidget(self.btn_cancel, 0)
        bar.addStretch(1)
        bar.addWidget(self.btn_keep, 0)
        bar.addWidget(self.btn_move, 0)
        root.addLayout(bar)

        self._refresh_move_button()
        if not self._allow_move:
            # 从表导出的那条路：不改用户的工作簿，只把"哪几拍"指出来。
            self.table.setColumnHidden(0, True)
            self.btn_move.setVisible(False)
            self.hint.setText(
                '请在表格里把这些内容挪到「备注」列（那一列导出时会自动补 # 号），'
                '或确认它们确实是语句后点「原样导出」。')
        self.resize(860, 420)

    # ------------------------------------------------------------------
    #  勾选框
    # ------------------------------------------------------------------

    def selected(self) -> list:
        """勾上的那些（默认全勾；人取消勾选就不动它）。"""
        return [s for it, s in self._rows
                if it.checkState() == Qt.Checked]

    def _refresh_move_button(self) -> None:
        n = len(self.selected())
        if n:
            self.btn_move.setText(f'移到备注（{n} 条）并继续导出')
            self.btn_move.setEnabled(True)
        else:
            self.btn_move.setText('移到备注（未勾选任何一条）')
            self.btn_move.setEnabled(False)

    def _on_item_changed(self, _item) -> None:
        self._refresh_move_button()

    # ------------------------------------------------------------------
    #  三个按钮
    # ------------------------------------------------------------------

    def _on_move(self):
        if not self.selected():
            return
        self.decision = MOVE
        self.accept()

    def _on_keep(self):
        self.decision = KEEP
        self.accept()

    def _on_cancel(self):
        self.decision = CANCEL
        self.reject()
