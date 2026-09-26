"""
asset_extensions.py
====================
资产模块扩展：
  - AssetSnapshot          资产快照（可序列化，用于对比）
  - AssetChange / Tracker  资产变更追踪（新增/删除/重命名/移动/修改）
  - DependencyAnalyzer     依赖分析（引用、断裂、未使用）
  - generate_rename_patch  生成脚本重命名/移动补丁
  - AssetCompletionProvider 自动补全
  - AssetPanelModel        资产面板数据源（树形、搜索、统计、详情）
  - AssetWorkspace         整合入口（加载、快照、对比、重命名、报告）

依赖：
  - asset_registry.py（主模块）
  - openpyxl
"""

from __future__ import annotations

import json
import re
import shutil
import difflib
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from typing import Iterable, Optional

from asset_registry import (
    AssetRegistry,
    parse_asset_tables,
    analyze_references,
    load_hooks,
)


# ===================== 序列化辅助 =====================

def _json_safe(obj):
    """将任意对象转换为可 JSON 序列化的形式"""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    try:
        return str(obj)
    except Exception:
        return None


def _deep_copy_row(row: dict) -> dict:
    return {k: _json_safe(v) for k, v in row.items()}


# ===================== 1. 资产快照 =====================

class AssetSnapshot:
    """
    资产注册表的可序列化快照。
    用于保存历史状态并与其他快照对比。
    """

    REGISTRY_NAMES = ['images', 'music', 'sounds', 'transforms', 'characters']

    def __init__(self, registry: AssetRegistry, timestamp: Optional[str] = None):
        self.timestamp = timestamp or datetime.now().isoformat(timespec='seconds')
        self.data = {name: {} for name in self.REGISTRY_NAMES}
        self.custom = {}
        self.references = {}
        self._populate_from(registry)

    def _populate_from(self, registry: AssetRegistry):
        for name in self.REGISTRY_NAMES:
            src = getattr(registry, name, {})
            self.data[name] = {aid: _deep_copy_row(row) for aid, row in src.items()}
        self.custom = {
            tbl: {aid: _deep_copy_row(row) for aid, row in rows.items()}
            for tbl, rows in registry.custom.items()
        }
        self.references = {
            aid: list(locs) for aid, locs in registry.references.items()
        }

    # ---------- 序列化 ----------
    def to_dict(self) -> dict:
        return {
            'timestamp': self.timestamp,
            'data': self.data,
            'custom': self.custom,
            'references': self.references,
        }

    def save(self, file_path):
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, file_path) -> 'AssetSnapshot':
        file_path = Path(file_path)
        with open(file_path, 'r', encoding='utf-8') as f:
            obj = json.load(f)
        snap = cls.__new__(cls)
        snap.timestamp = obj.get('timestamp', '')
        snap.data = obj.get('data', {})
        snap.custom = obj.get('custom', {})
        snap.references = obj.get('references', {})
        return snap

    # ---------- 便捷查询 ----------
    def all_ids(self, category: str) -> set:
        return set(self.data.get(category, {}).keys())

    def row(self, category: str, asset_id: str) -> Optional[dict]:
        return self.data.get(category, {}).get(asset_id)

    def find_by_path(self, path: str):
        """返回 [(category, asset_id)]"""
        result = []
        for cat in self.REGISTRY_NAMES:
            for aid, row in self.data.get(cat, {}).items():
                if row.get('Path') == path:
                    result.append((cat, aid))
        return result


# ===================== 2. 变更追踪 =====================

