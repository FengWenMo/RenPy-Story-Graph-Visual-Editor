"""
`storygraph.ext` —— **自定义能力的统一扩展层**
=============================================

四种插口合成一个形状（`ExtensionPoint`）：写法 / 技术调用 / 拍字段 / 批处理。
配套两个开关（是否使用、是否可批处理）和一条插件通道。

    from storygraph.ext import ExtensionRegistry
    reg = ExtensionRegistry.for_project(root)
    for p in reg.enabled('call'):
        print(p.id, [f.shown for f in p.field_specs()])

**一套词汇**：说一个字段是什么（`kind`：`keyword / variable / choice /
optional / number / expr / transition / code / multiline`）在解析器
（`authoring.pattern`）、界面（`DynamicForm`）、写回声明（`slot_of_field`）
三处是同一句话 —— 见 `ext.schema` 顶部那张表。

分层：本包**不 import Qt**，也不 import `storygraph.app` / `storygraph.ui_qt`
（只有函数内的惰性 import，方向 ext → story/core/authoring）。
`ext.bridge` 是运行时那一端：扩展点 → `statement_registry` / `batch_edit`
能直接吃的声明。
"""

from storygraph.ext.bridge import (call_declarations, declarations, for_project,
                                   statement_declarations)
from storygraph.ext.card_fields import (BUILTIN_CARD_FIELDS,
                                        builtin_card_fields, card_field_specs,
                                        field_overrides, overridable_keys)
from storygraph.ext.plugins import discover, write_example
from storygraph.ext.registry import MANIFEST, ExtensionRegistry
from storygraph.ext.schema import (EXT_SCHEMA, FIELD_KIND_LABELS, FIELD_KINDS,
                                   KIND_LABELS, KIND_TO_SLOT, KIND_TO_WIDGET,
                                   KINDS, ExtError, ExtensionPoint, FieldSpec,
                                   field_of_slot, normalize_kind,
                                   parse_extension, parse_field, slot_of_field,
                                   to_cue_rule)

__all__ = [
    'BUILTIN_CARD_FIELDS', 'EXT_SCHEMA', 'FIELD_KINDS', 'FIELD_KIND_LABELS',
    'KINDS', 'KIND_LABELS', 'KIND_TO_SLOT', 'KIND_TO_WIDGET', 'MANIFEST',
    'ExtError', 'ExtensionPoint', 'ExtensionRegistry', 'FieldSpec',
    'builtin_card_fields', 'call_declarations', 'card_field_specs',
    'declarations', 'discover', 'field_of_slot', 'field_overrides',
    'for_project', 'normalize_kind', 'overridable_keys', 'parse_extension',
    'parse_field', 'slot_of_field', 'statement_declarations', 'to_cue_rule',
    'write_example',
]
