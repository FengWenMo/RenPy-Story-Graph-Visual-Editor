# -*- coding: utf-8 -*-
"""
tests/test_vocabulary_ui.py
===========================
**词汇表在界面上真的生效吗**（用户 2026-10-31 的六条）

`tests/test_vocabulary.py` 钉的是那层逻辑；这里钉的是**屏幕**：

  · 立绘卡的「位置」chip 显示「1人，近处」而不是 `i11`（悬停里还能看到原名）；
  · 服装 chip 显示「B 泳装」，而写进剧本的仍是 `base_swimsuit`；
  · 「中文叫法」那一段：下拉里是中文种类、格子里存英文值，能加能改；
  · 查不到的名字**原样显示**（词汇表是补充，不是白名单）。
"""

from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest  # noqa: E402

pytest.importorskip('PySide6')

from storygraph.assets import vocabulary as V  # noqa: E402

DATA = {
    'vocabulary': [
        {'kind': 'transform', 'id': 'i11', 'label': '1人，近处'},
        {'kind': 'character', 'id': 'DY', 'label': '大爷'},
        {'kind': 'cue', 'id': 'my_wipe', 'label': '闪白'},
    ],
    'outfit_codes': [
        {'char': 'lan', 'code': 'B', 'attr': 'base_swimsuit', 'label': '泳装'},
    ],
    'expression_codes': [
        {'char': 'lan', 'no': '7', 'attr': 'indifferent', 'label': '不在意'},
    ],
}


def _provider(vocab):
    """网格注入的那个"分组提供者"：一个函数 + 挂在上面的词汇表。"""

    def provide(tag):
        return []

    provide.attr_groups = []
    provide.engine_facts = {}
    provide.vocabulary = vocab
    return provide


# ==================================================================
#  立绘卡：chip 上显示人话，工具提示里有原名
# ==================================================================

class TestCardChips:
    def _card(self, item, vocab=None):
        from storygraph.ui_qt.sprite_grid import SpriteCardWidget
        card = SpriteCardWidget(dict(item))
        # 窗口/网格就是这么接的：把 provider 挂到卡片上（`SpriteGridWidget`
        # 的 `set_choice_groups_provider` 对每张卡做同一件事）。
        card._choice_groups_for = _provider(vocab or V.EMPTY_VOCABULARY)
        return card

    def test_位置显示中文(self, qapp):
        card = self._card({'image': 'lan happy', 'position': 'i11'},
                          V.from_mapping(DATA))
        try:
            card.refresh()
            assert '1人，近处' in card._pos_btn.text(), card._pos_btn.text()
            assert 'i11' in card._pos_btn.toolTip()      # 原名还在悬停里
            # **写回去的仍是引擎写法**：显示归显示
            assert card.item.get('position') == 'i11'
        finally:
            card.deleteLater()

    def test_没登记的名字原样显示(self, qapp):
        card = self._card({'image': 'lan happy', 'position': 'i99'},
                          V.from_mapping(DATA))
        try:
            card.refresh()
            assert 'i99' in card._pos_btn.text()
        finally:
            card.deleteLater()

    def test_服装chip显示代号和说法(self, qapp):
        card = self._card({'image': 'lan base_swimsuit happy'},
                          V.from_mapping(DATA))
        rows = [{'group': 'outfit', 'label': '服装', 'value': 'base_swimsuit',
                 'display': 'base_swimsuit', 'options': ['base_swimsuit']}]
        try:
            card._slot_rows = lambda: rows        # 只测显示层（分组由引擎给）
            card.refresh()
            text = card._slot_btns[0].text()
            assert 'B 泳装' in text, text
            assert 'base_swimsuit' in card._slot_btns[0].toolTip()
        finally:
            card.deleteLater()

    def test_表情chip也走同一套(self, qapp):
        card = self._card({'image': 'lan happy'}, V.from_mapping(DATA))
        rows = [{'group': 'expressions', 'label': '表情',
                 'value': 'indifferent', 'display': 'indifferent',
                 'options': ['indifferent']}]
        try:
            card._slot_rows = lambda: rows
            card.refresh()
            assert '7 不在意' in card._slot_btns[0].text()
        finally:
            card.deleteLater()

    def test_太长会截断但悬停有全文(self, qapp):
        long_name = '一个特别特别特别长的位置名字'
        vocab = V.from_mapping({'vocabulary': [
            {'kind': 'transform', 'id': 'i11', 'label': long_name}]})
        card = self._card({'image': 'lan happy', 'position': 'i11'}, vocab)
        try:
            card.refresh()
            text = card._pos_btn.text()
            assert '…' in text and len(text) < len(long_name) + 4, text
            assert long_name in card._pos_btn.toolTip()
        finally:
            card.deleteLater()


# ==================================================================
#  「中文叫法」那段：中文种类的下拉，英文值的存储
# ==================================================================

class TestTheTable:
    def _editor(self):
        from storygraph.ui_qt.mapping_editor import MappingEditor
        editor = MappingEditor()
        editor.load(DATA)
        editor.set_section('vocabulary')
        return editor

    def test_格子里是中文_文件里是英文(self, qapp):
        editor = self._editor()
        try:
            assert editor.cell_text(0, 1) == '位置 / 镜头'
            assert editor.cell_value(0, 1) == 'transform'
            assert editor.cell_text(0, 2) == 'i11'
            assert editor.cell_text(0, 3) == '1人，近处'
        finally:
            editor.deleteLater()

    def test_加一行_选种类_填名字(self, qapp):
        editor = self._editor()
        try:
            index = editor.add_row()
            assert index >= 0
            # 程序写格子走的仍是**文件里的值**（`cue`），屏幕上翻成「过场 / 调用」
            assert editor.set_cell(index, 1, 'cue') == ''
            editor.set_cell(index, 2, 'my_flash')
            editor.set_cell(index, 3, '闪白')
            assert editor.cell_text(index, 1) == '过场 / 调用'
            rows = editor.current_data()['vocabulary']
            assert rows[-1] == {'kind': 'cue', 'id': 'my_flash', 'label': '闪白'}
        finally:
            editor.deleteLater()

    def test_种类下拉有全部种类(self, qapp):
        from storygraph.assets import mapping_edit as ME
        editor = self._editor()
        try:
            delegate = editor._table.itemDelegateForColumn(1)
            editor_widget = delegate.createEditor(editor._table, None,
                                                  editor._table.model().index(0, 1))
            items = [editor_widget.itemText(i)
                     for i in range(editor_widget.count())]
            assert '位置 / 镜头' in items and '服装' in items and '过场 / 调用' in items
            assert len(items) == len(ME.VOCAB_CHOICES)
            editor_widget.deleteLater()
        finally:
            editor.deleteLater()
