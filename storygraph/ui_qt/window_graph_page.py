"""
`storygraph/ui_qt/window_graph_page.py`
======================================

**两页的构建 + 两组工具栏** —— `MainWindowQt` 的第八块（2026-11-01）。

为什么单开一块：主类成员受 `tests/test_main_window_mixins.py` 的两条约束 ——
「每个成员都要认领（`KEEP` 白名单）」和「主类自己定义的方法 ≤ 35」。上一轮
`BeatCardEditor._build` 拆完，主类只剩**一个方法位**，`_build_graph_page`
（293 行）就地拆成五个小方法就顶破"≤ 35"，所以拆不动。搬进 mixin 后那两条
只管主类 —— 本模块可以自由按"一段一屏"拆，这正是搬家要换来的东西。

**上一轮为什么只搬了第 0 页**：`_build_card_page`（268 行）和
`_build_toolbars`（227 行）要调 `BeatCardEditor` / `apply_size_from_card` /
`open_mapping_from_card` / `_install_mapping_ui` … 那些名字当时还住在
`main_window` 里，一起搬过来就是 `main_window ↔ window_graph_page` 的
模块级环。第 0 页**一个都不调**，所以先搬它。

**2026-11-01 这一轮把剩下两页也搬齐了**，因为那个环的两个断点都断了：

    ① `BeatCardEditor`（1559 行）→ `ui_qt/card_editor.py`
       它只依赖 main_window 的**一个**模块级名字 `refresh_mapping_status`；
    ② 「资源映射」那一块 → `ui_qt/mapping_status.py`（就是那个名字的家）。

于是本模块的依赖变成**单向**，全是叶子：

    main_window → window_graph_page → {card_editor, card_actions, mapping_status}

拆法：一长串 `addWidget` 按**屏**切开，每屏一个方法，编排只剩一串调用：

    _build_graph_page         ~40 行  编排 + 挂到 self 上
    ├ _graph_page_split               左右分栏（画布 / 右栏的比例）
    ├ _graph_page_canvas              画布 + 空图引导 + 缩放条
    ├ _graph_page_right_column        右栏竖排（控制台 / 当前模块 / 按钮组）
    └ _graph_page_wire_canvas         画布信号 → 宿主槽（最后接线）

    _build_card_page          ~30 行  编排（面包屑 / 中栏 / 右栏 / 分栏比例）
    ├ _card_page_breadcrumb            ← 返回 / 章节 / 那一排增删按钮
    ├ _card_page_left_column           章节下拉 + 拍列表 + 参与角色
    ├ _card_page_right_column          定位折叠 + 卡片（信号全接宿主）
    └ _card_page_splitter              三项尺寸 + 双击把手 = 展开/收起

    _build_toolbars           ~25 行  两组各建一条，再交给两个装配函数
    ├ _toolbar_graph                  第 0 页那 12 个动作
    └ _toolbar_card                   第 1 页那 20 个动作 + 映射菜单

搬家是**原样搬**（先整块切片、跑全绿，再拆），所以任何一步坏掉都能一眼
看出是"搬"还是"拆"造成的 —— 上一轮 `assets` 环清零吃的就是这个甜头。
"""
from __future__ import annotations

from functools import partial

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QListWidget, QPlainTextEdit, QPushButton,
                               QScrollArea, QSplitter, QToolBar, QToolButton,
                               QVBoxLayout, QWidget)

from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt.card_actions import (apply_merge_from_panel,
                                           apply_scene_from_card,
                                           apply_size_from_card,
                                           notify_card_notice,
                                           open_mapping_from_card,
                                           open_stage_preview_with_at,
                                           pick_sprite_result)
from storygraph.ui_qt.card_editor import BeatCardEditor
from storygraph.ui_qt.elide import ElidedLabel
from storygraph.ui_qt.flow import FlowRow
from storygraph.ui_qt.graph_canvas import GraphCanvas
from storygraph.ui_qt.inspector import PropertyStrip
from storygraph.ui_qt.mapping_status import _install_mapping_ui
from storygraph.ui_qt.shot_list import ShotDelegate
from storygraph.ui_qt.window_layout import (CARD_COLUMN_MIN_W,
                                            CARD_SPLIT_DEFAULT,
                                            CARD_SPLIT_EXPANDED_RATIO)


# ==================================================================
#  卡片页的两个"帮手"（2026-11-01 从 main_window 搬来）
# ==================================================================
# `toggle_card_column` 是模块级函数（不是主类方法）：主类成员受
# `tests/test_main_window_mixins.py` 的"每个成员都要认领"约束，
# 而它只操作窗口状态，不需要成为主类 API。`MW.toggle_card_column` 仍可用
# —— main_window 里 re-export 了。
#
# `CardActionRow` / `CardColumnExpandFilter` 是**卡片页**的控件与事件过滤器，
# 与它们的用户（`_build_card_page`）住在一起才不会越搬越远。


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


