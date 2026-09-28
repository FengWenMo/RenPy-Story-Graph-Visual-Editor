"""
tests/test_models_commands.py
=============================
Graph 数据结构与 Command 撤销 / 重做测试。

用一个 FakeGraphView 替代真实 GraphView（需要 tkinter 画布），
只实现 commands.py 实际用到的回调接口。

重点：
  · Graph.copy 必须是深拷贝（operations 不能被共享）
  · remove_node 级联删除相连边
  · 各 Command 的 execute/undo 必须恢复原状
"""

import pytest

from storygraph.graph.models import Graph, Node, Edge
from storygraph.graph.commands import (
    AddNodeCommand, RemoveNodeCommand,
    AddEdgeCommand, RemoveEdgeCommand,
    UpdateNodeOperationsCommand, UpdateEdgeCommand,
    SetCurrentNodeCommand, MoveNodeCommand, ClearGraphCommand,
    HistoryManager,
)


class FakeGraphView:
    """commands 模块所需的最小视图契约。"""

    def __init__(self):
        self.graph = Graph()
        self.selected_node = None
        self.selected_edge = None
        self.events = []

    def on_graph_structure_changed(self):
        self.events.append('structure')

    def on_graph_content_changed(self, nid=None):
        self.events.append(('content', nid))

    def on_current_changed(self):
        self.events.append('current')

    def update_node_position(self, nid):
        self.events.append(('move', nid))


@pytest.fixture
def view():
    return FakeGraphView()


# ------------------------------------------------------------------
#  Graph
# ------------------------------------------------------------------

class TestGraph:
    def test_add_node_increments_id(self):
        g = Graph()
        a = g.add_node('start', 0, 0)
        b = g.add_node('end', 0, 0)
        assert (a, b) == (1, 2)

    def test_add_node_default_positions(self):
        g = Graph()
        nid = g.add_node('start', 0, 0)
        n = g.nodes[nid]
        assert n.x == 0 and n.y == 0
        assert n.operations == []

    def test_add_node_stores_metadata(self):
        g = Graph()
        nid = g.add_node('start', 0, 0, operations=['x = 1'],
                         file_path='s.rpy', line_number=10)
        assert g.nodes[nid].operations == ['x = 1']
        assert g.nodes[nid].file_path == 's.rpy'
        assert g.nodes[nid].line_number == 10

    def test_add_node_operations_shared_reference(self):
        """
        已知行为（非 bug 但易踩）：operations 直接存传入的 list 引用。
        外部修改源 list 会影响节点。测试固化该语义，防止未来意外改动。
        """
        g = Graph()
        ops = ['x = 1']
        nid = g.add_node('start', 0, 0, operations=ops)
        ops.append('y = 2')
        assert g.nodes[nid].operations is ops

    def test_add_edge_increments_id(self):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        e1 = g.add_edge(a, b)
        e2 = g.add_edge(b, a)
        assert (e1, e2) == (1, 2)

    def test_remove_node_also_removes_attached_edges(self):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(a, b)
        g.add_edge(c, a)      # 入边
        g.add_edge(b, c)      # 无关边
        g.remove_node(a)
        assert a not in g.nodes
        remaining_targets = {e.to_id for e in g.edges.values()}
        remaining_sources = {e.from_id for e in g.edges.values()}
        assert a not in remaining_targets and a not in remaining_sources
        assert len(g.edges) == 1

    def test_remove_node_missing_is_noop(self):
        g = Graph()
        g.remove_node(999)   # 不应抛

    def test_remove_edge_missing_is_noop(self):
        g = Graph()
        g.remove_edge(999)

    def test_clear_resets_counters(self):
        g = Graph()
        g.add_node('a', 0, 0)
        g.clear()
        assert g.nodes == {} and g.edges == {}
        assert g.next_node_id == 1 and g.next_edge_id == 1

    def test_copy_is_deep(self):
        g = Graph()
        nid = g.add_node('a', 0, 0, operations=['x = 1'])
        other = g.copy()
        other.nodes[nid].operations.append('y = 2')
        other.nodes[nid].label_name = 'changed'
        assert g.nodes[nid].operations == ['x = 1']
        assert g.nodes[nid].label_name == 'a'

    def test_copy_preserves_counters(self):
        g = Graph()
        g.add_node('a', 0, 0)
        g.add_node('b', 0, 0)
        other = g.copy()
        assert other.next_node_id == g.next_node_id
        assert other.next_edge_id == g.next_edge_id

    def test_node_defaults(self):
        n = Node(1, 'start')
        assert n.is_current is False
        assert n.operations == []
        assert n.file_path is None

    def test_edge_defaults(self):
        e = Edge(1, 1, 2)
        assert e.type == 'jump'
        assert e.condition == ''
        assert e.option_text == ''


# ------------------------------------------------------------------
#  Command: 节点
# ------------------------------------------------------------------

