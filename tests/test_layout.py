"""
tests/test_layout.py
====================
自动布局算法测试。

布局是"不崩 + 全覆盖 + 分层合理"即可，不校验具体像素值。
重点回归：
  · 空图 early return
  · 环形图必须终止
  · 自环边不得让算法崩溃
  · 所有节点最终都要有坐标
  · DAG 上，边的源层严格小于目标层
"""

from storygraph.graph.models import Graph
from storygraph.graph.layout import layout_graph


def _layers(graph, start_id=None):
    layout_graph(graph, start_id=start_id)
    return {nid: (n.x, n.y) for nid, n in graph.nodes.items()}


class TestLayoutBasics:
    def test_empty_graph_is_noop(self):
        g = Graph()
        layout_graph(g)
        assert g.nodes == {}

    def test_single_node(self):
        g = Graph()
        nid = g.add_node('a', 500, 500)
        layout_graph(g)
        assert (g.nodes[nid].x, g.nodes[nid].y) == (0.0, 0.0)

    def test_chain_orders_left_to_right(self):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(a, b)
        g.add_edge(b, c)
        coords = _layers(g)
        assert coords[a][0] < coords[b][0] < coords[c][0]

    def test_all_nodes_get_coordinates(self):
        g = Graph()
        ids = [g.add_node(f'n{i}', 0, 0) for i in range(6)]
        for i in range(0, 5, 2):
            g.add_edge(ids[i], ids[i + 1])
        coords = _layers(g)
        assert set(coords.keys()) == set(ids)
        assert all(isinstance(v[0], (int, float)) for v in coords.values())

    def test_nodes_in_same_layer_do_not_overlap(self):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        d = g.add_node('d', 0, 0)
        g.add_edge(a, c)
        g.add_edge(b, c)
        g.add_edge(a, d)
        coords = _layers(g)
        ys = sorted(coords[n][1] for n in (a, b))
        assert ys[0] != ys[1]

    def test_layer_gap_respected(self):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b)
        coords = _layers(g)
        assert coords[b][0] - coords[a][0] >= 1


class TestLayoutRobustness:
    def test_cycle_terminates(self):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(a, b)
        g.add_edge(b, c)
        g.add_edge(c, a)
        coords = _layers(g)
        assert set(coords.keys()) == {a, b, c}

    def test_self_loop_terminates(self):
        g = Graph()
        a = g.add_node('a', 0, 0)
        g.add_edge(a, a)
        coords = _layers(g)
        assert a in coords

    def test_two_node_cycle(self):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b)
        g.add_edge(b, a)
        assert len(_layers(g)) == 2

    def test_disconnected_components(self):
        g = Graph()
        ids = [g.add_node(f'n{i}', 0, 0) for i in range(5)]
        g.add_edge(ids[0], ids[1])
        coords = _layers(g)
        assert set(coords.keys()) == set(ids)

    def test_multiple_edges_same_pair(self):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b)
        g.add_edge(a, b, etype='call')
        assert len(_layers(g)) == 2

    def test_start_id_influences_placement(self):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b)
        coords = _layers(g, start_id=a)
        assert coords[a][0] < coords[b][0]

    def test_large_graph_completes(self):
        """50 节点链式图，确保没有指数级退化。"""
        g = Graph()
        ids = [g.add_node(f'n{i}', 0, 0) for i in range(50)]
        for i in range(49):
            g.add_edge(ids[i], ids[i + 1])
        coords = _layers(g)
        assert len(coords) == 50

    def test_fan_out_and_fan_in(self):
        g = Graph()
        root = g.add_node('root', 0, 0)
        leaves = [g.add_node(f'l{i}', 0, 0) for i in range(5)]
        sink = g.add_node('sink', 0, 0)
        for l in leaves:
            g.add_edge(root, l)
            g.add_edge(l, sink)
        coords = _layers(g)
        assert coords[root][0] < coords[sink][0]