class AssetChange:
    """
    描述一次资产变更。
    change_type: 'added' / 'removed' / 'renamed' / 'moved' / 'modified'
    """
    def __init__(self, change_type: str, category: str, asset_id: str,
                 before: Optional[dict] = None,
                 after: Optional[dict] = None,
                 new_id: Optional[str] = None):
        self.change_type = change_type
        self.category = category
        self.asset_id = asset_id
        self.before = before or {}
        self.after = after or {}
        self.new_id = new_id

    @property
    def display_id(self) -> str:
        if self.change_type == 'renamed' and self.new_id:
            return f"{self.asset_id} -> {self.new_id}"
        return self.asset_id

    def describe(self) -> str:
        labels = {
            'added': '新增',
            'removed': '删除',
            'renamed': '重命名',
            'moved': '路径移动',
            'modified': '修改',
        }
        label = labels.get(self.change_type, self.change_type)
        text = f"[{label}] {self.category}/{self.display_id}"
        if self.change_type == 'moved':
            old_p = self.before.get('Path', '')
            new_p = self.after.get('Path', '')
            text += f"  {old_p}  ->  {new_p}"
        elif self.change_type == 'modified':
            diffs = self._field_diffs()
            if diffs:
                text += "  " + "; ".join(diffs)
        return text

    def _field_diffs(self):
        keys = set(self.before.keys()) | set(self.after.keys())
        diffs = []
        for k in sorted(keys):
            if k.startswith('_'):
                continue
            b = self.before.get(k)
            a = self.after.get(k)
            if b != a:
                diffs.append(f"{k}: {b!r} -> {a!r}")
        return diffs


class AssetChangeTracker:
    """
    对比两个 AssetSnapshot，生成变更列表。
    识别：
      - added      新增
      - removed    删除
      - renamed    ID 变化（Path 相同）
      - moved      路径变化（ID 相同）
      - modified   其他字段变化
    """

    @staticmethod
    def diff(old_snapshot: AssetSnapshot,
             new_snapshot: AssetSnapshot) -> list:
        changes = []

        for cat in AssetSnapshot.REGISTRY_NAMES:
            old_rows = old_snapshot.data.get(cat, {})
            new_rows = new_snapshot.data.get(cat, {})

            old_ids = set(old_rows.keys())
            new_ids = set(new_rows.keys())

            # 新增
            for aid in new_ids - old_ids:
                changes.append(AssetChange('added', cat, aid, after=new_rows[aid]))

            # 构建 path -> new_id 索引（仅对新增部分），用于识别重命名
            new_by_path = defaultdict(list)
            for nid in new_ids - old_ids:
                p = new_rows[nid].get('Path')
                if p:
                    new_by_path[p].append(nid)

            # 删除 / 重命名
            for aid in old_ids - new_ids:
                old_path = old_rows[aid].get('Path')
                matched_new = None
                if old_path and new_by_path.get(old_path):
                    matched_new = new_by_path[old_path].pop(0)
                if matched_new:
                    changes.append(AssetChange(
                        'renamed', cat, aid,
                        before=old_rows[aid], after=new_rows[matched_new],
                        new_id=matched_new
                    ))
                else:
                    changes.append(AssetChange(
                        'removed', cat, aid, before=old_rows[aid]
                    ))

            # 移动 / 修改
            for aid in old_ids & new_ids:
                old_row = old_rows[aid]
                new_row = new_rows[aid]
                if old_row.get('Path') != new_row.get('Path'):
                    changes.append(AssetChange(
                        'moved', cat, aid, before=old_row, after=new_row
                    ))
                elif old_row != new_row:
                    changes.append(AssetChange(
                        'modified', cat, aid, before=old_row, after=new_row
                    ))

        return changes

    @staticmethod
    def format_report(changes) -> str:
        if not changes:
            return "无变更。"
        lines = [f"共 {len(changes)} 项变更：", ""]
        grouped = defaultdict(list)
        for c in changes:
            grouped[c.change_type].append(c)
        order = ['added', 'removed', 'renamed', 'moved', 'modified']
        for typ in order:
            items = grouped.get(typ, [])
            if not items:
                continue
            lines.append(f"--- {typ} ({len(items)}) ---")
            for c in items:
                lines.append("  " + c.describe())
            lines.append("")
        return "\n".join(lines)


# ===================== 3. 依赖分析 =====================

