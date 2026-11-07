# -*- coding: utf-8 -*-
"""
卡片右栏的**行构造口** —— Unity Inspector 那套排布的唯一实现
================================================================

用户 2026-11-07：

> 卡片编辑器右边的UI和自定义模块定义出的模块一定要对应！UI 要重构……按 Unity 方案

## 抄的是哪三条（调研 2 的结论）

  · **一行一条控件**：Unity 的 Inspector 是**架构上的**"一行一个字段"，不是风格
    （`PropertyDrawer.OnGUI(Rect, SerializedProperty, GUIContent)` 只拿到一块矩形，
    你画什么都可以，但影响不了下一行的位置）；
  · **标签宽度是全局常量**：`EditorGUIUtility.labelWidth` —— 所有行的标签右对齐到
    **同一条竖线**，控件从同一条竖线开始。这是"整齐感"的物理来源；
  · **组标题 / 留白 / 缩进**：Unity 用 `DecoratorDrawer` 画 `[Header]` 和
    `[Space(10)]`，用 `EditorGUI.indentLevel` 表层级 —— **呼吸感是声明出来的**。

## 为什么要单独一个模块

改之前，`card_editor` 里每个字段行都是**各处自己拼 `QHBoxLayout`**：标签有的
`setMinimumWidth(72)`、有的没设、有的干脆没标签。于是"那条竖线"根本不存在，
一屏看下来参差 —— 这就是用户说的"折磨"里最难说清的那一份。

从现在起：**任何字段行都走 `row()`，`LABEL_WIDTH` 只有一份。**

## 纪律

  · 只负责**排布**：不碰值、不碰 `_widgets`（那是卡片的事）；
  · `row()` 返回 **QWidget**（竖着堆），不返回布局 —— 布局没法 `setVisible`，
    左栏"这一块收起来"的判据（`isHidden()`）就写不出来；
  · 缩进用**像素**（`INDENT_PX`），不用"打几个空格"那种做法；
  · `gap()` / `note()` 对应 Unity 的 `[Space]` 与 `InfoBox`：**要留白就声明**，
    不要在调用处随手挑一个 `T.SPACE['sm']`。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from storygraph.ui_qt import tokens as T

#: 标签列宽度 —— 对应 `EditorGUIUtility.labelWidth`。
#:
#: 88 是量出来的：现有最长的标签是「台词后缀」「当前舞台」（4 个汉字），
#: 再留一点给「（可空）」这种尾巴。**改它要跑判据**（`test_card_rows.py` 钉了
#: "所有行的标签宽都一样"这件事，不只是等于这个常量）。
LABEL_WIDTH = 88

#: 一级缩进多少像素 —— 对应 `EditorGUI.indentLevel++` 的视觉量。
INDENT_PX = 16

#: 行与行之间的基础间距（Unity 里是 `EditorGUIUtility.standardVerticalSpacing`）。
ROW_GAP = 6

#: **并排**两格时用的窄标签宽（音乐 + 语音那一行）。
#:
#: 为什么允许窄的：两个 88px 的标签会把半屏吃掉，而「音乐 / 语音」是两个字。
#: 这不是"破坏统一"—— 常量仍然只有这里一份，只是两个合法的档位。
NARROW_LABEL_WIDTH = 48


def row(label, widget, *, indent: int = 0, label_width: int = None,
        stretch: int = 1, trailing=None, lead=(), tail=(),
        label_tip: str = '') -> QWidget:
    """
    **一行一个字段**：左标签（右对齐、固定宽）+ 右控件（吃掉剩余宽度）。

    `label` 可以是空串 —— 那就只放控件、但**仍然占住标签列**（保持竖线连续）。
    这一点很重要：Unity 里没有标签的字段（比如数组的元素行）也是从同一条
    竖线开始的，所以整列看起来是一条线，而不是"有的缩进有的不缩进"。

    `stretch=0` 表示控件按自身宽度靠左（下拉这种不该拉满的用它）。

    `trailing` / `tail` 是行尾的控件（「⋯」、多值提示…），`lead` 是控件**之前**
    的（色块）。**"下拉 + ⋯" 只有 `picker_row()` 一处实现** —— 舞台三格和
    语句参数格共用它，别再各写一遍。
    """
    width = LABEL_WIDTH if label_width is None else int(label_width)
    host = QWidget()
    lay = QHBoxLayout(host)
    lay.setContentsMargins(int(indent) * INDENT_PX, 0, 0, 0)
    lay.setSpacing(T.SPACE['sm'])
    lab = QLabel(str(label or ''))
    lab.setProperty('role', 'caption')
    lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    lab.setFixedWidth(width)
    if label_tip:
        lab.setToolTip(str(label_tip))
    lay.addWidget(lab, 0)
    for extra in (lead or ()):
        lay.addWidget(extra, 0)
    lay.addWidget(widget, int(stretch))
    if stretch <= 0:
        lay.addStretch(1)
    for extra in (tail or ()):
        lay.addWidget(extra, 0)
    if trailing is not None:
        lay.addWidget(trailing, 0)
    #: 这一行的标签（`_fill_inherited_fields` 要改「沿用上一拍」的标题）
    host._label = lab
    #: 打一个"我是 card_rows 造的"标记 —— 护栏判据靠它盘查
    #: （`test_card_rows.py::test_every_field_row_comes_from_card_rows`）。
    #: 没有这个标记，就没法区分"标准行"和"谁又在调用处自己拼了一个布局"。
    host._row_kind = 'row'
    return host


def picker_button(on_pick, hint: str = ''):
    """
    **行尾那个「⋯」按钮的唯一实现**（舞台三格与语句参数格共用）。

    三条坑都写在这一个函数里 —— 从前它们散在调用处，谁想复用得再抄一遍：

      · ⚠ `setStyleSheet('padding:0;')`：默认 QSS 的内边距有十几 px，
        24px 宽的按钮里那个「⋯」会被裁成**一片空白**（offscreen 截图实测过），
        用户看到的是"点了没反应的方块"——正是本项目最怕的形态；
      · ⚠ `clicked` 会带一个 bool：不吞掉的话 `on_pick()` 会收到 `False`，
        而 `_choose_asset(field)` 那样的签名会把它当参数（`test_qt_signal_wiring` 抓过）；
      · 尺寸走 `T.CONTROL['iconW']`（24×24），与资产三格的判据同源。
    """
    from PySide6.QtWidgets import QPushButton

    btn = QPushButton('⋯')
    btn.setFixedSize(T.CONTROL['iconW'], T.CONTROL['iconW'])
    btn.setStyleSheet('padding:0;')
    if hint:
        btn.setToolTip(hint)
    btn.clicked.connect(lambda _checked=False: on_pick())
    return btn


def compact_picker(label, combo, on_pick, *, hint: str = '') -> QWidget:
    """
    **并排专用**的资产格：标签在上、控件在下（省横向宽度）。

    为什么要两个形态（用户 2026-11-07："相似或者相同的功能应当放在一处"）：
    「相似放一处」指的是**实现**只有一份，不是"必须长成一样"。
    `音乐 + 语音` 那一行是两个格子并排（用户 2026-10-04 明确要求），
    若给每一格都加一条固定宽的标签列，卡片最小宽会从 340 涨到 352 ——
    右栏一窄就出横向滚动条（`test_ui_width` 的预算当场抓到）。
    所以并排时标签回到上方；**「⋯」按钮仍然只有 `picker_button()` 一份实现**。

    `host._label` 指向那个标签 —— `_fill_inherited_fields` 要给它加
    「沿用上一拍」的后缀。
    """
    from PySide6.QtWidgets import QHBoxLayout as _HBox
    from PySide6.QtWidgets import QVBoxLayout as _VBox

    host = QWidget()
    outer = _VBox(host)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(2)
    cap = QLabel(str(label or ''))
    cap.setProperty('role', 'caption')
    outer.addWidget(cap)
    line = _HBox()
    line.setSpacing(T.SPACE['xs'])
    line.addWidget(combo, 1)
    line.addWidget(picker_button(on_pick, hint), 0)
    outer.addLayout(line)
    host._label = cap
    host._row_kind = 'compact'
    return host


def picker_row(label, combo, on_pick, *, label_width=None, indent: int = 0,
               hint: str = '') -> QWidget:
    """
    **「下拉 + ⋯」的唯一实现** —— 舞台三格（背景/音乐/语音）与语句参数格共用。

    三条约束都搬到这里，别再散在调用处（用户 2026-11-07：
    "相似或者相同的功能，应当放在一处！不许东一块西一块的"）：

      · ⚠ 下拉必须是 `WheelSafeComboBox`（2026-10-29 用户："不要让滚轮滑动
        调整背景/音乐/语音……太容易误触"）—— 由**调用方**传入，这里只排布；
      · ⚠ 按钮 `setStyleSheet('padding:0;')`：默认 QSS 的内边距有十几 px，
        24px 宽的按钮里那个「⋯」会被裁成**一片空白**（offscreen 截图实测过），
        用户看到的是"点了没反应的方块"；
      · ⚠ `clicked` 会带一个 bool —— 不吞掉的话 `on_pick(False)` 会让
        `_choose_asset(field)` 收到 False（Qt 接线审计会红）。
    """
    return row(label, combo, label_width=label_width, indent=indent,
               trailing=picker_button(on_pick, hint))


def section(text, *, indent: int = 0, hint: str = '') -> QWidget:
    """
    组标题（Unity 的 `[Header("…")]`，可带一句同行说明）。

    和 `card_editor._section()` 的区别：这个会**带缩进**、上下留一点空
    （`DecoratorDrawer` 那种"呼吸"），并且能带一句灰字说明
    （Unity 里是 `[Header]` + `[Tooltip]`，这里做成同行不占高度）。

    `hint` 是为了把「立绘 点 chip 换表情/位置」这种"标题 + 一句提示"的写法
    也收进来 —— 从前它们是 `_section()` + 手拼 `QHBoxLayout`，又是一处自己拼。
    """
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(int(indent) * INDENT_PX, ROW_GAP, 0, 0)
    lay.setSpacing(0)
    line = QHBoxLayout()
    line.setSpacing(T.SPACE['xs'])
    lab = QLabel(str(text or ''))
    lab.setProperty('role', 'section')
    line.addWidget(lab, 0)
    if hint:
        tail = QLabel(str(hint))
        tail.setProperty('role', 'caption')
        line.addWidget(tail, 0)
    line.addStretch(1)
    lay.addLayout(line)
    return host


def gap(height: int = 10) -> QWidget:
    """声明出来的留白 —— Unity 的 `[Space(10)]`。"""
    holder = QWidget()
    holder.setFixedHeight(max(0, int(height)))
    return holder


def note(text, *, indent: int = 0, level: str = 'caption') -> QWidget:
    """
    一行说明 —— Unity 的 `InfoBox` / tooltip 的内联版。

    `level='caption'` 是灰字提示；`level='warn'` 是"这会写坏"的那种。
    **不换行**（`wordWrap` 打开）但也不占整行高度。
    """
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(int(indent) * INDENT_PX, 0, 0, 0)
    lay.setSpacing(0)
    lab = QLabel(str(text or ''))
    lab.setProperty('role', 'warn' if level == 'warn' else 'caption')
    lab.setWordWrap(True)
    lay.addWidget(lab)
    return host


__all__ = ['INDENT_PX', 'LABEL_WIDTH', 'NARROW_LABEL_WIDTH', 'ROW_GAP',
           'compact_picker', 'gap', 'note', 'picker_button', 'picker_row',
           'row', 'section']