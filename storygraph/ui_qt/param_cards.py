"""
参数编辑器（**宽卡片式**）—— 用户 2026-10-11 的实测反馈直接催生的重做：

> "表格太小了！多个参数还要下拉看……而且编剧仍然看不明白"

## 为什么不用表格（旧版是 `QTableWidget` 三列）

| 表格的毛病 | 卡片怎么解决 |
|---|---|
| **太窄**：三列挤在半屏，"参数名"那一列只能显示几个字 | 每张卡**占满整行宽**，标签在左、输入框吃掉剩余宽度 |
| **多个参数要横向下拉**：加一个参数只是多一行，列被压得更窄 | 每个参数**竖着排**，多一个参数就多一张卡，**永远不需要横向滚动** |
| **编剧看不明白**：表头写"参数名 / 中文名 / 候选值" | 卡里写人话："在剧本里怎么写" / "表格那一列显示什么" / "能填哪些（可留空）" |
| **候选值要打逗号**：写 `1,2,3` | **敲回车就变一个小方块**，点 ✕ 去掉；分隔符不用记 |

## 兼容

对外仍是 `add_param(name, label, choices)` / `rows()` / `clear()` / `count()`
（旧表格的接口），所以调用方与判据不用改。
"""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T

#: 候选值分隔符 —— 与后端 `learn_statement.parse_choices` **同一套**
#: （中英文逗号、顿号、分号、竖线、空白）。用户"一次粘一大段"时就靠它全收下。
_SEP_RE = re.compile(r'[,，、;；|\s]+')


