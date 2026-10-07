#!/usr/bin/env python
"""探针：找出"真·什么都不说"的 `except: pass`（health_check 只报了个数）。"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    hits = 0
    for p in sorted((ROOT / 'storygraph').rglob('*.py')):
        try:
            tree = ast.parse(p.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = [n for n in node.body
                    if not (isinstance(n, ast.Expr)
                            and isinstance(n.value, ast.Constant)
                            and isinstance(n.value.value, str))]
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                hits += 1
                print(f'{p.relative_to(ROOT)}:{node.lineno}')
    print(f'共 {hits} 处')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
