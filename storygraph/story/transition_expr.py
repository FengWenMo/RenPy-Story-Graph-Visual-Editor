"""
`storygraph.story.transition_expr`
==================================
把**项目自定义的过场写法**读成结构（只读，不碰任何字节）。

背景（2026-10-30 用户第 3 条反馈："过场没有注册"）
--------------------------------------------------
`$ renpy.transition(Dissolve(0.1), layer="master")` 在工具里是 **Python
代码**（`StatementKind.PYTHON` → `Beat.code` / CodeBlock），不是 Ren'Py 的
"语句"；而工具过去只有 `Beat.with_` 一条过场通道（只由 `show/scene/hide …
with` 的子句或 Stage 列的 `[转场]` 写入）。两条路互不相通，于是真机项目里
几百处过场在卡片、预览、Excel「转场」列里**根本不存在**，预览永远硬切。

这一层只负责**读**：从 `Beat.code` 里认出单行/多行 `renpy.transition(...)`
调用、从 `Beat.raw` 里认出独立一行的 `with <表达式>`、从 `Beat.with_` 里
认出 `with` 子句。派生结果交给 `story.transition_catalog` 翻成"效果描述"，
再交给预览/卡片（第二波接入，见实施契约 §1.7）。

纪律（与 `cds_registry` / `layered_model` 同源）
------------------------------------------------
- **不 eval**：表达式只做字面量语法解析（字符串 / 数字 / 列表 / 名字 /
  调用），遇到变量运算、lambda、推导式等一律返回 `None`（"认不出"）。
- **不猜**：括号配不平、字符串没闭合、表达式读不懂 → 该条**不产出**；
  参数不是字面量 → 记成 `NonLiteral`（`args` 里能看出"这里不知道"），
  绝不把变量名当成字符串值。
- **注释与字符串里的 `renpy.transition` 不算**：先做逐字符"掩码"，只在
  真代码上定位；取参数时仍从原串切（字符串必须原样保留）。
- **纯读**：不改 `Beat` / `Statement` / `Beat.with_`，不提供写入口。
  "改值才写、只动那一行"是 B 档的事，不在这里。

三种来源（`TransitionCall.source`）
-----------------------------------
  · `'renpy.transition'`：`$ renpy.transition(<表达式>, layer="master")`
  · `'with'`           ：独立一行的 `with <表达式>`（`Beat.raw` 里）
  · `'inline-with'`    ：`show/scene … with X` 的子句（`Beat.with_` 里）

三条来路**都要**（Lead 2026-10-30 拍板）
----------------------------------------
`beat_transitions` 同时产出上面三类：真机 `chapter3_1` 有 35 处独立
`with X`，只按"`Beat.code` + `Beat.with_`"两路读会整章丢过场；契约 §1.3
的"两路"是下限、§1.7 的"独立 with 同样生效"是补充。`line` 统一 0（beat
级别没有可靠源码行号，不编假行号）。

已知限制（记录、不阻塞）
------------------------
`hide ye with vpunch` 这类 **`hide` + `with` 子句**仍派生不出过场：上游把
它压进 `Beat.hide`（形如 `'ye@vpunch'`），本层只认 `show/scene … with` 的
子句与独立 `with` 行。真机只剩个别残留（`vpunch` 本身是内置 ATL 抖动，
不是项目 `define` 的渐变转场），第二波不改；要修得先约定 `hide` 列的
转场写法。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from storygraph.core.safeguard import swallow

#: `$ renpy.transition(...)` —— Python 代码里的函数调用
SOURCE_RENPY = 'renpy.transition'
#: 独立一行的 `with <表达式>`
SOURCE_WITH = 'with'
#: `show/scene/… with <表达式>` 的子句（`Beat.with_`）
SOURCE_INLINE_WITH = 'inline-with'
#: 项目**声明过的复杂子句**（`mycue … with X` 的 `with`，2026-10-30）：
#: 它不是 `Beat.with_`，由 `app.stage` 按 `rules.json` 的 `clauses` 读出来。
SOURCE_CLAUSE = 'declared-clause'
#: 项目**声明过的技术调用**（`$ my_wipe(...)` 的包装函数，2026-10-30）：
#: 它本身不是 Ren'Py 转场，但项目在 `rules.json` 的 `calls` 里声明了
#: "等价于哪个效果"；预览按那个效果演。
SOURCE_CALL = 'declared-call'

#: Python 标识符（含中文等 Unicode 名字 —— 项目完全可以写 `with 场景淡入`）
_IDENT_RE = re.compile(r'[^\W\d]\w*', re.UNICODE)
#: `renpy.transition(` 的调用头（掩码后定位；允许 `renpy . transition (`）
_TRANSITION_RE = re.compile(r'(?<![\w.])renpy[ \t]*\.[ \t]*transition'
                            r'[ \t]*\(')
#: `with` 关键字（前后都不能贴着标识符；排除 `withhold` / `foo.with`）
_WITH_RE = re.compile(r'(?<![\w.])with(?=[ \t\r\n])')
#: `key=value` 形状的参数（`==` / `>=` 不算关键字参数）
_KWARG_RE = re.compile(r'^([^\W\d]\w*)[ \t]*=(?!=)', re.UNICODE)
#: 数字字面量
_NUMBER_RE = re.compile(r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?')
#: 字符串转义里最常见的几个（不认识的转义按"字符本身"处理，不求值）
_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', '0': '\0',
            '\\': '\\', "'": "'", '"': '"'}
#: 表达式里不该当"名字"的关键字（`True/False/None` 单独处理）。
#: 见到就 `_ParseError`：`with`、`and`、`lambda` 这些是语法，不是转场名。
_KEYWORDS = frozenset({
    'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue',
    'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from',
    'global', 'if', 'import', 'in', 'is', 'lambda', 'nonlocal', 'not',
    'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield',
})


# ---------------------------------------------------------------------------
#  表达式节点（只表示结构，**不执行**）
# ---------------------------------------------------------------------------

class Expr:
    """表达式节点基类。子类都只是"读到的形状"，没有求值能力。"""


@dataclass(frozen=True)
class LiteralExpr(Expr):
    """字符串 / 数字 / `True` / `False` / `None` 字面量。"""

    value: object = None


@dataclass(frozen=True)
class NameExpr(Expr):
    """裸名字（项目 `define` 过的转场名、`dissolve` 这类内置名）。"""

    text: str = ''


@dataclass(frozen=True)
class CallExpr(Expr):
    """函数/构造器调用，如 `Dissolve(0.1)` / `MultipleTransition([...])`。"""

    name: str = ''
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)

    def keyword(self, key: str):
        """取关键字参数的**节点**（没有返回 None）。"""
        return self.kwargs.get(key)


@dataclass(frozen=True)
class ListExpr(Expr):
    """`[...]` 列表（`MultipleTransition` 的参数就是这个形状）。"""

    items: tuple = ()


@dataclass(frozen=True)
class TupleExpr(Expr):
    """`(...)` 元组 / 括起来的单个表达式。"""

    items: tuple = ()


@dataclass(frozen=True)
class NonLiteral:
    """
    一个**不是字面量**的参数 —— 只知道原文，不猜值。

    典型：`renpy.transition(get_transition())` 的表达式、`Dissolve(d)`
    里的 `d`。放进 `TransitionCall.args`，让消费方分清"0.1"与"读不到"。
    """

    text: str = ''


@dataclass(frozen=True)
class TransitionCall:
    """一次过场调用（`expr` 是**转场表达式**，不含 `renpy.transition` 外壳）。"""

    expr: str = ''            # 规范化表达式文本，如 'Dissolve(0.1)'
    name: str = ''            # 顶层名字：'Dissolve' / 'dissolve_scene_full'
    args: tuple = ()          # 字面量参数（没见过字面量处是 NonLiteral）
    kwargs: dict = field(default_factory=dict)   # renpy.transition 的关键字
    layer: str = ''           # 层，默认 ''（= 引擎默认）
    line: int = 0             # 输入文本内的行号（1-based；0 = 未知）
    source: str = ''          # SOURCE_* 之一

    def to_dict(self) -> dict:
        """可 JSON 化（探针/报告用）：`NonLiteral` 折成原文。"""
        return {
            'expr': self.expr,
            'name': self.name,
            'args': [_plain(a) for a in self.args],
            'kwargs': {k: _plain(v) for k, v in self.kwargs.items()},
            'layer': self.layer,
            'line': self.line,
            'source': self.source,
        }


def _plain(value):
    """`NonLiteral` → 原文；容器逐项折；别的原样。"""
    if isinstance(value, NonLiteral):
        return value.text
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


# ---------------------------------------------------------------------------
#  安全字面量解析器（递归下降，**不 eval**）
# ---------------------------------------------------------------------------

class _ParseError(Exception):
    """表达式不是"安全字面量 + 调用"形状 —— 调用方按"认不出"处理。"""


class _ExprParser:
    """只认字面量、名字、调用、列表/元组；别的一律 `_ParseError`。"""

    def __init__(self, text: str):
        self.s = str(text or '')
        self.i = 0
        self.n = len(self.s)

    # ---- 空白 / 注释 ----
    def _skip(self) -> None:
        s = self.s
        while self.i < self.n:
            ch = s[self.i]
            if ch in ' \t\r\n\f\v':
                self.i += 1
            elif ch == '#':                    # 注释到行尾
                nl = s.find('\n', self.i)
                self.i = self.n if nl < 0 else nl + 1
            elif ch == '\\' and s[self.i + 1:self.i + 2] == '\n':
                self.i += 2                    # 行续（Python 口径）
            elif ch == '\\' and s[self.i + 1:self.i + 3] == '\r\n':
                self.i += 3
            else:
                break

    # ---- 入口 ----
    def parse(self) -> Expr:
        node = self._value()
        self._skip()
        if self.i != self.n:
            raise _ParseError(f'表达式后面还有认不出的内容：'
                              f'{self.s[self.i:self.i + 20]!r}')
        return node

    def _value(self) -> Expr:
        self._skip()
        if self.i >= self.n:
            raise _ParseError('表达式为空')
        ch = self.s[self.i]
        # 字符串前缀（r/u/b 都是字面量；f-string 不求值 → 认不出）
        if ch in 'rRuUbB' and self.i + 1 < self.n \
                and self.s[self.i + 1] in '"\'':
            self.i += 1
            return LiteralExpr(self._string())
        if ch in 'fF' and self.i + 1 < self.n \
                and self.s[self.i + 1] in '"\'':
            raise _ParseError('f-string 是表达式，不是字面量')
        if ch in '"\'':
            return LiteralExpr(self._string())
        if ch.isdigit() or (ch == '.' and self.i + 1 < self.n
                            and self.s[self.i + 1].isdigit()):
            return LiteralExpr(self._number())
        if ch in '+-' and self._signed_number_ahead():
            return LiteralExpr(self._number())
        if ch == '[':
            return self._container('[', ']', ListExpr)
        if ch == '(':
            return self._container('(', ')', TupleExpr)
        if ch == '{':
            raise _ParseError('不支持 dict 字面量（不是过场参数）')
        m = _IDENT_RE.match(self.s, self.i)
        if not m:
            raise _ParseError(f'认不出的字符：{ch!r}')
        name = m.group(0)
        self.i = m.end()
        # 点号链：`renpy.transition` / `fade.quick`
        while True:
            save = self.i
            self._skip()
            if self.i + 1 < self.n and self.s[self.i] == '.' \
                    and _IDENT_RE.match(self.s, self.i + 1):
                nxt = _IDENT_RE.match(self.s, self.i + 1)
                name += '.' + nxt.group(0)
                self.i = nxt.end()
                continue
            self.i = save
            break
        if name in ('True', 'False', 'None'):
            return LiteralExpr({'True': True, 'False': False,
                                'None': None}[name])
        if name in _KEYWORDS:
            raise _ParseError(f'{name!r} 是关键字，不是转场名')
        self._skip()
        if self.i < self.n and self.s[self.i] == '(':
            return self._call(name)
        return NameExpr(name)

    # ---- 具体形状 ----
    def _signed_number_ahead(self) -> bool:
        j = self.i + 1
        if j >= self.n:
            return False
        return self.s[j].isdigit() or (self.s[j] == '.' and j + 1 < self.n
                                       and self.s[j + 1].isdigit())

    def _number(self):
        m = _NUMBER_RE.match(self.s, self.i)
        if not m:
            raise _ParseError('数字认不出')
        self.i = m.end()
        text = m.group(0)
        if '.' in text or 'e' in text or 'E' in text:
            return float(text)
        return int(text)

    def _string(self) -> str:
        quote = self.s[self.i]
        i = self.i + 1
        out = []
        while i < self.n:
            ch = self.s[i]
            if ch == '\\':
                if i + 1 >= self.n:
                    raise _ParseError('字符串以转义符结尾')
                nxt = self.s[i + 1]
                out.append(_ESCAPES.get(nxt, nxt))
                i += 2
                continue
            if ch == quote:
                self.i = i + 1
                return ''.join(out)
            out.append(ch)
            i += 1
        raise _ParseError('字符串没闭合')

    def _container(self, open_ch: str, close_ch: str, node_type):
        self.i += 1                            # 吃掉开括号
        items = []
        self._skip()
        if self.i < self.n and self.s[self.i] == close_ch:
            self.i += 1
            return node_type(())
        while True:
            items.append(self._value())
            self._skip()
            if self.i < self.n and self.s[self.i] == ',':
                self.i += 1
                self._skip()
                if self.i < self.n and self.s[self.i] == close_ch:
                    self.i += 1
                    break
                continue
            if self.i < self.n and self.s[self.i] == close_ch:
                self.i += 1
                break
            raise _ParseError(f'{open_ch}…{close_ch} 没闭合')
        return node_type(tuple(items))

    def _call(self, name: str) -> CallExpr:
        self.i += 1                            # 吃掉 '('
        args = []
        kwargs: dict = {}
        self._skip()
        if self.i < self.n and self.s[self.i] == ')':
            self.i += 1
            return CallExpr(name, (), {})
        while True:
            kw = self._try_kwarg()
            if kw is not None:
                key, val = kw
                kwargs[key] = val
            else:
                args.append(self._value())
            self._skip()
            if self.i >= self.n:
                raise _ParseError('调用括号没闭合')
            ch = self.s[self.i]
            if ch == ',':
                self.i += 1
                self._skip()
                if self.i < self.n and self.s[self.i] == ')':
                    self.i += 1
                    break
                continue
            if ch == ')':
                self.i += 1
                break
            raise _ParseError(f'调用参数分隔认不出：{ch!r}')
        return CallExpr(name, tuple(args), kwargs)

    def _try_kwarg(self):
        """`key=value` 形状 → `(key, 节点)`；不是就返回 None（位置回退）。"""
        m = _KWARG_RE.match(self.s[self.i:])
        if not m:
            return None
        save = self.i
        self.i += m.end()
        try:
            val = self._value()
        except _ParseError:
            self.i = save
            return None
        return m.group(1), val


def parse_expression(text: str):
    """
    表达式文本 → 节点；**认不出返回 `None`**（不猜、不抛）。

    只接受"字面量 / 名字 / 调用 / 列表 / 元组"的纯语法；变量运算、
    lambda、推导式、dict、f-string 一律 None。
    """
    try:
        return _ExprParser(text).parse()
    except _ParseError:
        return None


# ---------------------------------------------------------------------------
#  文本预处理：掩码、括号配平、顶层切分
# ---------------------------------------------------------------------------

def _mask_non_code(text: str) -> str:
    """
    把**字符串字面量与注释**替换成空格（换行保留），长度与原串一致。

    这样在"掩码"上找 `renpy.transition(` / `with` 只命中真代码；
    取参数时仍从原串切，字符串里的括号/逗号原样保留。
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


