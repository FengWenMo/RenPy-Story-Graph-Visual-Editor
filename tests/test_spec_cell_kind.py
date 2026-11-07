# -*- coding: utf-8 -*-
"""
参数格**按声明里的类型选控件**（2026-11-07 第 1 项）
=====================================================

用户反复讲的那句：

> 卡片编辑器右边的 UI 和自定义模块定义出的模块一定要对应！

改之前 `_spec_cell_editor` 只有两个分支（有候选→下拉，否则→输入框），
**`kind` 一次都没被读**。后果里最重的一个是：声明里的 `keyword`（固定字，
剧本里必须原样出现）在卡片上是**可编辑的输入框** —— 人改一下就把语句名改掉，
写回时按位置替换第 0 个词，整条语句废掉。

判据分两层：**数据层带没带 kind**、**卡片按 kind 选没选对控件**。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ----------------------------------------------------------------------
#  数据层：`kind` 必须顺着列一起出来
# ----------------------------------------------------------------------

def _declared(tmp_path):
    """写一条带三种格子的声明，返回它所在项目的 registry。"""
    from storygraph.story import statement_registry as SR

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch01.rpy').write_text(
        'label start:\n    mycue "闪电" 3\n    return\n', encoding='utf-8')
    res = SR.declare_statement(
        root, 'mycue', label='提示',
        columns=[{'name': '喊话', 'label': '喊话'},
                 {'name': '强度', 'label': '强度', 'positional': 1}],
        pattern=[{'kind': 'variable', 'name': '喊话', 'label': '喊话'},
                 {'kind': 'number', 'name': '强度', 'label': '强度'},
                 {'kind': 'keyword', 'text': 'mycue'}])
    assert res.get('ok'), res
    SR.clear_cache()
    return SR, root


def test_columns_carry_the_declared_kind(tmp_path):
    """`spec_columns` 的每一条都要带 `kind` —— 界面才有东西可以分派。"""
    SR, root = _declared(tmp_path)
    reg = SR.StatementRegistry.for_project(root)
    cols = {c['name']: c for c in SR.spec_columns(reg, 'mycue')}
    assert cols['喊话']['kind'] == 'variable'
    assert cols['强度']['kind'] == 'number'


def test_dynamic_spec_columns_carry_the_kind(tmp_path):
    """`dynamic_spec_columns` 也要带 —— 卡片读的是它，不是 `spec_columns`。"""
    from storygraph.story.beat_model import Beat

    SR, root = _declared(tmp_path)
    reg = SR.StatementRegistry.for_project(root)
    beat = Beat(seq=1, label='ch01', kind='say', spec_name='mycue',
                spec_head='mycue "闪电" 3')
    cols = SR.dynamic_spec_columns([beat], reg)
    assert cols, '这条语句没建出任何列'
    kinds = {c['param']: c.get('kind') for c in cols}
    assert kinds.get('强度') == 'number'


# ----------------------------------------------------------------------
#  卡片：按 kind 选控件
# ----------------------------------------------------------------------

def _card(qapp):
    from storygraph.ui_qt.card_editor import BeatCardEditor
    return BeatCardEditor()


def _editor_for(card, kind, opts=()):
    """在卡片里让某一格按指定 kind 建出来，返回那个控件。"""
    # ⚠ 候选走**第二个参数**（`choices={参数名: [...]}`）——卡片里
    #   `opts = as_choices(choices.get(param))`，塞在 col['choices'] 里不生效
    #   （我第一版就是这么写的，判据当场抓到）。
    card.set_spec_args([{'header': 'mycue·x', 'param': 'x', 'label': 'x',
                         'choices': [], 'positional': 0,
                         'multi': False, 'kind': kind}], {},
                       {'x': list(opts)}, None)
    return card._spec_widgets.get('mycue·x')


def test_keyword_becomes_read_only(qapp):
    """
    固定字**只读** —— 这是这一轮最重要的那一条。

    `keyword` 是"剧本里必须原样出现的那几个字"，可编辑 = 允许人把它改掉。
    """
    card = _card(qapp)
    try:
        w = _editor_for(card, 'keyword')
        assert isinstance(w, QLineEdit)
        assert w.isReadOnly(), '固定字变成可编辑框了（改一次就写坏脚本）'
    finally:
        card.shutdown_stage_thumb()


def test_keyword_gets_no_candidate_picker(qapp):
    """固定字不该有「⋯」候选按钮（它本来就不能改）。"""
    card = _card(qapp)
    try:
        card.set_spec_args([{'header': 'mycue·x', 'param': 'x', 'label': 'x',
                             'choices': ['a', 'b'], 'positional': 0,
                             'multi': False, 'kind': 'keyword'}], {}, {}, None)
        host = card._spec_widgets['mycue·x'].parentWidget()
        btns = [b for b in host.findChildren(QPushButton) if b.text() == '⋯']
        assert btns == [], '固定字旁边还挂着候选按钮'
    finally:
        card.shutdown_stage_thumb()


def test_number_gets_a_validator(qapp):
    card = _card(qapp)
    try:
        w = _editor_for(card, 'number')
        assert isinstance(w, QLineEdit)
        assert w.validator() is not None, 'number 格没有数字校验'
    finally:
        card.shutdown_stage_thumb()


def test_choice_with_options_becomes_a_combo(qapp):
    card = _card(qapp)
    try:
        w = _editor_for(card, 'choice', ['泳装', '校服'])
        assert isinstance(w, QComboBox)
        assert w.isEditable(), '候选只是提示，手打新值也得能填（同 Excel）'
    finally:
        card.shutdown_stage_thumb()


def test_unknown_kind_falls_back_to_options(qapp):
    """没有 kind 的旧声明照旧：有候选就下拉，没有就输入框。"""
    card = _card(qapp)
    try:
        assert isinstance(_editor_for(card, '', ['a']), QComboBox)
        assert isinstance(_editor_for(card, '', []), QLineEdit)
    finally:
        card.shutdown_stage_thumb()


def test_a_read_only_keyword_still_writes_back_its_original_value(qapp):
    """只读不等于"不写回"：这一格的值照旧进 `collect_fields`。"""
    card = _card(qapp)
    try:
        card.set_spec_args([{'header': 'mycue·x', 'param': 'x', 'label': 'x',
                             'choices': [], 'positional': None,
                             'multi': False, 'kind': 'keyword'}],
                           {'mycue·x': 'mycue'}, {}, None)
        assert card.collect_fields().get('mycue·x') == 'mycue'
    finally:
        card.shutdown_stage_thumb()


# ----------------------------------------------------------------------
#  「不管它是代码还是别的什么东西」（用户 2026-11-07）
# ----------------------------------------------------------------------

def _code_card(qapp, value=''):
    card = _card(qapp)
    card.set_spec_args([{'header': 'mycue·x', 'param': 'x', 'label': 'x',
                         'choices': [], 'positional': 0,
                         'multi': False, 'kind': 'code'}],
                       {'mycue·x': value}, {}, None)
    return card


def test_code_kind_gets_a_multi_line_editor(qapp):
    """
    声明里说"这一格是一段代码" → 卡片上**必须是多行框**。

    改之前所有 kind 都落到"有候选就下拉、否则单行输入框"，
    于是一段带括号/逗号的表达式只能挤在一行里看 —— 这正是用户说的
    "UI 界面根本显示不出来"。
    """
    from PySide6.QtWidgets import QPlainTextEdit

    card = _code_card(qapp)
    try:
        assert isinstance(card._spec_widgets['mycue·x'], QPlainTextEdit)
    finally:
        card.shutdown_stage_thumb()


def test_code_kind_folds_back_to_one_line(qapp):
    """
    多行只是**看得舒服**：写回去必须折成一行。

    参数最终要折进语句头（`mycue "闪电" <这里>`），带换行写回去就是语法错
    —— 与 `expr` 只给单行是同一条理由。
    """
    card = _code_card(qapp)
    try:
        card._spec_widgets['mycue·x'].setPlainText('Dissolve(0.1,\n      0.2)')
        assert card.collect_fields().get('mycue·x') == 'Dissolve(0.1, 0.2)'
    finally:
        card.shutdown_stage_thumb()


def test_code_kind_round_trips_the_original_value(qapp):
    """回填也得认得这个控件（它没有 `setText`）。"""
    card = _code_card(qapp, 'Fade(0.2, 0, 0.2)')
    try:
        assert card.collect_fields().get('mycue·x') == 'Fade(0.2, 0, 0.2)'
    finally:
        card.shutdown_stage_thumb()


def test_slot_kind_code_maps_to_the_code_field():
    """解析器词汇 ↔ 界面控件**只有一处映射**（`ext.schema`）。"""
    from storygraph.ext import schema as ES

    assert ES.field_of_slot({'kind': 'code', 'column': '片段'}).kind == 'code'
    assert ES.field_of_slot({'kind': 'multiline',
                             'column': '段落'}).kind == 'multiline'
