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


def fit_to_available(width, height, available=None, margin: int = 48) -> tuple:
    """
    把想要的窗口尺寸**压进屏幕可用区**（四周留 `margin`）。返回 `(宽, 高)`。

    为什么必须有它（2026-10-23，用户："整个界面太宽了……全屏都没看全"）：
    `QMainWindow.sizeHint()` 会把工具栏的完整宽度算进去 —— 这一版实测
    **2720 × 705**（剧情结构页 25 个动作）。窗口按 sizeHint 打开就是
    2720 宽，在 1600~1900 的屏幕上右侧直接跑到屏幕外，用户看到的是
    "按钮被裁掉一半"，而不是"窗口比屏幕大"。

    记下来的布局同理：`_restore_to_screen` 以前只判"矩形与屏幕**相交**"，
    2720×1500 的旧尺寸当然相交 —— 于是每次启动都还原成一个放不下的窗口。
    """
    try:
        w, h = int(width), int(height)
    except (TypeError, ValueError):
        return 1200, 800
    if available is None:
        try:
            from PySide6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
            available = screen.availableGeometry() if screen else None
        except Exception as e:                       # noqa: BLE001 - 没屏幕就按原样
            swallow('ui_qt.window_state.available_geometry', e)
            available = None
    if available is None:
        return w, h
    max_w = max(640, int(available.width()) - int(margin))
    max_h = max(480, int(available.height()) - int(margin))
    return min(w, max_w), min(h, max_h)


#: 布局记忆的**格式版本**（不是 `session_store.VERSION`，那个管整份文件的结构）。
#: 升到 2 = "默认窗口改窄 + 卡片栏默认窄"那一轮（2026-10-30，用户第 5 条）。
#: 升到 3 = "对半的卡片栏也算旧摆法"（2026-10-30 第 7 条：用户看着截图说
#:          "还是一模一样……都说了要变窄一些"——他记忆里是 583:584，50% 正好
#:          卡在旧的 60% 门槛下面，于是**一次都没被迁移过**）。
LAYOUT_VERSION = 3
#: 布局里记版本号的键。
LAYOUT_VERSION_KEY = 'layout_version'
#: 迁移时卡片页右栏的**上限占比**：超过就是"卡片太宽"的旧摆法。
#: 真机老记忆 813/1215 = 67%；对半 583/1167 = 50%；默认 420/1060 = 39.6%。
#: 门槛取 **45%**：对半会被压，用户自己拖到 40% 左右的窄栏不会被乱动。
LEGACY_CARD_SHARE = 0.45


