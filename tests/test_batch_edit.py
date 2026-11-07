# -*- coding: utf-8 -*-
"""
通用批处理编辑（`app.batch_edit` + `cli batch`）的判据。

覆盖的是"规则"本身，不是某个项目的某一章：
  · 选谁（sprite show / hide / say / 转场 / 说话人 / 标签）
  · 做什么（insert / replace / delete；单行、整块、到结束行为止）
  · 在哪（最后一条 show 后 / 拍末 / 拍前；ATL 块后；块体/块头）
  · 写什么（字面一行或几行；已有就跳过；幂等）
  · 写盘（预演不写、原子替换、CRLF/BOM 不变、文件被改过就拒绝）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app.batch_edit import (BatchRuleError, SCHEMA, apply_plan,
                                       format_diff, load_rule_file, plan_batch)
from storygraph.cli import main

CUE = '$ renpy.transition(Dissolve(0.1), layer="master")'

CHAPTER = '''\
label start:
    scene bg room
    show cg cg01
    "cg line"

    show ye happy
    y "hello"

    show lan sad
    $ renpy.transition(Dissolve(0.1), layer="master")
    l "already"

    hide ye
    show ye angry
    y "angry"

    show ye shy:
        xalign 0.5
        ease 0.2 alpha 1.0
    y "atl"
'''

RULE = {
    'schema': SCHEMA,
    'rules': [{
        'id': 'cue',
        'when': {'show': 'sprite', 'transition': 'none'},
        'insert': {'placement': 'after_last_show', 'text': CUE},
    }],
}


def _write_project(tmp_path, text: str = CHAPTER, name: str = 'ch01.rpy',
                   raw: bytes | None = None):
    root = tmp_path / 'proj'
    chapter = root / 'game' / 'chapter'
    chapter.mkdir(parents=True)
    path = chapter / name
    path.write_bytes(raw if raw is not None else text.encode('utf-8'))
    return root, path


def _write_rule(tmp_path, rule: dict | None = None) -> Path:
    path = tmp_path / 'rule.json'
    path.write_text(json.dumps(rule or RULE, ensure_ascii=False),
                    encoding='utf-8')
    return path


def _rules(tmp_path, rule: dict | None = None):
    return load_rule_file(_write_rule(tmp_path, rule))


def _src_lines(path: Path) -> list:
    return path.read_text(encoding='utf-8').split('\n')


def test_plan_inserts_after_last_sprite_show_and_after_atl_block(tmp_path):
    root, _src = _write_project(tmp_path)
    plan = plan_batch(root, _rules(tmp_path),
                      chapters=['game/chapter/*.rpy'])
    assert plan.errors == []
    assert plan.pending == 3
    got = [(x.line, x.before) for x in plan.files[0].insertions]
    assert got == [(6, False), (14, False), (19, False)]
    lines = _src_lines(_src)
    assert lines[5].strip() == 'show ye happy'
    assert lines[13].strip() == 'show ye angry'
    assert lines[18].strip().startswith('ease ')   # ATL 块尾，不是块里


def test_plan_does_not_touch_disk(tmp_path):
    root, src = _write_project(tmp_path)
    before = src.read_bytes()
    plan_batch(root, _rules(tmp_path), chapters=['game/chapter/*.rpy'])
    assert src.read_bytes() == before


def test_diff_preview_shows_the_inserted_line(tmp_path):
    root, _src = _write_project(tmp_path)
    plan = plan_batch(root, _rules(tmp_path), chapters=['game/chapter/*.rpy'])
    diff = format_diff(plan)
    assert '--- game/chapter/ch01.rpy (before)' in diff
    assert '+' + '    ' + CUE in diff


def test_apply_writes_and_is_idempotent(tmp_path):
    root, src = _write_project(tmp_path)
    plan = plan_batch(root, _rules(tmp_path), chapters=['game/chapter/*.rpy'])
    result = apply_plan(plan)
    assert result['written'] == ['game/chapter/ch01.rpy']
    assert result['inserted'] == 3
    text = src.read_text(encoding='utf-8')
    assert text.count(CUE) == 4                    # 原有 1 + 新 3
    assert text.count('\n') == CHAPTER.count('\n') + 3

    again = plan_batch(root, _rules(tmp_path), chapters=['game/chapter/*.rpy'])
    assert again.pending == 0


def test_existing_line_is_skipped_without_transition_condition(tmp_path):
    root, _src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        f'    {CUE}\n'
        '    y "hi"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'cue',
            'when': {'show': 'sprite'},
            'insert': {'placement': 'after_last_show', 'text': CUE},
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.pending == 0
    assert plan.files[0].skipped[0]['reason'] == '已经有同一行'


def test_before_beat_and_when_speaker(tmp_path):
    root, _src = _write_project(tmp_path)
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'before-l',
            'when': {'say': True, 'speaker': 'l', 'text_contains': 'already'},
            'insert': {'placement': 'before_beat', 'text': '# before l'},
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.pending == 1
    ins = plan.files[0].insertions[0]
    assert ins.before is True
    assert _src_lines(_src)[ins.line - 1].strip() == 'show lan sad'


def test_after_beat_inserts_after_the_last_line(tmp_path):
    root, _src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        '    y "hi"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'after',
            'when': {'speaker': 'y'},
            'insert': {'placement': 'after_beat', 'text': '# after'},
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.pending == 1
    ins = plan.files[0].insertions[0]
    assert _src_lines(_src)[ins.line - 1].strip() == 'y "hi"'


def test_tag_kind_any_can_target_a_cg_show(tmp_path):
    root, _src = _write_project(tmp_path)
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'cg',
            'when': {'show': ['cg']},
            'insert': {'placement': 'after_last_show', 'tag_kind': 'any',
                       'text': '# cg cue'},
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.pending == 1
    ins = plan.files[0].insertions[0]
    assert _src_lines(_src)[ins.line - 1].strip() == 'show cg cg01'


def test_hide_only_can_be_a_target(tmp_path):
    root, _src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        '    y "hi"\n'
        '    hide ye\n'
        '    "gone"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'hide',
            'when': {'hide': 'sprite'},
            'insert': {'placement': 'after_last_change', 'text': '# hidden'},
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.pending == 1
    ins = plan.files[0].insertions[0]
    assert _src_lines(_src)[ins.line - 1].strip() == 'hide ye'


def test_change_condition_covers_hide_only(tmp_path):
    root, _src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        '    y "hi"\n'
        '    hide ye\n'
        '    "gone"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'change',
            'when': {'change': 'sprite'},
            'insert': {'placement': 'after_last_change', 'text': '# changed'},
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.pending == 2      # show 那一拍 + hide 那一拍
    assert any(_src_lines(_src)[x.line - 1].strip() == 'hide ye'
               for x in plan.files[0].insertions)


def test_null_condition_is_rejected(tmp_path):
    bad = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'bad',
            'when': {'show': None},
            'insert': {'placement': 'after_last_show', 'text': CUE},
        }],
    }
    with pytest.raises(BatchRuleError, match='不能是 null'):
        load_rule_file(_write_rule(tmp_path, bad))


def test_hide_with_counts_as_a_transition(tmp_path):
    root, _src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        '    hide ye with dissolve\n'
        '    y "hi"\n')
    plan = plan_batch(root, _rules(tmp_path), chapters=['game/chapter/*.rpy'])
    assert plan.pending == 0


def test_say_tail_with_counts_as_a_transition(tmp_path):
    root, _src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        '    y "hi" with dissolve\n')
    plan = plan_batch(root, _rules(tmp_path), chapters=['game/chapter/*.rpy'])
    assert plan.pending == 0


def test_after_beat_does_not_land_inside_a_python_block(tmp_path):
    text = (
        'label start:\n'
        '    show ye happy\n'
        '    python:\n'
        '        x = 1\n'
    )
    root, _src = _write_project(tmp_path, text)
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'after',
            'when': {'show': 'sprite'},
            'insert': {'placement': 'after_beat', 'text': '# after'},
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.pending == 1
    ins = plan.files[0].insertions[0]
    assert ins.indent == '    '               # 块头缩进，不是块体缩进
    assert _src_lines(_src)[ins.line - 1].strip() == 'x = 1'


def test_python_block_text_that_looks_like_show_is_not_an_anchor(tmp_path):
    text = (
        'label start:\n'
        '    show ye happy\n'
        '    python:\n'
        '        text = """\n'
        'show ye happy\n'
        '"""\n'
        '    y "hi"\n'
    )
    root, src = _write_project(tmp_path, text)
    plan = plan_batch(root, _rules(tmp_path), chapters=['game/chapter/*.rpy'])
    assert plan.pending == 1
    ins = plan.files[0].insertions[0]
    assert ins.line == 2                       # 真 show，不是 Python 字符串里那行
    apply_plan(plan)
    assert '        text = """\nshow ye happy\n"""' in \
        src.read_text(encoding='utf-8')


