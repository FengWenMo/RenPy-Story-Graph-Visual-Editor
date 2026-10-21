# commands.py
import copy

from storygraph.core import notify
from storygraph.core.statement import python_operation
from storygraph.parsing.renpy_parser import parse_renpy_file

class Command:
    def execute(self):
        raise NotImplementedError
    def undo(self):
        raise NotImplementedError

class AddNodeCommand(Command):
    def __init__(self, graph_view, label, x, y, operations=None, file_path=None, line_number=None):
        self.graph_view = graph_view
        self.label = label
        self.x = x
        self.y = y
        self.operations = operations
        self.file_path = file_path
        self.line_number = line_number
        self.node_id = None

    def execute(self):
        self.node_id = self.graph_view.graph.add_node(
            self.label, self.x, self.y,
            operations=self.operations,
            file_path=self.file_path,
            line_number=self.line_number
        )
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        if self.node_id is not None:
            self.graph_view.graph.remove_node(self.node_id)
            self.graph_view.on_graph_structure_changed()

class RemoveNodeCommand(Command):
    def __init__(self, graph_view, node_id):
        self.graph_view = graph_view
        self.node_id = node_id
        self.removed_node = None
        self.removed_edges = []

    def execute(self):
        self.removed_node = copy.deepcopy(self.graph_view.graph.nodes[self.node_id])
        self.removed_edges = [copy.deepcopy(e) for e in self.graph_view.graph.edges.values()
                              if e.from_id == self.node_id or e.to_id == self.node_id]
        self.graph_view.graph.remove_node(self.node_id)
        self.graph_view.selected_node = None
        self.graph_view.selected_edge = None
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self.graph_view.graph.nodes[self.node_id] = self.removed_node
        for edge in self.removed_edges:
            self.graph_view.graph.edges[edge.id] = edge
        self.graph_view.on_graph_structure_changed()

class AddEdgeCommand(Command):
    def __init__(self, graph_view, from_id, to_id, etype, condition,
                 option_text, from_clause=''):
        self.graph_view = graph_view
        self.from_id = from_id
        self.to_id = to_id
        self.etype = etype
        self.condition = condition
        self.option_text = option_text
        #: `call X from Y` 的存档点标识（控制流卡同步用）。
        self.from_clause = from_clause
        self.edge_id = None

    def execute(self):
        self.edge_id = self.graph_view.graph.add_edge(
            self.from_id, self.to_id, self.etype, self.condition,
            self.option_text, self.from_clause
        )
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        if self.edge_id is not None:
            self.graph_view.graph.remove_edge(self.edge_id)
            self.graph_view.on_graph_structure_changed()


class RetargetEdgeCommand(Command):
    """
    改一条已有边的目标 / 类型 / from 子句（控制流卡保存后同步图）。

    2026-10-22（P0-4 第一阶段）：卡片上把 `call side` 改成 `call other`
    时，图上的边不能还指着 side —— 否则用户看到的是"改了、图上没变"。
    只改边，不删边再加边：这样边的 id、布局与其它属性都保住。
    """

    def __init__(self, graph_view, edge_id, to_id=None, etype=None,
                 from_clause=None):
        self.graph_view = graph_view
        self.edge_id = edge_id
        edge = graph_view.graph.edges[edge_id]
        self.old = copy.deepcopy(edge)
        self.to_id = edge.to_id if to_id is None else to_id
        self.etype = edge.type if etype is None else etype
        self.from_clause = (getattr(edge, 'from_clause', '')
                            if from_clause is None else from_clause)

    def execute(self):
        edge = self.graph_view.graph.edges[self.edge_id]
        edge.to_id = self.to_id
        edge.type = self.etype
        edge.from_clause = self.from_clause
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self.graph_view.graph.edges[self.edge_id] = self.old
        self.graph_view.on_graph_structure_changed()

class RemoveEdgeCommand(Command):
    def __init__(self, graph_view, edge_id):
        self.graph_view = graph_view
        self.edge_id = edge_id
        self.removed_edge = None

    def execute(self):
        self.removed_edge = copy.deepcopy(self.graph_view.graph.edges[self.edge_id])
        self.graph_view.graph.remove_edge(self.edge_id)
        self.graph_view.selected_edge = None
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self.graph_view.graph.edges[self.edge_id] = self.removed_edge
        self.graph_view.on_graph_structure_changed()

class UpdateNodeOperationsCommand(Command):
    """
    改一个节点的代码块（可撤销）。

    迁移（2026-10-05）：快照存的是**结构化语句**（`node.statements`），
    撤销时整份还原 —— 用 `operations` 字符串往返会把语句上的
    `source`（文件/行号）/ `condition` 元信息丢掉。
    """

    def __init__(self, graph_view, node_id, new_ops, old_ops=None):
        self.graph_view = graph_view
        self.node_id = node_id
        self.new_ops = list(new_ops)
        #: 调用方**已经改完**的情况（"只记历史、不重复 execute"）：
        #: 把改动前的文本显式给进来 —— 这时 `execute()` 不会被调，
        #: 所以快照必须在构造时就备好，否则撤销会恢复不了。
        if old_ops is not None:
            self.old_statements = [python_operation(o) for o in old_ops]
        else:
            self.old_statements = None

    def execute(self):
        node = self.graph_view.graph.nodes[self.node_id]
        if self.old_statements is None:
            self.old_statements = copy.deepcopy(node.statements)
        node.set_operations(self.new_ops)
        self.graph_view.on_graph_content_changed(self.node_id)

    def undo(self):
        node = self.graph_view.graph.nodes[self.node_id]
        if self.old_statements is not None:
            node.statements = copy.deepcopy(self.old_statements)
        self.graph_view.on_graph_content_changed(self.node_id)

