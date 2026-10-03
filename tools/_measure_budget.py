#!/usr/bin/env python
"""
分别量 `_reduce_crossings` 内部与 `_barycenter_once` 的评估次数，
并找出"最小病态图"（会挂住的那个）到底要多少次。

只有把"正常图需要多少"和"病态图被截在多少"两个数摆在一起，
才能定出一个**既不改变正常布局、又挡得住卡死**的预算。
"""
from __future__ import annotations

import random
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import storygraph.graph.layout as L          # noqa: E402

COUNT = {'total': 0, 'reduce': 0, 'bary': 0}
_real = L._count_crossings
_real_reduce = L._reduce_crossings


def counted(order, edge_segments, max_layer):
    COUNT['total'] += 1
    return _real(order, edge_segments, max_layer)


def counted_reduce(order_ext, edge_segments, max_layer):
    before = COUNT['total']
    out = _real_reduce(order_ext, edge_segments, max_layer)
    COUNT['reduce'] += COUNT['total'] - before
    return out


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


def build(n, e, seed, dag=True):
    rng = random.Random(seed)
    g = G()
    ids = [str(i) for i in range(n)]
    for i in ids:
        g.nodes[i] = N()
    for k in range(e):
        if dag and n >= 2:
            i, j = sorted(rng.sample(range(n), 2))
        else:
            i, j = rng.randrange(n), rng.randrange(n)
        g.edges[str(k)] = E(ids[i], ids[j])
    return g


def run_limited(label, n, e, seed, dag, limit, timeout=15):
    """限量跑：把预算设成 limit，看它跑完要多久、用了多少。"""
    L._count_crossings = counted
    L._reduce_crossings = counted_reduce
    L._REDUCE_BUDGET_BASE = limit
    L._REDUCE_BUDGET_FACTOR = 0
    COUNT.update(total=0, reduce=0, bary=0)
    g = build(n, e, seed, dag)
    box = {}

    def go():
        t0 = time.time()
        L.layout_graph(g)
        box['dt'] = time.time() - t0
        box['total'] = COUNT['total']
        box['reduce'] = COUNT['reduce']

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None
    return box


# 先找"会不会挂"：随机无环图里哪些在无穷预算下跑不完
print('== 无穷预算下，正常图需要多少次 ==')
for label, n, e, dag in (('真实规模 51/70', 51, 70, True),
                         ('真实较密 51/140', 51, 140, True),
                         ('大图 100/200', 100, 200, True)):
    for seed in (1, 2, 3):
        r = run_limited(label, n, e, seed, dag, 10 ** 9, timeout=30)
        if r is None:
            print('%-22s seed%d  **30 秒没跑完**' % (label, seed))
        else:
            print('%-22s seed%d  总 %-6d 其中降交叉 %-6d  %.2fs'
                  % (label, seed, r['total'], r['reduce'], r['dt']))

print()
print('== 小图：正常 vs 病态（有环图更容易病态）==')
for label, n, e, dag in (('无环 15/20', 15, 20, True),
                         ('无环 25/35', 25, 35, True),
                         ('有环 15/20', 15, 20, False),
                         ('有环 20/30', 20, 30, False),
                         ('有环 25/50', 25, 50, False)):
    for seed in (1, 2, 3):
        r = run_limited(label, n, e, seed, dag, 10 ** 9, timeout=15)
        if r is None:
            print('%-16s seed%d  **15 秒没跑完（病态）**' % (label, seed))
        else:
            print('%-16s seed%d  总 %-6d 其中降交叉 %-6d  %.2fs'
                  % (label, seed, r['total'], r['reduce'], r['dt']))
