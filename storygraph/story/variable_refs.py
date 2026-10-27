# -*- coding: utf-8 -*-
"""
条件里**读**的变量 vs 节点里**写**的变量 —— 对不上的地方要当场说。

## 为什么必须有这一层（2026-10-28，真实项目 P0）

用户在 12 个章节节点里写 `Plotnode214=1`……，在 5 条分支条件里写
`Plotnodes214 + Plotnodes224 + Plotnodes244 == 3`。**差一个 `s`** ——
条件读的是另一批变量，它们从没被赋值，于是 4 条路线永远不成立、全部落进
兜底分支，而**没有一条报错**：

  · 「语法检查」抓不到 —— 两行各自都是合法 Python，`ast.parse` 都过；
  · Ren'Py lint 抓不到 —— 变量在 `plottree.rpy` 里有 `default`，不报未定义；
  · 跑起来也不炸 —— 只是玩家永远进不去那四条线。

只有把"谁写"和"谁读"摆在同一张桌上才看得见。这个模块就干这件事，
判据全部是**静态**的（不做任何运行时假设）：

  · 读 = 所有边上 `condition` 里的 `Name`（AST 抽，不是正则）＋ 语句自带的
    `condition`；
  · 写 = 所有节点代码语句里的赋值目标（`x = …` / `x += …` / `x: int = …`），
    只认**裸名字**（`persistent.x` / `d["k"]` 不算给 `x` 赋值）。

## 只报两种，都可解释

1. **读了没人写**：这条条件永远按"变量为初始值"算 —— 分支不会成立。
   如果同时存在一个"像拼错了"的已写变量（差一个字符），直接点名。
2. **写了没人读**（只在"差一个字符"时报）：`Plotnode214` 写得很勤，但
   没有任何条件读它 —— 多半就是上面那种拼错。

不报"写了但项目里别处会读"的普通情况：图只看得见图里的条件，
`plottree.rpy` 里的 `screen` 读什么它不知道，也不该猜。
"""
from __future__ import annotations

import ast
import re

from storygraph.core.statement import (is_code_statement, operation_source,
                                       strip_marker)

#: 不是变量名的词（条件里可能出现）
_NOT_VARIABLES = frozenset((
    'True', 'False', 'None', 'and', 'or', 'not', 'in', 'is', 'if', 'else',
    'for', 'while', 'lambda', 'return', 'yield', 'await', 'assert', 'del',
    'import', 'from', 'pass', 'break', 'continue', 'class', 'def', 'try',
    'except', 'finally', 'with', 'as', 'global', 'nonlocal', 'raise',
))

#: 兜底正则（条件解析不了时才用）：`Name` 的粗筛
_RE_NAME = re.compile(r'(?<![\w.])[A-Za-z_]\w*(?![\w(])')


def condition_variables(condition) -> set:
    """
    一条条件表达式读了哪些变量（AST；解析不了退回正则）。

    `x.y` 只算读了 `x`；函数调用 `f(a)` 算读了 `f` 与 `a`（`f` 通常不是剧情
    变量，但宁可多报一个，也别漏掉真正的那个）。
    """
    text = str(condition or '').strip()
    if not text:
        return set()
    try:
        tree = ast.parse(text, mode='eval')
    except SyntaxError:
        return {m.group(0) for m in _RE_NAME.finditer(text)
                if m.group(0) not in _NOT_VARIABLES}
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in _NOT_VARIABLES:
                out.add(node.id)
    return out


def assignment_variables(code) -> set:
    """一条代码语句给哪些**裸名字**赋了值（`x = …` / `x += …` / `x: int = …`）。"""
    text = strip_marker(str(code or '')).strip()
    if not text:
        return set()
    try:
        tree = ast.parse(text, mode='exec')
    except SyntaxError:
        # 解析不了就当没有（语法错误由「语法检查」那条路报，别在这儿重复）
        return set()
    out = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for tgt in targets:
            for name in _name_targets(tgt):
                out.add(name)
    return out


def _name_targets(target) -> list:
    """赋值目标里的裸名字（`a, b = …` / `[a, b] = …` 要拆开；属性/下标不算）。"""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        out = []
        for elt in target.elts:
            out.extend(_name_targets(elt))
        return out
    if isinstance(target, ast.Starred):
        return _name_targets(target.value)
    return []


def written_variables(graph) -> dict:
    """`变量名 → [写它的节点 label, …]`（同一个节点里写两次只记一次）。"""
    out: dict = {}
    for node in (getattr(graph, 'nodes', {}) or {}).values():
        label = str(getattr(node, 'label_name', '') or '')
        for stmt in (getattr(node, 'statements', []) or []):
            if not is_code_statement(stmt):
                continue
            for name in assignment_variables(operation_source(stmt)):
                owners = out.setdefault(name, [])
                if label and label not in owners:
                    owners.append(label)
    return out


