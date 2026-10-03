#!/usr/bin/env python
"""
给 `_reduce_crossings` 加**工作量预算**，并实测"正常需要多少"。

为什么需要预算（2026-10-05，P3-12 顺带发现）
--------------------------------------------
`layout_graph` 在某些图上会**几分钟不返回**（HEAD 版本也一样，是既有问题，
不是那次重构引入的）：20 节点 / 60 边这种规模就能触发。

算法本身是"只接受能减少交叉数的相邻交换"，所以它**必然**终止
（交叉数严格下降且有下界 0）—— 但**终止得很慢**：
每接受一次交换，`while local_improved` 就从该层最左边**重扫一遍**，
而这层几乎每次都能再找到一个可接受交换。代价大约是
`接受次数 × 层内对数 × 每次 O(E log E)`。

预算的作用：**正常规模的图跑不到预算**（实测见下表），
只有病态图会被截断 —— 那时布局**仍然是合法的**
（只是交叉数没降到最优），远好过界面卡死几分钟。

这条判据的边界要说清：预算**不改变**任何"能正常跑完"的图的输出，
因为它在那些图上根本不会被触发。
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

COUNT = {'n': 0}
_real = L._count_crossings


def counted(order, edge_segments, max_layer):
    COUNT['n'] += 1
    if COUNT['n'] > 400_000:                 # 安全阀：别把这个脚本自己挂死
        raise RuntimeError('预算爆了，判定为病态图')
    return _real(order, edge_segments, max_layer)


L._count_crossings = counted


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
        if dag:
            i, j = sorted(rng.sample(range(n), 2))
        else:
            i, j = rng.randrange(n), rng.randrange(n)
        g.edges[str(k)] = E(ids[i], ids[j])
    return g


RESULT = []


def one(label, n, e, seed, dag=True, timeout=10):
    g = build(n, e, seed, dag)
    COUNT['n'] = 0
    box = {}

    def run():
        t0 = time.time()
        try:
            L.layout_graph(g)
            box['ok'] = time.time() - t0
        except Exception as exc:                  # noqa: BLE001
            box['err'] = f'{type(exc).__name__}'
        box['n'] = COUNT['n']

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        RESULT.append((label, f'{n}/{e}', f'挂住(>{timeout}s)',
                       COUNT['n']))
    else:
        RESULT.append((label, f'{n}/{e}',
                       f"{box.get('ok', box.get('err')):.2f}s"
                       if isinstance(box.get('ok'), float) else box.get('err'),
                       box.get('n')))


cases = [
    ('树状', 12, 10, True), ('中等分支', 20, 24, True),
    ('较密 DAG', 20, 40, True), ('很密 DAG', 20, 60, True),
    ('随机有环', 20, 40, False), ('随机有环密', 20, 60, False),
    ('真实项目规模', 51, 70, True), ('真实项目较密', 51, 140, True),
    ('大图 100/200', 100, 200, True), ('大图 200/400', 200, 400, True),
] + [(f'随机 {n}/{e} seed{s}', n, e, False)
     for s, (n, e) in enumerate([(15, 30), (25, 50), (30, 80), (40, 120)])]

for label, n, e, dag in cases:
    one(label, n, e, 3, dag)

print('%-20s %10s %16s %14s' % ('形状', '节点/边', '用时', '降交叉次数'))
for label, ne, t, n in RESULT:
    print('%-20s %10s %16s %14s' % (label, ne, t, n))
