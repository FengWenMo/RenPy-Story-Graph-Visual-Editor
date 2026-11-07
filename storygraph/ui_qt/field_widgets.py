"""
storygraph.ui_qt.field_widgets
==============================
**语义类型 → 控件**的唯一一处画法（Unity 的 Property Drawer 的等价物）。

为什么要有它（2026-11-07 第三轮）
---------------------------------
上一轮把"一个字段的类型"收成了一套词（`ext.schema.FIELD_KINDS`），
但**"什么类型画什么控件"仍有两份实现**：

    · `dynamic_form._make_editor`      —— if 链，按 `kind` 走（扩展窗口用）
    · `card_editor._spec_cell_editor`  —— `SPEC_EDITOR_KIND` 字典 + if 链
                                          （卡片参数格用）

两份的**行为不一致**，而且不一致的地方正是用户能看见的：
`transition` 在卡片上是下拉（项目有 transform 候选），在扩展窗口里是普通
输入框；`code` 在卡片上带行号、在窗口里是纯多行。于是"定义出来的模块"
和"卡片上那一格"长得不一样 —— 用户 2026-11-07：

> 卡片编辑器右边的 UI 和自定义模块定义出的模块一定要对应！

现在只有 `make_editor()`：**kind → 控件类**（`ext.schema.KIND_TO_WIDGET`）
→ 这一个函数。两处界面都调它，判据直接比控件类
（`tests/test_ext_card_fields.py::test_card_and_form_build_the_same_widget`）。

读写值也在这里（`read_value` / `write_value`）
---------------------------------------------
"怎么读这一格"同样只有一份。它们按**控件类**分派（不是按 `kind`）——
控件已经造出来了，它是什么类就是什么读法，不可能与画法对不上。
`card_editor` 只额外做两件业务后处理（`SayTail` 的 `with …` 子句、参数格折成
一行），那不属于"控件怎么读"。

⚠ 本模块**只管画和读**，不判断值合不合法（合法性归语句 / 批处理各自的
业务层）。唯一的例外是必填没填 —— 那是"表单没填完"，属于界面自己的事
（`DynamicForm.problems`）。
"""
from __future__ import annotations

from PySide6.QtGui import QDoubleValidator, QFont, QIntValidator
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLineEdit,
                               QPlainTextEdit, QWidget)

from storygraph.ext.schema import CHOICE_LIKE, PARAM_PLACEHOLDER, PARAM_TIP
from storygraph.ui_qt.code_edit import CodeEditor
from storygraph.ui_qt.labeled_choices import (as_choices, combo_value,
                                              fill_combo, select_value)

#: 代码 / 多行框的默认行数（× 行高 + 边距 = 固定高度）。
CODE_ROWS = 4

#: 这些控件类**本来就能换行**：它们的值在读回来时要按原样（不折行）。
_MULTILINE = (QPlainTextEdit,)


def _mono(widget):
    """等宽字体（只是好看；拿不到也不致命）。"""
    font = QFont('Consolas')
    font.setStyleHint(QFont.Monospace)
    widget.setFont(font)
    return widget


def _line(parent=None, *, placeholder: str = '', tip: str = '', mono=False,
          readonly=False, validator=None) -> QLineEdit:
    edit = QLineEdit(parent)
    if placeholder:
        edit.setPlaceholderText(placeholder)
    if tip:
        edit.setToolTip(tip)
    if readonly:
        edit.setReadOnly(True)
        edit.setProperty('role', 'caption')
    if validator is not None:
        edit.setValidator(validator)
    if mono and not readonly:
        _mono(edit)
    return edit


def _combo(choices, *, tip: str = '', editable: bool = True) -> QComboBox:
    """
    下拉：**屏幕上可能是人话，数据里是真值**（`labeled_choices` 的契约）。

    `editable`（候选不是限死的时候）—— 候选只是提示：项目里临时用了新值也
    得能填进去（与 Excel 那套口径一致）。
    """
    box = QComboBox()
    box.setEditable(bool(editable))
    fill_combo(box, as_choices(choices), '', blank=True)
    if tip:
        box.setToolTip(tip)
    if editable:
        # 手打时下拉不该把候选列表弹到脸上（`editable` 的默认行为会）
        box.setInsertPolicy(QComboBox.NoInsert)
    return box


