"""
storygraph.story.python_lift
============================
把 `python:` 块里的**单行赋值**搬回脚本层（`$` 行）的判定与计划。

用户 2026-10-22（方向性判断，决定这一刀怎么切）：

> python块别介入内部，但有一件事方向相反、值得做……真机那 26 条里 21 条是
> 脚本层 `$` 就写得出来的赋值。它们待在 python 块里不只是"工具管不着"，
> 而是有害的 —— 存档粒度是整块。……正确的方向不是"工具去适应 python 块"，
> 是"工具帮用户把语句从 python 块里搬出来"。

所以这里**不解析块内的任意代码**（那会让工具变成半个 IDE），只回答一个问题：

> 这一块里有哪些行，**原样挪到 `$` 行、语义不变**？

判据刻意保守 —— 只有同时满足下面每一条的行才算"可搬"：

  1. 它是**块体顶层**的语句（缩进等于块体基准；`if` / `for` / `with` 的体里
     那些不算 —— 搬出去会丢掉嵌套结构）；
  2. 它是**单行**语句（`ast` 的 `end_lineno == lineno`）：续行、三引号串
     一律不搬，否则 `$` 只能跟第一行，语义会断；
  3. 它是**简单赋值**：`name = …` 或 `name op= …`，目标是**一个普通变量名**。
     `persistent.x = …` / `store.y = …` / `obj.attr = …` / `d[k] = …` /
     元组解包都不搬（那些不是"剧情变量"，也不该进表格）；
  4. 名字不以 `_` 开头（Ren'Py 的私有约定；与 `story_ops.is_narrative_code`
     同一口径）。

**顺序不变**是这一刀能成立的全部理由：搬出来的行留在**原来的行位置**上
（块体前半 → `$` 行 → 块体后半），所以执行顺序一字不动，也不存在"变量还没
定义"的问题 —— 这也解释了为什么不需要做依赖分析。

唯一的形状约束是第 5 条：

  5. 搬完**不能把块拆出"只有注释的块"**：`python:` 块里只有注释是语法错
     （见过真实项目里作者把整段注释留在块里，靠上下语句撑着）。所以夹在两条
     被搬语句中间的那条注释，会**拦住**离它最近的那条搬动（那条语句留下，
     块里就还有语句）。空行没有这个问题（块外渲染成空行，合法）。

—— 这一套只在"块体是合法 Python"时工作。解析不了（比如作者在块里写了
Ren'Py 方言 / 半截代码）就整块不动，并说明原因；**绝不猜**。
"""

from __future__ import annotations

import ast
import textwrap

from storygraph.core.indent import indent_width

__all__ = ['analyze_block', 'lift_summary']

#: 这一行为什么不能搬（给界面 / 日志用的人话）
WHY_NOT_SIMPLE = '不是单行赋值（调用 / 控制流 / 多行语句）'
WHY_NOT_TOP = '块内嵌套的语句（搬出去会丢掉结构）'
WHY_NOT_NAME = '赋值目标不是普通变量（属性 / 下标 / 解包）'
WHY_PRIVATE = '变量名以下划线开头（私有约定）'
WHY_COMMENT = '注释留在块里（块里只剩注释就是语法错）'
WHY_BLANK = '空行'
WHY_NEAR_COMMENT = '块里这条注释需要留一条语句陪着，所以这一条留在块里'
WHY_PARSE = '块体不是合法 Python（有语法错 / 非 Python 内容）'


def _is_blank(text: str) -> bool:
    return not str(text or '').strip()


def _is_comment(text: str) -> bool:
    return str(text or '').lstrip().startswith('#')


def _target_name(node) -> str:
    """简单赋值的变量名；不是"普通变量"时返回空串。"""
    if isinstance(node, ast.Assign):
        targets = list(node.targets or ())
        if len(targets) != 1:
            return ''
        target = targets[0]
    elif isinstance(node, ast.AugAssign):
        target = node.target
    else:
        return ''
    return str(getattr(target, 'id', '') or '') \
        if isinstance(target, ast.Name) else ''


def _classify(node) -> dict:
    """一条顶层语句 → `{kind, target, why}`（`why` 非空 = 不能搬）。"""
    single = (getattr(node, 'end_lineno', node.lineno) == node.lineno)
    if isinstance(node, (ast.Assign, ast.AugAssign)):
        name = _target_name(node)
        if not single:
            return {'kind': '', 'target': '', 'why': WHY_NOT_SIMPLE}
        if not name:
            return {'kind': '', 'target': '', 'why': WHY_NOT_NAME}
        if name.startswith('_'):
            return {'kind': '', 'target': name, 'why': WHY_PRIVATE}
        kind = 'augassign' if isinstance(node, ast.AugAssign) else 'assign'
        return {'kind': kind, 'target': name, 'why': ''}
    return {'kind': '', 'target': '', 'why': WHY_NOT_SIMPLE}


def _runs_of(n: int, lifted: set) -> list:
    """`0..n-1` 里**没被搬走**的连续区间 `[(a, b), …]`（闭区间）。"""
    runs = []
    start = None
    for i in range(n):
        if i in lifted:
            if start is not None:
                runs.append((start, i - 1))
                start = None
        elif start is None:
            start = i
    if start is not None:
        runs.append((start, n - 1))
    return runs


def _run_has_statement(run, stmt_lines: set) -> bool:
    a, b = run
    return any(i in stmt_lines for i in range(a, b + 1))


