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

2026-11-01（技术债第十轮）追加两类**文本格式事实**：

- `CODE_BLOCK_OPEN` / `CODE_BLOCK_BODY`：`python:` 代码块的行首哨兵。
  它们原先定义在 `beat_model.py`，`call_expr` / `transition_expr` 为了
  认这两个标记反过来 import `beat_model` —— 又一个"上层被下层反写"的环
  （`beat_model → call_expr → transition_expr → beat_model`）。哨兵是
  **文本格式**，不是 `Beat` 的属性，放这里两个方向都变成"从上往下拿"。
- `_mask_non_code`：字符串/注释遮空。`transition_expr` 与 `spec_clauses`
  各抄了一份**逐字相同**的实现（45 行 × 2），顺带合并成一份。
"""

from __future__ import annotations

import re


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


# ---------------------------------------------------------------------------
#  `python:` 代码块的行首哨兵（文本格式，不属于 `Beat`）
# ---------------------------------------------------------------------------

#: 块**开头**那行（`python:`）的前缀。
CODE_BLOCK_OPEN = '\ue001'
#: 块**体**里每一行的前缀。
CODE_BLOCK_BODY = '\ue002'


def _mask_non_code(text: str) -> str:
    """
    把**字符串字面量与注释**替换成空格（换行保留），长度与原串一致。

    这样在"掩码"上找 `renpy.transition(` / `with` / 行尾的 `:` 只命中真
    代码；取参数时仍从原串切，字符串里的括号/逗号原样保留。
    """
    n = len(text)
    out = list(text)
    i = 0
    quote = ''
    while i < n:
        ch = text[i]
        if quote:
            out[i] = '\n' if ch == '\n' else ' '
            if ch == '\\':
                if i + 1 < n:
                    out[i + 1] = '\n' if text[i + 1] == '\n' else ' '
                i += 2
                continue
            if ch == quote:
                quote = ''
            i += 1
            continue
        # 三引号整块遮掉（块里可以有任意括号/关键字）
        triple = ''
        if text.startswith('"""', i):
            triple = '"""'
        elif text.startswith("'''", i):
            triple = "'''"
        if triple:
            end = text.find(triple, i + 3)
            stop = n if end < 0 else end + 3
            for j in range(i, stop):
                if text[j] != '\n':
                    out[j] = ' '
            i = stop
            continue
        if ch in '"\'':
            quote = ch
            out[i] = ' '
            i += 1
            continue
        if ch == '#':
            while i < n and text[i] != '\n':
                out[i] = ' '
                i += 1
            continue
        i += 1
    return ''.join(out)


#: `key=value` 形状的实参（`==` / `>=` 不算关键字实参）。
_KWARG_RE = re.compile(r'^([^\W\d]\w*)[ \t]*=(?!=)', re.UNICODE)


def _match_paren(text: str, open_idx: int) -> int:
    """`text[open_idx]` 是 `(` → 配对 `)` 的下标；配不平返回 -1。"""
    depth = 0
    i = open_idx
    n = len(text)
    quote = ''
    while i < n:
        ch = text[i]
        if quote:
            if ch == '\\':
                i += 2
                continue
            if ch == quote:
                quote = ''
            i += 1
            continue
        if ch in '"\'':
            quote = ch
            i += 1
            continue
        if ch == '#':
            nl = text.find('\n', i)
            i = n if nl < 0 else nl + 1
            continue
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_top_level(body: str) -> list:
    """调用体按**顶层逗号**切（跳过嵌套括号、字符串、注释）。"""
    parts = []
    buf = []
    depth = 0
    quote = ''
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if quote:
            buf.append(ch)
            if ch == '\\' and i + 1 < n:
                buf.append(body[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ''
            i += 1
            continue
        if ch in '"\'':
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == '#':
            nl = body.find('\n', i)
            if nl < 0:
                break
            i = nl + 1
            continue
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(''.join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append(''.join(buf))
    return [p.strip() for p in parts if p.strip()]
