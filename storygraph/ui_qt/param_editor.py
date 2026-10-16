"""
`storygraph.ui_qt.param_editor` —— 「一个动作要填什么」的**字段编辑器**。
=====================================================================

这是 2026-10-13 重写的版本，**替代**旧的 `ui_qt/param_cards.py`。
旧文件被整个删掉（用户原话）：

> 我之前不是说了让你把这个子界面删了重写吗？？？不要再在原来的地方缝补了！
> 整个删了重写！

## 它解决的两个界面问题（都是用户实测报的）

1. **"选的一多就把后面那个变量挤出窗口"**
   → 旧版卡内用 6 列网格（一张空卡的最小宽 **480 px**）+ 候选方块横排不折行，
   而横向滚动是关掉的，于是内容只能被裁。现在：卡内**单列堆叠**，
   方块走 `FlowRow`（**折行**），空卡最小宽 **188 px**、方块再多也不涨。

2. **"这两个滚动条是何意味啊"**
   → 旧版 `FieldList` 自己是 `QScrollArea`，外面弹窗又套了一个滚动区 = **两条**。
   现在 `FieldList` 是**纯 QWidget**，滚动只由外层那一个滚动区负责。

## 命名（用户："你这不应该叫词组！你换个名字！"）

旧词 **词组**（`attr_groups`）废弃，界面上叫 **「候选分栏」**：

| 概念 | 界面上的说法 | 用途 |
|---|---|---|
| 一组候选值 | **一栏** | 把下拉里的候选分成「服装」「神态」这样的栏 |
| 组的内部名（`outfit`） | **分栏标识** | 写进清单的键，程序用 |
| 组的中文名（`服装`） | **这一栏显示成** | 编剧在下拉里看到的分栏标题 |

⚠ 立绘属性（`show ye base_handdown happy`）的分栏**来自引擎**
（`layeredimage` 的 `group`），那种情况**不需要在这里建栏** ——
这里建栏只对"自定义动作的参数候选"有意义。
"""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPlainTextEdit,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt.flow import FlowRow

#: 候选值分隔符 —— 与后端 `learn_statement.parse_choices` **同一套**
#: （中英文逗号、顿号、分号、竖线）。
#: ⚠ 它**不含空白**：中文候选值里带空格是正常的（"洛丽塔 长裙"），
#:   在"一格只能填一个"的场景里按空格切就会把一个值切成两个。
_SEP_RE = re.compile(r'[,，、;；|]+')

#: 分隔符的**超集** —— 只给"这一格可以有多个"用（2026-10-16 用户提的
#: "能多选就必须能批量导入候选"）。多选格里的值本来就是"一个个词"，
#: 空格/换行/制表符都是**排版**而不是值的一部分，所以这时它们才算分隔符。
#: 于是"一行一个 / 空格分隔 / 逗号分隔 / 直接粘一大段"全都收得下。
_BATCH_SEP_RE = re.compile(r'[,，、;；|\s]+')


def split_candidate_values(text: str, *, multi: bool = False) -> list:
    """
    候选值输入框里那段文字 → 候选值列表（去空、保序去重）。

    两种口径（**由"这一格可以有多个"决定**，用户 2026-10-16）：

      · `multi=False`（一格一个值）：只认**真分隔符**
        （`,` `，` `、` `;` `；` `|`）与换行 —— 空格**不算**，
        所以"洛丽塔 长裙"是**一个**候选值。
      · `multi=True`（一格可以多个值）：空格/换行/制表符**都算**分隔符 ——
        "粘一大段"直接全收下（用户原话："能多选的时候就必须要有批量导入候选"）。

    ⚠ 这是"界面收词"的口径，不是"清单怎么存"的口径：
    存下去仍是后端 `parse_choices` 那一套（逗号串）。
    """
    raw = str(text or '')
    if not multi:
        # 换行在单值场景里也只可能是"想分多条"，照样当分隔符
        raw = raw.replace('\n', ',').replace('\r', ',')
        pattern = _SEP_RE
    else:
        pattern = _BATCH_SEP_RE
    out = []
    for part in pattern.split(raw):
        s = part.strip()
        if s and s not in out:
            out.append(s)
    return out


