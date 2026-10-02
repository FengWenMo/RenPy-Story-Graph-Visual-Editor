"""
Statement 类型系统（Phase 1 Step 2 S1）。

零依赖：不 import 项目内其他模块，可被独立单测。

设计要点：
  · kind 用字符串常量类（非 Enum），与 Excel 列直接对应
  · payload 用 dict（非子类），Phase 2 加列不改类结构
  · content_hash 是"内容指纹"，不含 label 前缀；唯一化交给消费方
  · 每种 kind 显式定义哈希输入格式，不用 canonical(payload)

与 TranslationUnitGenerator 的契约：
  · content_hash 与 TU 的 hash 部分必须逐字节一致
  · uid 拼接由 core.uid.make_translation_uid 统一负责
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

#: `$` 语句"紧凑写法"的**行首标记**（见 `Statement.make_python`）。
#:
#: 为什么要有这个常量（2026-10-05 审计"双 IR / 两套生成器"时抓到的真 bug）：
#: 这个标记以前是**三处各自硬编码**的 `'\x01'`（`statement.py` 写、`beat_model.py`
#: 读、`beat_render.py` 再读），而 `pipeline/renpy_exporter` **根本不知道它** ——
#: 于是导出控制流时把标记原样写进了 .rpy：
#:
#:     $ ^Apersistent.lock_lst["cg01"]= True      ← 控制字符，Ren'Py 直接不认
#:
#: 约定只能有一份：谁要拼 `$` 语句、谁要判分类，都从这里拿。
MARKER = '\x01'


def is_tight_dollar(code) -> bool:
    """这条 python op 原文是紧凑写法（`$x = 1`，`$` 后没有空格）吗？"""
    return str(code or '').startswith(MARKER)


def strip_marker(code) -> str:
    """去掉行首那个记号，拿到**真正的代码文本**（分类 / 抽变量 / 渲染都要先过它）。"""
    return str(code or '').lstrip(MARKER)


class StatementKind:
    """Statement 的 kind 常量。"""
    SAY = 'say'
    EXTEND = 'extend'
    SHOW = 'show'
    SCENE = 'scene'
    HIDE = 'hide'
    PLAY = 'play'
    VOICE = 'voice'
    MENU = 'menu'
    MENU_OPTION = 'menu_option'
    JUMP = 'jump'
    CALL = 'call'
    RETURN = 'return'
    PYTHON = 'python'
    # 注释：`# ...`。作者会说"这是给美术的表情标注"，
    # 丢掉它等于丢掉一条跨职能沟通信息，必须保真往返。
    COMMENT = 'comment'
    # 原始语句：当前模型没建模、但在 label 内确属演出流的语句
    # （`with dissolve` / `pause 1.0` / `window hide` / `call screen` …）。
    # 原样保存、原样回写——这是"容忍输入的编译器"不丢信息的关键。
    RAW = 'raw'
    UNKNOWN = 'unknown'

    ALL = (SAY, EXTEND, SHOW, SCENE, HIDE, PLAY, VOICE,
           MENU, MENU_OPTION, JUMP, CALL, RETURN, PYTHON,
           COMMENT, RAW, UNKNOWN)


# 每种 kind 参与哈希的字段
# 值 (prefix, fields)：
#   · prefix=None 表示该 kind 不参与哈希（content_hash 恒为空串）
#   · fields 是 payload 中按顺序拼接的字段名
#
# 注意 menu_option 的 prefix 是 'menu'（不是 'menu_option'），
# 这是历史兼容决策，与 TranslationUnitGenerator 现有 hash 对齐，
# 避免 Phase 7 的数千行 Excel 工作簿迁移。这不是命名失误。
_HASH_FIELDS = {
    StatementKind.SAY:         ('say',         ('speaker', 'text')),
    StatementKind.EXTEND:      ('extend',      ('text',)),
    StatementKind.MENU:        ('menu',        ('caption',)),
    StatementKind.MENU_OPTION: ('menu',        ('text',)),
    StatementKind.SHOW:        (None, None),
    StatementKind.SCENE:       (None, None),
    StatementKind.HIDE:        (None, None),
    StatementKind.PLAY:        (None, None),
    StatementKind.VOICE:       (None, None),
    StatementKind.JUMP:        (None, None),
    StatementKind.CALL:        (None, None),
    StatementKind.RETURN:      (None, None),
    StatementKind.PYTHON:      (None, None),
    StatementKind.COMMENT:     (None, None),
    StatementKind.RAW:         (None, None),
}


# 每种 kind 的工厂方法必须提供的 payload 字段。
# 与 _HASH_FIELDS 一样，是自解释契约的一部分：加字段忘了同步
# 会让 content_hash 静默错位，所以用 validate_hash_contract() 卡住。
_FACTORY_FIELDS = {
    StatementKind.SAY:         ('speaker', 'text'),
    StatementKind.EXTEND:      ('text',),
    StatementKind.MENU:        ('caption',),
    StatementKind.MENU_OPTION: ('text',),
    StatementKind.SHOW:        ('image',),
    StatementKind.SCENE:       ('image',),
    StatementKind.HIDE:        ('image',),
    StatementKind.PLAY:        ('action', 'channel', 'name'),
    StatementKind.VOICE:       ('name',),
    StatementKind.JUMP:        ('target',),
    StatementKind.CALL:        ('target',),
    StatementKind.RETURN:      (),
    StatementKind.PYTHON:      ('code',),
    StatementKind.COMMENT:     ('text',),
    StatementKind.RAW:         ('text',),
}


def validate_hash_contract() -> list:
    """
    自检：每个参与哈希的 kind，其 _HASH_FIELDS 字段必须被
    对应工厂方法的 payload 覆盖。返回问题列表（空 = 通过）。

    这正是上一轮指出的坑：加了字段却忘了同步哈希输入，
    content_hash 会静默错位，翻译单元 uid 跟着漂移。
    """
    issues = []
    for kind, (prefix, fields) in _HASH_FIELDS.items():
        if prefix is None:
            continue
        factory_fields = _FACTORY_FIELDS.get(kind)
        if factory_fields is None:
            issues.append(f"{kind}: 参与哈希但没有工厂字段声明")
            continue
        for f in fields:
            if f not in factory_fields:
                issues.append(
                    f"{kind}: 哈希输入字段 {f!r} 不在工厂 payload 中"
                )
    return issues


def _freeze(value):
    """
    把任意 payload 值递归转成可哈希、且与 == 语义一致的不可变表示。

    · dict → 按键排序的 tuple（键排序保证 {a:1,b:2} 与 {b:2,a:1} 同哈希）
    · list/tuple → tuple
    · set → 排序后的 tuple
    · 其它不可哈希对象 → 退化为 repr（兜底，不影响常见路径）
    """
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(str(v) for v in value))
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _compute_content_hash(kind: str, payload: dict) -> str:
    """
    按 _HASH_FIELDS 计算 content_hash。

    返回 '' 表示该 kind 不参与哈希（show/scene/jump/call/return/python）。
    """
    spec = _HASH_FIELDS.get(kind)
    if spec is None or spec[0] is None:
        return ''

    prefix, fields = spec
    parts = [prefix]
    for f in fields:
        v = payload.get(f, '')
        parts.append(str(v))
    content = '\0'.join(parts)
    return hashlib.md5(content.encode('utf-8')).hexdigest()[:8]


@dataclass
class Statement:
    """
    演出语句。

    字段：
      kind:          StatementKind 里的常量
      payload:       按 kind 不同；见工厂方法
      condition:     前缀条件（来自 if 累积）
      source:        (file, line)，位置元信息，不参与相等比较
      content_hash:  内容指纹（8 位 hex），'' 表示不参与哈希；
                     由工厂方法计算，不参与相等比较

    相等语义：kind + payload + condition
      · source 不同 → 仍相等（同一句话在不同位置出现）
      · content_hash 是 kind+payload 的派生值，比较时由 payload 差异决定
    """
    kind: str
    payload: dict = field(default_factory=dict)
    condition: str = ''
    source: tuple = ('', 0)
    content_hash: str = ''

    # ---------- 相等 / 哈希 ----------

    def __eq__(self, other):
        if not isinstance(other, Statement):
            return NotImplemented
        return (self.kind == other.kind
                and self.payload == other.payload
                and self.condition == other.condition)

    def __hash__(self):
        # 契约：__eq__ 相等 ⇒ __hash__ 相等。
        # payload 是 dict（值里还有 list，如 show 的 at/behind），
        # 不能直接哈希；用**与 __eq__ 同源的字段**做递归冻结，
        # 而不是用 content_hash——content_hash 对 show/scene 恒为空，
        # 会造成"两份不同的 show 语句哈希相同"（违反契约的反向）。
        return hash((self.kind, _freeze(self.payload), self.condition))

    def __repr__(self):
        summary = []
        for k in ('speaker', 'text', 'caption', 'image',
                  'target', 'as_', 'with_'):
            if k in self.payload:
                summary.append(f"{k}={self.payload[k]!r}")
        body = ', '.join(summary) if summary else repr(self.payload)
        cond = f", condition={self.condition!r}" if self.condition else ''
        return f"Statement(kind={self.kind!r}, {body}{cond})"

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return {
            'kind': self.kind,
            'payload': dict(self.payload),
            'condition': self.condition,
            'source': list(self.source),
            'content_hash': self.content_hash,
        }

    @classmethod
    def from_dict(cls, obj: dict) -> 'Statement':
        """反序列化；未知 kind 归入 UNKNOWN，保留原始 payload。"""
        kind = obj.get('kind', StatementKind.UNKNOWN)
        if kind not in StatementKind.ALL:
            kind = StatementKind.UNKNOWN
        source = obj.get('source', ['', 0])
        if isinstance(source, list):
            source = tuple(source)
        return cls(
            kind=kind,
            payload=dict(obj.get('payload', {})),
            condition=obj.get('condition', ''),
            source=source,
            content_hash=obj.get('content_hash', ''),
        )

    # ---------- 工厂方法 ----------

    @classmethod
    def make_say(cls, speaker, text, condition='', source=('', 0)):
        payload = {'speaker': speaker, 'text': text}
        return cls(
            kind=StatementKind.SAY,
            payload=payload,
            condition=condition,
            source=source,
            content_hash=_compute_content_hash(StatementKind.SAY, payload),
        )

    @classmethod
    def make_extend(cls, text, condition='', source=('', 0)):
        payload = {'text': text}
        return cls(
            kind=StatementKind.EXTEND,
            payload=payload,
            condition=condition,
            source=source,
            content_hash=_compute_content_hash(StatementKind.EXTEND, payload),
        )

    @classmethod
    def make_show(cls, image, at=None, as_='', with_='', zorder=None,
                  onlayer='', behind=None, is_expression=False,
                  condition='', source=('', 0)):
        payload = {
            'image': image,
            'at': list(at) if at else [],
            'as_': as_,
            'with_': with_,
            'zorder': zorder,
            'onlayer': onlayer,
            'behind': list(behind) if behind else [],
            'is_expression': bool(is_expression),
        }
        return cls(
            kind=StatementKind.SHOW,
            payload=payload,
            condition=condition,
            source=source,
            content_hash='',
        )

    @classmethod
    def make_scene(cls, image, at=None, with_='', onlayer='',
                   is_expression=False, condition='', source=('', 0)):
        payload = {
            'image': image,
            'at': list(at) if at else [],
            'with_': with_,
            'onlayer': onlayer,
            'is_expression': bool(is_expression),
        }
        return cls(
            kind=StatementKind.SCENE,
            payload=payload,
            condition=condition,
            source=source,
            content_hash='',
        )

    @classmethod
    def make_hide(cls, image, with_='', condition='', source=('', 0)):
        payload = {'image': image, 'with_': with_}
        return cls(
            kind=StatementKind.HIDE,
            payload=payload,
            condition=condition,
            source=source,
            content_hash='',
        )

    @classmethod
    def make_play(cls, action, channel, name, loop=False,
                  fadein=0.0, fadeout=0.0,
                  condition='', source=('', 0)):
        payload = {
            'action': action,      # 'play' / 'queue' / 'stop'
            'channel': channel,    # 'music' / 'sound' / 'audio'
            'name': name,
            'loop': bool(loop),
            'fadein': float(fadein or 0.0),
            'fadeout': float(fadeout or 0.0),
        }
        return cls(
            kind=StatementKind.PLAY,
            payload=payload,
            condition=condition,
            source=source,
            content_hash='',
        )

    @classmethod
    def make_voice(cls, name, condition='', source=('', 0)):
        payload = {'name': name}
        return cls(
            kind=StatementKind.VOICE,
            payload=payload,
            condition=condition,
            source=source,
            content_hash='',
        )

    @classmethod
    def make_menu(cls, caption, condition='', source=('', 0)):
        payload = {'caption': caption}
        return cls(
            kind=StatementKind.MENU,
            payload=payload,
            condition=condition,
            source=source,
            content_hash=_compute_content_hash(StatementKind.MENU, payload),
        )

    @classmethod
    def make_menu_option(cls, text, condition='', source=('', 0)):
        payload = {'text': text}
        return cls(
            kind=StatementKind.MENU_OPTION,
            payload=payload,
            condition=condition,
            source=source,
            content_hash=_compute_content_hash(
                StatementKind.MENU_OPTION, payload
            ),
        )

    @classmethod
    def make_jump(cls, target, condition='', source=('', 0)):
        return cls(
            kind=StatementKind.JUMP,
            payload={'target': target},
            condition=condition,
            source=source,
            content_hash='',
        )

    @classmethod
    def make_call(cls, target, condition='', source=('', 0), from_clause=''):
        return cls(
            kind=StatementKind.CALL,
            payload={'target': target, 'from_clause': from_clause},
            condition=condition,
            source=source,
            content_hash='',
        )

    @classmethod
    def make_return(cls, condition='', source=('', 0)):
        return cls(
            kind=StatementKind.RETURN,
            payload={},
            condition=condition,
            source=source,
            content_hash='',
        )

    @classmethod
    def make_python(cls, code, condition='', source=('', 0),
                    dollar_space: bool = True):
        """
        `$ python` 语句。

        `dollar_space=False` 表示原文写的是 `$code`（`$` 后无空格）。
        两种写法 Ren'Py 都认，但**往返要保真** —— 原文 `$persistent.x= True`
        导回去变成 `$ persistent.x= True`，会让每个 `$` 行都成为一个 diff 块。

        这一位用**行首标记 `\\x01`** 承载（而不是加 payload 键）：
        payload 形状不变，对 content_hash / 表格列 / 其他消费者都没有影响。
        """
        if not dollar_space:
            code = MARKER + (code or '')
        return cls(
            kind=StatementKind.PYTHON,
            payload={'code': code},
            condition=condition,
            source=source,
            content_hash='',
        )

    @classmethod
    def make_comment(cls, text, condition='', source=('', 0)):
        """整行注释（不含前导 '#'）。"""
        return cls(
            kind=StatementKind.COMMENT,
            payload={'text': text},
            condition=condition,
            source=source,
            content_hash='',
        )

    @classmethod
    def make_raw(cls, text, raw_kind='', condition='', source=('', 0)):
        """
        未被结构化建模、但属于演出流的原始语句，原样保存。

        raw_kind 只是诊断标签（如 'with' / 'pause' / 'call_screen'），
        不参与渲染——渲染时按 text 原样回写。
        """
        return cls(
            kind=StatementKind.RAW,
            payload={'text': text, 'raw_kind': raw_kind},
            condition=condition,
            source=source,
            content_hash='',
        )


def validate_statement(stmt: Statement) -> list:
    """
    校验 Statement 的 payload 是否完整。返回问题列表（字符串）。
    空列表表示无问题。

    不抛异常——调用方按需记录 warning / error。
    """
    issues = []

    if stmt.kind not in StatementKind.ALL:
        issues.append(f"未知 kind: {stmt.kind!r}")
        return issues

    required = {
        StatementKind.SAY:         ('speaker', 'text'),
        StatementKind.EXTEND:      ('text',),
        StatementKind.SHOW:        ('image',),
        StatementKind.SCENE:       ('image',),
        StatementKind.HIDE:        ('image',),
        StatementKind.PLAY:        ('channel', 'name'),
        StatementKind.VOICE:       ('name',),
        StatementKind.MENU:        ('caption',),
        StatementKind.MENU_OPTION: ('text',),
        StatementKind.JUMP:        ('target',),
        StatementKind.CALL:        ('target',),
        StatementKind.RETURN:      (),
        StatementKind.PYTHON:      ('code',),
        StatementKind.COMMENT:     ('text',),
        StatementKind.RAW:         ('text',),
        StatementKind.UNKNOWN:     (),
    }
    for f in required.get(stmt.kind, ()):
        if f not in stmt.payload:
            issues.append(f"{stmt.kind} 缺少字段: {f}")
        elif stmt.payload[f] == '' and f != 'speaker':
            # speaker 允许为空（旁白）
            issues.append(f"{stmt.kind} 字段 {f} 为空")

    return issues