class TestNodeCommands:
    def test_add_node_undo(self, view):
        cmd = AddNodeCommand(view, 'start', 5, 6)
        cmd.execute()
        assert len(view.graph.nodes) == 1
        nid = cmd.node_id
        cmd.undo()
        assert nid not in view.graph.nodes

    def test_add_node_undo_before_execute_is_safe(self, view):
        cmd = AddNodeCommand(view, 'start', 0, 0)
        cmd.undo()
        assert view.graph.nodes == {}

    def test_remove_node_undo_restores_edges(self, view):
        a = view.graph.add_node('a', 0, 0)
        b = view.graph.add_node('b', 0, 0)
        view.graph.add_edge(a, b)
        cmd = RemoveNodeCommand(view, a)
        cmd.execute()
        assert a not in view.graph.nodes and not view.graph.edges
        cmd.undo()
        assert a in view.graph.nodes and len(view.graph.edges) == 1

    def test_operations_undo_restores_previous(self, view):
        nid = view.graph.add_node('a', 0, 0, operations=['x = 1'])
        cmd = UpdateNodeOperationsCommand(view, nid, ['y = 2'])
        cmd.execute()
        assert view.graph.nodes[nid].operations == ['y = 2']
        cmd.undo()
        assert view.graph.nodes[nid].operations == ['x = 1']

    def test_move_node_undo(self, view):
        nid = view.graph.add_node('a', 0, 0)
        cmd = MoveNodeCommand(view, nid, 0, 0, 100, 200)
        cmd.execute()
        assert (view.graph.nodes[nid].x, view.graph.nodes[nid].y) == (100, 200)
        cmd.undo()
        assert (view.graph.nodes[nid].x, view.graph.nodes[nid].y) == (0, 0)


# ------------------------------------------------------------------
#  Command: 边
# ------------------------------------------------------------------

class TestEdgeCommands:
    def test_add_edge_undo(self, view):
        a = view.graph.add_node('a', 0, 0)
        b = view.graph.add_node('b', 0, 0)
        cmd = AddEdgeCommand(view, a, b, 'jump', 'x>1', '')
        cmd.execute()
        assert len(view.graph.edges) == 1
        cmd.undo()
        assert view.graph.edges == {}

    def test_remove_edge_undo(self, view):
        a = view.graph.add_node('a', 0, 0)
        b = view.graph.add_node('b', 0, 0)
        eid = view.graph.add_edge(a, b)
        cmd = RemoveEdgeCommand(view, eid)
        cmd.execute()
        assert view.graph.edges == {}
        cmd.undo()
        assert eid in view.graph.edges

    def test_update_edge_partial_change_keeps_rest(self, view):
        a = view.graph.add_node('a', 0, 0)
        b = view.graph.add_node('b', 0, 0)
        eid = view.graph.add_edge(a, b, 'jump', 'old', 'old_opt')
        edge = view.graph.edges[eid]
        cmd = UpdateEdgeCommand(view, eid, condition='new')
        cmd.execute()
        assert edge.condition == 'new'
        assert edge.type == 'jump' and edge.option_text == 'old_opt'
        cmd.undo()
        assert edge.condition == 'old'

    def test_update_edge_undo_before_snapshot_value(self, view):
        """None 表示"不修改"，undo 应用回旧值而非 None。"""
        a = view.graph.add_node('a', 0, 0)
        b = view.graph.add_node('b', 0, 0)
        eid = view.graph.add_edge(a, b, 'jump', 'c1', 'o1')
        cmd = UpdateEdgeCommand(view, eid, etype='call')
        cmd.execute()
        cmd.undo()
        edge = view.graph.edges[eid]
        assert edge.type == 'jump'


# ------------------------------------------------------------------
#  Command: 当前节点 / 清空
# ------------------------------------------------------------------

class TestMiscCommands:
    def test_set_current_moves_flag(self, view):
        a = view.graph.add_node('a', 0, 0)
        b = view.graph.add_node('b', 0, 0)
        SetCurrentNodeCommand(view, a).execute()
        assert view.graph.nodes[a].is_current
        cmd = SetCurrentNodeCommand(view, b)
        cmd.execute()
        assert view.graph.nodes[b].is_current
        assert not view.graph.nodes[a].is_current
        cmd.undo()
        assert view.graph.nodes[a].is_current
        assert not view.graph.nodes[b].is_current

    def test_set_current_none_clears_all(self, view):
        a = view.graph.add_node('a', 0, 0)
        view.graph.nodes[a].is_current = True
        SetCurrentNodeCommand(view, None).execute()
        assert not any(n.is_current for n in view.graph.nodes.values())

    def test_clear_graph_undo(self, view):
        view.graph.add_node('a', 0, 0, operations=['x = 1'])
        cmd = ClearGraphCommand(view)
        cmd.execute()
        assert view.graph.nodes == {}
        cmd.undo()
        assert len(view.graph.nodes) == 1
        assert next(iter(view.graph.nodes.values())).operations == ['x = 1']


# ------------------------------------------------------------------
#  HistoryManager
# ------------------------------------------------------------------

class TestHistoryManager:
    def test_undo_redo_flow(self, view):
        h = HistoryManager(view)
        cmd = AddNodeCommand(view, 'a', 0, 0)
        h.execute_command(cmd)
        assert len(view.graph.nodes) == 1
        h.undo()
        assert len(view.graph.nodes) == 0
        h.redo()
        assert len(view.graph.nodes) == 1

    def test_new_command_clears_redo_stack(self, view):
        h = HistoryManager(view)
        h.execute_command(AddNodeCommand(view, 'a', 0, 0))
        h.undo()
        h.execute_command(AddNodeCommand(view, 'b', 0, 0))
        h.redo()      # 栈已清空，应提示无可重做
        assert len(h.undo_stack) == 1

    def test_undo_stack_capped(self, view):
        h = HistoryManager(view)
        h.max_steps = 3
        for i in range(5):
            h.execute_command(AddNodeCommand(view, f'n{i}', 0, 0))
        assert len(h.undo_stack) == 3

    def test_undo_empty_stack_is_safe(self, view):
        h = HistoryManager(view)
        h.undo()
        h.redo()
