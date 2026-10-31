# -*- coding: utf-8 -*-
"""
storygraph.ui_qt.labeled_choices
================================
**「显示值 ≠ 真值」的候选** —— 下拉、可搜索列表、说话人…都走这一层。

为什么要有它（用户 2026-10-31 的六条）：词汇表把那串引擎写法（`i11` /
`bgm/嗯？.mp3` / `bg9_5` / `DY` / `my_wipe`）翻成了人话（`1人，近处` / `18 嗯？`
/ `bg9.2` / `大爷` / `闪白`），但**写进剧本的必须还是引擎写法**。于是每一个
"能选的格子"都要同时记住两件事：

    屏幕上看到：  18 嗯？
    存进 beat：   bgm/嗯？.mp3

Qt 本来就有这个原语 —— `QComboBox.addItem(文本, 数据)` 的第二个参数
（`UserRole`）。所以规矩只有一条：

    **一个下拉/列表的"真值"永远优先取 `UserRole` 里的数据；没有数据才用显示的文本。**

这条规矩放一份，`main_window._get_value` / `set_asset_choices` / `set_speakers` /
`choice_picker` 全调它 —— 各处不再各写一遍"要不要 strip、要不要回退"。

## 三种输入形状（**一个形状只有一种意思**）

`as_choices()` 认这三种，别当别的：

    'bgm/a.ogg'                      → 显示与真值相同（老调用方，一字不变）
    ('bgm/a.ogg', '18 嗯？')          → `(值, 显示)`
    ('bgm/a.ogg', '18 嗯？', '说明')  → `(值, 显示, 提示)` —— 提示挂 tooltip
    Choice(值, 显示, 提示)            → 显式，二义性为零

⚠ **二元组是 (值, 显示)**，不是 `(值, 说明)`。老契约 `set_asset_choices`
那个 `[(值, 说明)]` 仍然照旧（说明进 tooltip），但换算在**它自己那一处**
做完（`Choice(v, '', tip)`）—— 所以这里只留一种解释。2 元组被当成
"值+提示"过一次：屏幕上是英文、中文跑进 tooltip，两边都"有字"，看不出来。

## 与 `assets/vocabulary.py` 的分工

那边管"这串字该叫什么"（纯数据、不碰 Qt）；这边管"怎么把它摆进控件、
怎么把它读回来"。**翻译不在这一层做**（只调 `vocab.label_in_text()`）——
否则同一条词汇会有两份投影。

## 值不在候选里怎么办

- 手写的自由值 → 原样写进输入框，`combo_value` 原样读回来（老承诺）；
- **认得出来该叫什么**（`[音乐] night fadein 1.0` 这种复合值）→ 挂一行
  "当前值"（`show_current`），屏幕上是人话、`UserRole` 里还是原串。
  同一时刻只留一行，换成真候选时撤掉。
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox

from storygraph.core.safeguard import swallow

__all__ = ['Choice', 'as_choice', 'as_choices', 'fill_combo', 'combo_value',
           'select_value', 'index_of_value', 'display_of', 'say', 'explain',
           'labeled', 'pairs', 'session_vocabulary', 'legacy_pairs']


@dataclass(frozen=True)
class Choice:
    """一个候选：`value` 是**写进剧本**的那个，`display` 只在屏幕上。"""

    value: str
    display: str = ''
    tip: str = ''

    @property
    def text(self) -> str:
        """屏幕上显示的那串（没给 display 就是原值 —— 规矩 2）。"""
        return self.display or self.value

    def as_dict(self) -> dict:
        return {'value': self.value, 'display': self.text, 'tip': self.tip}


def as_choice(item) -> Choice:
    """一条原始候选 → `Choice`（认不出就把它当"值 = 显示"）。"""
    if isinstance(item, Choice):
        return item
    if isinstance(item, dict):
        value = str(item.get('value', item.get('id', '')) or '')
        return Choice(value, str(item.get('display', '') or ''),
                      str(item.get('tip', '') or ''))
    if isinstance(item, (tuple, list)):
        parts = [str(p if p is not None else '') for p in item]
        if len(parts) == 2:
            return Choice(parts[0], parts[1])       # (值, 显示)
        if len(parts) >= 3:
            return Choice(parts[0], parts[1], parts[2])
        if parts:
            return Choice(parts[0])
        return Choice('')
    return Choice(str(item if item is not None else ''))


def as_choices(items) -> list:
    """一列原始候选 → `[Choice, …]`（**全空白**的丢掉；顺序原样）。"""
    out = []
    for item in items or ():
        choice = as_choice(item)
        if (choice.value or '').strip() or (choice.display or '').strip():
            out.append(choice)
    return out


def display_of(items, value) -> str:
    """这一列候选里，`value` 该显示成什么（找不到就原样返回 `value`）。"""
    want = str(value if value is not None else '').strip()
    for choice in as_choices(items):
        if choice.value == want:
            return choice.text
    return want


def session_vocabulary(session):
    """
    从**会话**取项目词汇表（拿不到 → 空表，界面原样显示引擎写法）。

    为什么是这样一个自由函数而不是两个 mixin 各写一遍：`window_card` 与
    `window_assets` 都要它，而它们之间不该互相调用私有方法（`REQUIRES`
    契约就是为这个立的）。也兜住"会话是测试里的假对象、没有 vocabulary()"。
    """
    from storygraph.assets import vocabulary as V

    getter = getattr(session, 'vocabulary', None)
    if callable(getter):
        try:
            return getter()
        except Exception as e:                 # noqa: BLE001 - 词汇表坏不该拦界面
            swallow('ui_qt.labeled_choices.session_vocabulary', e)
            return V.EMPTY_VOCABULARY
    return V.EMPTY_VOCABULARY


def legacy_pairs(choices) -> list:
    """
    老形状 `[(值, 说明)]` → `[Choice(值, '', 说明)]`（**说明进 tooltip**）。

    整个代码库里**只有这一处**知道"二元组的老含义是说明"。资源表那条链路
    （`session.asset_choices` / `audio_choice_items`）产出的就是老形状，
    在它进入"显示值 ≠ 真值"这一层之前先换算过来 —— 否则 `as_choices` 会把
    说明当成显示值，屏幕上就成了一整句话。
    """
    out = []
    for item in choices or ():
        if isinstance(item, (tuple, list)) and len(item) == 2:
            out.append(Choice(str(item[0] or ''), '', str(item[1] or '')))
        else:
            out.append(item)
    return out


def pairs(values, labels=None) -> list:
    """
    `[值, …]` + `{值: 显示}` → `[Choice(值, 显示), …]`（查不到显示就留空）。

    为什么要这个函数：调用方手上是"一串 tag + 一张名字表"，直接拼成
    `(tag, 名字)` 二元组**本来也对**，但那样每处都要记得顺序；这里是具名的。
    """
    book = labels or {}
    out = []
    for value in values or ():
        raw = str(value if value is not None else '')
        shown = str(book.get(raw, '') or '')
        out.append(Choice(raw, '' if shown == raw else shown))
    return out


def say(vocab, kind, ident, char: str = '') -> str:
    """
    `ident` 在这张词汇表里叫什么（查不到 / 没有词汇表 → 原样返回）。

    `kind` 可以是**一串种类**（`('transform', 'cue')`）：转场那一格既可能是
    资源表里的变换、也可能是词汇表登记的过场调用，一个值只属于其中一种，
    按顺序问下去即可（第一个答上来的算）。

    ⚠ **绝不抛**：词汇表是"锦上添花"，它坏掉不该让一个下拉填不出来
    （`vocabulary.label` 已经保证不抛，这里再兜一层 —— 传进来的
    可能是任何东西，比如测试里的假对象）。
    """
    raw = str(ident if ident is not None else '').strip()
    if not raw or vocab is None:
        return raw
    kinds = (kind,) if isinstance(kind, str) or kind is None else tuple(kind)
    for one in kinds:
        if not one:
            continue
        try:
            # `label_in_text` 还会翻**带壳**的写法（`[音乐] x fadein 1.0`）；
            # 老词表对象没有这个方法时退回 `label`（鸭子类型，不挑食）。
            fn = getattr(vocab, 'label_in_text', None) or vocab.label
            shown = fn(one, raw, char)
        except Exception as e:                 # noqa: BLE001 - 显示层不该炸
            swallow('ui_qt.labeled_choices.say', e)
            continue
        shown = str(shown or '').strip()
        if shown and shown != raw:
            return shown
    return raw


def explain(vocab, kind, ident, char: str = '') -> str:
    """
    这一条"凭什么这么判"的**一句话**（P0-3）：覆盖了谁、原来是什么。

    形如：`transform i11：映射的精确覆盖「1人，近处」覆盖了资源表「i11」`。

    为什么要单独一个函数：`say()` 回答"显示成什么"，`explain()` 回答
    "凭什么"——后者要能贴进 tooltip，而 tooltip 里塞十行没人看。
    **没有覆盖记录就返回空串**（不编；"依据"由 `Identity.source` 那侧回答）。
    """
    raw = str(ident if ident is not None else '').strip()
    if not raw or vocab is None:
        return ''
    kinds = (kind,) if isinstance(kind, str) or kind is None else tuple(kind)
    for one in kinds:
        if not one:
            continue
        try:
            fn = getattr(vocab, 'explain', None)
            if not callable(fn):
                continue
            text = str(fn(one, raw, char) or '').strip()
        except Exception as e:                 # noqa: BLE001 - 说明文字不该拦界面
            swallow('ui_qt.labeled_choices.explain', e)
            continue
        if text:
            return text
    return ''


def labeled(choices, vocab, kind: str, char: str = '') -> list:
    """
    `[(值, 提示)]` / `[值]` → `[Choice(值, 显示, 提示)]`：**翻译在这一层补**。

    为什么不让调用方各写一遍：`set_asset_choices` / `set_speakers` /
    参数列候选三处形状不同，但"查词汇表、把结果显示出来"是同一条规则；
    写三遍就一定会有一处忘了（那处就永远是英文）。

    2026-10-31（P0-3）：调用方没给 tip 时，从词汇表的覆盖记录里补一句
    "这条盖掉了谁" —— 下拉里每一项都能回答"凭什么"。
    """
    out = []
    for choice in as_choices(choices):
        display = choice.display or say(vocab, kind, choice.value, char)
        tip = choice.tip or explain(vocab, kind, choice.value, char)
        out.append(Choice(choice.value,
                          '' if display == choice.value else display,
                          tip))
    return out


# ---------------------------------------------------------------------------
#  下拉（QComboBox）
# ---------------------------------------------------------------------------

def index_of_value(combo: QComboBox, value) -> int:
    """`value`（真值）在第几项；没有 -1。"""
    want = str(value if value is not None else '')
    if not want:
        return -1
    for row in range(combo.count()):
        data = combo.itemData(row)
        if isinstance(data, str) and data == want:
            return row
    return -1


#: 下拉上挂"当前值该怎么显示"的属性名（见 `set_display`）。
_DISPLAY_ATTR = '_sg_display'
#: 临时项（"当前值"那一行）的标记角色 —— 重填 / 换值时先删掉旧的。
_CURRENT_ROLE = Qt.UserRole + 7


def _drop_current(combo: QComboBox) -> None:
    """删掉上次挂的"当前值"那一行（翻页 / 换成真候选时不留残影）。"""
    for row in range(combo.count() - 1, -1, -1):
        if combo.itemData(row, _CURRENT_ROLE):
            combo.removeItem(row)


def set_display(combo: QComboBox, display) -> None:
    """
    告诉这个下拉：**值不在候选里时**屏幕上该显示什么（`display(value) -> str`）。

    为什么挂在控件上而不是面板上：`select_value` 会在很多地方被调
    （`_set_value` 载入一拍、选完候选、`⋯` 挑选），而"这一格属于哪一类词汇"
    只有装候选的那一处知道。挂在控件上，之后谁调 `select_value` 都自动生效
    —— 少一条"要记得转发"的通道。
    """
    try:
        setattr(combo, _DISPLAY_ATTR, display if callable(display) else None)
    except Exception as e:                     # noqa: BLE001 - 挂不上就算了
        swallow('ui_qt.labeled_choices.set_display', e)


def _shown_for(combo: QComboBox, value: str) -> str:
    """这个下拉能把 `value` 显示成人话吗（不能 → 空串）。"""
    fn = getattr(combo, _DISPLAY_ATTR, None)
    if not callable(fn) or not value:
        return ''
    try:
        shown = str(fn(value) or '')
    except Exception as e:                     # noqa: BLE001 - 显示层不该炸
        swallow('ui_qt.labeled_choices.shown_for', e)
        return ''
    return '' if shown == value else shown


def show_current(combo: QComboBox, value) -> bool:
    """
    把 `value` 作为**一行"当前值"**挂进下拉并选中（屏幕上是人话、数据是真值）。

    返回"挂上了吗"。同一时刻只留一行：先删掉上一次那行，否则在章节里翻页
    会越积越多（每拍一个复合值）。
    """
    raw = str(value if value is not None else '')
    shown = _shown_for(combo, raw)
    if not shown:
        return False
    was = combo.blockSignals(True)          # 归还**进来时**的状态（可能已在外层被屏蔽）
    try:
        _drop_current(combo)
        combo.addItem(shown, raw)
        combo.setItemData(combo.count() - 1, 1, _CURRENT_ROLE)
        combo.setCurrentIndex(combo.count() - 1)
    finally:
        combo.blockSignals(was)
    return True


def fill_combo(combo: QComboBox, choices, current=None, *,
               blank: bool = False, display=None) -> None:
    """
    重填一个下拉：**屏幕上是 `display`，数据里是 `value`**。

    保留用户当前正在编辑的值（`current=None` 时现读）—— 手写的自定义值
    不许被重填清空（这是 `set_asset_choices` 一直以来的承诺）。

    `blank=True` 在最前面加一条空候选（说话人那格就是这么用的）。

    `display(value)`（可选）：**当前值不在候选里**时，它说了屏幕上该显示
    什么。给了就把它作为"当前值"那一项挂在最后（`UserRole` 仍是原值），
    于是 `[音乐] night fadein 1.0` 这种复合值也能显示成
    `[音乐] 18 夜 fadein 1.0` —— 显示是翻译过的，**读回去还是一条不差**。
    """
    if display is not None:
        set_display(combo, display)
    keep = combo_value(combo) if current is None else str(current or '')
    items = as_choices(choices)
    was = combo.blockSignals(True)          # 归还**进来时**的状态
    try:
        combo.clear()
        if blank:
            combo.addItem('')
        for choice in items:
            combo.addItem(choice.text, choice.value)
            if choice.tip:
                combo.setItemData(combo.count() - 1, choice.tip, Qt.ToolTipRole)
        # 选不上就退回"原样写输入框"；认得出来时 `select_value` 会挂一行
        # "当前值"让它显示人话（复合值 `[音乐] x fadein 1.0` 就靠这一行）。
        select_value(combo, keep)
    finally:
        combo.blockSignals(was)


def select_value(combo: QComboBox, value) -> bool:
    """
    按**真值**选中一项 → 屏幕上显示它的 `display`；找不到就原样写 `value`。

    返回"是不是在候选里"（`True` = 命中了某一项的 UserRole）。
    """
    raw = str(value if value is not None else '')
    row = index_of_value(combo, raw)
    if row < 0 and raw:
        # 调用方给的也可能是**屏幕上那句**（例如从别处拷来的 `18 嗯？`）——
        # 认得出来就按那一项走，读回去仍是真值。
        row = combo.findText(raw)
    if row >= 0:
        if not combo.itemData(row, _CURRENT_ROLE):
            # 命中的是真候选 → 上一次挂的"当前值"那一行可以撤了（不留残影）
            _drop_current(combo)
            row = index_of_value(combo, raw)
        if row >= 0:
            combo.setCurrentIndex(row)
        return True
    if combo.isEditable():
        # 不在候选里 → 原样写进输入框（手写的自由值必须能过）；
        # 认得出来"这串字该叫什么"时，再挂一行"当前值"让它显示人话。
        combo.setCurrentIndex(-1)
        combo.setEditText(raw)
        show_current(combo, raw)
    else:
        # **不可编辑的下拉不许被清空**（找不到就保持原样，与从前
        # `setCurrentText` 的语义一致：那几格都是"只能选，不能写"的）。
        combo.setCurrentText(raw)
    return False


def combo_value(combo: QComboBox) -> str:
    """
    这个下拉**现在代表什么**（真值；空 = 没选）。

    判定顺序（每一步都有实测理由）：

      1. 当前项有 `UserRole` 字符串 **且输入框里还是它那句显示文本** → 就是它
         （选过下拉 / 程序设过值）；
      2. 输入框里的字**恰好等于某一项的显示文本**且只有一项 → 认那一项
         （用户把 `18 嗯？` 手打回来时不该把它当中文值写进剧本）；
      3. 都没有 → 就是输入框里那串（**手写的自定义值必须能过**）。

    ⚠ 第 1 步**必须同时比文本**：可编辑下拉在用户改字之后
    `currentIndex` 仍可能指着上一项（`setEditText` 实测就是这样），
    只看 index 的话，用户手打的 `bgm/别的.ogg` 会被读成旧值 ——
    直接改错剧本，而且屏幕上看着"就是我刚打的字"。
    """
    row = combo.currentIndex()
    text = (combo.currentText() or '').strip()
    if 0 <= row < combo.count():
        data = combo.itemData(row)
        if isinstance(data, str) and data:
            if not combo.isEditable() or text == (combo.itemText(row) or '').strip():
                return data
    if not text:
        return ''
    hit = [row for row in range(combo.count())
           if (combo.itemText(row) or '').strip() == text]
    if len(hit) == 1:
        data = combo.itemData(hit[0])
        if isinstance(data, str) and data:
            return data
    return text
