"""
asset_completion.py
====================
资产 ID 自动补全。

数据源：AssetEngine
  - 五类资产：image / music / sound / transform / character
  - 三级匹配：前缀 > 子串 > 模糊（difflib）
  - 上下文感知：根据光标前的 Ren'Py 语句推断应该推荐哪类资产

每次调用实时从 engine 取数据，无需主动刷新。
"""

from __future__ import annotations

import difflib
import re
from typing import Optional


class AssetCompletion:
    """资产 ID 自动补全器。"""

    # 资产类型 -> registry 属性名
    TYPE_MAP = {
        'image': 'images',
        'music': 'music',
        'sound': 'sounds',
        'transform': 'transforms',
        'character': 'characters',
    }

    # 上下文提示：光标前的语句模式 -> 应推荐的资产类型列表
    # 按从上到下的顺序匹配，命中即返回
    CONTEXT_PATTERNS = [
        (re.compile(r'(?:^|\n)\s*show\s+\S*\s*$'),           ['image']),
        (re.compile(r'(?:^|\n)\s*scene\s+\S*\s*$'),          ['image']),
        (re.compile(r'(?:^|\n)\s*hide\s+\S*\s*$'),           ['image']),
        (re.compile(r'(?:^|\n)\s*play\s+music\s+\S*$'),      ['music']),
        (re.compile(r'(?:^|\n)\s*queue\s+music\s+\S*$'),     ['music']),
        (re.compile(r'(?:^|\n)\s*stop\s+music\s+\S*$'),      ['music']),
        (re.compile(r'(?:^|\n)\s*play\s+sound\s+\S*$'),      ['sound']),
        (re.compile(r'(?:^|\n)\s*play\s+audio\s+\S*$'),      ['sound', 'music']),
        (re.compile(r'(?:^|\n)\s*(?:show|scene)\s+.*\bat\s+\S*$'), ['transform']),
        (re.compile(r'(?:^|\n)\s*with\s+\S*$'),              ['transform']),
    ]

    def __init__(self, engine):
        """
        engine: AssetEngine 实例。必须提供 get_registry()。
        """
        self.engine = engine

    # ===================== 主接口 =====================

    def complete(
        self,
        prefix: str,
        asset_types: Optional[list] = None,
        limit: int = 20,
        fuzzy: bool = True,
        min_score: float = 0.3,
    ) -> list:
        """
        根据前缀返回候选资产。

        返回 [(asset_type, asset_id), ...]，按相关性排序：
          1. 前缀匹配（startswith）
          2. 子串匹配（substring）
          3. 模糊匹配（difflib ratio >= min_score）
        """
        registry = self.engine.get_registry()
        if registry is None:
            return []

        if asset_types is None:
            asset_types = list(self.TYPE_MAP.keys())

        prefix_low = (prefix or '').lower()
        candidates = []

        for atype in asset_types:
            attr = self.TYPE_MAP.get(atype)
            if not attr:
                continue
            data = getattr(registry, attr, {})
            if not data:
                continue

            for aid in data.keys():
                aid_low = aid.lower()

                if not prefix_low:
                    # 空前缀：按字母序返回全部
                    candidates.append((3, 0, atype, aid))
                    continue

                if aid_low.startswith(prefix_low):
                    candidates.append((0, len(aid), atype, aid))
                elif fuzzy:
                    idx = aid_low.find(prefix_low)
                    if idx >= 0:
                        candidates.append((1, idx, atype, aid))
                    else:
                        ratio = difflib.SequenceMatcher(
                            None, prefix_low, aid_low
                        ).ratio()
                        if ratio >= min_score:
                            candidates.append((2, -ratio, atype, aid))

        candidates.sort(key=lambda x: (x[0], x[1], x[3]))
        return [(c[2], c[3]) for c in candidates[:limit]]

    def complete_in_context(
        self,
        text_before_cursor: str,
        prefix: str,
        limit: int = 20,
    ) -> list:
        """
        上下文感知补全：根据光标前的文本推断应该推荐哪类资产。
        若无法推断，则退化为全类型补全。
        """
        asset_types = self._infer_types_from_context(text_before_cursor)
        return self.complete(prefix, asset_types=asset_types, limit=limit)

    # ===================== 上下文推断 =====================

    def _infer_types_from_context(self, text_before_cursor: str) -> list:
        """根据光标前的文本推断资产类型。"""
        if not text_before_cursor:
            return list(self.TYPE_MAP.keys())

        # 取最后 200 个字符作为上下文窗口，避免全文扫描
        ctx = text_before_cursor[-200:]

        for pattern, types in self.CONTEXT_PATTERNS:
            if pattern.search(ctx):
                return list(types)

        return list(self.TYPE_MAP.keys())

    # ===================== 便捷查询 =====================

    def get_all(self, asset_type: str) -> list:
        """获取某类型下所有 ID（用于下拉列表）。"""
        registry = self.engine.get_registry()
        if registry is None:
            return []
        attr = self.TYPE_MAP.get(asset_type)
        if not attr:
            return []
        return list(getattr(registry, attr, {}).keys())

    def suggest_types(self) -> list:
        """返回支持的资产类型列表。"""
        return list(self.TYPE_MAP.keys())

    def contains(self, asset_type: str, asset_id: str) -> bool:
        """检查某资产是否存在于当前注册表。"""
        registry = self.engine.get_registry()
        if registry is None:
            return False
        attr = self.TYPE_MAP.get(asset_type)
        if not attr:
            return False
        return asset_id in getattr(registry, attr, {})

    def find_asset_type(self, asset_id: str) -> Optional[str]:
        """根据 ID 反查资产类型，找不到返回 None。"""
        registry = self.engine.get_registry()
        if registry is None:
            return None
        for atype, attr in self.TYPE_MAP.items():
            if asset_id in getattr(registry, attr, {}):
                return atype
        return None