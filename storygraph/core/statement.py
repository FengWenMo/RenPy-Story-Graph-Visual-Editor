"""
Statement 类型系统（Phase 1 Step 2 S1）。

零依赖：不 import 项目内其他模块，可被独立单测。

设计要点：
  · kind 用字符串常量类（非 Enum），与 Excel 列直接对应
  · payload 用 dict（非子类），Phase 2 加列不改类结构
  · content_hash 是"内容指纹"，不含 label 前缀；唯一化交给消费方
  · 每种 kind 显式定义哈希输入格式，不用 canonical(payload)

与 TranslationUnitGenerator 的契约：
  · content_hash 与 TU 的 hash 部分必须逐字节一致。
    ⚠ 这句说的是"**两边算法要一致**"（唯一真源在 `core/uid.py`），
    **不是**"必须等于某个外部生产的值" —— 它是本项目自己算的指纹。
    所以位数可以一起改（8 → 16，见 `CONTENT_HASH_HEX`），
    不存在"必须迁就 Ren'Py 原生 tl 文件"这回事。
  · uid 拼接由 core.uid.make_translation_uid 统一负责
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

#: `$` 语句"紧凑写法"的**行首标记**（见 `Statement.make_python`）。
#:#: 2026-10-05 二次修正（第一次是把三处硬编码收敛到这一个常量）：
#: 标记**不能再是控制字符**。
#:
#: 上一版用 `'\x01'` 承载"原文紧凑写 `$code`"，但这一位要经过「代码」列
#: 进出 Excel，而 `openpyxl` 会直接拒收控制字符
#: （`IllegalCharacterError: … cannot be used in worksheets`）——
#: 于是写表时只能把它 `.replace()` 掉（见 `beat_model.compose_code_cell`），
#: **读回来就再也认不出**：真实项目实测 22 条 `$persistent…` 的紧凑写法在
#: `rpy → xlsx → rpy` 往返里被改写成 `$ persistent…`（23/464 = 5% 的代码丢失）。
#:
#: 换成 Unicode **私用区**字符：非控制字符，Excel 原样保存，人眼也看不见
#: （不会污染卡片和表格里显示的代码）。半角/全角空格之类的"看起来像空白"
#: 的候选都不行 —— Excel 会顺手把它们归一到普通空格。
#:
#: 约定只能有一份：谁要拼 `$` 语句、谁要判分类，都从这里拿
#: （以前它散在三处各自硬编码，`pipeline/renpy_exporter` 根本不知道它，
#: 于是把控制字符原样写进了 .rpy：`$ ^Apersistent.lock_lst["cg01"]= True`）。
MARKER = '\ue000'


def is_tight_dollar(code) -> bool:
    """这条 python op 原文是紧凑写法（`$x = 1`，`$` 后没有空格）吗？"""
    return str(code or '').startswith(MARKER)


def strip_marker(code) -> str:
    """去掉行首那个记号，拿到**真正的代码文本**（分类 / 抽变量 / 渲染都要先过它）。"""
    return str(code or '').lstrip(MARKER)


# ===================== python op ↔ Statement（结构债迁移用） =====================
#
# 用户 2026-10-05：
# > 开始迁移，处理结构债
#
# 背景（审计结论"双 IR 并存"）：`Graph.Node.operations` 存的是**裸代码字符串**，
# 下游（导出 / 分析 / 分类 / 抽变量）各自用正则或 AST 反解它；而 `core.Statement`
# 这套结构化 IR 一直只服务于"拍"那条路。迁移之后：
#
#     graph.Node.statements : list[Statement]      ← 结构化真源
#     graph.Node.operations : list[str]            ← 兼容视图（派生，老调用方照旧）
#
# 这两个函数就是"字符串 ↔ 语句"的唯一转换点（**含行首标记**这一位，
# 以前它散落在三处）。

def python_operation(code) -> 'Statement':
    """裸代码字符串 → PYTHON 语句（保留"紧凑 `$code`"这一位）。"""
    text = str(code if code is not None else '')
    tight = is_tight_dollar(text)
    return Statement.make_python(strip_marker(text), dollar_space=not tight)


def operation_source(stmt) -> str:
    """
    语句 → 裸代码字符串（`Node.operations` 的取值方式）。

    只对 PYTHON / RAW 有定义：其余 kind 返回空串 —— 它们**不是**"操作"，
    调用方该看 `stmt.kind`，而不是把它硬塞回字符串。
    """
    if not isinstance(stmt, Statement):
        return str(stmt or '')
    if stmt.kind == StatementKind.PYTHON:
        return str(stmt.payload.get('code', '') or '')
    if stmt.kind == StatementKind.RAW:
        return str(stmt.payload.get('text', '') or '')
    return ''


def is_code_statement(stmt) -> bool:
    """这条语句是不是"代码/原生语句"（= 以前 `operations` 里的东西）？"""
    return (isinstance(stmt, Statement)
            and stmt.kind in (StatementKind.PYTHON, StatementKind.RAW))


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


#: `content_hash` 的**十六进制位数**（唯一真源，见 `_compute_content_hash`）。
#:
#: **为什么是 16 而不是 8**（P3-15，2026-10-05）
#: -------------------------------------------------
#: 原来是 `md5(...).hexdigest()[:8]` —— 只有 **32 bit**。风险清单把它列为
#: P3-15：8 位 hex 的生日碰撞门槛是 `2**16 = 65536` 个不同内容，
#: 也就是**约 65% 概率在 7.7 万条时撞一次**。真实项目（sr_general）
#: 已经 3800 条 → 约 0.17%；一个 30 章的中大型项目就上到 1% 量级。
#:
#: 撞了会怎样（**不是**丢内容，是"身份"错）：
#:   · `make_translation_uid` 把 `{label}_{hash}` 当翻译单元的**身份**，
#:     `_unique_id` 用 `_1`/`_2` 后缀去重 —— 两条**不同**台词撞上同一个
#:     hash 时，第二条会被改名，于是**已有译文对不上号**；
#:   · `collab_store` 拿它当"内容变没变"的判据（`row['content_hash']
#:     == chash`）—— 撞了就当没变，改动静默不同步。
#:
#: 16 位 = 64 bit，门槛抬到 `2**32 ≈ 43 亿`，对任何剧本都是零风险，
#: 而 uid 只长 8 个字符（Excel 列宽、工具链都没压力）。
#:
#: **为什么可以改**：`content_hash` 是**本项目自己算的**，不是 Ren'Py
#: 外部格式 —— 文档里那句"与 TU 的 hash 部分逐字节一致"指的是
#: `Statement` 与 `TranslationUnitGenerator` **两边算法要一致**
#: （唯一真源在 `core/uid.py`），不是"必须等于某个外部生产的值"。
#: 所以两边一起改就自洽；`tests/test_core.py` 里那几条"手动拼 md5"
#: 的断言断言的是**格式**，跟着这条常量走。
#:
#: 老数据的兼容：既有的协作库 / 旧表里存的是 8 位。
#: 我们**不做迁移**——`collab_store` 的比较是"不等 → 认为变了 → 更新"，
#: 所以第一次同步会把旧 hash 刷成新的，代价是一次多余的上行；
#: 反过来（当成没变）才危险。`tests/test_no_short_content_hash.py`
#: 把这条常量的下限钉住，防止有人手滑改回 8。
CONTENT_HASH_HEX = 16

#: 公开符号。
#:
#: `CONTENT_HASH_HEX` **必须**对外可见：`parsing/renpy_parser.py` 的
#: `TranslationUnitGenerator._unique_id` 也要用它拼翻译单元 id ——
#: 两处各写一个 `[:8]` 正是文档禁止的"各自拼接"，改位数时会只改一边。
#: 由 `tests/test_content_hash_contract.py` 守。
__all__ = ['CONTENT_HASH_HEX', 'Statement', 'StatementKind',
           'validate_hash_contract']


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


# 每种 kind 的工厂方法会产出哪些 payload 字段 —— **完整清单**。
#
# 与 _HASH_FIELDS 不同：这里覆盖**所有** kind，不只是参与哈希的那些。
# 为什么要完整的（2026-10-05，风险清单 P2-3）：
# 原来的自检只查"参与哈希的 kind"，于是**不参与哈希的 kind 声不声明都没人
# 管** —— `CALL` 的 payload 里明明有 `from_clause`（`call X from Y` 的存档点
# ID，真实项目 7 条），声明里却只写了 `target`；`SHOW` 漏了
# `at` / `as_` / `with_` / `zorder` / `onlayer` / `behind` / `is_expression`。
#
# 这份清单的正确性由 `tests/test_kind_declaration_sync.py` 守着：
# 它拿真的工厂产出与这里逐一对照，**新增字段漏改会当场变红**。
_FACTORY_FIELDS = {
    StatementKind.SAY:         ('speaker', 'text'),
    StatementKind.EXTEND:      ('text',),
    StatementKind.MENU:        ('caption',),
    StatementKind.MENU_OPTION: ('text',),
    StatementKind.SHOW:        ('image', 'at', 'as_', 'with_', 'zorder',
                                'onlayer', 'behind', 'is_expression'),
    StatementKind.SCENE:       ('image', 'at', 'with_', 'onlayer',
                                'is_expression'),
    StatementKind.HIDE:        ('image', 'with_'),
    StatementKind.PLAY:        ('action', 'channel', 'name', 'loop',
                                'fadein', 'fadeout'),
    StatementKind.VOICE:       ('name',),
    StatementKind.JUMP:        ('target',),
    StatementKind.CALL:        ('target', 'from_clause'),
    StatementKind.RETURN:      (),
    StatementKind.PYTHON:      ('code',),
    StatementKind.COMMENT:     ('text',),
    StatementKind.RAW:         ('text', 'raw_kind'),
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
    return hashlib.md5(content.encode('utf-8')).hexdigest()[:CONTENT_HASH_HEX]


@dataclass
class Statement:
    """
    演出语句。

    字段：
      kind:          StatementKind 里的常量
      payload:       按 kind 不同；见工厂方法
      condition:     前缀条件（来自 if 累积）
      source:        (file, line)，位置元信息，不参与相等比较
      content_hash:  内容指纹（`CONTENT_HASH_HEX` 位 hex，默认 16），
                     '' 表示不参与哈希；
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

        这一位用**行首标记**（`MARKER`，一个私用区字符）承载，而不是加
        payload 键：payload 形状不变，对 content_hash / 表格列 / 其他消费者
        都没有影响。标记本身**绝不会进产物**——渲染时由
        `beat_render.render_code_block` 翻回 `$` / `$ ` 两种写法。
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