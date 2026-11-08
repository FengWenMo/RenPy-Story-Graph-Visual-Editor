# -*- coding: utf-8 -*-
"""
`storygraph.app.batch_report`
=============================
批处理计划 → **人话 / 统一 diff**（只读渲染，不写盘、不算计划）。

为什么单独一层（2026-11-08）：`format_plan` 的分支数被"追加"那一段推过了
`tools/debt_report.py` 的 20 条上限（技术债棘轮只许降），而它跟"怎么算
计划"本来就不是一件事 —— 它只读 `BatchPlan` 的字段。拆出来之后
`batch_edit` 继续导出 `format_plan` / `format_diff`（调用方一行都不用改），
但"一条编辑怎么写成两行明细"有了自己的名字。
"""
from __future__ import annotations

import difflib

from storygraph.app.batch_text import splice_edits


def format_diff(plan, context: int = 2) -> str:
    """统一 diff（预演用）；只对有计划改动的文件生成。"""
    out = []
    for fp in plan.files:
        if fp.error or not (fp.insertions or fp.edits):
            continue
        new_text = splice_edits(fp.old_text, fp.insertions, fp.edits)
        out.extend(difflib.unified_diff(
            fp.old_text.split('\n'), new_text.split('\n'),
            fromfile=f'{fp.rel} (before)', tofile=f'{fp.rel} (after)',
            n=max(0, int(context)), lineterm=''))
    return '\n'.join(out) if out else '（没有差异）'


def _insertion_item(ins) -> tuple:
    """一条插入 → `(行号, 排序位, 明细行)`。"""
    arrow = 'before' if ins.before else 'after'
    first = ins.text.split('\n')[0]
    more = '' if '\n' not in ins.text else f'… 共 {len(ins.text_lines)} 行'
    return (ins.line, 0,
            f'    L{ins.line} {arrow}  + {ins.indent}{first}{more}')


def _edit_items(ed) -> list:
    """
    一条 replace / delete → `[(排序位, 明细行), …]`（一般 1–2 行）。

    追加的预览要显示**成品行**（含行尾注释），否则预演里那行是空的 ——
    人看不出到底会补成什么样（2026-11-08）。
    """
    where = (f'L{ed.line}' if ed.span_lines == 1
             else f'L{ed.line}-L{ed.last_line}')
    pad = ' ' * len(where)
    if ed.kind == 'replace':
        verb = 'append ' if ed.append else 'replace'
        out = [(1, f'    {where} {verb}  - {ed.old.strip()}')]
        if ed.append:
            new = ed.new or (ed.old.rstrip() + ed.append)
            out.append((2, f'    {pad} {verb}  + {new.strip()}'))
            return out
        first = ed.text.split('\n')[0]
        more = '' if ed.span_lines == 1 and '\n' not in ed.text \
            else f'… {ed.span_lines} 行'
        out.append((2, f'    {pad} {verb}  + {ed.indent}{first}{more}'))
        return out
    suffix = ('' if ed.span_lines == 1 else f'… 共 {ed.span_lines} 行')
    return [(3, f'    {where} delete   - {ed.old.strip()}{suffix}')]


def format_plan(plan, limit: int = 5) -> str:
    """人话报告：每份文件几处 insert / replace / delete，前几处是什么。"""
    out = [f'批量编辑预演：{len(plan.rules)} 条规则，'
           f'{plan.pending} 处待处理']
    for fp in plan.files:
        if fp.error:
            out.append(f'  {fp.rel}: 跳过（{fp.error}）')
            continue
        n_ins = len(fp.insertions)
        n_rep = sum(1 for e in fp.edits if e.kind == 'replace')
        n_del = sum(1 for e in fp.edits if e.kind == 'delete')
        out.append(f'  {fp.rel}: {fp.total} 处'
                   f'（+{n_ins} ~{n_rep} -{n_del}）')
        if limit > 0:
            items = [_insertion_item(ins) for ins in fp.insertions]
            for ed in fp.edits:
                for order, text in _edit_items(ed):
                    items.append((ed.line, order, text))
            items.sort(key=lambda item: (item[0], item[1]))
            out.extend(item[2] for item in items[:limit])
            if len(items) > limit:
                out.append(f'    … 还有 {len(items) - limit} 行明细')
        if fp.skipped:
            reasons = {}
            for sk in fp.skipped:
                reasons[sk['reason']] = reasons.get(sk['reason'], 0) + 1
            tail = '；'.join(f'{k} {v}' for k, v in reasons.items())
            out.append(f'    （跳过 {len(fp.skipped)}：{tail}）')
    if plan.errors:
        for err in plan.errors:
            out.append(f'  ! {err}')
    if not plan.pending:
        out.append('没有待处理的编辑。')
    else:
        out.append('未写盘；加 --apply 才写。')
    return '\n'.join(out)


__all__ = ['format_diff', 'format_plan']
