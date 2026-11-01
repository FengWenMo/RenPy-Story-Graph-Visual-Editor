"""一次性探针：报某个模块里"导入了但没用到"的名字（re-export 行跳过）。"""
from __future__ import annotations

import ast
import pathlib
import sys


def main() -> None:
    path = pathlib.Path(sys.argv[1])
    src = path.read_text(encoding='utf-8')
    tree = ast.parse(src)
    lines = src.splitlines()
    imported: dict[str, int] = {}
    keep: set[str] = set()
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            line = lines[n.lineno - 1]
            if 'noqa' in line or 're-export' in line:
                for a in n.names:
                    keep.add(a.asname or a.name.split('.')[0])
                continue
            for a in n.names:
                imported[a.asname or a.name.split('.')[0]] = n.lineno
    used: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            used.add(n.id)
    unused = sorted(k for k in imported if k not in used)
    print(f'{path.name}: 导入 {len(imported)} 个，白名单 {len(keep)} 个，'
          f'疑似没用到 {len(unused)} 个')
    for name in unused:
        print(f'  line {imported[name]}: {name}')


if __name__ == '__main__':
    main()
