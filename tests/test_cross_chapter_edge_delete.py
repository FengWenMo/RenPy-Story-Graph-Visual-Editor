# -*- coding: utf-8 -*-
"""
P2（2026-10-24）：删一条**另一章**里的源边，不切当前章节。

从前 `edge_delete_plan` 只认当前会话的拍：图上的 `main → side` 来自
`edges.rpy`，而编辑器里开着 `other.rpy` 时，用户只能先切过去再删。
切过去会丢当前未保存的编辑。

现在的路径：

  · 用临时会话读那个文件，按 `line_id` 找到拍（身份对不上就拒绝）；
  · 用拍上的 `src_first` / `src_last` 只删物理行；
  · 不加生成标记、不重排、不碰别的行；
  · 撤销时把旧全文逐字节写回，并把图边放回。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project  # noqa: E402

from storygraph.app.editor import EditorSession  # noqa: E402
from storygraph.graph.models import Graph  # noqa: E402
from storygraph.app.source_line_delete import (  # noqa: E402
    apply_source_line_delete, plan_source_line_delete)
from storygraph.ui_qt.window_nodes import NodeEditMixin  # noqa: E402

EDGES = '''# 删边测试用
label main:
    e "开始"
    call side from _call_side_1
    e "回来之后"
    jump outro

label side:
    e "支线内容"
    return

label outro:
    e "结尾"
    return
'''

OTHER = '''label other:
    e "别处"
    return
'''


class _Host:
    def __init__(self, root, session, graph):
        self._project_root = root
        self._session = session
        self.graph = graph
        self.status: list = []
        self.logs: list = []
        self.add_edge_calls: list = []
        self._delete_line_note = lambda _p: ''

    def _status(self, msg):
        self.status.append(str(msg))

    def _log(self, msg):
        self.logs.append(str(msg))

    def _set_graph(self, graph, keep_view=None):
        self.graph = graph

    def _run_command(self, command):
        command.execute()
        return True

    def on_graph_structure_changed(self):
        pass


_Host.edge_delete_plan = NodeEditMixin.edge_delete_plan
_Host._plan_source_file_delete = NodeEditMixin._plan_source_file_delete
_Host._edge_archive_note = NodeEditMixin._edge_archive_note
_Host.delete_edge = NodeEditMixin.delete_edge
_Host._confirm_and_delete_source_file_edge = \
    NodeEditMixin._confirm_and_delete_source_file_edge


@pytest.fixture
def project(tmp_path):
    root = build_project(tmp_path / 'proj')
    (root / 'game/script/edges.rpy').write_text(EDGES, encoding='utf-8')
    (root / 'game/script/other.rpy').write_text(OTHER, encoding='utf-8')
    return root


def _setup(project):
    edges = project / 'game/script/edges.rpy'
    other = project / 'game/script/other.rpy'
    session = EditorSession(project_root=project)
    res = session.import_chapters([edges], Graph())
    assert res['ok'], res.get('error')
    graph = res['graph']
    session.load(other)
    assert Path(session.script_path).name == 'other.rpy'
    return session, graph, _Host(project, session, graph), edges, other


def _flow_edge(graph):
    return next(e for e in graph.edges.values()
                if e.type == 'call' and e.origin == 'source')


def test_plan_finds_the_physical_line_in_the_other_chapter(project):
    _s, graph, host, edges, _other = _setup(project)
    plan = host.edge_delete_plan(_flow_edge(graph))
    assert plan['ok'] is True, plan
    assert plan['kind'] == 'source_file'
    assert plan['source_plan'].path == str(edges)
    assert plan['source_plan'].start == 4
    assert 'call side' in plan['text']


def test_plan_does_not_switch_the_current_chapter(project):
    session, graph, host, _e, _o = _setup(project)
    before = session.script_path
    host.edge_delete_plan(_flow_edge(graph))
    assert session.script_path == before


def test_delete_removes_exactly_that_line_and_keeps_the_rest(project,
                                                              monkeypatch):
    _s, graph, host, edges, other = _setup(project)
    before = edges.read_text(encoding='utf-8')
    other_before = other.read_bytes()
    edge = _flow_edge(graph)

    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    host.delete_edge(edge.id)

    after = edges.read_text(encoding='utf-8')
    assert 'call side' not in after
    assert '开始' in after and '回来之后' in after and '支线内容' in after
    assert after.count('label ') == before.count('label ')
    assert other.read_bytes() == other_before, '不该碰当前这一章'
    assert edge.id not in graph.edges


def test_cancel_changes_neither_file_nor_graph(project, monkeypatch):
    _s, graph, host, edges, _o = _setup(project)
    before = edges.read_bytes()
    edge = _flow_edge(graph)

    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QMessageBox.No))
    host.delete_edge(edge.id)

    assert edges.read_bytes() == before
    assert edge.id in graph.edges


def test_stale_line_id_is_refused_not_guessed(project):
    _s, graph, host, _e, _o = _setup(project)
    edge = _flow_edge(graph)
    edge.line_id = 'main.9999'
    plan = host.edge_delete_plan(edge)
    assert plan['ok'] is False
    assert '改过' in plan['error'] or '找不到' in plan['error']


def test_source_file_changed_after_plan_writes_nothing(project):
    _s, _g, _h, edges, _o = _setup(project)
    plan = plan_source_line_delete(project, edges, 'main.0002')
    assert plan.ok is True
    edges.write_text(plan.old_text + '\n# 新加的一行\n', encoding='utf-8')
    before = edges.read_bytes()
    res = apply_source_line_delete(plan)
    assert res['ok'] is False
    assert '又被改过' in res['error']
    assert edges.read_bytes() == before


def test_command_undo_restores_bytes_and_edge(project):
    _s, graph, host, edges, _o = _setup(project)
    before = edges.read_bytes()
    edge = _flow_edge(graph)
    plan = host.edge_delete_plan(edge)
    assert plan['ok']

    from storygraph.graph.commands import DeleteSourceLineCommand
    cmd = DeleteSourceLineCommand(host, plan['source_plan'], edge.id)
    cmd.execute()
    assert edges.read_bytes() != before
    assert edge.id not in graph.edges

    cmd.undo()
    assert edges.read_bytes() == before, '撤销必须逐字节还原'
    assert edge.id in graph.edges
