"""
storygraph.ui_qt.main_window
============================
Qt 版**剧本表编辑器主窗口**（重设计版）。

布局（用户 2026-09-30 定调："更整体、简洁、美观、实用"）：

  · 顶部工具栏：载入 / **保存这一拍** / 导出 / 体检 / 预览 ｜ 翻页 ｜ 说明
  · 左侧 dock：章节列表 + 参与角色（谁在说话）
  · 中央编辑区：说话人/台词/立绘网格/背景/音乐/语音/备注 + 可折叠高级区
  · **操作提示移出主界面**：都在「说明」窗口（工具栏 ❓ 打开，不占空间）

**语义全部复用后端**（见 `docs/前端迁移方案_qt.md` 第三条）：
beats / hydrate / fields / sprite_rows / participants / patch_export / guard
—— 这个文件只做 Qt 的**摆控件、读表单、写表单**。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDockWidget,
                               QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QStatusBar,
                               QToolBar, QVBoxLayout, QWidget)

from storygraph.story.beat_render import make_symbols
from storygraph.ui import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.sprite_grid import SpriteGridWidget, cards_to_show
from storygraph.ui_qt.thumb_loader import ThumbLoader

# 与 tkinter 版共用主区/高级区划分（同一个设计语义，别各写一份）
PRIMARY_FIELDS = ['Speaker', 'Text', 'Scene', 'BGM', 'Voice', 'Comment']
ADVANCED_FIELDS = ['SFX', 'CodeBlock', 'Label', 'Kind', 'Notes', 'Stage']


class BeatCardEditor(QWidget):
    """
    中央编辑卡片：说话人/台词/立绘网格/背景/音乐/语音/备注 + 可折叠高级区。

    它**不知道**剧情语义 —— 只认 `beat_to_fields` 给的字典与
    `fields_to_beat` 要的字典（这两个函数在 `beat_editor` 里，与 tkinter 版
    **同一份**），所以"哪个字段对应 Beat 的哪个属性"不用重写。
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

        # —— 说话人 / 台词 ——
        row1 = QHBoxLayout()
        row1.setSpacing(T.SPACE['sm'])
        row1.addWidget(self._section('说话人'))
        self._speaker = QComboBox()
        self._speaker.setEditable(True)
        self._widgets['Speaker'] = self._speaker
        self._speaker.setMaximumWidth(220)
        row1.addWidget(self._speaker, 0)
        row1.addWidget(QLabel('台词'), 0)
        root.addLayout(row1)

        self._text = QPlainTextEdit()
        self._text.setFixedHeight(3 * 22 + 12)
        self._text.setProperty('role', 'text')
        self._widgets['Text'] = self._text
        root.addWidget(self._text)

        # —— 立绘网格 ——
        root.addWidget(self._section('立绘（表情 / 位置点 chip 换，双击换图）'))
        self._grid = SpriteGridWidget(symbols=self._symbols, loader=loader,
                                      on_pick_image=self._on_pick_image)
        root.addWidget(self._grid)

        # —— 背景 / 音乐 / 语音 / 备注 ——
        row2 = QHBoxLayout()
        row2.setSpacing(T.SPACE['sm'])
        for name, hint in (('Scene', '背景'), ('BGM', '音乐'), ('Voice', '语音')):
            box = QVBoxLayout()
            box.setSpacing(2)
            box.addWidget(QLabel(hint))
            combo = QComboBox()
            combo.setEditable(True)
            self._widgets[name] = combo
            box.addWidget(combo)
            row2.addLayout(box, 1)
        root.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(T.SPACE['sm'])
        row3.addWidget(QLabel('备注'), 0)
        self._comment = QLineEdit()
        self._widgets['Comment'] = self._comment
        row3.addWidget(self._comment, 1)
        root.addLayout(row3)

        # —— 高级区（可折叠） ——
        self._adv_btn = QPushButton('▸ 高级区（代码 / Label / 类型 / Notes / Stage）')
        self._adv_btn.setFlat(True)
        self._adv_btn.setProperty('role', 'caption')
        self._adv_btn.clicked.connect(self._toggle_adv)
        root.addWidget(self._adv_btn)
        self._adv_host = QWidget()
        adv = QVBoxLayout(self._adv_host)
        adv.setContentsMargins(0, 0, 0, 0)
        adv.setSpacing(T.SPACE['xs'])
        self._adv_host.setVisible(False)
        for name in ADVANCED_FIELDS:
            box = QHBoxLayout()
            box.setSpacing(T.SPACE['xs'])
            box.addWidget(QLabel(name), 0)
            w = (QPlainTextEdit() if name in ('CodeBlock', 'Stage', 'Notes')
                 else QLineEdit())
            if isinstance(w, QPlainTextEdit):
                w.setFixedHeight(3 * 20 + 12)
            self._widgets[name] = w
            box.addWidget(w, 1)
            adv.addLayout(box)
        root.addWidget(self._adv_host)
        root.addStretch(1)

    def _toggle_adv(self):
        self._adv_open = not self._adv_open
        self._adv_host.setVisible(self._adv_open)
        self._adv_btn.setText(('▾' if self._adv_open else '▸')
                              + ' 高级区（代码 / Label / 类型 / Notes / Stage）')

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

    def load_fields(self, fields: dict, sprite_items: list):
        """fields：`beat_to_fields(beat)`；sprite_items：卡 item 列表。"""
        for name in PRIMARY_FIELDS + ADVANCED_FIELDS:
            self._set_value(name, fields.get(name, ''))
        self._grid.load(sprite_items)

    def collect_fields(self) -> dict:
        """读回主区（始终可写）+ 展开时的高级区。"""
        out = {n: self._get_value(n) for n in PRIMARY_FIELDS}
        if self._adv_open:
            out.update({n: self._get_value(n) for n in ADVANCED_FIELDS})
        return out

    def sprite_text(self, symbols=None) -> str:
        return cards_to_show(self._grid.cards(), symbols)


