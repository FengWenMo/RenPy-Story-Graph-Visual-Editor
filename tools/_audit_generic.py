"""通用性审计 v2：排除 docstring，只看真正参与逻辑的字符串常量。"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

NAMES = ['lan', 'ling', 'xing', 'ye', 'dhy', 'jc', 'toto', 'dororo',
         'sr_general', 'cc_preview', 'akimbo', 'base_swimsuit', 'swimsuit',
         '千连万理', '凌跃捷', '叶怀瑾', '蒋忱', '杜恒远']


def docstring_nodes(tree: ast.AST) -> set:
    keep = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, 'body', None) or []
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                keep.add(id(body[0].value))
    return keep


def main(root: Path) -> int:
    hits = []
    for path in sorted(root.rglob('*.py')):
        if '__pycache__' in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
        except SyntaxError:
            continue
        skip = docstring_nodes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in skip:
                continue
            low = node.value.lower()
            for name in NAMES:
                if re.search(r'(?<![a-z0-9_])' + re.escape(name.lower()) + r'(?![a-z0-9_])', low):
                    hits.append((path, node.lineno, name, node.value[:160]))
                    break
    print(f'== 非 docstring 的字符串常量命中：{len(hits)} 处 ==')
    for path, line, name, text in hits:
        print(f'{path.relative_to(root.parent)}:{line}  [{name}]  {text!r}')
    return 0


if __name__ == '__main__':
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else 'storygraph').resolve()))
