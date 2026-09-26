"""
asset_engine.py
===============
资产引擎：自动监听、自动处理、自动发布事件。

职责：
  - 持有 AssetRegistry、ParseResult、CharacterScriptLinker
  - 通过 AssetFileWatcher 监听文件系统变化
  - 在变化时自动重载、对比、应用变更
  - 通过 AssetEventBus 发布事件，UI 被动订阅

线程模型：
  - AssetFileWatcher 在后台线程检测变化，只标记 dirty flag
  - 主线程定期调用 poll() 消费 dirty，执行实际处理
  - 注册表只在主线程被修改，订阅者回调也在主线程被调用

可选依赖：
  - renpy_parser / character_script_linker：若不存在，脚本解析自动禁用
"""

from __future__ import annotations
import os
import re
import shutil
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from asset_registry import (
    AssetRegistry,
    parse_asset_tables,
    analyze_references,
    load_hooks,
)
from asset_events import (
    AssetEvent,
    AssetEventBus,
    AssetEventType,
    AssetFileWatcher,
    ConfirmationGate,
)

# 脚本解析可选依赖
try:
    from renpy_parser import IncrementalRenPyAnalyzer, parse_translation_files
    _HAS_PARSER = True
except ImportError:
    _HAS_PARSER = False

try:
    from character_script_linker import CharacterScriptLinker
    _HAS_LINKER = True
except ImportError:
    _HAS_LINKER = False


# ===================== 内部数据结构 =====================

@dataclass
class _Change:
    kind: str            # 'added' / 'removed' / 'renamed' / 'moved' / 'modified'
    category: str        # 'images' / 'music' / 'sounds' / 'transforms' / 'characters'
    asset_id: str
    before: dict
    after: dict
    new_id: Optional[str] = None


# ===================== 脚本重命名补丁 =====================

def _apply_rename_patch(old_id: str, new_id: str,
                        script_files: Iterable, backup: bool = True) -> dict:
    """在脚本文件中替换 old_id -> new_id（词边界正则，避免部分匹配）。"""
    pattern = re.compile(r'(?<![\w])' + re.escape(old_id) + r'(?![\w])')
    modified = []
    matched = []

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
        matched.append(str(fp))
        if backup:
            try:
                shutil.copy2(fp, fp.with_suffix(fp.suffix + '.bak'))
            except Exception:
                pass
        try:
            fp.write_text(pattern.sub(new_id, text), encoding='utf-8')
            modified.append(str(fp))
        except Exception:
            pass

    return {'modified': modified, 'matched_files': matched}


# ===================== 资产引擎 =====================