class ValueChips(QWidget):
    """
    候选值输入：**敲回车 / 逗号就变成一个可点的小方块**（不用记分隔符）。

    用户原话（2026-10-11）："候选值要打逗号"太麻烦；
    （2026-10-12）："我就只能填一个候选值，填不了多个" → 回车**一次收下所有**；
    （2026-10-16）："这个你没有给我加批量导入……当能够多选的时候就必须要有
    批量导入候选" → 参数 `multi` 决定收词口径，并露出「📋 批量粘贴一大段…」。

    `text()` 给回逗号分隔的串（与后端契约一致）；`set_text()` 反向填。
    """

    def __init__(self, parent=None, on_change=None, *, multi: bool = False):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(T.SPACE['xs'])
        #: "这一格可以有多个值" —— 由卡片在勾选框变化时同步（`set_multi`）。
        #: 它决定"空格算不算分隔符"（见 `split_candidate_values`）。
        self._multi = bool(multi)
        # ⚠ **必须折行**：横排布局的最小宽 = 所有方块之和，候选一多就把卡片
        #   （进而整窗）顶宽，而这里不提供横向滚动 → 后面那列字被挤出窗口。
        self._row = FlowRow(spacing=T.SPACE['xs'])
        outer.addWidget(self._row)
        self._edit = QLineEdit()
        self._edit.setPlaceholderText(self._placeholder_text())
        self._edit.returnPressed.connect(self._commit)
        self._edit.textEdited.connect(self._maybe_split)
        #: 批量导入入口（**勾了"可以有多个"才露出来**，见 `set_multi`）：
        #: 一行一个 / 空格分隔 / 直接粘一大段都能收。
        self._batch_btn = QPushButton('📋 批量粘贴一大段…')
        self._batch_btn.setProperty('role', 'ghost')
        self._batch_btn.setToolTip(
            '一次粘一批候选值：一行一个、或空格/逗号分隔都行。\n'
            '不勾"可以有多个"时只有一个值，所以这个入口藏起来。')
        self._batch_btn.clicked.connect(self.open_batch_import)
        outer.addWidget(self._batch_btn)
        self._batch_btn.setVisible(self._multi)
        self._hint = QLabel('✅ 批量模式：一行一个 / 空格分隔 / 直接粘一大段，'
                            '回车一次全收下')
        self._hint.setProperty('role', 'caption')
        self._hint.setWordWrap(True)
        outer.addWidget(self._hint)
        self._hint.setVisible(self._multi)
        #: 批量导入对话框的"从项目里拿"来源（由卡片注入同一份 provider）
        self._sources_provider = None
        # ⚠⚠ 实测修复（用户报"按了回车没反应"）：`QLineEdit` 收到 Return 是
        #   `event->ignore()`（Qt 为了让"对话框里回车 = 确定"生效），按键于是冒到
        #   `QDialog`，把 Ok（autoDefault）点了 → **候选值刚加上、弹窗立刻被关掉**，
        #   还会顺着 `exec()==Accepted` 走写盘。这里装一道过滤器把它吃掉。
        self._edit.installEventFilter(self)
        outer.addWidget(self._edit)
        self._values: list = []
        self._on_change = on_change

    # ---- 事件 ----
    def eventFilter(self, obj, event):              # noqa: N802 - Qt 接口
        """回车只用来"加候选值"，**绝不许它冒到弹窗去点默认按钮**。"""
        if (obj is self._edit and event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)):
            self._commit()
            event.accept()
            return True
        return super().eventFilter(obj, event)

    def _maybe_split(self, text: str):
        """打了逗号（中英文都认）就把前面那段变成方块 —— 迁就旧习惯。

        ⚠ 只认逗号：空格**不能**即时分词，否则"洛丽塔 长裙"这种带空格的
        单个候选值还没打完就被切走了。
        """
        for sep in (',', '，'):
            if sep in text:
                head, _, tail = text.partition(sep)
                if head.strip():
                    self.add_values([head.strip()])
                self._edit.setText(tail)
                return

    def _placeholder_text(self) -> str:
        if self._multi:
            return ('一行一个、空格分隔都行（例：happy sad 或 泳装 校服）；'
                    '回车一次全收下')
        return ('填一个就回车（例：泳装 ／ 校服）；值里可以有空格；'
                '不填 = 编剧能随便写')

    def set_multi(self, on: bool):
        """
        同步"这一格可以有多个"（`FieldCard._multi` 勾选框）。

        为什么要有这个方法（用户 2026-10-16）：
        > 当能够多选的时候就必须要有批量导入候选

        勾上之后：① 空格/换行**才算**分隔符（一行一个 / 粘一大段）；
        ② 露出「📋 批量粘贴一大段…」入口与那行说明。
        """
        on = bool(on)
        if on == self._multi:
            return
        self._multi = on
        self._edit.setPlaceholderText(self._placeholder_text())
        self._batch_btn.setVisible(on)
        self._hint.setVisible(on)

    def is_multi(self) -> bool:
        return self._multi

    def _commit(self):
        """回车 → **一次收下所有值**（不是"只收一个"）。"""
        value = self._edit.text().strip()
        if not value:
            return
        self.add_values(split_candidate_values(value, multi=self._multi))
        self._edit.clear()

    # ---- 对外 ----
    def set_height_source(self):
        """
        让"我的高度"跟着**内部折行布局**走。

        为什么需要（2026-10-13 实测）：`QWidget` 当容器时 Qt 认不出里面的布局，
        `sizeHint()` 是 0×0 —— 方块加进去了却**一个都看不见**。
        这里把高度交给 `FlowRow.heightForWidth()`（它知道折了几行）。
        """
        row = self._row

        def _hfw(width: int) -> int:
            margins = self.contentsMargins()
            inner = max(40, int(width) - margins.left() - margins.right())
            return row.heightForWidth(inner) + self._edit.sizeHint().height() \
                + T.SPACE['xs'] + margins.top() + margins.bottom()

        self.heightForWidth = _hfw            # type: ignore[assignment]
        self.hasHeightForWidth = lambda: True  # type: ignore[assignment]

    def sizeHint(self) -> QSize:                 # noqa: N802 - Qt 接口
        hint = super().sizeHint()
        return QSize(max(hint.width(), 240), max(hint.height(), 56))

    def text(self) -> str:
        return ','.join(self._values)

    @property
    def _chips(self) -> list:
        """旧名（判据里还在用 `chip._chips`）。"""
        return self._values

    def values(self) -> list:
        return list(self._values)

    def set_text(self, text: str):
        """
        反向填（存量声明 / 旧调用点传进来的分隔串）。

        ⚠ 2026-10-16：切分改走 `split_candidate_values` —— 从前这里只认逗号，
        而 `ColumnCard.set_data` 传的是**顿号串**（`'泳装、校服'`）→
        整串变成一个方块（"读出来的和写下去的不一样"）。现在
        逗号/顿号/分号/竖线/换行都认，且**多选格才算空格**。
        """
        self._values = split_candidate_values(text, multi=self._multi)
        self._rebuild()

    def add_values(self, values):
        """
        **一次倒进一批**（「⚡ 从项目里拿」用）—— 去重、**追加**不覆盖。

        为什么不复用 `set_text`：导入是"加上去"，不是"换掉我现在写的"。
        """
        added = False
        for v in (values or ()):
            s = str(v).strip()
            if s and s not in self._values:
                self._values.append(s)
                added = True
        if added:
            self._rebuild()
        return added

    # ---- 批量导入（勾了「可以有多个」才露出来）----
    def project_sources(self) -> list:
        """给批量对话框用的"从项目里拿"清单（与卡片那枚 ⚡ 同一份来源）。"""
        try:
            if self._sources_provider is not None:
                return list(self._sources_provider() or [])
        except Exception as e:                       # noqa: BLE001 - 导入不该炸卡片
            swallow('ui_qt.param_editor.ValueChips.sources', e)
        return []

    def open_batch_import(self) -> list:
        """
        批量导入候选值：**左边粘一大段、右边从项目里勾**。

        用户 2026-10-16：
        > 这个你没有给我加批量导入……当能够多选的时候就必须要有批量导入候选

        为什么"多选"才要（而不是所有格都摆出来）：一格只能填一个值的时候，
        批量入口没有意义（收下 10 个也只会用一个）；而**多选格**恰恰是
        "候选越多越好"的地方（真机 `expressions` 组一次就能倒出 68 个）。

        返回**这次导入收下的值**（去重后真正加进去的那批；取消则空列表）。
        """
        values = self._batch_import_dialog()
        if not values:
            return []
        added = self.add_values(values)
        return list(values) if added else []

    def _batch_import_dialog(self) -> list:
        """
        弹出批量导入窗，返回用户挑中的值（**未经去重**）。

        ⚠ 单独一个方法是为了**可测**：模态窗在测试里点不动（`QDialog.exec`
        是 C++ 槽，猴子补丁不可靠），所以判据替换的是这一层（见
        `tests/test_custom_statement_entry.py::test_批量导入窗口一次收下一整批`）。
        """
        from dataclasses import dataclass
        dlg = QDialog(self)
        dlg.setWindowTitle('批量导入候选值')
        dlg.resize(720, 480)
        col = QVBoxLayout(dlg)
        col.setSpacing(T.SPACE['sm'])

        tip = QLabel('粘一大段也行：一行一个、空格分隔、逗号分隔都认；'
                     '去空、去重，**追加**到已有候选后面。')
        tip.setProperty('role', 'caption')
        tip.setWordWrap(True)
        col.addWidget(tip)

        text = QPlainTextEdit()
        text.setPlaceholderText('例：\n泳装\n校服 汉服\nhappy sad')
        col.addWidget(text, 1)

        sources = self.project_sources()
        listed = []
        if sources:
            head = QLabel('或者从项目里勾（可以多选）：')
            head.setProperty('role', 'caption')
            col.addWidget(head)
            listed = QListWidget()
            listed.setSelectionMode(QListWidget.MultiSelection)
            for r in sources:
                label = str(r.get('label') or '')
                values = [str(v) for v in (r.get('values') or [])]
                item = QListWidgetItem(f'{label}（{len(values)} 个）')
                item.setData(Qt.UserRole, values)
                item.setToolTip('、'.join(values[:40])
                                + ('…' if len(values) > 40 else ''))
                listed.addItem(item)
            listed.setMaximumHeight(140)
            col.addWidget(listed)

            all_btn = QPushButton('全选')
            all_btn.setProperty('role', 'ghost')
            all_btn.clicked.connect(listed.selectAll)
            col.addWidget(all_btn)

        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton('导入')
        ok.setProperty('role', 'primary')
        cancel = QPushButton('取消')
        row.addWidget(ok)
        row.addWidget(cancel)
        col.addLayout(row)

        @dataclass
        class _Pick:
            values: list = None            # type: ignore[assignment]

        pick = _Pick([])

        def do_import():
            vals = split_candidate_values(text.toPlainText(), multi=True)
            for item in (listed.selectedItems() if listed else ()):
                vals.extend(str(v) for v in (item.data(Qt.UserRole) or []))
            pick.values = vals
            dlg.accept()

        ok.clicked.connect(do_import)
        cancel.clicked.connect(dlg.reject)
        if dlg.exec() != QDialog.Accepted:
            return []
        return list(pick.values)

    def set_sources_provider(self, provider):
        """接上"从项目里拿"的来源（卡片把同一份 provider 注入进来）。"""
        self._sources_provider = provider

    def _remove(self, value: str):
        if value in self._values:
            self._values.remove(value)
        self._rebuild()

    def _rebuild(self):
        self._row.clear()
        for value in self._values:
            chip = QPushButton(f'{value}  ✕')
            chip.setProperty('role', 'chip')
            chip.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            chip.setToolTip('点一下去掉这个候选值')
            chip.clicked.connect(lambda _=False, v=value: self._remove(v))
            self._row.add(chip)
        self.updateGeometry()
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception as e:                   # noqa: BLE001 - 通知失败不该炸
                swallow('ui_qt.param_editor.ValueChips.changed', e)