def _multi(choices) -> QWidget:
    """多选：一排勾选框（`objectName` 记真值，见 `read_value`）。"""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    for choice in as_choices(choices):
        cb = QCheckBox(str(choice.display or choice.value))
        cb.setObjectName(f'multi:{choice.value}')
        if choice.tip:
            cb.setToolTip(str(choice.tip))
        row.addWidget(cb)
    row.addStretch(1)
    return box


def _code(rows: int = CODE_ROWS, *, tip: str = '', numbered: bool = True):
    """一段代码 / 多行：`numbered` = 带行号栏（`CodeEditor`）。"""
    if numbered:
        edit = CodeEditor()
        edit.setFixedHeight(int(rows) * 20 + 12)
    else:
        edit = QPlainTextEdit()
        edit.setFixedHeight(int(rows) * 20 + 12)
        _mono(edit)
    if tip:
        edit.setToolTip(tip)
    return edit


def _widget_of(spec, picked, parser_only: bool) -> str:
    """
    `kind` + 有没有候选 + 是不是参数格 → **控件名**（`KIND_TO_WIDGET` 那套词）。

    三条规则，都在这里说一次（从前它们散在两处 if 链里，就是"两份实现"
    的源头）：

      · 参数格（值要折回语句头）不许是开关/多选 —— 复选框读出来是 True、
        多选读出来是列表，折进头行会写出 `foo True` 这种脚本里没有的东西；
      · 说"从候选选"但一个候选都没有 → 输入框（空下拉比输入框更难用）；
      · `choice` / `transition`（`CHOICE_LIKE`）**有候选就给下拉**，
        转场那格就是这条（项目有 transform 候选）。
    """
    widget = str(getattr(spec, 'widget', None) or 'text')
    kind = str(getattr(spec, 'kind', 'variable') or 'variable')
    if parser_only and widget in ('bool', 'multi_choice'):
        return 'text'
    if widget == 'choice' and not picked:
        return 'text'
    if kind in CHOICE_LIKE and picked:
        return 'choice'
    return widget


def _hint_of(spec, kind: str, parser_only: bool) -> str:
    """灰字提示：字段自己写的优先，其次才是参数格那套按类型的默认值。"""
    hint = str(getattr(spec, 'placeholder', '') or '')
    if not hint and parser_only:
        hint = PARAM_PLACEHOLDER.get(kind, '')
    return hint


def _tip_of(spec, widget: str, parser_only: bool, tip: str) -> str:
    """tooltip：调用方给的优先（固定字那格要说"去哪改"），其次字段的说明。"""
    told = str(tip or getattr(spec, 'help', '') or '')
    if not told and parser_only:
        told = PARAM_TIP.get(widget, '')
    return told


def _new_widget(spec, widget: str, picked, hint: str, told: str,
                code_rows: int) -> QWidget:
    """按控件名造控件（**只造，不连接**）。"""
    if widget == 'bool':
        box = QCheckBox(str(getattr(spec, 'shown', '') or spec.key))
        if told:
            box.setToolTip(told)
        return box
    if widget == 'multi_choice':
        return _multi(picked)
    if widget == 'choice':
        # `limit` = 候选是限死的（写别的词就是错）；否则可编辑
        return _combo(picked, tip=told,
                      editable=not bool(getattr(spec, 'limit', False)))
    if widget == 'number':
        return _line(placeholder=hint, tip=told, mono=True,
                     validator=QDoubleValidator())
    if widget == 'int':
        return _line(placeholder=hint, tip=told, mono=True,
                     validator=QIntValidator())
    if widget == 'code':
        return _code(code_rows, tip=told, numbered=True)
    if widget == 'multiline':
        return _code(code_rows, tip=told, numbered=False)
    if widget == 'expr':
        return _line(placeholder=hint, tip=told, mono=True)
    return _line(placeholder=hint, tip=told)


def _fixed_widget(spec, told: str) -> QLineEdit:
    """固定字：剧本里必须原样出现。**只读**（改它等于改语句名）。"""
    edit = _line(tip=told or f'这一格是固定字：{spec.key}', mono=True,
                 readonly=True)
    edit.setText(str(getattr(spec, 'shown_literal', '') or spec.key))
    return edit


