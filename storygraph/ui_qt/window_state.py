"""
窗口**记忆**：下次打开时回到上次那个项目、把布局摆回去。
==========================================================================

用户 2026-10-13：

> 再给这个工具加个"记忆"功能，当第二次打开时自动读取之前打开过的那个项目，
> 如果项目内部有保存布局，一并读取，能做到吗？

存哪儿、存什么、三条硬规矩 —— 全在 `core/session_store.py` 的文档里
（**用户级目录，绝不写进项目仓库**）。这个 mixin 只负责"界面这一侧"：

```
启动 ─► restore_session()          读记忆 → 打开项目 → 载入上次那一章 → 摆布局
  │                                  ▲ 全部只在"命令行什么都没给"时做
  ├─ 用户点「📁 打开项目」─► _remember(...)   每次打开都更新（不用等退出）
  ├─ 切页 / 双击进卡片 ───► (退出时统一落一次，不必每次都写盘)
退出 ─► save_session_state()       写记忆 → 窗口大小 / 两处分栏 / 页 / 拍 / 章节
```

## 两个"不"（都是刻意的）

1. **不抢命令行**：`open_editor(engine, script, root)` 明确给了项目或章节时，
   记忆**一个字都不读** —— 那是用户/脚本的明确指定，不能被"上次那个"盖掉。
   （`selfcheck` 就是这条的受益者：它建窗口时什么都没给，也不会去动你的项目。）
2. **不记忆失败**：读不出、项目没了、布局是坏的 → **照常启动**，
   只在状态栏与控制台留一句人话，绝不弹错、绝不拦路。
"""

from __future__ import annotations

from pathlib import Path

from storygraph.core import session_store as SS
from storygraph.core.safeguard import swallow