def narrow_layout(layout: dict) -> dict:
    """
    布局记忆 → 窄默认（**每次恢复都跑窗口那条，卡片栏那条只迁一次**）。

    三条规则，力度不同：

      · **窗口大小：每次恢复都压**到 `DEFAULT_WINDOW_SIZE`（只压大的，小的照旧）。
        为什么不再看版本号：真机第一次迁移确实变成了 1200×800，但用户随后把
        窗口拖大/最大化过一次，退出时就把 `1392×804` 存回去了（这时
        `layout_version` 已经是 2）—— 下一次启动"又这么大"，用户第 6 条反馈
        就是这句话。所以：**记忆只用来把窗口记小，不用来记大**。会话里拖多大、
        要不要最大化都随你，只是下次打开仍回到默认窄窗。

      · **卡片栏：只压一次**（`layout_version < 3` 的旧摆法，右栏 ≥ 45%）。
        用户自己拖过的**窄**比例（≤45%）之后一直尊重不动。

        为什么门槛从 60% 降到 45%（用户第 7 条）：他记忆里是 `[583, 584]`
        对半 —— 50% 卡在 60% 门槛下面，上一轮的迁移**一次都没动过它**，
        所以"打开还是那么宽"。对半就不是"窄栏"，这个判断得改。

    位置不在这里动：屏幕夹紧仍由 `_restore_to_screen` 做。
    """
    if not isinstance(layout, dict):
        return {}
    try:
        version = int(layout.get(LAYOUT_VERSION_KEY) or 1)
    except (TypeError, ValueError):
        version = 1
    out = dict(layout)
    out[LAYOUT_VERSION_KEY] = LAYOUT_VERSION
    try:
        # 顶层导入会成环（`main_window` 顶层导入本模块的 mixin）—— 常量的
        # 叶子在 `ui_qt.window_layout`（2026-11-01 解环），这里直接拿。
        from storygraph.ui_qt.window_layout import (CARD_SPLIT_DEFAULT,
                                                    DEFAULT_WINDOW_SIZE)
    except Exception as e:                       # noqa: BLE001 - 拿不到常量就只打版本
        swallow('ui_qt.window_state.narrow_layout.constants', e)
        return out
    g = SS.normalize_geometry(out.get('geometry'))
    if g and g[0] and g[1]:
        w = min(int(g[0]), int(DEFAULT_WINDOW_SIZE[0]))
        h = min(int(g[1]), int(DEFAULT_WINDOW_SIZE[1]))
        if (w, h) != (int(g[0]), int(g[1])):
            out['geometry'] = [w, h, g[2], g[3]]
    if version < LAYOUT_VERSION:
        sc = SS.normalize_sizes(out.get('split_card'))
        if len(sc) == 2 and sum(sc) > 0 and sc[1] >= sum(sc) * LEGACY_CARD_SHARE:
            out['split_card'] = list(CARD_SPLIT_DEFAULT)
    return out


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
        layout = SS.layout_from(
            page=getattr(self, '_page', None),
            card_index=card_index,
            chapter=getattr(self, '_file_path', '') or None,
            geometry=geometry,
            split_graph=self._splitter_sizes('_split_graph'),
            split_card=self._splitter_sizes('_split_card'),
            dock_state=self._dock_state_blob(),
            root=getattr(self, '_project_root', None))
        # 打上格式版本：`narrow_layout` 靠它判断"卡片栏要不要迁回默认窄"
        # （只迁一次）。⚠ 窗口大小**不看**这个版本号 —— 每次恢复都会压到
        # `DEFAULT_WINDOW_SIZE` 以内（用户："打开已经打开过的项目还是这么大"）。
        layout[LAYOUT_VERSION_KEY] = LAYOUT_VERSION
        return layout

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
        """
        把记下的窗口大小摆回去（**摆不下就不摆**，绝不把窗口放到屏幕外）。

        2026-10-23 补两条（用户："全屏都没看全整个界面"）：
          · 尺寸要**压进**那块屏幕（`fit_to_available`）—— 旧记录里
            2720×1500 这种"大于屏幕"的尺寸，光判 `intersects` 是拦不住的；
          · 位置也要**夹回**屏幕内 —— 否则标题栏可能落在屏幕上方。
        """
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
            screen = None
            if x is not None and y is not None:
                rect = QRect(int(x), int(y), w, h)
                for scr in screens:
                    if scr.availableGeometry().intersects(rect):
                        screen = scr
                        chosen = rect
                        break
            if chosen is None:
                # 位置没记 / 记的位置已经不在任何屏幕上（换显示器、拔了外接屏）
                # → **只把大小摆回去**（并压进主屏），位置交给窗口管理器。
                avail = (screen or (screens[0] if screens else None))
                w, h = fit_to_available(
                    w, h, avail.availableGeometry() if avail else None)
                self.resize(w, h)
                return True
            avail = screen.availableGeometry()
            w, h = fit_to_available(w, h, avail)
            nx = max(avail.left(), min(int(x), avail.right() - w + 1))
            ny = max(avail.top(), min(int(y), avail.bottom() - h + 1))
            top = self.window()
            (top or self).setGeometry(QRect(nx, ny, w, h))
            return True
        except Exception as e:                      # noqa: BLE001 - 摆不回去就用默认
            swallow('ui_qt.window_state.restore_to_screen', e)
            return False

    @staticmethod
    def _scale_sizes(sizes, available):
        """
        记忆里的**绝对像素** → 当前分栏宽度的**等比像素**。

        为什么非换算不可（2026-10-30 实测）：窗口被压到 1200 之后分栏宽 ~975，
        而记忆里是 `[583, 584]`（和 = 1167 > 975）。`setSizes` 不会等比缩，
        它从右边硬砍 —— 实测摆出来是 `[545, 357]`，卡片页只剩三分之一。
        用户看到的会是"我明明摆的是对半，怎么卡片这么窄"。
        """
        try:
            total = sum(int(n) for n in sizes)
            if total <= 0 or available <= 0:
                return []
            avail = int(available)
            out = [max(1, int(round(int(n) * avail / total))) for n in sizes]
            # 四舍五入的零头补给最后一栏，保证总和 = available
            out[-1] += avail - sum(out)
            return out
        except (TypeError, ValueError):
            return []

    def _restore_splitters(self, layout: dict):
        """两处分栏比例摆回去（**按比例**，值不合法就保持默认 —— 不猜）。"""
        for name, key in (('_split_graph', 'split_graph'),
                          ('_split_card', 'split_card')):
            sizes = SS.normalize_sizes((layout or {}).get(key))
            if not sizes:
                continue
            try:
                sp = getattr(self, name, None)
                if sp is None:
                    continue
                available = sum(int(n) for n in sp.sizes()) or int(sp.width())
                scaled = self._scale_sizes(sizes, available)
                sp.setSizes(scaled or sizes)
            except Exception as e:                  # noqa: BLE001
                swallow('ui_qt.window_state.restore_splitters', e)

    # ------------------------------------------------------------------
    #  停靠面板（左侧「章节导航」）
    # ------------------------------------------------------------------

    def _dock_state_blob(self) -> str:
        """
        `QMainWindow.saveState()` → base64 文本（存不进 JSON 的是 QByteArray）。

        记的是**所有** dock 的位置 / 宽度 / 是否浮出 / 是否关掉。分成
        "某个 dock 宽度"单独记字段的话，关掉再打开、拖到右边、浮出这几件事
        都得各写一份，而 Qt 已经把它们编码好了 —— 抄一份现成的。
        """
        try:
            raw = self.saveState()
            if not raw:
                return ''
            return bytes(raw.toBase64()).decode('ascii')
        except Exception as e:                      # noqa: BLE001 - 记不下就不记
            swallow('ui_qt.window_state.dock_state_blob', e)
            return ''

    def _restore_docks(self, layout: dict):
        """把 dock 的状态摆回去（旧记忆里没有这一项就保持默认，不猜）。"""
        blob = str((layout or {}).get('dock_state') or '')
        if not blob:
            return False
        try:
            from PySide6.QtCore import QByteArray
            data = QByteArray.fromBase64(blob.encode('ascii'))
            ok = bool(self.restoreState(data))
        except Exception as e:                      # noqa: BLE001 - 摆不回去就用默认
            swallow('ui_qt.window_state.restore_docks', e)
            return False
        # 工具栏上那个勾选框要跟真实可见性对齐 —— 不同步的话会出现
        # "勾着但面板已经关了"，再点一次反而把它打开（看起来像失灵）。
        try:
            dock = getattr(self, '_nav_dock', None)
            act = getattr(self, '_nav_action', None)
            if dock is not None and act is not None:
                # 同 `window_nav._on_nav_dock_visible`：用 `isHidden()` ——
                # 恢复布局时窗口通常还没 show，`isVisible()` 一律是 False。
                act.blockSignals(True)
                act.setChecked(not dock.isHidden())
                act.blockSignals(False)
        except Exception as e:                      # noqa: BLE001
            swallow('ui_qt.window_state.sync_nav_action', e)
        return ok

    def reopen_project(self, root, remember: bool = True) -> dict:
        """
        打开一个项目 **并把它上次的布局一起摆回去**。

        与「📁 打开项目」的区别只有一条：这里会去读记忆（布局 + 上次那一章）。
        `remember=False` 用于"恢复"本身 —— 恢复不该把它自己又记一遍
        （记也是记同样的东西，没必要多写一次盘）。
        """
        if not root:
            return {'ok': False, 'reason': '没给项目根'}
        layout = narrow_layout(SS.project_layout(root,
                                                 path=self.session_store_path()))
        res = {}
        try:
            res = self.open_project_path(root) or {}
        except Exception as e:                      # noqa: BLE001 - 打开失败要说清
            swallow('ui_qt.window_state.reopen_project', e)
            self._log(f'[记忆] ⚠ 打不开上次那个项目：{type(e).__name__}: {e}')
            return {'ok': False, 'reason': str(e)}
        try:
            # 顺序有讲究（2026-10-30）：**先定窗口大小，再摆 dock**。
            # 反过来摆的话，分栏是在"还没 resize 的旧宽度"上摆的，像素尺寸
            # 会被 Qt 硬砍，比例失真（实测 583:584 摆成 545:357）。
            self._restore_to_screen(layout.get('geometry'))
            self._restore_docks(layout)
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
            # 分栏**最后摆**，而且下一回合再补一次：分栏宽度要等这一页真的
            # 显示、布局跑完才准。在页面还藏着的时候 `setSizes`，量到的是
            # "最小宽兜底"（实测 172:340，用户摆的对半完全失真）。
            self._restore_splitters(layout)
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._restore_splitters(layout))
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
