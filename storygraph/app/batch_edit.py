# -*- coding: utf-8 -*-
"""
通用批处理：按**规则**批量插入 / 修改 / 删除 `.rpy` 里的一行或一段
（默认只预演）。

## 为什么是"规则"而不是"给某个写法开特例"

需求方要的是"自定义单行代码 + 整章批处理"，但下一次可能是
`$ camera(...)`、`$ msg_add(...)`、`play sound ...` 或项目自己的
`mycue ...`。所以这一层只认四件事：

  · **对谁**（`when`：这一拍有没有立绘 / 有没有台词 / 有没有转场 …）；
  · **做什么**（`insert` / `replace` / `delete`）；
  · **范围 / 位置**（`placement` / `scope`，以及 `span`：单行、整块、到某行为止）；
  · **什么内容**（`text`：原样一行或几行，不解析、不改写）。

规则文件是项目自己的（默认 `<项目>/storygraph.batch.json`），不是把
`renpy.transition` 写死在代码里。

## 和现有写盘路径的关系

只做**源文件级的行/段编辑**：读原文 → 按规则算出 insert/replace/delete →
原子替换。不重生成、不改拍模型、不动 Excel；写完重载，解析器自然把
新行收进 `Beat.code`。写盘仍然只有一条路：
`core/atomicio.write_text_atomic`（带备份）。

## 判据

1. 文件不是 UTF-8 → 拒绝（`source_style.apply(source=…)` 会先严格解码）；
2. 计划之后源文件又被改过 → 拒绝（`old_text` 必须逐字相同）；
3. 找不到锚点 / 匹配行 / 结束行 → 跳过并报告；
4. 同一行 / 同一段被多条规则改 → 拒绝整份文件（不猜先后）；
5. 单行规则不碰块头（`python:` / `if:` / `show …:`）；要动整块必须
   显式写 `span`，且范围要覆盖整个块体，否则跳过（不留下孤儿缩进）；
6. 规则里有不认识的键 → 整个规则文件拒绝（不猜）。
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.vocabulary import NON_ACTOR_TAGS, SHOW_INTRO_KEYWORDS
from storygraph.story.source_style import read_text

SCHEMA = 'storygraph.batch/1'
PLAN_SCHEMA = 'storygraph.batch.plan/1'

PLACEMENTS = ('after_last_show', 'after_last_change', 'after_beat', 'before_beat')
SCOPES = ('beat', 'after_last_show', 'after_last_change')
SPAN_MODES = ('', 'block', 'until')
OCCURRENCES = ('first', 'last', 'all')
MATCH_MODES = ('exact', 'contains', 'regex')
TAG_KINDS = ('sprite', 'any')

_WHEN_KEYS = frozenset({
    'show', 'hide', 'change', 'say', 'transition', 'text_contains', 'speaker',
    'label', 'code_contains', 'code_not_contains',
})
_INSERT_KEYS = frozenset({'text', 'placement', 'tag_kind', 'skip_if_present'})
_REPLACE_KEYS = frozenset({
    'scope', 'match', 'text', 'append', 'occurrence', 'tag_kind',
    'skip_if_present', 'span',
})
_DELETE_KEYS = frozenset({'scope', 'match', 'occurrence', 'tag_kind', 'span'})
_RULE_KEYS = frozenset({'id', 'when', 'insert', 'replace', 'delete'})

_LINE_KIND = re.compile(r'^([ \t]*)(show|hide)[ \t]+(\S+)')
_BLOCK_TAIL = re.compile(r':[ \t]*(?:#.*)?$')


class BatchRuleError(ValueError):
    """规则文件写错 / 不认识的键。**不猜**，整份拒绝。"""


@dataclass(frozen=True)
class CueRule:
    """一条"对谁、做什么、在哪、写什么"的规则。

    `insert` / `replace` / `delete` 三选一；内部统一成 `op`，
    `match` / `scope` / `occurrence` 只对后两种有意义。
    """

    id: str
    op: str = 'insert'
    text: str = ''
    #: `replace.append` 用：**追加到匹配行末尾**的那一小段（含前导空格）。
    #: 为什么单列一个字段（2026-11-08）：`play music "x"` 要变成
    #: `play music "x" fadein 1.0` —— 原行必须原样保留，只加尾巴；
    #: `text` 是"整行替换"，拿它拼原行要么丢内容、要么得支持正则反向引用
    #: （那会让每条规则都变成模板，误伤面太大）。
    append: str = ''
    placement: str = 'after_last_show'
    tag_kind: str = 'sprite'
    skip_if_present: bool = True
    scope: str = 'beat'
    match: dict = field(default_factory=dict)
    occurrence: str = 'first'
    span: str = ''
    until: dict = field(default_factory=dict)
    when: dict = field(default_factory=dict)
    source: str = ''

    def to_dict(self) -> dict:
        body = {}
        if self.op == 'insert':
            body = {
                'text': _text_json(self.text),
                'placement': self.placement,
                'tag_kind': self.tag_kind,
                'skip_if_present': self.skip_if_present,
            }
        elif self.op == 'replace':
            body = {
                'scope': self.scope,
                'match': dict(self.match),
                'occurrence': self.occurrence,
                'tag_kind': self.tag_kind,
                'skip_if_present': self.skip_if_present,
            }
            if self.append:
                body['append'] = self.append
            else:
                body['text'] = _text_json(self.text)
        elif self.op == 'delete':
            body = {
                'scope': self.scope,
                'match': dict(self.match),
                'occurrence': self.occurrence,
                'tag_kind': self.tag_kind,
            }
        if self.op in ('replace', 'delete') and self.span:
            body['span'] = ('block' if self.span == 'block'
                            else {'until': dict(self.until)})
        return {
            'id': self.id,
            'when': dict(self.when),
            self.op: body,
            'source': self.source,
        }


@dataclass
class Insertion:
    """一条待插入的内容（一行或多行；纯计划，不碰磁盘）。"""

    line: int                  # 1 基；after=插在这一行之后，before=插在这一行之前
    text: str                  # 多行用 \n 连接；续行的缩进是相对于锚点的
    indent: str
    seq: int
    rule_id: str
    before: bool = False

    @property
    def text_lines(self) -> list:
        return self.text.split('\n')

    def to_dict(self) -> dict:
        return {
            'line': self.line,
            'before': self.before,
            'text': _text_json(self.text),
            'seq': self.seq,
            'rule': self.rule_id,
        }


@dataclass
class LineEdit:
    """一条 replace / delete（可覆盖一行或连续多行；纯计划，不碰磁盘）。"""

    kind: str                  # replace | delete
    line: int                  # 1 基，起始行
    end_line: int = 0          # 1 基，结束行（含）；0 = 与 line 相同
    old: str = ''
    old_lines: list = field(default_factory=list)
    text: str = ''             # replace 的新内容（不含基准缩进，可多行）
    append: str = ''           # replace.append：追加到原行末尾的一小段
    indent: str = ''
    seq: int = 0
    rule_id: str = ''

    @property
    def last_line(self) -> int:
        return self.end_line or self.line

    @property
    def span_lines(self) -> int:
        return max(1, self.last_line - self.line + 1)

    def to_dict(self) -> dict:
        return {
            'kind': self.kind,
            'line': self.line,
            'end_line': self.last_line,
            'lines': self.span_lines,
            'old': self.old,
            'text': _text_json(self.text),
            'seq': self.seq,
            'rule': self.rule_id,
        }


@dataclass
class FilePlan:
    path: Path
    rel: str
    old_text: str = ''
    insertions: list = field(default_factory=list)
    edits: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    error: str = ''

    @property
    def total(self) -> int:
        return len(self.insertions) + len(self.edits)

    def to_dict(self) -> dict:
        return {
            'path': self.rel,
            'insertions': [x.to_dict() for x in self.insertions],
            'edits': [x.to_dict() for x in self.edits],
            'skipped': list(self.skipped),
            'error': self.error,
        }


@dataclass
class BatchPlan:
    root: Path
    rules: list
    files: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def pending(self) -> int:
        return sum(f.total for f in self.files)

    def to_dict(self) -> dict:
        return {
            'schema': PLAN_SCHEMA,
            'pending': self.pending,
            'rules': [r.to_dict() for r in self.rules],
            'files': [f.to_dict() for f in self.files],
            'errors': list(self.errors),
        }


# ---------------------------------------------------------------------------
#  规则文件
# ---------------------------------------------------------------------------

def load_rule_file(path) -> list:
    """读一份规则 JSON → `[CueRule]`。坏规则整份拒绝。"""
    import json

    p = Path(path)
    try:
        # 规则文件常被 Windows 编辑器存成带 BOM 的 UTF-8；utf-8-sig 两种都收。
        data = json.loads(p.read_text(encoding='utf-8-sig'))
    except OSError as exc:
        raise BatchRuleError(f'读不了规则文件：{p}（{exc}）') from exc
    except UnicodeError as exc:
        raise BatchRuleError(f'规则文件不是 UTF-8：{p}（{exc}）') from exc
    except ValueError as exc:
        raise BatchRuleError(f'规则文件不是合法 JSON：{p}（{exc}）') from exc
    if not isinstance(data, dict):
        raise BatchRuleError(f'规则文件的顶层必须是对象：{p}')
    if str(data.get('schema') or '') != SCHEMA:
        raise BatchRuleError(
            f'规则文件 schema 必须是 {SCHEMA!r}：{p}')
    raw_rules = data.get('rules')
    if not isinstance(raw_rules, list) or not raw_rules:
        raise BatchRuleError(f'规则文件里没有 rules：{p}')
    out = []
    seen = set()
    for i, raw in enumerate(raw_rules):
        rule = _parse_rule(raw, f'{p} rules[{i}]')
        if rule.id in seen:
            raise BatchRuleError(f'规则 id 重复：{rule.id!r}（{p}）')
        seen.add(rule.id)
        out.append(rule)
    return out


def parse_rule(raw, where: str = 'rule') -> CueRule:
    """
    `{...}` → `CueRule`（**唯一的规则校验**；界面表单也走这里）。

    为什么把私有那个包一层：可视化表单会现拼规则，如果它自己再写一遍校验，
    迟早出现"界面说合法、引擎说不合法"（或者反过来）—— 那就是两份真源。
    """
    return _parse_rule(raw, where)


def parse_rules(raw_rules, where: str = 'rules') -> list:
    """
    一串**原始 dict** → `[CueRule]`（例如扩展点 `batchable` 产出的那几条）。

    为什么需要这一步（2026-11-07 探针实测）：`ExtensionRegistry.cue_rules()`
    产出的是普通 dict，而 `plan_batch` 要的是 `CueRule` —— 直接把前者喂
    进去会 `AttributeError: 'dict' object has no attribute 'when'`。
    也就是说"批处理开关"在界面上看着能开，真跑批处理时**断在这里**。
    现在断口补上：不管规则从哪来（文件 / 表单 / 扩展点），都过同一个
    `_parse_rule`，校验只有一份。
    """
    out = []
    for index, raw in enumerate(raw_rules or ()):
        if isinstance(raw, CueRule):
            out.append(raw)                 # 已经是规则对象：幂等
            continue
        out.append(_parse_rule(raw, f'{where}[{index}]'))
    return out


def rules_from_extensions(project_root) -> tuple:
    """
    项目里**开了批处理**的扩展点 → `([CueRule], [problems])`。

    这是「是否能够批处理」那个开关的**唯一消费口**：开关关着 → `cue_rules()`
    不产出 → 这里一条都没有（不会偷偷跑）。坏规则不连坐：一条坏了记进
    `problems`，其余照常返回（与插件装载同一条纪律）。

    ⚠ 会装载扩展层（含 `extensions/*.py` 插件）—— 所以这里是"点一下预演"
      才走的路径，不是逐章热路径。
    """
    try:
        from storygraph.ext.registry import ExtensionRegistry
    except ImportError:                     # 后端被裁剪过
        return [], []
    registry = ExtensionRegistry.for_project(project_root)
    rules, problems = [], []
    for raw in registry.cue_rules():
        try:
            rules.append(parse_rule(raw, f'扩展点 {raw.get("id")!r}'))
        except BatchRuleError as exc:
            problems.append(str(exc))
    return rules, problems


def _same_rule(left: CueRule, right: CueRule) -> bool:
    """两条规则是不是**同一个写法**（`source` 不算：那是来处，不是内容）。"""
    a = left.to_dict()
    b = right.to_dict()
    a.pop('source', None)
    b.pop('source', None)
    return a == b


def merge_declared_rules(rules, declared, *, where: str = '扩展声明') -> tuple:
    """
    已有规则 + 声明出来的规则 → `([CueRule], [要人看一眼的话])`。

    这是「是否能够批处理」那个开关**接进批量执行**的那一步（2026-11-07 第三轮）。
    之前 `rules_from_extensions()` 只有判据在调：扩展窗口里把开关打开、点「预演」
    也能看到效果，但 CLI 的 `batch` 与批处理窗口读的是
    `storygraph.batch.json` / 界面上的规则 —— 声明的规则**从来没有进过这两条路**。
    也就是"开关看着能开、真跑起来少一条"。

    去重口径：**同名时留下原来那条**。原来那条来自文件/界面，是能改、能审、
    能进版本库的一份；声明是"我开了一个开关"。两者同名却内容不同时**说出来**
    （这正好是两份真源开始漂移的那一刻），但不算错误、不阻断计划 —— 否则
    一个人把规则抄进文件之后，整章都跑不了。
    """
    merged = list(rules or ())
    seen = {rule.id: rule for rule in merged}
    notes = []
    for rule in declared or ():
        old = seen.get(rule.id)
        if old is not None:
            if not _same_rule(old, rule):
                notes.append(f'{where}里也有一条 {rule.id!r}，写法不同 —— '
                             f'用的是原来那条（要改请改原来那条）')
            continue
        seen[rule.id] = rule
        merged.append(rule)
    return merged, notes


def _parse_rule(raw, where: str) -> CueRule:
    if not isinstance(raw, dict):
        raise BatchRuleError(f'{where} 必须是对象')
    unknown = set(raw) - _RULE_KEYS
    if unknown:
        raise BatchRuleError(f'{where} 有不认识的键：{sorted(unknown)}')
    rid = str(raw.get('id') or '').strip()
    if not rid:
        raise BatchRuleError(f'{where} 缺少 id')

    when = raw.get('when') or {}
    if not isinstance(when, dict):
        raise BatchRuleError(f'{where}.when 必须是对象')
    unknown = set(when) - _WHEN_KEYS
    if unknown:
        raise BatchRuleError(f'{where}.when 有不认识的键：{sorted(unknown)}')
    _check_when(when, f'{where}.when')

    ops = [key for key in ('insert', 'replace', 'delete') if key in raw]
    if len(ops) != 1:
        raise BatchRuleError(
            f'{where} 必须且只能有 insert / replace / delete 之一，'
            f'收到 {ops or "一个都没有"}')
    op = ops[0]
    body = raw.get(op)
    if not isinstance(body, dict):
        raise BatchRuleError(f'{where}.{op} 必须是对象')

    if op == 'insert':
        unknown = set(body) - _INSERT_KEYS
        if unknown:
            raise BatchRuleError(f'{where}.insert 有不认识的键：{sorted(unknown)}')
        text = _parse_text(body.get('text'), f'{where}.insert.text')
        placement = str(body.get('placement') or 'after_last_show')
        if placement not in PLACEMENTS:
            raise BatchRuleError(
                f'{where}.insert.placement 只支持 {PLACEMENTS}，'
                f'收到 {placement!r}')
        tag_kind = _parse_tag_kind(
            body.get('tag_kind'), f'{where}.insert.tag_kind')
        return CueRule(id=rid, op='insert', text=text, placement=placement,
                       tag_kind=tag_kind,
                       skip_if_present=bool(body.get('skip_if_present', True)),
                       when=dict(when), source=where)

    if op == 'replace':
        return _parse_replace_body(rid, body, when, where)

    unknown = set(body) - _DELETE_KEYS
    if unknown:
        raise BatchRuleError(f'{where}.delete 有不认识的键：{sorted(unknown)}')
    scope = _parse_scope(body.get('scope'), f'{where}.delete.scope')
    match = _parse_match(body.get('match'), f'{where}.delete.match')
    occurrence = _parse_occurrence(
        body.get('occurrence'), f'{where}.delete.occurrence')
    tag_kind = _parse_tag_kind(
        body.get('tag_kind'), f'{where}.delete.tag_kind')
    span, until = _parse_span(body.get('span'), f'{where}.delete.span')
    return CueRule(id=rid, op='delete', scope=scope, match=match,
                   occurrence=occurrence, tag_kind=tag_kind,
                   span=span, until=until,
                   when=dict(when), source=where)


def _parse_replace_body(rid: str, body: dict, when: dict, where: str) -> CueRule:
    """
    `replace` 的规则体 → `CueRule`（拆出来只为把 `_parse_rule` 的分支压回去，
    那条函数一直是复杂度棘轮边上那一个）。

    `text` 与 `append` **必须且只能有一个**：前者整行替换，后者原行末尾追加
    （2026-11-08 起；详见 `_parse_append`）。
    """
    unknown = set(body) - _REPLACE_KEYS
    if unknown:
        raise BatchRuleError(f'{where}.replace 有不认识的键：{sorted(unknown)}')
    scope = _parse_scope(body.get('scope'), f'{where}.replace.scope')
    match = _parse_match(body.get('match'), f'{where}.replace.match')
    span, until = _parse_span(body.get('span'), f'{where}.replace.span')
    has_text = 'text' in body
    has_append = 'append' in body
    if has_text == has_append:
        raise BatchRuleError(
            f'{where}.replace 必须且只能有 text / append 之一'
            f'（text = 整行替换；append = 原行末尾追加）')
    text = ('' if has_append
            else _parse_text(body.get('text'), f'{where}.replace.text'))
    append = ('' if has_text
              else _parse_append(body.get('append'),
                                 f'{where}.replace.append'))
    occurrence = _parse_occurrence(
        body.get('occurrence'), f'{where}.replace.occurrence')
    tag_kind = _parse_tag_kind(
        body.get('tag_kind'), f'{where}.replace.tag_kind')
    return CueRule(id=rid, op='replace', text=text, append=append,
                   scope=scope, match=match, occurrence=occurrence,
                   tag_kind=tag_kind, span=span, until=until,
                   skip_if_present=bool(body.get('skip_if_present', True)),
                   when=dict(when), source=where)


def _parse_append(raw, where: str) -> str:
    """
    `replace.append`：**追加到原行末尾**的一小段（含前导空格，原样）。

    与 `text` 的校验刻意不同：这里**允许**以空格开头（` fadein 1.0`），
    也允许是片段而不是完整语句 —— 它不是"写一行"，是"补在这行后面"。
    只挡三种真会写出坏脚本的：空、含换行、含 CR。
    """
    text = str(raw or '')
    if not text.strip():
        raise BatchRuleError(f'{where} 不能为空')
    if '\n' in text or '\r' in text:
        raise BatchRuleError(f'{where} 只能是单行（换行请用 text 整行替换）')
    return text


def _parse_text(raw, where: str) -> str:
    """
    规则里的正文：一个字符串，或字符串数组（多行）。

    约定：**第一行不能自带缩进**（基准缩进跟锚点/被替换首行走）；
    后续行的前导空格是*相对*缩进，会原样叠在基准缩进后面。
    段里出现块头（以 `:` 收尾）时，必须在本段内带一个缩进更深的块体，
    否则写进去就是孤儿块头。
    """
    if isinstance(raw, list):
        parts = []
        for i, item in enumerate(raw):
            if not isinstance(item, str):
                raise BatchRuleError(f'{where}[{i}] 必须是字符串')
            if '\n' in item or '\r' in item:
                raise BatchRuleError(f'{where}[{i}] 里不能有换行')
            parts.append(item)
        lines = parts
    else:
        text = str(raw or '')
        if '\r' in text:
            raise BatchRuleError(f'{where} 不能含 CR；换行统一用 LF')
        lines = text.split('\n')
    if not any(line.strip() for line in lines):
        raise BatchRuleError(f'{where} 不能为空')
    if not lines[0].strip():
        raise BatchRuleError(f'{where} 第一行不能是空行')
    if lines[0][:1] in (' ', '\t'):
        raise BatchRuleError(f'{where} 第一行不要自己带缩进（缩进跟锚点走）')
    _check_text_structure(lines, where)
    return '\n'.join(lines)


def _check_text_structure(lines: list, where: str) -> None:
    """块头必须在同一段正文里带上缩进更深的块体。"""
    for i, line in enumerate(lines):
        if not _is_block_header(line):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines) or _indent_of(lines[j]) <= _indent_of(line):
            raise BatchRuleError(
                f'{where} 第 {i + 1} 行是块头（以 : 结尾），'
                '但这段正文里没有缩进更深的块体；写进去就是语法错')


def _parse_span(raw, where: str):
    """`span`: 缺省=单行；`"block"`=整块；`{"until": match}`=到匹配行为止。"""
    if raw is None or raw is False or raw == '' or raw == 'line':
        return '', {}
    if raw == 'block':
        return 'block', {}
    if isinstance(raw, dict):
        unknown = set(raw) - {'until'}
        if unknown:
            raise BatchRuleError(f'{where} 有不认识的键：{sorted(unknown)}')
        if 'until' not in raw:
            raise BatchRuleError(
                f'{where} 对象写法必须带 until；整块请用 "span": "block"')
        until = _parse_match(raw.get('until'), f'{where}.until')
        return 'until', until
    raise BatchRuleError(
        f'{where} 只支持 "block" / {{"until": ...}} / 省略（单行），'
        f'收到 {raw!r}')


def _text_json(text: str):
    """报告里多行正文用数组，单行保持字符串。"""
    return list(text.split('\n')) if '\n' in text else text


def _parse_tag_kind(raw, where: str) -> str:
    tag_kind = str(raw or 'sprite')
    if tag_kind not in TAG_KINDS:
        raise BatchRuleError(
            f'{where} 只支持 {TAG_KINDS}，收到 {tag_kind!r}')
    return tag_kind


def _parse_scope(raw, where: str) -> str:
    scope = str(raw or 'beat')
    if scope not in SCOPES:
        raise BatchRuleError(f'{where} 只支持 {SCOPES}，收到 {scope!r}')
    return scope


def _parse_occurrence(raw, where: str) -> str:
    occurrence = str(raw or 'first')
    if occurrence not in OCCURRENCES:
        raise BatchRuleError(
            f'{where} 只支持 {OCCURRENCES}，收到 {occurrence!r}')
    return occurrence


def _parse_match(raw, where: str) -> dict:
    if isinstance(raw, str):
        raw = {'pattern': raw}
    if not isinstance(raw, dict):
        raise BatchRuleError(f'{where} 必须是字符串或对象')
    unknown = set(raw) - {'mode', 'pattern', 'flags'}
    if unknown:
        raise BatchRuleError(f'{where} 有不认识的键：{sorted(unknown)}')
    mode = str(raw.get('mode') or 'exact')
    if mode not in MATCH_MODES:
        raise BatchRuleError(f'{where}.mode 只支持 {MATCH_MODES}')
    pattern = str(raw.get('pattern') or '')
    if not pattern:
        raise BatchRuleError(f'{where}.pattern 不能为空')
    flags = str(raw.get('flags') or '')
    if flags not in ('', 'i'):
        raise BatchRuleError(f"{where}.flags 只支持 '' / 'i'")
    if mode == 'regex':
        try:
            re.compile(pattern, re.I if 'i' in flags else 0)
        except re.error as exc:
            raise BatchRuleError(f'{where}.pattern 不是合法正则：{exc}') from exc
    return {'mode': mode, 'pattern': pattern, 'flags': flags}


def _check_when(when: dict, where: str) -> None:
    for key, val in when.items():
        if val is None:
            raise BatchRuleError(f'{where}.{key} 不能是 null（不写这个键就是不过滤）')
    for key in ('show', 'hide', 'change'):
        val = when.get(key)
        if val is None:
            continue
        ok = val in ('any', 'none', 'sprite') or (
            isinstance(val, list) and all(isinstance(x, str) and x for x in val))
        if not ok:
            raise BatchRuleError(
                f'{where}.{key} 只支持 any / none / sprite / 字符串列表')
    if 'say' in when and not isinstance(when.get('say'), bool):
        raise BatchRuleError(f'{where}.say 必须是 true / false')
    if 'transition' in when and when.get('transition') not in ('any', 'none'):
        raise BatchRuleError(f'{where}.transition 只支持 any / none')
    for key in ('text_contains', 'label', 'code_contains', 'code_not_contains'):
        if key in when and not isinstance(when.get(key), str):
            raise BatchRuleError(f'{where}.{key} 必须是字符串')
    speaker = when.get('speaker')
    if speaker is not None and not (
            isinstance(speaker, str)
            or (isinstance(speaker, list)
                and all(isinstance(x, str) for x in speaker))):
        raise BatchRuleError(f'{where}.speaker 必须是字符串或字符串列表')


# ---------------------------------------------------------------------------
#  选谁 / 插哪
# ---------------------------------------------------------------------------

def _spec_tags(text) -> list:
    """`'lan aa happy; ye ...'` → `['lan', 'ye', ...]`。"""
    out = []
    for spec in str(text or '').split(';'):
        spec = spec.strip()
        if not spec:
            continue
        out.append(spec.split()[0].split('@')[0])
    return out


def _is_sprite_tag(tag: str, symbols) -> bool:
    """
    这个 tag 是不是**立绘**。

    有角色表 → 以 `Symbols.is_actor_tag` 为准（角色表是权威）；
    没有角色表 → 退到"排除已知非立绘"（bg / cg / screen / expression …）。
    这个降级是刻意的：批处理不该因为项目没建资源表就一行都选不中。
    """
    tag = str(tag or '').strip()
    if not tag or tag in NON_ACTOR_TAGS or tag in SHOW_INTRO_KEYWORDS:
        return False
    if symbols is not None and symbols.has('character'):
        return bool(symbols.is_actor_tag(tag))
    return True


def _show_matches(text, mode, symbols) -> bool:
    tags = _spec_tags(text)
    if mode == 'any':
        return bool(tags)
    if mode == 'none':
        return not tags
    if mode == 'sprite':
        return any(_is_sprite_tag(t, symbols) for t in tags)
    return any(t in mode for t in tags)


def _change_matches(beat, mode, symbols) -> bool:
    """`change` = show **或** hide：只看"这一拍有没有立绘变化"。"""
    tags = _spec_tags(beat.show) + _spec_tags(beat.hide)
    if mode == 'any':
        return bool(tags)
    if mode == 'none':
        return not tags
    if mode == 'sprite':
        return any(_is_sprite_tag(t, symbols) for t in tags)
    return any(t in mode for t in tags)


def _match_when(beat, when, symbols) -> bool:
    if not when:
        return True
    if not _matches_changes(beat, when, symbols):
        return False
    if 'say' in when and bool(str(beat.text or '').strip()) != bool(when['say']):
        return False
    if 'transition' in when:
        has = bool(_beat_transitions(beat))
        if when['transition'] == 'none' and has:
            return False
        if when['transition'] == 'any' and not has:
            return False
    return _matches_text_and_code(beat, when)


def _matches_changes(beat, when, symbols) -> bool:
    """`show` / `hide` / `change` 三条"这一拍有没有立绘动作"。"""
    if 'show' in when and not _show_matches(beat.show, when['show'], symbols):
        return False
    if 'hide' in when and not _show_matches(beat.hide, when['hide'], symbols):
        return False
    if 'change' in when and not _change_matches(beat, when['change'], symbols):
        return False
    return True


def _matches_text_and_code(beat, when) -> bool:
    """台词 / 说话人 / label / 代码片段。"""
    if 'text_contains' in when:
        if when['text_contains'] not in str(beat.text or ''):
            return False
    if 'speaker' in when:
        want = when['speaker']
        want = [want] if isinstance(want, str) else list(want)
        if str(beat.speaker or '') not in want:
            return False
    if 'label' in when:
        if not fnmatch.fnmatchcase(str(beat.label or ''), when['label']):
            return False
    if 'code_contains' in when:
        if when['code_contains'] not in _compose_code(beat):
            return False
    if 'code_not_contains' in when:
        if when['code_not_contains'] in _compose_code(beat):
            return False
    return True


def _beat_transitions(beat) -> list:
    try:
        from storygraph.story.transition_expr import beat_transitions
        calls = list(beat_transitions(beat))
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        calls = []
    if calls:
        return calls
    # `hide ye with dissolve` 被模型存成 `hide='ye@dissolve'`；
    # `beat_transitions` 不派生它（见 transition_expr 的已知限制），这里补上。
    for part in str(getattr(beat, 'hide', '') or '').split(';'):
        if '@' in part and part.split('@', 1)[1].strip():
            return [True]
    # `y "…" with dissolve` 的 `with` 在 `say_tail` 里，不在 Beat.with_ /
    # CodeBlock 里；`beat_transitions` 目前不收它，这里补上（只用于"有没有"）。
    tail = str(getattr(beat, 'say_tail', '') or '')
    if re.search(r'(?<!\w)with\s+\S', tail):
        return [True]
    return calls


def _compose_code(beat) -> str:
    try:
        from storygraph.story.beat_model import compose_code
        return str(compose_code(beat) or '')
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return str(getattr(beat, 'code', '') or '')


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(' \t'))


def _is_block_header(line: str) -> bool:
    """`if:` / `python:` / `show …:` / `menu:`；注释不算。"""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith('#') \
        and bool(_BLOCK_TAIL.search(line.rstrip()))


def _after_block(lines: list, idx: int, hi: int) -> int:
    """锚点行若以 `:` 收尾（show + ATL 块），返回块尾；否则就是它自己。"""
    if not _is_block_header(lines[idx]):
        return idx
    base = _indent_of(lines[idx])
    last = idx
    j = idx + 1
    while j < hi:
        if lines[j].strip():
            if _indent_of(lines[j]) <= base:
                break
            last = j
        j += 1
    return last


def _anchor(beat, placement: str, tag_kind: str, symbols, lines: list,
            statement_lines: set):
    """
    这一拍的插入锚点 → `(行下标 0 基, 缩进)`；找不到返回 `None`。

    `anchor` 是**源文件行下标**；`after_last_show` 取拍内最后一条符合
    tag_kind 的 show，`after_beat` / `before_beat` 取拍区间首尾。

    `statement_lines` = 解析器确认过的 `show` / `hide` **语句行号**（1 基）。
    文本行匹配必须过这一关：`python:` 块里的字符串/变量也可能以 `show `
    开头，只按文本认会往 Python 块里插一行，那是改坏脚本。
    """
    first = int(getattr(beat, 'src_first', 0) or 0)
    last = int(getattr(beat, 'src_last', 0) or 0)
    if first < 1 or last < first or last > len(lines):
        return None
    lo, hi = first - 1, last
    if placement in ('after_last_show', 'after_last_change'):
        kinds = ('show',) if placement == 'after_last_show' else ('show', 'hide')
        hit = None
        for i in range(lo, hi):
            if (i + 1) not in statement_lines:
                continue
            m = _LINE_KIND.match(lines[i])
            if not m or m.group(2) not in kinds:
                continue
            tag = m.group(3).split('@')[0]
            if tag_kind == 'sprite' and not _is_sprite_tag(tag, symbols):
                continue
            hit = i
        if hit is None:
            return None
        # 块体可能不在 beat 的 src 区间里（`python:` / ATL 都实测过），
        # 所以块尾要扫到整份文件，按缩进回流判断，不能卡在 src_last。
        return _after_block(lines, hit, len(lines)), \
            lines[hit][:_indent_of(lines[hit])]
    indices = [i for i in range(lo, hi) if lines[i].strip()]
    if not indices:
        return None
    if placement == 'before_beat':
        idx = indices[0]
        return idx, lines[idx][:_indent_of(lines[idx])]
    # `after_beat`：不能拿"最后一行"当锚点 —— 那一行可能是 `python:` /
    # `if:` / ATL 块的**块体**，在它的缩进上插一行会插进块里。
    # 取这一拍最后一个**顶层**行；如果它是块头，再落到整块后面。
    base = _indent_of(lines[indices[0]])
    top = [i for i in indices if _indent_of(lines[i]) == base]
    idx = top[-1] if top else indices[-1]
    return _after_block(lines, idx, len(lines)), lines[idx][:base]


def _already_present(lines: list, beat, text: str, anchor: int) -> bool:
    """整段正文已经连续出现过（插入幂等）。比较时忽略缩进。"""
    needles = [line.strip() for line in text.split('\n')]
    lo = max(0, int(getattr(beat, 'src_first', 1) or 1) - 1)
    hi = min(len(lines), int(getattr(beat, 'src_last', 0) or 0))
    for i in range(lo, hi):
        if _sequence_at(lines, i, needles):
            return True
    # 插在拍边界时，上一轮插进去的段落可能被算进了邻拍；只看锚点这一格
    # 和它后面一格（不往前看：前一拍的同名行不是本拍的，不能因此跳过）。
    for i in (anchor, anchor + 1):
        if _sequence_at(lines, i, needles):
            return True
    return False


def _sequence_at(lines: list, start: int, needles: list) -> bool:
    if start < 0 or start + len(needles) > len(lines):
        return False
    return all(lines[start + i].strip() == needles[i]
               for i in range(len(needles)))


def _match_line(line: str, match: dict) -> bool:
    mode = str(match.get('mode') or 'exact')
    pattern = str(match.get('pattern') or '')
    if mode == 'exact':
        return line.strip() == pattern.strip()
    if mode == 'contains':
        return pattern in line
    flags = re.I if 'i' in str(match.get('flags') or '') else 0
    return re.search(pattern, line, flags) is not None


_CONTROL_HEAD = re.compile(
    r'^(?:if|elif|else|while|for|python|menu)\b')


def _enclosing_control_head(lines: list, idx: int):
    """
    beat 首行若缩进在 `if:` / `python:` 块体里，解析器的拍区间会从块体
    开始、**不含块头**。跨行规则要改块头时，把搜索范围往上扩一格。

    只认控制流块头（不认 label）：label 整块太大，不能因为拍在里面就
    让 `span: block` 有机会把整个 label 删掉。
    """
    if not (0 <= idx < len(lines)) or not lines[idx].strip():
        return None
    base = _indent_of(lines[idx])
    for j in range(idx - 1, -1, -1):
        if not lines[j].strip() or lines[j].lstrip().startswith('#'):
            continue
        if _indent_of(lines[j]) >= base:
            continue
        if _is_block_header(lines[j]) and _CONTROL_HEAD.match(
                lines[j].strip()):
            return j
        return None
    return None


def _scope_range(beat, rule, symbols, lines: list, statement_lines: set):
    """replace/delete 的搜索范围 → `(start0, end)`；找不到锚点返回 `None`。"""
    first = int(getattr(beat, 'src_first', 0) or 0)
    last = int(getattr(beat, 'src_last', 0) or 0)
    if first < 1 or last < first or last > len(lines):
        return None
    if rule.scope == 'beat':
        lo = first - 1
        if rule.span:
            head = _enclosing_control_head(lines, lo)
            if head is not None:
                lo = head
        return lo, last
    got = _anchor(beat, rule.scope, rule.tag_kind, symbols, lines,
                  statement_lines)
    if got is None:
        return None
    anchor, _indent = got
    return anchor + 1, max(last, anchor + 1)


def _would_empty_block(lines: list, idx: int) -> bool:
    """删这一行会不会把所在的 `if:` / `menu:` / `python:` 块体删空。"""
    base = _indent_of(lines[idx])
    head = None
    for j in range(idx - 1, -1, -1):
        if not lines[j].strip():
            continue
        if _indent_of(lines[j]) < base:
            head = j
            break
    if head is None or not _is_block_header(lines[head]):
        return False
    head_indent = _indent_of(lines[head])
    content = []
    for j in range(head + 1, len(lines)):
        if not lines[j].strip():
            continue
        if _indent_of(lines[j]) <= head_indent:
            break
        if not lines[j].lstrip().startswith('#'):
            content.append(j)
    return content == [idx]


def _find_span(lines: list, idx: int, rule, hi: int):
    """从一条命中行算出 `(start0, end0)`；算不出返回 `None`。"""
    if rule.span == 'block':
        if not _is_block_header(lines[idx]):
            return None
        return idx, _after_block(lines, idx, len(lines))
    if rule.span == 'until':
        for j in range(idx, min(hi, len(lines))):
            if _match_line(lines[j], rule.until):
                return idx, j
        return None
    return idx, idx


def _span_cuts_block(lines: list, start: int, end: int):
    """这个范围是否切断了某个块（块头在范围内、块体却延伸到范围外）。"""
    for idx in range(start, end + 1):
        if not _is_block_header(lines[idx]):
            continue
        if _after_block(lines, idx, len(lines)) > end:
            return idx
    return None


def _render_lines(text: str, indent: str) -> list:
    """把规则正文渲染成源文件行：基准缩进 + 续行的相对缩进。"""
    return ['' if not line.strip() else indent + line
            for line in text.split('\n')]


def _span_candidates(lines: list, rule, lo: int, hi: int):
    """在 `[lo, hi)` 里找匹配行并算出范围；返回 `(spans, 跳过原因)`。"""
    hits = [i for i in range(lo, min(hi, len(lines)))
            if _match_line(lines[i], rule.match)]
    candidates, reasons = [], []
    for idx in hits:
        got = _find_span(lines, idx, rule, hi)
        if got is None:
            if rule.span == 'until':
                reasons.append('until 在搜索范围里没有结束行')
            continue
        candidates.append(got)
    if not candidates:
        if not hits:
            reasons.append('没有匹配到行')
        elif rule.span == 'block':
            reasons.append('没有匹配到块头（span=block 只认以 : 收尾的行）')
        else:
            reasons.append('没有可用的匹配范围')
        return [], reasons

    if rule.occurrence == 'first':
        candidates = candidates[:1]
    elif rule.occurrence == 'last':
        candidates = candidates[-1:]

    spans = []
    for start, end in candidates:
        if any(not (end < a or start > b) for a, b in spans):
            reasons.append('匹配范围重叠，保留前一个')
            continue
        spans.append((start, end))
    return spans, reasons


def _append_already_there(line: str, append: str) -> bool:
    """
    这一行是不是**已经加过这条尾巴**（append 的幂等判据）。

    两个判据，任一成立就算"加过了"：

      · 行尾已经是这段（逐字）；
      · **追加内容的第一个词**（`fadein` / `fadeout` / `with`）已经作为
        一个词出现在行里。

    为什么要有第二条（真实语料踩点，2026-11-08）：项目里本来就有
    `play music "bgm/bgm02.mp3" fadein 1.0`（作者手写的），逐字比行尾当然
    不相等 —— 再加一次会变成 `… fadein 1.0 fadein 1.0`，**重复关键字 =
    语法错**，脚本直接起不来。宁可少补一次，也不能补出跑不了的脚本。
    """
    line = str(line or '')
    append = str(append or '')
    if not append.strip():
        return False
    if line.rstrip().endswith(append.rstrip()):
        return True
    word = append.strip().split()[0]
    # 只在**引号外面**找那个词：`play music "volume.mp3"` 里的 volume
    # 是文件名，不是参数名 —— 拿它判"加过了"会永远不补。
    bare = re.sub(r'"[^"]*"|\'[^\']*\'', '""', line)
    return re.search(rf'(?<!\w){re.escape(word)}(?!\w)', bare) is not None


def _plan_one_span(fp: FilePlan, rule, seq: int, lines: list,
                   start: int, end: int) -> None:
    """把一条已算好范围的行/段编辑收进 `fp.edits`；不合判据就跳过。"""
    # 同一个块可能同时落在两拍的 src 区间里（menu 块头尤其常见）；
    # 同一条规则、同一段只算一次，不能当成两条规则冲突。
    if any(e.rule_id == rule.id and e.line == start + 1
           and e.last_line == end + 1 for e in fp.edits):
        return
    raw_line = lines[start]
    if start == end and _is_block_header(raw_line):
        fp.skipped.append({'seq': seq, 'rule': rule.id,
                           'reason': '目标是块头，不 replace/delete'})
        return
    cut = _span_cuts_block(lines, start, end)
    if cut is not None:
        fp.skipped.append({'seq': seq, 'rule': rule.id,
                           'reason': f'跨行范围会切断 L{cut + 1} 的块体'})
        return
    if rule.op == 'delete':
        if start == end and _would_empty_block(lines, start):
            fp.skipped.append({'seq': seq, 'rule': rule.id,
                               'reason': '删掉会让块体变空'})
            return
        fp.edits.append(LineEdit(
            kind='delete', line=start + 1, end_line=end + 1,
            old=raw_line, old_lines=list(lines[start:end + 1]),
            seq=seq, rule_id=rule.id))
        return
    if not raw_line.strip():
        fp.skipped.append({'seq': seq, 'rule': rule.id,
                           'reason': '空白行不 replace（缩进不可信）'})
        return
    if rule.append:
        # **原行末尾追加**（2026-11-08）：`play music "x"` → `… "x" fadein 1.0`。
        # 原行一个字节都不动，只加尾巴；已经是目标内容的就跳过（幂等）。
        if rule.skip_if_present and _append_already_there(raw_line, rule.append):
            fp.skipped.append({'seq': seq, 'rule': rule.id,
                               'reason': '已经是目标内容'})
            return
        fp.edits.append(LineEdit(
            kind='replace', line=start + 1, end_line=end + 1,
            old=raw_line, old_lines=list(lines[start:end + 1]),
            append=rule.append, seq=seq, rule_id=rule.id))
        return
    indent = raw_line[:_indent_of(raw_line)]
    if rule.skip_if_present and \
            _render_lines(rule.text, indent) == lines[start:end + 1]:
        fp.skipped.append({'seq': seq, 'rule': rule.id,
                           'reason': '已经是目标内容'})
        return
    fp.edits.append(LineEdit(
        kind='replace', line=start + 1, end_line=end + 1,
        old=raw_line, old_lines=list(lines[start:end + 1]),
        text=rule.text, indent=indent, seq=seq, rule_id=rule.id))


def _plan_line_edits(fp: FilePlan, beat, rule, symbols, lines: list,
                     statement_lines: set) -> None:
    """把一条 replace/delete 规则在拍内匹配到的行/段收进 `fp.edits`。"""
    seq = int(getattr(beat, 'seq', 0) or 0)
    scope = _scope_range(beat, rule, symbols, lines, statement_lines)
    if scope is None:
        fp.skipped.append({'seq': seq, 'rule': rule.id,
                           'reason': f'拍里找不到 {rule.scope} 范围'})
        return
    spans, reasons = _span_candidates(lines, rule, *scope)
    for reason in reasons:
        fp.skipped.append({'seq': seq, 'rule': rule.id, 'reason': reason})
    for start, end in spans:
        _plan_one_span(fp, rule, seq, lines, start, end)


# ---------------------------------------------------------------------------
#  计划
# ---------------------------------------------------------------------------

def plan_batch(project_root, rules, chapters=None, registry=None) -> BatchPlan:
    """
    扫项目里的 `.rpy`，按规则算出**要插哪些行**。只读，不碰磁盘。

    `chapters`：glob 列表（可匹配相对路径或文件名）；空 = 全部脚本。
    """
    from storygraph.parsing.discover import discover_scripts

    root = Path(project_root).resolve()
    plan = BatchPlan(root=root, rules=list(rules or ()))
    if not root.is_dir():
        plan.errors.append(f'项目目录不存在：{root}')
        return plan
    if not plan.rules:
        plan.errors.append('没有规则')
        return plan

    try:
        files = discover_scripts(root)
    except OSError as exc:
        plan.errors.append(f'扫不了项目脚本：{exc}')
        return plan
    patterns = [str(x).replace('\\', '/') for x in (chapters or ()) if str(x)]
    if patterns:
        files = [p for p in files if _chapter_match(root, p, patterns)]
    if not files:
        plan.errors.append('没有匹配到任何 .rpy')
        return plan

    symbols = _symbols(registry, root)
    for path in sorted(files):
        fp = _plan_file(root, path, plan.rules, symbols)
        if fp.error:
            plan.errors.append(f'{fp.rel}: {fp.error}')
        if fp.error or fp.insertions or fp.edits or fp.skipped:
            plan.files.append(fp)
    return plan


def _chapter_match(root: Path, path: Path, patterns: list) -> bool:
    try:
        rel = path.resolve().relative_to(root).as_posix()
    except ValueError:
        rel = path.name
    return any(fnmatch.fnmatchcase(rel, pat) or fnmatch.fnmatchcase(path.name, pat)
               for pat in patterns)


def _symbols(registry, root: Path):
    try:
        if registry is None:
            from storygraph.assets.registry_load import load_asset_registry
            registry = load_asset_registry(root)
    except (OSError, ValueError, KeyError, TypeError):
        registry = None
    try:
        from storygraph.story.beat_render import make_symbols
        return make_symbols(registry)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _plan_file(root: Path, path: Path, rules: list, symbols) -> FilePlan:
    try:
        rel = path.resolve().relative_to(root).as_posix()
    except ValueError:
        rel = path.name
    fp = FilePlan(path=path, rel=rel)
    try:
        path.resolve().relative_to(root)
    except ValueError:
        fp.error = '不在项目目录内（符号链接？）；不碰'
        return fp
    try:
        text, _style = read_text(path)
    except OSError as exc:
        fp.error = f'读不了：{exc}'
        return fp
    try:
        beats, statement_lines = _load_beats(path)
    except (OSError, ValueError, SyntaxError, UnicodeError, RuntimeError,
            KeyError, IndexError, AttributeError) as exc:
        fp.error = f'解析失败：{exc}'
        return fp
    fp.old_text = text
    lines = text.split('\n')
    seen = set()
    for beat in beats:
        for rule in rules:
            if not _match_when(beat, rule.when, symbols):
                continue
            if rule.op != 'insert':
                _plan_line_edits(fp, beat, rule, symbols, lines,
                                 statement_lines)
                continue
            got = _anchor(beat, rule.placement, rule.tag_kind, symbols, lines,
                          statement_lines)
            if got is None:
                fp.skipped.append({
                    'seq': int(getattr(beat, 'seq', 0) or 0),
                    'rule': rule.id,
                    'reason': f'拍里找不到 {rule.placement} 锚点',
                })
                continue
            anchor, indent = got
            if rule.skip_if_present and _already_present(lines, beat, rule.text,
                                                         anchor):
                fp.skipped.append({
                    'seq': int(getattr(beat, 'seq', 0) or 0),
                    'rule': rule.id,
                    'reason': ('已经有同一行' if '\n' not in rule.text
                               else '已经有同一段'),
                })
                continue
            before = rule.placement == 'before_beat'
            line = anchor + 1
            key = (line, before, rule.text)
            if key in seen:
                continue
            seen.add(key)
            fp.insertions.append(Insertion(
                line=line, text=rule.text, indent=indent,
                seq=int(getattr(beat, 'seq', 0) or 0), rule_id=rule.id,
                before=before))
    _check_conflicts(fp)
    if not fp.error:
        _check_span_blocks(fp, lines)
    if not fp.error:
        _check_empty_blocks(fp, lines)
    return fp


def _check_span_blocks(fp: FilePlan, lines: list) -> None:
    """跨行编辑不能把块头删掉却留下块体（那会变成孤儿缩进）。"""
    for edit in fp.edits:
        cut = _span_cuts_block(lines, edit.line - 1, edit.last_line - 1)
        if cut is not None:
            fp.error = (f'L{edit.line}-L{edit.last_line} 的编辑会切断 '
                        f'L{cut + 1} 的块体；不写')
            return


def _check_empty_blocks(fp: FilePlan, lines: list) -> None:
    """多条 delete 合起来把同一个块体删空 → 整份拒绝。"""
    deleted = set()
    for edit in fp.edits:
        if edit.kind != 'delete':
            continue
        deleted.update(range(edit.line, edit.last_line + 1))
    if not deleted:
        return
    for head, line in enumerate(lines):
        if not _is_block_header(line):
            continue
        if (head + 1) in deleted:
            continue                     # 块头本身也要删：整块消失，不算空块
        base = _indent_of(line)
        content = []
        for j in range(head + 1, len(lines)):
            if not lines[j].strip():
                continue
            if _indent_of(lines[j]) <= base:
                break
            if not lines[j].lstrip().startswith('#'):
                content.append(j + 1)
        if content and all(n in deleted for n in content):
            fp.error = f'删除后 L{head + 1} 的块体会变空；不写'
            return


def _check_conflicts(fp: FilePlan) -> None:
    """同一行/同一段被两条线编辑命中 / 插入紧贴线编辑 → 整份拒绝，不猜先后。"""
    ranges = []
    for edit in fp.edits:
        for start, end in ranges:
            if not (edit.last_line < start or edit.line > end):
                if edit.line == edit.last_line == start == end:
                    fp.error = (f'多条规则改同一行 L{edit.line}；'
                                '不猜先后')
                else:
                    fp.error = (f'L{edit.line}-L{edit.last_line} 与 '
                                f'L{start}-L{end} 的编辑范围重叠；不猜先后')
                return
        ranges.append((edit.line, edit.last_line))
    for ins in fp.insertions:
        for start, end in ranges:
            if start - 1 <= ins.line <= end + 1:
                fp.error = (f'插入点 L{ins.line} 与 L{start}-L{end} 的 '
                            'replace/delete 冲突；不猜先后')
                return


def _load_beats(path: Path) -> tuple:
    """返回 `(beats, show/hide 语句行号集合)`；行号 1 基。"""
    from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
    from storygraph.story.beat_build import beats_from_parse_result
    pr = ParseResultBuilder().build(RenPyParser().parse(str(path)))
    lines = set()
    for st in getattr(pr, 'statements', ()) or ():
        if str(getattr(st, 'kind', '')) not in ('show', 'hide'):
            continue
        src = getattr(st, 'source', None)
        try:
            lines.add(int(src[1]))
        except (TypeError, ValueError, IndexError):
            continue
    return beats_from_parse_result(pr), lines


# ---------------------------------------------------------------------------
#  写盘
# ---------------------------------------------------------------------------

def apply_plan(plan: BatchPlan) -> dict:
    """
    真的写盘：每个文件重新读一遍、逐字比对计划时的原文，再按原始行号
    从后往前 insert / replace / delete。

    返回 `{written, failed, inserted, replaced, deleted}`；失败的文件**不写**，
    其余文件继续（每份文件独立，重跑幂等）。
    """
    from storygraph.story.paths import write_text_atomic

    written, failed = [], []
    inserted = replaced = deleted = 0
    if not isinstance(plan, BatchPlan):
        return {'written': [], 'failed': [('', '计划无效')],
                'inserted': 0, 'replaced': 0, 'deleted': 0}
    for fp in plan.files:
        if fp.error or not (fp.insertions or fp.edits):
            continue
        try:
            text, style = read_text(fp.path)
            if text != fp.old_text:
                raise RuntimeError('源文件在计划之后又被改过；为了不误改，这次没有写')
            new_text = _splice_edits(text, fp.insertions, fp.edits)
            write_text_atomic(fp.path, style.apply(new_text, source=fp.path),
                              backup=True)
        except (OSError, ValueError, RuntimeError, UnicodeError) as exc:
            failed.append((fp.rel, str(exc)))
            continue
        written.append(fp.rel)
        inserted += sum(len(x.text_lines) for x in fp.insertions)
        replaced += sum(e.span_lines for e in fp.edits
                        if e.kind == 'replace')
        deleted += sum(e.span_lines for e in fp.edits if e.kind == 'delete')
    return {'written': written, 'failed': failed, 'inserted': inserted,
            'replaced': replaced, 'deleted': deleted}


def _splice_edits(text: str, insertions: list, edits: list) -> str:
    """按**原始行号**从后往前改；同一位置的多条插入保持规则顺序。"""
    lines = text.split('\n')
    ops = []
    for ins in insertions:
        pos = ins.line if not ins.before else ins.line - 1
        pos = max(0, min(pos, len(lines)))
        ops.append((pos, int(ins.seq), 0, 'insert', ins))
    for edit in edits:
        start = edit.line - 1
        end = edit.last_line - 1
        if 0 <= start < len(lines):
            end = min(max(end, start), len(lines) - 1)
            ops.append((start, int(edit.seq), 1, edit.kind, edit))
    # 从后往前；同一位置先做线编辑、后做插入；插入按 seq 逆序放入，
    # 最终文件里的顺序才是 seq 正序。
    ops.sort(key=lambda op: (op[0], op[2], op[1]), reverse=True)
    for pos, _seq, _kind_order, kind, obj in ops:
        if kind == 'insert':
            lines[pos:pos] = _render_lines(obj.text, obj.indent)
        elif kind == 'replace':
            end = min(max(obj.last_line - 1, pos), len(lines) - 1)
            if obj.append:
                lines[pos:end + 1] = [obj.old.rstrip() + obj.append]
            else:
                lines[pos:end + 1] = _render_lines(obj.text, obj.indent)
        elif kind == 'delete':
            end = min(max(obj.last_line - 1, pos), len(lines) - 1)
            del lines[pos:end + 1]
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
#  报告
# ---------------------------------------------------------------------------

def format_diff(plan: BatchPlan, context: int = 2) -> str:
    """统一 diff（预演用）；只对有计划改动的文件生成。"""
    import difflib

    out = []
    for fp in plan.files:
        if fp.error or not (fp.insertions or fp.edits):
            continue
        new_text = _splice_edits(fp.old_text, fp.insertions, fp.edits)
        out.extend(difflib.unified_diff(
            fp.old_text.split('\n'), new_text.split('\n'),
            fromfile=f'{fp.rel} (before)', tofile=f'{fp.rel} (after)',
            n=max(0, int(context)), lineterm=''))
    return '\n'.join(out) if out else '（没有差异）'


def format_plan(plan: BatchPlan, limit: int = 5) -> str:
    """人话报告：每份文件几处 insert / replace / delete，前几处是什么。"""
    out = [f'批量编辑预演：{len(plan.rules)} 条规则，'
           f'{plan.pending} 处待处理']
    for fp in plan.files:
        if fp.error:
            out.append(f'  {fp.rel}: 跳过（{fp.error}）')
            continue
        n_ins = len(fp.insertions)
        n_rep = sum(1 for e in fp.edits if e.kind == 'replace')
        n_del = sum(1 for e in fp.edits if e.kind == 'delete')
        out.append(f'  {fp.rel}: {fp.total} 处'
                   f'（+{n_ins} ~{n_rep} -{n_del}）')
        if limit > 0:
            items = []
            for ins in fp.insertions:
                arrow = 'before' if ins.before else 'after'
                first = ins.text.split('\n')[0]
                more = '' if '\n' not in ins.text else \
                    f'… 共 {len(ins.text_lines)} 行'
                items.append((ins.line, 0,
                              f'    L{ins.line} {arrow}  + '
                              f'{ins.indent}{first}{more}'))
            for ed in fp.edits:
                where = (f'L{ed.line}' if ed.span_lines == 1
                         else f'L{ed.line}-L{ed.last_line}')
                if ed.kind == 'replace':
                    items.append((ed.line, 1,
                                  f'    {where} replace  - {ed.old.strip()}'))
                    first = ed.text.split('\n')[0]
                    more = '' if ed.span_lines == 1 and '\n' not in ed.text \
                        else f'… {ed.span_lines} 行'
                    items.append((ed.line, 2,
                                  f'    {" " * len(where)} replace  + '
                                  f'{ed.indent}{first}{more}'))
                else:
                    suffix = ('' if ed.span_lines == 1
                              else f'… 共 {ed.span_lines} 行')
                    items.append((ed.line, 3,
                                  f'    {where} delete   - '
                                  f'{ed.old.strip()}{suffix}'))
            items.sort(key=lambda item: (item[0], item[1]))
            out.extend(item[2] for item in items[:limit])
            if len(items) > limit:
                out.append(f'    … 还有 {len(items) - limit} 行明细')
        if fp.skipped:
            reasons = {}
            for sk in fp.skipped:
                reasons[sk['reason']] = reasons.get(sk['reason'], 0) + 1
            tail = '；'.join(f'{k} {v}' for k, v in reasons.items())
            out.append(f'    （跳过 {len(fp.skipped)}：{tail}）')
    if plan.errors:
        for err in plan.errors:
            out.append(f'  ! {err}')
    if not plan.pending:
        out.append('没有待处理的编辑。')
    else:
        out.append('未写盘；加 --apply 才写。')
    return '\n'.join(out)


__all__ = [
    'BatchPlan', 'BatchRuleError', 'CueRule', 'FilePlan', 'Insertion',
    'LineEdit', 'MATCH_MODES', 'OCCURRENCES', 'PLAN_SCHEMA', 'SCHEMA',
    'SCOPES', 'SPAN_MODES', 'apply_plan', 'format_diff', 'format_plan',
    'load_rule_file', 'merge_declared_rules', 'parse_rule', 'parse_rules',
    'plan_batch', 'rules_from_extensions',
]
