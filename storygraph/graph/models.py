# models.py
import copy

class Node:
    def __init__(self, node_id, label_name, x=100, y=100):
        self.id = node_id
        self.label_name = label_name
        self.x = x
        self.y = y
        self.is_current = False
        self.operations = []
        self.file_path = None
        self.line_number = None

    def __deepcopy__(self, memo):
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        result.id = self.id
        result.label_name = self.label_name
        result.x = self.x
        result.y = self.y
        result.is_current = self.is_current
        result.operations = copy.deepcopy(self.operations, memo)
        result.file_path = self.file_path
        result.line_number = self.line_number
        return result

class Edge:
    def __init__(self, edge_id, from_id, to_id, etype="jump", condition="",
                 option_text="", from_clause=""):
        self.id = edge_id
        self.from_id = from_id
        self.to_id = to_id
        self.type = etype
        self.condition = condition
        self.option_text = option_text
        #: `call X from Y` 里的 `Y`（空 = 没写 from）。
        #:
        #: 为什么要存它（用户 2026-10-05）：
        #: > 先修复子句会丢的问题。
        #: `from` 是 Ren'Py 的"存档点标识"：同一个 label 被多个地方 call 时，
        #: 靠它区分存档属于哪一处调用。丢了它，导出的脚本语义等价但**存档
        #: 兼容性变了**（老存档会因为找不到 `_call_xxx` 而回滚到上一个 label）。
        self.from_clause = from_clause

    def __deepcopy__(self, memo):
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        result.id = self.id
        result.from_id = self.from_id
        result.to_id = self.to_id
        result.type = self.type
        result.condition = self.condition
        result.option_text = self.option_text
        result.from_clause = getattr(self, 'from_clause', '')
        return result

class Graph:
    def __init__(self):
        self.nodes = {}
        self.edges = {}
        self.next_node_id = 1
        self.next_edge_id = 1
        #: 这张图是不是"**控制流图**"（只由 call/jump/menu/条件 组成，
        #: 没有演出语句）？由**导入时的内容判据**设定，见
        #: EditorSession.import_chapters。
        #:
        #: 为什么必须有这个字段（用户 2026-10-05）：
        #: > 要是有人非要以 A_control 这种标签起名怎么办？……不得是变成保留字，
        #: > 因为你不能阻拦用户起什么名。
        #: 上一版靠"节点名是否以 _control 结尾"猜，用户一旦把章节命名成
        #: A_control 就会被误判。现在改成**内容判据 + 显式标志**，
        #: 名字完全不参与判断。
        self.control_flow = False

    def copy(self):
        g = Graph()
        g.next_node_id = self.next_node_id
        g.next_edge_id = self.next_edge_id
        g.control_flow = bool(getattr(self, 'control_flow', False))
        for nid, node in self.nodes.items():
            g.nodes[nid] = copy.deepcopy(node)
        for eid, edge in self.edges.items():
            g.edges[eid] = copy.deepcopy(edge)
        return g

    def add_node(self, label_name, x, y, operations=None, file_path=None, line_number=None):
        nid = self.next_node_id
        self.next_node_id += 1
        node = Node(nid, label_name, x, y)
        if operations:
            node.operations = operations
        if file_path:
            node.file_path = file_path
        if line_number:
            node.line_number = line_number
        self.nodes[nid] = node
        return nid

    def add_edge(self, from_id, to_id, etype="jump", condition="",
                 option_text="", from_clause=""):
        eid = self.next_edge_id
        self.next_edge_id += 1
        edge = Edge(eid, from_id, to_id, etype, condition, option_text,
                    from_clause)
        self.edges[eid] = edge
        return eid

    def remove_node(self, nid):
        if nid in self.nodes:
            del self.nodes[nid]
        to_del = [eid for eid, e in self.edges.items() if e.from_id == nid or e.to_id == nid]
        for eid in to_del:
            del self.edges[eid]

    def remove_edge(self, eid):
        if eid in self.edges:
            del self.edges[eid]

    def clear(self):
        self.nodes.clear()
        self.edges.clear()
        self.next_node_id = 1
        self.next_edge_id = 1