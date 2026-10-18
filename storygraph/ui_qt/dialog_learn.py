"""
「自定义语句」弹窗（**2026-10-18 第六版：信息架构重做**）
==========================================================

前五版都是"在同一个四页签壳子里换皮"（见 `docs/开发日志.md` 与
`docs/archive/设计_自定义动作界面_v3_重做_2026-10-12.md`）。这一版**不再改皮**，
把"人到底在干什么"重新排了一遍：

> 用户 2026-10-18："运用UI设计和用户体验的角度重构一下自定义模块的UI界面，
> 去除令人疑惑的表述，保证策划/程序逻辑清晰明了，操作方便，层次合理……
> 可直接进行前端重构实现，不再旧有基础上改"

## 一、旧版到底乱在哪（逐条对着截图看）

| 旧版 | 问题 | 这一版 |
|---|---|---|
| 四个编号页签「① 认识新动作 ② 填一条声明 ③ 候选分栏 ④ 已有声明（查看 / 删除）」 | 编号让人以为是**四个步骤**，其实 ① 和 ② 是同一件事的两半（选词 → 描述），③ 是 ② 的一个属性，④ 是管理。用户点 ① 选一行，界面**自己跳**到 ② —— 编号更假了 | **两页**：一页"有哪些写法 / 已经认识哪些"（列表），一页"编这条语句"（下钻）。列表 → 详情，带「← 返回列表」 |
| 同一个东西四个名字：动作 / 写法 / 语句 / 模块 | 标题写"模块"，页签写"动作"，正文写"写法" | 只有一个名字：**语句**。首词叫**开头词**，其余叫**格** |
| "清单 / 声明 / rules.json / 写盘" 混用；主按钮直接写「写进 storygraph.rules.json」 | 把文件名塞进主按钮 | 界面只说**项目清单**；文件名只出现在「项目清单」那一页，以及底部一行小字 |
| 底部常驻一大段引擎内部话（"两把锁判定" / "init python 里注册赶不上解析期"） | 永远占着最显眼的位置，策划读不懂、程序嫌浅 | 挪进「使用说明」和验证结果里；底部只留**当前这条语句的状态/毛病** |
| 候选分栏是第三页，却又要一个勾选框决定"要不要一起写" | 两种保存语义并存 | 分栏在自己的页里有自己的**保存候选分栏**按钮；语句的保存**永不碰分栏** |
| 保存 = 关窗（`exec() == Accepted` 之后由窗口层写盘） | 保存失败/成功都看不见；想连着教三条要开三次窗 | 保存**写在弹窗里**（`_save_statement`）：成功→回列表、列表当场多出这条；失败→当场说原因，不关窗 |

## 二、术语表（界面上一律用右列，代码注释里保留左列）

| 代码 | 界面 | 例子 |
|---|---|---|
| statement | **语句** | `swim_suit 小明 泳装` |
| head | **开头词** | `swim_suit` |
| slot / param | **格** | 第 2 格 = 服装 |
| column | **列名**（"表格里这一列叫什么"） | 角色 / 服装 / 神态 |
| choices | **候选值** | 泳装、校服 |
| attr_groups | **候选分栏**（键名 / 显示名 / 候选值） | `outfit` → 服装 |
| manifest | **项目清单** | 项目根的 `storygraph.rules.json` |
| block | **代码块** | `mycue "闪电":` 后面缩进的那几行 |

## 三、界面结构

```
┌ 自定义语句 ─────────────────────────────────────────── [?] [□][─][✕]
│ 教工具认识剧本里你自己加的写法：认识之后，它们会变成表格里的列和下拉项。
├──────────────────────────────────────────────────────────────────────┤
│  [← 返回列表]  语句「swim_suit」        ← 只有编辑页才有返回
│                                                                      │
│  1  它以哪个词开头                                                    │
│     [ swim_suit ]   （英文名，剧本里原样出现的那个词）                 │
│     中文名 [ 泳装切换 ]  一句话说明 [ …… ]   ☐ 后面跟一个代码块        │
│  2  后面跟什么                                                       │
│     一格 = 剧本里的一个位置，从上到下就是顺序。                        │
│     ┌ 第 1 格 ────────────────────────────── ↑ ↓ 删掉 ┐              │
│     └──────────────────────────────────────────────────┘              │
│     [＋ 加一格]                                                       │
│  3  拿一行剧本试试                                                    │
│     [ show lan happy ] [切一下] [用引擎验证]                          │
│     → 切出来是这样：……                                               │
│     ▸ 会写进项目清单的内容（点开看）                                   │
├──────────────────────────────────────────────────────────────────────┤
│  · 状态/毛病这一行（会变红/黄）        [删除这条]  [关闭]  [保存语句]  │
└──────────────────────────────────────────────────────────────────────┘
```

## 四、对外接口（判据与调用方）

**保留**（判据压着，含义不变）：
`.found` / `.declared` / `.name` / `.label` / `.docs` / `.block` / `.sample` /
`.cut_view` / `.cut_try` / `.params` / `.preview` / `._ok_btn` /
`._verify_btn` / `._do_verify()` / `set_verify_available()` /
`_refresh_preview()` / `_refresh_cut()` / `_slot_problems()` /
`missing_name_cards()` / `blank_cards()` / `result_declaration()` / `built()` /
`built_groups()` / `group_problems()` / `columns_editor` / `groups` /
`manage_view` / `manage_list` / `_manage_del` / `_manage_clear_groups` /
`_manage_reset` / `_manage_status` / `_delete_selected_statement()` /
`_clear_groups()` / `_reset_all()` / `_save_columns_now()` /
`changed` / `statement_mode()` / `_on_pick()` / `_group_problems`（见方法）。

**新增**：`pages`（页面栈）、`saved`（这次弹窗里真正写下去的记录）、
`_save_statement()`、`_new_statement()`、`_load_statement()`、`_go()`。

**去掉**（旧信息架构的产物，判据一起改了）：
`tabs` / `PAGE_*` 四个页签常量 / `mode` 与 `builtin_name` 那对二选一下拉 /
`write_columns` 勾选框（分栏改由它自己那页的按钮保存）/ `groups_box`。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QCompleter, QDialog, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy,
                               QStackedWidget, QTableWidget, QTableWidgetItem,
                               QToolButton, QVBoxLayout, QWidget)

from storygraph.authoring.learn_statement import (build_declaration,
                                                  declaration_preview)
from storygraph.core.safeguard import swallow
from storygraph.ui_qt import theme
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt.column_editor import ColumnList
from storygraph.ui_qt.flow import FlowRow
from storygraph.ui_qt.param_editor import FieldList

#: 页面栈下标（`self.pages`）
PAGE_LIST = 0       # 有哪些写法 / 已经认识哪些
PAGE_FORM = 1       # 编这一条语句
PAGE_GROUPS = 2     # 候选分栏
PAGE_MANIFEST = 3   # 项目清单（只读 + 删除/重置）


class CustomStatementDialog(QDialog):
    """教工具认识一条剧本自己的写法（列表 → 编辑 → 保存即生效）。"""

    def __init__(self, parent=None, project_root=None, declares=None,
                 discovered=None, registry_report: str = '',
                 declared_groups=None, on_verify=None):
        super().__init__(parent)
        self.setWindowTitle('自定义语句')
        theme.apply_to(self)
        self._root_path = project_root
        self._discovered = list(discovered or [])
        self._registry_report = registry_report or ''
        self.on_verify = on_verify
        #: 用户在弹窗里改过清单没有（调用方据此刷新表格）
        self.changed = False
        #: 这次会话里真正写下去的东西（调用方拿去写日志）
        self.saved: list = []

        # 内容会长（格子多少、候选多少都随人填），所以窗口必须可缩放 + 可最大化；
        # Qt 的 QDialog 默认没有最大化按钮，必须显式给这个标志。
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowMinMaxButtonsHint
                            | Qt.WindowSystemMenuHint)
        self.setSizeGripEnabled(True)

        # ---- 运行时状态（**先立好**：构建过程中回调会读它们）----
        self._declared: list = []      # 项目清单里的语句
        self._host: list = []          # 脚本里 renpy.register_statement 的
        self._pending: list = []       # 剧本里见过、但还没声明的
        self._editing = None           # 正在编辑的那条（None = 新语句）
        self._loading = False          # 正在往表单里灌数据（别当成"人改了"）
        self._baseline = ''            # 打开表单时的样子（判"改过没有"）
        #: 角色表读一次就存这儿（`_roles_cached`）：读它要读整张资产表
        self._roles_cache = None
        #: 载入声明时被换成"剧本真源"的格子（`_refresh_role_note` 据此出提示）
        self._role_fixes: list = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        # ⚠ 页的构建顺序 = `PAGE_*` 的顺序；`pages.addWidget` 的下标就是它
        self.pages.addWidget(self._build_list_page())
        self.pages.addWidget(self._build_form_page())
        self.pages.addWidget(self._build_groups_page())
        self.pages.addWidget(self._build_manifest_page())
        # 切页 = 换一套动作（保存/删除/保存分栏各自只在对应页出现）
        self.pages.currentChanged.connect(lambda *_: self._sync_action_bar())

        root.addWidget(self._build_action_bar())

        # ---- 收尾 ----
        self.setMinimumSize(780, 500)
        self.resize(*self._fit_to_screen(1040, 720))
        for w in (self.name, self.label, self.docs, self.sample):
            w.installEventFilter(self)
        self.columns_editor.load_columns(declared_groups or [])
        # 候选分栏 → 格子上的标注（一改就跟着变）
        self._sync_columns_to_fields(notify=False)
        # 没给回调就灰化（"能点就必须有反应"：点了没反应不如点不动 + 说清原因）
        self.set_verify_available(bool(on_verify),
                                  '这次没接上"跑引擎"的通道（例如只做界面/单测）')
        self._reload_registry()
        self._new_statement()
        self._go(PAGE_LIST)

    # ==================================================================
    #  外壳：标题区 / 动作条
    # ==================================================================

    def _build_header(self) -> QWidget:
        head = QFrame()
        head.setProperty('role', 'dialogHeader')
        lay = QHBoxLayout(head)
        lay.setContentsMargins(T.SPACE['lg'], T.SPACE['md'],
                               T.SPACE['lg'], T.SPACE['md'])
        lay.setSpacing(T.SPACE['sm'])

        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel('自定义语句')
        title.setProperty('role', 'panelTitle')
        text.addWidget(title)
        sub = _hint('教工具认识剧本里你自己加的写法：认识之后，它们会变成表格里的列和下拉项。'
                    '改动会写进项目根的 storygraph.rules.json（每次保存都会先备份，可以回退）。')
        text.addWidget(sub)
        lay.addLayout(text, 1)

        help_btn = QPushButton('使用说明')
        help_btn.setProperty('role', 'ghost')
        help_btn.setToolTip('术语表 + 四步操作 + 它到底会改哪个文件')
        help_btn.clicked.connect(self._show_help)
        lay.addWidget(help_btn)
        return head

    def _build_action_bar(self) -> QWidget:
        """
        底部动作条：**在滚动区外**（滚多远都点得到）。

        左边那行字只回答一个问题：**现在按保存会发生什么（或者为什么按不了）**。
        它以前是一段永远不变的引擎内部说明 —— 那是"帮助"，不是"状态"。
        """
        bar = QFrame()
        bar.setProperty('role', 'actionBar')
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(T.SPACE['lg'], T.SPACE['sm'],
                               T.SPACE['lg'], T.SPACE['sm'])
        lay.setSpacing(T.SPACE['sm'])

        self._status = QLabel('')
        self._status.setWordWrap(True)
        self._status.setProperty('role', 'caption')
        lay.addWidget(self._status, 1)

        # 删除当前这条（只有"在编辑一条已经存在的语句"时才出现）
        self._delete_btn = QPushButton('删除这条语句')
        self._delete_btn.setProperty('variant', 'danger')
        self._delete_btn.clicked.connect(self._delete_current_statement)
        self._delete_btn.setVisible(False)
        lay.addWidget(self._delete_btn)

        # 候选分栏页自己的保存按钮（不跟语句的保存混在一起）
        self._groups_save = QPushButton('保存候选分栏')
        self._groups_save.setProperty('variant', 'primary')
        self._groups_save.clicked.connect(self._save_columns_now)
        self._groups_save.setVisible(False)
        lay.addWidget(self._groups_save)

        self._close_btn = QPushButton('关闭')
        self._close_btn.clicked.connect(self.close)
        lay.addWidget(self._close_btn)

        self._ok_btn = QPushButton('保存这条语句')
        self._ok_btn.setProperty('variant', 'primary')
        # ⚠ 回车**不许**等于"保存"：任何输入框里按一下回车都不该写盘
        #   （候选值那一格的回车是"加一个方块"，见 `ValueChips`）。
        self._ok_btn.setAutoDefault(False)
        self._ok_btn.clicked.connect(self._save_statement)
        lay.addWidget(self._ok_btn)
        return bar

    def _show_help(self):
        """一份说人话的说明（术语表 + 四步）。"""
        QMessageBox.information(self, '自定义语句 · 怎么用', (
            '一、这里在干什么\n'
            '  剧本里会出现工具没见过的写法，例如  swim_suit 小明 泳装 。\n'
            '  在这里把它的形状说一遍，工具就能把它拆成表格里的列。\n'
            '\n'
            '二、几个词\n'
            '  语句      剧本里的一行，例如 swim_suit 小明 泳装\n'
            '  开头词    这一行的第一个词：swim_suit\n'
            '  格        开头词之后的每一个位置；格子从上到下的顺序 = 剧本里的顺序\n'
            '  列名      这一格在表格里显示成什么（例如「角色」）\n'
            '  候选值    这一格允许写的词（不填也行，那就谁都能写）\n'
            '  候选分栏  把候选值分组显示（例如「服装」「表情」）；不建也能用\n'
            '  项目清单  项目根目录的 storygraph.rules.json，可评审、可 diff\n'
            '\n'
            '三、四步\n'
            '  1. 在列表里点一个工具还不认识的写法（或者手动输入一个）\n'
            '  2. 说清它后面跟什么：加几格、每格叫什么、是不是从候选里选\n'
            '  3. 粘一行真实剧本，按「切一下」，看切得对不对\n'
            '  4. 按「保存这条语句」—— 保存就生效，表格里立刻多出这几列\n'
            '\n'
            '四、两件容易误会的事\n'
            '  · 这里写的声明只让「工具」认识它（多几列、多几个下拉），不动引擎。\n'
            '    要让引擎也认识，项目里得有一段 renpy.register_statement。\n'
            '  · 保存只写项目清单这一个文件，其它键一律原样保留，改之前自动备份\n'
            '    到 storygraph.rules.backup/。'))

    # ==================================================================
    #  页 1：列表（有哪些写法 / 已经认识哪些）
    # ==================================================================

    def _build_list_page(self) -> QWidget:
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
        lay.setContentsMargins(T.SPACE['lg'], T.SPACE['md'],
                               T.SPACE['lg'], T.SPACE['md'])
        lay.setSpacing(T.SPACE['sm'])

        # —— A. 还没认出来的写法 ——
        self._found_title = _section('工具还不认识的写法')
        lay.addWidget(self._found_title)
        lay.addWidget(_hint('工具本来就认识的（show / pause / with 这些）不会出现在这里 —— '
                            '它们不用教。点一行就能把那个词带进编辑器。'))
        self.found = QTableWidget(0, 4)
        self.found.setHorizontalHeaderLabels(['写法', '出现', '带块', '原文样例'])
        self.found.verticalHeader().setVisible(False)
        self.found.setMinimumWidth(0)
        self.found.setMinimumHeight(96)
        self.found.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.found.setSelectionMode(QAbstractItemView.SingleSelection)
        self.found.setEditTriggers(QAbstractItemView.NoEditTriggers)
        fh = self.found.horizontalHeader()
        for col in (0, 1, 2):
            fh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        fh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.found.itemSelectionChanged.connect(self._on_pick)
        self.found.cellDoubleClicked.connect(lambda *_a: self._teach_selected())
        self.found.horizontalHeader().setDefaultAlignment(
            Qt.AlignLeft | Qt.AlignVCenter)
        self.found.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay.addWidget(self.found)
        self._found_note = _hint('')
        lay.addWidget(self._found_note)

        found_row = FlowRow(spacing=T.SPACE['xs'])
        self._teach_btn = QPushButton('教工具认识这个词')
        self._teach_btn.setProperty('variant', 'primary')
        self._teach_btn.setToolTip('先在上面那张表里点一行，再按这里')
        self._teach_btn.setEnabled(False)
        self._teach_btn.clicked.connect(self._teach_selected)
        found_row.add(self._teach_btn)
        self._manual_btn = QPushButton('＋ 手动输入一个写法')
        self._manual_btn.clicked.connect(lambda: self._new_statement()
                                         or self._go(PAGE_FORM))
        found_row.add(self._manual_btn)
        lay.addWidget(found_row)

        # —— B. 已经认识的 ——
        lay.addWidget(_section('已经认识的写法'))
        lay.addWidget(_hint('来自项目清单，或者脚本里的 renpy.register_statement。'
                            '选中一条可以查看、修改它现在是怎么描述的。'))
        self.known = QTableWidget(0, 4)
        self.known.setHorizontalHeaderLabels(['写法', '中文名', '格数', '来源'])
        self.known.verticalHeader().setVisible(False)
        self.known.setMinimumWidth(0)
        self.known.setMinimumHeight(88)
        self.known.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.known.setSelectionMode(QAbstractItemView.SingleSelection)
        self.known.setEditTriggers(QAbstractItemView.NoEditTriggers)
        kh = self.known.horizontalHeader()
        for col in (0, 1, 2):
            kh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        kh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.known.itemSelectionChanged.connect(self._on_known_pick)
        self.known.cellDoubleClicked.connect(lambda *_a: self._edit_selected())
        self.known.horizontalHeader().setDefaultAlignment(
            Qt.AlignLeft | Qt.AlignVCenter)
        self.known.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay.addWidget(self.known)
        self._known_note = _hint('')
        lay.addWidget(self._known_note)

        known_row = FlowRow(spacing=T.SPACE['xs'])
        self._edit_btn = QPushButton('查看 / 修改这一条')
        self._edit_btn.setEnabled(False)
        self._edit_btn.clicked.connect(self._edit_selected)
        known_row.add(self._edit_btn)
        lay.addWidget(known_row)

        # —— C. 两件"不进语句编辑器"的事 ——
        lay.addWidget(_section('项目里的公共设置'))
        lay.addWidget(_hint('这两件事不属于某一条语句，整个项目共用。'))
        other = FlowRow(spacing=T.SPACE['xs'])
        self._groups_btn = QPushButton('候选分栏')
        self._groups_btn.setToolTip(
            '把候选值分组显示（例如「服装」「表情」）。不建也能用 —— 下拉里就是平铺的。')
        self._groups_btn.clicked.connect(lambda: self._go(PAGE_GROUPS))
        other.add(self._groups_btn)
        self._manifest_btn = QPushButton('项目清单')
        self._manifest_btn.setToolTip(
            '看 storygraph.rules.json 里实际写着什么，以及删除 / 重置。')
        self._manifest_btn.clicked.connect(lambda: self._go(PAGE_MANIFEST))
        other.add(self._manifest_btn)
        lay.addWidget(other)

        lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        self.list_scroll = scroll
        return page

    # ==================================================================
    #  页 2：编辑一条语句
    # ==================================================================

    def _build_form_page(self) -> QWidget:
        outer = QWidget()
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(0)

        # ⚠ 这一页**只有一个滚动区**（"屏幕上一堆滚动条"是本仓的历史病）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setMinimumWidth(0)
        self.declare_scroll = scroll        # 旧名（判据里用过）
        body = QWidget()
        body.setMinimumWidth(0)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(T.SPACE['lg'], T.SPACE['md'],
                               T.SPACE['lg'], T.SPACE['md'])
        lay.setSpacing(T.SPACE['sm'])

        head = QHBoxLayout()
        head.setSpacing(T.SPACE['sm'])
        self._back_btn = QPushButton('← 返回列表')
        self._back_btn.setProperty('role', 'ghost')
        self._back_btn.clicked.connect(lambda: self._go(PAGE_LIST))
        head.addWidget(self._back_btn)
        self._form_title = QLabel('新语句')
        self._form_title.setProperty('role', 'panelTitle')
        head.addWidget(self._form_title)
        self._form_sub = _hint('')
        head.addWidget(self._form_sub, 1)
        lay.addLayout(head)
        #: 「候选被换成了剧本真源」的提示（只在真的换过时出现 —— 见
        #: `_refresh_role_note`）。⚠ 放在**页顶**：格子卡一多，这一声放在
        #: 列表底下就等于没说话（真机三张卡就把视口占满了）。
        self._role_fix_note = _hint('')
        self._role_fix_note.setVisible(False)
        lay.addWidget(self._role_fix_note)

        # —— 1. 它以哪个词开头 ——
        lay.addWidget(_step(1, '它以哪个词开头'))
        self.name = QLineEdit()
        self.name.setPlaceholderText('剧本里原样出现的那个词，例如 swim_suit')
        self.name.setMinimumWidth(220)
        self.name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # ⭐ 一个输入框、一份补全：剧本里见过的词 + 引擎本来就有的词。
        #   旧版是"二选一下拉（自己写的 / 引擎自带的）+ 另一个下拉" ——
        #   同一件事要人先选类别再选词，反而多两步（用户 2026-10-10 卡在这里：
        #   "我第一反应是填 show。但是等一下，show 是 Ren'Py 的原生指令！"）。
        #   现在：直接写，写的是引擎已有的就当场解释清楚后果。
        self._completer = QCompleter(self._completion_words(), self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self.name.setCompleter(self._completer)
        name_row = FlowRow(spacing=T.SPACE['xs'])
        name_row.add(QLabel('开头词'))
        name_row.add(self.name)
        lay.addWidget(name_row)
        #: 这一行解释"引擎认不认识它" —— 直接把最容易卡死的问题答在当场
        #: （用户 2026-10-10 的原话："如果我把 show 注册成自定义语句，
        #:  会不会覆盖引擎原有的 show？"）。
        self.mode_note = _hint('')
        lay.addWidget(self.mode_note)

        meta = QHBoxLayout()
        meta.setSpacing(T.SPACE['sm'])
        cn = QVBoxLayout()
        cn.setSpacing(2)
        cn.addWidget(_field_label('中文名（可留空）'))
        self.label = QLineEdit()
        self.label.setPlaceholderText('例：泳装切换')
        cn.addWidget(self.label)
        meta.addLayout(cn, 1)
        ds = QVBoxLayout()
        ds.setSpacing(2)
        ds.addWidget(_field_label('一句话说明（可留空）'))
        self.docs = QLineEdit()
        self.docs.setPlaceholderText('例：切到泳装立绘')
        ds.addWidget(self.docs)
        meta.addLayout(ds, 2)
        lay.addLayout(meta)

        self.block = QCheckBox('它后面跟一个代码块（mycue "闪电": 下面缩进的那几行逐字保留）')
        lay.addWidget(self.block)

        # —— 2. 后面跟什么 ——
        lay.addWidget(_step(2, '后面跟什么'))
        lay.addWidget(_hint('一格 = 剧本里的一个位置；格子从上到下的顺序，就是剧本里的先后顺序。'
                            '不需要填的位置就别加格。'))
        tools = FlowRow(spacing=T.SPACE['xs'])
        add = QPushButton('＋ 加一格')
        add.clicked.connect(lambda: self.params.add_field())
        rm = QPushButton('删掉最后一格')
        rm.clicked.connect(self._remove_param)
        tools.add(add)
        tools.add(rm)
        lay.addWidget(tools)
        # ⚠ 不是 QScrollArea（那会变成第二条滚动条）—— 纯竖向容器
        self.params = FieldList(on_change=self._on_form_changed,
                                sources_provider=self._choice_sources,
                                on_missing_change=self._on_form_changed)
        lay.addWidget(self.params)
        self.params.add_field()          # 一进来就有一格，省得人想"要先点加号吗"

        # —— 3. 拿一行剧本试试 ——
        lay.addWidget(_step(3, '拿一行剧本试试'))
        lay.addWidget(_hint('从剧本里粘一整行最准（开头那个词不用删，工具认得出）。'))
        cut_row = FlowRow(spacing=T.SPACE['xs'])
        self.sample = QLineEdit()
        self.sample.setPlaceholderText('例：show 小明 泳装 开心')
        self.sample.setMinimumWidth(240)
        self.sample.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        cut_row.add(self.sample)
        self.cut_try = QPushButton('切一下')
        self.cut_try.setToolTip('按上面这些格子，把这行剧本切开看看')
        self.cut_try.clicked.connect(self._refresh_cut)
        cut_row.add(self.cut_try)
        self._verify_btn = QPushButton('用引擎验证')
        self._verify_btn.setToolTip('真跑一次引擎（慢，要 SDK）')
        self._verify_btn.clicked.connect(self._do_verify)
        cut_row.add(self._verify_btn)
        lay.addWidget(cut_row)
        self.cut_view = QLabel()
        self.cut_view.setWordWrap(True)
        self.cut_view.setProperty('role', 'caption')
        self.cut_view.setMinimumHeight(40)
        lay.addWidget(self.cut_view)
        self._verify_view = _hint('')
        self._verify_view.setVisible(False)
        lay.addWidget(self._verify_view)

        # —— 4. 写入预览（给程序看的，默认收起）——
        self._preview_toggle = QToolButton()
        self._preview_toggle.setText('会写进项目清单的内容')
        self._preview_toggle.setCheckable(True)
        self._preview_toggle.setChecked(False)
        self._preview_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._preview_toggle.setArrowType(Qt.RightArrow)
        self._preview_toggle.toggled.connect(self._on_preview_toggled)
        lay.addWidget(self._preview_toggle)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(120)
        self.preview.setVisible(False)
        lay.addWidget(self.preview)

        lay.addStretch(1)
        scroll.setWidget(body)
        ol.addWidget(scroll, 1)

        for w in (self.name, self.label, self.docs):
            w.textChanged.connect(self._on_form_changed)
        self.block.toggled.connect(self._on_form_changed)
        # ⚠ 只接"试切"这一个落点：接两次再断开是自找麻烦（顺序错一次就炸）
        self.sample.textChanged.connect(self._refresh_cut)
        return outer

    def _on_preview_toggled(self, on: bool):
        self.preview.setVisible(bool(on))
        self._preview_toggle.setArrowType(Qt.DownArrow if on else Qt.RightArrow)

    # ==================================================================
    #  页 3：候选分栏
    # ==================================================================

    def _build_groups_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.groups_scroll = scroll
        inner = QWidget()
        inner.setMinimumWidth(0)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(T.SPACE['lg'], T.SPACE['md'],
                               T.SPACE['lg'], T.SPACE['md'])
        lay.setSpacing(T.SPACE['sm'])

        head = QHBoxLayout()
        head.setSpacing(T.SPACE['sm'])
        back = QPushButton('← 返回列表')
        back.setProperty('role', 'ghost')
        back.clicked.connect(lambda: self._go(PAGE_LIST))
        head.addWidget(back)
        title = QLabel('候选分栏')
        title.setProperty('role', 'panelTitle')
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)

        lay.addWidget(_hint('什么时候需要它：某一格的候选值很多（例如表情几十个），'
                            '想让下拉里分组显示。不建栏 = 完全平铺，一样能用。'))
        lay.addWidget(_hint('一栏三件事：键名（写进项目清单的那个英文名）/ 显示名（下拉里看到的）/ '
                            '这一栏的候选值（留空 = 兜底栏：没归到别的栏的词都进它）。'))
        lay.addWidget(_hint('立绘属性（show ye base_handdown happy）的分栏引擎自己就有，'
                            '不需要在这里建 —— 这里建的栏只给自定义语句的候选值用。'))

        self.columns_editor = ColumnList(on_change=self._sync_columns_to_fields)
        self.columns_editor.set_known_keys(self._project_group_keys())
        lay.addWidget(self.columns_editor)
        tools = FlowRow(spacing=T.SPACE['xs'])
        add = QPushButton('＋ 加一栏')
        add.clicked.connect(self._add_column)
        rm = QPushButton('－ 删最后一栏')
        rm.clicked.connect(self._remove_column)
        tools.add(add)
        tools.add(rm)
        lay.addWidget(tools)
        lay.addWidget(_hint('保存会用这一页整段替换项目清单里的候选分栏：在这里删掉的栏会真的消失。'
                            '语句声明一个字都不会动。'))
        lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # ⚠ 旧名字保留成**只读别名**：判据与别处的代码还在用 `groups` 这几个字。
        self.groups = self.columns_editor
        return page

    # ==================================================================
    #  页 4：项目清单（只读 + 删除 / 重置）
    # ==================================================================

    def _build_manifest_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(T.SPACE['lg'], T.SPACE['md'],
                               T.SPACE['lg'], T.SPACE['md'])
        lay.setSpacing(T.SPACE['sm'])

        head = QHBoxLayout()
        head.setSpacing(T.SPACE['sm'])
        back = QPushButton('← 返回列表')
        back.setProperty('role', 'ghost')
        back.clicked.connect(lambda: self._go(PAGE_LIST))
        head.addWidget(back)
        title = QLabel('项目清单')
        title.setProperty('role', 'panelTitle')
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)

        lay.addWidget(_hint('这里是 storygraph.rules.json 的实际内容（语句 + 候选分栏），'
                            '以及会自动保留的其它键。删除 / 清空都会先把整份清单备份到 '
                            'storygraph.rules.backup/，可以回退。'))
        self._manage_path = QLabel('', self)
        self._manage_path.setWordWrap(True)
        self._manage_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._manage_path.setProperty('role', 'caption')
        # ⚠ 不进布局：路径那一行已经写在只读框的第一行了，再挂一个标签就是
        #   同一句话在同一个屏幕上出现两次（截图里看着像界面出了错）。
        self._manage_path.setVisible(False)
        self.manage_view = QPlainTextEdit()
        self.manage_view.setReadOnly(True)
        self.manage_view.setMinimumHeight(150)
        lay.addWidget(self.manage_view, 1)

        lay.addWidget(_field_label('要删除的语句（选中一条，再点删除）'))
        self.manage_list = QListWidget()
        self.manage_list.setMaximumHeight(104)
        lay.addWidget(self.manage_list)

        tools = FlowRow(spacing=T.SPACE['xs'])
        self._manage_del = QPushButton('删除选中的语句')
        self._manage_del.clicked.connect(self._delete_selected_statement)
        tools.add(self._manage_del)
        self._manage_clear_groups = QPushButton('清空候选分栏')
        self._manage_clear_groups.clicked.connect(self._clear_groups)
        tools.add(self._manage_clear_groups)
        self._manage_reset = QPushButton('重置所有自定义声明…')
        self._manage_reset.clicked.connect(self._reset_all)
        tools.add(self._manage_reset)
        lay.addWidget(tools)

        self._manage_status = QLabel('')
        self._manage_status.setWordWrap(True)
        self._manage_status.setProperty('role', 'caption')
        lay.addWidget(self._manage_status)

        #: 只读的"现在清单里有什么"报告（页 1 与这里共用同一份文字）
        self.declared = QPlainTextEdit()
        self.declared.setReadOnly(True)
        self.declared.setVisible(False)     # 内容已并进 `manage_view`
        lay.addWidget(self.declared)
        return page

    # ==================================================================
    #  数据：读清单 / 重画列表
    # ==================================================================

    def _registry_now(self):
        from storygraph.story import statement_registry as SR
        return SR, SR.StatementRegistry.for_project(self._root_path)

    def _reload_registry(self):
        """重读项目清单（写完盘之后要立刻重读，否则列表还是旧的）。"""
        from storygraph.story import statement_registry as SR
        try:
            SR.clear_cache()
            reg = SR.StatementRegistry.for_project(self._root_path)
            self._declared = list(reg.declared or ())
            self._host = list(reg.host or ())
        except Exception as e:                  # noqa: BLE001 - 读不到就当空
            swallow('ui_qt.dialog_learn.reload_registry', e)
            self._declared, self._host = [], []
        self._rebuild_found()
        self._refresh_manage()

    def _rebuild_found(self):
        """把"还没声明"的那份表重画一遍（刚保存的会当场消失）。"""
        known = set()
        for s in self._declared:
            for n in (getattr(s, 'names', None) or [getattr(s, 'name', '')]):
                if n:
                    known.add(str(n))
        self._pending = [r for r in self._discovered
                         if str(r.get('head') or '') not in known]
        table = self.found
        # ⚠ 重画表格时**别惊动选中回调**：那会把正在编辑的表单顶掉
        was = table.blockSignals(True)
        try:
            table.setRowCount(len(self._pending))
            for i, rec in enumerate(self._pending):
                table.setItem(i, 0, QTableWidgetItem(str(rec.get('head', ''))))
                table.setItem(i, 1, QTableWidgetItem(str(rec.get('count', 0))))
                table.setItem(i, 2, QTableWidgetItem('是' if rec.get('block') else ''))
                sample = (rec.get('samples') or [('', '')])[0]
                item = QTableWidgetItem(str(sample[1]))
                item.setToolTip(str(sample[1]))
                table.setItem(i, 3, item)
        finally:
            table.blockSignals(was)
        n = len(self._pending)
        self._found_title.setText(f'工具还不认识的写法（{n}）' if n
                                  else '工具还不认识的写法')
        note = ''
        if not n:
            note = ('没发现未声明的写法 —— 要么项目里没有自定义语句，'
                    '要么它们已经在项目清单或脚本的 CDS 里声明过了（见下面那张表）。')
        self._found_note.setText(note)
        # 空说明不该占一行高度（截图里那两片空白就是这么来的）
        self._found_note.setVisible(bool(note))
        self._teach_btn.setEnabled(bool(n) and self.found.currentRow() >= 0)
        self._refresh_known()
        self._fit_table(self.found, 96)
        self._fit_table(self.known, 88)

    @staticmethod
    def _fit_table(table: QTableWidget, floor: int = 60):
        """
        表格高度 = **内容高度**（不再拉满整页）。

        为什么（截图当场看出来的）：两张表各占一半剩余高度时，空表会撑出
        一大片空白，"几张卡片浮在一片空地上"；而内容多了又只能在那片空白里滚。
        固定成"够放下现有几行"最干净 —— 内容多了它自己出滚动条。
        """
        header = table.horizontalHeader().height() if table.rowCount() else 26
        body = sum(table.rowHeight(i) for i in range(table.rowCount()))
        height = max(floor, header + body + 2 * table.frameWidth() + 6)
        table.setMinimumHeight(height)
        table.setMaximumHeight(height)

    def _refresh_known(self):
        rows = [(s, '项目清单') for s in self._declared] + \
               [(s, '脚本里的 CDS') for s in self._host]
        table = self.known
        table.setRowCount(len(rows))
        for i, (s, source) in enumerate(rows):
            cols = len(getattr(s, 'columns', []) or [])
            table.setItem(i, 0, QTableWidgetItem(str(getattr(s, 'name', ''))))
            table.setItem(i, 1, QTableWidgetItem(str(getattr(s, 'label', '') or '')))
            table.setItem(i, 2, QTableWidgetItem(f'{cols} 格' if cols else ''))
            origin = str(getattr(s, 'origin', '') or '')
            item = QTableWidgetItem(source + (f'（{origin}）' if origin
                                              and source == '脚本里的 CDS' else ''))
            item.setToolTip(origin)
            table.setItem(i, 3, item)
        self._known_note.setText('' if rows else
                                 '还没有声明过任何语句 —— 从上面挑一个词教它。')
        self._known_note.setVisible(not rows)
        self._edit_btn.setEnabled(bool(rows) and self.known.currentRow() >= 0)
        # 底部两个入口带上的计数，让人不用点进去就知道有多少东西
        try:
            n_groups = len(self.columns_editor.rows())
        except Exception as e:                  # noqa: BLE001 - 还没建好就当 0
            swallow('ui_qt.dialog_learn.refresh_known.groups', e)
            n_groups = 0
        self._groups_btn.setText(f'候选分栏（{n_groups}）' if n_groups else '候选分栏')
        self._manifest_btn.setText('项目清单')

    def _registry_report_now(self) -> str:
        """把"现在清单里有什么"拼成人话（页 1 与页 4 共用）。"""
        if not self._root_path:
            return '还没有打开项目。'
        try:
            SR, reg = self._registry_now()
            text = SR.format_registry_report(reg)
            groups = list(reg.attr_groups or ())
            if groups:
                text += '\n\n候选分栏（attr_groups）：\n'
                for g in groups:
                    words = (f'{len(g.options)} 个词' if g.options else '兜底栏')
                    text += f'  · {g.title()}（{g.name}）：{words}\n'
            else:
                text += '\n\n候选分栏：没有声明（立绘属性候选是平铺的）。'
            return text.rstrip()
        except Exception as e:                  # noqa: BLE001 - 读不出来就说清楚
            swallow('ui_qt.dialog_learn.registry_report_now', e)
            return f'读不到清单：{type(e).__name__}: {e}'

    def _refresh_manage(self):
        """重画"项目清单"页（只读内容 + 三个危险按钮的可用性）。"""
        if not hasattr(self, 'manage_view'):
            return
        if not self._root_path:
            self.manage_view.setPlainText('还没有打开项目。')
            self._manage_path.setText('')
            for b in (self._manage_del, self._manage_clear_groups,
                      self._manage_reset):
                b.setEnabled(False)
            return
        from storygraph.story import statement_registry as SR
        from storygraph.story.project_conventions import load_manifest
        try:
            reg = SR.StatementRegistry.for_project(self._root_path)
            data, path, warns = load_manifest(self._root_path)
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.dialog_learn.refresh_manage', e)
            self.manage_view.setPlainText(f'读不到清单：{type(e).__name__}: {e}')
            return
        manifest_path = Path(path) if path else (
            Path(self._root_path) / SR.MANIFEST_NAMES[0])
        declared = list(reg.declared or ())
        host = list(reg.host or ())
        groups = list(reg.attr_groups or ())
        lines = [f'清单文件：{manifest_path}',
                 '状态：' + ('存在' if manifest_path.is_file()
                             else '不存在（项目没有自定义声明）'), '']
        lines.append(f'语句声明（{len(declared)} 条）')
        if declared:
            for s in declared:
                cols = len(getattr(s, 'columns', []) or [])
                label = f'（{s.label}）' if getattr(s, 'label', '') else ''
                lines.append(f'  · {s.name}{label} —— {cols} 格')
        else:
            lines.append('  （没有）')
        if host:
            lines.append('')
            lines.append(f'脚本里的 CDS（{len(host)} 条，清单里删不掉）：')
            for s in host:
                lines.append(f'  · {s.name}（{s.origin or "脚本"}）')
        lines.append('')
        lines.append(f'候选分栏（{len(groups)} 栏）')
        if groups:
            for g in groups:
                words = f'{len(g.options)} 个词' if g.options else '兜底栏'
                lines.append(f'  · {g.title()}（标识 {g.name}）—— {words}')
        else:
            lines.append('  （没有）')
        others = ([k for k in data if k not in (SR.MANIFEST_KEY,
                                                SR.ATTR_GROUPS_KEY)]
                  if isinstance(data, dict) else [])
        if others:
            lines.append('')
            lines.append('其它键（原样保留，不会被删）：' + '、'.join(others))
        if warns:
            lines.append('')
            lines.append('清单告警：' + '；'.join(str(w) for w in warns))
        self.manage_view.setPlainText('\n'.join(lines))
        self._manage_path.setText(f'清单：{manifest_path}')

        self.manage_list.clear()
        for s in declared:
            cols = len(getattr(s, 'columns', []) or [])
            item = QListWidgetItem(
                f'{s.name}' + (f'（{s.label}）' if getattr(s, 'label', '') else '')
                + f' —— {cols} 格')
            item.setData(Qt.UserRole, s.name)
            self.manage_list.addItem(item)
        self._manage_del.setEnabled(bool(declared))
        self._manage_clear_groups.setEnabled(bool(groups))
        self._manage_reset.setEnabled(bool(declared or groups))
        report = self._registry_report_now()
        self._registry_report = report

    # ==================================================================
    #  导航：列表 ↔ 编辑 ↔ 分栏 / 清单
    # ==================================================================

    def _go(self, page: int):
        """
        切页。**离开编辑页**时先看有没有没保存的改动（有就问一句）。

        为什么要问：这一版允许"编一条、存一条、再编一条"，编辑页随时会被
        列表页顶掉；不问一句就等于把刚敲的格子静默丢掉。
        """
        if page != PAGE_FORM and self.pages.currentIndex() == PAGE_FORM:
            if not self._confirm_leave_form():
                return
            self._new_statement()
        if page == PAGE_GROUPS:
            self._refresh_known()
        self.pages.setCurrentIndex(page)
        self._sync_action_bar()

    def _sync_action_bar(self):
        page = self.pages.currentIndex()
        on_form = page == PAGE_FORM
        self._ok_btn.setVisible(on_form)
        # ⚠ 脚本里 `renpy.register_statement` 声明的那几条（`source == 'host-registry'`）
        #   在项目清单里**没有条目** —— 给它们一个"删除这条语句"的按钮，
        #   点下去只会得到一句"没删成"（`remove_statement` 找不到它）。
        editable = (self._editing is not None
                    and str(getattr(self._editing, 'source', '')) != 'host-registry')
        self._delete_btn.setVisible(on_form and editable)
        self._delete_btn.setToolTip(
            '从项目清单里删掉这一条（先备份；脚本里的 CDS 删不掉）')
        self._groups_save.setVisible(page == PAGE_GROUPS)
        self._status.setVisible(True)
        if page == PAGE_LIST:
            n = len(self._pending)
            m = len(self._declared) + len(self._host)
            self._set_status('caption',
                             f'待认领 {n} 个 · 已认识 {m} 条。点一个词开始，'
                             f'或者用「＋ 手动输入一个写法」。')
        elif page == PAGE_GROUPS:
            self._set_status('caption',
                             '改完按「保存候选分栏」—— 只动候选分栏，语句声明一个字都不动。')
        elif page == PAGE_MANIFEST:
            self._set_status('caption', '这一页是只读的清单内容；删除 / 重置都会先备份。')
        else:
            self._refresh_preview()

    def _set_status(self, level: str, text: str):
        lab = getattr(self, '_status', None)
        if lab is None:
            return
        lab.setText(text)
        lab.setProperty('role', {'warn': 'warn', 'error': 'warn',
                                 'ok': 'note'}.get(level, 'caption'))
        lab.setStyleSheet('')
        lab.style().unpolish(lab)
        lab.style().polish(lab)

    def _teach_selected(self):
        r = self.found.currentRow()
        if not (0 <= r < len(self._pending)):
            return
        self._on_pick()
        self._go(PAGE_FORM)

    def _edit_selected(self):
        row = self.known.currentRow()
        rows = list(self._declared) + list(self._host)
        if not (0 <= row < len(rows)):
            return
        if not self._confirm_leave_form():
            return
        self._load_statement(rows[row])
        self._go(PAGE_FORM)

    def _on_pick(self):
        """点左边发现的一条 → 把这个词带进编辑器（**不跳页**，人自己按按钮）。"""
        r = self.found.currentRow()
        if not (0 <= r < len(self._pending)):
            return
        rec = self._pending[r]
        self._load_statement(None, head=str(rec.get('head') or ''),
                             block=bool(rec.get('block')))
        self._teach_btn.setEnabled(True)

    def _on_known_pick(self):
        """点一条已经认识的 → 只选中（不加载，免得把正在编的东西顶掉）。"""
        self._edit_btn.setEnabled(self.known.currentRow() >= 0)

    # ==================================================================
    #  载入 / 清空表单
    # ==================================================================

    def _new_statement(self):
        self._load_statement(None)

    def _load_statement(self, decl=None, head: str = '', block=None):
        """
        把一条声明（或一个待认领的词）灌进表单。

        `decl` 给 `RegisteredStatement` = 编辑已有；给 `None` = 新语句。
        """
        self._loading = True
        try:
            self._editing = decl
            self.name.setText(str(head or getattr(decl, 'name', '') or ''))
            self.label.setText(str(getattr(decl, 'label', '') or ''))
            self.docs.setText(str(getattr(decl, 'docs', '') or ''))
            # 清单里的 `block` 是 True/False/'possible' 三种（'possible' = 不确定，
            # 别替人勾上；新语句则用"剧本里见没见过它带块"来预填）。
            raw = getattr(decl, 'block', None)
            if raw in (True, 'yes'):
                checked = True
            elif raw in (False, 'no'):
                checked = False
            else:
                checked = bool(block)
            self.block.setChecked(checked)
            self.sample.clear()
            self.cut_view.clear()
            self._verify_view.setVisible(False)
            self._fill_params(decl)
            self._sync_optional_defaults()
        finally:
            self._loading = False
        self._baseline = self._signature()
        self._refresh_form_head()
        self._refresh_preview()

    def _roles_cached(self) -> dict:
        """
        角色表读**一次**（`learn_statement.role_table`）—— 真机首次 ~600 ms。

        ⚠ 必须缓存：`_choices_for_script` 每格调一次、`_why_not` 又会被
          `sample.textChanged` 逐字调一次（输入时每个按键都会走到），
          不缓存就是"打字卡一下"。
        ⚠ 只在弹窗打开期间有效：角色表在弹窗关着的时候才可能被别处改。
        """
        cache = getattr(self, '_roles_cache', None)
        if cache is not None:
            return cache
        try:
            from storygraph.authoring.learn_statement import role_table
            cache = role_table(self._root_path) or {}
        except Exception as e:                  # noqa: BLE001 - 没表就当没有
            swallow('ui_qt.dialog_learn.roles_cached', e)
            cache = {}
        self._roles_cache = cache
        return cache

    def _choices_for_script(self, values, head: str, where: str = ''):
        """
        候选值 → **剧本里要写的那种写法**（+ 显示名标签）。逗号串 / 标签给 `add_field`。

        ⚠⚠ 为什么在**载入**时就要换（2026-10-18 用户现场）：

        > 明明它只是换成变量名又不认得了……

        角色表里同一个人有三种写法：变量名（`l`）、显示名（`蓝`）、立绘 tag
        （`lan`）。旧版把**显示名**写进了声明（`rules.json` 的 `choices`），
        于是：剧本里的 `show lan …` 在封闭枚举里判"切不上"，而卡片的下拉
        又会把 `蓝` 写回脚本 —— 两处都是同一个错值。

        只做**查表**（`learn_statement.role_candidates_for`），认不出是角色的
        值一个字都不动（「服装」「表情」那两格与角色表无关）。换掉之后仍然把
        显示名当**标签**交给芯片（`蓝（lan）`），编剧不至于对着一串 `dhy` 发懵。
        """
        items = list(values or ())
        head = str(head or '')
        try:
            from storygraph.authoring.learn_statement import role_candidates_for
            got = role_candidates_for(self._root_path, head, items,
                                      roles=self._roles_cached())
        except Exception as e:                  # noqa: BLE001 - 换不了就原样
            swallow('ui_qt.dialog_learn.choices_for_script', e)
            return '、'.join(str(v) for v in items), None
        changed = dict(got.get('changed') or {})
        if changed:
            self._role_fixes.append({'where': str(where or ''),
                                     'changed': changed,
                                     'before': int(got.get('asked') or 0),
                                     'after': len(got.get('values') or ())})
        labels = dict(got.get('labels') or {}) or None
        return '、'.join(str(v) for v in (got.get('values') or ())), labels

    def _fill_params(self, decl):
        """声明 → 格子卡（顺序与类型都照 `pattern`；没有 pattern 就照 columns）。"""
        self.params.clear()
        #: 载入时被换成"剧本真源"的格子（`_refresh_role_note` 据此出提示）
        self._role_fixes = []
        if decl is None:
            self.params.add_field()
            self._refresh_role_note()
            return
        head = self.name.text().strip()
        slots = [s for s in (getattr(decl, 'pattern', None) or ())
                 if isinstance(s, dict)]
        if not slots:
            # 老声明只有列、没有位置序列：按"一格一个值"恢复，类型留给用户改
            for col in (getattr(decl, 'columns', None) or ()):
                if not isinstance(col, dict):
                    continue
                name = str(col.get('name') or '')
                label = str(col.get('label') or '') or name
                kind = 'choice' if col.get('choices') else 'variable'
                choices, labels = self._choices_for_script(
                    col.get('choices'), head, label)
                self.params.add_field(name=name, label=label, kind=kind,
                                      choices=choices, labels=labels)
            if not (getattr(decl, 'columns', None) or ()):
                self.params.add_field()
            self._refresh_role_note()
            return
        for slot in slots:
            kind = str(slot.get('kind') or 'variable')
            column = str(slot.get('column') or '').strip()
            text = str(slot.get('text') or '').strip()
            extra = {'text': text,
                     'multi': bool(slot.get('multi'))}
            # ⚠ `required` 只在声明里**写过**时才恢复：没写 = "没说"，
            #   交给语句头决定默认（见 `pattern.with_optional_defaults`）。
            if 'required' in slot:
                extra['required'] = bool(slot.get('required'))
            choices, labels = self._choices_for_script(
                slot.get('options'), head, column)
            self.params.add_field(
                name=column or text, label=column, kind=kind,
                choices=choices, labels=labels, **extra)
        self._refresh_role_note()

    def _refresh_role_note(self):
        """
        把"候选被换成了剧本真源"这件事**说出来**（别做静默的数据改写）。

        文案要点：换了哪一格、换了几个、举一例、以及"保存才会写回清单"。
        """
        lab = getattr(self, '_role_fix_note', None)
        if lab is None:
            return
        fixes = list(getattr(self, '_role_fixes', ()) or ())
        if not fixes:
            lab.setText('')
            lab.setVisible(False)
            return
        parts = []
        for f in fixes[:3]:
            # 例子优先挑**中文显示名**那几条（`蓝 → lan`）—— 人一眼就对得上；
            # `??? → u` 这种也照实给出来，但不占前三样例的位置。
            pairs = list(f['changed'].items())
            pairs.sort(key=lambda kv: (not _has_cjk(kv[0]),))
            pairs = pairs[:3]
            sample = '、'.join(f'{a} → {b}' for a, b in pairs)
            if len(f['changed']) > 3:
                sample += '…'
            where = f'「{f["where"]}」' if f['where'] else '一格'
            parts.append(f'{where}的候选写的是角色表里的另一种写法，'
                         f'已换成剧本里要写的那个词（{sample}；'
                         f'{f["before"]} → {f["after"]} 个）')
        lab.setText('⚠ ' + '；'.join(parts)
                    + '。保存后写回项目清单 —— 不保存的话，卡片下拉里挑出来的'
                      '还是显示名（写进脚本跑不起来）。')
        lab.setVisible(True)

    def _signature(self) -> str:
        """当前表单的"指纹" —— 用来判"改过没有"（不靠一堆信号接线）。"""
        import json
        try:
            b = self.built()
        except Exception as e:                  # noqa: BLE001 - 算不出就当没改
            swallow('ui_qt.dialog_learn.signature', e)
            return ''
        keep = {k: b.get(k) for k in ('name', 'label', 'docs', 'block',
                                      'columns', 'pattern')}
        return json.dumps(keep, sort_keys=True, ensure_ascii=False)

    def _is_dirty(self) -> bool:
        if self._loading:
            return False
        return bool(self._signature() != self._baseline)

    def _confirm_leave_form(self) -> bool:
        """编辑页有没保存的改动 → 问一句。返回 False = 别走。"""
        if not self._is_dirty():
            return True
        # ⚠ **窗口没显示出来时不问**：那种情况下没有"人"可以回答，
        #   弹一个模态框只会让调用方（脚本/判据）永远卡住 —— 而且没有
        #   可见窗口的对话框本来就谈不上"用户正在编辑"。
        if not self.isVisible():
            return True
        head = self.name.text().strip()
        if not head and not self.params.rows():
            return True                     # 空的，没什么可丢的
        box = QMessageBox(self)
        box.setWindowTitle('这条语句还没保存')
        box.setText(f'「{head or "新语句"}」改了还没保存。')
        box.setInformativeText('要保存后再走吗？')
        save = box.addButton('保存', QMessageBox.AcceptRole)
        box.addButton('不保存，直接走', QMessageBox.DestructiveRole)
        stay = box.addButton('留在这里', QMessageBox.RejectRole)
        box.setDefaultButton(save)
        box.exec()
        clicked = box.clickedButton()
        if clicked is stay:
            return False
        if clicked is save:
            return bool(self._save_statement(close=False))
        return True

    def closeEvent(self, event):                # noqa: N802 - Qt 接口
        if self.pages.currentIndex() == PAGE_FORM and not self._confirm_leave_form():
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self):
        if self.pages.currentIndex() == PAGE_FORM and not self._confirm_leave_form():
            return
        super().reject()

    # ==================================================================
    #  保存
    # ==================================================================

    def _save_statement(self, *_a, close: bool = False) -> bool:
        """
        **保存这一条语句** —— 弹窗里唯一的语句写入口。

        为什么要自己写（而不是"关窗之后让调用方写"）：
          · 旧版保存失败只会出现在主窗口控制台里，人看到的是"点了一下没反应"；
          · 旧版一次只能教一条（关窗才能写），教三条要开三次；
          · 更要紧的：旧版调用方漏传了 `pattern` —— 类型/顺序/多选全丢。
            写在一处，就不会再漏。
        """
        if not self._root_path:
            self._set_status('error', '先打开项目 —— 项目清单要写进项目根目录。')
            return False
        built = self.built()
        if not built.get('ok'):
            self._set_status('error', self._built_problem(built))
            return False
        missing = self.missing_name_cards()
        if missing:
            self._set_status('error',
                             f'有 {len(missing)} 格还没起列名 —— 它们会被丢掉，'
                             f'先给它们起个名字（或者删掉那几格）。')
            return False
        from storygraph.story import statement_registry as SR
        res = SR.declare_statement(
            self._root_path, built['name'], label=built.get('label', ''),
            columns=built.get('columns') or [], block=built.get('block'),
            docs=built.get('docs', ''), pattern=built.get('pattern') or [])
        if not res.get('ok'):
            self._set_status('error', '没写进项目清单：' + str(res.get('reason') or '?'))
            return False
        verb = '更新' if res.get('action') == 'updated' else '新增'
        self.changed = True
        self.saved.append({'kind': 'statement', 'verb': verb,
                           'name': built['name'], 'path': res.get('path', ''),
                           'columns': len(built.get('columns') or [])})
        self._reload_registry()
        # 刚存下的东西 = 新的基线，别让"离开"再问一遍
        self._baseline = self._signature()
        self._set_status('ok',
                         f'已{verb}语句「{built["name"]}」：'
                         f'{len(built.get("columns") or [])} 格会变成表格里的列'
                         f'（重新导入那几章就能看到）。')
        if close:
            return True
        self._go(PAGE_LIST)
        self._set_status('ok', f'已{verb}语句「{built["name"]}」—— 它现在在「已经认识的写法」里。')
        return True

    def _built_problem(self, built: dict) -> str:
        warns = [str(w) for w in (built.get('warnings') or [])]
        if warns:
            return '这条还不能保存：' + '；'.join(warns[:3])
        return '这条还不能保存：先给它一个合法的开头词。'

    def _delete_current_statement(self):
        decl = self._editing
        if decl is None or not self._root_path:
            return
        name = str(getattr(decl, 'name', '') or '')
        if not name:
            return
        if QMessageBox.question(
                self, '删除这条语句',
                f'从项目清单里删掉「{name}」？\n\n'
                f'会先把整份清单备份到 storygraph.rules.backup/；'
                f'其它语句、候选分栏与其它键原样保留。'
        ) != QMessageBox.Yes:
            return
        from storygraph.story import statement_registry as SR
        res = SR.remove_statement(self._root_path, name)
        if not res.get('ok'):
            self._set_status('error', '没删成：' + str(res.get('reason') or '?'))
            return
        self.changed = True
        self.saved.append({'kind': 'delete', 'name': name,
                           'path': res.get('path', '')})
        self._reload_registry()
        self._new_statement()
        self._go(PAGE_LIST)
        self._set_status('ok', f'已从项目清单删除「{name}」。')

    # ==================================================================
    #  表单 → 声明
    # ==================================================================

    def _on_form_changed(self, *_a):
        """表单里任何一个字变了：重算预览 / 状态 / 保存键。"""
        self._refresh_preview()

    def _show_like(self) -> bool:
        """这个语句的头是不是 `show` / `scene` / `hide`（属性格可填可不填）。"""
        return str(self.name.text()).strip() in ('show', 'scene', 'hide')

    def _sync_optional_defaults(self):
        """
        把「这一格可以不填」的**默认**同步到卡片上（只动没人碰过的格子）。

        为什么要有这一步（2026-10-18 用户现场）：
          用户写 `show lan happy`（真实项目里服装省略了几十次），试切却报
          "第 2 格该有一个词"。Ren'Py 的 `show` 是 `show <tag> [属性…]`，
          属性**本来就是可选的** —— 默认必须站在"可以不填"这一边，
          而卡片上那个勾选框要**看得见**这个默认（不然人以为它没生效）。
        """
        params = getattr(self, 'params', None)
        name = getattr(self, 'name', None)
        if params is None or name is None:
            return
        try:
            params.set_optional_defaults(self._show_like())
        except Exception as e:                  # noqa: BLE001 - 默认值不该炸窗
            swallow('ui_qt.dialog_learn.optional_defaults', e)

    def built(self) -> dict:
        """当前表单 → 后端参数（纯函数；判据不在这里）。"""
        block = True if self.block.isChecked() else None
        return build_declaration(
            self.name.text(), label=self.label.text(),
            docs=self.docs.text(), block=block,
            params=self.params.rows(),
            # ⚠ 属性格默认"可不填"在**这一处**补上（`show/scene/hide`）：
            #   卡片没被人动过时不会自带 `required`，这里按语句头补默认，
            #   写进声明的 `pattern` 也就是"以后一直是这个意思"。
            pattern=_with_optional_defaults(self.params.slots(),
                                            self.name.text()))

    def result_declaration(self) -> dict:
        """按下去之后的最终参数（调用方/判据拿去查声明）。"""
        return self.built()

    def _remove_param(self):
        self.params.remove_current()
        self._refresh_preview()

    # ==================================================================
    #  候选分栏 <-> 格子
    # ==================================================================

    def _project_group_keys(self) -> list:
        """项目里真实存在的立绘组名（分栏键不该让人盲填）。"""
        try:
            from storygraph.authoring.learn_statement import project_group_keys
            return project_group_keys(self._root_path)
        except Exception as e:                  # noqa: BLE001 - 读不到就空
            swallow('ui_qt.dialog_learn.project_group_keys', e)
            return []

    def _add_column(self):
        self.columns_editor.add_column()

    def _remove_column(self):
        self.columns_editor.remove_current()

    def _sync_columns_to_fields(self, notify: bool = True):
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
        except Exception as e:                  # noqa: BLE001 - 不许炸窗口
            swallow('ui_qt.dialog_learn.sync_columns', e)
        if notify:
            self._refresh_known()
            self._refresh_preview()

    # 旧名保留（判据与旧调用点还在用 `_sync_groups_to_slots`）
    def _sync_groups_to_slots(self, *_a):
        return self._sync_columns_to_fields()

    def built_groups(self) -> list:
        """分栏表 → 后端参数（`learn_statement.build_group`）。"""
        from storygraph.authoring.learn_statement import build_group
        return [build_group(row['key'], row['shown'], row['values'],
                            split=bool(row.get('split')))
                for row in self.columns_editor.rows()
                if str(row.get('key') or '').strip()]

    def group_problems(self) -> list:
        """当前分栏页里**不合法**的项（保存前先看它）。"""
        out = []
        for r in self.built_groups():
            if not r.get('ok'):
                out.extend(r.get('warnings') or ['分栏键名不合法'])
        return out

    def groups_to_write(self) -> list:
        """现在能写进清单的分栏（非法项不在这里静默吞掉）。"""
        return [g for g in self.built_groups() if g.get('ok')]

    def _save_columns_now(self):
        """保存候选分栏（整段替换；**不要求**先编一条语句）。"""
        if not self._root_path:
            self._set_status('error', '先打开项目 —— 项目清单要写进项目根目录。')
            return
        bad = self.group_problems()
        if bad:
            QMessageBox.warning(self, '分栏有问题',
                                '下面这些栏不会被写进项目清单：\n\n'
                                + '\n'.join(f'· {p}' for p in bad))
            return
        clean = [{'name': r['name'], 'label': r.get('label', ''),
                  'options': list(r.get('options') or ()),
                  'split': bool(r.get('split'))}
                 for r in self.groups_to_write()]
        if QMessageBox.question(
                self, '保存候选分栏',
                f'用当前 {len(clean)} 栏替换项目清单里的候选分栏？\n\n'
                f'在这里删掉的栏会真的从清单移除（先备份）；语句声明一个字都不动。'
        ) != QMessageBox.Yes:
            return
        from storygraph.story import statement_registry as SR
        res = SR.declare_attr_groups(self._root_path, clean, replace=True,
                                     clear=True)
        if not res.get('ok'):
            self._set_status('error', '没存成：' + str(res.get('reason') or '?'))
            return
        self.changed = True
        self.saved.append({'kind': 'groups', 'count': res.get('count', 0),
                           'path': res.get('path', '')})
        self._reload_registry()
        self._refresh_known()
        self._set_status('ok',
                         f'已保存 {res.get("count", 0)} 栏候选分栏（整段替换）。')
        self._manage_status.setText(
            f'已保存 {res.get("count", 0)} 栏候选分栏（整段替换）。')

    # ==================================================================
    #  清单页：删除 / 清空 / 重置
    # ==================================================================

    def _delete_selected_statement(self):
        item = self.manage_list.currentItem() if hasattr(self, 'manage_list') else None
        if item is None:
            self._manage_status.setText('先在列表里选中一条要删的语句。')
            return
        name = str(item.data(Qt.UserRole) or '')
        if QMessageBox.question(
                self, '从项目清单删除一条声明',
                f'从 storygraph.rules.json 删掉「{name}」？\n\n'
                f'会先把整份清单备份到 storygraph.rules.backup/；'
                f'其它声明与其它键原样保留。'
        ) != QMessageBox.Yes:
            return
        from storygraph.story import statement_registry as SR
        res = SR.remove_statement(self._root_path, name)
        if not res.get('ok'):
            QMessageBox.warning(self, '没删成', res.get('reason') or '未知错误')
            self._manage_status.setText('✕ ' + (res.get('reason') or '没删成'))
            return
        extra = '（清单空了，文件已删）' if res.get('deleted_file') else ''
        self._manage_status.setText(
            f'已从清单删除「{name}」{extra}。备份：{res.get("backup") or "（没有改动）"}')
        self.changed = True
        self.saved.append({'kind': 'delete', 'name': name,
                           'path': res.get('path', '')})
        self._reload_registry()

    def _clear_groups(self):
        from storygraph.story import statement_registry as SR
        try:
            _SR, reg = self._registry_now()
            count = len(reg.attr_groups or ())
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.dialog_learn.clear_groups.count', e)
            count = 0
        if QMessageBox.question(
                self, '清空候选分栏',
                f'把 {count} 栏候选分栏从项目清单里全部删掉？\n\n'
                f'语句声明与其它键原样保留；会先备份整份清单。'
        ) != QMessageBox.Yes:
            return
        res = SR.clear_attr_groups(self._root_path)
        if not res.get('ok'):
            QMessageBox.warning(self, '没清成', res.get('reason') or '未知错误')
            self._manage_status.setText('✕ ' + (res.get('reason') or '没清成'))
            return
        self._manage_status.setText(
            f'已清空 {res.get("count", 0)} 栏候选分栏。'
            f'备份：{res.get("backup") or "（没有改动）"}')
        self.changed = True
        self.saved.append({'kind': 'groups', 'count': 0,
                           'path': res.get('path', '')})
        self._reload_columns()

    def _reset_all(self):
        from storygraph.story import statement_registry as SR
        try:
            _SR, reg = self._registry_now()
            n_st = len(reg.declared or ())
            n_gr = len(reg.attr_groups or ())
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.dialog_learn.reset_all.count', e)
            n_st = n_gr = 0
        if QMessageBox.question(
                self, '重置自定义声明',
                f'清空项目清单里的 {n_st} 条语句声明与 {n_gr} 栏候选分栏？\n\n'
                f'host_hooks 或你自己加的其它键会原样保留；会先备份整份清单。'
        ) != QMessageBox.Yes:
            return
        res = SR.reset_declarations(self._root_path)
        if not res.get('ok'):
            QMessageBox.warning(self, '没重置成', res.get('reason') or '未知错误')
            self._manage_status.setText('✕ ' + (res.get('reason') or '没重置成'))
            return
        extra = '（清单空了，文件已删）' if res.get('deleted_file') else ''
        self._manage_status.setText(
            f'已清空 {res.get("statements", 0)} 条声明与 '
            f'{res.get("groups", 0)} 栏分栏{extra}。'
            f'备份：{res.get("backup") or "（没有改动）"}')
        self.changed = True
        self.saved.append({'kind': 'reset', 'name': '',
                           'path': res.get('path', '')})
        self._reload_columns()

    def _reload_columns(self):
        """清单变了之后把分栏页重灌一遍（删掉的栏不许还留在卡片上）。"""
        try:
            _SR, reg = self._registry_now()
            self.columns_editor.clear()
            self.columns_editor.load_columns(reg.attr_groups)
            self._sync_columns_to_fields(notify=False)
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.dialog_learn.reload_columns', e)
        self._reload_registry()

    # ==================================================================
    #  试切（解释工具：把这一行按格子切开，并说清为什么切不上）
    # ==================================================================

    def _refresh_form_head(self):
        """编辑页标题：正在编哪一条、它是从哪来的。"""
        decl = self._editing
        head = self.name.text().strip()
        if decl is not None:
            self._form_title.setText(f'语句「{head or decl.name}」')
            cols = len(getattr(decl, 'columns', []) or [])
            self._form_sub.setText(f'已声明 · {cols} 格 · 保存会覆盖项目清单里的这一条')
        else:
            self._form_title.setText(f'语句「{head}」' if head else '新语句')
            r = self.found.currentRow()
            if 0 <= r < len(self._pending) and self._pending[r].get('head') == head:
                count = self._pending[r].get('count', 0)
                self._form_sub.setText(f'待认领 · 剧本里出现 {count} 次')
            else:
                self._form_sub.setText('还没保存 · 保存后才会出现在表格里')
        self._refresh_range_note()

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
            view.setText('粘一行真实剧本，点「切一下」—— 就能看出你的格子切得对不对。'
                         + (('\n' + problems) if problems else ''))
            return
        # ⚠⚠ **允许粘整行**（2026-10-13 实测修，用户现场）：
        #   格子里描述的是"**开头词后面**的东西"，而人从剧本里复制的一定是整行
        #   （`show lan swim_suit happy`）—— 开头那个词不在格子里。
        #   不把它摘掉，试切就永远"多出来 1 个词 → 切不上"，
        #   而界面还只会说"检查顺序、关键字写法"（人怎么查都查不出来）。
        #   ⚠ 只在**开头整词等于开头词**时摘一个词，绝不猜别的。
        #
        #   ⚠⚠ 2026-10-17 补：**格子自己就是那个固定字时不许摘**。
        #   现在的界面能声明"固定几个字"了，`动作` 格常常就是 `show`；
        #   再摘一次等于把它的词偷走 → 第 1 格永远对不上（人会更懵）。
        head = self.name.text().strip()
        try:
            from storygraph.authoring import pattern as P
            # ⚠ 属性格默认"可不填"（`show/scene/hide`）：`show lan happy` 里
            #   服装省略是**合法**的（Ren'Py 的属性本来就可选），不该报切不上。
            slots = _with_optional_defaults(self.params.slots(), head)
            groups = self._cut_groups()
        except Exception as e:                  # noqa: BLE001 - 试切不许炸窗口
            swallow('ui_qt.dialog_learn.refresh_cut.prepare', e)
            view.setText(f'试切没做成：{type(e).__name__}: {e}')
            return
        first = slots[0] if slots else {}
        pattern_wants_head = (str(first.get('kind') or '') in ('keyword', 'optional')
                              and str(first.get('text') or '').strip() == head)
        #: 行首被摘掉了几个词 —— 报错说"第几个词"时要补回来
        #: （人粘的是整行，`第 2 个词` 指的必须是他**粘的那一行**里的第二个词）。
        dropped = 0
        if (head and not pattern_wants_head and line.split()
                and line.split()[0] == head):
            line = ' '.join(line.split()[1:])
            dropped = 1
            if not line:
                view.setText('只在行首看到了开头词「' + head + '」—— '
                             '后面还没写东西，先把那一行粘全。'
                             + (('\n' + problems) if problems else ''))
                return
        try:
            got = P.match_line(slots, line, groups=groups, word_offset=dropped)
        except Exception as e:                  # noqa: BLE001 - 试切不许炸窗口
            swallow('ui_qt.dialog_learn.refresh_cut', e)
            view.setText(f'试切没做成：{type(e).__name__}: {e}')
            return
        if not got.get('ok'):
            # ⚠ 光说"检查顺序"等于让人瞎猜（用户实测："为什么切不上呢？"）——
            #   把**具体是哪个词没人认领 / 第几格没吃到东西**念出来。
            # ⚠ 死格那句话已经在 `_why_not` 里说过了，别再让 `problems` 念一遍
            #   （同一句在同一个框里出现两次，人会以为界面出错了）。
            dead = any(str(s.get('kind') or '') in ('keyword', 'optional')
                       and not str(s.get('text') or '').strip() for s in slots)
            extra = self._slot_problems(skip=('固定段得写清',) if dead else ())
            view.setText('⚠ 切不上：' + self._why_not(got)
                         + (('\n' + extra) if extra else ''))
            return
        # ⚠ 值不在候选里的格子要**看得见**（`pattern._cell` 的 `ok`，
        #   2026-10-17 第二轮攻击修）：以前这个字段恒真，等于没有 ——
        #   人看到的是一排绿格子，库里存下的是一条不存在的服装/表情。
        def _one_cell(c: dict) -> str:
            name = c['column'] or '固定'
            if str(c.get('text') or '').strip() and not c.get('ok', True):
                name += '，不在候选里'
            return f'{c["text"]}（{name}）'

        cells = ' ｜ '.join(_one_cell(c) for c in got['cells'])
        view.setText('切出来是这样：' + cells
                     + f'　—　共 {len(got["cells"])} 格'
                     + (('\n' + problems) if problems else ''))

    def _cut_groups(self) -> dict:
        """
        试切用的候选表 —— **就是人眼前那一份**（分栏页里写的）。

        为什么要单独一层（2026-10-17 用户现场：预览里列着候选、试切却说"候选外"）：
          · 旧代码拿 `built_groups()` 现造字典，而 `build_group()` 会**校验**
            组名（必须是合法标识符）—— 编剧把栏目写成中文（「服装」）时它返回
            `ok=False, name=''`，于是这一栏的候选**被静默丢掉**；
          · 旧代码还用 `getattr(g, 'label', '')` 读一个 dict（恒为 `''`）——
            典型的"写得像对、其实没接上"。
        试切是**解释工具**，不是写盘校验：候选越全越好，绝不该比界面少。
        """
        out: dict = {}
        editor = getattr(self, 'columns_editor', None)
        if editor is None:
            return out
        try:
            rows = list(editor.rows() or ())
        except Exception as e:                  # noqa: BLE001 - 读不到就不加分栏
            swallow('ui_qt.dialog_learn.cut_groups', e)
            return out
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get('key') or '').strip()
            shown = str(row.get('shown') or '').strip()
            # ⚠ 用**与写盘同一个**分隔符口径（`,` `，` `、` 空白都算）——
            #   `load_columns` 恰恰是用「、」把已有候选拼进去的，手写 split(',')
            #   会把 `base_swimsuit、clothes2` 当成一个候选（= 又一种"看不见的
            #   对不上"）。这里不猜别的：认不出就当一个值。
            from storygraph.authoring.learn_statement import parse_choices
            opts = list(parse_choices(row.get('values')) or ())
            if not (key or shown):
                continue
            entry = {'label': shown or key, 'options': opts}
            # 两个名字都认：格子上的「分栏」可能是键，也可能被认成了显示名
            for alias in (key, shown):
                if alias:
                    out.setdefault(alias, entry)
        return out

    def _why_not(self, got: dict) -> str:
        """
        "切不上"的**具体原因**（哪个词没人认领 / 第几格没吃到），而不是一句万能话。

        为什么必须说具体（2026-10-13 用户实测）：
        > 为什么切不上呢？

        旧文案只会说"检查顺序、关键字写法" —— 人拿着这句话**怎么查都查不出来**。
        现在：剩哪些词 → 直接念出来，还顺手比一下就差在哪。
        """
        slots = self.params.slots() if getattr(self, 'params', None) else []
        try:
            from storygraph.authoring import pattern as P
            # ⚠ 与 `_refresh_cut` **同一份**格子：属性格默认"可不填"
            #   （`show/scene/hide`），否则这里会把"合法空着"说成"没吃到东西"。
            parsed = P.parse_pattern(_with_optional_defaults(
                slots, self.name.text() if hasattr(self, 'name') else ''))
        except Exception as e:                  # noqa: BLE001 - 诊断不许炸窗口
            swallow('ui_qt.dialog_learn.why_not.parse', e)
            parsed = [dict(s) for s in slots if isinstance(s, dict)]
        leftover = [str(w) for w in (got.get('leftover') or []) if str(w)]
        # ⚠ **空着合法的格子不许念**（2026-10-18）：`match_line` 的 `missing`
        #   是"这一格没有值"，而 `multi` / 可填可不填 / 可选段空着本来就正常 ——
        #   挨个念一遍等于把人往错的方向推（他会去补一个不需要补的格）。
        missing = []
        for i in (got.get('missing') or []):
            try:
                s = parsed[int(i)]
            except Exception:                   # noqa: BLE001 - 认不出就照旧说
                missing.append(i)
                continue
            if str(s.get('kind') or '') in ('keyword', 'optional') \
                    or s.get('multi') or not s.get('required', True):
                continue
            missing.append(i)
        parts = []
        reason = str(got.get('reason') or '')
        # ⓪ 词法层的错：**空引号** / 引号没关上（2026-10-18 第三轮攻击）。
        #
        #   ⚠ 它必须**排第一**：这是这一行真正的病根，后面那些"这些词没人认领"
        #     "这些格子没吃到东西" 全是它的**后果** —— 先说后果等于让人去改错地方
        #     （与下面 ① 死格同一条道理）。
        #   ⚠ 序号已经由 `match_line` 补过行首被摘掉的词数，所以直接用 `reason`。
        lex = dict(got.get('lex_problem') or {})
        if lex:
            parts.append(reason or str(lex.get('why') or '引号这一处不对'))
        # ① 死格（永远对不上的固定段）—— 它比"候选外"更接近真正原因
        #
        #   ⚠⚠ 2026-10-17 用户现场：`show lan base_swimsuit happy` 判"切不上"，
        #   而预览里明明列着这两个词。真凶是第 2 格选了「可出现可不出现的一段」
        #   —— 旧版那类格子**没有"是哪几个字"的输入框**，`text` 恒为空：
        #   它一个词都吃不掉、也不报错，于是后面的格子整体错位，最后由"表情"
        #   的候选背锅（人只会去改候选，改错地方）。
        for i, s in enumerate(parsed, 1):
            kind = str(s.get('kind') or '')
            if kind not in ('keyword', 'optional'):
                continue
            if str(s.get('text') or '').strip():
                continue
            label = str(s.get('column') or '').strip()
            kind_name = ('可出现可不出现的一段' if kind == 'optional'
                         else '固定几个字')
            parts.append(
                f'第 {i} 格' + (f'「{label}」' if label else '')
                + f'是「{kind_name}」，却没写"是哪几个字" —— 这一格永远对不上，'
                  f'还会把后面的格子整体挤错位（先补上它）')
        # ② 被"限死"挡下的词：说清是哪一格、有多少候选、它本来是谁的
        if got.get('rejected') is not None:
            try:
                rej = int(got['rejected'])
            except (TypeError, ValueError):
                rej = -1
            if 0 <= rej < len(parsed):
                s = parsed[rej]
                word = str(got.get('rejected_word') or '')
                label = str(s.get('column') or s.get('text') or '').strip()
                cands = list(s.get('options') or [])
                who = f'第 {rej + 1} 格' + (f'「{label}」' if label else '')
                if not cands:
                    parts.append(f'{who}勾了"只能从这里选"，但一个候选都没给 '
                                 f'—— 这一格谁都不可能通过（先填候选，'
                                 f'或取消那个勾）')
                else:
                    msg = (f'{who}的 {len(cands)} 个候选里没有'
                           + (f'「{word}」' if word else '这个词'))
                    owners = [j for j, t in enumerate(parsed)
                              if j != rej and word
                              and word in (t.get('options') or [])]
                    if owners:
                        j = owners[0]
                        o = parsed[j]
                        oname = str(o.get('column') or o.get('text') or '').strip()
                        msg += (f'；但它就是第 {j + 1} 格'
                                + (f'「{oname}」' if oname else '')
                                + '的候选 —— 检查这两格的顺序，'
                                  '或那一格的类型选错了')
                    elif word:
                        # ⚠ 同一个人的**另一种写法**（2026-10-18 用户现场：
                        #   "明明它只是换成变量名又不认得了"）：`lan` 是「蓝」的
                        #   立绘 tag，而候选里写的是显示名「蓝」。这不是"顺序错"，
                        #   是候选存了**脚本里不能写的那一种写法** —— 说出来。
                        try:
                            from storygraph.authoring.learn_statement import (
                                explain_role_spelling)
                            why = explain_role_spelling(
                                self._root_path, self.name.text(), word, cands,
                                roles=self._roles_cached())
                        except Exception as e:      # noqa: BLE001 - 解释不许炸
                            swallow('ui_qt.dialog_learn.why_not.role', e)
                            why = ''
                        if why:
                            msg += '；' + why
                    parts.append(msg)
        # ⚠⚠ 2026-10-17：`reason` 现在**精确到词**（"第 3 个词「dirty」没人认领"），
        #   所以"这些词没人认领：dirty"那行概述就成了重复 —— 有它就不再念一遍。
        if leftover and '没人认领' not in reason:
            parts.append('这些词没人认领：' + '、'.join(leftover[:6])
                         + ('…' if len(leftover) > 6 else ''))
        explained = got.get('rejected') is not None
        if missing and not explained:
            names = []
            for i in missing:
                try:
                    s = slots[int(i)]
                    names.append(str(s.get('column') or s.get('text') or f'第{i + 1}格'))
                except Exception:               # noqa: BLE001 - 认不出就说第几格
                    names.append(f'第{int(i) + 1}格')
            parts.append('这些格子没吃到东西：' + '、'.join(names[:6]))
        if not parts:
            parts.append(reason or '顺序或关键字不对')
        # ⚠ 兜底也要说人话：`match_line` 有时只在 `reason` 里说细节（例如
        #   "第 2 格要的关键字是 `at`，这里是 `swim_suit`"）——那种句子比
        #   我们上面的概述更准，一起带上。
        #   ⚠ 但"候选外"那句已经由上面第 ② 段说得更具体了，再念一遍是噪声。
        if reason and not explained and reason not in parts:
            parts.append(reason)
        return '；'.join(parts)

    def _slot_problems(self, skip=()) -> str:
        """格子本身的毛病汇总成人话（`pattern.problems_of` 的界面接线）。"""
        params = getattr(self, 'params', None)
        if params is None:
            return ''
        try:
            from storygraph.authoring import pattern as P
            # ⚠ 直接交 `slots()` —— 它才是"界面 → 格子"的**唯一**形状，
            #   少抄一个键就会让某条检查悄悄失效（实测：`limit` 漏了，
            #   于是"勾了限死却没给候选"这条毛病在界面上永远不报）。
            items = list(P.problems_of(params.slots()) or ())
        except Exception as e:                  # noqa: BLE001 - 汇总不许炸窗口
            swallow('ui_qt.dialog_learn.slot_problems', e)
            return ''
        if skip:
            items = [x for x in items
                     if not any(str(k) in str(x) for k in skip)]
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
        except Exception as e:                  # noqa: BLE001 - 查询不该炸窗口
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
            lines.append(f'⚠ 有 {len(cards)} 格还没起名字 —— 按保存也不会写进去，'
                         f'里面的候选会一起丢掉：')
            for i, card in enumerate(cards, 1):
                hint = str(getattr(card, 'missing_name_hint', lambda: '')() or '')
                lines.append(f'  · 第 {i} 格'
                             + (f'（建议用「{hint}」）' if hint else ''))
            lines.append('  在「表格里这一列叫什么」里填一个名字，'
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

    def _completion_words(self) -> list:
        """开头词那个输入框的补全词表：剧本里见过的 + 引擎本来就有的。"""
        words = [str(r.get('head') or '') for r in self._discovered]
        try:
            words += list(self._engine_heads())
        except Exception as e:                  # noqa: BLE001 - 补全不该炸窗
            swallow('ui_qt.dialog_learn.completion_words', e)
        return sorted({w for w in words if w})

    def _refresh_range_note(self):
        """
        开头词下面那句话 —— **当场回答"这词引擎认不认识、写了会怎样"**。

        用户 2026-10-10 的原话：
        > "我第一反应是填 show。但是等一下，show 是 Ren'Py 的原生指令！
        >  如果我把 show 注册成自定义语句，会不会覆盖引擎原有的 show？"
        """
        lab = getattr(self, 'mode_note', None)
        if lab is None:
            return
        head = str(self.name.text()).strip()
        if not head:
            lab.setText('填这段剧本的第一个词（英文、下划线；别名用空格隔开，例如 mycue cue）。'
                        '工具认不认得它、写了会不会动引擎，写上来这里会当场告诉你。')
        elif self.statement_mode() == 'builtin':
            lab.setText('引擎本来就认识这个词。在这里给它加格只会让「工具」多几列、'
                        '多几个下拉，不会动引擎；真正会替换引擎的是项目里的 '
                        'renpy.register_statement。')
        else:
            lab.setText('工具还不认识这个词。要让「引擎」也认识它，项目里得有一段 '
                        'renpy.register_statement；这里保存只让「工具」认识它。')

    def _refresh_preview(self, *_a):
        if getattr(self, '_loading', False):
            return
        self._sync_optional_defaults()
        self._refresh_form_head()
        try:
            built = self.built()
        except Exception as e:                  # noqa: BLE001 - 预览不许炸窗口
            swallow('ui_qt.dialog_learn.refresh_preview', e)
            return
        text = declaration_preview(built, path=str(self._root_path or ''))
        notice = self._missing_name_notice()
        if notice:
            text = notice + '\n\n' + text
        # ⚠ "候选/固定字配错了"这类毛病以前只在试切那一行里出现 —— 试切没粘东西
        #   时它一个字都不说，人以为格子没问题。预览里再放一遍（去重）。
        problems = self._slot_problems()
        if problems and problems not in text:
            text += '\n\n' + problems
        preview = getattr(self, 'preview', None)
        if preview is not None:
            preview.setPlainText(text)
        self._refresh_cut()
        btn = getattr(self, '_ok_btn', None)
        ok = bool(built.get('ok'))
        # ⚠ 缺列名的格子会被后端丢掉 —— 那是**静默的数据丢失**，
        #   所以这里必须挡住保存键（人补上名字，或者把这一格删掉）。
        if self.missing_name_cards():
            ok = False
        if btn is not None:
            btn.setEnabled(ok)
        if self.pages.currentIndex() == PAGE_FORM:
            self._update_form_status(built, ok)

    def _update_form_status(self, built: dict, ok: bool):
        """底部那一行：现在按保存会发生什么（或为什么按不了）。"""
        if not built.get('ok'):
            self._set_status('error', self._built_problem(built))
            return
        missing = self.missing_name_cards()
        if missing:
            self._set_status('warn',
                             f'⚠ 有 {len(missing)} 格还没起列名 —— 它们不会被保存。')
            return
        head = str(built.get('name') or '')
        verb = '更新' if self._editing is not None else '新增'
        self._set_status('ok', f'按「保存这条语句」会{verb}「{head}」'
                               f'（{len(built.get("columns") or [])} 格 → 表格里的列）。')

    # ==================================================================
    #  引擎验证
    # ==================================================================

    def set_verify_available(self, available: bool, reason: str = ''):
        """**能点就必须有反应**：不可用要灰化 + 写清为什么。"""
        self._verify_btn.setEnabled(bool(available))
        try:
            self._verify_btn.setToolTip(
                '点一下真跑一次引擎（慢，要 SDK）' if available
                else (reason or '这里暂时没法验证（需要 SDK 与真项目）'))
        except Exception as e:                  # noqa: BLE001 - 提示设不上不该炸
            swallow('ui_qt.dialog_learn.set_verify_available.tooltip', e)

    def _do_verify(self):
        """「用引擎验证」：把当前填的开头词交给注入的回调（跑引擎是它的事）。"""
        fn = self.on_verify
        if fn is None:
            return
        head = self.name.text().strip()
        if not head:
            self.preview.setPlainText('先填「开头词」，再验证 —— '
                                      '验证要拿它去章节里找用到的地方。')
            self._verify_view.setText('先填「开头词」，再验证。')
            self._verify_view.setVisible(True)
            return
        self._verify_btn.setEnabled(False)
        self._verify_btn.setText('正在跑引擎…')
        self.preview.setPlainText(f'正在验证「{head}」：找出用到它的章节 → '
                                  f'注入证据 → 跑 test → 两把锁判定…\n'
                                  f'（要几秒到几十秒，请稍等）')
        QApplication.processEvents()
        try:
            text = fn(head)
        except Exception as e:                  # noqa: BLE001 - 验证不该炸窗口
            text = f'验证没做成：{type(e).__name__}: {e}'
        finally:
            self._verify_btn.setText('用引擎验证')
            self.set_verify_available(bool(self.on_verify))
        self.preview.setPlainText(str(text or ''))
        first = str(text or '').strip().splitlines()
        self._verify_view.setText('引擎说：' + (first[0] if first else '（没有输出）'))
        self._verify_view.setVisible(True)

    # ==================================================================
    #  模式 / 杂项
    # ==================================================================

    def statement_mode(self) -> str:
        """`builtin`（引擎本来就认识这个词）/ `custom`（项目自己加的）。"""
        head = str(self.name.text()).strip()
        if not head:
            return 'custom'
        try:
            return 'builtin' if head in set(self._engine_heads()) else 'custom'
        except Exception as e:                  # noqa: BLE001 - 问不到就按自定义
            swallow('ui_qt.dialog_learn.statement_mode', e)
            return 'custom'

    def _engine_heads(self) -> list:
        """
        引擎自己就有的写法 —— **优先问引擎**，问不到退回词表（不硬编清单）。

        ⚠ 结果要**缓存**：它被 `statement_mode()` 问、而那句话在每次按键刷新
        预览时都会跑一遍 —— 不缓存就是"每敲一个字读一次引擎事实 JSON"。
        """
        cached = getattr(self, '_engine_heads_cache', None)
        if cached is not None:
            return list(cached)
        heads = []
        try:
            from storygraph.core import engine_facts as EF
            path = EF.facts_path(self._root_path) if self._root_path else None
            if path is not None and path.is_file():
                facts = EF.loads(path.read_text(encoding='utf-8'))
                heads = sorted(EF.statement_names(facts))
        except Exception as e:                  # noqa: BLE001 - 没事实也能用
            swallow('ui_qt.dialog_learn.engine_heads', e)
        if not heads:
            from storygraph.core.vocabulary import RENPY_KNOWN_HEADS
            heads = sorted(RENPY_KNOWN_HEADS)
        self._engine_heads_cache = heads[:400]
        return list(self._engine_heads_cache)

    def _choice_sources(self) -> list:
        """「从项目里拿」的来源清单（纯函数在 `authoring.learn_statement`）。"""
        try:
            from storygraph.authoring.learn_statement import choice_sources
            return choice_sources(self._root_path, self.name.text().strip())
        except Exception as e:                  # noqa: BLE001 - 取不到就空
            swallow('ui_qt.dialog_learn.choice_sources', e)
            return []

    def eventFilter(self, obj, event):          # noqa: N802 - Qt 接口
        """
        ⚠ 输入框里回车 = **焦点去下一格**（一路往下填），而不是"什么都没发生"；
        `sample` 那一格回车 = **直接试切**（比跳走更贴意图）。
        保存键已 `setAutoDefault(False)`，所以回车**不会**触发写盘。
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
        except Exception as e:                  # noqa: BLE001 - 算不出就用期望值
            swallow('ui_qt.dialog_learn.fit_to_screen', e)
        return int(want_w), int(want_h)


