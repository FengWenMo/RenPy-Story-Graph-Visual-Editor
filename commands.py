# commands.py
import copy
import tkinter.messagebox as messagebox
from models import Graph
from renpy_parser import parse_renpy_file

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
    def __init__(self, graph_view, from_id, to_id, etype, condition, option_text):
        self.graph_view = graph_view
        self.from_id = from_id
        self.to_id = to_id
        self.etype = etype
        self.condition = condition
        self.option_text = option_text
        self.edge_id = None

    def execute(self):
        self.edge_id = self.graph_view.graph.add_edge(
            self.from_id, self.to_id, self.etype, self.condition, self.option_text
        )
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        if self.edge_id is not None:
            self.graph_view.graph.remove_edge(self.edge_id)
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
    def __init__(self, graph_view, node_id, new_ops):
        self.graph_view = graph_view
        self.node_id = node_id
        self.new_ops = list(new_ops)
        self.old_ops = None

    def execute(self):
        self.old_ops = list(self.graph_view.graph.nodes[self.node_id].operations)
        self.graph_view.graph.nodes[self.node_id].operations = list(self.new_ops)
        self.graph_view.on_graph_content_changed(self.node_id)

    def undo(self):
        self.graph_view.graph.nodes[self.node_id].operations = list(self.old_ops)
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
            messagebox.showinfo("提示", "没有可撤销的操作")
            return
        command = self.undo_stack.pop()
        command.undo()
        self.redo_stack.append(command)

    def redo(self):
        if not self.redo_stack:
            messagebox.showinfo("提示", "没有可重做的操作")
            return
        command = self.redo_stack.pop()
        command.execute()
        self.undo_stack.append(command)