def make_editor(spec, *, choices=None, parser_only: bool = False,
                tip: str = '', code_rows: int = CODE_ROWS,
                on_change=None) -> QWidget:
    """
    一个字段 → 一格控件。**全库只此一处** kind → 控件。

    `choices`     —— 覆盖 `spec.choices`（项目的候选是运行时才知道的，
                     比如 `with` 那格的 transform 列表）；
    `parser_only` —— 这一格的值要**折回语句头**（卡片上的参数格）。只影响
                     两件事：`bool` / `多选` 退回输入框（复选框折不进一行
                     脚本），以及灰字提示 / tooltip 用参数格那套说法；
    `tip`         —— 覆盖提示文字（固定字那格要说明"去哪改"）；
    `on_change`   —— 每次改动回调一次（信号差异在这里吞掉，调用方不用按
                     控件类各连一遍）。
    """
    kind = str(getattr(spec, 'kind', 'variable') or 'variable')
    picked = tuple(choices if choices is not None else
                   (getattr(spec, 'choices', ()) or ()))
    widget = _widget_of(spec, picked, parser_only)
    told = _tip_of(spec, widget, parser_only, tip)
    if getattr(spec, 'fixed', False):
        return _fixed_widget(spec, told)
    w = _new_widget(spec, widget, picked,
                    _hint_of(spec, kind, parser_only), told, code_rows)
    if on_change is not None:
        connect_change(w, on_change)
    return w


def connect_change(widget, callback) -> None:
    """把"这一格变了"接到 `callback()`（信号差异全在这一处吞掉）。"""
    def fire(*_a):
        callback()

    if isinstance(widget, QComboBox):
        widget.currentIndexChanged.connect(fire)
        # 可编辑下拉：手打的字变了也要算（`currentIndexChanged` 不会响）
        widget.editTextChanged.connect(fire)
    elif isinstance(widget, QPlainTextEdit):
        widget.textChanged.connect(fire)
    elif isinstance(widget, QLineEdit):
        widget.textChanged.connect(fire)
    elif isinstance(widget, QCheckBox):
        widget.toggled.connect(fire)
    else:
        # 多选：一排勾选框，各连一次（没有单一信号）
        for cb in widget.findChildren(QCheckBox):
            cb.toggled.connect(fire)


def is_multiline(widget) -> bool:
    """这一格是不是**多行框**（布局要顶对齐、旁边不放说明）。"""
    return isinstance(widget, _MULTILINE)


def is_toggle(widget) -> bool:
    """这一格是不是**勾选框**（它自己带文字，不再加标签）。"""
    return isinstance(widget, QCheckBox)


# ---------------------------------------------------------------- 读写值

def read_value(widget):
    """控件 → 值（`bool` / 字符串 / 多选列表）。认不出 → 空串。"""
    if isinstance(widget, QCheckBox):
        return bool(widget.isChecked())
    if isinstance(widget, QComboBox):
        return combo_value(widget)
    if isinstance(widget, QPlainTextEdit):
        return widget.toPlainText()
    if isinstance(widget, QLineEdit):
        return widget.text()
    boxes = [cb for cb in widget.findChildren(QCheckBox)
             if str(cb.objectName()).startswith('multi:')]
    if boxes:
        return [str(cb.objectName()).split(':', 1)[-1]
                for cb in boxes if cb.isChecked()]
    return ''


def write_value(widget, value) -> None:
    """值 → 控件（与 `read_value` 互逆；下拉按**真值**选、按人话显示）。"""
    if isinstance(widget, QCheckBox):
        widget.setChecked(bool(value))
        return
    if isinstance(widget, QComboBox):
        raw = '' if value is None else str(value)
        if not select_value(widget, raw) and raw and not widget.isEditable():
            # 不可编辑 + 值不在候选里：退回空白那一项（保留用户原来选的会
            # 显示成"选的是 A，实际值是 B"—— 那比空着糟得多）。
            widget.setCurrentIndex(0)
        return
    if isinstance(widget, QPlainTextEdit):
        widget.setPlainText('' if value is None else str(value))
        return
    if isinstance(widget, QLineEdit):
        widget.setText('' if value is None else str(value))
        return
    picked = {str(v) for v in (value or ())} if isinstance(value, (list, tuple)) \
        else {str(value or '')}
    for cb in widget.findChildren(QCheckBox):
        name = str(cb.objectName())
        if name.startswith('multi:'):
            cb.setChecked(name.split(':', 1)[-1] in picked)


def text_of(widget) -> str:
    """"这一格现在显示的字符串"（色块那种要跟着输入变的地方用）。"""
    value = read_value(widget)
    return '' if value is None else str(value)


__all__ = ['CODE_ROWS', 'connect_change', 'is_multiline', 'is_toggle',
           'make_editor', 'read_value', 'text_of', 'write_value']
