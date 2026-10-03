#!/usr/bin/env python
"""
证明 `layout_graph` 在**不同进程**里给出不同布局（既有 bug）。

用法：`python tools/_hashseed_demo.py`
它用不同的 `PYTHONHASHSEED` 起子进程跑同一张图，比较坐标和。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHILD = r'''
import random, sys
sys.path.insert(0, {root!r})
from storygraph.graph.layout import layout_graph


class N:
    __slots__ = ("x", "y")

    def __init__(self):
        self.x = self.y = None


class E:
    __slots__ = ("from_id", "to_id")

    def __init__(self, u, v):
        self.from_id, self.to_id = u, v


class G:
    def __init__(self):
        self.nodes = {}
        self.edges = {}


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
'''


def main() -> int:
    code = CHILD.replace('{root!r}', repr(str(ROOT)))
    outs = []
    for seed in ('0', '1', '2', '3', '4', '5'):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        r = subprocess.run([sys.executable, '-c', code], capture_output=True,
                           text=True, cwd=str(ROOT), env=env,
                           encoding='utf-8')
        outs.append((seed, (r.stdout or r.stderr).strip().splitlines()[-1]
                     if (r.stdout or r.stderr).strip() else '?'))
    print('不同 hash seed 下的坐标和：')
    for seed, val in outs:
        print(f'  PYTHONHASHSEED={seed}  →  {val}')
    uniq = {v for _s, v in outs}
    print()
    print('**一致**（布局确定）' if len(uniq) == 1
          else f'**不一致（{len(uniq)} 种结果）→ 布局不确定**')
    return 0 if len(uniq) == 1 else 1


if __name__ == '__main__':
    sys.exit(main())
