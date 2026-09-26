"""
asset_panel.py
===============
资产面板数据源。

数据源：AssetEngine（含 CharacterScriptLinker / ParseResult）
  - 树形结构：按 category / tag / folder / character 分组
  - 详情：字段、引用位置
  - 搜索：跨表模糊匹配
  - 统计：类型数量、错误警告、翻译完成度等
  - 角色视角：活跃度排名、对话列表、未定义/未使用角色

每次调用实时从 engine 取数据，无需主动刷新。
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Optional


class AssetPanelData:
    """UI 面板数据源。"""

    REGISTRY_NAMES = ['images', 'music', 'sounds', 'transforms', 'characters']

    def __init__(self, engine):
        self.engine = engine

    # ===================== 树形结构 =====================

    def get_tree(self, group_by: str = 'category') -> dict:
        """
        返回 { group: { subgroup: [asset_id, ...] } }。

        group_by:
          'category'  → Category -> Tag（图片）；音频与角色各自聚合
          'tag'       → Tag -> Category
          'folder'    → 文件夹路径 -> Category
          'type'      → 资产类型 -> 子分组
        """
        registry = self.engine.get_registry()
        if registry is None:
            return {}

        tree = defaultdict(lambda: defaultdict(list))

        # ---------- 图片 ----------
        for aid, row in registry.images.items():
            if group_by == 'category':
                g = row.get('Category', 'other') or 'other'
                sg = row.get('Tag', '') or '_'
            elif group_by == 'tag':
                g = row.get('Tag', '') or '_'
                sg = row.get('Category', 'other') or 'other'
            elif group_by == 'folder':
                p = row.get('Path', '')
                # 用 posix 分隔符：否则 Windows 上会生成反斜杠分组 key
                g = str(PurePosixPath(p).parent) if p else '_'
                sg = row.get('Category', 'other') or 'other'
            elif group_by == 'type':
                g = 'image'
                sg = row.get('Category', 'other') or 'other'
            else:
                g, sg = 'other', '_'
            tree[g][sg].append(aid)

        # ---------- 音频 ----------
        for name, label in [('music', 'music'), ('sounds', 'sound')]:
            for aid in getattr(registry, name, {}):
                if group_by == 'type':
                    tree[label][label].append(aid)
                else:
                    tree[label]['_'].append(aid)

        # ---------- 变换 ----------
        for aid in registry.transforms:
            if group_by == 'type':
                tree['transform']['transform'].append(aid)
            else:
                tree['transform']['_'].append(aid)

        # ---------- 角色 ----------
        for aid, row in registry.characters.items():
            if group_by == 'type':
                tree['character']['character'].append(aid)
            elif group_by == 'category':
                # 角色按活跃度分组：有对话 / 无对话
                count = row.get('_dialogue_count', 0)
                sg = 'active' if count > 0 else 'defined_only'
                tree['character'][sg].append(aid)
            else:
                tree['character']['_'].append(aid)

        return {
            g: {sg: sorted(ids) for sg, ids in subs.items()}
            for g, subs in tree.items()
        }

    # ===================== 详情 =====================

    def get_detail(self, asset_id: str) -> Optional[dict]:
        """
        返回某资产的完整信息，含引用位置。
        """
        registry = self.engine.get_registry()
        if registry is None:
            return None

        for name in self.REGISTRY_NAMES:
            data = getattr(registry, name, {})
            if asset_id in data:
                return {
                    'category': name,
                    'id': asset_id,
                    'data': dict(data[asset_id]),
                    'references': list(
                        registry.references.get(asset_id, [])
                    ),
                }

        # 自定义表
        for tbl_name, rows in registry.custom.items():
            if asset_id in rows:
                return {
                    'category': f'custom:{tbl_name}',
                    'id': asset_id,
                    'data': dict(rows[asset_id]),
                    'references': list(
                        registry.references.get(asset_id, [])
                    ),
                }

        return None

    # ===================== 搜索 =====================

    def search(
        self,
        query: str,
        categories: Optional[list] = None,
        limit: int = 100,
    ) -> list:
        """
        跨表模糊搜索，匹配 ID / Path / Tag / Attributes / Notes。
        返回 [(category, asset_id), ...]。
        """
        registry = self.engine.get_registry()
        if registry is None or not query:
            return []

        q = query.lower()
        results = []
        cats = categories or self.REGISTRY_NAMES

        for cat in cats:
            data = getattr(registry, cat, {})
            for aid, row in data.items():
                haystack = " ".join(str(v) for v in [
                    aid,
                    row.get('Path', ''),
                    row.get('Tag', ''),
                    row.get('Attributes', ''),
                    row.get('Name', ''),
                    row.get('Notes', ''),
                ]).lower()
                if q in haystack:
                    results.append((cat, aid))
                    if len(results) >= limit:
                        return results
        return results

    # ===================== 统计 =====================

    def get_statistics(self) -> dict:
        """返回综合统计数据。"""
        registry = self.engine.get_registry()
        if registry is None:
            return {}

        stats = {
            name: len(getattr(registry, name, {}))
            for name in self.REGISTRY_NAMES
        }
        stats['custom_tables'] = len(registry.custom)
        stats['errors'] = len(registry.errors)
        stats['warnings'] = len(registry.warnings)

        # 引用统计
        referenced = set(registry.references.keys())
        all_ids = set()
        for name in self.REGISTRY_NAMES:
            all_ids.update(getattr(registry, name, {}).keys())
        stats['referenced'] = len(referenced & all_ids)
        stats['unused'] = len(all_ids - referenced)
        stats['broken_refs'] = len(referenced - all_ids)

        # 引擎侧统计（可能存在也可能不存在）
        try:
            stats['unused_assets'] = len(self.engine.find_unused_assets())
            stats['broken_references'] = len(
                self.engine.find_broken_references())
        except Exception:
            pass

        # 剧本侧统计（依赖 linker / parse_result）
        linker = getattr(self.engine, 'linker', None)
        parse_result = getattr(self.engine, 'parse_result', None)
        if parse_result is not None:
            stats['total_dialogues'] = len(parse_result.dialogues)
            stats['total_labels'] = len(parse_result.labels)
        if linker is not None:
            try:
                stats['undefined_characters'] = len(
                    linker.find_undefined_characters())
                stats['unused_characters'] = len(
                    linker.find_unused_characters())
                stats['translation_status'] = linker.translation_status()
            except Exception:
                pass

        return stats

    def get_category_breakdown(self) -> dict:
        """图片的 Category 分布。"""
        registry = self.engine.get_registry()
        if registry is None:
            return {}
        breakdown = defaultdict(int)
        for row in registry.images.values():
            breakdown[row.get('Category', 'other') or 'other'] += 1
        return dict(breakdown)

    # ===================== 角色视角 =====================

    def get_character_ranking(self) -> list:
        """
        角色活跃度排名（按对话行数降序）。
        返回 [{'variable_name', 'display_name', 'dialogue_count'}, ...]。
        """
        linker = getattr(self.engine, 'linker', None)
        if linker is None:
            return []
        try:
            return linker.character_activity_ranking()
        except Exception:
            return []

    def get_character_dialogues(self, var_name: str) -> list:
        """
        返回某角色的所有对话行。
        [{'text', 'label', 'source_file', 'source_line', 'is_menu_option'}, ...]
        """
        linker = getattr(self.engine, 'linker', None)
        if linker is None:
            return []
        dialogues = linker.character_dialogues.get(var_name, [])
        return [
            {
                'text': d.text,
                'label': d.label,
                'source_file': d.source_file,
                'source_line': d.source_line,
                'is_menu_option': d.is_menu_option,
            }
            for d in dialogues
        ]

    def get_undefined_characters(self) -> list:
        """剧本引用了但未定义的角色。"""
        linker = getattr(self.engine, 'linker', None)
        if linker is None:
            return []
        try:
            return linker.find_undefined_characters()
        except Exception:
            return []

    def get_unused_characters(self) -> list:
        """定义了但从未使用的角色。"""
        linker = getattr(self.engine, 'linker', None)
        if linker is None:
            return []
        try:
            return linker.find_unused_characters()
        except Exception:
            return []

    # ===================== 依赖视角 =====================

    def get_broken_references(self) -> list:
        """脚本引用但资产不存在的引用列表。"""
        try:
            return self.engine.find_broken_references()
        except Exception:
            return []

    def get_unused_assets(self) -> list:
        """未被脚本引用的资产列表。"""
        try:
            return self.engine.find_unused_assets()
        except Exception:
            return []

    # ===================== 分组工具 =====================

    def get_all_group_names(self, group_by: str = 'category') -> list:
        """获取某分组方式下的所有顶层组名，用于构建下拉。"""
        tree = self.get_tree(group_by=group_by)
        return sorted(tree.keys())