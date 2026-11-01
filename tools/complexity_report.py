# -*- coding: utf-8 -*-
"""
复杂度报告：谁最长、谁最绕、谁参数最多。

用法：
    python tools/complexity_report.py                 # 前 25 个
    python tools/complexity_report.py --top 50 --json
    python tools/complexity_report.py --min-lines 80  # 只看超过 80 行的

判据（都从 AST 数出来，不引第三方库）：

  · **行数**：函数体占多少行（含 docstring —— 这个项目的 docstring 就是文档，
    但重构成 helper 时它是"搬走"的信号）；
  · **分支数**（cyclomatic 的近似）：`if` / `elif` / `for` / `while` / `except` /
    `with` / `BoolOp` / 推导式的 `if` / `assert` / 三元。>20 基本没法一眼读完；
  · **参数个数**：>6 说明这一层在替好几个调用方做决定。

为什么要有（2026-11-01，第二轮审查 P2-13/P2-14 之后）：
"这个类 113 个方法 / 4200 行"是审查给的结论，但**改哪里**要一份体检表。
这份报告就是那张表 —— 它只报数，不替人决定。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 一个函数超过这么多行就该考虑拆（不是硬线，是提醒）
LONG_FUNC = 120
#: cyclomatic 近似超过这个数就"一眼读不完"
COMPLEX_FUNC = 20


def _branch_count(node) -> int:
    """函数体的分支点个数（不含函数自身）。"""
    n = 0
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.IfExp, ast.For, ast.AsyncFor,
                              ast.While, ast.ExceptHandler, ast.Assert,
                              ast.With, ast.AsyncWith)):
            n += 1
        elif isinstance(child, ast.BoolOp):
            n += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            n += len(child.ifs)
    return n


def scan_file(path: Path) -> list:
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except SyntaxError as e:
        print(f'⚠ 语法错误，跳过 {path}: {e}', file=sys.stderr)
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(node, 'end_lineno', None) or node.lineno
        lines = int(end - node.lineno + 1)
        args = node.args
        params = (len(args.posonlyargs) + len(args.args)
                  + len(args.kwonlyargs)
                  + (1 if args.vararg else 0) + (1 if args.kwarg else 0))
        # 类里的方法去掉 self/cls
        if node.args.args and node.args.args[0].arg in ('self', 'cls'):
            params -= 1
        out.append({
            'file': path.relative_to(ROOT).as_posix(),
            'name': node.name,
            'line': int(node.lineno),
            'lines': lines,
            'branches': _branch_count(node),
            'params': max(0, params),
            'doc_lines': len((ast.get_docstring(node) or '').splitlines()),
        })
    return out


def collect() -> list:
    rows = []
    for p in sorted((ROOT / 'storygraph').rglob('*.py')):
        rows.extend(scan_file(p))
    return rows


def report(rows: list, *, top: int = 25, min_lines: int = 0) -> int:
    pool = [r for r in rows if r['lines'] >= min_lines]
    print(f'扫描 {len(rows)} 个函数（storygraph/），'
          f'其中 {sum(1 for r in rows if r["lines"] > LONG_FUNC)} 个超过 '
          f'{LONG_FUNC} 行、'
          f'{sum(1 for r in rows if r["branches"] > COMPLEX_FUNC)} 个分支超过 '
          f'{COMPLEX_FUNC}。\n')

    def table(title, key, limit):
        print(f'== {title} ==')
        picked = sorted(pool, key=lambda r: (r[key], r['lines']), reverse=True)[:limit]
        for r in picked:
            print(f'  {r[key]:4d}  {r["file"]}:{r["line"]}  {r["name"]}()'
                  f'  （{r["lines"]} 行 / {r["branches"]} 分支 / '
                  f'{r["params"]} 参数）')
        print()

    table('最长的函数', 'lines', top)
    table('分支最多的函数', 'branches', top)
    table('参数最多的函数', 'params', max(8, top // 2))

    # 按文件汇总：改文件先看这张
    per_file = {}
    for r in pool:
        f = per_file.setdefault(r['file'], [0, 0])
        f[0] += r['lines']
        f[1] = max(f[1], r['lines'])
    print('== 按文件（函数总行数 / 最长函数）==')
    for file, (total, longest) in sorted(per_file.items(),
                                         key=lambda kv: kv[1][0], reverse=True)[:15]:
        print(f'  {total:6d}  {longest:4d}  {file}')
    print()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--top', type=int, default=25)
    ap.add_argument('--min-lines', type=int, default=0)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    rows = collect()
    if a.json:
        print(json.dumps({'functions': len(rows), 'rows': rows},
                         ensure_ascii=False, indent=1))
        return 0
    return report(rows, top=a.top, min_lines=a.min_lines)


if __name__ == '__main__':
    raise SystemExit(main())
