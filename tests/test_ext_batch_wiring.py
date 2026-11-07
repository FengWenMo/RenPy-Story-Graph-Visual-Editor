# -*- coding: utf-8 -*-
"""
**批处理开关接进批量执行**（`rules_from_extensions` → CLI / 批处理窗口）。

2026-11-07 第三轮。起因是上一轮交付时自己记下的"仍然没做"第 1 条：
批处理窗口里"导入扩展规则"。真去接的时候先撞上一个更硬的事实：

    `rules_from_extensions()` **只有判据在调**。

也就是说扩展窗口里把「是否能够批处理」打开，界面能点「预演」、
`cue_rules()` 也能产出规则 —— 但 CLI 的 `batch` 与批处理窗口读的是
`storygraph.batch.json` / 界面上的规则，声明的规则**从来没进过这两条路**。
"开关看着能开、真跑起来少一条"，和上一轮那三处断链是同一类。

所以这一轮钉的是"**声明 → 真跑**"这条线：

1. 项目里只有一条声明（没有规则文件）时，`cli.py batch` 能跑；
2. 声明 + 规则文件同时存在时，两边都跑；同 id 以文件为准，写法不同要说出来；
3. `--rule` / 「换个文件…」是"就按这一份来"，**不**偷偷并入声明；
4. 关掉开关 = 一条都不跑（不是"跑了个空的"）；
5. 插件里加的批处理动作，不改工具代码就能进这条路；
6. 一条声明坏了不连坐，其余照跑；
7. 批处理窗口：声明随计划一起跑、能一键导进列表、导入后能存成规则文件。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app.batch_edit import SCHEMA

CUE = '$ camera(0.1)'
OTHER = '$ other_effect(0.2)'

CHAPTER = '''\
label start:
    scene bg room
    show ye happy
    y "hello"

    show lan sad
    l "hi"
'''

BATCH = {'when': {'show': 'sprite', 'transition': 'none'},
         'insert': {'placement': 'after_last_show', 'text': CUE}}

POINT = {'id': '镜头补一行', 'kind': 'batch', 'label': '镜头',
         'batchable': True, 'batch': BATCH}


def _project(tmp_path, *, ext=None, rules=None, plugins=None,
             name='proj') -> Path:
    root = tmp_path / name
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch01.rpy').write_text(CHAPTER, encoding='utf-8')
    if ext is not None:
        (root / 'storygraph.ext.json').write_text(
            json.dumps({'extensions': ext}, ensure_ascii=False),
            encoding='utf-8')
    if rules is not None:
        (root / 'storygraph.batch.json').write_text(
            json.dumps({'schema': SCHEMA, 'rules': rules}, ensure_ascii=False),
            encoding='utf-8')
    if plugins:
        folder = root / 'extensions'
        folder.mkdir(parents=True)
        for fname, body in plugins.items():
            (folder / fname).write_text(
                json.dumps(body, ensure_ascii=False), encoding='utf-8')
    return root


def _rule_file(tmp_path, rule_id='别的规则', text=OTHER) -> Path:
    path = tmp_path / 'another.json'
    path.write_text(json.dumps({'schema': SCHEMA, 'rules': [
        {'id': rule_id, 'when': {},
         'insert': {'placement': 'after_last_show', 'text': text}}]},
        ensure_ascii=False), encoding='utf-8')
    return path


# ======================================================================
#  引擎侧：合并口径（唯一一处）
# ======================================================================

def test_the_switch_output_merges_into_the_rule_list(tmp_path):
    from storygraph.app.batch_edit import (merge_declared_rules,
                                           rules_from_extensions)

    root = _project(tmp_path, ext=[POINT])
    declared, problems = rules_from_extensions(str(root))
    assert problems == [] and [r.id for r in declared] == ['镜头补一行']
    merged, notes = merge_declared_rules([], declared)
    assert [r.id for r in merged] == ['镜头补一行']
    assert notes == []


def test_the_same_id_from_the_file_wins_and_the_drift_is_said(tmp_path):
    from storygraph.app.batch_edit import (load_rule_file, merge_declared_rules,
                                           rules_from_extensions)

    rule_file = _rule_file(tmp_path, rule_id='镜头补一行', text=OTHER)
    root = _project(tmp_path, ext=[POINT])
    declared, _ = rules_from_extensions(str(root))
    merged, notes = merge_declared_rules(load_rule_file(rule_file), declared)
    assert len(merged) == 1
    assert merged[0].text == OTHER, '同 id 时文件那份才是真的'
    assert notes and '写法不同' in notes[0]


def test_an_identical_copy_is_not_reported_as_drift(tmp_path):
    """扩展窗口「写进 batch.json」之后两边内容一样 —— 别再喊一遍。"""
    from storygraph.app.batch_edit import (load_rule_file, merge_declared_rules,
                                           rules_from_extensions)

    rule_file = tmp_path / 'same.json'
    rule_file.write_text(json.dumps({'schema': SCHEMA, 'rules': [
        {'id': '镜头补一行', 'when': dict(BATCH['when']),
         'insert': dict(BATCH['insert'])}]}, ensure_ascii=False),
        encoding='utf-8')
    root = _project(tmp_path, ext=[POINT])
    declared, _ = rules_from_extensions(str(root))
    merged, notes = merge_declared_rules(load_rule_file(rule_file), declared)
    assert len(merged) == 1 and notes == []


# ======================================================================
#  CLI：声明真的进了计划
# ======================================================================

def test_cli_batch_runs_on_a_declaration_alone(tmp_path, capsys):
    """没有 `storygraph.batch.json`，只有一条声明 → 也能跑（本轮的核心判据）。"""
    from storygraph.cli import main

    root = _project(tmp_path, ext=[POINT])
    assert not (root / 'storygraph.batch.json').exists()
    code = main(['batch', str(root), '--diff'])
    log = capsys.readouterr()
    assert code == 0, (log.out + log.err)
    assert CUE in log.out, log.out
    assert '扩展声明' in log.out


def test_cli_batch_still_refuses_when_there_is_nothing(tmp_path, capsys):
    """开关关着 + 没有规则文件 = 明确"没有规则"，不是静默跑个空的。"""
    from storygraph.cli import main

    root = _project(tmp_path, ext=[{**POINT, 'enabled': False}])
    code = main(['batch', str(root)])
    err = capsys.readouterr().err
    assert code != 0
    assert '没有规则文件' in err
    assert '是否能够批处理' in err


def test_cli_batch_with_an_explicit_rule_file_ignores_declarations(
        tmp_path, capsys):
    """`--rule` = 就按这一份来；声明**不**偷偷混进来（写盘类操作最怕这种）。"""
    from storygraph.cli import main

    root = _project(tmp_path, ext=[POINT])
    other = _rule_file(tmp_path)
    code = main(['batch', str(root), '--rule', str(other), '--diff'])
    out = capsys.readouterr().out
    assert code == 0, out
    assert OTHER in out
    assert CUE not in out
    assert '扩展声明' not in out


def test_cli_batch_runs_both_sources_together(tmp_path, capsys):
    from storygraph.cli import main

    root = _project(tmp_path, ext=[POINT],
                    rules=[{'id': '别的规则', 'when': {},
                            'insert': {'placement': 'after_last_show',
                                       'text': OTHER}}])
    code = main(['batch', str(root), '--diff'])
    out = capsys.readouterr().out
    assert code == 0, out
    assert CUE in out and OTHER in out, out
    assert '共 2 条规则' in out


def test_cli_reports_a_drifting_duplicate_instead_of_failing(tmp_path, capsys):
    from storygraph.cli import main

    root = _project(tmp_path, ext=[POINT],
                    rules=[{'id': '镜头补一行', 'when': {},
                            'insert': {'placement': 'after_last_show',
                                       'text': OTHER}}])
    code = main(['batch', str(root), '--diff'])
    out = capsys.readouterr().out
    assert code == 0, out
    assert '写法不同' in out
    assert OTHER in out and CUE not in out


def test_a_broken_declaration_does_not_take_the_good_one_down(tmp_path, capsys):
    from storygraph.cli import main

    broken = {**POINT, 'id': '坏的那条',
              'batch': {'when': {}, 'insert': {'text': CUE, 'nope': 1}}}
    root = _project(tmp_path, ext=[broken, POINT])
    code = main(['batch', str(root), '--diff'])
    log = capsys.readouterr()
    assert code == 0, (log.out + log.err)
    assert CUE in log.out
    assert '坏的那条' in log.out, '坏规则要说出来（不连坐，但也不静默）'


# ======================================================================
#  插件：加一条批处理动作，不改工具代码
# ======================================================================

def test_a_plugin_can_add_a_batch_action(tmp_path):
    """`extensions/*.json` 里的一条声明 → 直接进计划（核心一行没改）。"""
    from storygraph.app.batch_edit import plan_batch, rules_from_extensions

    root = _project(tmp_path, plugins={'镜头插件.json': {
        'extensions': [{**POINT, 'id': '插件加的'}]}})
    declared, problems = rules_from_extensions(str(root))
    assert problems == []
    assert [r.id for r in declared] == ['插件加的']
    plan = plan_batch(root, declared)
    assert plan.errors == []
    assert plan.pending == 2
    assert any(i.rule_id == '插件加的'
               for f in plan.files for i in f.insertions)


def test_a_python_plugin_can_add_a_batch_action(tmp_path):
    from storygraph.app.batch_edit import rules_from_extensions

    root = tmp_path / 'pyproj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch01.rpy').write_text(CHAPTER, encoding='utf-8')
    folder = root / 'extensions'
    folder.mkdir()
    (folder / '镜头.py').write_text(
        'EXTENSIONS = [{"id": "py插件", "kind": "batch", "batchable": True,\n'
        f'               "batch": {json.dumps(BATCH, ensure_ascii=False)}}}]\n',
        encoding='utf-8')
    declared, problems = rules_from_extensions(str(root))
    assert problems == [], problems
    assert [r.id for r in declared] == ['py插件']


# ======================================================================
#  批处理窗口：随计划跑 / 一键导入 / 别偷偷改
# ======================================================================

def _dialog(qapp, root, rule_path=None):
    from storygraph.ui_qt.dialog_batch import BatchDialog
    return BatchDialog(root, rule_path)


def test_the_dialog_previews_on_a_declaration_alone(qapp, tmp_path):
    root = _project(tmp_path, ext=[POINT])
    dlg = _dialog(qapp, root)
    assert dlg._declared and dlg._declared[0].id == '镜头补一行'
    assert dlg._btn_preview.isEnabled(), '只声明、没规则文件时也该能预演'
    assert not dlg._btn_import.isHidden()
    assert '扩展声明' in dlg._declared_hint.text()
    plan = dlg.preview()
    assert plan is not None and plan.pending == 2
    assert CUE in dlg._view.toPlainText()
    assert '扩展声明' in dlg._status.text()


def test_the_dialog_imports_the_declaration_into_an_editable_rule(qapp,
                                                                  tmp_path):
    root = _project(tmp_path, ext=[POINT])
    dlg = _dialog(qapp, root)
    assert dlg.import_declared_rules() == 1
    assert len(dlg._items) == 1
    assert dlg._items[0]['form'] is not None, '导进来就要能改'
    assert dlg.import_declared_rules() == 0, '第二次不该再加一条'
    assert len(dlg._items) == 1
    saved = dlg.save()
    assert saved is not None and saved.is_file()
    data = json.loads(saved.read_text(encoding='utf-8'))
    assert [r['id'] for r in data['rules']] == ['镜头补一行']
    assert CUE in json.dumps(data, ensure_ascii=False)


def test_a_picked_rule_file_is_exactly_what_runs(qapp, tmp_path):
    root = _project(tmp_path, ext=[POINT])
    other = _rule_file(tmp_path)
    dlg = _dialog(qapp, root, other)
    assert dlg._declared == [], '显式选了一份文件 = 就按那份来'
    assert dlg._btn_import.isHidden()
    plan = dlg.preview()
    assert plan is not None and plan.pending == 2
    assert OTHER in dlg._view.toPlainText()
    assert CUE not in dlg._view.toPlainText()


def test_the_dialog_says_nothing_when_the_switch_is_off(qapp, tmp_path):
    root = _project(tmp_path, ext=[{**POINT, 'enabled': False}])
    dlg = _dialog(qapp, root)
    assert dlg._declared == []
    assert dlg._btn_preview.isEnabled() is False
    assert dlg._btn_import.isHidden()
    assert '还没有规则文件' in dlg._rule_label.text()


def test_a_broken_plugin_rule_is_shown_in_the_dialog_not_raised(qapp,
                                                               tmp_path):
    root = _project(tmp_path, ext=[{**POINT, 'id': '坏的那条',
                                    'batch': {'when': {},
                                              'insert': {'text': CUE,
                                                         'nope': 1}}}])
    dlg = _dialog(qapp, root)
    assert dlg._declared == []
    assert dlg._declared_notes and '坏的那条' in dlg._declared_notes[0]
    assert '扩展声明' in dlg._declared_hint.text()
    assert not dlg._btn_preview.isEnabled()
