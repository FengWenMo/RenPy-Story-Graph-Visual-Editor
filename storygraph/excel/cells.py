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

import re
from typing import Iterable

from storygraph.core.safeguard import swallow
from storygraph.core.cell_limits import (CELL_TEXT_LIMIT, CellTextTooLong,
                                          over_limit)

#: xlsx（XML 1.0）**根本装不下**的控制字符：`U+0000~U+0008` / `U+000B` /
#: `U+000C` / `U+000E~U+001F`（`\t` `\n` `\r` 是合法的，不在此列）。
#:
#: 2026-10-28 真机（用户项目 `chapter3_3_3.rpy` 第 79 行）撞上：一句台词里
#: 混进两个 `U+0005`，`openpyxl` 直接抛 `IllegalCharacterError` ——
#: **整份剧本表一行都导不出来**，而错因是五万行里的一格。
#:
#: ⚠ 2026-11-02 改正：**不再剥掉，改写成 `\x05` 这种看得见的转义**。
#: 剥掉的代价是逐字节保真直接破功 —— 真机 `sr_general` 96 个章节里
#: 有 4 个（9 处）含 `U+0005`，`.rpy → 表 → .rpy` 一趟回来那 9 个字符就没了，
#: 而且用户完全不知道（只有 `safeguard` 日志里有一行）。
#: 转义是**语义无损**的：Ren'Py 的字符串字面量本来就认 `\xNN`，
#: 引擎读出来的字符与原来的控制字符**完全相同**；读表时 `xml_unescape`
#: 再把它还原成真字符，于是 `原文 == 产物` 逐字节成立。
_ILLEGAL_XML_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')

#: 反向：`\x05`（四到六个可见字符）→ 控制字符。只认 `_ILLEGAL_XML_CHARS`
#: 覆盖的那一段 —— `\x41` 是 `A`，属于普通可见字符，用户真写了 `\x41`
#: 是想要 `A` 还是想要字面文本？**不猜**，原样留着。
_ESCAPED_CONTROL = re.compile(
    r'\\x(0[0-8]|0[bBcC]|0[eEfF]|1[0-9a-fA-F])')


def xml_escape(value, where: str = ''):
    """
    把 xlsx 装不下的控制字符**写成 `\\xNN` 转义**（非字符串原样返回）。

    返回**新值**，不改入参。`where` 只用于留痕（`'Beats!R61C7'` 这种），
    让 `safeguard.report()` 能指到"哪张表哪一格"。

    为什么不是剥掉：见 `_ILLEGAL_XML_CHARS` 上面的说明 —— 剥掉等于
    静默改内容，而"表只是中转站"这个架构让它在导出时变成永久损失。
    """
    if not isinstance(value, str):
        return value
    out = _ILLEGAL_XML_CHARS.sub(lambda m: '\\x%02x' % ord(m.group(0)), value)
    if out == value:
        return value
    codes = sorted({f'U+{ord(ch):04X}' for ch in value
                    if _ILLEGAL_XML_CHARS.match(ch)})
    swallow('excel.cells.xml_escape：控制字符写成 \\xNN 转义'
            + (f'（{where}）' if where else ''),
            ValueError(f'{len(codes)} 种：{"、".join(codes)}'))
    return out


def xml_unescape(value):
    """
    读表时的反向还原：`\\x05` → `U+0005`（非字符串原样返回）。

    与 `xml_escape` 是**同一对**，两处必须一起改（守卫见
    `tests/test_excel_illegal_chars.py` 的往返用例）。
    """
    if not isinstance(value, str) or '\\x' not in value:
        return value
    return _ESCAPED_CONTROL.sub(lambda m: chr(int(m.group(1), 16)), value)


def xml_unescape_row(row: dict) -> dict:
    """整行还原（`Beat.from_row` 的入参）：只碰字符串值，其余原样。"""
    if not isinstance(row, dict):
        return row
    return {k: (xml_unescape(v) if isinstance(v, str) else v)
            for k, v in row.items()}


#: 老名字（调用点与测试都还在用）。**语义已从"剥掉"变成"转义"**。
xml_safe = xml_escape


def set_text(ws, row, col, value):
    """
    写一个单元格，**强制按文本存储**（见模块 docstring）。返回该单元格。

    ⚠ **最后一公里的 32767 闸门**（2026-10-18）：超过 Excel 单元格上限的
    字符串**不写**，直接抛 `CellTextTooLong`。理由见 `core.cell_limits`：
    写下去会被 openpyxl 静默截断，而"少了几千字还零警告"是本项目最不能
    接受的一类缺陷。调用方（`excel_export`）会先按行 / 字段做一次带
    行号的检查，这里只兜底 —— 任何一条漏网的写路径都不会把内容写残。

    ⚠ 还有一道：xlsx **装不下**的控制字符（`U+0000~08` / `0B` / `0C` /
    `0E~1F`）在这里先写成 `\\xNN` 转义并留痕 —— 见 `xml_escape`。不处理的话，
    一格脏会让**整份工作簿**都写不出来（2026-10-28 真机就是这么撞上的）；
    而像从前那样直接剥掉，则会静默丢字符（2026-11-02 改正）。
    """
    where = f'{ws.title}!R{row}C{col}'
    # 先转义控制字符再判长度：转义会变长，而 32767 是**写下去的字节**上限，
    # 所以顺序必须是"先转义、后判长度"（与用户看到的那一格一致）。
    value = xml_escape(value, where=where)
    if over_limit(value):
        raise CellTextTooLong([{'where': where,
                                'field': '', 'length': len(str(value))}],
                              where=f'写 {ws.title} 的第 {row} 行第 {col} 列')
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


__all__ = ['set_text', 'append_text_row', 'set_row', 'xml_safe',
           'xml_escape', 'xml_unescape', 'xml_unescape_row']
