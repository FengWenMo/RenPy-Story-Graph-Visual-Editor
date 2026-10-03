"""
翻译单元 uid 的拼接契约。

唯一真源：Statement 与 TranslationUnitGenerator 都调用这里，
禁止各自拼接 f"{label}_{hash}"。

格式：{safe_label}_{content_hash}
  · safe_label: label 中非 \\w 字符替换为下划线，去首尾下划线；
                空 label 退化为 'unnamed'
  · content_hash: `statement.CONTENT_HASH_HEX` 位 hex（2026-10-05 起是 **16**，
    原来是 8 —— 32 bit 的生日碰撞门槛只有 65536 个内容，
    撞上会让两条不同台词共用一个翻译单元身份，已有的译文就对不上号了。
    位数只由那一个常量决定，本模块**不重复写死**。）

不保证可反解（label 可能含下划线，冲突后缀 _1/_2 也可能撞车）。
需要 label 时从结构化对象取字段，不要从 uid 反推。

唯一化（_1/_2 后缀）由 TranslationUnitGenerator 负责，
不在本模块——本模块只做无状态字符串拼接。

注意：sanitize_label 的 \\w 是 Unicode-aware（Python 3 默认），
      中文 label 会原样保留。与历史行为一致，不能改，
      否则现有 uid 会漂移。

不 import 任何项目内模块。
"""
from __future__ import annotations

import re

_LABEL_SAFE_RE = re.compile(r'[^\w]')


def sanitize_label(label: str) -> str:
    """把 label 规范化为可用于 uid 的形式。行为必须与历史一致。"""
    return _LABEL_SAFE_RE.sub('_', label).strip('_') or 'unnamed'


def make_translation_uid(label: str, content_hash: str) -> str:
    """
    拼接翻译单元 uid。

    content_hash 为空时返回空串——调用方应跳过，不生成 TU。
    """
    if not content_hash:
        return ''
    return f"{sanitize_label(label)}_{content_hash}"