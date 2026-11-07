# -*- coding: utf-8 -*-
"""
「从一行剧本自动拆参数」的判据（`authoring.autospec`）
======================================================

用户 2026-11-07：

> 想一想到底有没有更简便的操作方式……谁会填那个逆天的表啊！！！

这一层替代的正是"填表"：语法的格子、技术调用的参数列，都从**剧本里真实
写出来的那一行**推出来。所以判据盯三件事：

1. **切得对**：引号里的空格不切、`at left` 的 `at` 是固定字、空引号占一个位置；
2. **不猜语义**：只给 `keyword` / `variable` 两种，名字就取原文（人能改）；
3. **能被人用上**：从「还不认识的写法」点一条进来，格子**已经建好**
   （这是"少点几下"的真正落点，光有纯函数没用）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.authoring.autospec import (call_columns, guess_call_heads,
                                           split_sample)


def _slots(line, head=''):
    """`split_sample` 现在返回 `(位置格, 子句)` —— 测试只关心位置格时用这个。"""
    slots, _clauses = split_sample(line, head)
    return slots


def _names(slots) -> list:
    return [s['name'] for s in slots]


# ----------------------------------------------------------------------
#  自定义语句：一行 → 格子
# ----------------------------------------------------------------------

def test_head_is_dropped_exactly_once():
    assert _names(_slots('show lan happy', 'show')) == ['lan', 'happy']
    # 没给 head：一个都不摘（不猜"第一个词是语句名"）
    assert _names(_slots('show lan happy')) == ['show', 'lan', 'happy']


def test_quoted_value_keeps_its_spaces_as_one_slot():
    slots = _slots('mycue "闪电 很亮" happy', 'mycue')
    assert _names(slots) == ['闪电 很亮', 'happy']
    assert all(s['kind'] == 'variable' for s in slots)


def test_clause_is_not_a_position_slot():
    """
    `at left` 是**一个子句**，不是两个位置格。

    从前把它当两格喂给 `pattern.match_line`，试切直接报
    `leftover: ['at','left']` —— 工具认错却怪人写错（准确性的核心）。 
    """
    slots, clauses = split_sample('mycue "闪电" at left', 'mycue')
    assert [s['name'] for s in slots] == ['闪电']
    assert clauses == [{'keyword': 'at', 'name': '位置', 'kind': 'words',
                        'values': ['left']}]


def test_transition_clause_keeps_its_expression_in_one_piece():
    slots, clauses = split_sample('mycue "闪电" with Dissolve( 0.3 )', 'mycue')
    assert [s['name'] for s in slots] == ['闪电']
    assert clauses[0]['kind'] == 'transition'
    assert clauses[0]['values'] == ['Dissolve(', '0.3', ')']


def test_two_clauses_split_at_the_next_keyword():
    slots, clauses = split_sample('mycue "闪电" at 左, 右 zorder 5', 'mycue')
    assert [s['name'] for s in slots] == ['闪电']
    assert [c['keyword'] for c in clauses] == ['at', 'zorder']
    assert clauses[0]['values'] == ['左,', '右']
    assert clauses[1]['values'] == ['5'] and clauses[1]['kind'] == 'number'


def test_a_clause_word_in_the_first_position_is_not_stolen():
    """第一个词就是 `at` → 说明项目把它当普通词用，别抢（当位置格）。"""
    slots, clauses = split_sample('at left', '')
    assert [s['name'] for s in slots] == ['at', 'left']
    assert clauses == []


def test_chinese_statement_splits_like_anything_else():
    slots = _slots('泳装切换 小明 泳装 开心', '泳装切换')
    assert _names(slots) == ['小明', '泳装', '开心']


def test_kwarg_uses_the_key_as_the_column_name():
    slots = _slots('mycue "闪电" intensity=3', 'mycue')
    assert slots[1]['name'] == 'intensity'
    assert slots[1]['sample'] == 'intensity=3'   # 值原样，不改写


def test_empty_quote_still_occupies_a_slot():
    slots = _slots('mycue ""', 'mycue')
    assert len(slots) == 1
    assert slots[0]['name'] == '第1格' and slots[0]['sample'] == ''


def test_blank_line_yields_nothing():
    assert split_sample('', 'x') == ([], [])
    assert split_sample('   ', 'x') == ([], [])
    assert split_sample('mycue', 'mycue') == ([], [])


# ----------------------------------------------------------------------
#  技术调用：一行 `$ f(...)` → 参数列
# ----------------------------------------------------------------------

def test_positional_and_keyword_columns():
    cols = call_columns('$ film_transition("fade", 0.4, layer="screens")',
                        'film_transition')
    assert cols[0] == {'name': 'fade', 'label': 'fade', 'position': 0,
                       'sample': 'fade'}
    assert cols[2]['kw'] == 'layer'
    assert cols[1]['name'] == '参数2'            # 裸数字给个能认的名字


def test_transition_call_names_the_column_after_the_function():
    cols = call_columns('$ renpy.transition(Dissolve(0.1), layer="master")',
                        'renpy.transition')
    assert cols[0]['name'] == 'Dissolve'
    assert cols[1]['kw'] == 'layer'


def test_multiple_calls_of_the_same_head_are_refused():
    """一拍里两次同名调用 = 一格装不下两条，**不猜改哪条**（`call_expr` 的纪律）。"""
    code = '$ my_wipe("a")\n$ my_wipe("b")'
    assert call_columns(code, 'my_wipe') == []


def test_guess_heads_skips_engine_and_builtins_and_ranks_by_count():
    got = guess_call_heads([
        '$ my_wipe("left", 0.6)',
        '$ my_wipe("right", 0.6)',
        '$ film_transition("fade", 0.4)',
        '$ renpy.transition(Dissolve(0.1))',
        '$ print("x")',
        'x = my_wipe("不是语句")',      # 没有 `$` → 不算
    ])
    assert [r['head'] for r in got] == ['my_wipe', 'film_transition']
    assert got[0]['count'] == 2 and got[0]['sample'].startswith('$ my_wipe')


# ----------------------------------------------------------------------
#  中文语句头必须能被"发现"（真 bug，2026-11-07 修）
# ----------------------------------------------------------------------

def _project(tmp_path, text: str):
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch01.rpy').write_text(text, encoding='utf-8')
    return root


def test_chinese_statement_head_is_discovered(tmp_path):
    """
    从前那道闸门是 `^[A-Za-z_]\\w*$`（纯 ASCII）→ **中文项目的自定义语句
    永远发现不了**，用户看到的是"这个模块根本不认识我的写法"。
    合法的 CDS 名可以是中文（`renpy.register_statement("泳装切换")`）。
    """
    from storygraph.story.statement_registry import discover_undeclared

    root = _project(tmp_path, '''label start:
    泳装切换 小明 泳装 开心
    "台词"
    mycue "闪电" intensity=3
    return
''')
    heads = [r['head'] for r in discover_undeclared(root)]
    assert '泳装切换' in heads
    assert 'mycue' in heads


def test_symbols_and_numbers_are_still_refused(tmp_path):
    """放宽成 Unicode 不等于什么都要：`$`/数字/下划线开头仍然不收。"""
    from storygraph.story.statement_registry import discover_undeclared

    root = _project(tmp_path, '''label start:
    $ renpy.notify("x")
    9bad 一个东西
    return
''')
    heads = [r['head'] for r in discover_undeclared(root)]
    assert not [h for h in heads if h.startswith(('$', '9'))]


# ----------------------------------------------------------------------
#  端到端：从「还不认识的写法」点进去，格子已经建好
# ----------------------------------------------------------------------

def test_picking_a_discovered_head_fills_the_slots(qapp, tmp_path):
    """
    **这条是"少点几下"的真正判据**：纯函数再对，如果界面进来还是空表，
    人照样得自己加格（那就是用户骂的那件事）。
    """
    from storygraph.story.statement_registry import discover_undeclared
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = _project(tmp_path, '''label start:
    mycue "闪电" intensity=3
    mycue "暴雨" intensity=5
    return
''')
    found = discover_undeclared(root)
    dlg = CustomStatementDialog(parent=None, project_root=root, discovered=found)
    assert dlg._pending
    dlg.found.setCurrentCell(0, 0)
    dlg._on_pick()
    assert dlg.name.text() == 'mycue'
    assert dlg.sample.text().startswith('mycue "闪电"')
    names = [row.get('name') for row in dlg.params.rows()]
    assert names == ['闪电', 'intensity'], names


def test_picking_a_chinese_head_fills_three_slots(qapp, tmp_path):
    from storygraph.story.statement_registry import discover_undeclared
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = _project(tmp_path, '''label start:
    泳装切换 小明 泳装 开心
    return
''')
    found = discover_undeclared(root)
    dlg = CustomStatementDialog(parent=None, project_root=root, discovered=found)
    assert [r['head'] for r in dlg._pending] == ['泳装切换']
    dlg.found.setCurrentCell(0, 0)
    dlg._on_pick()
    assert [row.get('name') for row in dlg.params.rows()] == ['小明', '泳装', '开心']


def test_the_autofill_button_is_reachable_and_works(qapp, tmp_path):
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = _project(tmp_path, 'label start:\n    return\n')
    dlg = CustomStatementDialog(parent=None, project_root=root, discovered=[])
    assert dlg.autofill_btn.text() == '照着这行建格子'
    dlg.name.setText('mycue')
    dlg.sample.setText('mycue "雷" intensity=2')
    assert dlg._autofill_from_sample() == 2
    assert [row.get('name') for row in dlg.params.rows()] == ['雷', 'intensity']
    # 这一行没有参数 → 说一句，不静默
    dlg.sample.setText('mycue')
    assert dlg._autofill_from_sample() == 0


# ----------------------------------------------------------------------
#  技术调用表：扫一遍剧本，把用到的调用列出来（只加不改）
# ----------------------------------------------------------------------

def test_call_dialog_scan_fills_columns_from_the_script(qapp, tmp_path):
    from storygraph.ui_qt.dialog_calls import CallDialog

    root = _project(tmp_path, '''label start:
    $ my_wipe("left", 0.6)
    $ my_wipe("right", 0.5)
    $ film_transition("fade", 0.4, layer="screens")
    "台词"
    return
''')
    dlg = CallDialog(None, project_root=root)
    assert dlg.scan_project() == 2
    calls = {c['head']: c for c in dlg.built_calls()}
    assert set(calls) == {'my_wipe', 'film_transition'}
    # 参数已经按逗号拆好，人不用写 `left@0`
    assert calls['my_wipe']['columns'][0]['position'] == 0
    assert calls['film_transition']['columns'][1]['position'] == 1
    assert calls['film_transition']['columns'][2]['kw'] == 'layer'
    # 再扫一次：一条都不加（幂等，且不动已经填好的中文名）
    dlg.table.item(0, 1).setText('擦除')
    assert dlg.scan_project() == 0
    assert dlg.table.item(0, 1).text() == '擦除'


def test_call_dialog_scan_without_a_project_says_so(qapp):
    from storygraph.ui_qt.dialog_calls import CallDialog

    dlg = CallDialog(None, project_root=None)
    assert dlg.scan_project() == 0
    assert '先打开项目' in dlg.status.text()


def test_cut_succeeds_after_the_clause_is_stripped(qapp, tmp_path):
    """
    声明了 `at` 子句之后，试切**不能**再说"多出 at、left"。

    这是"准确"的收口：自动拆把子句放进子句表之后，试切必须先把它们从
    这一行里摘掉再切，否则工具会一直指责一条**本来就对**的写法。
    """
    from storygraph.story.statement_registry import discover_undeclared
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = _project(tmp_path, '''label start:
    mycue "闪电" at left
    return
''')
    dlg = CustomStatementDialog(parent=None, project_root=root,
                                discovered=discover_undeclared(root))
    dlg.found.setCurrentCell(0, 0)
    dlg._on_pick()
    assert dlg.params.count() == 1                 # 只剩「闪电」
    assert dlg.clause_table.rowCount() == 1        # at 进了子句表
    assert dlg.clause_table.item(0, 0).text() == 'at'
    dlg._refresh_cut()
    text = dlg.cut_view.text()
    assert '切不上' not in text, text
    assert '闪电' in text


def test_autofill_rebuilds_the_clause_table_instead_of_appending(qapp, tmp_path):
    """反复点「照着这行建格子」不能把子句越堆越多（清空重建）。"""
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = _project(tmp_path, '''label start:
    return
''')
    dlg = CustomStatementDialog(parent=None, project_root=root, discovered=[])
    dlg.name.setText('mycue')
    dlg.sample.setText('mycue "闪电" at left with Dissolve(0.3)')
    assert dlg._autofill_from_sample() == 1
    assert dlg.clause_table.rowCount() == 2
    dlg._autofill_from_sample()
    assert dlg.clause_table.rowCount() == 2, '子句表被重复追加了'
