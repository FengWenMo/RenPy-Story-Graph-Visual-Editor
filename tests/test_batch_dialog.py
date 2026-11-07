# -*- coding: utf-8 -*-
"""
规则批处理的**界面入口**（`ui_qt.dialog_batch`）。

`app.batch_edit` 是引擎（只算，不写）；这一层是人在图界面里真正按下去的
那个按钮，所以钉的是四件会要命的事：

1. 一进来**不写盘**，且"应用"是灰的（没有计划就没什么可写的）；
2. 预演要把"几个文件、几处"和 diff 摊出来（不摊开就没人敢按）；
3. 应用必须过一次确认，默认是 No —— 没人点就不写；
4. 写之前**重算计划**：预演之后源文件被别人改过，不能拿旧计划硬写。

（2026-11-07）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app.batch_edit import SCHEMA

CUE = '$ renpy.transition(Dissolve(0.1), layer="master")'

CHAPTER = '''\
label start:
    scene bg room
    show ye happy
    y "hello"

    show lan sad
    $ renpy.transition(Dissolve(0.1), layer="master")
    l "already"

    hide ye
    show ye angry
    y "angry"
'''

RULE = {
    'schema': SCHEMA,
    'rules': [{
        'id': 'cue',
        'when': {'show': 'sprite', 'transition': 'none',
                 'code_not_contains': 'renpy.transition'},
        'insert': {'placement': 'after_last_show', 'text': CUE},
    }],
}


def _project(tmp_path, text: str = CHAPTER):
    root = tmp_path / 'proj'
    chapter = root / 'game' / 'chapter'
    chapter.mkdir(parents=True)
    path = chapter / 'ch01.rpy'
    path.write_text(text, encoding='utf-8')
    return root, path


def _rule(tmp_path, rule: dict | None = None) -> Path:
    path = tmp_path / 'storygraph.batch.json'
    path.write_text(json.dumps(rule if rule is not None else RULE,
                               ensure_ascii=False), encoding='utf-8')
    return path


def _dialog(qapp, root, rule_path=None):
    from storygraph.ui_qt.dialog_batch import BatchDialog
    return BatchDialog(root, rule_path)


def _yes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QMessageBox.Yes))


def _no(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QMessageBox.No))


def test_preview_does_not_write_and_apply_starts_disabled(qapp, tmp_path):
    root, src = _project(tmp_path)
    before = src.read_bytes()
    dlg = _dialog(qapp, root, _rule(tmp_path))
    assert not dlg._btn_apply.isEnabled()        # 还没预演过：没东西可写
    dlg.preview()
    assert src.read_bytes() == before            # 预演**一行都没写**
    assert dlg._btn_apply.isEnabled()
    text = dlg._view.toPlainText()
    assert '2 处待处理' in text                   # 第三章有现成的转场，跳过
    assert CUE in text                            # diff 里能看到要插什么


def test_apply_asks_first_and_no_means_no(qapp, tmp_path, monkeypatch):
    root, src = _project(tmp_path)
    before = src.read_bytes()
    dlg = _dialog(qapp, root, _rule(tmp_path))
    _no(monkeypatch)
    assert dlg.apply() is None
    assert src.read_bytes() == before


def test_apply_writes_and_emits(qapp, tmp_path, monkeypatch):
    root, src = _project(tmp_path)
    dlg = _dialog(qapp, root, _rule(tmp_path))
    got = []
    dlg.applied.connect(got.append)
    _yes(monkeypatch)
    result = dlg.apply()
    assert result['written'] == ['game/chapter/ch01.rpy']
    assert result['inserted'] == 2
    assert got and got[0]['inserted'] == 2
    lines = src.read_text(encoding='utf-8').split('\n')
    assert lines[3].strip() == CUE                # 紧跟在 `show ye happy` 后
    assert not dlg._btn_apply.isEnabled()         # 写完了，没计划了


def test_apply_replans_instead_of_using_a_stale_preview(qapp, tmp_path,
                                                        monkeypatch):
    """预演之后文件被别人加了一行：仍然按**新内容**算，不拿旧行号硬插。"""
    root, src = _project(tmp_path)
    dlg = _dialog(qapp, root, _rule(tmp_path))
    dlg.preview()
    src.write_text('# 别人刚加的一行\n' + CHAPTER, encoding='utf-8')
    _yes(monkeypatch)
    assert dlg.apply()['inserted'] == 2
    text = src.read_text(encoding='utf-8')
    assert text.startswith('# 别人刚加的一行\n')  # 别人的改动没被冲掉
    # 原本那一处（`l "already"` 前面）+ 新补的两处
    assert text.count(CUE) == 3
    assert text.count(CUE) == CHAPTER.count(CUE) + 2


def test_bad_rule_is_shown_and_apply_stays_disabled(qapp, tmp_path):
    root, src = _project(tmp_path)
    bad = _rule(tmp_path, {'schema': SCHEMA, 'rules': [{'id': 'x',
                                                        'nope': {}}]})
    before = src.read_bytes()
    dlg = _dialog(qapp, root, bad)
    assert dlg.preview() is None
    assert '不认识的键' in dlg._view.toPlainText()
    assert not dlg._btn_apply.isEnabled()
    assert src.read_bytes() == before


def test_missing_rule_file_keeps_everything_off(qapp, tmp_path):
    root, _src = _project(tmp_path)
    dlg = _dialog(qapp, root)                     # 项目里没有规则文件
    assert not dlg._btn_preview.isEnabled()
    assert not dlg._btn_apply.isEnabled()
    assert '还没有规则文件' in dlg._rule_label.text()


def test_chapter_filter_narrows_the_scope(qapp, tmp_path):
    root, _src = _project(tmp_path)
    (root / 'game' / 'chapter' / 'ch02.rpy').write_text(
        'label two:\n    show ye sad\n    y "x"\n', encoding='utf-8')
    dlg = _dialog(qapp, root, _rule(tmp_path))
    assert dlg.preview().pending == 3             # 两章都算
    dlg._chapters.setText('game/chapter/ch02.rpy')
    assert dlg.preview().pending == 1             # 只看这一章


def test_rule_error_without_project_is_reported_not_raised(qapp, tmp_path):
    dlg = _dialog(qapp, tmp_path / 'nope', _rule(tmp_path))
    assert dlg.preview() is None
    assert '项目目录不存在' in dlg._view.toPlainText()
