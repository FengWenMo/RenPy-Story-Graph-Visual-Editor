"""
`storygraph.ui_qt.column_editor` —— **候选分栏**编辑器（一栏一张卡）。
====================================================================

用户 2026-10-12/13（两轮都提到）：

> 还有你这个词组栏不也还是打勾下拉吗？全都再改！
> ……如果你是给候选值分栏+改名用的！你这不应该叫词组！你换个名字！
> 不要让我觉得完全不知所云！

所以旧名字 **词组**（`attr_groups`）在界面上**彻底不用了**，改叫 **候选分栏**：

| 内部键 | 界面上的说法 | 例子 |
|---|---|---|
| `name` | **分栏标识** | `outfit`（写进清单的键，程序用） |
| `label` | **这一栏显示成** | `服装`（编剧在下拉里看到的分栏标题） |
| `options` | **这一栏有哪些词** | `泳装 校服`（留空 = 兜底栏） |

⚠ 立绘属性（`show ye base_handdown happy`）的分栏**来自引擎**的
`layeredimage` group，那种情况**不需要在这里建栏** —— 这里建的栏只对
"自定义动作的参数候选"有意义。界面上一句话说清这件事。

⚠ 它**不是** `QScrollArea`：滚动只由弹窗那一页负责（用户报的
"这两个滚动条是何意味"就是同一屏摆了 2~3 个滚动区）。
"""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt.param_editor import ValueChips


