"""
冲突预检弹窗（`ui_qt`）—— 撞 label / 会覆盖手写文件时，把新旧内容摆出来让人逐份拍板
==============================================================================

用户 2026-10-26 实机原话：

> 我一个个删 vscode 里面的 .bak 和 blocked 里面的文件老狼狈了……
> 当检测到有 label 重复的时候，能不能不要擅自不落盘，或者擅自全量替换呀……
> 能不能给出一个 UI 提醒，让我能够对着 txt 换 label？如果撞 label，
> 显示新内容与旧内容对比，再决定是否强制更换（可以全部强制更换，
> 也可以一个个文件分……）

纯逻辑（谁撞了谁 / 三种动作各自写到哪 / 新名字合不合法）一行都不在这里 ——
全在 `storygraph.authoring.import_review`。本文件只做三件事：

  1. **把处境摆出来**：左边一表，一行一个源文件（源 .txt / 撞上了什么 /
     处理方式 / 新 label）；右边把**旧文件全文 vs 新产物**按行对齐、着色、
     带行号地放出来 —— 人看得见"撞的是哪个文件、差在哪"才敢点；
  2. **让批量操作省事**：「全部覆盖旧文件 / 全部改个名字 / 全部跳过」一点
     就好；逐份也能改（下拉选动作 + 输入框填新 label）；
  3. **不替人做决定，也不替门禁放行**：这里只收集选择（`choices()`），
     真正落盘 / 校验是会话层 + `apply_decisions` + `write_generation` 的事。
     点「取消（什么都不写）」= **一个字节都不写**（连 `.blocked.rpy` 都不产生）。

## 为什么对比栏要按 diff 行对齐，而不是并排两份原文

一场戏 200~400 行。直接并排两份原文，人眼要在两栏滚动里同时找"哪一句被改了"，
等于没对比。所以左右两个 QTextEdit 吃的是**同一份 `DiffRow` 列表**：行数必然
一样多，某一边没有这行就留空行（行号沿用**原文件的行号**）—— 人一眼就能说
"旧文件第 12 行被改了"。底色：改=黄、增=绿、删=红（色值只从 `tokens` 取，
深色主题跟着换）。

## 为什么"就地报错"而不是弹 `QMessageBox`

本仓库踩过"模态框把测试挂死"的坑（见 `dialogs._BaseDialog` 的注释）：校验
失败时在对话框里出一行红字说清"第几行、为什么"，**不关窗口、不叠模态框**，
人改完那一个输入框就能再点。

## 为什么默认值就是"不动"

见 `import_review.ReviewItem.default_action`：工具以前生成的 → 预选「覆盖」；
手写的 / 项目本来就重名 → 预选「跳过」。用户点错"继续"也不至于毁掉手写文件。
"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QTextEdit,
                               QVBoxLayout, QWidget)

from storygraph.authoring import import_review as IR
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme

#: 三种动作 → 下拉里的中文文案。动作取值本身以 `import_review.ALL_ACTIONS`
#: 为唯一来源，这里只负责"人话"（界面文案与落地语义必须对得上）。
_ACTION_TEXT = {
    IR.ACT_REPLACE: '覆盖旧文件',
    IR.ACT_RENAME: '改个名字写入',
    IR.ACT_SKIP: '这次跳过',
}

#: diff 行的底色 → 取 `tokens` 里的**强语义色**再压透明度（界面色值只许有
#: 这一个来源）。为什么不用现成的 `state.*Bg` 那三档浅底色：那三档是横幅 /
#: 提示条的底色（`#FEF3E2` 这种在白色正文里几乎看不出边界），而对比栏要的是
#: "扫一眼就知道哪几行变了"。压透明度还有个好处：深色主题下同一条规则直接
#: 变成"暗底上的高亮"，不用两套色值。
_DIFF_STATE = {
    'chg': 'state.warning',           # 改 = 黄
    'add': 'state.success',           # 增 = 绿
    'del': 'state.error',             # 删 = 红
}

#: 差异底色的透明度（0~255）：看得出来，又不至于盖住等宽字体的正文。
DIFF_ALPHA = 80

#: 表格行高：冲突说明是中文长句（"`label X` 已经被 game/.../x.rpy 定义……"），
#: 一行放不下。这里是**最小行高**，真正的行高在 show/resize 之后按列宽算
#: （见 `_fit_rows`），保证句子不被切一半。
ROW_HEIGHT = 52


def _diff_colour(kind: str) -> QColor:
    """diff 行底色：tokens 的强色 + 统一透明度（`kind` ∈ chg/add/del）。"""
    colour = QColor(T.t(_DIFF_STATE[kind]))
    colour.setAlpha(DIFF_ALPHA)
    return colour


def _gutter(line_no: int, text: str, width: int) -> str:
    """
    对比区的一行：`  12 │ 内容`；这一边没有这行就只留空白（对齐靠它）。

    行号用**原文件的行号**（`DiffRow.old_no/new_no`）而不是"第几条记录"：
    人要拿它回 vscode 里找位置，必须是真行号。
    """
    if line_no:
        return f'{line_no:>{width}} │ {text}'
    return ' ' * (width + 1) + '│ '


def action_text(action: str) -> str:
    """动作 → 中文文案（测试与日志共用；未知动作原样返回而不是猜一个）。"""
    return _ACTION_TEXT.get(action, action)


class CollisionReviewDialog(QDialog):
    """
    冲突预检弹窗（`review` 来自 `import_review.preflight`）。

    用法（唯一调用方是 `window_graph_ops._resolve_import_conflicts`）::

        dlg = CollisionReviewDialog(parent, review)
        if dlg.exec() == QDialog.Accepted:
            decisions = dlg.choices()    # 与 apply_decisions 的入参同格式
        # 取消 → 调用方返回 None → 会话层保证一个字节都不写

    本类**不碰磁盘、不改 `ReviewItem`、不落任何判据**：动作选项来自
    `item.options`，默认值来自 `item.default_action`，新名字校验来自
    `review.rename_problem`（与落盘层共用同一套理由，避免"弹窗说行、
    落地说不行"）。右侧对比文本来自 `item.diff()`。
    """

    def __init__(self, parent, review):
        super().__init__(parent)
        self._review = review
        self._items = list(review.items or [])
        self._combos = []
        self._label_edits = []
        self._current = -1
        #: 两个 QTextEdit 互相联动时的重入闸（`valueChanged` 会来回触发）。
        self._syncing = False
        #: 行高重算是否已排队（`show`/`resize` 会各排一次，只跑一次就够）。
        self._fit_pending = False
        #: 批量按钮执行中（此时下拉变化**不许**抢走当前选中行）。
        self._batch_running = False

        self.setWindowTitle('导入冲突预检 —— 新旧内容对比')
        theme.apply_to(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])

        # —— 顶部说明：**先让人知道"现在什么都没写"**（用户最怕擅自落盘）——
        self.summary = QLabel(
            f'{review.summary()} —— 没决定之前一个字节都不写；'
            f'点「取消（什么都不写）」= 什么都不写。')
        self.summary.setObjectName('summary')
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        self.hint = QLabel(
            '「覆盖旧文件」= 把新产物写进那份已经占位的 .rpy（旧文件先备份成 '
            '.bak；手写文件里的 define / init python / transform 会一起消失，'
            '右边橙色警告会逐条列出来）。'
            '「改个名字写入」= 只新写一个文件，旧文件一个字不动。'
            '「这次跳过」= 这一份不写，也不留任何对照副本。')
        self.hint.setObjectName('hint')
        self.hint.setWordWrap(True)
        self.hint.setProperty('role', 'note')
        root.addWidget(self.hint)

        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.setObjectName('splitter')
        self.splitter.addWidget(self._build_table())
        self.splitter.addWidget(self._build_compare())
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([720, 600])
        root.addWidget(self.splitter, 1)

        self.error = QLabel('')
        self.error.setObjectName('error')
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {T.t('state.error')};")
        self.error.setVisible(False)
        root.addWidget(self.error)

        root.addLayout(self._build_buttons())

        if self._items:
            for row in range(len(self._items)):
                self._refresh_row(row)
            self._select_row(0)
            self._show_item(0)
        self.resize(1360, 820)

    # ==================================================================
    #  搭界面
    # ==================================================================

    def _build_table(self) -> QWidget:
        """
        左半边：一张表，一行一个源文件。

        ⚠ 动作选项**只按 `item.options` 生成** —— "覆盖旧文件"不可用
        （`allows_replace` 为假，例如项目里本来就有两个同名 label）时，
        这一项**不能出现在下拉里**：看得见却选不了比看不到更让人恼火。
        """
        left = QWidget(self)
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.SPACE['xs'])

        self.table = QTableWidget(len(self._items), 4, left)
        self.table.setObjectName('table')
        self.table.setHorizontalHeaderLabels(
            ['源 .txt', '撞上了什么', '处理方式', '新 label'])
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.Stretch)
        head.setSectionResizeMode(2, QHeaderView.Interactive)
        head.setSectionResizeMode(3, QHeaderView.Interactive)
        self.table.setColumnWidth(2, 132)
        self.table.setColumnWidth(3, 190)
        self._style_table()

        for row, item in enumerate(self._items):
            source = QTableWidgetItem(item.source.name)
            source.setToolTip(str(item.source))
            source.setTextAlignment(Qt.AlignTop | Qt.AlignLeft)
            self.table.setItem(row, 0, source)

            conflict = QTableWidgetItem(item.conflict_text())
            conflict.setToolTip(item.conflict_text())
            conflict.setTextAlignment(Qt.AlignTop | Qt.AlignLeft)
            self.table.setItem(row, 1, conflict)

            combo = QComboBox()
            combo.setObjectName(f'action_{row}')
            for action in item.options:
                combo.addItem(action_text(action), action)
            default_index = combo.findData(item.default_action)
            combo.setCurrentIndex(default_index if default_index >= 0 else 0)
            combo.setToolTip('这一份要怎么处理')
            # 连信号必须在预选值之后：否则铺表时每行都会触发一次刷新，
            # 还没建完的控件会被回调碰到（第 0 行时右侧对比区还不存在）。
            combo.currentIndexChanged.connect(
                partial(self._on_action_changed, row))
            self.table.setCellWidget(row, 2, combo)
            self._combos.append(combo)

            edit = QLineEdit()
            edit.setObjectName(f'label_{row}')
            edit.setPlaceholderText(item.suggest_label(self._review.taken_labels))
            edit.setToolTip('「改个名字写入」用的新 label'
                            '（只能用字母/数字/下划线，不能数字开头）')
            edit.textChanged.connect(partial(self._on_label_changed, row))
            self.table.setCellWidget(row, 3, edit)
            self._label_edits.append(edit)

            self.table.setRowHeight(row, ROW_HEIGHT)

        self.table.currentCellChanged.connect(self._on_current_cell)
        lay.addWidget(self.table)
        return left

    def _build_compare(self) -> QWidget:
        """
        右半边：旧文件 vs 新产物的**按行对齐**对比。

        标题两行先说清"比的是哪份文件、新产物会落到哪"；`risk_text()` 的
        橙色警告只在"覆盖旧文件真有代价"时出现（工具生成的旧文件没有代价，
        不吓人）。
        """
        right = QWidget(self)
        lay = QVBoxLayout(right)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.SPACE['xs'])

        self.old_path_label = QLabel('旧文件：')
        self.old_path_label.setObjectName('old_path')
        self.old_path_label.setWordWrap(True)
        lay.addWidget(self.old_path_label)

        self.new_path_label = QLabel('新产物：')
        self.new_path_label.setObjectName('new_path')
        self.new_path_label.setWordWrap(True)
        lay.addWidget(self.new_path_label)

        self.risk_label = QLabel('')
        self.risk_label.setObjectName('risk')
        self.risk_label.setWordWrap(True)
        self.risk_label.setStyleSheet(f"color: {T.t('state.warning')};")
        self.risk_label.setVisible(False)
        lay.addWidget(self.risk_label)

        self.stats_label = QLabel('')
        self.stats_label.setObjectName('stats')
        self.stats_label.setProperty('role', 'caption')
        lay.addWidget(self.stats_label)

        views = QHBoxLayout()
        views.setSpacing(T.SPACE['sm'])
        self.old_view = self._make_view('old_view')
        self.new_view = self._make_view('new_view')
        views.addWidget(self.old_view, 1)
        views.addWidget(self.new_view, 1)
        lay.addLayout(views, 1)

        # 垂直滚动联动：左右是一份 diff 的两栏，滚一边另一边不跟 = 看着看着
        # 就错位了（行号对不上是最坏的结果）。
        self.old_view.verticalScrollBar().valueChanged.connect(
            self._scroll_from_old)
        self.new_view.verticalScrollBar().valueChanged.connect(
            self._scroll_from_new)
        return right

    @staticmethod
    def _make_view(name: str) -> QTextEdit:
        """只读、不换行、等宽字体的对比栏（`NoWrap` 保证"一行文本 = 一个块"，行数才可数）。"""
        view = QTextEdit()
        view.setObjectName(name)
        view.setReadOnly(True)
        view.setLineWrapMode(QTextEdit.NoWrap)
        view.setFont(QFont(T.FONTS['mono'][0], T.FONTS['mono'][1]))
        view.setMinimumWidth(220)
        return view

    def _build_buttons(self) -> QHBoxLayout:
        """底部：左边批量、右边"取消 / 按上面执行"（就地报错的红字在它上面）。"""
        bar = QHBoxLayout()
        bar.setSpacing(T.SPACE['sm'])
        self.btn_all_replace = self._make_button(
            '全部覆盖旧文件', 'btn_all_replace', self._all_replace)
        self.btn_all_rename = self._make_button(
            '全部改个名字', 'btn_all_rename', self._all_rename)
        self.btn_all_skip = self._make_button(
            '全部跳过', 'btn_all_skip', self._all_skip)
        bar.addWidget(self.btn_all_replace)
        bar.addWidget(self.btn_all_rename)
        bar.addWidget(self.btn_all_skip)
        bar.addStretch(1)
        self.btn_cancel = self._make_button(
            '取消（什么都不写）', 'btn_cancel', self.reject)
        self.btn_accept = self._make_button(
            '按上面执行»', 'btn_accept', self._on_accept)
        self.btn_accept.setProperty('variant', 'primary')
        self.btn_accept.setDefault(True)
        bar.addWidget(self.btn_cancel)
        bar.addWidget(self.btn_accept)
        return bar

    @staticmethod
    def _make_button(text: str, name: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(name)
        button.clicked.connect(slot)
        return button

    # ==================================================================
    #  表格外观 / 行高（长中文句子不能被切一半）
    # ==================================================================

    def _style_table(self):
        """
        表格的 QSS：**全部从 `tokens` 取色**（界面色值只许有一个来源）。

        为什么非得单独写：全局 QSS 只管了 `QListWidget`，表格用的是 Qt 默认
        调色板 —— 选中行是系统蓝 + 黑字（对比度不够），空白区域是灰底，
        和旁边白色的对比栏明显不是一套。这里把底色 / 选中色 / 表头补齐。
        """
        self.table.setStyleSheet(f"""
