"""
`tools/_trace_layout_child.py` —— 由 `_trace_layout.py` 起子进程调用。

把 `layout_graph` 的每个阶段做一次"与迭代顺序无关"的摘要并打印，
用来定位不确定性出在哪一步。**必须单独成文件**：
靠 `-c` 传源码时转义会把 `{{ }}` 弄坏（踩过两次）。
读环境变量 `SG_ROOT` 拿项目根。
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys

ROOT = os.environ.get('SG_ROOT') or '.'
sys.path.insert(0, ROOT)

import storygraph.graph.layout as L          # noqa: E402


class N:
    __slots__ = ('x', 'y')

    def __init__(self):
        self.x = self.y = None


class E:
    __slots__ = ('from_id', 'to_id')

    def __init__(self, u, v):
        self.from_id, self.to_id = u, v


class G:
    def __init__(self):
        self.nodes = {}
        self.edges = {}


def dig(label, obj):
    def norm(v):
        if isinstance(v, dict):
            return {k: norm(x) for k, x in v.items()}
        if isinstance(v, (set, frozenset)):
            return sorted(str(norm(x)) for x in v)
        if isinstance(v, (list, tuple)):
            return [norm(x) for x in v]
        return v
    s = json.dumps(norm(obj), sort_keys=True, default=str)
    print(label, hashlib.md5(s.encode()).hexdigest()[:8])


def main() -> int:
    rng = random.Random(3)
    g = G()
    ids = [str(i) for i in range(20)]
    for i in ids:
        g.nodes[i] = N()
    for k in range(24):
        i, j = sorted(rng.sample(range(20), 2))
        g.edges[str(k)] = E(ids[i], ids[j])

    adj = {nid: [] for nid in g.nodes}
    edges = []
    for eid, edge in g.edges.items():
        u, v = edge.from_id, edge.to_id
        adj[u].append(v)
        edges.append((u, v, eid))
    dig('edges', edges)

    rev = L._break_cycles(adj)
    dig('reversed_edges', sorted(str(x) for x in rev))

    dag_adj = {nid: set() for nid in g.nodes}
    for u, v, _ in edges:
        if (u, v) in rev:
            dag_adj[v].add(u)
        else:
            dag_adj[u].add(v)

    L._ensure_dag(dag_adj, list(g.nodes))
    dig('dag_adj', {k: sorted(v) for k, v in dag_adj.items()})

    layers, preds, sources, max_layer = L._assign_layers(
        dag_adj, list(g.nodes), None)
    dig('layers', layers)

    for u, v, _ in edges:
        if (u, v) in rev:
            preds[u].add(v)
        else:
            preds[v].add(u)
    dig('preds', {k: sorted(v) for k, v in preds.items()})

    L._pull_up_layers(layers, preds, dag_adj, list(g.nodes))
    dig('layers_pulled', layers)

    _v, lne, segs, max_layer = L._insert_virtual_nodes(edges, layers)
    dig('edge_segments', segs)

    order = L._build_initial_order(list(g.nodes), dag_adj, layers, sources,
                                   max_layer, lne)
    dig('order_initial', {k: list(v) for k, v in order.items()})

    from collections import defaultdict
    ext = defaultdict(set)
    for a, b, _ in segs:
        ext[a].add(b)
        ext[b].add(a)

    L._REDUCE_BUDGET = 10 ** 9
    for step in range(1, 11):
        order = L._barycenter_once(order, max_layer, ext)
        dig(f'bary_{step}', {k: list(v) for k, v in order.items()})
    order = L._reduce_crossings(order, segs, max_layer)
    dig('order_reduced', {k: list(v) for k, v in order.items()})
    return 0


if __name__ == '__main__':
    sys.exit(main())
