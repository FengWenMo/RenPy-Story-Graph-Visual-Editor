"""
storygraph.ui_qt.dialog_extensions
==================================
**「扩展」一个窗口**：写法 / 技术调用 / 拍字段 / 批处理，四件事一张脸。

用户 2026-11-07 的原话：

> "你现在根本没办法让我把 `$ renpy.transition(Dissolve(0.1), layer="master")`
> 这条东西当作一个自定义写法输入进去，并自由让我决定它是否使用、是否能够批处理！
> 你的工具 UI 根本就没有给我这个功能！而且**太散了**！"

这个窗口就是那句话的正面回答：左树是**全部扩展点**（四个来源合成一份），
右边是**由字段表动态生成**的表单（`DynamicForm`），顶上两个开关就是
「是否使用」「是否能够批处理」。

为什么不是再做一个弹窗
----------------------
在它之前有 `dialog_learn`（语句，2332 行）、`dialog_calls`（调用，376 行）、
批处理规则（**只有 JSON，没有界面**）。三处各有一套表、各有一套字段语法。
这里不重写它们 —— 它是**第四个入口，也是唯一一个能看见全部四个入口的地方**；
等 P1 把卡片也接到同一张字段表上，`dialog_learn` / `dialog_calls` 会退化成
这个窗口的快捷方式。

写盘
----
只写 `<项目>/storygraph.ext.json`（`ExtensionRegistry.save`，带备份、
坏 JSON 拒绝覆盖）。**不碰** `storygraph.rules.json` —— 那份仍是语句与批处理的
运行时真源，什么时候回写由后面的步骤决定（见设计文档 §五）。
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QDialog, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QSplitter, QTableWidget,
                               QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)
from PySide6.QtCore import Qt

from storygraph.core.atomicio import write_text_atomic
from storygraph.ext import archive as EA
from storygraph.ext import card_fields as CF
from storygraph.ext import registry as ER
from storygraph.ext import schema as ES
from storygraph.ext.schema import (FIELD_KINDS, FIELD_KIND_LABELS,
                                   KIND_LABELS, KINDS, ExtError,
                                   ExtensionPoint, parse_field)
from storygraph.ui_qt.dynamic_form import DynamicForm

#: 字段表的列。
#:
#: **「类型」那一列现在是语义类型**（`ext.schema.FIELD_KINDS`：固定关键字 /
#: 任意值 / 一段代码 ……），不是"控件名"。同一个词同时决定三件事：解析器
#: 怎么读这一格、预览画什么控件、写回声明是什么 —— 用户 2026-11-07：
#: "统一三套字段表，不能让它们互不相通"。
#:
#: 「位置 / 关键字名」那一列只有**技术调用**用得上（2026-11-07 第五轮）：
#: 它是 `calls[].columns[]` 里 `position` / `kw` 这两位在界面上唯一的位置。
#: 以前这两件事只能靠"键名写成 `layer=`"和"按行序"这两条约定，而且只有
#: 调用窗口知道 —— 同一个形状两个窗口说法不同，等于两张表。
_FIELD_HEADERS = ['键名', '位置 / 关键字名', '类型', '固定字', '标签',
                  '候选（逗号分隔）']

#: 列号（6 列，别在下面用数字下标 —— 第五轮插列时就是这么错的）
_F_KEY, _F_POS, _F_KIND, _F_LIT, _F_LABEL, _F_CHOICES = range(6)

#: 「位置 / 关键字名」那一列的两句表头（按种类换）
_POS_HEADER_CALL = '位置 / 关键字名（填 0/1/2 或 layer）'
_POS_HEADER_OTHER = '位置 / 关键字名（只有技术调用用）'


def _switch_only(point: ExtensionPoint) -> bool:
    """这一条**在这里只能改开关**（形状归别处）吗。

    三类：工具自带（引擎 API）、旧清单投影（`rules.json`）、插件
    （`plugin:xxx.json`）。插件是**别人的文件**（2026-11-07 第五轮补）：
    在这里改形状存进 ext.json，下次装载会被插件覆盖 —— 那就是
    "保存成功、没有效果"，所以字段表直接锁上，只留开关 + 批处理。
    """
    return (bool(point.override)
            or point.source in ('builtin', 'rules.json')
            or str(point.source).startswith('plugin:'))


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setProperty('role', 'caption')
    return lab


def _kind_combo(current: str = '') -> QComboBox:
    """类型下拉：**只有一份词表**（`FIELD_KINDS` + 中文名）。"""
    combo = QComboBox()
    for kind in FIELD_KINDS:
        combo.addItem(FIELD_KIND_LABELS.get(kind, kind), kind)
    idx = combo.findData(str(current or 'variable'))
    combo.setCurrentIndex(max(0, idx))
    return combo


class ExtensionDialog(QDialog):
    """全部扩展点：看、改、开/关、批处理。"""

    def __init__(self, parent=None, project_root=None, focus_id: str = ''):
        super().__init__(parent)
        self.setWindowTitle('扩展（写法 / 调用 / 字段 / 批处理）')
        self.resize(1120, 760)
        self._root_path = str(project_root) if project_root else ''
        self._reg = ER.ExtensionRegistry.for_project(self._root_path)
        self._current = None
        self._loading = False
        #: 上一次保存的撤销材料（`save()` 返回的 `undo`）。
        #:
        #: ⚠ 放在**窗口**里，不放在注册表里（2026-11-07 第九轮）：`_reread()`
        #:   会换一个新的注册表对象，挂在注册表上的材料每次保存完就被自己弄丢了。
        self._undo = None
        #: 存过盘没有（另一个窗口据此决定要不要重新读一遍项目）
        self.changed = False

        self._build()
        self._reload_tree()
        self._report_problems()
        # 从别的窗口**带着一条 id 进来**（2026-11-07 第四轮）：调用窗口里
        # "这条归扩展管"要能点到这一条上，而不是让人在树上自己找。
        if focus_id:
            self.focus_on(focus_id)

    # ==================================================================
    #  搭界面（拆成几段：一个函数别超过一屏）
    # ==================================================================

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(8)
        title = QLabel('扩展')
        f = title.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 3)
        title.setFont(f)
        root.addWidget(title)
        root.addWidget(_hint(
            '这里能加的不只是"写法"：`$ 函数(...)` 这类技术调用、卡片上要多一格、'
            '以及把某条代码**批处理**到整章，都是同一种东西（一个扩展点）。'
            '右边的格子由「字段」表动态长出来 —— 你加一格，卡片/表格上就多一列。'))
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)
        self._build_left(split)
        self._build_right(split)
        split.setSizes([340, 780])

    def _build_left(self, split):
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['扩展点', '开关'])
        self.tree.setColumnWidth(0, 260)
        self.tree.itemSelectionChanged.connect(self._on_pick)
        ll.addWidget(self.tree, 1)
        row = QHBoxLayout()
        self.btn_new = QPushButton('＋ 新建')
        self.btn_new.clicked.connect(self._new_point)
        self.btn_del = QPushButton('删除')
        self.btn_del.clicked.connect(self._delete_point)
        row.addWidget(self.btn_new)
        row.addWidget(self.btn_del)
        row.addStretch(1)
        ll.addLayout(row)
        split.addWidget(left)

    def _build_right(self, split):
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        self._build_identity(rl)
        self._build_fields_section(rl)
        self._build_batch_section(rl)
        self._build_footer(rl)
        split.addWidget(right)

    def _build_identity(self, rl):
        """名字 / 显示名 / 说明 / 两个开关。"""
        self.head = QLabel('左边挑一个扩展点')
        fh = self.head.font()
        fh.setBold(True)
        self.head.setFont(fh)
        rl.addWidget(self.head)
        self.source_note = _hint('')
        rl.addWidget(self.source_note)

        ids = QHBoxLayout()
        ids.addWidget(QLabel('名字'))
        self.ed_id = QLineEdit()
        self.ed_id.setMaximumWidth(220)
        ids.addWidget(self.ed_id)
        ids.addWidget(QLabel('显示名'))
        self.ed_label = QLineEdit()
        ids.addWidget(self.ed_label, 1)
        rl.addLayout(ids)

        ids2 = QHBoxLayout()
        ids2.addWidget(QLabel('种类'))
        self.cb_kind = QComboBox()
        for kind in KINDS:
            self.cb_kind.addItem(KIND_LABELS.get(kind, kind), kind)
        self.cb_kind.currentIndexChanged.connect(self._on_kind_changed)
        self.cb_kind.setToolTip(
            '这一条扩展点是哪一类：写法 / 技术调用 / 拍字段 / 批处理。\n'
            '⚠ 2026-11-07 前这里**没有这个下拉** —— 新建出来的永远是"写法"，'
            '所以调用 / 拍字段 / 批处理在界面上根本建不出来（只能手改 JSON）。')
        ids2.addWidget(self.cb_kind, 0)
        ids2.addWidget(QLabel('说明'))
        self.ed_docs = QLineEdit()
        ids2.addWidget(self.ed_docs, 1)
        rl.addLayout(ids2)

        #: 跟着"种类"走的一句提示（拍字段的键名规则与写法完全不同）
        self.kind_note = _hint('')
        rl.addWidget(self.kind_note)

        # 技术调用自己的**演出信息**（2026-11-07 第五轮）：包装函数不是转场，
        # 但项目可以说"等价于哪个效果、时长取第几个参数"。这两句话以前只有
        # rules.json 写得了，扩展窗口里新建的调用存不下 —— 舞台预览里
        # 就既没时长、也标"认不出"。
        self.meta_row = QWidget()
        ml = QHBoxLayout(self.meta_row)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.addWidget(QLabel('等价于哪个转场'))
        self.ed_effect = QLineEdit()
        self.ed_effect.setPlaceholderText('Dissolve / Fade（留空 = 不假装）')
        self.ed_effect.setToolTip(
            '这条调用演出来等于哪个转场。留空 = 舞台预览如实标"认不出"，'
            '不假装是淡入。例：my_wipe 内部调 renpy.transition(Dissolve(...)) → 填 Dissolve')
        ml.addWidget(self.ed_effect, 1)
        ml.addWidget(QLabel('时长取第几个参数'))
        self.sp_seconds = QSpinBox()
        self.sp_seconds.setRange(-1, 9)
        self.sp_seconds.setSpecialValueText('不按参数算')
        self.sp_seconds.setToolTip('这个转场的秒数取自第几个位置参数（0 起）。'
                                   '「不按参数算」= 用默认时长，不读参数。')
        ml.addWidget(self.sp_seconds)
        rl.addWidget(self.meta_row)

        self.ck_enabled = QCheckBox(
            '使用这条扩展（关掉 = 工具不再拿它去认写法 / 出格子 / 批处理）')
        self.ck_enabled.setChecked(True)
        rl.addWidget(self.ck_enabled)
        self.ck_batch = QCheckBox('允许把它批处理到整章')
        self.ck_batch.toggled.connect(self._on_batch_toggled)
        rl.addWidget(self.ck_batch)

    def _build_fields_section(self, rl):
        """字段表 + 增删 + 动态预览。"""
        rl.addWidget(_hint('字段（决定卡片 / 表格上长出哪些格；类型按"解析器怎么读"选，'
                           '控件是它推出来的）'))
        self.tbl = QTableWidget(0, len(_FIELD_HEADERS))
        self.tbl.setHorizontalHeaderLabels(_FIELD_HEADERS)
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(_F_KEY, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(_F_POS, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(_F_KIND, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(_F_LIT, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(_F_LABEL, QHeaderView.Stretch)
        hh.setSectionResizeMode(_F_CHOICES, QHeaderView.Stretch)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setMinimumHeight(130)
        self.tbl.itemChanged.connect(self._refresh_form)
        rl.addWidget(self.tbl, 1)

        frow = QHBoxLayout()
        self.btn_add_field = QPushButton('＋ 加一格')
        self.btn_add_field.clicked.connect(self._add_field)
        self.btn_rm_field = QPushButton('删掉这一格')
        self.btn_rm_field.clicked.connect(self._remove_field)
        frow.addWidget(self.btn_add_field)
        frow.addWidget(self.btn_rm_field)
        frow.addStretch(1)
        rl.addLayout(frow)

        rl.addWidget(_hint('预览（就是卡片上会长成的样子 —— 真的按字段表画）'))
        self.form = DynamicForm([])
        rl.addWidget(self.form)

    def _build_batch_section(self, rl):
        """批处理：放在哪 / 对谁 / 写什么 / **预演**。"""
        self.batch_box = QWidget()
        bl = QVBoxLayout(self.batch_box)
        bl.setContentsMargins(0, 4, 0, 0)
        bl.setSpacing(4)
        bl.addWidget(_hint('批处理：把这段代码按规则补到整章（默认只预演，不写盘）'))
        brow = QHBoxLayout()
        brow.addWidget(QLabel('放在哪'))
        self.cb_placement = QComboBox()
        for key, text in (('after_last_show', '最后一次立绘之后'),
                          ('after_last_change', '最后一次立绘变化之后'),
                          ('after_beat', '这一拍之后'),
                          ('before_beat', '这一拍之前')):
            self.cb_placement.addItem(text, key)
        brow.addWidget(self.cb_placement, 1)
        brow.addWidget(QLabel('对谁'))
        self.cb_when = QComboBox()
        for key, text in (('', '全部拍'), ('sprite', '有立绘的拍'),
                          ('say', '有台词的拍')):
            self.cb_when.addItem(text, key)
        brow.addWidget(self.cb_when, 1)
        bl.addLayout(brow)
        bl.addWidget(QLabel('写入的代码（原样一行）'))
        self.ed_text = QPlainTextEdit()
        self.ed_text.setMaximumHeight(64)
        self.ed_text.setPlaceholderText(
            '$ renpy.transition(Dissolve(0.1), layer="master")')
        bl.addWidget(self.ed_text)
        # —— 预演：**开关的产物必须能跑一遍引擎**（2026-11-07 探针实测：
        #    以前 `cue_rules()` 产出的是 dict，`plan_batch` 直接 AttributeError，
        #    也就是"开关看着能开、真跑批处理就断"）。
        prow = QHBoxLayout()
        self.btn_preview = QPushButton('预演（不写盘）')
        self.btn_preview.clicked.connect(self._preview_batch)
        prow.addWidget(self.btn_preview)
        self.btn_push = QPushButton('写进 storygraph.batch.json')
        self.btn_push.clicked.connect(self._push_batch_rule)
        prow.addWidget(self.btn_push)
        self.preview_note = _hint('')
        prow.addWidget(self.preview_note, 1)
        bl.addLayout(prow)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(150)
        self.preview.setVisible(False)
        bl.addWidget(self.preview)
        self.batch_box.setVisible(False)
        rl.addWidget(self.batch_box)

    def _build_footer(self, rl):
        rl.addStretch(0)
        foot = QHBoxLayout()
        self.status = _hint('')
        foot.addWidget(self.status, 1)
        # —— 把声明带走（2026-11-07 第七轮，设计文档 §15.10 第 1 条）——
        #
        # 导出 / 导入走**另一份文件**（`*.sg-ext.json` 档案），不碰项目里的
        # `storygraph.ext.json`：导入只改内存，落盘还是「保存到项目」那一步
        # （于是冲突照旧会被 `_ask_conflict` 拦下来）。
        b_export = QPushButton('导出…')
        b_export.setToolTip('把当前这些声明存成一份可移植档案（别的项目可以导入）')
        # ⚠ 连的是 `export_archive` —— 第九轮之前这里写的是
        #   `self._export_archive()`，而类里根本没有这个方法：点下去是
        #   AttributeError，PySide6 只把它打到 stderr，按钮看着**完全没反应**
        #   （`tests/test_dead_slots.py` 现在钉着这一类）。包一层 lambda 仍然
        #   必须：直接 `connect(self.export_archive)` 会把 `checked=False`
        #   当第一个实参喂进去（`path='False'`）。
        b_export.clicked.connect(lambda: self.export_archive())
        b_import = QPushButton('导入…')
        b_import.setToolTip('读一份扩展档案装进这个项目（先看冲突，再保存）')
        b_import.clicked.connect(lambda: self.import_archive())
        foot.addWidget(b_export)
        foot.addWidget(b_import)
        # —— 撤销刚才那次保存（2026-11-07 第九轮，设计文档 §18.4）——
        #
        # 导入是**批量改**：走错一份档案就是几十条声明一起变。从前退路只有
        # `save()` 留在同目录的 `.bak`（要人自己去翻文件）。现在这里能一键
        # 退回去，而且**只退自己那一次**：文件在之后又被谁改过就拒绝动手。
        self.btn_undo = QPushButton('撤销上次保存')
        self.btn_undo.setToolTip(
            '把 storygraph.ext.json 退回**上一次「保存到项目」之前**那副样子。\n'
            '只退本窗口自己那次保存：文件在那之后又被改过 / 删了 → 拒绝动手，'
            '一个字都不写。')
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(lambda: self._undo_save())
        foot.addWidget(self.btn_undo)
        b_close = QPushButton('关闭')
        b_close.clicked.connect(self.reject)
        b_save = QPushButton('保存到项目')
        b_save.setDefault(True)
        # ⚠ 必须包一层：`_save` 现在收 `on_conflict` 这个参数，直接 `connect`
        #   会把 Qt 的 `checked=False` 当成第一个实参喂进去（`tools/
        #   audit_qt_signal_slots.py` 当场抓到的 —— 点"保存"会变成
        #   "on_conflict=False，不认识，没存"）。
        b_save.clicked.connect(lambda: self._save())
        foot.addWidget(b_close)
        foot.addWidget(b_save)
        rl.addLayout(foot)

    # ==================================================================
    #  左树
    # ==================================================================

    def _reload_tree(self):
        self.tree.clear()
        for kind in KINDS:
            points = self._reg.by_kind(kind)
            if not points:
                continue
            top = QTreeWidgetItem([KIND_LABELS.get(kind, kind), ''])
            top.setFlags(Qt.ItemIsEnabled)
            self.tree.addTopLevelItem(top)
            for p in points:
                flags = []
                if not p.enabled:
                    flags.append('停用')
                if p.batchable:
                    flags.append('可批处理')
                child = QTreeWidgetItem([p.label or p.id, '、'.join(flags)])
                child.setData(0, Qt.UserRole, p.id)
                top.addChild(child)
            top.setExpanded(True)
        # 读不进来的条目**也要出现在树上**（2026-11-07）：以前它们只进
        # `status` 那一行小字，用户看到的是"我加的写法不见了，也没人告诉我"。
        if self._reg.problems:
            top = QTreeWidgetItem([f'⚠ 读取时的问题（{len(self._reg.problems)}）', ''])
            top.setFlags(Qt.ItemIsEnabled)
            self.tree.addTopLevelItem(top)
            for text in self._reg.problems:
                item = QTreeWidgetItem([text, ''])
                item.setFlags(Qt.ItemIsEnabled)
                top.addChild(item)
            top.setExpanded(True)

    def focus_on(self, ident: str) -> bool:
        """
        在树上选中 `ident`（找不到就返回 False —— **不猜**、不新建）。

        为什么需要它：调用窗口里那条"归扩展管"的声明，用户点过来时应该
        直接落在这一条上。让人在四组树里自己找，等于把"统一入口"又拆散了。
        """
        want = str(ident or '').strip()
        if not want:
            return False
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                if str(child.data(0, Qt.UserRole) or '') == want:
                    self.tree.setCurrentItem(child)
                    top.setExpanded(True)
                    return True
        return False

    def _on_pick(self):
        item = self.tree.currentItem()
        if item is None:
            return
        ident = item.data(0, Qt.UserRole)
        if not ident:
            return
        point = self._reg.get(str(ident))
        if point is None:
            return
        self._load(point)

    def _load(self, point: ExtensionPoint):
        self._loading = True
        try:
            self._current = point
            self.head.setText(f'{point.label or point.id}'
                              f'   （{KIND_LABELS.get(point.kind, point.kind)}）')
            self.source_note.setText(self._where(point))
            self.ed_id.setText(point.id)
            self.ed_label.setText(point.label)
            self.ed_docs.setText(point.docs)
            idx = self.cb_kind.findData(str(point.kind or 'statement'))
            self.cb_kind.setCurrentIndex(max(0, idx))
            self._on_kind_changed()
            self.ck_enabled.setChecked(bool(point.enabled))
            self.ck_batch.setChecked(bool(point.batchable))
            self.ed_effect.setText(str(getattr(point, 'effect', '') or ''))
            seconds = getattr(point, 'seconds_arg', None)
            self.sp_seconds.setValue(-1 if seconds is None else int(seconds))
            self._fill_fields(point)
            self._fill_batch(point)
        finally:
            self._loading = False
        # ⚠ 必须**解除 `_loading` 之后**再画预览：`_refresh_form` 在载入期间
        #   是被刻意挡住的（否则每填一格就重建一次表单）。第一版把它放在
        #   `try` 里，于是"字段有、预览空白" —— 界面上看就是"字段没生效"。
        self._refresh_form()
        switch_only = _switch_only(point)
        self._set_fields_editable(not switch_only)
        # 种类也归"形状"：工具自带 / 旧清单投影 / 插件的那几类动不得（换了种类
        # 树上的分组就变了，而它们本来就住在别处）。
        self.cb_kind.setEnabled(not switch_only)

    def _where(self, point: ExtensionPoint) -> str:
        """这一条从哪来 + **能不能在这里改形状**（不能的话说清去哪改）。"""
        if point.override:
            return ('只带开关的记录（形状归原处：插件 / 旧清单 / 引擎 API）'
                    ' —— 这里只能改「是否使用」「是否能够批处理」')
        if point.source == 'builtin':
            return ('工具自带（引擎 API）—— 形状由工具内置，这里只能停用 / 配批处理')
        if point.source == 'rules.json':
            return ('来自 storygraph.rules.json（旧清单的**只读投影**）'
                    ' —— 改形状请用「自定义语句」窗；这里只能停用 / 配批处理')
        if point.source == 'project':
            return '项目自己的扩展点（改完保存即生效）'
        if str(point.source).startswith('plugin:'):
            return (f'插件：{str(point.source).split(":", 1)[-1]}'
                    ' —— 形状归插件文件（别人的），这里只能停用 / 配批处理')
        return f'插件：{str(point.source).split(":", 1)[-1]}'

    def _set_fields_editable(self, editable: bool) -> None:
        self.tbl.setEnabled(bool(editable))
        self.btn_add_field.setEnabled(bool(editable))
        self.btn_rm_field.setEnabled(bool(editable))

    # ==================================================================
    #  字段表
    # ==================================================================

    def _fill_fields(self, point: ExtensionPoint):
        rows = point.field_specs()
        # 只有"技术调用 + 形状归自己"的那条才允许改这一列
        editable = self._is_call() and not _switch_only(point)
        blocked = self.tbl.blockSignals(True)
        try:
            self.tbl.setRowCount(0)
            for spec in rows:
                self.tbl.insertRow(self.tbl.rowCount())
                r = self.tbl.rowCount() - 1
                self.tbl.setItem(r, _F_KEY, QTableWidgetItem(spec.key))
                # 位置 / 关键字：只有技术调用会真读这一格（其余种类置灰）
                cell = QTableWidgetItem(self._pos_text(spec))
                self._lock_pos_item(cell, editable)
                self.tbl.setItem(r, _F_POS, cell)
                combo = _kind_combo(spec.kind)
                combo.currentIndexChanged.connect(self._refresh_form)
                self.tbl.setCellWidget(r, _F_KIND, combo)
                self.tbl.setItem(r, _F_LIT, QTableWidgetItem(
                    spec.shown_literal if spec.fixed else ''))
                self.tbl.setItem(r, _F_LABEL, QTableWidgetItem(spec.label))
                self.tbl.setItem(r, _F_CHOICES, QTableWidgetItem(
                    ', '.join(spec.choices)))
        finally:
            self.tbl.blockSignals(blocked)

    @staticmethod
    def _pos_text(spec) -> str:
        """字段 → 「位置 / 关键字名」那一格的字（位置参数写数字，关键字写字）。

        老文件里关键字只能写成键名 `layer=`（那时没有 `kw` 这一位）——
        这里**按同一句话翻译出来**，于是老文件在窗口里也看得见"这是关键字"，
        存一次就变成 `kw` 那一位（意思不变，见 `schema.column_of_field`）。
        """
        if spec.kw:
            return str(spec.kw)
        if spec.position is not None:
            return str(spec.position)
        key = str(spec.key or '')
        if key.endswith('=') and len(key) > 1 and ES.kw_ok(key[:-1]):
            return key[:-1]
        return ''

    def _is_call(self) -> bool:
        """当前这条是技术调用吗（那一列只有它有用）。"""
        return str(self.cb_kind.currentData() or '') == 'call'

    @staticmethod
    def _lock_pos_item(item, editable: bool) -> None:
        """能不能改那一格（不能 = 这一列对当前这条没有意义）。"""
        flags = item.flags()
        if editable:
            item.setFlags(flags | Qt.ItemIsEditable)
            item.setToolTip('第几个位置参数（0/1/2…）或关键字参数名（layer）；'
                            '二选一。留空 = 按行序。')
        else:
            item.setFlags(flags & ~Qt.ItemIsEditable)
            item.setToolTip('这一列只有「技术调用」用得上：'
                            '写法的格子在 pattern / 子句里各有一套位置规则；'
                            '插件 / 旧清单那几条的形状归原处，这里改不了。')

    def _table_fields(self) -> list:
        out = []
        for r in range(self.tbl.rowCount()):
            key = (self.tbl.item(r, _F_KEY).text()
                   if self.tbl.item(r, _F_KEY) else '').strip()
            if not key:
                continue
            combo = self.tbl.cellWidget(r, _F_KIND)
            kind = str(combo.currentData() or 'variable') if combo else 'variable'
            literal = (self.tbl.item(r, _F_LIT).text()
                       if self.tbl.item(r, _F_LIT) else '')
            label = (self.tbl.item(r, _F_LABEL).text()
                     if self.tbl.item(r, _F_LABEL) else '')
            raw_choices = (self.tbl.item(r, _F_CHOICES).text()
                           if self.tbl.item(r, _F_CHOICES) else '')
            raw_pos = (self.tbl.item(r, _F_POS).text()
                       if self.tbl.item(r, _F_POS) else '').strip()
            raw = {'key': key, 'label': label, 'kind': kind,
                   'literal': literal.strip(),
                   'choices': [c.strip() for c in raw_choices.replace('，', ',')
                               .split(',') if c.strip()]}
            if raw_pos:
                # 数字 = 位置参数，其余 = 关键字参数名。两件事写在一个格子里，
                # 但**存下来是两位**（`position` / `kw`），不是把 `转场@0`
                # 又塞回一个字符串。只有技术调用会真用它们（其余种类那两格
                # 锁着，已有的值原样留着，不悄悄抹掉）。
                if raw_pos.lstrip('-').isdigit():
                    raw['position'] = raw_pos
                else:
                    raw['kw'] = raw_pos
            try:
                out.append(parse_field(raw))
            except ExtError as e:
                self.status.setText(f'字段「{key}」没读进来（第 {r + 1} 行）：{e}')
        return out

    def _add_field(self):
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        self.tbl.setItem(r, _F_KEY, QTableWidgetItem(''))
        cell = QTableWidgetItem('')
        self._lock_pos_item(cell, self._is_call())
        self.tbl.setItem(r, _F_POS, cell)
        combo = _kind_combo('variable')
        combo.currentIndexChanged.connect(self._refresh_form)
        self.tbl.setCellWidget(r, _F_KIND, combo)
        self.tbl.setItem(r, _F_LIT, QTableWidgetItem(''))
        self.tbl.setItem(r, _F_LABEL, QTableWidgetItem(''))
        self.tbl.setItem(r, _F_CHOICES, QTableWidgetItem(''))
        self.tbl.setCurrentCell(r, _F_KEY)
        self.tbl.editItem(self.tbl.item(r, _F_KEY))

    def _remove_field(self):
        r = self.tbl.currentRow()
        if r >= 0:
            self.tbl.removeRow(r)
            self._refresh_form()

    def _refresh_form(self, *_a):
        if self._loading:
            return
        self.form.rebuild(self._table_fields())

    # ==================================================================
    #  批处理
    # ==================================================================

    def _on_batch_toggled(self, on: bool):
        self.batch_box.setVisible(bool(on))

    def _on_kind_changed(self, *_a):
        """种类变了：字段表那几句说明跟着换（四类的"键名"意思不一样）。"""
        kind = str(self.cb_kind.currentData() or 'statement')
        self.kind_note.setText(self._kind_note(kind))
        self._sync_pos_column(kind == 'call')
        self.meta_row.setVisible(kind == 'call')

    def _sync_pos_column(self, is_call: bool) -> None:
        """「位置 / 关键字名」那一列：表头 + 能否编辑（只有调用能编辑）。"""
        self.tbl.setHorizontalHeaderItem(
            _F_POS, QTableWidgetItem(_POS_HEADER_CALL if is_call
                                     else _POS_HEADER_OTHER))
        blocked = self.tbl.blockSignals(True)
        try:
            for r in range(self.tbl.rowCount()):
                item = self.tbl.item(r, _F_POS)
                if item is not None:
                    self._lock_pos_item(item, is_call)
        finally:
            self.tbl.blockSignals(blocked)

    @staticmethod
    def _kind_note(kind: str) -> str:
        """这一类的字段表**键名写什么**（四句话，只有一处）。"""
        if kind == 'field':
            return ('拍字段（改卡片上已有的一格）：键名写 '
                    + ' / '.join(CF.overridable_keys())
                    + ' 之一，标签 / 类型 / 候选就是它的新样子 —— '
                    '值仍然写回同一个字段。说话人 / 台词 / 背景 / 立绘那几个是'
                    '专用控件，不在这张表里。')
        if kind == 'call':
            return ('技术调用：键名是参数名。「位置 / 关键字名」那一列二选一 —— '
                    '填 0/1/2 就是第几个位置参数，填 layer 就是关键字参数'
                    '（`$ f(..., layer="screens")` 那个 layer）。'
                    '整列留空 = 按行序（关键字那几行不占位置）。'
                    '下面那两个是这条调用的演出信息：等价于哪个转场、'
                    '时长取第几个参数（不填就不假装）。')
        if kind == 'batch':
            return ('批处理：主角是下面那个「允许把它批处理到整章」+ 规则；'
                    '字段表可以不填。')
        return ('写法：键名是这一格的列名（写回脚本 / 表格上用它的名字）；'
                '固定关键字那格写"语句里必须原样出现的那个词"。')

    def _fill_batch(self, point: ExtensionPoint):
        batch = dict(point.batch or {})
        insert = dict(batch.get('insert') or {})
        when = dict(batch.get('when') or {})
        placement = str(insert.get('placement') or 'after_last_show')
        idx = self.cb_placement.findData(placement)
        self.cb_placement.setCurrentIndex(max(0, idx))
        want = 'sprite' if when.get('show') == 'sprite' else (
            'say' if when.get('say') else '')
        idx = self.cb_when.findData(want)
        self.cb_when.setCurrentIndex(max(0, idx))
        self.ed_text.setPlainText(str(insert.get('text') or ''))
        self.batch_box.setVisible(bool(point.batchable))

    def _collect_batch(self) -> dict:
        when = {}
        w = str(self.cb_when.currentData() or '')
        if w == 'sprite':
            when['show'] = 'sprite'
        elif w == 'say':
            when['say'] = True
        insert = {'text': self.ed_text.toPlainText().strip(),
                  'placement': str(self.cb_placement.currentData()
                                   or 'after_last_show'),
                  'skip_if_present': True}
        return {'when': when, 'insert': insert}

    def _preview_batch(self):
        """
        把界面上的批处理规则**真的跑一遍引擎**（只算计划，不写盘）。

        这条按钮存在的理由很具体：开关的产物必须是引擎吃得下的东西。
        2026-11-07 的探针里，`cue_rules()` 产出的 dict 直接喂 `plan_batch`
        会 `AttributeError: 'dict' has no attribute 'when'` —— 界面上"能开"
        和"真能跑"是两件事，所以这里按引擎的口径走一遍并把结果摊开。
        """
        from storygraph.app import batch_edit as BE

        if not self._root_path:
            self.preview_note.setText('先打开项目 —— 批处理要扫项目里的章节。')
            return None
        rule = self._rule_from_form()
        self.preview.setVisible(True)
        try:
            rules = BE.parse_rules([rule])
            plan = BE.plan_batch(self._root_path, rules)
        except BE.BatchRuleError as exc:
            self.preview.setPlainText(f'这条规则还不合法：{exc}')
            self.preview_note.setText('没有生成计划')
            return None
        except (OSError, ValueError) as exc:
            self.preview.setPlainText(f'算不了计划：{exc}')
            self.preview_note.setText('没有生成计划')
            return None
        text = BE.format_plan(plan, limit=4)
        if plan.files:
            text += '\n\n' + BE.format_diff(plan, context=2)
        self.preview.setPlainText(text)
        self.preview_note.setText(
            f'{len(plan.files)} 个文件 · {plan.pending} 处会写'
            if not plan.errors else '有错误，不能写')
        return plan

    def _rule_from_form(self) -> dict:
        """界面上的批处理配置 → 一条**规则 dict**（id 取名字那一栏）。"""
        rule = self._collect_batch()
        rule['id'] = (self.ed_id.text().strip()
                      or f'扩展点{self.tbl.rowCount()}')
        return rule

    def _push_batch_rule(self):
        """
        把这条规则**写进项目的 `storygraph.batch.json`**（批处理窗口读的就是它）。

        为什么要有这一步：开关只是"允许",真正让整章照这条跑的是批处理窗口 ——
        它只读 `storygraph.batch.json`。没有这一步，配好的规则只能在这窗口里
        预演一次，用户还得自己抄进 JSON。

        ⚠ 纪律与 `batch_edit.load_rule_file` 一致：**坏文件不许半读半改** ——
          现有文件读不出来就拒绝写入，绝不覆盖。
        """
        from storygraph.app import batch_edit as BE
        from storygraph.app import batch_recipes as BR

        if not self._root_path:
            self.preview_note.setText('先打开项目 —— 规则要写进项目根目录。')
            return None
        rule = self._rule_from_form()
        try:
            BE.parse_rules([rule])              # 先过引擎那道门
        except BE.BatchRuleError as exc:
            self.preview_note.setText(f'这条规则还不合法：{exc}')
            return None
        path = Path(self._root_path) / 'storygraph.batch.json'
        items = []
        if path.is_file():
            try:
                items = BR.load_rules(path)
            except (BR.BatchRuleError, OSError, ValueError) as exc:
                self.preview_note.setText(
                    f'现有规则文件读不出来，不覆盖：{exc}')
                return None
        # 同 id 就地更新（规则文件的 id 必须唯一，`dump_rules` 会拒绝重复）
        keep = [it for it in items
                if str((it.get('raw') or {}).get('id') or '') != rule['id']]
        replaced = len(keep) != len(items)
        keep.append({'raw': rule, 'form': None, 'why': '来自扩展点'})
        if QMessageBox.question(
                self, '写进批处理规则文件',
                f'把「{rule["id"]}」{"替换" if replaced else "加"}进\n{path}？\n'
                '（写前会自动备份；批处理窗口里仍会再预演一次才动剧本）') \
                != QMessageBox.Yes:
            self.preview_note.setText('没有写入')
            return None
        try:
            BR.save_rules(path, keep)
        except (BR.BatchRuleError, OSError, ValueError) as exc:
            self.preview_note.setText(f'没写进去：{exc}')
            return None
        self.preview_note.setText(
            f'已{"更新" if replaced else "写入"} 1 条到 {path.name}'
            f'（共 {len(keep)} 条）—— 批处理窗口里能看到了。')
        return path

    # ==================================================================
    #  动作
    # ==================================================================

    def _apply_to(self, point: ExtensionPoint):
        """
        把界面上的值写回扩展点对象。

        ⚠ 三类点**只写开关**：工具自带（`builtin`）、旧清单投影
          （`rules.json`）、插件（`plugin:xxx.json`）。它们的"形状"归别处的
          真源；在这里改字段、存成一份新声明，就是**有损覆盖**（别名 / 块体 /
          子句 / 渲染都会丢），而且插件在装载顺序上**后面会盖回来** ——
          那就是"保存成功、实际没变"。所以它们落盘时带 `override: true`：
          只带开关，声明仍归原处。
        """
        point.id = self.ed_id.text().strip() or point.id
        point.label = self.ed_label.text().strip()
        point.docs = self.ed_docs.text().strip()
        point.enabled = bool(self.ck_enabled.isChecked())
        point.batchable = bool(self.ck_batch.isChecked())
        if _switch_only(point):
            point.override = True
            # 开关要落盘（否则下次打开又变回去）；形状不写（`to_dict` 有闸门）
            point.source = 'project'
            # 借来的字段只用于显示：**不写回**
            point.fields = list(point.field_specs())
        else:
            point.kind = str(self.cb_kind.currentData() or point.kind)
            point.fields = self._table_fields()
            if point.kind == 'call':
                effect = self.ed_effect.text().strip()
                point.effect = effect
                seconds = int(self.sp_seconds.value())
                point.seconds_arg = None if seconds < 0 else seconds
            else:
                # 换了种类：演出信息与这一条无关了（不留在文件里当噪音）
                point.effect = ''
                point.seconds_arg = None
        if point.batchable:
            point.batch = self._collect_batch()
        else:
            point.batch = {}

    def _save(self, on_conflict: str = 'abort') -> bool:
        """
        存盘 —— **先对表再写**（`ExtensionRegistry.save` 的三方合并）。

        `on_conflict='abort'`（默认）时，如果文件里有人改了**同样的那几条**，
        这次一个字都不写，先把冲突列给用户看（`_ask_conflict`）；
        `'mine'` = 以这边为准覆盖。返回存没存成。
        """
        keep = self._current.id if self._current is not None else ''
        if self._current is not None:
            self._apply_to(self._current)
            self._reg.upsert(self._current)
        if not self._root_path:
            self.status.setText('先打开项目 —— 扩展点要存进项目根目录。')
            return False
        res = self._reg.save(self._root_path, on_conflict=on_conflict)
        if res.get('conflicts') and not res.get('ok') \
                and on_conflict == 'abort':
            return self._ask_conflict(keep, res)
        if not res.get('ok'):
            self.status.setText('没存成：' + str(res.get('reason') or '?'))
            return False
        # 保存完必须让**装配缓存**失效：`StatementRegistry.for_project` 按项目
        # 根缓存，不清的话"刚加完的写法"要等重启工具才认（用户 2026-11-07：
        # "改了没反应"）。`_reread` 里做，删除那条路也顺手刷新。
        absorbed = [str(i) for i in (res.get('absorbed') or ())]
        notes = []
        if absorbed:
            notes.append('顺手收进 ' + str(len(absorbed)) + ' 条别人改的：'
                         + '、'.join(absorbed[:4])
                         + ('…' if len(absorbed) > 4 else ''))
        self.changed = True
        self._remember_undo(res)
        self._reread(keep)
        if self._reg.problems:
            notes.append(f'{len(self._reg.problems)} 条读取问题（见左树 ⚠）')
        self.status.setText(
            f'已保存 {res.get("count", 0)} 条到 {ER.MANIFEST}'
            '（旧清单没动；注册表缓存已刷新）。'
            + ('｜' + '；'.join(notes) if notes else ''))
        return True

    def _ask_conflict(self, keep: str, res: dict) -> bool:
        """文件里有人改了同样的条目 —— **当面说**，别默默覆盖。返回存没存成。"""
        ids = [str(i) for i in (res.get('conflicts') or ())]
        shown = '、'.join(ids[:6]) + ('…' if len(ids) > 6 else '')
        ans = QMessageBox.question(
            self, '文件里也有人在改',
            f'你打开这个窗口之后，{ER.MANIFEST} 里这些条也被改过：\n\n{shown}\n\n'
            '这次**没有写盘**（文件保持原样，别人的改动都还在）。\n'
            '要以你这边的版本覆盖它们吗？选「否」= 这次不存，'
            '关掉重开就能看到别人的版本。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            self.status.setText(
                f'没存：{ER.MANIFEST} 里有人改过同样的条目，文件一个字没动。')
            return False
        # ⚠ 这里**不能**先 `_reread()`：那会把"我的改动"换成文件里的版本，
        #   "以我的为准"就没有"我的"了。合并本身会按 id 决定谁说话。
        if not self._save(on_conflict='mine'):
            return False
        self.status.setText(self.status.text()
                            + '｜冲突的几条按你这边的版本覆盖了。')
        return True

    # ==================================================================
    #  撤销"刚才那次保存"（2026-11-07 第九轮）
    # ==================================================================

    def _remember_undo(self, res: dict) -> None:
        """
        记下"这次保存之前长什么样"，按钮据此亮不亮。

        `save()` 只在**真的改了字节**时才给材料（连点两次保存 → `undo is None`）。
        那时**保留上一份**：文件内容没变，上一份撤销材料照样成立，不该被清掉。
        """
        if res.get('undo'):
            self._undo = res['undo']
        self.btn_undo.setEnabled(bool(self._undo))

    def _undo_save(self) -> dict:
        """
        把上一次保存退回去（文件整份恢复成保存之前那副样子）。

        **不是**用 `save()` 再存一遍旧内容：那份是三方合并，会按 id 判"谁改过"，
        撤销就会变成"部分撤销 + 一堆冲突提示"（见 `registry.restore_manifest`）。

        文件在那之后被别人改过 / 删了 → **拒绝动手**，一个字不写；那种情况下
        这份材料再也不会对上了，把按钮灰掉（别让人反复点一个注定失败的东西）。
        拿不到锁是另一回事：那是"稍后再试"，按钮留着。
        """
        if not self._undo:
            self.status.setText('没有可撤销的保存。')
            self.btn_undo.setEnabled(False)
            return {'ok': False, 'reason': '没有可撤销的保存'}
        res = ER.restore_manifest(self._undo)
        if not res.get('ok'):
            self.status.setText('没撤销：' + str(res.get('reason') or '?'))
            if res.get('changed'):
                self._undo = None
            self.btn_undo.setEnabled(bool(self._undo))
            return res
        self._undo = None
        self.btn_undo.setEnabled(False)
        self.changed = True
        self._reread()
        if res.get('deleted'):
            self.status.setText(
                f'已撤销上次保存：{ER.MANIFEST} 在保存前本来不存在，照原样删掉了。')
        else:
            self.status.setText(
                f'已撤销上次保存：{ER.MANIFEST} 回到保存之前那副样子'
                '（退掉的那份留在同目录的 .bak 里）。')
        return res

    # ==================================================================
    #  把声明带走（导出 / 导入一份可移植档案）
    # ==================================================================

    def _ask_export_path(self) -> str:
        """问导出到哪 —— **单独一个方法**：判据要打桩它，不弹模态。"""
        path, _ = QFileDialog.getSaveFileName(
            self, '导出扩展档案',
            str(Path.home() / f'扩展档案{EA.ARCHIVE_SUFFIX}'),
            f'扩展档案 (*{EA.ARCHIVE_SUFFIX});;JSON (*.json)')
        return str(path or '')

    def _ask_import_path(self) -> str:
        """问从哪导入（同上：打桩点）。"""
        path, _ = QFileDialog.getOpenFileName(
            self, '导入扩展档案', str(Path.home()),
            f'扩展档案 (*{EA.ARCHIVE_SUFFIX});;JSON (*.json);;所有文件 (*)')
        return str(path or '')

    def export_archive(self, path: str = '') -> dict:
        """
        当前声明 → 档案文件（`path` 空 = 先问人）。

        只带**形状真的住在文件里**的声明；内置 / 只带开关的记录跳过，并把
        "跳过了几条、为什么"写在状态栏（**不静默**：人要知道带走的东西不全）。
        """
        if not self._root_path:
            self.status.setText('先打开项目 —— 档案要从项目的声明里生成。')
            return {'ok': False, 'reason': '没有项目根目录'}
        arch = self._reg.export_archive(name=Path(self._root_path).name)
        if not arch['extensions']:
            self.status.setText('这个项目还没有可带走的声明（内置 / 只带开关的'
                                '记录不算）。')
            return {'ok': False, 'reason': '没有可导出的声明'}
        target = str(path or '')
        if not target:
            target = self._ask_export_path()
        if not target:
            return {'ok': False, 'reason': '取消了'}
        try:
            write_text_atomic(target, EA.dump_archive(arch))
        except OSError as e:
            self.status.setText(f'导出失败：{e}')
            return {'ok': False, 'reason': str(e)}
        skipped = EA.describe_skipped(self._reg.all())
        note = (f'已导出 {len(arch["extensions"])} 条到 {Path(target).name}')
        if skipped:
            note += (f'；{len(skipped)} 条没带'
                     f'（{"、".join(s["id"] for s in skipped[:3])}'
                     f'{"…" if len(skipped) > 3 else ""} —— 形状在别处）')
        self.status.setText(note + '。别的项目「导入…」就能用。')
        return {'ok': True, 'path': target, 'count': len(arch['extensions']),
                'skipped': skipped}

    def _warn(self, title: str, text: str) -> None:
        """弹一句警告。**单独一个方法**：判据里打桩它，否则模态把测试挂住。"""
        QMessageBox.warning(self, title, text)

    def import_archive(self, path: str = '') -> dict:
        """
        档案 → **内存里的注册表**，然后走「保存到项目」落盘。

        `path` 空 = 先问人。冲突（同名、形状不同）时**当面问**：
        用档案的覆盖 / 保留项目里的 / 取消 —— 默认不写任何东西。
        """
        if not self._root_path:
            self.status.setText('先打开项目 —— 档案要装进项目的声明里。')
            return {'ok': False, 'reason': '没有项目根目录'}
        target = str(path or '') or self._ask_import_path()
        if not target:
            return {'ok': False, 'reason': '取消了'}
        try:
            data = json.loads(Path(target).read_text(encoding='utf-8') or '{}')
        except (OSError, ValueError) as e:
            self.status.setText(f'{Path(target).name} 读不出来：{e}')
            return {'ok': False, 'reason': str(e)}
        res = self._reg.import_archive(data)
        if not res.get('ok') and res.get('problems'):
            self._warn(
                '这份档案读不懂',
                '下面这些地方不对，所以**整份没有导入**：\n\n'
                + '\n'.join(res['problems'][:8])
                + ('\n…' if len(res['problems']) > 8 else ''))
            self.status.setText(f'没导入：{Path(target).name} 有 '
                                f'{len(res["problems"])} 处读不懂。')
            return res
        if not res.get('ok') and res.get('conflicts'):
            res = self._ask_import_conflict(data, res)
            if res is None:
                self.status.setText('没导入：档案里那几条与项目同名，'
                                    '这次什么都没改。')
                return {'ok': False, 'reason': '取消了'}
        # ⚠ 先断开"当前选中条目"：`_save()` 会把表单上的值写进它再 upsert ——
        #   如果它就是刚导入的那条，等于拿旧表单把档案覆盖回去。
        self._current = None
        self._reload_tree()
        before_undo = self._undo
        saved = self._save()
        added = list(res.get('added') or ())
        replaced = list(res.get('replaced') or ())
        told = f'导入：新增 {len(added)} 条'
        if added:
            told += f'（{"、".join(added[:4])}{"…" if len(added) > 4 else ""}）'
        if replaced:
            told += f'，覆盖 {len(replaced)} 条'
        if res.get('same'):
            told += f"，{len(res['same'])} 条本来一样"
        if not saved:
            self.status.setText(told + '｜**还没存盘**：' + self.status.text())
            return {**res, 'saved': False}
        # `_save()` 会把状态栏换成它自己那句；导入的结果**拼在前面**（两件事都
        # 得说：导进了几条、存盘成没成）。导错了要有退路 —— 也写在同一行里，
        # 别指望用户自己发现右边多了个按钮（第九轮）。
        # ⚠ 只在**这次导入真的写了盘**时才说：这次一条都没变（档案里的都一样的）
        #   却提示"能退回"，退的会是更早那次保存 —— 那是骗人。
        if self._undo and self._undo is not before_undo:
            told += '｜导错了可以点「撤销上次保存」整体退回'
        self.status.setText(told + '｜' + self.status.text())
        return {**res, 'saved': True}

    def _ask_import_conflict(self, data: dict, first: dict):
        """同名不同形 → 当面问（覆盖 / 保留 / 取消）。取消返回 None。"""
        ids = [str(i) for i in (first.get('conflicts') or ())]
        shown = '、'.join(ids[:6]) + ('…' if len(ids) > 6 else '')
        box = QMessageBox(self)
        box.setWindowTitle('档案里有同名但形状不同的条目')
        box.setText(f'项目里已经有这些条，形状与档案里不一样：\n\n{shown}')
        box.setInformativeText('现在**什么都没改**。要怎么办？')
        box.setIcon(QMessageBox.Question)
        use_archive = box.addButton('用档案的覆盖', QMessageBox.AcceptRole)
        keep = box.addButton('保留项目里的', QMessageBox.AcceptRole)
        box.addButton('取消', QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is use_archive:
            return self._reg.import_archive(data, on_conflict='replace')
        if clicked is keep:
            return self._reg.import_archive(data, on_conflict='keep')
        return None

    def _reread(self, keep: str = '') -> None:
        """
        保存之后**重新读盘**：树上是文件里真正的样子。

        为什么不能只 `_reload_tree()`（2026-11-07 第六轮）：三方合并可能把
        **别人加的**条目收进文件、也可能把**我删的**条目真删掉 —— 内存里那份
        和文件已经不是一回事。不重读，下一次保存就会拿更旧的内存去盖。
        """
        if not self._root_path:
            return
        from storygraph.story import statement_registry as SR
        SR.clear_cache()
        self._reg = ER.ExtensionRegistry.for_project(self._root_path)
        self._reload_tree()
        self._current = None
        if keep:
            self.focus_on(keep)

    def _new_point(self):
        n = 1
        while self._reg.get(f'扩展点{n}') is not None:
            n += 1
        point = ExtensionPoint(id=f'扩展点{n}', kind='statement',
                               label='新扩展点', source='project')
        self._reg.upsert(point)
        self._reload_tree()
        self._load(point)

    def _delete_point(self):
        if self._current is None:
            return
        p = self._current
        if p.source in ('builtin', 'rules.json') \
                or str(p.source).startswith('plugin:'):
            where = ('工具自带' if p.source == 'builtin'
                     else '旧清单' if p.source == 'rules.json'
                     else '插件（' + str(p.source).split(':', 1)[-1] + '）')
            QMessageBox.information(
                self, '这条删不掉',
                f'「{p.id}」来自{where}，不在这里删 —— 删掉开关记录它照样会被'
                '装回来。要让它不生效，请取消「使用这条扩展」并保存'
                '（工具自带的只能停用；旧清单请用「自定义语句」窗改）。')
            return
        if QMessageBox.question(
                self, '删除扩展点',
                f'把「{p.label or p.id}」从项目的 {ER.MANIFEST} 里删掉？') \
                != QMessageBox.Yes:
            return
        self._reg.remove(p.id)
        if not self._root_path:
            self.status.setText('没有项目目录，删不掉。')
            return
        res = self._reg.save(self._root_path)
        if not res.get('ok'):
            why = ('文件里这条也被人改过，这次**没删**' if res.get('conflicts')
                   else str(res.get('reason') or '?'))
            self.status.setText(f'删除没落盘：{why}')
            # 文件里那条还在 → 树上也得还在（别删完给个"删了"的假象）
            self._reread(p.id)
            return
        self.changed = True
        self._remember_undo(res)
        self._reread()
        self.status.setText(f'已删除「{p.label or p.id}」。')

    def _report_problems(self):
        if self._reg.problems:
            self.status.setText('；'.join(self._reg.problems[:2]))
