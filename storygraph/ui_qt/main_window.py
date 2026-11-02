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

而且连"摆控件"也大半不在这里了（2026-11-01）：本文件从 4478 行降到 ~1200
行，前端按"一块一文件、依赖单向"重新分层：

    ui_qt/main_window.py        装配：主类 + 启动/页切换/日志/窗口状态
    ui_qt/window_*.py           十一个 mixin（各一块职责）
    ui_qt/window_graph_page.py  两页 + 两组工具栏的**构建**
    ui_qt/card_editor.py        卡片表单本体（独立 QWidget）
    ui_qt/card_actions.py       卡片上"按钮点下去干的事"（fn(win, req)）
    ui_qt/mapping_status.py     「🗂 资源映射」菜单 + 状态栏那一格

依赖方向是**单向**的：`main_window → window_graph_page → {card_editor,
card_actions, mapping_status}`。判据：

  · `tests/test_main_window_mixins.py`（主类 ≤35 个方法、每个成员都要认领、
    每个 mixin 的 `REQUIRES` 要如实）；
  · `tests/test_package_layering.py`（模块级不许有环）；
  · `tools/debt_report.py`（导入环 / 超大函数 / 超长文件的棘轮）。
"""

from __future__ import annotations

from pathlib import Path

from storygraph.core.safeguard import swallow

#: Ren'Py 台词 → 预览用 HTML 的转换器搬到 `ui_qt.text_html`（2026-11-01 解导入环）：
#: `stage_preview` 也要用**同一份**（卡片与预览不能各写一套），而
#: `stage_thumb` 顶层导入 `stage_preview`、本模块顶层导入 `stage_thumb` ——
#: 转换器放在这里就是 `main_window → stage_thumb → stage_preview → main_window`
#: 那个三模块环。它只依赖 `re`，搬到叶子后环断了；这里 re-export 老名字。
from storygraph.ui_qt.text_html import renpy_text_to_html  # noqa: F401 - 老调用点 re-export


from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QApplication, QComboBox, QLineEdit,
                               QMainWindow, QPlainTextEdit, QStackedWidget)

from storygraph.app.editor import EditorSession
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.status import AppStatusBar, Notifier, Toast
from storygraph.ui_qt.thumb_loader import ThumbLoader
from storygraph.ui_qt.wheel_guard import ensure_installed
# `MainWindowQt` 的十一块职责各住一个文件（P3-14，2026-10-05）。
# **基类顺序**：mixin 必须排在 `QMainWindow` **前面** ——
# `eventFilter` / `closeEvent` 是 Qt 虚函数（`QObject` 自己就定义了），
# 排在后面就会被它抢先、mixin 那份成死代码（实测 13 条键盘用例全红，
# 而 `hasattr(MainWindowQt, 'eventFilter')` 照样是 True）。
# 由 `tests/test_main_window_mixins.py` 守。
from storygraph.ui_qt.window_assets import WindowAssetsMixin
from storygraph.ui_qt.window_card import CardPageMixin
from storygraph.ui_qt.window_export import (  # noqa: F401 - 老调用点 re-export
    WindowExportMixin, overwritable as _overwritable)
from storygraph.ui_qt.window_graph_page import GraphPageMixin
# 卡片页的两个构建器（`_build_card_page` / `_build_toolbars`）连同
# `toggle_card_column` / `CardActionRow` / `CardColumnExpandFilter`
# 一起搬进 `window_graph_page.GraphPageMixin`（2026-11-01）：
# 主类那两个"方法位"就是留给它们的（预算 ≤35，见 mixins 守卫）。
# 这里 re-export，`MW.toggle_card_column` 之类的老写法（含测试）不变。
from storygraph.ui_qt.window_graph_page import (  # noqa: F401
    CardActionRow, CardColumnExpandFilter, toggle_card_column)
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


# 卡片上的那些"按钮点下去干的事"搬到 `ui_qt.card_actions`（2026-11-01）：
# 它们是 `fn(win, req)` 形态的模块级回调，不是主类的方法；搬走之后
# `window_graph_page` 才 import 得到它们（否则就是反向边）。
# 这里 re-export，`MW.apply_size_from_card` 之类的老写法（含测试）不变。
# ⚠ 要打桩 `_ask_scene_image` / `_confirm_merge_plan` 的判据请打在
# `storygraph.ui_qt.card_actions` 上 —— 函数读的是那里的名字。
from storygraph.ui_qt.card_actions import (  # noqa: F401 - 老调用点 re-export
    _after_preview_beat_edit,
    _ask_scene_image,
    _confirm_at_plan,
    _confirm_merge_plan,
    _plan_preview_at,
    _plan_scene_from_card,
    _plan_sg_from_card,
    _plan_size_from_card,
    _refresh_card_after_merge,
    _refresh_card_after_size,
    _screen_pair,
    apply_at_from_preview,
    apply_merge_from_panel,
    apply_scene_from_card,
    apply_size_from_card,
    notify_card_notice,
    open_mapping_from_card,
    open_stage_preview_with_at,
    pick_sprite_result,)
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
        self._base_title = '织言片语'
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
