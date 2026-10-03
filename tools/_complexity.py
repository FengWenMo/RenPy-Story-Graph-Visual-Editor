#!/usr/bin/env python
"""临时：列出全项目圈复杂度头部（近似 flake8-cognitive-complexity 口径）。"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'storygraph'

#: 每加一个分支 +1（与 mccabe 同口径，够用来看"哪里最该拆"）
BRANCH = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try,
          ast.ExceptHandler, ast.With, ast.AsyncWith, ast.BoolOp,
          ast.IfExp, ast.comprehension, ast.Match)


def cc(node) -> int:
    n = 1
    for sub in ast.walk(node):
        if isinstance(sub, BRANCH):
            if isinstance(sub, ast.BoolOp):
                n += max(0, len(sub.values) - 1)
            else:
                n += 1
    return n


rows = []
for p in sorted(PKG.rglob('*.py')):
    src = p.read_text(encoding='utf-8')
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        rows.append((cc(node), len(src.splitlines()[node.lineno - 1:
                                                     (node.end_lineno or
                                                      node.lineno)]),
                     str(p.relative_to(ROOT)), node.lineno, node.name))

rows.sort(reverse=True)
print('%-6s %-6s %s' % ('复杂度', '行数', '位置'))
for c, ln, f, lineno, name in rows[:28]:
    print('%6d %6d %s:%d  %s()' % (c, ln, f, lineno, name))
print()
print('>20 的函数数:', sum(1 for r in rows if r[0] > 20))
print('>15 的函数数:', sum(1 for r in rows if r[0] > 15))
print('总函数数:', len(rows))
