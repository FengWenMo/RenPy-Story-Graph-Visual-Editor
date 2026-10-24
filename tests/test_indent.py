"""
tests/test_indent.py
====================
清单 §十四：缩进测量收口到 `core.indent` 的两个名字。

  · `indent_prefix(line)` —— 原样前缀字符串（拼接用）；
  · `indent_width(line)`  —— 视觉宽度（比较层级用，tab=4，Ren'Py 口径）。

以前四个模块各写一份 `_indent_of`：三份返回 int、一份返回 str；tab 算 4
还是算 1 也不一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.core.indent import (TAB_WIDTH, indent_prefix,  # noqa: E402
                                    indent_width)


def test_prefix_is_the_literal_prefix():
    assert indent_prefix('    show ling') == '    '
    assert indent_prefix('\tshow ling') == '\t'
    assert indent_prefix('') == ''
    assert indent_prefix('top') == ''


def test_width_treats_tab_as_four():
    assert TAB_WIDTH == 4
    assert indent_width('    show') == 4
    assert indent_width('\tshow') == 4
    assert indent_width(' \tshow') == 5
    assert indent_width('') == 0
    assert indent_width('top') == 0


def test_prefix_length_is_character_count_not_width():
    """需要"字符数"时写 `len(indent_prefix(...))`，不要再造第三个名字。"""
    assert len(indent_prefix('\tx')) == 1
    assert indent_width('\tx') == 4


def test_all_four_old_call_sites_now_agree():
    """以前四份 `_indent_of` 对 tab 行的结论不同；现在都走同一份实现。"""
    from storygraph.parsing.renpy_parser import indent_width as parser_width
    from storygraph.story.patcher import indent_prefix as patch_prefix
    from storygraph.story.python_lift import indent_width as lift_width

    for sample in ('    x', '\tx', '        x', ''):
        assert parser_width(sample) == indent_width(sample)
        assert lift_width(sample) == indent_width(sample)
        assert patch_prefix(sample) == indent_prefix(sample)
