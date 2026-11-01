"""
`beat_render` 的**纯文本扫描**（2026-11-01 从 `beat_render_code.py` 搬出）
============================================================================

为什么单独一个模块：

`_bracket_delta` / `_quote_state` **不认识 Beat、也不认识 Ren'Py** —— 输入是
"一行文本 + 当前引号状态"，输出是括号增量 / 新的引号状态。纯字符串扫描。

它们原先住在 `beat_render_code.py` 里，逼得 `beat_model.py`（`Beat` 本体）
为了"代码块该不该合并成一条"反过来 `import story.beat_render` —— 就是
`debt_report.py` 记的那 12 条**反馈边**之一（`beat_model → beat_render`，
4 处使用）。搬到叶子模块之后，`beat_model` 与 `beat_render_code` 各自从这里拿，
方向回到"上层用下层"。

⚠ 只搬不改语义、逐字照搬。`beat_render_code` 与 `beat_render` 仍然
re-export 这两个名字 —— `tests/test_product_python_syntax.py` 直接从
`beat_render` 取 `_bracket_delta`，那条路必须还通。
"""

from __future__ import annotations


def _bracket_delta(line: str, quote: str) -> int:
    """这一行让括号深度变化多少（引号内的括号不算；`#` 之后不算）。"""
    depth = 0
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote:
            if ch == '\\':
                i += 2
                continue
            if line.startswith(quote, i):
                i += len(quote)
                quote = ''
                continue
            i += 1
            continue
        if ch == '#':
            break
        if ch in '"\'':
            if line.startswith(ch * 3, i):
                quote = ch * 3
                i += 3
                continue
            quote = ch
            i += 1
            continue
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        i += 1
    return depth

def _quote_state(line: str, quote: str) -> str:
    """扫完这一行之后，三引号字符串是否还开着（开着的引号标记，否则空串）。"""
    i = 0
    n = len(line)
    while i < n:
        if quote:
            if line.startswith(quote, i):
                quote = ''
                i += 3
                continue
            i += 1
            continue
        ch = line[i]
        if ch == '#':
            break
        if ch in '"\'':
            if line.startswith(ch * 3, i):
                quote = ch * 3
                i += 3
                continue
            # 单行字符串：跳到它的收尾
            i += 1
            while i < n:
                if line[i] == '\\':
                    i += 2
                    continue
                if line[i] == ch:
                    break
                i += 1
        i += 1
    return quote
