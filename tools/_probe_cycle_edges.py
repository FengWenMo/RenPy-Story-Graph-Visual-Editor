# -*- coding: utf-8 -*-
"""临时探针：列出每个导入环**内部的每一条边**（顶层 / 函数内两档），供拆环用。

用法：python tools/_probe_cycle_edges.py
"""
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import debt_report as DR  # noqa: E402


def main() -> int:
    files = sorted(DR.PKG.rglob('*.py'))
    modules = {DR._module_name(p) for p in files}
    top = defaultdict(set)
    all_ = defaultdict(set)
    where = defaultdict(set)      # (src, dst) -> {'top', 'in-fn'}
    for path in files:
        tree = DR.ast.parse(path.read_text(encoding='utf-8'))
        cfg = DR._module_name(path)
        for node in tree.body:
            for name in _names(node):
                base = '.'.join(name.split('.')[:3])
                if base in modules and base != cfg:
                    where[(cfg, base)].add('top')
                    top[cfg].add(base)
        for node in DR.ast.walk(tree):
            if not isinstance(node, (DR.ast.Import, DR.ast.ImportFrom)):
                continue
            for name in _names(node):
                base = '.'.join(name.split('.')[:3])
                if base in modules and base != cfg:
                    all_[cfg].add(base)
                    if node.col_offset:
                        where[(cfg, base)].add('in-fn')
    for comp in DR._sccs(set(modules), all_):
        inside = set(comp)
        print(f'\n===== SCC size {len(comp)} =====')
        for src in comp:
            for dst in sorted(all_.get(src, ())):
                if dst in inside:
                    kinds = '/'.join(sorted(where.get((src, dst), {'in-fn'})))
                    print(f'  {src:48s} -> {dst:48s} [{kinds}]')
    return 0


def _names(node):
    if isinstance(node, DR.ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, DR.ast.ImportFrom) and node.module:
        return [node.module] + [f'{node.module}.{a.name}'
                                for a in node.names if a.name != '*']
    return []


if __name__ == '__main__':
    raise SystemExit(main())
