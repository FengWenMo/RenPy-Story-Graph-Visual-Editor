# commands.py
import copy

from storygraph.core import notify
from storygraph.core.safeguard import swallow
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
                 option_text, from_clause='', origin='manual'):
        self.graph_view = graph_view
        self.from_id = from_id
        self.to_id = to_id
        self.etype = etype
        self.condition = condition
        self.option_text = option_text
        #: `call X from Y` 的存档点标识（控制流卡同步用）。
        self.from_clause = from_clause
        #: 默认 **manual**：这个命令的常规用法是"用户在图上画一条线"。
        #: 控制流卡同步出来的边要显式传 `origin='source'`（它代表源文件里的
        #: `call` / `jump`，第二阶段的"从拍派生"会按这个来源区分重建范围）。
        self.origin = str(origin or 'manual')
        self.edge_id = None

    def execute(self):
        self.edge_id = self.graph_view.graph.add_edge(
            self.from_id, self.to_id, self.etype, self.condition,
            self.option_text, self.from_clause, origin=self.origin
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

    `beat_states`（2026-10-22，变量工作台）：这条改动**同时**落在图节点和
    章节的拍上（`session.beats`），而图级撤销从前只退节点 —— 撤销之后
    "工作台里那条没了、导出里却还在"（实测：Ctrl+Z 之后 `compile()` 仍带
    `$ mark = 1`）。所以把"改之前 / 改之后"的**逐拍快照**一并带进来：

        {'session': 会话, 'before': {拍号: 快照}, 'after': {…},
         'refresh': 可选回调（让卡片页列表跟上）}

    用逐拍快照而不是整份 `beats` 深拷贝：拍对象保持同一个身份，
    `ChapterDocument` 的树、卡片页的映射都还指得对。
    """

    def __init__(self, graph_view, node_id, new_ops, old_ops=None,
                 beat_states=None):
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
        self.beat_states = dict(beat_states or {})

    def _restore_beats(self, which: str):
        """
        把拍退到 `before` / 进到 `after`（没有快照就什么都不做）。

        还原**前先对一下现在的样子**是否就是另一头（撤销前应当等于 `after`、
        重做前应当等于 `before`）：原地导出会重新解析文件，拍数 / 粒度可能变
        （只有注释的卡会并进后面那张），此时按拍号硬还原会把快照摊到
        **别人的拍**上。对不上就跳过那一拍，并在诊断台账里留一行 ——
        宁可少退一拍，也不能改错拍。
        """
        st = self.beat_states or {}
        session = st.get('session')
        snaps = st.get(which) or {}
        if session is None or not snaps:
            return
        other = (st.get('after' if which == 'before' else 'before') or {})
        for idx, snap in sorted(snaps.items()):
            expect = other.get(idx)
            if expect is not None:
                try:
                    now = session.snapshot(int(idx))
                except Exception as e:              # noqa: BLE001
                    swallow('graph.commands.restore_beats.peek', e)
                    now = None
                if now is None or now.fingerprint() != expect.fingerprint():
                    swallow('graph.commands.restore_beats.stale',
                            RuntimeError(f'第 {int(idx) + 1} 拍已经变了，'
                                         f'跳过还原（导出重载 / 别的改动）'))
                    continue
            try:
                session.restore(snap)
            except Exception as e:              # noqa: BLE001 - 单拍还原失败不拦撤销
                swallow('graph.commands.restore_beats', e)
        try:
            session.refresh_edits_flag()        # 退回到"与磁盘一致"时别喊未导出
        except Exception as e:                  # noqa: BLE001
            swallow('graph.commands.restore_beats.dirty', e)
        cb = st.get('refresh')
        if callable(cb):
            try:
                cb()
            except Exception as e:              # noqa: BLE001 - 刷新界面失败不拦撤销
                swallow('graph.commands.restore_beats.refresh', e)

    def execute(self):
        node = self.graph_view.graph.nodes[self.node_id]
        if self.old_statements is None:
            self.old_statements = copy.deepcopy(node.statements)
        node.set_operations(self.new_ops)
        self._restore_beats('after')            # 重做：拍也跟着回到"改之后"
        self.graph_view.on_graph_content_changed(self.node_id)

    def undo(self):
        node = self.graph_view.graph.nodes[self.node_id]
        if self.old_statements is not None:
            node.statements = copy.deepcopy(self.old_statements)
        self._restore_beats('before')           # 撤销：拍也跟着退回"改之前"
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