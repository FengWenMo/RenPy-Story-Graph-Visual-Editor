"""
`storygraph.core.cst`
====================
**无损语法树的支点**（D3，2026-10-07）。

为什么要有这一层
----------------
在此之前，"逐字节保真"是**一条后门**：`RawNode` / `FileBlocks` /
`# TODO` / `Beat.opaque` —— 每来一条新的保真需求就再加一个。而 Roslyn 的经验是
（`docs/archive/设计_自定义语法适应架构_2026-10-07.md` §2）：**全保真应该是语法树的属性**
—— 每个节点带着自己的**原文区间**与**前导 trivia**，于是"把树写回原文"是
**结构上可能**的，而不是靠各条路各自小心。

这一层给人的东西很小、但很关键：

    `Span`（哪个文件的哪几行）+ `raw_text`（那几行的**原文**）+ `trivia`

于是每个语句都能回答："**我在原文里是第几行到第几行、原文是什么。**"
`statement_text(stmt)` 因此不需要渲染器、也不需要再读文件 —— IR 自足。

⚠ **不许进 dataclass 字段**（重要）
----------------------------------
`Statement` 的相等与哈希是**内容指纹契约**（协作主键、增量对位都依赖它）。
把 `span` / `raw_text` 变成 dataclass 字段会同时改动
`_HASH_FIELDS` / `_FACTORY_FIELDS` / 每个消费者的相等语义 —— 那是把一次
保真改动变成一次全局契约变更。

所以这里沿用本仓库已有的做法（`Statement.nest_path`、`LogicalLine.inline_comment`
同款）：**挂成动态属性**。它们不参与相等/哈希，因此：

  · 现有测试一条都不用改；
  · 谁想要 CST 谁去取（`span_of` / `raw_text_of`），不要就完全感知不到；
  · 少了它，行为退回 D3 之前（**没有降级风险**）。

`Span` 的边界怎么定
-------------------
判据是**一条**（在 `attach_spans` 里实现）：这个节点拥有从它自己那一行起、
直到"**下一条语句的前一行**"或"**第一个顶格行**"为止的正文。

  · 为什么不是"把子树都算进来"：语句流是**扁平**的（`menu` 的选项体也是
    平铺的语句），按子树算会让父子区间**重叠**；重叠的区间在写回时必然打架。
    按"下一个语句之前"分，正好**不重不漏**。
  · 为什么遇到顶格行就停：label 内的语句都是缩进的，顶格行只可能是顶层
    定义（`define` / `screen` / 下一个 `label` …）—— 那些**不归我**，
    吞进来就是"改别人的脚本"。
  · 空行留在自己这一段里（`Trivia.blank_before` 另外记了上面有几行空行）。

⚠ **原文切片必须来自"解析时读到的那一份文本"**：`IncrementalRenPyAnalyzer`
把解析器读到的文本（`RenPyParser.source_texts()`）直接交给这里，
**不在挂载时再读一遍盘** —— 行号来自 AST、原文来自另一次读盘时，
只要中间文件被改写，真迹就会**贴错行**（那比"没有真迹"危险得多）。
`attach_spans` 的 `file_texts` 兜底读盘只是兼容老式缓存，不是主路。
"""

from __future__ import annotations

from dataclasses import dataclass

from storygraph.core.safeguard import swallow

#: 动态属性名（统一出口，别在别处手写字符串）
ATTR_SPAN = 'cst_span'
ATTR_RAW = 'cst_raw_text'
ATTR_TRIVIA = 'cst_trivia'


@dataclass(frozen=True)
class Span:
    """一段**原文**区间（1-based、闭区间）。`end` 是**行号**不是字节偏移。"""

    file: str = ''
    start: int = 0
    end: int = 0

    @property
    def lines(self) -> int:
        return max(0, self.end - self.start + 1)

    @property
    def ok(self) -> bool:
        return bool(self.file) and self.start > 0 and self.end >= self.start

    def to_dict(self) -> dict:
        return {'file': self.file, 'start': self.start, 'end': self.end,
                'lines': self.lines}


