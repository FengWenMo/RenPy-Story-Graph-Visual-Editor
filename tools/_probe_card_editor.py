"""一次性探针：统计 BeatCardEditor 依赖的 main_window 模块级名字。"""
from __future__ import annotations

import ast
import pathlib


def main() -> None:
    p = pathlib.Path('storygraph/ui_qt/main_window.py')
    tree = ast.parse(p.read_text(encoding='utf-8'))

    defined: set[str] = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            defined.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            defined.add(n.target.id)

    imports: set[str] = set()
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                imports.add(a.asname or a.name.split('.')[0])

    for cls_name in ('BeatCardEditor', 'CardActionRow', 'CardColumnExpandFilter'):
        cls = next((n for n in tree.body if isinstance(n, ast.ClassDef)
                    and n.name == cls_name), None)
        if cls is None:
            print(f'{cls_name}: not found')
            continue
        print(f'{cls_name} lines {cls.lineno}..{cls.end_lineno}'
              f' ({cls.end_lineno - cls.lineno + 1} 行)')
        used: set[str] = set()
        for node in ast.walk(cls):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                pass
        print('  module-level:', sorted((used & defined) - {cls_name}))
        print('  imported    :', sorted(used & imports))


if __name__ == '__main__':
    main()
