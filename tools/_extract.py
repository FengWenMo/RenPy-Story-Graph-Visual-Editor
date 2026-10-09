"""M4-续 的通用提取工具：**搬块 + 自由名审计**（不是产品代码，是重构脚手架）。

为什么要有它：这一轮要连拆 8 个函数，每次手写切片脚本都踩同一个坑 ——
**搬走的块引用了宿主作用域里的东西**（宿主局部 / 闭包 / 局部 import / 容器）。
所以把它固化成"先审计、再搬"，审计不过就不写盘。

    extract(path, host, pick, new_name, params, call_lines, *,
            doc='', ret=None, dedent_to=None)

  · `pick`    : 在 host 顶层语句里挑一个（接收语句，返回 True）
  · `params`  : 新函数的形参（这些名字**不**参与审计）
  · `call_lines`: 用哪几行替换掉被搬走的块
  · `ret`     : 非空则在末尾 `return <expr>`
  · 审计      : 块里的自由名必须落在 {params} ∪ {模块级定义/导入} ∪ {块内自己赋值的名字}
                ∪ {builtins} 里，否则**报错不写盘**。
"""
from __future__ import annotations

import ast
import builtins
import re
from pathlib import Path


def _module_names(tree: ast.Module) -> set:
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                out.add(a.asname or a.name.split('.')[0])
        elif isinstance(node, ast.Try):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for a in sub.names:
                        out.add(a.asname or a.name.split('.')[0])
    return out


