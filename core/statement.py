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
from typing import Optional


class StatementKind:
    """Statement 的 kind 常量。"""
    SAY = 'say'
    EXTEND = 'extend'
    SHOW = 'show'
    SCENE = 'scene'
    MENU = 'menu'
    MENU_OPTION = 'menu_option'
    JUMP = 'jump'
    CALL = 'call'
    RETURN = 'return'
    PYTHON = 'python'
    UNKNOWN = 'unknown'

    ALL = (SAY, EXTEND, SHOW, SCENE, MENU, MENU_OPTION,
           JUMP, CALL, RETURN, PYTHON, UNKNOWN)


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
    StatementKind.JUMP:        (None, None),
    StatementKind.CALL:        (None, None),
    StatementKind.RETURN:      (None, None),
    StatementKind.PYTHON:      (None, None),
}


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
        # payload 是 dict，不可哈希；用 content_hash + kind + condition
        # 作为稳定键。content_hash 为空时用 sorted items 兜底。
        if self.content_hash:
            payload_key = self.content_hash
        else:
            payload_key = tuple(sorted(
                (k, str(v)) for k, v in self.payload.items()
            ))
        return hash((self.kind, payload_key, self.condition))

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
    def make_call(cls, target, condition='', source=('', 0)):
        return cls(
            kind=StatementKind.CALL,
            payload={'target': target},
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
    def make_python(cls, code, condition='', source=('', 0)):
        return cls(
            kind=StatementKind.PYTHON,
            payload={'code': code},
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
        StatementKind.MENU:        ('caption',),
        StatementKind.MENU_OPTION: ('text',),
        StatementKind.JUMP:        ('target',),
        StatementKind.CALL:        ('target',),
        StatementKind.RETURN:      (),
        StatementKind.PYTHON:      ('code',),
        StatementKind.UNKNOWN:     (),
    }
    for f in required.get(stmt.kind, ()):
        if f not in stmt.payload:
            issues.append(f"{stmt.kind} 缺少字段: {f}")
        elif stmt.payload[f] == '' and f != 'speaker':
            # speaker 允许为空（旁白）
            issues.append(f"{stmt.kind} 字段 {f} 为空")

    return issues