def test_crlf_and_bom_are_preserved(tmp_path):
    raw = b'\xef\xbb\xbf' + CHAPTER.replace('\n', '\r\n').encode('utf-8')
    root, src = _write_project(tmp_path, raw=raw)
    plan = plan_batch(root, _rules(tmp_path), chapters=['game/chapter/*.rpy'])
    assert apply_plan(plan)['inserted'] == 3
    data = src.read_bytes()
    assert data.startswith(b'\xef\xbb\xbf')
    assert data.replace(b'\r\n', b'').count(b'\n') == 0
    assert b'\r\n' in data


def test_stale_file_is_refused(tmp_path):
    root, src = _write_project(tmp_path)
    plan = plan_batch(root, _rules(tmp_path), chapters=['game/chapter/*.rpy'])
    src.write_bytes(b'# changed outside\n' + src.read_bytes())
    result = apply_plan(plan)
    assert result['written'] == []
    assert result['failed'][0][0] == 'game/chapter/ch01.rpy'
    assert '又被改过' in result['failed'][0][1]
    assert src.read_bytes().startswith(b'# changed outside')


def test_unknown_rule_key_is_rejected(tmp_path):
    bad = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'bad',
            'when': {'shwo': 'sprite'},
            'insert': {'placement': 'after_last_show', 'text': CUE},
        }],
    }
    with pytest.raises(BatchRuleError, match='不认识的键'):
        load_rule_file(_write_rule(tmp_path, bad))


