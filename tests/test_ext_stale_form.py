# -*- coding: utf-8 -*-
"""
**右栏那个表单，在"退路"之后还是活的吗**（2026-11-07 第十轮）

探针 `.pip-tmp/probe_r10_stale_form.py` 在接「历史版本」的路上顺手量到一条
**上一轮就在**的洞：

    选中一条 → 改 → 保存 → 点「撤销上次保存」
    → `_reread()` 把 `_current` 置 None、树重建，**右栏却还显示着刚才那一格**；
    → 人在里面接着改、点保存 —— `_save` 里 `if self._current is not None` 把
      改动静默跳过：界面上写着"甲改过了"，文件里还是"甲"，一句提示都没有。

这就是这个工具一直在防的那类症状（"格子在、改了不生效"）。修法不是加一句
提示，而是让状态**没有第二种解释**：

  · 那一条还在 → 重新选中它（表单照旧是活的，改完能存）；
  · 那一条没了 → 表单**停用并清空**（改不了，就没有"改了不生效"）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.ext import registry as ER  # noqa: E402
from storygraph.ext import schema as ES  # noqa: E402

ALPHA = {'id': 'alpha', 'kind': 'statement', 'label': '甲'}


def _project(tmp_path: Path) -> Path:
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / ER.MANIFEST).write_text(json.dumps(
        {'schema': ES.EXT_SCHEMA, 'extensions': [ALPHA]},
        ensure_ascii=False), encoding='utf-8')
    return root


def _dialog(root):
    from storygraph.ui_qt.dialog_extensions import ExtensionDialog
    return ExtensionDialog(None, project_root=root)


def _label(root, ident) -> str:
    point = ER.ExtensionRegistry.for_project(str(root)).get(ident)
    return '' if point is None else point.label


class TestAtStartup:
    def test_nothing_selected_means_the_form_is_disabled(self, qapp, tmp_path):
        """开窗那一眼：没选中任何一条 → 右栏停用（不是"能打字、存不进去"）。"""
        dlg = _dialog(_project(tmp_path))
        assert dlg._current is None
        assert not dlg.ed_label.isEnabled()
        assert dlg.head.text() == '左边挑一个扩展点'
        assert dlg.focus_on('alpha')            # 选中之后才解锁
        assert dlg.ed_label.isEnabled()
        assert dlg.ed_label.text() == '甲'


class TestAfterUndo:
    def test_the_same_point_is_re_selected_and_new_edits_still_land(
            self, qapp, tmp_path):
        root = _project(tmp_path)
        dlg = _dialog(root)
        assert dlg.focus_on('alpha')
        dlg.ed_label.setText('甲改')
        assert dlg._save()
        assert _label(root, 'alpha') == '甲改'

        dlg.btn_undo.click()                       # 退回保存之前
        assert _label(root, 'alpha') == '甲'
        assert getattr(dlg._current, 'id', None) == 'alpha', (
            '撤销之后右栏该重新落在同一条上 —— 否则接着改会被静默跳过')
        assert dlg.ed_label.text() == '甲'
        assert dlg.ed_label.isEnabled()

        dlg.ed_label.setText('甲改过了')            # 接着改，还能存
        assert dlg._save()
        assert _label(root, 'alpha') == '甲改过了'

    def test_when_the_point_is_gone_the_form_is_disabled_and_empty(
            self, qapp, tmp_path):
        root = _project(tmp_path)
        dlg = _dialog(root)
        dlg._new_point()                           # 新建一条（名字是「扩展点1」）
        assert dlg._save()
        assert _label(root, '扩展点1') == '新扩展点'

        dlg.btn_undo.click()                       # 撤销 = 把这条撤没了
        assert _label(root, '扩展点1') == ''
        assert dlg._current is None
        assert not dlg.ed_label.isEnabled(), '那一条已经没了，表单却还能改'
        assert dlg.ed_label.text() == ''
        assert dlg.head.text() == '左边挑一个扩展点'


class TestAfterDelete:
    def test_deleting_the_selected_point_does_not_leave_a_live_form(
            self, qapp, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        root = _project(tmp_path)
        dlg = _dialog(root)
        assert dlg.focus_on('alpha')
        monkeypatch.setattr(QMessageBox, 'question',
                            staticmethod(lambda *a, **kw: QMessageBox.Yes))
        dlg._delete_point()
        assert _label(root, 'alpha') == ''
        assert dlg._current is None
        assert not dlg.ed_label.isEnabled()
        assert dlg.tbl.rowCount() == 0
