#!/usr/bin/env python
"""
量"正常图"到底需要多少次降交叉评估 —— 用来定一个**确定性**预算。

为什么不能用时间预算（教训）
----------------------------
第一版封顶用的是 `time.monotonic()` 截止时间。功能上它确实挡住了卡死，
但它让**布局变成不确定的**：同一张图，机器忙一点就多截断几次、
结果就换一个排法。布局是用户要盯着看的东西，反复重排不可接受。

所以改成**纯计数**预算（同样输入必然同样结果）。这就要求预算
**足够大到正常图永远碰不到** —— 本脚本量出那个数。
"""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import storygraph.graph.layout as L          # noqa: E402

COUNT = {'n': 0}
_real = L._count_crossings


def counted(order, edge_segments, max_layer):
    COUNT['n'] += 1
    return _real(order, edge_segments, max_layer)


L._count_crossings = counted
# 闸开到无穷，量"真正需要多少"
L._LAYOUT_TIME_BUDGET = 10 ** 9
L._REDUCE_BUDGET_FACTOR = 10 ** 9
L._REDUCE_BUDGET_BASE = 10 ** 9


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


rows = []
shapes = [
    ('树状', 12, 10, True), ('中等分支', 20, 24, True),
    ('较密 DAG', 20, 40, True), ('很密 DAG', 20, 60, True),
    ('真实规模', 51, 70, True), ('真实较密', 51, 140, True),
    ('大图 100/200', 100, 200, True),
] + [(f'随机无环 {n}/{e}', n, e, True)
     for n, e in ((15, 20), (25, 35), (30, 50), (40, 80))]
rows_out = []
for label, n, e, dag in shapes:
    for seed in (1, 2, 3, 5, 7):
        g = build(n, e, seed, dag)
        COUNT['n'] = 0
        t0 = time.time()
        L.layout_graph(g)
        rows_out.append((COUNT['n'], time.time() - t0, f'{label} seed{seed}',
                         n, e))

rows_out.sort(reverse=True)
print('%-28s %8s %10s %8s' % ('形状', '次数', '用时', '节点/边'))
for n, dt, label, nn, ee in rows_out[:18]:
    print('%-28s %8d %9.2fs %8s' % (label, n, dt, f'{nn}/{ee}'))
print()
print('次数最多:', rows_out[0][0])
print('前 10% 分位:', rows_out[len(rows_out) // 10][0])
