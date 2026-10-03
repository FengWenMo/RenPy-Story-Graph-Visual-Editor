#!/usr/bin/env python
"""临时：把"什么都不说"的 handler 按体形状分类。"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'storygraph'
TELLS = {'swallow', 'append', 'extend', 'warning', 'error', 'critical',
         'information', 'question', 'setText', 'print', 'log', 'exception'}

shapes = Counter()
examples = {}
other = []

for p in sorted(PKG.rglob('*.py')):
    src = p.read_text(encoding='utf-8')
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        if ast.unparse(node.type) not in ('Exception', 'BaseException'):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        calls = {n.func.attr if isinstance(n.func, ast.Attribute)
                 else getattr(n.func, 'id', '')
                 for n in ast.walk(node) if isinstance(n, ast.Call)}
        if (names | attrs | calls) & TELLS:
            continue
        detail = []
        for st in node.body:
            if isinstance(st, ast.Pass):
                detail.append('pass')
            elif isinstance(st, ast.Return):
                detail.append('return ' + (ast.unparse(st.value)
                                           if st.value else '(空)'))
            elif isinstance(st, ast.Continue):
                detail.append('continue')
            elif isinstance(st, ast.Break):
                detail.append('break')
            elif isinstance(st, ast.Assign):
                detail.append('assign:' + ast.unparse(st.targets[0]))
            elif isinstance(st, ast.Expr):
                detail.append('expr:' + ast.unparse(st.value)[:40])
            else:
                detail.append(type(st).__name__)
        key = ' | '.join(detail)
        shapes[key] += 1
        examples.setdefault(key, f'{p.relative_to(ROOT)}:{node.lineno}')
        if key not in ('pass',) and not key.startswith('return') \
                and not key.startswith('continue') \
                and not key.startswith('break') \
                and not key.startswith('assign:'):
            other.append((f'{p.relative_to(ROOT)}:{node.lineno}', key))

print('== 体形状 ==')
for k, v in shapes.most_common(40):
    print('  %-56s %3d   e.g. %s' % (k[:56], v, examples[k]))
print()
if other:
    print('== 需要人看的 ==')
    for loc, k in other:
        print('  %s  %s' % (loc, k[:70]))
