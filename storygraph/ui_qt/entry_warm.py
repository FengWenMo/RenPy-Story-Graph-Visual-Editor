# -*- coding: utf-8 -*-
"""
storygraph/ui_qt/entry_warm.py
==============================
「上一章是谁」的**后台**分析（跨章继承，2026-10-31）。

为什么必须后台
--------------
要回答"本章第一拍之前台上是什么"，只能按 `pipeline.continuity` 的口径
**整本分析**（真机 `sr_general`：92 个文件、解析 1.6s + 分析 0.5s ≈ 2.2s）。
摆在 GUI 线程里就是每次开章卡两秒 —— 比"没有这个功能"更糟。

所以：卡片先按**本章自己的拍**显示（与历史行为一致），同时把整本分析丢进
`QThreadPool` 跑一次；算完回到主线程，把入口状态塞进会话，卡片只重画
派生面板（当前舞台 / 尺寸格 / 小图）—— **不碰用户正在编辑的表单**。

缓存与失效
----------
分析结果进 `app.stage_entry` 的缓存（键 = 项目根 + 源文件指纹）。同一份
文件不会算第二次；文件一改，指纹变了，下次请求自然重算。**不写盘**。

⚠ 这个模块只搬"算"这件事：判据全在 `pipeline.continuity`，状态推导全在
  `story.stage_snapshot` —— 不在这里再写一遍链条/分支判断。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from storygraph.core.safeguard import swallow


class _Signals(QObject):
    """工作线程 → 主线程的唯一通道（`done` 在 GUI 线程里发回来）。"""

    done = Signal(str, str, object)     # (项目根, 指纹, 报告 | None)


class _Task(QRunnable):
    """整本分析（吃 CPU、不碰 Qt 对象）。"""

    def __init__(self, project_root: str, signals: _Signals):
        super().__init__()
        self._root = str(project_root)
        self._signals = signals
        self.setAutoDelete(True)

    def run(self):                                  # noqa: D401 - QRunnable 入口
        report = None
        signature = ''
        try:
            from storygraph.app import stage_entry as SE
            signature, report = SE.analyze(self._root)
        except Exception as e:                      # noqa: BLE001 - 分析失败要说出来
            swallow('ui_qt.entry_warm.analyze', e)
        try:
            self._signals.done.emit(self._root, signature, report)
        except RuntimeError as e:                   # 窗口已关 / 信号对象已销毁
            swallow('ui_qt.entry_warm.emit', e)


class StageEntryWarmer(QObject):
    """
    "本项目分析一次"的调度器：**同一个项目同时只跑一份**，算完发 `ready`。

    `request()` 的返回值是给界面看的（也方便测试）：
      · `'cached'`  —— 缓存里已经有这一份（不用等，主线程直接查就可）；
      · `'queued'`  —— 刚排进后台；
      · `'running'` —— 同一份正在算，别重复排；
      · `'skipped'` —— 没有项目根（单文件模式），跨章本来就不存在。
    """

    #: 分析完成（`ContinuityReport`）。**主线程**里发。
    ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._signals = _Signals(self)
        self._signals.done.connect(self._on_done)
        #: 正在算的项目根（归一化后）—— 同一份不排第二次。
        self._inflight: set = set()

    # ---------------- 调度 ----------------
    def request(self, project_root) -> str:
        root = str(project_root or '').strip()
        if not root:
            return 'skipped'
        key = _root_key(root)
        try:
            from storygraph.app import stage_entry as SE
            if SE.cached_report(root) is not None:
                return 'cached'
        except Exception as e:                      # noqa: BLE001 - 查缓存失败就重算
            swallow('ui_qt.entry_warm.cached', e)
        if key in self._inflight:
            return 'running'
        self._inflight.add(key)
        self._pool.start(_Task(root, self._signals))
        return 'queued'

    def is_running(self) -> bool:
        return bool(self._inflight)

    def shutdown(self, msec: int = 3000) -> None:
        """停掉（正在跑的会跑完，结果照常进缓存；窗口关了就丢掉）。"""
        try:
            self._pool.waitForDone(int(msec))
        except Exception as e:                      # noqa: BLE001 - 关窗不该抛
            swallow('ui_qt.entry_warm.shutdown', e)
        self._inflight.clear()

    # ---------------- 回来 ----------------
    def _on_done(self, project_root: str, signature: str, report):
        self._inflight.discard(_root_key(project_root))
        if report is None:
            return
        try:
            self.ready.emit(report)
        except RuntimeError as e:                   # 窗口已关
            swallow('ui_qt.entry_warm.ready', e)


def _root_key(project_root) -> str:
    """项目根的归一化键（与 `app.stage_entry` 同一个口径：Windows 不敏感）。"""
    try:
        return os.path.normcase(str(project_root or '')).rstrip('\\/')
    except Exception as e:                          # noqa: BLE001
        swallow('ui_qt.entry_warm.root_key', e)
        return str(project_root or '')


__all__ = ['StageEntryWarmer']
