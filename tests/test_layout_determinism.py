"""
`layout_graph` 的**确定性**守卫（P3-12，2026-10-05）
==================================================

布局必须"同一张图 → 同样的坐标"。这条听起来像废话，
但本项目**真的坏了很久**，而且坏得很隐蔽：

  实测（`tools/_hashseed_demo.py`）：同一张 20 节点 / 24 边的图，
  在 6 个不同的 `PYTHONHASHSEED` 下给出 **4 种不同的坐标和**。

用户侧表现是"**剧情图每次打开都长得不一样**" —— 比"排得不够好"
更让人不安（会怀疑自己是不是动过什么、是不是没保存上）。

根因（`tools/_trace_layout.py` 逐步摘要定位）：前 8 个阶段在所有
hash seed 下**完全一致**，从 `bary_1` 起分叉。`_barycenter_once` 里的

    neighbors = [nb for nb in ext_neighbors[nid] if nb in pos_ref]
    ...
    sorted(zip(weighted_vals, neighbors, weights))

`ext_neighbors` 是 `defaultdict(set)` —— set 的迭代顺序取决于字符串哈希。
而 `sorted` 比的是**元组**：第一个元素 `weighted_vals` 相等时
（多个邻居位置相同、或权重都是 0.5，很常见）就退化成比较第二个元素
`neighbors` 的顺序 —— 那正是随机顺序。

修法：邻居按 `str(nid)` 排序。同一个 bug 在 `_ensure_dag` 里也有一份
（反环时取"第一条环内边"），一并按 `str` 排序。

为什么不靠"跑一遍比较两次结果"来守
----------------------------------
同进程内两次调用**会**一致（哈希在进程内固定），所以
"跑两次比一比"抓不到它 —— 必须**换进程**。下面第一条用例就是
真起子进程、换 `PYTHONHASHSEED`。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: 子进程里跑的脚本（**必须单独成文件**：靠 `-c` 传源码时转义会把
#: `{{ }}` 弄坏 —— 这个坑在写 `tools/_trace_layout.py` 时踩过两次）。
CHILD = ROOT / 'tools' / '_hashseed_child.py'


class Node:
    __slots__ = ('x', 'y')

    def __init__(self):
        self.x = self.y = None


class Edge:
    __slots__ = ('from_id', 'to_id')

    def __init__(self, u, v):
        self.from_id, self.to_id = u, v


class Graph:
    def __init__(self):
        self.nodes = {}
        self.edges = {}


def _build(n, e, seed, dag=True):
    import random
    rng = random.Random(seed)
    g = Graph()
    ids = [str(i) for i in range(n)]
    for i in ids:
        g.nodes[i] = Node()
    for k in range(e):
        if dag and n >= 2:
            i, j = sorted(rng.sample(range(n), 2))
        else:
            i, j = rng.randrange(n), rng.randrange(n)
        g.edges[str(k)] = Edge(ids[i], ids[j])
    return g


def _coords(g):
    return {nid: (nd.x, nd.y) for nid, nd in g.nodes.items()}


class TestDeterministicAcrossProcesses:

    def test_same_graph_same_layout_in_a_fresh_process(self):
        """
        **换进程**（换 `PYTHONHASHSEED`）跑同一张图，坐标必须一样。

        这条是唯一能抓住那个 bug 的判据：同进程内跑两次永远一致。
        """
        pytest.importorskip  # noqa: B018 - 保留 importorskip 的语义位置
        assert CHILD.exists(), f'缺少子脚本：{CHILD}'
        results = []
        for seed in ('0', '1', '2', '3'):
            env = dict(os.environ, PYTHONHASHSEED=seed,
                       SG_ROOT=str(ROOT))
            r = subprocess.run([sys.executable, str(CHILD)],
                               capture_output=True, text=True,
                               cwd=str(ROOT), env=env, encoding='utf-8')
            line = (r.stdout or '').strip().splitlines()
            assert line, f'seed {seed} 子进程没输出：{(r.stderr or "")[-300:]}'
            results.append(line[-1].strip())
        assert len(set(results)) == 1, (
            f'同一张图在不同进程里给出了 {len(set(results))} 种布局：\n'
            + '\n'.join(f'  PYTHONHASHSEED={s}  →  {v}'
                        for s, v in zip(('0', '1', '2', '3'), results))
            + '\n\n根因通常是"迭代了 set/dict 来决定顺序" —— '
              '见本文件开头的说明与 `tools/_trace_layout.py`。')

    def test_same_graph_same_layout_twice_in_process(self):
        """同进程两次一致（这条**抓不到**那个 bug，但便宜且能防别的）。"""
        from storygraph.graph.layout import layout_graph
        for shape in ((12, 10, True), (20, 24, True), (25, 35, True),
                      (20, 30, False)):
            n, e, dag = shape
            g1, g2 = _build(n, e, 3, dag), _build(n, e, 3, dag)
            layout_graph(g1)
            layout_graph(g2)
            assert _coords(g1) == _coords(g2), f'{shape} 两次结果不同'

    def test_layout_is_a_permutation_of_the_nodes(self):
        """坐标必须落满每个节点（别因为排序把节点漏掉）。"""
        from storygraph.graph.layout import layout_graph
        g = _build(25, 35, 5)
        layout_graph(g)
        co = _coords(g)
        assert set(co) == set(g.nodes), '有节点没拿到坐标'
        assert all(x is not None and y is not None for x, y in co.values())


class TestTerminatesOnPathologicalGraphs:
    """
    两类病态图**必须能返回**（原实现会挂几分钟，见 `_ensure_dag` /
    `_reduce_crossings` 的说明）。用子进程 + 超时判，避免把测试套件挂死。
    """

    @pytest.mark.parametrize('n,e,dag', [
        (25, 50, False),      # 有环（`_ensure_dag` 那条路）
        (30, 80, False),
        (40, 120, True),      # 稠密（`_reduce_crossings` 那条路）
        (100, 200, True),
    ])
    def test_terminates_within_budget(self, n, e, dag):
        script = (
            'import sys\n'
            f'sys.path.insert(0, {str(ROOT)!r})\n'
            'from tests.test_layout_determinism import _build\n'
            'from storygraph.graph.layout import layout_graph\n'
            f'g = _build({n}, {e}, 3, {dag!r})\n'
            'layout_graph(g)\n'
            'print("ok")\n'
        )
        r = subprocess.run([sys.executable, '-c', script],
                           capture_output=True, text=True, cwd=str(ROOT),
                           timeout=60, encoding='utf-8')
        assert 'ok' in (r.stdout or ''), (
            f'n={n} e={e} dag={dag} 没跑完：{(r.stderr or "")[-300:]}')
