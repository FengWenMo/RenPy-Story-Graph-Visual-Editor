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
