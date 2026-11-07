# -*- coding: utf-8 -*-
"""
`placeholder` 串到**语句参数格**（2026-11-07 第七轮，设计文档 §10.9 第 3 条）

断在哪：声明里写的提示（`FieldSpec.placeholder`）只到得了**扩展窗口**那半边；
卡片上的参数格用的是按类型的默认灰字（`PARAM_PLACEHOLDER`）。于是
"字段表上写的『留空 = 不写这一格』"人根本看不见。

这条链要一路通到**控件**：

    FieldSpec.placeholder
      → slot_of_field / column_of_field      （声明）
      → pattern[] / calls[].columns[]        （落盘的那两位）
      → parse_pattern / _calls_from_manifest （读回来）
      → spec_columns → _column_entry         （列描述）
      → card_editor._spec_cell_editor        （建控件）
      → QLineEdit.placeholderText()          （人眼前）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.pattern import parse_pattern  # noqa: E402
from storygraph.ext.schema import (  # noqa: E402
    FieldSpec, column_of_field, field_of_column, slot_of_field)
from storygraph.story import statement_registry as SR  # noqa: E402


def _project(tmp_path: Path, data: dict) -> Path:
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.rules.json').write_text(
        json.dumps(data, ensure_ascii=False), encoding='utf-8')
    SR.clear_cache()
    return root


def _statement_with(placeholder: str) -> dict:
    return {'statements': [{
        'name': 'mycue', 'label': '我的写法',
        'pattern': [{'kind': 'number', 'column': '强度', 'required': False,
                     'placeholder': placeholder}]}]}


class TestDeclarationCarriesIt:
    def test_slot_of_field_keeps_placeholder_and_help(self):
        spec = FieldSpec(key='强度', kind='number', placeholder='1~10',
                         help='越高越亮')
        slot = slot_of_field(spec)
        assert slot['placeholder'] == '1~10'
        assert slot['help'] == '越高越亮'
        # 老写法（没有提示）不许多出这两个键
        bare = slot_of_field(FieldSpec(key='强度', kind='number'))
        assert 'placeholder' not in bare and 'help' not in bare

    def test_parse_pattern_keeps_them(self):
        slot = parse_pattern([{'kind': 'number', 'column': '强度',
                               'placeholder': '1~10', 'help': '说明'}])[0]
        assert slot['placeholder'] == '1~10'
        assert slot['help'] == '说明'

    def test_call_column_round_trip(self):
        spec = FieldSpec(key='方向', kind='choice', choices=('left',),
                         position=0, placeholder='往哪边',
                         help='左右都行')
        col = column_of_field(spec)
        assert col['placeholder'] == '往哪边' and col['help'] == '左右都行'
        back = field_of_column(col)
        assert back.placeholder == '往哪边'
        assert back.help == '左右都行'


class TestItReachesTheColumnDescriptions:
    def test_statement_spec_columns_carry_it(self, tmp_path):
        root = _project(tmp_path, _statement_with('1~10（留空 = 不写）'))
        reg = SR.StatementRegistry.for_project(root)
        cols = SR.spec_columns(reg, 'mycue')
        assert cols and cols[0]['placeholder'] == '1~10（留空 = 不写）', cols
        assert 'placeholder' in cols[0]

    def test_call_spec_columns_carry_it(self, tmp_path):
        root = _project(tmp_path, {'calls': [{
            'head': 'my_wipe',
            'columns': [{'name': '方向', 'position': 0, 'kind': 'choice',
                         'choices': ['left', 'right'],
                         'placeholder': '往哪边擦'}]}]})
        reg = SR.StatementRegistry.for_project(root)
        cols = SR.call_columns(reg, 'my_wipe')
        assert cols[0]['placeholder'] == '往哪边擦', cols

    def test_absent_placeholder_is_empty_not_missing(self, tmp_path):
        root = _project(tmp_path, {'statements': [{
            'name': 'bare', 'pattern': [{'kind': 'number', 'column': '强度'}]}]})
        reg = SR.StatementRegistry.for_project(root)
        assert SR.spec_columns(reg, 'bare')[0]['placeholder'] == ''


class TestItReachesTheWidget:
    @pytest.fixture()
    def card(self, qapp):
        from storygraph.ui_qt.card_editor import BeatCardEditor
        return BeatCardEditor()

    def test_the_param_cell_shows_the_declared_hint(self, card):
        w = card._spec_cell_editor('强度', (), None, 'number',
                                   placeholder='1~10（留空 = 不写）')
        assert w.placeholderText() == '1~10（留空 = 不写）'

    def test_without_a_hint_the_default_still_applies(self, card):
        from storygraph.ext.schema import PARAM_PLACEHOLDER
        w = card._spec_cell_editor('强度', (), None, 'number')
        assert w.placeholderText() == PARAM_PLACEHOLDER['number']

    def test_the_whole_chain_end_to_end(self, card, tmp_path):
        """声明 → 列描述 → 卡片那一格（人眼前），一步不断。"""
        root = _project(tmp_path, _statement_with('1~10（留空 = 不写）'))
        reg = SR.StatementRegistry.for_project(root)
        col = SR.spec_columns(reg, 'mycue')[0]
        card._add_spec_cell({'header': col['name'], 'param': '强度',
                             'kind': col['kind'],
                             'placeholder': col['placeholder']}, {}, {})
        widget = card._widgets['强度']
        assert widget.placeholderText() == '1~10（留空 = 不写）'
