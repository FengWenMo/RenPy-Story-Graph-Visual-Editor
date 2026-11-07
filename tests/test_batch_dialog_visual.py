# -*- coding: utf-8 -*-
"""
「可视化配规则」的**界面判据**（`ui_qt.dialog_batch`）。

用户 2026-11-07 的原话：

> 我当然知道！那是 json 文件！是人亲手写进去的！可是我不要手写 json！
> 我这个工具是给编剧设计的！我需要自定义模块中的可视化界面！
> 而且你的可视化界面无法单独定义出这玩意儿！不信你从可视化界面上操作一遍

所以这里**照着"从界面操作一遍"的路径**钉：

1. 项目里**没有**规则文件时，能点「新建」造出一条规则，并直接预演/应用
   （不许要求人先去写一个 JSON 再"选文件进来"）；
2. 界面上看得见"将写成哪一行"（人不用脑补括号和引号）；
3. 保存出来的文件能被自己重新读回（闭合，不是单向导出）；
4. 文件里**手写**的、界面表达不了的规则，列出来标"只读"，保存后**原样还在**；
5. 新建/切换条目时，旧表单不许被写到别的那一条上（Qt 里最经典的那种静默错位）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CUE = '$ renpy.transition(Dissolve(0.1), layer="master")'

CHAPTER = '''\
label start:
    scene bg room
    show ye happy
    y "hello"

    hide ye
    y "bye"

    show ye angry
    y "angry"
'''

HANDWRITTEN = {'id': 'drop-debug', 'when': {'code_contains': 'debug'},
               'delete': {'scope': 'beat',
                          'match': {'mode': 'contains', 'pattern': 'debug'}}}


def _project(tmp_path, text: str = CHAPTER):
    root = tmp_path / 'proj'
    chapter = root / 'game' / 'chapter'
    chapter.mkdir(parents=True)
    path = chapter / 'ch01.rpy'
    path.write_text(text, encoding='utf-8')
    return root, path


def _dialog(root, rule_path=None):
    from storygraph.ui_qt.dialog_batch import BatchDialog
    return BatchDialog(root, rule_path)


def _yes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QMessageBox.Yes))


# ----------------------------------------------------------------------
#  ① 空项目：从界面造规则 → 预演 → 应用
# ----------------------------------------------------------------------

def test_no_rule_file_means_preview_is_off_but_new_is_available(qapp, tmp_path):
    root, _src = _project(tmp_path)
    dlg = _dialog(root)
    assert not dlg._btn_preview.isEnabled()      # 还没有规则，不能预演
    assert dlg._btn_save.isEnabled() is False    # 也没东西可存
    assert dlg._btn_new.isEnabled()              # 但可以新建
    assert '还没有规则文件' in dlg._rule_label.text()
    assert dlg._list.count() == 0


def test_create_rule_from_scratch_then_preview_and_apply(qapp, tmp_path,
                                                        monkeypatch):
    """**核心判据**：不碰 JSON，全靠界面把这件事做成。"""
    root, src = _project(tmp_path)
    dlg = _dialog(root)

    dlg.add_rule()                               # 点「新建」
    assert dlg._list.count() == 1
    assert dlg._btn_preview.isEnabled()
    # 默认值就是"每次立绘变化 → 补一行 master 层溶解 0.1 秒"
    assert dlg._mode.currentData() == 'sprite_change'
    assert dlg._cue.currentData() == 'master_transition'
    assert CUE in dlg._hint.text()               # 人能看见将写成什么

    plan = dlg.preview()
    # 三处：show happy / **hide ye（退场也算立绘变化）** / show angry。
    # 这一条就是要区分 `change` 和 `show`：只盯 show 的规则会给 2 处，
    # 把退场漏掉（真机里"人走掉"那一刻是最需要过渡的）。
    assert plan is not None and plan.pending == 3, plan.pending
    assert CUE in dlg._view.toPlainText()

    _yes(monkeypatch)
    result = dlg.apply()
    assert result['inserted'] == 3
    text = src.read_text(encoding='utf-8')
    assert text.count(CUE) == 3
    lines = text.split('\n')
    # 插在立绘/退场后面（不是台词后面、也不是文件开头）
    happy = next(i for i, ln in enumerate(lines) if 'show ye happy' in ln)
    assert lines[happy + 1].strip() == CUE
    bye = next(i for i, ln in enumerate(lines) if 'hide ye' in ln)
    assert lines[bye + 1].strip() == CUE

    # 再预演一次：已经加过了 → 0 处（幂等）
    assert dlg.preview().pending == 0


def test_hint_follows_the_form_so_nobody_writes_code_by_hand(qapp, tmp_path):
    root, _src = _project(tmp_path)
    dlg = _dialog(root)
    dlg.add_rule()
    dlg._seconds.setText('0.4')
    assert 'Dissolve(0.4)' in dlg._hint.text()
    dlg._cue.setCurrentIndex(dlg._cue.findData('full_transition'))
    assert 'layer=' not in dlg._hint.text()      # 整屏溶解没有 layer
    dlg._cue.setCurrentIndex(dlg._cue.findData('named'))
    dlg._named.setText('dis_master')
    assert 'renpy.transition(dis_master' in dlg._hint.text()


def test_bad_form_says_why_instead_of_writing_something_wrong(qapp, tmp_path):
    root, _src = _project(tmp_path)
    dlg = _dialog(root)
    dlg.add_rule()
    dlg._seconds.setText('abc')
    assert '时长' in dlg._hint.text() and '⚠' in dlg._hint.text()


# ----------------------------------------------------------------------
#  ② 存 → 读回（闭合）
# ----------------------------------------------------------------------

def test_save_then_reopen_keeps_the_same_rule(qapp, tmp_path):
    root, _src = _project(tmp_path)
    dlg = _dialog(root)
    dlg.add_rule()
    dlg._name.setText('立绘过渡')
    dlg._seconds.setText('0.2')
    path = dlg.save()
    assert path is not None and path.is_file()

    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['schema'] == 'storygraph.batch/1'
    assert data['rules'][0]['id'] == '立绘过渡'
    assert data['rules'][0]['insert']['text'] == \
        '$ renpy.transition(Dissolve(0.2), layer="master")'

    again = _dialog(root)
    assert again._list.count() == 1
    assert again._seconds.text() == '0.2'
    assert again._mode.currentData() == 'sprite_change'


def test_saved_rule_file_still_works_from_the_cli_path(qapp, tmp_path):
    """界面存出来的文件，`batch_edit.load_rule_file` 必须认（同一条校验）。"""
    from storygraph.app.batch_edit import load_rule_file

    root, _src = _project(tmp_path)
    dlg = _dialog(root)
    dlg.add_rule()
    path = dlg.save()
    rules = load_rule_file(path)
    assert len(rules) == 1
    assert rules[0].op == 'insert'
    assert rules[0].when['change'] == 'sprite'


# ----------------------------------------------------------------------
#  ③ 手写规则：列出来、标只读、保存后原样还在
# ----------------------------------------------------------------------

def _with_handwritten(tmp_path):
    root, src = _project(tmp_path)
    path = root / 'storygraph.batch.json'
    path.write_text(json.dumps({'schema': 'storygraph.batch/1',
                                'rules': [HANDWRITTEN]}, ensure_ascii=False),
                    encoding='utf-8')
    return root, src, path


def test_handwritten_rule_is_listed_and_marked_read_only(qapp, tmp_path):
    root, _src, _path = _with_handwritten(tmp_path)
    dlg = _dialog(root)
    assert dlg._list.count() == 1
    assert '只读' in dlg._list.item(0).text()
    assert not dlg._mode.isEnabled()             # 表单整体灰掉
    assert '改不了' in dlg._hint.text()


def test_saving_does_not_delete_the_handwritten_rule(qapp, tmp_path):
    """「界面没显示」绝不等于「可以删」。"""
    root, _src, path = _with_handwritten(tmp_path)
    dlg = _dialog(root)
    dlg.add_rule()                               # 再加一条自己配的
    dlg.save()
    data = json.loads(path.read_text(encoding='utf-8'))
    ids = [r['id'] for r in data['rules']]
    assert ids == ['drop-debug', '规则1'], ids
    assert data['rules'][0] == HANDWRITTEN


# ----------------------------------------------------------------------
#  ④ 状态机：新建/切换不许把旧表单写到别处
# ----------------------------------------------------------------------

def test_new_rule_does_not_inherit_the_previous_form(qapp, tmp_path):
    root, _src = _project(tmp_path)
    dlg = _dialog(root)
    dlg.add_rule()
    dlg._seconds.setText('0.9')                  # 改第一条
    dlg.add_rule()                               # 再新建
    assert dlg._list.count() == 2
    assert dlg._seconds.text() == '0.1'           # 新规则是默认值
    assert dlg._mode.currentData() == 'sprite_change'
    # 切回第一条，改过的值还在
    dlg._list.setCurrentRow(0)
    assert dlg._seconds.text() == '0.9'


def test_two_rules_are_written_as_two_entries(qapp, tmp_path):
    root, src = _project(tmp_path)
    dlg = _dialog(root)
    dlg.add_rule()
    dlg._name.setText('a-master')
    dlg.add_rule()
    dlg._name.setText('b-full')
    dlg._cue.setCurrentIndex(dlg._cue.findData('full_transition'))
    dlg._mode.setCurrentIndex(dlg._mode.findData('say_line'))
    path = dlg.save()
    data = json.loads(path.read_text(encoding='utf-8'))
    assert [r['id'] for r in data['rules']] == ['a-master', 'b-full']
    assert data['rules'][1]['when'] == {'say': True, 'transition': 'none',
                                        'code_not_contains': 'renpy.transition'}
    assert data['rules'][1]['insert']['text'] == \
        '$ renpy.transition(Dissolve(0.1))'
    assert dlg.preview().pending >= 1


def test_removing_the_last_rule_disables_preview(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    root, _src = _project(tmp_path)
    dlg = _dialog(root)
    dlg.add_rule()
    monkeypatch.setattr(QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    dlg.remove_rule()
    assert dlg._list.count() == 0
    assert not dlg._btn_preview.isEnabled()
    assert '还没有规则文件' in dlg._rule_label.text()


# ----------------------------------------------------------------------
#  ⑤ 发现路径：从「技术调用」弹窗能走到这里
# ----------------------------------------------------------------------

def test_calls_dialog_offers_a_way_to_the_batch_window(qapp, tmp_path):
    """
    编剧找"能不能定义这玩意"时，第一个去的地方是「自定义模块 → 技术调用」。
    那里必须有一条路通向批处理窗口，否则人会以为"做不到"（本需求的原话）。
    """
    from storygraph.ui_qt.dialog_calls import CallDialog

    root, _src = _project(tmp_path)
    dlg = CallDialog(None, project_root=root)
    assert dlg.batch_btn.text().startswith('按规则批量')
    assert dlg.batch_btn.isEnabled()


def test_calls_dialog_without_a_project_says_so_instead_of_crashing(qapp):
    """没有项目时点那个按钮：只更新状态栏，不许弹窗、不许抛（Qt 槽会吞异常）。"""
    from storygraph.ui_qt.dialog_calls import CallDialog

    dlg = CallDialog(None, project_root=None)
    assert dlg._go_batch() is None
    assert '先打开项目' in dlg.status.text()


# ----------------------------------------------------------------------
#  ⑤ 发现路径：从「技术调用」弹窗能走到这里
# ----------------------------------------------------------------------

def test_calls_dialog_offers_a_way_to_the_batch_window(qapp, tmp_path):
    """
    编剧找"能不能定义这玩意"时，第一个去的地方是「自定义模块 → 技术调用」。
    那里必须有一条路通向批处理窗口，否则人会以为"做不到"（本需求的原话）。
    """
    from storygraph.ui_qt.dialog_calls import CallDialog

    root, _src = _project(tmp_path)
    dlg = CallDialog(None, project_root=root)
    assert dlg.batch_btn.text().startswith('按规则批量')
    assert dlg.batch_btn.isEnabled()


def test_calls_dialog_without_a_project_says_so_instead_of_crashing(qapp):
    """没有项目时点那个按钮：只更新状态栏，不许弹窗、不许抛（Qt 槽会吞异常）。"""
    from storygraph.ui_qt.dialog_calls import CallDialog

    dlg = CallDialog(None, project_root=None)
    assert dlg._go_batch() is None
    assert '先打开项目' in dlg.status.text()
