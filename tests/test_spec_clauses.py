"""
tests/test_spec_clauses.py
==========================
「复杂子句」与「技术调用」的判据（2026-10-30）。

这一轮补的是用户点名的两件事：

  · 自定义语句除了位置参数，还能声明 `at …` / `behind …` / `with …` 这类
    **关键字子句**（顺序可变、可以不出现、值可以是带空格的表达式）；
  · `$ renpy.transition(...)` / 项目自己写的包装函数这类**技术调用**，
    也能长出可编辑的参数格，并且预览按声明的效果演。

纪律（与 `spec_args` / `transition_expr` 同源，这里逐条钉住）：
  · 值没变 → **逐字节原样**（铁律 #6）；
  · 改一格 → 只动那一段，别的一个字节不动；
  · 认不出 / 多条同名调用 → **不猜、不写**；
  · 声明只是"多一双眼睛"，没声明时剧本原样保留。
"""

from __future__ import annotations

import json

import pytest

from storygraph.story import call_expr
from storygraph.story.beat_model import Beat, fold_spec_arg_columns
from storygraph.story.spec_clauses import (CLAUSE_KINDS, clause_spans,
                                           clause_values, normalize_clauses,
                                           set_clause_value)
from storygraph.story.statement_registry import (StatementRegistry, clear_cache,
                                                 declare_calls,
                                                 declare_statement,
                                                 dynamic_spec_columns)


@pytest.fixture(autouse=True)
def _fresh_registry():
    clear_cache()
    yield
    clear_cache()


CLAUSES = normalize_clauses([
    {'keyword': 'at', 'name': '位置', 'kind': 'words'},
    {'keyword': 'behind', 'name': '挡住', 'kind': 'word'},
    {'keyword': 'with', 'name': '转场', 'kind': 'transition'},
    {'keyword': 'zorder', 'name': '叠序', 'kind': 'number'},
])


def _write_project(root, statements=(), calls=()):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'game').mkdir(parents=True, exist_ok=True)
    (root / 'game' / 'script.rpy').write_text(
        'label start:\n    return\n', encoding='utf-8')
    data = {}
    if statements:
        data['statements'] = list(statements)
    if calls:
        data['calls'] = list(calls)
    (root / 'storygraph.rules.json').write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')
    return root


# ---------------------------------------------------------------------------
#  声明规范化
# ---------------------------------------------------------------------------

def test_normalize_clauses_keeps_order_and_defaults():
    got = normalize_clauses([
        {'keyword': 'at', 'name': '位置', 'kind': 'words'},
        {'keyword': 'with', 'name': '转场'},
    ])
    assert [c['keyword'] for c in got] == ['at', 'with']
    assert got[0]['kind'] == 'words'
    assert got[1]['kind'] == 'word'          # 没写 → 一个词（最保守）
    assert got[1]['optional'] is True


def test_normalize_clauses_drops_bad_and_duplicate_rows_with_warnings():
    warnings = []
    got = normalize_clauses([
        {'keyword': 'bad key', 'name': 'x'},
        {'keyword': 'at', 'name': '位置'},
        {'keyword': 'at', 'name': '重复'},
        {'keyword': 'behind', 'name': '位置'},        # 列名重复
    ], warnings=warnings)
    assert [c['keyword'] for c in got] == ['at']
    assert len(warnings) == 3


def test_clause_kinds_are_the_documented_set():
    assert CLAUSE_KINDS == ('word', 'words', 'expr', 'transition', 'number')


# ---------------------------------------------------------------------------
#  读值
# ---------------------------------------------------------------------------

def test_reads_all_clause_kinds_from_one_line():
    head = ('mycue "开场" at 左, 右 behind ye '
            'with Dissolve( 0.3 ) zorder 5')
    got = clause_values(head, CLAUSES)
    assert got == {'位置': '左, 右', '挡住': 'ye',
                   '转场': 'Dissolve( 0.3 )', '叠序': '5'}


def test_quoted_and_parenthesized_keywords_are_not_clauses():
    assert clause_values('mycue "a at b" with Dissolve(0.3)', CLAUSES) == {
        '转场': 'Dissolve(0.3)'}
    assert clause_values('mycue "a at b"', CLAUSES) == {}


def test_comment_after_head_is_not_a_clause():
    assert clause_values('mycue "x" # at left', CLAUSES) == {}


