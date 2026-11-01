"""
`storygraph.story.call_expr`
============================
**技术调用**（`$ renpy.transition(...)` / `$ 项目自己的包装函数(...)`）：
单行调用的参数读值 + 定点写回（纯函数，不 eval）。

为什么单独一层（2026-10-30，用户："自定义模块要支撑复杂的转场调用"）
---------------------------------------------------------------------
`$ renpy.transition(MultipleTransition([...]), layer="master")` 是 **Python
代码**（`Beat.code`），不是 Ren'Py 语句；「自定义语句」那套按"语句头 +
格子"的机制**装不下它**。但项目里恰恰最爱在这里写复杂过场 —— 表达式带
括号/空格/列表，手改一格就等于重写整行，很容易改坏。

这一层只回答两个问题：

  · 这一行里**恰好一次** `head(...)` 调用时，第 N 个位置参数 / 某个关键字
    参数的值是什么？（`read_arg`）
  · 把那个参数的值换掉，**其余字符一个不动**（`set_arg`）。

纪律与 `transition_expr` 同源
-----------------------------
  · **不 eval**：参数只按顶层逗号切开，值原样取原文；
  · 注释/字符串里的 `head(` 不算；
  · 括号配不平 / 一拍里同一 `head` 出现多次 → **不认**（返回空 / 原样），
    绝不挑一条改 —— 挑错就是改了另一条指令；
  · 值没变 → 返回**入参原串**（逐字节相同）。

判定"这是不是一次技术调用"由调用方（`statement_registry` 的 `calls` 声明 /
内置 `renpy.transition`）完成；本模块只认字面量 head，不猜语义。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from storygraph.story.beat_text import (CODE_BLOCK_BODY, CODE_BLOCK_OPEN,
                                        _KWARG_RE, _mask_non_code,
                                        _match_paren, _split_top_level)


@dataclass(frozen=True)
class CallArg:
    """一个实参：位置参数（`index`）或关键字参数（`name`）。"""

    kind: str = 'pos'          # 'pos' | 'kw'
    index: int = -1            # 位置参数下标（kw 时 -1）
    name: str = ''             # 关键字名（pos 时空）
    start: int = 0             # 整个实参区间（kw 含 `name=`；用于删除）
    end: int = 0
    value_start: int = 0       # 值本身的区间（用于替换，保住 `name=`）
    value_end: int = 0
    value: str = ''            # 值原文（去首尾空白，不含外层逗号）


@dataclass(frozen=True)
class CallHit:
    """一次 `head(...)` 调用（`start` = head 起点，`end` = `)` 之后）。"""

    head: str = ''
    start: int = 0
    end: int = 0
    open: int = 0              # `(` 的下标
    close: int = 0             # `)` 的下标
    expr: str = ''             # 整个调用原文（`head(...)`）
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)

    def arg(self, position=None, kw: str = ''):
        """按位置号 / 关键字取实参（没有 → None）。"""
        if kw:
            return self.kwargs.get(str(kw))
        if position is None:
            return None
        try:
            idx = int(position)
        except (TypeError, ValueError):
            return None
        for arg in self.args:
            if arg.kind == 'pos' and arg.index == idx:
                return arg
        return None


def _head_re(head: str):
    """`head(` 的正则；`renpy.transition` 这种带点的按字面量。"""
    text = str(head or '').strip()
    if not text:
        return None
    return re.compile(r'(?<![\w.])' + re.escape(text) + r'[ \t]*\(')


def _arg_spans(text: str, open_idx: int, close_idx: int) -> tuple:
    """调用体 → `([CallArg...], {kw: CallArg})`（绝对下标，保留原文）。"""
    body = text[open_idx + 1:close_idx]
    args: list = []
    kwargs: dict = {}
    offset = 0
    pos_index = 0
    for part in _split_top_level(body):
        # `_split_top_level` 已经 strip 过；重新在原文里找它的位置
        # （不能用 `body.find`：相同的参数值会出现两次）。
        idx = body.find(part, offset)
        if idx < 0:
            continue
        offset = idx + len(part)
        start = open_idx + 1 + idx
        end = start + len(part)
        pair = _KWARG_RE.match(part)
        if pair:
            key = pair.group(1)
            tail = part[pair.end():]
            raw = tail.strip()
            lead = len(tail) - len(tail.lstrip())
            value_start = start + pair.end() + lead
            value_end = value_start + len(raw)
            arg = CallArg('kw', -1, key, start, end, value_start, value_end,
                          raw)
            kwargs[key] = arg
        else:
            arg = CallArg('pos', pos_index, '', start, end, start, end, part)
            pos_index += 1
        args.append(arg)
    return tuple(args), kwargs


def _block_masked(text: str) -> str:
    """
    `python:` 块体行 → 空格（长度不变）。

    `Beat.code` 里块体每行以 `CODE_BLOCK_OPEN` / `CODE_BLOCK_BODY` 开头
    （哨兵定义在 `story.beat_text`，是文本格式事实）；块里的调用语义是
    "循环/条件里可能执行"，不是"这一拍固定过场"，不能给它建可编辑列/派生
    预览。掩码而不是删除，是为了让区间下标仍能切回原文。
    """
    s = str(text or '')
    markers = (CODE_BLOCK_OPEN, CODE_BLOCK_BODY)
    out = []
    for line in s.split('\n'):
        out.append(' ' * len(line) if line[:1] in markers else line)
    return '\n'.join(out)


def find_calls(text, head: str) -> list:
    """
    文本里**所有** `head(...)` 调用（按出现顺序）；配不平的跳过。

    `text` 可以是整段代码块（多行），返回的区间是它在 `text` 里的绝对位置。
    `python:` 块体里的调用不算（见 `_block_masked`）。
    """
    s = str(text or '')
    pattern = _head_re(head)
    if not pattern or not s:
        return []
    # 注释/字符串/`python:` 块体里的 `head(` 不算。
    masked = _mask_non_code(_block_masked(s))
    out = []
    for m in pattern.finditer(masked):
        open_idx = m.end() - 1
        close_idx = _match_paren(s, open_idx)
        if close_idx < 0:
            continue
        args, kwargs = _arg_spans(s, open_idx, close_idx)
        out.append(CallHit(head=str(head), start=m.start(), end=close_idx + 1,
                           open=open_idx, close=close_idx,
                           expr=s[m.start():close_idx + 1],
                           args=args, kwargs=kwargs))
    return out


def has_call(text, head: str) -> bool:
    """文本里有没有这个 head 的调用（只回答"有没有"，不要求唯一）。"""
    return bool(find_calls(text, head))


def _unquote(raw: str) -> str:
    """`"master"` → `master`（只处理成对引号；转义按字符本身）。"""
    text = str(raw or '').strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'':
        body = text[1:-1]
        return body.replace('\\' + text[0], text[0]).replace('\\\\', '\\')
    return text


def read_arg(text, head: str, position=None, kw: str = '',
             unquote: bool = True) -> str:
    """
    单次调用的某个实参 → 值；**同一 head 出现多次 / 没有这个参数 → 空串**。

    `unquote=True`（默认）把 `layer="master"` 读成 `master`（表格里好看）；
    过场表达式用 `unquote=False`，保留 `"..."` 原样（写回要逐字）。
    """
    hits = find_calls(text, head)
    if len(hits) != 1:
        return ''
    arg = hits[0].arg(position=position, kw=kw)
    if arg is None:
        return ''
    return _unquote(arg.value) if unquote else arg.value


def _with_quote(raw: str, value: str) -> str:
    """原值带引号 → 新值也包同一种引号（`layer="master"` 的写法要保住）。"""
    text = str(raw or '').strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'':
        quote = text[0]
        body = str(value).replace('\\', '\\\\').replace(quote, '\\' + quote)
        return f'{quote}{body}{quote}'
    return str(value)


def set_arg(text, head: str, position=None, kw: str = '', value='') -> str:
    """
    换掉单次调用的一个实参；返回新文本（**认不出/多次调用 → 原样返回**）。

    清空（`value=''`）会连同分隔逗号一起删掉；参数本来不存在且新值为空
    → 不动。值没变 → **逐字节原样**。
    """
    s = str(text or '')
    hits = find_calls(s, head)
    if len(hits) != 1:
        return s
    hit = hits[0]
    arg = hit.arg(position=position, kw=kw)
    new_value = str(value if value is not None else '').strip()
    if arg is None:
        if not new_value:
            return s
        insert = f'{new_value}, ' if hit.args or hit.kwargs else new_value
        return s[:hit.close] + insert + s[hit.close:]
    old_value = _unquote(arg.value)
    # 调用方可能把值连引号一起给过来（表格里存的就是原文）——两边的
    # "值"口径要一致：比的是去引号后的内容，写回时再按原样式包。
    new_plain = _unquote(new_value)
    if old_value.strip() == new_plain.strip():
        return s
    if not new_value:
        start, end = arg.start, arg.end
        # 吃掉一个分隔逗号：优先吃前面的（`a, b` 删 b），否则吃后面的。
        before = s[:start].rstrip()
        after = s[end:].lstrip()
        if before.endswith(','):
            start = len(before) - 1
            while start > 0 and s[start - 1] in ' \t':
                start -= 1
        elif after.startswith(','):
            end += len(s[end:]) - len(after) + 1
            while end < len(s) and s[end] in ' \t':
                end += 1
        return s[:start] + s[end:]
    quoted = (len(new_value) >= 2 and new_value[0] == new_value[-1]
              and new_value[0] in ('"', "'"))
    replacement = new_value if quoted else _with_quote(arg.value, new_value)
    return s[:arg.value_start] + replacement + s[arg.value_end:]


__all__ = ['CallArg', 'CallHit', 'find_calls', 'has_call', 'read_arg',
           'set_arg']
