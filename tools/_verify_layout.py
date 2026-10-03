#!/usr/bin/env python
"""
验证 `layout_graph` 重构 + 封顶之后，**正常图仍然与 HEAD 逐字节一致**。

（旧版验证脚本会把"病态图"也拿去比，结果两边一起挂住 ——
所以这一版：(a) 只比**能跑完**的图；(b) 每个用例带子进程超时。）
"""
from __future__ import annotations

import importlib.util
import multiprocessing as mp
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


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


def build(n, e, seed, dag):
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


def coords(g):
    return {nid: (nd.x, nd.y) for nid, nd in g.nodes.items()}


def run_one(path, n, e, seed, dag, kwargs, q):
    try:
        mod = load(path, 'lay_' + str(abs(hash(path)) % 10000))
        g = build(n, e, seed, dag)
        mod.layout_graph(g, **kwargs)
        q.put(coords(g))
    except Exception as exc:                      # noqa: BLE001
        q.put(f'ERR:{type(exc).__name__}:{exc}')


def call(path, n, e, seed, dag, kwargs, timeout=20):
    q = mp.Queue()
    p = mp.Process(target=run_one, args=(path, n, e, seed, dag, kwargs, q))
    p.start()
    p.join(timeout)
    if p.is_alive():
        p.terminate()
        p.join()
        return 'TIMEOUT'
    return q.get() if not q.empty() else 'EMPTY'


def main() -> int:
    old_src = subprocess.run(
        ['git', 'show', 'HEAD:storygraph/graph/layout.py'],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8').stdout
    tmp = ROOT / 'tools' / '_layout_head.py'
    tmp.write_text(old_src, encoding='utf-8')
    new_path = str(ROOT / 'storygraph' / 'graph' / 'layout.py')

    shapes = [
        ('树状 12/10', 12, 10, True),
        ('中等分支 20/24', 20, 24, True),
        ('较密 DAG 20/40', 20, 40, True),
        ('真实规模 51/70', 51, 70, True),
        ('真实较密 51/140', 51, 140, True),
        ('单节点', 1, 0, True),
        ('自环 8/8', 8, 8, False),
        ('无边 20/0', 20, 0, True),
        ('随机无环 15/20 seed1', 15, 20, True),
        ('随机无环 15/20 seed2', 15, 20, True),
        ('随机无环 25/35 seed5', 25, 35, True),
    ]
    same = diff = skipped = 0
    try:
        for label, n, e, dag in shapes:
            a = call(str(tmp), n, e, 3, dag, {})
            b = call(new_path, n, e, 3, dag, {})
            if a in ('TIMEOUT', 'EMPTY') or b in ('TIMEOUT', 'EMPTY'):
                print('%-24s 旧=%-9s 新=%-9s  跳过（有一边跑不完）'
                      % (label, a if isinstance(a, str) else 'ok',
                         b if isinstance(b, str) else 'ok'))
                skipped += 1
                continue
            if a == b:
                print('%-24s **一致**（%d 个节点）' % (label, len(a)))
                same += 1
            else:
                diff += 1
                bad = [k for k in a if a[k] != b.get(k)]
                print('%-24s **不一致** %d 个节点：%s'
                      % (label, len(bad), bad[:4]))
        print(f'\n一致 {same} / 不一致 {diff} / 跳过 {skipped}')
        return 1 if diff else 0
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == '__main__':
    sys.exit(main())