def test_block_colon_is_not_part_of_the_last_value():
    assert clause_values('mycue at left:', CLAUSES) == {'位置': 'left'}
    spans = clause_spans('mycue at left:', CLAUSES)
    assert spans[0]['value_end'] == len('mycue at left')


def test_a_later_declared_keyword_terminates_the_previous_value():
    # 只声明 `at` 时它会一路吞到行尾；声明了 `with` 就必须在 with 前停。
    only_at = normalize_clauses([{'keyword': 'at', 'name': '位置',
                                  'kind': 'words'}])
    line = 'mycue at 左 with Dissolve(0.3)'
    assert clause_values(line, only_at)['位置'] == '左 with Dissolve(0.3)'
    assert clause_values(line, CLAUSES)['位置'] == '左'


# ---------------------------------------------------------------------------
#  写回：不变、改、插、删
# ---------------------------------------------------------------------------

def test_unchanged_value_returns_the_exact_same_string():
    head = 'mycue "开场" at 左,  右  with Dissolve( 0.3 )'
    assert set_clause_value(head, CLAUSES, '位置', '左,  右') is head


def test_changing_a_clause_only_touches_that_value():
    head = 'mycue "开场" at 左, 右 behind ye with Dissolve( 0.3 )'
    got = set_clause_value(head, CLAUSES, '转场', 'Fade(0.5, 0, 0.5)')
    assert got == ('mycue "开场" at 左, 右 behind ye '
                   'with Fade(0.5, 0, 0.5)')


def test_inserting_a_missing_clause_uses_declaration_order():
    head = 'mycue "开场" behind ye'
    got = set_clause_value(head, CLAUSES, '转场', 'Dissolve(0.3)')
    # `with` 在声明里排在 behind 后面 → 插在 behind 之后
    assert got == 'mycue "开场" behind ye with Dissolve(0.3)'


def test_inserting_before_block_colon_keeps_the_colon_last():
    assert set_clause_value('mycue "x":', CLAUSES, '位置', 'left') == \
        'mycue "x" at left:'


def test_clearing_a_clause_removes_keyword_and_one_separator():
    head = 'mycue "x" at left behind ye with Dissolve(0.3)'
    got = set_clause_value(head, CLAUSES, '挡住', '')
    assert got == 'mycue "x" at left with Dissolve(0.3)'


def test_unknown_clause_name_is_a_no_op():
    head = 'mycue "x"'
    assert set_clause_value(head, CLAUSES, '不存在', 'v') is head


def test_comment_is_preserved_when_inserting_a_clause():
    assert set_clause_value('mycue "x" # note', CLAUSES, '位置', 'left') == \
        'mycue "x" at left # note'


# ---------------------------------------------------------------------------
#  声明 → 卡片/Excel 的动态列 → 折回
# ---------------------------------------------------------------------------

def _project_with_clauses(tmp_path):
    root = _write_project(tmp_path / 'proj')
    res = declare_statement(root, 'mycue', label='提示',
                            columns=[{'name': '标题'}],
                            pattern=[
                                {'kind': 'keyword', 'text': 'mycue'},
                                {'kind': 'variable', 'column': '标题'},
                            ],
                            clauses=CLAUSES)
    assert res['ok'], res
    clear_cache()
    return root


def _beat_with_head(head):
    return Beat(seq=1, label='start', kind='say', spec_name='mycue',
                spec_head=head, opaque=head)


def test_clause_columns_are_dynamic_headers_with_the_keyword(tmp_path):
    root = _project_with_clauses(tmp_path)
    reg = StatementRegistry.for_project(root)
    cols = dynamic_spec_columns([_beat_with_head('mycue "x" at left')], reg)
    headers = [c['header'] for c in cols]
    assert 'mycue·标题@1' in headers
    assert 'mycue·位置~at' in headers
    assert 'mycue·转场~with' in headers


def test_card_reads_and_writes_clause_values(tmp_path):
    root = _project_with_clauses(tmp_path)
    reg = StatementRegistry.for_project(root)
    head = 'mycue "开场" at 左 behind ye with Dissolve(0.3)'
    beat = _beat_with_head(head)
    cols = dynamic_spec_columns([beat], reg)
    values = dict(zip([c['header'] for c in cols], beat.spec_arg_cells(cols)))
    assert values['mycue·位置~at'] == '左'
    assert values['mycue·转场~with'] == 'Dissolve(0.3)'
    assert values['mycue·挡住~behind'] == 'ye'

    fold_spec_arg_columns(beat, {
        'mycue·位置~at': '右',
        'mycue·转场~with': 'Fade(0.5, 0, 0.5)',
    })
    assert beat.spec_head == ('mycue "开场" at 右 behind ye '
                              'with Fade(0.5, 0, 0.5)')