class DependencyAnalyzer:
    """
    依赖分析：查找资产的引用位置、断裂引用、未使用资产。
    """

    def __init__(self, registry: AssetRegistry):
        self.registry = registry

    def references_of(self, asset_id: str):
        return list(self.registry.references.get(asset_id, []))

    def all_referenced_ids(self) -> set:
        return set(self.registry.references.keys())

    def all_asset_ids(self) -> set:
        ids = set()
        for name in ['images', 'music', 'sounds', 'transforms', 'characters']:
            ids.update(getattr(self.registry, name).keys())
        return ids

    def find_broken(self):
        """返回 [(asset_id, [引用位置])]，资产 ID 在注册表中不存在。"""
        all_ids = self.all_asset_ids()
        broken = []
        for aid, locs in self.registry.references.items():
            if aid not in all_ids:
                broken.append((aid, list(locs)))
        return broken

    def find_unused(self, categories=None):
        """
        返回 [(category, asset_id)]，资产未被任何脚本引用。
        """
        if categories is None:
            categories = ['images', 'music', 'sounds', 'transforms']
        referenced = self.all_referenced_ids()
        unused = []
        for cat in categories:
            data = getattr(self.registry, cat, {})
            for aid in data:
                if aid not in referenced:
                    unused.append((cat, aid))
        return unused

    def reference_count(self, asset_id: str) -> int:
        return len(self.registry.references.get(asset_id, []))

    def summary(self) -> dict:
        return {
            'total_assets': len(self.all_asset_ids()),
            'referenced_assets': len(self.all_referenced_ids()),
            'broken_count': len(self.find_broken()),
            'unused_count': len(self.find_unused()),
        }


# ===================== 4. 重命名 / 移动的脚本补丁 =====================

def generate_rename_patch(
    old_id: str,
    new_id: str,
    script_files: Iterable,
    dry_run: bool = False,
    backup: bool = True,
) -> dict:
    """
    在脚本文件中把 old_id 的引用替换为 new_id。
    使用词边界避免部分匹配（如 'hero' 不匹配 'hero_happy'）。

    返回：
        {
          'modified':      [已修改文件路径],
          'matched_files': [包含引用的文件路径],
          'dry_run':       bool,
        }
    """
    pattern = re.compile(r'(?<![\w])' + re.escape(old_id) + r'(?![\w])')
    modified = []
    matched_files = []

    for fp in script_files:
        fp = Path(fp)
        if not fp.exists():
            continue
        try:
            text = fp.read_text(encoding='utf-8')
        except Exception:
            continue
        if not pattern.search(text):
            continue
        matched_files.append(str(fp))
        if dry_run:
            continue
        if backup:
            backup_path = fp.with_suffix(fp.suffix + '.bak')
            try:
                shutil.copy2(fp, backup_path)
            except Exception:
                pass
        new_text = pattern.sub(new_id, text)
        try:
            fp.write_text(new_text, encoding='utf-8')
            modified.append(str(fp))
        except Exception:
            pass

    return {
        'modified': modified,
        'matched_files': matched_files,
        'dry_run': dry_run,
    }


# ===================== 5. 自动补全 =====================

