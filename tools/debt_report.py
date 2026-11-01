# -*- coding: utf-8 -*-
"""
技术债体检：把"复杂度"之外那几笔债也**量出来**。

用法：
    python tools/debt_report.py                # 人看的报告
    python tools/debt_report.py --json         # 机器读（给棘轮测试/CI）
    python tools/debt_report.py --cycles       # 只列导入环（逐条）

量什么、为什么：

| 指标 | 为什么算债 |
|---|---|
| 超大函数（>120 行） / 高分支（>20） | 改一处要读一屏，回归概率随长度线性涨（复用 `complexity_report`） |
| **模块级导入环** | 真实的依赖方向被藏起来了；层次测试看不出、改一个模块全崩 |
| **含函数内 import 的环** | 审查说的"27 个模块的环"就是这种：能跑，但依赖关系是隐形的 |
| `print(` 数量 | 打包成无窗口程序后没有输出通道；日志才是可诊断的 |
| 宽泛 `except` / `swallow(` | 失败被咽下去的入口数量（项目允许 swallow，但**总数**要看得见） |
| `TODO` / `FIXME` / `XXX` / `HACK` | 写着"以后再说"的地方 |
| 超长文件（>1500 行） | 单文件太大 → 阅读/合并冲突成本 |

只统计 `storygraph/`（产品代码）；`tests/`、`tools/` 不算债。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'storygraph'

LONG_FUNC = 120
COMPLEX_FUNC = 20
LONG_FILE = 1500

_TODO_RE = re.compile(r'\b(TODO|FIXME|XXX|HACK)\b')


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix('')
    parts = list(rel.parts)
    if parts and parts[-1] == '__init__':
        parts = parts[:-1]
    return '.'.join(parts)


def _imports_of(tree) -> tuple:
    """返回 `(顶层导入, 全部导入)` —— 模块名集合。"""
    top, all_ = set(), set()

    def take(node, bucket):
        if isinstance(node, ast.Import):
            for a in node.names:
                bucket.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                bucket.add(node.module)
                for a in node.names:
                    if a.name != '*':
                        bucket.add(f'{node.module}.{a.name}')

    for node in tree.body:
        take(node, top)
    for node in ast.walk(tree):
        take(node, all_)
    return top, all_


def _sccs(nodes: set, edges: dict) -> list:
    """Tarjan（迭代版）→ 强连通分量列表（只返回 size>1 的环）。"""
    index = {}
    low = {}
    on_stack = set()
    stack = []
    out = []
    counter = [0]

    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, iter(sorted(edges.get(root, ()))))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in nodes:
                    continue
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(edges.get(nxt, ())))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    out.append(sorted(comp))
    return out


def collect() -> dict:
    sys.path.insert(0, str(ROOT / 'tools'))
    import complexity_report as CR          # noqa: E402 - 同目录工具（分支判据的唯一实现）

    files = sorted(PKG.rglob('*.py'))
    modules = {_module_name(p) for p in files}
    top_edges = defaultdict(set)
    all_edges = defaultdict(set)
    prints = 0
    broad = 0
    swallows = 0
    todos = 0
    long_files = []
    total_lines = 0
    funcs = []
    for path in files:
        try:
            src = path.read_text(encoding='utf-8')
        except OSError as e:                # noqa: PERF203 - 读不了就跳过并计数
            print(f'⚠ 读不了 {path}: {e}', file=sys.stderr)
            continue
        total_lines += src.count('\n') + 1
        if len(src.splitlines()) > LONG_FILE:
            long_files.append({'file': path.relative_to(ROOT).as_posix(),
                               'lines': len(src.splitlines())})
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            print(f'⚠ 语法错 {path}: {e}', file=sys.stderr)
            continue
        cfg = _module_name(path)
        top, all_ = _imports_of(tree)
        for name in top:
            base = '.'.join(name.split('.')[:3])     # storygraph.a.b
            if base in modules and base != cfg:
                top_edges[cfg].add(base)
        for name in all_:
            base = '.'.join(name.split('.')[:3])
            if base in modules and base != cfg:
                all_edges[cfg].add(base)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == 'print':
                    prints += 1
                elif node.func.id == 'swallow':
                    swallows += 1
            elif isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    broad += 1
                elif isinstance(node.type, ast.Name) and node.type.id == 'Exception':
                    broad += 1
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # 与 `complexity_report` **同一个分支判据**（不另写一套）
                end = getattr(node, 'end_lineno', None) or node.lineno
                funcs.append({
                    'file': path.relative_to(ROOT).as_posix(),
                    'name': node.name,
                    'lines': int(end - node.lineno + 1),
                    'branches': CR._branch_count(node),
                })
        todos += len(_TODO_RE.findall(src))

    cycles_top = _sccs(set(modules), top_edges)
    cycles_all = _sccs(set(modules), all_edges)

    return {
        'modules': len(modules),
        'files': len(files),
        'total_lines': total_lines,
        'functions': len(funcs),
        'long_functions': sum(1 for f in funcs if f['lines'] > LONG_FUNC),
        'complex_functions': sum(1 for f in funcs if f['branches'] > COMPLEX_FUNC),
        'file_edges_top': sum(len(v) for v in top_edges.values()),
        'file_edges_all': sum(len(v) for v in all_edges.values()),
        'import_cycles_top': [c for c in cycles_top],
        'import_cycles_all': [c for c in cycles_all],
        'print_calls': prints,
        'swallow_calls': swallows,
        'broad_excepts': broad,
        'todos': todos,
        'long_files': long_files,
        'thresholds': {'long_func': LONG_FUNC, 'complex_func': COMPLEX_FUNC,
                       'long_file': LONG_FILE},
    }


def _print_cycles(title: str, cycles: list) -> None:
    print(f'== {title}：{len(cycles)} 个环，涉及 '
          f'{sum(len(c) for c in cycles)} 个模块 ==')
    for comp in sorted(cycles, key=len, reverse=True):
        print(f'  [{len(comp)}] ' + ' → '.join(comp))
    print()


def report(data: dict, *, only_cycles: bool = False) -> int:
    if only_cycles:
        _print_cycles('模块级导入环', data['import_cycles_top'])
        _print_cycles('含函数内 import 的环', data['import_cycles_all'])
        return 0
    print(f"模块 {data['modules']} 个 / 文件 {data['files']} 个 / "
          f"{data['total_lines']} 行 / 函数 {data['functions']} 个")
    print(f"超大函数（>{data['thresholds']['long_func']} 行）："
          f"{data['long_functions']} 个 · "
          f"高分支（>{data['thresholds']['complex_func']}）："
          f"{data['complex_functions']} 个")
    print(f"导入边：顶层 {data['file_edges_top']} 条 / 含延迟 "
          f"{data['file_edges_all']} 条")
    _print_cycles('模块级导入环', data['import_cycles_top'])
    _print_cycles('含函数内 import 的环', data['import_cycles_all'])
    print(f"print 调用 {data['print_calls']} 处 · swallow {data['swallow_calls']} 处 "
          f"· 宽泛 except {data['broad_excepts']} 处 · "
          f"TODO/FIXME {data['todos']} 处")
    if data['long_files']:
        print(f"超长文件（>{data['thresholds']['long_file']} 行）：")
        for f in data['long_files']:
            print(f"  {f['lines']:5d}  {f['file']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--cycles', action='store_true')
    a = ap.parse_args(argv)
    data = collect()
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0
    return report(data, only_cycles=a.cycles)


if __name__ == '__main__':
    raise SystemExit(main())