def test_folding_unchanged_clause_values_is_byte_exact(tmp_path):
    root = _project_with_clauses(tmp_path)
    reg = StatementRegistry.for_project(root)
    head = 'mycue "开场" at 左,  右  with Dissolve( 0.3 )'
    beat = _beat_with_head(head)
    cols = dynamic_spec_columns([beat], reg)
    before = beat.spec_head
    fold_spec_arg_columns(beat, {
        'mycue·位置~at': '左,  右',
        'mycue·转场~with': 'Dissolve( 0.3 )',
    })
    assert beat.spec_head == before


def test_parenthetical_transition_is_not_split_by_the_pattern_columns(tmp_path):
    root = _project_with_clauses(tmp_path)
    reg = StatementRegistry.for_project(root)
    head = ('mycue "x" with MultipleTransition([(False, Dissolve(0.2)), '
            '(True, ImageDissolve("x.png", 0.4))])')
    beat = _beat_with_head(head)
    cols = dynamic_spec_columns([beat], reg)
    values = dict(zip([c['header'] for c in cols], beat.spec_arg_cells(cols)))
    assert values['mycue·转场~with'] == (
        'MultipleTransition([(False, Dissolve(0.2)), '
        '(True, ImageDissolve("x.png", 0.4))])')


def test_builtin_show_can_declare_extra_clauses(tmp_path):
    """内置 `show` 也能声明自己的子句（用户点名："连 show 也能长参数格"）。"""
    root = _write_project(tmp_path / 'proj', statements=[{
        'name': 'show',
        'clauses': [
            {'keyword': 'at', 'name': '位置', 'kind': 'words'},
            {'keyword': 'behind', 'name': '挡住', 'kind': 'word'},
        ],
    }])
    clear_cache()
    reg = StatementRegistry.for_project(root)
    beat = Beat(seq=1, label='start', show='lan happy at left behind ye')
    cols = dynamic_spec_columns([beat], reg)
    headers = [c['header'] for c in cols]
    assert 'show·位置~at' in headers
    assert 'show·挡住~behind' in headers
    assert dict(zip(headers, beat.spec_arg_cells(cols)))['show·位置~at'] == 'left'
    fold_spec_arg_columns(beat, {'show·位置~at': 'right'})
    assert beat.show == 'lan happy at right behind ye'


def test_from_row_folds_clause_columns_without_a_registry():
    """Excel 那条路没有项目根：列名里的 `~关键字` 就够改/删了。"""
    row = {
        'Seq': 1, 'Label': 'start', 'Kind': 'say', 'Text': '一',
        'SpecName': 'mycue',
        'SpecHead': 'mycue "开场" at 左 with Dissolve(0.3)',
        'Opaque': 'mycue "开场" at 左 with Dissolve(0.3)',
        'mycue·位置~at': '右',
        'mycue·转场~with': 'Fade(0.5, 0, 0.5)',
    }
    beat = Beat.from_row(row)
    assert beat.spec_head == ('mycue "开场" at 右 '
                              'with Fade(0.5, 0, 0.5)')


# ---------------------------------------------------------------------------
#  技术调用：读 / 写 / 多次不猜
# ---------------------------------------------------------------------------

def test_call_expr_reads_positional_and_keyword_args():
    code = 'renpy.transition(Dissolve(0.1), layer="master")'
    assert call_expr.read_arg(code, 'renpy.transition', position=0,
                              unquote=False) == 'Dissolve(0.1)'
    assert call_expr.read_arg(code, 'renpy.transition', kw='layer') == 'master'


def test_call_expr_changes_only_the_argument():
    code = 'renpy.transition(Dissolve(0.1), layer="master")'
    got = call_expr.set_arg(code, 'renpy.transition', position=0,
                            value='Fade(0.5, 0, 0.5)')
    assert got == 'renpy.transition(Fade(0.5, 0, 0.5), layer="master")'


def test_call_expr_keeps_the_quote_style_of_a_keyword_arg():
    code = 'renpy.transition(Dissolve(0.1), layer="master")'
    got = call_expr.set_arg(code, 'renpy.transition', kw='layer',
                            value='screens')
    assert got == 'renpy.transition(Dissolve(0.1), layer="screens")'