class ChipEdit(QWidget):
    """
    候选值输入：**敲回车 / 逗号就变成一个可点的小方块**（不用记分隔符）。

    `text()` 给回逗号分隔的串（与旧接口一致）；`set_text()` 反向填。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(T.SPACE['xs'])
        self._chips_row = QHBoxLayout()
        self._chips_row.setSpacing(T.SPACE['xs'])
        self._chips_row.addStretch(1)
        outer.addLayout(self._chips_row)
        self._edit = QLineEdit()
        self._edit.setPlaceholderText('填一个就回车（例：1 ／ 2 ／ 3）；'
                                      '不填 = 编剧能随便写')
        self._edit.returnPressed.connect(self._commit)
        self._edit.textEdited.connect(self._maybe_split)
        # ⚠⚠ 2026-10-12 实测修复（用户报"按了回车没反应"）：`QLineEdit` 收到
        #   Return 时是 `event->ignore()`（Qt 为了让"对话框里回车 = 确定"生效），
        #   于是这个按键**冒到 QDialog**，把 Ok 按钮（autoDefault）点了 ——
        #   实测症状：候选值刚加进去、**整个弹窗立刻被 accept 关掉**，
        #   还会顺着 `exec()==Accepted` 走写盘。这里装一道过滤器把它吃掉。
        self._edit.installEventFilter(self)
        outer.addWidget(self._edit)
        self._chips = []

    def eventFilter(self, obj, event):              # noqa: N802 - Qt 接口
        """
        回车只用来"加一个候选值"，**绝不许它冒到弹窗去点默认按钮**。

        为什么不用 `returnPressed` 一手包办：它的连接照样在（下面 `_commit`），
        但事件本身会继续传播；只有在这里 `accept()` 才能既收下候选值、
        又拦住那次传播。（判据：`test_chip_edit_return_does_not_close_dialog`）
        """
        if (obj is self._edit and event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)):
            self._commit()
            event.accept()
            return True
        return super().eventFilter(obj, event)

    # ---- 内部 ----
    def _maybe_split(self, text: str):
        """打了逗号（中英文都认）就把前面那段变成方块 —— 迁就旧习惯。"""
        for sep in (',', '，'):
            if sep in text:
                head, _, tail = text.partition(sep)
                if head.strip():
                    self._add_chip(head.strip())
                self._edit.setText(tail)
                return

    def _commit(self):
        """
        回车 → **一次收下所有值**（不是"只收一个"）。

        ⚠ 2026-10-12 用户实测："我就只能填一个候选值，填不了多个"。
        分隔符跟后端同一套（`，,、;；|` 与空白），所以"粘一大段 / 打一串 /
        粘多行"都能一次变成方块 —— 这也是"项目里一堆表情"最省事的入口。
        """
        value = self._edit.text().strip()
        if not value:
            return
        for part in _SEP_RE.split(value):
            if part.strip():
                self._add_chip(part.strip())
        self._edit.clear()

    def _add_chip(self, value: str):
        if value in self._chips:
            return
        chip = QPushButton(f'{value}  ✕')
        chip.setProperty('role', 'chip')
        chip.setToolTip('点一下去掉这个候选值')
        chip.clicked.connect(lambda _=False, v=value: self._remove_chip(v))
        self._chips_row.insertWidget(self._chips_row.count() - 1, chip)
        self._chips.append(value)

    def _remove_chip(self, value: str):
        for i in range(self._chips_row.count()):
            w = self._chips_row.itemAt(i).widget()
            if isinstance(w, QPushButton) and w.text().startswith(value + '  '):
                w.setParent(None)
                w.deleteLater()
                break
        if value in self._chips:
            self._chips.remove(value)

    # ---- 对外 ----
    def text(self) -> str:
        return ','.join(self._chips)

    def set_text(self, text: str):
        for v in list(self._chips):
            self._remove_chip(v)
        for part in str(text or '').replace('，', ',').split(','):
            if part.strip():
                self._add_chip(part.strip())

    def add_values(self, values):
        """
        **一次倒进一批候选值**（「⚡ 导入候选」用）—— 自动去重，不覆盖已有的。

        为什么不复用 `set_text`：导入是"加上去"，不是"换掉我现在写的"。
        """
        for v in (values or ()):
            s = str(v).strip()
            if s:
                self._add_chip(s)


class _Card(QFrame):
    """一张卡片的外观（参数卡与词组卡共用）。"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setProperty('role', 'card')
        self.setFrameShape(QFrame.NoFrame)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(T.SPACE['md'], T.SPACE['sm'],
                                       T.SPACE['md'], T.SPACE['sm'])
        self._outer.setSpacing(T.SPACE['xs'])
        head = QHBoxLayout()
        self._title = QLabel(title)
        self._title.setProperty('role', 'cardTitle')
        head.addWidget(self._title)
        head.addStretch(1)
        self._outer.addLayout(head)
        self.body = QGridLayout()
        self.body.setHorizontalSpacing(T.SPACE['sm'])
        self.body.setVerticalSpacing(T.SPACE['xs'])
        self._outer.addLayout(self.body)

    def set_title(self, text: str):
        self._title.setText(text)

    def field(self, row: int, label: str, widget: QWidget, hint: str = ''):
        """一行：**左边人话标签（固定宽） + 右边控件吃掉全部宽度**。"""
        tag = QLabel(label)
        tag.setProperty('role', 'fieldLabel')
        tag.setFixedWidth(T.LABEL_W + 60)        # 比默认标签列更宽，容得下人话
        tag.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.body.addWidget(tag, row, 0)
        self.body.addWidget(widget, row, 1)
        self.body.setColumnStretch(1, 1)
        if hint:
            note = QLabel(hint)
            note.setProperty('role', 'caption')
            note.setWordWrap(True)
            self.body.addWidget(note, row + 1, 1)
        return widget