class FieldCard(QFrame):
    """
    **一个「要填的东西」一张卡** —— 单列堆叠，标签在上、控件在下。

    顺序就是"人怎么读它"（也是 `pattern` 里的顺序）：

        这一格是                  [ 从这些里选 ▾ ]
        这一列叫什么（编剧看到的）  [ 角色         ]
        ☐ 这一格可以有多个（例：happy sad）
        ☐ 编剧只能从下面这些里选
        [ 泳装 ✕ ] [ 校服 ✕ ]
        [ 一行一个、空格分隔都行 ]      ← 勾了"多个"就是这个口径
        [ 📋 批量粘贴一大段… ]          ← 勾了"多个"才露出来

    ⚠⚠ **勾了「可以有多个」= 换一副收词口径**（用户 2026-10-16：
    "当能够多选的时候就必须要有批量导入候选"）：

      · 不勾：一格**一个值** → 空格不算分隔符（"洛丽塔 长裙"是一个候选值）；
      · 勾了：一格**可以多个** → 空格/换行都算分隔符，并露出批量入口
        （真机 `expressions` 组一次 68 个，一个个敲不现实）。

    ⚠⚠ **"剧本里怎么写"这个字段被删掉了**（2026-10-13 用户否掉）：

    > 就是这个剧本里怎么写这就很令人误会……我不填这个，剧本里怎么写与我这个
    > 结构有什么关系吗？你让我填吧……你又只给一行！比如说角色有 lan ye mm dhy 等……
    > 这些不可能全对应上！所以这个你让我填根本就不合理！编剧也不知道啊？？？
    > 而且这文字又很令人误会了！剧本里怎么写？在编剧眼里……剧本就是"角色"……

    说得对，所以现在**只有一个名字**：`这一列叫什么（编剧看到的）`。
    它同时当"表格列名"用（内部键就用它自己）—— 于是：

      · 不用再让任何人回答"代码里那串字是什么"（编剧答不出来，也不该问）；
      · 值到底长什么样由**试切/真实项目**回答，不由这个字段回答。

    卡头右侧：`分栏：服装（自动认的）` + `⚡ 从项目里拿` + `↑ ↓` + `删掉这一格`。
    """

    def __init__(self, on_change=None, on_remove=None, on_move=None,
                 sources_provider=None, parent=None, on_missing_change=None):
        super().__init__(parent)
        self.setProperty('role', 'card')
        self.setFrameShape(QFrame.NoFrame)
        self._on_change = on_change
        self._on_remove = on_remove
        self._sources_provider = sources_provider
        #: ⚠ "缺列名"状态一变就要让外面知道（弹窗据此**挡住确定键**）——
        #:   否则用户辛苦导入的候选会被后端**静默丢掉**（见 `_refresh_name_warning`）。
        self._missing_changed = on_missing_change
        #: 这一格**自动认到**的分栏（`FieldList.match_column` 算出来的）
        self._column = ''
        self._columns_changed = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(T.SPACE['md'], T.SPACE['sm'],
                                 T.SPACE['md'], T.SPACE['sm'])
        outer.setSpacing(T.SPACE['xs'])

        head = QHBoxLayout()
        head.setSpacing(T.SPACE['xs'])
        self._title = QLabel('这一格要填的东西')
        self._title.setProperty('role', 'cardTitle')
        self._title.setWordWrap(True)
        head.addWidget(self._title, 1)
        #: ⚠ **缺列名要当场看得见**（2026-10-16 用户实测的坑）：从前没名字这一格
        #:   会被后端"静默丢掉"，用户只在控制台里看见一句"参数：无"，人当场懵。
        self._name_warning = QLabel()
        self._name_warning.setProperty('role', 'caption')
        self._name_warning.setStyleSheet('color: #B3261E;')
        # ⚠ **必须折行**：不折行时这一句会把卡头最小宽撑到 391 px
        #   （`test_a_param_card_stays_narrow_even_with_many_choices` 当场变红）。
        #   完整解释放在预览里（`dialog_learn._missing_name_notice`）。
        self._name_warning.setWordWrap(True)
        head.addWidget(self._name_warning)
        #: 一键把"建议的列名"填进去（分栏自动认到的名字 / 刚导入的那组来源）
        self._name_suggest = QPushButton()
        self._name_suggest.setProperty('role', 'ghost')
        self._name_suggest.setToolTip('把建议的名字填进「这一列叫什么」')
        self._name_suggest.clicked.connect(self._use_suggested_name)
        head.addWidget(self._name_suggest)
        self._name_suggestion = ''
        self._column_hint = QLabel()
        self._column_hint.setProperty('role', 'caption')
        self._column_hint.setToolTip(
            '这一格的候选属于哪一栏（把下拉里的候选分组用，例如「服装」）。\n'
            '按"这一格的名字 / 显示成什么"自动认；认不出就不显示。')
        head.addWidget(self._column_hint)
        self._pick_btn = QPushButton('⚡ 从项目里拿')
        self._pick_btn.setProperty('role', 'ghost')
        self._pick_btn.setToolTip(
            '候选值不用手打：从项目里一次倒进来\n'
            '· 立绘属性（引擎自己分好的组，如 expressions）\n'
            '· 角色表\n'
            '· 这个动作在脚本里真正用过的值')
        self._pick_btn.clicked.connect(self._on_import)
        self._pick_btn.setVisible(False)
        head.addWidget(self._pick_btn)
        self._up = QPushButton('↑')
        self._down = QPushButton('↓')
        for btn, tip in ((self._up, '跟上一格换位置'), (self._down, '跟下一格换位置')):
            btn.setProperty('role', 'ghost')
            btn.setToolTip(tip)
            btn.setFixedWidth(28)
        self._up.clicked.connect(lambda: on_move(self, -1) if on_move else None)
        self._down.clicked.connect(lambda: on_move(self, +1) if on_move else None)
        head.addWidget(self._up)
        head.addWidget(self._down)
        self._rm = QPushButton('删掉这一格')
        self._rm.setProperty('role', 'ghost')
        self._rm.setToolTip('这个动作不需要填它')
        self._rm.clicked.connect(lambda: on_remove(self) if on_remove else None)
        head.addWidget(self._rm)
        outer.addLayout(head)

        # ---- 单列堆叠的字段（**没有多列网格**）----
        self._kind = QComboBox()
        for key, text in (('keyword', '固定几个字（如 show / at）'),
                          ('variable', '谁都可以（如人物名）'),
                          ('choice', '从这些里选（枚举）'),
                          ('optional', '可出现可不出现的一段')):
            self._kind.addItem(text, key)
        self._kind.setToolTip(
            '固定几个字 = 剧本里必须原样出现这几个字；\n'
            '谁都可以 = 任意值（人物名、数字…）；\n'
            '从这些里选 = 编剧只能从你列的候选值里挑；\n'
            '可出现可不出现 = 这一段有时写有时不写（如 at 位置）。')
        self._kind.currentIndexChanged.connect(self._on_kind_changed)
        self._kind.currentIndexChanged.connect(self._changed)
        # ⚠ **只有一个名字**（"剧本里怎么写"已删，见类 docstring）。
        #   它放的是**编剧看到的那一列叫什么**（例：角色 / 服装 / 神态）。
        self._name = QLineEdit()
        self._name.setPlaceholderText('编剧看到的列名，例：角色 / 服装 / 神态')
        self._name.setToolTip(
            '这一格在表格/卡片上叫什么。\n'
            '填「角色」就够 —— 不用去管代码里那串字长什么样，'
            '那由「试切」和项目里真实的资源告诉你。')
        self._name.textChanged.connect(self._changed)
        self._name.textChanged.connect(self._column_maybe_changed)
        #: 兼容视图：`label` 与 `_name` 现在**同源**（老代码读 `card._label`）。
        self._label = self._name
        #: ⚠ **内部键（旧形状的 `name`）**：只在读存量声明时非空。
        #:   它不进界面（用户明确说"剧本里怎么写"让人误会），但**写回时保留** ——
        #:   否则"中文列名"会悄悄替换掉项目原有的英文键（那是一次静默的数据改写）。
        self._key = ''
        #: 存量声明里"代码里必须出现的那串字"（`set_data` 会填）——
        #: ⚠ 在这里先立好：`_refresh_title()` 在建卡过程中就会问
        #: `has_content()`，那时 `set_data` 还没跑（AttributeError 实测踩到）。
        self._fixed_text = ''
        self._display_name = ''
        self._limit = QCheckBox('编剧只能从下面这些里选')
        self._limit.setToolTip('不勾 = 编剧随便写（候选只是下拉里的备选）；'
                               '勾了 = 只能从你列的候选里挑。')
        self._limit.toggled.connect(self._on_limit_toggled)
        # ⚠ 「可以有多个」（2026-10-13 用户指出的 A/B/C）：
        #   > 你去查查服装的 A B C 定义……A 是默认的，也就是可填可不填的，
        #   > 它的默认字符为空字符！但是它不是一段，它**允许有多个**！
        #   > 你这个"可填可不填"的根本就没多段的选择！
        #   真机依据：`show lan happy sad` 里 `happy sad` 是**同一格**
        #   （`layeredimage lan` 的 expressions 组可以有多个属性）。
        self._multi = QCheckBox('这一格可以有多个（例：happy sad）')
        self._multi.setToolTip(
            '勾上 = 这一格可以连着写好几个词（空格分隔），试切会把它们都收进来。\n'
            '真机例子：`show lan happy sad` —— 一句里同时给两个表情属性。')
        self._multi.toggled.connect(self._changed)
        self._values = ValueChips(on_change=self._changed)
        # ⚠ 「可以有多个」不只是个标志：它同时决定**候选值怎么收词**
        #   （空格算不算分隔符）、并露出批量导入入口（用户 2026-10-16：
        #   "当能够多选的时候就必须要有批量导入候选"）。
        #   必须在 `_values` 建好**之后**接 —— 别把顺序写反了。
        self._multi.toggled.connect(self._values.set_multi)
        self._values.set_multi(self._multi.isChecked())
        #: 批量导入对话框要的"从项目里拿"来源（与卡片那枚 ⚡ 同一份）
        self._values.set_sources_provider(self._project_sources)

        self._field('这一格是', self._kind)
        self._field('这一列叫什么（编剧看到的）', self._name)
        self._stack_add(self._multi)
        self._stack_add(self._limit)
        self._stack_add(self._values)
        self._values.setVisible(False)
        # ⚠ `ValueChips` 是**普通 QWidget 当容器**（不用重写容器控件）——
        #   Qt 认不出"它里面是布局"，`sizeHint()` 于是变成 0×0，
        #   方块加进去了却不显示（实测：加了 8 个方块，界面一个都看不见）。
        #   显式喂进去：高度 = 内容折行后的高度，宽度 = 可伸展。
        self._values.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._values.setMinimumHeight(56)
        self._values.set_height_source()
        self._on_kind_changed()
        self._refresh_title()

    # ---- 内部 ----
    def _field(self, label: str, widget: QWidget):
        tag = QLabel(label)
        tag.setProperty('role', 'fieldLabel')
        tag.setWordWrap(True)
        self._stack_add(tag)
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._stack_add(widget)

    @property
    def body(self):
        """
        旧名（判据 `test_no_two_widgets_share_a_grid_cell_in_a_param_card`
        扫的是 `card.body`）。新版的卡身是**单列**布局，物理上不可能叠格。
        """
        return self.layout()

    def _stack_add(self, widget: QWidget):
        lay = self.layout()
        lay.addWidget(widget)

    def _on_kind_changed(self, *_a):
        is_choice = self._kind.currentData() == 'choice'
        self._pick_btn.setVisible(is_choice)
        if not is_choice:
            self._limit.setChecked(False)
            self._values.setVisible(False)

    def _on_limit_toggled(self, on: bool):
        self._values.setVisible(bool(on))
        if on:
            self._values._edit.setFocus()
        self._changed()

    def _changed(self, *_a):
        self._refresh_title()
        if self._on_change:
            self._on_change()

    def _column_maybe_changed(self, *_a):
        fn = getattr(self, '_columns_changed', None)
        if fn is not None:
            try:
                fn()
            except Exception as e:                   # noqa: BLE001 - 重算不该炸
                swallow('ui_qt.param_editor.FieldCard.rematch', e)

    def _refresh_title(self):
        text = self._name.text().strip()
        self.set_title(text or '这一格要填的东西')
        self._refresh_name_warning()

    # ---- 缺列名（别静默丢格子）----
    def has_content(self) -> bool:
        """
        这一格**填过东西吗**（有内容才会"被丢掉 = 真丢数据"）。

        "空卡"是常态（一进来就有一格、加了两格还没想好）—— 那种格子没名字
        也不该拦着人写，只是它不会进声明（后端行为，界面上给个提醒就够）。
        """
        if str(self._key or '').strip() or str(self._fixed_text or '').strip():
            return True
        if str(self._name.text()).strip():
            return True
        # ⚠ 建卡过程中 `_refresh_title()` 会先跑一次（那时 `_values` 还没建好），
        #   所以这里一律用 getattr 兜底。
        values = getattr(self, '_values', None)
        if values is not None and values.values():
            return True
        return bool(getattr(self, '_limit', None) is not None
                    and self._limit.isChecked()
                    or getattr(self, '_multi', None) is not None
                    and self._multi.isChecked())

    def needs_a_column_name(self) -> bool:
        """
        这一格会不会被后端**丢掉**，而且**丢掉的是人填过的东西**？

        对的是 `learn_statement.build_declaration` 那段（读 `name` → 空就退
        `label`/`column` → 还空就"这一格没写进声明"）。用户 2026-10-16 的现场
        正是"倒好一屏候选、只有一格填了名字" → 另外两格的候选被**静默丢掉**。

        ⚠ 只判"**有内容**且没名字"：空白的格子（还没想好）不该把确定键锁死，
        它只是不会进声明 —— 那种在界面上给个浅色提醒即可。
        """
        if not self.has_content():
            return False
        return not self._resolved_column_name()

    def _resolved_column_name(self) -> str:
        """与后端同一套口径：内部键 → 界面上的列名。"""
        return str(self._key or self._name.text() or '').strip()

    def missing_name_hint(self) -> str:
        """给外面看的一句话（弹窗把它汇总成"这几格还没起名字"）。"""
        return self._name_suggestion or '例如：角色 / 服装 / 神态'

    def _use_suggested_name(self):
        if self._name_suggestion:
            self._name.setText(self._name_suggestion)
            self._name.setFocus()

    def _refresh_name_warning(self):
        """
        ⚠ **只在"会真正丢东西"时报警**（有内容 + 没列名）。

        为什么不给"空白格"也常驻一句提示（试过，实测否决）：卡头多一个
        常驻 label 会把**空卡最小宽从 320 顶到 352 px**
        （`test_a_param_card_stays_narrow_even_with_many_choices` 当场变红）——
        而 320 px 那个窄度正是用户要的（"选的一多就把后面那个变量挤出窗口"）。
        空白格的事由预览那一行说清（`dialog_learn._missing_name_notice`）。
        """
        missing = self.needs_a_column_name()
        self._name_warning.setText('⚠ 没起名字（会和候选一起丢掉）' if missing else '')
        self._name_warning.setStyleSheet('color: #B3261E;')
        self._name_warning.setVisible(missing)
        self._name_suggest.setText(
            f'用「{self._name_suggestion}」当列名' if missing and self._name_suggestion
            else '')
        self._name_suggest.setVisible(bool(missing and self._name_suggestion))
        fn = getattr(self, '_missing_changed', None)
        if fn is not None:
            try:
                fn()
            except Exception as e:                   # noqa: BLE001 - 通知失败不该炸
                swallow('ui_qt.param_editor.FieldCard.missingChanged', e)

    def set_title(self, text: str):
        self._title.setText(text)

    def _project_sources(self) -> list:
        """读一遍"从项目里拿"的来源（卡片与批量对话框共用这一处）。"""
        try:
            if self._sources_provider is not None:
                return list(self._sources_provider() or [])
        except Exception as e:                       # noqa: BLE001 - 导入不该炸卡片
            swallow('ui_qt.param_editor.FieldCard.import', e)
        return []

    def pick_source_action(self):
        """
        弹出"从项目里拿"菜单，返回用户点的那一项（`QAction`），取消返回 None。

        ⚠ 单列一个方法是**为了可测**：模态菜单在测试里点不动（`QMenu.exec`
        是 PySide6 侧调用的），所以判据替换的是这一层 —— 见
        `tests/test_custom_statement_entry.py::test_导入一组候选之后列名可以一键填上`。
        """
        menu = QMenu(self)
        rows = self._project_sources()
        if self._multi.isChecked():
            act = menu.addAction('📋 批量导入（可勾多组 / 粘一大段）…')
            act.triggered.connect(
                lambda _=False: self._values.open_batch_import())
            menu.addSeparator()
        if not rows:
            act = menu.addAction('（项目里还没读到可导入的值 —— 先「打开项目」；'
                                 '"立绘属性"要先跑过一次引擎体检）')
            act.setEnabled(False)
        else:
            for r in rows:
                act = menu.addAction(f'{r.get("label")}'
                                     f'（{r.get("count", 0)} 个）')
                act.setData(list(r.get('values') or []))
        return menu.exec(self._pick_btn.mapToGlobal(
            self._pick_btn.rect().bottomLeft()))

    def _on_import(self):
        """「⚡ 从项目里拿」：把项目里**真正有**的候选摆出来，挑一个倒进这一格。

        ⚠ 工具**不猜**"这一格是不是表情"（那是语义，猜错改剧情）——
        它只列"项目里到底有哪些值、从哪来"，点哪个由人决定。

        ⚠ 一格可以填多个值的时候，这条路太慢（真机 `expressions` 一次 68 个）——
        那时该走 `ValueChips.open_batch_import`（可以勾好几组一起倒）。
        所以这里多加一行指路，而不是把菜单堆成一棵勾选树。
        """
        chosen = self.pick_source_action()
        if chosen is None or not chosen.data():
            return
        # 刚导入的这组来源的名字，就是最好的"列名建议"
        # （真机实测：`expressions` 组倒出来 68 个，用户接着要起的就是"神态"那种名）
        label = str(chosen.text() or '').split('（', 1)[0].strip()
        if label:
            self._name_suggestion = label
            self._refresh_name_warning()
        self._values.add_values(chosen.data())
        # 拿进来就是"只能填这些"——不勾上候选白拿（只有勾了才进下拉）
        self._limit.setChecked(True)
        self._values.setVisible(True)
        self._changed()

    # ---- 分栏标注（自动认，不是下拉）----
    def set_column(self, column: str):
        """
        把"这一格的候选属于哪一栏"标在卡头。

        用户 2026-10-12：
        > 如果你所谓的"词组"只是给变量一个名字的话……那你不应该弄一个下拉，
        > 你应该就在原地，就在旁边标注

        所以这里是**标注**：`FieldList.match_column` 按"这一格的名字 / 显示名"
        自动认；认不出就整条藏起来（不写"无"、不占地方）。
        """
        self._column = str(column or '')
        self._column_hint.setText(f'分栏：{self._column}' if self._column else '')
        self._column_hint.setVisible(bool(self._column))
        # 自动认到的分栏名就是最好的"列名建议"（「服装」这种）
        if self._column and not self._name_suggestion:
            self._name_suggestion = self._column
        self._refresh_name_warning()

    def column_of(self) -> str:
        return str(self._column or '')

    # ---- 旧名字的别名（判据与旧调用点还在用"group / import / choices"这套字）----
    def group_of(self) -> str:
        return self.column_of()

    @property
    def _import(self):
        return self._pick_btn

    @property
    def _choices(self):
        return self._values

    @property
    def _group_hint(self):
        """旧名（判据里还在用 `card._group_hint`）——就是卡头那行分栏标注。"""
        return self._column_hint

    # ---- 数据 ----
    def data(self) -> dict:
        """
        这一格 → 后端参数。

        ⚠⚠ **候选与"限死"是两件事**（2026-10-13 用户实测修）：

        > 这个仍然切不上！有一个巨大的 bug……

        真凶：旧版只要候选非空就把 `kind` 报成 `choice`，而 `choice` 在
        `pattern` 里是**封闭枚举**（值必须在候选里）—— 于是用户在「角色」里
        贴了一串角色名之后，剧本里任何一个**没列进去**的角色都判"切不上"，
        而界面只会说"检查顺序或关键字写法"。真实项目的资源一直在变，
        封闭枚举本来就该是**显式选择**的结果，不是"填了候选"的副作用。

        现在：`kind` 只由「这一格是」决定（`variable` 就是任意值）；
        候选进 `options`，`limit` 单独告诉后端"这一格是不是封闭枚举"。
        """
        name = self._name.text().strip()
        is_fixed = self._kind.currentData() == 'keyword'
        # ⚠ "列名"取哪一个：**存量从宽、新建从严**。
        #   · 存量（`set_data` 给过 `label`）→ 那个 `label` 就是列名，
        #     界面上字段显示它只是"看得见"，不改变它是谁；
        #   · 用户新建的格子 → 界面上那唯一的字段就是列名。
        #   这条让旧调用方 `add_param('intensity', '强度', …)` 仍然产出
        #   `{'name': 'intensity', 'label': '强度'}`（判据压着），
        #   而新界面里"只有列名"（用户要的那个简化）。
        column = self._display_name or name
        return {'kind': self._kind.currentData() or 'variable',
                'group': self.column_of(),
                'text': (self._fixed_text or name) if is_fixed else '',
                'name': self._key or name,
                'label': column,
                'column': column,
                'choices': self._values.text(),
                'limit': bool(self._limit.isChecked()),
                'multi': bool(self._multi.isChecked())}

    def set_data(self, name: str = '', label: str = '', choices: str = '',
                 kind: str = '', group: str = '', **extra):
        if kind:
            idx = self._kind.findData(kind)
            if idx >= 0:
                self._kind.setCurrentIndex(idx)
        self._on_kind_changed()
        # ⚠ 两个来源都认，**显示名优先**：
        #   · `label` = 老的"这一列显示成什么"（存量声明与旧调用方都在这边）；
        #   · `name`  = 界面上唯一那个字段（`data()` 里与 `label` 同源），
        #               也是旧调用方传的第一个参数。
        #   显示名优先，于是"「小明」这一格显示成「角色」"这种老数据仍然
        #   显示成「角色」（而不是把内部键推到编剧眼前）。
        self._name.setText(str(label or name or ''))
        #: 存量声明的**内部键**（旧 `name`）。它不进界面，但**写回时保留** ——
        #: 否则"中文列名"会悄悄替换掉项目原有的英文键（一次静默的数据改写）。
        self._key = str(name or '').strip()
        #: 存量声明的**显示名**（旧 `label`）。非空 = 这一格是从存量读来的，
        #: 那一刻的"列名"就该是它（界面上那个字段显示它，但不改变它是谁）。
        self._display_name = str(label or '').strip()
        #: 存量声明里"代码里必须出现的那串字"（`text`，只有固定段用）。
        self._fixed_text = str(name or '').strip()
        text = str(choices or '')
        self._values.set_text(text)
        # 已有声明里本来就有候选 → 自动展开（不然人会以为丢了）。
        # ⚠ **但不自动勾"只能从这些里选"** —— 存量候选多半是"建议清单"，
        #   一勾上就把存量剧本判成"切不上"（上面那个 bug 的另一半）。
        self._limit.setChecked(False)
        self._values.setVisible(bool(text.strip()))
        # ⚠ `multi`（"可以有多个"）**照存量恢复** —— 它不像候选那样会被误解成
        #   封闭枚举，而是纯粹的语法事实（真机里 `happy sad` 就是一格两个词）。
        try:
            self._multi.setChecked(bool((extra or {}).get('multi')))
        except Exception as e:                      # noqa: BLE001 - 恢复不了就用缺省
            swallow('ui_qt.param_editor.FieldCard.set_data.multi', e)
        if group:
            self.set_column(group)

    def focus(self):
        self._name.setFocus()


