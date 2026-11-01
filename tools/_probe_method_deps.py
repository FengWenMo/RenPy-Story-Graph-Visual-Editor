"""一次性探针：报 main_window 里若干**方法**用到哪些模块级名字 / import。"""
from __future__ import annotations

import ast
import builtins
import pathlib
import sys


def main() -> None:
    want = sys.argv[1:]
    tree = ast.parse(pathlib.Path('storygraph/ui_qt/main_window.py')
                     .read_text(encoding='utf-8'))
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
    for target in want:
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == target)
        used: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                used.add(node.id)
        used -= set(dir(builtins))
        print(f'--- {target}: {fn.end_lineno - fn.lineno + 1} 行')
        print('  main_window 模块级：', sorted(used & defined))
        print('  import            ：', sorted(used & imports))
        print('  其它自由名        ：', sorted(used - defined - imports))


if __name__ == '__main__':
    main()
