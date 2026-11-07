"""
「技术调用的参数」小表（**还是那一个 `columns` 形状**，2026-11-07 第四轮）
========================================================================

用户 2026-11-07 的原话："自定义模块一定要支撑起复杂的转场调用一类的。"
上一轮把它做成了能扫、能存；但**参数那一格**还是要人背一行语法：

    转场@0; 层=layer          ← 第 0 个位置参数叫「转场」，关键字参数 layer

这一格是这扇窗口里**最后一份私有语法**：同一个 `columns` 形状，扩展窗口用
字段表表达、调用窗口要人背符号。这一轮把它换成一张小表：

    | 参数名 | 显示名 | 位置（0,1,2…） | 关键字名 |
    | 转场   | 转场   | 0              |          |
    | 层     | 层     |                | layer    |

**位置和关键字二选一**（填了位置 = 位置参数，填了关键字 = 关键字参数，
两个都填就是错）。表格的每一格就是 `columns` 里的一个键，没有第二层翻译：

    {'name': …, 'label': …, 'position': 0}   或
    {'name': …, 'label': …, 'kw': 'layer'}

为什么不做成"再写一行文本"：`转场@0` 这种写法里，`@` 和 `0` 是两个概念被
压进一个字符串（名字 / 位置），人写错的时候分不清错在哪；而 `层=layer`
里又有一个"`=` = 关键字"的约定 —— 同一个符号要学两次。
"""
from __future__ import annotations

from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout,
                               QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from storygraph.story import statement_registry as SR
from storygraph.ui_qt import theme
from storygraph.ui_qt import tokens as T

#: 参数表的列（顺序就是人的阅读顺序：名字 → 给人看的名 → 怎么传）
_HEADERS = ['参数名（剧本里那个参数）', '显示名（可空，卡片上那一格的标题）',
            '位置：第几个参数（0 起）', '关键字名（留空 = 位置参数）']

#: 这两个坐标在 `_HEADERS` 里的下标（`built` 与 `add_row` 共用）
_COL_NAME, _COL_LABEL, _COL_POS, _COL_KW = 0, 1, 2, 3


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setProperty('role', 'caption')
    return lab


