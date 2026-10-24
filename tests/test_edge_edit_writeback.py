# -*- coding: utf-8 -*-
"""
「编辑连线」直接改动**对应的地方**（2026-10-24 用户追问）。

用户原话：

> 我查看属性的时候……我希望我能够就这样直接编辑它
> （就在这个界面内编然后直接改动对应的地方）

判据：

  1. 编排线改「类型 / 到」→ 直接改写 `X_control` 里那一行（立刻落盘、可撤销）；
  2. 源边改「类型 / 到」→ 直接改写剧本里那一行（内存、导出才落盘、可撤销）；
  3. 起点章没打开时，接线窗口照样列出**它的**拍；点创建时按需载入它再写，
     不要求用户先手动开章（用户抓到的"从 1 跳到 2 就不能选"的 bug）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QDialog  # noqa: E402

from storygraph.graph.commands import (EditControlRouteCommand,  # noqa: E402
                                       EditSourceFlowCommand)
from storygraph.graph.models import Graph  # noqa: E402
from storygraph.story.control_route import (apply_control_edit,  # noqa: E402
                                            plan_control_edit)

from _ui_project import build_project  # noqa: E402
from test_wire_landing import _Engine, _drain_threads  # noqa: E402


CHAPTERS = {
    'chapter0_0': '''label chapter0_0:
    e "开头"
    return
''',
    'chapter1_2': '''label chapter1_2:
    e "第一句"
    jump chapter1_3

label chapter1_3:
    e "下一章"
    return
''',
}

CONTROL = '''label chapter0_0_control:
    call chapter0_0
    jump chapter1_2_control

label chapter1_2_control:
    call chapter1_2
    return

label chapter1_3_control:
    call chapter1_3
    return
'''


def _project(tmp_path):
    root = build_project(tmp_path / 'proj')
    script_dir = root / 'game' / 'script'
    for name, text in CHAPTERS.items():
        (script_dir / f'{name}.rpy').write_text(text, encoding='utf-8')
    control = root / 'game' / 'chapter_controller.rpy'
    control.write_text(CONTROL, encoding='utf-8')
    return root, script_dir, control


class _View:
    def __init__(self, graph):
        self.graph = graph

    def on_graph_structure_changed(self):
        pass


# ===========================================================================
#  一、编排层那一行：计划 + 应用 + 撤销
# ===========================================================================

class TestControlLine:
    def test_jump_target_is_rewritten_in_place(self, tmp_path):
        root, _scripts, control = _project(tmp_path)
        plan = plan_control_edit(root, 'chapter0_0', 'chapter1_2',
                                 'jump', 'chapter1_3', 'jump')
        assert plan.ok, plan.reason
        assert plan.new_lines == ['    jump chapter1_3_control']

        res = apply_control_edit(plan)
        assert res['ok'], res.get('error')
        text = control.read_text(encoding='utf-8')
        assert 'jump chapter1_3_control' in text
        assert 'jump chapter1_2_control' not in text

    def test_jump_to_call_appends_return_so_it_does_not_fall_through(
            self, tmp_path):
        root, _scripts, _control = _project(tmp_path)
        plan = plan_control_edit(root, 'chapter0_0', 'chapter1_2',
                                 'jump', 'chapter1_2', 'call')
        assert plan.ok, plan.reason
        assert plan.new_lines == ['    call chapter1_2', '    return']

    def test_call_to_jump_is_refused_when_routes_follow_it(self, tmp_path):
        root, _scripts, control = _project(tmp_path)
        control.write_text(
            'label chapter0_0_control:\n'
            '    call chapter1_2\n'
            '    jump chapter1_3_control\n'
            '\n'
            'label chapter1_2_control:\n'
            '    call chapter1_2\n'
            '    return\n'
            '\n'
            'label chapter1_3_control:\n'
            '    call chapter1_3\n'
            '    return\n',
            encoding='utf-8')
        plan = plan_control_edit(root, 'chapter0_0', 'chapter1_2',
                                 'call', 'chapter1_3', 'jump')
        assert plan.ok is False
        assert '永远不执行' in plan.reason

    def test_command_updates_the_edge_and_undo_restores_the_line(self, tmp_path):
        root, _scripts, control = _project(tmp_path)
        g = Graph()
        a = g.add_node('chapter0_0', 0, 0)
        b = g.add_node('chapter1_2', 200, 0)
        c = g.add_node('chapter1_3', 400, 0)
        eid = g.add_edge(a, b, 'jump', '', '', '', origin='control',
                         position='control_end')
        view = _View(g)
        plan = plan_control_edit(root, 'chapter0_0', 'chapter1_2',
                                 'jump', 'chapter1_3', 'jump')
        cmd = EditControlRouteCommand(view, plan, eid, c, 'jump',
                                      project_root=root)
        cmd.execute()
        assert g.edges[eid].to_id == c
        assert 'jump chapter1_3_control' in control.read_text(encoding='utf-8')

        cmd.undo()
        assert g.edges[eid].to_id == b
        text = control.read_text(encoding='utf-8')
        assert 'jump chapter1_2_control' in text
        assert 'jump chapter1_3_control' not in text.split(
            'label chapter1_2_control')[0], text


# ===========================================================================
#  二、源边那一行：命令 + 撤销
# ===========================================================================

class TestSourceLine:
    def test_move_command_moves_a_flow_line_and_undo_puts_it_back(
            self, tmp_path):
        """源边改插入位置 = 移动那一拍；块结构由树维护，撤销走结构栈。"""
        from storygraph.app.editor import EditorSession
        from storygraph.graph.commands import MoveSourceFlowCommand

        root = build_project(tmp_path / 'proj')
        script = root / 'game' / 'script' / 'move.rpy'
        script.write_text(
            'label move:\n'
            '    e "第一句"\n'
            '    e "第二句"\n'
            '    jump target\n'
            '    return\n\n'
            'label target:\n'
            '    return\n', encoding='utf-8')
        session = EditorSession(project_root=root)
        session.load(script)
        before = [str(getattr(b, 'raw', '') or '').strip()
                  for b in session.beats]
        jump = next(i for i, b in enumerate(session.beats)
                    if str(getattr(b, 'raw', '') or '').strip()
                    == 'jump target')
        cmd = MoveSourceFlowCommand(_View(Graph()), session, jump, 1)
        cmd.execute()
        order = [str(getattr(b, 'raw', '') or '').strip()
                 for b in session.beats]
        assert order.index('jump target') == 1, order
        cmd.undo()
        assert [str(getattr(b, 'raw', '') or '').strip()
                for b in session.beats] == before

    def test_command_rewrites_the_flow_beat_and_undo_puts_it_back(self,
                                                                  tmp_path):
        from storygraph.app.editor import EditorSession

        root, scripts, _control = _project(tmp_path)
        session = EditorSession(project_root=root)
        session.load(scripts / 'chapter1_2.rpy')
        beat = next(b for b in session.beats
                    if str(getattr(b, 'raw', '') or '').strip()
                    == 'jump chapter1_3')
        g = Graph()
        a = g.add_node('chapter1_2', 0, 0)
        b = g.add_node('chapter1_3', 200, 0)
        eid = g.add_edge(a, b, 'jump', '', '', '', origin='source',
                         line_id=beat.line_id, position='beat',
                         position_line_id=beat.line_id)
        view = _View(g)
        cmd = EditSourceFlowCommand(view, session, beat, 'call', 'chapter1_3')
        cmd.execute()
        assert beat.raw.strip() == 'call chapter1_3'
        assert g.edges[eid].type == 'jump', '类型由 _refresh_flow_edges 派生；这里没传回调'

        cmd.undo()
        assert beat.raw.strip() == 'jump chapter1_3'


# ===========================================================================
#  三、真窗口：编辑连线直接改文件 / 按需载入起点章
# ===========================================================================

@pytest.fixture(scope='module')
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _window(root):
    from storygraph.ui_qt.main_window import MainWindowQt
    win = MainWindowQt(_Engine(root))
    win.open_project_path(root)
    return win


def _ids(win):
    return {n.label_name: nid for nid, n in (win.graph.nodes or {}).items()}


def test_control_edge_can_be_edited_in_the_window(qt_app, tmp_path,
                                                  monkeypatch):
    root, scripts, control = _project(tmp_path)
    win = _window(root)
    try:
        win.import_chapter_files(
            [scripts / 'chapter0_0.rpy', scripts / 'chapter1_2.rpy'])
        ids = _ids(win)
        eid = win.graph.add_edge(ids['chapter0_0'], ids['chapter1_2'],
                                 'jump', '', '', '', origin='control',
                                 position='control_end')
        target = ids['chapter1_3']

        class Dlg:
            def __init__(self, *args, **kwargs):
                self.etype = 'jump'
                self.to_id = target
                self.changed = True
                self.condition = ''
                self.option_text = ''
                self.from_clause = ''
                self.position = 'chapter_end'
                self.position_line_id = ''

            def exec(self):
                return QDialog.Accepted

        monkeypatch.setattr('storygraph.ui_qt.dialogs.EditEdgeDialog', Dlg)
        assert win.edit_edge(win.graph.edges[eid]) is True
        assert win.graph.edges[eid].to_id == target
        assert 'jump chapter1_3_control' in control.read_text(encoding='utf-8')

        win.undo_graph()
        assert win.graph.edges[eid].to_id == ids['chapter1_2']
        assert 'jump chapter1_2_control' in control.read_text(encoding='utf-8')
    finally:
        win.close()
        _drain_threads()


def test_source_edge_edit_loads_its_chapter_and_rewrites_the_line(
        qt_app, tmp_path, monkeypatch):
    root, scripts, _control = _project(tmp_path)
    win = _window(root)
    try:
        win.import_chapter_files(
            [scripts / 'chapter1_2.rpy', scripts / 'chapter0_0.rpy'])
        ids = _ids(win)
        edge = next(e for e in win.graph.edges.values()
                    if e.from_id == ids['chapter1_2']
                    and e.to_id == ids['chapter1_3']
                    and str(getattr(e, 'origin', '')) == 'source')
        eid, line_id = edge.id, str(edge.line_id)
        assert line_id, '导入之后源边应带着那一拍的身份'
        # 模拟用户正开着**别的**章：源章没载入
        win.load_script(scripts / 'chapter0_0.rpy', goto_card=False)

        class Dlg:
            def __init__(self, *args, **kwargs):
                self.etype = 'call'
                self.to_id = ids['chapter1_3']
                self.changed = True
                self.condition = ''
                self.option_text = ''
                self.from_clause = ''
                self.position = 'beat'
                self.position_line_id = line_id

            def exec(self):
                return QDialog.Accepted

        monkeypatch.setattr('storygraph.ui_qt.dialogs.EditEdgeDialog', Dlg)
        assert win.edit_edge(win.graph.edges[eid]) is True
        assert win._session.script_path.name == 'chapter1_2.rpy', \
            '改源边之前必须先把那一章载入（按需，不要求用户手开）'
        raw = next(b.raw.strip() for b in win._session.beats
                   if b.line_id == line_id)
        assert raw == 'call chapter1_3'
        assert win.graph.edges[eid].type == 'call'

        win.undo_graph()
        raw = next(b.raw.strip() for b in win._session.beats
                   if b.line_id == line_id)
        assert raw == 'jump chapter1_3'
    finally:
        win.close()
        _drain_threads()


def test_add_edge_from_a_chapter_that_is_not_open_lists_its_beats_and_writes(
        qt_app, tmp_path, monkeypatch):
    """
    用户抓到的 bug 的端到端判据：当前开着 `chapter0_0`，从 `chapter1_2` 拖线
    → 位置列表里必须有 `chapter1_2` 的拍；选一拍创建 → 自动载入 `chapter1_2`
    并把那一行写进去（不是只剩章末 / 不是只画草图）。
    """
    from storygraph.ui_qt import dialogs as D

    root, scripts, _control = _project(tmp_path)
    win = _window(root)
    seen = {}
    try:
        win.import_chapter_files(
            [scripts / 'chapter0_0.rpy', scripts / 'chapter1_2.rpy'])
        ids = _ids(win)
        win.load_script(scripts / 'chapter0_0.rpy', goto_card=False)
        assert win._session.script_path.name == 'chapter0_0.rpy'

        def _accept(dlg):
            labels = [dlg._pos.itemText(i) for i in range(dlg._pos.count())]
            seen['labels'] = labels
            # 选「第 1 拍」（台词那一拍；第 2 拍是 jump，接不上）
            dlg._pos.setCurrentIndex(1)
            dlg._on_accept()
            return QDialog.Accepted

        monkeypatch.setattr(D.AddEdgeDialog, 'exec', _accept)
        win.add_edge(from_id=ids['chapter1_2'], to_id=ids['chapter1_3'])

        assert any('第 1 拍' in text for text in seen['labels']), seen['labels']
        assert win._session.script_path.name == 'chapter1_2.rpy', \
            '写回之前没有按需载入起点章'
        raws = [b.raw.strip() for b in win._session.beats
                if b.label == 'chapter1_2']
        assert raws == ['', 'jump chapter1_3', 'jump chapter1_3'], raws
    finally:
        win.close()
        _drain_threads()