class AssetEngine:
    """
    资产引擎。

    使用方式：
        bus = AssetEventBus()
        gate = ConfirmationGate(ask_callback=my_confirm_dialog)
        engine = AssetEngine(
            asset_dir='asset_tables',
            project_root='game',
            script_files=['game/script.rpy'],
            event_bus=bus,
            confirmation_gate=gate,
        )
        engine.start()

        # 主线程定期调用
        def poll():
            engine.poll()
            root.after(200, poll)
        poll()

        # 关闭时
        engine.stop()
    """

    TASK_TABLE = 'table'
    TASK_SCRIPT = 'script'
    TASK_SOURCE = 'source'

    _SOURCE_EXT = {
        '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif',
        '.ogg', '.mp3', '.wav', '.m4a', '.flac', '.aac', '.opus',
    }

    def __init__(
        self,
        asset_dir,
        project_root,
        script_files: Optional[Iterable] = None,
        tl_dir=None,
        event_bus: Optional[AssetEventBus] = None,
        confirmation_gate: Optional[ConfirmationGate] = None,
        watch_source_dirs: bool = True,
        poll_interval: float = 1.5, 
    ):
        self.asset_dir = Path(asset_dir)
        self.project_root = Path(project_root)
        self.script_files = [Path(p) for p in (script_files or [])]
        self.tl_dir = (Path(tl_dir) if tl_dir else (self.project_root / 'game' / 'tl')).resolve()
        self.bus = event_bus or AssetEventBus()
        self.gate = confirmation_gate or ConfirmationGate()
        self.poll_interval = poll_interval
        self.watch_source_dirs = watch_source_dirs

        # 主线程状态
        self.registry: Optional[AssetRegistry] = None
        self.parse_result = None
        self.linker = None
        self._prev_state: dict = {}
        self._analyzer = None          # 增量解析器（懒加载）

        # 线程间通信
        self._dirty: set = set()
        self._dirty_lock = threading.Lock()

        # 监听目录
        self._watch_paths = self._compute_watch_paths()

        self._watcher: Optional[AssetFileWatcher] = None
        self._running = False

    # ===================== 监听目录 =====================

    @staticmethod
    def is_under_path(path, parent) -> bool:
        """
        判断 path 是否位于 parent 目录下（含 parent 本身）。
        - 大小写不敏感（Windows 下 TL / Tl / tl 都算）
        - 对不存在的路径安全返回 False
        - 对类型异常（None / 非路径）安全返回 False
        """
        try:
            path = Path(path).resolve()
            parent = Path(parent).resolve()
        except (OSError, TypeError):
            return False

        path_parts = tuple(os.path.normcase(p) for p in path.parts)
        parent_parts = tuple(os.path.normcase(p) for p in parent.parts)

        if len(path_parts) < len(parent_parts):
            return False
        return path_parts[:len(parent_parts)] == parent_parts

    def _compute_watch_paths(self) -> list:
        paths = []
        seen = set()

        def _add(p: Path):
            try:
                key = str(p.resolve()) if p.exists() else str(p)
            except OSError:
                key = str(p)
            if key not in seen:
                seen.add(key)
                paths.append(p)

        if self.asset_dir.exists():
            _add(self.asset_dir)

        if self.watch_source_dirs:
            game_dir = self.project_root / 'game'
            if game_dir.exists():
                _add(game_dir)
            for sub in ('images', 'audio', 'music', 'sounds', 'bgm', 'gui'):
                cand = self.project_root / sub
                if cand.exists():
                    _add(cand)

        return paths

    # ===================== 生命周期 =====================

    def start(self):
        if self._running:
            return
        try:
            self._initial_load()
        except Exception as e:
            self._emit(AssetEventType.ENGINE_ERROR,
                       {'error': str(e), 'phase': 'initial_load'})
            raise

        self._watcher = AssetFileWatcher(
            paths=self._watch_paths,
            on_change=self._on_fs_change,
            interval=self.poll_interval,
        )
        self._watcher.start()
        self._running = True

        self._emit(AssetEventType.ENGINE_READY, {
            'watch_paths': [str(p) for p in self._watch_paths],
            'image_count': len(self.registry.images) if self.registry else 0,
            'music_count': len(self.registry.music) if self.registry else 0,
            'sound_count': len(self.registry.sounds) if self.registry else 0,
            'character_count': len(self.registry.characters) if self.registry else 0,
            'parser_enabled': _HAS_PARSER and _HAS_LINKER,
        })

    def stop(self):
        if self._watcher:
            self._watcher.stop()
            self._watcher = None
        self._running = False
        self._emit(AssetEventType.ENGINE_STOPPED, {})

    def is_running(self) -> bool:
        return self._running

    def reload_all(self):
        """请求一次全量重载（在下一次 poll 中执行）。"""
        # 清空增量解析器缓存，保证真正全量
        if self._analyzer is not None:
            self._analyzer.clear()
        self._mark_dirty(self.TASK_SCRIPT)
        self._mark_dirty(self.TASK_TABLE)

    # ===================== 初始加载 =====================

    def _initial_load(self):
        # 首次启动时，如果资产表不存在，自动扫描生成
        self._ensure_asset_tables_exist()

        hooks = load_hooks(project_root=self.project_root)
        self.registry = parse_asset_tables(
            self.asset_dir, hooks=hooks, project_root=self.project_root
        )
        self._load_scripts()
        self._prev_state = self._snapshot_registry_state(self.registry)

    def _ensure_asset_tables_exist(self):
        """
        如果 asset_dir 中没有任何 xlsx 文件，调用 scan_assets 生成。
        已有表则不触碰（防止覆盖用户编辑）。
        生成的表路径相对 project_root，与 validate_registry 的路径拼接一致。
        """
        if self.asset_dir.exists():
            # 有任何 xlsx 就认为用户已经配置过了，不覆盖
            if any(self.asset_dir.glob('*.xlsx')):
                return

        try:
            from asset_registry import scan_assets, write_asset_excel

            # scan_root 用 project_root，这样 Path 字段形如
            # game/images/bg/bg_1.jpg，validate_registry 拼接时才找得到文件
            assets = scan_assets(self.project_root, include_ui=True)
            write_asset_excel(assets, self.asset_dir, split_by_category=False)

            self._emit(AssetEventType.ENGINE_NOTICE, {
                'info': 'auto_scan_generated',
                'message': (
                    f"首次启动，已自动生成资产表到 {self.asset_dir} "
                    f"（图片 {len(assets['images'])}，"
                    f"音乐 {len(assets['audio']['music'])}，"
                    f"音效 {len(assets['audio']['sound'])}）"
                ),
            })
        except Exception as e:
            self._emit(AssetEventType.ENGINE_ERROR,
                       {'error': str(e), 'phase': 'auto_scan'})
            # 不抛出，让引擎继续以空注册表启动

    # ===================== 脚本加载 =====================

    def _load_scripts(self):
        """解析所有 .rpy 脚本，构建角色定义、对话、引用索引。"""
        if not (_HAS_PARSER and _HAS_LINKER):
            # 降级：只做简单的引用分析
            if self.script_files and self.registry is not None:
                self.registry.references = defaultdict(list)
                analyze_references(self.script_files, self.registry)
            return

        # 懒加载增量分析器
        if self._analyzer is None:
            self._analyzer = IncrementalRenPyAnalyzer()

        # 只解析脚本，不需要 Graph（那是 GraphView 的活）
        self.parse_result, _ = self._analyzer.analyze(
            files=self.script_files,
            graph=None,
        )

        translations = {}
        try:
            translations = parse_translation_files(self.tl_dir)
        except Exception:
            pass

        self.linker = CharacterScriptLinker(self.parse_result, translations)
        self.linker.link()

        if self.registry is None:
            return

        # 把角色定义合并进 registry，重建引用索引
        self._sync_character_definitions()
        self._rebuild_references()

    def _rebuild_references(self):
        """重建 registry.references：脚本引用 + 角色对话位置。"""
        if self.registry is None:
            return

        self.registry.references = defaultdict(list)

        if self.script_files:
            analyze_references(self.script_files, self.registry)

        if self.linker is not None:
            for var_name, dialogues in self.linker.character_dialogues.items():
                for d in dialogues:
                    self.registry.references[var_name].append(
                        f"{d.source_file}:{d.source_line}"
                    )

    # ===================== 快照与差异 =====================

    @staticmethod
    def _snapshot_registry_state(registry: AssetRegistry) -> dict:
        state = {}
        for name in ('images', 'music', 'sounds', 'transforms', 'characters'):
            data = getattr(registry, name, {})
            state[name] = {aid: dict(row) for aid, row in data.items()}
        return state

    def _diff_state(self, old_state: dict, new_state: dict) -> list:
        changes = []
        for cat in ('images', 'music', 'sounds', 'transforms', 'characters'):
            old = old_state.get(cat, {})
            new = new_state.get(cat, {})

            old_ids = set(old.keys())
            new_ids = set(new.keys())

            # 新增
            for aid in new_ids - old_ids:
                changes.append(_Change('added', cat, aid, {}, new[aid]))

            # 用 Path 识别重命名
            new_by_path = defaultdict(list)
            for nid in new_ids - old_ids:
                p = new[nid].get('Path')
                if p:
                    new_by_path[p].append(nid)

            # 删除 / 重命名
            for aid in old_ids - new_ids:
                old_path = old[aid].get('Path')
                matched_new = None
                if old_path and new_by_path.get(old_path):
                    matched_new = new_by_path[old_path].pop(0)
                if matched_new:
                    changes.append(_Change(
                        'renamed', cat, aid,
                        before=old[aid], after=new[matched_new],
                        new_id=matched_new,
                    ))
                else:
                    changes.append(_Change(
                        'removed', cat, aid, before=old[aid], after={}
                    ))

            # 移动 / 修改
            for aid in old_ids & new_ids:
                o, n = old[aid], new[aid]
                if o.get('Path') != n.get('Path'):
                    changes.append(_Change('moved', cat, aid, o, n))
                elif o != n:
                    changes.append(_Change('modified', cat, aid, o, n))

        return changes

    # ===================== 文件系统回调（后台线程） =====================

    def _on_fs_change(self, changes: list):
        """
        由 AssetFileWatcher 在后台线程调用。
        只标记 dirty flag，不做任何注册表修改。
        """
        asset_dir_resolved = self.asset_dir.resolve()

        for path, _ctype in changes:
            p = Path(path)
            suffix = p.suffix.lower()

            # ---- xlsx：只看 asset_dir 下的 ----
            if suffix == '.xlsx':
                try:
                    parent_resolved = p.resolve().parent
                except OSError:
                    parent_resolved = p.parent
                if parent_resolved == asset_dir_resolved:
                    self._mark_dirty(self.TASK_TABLE)
                continue

            # ---- rpy：主脚本才触发脚本重载，tl/ 下一律忽略 ----
            if suffix == '.rpy':
                if self.is_under_path(p, self.tl_dir):
                    continue
                self._mark_dirty(self.TASK_SCRIPT)
                continue

            # ---- 源目录素材 ----
            if suffix in self._SOURCE_EXT and self.watch_source_dirs:
                self._mark_dirty(self.TASK_SOURCE)

    def _mark_dirty(self, task: str):
        with self._dirty_lock:
            self._dirty.add(task)

    # ===================== 主线程轮询 =====================

    def poll(self) -> None:
        """
        主线程定期调用，处理所有 dirty task。
        事件通过 bus 实时发布。若需状态摘要，另开 poll_status()。
        """
        with self._dirty_lock:
            dirty = set(self._dirty)
            self._dirty.clear()

        if self.TASK_SCRIPT in dirty:
            try:
                self._handle_script_changed()
            except Exception as e:
                self._emit(AssetEventType.ENGINE_ERROR,
                           {'error': str(e), 'phase': 'script'})

        if self.TASK_TABLE in dirty:
            try:
                self._handle_table_changed()
            except Exception as e:
                self._emit(AssetEventType.ENGINE_ERROR,
                           {'error': str(e), 'phase': 'table'})

        if self.TASK_SOURCE in dirty:
            try:
                self._handle_source_changed()
            except Exception as e:
                self._emit(AssetEventType.ENGINE_ERROR,
                           {'error': str(e), 'phase': 'source'})

    # ===================== 事件发布 =====================

    def _emit(self, event_type: AssetEventType, payload: dict):
        """
        发布事件。

        所有 _emit 调用点都在主线程（poll / start / stop / _initial_load），
        bus.publish 是同步的，订阅者回调也在主线程执行，与"注册表只在
        主线程被修改"的约束一致。后台 watcher 线程只调 _mark_dirty。
        """
        event = AssetEvent(event_type=event_type, payload=payload)
        self.bus.publish(event)

    # ===================== 处理：资产表变化 =====================

    def _handle_table_changed(self):
        hooks = load_hooks(project_root=self.project_root)
        new_registry = parse_asset_tables(
            self.asset_dir, hooks=hooks, project_root=self.project_root
        )

        # 保留引用索引（脚本未变）
        if self.registry is not None:
            new_registry.references = self.registry.references
        else:
            new_registry.references = defaultdict(list)

        # 关键：先同步脚本角色定义到 new_registry，再 diff
        # 否则会把脚本里 define 出来的角色误判为 removed
        self.registry = new_registry
        self._sync_character_definitions()

        # 角色定义可能改变（新增了脚本里 define 的角色），
        # 引用索引需要重建，否则 references 会保留旧值
        self._rebuild_references()

        new_state = self._snapshot_registry_state(new_registry)
        changes = self._diff_state(self._prev_state, new_state)
        self._prev_state = new_state

        # 应用变更（重命名补丁、删除确认）
        self._apply_changes(changes)

        # 汇总事件
        self._emit(AssetEventType.TABLE_RELOADED, {
            'change_count': len(changes),
            'images': len(new_registry.images),
            'music': len(new_registry.music),
            'sounds': len(new_registry.sounds),
            'characters': len(new_registry.characters),
        })

        # 逐项事件（removed 已在 _handle_removed 中单独处理）
        # 注意：'renamed' 与 'removed' 已在 _apply_changes 里由
        # _handle_rename / _handle_removed 单独 emit（带 ref_count、
        # modified_files、orphan_references 等额外字段）。
        # 这里不再重复 emit，否则：
        #   1) 会发两条 ASSET_RENAMED
        #   2) 用户在 gate 里拒绝重命名时，仍会发一条语义错误的 ASSET_RENAMED
        kind_map = {
            'added': AssetEventType.ASSET_ADDED,
            'moved': AssetEventType.ASSET_MOVED,
            'modified': AssetEventType.ASSET_MODIFIED,
        }
        for ch in changes:
            et = kind_map.get(ch.kind)
            if et is None:
                continue
            self._emit(et, {
                'category': ch.category,
                'asset_id': ch.asset_id,
                'new_id': ch.new_id,
                'before': ch.before,
                'after': ch.after,
            })

        self._emit_unused_summary()

    # ===================== 处理：脚本变化 =====================

    def _handle_script_changed(self):
        self._load_scripts()

        undefined = self.find_undefined_characters()
        if undefined:
            self._emit(AssetEventType.ENGINE_NOTICE, {
                'info': 'undefined_characters',
                'count': len(undefined),
                'items': undefined[:20],
            })

        unused_chars = self.find_unused_characters()
        if unused_chars:
            self._emit(AssetEventType.ENGINE_NOTICE, {
                'info': 'unused_characters',
                'count': len(unused_chars),
                'items': unused_chars[:20],
            })

        broken = self.find_broken_references()
        if broken:
            self._emit(AssetEventType.BROKEN_REFERENCES_FOUND, {
                'count': len(broken),
                'items': broken[:50],
            })

        self._emit(AssetEventType.SCRIPT_REFERENCES_REBUILT, {
            'ref_count': len(self.registry.references) if self.registry else 0,
            'character_count': (len(self.parse_result.characters)
                                if self.parse_result else 0),
            'dialogue_count': (len(self.parse_result.dialogues)
                               if self.parse_result else 0),
        })

        self._emit_unused_summary()

    # ===================== 处理：源目录变化 =====================

    def _handle_source_changed(self):
        self._emit(AssetEventType.ENGINE_NOTICE, {
            'info': 'source_changed',
            'message': '源目录有文件变化，可考虑重新生成资产表',
        })

    # ===================== 变更应用 =====================

    def _apply_changes(self, changes: list):
        for ch in changes:
            if ch.kind == 'renamed':
                refs = (list(self.registry.references.get(ch.asset_id, []))
                        if self.registry else [])
                self._handle_rename(ch, refs)
            elif ch.kind == 'removed':
                refs = (list(self.registry.references.get(ch.asset_id, []))
                        if self.registry else [])
                self._handle_removed(ch, refs)

    def _handle_rename(self, ch: _Change, refs: list):
        request = self.gate.needs_confirmation('renamed', ref_count=len(refs))
        if request is not None and not self.gate.ask(request):
            # 回滚 registry：删掉新 ID，恢复旧 ID
            if self.registry is not None:
                target = getattr(self.registry, ch.category, None)
                if target is not None:
                    if ch.new_id and ch.new_id in target:
                        del target[ch.new_id]
                    if ch.before:
                        target[ch.asset_id] = ch.before

            # 回滚 _prev_state：同样恢复旧 ID、删除新 ID
            prev_cat = self._prev_state.setdefault(ch.category, {})
            if ch.new_id and ch.new_id in prev_cat:
                del prev_cat[ch.new_id]
            if ch.before:
                prev_cat[ch.asset_id] = ch.before

            self._emit(AssetEventType.ENGINE_NOTICE, {
                'info': 'rename_rejected',
                'message': f'用户拒绝重命名 {ch.asset_id} -> {ch.new_id}',
            })
            return

        if not refs or not self.script_files or not ch.new_id:
            self._emit(AssetEventType.ASSET_RENAMED, {
                'category': ch.category,
                'old_id': ch.asset_id,
                'new_id': ch.new_id,
                'ref_count': 0,
                'modified_files': [],
            })
            return

        result = _apply_rename_patch(
            old_id=ch.asset_id,
            new_id=ch.new_id,
            script_files=self.script_files,
            backup=True,
        )

        # 补丁后立即让缓存失效并重建引用，保证一致性
        if result['modified']:
            if self._analyzer is not None:
                for fp in result['modified']:
                    self._analyzer.invalidate(fp)
            self._load_scripts()

        self._emit(AssetEventType.ASSET_RENAMED, {
            'category': ch.category,
            'old_id': ch.asset_id,
            'new_id': ch.new_id,
            'ref_count': len(refs),
            'modified_files': result['modified'],
        })

    def _handle_removed(self, ch: _Change, refs: list):
        request = self.gate.needs_confirmation('removed', ref_count=len(refs))
        if request is not None and not self.gate.ask(request):
            # 回滚
            if self.registry is not None and ch.before:
                target = getattr(self.registry, ch.category, None)
                if target is not None:
                    target[ch.asset_id] = ch.before
                self._prev_state.setdefault(
                    ch.category, {})[ch.asset_id] = ch.before
            self._emit(AssetEventType.ENGINE_NOTICE, {
                'info': 'delete_rejected',
                'message': f'用户拒绝删除 {ch.asset_id}，已回滚',
            })
            return

        payload = {
            'category': ch.category,
            'asset_id': ch.asset_id,
            'ref_count': len(refs),
        }
        if refs:
            payload['orphan_references'] = refs[:20]
        self._emit(AssetEventType.ASSET_REMOVED, payload)

    # ===================== 角色定义同步 =====================

    def _sync_character_definitions(self):
        """把当前脚本解析出的角色定义合并进 self.registry。"""
        if self.parse_result is None or self.registry is None:
            return

        for var_name, char in self.parse_result.characters.items():
            if var_name in self.registry.characters:
                existing = self.registry.characters[var_name]
                for key, val in (
                    ('Name', char.display_name),
                    ('Color', char.color),
                    ('ImageTag', char.image_tag),
                ):
                    if not existing.get(key) and val:
                        existing[key] = val
                existing['_dialogue_count'] = char.dialogue_count
                existing.setdefault(
                    'Notes', f"{char.source_file}:{char.source_line}"
                )
            else:
                self.registry.characters[var_name] = {
                    'ID': var_name,
                    'Name': char.display_name,
                    'Color': char.color,
                    'ImageTag': char.image_tag,
                    'SideImage': '',
                    'DefaultTransform': '',
                    'Notes': f"{char.source_file}:{char.source_line}",
                    '_dialogue_count': char.dialogue_count,
                }

    # ===================== 未使用资产汇总 =====================

    def _emit_unused_summary(self):
        unused = self.find_unused_assets()
        if unused:
            self._emit(AssetEventType.UNUSED_ASSETS_FOUND, {
                'count': len(unused),
                'items': unused[:50],
            })

    # ===================== 公开查询接口 =====================

    def get_registry(self) -> Optional[AssetRegistry]:
        return self.registry

    def get_references(self) -> dict:
        return self.registry.references if self.registry else {}

    def get_all_asset_ids(self) -> set:
        if self.registry is None:
            return set()
        ids = set()
        for name in ('images', 'music', 'sounds', 'transforms', 'characters'):
            ids.update(getattr(self.registry, name, {}).keys())
        return ids

    def find_broken_references(self) -> list:
        if self.registry is None:
            return []
        all_ids = self.get_all_asset_ids()
        return [
            {'asset_id': aid, 'locations': list(locs)}
            for aid, locs in self.registry.references.items()
            if aid not in all_ids
        ]

    def find_unused_assets(self) -> list:
        if self.registry is None:
            return []
        referenced = set(self.registry.references.keys())
        unused = []
        for name in ('images', 'music', 'sounds', 'transforms'):
            for aid in getattr(self.registry, name, {}):
                if aid not in referenced:
                    unused.append({'category': name, 'asset_id': aid})
        return unused

    def find_undefined_characters(self) -> list:
        if self.linker is None:
            return []
        try:
            return self.linker.find_undefined_characters()
        except Exception:
            return []

    def find_unused_characters(self) -> list:
        if self.linker is None:
            return []
        try:
            return self.linker.find_unused_characters()
        except Exception:
            return []

# ===================== 模块级别名（向后兼容） =====================
# graph_view.py 顶部 `from asset_engine import AssetEngine, is_under_path`
# 依赖这个符号在模块级可见。Phase 4 拆 graph_view 时统一清理。
# backward-compat alias
is_under_path = AssetEngine.is_under_path