def _free_names(block_src: str, bound: set, module: set) -> set:
    """
    块里的自由名 = 用到但既不是 bound（参数/块内赋值）也不是模块级的。

    ⚠ **`x += 1` 是"既读又写"**：它必须**本来就有值**，所以目标是**自由名**，
    不能算"块内定义"。第一版把 AugAssign 目标也算进 `assigned`，于是
    `outside_total += 1` 被放行 → 搬完当场 `UnboundLocalError`
    （`test_asset_tables_autocreate` 抓到）。这里只认**纯赋值**为绑定。
    """
    import textwrap
    t = ast.parse(textwrap.dedent(block_src))   # ⚠ 带缩进的片段要先 dedent 才能 parse
    assigned, used = set(), set()
    # `x += 1` 的目标既是读又是写 —— 它的 `Name` 节点 ctx 也是 Store，
    # 会被下面的通用分支加进 `assigned`。所以**先收集**，再在通用分支里改判。
    aug = {n.target.id for n in ast.walk(t)
           if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name)}

    def _targets(node):
        for n in ast.walk(node):
            if isinstance(n, ast.Name):
                assigned.add(n.id)

    for node in ast.walk(t):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Store):
                (used if node.id in aug else assigned).add(node.id)
            else:
                used.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assigned.add(node.name)
            a = node.args
            assigned.update(x.arg for x in
                            list(a.args) + list(a.kwonlyargs) + list(a.posonlyargs))
            if a.vararg:
                assigned.add(a.vararg.arg)
            if a.kwarg:
                assigned.add(a.kwarg.arg)
        elif isinstance(node, ast.Lambda):
            assigned.update(x.arg for x in node.args.args)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                assigned.add(a.asname or a.name.split('.')[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            assigned.add(node.name)
        elif isinstance(node, ast.comprehension):
            _targets(node.target)
        elif isinstance(node, ast.NamedExpr):
            _targets(node.target)
    return {n for n in used
            if n not in assigned and n not in bound and n not in module
            and not hasattr(builtins, n)}


def extract(path, host_name, pick, new_name, params, call_lines, *,
            doc='', ret=None, prologue=(), audit_allow=(), dry=False,
            method=False):
    P = Path(path)
    text = P.read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    host = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == host_name)
    stmts = [s for s in host.body if pick(s)]
    assert len(stmts) == 1, f'pick 命中 {len(stmts)} 个语句'
    st = stmts[0]
    start, end = st.lineno - 1, st.end_lineno
    body = ''.join(lines[start:end])

    module = _module_names(tree)
    # ⚠ `audit_allow` **只许用来放行"确实在模块级/参数里"的名字**——
    # 拿它掩盖真正的自由名（上一轮我把累加器 `matched` 放进去，结果
    # `UnboundLocalError`）。累加器要用 `prologue` 在函数里初始化。
    # ⚠ 宿主**局部 import / 嵌套闭包 / 宿主局部容器**都会在这里现形 ——
    # 它们是"搬走之后必须跟着走"的东西。
    bound = set(params) | set(audit_allow)
    all_params = list(params)
    if method:
        bound.add('self')       # 方法里的 `self` 由新方法自己带走
        if 'self' not in all_params:
            all_params = ['self'] + all_params
    free = _free_names(body, bound, module)

    # 块可能嵌在 `if`/`for` 里（缩进 8/12）—— **新函数体一律对齐到 4**
    ind = len(body) - len(body.lstrip(' '))
    if dry:
        return {'block': (st.lineno, st.end_lineno), 'free': sorted(free),
                'indent': ind, 'dedent': ind - 4}
    assert not free, ('搬走的块里有**自由名**（宿主作用域里的东西）：'
                      f'{sorted(free)}\n→ 要么加进 params、要么用 `prologue` '
                      '在新函数里初始化、要么改调用点名字。**没有写盘。**')
    # ⚠ 多行字符串里**不能**盲目 dedent（会改内容）—— 遇到就拒绝，人工处理。
    import io
    import tokenize
    multi = [(t.start[0], t.end[0]) for t in
             tokenize.generate_tokens(io.StringIO(body).readline)
             if t.type == tokenize.STRING and t.end[0] > t.start[0]]
    assert not multi, f'块里有跨行字符串，不能自动 dedent：{multi}'

    # 缩进对齐：新函数体——模块级落到 4，**方法落到 8**（def 本身在 4）
    target_ind = 8 if method else 4
    shift = ind - target_ind
    assert shift >= 0, f'块的缩进 {ind} 比目标 {target_ind} 还浅'
    moved = ''.join(
        (ln[shift:] if ln.startswith(' ' * shift) else ln)
        for ln in body.splitlines(keepends=True))
    sig = ', '.join(all_params)
    head = '    def' if method else 'def'
    pad = ' ' * target_ind          # docstring / prologue / return 的缩进
    new_fn = f'{head} {new_name}({sig}):\n'
    if doc:
        new_fn += f'{pad}"""{doc}"""\n'
    for ln in prologue:
        new_fn += f'{pad}{ln}\n'
    new_fn += moved
    if ret:
        new_fn += f'{pad}return {ret}\n'
    new_fn += '\n'

    lines[start:end] = [ln + '\n' for ln in call_lines]
    out = ''.join(lines)
    tree2 = ast.parse(out)
    host2 = next(n for n in ast.walk(tree2)
                 if isinstance(n, ast.FunctionDef) and n.name == host_name)
    o = out.splitlines(keepends=True)
    if method:
        # 方法：插在宿主**前面**（同一个类体里，缩进 4）
        o[host2.lineno - 1:host2.lineno - 1] = [new_fn]
    else:
        # 模块级函数：插在宿主前面（模块顶层，缩进 0）
        # ⚠ method=False 时宿主必须是模块级函数 —— 否则插进去会掉进类体里
        cls = next((c for c in ast.walk(tree2) if isinstance(c, ast.ClassDef)
                    and any(s is host2 for s in c.body)), None)
        assert cls is None, f'{host_name} 是方法，请用 method=True'
        o[host2.lineno - 1:host2.lineno - 1] = [new_fn]
    P.write_text(''.join(o), encoding='utf-8')
    return f'{host_name}: 已提出 {new_name}({sig})，块 {st.lineno}-{st.end_lineno}'


__all__ = ['extract', 're']