def _run_has_comment(run, lines) -> bool:
    a, b = run
    return any(_is_comment(lines[i]) for i in range(a, b + 1))


def analyze_block(lines) -> dict:
    """
    一块 `python:` **块体**（`Beat.code` 里那几行，不含 `python:` 头）→ 计划。

    返回：

        {
          'ok': bool,              # 有没有可搬的行
          'why': str,              # ok=False 时的原因（人话）
          'anchor': int,           # 块体基准缩进
          'lift': [行下标, …],      # 建议搬走（0-based，按出现顺序）
          'keep_ranges': [(a,b)],  # 留下继续当块体的区间（0-based 闭区间）
          'candidates': [{line, text, target, kind}, …],
          'skipped': [{line, text, why}, …],   # 想搬但搬不了的行（含注释/嵌套）
        }

    `lines` 里保留原文的相对缩进（`Beat.code` 就是这个形状）；本函数**不修改**
    任何东西，也不碰文件 —— 纯函数，判据直接调它。
    """
    lines = [str(ln) for ln in (lines or ())]
    n = len(lines)
    blank = {'ok': False, 'why': '块里没有语句', 'anchor': 0, 'lift': [],
             'keep_ranges': [], 'candidates': [], 'skipped': []}
    if n == 0 or all(_is_blank(ln) for ln in lines):
        return blank

    anchor = next(indent_width(ln) for ln in lines if not _is_blank(ln))
    # 比基准还浅的行：块体本身就不整齐（手工改过），不猜。
    if any(not _is_blank(ln) and indent_width(ln) < anchor for ln in lines):
        return dict(blank, why='块体缩进不整齐（有比第一行还浅的行）')

    body = textwrap.dedent('\n'.join(lines))
    try:
        tree = ast.parse(body)
    except SyntaxError:
        return dict(blank, why=WHY_PARSE)
    if not tree.body:
        return dict(blank, why='块里没有语句')

    by_line: dict = {}
    for node in tree.body:
        by_line[int(node.lineno) - 1] = node
    stmt_lines = set(by_line)
    # 顶层语句覆盖到的行（多行语句 / 复合语句的体）—— 这些行都不是"简单赋值"
    covered = set()
    for node in tree.body:
        end = int(getattr(node, 'end_lineno', node.lineno) or node.lineno)
        covered.update(range(int(node.lineno) - 1, end))

    # ---- 候选：顶层、单行、简单赋值 ----
    candidates = []
    skipped = []
    for i in range(n):
        if _is_blank(lines[i]):
            continue
        if _is_comment(lines[i]):
            skipped.append({'line': i, 'text': lines[i].strip(),
                            'why': WHY_COMMENT})
            continue
        node = by_line.get(i)
        if node is None:
            skipped.append({'line': i, 'text': lines[i].strip(),
                            'why': WHY_NOT_TOP if i in covered
                            else WHY_NOT_SIMPLE})
            continue
        info = _classify(node)
        if info['why']:
            skipped.append({'line': i, 'text': lines[i].strip(),
                            'why': info['why']})
            continue
        candidates.append({'line': i, 'text': lines[i].strip(),
                           'target': info['target'], 'kind': info['kind']})

    if not candidates:
        return dict(blank,
                    why='没有单行赋值（块里都是调用 / 控制流 / 多行语句）',
                    anchor=anchor, skipped=skipped)

    # ---- 选：先全选，再把"会让注释独自成块"的那些退回去 ----
    lift = {c['line'] for c in candidates}
    dropped: dict = {}
    for _ in range(n + 2):
        changed = False
        for run in _runs_of(n, lift):
            if not _run_has_comment(run, lines):
                continue
            if _run_has_statement(run, stmt_lines):
                continue
            # 这条注释左右都没有语句了 → 把**最近的一条搬动**退回去，
            # 让它重新有一条语句陪着（只退一条：退多了会白丢可搬的行）。
            a, b = run
            near = None
            if a - 1 in lift:
                near = a - 1
            elif b + 1 in lift:
                near = b + 1
            if near is not None:
                lift.discard(near)
                dropped[near] = WHY_NEAR_COMMENT
                changed = True
        if not changed:
            break

    lift_sorted = sorted(lift)
    if not lift_sorted:
        return dict(blank, why='可搬的赋值都得留在块里给注释作伴'
                              '（把注释也一起处理掉之后才能搬）',
                    anchor=anchor, skipped=skipped)

    for i in sorted(dropped):
        text = lines[i].strip() if 0 <= i < n else ''
        skipped.append({'line': i, 'text': text, 'why': dropped[i]})
    skipped.sort(key=lambda r: r['line'])

    keep_ranges = [(a, b) for a, b in _runs_of(n, set(lift))
                   if _run_has_statement((a, b), stmt_lines)]
    return {'ok': True, 'why': '', 'anchor': anchor, 'lift': lift_sorted,
            'keep_ranges': keep_ranges,
            'candidates': [c for c in candidates
                           if c['line'] in set(lift_sorted)],
            'skipped': skipped}


def lift_summary(plan: dict) -> str:
    """一块的计划 → 一句人话（状态栏 / 日志 / 对话框标题用）。"""
    plan = plan or {}
    if not plan.get('ok'):
        return str(plan.get('why') or '没有可搬的语句')
    rows = plan.get('candidates') or []
    names = [f"{r['target']}" for r in rows if r.get('target')]
    return f'{len(rows)} 条可搬到脚本层：' + '、'.join(names[:6]) \
        + (f' 等 {len(names)} 个变量' if len(names) > 6 else '')
