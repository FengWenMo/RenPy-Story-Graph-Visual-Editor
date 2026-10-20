"""
storygraph.core.nest_path
=========================
**结构位（块路径）的 DSL** —— 定义只有这一处：解析器写它、渲染器与门禁读它。

    ``                 label 顶层
    ``if:1``           第 1 个 ``if`` 块里
    ``if:1>menu:2>A>if:3``
                       第 1 个 if → 第 2 个菜单 → **第 A 个选项体** → 第 3 个 if

片段形如 ``<块类型>:<同类型的文档序>``（文档序在**整个 label 内**数，
与按行扫出来的顺序一致）；选项体用**位置键**（``A`` / ``B`` …）。

为什么用位置键而不是选项文案：文案改一个字，"结构"就变了 ——
对拍会天天假报，而假阳性比漏报更贵。位置是稳定的，渲染层数出来的也是位置。

为什么放在 ``core``：``parsing`` 拼它、``story`` 解它，而**跨包 import 私有名
是被禁止的**（``tests/test_package_layering.py`` 守着）。所以 DSL 的函数
必须是公开名，并且只有这一份实现。
"""

from __future__ import annotations


def join_nest(parent: str, seg: str) -> str:
    """块路径拼接：``'if:1'`` + ``'menu:2'`` → ``'if:1>menu:2'``。"""
    parent = str(parent or '')
    seg = str(seg or '')
    if not seg:
        return parent
    return f'{parent}>{seg}' if parent else seg


def option_key(index: int) -> str:
    """
    选项在菜单里的**位置键**：1 → ``A``，2 → ``B`` …… 26 之后 → ``A27``。

    超过 26 个选项的菜单很少见，但**不许撞键**：``A27`` 带数字，
    与单字母不会混。
    """
    try:
        i = int(index)
    except (TypeError, ValueError):
        return ''
    if 1 <= i <= 26:
        return chr(ord('A') + i - 1)
    return f'A{i}' if i > 0 else ''


def option_index(key) -> int:
    """
    位置键 → 序号（1-based，``option_key`` 的逆）：``A`` → 1、``B`` → 2、
    ``A27`` → 27。**认不出返回 0**（调用方据此退回"按出现顺序配对"）。

    为什么需要逆函数（2026-10-05 实测，空选项体）：渲染层要把"块体的键"
    对到"第几个选项"。只按出现顺序配对时，**某个选项体是空的**就会错位 ——

        menu:
            "空的":            ← A，原文里没有块体
            "有内容":          ← B
                e "x"          ← 路径 `menu:1>B`

    唯一的键 ``B`` 被配给第 1 个选项（``空的``）→ **两个选项体对调**：
    选"空的"播了台词、选"有内容"什么都不做（静默改剧情）。
    键本身就是位置，能解出来就必须用它。
    """
    text = str(key or '').strip()
    if len(text) == 1 and 'A' <= text <= 'Z':
        return ord(text) - ord('A') + 1
    if len(text) > 1 and text[0] == 'A' and text[1:].isdigit():
        i = int(text[1:])
        return i if i > 0 else 0
    return 0


def split_nest(path) -> list:
    """``'if:1>menu:2>A'`` → ``['if:1', 'menu:2', 'A']``（空路径 → ``[]``）。"""
    return [s for s in str(path or '').split('>') if s]