def _with_optional_defaults(slots, head):
    """`pattern.with_optional_defaults` 的兜底包装（读不到模块就原样返回）。"""
    try:
        from storygraph.authoring import pattern as P
        return P.with_optional_defaults(slots, head)
    except Exception as e:                      # noqa: BLE001 - 不许炸界面
        swallow('ui_qt.dialog_learn.with_optional_defaults', e)
        return list(slots or ())


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


def _field_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty('role', 'fieldLabel')
    lab.setWordWrap(True)
    return lab


def _has_cjk(text: str) -> bool:
    """这句里有没有汉字？（只用来挑"举哪个例子"更像人话）"""
    return any('\u4e00' <= ch <= '\u9fff' for ch in str(text or ''))


def _step(number: int, title: str) -> QWidget:
    """「1  它以哪个词开头」—— 序号是个小圆牌，标题是这一段的重点。"""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, T.SPACE['md'], 0, 0)
    lay.setSpacing(T.SPACE['sm'])
    num = QLabel(str(number))
    num.setProperty('role', 'step')
    num.setAlignment(Qt.AlignCenter)
    num.setFixedSize(20, 20)
    lay.addWidget(num)
    lab = QLabel(title)
    lab.setProperty('role', 'stepTitle')
    lab.setWordWrap(True)
    lay.addWidget(lab, 1)
    return w


__all__ = ['CustomStatementDialog', 'PAGE_LIST', 'PAGE_FORM', 'PAGE_GROUPS',
           'PAGE_MANIFEST']
