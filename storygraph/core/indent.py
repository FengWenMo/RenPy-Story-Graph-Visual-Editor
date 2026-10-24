"""
storygraph.core.indent
======================
**缩进测量的唯一实现**（两个语义，两个名字）。

为什么单列（2026-10-23，清单 §十四）：
同一个私有的 `_indent_of` 在四个模块里各写了一遍，其中三份返回整数、
`story.patcher` 那份返回**缩进前缀字符串**；tab 一行算 4 还是算 1 也不一致
（`atl_block` 按 4，`renpy_parser` / `python_lift` 按字符数 1）。同名不同义
的真实代价：从 `renpy_parser` 复制 `n = _indent_of(line)` 到需要前缀的地方，
得到的是 `4 + "show ling"`。

这里只提供两个名字，语义写在名字上：

  · `indent_prefix(line)` —— 原样的缩进**前缀字符串**（拼接用）；
  · `indent_width(line)`  —— 视觉**宽度**（比较层级用，tab=4，与 Ren'Py
    默认口径一致）。

需要"字符数"的地方请写 `len(indent_prefix(line))`，别再加第三个名字。
"""

from __future__ import annotations

#: tab 的视觉宽度（Ren'Py 默认 tab 口径）。
TAB_WIDTH = 4


def indent_prefix(line) -> str:
    """行首的缩进原样前缀（空格 / tab 都保留；空行 → 空串）。"""
    text = str(line or '')
    return text[:len(text) - len(text.lstrip(' \t'))]


def indent_width(line) -> int:
    """行首缩进的**视觉宽度**（空格 = 1，tab = `TAB_WIDTH`）。"""
    width = 0
    for ch in indent_prefix(line):
        width += TAB_WIDTH if ch == '\t' else 1
    return width


__all__ = ['TAB_WIDTH', 'indent_prefix', 'indent_width']
