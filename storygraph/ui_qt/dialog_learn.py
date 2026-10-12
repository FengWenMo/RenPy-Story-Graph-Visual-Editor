"""
「自定义模块」弹窗（教工具认识项目自己的写法）
================================================

用户 2026-10-10 报的缺口：

> 你说支持自适应的解析……但是我并没有找到一个入口可以教这个工具学会
> 我自定义的项目模块……至少我想要个能够自定义的模块入口放在最上方

这个弹窗就是那个入口。它把后端的四步摆到一屏上：

    **发现**（扫项目里还不认识的写法，带出现次数与原文样例）
        → **勾选/填写**（语句名、中文名、说明、是否带块、参数与候选值）
        → **写进 `storygraph.rules.json`**（`declare_statement`，唯一写入口）
        → 立刻生效（清单缓存作废 → 表里多出参数列与候选值）

⚠ 判据一条都不在这里：表单 → 后端参数是 `authoring.learn_statement.
build_declaration`（纯函数，有判据），写盘是 `declare_statement`（有判据）。
本文件只负责"把事实摆出来、把人的输入收上去"。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFrame, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from storygraph.authoring.learn_statement import (build_declaration,
                                                  declaration_preview)
from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.param_cards import GroupList, ParamList


class CustomStatementDialog(QDialog):
    """
    教工具认识一条自定义写法。

    左侧 = **发现**（项目里还没声明、但脚本里真的在用的写法）；
    右侧 = 填写 + 预览。左边点一行会把名字填进右边（参数留空，人自己加）。

    ⚠ 参数与词组**不再是表格**（用户 2026-10-11 实测："表格太小了！多个参数
    还要下拉看……编剧仍然看不明白"）→ 换成 `ui_qt/param_cards.py` 的**宽卡片**：
    一个参数一张卡、竖着排、候选值敲回车变成可点方块。
    """

    def __init__(self, parent=None, project_root=None, declares=None,
                 discovered=None, registry_report: str = '',
                 declared_groups=None, on_verify=None):
        super().__init__(parent)
        self.setWindowTitle('自定义模块 —— 教工具认识项目自己的写法')
        theme.apply_to(self)
        self._root_path = project_root
        self._declares = list(declares or [])
        self._discovered = list(discovered or [])
        self._registry_report = registry_report or ''

        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])
        head = QLabel(
            '项目里自己的写法（swim_suit / mycue 这类）默认逐字保留，'
            '但工具不知道它是什么。在这里写一条声明，它就变成「认识的语句」：'
            '参数会成为表格里的列、候选值会成为下拉。'
            '声明落在项目根的 storygraph.rules.json（可评审、可 diff）。')
        head.setWordWrap(True)
        root.addWidget(head)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        # —— 左：发现 + 已有声明 ——
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(T.SPACE['xs'])
        lv.addWidget(QLabel('① 项目里还没声明的写法'
                            + (f'（发现 {len(self._discovered)} 个）'
                               if self._discovered else '')))
        self.found = QTableWidget(len(self._discovered), 4)
        self.found.setHorizontalHeaderLabels(['写法', '出现', '带块', '原文样例'])
        self.found.verticalHeader().setVisible(False)
        fh = self.found.horizontalHeader()
        fh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        fh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        fh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        fh.setSectionResizeMode(3, QHeaderView.Stretch)
        for i, rec in enumerate(self._discovered):
            self.found.setItem(i, 0, QTableWidgetItem(rec.get('head', '')))
            self.found.setItem(i, 1, QTableWidgetItem(str(rec.get('count', 0))))
            self.found.setItem(i, 2, QTableWidgetItem(
                '是' if rec.get('block') else ''))
            sample = (rec.get('samples') or [('', '')])[0]
            self.found.setItem(i, 3, QTableWidgetItem(str(sample[1])))
        self.found.itemSelectionChanged.connect(self._on_pick)
        lv.addWidget(self.found, 1)
        if not self._discovered:
            lv.addWidget(self._hint(
                '没发现未声明的写法 —— 要么项目里没有自定义语句，'
                '要么它们已经在清单或 CDS 里声明过了（见下方）。'))

        lv.addWidget(QLabel('② 已经声明过的（rules.json + 脚本里的 CDS）'))
        self.declared = QPlainTextEdit()
        self.declared.setReadOnly(True)
        self.declared.setPlainText(self._registry_report
                                   or '（还没有任何声明）')
        self.declared.setMaximumHeight(120)
        lv.addWidget(self.declared)

        # —— 右：填写 ——
        # ⚠ 结构（用户 2026-10-11："多个参数还要下拉看"）：
        #   顶部 4 行基本信息**固定**，下半部分（参数 + 词组）放进**滚动区**，
        #   按钮条**固定在底** —— 这样加多少参数都不会把"写进清单"挤出屏幕。
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(T.SPACE['sm'])

        rv.addWidget(QLabel('③ 填一条声明'))

        # ⭐⭐ 2026-10-11（程序员撞墙记录 卡点 1）：
        #   "我第一反应是填 show。但是等一下，show 是 Ren'Py 的原生指令！
        #    如果我把 show 注册成自定义语句，会不会覆盖引擎原有的 show？"
        #   —— 界面**必须在这里回答**，否则用户从第一步就卡死。
        #   答案（真机核过）：`rules.json` 里的声明**只影响工具**（表格列、下拉），
        #   **不碰引擎**；真正替换引擎的是项目里的 `renpy.register_statement(...)`
        #   （CDS 覆盖内置 → 最高级警告 N5）。
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel('你要教它认识的是'))
        self.mode = QComboBox()
        # ⚠ 顺序 = 默认项：**保持我们自己写的为默认**（老行为不变，既有的
        #   发现→声明那条链子不受影响）；给引擎自带的写法加列放第二项，
        #   但切换时的说明**照样当场给**（用户卡点 1 的答案）。
        self.mode.addItem('我们自己的新写法', 'custom')
        self.mode.addItem('引擎自带的写法（只给它加表格列）', 'builtin')
        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode, 1)
        rv.addLayout(mode_row)
        self.mode_note = self._hint('')
        rv.addWidget(self.mode_note)

        row = QHBoxLayout()
        row.addWidget(QLabel('语句名'))
        self.name = QLineEdit()
        self.name.setPlaceholderText('例如 swim_suit（多个写法用空格分隔：'
                                     'mycue cue）')
        row.addWidget(self.name, 1)
        # 选了"引擎自带"时，改从**下拉里挑**（不让人猜该填 show 还是 show 角色 服装）
        self.builtin_name = QComboBox()
        self.builtin_name.setEditable(True)
        self.builtin_name.setToolTip('这些是引擎自己就有的写法；挑一个就行')
        for head in self._engine_heads():
            self.builtin_name.addItem(head)
        self.builtin_name.currentTextChanged.connect(self._on_builtin_picked)
        self.builtin_name.setVisible(False)
        row.addWidget(self.builtin_name, 1)
        rv.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel('中文名'))
        self.label = QLineEdit()
        self.label.setPlaceholderText('例如 泳装切换（只是给人看的）')
        row2.addWidget(self.label, 1)
        rv.addLayout(row2)

        self.block = QCheckBox('它后面跟一个块（mycue "闪电": 这种，'
                               '块体逐字保真）')
        rv.addWidget(self.block)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel('说明'))
        self.docs = QLineEdit()
        self.docs.setPlaceholderText('例如 切到泳装立绘（会进 describe / 提示）')
        row3.addWidget(self.docs, 1)
        rv.addLayout(row3)

        # ---- 滚动区：参数 + 词组 ----
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(T.SPACE['sm'])

        pv = QHBoxLayout()
        pv.addWidget(QLabel('这个动作要填的东西（一个一张卡，竖着排）'))
        pv.addStretch(1)
        add = QPushButton('＋ 加一个')
        add.clicked.connect(lambda: self.params.add_param())
        rm = QPushButton('删掉最后一个')
        rm.clicked.connect(self._remove_param)
        pv.addWidget(add)
        pv.addWidget(rm)
        iv.addLayout(pv)

        # ⭐⭐ 「试切」：用户卡死的第一步就是"这一行会被怎么切、语句名该填什么"。
        #   这里让他**粘一行真实剧本**，当场看到它被切成几格、每格对应哪个列名。
        cut_row = QHBoxLayout()
        cut_row.addWidget(QLabel('拿一行真实剧本试试'))
        self.sample = QLineEdit()
        self.sample.setPlaceholderText('例：show 小明 泳装 开心 '
                                       '（从剧本里粘一行最准）')
        cut_row.addWidget(self.sample, 1)
        self.cut_try = QPushButton('试切')
        self.cut_try.clicked.connect(self._refresh_cut)
        cut_row.addWidget(self.cut_try)
        iv.addLayout(cut_row)
        self.cut_view = QLabel()
        self.cut_view.setWordWrap(True)
        self.cut_view.setProperty('role', 'caption')
        iv.addWidget(self.cut_view)

        # ⭐ 用户 2026-10-11 实测反馈后的重做（`ui_qt/param_cards.py`）：
        #   "表格太小了！多个参数还要下拉看……编剧仍然看不明白"
        #   → 三列表格换成**一行一张宽卡**（自带"删掉这一项"），
        #     候选值敲回车就变成可点的小方块（不用记逗号）。
        # ⭐ 2026-10-12：候选值还能**从项目里白拿**（用户："我的真实项目内有无
        #   数个表情！不可能一个个输入"）—— 来源由 `_choice_sources` 提供，
        #   卡片只负责把清单摆出来（工具不猜"这格是不是表情"）。
        self.params = ParamList(on_change=self._refresh_preview,
                                sources_provider=self._choice_sources)
        iv.addWidget(self.params, 1)
        # 一进来就有一张空卡，省得人去想"要先点加号吗"
        self.params.add_param()

        # ⚠ 预览搬到**左栏底部**（原来在右栏、150px 高，把参数卡挤得只剩一卡多）
        #   —— 用户抱怨"多个参数还要下拉看"，一半原因就是这个。
        #   ⚠ `left` 是 QWidget，加东西要用它的布局 `lv`（不是 left.addWidget）。
        lv.addWidget(QLabel('这样写对吗（右边一改，这里立刻变）'))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(120)
        lv.addWidget(self.preview, 1)

        self.name.textChanged.connect(self._refresh_preview)
        self.label.textChanged.connect(self._refresh_preview)
        self.docs.textChanged.connect(self._refresh_preview)
        self.block.toggled.connect(self._refresh_preview)
        self.sample.textChanged.connect(self._refresh_cut)
        self._refresh_preview()

        # —— 词组（分栏显示"服装 / 表情"这类）——
        #
        # 用户 2026-10-10："实际上需要分组显示……因为我这个要求通用！所以一般人
        # 其实是没有服装这个选项的！所以事实上应当通过 B 这个自定义模块，
        # 做到把服装跟表情这种东西分开！"
        #
        # ⚠ 默认**收起**（用户 2026-10-11："多个参数还要下拉看"）：词组是高级功能，
        #   一般人用不上 —— 收起后纵向空间全给参数卡，要分组时再点开。
        #   注意 `declared_groups` 仍然照常填进去，勾开就能看到，不会丢。
        box_groups = QGroupBox('词组（可选 · 高级）—— 把立绘候选分成"服装 / 表情"这样的栏')
        box_groups.setCheckable(True)
        # ⚠ `QGroupBox` 一旦 setCheckable，**默认就是勾上的** —— 不显式取消，
        #   界面会显示"勾着但内容不见了"（判据当场抓到）。先取消，再连信号。
        box_groups.setChecked(False)
        gv = QVBoxLayout(box_groups)
        gv.setSpacing(T.SPACE['xs'])
        gv.addWidget(self._hint(
            '你写"服装"就是服装，写"姿势"就是姿势（中文名才是编剧看到的那栏字）。'
            '留空候选词的那组 = 兜底。'))
        self.groups = GroupList(on_remove_group=self._remove_group)
        gv.addWidget(self.groups)
        grow = QHBoxLayout()
        add_g = QPushButton('＋ 加一组')
        add_g.clicked.connect(self._add_group)
        rm_g = QPushButton('－ 删这一组')
        rm_g.clicked.connect(self._remove_group)
        grow.addWidget(add_g)
        grow.addWidget(rm_g)
        grow.addStretch(1)
        self.write_groups = QCheckBox('把词组一起写进清单')
        self.write_groups.setChecked(False)
        self.write_groups.toggled.connect(self._refresh_preview)
        grow.addWidget(self.write_groups)
        gv.addLayout(grow)
        self.groups_box = box_groups
        # ⭐ **词组表一变，每一格的下拉跟着变**（用户卡点 5：词组到底怎么用 ——
        #   它就是"这一格的候选值从哪来"，所以必须挂在格子上、并跟着表走）
        self._sync_groups_to_slots()

        def _toggle_groups(on: bool):
            """
            ⚠ `QGroupBox.setCheckable` **只画一个勾选框，不会自己收起内容** ——
            必须真的显示/隐藏。收起时把最大高度压到一行，空间让给参数卡。
            """
            for w in (self.groups, add_g, rm_g, self.write_groups):
                w.setVisible(bool(on))
            box_groups.setMaximumHeight(16777215 if on else 30)
        box_groups.toggled.connect(_toggle_groups)
        _toggle_groups(False)                    # 默认收起
        iv.addWidget(box_groups)
        self.groups.load_groups(declared_groups or [])

        scroll.setWidget(inner)
        rv.addWidget(scroll, 1)                  # 滚动区吃掉右栏剩余高度

        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([400, 620])
        self.resize(1100, 720)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._ok_btn = box.button(QDialogButtonBox.Ok)
        self._ok_btn.setText('写进 storygraph.rules.json')
        self._ok_btn.setProperty('variant', 'primary')
        # ⚠⚠ 2026-10-12（用户实测）：回车**不许**等于"确定"——
        #   `learn_custom_statement` 里 `exec() == Accepted` 的下一句就是
        #   `declare_statement`（**写盘**）。按钮是 autoDefault 时，在任何
        #   输入框里按一下回车都会走这条路 = "顺手按个回车就把清单写了"。
        self._ok_btn.setAutoDefault(False)
        box.button(QDialogButtonBox.Cancel).setAutoDefault(False)
        box.button(QDialogButtonBox.Cancel).setText('取消（什么都不改）')
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

        # ⭐ 「验证它能跑」（第三层）：单独一行（**放进主按钮盒会挤成一行**，
        #    而且它需要一段说明 —— 那说明本身就是"工具不教错人"的一部分）。
        #
        # ⚠ 这段说明是**真机挖出来的硬事实**（引擎 `script.py:447-452`）：
        #    `rules.json` 只让**我们的工具**认识这个写法；
        #    **引擎**要靠项目自己的 `renpy.register_statement` ——
        #    而且 `init python` 注册**赶不上解析期**（一组出错、后面全不解析），
        #    所以"声明了却跑不起来"是常态，必须**当面说清**。
        self.on_verify = on_verify
        grow2 = QHBoxLayout()
        self._verify_btn = QPushButton('▶ 验证它能跑')
        self._verify_btn.clicked.connect(self._do_verify)
        grow2.addWidget(self._verify_btn)
        self._verify_hint = QLabel(
            '真跑一次引擎：找出用到这个写法的章节 → 注入证据 → 跑 test → '
            '两把锁判定（引擎报 PASSED 且证据文件真的存在）。'
            '这里写声明只让「工具」认识它；要让「引擎」也认识，项目里还得有它的 '
            'renpy.register_statement（注意：init python 里注册赶不上解析期）。'
            '没验证成 ≠ 失败：我们会如实分成「通过 / 失败 / 没证明成」。')
        self._verify_hint.setProperty('role', 'caption')
        self._verify_hint.setWordWrap(True)
        grow2.addWidget(self._verify_hint, 1)
        rv.addLayout(grow2)
        self.set_verify_available(bool(on_verify))
        # ⚠ mode 的初值要刷一次（否则引擎自带的下拉/说明不会显示）
        self._on_mode_changed()
        # ⚠ 按钮建好之后**再刷一次**：构造早期那次刷新时按钮还不存在，
        # 于是"名字空着"这个初值状态没被同步到可用性上（实测：按钮一开始可点）。
        self._refresh_preview()
        # ⚠ 2026-10-12（用户实测）：输入框里回车 = **焦点去下一格**
        #   （能一路往下填），而不是"什么都没发生"。候选值那个框**不在这里**
        #   —— 它的回车是"加一个候选"（`param_cards.ChipEdit` 自己装了过滤器）。
        for w in (self.name, self.label, self.docs, self.sample):
            w.installEventFilter(self)

    # ---------------- 内部 ----------------

    def eventFilter(self, obj, event):              # noqa: N802 - Qt 接口
        """
        ⚠ 2026-10-12（用户实测"回车按了没反应 / 没有下一个"）：

        两件事一起定规矩 ——
          · 普通输入框里回车 = **焦点去下一格**（一路往下填），回车终于有语义；
          · `self.sample`（拿一行真实剧本）回车 = **直接试切**（比跳走更贴意图）；
          · 底部按钮已 `setAutoDefault(False)`，所以回车**再也不会**触发
            `accept()` —— 那在 `learn_custom_statement` 里等于顺手写盘。

        候选值输入框不走这里（它的回车是"加一个候选"，见 `param_cards`）。
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

    @staticmethod
    def _hint(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setStyleSheet(f"color: {T.t('text.muted')};")
        return lab

    def _remove_param(self):
        """删掉最后一张参数卡（卡片自带"删掉这一项"，这个是批量入口）。"""
        self.params.remove_current()
        self._refresh_preview()

    def _on_pick(self):
        """点左边的发现项 → 把名字填进右边（带块也顺手勾上）。"""
        r = self.found.currentRow()
        if not (0 <= r < len(self._discovered)):
            return
        rec = self._discovered[r]
        self.name.setText(str(rec.get('head') or ''))
        self.block.setChecked(bool(rec.get('block')))
        self._refresh_preview()

    def set_verify_available(self, available: bool, reason: str = ''):
        """
        验证按钮的可用性（**能点就必须有反应**：不可用要灰化 + 写清为什么）。

        `reason` 用来把"为什么不给你点"说清楚（例如"这个写法引擎不认识，
        验证只会得到'没证明成'"）。
        """
        self._verify_btn.setEnabled(bool(available))
        try:
            if available:
                self._verify_btn.setToolTip('点一下真跑一次引擎')
            else:
                self._verify_btn.setToolTip(
                    reason or '这里暂时没法验证（需要 SDK 与真项目）')
        except Exception as e:                  # noqa: BLE001 - 提示设不上不该炸
            swallow('ui_qt.dialog_learn.set_verify_available.tooltip', e)
    def _do_verify(self):
        """
        「▶ 验证它能跑」：把当前填的动作名交给注入的回调（**跑引擎是它的事**）。

        为什么回调而不是在这里跑：这里只该管界面；跑引擎（拷副本、跑 `test`、
        两把锁判定）在 `pipeline.statement_probe.verify_head`，由窗口注入。
        结果由回调写进预览区 —— 用户在同一个窗口里就能看到结论。
        """
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

    def _add_group(self):
        """加一组 → 立刻同步到每一格的下拉（不然新词组在格子上看不到）。"""
        self.groups.add_group()
        self._sync_groups_to_slots()

    def _remove_group(self):
        self.groups.remove_current()
        self._sync_groups_to_slots()
        self._refresh_preview()

    def built_groups(self) -> list:
        """当前词组表 → 后端参数（`learn_statement.build_group`）。"""
        from storygraph.authoring.learn_statement import build_group
        return [build_group(row['name'], row['label'], row['choices'])
                for row in self.groups.rows() if str(row.get('name') or '').strip()]

    def groups_to_write(self) -> list:
        """该写进清单的词组（没勾"一起写"就返回空）。"""
        wg = getattr(self, 'write_groups', None)
        if wg is None or not wg.isChecked():
            return []
        return [g for g in self.built_groups() if g.get('ok')]

    def built(self) -> dict:
        """当前表单 → 后端参数（纯函数；判据不在这里）。"""
        # ⭐ 把**位置序列**一起交出去（顺序 + 类型 + 中文列名）——
        #   它是代码里长什么样的唯一依据，不能只用来做试切。
        block = True if self.block.isChecked() else None
        return build_declaration(self.name.text(), label=self.label.text(),
                                 docs=self.docs.text(), block=block,
                                 params=self.params.rows(),
                                 pattern=self.params.slots())

    def _choice_sources(self) -> list:
        """
        「⚡ 导入候选」的来源清单 —— 交给卡片显示（**卡片不碰后端**）。

        为什么把这层放在弹窗、不放在卡片里：卡片是纯界面（判据少、好测），
        而"来源怎么取"是纯函数 `authoring.learn_statement.choice_sources`
        （引擎事实 / 资产表 / 真实脚本）—— 两边各管各的事。
        """
        try:
            from storygraph.authoring.learn_statement import choice_sources
            return choice_sources(self._root_path, self.name.text().strip())
        except Exception as e:                      # noqa: BLE001 - 取不到就空
            swallow('ui_qt.dialog_learn.choice_sources', e)
            return []

    def _engine_heads(self) -> list:
        """
        引擎自己就有的写法（给下拉用）—— **优先问引擎**，问不到退回词表。

        ⚠ 不硬编一张清单：`engine_facts` 里那 23 条注册表才是权威
        （它是真机快照，连内置语句都在里面）；读不到才用工具词表兜底。
        """
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

    def _on_mode_changed(self, *_a):
        """切换"引擎自带 / 自己写的"：**把该说的话当场说清**（用户卡点 1）。"""
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

    def statement_mode(self) -> str:
        """`builtin`（给引擎自带的写法加列）/ `custom`（新写法）。"""
        getter = getattr(self, 'mode', None)
        if getter is None:
            return 'custom'
        data = getter.currentData()
        return str(data or 'custom')

    def _sync_groups_to_slots(self, *_a):
        """
        把词组名喂给每一格的下拉（**词组是"这一格的候选值从哪来"，不是独立表格**）。

        用户卡点 5 原话："「词组（可选·高级）」是什么鬼……如果我要用 swim_suit
        来匹配 泳装，我该写在哪？" —— 答：词组里一行 `泳装 → swim_suit`，
        然后在**那一格**上挂这个词组。
        """
        params = getattr(self, 'params', None)
        groups = getattr(self, 'groups', None)
        if params is None or groups is None:
            return
        try:
            names = [str(r.get('name') or '') for r in groups.rows()
                     if str(r.get('name') or '').strip()]
            params.set_groups(names)
        except Exception as e:                      # noqa: BLE001 - 不许炸窗口
            swallow('ui_qt.dialog_learn.sync_groups', e)

    def _refresh_cut(self, *_a):
        """
        **试切**：把粘进来的那一行按当前格子切开，显示每格吃到什么。

        ⚠ 这一段的唯一职责是**回答用户卡死的第一步**：
        "语句名该填什么、这一行会被怎么切" —— 切成什么样，这里就显示什么样。
        """
        view = getattr(self, 'cut_view', None)
        sample = getattr(self, 'sample', None)
        if view is None or sample is None:
            return
        line = sample.text().strip()
        # ⭐ **先报毛病，再谈切不切得上**（2026-10-12 接线）：
        #   `pattern.problems_of` 的文档自己写着"**界面直接显示**，不用自己拼"，
        #   而全仓只有它自己引用了它 —— 界面从没调过（又一个"实现了没接线"）。
        #   人最容易卡住的正是这里：某格选了"从这些里选"却**没填候选值**、
        #   或者一格都没挂词组 —— 这时试切**可能照样成功**，于是人不知道为什么不好使。
        problems = self._slot_problems()
        if not line:
            hint = '粘一行真实剧本，点「试切」—— 就能看出你的格子切得对不对。'
            view.setText(hint + (('\n' + problems) if problems else ''))
            return
        try:
            from storygraph.authoring import pattern as P
            groups = {g.get('name'): {'label': getattr(g, 'label', ''),
                                      'options': list(getattr(g, 'options', [])
                                                      or [])}
                      for g in (self.groups.rows() if hasattr(self, 'groups')
                                else [])
                      if isinstance(g, dict) and g.get('name')}
            got = P.match_line(self.params.slots(), line, groups=groups)
        except Exception as e:                      # noqa: BLE001 - 试切不许炸窗口
            swallow('ui_qt.dialog_learn.refresh_cut', e)
            view.setText(f'试切没做成：{type(e).__name__}: {e}')
            return
        if not got.get('ok'):
            view.setText('⚠ 切不上：' + str(got.get('reason') or '顺序或关键字不对')
                         + (('\n' + problems) if problems else ''))
            return
        cells = ' ｜ '.join(
            f'{c["text"]}（{c["column"] or "固定"}）' for c in got['cells'])
        # ⚠ 切得上**不代表**没问题（例：枚举格没填候选值时照样能切）——
        #   所以毛病单独再说一遍，别让人以为"绿了就没事"。
        view.setText('切出来是这样：' + cells
                     + f'　—　共 {len(got["cells"])} 格'
                     + (('\n' + problems) if problems else ''))

    def _slot_problems(self) -> str:
        """
        把格子本身的毛病汇总成人话（`pattern.problems_of` 的**界面接线**）。

        ⚠ 为什么必须显示：这些毛病**不会**让试切失败 —— 试切绿了人却不知道为什么
        不好使，正是用户反复卡住的地方。返回空串表示没毛病。
        """
        params = getattr(self, 'params', None)
        if params is None:
            return ''
        try:
            from storygraph.authoring import pattern as P
            rows = [{'kind': s.get('kind'), 'text': s.get('text'),
                     'column': s.get('column'), 'group': s.get('group'),
                     'choices': s.get('choices')}
                    for s in params.slots()]
            items = list(P.problems_of(rows) or [])
        except Exception as e:                      # noqa: BLE001 - 汇总不许炸窗口
            swallow('ui_qt.dialog_learn.slot_problems', e)
            return ''
        if not items:
            return ''
        return '这几点也顺手看看：' + '；'.join(str(x) for x in items[:5])

    def _refresh_preview(self, *_a):
        built = self.built()
        text = declaration_preview(built, path=str(self._root_path or ''))
        # ⚠ 构造期/早期就会调（表单初值要显示预览），那时后面那些控件还没建出来
        #    —— 所以一律走 `getattr` 守卫，末尾再刷一次（上次就是踩了这个坑）。
        wg = getattr(self, 'write_groups', None)
        if wg is not None and wg.isChecked():
            from storygraph.authoring.learn_statement import group_preview
            text += '\n\n' + group_preview(self.built_groups())
        preview = getattr(self, 'preview', None)
        if preview is not None:
            preview.setPlainText(text)
        # 试切跟着一起刷（顺序一变，"切出来什么样"立刻要跟着变）
        self._refresh_cut()
        btn = getattr(self, '_ok_btn', None)
        if btn is not None:
            # 勾了"一起写词组"时，允许**只写词组**（语句名可以是空的）
            ok = bool(built.get('ok')) or bool(self.groups_to_write())
            btn.setEnabled(ok)

    def result_declaration(self) -> dict:
        """按下去之后的最终参数（调用方拿去 `declare_statement`）。"""
        return self.built()


__all__ = ['CustomStatementDialog']
