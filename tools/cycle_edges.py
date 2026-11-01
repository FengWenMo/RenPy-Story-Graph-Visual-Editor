# -*- coding: utf-8 -*-
"""导入环**逐条边**报告：谁和谁互引、拿什么互引、要拆几条才解开。

`tools/debt_report.py` 只回答"有几个环、几个模块"；要动手时必须知道
**每条边**的方向、是不是模块级、导入了哪些名字、这些名字被用了几次。
这个工具就是那把手术灯。

三个视图：

1. **度表** —— 环内每个模块的入/出度。`(1, 1)` 的是"过路节点"
   （拆它的那条边，它自己就出环了）；出度大的是"汇"（它进环只因为
   一条边），入度大的是"源"（大家都依赖它）。
2. **逐条边** —— 按"用了几次"升序。`0` 的几乎肯定是**死导入**或
   纯 re-export（`# noqa: F401`），最便宜；`39` / `52` 那种是核心依赖，
   拆它等于重构。
3. **反馈边** —— `Eades–Lin–Smyth` 求一个近似的**最小反馈边集**：
   这些边就是"环真正靠它们闭合"的那些。要把环拆成 0，必须一条条
   处理掉它们（其余的边都是 DAG 内部的，不用动）。

用法：

    python tools/cycle_edges.py            # 三个视图全打
    python tools/cycle_edges.py --md       # 输出 Markdown 表（写文档用）
    python tools/cycle_edges.py <前缀>      # 只看边里带某个前缀的
"""
from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import debt_report as DR  # noqa: E402


def _bound_names(node) -> list:
    """一条 import 语句**在本地绑定的名字**（认得 `as`）。"""
    out = []
    for a in node.names:
        if a.name == '*':
            continue
        if isinstance(node, ast.Import):
            out.append(a.asname or a.name.split('.')[-1])
        else:
            out.append(a.asname or a.name)
    return out


def _edge_detail(src: str, target: str) -> tuple:
    """`src` → `target` 这条边：层级、导入的名字、被用几次。"""
    tree = ast.parse(src)
    top_nodes = {n for n in tree.body
                 if isinstance(n, (ast.Import, ast.ImportFrom))}
    top_names, fn_names = [], []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                else ([node.module] if node.module else []))
        if not any('.'.join((m or '').split('.')[:3]) == target for m in mods):
            continue
        bucket = top_names if node in top_nodes else fn_names
        bucket.extend(_bound_names(node))
    names = set(top_names) | set(fn_names)
    uses = sum(1 for node in ast.walk(tree)
               if isinstance(node, ast.Name) and node.id in names)
    kind = 'fn' if fn_names and not top_names else (
        'both' if top_names and fn_names else 'top')
    return kind, sorted(names), uses


def _feedback_edges(nodes, edges) -> list:
    """`Eades–Lin–Smyth` 近似最小反馈边集（返回"往回指"的那些边）。

    算法：反复摘"汇点"（出度 0，放最右）、"源点"（入度 0，放最左）；
    剩下的图里摘 `出度-入度` 最大的那个放最左。最后按这个顺序，
    凡是从后往前指的边就是反馈边。
    """
    live = set(nodes)
    out = {n: {m for m in edges.get(n, ()) if m in live} for n in live}
    inc = {n: {m for m in live if n in edges.get(m, ())} for n in live}
    left, right = [], []
    while live:
        changed = True
        while changed:
            changed = False
            for n in sorted(live):
                if not (out[n] & live):
                    live.discard(n)
                    right.append(n)
                    changed = True
                    break
            else:
                for n in sorted(live):
                    if not (inc[n] & live):
                        live.discard(n)
                        left.append(n)
                        changed = True
                        break
        if not live:
            break
        pick = max(sorted(live), key=lambda n: len(out[n] & live)
                   - len(inc[n] & live))
        live.discard(pick)
        left.append(pick)
    order = {n: i for i, n in enumerate(left + right[::-1])}
    back = [(a, b) for a in order for b in edges.get(a, ())
            if b in order and order[a] > order[b]]
    return sorted(back)


