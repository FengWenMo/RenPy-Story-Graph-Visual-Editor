"""
tests/test_transition_expr.py
=============================
**过场表达式解析**（`storygraph.story.transition_expr`）的判据。

用户 2026-10-30 第 3 条反馈"过场没有注册"：`$ renpy.transition(...)` 是
Python 代码、独立 `with` 行是未建模语句，两条过去对预览/卡片都不可见，
预览永远硬切。这一层把它们读成 `TransitionCall`（随后由
`transition_catalog` 翻成效果描述）。

判据分四类：
  ① **读得出来**：单行 / 多行 / layer（位置与关键字）/ 注释 / 中文名；
  ② **不误读**：注释、字符串、`python:` 块体、括号配不平 —— 都不产出；
  ③ **不猜**：表达式认不出就空；参数不是字面量记 `NonLiteral`，不当值；
  ④ **纯读**：`beat_transitions()` 前后 Beat 的 `to_row()` 逐项不变。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from storygraph.story.beat_model import Beat
from storygraph.story.transition_expr import (
    SOURCE_INLINE_WITH, SOURCE_RENPY, SOURCE_WITH, NonLiteral, TransitionCall,
    beat_transitions, normalize_expression, parse_expression,
    parse_python_transition, parse_with_clause)

import storygraph.story.transition_expr as transition_expr


# ---------------------------------------------------------------------------
#  ① 读得出来
# ---------------------------------------------------------------------------

def test_single_line_code_transition():
    """真机最常见的一行：`$ renpy.transition(Dissolve(0.1), layer=…)`。"""
    calls = parse_python_transition(
        '$ renpy.transition(Dissolve(0.1), layer="master")')
    assert len(calls) == 1
    call = calls[0]
    assert isinstance(call, TransitionCall)
    assert (call.expr, call.name) == ('Dissolve(0.1)', 'Dissolve')
    assert call.args == (0.1,)
    assert call.kwargs == {'layer': 'master'}
    assert call.layer == 'master'
    assert call.source == SOURCE_RENPY
    assert call.line == 1


def test_multiline_call_and_comment_and_string_parens():
    """
    多行调用 + 行尾注释（注释里有 `)`）+ 字符串里的 `)` —— 一处都不能配错。
    """
    code = ('$ renpy.transition(\n'
            '    Dissolve(0.1),      # 注释里的 ) 不能参与配平\n'
            '    layer="m)a",\n'
            ')\n')
    calls = parse_python_transition(code)
    assert len(calls) == 1
    assert calls[0].expr == 'Dissolve(0.1)'
    assert calls[0].layer == 'm)a'
    assert calls[0].line == 1, '行号要指向调用头那一行'


def test_layer_positional_and_keyword_always():
    """`renpy.transition(trans, layer)` 的位置参数；`always=` 是关键字。"""
    calls = parse_python_transition(
        'renpy.transition(dissolve, "screens", True)\n'
        'renpy.transition(Dissolve(0.2), layer=layer_var, always=True)')
    assert calls[0].layer == 'screens'
    assert calls[0].kwargs == {}, '位置参数不进 kwargs（层已经单独给出）'
    second = calls[1]
    assert second.layer == '', '层不是字面量 → 不猜'
    assert second.kwargs['layer'] == NonLiteral('layer_var')
    assert second.kwargs['always'] is True


def test_unicode_transition_name():
    """项目完全可以用中文给转场命名（Python 3 标识符口径）。"""
    calls = parse_with_clause('with 场景淡入')
    assert len(calls) == 1
    assert calls[0].name == '场景淡入'
    assert calls[0].expr == '场景淡入'


def test_line_numbers_in_text():
    calls = parse_python_transition('\n\nrenpy.transition(Dissolve(0.1))')
    assert [c.line for c in calls] == [3]
    calls = parse_with_clause('with dissolve\nwith fade')
    assert [(c.name, c.line) for c in calls] == [('dissolve', 1), ('fade', 2)]


# ---------------------------------------------------------------------------
#  ② 不误读
# ---------------------------------------------------------------------------

def test_comments_and_strings_are_not_transitions():
    code = ('msg = "renpy.transition(Dissolve(1.0))"\n'
            '# renpy.transition(Fade(2.0))\n'
            'renpy.notify("with fade")\n'
            'renpy.transition(pixellate, layer="master")\n')
    calls = parse_python_transition(code)
    assert [c.expr for c in calls] == ['pixellate']
    assert calls[0].line == 4


def test_python_block_bodies_are_ignored_by_beat_transitions():
    """
    `python:` 块体里的调用**不算**这一拍的固定过场（块里可能是循环/条件，
    语义不同）。块边界来自 `Beat.code_blocks`，不是猜缩进。
    """
    beat = Beat(line_id='probe', code=(
        'renpy.transition(Dissolve(0.1))\n'
        'for i in range(3):\n'
        '    renpy.transition(Dissolve(0.2))\n'))
    beat.code_blocks = [(2, 3)]
    calls = beat_transitions(beat)
    assert [c.expr for c in calls] == ['Dissolve(0.1)']


@pytest.mark.parametrize('text', [
    'with',                 # 关键字单独一行：不是转场名
    'with 30',              # 数字不是转场
    'with Dissolve(',       # 括号配不平
    'with Dissolve(0.5',    # 括号配不平（单边）
    '"with fade"',          # 整段是字符串
    '# with fade',          # 整行注释
])
def test_unrecognized_with_clauses_are_not_guessed(text):
    assert parse_with_clause(text) == []


def test_with_in_comment_does_not_add_a_second_transition():
    """`with fade # with dissolve`：只算前面那个真的 `with`。"""
    calls = parse_with_clause('with fade  # with dissolve')
    assert [c.name for c in calls] == ['fade']


@pytest.mark.parametrize('code', [
    'renpy.transition()',
    'renpy.transition(None)',
    'renpy.transition(a + b)',
    'renpy.transition(Dissolve(0.1)',
    'renpy.transition(lambda: 1)',
])
def test_unrecognized_code_calls_are_not_guessed(code):
    assert parse_python_transition(code) == []


def test_parse_expression_only_accepts_safe_literals():
    """安全解析器只认字面量/名字/调用；运算、推导式、属性链 → None。"""
    assert parse_expression('Dissolve(0.5)') is not None
    assert parse_expression('MultipleTransition([False, Dissolve(1.0)])') \
        is not None
    for bad in ('a + b', 'lambda x: x', '[x for x in y]',
                'open("f").read()', '__import__("os").system("x")',
                'Dissolve(0.5) or Fade(1.0)'):
        assert parse_expression(bad) is None, bad


# ---------------------------------------------------------------------------
#  ③ 不猜
# ---------------------------------------------------------------------------

def test_non_literal_argument_is_marked_not_guessed():
    """`Dissolve(d)` 的 `d` 是变量 —— 不能变成字符串 'd' 当值用。"""
    calls = parse_python_transition(
        'renpy.transition(Dissolve(d), layer="master")')
    assert len(calls) == 1
    assert calls[0].args == (NonLiteral('d'),)
    assert calls[0].name == 'Dissolve'


# ---------------------------------------------------------------------------
#  ④ 三种来源与 beat 派生
# ---------------------------------------------------------------------------

def test_independent_with_line_has_source_with():
    calls = parse_with_clause('\n    with Dissolve(0.5)\n')
    assert len(calls) == 1
    assert (calls[0].expr, calls[0].source) == ('Dissolve(0.5)', SOURCE_WITH)
    assert calls[0].line == 2
    assert calls[0].args == (0.5,)


def test_inline_with_clause_has_source_inline_with():
    calls = parse_with_clause('show e happy with Dissolve(0.5)')
    assert len(calls) == 1
    assert calls[0].source == SOURCE_INLINE_WITH
    assert calls[0].expr == 'Dissolve(0.5)'
    assert parse_with_clause('scene bg room with 场景淡入')[0].name == '场景淡入'


def test_bare_expression_mode_for_beat_with_field():
    """`Beat.with_` 里只有表达式（没有 `with` 关键字），也要能解析。"""
    calls = parse_with_clause('dissolve')
    assert [(c.expr, c.source) for c in calls] == [('dissolve', SOURCE_WITH)]
    assert parse_with_clause('Dissolve(0.25)')[0].args == (0.25,)


def test_beat_transitions_combines_with_and_code():
    beat = Beat(line_id='x', with_='dissolve',
                code='renpy.transition(Dissolve(0.1), layer="master")')
    calls = beat_transitions(beat)
    assert [c.source for c in calls] == [SOURCE_INLINE_WITH, SOURCE_RENPY]
    assert [c.expr for c in calls] == ['dissolve', 'Dissolve(0.1)']
    assert all(c.line == 0 for c in calls), \
        'beat 级别派生没有可靠的源码行号，一律 0（契约：0=未知）'


def test_beat_transitions_reads_independent_with_in_raw():
    beat = Beat(line_id='y',
                raw='with fade\npause 0.5\nshow screen prompt')
    calls = beat_transitions(beat)
    assert [(c.expr, c.source) for c in calls] == [('fade', SOURCE_WITH)]


def test_beat_transitions_is_pure():
    beat = Beat(line_id='z', with_='dissolve',
                code='renpy.transition(Dissolve(0.1))',
                raw='with fade', text='hi', speaker='e')
    before = beat.to_row()
    beat_transitions(beat)
    assert beat.to_row() == before, '派生必须只读：Beat 一个字段都不许动'


def test_module_never_evals_project_code():
    """静态守卫：实现里不许出现 `eval` / `exec` / `literal_eval`。"""
    source = Path(transition_expr.__file__).read_text(encoding='utf-8')
    for forbidden in ('eval(', 'exec(', 'literal_eval'):
        assert forbidden not in source, f'禁用 {forbidden}（只做字面量解析）'


def test_normalize_expression_collapses_multiline():
    raw = ('MultipleTransition([\n'
           '    False, Dissolve(1.0),\n'
           '    True])  # 注释')
    assert normalize_expression(raw) == \
        'MultipleTransition([False, Dissolve(1.0), True])'
    assert normalize_expression('Dissolve( 0.1 )') == 'Dissolve(0.1)'
