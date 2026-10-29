"""
storygraph.ui_qt.window_assets.py
=================================
`MainWindowQt` 的**资产选择：图片 / 下拉候选（资源表经会话取，不自己读表）。**

**为什么是 mixin、当初怎么拆的**：`docs/代码地图.md` §三·五 讲一次，
不再在 6 个 `window_*.py` 里各抄一遍（从前这 22 行在 6 个文件里逐字重复）。

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
    REQUIRES = ('ASSET_FIELD_KINDS', '_ensure_registry', '_file_path', '_log',
                '_panel', '_project_root', '_registry_provider', '_session',
                'load_script')

    def set_assets_ready(self, ready: bool):
        """
        「现在判得了'这张立绘在不在'了吗」—— 项目打开 / 资源表读完之后说一声。

        用户 2026-10-13：

        > 如果在卡牌编辑器内打开时，若找不到对应的文件，我希望你能给出提示
        > （不必是弹窗，直接在立绘那里显示没有）

        ⚠ **为什么要有这个开关**（而不是"找不到就说没有"）：项目**还没打开**时
        路径解析必然失败（`game/images/...` 那条兜底路要有项目根才走得通）——
        那时把每张立绘都写成"没有"是**假警报**，比不提示更糟。

        ⚠ **通用性**：这里只传"判得了吗"这一个事实，**不传任何具体项目的东西**
        （没有角色名、没有组名、没有一个字是某个项目的特例）。判定规则全在
        `SpriteCardWidget._load_thumb` 里那一条通用的"路径解析不出来"。
        """
        panel = getattr(self, '_panel', None)
        grid = getattr(panel, '_grid', None) if panel is not None else None
        if grid is None:
            return
        try:
            grid.set_assets_ready(bool(ready))
        except Exception as e:                      # noqa: BLE001 - 不该拦任何操作
            swallow('ui_qt.window_assets.set_assets_ready', e)

    def open_asset_rename_dialog(self):
        """
        「资源改名」：把新名字**传播到脚本里的所有引用处**（E2，2026-10-19）。

        用户原话：

        > 真正缺的是"资源表改名之后，Beats 里的旧值怎么办"……
        > 让改名能一键传播到所有引用处，并且能回滚。

        入口只做两件事：确保资源表在（候选要读它）、把对话框打开；
        真正的预览 / 应用 / 回滚全在 `dialog_asset_rename` + 会话方法里。
        """
        if not self._ensure_registry():
            return
        from storygraph.ui_qt.dialog_asset_rename import AssetRenameDialog
        dlg = AssetRenameDialog(self, session=self._session,
                                registry=self._registry_provider(),
                                on_applied=self._after_asset_rename)
        dlg.exec()

    def _after_asset_rename(self, res: dict):
        """
        改名**写进磁盘之后**：这一章被改过，就必须从磁盘重读。

        为什么非做不可（2026-10-23）：改名把 `ling_happy` 换成 `ling_smile`
        改的是**文件**，而卡片页还捧着改名前的拍。用户接着按「⤓ 导出 .rpy」，
        会话会把自己那份（还带着旧名字）写回去 —— **改名被自己的编辑器撤销了**，
        而且导出还会报"成功"。

        只在"这一章确实被改过"时重读（别的章节被改名与手上这一章无关，
        重读会白丢用户没导出的改动 —— `AssetRenameDialog.apply` 已经先拦住
        了"这一章有未导出改动"那种情况）。
        """
        modified = [str(p) for p in ((res or {}).get('modified') or ())]
        current = str(getattr(self, '_file_path', '') or '')
        if not modified or not current:
            return
        try:
            hit = any(Path(p).resolve() == Path(current).resolve()
                      for p in modified)
        except OSError as e:                    # noqa: BLE001 - 路径解析不了就不重读
            swallow('ui_qt.window_assets.after_rename.path', e)
            hit = False
        if not hit:
            return
        self._log('[资源改名] 这一章被改过 → 从磁盘重读'
                  '（不重读的话，下次导出会把旧名字写回去）')
        self.load_script(current, goto_card=False)

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

        音频两格**额外加动作伪值**（`（停止音乐）` / `（停止音效）`）：
        与 Excel 候选表同一套词，但**不含「（排队播放）」**（用户拍板
        2026-10-28：排队必须带曲名，单放伪值会导成 TODO）。
        """
        from storygraph.app.beats import audio_choice_items
        panel = getattr(self, '_panel', None)
        if panel is None:
            return
        for field, kind in self.ASSET_FIELD_KINDS.items():
            try:
                choices = self._session.asset_choices(kind)
                choices = audio_choice_items(kind, choices)
            except Exception as e:                  # noqa: BLE001
                swallow('ui_qt.window_assets._refresh_asset_choices', e)
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
