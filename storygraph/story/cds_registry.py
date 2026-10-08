"""
`storygraph.story.cds_registry`
===============================
**去读项目自己公布的"语法说明书"**（M2，2026-10-07）。

背景（设计文档 §1.4）：Ren'Py 官方从 6.x 起就有 **Creator-Defined
Statements (CDS)**：

```renpy
python early:
    def parse_mycue(lexer):
        ...
    renpy.register_statement(
        name="mycue battle",
        parse=parse_mycue,
        block=True,
        lint=lint_mycue,
    )
```

规矩是这份注册**必须写在 `python early:` 块里**，而且要早于使用它的文件加载。
换句话说 —— **项目已经把自定义语句的形状写在自己仓库里了。**

而工具从前把 `early` 整块当"不编辑的容器"跳过（`project_conventions.
_CONTAINER_HEADS` 里有它），于是"说明书在手边，工具选择不看"。

这一层的职责**只有一个**：把那些注册调用**读出来**，变成结构化的
`StatementSpec`。它是**纯读**的 —— 不碰 .rpy、不改行为、不接渲染。
先把结论接进体检报告，让编剧看到"工具其实愿意被教"；真正拿它去解析/渲染
是 M3 的事。

设计取舍
--------
- **不 eval 项目代码**（与 `layered_model` 同一条纪律）：只做**字面量**提取。
  参数写成变量（`name=MY_NAME`）就老实说"认不出"，**绝不猜**。
- **括号配平**而不是按行切：注册调用几乎总是多行写的。
- 找不到/认不出 → 返回空列表或跳过那一条，**不抛异常**（一个手写坏的
  Python 片段不该让项目打不开）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: 注册函数名（Ren'Py 官方就这一个入口）
REGISTER_CALL = 'register_statement'

#: 扫描脚本时跳过的目录（与 `project_conventions` / `scan_assets` 同源口径）
_SKIP_DIRS = frozenset({'tmp', 'build', 'build_out', '.git', '__pycache__',
                        'saves', 'dist'})

_STR_DQ = re.compile(r'"((?:[^"\\]|\\.)*)"')
_STR_SQ = re.compile(r"'((?:[^'\\]|\\.)*)'")


@dataclass
class StatementSpec:
    """
    一条自定义语句的**形状**（只由宿主注册信息决定，不含我们的猜测）。

    `source` 一律是 `'host-registry'`：报告里要能区分"这是项目自己说的"
    vs "我猜的"（`Conventions.source` 同一套口径）。
    """

    name: str = ''                    # 主名（`name=` 里的第一个词）
    aliases: list = field(default_factory=list)   # 全部名字（含主名）
    block: str = 'no'                 # 'no' | 'yes' | 'possible'
    init: bool = False
    translatable: bool = False
    #: 注册里给了哪些回调 —— 只作信息（我们不执行它们）
    hooks: list = field(default_factory=list)
    source_file: str = ''
    source_line: int = 0
    source: str = 'host-registry'
    #: 认得出来才有；认不出（如 `name=SOME_VAR`）留空并记 `note`
    note: str = ''

    @property
    def has_block(self) -> bool:
        return self.block in ('yes', 'possible')

    def to_dict(self) -> dict:
        return {'name': self.name, 'aliases': list(self.aliases),
                'block': self.block, 'init': self.init,
                'translatable': self.translatable,
                'hooks': list(self.hooks),
                'file': self.source_file, 'line': self.source_line,
                'source': self.source, 'note': self.note}


# ---------------------------------------------------------------------------
#  调用提取（括号配平 + 字面量）
# ---------------------------------------------------------------------------

def _match_paren(text: str, open_idx: int) -> int:
    """
    `text[open_idx]` 是 `(` → 返回配对 `)` 的下标；配不平返回 `-1`。

    跳过字符串字面量内的括号（`name="a(b"` 不该乱配平）；
    `#` 之后到行尾算注释。
    """
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


def _iter_register_calls(text: str):
    """产出 `(调用体文本, 0-based 行号)`；调用体 = 括号**内**的内容。"""
    for m in re.finditer(r'\b' + REGISTER_CALL + r'\s*\(', text):
        open_idx = text.index('(', m.start())
        close_idx = _match_paren(text, open_idx)
        if close_idx < 0:
            continue
        line_no = text.count('\n', 0, m.start()) + 1
        yield text[open_idx + 1:close_idx], line_no


def _top_level_args(body: str) -> list:
    """
    把调用体按**顶层逗号**切开（跳过嵌套括号与字符串）。

    不切的话 `parse=lambda lex: f(a, b)` 会被切成两条，`name=` 就找不到了。
    """
    parts = []
    depth = 0
    quote = ''
    cur = []
    i = 0
    while i < len(body):
        ch = body[i]
        if quote:
            cur.append(ch)
            if ch == '\\':
                if i + 1 < len(body):
                    cur.append(body[i + 1])
                    i += 2
                    continue
            elif ch == quote:
                quote = ''
            i += 1
            continue
        if ch in '"\'':
            quote = ch
            cur.append(ch)
            i += 1
            continue
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif ch == ',' and depth == 0:
            parts.append(''.join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    parts.append(''.join(cur))
    return [p.strip() for p in parts if p.strip()]


def _string_literal(src: str):
    """这个片段**整体**是不是一个字符串字面量？是就返回它的值，否则 None。"""
    s = src.strip()
    for rx in (_STR_DQ, _STR_SQ):
        m = rx.fullmatch(s)
        if m:
            return m.group(1)
    return None


def _kwarg(args: list, key: str):
    """取 `key=value` 的 value 原文（没有返回 None）。"""
    prefix = key + '='
    for a in args:
        if a.startswith(prefix):
            return a[len(prefix):].strip()
    return None


def spec_from_call(body: str, filename: str = '',
                   line_no: int = 0) -> 'StatementSpec | None':
    """
    一个 `register_statement(...)` 的调用体 → `StatementSpec`（认不出返回 None）。

    名字可以来自关键字（`name="a b"`）或第一个位置参数（`"a"`）——
    两种官方都支持。**只认字面量**：`name=SOME_CONST` 会留下 `note` 而不是猜。
    """
    args = _top_level_args(body)
    if not args:
        return None

    # 名字的两个官方来源：`name=` 关键字，或**第一个位置参数**。
    # 两者都**只认字符串字面量** —— `name=SOME_CONST` 时返回 None（跳过）。
    #
    # ⚠ 这里刻意"宁可跳过，也不猜"。猜错的名字会一路进 M3 的解析分派：
    # 工具会把 `SOME_CONST ...:` 这类**不是自定义语句**的行当成自定义语句，
    # 那正是本仓库最怕的一类缺陷（静默改语义）。说"不知道"只是少一层能力，
    # 猜错是**写坏别人的脚本**。
    # ⚠ 两个来源都试：`name=` 认不出来时**继续看位置参数**。
    # 这不是"猜"—— `renpy.register_statement` 的签名第一个位置参数
    # **就是** name，所以任何一个读到了都是真名字。
    # 两处都不是字面量才放弃。
    names_src = None
    raw_name = _kwarg(args, 'name')
    if raw_name is not None:
        names_src = _string_literal(raw_name)
    if names_src is None:
        names_src = _string_literal(args[0])
    if names_src is None:
        return None

    names = [n for n in names_src.split() if n]
    if not names:
        return None
    spec = StatementSpec(name=names[0], aliases=names,
                         source_file=filename, source_line=line_no)
    _fill_flags(spec, args, body)
    return spec


def _fill_flags(spec: StatementSpec, args: list, body: str) -> None:
    """`block=` / `init=` / `translatable=` / 回调名 —— 都只认字面量。"""
    raw_block = _kwarg(args, 'block')
    if raw_block is not None:
        lit = _string_literal(raw_block)
        if lit is not None and lit.lower() == 'possible':
            spec.block = 'possible'
        elif raw_block.strip() == 'True':
            spec.block = 'yes'
        elif raw_block.strip() == 'False':
            spec.block = 'no'
        else:
            # `block=NEEDS_BLOCK` 这种认不出：保守当成"可能有块"
            spec.block = 'possible'
            spec.note = (spec.note or '') + '；`block=` 不是字面量，'
            spec.note = spec.note.lstrip('；')
    for flag in ('init', 'translatable'):
        raw = _kwarg(args, flag)
        if raw is not None and raw.strip() == 'True':
            setattr(spec, flag, True)
    # 回调：只记"给了哪些"，不求值
    for hook in ('parse', 'lint', 'execute', 'predict', 'next', 'scry'):
        if _kwarg(args, hook) is not None:
            spec.hooks.append(hook)


# ---------------------------------------------------------------------------
#  扫项目
# ---------------------------------------------------------------------------

def parse_cds_calls(text: str, filename: str = '') -> list:
    """一份 .rpy 文本里的全部注册调用 → `[StatementSpec, ...]`。"""
    out = []
    for body, line_no in _iter_register_calls(str(text or '')):
        spec = spec_from_call(body, filename=filename, line_no=line_no)
        if spec is not None and spec.name:
            out.append(spec)
    return out


def find_scripts(project_root) -> list:
    """项目里该扫的 `.rpy`（排除构建产物、嵌套副本、缓存目录）。"""
    root = Path(project_root)
    game = root / 'game' if (root / 'game').is_dir() else root
    out = []
    for p in sorted(game.rglob('*.rpy')):
        try:
            rel = p.relative_to(game)
        except ValueError:            # 理论上不会；真发生了就别猜
            continue
        rel_parts = rel.parts
        # ⚠ **只看"扫描根之内"的目录段**（`rel_parts[:-1]` = 目录，末位是文件名）。
        #
        # 2026-10-08 修：原来是 `any(... for part in p.parts)` —— 拿**绝对路径**
        # 逐段比。于是只要**祖先目录**里有一个以 `.` 开头（`.pip-tmp` / `.venv` /
        # 用户把项目放在 `~/.games/mygame`），**整个项目的脚本全被跳过**：
        # CDS 认不出来、自定义语句一条都发现不了，而且**没有任何报错**
        # （表现为"这项目好像没声明过自定义语句"）。
        # 项目根之外的目录名不归本函数管：只看根内。
        if any(part in _SKIP_DIRS or part.startswith('.')
               for part in rel_parts[:-1]):
            continue
        if p.name.endswith('.generated.rpy'):
            continue
        # 嵌套副本（子目录自带 game/）不是本项目的脚本
        if 'game' in rel_parts and rel_parts[0] != 'game':
            continue
        out.append(p)
    return out


def scan_project_cds(project_root, limit_files: int = 0) -> list:
    """
    扫全项目，返回**去重后**的 `StatementSpec`（同名只留第一条并记来源）。

    `limit_files` 给了就只扫前 N 个文件（探针/大项目预览用）。
    """
    specs: dict = {}
    files = find_scripts(project_root)
    if limit_files:
        files = files[:limit_files]
    for f in files:
        try:
            text = f.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for spec in parse_cds_calls(text, filename=str(f)):
            if spec.name not in specs:
                specs[spec.name] = spec
    return list(specs.values())


def as_spec(item) -> 'StatementSpec | None':
    """
    `StatementSpec` 或它的 `to_dict()` 形态 → `StatementSpec`（别的一律 None）。

    为什么要有这个：体检报告是**跨进程/跨层**传递的（`EditorSession` 把
    dict 交给 `app.asset_report` 那个纯函数），沿路都不该依赖 dataclass
    实例。与其在两处各写一遍字段映射，不如在这里收口。
    """
    if isinstance(item, StatementSpec):
        return item
    if isinstance(item, dict):
        try:
            return StatementSpec(
                name=item.get('name') or '',
                aliases=list(item.get('aliases') or []),
                block=item.get('block') or 'no',
                init=bool(item.get('init')),
                translatable=bool(item.get('translatable')),
                hooks=list(item.get('hooks') or []),
                source_file=item.get('file') or item.get('source_file') or '',
                source_line=int(item.get('line')
                                or item.get('source_line') or 0),
                source=item.get('source') or 'host-registry',
                note=item.get('note') or '')
        except (TypeError, ValueError):
            return None
    return None


def format_cds_report(specs) -> str:
    """
    `[StatementSpec | dict]` → 人话（体检报告用）。**结论在第一条**。

    报告口径与 `unknown_statements` 一致：说清楚"项目声明了哪些我该认识的
    结构"，以及哪些**还没被工具接住**。
    """
    specs = [s for s in (as_spec(x) for x in (specs or ()))
             if s is not None and s.name]
    if not specs:
        return ('项目没有用 `renpy.register_statement` 声明自定义语句'
                '（CDS）。自定义结构若有，只能靠 storygraph.rules.json '
                '或形状识别。')
    lines = [f'项目用 CDS 声明了 {len(specs)} 个自定义语句：'
             + '、'.join(s.name for s in specs)]
    for s in specs:
        extra = []
        if len(s.aliases) > 1:
            extra.append('别名 ' + '/'.join(s.aliases[1:]))
        extra.append('带块' if s.has_block else '不带块')
        if s.init:
            extra.append('init 期')
        if s.translatable:
            extra.append('可翻译')
        if s.hooks:
            extra.append('回调 ' + ','.join(s.hooks))
        where = f'{Path(s.source_file).name}:{s.source_line}' \
            if s.source_file else '?'
        note = f'  ⚠ {s.note}' if s.note else ''
        lines.append(f'  · {s.name}（{"；".join(extra)}）  {where}{note}')
    return '\n'.join(lines)


__all__ = ['StatementSpec', 'REGISTER_CALL', 'spec_from_call',
           'parse_cds_calls', 'find_scripts', 'scan_project_cds',
           'as_spec', 'format_cds_report']