def test_rule_text_can_be_multiline(tmp_path):
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'multi',
            'when': {'show': 'sprite'},
            'insert': {'placement': 'after_last_show',
                       'text': ['$ data = {', '    "a": 1,', '}']},
        }],
    }
    got = load_rule_file(_write_rule(tmp_path, rule))
    assert got[0].text == '$ data = {\n    "a": 1,\n}'


def test_rule_text_block_header_needs_a_body_in_the_same_text(tmp_path):
    bad = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'bad',
            'when': {'show': 'sprite'},
            'insert': {'placement': 'after_last_show',
                       'text': ['if flag:', '    $ foo()', 'if other:']},
        }],
    }
    with pytest.raises(BatchRuleError, match='块头'):
        load_rule_file(_write_rule(tmp_path, bad))


def test_rule_text_cannot_be_a_block_header(tmp_path):
    bad = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'bad',
            'when': {'show': 'sprite'},
            'insert': {'placement': 'after_last_show', 'text': 'if x:'},
        }],
    }
    with pytest.raises(BatchRuleError, match='块头'):
        load_rule_file(_write_rule(tmp_path, bad))


def test_replace_line_in_beat_and_idempotent(tmp_path):
    root, src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        '    $ renpy.transition(Dissolve(0.1))\n'
        '    y "hi"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'slow',
            'when': {'show': 'sprite'},
            'replace': {
                'scope': 'after_last_show',
                'match': {'mode': 'contains',
                          'pattern': 'renpy.transition'},
                'text': '$ renpy.transition(Dissolve(0.5))',
            },
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.pending == 1
    ed = plan.files[0].edits[0]
    assert (ed.kind, ed.line) == ('replace', 3)
    result = apply_plan(plan)
    assert (result['replaced'], result['inserted'], result['deleted']) == \
        (1, 0, 0)
    assert '$ renpy.transition(Dissolve(0.5))' in src.read_text(encoding='utf-8')
    again = plan_batch(root, _rules(tmp_path, rule),
                       chapters=['game/chapter/*.rpy'])
    assert again.pending == 0


def test_delete_line_and_occurrence_all(tmp_path):
    root, src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        '    $ foo()\n'
        '    $ foo()\n'
        '    y "hi"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'drop',
            'when': {'show': 'sprite'},
            'delete': {
                'scope': 'beat',
                'match': {'mode': 'exact', 'pattern': '$ foo()'},
                'occurrence': 'all',
            },
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.pending == 2
    result = apply_plan(plan)
    assert result['deleted'] == 2
    assert '$ foo()' not in src.read_text(encoding='utf-8')


