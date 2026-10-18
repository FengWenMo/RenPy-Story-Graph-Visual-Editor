"""
storygraph.core.cell_limits
============================
Excel 单元格的**硬上限 32767** —— 全工具只认这一个数。

为什么单独一个模块
------------------
这个数在别处出现过三种口径，每一个都踩过坑：

  · 写表时**不设限**，让 openpyxl 自己去截 → 40000 字的独白只剩 32767，
    **零警告**，下次"导出回 .rpy"带着截断文本覆盖原文（2026-10-16 载体攻击
    量到的：一段 40000 字独白，产物少 3187 字）；
  · 为了绕开它，把超长文本搬进隐藏表「长文本保真」→ 表面修好了，实际上
    造出了"一行 64KB"的怪数据：Excel 里装不下、协作库的 `BasePayload`
    抄进单元格时照样被截成 32767，JSON 在 char 592 断掉，**第二次协作起
    字段级合并全部退化成 revision 比较**（2026-10-18 实测）；
  · 只在导出时检查 → 用户在卡片里敲了三万字，到出表才被打回。

用户拍板（2026-10-18）：

> 算了……还是把字数改回 excel 的单元格上限吧……不然太容易出问题了
> （一条 3w 多字，整个屏幕估计都装不下）

所以现在的口径是**一条**：

    任何一格超过 32767 字符 → **明确报错**，不截断、不搬去隐藏表、
    不写一个"看起来成功"的表。

报错而不是截断：截断是静默改内容（最坏的缺陷）；搬表是把问题藏起来
（协作、性能、可读性全被它拖下水）。报错让人把一句拆成几拍 —— 那本来
也是三万字独白该有的形态。

历史数据的读法：老表里可能还带着「长文本保真」隐藏表（那一版工具写的），
`excel_export._read_long_text` 仍然认它 —— **只读兼容，不再产出**。
"""

from __future__ import annotations

from typing import Iterable, List

__all__ = ['CELL_TEXT_LIMIT', 'CellTextTooLong', 'over_limit',
           'row_offenders', 'ensure_fit']

#: Excel（xlsx 规范）单元格的字符串硬上限。超过它 openpyxl **静默截断**。
CELL_TEXT_LIMIT = 32767


class CellTextTooLong(ValueError):
    """
    有格超过 `CELL_TEXT_LIMIT` —— 写下去就会被静默截断，所以**不写**。

    `offenders` 每项 `{'where': '哪一行/哪张表', 'field': '哪一格',
    'length': 多少字}`。信息里点名到"行 + 字段 + 多出多少字"，
    因为只说"太长了"等于把找的工作丢回给人。
    """

    def __init__(self, offenders: Iterable, where: str = ''):
        self.offenders: List[dict] = [dict(o) for o in (offenders or ())]
        self.where = str(where or '')
        lines = [f'这些格超过 Excel 单元格上限 {CELL_TEXT_LIMIT} 字符'
                 f'（写下去会被静默截断，所以工具拒绝写）：']
        for o in self.offenders[:10]:
            who = str(o.get('where') or '').strip()
            field = str(o.get('field') or '').strip()
            length = int(o.get('length') or 0)
            tail = f'（多出 {length - CELL_TEXT_LIMIT} 字）' if length > CELL_TEXT_LIMIT else ''
            label = ' · '.join(x for x in (who, field) if x) or '?'
            lines.append(f'  · {label} {length} 字{tail}')
        if len(self.offenders) > 10:
            lines.append(f'  … 另有 {len(self.offenders) - 10} 处')
        lines.append('把这一句拆成几拍再导出（工具不会替你截断，'
                     '也不会把它搬进隐藏表 —— 那两样都会让内容静默变形）。')
        if self.where:
            lines.append(f'（出在：{self.where}）')
        super().__init__('\n'.join(lines))


def over_limit(value) -> int:
    """
    这一格超限了吗？返回**字符数**（0 = 没超 / 不是字符串）。

    ⚠ 只数 `str`：数字、日期、`None` 不会被 Excel 截断，不该在这里拦。
    """
    if not isinstance(value, str):
        return 0
    return len(value) if len(value) > CELL_TEXT_LIMIT else 0


def row_offenders(values, headers=(), where: str = '') -> List[dict]:
    """一整行 → 超限的格（`where` 一般写行号 / LineId）。"""
    out = []
    for i, value in enumerate(values or ()):
        n = over_limit(value)
        if n:
            field = headers[i] if i < len(headers) else f'#{i + 1}'
            out.append({'where': str(where or ''), 'field': str(field),
                        'length': n})
    return out


def ensure_fit(offenders, where: str = '') -> None:
    """有超限的格就抛（把"检查"和"抛"分开，调用方可以先收集完再报）。"""
    if offenders:
        raise CellTextTooLong(offenders, where=where)
