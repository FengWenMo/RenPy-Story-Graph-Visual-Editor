"""
tests/test_excel_usage_sheet.py
===============================
清单 §十三：Excel 的「列说明」表必须**由列定义真源生成**，不许手抄。

从前是 25 行手抄（真源 35 列），漏掉的恰好是结构位 / 原样块 / 行尾注释
这些"改坏就等于改坏文件"的隐藏列；加字段时这张表不会跟着变，也不会有
任何测试失败。
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.story.beat_fields import COLUMN_SPECS                 # noqa: E402
from storygraph.story.excel_export import export_beats_to_excel      # noqa: E402


def _usage_rows(tmp_path):
    out = tmp_path / 'usage.xlsx'
    export_beats_to_excel([], out)
    wb = openpyxl.load_workbook(out)
    ws = wb['列说明']
    return [[c.value for c in row] for row in ws.iter_rows()]


def test_usage_sheet_has_exactly_one_row_per_column_spec(tmp_path):
    rows = _usage_rows(tmp_path)
    body = rows[1:]
    assert len(body) == len(COLUMN_SPECS), (
        f'列说明 {len(body)} 行，真源 {len(COLUMN_SPECS)} 列')
    assert [r[0] for r in body] == [f'{s.header} {s.name}'
                                    for s in COLUMN_SPECS]


def test_every_column_has_a_usage_row(tmp_path):
    body = _usage_rows(tmp_path)[1:]
    labels = [str(r[0] or '') for r in body]
    for spec in COLUMN_SPECS:
        assert any(spec.name in label and spec.header in label
                   for label in labels), spec.name


def test_usage_sheet_mentions_the_structural_truth_columns(tmp_path):
    """漏过的那几列必须回来（尤其「结构位」——唯一结构真源）。"""
    body = _usage_rows(tmp_path)[1:]
    labels = '\n'.join(str(r[0] or '') for r in body)
    for name in ('Nest', 'Opaque', 'Inline', 'StageOrder', 'OpOrder',
                 'CodeBlocks', 'SpecName', 'SpecHead', 'LoopHeader',
                 'CameraHeader'):
        assert name in labels, name


@pytest.mark.parametrize('name', ['Nest', 'Opaque', 'Inline'])
def test_hidden_truth_columns_are_explained(tmp_path, name):
    body = _usage_rows(tmp_path)[1:]
    row = next(r for r in body if r[0] and name in r[0])
    assert row[1] == '程序区'
    assert row[3], '隐藏列也要有一句"怎么填/别手改"'
