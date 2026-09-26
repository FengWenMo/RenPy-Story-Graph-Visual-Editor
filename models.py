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
    def __init__(self, edge_id, from_id, to_id, etype="jump", condition="", option_text=""):
        self.id = edge_id
        self.from_id = from_id
        self.to_id = to_id
        self.type = etype
        self.condition = condition
        self.option_text = option_text

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
        return result

class Graph:
    def __init__(self):
        self.nodes = {}
        self.edges = {}
        self.next_node_id = 1
        self.next_edge_id = 1

    def copy(self):
        g = Graph()
        g.next_node_id = self.next_node_id
        g.next_edge_id = self.next_edge_id
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

    def add_edge(self, from_id, to_id, etype="jump", condition="", option_text=""):
        eid = self.next_edge_id
        self.next_edge_id += 1
        edge = Edge(eid, from_id, to_id, etype, condition, option_text)
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