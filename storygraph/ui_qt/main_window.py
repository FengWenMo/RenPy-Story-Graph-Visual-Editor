"""
storygraph.ui_qt.main_window
============================
Qt 版**主窗口**：块状编辑器（图视图）+ 卡片编辑器，**两个都要**。

用户 2026-10-01 明确：
> 我要改的是：tk做的一个块状编辑器 + 这个卡片界面！不是让你只剩一个卡片界面

所以这里不是"用卡片替换图视图"，而是把它们**并排**：

  ┌ 工具栏：打开项目 / 重新扫描 / 自动布局 / 载入单章 / 保存 / 导出 / 体检 / 预览 / 说明 ┐
  ├ 章节(拍) + 参与角色(左 dock) ┬ **块状编辑器（中央画布）** ┬ 卡片界面(右 dock) ┤
  └──────── 状态栏：当前章 · 拍号 · 是否已导出 ────────────────────────────────────┘

   · 双击一个块 = 在右边的卡片界面里打开**那一章**；
   · 右边的卡片界面改完 → 💾保存这一拍 → ⤓导出；
   · **操作说明全部在「说明」窗口**（工具栏 ❓ 打开，默认关闭，不占主界面）。

本文件只做**摆控件、读表单、写表单**三件事
------------------------------------------
全部编辑语义都在 `storygraph.app.editor.EditorSession` 里（深模块）：
载入 / 字段投影 / 立绘卡 / 写回 / 导出（含增量补丁与安全门禁）/ 体检 / 舞台帧。
这里**不再**出现任何 `beat.xxx = ` 赋值、也不再有 `self._panel._speaker`
这种"伸手进别人私有属性"的写法 —— 那些正是两套前端各写一套语义的根源。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDockWidget, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QToolBar, QVBoxLayout,
                               QWidget)

from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS, EditorSession
from storygraph.ui import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.graph_canvas import GraphCanvas
from storygraph.ui_qt.sprite_grid import SpriteGridWidget
from storygraph.ui_qt.status import AppStatusBar, Notifier, Toast
from storygraph.ui_qt.thumb_loader import ThumbLoader


class BeatCardEditor(QWidget):
    """
    一张**拍**的卡片：说话人 / 台词 / 立绘网格 / 背景 / 音乐 / 语音 / 备注
    + 可折叠高级区。

    它**不认识剧情语义** —— 只做 `load_fields(dict)` 与 `collect_fields()`。
    表情/位置是立绘网格上的 chip，不在这里。

    公共接口（窗口只用这些，不碰 `_` 开头的成员）：
        load_fields / collect_fields / sprite_cards / set_symbols
        set_speaker / speaker / set_text / text
    """

    def __init__(self, symbols=None, loader=None, parent=None,
                 on_pick_image=None):
        super().__init__(parent)
        self._symbols = symbols
        self._widgets: dict = {}
        self._adv_open = False
        self._on_pick_image = on_pick_image
        self._build(loader)

    # ---------- 界面 ----------

    def _section(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setProperty('role', 'section')
        return lab

    def _build(self, loader):
        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])

        # —— 说话人 ——（一行，右边留白，视觉上"标签 + 值"）
        row1 = QHBoxLayout()
        row1.setSpacing(T.SPACE['sm'])
        row1.addWidget(self._section('说话人'))
        self._speaker = QComboBox()
        self._speaker.setEditable(True)
        self._speaker.setMinimumWidth(160)
        self._speaker.setMaximumWidth(240)
        self._widgets['Speaker'] = self._speaker
        row1.addWidget(self._speaker, 0)
        row1.addStretch(1)
        root.addLayout(row1)

        # —— 台词（这张卡的主角，给足高度）——
        self._text = QPlainTextEdit()
        self._text.setPlaceholderText('这一拍要说的台词…')
        self._text.setFixedHeight(3 * 24 + 16)
        self._widgets['Text'] = self._text
        root.addWidget(self._text)

        # —— 立绘网格 ——
        head = QHBoxLayout()
        head.setSpacing(T.SPACE['xs'])
        head.addWidget(self._section('立绘'))
        hint = QLabel('点 chip 换表情/位置 · 双击卡换图')
        hint.setProperty('role', 'caption')
        head.addWidget(hint, 0)
        head.addStretch(1)
        root.addLayout(head)
        self._grid = SpriteGridWidget(symbols=self._symbols, loader=loader,
                                      on_pick_image=self._on_pick_image)
        root.addWidget(self._grid)

        # —— 背景 / 音乐 / 语音 ——
        row2 = QHBoxLayout()
        row2.setSpacing(T.SPACE['sm'])
        for name, label in (('Scene', '背景'), ('BGM', '音乐'), ('Voice', '语音')):
            box = QVBoxLayout()
            box.setSpacing(2)
            cap = QLabel(label)
            cap.setProperty('role', 'caption')
            box.addWidget(cap)
            combo = QComboBox()
            combo.setEditable(True)
            self._widgets[name] = combo
            box.addWidget(combo)
            row2.addLayout(box, 1)
        root.addLayout(row2)

        # —— 备注 ——
        row3 = QHBoxLayout()
        row3.setSpacing(T.SPACE['sm'])
        cap = QLabel('备注')
        cap.setProperty('role', 'caption')
        row3.addWidget(cap, 0)
        self._comment = QLineEdit()
        self._comment.setPlaceholderText('给同事/程序看的说明（不进脚本）')
        self._widgets['Comment'] = self._comment
        row3.addWidget(self._comment, 1)
        root.addLayout(row3)

        # —— 高级区（可折叠）——
        self._adv_btn = QPushButton(self._adv_label())
        self._adv_btn.setProperty('variant', 'chip')
        self._adv_btn.setFlat(True)
        self._adv_btn.setToolTip('代码块 / Label / 类型 / Notes / Stage'
                                 '（收起时这些字段不会被写回）')
        self._adv_btn.clicked.connect(self._toggle_adv)
        root.addWidget(self._adv_btn, 0, Qt.AlignLeft)

        self._adv_host = QWidget()
        adv = QVBoxLayout(self._adv_host)
        adv.setContentsMargins(0, 0, 0, 0)
        adv.setSpacing(T.SPACE['xs'])
        self._adv_host.setVisible(False)
        for name in ADVANCED_FIELDS:
            box = QHBoxLayout()
            box.setSpacing(T.SPACE['xs'])
            lab = QLabel(name)
            lab.setProperty('role', 'caption')
            lab.setMinimumWidth(72)
            box.addWidget(lab, 0)
            w = (QPlainTextEdit() if name in ('CodeBlock', 'Stage', 'Notes')
                 else QLineEdit())
            if isinstance(w, QPlainTextEdit):
                w.setFixedHeight(2 * 20 + 12)
            self._widgets[name] = w
            box.addWidget(w, 1)
            adv.addLayout(box)
        root.addWidget(self._adv_host)
        root.addStretch(1)

    def _adv_label(self) -> str:
        return (('▾' if self._adv_open else '▸')
                + ' 高级区（代码 / Label / 类型 / Notes / Stage）')

    def _toggle_adv(self):
        self._adv_open = not self._adv_open
        self._adv_host.setVisible(self._adv_open)
        self._adv_btn.setText(self._adv_label())

    # ---------- 数据 ----------

    def _set_value(self, name, value):
        w = self._widgets.get(name)
        if w is None:
            return
        value = str(value or '')
        if isinstance(w, QComboBox):
            w.setCurrentText(value)
        elif isinstance(w, QPlainTextEdit):
            w.setPlainText(value)
        elif isinstance(w, QLineEdit):
            w.setText(value)

    def _get_value(self, name) -> str:
        w = self._widgets.get(name)
        if w is None:
            return ''
        if isinstance(w, QComboBox):
            return (w.currentText() or '').strip()
        if isinstance(w, QPlainTextEdit):
            return w.toPlainText().strip()
        if isinstance(w, QLineEdit):
            return w.text().strip()
        return ''

    def set_symbols(self, symbols):
        self._symbols = symbols
        self._grid.set_symbols(symbols)

    def set_path_resolver(self, resolver):
        """注入"立绘 → 图片路径"解析器（路径真源是资源表，见 sprite_grid）。"""
        self._grid.set_path_resolver(resolver)

    # —— 公共访问器（窗口不再摸私有属性）——

    def speaker(self) -> str:
        return (self._speaker.currentText() or '').strip()

    def set_speaker(self, value):
        self._speaker.setCurrentText(str(value or ''))

    def set_speakers(self, values, current=''):
        """重填说话人下拉（不触发信号，避免把用户正在改的拍写脏）。"""
        self._speaker.blockSignals(True)
        self._speaker.clear()
        self._speaker.addItem('')
        for v in values or []:
            self._speaker.addItem(str(v))
        self._speaker.setCurrentText(str(current or ''))
        self._speaker.blockSignals(False)

    def text(self) -> str:
        return self._text.toPlainText().strip()

    def set_text(self, value):
        self._text.setPlainText(str(value or ''))

    def is_advanced_open(self) -> bool:
        return bool(self._adv_open)

    def sprite_cards(self) -> list:
        return self._grid.cards()

    def load_fields(self, fields: dict, sprite_items: list):
        for name in PRIMARY_FIELDS + ADVANCED_FIELDS:
            self._set_value(name, fields.get(name, ''))
        self._grid.load(sprite_items)

    def collect_fields(self) -> dict:
        out = {n: self._get_value(n) for n in PRIMARY_FIELDS}
        if self._adv_open:
            out.update({n: self._get_value(n) for n in ADVANCED_FIELDS})
        return out


class MainWindowQt(QMainWindow):
    """Qt 版主窗口：块状编辑器（中央）+ 卡片界面（右 dock）。"""

    def __init__(self, engine, script_path=None, project_root=None, graph=None):
        super().__init__()
        self.engine = engine
        self._project_root = project_root or getattr(engine, 'project_root', None)
        #: 全部编辑语义都在这个会话里（本窗口不自己实现任何一条）
        self._session = EditorSession(
            registry_provider=self._registry_provider,
            project_root=self._project_root)
        self._loader = ThumbLoader(self, max_size=(160, 220),
                                   project_root=self._project_root)
        self._notifier = None
        self.graph = graph
        self.setWindowTitle('剧情编辑器 · 块状编辑器 + 卡片界面')
        self.resize(1600, 950)
        self._build()
        #: 构造后**立刻**载入这一章（同步）。
        #: 不推迟到 QTimer 里 —— 调用方（测试、脚本、`open_editor`）
        #: 需要"构造完就能用"，否则得先跑一次事件循环才知道载没载上。
        self._pending_script = Path(script_path) if script_path else None
        if self._pending_script is not None:
            self.load_script(self._pending_script)
        QTimer.singleShot(0, self._boot)

    # ------------------------------------------------------------------
    #  会话 / 状态
    # ------------------------------------------------------------------

    def _registry_provider(self):
        try:
            return self.engine.get_registry()
        except Exception:                    # noqa: BLE001
            return None

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

    def _boot(self):
        if self.graph is not None:
            self._canvas.set_graph(self.graph)
        self._sync_nav()

    # ------------------------------------------------------------------
    #  界面
    # ------------------------------------------------------------------

    def _build(self):
        theme.apply_to(self)

        self._status_bar = AppStatusBar(self)
        self.setStatusBar(self._status_bar)

        tb = QToolBar()
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)

        def act(text, fn, tip=None, shortcut=None, primary=False):
            a = QAction(text, self)
            a.triggered.connect(fn)
            if tip:
                a.setToolTip(tip)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            tb.addAction(a)
            if primary:
                btn = tb.widgetForAction(a)
                if btn is not None:
                    btn.setProperty('variant', 'primary')
                    btn.style().unpolish(btn)
                    btn.style().polish(btn)
            return a

        act('📁 打开项目', self.open_project, '选择项目根（含 game/）→ 建出块状图')
        act('🔄 重新扫描', self.rescan_project, '重扫脚本与资产')
        act('➕ 新章节', self.add_node, '新建一个章节块（label 名校验过）')
        act('🔗 连线', self.add_edge, '在两个章节之间连 jump / call / 菜单')
        act('⤢ 自动布局', lambda: self._canvas.auto_layout(), '把块重新排好')
        tb.addSeparator()
        act('📂 载入单章', self._on_load, "在卡片界面里打开一章 .rpy")
        self._save_action = act('💾 保存这一拍', self.save_current_beat,
                                '把卡片改动提交到当前拍', 'Ctrl+S', primary=True)
        act('⤓ 导出 .rpy', self.export_script, '生成/增量补丁写回（过安全门禁）')
        act('🔍 项目体检', self.show_health, '只读检查，不写盘')
        act('📊 变量分析', self.show_analysis, '变量可达性 / 死代码（只读）')
        act('🎬 演出预览', self.open_stage_preview, '按卡片效果"跑起来"')
        tb.addSeparator()
        self._prev_action = act('◀ 上一拍', self.prev_beat, 'Alt+↑')
        self._pos = QLabel('— / —')
        self._pos.setProperty('role', 'caption')
        self._pos.setMinimumWidth(72)
        self._pos.setAlignment(Qt.AlignCenter)
        tb.addWidget(self._pos)
        self._next_action = act('下一拍 ▶', self.next_beat, 'Enter / Alt+↓')
        tb.addSeparator()
        act('❓ 使用说明', self.show_help, '操作说明与快捷键（独立窗口，不占界面）')

        # —— 中央：块状编辑器 ——（Toast 浮在它上面）
        self._canvas = GraphCanvas()
        self._canvas.node_double_clicked.connect(self._on_node_open)
        self._canvas.node_menu_requested.connect(self._on_node_menu)
        self.setCentralWidget(self._canvas)
        self._toast = Toast(self._canvas)
        self._notifier = Notifier(self._status_bar, self._toast).subscribe()

        # —— 左 dock：章节（拍）/ 参与角色 ——
        dock = QDockWidget('章节（拍） / 参与角色', self)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                               T.SPACE['sm'], T.SPACE['sm'])
        lay.setSpacing(T.SPACE['xs'])
        cap1 = QLabel('章节（拍）')
        cap1.setProperty('role', 'section')
        lay.addWidget(cap1)
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_select)
        lay.addWidget(self._list, 1)
        cap2 = QLabel('参与角色（双击填进这一拍）')
        cap2.setProperty('role', 'caption')
        lay.addWidget(cap2)
        self._who = QListWidget()
        self._who.setFixedHeight(120)
        self._who.itemDoubleClicked.connect(self._fill_speaker)
        lay.addWidget(self._who, 0)
        dock.setWidget(left)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.resizeDocks([dock], [260], Qt.Horizontal)

        # —— 右 dock：卡片界面 ——
        card_dock = QDockWidget('卡片界面（这一拍）', self)
        card_dock.setFeatures(QDockWidget.DockWidgetMovable)
        self._panel = BeatCardEditor(symbols=None, loader=self._loader,
                                     on_pick_image=self._pick_image)
        # 立绘缩略图的路径解析：真源是资源表的 Path 列，只有会话拿得到
        self._panel.set_path_resolver(self._session.path_of_image)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._panel)
        card_dock.setWidget(scroll)
        self.addDockWidget(Qt.RightDockWidgetArea, card_dock)
        self.resizeDocks([card_dock], [440], Qt.Horizontal)

        self._status('从「📁 打开项目」开始，或直接「📂 载入单章」'
                     '（❓ 使用说明随时可查）')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            self._toast._reposition()
        except Exception:                    # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    #  项目（块状编辑器的数据来源）
    # ------------------------------------------------------------------

    def open_project(self):
        start = str(self._project_root or Path.cwd())
        root = QFileDialog.getExistingDirectory(
            self, '选择项目根目录（含 game/）', start)
        if not root:
            return
        self._project_root = Path(root)
        self._session.project_root = self._project_root
        self._loader.project_root = self._project_root
        self.rescan_project()

    def rescan_project(self):
        """
        扫全项目：**一次解析同时拿到资产表与图**。

        解析这件事本身也归后端（`pipeline.build`），窗口只管把结果塞进画布。
        """
        if not self._project_root:
            QMessageBox.information(self, '提示', '先选择项目根目录。')
            return
        try:
            from storygraph.parsing.discover import discover_scripts
            from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
            from storygraph.story.beat_build import apply_script_definitions
            files = discover_scripts(self._project_root)
            analyzer = IncrementalRenPyAnalyzer()
            parse_result, graph = analyzer.analyze(files=files, graph=None)
            reg = self._registry_provider()
            if reg is not None:
                apply_script_definitions(reg, parse_result)
            self._session.refresh_symbols()
            self._panel.set_symbols(self._session.symbols)
            self.graph = graph
            self._canvas.set_graph(graph)
            n_node = len(getattr(graph, 'nodes', {}) or {})
            n_edge = len(getattr(graph, 'edges', {}) or {})
            self._status(f'已扫描 {len(files)} 个脚本 → 块 {n_node} 个 / '
                         f'连线 {n_edge} 条（双击块在这一章打开卡片）')
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '扫描失败', str(e))

    # ------------------------------------------------------------------
    #  载入单章 → 卡片
    # ------------------------------------------------------------------

    def _on_load(self):
        start = str(Path(self._project_root or '.') / 'game')
        path, _ = QFileDialog.getOpenFileName(
            self, '载入 .rpy', start, "Ren'Py 脚本 (*.rpy)")
        if path:
            self.load_script(path)

    def load_script(self, path=None):
        """载入一章（**语义全在 EditorSession.load**）。"""
        target = path or self._session.script_path
        if not target:
            return
        try:
            n = self._session.load(target)
            self._panel.set_symbols(self._session.symbols)
            self._reload_list()
            self._show_index(0)
            self._sync_graph_highlight()
            self._status(f'已载入 {Path(target).name}：{n} 拍')
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '载入失败', str(e))

    def _sync_graph_highlight(self):
        """卡片界面正在编辑的这一章 → 图上对应块标成"当前"。"""
        if self.graph is None or not self._session.script_path:
            return
        target = Path(self._session.script_path).resolve()
        for node in (self.graph.nodes or {}).values():
            fp = getattr(node, 'file_path', None)
            if fp and Path(str(fp)).resolve() == target:
                self._canvas.highlight_node(node.id)
                return

    def _reload_list(self):
        self._list.blockSignals(True)
        self._list.clear()
        for i in range(len(self._session)):
            e = self._session.list_entry(i)
            item = QListWidgetItem(
                f"{i + 1:>3} {e['prefix']}{e['who']}：{e['text'][:24]}")
            item.setData(Qt.UserRole, i)
            self._list.addItem(item)
        self._list.blockSignals(False)

        self._who.clear()
        for cid in self._session.participants():
            self._who.addItem(QListWidgetItem(cid))

    # ------------------------------------------------------------------
    #  块 ↔ 卡片
    # ------------------------------------------------------------------

    def _on_node_open(self, nid):
        """双击一个块 = 在卡片界面里打开这一章。"""
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        if node is None:
            return
        fp = getattr(node, 'file_path', None)
        if not fp or not Path(str(fp)).exists():
            self._status(f'「{node.label_name}」没有关联到源文件，'
                         f'先扫一次项目（🔄 重新扫描）。')
            return
        self.load_script(fp)
        self._canvas.highlight_node(nid)

    def _on_node_menu(self, nid, global_pos):
        menu = QMenu(self)
        if nid is not None:
            node = (self.graph.nodes or {}).get(nid)
            name = getattr(node, 'label_name', '') if node else ''
            menu.addAction(f'在卡片界面里打开「{name}」',
                           lambda: self._on_node_open(nid))
            menu.addAction('设为当前章节',
                           lambda: self._canvas.highlight_node(nid))
            menu.addSeparator()
            ops = len(getattr(node, 'operations', []) or []) if node else 0
            menu.addAction(f'编辑变量操作…（{ops} 条）',
                           lambda: self.edit_node_ops(nid))
            menu.addAction('从这里连一条到…', lambda: self.add_edge(from_id=nid))
            menu.addSeparator()
            menu.addAction('删除这个块…', lambda: self._delete_node(nid))
        else:
            menu.addAction('添加新章节…', self.add_node)
            menu.addAction('添加连线…', self.add_edge)
            menu.addSeparator()
            menu.addAction('自动布局', lambda: self._canvas.auto_layout())
            menu.addAction('适应窗口', lambda: self._canvas.fit_to_content())
            menu.addAction('重新扫描项目', self.rescan_project)
        menu.exec(global_pos)

    def add_node(self):
        """新建一个章节块（**校验在对话框里**，不合法就地提示）。"""
        from storygraph.ui_qt.dialogs import AddNodeDialog
        existing = [getattr(n, 'label_name', '')
                    for n in ((self.graph.nodes or {}).values()
                              if self.graph else [])]
        dlg = AddNodeDialog(self, existing=existing)
        if dlg.exec() != dlg.Accepted:
            return
        if self.graph is None:
            from storygraph.graph.models import Graph
            self.graph = Graph()
            self._canvas.set_graph(self.graph)
        # 放在视野中央，免得新建的块跑到很远的地方
        center = self._canvas.mapToScene(self._canvas.viewport().rect().center())
        nid = self.graph.add_node(dlg.name, center.x(), center.y(),
                                  operations=([dlg.operations]
                                              if dlg.operations else None))
        self._canvas.set_graph(self.graph)
        self._canvas.highlight_node(nid)
        self._status(f'已新建章节「{dlg.name}」（在内存里；'
                     f'点「⤓ 导出」才会进脚本）。')

    def edit_node_ops(self, nid):
        """编辑一个章节块的变量操作。"""
        from storygraph.ui_qt.dialogs import NodeOpsDialog
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        if node is None:
            return
        dlg = NodeOpsDialog(self, label_name=getattr(node, 'label_name', ''),
                            operations=node.operations)
        if dlg.exec() != dlg.Accepted:
            return
        node.operations = list(dlg.operations)
        self._canvas.set_graph(self.graph)
        self._status(f'「{node.label_name}」的变量操作已更新'
                     f'（{len(node.operations)} 条）。')

    def add_edge(self, from_id=None):
        """加一条连线（自环与不可能条件就地挡住）。"""
        from storygraph.ui_qt.dialogs import AddEdgeDialog
        if self.graph is None or len(self.graph.nodes or {}) < 2:
            self._status('至少要有两个章节才能连线（先扫描项目或新建章节）。')
            return
        if from_id is None:
            from_id = self._canvas.selected_node_id()
        dlg = AddEdgeDialog(self, nodes=self.graph.nodes, from_id=from_id)
        if dlg.exec() != dlg.Accepted:
            return
        self.graph.add_edge(dlg.from_id, dlg.to_id, etype=dlg.etype,
                            condition=dlg.condition,
                            option_text=dlg.option_text)
        self._canvas.set_graph(self.graph)
        self._status(f'已连线 {dlg.from_id} → {dlg.to_id}（{dlg.etype}）。')

    def delete_edge(self, eid):
        if self.graph is None or eid not in (self.graph.edges or {}):
            return
        self.graph.remove_edge(eid)
        self._canvas.set_graph(self.graph)
        self._status('已删除这条连线。')

    def show_analysis(self):
        """变量可达性 / 死代码分析（只读窗口，可过滤可复制）。"""
        if self.graph is None or not (self.graph.nodes or {}):
            self._status('先扫描项目（🔄 重新扫描）再分析。')
            return
        from storygraph.ui_qt.dialogs import AnalysisDialog
        try:
            text = self._session.analyze_graph(self.graph)
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '分析失败', str(e))
            return
        AnalysisDialog(self, '变量与可达性分析', text).exec()

    def _delete_node(self, nid):
        if self.graph is None:
            return
        node = self.graph.nodes.get(nid)
        if node is None:
            return
        if QMessageBox.question(
                self, '删除块',
                f'删除「{node.label_name}」及其连线？（只改内存里的图，不动文件）'
        ) != QMessageBox.Yes:
            return
        self.graph.remove_node(nid)
        self._canvas.set_graph(self.graph)

    # ------------------------------------------------------------------
    #  卡片 ↔ 数据（全部委托给会话）
    # ------------------------------------------------------------------

    def _show_index(self, idx: int):
        if not self._session.has(idx):
            self._idx = -1
            self._update_nav()
            return
        self._idx = idx
        self._panel.load_fields(self._session.fields(idx),
                                self._session.cards(idx))
        self._panel.set_speakers(self._session.characters(),
                                 self._session.beats[idx].speaker or '')

        self._list.blockSignals(True)
        self._list.setCurrentRow(idx)
        self._list.blockSignals(False)
        self._status_bar.set_dirty(False, self._file_path)
        self._update_nav()

    def apply_current_beat(self):
        """表单 → 当前拍（语义在 `EditorSession.apply`，这里只递数据）。"""
        if not self._session.has(self._loaded_idx):
            return None
        return self._session.apply(
            self._loaded_idx,
            self._panel.collect_fields(),
            self._panel.sprite_cards(),
            advanced_open=self._panel.is_advanced_open())

    def save_current_beat(self):
        if not self._session.has(self._loaded_idx):
            self._status('还没有载入章节，没什么可保存。')
            return
        b = self.apply_current_beat()
        line_id = getattr(b, 'line_id', '') or f'第 {self._loaded_idx + 1} 拍'
        self._status_bar.set_dirty(True, self._file_path)
        self._status(f'✔ 已保存到这一拍（{line_id}）。'
                     f'改动在内存里，点「⤓ 导出 .rpy」时才落盘。')

    # ------------------------------------------------------------------
    #  导航（**纯导航**：翻页不碰别的拍）
    # ------------------------------------------------------------------

    def _update_nav(self):
        n = len(self._session)
        i = self._loaded_idx
        self._prev_action.setEnabled(i > 0)
        self._next_action.setEnabled(0 <= i < n - 1)
        self._pos.setText(f'{i + 1 if i >= 0 else 0} / {n}')

    def _sync_nav(self):
        if hasattr(self, '_prev_action'):
            self._update_nav()

    def _on_select(self, row: int):
        if row >= 0 and row != self._loaded_idx:
            self.apply_current_beat()
            self._show_index(row)

    def next_beat(self):
        if self._session.has(self._loaded_idx):
            self.apply_current_beat()
            self._show_index(self._loaded_idx + 1)

    def prev_beat(self):
        if self._session.has(self._loaded_idx):
            self.apply_current_beat()
            self._show_index(self._loaded_idx - 1)

    def _fill_speaker(self, item):
        cid = item.text() if item else ''
        if not cid or not self._session.has(self._loaded_idx):
            return
        self._session.set_speaker(self._loaded_idx, cid)
        self._panel.set_speaker(cid)
        self._status_bar.set_dirty(True, self._file_path)
        self._status(f'已把「{cid}」填进这一拍的说话人。')

    # ------------------------------------------------------------------
    #  键盘
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.KeyPress:
            key = event.key()
            mods = event.modifiers()
            if key == Qt.Key_S and mods & Qt.ControlModifier:
                self.save_current_beat()
                return True
            # **回车 = 下一拍**（用户明确："回车是下一张卡片的意思"）
            if key in (Qt.Key_Return, Qt.Key_Enter) and not (mods & Qt.ShiftModifier):
                self.next_beat()
                return True
            # 翻页优先用 Alt+↑/↓：多行台词框里的 ↑/↓ 必须留给移动光标
            if mods & Qt.AltModifier:
                if key == Qt.Key_Down:
                    self.next_beat()
                    return True
                if key == Qt.Key_Up:
                    self.prev_beat()
                    return True
            fw = self.focusWidget()
            if key == Qt.Key_Down:
                if isinstance(fw, QPlainTextEdit) and not self._caret_at_edge(fw, True):
                    return False
                self.next_beat()
                return True
            if key == Qt.Key_Up:
                if isinstance(fw, QPlainTextEdit) and not self._caret_at_edge(fw, False):
                    return False
                self.prev_beat()
                return True
        return super().eventFilter(obj, event)

    @staticmethod
    def _caret_at_edge(edit, down: bool) -> bool:
        try:
            cur = edit.textCursor().blockNumber()
            return (cur >= edit.blockCount() - 1) if down else cur <= 0
        except Exception:                       # noqa: BLE001
            return True

    # ------------------------------------------------------------------

    def _pick_image(self, current: str) -> str:
        """挑一张立绘图（走 Qt 版资产选择器）。取消返回 ''。"""
        from storygraph.ui_qt.asset_picker import pick_image
        return pick_image(self, self._registry_provider(), current=current,
                          project_root=self._project_root) or ''

    # ------------------------------------------------------------------
    #  导出 / 体检 / 预览 / 说明
    # ------------------------------------------------------------------

    def export_script(self):
        """导出这一章（编译 + 增量补丁 + 安全门禁，全在会话里）。"""
        if not len(self._session):
            self._status('没有可导出的内容。')
            return
        self.apply_current_beat()
        try:
            self._session.compile()
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '编译失败', str(e))
            return

        default = self._session.default_output_path()
        try:
            default.parent.mkdir(parents=True, exist_ok=True)
        except Exception:                       # noqa: BLE001
            pass
        path, _ = QFileDialog.getSaveFileName(self, '导出 .rpy',
                                              str(default), "Ren'Py (*.rpy)")
        if not path:
            return
        try:
            res = self._session.export(path)
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '导出失败', str(e))
            return
        if not res.get('ok'):
            blocking = res.get('blocking') or ['（未知原因）']
            QMessageBox.warning(self, '生成被门禁拦截', '\n'.join(blocking))
            return
        self._status_bar.set_dirty(False, str(res.get('path') or path))
        todos = res.get('todos') or 0
        warn = f'，缺资源 {todos} 处（见 TODO）' if todos else ''
        self._status(f'✔ 已导出 {Path(path).name}（{res.get("note", "")}{warn}）')

    def show_health(self):
        if not self._project_root:
            self._status('先打开一个项目。')
            return
        try:
            r = self._session.health(limit=30)
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '体检失败', str(e))
            return
        if r.get('error'):
            self._status(r['error'])
            return
        lines = [f'项目：{self._project_root}',
                 f'问题 {r["total"]} 条（前 {len(r["issues"])}）', '']
        lines += [f'  · {m}' for m in r['issues']]
        QMessageBox.information(self, '项目体检', '\n'.join(lines))

    def open_stage_preview(self):
        if not len(self._session):
            self._status('先载入一章。')
            return
        self.apply_current_beat()
        try:
            from storygraph.ui_qt.stage_preview import open_preview
            open_preview(self, self._session.frames(),
                         registry=self._registry_provider(),
                         project_root=self._project_root)
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '无法打开预览', str(e))

    def show_help(self):
        from storygraph.ui_qt.help_dialog import show_help
        return show_help(self)

    def _status(self, text: str):
        try:
            self._status_bar.showMessage(str(text), 8000)
        except Exception:                       # noqa: BLE001
            pass

    def closeEvent(self, event):
        for fn in (lambda: self._loader.shutdown(),
                   lambda: self._notifier and self._notifier.close()):
            try:
                fn()
            except Exception:                   # noqa: BLE001
                pass
        super().closeEvent(event)


#: 迁移期别名（测试与 `main_qt` 都引过它）
ScriptTableEditorWindowQt = MainWindowQt


def open_editor(engine, script_path=None, project_root=None, graph=None):
    win = MainWindowQt(engine, script_path, project_root, graph)
    win.show()
    return win
