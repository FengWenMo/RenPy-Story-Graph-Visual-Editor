"""
core 包：Statement 类型系统与 uid 契约。

零依赖：core 内的模块不 import 任何项目内其他模块。
反向依赖（renpy_parser → core.uid）是允许的，单向。
"""
from .statement import Statement, StatementKind, validate_statement
from .uid import sanitize_label, make_translation_uid

__all__ = [
    'Statement', 'StatementKind', 'validate_statement',
    'sanitize_label', 'make_translation_uid',
]