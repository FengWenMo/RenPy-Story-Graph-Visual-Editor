"""一次性探针：谁从 main_window 里拿了哪些名字。"""
from __future__ import annotations

import ast
import collections
import pathlib


def main() -> None:
    hits: dict[str, list[str]] = collections.defaultdict(list)
    for p in list(pathlib.Path('.').rglob('*.py')):
        sp = str(p)
        if any(part in sp for part in ('.pip-tmp', '.git', 'build', 'dist')):
            continue
        try:
            tree = ast.parse(p.read_text(encoding='utf-8'))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in (
                    'storygraph.ui_qt.main_window',
                    'storygraph.ui_qt.main_window_qt'):
                for a in node.names:
                    hits[a.asname or a.name].append(f'{p}:{node.lineno}')
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in ('storygraph.ui_qt.main_window',
                                  'storygraph.ui_qt.main_window_qt'):
                        hits['<module>'].append(str(p))
    for name in sorted(hits):
        where = hits[name]
        if name == '<module>':
            print(f'{name:38s} {len(where):3d}  ' + ', '.join(where))
            continue
        srcs = collections.Counter(w.split(':')[0] for w in where)
        print(f'{name:38s} {len(where):3d}  ' + ', '.join(
            f'{k}×{v}' for k, v in srcs.most_common()))


if __name__ == '__main__':
    main()
