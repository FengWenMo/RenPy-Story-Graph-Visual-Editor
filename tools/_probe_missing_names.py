"""一次性探针：报某个模块里"读到了但没定义/没导入"的全局名。"""
from __future__ import annotations

import ast
import builtins
import pathlib
import sys


def main() -> None:
    path = pathlib.Path(sys.argv[1])
    tree = ast.parse(path.read_text(encoding='utf-8'))
    bound: set[str] = set(dir(builtins))
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                bound.add(a.asname or a.name.split('.')[0])
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            bound.add(n.name)
        elif isinstance(n, ast.Assign):
            bound |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            bound.add(n.target.id)
    used: dict[str, int] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            used.setdefault(n.id, n.lineno)
    missing = sorted(k for k in used if k not in bound)
    print(f'{path.name}: 缺 {len(missing)} 个全局名')
    for name in missing:
        print(f'  {name}  (line {used[name]})')


if __name__ == '__main__':
    main()
