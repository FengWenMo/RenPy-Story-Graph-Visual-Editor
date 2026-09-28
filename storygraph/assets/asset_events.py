"""
asset_events.py
===============
原生文件监听 + 事件总线 + 确认闸门。

不依赖 watchdog，使用 threading + 定期扫描（默认 1.5s）。
通过"连续两次扫描结果一致"实现稳定性检查，
避免 Excel 保存中途或编辑器临时文件触发事件。

组件：
  - AssetEventType      事件类型枚举
  - AssetEvent          事件数据类
  - AssetEventBus       线程安全的事件总线（观察者模式）
  - AssetFileWatcher    原生文件监听器（后台线程 + 轮询 + 稳定性检查）
  - ConfirmationGate    变更确认判定（是否需要弹窗）
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Iterable, Optional


# ===================== 事件类型 =====================

class AssetEventType(Enum):
    # 资产级事件
    ASSET_ADDED = auto()
    ASSET_REMOVED = auto()
    ASSET_RENAMED = auto()
    ASSET_MOVED = auto()
    ASSET_MODIFIED = auto()

    # 表级事件
    TABLE_RELOADED = auto()

    # 脚本级事件
    SCRIPT_REFERENCES_REBUILT = auto()

    # 依赖分析
    BROKEN_REFERENCES_FOUND = auto()
    UNUSED_ASSETS_FOUND = auto()

    # 引擎生命周期
    ENGINE_READY = auto()
    ENGINE_ERROR = auto()
    ENGINE_STOPPED = auto()
    ENGINE_NOTICE = auto()

    # 需要用户确认
    CONFIRMATION_REQUIRED = auto()


@dataclass
class AssetEvent:
    event_type: AssetEventType
    payload: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def __repr__(self):
        return (f"<AssetEvent {self.event_type.name} @ {self.timestamp:.2f} "
                f"keys={list(self.payload.keys())}>")


# ===================== 事件总线 =====================

class AssetEventBus:
    """
    线程安全的观察者模式事件总线。
    
    支持订阅特定类型或订阅所有事件（event_type=None 表示通配）。
    订阅者回调可能在发布事件的线程中执行，因此：
      - 若订阅者涉及 UI，请确保发布总是在 UI 主线程
      - AssetEngine 已保证在主线程的 poll() 中发布
    """

    def __init__(self):
        self._subscribers: dict = {}  # event_type -> [callback]
        self._lock = threading.RLock()

    def subscribe(self, event_type: Optional[AssetEventType], callback: Callable):
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(callback)

    def unsubscribe(self, event_type: Optional[AssetEventType], callback: Callable):
        with self._lock:
            lst = self._subscribers.get(event_type, [])
            if callback in lst:
                lst.remove(callback)

    def publish(self, event: AssetEvent):
        # 复制订阅者列表，避免回调中修改订阅关系导致死锁
        with self._lock:
            specific = list(self._subscribers.get(event.event_type, []))
            wildcard = list(self._subscribers.get(None, []))
        for cb in specific + wildcard:
            try:
                cb(event)
            except Exception as e:
                print(f"[AssetEventBus] 订阅者回调异常: {e}")


# ===================== 原生文件监听器 =====================

class AssetFileWatcher:
    """
    原生文件监听器：后台线程定期扫描目录。
    
    特性：
      - 稳定性检查：连续两次扫描结果一致才认为文件稳定（避免 Excel 保存中途触发）
      - 最长等待保护：超过 MAX_PENDING_TICKS * interval 仍未稳定，强制提交最新结果
      - 首次扫描仅建立基线，不触发事件
      - 支持多个根目录
      - 使用 os.walk 原地剪枝，跳过隐藏目录与常见缓存目录
      - 通过回调返回 [(Path, 'added'|'removed'|'modified')]
    
    参数：
      paths:           要监听的目录列表
      on_change:       回调，签名 on_change(changes: list[tuple[Path, str]])
      interval:        扫描间隔（秒），默认 1.5
      extensions:      只关心这些扩展名（None 表示全部）
      ignore_prefixes: 忽略文件名前缀（如 Office 的 ~$ 临时文件）
      ignore_suffixes: 忽略文件名后缀
    """

    def __init__(
        self,
        paths: Iterable[Path],
        on_change: Callable[[list], None],
        interval: float = 1.5,
        extensions: Optional[set] = None,
        ignore_prefixes: tuple = ('~$', '.~', '#'),
        ignore_suffixes: tuple = ('.tmp', '.swp', '.bak', '~'),
    ):
        self.paths = [Path(p) for p in paths]
        self.on_change = on_change
        self.interval = interval
        self.extensions = {e.lower() for e in extensions} if extensions else None
        self.ignore_prefixes = ignore_prefixes
        self.ignore_suffixes = ignore_suffixes

        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

        # 状态：{path_str: (mtime, size)}
        self._snapshot: Optional[dict] = None   # 已确认稳定的状态
        self._pending: Optional[dict] = None    # 上一轮扫描结果，用于稳定性对比
        self._pending_since: Optional[float] = None  # pending 起始时间（monotonic）

        # 稳定性检查的最长等待：超过 MAX_PENDING_TICKS * interval 仍未稳定，
        # 强制以最新扫描结果提交（避免用户持续保存导致 on_change 永不触发）
        self.MAX_PENDING_TICKS = 3

    # ---------- 生命周期 ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._run, name='AssetFileWatcher', daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0):
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---------- 主循环 ----------
    def _run(self):
        # 首次扫描：建立基线，不触发事件
        try:
            initial = self._scan_all()
        except Exception as e:
            print(f"[AssetFileWatcher] 初始扫描失败: {e}")
            initial = {}
        self._snapshot = initial
        self._pending = dict(initial)
        self._pending_since = None

        while not self._stop_flag.is_set():
            if self._stop_flag.wait(self.interval):
                break
            try:
                self._tick()
            except Exception as e:
                print(f"[AssetFileWatcher] 扫描异常: {e}")

    def _tick(self):
        current = self._scan_all()

        # 稳定性检查：本轮结果与上一轮完全一致，说明变化已稳定
        if self._pending is not None and current == self._pending:
            if current != self._snapshot:
                changes = self._diff(self._snapshot or {}, current)
                if changes:
                    try:
                        self.on_change(changes)
                    except Exception as e:
                        print(f"[AssetFileWatcher] on_change 回调异常: {e}")
                self._snapshot = current
            self._pending_since = None
        else:
            # 本轮与上一轮不同（或首轮）：只记录时间，继续等待稳定
            if self._pending_since is None:
                self._pending_since = time.monotonic()
            elif (time.monotonic() - self._pending_since
                  > self.MAX_PENDING_TICKS * self.interval):
                # 超过最大等待，强制以最新扫描结果为准
                changes = self._diff(self._snapshot or {}, current)
                if changes:
                    try:
                        self.on_change(changes)
                    except Exception as e:
                        print(f"[AssetFileWatcher] on_change 回调异常: {e}")
                self._snapshot = current
                self._pending_since = None

        self._pending = current

    # ---------- 差异计算 ----------
    @staticmethod
    def _diff(old: dict, new: dict) -> list:
        changes = []
        for p in new.keys() - old.keys():
            changes.append((Path(p), 'added'))
        for p in old.keys() - new.keys():
            changes.append((Path(p), 'removed'))
        for p in old.keys() & new.keys():
            if old[p] != new[p]:
                changes.append((Path(p), 'modified'))
        return changes

    # ---------- 扫描 ----------
    def _scan_all(self) -> dict:
        result = {}
        for root in self.paths:
            if not root.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(str(root)):
                # 原地剪枝：跳过隐藏目录与常见缓存目录
                dirnames[:] = [
                    d for d in dirnames
                    if not d.startswith('.') and d not in (
                        '__pycache__', 'node_modules', '.git', '.svn', '.hg',
                    )
                ]
                for fn in filenames:
                    if self._should_ignore(fn):
                        continue
                    fp = Path(dirpath) / fn
                    if (self.extensions is not None and
                            fp.suffix.lower() not in self.extensions):
                        continue
                    try:
                        st = fp.stat()
                    except OSError:
                        continue
                    result[str(fp)] = (st.st_mtime, st.st_size)
        return result

    def _should_ignore(self, filename: str) -> bool:
        for pref in self.ignore_prefixes:
            if filename.startswith(pref):
                return True
        lower = filename.lower()
        for suf in self.ignore_suffixes:
            if lower.endswith(suf):
                return True
        return False


# ===================== 确认闸门 =====================

@dataclass
class ConfirmationRequest:
    """描述一次需要用户确认的变更"""
    reason: str                # 'delete' / 'rename_many_refs' / 'script_patch'
    summary: str               # 人类可读的简述
    details: dict = field(default_factory=dict)


class ConfirmationGate:
    """
    判定哪些变更需要用户确认。

    默认策略：
      - 删除**有引用**的资产   → 需要确认
      - 删除无引用的资产       → 静默（没有脚本会因此坏掉）
      - 重命名影响引用 > N     → 需要确认（N 可配置，默认 5）
      - 脚本补丁前             → 可选确认（默认关闭，因为已自动备份）
      - 其余变更               → 静默

    一次重载可能产出成百上千条变更，逐条弹窗会把 UI 卡死。
    因此所有确认都走 ask_batch：把待确认项汇总成一条请求，
    用户只需回答一次。

    通过 ask_callback 与 UI 交互：
      ask_callback(request: ConfirmationRequest) -> bool
      返回 True 表示用户同意执行。
    """

    def __init__(
        self,
        ask_callback: Optional[Callable[[ConfirmationRequest], bool]] = None,
        rename_threshold: int = 5,
        confirm_script_patch: bool = False,
        max_preview_items: int = 8,
    ):
        self.ask_callback = ask_callback
        self.rename_threshold = rename_threshold
        self.confirm_script_patch = confirm_script_patch
        self.max_preview_items = max_preview_items

    def needs_confirmation(self, change_type: str,
                           ref_count: int = 0) -> Optional[ConfirmationRequest]:
        """
        返回 None 表示不需要确认；否则返回 ConfirmationRequest，
        由调用方决定何时调用 ask()。
        """
        if change_type == 'removed':
            # 没有任何脚本引用的资产，删掉不会破坏任何东西，不必打扰用户
            if ref_count <= 0:
                return None
            return ConfirmationRequest(
                reason='delete',
                summary=f'删除被 {ref_count} 处脚本引用的资产',
                details={'ref_count': ref_count},
            )
        if change_type == 'renamed' and ref_count > self.rename_threshold:
            return ConfirmationRequest(
                reason='rename_many_refs',
                summary=f'重命名影响 {ref_count} 处引用（阈值 {self.rename_threshold}）',
                details={'ref_count': ref_count},
            )
        if change_type == 'script_patch' and self.confirm_script_patch:
            return ConfirmationRequest(
                reason='script_patch',
                summary='即将修改脚本文件',
                details={},
            )
        return None

    def build_batch_request(self, items: list) -> Optional[ConfirmationRequest]:
        """
        把 [(change, request), ...] 汇总成一条 ConfirmationRequest。

        items 为空时返回 None（没有需要打扰用户的事）。
        """
        if not items:
            return None

        total = len(items)
        refs = sum(
            (req.details or {}).get('ref_count', 0) for _c, req in items
        )
        preview = []
        for change, _req in items[:self.max_preview_items]:
            preview.append(f"{change.category}/{change.asset_id}")
        if total > len(preview):
            preview.append(f'…等共 {total} 项')

        return ConfirmationRequest(
            reason='batch',
            summary=f'{total} 项资产变更需要确认（涉及 {refs} 处脚本引用）',
            details={
                'total': total,
                'ref_count': refs,
                'preview': preview,
            },
        )

    def ask(self, request: ConfirmationRequest) -> bool:
        """调用 UI 弹窗。若未提供 ask_callback，则默认同意。"""
        if self.ask_callback is None:
            return True
        try:
            return bool(self.ask_callback(request))
        except Exception as e:
            print(f"[ConfirmationGate] ask_callback 异常: {e}")
            return False