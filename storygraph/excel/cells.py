"""
storygraph.excel.cells
======================
**安全写单元格**的唯一实现。

为什么需要它
------------
openpyxl 会给以 ``=`` 开头的字符串自动打上 ``data_type='f'``（公式）。
于是凡以 ``=`` 开头的**用户文本**——transform 说明里的
``=========== Transform … ===========`` 分隔线、用户随手写的 ``=TODO``——
都会被 Excel 当成公式，打开时报"已删除的公式"记录。

原理：写入后把类型掰回 ``'s'``（字符串），序列化时就以 ``<v>`` 而不是
``<f>`` 落盘，Excel 当普通文本读入。

2026-09-28 收敛
---------------
这段逻辑曾经在**三处各写一份**：``script_table._set_text`` /
``asset_registry._asset_set_text`` / ``compile._fill_export_sheet`` 内联。
其中一处只对"整行 append"生效、另一处对"按行号写"生效——行为已经开始漂移，
而它管的是"用户填的字会不会被 Excel 吃掉"。现在只有这一份。
"""
from __future__ import annotations

from typing import Iterable


def set_text(ws, row, col, value):
    """
    写一个单元格，**强制按文本存储**（见模块 docstring）。返回该单元格。
    """
    cell = ws.cell(row=row, column=col)
    cell.value = value
    if isinstance(value, str) and value.startswith('='):
        cell.data_type = 's'
    return cell


def append_text_row(ws, values: Iterable):
    """
    在表尾整行写入（等价 ``ws.append``，但每个文本单元格都过 `set_text`）。
    返回写入的行号。
    """
    row = ws.max_row + 1 if ws.max_row else 1
    for col, value in enumerate(values, start=1):
        set_text(ws, row, col, value)
    return row


def set_row(ws, row: int, values: Iterable):
    """
    写到**指定行**（编译结果表按行号写的时候用）。返回写出的单元格列表。
    """
    cells = []
    for col, value in enumerate(values, start=1):
        cells.append(set_text(ws, row, col, value))
    return cells


__all__ = ['set_text', 'append_text_row', 'set_row']
