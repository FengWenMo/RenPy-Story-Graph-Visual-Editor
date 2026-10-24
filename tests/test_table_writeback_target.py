"""
tests/test_table_writeback_target.py
====================================
清单 §十二（P1）：「从剧本表写回 .rpy」的落点不许取决于"画布上选中了哪个节点"。

旧行为：用户挑了 `ch01.xlsx`，画布上恰好选中 `ch02` → 落点是 `ch02.rpy`，
而报错文案说的是 `ch01` 的 label。两个 UI 状态之间没有任何一致性检查。

新规则：**只看表自己的同名 `.rpy`**。没有就返回 None，让 app 层落
`build/renpy/` —— 绝不凭空往 game/ 里多写一份。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip('PySide6')

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.ui_qt.window_export import WindowExportMixin  # noqa: E402


class _BoomCanvas:
    def selected_node_id(self):
        raise AssertionError('落点判定不该再读画布选中态')


def _window() -> WindowExportMixin:
    win = object.__new__(WindowExportMixin)
    win._canvas = _BoomCanvas()
    win.graph = None
    return win


def test_sibling_rpy_is_the_source(tmp_path):
    win = _window()
    table = tmp_path / 'ch01.xlsx'
    table.write_bytes(b'')
    rpy = tmp_path / 'ch01.rpy'
    rpy.write_text('label ch01:\n    return\n', encoding='utf-8')

    assert win._source_rpy_for_table(table) == rpy


def test_no_sibling_returns_none_instead_of_canvas_selection(tmp_path):
    win = _window()
    table = tmp_path / 'ch01.xlsx'
    table.write_bytes(b'')

    assert win._source_rpy_for_table(table) is None