class AssetCompletionProvider:
    """
    根据前缀提供资产 ID 的自动补全。
    支持类型过滤、子串匹配、模糊匹配、权重排序。
    """

    TYPE_MAP = {
        'image': 'images',
        'music': 'music',
        'sound': 'sounds',
        'transform': 'transforms',
        'character': 'characters',
    }

    def __init__(self, registry: AssetRegistry):
        self.registry = registry
        self._cache = {}
        self._build_cache()

    def _build_cache(self):
        self._cache = {}
        for atype, name in self.TYPE_MAP.items():
            data = getattr(self.registry, name, {})
            self._cache[atype] = list(data.keys())
        for tbl_name, rows in self.registry.custom.items():
            self._cache[f'custom:{tbl_name}'] = list(rows.keys())

    def refresh(self):
        self._build_cache()

    def complete(
        self,
        prefix: str,
        asset_types: Optional[list] = None,
        limit: int = 20,
        fuzzy: bool = True,
        min_score: float = 0.3,
    ):
        """
        返回 [(asset_type, asset_id), ...]，已按相关性排序。
        - 前缀匹配优先
        - 其次是子串匹配
        - 最后是模糊匹配（difflib 相似度）
        """
        if asset_types is None:
            asset_types = list(self.TYPE_MAP.keys())

        prefix_low = prefix.lower()
        candidates = []

        for atype in asset_types:
            for aid in self._cache.get(atype, []):
                aid_low = aid.lower()
                if aid_low.startswith(prefix_low):
                    candidates.append((0, len(aid), atype, aid))
                elif fuzzy:
                    idx = aid_low.find(prefix_low)
                    if idx >= 0:
                        candidates.append((1, idx, atype, aid))
                    else:
                        ratio = difflib.SequenceMatcher(None, prefix_low, aid_low).ratio()
                        if ratio >= min_score:
                            candidates.append((2, -ratio, atype, aid))

        candidates.sort(key=lambda x: (x[0], x[1], x[3]))
        return [(c[2], c[3]) for c in candidates[:limit]]

    def get_all(self, asset_type: str):
        """获取某类型下所有 ID（用于下拉列表）"""
        return list(self._cache.get(asset_type, []))

    def suggest_types(self) -> list:
        return list(self.TYPE_MAP.keys())


# ===================== 6. 资产面板数据源 =====================

class AssetPanelModel:
    """
    为 UI 面板提供数据：树形结构、搜索、详情、统计。
    """

    REGISTRY_NAMES = ['images', 'music', 'sounds', 'transforms', 'characters']

    def __init__(self, registry: AssetRegistry):
        self.registry = registry

    # ---------- 树形结构 ----------
    def get_tree(self, group_by: str = 'category') -> dict:
        """
        group_by:
          'category' 按 Category -> Tag 分组
          'tag'      按 Tag -> Category 分组
          'folder'   按文件夹路径分组
        返回：{ group: { subgroup: [asset_id, ...] } }
        """
        tree = defaultdict(lambda: defaultdict(list))

        for aid, row in self.registry.images.items():
            if group_by == 'category':
                g = row.get('Category', 'other') or 'other'
                sg = row.get('Tag', '') or '_'
            elif group_by == 'tag':
                g = row.get('Tag', '') or '_'
                sg = row.get('Category', 'other') or 'other'
            elif group_by == 'folder':
                path = row.get('Path', '')
                g = str(Path(path).parent) if path else '_'
                sg = row.get('Category', 'other') or 'other'
            else:
                g, sg = 'other', '_'
            tree[g][sg].append(aid)

        # 音频按类别聚合
        for name, label in [('music', 'music'), ('sounds', 'sound')]:
            data = getattr(self.registry, name, {})
            for aid in data:
                tree[label]['_'].append(aid)

        return {
            g: {sg: sorted(ids) for sg, ids in subgroups.items()}
            for g, subgroups in tree.items()
        }

    # ---------- 详情 ----------
    def get_detail(self, asset_id: str) -> Optional[dict]:
        for name in self.REGISTRY_NAMES:
            data = getattr(self.registry, name, {})
            if asset_id in data:
                return {
                    'category': name,
                    'id': asset_id,
                    'data': dict(data[asset_id]),
                    'references': list(self.registry.references.get(asset_id, [])),
                }
        for tbl_name, rows in self.registry.custom.items():
            if asset_id in rows:
                return {
                    'category': f'custom:{tbl_name}',
                    'id': asset_id,
                    'data': dict(rows[asset_id]),
                    'references': list(self.registry.references.get(asset_id, [])),
                }
        return None

    # ---------- 搜索 ----------
    def search(self, query: str, categories: Optional[list] = None,
               limit: int = 100):
        """模糊搜索（匹配 ID、Path、Tag、Attributes、Notes）"""
        if not query:
            return []
        q = query.lower()
        results = []
        cats = categories or self.REGISTRY_NAMES
        for cat in cats:
            data = getattr(self.registry, cat, {})
            for aid, row in data.items():
                haystack = " ".join(str(v) for v in [
                    aid,
                    row.get('Path', ''),
                    row.get('Tag', ''),
                    row.get('Attributes', ''),
                    row.get('Notes', ''),
                ]).lower()
                if q in haystack:
                    results.append((cat, aid))
                    if len(results) >= limit:
                        return results
        return results

    # ---------- 统计 ----------
    def get_statistics(self) -> dict:
        stats = {name: len(getattr(self.registry, name, {}))
                 for name in self.REGISTRY_NAMES}
        stats['custom_tables'] = len(self.registry.custom)
        stats['errors'] = len(self.registry.errors)
        stats['warnings'] = len(self.registry.warnings)

        referenced = set(self.registry.references.keys())
        all_ids = set()
        for name in self.REGISTRY_NAMES:
            all_ids.update(getattr(self.registry, name, {}).keys())
        stats['referenced'] = len(referenced & all_ids)
        stats['unused'] = len(all_ids - referenced)
        stats['broken_refs'] = len(referenced - all_ids)
        return stats

    # ---------- 按 Category 分布 ----------
    def get_category_breakdown(self) -> dict:
        breakdown = defaultdict(int)
        for row in self.registry.images.values():
            breakdown[row.get('Category', 'other')] += 1
        return dict(breakdown)


