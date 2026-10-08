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

from storygraph.core.safeguard import swallow


def set_text(ws, row, col, value):
    """
    写一个单元格，**强制按文本存储**（见模块 docstring）。返回该单元格。
    """
    cell = ws.cell(row=row, column=col)
    cell.value = value
    if isinstance(value, str) and value.startswith('='):
        cell.data_type = 's'
    return cell


def _remember_row(ws, row: int) -> None:
    """把"已经写到第几行"记在表对象上（**O(1) 游标**，见 `_next_free_row`）。"""
    last = getattr(ws, '_dsh_last_row', 0) or 0
    if row > last:
        try:
            ws._dsh_last_row = row
        except Exception as e:           # noqa: BLE001 - 只读/带 __slots__ 的表对象
            swallow('excel.cells._remember_row', e)


def _next_free_row(ws) -> int:
    """
    表尾的**下一个空行号** —— 必须是 O(1)。

    为什么不能直接用 `ws.max_row`（2026-10-07 性能修，用户报"打开剧情演出表
    过久 → 程序未响应"）：openpyxl 的 `max_row` 每次都要**遍历整张表的
    `_cells` 求最大行号**，而建表是"一行一行追加"（真机 1.8 万行 / 18.5 万格）
    → 整体 O(n²)。实测 `tools/probe_table_perf.py --profile`：

        27.7 秒里 **18.2 秒**花在 `max_row`（37,177 次调用 = 每行问两次）

    这里改成"自己记游标"：第一次问一次 `max_row` 并记下，之后只看那个数。
    别的写入者（`ws.append` / `set_row`）也会更新它，所以不会写重行。
    """
    # ⚠ 必须**同时**看两个游标（2026-10-07 修，5 个表结构判据当场红）：
    #   · `_dsh_last_row` = 我们自己写的（`set_text` / `set_row` 走的那条）；
    #   · `_current_row`  = openpyxl 自己在 `ws.append()` / `ws.cell()` 里维护的 ——
    #     同一个表**混着用** `ws.append` 与 `append_text_row` 时（资源图库那张
    #     表就是这样），只看自己的游标会**写重行**（盖掉刚 append 的内容）。
    # 取两者的最大值：任一路径写过，行号都跟着走；两个都没有（新表）才问一次
    # `max_row`（它才是 O(n) 的那一个）。
    last = getattr(ws, '_dsh_last_row', 0) or 0
    try:
        last = max(int(last), int(getattr(ws, '_current_row', 0) or 0))
    except (TypeError, ValueError) as e:  # noqa: BLE001 - 表对象的游标不是数字
        swallow('excel.cells._next_free_row.current_row', e)
    if not last:
        last = ws.max_row or 0
        try:
            ws._dsh_last_row = last
        except Exception as e:           # noqa: BLE001 - 同上：退化成每次问 max_row
            swallow('excel.cells._next_free_row', e)
    return int(last) + 1


def append_text_row(ws, values: Iterable):
    """
    在表尾整行写入（等价 ``ws.append``，但每个文本单元格都过 `set_text`）。
    返回写入的行号。
    """
    row = _next_free_row(ws)
    for col, value in enumerate(values, start=1):
        set_text(ws, row, col, value)
    _remember_row(ws, row)
    return row


def set_row(ws, row: int, values: Iterable):
    """
    写到**指定行**（编译结果表按行号写的时候用）。返回写出的单元格列表。
    """
    cells = []
    for col, value in enumerate(values, start=1):
        cells.append(set_text(ws, row, col, value))
    _remember_row(ws, row)               # 游标跟着走，追加不会写重
    return cells


__all__ = ['set_text', 'append_text_row', 'set_row']
