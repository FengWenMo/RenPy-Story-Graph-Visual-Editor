# -*- coding: utf-8 -*-
"""
条件 / 菜单分支的**窗口层**判据（不是只测命令）。

要害有两个，都是上一轮被用户抓到的形态：

  · 窗口里填了条件 / 菜单项，点「创建」只画草图、只弹一句"分支不能写成
    一行" —— 现在必须真的写进编排层；
  · 分支只能落在**章末**（`X_control` 的 `call X` 之后）。选了"第 N 拍"
    还悄悄换成章末，等于改了用户的意思，所以窗口要当场拒绝并说清。

真窗口用例走 `MainWindowQt`：导入章节 → 直接调窗口的 `_add_control_branch`
（对话框只负责收字段，落盘链路由这里覆盖）。
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QDialog, QPlainTextEdit

from _ui_project import build_project

from storygraph.graph.models import Graph
from storygraph.ui_qt.dialogs import AddEdgeDialog, EditEdgeDialog, TakeoverDialog
from storygraph.story.control_takeover import plan_takeover


# ---------------------------------------------------------------------------
#  对话框：分支字段真的能交出去
# ---------------------------------------------------------------------------

def _nodes():
    return {1: SimpleNamespace(label_name='A'),
            2: SimpleNamespace(label_name='B')}


def _control_plan(*_args, **_kw):
    return {'ok': True, 'kind': 'control', 'reason': '写进 A_control'}


def _control_positions(_from_id):
    return {'control': True, 'items': [
        {'line_id': 'A.0002', 'number': 2, 'text': '第二拍',
         'selectable': True}]}


def test_add_dialog_hands_a_condition_branch_to_the_window(qapp):
    dlg = AddEdgeDialog(None, nodes=_nodes(), from_id=1, to_id=2,
                        plan_fn=_control_plan,
                        positions_fn=_control_positions)
    dlg._cond.setText('score > 3')
    dlg._on_accept()
    assert dlg.result() == QDialog.Accepted
    assert dlg.branch is True
    assert dlg.write is True, '分支不该再被当成"只能画草图"'
    assert dlg.condition == 'score > 3'


def test_add_dialog_refuses_a_branch_at_a_specific_beat(qapp):
    dlg = AddEdgeDialog(None, nodes=_nodes(), from_id=1, to_id=2,
                        plan_fn=_control_plan,
                        positions_fn=_control_positions)
    dlg._opt.setText('去屋顶')
    dlg._pos.setCurrentIndex(1)              # 第 2 拍
    dlg._on_accept()
    assert dlg.result() != QDialog.Accepted, '分支落在一拍上会破坏块体语义'
    assert '章末' in dlg._error.text()
    assert dlg.write is False


def test_edit_dialog_lets_a_control_edge_change_condition_and_option(qapp):
    edge = SimpleNamespace(
        id=7, type='jump', condition='', option_text='', from_clause='',
        origin='control', line_id='', from_id=1, to_id=2,
        position='control_end', position_line_id='')
    dlg = EditEdgeDialog(None, edge=edge, from_label='A', to_label='B',
                         targets=[(1, 'A'), (2, 'B')])
    assert dlg._cond.isEnabled() is True
    assert dlg._opt.isEnabled() is True
    dlg._cond.setText('flag')
    dlg._opt.setText('去屋顶')
    dlg._on_accept()
    assert dlg.result() == QDialog.Accepted
    assert (dlg.condition, dlg.option_text) == ('flag', '去屋顶')
    assert dlg.changed is True


# ---------------------------------------------------------------------------
#  接管预览：diff 必须看得见
# ---------------------------------------------------------------------------

def test_takeover_dialog_shows_the_multi_file_diff(qapp, tmp_path):
    root = tmp_path / 'proj'
    (root / 'game' / 'script').mkdir(parents=True)
    (root / 'game' / 'script' / 'ch01.rpy').write_text(
        'label ch01:\n    jump ch02\n', encoding='utf-8')
    (root / 'game' / 'script' / 'ch02.rpy').write_text(
        'label ch02:\n    return\n', encoding='utf-8')
    (root / 'game' / 'chapter_controller.rpy').write_text(
        'label ch01_control:\n    call ch01\n    return\n', encoding='utf-8')
    plan = plan_takeover(root, ['ch01', 'ch02'])
    assert plan.ok
    dlg = TakeoverDialog(None, plan=plan)
    box = dlg.findChild(QPlainTextEdit)
    assert box is not None and 'jump ch02_control' in box.toPlainText()
    assert 'chapter_controller.rpy' in box.toPlainText()


# ---------------------------------------------------------------------------
#  真窗口：创建分支 → 编排层真的变了
# ---------------------------------------------------------------------------

class _Engine:
    def __init__(self, root):
        self.project_root = root
        self._registry = None

    def get_registry(self):
        return self._registry


def _drain_threads(ms=1000):
    try:
        from PySide6.QtCore import QThreadPool
        QThreadPool.globalInstance().waitForDone(ms)
    except Exception:                        # noqa: BLE001 - 清理失败不影响判据
        pass


def test_real_window_writes_a_condition_branch_into_the_control_layer(
        qapp, tmp_path):
    from storygraph.ui_qt.main_window import MainWindowQt

    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / 'ch01.rpy'
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text('label ch01:\n    e "a"\n    return\n',
                      encoding='utf-8')
    other = script.parent / 'ch02.rpy'
    other.write_text('label ch02:\n    e "b"\n    return\n', encoding='utf-8')
    controller = root / 'game' / 'chapter_controller.rpy'
    controller.write_text(
        'label ch01_control:\n    call ch01\n    return\n\n'
        'label ch02_control:\n    call ch02\n    return\n', encoding='utf-8')

    win = MainWindowQt(_Engine(root))
    try:
        win.open_project_path(root)
        win.import_chapter_files([script, other])
        ids = {n.label_name: nid for nid, n in win.graph.nodes.items()}
        dlg = SimpleNamespace(
            from_id=ids['ch01'], to_id=ids['ch02'], etype='jump',
            condition='score > 3', option_text='', from_clause='',
            draft=False, write=True, reason='')
        assert win._add_control_branch(dlg) is True
        text = controller.read_text(encoding='utf-8')
        assert 'storygraph-edge:' in text
        assert 'if score > 3:' in text
        assert 'jump ch02_control' in text
        # 图上多了一条编排线（不是草图）
        assert any(getattr(e, 'origin', '') == 'control'
                   for e in win.graph.edges.values())
        # 章节剧本一个字没动
        assert script.read_text(encoding='utf-8') == (
            'label ch01:\n    e "a"\n    return\n')
    finally:
        win.close()
        _drain_threads()


def test_real_window_two_menu_edges_share_one_menu(qapp, tmp_path):
    """两条菜单边在真窗口里要落进**同一个** menu，不能并排两个。"""
    from storygraph.ui_qt.main_window import MainWindowQt

    root = build_project(tmp_path / 'proj')
    script_dir = root / 'game' / 'script'
    script_dir.mkdir(parents=True, exist_ok=True)
    labels = []
    for name in ('ch01', 'ch02', 'ch03'):
        path = script_dir / f'{name}.rpy'
        path.write_text(f'label {name}:\n    e "{name}"\n    return\n',
                        encoding='utf-8')
        labels.append(path)
    controller = root / 'game' / 'chapter_controller.rpy'
    controller.write_text(
        'label ch01_control:\n    call ch01\n    return\n\n'
        'label ch02_control:\n    call ch02\n    return\n\n'
        'label ch03_control:\n    call ch03\n    return\n', encoding='utf-8')

    win = MainWindowQt(_Engine(root))
    try:
        win.open_project_path(root)
        win.import_chapter_files(labels)
        ids = {n.label_name: nid for nid, n in win.graph.nodes.items()}

        def _dlg(target, text, etype='jump', cond=''):
            return SimpleNamespace(
                from_id=ids['ch01'], to_id=ids[target], etype=etype,
                condition=cond, option_text=text, from_clause='',
                draft=False, write=True, reason='')

        assert win._add_control_branch(_dlg('ch02', '去屋顶'))
        assert win._add_control_branch(_dlg('ch03', '留在教室', cond='flag'))
        body = controller.read_text(encoding='utf-8')
        assert body.count('\n    menu:\n') == 1, body
        assert '去屋顶' in body and '留在教室' in body
        assert 'storygraph-menu:ch01' in body
    finally:
        win.close()
        _drain_threads()


def test_real_window_takeover_previews_then_writes_and_undoes(
        qapp, tmp_path, monkeypatch):
    """接管真窗口链路：先预览 → 确认 → 多文件写 → Ctrl+Z 全回去。"""
    from storygraph.ui_qt import dialogs as D
    from storygraph.ui_qt.main_window import MainWindowQt

    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / 'ch01.rpy'
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text('label ch01:\n    e "a"\n    jump ch02\n',
                      encoding='utf-8')
    other = script.parent / 'ch02.rpy'
    other.write_text('label ch02:\n    e "b"\n    return\n',
                     encoding='utf-8')
    controller = root / 'game' / 'chapter_controller.rpy'
    controller.write_text(
        'label ch01_control:\n    call ch01\n    return\n', encoding='utf-8')
    before = {p: p.read_bytes() for p in root.rglob('*.rpy')}
    seen = []
    monkeypatch.setattr(D.TakeoverDialog, 'exec',
                        lambda self: (seen.append(self.plan.diff)
                                      or QDialog.Accepted))

    win = MainWindowQt(_Engine(root))
    try:
        win.open_project_path(root)
        win.import_chapter_files([script, other])
        ok = win._takeover_labels(['ch02'])
        assert ok is True, win._console.toPlainText()[-1200:]
        assert seen and 'jump ch02_control' in seen[0], '预览里要看见改哪一行'
        assert 'jump ch02_control' in script.read_text(encoding='utf-8')
        assert 'label ch02_control:' in controller.read_text(encoding='utf-8')
        win.undo_graph()
        assert {p: p.read_bytes() for p in root.rglob('*.rpy')} == before, \
            '撤销必须逐字节还原'
    finally:
        win.close()
        _drain_threads()