class CallParamsDialog(QDialog):
    """一条技术调用的参数表（位置 / 关键字二选一，写错就说，不猜）。"""

    def __init__(self, columns=(), head: str = '', parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'参数 —— {head}' if head else '技术调用的参数')
        theme.apply_to(self)
        self._head = str(head or '')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(T.SPACE['lg'], T.SPACE['md'],
                               T.SPACE['lg'], T.SPACE['md'])
        lay.setSpacing(T.SPACE['sm'])
        title = QLabel('这条调用要哪些参数')
        title.setProperty('role', 'panelTitle')
        lay.addWidget(title)
        lay.addWidget(_hint(
            '剧本里 `$ my_wipe("left", 0.6)` 的两个参数，就是下面两行：'
            '第 0 个、第 1 个。位置参数填「位置」那一列（0/1/2…）；'
            '关键字参数（`layer="screens"`）改填「关键字名」那一列。'
            '两个都填 = 写错了，会当场说。'))
        lay.addWidget(_hint(
            '「位置」不必连着写：只想让工具管第 2 个参数，就只写一行、位置填 2。'
            '位置空着的那几个参数，工具原样保留（它只改你给出来的这几格）。'))

        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(_COL_LABEL, QHeaderView.Stretch)
        hh.setSectionResizeMode(_COL_POS, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(_COL_KW, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(150)
        lay.addWidget(self.table, 1)

        tools = QHBoxLayout()
        add_pos = QPushButton('＋ 位置参数')
        add_pos.setToolTip('按顺序加一个位置参数（位置自动填下一个空位）')
        add_pos.clicked.connect(lambda: self.add_positional())
        add_kw = QPushButton('＋ 关键字参数')
        add_kw.setToolTip('加一个 `名字=值` 那种参数（例：`layer="screens"`）')
        add_kw.clicked.connect(lambda: self.add_keyword())
        rm = QPushButton('删掉这一行')
        rm.clicked.connect(self.remove_row)
        tools.addWidget(add_pos)
        tools.addWidget(add_kw)
        tools.addWidget(rm)
        tools.addStretch(1)
        lay.addLayout(tools)

        self.status = _hint('')
        lay.addWidget(self.status)

        foot = QHBoxLayout()
        foot.addStretch(1)
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)
        ok = QPushButton('就这些参数')
        ok.setProperty('variant', 'primary')
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        foot.addWidget(ok)
        lay.addLayout(foot)

        self.resize(720, 380)
        for col in (columns or ()):
            if isinstance(col, dict):
                self.add_row(col)

    # ---- 加 / 删 ------------------------------------------------------
    def add_row(self, column=None):
        col = column if isinstance(column, dict) else {}
        name = str(col.get('name') or '')
        kw = str(col.get('kw') or '')
        pos = col.get('position')
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, _COL_NAME, QTableWidgetItem(name))
        self.table.setItem(row, _COL_LABEL, QTableWidgetItem(
            str(col.get('label') or '')))
        self.table.setItem(row, _COL_POS, QTableWidgetItem(
            '' if (kw or pos is None) else str(pos)))
        self.table.setItem(row, _COL_KW, QTableWidgetItem('' if not kw else kw))

    def _next_position(self) -> int:
        """下一个空着的位置（**不猜**：只看表里已有的数字）。"""
        used = set()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, _COL_POS)
            text = (item.text() if item else '').strip()
            if text.isdigit():
                used.add(int(text))
        pos = 0
        while pos in used:
            pos += 1
        return pos

    def add_positional(self):
        row = self.table.rowCount()
        self.add_row({'name': '', 'position': self._next_position()})
        self.table.setCurrentCell(row, _COL_NAME)
        self.table.editItem(self.table.item(row, _COL_NAME))

    def add_keyword(self):
        # 位置那一格留空 = "这一行是关键字参数"（`add_row` 见 pos=None 就不填）
        row = self.table.rowCount()
        self.add_row({})
        self.table.setCurrentCell(row, _COL_NAME)
        self.table.editItem(self.table.item(row, _COL_NAME))

    def remove_row(self):
        row = self.table.currentRow()
        if row < 0 and self.table.rowCount():
            row = self.table.rowCount() - 1
        if row >= 0:
            self.table.removeRow(row)

    # ---- 读 ----------------------------------------------------------
    def _cells(self, row: int) -> tuple:
        def cell(idx):
            item = self.table.item(row, idx)
            return (item.text() if item else '').strip()
        return (cell(_COL_NAME), cell(_COL_LABEL), cell(_COL_POS),
                cell(_COL_KW))

    def built(self) -> tuple:
        """表格 → `(columns, problems)`（纯；坏行**跳过**并说清第几行）。"""
        out, bad = [], []
        seen_pos, seen_kw = {}, {}
        for row in range(self.table.rowCount()):
            name, label, pos_text, kw = self._cells(row)
            if not name:
                bad.append(f'第 {row + 1} 行没有参数名')
                continue
            if pos_text and kw:
                bad.append(f'第 {row + 1} 行"位置"和"关键字名"都填了 —— '
                           f'这两个只能选一个')
                continue
            if kw:
                if not SR.call_kw_ok(kw):
                    bad.append(f'第 {row + 1} 行的关键字名 `{kw}` 不是合法名字'
                               f'（字母或下划线开头，不能有空格）')
                    continue
                if kw in seen_kw:
                    bad.append(f'第 {row + 1} 行和第 {seen_kw[kw]} 行的关键字'
                               f'都是 `{kw}` —— 同一个参数不要写两行')
                    continue
                seen_kw[kw] = row + 1
                out.append({'name': name, 'label': label or name, 'kw': kw})
                continue
            if not pos_text:
                bad.append(f'第 {row + 1} 行没填位置 —— 位置参数要写第几个'
                           f'（0、1、2…），关键字参数请填"关键字名"')
                continue
            if not pos_text.isdigit():
                bad.append(f'第 {row + 1} 行的位置 `{pos_text}` 不是数字')
                continue
            pos = int(pos_text)
            if pos in seen_pos:
                bad.append(f'第 {row + 1} 行和第 {seen_pos[pos]} 行都是第 '
                           f'{pos} 个参数 —— 一个位置只能有一格')
                continue
            seen_pos[pos] = row + 1
            out.append({'name': name, 'label': label or name, 'position': pos})
        if not out and not bad:
            bad.append('一个参数都没有 —— 至少要一格（否则这条调用读不到值）')
        return out, bad

    def _accept(self):
        """有问题就**留在窗口里**说清（关掉窗口只会让人以为保存了）。"""
        _cols, bad = self.built()
        if bad:
            self.status.setText('还不能保存：' + '；'.join(bad[:3]))
            return
        self.accept()


__all__ = ['CallParamsDialog']