def test_replace_whole_block_with_multiline_text(tmp_path):
    root, src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy:\n'
        '        xalign 0.5\n'
        '        ease 0.2 alpha 1.0\n'
        '    y "hi"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'atl',
            'when': {'show': 'sprite'},
            'replace': {
                'scope': 'beat',
                'match': {'mode': 'exact', 'pattern': 'show ye happy:'},
                'span': 'block',
                'text': ['show ye happy:',
                         '    xalign 0.3',
                         '    ease 0.4 alpha 1.0'],
            },
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.errors == []
    assert plan.pending == 1
    ed = plan.files[0].edits[0]
    assert (ed.kind, ed.line, ed.last_line) == ('replace', 2, 4)
    result = apply_plan(plan)
    assert result['replaced'] == 3
    text = src.read_text(encoding='utf-8')
    assert 'xalign 0.3' in text and 'xalign 0.5' not in text
    assert 'ease 0.4 alpha 1.0' in text

    again = plan_batch(root, _rules(tmp_path, rule),
                       chapters=['game/chapter/*.rpy'])
    assert again.pending == 0
    assert again.files[0].skipped[0]['reason'] == '已经是目标内容'


def test_delete_whole_if_block_including_header(tmp_path):
    root, src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        '    if True:\n'
        '        $ foo()\n'
        '        $ bar()\n'
        '    y "hi"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'drop-if',
            'when': {},
            'delete': {
                'scope': 'beat',
                'match': {'mode': 'exact', 'pattern': 'if True:'},
                'span': 'block',
            },
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.errors == []
    assert plan.pending == 1
    ed = plan.files[0].edits[0]
    assert (ed.line, ed.last_line) == (3, 5)
    result = apply_plan(plan)
    assert result['deleted'] == 3
    text = src.read_text(encoding='utf-8')
    assert 'if True:' not in text and 'foo()' not in text and 'bar()' not in text
    assert 'show ye happy' in text and 'y "hi"' in text


def test_until_span_replaces_a_multiline_statement(tmp_path):
    root, src = _write_project(
        tmp_path,
        'label start:\n'
        '    $ data = {\n'
        '        "a": 1,\n'
        '        "b": 2,\n'
        '    }\n'
        '    y "hi"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'dict',
            'when': {'code_contains': 'data = {'},
            'replace': {
                'scope': 'beat',
                'match': {'mode': 'contains', 'pattern': '$ data = {'},
                'span': {'until': {'mode': 'exact', 'pattern': '}'}},
                'text': ['$ data = {', '    "a": 9,', '}'],
            },
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.errors == []
    assert plan.pending == 1
    ed = plan.files[0].edits[0]
    assert (ed.line, ed.last_line) == (2, 5)
    assert apply_plan(plan)['replaced'] == 4
    text = src.read_text(encoding='utf-8')
    assert '"a": 9' in text and '"b": 2' not in text

    again = plan_batch(root, _rules(tmp_path, rule),
                       chapters=['game/chapter/*.rpy'])
    assert again.pending == 0


def test_until_span_cutting_a_block_is_refused(tmp_path):
    root, _src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        '    if True:\n'
        '        $ foo()\n'
        '        $ bar()\n'
        '    y "hi"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'cut',
            'when': {},
            'delete': {
                'scope': 'beat',
                'match': {'mode': 'exact', 'pattern': 'if True:'},
                'span': {'until': {'mode': 'exact', 'pattern': '$ foo()'}},
            },
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.errors == []
    assert plan.pending == 0
    assert any('切断' in sk['reason'] for sk in plan.files[0].skipped)


def test_multiline_insert_gets_anchor_indent_and_is_idempotent(tmp_path):
    root, src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        '    y "hi"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'dict-insert',
            'when': {'show': 'sprite'},
            'insert': {'placement': 'after_last_show',
                       'text': ['$ data = {', '    "a": 1,', '}']},
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.pending == 1
    assert apply_plan(plan)['inserted'] == 3
    lines = _src_lines(src)
    assert lines[2] == '    $ data = {'
    assert lines[3] == '        "a": 1,'
    assert lines[4] == '    }'

    again = plan_batch(root, _rules(tmp_path, rule),
                       chapters=['game/chapter/*.rpy'])
    assert again.pending == 0
    assert again.files[0].skipped[0]['reason'] == '已经有同一段'


def test_multiline_insert_can_include_a_complete_block(tmp_path):
    root, src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        '    y "hi"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'if-insert',
            'when': {'show': 'sprite'},
            'insert': {'placement': 'after_last_show',
                       'text': ['if flag:', '    $ foo()']},
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.pending == 1
    assert apply_plan(plan)['inserted'] == 2
    lines = _src_lines(src)
    assert lines[2] == '    if flag:'
    assert lines[3] == '        $ foo()'

    again = plan_batch(root, _rules(tmp_path, rule),
                       chapters=['game/chapter/*.rpy'])
    assert again.pending == 0


def test_overlapping_spans_from_two_rules_refuse_the_file(tmp_path):
    root, _src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        '    if True:\n'
        '        $ foo()\n'
        '        $ bar()\n'
        '    y "hi"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [
            {'id': 'a', 'when': {},
             'delete': {'scope': 'beat',
                        'match': {'mode': 'exact', 'pattern': 'if True:'},
                        'span': 'block'}},
            {'id': 'b', 'when': {},
             'replace': {'scope': 'beat',
                         'match': {'mode': 'exact', 'pattern': '$ foo()'},
                         'text': '$ baz()'}},
        ],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.errors
    assert '重叠' in plan.errors[0]


