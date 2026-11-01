"""一次性探针：给定 main_window.py 的行区间，报它用到哪些模块级名字。"""
from __future__ import annotations

import ast
import pathlib
import sys


def module_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    defined: set[str] = set()
    imports: set[str] = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            defined.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            defined.add(n.target.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                imports.add(a.asname or a.name.split('.')[0])
    return defined, imports


def main() -> None:
    lo, hi = int(sys.argv[1]), int(sys.argv[2])
    tree = ast.parse(pathlib.Path('storygraph/ui_qt/main_window.py')
                     .read_text(encoding='utf-8'))
    defined, imports = module_names(tree)
    tops = [n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))
            and n.lineno >= lo and n.end_lineno <= hi]
    print('区间内的顶层成员：', ', '.join(n.name for n in tops))
    used_defined: set[str] = set()
    used_imports: set[str] = set()
    for node in tops:
        inside = {id(x) for x in ast.walk(node)}
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) \
                    and n.id in defined and n.id not in {t.name for t in tops}:
                used_defined.add(n.id)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) \
                    and n.id in imports:
                used_imports.add(n.id)
        _ = inside
    print('用到 main_window 其它模块级名字：', sorted(used_defined))
    print('用到的 import：', sorted(used_imports))


if __name__ == '__main__':
    main()
