# -*- coding: utf-8 -*-
"""
按规则批处理 `.rpy` —— 界面入口（**先预演，后应用**；规则也能在这里配出来）
================================================================================

为什么要有这一层
----------------
`app.batch_edit` 是引擎：给它项目根和一份规则，它算"哪里该多一行 / 哪一行
该换掉"，**不弹窗、不写盘**（只有 `apply_plan` 才写，写前有备份）。可是人
在图/卡片界面里干活，不该为了补一句 `$ renpy.transition(...)` 去开命令行；
反过来，这种"整章上百处"的改动也不能做成一个点了就写的按钮。

2026-11-07 补的入口（用户原话）
-------------------------------
> 「我当然知道！那是 json 文件！是人亲手写进去的！可是我不要手写 json！
>   我这个工具是给编剧设计的！我需要自定义模块中的可视化界面！」

所以这个窗口现在有两个半边：

  · **左边是规则**（列表 + 新建 / 删除）：人只说"**什么时候加**、
    **加什么**"，那行代码由 `app.batch_recipes.build_text` 拼出来，
    底下灰字实时显示"将写成：`$ renpy.transition(Dissolve(0.1), layer="master")`"；
  · **下面是账**：预演报告 + diff，还是原来那套。

界面口径
--------
· **先把话说清楚**：一进来只预演，不动源文件；"应用"在"确实有东西可写"
  之前是灰的。
· **应用前重算计划**：预演之后源文件可能被别人改过。这里不拿"刚才看过
  一眼"当保证，而是重算 + `apply_plan` 里的逐字比对（两道锁）。
· **界面不认识具体语法**：它只认识"对谁 / 做什么 / 在哪 / 写什么"，四种
  "加什么"是**预设表**（`batch_recipes.CUE_KINDS`），不是写死的分支 ——
  换项目、换写法，改的是那张表。
· **认不出的规则不硬凑**：文件里已有、但界面表达不了的规则（`replace` /
  `delete` / 带章名条件的）照样列出来、**保存时原样带回去**，点它只显示
  "这条改不了"和原因 —— 绝不因为"界面没显示"就把用户的规则删了。
· **报告要有数字**：几个文件、插/改/删各几行；失败的文件单独列，不混成
  一句"完成"。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from storygraph.ui_qt import tokens as T

#: 预演窗口里最多显示多少行（diff 可能有几千行；**写入的永远是全部计划**，
#: 这里只是别让一个 QPlainTextEdit 卡住 —— 真正的明细在 --json 里）。
PREVIEW_LIMIT = 4000

#: diff 最多显示多少行（见 `_cap_diff`）。
DIFF_LIMIT = 80

#: 规则文件的默认名（项目根下）。
RULE_FILENAME = 'storygraph.batch.json'


def split_chapters(text: str) -> list:
    """`'a.rpy, b*.rpy'` → `['a.rpy', 'b*.rpy']`（空 = 全部脚本）。"""
    raw = str(text or '').replace('；', ';').replace('，', ',')
    for sep in (';', ','):
        raw = raw.replace(sep, ' ')
    return [part for part in raw.split() if part]


def _cap_diff(diff: str) -> str:
    """
    diff 只给头 `DIFF_LIMIT` 行。

    一千多处插入的 diff 有几万行 —— 全塞进 QPlainTextEdit 只会让窗口卡住，
    而人真正要看的是"会多出哪一行、插在哪"；全量明细在 `format_plan` 的
    报告里和 `--json` 里，这个窗口不兜底。
    """
    text = str(diff or '')
    if not text.strip():
        return '（没有差异）'
    lines = text.split('\n')
    if len(lines) <= DIFF_LIMIT:
        return text
    return '\n'.join(lines[:DIFF_LIMIT]) + (
        f'\n… 还有 {len(lines) - DIFF_LIMIT} 行 diff 没显示（写入的是全部计划）')


def _escape(text: str) -> str:
    """只给"将写成"那段用：`<` `>` `&` 转义（QLabel 认 HTML）。"""
    return (str(text or '').replace('&', '&amp;')
            .replace('<', '&lt;').replace('>', '&gt;'))


class BatchDialog(QDialog):
    """
    规则批处理对话框。

    参数
    ----
    project_root : 项目根（规则默认读它下面的 `storygraph.batch.json`）
    rule_path    : 指定某份规则文件（省略 = 项目根那份）
    """

    #: 真的写盘之后发一次，带 `apply_plan` 的结果（调用方据此重载章节）。
    applied = Signal(dict)

    def __init__(self, project_root, rule_path=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('按规则批处理 .rpy')
        self.resize(1060, 760)
        self._root = Path(project_root) if project_root else None
        self._rule_path = Path(rule_path) if rule_path else None
        self._plan = None
        #: `[{'raw': dict, 'form': dict|None, 'why': str}]`（见 batch_recipes）
        self._items = []
        #: 项目里"开了批处理"的扩展声明（`app.batch_edit.rules_from_extensions`）
        self._declared = []
        #: 合并/装载过程中要人看一眼的话（不阻断）
        self._declared_notes = []
        #: 最近一次 `_rules_for_plan` 的合并说明（同 id 写法不同之类）
        self._merge_notes = []
        self._current = -1
        self._loading = False
        self._build_ui()
        self._load_rules()
        self._refresh_declared()
        self._sync_buttons()

    # ---- 界面 ----

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                               T.SPACE['md'], T.SPACE['md'])
        lay.setSpacing(T.SPACE['sm'])

        cap = QLabel('规则文件')
        cap.setProperty('role', 'section')
        lay.addWidget(cap)

        row = QHBoxLayout()
        row.setSpacing(T.SPACE['sm'])
        self._rule_label = QLabel('')
        self._rule_label.setWordWrap(True)
        self._rule_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self._rule_label, 1)
        pick = QPushButton('换个文件…')
        pick.clicked.connect(self._pick_rule)
        row.addWidget(pick)
        lay.addLayout(row)

        hint = QLabel('规则属于项目（storygraph.batch.json）。左边配「什么时候加、'
                      '加什么」，那行代码会自动拼好 —— 不用手写 JSON。')
        hint.setProperty('role', 'caption')
        hint.setWordWrap(True)
        lay.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(T.SPACE['sm'])
        body.addWidget(self._build_rule_list(), 2)
        body.addWidget(self._build_form(), 5)
        lay.addLayout(body, 1)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(T.SPACE['sm'])
        filter_row.addWidget(QLabel('只处理'))
        self._chapters = QLineEdit()
        self._chapters.setPlaceholderText(
            'game/chapter/chapter1_*.rpy（留空 = 项目里全部脚本）')
        filter_row.addWidget(self._chapters, 1)
        lay.addLayout(filter_row)

        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._view.setMinimumHeight(140)
        lay.addWidget(self._view, 2)

        btns = QHBoxLayout()
        btns.setSpacing(T.SPACE['sm'])
        self._status = QLabel('')
        self._status.setProperty('role', 'caption')
        btns.addWidget(self._status, 1)
        self._btn_save = QPushButton('保存规则')
        self._btn_save.clicked.connect(self.save)
        self._btn_preview = QPushButton('预演')
        self._btn_preview.clicked.connect(self.preview)
        self._btn_apply = QPushButton('应用…')
        self._btn_apply.clicked.connect(self.apply)
        close = QPushButton('关闭')
        close.clicked.connect(self.reject)
        for b in (self._btn_save, self._btn_preview, self._btn_apply, close):
            btns.addWidget(b)
        lay.addLayout(btns)

    def _build_rule_list(self) -> QWidget:
        box = QGroupBox('规则')
        lay = QVBoxLayout(box)
        lay.setSpacing(T.SPACE['sm'])
        self._list = QListWidget()
        self._list.setMinimumWidth(240)
        self._list.currentRowChanged.connect(self._on_select)
        lay.addWidget(self._list, 1)
        bar = QHBoxLayout()
        bar.setSpacing(T.SPACE['sm'])
        self._btn_new = QPushButton('新建')
        self._btn_new.clicked.connect(self.add_rule)
        self._btn_del = QPushButton('删除')
        self._btn_del.clicked.connect(self.remove_rule)
        for b in (self._btn_new, self._btn_del):
            bar.addWidget(b)
        lay.addLayout(bar)
        # 声明出来的批处理（扩展窗口里那个开关）：以前它们只让"预演"看着有东西，
        # 计划里其实没有它们。这里既**随计划一起生效**，也能一键放进列表编辑。
        self._declared_hint = QLabel('')
        self._declared_hint.setProperty('role', 'caption')
        self._declared_hint.setWordWrap(True)
        self._declared_hint.setVisible(False)
        lay.addWidget(self._declared_hint)
        self._btn_import = QPushButton('导入扩展声明…')
        self._btn_import.setToolTip(
            '把项目里「是否能够批处理」打开的声明，作为规则放进这个列表 '
            '（放进来后可以改、可以保存进规则文件）')
        self._btn_import.clicked.connect(lambda: self.import_declared_rules())
        self._btn_import.setVisible(False)
        lay.addWidget(self._btn_import)
        return box

    def _build_form(self) -> QWidget:
        from storygraph.app.batch_recipes import (BUILTIN_EFFECTS, CUE_KINDS,
                                                  LAYERS, MODES)

        box = QGroupBox('这条规则')
        outer = QVBoxLayout(box)
        outer.setSpacing(T.SPACE['sm'])

        grid = QGridLayout()
        grid.setHorizontalSpacing(T.SPACE['sm'])
        grid.setVerticalSpacing(T.SPACE['sm'])
        grid.setColumnStretch(1, 1)
        self._rows = {}

        def add_row(key: str, text: str, widget: QWidget, row: int):
            lab = QLabel(text)
            grid.addWidget(lab, row, 0)
            grid.addWidget(widget, row, 1)
            self._rows[key] = (lab, widget)

        self._name = QLineEdit()
        self._name.setPlaceholderText('给这条规则起个名字（只给自己看）')
        add_row('name', '名字', self._name, 0)

        self._mode = QComboBox()
        for value, text in MODES:
            self._mode.addItem(text, value)
        add_row('mode', '什么时候加', self._mode, 1)

        self._cue = QComboBox()
        for value, text in CUE_KINDS:
            self._cue.addItem(text, value)
        add_row('cue', '加什么', self._cue, 2)

        self._effect = QComboBox()
        self._effect.setEditable(True)
        self._effect.addItems(list(BUILTIN_EFFECTS))
        add_row('effect', '过渡效果', self._effect, 3)

        sec_row = QWidget()
        sec_lay = QHBoxLayout(sec_row)
        sec_lay.setContentsMargins(0, 0, 0, 0)
        sec_lay.setSpacing(T.SPACE['sm'])
        self._seconds = QLineEdit()
        self._seconds.setFixedWidth(80)
        self._seconds.setPlaceholderText('0.1')
        sec_lay.addWidget(self._seconds)
        sec_lay.addWidget(QLabel('秒'))
        sec_lay.addStretch(1)
        add_row('seconds', '时长', sec_row, 4)

        self._layer = QComboBox()
        self._layer.setEditable(True)
        self._layer.addItems(list(LAYERS))
        add_row('layer', '只动这一层', self._layer, 5)

        self._named = QLineEdit()
        self._named.setPlaceholderText('项目里 define 过的转场名，例如 dis_master')
        add_row('named', '转场名字', self._named, 6)

        self._raw = QLineEdit()
        self._raw.setPlaceholderText(
            '例如 $ renpy.transition(Dissolve(0.1), layer="master")')
        add_row('raw', '这一行代码', self._raw, 7)

        self._skip_transition = QCheckBox('这一拍已经有转场就跳过（推荐）')
        self._skip_present = QCheckBox('已经加过就不重复加（推荐）')
        self._always = QCheckBox('玩家关掉转场时也强制生效')
        flags = QHBoxLayout()
        flags.setSpacing(T.SPACE['sm'])
        for w in (self._skip_transition, self._skip_present, self._always):
            flags.addWidget(w)
        flags.addStretch(1)
        grid.addLayout(flags, 8, 0, 1, 2)
        outer.addLayout(grid)

        self._hint = QLabel('')
        self._hint.setWordWrap(True)
        self._hint.setProperty('role', 'caption')
        self._hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self._hint)
        outer.addStretch(1)

        # 任何改动都重算"将写成…"（人不用点任何按钮就能看见自己造出什么）
        for combo in (self._mode, self._cue, self._layer):
            combo.currentIndexChanged.connect(self._on_form_changed)
        self._effect.currentTextChanged.connect(self._on_form_changed)
        self._layer.currentTextChanged.connect(self._on_form_changed)
        for edit in (self._name, self._seconds, self._named, self._raw):
            edit.textChanged.connect(self._on_form_changed)
        for check in (self._skip_transition, self._skip_present, self._always):
            check.toggled.connect(self._on_form_changed)
        return box

    # ---- 规则：读 / 存 / 切 ----

    def _default_rule_path(self):
        if self._rule_path:
            return self._rule_path
        if self._root:
            return self._root / RULE_FILENAME
        return None

    def _uses_project_rules(self) -> bool:
        """
        现在这扇窗用的是不是**项目自己的**规则文件？

        「换个文件…」= "就按这一份来"：那时不并入扩展声明（与
        `cli batch --rule` 同一条口径）。传进来的路径正好等于项目默认文件时，
        仍然算"项目自己的"。
        """
        if self._rule_path is None:
            return True
        if not self._root:
            return False
        try:
            return (Path(self._rule_path).resolve()
                    == (self._root / RULE_FILENAME).resolve())
        except OSError:
            return False

    def _refresh_declared(self) -> None:
        """
        读一次项目里「是否能够批处理」打开的声明（会装扩展层，含 `.py` 插件）。

        ⚠ 这是**这一层才做**的事，不是逐章热路径：`for_project` 会执行项目里的
          插件代码，所以只在开窗/重读时问一次。
        """
        from storygraph.app.batch_edit import rules_from_extensions

        self._declared, self._declared_notes = [], []
        if self._root and self._uses_project_rules():
            self._declared, self._declared_notes = rules_from_extensions(self._root)
        self._update_declared_ui()

    def _update_declared_ui(self) -> None:
        """声明那一条提示 + 「导入」按钮（导入之后要跟着变，别让人对着旧字点）。"""
        have = {str((item.get('raw') or {}).get('id') or '')
                for item in self._items}
        left = [rule for rule in self._declared if rule.id not in have]
        n = len(self._declared)
        self._declared_hint.setVisible(bool(n or self._declared_notes))
        self._btn_import.setVisible(bool(left))
        self._btn_import.setEnabled(bool(left))
        self._btn_import.setText(f'把声明里的 {len(left)} 条放进列表…'
                                 if left else '导入扩展声明…')
        if n and not left:
            self._declared_hint.setText(
                f'扩展声明里的 {n} 条都已经在列表里了 —— 改完点「保存规则」'
                '才会写进规则文件（声明本身还在 storygraph.ext.json 里）。')
        elif n:
            self._declared_hint.setText(
                f'另有 {n} 条来自扩展声明（扩展窗口里「是否能够批处理」打开的'
                '那些）—— 预演和应用时会一并生效；要改它们请回扩展窗口，'
                '或者先导进这个列表再改。')
        elif self._declared_notes:
            self._declared_hint.setText('扩展声明：' +
                                        '；'.join(self._declared_notes[:3]))

    def import_declared_rules(self) -> int:
        """
        扩展声明里的规则 → 规则列表（**已经在列表里的跳过**），返回放进几条。

        为什么要有这一步：声明是"我开了一个开关"，文件才是"能改、能审、能进
        版本库"的那一份。让人能一键把它们落成列表项，改起来才有地方改。
        """
        from storygraph.app.batch_recipes import read_form

        have = {str((item.get('raw') or {}).get('id') or '')
                for item in self._items}
        pending = [rule for rule in self._declared if rule.id not in have]
        if not pending:
            self._status.setText('声明里的规则都已经在列表里了。')
            return 0
        for rule in pending:
            raw = rule.to_dict()
            # `CueRule.to_dict()` 带一个 `source`（那是"从哪读进来的"，
            # 供报错定位用，不是规则内容）—— 规则文件的白名单里没有它，
            # 存回去会被整份拒绝。列表里要的是**能保存的**那一份。
            raw.pop('source', None)
            form, why = read_form(raw)
            self._items.append({'raw': raw, 'form': form, 'why': why})
        self._reload_list()
        self._update_declared_ui()
        self._sync_buttons()
        self._status.setText(
            f'放进来了 {len(pending)} 条 —— 它们原本跟着扩展开关走；'
            '要长期留下就点「保存规则」写成文件。')
        return len(pending)

    def _load_rules(self) -> None:
        """读规则文件 → `self._items`（读不动就空着，把原因留给 preview 说）。"""
        from storygraph.app.batch_edit import BatchRuleError
        from storygraph.app.batch_recipes import load_rules

        self._items = []
        self._current = -1
        path = self._default_rule_path()
        if path is None or not path.is_file():
            self._reload_list()
            return
        try:
            self._items = load_rules(path)
        except BatchRuleError:                  # 坏文件由 preview 那一步说清楚
            self._items = []
        self._reload_list()

    def _entry_text(self, index: int, item, form=None) -> str:
        """
        列表里那一行字。

        `form` 给了就用它现算（正在编辑、还没写回条目时也能看见最新摘要）；
        没给就用条目里存的那份。**算不出来不报错**，写成"还没填完" ——
        人在输入框里打字的中途（时长只打了半个小数点）不该弹任何东西。

        ⚠ 列表那一栏很窄，所以这里**截断**（全文进 tooltip）：截断的是显示，
        不是数据 —— 点进去表单里一个字段都不少。
        """
        from storygraph.app.batch_edit import BatchRuleError
        from storygraph.app.batch_edit import BatchRuleError
        from storygraph.app.batch_recipes import build_rule, describe

        f = form if form is not None else item.get('form')
        if f is not None:
            try:
                built = build_rule(f, rule_id=str(f.get('id') or '临时'))
            except (BatchRuleError, ValueError, KeyError, AttributeError):
                return f'{index + 1}. （还没填完）'   # 半填状态很正常
            full = describe(built)
        else:
            full = (f'{item.get("raw", {}).get("id") or "?"}'
                    '（手写规则，界面只读）')
        name = str(f.get('id') or '') if f is not None else ''
        head = full if len(full) <= 26 else full[:26] + '…'
        return f'{index + 1}. {name}｜{head}' if name else f'{index + 1}. {head}'

    def _full_text_of(self, item, form=None) -> str:
        """tooltip 用：完整摘要（列表截断了，鼠标悬停要能看全）。"""
        from storygraph.app.batch_edit import BatchRuleError
        from storygraph.app.batch_edit import BatchRuleError
        from storygraph.app.batch_recipes import build_rule, describe

        f = form if form is not None else item.get('form')
        if f is None:
            return str(item.get('why') or '这条界面改不了')
        try:
            return describe(build_rule(f, rule_id=str(f.get('id') or '临时')))
        except (BatchRuleError, ValueError, KeyError, AttributeError):
            return '还没填完'

    def _refresh_entry_text(self) -> None:
        """当前条目在列表里的摘要跟着表单更新（不用切走就能看见）。"""
        if not (0 <= self._current < len(self._items)):
            return
        entry = self._list.item(self._current)
        if entry is None:
            return
        form = self._read_form()
        entry.setText(self._entry_text(self._current, self._items[self._current],
                                       form=form))
        entry.setToolTip(self._full_text_of(self._items[self._current], form=form))

    def _reload_list(self) -> None:
        self._loading = True
        self._list.clear()
        for i, item in enumerate(self._items):
            entry = QListWidgetItem(self._entry_text(i, item))
            entry.setToolTip(self._full_text_of(item))
            self._list.addItem(entry)
        self._loading = False
        # ⚠ **必须先把 `_current` 清掉再选行**：`setCurrentRow` 会触发
        # `_on_select` → `_flush_form`，而那时表单里装的还是"上一条/新建前"
        # 的内容；不清就等于把旧表单写进刚选中的那一条（新建时尤其明显：
        # 新规则当场被上一条的内容覆盖，而且一点异常都没有）。
        self._current = -1
        if self._items:
            self._list.setCurrentRow(0)
        else:
            self._fill_form(None, why='还没有规则')

    def _on_select(self, row: int) -> None:
        if self._loading:
            return
        self._flush_form()
        self._current = row
        item = self._items[row] if 0 <= row < len(self._items) else None
        self._fill_form(item.get('form') if item else None,
                        why=(item or {}).get('why', ''))
        self._plan = None
        self._sync_buttons()

    def _flush_form(self) -> None:
        """把表单写回当前条目（切条目 / 预演 / 保存前都要先做这一步）。"""
        if self._loading or not (0 <= self._current < len(self._items)):
            return
        item = self._items[self._current]
        if item.get('form') is None:
            return
        form = self._read_form()
        item['form'] = form
        if form.get('id'):
            item['raw'] = dict(item.get('raw') or {}, id=form['id'])

    def _fill_form(self, form, why: str = '') -> None:
        from storygraph.app.batch_recipes import DEFAULT_FORM

        self._loading = True
        editable = form is not None
        f = dict(DEFAULT_FORM)
        f.update(form or {})
        self._name.setText(str(f.get('id') or ''))
        self._set_combo(self._mode, f.get('mode'))
        self._set_combo(self._cue, f.get('cue'))
        self._effect.setCurrentText(str(f.get('effect') or ''))
        self._seconds.setText(str(f.get('seconds') or ''))
        self._layer.setCurrentText(str(f.get('layer') or ''))
        self._named.setText(str(f.get('named') or ''))
        self._raw.setText(str(f.get('raw') or ''))
        self._always.setChecked(bool(f.get('always')))
        self._skip_transition.setChecked(
            bool(f.get('skip_existing_transition')))
        self._skip_present.setChecked(bool(f.get('skip_if_present')))
        self._loading = False
        for _lab, widget in self._rows.values():
            widget.setEnabled(editable)
        for w in (self._skip_transition, self._skip_present, self._always):
            w.setEnabled(editable)
        if not editable:
            self._hint.setText(
                f'⚠ 这条规则在界面里改不了：{why or "它用了界面没表达的条件"}'
                '（保存时会原样带回去，不会被删）')
        else:
            self._sync_cue_controls()
            self._refresh_hint()

    @staticmethod
    def _set_combo(combo: QComboBox, value) -> None:
        idx = combo.findData(str(value or ''))
        if idx >= 0:
            combo.setCurrentIndex(idx)
        elif combo.isEditable():
            combo.setCurrentText(str(value or ''))

    def _on_form_changed(self) -> None:
        if self._loading:
            return
        self._sync_cue_controls()
        self._refresh_hint()
        self._refresh_entry_text()
        self._plan = None
        self._btn_apply.setEnabled(False)

    def _sync_cue_controls(self) -> None:
        """按「加什么」决定哪几格有意义（没意义的灰掉，不是藏起来 —— 眼不跳）。"""
        cue = str(self._cue.currentData() or '')
        want = {
            'master_transition': ('effect', 'seconds', 'layer'),
            'full_transition': ('effect', 'seconds'),
            'named': ('named',),
            'raw': ('raw',),
        }.get(cue, ('effect', 'seconds'))
        editable = bool(self._items) and self._btn_save.isEnabled()
        for key, (_lab, widget) in self._rows.items():
            if key in ('name', 'mode', 'cue'):
                continue
            widget.setEnabled(editable and key in want)

    def _refresh_hint(self) -> None:
        from storygraph.app.batch_edit import BatchRuleError

        try:
            rule = self._rule_from_form()
        except (BatchRuleError, ValueError) as exc:   # 不合法就当场说
            self._hint.setText(f'⚠ {exc}')
            return
        text = (rule.get('insert') or {}).get('text') or ''
        self._hint.setText(
            f'将写成：<code>{_escape(text)}</code><br>'
            '<span style="color:#888">（这就是最后进 .rpy 的那一行；'
            '缩进会自动跟上面的语句对齐）</span>')

    def _read_form(self) -> dict:
        return {
            'id': self._name.text().strip(),
            'mode': str(self._mode.currentData() or ''),
            'cue': str(self._cue.currentData() or ''),
            'effect': self._effect.currentText().strip(),
            'seconds': self._seconds.text().strip(),
            'layer': self._layer.currentText().strip(),
            'named': self._named.text().strip(),
            'raw': self._raw.text().strip(),
            'always': self._always.isChecked(),
            'skip_existing_transition': self._skip_transition.isChecked(),
            'skip_if_present': self._skip_present.isChecked(),
        }

    def _rule_from_form(self) -> dict:
        """当前表单 → 一条规则 dict（走唯一校验）。名字留空就用序号。"""
        from storygraph.app.batch_recipes import build_rule

        form = self._read_form()
        rid = form.get('id') or self._suggest_id()
        built = build_rule(form, rule_id=rid)
        self._check_unique_id(rid)
        return built

    def _suggest_id(self) -> str:
        from storygraph.app.batch_recipes import next_id

        return next_id(self._items)

    def _check_unique_id(self, rid: str) -> None:
        for i, item in enumerate(self._items):
            if i == self._current:
                continue
            if str((item.get('raw') or {}).get('id') or '') == rid:
                raise ValueError(f'规则名字「{rid}」和另一条重了，换一个')

    # ---- 规则的增删 ----

    def add_rule(self) -> None:
        from storygraph.app.batch_recipes import DEFAULT_FORM, next_id

        self._flush_form()
        self._current = -1                      # 见 `_reload_list` 的注释
        form = dict(DEFAULT_FORM)
        form['id'] = next_id(self._items)
        self._items.append({'raw': {'id': form['id']}, 'form': form, 'why': ''})
        self._reload_list()
        self._list.setCurrentRow(len(self._items) - 1)
        self._status.setText(
            f'新建了「{form["id"]}」：选好「什么时候加 / 加什么」，再点「预演」。')

    def remove_rule(self) -> None:
        if not (0 <= self._current < len(self._items)):
            return
        item = self._items[self._current]
        name = str((item.get('raw') or {}).get('id') or
                   (item.get('form') or {}).get('id') or '')
        answer = QMessageBox.question(
            self, '删掉这条规则？',
            f'「{name}」会从规则列表里去掉（还没写进文件，点「保存规则」才生效）。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self._items.pop(self._current)
        self._current = -1
        self._reload_list()
        self._sync_buttons()

    def _pick_rule(self) -> None:
        start = str(self._root or Path.cwd())
        path, _ = QFileDialog.getOpenFileName(
            self, '选规则文件', start, '规则 JSON (*.json);;所有文件 (*)')
        if not path:
            return
        self._rule_path = Path(path)
        self._plan = None
        self._view.setPlainText('')
        self._status.setText('')
        self._load_rules()
        # 换了一份文件 → "用不用扩展声明"的答案可能会变（显式文件 = 不并入）
        self._refresh_declared()
        self._sync_buttons()

    # ---- 保存规则 ----

    def save(self):
        """把界面上的规则写回 `storygraph.batch.json`（原子写 + 备份）。"""
        from storygraph.app.batch_edit import BatchRuleError
        from storygraph.app.batch_recipes import save_rules

        self._flush_form()
        path = self._default_rule_path()
        if path is None:
            QMessageBox.warning(self, '存不了', '还没打开项目，也没选规则文件。')
            return None
        try:
            save_rules(path, self._items)
        except (BatchRuleError, OSError, ValueError) as exc:
            QMessageBox.warning(self, '没有保存', str(exc))
            return None
        self._rule_path = path
        self._load_rules()
        self._update_declared_ui()
        self._status.setText(f'规则已保存：{path.name}（{len(self._items)} 条）')
        self._sync_buttons()
        return path

    # ---- 计划 / 写盘 ----

    def _sync_buttons(self) -> None:
        """没有规则 / 没有待处理 / 计划报错 → 应用按钮灰着。"""
        ready = bool(self._plan is not None
                     and not self._plan.errors
                     and self._plan.pending)
        # 只有扩展声明、还没有规则文件时，也**能**预演（声明随计划跑）
        has_rules = (bool(self._items) or bool(self._rule_path)
                     or bool(self._declared))
        # 一条规则都没有时"保存"也灰：不然点下去只会得到"一条规则都没有"。
        # 只有扩展声明时同样灰：声明在 `storygraph.ext.json` 里，这里的"保存"
        # 写的是规则文件 —— 想落成文件请先「把声明放进列表」。
        self._btn_save.setEnabled(bool(self._items
                                       and (self._root or self._rule_path)))
        self._btn_new.setEnabled(bool(self._root))
        self._btn_del.setEnabled(0 <= self._current < len(self._items))
        self._btn_preview.setEnabled(bool(self._root and has_rules))
        self._btn_apply.setEnabled(ready)
        path = self._default_rule_path()
        # "有规则文件"= **磁盘上真有**那份文件（列表里装着东西不代表存过）
        # 或显式选了别的文件。只有声明/只有界面上的规则时，别把
        # `storygraph.batch.json` 显示成"规则文件" —— 那会让人以为已经存过一次了。
        file_exists = bool(self._rule_path and Path(self._rule_path).is_file())
        if self._items and not file_exists:
            self._rule_label.setText(
                f'{path}（还没保存 · 界面里 {len(self._items)} 条）'
                if path is not None
                else f'界面里 {len(self._items)} 条（还没保存）')
        elif file_exists:
            self._rule_label.setText(
                f'{path}（{len(self._items)} 条）' if self._items else str(path))
        elif self._declared:
            self._rule_label.setText(
                f'还没有规则文件 —— 但扩展声明里有 {len(self._declared)} 条'
                '（预演会带上它们）')
        else:
            self._rule_label.setText(
                '还没有规则文件 —— 点左边「新建」配一条，或右上角换一份')
        self._sync_cue_controls()

    def _rules_for_plan(self) -> list:
        """界面上的规则 → `[CueRule]`（与保存走同一条序列化/校验路径）。"""
        import json

        from storygraph.app.batch_edit import (load_rule_file, merge_declared_rules,
                                               parse_rule)
        from storygraph.app.batch_recipes import dump_rules

        base = []
        if self._items:
            data = json.loads(dump_rules(self._items))
            base = [parse_rule(r, f'规则 {r.get("id")!r}')
                    for r in data.get('rules') or ()]
        elif self._rule_path:
            base = load_rule_file(self._rule_path)
        # 声明出来的规则**随计划一起跑**（同 id 以文件/列表那份为准，见
        # `merge_declared_rules`）。没有它们，开关打开也只是"预演时看着有"。
        base, self._merge_notes = merge_declared_rules(base, self._declared)
        if not base:
            raise ValueError('还没有规则文件')
        return base

    def _build_plan(self):
        """读规则 + 扫项目 → 计划；失败抛给调用方显示，不弹二次错误框。"""
        from storygraph.app.batch_edit import BatchRuleError, plan_batch
        if not self._root or not self._root.is_dir():
            raise BatchRuleError(f'项目目录不存在：{self._root}')
        self._merge_notes = []
        self._flush_form()
        if not self._items and not self._rule_path and not self._declared:
            raise BatchRuleError('还没有规则：点左边「新建」配一条，再预演')
        rules = self._rules_for_plan()
        return plan_batch(self._root, rules,
                          chapters=split_chapters(self._chapters.text()))

    def preview(self):
        """算一遍计划、把报告 + diff 摊在窗口里；**不写盘**。返回计划。"""
        from storygraph.app.batch_edit import (BatchRuleError, format_diff,
                                               format_plan)
        try:
            plan = self._build_plan()
        except BatchRuleError as exc:
            self._plan = None
            self._view.setPlainText(str(exc))
            self._status.setText('规则有问题，没有生成计划')
            self._sync_buttons()
            return None
        except (OSError, ValueError) as exc:
            self._plan = None
            self._view.setPlainText(f'算不了计划：{exc}')
            self._status.setText('')
            self._sync_buttons()
            return None

        self._plan = plan
        # 顺序：**摘要 → diff → 每份文件的明细**。一千多处的计划里，人要
        # 先看"到底会多出什么字"，明细是后面查账用的；反过来放，diff 会被
        # 挤到几百行之外，等于没给看。
        report = format_plan(plan, limit=4)
        head, _sep, body = report.partition('\n')
        chunks = [head]
        if plan.errors:
            chunks.append('（有问题，不会写：' + '；'.join(plan.errors) + '）')
        else:
            chunks.append(_cap_diff(format_diff(plan)))
        notes = [*self._declared_notes, *self._merge_notes]
        if notes:
            chunks.append('注：' + '；'.join(notes[:4]))
        if body.strip():
            chunks.append(body)
        lines = '\n\n'.join(chunks).split('\n')
        if len(lines) > PREVIEW_LIMIT:
            extra = len(lines) - PREVIEW_LIMIT
            lines = lines[:PREVIEW_LIMIT] + [f'… 还有 {extra} 行没显示'
                                             '（写入的是全部计划）']
        self._view.setPlainText('\n'.join(lines))
        extra = (f' · 另含 {len(self._declared)} 条扩展声明'
                 if self._declared else '')
        self._status.setText(
            f'{len(plan.files)} 个文件 · {plan.pending} 处待处理{extra}'
            if not plan.errors else '有错误，不能写')
        self._sync_buttons()
        return plan

    def apply(self):
        """确认后写盘。**写前重算计划**，不拿预演时那份直接用。"""
        from storygraph.app.batch_edit import BatchRuleError, apply_plan
        try:
            plan = self._build_plan()
        except (BatchRuleError, OSError, ValueError) as exc:
            self._plan = None
            self._sync_buttons()
            QMessageBox.warning(self, '没有写', str(exc))
            return None
        if plan.errors:
            self._plan = plan
            self._sync_buttons()
            QMessageBox.warning(self, '没有写', '\n'.join(plan.errors[:5]))
            return None
        if not plan.pending:
            self._plan = plan
            self._sync_buttons()
            QMessageBox.information(self, '没有要改的',
                                    '这条规则在当前范围里已经没有待处理的位置'
                                    '（可能上次已经写过了）。')
            return None

        what = []
        n_ins = sum(len(f.insertions) for f in plan.files)
        n_rep = sum(1 for f in plan.files for e in f.edits
                    if e.kind == 'replace')
        n_del = sum(1 for f in plan.files for e in f.edits
                    if e.kind == 'delete')
        for label, n in (('插入', n_ins), ('替换', n_rep), ('删除', n_del)):
            if n:
                what.append(f'{label} {n} 处')
        answer = QMessageBox.question(
            self, '写入源文件？',
            f'将在 {len(plan.files)} 个 .rpy 里{"、".join(what)}。\n'
            '写前会自动备份；写的时候会逐字核对源文件，'
            '期间被别人改过的文件跳过不写。\n\n继续？',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return None

        result = apply_plan(plan)
        self._plan = None
        self._view.clear()
        self._status.setText(
            f'已写 {len(result["written"])} 个文件：'
            f'+{result["inserted"]} ~{result["replaced"]} '
            f'-{result["deleted"]}')
        self._sync_buttons()
        if result['failed']:
            self._view.setPlainText(
                '这些文件没写：\n' + '\n'.join(
                    f'  {rel}：{why}' for rel, why in result['failed']))
            QMessageBox.warning(
                self, '有一部分没写',
                '写成了 {0} 个，另有 {1} 个跳过（原因见窗口）：\n{2}'.format(
                    len(result['written']), len(result['failed']),
                    '\n'.join(rel for rel, _why in result['failed'])))
        self.applied.emit(dict(result))
        return result


__all__ = ['BatchDialog', 'DIFF_LIMIT', 'PREVIEW_LIMIT', 'RULE_FILENAME',
           'split_chapters']
