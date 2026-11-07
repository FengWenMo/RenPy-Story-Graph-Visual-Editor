"""
storygraph.ui_qt.dynamic_form
=============================
**字段表 → 界面** 的唯一一处映射（Unity 的 Property Drawer 的等价物）。

为什么要有它（用户 2026-11-07）：

> "我们首先要统一三套字段表，不能让它们互不相通……其次需要做一个动态的自定义界面！"
> "太散了！"

在这之前，"一格长什么样"在三个地方各写一遍：

  · `card_editor._spec_cell_editor`（参数格：下拉 / 输入框二选一）；
  · `card_editor` 高级区（7 个硬编码输入框）；
  · `dialog_calls` 的参数表（一列文字 `转场@0; 层=layer`）。

于是同一个"转场表达式"，卡片上是普通输入框、调用窗里要人背 `@0` 语法。
现在只有 `KIND → 控件` 这一张表（见 `ext.schema.FIELD_KINDS`），
谁要画一格就调 `DynamicForm`。

⚠ 这一层**只管画和取值**，不判断值合不合法（合法性归各自的业务层：
语句归 `statement_registry`、批处理归 `batch_edit`）。唯一的例外是
`required` 为空的必填格 —— 那是"表单没填完"，属于界面自己的事。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from storygraph.ui_qt import field_widgets, theme


def _caption(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty('role', 'caption')
    lab.setWordWrap(True)
    return lab


def _group_heading(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty('role', 'group')
    font = QFont(theme.font_family(), theme.font_pt('body'))
    font.setBold(True)
    lab.setFont(font)
    return lab
class DynamicForm(QWidget):
    """按 `[FieldSpec]` 画出来的一组输入控件。"""

    changed = Signal()

    def __init__(self, specs=(), parent=None):
        super().__init__(parent)
        self._specs = list(specs or ())
        self._editors: dict = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self.rebuild(self._specs)

    # ---------- 构建 ----------

    def specs(self) -> list:
        return list(self._specs)

    def rebuild(self, specs):
        self._specs = list(specs or ())
        self._editors = {}
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                # ⚠ 只 `deleteLater()` 不够：控件已经从布局里摘掉，但仍是本窗口的
                #   子控件、**依然可见** —— 实测表现是"旧的一行 + 新的空提示"
                #   叠在一起。`setParent(None)` 才是立刻从画面上拿掉。
                w.setParent(None)
                w.deleteLater()
        last_group = None
        for spec in self._specs:
            group = str(getattr(spec, 'group', '') or '')
            if group and group != last_group:
                self._layout.addWidget(_group_heading(group))
                last_group = group
            self._layout.addWidget(self._row(spec))
        if not self._specs:
            self._layout.addWidget(_caption('这个扩展点还没有字段。'
                                            '在下面「字段」里加一格，'
                                            '卡片 / 表格上就会多一列。'))

    def _row(self, spec) -> QWidget:
        """一行 = 标签（可带说明）+ 控件。勾选框例外：它自己带文字。"""
        host = QWidget()
        lay = QHBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        editor = self._make_editor(spec)
        self._editors[spec.key] = (spec, editor)
        # ⚠ 判断"要不要标签"看**控件**，不看 `kind`：控件 already 造出来了，
        #   它是什么就是什么；按 kind 再判一次就是第三份对照表。
        toggle = field_widgets.is_toggle(editor)
        multi = field_widgets.is_multiline(editor)
        if not toggle:
            lab = QLabel(str(spec.shown) + (' *' if spec.required else ''))
            lab.setMinimumWidth(96)
            lay.addWidget(lab, 0)
            if spec.help:
                lab.setToolTip(str(spec.help))
        lay.addWidget(editor, 1)
        if multi:
            lay.setAlignment(editor, Qt.AlignTop)
        elif spec.help:
            lay.addWidget(_caption(spec.help), 0)
        return host

    def _make_editor(self, spec):
        """
        一格控件 —— **不自己 if/elif**，交给 `field_widgets.make_editor`。

        2026-11-07 第三轮：这里原来是第二份"kind → 控件"的实现（第一份在
        `card_editor.SPEC_EDITOR_KIND`），两份行为还不一样（`transition`
        在这边是输入框、在卡片上是下拉）。用户说的"定义出来的模块和卡片上
        对不上"就是这种不一致。
        """
        return field_widgets.make_editor(
            spec, on_change=lambda *_a: self.changed.emit())

    # ---------- 取值 / 赋值 ----------

    def set_values(self, values) -> None:
        values = dict(values or {})
        for key, (spec, editor) in self._editors.items():
            val = values.get(key, spec.default)
            if spec.fixed:
                # 固定字：**永远显示它自己那个词**，不接受外部值
                # （卡片把某一拍的值传进来时，不该把 `with` 改成别的字）。
                val = spec.shown_literal
            blocked = editor.blockSignals(True)
            try:
                field_widgets.write_value(editor, val)
            finally:
                editor.blockSignals(blocked)

    def values(self) -> dict:
        return {key: field_widgets.read_value(editor)
                for key, (_spec, editor) in self._editors.items()}

    def problems(self) -> list:
        """必填没填 / 数字格写了非数字 —— **界面自己的事**，别的不管。"""
        out = []
        vals = self.values()
        for spec in self._specs:
            val = vals.get(spec.key)
            blank = (val is None or (isinstance(val, str) and not val.strip())
                     or (isinstance(val, list) and not val))
            if spec.required and blank:
                out.append(f'「{spec.shown}」是必填')
                continue
            if spec.kind in ('int', 'number') and isinstance(val, str) \
                    and val.strip():
                try:
                    int(val.strip()) if spec.kind == 'int' else float(val.strip())
                except ValueError:
                    out.append(f'「{spec.shown}」要写数字，收到 {val!r}')
        return out