# ===================== 7. 工作区（整合入口） =====================

class AssetWorkspace:
    """
    资产工作区：整合注册表、变更追踪、依赖分析、自动补全、面板数据源。

    典型用法：

        ws = AssetWorkspace(asset_dir='asset_tables', project_root='game')
        ws.load(script_files=['game/script.rpy'])

        # 保存快照
        ws.snapshot(tag='before_edit')

        # ... 用户在外部编辑 asset_tables/*.xlsx ...

        # 重新加载并对比
        ws.load(script_files=['game/script.rpy'])
        ws.snapshot(tag='after_edit')
        changes = ws.diff_latest_two()
        print(ws.build_change_report(changes))

        # 自动补全
        for atype, aid in ws.completion.complete('bg_forest', limit=5):
            print(atype, aid)

        # 面板数据
        tree = ws.panel.get_tree(group_by='category')
        stats = ws.panel.get_statistics()
    """

    def __init__(self, asset_dir, project_root=None, snapshot_dir=None):
        self.asset_dir = Path(asset_dir)
        self.project_root = Path(project_root) if project_root else self.asset_dir.parent
        self.snapshot_dir = (
            Path(snapshot_dir) if snapshot_dir
            else self.asset_dir / '.snapshots'
        )

        self.registry: Optional[AssetRegistry] = None
        self.dependency: Optional[DependencyAnalyzer] = None
        self.completion: Optional[AssetCompletionProvider] = None
        self.panel: Optional[AssetPanelModel] = None

    # ---------- 加载 ----------
    def load(self, hooks=None, script_files=None):
        """加载资产表并初始化子组件。"""
        if hooks is None:
            hooks = load_hooks()
        self.registry = parse_asset_tables(
            self.asset_dir, hooks=hooks, project_root=self.project_root
        )
        if script_files:
            analyze_references(script_files, self.registry)

        self.dependency = DependencyAnalyzer(self.registry)
        self.completion = AssetCompletionProvider(self.registry)
        self.panel = AssetPanelModel(self.registry)
        return self.registry

    # ---------- 快照 ----------
    def snapshot(self, tag: Optional[str] = None) -> AssetSnapshot:
        if self.registry is None:
            raise RuntimeError("请先调用 load()")
        snap = AssetSnapshot(self.registry)
        name = tag or datetime.now().strftime('%Y%m%d_%H%M%S')
        snap.save(self.snapshot_dir / f'snapshot_{name}.json')
        return snap

    def list_snapshots(self):
        if not self.snapshot_dir.exists():
            return []
        return sorted(self.snapshot_dir.glob('snapshot_*.json'))

    def load_snapshot(self, path) -> AssetSnapshot:
        return AssetSnapshot.load(path)

    def diff_snapshots(self, old_path, new_path):
        old = self.load_snapshot(old_path)
        new = self.load_snapshot(new_path)
        return AssetChangeTracker.diff(old, new)

    def diff_latest_two(self):
        snaps = self.list_snapshots()
        if len(snaps) < 2:
            return []
        return self.diff_snapshots(snaps[-2], snaps[-1])

    def prune_snapshots(self, keep: int = 10):
        """只保留最近 N 个快照"""
        snaps = self.list_snapshots()
        if len(snaps) <= keep:
            return
        for old in snaps[:-keep]:
            try:
                old.unlink()
            except Exception:
                pass

    # ---------- 依赖追踪 + 重命名补丁 ----------
    def rename_asset_in_scripts(
        self,
        old_id: str,
        new_id: str,
        script_files: Iterable,
        dry_run: bool = False,
    ) -> dict:
        """在脚本中批量替换 old_id -> new_id，返回执行摘要"""
        return generate_rename_patch(
            old_id, new_id, script_files, dry_run=dry_run, backup=True
        )

    def preview_rename(self, old_id: str, new_id: str,
                       script_files: Iterable) -> dict:
        """只预览影响，不修改文件"""
        return generate_rename_patch(
            old_id, new_id, script_files, dry_run=True, backup=False
        )

    # ---------- 变更报告（含引用影响） ----------
    def build_change_report(self, changes) -> str:
        """生成变更报告，含引用影响说明"""
        lines = ["==== 资产变更报告 ====", ""]
        lines.append(AssetChangeTracker.format_report(changes))
        lines.append("")
        lines.append("==== 引用影响 ====")

        if not changes:
            lines.append("无变更，无需处理引用。")
            return "\n".join(lines)

        for ch in changes:
            if ch.change_type in ('removed', 'renamed', 'moved'):
                refs = (
                    self.registry.references.get(ch.asset_id, [])
                    if self.registry else []
                )
                if refs:
                    lines.append(f"[{ch.change_type}] {ch.asset_id} 被引用 {len(refs)} 处:")
                    for r in refs[:10]:
                        lines.append(f"    - {r}")
                    if len(refs) > 10:
                        lines.append(f"    ... 还有 {len(refs) - 10} 处")
                    # 对 renamed 提示补丁
                    if ch.change_type == 'renamed' and ch.new_id:
                        lines.append(f"    建议：将脚本中 '{ch.asset_id}' 替换为 '{ch.new_id}'")
                elif ch.change_type == 'removed':
                    lines.append(f"[removed] {ch.asset_id} 无引用")
        return "\n".join(lines)