@dataclass(frozen=True)
class Trivia:
    """
    语句的**前导 trivia**：它上面紧邻的空行 / 缩进原文。

    行尾注释与整行注释**不是** trivia —— 前者走隐藏列 `Inline`，
    后者是 `CommentNode`（**自己的节点**，有自己的 span）。
    把注释归成 trivia 会让"注释属于谁"变得含糊，而它现在是明确的。
    """

    blank_before: int = 0
    indent: str = ''

    def to_dict(self) -> dict:
        return {'blank_before': self.blank_before, 'indent': self.indent}


def attach(node, span: Span = None, raw_text: str = '',
           trivia: Trivia = None) -> None:
    """
    把 CST 信息挂到**任意** IR 对象上（`Statement` / AST 节点都行）。

    动态属性：不参与 dataclass 的相等 / 哈希 / 序列化 —— 见模块 docstring。
    """
    if span is not None:
        try:
            setattr(node, ATTR_SPAN, span)
        except (AttributeError, TypeError) as e:
            # `__slots__` / 冻结对象之类：**记下来**，别装作没发生
            # （本仓库的硬规矩：静默吞异常比报错坏得多）。
            swallow('core.cst.attach.span', e)
    if raw_text:
        try:
            setattr(node, ATTR_RAW, raw_text)
        except (AttributeError, TypeError) as e:
            swallow('core.cst.attach.raw', e)
    if trivia is not None:
        try:
            setattr(node, ATTR_TRIVIA, trivia)
        except (AttributeError, TypeError) as e:
            swallow('core.cst.attach.trivia', e)


def span_of(node):
    """`Span | None`（没有 CST 信息就是 None —— **不是**报错）。"""
    return getattr(node, ATTR_SPAN, None)


def raw_text_of(node) -> str:
    """这条语句在原文里的**逐字**文本（没有就空串）。"""
    return str(getattr(node, ATTR_RAW, '') or '')


def trivia_of(node):
    """`Trivia | None`。"""
    return getattr(node, ATTR_TRIVIA, None)


def has_cst(node) -> bool:
    """这个对象有没有带 CST 信息（覆盖率统计用）。"""
    sp = span_of(node)
    return bool(sp is not None and sp.ok and raw_text_of(node))


def statement_text(node) -> str:
    """
    这条语句的原文 —— **只用 IR 里的东西**，不再读文件。

    没有 CST 信息时退回 `payload['text']`（那是解析器给的规范化文本，
    形式上等价、但**不一定逐字节相同**）。调用方要判"是不是真迹"，
    请看 `has_cst()` —— 别把两者混为一谈。
    """
    raw = raw_text_of(node)
    if raw:
        return raw
    payload = getattr(node, 'payload', None)
    if isinstance(payload, dict):
        return str(payload.get('text') or '')
    return ''


def _source_of(statement):
    """语句的 `(文件, 起行)` —— `Statement.source` 是唯一的来源。"""
    src = getattr(statement, 'source', None)
    if isinstance(src, (tuple, list)) and src:
        f = str(src[0] or '')
        try:
            ln = int(src[1]) if len(src) > 1 else 0
        except (TypeError, ValueError):
            ln = 0
        return f, ln
    return '', 0


