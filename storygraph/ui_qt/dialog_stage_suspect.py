# -*- coding: utf-8 -*-
"""
导出前的两句问话：舞台内容看着不像语句 / show 的名字项目里找不到（`ui_qt`）
=======================================================================

用户 2026-10-29：

> 可以！但也要尽量检测一下！实在看着不像是代码的才加弹窗确认，询问要不要
> 补为备注！！！如果很明显是代码，那就不需要，原样放进去就行……这个判断器
> 要写好，宁错报，不要漏报

用户 2026-10-30（P0）：

> 不许静默写出 `show refusing`

判据（谁可疑、为什么）**一行都不在这里**：

  · 「舞台（高级）」里不像 Ren'Py 语句的行 → `storygraph.story.stage_suspect`；
  · 「立绘 / 舞台」里 show 的名字项目里找不到 → `storygraph.app.stage_identity_guard`；
  · 「立绘」里 show 的属性项目没声明 → `storygraph.app.sprite_attr_guard`
    （2026-11-05 加：`show ye base_hand_down …` 导出前再警告一次）。

本文件只做三件事：

  1. 把三类可疑行**摆出来**（各一张只读表：第几拍 / 原文 / 为什么）；
  2. 让人决定：勾掉不想动的，点「移到备注」；或者「原样导出」（= 我知道，
     就要这么写）；或者「取消导出」（= 我去改，一个字节都不写）；
  3. **不替人做决定，也不偷偷写**。

## 三类的处置为什么不一样

「舞台可疑行」的处置是**移动**（把备忘从代码列挪到备注列，内容一个字不丢），
所以有勾选框、有「移到备注」按钮。

「名字找不到」的处置**只能是改名字**（把 `show refusing` 改成 `show ling`）
或**确认照写**（也许那名字是别处定义的）。挪到备注等于把一条演出删掉 ——
那是**改剧情**，不是修名字。所以这一类没有勾选框、没有移动按钮，只有
「回卡片改」或「原样导出」。

「属性没声明」的处置**只能是改写法**（`base_hand_down` → `base_handdown`）
或**确认照写**（项目自定义写法）。同样没有勾选框、没有"修一下"按钮：
改错一个下划线就换了一张图，工具不替人做这个决定（用户 2026-11-05：
"只报，不乱改"）。别把两种处置混成一个按钮：那会让"我点了移到
备注"变成"我的立绘静默消失了"。

## 为什么不是 `QMessageBox.question`

要一次说清"哪几拍、原文是什么、为什么"—— 三四行文本塞进 `QMessageBox`
就糊成一团，而作者需要逐条看。更重要的是本仓库踩过"模态框套模态框把测试
挂死"的坑：这里**只开一个窗口**（两类问题并排摆），不再叠框。

## 为什么默认「原样导出」

挪走/改名都会动用户填的格子。工具不该把"改用户的表"当默认动作 ——
默认 = 不动，要改得人自己点。
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

        dlg = StageSuspectDialog(self, stage_result, identity=ident_result,
                                 attrs=attr_result)
        if dlg.exec() != QDialog.Accepted:
            return False                            # 取消 = 零写
        if dlg.decision == MOVE:
            move_to_comment(beats, dlg.selected())
        return True

    `identity` 是 `app.stage_identity_guard.ScanResult`，`attrs` 是
    `app.sprite_attr_guard.ScanResult`（或任何带 `.suspects` 的列表容器）。
    **只有**「舞台可疑行」那类能移动；身份问题和属性问题都只展示、
    不改任何东西（见模块 docstring 的"三类的处置为什么不一样"）。

    本类**不碰 Beat、不调用 `move_to_comment`**：它只收集"要不要挪、挪哪几条"。
    真正动手在调用方 —— 这样这一层可以脱离会话单测（`test_stage_suspect.py`）。
    """

    def __init__(self, parent, result, allow_move: bool = True, identity=None,
                 attrs=None):
        super().__init__(parent)
        self._init_state(result, allow_move, identity, attrs)
        root = self._build_layout()
        n_stage = len(result)
        self._build_stage_section(root, n_stage)
        if self._identity:
            self._build_identity_section(root)
        if self._attrs:
            self._build_attr_section(root)
        self._build_buttons(root)
        self._apply_mode(n_stage)

    # ------------------------------------------------------------------
    #  构造顺序：字段 → 标题/根布局 → 两类各自的控件 → 按钮 → 收尾
    #  （2026-11-01 拆分；**创建顺序是契约**，Qt 的控件顺序跟着它走）
    # ------------------------------------------------------------------

    def _init_state(self, result, allow_move, identity, attrs=None) -> None:
        """纯字段：四类数据源 + 这条路上的"能不能挪"。"""
        self._result = result
        self._rows = []                 # [(QTableWidgetItem 复选框项, Suspect)]
        #: 身份门禁的结果（没有 / 空 = 这一类不出现）。
        #:
        #: 现场取 `len()` 判空：`stage_identity_guard.ScanResult` 实现了
        #: `__len__`，但调用方也可能传 `None`（没有资源表 → 这次判不了）。
        self._identity = identity if (identity and len(identity)) else None
        #: 立绘属性门禁的结果（`app.sprite_attr_guard.ScanResult`；同上）。
        #:
        #: ⚠ **只展示，不提供"修一下"按钮**（用户 2026-11-05："只报，不乱改"）：
        #: 这类词改错一个下划线就换了一张图，默认动作必须是"人自己回卡片改"。
        self._attrs = attrs if (attrs and len(attrs)) else None
        self.decision = KEEP
        #: 这一条路能不能"移"。
        #:
        #: `export_script`（卡片页）→ True：内存里的 beats 就是真源，挪了直接编译；
        #: `export_from_table`（从 .xlsx 导出）→ False：那份表的真源是 Excel，
        #: 工具在导出前顺手改用户的工作簿风险更大（还有别的 sheet、格式、
        #: 公式）。那边只提示"第几拍、原文是什么"，请人回 Excel 自己挪。
        self._allow_move = bool(allow_move)

    def _build_layout(self):
        """窗标题 + 根布局，返回根布局（其余控件按类往里追加）。"""
        n_stage = len(self._result)
        parts = []
        if n_stage:
            parts.append('舞台内容看着不像语句')
        if self._identity:
            parts.append('show 的名字项目里找不到')
        if self._attrs:
            parts.append('show 的属性项目里没声明')
        title = '导出前检查 —— ' + '；'.join(parts) if parts else '导出前检查'
        self.setWindowTitle(title)
        theme.apply_to(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])
        return root

    def _build_stage_section(self, root, n_stage) -> None:
        """第一类：「舞台（高级）」里不像语句的行（可移动）。"""
        self.summary = QLabel(
            f'「舞台（高级）」里有 {n_stage} 处不像 Ren\'Py 语句。'
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

        self.table = QTableWidget(n_stage, 4, self)
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
        for row, suspect in enumerate(self._result.suspects):
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
        if any(s.multi for s in self._result.suspects):
            self.multi_note = QLabel(
                '有的格子里除了这一行还有别的语句：挪走只会拿走这一行，'
                '其余语句原地保留。')
            self.multi_note.setObjectName('multi_note')
            self.multi_note.setWordWrap(True)
            self.multi_note.setProperty('role', 'note')
            root.addWidget(self.multi_note)

    def _build_identity_section(self, root) -> None:
        """
        第二类：show 的名字项目里找不到（只展示，不能移）。

        这一类的证据来自 `assets.asset_identity`（只认字面量），处置只有
        "改名字"或"确认照写"—— 没有移动按钮（挪到备注 = 删掉一条演出）。
        """
        self.identity_summary = QLabel(
            f'另外，「立绘 / 舞台」里有 {len(self._identity)} 条 show '
            f'写的名字在项目里找不到。导出后游戏运行到这几拍会报'
            f'「找不到图像」—— 下面的表只列出来，工具不会替你改。')
        self.identity_summary.setObjectName('identity_summary')
        self.identity_summary.setWordWrap(True)
        root.addWidget(self.identity_summary)

        self.identity_hint = QLabel(
            '请在卡片里把「立绘」列（或「舞台」里的 show）改成能上舞台的'
            '名字：角色的立绘 tag，或项目 .rpy 里声明过的图像名。'
            '认不出 / 预览图这类名字**不能**直接 show —— 选对应的立绘。'
            '确认这些名字确实存在（比如别处有定义）也可以点「原样导出」。')
        self.identity_hint.setObjectName('identity_hint')
        self.identity_hint.setWordWrap(True)
        self.identity_hint.setProperty('role', 'note')
        root.addWidget(self.identity_hint)

        self.identity_table = QTableWidget(len(self._identity), 3, self)
        self.identity_table.setObjectName('identity_suspects')
        self.identity_table.setHorizontalHeaderLabels(
            ['拍', '写的名字', '为什么'])
        self.identity_table.verticalHeader().setVisible(False)
        self.identity_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.identity_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers)
        self.identity_table.setAlternatingRowColors(True)
        ih = self.identity_table.horizontalHeader()
        ih.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        ih.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        ih.setSectionResizeMode(2, QHeaderView.Stretch)
        for row, suspect in enumerate(self._identity.suspects):
            self.identity_table.setItem(
                row, 0, QTableWidgetItem(str(suspect.seq or '')))
            self.identity_table.setItem(
                row, 1, QTableWidgetItem(f'show {suspect.spec}'))
            self.identity_table.setItem(
                row, 2, QTableWidgetItem(str(suspect.reason)))
        self.identity_table.resizeColumnsToContents()
        root.addWidget(self.identity_table, 1)

    def _build_attr_section(self, root) -> None:
        """
        第三类：`show` 的属性项目没声明（只展示，不能移、也不能一键改）。

        用户 2026-11-05：

        > 只报，不乱改……导出时也要再警告一次，并且说明其可能导致无法运行。

        后果必须说准（引擎源码 `00layeredimage_ren.py`）：认不出的属性
        **不抛异常、只忽略**，所以这里写"通常还能跑，但画面不是你要的"；
        真正会起不来的是未知 tag / 语法错，那两类在别的小节里单独报。
        """
        self.attr_summary = QLabel(
            f'另外，「立绘」里有 {len(self._attrs)} 条 show 写了项目没声明的'
            f'属性。导出后最坏会让游戏起不来；最常见是引擎忽略这些词 —— '
            f'能跑，但这一层不会出现，画面不是你要的。下面只列出来，'
            f'工具不会替你改。')
        self.attr_summary.setObjectName('attr_summary')
        self.attr_summary.setWordWrap(True)
        root.addWidget(self.attr_summary)

        self.attr_hint = QLabel(
            '请在卡片里把属性改成项目声明过的写法（例如「base_handdown」）；'
            '确认这是项目自定义写法（项目里定义过的代号不算错），'
            '也可以点「原样导出」。')
        self.attr_hint.setObjectName('attr_hint')
        self.attr_hint.setWordWrap(True)
        self.attr_hint.setProperty('role', 'note')
        root.addWidget(self.attr_hint)

        self.attr_table = QTableWidget(len(self._attrs), 4, self)
        self.attr_table.setObjectName('attr_suspects')
        self.attr_table.setHorizontalHeaderLabels(
            ['拍', '原文', '为什么', '后果'])
        self.attr_table.verticalHeader().setVisible(False)
        self.attr_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.attr_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers)
        self.attr_table.setAlternatingRowColors(True)
        ah = self.attr_table.horizontalHeader()
        ah.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        ah.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        ah.setSectionResizeMode(2, QHeaderView.Stretch)
        ah.setSectionResizeMode(3, QHeaderView.Stretch)
        for row, suspect in enumerate(self._attrs.suspects):
            self.attr_table.setItem(
                row, 0, QTableWidgetItem(str(suspect.seq or '')))
            self.attr_table.setItem(
                row, 1, QTableWidgetItem(f'show {suspect.spec}'))
            self.attr_table.setItem(
                row, 2, QTableWidgetItem(str(suspect.reason)))
            self.attr_table.setItem(
                row, 3, QTableWidgetItem(str(suspect.consequence)))
        self.attr_table.resizeColumnsToContents()
        root.addWidget(self.attr_table, 1)

    def _build_buttons(self, root) -> None:
        """按钮：三类共用同一条"原样导出 / 取消"决定（属性类没有"修一下"）。"""
        bar = QHBoxLayout()
        bar.setSpacing(T.SPACE['sm'])
        self.btn_keep = QPushButton('原样导出')
        self.btn_keep.setObjectName('btn_keep')
        self.btn_keep.setToolTip(
            '一个字都不改，这些行照原文写进脚本（包括项目没声明的属性）')
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

    def _apply_mode(self, n_stage) -> None:
        """
        收尾：按钮文案、三处"这类没有就别显示"、窗口高度。

        三种降级各走一路，互不影响：没有舞台可疑行 → 第一类整块隐掉；
        从表导出 → 不给"移到备注"（不改用户的工作簿）；属性类永远没有
        "修一下"按钮（只报，不改）。
        """
        self._refresh_move_button()
        if not n_stage:
            # 只有身份 / 属性类：舞台那张表和"移到备注"都不该出现。
            self.summary.setVisible(False)
            self.hint.setVisible(False)
            self.table.setVisible(False)
            self.btn_move.setVisible(False)
            self.btn_cancel.setText('取消导出，去改「立绘」')
        if not self._allow_move:
            # 从表导出的那条路：不改用户的工作簿，只把"哪几拍"指出来。
            self.table.setColumnHidden(0, True)
            self.btn_move.setVisible(False)
            if n_stage:
                self.hint.setText(
                    '请在表格里把这些内容挪到「备注」列（那一列导出时会自动补 # 号），'
                    '或确认它们确实是语句后点「原样导出」。')
            if self._identity:
                self.identity_hint.setText(
                    '这些名字请在表格里改（或确认确实存在后点「原样导出」）—— '
                    '工具不会在这里改你的工作簿。')
            if self._attrs:
                self.attr_hint.setText(
                    '请在表格里把这些属性改成项目声明过的写法（或确认这是'
                    '项目自定义写法后点「原样导出」）—— 工具不会在这里改'
                    '你的工作簿。')
        # 每多一类问题，多给一点高度；没有的那类不会建控件，不算。
        sections = int(bool(n_stage)) + int(bool(self._identity)) \
            + int(bool(self._attrs))
        self.resize(860, {0: 420, 1: 460, 2: 600, 3: 720}.get(sections, 420))

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
