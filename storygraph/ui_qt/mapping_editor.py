# -*- coding: utf-8 -*-
"""
storygraph.ui_qt.mapping_editor
===============================
**"不会写 JSON 也能定义映射"的那张表**（用户 2026-10-30，⑧）

用户原话：

> json的自定义能不能用工具显式化……？本质上它就是键值对嘛！也就是类似于
> 之前探测未定义角色名称的样子，弄一个显化的UI，然后点击保存的话再存为json……？
> 因为实际上使用json定义对非程序员门槛有点高。

所以这一页只有**一张能打的表**：

  · 左栏六段（角色 / 图片 / 规则 / 服装代号 / 表情编号 / 属性名），点哪段看哪段；
  · 右栏一行一条，**一格一个值**（`是什么` 是下拉、`角色` 能挑已有 tag），
    「新增一行 / 删除选中 / 上移下移 / 选一张图…」都在手边；
  · 改的只是内存里的工作副本 —— **点「保存到项目根」才落盘**，而且和别的
    入口走同一条 diff + 确认写入 + `.bak`（铁律第 5 条）。这一页**自己绝不
    写文件**，所以它连 `save_mapping` 都不 import。

真正的编辑逻辑一行都不在这里：`assets/mapping_edit.py`（纯函数 + 判据）。
本文件只负责"把 `Row` 摆出来、把人的输入交回去"。

## 为什么不做成"键 / 值 两列的通用表"

通用键值表能表达一切，也就等于什么都没表达：人看到 `images` 里一个
`{"kind": "sprite", "char": "ling"}` 不知道该填什么。所以每一段有自己的列，
列头就是人话（「是什么 / 角色 / 属性」），下拉里就是全部合法值
（`asset_identity.KINDS` + `KIND_LABELS`）—— **合法值不许让人背**。

## 为什么改一格不重建整张表

`itemChanged` 里重建会在人打字打到一半时把焦点抢走（真实项目里这条最招
人烦）。所以：改一格 → 只改模型 + 重算问题条；**加/删/移动行**才重建。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox,
                               QStyledItemDelegate, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from storygraph.assets import code_tables as CT
from storygraph.assets import mapping_edit as ME
from storygraph.assets import sprite_codes as SC
from storygraph.core.safeguard import swallow
from storygraph.ui_qt import theme
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt.asset_picker import KIND_LABELS
from storygraph.ui_qt.wheel_guard import ensure_installed

#: 「它是哪种图」下拉的全部选项：`(显示文字, 写进文件的值, 悬停解释)`。
#:
#: 为什么**不直接用 `KIND_LABELS`**（用户 2026-10-30："我需要一个编剧跟文案
#: 都能看明白的表"）：那份是给**悬浮提示 / 详情面板**写的长句
#: （`角色预览图（不能直接上舞台）`），塞进 130px 的格子只会被截成
#: `角色预览图（不能…`。这里用**短名**，长句挪到每一行的悬停解释里。
KIND_CHOICES = (
    ('（先不填 · 让工具自己猜）', '', '留空 = 工具按文件夹和文件名自己判断'),
) + tuple(
    (short, kind, KIND_LABELS.get(kind, kind)) for kind, short in (
        ('sprite', '立绘'),
        ('preview', '角色预览图'),
        ('avatar', '角色头像'),
        ('bg', '背景'),
        ('cg', 'CG'),
        ('ui', '界面'),
        ('other', '其他图片'),
        ('unknown', '还认不出来'),
    ))

#: 「它是哪种图」的 `值 → 屏幕上那两个字` / 反过来。**表格里显示人话**
#: （`bg` → 背景）：文件里存的是 `bg`，但屏幕上再出现一次 `bg` 等于白改。
KIND_TEXT = {kind: short for short, kind, _tip in KIND_CHOICES if kind}
KIND_BY_TEXT = {short: kind for short, kind, _tip in KIND_CHOICES if kind}

#: 最多列几条问题（再多就"还有 N 条"，不让人看一屏红字）。
MAX_PROBLEMS = 3

#: 第一列（这一条是哪张图 / 哪个角色）的宽。
KEY_WIDTH = 260

#: 表上方那句"这张表是干什么的"（第一眼就该看懂的，不含任何行话）。
WHAT_IS_THIS = ('这张表只回答一件事：哪张图是什么、谁是谁。'
                '它不改剧本 —— 改完点「保存到项目根」才真正存下来。')

#: 代号两段专用的一句（用户 2026-10-31："lan swim_suit angry 怎么变成 lan B 7"）。
WHAT_IS_CODES = ('美术需求表里的写法就是「服装字母 + 表情编号」，'
                 '比如 B7 = 服装 B、第 7 号表情。'
                 '下面把两边对上，上面那个框就能来回换算。')

#: 换算框里先摆一个例子（不用人先想"这里该填什么"）。
CODE_PLACEHOLDER = 'lan swim_suit angry  或  lan B7'


# ---------------------------------------------------------------------------
#  资源表里的角色（`known_characters` 的两种形态）
# ---------------------------------------------------------------------------

def _char_entry(value) -> dict:
    """一个角色在资源表里的样子 → `{'display','aliases','avatar'}`（两种形态都认）。"""
    if isinstance(value, dict):
        entry = dict(value)
    else:
        entry = {'display': str(value or '')}
    aliases = entry.get('aliases')
    if isinstance(aliases, str):
        aliases = [aliases]
    entry['aliases'] = [str(a).strip() for a in (aliases or ())
                        if str(a).strip()]
    return entry


def _char_display(value, fallback: str = '') -> str:
    """这个角色给人看的名字（表里没写就退回剧本里的写法）。"""
    name = str(_char_entry(value).get('display') or '').strip()
    return name or str(fallback or '')


def _char_names(chars, limit: int = 4) -> list:
    """
    挑几个名字摆给人看。

    **按表里的顺序**（不是字母序）：资源表的角色表就是剧本 `define` 的出场
    顺序，前几个通常是主角；字母序会把 `ALL / CAT / DOG`（所有人 / 猫 / 狗）
    排到最前面，那句话读起来就很好笑。名字和写法一样的跳过（没信息量）。
    """
    out = []
    for tag in (chars or {}):
        name = _char_display(chars[tag], tag)
        if name and name.lower() != str(tag).lower():
            out.append(name)
        if len(out) >= limit:
            break
    return out


def _char_fields(entry: dict, tag: str) -> dict:
    """
    资源表里一个角色 → 「角色」段那一行的格子（**只写有信息量的**）。

    名字和写法一模一样时不写（`narrator → narrator` 这种抄进去只是噪音），
    这样一个字都没有的条目干脆不落行 —— 它本来就不需要人管。
    """
    entry = _char_entry(entry)
    fields = {}
    display = str(entry.get('display') or '').strip()
    if display and display.lower() != str(tag).lower():
        fields['display'] = display
    if entry['aliases']:
        fields['aliases'] = list(entry['aliases'])
    avatar = str(entry.get('avatar') or '').strip()
    if avatar:
        fields['avatar'] = avatar
    return fields


class _ChoiceDelegate(QStyledItemDelegate):
    """下拉列。文件里写了不认识的类别时**原样显示**，不悄悄改成别的。"""

    def __init__(self, choices, parent=None):
        super().__init__(parent)
        #: 每项 `(文字, 值)` 或 `(文字, 值, 悬停解释)` —— 两种都收。
        self._choices = tuple(choices)

    def createEditor(self, parent, option, index):      # noqa: N802 - Qt 命名
        combo = QComboBox(parent)
        for choice in self._choices:
            text, data = choice[0], choice[1]
            combo.addItem(text, data)
            tip = choice[2] if len(choice) > 2 else ''
            if tip:
                combo.setItemData(combo.count() - 1, tip, Qt.ToolTipRole)
        return combo

    def setEditorData(self, editor, index):             # noqa: N802 - Qt 命名
        # **真值在 UserRole**（屏幕上那几个字是翻过的「背景」）：拿显示文字去
        # `findData` 永远对不上，下拉会当场冒出一项「背景（不认识）」。
        raw = index.data(Qt.ItemDataRole.UserRole)
        value = str(index.data(Qt.ItemDataRole.EditRole) or ''
                    if raw is None else raw)
        pos = editor.findData(value)
        if pos < 0:
            # 不认识的写法（人手改的 JSON）：摆在最上面让他们看见，
            # 而不是一打开下拉就被换成第一项。
            editor.addItem(f'{value}（不认识）', value)
            pos = editor.count() - 1
        editor.setCurrentIndex(pos)

    def setModelData(self, editor, model, index):       # noqa: N802 - Qt 命名
        raw = str(editor.currentData() or '')
        # 两处都要写：`EditRole` 是格子上的字，`UserRole` 是"写回文件的值"。
        # 只写 EditRole 的话，`_on_item_changed` 会拿到**旧的** UserRole。
        model.setData(index, raw, Qt.ItemDataRole.EditRole)
        model.setData(index, raw, Qt.ItemDataRole.UserRole)


class _SuggestDelegate(QStyledItemDelegate):
    """可输入 + 有建议的文本列（`角色`：挑已有 tag，也允许写占位符 `<tag>`）。"""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self._items = tuple(items)

    def createEditor(self, parent, option, index):      # noqa: N802 - Qt 命名
        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItems(list(self._items))
        combo.setCurrentIndex(-1)
        return combo

    def setEditorData(self, editor, index):             # noqa: N802 - Qt 命名
        editor.setEditText(str(index.data(Qt.ItemDataRole.EditRole) or ''))

    def setModelData(self, editor, model, index):       # noqa: N802 - Qt 命名
        model.setData(index, editor.currentText(),
                      Qt.ItemDataRole.EditRole)


class MappingEditor(QWidget):
    """
    映射的表格式编辑页。

    · `changed`：数据变了（对话框据此重画 JSON 预览 / 更新状态行）；
    · `note`：一句给人看的话（`warn=True` 时标黄）。

    它不认识"当前映射是哪个文件" —— 写盘是对话框的事。
    """

    changed = Signal()
    note = Signal(str, bool)
    #: 表里点了「保存到项目根」——**只是喊一声**，怎么算 diff、写不写由对话框决定。
    save_requested = Signal()

    def __init__(self, parent=None, *, known_chars=None, project_root=''):
        super().__init__(parent)
        self.setObjectName('mapping_editor')
        ensure_installed()
        #: 资源表里认得出的角色。两种形态都认：`{tag: 显示名}` 或
        #: `{tag: {'display','aliases','avatar'}}`（`known_characters`）。
        #: 只干两件事：「剧本里的写法」那一列的建议；以及「这一段空着
        #: 就行 —— 资源表里已经有 N 个角色」那句人话 + 抄写按钮。
        self._known = dict(known_chars or {})
        self._root = str(project_root or '')
        self._edit = ME.MappingEdit({})
        self._baseline = {}
        self._section = ME.SECTIONS[0]
        self._filling = False
        self._build()
        self.load({})

    # ------------------------------------------------------------------
    #  搭界面
    # ------------------------------------------------------------------

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, T.SPACE['sm'], 0, 0)
        lay.setSpacing(T.SPACE['xs'])

        self._banner = QLabel('')
        self._banner.setObjectName('mapping_editor_banner')
        self._banner.setWordWrap(True)
        self._banner.setProperty('role', 'warn')
        self._banner.setVisible(False)
        lay.addWidget(self._banner)

        body = QHBoxLayout()
        body.setSpacing(T.SPACE['sm'])

        self._list = QListWidget()
        self._list.setObjectName('mapping_sections')
        self._list.setFixedWidth(150)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.currentRowChanged.connect(self._on_section_changed)
        body.addWidget(self._list, 0)

        right = QVBoxLayout()
        right.setSpacing(T.SPACE['xs'])

        # 第一眼要能看懂"这张表是干什么的"（用户 2026-10-30：太学术了）。
        self._what = QLabel(WHAT_IS_THIS)
        self._what.setObjectName('mapping_editor_what')
        self._what.setWordWrap(True)
        self._what.setProperty('role', 'caption')
        right.addWidget(self._what)

        self._hint = QLabel('')
        self._hint.setObjectName('mapping_editor_hint')
        self._hint.setWordWrap(True)
        self._hint.setProperty('role', 'note')
        right.addWidget(self._hint)

        # 代号两段：上面一个换算框（`lan swim_suit angry` ⇄ `lan B7`）。
        # 单独一条、只在两段出现 —— 别的段落里它没有意义，摆着只会分神。
        self._code_box = QWidget()
        self._code_box.setObjectName('mapping_code_box')
        code_lay = QVBoxLayout(self._code_box)
        code_lay.setContentsMargins(0, 0, 0, 0)
        code_lay.setSpacing(T.SPACE['xs'])
        line = QHBoxLayout()
        line.setSpacing(T.SPACE['xs'])
        self._code_input = QLineEdit()
        self._code_input.setObjectName('mapping_code_input')
        self._code_input.setPlaceholderText(CODE_PLACEHOLDER)
        self._code_input.setToolTip(
            '左边写角色，后面写衣服和表情；也可以直接写代号（B7 / B 7）。\n'
            '两种写法都会换算成另一种 —— 换算是查下面这张表，表填错这里就错。')
        self._code_input.textChanged.connect(lambda *_: self._update_code())
        line.addWidget(self._code_input, 1)
        self._btn_autofill = QPushButton('按项目自动填')
        self._btn_autofill.setObjectName('btn_code_autofill')
        self._btn_autofill.setToolTip(
            '照项目数一遍：表情编号读角色文件夹，服装字母从注释数；'
            '「中文叫法」另读任何目录的对应.txt（bgm/对应.txt ↔ # bgm13）。')
        self._btn_autofill.clicked.connect(lambda *_: self.autofill_codes())
        line.addWidget(self._btn_autofill, 0)
        code_lay.addLayout(line)
        self._code_out = QLabel('')
        self._code_out.setObjectName('mapping_code_out')
        self._code_out.setWordWrap(True)
        self._code_out.setProperty('role', 'note')
        # 结果里有 `<b>`（代号加粗）：不显式声明 RichText 的话，AutoText 会
        # 因为开头是中文而当成纯文本 —— 屏幕上就露出标签本身。
        self._code_out.setTextFormat(Qt.TextFormat.RichText)
        self._code_out.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        code_lay.addWidget(self._code_out)
        self._code_box.setVisible(False)
        right.addWidget(self._code_box)

        # 这一段还是空的时候，**说清怎么开始**（空表格什么都不说 = 门槛）。
        # 摆在表**上面**：空着的时候人的视线就落在表头那一片，提示得在那儿。
        self._empty = QLabel('')
        self._empty.setObjectName('mapping_editor_empty')
        self._empty.setWordWrap(True)
        self._empty.setProperty('role', 'caption')
        self._empty.setVisible(False)
        right.addWidget(self._empty)

        # 「资源表里已经有 45 个角色」不只是说说 —— 想把它抄进映射的人，一键。
        # 只在「角色」段空着的时候摆出来（已经有行了就不该再劝他抄一遍）。
        self._btn_import = QPushButton('把资源表里的角色写进来')
        self._btn_import.setObjectName('btn_import_characters')
        self._btn_import.clicked.connect(lambda *_: self.import_characters())
        self._btn_import.setVisible(False)
        # 靠左、按自己的宽度（铺满整行会像一条横幅，不像一个能点的按钮）。
        right.addWidget(self._btn_import, 0, Qt.AlignmentFlag.AlignLeft)

        self._table = QTableWidget(0, 2)
        self._table.setObjectName('mapping_edit_table')
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        # 点一格就能改（这是表单，不是只读表格）；键列 / 序号列在填表时
        # 单独去掉可编辑位。
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self._table.itemChanged.connect(self._on_item_changed)
        head = self._table.horizontalHeader()
        # 最后留一条**空列**吃满剩余宽度：表格右边缘就不会停在半空中了。
        # （伸缩真正有内容的那一列不行：窗口一宽就有一列被拉成六七百像素的
        # 空格子，看着像"这里该填东西"。）
        head.setStretchLastSection(True)
        right.addWidget(self._table, 1)

        bar = QHBoxLayout()
        bar.setSpacing(T.SPACE['xs'])
        self._btn_add = QPushButton('新增一行')
        self._btn_add.setObjectName('btn_edit_add')
        self._btn_add.setToolTip('在这一段末尾加一条（空着不填的不会写进文件）')
        self._btn_add.clicked.connect(lambda *_: self.add_row())
        self._btn_del = QPushButton('删除选中')
        self._btn_del.setObjectName('btn_edit_del')
        self._btn_del.setToolTip('删掉选中的行（还没保存，随时可以「重读」放弃）')
        self._btn_del.clicked.connect(lambda *_: self.delete_selected())
        self._btn_up = QPushButton('上移')
        self._btn_up.setObjectName('btn_edit_up')
        self._btn_up.setToolTip('这类规则是从上往下、先对上的算数，所以顺序有意义')
        self._btn_up.clicked.connect(lambda *_: self.move_selected(-1))
        self._btn_down = QPushButton('下移')
        self._btn_down.setObjectName('btn_edit_down')
        self._btn_down.clicked.connect(lambda *_: self.move_selected(1))
        self._btn_pick = QPushButton('选一张图…')
        self._btn_pick.setObjectName('btn_edit_pick')
        self._btn_pick.setToolTip('挑一张图，把它的路径填进「是哪张图」')
        self._btn_pick.clicked.connect(lambda *_: self.pick_image())
        # 表里也放一个保存：人是看着这张表改的，"改完去哪存"不该让他去窗口另一头找。
        # 它和窗口上方的按钮走**同一段**代码（对话框的 `_on_save`），不是第二条写盘路。
        self._btn_save = QPushButton('保存到项目根')
        self._btn_save.setObjectName('btn_edit_save')
        self._btn_save.setProperty('variant', 'primary')
        self._btn_save.setToolTip(
            '把这张表写进项目（与上方同名按钮同一个动作：先看 diff，确认后才落盘）')
        self._btn_save.clicked.connect(lambda *_: self.save_requested.emit())
        self._btn_save.setEnabled(False)
        for btn in (self._btn_add, self._btn_del, self._btn_up, self._btn_down):
            bar.addWidget(btn, 0)
        bar.addStretch(1)
        bar.addWidget(self._btn_pick, 0)
        bar.addWidget(self._btn_save, 0)
        right.addLayout(bar)

        self._problems = QLabel('')
        self._problems.setObjectName('mapping_editor_problems')
        self._problems.setWordWrap(True)
        self._problems.setProperty('role', 'note')
        right.addWidget(self._problems)

        body.addLayout(right, 1)
        lay.addLayout(body, 1)

    # ------------------------------------------------------------------
    #  对外
    # ------------------------------------------------------------------

    def load(self, data):
        """换一份映射（深拷进工作副本；`is_dirty` 以载入时为基线）。"""
        self._edit = ME.MappingEdit(data or {})
        self._baseline = ME.MappingEdit(data or {}).to_data()
        self._fill_sections()
        self._rebuild()

    def current_data(self) -> dict:
        """当前表里的内容（可写的字典；空壳行已经不在了）。"""
        return self._edit.to_data()

    def is_dirty(self) -> bool:
        """和载入时相比改过没有（**比的是规范化之后**，不是字节）。"""
        return self.current_data() != self._baseline

    def section(self) -> str:
        return self._section

    def set_section(self, name: str):
        if name not in ME.SECTIONS or name == self._section:
            return
        self._section = name
        self._list.setCurrentRow(ME.SECTIONS.index(name))
        self._rebuild()

    def set_banner(self, text: str, *, warn: bool = True):
        """顶部那条整段提示（Excel 生效 / 文件读不出来时用）。"""
        text = str(text or '').strip()
        self._banner.setText(text)
        self._banner.setVisible(bool(text))
        self._banner.setProperty('role', 'warn' if warn else 'note')
        _repolish(self._banner)

    def set_can_save(self, can: bool):
        """「保存到项目根」能不能点（由对话框统一判：有项目根 + 表里有内容）。"""
        self._btn_save.setEnabled(bool(can))

    # -- 悬停提示：一句人话 + 一行"技术写法"（只给要查的人看） ----------

    @staticmethod
    def _section_tip(spec) -> str:
        """表头第一列 / 左栏的悬停：这段是干什么的 + 文件里叫什么。"""
        parts = [spec.hint] if spec.hint else []
        if spec.tech:
            parts.append(f'技术写法（给开发者）：storygraph.mapping.json → '
                         f'{spec.tech}')
        return '\n'.join(parts)

    @staticmethod
    def _field_tip(field) -> str:
        parts = [field.hint or field.label]
        if field.tech:
            parts.append(f'技术写法（给开发者）：{field.tech}')
        return '\n'.join(p for p in parts if p)

    def problems(self) -> list:
        return self._edit.problems()
    def counts(self) -> dict:
        return self._edit.counts()

    def blank_rows(self) -> int:
        return self._edit.blank_rows()

    def cell_text(self, row: int, column: int) -> str:
        """表里那一格显示的文字（测试 / 复制用）。"""
        item = self._table.item(row, column)
        return item.text() if item is not None else ''

    def cell_value(self, row: int, column: int) -> str:
        """
        那一格**写回文件**的值（下拉列是 `bg` 而不是屏幕上的「背景」）。

        为什么要有两个：屏幕上要人话、文件里要真值；只给 `cell_text`
        的测试会把「背景」当成写进 JSON 的东西，看起来"对"，实际是错的。
        """
        item = self._table.item(row, column)
        if item is None:
            return ''
        if column == 0:
            return item.text()
        field = ME.SPECS[self._section].fields[column - 1]
        return self._cell_value(field, item)

    def row_count(self) -> int:
        return self._table.rowCount()

    # ------------------------------------------------------------------
    #  动作
    # ------------------------------------------------------------------

    def add_row(self) -> int:
        index = self._edit.add(self._section)
        if index < 0:
            self.note.emit('这一段在文件里的写法不对，先到「原始文件」页改对。',
                           True)
            return -1
        self._rebuild(select=[index])
        self._emit_changed()
        self.note.emit('已加一行 —— 填第一列和后面的格子；空着不填不会写进文件。',
                       False)
        return index

    def delete_selected(self) -> int:
        rows = self._selected_rows()
        if not rows:
            self.note.emit('先在表里选中要删的行。', True)
            return 0
        removed = self._edit.remove(self._section, rows)
        self._rebuild(select=[])
        self._emit_changed()
        self.note.emit(f'已删掉 {removed} 条（还没保存 —— 「保存到项目根」才落盘）。',
                       False)
        return removed

    def move_selected(self, delta: int) -> int:
        if not ME.SPECS[self._section].ordered:
            self.note.emit('只有「规则」这一段的顺序有意义（先中先得），'
                           '其它段的上移下移只是好看。', True)
            return -1
        rows = self._selected_rows()
        if len(rows) != 1:
            self.note.emit('选中一行再上移 / 下移。', True)
            return -1
        new_index = self._edit.move(self._section, rows[0], delta)
        self._rebuild(select=[new_index])
        self._emit_changed()
        return new_index

    def pick_image(self) -> str:
        """选一张图，把它的**项目内相对路径**填进第一列。返回填进去的路径。"""
        start = self._root or ''
        path, _ = QFileDialog.getOpenFileName(
            self, '选一张图', start,
            '图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;所有文件 (*)')
        if not path:
            return ''
        rel = self.relative_path(path)
        if not rel:
            self.note.emit(
                '这张图不在项目目录里 —— 映射里的路径必须写项目相对的，'
                '否则换台机器就失效。', True)
            return ''
        if self._section != 'images':
            self.set_section('images')
        rows = self._selected_rows()
        if len(rows) == 1:
            row = rows[0]
            current = self.cell_text(row, 0)
            if not current or current == ME.SPECS['images'].auto_key:
                self.set_cell(row, 0, rel)
                return rel
        index = self._edit.add('images', rel)
        self._rebuild(select=[index] if index >= 0 else [])
        self._emit_changed()
        self.note.emit(f'已加一条：{rel}', False)
        return rel

    def relative_path(self, path) -> str:
        """绝对路径 → 项目内相对路径（POSIX 斜杠）；不在项目里返回 `''`。"""
        text = str(path or '').strip()
        if not text or not self._root:
            return ''
        try:
            rel = os.path.relpath(os.path.abspath(text),
                                  os.path.abspath(self._root))
        except (OSError, ValueError):
            return ''
        if rel.startswith('..'):
            return ''
        return rel.replace(os.sep, '/')

    def add_images(self, rows) -> int:
        """
        「把对照表选中的图补进映射」：`rows` 是 `[{key, kind, char, attr}, ...]`。

        键已存在的**并进去**（同一张图点两次不会出现两条）—— 返回**新加**
        的条数（"就地更新"的不算，但界面会说一句有几条已经在了）。
        """
        keys = {row.key for row in self._edit.rows('images')}
        added = merged = 0
        for row in rows or []:
            key = str((row or {}).get('key') or '').strip()
            if not key:
                continue
            fields = {k: v for k, v in row.items()
                      if k != 'key' and str(v or '').strip()}
            index = self._edit.add('images', key, fields)
            if index < 0:
                continue
            if key in keys:
                merged += 1
            else:
                added += 1
                keys.add(key)
        if added or merged:
            self._section = 'images'
            self._list.setCurrentRow(ME.SECTIONS.index('images'))
            last = len(self._edit.rows('images')) - 1
            self._rebuild(select=[last] if last >= 0 else [])
            self._emit_changed()
            tail = f'（{merged} 条本来就有，就地更新了）' if merged else ''
            labels = ' / '.join(f.label for f in ME.SPECS['images'].fields)
            self.note.emit(f'已补进 {added} 条{tail} —— 检查一下「{labels}」，'
                           f'再点「保存到项目根」。', False)
        return added

    # -- 「角色」段：资源表里已经有谁（说清楚 + 想抄再抄） ------------------

    def _can_import_characters(self, spec, rows) -> bool:
        """只有「角色」段空着、而且资源表里真读到角色时，才摆那个按钮。"""
        return bool(spec.name == 'characters' and not rows and self._known)

    def _import_tip(self) -> str:
        count = len(self._known)
        sample = '、'.join(_char_names(self._known))
        where = f'（{sample}…）' if sample else ''
        return (f'把资源表里那 {count} 个角色{where}照抄成一行一个。\n'
                '抄进来以后「这一段说的算」：以后资源表里再改名字，这里不会'
                '自动跟着变（想跟，就把那行删掉；空着 = 照资源表）。')

    def _empty_text(self, spec) -> str:
        """
        这一段空着时那句话。

        用户 2026-11-01 的困惑（原话）：

        > 为什么角色是0……？除了服装代号和表情编号外什么都没有……？？
        > 它不能自己联动xlsx中的资源管理表格进行标明吗……？

        所以「角色」段空着时得**当场说清三件事**：表里有谁、空着为什么不
        影响使用、什么时候才需要写。别的段照 `spec.empty` 原样。
        """
        if spec.name != 'characters' or not self._known:
            return spec.empty
        count = len(self._known)
        names = _char_names(self._known)
        who = ''
        if names:
            who = '（' + '、'.join(names) + ('…）' if count > len(names) else '）')
        return (f'这一段空着就行 —— 资源表里已经有 {count} 个角色{who}，'
                f'界面上用的就是这些名字。只有想改谁的名字、或者给谁补个'
                f'头像，才在这里写一行：写了就以这里为准。')

    def apply_characters(self, chars) -> dict:
        """
        把"资源表里认得出的角色"并进「角色」段（**只加不改**）。

        `chars` 是 `{tag: 显示名}` 或 `{tag: {'display','aliases','avatar'}}`
        （`known_characters` 的结果）。返回
        `{'added': 新加几条, 'kept': 表里已有的, 'blank': 没什么可抄的}`。

        为什么要"抄"而不是"每次打开就自动写"：这一段是**覆盖层**。留空时
        名字照样从资源表来（`identity_of` 一直在用）；一旦写进映射，资源表再
        改名字就盖不动它了。所以抄是人的决定，不是开窗的副作用。
        """
        counts = {'added': 0, 'kept': 0, 'blank': 0}
        if not self._edit.editable('characters'):
            return counts
        have = {row.key for row in self._edit.rows('characters')}
        for tag in (chars or {}):
            name = str(tag or '').strip()
            if not name:
                continue
            if name in have:
                counts['kept'] += 1
                continue
            fields = _char_fields(chars[tag], name)
            if not fields:
                # 名字就是写法本身、又没别名 / 头像 —— 写进去也没有信息量。
                counts['blank'] += 1
                continue
            if self._edit.add('characters', name, fields) < 0:
                continue
            have.add(name)
            counts['added'] += 1
        if counts['added']:
            if self._section == 'characters':
                self._rebuild()
            else:
                # 左栏的条数也得跟上（不加这句，抄完还写着「角色 0」）。
                self._fill_sections()
            self._emit_changed()
        return counts

    def import_characters(self, chars=None) -> dict:
        """「把资源表里的角色写进来」按钮：照抄一遍，然后**说清代价**。"""
        source = self._known if chars is None else chars
        if not source:
            self.note.emit('资源表里没读到角色 —— 没有可抄的。', True)
            return {}
        counts = self.apply_characters(source)
        if not counts.get('added'):
            self.note.emit(
                '没有新抄的：表里已经有了，或者名字和剧本写法一样（那几个不用抄）。',
                True)
            return counts
        skipped = []
        if counts.get('kept'):
            skipped.append(f'{counts["kept"]} 个表里已有')
        if counts.get('blank'):
            skipped.append(f'{counts["blank"]} 个名字就是写法本身')
        self.note.emit(
            f'从资源表抄进来 {counts["added"]} 个角色'
            + (f'（{"，".join(skipped)}，没动）' if skipped else '')
            + ' —— 还没保存。抄进来以后这一段优先：资源表里再改名字，'
              '这里不会跟着变。', False)
        return counts

    # -- 代号两段：自动建表 + 换算 ---------------------------------------

    def apply_codes(self, scan) -> dict:
        """
        把一次 `sprite_codes.scan_project` 的结果并进表里（**只加不改**）。

        已有的同一角色同一代号**跳过**（人可能已经改过它）；返回
        `{'outfits': 新加几条, 'expressions': 新加几条, 'kept': 已有几条}`。

        为什么是"并"不是"覆盖"：这张表是人会手改的（美术需求表就是权威），
        自动填只该补空洞，不该把人写的字母冲掉。
        """
        counts = {'outfits': 0, 'expressions': 0, 'kept': 0}
        rows_by_section = SC.template_rows(scan)
        for section, key in ((SC.SECTION_OUTFITS, 'outfits'),
                             (SC.SECTION_EXPRESSIONS, 'expressions')):
            if not self._edit.editable(section):
                continue
            have = {_code_identity(section, e)
                    for e in self._edit.entries(section)}
            for row in rows_by_section.get(section) or ():
                identity = _code_identity(section, row)
                if identity in have:
                    counts['kept'] += 1
                    continue
                index = self._edit.add(section, '', dict(row))
                if index < 0:
                    continue
                have.add(identity)
                counts[key] += 1
        if counts['outfits'] or counts['expressions']:
            if self._section in (SC.SECTION_OUTFITS, SC.SECTION_EXPRESSIONS):
                self._rebuild()
            else:
                # 左栏的条数必须在**当前这一段**也跟上：否则刚"自动建表"完，
                # 左栏还写着「服装代号 0」，人会以为没建出来。
                self._fill_sections()
            self._emit_changed()
        return counts

    def apply_vocabulary(self, scan) -> dict:
        """
        把一次 `code_tables.scan_project` 的结果并进「中文叫法」那段（**只加不改**）。

        为什么叫"中文叫法"而不是"音乐编号"：那段本来就是一个**通用**的
        "引擎写法 → 人话 + 编号"表（`kind` 决定它管哪类资产）。音乐只是
        第一个能自动读出来的 —— 以后哪个目录里放一张 `对应.txt`，这里就
        多一类，不用再加一段。

        同一件资产（`kind` + `id`）已经有一条就跳过：人可能已经改过它，
        或者它是上一次自动填出来的。返回 `{'added': 新加几条, 'kept': 已有
        几条, 'notes': [...]}`。
        """
        counts = {'added': 0, 'kept': 0, 'notes': list(scan.notes)}
        if not self._edit.editable('vocabulary'):
            counts['notes'].append(
                '「中文叫法」这段结构不对（应为数组），没往里写 —— 先去'
                '「原始文件」页修好。')
            return counts
        have = {_vocab_identity(entry) for entry in
                self._edit.entries('vocabulary')}
        for row in scan.rows or ():
            identity = _vocab_identity(row)
            if identity in have:
                counts['kept'] += 1
                continue
            index = self._edit.add('vocabulary', '', dict(row))
            if index < 0:
                continue
            have.add(identity)
            counts['added'] += 1
        if counts['added']:
            if self._section == 'vocabulary':
                self._rebuild()
            else:
                # 左栏的「中文叫法 N」也得跟上（与代号两段同一条规矩）。
                self._fill_sections()
            self._emit_changed()
        return counts

    def autofill_codes(self, *, codes: bool = True,
                       vocabulary: bool = True) -> dict:
        """
        「按项目自动填」：扫一遍项目，把该补的补上（数不出来的留空）。

        `codes` = 服装代号 / 表情编号两段（`sprite_codes`：角色文件夹的
        `对应.txt` + 剧本注释）；`vocabulary` = 「中文叫法」那段里的编号行
        （`code_tables`：**任何**目录里的 `对应.txt`，例如
        `game/bgm/对应.txt` ↔ 剧本里的 `# bgm13`）。

        两件事分开判定：表情那两段已经有人填过，不代表音乐编号也该跳过 ——
        反过来也一样。两个开关只由调用方（开窗那一次）决定，手动点按钮
        时两个都开。
        """
        counts = {'outfits': 0, 'expressions': 0, 'kept': 0,
                  'vocabulary': 0, 'vocab_kept': 0}
        notes = []
        if not self._root:
            self.note.emit('还没有打开项目 —— 不知道去哪儿数代号。', True)
            return {}
        if codes:
            counts.update(self.apply_codes(SC.scan_project(self._root)))
        if vocabulary:
            got = self.apply_vocabulary(CT.scan_project(self._root))
            counts['vocabulary'] = got['added']
            counts['vocab_kept'] = got['kept']
            notes = got['notes']
        if not (counts['outfits'] or counts['expressions']
                or counts['vocabulary']):
            said = ('没数出新代号：表里已经有了，或项目里没有对应.txt / '
                    '剧本注释（# 表情B7、# bgm13）可数。')
            if notes:
                said += ' 项目里还有：' + notes[0]
            self.note.emit(said, True)
            return counts
        said = (f'照项目数了一遍：服装代号 +{counts["outfits"]}，'
                f'表情编号 +{counts["expressions"]}，'
                f'中文叫法 +{counts["vocabulary"]}')
        kept = counts['kept'] + counts.get('vocab_kept', 0)
        if kept:
            said += f'（{kept} 条本来就有，没动）'
        said += '。核对一下再「保存到项目根」。'
        if notes:
            said += ' 要你看一眼：' + '；'.join(notes[:2])
            if len(notes) > 2:
                said += f'（还有 {len(notes) - 2} 条）'
        self.note.emit(said, False)
        return counts

    def _update_code(self):
        """换算框：边打字边算，算不出来就说缺什么。"""
        data = self.current_data()
        text = self._code_input.text().strip()
        if not text:
            chars = SC.chars_in(data)
            who = chars[0] if chars else 'lan'
            self._code_out.setText(
                f'例：{who} swim_suit angry  →  代号 {who} B7；'
                f'反过来写 {who} B7 也行。')
            self._code_out.setProperty('role', 'note')
            _repolish(self._code_out)
            return
        reading = SC.translate(text, data)
        if reading.ok:
            code = f'{reading.char} {reading.code}'.strip()
            # 代号加粗：一眼能看到"B8 这个结果"，其余是解释。
            self._code_out.setText(
                f'代号：<b>{code}</b>　｜　剧本里写：'
                f'<span style="font-family:Consolas,\'Courier New\',monospace">'
                f'{reading.show}</span>')
            self._code_out.setProperty('role', 'note')
        else:
            self._code_out.setText(f'还差一点：{reading.note}')
            self._code_out.setProperty('role', 'warn')
        _repolish(self._code_out)

    def set_cell(self, row: int, column: int, text) -> str:
        """
        按行/列改一格（测试与代码调用走这里；返回错误文字）。

        `text` 给的是**文件里的值**（`bg`），不是屏幕上的「背景」—— 和
        `cell_value()` 同一个口径；显示文字由这里翻。
        """
        if column == 0:
            error = self._edit.set_key(self._section, row, text)
        else:
            field = ME.SPECS[self._section].fields[column - 1]
            error = self._edit.set_field(self._section, row, field.key, text)
        if error:
            return error
        # 程序调用也要让**格子**跟上模型（否则显示的是改之前的值，直到下次重建）。
        # ⚠ `setData(UserRole, …)` 同样会发 `itemChanged` —— 整段必须压在
        # `_filling` 底下，否则 `_on_item_changed` 会拿旧文字再写一遍模型，
        # 刚改的值当场被抹掉（实测：`set_cell(1, 1, '…')` 返回成功但数据没变）。
        rows = self._edit.rows(self._section)
        if 0 <= row < len(rows):
            item = self._table.item(row, column)
            raw = None
            if column == 0:
                value = rows[row].key
            else:
                field = ME.SPECS[self._section].fields[column - 1]
                raw = rows[row].values[column - 1]
                value = self._cell_text(field, raw)
            if item is not None:
                self._filling = True
                try:
                    if raw is not None:
                        item.setData(Qt.ItemDataRole.UserRole, str(raw))
                    if item.text() != str(value):
                        item.setText(str(value))
                finally:
                    self._filling = False
        self._refresh_problems()
        self._emit_changed()
        return ''

    # ------------------------------------------------------------------
    #  内部
    # ------------------------------------------------------------------

    def _fill_sections(self):
        counts = self._edit.counts()
        current = self._section
        self._list.blockSignals(True)
        try:
            self._list.clear()
            for name in ME.SECTIONS:
                spec = ME.SPECS[name]
                item = QListWidgetItem(f'{spec.label}  {counts.get(name, 0)}')
                item.setData(Qt.ItemDataRole.UserRole, name)
                item.setToolTip(self._section_tip(spec))
                self._list.addItem(item)
            self._list.setCurrentRow(ME.SECTIONS.index(current))
        finally:
            self._list.blockSignals(False)

    def _rebuild(self, select=None):
        spec = ME.SPECS[self._section]
        rows = self._edit.rows(self._section)
        wanted = list(select or [])
        self._filling = True
        try:
            self._table.clearContents()
            # 末尾多一列**空列**（吃完剩余宽度的那个"填充位"）。
            self._table.setColumnCount(2 + len(spec.fields))
            self._table.setHorizontalHeaderLabels(
                [spec.key_label] + [f.label for f in spec.fields] + [''])
            # 表头也要能问"这一列到底填什么"：悬停给一句人话 + 技术写法。
            for column, tip in enumerate(
                    [self._section_tip(spec)]
                    + [self._field_tip(f) for f in spec.fields] + ['']):
                header_item = self._table.horizontalHeaderItem(column)
                if header_item is not None:
                    header_item.setToolTip(tip)
            self._table.setRowCount(len(rows))
            for column in range(1 + len(spec.fields)):
                self._table.horizontalHeader().setSectionResizeMode(
                    column, QHeaderView.ResizeMode.Interactive)
            # 每列定宽、**不伸缩**：伸缩不管选哪一列，窗口一宽就会有一列被拉成
            # 六七百像素的空格子（规则段伸缩第一列更糟：`#` 被拉成 380px）。
            # 剩下的空白留在表格右侧，和「资源对照表」同一个做法。
            self._table.setColumnWidth(0, 44 if spec.ordered else KEY_WIDTH)
            for column, field in enumerate(spec.fields, start=1):
                self._table.setColumnWidth(column, field.width)
                self._table.setItemDelegateForColumn(
                    column, self._delegate_for(field))
            problems = {}
            for problem in self._edit.problems():
                if problem.section == self._section:
                    problems.setdefault(problem.index, []).append(problem)
            for index, row in enumerate(rows):
                self._fill_row(index, row, problems.get(index, ()))
            self._table.setCurrentCell(-1, -1)
            for index in wanted:
                if 0 <= index < self._table.rowCount():
                    for column in range(self._table.columnCount()):
                        item = self._table.item(index, column)
                        if item is not None:
                            item.setSelected(True)
                    # 新加的行必须**看得见**：不然"已补进 1 条"和屏幕对不上。
                    self._table.scrollToItem(self._table.item(index, 0))
            # **当前行**也要落在那一行上（`setSelected` 只改选中态：`currentRow()`
            # 还是 -1，键盘/焦点都不在那儿 —— 从卡片警告点进来时这条最要紧）。
            if wanted:
                last = wanted[-1]
                if 0 <= last < self._table.rowCount():
                    self._table.setCurrentCell(last, 1)
                    self._table.scrollToItem(self._table.item(last, 0))
        finally:
            self._filling = False
        self._hint.setText(spec.hint)
        # 代号两段才摆换算框（别的段落里它没有意义）。
        codes = self._section in (SC.SECTION_OUTFITS, SC.SECTION_EXPRESSIONS)
        self._code_box.setVisible(codes)
        self._what.setText(WHAT_IS_CODES if codes else WHAT_IS_THIS)
        if codes:
            self._update_code()
        empty = not rows
        self._empty.setText(self._empty_text(spec) if empty else '')
        self._empty.setVisible(bool(empty and self._empty.text()))
        self._btn_import.setVisible(self._can_import_characters(spec, rows))
        self._btn_import.setToolTip(self._import_tip())
        self._btn_pick.setVisible(self._section == 'images')
        self._btn_up.setEnabled(spec.ordered)
        self._btn_down.setEnabled(spec.ordered)
        self._refresh_problems()
        self._fill_sections()

    def _fill_row(self, index: int, row, problems):
        spec = ME.SPECS[self._section]
        block = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        key_text = str(index + 1) if spec.ordered else row.key
        key_item = QTableWidgetItem(key_text)
        key_item.setFlags(block if (spec.ordered or row.broken)
                          else block | Qt.ItemFlag.ItemIsEditable)
        tips = [f'第 {index + 1} 条']
        if spec.ordered:
            tips = ['从上往下、先对上的算数：排在下面的，被上面盖住就不会生效。']
        else:
            # 这一列可能是很长的相对路径（列宽不够会截断），tooltip 里给全文。
            tips.append(f'{spec.key_label}：{row.key}')
        if row.unknown:
            tips.append('这条里还有些工具不认识的内容，会原样保留：'
                        + '、'.join(str(k) for k in row.unknown))
        if row.broken:
            tips.append('这一条在文件里被写成了短写法，只能在「原始文件」页改。')
        if problems:
            tips.extend(f'⚠ {p.text}' for p in problems)
        key_item.setToolTip('\n'.join(tips))
        if problems:
            level = 'bad' if any(p.level == 'bad' for p in problems) else 'warn'
            key_item.setForeground(QBrush(QColor(
                theme.t('state.error' if level == 'bad' else 'state.warning'))))
        self._table.setItem(index, 0, key_item)
        for column, (field, value) in enumerate(zip(spec.fields, row.values),
                                                start=1):
            item = QTableWidgetItem(self._cell_text(field, value))
            # 下拉列：屏幕上是「背景」，文件里是 `bg` —— 真值存在 UserRole 里，
            # 改完回写用它（不然会把「背景」两个字写进 JSON）。
            item.setData(Qt.ItemDataRole.UserRole, str(value))
            item.setFlags(block if row.broken
                          else block | Qt.ItemFlag.ItemIsEditable)
            item.setToolTip(self._field_tip(field))
            self._table.setItem(index, column, item)

    def _cell_text(self, field, value) -> str:
        """一格**显示**的文字（下拉列翻成人话；文件里认不出的值原样显示）。"""
        raw = str(value if value is not None else '')
        if field.kind == 'choice' and raw:
            return ME.choice_text(field, raw) or KIND_TEXT.get(raw, raw)
        return raw

    def _cell_value(self, field, item) -> str:
        """一格**写回文件**的值（下拉列用 UserRole 里那个真值）。"""
        if field.kind == 'choice':
            raw = item.data(Qt.ItemDataRole.UserRole)
            return str(raw if raw is not None else item.text())
        return item.text()

    def _delegate_for(self, field):
        if field.kind == 'choice':
            return _ChoiceDelegate(field.choices or KIND_CHOICES, self._table)
        if field.key == 'char':
            return _SuggestDelegate(self._char_suggestions(), self._table)
        return None

    def _char_suggestions(self) -> list:
        """`角色` 列的建议：映射里的角色 + 资源表里的角色（只做建议）。"""
        tags = set(self._known) | set(self._mapping_characters())
        tags |= {'<tag>', '<char>'}
        return sorted(str(t) for t in tags if str(t).strip())

    def _mapping_characters(self) -> list:
        container = self._edit.section_data('characters')
        return list(container) if isinstance(container, dict) else []

    def _on_section_changed(self, row: int):
        if self._filling or row < 0 or row >= len(ME.SECTIONS):
            return
        self._section = ME.SECTIONS[row]
        self._rebuild()

    def _on_item_changed(self, item):
        if self._filling or item is None:
            return
        index, column = item.row(), item.column()
        rows = self._edit.rows(self._section)
        if not 0 <= index < len(rows):
            return
        if column == 0:
            old = rows[index].key
            error = self._edit.set_key(self._section, index, item.text())
            if error:
                self._revert(item, rows, index, column)
                self.note.emit(error, True)
                return
            if self._section == 'characters':
                hits = self._edit.count_references('characters', old)
                if hits:
                    self.note.emit(
                        f'写法改成了「{item.text().strip()}」，但还有 {hits} 条'
                        f'图片和一类图片里写着老写法「{old}」—— 要一起改的话在'
                        f'那两段里改。', False)
        else:
            field = ME.SPECS[self._section].fields[column - 1]
            error = self._edit.set_field(self._section, index, field.key,
                                         self._cell_value(field, item))
            if error:
                self._revert(item, rows, index, column)
                self.note.emit(error, True)
                return
            # 下拉列改完要把格子翻回人话（不然屏幕上留着 `bg`）。
            shown = self._cell_text(field, self._cell_value(field, item))
            if shown != item.text():
                self._filling = True
                try:
                    item.setText(shown)
                finally:
                    self._filling = False
        self._refresh_problems()
        self._fill_sections()
        self._emit_changed()

    def _revert(self, item, rows, index: int, column: int):
        """改不成功时把格子恢复成模型里的值（界面不能和模型不一致）。"""
        row = rows[index]
        spec = ME.SPECS[self._section]
        if column == 0:
            value = row.key
        else:
            field = spec.fields[column - 1]
            value = self._cell_text(field, row.values[column - 1])
        self._filling = True
        try:
            if column != 0:
                field = spec.fields[column - 1]
                item.setData(Qt.ItemDataRole.UserRole,
                             str(row.values[column - 1]))
            item.setText(str(value))
        finally:
            self._filling = False

    def _selected_rows(self) -> list:
        indexes = {item.row() for item in self._table.selectedItems()
                   if item is not None}
        return sorted(indexes)

    def _refresh_problems(self):
        problems = self._edit.problems()
        lines = []
        bad = [p for p in problems if p.level == 'bad']
        for problem in problems[:MAX_PROBLEMS]:
            mark = '✗' if problem.level == 'bad' else '⚠'
            where = ME.SPECS[problem.section].label
            if problem.index >= 0:
                where = f'{where} 第 {problem.index + 1} 条'
            lines.append(f'{mark} {where}：{problem.text}')
        rest = len(problems) - len(lines)
        if rest > 0:
            lines.append(f'……还有 {rest} 条（切到那一段能看见标色）')
        blank = self._edit.blank_rows()
        if blank:
            lines.append(f'（空着的 {blank} 行不会写进文件）')
        self._problems.setText('\n'.join(lines))
        self._problems.setProperty('role', 'warn' if bad else 'note')
        _repolish(self._problems)

    def _emit_changed(self):
        self._refresh_problems()
        # 换算框跟着表实时算：改了一个字母，上面那句"B7"要当场变。
        if self._section in (SC.SECTION_OUTFITS, SC.SECTION_EXPRESSIONS):
            self._update_code()
        self.changed.emit()


def _code_identity(section: str, row) -> tuple:
    """
    代号两段里"这是同一条"的判据：角色 + 代号（没有代号就用剧本里的写法）。

    只用来**跳过已经有的**（自动填不覆盖人写的东西），不参与任何保存判定。
    """
    char = str((row or {}).get('char') or '').strip()
    if section == SC.SECTION_OUTFITS:
        code = str((row or {}).get('code') or '').strip().upper()
    else:
        code = str((row or {}).get('no') or '').strip()
    return (char, code or 'attr:' + str((row or {}).get('attr') or '').strip())


def _vocab_identity(row) -> tuple:
    """
    「中文叫法」里"这是同一条"的判据：种类 + 原名（剧本里那个写法）。

    与 `Vocabulary` 的键同口径（一条词 = 种类 + 写法）：`bgm/嗯？.mp3`
    与 `bgm\\嗯？.mp3` 是同一个文件，斜杠先统一。
    """
    kind = str((row or {}).get('kind') or '').strip().lower()
    ident = str((row or {}).get('id') or '').strip().replace('\\', '/')
    return (kind, ident)


def _repolish(widget):
    """`role` 变了要重新 polish，QSS 才会换色。"""
    try:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
    except Exception as e:                 # noqa: BLE001 - 样式没装就当没事
        swallow('ui_qt.mapping_editor._repolish', e)