def test_call_expr_clears_an_argument_and_its_comma():
    code = 'renpy.transition(Dissolve(0.1), layer="master")'
    assert call_expr.set_arg(code, 'renpy.transition', kw='layer',
                             value='') == 'renpy.transition(Dissolve(0.1))'


def test_call_expr_does_not_guess_when_the_same_head_appears_twice():
    code = ('renpy.transition(Dissolve(0.1))\n'
            'renpy.transition(Fade(0.5))')
    assert call_expr.read_arg(code, 'renpy.transition', position=0) == ''
    assert call_expr.set_arg(code, 'renpy.transition', position=0,
                             value='Fade(1)') == code


def test_call_expr_ignores_comments_and_strings():
    code = ('# renpy.transition(Dissolve(0.1))\n'
            'renpy.notify("renpy.transition(x)")\n'
            'renpy.transition(Dissolve(0.2))')
    hits = call_expr.find_calls(code, 'renpy.transition')
    assert len(hits) == 1
    assert call_expr.read_arg(code, 'renpy.transition', position=0) == \
        'Dissolve(0.2)'


def test_call_expr_unchanged_value_is_byte_exact():
    code = 'renpy.transition( Dissolve( 0.1 ) , layer="master" )'
    assert call_expr.set_arg(code, 'renpy.transition', position=0,
                             value='Dissolve( 0.1 )') is code


# ---------------------------------------------------------------------------
#  技术调用的动态列（内置 renpy.transition）
# ---------------------------------------------------------------------------

def test_builtin_transition_call_gets_editable_columns():
    beat = Beat(seq=1, label='start', kind='say',
                code='renpy.transition(Dissolve(0.1), layer="master")')
    cols = dynamic_spec_columns([beat], StatementRegistry())
    headers = [c['header'] for c in cols]
    assert 'renpy.transition·transition@0' in headers
    assert 'renpy.transition·layer@kw' in headers
    values = dict(zip(headers, beat.spec_arg_cells(cols)))
    assert values['renpy.transition·transition@0'] == 'Dissolve(0.1)'
    assert values['renpy.transition·layer@kw'] == 'master'


def test_editing_a_transition_call_updates_code_and_ordered():
    old = 'renpy.transition(Dissolve(0.1), layer="master")'
    beat = Beat(seq=1, label='start', kind='say', code=old,
                ordered=[('code', old)])
    fold_spec_arg_columns(beat, {'renpy.transition·transition@0':
                                 'Fade(0.5, 0, 0.5)'})
    assert beat.code == \
        'renpy.transition(Fade(0.5, 0, 0.5), layer="master")'
    assert beat.ordered == [('code', beat.code)]


def test_excel_roundtrip_of_transition_call_columns(tmp_path):
    """Excel 里改 `renpy.transition` 的转场格 → 读回来只改那一行。"""
    from openpyxl import load_workbook

    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel

    beat = Beat(seq=1, label='start', kind='say', text='一',
                code='renpy.transition(Dissolve(0.1), layer="master")')
    path = tmp_path / 'calls.xlsx'
    export_beats_to_excel([beat], path)
    wb = load_workbook(path)
    ws = wb['Beats']
    headers = [c.value for c in ws[1]]
    col = headers.index('renpy.transition·transition@0') + 1
    ws.cell(row=2, column=col, value='Fade(0.5, 0, 0.5)')
    wb.save(path)
    back = import_beats_from_excel(path)
    assert back[0].code == \
        'renpy.transition(Fade(0.5, 0, 0.5), layer="master")'


def test_two_transition_calls_in_one_beat_get_no_columns():
    beat = Beat(seq=1, label='start', kind='say',
                code=('renpy.transition(Dissolve(0.1))\n'
                      'renpy.transition(Fade(0.5))'))
    assert dynamic_spec_columns([beat], StatementRegistry()) == []


def test_a_call_inside_a_python_block_gets_no_columns():
    """`python:` 块体里的调用是"循环/条件里可能执行"，不是这一拍固定过场。"""
    from storygraph.story.beat_model import (CODE_BLOCK_BODY,
                                             CODE_BLOCK_OPEN)
    beat = Beat(seq=1, label='start', kind='say',
                code=(f'{CODE_BLOCK_OPEN}renpy.transition(Dissolve(0.1))\n'
                      f'{CODE_BLOCK_BODY}renpy.transition(Fade(0.5))'))
    assert call_expr.find_calls(beat.code, 'renpy.transition') == []
    assert dynamic_spec_columns([beat], StatementRegistry()) == []


