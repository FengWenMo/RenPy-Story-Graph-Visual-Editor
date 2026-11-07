# -*- coding: utf-8 -*-
"""
「这条声明在卡片上会长什么样」—— 自定义模块表单页的**实时预览**
==================================================================

用户 2026-11-07（第三轮调研的结论，落到代码）：

> 卡片编辑器右边的 UI 和自定义模块定义出的模块一定要对应！UI 要重构……

为什么预览是"对应"的解药：**让人在配置的时候看见结果**。配置时看不见结果，
就只能靠记忆和想象，而记忆靠不住 —— 于是出现"声明写完了，卡片上还是一团"。

调研里 Odin 给策划做工具的那篇也是这么做的：`[InlineEditor(Expanded = true)]`
就是"编辑的就是最终形态"。

## 纪律

  · **只读**：预览里所有控件都不可改（`setReadOnly` / `setEnabled(False)`），
    它不参与 `collect_fields`，也不写盘；
  · **复用 `card_rows`**：预览的每一行都用真卡片那套构造口 —— 否则"预览"和
    "真卡片"就是两个样子，比没有更糟（用户 2026-11-07："不许东一块西一块"）；
  · **按签名防抖**：`refresh()` 每个按键都会被调一次，签名没变就直接返回，
    不然每敲一个字就重建一遍控件（下拉尤其贵）。
"""

from __future__ import annotations

from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

from storygraph.ui_qt import card_rows

#: 预览里"值"的占位（这一格还没有真值，说明"这一格会在卡片上占一行"）。
_SLOT_HINT = {
    'keyword': '（固定字，剧本里原样出现）',
    'optional': '（可省略的固定字）',
    'variable': '（任意文字）',
    'choice': '（从候选里选）',
    'number': '（数字）',
    'expr': '（一段表达式）',
    'transition': '（转场）',
}


def _clear_layout(lay) -> None:
    """
    清空一个布局（先拆控件、再删布局项）。

    ⚠ 顺序不能反：`takeAt` 拿到的是"布局项"，控件得先 `setParent(None)`
    才真的从屏幕上消失 —— 直接 `deleteLater` 布局会留下一排孤儿控件
    （`card_editor._clear_spec_form` 里记过同一件事）。
    """
    while lay.count():
        item = lay.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        sub = item.layout()
        if sub is not None:
            while sub.count():
                inner = sub.takeAt(0)
                w = inner.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
            sub.deleteLater()


class SpecPreview(QWidget):
    """只读的"卡片预览"：一个标题 + 每格一行。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(card_rows.ROW_GAP)
        self._lay = lay
        #: 上一次渲染的签名（防抖：每个按键都会走到这里）
        self._signature = None
        self.refresh('', (), ())

    # ------------------------------------------------------------------
    def refresh(self, name: str, slots=(), clauses=()) -> None:
        """
        按**当前表单**重画预览。

        `slots`  = `FieldList.rows()` 的产物（`name` / `kind` / `label` / `options`）；
        `clauses`= `_clause_rows()` 的产物（`keyword` / `name` / `kind`）。
        """
        slots = [s for s in (slots or ()) if isinstance(s, dict)]
        clauses = [c for c in (clauses or ()) if isinstance(c, dict)]
        signature = (
            str(name or ''),
            tuple((str(s.get('name') or ''), str(s.get('kind') or ''),
                   tuple(str(o) for o in (s.get('options') or ()))) for s in slots),
            tuple((str(c.get('keyword') or ''), str(c.get('name') or ''))
                  for c in clauses),
        )
        if signature == self._signature:
            return
        self._signature = signature

        _clear_layout(self._lay)
        # ⚠ 这里**不再带 hint**：调用方（`dialog_learn`）已经在外面写了一次
        #   「只读预览」—— 两处都写，屏幕上就是两遍（截图当场看到）。
        self._lay.addWidget(card_rows.section(
            f'卡片上：{name}' if name else '卡片上（还没填开头词）'))
        if not slots and not clauses:
            self._lay.addWidget(card_rows.note(
                '加一格，这里就会立刻长出那一行 —— 和真卡片用的是同一套排布。'))
            return
        for slot in slots:
            self._lay.addWidget(self._slot_row(slot))
        for clause in clauses:
            self._lay.addWidget(self._clause_row(clause))

    # ------------------------------------------------------------------
    def _slot_row(self, slot) -> QWidget:
        """一格 → 卡片上的一行（只读）。"""
        label = str(slot.get('label') or slot.get('name') or '')
        kind = str(slot.get('kind') or '')
        options = [str(o) for o in (slot.get('options') or ())]
        if options:
            # 有候选 → 画成**下拉**（形态要对：让人看出"这里是选，不是随便写"）
            from storygraph.ui_qt.wheel_guard import WheelSafeComboBox
            w = WheelSafeComboBox()
            w.setEditable(True)
            for value in options:
                w.addItem(value)
            if slot.get('limit'):
                w.setToolTip('封闭枚举：剧本里只能是这几个值之一')
            w.setEnabled(False)              # 预览不可改
        else:
            w = QLineEdit()
            w.setReadOnly(True)
            if kind in ('keyword', 'optional'):
                w.setText(str(slot.get('text') or slot.get('name') or ''))
                w.setProperty('role', 'caption')
            else:
                w.setPlaceholderText(_SLOT_HINT.get(kind, '（这一格随便写）'))
        return card_rows.row(label, w)

    def _clause_row(self, clause) -> QWidget:
        """一个子句 → 卡片上的一行（只读，形状与参数格一致）。"""
        keyword = str(clause.get('keyword') or '')
        name = str(clause.get('name') or keyword)
        w = QLineEdit()
        w.setReadOnly(True)
        w.setPlaceholderText(f'{keyword} …')
        return card_rows.row(name or keyword, w)


__all__ = ['SpecPreview']