QTableWidget {{
    background: {T.t('bg.canvas')};
    border: 1px solid {T.t('border.default')};
    gridline-color: {T.t('border.default')};
}}
QTableWidget::item {{ padding: 3px 5px; }}
QTableWidget::item:selected {{
    background: {T.t('listbox.selectBg')};
    color: {T.t('listbox.selectFg')};
}}
QHeaderView::section {{
    background: {T.t('bg.surface')};
    color: {T.t('text.secondary')};
    border: none;
    border-bottom: 1px solid {T.t('border.default')};
    padding: 4px 6px;
}}
""")

    def showEvent(self, event):
        """窗口第一次显示、拿到真实列宽之后再算行高（构造时列宽还没定）。"""
        super().showEvent(event)
        self._schedule_fit()

    def resizeEvent(self, event):
        """拉大/缩小窗口 → 冲突说明的换行数会变，行高跟着重算。"""
        super().resizeEvent(event)
        self._schedule_fit()

    def _schedule_fit(self):
        """行高重算排到事件循环下一拍（此时布局已经把列宽算好了）。"""
        if self._fit_pending:
            return
        self._fit_pending = True
        QTimer.singleShot(0, self._fit_rows)

    def _fit_rows(self):
        """
        按**冲突说明实际换行后的高度**调整行高。

        ⚠ 不能用 `resizeRowsToContents()`：实测它不认 `setWordWrap(True)` 的
        换行，三行中文句子被算成一行（42px），比固定行高还糟。这里拿
        `fontMetrics` 按当前列宽自己算，`ROW_HEIGHT` 只当最小高度兜底。
        """
        self._fit_pending = False
        width = self.table.columnWidth(1) - 12      # 减掉 QSS 的左右内边距
        if width < 40:
            return
        metrics = self.table.fontMetrics()
        for row, item in enumerate(self._items):
            rect = metrics.boundingRect(0, 0, width, 10000, Qt.TextWordWrap,
                                        item.conflict_text())
            self.table.setRowHeight(row, max(ROW_HEIGHT,
                                             min(rect.height() + 10, 240)))

    # ==================================================================
    #  表格 ↔ 右侧对比区 的联动
    # ==================================================================

    def _on_current_cell(self, current_row: int, _current_col: int,
                         _prev_row: int, _prev_col: int):
        """选中某一行 → 右侧切到这一份（默认选第一行）。"""
        if current_row == self._current:
            return
        self._show_item(current_row)

    def _on_action_changed(self, row: int, _index: int = -1):
        """下拉变了：选中这一行（右侧跟着切），并刷新"新 label"输入框。"""
        if not self._batch_running and row != self.table.currentRow():
            self.table.setCurrentCell(row, 2)      # 会触发 currentCellChanged
        self._refresh_row(row)

    def _on_label_changed(self, row: int, _text: str):
        """新名字边打边更新"新产物落到哪"（标题里的路径要跟着变）。"""
        if row == self._current and \
                self._combos[row].currentData() == IR.ACT_RENAME:
            self._refresh_titles(row)

    def _select_row(self, row: int):
        """把表格当前行指到 row（报错时也跳到那一行，人要能立刻改）。"""
        if not (0 <= row < len(self._items)):
            return
        self.table.setCurrentCell(row, 2)
        self.table.selectRow(row)

    def _show_item(self, row: int):
        """右侧整块换成第 row 份：旧文件 / 新产物 / 风险 / 摘要 / 对比文本。"""
        if not (0 <= row < len(self._items)):
            return
        self._current = row
        item = self._items[row]
        self._refresh_titles(row)

        risk = item.risk_text()
        self.risk_label.setText(('⚠ ' + risk) if risk else '')
        self.risk_label.setVisible(bool(risk))

        rows, stats = item.diff()
        self.stats_label.setText(stats.summary())
        self._fill_diff(rows)

    def _refresh_titles(self, row: int):
        """标题两行：旧文件是哪个；新产物会落到哪（改名时跟着输入框变）。"""
        item = self._items[row]
        old_path = str(item.old_path) if item.old_path is not None \
            else '（没有旧文件）'
        self.old_path_label.setText(f'旧文件：{old_path}')
        self.old_path_label.setToolTip(old_path)

        if self._combos[row].currentData() == IR.ACT_RENAME:
            target = item.rename_target(self._label_edits[row].text().strip())
        elif self._combos[row].currentData() == IR.ACT_REPLACE and \
                item.replace_path is not None:
            target = item.replace_path
        else:
            target = item.target
        self.new_path_label.setText(f'新产物：{item.source.name} → {target}')
        self.new_path_label.setToolTip(str(target))

    def _refresh_row(self, row: int):
        """按当前动作决定"新 label"能不能填；切到改名时自动填建议名。"""
        item = self._items[row]
        edit = self._label_edits[row]
        renaming = self._combos[row].currentData() == IR.ACT_RENAME
        edit.setEnabled(renaming)
        if renaming and not edit.text().strip():
            # 自动填**没被占用**的建议名（人可以改）。已经手打过就不覆盖：
            # 改一半切去别的动作再切回来，输入不该凭空消失。
            edit.setText(item.suggest_label(self._review.taken_labels))
        if row == self._current:
            self._refresh_titles(row)

    # ==================================================================
    #  diff → 两个对齐的文本栏
    # ==================================================================

    def _fill_diff(self, rows):
        """
        把 `DiffRow` 列表铺进左右两栏。**两边行数必然一样多**：每一行都
        同时往两边各写一条（某一边没有这行就写空白栏位），行号沿用原文件。
        """
        width = max(3, len(str(max((max(r.old_no, r.new_no) for r in rows),
                                   default=0))))
        old_lines = []
        new_lines = []
        old_marks = {}
        new_marks = {}
        for index, row in enumerate(rows):
            old_lines.append(_gutter(row.old_no, row.old, width))
            new_lines.append(_gutter(row.new_no, row.new, width))
            if row.tag == 'chg':            # 改：两边都标黄
                old_marks[index] = 'chg'
                new_marks[index] = 'chg'
            elif row.tag == 'del':          # 删：只标旧的那边（红）
                old_marks[index] = 'del'
            elif row.tag == 'add':          # 增：只标新的那边（绿）
                new_marks[index] = 'add'
        self.old_view.setPlainText('\n'.join(old_lines))
        self.new_view.setPlainText('\n'.join(new_lines))
        self._mark_lines(self.old_view, old_marks)
        self._mark_lines(self.new_view, new_marks)

    @staticmethod
    def _mark_lines(view: QTextEdit, marks: dict):
        """
        给指定块铺底色。

        用 `ExtraSelection` 而不是 HTML：HTML 会把文本变成富文本，行号、
        空白、`<`/`&` 这些 RPY 里常见的字符都要转义，一旦漏转义，人看到的
        就不是真实产物了 —— 对比栏里"显示得对"比"好看"重要。
        """
        selections = []
        document = view.document()
        for index, kind in marks.items():
            block = document.findBlockByNumber(index)
            if not block.isValid():
                continue
            cursor = QTextCursor(block)
            cursor.select(QTextCursor.LineUnderCursor)
            fmt = QTextCharFormat()
            fmt.setBackground(_diff_colour(kind))
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = fmt
            selections.append(selection)
        view.setExtraSelections(selections)

    # ==================================================================
    #  两个滚动条联动
    # ==================================================================

    def _scroll_from_old(self, value: int):
        self._sync_scroll(self.new_view, value)

    def _scroll_from_new(self, value: int):
        self._sync_scroll(self.old_view, value)

    def _sync_scroll(self, target_view: QTextEdit, value: int):
        """把 value 搬到另一边；`_syncing` 防止两边来回触发成环。"""
        if self._syncing:
            return
        self._syncing = True
        target_view.verticalScrollBar().setValue(value)
        self._syncing = False

    # ==================================================================
    #  批量操作（"可以全部强制更换"）
    # ==================================================================

    def _set_action(self, row: int, action: str):
        """把第 row 行切到 action；这一行没有该选项就保持原选择。"""
        combo = self._combos[row]
        index = combo.findData(action)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _all_replace(self):
        """「全部覆盖旧文件」：**只动允许覆盖的行**（不允许的保持上一选择）。"""
        self._apply_batch(
            lambda row, item: IR.ACT_REPLACE if item.allows_replace else None)

    def _all_rename(self):
        """「全部改个名字」：每行填上没被占用的建议名（逐行还能改）。"""
        self._apply_batch(lambda row, item: IR.ACT_RENAME)

    def _all_skip(self):
        """「全部跳过」：每行都选跳过（**不动**现有文件，一份都不写）。"""
        self._apply_batch(lambda row, item: IR.ACT_SKIP)

    def _apply_batch(self, action_of):
        """
        批量按钮的公共流程：算出每行动作 → 逐行切（改名的顺带填建议名）。

        批量期间**不抢当前选中行**：否则点一次"全部改名"，右侧对比区会
        一路跳到最后一个改过的文件 —— 人正看着哪一份，就该还停在哪一份。
        """
        self._batch_running = True
        try:
            for row, item in enumerate(self._items):
                action = action_of(row, item)
                if action is None:
                    continue
                self._set_action(row, action)
                if action == IR.ACT_RENAME:
                    self._label_edits[row].setText(
                        item.suggest_label(self._review.taken_labels))
        finally:
            self._batch_running = False

    # ==================================================================
    #  校验 / 结论
    # ==================================================================

    def _on_accept(self):
        """
        点「按上面执行」：逐行校验，**有问题就地红字说明、不关窗口**。

        校验口径直接用 `review.rename_problem`（与落盘层同一套理由）；
        额外查"两行改成同一个新名字"—— 单看每一行都合法，但落盘时第二份
        会撞第一份，那种半成功最难跟用户交代。
        """
        self._ok()
        renamed = {}
        for row, item in enumerate(self._items):
            action = self._combos[row].currentData()
            if action == IR.ACT_REPLACE and not item.allows_replace:
                self._fail_row(row, '「覆盖旧文件」对这一份不可用，'
                                    '请改选「改个名字写入」或「这次跳过」')
                return
            if action != IR.ACT_RENAME:
                continue
            label = self._label_edits[row].text().strip()
            why = self._review.rename_problem(item, label)
            if not why and label in renamed:
                why = (f'`{label}` 和第 {renamed[label] + 1} 行改成的新名字一样，'
                       f'两份产物会撞名，换一个')
            if why:
                self._fail_row(row, why)
                return
            renamed[label] = row
        self.accept()

    def _fail_row(self, row: int, why: str):
        """红字说清"哪一行、为什么"，并把表格跳到那一行。"""
        self._select_row(row)
        name = self._items[row].source.name
        self._fail(f'第 {row + 1} 行（{name}）：{why}')

    def _fail(self, message: str):
        """就地报错（`_BaseDialog` 的同款做法：不弹二次框）。"""
        self.error.setText(message)
        self.error.setVisible(True)
        return False

    def _ok(self):
        self.error.setVisible(False)

    def choices(self) -> list:
        """
        当前选择 → `apply_decisions` 的入参：

            [{'source': 源文件路径, 'action': 'replace'|'rename'|'skip',
              'label': 新名字（只有 rename 才有，其余为 ''）}, …]

        每个 item 一条、顺序与表格一致。**这个函数不写盘** —— 只把人的
        决定交回调用方（会话层拿它去 `apply_decisions` + 门禁）。
        """
        picks = []
        for row, item in enumerate(self._items):
            action = self._combos[row].currentData()
            label = ''
            if action == IR.ACT_RENAME:
                label = self._label_edits[row].text().strip()
            picks.append({'source': str(item.source), 'action': action,
                          'label': label})
        return picks


__all__ = ['CollisionReviewDialog', 'DIFF_ALPHA', 'ROW_HEIGHT', 'action_text']