class ScriptTableEditorWindowQt(QMainWindow):
    """Qt 版剧本表编辑器（与 tkinter 版**同一份数据契约**）。"""

    def __init__(self, engine, script_path=None):
        super().__init__()
        self.engine = engine
        self.beats: list = []
        self._loaded_idx = -1
        self._file_path = str(script_path or '')
        self._symbols = make_symbols(self._registry())
        self._loader = ThumbLoader(self, max_size=(160, 220),
                                   project_root=getattr(engine, 'project_root',
                                                        None))
        self.setWindowTitle('剧本表编辑器（Qt 版）')
        self.resize(1500, 900)
        self._build()
        if script_path:
            QTimer.singleShot(0, self.load_script)

    # ------------------------------------------------------------------
    #  基础
    # ------------------------------------------------------------------

    def _registry(self):
        try:
            return self.engine.get_registry()
        except Exception:                    # noqa: BLE001
            return None

    def _project_root(self):
        return getattr(self.engine, 'project_root', None)

    # ------------------------------------------------------------------
    #  界面
    # ------------------------------------------------------------------

    def _build(self):
        theme.apply_to(self)
        self.setStatusBar(QStatusBar())

        # —— 工具栏 ——
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, fn, tip=None, primary=False):
            a = QAction(text, self)
            a.triggered.connect(fn)
            if tip:
                a.setToolTip(tip)
            tb.addAction(a)
            if primary:
                btn = tb.widgetForAction(a)
                if btn is not None:
                    btn.setProperty('variant', 'primary')
                    btn.style().unpolish(btn)
                    btn.style().polish(btn)
            return a

        act('📂 载入 .rpy', self._on_load, '打开一章 Ren\'Py 脚本')
        self._save_action = act('💾 保存这一拍', self.save_current_beat,
                                '把这张卡片的改动提交到当前拍（Ctrl+S）', primary=True)
        act('⤓ 导出 .rpy', self._on_export, '生成/增量补丁写回 .rpy（过安全门禁）')
        act('🔍 项目体检', self.show_health, '全项目检查（只读，不写盘）')
        act('🎬 演出预览', self.open_stage_preview, '把这一章按卡片效果"跑起来"')

        tb.addSeparator()
        self._prev_action = act('◀ 上一拍', self.prev_beat, 'Alt+↑ / ↑')
        self._pos = QLabel('— / —')
        self._pos.setProperty('role', 'caption')
        tb.addWidget(self._pos)
        self._next_action = act('下一拍 ▶', self.next_beat, 'Enter / Alt+↓ / ↓')
        tb.addSeparator()
        act('❓ 说明', self.show_help, '使用说明（从这里看，不占用主界面）')

        # —— 中央编辑区（可滚） ——
        self._panel = BeatCardEditor(symbols=self._symbols, loader=self._loader,
                                     on_pick_image=self._pick_image)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._panel)
        self.setCentralWidget(scroll)

        # —— 左侧 dock：章节 + 参与角色 ——
        dock = QDockWidget('章节 / 参与角色', self)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                               T.SPACE['sm'], T.SPACE['sm'])
        lay.setSpacing(T.SPACE['sm'])
        lay.addWidget(QLabel('章节（拍）'))
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_select)
        lay.addWidget(self._list, 1)
        lay.addWidget(QLabel('参与角色（双击填进这一拍）'))
        self._who = QListWidget()
        self._who.setFixedHeight(110)
        self._who.itemDoubleClicked.connect(self._fill_speaker)
        lay.addWidget(self._who, 0)
        dock.setWidget(left)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.resizeDocks([dock], [240], Qt.Horizontal)

        # 快捷键
        self.setFocusPolicy(Qt.StrongFocus)
        QApplication.instance().installEventFilter(self)

    # ------------------------------------------------------------------
    #  载入
    # ------------------------------------------------------------------

    def _on_load(self):
        root = self._project_root() or ''
        start = str(Path(root) / 'game' if root else Path.cwd())
        path, _ = QFileDialog.getOpenFileName(
            self, '载入 .rpy', start, "Ren'Py 脚本 (*.rpy)")
        if path:
            self._file_path = path
            self.load_script()

    def load_script(self):
        if not self._file_path:
            return
        try:
            from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                         RenPyParser)
            from storygraph.story.beat_build import (beats_from_parse_result,
                                                     hydrate_from_dsl)
            from storygraph.story.beat_model import compose_stage_extra
            pr = ParseResultBuilder().build(
                RenPyParser().parse(self._file_path))
            self.beats = beats_from_parse_result(pr)
            self._symbols = make_symbols(self._registry())
            for b in self.beats:
                b.stage = compose_stage_extra(b, self._symbols)
            hydrate_from_dsl(self.beats, self._symbols)
            self._panel.set_symbols(self._symbols)
            self._reload_list()
            self._show_index(0)
            self._status(f'已载入 {Path(self._file_path).name}：'
                         f'{len(self.beats)} 拍')
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '载入失败', str(e))

    def _reload_list(self):
        self._list.blockSignals(True)
        self._list.clear()
        from storygraph.story.beat_model import KIND_MENU
        for i, b in enumerate(self.beats):
            who = (b.speaker or '').strip()
            kind = b.kind
            prefix = {'narration': '☁', 'menu': '⚑', 'menu_option': '➜'}.get(
                kind, '')
            text = (b.text or '').strip() or '（无台词）'
            item = QListWidgetItem(
                f"{i + 1:>3} {prefix}{who}：{text[:26]}")
            item.setData(Qt.UserRole, i)
            self._list.addItem(item)
        self._list.blockSignals(False)
        # 参与角色
        try:
            from storygraph.ui.beat_editor import participants
            people = participants(self.engine)
        except Exception:                       # noqa: BLE001
            people = []
        self._who.clear()
        for cid in people:
            self._who.addItem(QListWidgetItem(cid))

    # ------------------------------------------------------------------
    #  卡片 ↔ 数据
    # ------------------------------------------------------------------

    def _show_index(self, idx: int):
        if not (0 <= idx < len(self.beats)):
            self._loaded_idx = -1
            return
        self._loaded_idx = idx
        b = self.beats[idx]
        from storygraph.ui.beat_editor import (beat_sprite_rows, beat_to_fields,
                                               group_sprites_by_tag)
        fields = beat_to_fields(b, self._symbols)
        rows = beat_sprite_rows(b, self._symbols)
        # 聚合卡：字段在 `active` 里（同一角色的其余条目在 `extras`）
        items = []
        for card in group_sprites_by_tag(rows):
            it = dict(card.get('active') or {})
            it['extra_rows'] = list(card.get('extras') or [])
            it['order_index'] = card.get('first_index', 0)
            it['speaking'] = it.get('speaking', False)
            items.append(it)
        self._panel.load_fields(fields, items)
        # 说话人下拉项（供选）
        try:
            chars = sorted((self._registry().characters or {}).keys())
        except Exception:                       # noqa: BLE001
            chars = []
        cur = (b.speaker or '').strip()
        combo = self._panel._speaker
        combo.blockSignals(True)
        combo.clear()
        combo.addItem('')
        for c in chars:
            combo.addItem(c)
        combo.setCurrentText(cur)
        combo.blockSignals(False)

        self._list.blockSignals(True)
        self._list.setCurrentRow(idx)
        self._list.blockSignals(False)
        self._update_nav()

    def apply_current_beat(self):
        """
        表单 → 当前拍。与 tkinter 版 `collect_beat` **同一条语义**：

          · 主区（说话人/台词/背景/音乐/语音/备注）**始终**写回；
          · 高级区只在**展开时**写回（收起时一个字都不碰 ——
            不然"改一句台词把这一拍的 Stage/代码弄没了"）；
          · 立绘网格 → `show`；空网格**不**清空立绘；
          · 整拍位置只有"所有立绘一致"才同步（防立绘错位）；
          · `Stage` 是结构化列的**投影**，每次重算（不是输入）。
        """
        if not (0 <= self._loaded_idx < len(self.beats)):
            return None
        b = self.beats[self._loaded_idx]
        fields = self._panel.collect_fields()
        b.speaker = str(fields.get('Speaker') or '').strip()
        b.text = str(fields.get('Text') or '').strip()
        b.scene = str(fields.get('Scene') or '').strip()
        b.bgm = str(fields.get('BGM') or '').strip()
        b.voice = str(fields.get('Voice') or '').strip()
        b.comment = str(fields.get('Comment') or '').strip()
        if self._panel._adv_open:
            b.sfx = str(fields.get('SFX') or '').strip()
            b.code = str(fields.get('CodeBlock') or '').strip()
            b.raw = ''
            b.label = str(fields.get('Label') or '').strip() or b.label
            b.kind = str(fields.get('Kind') or '').strip() or b.kind
            b.notes = str(fields.get('Notes') or '').strip()
        sprite_text = self._panel.sprite_text(self._symbols)
        if sprite_text:
            b.show = sprite_text
        from storygraph.ui_qt.sprite_grid import uniform_position_of
        b.at = uniform_position_of(self._panel._grid.cards())
        try:
            from storygraph.story.beat_model import compose_stage_extra
            from storygraph.story.beat_build import hydrate_from_dsl
            b.stage = compose_stage_extra(b, self._symbols)
            hydrate_from_dsl([b], self._symbols)
        except Exception:                    # noqa: BLE001
            pass
        return b

    def save_current_beat(self):
        if not (0 <= self._loaded_idx < len(self.beats)):
            self._status('还没有载入章节，没什么可保存。')
            return
        b = self.apply_current_beat()
        line_id = getattr(b, 'line_id', '') or f'第 {self._loaded_idx + 1} 拍'
        self._status(f'✔ 已保存到这一拍（{line_id}）。'
                     f'改动在内存里，点「导出 .rpy」时才落盘。')

    # ------------------------------------------------------------------
    #  导航（与 tkinter 版**同一契约**：翻页 = 纯导航，不写别的拍）
    # ------------------------------------------------------------------

    def _update_nav(self):
        n = len(self.beats)
        i = self._loaded_idx
        if hasattr(self, '_prev_action'):
            self._prev_action.setEnabled(i > 0)
            self._next_action.setEnabled(0 <= i < n - 1)
            self._pos.setText(f'{i + 1 if i >= 0 else 0} / {n}')

    def _on_select(self, row: int):
        if row >= 0 and row != self._loaded_idx:
            self.apply_current_beat()
            self._show_index(row)

    def next_beat(self):
        if 0 <= self._loaded_idx < len(self.beats):
            self.apply_current_beat()
            self._show_index(self._loaded_idx + 1)

    def prev_beat(self):
        if 0 <= self._loaded_idx < len(self.beats):
            self.apply_current_beat()
            self._show_index(self._loaded_idx - 1)

    def _fill_speaker(self, item):
        cid = item.text() if item else ''
        if not cid or not (0 <= self._loaded_idx < len(self.beats)):
            return
        self.beats[self._loaded_idx].speaker = cid
        self._panel._speaker.setCurrentText(cid)
        self._status(f'已把「{cid}」填进这一拍的说话人。')

    # ------------------------------------------------------------------
    #  事件（翻页键：Enter / ↓ / ↑，Ctrl+S）
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.KeyPress:
            key = event.key()
            mods = event.modifiers()
            if key in (Qt.Key_S,) and mods & Qt.ControlModifier:
                self.save_current_beat()
                return True
            # **回车 = 下一拍**（用户 2026-09-30 明确："回车是下一张卡片的意思"）。
            # 无论焦点在哪；台词要换行用 Shift+Enter（见「说明」）。
            if key in (Qt.Key_Return, Qt.Key_Enter) and not (mods & Qt.ShiftModifier):
                self.next_beat()
                return True
            fw = QApplication.focusWidget()
            if key == Qt.Key_Down:
                if isinstance(fw, QPlainTextEdit) and not self._caret_at_edge(fw, True):
                    return False              # 多行台词：光标没到底就让它移动
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
        """多行输入框的光标是否已在最后一行（down）/ 第一行（up）。
        与 tkinter 版 `_caret_at_edge` 同一规则：只有"自己敲过回车（多行）"
        才在行间移动光标；折行的长台词按 ↓ 直接翻页。"""
        try:
            cur = edit.textCursor().blockNumber()
            last = edit.blockCount() - 1
            return cur >= last if down else cur <= 0
        except Exception:                    # noqa: BLE001
            return True

    # ------------------------------------------------------------------
    #  选图（立绘）
    # ------------------------------------------------------------------

    def _pick_image(self, current: str) -> str:
        """打开立绘选择器（真实数据下复用已有的 Qt 选择器或简化版）。"""
        try:
            from storygraph.ui_qt.asset_picker import pick_image
            return pick_image(self, self._registry(),
                              current=current,
                              project_root=self._project_root()) or ''
        except Exception:                       # noqa: BLE001 - 选择器还没做
            return ''

    # ------------------------------------------------------------------
    #  导出 / 体检 / 预览 / 说明
    # ------------------------------------------------------------------

    def _on_export(self):
        if not self.beats:
            self._status('没有可导出的内容。')
            return
        self.apply_current_beat()
        try:
            from storygraph.pipeline.compile import compile_beats
            result = compile_beats(self.beats, registry=self._registry())
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '编译失败', str(e))
            return
        if getattr(result, 'blocking', None):
            QMessageBox.warning(self, '生成被门禁拦截',
                                '\n'.join(str(i) for i in result.blocking))
            return
        out_dir = Path(self._project_root() or '.') / 'build' / 'renpy'
        out_dir.mkdir(parents=True, exist_ok=True)
        default = out_dir / (Path(self._file_path).stem + '.generated.rpy'
                             if self._file_path else 'out.rpy')
        path, _ = QFileDialog.getSaveFileName(self, '导出 .rpy',
                                              str(default), "Ren'Py (*.rpy)")
        if not path:
            return
        from storygraph.story.generation_guard import (evaluate_generation,
                                                       labels_in_danger,
                                                       write_generation)
        from storygraph.story.patcher import patch_export
        text = result.rpy_text
        patch_note = ''
        try:
            src = self._file_path if Path(self._file_path).exists() else None
            res = patch_export(self.beats, src, text,
                               registry=self._registry())
            if res.ok:
                text = res.text
                patch_note = (f'增量补丁：对位 {res.matched_beats}/'
                              f'{res.total_beats} 拍，改动 {res.changed_lines} 行')
            else:
                patch_note = '全量重生成（增量补丁未采用：' \
                             + ('；'.join(res.reasons) or f'对位率不足（{res.coverage:.0%}）') + '）'
        except Exception as e:                  # noqa: BLE001
            patch_note = f'全量重生成（增量补丁不可用：{e}）'

        occupied = labels_in_danger(path, self._project_root(),
                                    exclude={path, self._file_path})
        issues = evaluate_generation(text, path,
                                     project_root=self._project_root(),
                                     occupied=occupied)
        blocking = [i for i in issues if i.is_blocking]
        if blocking:
            QMessageBox.warning(self, '生成被门禁拦截',
                                '\n'.join(i.message for i in blocking))
            return
        write_generation(text, path, project_root=self._project_root())
        todos = getattr(result, 'todo_count', 0)
        warn = f'，缺资源 {todos} 处（见 TODO）' if todos else ''
        self._status(f'✔ 已导出 {Path(path).name}（{patch_note}{warn}）')

    def show_health(self):
        root = self._project_root()
        if not root:
            self._status('先载入一个项目。')
            return
        try:
            from storygraph.pipeline.build import collect_report
            r = collect_report(root)
            issues = getattr(r, 'issues', []) or []
            lines = [f'项目：{root}', f'问题 {len(issues)} 条（前 30）', '']
            for i in issues[:30]:
                lines.append(f'  · {getattr(i, "message", i)}')
            QMessageBox.information(self, '项目体检', '\n'.join(lines))
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '体检失败', str(e))

    def open_stage_preview(self):
        if not self.beats:
            self._status('先载入一章。')
            return
        self.apply_current_beat()
        try:
            from storygraph.ui_qt.stage_preview import open_preview
            open_preview(self, self.beats, registry=self._registry(),
                         project_root=self._project_root())
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '无法打开预览', str(e))

    def show_help(self):
        try:
            from storygraph.ui_qt.help_dialog import show_help
            show_help(self)
        except Exception as e:                  # noqa: BLE001
            QMessageBox.information(self, '说明', str(e))

    # ------------------------------------------------------------------

    def _status(self, text: str):
        try:
            self.statusBar().showMessage(str(text), 8000)
        except Exception:                       # noqa: BLE001
            pass

    def closeEvent(self, event):
        try:
            self._loader.shutdown()
        except Exception:                       # noqa: BLE001
            pass
        super().closeEvent(event)


def open_editor(engine, script_path=None):
    """开一个 Qt 版编辑器（不进事件循环）。"""
    win = ScriptTableEditorWindowQt(engine, script_path)
    win.show()
    return win
