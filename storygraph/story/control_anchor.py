# -*- coding: utf-8 -*-
"""
storygraph.story.control_anchor
===============================
**"我要动的那一行还在不在"** —— 接线 / 断线 / 改接线共用的锚点判定。

## 为什么单独一层（2026-11-08 第十四轮）

`apply_control_route` / `apply_control_unroute` / `apply_control_edit` 这三个
落盘点本来就是"读**现在**这份 → 只改那一行 → 写回去"：别人的改动天生被
保住，因为没有被覆盖的余地。唯一的死结是**行号漂了** —— 别人在锚点上面插了
一行，`lines[idx]` 就对不上，于是拒写；可那一行明明好端端地待在下面。

判据本来就该是"那一行还在不在"，不是"它还在第几行"。

## 三条规矩（第二条是踩出来的，别放宽）

  · 行号对得上 → 用它（老路，跑了两年的那条）；
  · 对不上 → 缩到**它自己那个 label 块**里按内容找，**恰好一处**才用；
  · 块里找不到 / 有好几处一字不差 → 拒写，并说清是哪一种。

第二条为什么必须有：`return` 在这种文件里**每个 `_control` 都有一条**。
全文找"唯一的一处"会把 `ch02_control` 的 `return` 认成 `ch01_control` 的，
于是把接线写到**别的章**头上 —— 静默改错地方，比拒写坏一百倍。
（第一版就是这么写的，`test_merge_commit` 里那条"锚点被改掉"的判据当场抓住。）

## 与 `write_gate.commit_text` 的分工

  · **整份文本**要重算的（删一个区间、补一段包装、插一个分支段）→
    `commit_text` 做行级三路合并；
  · **只动一行**的（接线 / 断线 / 改接线）→ 这里按内容认锚点，然后在
    **最新那份文本**上就地改一行。

后者比前者更保守：它从不重放整份 diff，只碰那一行，位置错了也不碰别的。
"""

from __future__ import annotations

import re

#: `label 名字:` / `label 名字(params):`
LABEL_LINE = re.compile(
    r'^label\s+([^\s:(#]+)\s*(?:\(([^)]*)\))?\s*:')

#: 要动的那一行**已经不在了**（被改掉 / 被删）时说这一句。
GONE = '编排层刚被改过（要动的那一行已经不在了）；重新算一次'


def label_block(lines, idx: int) -> tuple:
    """
    `idx` 所在的 `label` 块 → `(起, 止)`（0 基，止不含）。

    找不到 label 就退回整份（新建文件、还没包 label 的草稿）。
    """
    n = len(lines)
    if n <= 0:
        return 0, 0
    start = max(0, min(int(idx), n - 1))
    head = -1
    for i in range(start, -1, -1):
        if LABEL_LINE.match(str(lines[i] or '')):
            head = i
            break
    if head < 0:
        return 0, n
    for j in range(head + 1, n):
        if LABEL_LINE.match(str(lines[j] or '')):
            return head + 1, j
    return head + 1, n


def locate(lines, old_line: str, line_no) -> tuple:
    """
    那一行在**现在这份**文本里的下标；认不出就返回 `(idx, 理由)`。

    成功时理由为空串（调用方只看理由在不在）。`old_line` 是空串时（计划没给
    锚点，只有插入会这样）不找，直接用原行号（越界才拒）。
    """
    try:
        idx = int(line_no) - 1
    except (TypeError, ValueError):
        idx = -1
    old = str(old_line or '')
    if not old:
        if 0 <= idx <= len(lines):
            return idx, ''
        return idx, '计划里的行号已经越界；重新算一次'
    if 0 <= idx < len(lines) and lines[idx] == old:
        return idx, ''

    if 0 <= idx < len(lines):
        lo, hi = label_block(lines, idx)
        if (lo, hi) != (0, len(lines)):
            in_block = [i for i in range(lo, hi) if lines[i] == old]
            if len(in_block) == 1:
                return in_block[0], ''
            if not in_block:
                return -1, GONE
            return -1, (f'编排层里这个 label 有一模一样的 {len(in_block)} 行，'
                        f'认不出该动哪一处；重新算一次')

    hits = [i for i, line in enumerate(lines) if line == old]
    if len(hits) == 1:
        return hits[0], ''
    if not hits:
        return -1, GONE
    return -1, (f'编排层里有一模一样的 {len(hits)} 行，认不出该动哪一处；'
                f'重新算一次')


__all__ = ['GONE', 'LABEL_LINE', 'label_block', 'locate']