def read_variables(graph) -> dict:
    """`变量名 → [读它的节点 label, …]`（边上的条件 + 语句自带的 condition）。"""
    out: dict = {}
    for edge in (getattr(graph, 'edges', {}) or {}).values():
        names = condition_variables(getattr(edge, 'condition', ''))
        if not names:
            continue
        from_id = getattr(edge, 'from_id', None)
        node = (getattr(graph, 'nodes', {}) or {}).get(from_id)
        label = str(getattr(node, 'label_name', '') or '') or str(from_id or '')
        for name in names:
            owners = out.setdefault(name, [])
            if label and label not in owners:
                owners.append(label)
    return out


def _one_edit_apart(a: str, b: str) -> bool:
    """`a` 与 `b` 只差一个字符（多一个/少一个/改一个）。"""
    if not a or not b or a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    short, long = (a, b) if la < lb else (b, a)
    i = j = 0
    skipped = False
    while i < len(short) and j < len(long):
        if short[i] == long[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped = True
            j += 1
    return True


def lookalikes(written, read_vars) -> dict:
    """`读的变量 → 那个"差一个字符"的已写变量`（找不到就不进结果）。"""
    out = {}
    for name in sorted(read_vars):
        for cand in sorted(written):
            if _one_edit_apart(name, cand):
                out[name] = cand
                break
    return out


def unassigned_condition_variables(graph) -> list:
    """
    条件里读了、但**图里没有任何节点给它赋值**的变量（排好序）。

    返回 `[{'name', 'read_by', 'lookalike'}, …]`；`lookalike` 是"像拼错了"
    的那个已写变量（没有就是空串）。
    """
    written = written_variables(graph)
    read = read_variables(graph)
    near = lookalikes(written, read)
    out = []
    for name in sorted(read):
        if name in written:
            continue
        out.append({'name': name, 'read_by': list(read[name]),
                    'lookalike': near.get(name, '')})
    return out


def describe_unassigned(graph, limit: int = 6) -> list:
    """
    给导出/检查用的一句话警告（空列表 = 没问题）。

    文案必须**说清后果**："这些分支永远不会成立"，而不是"变量未赋值"——
    后者听起来像 lint 的噪音，前者是作者真正需要知道的事。
    """
    found = unassigned_condition_variables(graph)
    if not found:
        return []
    names = [f"`{i['name']}`" for i in found[:limit]]
    more = (f" 等 {len(found)} 个" if len(found) > limit else '')
    hints = [i for i in found[:limit] if i.get('lookalike')]
    msg = (f'分支条件里读了 {"、".join(names)}{more}，但图里没有任何节点给它们'
           f'赋值 —— 这些条件永远按"变量为初始值"算，对应的分支进不去。')
    if hints:
        pairs = '、'.join(f"`{i['name']}`（节点里写的是 `{i['lookalike']}`）"
                          for i in hints[:4])
        msg += (f' 其中 {pairs} 只差一个字符，像是拼错了 —— '
                f'把节点里那几行改成条件用的名字即可。')
    return [msg]


def describe_near_miss(graph, lines, limit: int = 4) -> list:
    """
    「语法检查」按钮用的**即时**提示：用户正敲的这几行，有没有"差一个字符"。

    只在**确实存在**一个只差一个字符、且被条件读过的变量名时才说话 ——
    普通变量（比如 `love`）不会被指手画脚。
    """
    read = read_variables(graph)
    written = written_variables(graph)
    typed = set()
    for line in (lines or []):
        typed |= assignment_variables(line)
    out = []
    for name in sorted(typed):
        if name in read:
            continue                     # 条件里读的正是它 —— 没问题
        for cand in sorted(read):
            if cand in written:
                continue                 # 那个名字也有人写，不是拼错
            if _one_edit_apart(name, cand):
                out.append(f'`{name}` 没有任何条件读它；条件里读的是 '
                           f'`{cand}`（差一个字符）—— 这一行不会让那条分支'
                           f'成立，改哪一个由你定。')
                break
    if len(out) > limit:
        out = out[:limit] + [f'……还有 {len(out) - limit} 行同类问题']
    return out


__all__ = ['assignment_variables', 'condition_variables',
           'describe_near_miss', 'describe_unassigned', 'lookalikes',
           'read_variables', 'unassigned_condition_variables',
           'written_variables']
