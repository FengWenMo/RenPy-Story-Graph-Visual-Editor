#!/usr/bin/env python
"""
P3-13：把 88 处"什么都不说"的 `except Exception` 补上 `swallow(...)`。

判据（与 `tools/_audit_silent_except.py` 同源）：一个 `except` 体
**既没用异常对象、也没有任何调用**，就是"什么都不说" ——
出事时用户只看到"功能没反应"，我们连一行痕迹都没有。

**只加痕迹、不改行为**：在体首插一条 `swallow('<模块>.<函数>', e)`。
`swallow` 写的是 `core.safeguard` 的环形缓冲（有界、永不抛），
所以

  · 不改变任何返回值 / 控制流；
  · 不新增依赖（标准库 + 本项目 core 层）；
  · 出事时 `report()` / 控制台 / 体检都看得到。

三条铁律（第一版踩过的都写在这里）：
  1. `except Exception:` **没有 `as e`** 时要补上 `as e`，否则 `swallow` 拿不到详情；
  2. `except ... as e:` 里 `e` 可能在 Python 3 里被 `del` 掉 —— 但我们在
     体首就用它，安全；
  3. `pass` 体要**换成** `swallow(...)`，不能变成 `swallow(...)` + `pass`
     （那不算错，但没必要；唯一例外是 `safeguard.py` 自己，见下）。

`safeguard.py:88` 是**故意**的 `except: pass`（`swallow` 自己不能递归调自己），
在 `SWALLOW_SKIP` 里跳过，并要求那里带说明注释。
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'storygraph'

#: 故意不动的：`swallow` 自己的兜底（递归风险），
#: 以及 core 层里不允许依赖 safeguard 的地方（如果有）。
SWALLOW_SKIP = {
    ('core/safeguard.py', 88),        # swallow 内部：再调自己会无限递归
}

#: 这些文件已经在用 `swallow`，import 要合并而不是重复加
IMPORT_RE = re.compile(r'^from storygraph\.core\.safeguard import ([^\n]+)$',
                       re.M)


def _func_name(tree, lineno: int) -> str:
    """`lineno` 属于哪个函数（取最内层那个）。"""
    best = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.lineno <= lineno <= (node.end_lineno or node.lineno):
            if best is None or node.lineno > best.lineno:
                best = node
    return best.name if best else '<module>'


def _is_silent(node: ast.ExceptHandler) -> bool:
    if node.type is None:
        return False
    if ast.unparse(node.type) not in ('Exception', 'BaseException'):
        return False
    body_src = '\n'.join(ast.unparse(st) for st in node.body)
    calls = [n for st in node.body for n in ast.walk(st)
             if isinstance(n, ast.Call)]
    if calls:
        return False
    if node.name:
        if (f'{{{node.name}}}' in body_src or f'{node.name}.' in body_src
                or f' {node.name}' in body_src or f'({node.name}' in body_src
                or f', {node.name}' in body_src):
            return False
    return True


def main(apply: bool) -> int:
    targets = []
    for p in sorted(PKG.rglob('*.py')):
        rel = p.relative_to(PKG).as_posix()
        src = p.read_text(encoding='utf-8')
        lines = src.splitlines(keepends=True)
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            print(f'!! {rel} 解析不了：{e}')
            return 1
        edits = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or not _is_silent(node):
                continue
            if (rel, node.lineno) in SWALLOW_SKIP:
                print(f'  · 跳过（故意）{rel}:{node.lineno}')
                continue
            head = lines[node.lineno - 1]
            indent = len(head) - len(head.lstrip())
            pad = ' ' * (indent + 4)
            fn = _func_name(tree, node.lineno)
            ctx = f'{rel[:-3].replace("/", ".")}.{fn}'
            # 体首插入 swallow
            insert_at = node.body[0].lineno - 1
            call = f"{pad}swallow('{ctx}', e)\n"
            extra = []
            if not node.name:
                # 没有 `as e`：补上
                extra.append((node.lineno - 1, 'add_as_e'))
            if (len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
                    and (rel, node.lineno) not in SWALLOW_SKIP):
                extra.append((node.body[0].lineno - 1, 'drop_pass'))
            edits.append((insert_at, call, extra, rel, node.lineno, ctx))
            targets.append((rel, node.lineno, ctx))

        if not edits:
            continue
        if not apply:
            for _a, _c, _e, rel2, ln, ctx in edits:
                print(f'  {rel2}:{ln}  + swallow({ctx!r}, e)')
            continue

        # 从后往前改，行号不漂
        for insert_at, call, extra, rel2, ln, ctx in sorted(
                edits, key=lambda t: -t[0]):
            if any(k == 'add_as_e' for _i, k in extra):
                h = lines[ln - 1]
                nl = '\n' if h.endswith('\n') else ''
                body = h.rstrip('\n')
                if body.rstrip().endswith(':'):
                    lines[ln - 1] = body.rstrip()[:-1].rstrip() + ' as e:' + nl
                else:
                    lines[ln - 1] = body.rstrip() + ' as e' + nl
            for i, kind in extra:
                if kind == 'drop_pass':
                    lines[i] = ''
            lines.insert(insert_at, call)

        text = ''.join(lines)
        # 补 import
        if not IMPORT_RE.search(text):
            # 插在最后一个顶层 import 之后（`from __future__` 之后）
            m = list(re.finditer(r'^(?:from|import) [^\n]+$', text, re.M))
            if m:
                last = m[-1]
                text = (text[:last.end()] + '\nfrom storygraph.core.safeguard '
                        'import swallow' + text[last.end():])
            else:
                text = ('from storygraph.core.safeguard import swallow\n'
                        + text)
        elif 'swallow' not in IMPORT_RE.search(text).group(1).split(',')[0] \
                and 'swallow' not in IMPORT_RE.search(text).group(1):
            m = IMPORT_RE.search(text)
            text = (text[:m.start(1)]
                    + 'swallow, ' + m.group(1) + text[m.end(1):])

        p.write_text(text, encoding='utf-8')

    print()
    print('目标数:', len(targets))
    return 0


if __name__ == '__main__':
    sys.exit(main(apply='--apply' in sys.argv))
