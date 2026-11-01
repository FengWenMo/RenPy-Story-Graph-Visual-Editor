"""
storygraph.ui_qt.main_window
============================
Qt 版**主窗口**：剧情结构图（第一屏）+ 卡片编辑器（点进模块才进），**两个都要**。

用户 2026-09-30 明确：
> 我要改的是：tk做的一个块状编辑器 + 这个卡片界面！不是让你只剩一个卡片界面

于是第一版把它们**并排**摆开（画布中央 + 卡片右 dock）。2026-10-02 用户
指出这个摆法是错的：

> 本来它应该先是一个结构化模块化的图排版将所有的东西连在一起，
> 然后再是卡片编辑器的…… 我的剧情树编辑器在哪儿？
> 本来应该先是剧情树编辑器才对的，点进单个模块后才跳转到卡牌编辑器

并排的后果有两个，截图里都看得到：卡片常驻右栏 440px（第一眼就是卡片
编辑器）；不带项目参数启动时 `graph=None`，中央是**一片空白**，整屏只剩
"拍列表 + 卡片"。所以改成**页级导航**：

  ┌ 工具栏（两组，随页切换）──────────────────────────────────────────┐
  │ 第 0 页 剧情结构：剧情树 │ 结构图（画布） │ 结构分析（静态，只读）  │
  │ 第 1 页 卡片编辑：← 返回 │ 拍列表 │ 卡片（💾保存 / ↩撤销）        │
  └ 状态栏 ──────────────────────────────────────────────────────────┘

   · 启动**永远停在结构页**（带了章节参数也是）；
   · 双击模块 / 剧情树双击 / 右键「在卡片界面里打开」= 进卡片页；
   · 卡片页「← 返回剧情结构」(Esc) 回第一屏，并高亮刚编辑的模块；
   · 键盘快捷键按页分流：结构页回车=进卡片、Delete=删块；
     卡片页回车=下一拍、Ctrl+S=保存这一拍、Ctrl+Z=撤销这一拍。

**两条路必须分开**（用户 2026-10-03 纠正）
------------------------------------------
> 我没有叫你所有的文件全部导进剧情图内！……所谓的导入项目只是为了表格的
> xlsx 的资源管理系统！不是把整个项目都排在剧情图上面！！！
> 剧情图上只有用户自己导入的章节排版！！！跟条件设置！

| 动作 | 管什么 | **不管**什么 |
|---|---|---|
| 📁 打开项目 / 🔄 重读资源表 | 资源表 xlsx：立绘 / 音乐 / 语音 / 变换 / 角色候选、缩略图路径 | 剧情图（一个节点都不加） |
| ➕ 导入章节 .rpy | 剧情图：只放**用户导入过**的文件里的 label 与它们之间的 jump/call/条件 | 资源表 |

上一版把两者写成一句话（"打开项目 → 建出结构图"），40 个脚本一次性灌进
图里变成 51 个块 —— 用户："这还选个毛线啊！看都要看瞎了！"
会话层的 `import_chapters` / `load_assets` 从签名上就断开这种可能
（`load_assets` 根本拿不到图）。

布局的"骨架"在 `storygraph.ui_qt.graph_canvas`（节点尺寸与层间距是
**一份**数据，见那里的 `_layout_gaps`）。

本文件只做**摆控件、读表单、写表单**三件事
------------------------------------------
全部编辑语义都在 `storygraph.app.editor.EditorSession` 里（深模块）：
载入 / 字段投影 / 立绘卡 / 写回 / 导出（含增量补丁与安全门禁）/ 体检 /
舞台帧 / 结构分析。这里**不再**出现任何 `beat.xxx = ` 赋值、也不再有
`self._panel._speaker` 这种"伸手进别人私有属性"的写法 —— 那些正是两套
前端各写一套语义的根源。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow
from functools import partial
from pathlib import Path

import re

#: Ren'Py 台词 → 预览用 HTML 的转换器搬到 `ui_qt.text_html`（2026-11-01 解导入环）：
#: `stage_preview` 也要用**同一份**（卡片与预览不能各写一套），而
#: `stage_thumb` 顶层导入 `stage_preview`、本模块顶层导入 `stage_thumb` ——
#: 转换器放在这里就是 `main_window → stage_thumb → stage_preview → main_window`
#: 那个三模块环。它只依赖 `re`，搬到叶子后环断了；这里 re-export 老名字。
from storygraph.ui_qt.text_html import renpy_text_to_html  # noqa: F401 - 老调用点 re-export


from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QTextCursor
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDockWidget,
                               QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QSplitter,
                               QStackedWidget, QTabBar, QToolBar, QToolButton,
                               QVBoxLayout, QWidget)

from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS, EditorSession
from storygraph.ui_qt.code_edit import CodeEditor
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.elide import ElidedLabel
from storygraph.ui_qt.flow import FlowRow
from storygraph.ui_qt.graph_canvas import GraphCanvas
from storygraph.ui_qt.inspector import PropertyStrip
from storygraph.ui_qt.labeled_choices import (as_choices, combo_value,
                                              fill_combo, legacy_pairs,
                                              select_value)
from storygraph.ui_qt.navigator import ChapterNavigator
from storygraph.ui_qt.shot_list import ShotDelegate
from storygraph.ui_qt.size_panel import SizePanel
from storygraph.ui_qt.sprite_grid import SpriteGridWidget
from storygraph.ui_qt.stage_thumb import BeatStageThumb
from storygraph.ui_qt.status import AppStatusBar, Notifier, Toast
from storygraph.ui_qt.thumb_loader import ThumbLoader
from storygraph.ui_qt.wheel_guard import WheelSafeComboBox, ensure_installed
from storygraph.ui_qt.wrap_label import NarrowWrapLabel
# `MainWindowQt` 的十一块职责各住一个文件（P3-14，2026-10-05）。
# **基类顺序**：mixin 必须排在 `QMainWindow` **前面** ——
# `eventFilter` / `closeEvent` 是 Qt 虚函数（`QObject` 自己就定义了），
# 排在后面就会被它抢先、mixin 那份成死代码（实测 13 条键盘用例全红，
# 而 `hasattr(MainWindowQt, 'eventFilter')` 照样是 True）。
# 由 `tests/test_main_window_mixins.py` 守。
from storygraph.ui_qt.window_assets import WindowAssetsMixin
from storygraph.ui_qt.window_card import CardPageMixin
from storygraph.ui_qt.window_export import (WindowExportMixin,
                                             overwritable as _overwritable)
from storygraph.ui_qt.window_graph_page import GraphPageMixin
from storygraph.ui_qt.window_graph_ops import GraphOpsMixin
from storygraph.ui_qt.window_keyboard import WindowKeyboardMixin
from storygraph.ui_qt.window_nav import WindowNavMixin
from storygraph.ui_qt.window_state import WindowStateMixin, fit_to_available
from storygraph.ui_qt.window_recover import RecoverMixin
from storygraph.ui_qt.window_engine import WindowEngineMixin
from storygraph.ui_qt.window_nodes import NodeEditMixin


# ======================================================================
#  卡片页宽度预算（2026-10-30，用户反馈第 5 条「UI 宽窄可变」）
# ======================================================================
#
# 六个数字与那段"为什么是这些数"的推理搬到 `ui_qt.window_layout`
# （2026-11-01 解导入环）：`window_state` 也要用它们，而它被本模块顶层
# 导入 —— 常量放在这里就是 `main_window ↔ window_state` 的模块级环。
# 数字本身零依赖，搬到叶子后两边都指向它；这里 re-export，
# `MW.DEFAULT_WINDOW_SIZE` 之类的老写法（含 `tests/test_ui_width.py`）不变。
from storygraph.ui_qt.window_layout import (  # noqa: F401 - 老调用点 re-export
    CARD_COLUMN_MIN_W, CARD_SPLIT_DEFAULT, CARD_SPLIT_EXPANDED_RATIO,
    CARD_STAGE_THUMB, DEFAULT_WINDOW_H, DEFAULT_WINDOW_SIZE, DEFAULT_WINDOW_W)


def toggle_card_column(win) -> bool:
    """
    展开 / 收起卡片页右栏（用户要求"UI 宽窄可变"的一键入口）。

    返回**切换后**的状态：`True` = 现在是大图（右栏占 72%）。

    ⚠ 做成模块级函数、而不是 `MainWindowQt` 的方法：主类成员受
    `tests/test_main_window_mixins.py` 的"每个成员都要认领"约束
    （`KEEP` 白名单在测试里，本轮的写范围不含那个文件）。
    这里只是操作窗口状态，不需要成为主类 API。
    """
    split = getattr(win, '_split_card', None)
    if split is None:
        return False
    expanded = bool(getattr(win, '_card_col_expanded', False))
    if expanded:
        sizes = list(getattr(win, '_card_col_saved', None)
                     or CARD_SPLIT_DEFAULT)
        win._card_col_expanded = False
    else:
        win._card_col_saved = [int(n) for n in split.sizes()]
        total = max(1, sum(int(n) for n in split.sizes()))
        right = int(total * CARD_SPLIT_EXPANDED_RATIO)
        sizes = [max(1, total - right), right]
        win._card_col_expanded = True
    try:
        split.setSizes(sizes)
    except Exception as e:                       # noqa: BLE001 - 摆不动就保持原样
        swallow('ui_qt.main_window.toggle_card_column', e)
        win._card_col_expanded = expanded
        return expanded
    return not expanded


class CardColumnExpandFilter(QObject):
    """双击分栏把手 → 展开 / 收起卡片页右栏（`toggle_card_column`）。"""

    def __init__(self, window):
        super().__init__(window)
        self._window = window

    def eventFilter(self, obj, event):            # noqa: N802 - Qt 接口
        if event.type() == QEvent.MouseButtonDblClick:
            toggle_card_column(self._window)
            return True
        return False


class CardActionRow(FlowRow):
    """
    卡片页右上那一排**增 / 删卡片**动作：宽了一行放下，窄了自动折行。

    为什么是"折行"、而不是 2026-10-30 第一版那个「⋯ 结构」下拉菜单：
    用户看完截图的第 6 条反馈是**"不要那个结构！恢复回原来的 UI"** ——
    6 颗按钮被藏进两层菜单换来可缩窄的宽度，他不接受。折行两样都留着：

      · 默认窗口（1200 宽）及以上 → **与从前一模一样**：一行、右对齐、顺序不变
        （`.pip-tmp/plan/probe_band_rows.py` 实测：1200/1240/1280/1360/1440 都是 1 行）；
      · 再窄（≈1150 以下）→ 后面的按钮折到第二行，**一颗都不藏、也不裁在窗口外**；
      · `minimumSizeHint` 仍是 `FlowRow` 的"最宽那一颗"（≈113px），
        所以"窗口能缩到 700 出头"这个好处**没有丢**。

    `sizeHint` 报"一行全排开"的宽：父布局（`QHBoxLayout`）在宽窗口下就按
    这个宽把它摆成一行；空间不够时它按自己的最小宽收窄，`FlowLayout` 自己
    折行。高度靠 `hasHeightForWidth` 一路传上去（`bar` → 页面根布局）。
    """

    def sizeHint(self) -> QSize:                    # noqa: N802 - Qt 接口
        w = h = 0
        n = self.flow.count()
        for i in range(n):
            m = self.flow.itemAt(i).sizeHint()
            w += m.width()
            h = max(h, m.height())
        if n > 1:
            w += self.flow.gap() * (n - 1)
        return QSize(w, h)


# 「🗂 资源映射」菜单 + 状态栏那一行搬到 `ui_qt.mapping_status`
# （2026-11-01 解导入环）：`BeatCardEditor` 要调 `refresh_mapping_status`，
# 而它自己也搬去了 `ui_qt.card_editor` —— 名字留在本模块就是
# `main_window → card_editor → main_window` 的模块级环。
# 这里 re-export，`MW.refresh_mapping_status` 之类的老写法（含测试）不变。
from storygraph.ui_qt.mapping_status import (  # noqa: F401 - 老调用点 re-export
    MAPPING_CACHE_ATTR, MAPPING_MENU_TEXTS, _choose_mapping_export_path,
    _choose_mapping_file, _exec_asset_picker, _exec_mapping_dialog,
    _ignore_checked, _install_mapping_ui, mapping_export_draft,
    mapping_load_file, mapping_reload, mapping_save_to_project,
    mapping_show_table, open_mapping_dialog, refresh_mapping_status)


# ==================================================================
#  立绘网格 ↔ 资产选择器 / 资源映射（④⑥ 第二阶段接线）
# ==================================================================

def pick_sprite_result(win, current: str = ''):
    """
    立绘网格的「＋ 添加立绘 / 双击换图」→ 资产选择器（**用途预选「立绘」**）。

    为什么要专门的入口（而不是复用 `window_assets._pick_image`）：那条路只还
    一个 spec 字符串，卡片就无从知道"用户选的预览图被换成了哪张真立绘"，
    提示条也就摆不出来（用户 2026-10-30：要看得见判定）。这里多返回
    `PickResult`（含 `replaced_from/replaced_to/message`），取消返回 None。
    """
    from storygraph.ui_qt.asset_picker import AssetPickerDialog
    if not win._ensure_registry():
        return None
    dlg = AssetPickerDialog(
        win, registry=win._registry_provider(),
        project_root=getattr(win, '_project_root', None), current=current,
        types=['image'], want_image_spec=True,
        title='选择立绘（预览图 / 头像会自动换成真立绘）',
        purpose='sprite')
    try:
        if _exec_asset_picker(dlg) != QDialog.Accepted:
            return None
        res = getattr(dlg, 'pick_result', None)
    finally:
        try:
            dlg.close()
        except Exception as e:                    # noqa: BLE001 - 关不掉也返回
            swallow('ui_qt.main_window.pick_sprite.close', e)
    return res if res is not None else str(dlg.image_spec or '')


def open_mapping_from_card(win, spec=''):
    """
    卡片提示条上「去『资源映射』修」→ 打开资源映射，并**落到那张图那一行**。

    `spec` 是那条警告里的 image spec（`ling happy`）：开窗直接翻到
    「逐条编辑 → 单张图片」并选中它（表里没有就先按推断补一条）。
    从前这里只 `open_mapping_dialog(win)` —— 人还得自己在 3889 行里找。
    """
    return open_mapping_dialog(win, focus_image=str(spec or ''))


def notify_card_notice(win, message: str):
    """
    立绘网格的"没写成"通知（选择器拒绝 / 给不出可写 spec）→ 控制台 + 状态栏。

    网格里没有状态栏可用，但**绝不能**因此把拒绝变成静默：用户会以为加上了。
    """
    text = str(message or '').strip()
    if not text:
        return
    try:
        win._log('[立绘] ' + text)
        win._status(text)
    except Exception as e:                        # noqa: BLE001 - 提示失败也不拦
        swallow('ui_qt.main_window.card_notice', e)


# ==================================================================
#  「🎬 演出预览」的写回 at 接线（P2，2026-10-30）
# ==================================================================
#
# ⚠ 真正的调用点在 `window_export.open_stage_preview`（S1 的文件，本轮不加线）。
# 所以这里给**工具栏那条**换成带 beats / on_apply_at 的版本；各处测试仍可调
# `win.open_stage_preview()` 走老路（它们只关心"从当前那一拍开始"）。

def _confirm_at_plan(parent, plan, extra: str = '',
                     title: str = '写回 at？') -> bool:
    """
    「写回 at」的确认框（**唯一出口**：判据把它换掉，不弹真窗）。

    `extra` 是**同一张框里**的第二件事（2026-10-31 起：生成 `sg_*` 时要顺带
    新建/追加 `game/sg_transforms.rpy`）。刻意不弹第二个框 —— 一次点击却弹
    两次确认，人会条件反射点"是"，那等于没有确认。

    `title` 只影响窗口标题：换景走的是同一个出口，但它是"会清台"的那件事，
    标题得说清（`换景（会清台）？`），不能还写着"写回 at"。
    """
    body = plan.diff_text()
    extra = str(extra or '').strip()
    if extra:
        body = f'{body}\n\n—— 另外要动项目文件 ——\n{extra}'
    return QMessageBox.question(
        parent, title, body,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes


def _plan_preview_at(win, req):
    """演出预览发来的写回请求 → `AtEdit` 计划（只算，不动 Beat）。"""
    from storygraph.app.stage import MODE_CHAPTER_FIRST, is_unknown_source
    from storygraph.app.stage import plan_at_edit
    from storygraph.story.atl import DEFAULT_SCREEN, project_screen
    root = getattr(win, '_project_root', None)
    try:
        screen = project_screen(root) if root else DEFAULT_SCREEN
    except Exception as e:                        # noqa: BLE001 - 读不到就用默认分辨率
        swallow('ui_qt.main_window.preview_at.screen', e)
        screen = DEFAULT_SCREEN
    try:
        idx = int((req or {}).get('beat_index', -1))
    except (TypeError, ValueError):
        idx = -1
    mode = str((req or {}).get('mode') or 'source')
    src = (req or {}).get('source_index')
    # 跨章继承（出处不在这本剧本里，`source_index < 0`）且这一拍**看得见**背景/
    # 图层：没有"那一行"可改 → 与尺寸面板同一条路（本章第一拍补一条 show）。
    # 同一个请求两种出口两种待遇，正是本轮在治的病。
    if mode == 'source' and is_unknown_source(src) \
            and str((req or {}).get('scene_image') or '').strip():
        mode = MODE_CHAPTER_FIRST
    return plan_at_edit(list(getattr(win._session, 'beats', None) or ()), idx,
                        target=str((req or {}).get('target') or 'scene'),
                        tag=str((req or {}).get('tag') or ''),
                        transform=str((req or {}).get('transform') or ''),
                        project_root=root, screen=screen,
                        source_index=src, mode=mode,
                        scene_image=str((req or {}).get('scene_image') or ''))


def _after_preview_beat_edit(win, plan, *, kind: str = '已写回 at',
                             tag: str = '演出预览') -> None:
    """
    改完一拍之后的**既有门禁**：会话脏标记 + 状态栏「● 未导出」+ 一行台账。

    `kind` / `tag` 只影响那行台账与状态栏措辞：换景（`scene`）走的不再是
    "写回 at"，日志里不能还这么写（真实项目第一次跑就露出来了：
    `[演出预览] 已写回 at：第 134 拍 · 换景（scene，会清台） ·  ·`）——
    空 transform 还会多出一对孤零零的 `·`。

    ⚠ 刻意**不重载卡片**：卡片里可能有还没保存的手改，重载会把它们冲掉。
    `session.apply` 对"用户没手改 Stage"的拍会保留结构化列，所以不重载不会
    把这次写回弄丢（Stage 被手改过的拍以卡片为准 —— 那是用户自己的选择）。
    """
    try:
        win._session.mark_edits_dirty()
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.main_window.preview_at.dirty', e)
    try:
        win._sync_export_dirty()
    except Exception as e:                        # noqa: BLE001 - 状态标签失败不拦写回
        swallow('ui_qt.main_window.preview_at.dirty_ui', e)
    try:
        span = ''
        try:
            if getattr(plan, 'affects', ()):
                first, last = int(plan.affects[0]), int(plan.affects[1])
                span = (f' · 影响第 {first + 1}–{last + 1} 拍'
                        f'（共 {last - first + 1} 拍）')
        except (TypeError, ValueError):
            span = ''
        what = str(getattr(plan, 'transform', '') or '').strip()
        win._log(f'[{tag}] {kind}：第 {int(plan.beat_index) + 1} 拍 · '
                 f'{plan.target_label}' + (f' · {what}' if what else '')
                 + f'{span}（只改内存里的拍；导出时才写 .rpy，可撤销）')
        win._status(f'{kind}（只改内存里的拍；导出仍走既有 diff 确认门禁）')
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.main_window.preview_at.log', e)


def apply_at_from_preview(win, req):
    """
    预览窗口「写回 at…」的外部钩子（`on_apply_at(request) -> AtEdit | None`）。

    约定（见 `stage_preview._on_apply_at_clicked`）：返回的 `AtEdit` 已经
    **落进内存 Beat**，窗口负责把它记进自己的撤销栈并重画帧；返回 None =
    没写（用户取消 / 计划被拒）。

    流程与 S3 窗口内的默认路径一致：算计划 → 给 diff 确认 → 写内存 →
    会话脏标记（导出时的既有确认门禁因此照常生效）。
    """
    plan = _plan_preview_at(win, req)
    if plan is None or not getattr(plan, 'ok', False):
        reason = str(getattr(plan, 'reason', '') or '').strip() or '没有可写的改动。'
        notify_card_notice(win, '写回 at 没做：' + reason)
        return None
    if not _confirm_at_plan(win, plan):
        try:
            win._status('已取消：没有写回 at。')
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.main_window.preview_at.cancel', e)
        return None
    try:
        if not plan.apply():
            notify_card_notice(win, '写回 at 失败：计划没落进内存里的拍。')
            return None
    except Exception as e:                        # noqa: BLE001 - 写失败要说出来
        swallow('ui_qt.main_window.preview_at.apply', e)
        notify_card_notice(win, f'写回 at 失败：{e}')
        return None
    _after_preview_beat_edit(win, plan)
    return plan


def _screen_pair(win):
    """项目的游戏分辨率（`gui.init`）；读不到用 1920×1080。"""
    from storygraph.story.atl import DEFAULT_SCREEN, project_screen

    root = getattr(win, '_project_root', None)
    try:
        return project_screen(root) if root else DEFAULT_SCREEN
    except Exception as e:                        # noqa: BLE001 - 读不到就用默认分辨率
        swallow('ui_qt.main_window.size.screen', e)
        return DEFAULT_SCREEN


def _plan_size_from_card(win, req, index=None):
    """
    尺寸面板（卡片上的「尺寸」一格）的一次「改」→ `AtEdit` 计划（只算）。

    请求 dict 来自 `ui_qt.size_panel.SizePanel.request`：
    `{row, key, at, kind, tag, spec, source, generate, natural}` —— 里面的 `at`
    是 `story.size_presets` **按几何**在项目里匹配到的 transform（或 `''` =
    原样）。**`generate=True` 时 `at` 是个还不存在的 `sg_*` 名字**：窗口层先把
    它加进 `index`（`transform_def` 从生成的文本再解析一次），计划就能像
    "文件已经写好"那样算几何；真写盘在确认之后（见 `apply_size_from_card`）。

    这里只把它翻成"改哪一拍、写哪个字段"，不重新匹配（面板给的就是它看见的）。
    """
    from storygraph.app.stage import plan_size_edit

    root = getattr(win, '_project_root', None)
    screen = _screen_pair(win)
    try:
        idx = int(getattr(win, '_loaded_idx', -1))
    except (TypeError, ValueError):
        idx = -1
    req = req or {}
    row = {'kind': str(req.get('kind') or 'scene'),
           'tag': str(req.get('tag') or ''),
           'spec': str(req.get('spec') or ''),
           'source': req.get('source', -1)}
    return plan_size_edit(list(getattr(win._session, 'beats', None) or ()),
                          idx, row, key=str(req.get('key') or ''),
                          at=str(req.get('at') or ''),
                          project_root=root, transform_index=index,
                          screen=screen)


def _refresh_card_after_size(win, plan) -> None:
    """
    改完尺寸把卡片对齐（**只有这一条路会重载表单**）。

    为什么允许重载（`_after_preview_beat_edit` 刻意不重载）：这次改动是从
    **卡片自己**发起的，动笔之前已经把表单里的手改提交进这一拍了
    （`apply_current_beat`）。所以重载丢掉的东西 = 无。

    改的是**别的拍**（背景是第 41 拍写的、你在第 100 拍改）时只刷只读派生面板
    （当前舞台 / 尺寸行 / 小图），不重载表单 —— 表单本来就不该变。
    """
    try:
        loaded = int(getattr(win, '_loaded_idx', -1))
    except (TypeError, ValueError):
        loaded = -1
    try:
        if int(getattr(plan, 'beat_index', -2)) == loaded:
            win._show_index(loaded)
        else:
            win.refresh_derived_panels(loaded)
    except Exception as e:                        # noqa: BLE001 - 刷不动不拦写回
        swallow('ui_qt.main_window.size.refresh_card', e)


def _plan_sg_from_card(win, req, index):
    """
    `generate=True` 的那一条 → 写 `game/sg_transforms.rpy` 的计划 + 新 transform 表。

    返回 `(plan, index)`：

      · `plan.writes` → 确认之后要**先写文件**，`index` 里已经带上这条新定义的
        几何（`AtEdit` 计划因此能算得像"文件已写好"）；
      · `plan` 是 `reuse` / `noop` → 项目里已经有等价的东西（护栏 ②），
        **不写文件**，只是把名字换成它；
      · `plan` 是 `refuse` / `None` → 调用方说清原因、什么都不动。

    三条护栏都在 `story.sg_transforms` 里，这里只负责"读一次表、合一次表"。
    """
    from storygraph.story import sg_transforms as SG

    key = str((req or {}).get('key') or '')
    # `zoom` 只有「自定义倍数」有（`key='custom'`）：名字与体都从这个数来
    # （`sg_zoom_075` / `zoom 0.75`），所以一路带着它 —— 只在 `plan_for_project`
    # 里丢掉的话，写进文件的会是 `zoom 1`（2026-10-31）。
    zoom = (req or {}).get('zoom')
    root = getattr(win, '_project_root', None)
    plan = SG.plan_for_project(root, key, zoom, index=index,
                               natural=(req or {}).get('natural'),
                               screen=_screen_pair(win))
    if not plan.ok or not plan.writes:
        return plan, index
    defn = SG.transform_def(plan.name, key, plan.zoom)
    if defn is None:
        return SG.SgPlan(action=SG.REFUSE, key=key, zoom=zoom,
                         reason=f'生成的 {plan.name} 解析不出几何，拒绝写'), index
    merged = dict(index or {})
    merged[plan.name] = defn
    return plan, merged


def apply_size_from_card(win, req):
    """
    「尺寸」一格的「改」：算计划 → diff 确认 → 写内存 Beat → 刷卡片。

    与 `apply_at_from_preview` **同一条门禁**（同一个 `_confirm_at_plan`、
    同一个 `_after_preview_beat_edit`）：只改内存里的拍，导出时才落盘；
    会话脏标记照常点亮，所以既有 diff 确认不会绕过去。

    **唯一的例外**（2026-10-31，用户拍板的前提）：`generate=True` 时要先往
    `game/sg_transforms.rpy` 里写一条 `sg_*` 定义 —— 那是**项目文件**，不是
    内存。所以：

      ① 先把两件事一起摊在**同一张**确认框里（脚本 diff + 文件 diff）；
      ② 确认后**先写文件**（护栏过了才写），再 `.apply()` 内存里的拍；
      ③ 写完刷 session 的 transform 缓存 —— 否则卡片还显示"项目里没有"。

    返回落进内存的 `AtEdit`；取消 / 被拒 / 失败返回 None。
    """
    if not isinstance(req, dict):
        return None
    # 先把卡片上还没保存的手改落进这一拍 —— 否则它会在写回后被表单盖掉
    try:
        win.apply_current_beat()
    except Exception as e:                        # noqa: BLE001 - 提交失败也照样能改
        swallow('ui_qt.main_window.size.apply_current', e)
    index = None
    sg_plan = None
    if req.get('generate'):
        try:
            index = dict(win._session.transform_index() or {})
        except Exception as e:                    # noqa: BLE001 - 取不到表就别生成
            swallow('ui_qt.main_window.size.index', e)
            index = {}
        try:
            sg_plan, index = _plan_sg_from_card(win, req, index)
        except Exception as e:                    # noqa: BLE001 - 计划失败要说出来
            swallow('ui_qt.main_window.size.sg_plan', e)
            notify_card_notice(win, f'生成 transform 没做：{e}')
            return None
        if sg_plan is None or not sg_plan.ok:
            reason = str(getattr(sg_plan, 'reason', '') or '').strip() \
                or '生成不了这个 transform。'
            notify_card_notice(win, '生成 transform 没做：' + reason)
            return None
        # 查重命中（项目里已经有等价的东西）时，名字可能不是面板上那个
        req = dict(req, at=str(sg_plan.name or ''), generate=False)
    try:
        plan = _plan_size_from_card(win, req, index=index)
    except Exception as e:                        # noqa: BLE001 - 计划失败要说出来
        swallow('ui_qt.main_window.size.plan', e)
        notify_card_notice(win, f'改尺寸没做：{e}')
        return None
    if plan is None or not getattr(plan, 'ok', False):
        reason = str(getattr(plan, 'reason', '') or '').strip() or '没有可写的改动。'
        notify_card_notice(win, '改尺寸没做：' + reason)
        return None
    extra = sg_plan.diff_text() if (sg_plan is not None
                                   and getattr(sg_plan, 'writes', False)) else ''
    if not _confirm_at_plan(win, plan, extra=extra):
        try:
            win._status('已取消：没有改尺寸。')
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.main_window.size.cancel', e)
        return None
    if sg_plan is not None and getattr(sg_plan, 'writes', False):
        try:
            sg_plan.apply()
        except Exception as e:                    # noqa: BLE001 - 写文件失败要说出来
            swallow('ui_qt.main_window.size.sg_apply', e)
            notify_card_notice(win, f'写 {sg_plan.rel} 失败：{e}')
            return None
        try:
            win._session.refresh_transform_index()
        except Exception as e:                    # noqa: BLE001 - 刷不到只影响显示
            swallow('ui_qt.main_window.size.sg_refresh', e)
        notify_card_notice(win, f'已生成 {sg_plan.name} → {sg_plan.rel}'
                                '（工具生成、带指纹；手改过就不再覆盖）')
    try:
        if not plan.apply():
            notify_card_notice(win, '改尺寸失败：计划没落进内存里的拍。')
            return None
    except Exception as e:                        # noqa: BLE001 - 写失败要说出来
        swallow('ui_qt.main_window.size.apply', e)
        notify_card_notice(win, f'改尺寸失败：{e}')
        return None
    _after_preview_beat_edit(win, plan)
    _refresh_card_after_size(win, plan)
    return plan


def _ask_scene_image(win, req) -> str:
    """
    「换景」的**唯一入口**：让用户挑新背景（判据把它换掉，不开真窗）。

    走的是卡片「背景」那一格**同一个**资产选择器（`_pick_asset_for`）：
    背景的可选范围必须与那一格完全一致，否则"从这里能选、从那里选不到"
    就成了两个真相。取消 / 打不开 → 空串（调用方什么都不做）。
    """
    current = str((req or {}).get('spec') or '')
    try:
        return str(win._pick_asset_for('Scene', current) or '')
    except Exception as e:                        # noqa: BLE001 - 打不开要说出来
        swallow('ui_qt.main_window.scene.pick', e)
        notify_card_notice(win, f'打不开背景选择器：{e}')
        return ''


def _plan_scene_from_card(win, req, index=None):
    """「换景」请求 → `SceneEdit` 计划（只算，不动 Beat）。"""
    from storygraph.app.stage import plan_scene_change

    root = getattr(win, '_project_root', None)
    screen = _screen_pair(win)
    try:
        idx = int(getattr(win, '_loaded_idx', -1))
    except (TypeError, ValueError):
        idx = -1
    return plan_scene_change(
        list(getattr(win._session, 'beats', None) or ()), idx,
        str((req or {}).get('image') or ''),
        at=str((req or {}).get('at') or ''),
        project_root=root, transform_index=index, screen=screen)


def apply_scene_from_card(win, req):
    """
    背景行的「换景」：挑图 → 计划 → **一张**确认框（写清会清掉几个立绘）
    → 写内存 Beat → 刷卡片。

    为什么这条路要单独存在（用户 2026-10-31 拍板）：Ren'Py 的 `scene`
    移除 master 层上**所有** displayable 再显示新背景 —— 立绘会一起没。
    改背景"大小"用不着它（那写 `show`，见 `apply_size_from_card`）；
    但"这一拍换成另一张背景"只有它，所以入口必须**显式**，代价必须
    **写在确认框里**（`SceneEdit.diff_text` 那句"会清掉台上 N 个立绘"）。

    与尺寸那条路共用同一套门禁：只改内存里的拍（导出时才落盘）、同一个
    脏标记、同一张确认框出口；`generate=True`（新背景要套一个项目里还没有
    的 `sg_*`）时也先写 `game/sg_transforms.rpy`（三条护栏都在
    `story.sg_transforms` 里）。

    返回落进内存的 `SceneEdit`；取消 / 被拒 / 失败返回 None。
    """
    if not isinstance(req, dict):
        return None
    try:
        loaded = int(getattr(win, '_loaded_idx', -1))
    except (TypeError, ValueError):
        loaded = -1
    if loaded < 0:
        # 没有"当前拍"就没有换景的落点：别先弹一个背景选择器再告诉人没载入
        try:
            win._status('先载入一章，再换景。')
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.main_window.scene.no_chapter', e)
        return None
    # 卡片上还没保存的手改先落进这一拍 —— 换景会重建这一拍的脚本
    try:
        win.apply_current_beat()
    except Exception as e:                        # noqa: BLE001 - 提交失败也照样能换
        swallow('ui_qt.main_window.scene.apply_current', e)
    image = _ask_scene_image(win, req)
    if not image:
        try:
            win._status('已取消：没有换景。')
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.main_window.scene.cancel', e)
        return None
    index = None
    sg_plan = None
    if req.get('generate'):
        try:
            index = dict(win._session.transform_index() or {})
        except Exception as e:                    # noqa: BLE001 - 取不到表就别生成
            swallow('ui_qt.main_window.scene.index', e)
            index = {}
        try:
            # ⚠ 换景要**换图**：请求里那个 `natural` 是**旧背景**的尺寸（面板是
            # 按"现在这张图"算的）。拿它去查重会把"只对旧图等价"的 transform
            # 当成等价（例如 `xysize (1920,1080)` 恰好等于 4K 图的「铺满」），
            # 可新图还没量过。所以这里**不传尺寸**：只按逐字相同的 ATL 体
            # （尺度无关）与倍数类预设的"不用看图尺寸"那条查重（2026-10-31）。
            sg_plan, index = _plan_sg_from_card(
                win, dict(req, natural=None), index)
        except Exception as e:                    # noqa: BLE001 - 计划失败要说出来
            swallow('ui_qt.main_window.scene.sg_plan', e)
            notify_card_notice(win, f'生成 transform 没做：{e}')
            return None
        if sg_plan is None or not sg_plan.ok:
            reason = str(getattr(sg_plan, 'reason', '') or '').strip() \
                or '生成不了这个 transform。'
            notify_card_notice(win, '换景没做：' + reason)
            return None
        # 查重命中（项目里已有等价的东西）时名字可能不是面板上那个
        req = dict(req, at=str(sg_plan.name or ''), generate=False)
    try:
        plan = _plan_scene_from_card(win, dict(req, image=image), index=index)
    except Exception as e:                        # noqa: BLE001 - 计划失败要说出来
        swallow('ui_qt.main_window.scene.plan', e)
        notify_card_notice(win, f'换景没做：{e}')
        return None
    if plan is None or not getattr(plan, 'ok', False):
        reason = str(getattr(plan, 'reason', '') or '').strip() or '没有可写的改动。'
        notify_card_notice(win, '换景没做：' + reason)
        return None
    extra = sg_plan.diff_text() if (sg_plan is not None
                                   and getattr(sg_plan, 'writes', False)) else ''
    if not _confirm_at_plan(win, plan, extra=extra, title='换景（会清台）？'):
        try:
            win._status('已取消：没有换景。')
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.main_window.scene.cancel_confirm', e)
        return None
    if sg_plan is not None and getattr(sg_plan, 'writes', False):
        try:
            sg_plan.apply()
        except Exception as e:                    # noqa: BLE001 - 写文件失败要说出来
            swallow('ui_qt.main_window.scene.sg_apply', e)
            notify_card_notice(win, f'写 {sg_plan.rel} 失败：{e}')
            return None
        try:
            win._session.refresh_transform_index()
        except Exception as e:                    # noqa: BLE001 - 刷不到只影响显示
            swallow('ui_qt.main_window.scene.sg_refresh', e)
        notify_card_notice(win, f'已生成 {sg_plan.name} → {sg_plan.rel}'
                                '（工具生成、带指纹；手改过就不再覆盖）')
    try:
        if not plan.apply():
            notify_card_notice(win, '换景失败：计划没落进内存里的拍。')
            return None
    except Exception as e:                        # noqa: BLE001 - 写失败要说出来
        swallow('ui_qt.main_window.scene.apply', e)
        notify_card_notice(win, f'换景失败：{e}')
        return None
    _after_preview_beat_edit(win, plan, kind='已换景', tag='换景')
    _refresh_card_after_size(win, plan)
    try:
        # 台账那行由 `_after_preview_beat_edit` 统一写（措辞已经按"换景"走）；
        # 这里只补它不知道的那件事：**清掉了谁**（确认框里报过，事后也要能查）。
        if plan.cleared_sprites or plan.cleared_layers:
            win._log('换景清掉：'
                     + (f'立绘 {len(plan.cleared_sprites)} 个'
                        f'（{"、".join(plan.cleared_sprites[:4])}）'
                        if plan.cleared_sprites else '')
                     + ('；' if plan.cleared_sprites and plan.cleared_layers
                        else '')
                     + (f'图层 {len(plan.cleared_layers)} 个'
                        f'（{"、".join(plan.cleared_layers[:3])}）'
                        if plan.cleared_layers else ''))
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.main_window.scene.log', e)
    return plan


# ==================================================================
#  「一并改成一样」—— 合并回源（2026-10-31）
# ==================================================================
#
# 尺寸面板那句"本景还有 2 处单独改写"后面那个按钮。它动的是**用户的 show
# 行**（`docs/超清CG与BG_零侵入导入_2026-10-31.md` §5.5 的 L4：confirm、永远、
# 逐处），所以：
#
#   ① 计划是 `story.at_merge.plan_at_merge`（只算，不动 Beat）；
#   ② 确认框**一处一个勾**（`ui_qt.merge_dialog`），勾掉的一个字都不动；
#   ③ 写进内存的拍，导出时仍有既有 diff 门禁 —— 那一步还能反悔。

def _confirm_merge_plan(parent, plan):
    """
    合并回源的确认框（**唯一出口**：判据把它换掉，不弹真窗）。

    返回勾选的拍号集合；**取消**返回 `None`（与"一个都没勾"区分：
    后者要说"一处都没勾"，前者只写一行"已取消"）。
    """
    from storygraph.ui_qt.merge_dialog import ask_merge

    return ask_merge(plan, parent)


def apply_merge_from_panel(win, req):
    """
    尺寸面板「一并改成一样」：算计划 → **逐处**确认 → 写内存 Beat → 刷卡片。

    为什么是"把源的值套给那几处"而不是"删掉那几行"：Ren'Py 的 `show` 会
    **重建** displayable（`at` 不继承），所以删掉 `at` 等于回到原样 —— 见
    `story.at_merge` 的模块注释。这里只负责窗口层：门禁、脏标记、台账、刷新。

    返回落进内存的 `MergePlan`；取消 / 被拒 / 一处都没勾 / 失败返回 None。
    """
    if not isinstance(req, dict):
        return None
    from storygraph.story.at_merge import plan_at_merge

    # 卡片上还没保存的手改先落进这一拍（与「改」「换景」同一条前提）
    try:
        win.apply_current_beat()
    except Exception as e:                        # noqa: BLE001 - 提交失败也照样能合
        swallow('ui_qt.main_window.merge.apply_current', e)
    try:
        plan = plan_at_merge(
            list(getattr(win._session, 'beats', None) or ()),
            int(req.get('source', -1)),
            kind=str(req.get('kind') or 'scene'),
            tag=str(req.get('tag') or ''),
            label=str(req.get('label') or ''))
    except Exception as e:                        # noqa: BLE001 - 计划失败要说出来
        swallow('ui_qt.main_window.merge.plan', e)
        notify_card_notice(win, f'合并回源没做：{e}')
        return None
    if plan is None or not getattr(plan, 'ok', False):
        reason = str(getattr(plan, 'reason', '') or '').strip() \
            or '没有要合并的。'
        notify_card_notice(win, '合并回源没做：' + reason)
        return None
    chosen = _confirm_merge_plan(win, plan)
    if chosen is None:
        try:
            win._status('已取消：没有合并回源。')
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.main_window.merge.cancel', e)
        return None
    if not chosen:
        notify_card_notice(win, '一处都没勾：没有改任何行。')
        return None
    try:
        if not plan.apply(selected=set(int(i) for i in chosen)):
            notify_card_notice(win, '合并回源失败：计划没落进内存里的拍。')
            return None
    except Exception as e:                        # noqa: BLE001 - 写失败要说出来
        swallow('ui_qt.main_window.merge.apply', e)
        notify_card_notice(win, f'合并回源失败：{e}')
        return None
    try:
        win._session.mark_edits_dirty()
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.main_window.merge.dirty', e)
    try:
        win._sync_export_dirty()
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.main_window.merge.dirty_ui', e)
    _refresh_card_after_merge(win, plan)
    try:
        names = '、'.join(str(i + 1) for i in plan.beats[:6])
        more = (f' 等 {plan.count} 处' if plan.count > 6 else '')
        skipped = (f'（另有 {plan.skipped_count} 处改不了，见台账）'
                   if plan.skipped_count else '')
        win._log(f'[尺寸] 合并回源：第 {names} 拍的{plan.label} at 改成'
                 f' {plan.value_text}（源第 {plan.source + 1} 拍；'
                 '只改内存里的拍；不删行、不加行）')
        win._status(f'已把 {plan.count} 处改成和源一样{more}{skipped}'
                    '（只改内存里的拍；导出仍走既有 diff 确认门禁）')
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.main_window.merge.log', e)
    return plan


def _refresh_card_after_merge(win, plan) -> None:
    """
    合并之后刷卡片：**动到的拍里有当前载入的那一拍**才重载表单。

    与 `_refresh_card_after_size` 同一条规矩（重载会冲掉手改，所以只在
    "反正已经提交过"时重载）：这里事先调过 `apply_current_beat`，而且只有
    当载入的那一拍**自己被改了**才需要重载（`show` 那一列的文字变了）。
    """
    try:
        loaded = int(getattr(win, '_loaded_idx', -1))
    except (TypeError, ValueError):
        loaded = -1
    try:
        if loaded >= 0 and loaded in set(int(i) for i in plan.beats):
            win._show_index(loaded)
        else:
            win.refresh_derived_panels(loaded)
    except Exception as e:                        # noqa: BLE001 - 刷不动不拦写回
        swallow('ui_qt.main_window.merge.refresh_card', e)


def open_stage_preview_with_at(win):
    """
    工具栏「🎬 演出预览」：打开预览窗口，并把**当前 beats + 写回 at 入口**交给它。

    与 `window_export.open_stage_preview` 的差别只有"多传两个参数"：
    `beats` 让 S3 的「尺寸变换 → 写回 at」能用（只改内存 Beat），
    `on_apply_at` 让写回走主窗口的脏标记 / 导出确认门禁（见
    `apply_at_from_preview`）。
    """
    session = getattr(win, '_session', None)
    if not len(session):
        try:
            win._status('先载入一章。')
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.main_window.preview.no_chapter', e)
        return None
    try:
        win.apply_current_beat()                  # 先把卡片上的改动提交（老行为）
    except Exception as e:                        # noqa: BLE001 - 提交失败也照样能预览
        swallow('ui_qt.main_window.preview.apply_current', e)
    try:
        from storygraph.ui_qt.stage_preview import open_preview
        beats = list(getattr(session, 'beats', None) or [])
        return open_preview(win, session.frames(),
                            registry=win._registry_provider(),
                            project_root=getattr(win, '_project_root', None),
                            start_index=getattr(win, '_loaded_idx', 0),
                            beats=beats or None,
                            on_apply_at=partial(apply_at_from_preview, win),
                            # 跨章继承（2026-10-31）：第 1 拍的背景可能来自上一章。
                            initial_state=session.stage_initial_state())
    except Exception as e:                        # noqa: BLE001 - 与老入口同一处理
        QMessageBox.critical(win, '无法打开预览', str(e))
        return None


# 卡片编辑器（一句话一张表）搬到 `ui_qt.card_editor`（2026-11-01）：
# 它是一个**独立 QWidget**，不是主窗口的方法 —— 1559 行塞在主类文件里，
# 只为"少一个文件"。这里 re-export，`from ...main_window import
# BeatCardEditor` 的老写法（9 处测试 + 工具）不变。
from storygraph.ui_qt.card_editor import BeatCardEditor  # noqa: F401


# `_overwritable` 已搬到 `window_export.py`（唯一的使用者在那儿，见那边的
# 说明：自由函数不在 `REQUIRES` 契约里，搬 mixin 时漏了它 = 导出被拦下时
# `NameError`、界面什么都不发生）。这里保留 `_overwritable` 这个名字，
# 是因为它曾经是 `main_window` 的公共面（测试与文档都按那个名字引用）。


class MainWindowQt(GraphPageMixin, GraphOpsMixin, CardPageMixin,
                   NodeEditMixin,
                   WindowExportMixin, WindowAssetsMixin,
                   WindowKeyboardMixin, WindowNavMixin, WindowStateMixin,
                   RecoverMixin, WindowEngineMixin, QMainWindow):
    """
    Qt 版主窗口 —— **两页**：剧情结构图（第一屏）→ 卡片编辑器（点进模块才进）。

    **职责拆分**（P3-14，2026-10-05）
    --------------------------------
    原本是 3452 行 / 126 个成员的上帝对象（`EditorSession` 的另一半）。
    现在八块各住一个文件，全部做成 mixin —— **调用方一行都不用改**
    （这是搬家不是改契约：菜单、工具栏、信号、`open_editor` 全都没动）：

      · `GraphPageMixin`（`window_graph_page.py`，2026-11-01）——
        **第 0 页（结构页）的构建**。单开一块的理由见那个文件的模块
        docstring：主类受"方法数 ≤ 35"约束，293 行的构建器拆不开；
      · `GraphOpsMixin`（`window_graph_ops.py`，38）——
        结构分析 / 章节导入导出 / 布局存取 / 图级撤销 / 资源表；
      · `CardPageMixin`（`window_card.py`，22）——
        载入单章 / 拍列表 / 导航 / 增删卡片 / 卡片级撤销；
      · `NodeEditMixin`（`window_nodes.py`，12）——
        模块 ↔ 卡片 / 画布增删连边 / 右键菜单；
      · `WindowExportMixin`（`window_export.py`，10）——
        导出落盘 / 体检 / 舞台预览 / 剧本表；
      · `WindowAssetsMixin`（`window_assets.py`，3）—— 图片与下拉候选；
      · `WindowKeyboardMixin`（`window_keyboard.py`，5）—— 键盘与快捷键；
      · `WindowStateMixin`（`window_state.py`，8）—— **会话记忆**
        （下次打开回到上次那个项目 + 摆回布局，2026-10-13 用户要求）；
      · `RecoverMixin`（`window_recover.py`，9）—— **崩溃恢复**
        （编辑时定时抄一份、下次打开问一句；2026-10-23，体验报告 P0-2）。

    **本类自己留 34 个成员**，全是"界面骨架 + 页切换 + 谁都要用的工具"：
    两个页构建器（`_build_card_page` / `_build_toolbars`）、
    `_set_page` / `_update_crumb` /
    `_sync_action_states`、状态栏与日志（`_status` / `_log`）、
    静默失败上报（`_swallow_mark` / `_report_swallowed`）、
    图的所有权（`_set_graph`）、属性（`beats` / `_loaded_idx` …）、
    两个预取常量，以及 Qt 重写点（`closeEvent`）。

    ⚠ `_build_graph_page`（293 行）2026-11-01 搬去了 `GraphPageMixin`：
    主类只剩**一个方法位**，就地拆成五个小方法就顶破"≤ 35"。搬到
    mixin 之后那条约束只管主类，所以它已经拆成"造控件 / 摆布局 / 接线"
    四段（`_graph_page_*`）。`_build_card_page` / `_build_toolbars`
    **暂时留在本类**：它们各调了 10 / 2 个本模块的**模块级函数**
    （`BeatCardEditor` / `apply_size_from_card` / `open_mapping_from_card` …），
    一起搬走会立刻形成 `main_window ↔ window_graph_page` 的环。
    下一刀是先把 `BeatCardEditor`（1560 行，只依赖一个模块级名字）
    搬去 `ui_qt/card_editor.py`，再回来搬这两页。

    **基类顺序：mixin 必须排在 `QMainWindow` 前面**（实测踩过）
    --------------------------------------------------------------------------
    `eventFilter` / `closeEvent` 是 **Qt 虚函数** —— `QObject` 自己就定义了。
    基类写成 `(QMainWindow, WindowKeyboardMixin, …)` 时 MRO 是
    `MainWindowQt → QMainWindow → … → QObject → WindowKeyboardMixin`，
    于是 `QObject.eventFilter` **赢**、mixin 那份成了死代码：

        QTest.keyPress(...)  →  一个键都没接上
        （实测 13 条键盘用例全红：回车、Ctrl+S、裸↓、空格拖拽…，
         而 `hasattr(MainWindowQt, 'eventFilter')` 照样是 True）

    反过来写 MRO 仍然合法（C3 线性化），`super().__init__()` 也照样链到
    `QMainWindow` —— 十一个 mixin 都**没有**自己的 `__init__`，链上一路直通。
    这类事故是"搬家不是改契约"里最容易漏的一种：方法**在**（`hasattr` 真），
    **谁先被找到**却变了。由 `tests/test_main_window_mixins.py` 守。

    十一个 mixin 的成员在本类里**不许**有同名 —— 同名会让子类那份**静默**赢。

    用户 2026-10-02 的原话（这一版就是照它改的）：

    > 本来它应该先是一个结构化模块化的图排版将所有的东西连在一起，
    > 然后再是卡片编辑器的…… 我的剧情树编辑器在哪儿？
    > 本来应该先是剧情树编辑器才对的，点进单个模块后才跳转到卡牌编辑器

    上一版把两者**并排**（画布居中 + 卡片右 dock），后果有两个：

      · 卡片常驻右栏 440px，第一眼看到的就是卡片编辑器；
      · 不带项目参数启动时 `graph=None`，中央是**一片空白**，
        整屏只剩"拍列表 + 卡片" —— 用户说的"上来就是个卡牌编辑器"
        字面成立（有截图为证）。

    所以这里改成**页级导航**（`QStackedWidget`）：

      ┌ 工具栏（随页切换：结构页一组 / 卡片页一组）──────────────────────┐
      │ 第 0 页 剧情结构：剧情树 │ 结构图（画布） │ 结构分析（静态）        │
      │ 第 1 页 卡片编辑：← 返回 │ 拍列表 │ 卡片（保存 / 撤销）            │
      └ 状态栏 ──────────────────────────────────────────────────────────┘

      · 启动**永远停在结构页**（哪怕带了章节参数）；
      · 双击模块 / 剧情树双击 / 右键「在卡片界面里打开」→ 进卡片页；
      · 卡片页「← 返回剧情结构」→ 回结构页，并高亮刚编辑过的那个模块；
      · 键盘快捷键（回车翻拍、Ctrl+S…）**只在卡片页生效** ——
        在结构图上按回车应该"进这一章的卡片"，而不是翻别人的拍。

    两条路分开（2026-10-03，见本文件顶部说明）
    ------------------------------------------
      · 📁 打开项目 / 🔄 重读资源表 → 只加载资源表 xlsx，**图一个节点都不加**；
      · ➕ 导入章节 .rpy → 图里只放用户导入过的章节（累加，可整章移除）。

    本文件只做**摆控件、读表单、写表单**三件事
    ------------------------------------------
    全部编辑语义都在 `storygraph.app.editor.EditorSession` 里（深模块）：
    载入 / 字段投影 / 立绘卡 / 写回 / 导出（含增量补丁与安全门禁）/ 体检 /
    舞台帧 / 结构分析 / **图的范围**（导入哪些章节）。这里**不再**出现任何
    `beat.xxx = ` 赋值、也不再有 `self._panel._speaker` 这种"伸手进别人
    私有属性"的写法。
    """

    #: 页号（`_set_page` / 测试用）
    PAGE_GRAPH = 0
    PAGE_CARD = 1

    #: 结构图上没有图时的引导（**别再让人对着一片空白猜**）
    #:
    #: 注意第一句就说清楚"图里只有你导入的章节"：用户 2026-10-03 的原话是
    #: 「我没有叫你所有的文件全部导进剧情图内！……我要看瞎了」——
    #: 引导文案必须把"导入哪些才在图里"讲明白，否则又会有人以为该灌整个项目。
    EMPTY_HINT = (
        '剧情图是空的 —— 它只放「你自己导入的章节」，不会自动装进整个项目。\n\n'
        '① 点「➕ 导入章节 .rpy」选要看的章节（可多选，累加）\n'
        '② 双击模块 → 进入卡片编辑器改这一章的台词\n'
        '③ 想看整部戏的分支/条件，就把相关章节都导入进来\n\n'
        '（「📁 打开项目」是另一条路：只加载资源表 xlsx —— 立绘 / 音乐 / '
        '角色候选与缩略图，它不往这张图里放任何东西）')

    def __init__(self, engine, script_path=None, project_root=None, graph=None):
        super().__init__()
        #: **全局滚轮守卫**（2026-10-30）：任何组合框 / 数值框 / 滑块都不许被
        #: 滚轮改值（用户："滚动会导致选择切换！！！不要这个！"）。装一次就够，
        #: 幂等；装在这里而不是 `main()`，是为了测试里单独建窗口也受保护。
        ensure_installed()
        self.engine = engine
        self._project_root = project_root or getattr(engine, 'project_root', None)
        #: 全部编辑语义都在这个会话里（本窗口不自己实现任何一条）
        self._session = EditorSession(
            registry_provider=self._registry_provider,
            project_root=self._project_root)
        # 缩略图的解码上限要跟得上卡片尺寸（`tokens.CARD.thumbW/H` 放大过一轮，
        # 解码尺寸还停在 160×220 的话，放大后就是糊的）。
        self._loader = ThumbLoader(self, max_size=(240, 320),
                                   project_root=self._project_root)
        self._notifier = None
        self.graph = graph
        #: 剧情图"有没有未保存的改动"（= 现在这张图 ≠ 上次落盘/加载的那张）。
        #:
        #: 判据用布局 JSON 的指纹（`session_graph.layout_signature`），不是
        #: 手工在每个命令里置位 —— 置位总有漏掉的路径（拖块 / 撤销 / 派生边），
        #: 而指纹在 `_set_graph` 这个唯一同步点自动成立。
        self._graph_saved_sig = ''
        self._graph_dirty = False
        #: 这一章有没有**还没导出到 .rpy** 的改动（标题栏 `*` 标记）。
        self._script_dirty = False
        self._last_layout_path = ''
        self._base_title = '剧情编辑器 · 剧情结构图 + 卡片界面'
        # —— 图级撤销栈（旧版 `graph.commands.HistoryManager`）——
        #
        # 命令对象要一个"视图接口"：`graph` / `selected_node` / `selected_edge`
        # / `on_graph_*_changed()` / `update_node_position()` /
        # `_apply_layout_data()` —— 都在下面几个方法里实现（旧版是
        # `GraphView` 身上的同名成员，Qt 版一个都没接，于是结构页全无撤销）。
        from storygraph.graph.commands import HistoryManager
        self._history = HistoryManager(self)
        self.selected_node = None
        self.selected_edge = None
        #: 已知的"演出表 mtime"（表被外部改过 → 自动刷候选下拉）。
        #: 状态放在窗口上、规则放在 `app.tables`，两边都不越界。
        self._table_mtimes: dict = {}
        #: 当前页（`QStackedWidget` 的索引；`_set_page` 维护）
        self._page = self.PAGE_GRAPH
        # ⚠ **命令行的意图要先记下来，再 `_build()`**（2026-10-13）：
        #   `_build()` 会建菜单，而菜单要读"上次的项目"给勾选框定状态；
        #   而 `_boot`（第一个事件循环回合）里的会话记忆要能回答
        #   "命令行是不是已经明确指定了项目/章节"—— 那个判断就靠这两个属性。
        #   放到 `_build()` 之后设，就是"恢复跑完了才知道命令行给了什么"。
        self._pending_script = Path(script_path) if script_path else None
        self.setWindowTitle(self._base_title)
        # ⚠ **按屏幕压一遍**（2026-10-23）：`sizeHint()` 会把工具栏的完整宽度
        #   算进来（实测 2720×705），照它开窗在 1080p/2K 上都跑到屏幕外。
        # 2026-10-30：默认尺寸 1600×950 → **1200×800**（用户第 5 条反馈"默认为窄"；
        #   数字见模块顶部 `DEFAULT_WINDOW_SIZE`）。有记忆时以记忆为准，但
        #   `window_state.narrow_layout` 会把它**压在这个上限以内**（每 次 都 压，
        #   不是只迁一次 —— 用户第 6 条："打开已经打开过的项目还是这么大"）。
        self.resize(*fit_to_available(*DEFAULT_WINDOW_SIZE))
        self._build()
        #: 构造后**立刻**载入这一章（同步）。
        #: 不推迟到 QTimer 里 —— 调用方（测试、脚本、`open_editor`）
        #: 需要"构造完就能用"，否则得先跑一次事件循环才知道载没载上。
        #: `goto_card=False`：带了章节参数也**先停在结构页**（用户明确要求
        #: 第一屏是剧情树），章节内容已经在会话里，双击模块即可进去。
        if self._pending_script is not None:
            self.load_script(self._pending_script, goto_card=False)
        QTimer.singleShot(0, self._boot)

    # ------------------------------------------------------------------
    #  会话 / 状态
    # ------------------------------------------------------------------

    def _registry_provider(self):
        """
        资产注册表：**优先用「打开项目」装进来的那份**，其次才问引擎。

        用户 2026-10-03：

        > 添加立绘，添加语音，添加音乐，全部都没有反应！
        > 我不是已经注册了资产吗？为何不能从中选取并导入呢？？？

        根因就在这个函数：它只问 `engine.get_registry()`，而真实应用里的引擎
        是 `_EngineShim` —— 「打开项目」只更新了会话与缩略图 loader 的
        `project_root`，**没更新引擎**，于是引擎永远返回 None。
        后果：`pick_image(registry=None)` **静默返回 ''**，按钮点了毫无反应。

        现在先取会话已加载的那份（「打开项目」写进去的），再回落引擎
        （老路径 / 测试里的假引擎），两边都不会成环 ——
        `session.assets_registry()` 只回答"我手上有没有"。
        """
        try:
            reg = self._session.assets_registry()
            if reg is not None:
                return reg
        except Exception:                    # noqa: BLE001
            swallow('main_window._registry_provider')
        try:
            return self.engine.get_registry()
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.main_window._registry_provider', e)
            return None

    def _ensure_registry(self) -> bool:
        """有没有可用的资源表？没有就**说清楚**（不再静默什么都不做）。"""
        if self._registry_provider() is not None:
            return True
        msg = ('还没有资源表可用 —— 先点「📁 打开项目」选项目根'
               '（含资源表 xlsx），才能从已登记的立绘 / 音乐 / 语音里挑。')
        self._log('[提示] ' + msg)
        self._status(msg)
        return False

    @property
    def beats(self) -> list:
        """当前会话的拍列表（只读视图，测试与图高亮要用）。"""
        return self._session.beats

    @property
    def _loaded_idx(self) -> int:
        return getattr(self, '_idx', -1)

    @property
    def _file_path(self) -> str:
        p = self._session.script_path
        return str(p) if p else ''

    @property
    def _symbols(self):
        return self._session.symbols

    def current_page(self) -> int:
        """当前页号（0 = 剧情结构，1 = 卡片编辑）。"""
        return self._page

    def _boot(self):
        """
        构造之后（第一个事件循环回合）的收尾。

        **这里只同步状态，不碰页号、也不碰空状态** —— 曾经的写法是 `_boot`
        里再 `_set_page(结构页)`，于是"构造完立刻打开某一章"会被这个延迟任务
        **静默弹回结构页**（用户在第一个回合内做的跳转全部作废）。
        页号、空状态、剧情树现在都在 `_build` 末尾同步定好了；
        这里只把导航可用性对一次会话状态。

        ⚠ **会话记忆的恢复就挂在这一步**（2026-10-13）：它读盘、开项目、载章节、
        摆布局 —— 全在第一个事件循环回合里做，所以构造窗口的人拿到的是一个
        完整可用的窗口，而不是"先空着、一会儿自己变"。
        `_boot` 里已经不再碰页号（见下），所以恢复出来的页号也不会被弹回去。
        """
        self._sync_nav()
        self._boot_restore_session()

    def _boot_restore_session(self):
        """
        启动时"按记忆回到上次那个项目"——**只在命令行什么都没给时**。

        为什么加这条门（两条都很实在）：
          · `main_qt` 已经把项目/章节从命令行喂进来了 —— 那是用户的**明确指定**，
            不能被"上次那个"盖掉（否则 `StoryGraph.exe 某项目 某章` 会先开
            上次的项目、再开你指的，白读一遍资源表还算轻的）；
          · 打包自检（`selfcheck.py` 用 `open_editor(None, None, None, None)`
            建窗口）也走这里 —— 记忆坏了绝不能让自检跟着红。

        `STORYGRAPH_NO_SESSION=1` 可整趟关掉（自动化 / "今天想空着开"）。
        """
        from storygraph.core import session_store as SS
        if not SS.enabled():
            return
        if getattr(self, '_pending_script', None) is not None:
            return
        if getattr(self, '_project_root', None):
            return
        self.restore_session()

    def _beat_context(self) -> dict:
        """
        `line_id` → "第几拍 / 共几拍 / 前后各一句"（S0，只读）。

        ⚠ 判空用 `is not None`：`EditorSession` 有 `__len__`（= 拍数），
        没载入章节时是 **falsy** —— 写成 `if self._session:` 会静默走 else，
        图上看起来"这条边没有位置"，其实就是被判空了（2026-10-23 踩过）。
        """
        from storygraph.story.flow import beat_context

        session = getattr(self, '_session', None)
        if session is None:
            return {}
        try:
            return beat_context(list(getattr(session, 'beats', None) or ()))
        except Exception as e:               # noqa: BLE001 - 位置画不出来不该拖垮图
            swallow('main_window._beat_context', e)
            return {}

    def _open_edge_in_card(self, edge):
        """
        右键一条线 →「定位到这一拍」：进卡片页，落在**它对应的那一拍**
        （2026-10-24 起双击线改为「编辑连线」，不再抢这个动作）。

        ## 顺序是有讲究的

        先 `_on_node_open`（**同步**载入 from 所在那一章），**然后**才取拍上下文。
        反过来的话，跨章边会用"上一章"的拍号去定位 —— 图上同时有好几章时必现
        （用户看到的是"点一条线，跳到了完全无关的位置"）。

        ## 三种"点不动"都要说话

        草图没有 `line_id`、拍已经不存在、节点不在图上 —— 一律给一句状态栏提示。
        没有反馈最容易被当成卡死（这条是项目里反复踩过的）。
        """
        line_id = str(getattr(edge, 'line_id', '') or '')
        if not line_id:
            self._status('这条线是草图（未写入），剧本里还没有对应的那一行 —— '
                         '右键这条线 →「写入剧本…」，或删除它。')
            return

        nid = getattr(edge, 'from_id', None)
        if self.graph is None or nid not in (self.graph.nodes or {}):
            self._status('这条线的起点不在当前图上。')
            return

        try:
            self._on_node_open(nid)
        except Exception as e:               # noqa: BLE001 - 打不开只提示，不崩
            swallow('main_window._open_edge_in_card.open', e)
            self._status(f'打开这一章失败：{e}')
            return

        info = self._beat_context().get(line_id)
        if not info:
            self._status('这条线对应的那一拍不在这章里了（可能刚改过）—— '
                         '已停在章节开头。')
            return

        idx = int(info.get('index') or 0) - 1
        try:
            if self._session is not None and self._session.has(idx):
                self._show_index(idx)
                where = f'（{info["label"]}）' if info.get('label') else ''
                self._status(f'已定位到第 {info["index"]} 拍 / '
                             f'共 {info["total"]} 拍{where}')
        except Exception as e:               # noqa: BLE001
            swallow('main_window._open_edge_in_card.show', e)

    def _set_graph(self, graph, keep_view=None):
        """
        换一张图：画布 + 空状态提示 + 右栏"当前模块"**一起**更新。

        这三个以前是散在各处的（`rescan_project` / `_boot` / 右键菜单各写
        一遍 `self._canvas.set_graph(...)`），漏一处就出现"图上变了、
        别处还是旧的"。现在只有这一个入口。

        `keep_view` 透传给画布（见 `GraphCanvas.set_graph`）：默认保住用户
        当前的缩放与视口位置 —— 连线 / 撤销 / 改内容都**不该**把缩放重置
        （用户 2026-10-05："每连一个线，缩放就变……这不好"）。
        """
        self.graph = graph
        try:
            self._canvas.set_graph(graph, keep_view=keep_view)
        except Exception as e:               # noqa: BLE001
            self._status(f'画布更新失败：{e}')
        # 位置徽标（S0）：**必须在 set_graph 之后** —— set_graph 会重建全部
        # EdgeItem，先灌上下文的话，那些新建的线拿到的还是空的。
        try:
            self._canvas.set_beat_context(self._beat_context())
        except Exception as e:               # noqa: BLE001 - 位置是增强，不该挡住图
            swallow('main_window._set_graph.beat_context', e)
        has_nodes = bool(getattr(graph, 'nodes', None))
        self._canvas.set_empty_hint('' if has_nodes else self.EMPTY_HINT)
        self._on_canvas_select(self._canvas.selected_node_id())
        self._update_crumb()
        self._sync_graph_dirty()
        # 图上多了/少了哪些 label → 左侧导航的 `●` 跟着变（`_set_graph` 是
        # 图变更的唯一同步点，放这里就不会有"图变了导航没变"的路径）。
        try:
            self._sync_nav_marks()
        except Exception as e:               # noqa: BLE001 - 标记失败不影响图
            swallow('main_window._set_graph.nav_marks', e)

    # ------------------------------------------------------------------
    #  界面
    # ------------------------------------------------------------------

    def _build(self):
        theme.apply_to(self)

        self._status_bar = AppStatusBar(self)
        self.setStatusBar(self._status_bar)

        # —— 中央：两页（0 结构 / 1 卡片）——
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)
        self._stack.addWidget(self._build_graph_page())
        self._stack.addWidget(self._build_card_page())
        #: Toast 挂在 stack 上：两页都能飘提示（挂在画布上的话，
        #: 切到卡片页就再也看不到后端通知了）。
        self._toast = Toast(self._stack)
        self._notifier = Notifier(self._status_bar, self._toast).subscribe()

        # —— 左侧「章节导航」+ 顶部模式条（2026-10-23，照参考界面）——
        # 导航是 QDockWidget：它不属于任何一页，切页不丢；关掉还能从
        # 「视图」菜单叫回来（状态跟着布局一起记，见 `window_state`）。
        self._build_nav_dock()
        self._build_mode_bar()

        self._build_toolbars()

        self._status('先「➕ 导入章节 .rpy」把要看的章节加进剧情图'
                     '（可多选，累加）；「📁 打开项目」只加载资源表 xlsx，'
                     '不往图里放东西。❓ 使用说明随时可查')
        # 空状态 / 剧情树 / 面包屑**同步**摆好，不留给 QTimer：
        # 延迟到第一个事件循环回合的话，构造完到那一回合之间是"没有提示的
        # 空白画布 + 旧标题"，而且任何"构造完立刻做事"的调用方都会看到
        # 半成品界面（这个坑 2026-10-02 在页号上踩过一次，见 `_boot`）。
        # 图从空开始（用户导入才长），所以这里是廉价操作。
        self._set_graph(self.graph)
        # 第一屏 = 剧情结构。**同步定下来**（不留给 QTimer）：
        # 一是开局工具栏/页号立刻就是对的，二是后面任何跳转都不会被
        # 延迟任务覆盖掉（见 `_boot` 的注释）。
        self._set_page(self.PAGE_GRAPH)
        # 键盘：**必须最后装**（_panel / _list 都建好了，
        # 事件里要判断焦点在哪）
        self._install_shortcuts()
        # 自动保存：**建好就开始走**（干净时每个 tick 什么都不做）。
        # 为什么不等到载入章节再建：`load_script` 有多条路进（导入 / 记忆恢复 /
        # 双击模块），漏一条就是"这一段时间里崩了没处捡"。
        self.autosave_timer()

    # ---------------- 第 0 页：剧情结构 ----------------


    def _log(self, text: str):
        """
        往右上角的控制台追加一行（**保留原内容**，并滚到末尾）。

        旧版 `GraphView._append_analysis` 就是这个职责，删 tkinter 时被我
        连同右栏一起撤掉了 —— 于是"导入完了什么都不知道"（用户 2026-10-03）。
        消息前缀沿用旧版的方括号风格：`[项目] / [导入] / [剧本表] / [体检]`。
        """
        line = str(text or '').rstrip()
        if not line:
            return
        try:
            self._console.appendPlainText(line)
            bar = self._console.verticalScrollBar()
            bar.setValue(bar.maximum())
        except Exception:                    # noqa: BLE001
            swallow('main_window._log')

    def clear_console(self):
        """只清控制台文字（不动图、不动文件）。"""
        try:
            self._console.clear()
        except Exception:                    # noqa: BLE001
            swallow('main_window.clear_console')






    # ---------------- 第 1 页：卡片编辑 ----------------

    def _build_card_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                                 T.SPACE['sm'], T.SPACE['sm'])
        outer.setSpacing(T.SPACE['sm'])

        # —— 面包屑：回来路 + 现在在哪一章 ——
        bar = QWidget()
        bh = QHBoxLayout(bar)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(T.SPACE['sm'])
        self._back_btn = QPushButton('← 返回剧情结构')
        self._back_btn.setToolTip('回到剧情结构图（Esc）')
        self._back_btn.clicked.connect(self._back_to_graph)
        bh.addWidget(self._back_btn)
        self._crumb = QLabel('剧情结构')
        self._crumb.setProperty('role', 'section')
        bh.addWidget(self._crumb)
        # 面包屑右边那句提示：**可以被压窄**。
        # 2026-10-23 用户截图：这一行在 1680px 上把「↩ 撤销结构操作」「🗑 删除
        # 这张卡片」挤到窗口外 —— 实测行最小 1279px、可用 1106px。
        # 元凶是普通 QLabel 的最小宽度 = 整句宽度（468px）；换成省略标签后
        # 最小 0，窗口再窄也只会把提示画成 `…`，按钮始终在。
        self._crumb_hint = ElidedLabel('', mode=Qt.ElideRight, role='caption')
        bh.addWidget(self._crumb_hint, 1)
        # —— 增 / 删卡片（结构编辑）——
        # 用户 2026-10-03："只能够修改，不能增删，还是没用！"
        #
        # ⚠ **必须包一层 lambda**（2026-10-05 实测，用户报"两个按钮一模一样"）：
        # `QPushButton.clicked` 的信号签名是 `clicked(bool checked = False)`，
        # PySide 会把这个 bool **当成第一个位置参数**喂给槽函数 ——
        # 直接 `connect(self.insert_card)` 等于每次都调
        # `insert_card(after=False)`，也就是**"插到前面"**！
        # 于是「插入卡片」与「插到前面」效果完全相同（用户的原话）。
        #
        # 为什么单测没抓到：`test_ui_qt` 里那两条是**直接调方法**
        # （`window.insert_card(after=True)`），根本没经过信号 ——
        # 缺陷在接线处，而用例绕开了接线（"测试绿了 ≠ 缺陷被钉住"）。
        # 现在有 `test_every_button_slot_can_accept_the_clicked_bool` 守着这一类。
        #
        # 2026-10-30（用户第 5 条「UI 宽窄可变」）：这 5 个按钮合计约 570px，
        # 是**窗口最小宽 1142px 的主因**（`.pip-tmp/plan/05_ui_width.md` §一）。
        # 第一版把它们收进「⋯ 结构」下拉菜单换宽度 —— 用户 2026-10-30 第 6 条
        # 直接否掉：**"不要那个结构！恢复回原来的 UI"**。菜单已整个删掉
        # （`_structural_btn` / `_structural_menu` / `_structural_actions` /
        # `sync_structural_menu` 都不再存在）。
        #
        # 现在这两个诉求**同时**成立：按钮全在条上、顺序照旧；装进一个会折行的
        # `CardActionRow` —— 宽窗口下与从前**一模一样**（一行、右对齐），
        # 窗口窄到放不下时才折到第二行。**一颗都不藏、也不裁在窗口外**，
        # 而"最小宽 = 最宽那一颗"（≈113px），窗口照样能缩到 700 出头。
        self._action_row = CardActionRow(self, spacing=T.SPACE['sm'])
        self._insert_btn = QPushButton('＋ 插入卡片')
        self._insert_btn.setToolTip('在当前这一拍后面插入一张空卡片（Ctrl+Insert）')
        self._insert_btn.clicked.connect(lambda: self.insert_card(after=True))
        self._insert_before_btn = QPushButton('＋ 插到前面')
        self._insert_before_btn.setToolTip('在当前这一拍**前面**插入一张空卡片')
        self._insert_before_btn.clicked.connect(
            lambda: self.insert_card(after=False))
        self._flow_btn = QPushButton('↳ 插入控制流卡')
        self._flow_btn.setToolTip(
            '在当前这一拍后面插入一张 call / jump 卡：选目标 label，'
            '调用完会回到这里继续')
        self._flow_btn.clicked.connect(lambda: self.insert_flow_card(after=True))
        self._choice_btn = QPushButton('◇ 插入选择点')
        self._choice_btn.setToolTip(
            '在当前这一拍后面放一个分支：剧本里**只多一行** call，'
            'menu 写在同目录的 <章节>_choices.rpy 里（不动剧本正文）')
        self._choice_btn.clicked.connect(self.insert_choice_point)
        self._restore_btn = QPushButton('↩ 撤销结构操作')
        self._restore_btn.setToolTip(
            '撤销最近一次增删卡片（可连按多级）：删掉的放回原位、插进去的撤掉')
        self._restore_btn.clicked.connect(self.restore_card)
        self._restore_btn.setEnabled(False)
        self._delete_btn = QPushButton('🗑 删除这张卡片')
        self._delete_btn.setToolTip('删掉当前这一拍（只改内存，导出才落盘）')
        self._delete_btn.setProperty('variant', 'danger')
        self._delete_btn.clicked.connect(self.delete_card)
        # 顺序 = 原来的动作条（插入 / 插到前面 / 控制流 / 选择点 / 撤销 / 删除）
        for _btn in (self._insert_btn, self._insert_before_btn, self._flow_btn,
                     self._choice_btn, self._restore_btn, self._delete_btn):
            self._action_row.add(_btn)
        bh.addWidget(self._action_row, 0)
        outer.addWidget(bar)

        # ⚠ 三栏的顺序照参考界面（2026-10-23）：
        #    左 = 章节导航（窗口级 dock，不在这里）
        #    中 = **剧本**（拍列表：行号 + 类型牌 + 说话人）
        #    右 = 检查器（这一拍的属性 + 卡片表单）
        # 原来拍列表挤在最左边 260px、卡片占掉其余全部宽度 —— 那是"表单
        # 为主"的排法；改成"正文居中、属性在右"之后，读剧本时视线不用横穿
        # 整块表单。`_split_card` 的名字与下标（0 = 列表）保持不变：
        # `window_state` 按它恢复宽度，改下标等于把用户存的宽度对错栏。
        #
        # 2026-10-30（用户第 5 条）：右栏**默认窄**（`CARD_SPLIT_DEFAULT`），
        # 并且 `setChildrenCollapsible(False)` —— 不让孩子被拖成 0 宽；
        # 右栏自身有 `CARD_COLUMN_MIN_W=340` 的硬下限（见下面 `right.setMinimumWidth`），
        # 所以"分栏拖到最窄"= 卡片刚好放得下 = **横向滚动条不可能出现**。
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        # —— 中：章节（label）/ 拍 / 参与角色 ——
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.SPACE['xs'])
        # 同一个 `.rpy` 可以有很多 label。没有这一行时，用户点第二个 label
        # 的节点只能看到文件第一拍（2026-10-22 实测报的）。
        label_row = QHBoxLayout()
        label_row.setSpacing(T.SPACE['xs'])
        label_cap = QLabel('章节（label）')
        label_cap.setProperty('role', 'section')
        label_row.addWidget(label_cap, 0)
        self._label_combo = QComboBox()
        self._label_combo.setToolTip('同一个 .rpy 里的 label；选一个跳到它的第一拍')
        self._label_combo.currentIndexChanged.connect(self._on_label_changed)
        label_row.addWidget(self._label_combo, 1)
        lay.addLayout(label_row)
        cap1 = QLabel('剧本（行号 = 拍号 · 回车翻拍）')
        cap1.setProperty('role', 'section')
        lay.addWidget(cap1)
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_select)
        # 多个 label 时列表里有"分组标题行"（不是拍）：点它折叠 / 展开，
        # 不改变当前拍。选中还是走 `currentRowChanged`。
        self._list.itemClicked.connect(self._on_list_item_clicked)
        # —— 剧本版画法（2026-10-23）——
        # **只换 delegate，不换控件**：模型、`Qt.UserRole` 里的拍号、选中态
        # 全是原样（几十条用例按 `item.text()` / `setCurrentRow` 判过）。
        # `entry_of` 把拍号递回给会话，排版逻辑不在这里重写。
        self._shot_delegate = ShotDelegate(
            self._list, entry_of=self._entry_of_beat)
        self._list.setItemDelegate(self._shot_delegate)
        self._list.setUniformItemSizes(False)
        self._list.setSpacing(0)
        # 横向滚动条**永久关掉**（2026-10-23）：行文字由 delegate 自己按
        # 视口宽度省略，横向滚动条只会多一条"看起来还有内容在右边"的假线索
        # —— 而且它一旦出现就会吃掉行高，列表底部那条细线就是它。
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setTextElideMode(Qt.ElideRight)
        self._list.setMouseTracking(True)      # 悬停高亮要它
        # `MouseTracking` 只管"鼠标移动事件"；`State_MouseOver` 还要 viewport
        # 带 `WA_Hover`（Qt 的 viewOptions 就是按这个属性判的）。
        self._list.viewport().setAttribute(Qt.WA_Hover, True)
        lay.addWidget(self._list, 1)
        cap2 = QLabel('参与角色（双击填进这一拍）')
        cap2.setProperty('role', 'caption')
        lay.addWidget(cap2)
        self._who = QListWidget()
        self._who.setFixedHeight(120)
        self._who.itemDoubleClicked.connect(self._fill_speaker)
        lay.addWidget(self._who, 0)
        split.addWidget(left)

        # —— 右：检查器（属性 + 卡片）——
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(T.SPACE['xs'])

        # —— 定位（**默认收起**，2026-10-27 用户点名）——
        #
        #   > 还有就是这个定位能不能折叠一下（卡片界面才是最主要的！！！定位
        #   > 默认折叠起来，卡片界面必须留几乎右边全屏）……留给我的可编辑
        #   > 窗口太小了！我看不过来！
        #
        # 那 6~7 行（文件 / label / 第几拍 / 类型 / 源位置 / 说话人 / 这一章）
        # 是"我在哪儿"用的，改台词时不需要一直盯着；收起之后整块高度都还给
        # 卡片。它**不是消失了**：点一下标题就展开，内容一直是同步的
        # （`_sync_inspector` 照旧在翻拍时填）。顶部模式条右边也已经有
        # 「ch01.rpy · ch01 · 第 1 拍」那一句，收起来不会"不知道自己在哪"。
        self._loc_open = False
        self._loc_btn = QPushButton('▸ 定位（这一拍在文件里的位置）')
        self._loc_btn.setProperty('variant', 'chip')
        self._loc_btn.setFlat(True)
        self._loc_btn.setToolTip(
            '文件 / label / 第几拍 / 类型 / 源在文件里的行 —— 点一下展开。'
            '\n默认收起：把右边这一整栏留给卡片。')
        self._loc_btn.clicked.connect(self._toggle_location)
        rv.addWidget(self._loc_btn, 0, Qt.AlignLeft)
        self._inspector = PropertyStrip()
        self._inspector.setVisible(False)
        rv.addWidget(self._inspector, 0)
        cap3_row = QHBoxLayout()
        cap3_row.setSpacing(T.SPACE['xs'])
        cap3 = QLabel('卡片界面（这一拍）')
        cap3.setProperty('role', 'section')
        cap3_row.addWidget(cap3, 0)
        cap3_row.addStretch(1)
        # 「⤢ 展开卡片」：右栏默认窄（420），要改大块内容时一键放大。
        # 与"双击分栏把手"是同一个动作（`toggle_card_column`）。
        self._card_expand_btn = QToolButton()
        self._card_expand_btn.setText('⤢')
        self._card_expand_btn.setToolTip(
            '展开 / 收起卡片栏（也可**双击分栏把手**）\n'
            '展开 = 这一栏占 72%，收起 = 回到默认的窄栏')
        self._card_expand_btn.clicked.connect(
            lambda _=False: toggle_card_column(self))
        cap3_row.addWidget(self._card_expand_btn, 0)
        rv.addLayout(cap3_row)
        self._panel = BeatCardEditor(
            symbols=None, loader=self._loader,
            on_pick_image=self._pick_image,
            on_pick_asset=self._pick_asset_for,
            # 立绘网格这条路要"看得见判定"（④⑥）：多带 PickResult / 资源映射入口 /
            # 拒绝通知（全是模块级函数的 partial —— 主类方法数预算见 `toggle_card_column`）
            on_pick_image_result=partial(pick_sprite_result, self),
            on_open_mapping=partial(open_mapping_from_card, self),
            on_notice=partial(notify_card_notice, self))
        # 立绘缩略图的路径解析：真源是资源表的 Path 列，只有会话拿得到
        self._panel.set_path_resolver(self._session.path_of_image)
        # 卡片里那两个按钮直接连过来（用户要的"保存 / 撤销这张卡片"）
        self._panel.save_requested.connect(self.save_current_beat)
        self._panel.undo_requested.connect(self.undo_current_beat)
        # 「尺寸」一格的「改」：计划 / diff 确认 / 写回。
        # 走模块级函数（`apply_size_from_card`）而不是主类方法 —— 主类成员受
        # `tests/test_main_window_mixins.py` 的"每个成员都要认领"约束。
        # ⚠ 直接连**面板上**那个信号：卡片不转一层（少一个"信号连信号"）。
        self._panel.size_panel().size_requested.connect(
            partial(apply_size_from_card, self))
        # 背景行的「换景」：写 `scene`（会清台），确认框里报会清掉几个立绘。
        # 与「改」**分开一条路**：`scene` 的代价不是像素，是台上的人。
        self._panel.size_panel().scene_requested.connect(
            partial(apply_scene_from_card, self))
        # 「一并改成一样」（合并回源）：动的是用户的 `show` 行（L4），
        # 逐处确认、只改内存 —— 见 `story.at_merge`。
        self._panel.size_panel().merge_requested.connect(
            partial(apply_merge_from_panel, self))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._panel)
        # 横向滚动条**永久关掉**（用户 2026-10-30："右窗口不得有横向滚动条！
        # 所有图案不得超出右边框！"）。理由与左边剧本列表同一条：卡片是"表单"，
        # 横向滚动条只会多一条"右边还有东西"的假线索，还把内容挤成两屏宽。
        # ⚠ 关掉它**不是**解决办法 —— 真正的保证是"卡片最小宽 ≤ 右栏"：
        # 判据 `tests/test_ui_width.py::test_card_min_width_budget` 与
        # `test_no_widget_sticks_out_of_the_right_edge`（逐拍量右边界）。
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        rv.addWidget(scroll, 1)
        # ⚠ **结构性保证**：右栏最小宽 = 340（卡片地板实测 296，留 44px 余量）。
        # `QScrollArea(widgetResizable=True)` 的规则是"卡片宽 = max(视口宽, 卡片
        # 最小宽)"，所以只要右栏 ≥ 卡片最小宽，横向滚动条就**不可能**出现；
        # 分栏拖到最窄会自动停在这里（`setChildrenCollapsible(False)` + 本下限）。
        # 判据：`tests/test_ui_width.py` 把右栏压到 340 逐拍断言 hbar 不可见。
        right.setMinimumWidth(CARD_COLUMN_MIN_W)
        self._right_col = right
        split.addWidget(right)

        # 中间那一栏要能读剧本（行号 + 类型牌 + 说话人），比原来 260 宽不少；
        # 右栏是"属性 + 表单"，不该吃掉 2/3 的宽度 —— 2026-10-23 用户截图：
        # 正文 500px、右栏 900px，读台词反而最挤。
        # 2026-10-30：默认再往窄拉一档（用户第 5 条"默认为窄"）：右栏 ≈ 420，
        # 读剧本的正文栏多留一些。要改大块内容时点「⤢」或双击分栏把手。
        split.setSizes(list(CARD_SPLIT_DEFAULT))
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        # 双击分栏把手 = 展开 / 收起卡片栏（与「⤢」同一个动作）
        handle = split.handle(1)
        if handle is not None:
            handle.setToolTip('拖动 = 调整卡片栏宽度\n双击 = 展开 / 收起卡片栏')
            self._card_handle_filter = CardColumnExpandFilter(self)
            handle.installEventFilter(self._card_handle_filter)
        self._card_col_expanded = False
        self._card_col_saved = list(CARD_SPLIT_DEFAULT)
        self._split_card = split          # 同上：会话记忆要摆回它
        outer.addWidget(split, 1)
        return page

    # ---------------- 工具栏（两组，随页切换）----------------

    def _build_toolbars(self):
        tb_graph = QToolBar('剧情结构')
        tb_graph.setObjectName('pageBarGraph')     # saveState 要它（否则 dock 状态存不下）
        tb_graph.setMovable(False)
        tb_graph.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb_graph)
        tb_card = QToolBar('卡片编辑')
        tb_card.setObjectName('pageBarCard')
        tb_card.setMovable(False)
        tb_card.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb_card)

        #: 下面这些 `act('…')` 全部加进**这个**工具栏（随页切换显隐）。
        #: 保持 `act('文字', 回调)` 的写法不变：`tests/test_entry_points.py`
        #: 就是按这个模式扫"挂上去的回调是否真的存在"。
        self._act_tb = tb_graph

        def act(text, fn, tip=None, primary=False):
            """
            往 `self._act_tb` 指向的工具栏加一个动作。

            **刻意不收 `shortcut=` 参数**：`QAction` 的快捷键不受"当前在哪一页"
            约束（工具栏隐藏也照样触发），两页导航之后那是个陷阱。
            键盘只在 `eventFilter` 里处理（那里判页号），
            `tests/test_ui_qt.py::test_no_action_carries_a_shortcut_...`
            守着这条规矩。快捷键写在 `tip` 里告诉用户即可。
            """
            a = QAction(text, self)
            # ⚠ **统一包一层**：`QAction.triggered` 的签名是
            # `triggered(bool checked = False)`，PySide 会把这个 bool 当成
            # **第一个位置参数**喂给槽函数。直接 `connect(fn)` 时，
            # 回调形如 `add_node(at=None)` 的就会收到 `at=False` —— 于是
            # "不给就放视野中央"那条分支永远走不到，`float(False[0])` 直接抛
            # （用户看到的是"点了没反应"）。`add_edge(from_id=None)` 同理。
            #
            # 一处收口：工具栏回调**一律不收参数**（想拿 checked 就自己传
            # lambda 进来）。这样以后往 `act(...)` 挂回调不会再踩同一个坑。
            # 守卫：`tools/audit_qt_signal_slots.py`
            # + `tests/test_qt_signal_wiring.py`。
            a.triggered.connect(lambda _checked=False: fn())
            if tip:
                a.setToolTip(tip)
            self._act_tb.addAction(a)
            if primary:
                btn = self._act_tb.widgetForAction(a)
                if btn is not None:
                    btn.setProperty('variant', 'primary')
                    btn.style().unpolish(btn)
                    btn.style().polish(btn)
            return a

        self._tb_graph = tb_graph
        self._tb_card = tb_card

        # —— 「▥ 章节导航」开关：**摆最左边**（2026-10-26 用户："章节导航应摆到
        #   最左边，然后是打开项目，打开项目后是自定义模块，再之后 ai 助手……
        #   然后先是导入，后是导出，最后才是刷新和清理"）——
        # 它是窗口级的 dock 开关，不是某一页的功能，所以**两页工具栏都有**，
        # 而且都排第一个。同一个 QAction 挂两个工具栏，勾选状态由 Qt 同步。
        nav = QAction('▥ 章节导航', self)
        nav.setObjectName('navAction')
        nav.setCheckable(True)
        nav.setChecked(True)
        nav.setToolTip('左侧项目目录：文件 → label，双击打开这一章')
        nav.toggled.connect(self._toggle_nav_dock)
        self._nav_action = nav
        for bar in (tb_graph, tb_card):
            bar.addAction(nav)
            bar.addSeparator()

        # —— 第一屏：剧情结构 ——
        #
        # ⚠ 两条路**必须分开**（用户 2026-10-03 纠正）：
        #   · 「打开项目」= 资源表（xlsx）那条路：立绘/音乐候选、缩略图、
        #     参与角色。**它不往剧情图里放任何东西**。
        #   · 「导入章节」= 剧情图那条路：图里只有用户自己导入的章节。
        # 以前把两者写成一句话（"打开项目 → 建出结构图"），40 个脚本一次性
        # 灌进图里变成 51 个块 —— 用户："这还选个毛线啊！看都要看瞎了！"
        #
        # ⭐ 顺序是用户 2026-10-26 点名的（**从左到右**）：
        #     章节导航 → 打开项目 → 自定义模块 → AI 助手 →
        #     导入（章节 / 纯文字）→ 导出 → 刷新 → 清理
        #   判据 `tests/test_custom_statement_entry.py::test_the_toolbar_order_matches_the_user_spec`。
        act('📁 打开项目', self.open_project,
            '只加载资源表（xlsx：立绘 / 音乐 / 语音 / 变换 / 角色）'
            '与缩略图 —— **不往剧情图里放任何东西**')
        act('🧩 自定义语句', self.learn_custom_statement,
            '教工具认识项目自己的写法（swim_suit / mycue 这类）：'
            '先扫出"工具还不认识的写法"，挑一个、说清它后面跟什么，'
            '粘一行剧本切一下看看，保存就写进 storygraph.rules.json —— '
            '之后这些格就是表格列、候选值就是下拉。'
            '候选分栏与项目清单（删除 / 重置）在同一个弹窗里。',
            primary=True)
        # 「填 API key」**不再单独占一个按钮**（用户 2026-10-26：
        #   "ai 助手那里填入 api 的那个功能完全归入 ai 助手之中"）——
        #   它已经是 AI 助手面板里的「🔑 填 API key」按钮（`panel_ai.AiPanel`）。
        act('🤖 AI 助手', self.open_ai_panel,
            '跟它说要干什么（例：看看项目里有哪些我不认识的写法）。'
            '它会自己读项目、提议怎么改；**要写盘时会先弹窗给你看 diff**，'
            '你点头它才写。没配好 key 时面板里就有「🔑 填 API key」按钮',
            primary=True)
        act('➕ 导入章节 .rpy', self.import_chapters,
            '可多选；图里只出现你导入过的章节（累加，不覆盖已导入的）',
            primary=True)
        act('📝 导入纯文字章节', self.import_plain_text,
            '选 .txt（作者写的纯文字）→ 先问你「导出到哪个文件夹」→ 转成 .rpy '
            '落盘并导入；有认不出的角色写法会先问你一句'
            '（确认后写进 characters.xlsx）')
        # ⭐ 「导出」紧跟导入（用户 2026-10-26："先是导入，后是导出，最后才是
        #   刷新和清理"）。它跟"现在在哪一页"无关 —— 导的是**已载入的那一章**，
        #   所以两页顶部都有它。
        self._export_action = act('⤓ 导出 .rpy', self.export_script,
                                  '把这一章写回磁盘（编译 + 增量补丁 + 安全门禁）。\n'
                                  '最常用的落盘动作：**两页顶部都有它**，'
                                  '不用先回卡片页。\n'
                                  '路径默认是这张表该去的地方，可以改到 build/ 下先看一眼。',
                                  primary=True)
        act('🔁 刷新已导入章节…', self.reload_imported_chapters,
            '磁盘上的 .rpy 改过之后，重新解析**已导入的那些**（先给预览：'
            '新增 / 移除哪些 label；源里删掉的会一起移除）')
        act('🧹 清理导入残留…', self.cleanup_import_junk,
            '把导入/导出留下的 `.rpy.bak` / `build/review/*.blocked.rpy` 一次扫出来，'
            '按类勾选后一起删（都不参与游戏运行、删除不可恢复；'
            '`.rpy.bak` 默认不勾 —— 它可能是手写文件唯一的旧版本）')
        act('🔗 补全缺失章节…', self.complete_related_chapters,
            '把图上"被跳到、但文件还没导入"的章节一起导进来（先给预览）'
            ' → 连线才画得出')
        act('🗑 清空剧情图', self.clear_graph,
            '只清图（资源表 / 项目设置都不动）')
        tb_graph.addSeparator()
        act('➕ 新章节', self.add_node, '新建一个章节模块（label 名校验过）')
        act('🔗 连线', self.add_edge, '在两个模块之间连 jump / call / 菜单')
        act('⤢ 自动布局', self._canvas.auto_layout,
            '把模块重新排好（分层 + 减交叉）：层还是左右排开，排到画布右边\n'
            '就拐到下面一排、下一排反着走（分叉仍然竖着并排）')
        act('▤ 按文件顺序排列', self.arrange_by_file,
            '一个 .rpy 一列，同一列从上到下就是文件里的先后（按行号）——\n'
            '一个 .rpy 里写了多个 label 时用它对照源文件；\n'
            '按画布大小折行（排满往右、再满了往下）；\n'
            '要看跳转关系用「⤢ 自动布局」')
        act('🔍 适应窗口', self._canvas.fit_to_content, '整张图缩放到看得全')
        self._open_action = act('✎ 编辑这一章的卡片', self._open_selected_in_card,
                                '进卡片编辑器改选中模块那一章（也可双击模块）')
        # 「🎮 引擎试玩」放这里：结构图上看完"章和章怎么串"，下一步就是想看
        # 这一章真跑起来是什么样 —— 不用先进卡片页再点一次（2026-10-29）。
        self._engine_graph_action = act(
            '🎮 引擎试玩', self.engine_preview_selected,
            '用随工具的 Ren\'Py 引擎，从选中模块那一章开头真跑一遍\n'
            '（转场 / ATL / 自定义 UI / 小游戏都是真的；标准版会提示装演出专用版）')
        tb_graph.addSeparator()
        act('🔎 检查逻辑问题', self.check_logic, '可达性 / 死分支；结论写在右侧并在图上标红')
        act('📊 变量分析', self.show_analysis, '逐节点的变量区间与操作明细（只读）')
        act('📊 变量分析（可过滤）…', self.show_analysis_dialog,
            '同样的分析结果，给一个能按变量名过滤的窗口（找"love 出现在哪"）')
        act('📦 资源体检', self.check_asset_health,
            '检查资源表与项目资源是否一一对应，并出示报告（只读）')
        act('🩺 项目体检', self.show_health, '三级校验，只读检查，不写盘')
        act('🧵 跨章连续性', self.show_continuity,
            '上一章留下的背景 / 立绘 / CG / BGM，下一章第一句台词时还在不在？\n'
            '只读；下一章只有一条入边就判继承（分支点分出去的各章继承同一份'
            '章末状态），分支汇聚不猜\n'
            '（报告里会把"不判定"的章节与各前驱的章末状态如实列出来）。')
        act('✏️ 资源改名', self.open_asset_rename_dialog,
            '资源表改了名字之后，把脚本里的旧名字按词边界换掉。\n'
            '先看预览（哪些文件、哪几行）→ 确认后写盘（自动备份 .bak）→ 可一键回滚。')
        act('✎ 直接编辑单章', self._on_load,
            '不进剧情图，只把一章 .rpy 打开到卡片界面（改一章不想建图时用）')
        act('🔄 重读资源表', self.rescan_project,
            '改了 xlsx 之后重读资源表（下拉候选 / 缩略图）；剧情图不动')
        act('❓ 使用说明', self.show_help, '操作说明与快捷键（独立窗口，不占界面）')

        # —— 第二屏：卡片编辑 ——
        self._act_tb = tb_card
        # 「← 返回剧情结构」**不在这里**：卡片页顶部那条面包屑左边已经有
        # 一个同样的按钮（`_back_btn`），两个并排等于让人猜"有什么区别"。
        # 保留下面那个（它挨着面包屑，位置更贴近"我要回去哪"）。
        self._save_action = act('💾 保存这一拍', self.save_current_beat,
                                '把卡片改动提交到当前拍（Ctrl+S；'
                                'Ctrl+Shift+S = 直接导出 .rpy）', primary=True)
        # ⭐ 同一条理由（用户 2026-10-21）：导出太常用，紧跟「保存」，
        #   不排在「下一句台词」后面 —— 那条位置在窄窗口里会溢进「»」。
        act('⤓ 导出 .rpy', self.export_script,
            '生成/增量补丁写回（过安全门禁）\n'
            '与结构页顶部那个是同一个动作：两页都能直接导出',
            primary=True)
        self._undo_action = act('↩ 撤销修改', self.undo_current_beat,
                                '恢复成上一次保存时的样子（Ctrl+Z，只这一拍）')
        self._prev_action = act('◀ 上一拍', self.prev_beat, 'Alt+↑')
        self._pos = QLabel('— / —')
        self._pos.setProperty('role', 'caption')
        self._pos.setMinimumWidth(72)
        self._pos.setAlignment(Qt.AlignCenter)
        tb_card.addWidget(self._pos)
        self._next_action = act('下一拍 ▶', self.next_beat, 'Enter / Alt+↓')
        # ⭐ 「只改台词」快捷路径（用户 2026-09-28 要的，本轮补做）：
        #   一句一句跳，跳过没有台词的拍；**不藏列表**（藏了下标就与拍错位，
        #   那是"存错拍"级别的隐患）—— 见 `CardPageMixin.next_dialogue`。
        act('💬 下一句台词 ▶', self.next_dialogue,
            '跳到下一拍**有台词**的，并把光标放进台词框（快速过一遍台词用）')
        act('⤒ 从剧本表写回', self.export_from_table,
            '选一张演出表 .xlsx → 写回 .rpy（导出前会先给你看门禁结论）')
        act('🔄 刷新派生视图', self.refresh_stage_views,
            '只重算「舞台快照」「演出时间轴」两张只读表（不动 Beats 一格）。\n'
            '这两张是递推推导出来的，改了脚本/表格后不会自己变 —— '
            '表头会写着它是哪一刻生成的。')
        # ⚠ 这里**不走** `self.open_stage_preview`（S1 的 `window_export` 那份只传
        # frames）：工具栏这条要顺手把当前 beats 与"写回 at"入口交给预览窗口，
        # 见 `open_stage_preview_with_at`（P2，2026-10-30）。
        act('🎬 演出预览', partial(open_stage_preview_with_at, self),
            '按卡片效果"跑起来"\n'
            '预览窗口里能用「尺寸变换」把 at 写回这一拍（只改内存，导出时才写 .rpy）')
        # 真引擎（2026-10-29）：Pillow 预览负责"改一句看一眼"，它负责
        # "这一章真跑起来是什么样" —— 转场/ATL/自定义 UI/小游戏只有引擎准。
        self._engine_action = act(
            '🎮 引擎试玩', self.engine_preview_chapter,
            '用随工具的 Ren\'Py 引擎，从**这一章开头**真跑一遍\n'
            '（第一次要几秒准备镜像；关掉游戏窗口就回到工具）')
        self._engine_beat_action = act(
            '🎞 从这一拍试玩', self.engine_preview_from_beat,
            '用引擎直接落到**当前这一拍**（近似：跳过前情，'
            '前面几章设的变量没有值；先导出再试）')
        act('❓ 使用说明', self.show_help, '操作说明与快捷键（独立窗口，不占界面）')

        # —— 「🗂 资源映射」菜单 + 状态栏那一格（④⑥ 第二阶段）——
        # 放在**菜单栏**而不是工具栏：900px 窗口下工具栏已贴着宽度预算
        # （见 `_install_mapping_ui` 的说明）；而且映射与"在哪一页"无关。
        _install_mapping_ui(self)

    # ------------------------------------------------------------------
    #  页切换
    # ------------------------------------------------------------------

    def _set_page(self, index: int):
        """切页（两组工具栏跟着显隐；**不碰任何数据**）。"""
        index = self.PAGE_CARD if int(index) == self.PAGE_CARD else self.PAGE_GRAPH
        self._page = index
        try:
            self._stack.setCurrentIndex(index)
        except Exception:                    # noqa: BLE001
            swallow('main_window._set_page')
        # 顶部模式条跟着切（`blockSignals` 防"我改它 → 它回调我"的环）
        tabs = getattr(self, '_mode_tabs', None)
        if tabs is not None:
            try:
                if tabs.currentIndex() != index:
                    tabs.blockSignals(True)
                    tabs.setCurrentIndex(index)
                    tabs.blockSignals(False)
            except Exception:                # noqa: BLE001
                swallow('main_window._set_page.mode_tabs')
        for tb, want in ((getattr(self, '_tb_graph', None), index == self.PAGE_GRAPH),
                         (getattr(self, '_tb_card', None), index == self.PAGE_CARD)):
            if tb is not None:
                try:
                    tb.setVisible(want)
                except Exception:            # noqa: BLE001
                    swallow('main_window._set_page')
        self._sync_action_states()
        if index == self.PAGE_GRAPH:
            try:
                self._canvas.setFocus()
            except Exception:                # noqa: BLE001
                swallow('main_window._set_page')
        else:
            # 卡片页也要"焦点跟着页走"：否则焦点可能留在**已经隐藏**的画布/
            # 控制台上 —— 那时敲字没有任何反应（焦点在看不见的控件里），
            # 而空格这类键的归属判断（`_text_input_has_focus`）也会看错对象。
            self._focus_into_card_page()
        self._update_crumb()

    def _focus_into_card_page(self):
        """焦点不在卡片页里时，把它收到拍列表上（用户敲字前不用先点一下）。"""
        try:
            fw = QApplication.focusWidget()
            page = self._stack.currentWidget()
            if fw is not None and page is not None and self.isAncestorOf(fw) \
                    and (fw is page or page.isAncestorOf(fw)):
                return                       # 已经在当前页里，别抢用户的焦点
            self._list.setFocus()
        except Exception:                    # noqa: BLE001
            swallow('main_window._focus_into_card_page')

    def _sync_action_states(self):
        """
        卡片页专属的动作**只在卡片页可用**（工具栏不该出现"点了没用"的按钮）。

        结构页上卡片动作没有意义，所以置灰 —— 用户一眼就知道"这些键现在
        不管用"，比"按了没反应"友好。

        注意快捷键**不是**靠这里兜的：`QAction` 的快捷键不受"当前在哪一页"
        约束，所以本窗口**一律不给动作挂快捷键**（见 `_build_toolbars.act`），
        键盘只走 `eventFilter`（那里判页号）。
        `_update_nav` 负责 prev/next 的可用性，它同样按页判定。
        """
        on_card = self._page == self.PAGE_CARD
        for name in ('_save_action', '_undo_action'):
            act = getattr(self, name, None)
            if act is not None:
                try:
                    act.setEnabled(on_card)
                except Exception:            # noqa: BLE001
                    swallow('main_window._sync_action_states')
        self._update_nav()

    def _update_crumb(self):
        """
        面包屑：`文件名 › label`（+ 右边的 `第 i / n 拍`）。

        ⚠ 右边那句**只放"第几拍 + 这一拍在产物里几行"**（2026-10-23）：
        从前它后面还挂着一整句"改完按「💾 保存这一拍」，「← 返回剧情结构」
        回第一屏" —— 那句话把这一行的最小宽度顶到 1279px，1400px 窗口上
        最后一个按钮直接被裁掉。常驻的提示不该按"每次都要完整读一遍"排版：
        操作提示进 tooltip，位置信息留在行上。

        ⚠ 2026-10-30 晚：前面那个 `剧情结构 › ` **去掉了**（省 60px）。左边
        那颗按钮就写着「← 返回剧情结构」，面包屑再重复一遍纯属占宽 —— 而
        60px 恰好是 1200px 默认窗口下"6 颗结构按钮能不能排成一行"的分水岭
        （`.pip-tmp/plan/probe_band_rows.py`：1240 一行 / 1200 折行 → 去掉
        前缀后 1200 也能一行）。
        """
        if not hasattr(self, '_crumb'):
            return
        name = Path(self._file_path).name if self._file_path else ''
        label = str(getattr(self._session, 'focus_label', '') or '')
        n = len(self._session)
        i = self._loaded_idx
        if self._page == self.PAGE_GRAPH:
            self._crumb.setText('剧情结构')
            self._crumb_hint.setText(
                f'当前模块：{name}' if name else '（双击一个模块进卡片编辑器）')
            self._crumb_hint.setToolTip(self._crumb_hint.full_text())
        else:
            where = f'{name or "（未载入）"}'
            if label:
                where += f' › {label}'
            self._crumb.setText(where)
            pos = f'第 {i + 1 if i >= 0 else 0} / {n} 拍{self._beat_lines_hint(i)}'
            self._crumb_hint.setText(pos)
            self._crumb_hint.setToolTip(
                f'{pos}\n改完按「💾 保存这一拍」；「← 返回剧情结构」回第一屏。'
                '\n快捷键：← / → 翻拍，Ctrl+S 保存这一拍，'
                'Ctrl+Shift+S 直接导出 .rpy。')

    def _beat_lines_hint(self, idx: int) -> str:
        """
        当前这一拍在**产物**里占几行（`（产物 8 行）`；一行拍不显示）。

        用户 2026-10-21：一拍可能渲染成 7 行代码 + 1 句台词，而表里那些行
        都塞在「代码」那一格里 —— Excel 单元格只有一行高，看不出来。
        所以至少在这一拍被选中时说出来（一拍 ~0.3ms，不用缓存）。
        """
        try:
            if idx < 0 or idx >= len(self._session):
                return ''
            beat = self._session.beats[idx]
            n = len(self._session.rendered_lines_of(beat))
        except Exception as e:                    # noqa: BLE001 - 提示失败不影响导航
            swallow('main_window._beat_lines_hint', e)
            return ''
        return f'（产物 {n} 行）' if n > 1 else ''

    def _back_to_graph(self):
        """
        回第一屏。

        **先提交当前拍的卡片改动**（和翻页同一个规矩）：不提交的话，
        "在卡片里改了字 → 直接点返回"会静默丢掉这一拍的最后一次编辑。
        只提交到内存（会话），落盘仍然要按「⤓ 导出」。
        """
        if self._page == self.PAGE_CARD:
            self.apply_current_beat()
        self._set_page(self.PAGE_GRAPH)
        self._sync_graph_highlight()
        self._status('已回到剧情结构图（双击模块可再进卡片编辑器）。')

    def _open_selected_in_card(self):
        """工具栏「✎ 编辑这一章的卡片」：优先画布选中的模块。"""
        nid = self._canvas.selected_node_id()
        if nid is None and self._file_path and self.graph is not None:
            # 没选块：把当前会话那一章对应的模块找出来（同一文件多 label 时
            # 优先当前 label，别又回第一个）
            target = Path(self._file_path).resolve()
            focus = str(getattr(self._session, 'focus_label', '') or '')
            matches = [n for n in (self.graph.nodes or {}).values()
                       if getattr(n, 'file_path', None)
                       and Path(str(n.file_path)).resolve() == target]
            hit = next((n for n in matches
                        if str(getattr(n, 'label_name', '') or '') == focus),
                       matches[0] if matches else None)
            if hit is not None:
                nid = hit.id
        if nid is None:
            self._status('先在图上点一个模块（或双击它），再进卡片编辑器。')
            return
        self._on_node_open(nid)

    # ------------------------------------------------------------------
    #  剧情变量（双击模块 / 右栏按钮）
    # ------------------------------------------------------------------



    def _on_zoom_changed(self, percent):
        try:
            self._zoom_label.setText(f'{percent}%')
        except Exception:                    # noqa: BLE001
            swallow('main_window._on_zoom_changed')

    # ------------------------------------------------------------------
    #  结构分析（静态）
    # ------------------------------------------------------------------






    # ------------------------------------------------------------------
    #  资源表（xlsx）—— 与剧情图**无关**的那条路
    # ------------------------------------------------------------------




    # ------------------------------------------------------------------
    #  剧情图的范围：**只有用户导入过的章节**
    # ------------------------------------------------------------------



    # ------------------------------------------------------------------
    #  画布：保存 / 加载布局 + 导出控制流（旧版右栏那一组）
    # ------------------------------------------------------------------





    # ------------------------------------------------------------------
    #  更多工具：多引擎导出 / 资产统计 / 协作 / 校验
    # ------------------------------------------------------------------







    # ------------------------------------------------------------------
    #  图级撤销 / 重做（旧版 `history` + Ctrl+Z / Ctrl+Y）
    # ------------------------------------------------------------------
    #
    # 用户 2026-10-04："为何模块编辑器的功能缺失那么多？？？"
    # 旧版结构页有完整撤销栈（`graph.commands.HistoryManager` + 十来个
    # 命令），Qt 版**一个都没接** —— 于是删一个块只能靠重新导入。
    # 命令对象全在（`commands.py`），它们要的"视图接口"就下面这几个方法。











    @staticmethod
    def _is_text_widget(w) -> bool:
        """
        这个控件会不会**把空格当文字吃掉**？

        用户 2026-10-05：

        > 你这个拖拽画布的修复有一个bug……时灵时不灵

        根因就是这里判得太宽：以前任何 `QPlainTextEdit` / `QTextEdit` 都算
        "文本框"，而**控制台是只读的**（`setReadOnly(True)`）—— 用户在控制台
        里划一下选文字，焦点就落在它身上，之后"空格 + 左键拖"全都不平移了；
        点一下画布又好了。于是同一个动作"时灵时不灵"。

        只读控件**收不到输入的文字**，没有理由为它让路。所以这里加一道
        `isReadOnly()`：只读的文本控件按普通控件处理（空格照样能平移画布）。
        """
        if w is None:
            return False
        from PySide6.QtWidgets import (QAbstractSpinBox, QComboBox, QLineEdit,
                                       QPlainTextEdit, QTextEdit)
        if isinstance(w, (QLineEdit, QPlainTextEdit, QTextEdit)):
            try:
                if w.isReadOnly():
                    return False          # 只读：不会把空格当文字
            except AttributeError as e:
                swallow('main_window._is_text_widget', e)
            return True
        # 可编辑下拉的**内部行编辑**也会收到键事件
        if isinstance(w, QComboBox) and w.isEditable():
            return True
        return isinstance(w, QAbstractSpinBox)

    def _text_input_has_focus(self, obj=None) -> bool:
        """
        这次按键是不是落在文本框里？（是的话空格要照常输入，不能拿去平移）

        两个来源都要看：**事件接收者**（`obj`）与当前焦点控件。
        只看 `focusWidget()` 在无头环境下不可靠（窗口没有真实激活状态），
        而且事件本来就是发给接收者的 —— 它是最直接的信息。
        """
        try:
            if self._is_text_widget(obj):
                return True
            return self._is_text_widget(QApplication.focusWidget())
        except Exception as e:                     # noqa: BLE001
            swallow('ui_qt.main_window._text_input_has_focus', e)
            return False

    def _record_applied(self, command):
        """
        把**已经生效**的操作记进历史（不重复 execute）。

        用于"先走语义层（它会做保留/合并），再记一条可撤销记录"的场景 ——
        变量编辑就是：`session.set_node_variables` 会原位保留技术代码，
        不能改用命令去直接赋值，但撤销仍然要能用。
        """
        self._history.undo_stack.append(command)
        if len(self._history.undo_stack) > self._history.max_steps:
            self._history.undo_stack.pop(0)
        self._history.redo_stack.clear()
        self._sync_history_buttons()






    # ------------------------------------------------------------------
    #  载入单章 → 卡片（**不进图**）
    # ------------------------------------------------------------------



    # ------------------------------------------------------------------
    #  "静默失败"要看得见（见 `core.safeguard` 与不变量 58）
    # ------------------------------------------------------------------

    def _swallow_mark(self):
        """
        记一个"操作前"的基线，用来只看**本次操作**新增的静默失败。

        返回 `{context: count}`（不是总数）：同一批 173 张缩略图在后续操作里
        不会重复刷屏 —— 这是 212 条红字的一大半来源。
        """
        from storygraph.core import safeguard
        return safeguard.snapshot()

    def _report_swallowed(self, mark=0) -> int:
        """
        把本次操作**新产生**的静默失败收成一条摘要（没有就什么都不做）。

        控制台只写一行摘要，明细进诊断面板（分组、聚合、可复制）。
        用户 2026-10-22：
        > 一大片红字，我不知道该处理哪条。

        兼容老调用方：`mark` 是 int（总数）时按总数差判断"有没有新增"。
        """
        from storygraph.core import safeguard
        from storygraph.ui_qt.diagnostics import (category_of, summary_line)
        before = mark if isinstance(mark, dict) else None
        baseline = int(sum((before or {}).values())) if before is not None \
            else int(mark or 0)
        if safeguard.total() <= baseline:
            return 0
        if before is not None:
            now = safeguard.snapshot()
            lasts = {ctx: last for ctx, _n, last in safeguard.recent(1000)}
            items = [(ctx, now[ctx] - int(before.get(ctx, 0)),
                      lasts.get(ctx, ''))
                     for ctx in now if now[ctx] > int(before.get(ctx, 0))]
        else:
            items = safeguard.recent(1000)
        n = sum(int(count or 0) for _c, count, _l in items)
        if n <= 0:
            return 0
        self._diag_items = list(items)
        # 控制台只留摘要；具体 context 在详情里，避免同类刷屏。
        try:
            self._diag_text.setText('本次操作：' + summary_line(items))
            self._diag_bar.setVisible(True)
            code = sum(int(c or 0) for ctx, c, _l in items
                       if category_of(ctx) == 'code')
            self._diag_icon.setStyleSheet(
                f"color: {T.t('state.error' if code else 'state.warning')};"
                f" font-weight: bold;")
        except Exception as e:                  # noqa: BLE001
            swallow('main_window._report_swallowed.panel', e)
        head = '、'.join(
            f'{ctx} ×{count}' + (f'（{last}）' if last else '')
            for ctx, count, last in items[:3])
        if len(items) > 3:
            head += f' 等 {len(items)} 处'
        # 前缀保留 `[静默失败]`：老用户认得这个词，只是不再一条一行。
        self._log(f'[静默失败] 本次 {n} 次（{len(items)} 处）：{head}'
                  f' —— 点控制台上方「详情」按代码 / 环境 / 资源分类查看')
        self._status(f'⚠ 本次有 {n} 次失败被跳过 —— '
                     f'{summary_line(items)}（控制台上方「详情」）')
        return n





    # ------------------------------------------------------------------
    #  模块 ↔ 卡片
    # ------------------------------------------------------------------










    # ------------------------------------------------------------------
    #  增 / 删卡片
    # ------------------------------------------------------------------





    # ------------------------------------------------------------------
    #  卡片 ↔ 数据（全部委托给会话）
    # ------------------------------------------------------------------


    #: 预取范围：往后 2 拍（往前翻也给 1 拍）。
    PREFETCH_AHEAD, PREFETCH_BEHIND = 2, 1






    # ------------------------------------------------------------------
    #  导航（**纯导航**：翻页不碰别的拍）
    # ------------------------------------------------------------------







    # ------------------------------------------------------------------
    #  键盘
    # ------------------------------------------------------------------





    # ------------------------------------------------------------------


    #: 卡片上那三格 → 资产种类（选择器要用）
    ASSET_FIELD_KINDS = {'Scene': 'image', 'BGM': 'music', 'Voice': 'voice',
                         'SFX': 'sound'}



    # ------------------------------------------------------------------
    #  导出 / 体检 / 预览 / 说明
    # ------------------------------------------------------------------



    # ------------------------------------------------------------------
    #  剧本表（Excel 双向）
    # ------------------------------------------------------------------






    # ------------------------------------------------------------------




    def _status(self, text: str):
        try:
            self._status_bar.showMessage(str(text), 8000)
        except Exception:                       # noqa: BLE001
            swallow('main_window._status')

    def closeEvent(self, event):
        # **先处理"没保存的东西"再拆自己**（2026-10-22）：剧情图与这一章的拍
        # 都只存在内存里。关窗前问一句"保存 / 不保存 / 取消"——取消就真的不关
        # （`event.ignore`），一个字节都不动。没有可见窗口时（测试 / 嵌入）不弹框，
        # 图沿用自动保存、拍的改动没有安全落点只能放弃。
        try:
            keep_open = not self._confirm_close()
        except Exception as e:                      # noqa: BLE001
            swallow('main_window.closeEvent.confirm_close', e)
            keep_open = False
        if keep_open:
            event.ignore()
            return
        # ⚠ **先把记忆落下来再拆自己**（2026-10-13）：`save_session_state()`
        #   要读分栏尺寸、页号、当前章节 —— 那些都还活着的时候才读得到。
        #   放到下面那组"摘掉自己"之后就晚了（窗口在关，控件可能已经不可用）。
        #   失败只记台账：退出路径上绝不因为"没记住"而弹错、也绝不拦关闭。
        try:
            self.save_session_state()
        except Exception as e:                      # noqa: BLE001
            swallow('main_window.closeEvent.save_session', e)
        # 自己摘掉应用级事件过滤器：不摘的话，窗口关了过滤器还活着，
        # 之后（同一进程里的下一个窗口 / 测试用例）按键会被死人接走。
        for fn in (lambda: self._remove_shortcuts(),
                   lambda: self._loader.shutdown(),
                   lambda: self._panel.shutdown_stage_thumb(),
                   # 跨章继承的后台分析（整本约 2 秒）：关窗时别留一个线程在跑。
                   # ⚠ 它是**窗口**上的（`CardPageMixin`），不在 `self._panel`
                   # 那个卡片控件上 —— 写到 `_panel.` 上就是 AttributeError →
                   # 模态弹窗（`test_every_panel_method_the_window_calls_really_exists`）。
                   lambda: self.shutdown_stage_entry(),
                   # 关掉游戏窗口只是"这次预览结束"，工具关掉时引擎也必须停 ——
                   # 否则后台留一个全屏游戏进程，用户只会觉得"电脑变卡了"。
                   lambda: self.shutdown_engine_preview(),
                   lambda: self._notifier and self._notifier.close()):
            try:
                fn()
            except Exception:                   # noqa: BLE001
                swallow('main_window.closeEvent')
        super().closeEvent(event)



#: 迁移期别名（测试与 `main_qt` 都引过它）
ScriptTableEditorWindowQt = MainWindowQt


def open_editor(engine, script_path=None, project_root=None, graph=None, *,
                restore: bool = True):
    """
    建窗口并显示（**程序入口用这个**）。

    `restore=True`（默认）：**按记忆回到上次那个项目**（用户 2026-10-13：
    "当第二次打开时自动读取之前打开过的那个项目"）。它与命令行参数的关系是：

      · 命令行**给了**项目或章节 → 不恢复（那是明确指定，不能被"上次"盖掉）；
      · 什么都没给 → 恢复；`STORYGRAPH_NO_SESSION=1` 或记忆里
        `auto_open=false` 也能整趟关掉。

    ⚠ 恢复跑在窗口显示之后的第一个事件循环回合里（`_boot`），
    所以**不会拖慢第一屏**，也不会让"构造完就能用"的调用方看到半成品
    （测试与脚本大多直接 `MainWindowQt(...)`，那条路本来就不恢复）。
    """
    win = MainWindowQt(engine, script_path, project_root, graph)
    win.show()
    if not restore:
        return win
    if script_path or project_root:
        return win                      # 命令行说了算
    if getattr(win, '_restore_on_boot', True):
        # 恢复已经在 `_boot` 里排好了（`QTimer.singleShot(0, …)`）——
        # 这里不再催一次，免得同一个项目被读两遍资源表。
        pass
    return win
