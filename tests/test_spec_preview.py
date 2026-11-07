# -*- coding: utf-8 -*-
"""
自定义模块的**实时预览**（`ui_qt/spec_preview.py`）
=====================================================

用户 2026-11-07：

> 卡片编辑器右边的 UI 和自定义模块定义出的模块一定要对应！UI 要重构……

预览是"对应"的解药：配置时能看见结果，就不用靠记忆去猜"声明完之后卡片长啥样"。

判据盯四件事：**空态说人话**、**一格一行**、**形态跟类型走**、**不跟着按键乱重建**。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit

from storygraph.ui_qt.spec_preview import SpecPreview


def _texts(widget) -> list:
    return [lab.text() for lab in widget.findChildren(QLabel)]


def test_empty_state_says_what_to_do(qapp):
    preview = SpecPreview()
    texts = _texts(preview)
    assert any('还没填开头词' in t for t in texts)
    assert any('加一格' in t for t in texts), '空态要告诉人下一步做什么'


def test_one_slot_renders_one_row(qapp):
    preview = SpecPreview()
    preview.refresh('mycue', [{'name': '喊话', 'label': '喊话',
                               'kind': 'variable'}], [])
    texts = _texts(preview)
    assert any('mycue' in t for t in texts)
    # 一格的标签在行里
    assert '喊话' in texts
    assert len(preview.findChildren(QLineEdit)) == 1


def test_choices_render_as_a_disabled_combo(qapp):
    """有候选 → 画成下拉（形态要对：让人看出"这里是选，不是随便写"），且不可改。"""
    preview = SpecPreview()
    preview.refresh('mycue', [{'name': '服装', 'label': '服装',
                               'kind': 'variable',
                               'options': ['泳装', '校服']}], [])
    combos = preview.findChildren(QComboBox)
    assert len(combos) == 1
    assert combos[0].isEnabled() is False, '预览里的控件不该能改'
    assert [combos[0].itemText(i) for i in range(combos[0].count())] == ['泳装', '校服']


def test_keyword_shows_the_literal_and_is_read_only(qapp):
    preview = SpecPreview()
    preview.refresh('mycue', [{'name': 'at', 'label': 'at', 'kind': 'keyword',
                               'text': 'at'}], [])
    edit = preview.findChildren(QLineEdit)[0]
    assert edit.isReadOnly()
    assert edit.text() == 'at'


def test_clauses_get_their_own_rows(qapp):
    preview = SpecPreview()
    preview.refresh('mycue', [], [{'keyword': 'at', 'name': '位置',
                                   'kind': 'words'}])
    texts = _texts(preview)
    assert '位置' in texts
    edit = preview.findChildren(QLineEdit)[0]
    assert edit.isReadOnly()
    assert 'at' in edit.placeholderText()


def test_the_same_signature_does_not_rebuild(qapp):
    """
    防抖：表单每个按键都会调 `refresh`，签名没变就不能重建控件
    （重建下拉尤其贵，还会把滚动位置抖掉）。
    """
    preview = SpecPreview()
    slots = [{'name': '喊话', 'label': '喊话', 'kind': 'variable'}]
    preview.refresh('mycue', slots, [])
    first = preview.findChildren(QLineEdit)[0]
    preview.refresh('mycue', list(slots), [])
    second = preview.findChildren(QLineEdit)[0]
    assert first is second, '签名没变却重建了控件'


def test_changing_a_slot_rebuilds(qapp):
    preview = SpecPreview()
    preview.refresh('mycue', [{'name': '喊话', 'label': '喊话',
                               'kind': 'variable'}], [])
    assert len(preview.findChildren(QLineEdit)) == 1
    preview.refresh('mycue', [{'name': '喊话', 'label': '喊话', 'kind': 'variable'},
                              {'name': '强度', 'label': '强度', 'kind': 'number'}], [])
    assert len(preview.findChildren(QLineEdit)) == 2
