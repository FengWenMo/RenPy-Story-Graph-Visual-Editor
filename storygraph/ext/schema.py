"""
storygraph.ext.schema
=====================
**扩展点的唯一一份描述**（纯数据 + 纯函数，不 import Qt、不 import app）。

为什么要有这一层
----------------
用户 2026-11-07 的原话：**"必须要能够兼容一切自定义功能的改进……比如我想要在
工具里加个新写法，加个批处理的功能一类的，这些东西我们也必须能够支持！"**

在那之前，"自定义"是**四个各写各的入口**：

| 入口 | 文件 | 能加什么 | 状态存在哪 |
|---|---|---|---|
| 自定义语句 | `rules.json` 的 `statements` | 写法（格子、子句、块体） | 手写 JSON / 一个弹窗 |
| 技术调用 | `rules.json` 的 `calls` | `$ my_wipe(...)` 的形状 | 手写 JSON / 一个小窗 |
| 批处理 | `storygraph.batch.json` | 插/替/删一行 | **手写 JSON，没有界面** |
| 拍字段 | `beat_fields.COLUMN_SPECS` | 拍上有哪几列 | 改代码 |

它们互不相通：**声明了一个写法，它不会自动可批处理；能批处理的规则又看不见
卡片上那些格子**。这个模块把四件事收成**一个形状**：

    ExtensionPoint(kind='statement' | 'call' | 'field' | 'batch', …)

每个扩展点都带两个**用户点名的开关**：

  · `enabled`   —— 是否使用（关掉 = 工具不再拿它去认写法 / 出格子 / 批处理，
                  但声明本身留着，随时能再打开）；
  · `batchable` —— 是否能够批处理，以及批处理的规则（`batch` 段）。

一套词汇，三处同一句话
----------------------
**2026-11-07 第二轮（用户："统一三套字段表，不能让它们互不相通"）**：
一个字段的类型（`FieldSpec.kind`）现在就是**解析器的格类型**
（`keyword / variable / choice / optional / number / expr / transition /
code / multiline`，从 `authoring.pattern.SLOT_KINDS` **直接 import**，不另抄
一份），外加三个只影响控件的细化（`bool / int / multi_choice`）。于是：

    声明里写什么  →  解析器怎么读（`parser_kind`）
                  →  界面画什么  （`widget`）
                  →  写回 `.rpy` 是什么（`to_cue_*` / `slot_of_field`）

三句话**是同一条链**，不是三张表。`field_of_slot()`（声明 → 字段）与
`slot_of_field()`（字段 → 声明）互为逆运算，判据钉着往返不丢信息。

**2026-11-07 第五轮：最后一份字段表（技术调用的 `columns`）也并进来了。**
`$ my_wipe("left", 0.6, layer="screens")` 的参数形状也是**一个字段**，
只是它多两个维度：**第几个位置参数**（`position`）和**关键字名**（`kw`）。
从前这两件事只有 `columns[]` 有，字段表没有位置，于是：

  · 扩展窗口里新建的调用**只能按行序**当位置参数，`layer=` 是唯一能表达
    "关键字参数"的办法（一个私有记号）；
  · 人按 `columns` 的词写 `"kw": "layer"` → **整条扩展点消失**
    （`字段里有不认识的键：kw`，只在 `problems` 里留一句）；
  · 从 `rules.json` 投影到字段表时 `position` / `kw` / `kind` / `choices`
    全丢，于是"看得见的形状"和"真声明"不是同一个东西。

现在 `FieldSpec.position` / `FieldSpec.kw` 就是那两位，`field_of_column()`
（`columns` → 字段）与 `column_of_field()`（字段 → `columns`）互逆，
调用窗口与扩展窗口读写的**是同一份 schema**。

⚠ 旧版这里有一处**静默误读**（2026-11-07 探针实测）：`kind` 只认控件那几种，
声明里写 `kind: "keyword"` 会**整条扩展点被丢掉**（只留一句 `problems`），
界面上看就是"我加的写法不见了"。现在两套词都收，且别名归一。

⚠ 本模块**不** import `app.batch_edit`。批处理规则的形状在这里只做"形状检查"
（`to_cue_rule` 产出的是普通 dict），真正的校验仍归 `app.batch_edit`——
那一层是唯一真源（分层判据 `tests/test_package_layering.py` 钉着 import 方向）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as _dc_field

from storygraph.authoring.pattern import SLOT_ALIASES, SLOT_KINDS

#: 扩展点描述文件的 schema 版本（改结构才动）
EXT_SCHEMA = 'storygraph.ext/1'

#: 四种扩展点。**刻意只有四种**：加第五种要先说清它和这四种的关系。
KINDS = ('statement', 'call', 'field', 'batch')

KIND_LABELS = {
    'statement': '写法（自定义语句）',
    'call': '技术调用（`$ 函数(...)`）',
    'field': '拍字段（卡片上的一格）',
    'batch': '批处理规则',
}

#: 一个字段能是什么 —— **界面 / 插件 / 解析器共用的唯一一套词**。
#:
#: 前九个是解析器认的格类型（顺序与 `authoring.pattern.SLOT_KINDS` 一致），
#: 后三个只影响控件：`bool`（勾选框）/`int`（整数框）/`multi_choice`（多选）。
#: 语句里没有 bool 这种东西，但"拍字段"那一类扩展点用得上，所以留着。
FIELD_KINDS = (*SLOT_KINDS, 'bool', 'int', 'multi_choice')

#: 给界面看的中文名。**一格一种说法**，不在每个弹窗里各起一个名字。
FIELD_KIND_LABELS = {
    'keyword': '固定关键字',
    'variable': '任意值',
    'choice': '从这些里选',
    'optional': '可选固定段',
    'number': '数字',
    'expr': '一段表达式',
    'transition': '转场表达式',
    'code': '一段代码',
    'multiline': '多行文本',
    'bool': '开关',
    'int': '整数',
    'multi_choice': '多选',
}

#: 语义类型 → 控件。**只有这一处映射**（Unity 的 Property Drawer 等价物）。
KIND_TO_WIDGET = {
    'keyword': 'text',      # 固定字：卡片上是**只读**的灰字，这里仍是文本框
    'variable': 'text',
    'choice': 'choice',
    'optional': 'text',
    'number': 'number',
    'expr': 'expr',
    'transition': 'expr',
    'code': 'code',
    'multiline': 'multiline',
    'bool': 'bool',
    'int': 'int',
    'multi_choice': 'multi_choice',
}

#: 这些类型**只要有候选就该是下拉**（其余类型有候选也只当提示，仍是输入框）。
#:
#: 为什么是"类型"的属性、不是"调用处的选择"（2026-11-07 第三轮）：卡片从前把
#: 这条规则写在 `SPEC_EDITOR_KIND` 里（`transition: 'combo'`），扩展窗口又把
#: 它写在 `_make_editor` 的 if 链里 —— 同一条规则两份，改一处另一处不动。
#:
#: `choice` 是"定义上就从列表里选"；`transition` 是"转场表达式"，项目里
#: 能用的转场是有限的（`transforms` 生成的那些），所以给下拉、同时**可编辑**
#: （`Dissolve(0.1, 0.2)` 这种手写表达式必须能填进去，见 `limit`）。
CHOICE_LIKE = frozenset({'choice', 'transition'})

#: **语句参数格**空着时的灰字提示（按语义类型）。
#:
#: 只用在"这一格的值要折回语句头"的地方（卡片上的参数格）。同一句提示在
#: 扩展窗口里没有意义（那是"这个扩展点还没字段"，不是"这个参数不带"），所以
#: 用不用它由调用方说（`field_widgets.make_editor(..., parser_only=True)`），
#: 不在这里猜。
PARAM_PLACEHOLDER = {
    'variable': '留空 = 不带这个参数',
    'optional': '留空 = 不带这个参数',
    'expr': '留空 = 不带这个参数',
    'transition': '留空 = 不带这个参数',
    'code': '留空 = 不带这个参数',
    'multiline': '留空 = 不带这个参数',
    'number': '数字（例：0.5）',
    'int': '整数（例：3）',
}

#: **语句参数格**的 tooltip（按控件名，见 `KIND_TO_WIDGET`）。
#:
#: 与 `PARAM_PLACEHOLDER` 同一条理由：这些话说的是"参数要折回语句"这件事，
#: 只有参数格才成立。`fixed`（固定字）的提示由调用方给 —— 它得说清"去哪改"。
PARAM_TIP = {
    'choice': '从下拉里选，或直接写一个项目里在用的值'
              '\n（候选里带人话名字时，悬停能看到引擎写法）',
    'code': '这一格可以写长表达式 / 一小段代码；'
            '\n保存时会**折成一行**再折回语句里（多行参数会让语法坏掉）。',
    'multiline': '这一格可以写多行；保存时会**折成一行**再折回语句里'
                 '（多行参数会让语法坏掉）。',
}

#: 语义类型 → 解析器的格类型（**这一条链的中间那段**）。
#:
#: `bool` / `int` / `multi_choice` 是控件的细化，落到解析器只有三种落点：
#: 开关和任意值一样是个词、整数就是数字、多选就是"可多选的枚举"。
KIND_TO_SLOT = {
    'keyword': 'keyword',
    'variable': 'variable',
    'choice': 'choice',
    'optional': 'optional',
    'number': 'number',
    'expr': 'expr',
    'transition': 'transition',
    'code': 'code',
    'multiline': 'multiline',
    'bool': 'variable',
    'int': 'number',
    'multi_choice': 'choice',
}

#: 旧写法 / 别名 → 规范语义类型。
#:
#: `text` 是上一版（2026-11-07 第一轮）的默认值，`word` / `words` 是
#: `authoring.pattern` 里的老别名 —— 三个都得继续认，否则老文件的字段会
#: 在打开时**悄悄变一种控件**。
KIND_ALIASES = {**SLOT_ALIASES, 'text': 'variable'}

#: 这些别名**还额外**意味着"这一格可以吃多个词"
MULTI_ALIASES = frozenset({'words'})

#: 这些类型的格子**天生可能含空格**（`Dissolve(0.1, 0.2)` / 一整行代码），
#: 所以从字段翻成声明时自动带上 `multi`（"这一格吃多个词"）。
#:
#: 为什么必须自动：解析器按**空白**切词，`Fade(0.2, 0, 0.2)` 会被切成三个词。
#: 不标 multi，卡片上「转场」那一格只拿到 `Fade(0.2,`，剩下两个词变成
#: "没人认领" —— 用户看到的是"我明明写对了，工具说切不上"。
#:
#: ⚠ 不会因此抢别人的词：多吃的每个词要付 `COST_EXTRA`，而后面那一格空着
#:   要付 `EMPTY_SLOT`（更贵），所以**后面还有必填值格时，代价最小的解是
#:   不抢**。判据 `test_code_cell_does_not_steal_the_next_field`。
MULTI_BY_DEFAULT = frozenset({'expr', 'transition', 'code', 'multiline'})



def normalize_kind(raw) -> str:
    """别名/大小写/空白 → 规范语义类型；不认识的原样返回（由调用方报错）。"""
    kind = str(raw or '').strip()
    return KIND_ALIASES.get(kind, kind)


class ExtError(ValueError):
    """扩展点写错。**不猜**：整份拒绝并说清是哪个键（与 `batch_edit` 同一纪律）。"""


@dataclass
class FieldSpec:
    """扩展点里的**一个字段**（= 界面上的一格 + 写回的一段）。"""

    #: 键名。语句用列名，调用用参数名，批处理用规则键
    key: str
    #: 给人看的标签；空 = 用 `key`
    label: str = ''
    #: `FIELD_KINDS` 之一 —— **语义类型**（决定控件、也决定解析器怎么读）
    kind: str = 'variable'
    help: str = ''
    default: object = ''
    #: `kind` 是 choice / multi_choice 时的候选
    choices: tuple = ()
    required: bool = False
    #: 界面上的分组标题（空 = 不分组）
    group: str = ''
    #: `keyword` / `optional` 的那个**固定词**（`with` / `at`）。空 = 用 `key`
    literal: str = ''
    #: 这一格可以吃多个词（`base_swimsuit clothes2` 这种"可有多个"）
    multi: bool = False
    #: 候选是**限死**的吗（`choice` 才有意义：写别的词就是错）
    limit: bool = False
    #: 空着时控件里的灰字提示（`留空 = 无条件（例：love >= 5）`）。
    #:
    #: 为什么它属于 schema（2026-11-07 第三轮）：这句提示从前是**卡片里写死的
    #: 字符串**，于是"字段表 → 界面"在最后一米断掉 —— 扩展窗口凭空多一格时
    #: 它永远没有提示。挂在字段上之后，两处界面都读同一个值。
    placeholder: str = ''
    #: **技术调用的第几个位置参数**（0 起）。`None` = 没指定（按行序 / 不适用）。
    #:
    #: 只有 `kind == 'call'` 的扩展点用它。为什么它必须在 schema 里
    #: （2026-11-07 第五轮）：调用窗口能说"我只管第 2 个参数"，扩展窗口
    #: 以前说不出这句话 —— 同一份声明，两个窗口能表达的形状不一样，
    #: 那就还是两张表。判据：`column_of_field` / `field_of_column` 往返。
    position: object = None
    #: **关键字参数的名**（`layer` / `duration`）。非空 = 不是位置参数。
    #:
    #: 与 `position` 二选一（`parse_field` 会拦）。它与"键名写成 `layer=`"
    #: 那个老记号是同一件事的两种说法：老记号仍收（老文件不能变意思），
    #: 但**新写入一律用这一位**，两个窗口和 `columns[]` 用同一个词。
    kw: str = ''

    def __post_init__(self):
        raw = str(self.kind or '').strip()
        # `kind: "words"` = 这个值本身是"多个词"（别名要**在归一之前**看，
        # 归一之后它就只是 `variable` 了 —— 这半句话会丢）。
        if raw in MULTI_ALIASES:
            self.multi = True
        self.kind = normalize_kind(raw)

    @property
    def shown(self) -> str:
        return self.label or self.key

    @property
    def widget(self) -> str:
        """界面上画什么控件（`DynamicForm` 只看这一个）。"""
        return KIND_TO_WIDGET.get(self.kind, 'text')

    @property
    def parser_kind(self) -> str:
        """解析器怎么读这一格（`authoring.pattern` 的格类型）。"""
        return KIND_TO_SLOT.get(self.kind, 'variable')

    @property
    def fixed(self) -> bool:
        """这一格是**写死的字**（`keyword` / `optional`），不是值。"""
        return self.kind in ('keyword', 'optional')

    @property
    def shown_literal(self) -> str:
        return str(self.literal or self.key)

    def to_dict(self) -> dict:
        out = {'key': self.key, 'label': self.label,
               'kind': self.kind}
        if self.help:
            out['help'] = self.help
        if self.default not in ('', None, False):
            out['default'] = self.default
        if self.choices:
            out['choices'] = list(self.choices)
        if self.required:
            out['required'] = True
        if self.group:
            out['group'] = self.group
        if self.fixed:
            out['literal'] = self.shown_literal
        if self.multi:
            out['multi'] = True
        if self.limit:
            out['limit'] = True
        if self.placeholder:
            out['placeholder'] = self.placeholder
        # 只有技术调用才写这两位（其余种类写了也没人读，但写了会让
        # `storygraph.ext.json` 多出恒为空/恒为 0 的键，是噪音）
        if self.position is not None:
            out['position'] = self.position
        if self.kw:
            out['kw'] = self.kw
        return out


_FIELD_KEYS = frozenset({'key', 'label', 'kind', 'help', 'default',
                         'choices', 'required', 'group', 'literal',
                         'multi', 'limit', 'placeholder',
                         'position', 'kw'})

#: 关键字参数名 = 标识符。与 `story.statement_registry.call_kw_ok` 同一条规则
#: （判据 `test_kw_rule_has_one_definition` 钉着两处不许漂）。
#:
#: 为什么不 import 那边：`ext.schema` 是**纯数据层**（不 import story / app），
#: 分层判据（`tests/test_package_layering.py`）钉着 import 方向。
_KW_RE = re.compile(r'^[A-Za-z_]\w*$')


def kw_ok(text) -> bool:
    """是不是一个合法的关键字参数名（`layer` / `duration`）。"""
    return bool(_KW_RE.fullmatch(str(text or '')))


def _position_of(raw, key: str):
    """字段的 `position` → `int | None`（写错抛 `ExtError`，不静默当 0）。"""
    value = raw.get('position')
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        pos = int(value)
    except (TypeError, ValueError):
        raise ExtError(f'字段 {key} 的 position 必须是整数'
                       f'（第几个参数，0 起），收到 {value!r}') from None
    if pos < 0:
        raise ExtError(f'字段 {key} 的 position 不能是负数（{pos}）')
    return pos


def _text(value) -> str:
    """`None` → 空串，其余原样成串。**没有分支**（见 `parse_field` 的棘轮）。"""
    return '' if value is None else str(value)


def parse_field(raw) -> FieldSpec:
    """一个字段 dict → `FieldSpec`；不认识的键 / 坏 kind → `ExtError`。

    `kind` **两套词都收**（语义类型与别名）。`kind: "keyword"` 这种以前会让
    整条扩展点被丢掉，现在它就是一个固定字格。

    `kind` **没写** → `variable`（2026-11-07 第五轮改）。以前空 kind 直接
    `ExtError: kind='' 不认识`，于是插件里写 `{"key": "图层", "kw": "layer"}`
    会**整条扩展点消失**，界面上只看到"我加的调用不见了"。缺省是"任意值"，
    这不是猜 —— `FieldSpec.kind` 的缺省本来就是 `variable`。
    """
    if isinstance(raw, str):
        raw = {'key': raw}
    if not isinstance(raw, dict):
        raise ExtError(f'字段必须是对象或字符串，收到 {type(raw).__name__}')
    extra = set(raw) - _FIELD_KEYS
    if extra:
        raise ExtError(f'字段里有不认识的键：{"、".join(sorted(extra))}')
    key = _text(raw.get('key')).strip()
    if not key:
        raise ExtError('字段的 key 不能为空')
    raw_kind = _text(raw.get('kind')).strip()
    kind = normalize_kind(raw_kind) or 'variable'
    if kind not in FIELD_KINDS:
        raise ExtError(f'字段 {key} 的 kind={kind!r} 不认识'
                       f'（可选：{"、".join(FIELD_KINDS)}）')
    choices = raw.get('choices')
    if isinstance(choices, str):
        choices = [c.strip() for c in choices.replace('，', ',').split(',')
                   if c.strip()]
    if choices is None:
        choices = ()
    if not isinstance(choices, (list, tuple)):
        raise ExtError(f'字段 {key} 的 choices 必须是列表')
    if kind == 'choice' and not choices:
        raise ExtError(f'字段 {key} 是 choice，但没有给 choices')
    literal = _text(raw.get('literal')).strip()
    if kind in ('keyword', 'optional') and not literal:
        # 固定字格没写是哪个字 → 用 key 当那个字（`{"key": "with"}` 是最常见写法）。
        # **不报错**：报错会让整条扩展点消失，比"字就是 key"糟糕得多。
        literal = key
    position = _position_of(raw, key)
    kw = _text(raw.get('kw')).strip()
    if kw:
        if position is not None:
            raise ExtError(f'字段 {key} 的 position 和 kw 只能填一个'
                           f'（位置参数 / 关键字参数，二选一）')
        if not kw_ok(kw):
            raise ExtError(f'字段 {key} 的 kw={kw!r} 不是合法参数名'
                           f'（字母或下划线开头，不能有空格）')
    return FieldSpec(key=key,
                     label=_text(raw.get('label')),
                     kind=kind,
                     help=_text(raw.get('help')),
                     default=raw.get('default', ''),
                     choices=tuple(str(c) for c in choices),
                     required=bool(raw.get('required')),
                     group=_text(raw.get('group')),
                     literal=literal,
                     # `words` 这半句话必须在**归一之前**读（见 `__post_init__`）
                     multi=bool(raw.get('multi')) or raw_kind in MULTI_ALIASES,
                     limit=bool(raw.get('limit')),
                     placeholder=_text(raw.get('placeholder')),
                     position=position, kw=kw)


def field_of_slot(slot: dict) -> FieldSpec:
    """声明里的**一格**（`pattern[]` / `clauses[]`）→ 界面字段。

    这是"解析器词汇 → 字段"的**唯一**映射点。`slot` 认不出 `kind` 时按
    `variable` 画（不报错：老清单里本来就有各种写法）。
    """
    slot = dict(slot or {})
    raw_kind = str(slot.get('kind') or 'word').strip()
    kind = normalize_kind(raw_kind) or 'variable'
    if kind not in FIELD_KINDS:
        kind = 'variable'
    # 固定字格的 key **就是那个字**（`{"kind": "keyword", "text": "with"}`）；
    # 取值格用列名。这与 `statement_registry.spec_columns` 的口径一致。
    name = str(slot.get('column') or slot.get('name')
               or slot.get('text') or '').strip()
    return FieldSpec(
        key=name,
        label=str(slot.get('column') or slot.get('label') or slot.get('name')
                  or slot.get('text') or name),
        kind=kind,
        help=str(slot.get('help') or ''),
        choices=tuple(str(c) for c in (slot.get('options')
                                       or slot.get('choices') or ())),
        required=bool(slot.get('required', True)),
        group=str(slot.get('group') or ''),
        literal=str(slot.get('text') or '') if kind in ('keyword', 'optional')
        else '',
        multi=bool(slot.get('multi')) or raw_kind in MULTI_ALIASES,
        limit=bool(slot.get('limit')))


def slot_of_field(field_spec: FieldSpec) -> dict:
    """
    字段 → 声明里的**一格**（`pattern[]` 的形状）。与 `field_of_slot` 互逆。

    这是"在扩展窗口里配出来的写法"能变成**运行时声明**的那一步：扩展点的
    字段表经这里变成 `pattern`，再交给 `statement_registry`（与手写
    `storygraph.rules.json` 走同一条装配线）。

    ⚠ `required` **显式写两边**：`authoring.pattern.parse_pattern` 缺省是
      True（语句参数本来就该有），而 `FieldSpec.required` 缺省是 False
      （界面上的"必填"要人自己勾）。不写就等于猜，猜错的方向恰好是
      "我的可选参数被当成必填"。
    """
    kind = field_spec.parser_kind
    out: dict = {'kind': kind, 'required': bool(field_spec.required)}
    if kind in ('keyword', 'optional'):
        out['text'] = field_spec.shown_literal
        out['column'] = field_spec.key
    else:
        out['column'] = field_spec.key
    if field_spec.choices:
        out['options'] = [str(c) for c in field_spec.choices]
    if field_spec.group:
        out['group'] = field_spec.group
    if field_spec.multi or kind in MULTI_BY_DEFAULT:
        out['multi'] = True
    if field_spec.limit:
        out['limit'] = True
    # 提示（`placeholder`）与说明（`help`）跟着格子走（2026-11-07 第七轮）：
    # 从前它们只活在**扩展窗口**那半边，卡片上的参数格拿不到 ——
    # 字段表上写的"留空 = 不写这一格"到不了人眼前。`parse_pattern` 会原样
    # 带过去，`spec_columns` 再把它们放进列描述（`_column_entry`）。
    if field_spec.placeholder:
        out['placeholder'] = field_spec.placeholder
    if field_spec.help:
        out['help'] = field_spec.help
    return out


def field_of_column(col: dict) -> FieldSpec:
    """技术调用的**一格参数**（`calls[].columns[]`）→ 界面字段。

    与 `column_of_field` 互逆（判据 `test_column_round_trip`）。这是把
    `rules.json` 里那条调用的形状搬进扩展窗口的唯一映射点：以前投影只带
    `name/label`，`kind` 一律按 `variable` 画，`choices` / `position` / `kw`
    **全丢** —— 于是"窗口里看到的形状"和"真声明"不是同一个东西，照着它
    再存一份就变成了另一条调用（关键字参数变成第 3 个位置参数）。

    `kind` 认不出（老清单里什么写法都有）按 `variable`，不报错：宁可按
    任意值画，也不让一条调用从窗口里消失。
    """
    col = dict(col or {})
    name = str(col.get('name') or '').strip()
    raw_kind = str(col.get('kind') or '').strip() or 'word'
    kind = normalize_kind(raw_kind) or 'variable'
    if kind not in FIELD_KINDS:
        kind = 'variable'
    kw = str(col.get('kw') or '').strip()
    position = None
    if not kw:
        raw_pos = col.get('position', col.get('positional'))
        try:
            position = int(raw_pos)
        except (TypeError, ValueError):
            position = None
        if position is not None and position < 0:
            position = None
    return FieldSpec(key=name,
                     label=str(col.get('label') or name),
                     kind=kind,
                     choices=tuple(str(c) for c in (col.get('choices') or ())),
                     required=bool(col.get('required')),
                     position=position, kw=kw,
                     placeholder=str(col.get('placeholder') or ''),
                     help=str(col.get('help') or ''))


def _put_hints(out: dict, spec: FieldSpec) -> None:
    """
    把提示 / 说明放进一条列描述（**有才写**，老文件不多出空键）。

    为什么单独一个函数：`column_of_field` 已经在分支棘轮上（21 条），
    这段是纯拼装 —— 拼装就该有自己的名字（与 `_column_entry` 同一条理由）。
    """
    for key, value in (('placeholder', spec.placeholder), ('help', spec.help)):
        if value:
            out[key] = value


def column_of_field(field_spec: FieldSpec, position=None) -> dict:
    """界面字段 → 技术调用的**一格参数**（`calls[].columns[]`）。互逆。

    `position` 是**兜底位置号**：字段自己没写 `position` 也没写 `kw` 时用它
    （`bridge.call_declarations` 按行序数出来的那个）。为什么不在字段上补
    默认值：老 `storygraph.ext.json` 里没有 `position` 这一位，"按行序"是它
    当时的意思，补上就会把老文件的语义改掉。

    ⚠ 键名带 `=`（`layer=`）是 2026-11-07 之前的**唯一**关键字写法，
      `statement_registry._calls_from_manifest` 还认它，所以这里也继续认：
      老文件不改一个字节，新写入一律用 `kw` 那一位。
    """
    key = str(field_spec.key or '').strip()
    kw = str(field_spec.kw or '').strip()
    name = key
    if not kw and field_spec.position is None and key.endswith('=') \
            and len(key) > 1 and kw_ok(key[:-1]):
        # 老记号只在"去掉等号确实是个参数名"时才翻（`layer=`）；
        # `图层=` 这种翻出来是个非法关键字名，会被 `_calls_from_manifest`
        # **整格丢掉** —— 不如按原名留成位置参数，让人在界面上看得见它。
        kw = key[:-1]
    if kw and key.endswith('=') and kw == key[:-1]:
        # 键名还是老的 `layer=`、但 `kw` 已经写明白了（窗口存过一次）：
        # 列名用去掉等号的那个（参数在剧本里就叫 `layer`）。
        name = key[:-1]
    label = str(field_spec.label or '').strip() or name
    out: dict = {'name': name, 'label': label}
    if kw:
        out['kw'] = kw
    else:
        out['position'] = (int(field_spec.position)
                           if field_spec.position is not None
                           else (int(position) if position is not None else 0))
    # 不写 `kind: "variable"`（那是缺省，写了会让每一格都多一个键，
    # 也让 round-trip 把 `kind: "word"` 这种老写法改成 `variable`）
    if field_spec.kind and field_spec.kind != 'variable':
        out['kind'] = field_spec.kind
    if field_spec.choices:
        out['choices'] = [str(c) for c in field_spec.choices]
    if field_spec.required:
        out['required'] = True
    _put_hints(out, field_spec)
    return out


@dataclass
class ExtensionPoint:
    """一个可插拔的**能力**：一种写法 / 一个调用 / 一个字段 / 一条批处理。"""

    id: str
    kind: str = 'statement'
    label: str = ''
    docs: str = ''
    #: 是否使用（用户点名的开关之一）
    enabled: bool = True
    #: 是否能够批处理（用户点名的开关之二）
    batchable: bool = False
    #: 批处理规则（形状同 `app.batch_edit` 的一条规则；只在 `batchable` 时有意义）
    batch: dict = _dc_field(default_factory=dict)
    fields: list = _dc_field(default_factory=list)
    #: 来源：`builtin` / `project` / `plugin:<文件名>` / `rules.json`
    source: str = 'project'
    #: **只带开关、不带声明**（2026-11-07 第二轮）。
    #:
    #: 工具自带的那几条与旧清单的投影，它们的"形状"是别处的真源
    #: （`spec_args` / `rules.json`）。在扩展窗口里只能改「是否使用」和
    #: 「是否能够批处理」；如果把这些字段存成一份新声明，就会**有损覆盖**
    #: 真声明（别名 / 块体 / 子句 / 渲染都在转换里丢了）。
    #: 所以存下来的是这个标记：`bridge` 见到它就不产出运行时声明。
    override: bool = False
    #: **技术调用**的演出元数据（2026-11-07 第五轮）。
    #:
    #: 包装函数本身不是转场，但项目可以说"它等价于哪个效果、时长取第几个
    #: 参数"（`app.stage._declared_call_transitions` 就是这么演的）。
    #: 这两句话以前只有 `storygraph.rules.json` 能写（`calls[].effect` /
    #: `seconds_arg`）—— 扩展窗口里新建的调用**存不下**它们，于是"扩展点里
    #: 声明的转场"在舞台预览里既没有时长、也标"认不出"。
    #: `effect` 空 = 不假装是什么效果（照样列出"这里有一次调用"）。
    effect: str = ''
    #: 时长取第几个位置参数（`None` = 不按参数算）。与 `effect` 配套。
    seconds_arg: object = None

    def field_specs(self) -> list:
        return [f for f in self.fields if isinstance(f, FieldSpec)]

    def field(self, key: str):
        for f in self.field_specs():
            if f.key == key:
                return f
        return None

    def pattern(self) -> list:
        """字段表 → 解析器的 `pattern`（空 key 的格子跳过）。"""
        return [slot_of_field(f) for f in self.field_specs() if f.key]

    def to_dict(self) -> dict:
        out = {'id': self.id, 'kind': self.kind,
               'label': self.label, 'docs': self.docs,
               'enabled': bool(self.enabled)}
        if self.override:
            # 只带开关：**不写 fields**（借来的字段是显示用的，不是声明）
            out['override'] = True
        elif self.fields:
            out['fields'] = [f.to_dict() for f in self.field_specs()]
        # 调用元数据：只在**技术调用**且真写了的时候落盘（别的种类写了也没人读，
        # 而恒为空的键会让每一份 ext.json 都长两个空位）。
        # `override` 记录不写：它的形状归原处（闸门在 `bridge`），写了就是
        # 一份跟真声明可能已经不一致的副本。
        if self.kind == 'call' and not self.override:
            if self.effect:
                out['effect'] = self.effect
            if self.seconds_arg is not None:
                out['seconds_arg'] = int(self.seconds_arg)
        if self.batchable:
            out['batchable'] = True
            out['batch'] = dict(self.batch)
        return out


_EXT_KEYS = frozenset({'id', 'kind', 'label', 'docs', 'enabled',
                       'batchable', 'batch', 'fields', 'source', 'override',
                       'effect', 'seconds_arg'})


def parse_extension(raw, source: str = 'project') -> ExtensionPoint:
    """一条扩展点 dict → `ExtensionPoint`（不认识就抛 `ExtError`，不静默吞）。"""
    if not isinstance(raw, dict):
        raise ExtError(f'扩展点必须是对象，收到 {type(raw).__name__}')
    extra = set(raw) - _EXT_KEYS
    if extra:
        raise ExtError(f'扩展点里有不认识的键：{"、".join(sorted(extra))}')
    ident = str(raw.get('id') or '').strip()
    if not ident:
        raise ExtError('扩展点的 id 不能为空')
    kind = str(raw.get('kind') or 'statement').strip()
    if kind not in KINDS:
        raise ExtError(f'扩展点 {ident} 的 kind={kind!r} 不认识'
                       f'（可选：{"、".join(KINDS)}）')
    fields = [parse_field(f) for f in (raw.get('fields') or ())]
    batch = raw.get('batch') or {}
    if batch and not isinstance(batch, dict):
        raise ExtError(f'扩展点 {ident} 的 batch 必须是对象')
    seconds = raw.get('seconds_arg')
    if seconds is None or (isinstance(seconds, str) and not seconds.strip()):
        seconds = None
    else:
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            raise ExtError(f'扩展点 {ident} 的 seconds_arg 必须是整数'
                           f'（第几个参数，0 起），收到 {raw.get("seconds_arg")!r}'
                           ) from None
        if seconds < 0:
            raise ExtError(f'扩展点 {ident} 的 seconds_arg 不能是负数（{seconds}）')
    return ExtensionPoint(id=ident, kind=kind,
                          label=str(raw.get('label') or ''),
                          docs=str(raw.get('docs') or ''),
                          enabled=bool(raw.get('enabled', True)),
                          batchable=bool(raw.get('batchable')),
                          batch=dict(batch), fields=fields,
                          source=str(raw.get('source') or source),
                          override=bool(raw.get('override')),
                          effect=str(raw.get('effect') or '').strip(),
                          seconds_arg=seconds)


def to_cue_rule(point: ExtensionPoint, rule_id: str = '') -> dict:
    """
    扩展点 → **`app.batch_edit` 的一条规则**（纯转换，不校验）。

    `batch` 段直接就是规则体（`when` / `insert` / `replace` / `delete`）；
    这里只补 `id` 并做**最低限度**的形状检查——真正的白名单在
    `app.batch_edit._RULE_KEYS` 那一侧（唯一真源）。

    没开 `batchable` / 没有 `batch` → 返回 `{}`（调用方据此跳过，不猜）。

    ⚠ 产出的是**普通 dict**，`batch_edit.plan_batch` 要的是 `CueRule`：
      消费方得先过 `app.batch_edit.parse_rules()`（或 `rules_from_extensions()`
      一步到位）。上一版文档写着"直接产出引擎能吃的那份规则"，
      实测直接喂给 `plan_batch` 会 `AttributeError: 'dict' has no attribute
      'when'` —— 这就是"开关看着能开、批处理真跑不了"的那种断链。

    ✅ 2026-11-07 第三轮起，"开关开着的声明"由这两条真路消费：
      `cli.py batch`（默认合并）与 `ui_qt.dialog_batch.BatchDialog`
      （随计划跑 + 可一键导入列表），去重口径见
      `app.batch_edit.merge_declared_rules`。
    """
    if not point.batchable or not point.batch:
        return {}
    rule = dict(point.batch)
    rule.setdefault('id', rule_id or point.id)
    return rule
