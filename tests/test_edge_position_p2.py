# -*- coding: utf-8 -*-
"""
P2（2026-10-24）：草图落点可以**改**，不再是"选错只能删了重画"。

判据：

  · 对话框列出章末 + 本章每一拍；不能接的拍置灰；
  · 当前那拍还在 → 回显选中的它；不在了 → 明说，只让人重选；
  · 改落点只改图上的 `position/position_line_id`，**剧本一个字节不动**；
  · 进图级撤销栈，Ctrl+Z 能退回原来的落点。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QDialog, QDialogButtonBox  # noqa: E402

from storygraph.graph.models import Graph  # noqa: E402
from storygraph.graph.commands import SetEdgePositionCommand  # noqa: E402
from storygraph.ui_qt.dialogs import EdgePositionDialog  # noqa: E402
from storygraph.ui_qt.window_nodes import NodeEditMixin  # noqa: E402


POSITIONS = {'ok': True, 'items': [
    {'line_id': 'A.0001', 'index': 0, 'number': 1, 'total': 3,
     'text': 'e：第一句', 'selectable': True, 'note': '接在第 1 拍之后'},
    {'line_id': 'A.0002', 'index': 1, 'number': 2, 'total': 3,
     'text': 'jump B', 'selectable': False,
     'note': '第 2 拍已经有去向（jump B），后面接不上'},
]}


class _Host:
    def __init__(self, graph):
        self.graph = graph
        self.status: list = []
        self.logs: list = []
        self._beat_context = lambda: {}
        self._delete_line_note = lambda _p: ''

    def _status(self, msg):
        self.status.append(str(msg))

    def _log(self, msg):
        self.logs.append(str(msg))

    def _set_graph(self, graph, keep_view=None):
        self.graph = graph

    def _run_command(self, cmd):
        cmd.execute()
        return True

    def on_graph_content_changed(self, nid=None):
        pass

    def edge_position_choices(self, _from_id):
        return POSITIONS

    def change_edge_position(self, edge):
        return NodeEditMixin.change_edge_position(self, edge)


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _graph():
    g = Graph()
    a = g.add_node('A', 0, 0)
    b = g.add_node('B', 100, 0)
    eid = g.add_edge(a, b, etype='jump', origin='manual')
    return g, a, b, eid


# ===========================================================================
#  一、对话框
# ===========================================================================

class TestDialog:
    def test_lists_chapter_end_and_every_beat(self, qapp):
        dlg = EdgePositionDialog(None, positions=POSITIONS)
        assert dlg._pos.count() == 3
        assert dlg._pos.itemData(0) == ('chapter_end', '')
        assert dlg._pos.itemData(1) == ('beat', 'A.0001')
        assert dlg._pos.model().item(2).isEnabled() is False, '不能接的拍要置灰'
        dlg.close()

    def test_preselects_the_current_beat(self, qapp):
        dlg = EdgePositionDialog(None, positions=POSITIONS,
                                 current_position='beat',
                                 current_line_id='A.0001')
        assert dlg._pos.currentData() == ('beat', 'A.0001')
        assert dlg._lost_current is False
        dlg.close()

    def test_current_beat_gone_is_reported_not_silently_changed(self, qapp):
        dlg = EdgePositionDialog(None, positions=POSITIONS,
                                 current_position='beat',
                                 current_line_id='A.9999')
        assert dlg._lost_current is True
        assert '不在本章' in dlg._note.text()
        assert dlg._pos.currentData() == ('chapter_end', '')
        dlg.close()

    def test_accept_returns_the_selected_position(self, qapp):
        dlg = EdgePositionDialog(None, positions=POSITIONS)
        dlg._pos.setCurrentIndex(1)
        dlg._on_accept()
        assert dlg.result() == QDialog.Accepted
        assert dlg.position == 'beat'
        assert dlg.position_line_id == 'A.0001'
        dlg.close()

    def test_accept_chapter_end_clears_the_line_id(self, qapp):
        dlg = EdgePositionDialog(None, positions=POSITIONS,
                                 current_position='beat',
                                 current_line_id='A.0001')
        dlg._pos.setCurrentIndex(0)
        dlg._on_accept()
        assert (dlg.position, dlg.position_line_id) == ('chapter_end', '')
        dlg.close()


# ===========================================================================
#  二、命令：改落点 + 撤销
# ===========================================================================

class TestCommand:
    def test_sets_position_and_undo_restores_it(self):
        g, _a, _b, eid = _graph()
        host = _Host(g)
        edge = g.edges[eid]
        edge.position = 'chapter_end'
        edge.position_line_id = ''
        cmd = SetEdgePositionCommand(host, eid, 'beat', 'A.0001')
        cmd.execute()
        assert edge.position == 'beat'
        assert edge.position_line_id == 'A.0001'
        cmd.undo()
        assert edge.position == 'chapter_end'
        assert edge.position_line_id == ''

    def test_setting_chapter_end_clears_the_line_id(self):
        g, _a, _b, eid = _graph()
        host = _Host(g)
        edge = g.edges[eid]
        edge.position = 'beat'
        edge.position_line_id = 'A.0001'
        cmd = SetEdgePositionCommand(host, eid, 'chapter_end', 'A.0001')
        cmd.execute()
        assert edge.position == 'chapter_end'
        assert edge.position_line_id == ''


# ===========================================================================
#  三、窗口：选中后走命令，不改剧本
# ===========================================================================

def test_change_edge_position_does_not_touch_the_script(qapp, monkeypatch):
    g, _a, _b, eid = _graph()
    host = _Host(g)

    def fake_exec(self):
        self._pos.setCurrentIndex(1)
        self._sync()
        return QDialog.Accepted

    monkeypatch.setattr(EdgePositionDialog, 'exec', fake_exec)
    assert host.change_edge_position(g.edges[eid]) is True
    edge = g.edges[eid]
    assert (edge.position, edge.position_line_id) == ('beat', 'A.0001')
    assert any('Ctrl+Z' in s for s in host.status), host.status


def test_no_change_returns_false(qapp, monkeypatch):
    g, _a, _b, eid = _graph()
    host = _Host(g)

    def fake_exec(self):
        return QDialog.Accepted  # 当前就是章末

    monkeypatch.setattr(EdgePositionDialog, 'exec', fake_exec)
    assert host.change_edge_position(g.edges[eid]) is False


def test_cancel_changes_nothing(qapp, monkeypatch):
    g, _a, _b, eid = _graph()
    host = _Host(g)

    def fake_exec(self):
        self._pos.setCurrentIndex(1)
        return QDialog.Rejected

    monkeypatch.setattr(EdgePositionDialog, 'exec', fake_exec)
    assert host.change_edge_position(g.edges[eid]) is False
    assert g.edges[eid].position == 'chapter_end'


def test_source_edge_is_not_offered_position_editing(qapp):
    g, _a, _b, eid = _graph()
    g.edges[eid].origin = 'source'
    host = _Host(g)
    assert host.change_edge_position(g.edges[eid]) is False
