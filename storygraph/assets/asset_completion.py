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

from storygraph.core.vocabulary import ASSET_TYPE_ATTR


def score_candidates(items, prefix: str, fuzzy: bool = True,
                     min_score: float = 0.3, limit: int = 20) -> list:
    """
    「按前缀找资产」的**打分与排序唯一实现**。

    为什么必须只有一处：项目里有两套补全器——UI 用的
    `AssetCompletion` 与 `asset_extensions.AssetCompletionProvider`——
    各自抄了一份这个排序逻辑，规则已经开始**漂移**：
    一个有 `voice` 类型、一个没有；空前缀一个按字母序、一个按长度。
    同一份资产在两个入口里的推荐顺序不一致，用起来像两个工具。

    排序（从强到弱）：
      0  前缀命中（`aid` 越短越靠前：输入越少越可能是想要的）
      1  子串命中（出现位置越靠前越优先）
      2  模糊命中（difflib 相似度，取负值参与升序）
      3  空前缀（没有输入时按字母序给全部）

    items: 可迭代的 `(asset_type, asset_id)`。
    """
    prefix_low = (prefix or '').lower()
    scored = []
    for atype, aid in items:
        aid_low = str(aid).lower()
        if not prefix_low:
            scored.append((3, 0, atype, aid))
            continue
        if aid_low.startswith(prefix_low):
            scored.append((0, len(aid), atype, aid))
        elif fuzzy:
            idx = aid_low.find(prefix_low)
            if idx >= 0:
                scored.append((1, idx, atype, aid))
            else:
                ratio = difflib.SequenceMatcher(
                    None, prefix_low, aid_low).ratio()
                if ratio >= min_score:
                    scored.append((2, -ratio, atype, aid))
    scored.sort(key=lambda x: (x[0], x[1], x[3]))
    return [(c[2], c[3]) for c in scored[:limit]]


class AssetCompletion:
    """资产 ID 自动补全器（数据源：AssetEngine，每次实时取）。"""

    # 资产类型 -> registry 属性名（唯一真源：core.vocabulary）
    TYPE_MAP = ASSET_TYPE_ATTR

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

        返回 `[(asset_type, asset_id), ...]`，按相关性排序。

        这里只负责"从 registry 取哪些 ID"；**打分与排序走 `score_candidates`**
        —— 与 `asset_extensions.AssetCompletionProvider` 共用同一份规则，
        免得两个入口给出不同的推荐顺序。
        """
        registry = self.engine.get_registry()
        if registry is None:
            return []

        if asset_types is None:
            asset_types = list(self.TYPE_MAP.keys())

        items = []
        for atype in asset_types:
            attr = self.TYPE_MAP.get(atype)
            if not attr:
                continue
            for aid in (getattr(registry, attr, {}) or {}).keys():
                items.append((atype, aid))
        return score_candidates(items, prefix, fuzzy=fuzzy,
                                min_score=min_score, limit=limit)

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