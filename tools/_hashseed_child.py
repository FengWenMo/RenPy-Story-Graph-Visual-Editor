"""
`tools/_hashseed_child.py` —— 由确定性守卫起子进程调用。

打印一行"坐标和"。父进程换 `PYTHONHASHSEED` 跑多次，
如果结果不一致，说明布局依赖 set/dict 的迭代顺序（**是 bug**）。

读 `SG_ROOT` 拿项目根（父进程用 env 传）。
"""
from __future__ import annotations

import os
import random
import sys

ROOT = os.environ.get('SG_ROOT') or '.'
sys.path.insert(0, ROOT)

from storygraph.graph.layout import layout_graph      # noqa: E402


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


def main() -> int:
    rng = random.Random(3)
    g = G()
    ids = [str(i) for i in range(20)]
    for i in ids:
        g.nodes[i] = N()
    for k in range(24):
        i, j = sorted(rng.sample(range(20), 2))
        g.edges[str(k)] = E(ids[i], ids[j])
    layout_graph(g)
    print(sum(v.x + v.y for v in g.nodes.values()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
