"""
资产 / 角色统计的**键名契约**（2026-10-18 审计 P1-4）
=====================================================

`character_script_linker` 给的是 `variable_name / display_name /
dialogue_count`，界面却读 `name / character / count / lines` ——
每一行都显示 `None：None`。这条钉住：两个词都要显示，绝不出现 None。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest
# **先确认有 PySide6 再 import 任何 ui_qt 模块**（2026-10-21，修 CI 全红）：
# 这个文件直接 `from storygraph.ui_qt.…` / `from PySide6…`，没有 PySide6
# 的环境（CI）会在**收集阶段**整批 ERROR —— 收集阶段没有 skip 的机会，
# 一条测试都不会跑。`importorskip` 必须站在第一次 import 它们之前。
pytest.importorskip('PySide6')

from storygraph.ui_qt.window_graph_ops import format_character_ranking  # noqa: E402


def test_linker_keys_are_rendered_with_variable_and_display_name():
    rows = [{'variable_name': 'lan', 'display_name': '蓝',
             'dialogue_count': 12, 'label_count': 2}]
    line = format_character_ranking(rows)[0]
    assert '蓝（lan）：12' in line, line
    assert 'None' not in line, line


def test_legacy_keys_still_work():
    rows = [{'name': '岚', 'count': 7}]
    line = format_character_ranking(rows)[0]
    assert '岚：7' in line, line
    assert 'None' not in line