def test_menu_block_is_one_edit_even_when_several_beats_point_at_it(tmp_path):
    root, src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        '    menu:\n'
        '        "A":\n'
        '            $ a = 1\n'
        '        "B":\n'
        '            $ b = 2\n'
        '    y "hi"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'menu',
            'when': {},
            'delete': {
                'scope': 'beat',
                'match': {'mode': 'exact', 'pattern': 'menu:'},
                'span': 'block',
            },
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.errors == []
    assert plan.pending == 1
    assert (plan.files[0].edits[0].line,
            plan.files[0].edits[0].last_line) == (3, 7)
    assert apply_plan(plan)['deleted'] == 5
    assert 'menu:' not in src.read_text(encoding='utf-8')


def test_rule_file_with_bom_is_accepted(tmp_path):
    path = tmp_path / 'rule.json'
    path.write_bytes(b'\xef\xbb\xbf' + json.dumps(RULE).encode('utf-8'))
    assert load_rule_file(path)[0].id == 'cue'


def test_delete_block_header_is_refused(tmp_path):
    root, _src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy:\n'
        '        xalign 0.5\n'
        '    y "hi"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'drop-show',
            'when': {'show': 'sprite'},
            'delete': {
                'scope': 'beat',
                'match': {'mode': 'regex', 'pattern': r'^\s*show '},
            },
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.pending == 0
    assert plan.files[0].skipped[0]['reason'] == '目标是块头，不 replace/delete'


def test_delete_would_empty_block_is_refused(tmp_path):
    root, _src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        '    if True:\n'
        '        $ foo()\n'
        '    y "hi"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [{
            'id': 'drop',
            'when': {'code_contains': 'foo'},
            'delete': {
                'scope': 'beat',
                'match': {'mode': 'exact', 'pattern': '$ foo()'},
            },
        }],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.pending == 0
    assert any('块体变空' in x['reason'] for x in plan.files[0].skipped)


def test_conflicting_line_edits_refuse_the_file(tmp_path):
    root, _src = _write_project(
        tmp_path,
        'label start:\n'
        '    show ye happy\n'
        '    $ foo()\n'
        '    y "hi"\n')
    rule = {
        'schema': SCHEMA,
        'rules': [
            {'id': 'a', 'when': {'show': 'sprite'},
             'replace': {'scope': 'beat',
                         'match': {'mode': 'exact', 'pattern': '$ foo()'},
                         'text': '$ bar()'}},
            {'id': 'b', 'when': {'show': 'sprite'},
             'replace': {'scope': 'beat',
                         'match': {'mode': 'exact', 'pattern': '$ foo()'},
                         'text': '$ baz()'}},
        ],
    }
    plan = plan_batch(root, _rules(tmp_path, rule),
                      chapters=['game/chapter/*.rpy'])
    assert plan.errors
    assert '同一行' in plan.errors[0]


def test_legacy_batch_insert_import_still_works():
    import storygraph.app.batch_insert as old
    assert old.plan_batch is plan_batch


def test_cli_check_then_apply(tmp_path, capsys):
    root, _src = _write_project(tmp_path)
    rule = _write_rule(tmp_path)
    base = ['batch', str(root), '--rule', str(rule),
            '--chapter', 'game/chapter/*.rpy']
    assert main(base + ['--check']) == 1
    capsys.readouterr()
    assert main(base + ['--apply']) == 0
    capsys.readouterr()
    assert main(base + ['--check']) == 0


def test_cli_without_rule_is_usage_error(tmp_path, capsys):
    root, _src = _write_project(tmp_path)
    assert main(['batch', str(root)]) == 2
    err = capsys.readouterr().err
    assert '没有规则文件' in err
