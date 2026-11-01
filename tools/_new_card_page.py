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
