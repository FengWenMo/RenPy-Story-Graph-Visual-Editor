"""
storygraph.ui_qt.choice_picker
==============================
**可搜索的候选选择器** —— 右键 chip 时弹它。

为什么需要（用户 2026-10-05）：

> 既然变换如此多，那为什么不加个右键列表筛选跟搜索功能呢？

`SpriteCardWidget` 的 chip 是"点一下换下一个"——候选少时够用，而真实项目的
位置候选有 **151 个**（146 个自定义 transform + 内置位置），点 40 次才轮到
`i11`，那不叫能用。这里给一个"输入即筛 + 列表点选"的弹窗：

  · `filter_choices(choices, query)` —— **纯函数**（可单测，不碰 Qt）；
  · `ChoicePickerDialog` / `choose_from(...)` —— 真弹窗（`None` = 用户取消）。

过滤口径：去空白、**忽略大小写**、**子串匹配**（`i1` 能筛出 `i11`/`bi11`）；
查询为空 → 全给。刻意不做模糊打分：候选就是"要写进脚本的名字"，
排序必须稳定可预期（按传入顺序），否则用户找东西的手感每次都变。

## 显示值 ≠ 真值（2026-10-31）

词汇表生效之后，候选**看起来**是 `1人，近处`，**选出来**必须是 `i11`。
所以候选交给这个弹窗的形状与下拉同一套（`labeled_choices.as_choices`）：

    'i11'                    → 显示与真值相同
    ('i11', '1人，近处')      → 3 元组才是 (值, 显示, 提示)
    ('i11', '', '说明')       → 2 元组 = (值, 提示)

⚠ 屏幕上是 `display`、`item.data(UserRole)` 是真值、`filter` **两栏都搜**
（搜 `i11` 和搜 `1人` 都能找到同一条）—— 只搜显示值的话，记得原名的人
就再也搜不到了。

## 分组（用户 2026-10-10）

> 实际上需要分组显示……但因为我这个要求通用！所以一般人其实是没有服装这个
> 选项的！所以事实上应当通过 B 这个自定义模块，做到把服装跟表情这种东西分开！

`groups` 给了就在列表里插**不可点的组标题**（`服装` / `表情`…），
筛选时**空组连标题一起消失**。没给 `groups` 时行为与从前**完全一样**。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QVBoxLayout)

from storygraph.ui_qt.labeled_choices import Choice, as_choices

__all__ = ['ChoicePickerDialog', 'choose_from', 'filter_choices',
           'filter_groups', 'filter_pairs']


def filter_pairs(choices, query: str = '') -> list:
    """
    候选 → 过滤后的 `[Choice, …]`（**纯函数**，筛选口径的唯一实现）。

    · 查询为空（或只有空白）→ 原样返回（顺序不动）；
    · 否则忽略大小写做**子串**匹配：**显示值、真值、提示** 三栏都算命中。

    为什么是子串而不是模糊打分：候选就是"脚本里要写的那个名字"，
    顺序稳定比"聪明"重要 —— 每次筛出来的次序都变，用户的手指就记不住位置。
    """
    items = as_choices(choices)
    q = str(query or '').strip().lower()
    if not q:
        return items
    return [c for c in items
            if q in c.value.lower() or q in c.text.lower()
            or q in str(c.tip or '').lower()]


def filter_choices(choices, query: str = '') -> list:
    """候选 → 过滤后的**值**（`filter_pairs` 的字符串版，老调用方用的）。"""
    return [c.value for c in filter_pairs(choices, query)]


def filter_groups(groups, query: str = '') -> list:
    """
    分组候选 → 过滤后的分组（**纯函数**）：组内先筛，**空组整条丢掉**
    （否则筛选结果里会剩一堆光秃秃的标题）。

    输入 `[{'group','label','values'}]`；输出同形（顺序不变，`values` 仍是值）。
    """
    out = []
    for g in (groups or ()):
        vals = filter_choices((g or {}).get('values'), query)
        if not vals:
            continue
        out.append({'group': str((g or {}).get('group') or ''),
                    'label': str((g or {}).get('label') or ''),
                    'values': vals})
    return out


class ChoicePickerDialog(QDialog):
    """一行筛选框 + 一个列表；双击/回车即选定。给 `groups` 就带组标题。"""

    def __init__(self, parent=None, title: str = '选择', choices=None,
                 current: str = '', groups=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(360, 460)
        #: **分组候选**（每一项的 `values` 已经归一成 `Choice`）
        self._groups = [{'group': str(g.get('group') or ''),
                         'label': str(g.get('label') or ''),
                         'values': as_choices(g.get('values'))}
                        for g in (groups or ()) if as_choices(g.get('values'))]
        #: **扁平候选**（无分组时用它；有分组时它是各组的并集，判"共几个"）
        self._all = (as_choices(choices) if not self._groups
                     else [c for g in self._groups for c in g['values']])
        self.value = ''

        lay = QVBoxLayout(self)
        self._hint = QLabel(f'共 {len(self._all)} 个候选 —— 输入即筛，'
                            f'双击选定（Esc 取消）')
        self._hint.setProperty('role', 'caption')
        self._hint.setWordWrap(True)
        lay.addWidget(self._hint)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText('筛选…（例：i1 / 近处）')
        self._filter.textChanged.connect(self._refill)
        lay.addWidget(self._filter)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._accept_item)
        lay.addWidget(self._list, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_current)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self._refill('')
        self._select(current)

    # ------------------------------------------------------------------

    def _add(self, choice: Choice):
        """一行真候选：**屏幕上是 display，数据里是真值**。"""
        item = QListWidgetItem(choice.text)
        item.setData(Qt.UserRole, choice.value)
        if choice.tip:
            item.setToolTip(choice.tip)
        elif choice.text != choice.value:
            # 显示值被翻译过 → 悬停里留一份原名（核对脚本时要用）
            item.setToolTip(choice.value)
        self._list.addItem(item)

    def _refill(self, query: str):
        """
        ⚠ **组标题必须是"选不中"的**：用 `Qt.ItemIsSelectable` 关掉、
        文本里也不放真候选 —— 否则回车/双击会把 `服装` 当成值写进脚本。
        """
        self._list.clear()
        shown = 0
        if self._groups:
            for g in self._groups:
                vals = filter_pairs(g['values'], query)
                if not vals:
                    continue
                label = g['label'] or g['group'] or ''
                if label:
                    head = QListWidgetItem(f'── {label} ──')
                    head.setFlags(Qt.NoItemFlags)          # 不可选
                    # 粗体才像标题（灰色在浅色主题下几乎看不见 —— 出图才发现）
                    font = head.font()
                    font.setBold(True)
                    head.setFont(font)
                    self._list.addItem(head)
                for choice in vals:
                    self._add(choice)
                    shown += 1
        else:
            for choice in filter_pairs(self._all, query):
                self._add(choice)
                shown += 1
        for row in range(self._list.count()):              # 跳过标题
            if self._list.item(row).flags() & Qt.ItemIsSelectable:
                self._list.setCurrentRow(row)
                break
        self._hint.setText(f'{shown} / {len(self._all)} 个候选'
                           f' —— 双击选定（Esc 取消）')

    def _select(self, current: str):
        """把当前值滚到眼前（先按真值找，再按显示值找）。"""
        cur = str(current or '').strip()
        if not cur:
            return
        for row in range(self._list.count()):
            item = self._list.item(row)
            if not (item.flags() & Qt.ItemIsSelectable):
                continue
            if str(item.data(Qt.UserRole) or '') == cur:
                self._list.setCurrentRow(row)
                return
        for row in range(self._list.count()):
            if self._list.item(row).text() == cur:
                self._list.setCurrentRow(row)
                return

    @staticmethod
    def _value_of(item) -> str:
        """这一行的**真值**（`UserRole`；没有就退回屏幕上那句）。"""
        value = item.data(Qt.UserRole)
        return str(value) if isinstance(value, str) and value else item.text()

    def chosen(self) -> Choice:
        """当前选中项的 `(值, 显示)`；没选 → `Choice('')`。"""
        item = self._list.currentItem()
        if item is None or not (item.flags() & Qt.ItemIsSelectable):
            return Choice('')
        value = self._value_of(item)
        return Choice(value, item.text() if item.text() != value else '')

    def _accept_item(self, item):
        """
        选定**这一行**（双击给的实参优先）。

        ⚠ 不能用 `currentItem()` 顶替：双击的实参与"当前行"在真实 Qt 里
        总是一致，但判据里是**直接传进来**的 —— 用 currentItem 会选错项
        （`test_double_click_still_selects` 当场抓到）。
        """
        if item is not None and (item.flags() & Qt.ItemIsSelectable):
            self.value = self._value_of(item)
            self.accept()

    def _accept_current(self):
        self._accept_item(self._list.currentItem())

    def keyPressEvent(self, event):
        """回车 = 选定（列表有焦点时 QListWidget 自己也会处理双击）。"""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._accept_current()
            return
        super().keyPressEvent(event)


def choose_from(parent, title: str, choices, current: str = '',
                groups=None) -> str:
    """
    弹选择器；返回**选中的真值**（取消 → `''`）。给了 `groups` 就分组显示。

    ⚠ 返回的一定是 `value` 而不是屏幕上那串字：调用方拿它去写剧本
    （`i11` 而不是 `1人，近处`）。
    """
    dlg = ChoicePickerDialog(parent, title=title, choices=choices,
                             current=current, groups=groups)
    if dlg.exec() == QDialog.Accepted:
        return dlg.value
    return ''
