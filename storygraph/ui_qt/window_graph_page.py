"""
`storygraph/ui_qt/window_graph_page.py`
======================================

**第 0 页（剧情结构页）的构建** —— `MainWindowQt` 的第八块（2026-11-01）。

为什么单开一块：主类成员受 `tests/test_main_window_mixins.py` 的两条约束 ——
「每个成员都要认领（`KEEP` 白名单）」和「主类自己定义的方法 ≤ 35」。上一轮
`BeatCardEditor._build` 拆完，主类只剩**一个方法位**，`_build_graph_page`
（293 行）就地拆成五个小方法就顶破"≤ 35"，所以拆不动。搬进 mixin 后那两条
只管主类 —— 本模块可以自由按"一段一屏"拆，这正是搬家要换来的东西。

**为什么只搬了第 0 页**：`_build_card_page`（268 行）和 `_build_toolbars`
（227 行）各调了 10 / 2 个 `main_window` 的**模块级函数**
（`BeatCardEditor` / `apply_size_from_card` / `open_mapping_from_card` /
`_install_mapping_ui` …）。在那些名字还住在 `main_window` 里的时候把它们
一起搬过来，就成了 `main_window ↔ window_graph_page` 的模块级环。
第 0 页**一个都不调** —— 所以先把它搬干净，剩下两页等 `BeatCardEditor`
（1560 行，只依赖一个模块级名字）搬去 `ui_qt/card_editor.py` 之后再动。

拆法（2026-11-01 同一轮）：一长串 `addWidget` 按**屏**切开，每屏一个
`_graph_page_*`，编排只剩一串调用：

    _build_graph_page         ~40 行  编排 + 挂到 self 上
    ├ _graph_page_split               左右分栏（画布 / 右栏的比例）
    ├ _graph_page_canvas              画布 + 空图引导 + 缩放条
    ├ _graph_page_right_column        右栏竖排（控制台 / 当前模块 / 按钮组）
    └ _graph_page_wire_canvas         画布信号 → 宿主槽（最后接线）

搬家是**原样搬**（先整块切片、跑全绿，再拆），所以任何一步坏掉都能一眼
看出是"搬"还是"拆"造成的 —— 上一轮 `assets` 环清零吃的就是这个甜头。
"""
from __future__ import annotations

from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QListWidget, QPlainTextEdit, QPushButton,
                               QScrollArea, QSplitter, QToolBar, QToolButton,
                               QVBoxLayout, QWidget)

from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt.elide import ElidedLabel
from storygraph.ui_qt.graph_canvas import GraphCanvas
from storygraph.ui_qt.inspector import PropertyStrip
from storygraph.ui_qt.shot_list import ShotDelegate
from storygraph.ui_qt.window_layout import CARD_COLUMN_MIN_W, CARD_SPLIT_DEFAULT


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