class ParamCard(_Card):
    """
    **一个参数一张卡** —— 竖着排，永远不需要横向滚动。

    ⚠ 高度是**紧**的（一屏要能看见好几张）：只有两行 ——
    ① 剧本里怎么写 ＋ 表格那一列显示什么（同一行并排）
    ② 只能填这些（候选值方块，**没有候选时就整行藏起来**）
    说明文字一律走 **tooltip**，不占纵向空间（用户："多个参数还要下拉看"）。
    """

    def __init__(self, on_change=None, on_remove=None, on_move=None,
                 sources_provider=None, parent=None):
        super().__init__('这个动作要填的东西', parent)
        self._on_change = on_change
        self._on_remove = on_remove
        self._on_move = on_move
        self._sources_provider = sources_provider
        head = self._outer.itemAt(0)
        # ⭐ 2026-10-12（用户："我的真实项目内有无数个表情！不可能一个个输入"）：
        #   候选值能从项目里**白拿**（引擎分好的立绘属性 / 角色表 / 真正用过的值），
        #   入口放卡头 —— 手填只是"改改它拿来的"。
        self._import = QPushButton('⚡ 导入候选')
        self._import.setProperty('role', 'ghost')
        self._import.setToolTip(
            '候选值不用手打：从项目里一次倒进来\n'
            '· 立绘属性（引擎自己分好的组，如 expressions）\n'
            '· 角色表\n'
            '· 这个动作在脚本里真正用过的值')
        self._import.clicked.connect(self._on_import)
        self._import.setVisible(False)
        head.addWidget(self._import)
        self._rm = QPushButton('删掉这一项')
        self._rm.setProperty('role', 'ghost')
        self._rm.setToolTip('这个动作不需要填它')
        self._rm.clicked.connect(lambda: on_remove(self) if on_remove else None)
        head.addWidget(self._rm)

        self._name = QLineEdit()
        self._name.setPlaceholderText('编剧写这个动作时跟的那串字，例：强度')
        self._name.setToolTip('必填。就是紧跟在动作名后面的那串字。')
        self._label = QLineEdit()
        self._label.setPlaceholderText('这一列给编剧看的中文名（可留空）')
        self._label.setToolTip('可留空。留空就用左边那串字当列名。')
        # ⭐ 2026-10-11（程序员撞墙记录）：**这一格是什么类型，由人选** ——
        #   用户原话："工具怎么知道我填的是'变量'还是'固定文本'？"
        #   答案就是这里：`固定关键字 / 任意变量 / 从这些里选 / 可选的固定段`。
        self._kind = QComboBox()
        for key, text in (('keyword', '固定关键字'), ('variable', '任意变量'),
                          ('choice', '从这些里选'),
                          ('optional', '可选的固定段')):
            self._kind.addItem(text, key)
        self._kind.setToolTip(
            '固定关键字 = 剧本里必须原样出现这几个字（如 show / at）；\n'
            '任意变量 = 谁都可以（如人物名）；\n'
            '从这些里选 = 只能填你列的候选值；\n'
            '可选的固定段 = 可出现可不出现的一段（如 at 位置）。')
        self._kind.currentIndexChanged.connect(self._on_kind_changed)
        self._kind.currentIndexChanged.connect(self._changed)
        # ⭐ 词组**挂在格子上**（用户撞墙记录 卡点 5：
        #   "「词组（可选·高级）」是什么鬼……如果我要用 swim_suit 来匹配 泳装，
        #    我该写在哪？"）
        #   答案：这一格选「从这些里选」时，就在这儿挑一个词组当候选来源 ——
        #   词组里存着"中文词 ↔ 内部写法"（泳装 → swim_suit）。
        self._group = QComboBox()
        self._group.setToolTip('候选值从哪个词组来（词组里能写"中文词 → 内部写法"）')
        self._group.currentIndexChanged.connect(self._changed)
        self._group.setVisible(False)
        # ⭐ 顺序是可以调的（用户卡点：竖着排的三张卡凭什么对应那个顺序）
        self._up = QPushButton('↑')
        self._down = QPushButton('↓')
        for btn, tip in ((self._up, '跟上一格换位置'),
                         (self._down, '跟下一格换位置')):
            btn.setProperty('role', 'ghost')
            btn.setToolTip(tip)
            btn.setFixedWidth(28)
        self._up.clicked.connect(lambda: on_move(self, -1) if on_move else None)
        self._down.clicked.connect(lambda: on_move(self, +1) if on_move else None)
        self.field(0, '这一格是', self._kind)
        self.field(1, '剧本里怎么写', self._name)
        tag = QLabel('表格那一列显示什么')
        tag.setProperty('role', 'fieldLabel')
        tag.setToolTip('可留空。给编剧看的中文列名（例如「强度」）。')
        self.body.addWidget(tag, 1, 2)
        self.body.addWidget(self._label, 1, 3)
        self.body.addWidget(self._up, 1, 4)
        self.body.addWidget(self._down, 1, 5)
        self.body.setColumnStretch(1, 3)
        self.body.setColumnStretch(3, 2)

        # 候选值：**默认藏起来**（多数动作是"随便填"），勾了才展开
        self._choices = ChipEdit()
        self._limit = QCheckBox('只能填这些（限定候选）')
        self._limit.setToolTip('不勾 = 编剧随便写；勾了 = 只能从你列的里面挑。')
        self._limit.toggled.connect(self._on_limit_toggled)
        # ⚠⚠ 2026-10-12 实测修复（用户报"按了没反应"）：这两行原来都把控件
        #   放进 `(1, 1)` —— 而那格已经是「剧本里怎么写」的输入框，
        #   **两个控件叠在同一格里**（offscreen 渲染确认：输入框里套着一个
        #   复选框，点击命中上层那个，输入框根本填不进去）。
        #   重排为：`_group` 跟「这一格是」同排（它只在"从这些里选"时出现，
        #   本来就是"这一格是什么"的一部分）；`_limit` 跟候选值同排
        #   （它管的就是候选值显不显示）。行数不变，语义更贴。
        self.body.addWidget(self._group, 0, 2, 1, 2)
        self.body.addWidget(self._limit, 2, 0, 1, 2)
        self.body.addWidget(self._choices, 2, 2, 1, 2)
        self._choices.setVisible(False)

        for w in (self._name, self._label):
            w.textChanged.connect(self._changed)
        self._refresh_title()
        self._on_kind_changed()      # 导入按钮的显隐要跟着"这一格是"的初值

    def _on_kind_changed(self, *_a):
        is_choice = self._kind.currentData() == 'choice'
        self._group.setVisible(is_choice)
        # 「⚡ 导入候选」只对枚举格有意义（别的格没有候选值这回事）
        btn = getattr(self, '_import', None)
        if btn is not None:
            btn.setVisible(is_choice)

    def _on_limit_toggled(self, on: bool):
        self._choices.setVisible(bool(on))
        if on:
            self._choices.setFocus()
        self._changed()

    def _on_import(self):
        """
        「⚡ 导入候选」：把项目里**能拿到的候选值**摆出来，挑一个倒进这一格。

        ⚠ 工具**不猜**"这一格是不是表情"（那是语义，猜错改剧情）——
        它只列"项目里到底有哪些值、从哪来"，点哪个由人决定。
        来源清单（纯函数）在 `authoring.learn_statement.choice_sources`。
        """
        menu = QMenu(self)
        rows = []
        try:
            if self._sources_provider is not None:
                rows = list(self._sources_provider() or [])
        except Exception as e:                       # noqa: BLE001 - 导入不该炸卡片
            swallow('ui_qt.param_cards._on_import', e)
            rows = []
        if not rows:
            act = menu.addAction('（项目里还没读到可导入的值 —— 先「打开项目」；'
                                 '"立绘属性"要先跑过一次引擎体检）')
            act.setEnabled(False)
        else:
            for r in rows:
                act = menu.addAction(f'{r.get("label")}'
                                     f'（{r.get("count", 0)} 个）')
                act.setData(list(r.get('values') or []))
        chosen = menu.exec(self._import.mapToGlobal(
            self._import.rect().bottomLeft()))
        if chosen is None or not chosen.data():
            return
        self._choices.add_values(chosen.data())
        # 导进来就是"只能填这些"——不勾上的话候选白导（只有勾了才进表格下拉）
        self._limit.setChecked(True)
        self._choices.setVisible(True)
        self._changed()

    def _changed(self):
        self._refresh_title()
        if self._on_change:
            self._on_change()

    def _refresh_title(self):
        text = self._name.text().strip()
        label = self._label.text().strip()
        if text and label:
            self.set_title(f'参数：{text}（表格里叫「{label}」）')
        elif text:
            self.set_title(f'参数：{text}')
        else:
            self.set_title('这个动作要填的东西')

    # ---- 数据 ----
    def data(self) -> dict:
        return {'kind': self._kind.currentData() or 'variable',
                'group': self._group.currentData() or '',
                'text': self._name.text().strip(),
                'name': self._name.text().strip(),
                'label': self._label.text().strip(),
                'column': self._label.text().strip(),
                'choices': self._choices.text() if self._limit.isChecked() else ''}

    def set_data(self, name: str = '', label: str = '', choices: str = '',
                 kind: str = '', group: str = ''):
        if kind:
            idx = self._kind.findData(kind)
            if idx >= 0:
                self._kind.setCurrentIndex(idx)
        if group:
            j = self._group.findData(group)
            if j >= 0:
                self._group.setCurrentIndex(j)
        self._on_kind_changed()
        self._name.setText(str(name or ''))
        self._label.setText(str(label or ''))
        text = str(choices or '')
        self._choices.set_text(text)
        # 已有声明里**本来就有候选** → 自动展开（不然人会以为丢了）
        self._limit.setChecked(bool(text.strip()))
        self._choices.setVisible(bool(text.strip()))

    def focus(self):
        self._name.setFocus()


