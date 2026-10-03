#!/usr/bin/env python
"""
判断 `layout_graph` 在某类图上挂住**是不是重构引入的**。

做法：HEAD 版本与工作区版本各跑一遍，**加超时**（子进程级不行，
所以用信号 / 线程）。这里用 `multiprocessing` 起独立进程，
超时就杀掉并报告 —— 两边分别报告，就能区分"新引入"与"本来就有"。
"""
from __future__ import annotations

import multiprocessing as mp
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_case(mod_path: str, n: int, e: int, seed: int) -> float:
    import importlib.util
    spec = importlib.util.spec_from_file_location('lay', mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['lay'] = mod
    spec.loader.exec_module(mod)

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

    rng = random.Random(seed)
    g = G()
    ids = [str(i) for i in range(n)]
    for i in ids:
        g.nodes[i] = N()
    for k in range(e):
        g.edges[str(k)] = E(rng.choice(ids), rng.choice(ids))
    mod.layout_graph(g)


def guarded(mod_path, n, e, seed, q):
    try:
        run_case(mod_path, n, e, seed)
        q.put('ok')
    except Exception as exc:                     # noqa: BLE001
        q.put(f'err:{type(exc).__name__}:{exc}')


def main() -> int:
    old_src = subprocess.run(
        ['git', 'show', 'HEAD:storygraph/graph/layout.py'],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8').stdout
    tmp = ROOT / 'tools' / '_layout_head.py'
    tmp.write_text(old_src, encoding='utf-8')
    new_path = str(ROOT / 'storygraph' / 'graph' / 'layout.py')

    cases = [('无边', 8, 0), ('无边20', 20, 0), ('全自环', 8, 8),
             ('稀疏', 8, 6), ('稠密', 20, 60)]
    try:
        for label, n, e in cases:
            row = []
            for which, path in (('旧', str(tmp)), ('新', new_path)):
                q = mp.Queue()
                p = mp.Process(target=guarded, args=(path, n, e, 7, q))
                p.start()
                p.join(6)
                if p.is_alive():
                    p.terminate()
                    p.join()
                    row.append(f'{which}=**挂住**')
                else:
                    row.append(f'{which}={q.get() if not q.empty() else "?"}')
            print('%-8s n=%-3d e=%-3d  %s' % (label, n, e, '  '.join(row)))
    finally:
        tmp.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