class FieldList(QWidget):
    """
    字段卡的**竖向列表** —— ⚠ **不是** `QScrollArea`（那是"两条滚动条"的来源）。

    滚动一律由外层那一个滚动区负责；这里只负责竖着排。

    对外接口与旧版同形（`add_field` / `rows` / `clear` / `count` / `slots` /
    `set_columns` / `move`），所以弹窗与判据的接线不用改。
    """

    def __init__(self, parent=None, on_change=None, sources_provider=None,
                 on_missing_change=None):
        super().__init__(parent)
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(T.SPACE['sm'])
        self._col.addStretch(1)
        self._cards: list = []
        #: 当前分栏表 `[(分栏标识, 这一栏显示成, [候选词…])…]`
        self._columns: list = []
        self._on_change = on_change
        self._sources_provider = sources_provider
        #: 任一格的"缺列名"状态变了就回调（弹窗据此挡住确定键）
        self._on_missing_change = on_missing_change

    # ---- 缺列名（会被后端丢掉的格子）----
    def missing_name_cards(self) -> list:
        """
        哪些格子**会被后端丢掉**（没有列名）。

        用户 2026-10-16 实测现场：三格都倒好候选了，只有一格填了名字 →
        另外两格被 `build_declaration` 静默丢掉，预览里却只写"参数：无"。
        所以弹窗必须**在按确定之前**把这些格子摆出来。
        """
        return [c for c in self._cards if c.needs_a_column_name()]

    # ---- 分栏 ----
    @property
    def _groups(self) -> list:
        """旧名（判据里还在用 `params._groups`）——就是分栏表。"""
        return self._columns

    def columns(self) -> list:
        return list(self._columns)

    def set_columns(self, columns):
        """
        分栏表变了 → **重算每一格的标注**（`match_column`）。

        `columns` 收 `[(标识, 显示名, [候选词…])…]`；旧的"只给一串名字"也认。
        """
        norm = []
        for c in (columns or ()):
            if isinstance(c, (list, tuple)):
                key = str(c[0] or '').strip()
                shown = str(c[1] or '').strip() if len(c) > 1 else ''
                opts = [str(o).strip() for o in (c[2] if len(c) > 2 else ()) or ()
                        if str(o).strip()]
            else:
                key, shown, opts = str(c or '').strip(), '', []
            if key:
                norm.append((key, shown, opts))
        self._columns = norm
        self.rematch()

    def match_column(self, name: str, shown: str) -> str:
        """
        这一格属于哪一栏 —— **自动认**（名字 / 显示名对得上就算）。

        理由（用户 2026-10-12）："就在原地，就在旁边标注，而不是下拉一个展开"。
        规则要能解释、且**宁可不认**：
          1. 这一格的名字或显示名，**逐字等于**某一栏的标识；
          2. 或等于它声明过的候选词；
          3. 或等于它的显示名；
          4. 都不中 → `''`（界面什么都不显示 —— 不是每个项目都有「服装」）。
        """
        tokens = [str(t or '').strip() for t in (name, shown) if str(t or '').strip()]
        if not tokens:
            return ''
        for key, label, opts in self._columns:
            if key in tokens or (label and label in tokens):
                return key
            for opt in opts:
                if opt in tokens:
                    return key
        return ''

    def rematch(self):
        for card in self._cards:
            row = card.data()
            card.set_column(self.match_column(str(row.get('name') or ''),
                                              str(row.get('label') or '')))

    # ---- 卡片 ----
    def add_field(self, name: str = '', label: str = '', choices: str = '',
                  kind: str = 'variable', group: str = '', **extra):
        card = FieldCard(on_change=self._on_change, on_remove=self._remove,
                         on_move=self.move,
                         sources_provider=self._sources_provider, parent=self,
                         on_missing_change=self._on_missing_change)
        card._columns_changed = self.rematch
        card.set_data(name, label, choices, kind, group, **extra)
        if not group:
            card.set_column(self.match_column(name, label))
        self._col.insertWidget(self._col.count() - 1, card)
        self._cards.append(card)
        card.focus()
        if self._on_change:
            self._on_change()
        return card

    def rows(self) -> list:
        """卡片 → 后端参数（`build_declaration` 吃这个）。"""
        out = []
        for card in self._cards:
            row = dict(card.data())
            # ⚠ `limit` 不是列的一部分（它是"候选限不限死"），不进 `columns`
            #   ——由 `slots()` 报给 `pattern`。这里摘掉免得写进 rules.json 的列里。
            row.pop('limit', None)
            out.append(row)
        return out

    def count(self) -> int:
        return len(self._cards)

    def clear(self):
        for card in list(self._cards):
            self._remove(card, notify=False)
        if self._on_change:
            self._on_change()

    def remove_current(self):
        if self._cards:
            self._remove(self._cards[-1])

    def move(self, card, delta: int):
        """
        把这一格**跟邻居换位置** —— 顺序是"代码里长什么样"的唯一依据
        （改完立刻能在「试切」里看出来）。
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

    def slots(self) -> list:
        """卡片 → **位置序列**（喂给 `authoring.pattern` 做试切/落进声明）。"""
        out = []
        for card in self._cards:
            row = card.data()
            out.append({'kind': row.get('kind') or 'variable',
                        'group': row.get('group') or '',
                        'text': row.get('name') or '',
                        'column': row.get('label') or '',
                        'choices': row.get('choices') or '',
                        # ⚠ "限死"与"可以有多个"都是**显式**的（见 `FieldCard.data`）：
                        #   不报上去 = 后端按"候选只是建议 / 一格一个词"处理。
                        'limit': bool(row.get('limit')),
                        'multi': bool(row.get('multi'))})
        return out

    def _remove(self, card, notify: bool = True):
        if card in self._cards:
            self._cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        if notify and self._on_change:
            self._on_change()

    # ---- 旧名字的别名（判据与旧调用点还在用 `add_param` / `_cards`）----
    def add_param(self, name: str = '', label: str = '', choices: str = '',
                  kind: str = 'variable', group: str = ''):
        return self.add_field(name, label, choices, kind, group)


__all__ = ['FieldCard', 'FieldList', 'ValueChips']
