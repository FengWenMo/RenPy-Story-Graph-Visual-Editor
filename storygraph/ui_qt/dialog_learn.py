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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from storygraph.authoring.learn_statement import (build_declaration,
                                                  declaration_preview)
from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme


class _ParamsTable(QTableWidget):
    """参数表：名字 / 中文名 / 候选值（逗号分隔）。"""

    def __init__(self, parent=None):
        super().__init__(0, 3, parent)
        self.setHorizontalHeaderLabels(['参数名', '中文名（可空）',
                                        '候选值（可空，逗号分隔）'])
        self.verticalHeader().setVisible(False)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)

    def rows(self) -> list:
        out = []
        for r in range(self.rowCount()):
            def cell(c):
                item = self.item(r, c)
                return item.text() if item is not None else ''
            out.append({'name': cell(0), 'label': cell(1), 'choices': cell(2)})
        return out

    def add_param(self, name: str = '', label: str = '', choices: str = ''):
        r = self.rowCount()
        self.insertRow(r)
        for c, text in enumerate((name, label, choices)):
            self.setItem(r, c, QTableWidgetItem(text))
        self.setCurrentCell(r, 0)


class _GroupsTable(QTableWidget):
    """词组表：组名 / 中文名 / 候选词（逗号分隔；留空 = 兜底组）。"""

    def __init__(self, parent=None):
        super().__init__(0, 3, parent)
        self.setHorizontalHeaderLabels(['词组名', '中文名（如 服装）',
                                        '这一组有哪些词（留空 = 兜底）'])
        self.verticalHeader().setVisible(False)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)

    def rows(self) -> list:
        out = []
        for r in range(self.rowCount()):
            def cell(c):
                item = self.item(r, c)
                return item.text() if item is not None else ''
            out.append({'name': cell(0), 'label': cell(1), 'choices': cell(2)})
        return out

    def add_group(self, name: str = '', label: str = '', choices: str = ''):
        r = self.rowCount()
        self.insertRow(r)
        for c, text in enumerate((name, label, choices)):
            self.setItem(r, c, QTableWidgetItem(text))
        self.setCurrentCell(r, 0)

    def load_groups(self, groups):
        """已有声明 → 填进表（打开时让用户看到现在是怎么分的）。"""
        for g in (groups or ()):
            self.add_group(getattr(g, 'name', '') or '',
                           getattr(g, 'label', '') or '',
                           '、'.join(getattr(g, 'options', []) or []))


class CustomStatementDialog(QDialog):
    """
    教工具认识一条自定义写法。

    左侧 = **发现**（项目里还没声明、但脚本里真的在用的写法）；
    右侧 = 填写 + 预览。左边点一行会把名字填进右边（参数留空，人自己加）。
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
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(T.SPACE['sm'])

        rv.addWidget(QLabel('③ 填一条声明'))
        row = QHBoxLayout()
        row.addWidget(QLabel('语句名'))
        self.name = QLineEdit()
        self.name.setPlaceholderText('例如 swim_suit（多个写法用空格分隔：'
                                     'mycue cue）')
        row.addWidget(self.name, 1)
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

        pv = QHBoxLayout()
        pv.addWidget(QLabel('参数（会成为表格列；候选值成为下拉）'))
        pv.addStretch(1)
        add = QPushButton('＋ 加一行')
        add.clicked.connect(lambda: self.params.add_param())
        rm = QPushButton('－ 删这一行')
        rm.clicked.connect(self._remove_param)
        pv.addWidget(add)
        pv.addWidget(rm)
        rv.addLayout(pv)

        self.params = _ParamsTable()
        rv.addWidget(self.params, 1)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(150)
        rv.addWidget(self.preview)

        self.name.textChanged.connect(self._refresh_preview)
        self.label.textChanged.connect(self._refresh_preview)
        self.docs.textChanged.connect(self._refresh_preview)
        self.block.toggled.connect(self._refresh_preview)
        self.params.itemChanged.connect(self._refresh_preview)
        self._refresh_preview()

        # —— 词组（分栏显示"服装 / 表情"这类）——
        #
        # 用户 2026-10-10："实际上需要分组显示……因为我这个要求通用！所以一般人
        # 其实是没有服装这个选项的！所以事实上应当通过 B 这个自定义模块，
        # 做到把服装跟表情这种东西分开！"
        box_groups = QGroupBox('词组（可选）—— 把立绘候选分成"服装 / 表情"这样的栏')
        gv = QVBoxLayout(box_groups)
        gv.setSpacing(T.SPACE['xs'])
        gv.addWidget(self._hint(
            '这里不预设任何组名：你写"服装"就是服装，写"姿势"就是姿势。'
            '词组名只是内部键，中文名才是界面上那一栏的字。'
            '留空"候选词"的那一组 = 兜底（没归到别的组的词都进它）。'))
        self.groups = _GroupsTable()
        self.groups.setMaximumHeight(160)
        self.groups.verticalHeader().setDefaultSectionSize(26)   # 别把中文挤掉
        gv.addWidget(self.groups)
        grow = QHBoxLayout()
        add_g = QPushButton('＋ 加一组')
        add_g.clicked.connect(lambda: self.groups.add_group())
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
        self.groups.itemChanged.connect(self._refresh_preview)
        rv.addWidget(box_groups)
        self.groups.load_groups(declared_groups or [])

        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([420, 560])
        self.resize(1040, 640)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._ok_btn = box.button(QDialogButtonBox.Ok)
        self._ok_btn.setText('写进 storygraph.rules.json')
        self._ok_btn.setProperty('variant', 'primary')
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
        # ⚠ 按钮建好之后**再刷一次**：构造早期那次刷新时按钮还不存在，
        # 于是"名字空着"这个初值状态没被同步到可用性上（实测：按钮一开始可点）。
        self._refresh_preview()

    # ---------------- 内部 ----------------

    @staticmethod
    def _hint(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setStyleSheet(f"color: {T.t('text.muted')};")
        return lab

    def _remove_param(self):
        r = self.params.currentRow()
        if r >= 0:
            self.params.removeRow(r)
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
    def _remove_group(self):
        r = self.groups.currentRow()
        if r >= 0:
            self.groups.removeRow(r)
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
        block = True if self.block.isChecked() else None
        return build_declaration(self.name.text(), label=self.label.text(),
                                 docs=self.docs.text(), block=block,
                                 params=self.params.rows())

    def _refresh_preview(self, *_a):
        built = self.built()
        text = declaration_preview(built, path=str(self._root_path or ''))
        # ⚠ 构造期/早期就会调（表单初值要显示预览），那时后面那些控件还没建出来
        #    —— 所以一律走 `getattr` 守卫，末尾再刷一次（上次就是踩了这个坑）。
        wg = getattr(self, 'write_groups', None)
        if wg is not None and wg.isChecked():
            from storygraph.authoring.learn_statement import group_preview
            text += '\n\n' + group_preview(self.built_groups())
        self.preview.setPlainText(text)
        btn = getattr(self, '_ok_btn', None)
        if btn is not None:
            # 勾了"一起写词组"时，允许**只写词组**（语句名可以是空的）
            ok = bool(built.get('ok')) or bool(self.groups_to_write())
            btn.setEnabled(ok)

    def result_declaration(self) -> dict:
        """按下去之后的最终参数（调用方拿去 `declare_statement`）。"""
        return self.built()


__all__ = ['CustomStatementDialog']
