# -*- coding: utf-8 -*-
"""
多行语句的续行缩进换算 —— `renpy_parser` 与 `renpy_parser_stmts` **共用的叶子**。

为什么单独一个模块（2026-11-01，技术债第 5 刀）：

  `renpy_parser_stmts` 要在"给 AST 节点补正文"时把多行代码按行基准左移，
  而那个函数原来住在 `renpy_parser.py` 里；`renpy_parser.py` 又**顶层**导入
  `renpy_parser_stmts`（拿 `StatementScreens` 的 mixin）—— 于是两个模块
  隔着一次函数内 import 互相够到，成了导入环里最短的一对（2 个模块）。

  把这段**纯字符串换算**搬到叶子模块，两条边就都指向叶子：
  `renpy_parser` → `continuation` ← `renpy_parser_stmts`，环自然消失。
  口径一个字没改（`_shift_continuations` 的名字在两个老模块里仍可用，
  下面 `renpy_parser` / `renpy_parser_stmts` 各自 re-export 了一份）。

只依赖标准库：`str` / `len` / `lstrip` —— 没有项目内依赖，谁都能导。
"""
from __future__ import annotations


def shift_continuations(text: str, base: int) -> str:
    """
    多行文本 → **首行去缩进、续行相对 `base` 左移**（空行留空）。

    ⚠ 分工写清楚，否则会双重计算（2026-10-07 实测踩到）：
      · **首行**的缩进由**调用方的 `rel`** 负责（`rel = node.indent - base`），
        所以这里只 `lstrip()` 首行 —— 不在这里减 base，减了就加两次；
      · **续行**才减 `base`。`_flatten` 会把括号未闭合的多行语句并成**一条
        逻辑行**（`text` 含 `\n`），而 `lstrip()` 对多行字符串**只作用于
        第一行** → 续行的**绝对**缩进原样留下，被当成"更深一层"；
        渲染时整块再加一次基准，于是**每导出一次缩进就长一截**：

            原文 12 → 第一轮 20 → 第二轮 28 → 第三轮 36 …（实测 mini_game1.rpy）

        那不是"排版漂移"那么轻：文件**永远在变** → 增量对位每轮都判
        "文本变了" → 整份退回全量。
        判据：`tests/test_roundtrip_idempotent.py`、`tools/audit_idempotence.py`。
    """
    parts = str(text or '').split('\n')
    out = [parts[0].lstrip()]
    for ln in parts[1:]:
        if not ln.strip():
            out.append('')
            continue
        ind = len(ln) - len(ln.lstrip())
        out.append(' ' * max(0, ind - base) + ln.lstrip())
    return '\n'.join(out)


__all__ = ['shift_continuations']