# ===================== 命令行自测 =====================

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python asset_extensions.py <asset_dir> [project_root] [script_file...]")
        sys.exit(0)

    asset_dir = sys.argv[1]
    project_root = sys.argv[2] if len(sys.argv) > 2 else None
    script_files = sys.argv[3:] if len(sys.argv) > 3 else []

    ws = AssetWorkspace(asset_dir, project_root)
    reg = ws.load(script_files=script_files)
    print(f"图片: {len(reg.images)}, 音乐: {len(reg.music)}, 音效: {len(reg.sounds)}")

    # 统计
    print("统计:", ws.panel.get_statistics())

    # 依赖
    unused = ws.dependency.find_unused()
    broken = ws.dependency.find_broken()
    print(f"未使用资产: {len(unused)}")
    for cat, aid in unused[:5]:
        print(f"  - [{cat}] {aid}")
    print(f"断裂引用: {len(broken)}")
    for aid, locs in broken[:5]:
        print(f"  - {aid}  引用: {locs[:3]}")

    # 快照
    ws.snapshot(tag='run')
    print(f"快照已保存到 {ws.snapshot_dir}")

    # 自动补全演示
    if reg.images:
        sample = list(reg.images.keys())[0]
        prefix = sample[:3]
        print(f"\n补全前缀 '{prefix}':")
        for atype, aid in ws.completion.complete(prefix, limit=5):
            print(f"  [{atype}] {aid}")

    # 面板树示例
    tree = ws.panel.get_tree(group_by='category')
    print("\n面板树顶层节点:", list(tree.keys())[:10])