def _short(name: str) -> str:
    return name.replace('storygraph.', '')


def main(argv) -> int:
    md = '--md' in argv
    only = next((a for a in argv[1:] if not a.startswith('--')), '')
    data = DR.collect()
    files = {DR._module_name(p): p for p in sorted(DR.PKG.rglob('*.py'))}
    known = set(files)
    srcs = {n: p.read_text(encoding='utf-8') for n, p in files.items()}
    edges_all = defaultdict(set)
    edges_top = defaultdict(set)
    for name, src in srcs.items():
        top, all_ = DR._imports_of(ast.parse(src))
        for bucket, names in ((edges_top, top), (edges_all, all_)):
            for imp in names:
                base = '.'.join(imp.split('.')[:3])
                if base in known and base != name:
                    bucket[name].add(base)
    sccs = sorted(DR._sccs(known, edges_all), key=len, reverse=True)
    if not sccs:
        print('没有环' if not md else '（没有导入环）')
        return 0
    comp = set(sccs[0])
    internal = sorted((a, b) for a in comp for b in edges_all.get(a, ())
                      if b in comp)
    rows = [(uses, kind, a, b, names)
            for a, b in internal
            for kind, names, uses in [_edge_detail(srcs[a], b)]]
    out_deg = defaultdict(int)
    in_deg = defaultdict(int)
    for a, b in internal:
        out_deg[a] += 1
        in_deg[b] += 1
    fas = _feedback_edges(comp, edges_all)

    if md:
        print(f'- 环内模块 **{len(comp)}** 个 · 环内边 **{len(internal)}** 条'
              f'（模块级 {sum(1 for a, b in internal if b in edges_top.get(a, ()))} 条）')
        print(f'- 模块级环 **{len(data["import_cycles_top"])}** 个 · '
              f'反馈边近似 **{len(fas)}** 条')
        print()
        print('| 出度 | 入度 | 模块 |')
        print('|---:|---:|---|')
        for name in sorted(comp, key=lambda n: (out_deg[n] * in_deg[n],
                                                -out_deg[n])):
            print(f'| {out_deg[name]} | {in_deg[name]} | `{_short(name)}` |')
        print()
        print('| 用了几次 | 层级 | 边 | 导入了什么 |')
        print('|---:|---|---|---|')
        for uses, kind, a, b, names in rows:
            shown = ', '.join(f'`{n}`' for n in names[:4])
            if len(names) > 4:
                shown += f' …（共 {len(names)} 个）'
            print(f'| {uses} | {kind} | `{_short(a)}` → `{_short(b)}` | {shown} |')
        print()
        print('| 反馈边（要解环就处理这些） |')
        print('|---|')
        for a, b in fas:
            print(f'| `{_short(a)}` → `{_short(b)}` |')
        return 0

    print(f'最大环：{len(comp)} 个模块 · 环内边 {len(internal)} 条'
          f'（模块级 {sum(1 for a, b in internal if b in edges_top.get(a, ()))} 条）')
    print(f'全部环：{len(data["import_cycles_all"])} 个 · 模块级环：'
          f'{len(data["import_cycles_top"])} 个 · 反馈边近似：{len(fas)} 条')
    print()
    print(f"{'出':>3} {'入':>3} 模块（环内）")
    for name in sorted(comp, key=lambda n: (out_deg[n] * in_deg[n],
                                            out_deg[n] + in_deg[n])):
        print(f'{out_deg[name]:3d} {in_deg[name]:3d} {_short(name)}')
    print()
    print(f"{'用途':>4} {'层级':4} 边（导入了谁）")
    for uses, kind, a, b, names in rows:
        if only and only not in a and only not in b:
            continue
        print(f'{uses:4d} {kind:4} {_short(a)} → {_short(b)}  {names}')
    print()
    print('反馈边（近似最小集，解环就处理这些）：')
    for a, b in fas:
        print(f'  {_short(a)} → {_short(b)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
