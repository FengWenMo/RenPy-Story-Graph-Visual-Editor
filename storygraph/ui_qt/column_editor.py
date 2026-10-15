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

from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

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
        self._key.setPlaceholderText('分栏标识（内部用，例：outfit）')
        self._key.setToolTip('写进清单的键，程序用。字母/数字/下划线。')
        self._shown = QLineEdit()
        self._shown.setPlaceholderText('这一栏显示成（编剧看到的，例：服装）')
        self._shown.setToolTip('编剧在下拉里看到的分栏标题。')
        self._values = ValueChips()
        self._values.setToolTip('留空 = 这一栏是兜底栏：没归到别的栏的词都进它。')

        self._field('分栏标识（内部用）', self._key)
        self._field('这一栏显示成', self._shown)
        self._field('这一栏有哪些词（留空 = 兜底栏）', self._values)

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

    def data(self) -> dict:
        return {'key': self._key.text().strip(),
                'name': self._key.text().strip(),        # 旧形状别名
                'shown': self._shown.text().strip(),
                'label': self._shown.text().strip(),     # 旧形状别名
                'values': self._values.text(),
                'choices': self._values.text()}          # 旧形状别名

    def set_data(self, key: str = '', shown: str = '', values: str = ''):
        self._key.setText(str(key or ''))
        self._shown.setText(str(shown or ''))
        # 旧调用点传的是逗号串（`'泳装,校服'`）—— 统一成顿号更贴中文习惯，
        # `ValueChips` 两种分隔符都认。
        self._values.set_text(str(values or '').replace(',', '、'))
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

    def add_column(self, key: str = '', shown: str = '', values: str = ''):
        card = ColumnCard(on_remove=self._remove, parent=self)
        #: ⚠ 顺序要紧：`set_data` 会触发 textChanged，回调必须**在那之前**接好
        card._on_changed = self._notify
        card.set_data(key, shown, values)
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
                                '、'.join(str(x) for x in (c.get('options') or ())))
            else:
                self.add_column(getattr(c, 'name', '') or '',
                                getattr(c, 'label', '') or '',
                                '、'.join(getattr(c, 'options', []) or []))

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