def _split_kwarg(part: str):
    """`layer="master"` → `('layer', '"master"')`；普通位置参数 → None。"""
    m = _KWARG_RE.match(part)
    if not m:
        return None
    return m.group(1), part[m.end():].strip()


# ---------------------------------------------------------------------------
#  规范化 + 取值
# ---------------------------------------------------------------------------

def _emit_space(out: list, next_ch: str) -> None:
    """多行合并后的空格：括号/逗号旁边不留空白（读起来像源码那一行）。"""
    if not out:
        return
    prev = out[-1]
    if prev in '([{.' or next_ch in ',)]}([':
        return
    out.append(' ')


def normalize_expression(text: str) -> str:
    """
    表达式 → 单行人话文本：去注释、多行合并、括号内不留多余空白。

    字符串内容**原样保留**（不重新转义、不改写引号）。
    """
    s = str(text or '')
    out: list = []
    i = 0
    n = len(s)
    quote = ''
    pending = False
    while i < n:
        ch = s[i]
        if quote:
            out.append(ch)
            if ch == '\\' and i + 1 < n:
                out.append(s[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ''
            i += 1
            continue
        if ch in '"\'':
            if pending:
                _emit_space(out, ch)
                pending = False
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == '#':
            nl = s.find('\n', i)
            i = n if nl < 0 else nl + 1
            pending = True
            continue
        if ch in ' \t\r\n\f\v':
            pending = True
            i += 1
            continue
        if pending:
            _emit_space(out, ch)
            pending = False
        out.append(ch)
        i += 1
    return ''.join(out).strip()


def _render_node(node) -> str:
    """节点 → 近似原文（只给 `NonLiteral` 的 `text` 用，不参与写回）。"""
    if isinstance(node, LiteralExpr):
        return repr(node.value)
    if isinstance(node, NameExpr):
        return node.text
    if isinstance(node, CallExpr):
        parts = [_render_node(a) for a in node.args]
        parts += [f'{k}={_render_node(v)}' for k, v in node.kwargs.items()]
        return f'{node.name}({", ".join(parts)})'
    if isinstance(node, ListExpr):
        return '[' + ', '.join(_render_node(x) for x in node.items) + ']'
    if isinstance(node, TupleExpr):
        body = ', '.join(_render_node(x) for x in node.items)
        if len(node.items) == 1:
            body += ','
        return '(' + body + ')'
    return ''


def _value_of(node):
    """节点 → `TransitionCall.args` 里的一项（字面量 / 列表 / NonLiteral）。"""
    if node is None:
        return NonLiteral('')
    if isinstance(node, LiteralExpr):
        return node.value
    if isinstance(node, ListExpr):
        return [_value_of(x) for x in node.items]
    if isinstance(node, TupleExpr):
        return tuple(_value_of(x) for x in node.items)
    return NonLiteral(_render_node(node))


def _node_name(node) -> str:
    """节点顶层名字（`Dissolve` / `dissolve_scene_full`）。"""
    if isinstance(node, CallExpr):
        return node.name
    if isinstance(node, NameExpr):
        return node.text
    return ''


def _literal_args(node) -> tuple:
    """转场表达式的字面量参数表（裸名字 → 空元组）。"""
    if isinstance(node, CallExpr):
        return tuple(_value_of(a) for a in node.args)
    return ()


def _is_transition_node(node) -> bool:
    """裸名字 / 调用才是过场；字符串、数字、`None` 都不是。"""
    return isinstance(node, (CallExpr, NameExpr))


# ---------------------------------------------------------------------------
#  `$ renpy.transition(<表达式>, layer=...)`
# ---------------------------------------------------------------------------

def _call_from_python(text: str, open_idx: int, close_idx: int, line: int,
                      source: str = SOURCE_RENPY):
    """把一段 `renpy.transition(...)` 的调用体翻成 `TransitionCall`。"""
    body = text[open_idx + 1:close_idx]
    positional: list = []
    keyword: dict = {}
    for part in _split_top_level(body):
        pair = _split_kwarg(part)
        if pair is None:
            positional.append(part)
        else:
            keyword[pair[0]] = pair[1]
    if not positional:
        return None                            # `renpy.transition()` 不是过场
    expr_src = positional[0]
    node = parse_expression(expr_src)
    if node is None or not _is_transition_node(node):
        return None                            # 认不出 → 不猜
    kwargs = {}
    for key, raw in keyword.items():
        val_node = parse_expression(raw)
        kwargs[key] = _value_of(val_node) if val_node is not None \
            else NonLiteral(raw)
    layer = ''
    layer_node = parse_expression(keyword['layer']) \
        if 'layer' in keyword else None
    if isinstance(layer_node, LiteralExpr) \
            and isinstance(layer_node.value, str):
        layer = layer_node.value
    elif len(positional) > 1:
        # `renpy.transition(trans, layer)` 的第二位置参数就是层
        second = parse_expression(positional[1])
        if isinstance(second, LiteralExpr) and isinstance(second.value, str):
            layer = second.value
    return TransitionCall(
        expr=normalize_expression(expr_src),
        name=_node_name(node),
        args=_literal_args(node),
        kwargs=kwargs,
        layer=layer,
        line=line,
        source=source,
    )


def parse_python_transition(code_text: str) -> list:
    """
    Python 代码文本 → `[TransitionCall, ...]`（按出现顺序）。

    `code_text` 是 `Beat.code` 那种**不带 `$`** 的代码正文（有 `$` 也无妨，
    扫描不依赖它）。多行调用、注释、字符串里的括号都能处理；
    `python:` 块体要不要排除由调用方决定（见 `beat_transitions`）。
    配不平 / 表达式认不出 → 那一条**不产出**。
    """
    text = str(code_text or '')
    if not text:
        return []
    masked = _mask_non_code(text)
    out = []
    for m in _TRANSITION_RE.finditer(masked):
        open_idx = m.end() - 1
        close_idx = _match_paren(text, open_idx)
        if close_idx < 0:
            continue
        line = text.count('\n', 0, m.start()) + 1
        call = _call_from_python(text, open_idx, close_idx, line)
        if call is not None:
            out.append(call)
    return out


# ---------------------------------------------------------------------------
#  `with <表达式>`（独立一行 / 行内子句）
# ---------------------------------------------------------------------------

def _read_with_expression(text: str, masked: str, pos: int):
    """
    从 `with` 关键字之后读一个表达式 → `(原文, 结束下标)`；读不出返回 None。

    表达式跨行只在括号没配平时允许（Ren'Py 的隐式续行规则）；
    顶层遇到换行/注释就结束。`masked` 里字符串和注释已经是空格，
    所以括号计数不会被它们骗到。
    """
    n = len(text)
    i = pos
    while i < n and text[i] in ' \t':
        i += 1
    if i >= n or text[i] in '\n#':
        return None
    start = i
    depth = 0
    while i < n:
        ch = masked[i]
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
            if depth < 0:
                return None
        elif ch == '\n' and depth == 0:
            break
        i += 1
    if depth != 0:
        return None
    expr = text[start:i].strip()
    if not expr:
        return None
    return expr, i


def _with_call(expr: str, source: str, line: int):
    node = parse_expression(expr)
    if node is None or not _is_transition_node(node):
        return None
    return TransitionCall(
        expr=normalize_expression(expr),
        name=_node_name(node),
        args=_literal_args(node),
        kwargs={},
        layer='',
        line=line,
        source=source,
    )


def parse_with_clause(text: str) -> list:
    """
    `with` 子句文本 → `[TransitionCall, ...]`。

    两种输入都接受：
      · 带关键字的：`with dissolve` / `show e happy with Dissolve(0.5)`
        （后者 `source='inline-with'`，前者 `source='with'`）；
      · 只有表达式的：`dissolve` / `Dissolve(0.5)`（`source='with'`，
        给 `Beat.with_` 这种已剥掉关键字的字段用）。
    认不出 → 空列表。
    """
    s = str(text or '')
    if not s.strip():
        return []
    masked = _mask_non_code(s)
    out = []
    consumed = 0
    for m in _WITH_RE.finditer(masked):
        if m.start() < consumed:
            continue
        got = _read_with_expression(s, masked, m.end())
        if got is None:
            continue
        expr, end_pos = got
        line_start = s.rfind('\n', 0, m.start()) + 1
        source = SOURCE_WITH if not s[line_start:m.start()].strip() \
            else SOURCE_INLINE_WITH
        call = _with_call(expr, source, s.count('\n', 0, m.start()) + 1)
        if call is None:
            consumed = end_pos
            continue
        out.append(call)
        consumed = end_pos
    if _WITH_RE.search(masked) is None:
        # 没有 `with` 关键字：整段当成一个表达式（`Beat.with_` 的形状）
        call = _with_call(s, SOURCE_WITH, 1)
        return [call] if call is not None else []
    return out


# ---------------------------------------------------------------------------
#  从 Beat 派生
# ---------------------------------------------------------------------------

def _block_markers() -> tuple:
    """`compose_code` 给 `python:` 块行打的标记（见 `beat_model.CODE_BLOCK_*`）。

    这里只**取常量**，不复制它们的字面量：标记属于 `beat_model` 的约定，
    第二处硬编码就说明约定分叉了（`test_code_health` 也盯着这条）。
    """
    try:
        from storygraph.story.beat_model import (CODE_BLOCK_BODY,
                                                 CODE_BLOCK_OPEN)
        return (CODE_BLOCK_OPEN, CODE_BLOCK_BODY)
    except Exception as e:                     # noqa: BLE001 - 读不到就退化为"无块"
        swallow('story.transition_expr.block_markers', e)
        return ()


def _composed_code_text(beat) -> str:
    """
    `Beat` 的 CodeBlock 文本（raw + code，按源码交错顺序），用于派生过场。

    走 `compose_code` 而不是自己拼 `beat.raw + beat.code`：两者之间**交错
    顺序**只有 `ordered` 知道，自己拼会把"先 show 再 $ f()"排成反的。
    """
    try:
        from storygraph.story.beat_model import compose_code
        return str(compose_code(beat) or '')
    except Exception as e:                     # noqa: BLE001 - 纯读派生，坏拍不崩
        swallow('story.transition_expr.compose_code', e)
        parts = [str(getattr(beat, 'raw', '') or ''),
                 str(getattr(beat, 'code', '') or '')]
        return '\n'.join(p for p in parts if p)


def _with_lines(text: str) -> list:
    """CodeBlock 文本里**独立一行**的 `with …` → `[(行号, 表达式), ...]`。"""
    lines = text.split('\n')
    offsets = []
    acc = 0
    for ln in lines:
        offsets.append(acc)
        acc += len(ln) + 1
    masked = _mask_non_code(text)
    markers = _block_markers()
    out = []
    for idx, ln in enumerate(lines):
        if markers and ln[:1] in markers:
            continue                           # `python:` 块体里不认
        pos = offsets[idx]
        m = re.match(r'[ \t]*with(?=[ \t])', masked[pos:])
        if not m:
            continue
        got = _read_with_expression(text, masked, pos + m.end())
        if got is None:
            continue
        expr, _end = got
        out.append((idx + 1, expr))
    return out


def _code_calls(text: str) -> list:
    """CodeBlock 文本里的 `renpy.transition(...)`（跳过 `python:` 块体行）。"""
    markers = _block_markers()
    if not markers:
        return parse_python_transition(text)
    lines = text.split('\n')
    safe = ['' if ln[:1] in markers else ln for ln in lines]
    return parse_python_transition('\n'.join(safe))


def beat_transitions(beat) -> list:
    """
    一拍 → `[TransitionCall, ...]`（纯读，不改 `Beat` 任何字段）。

    来源三路，按确定性顺序：
      1. `Beat.with_`（`show/scene … with X` 的子句 / Stage 列 `[转场]`）→
         `source='inline-with'`；
      2. CodeBlock 文本里独立一行的 `with X`（`Beat.raw` 通道）→
         `source='with'`；
      3. CodeBlock 里的 `$ renpy.transition(...)`（`Beat.code` 通道）→
         `source='renpy.transition'`。

    2/3 按源码行号排；`python:` 块体里的调用**不算**（块里可能是循环/
    条件，语义不是"这一拍固定过场"）。`line` 一律 0：beat 级别派生没有
    可靠的源码行号（`src_first` 只标行区间起点），不编一个假行号。

    **独立 `with` 必须留在结果里**（Lead 2026-10-30 拍板）：真机
    `chapter3_1` 有 35 处只有独立 `with`、没有 `Beat.with_`，漏掉就整章
    丢过场。已知限制见模块 docstring（`hide … with …` 不派生）。
    """
    out = []
    with_text = str(getattr(beat, 'with_', '') or '').strip()
    if with_text:
        for call in parse_with_clause(with_text):
            out.append(replace(call, source=SOURCE_INLINE_WITH, line=0))
    text = _composed_code_text(beat)
    if not text:
        return out
    found = []
    for line_no, expr in _with_lines(text):
        call = _with_call(expr, SOURCE_WITH, line_no)
        if call is not None:
            found.append((line_no, 0, call))
    for order, call in enumerate(_code_calls(text)):
        found.append((call.line, 1 + order, call))
    found.sort(key=lambda item: (item[0], item[1]))
    out.extend(replace(call, line=0) for _line, _order, call in found)
    return out


__all__ = [
    'SOURCE_RENPY', 'SOURCE_WITH', 'SOURCE_INLINE_WITH', 'SOURCE_CLAUSE',
    'SOURCE_CALL',
    'Expr', 'LiteralExpr', 'NameExpr', 'CallExpr', 'ListExpr', 'TupleExpr',
    'NonLiteral', 'TransitionCall',
    'parse_expression', 'normalize_expression',
    'parse_python_transition', 'parse_with_clause', 'beat_transitions',
]