class GraphPageMixin:
    """
    **第 0 页**：剧情结构图那一屏（画布 + 右栏）。

    这里只做三件事：造控件、摆布局、把信号接到宿主已有的槽上。
    任何一条编辑语义都不在这里（在 `app.editor.EditorSession`）。
    """

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    #:
    #: 注意这份清单里**没有** `_canvas` / `_list` / `_inspector` 这些 ——
    #: 它们是本 mixin 自己 `self.X = …` 造的控件。判据和守卫一致：
    #: "自己造的"不算依赖，只有"别人造的、我只读"才算。
    REQUIRES = (
        # 宿主属性与子控件（别处造、这里读）
        '_edit_selected_vars', '_menu_action', '_on_canvas_select',
        '_on_connect_requested', '_on_edge_menu', '_on_node_menu',
        '_on_node_moved', '_on_zoom_changed', '_open_edge_in_card',
        '_open_selected_in_card', '_refresh_choices_now',
        '_show_more_tools', '_status',
        # 宿主已有的槽与动作（这里只连线，不实现）
        'add_node_at', 'check_asset_health', 'check_logic',
        'clear_console', 'copy_diagnostics', 'edit_edge',
        'edit_node_ops', 'export_control_flow',
        'lift_python_assignments', 'load_layout', 'redo_graph',
        'save_layout', 'show_analysis', 'show_diagnostics',
        'show_variable_workbench', 'undo_graph',
        # ---- 卡片页（`_build_card_page`，2026-11-01 搬进来之后新增）----
        # 这些都是宿主自己的会话 / 动作：卡片页只管把它们接到信号上。
        # 读 `_session` 的两处是"路径解析"与"章节下拉"，不是编辑语义。
        '_entry_of_beat', '_fill_speaker', '_back_to_graph', '_loader',
        '_on_label_changed', '_on_list_item_clicked', '_on_select',
        '_pick_asset_for', '_pick_image', '_session', '_toggle_location',
        'delete_card', 'insert_card', 'insert_choice_point',
        'insert_flow_card', 'restore_card', 'save_current_beat',
        'undo_current_beat',
        # ---- 工具栏（`_build_toolbars`，同一轮搬进来）----
        # 一条 QAction 一条槽：这些名字就是"点了会发生什么"，实现都在别的
        # mixin 里（`window_graph_ops` / `window_card` / `window_export` …）。
        '_on_load', '_toggle_nav_dock', 'add_edge', 'add_node',
        'arrange_by_file', 'cleanup_import_junk', 'clear_graph',
        'complete_related_chapters', 'engine_preview_chapter',
        'engine_preview_from_beat', 'engine_preview_selected',
        'export_from_table', 'export_script', 'import_chapters',
        'import_plain_text', 'learn_custom_statement', 'next_beat',
        'next_dialogue', 'open_ai_panel', 'open_asset_rename_dialog',
        'open_project', 'prev_beat', 'refresh_stage_views',
        'reload_imported_chapters', 'rescan_project',
        'show_analysis_dialog', 'show_continuity', 'show_health',
        'show_help',
    )

    def _build_graph_page(self) -> QWidget:
        """
        第一屏布局**照 tkinter 版**：画布占左边（伸展）+ 右侧一栏。

        右栏的内容也是照旧版的顺序（`setup_ui` 里就是"分析结果文本框在最上"）：

          ┌ 右上：控制台（导入 / 项目 / 体检 / 分析的消息都往这里写）┐
          │ 当前模块（选中哪个块）                                  │
          │ ✎ 打开本章剧本（改台词）  ← 旧版的主入口按钮            │
          │ 🧩 编辑剧情变量…  🔎 检查逻辑问题  📊 变量分析           │
          └────────────────────────────────────────────────────────┘

        用户 2026-10-03：
        > 我没有让你把控制台的界面给撤掉!至少右上角要保留一块，
        > 导入的时候没有反馈！根本不知道导入了什么……
        所以控制台在最上面，且**每条导入/加载都写进去**（见 `_log`）。

        左栏的"剧情树"已按用户要求删除：
        > 这个左栏"剧情树"是不需要的！因为很显然中间的舞台就已经导入了
        > chapter，一看就知道有哪些！这个左边的根本没有用……

        这一段只负责**编排**（2026-11-01 拆）：三块内容各是一个
        `_graph_page_*`。调用顺序就是右栏从上到下的可见顺序 —— 控制台
        （消息流）→ 当前模块与主入口 → 画布工具 → 更多工具。
        """
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                                 T.SPACE['sm'], T.SPACE['sm'])
        split = QSplitter(Qt.Horizontal)
        # ⚠ 存成实例属性：**会话记忆要摆回分栏宽度**（`window_state.py`）。
        #   以前只在局部变量里，退出时谁也拿不到它 —— 布局也就无从记起。
        self._split_graph = split
        outer.addWidget(split)

        mid = self._graph_page_canvas()
        right = self._graph_page_right_column()
        split.addWidget(mid)
        split.addWidget(right)

        # 右栏比上一版**更窄**（420 → 340）：用户要求给这一组按钮腾出位置，
        # 同时控制台仍然是这一栏里最大的一块（它要显示导入/分析的流水）。
        split.setSizes([1080, 340])
        try:
            # ⚠ 从前这里写的是 `left`（**没有这个变量**，左边那一栏叫 `mid`）——
            # NameError 被下面这个 `except Exception` 吞掉，于是"右栏最窄 300 /
            # 最宽 420"这两条约束**从来没生效过**（用户看到的右栏宽度不受控）。
            # 静态扫"引用了但本模块看不到的全局名"才把它抓出来。
            mid.setMinimumWidth(200)
            right.setMinimumWidth(300)
            right.setMaximumWidth(420)
        except Exception:                     # noqa: BLE001
            swallow('ui_qt.window_graph_page._build_graph_page')
        return page

    # ------------------------------------------------------------------
    #  左栏：画布
    # ------------------------------------------------------------------

    def _graph_page_canvas(self) -> QWidget:
        """
        左栏 = 画布（伸展）+ 一行缩放按钮 + 一行手势提示。

        画布那十来条 `connect` 是**旧版操作清单的逐条落地**（见
        `graph_canvas` 的契约表）：请求接线 / 节点操作 / 加节点 / 右键 /
        双击连边 / 双击连线进卡片 / 右键连边 / 选中 / 拖动 / 缩放 / 排完版。
        """
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(T.SPACE['xs'])
        self._canvas = GraphCanvas()
        # 画布信号 → 窗口（**操作照旧版**：见 graph_canvas 的契约表）
        self._canvas.connect_requested.connect(self._on_connect_requested)
        self._canvas.node_ops_requested.connect(self.edit_node_ops)
        self._canvas.node_add_requested.connect(self.add_node_at)
        self._canvas.node_menu_requested.connect(self._on_node_menu)
        # 双击一条边 → 打开这条线自己的属性（旧版「编辑连线」，2026-10-24）。
        # 「定位到这一拍」保留在右键菜单里。
        self._canvas.edge_edit_requested.connect(self.edit_edge)
        self._canvas.edge_open_requested.connect(self._open_edge_in_card)
        # 右键一条边 → 编辑 / 写入剧本 / 删除（P0，2026-10-24）
        self._canvas.edge_menu_requested.connect(self._on_edge_menu)
        self._canvas.selection_changed.connect(self._on_canvas_select)
        self._canvas.node_moved.connect(self._on_node_moved)
        self._canvas.zoom_changed.connect(self._on_zoom_changed)
        # 自动布局排完了 → 状态栏说一句"排成了几排"（2026-10-27：
        # 分排是按**当时舞台大小**算的，不说明白用户会以为排法乱变）。
        self._canvas.layout_arranged.connect(self._status)
        mv.addWidget(self._canvas, 1)
        mv.addLayout(self._graph_page_zoom_row())
        return mid

    def _graph_page_zoom_row(self) -> QHBoxLayout:
        """缩放四件套（➖ / 百分比 / ➕ / 1:1 / 适应窗口）+ 右侧手势提示。"""
        zrow = QHBoxLayout()
        zrow.setSpacing(T.SPACE['xs'])
        self._zoom_out_btn = QPushButton('➖')
        self._zoom_out_btn.setToolTip('缩小（滚轮）')
        self._zoom_out_btn.clicked.connect(self._canvas.zoom_out)
        zrow.addWidget(self._zoom_out_btn)
        self._zoom_label = QLabel('100%')
        self._zoom_label.setProperty('role', 'caption')
        self._zoom_label.setMinimumWidth(52)
        self._zoom_label.setAlignment(Qt.AlignCenter)
        zrow.addWidget(self._zoom_label)
        self._zoom_in_btn = QPushButton('➕')
        self._zoom_in_btn.setToolTip('放大（滚轮）')
        self._zoom_in_btn.clicked.connect(self._canvas.zoom_in)
        zrow.addWidget(self._zoom_in_btn)
        self._zoom_reset_btn = QPushButton('1:1')
        self._zoom_reset_btn.setToolTip('回到 100%')
        self._zoom_reset_btn.clicked.connect(self._canvas.zoom_reset)
        zrow.addWidget(self._zoom_reset_btn)
        self._fit_btn = QPushButton('适应窗口')
        self._fit_btn.clicked.connect(self._canvas.fit_to_content)
        zrow.addWidget(self._fit_btn)
        zrow.addStretch(1)
        self._graph_hint = QLabel(
            '实线 = jump · 虚线 = call'
            ' · 虚线 +「草图」= 草图'
            ' · Shift + 左键拖动 = 接线 · 双击连线 = 编辑'
            ' · 右键连线 = 编辑/写入/改落点/删除'
            ' · 左键选中/拖动模块 · 双击模块 = 编辑剧情变量'
            ' · 滚轮缩放 · 空格 + 左键拖动 = 平移')
        self._graph_hint.setProperty('role', 'caption')
        zrow.addWidget(self._graph_hint)
        return zrow

    # ------------------------------------------------------------------
    #  右栏
    # ------------------------------------------------------------------

    def _graph_page_right_column(self) -> QWidget:
        """右栏：控制台 → 当前模块与主入口 → 画布工具（从上到下就是可见顺序）。"""
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(T.SPACE['xs'])
        self._graph_page_console(rv)
        self._graph_page_actions(rv)
        self._graph_page_canvas_tools(rv)
        return right

    def _graph_page_console(self, rv: QVBoxLayout) -> None:
        """
        控制台标题行 + 诊断摘要 + 日志框（右栏最上面，也是最大的一块）。

        诊断摘要（2026-10-22，P1-5）：212 条同格式的 `[静默失败]` 不是
        "日志详细"，是把诊断成本丢给用户。这里只留一行：代码问题优先，
        资源/环境收进详情。
        """
        crow = QHBoxLayout()
        crow.setSpacing(T.SPACE['xs'])
        cap_con = QLabel('控制台（导入 / 项目 / 体检 / 分析）')
        cap_con.setProperty('role', 'section')
        crow.addWidget(cap_con, 1)
        self._console_clear_btn = QPushButton('清空')
        self._console_clear_btn.setToolTip('只清这块文字，不删任何图或文件')
        self._console_clear_btn.clicked.connect(self.clear_console)
        crow.addWidget(self._console_clear_btn, 0)
        rv.addLayout(crow)

        self._diag_bar = QFrame()
        self._diag_bar.setProperty('role', 'card')
        self._diag_bar.setVisible(False)
        drow = QHBoxLayout(self._diag_bar)
        drow.setContentsMargins(T.SPACE['xs'], 2, T.SPACE['xs'], 2)
        drow.setSpacing(T.SPACE['xs'])
        self._diag_icon = QLabel('⚠')
        drow.addWidget(self._diag_icon, 0)
        self._diag_text = QLabel('')
        self._diag_text.setWordWrap(True)
        drow.addWidget(self._diag_text, 1)
        self._diag_detail_btn = QPushButton('详情')
        self._diag_detail_btn.setToolTip('按"代码 / 环境 / 资源"分组查看')
        self._diag_detail_btn.clicked.connect(self.show_diagnostics)
        drow.addWidget(self._diag_detail_btn, 0)
        self._diag_copy_btn = QPushButton('复制诊断')
        self._diag_copy_btn.setToolTip('把摘要与明细复制给开发者')
        self._diag_copy_btn.clicked.connect(self.copy_diagnostics)
        drow.addWidget(self._diag_copy_btn, 0)
        rv.addWidget(self._diag_bar)
        #: 最近一次操作新增的诊断 `[(context, count, last), …]`
        self._diag_items: list = []

        self._console = QPlainTextEdit()
        self._console.setReadOnly(True)
        self._console.setFont(QFont(T.FONTS['mono'][0], T.FONTS['mono'][1]))
        self._console.setPlaceholderText(
            '导入章节 / 打开项目 / 体检 / 分析的结果都会写在这里。\n'
            '例：[导入] chapter1_3.rpy → 新增 1 个模块 / 0 条连线')
        self._console.setMinimumHeight(180)
        rv.addWidget(self._console, 1)

    def _graph_page_actions(self, rv: QVBoxLayout) -> None:
        """当前模块 + 四个主入口按钮 + 一行检查（可达性 / 变量 / 资源）。"""
        cap_mod = QLabel('当前模块')
        cap_mod.setProperty('role', 'section')
        rv.addWidget(cap_mod)

        self._chapter_label = QLabel('（在图上点一个模块）')
        self._chapter_label.setProperty('role', 'caption')
        self._chapter_label.setWordWrap(True)
        rv.addWidget(self._chapter_label)

        self._graph_page_entry_buttons(rv)
        rv.addLayout(self._graph_page_checks())

    def _graph_page_entry_buttons(self, rv: QVBoxLayout) -> None:
        """
        四个"进这一章"的入口：改台词 / 编辑剧情变量 / 变量工作台 / 搬出 python 块。

        ⚠ 「打开本章剧本」要过 `_menu_action` 安全壳（2026-10-26）：槽里异常
        只打 stderr → 用户看到的是"点了没反应"。无源节点修复入口就在这个按钮上。
        """
        self._open_script_btn = QPushButton('✎ 打开本章剧本（改台词）')
        self._open_script_btn.setToolTip(
            '进入卡片编辑器改这一章的台词（也可右键模块 → 改本章台词）')
        self._open_script_btn.setProperty('variant', 'primary')
        self._open_script_btn.clicked.connect(
            self._menu_action('打开本章剧本', self._open_selected_in_card))
        rv.addWidget(self._open_script_btn)

        self._edit_vars_btn = QPushButton('🧩 编辑剧情变量…')
        self._edit_vars_btn.setToolTip(
            '给策划 / 编剧编排剧情分支用的地方（双击模块同样打开）')
        self._edit_vars_btn.clicked.connect(
            self._menu_action('编辑剧情变量', self._edit_selected_vars))
        rv.addWidget(self._edit_vars_btn)

        self._vars_workbench_btn = QPushButton('📋 变量工作台…')
        self._vars_workbench_btn.setToolTip(
            '按变量列出初值、每条赋值和它所在的拍；可以只改一条、跳到那一拍')
        self._vars_workbench_btn.clicked.connect(
            self._menu_action('变量工作台',
                              lambda: self.show_variable_workbench()))
        rv.addWidget(self._vars_workbench_btn)

        # 2026-10-22：方向是"把赋值从 python 块里搬出来"，不是"让工具读懂块"。
        self._lift_btn = QPushButton('⤴ 搬出 python 块…')
        self._lift_btn.setToolTip(
            '把 `python:` 块里那些 `$` 就写得出来的单行赋值搬回脚本层：\n'
            '搬完它们会变成变量工作台里能逐条改的操作，导出也能进 IR；\n'
            '块里的调用 / 控制流原位保留，不动。')
        self._lift_btn.clicked.connect(
            self._menu_action('搬出 python 块里的赋值',
                              lambda: self.lift_python_assignments()))
        rv.addWidget(self._lift_btn)

    def _graph_page_checks(self) -> QHBoxLayout:
        """一行三个检查：逻辑问题 / 变量分析 / 资源体检（用户 2026-10-05 要求）。"""
        brow = QHBoxLayout()
        brow.setSpacing(T.SPACE['xs'])
        self._check_btn = QPushButton('🔎 检查逻辑问题')
        self._check_btn.setToolTip(
            '可达性 / 分支条件 / 入口判定；问题模块会在图上标红')
        self._check_btn.clicked.connect(self.check_logic)
        brow.addWidget(self._check_btn)
        self._analysis_btn = QPushButton('📊 变量分析')
        self._analysis_btn.setToolTip('逐节点的剧情变量与区间明细')
        self._analysis_btn.clicked.connect(self.show_analysis)
        brow.addWidget(self._analysis_btn)
        # 用户 2026-10-05："在「变量分析」右边加一个检查资源完整性的功能，
        # 后出示检查报告"。
        self._health_btn = QPushButton('📦 资源体检')
        self._health_btn.setToolTip(
            '检查资源表与项目资源是否一一对应（缺失 / 未登记 / 重复 / '
            '指向 game 之外），并出示报告')
        self._health_btn.clicked.connect(self.check_asset_health)
        brow.addWidget(self._health_btn)
        brow.addStretch(1)
        return brow

    def _graph_page_canvas_tools(self, rv: QVBoxLayout) -> None:
        """
        右栏下半：画布工具箱（旧版 `LabelFrame(text="画布")` 那一组）。

        用户 2026-10-04：
        > 我的旧版本有导出控制流图.rpy文件（label 末尾加 _control 的那个）…
        > 有保存布局.json！现在这个新版本呢？为何模块编辑器的功能缺失那么多？
        > （可以把右边的命令栏缩小点添加这两个东西）

        后端 `pipeline.renpy_exporter.export_to_renpy` 与旧版 JSON 格式的
        `LoadLayoutCommand` 都还在，缺的只是这三个入口。
        """
        cap_canvas = QLabel('画布')
        cap_canvas.setProperty('role', 'section')
        rv.addWidget(cap_canvas)

        c1 = QHBoxLayout()
        c1.setSpacing(T.SPACE['xs'])
        self._save_layout_btn = QPushButton('💾 保存布局(JSON)')
        self._save_layout_btn.setToolTip(
            '把手排好的块位置存成 .json（Ctrl+Shift+L）；格式与旧版一致')
        self._save_layout_btn.clicked.connect(lambda: self.save_layout())
        c1.addWidget(self._save_layout_btn)
        self._load_layout_btn = QPushButton('📂 加载布局')
        self._load_layout_btn.setToolTip('读回一份布局 .json（Ctrl+O）')
        self._load_layout_btn.clicked.connect(lambda: self.load_layout())
        c1.addWidget(self._load_layout_btn)
        rv.addLayout(c1)

        c2 = QHBoxLayout()
        c2.setSpacing(T.SPACE['xs'])
        self._undo_graph_btn = QPushButton('↩ 撤销')
        self._undo_graph_btn.setToolTip('撤销上一步图操作（Ctrl+Z）')
        self._undo_graph_btn.setEnabled(False)
        self._undo_graph_btn.clicked.connect(self.undo_graph)
        c2.addWidget(self._undo_graph_btn)
        self._redo_graph_btn = QPushButton('↪ 重做')
        self._redo_graph_btn.setToolTip('重做被撤销的图操作（Ctrl+Y）')
        self._redo_graph_btn.setEnabled(False)
        self._redo_graph_btn.clicked.connect(self.redo_graph)
        c2.addWidget(self._redo_graph_btn)
        rv.addLayout(c2)
        self._graph_page_more_tools(rv)

    def _graph_page_more_tools(self, rv: QVBoxLayout) -> None:
        """
        右栏最底下三件：导出控制流 / 刷新下拉候选 / 更多工具菜单。

        ⚠ 这里两处 `clicked.connect` 都要包 lambda（`export_control_flow` 一处）：
        `clicked` 会发一个 `bool`，直接 connect 会让它变成
        `export_control_flow(target=False)` —— "没给路径就弹「保存为」"那条
        分支走不到，接着拿 `False` 当路径写文件（TypeError）。
        """
        self._export_flow_btn = QPushButton('⤓ 导出控制流 .rpy')
        self._export_flow_btn.setToolTip(
            '按图生成 Ren\'Py 控制流脚本：每个模块一个 `<label>_control`，'
            '里面 call 原 label，再按连线写 jump/call/菜单/条件')
        self._export_flow_btn.clicked.connect(lambda: self.export_control_flow())
        rv.addWidget(self._export_flow_btn)
        self._refresh_assets_btn = QPushButton('🔄 刷新下拉候选')
        self._refresh_assets_btn.setToolTip(
            '资源表改过之后，重读背景 / 音乐 / 语音的候选（旧版同名按钮）')
        self._refresh_assets_btn.clicked.connect(self._refresh_choices_now)
        rv.addWidget(self._refresh_assets_btn)

        # —— 更多工具（次要但完整的入口都收在这里，右栏不被撑散）——
        #
        # 用户 2026-10-05："请确保所有的功能都接上UI"。
        # 逐条对照后，后端有、界面确实没有入口的是这三样：
        #   · 多引擎导出（`pipeline.engine_exporters`：Unity/Godot/JSON/CSV）
        #   · 资产 / 角色统计（`app.asset_panel` 的只读查询层）
        #   · 协作库（`collab.collab_store`：SQLite ↔ Excel）
        # 加上"校验当前章节"（`pipeline.validate` 三级校验，CLI 一直有）。
        self._more_btn = QPushButton('🧰 更多工具…')
        self._more_btn.setToolTip(
            '多引擎导出 / 资产与角色统计 / 协作库 / 校验当前章节')
        self._more_btn.clicked.connect(self._show_more_tools)
        rv.addWidget(self._more_btn)

    def _build_card_page(self) -> QWidget:
        """
        第 1 页（卡片编辑）的骨架：面包屑 + 三栏。

        三栏的顺序照参考界面（2026-10-23）：

            左 = 章节导航（**窗口级 dock**，不在这里）
            中 = **剧本**（拍列表：行号 + 类型牌 + 说话人）
            右 = 检查器（这一拍的属性 + 卡片表单）

        原来拍列表挤在最左边 260px、卡片占掉其余全部宽度 —— 那是"表单为主"
        的排法；改成"正文居中、属性在右"之后，读剧本时视线不用横穿整块表单。

        2026-11-01 按**屏**拆成四个（原来一整块 268 行）：面包屑 / 中栏 /
        右栏 / 分栏比例。这里只留编排 —— 调用顺序就是屏幕上的顺序。
        """
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                                 T.SPACE['sm'], T.SPACE['sm'])
        outer.setSpacing(T.SPACE['sm'])
        outer.addWidget(self._card_page_breadcrumb())
        # ⚠ 名字与下标（0 = 列表）**保持不变**：`window_state` 按它恢复宽度，
        #   改下标等于把用户存下来的宽度对错栏。
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._card_page_left_column())
        split.addWidget(self._card_page_right_column())
        self._card_page_splitter(split)
        outer.addWidget(split, 1)
        return page

    def _card_page_breadcrumb(self) -> QWidget:
        """
        顶部那一条：← 返回 + 现在在哪一章 + 增删卡片那一排。

        面包屑右边那句提示**必须是可以被压窄的**。2026-10-23 用户截图：这一行
        在 1680px 上把「↩ 撤销结构操作」「🗑 删除这张卡片」挤到窗口外 ——
        实测行最小 1279px、可用 1106px。元凶是普通 QLabel 的最小宽度 = 整句
        宽度（468px）；换成省略标签后最小 0，窗口再窄也只会把提示画成 `…`，
        按钮始终在。

        ⚠ 增删那六个按钮**必须包一层 lambda**（2026-10-05 实测，用户报"两个
        按钮一模一样"）：`QPushButton.clicked` 的签名是
        `clicked(bool checked = False)`，PySide 会把这个 bool **当成第一个位置
        参数**喂给槽函数 —— 直接 `connect(self.insert_card)` 等于每次都调
        `insert_card(after=False)`，也就是**"插到前面"**！于是「插入卡片」与
        「插到前面」效果完全相同（用户的原话）。

        为什么单测没抓到：`test_ui_qt` 里那两条是**直接调方法**
        （`window.insert_card(after=True)`），根本没经过信号 —— 缺陷在接线处，
        而用例绕开了接线（"测试绿了 ≠ 缺陷被钉住"）。现在有
        `test_every_button_slot_can_accept_the_clicked_bool` 守着这一类。
        """
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
        # 面包屑右边那句提示：**可以被压窄**（理由见上面 docstring）
        self._crumb_hint = ElidedLabel('', mode=Qt.ElideRight, role='caption')
        bh.addWidget(self._crumb_hint, 1)
        # —— 增 / 删卡片（结构编辑）——
        # 用户 2026-10-03："只能够修改，不能增删，还是没用！"
        #
        # 2026-10-30（用户第 5 条「UI 宽窄可变」）：这 5 个按钮合计约 570px，
        # 是**窗口最小宽 1142px 的主因**。第一版把它们收进「⋯ 结构」下拉菜单
        # 换宽度 —— 用户 2026-10-30 第 6 条直接否掉：**"不要那个结构！恢复回
        # 原来的 UI"**。菜单已整个删掉。
        #
        # 现在这两个诉求**同时**成立：按钮全在条上、顺序照旧；装进一个会折行的
        # `CardActionRow` —— 宽窗口下与从前**一模一样**（一行、右对齐），窗口窄
        # 到放不下时才折到第二行。**一颗都不藏、也不裁在窗口外**，而"最小宽 =
        # 最宽那一颗"（≈113px），窗口照样能缩到 700 出头。
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
        return bar

    def _card_page_left_column(self) -> QWidget:
        """
        中栏：章节下拉 + 剧本（拍列表）+ 参与角色。

        · **章节下拉**：同一个 `.rpy` 可以有很多 label。没有这一行时，用户点
          第二个 label 的节点只能看到文件第一拍（2026-10-22 实测报的）。
        · **拍列表**：多个 label 时列表里有"分组标题行"（不是拍）—— 点它折叠 /
          展开，不改变当前拍。
        · **剧本版画法**（2026-10-23）：**只换 delegate，不换控件** —— 模型、
          `Qt.UserRole` 里的拍号、选中态全是原样（几十条用例按 `item.text()` /
          `setCurrentRow` 判过）。`entry_of` 把拍号递回给会话。
        """
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.SPACE['xs'])
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
        self._list.itemClicked.connect(self._on_list_item_clicked)
        self._shot_delegate = ShotDelegate(
            self._list, entry_of=self._entry_of_beat)
        self._list.setItemDelegate(self._shot_delegate)
        self._list.setUniformItemSizes(False)
        self._list.setSpacing(0)
        # 横向滚动条**永久关掉**（2026-10-23）：行文字由 delegate 自己按视口宽度
        # 省略，横向滚动条只会多一条"看起来还有内容在右边"的假线索 —— 而且它
        # 一旦出现就会吃掉行高，列表底部那条细线就是它。
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
        return left

    def _card_page_right_column(self) -> QWidget:
        """
        右栏：定位（默认收起）+ 卡片（`BeatCardEditor`）。

        定位那 6~7 行（文件 / label / 第几拍 / 类型 / 源位置 / 说话人 / 这一章）
        是"我在哪儿"用的，改台词时不需要一直盯着；收起之后整块高度都还给卡片。
        它**不是消失了**：点一下标题就展开，内容一直是同步的（`_sync_inspector`
        照旧在翻拍时填）。顶部模式条右边也已经有「ch01.rpy · ch01 · 第 1 拍」
        那一句，收起来不会"不知道自己在哪"。

        卡片上那几条信号**直接连模块级回调**（`apply_size_from_card` 等），
        不经主类方法 —— 主类成员受 `tests/test_main_window_mixins.py` 的
        "每个成员都要认领"约束，而它们只是"点了要干的那件事"。
        """
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(T.SPACE['xs'])
        # —— 定位（**默认收起**，2026-10-27 用户点名）——
        #
        #   > 还有就是这个定位能不能折叠一下（卡片界面才是最主要的！！！定位
        #   > 默认折叠起来，卡片界面必须留几乎右边全屏）……留给我的可编辑
        #   > 窗口太小了！我看不过来！
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
            # 立绘网格这条路要"看得见判定"（④⑥）：多带 PickResult / 资源映射
            # 入口 / 拒绝通知（全是模块级函数的 partial）
            on_pick_image_result=partial(pick_sprite_result, self),
            on_open_mapping=partial(open_mapping_from_card, self),
            on_notice=partial(notify_card_notice, self))
        # 立绘缩略图的路径解析：真源是资源表的 Path 列，只有会话拿得到
        self._panel.set_path_resolver(self._session.path_of_image)
        # 卡片里那两个按钮直接连过来（用户要的"保存 / 撤销这张卡片"）
        self._panel.save_requested.connect(self.save_current_beat)
        self._panel.undo_requested.connect(self.undo_current_beat)
        # 「尺寸」一格的「改」：计划 / diff 确认 / 写回。
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
        return right

    def _card_page_splitter(self, split) -> None:
        """
        分栏比例 + 双击把手（与「⤢」同一个动作）。

        中栏要能读剧本（行号 + 类型牌 + 说话人），比原来 260 宽不少；右栏是
        "属性 + 表单"，不该吃掉 2/3 的宽度 —— 2026-10-23 用户截图：正文 500px、
        右栏 900px，读台词反而最挤。

        2026-10-30 再往窄拉一档（用户第 5 条"默认为窄"）：右栏 ≈ 420，读剧本的
        正文栏多留一些。要改大块内容时点「⤢」或双击分栏把手。
        """
        split.setSizes(list(CARD_SPLIT_DEFAULT))
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        handle = split.handle(1)
        if handle is not None:
            handle.setToolTip('拖动 = 调整卡片栏宽度\n双击 = 展开 / 收起卡片栏')
            self._card_handle_filter = CardColumnExpandFilter(self)
            handle.installEventFilter(self._card_handle_filter)
        self._card_col_expanded = False
        self._card_col_saved = list(CARD_SPLIT_DEFAULT)
        self._split_card = split          # 会话记忆要摆回它（`window_state`）

    def _build_toolbars(self):
        """
        两组工具栏（随页切换显隐）：第 0 页那 12 个 + 第 1 页那 20 个。

        这里只做**三件事**：造两条 `QToolBar`、装 `act()`、装窗口级的
        「▥ 章节导航」开关。两组动作各自一个装配函数（2026-11-01 拆，
        原来一整块 227 行）：

            _toolbar_graph(act)   第一屏：打开项目 / 自定义语句 / AI / 导入 / 导出 …
            _toolbar_card(act)    第二屏：保存这一拍 / 导出 / 上下拍 / 预览 / 试玩 …

        `act()` **必须留在这里的闭包里**（不能提成方法）：它读的是
        `self._act_tb` 这个"当前正在装哪条工具栏"的游标，而
        `tests/test_entry_points.py` 与 `tools/audit_qt_signal_slots.py`
        都是按 `act('文字', self.回调)` 这个**词法形态**扫的 —— 改成
        `self._act(...)` 两处扫描都会当场失效（含"信号 bool 被当成参数"那条守卫）。
        """
        tb_graph = QToolBar('剧情结构')
        # saveState 要对象名（否则 dock 状态存不下）
        tb_graph.setObjectName('pageBarGraph')
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

        self._toolbar_graph(act)
        self._toolbar_card(act)

        # —— 「🗂 资源映射」菜单 + 状态栏那一格（④⑥ 第二阶段）——
        # 放在**菜单栏**而不是工具栏：900px 窗口下工具栏已贴着宽度预算
        # （见 `_install_mapping_ui` 的说明）；而且映射与"在哪一页"无关。
        _install_mapping_ui(self)

    def _toolbar_graph(self, act) -> None:
        """
        第一屏：剧情结构。

        ⚠ 两条路**必须分开**（用户 2026-10-03 纠正）：

          · 「打开项目」= 资源表（xlsx）那条路：立绘/音乐候选、缩略图、参与
            角色。**它不往剧情图里放任何东西**。
          · 「导入章节」= 剧情图那条路：图里只有用户自己导入的章节。

        以前把两者写成一句话（"打开项目 → 建出结构图"），40 个脚本一次性灌进
        图里变成 51 个块 —— 用户："这还选个毛线啊！看都要看瞎了！"

        ⭐ 顺序是用户 2026-10-26 点名的（**从左到右**）：
             章节导航 → 打开项目 → 自定义模块 → AI 助手 →
             导入（章节 / 纯文字）→ 导出 → 刷新 → 清理
        判据 `tests/test_custom_statement_entry.py::test_the_toolbar_order_matches_the_user_spec`。
        """
        self._act_tb = self._tb_graph
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
        self._act_tb.addSeparator()
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
        self._act_tb.addSeparator()
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

    def _toolbar_card(self, act) -> None:
        """
        第二屏：卡片编辑。

        「← 返回剧情结构」**不在这里**：卡片页顶部那条面包屑左边已经有一个
        同样的按钮（`_back_btn`），两个并排等于让人猜"有什么区别"。
        """
        self._act_tb = self._tb_card
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
        self._act_tb.addWidget(self._pos)
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
