# -*- coding: utf-8 -*-
"""
**条件 / 菜单分支写进编排层**（`X_control`），源剧本一个字都不加。

## 为什么分支不能写在章节源文件里（2026-10-24 追加）

章末的条件 / 菜单路由要回答的是"这一章**跑完之后**去哪"。可源文件里
"跑完之后"可能根本不是一个可插入的位置：

    label X:
        menu:
            "走":
                jump Y          ← 选项体里的 jump 不是"章末"
            "留":
                return          ← 这个 return 一执行，X 就结束了

往 `menu:` 后面插一行 `if …:` 要么落进选项体（缩进与分支语义一起坏），
要么写在 `return` 之后（永远不执行）。用户 2026-10-24 的要求是
**源 .rpy 只做最小插入、分支尽量放控制流**：

    label X_control:
        call X                 ← X 里怎么结束都行（menu / if / return）
        # storygraph-edge:12  ← 本模块写的一小段
        if score > 3:
            jump Y_control
        return

`call X` 在 X 的 `return` 之后回到这里，所以"X 以 menu/if 结尾"不再
影响路由。分支段带一个**行内标记注释**（`# storygraph-edge:<id>`），
改/删都按这个标记找，不按行号 —— 行号会漂，标记不会。

菜单项更进一步：同一章源的所有菜单项住在一个 `# storygraph-menu:<src>`
组里。各写一个 `menu:` 的话，第一个选项永远先执行，第二个菜单永远轮不到
（图上有两条线、游戏里只有一个选项）。

## 只打一小段，不整份重生成

编排层文件可能是作者手写的（`chapter_controller.rpy`）。整份重生成会
抹掉手写内容，而 `write_generation` 也会拒写手写文件。所以这里只做
"在 `X_control` 的最后一条路由之前插一小段" / "按标记换一小段" / "按标记
删一小段"，其余字节原样保留。

## 不做什么

- 不自动建 `X_control` 包装（那是 `ControlTakeover` 的活：补了没人进来的
  包装只是死代码）；
- 不往**源文件**插分支（见上）；
- 判不出安全插入点（`X_control` 以动态跳转收尾等）→ 明说，不猜。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.story.control_route import (CONTROL_SUFFIX, _block_for,
                                            _blocks, _indent, _route_of,
                                            _top_lines, control_label,
                                            find_control_file, plan_control_init)
from storygraph.core.textlines import split_lines
from storygraph.story.source_style import SourceNotUtf8

#: 分支段的稳定标记（行内注释）。改 / 删只认它，不认行号。
MARKER_PREFIX = '# storygraph-edge:'

#: 一个 **menu 组**的标记：同一章源的所有菜单项必须住在**同一个** menu 里。
MENU_GROUP_PREFIX = '# storygraph-menu:'


def menu_group_marker(src_label: str) -> str:
    return f'{MENU_GROUP_PREFIX}{src_label}'


def branch_marker(edge_id) -> str:
    """一条分支的标记注释（不带缩进）。"""
    return f'{MARKER_PREFIX}{int(edge_id)}'


def _marker_of(line: str):
    """`'    # storygraph-edge:12'` → `12`；不是标记行 → `None`。"""
    text = str(line or '').strip()
    if not text.startswith(MARKER_PREFIX):
        return None
    tail = text[len(MARKER_PREFIX):].strip()
    try:
        return int(tail)
    except (TypeError, ValueError):
        return None


def find_branch_range(text: str, edge_id, indent: str = None):
    """
    标记段在文件里的位置：`(start, end, indent)`（0 基，`end` 不含）。

    段 = 标记行 + 紧随其后的**一个块**（`if …:` / `menu:`）；块体按缩进
    判定到 dedent 或文件尾为止。找不到返回 `None`。
    """
    lines = split_lines(text)
    want = int(edge_id)
    for i, line in enumerate(lines):
        if _marker_of(line) != want:
            continue
        head = _indent(line)
        j = i + 1
        # 跳过空行 / 注释（标记和块头之间允许有空行）
        while j < len(lines) and (not lines[j].strip()
                                  or lines[j].lstrip().startswith('#')):
            j += 1
        if j >= len(lines):
            return i, i + 1, str(indent if indent is not None
                                 else lines[i][:head])
        body_indent = _indent(lines[j])
        k = j + 1
        while k < len(lines):
            ln = lines[k]
            if not ln.strip():
                # 空行：块可能在这里结束；继续看到底是空行还是块体。
                k += 1
                continue
            if _indent(ln) <= body_indent:
                break
            k += 1
        # 把块尾的空行留给下一段（不吞别人的分隔空行）
        while k > j + 1 and not lines[k - 1].strip():
            k -= 1
        return i, k, str(indent if indent is not None
                         else lines[i][:head])
    return None


def _block_end(lines: list, head_index: int) -> int:
    """块头所在行的**块尾**（0 基，不含）：缩进回到 <= 块头缩进为止。"""
    base = _indent(lines[head_index])
    i = head_index + 1
    last_body = head_index
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        if _indent(ln) <= base:
            break
        last_body = i
        i += 1
    return last_body + 1


def render_branch(edge_id, etype: str, target: str, condition: str = '',
                  option_text: str = '', indent: str = '    ') -> list:
    """
    一段分支的行（**带标记**）。`target` 是已经算好的最终 label。

    · 只填条件 → `if …:` + 一行路由；
    · 填了菜单项 → `menu:` + 一个选项（条件写在选项行上，Ren'Py 的
      `"文字" if 条件:` 语义，不是外层 if）；
    · 两样都填 → 同上（选项带条件）。
    """
    from storygraph.story.beat_render import quote_string
    etype = str(etype or 'jump')
    target = str(target or '')
    cond = str(condition or '').strip()
    opt = str(option_text or '').strip()
    out = [f'{indent}{branch_marker(edge_id)}']
    if opt:
        out.append(f'{indent}menu:')
        opt_indent = indent + '    '
        tail = f' if {cond}' if cond else ''
        out.append(f'{opt_indent}{quote_string(opt)}{tail}:')
        out.append(f'{opt_indent}    {etype} {target}')
        return out
    if cond:
        out.append(f'{indent}if {cond}:')
        out.append(f'{indent}    {etype} {target}')
        return out
    # 没有条件也没有菜单项 → 普通路由，不该走这条分支路。
    out.append(f'{indent}{etype} {target}')
    return out


def _target_label(dst_label: str, etype: str) -> str:
    """jump 去 `B_control`（章级编排）；call 去 `B`（返回调用点）。"""
    dst = str(dst_label or '').strip()
    return control_label(dst) if str(etype or '') == 'jump' else dst


@dataclass
class MenuOption:
    """menu 组里的一个选项（来自某条边的标记段）。"""

    edge_id: int
    text: str
    condition: str = ''
    etype: str = 'jump'
    target: str = ''

    def to_dict(self) -> dict:
        return {'edge_id': self.edge_id, 'text': self.text,
                'condition': self.condition, 'etype': self.etype,
                'target': self.target}


#: `"选项文字" [if 条件]:`（Ren'Py 的字符串转义只有一份，解析也按它来）。
_RE_OPTION = re.compile(r'^(\s*)"((?:\\.|[^"\\])*)"(?:\s+if\s+(.+?))?:\s*$')


def find_menu_group(text: str, src_label: str):
    """找到 `# storygraph-menu:<src>` 这个组的范围：`(start, end, indent)`。"""
    lines = split_lines(text)
    want = menu_group_marker(src_label)
    for i, line in enumerate(lines):
        if str(line).strip() != want:
            continue
        mark_indent = _indent(line)
        # 组标记后面紧跟 `menu:`（同一缩进）。菜单体从 `menu:` 下一行算起；
        # 不能从标记的下一行就算 `indent <= base` —— 那样范围只剩标记一行，
        # 第二次加选项会另起一个 menu（实测：两个 menu 并排）。
        j0 = i + 1
        while j0 < len(lines) and not lines[j0].strip():
            j0 += 1
        base = mark_indent
        if j0 < len(lines) and lines[j0].strip().startswith('menu:') \
                and _indent(lines[j0]) <= mark_indent:
            base = _indent(lines[j0])
            j = j0 + 1
        else:
            j = i + 1
        while j < len(lines):
            if not lines[j].strip():
                j += 1
                continue
            if _indent(lines[j]) <= base:
                break
            j += 1
        while j > i + 1 and not lines[j - 1].strip():
            j -= 1
        return i, j, str(line)[:mark_indent]
    return None


def parse_menu_group(text: str, src_label: str) -> list:
    """
    把 menu 组解析成 `[MenuOption]`。

    只认**带 `# storygraph-edge:<id>` 标记**的选项；没有标记的手写选项
    跳过（不能替人改手写内容，但渲染时也不会删它 —— 见 `plan_menu_branch`
    的"只替换本工具写的那一段"原则）。
    """
    found = find_menu_group(text, src_label)
    if found is None:
        return []
    start, end, _indent_text = found
    lines = split_lines(text)
    out = []
    edge_id = None
    i = start + 1
    while i < end:
        mark = _marker_of(lines[i])
        if mark is not None:
            edge_id = mark
            i += 1
            continue
        m = _RE_OPTION.match(lines[i])
        if m and edge_id is not None:
            text_raw = m.group(2)
            try:
                import ast
                opt_text = str(ast.literal_eval(f'"{text_raw}"'))
            except (SyntaxError, ValueError):
                opt_text = text_raw
            cond = str(m.group(3) or '').strip()
            body_indent = _indent(lines[i])
            etype, target = 'jump', ''
            k = i + 1
            while k < end:
                if not lines[k].strip():
                    k += 1
                    continue
                if _indent(lines[k]) <= body_indent:
                    break
                m2 = re.match(r'^\s*(jump|call)\s+(\S+)\s*$', lines[k])
                if m2:
                    etype, target = m2.group(1), m2.group(2)
                    break
                k += 1
            out.append(MenuOption(edge_id, opt_text, cond, etype, target))
            edge_id = None
            i += 1
            continue
        i += 1
    return out


def render_menu_group(src_label: str, options, indent: str = '    ') -> list:
    """渲染一整个 menu 组（组标记 + menu + 每个选项的标记与 router）。"""
    from storygraph.story.beat_render import quote_string
    opts = list(options or ())
    if not opts:
        return []
    out = [f'{indent}{menu_group_marker(src_label)}', f'{indent}menu:']
    opt_indent = indent + '    '
    for opt in opts:
        out.append(f'{opt_indent}{branch_marker(opt.edge_id)}')
        cond = str(getattr(opt, 'condition', '') or '').strip()
        tail = f' if {cond}' if cond else ''
        out.append(f'{opt_indent}{quote_string(opt.text)}{tail}:')
        out.append(f'{opt_indent}    {opt.etype} {opt.target}')
    return out


def _source_tail_jump(project_root, label: str):
    """
    这一章的**最后一条顶层语句**是不是一个 `jump`？返回 `(etype, target)`。

    为什么这个判据决定分支能不能写：`X_control` 的形状是
    `call X` + 分支。X 末尾若是一条 `jump Y`，`call X` **永远不返回**，
    分支段一次都不会执行 —— 那正是"图上接好了、跑起来没反应"的假完成。
    `jump X_control` 是例外（它就是要离开这一章去路由）。
    """
    want = str(label or '').strip()
    if not want:
        return None
    try:
        from storygraph.parsing.discover import discover_scripts
        from storygraph.story.control_route import (_block_for, _indent,
                                                    _read, _route_of,
                                                    _top_lines)
        for path in discover_scripts(Path(str(project_root))):
            try:
                text, _style = _read(path)
            except OSError:
                continue
            if _block_for(text, want) is None:
                continue
            lines = split_lines(text)
            block = _block_for(text, want)
            tops = _top_lines(lines, block)
            if not tops:
                return None
            last_i, last_line = tops[-1]
            raw = str(last_line).strip()
            route = _route_of(raw)
            if route and route[0] in ('jump', 'dynamic') \
                    and raw.startswith('jump'):
                return route
            return None
    except Exception as e:                          # noqa: BLE001 - 判不出就不拦
        from storygraph.core.safeguard import swallow
        swallow('story.control_branch._source_tail_jump', e)
    return None


@dataclass
class ControlBranchPlan:
    """一条条件 / 菜单分支**会写成什么** —— 纯计算，不碰磁盘。"""

    ok: bool = False
    #: `insert` / `replace` / `remove` / `exists` / `blocked`
    kind: str = 'blocked'
    path: str = ''
    src_label: str = ''
    dst_label: str = ''
    control_label: str = ''
    edge_id: int = 0
    etype: str = 'jump'
    condition: str = ''
    option_text: str = ''
    target_label: str = ''
    line_no: int = 0
    old_lines: list = field(default_factory=list)
    new_lines: list = field(default_factory=list)
    old_text: str = ''
    new_text: str = ''
    reason: str = ''
    blocking: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    #: 目标章缺包装时的补计划（和 `plan_control_route` 同一套）
    init: object = None
    #: 源章缺包装时只作解释（补了没人进来就是死代码）
    src_init: object = None

    def to_dict(self) -> dict:
        return {
            'ok': self.ok, 'kind': self.kind, 'path': self.path,
            'src_label': self.src_label, 'dst_label': self.dst_label,
            'control_label': self.control_label, 'edge_id': self.edge_id,
            'etype': self.etype, 'condition': self.condition,
            'option_text': self.option_text, 'target_label': self.target_label,
            'line_no': self.line_no, 'old_lines': list(self.old_lines),
            'new_lines': list(self.new_lines), 'reason': self.reason,
            'blocking': list(self.blocking), 'notes': list(self.notes),
            'init': self.init.to_dict() if self.init is not None else None,
            'src_init': (self.src_init.to_dict()
                         if self.src_init is not None else None),
        }


def _insert_before(lines: list, tops: list, block) -> int:
    """
    分支段插在哪一行之前（1 基行号）。

    规则与 `plan_control_route` 同源，但**允许块尾**：

      · 最后一条是 `return` / 路由 → 插在它**之前**（条件不成立就走原路）；
      · 最后一条是普通语句 → 插在它**之后**；
      · 最后一条是块头（`menu:` / `if …:`）→ 插在**整块之后**；
      · 空块 → header 之后。
    """
    if not tops:
        return block['start'] + 2
    last_i, last_line = tops[-1]
    raw = str(last_line).strip()
    if raw.startswith(('return', 'jump ', 'call ')):
        return last_i + 1
    if raw.endswith(':') and not raw.startswith('#'):
        return _block_end(lines, last_i) + 1
    return last_i + 2


def _plan_menu_branch(plan: ControlBranchPlan, text: str, block) -> ControlBranchPlan:
    """
    菜单项：同一章的**所有菜单项住在一个 menu 组里**。

    为什么必须分组：两条"菜单项"边各写一个 `menu:`，第一个选项永远先执行
    （无条件），第二个菜单永远轮不到 —— 图上有两条线，游戏里只有一个选项。
    组标记 `# storygraph-menu:<src>`，每个选项仍有自己的
    `# storygraph-edge:<id>`，增 / 改 / 删都只动那一个选项。
    """
    lines = split_lines(text)
    src = plan.src_label
    target = plan.target_label
    replace_dst = str(getattr(plan, 'replace_dst_label', '') or '')
    replace_etype = str(getattr(plan, 'replace_etype', '') or 'jump')
    new_opt = MenuOption(plan.edge_id, plan.option_text, plan.condition,
                         plan.etype, target)
    group = find_menu_group(text, src)
    per_edge = find_branch_range(text, plan.edge_id)
    if group is not None:
        start, end, indent = group
        opts = parse_menu_group(text, src)
        at_opt = next((i for i, o in enumerate(opts)
                       if o.edge_id == plan.edge_id), None)
        if at_opt is None:
            opts.append(new_opt)
        else:
            opts[at_opt] = new_opt
        new_lines = render_menu_group(src, opts, indent=indent)
        out = lines[:start] + new_lines + lines[end:]
        group_after = start + len(new_lines)
        kind = 'replace'
        plan.reason = (f'改写 `{plan.control_label}` 里菜单组的这一项'
                       f'（标记 {branch_marker(plan.edge_id)}）')
    elif replace_dst:
        # 原来是普通路由（没有条件 / 菜单项）：换成 menu 组时旧行必须消失，
        # 否则旧路由无条件先执行，菜单永远轮不到。
        old_target = _target_label(replace_dst, replace_etype)
        tops = _top_lines(lines, block)
        matches = [i for i, ln in tops
                   if _route_of(ln.strip()) == (replace_etype, old_target)]
        if len(matches) != 1:
            plan.reason = (f'`{plan.control_label}` 里'
                           + (f'有 {len(matches)} 条' if matches else '找不到')
                           + f' `{replace_etype} {old_target}`'
                             f' —— 不知道换哪一条')
            plan.blocking.append(plan.reason)
            return plan
        idx = matches[0]
        opts = [new_opt]
        new_lines = render_menu_group(src, [new_opt])
        out = lines[:idx] + new_lines + lines[idx + 1:]
        group_after = idx + len(new_lines)
        kind = 'replace'
        plan.reason = (f'把 `{plan.control_label}` 里的普通路由换成菜单组'
                       f'（标记 {branch_marker(plan.edge_id)}）')
    elif per_edge is not None:
        start, end, indent = per_edge
        # 只转换"老版本写的单菜单段"；`if` 段转菜单会改变交互（玩家要点一下），
        # 不替人做这个决定。
        code = next((lines[i] for i in range(start + 1, end)
                     if lines[i].strip() and not lines[i].lstrip().startswith('#')),
                    '')
        if not code.strip().startswith('menu:'):
            plan.reason = ('这条线现在是 `if` 分支，改成菜单会多出一次玩家选择；'
                           '先删掉这条线，再用「添加连线」建菜单项')
            plan.blocking.append(plan.reason)
            return plan
        opts = [new_opt]
        new_lines = render_menu_group(src, [new_opt], indent=indent)
        out = lines[:start] + new_lines + lines[end:]
        group_after = start + len(new_lines)
        kind = 'replace'
        plan.reason = (f'把单菜单段并进菜单组'
                       f'（标记 {branch_marker(plan.edge_id)}）')
    else:
        at = _insert_before(lines, _top_lines(lines, block), block)
        at = max(block['start'] + 2, min(at, block['end'] + 1))
        opts = [new_opt]
        new_lines = render_menu_group(src, [new_opt])
        out = lines[:at - 1] + new_lines + lines[at - 1:]
        group_after = at - 1 + len(new_lines)
        kind = 'insert'
        plan.reason = (f'在 `{plan.control_label}` 的第 {at} 行前建菜单组'
                       f'（标记 {branch_marker(plan.edge_id)}）')
    # 所有选项都可能不出现 → menu 被整块跳过。只有在**组后面一条顶层语句
    # 都没有**时才补 return（后面已有 return / 路由就让它接住）。
    block_end_out = block['end'] + (len(out) - len(lines))
    following = [ln for ln in out[group_after:block_end_out]
                 if ln.strip() and not ln.lstrip().startswith('#')]
    if all(str(o.condition or '').strip() for o in opts) and not following:
        new_lines.append('    return')
        out.insert(group_after, '    return')
    plan.kind = kind
    plan.old_lines = lines[per_edge[0]:per_edge[1]] if per_edge else []
    plan.new_lines = new_lines
    return _finish_branch_plan(plan, text, out)


def plan_control_branch(project_root, src_label, dst_label, etype='jump',
                        condition='', option_text='', edge_id=0,
                        from_clause='', replace_dst_label='',
                        replace_etype='') -> ControlBranchPlan:
    """
    算"这条分支写进 `X_control` 的哪儿、写成什么"。**不碰磁盘。**

    `replace_dst_label` / `replace_etype`：这条边原来是一条**普通路由**
    （没有条件 / 菜单项，所以没有标记段）。给它加条件时要把原来那一行
    **换成**分支段，不能"加一段、旧路由还留着" —— 否则旧路由无条件先执行，
    新分支永远轮不到。
    """
    src = str(src_label or '').strip()
    dst = str(dst_label or '').strip()
    etype = str(etype or 'jump').strip() or 'jump'
    cond = str(condition or '').strip()
    opt = str(option_text or '').strip()
    plan = ControlBranchPlan(src_label=src, dst_label=dst, etype=etype,
                             condition=cond, option_text=opt,
                             edge_id=int(edge_id or 0))
    #: 这条边原来是一条普通路由时，要换掉的那一行（`if`/菜单两种汇合点共用）
    plan.replace_dst_label = str(replace_dst_label or '')
    plan.replace_etype = str(replace_etype or '')
    if _branch_input_blocked(plan, src, dst, etype, cond, opt):
        return plan
    loaded = _read_control_layer(plan, project_root, src)
    if loaded is None:
        return plan
    text, block = loaded
    if _control_layer_not_ready(plan, project_root, text, dst, etype):
        return plan
    if opt:
        return _plan_menu_branch(plan, text, block)
    if _is_menu_option_edge(text, src, plan.edge_id):
        plan.reason = ('这是菜单组里的一项：清空「菜单项」会留下空菜单；'
                       '要改成条件分支请删掉这条线重接')
        plan.blocking.append(plan.reason)
        return plan
    out = _plan_condition_branch(plan, text, split_lines(text), block,
                                 cond, opt, replace_dst_label, replace_etype)
    if out is None:
        return plan
    return _finish_branch_plan(plan, text, out)


def _branch_input_blocked(plan, src, dst, etype, cond, opt) -> bool:
    """
    六条前置判据：任一条不成立就把理由写进 `plan.blocking` 并返回 True。

    都是"还没读文件就能断定写不进去"的情况 —— 早退，不碰磁盘。
    """
    problems = (
        (not src or not dst, '起点或目标没有 label 名'),
        (src == dst, '起点和终点是同一章'),
        (etype not in ('jump', 'call'), '类型只能是 jump / call'),
        (not (cond or opt),
         '条件 / 菜单项都为空：这是普通路由，走 plan_control_route'),
        ('\n' in cond or '\r' in cond or '\n' in opt or '\r' in opt,
         '条件 / 菜单项里不能换行（会把一行语法拆坏）'),
        (cond.rstrip().endswith(':'),
         '条件不能以冒号结尾（写出来会变成 `if 条件::`）'),
    )
    for hit, reason in problems:
        if hit:
            plan.reason = reason
            plan.blocking.append(reason)
            return True
    return False


def _read_control_layer(plan, project_root, src):
    """
    读编排层 + 定位这一章的 `X_control` 块，返回 `(文本, 块)`。

    ⚠ 读不到就返回 `None`（**不留半份文本**）：两种情形都已在 `plan.reason`
    里写明理由，调用方直接 `return plan`。
    """
    path = find_control_file(project_root, src)
    if path is None:
        plan.reason = ('项目里没有编排层（没有任何 `*_control` label）。'
                       '分支要先接管这一章的入口，才能写在 `X_control` 里')
        plan.blocking.append(plan.reason)
        return None
    plan.path = str(path)
    from storygraph.story.source_style import read_text
    text, _style = read_text(path)
    plan.control_label = control_label(src)
    block = _block_for(text, plan.control_label)
    if block is None:
        # 源章还没有 `_control`：分支这条路走"接管进入点"（`_ensure_control_entry`：
        # 补包装 + 把 `jump X` 改接 `jump X_control`），不在这里自作主张补一行。
        plan.src_init = plan_control_init(project_root, src, role='source')
        plan.reason = (f'编排层里没有 `{plan.control_label}` —— 这一章现在'
                       f'不是从编排层进入的。' + plan.src_init.reason)
        plan.blocking.append(plan.reason)
        return None
    return text, block


def _control_layer_not_ready(plan, project_root, text, dst, etype) -> bool:
    """
    读到了编排层，还要两道判据：源章怎么收尾、目标章的包装在不在。

    不通过就把理由写进 `plan` 并返回 True。
    """
    plan.target_label = _target_label(dst, etype)
    tail = _source_tail_jump(project_root, plan.src_label)
    if tail is not None:
        kind, target = tail
        if not (kind == 'jump' and target == plan.control_label):
            plan.reason = (
                f'`{plan.src_label}` 的最后一条是 `jump {target or "…"}` —— '
                f'`{plan.control_label}` 里的 `call {plan.src_label}` 永远不会返回，'
                f'分支写进去也不会执行。先把那一章的收尾改成 return，'
                f'或把它接到 `{plan.control_label}`，再来加条件 / 菜单分支')
            plan.blocking.append(plan.reason)
            return True
    if etype == 'jump' and _block_for(text, plan.target_label) is None:
        plan.init = plan_control_init(project_root, dst, role='target')
        if plan.init.ok:
            plan.reason = (f'还缺 `{plan.target_label}`（目标章包装）；'
                           f'可以先补上再写分支（{plan.init.reason}）')
        else:
            plan.reason = (f'没有 `{plan.target_label}`，而且补不了：'
                           f'{plan.init.reason}')
        plan.blocking.append(plan.reason)
        return True
    return False


def _is_menu_option_edge(text, src, edge_id) -> bool:
    """这条边现在是菜单组里的一个选项吗（清空它会在菜单里留个空壳）。"""
    return bool(find_menu_group(text, src)) and any(
        o.edge_id == edge_id for o in parse_menu_group(text, src))


def _plan_condition_branch(plan, text, lines, block, cond, opt,
                           replace_dst_label, replace_etype):
    """
    条件分支：有标记段就按标记换，没有就往插入点写。

    返回**新的行表**；写不进去时返回 `None`（理由已在 `plan` 里，包括
    "已经和输入一致"那种 `ok=True` 的情形 —— 那时 `plan.ok` 已经置好）。
    """
    found = find_branch_range(text, plan.edge_id)
    if found is not None:
        return _rewrite_branch(plan, text, lines, found, cond, opt)
    return _insert_branch(plan, text, lines, block, cond, opt,
                          replace_dst_label, replace_etype)


def _rewrite_branch(plan, text, lines, found, cond, opt):
    """按标记改写已有的分支段；内容没变就标 `exists` 并返回 `None`。"""
    start, end, indent = found
    plan.kind = 'replace'
    plan.line_no = start + 1
    plan.old_lines = lines[start:end]
    # 编辑时沿用原来的缩进（不把作者的两空格风格改成四空格）
    plan.new_lines = render_branch(plan.edge_id, plan.etype,
                                   plan.target_label, cond, opt,
                                   indent=indent)
    if plan.old_lines == plan.new_lines:
        plan.kind = 'exists'
        plan.ok = True
        plan.old_text = text
        plan.new_text = text
        plan.reason = '这一段已经和输入一致，无需再写'
        return None
    out = lines[:start] + plan.new_lines + lines[end:]
    plan.reason = (f'改写 `{plan.control_label}` 里的分支段'
                   f'（第 {start + 1} 行，标记 {branch_marker(plan.edge_id)}）')
    return out


def _insert_branch(plan, text, lines, block, cond, opt,
                   replace_dst_label, replace_etype):
    """新写一段分支（原来那条线是普通路由时先把它换掉）。"""
    tops = _top_lines(lines, block)
    at = _insert_before(lines, tops, block)
    at = max(block['start'] + 2, min(at, block['end'] + 1))
    plan.kind = 'insert'
    plan.line_no = at
    plan.new_lines = render_branch(plan.edge_id, plan.etype,
                                   plan.target_label, cond, opt)
    if replace_dst_label:
        return _replace_old_route(plan, lines, tops, replace_dst_label,
                                  replace_etype)
    # 如果插在**最后一个块之后**（没有原路由可落），补一句 return：
    # 条件不成立时这一章应该结束，而不是掉进下一个 label。
    fallback = (not any(str(lines[i]).strip().startswith(
        ('return', 'jump ', 'call ')) for i in range(at - 1, block['end'])))
    anchor_line = (str(lines[at - 1]) if 0 <= at - 1 < len(lines) else '')
    if fallback and not anchor_line.strip().startswith('return'):
        plan.new_lines.append('    return')
    out = lines[:at - 1] + plan.new_lines + lines[at - 1:]
    plan.reason = (f'在 `{plan.control_label}` 的第 {at} 行前写分支段'
                   f'（标记 {branch_marker(plan.edge_id)}）')
    return out


def _replace_old_route(plan, lines, tops, replace_dst_label, replace_etype):
    """
    这条边原来是普通路由：把那一行换成刚写好的分支段（返回新行表）。

    原来那一行是无条件出口；换成条件分支后，条件不成立时必须自己结束，
    不能掉进下一个 label（同 `plan_control_route` 的 call 收尾判据）。

    ⚠ 找不到 / 找到多条都返回 `None` —— 不知道换哪一条，**不猜**。
    """
    old_etype = str(replace_etype or 'jump')
    old_target = _target_label(replace_dst_label, old_etype)
    matches = [i for i, ln in tops
               if _route_of(ln.strip()) == (old_etype, old_target)]
    if len(matches) != 1:
        plan.reason = (
            f'`{plan.control_label}` 里'
            + (f'有 {len(matches)} 条' if matches else '找不到')
            + f' `{old_etype} {old_target}` —— 不知道换哪一条，'
              f'先打开编排层核对')
        plan.blocking.append(plan.reason)
        return None
    idx = matches[0]
    plan.line_no = idx + 1
    old_route = lines[idx]
    if idx == tops[-1][0] and not str(old_route).strip().startswith('return'):
        plan.new_lines.append('    return')
    out = lines[:idx] + plan.new_lines + lines[idx + 1:]
    plan.reason = (f'把 `{plan.control_label}` 里的 '
                   f'{str(old_route).strip()} 换成条件/菜单分支'
                   f'（标记 {branch_marker(plan.edge_id)}）')
    return out


def _finish_branch_plan(plan: ControlBranchPlan, text: str,
                        out: list) -> ControlBranchPlan:
    """
    收尾：把新行表写成 `plan.new_text`（原文件的换行风格照抄）。

    `_plan_menu_branch` 与条件分支汇合点共用 —— 两处各写一遍，迟早出现
    "菜单分支能写回、条件分支忘了换行符"这种分叉。
    """
    plan.old_text = text
    plan.new_text = '\n'.join(out)
    if text.endswith('\n'):
        plan.new_text += '\n'
    plan.ok = True
    plan.notes.append('只改编排层这一小段；章节剧本一个字不动')
    return plan


def plan_control_branch_remove(project_root, src_label, edge_id,
                               ) -> ControlBranchPlan:
    """删掉一条带标记的分支段（**只删这一小段**）。"""
    plan = ControlBranchPlan(src_label=str(src_label or ''),
                             edge_id=int(edge_id or 0), kind='remove')
    path = find_control_file(project_root, src_label)
    if path is None:
        plan.reason = '项目里没有编排层'
        plan.blocking.append(plan.reason)
        return plan
    plan.path = str(path)
    from storygraph.story.source_style import read_text
    text, _style = read_text(path)
    group = find_menu_group(text, str(src_label or ''))
    if group is not None:
        opts = parse_menu_group(text, str(src_label or ''))
        kept = [o for o in opts if o.edge_id != plan.edge_id]
        if len(kept) != len(opts):
            start, end, indent = group
            lines = split_lines(text)
            plan.old_lines = lines[start:end]
            if kept:
                plan.new_lines = render_menu_group(
                    str(src_label or ''), kept, indent=indent)
                plan.new_text = '\n'.join(
                    lines[:start] + plan.new_lines + lines[end:])
            else:
                plan.new_lines = []
                plan.new_text = '\n'.join(lines[:start] + lines[end:])
            if text.endswith('\n'):
                plan.new_text += '\n'
            plan.old_text = text
            plan.line_no = start + 1
            plan.kind = 'remove' if not kept else 'replace'
            plan.ok = True
            plan.reason = (f'从菜单组里删掉一项'
                           f'（第 {start + 1} 行，标记 '
                           f'{branch_marker(plan.edge_id)}）')
            return plan
    found = find_branch_range(text, plan.edge_id)
    if found is None:
        plan.reason = '编排层里已经找不到这条分支的标记段'
        plan.blocking.append(plan.reason)
        return plan
    start, end, _indent_text = found
    lines = split_lines(text)
    plan.old_lines = lines[start:end]
    plan.new_lines = []
    plan.line_no = start + 1
    out = lines[:start] + lines[end:]
    plan.old_text = text
    plan.new_text = '\n'.join(out)
    if text.endswith('\n'):
        plan.new_text += '\n'
    plan.kind = 'remove'
    plan.ok = True
    plan.reason = (f'删掉 `{src_label}_control` 里的分支段'
                   f'（第 {start + 1} 行，标记 {branch_marker(plan.edge_id)}）')
    return plan


def apply_control_branch(plan) -> dict:
    """
    把计划写进编排层（原子替换）；计划之后文件又被改过就**拒绝写**。

    返回 `{ok, old_text, new_text, path, error}`。
    """
    if not isinstance(plan, ControlBranchPlan) or not plan.ok:
        return {'ok': False, 'old_text': '', 'new_text': '',
                'path': '', 'error': '计划无效'}
    if plan.kind == 'exists':
        return {'ok': True, 'old_text': plan.old_text,
                'new_text': plan.new_text, 'path': plan.path, 'error': ''}
    path = Path(plan.path)
    from storygraph.story.source_style import read_text
    text, style = read_text(path)
    if text != plan.old_text:
        return {'ok': False, 'old_text': text, 'new_text': text,
                'path': str(path),
                'error': '编排层在计划之后又被改过；为了不覆盖新改动，'
                         '这次没有写'}
    try:
        from storygraph.story.paths import write_text_atomic
        write_text_atomic(path, style.apply(plan.new_text, source=path))
    except SourceNotUtf8 as exc:
        # 非 UTF-8：读的时候坏字节已变成 U+FFFD，写回去就是改坏它（P0-B）
        return {'ok': False, 'old_text': text, 'new_text': text,
                'path': str(path), 'error': str(exc)}
    except OSError as exc:
        return {'ok': False, 'old_text': text, 'new_text': text,
                'path': str(path), 'error': f'写不进编排层：{exc}'}
    return {'ok': True, 'old_text': plan.old_text,
            'new_text': plan.new_text, 'path': str(path), 'error': ''}


__all__ = ['MARKER_PREFIX', 'ControlBranchPlan', 'apply_control_branch',
           'branch_marker', 'find_branch_range', 'plan_control_branch',
           'plan_control_branch_remove', 'render_branch']