class WindowStateMixin:
    """窗口记忆（见模块 docstring）。"""

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    REQUIRES = ('PAGE_CARD', 'PAGE_GRAPH', '_file_path', '_loaded_idx', '_log',
                '_page', '_project_root', '_set_page', '_show_index',
                '_split_card', '_split_graph', '_status', 'load_script',
                'menuBar', 'open_project_path')

    # ------------------------------------------------------------------
    #  路径 / 兼容开关
    # ------------------------------------------------------------------

    def session_store_path(self):
        """
        记忆文件路径（默认 `core/session_store.config_path()`）。

        ⚠ 做成方法而不是常量：测试与打包要能换一处临时文件，
        否则每条用例都会去读写你真实的 `%LOCALAPPDATA%/StoryGraph/session.json`。
        """
        return SS.config_path()

    # ------------------------------------------------------------------
    #  写：把界面状态收成"可存的东西"
    # ------------------------------------------------------------------

    def _splitter_sizes(self, name: str) -> list:
        """取一处 QSplitter 的当前尺寸（取不到 → 空，调用方就不写这一项）。"""
        try:
            sp = getattr(self, name, None)
            return [int(n) for n in sp.sizes()] if sp is not None else []
        except Exception as e:                      # noqa: BLE001 - 取不到就不记
            swallow('ui_qt.window_state.splitter_sizes', e)
            return []

    def current_layout(self) -> dict:
        """当前界面状态 → 布局字典（`session_store.layout_from` 会清洗）。"""
        geometry = []
        try:
            top = self.window()
            if top is not None:
                g = top.geometry()
                geometry = [g.width(), g.height(), g.x(), g.y()]
        except Exception as e:                      # noqa: BLE001
            swallow('ui_qt.window_state.current_layout.geometry', e)
        card_index = None
        try:
            card_index = int(getattr(self, '_loaded_idx', -1))
            if card_index < 0:
                card_index = None
        except Exception as e:                      # noqa: BLE001
            swallow('ui_qt.window_state.current_layout.index', e)
        return SS.layout_from(
            page=getattr(self, '_page', None),
            card_index=card_index,
            chapter=getattr(self, '_file_path', '') or None,
            geometry=geometry,
            split_graph=self._splitter_sizes('_split_graph'),
            split_card=self._splitter_sizes('_split_card'),
            root=getattr(self, '_project_root', None))

    def save_session_state(self) -> dict:
        """
        退出时落一次记忆（**失败只是"这次没记住"**）。

        ⚠ 写的是**用户级**文件；`SS.set_enabled(False)` 时连尝试都不做
        （测试造几百个临时项目，不该把它们灌进你的最近列表）。
        """
        if not SS.enabled():
            return {'ok': False, 'reason': '记忆已关'}
        root = getattr(self, '_project_root', None)
        if not root:
            return {'ok': False, 'reason': '没有项目根'}
        try:
            res = SS.remember_project(root, self.current_layout(),
                                      path=self.session_store_path())
            if not res.get('ok'):
                self._log(f'[记忆] ⚠ 这次没记住（{res.get("reason")}）'
                          f'—— 不影响你正在做的事')
            return res
        except Exception as e:                      # noqa: BLE001 - 退出时绝不炸
            swallow('ui_qt.window_state.save_session_state', e)
            return {'ok': False, 'reason': f'{type(e).__name__}: {e}'}

    # ------------------------------------------------------------------
    #  读：启动时回到上次那个项目
    # ------------------------------------------------------------------

    def _restore_to_screen(self, geometry):
        """把记下的窗口大小摆回去（**摆不下就不摆**，绝不把窗口放到屏幕外）。"""
        if not geometry:
            return False
        try:
            from PySide6.QtGui import QGuiApplication
            from PySide6.QtCore import QRect
            w, h = int(geometry[0]), int(geometry[1])
            if w <= 0 or h <= 0:
                return False
            x = geometry[2] if len(geometry) > 2 else None
            y = geometry[3] if len(geometry) > 3 else None
            screens = list(QGuiApplication.screens() or [])
            chosen = None
            if x is not None and y is not None:
                rect = QRect(int(x), int(y), w, h)
                for scr in screens:
                    if scr.availableGeometry().intersects(rect):
                        chosen = rect
                        break
            if chosen is None:
                # 位置没记 / 记的位置已经不在任何屏幕上（换显示器、拔了外接屏）
                # → **只把大小摆回去**，位置交给窗口管理器。
                self.resize(w, h)
                return True
            top = self.window()
            (top or self).setGeometry(chosen)
            return True
        except Exception as e:                      # noqa: BLE001 - 摆不回去就用默认
            swallow('ui_qt.window_state.restore_to_screen', e)
            return False

    def _restore_splitters(self, layout: dict):
        """两处分栏尺寸摆回去（值不合法就保持默认 —— 不猜）。"""
        for name, key in (('_split_graph', 'split_graph'),
                          ('_split_card', 'split_card')):
            sizes = SS.normalize_sizes((layout or {}).get(key))
            if not sizes:
                continue
            try:
                sp = getattr(self, name, None)
                if sp is not None:
                    sp.setSizes(sizes)
            except Exception as e:                  # noqa: BLE001
                swallow('ui_qt.window_state.restore_splitters', e)

    def reopen_project(self, root, remember: bool = True) -> dict:
        """
        打开一个项目 **并把它上次的布局一起摆回去**。

        与「📁 打开项目」的区别只有一条：这里会去读记忆（布局 + 上次那一章）。
        `remember=False` 用于"恢复"本身 —— 恢复不该把它自己又记一遍
        （记也是记同样的东西，没必要多写一次盘）。
        """
        if not root:
            return {'ok': False, 'reason': '没给项目根'}
        layout = SS.project_layout(root, path=self.session_store_path())
        res = {}
        try:
            res = self.open_project_path(root) or {}
        except Exception as e:                      # noqa: BLE001 - 打开失败要说清
            swallow('ui_qt.window_state.reopen_project', e)
            self._log(f'[记忆] ⚠ 打不开上次那个项目：{type(e).__name__}: {e}')
            return {'ok': False, 'reason': str(e)}
        try:
            self._restore_splitters(layout)
            self._restore_to_screen(layout.get('geometry'))
            chapter = SS.resolve_chapter(layout, root)
            if chapter:
                # ⚠ `goto_card=False` 与构造时的自动载入一致：**第一屏必须是
                #   剧情结构**（用户明确要求过），章节内容已经在会话里。
                self.load_script(chapter, goto_card=False)
            page = layout.get('page')
            if page is not None:
                self._set_page(self.PAGE_CARD if int(page) == self.PAGE_CARD
                               else self.PAGE_GRAPH)
            idx = layout.get('card_index')
            if idx is not None and chapter:
                try:
                    self._show_index(int(idx))
                except Exception as e:              # noqa: BLE001 - 摆不回就停在第一拍
                    swallow('ui_qt.window_state.reopen_project.index', e)
        except Exception as e:                      # noqa: BLE001 - 摆布局绝不拦启动
            swallow('ui_qt.window_state.reopen_project.layout', e)
        if remember:
            SS.remember_project(root, path=self.session_store_path())
        # 用户要的就是**控制台一行**（"控制台打一行，已自动读取项目：……"）——
        # 不做弹窗、不做提示条、不问"要不要恢复"。
        chapter_name = (Path(SS.resolve_chapter(layout, root)).name
                        if SS.resolve_chapter(layout, root) else '')
        self._log(f'已自动读取项目：{root}'
                  + (f'（上次那一章：{chapter_name}）' if chapter_name else ''))
        self._status(f'已自动读取项目：{Path(root).name}'
                     + (f' · {chapter_name}' if chapter_name else ''))
        res = dict(res or {})
        res['restored'] = True
        res['layout'] = layout
        return res

    def restore_session(self, interactive: bool = False) -> dict:
        """
        启动时读记忆：回到上次那个项目。

        用户 2026-10-13（**明确否掉了第一版的 UI**）：

        > 记忆部分！我让你自动打开！而不是加个 UI……没有必要……直接记住，
        > 有上次的记录就读取，控制台打一行，已自动读取项目：……
        > 若是没有，就空白打开即可……不需要 UI！更不需要我手动点击说打开上次
        > 项目！那跟我手动打开项目有什么区别？？？

        所以这里**没有菜单、没有勾选框、没有"打开上次的项目"**：
        启动读到记录就默默打开并在控制台打一行，没有就空白打开。
        想要"这次别读"只有环境变量 `STORYGRAPH_NO_SESSION=1`（给自动化用，
        不是给人用的开关）。

        `interactive` 只是给"程序内部需要显式重开一次"留的口子（不再有 UI 入口），
        它多做的事只有一件：失败时把原因说出来（主动要求的事不该静默）。
        """
        if not SS.enabled():
            return {'ok': False, 'reason': '记忆已关', 'root': ''}
        path = self.session_store_path()
        try:
            data = SS.load(path)
        except Exception as e:                      # noqa: BLE001
            swallow('ui_qt.window_state.restore_session.load', e)
            return {'ok': False, 'reason': f'{type(e).__name__}: {e}', 'root': ''}
        root0 = str(data.get('last_project') or '')
        # ⚠ `auto_open=False` 只可能由老版本的勾选框写下（UI 已删）——
        #   读到它仍然尊重（不悄悄推翻用户当初的选择），但**不提示**。
        if not data.get('auto_open', True) and not interactive:
            return {'ok': False, 'reason': '记忆里关掉了自动打开（auto_open=false）',
                    'root': root0}
        root = SS.last_project(path) if not interactive else root0
        if not root:
            # 没有记录 / 项目已经不在磁盘上 → **空白打开，不打扰**
            # （用户："若是没有，就空白打开即可"）
            if interactive:
                reason = (f'上次那个项目已经不在磁盘上了：{root0}' if root0
                          else '记忆里还没有项目')
                self._log(f'[记忆] {reason}')
                self._status(reason)
            return {'ok': False, 'reason': '没有可恢复的项目', 'root': root0}
        try:
            self.reopen_project(root, remember=not interactive)
        except Exception as e:                      # noqa: BLE001 - 记忆绝不拦启动
            swallow('ui_qt.window_state.restore_session.reopen', e)
            self._log(f'[记忆] ⚠ 恢复失败（不影响你手动打开）：'
                      f'{type(e).__name__}: {e}')
            return {'ok': False, 'reason': str(e), 'root': root}
        return {'ok': True, 'reason': '', 'root': root}


__all__ = ['WindowStateMixin']