class ColumnCard(QFrame):
    """**一栏一张卡**：分栏标识 / 这一栏显示成 / 这一栏有哪些词。"""

    def __init__(self, on_remove=None, parent=None):
        super().__init__(parent)
        self.setProperty('role', 'card')
        self.setFrameShape(QFrame.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(T.SPACE['md'], T.SPACE['sm'],
                                 T.SPACE['md'], T.SPACE['sm'])
        outer.setSpacing(T.SPACE['xs'])

        head = QHBoxLayout()
        head.setSpacing(T.SPACE['xs'])
        self._title = QLabel('一栏')
        self._title.setProperty('role', 'cardTitle')
        self._title.setWordWrap(True)
        head.addWidget(self._title, 1)
        self._rm = QPushButton('删掉这一栏')
        self._rm.setProperty('role', 'ghost')
        self._rm.setToolTip('这一栏不要了（本来在用它的格子会自动改回平铺）')
        self._rm.clicked.connect(lambda: on_remove(self) if on_remove else None)
        head.addWidget(self._rm)
        outer.addLayout(head)

        self._key = QLineEdit()
        self._key.setPlaceholderText('键名（英文，例：outfit）')
        self._key.setToolTip('写进项目清单的那个键，程序用。字母/数字/下划线。')
        #: 项目里真实存在的引擎组名（由弹窗注入）——给人一个**能用**的键，
        #: 而不是让编剧盲填「服装」后静默失败（2026-10-18）。
        self._known_keys: list = []
        self._pick_key = QPushButton('项目里的组…')
        self._pick_key.setProperty('role', 'ghost')
        self._pick_key.setToolTip('从项目 `layeredimage` / 引擎事实里读到的组名中选一个')
        self._pick_key.clicked.connect(self._show_known_keys)
        self._pick_key.setVisible(False)
        self._shown = QLineEdit()
        self._shown.setPlaceholderText('显示名（下拉里看到的，例：服装）')
        self._shown.setToolTip('编剧在下拉里看到的分栏标题。')
        # ⚠ 「一栏有哪些词」本来就是**一批**（多个值），所以直接开多选口径：
        #   空格/换行算分隔符、露出「📋 批量粘贴一大段…」（用户 2026-10-16：
        #   "当能够多选的时候就必须要有批量导入候选"）。
        self._values = ValueChips(multi=True)
        self._values.setToolTip('留空 = 这一栏是兜底栏：没归到别的栏的词都进它。\n'
                                '可以一次粘一大段（一行一个 / 空格分隔都行）。')

        self._field('键名（程序用）', self._key)
        self._key_row = QHBoxLayout()
        self._key_row.setContentsMargins(0, 0, 0, 0)
        self._key_row.addWidget(self._pick_key)
        self.layout().addLayout(self._key_row)
        self._field('显示名（编剧看到的）', self._shown)
        self._field('这一栏的候选值（留空 = 兜底栏）', self._values)
        #: 引擎常把动作/姿势并进 `outfit` 组 —— 只改名拆不开；
        #: 勾上 = 按上面的词从引擎组里取走，单独成一格（只影响卡片/候选）。
        self._split = QCheckBox('从引擎组里拆出来（引擎把动作并进服装时用）')
        self._split.setToolTip(
            '只有引擎把两类词塞进同一个 group（例如 outfit 里既有泳装又有 '
            'stand/wave）时才需要勾。\n勾上后：上面这些词归这一栏，其余归引擎原组；'
            '不改引擎语义，只改卡片上的格子。')
        self._split.toggled.connect(self._notify)
        self.layout().addWidget(self._split)

        #: 任何一个字改了都要通知外面（字段卡上的「分栏：xxx」标注要跟着变）
        self._on_changed = None
        for w in (self._key, self._shown):
            w.textChanged.connect(self._notify)
        self._values._on_change = self._notify
        self._refresh_title()

    def _field(self, label: str, widget: QWidget):
        tag = QLabel(label)
        tag.setProperty('role', 'fieldLabel')
        tag.setWordWrap(True)
        self.layout().addWidget(tag)
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.layout().addWidget(widget)

    def _notify(self, *_a):
        self._refresh_title()
        if self._on_changed is not None:
            try:
                self._on_changed()
            except Exception as e:                  # noqa: BLE001 - 通知失败不该炸
                swallow('ui_qt.column_editor.ColumnCard.changed', e)

    def _refresh_title(self):
        key = self._key.text().strip()
        shown = self._shown.text().strip()
        if key and shown:
            self.set_title(f'{shown}（{key}）')
        elif key:
            self.set_title(key)
        elif shown:
            self.set_title(shown)
        else:
            self.set_title('一栏（还没起名字）')

    def set_title(self, text: str):
        self._title.setText(text)

    def set_known_keys(self, keys):
        """项目里真实存在的引擎组名 —— 有的话露出「项目里的组…」按钮。"""
        self._known_keys = [str(k).strip() for k in (keys or ())
                            if str(k).strip()]
        self._pick_key.setVisible(bool(self._known_keys))
        if self._known_keys:
            self._key.setToolTip(
                '写进清单的键，程序用。\n项目里现有的组名：'
                + '、'.join(self._known_keys[:12])
                + ('…' if len(self._known_keys) > 12 else '')
                + '\n⚠ 要让卡片把 `outfit` 显示成「服装」，这里的键必须是 `outfit`。')

    def known_keys(self) -> list:
        return list(self._known_keys)

    def apply_known_key(self, key):
        self._key.setText(str(key or ''))

    def _show_known_keys(self):
        if not self._known_keys:
            return
        menu = QMenu(self)
        for key in self._known_keys:
            act = menu.addAction(key)
            act.triggered.connect(
                lambda _=False, k=key: self.apply_known_key(k))
        menu.exec(self._pick_key.mapToGlobal(
            self._pick_key.rect().bottomLeft()))

    def data(self) -> dict:
        return {'key': self._key.text().strip(),
                'name': self._key.text().strip(),        # 旧形状别名
                'shown': self._shown.text().strip(),
                'label': self._shown.text().strip(),     # 旧形状别名
                'values': self._values.text(),
                'choices': self._values.text(),          # 旧形状别名
                'split': self._split.isChecked()}

    def set_data(self, key: str = '', shown: str = '', values: str = '',
                 split: bool = False):
        self._key.setText(str(key or ''))
        self._shown.setText(str(shown or ''))
        # 旧调用点传的是逗号串（`'泳装,校服'`）—— 统一成顿号更贴中文习惯，
        # `ValueChips` 两种分隔符都认。
        self._values.set_text(str(values or '').replace(',', '、'))
        self._split.setChecked(bool(split))
        self._refresh_title()


class ColumnList(QWidget):
    """
    分栏卡的**竖向列表** —— ⚠ 不是 `QScrollArea`（滚动归弹窗那一页）。

    对外接口与旧版 `GroupList` 同形（`add_column` / `rows` / `count` /
    `clear` / `remove_current` / `load_columns`），另加旧的 `add_group` 等别名。
    """

    def __init__(self, parent=None, on_change=None):
        super().__init__(parent)
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(T.SPACE['sm'])
        self._col.addStretch(1)
        self._cards: list = []
        self._on_change = on_change
        self._known_keys: list = []

    def set_known_keys(self, keys):
        self._known_keys = [str(k).strip() for k in (keys or ())
                            if str(k).strip()]
        for card in self._cards:
            card.set_known_keys(self._known_keys)

    def add_column(self, key: str = '', shown: str = '', values: str = '',
                   split: bool = False):
        card = ColumnCard(on_remove=self._remove, parent=self)
        card.set_known_keys(self._known_keys)
        #: ⚠ 顺序要紧：`set_data` 会触发 textChanged，回调必须**在那之前**接好
        card._on_changed = self._notify
        card.set_data(key, shown, values, split)
        self._col.insertWidget(self._col.count() - 1, card)
        self._cards.append(card)
        self._notify()
        return card

    def rows(self) -> list:
        return [c.data() for c in self._cards]

    def count(self) -> int:
        return len(self._cards)

    def clear(self):
        for card in list(self._cards):
            self._cards.remove(card)
            card.setParent(None)
            card.deleteLater()
        self._notify()

    def remove_current(self):
        if self._cards:
            self._remove(self._cards[-1])

    def load_columns(self, columns):
        """已有声明 → 填进来（打开时让用户看到现在是怎么分的）。"""
        for c in (columns or ()):
            if isinstance(c, dict):
                self.add_column(str(c.get('name') or ''), str(c.get('label') or ''),
                                '、'.join(str(x) for x in (c.get('options') or ())),
                                bool(c.get('split')))
            else:
                self.add_column(getattr(c, 'name', '') or '',
                                getattr(c, 'label', '') or '',
                                '、'.join(getattr(c, 'options', []) or []),
                                bool(getattr(c, 'split', False)))

    def _remove(self, card):
        if card in self._cards:
            self._cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        self._notify()

    def _notify(self, *_a):
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception as e:                  # noqa: BLE001 - 通知失败不该炸
                swallow('ui_qt.column_editor.ColumnList.changed', e)

    # ---- 旧名字的别名（判据与旧调用点还在用"group"这套字）----
    def add_group(self, name: str = '', label: str = '', choices: str = ''):
        return self.add_column(name, label, choices)

    def load_groups(self, groups):
        return self.load_columns(groups)


__all__ = ['ColumnCard', 'ColumnList']