def test_custom_call_declaration_reaches_columns_and_rules(tmp_path):
    root = _write_project(tmp_path / 'proj')
    res = declare_calls(root, [{
        'head': 'my_wipe', 'label': '擦除',
        'columns': [{'name': '转场', 'position': 0, 'kind': 'transition'}],
        'effect': 'Dissolve', 'seconds_arg': 1,
    }])
    assert res['ok'], res
    clear_cache()
    reg = StatementRegistry.for_project(root)
    beat = Beat(seq=1, label='start', kind='say',
                code='my_wipe("left", 0.6)')
    cols = dynamic_spec_columns([beat], reg)
    headers = [c['header'] for c in cols]
    assert 'my_wipe·转场@0' in headers
    assert beat.spec_arg_cells(cols) == ['"left"']
    fold_spec_arg_columns(beat, {'my_wipe·转场@0': '"right"'})
    assert beat.code == 'my_wipe("right", 0.6)'


# ---------------------------------------------------------------------------
#  预览：声明过的转场子句 / 技术调用
# ---------------------------------------------------------------------------

def test_declared_with_clause_drives_the_stage_preview(tmp_path):
    from storygraph.app.stage import build_frames

    root = _write_project(tmp_path / 'proj', statements=[{
        'name': 'mycue',
        'pattern': [{'kind': 'keyword', 'text': 'mycue'},
                    {'kind': 'variable', 'column': '标题'}],
        'clauses': [{'keyword': 'with', 'name': '转场',
                     'kind': 'transition'}],
    }])
    clear_cache()
    beat = Beat(seq=1, label='start', kind='say', text='一',
                spec_name='mycue', spec_head='mycue "x" with Dissolve(0.3)',
                opaque='mycue "x" with Dissolve(0.3)')
    frame = build_frames([beat], project_root=root)[0]
    assert len(frame.transitions) == 1
    assert frame.transitions[0].source == 'declared-clause'
    assert frame.effects[0].duration == pytest.approx(0.3)


def test_declared_call_effect_drives_the_stage_preview(tmp_path):
    from storygraph.app.stage import build_frames

    root = _write_project(tmp_path / 'proj', calls=[{
        'head': 'my_wipe', 'effect': 'Dissolve', 'seconds_arg': 1,
        'columns': [{'name': '转场', 'position': 0}],
    }])
    clear_cache()
    beat = Beat(seq=1, label='start', kind='say', text='一',
                code='my_wipe("left", 0.6)')
    frame = build_frames([beat], project_root=root)[0]
    assert frame.transitions[0].source == 'declared-call'
    assert frame.effects[0].duration == pytest.approx(0.6)


# ---------------------------------------------------------------------------
#  UI：声明弹窗的子句表 / 技术调用小窗
# ---------------------------------------------------------------------------

def test_dialog_form_round_trips_clauses(tmp_path, qapp):
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog

    root = _write_project(tmp_path / 'proj')
    dlg = CustomStatementDialog(None, project_root=root, discovered=[],
                                declared_groups=[])
    dlg.name.setText('mycue')
    dlg._add_clause_row('with', '转场', 'transition', True)
    built = dlg.built()
    assert built['clauses'] == [{'keyword': 'with', 'name': '转场',
                                 'label': '', 'kind': 'transition',
                                 'optional': True, 'choices': []}]
    assert dlg._save_statement(close=False) is True
    data = json.loads((root / 'storygraph.rules.json').read_text(
        encoding='utf-8'))
    assert data['statements'][0]['clauses'][0]['keyword'] == 'with'


def test_call_dialog_loads_and_saves_declarations(tmp_path, qapp):
    from storygraph.ui_qt.dialog_calls import CallDialog

    root = _write_project(tmp_path / 'proj', calls=[{
        'head': 'my_wipe', 'effect': 'Dissolve', 'seconds_arg': 1,
        'columns': [{'name': '转场', 'position': 0}],
    }])
    clear_cache()
    dlg = CallDialog(None, project_root=root)
    assert dlg.table.rowCount() == 1
    assert dlg.built_calls()[0]['head'] == 'my_wipe'
    # 第 2 列 = 等价效果（第 1 列是 2026-10-31 加的「中文名」）
    dlg.table.item(0, 2).setText('Fade')
    dlg._save()
    clear_cache()
    reg = StatementRegistry.for_project(root)
    assert reg.calls[0]['effect'] == 'Fade'