class ParamList(QScrollArea):
    """
    参数卡片的**竖向列表**（自带滚动）—— 对外接口与旧 `_ParamsTable` 一致。

    ⚠ 老接口保留（`add_param` / `rows` / `clear` / `count`），
    所以调用方、判据、以及"填进已有声明"那条路都不用改。
    """

    def __init__(self, parent=None, on_change=None, sources_provider=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # 明确：不横向滚
        self._host = QWidget()
        self._col = QVBoxLayout(self._host)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(T.SPACE['sm'])
        self._col.addStretch(1)
        self.setWidget(self._host)
        self._cards = []
        self._groups = []                  # 最近一次同步过来的词组名
        self._on_change = on_change
        self._sources_provider = sources_provider    # 「⚡ 导入候选」的来源

    # ---- 对外（与旧表格同形）----
    def add_param(self, name: str = '', label: str = '', choices: str = '',
                  kind: str = 'variable', group: str = ''):
        card = ParamCard(on_change=self._on_change, on_remove=self._remove,
                         on_move=self.move,
                         sources_provider=self._sources_provider,
                         parent=self._host)
        card.set_data(name, label, choices, kind, group)
        # ⚠⚠ 2026-10-12 修的 bug（用户："这个框是下拉框！但是怎么点击都没反应"）：
        #   这一段原来被 `if self._groups:` 包着 —— 项目里**一个词组都没声明**时
        #   `_groups` 是空列表，于是每张新卡的下拉**一项都没有**（点开是空白，
        #   看起来就是坏的）。这里无条件补兜底项，「不挂词组」永远是第一项。
        card._group.blockSignals(True)
        card._group.clear()
        card._group.addItem('（不挂词组）', '')
        for gname in self._groups:
            if card._group.findData(gname) < 0:
                card._group.addItem(gname, gname)
        card._group.blockSignals(False)
        self._col.insertWidget(self._col.count() - 1, card)
        self._cards.append(card)
        card.focus()
        if self._on_change:
            self._on_change()
        return card

    def rows(self) -> list:
        return [c.data() for c in self._cards]

    def count(self) -> int:
        return len(self._cards)

    def clear(self):
        for card in list(self._cards):
            self._remove(card)

    def remove_current(self):
        """删掉最后一张（旧的"删这一行"语义：表里选中的那行）。"""
        if self._cards:
            self._remove(self._cards[-1])

    def move(self, card, delta: int):
        """
        把这一格**跟邻居换位置**（用户卡点："这 3 张卡凭什么对应那个顺序"）。

        ⚠ 顺序是本设计里**唯一**决定"代码里长什么样"的东西 ——
        所以必须能改，而且改完立刻能在「试切」里看出来。
        """
        if card not in self._cards:
            return
        i = self._cards.index(card)
        j = i + int(delta)
        if not (0 <= j < len(self._cards)):
            return
        self._cards[i], self._cards[j] = self._cards[j], self._cards[i]
        self._col.removeWidget(card)
        self._col.insertWidget(j, card)
        if self._on_change:
            self._on_change()

    def set_groups(self, groups):
        """
        把当前词组名喂给每一格的下拉（**词组表变了要跟着变**）。

        ⚠ 这一格已经在用的词组**不能丢**（哪怕它暂时不在表里）——
        保住当前值，免得人一切换就白填。
        """
        names = [str(g) for g in (groups or ()) if str(g).strip()]
        self._groups = list(names)          # 记住，好给**后来新建的卡**用
        for card in self._cards:
            combo = getattr(card, '_group', None)
            if combo is None:
                continue
            keep = combo.currentData() or ''
            combo.blockSignals(True)
            combo.clear()
            combo.addItem('（不挂词组）', '')
            for name in names:
                combo.addItem(name, name)
            idx = combo.findData(keep)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
            if keep and idx < 0:                # 表里暂时没有 → 也保住它
                combo.addItem(keep, keep)
                combo.setCurrentIndex(combo.findData(keep))

    def slots(self) -> list:
        """把卡片转成**位置序列**（喂给 `authoring.pattern` 做试切/落进声明）。"""
        out = []
        for card in self._cards:
            row = card.data()
            out.append({'kind': row.get('kind') or 'variable',
                        'group': row.get('group') or '',
                        'text': row.get('name') or '',
                        'column': row.get('label') or '',
                        'choices': row.get('choices') or ''})
        return out

    def _remove(self, card):
        if card in self._cards:
            self._cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        if self._on_change:
            self._on_change()


class GroupCard(_Card):
    """
    **一个词组一张卡**（服装 / 表情 …）—— 同样是竖排宽卡，两行 + tooltip 说明。

    ⚠ 卡片里**不写长段说明**（那是把纵向空间吃光的主因）—— 说明走 tooltip，
    界面上一句话的外框说明放在这一节标题下面。
    """

    def __init__(self, on_remove=None, parent=None):
        super().__init__('一组词', parent)
        head = self._outer.itemAt(0)
        self._rm = QPushButton('删掉这一组')
        self._rm.setProperty('role', 'ghost')
        self._rm.clicked.connect(lambda: on_remove(self) if on_remove else None)
        head.addWidget(self._rm)
        self._name = QLineEdit()
        self._name.setPlaceholderText('组名（内部用，例：outfit）')
        self._label = QLineEdit()
        self._label.setPlaceholderText('中文组名（编剧看到的，例：服装）')
        self._choices = ChipEdit()
        self._choices.setToolTip('留空 = 这一组是兜底组：没归到别的组的词都进它。')
        self.field(0, '组名', self._name)
        tag = QLabel('编剧看到的中文名')
        tag.setProperty('role', 'fieldLabel')
        self.body.addWidget(tag, 0, 2)
        self.body.addWidget(self._label, 0, 3)
        self.body.setColumnStretch(1, 1)
        self.body.setColumnStretch(3, 1)
        self.body.addWidget(QLabel('这一组有哪些词'), 1, 0)
        self.body.addWidget(self._choices, 2, 0, 1, 4)

    def data(self) -> dict:
        return {'name': self._name.text().strip(),
                'label': self._label.text().strip(),
                'choices': self._choices.text()}

    def set_data(self, name='', label='', choices=''):
        self._name.setText(str(name or ''))
        self._label.setText(str(label or ''))
        self._choices.set_text(str(choices or ''))


class GroupList(QScrollArea):
    """
    词组卡片的竖向列表 —— 接口对齐旧 `_GroupsTable`
    （`add_group` / `rows` / `load_groups` / `clear`）。
    """

    def __init__(self, parent=None, on_remove_group=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._host = QWidget()
        self._col = QVBoxLayout(self._host)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(T.SPACE['sm'])
        self._col.addStretch(1)
        self.setWidget(self._host)
        self._cards = []
        self._on_remove_group = on_remove_group

    def add_group(self, name: str = '', label: str = '', choices: str = ''):
        card = GroupCard(on_remove=self._remove, parent=self._host)
        card.set_data(name, label, choices)
        self._col.insertWidget(self._col.count() - 1, card)
        self._cards.append(card)
        return card

    def rows(self) -> list:
        return [c.data() for c in self._cards]

    def count(self) -> int:
        return len(self._cards)

    def clear(self):
        for card in list(self._cards):
            self._remove(card)

    def load_groups(self, groups):
        """已有声明 → 填进来（打开时让用户看到现在是怎么分的）。"""
        for g in (groups or ()):
            self.add_group(getattr(g, 'name', '') or '',
                           getattr(g, 'label', '') or '',
                           '、'.join(getattr(g, 'options', []) or []))

    def remove_current(self):
        if self._cards:
            self._remove(self._cards[-1])

    def _remove(self, card):
        if card in self._cards:
            self._cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        if self._on_remove_group:
            self._on_remove_group()


__all__ = ['ChipEdit', 'ParamCard', 'ParamList', 'GroupCard', 'GroupList']
