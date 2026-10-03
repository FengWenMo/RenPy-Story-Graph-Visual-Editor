#!/usr/bin/env python
"""逐步对比 HEAD 与工作区版本的中间结果，定位分歧出现在哪一步。"""
from __future__ import annotations

import importlib.util
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import storygraph.graph.layout as NEW          # noqa: E402

old_src = subprocess.run(
    ['git', 'show', 'HEAD:storygraph/graph/layout.py'],
    cwd=ROOT, capture_output=True, text=True, encoding='utf-8').stdout
tmp = ROOT / 'tools' / '_layout_head.py'
tmp.write_text(old_src, encoding='utf-8')
spec = importlib.util.spec_from_file_location('layH', tmp)
OLD = importlib.util.module_from_spec(spec)
sys.modules['layH'] = OLD
spec.loader.exec_module(OLD)


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


def build(n, e, seed):
    rng = random.Random(seed)
    g = G()
    ids = [str(i) for i in range(n)]
    for i in ids:
        g.nodes[i] = N()
    for k in range(e):
        i, j = sorted(rng.sample(range(n), 2))
        g.edges[str(k)] = E(ids[i], ids[j])
    return g


# 两边都用"无穷预算"（旧版没有预算；新版开到无穷）
NEW._REDUCE_BUDGET = 10 ** 9

n, e, seed = 20, 24, 3
ga, gb = build(n, e, seed), build(n, e, seed)
OLD.layout_graph(ga)
NEW.layout_graph(gb)
ca = {k: (v.x, v.y) for k, v in ga.nodes.items()}
cb = {k: (v.x, v.y) for k, v in gb.nodes.items()}
bad = [k for k in ca if ca[k] != cb[k]]
print('不一致节点数:', len(bad))
for k in bad[:6]:
    print(f'  {k}: 旧={ca[k]}  新={cb[k]}')

# 看层号
print()
print('--- 层号对比 ---')
import io
import contextlib

# 借新版内部函数重算一遍中间结果
adj = {nid: [] for nid in gb.nodes}
edges = []
for eid, edge in gb.edges.items():
    u, v = edge.from_id, edge.to_id
    adj[u].append(v)
    edges.append((u, v, eid))
ra = OLD._break_cycles({k: list(v) for k, v in adj.items()}) \
    if hasattr(OLD, '_break_cycles') else None
print('（旧版的破环是闭包，取不到；这里只比坐标）')
tmp.unlink(missing_ok=True)