class UpdateEdgeCommand(Command):
    def __init__(self, graph_view, edge_id, etype=None, condition=None, option_text=None):
        self.graph_view = graph_view
        self.edge_id = edge_id
        self.new_etype = etype
        self.new_condition = condition
        self.new_option_text = option_text
        self.old_etype = None
        self.old_condition = None
        self.old_option_text = None

    def execute(self):
        edge = self.graph_view.graph.edges[self.edge_id]
        self.old_etype = edge.type
        self.old_condition = edge.condition
        self.old_option_text = edge.option_text
        if self.new_etype is not None:
            edge.type = self.new_etype
        if self.new_condition is not None:
            edge.condition = self.new_condition
        if self.new_option_text is not None:
            edge.option_text = self.new_option_text
        self.graph_view.on_graph_content_changed(edge.from_id)

    def undo(self):
        edge = self.graph_view.graph.edges[self.edge_id]
        edge.type = self.old_etype
        edge.condition = self.old_condition
        edge.option_text = self.old_option_text
        self.graph_view.on_graph_content_changed(edge.from_id)

class SetCurrentNodeCommand(Command):
    def __init__(self, graph_view, node_id):
        self.graph_view = graph_view
        self.node_id = node_id
        self.previous_current = None

    def execute(self):
        for nid, node in self.graph_view.graph.nodes.items():
            if node.is_current:
                self.previous_current = nid
                node.is_current = False
                break
        if self.node_id is not None:
            self.graph_view.graph.nodes[self.node_id].is_current = True
        self.graph_view.on_current_changed()

    def undo(self):
        for nid, node in self.graph_view.graph.nodes.items():
            node.is_current = False
        if self.previous_current is not None:
            self.graph_view.graph.nodes[self.previous_current].is_current = True
        self.graph_view.on_current_changed()

class MoveNodeCommand(Command):
    def __init__(self, graph_view, node_id, old_x, old_y, new_x, new_y):
        self.graph_view = graph_view
        self.node_id = node_id
        self.old_x = old_x
        self.old_y = old_y
        self.new_x = new_x
        self.new_y = new_y

    def execute(self):
        node = self.graph_view.graph.nodes[self.node_id]
        node.x = self.new_x
        node.y = self.new_y
        self.graph_view.update_node_position(self.node_id)

    def undo(self):
        node = self.graph_view.graph.nodes[self.node_id]
        node.x = self.old_x
        node.y = self.old_y
        self.graph_view.update_node_position(self.node_id)

class ClearGraphCommand(Command):
    def __init__(self, graph_view):
        self.graph_view = graph_view
        self.old_graph = None

    def execute(self):
        self.old_graph = self.graph_view.graph.copy()
        self.graph_view.graph.clear()
        self.graph_view.selected_node = None
        self.graph_view.selected_edge = None
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self.graph_view.graph = self.old_graph
        self.graph_view.on_graph_structure_changed()

class ImportRenPyCommand(Command):
    def __init__(self, graph_view, file_path):
        self.graph_view = graph_view
        self.file_path = file_path
        self.old_graph = None

    def execute(self):
        self.old_graph = self.graph_view.graph.copy()
        parse_renpy_file(self.file_path, self.graph_view.graph)
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self.graph_view.graph = self.old_graph
        self.graph_view.on_graph_structure_changed()


class ReplaceGraphCommand(Command):
    """整图替换（用于批量导入后撤销）：graph_view.graph 在两个快照间切换。"""

    def __init__(self, graph_view, new_graph, old_graph):
        self.graph_view = graph_view
        self.new_graph = new_graph
        self.old_graph = old_graph

    def execute(self):
        self.graph_view.graph = self.new_graph
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self.graph_view.graph = self.old_graph
        self.graph_view.on_graph_structure_changed()

class LoadLayoutCommand(Command):
    def __init__(self, graph_view, data):
        self.graph_view = graph_view
        self.data = data
        self.old_graph = None

    def execute(self):
        self.old_graph = self.graph_view.graph.copy()
        self.graph_view._apply_layout_data(self.data)
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self.graph_view.graph = self.old_graph
        self.graph_view.on_graph_structure_changed()

class HistoryManager:
    def __init__(self, graph_view):
        self.graph_view = graph_view
        self.undo_stack = []
        self.redo_stack = []
        self.max_steps = 30

    def execute_command(self, command):
        command.execute()
        self.undo_stack.append(command)
        if len(self.undo_stack) > self.max_steps:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            notify.info('没有可撤销的操作', title='提示')
            return
        command = self.undo_stack.pop()
        command.undo()
        self.redo_stack.append(command)

    def redo(self):
        if not self.redo_stack:
            notify.info('没有可重做的操作', title='提示')
            return
        command = self.redo_stack.pop()
        command.execute()
        self.undo_stack.append(command)