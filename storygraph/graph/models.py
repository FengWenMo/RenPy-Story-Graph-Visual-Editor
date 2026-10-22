# models.py
import copy

from storygraph.core.statement import (is_code_statement, operation_source,
                                       python_operation)


class Node:
    """
    剧情图上的一个模块（= 一个 label）。

    **结构债迁移（用户 2026-10-05："开始迁移，处理结构债"）**
    --------------------------------------------------------
    以前这里只有 `self.operations: list[str]` —— 裸代码字符串，下游（导出 /
    分析 / 分类 / 抽变量）各自用正则或 AST 反解。现在真源是**结构化语句**：

        node.statements : list[Statement]     ← 写路径（新）
        node.operations : list[str]            ← 兼容视图（派生，老调用方照旧）

    `operations` 是**属性**（不是字段）：读出来 = 每条语句的源码文本，
    写进去 = 重建语句表。所以二十来处老调用点一行都不用改，
    而"字符串 → 语句 → 字符串"必须逐字节回到原样（有回归测试钉着）。

    ⚠ **不要写 `node.operations.append(x)`** —— 属性返回的是新列表，
    那样 append 会**静默丢掉**（实测这条最容易踩）。要加就调
    `node.add_operation(x)`；`tests/test_graph_statements.py` 有守卫。
    """

    def __init__(self, node_id, label_name, x=100, y=100):
        self.id = node_id
        self.label_name = label_name
        self.x = x
        self.y = y
        self.is_current = False
        #: 结构化语句表（真源）。图上的"操作"目前都是 `kind='python'`。
        self.statements: list = []
        self.file_path = None
        self.line_number = None

    # ---- 结构化真源 ↔ 兼容视图 ----

    @property
    def operations(self) -> list:
        """兼容视图：每条语句的源码文本（**新列表**，改它不会改到节点）。"""
        return [operation_source(s) for s in self.statements
                if is_code_statement(s)]

    @operations.setter
    def operations(self, values) -> None:
        self.set_operations(values)

    def set_operations(self, values) -> None:
        """整批替换（`operations` 的赋值语法走这里）。"""
        self.statements = [python_operation(v) for v in (values or [])]

    def add_operation(self, code, index: int = None) -> None:
        """追加一条**裸代码**（老调用方 / 对话框输入走这里）。"""
        self.add_statement(python_operation(code), index=index)

    def add_statement(self, stmt, index: int = None) -> None:
        """
        追加一条**结构化语句**（解析器走这里 —— 它是唯一能带上
        `source`（文件/行号）与 `condition` 的入口，别退化成只塞字符串）。
        """
        if index is None:
            self.statements.append(stmt)
        else:
            self.statements.insert(int(index), stmt)

    def code_texts(self) -> list:
        """只要源码文本（与 `operations` 等价，给"不想拿新列表"的热路径用）。"""
        return self.operations

    def clear_operations(self) -> None:
        self.statements = []

    def __deepcopy__(self, memo):
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        result.id = self.id
        result.label_name = self.label_name
        result.x = self.x
        result.y = self.y
        result.is_current = self.is_current
        result.statements = copy.deepcopy(self.statements, memo)
        result.file_path = self.file_path
        result.line_number = self.line_number
        return result

    def __repr__(self):
        return (f'Node({self.id}, {self.label_name!r}, '
                f'{len(self.statements)} statements)')

class Edge:
    def __init__(self, edge_id, from_id, to_id, etype="jump", condition="",
                 option_text="", from_clause="", origin="source"):
        self.id = edge_id
        #: 这条边**从哪来**（2026-10-22 第二阶段）：
        #:
        #:   · `source` —— 源文件里的 `jump` / `call`（解析出来的）；
        #:   · `manual` —— 用户在图上画的草图。
        #:
        #: 为什么要分：第二阶段要让"图边从 flow 拍派生"（删双真源）。
        #: 不分来源的话，重建 `source` 边时会把用户画的草图一起删掉 ——
        #: 那是"工具吃掉用户的动作"，比双真源更糟。
        self.origin = str(origin or 'source')
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
        result.origin = getattr(self, 'origin', 'source')
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
            node.set_operations(operations)
        if file_path:
            node.file_path = file_path
        if line_number:
            node.line_number = line_number
        self.nodes[nid] = node
        return nid

    def add_edge(self, from_id, to_id, etype="jump", condition="",
                 option_text="", from_clause="", origin="source"):
        eid = self.next_edge_id
        self.next_edge_id += 1
        edge = Edge(eid, from_id, to_id, etype, condition, option_text,
                    from_clause, origin=origin)
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