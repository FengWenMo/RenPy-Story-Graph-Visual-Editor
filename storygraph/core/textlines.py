"""
storygraph.core.textlines
=========================
**按 Ren'Py 的口径切行**：只认 `\\n`。

为什么必须有一处统一的实现（2026-10-25《不安全因素》第五轮，P0-A）

`str.splitlines()` 认的"行边界"远不止 `\\n`：`\\x0b`(VT)、`\\x0c`(FF)、
`\\x1c`~`\\x1e`、`\\x85`(NEL)、`U+2028`(LINE SEPARATOR)、`U+2029`(PARAGRAPH
SEPARATOR) 在它眼里全是换行。Ren'Py 的 lexer 只认 `\\n`。

两边口径不一致会**静默改坏用户的脚本**，而且两个方向都坏：

  · 切完再拼回：`"\\n".join(text.splitlines())` 会把一个 `U+2028`
    **变成真正的换行** —— 一行变两行，后半截成了顶层语句（语法错）；
  · 拼的时候按"每行自己的换行符"还原：那个 `U+2028` 会被当成换行符写回，
    **真正的 `\\n` 被它顶掉**，两条语句并成一条。

这类字符最常从网页 / Word 粘台词时带进来（`U+2028` 是"软回车"），
作者完全看不见它 —— 所以判据只能是"字节一个不动"，不能靠人眼发现。

用法：**凡是"切开 → 处理 → 拼回去"的地方都用它**；只读的展示/统计
（`len(text.splitlines())` 之类）无所谓，但如果那个数字要和物理行号对上，
同样必须用它（否则解析器与改写点的行号会差开，删错行）。
"""

from __future__ import annotations

#: Ren'Py 认的换行符
NEWLINE = '\n'


def split_lines(text) -> list:
    """
    `text` → 每行内容（不含换行符）的列表。**只按 `\\n` 切**。

    与 `str.splitlines()` 的差别只在含特殊分隔符的文本上：这里把它们
    当**普通字符**留在行内，一个都不动。

    末尾换行不产生空行：`'a\\n'` → `['a']`（与 `splitlines()` 一致）。

    入参为 `None` / 空串 → `[]`。
    """
    body = '' if text is None else str(text)
    if not body:
        return []
    rows = body.split(NEWLINE)
    if rows and rows[-1] == '':
        rows.pop()
    return rows


def split_eols(text) -> tuple:
    """
    `text` → `(每行内容, 每行自己的换行符)`，**只按 `\\n` 切**。

    每行换行符是 `'\\r\\n'` / `'\\n'`；**最后一行没有换行符时是 `''`**
    （`tail_missing_eol`）—— 调用方靠它判断"末尾原本有没有换行"，
    写回去时不能凭空补一个。

    与 `source_style.apply(..., source=)` 的逐行还原是同一份判据，
    所以这边只实现一次，那边直接用。
    """
    body = '' if text is None else str(text)
    if not body:
        return [], []
    chunks = body.split(NEWLINE)
    tail_missing_eol = chunks[-1] != ''
    if not tail_missing_eol:
        chunks = chunks[:-1]
    plain, eols = [], []
    for i, chunk in enumerate(chunks):
        if chunk.endswith('\r'):
            plain.append(chunk[:-1])
            eols.append('\r\n')
        else:
            plain.append(chunk)
            eols.append('' if (tail_missing_eol and i == len(chunks) - 1)
                        else '\n')
    return plain, eols


def join_lines(rows, eols=None, eol: str = NEWLINE) -> str:
    """
    `split_eols` 的逆运算：按给定的逐行换行符拼回去（不给就用 `eol`）。

    `len(eols) != len(rows)` 时按 `eol` 兜底 —— 不猜、不缩水。
    """
    rows = list(rows or ())
    if eols is not None and len(eols) == len(rows):
        out = ''.join(str(r) + (e or '') for r, e in zip(rows, eols))
        return out
    return eol.join(str(r) for r in rows)


__all__ = ['NEWLINE', 'join_lines', 'split_eols', 'split_lines']