def attach_spans(statements, file_texts) -> int:
    """
    给语句流挂上 `Span` / `raw_text` / `Trivia`，返回挂上的条数。

    **归属规则**（一句话）：这条语句拥有从它自己那一行起、直到
    "下一条语句的前一行"或"第一个顶格行"为止的正文 —— 空行留给自己，
    顶格行结束这一段。

    为什么"到下一个语句之前"而不是"把子树都算进来"：语句流是**扁平**的
    （`menu` 的选项体也是平铺的语句），按子树算会让父子区间**重叠**；
    重叠的区间在写回时必然打架。按"下一个语句之前"分，正好**不重不漏**。

    为什么遇到**顶格行**就停：label 内的语句都是缩进的，顶格行只可能是
    顶层定义（`define` / `screen` / 下一个 `label` …）—— 那些**不归我**，
    把它们吞进来就是"改别人的脚本"。
    """
    by_file = {}
    for st in (statements or ()):
        f, ln = _source_of(st)
        if f and ln > 0:
            by_file.setdefault(f, []).append((ln, st))
    if not by_file:
        return 0

    def text_for(f):
        for key in (f, str(f)):
            if key in (file_texts or {}):
                return file_texts[key]
        try:
            from pathlib import Path
            return Path(f).read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError, TypeError):
            return ''

    n = 0
    for f, items in by_file.items():
        text = text_for(f)
        if not text:
            continue
        lines = text.split('\n')
        items.sort(key=lambda kv: kv[0])
        for idx, (ln, st) in enumerate(items):
            if ln > len(lines):
                continue
            nxt = items[idx + 1][0] if idx + 1 < len(items) else len(lines) + 1
            end = ln
            k = ln                     # 下一条**物理行**的 0-based 下标
            limit = max(ln, nxt - 1)
            while k < len(lines) and k < limit:
                cur = lines[k]
                if cur.strip() and _indent_width(cur) == 0:
                    break              # 顶格行：不归我
                end = k + 1
                k += 1
            raw = '\n'.join(lines[ln - 1:end])
            blank = 0
            j = ln - 2                 # 上一行的 0-based 下标
            while j >= 0 and not lines[j].strip():
                blank += 1
                j -= 1
            head = lines[ln - 1]
            attach(st, Span(file=f, start=ln, end=end), raw,
                   Trivia(blank_before=blank,
                          indent=head[:len(head)
                                     - len(head.lstrip(' \t'))]))
            n += 1
    return n


def _indent_width(line: str) -> int:
    return len(line) - len(line.lstrip(' \t'))


def overlaps(statements) -> list:
    """
    有没有**重叠的 span**？（同一文件里，两条语句占了同一行）

    重叠 = 写回时必然打架 = 这个模型还没准备好做"IR 重建原文"。
    所以它是 D3 的**验收判据之一**，不是可选的体检项。
    """
    by_file = {}
    for st in (statements or ()):
        sp = span_of(st)
        if sp is None or not sp.ok:
            continue
        by_file.setdefault(sp.file, []).append(sp)
    bad = []
    for f, spans in by_file.items():
        spans.sort(key=lambda s: s.start)
        for a, b in zip(spans, spans[1:]):
            if b.start <= a.end:
                bad.append((a.to_dict(), b.to_dict()))
    return bad


def cst_coverage(nodes, source_text: str = '') -> dict:
    """
    CST 覆盖率（报告用）：多少条语句带真迹、多少条只有规范化文本。

    `source_text` 给了就**额外核对**"`raw_text` 真的在那份原文里逐字出现"
    —— 这是"真迹"两个字唯一的硬证据（切片对不上就是**贴错了行**）。
    """
    total = verified = with_raw = bad = 0
    for n in (nodes or ()):
        total += 1
        raw = raw_text_of(n)
        if not raw:
            continue
        with_raw += 1
        if not source_text:
            verified += 1
            continue
        if raw in source_text:
            verified += 1
        else:
            bad += 1
    return {'total': total, 'with_raw': with_raw,
            'verified': verified, 'mismatch': bad,
            'ratio': (verified / total) if total else 1.0}


def format_cst_report(stats: dict, label: str = '语句') -> str:
    """CST 覆盖率 → 人话（结论在第一条）。"""
    stats = stats or {}
    total = int(stats.get('total') or 0)
    if not total:
        return f'没有{label}可核对。'
    pct = (stats.get('ratio') or 0) * 100
    head = (f'{label}原文真迹覆盖率 {pct:.1f}%'
            f'（{stats.get("verified", 0)}/{total} 条带逐字原文）')
    if stats.get('mismatch'):
        head += (f' ⚠ 其中 {stats["mismatch"]} 条的原文**在源文件里找不到**'
                 f'（那是贴错行，比没有更危险）')
    return head


__all__ = ['ATTR_SPAN', 'ATTR_RAW', 'ATTR_TRIVIA', 'Span', 'Trivia',
           'attach', 'span_of', 'raw_text_of', 'trivia_of', 'has_cst',
           'statement_text', 'attach_spans', 'overlaps', 'cst_coverage',
           'format_cst_report']
