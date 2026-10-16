"""
「自定义动作」弹窗（**2026-10-13 整个重写**）
=============================================

旧文件被整个删掉重写（用户原话）：

> 这两个滚动条是何意味啊？？？还有你这个词组栏不也还是打勾下拉吗？全都再改！
> 我之前不是说了让你把这个子界面删了重写吗？？？不要再在原来的地方缝补了！
> 整个删了重写！如果你是给候选值分栏+改名用的！你这不应该叫词组！你换个名字！

## 这一版相对旧版改了什么（逐条对着用户的抱怨）

| 用户抱怨 | 旧版 | 这一版 |
|---|---|---|
| **"这两个滚动条是何意味"** | 弹窗里一个滚动区 + 字段列表自己又是 `QScrollArea` + 词组列表还是 → 同一屏两三条 | **一屏只有一个滚动区**（当前页那个）；字段列表与分栏列表都是**纯 QWidget** |
| **"词组栏不也还是打勾下拉吗"** | `QGroupBox(checkable)` 折起来的一块 | 分栏有自己的**一页（页签）**，不用勾选、不用折叠；标题栏始终看得见 |
| **"你这不应该叫词组"** | 界面写"词组（可选·高级）" | 一律叫 **「候选分栏」**：一栏 = 一组候选；`分栏标识` = 写进清单的键；`这一栏显示成` = 编剧看到的字 |
| **"选的一多就把后面那个变量挤出去"** | 卡内 6 列网格（最小宽 480 px）+ 方块不折行 | 卡内**单列堆叠** + 方块**折行**（最小宽 188 px，且不随数量涨） |
| **"窗口不能全屏"** | 没有最大化按钮 | 可最大化 + 右下角拖动柄 + 初始大小按屏幕算 |

## 页签布局（这就是"没有两条滚动条"的原因）

```
┌ 自定义动作 ────────────────────────────────────────────────────────────[□][─][✕]
│ [① 认识新动作] [② 项目已有的声明]         ← 页签：一屏只显示一件事
│ ┌──────────────────────────────────────────────────────────────────┐
│ │  （当前页的内容 —— **只有这一块**会滚）                            │
│ │  ①：发现列表 + 「已声明过的」只读说明                              │
│ │  ②：填一条声明 —— 动作名 / 中文名 / 说明 / 要填的东西 / 试切 / 预览  │
│ │      + 「候选分栏」入口（点开进第三页）                            │
│ │  ③：候选分栏 —— 一栏一张卡（标识 / 显示成 / 这一栏有哪些词）        │
│ └──────────────────────────────────────────────────────────────────┘
├──────────────────────────────────────────────────────────────────────┤
│  说明（可换行）                      [▶ 验证它能跑] [写进清单] [取消]   │ ← 固定，永不滚动
└──────────────────────────────────────────────────────────────────────┘
```

## 对外接口（判据与调用方照旧）

`.found` / `.declared` / `.mode` / `.builtin_name` / `.name` / `.label` /
`.docs` / `.block` / `.sample` / `.cut_view` / `.cut_try` / `.params` /
`.preview` / `._ok_btn` / `._verify_btn` / `._do_verify()` /
`_refresh_preview()` / `_refresh_cut()` / `_slot_problems()` / `_on_pick()` /
`result_declaration()` / `statement_mode()` / `set_verify_available()`。

⚠ 三个名字换了（旧名保留成**只读别名**，只在"以前叫什么"的意义上存在）：
  · `params` 仍是字段列表（`param_editor.FieldList`）；
  · `groups` → 现在叫 **`columns_editor`**（候选分栏列表），`groups` 是别名；
  · `write_groups` → 现在叫 **`write_columns`**，`write_groups` 是别名。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy,
                               QTabWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from storygraph.authoring.learn_statement import (build_declaration,
                                                  declaration_preview)
from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.flow import FlowRow
from storygraph.ui_qt.param_editor import FieldList
from storygraph.ui_qt.column_editor import ColumnList

#: 页签下标（`self.tabs`）
PAGE_FIND = 0
PAGE_DECLARE = 1
PAGE_COLUMNS = 2


class CustomStatementDialog(QDialog):
    """教工具认识一条项目自己的写法（发现 → 填 → 试切 → 写清单）。"""

    def __init__(self, parent=None, project_root=None, declares=None,
                 discovered=None, registry_report: str = '',
                 declared_groups=None, on_verify=None):
        super().__init__(parent)
        self.setWindowTitle('自定义动作 —— 教工具认识剧本里的新写法')
        theme.apply_to(self)
        self._root_path = project_root
        self._declares = list(declares or [])
        self._discovered = list(discovered or [])
        self._registry_report = registry_report or ''
        self.on_verify = on_verify

        # ⭐ 内容会长（格子多少、候选多少都随人填），所以窗口必须可缩放 + 可最大化；
        #   Qt 的 QDialog **默认没有最大化按钮**，必须显式给这个标志。
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowMinMaxButtonsHint
                            | Qt.WindowSystemMenuHint)
        self.setSizeGripEnabled(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])

        top = QLabel(
            '项目里自己的写法（swim_suit / mycue 这类）默认逐字保留，'
            '但工具不知道它是什么。在这里教它一条，它就变成「认识的语句」：'
            '参数会成为表格里的列、候选值会成为下拉。'
            '声明落在项目根的 storygraph.rules.json（可评审、可 diff）。')
        top.setWordWrap(True)
        root.addWidget(top)

        # ⭐⭐ **页签**：一屏只显示一件事，而且**只有当前页那个滚动区**
        #    （用户报的"这两个滚动条是何意味"就是旧版在同一屏摆了 2~3 个滚动区）。
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs, 1)

        self.tabs.addTab(self._build_find_page(), '① 认识新动作')
        self.tabs.addTab(self._build_declare_page(), '② 填一条声明')
        self.tabs.addTab(self._build_columns_page(), '③ 候选分栏')
        # 已有声明（`attr_groups`）→ 填进分栏页，让人看到"现在是怎么分的"
        self.columns_editor.load_columns(declared_groups or [])

        # —— 底部动作条（**在滚动区外**：滚多远都点得到）——
        bar = QHBoxLayout()
        bar.setSpacing(T.SPACE['sm'])
        self._verify_btn = QPushButton('▶ 验证它能跑')
        self._verify_btn.clicked.connect(self._do_verify)
        bar.addWidget(self._verify_btn)
        hint = QLabel(
            '真跑一次引擎：找出用到这个写法的章节 → 注入证据 → 跑 test → '
            '两把锁判定（引擎报 PASSED 且证据文件真的存在）。'
            '这里写声明只让「工具」认识它；要让「引擎」也认识，项目里还得有它的 '
            'renpy.register_statement（注意：init python 里注册赶不上解析期）。'
            '没验证成 ≠ 失败：我们会如实分成「通过 / 失败 / 没证明成」。')
        hint.setProperty('role', 'caption')
        hint.setWordWrap(True)
        bar.addWidget(hint, 1)
        bar.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._ok_btn = box.button(QDialogButtonBox.Ok)
        self._ok_btn.setText('写进 storygraph.rules.json')
        self._ok_btn.setProperty('variant', 'primary')
        # ⚠ 回车**不许**等于"确定"：确定 = 写盘。任何输入框里按一下回车
        #   都不该把清单写了（`ValueChips` 的回车是"加一个候选"）。
        self._ok_btn.setAutoDefault(False)
        box.button(QDialogButtonBox.Cancel).setAutoDefault(False)
        box.button(QDialogButtonBox.Cancel).setText('取消（什么都不改）')
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        bar.addWidget(box)
        root.addLayout(bar)

        # —— 收尾：初始尺寸按屏幕算；分栏表同步到字段卡 ——
        self.setMinimumSize(760, 480)
        self.resize(*self._fit_to_screen(1000, 680))
        for w in (self.name, self.label, self.docs, self.sample):
            w.installEventFilter(self)
        # ⚠ 没给回调就**灰化**（"能点就必须有反应"：点了没反应不如点不动 + 说清原因）
        self.set_verify_available(bool(on_verify),
                                  '这次没接上"跑引擎"的通道（例如只做界面/单测）')
        self._sync_columns_to_fields()
        self._on_mode_changed()
        self._refresh_preview()

    # ==================================================================
    #  页 1：发现
    # ==================================================================

    def _build_find_page(self) -> QWidget:
        # ⚠ 发现列表可能很长 → 这一页要能滚；但**只有这一条**（页签把别的页藏起来）
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        inner = QWidget()
        inner.setMinimumWidth(0)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                               T.SPACE['sm'], T.SPACE['sm'])
        lay.setSpacing(T.SPACE['xs'])
        head = QLabel('① 项目里还没声明的写法'
                      + (f'（发现 {len(self._discovered)} 个）'
                         if self._discovered else ''))
        head.setWordWrap(True)
        lay.addWidget(head)
        note = _hint('工具认识的写法（show / pause / with / window 这些内置的）'
                     '不会出现在这里 —— 它们本来就认识，不用教。')
        lay.addWidget(note)

        self.found = QTableWidget(len(self._discovered), 4)
        self.found.setHorizontalHeaderLabels(['写法', '出现', '带块', '原文样例'])
        self.found.verticalHeader().setVisible(False)
        self.found.setMinimumWidth(0)
        fh = self.found.horizontalHeader()
        for col in (0, 1, 2):
            fh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        fh.setSectionResizeMode(3, QHeaderView.Stretch)
        for i, rec in enumerate(self._discovered):
            self.found.setItem(i, 0, QTableWidgetItem(rec.get('head', '')))
            self.found.setItem(i, 1, QTableWidgetItem(str(rec.get('count', 0))))
            self.found.setItem(i, 2,
                               QTableWidgetItem('是' if rec.get('block') else ''))
            sample = (rec.get('samples') or [('', '')])[0]
            self.found.setItem(i, 3, QTableWidgetItem(str(sample[1])))
        self.found.itemSelectionChanged.connect(self._on_pick)
        lay.addWidget(self.found, 1)
        if not self._discovered:
            lay.addWidget(_hint(
                '没发现未声明的写法 —— 要么项目里没有自定义语句，'
                '要么它们已经在清单或 CDS 里声明过了（见下一页）。'))

        lay.addWidget(QLabel('已经声明过的（rules.json + 脚本里的 CDS）'))
        self.declared = QPlainTextEdit()
        self.declared.setReadOnly(True)
        self.declared.setPlainText(self._registry_report or '（还没有任何声明）')
        self.declared.setMinimumHeight(88)
        self.declared.setMaximumHeight(180)
        lay.addWidget(self.declared)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        return page

    # ==================================================================
    #  页 2：填声明
    # ==================================================================

    def _build_declare_page(self) -> QWidget:
        outer = QWidget()
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(0)

        # ⚠ 这一页**只有一个滚动区**（用户报的"两个滚动条"就是这里来的）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setMinimumWidth(0)
        self.declare_scroll = scroll
        body = QWidget()
        body.setMinimumWidth(0)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                               T.SPACE['sm'], T.SPACE['sm'])
        lay.setSpacing(T.SPACE['sm'])

        # —— A. 教它认识哪个动作 ——
        lay.addWidget(_section('A. 教它认识哪个动作'))
        mode_row = FlowRow(spacing=T.SPACE['xs'])
        mode_row.add(QLabel('你要教它认识的是'))
        self.mode = QComboBox()
        self.mode.addItem('我们自己新加的写法', 'custom')
        self.mode.addItem('引擎本来就有的写法（只给它加表格列）', 'builtin')
        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.add(self.mode)
        lay.addWidget(mode_row)
        self.mode_note = _hint('')
        lay.addWidget(self.mode_note)

        self.name = QLineEdit()
        self.name.setPlaceholderText('例：swim_suit（多个写法用空格分隔：mycue cue）')
        self.builtin_name = QComboBox()
        self.builtin_name.setEditable(True)
        self.builtin_name.setToolTip('这些是引擎自己就有的写法；挑一个就行')
        for head in self._engine_heads():
            self.builtin_name.addItem(head)
        self.builtin_name.currentTextChanged.connect(self._on_builtin_picked)
        self.builtin_name.setVisible(False)
        name_row = FlowRow(spacing=T.SPACE['xs'])
        name_row.add(QLabel('动作名'))
        name_row.add(self.name)
        name_row.add(self.builtin_name)
        lay.addWidget(name_row)

        label_row = FlowRow(spacing=T.SPACE['xs'])
        label_row.add(QLabel('中文名'))
        self.label = QLineEdit()
        self.label.setPlaceholderText('例：泳装切换（编剧在下拉里看到的就是它）')
        label_row.add(self.label)
        lay.addWidget(label_row)

        docs_row = FlowRow(spacing=T.SPACE['xs'])
        docs_row.add(QLabel('一句话说明'))
        self.docs = QLineEdit()
        self.docs.setPlaceholderText('例：切到泳装立绘（可留空）')
        docs_row.add(self.docs)
        lay.addWidget(docs_row)

        self.block = QCheckBox('它后面跟一个块（mycue "闪电": 这种，块体逐字保真）')
        lay.addWidget(self.block)

        # —— B. 这个动作要填什么 ——
        lay.addWidget(_section('B. 这个动作要填什么'))
        tools = FlowRow(spacing=T.SPACE['xs'])
        tools.add(_hint('一格 = 代码里的一个位置，从上到下就是顺序'))
        add = QPushButton('＋ 加一格')
        add.clicked.connect(lambda: self.params.add_field())
        rm = QPushButton('删掉最后一格')
        rm.clicked.connect(self._remove_param)
        tools.add(add)
        tools.add(rm)
        lay.addWidget(tools)

        # ⚠ **不是** QScrollArea（那会变成第二条滚动条）——纯竖向容器
        self.params = FieldList(on_change=self._refresh_preview,
                                sources_provider=self._choice_sources,
                                on_missing_change=self._refresh_preview)
        lay.addWidget(self.params)
        self.params.add_field()          # 一进来就有一格，省得人想"要先点加号吗"

        # —— C. 试切 + 预览 ——
        lay.addWidget(_section('C. 拿一行真实剧本试试'))
        cut_row = FlowRow(spacing=T.SPACE['xs'])
        cut_row.add(QLabel('粘一行'))
        self.sample = QLineEdit()
        self.sample.setPlaceholderText('例：show 小明 泳装 开心（从剧本里粘一行最准）')
        self.sample.setMinimumWidth(200)
        self.sample.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        cut_row.add(self.sample)
        self.cut_try = QPushButton('试切')
        self.cut_try.clicked.connect(self._refresh_cut)
        cut_row.add(self.cut_try)
        lay.addWidget(cut_row)
        self.cut_view = QLabel()
        self.cut_view.setWordWrap(True)
        self.cut_view.setProperty('role', 'caption')
        lay.addWidget(self.cut_view)

        lay.addWidget(_section('D. 这样写对吗（一改就跟着变）'))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(120)
        lay.addWidget(self.preview)

        # —— E. 候选分栏入口（不在这里折叠/打勾：它是独立的一页）——
        lay.addWidget(_section('E. 候选值要分栏吗'))
        cols_row = FlowRow(spacing=T.SPACE['xs'])
        cols_row.add(_hint('把下拉里的候选分成「服装」「神态」这样的栏 —— '
                           '不建栏就是平铺，一样能用'))
        go = QPushButton('管理候选分栏…')
        go.clicked.connect(lambda: self.tabs.setCurrentIndex(PAGE_COLUMNS))
        cols_row.add(go)
        lay.addWidget(cols_row)

        lay.addStretch(1)
        scroll.setWidget(body)
        ol.addWidget(scroll, 1)

        # 输入框里回车 = **焦点去下一格**（一路往下填）；
        # 试切那一格回车 = 直接试切。候选值那一格不在这里
        # （它的回车是"加一个候选"，见 `param_editor.ValueChips`）。
        for w in (self.name, self.label, self.docs):
            w.textChanged.connect(self._refresh_preview)
        self.block.toggled.connect(self._refresh_preview)
        # ⚠ 只接"试切"这一个落点：接两次再断开是自找麻烦（顺序错一次就炸）
        self.sample.textChanged.connect(self._refresh_cut)
        return outer

    # ==================================================================
    #  页 3：候选分栏
    # ==================================================================

    def _build_columns_page(self) -> QWidget:
        # ⚠ 分栏**可以有很多栏**，所以这一页也要能滚 —— 但**只有这一条**
        #   （页签天然把"别的页的滚动区"藏起来了：实测同屏可见滚动区 = 1）。
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        inner = QWidget()
        inner.setMinimumWidth(0)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                               T.SPACE['sm'], T.SPACE['sm'])
        lay.setSpacing(T.SPACE['sm'])
        head = QLabel('候选分栏 —— 把某一格的候选值分成「服装」「神态」这样的栏')
        head.setWordWrap(True)
        lay.addWidget(head)
        lay.addWidget(_hint(
            '什么时候要它：某一格的候选值很多（例如表情几十个），想让下拉里分栏显示。'
            '不建栏 = 完全平铺，一样能用。'
            '⚠ 立绘属性（show ye base_handdown happy）的分栏引擎自己就有，'
            '不需要在这里建。'))
        lay.addWidget(_hint(
            '一栏三件事：分栏标识（写进清单的键）/ 这一栏显示成（编剧看到的字）/ '
            '这一栏有哪些词（留空 = 兜底栏，没归到别的栏的词都进它）。'))
        self.columns_editor = ColumnList(on_change=self._sync_columns_to_fields)
        lay.addWidget(self.columns_editor)
        tools = FlowRow(spacing=T.SPACE['xs'])
        add = QPushButton('＋ 加一栏')
        add.clicked.connect(self._add_column)
        rm = QPushButton('－ 删最后一栏')
        rm.clicked.connect(self._remove_column)
        tools.add(add)
        tools.add(rm)
        self.write_columns = QCheckBox('把分栏一起写进清单')
        self.write_columns.setChecked(False)
        self.write_columns.toggled.connect(self._refresh_preview)
        tools.add(self.write_columns)
        lay.addWidget(tools)
        lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # ⚠ **旧名字保留成别名**（只读）：判据与别处的代码还在用 `groups` /
        #   `write_groups` / `groups_box` 这几个字。名字换了，但接线不断。
        self.groups = self.columns_editor
        self.write_groups = self.write_columns
        self.groups_box = self.tabs          # 旧版这里是"可勾选的折叠块"
        return page

    # ==================================================================
    #  行为
    # ==================================================================

    def eventFilter(self, obj, event):              # noqa: N802 - Qt 接口
        """
        ⚠ 输入框里回车 = **焦点去下一格**（一路往下填），而不是"什么都没发生"；
        `sample` 那一格回车 = **直接试切**（比跳走更贴意图）。
        底部按钮已 `setAutoDefault(False)`，所以回车**不会**触发写盘。
        """
        if (event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)):
            if obj is getattr(self, 'sample', None):
                self._refresh_cut()
            else:
                self.focusNextChild()
            event.accept()
            return True
        return super().eventFilter(obj, event)

    def _fit_to_screen(self, want_w: int, want_h: int) -> tuple:
        """期望大小 → **真能放下的大小**（不超出屏幕可用区，也不小于最小尺寸）。"""
        try:
            from PySide6.QtGui import QGuiApplication
            screen = self.screen() or QGuiApplication.primaryScreen()
            area = screen.availableGeometry() if screen is not None else None
            if area is not None:
                w = max(self.minimumWidth(), min(int(want_w), area.width() - 80))
                h = max(self.minimumHeight(), min(int(want_h), area.height() - 80))
                return int(w), int(h)
        except Exception as e:                      # noqa: BLE001 - 算不出就用期望值
            swallow('ui_qt.dialog_learn.fit_to_screen', e)
        return int(want_w), int(want_h)

    # ---- 发现 ----
    def _on_pick(self):
        """点左边发现的一条 → 把动作名填进来（带块也顺手勾上），并跳到填写页。"""
        r = self.found.currentRow()
        if not (0 <= r < len(self._discovered)):
            return
        rec = self._discovered[r]
        self.name.setText(str(rec.get('head') or ''))
        self.block.setChecked(bool(rec.get('block')))
        self.tabs.setCurrentIndex(PAGE_DECLARE)
        self._refresh_preview()

    # ---- 模式 ----
    def statement_mode(self) -> str:
        """`builtin`（给引擎本来就有的写法加列）/ `custom`（新写法）。"""
        getter = getattr(self, 'mode', None)
        if getter is None:
            return 'custom'
        return str(getter.currentData() or 'custom')

    def _on_mode_changed(self, *_a):
        """切换"引擎自带 / 自己写的"：**把该说的话当场说清**。"""
        builtin = self.statement_mode() == 'builtin'
        self.builtin_name.setVisible(builtin)
        self.name.setVisible(not builtin)
        if builtin:
            self.mode_note.setText(
                '挑一个引擎本来就有的写法（如 show）。放心：这里写的声明'
                '只让「工具」多几列、多几个下拉，不会动引擎。'
                '⚠ 真正会替换引擎的，是项目里的 renpy.register_statement —— '
                '那是另一件事，工具会拿最高级警告报出来。')
            if self.builtin_name.currentText():
                self.name.setText(self.builtin_name.currentText())
        else:
            self.mode_note.setText(
                '我们自己加的写法（如 swim_suit）。注意：这样只让「工具」认识它；'
                '要让「引擎」也认识，项目里还得有一段 renpy.register_statement。')

    def _on_builtin_picked(self, text: str):
        if self.statement_mode() == 'builtin':
            self.name.setText(str(text or ''))
            self._refresh_preview()

    def _engine_heads(self) -> list:
        """引擎自己就有的写法 —— **优先问引擎**，问不到退回词表（不硬编清单）。"""
        heads = []
        try:
            from storygraph.core import engine_facts as EF
            path = EF.facts_path(self._root_path) if self._root_path else None
            if path is not None and path.is_file():
                facts = EF.loads(path.read_text(encoding='utf-8'))
                heads = sorted(EF.statement_names(facts))
        except Exception as e:                      # noqa: BLE001 - 没事实也能用
            swallow('ui_qt.dialog_learn.engine_heads', e)
        if not heads:
            from storygraph.core.vocabulary import RENPY_KNOWN_HEADS
            heads = sorted(RENPY_KNOWN_HEADS)
        return heads[:400]

    # ---- 候选来源 ----
    def _choice_sources(self) -> list:
        """「⚡ 从项目里拿」的来源清单（纯函数在 `authoring.learn_statement`）。"""
        try:
            from storygraph.authoring.learn_statement import choice_sources
            return choice_sources(self._root_path, self.name.text().strip())
        except Exception as e:                      # noqa: BLE001 - 取不到就空
            swallow('ui_qt.dialog_learn.choice_sources', e)
            return []

    # ---- 分栏 ----
    def _add_column(self):
        self.columns_editor.add_column()

    def _remove_column(self):
        self.columns_editor.remove_current()

    def _sync_columns_to_fields(self, *_a):
        """
        分栏表 → 字段卡上的「分栏：xxx」标注（**一改就跟着变**）。

        字段卡上的标注是**自动认**的（`FieldList.match_column`）——
        用户明确否掉了"每格一个下拉"（"就在原地，就在旁边标注"）。
        """
        params = getattr(self, 'params', None)
        editor = getattr(self, 'columns_editor', None)
        if params is None or editor is None:
            return
        try:
            rows = [r for r in editor.rows() if str(r.get('key') or '').strip()]
            params.set_columns([(str(r.get('key') or '').strip(),
                                 str(r.get('shown') or '').strip(),
                                 [o for o in str(r.get('values') or '')
                                  .replace('，', ',').split(',') if o.strip()])
                                for r in rows])
        except Exception as e:                      # noqa: BLE001 - 不许炸窗口
            swallow('ui_qt.dialog_learn.sync_columns', e)

    # 旧名保留（判据与旧调用点还在用 `_sync_groups_to_slots`）
    def _sync_groups_to_slots(self, *_a):
        return self._sync_columns_to_fields(*_a)

    def built_groups(self) -> list:
        """分栏表 → 后端参数（`learn_statement.build_group`）。"""
        from storygraph.authoring.learn_statement import build_group
        return [build_group(row['key'], row['shown'], row['values'])
                for row in self.columns_editor.rows()
                if str(row.get('key') or '').strip()]

    def groups_to_write(self) -> list:
        """该写进清单的分栏（没勾"一起写"就返回空）。"""
        wc = getattr(self, 'write_columns', None)
        if wc is None or not wc.isChecked():
            return []
        return [g for g in self.built_groups() if g.get('ok')]

    # ---- 表单 → 声明 ----
    def built(self) -> dict:
        """当前表单 → 后端参数（纯函数；判据不在这里）。"""
        block = True if self.block.isChecked() else None
        return build_declaration(self.name.text(), label=self.label.text(),
                                 docs=self.docs.text(), block=block,
                                 params=self.params.rows(),
                                 pattern=self.params.slots())

    def result_declaration(self) -> dict:
        """按下去之后的最终参数（调用方拿去 `declare_statement`）。"""
        return self.built()

    def _remove_param(self):
        self.params.remove_current()
        self._refresh_preview()

    # ---- 试切 / 预览 ----
    def _refresh_cut(self, *_a):
        """
        **试切**：把粘进来的那一行按当前格子切开，显示每格吃到什么。

        ⚠ 先报毛病，再谈切不切得上：某格选了"从这些里选"却没填候选值、
        或者一格都没挂分栏 —— 这时试切**可能照样成功**，于是人不知道为什么不好使。
        """
        view = getattr(self, 'cut_view', None)
        sample = getattr(self, 'sample', None)
        if view is None or sample is None:
            return
        line = sample.text().strip()
        problems = self._slot_problems()
        if not line:
            view.setText('粘一行真实剧本，点「试切」—— 就能看出你的格子切得对不对。'
                         + (('\n' + problems) if problems else ''))
            return
        # ⚠⚠ **允许粘整行**（2026-10-13 实测修，用户现场）：
        #   格子里描述的是"**动作名后面**的东西"，而人从剧本里复制的一定是整行
        #   （`show lan swim_suit happy`）——头部那个动作名不在格子里。
        #   不把它摘掉，试切就永远"多出来 1 个词 → 切不上"，
        #   而界面还只会说"检查顺序、关键字写法"（人怎么查都查不出来）。
        #   ⚠ 只在**开头整词等于动作名**时摘一个词，绝不猜别的。
        head = self.name.text().strip()
        if head and line.split() and line.split()[0] == head:
            line = ' '.join(line.split()[1:])
            if not line:
                view.setText('只在行首看到了动作名「' + head + '」—— '
                             '后面还没写东西，先把那一行粘全。'
                             + (('\n' + problems) if problems else ''))
                return
        try:
            from storygraph.authoring import pattern as P
            groups = {g.get('name'): {'label': getattr(g, 'label', ''),
                                      'options': list(getattr(g, 'options', []) or [])}
                      for g in (self.built_groups() if hasattr(self, 'columns_editor')
                                else [])
                      if isinstance(g, dict) and g.get('name')}
            got = P.match_line(self.params.slots(), line, groups=groups)
        except Exception as e:                      # noqa: BLE001 - 试切不许炸窗口
            swallow('ui_qt.dialog_learn.refresh_cut', e)
            view.setText(f'试切没做成：{type(e).__name__}: {e}')
            return
        if not got.get('ok'):
            # ⚠ 光说"检查顺序"等于让人瞎猜（用户实测："为什么切不上呢？"）——
            #   把**具体是哪个词没人认领 / 第几格没吃到东西**念出来。
            view.setText('⚠ 切不上：' + self._why_not(got)
                         + (('\n' + problems) if problems else ''))
            return
        cells = ' ｜ '.join(f'{c["text"]}（{c["column"] or "固定"}）'
                            for c in got['cells'])
        view.setText('切出来是这样：' + cells
                     + f'　—　共 {len(got["cells"])} 格'
                     + (('\n' + problems) if problems else ''))

    def _why_not(self, got: dict) -> str:
        """
        "切不上"的**具体原因**（哪个词没人认领 / 第几格没吃到），而不是一句万能话。

        为什么必须说具体（2026-10-13 用户实测）：
        > 为什么切不上呢？

        旧文案只会说"检查顺序、关键字写法" —— 人拿着这句话**怎么查都查不出来**。
        现在：剩哪些词 → 直接念出来，还顺手比一下就差在哪。
        """
        slots = self.params.slots() if getattr(self, 'params', None) else []
        leftover = [str(w) for w in (got.get('leftover') or []) if str(w)]
        missing = list(got.get('missing') or [])
        parts = []
        if leftover:
            parts.append('这些词没人认领：' + '、'.join(leftover[:6])
                         + ('…' if len(leftover) > 6 else ''))
        if missing:
            names = []
            for i in missing:
                try:
                    s = slots[int(i)]
                    names.append(str(s.get('column') or s.get('text') or f'第{i + 1}格'))
                except Exception:               # noqa: BLE001 - 认不出就说第几格
                    names.append(f'第{int(i) + 1}格')
            parts.append('这些格子没吃到东西：' + '、'.join(names[:6]))
        if not parts:
            parts.append(str(got.get('reason') or '顺序或关键字不对'))
        # ⚠ 兜底也要说人话：`match_line` 有时只在 `reason` 里说细节（例如
        #   "第 2 格要的关键字是 `at`，这里是 `swim_suit`"）——那种句子比
        #   我们上面的概述更准，一起带上。
        reason = str(got.get('reason') or '')
        if reason and parts[0] != reason:
            parts.append(reason)
        return '；'.join(parts)

    def _slot_problems(self) -> str:
        """格子本身的毛病汇总成人话（`pattern.problems_of` 的界面接线）。"""
        params = getattr(self, 'params', None)
        if params is None:
            return ''
        try:
            from storygraph.authoring import pattern as P
            # ⚠ 直接交 `slots()` —— 它才是"界面 → 格子"的**唯一**形状，
            #   少抄一个键就会让某条检查悄悄失效（实测：`limit` 漏了，
            #   于是"勾了限死却没给候选"这条毛病在界面上永远不报）。
            items = list(P.problems_of(params.slots()) or [])
        except Exception as e:                      # noqa: BLE001 - 汇总不许炸窗口
            swallow('ui_qt.dialog_learn.slot_problems', e)
            return ''
        if not items:
            return ''
        return '这几点也顺手看看：' + '；'.join(str(x) for x in items[:5])

    def missing_name_cards(self) -> list:
        """**会被后端丢掉的格子**（没列名）—— 一个都不许静默丢。"""
        params = getattr(self, 'params', None)
        fn = getattr(params, 'missing_name_cards', None)
        if fn is None:
            return []
        try:
            return list(fn() or [])
        except Exception as e:                      # noqa: BLE001 - 查询不该炸窗口
            swallow('ui_qt.dialog_learn.missingNameCards', e)
            return []

    def _missing_name_notice(self) -> str:
        """
        把"哪几格没名字"写成给人看的一句话（放在预览**最上面**）。

        用户 2026-10-16 现场：三格都倒好候选了，只有一格填了名字 →
        另外两格被静默丢掉，而预览底部那句提醒被长文本淹掉了，人只看到
        "参数：无（只逐字透传）"，当场问"你这个到底怎么判断的？"。
        """
        cards = self.missing_name_cards()
        blanks = self.blank_cards()
        if not cards and not blanks:
            return ''
        lines = []
        if cards:
            lines.append(f'⚠ 有 {len(cards)} 格还没起名字 —— 按确定也不会写进去，'
                         f'里面的候选会一起丢掉：')
            for i, card in enumerate(cards, 1):
                hint = str(getattr(card, 'missing_name_hint', lambda: '')() or '')
                lines.append(f'  · 第 {i} 格'
                             + (f'（建议用「{hint}」）' if hint else ''))
            lines.append('  在「这一列叫什么（编剧看到的）」里填一个名字，'
                         '例如「角色」「服装」「神态」—— 名字只是表格上的列名，'
                         '不用管代码里那串字。')
        if blanks:
            lines.append(f'（另外 {len(blanks)} 格还是空的，不会写进去 —— '
                         f'要留空就随它，要加就填上。）')
        return '\n'.join(lines)

    def blank_cards(self) -> list:
        """还没填内容的格子（不多嘴拦人，但要让他在预览里看得见）。"""
        params = getattr(self, 'params', None)
        cards = list(getattr(params, '_cards', None) or ())
        out = []
        for card in cards:
            fn = getattr(card, 'has_content', None)
            if fn is not None and not fn():
                out.append(card)
        return out

    def _refresh_preview(self, *_a):
        built = self.built()
        text = declaration_preview(built, path=str(self._root_path or ''))
        notice = self._missing_name_notice()
        if notice:
            text = notice + '\n\n' + text
        wc = getattr(self, 'write_columns', None)
        if wc is not None and wc.isChecked():
            from storygraph.authoring.learn_statement import group_preview
            text += '\n\n' + group_preview(self.built_groups())
        preview = getattr(self, 'preview', None)
        if preview is not None:
            preview.setPlainText(text)
        self._refresh_cut()
        btn = getattr(self, '_ok_btn', None)
        if btn is not None:
            ok = bool(built.get('ok')) or bool(self.groups_to_write())
            # ⚠ 缺列名的格子会被后端丢掉 —— 那是**静默的数据丢失**，
            #   所以这里必须挡住确定键（人补上名字，或者把这一格删掉）。
            if self.missing_name_cards():
                ok = False
            btn.setEnabled(ok)

    # ---- 验证 ----
    def set_verify_available(self, available: bool, reason: str = ''):
        """**能点就必须有反应**：不可用要灰化 + 写清为什么。"""
        self._verify_btn.setEnabled(bool(available))
        try:
            self._verify_btn.setToolTip(
                '点一下真跑一次引擎' if available
                else (reason or '这里暂时没法验证（需要 SDK 与真项目）'))
        except Exception as e:                      # noqa: BLE001 - 提示设不上不该炸
            swallow('ui_qt.dialog_learn.set_verify_available.tooltip', e)

    def _do_verify(self):
        """「▶ 验证它能跑」：把当前填的动作名交给注入的回调（跑引擎是它的事）。"""
        fn = self.on_verify
        if fn is None:
            return
        head = self.name.text().strip()
        if not head:
            self.preview.setPlainText('先填"动作名"，再验证 —— '
                                      '验证要拿它去章节里找用到的地方。')
            return
        self._verify_btn.setEnabled(False)
        self._verify_btn.setText('⏳ 正在跑引擎…')
        self.preview.setPlainText(f'正在验证「{head}」：找出用到它的章节 → '
                                  f'注入证据 → 跑 `test` → 两把锁判定…\n'
                                  f'（要几秒到几十秒，请稍等）')
        QApplication.processEvents()
        try:
            text = fn(head)
        except Exception as e:                      # noqa: BLE001 - 验证不该炸窗口
            text = f'验证没做成：{type(e).__name__}: {e}'
        finally:
            self._verify_btn.setText('▶ 验证它能跑')
            self.set_verify_available(bool(self.on_verify))
        self.preview.setPlainText(str(text or ''))


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setProperty('role', 'caption')
    return lab


def _section(title: str) -> QLabel:
    lab = QLabel(title)
    lab.setProperty('role', 'section')
    lab.setWordWrap(True)
    return lab


__all__ = ['CustomStatementDialog', 'PAGE_FIND', 'PAGE_DECLARE', 'PAGE_COLUMNS']
