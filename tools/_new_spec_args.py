    # ---------- 语句参数那一组（`app.beats.spec_arg_*`） ----------
    def set_spec_args(self, columns, values=None, choices=None,
                      statement=None, loose=None):
        """
        按这一拍的语句重建「参数」那一组控件（没有参数 → 整组隐藏）。

        `columns`  = `app.beats.spec_arg_columns_of` 的产物
                     （`[{header, param, label, choices, positional, multi}]`）；
        `values`   = `{列名: 当前值}`（`app.beats.spec_arg_fields_for`）；
        `choices`  = `{参数名: [候选]}`（`app.beats.spec_arg_choices_for`）；
        `statement`= `{'name','label','head'}`（这一拍是哪条语句；空 = 台词拍）；
        `loose`    = 这一拍里工具没建模、但**会原样执行**的行（顶部那条提示用）。

        为什么整组重建而不是复用控件：参数名是**每条语句各不相同**的
        （`mycue` 有 `intensity`，`camera` 有 `layer`），复用就得处理
        "上一拍那几格现在叫什么"——重建简单且不可能残留别人的参数。

        2026-11-01 拆成七个 `_spec_*`：原来是 169 行 / 48 分支的一整块，
        现在这里是"清空 → 分组 → 一组一组建 → 回填 → 并进基线"五步。
        """
        values = dict(values or {})
        choices = dict(choices or {})
        self._loading = True
        try:
            self._clear_spec_form()
            groups = self._spec_groups(columns)
            multi = len(groups) > 1
            loose_lines = [str(x).strip() for x in (loose or ())
                           if str(x).strip()]
            stmt_name = str((statement or {}).get('name') or '').strip()
            stmt_head = str((statement or {}).get('head') or '').strip()
            for gname, gcols in groups:
                if multi:
                    self._add_spec_group_caption(
                        self._spec_caption(gname, stmt_name, stmt_head,
                                           loose_lines))
                for col in gcols:
                    self._add_spec_cell(col, values, choices)
            self._apply_spec_values(values)
            self._spec_host.setVisible(bool(self._spec_widgets))
        finally:
            self._loading = False
        self._set_statement(statement, loose)
        self._absorb_spec_baseline()
        self.refresh_dirty()

    def _clear_spec_form(self) -> None:
        """
        清空「参数」那一组：先拆控件、再删布局。

        顺序不能反：一条参数行是 `QHBoxLayout`（不是控件），
        `takeAt` 拿到的是"布局项"，控件得先 `setParent(None)`
        才真的从屏幕上消失 —— 直接 `deleteLater` 布局会留下一排孤儿输入框。
        """
        while self._spec_form.count():
            item = self._spec_form.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
                continue
            lay = item.layout()
            if lay is not None:
                while lay.count():
                    sub = lay.takeAt(0)
                    sw = sub.widget()
                    if sw is not None:
                        sw.setParent(None)
                lay.deleteLater()
        for header in list(getattr(self, '_spec_widgets', {}) or {}):
            self._widgets.pop(header, None)
        self._spec_widgets = {}
        self._spec_swatches = {}

    @staticmethod
    def _spec_groups(columns) -> list:
        """
        按"这一格属于哪条语句"分组（相邻同名的归一组）。

        为什么必须分组：一张拍可以同时带着好几条语句 —— 声明过的
        `mycue …`、它的块体、跟在后面的 `pause 1.0` / `with fade` /
        `window hide`。这些语句的参数列会一起长出来，从前**没有任何
        归属标记**，卡片上就是"内容 / 强度 / 秒数 / 转场 / 动作"一串，
        人根本不知道「秒数」改的是哪一行。一条语句一组、组前写明那一行
        （原文），才谈得上"自适应排版"。
        """
        groups: list = []
        for col in (columns or ()):
            key = str(col.get('name') or '')
            if not groups or groups[-1][0] != key:
                groups.append((key, []))
            groups[-1][1].append(col)
        return groups

    @staticmethod
    def _spec_caption(name: str, stmt_name: str, stmt_head: str,
                      loose_lines) -> str:
        """这一组的标题：优先用**源码里那一行**，其次才是语句名。"""
        if name and name == stmt_name and stmt_head:
            # 头行末尾的 `:` 属于"块开始"，当标题时去掉（少一点噪声）
            return (stmt_head[:-1].rstrip() if stmt_head.endswith(':')
                    else stmt_head)
        for line in loose_lines:
            if line.split(None, 1)[0:1] == [name]:
                return line
        return name

    def _add_spec_group_caption(self, caption: str) -> None:
        """组前那一行「◆ 原文」—— 多组同屏时才有，告诉人这几格改的是哪一行。"""
        cap = QLabel(f'◆ {caption}')
        cap.setProperty('role', 'caption')
        cap.setToolTip(f'下面这几格改的是这一行：{caption}')
        cap.setStyleSheet('margin-top:4px; font-weight:600;')
        self._spec_form.addWidget(cap)

    def _add_spec_cell(self, col, values, choices) -> None:
        """
        一格参数：标签 +（色块）+ 输入 +「⋯」+「可写多个」。

        这一格**没有 header 就整格跳过**：header 是"列名"（写进 Excel 的键），
        空 header 的列无处可存，建出来只会是个改了不生效的输入框。
        """
        header = str(col.get('header') or '')
        if not header:
            return
        box = QHBoxLayout()
        box.setSpacing(T.SPACE['xs'])
        box.addWidget(self._spec_cell_label(col, header), 0)
        opts = as_choices(choices.get(col.get('param'), ()) or ())
        value = str(values.get(header, '') or '')
        # 颜色值：先给色块，再给输入框（色块跟着输入实时变）
        sw = self._make_swatch(value)
        if sw is not None:
            self._spec_swatches[header] = sw
            box.addWidget(sw, 0)
        editor = self._spec_cell_editor(header, opts, sw)
        box.addWidget(editor, 1)
        if opts:
            box.addWidget(self._spec_cell_pick(header, editor, opts), 0)
        if col.get('multi'):
            box.addWidget(self._spec_multi_hint(), 0)
        self._spec_form.addLayout(box)

    def _spec_cell_label(self, col, header: str) -> QLabel:
        """
        这一格的标签 + tooltip。

        **这一格是位置参数还是具名参数**（2026-10-18）：两者在脚本里的写法
        不同（`se` vs `gain=-6`），而卡片上长得一模一样 —— 人改错了只会得到
        一条"参数过多"的报错。提示写进 tooltip，不占版面。
        """
        lab = QLabel(str(col.get('label') or col.get('param') or header))
        lab.setProperty('role', 'caption')
        lab.setMinimumWidth(72)
        pos = col.get('positional')
        if pos is None:
            lab.setToolTip(f'{header}（具名参数，脚本里写成 '
                           f'{col.get("param")}=值）')
        else:
            lab.setToolTip(
                f'{header}（位置参数：按顺序写，第 {int(pos) + 1} 位）')
        return lab

    def _spec_cell_editor(self, header: str, opts, sw):
        """
        输入控件：有候选 → 可编辑下拉；没有 → 普通输入框。

        两条都**可编辑**：候选只是提示，项目里临时用了新值也得能填进去
        （与 Excel 那套口径一致）。色块跟着输入实时变，且**不重建控件**
        —— 重建会把光标冲掉，人打字打到一半就跳走了。
        """
        if opts:
            w = QComboBox()
            w.setEditable(True)              # 手打新值照样能过（同 Excel）
            # 候选可能是人话（`1人，近处`），**选出来的是真值**
            fill_combo(w, opts, '', blank=True)
            w.setToolTip('从下拉里选，或直接写一个项目里在用的值'
                         '\n（候选里带人话名字时，悬停能看到引擎写法）')
        else:
            w = QLineEdit()
            w.setPlaceholderText('留空 = 不带这个参数')
        if sw is not None:
            sig = (w.editTextChanged if isinstance(w, QComboBox)
                   else w.textChanged)
            sig.connect(lambda text, s=sw: self._paint_swatch(s, text))
        self._spec_widgets[header] = w
        self._widgets[header] = w            # `_set_value` / `_get_value` 复用
        return w

    def _spec_cell_pick(self, header: str, editor, opts) -> QPushButton:
        """
        「⋯」= **可搜索**的候选列表（151 个变换靠下拉翻不动）。

        与资产那三格的「⋯」同一个形态、同一个 24px 尺寸。
        """
        pick = QPushButton('⋯')
        pick.setFixedSize(T.CONTROL['iconW'], T.CONTROL['iconW'])
        pick.setStyleSheet('padding:0;')
        pick.setToolTip(f'从候选里搜索挑选（共 {len(opts)} 个）')
        pick.setAccessibleName(f'挑选{header}')
        pick.clicked.connect(
            lambda _=False, h=header, ed=editor, items=opts:
            self._pick_spec_value(h, ed, items))
        return pick

    @staticmethod
    def _spec_multi_hint() -> QLabel:
        """
        "这一格能装好几个词"（多音轨 / 多声部）：**说出来**。

        不说的话人只会填一个，而另一个在脚本里其实也能写。
        """
        more = QLabel('可写多个（空格分隔）')
        more.setProperty('role', 'caption')
        more.setToolTip('例：`se se2` —— 这一格能连着写多个值')
        return more

    def _apply_spec_values(self, values) -> None:
        """建完控件再统一回填（下拉得先有候选项，`select_value` 才选得中）。"""
        for header, w in self._spec_widgets.items():
            value = str(values.get(header, '') or '')
            if isinstance(w, QComboBox):
                select_value(w, value)
            else:
                w.setText(value)

    def _absorb_spec_baseline(self) -> None:
        """
        把**这一拍的**参数值并进基线（上一拍留下的那些键先删掉）。

        ⚠⚠ **参数格是"载入"的一部分**（2026-10-18 修）：它们是在
        `load_fields` **之后**才建出来的（`_sync_spec_args`），于是
        `_loaded_fields` 里没有这几个键 → `is_modified()` 一上来就真 →
        卡片右下角写着"有未保存的改动"、💾 亮着（截图当场看到）。
        """
        if not self._loaded_fields:
            return
        for stale in [k for k in self._loaded_fields if is_dynamic_column(k)]:
            self._loaded_fields.pop(stale, None)
        self._loaded_fields.update(self._spec_values())

