"""
storygraph.ui_qt.window_assets.py
=================================
`MainWindowQt` 的**资产选择：图片 / 下拉候选（资源表经会话取，不自己读表）。**

为什么拆（P3-14，2026-10-05）
-----------------------------
`MainWindowQt` 原本是 3452 行 / 120 方法的上帝对象 —— `EditorSession`
（已拆成 5 块）的另一半。它比那边更难拆，因为：

  · 它是 **UI 层**：行为测试薄得多，而且要真起 Qt（`test_ui_qt.py`）；
  · 方法之间靠 **Qt 信号**耦合，不像会话那样能按"主语是什么"干净切开。

所以顺序是**先补网、再拆**：`tests/test_main_window_snapshot.py`
（63 步操作序列 golden）先把外部可观察行为钉住，
拆分期间它必须一直绿。

**做成 mixin 而不是独立对象**：调用方（菜单、工具栏、信号、
`open_editor`）一行都不用改 —— 这是**搬家**不是改契约。

与主类的隐式依赖
----------------
见每个类的 `REQUIRES`：那是**实测**（静态扫 `self.*`）出来的，
由 `tests/test_main_window_mixins.py` 守着。
"""
from __future__ import annotations

from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QSplitter, QStackedWidget, QToolBar,
                               QVBoxLayout, QWidget)

from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.graph_canvas import GraphCanvas
from storygraph.ui_qt.sprite_grid import SpriteGridWidget
from storygraph.ui_qt.status import Notifier, Toast
from storygraph.ui_qt.thumb_loader import ThumbLoader


__all__ = ['WindowAssetsMixin']


class WindowAssetsMixin:
    """
    资产选择：图片 / 下拉候选（资源表经会话取，不自己读表）。

    它要求宿主（`MainWindowQt`）提供 `REQUIRES` 里那些成员 ——
    多半是**主类刻意留下**的：界面骨架、页切换、状态栏、
    静默失败上报、图的所有权。
    """

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    REQUIRES = ('ASSET_FIELD_KINDS', '_ensure_registry', '_project_root', '_registry_provider', '_session')

    def _pick_image(self, current: str) -> str:
        """挑一张立绘图（走 Qt 版资产选择器）。取消或没有资源表返回 ''。"""
        from storygraph.ui_qt.asset_picker import pick_image
        if not self._ensure_registry():
            return ''
        return pick_image(self, self._registry_provider(), current=current,
                          project_root=self._project_root) or ''
    def _refresh_asset_choices(self):
        """
        把资源表里的候选灌进卡片的三格下拉（背景 / 音乐 / 语音）。

        没有资源表时清空候选（而不是留一堆过期值）。
        """
        panel = getattr(self, '_panel', None)
        if panel is None:
            return
        for field, kind in self.ASSET_FIELD_KINDS.items():
            try:
                choices = self._session.asset_choices(kind)
            except Exception:                  # noqa: BLE001
                choices = []
            panel.set_asset_choices(field, choices)
    def _pick_asset_for(self, field: str, current: str) -> str:
        """
        卡片「选择…」的回调：从**已登记的资产**里挑一个填进这一格。

        背景走 `pick_image`（要的是 image spec，不是资产 ID）；
        音乐 / 语音走 `pick_asset`（要的就是资产 ID —— 渲染器会把它翻成
        真实文件路径，查不到会写 `# TODO: 缺少资源`，所以写 ID 是安全的）。
        """
        from storygraph.ui_qt.asset_picker import pick_asset, pick_image
        if not self._ensure_registry():
            return ''
        kind = self.ASSET_FIELD_KINDS.get(field, 'image')
        if kind == 'image':
            return pick_image(self, self._registry_provider(), current=current,
                              project_root=self._project_root) or ''
        return pick_asset(self, self._registry_provider(), asset_type=kind,
                          current=current, project_root=self._project_root) or ''
