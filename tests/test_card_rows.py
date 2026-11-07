# -*- coding: utf-8 -*-
"""
卡片「一行一条」的排布契约（`ui_qt/card_rows.py`）
====================================================

抄的是 Unity Inspector 的三件事（调研 2）：**一行一条**、**标签宽度全局统一**、
**留白/缩进是声明出来的**。这个文件钉住前两件 —— 它们最容易被"随手加一行"
悄悄破坏，而且破坏了也看不出来（只是"有点乱"）。

判据只测**结构**，不数像素（数像素的判据一改配色就红）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.ui_qt import card_rows


def _label_of(host: QWidget) -> QLabel:
    """一行里的那个标签（`row()` 的第一个子控件）。"""
    return host.findChild(QLabel)


def test_row_puts_the_label_at_a_global_width(qapp):
    host = card_rows.row('说话人', QLineEdit())
    lab = _label_of(host)
    assert lab is not None and lab.text() == '说话人'
    assert lab.width() == card_rows.LABEL_WIDTH or \
        lab.minimumWidth() == card_rows.LABEL_WIDTH


def test_row_aligns_the_label_to_the_right(qapp):
    """右对齐是"那条竖线"的来源：标签贴着控件，而不是飘在左边。"""
    host = card_rows.row('台词', QLineEdit())      # 必须留引用：临时对象会被回收
    lab = _label_of(host)
    assert lab.alignment() & Qt.AlignRight


def test_row_returns_a_widget_so_it_can_be_hidden(qapp):
    """必须返回 QWidget —— 布局没法 `setVisible`，左栏"收起来"的判据就写不了。"""
    host = card_rows.row('台词', QLineEdit())
    assert isinstance(host, QWidget)
    host.setVisible(False)
    assert host.isHidden()


def test_empty_label_still_reserves_the_column(qapp):
    """没标签的字段也要占住标签列 —— 否则竖线断掉（Unity 也是这样）。"""
    host = card_rows.row('', QLineEdit())
    lab = _label_of(host)
    assert lab is not None
    assert lab.text() == ''
    assert lab.minimumWidth() == card_rows.LABEL_WIDTH or \
        lab.width() == card_rows.LABEL_WIDTH


def test_indent_is_measured_in_pixels_not_spaces(qapp):
    plain = card_rows.row('x', QLineEdit())
    deep = card_rows.row('x', QLineEdit(), indent=2)
    assert deep.layout().contentsMargins().left() == 2 * card_rows.INDENT_PX
    assert plain.layout().contentsMargins().left() == 0


def test_stretch_zero_keeps_the_widget_from_filling_the_row(qapp):
    """短值（说话人/转场）不该拉满整行 —— 末尾要有一个 spacer 顶住。"""
    host = card_rows.row('说话人', QLineEdit(), stretch=0)
    lay = host.layout()
    last = lay.itemAt(lay.count() - 1)
    assert last is not None and last.spacerItem() is not None


def test_gap_is_a_declared_height(qapp):
    holder = card_rows.gap(14)
    assert holder.height() == 14 or holder.minimumHeight() == 14 or \
        holder.maximumHeight() == 14


def test_section_indents_like_a_unity_header(qapp):
    host = card_rows.section('这一拍', indent=1)
    assert host.layout().contentsMargins().left() == card_rows.INDENT_PX


def test_card_dialogue_rows_use_the_shared_contract(qapp):
    """
    卡片里那三行（说话人 / 台词 / 转场）**必须**走 `card_rows.row()`。

    这条防的是"新加一行时又自己拼 `QHBoxLayout`"：那样标签宽度会各写各的，
    这条判据就是为了让那种改动当场变红（而不是等下次截图才看出来）。
    """
    from storygraph.ui_qt.card_editor import BeatCardEditor

    card = BeatCardEditor()
    try:
        for name in ('Speaker', 'Text', 'SayTail'):
            widget = card._widgets.get(name)
            assert widget is not None, f'{name} 没有控件'
            parent = widget.parentWidget()
            assert isinstance(parent, QWidget)
            lab = parent.findChild(QLabel)
            assert lab is not None and lab.minimumWidth() == card_rows.LABEL_WIDTH \
                or lab.width() == card_rows.LABEL_WIDTH, (
                f'{name} 那一行不是 card_rows.row() 造的（标签宽不等于 LABEL_WIDTH）')
    finally:
        try:
            card.shutdown_stage_thumb()
        except AttributeError:
            pass


# ----------------------------------------------------------------------
#  护栏：**所有**字段行都必须来自 card_rows
# ----------------------------------------------------------------------

def test_every_field_row_comes_from_card_rows(qapp):
    """
    盘查：卡片上每个进 `_widgets` 的字段，它的某层祖先必须是 `card_rows`
    造的行（带 `_row_kind` 标记）。

    这条防的不是今天的代码，是**明天新加的那一行**：从前台词区一个
    `QHBoxLayout`、舞台三格一个 `QVBoxLayout`、高级区又一个、备注/条件各一个——
    每条竖线都不一样，攒起来就是用户说的"折磨"。现在谁再自己拼一个，
    这条判据当场红。
    """
    from storygraph.ui_qt.card_editor import BeatCardEditor

    card = BeatCardEditor()
    try:
        bad = []
        names = list(card._all_names()) + [str(k) for k in card._spec_widgets]
        for name in names:
            widget = card._widgets.get(name)
            if widget is None:
                continue
            node = widget.parentWidget()
            ok = False
            while node is not None and node is not card:
                if getattr(node, '_row_kind', ''):
                    ok = True
                    break
                node = node.parentWidget()
            if not ok:
                bad.append(name)
        assert not bad, f'这些字段行不是 card_rows 造的（会各写各的宽度）：{bad}'
    finally:
        card.shutdown_stage_thumb()


# ----------------------------------------------------------------------
#  护栏：**所有**字段行都必须来自 card_rows
# ----------------------------------------------------------------------

def test_every_field_row_comes_from_card_rows(qapp):
    """
    盘查：卡片上每个进 `_widgets` 的字段，它的某层祖先必须是 `card_rows`
    造的行（带 `_row_kind` 标记）。

    这条防的不是今天的代码，是**明天新加的那一行**：从前台词区一个
    `QHBoxLayout`、舞台三格一个 `QVBoxLayout`、高级区又一个、备注/条件各一个——
    每条竖线都不一样，攒起来就是用户说的"折磨"。现在谁再自己拼一个，
    这条判据当场红。
    """
    from storygraph.ui_qt.card_editor import BeatCardEditor

    card = BeatCardEditor()
    try:
        bad = []
        names = list(card._all_names()) + [str(k) for k in card._spec_widgets]
        for name in names:
            widget = card._widgets.get(name)
            if widget is None:
                continue
            node = widget.parentWidget()
            ok = False
            while node is not None and node is not card:
                if getattr(node, '_row_kind', ''):
                    ok = True
                    break
                node = node.parentWidget()
            if not ok:
                bad.append(name)
        assert not bad, f'这些字段行不是 card_rows 造的（会各写各的宽度）：{bad}'
    finally:
        card.shutdown_stage_thumb()
