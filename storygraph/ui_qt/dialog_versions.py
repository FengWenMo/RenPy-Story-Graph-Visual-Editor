# -*- coding: utf-8 -*-
"""
storygraph.ui_qt.dialog_versions
================================
**历史版本**：翻这份文件的自动备份、看一眼、放回某一版。

从前"退回去"的三种姿势，都有缺口（探针 `.pip-tmp/probe_r10_versions.py`）：

  · 扩展窗口的「撤销上次保存」—— 只退**本窗口这一次**，关窗即失效；
  · 失败提示里那句"旧版本还在同目录的 .bak 里"—— 叫人去翻文件，
    翻到了还得整份复制，而**复制不产生备份**（单向门）；
  · `dialog_asset_rename` 那种"按 `.bak` 逐位还原"—— 只认固定名那一份，
    而且是资源表专用的。

这一格把"磁盘上真有的那几代"摆出来：**时间 / 文件名 / 内容摘要**，右边是
这一版的原话（只读预览）。选中、点「放回这一版」才动手，动手时：

  · 整段在 `core.filelock` 里（读现在 → 比对 → 写回是一件事）；
  · 先比"现在这份还是不是打开清单时那份" —— 别人改过就**一个字不写**；
  · 写回走 `write_text_atomic(backup=True)`：**现在这份自动进新一代备份**，
    所以放错了还能再放回去（不是单向门）。

放回成功会发 `restored(res)`，由调用方去重读内存 / 重画树 —— 这个窗口
**不碰**任何模型，只碰文件。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QPlainTextEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from storygraph.core import versions as V

#: 表格的三列（顺序 = 界面顺序；判据按名字取，别按数字取）
COL_TIME, COL_FILE, COL_NOTE = range(3)


def plain_summary(text) -> dict:
    """不认得格式的文件：给一句"多少字"，其余照旧（`summarize` 的默认值）。"""
    if text is None:
        return {'ok': True, 'count': 0, 'missing': True,
                'note': '（这一版是空的：当时没有这份文件）'}
    return {'ok': True, 'count': 0, 'missing': False,
            'note': f'{len(text)} 字'}


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet('color: #666;')
    return lab


class VersionsDialog(QDialog):
    """一份文件的备份世代：列表 + 预览 + 放回。"""

    restored = Signal(dict)

    def __init__(self, parent=None, path=None, *, summarize=None,
                 title: str = ''):
        super().__init__(parent)
        self._path = Path(str(path)) if path else None
        self._summarize = summarize or plain_summary
        #: 打开这一刻文件里的**原话**：放回时的闸（别人改过就不动手）。
        self._current = None
        self._current_ok = True
        self._versions: list = []
        self.setWindowTitle(title or (f'历史版本 —— {self._path.name}'
                                      if self._path else '历史版本'))
        self.resize(860, 560)
        self._build()
        self.reload()

    # ------------------------------------------------------------------
    #  搭界面
    # ------------------------------------------------------------------

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)
        root.addWidget(_hint(
            f'{V.generations_hint()}（本工具写的每份文件都是这样）。'
            '每一行是「保存时被换掉的那一版」—— 挑一行看右边，确认了再放回。'))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['时间', '文件', '内容'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setWordWrap(False)          # 一行一代：换行会把两代挤成两行
        self.table.setColumnWidth(COL_TIME, 135)
        self.table.setColumnWidth(COL_FILE, 330)
        self.table.horizontalHeader().setSectionResizeMode(
            COL_NOTE, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._on_pick)
        root.addWidget(self.table, 1)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.preview.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.preview.setPlaceholderText('挑一行，这里显示那一版的原话（只读）')
        root.addWidget(self.preview, 2)
        self.status = _hint('')
        root.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_restore = QPushButton('放回这一版')
        self.btn_restore.setEnabled(False)
        self.btn_restore.clicked.connect(lambda: self.restore_selected())
        b_close = QPushButton('关闭')
        b_close.clicked.connect(self.reject)
        row.addWidget(self.btn_restore)
        row.addWidget(b_close)
        root.addLayout(row)

    # ------------------------------------------------------------------
    #  读盘 + 填表
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """重新读"现在这份"与磁盘上的几代备份（内容变了就重填表）。"""
        self._versions = V.backups_of(self._path) if self._path else []
        self._read_current()
        self.table.setRowCount(0)
        for ver in self._versions:
            self._add_row(ver)
        self._fill_status()
        self.preview.clear()
        self.btn_restore.setEnabled(False)

    def _read_current(self) -> None:
        """记下"现在这份"的原话（放回时的闸）。读不出来 → 闸失效并说明。"""
        self._current, self._current_ok = None, True
        if not self._path:
            return
        try:
            self._current = V.read_text_exact(self._path)
        except OSError:
            self._current, self._current_ok = None, False

    def _add_row(self, ver: V.Version) -> None:
        """一代备份 → 表里一行（读不出来也照样列出，内容那格写明）。"""
        try:
            text = V.read_text_exact(ver.path)
        except OSError:
            summary = {'note': '读不出来（权限 / 被占用）'}
        else:
            summary = self._summarize(text)
        row = self.table.rowCount()
        self.table.insertRow(row)
        cells = (V.stamp_text(ver.written), ver.name,
                 str(summary.get('note') or ''))
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if col == COL_FILE:
                item.setToolTip(str(ver.path))
            self.table.setItem(row, col, item)

    def _fill_status(self) -> None:
        if self._path is None:
            self.status.setText('没有文件 —— 没得看。')
            return
        if not self._current_ok:
            self.status.setText(f'{self._path.name} 现在读不出来（权限 / 被占用）——'
                                '只列历史，不放回。')
            return
        now = self._summarize(self._current)
        head = f'现在：{now.get("note") or "?"}'
        if not self._versions:
            self.status.setText(head + '｜还没有备份 ——'
                                       f'第一次写盘时会把当时那份留成第 1 代。')
            return
        self.status.setText(head + f'｜磁盘上有 {len(self._versions)} 代备份')

    # ------------------------------------------------------------------
    #  选 / 放回
    # ------------------------------------------------------------------

    def picked(self):
        """当前选中的那一代（没选 → `None`）。"""
        row = self.table.currentRow()
        if row < 0 or row >= len(self._versions):
            return None
        return self._versions[row]

    def _on_pick(self) -> None:
        ver = self.picked()
        # 现在这份读不出来时也**不给按**：放回要拿"现在这份"当闸，闸都没有，
        # 按下去只会被拒（"只列历史，不放回"，与状态栏那句同一条口径）。
        self.btn_restore.setEnabled(ver is not None and self._current_ok)
        if ver is None:
            self.preview.clear()
            return
        try:
            text = V.read_text_exact(ver.path)
        except OSError as e:
            self.preview.setPlainText(f'（读不出来：{e}）')
            return
        self.preview.setPlainText('' if text is None else text)

    def restore_selected(self) -> dict:
        """把选中的那一代放回去 → `core.versions.restore` 的回执。"""
        ver = self.picked()
        if ver is None:
            self.status.setText('先在上面挑一版。')
            return {'ok': False, 'reason': '没有选中任何一版'}
        res = V.restore(self._path, ver.path, expect_current=self._current)
        if res.get('ok'):
            # 先重读（列表多了一代、闸也要跟着现在这份换），再写状态栏 ——
            # 顺序反了会被 `reload()` 的 `_fill_status()` 当场盖掉。
            self.reload()
            self._say_restored(ver, res)
            self.restored.emit(res)
            return res
        reason = str(res.get('reason') or '?')
        if res.get('changed'):
            # 现在这份已经不是我打开时那份了：**先重读**（闸跟着换、列表按
            # 磁盘重来），再写状态 —— 反了会被 `reload()` 的 `_fill_status()`
            # 盖掉，人就只看到"现在：3 条"，不知道刚才为什么没动。
            self.reload()
            self.status.setText(f'没放回：{reason}｜列表已按现在这份重来')
        else:
            self.status.setText('没放回：' + reason)
        return res

    def _say_restored(self, ver: V.Version, res: dict) -> None:
        """放回之后的说明。**这句要说清"还能再放回"**，否则人不敢按。"""
        if res.get('same'):
            self.status.setText(f'{ver.name} 和现在逐字节一样，什么都没改。')
            return
        self.status.setText(
            f'已放回 {V.stamp_text(ver.written)} 那一版（{ver.name}）。'
            '放回之前那份进了新一代备份 —— 放错了还能再放回。')


__all__ = ['COL_FILE', 'COL_NOTE', 'COL_TIME', 'VersionsDialog', 'plain_summary']
