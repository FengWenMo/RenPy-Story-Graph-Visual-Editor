#!/usr/bin/env python
"""
临时：精确统计"什么都不说"的 `except Exception`。

第一版**高估了**：`ast.walk(handler)` 会把 `handler.type`（`Exception`
这个名字）一起走一遍，而我按"体里出现过哪些名字"判断有没有说 ——
于是 `e` 只要在**类型**里…… 不，问题在另一头：
我漏了 `f'…{e}'`（f-string 里的 `e` 是 `FormattedValue` 下的 `Name`，
`ast.walk` 能拿到，但 `_status(f'分析失败：{e}')` 的 call 名 `_status`
不在白名单里、而 `e` 又不是白名单词，于是被判成"没说"）。
其实那条**说了**（写进状态栏了）。

这一版改成**只看 handler.body 自己**、并且换一套判据：
体里有没有用异常对象、有没有任何调用、有没有赋值给外部名字。
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'storygraph'

rows = []
for p in sorted(PKG.rglob('*.py')):
    src = p.read_text(encoding='utf-8')
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        if ast.unparse(node.type) not in ('Exception', 'BaseException'):
            continue
        # **只看体**：把 body 单独 unparse，避免把 type 也算进来
        body_src = '\n'.join(ast.unparse(st) for st in node.body)
        calls = [n for st in node.body for n in ast.walk(st)
                 if isinstance(n, ast.Call)]
        uses_exc = bool(node.name) and (
            f'{{{node.name}}}' in body_src
            or f'{node.name})' in body_src
            or f'{node.name}.' in body_src
            or f' {node.name}' in body_src
            or f'({node.name}' in body_src
            or f', {node.name}' in body_src)
        # "说了" = 用了异常对象 **或** 有一次调用（记日志/发事件/弹窗都算）
        said = uses_exc or bool(calls)
        rows.append({
            'file': str(p.relative_to(ROOT)),
            'line': node.lineno,
            'shape': ' | '.join(type(st).__name__ for st in node.body),
            'body': body_src.replace('\n', ' ⏎ ')[:70],
            'said': said,
        })

silent = [r for r in rows if not r['said']]
print('`except Exception` 总数:', len(rows))
print('  **真的什么都不说**:', len(silent))
print()
print('== 按形状 ==')
for k, v in Counter(r['shape'] for r in silent).most_common():
    ex = next(r for r in silent if r['shape'] == k)
    print('  %-24s %3d   e.g. %s:%d  %s'
          % (k, v, ex['file'], ex['line'], ex['body']))
print()
print('== 按文件 ==')
for k, v in Counter(r['file'] for r in silent).most_common(24):
    print('  %-46s %d' % (k, v))
