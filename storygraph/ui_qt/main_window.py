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
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QTextCursor
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow,
                               QMenu, QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSplitter, QStackedWidget,
                               QToolBar, QVBoxLayout, QWidget)

from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS, EditorSession
from storygraph.ui_qt import tokens as T
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
        set_stage_loaded / is_modified / set_undo_enabled
    """

    #: 「保存这张卡片」/「撤销修改」两个按钮被按下时发出
    save_requested = Signal()
    undo_requested = Signal()

    def __init__(self, symbols=None, loader=None, parent=None,
                 on_pick_image=None, on_pick_asset=None):
        super().__init__(parent)
        self._symbols = symbols
        self._widgets: dict = {}
        self._adv_open = False
        self._on_pick_image = on_pick_image
        #: `(字段名, 当前值) -> 新值`：背景/音乐/语音那三格的「选择…」用它。
        #: 卡片不认识资源表与选择器 —— 那条路由主窗口注入（同一个回调模式）。
        self._on_pick_asset = on_pick_asset
        #: 载入时 `Stage` 那一格里的原文。用来判断"用户手改过 Stage 没有" ——
        #: 判据是 `当前值 != 这个值`，不是"格子非空"（`beat_to_fields`
        #: 会把结构化列投影进这一格，所以非空不代表人改过）。
        self._stage_loaded = ''
        #: 载入时的整份字段（判断"表单被改脏了没有"，纯显示用）。
        self._loaded_fields: dict = {}
        self._loading = False
        self._build(loader)
        self._connect_dirty_tracking()

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

        # —— 字数：超长台词在游戏里会溢出对话框 ——
        # tkinter 版有这一项，搬 Qt 时漏了。它属于"作者看不见的坑"：
        # 编辑器里换行好看，进游戏可能被裁掉，所以必须提示。
        self._char_label = QLabel('')
        self._char_label.setProperty('role', 'caption')
        self._char_label.setAlignment(Qt.AlignRight)
        root.addWidget(self._char_label)

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

        # —— 当前舞台（这一拍生效的状态）——
        # 用户 2026-10-03："为何不写明现在的舞台情况是什么样的，旧版明明都有"
        # 旧版预览面板用的是 `stage_snapshot.stage_digest`，卡片搬 Qt 时没接上。
        root.addWidget(self._section('当前舞台（这一拍生效）'))
        self._stage_label = QLabel('')
        self._stage_label.setProperty('role', 'note')
        self._stage_label.setWordWrap(True)
        self._stage_label.setFont(QFont(T.FONTS['mono'][0],
                                        T.FONTS['mono'][1]))
        self._stage_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self._stage_label)

        # —— 背景 / 音乐 / 语音（**两行**：背景一行，音乐 + 语音一行）——
        # 每格 = 下拉候选（从**资源表**来）+ 「选择…」按钮（打开资产选择器）。
        # 用户 2026-10-03："添加立绘，添加语音，添加音乐，全部都没有反应！"
        #   以前这三格是**空的可编辑下拉** —— 没有任何候选可挑。
        # 用户 2026-10-04："把三格改成两行布局（背景一行、音乐+语音一行）"。
        self._asset_btns = {}

        def _asset_row(specs, row_name):
            """
            一行资产格（背景一行；音乐 + 语音一行）。

            外面套一层 QWidget 并给 objectName：布局本身没法被断言，
            有了具名容器，测试才能钉住"哪几格在同一行"。
            """
            holder = QWidget()
            holder.setObjectName(row_name)
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(T.SPACE['sm'])
            for name, label in specs:
                box = QVBoxLayout()
                box.setSpacing(2)
                cap = QLabel(label)
                cap.setProperty('role', 'caption')
                box.addWidget(cap)
                line = QHBoxLayout()
                line.setSpacing(T.SPACE['xs'])
                combo = QComboBox()
                combo.setEditable(True)
                self._widgets[name] = combo
                line.addWidget(combo, 1)
                pick = QPushButton('选择…')
                pick.setToolTip(f'从资源表里挑{label}（分类 / 搜索 / 预览）')
                pick.clicked.connect(lambda _=False, f=name: self._choose_asset(f))
                self._asset_btns[name] = pick
                line.addWidget(pick, 0)
                box.addLayout(line)
                row.addLayout(box, 1)
            return holder

        root.addWidget(_asset_row([('Scene', '背景')], 'assetRowScene'))
        root.addWidget(_asset_row([('BGM', '音乐'), ('Voice', '语音')],
                                  'assetRowAudio'))

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

        # —— 条件显示（`if <条件>:`）——
        # 数据层的 `Beat.condition` 一直存在（解析时从语句取、渲染时包成
        # `if`），但**从来没暴露给策划**。这里给一格：空 = 无条件。
        row4 = QHBoxLayout()
        row4.setSpacing(T.SPACE['sm'])
        cap = QLabel('＋ 条件')
        cap.setProperty('role', 'caption')
        cap.setToolTip('填了就在这一拍外面包一层 if（例：love >= 5）。'
                       '留空 = 无条件。')
        row4.addWidget(cap, 0)
        self._condition = QLineEdit()
        self._condition.setPlaceholderText('留空 = 无条件（例：love >= 5）')
        self._widgets['condition'] = self._condition
        row4.addWidget(self._condition, 1)
        root.addLayout(row4)

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

        # —— 保存 / 撤销（这张卡片的）——
        # 用户 2026-10-01 明确要这两个按钮，而不是只有 Ctrl+S：
        #   > 我希望卡片端加两个功能：一个是保存该卡片的修改，
        #   > 另一个是撤销修改的操作
        # 它们属于**这一张卡片**，所以放在卡片里（工具栏也有 💾，两处都能按）。
        root.addSpacing(T.SPACE['xs'])
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setProperty('role', 'hairline')
        root.addWidget(line)

        actions = QHBoxLayout()
        actions.setSpacing(T.SPACE['sm'])
        self._save_btn = QPushButton('💾 保存这张卡片')
        self._save_btn.setProperty('variant', 'primary')
        self._save_btn.setToolTip(
            '把这张卡片上的改动写进这一拍（Ctrl+S）。\n'
            '写的是内存里的剧情，要进脚本还要点「⤓ 导出 .rpy」。')
        self._save_btn.clicked.connect(self.save_requested.emit)
        actions.addWidget(self._save_btn)

        self._undo_btn = QPushButton('↩ 撤销修改')
        self._undo_btn.setToolTip(
            '把这张卡片恢复成**上一次保存时**的样子（Ctrl+Z）。\n'
            '只影响这一拍，不动别的卡片。')
        self._undo_btn.clicked.connect(self.undo_requested.emit)
        actions.addWidget(self._undo_btn)

        self._dirty_label = QLabel('')
        self._dirty_label.setProperty('role', 'caption')
        actions.addWidget(self._dirty_label, 0)
        actions.addStretch(1)
        root.addLayout(actions)

        self._undo_btn.setEnabled(False)
        root.addStretch(1)

        # —— 防御性提示条（"有台词但没说话人"= 会变成旁白）——
        # 放在最下面、默认不占位：它是**警告**，不是常用操作。
        self._note_label = QLabel('')
        self._note_label.setProperty('role', 'warn')
        self._note_label.setWordWrap(True)
        self._note_label.setVisible(False)
        root.addWidget(self._note_label)

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

    def set_asset_choices(self, field: str, choices):
        """
        给某一格装候选（从资源表来）：`[(值, 说明), ...]`。

        保留用户当前正在编辑的值（即使它不在候选里 —— 手写的自定义值不许被
        下拉清空）。说明挂成候选项的 tooltip，挑的时候能看清是什么效果。
        """
        combo = self._widgets.get(field)
        if not isinstance(combo, QComboBox):
            return
        current = combo.currentText()
        combo.blockSignals(True)
        try:
            combo.clear()
            for value, desc in (choices or ()):
                combo.addItem(str(value))
                if desc:
                    combo.setItemData(combo.count() - 1, str(desc),
                                      Qt.ToolTipRole)
            combo.setCurrentText(current)
        finally:
            combo.blockSignals(False)

    def _choose_asset(self, field):
        """点「选择…」：把资产选择器的结果填进这一格（取消/没资源表则不动）。"""
        if self._on_pick_asset is None:
            return
        value = self._on_pick_asset(field, self._get_value(field))
        if value:
            self._set_value(field, value)
            self.refresh_dirty()

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

    def prefetch_sprites(self, items: list):
        """把"之后几拍要显示的立绘"先解进缩略图缓存（见 `SpriteGridWidget.prefetch`）。"""
        try:
            self._grid.prefetch(items)
        except Exception:                    # noqa: BLE001 - 预取失败不影响显示
            swallow('main_window.prefetch_sprites')

    #: 卡片上**不属于** Beat 列、但要单独处理的那几个控件名。
    #: `condition` 是小写（它是 `Beat.condition`，不是 Excel 列名）。
    EXTRA_WIDGETS = ('condition',)

    def load_fields(self, fields: dict, sprite_items: list):
        """
        把一拍的内容填进卡片。

        `_loading` 期间屏蔽脏标记计算：填控件本身会触发一串
        `textChanged`，不加这道闸，"刚载入"会被判成"已修改"。
        """
        self._loading = True
        try:
            for name in self._all_names():
                self._set_value(name, fields.get(name, ''))
            self._grid.load(sprite_items)
            self._loaded_fields = {n: str(fields.get(n, '') or '')
                                   for n in self._all_names()}
            # Stage 那一格的原文单独记一份（判"手改过没有"要用它）
            self._stage_loaded = str(fields.get('Stage', '') or '')
        finally:
            self._loading = False
        self.refresh_dirty()

    @classmethod
    def _all_names(cls) -> tuple:
        """卡片上**全部**字段名（主区 + 高级区 + 额外控件）。"""
        return tuple(PRIMARY_FIELDS) + tuple(ADVANCED_FIELDS) + cls.EXTRA_WIDGETS

    def collect_fields(self) -> dict:
        out = {n: self._get_value(n) for n in PRIMARY_FIELDS}
        if self._adv_open:
            out.update({n: self._get_value(n) for n in ADVANCED_FIELDS})
        # 条件始终写回（有值就包 if，空就不是条件）——它一直可见，
        # 所以适用"始终可编辑就必须始终写回"那条规则。
        out['condition'] = self._get_value('condition')
        return out

    # ---------- 保存 / 撤销 相关的状态 ----------

    def stage_loaded(self) -> str:
        """载入时 `Stage` 那一格的原文（回传给 `EditorSession.save`）。"""
        return self._stage_loaded

    def reset_baseline(self):
        """
        "当前表单内容 = 已保存状态" —— 保存成功后调用。

        只重记基线，**不动控件**（用户可能已经接着改了下一个字，
        这时把控件重填一遍会把他的光标冲掉）。
        """
        self._loaded_fields = self.collect_all_fields()
        self._stage_loaded = self._get_value('Stage')
        self.refresh_dirty()

    def collect_all_fields(self) -> dict:
        """主区 + 高级区 + 额外控件**全部**字段的当前值（不看折叠状态）。"""
        return {n: self._get_value(n) for n in self._all_names()}

    def is_modified(self) -> bool:
        """
        表单内容和"上一次保存"相比变了吗？（只管**表单字段**）

        立绘卡上的 chip 改动不在这里判 —— 那需要 `app.beats` 的语义，
        由窗口用会话的快照指纹判（见 `MainWindowQt._mark_dirty`）。
        """
        if not self._loaded_fields:
            return False
        return self.collect_all_fields() != self._loaded_fields

    def set_undo_enabled(self, on: bool):
        """撤销按钮是否可用（由窗口按会话的撤销栈状态决定）。"""
        self._undo_btn.setEnabled(bool(on))

    def refresh_dirty(self):
        """刷新那行小字提示（未保存 + 字数）。"""
        modified = self.is_modified()
        self._dirty_label.setText('● 有未保存的改动' if modified else '')
        self._dirty_label.setProperty('role',
                                      'warn' if modified else 'caption')
        try:
            self._dirty_label.style().unpolish(self._dirty_label)
            self._dirty_label.style().polish(self._dirty_label)
        except Exception:                    # noqa: BLE001
            swallow('main_window.refresh_dirty')
        self.update_char_count()

    #: 台词字数上限（**不含空白**）。超过就在游戏里溢出对话框 ——
    #: 这是"编辑器里看着好好的、进游戏被裁掉"的典型来源。
    CHAR_WARN = 200

    def set_note(self, text):
        """显示/清掉卡片底部的警告条（文案由 `app.beats` 给）。"""
        text = str(text or '')
        self._note_label.setText(text)
        self._note_label.setVisible(bool(text))

    def set_stage_digest(self, digest: str, inherit: str = ''):
        """
        卡片上的「当前舞台」：这一拍生效的状态 + 继承提示。

        `digest` 由 `story.stage_snapshot.stage_digest` 出（与预览、与
        Excel 的舞台快照表同源，前端不自己拼一套"当前舞台"的算法）；
        `inherit` 是 `app.beats.inherited_state_hint` 的"继承上一拍：…"。
        """
        parts = []
        if str(inherit or '').strip():
            parts.append(str(inherit).strip())
        if str(digest or '').strip():
            parts.append(str(digest).strip())
        try:
            self._stage_label.setText('\n'.join(parts))
        except Exception:                    # noqa: BLE001
            swallow('main_window.set_stage_digest')

    def update_char_count(self):
        """
        台词字数提示（不含空白）。

        为什么不算空白：中文台词几乎不空格，而写作时敲的换行/缩进
        不该被算成"内容"（否则排版一改，字数就跳）。
        """
        text = self._text.toPlainText()
        n = len(''.join(str(text or '').split()))
        if not n:
            self._char_label.setText('')
            return
        if n > self.CHAR_WARN:
            self._char_label.setText(
                f'{n} 字 —— 超过 {self.CHAR_WARN} 字，游戏里可能溢出对话框')
            self._char_label.setProperty('role', 'warn')
        else:
            self._char_label.setText(f'{n} 字')
            self._char_label.setProperty('role', 'caption')
        try:
            self._char_label.style().unpolish(self._char_label)
            self._char_label.style().polish(self._char_label)
        except Exception:                    # noqa: BLE001
            swallow('main_window.update_char_count')

    def _connect_dirty_tracking(self):
        """任何输入变化都刷新一次"未保存"提示。"""
        def hook(_=None):
            if not self._loading:
                self.refresh_dirty()
        for w in self._widgets.values():
            for sig in ('textChanged', 'currentTextChanged'):
                s = getattr(w, sig, None)
                if s is not None:
                    try:
                        s.connect(hook)
                    except Exception:        # noqa: BLE001
                        swallow('main_window._connect_dirty_tracking')
        self._grid.change.connect(hook)



class MainWindowQt(QMainWindow):
    """
    Qt 版主窗口 —— **两页**：剧情结构图（第一屏）→ 卡片编辑器（点进模块才进）。

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
        '剧情图是空的 —— 它只放**你自己导入的章节**，不会自动装进整个项目。\n\n'
        '① 点「➕ 导入章节 .rpy」选要看的章节（可多选，累加）\n'
        '② 双击模块 → 进入卡片编辑器改这一章的台词\n'
        '③ 想看整部戏的分支/条件，就把相关章节都导入进来\n\n'
        '（「📁 打开项目」是另一条路：只加载资源表 xlsx —— 立绘 / 音乐 / '
        '角色候选与缩略图，它不往这张图里放任何东西）')

    def __init__(self, engine, script_path=None, project_root=None, graph=None):
        super().__init__()
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
        self.setWindowTitle('剧情编辑器 · 剧情结构图 + 卡片界面')
        self.resize(1600, 950)
        self._build()
        #: 构造后**立刻**载入这一章（同步）。
        #: 不推迟到 QTimer 里 —— 调用方（测试、脚本、`open_editor`）
        #: 需要"构造完就能用"，否则得先跑一次事件循环才知道载没载上。
        #: `goto_card=False`：带了章节参数也**先停在结构页**（用户明确要求
        #: 第一屏是剧情树），章节内容已经在会话里，双击模块即可进去。
        self._pending_script = Path(script_path) if script_path else None
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
        except Exception:                    # noqa: BLE001
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
        """
        self._sync_nav()

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
        has_nodes = bool(getattr(graph, 'nodes', None))
        self._canvas.set_empty_hint('' if has_nodes else self.EMPTY_HINT)
        self._on_canvas_select(self._canvas.selected_node_id())
        self._update_crumb()

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

    # ---------------- 第 0 页：剧情结构 ----------------

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
        """
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                                 T.SPACE['sm'], T.SPACE['sm'])
        split = QSplitter(Qt.Horizontal)
        outer.addWidget(split)

        # —— 左：结构图（画布）+ 缩放条 ——
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
        self._canvas.selection_changed.connect(self._on_canvas_select)
        self._canvas.node_moved.connect(self._on_node_moved)
        self._canvas.zoom_changed.connect(self._on_zoom_changed)
        mv.addWidget(self._canvas, 1)

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
            'Shift + 左键拖动 = 连线 · 左键选中/拖动模块 · 双击模块 = 编辑剧情变量'
            ' · 滚轮缩放 · 空格 + 左键拖动 = 平移')
        self._graph_hint.setProperty('role', 'caption')
        zrow.addWidget(self._graph_hint)
        mv.addLayout(zrow)
        split.addWidget(mid)

        # —— 右：控制台 + 当前模块 + 主入口 + 分析 ——
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(T.SPACE['xs'])

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

        self._console = QPlainTextEdit()
        self._console.setReadOnly(True)
        self._console.setFont(QFont(T.FONTS['mono'][0], T.FONTS['mono'][1]))
        self._console.setPlaceholderText(
            '导入章节 / 打开项目 / 体检 / 分析的结果都会写在这里。\n'
            '例：[导入] chapter1_3.rpy → 新增 1 个模块 / 0 条连线')
        self._console.setMinimumHeight(180)
        rv.addWidget(self._console, 1)

        cap_mod = QLabel('当前模块')
        cap_mod.setProperty('role', 'section')
        rv.addWidget(cap_mod)
        self._chapter_label = QLabel('（在图上点一个模块）')
        self._chapter_label.setProperty('role', 'caption')
        self._chapter_label.setWordWrap(True)
        rv.addWidget(self._chapter_label)

        self._open_script_btn = QPushButton('✎ 打开本章剧本（改台词）')
        self._open_script_btn.setToolTip(
            '进入卡片编辑器改这一章的台词（也可右键模块 → 改本章台词）')
        self._open_script_btn.setProperty('variant', 'primary')
        self._open_script_btn.clicked.connect(self._open_selected_in_card)
        rv.addWidget(self._open_script_btn)

        self._edit_vars_btn = QPushButton('🧩 编辑剧情变量…')
        self._edit_vars_btn.setToolTip(
            '给策划 / 编剧编排剧情分支用的地方（双击模块同样打开）')
        self._edit_vars_btn.clicked.connect(self._edit_selected_vars)
        rv.addWidget(self._edit_vars_btn)

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
        brow.addStretch(1)
        rv.addLayout(brow)

        # —— 画布（旧版右栏 `LabelFrame(text="画布")` 那一组）——
        #
        # 用户 2026-10-04：
        # > 我的旧版本有导出控制流图.rpy文件（label 末尾加 _control 的那个）…
        # > 有保存布局.json！现在这个新版本呢？为何模块编辑器的功能缺失那么多？
        # > （可以把右边的命令栏缩小点添加这两个东西）
        #
        # 后端 `pipeline.renpy_exporter.export_to_renpy` 与旧版 JSON 格式的
        # `LoadLayoutCommand` 都还在，缺的只是这三个入口。
        cap_canvas = QLabel('画布')
        cap_canvas.setProperty('role', 'section')
        rv.addWidget(cap_canvas)
        c1 = QHBoxLayout()
        c1.setSpacing(T.SPACE['xs'])
        self._save_layout_btn = QPushButton('💾 保存布局(JSON)')
        self._save_layout_btn.setToolTip(
            '把手排好的块位置存成 .json（Ctrl+S）；格式与旧版一致')
        self._save_layout_btn.clicked.connect(self.save_layout)
        c1.addWidget(self._save_layout_btn)
        self._load_layout_btn = QPushButton('📂 加载布局')
        self._load_layout_btn.setToolTip('读回一份布局 .json（Ctrl+O）')
        self._load_layout_btn.clicked.connect(self.load_layout)
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
        self._export_flow_btn = QPushButton('⤓ 导出控制流 .rpy')
        self._export_flow_btn.setToolTip(
            '按图生成 Ren\'Py 控制流脚本：每个模块一个 `<label>_control`，'
            '里面 call 原 label，再按连线写 jump/call/菜单/条件')
        self._export_flow_btn.clicked.connect(self.export_control_flow)
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
        split.addWidget(right)

        # 右栏比上一版**更窄**（420 → 340）：用户要求给这一组按钮腾出位置，
        # 同时控制台仍然是这一栏里最大的一块（它要显示导入/分析的流水）。
        split.setSizes([1080, 340])
        try:
            left.setMinimumWidth(200)
            right.setMinimumWidth(300)
            right.setMaximumWidth(420)
        except Exception:                     # noqa: BLE001
            swallow('main_window._build_graph_page')
        return page

    # ---------------- 第 0 页：控制台 / 当前模块 ----------------

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

    def _show_analysis_text(self, text: str):
        """
        分析结论 → 控制台。

        旧版是"**先清空**再写入"（`analysis_text.delete(1.0, END)`），
        因为分析结果是"当前状态"而不是流水账；导入/项目那些是流水账。
        """
        try:
            self._console.setPlainText(str(text or ''))
            bar = self._console.verticalScrollBar()
            bar.setValue(0)
        except Exception:                    # noqa: BLE001
            swallow('main_window._show_analysis_text')

    def _selected_node(self):
        nid = self._canvas.selected_node_id()
        return (self.graph.nodes or {}).get(nid) if (self.graph and nid) else None

    def _on_canvas_select(self, nid):
        """左键选中模块 → 右栏"当前模块"跟着变（旧版 `_update_chapter_label`）。"""
        # 命令对象要读 `selected_node`（旧版视图接口），这里跟着同步
        self.selected_node = nid
        self.selected_edge = None
        node = (self.graph.nodes or {}).get(nid) if (self.graph and nid) else None
        name = getattr(node, 'label_name', '') if node else ''
        fp = getattr(node, 'file_path', None) if node else None
        if node is None:
            self._chapter_label.setText('（在图上点一个模块）')
        elif fp:
            self._chapter_label.setText(f'{name}　·　{Path(str(fp)).name}')
        else:
            self._chapter_label.setText(
                f'{name}　·　（这一章的文件还没导入：别的章节跳到了它）')
        for btn in (getattr(self, '_open_script_btn', None),
                    getattr(self, '_edit_vars_btn', None)):
            if btn is not None:
                btn.setEnabled(node is not None)

    def _on_connect_requested(self, from_id, to_id):
        """Shift + 左键从一个块拖到另一个块 → 弹旧版那张"选择跳转类型"对话框。"""
        self.add_edge(from_id=from_id, to_id=to_id)


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
        self._crumb_hint = QLabel('')
        self._crumb_hint.setProperty('role', 'caption')
        bh.addWidget(self._crumb_hint, 1)
        # —— 增 / 删卡片（结构编辑）——
        # 用户 2026-10-03："只能够修改，不能增删，还是没用！"
        self._insert_btn = QPushButton('＋ 插入卡片')
        self._insert_btn.setToolTip('在当前这一拍后面插入一张空卡片（Ctrl+Insert）')
        self._insert_btn.clicked.connect(self.insert_card)
        bh.addWidget(self._insert_btn)
        self._insert_before_btn = QPushButton('＋ 插到前面')
        self._insert_before_btn.setToolTip('在当前这一拍**前面**插入一张空卡片')
        self._insert_before_btn.clicked.connect(lambda: self.insert_card(after=False))
        bh.addWidget(self._insert_before_btn)
        self._restore_btn = QPushButton('↩ 撤销删除')
        self._restore_btn.setToolTip('把刚删掉的那张卡片放回原位')
        self._restore_btn.clicked.connect(self.restore_card)
        self._restore_btn.setEnabled(False)
        bh.addWidget(self._restore_btn)
        self._delete_btn = QPushButton('🗑 删除这张卡片')
        self._delete_btn.setToolTip('删掉当前这一拍（只改内存，导出才落盘）')
        self._delete_btn.setProperty('variant', 'danger')
        self._delete_btn.clicked.connect(self.delete_card)
        bh.addWidget(self._delete_btn)
        outer.addWidget(bar)

        split = QSplitter(Qt.Horizontal)

        # —— 左：章节（拍）/ 参与角色 ——
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
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
        split.addWidget(left)

        # —— 右：卡片（这一拍）——
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(T.SPACE['xs'])
        cap3 = QLabel('卡片界面（这一拍）')
        cap3.setProperty('role', 'section')
        rv.addWidget(cap3)
        self._panel = BeatCardEditor(symbols=None, loader=self._loader,
                                     on_pick_image=self._pick_image,
                                     on_pick_asset=self._pick_asset_for)
        # 立绘缩略图的路径解析：真源是资源表的 Path 列，只有会话拿得到
        self._panel.set_path_resolver(self._session.path_of_image)
        # 卡片里那两个按钮直接连过来（用户要的"保存 / 撤销这张卡片"）
        self._panel.save_requested.connect(self.save_current_beat)
        self._panel.undo_requested.connect(self.undo_current_beat)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._panel)
        rv.addWidget(scroll, 1)
        split.addWidget(right)

        split.setSizes([260, 900])
        outer.addWidget(split, 1)
        return page

    # ---------------- 工具栏（两组，随页切换）----------------

    def _build_toolbars(self):
        tb_graph = QToolBar('剧情结构')
        tb_graph.setMovable(False)
        tb_graph.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb_graph)
        tb_card = QToolBar('卡片编辑')
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
            a.triggered.connect(fn)
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

        # —— 第一屏：剧情结构 ——
        #
        # ⚠ 两条路**必须分开**（用户 2026-10-03 纠正）：
        #   · 「打开项目」= 资源表（xlsx）那条路：立绘/音乐候选、缩略图、
        #     参与角色。**它不往剧情图里放任何东西**。
        #   · 「导入章节」= 剧情图那条路：图里只有用户自己导入的章节。
        # 以前把两者写成一句话（"打开项目 → 建出结构图"），40 个脚本一次性
        # 灌进图里变成 51 个块 —— 用户："这还选个毛线啊！看都要看瞎了！"
        act('📁 打开项目', self.open_project,
            '只加载资源表（xlsx：立绘 / 音乐 / 语音 / 变换 / 角色）'
            '与缩略图 —— **不往剧情图里放任何东西**')
        act('➕ 导入章节 .rpy', self.import_chapters,
            '可多选；图里只出现你导入过的章节（累加，不覆盖已导入的）',
            primary=True)
        act('🔁 重载已导入章节', self.reload_imported_chapters,
            '磁盘上的 .rpy 改过之后，重新解析**已导入的那些**（范围不变）')
        act('🔗 补全相关章节', self.complete_related_chapters,
            '把图上"被跳到、但文件还没导入"的章节一起导进来 → 连线才画得出')
        act('🗑 清空剧情图', self.clear_graph,
            '只清图（资源表 / 项目设置都不动）')
        tb_graph.addSeparator()
        act('➕ 新章节', self.add_node, '新建一个章节模块（label 名校验过）')
        act('🔗 连线', self.add_edge, '在两个模块之间连 jump / call / 菜单')
        act('⤢ 自动布局', self._canvas.auto_layout, '把模块重新排好（分层 + 减交叉）')
        act('🔍 适应窗口', self._canvas.fit_to_content, '整张图缩放到看得全')
        self._open_action = act('✎ 编辑这一章的卡片', self._open_selected_in_card,
                                '进卡片编辑器改选中模块那一章（也可双击模块）')
        tb_graph.addSeparator()
        act('🔎 检查逻辑问题', self.check_logic, '可达性 / 死分支；结论写在右侧并在图上标红')
        act('📊 变量分析', self.show_analysis, '逐节点的变量区间与操作明细（只读）')
        act('🩺 项目体检', self.show_health, '三级校验，只读检查，不写盘')
        act('✎ 直接编辑单章', self._on_load,
            '不进剧情图，只把一章 .rpy 打开到卡片界面（改一章不想建图时用）')
        act('🔄 重读资源表', self.rescan_project,
            '改了 xlsx 之后重读资源表（下拉候选 / 缩略图）；剧情图不动')
        act('❓ 使用说明', self.show_help, '操作说明与快捷键（独立窗口，不占界面）')

        # —— 第二屏：卡片编辑 ——
        self._act_tb = tb_card
        self._back_action = act('← 返回剧情结构', self._back_to_graph,
                                '回到第一屏（Esc）')
        self._save_action = act('💾 保存这一拍', self.save_current_beat,
                                '把卡片改动提交到当前拍（Ctrl+S）', primary=True)
        self._undo_action = act('↩ 撤销修改', self.undo_current_beat,
                                '恢复成上一次保存时的样子（Ctrl+Z，只这一拍）')
        self._prev_action = act('◀ 上一拍', self.prev_beat, 'Alt+↑')
        self._pos = QLabel('— / —')
        self._pos.setProperty('role', 'caption')
        self._pos.setMinimumWidth(72)
        self._pos.setAlignment(Qt.AlignCenter)
        tb_card.addWidget(self._pos)
        self._next_action = act('下一拍 ▶', self.next_beat, 'Enter / Alt+↓')
        act('⤓ 导出 .rpy', self.export_script, '生成/增量补丁写回（过安全门禁）')
        act('⤒ 从剧本表写回', self.export_from_table,
            '选一张演出表 .xlsx → 写回 .rpy（导出前会先给你看门禁结论）')
        act('🎬 演出预览', self.open_stage_preview, '按卡片效果"跑起来"')
        act('❓ 使用说明', self.show_help, '操作说明与快捷键（独立窗口，不占界面）')

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
        """面包屑：`剧情结构 › 文件名 › 第 i / n 拍`。"""
        if not hasattr(self, '_crumb'):
            return
        name = Path(self._file_path).name if self._file_path else ''
        n = len(self._session)
        i = self._loaded_idx
        if self._page == self.PAGE_GRAPH:
            self._crumb.setText('剧情结构')
            self._crumb_hint.setText(
                f'当前模块：{name}' if name else '（双击一个模块进卡片编辑器）')
        else:
            self._crumb.setText(f'剧情结构 › {name or "（未载入）"}')
            self._crumb_hint.setText(
                f'第 {i + 1 if i >= 0 else 0} / {n} 拍'
                f'　·　改完按「💾 保存这一拍」，'
                f'「← 返回剧情结构」回第一屏')

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
            # 没选块：把当前会话那一章对应的模块找出来
            target = Path(self._file_path).resolve()
            for node in (self.graph.nodes or {}).values():
                fp = getattr(node, 'file_path', None)
                if fp and Path(str(fp)).resolve() == target:
                    nid = node.id
                    break
        if nid is None:
            self._status('先在图上点一个模块（或双击它），再进卡片编辑器。')
            return
        self._on_node_open(nid)

    # ------------------------------------------------------------------
    #  剧情变量（双击模块 / 右栏按钮）
    # ------------------------------------------------------------------

    def _edit_selected_vars(self):
        """右栏「🧩 编辑剧情变量…」：编辑当前选中模块的剧情变量。"""
        nid = self._canvas.selected_node_id()
        if nid is None:
            self._status('先在图上点一个模块，再编辑它的剧情变量。')
            return
        self.edit_node_ops(nid)

    def edit_node_ops(self, nid):
        """
        **双击模块 = 编辑剧情变量**（旧版 `on_double_click` →
        `edit_operations_dialog` 就是这个动作）。

        用户 2026-10-03：
        > 这个所谓的"技术代码"，它不是变量，是具体的实现细节！……
        > 我不要再在变量的窗口上看到任何内部技术代码的名字！编辑变量是为了
        > 让策划和编剧调整和编排剧情分支用的地方！不是拿来填细节的！！！

        所以对话框里**只有剧情变量**（`session.node_variables` 一条技术代码
        都不返回）；保存走 `session.set_node_variables`，技术代码原位保留。
        """
        from storygraph.ui_qt.dialogs import NodeOpsDialog

        info = self._session.node_variables(self.graph, nid)
        if not info.get('ok'):
            self._status('这个模块不在了（图可能刚被清空）。')
            return
        dlg = NodeOpsDialog(self, label_name=info['label'],
                            variables=info['variables'],
                            tech_count=info['tech_count'],
                            on_check=self._session.check_variable_syntax)
        if dlg.exec() != QDialog.Accepted:
            return
        before = list(getattr(self.graph.nodes.get(nid), 'operations', []) or [])
        res = self._session.set_node_variables(self.graph, nid, dlg.variables)
        if not res.get('ok'):
            self._status(f'保存失败：{res.get("error")}')
            return
        # 变量改动也进历史（Ctrl+Z 能撤回来）。这里操作**已经生效**，
        # 所以只记历史、不重复 execute；改动前的文本用 `old_ops` 显式给，
        # 否则命令会把"撤销"恢复成新值。
        try:
            from storygraph.graph.commands import UpdateNodeOperationsCommand
            cmd = UpdateNodeOperationsCommand(
                self, nid, list(self.graph.nodes[nid].operations),
                old_ops=before)
            self._record_applied(cmd)
        except Exception:                     # noqa: BLE001
            swallow('main_window.edit_node_ops')
        self._set_graph(self.graph)          # 摘要 / 连线可能变了
        self._log(f'[变量] {info["label"]}：剧情变量 {res["variables"]} 条'
                  f'（另有 {res["kept_tech"]} 条实现细节原样保留）')
        self._status(f'已保存「{info["label"]}」的剧情变量'
                     f'（{res["variables"]} 条）。')

    def _on_zoom_changed(self, percent):
        try:
            self._zoom_label.setText(f'{percent}%')
        except Exception:                    # noqa: BLE001
            swallow('main_window._on_zoom_changed')

    # ------------------------------------------------------------------
    #  结构分析（静态）
    # ------------------------------------------------------------------

    def _write_analysis(self, text: str):
        """分析结论写进控制台（旧版也是"先清空再写"）。"""
        self._show_analysis_text(text)

    def _run_analysis(self) -> dict:
        if self.graph is None or not getattr(self.graph, 'nodes', None):
            self._status('剧情图还是空的 —— 先「➕ 导入章节 .rpy」加几章再分析。')
            return {}
        try:
            return self._session.analyze_graph_problems(self.graph)
        except Exception as e:               # noqa: BLE001
            self._status(f'分析失败：{e}')
            return {}

    def check_logic(self):
        """
        「🔎 检查逻辑问题」：只列问题 + 在图上标红/打虚线。

        以前这是一个**弹窗**（看完得点确定，还没法对照图）；现在结论写进
        右上控制台，且**画回图上** —— 静态分析的价值就在于"指出哪个模块有问题"，
        光给一段文字等于让人自己去几十个块里找。
        """
        res = self._run_analysis()
        if not res:
            return
        self._canvas.set_analysis(entry_id=res.get('entry_id'),
                                  problem_ids=res.get('problem_ids') or [],
                                  independent_ids=res.get('independent_ids') or [])
        warnings = [str(w) for w in res.get('warnings') or []]
        notes = [str(w) for w in res.get('notes') or []]
        lines = ['[检查逻辑问题] ' + str(res.get('summary', '')), '']
        if not warnings:
            lines.append('✔ 没有发现"从任何入口都走不到"的模块'
                         '（分支条件与入口判定都在合理范围内）。')
        else:
            lines.append('问题清单（图上已标红）：')
            lines.append('')
            lines += warnings
        if notes:
            lines.append('')
            lines.append(f'另有 {len(notes)} 个模块只是"没有入边 / '
                         f'从本入口走不到"，但从别的入口可达 —— '
                         f'多为独立章节、被菜单或 `call screen` 进入，'
                         f'图上打灰虚线，**不算问题**：')
            lines += notes[:20]
            if len(notes) > 20:
                lines.append(f'… 还有 {len(notes) - 20} 个')
        self._write_analysis('\n'.join(lines))
        n = len(res.get('problem_ids') or [])
        self._status('✔ 检查完成：' + (f'{n} 个模块真断了（已标红）'
                                      if n else '没有发现结构断裂'))

    def show_analysis(self):
        """「📊 变量分析」：逐节点区间明细（同样画回图上）。"""
        res = self._run_analysis()
        if not res:
            return
        self._canvas.set_analysis(entry_id=res.get('entry_id'),
                                  problem_ids=res.get('problem_ids') or [],
                                  independent_ids=res.get('independent_ids') or [])
        self._write_analysis(res.get('text', ''))
        self._status('✔ 分析完成（只读，没有改动任何东西）')

    def clear_analysis(self):
        """清掉图上的分析标记与控制台里的结论（回到"未分析"的样子）。"""
        self.clear_console()
        self._canvas.clear_analysis()
        self._status('已清空分析结论与图上的标记。')

    # ------------------------------------------------------------------
    #  资源表（xlsx）—— 与剧情图**无关**的那条路
    # ------------------------------------------------------------------

    def open_project(self):
        """选项目根 → 只加载资源表（xlsx）。**不往剧情图里放任何东西。**"""
        start = str(self._project_root or Path.cwd())
        root = QFileDialog.getExistingDirectory(
            self, '选择项目根目录（含 game/ 与资源表）', start)
        if not root:
            return
        self.open_project_path(root)

    def open_project_path(self, root):
        """
        打开项目 = **只为资源管理**（用户 2026-10-03 明确）。

        > 所谓的导入项目只是为了表格的 xlsx 的资源管理系统！
        > 不是把整个项目都排在剧情图上面！！！

        所以这里只做三件事：记住项目根、读资源表、把符号表（立绘/音乐/
        角色候选）刷出来。**图一个节点都不加** —— 想让哪一章出现在剧情图上，
        用「➕ 导入章节 .rpy」。
        """
        if not root:
            return None
        self._project_root = Path(root)
        self._session.project_root = self._project_root
        self._loader.project_root = self._project_root
        # 引擎壳（`_EngineShim`）也有 `project_root`；顺手同步一下，
        # 免得它按老路径懒加载时拿不到项目（那是"添加立绘没反应"的另一半）。
        if getattr(self.engine, 'project_root', None) is None:
            try:
                self.engine.project_root = self._project_root
            except Exception:                 # noqa: BLE001
                swallow('main_window.open_project_path')
        res = self._session.load_assets()
        try:
            self._panel.set_symbols(self._session.symbols)
            self._refresh_asset_choices()
        except Exception:                       # noqa: BLE001
            swallow('main_window.open_project_path')
        if not res.get('ok'):
            msg = (f'[项目] 读不到资源表：{res.get("error") or "未知原因"}'
                   f'（剧情图不受影响；下拉候选会是空的）')
            self._log(msg)
            self._status(msg)
            return res
        c = res['counts']
        # 旧版的这一行原样搬回来（用户 2026-10-03："导入的时候没有反馈"）：
        # 图片/音乐/音效/语音/变换/角色的数量要看得见。
        self._log(
            f'[项目] 已加载 {self._project_root.name}：'
            f'图片 {c["images"]} · 音乐 {c["music"]} · 音效 {c["sounds"]} · '
            f'语音 {c["voices"]} · 变换 {c["transforms"]} · 角色 {c["characters"]}'
            f'（只影响资源表，剧情图不受影响）')
        self._status(
            f'✔ 已打开项目 {self._project_root.name}（资源表）—— '
            f'图片 {c["images"]} · 音乐 {c["music"]} · 音效 {c["sounds"]} · '
            f'语音 {c["voices"]} · 角色 {c["characters"]}；'
            f'剧情图**不受影响**，要加章节请用「➕ 导入章节 .rpy」')
        return res

    def rescan_project(self):
        """
        重读资源表（xlsx 改过之后用）。**剧情图的节点与连线一律不动** ——
        这是"资源系统"和"剧情图"两条路的边界。
        """
        if not self._project_root:
            QMessageBox.information(self, '提示', '先选择项目根目录。')
            return
        self.open_project_path(self._project_root)

    # ------------------------------------------------------------------
    #  剧情图的范围：**只有用户导入过的章节**
    # ------------------------------------------------------------------

    def import_chapters(self):
        """「➕ 导入章节 .rpy」：多选 → 累加进剧情图。"""
        start = str(Path(self._project_root or '.') / 'game')
        paths, _ = QFileDialog.getOpenFileNames(
            self, '选择要导入剧情图的章节脚本（可多选）', start,
            "Ren'Py 脚本 (*.rpy);;所有文件 (*)")
        if not paths:
            return
        self.import_chapter_files(paths)

    def import_chapter_files(self, paths) -> dict:
        """
        把选中的 `.rpy` 加进剧情图（**累加**）。语义全在
        `EditorSession.import_chapters`，这里只管把结果塞进画布**并写进控制台**。

        用户 2026-10-03："导入的时候没有反馈！根本不知道导入了什么……"
        所以这里**逐个文件**报"来了几个模块 / 几条连线"，而不是只报一个总数。
        """
        old_graph = self.graph.copy() if self.graph is not None else None
        res = self._session.import_chapters(paths, self.graph)
        if not res.get('ok'):
            msg = f'[导入] 失败：{res.get("error") or "未知原因"}'
            self._log(msg)
            self._status(msg)
            return res
        # 导入也进历史：一次导入几十个文件之后发现导错了，Ctrl+Z 就能回到
        # 导入前的图（旧版是 `ImportRenPyCommand`/`ReplaceGraphCommand`）。
        try:
            from storygraph.graph.commands import ReplaceGraphCommand
            if old_graph is None:
                self._set_graph(res['graph'])
            else:
                self._run_command(ReplaceGraphCommand(self, res['graph'],
                                                      old_graph))
        except Exception:                       # noqa: BLE001
            self._set_graph(res['graph'])
        try:
            self._panel.set_symbols(self._session.symbols)
            self._refresh_asset_choices()
        except Exception:                       # noqa: BLE001
            swallow('main_window.import_chapter_files')

        # —— 逐个文件报告（导入了什么，一眼看得见）——
        by_file: dict = {}
        for node in (res['graph'].nodes or {}).values():
            fp = getattr(node, 'file_path', None)
            if fp:
                by_file.setdefault(str(Path(str(fp))), []).append(
                    getattr(node, 'label_name', ''))
        for p in res['files']:
            labels = by_file.get(str(Path(str(p))), [])
            piece = ('、'.join(labels[:6])
                     + (f' 等 {len(labels)} 个' if len(labels) > 6 else ''))
            self._log(f'[导入] {Path(str(p)).name} → {len(labels)} 个模块'
                      + (f'（{piece}）' if piece else '（这个文件里没有 label）'))
        self._log(f'[导入] 合计：新增模块 {res["added_nodes"]} 个 / '
                  f'连线 {res["added_edges"]} 条；'
                  f'图上一共 {res["nodes"]} 个模块 · {res["edges"]} 条连线'
                  f'（已导入 {len(self._session.graph_files(res["graph"]))} 章）')
        for w in (res.get('warnings') or [])[:5]:
            self._log(f'[导入] ⚠ {w}')
        for e in (res.get('errors') or [])[:5]:
            self._log(f'[导入] ✕ {e}')

        names = '、'.join(Path(p).name for p in res['files'][:3])
        more = f' 等 {len(res["files"])} 个文件' if len(res['files']) > 3 else ''
        self._status(
            f'✔ 已导入 {len(res["files"])} 章（{names}{more}）：'
            f'新增模块 {res["added_nodes"]} 个 / 连线 {res["added_edges"]} 条，'
            f'图上共 {res["nodes"]} 个模块 · {res["edges"]} 条连线')
        # **"我连线呢？"**（用户 2026-10-03）：导入的章节之间没有跳转时，
        # 图里确实一条线都没有 —— 因为串起它们的是**另一个文件**。
        # 这里必须把"还缺哪个文件"说出来，并给出一键补全的入口。
        self._report_missing_chapters(res['graph'])
        self._set_page(self.PAGE_GRAPH)
        return res

    # ------------------------------------------------------------------
    #  画布：保存 / 加载布局 + 导出控制流（旧版右栏那一组）
    # ------------------------------------------------------------------

    def save_layout(self, target=None):
        """
        保存布局 JSON（Ctrl+S）。没给路径就弹「另存为」。

        语义在 `EditorSession.save_layout`（格式与旧版逐字段一致）。
        """
        if target is None:
            if self.graph is None or not getattr(self.graph, 'nodes', None):
                self._status('剧情图是空的，没有布局可保存。')
                return ''
            guess = str((self._project_root or Path.cwd())
                        / 'storygraph_layout.json')
            target, _ = QFileDialog.getSaveFileName(
                self, '保存布局（JSON）', guess, 'JSON 文件 (*.json)')
            if not target:
                return ''
        res = self._session.save_layout(self.graph, target)
        if not res.get('ok'):
            self._log(f'[布局] 保存失败：{res.get("error")}')
            self._status(f'保存布局失败：{res.get("error")}')
            return ''
        name = Path(res['path']).name
        self._log(f'[布局] 已保存 {res["nodes"]} 个模块 / {res["edges"]} 条连线'
                  f' → {res["path"]}')
        self._status(f'💾 布局已保存：{name}（{res["nodes"]} 块 / '
                     f'{res["edges"]} 线）')
        return res['path']

    def load_layout(self, source=None):
        """
        加载布局 JSON（Ctrl+O）：**整图替换**成文件里的那张图。

        会先问一声 —— 图上的导入结果会被换掉（内存里，磁盘不动）。
        """
        if source is None:
            start = str(self._project_root or Path.cwd())
            source, _ = QFileDialog.getOpenFileName(
                self, '加载布局（JSON）', start, 'JSON 文件 (*.json)')
            if not source:
                return ''
        if self.graph is not None and getattr(self.graph, 'nodes', None):
            if QMessageBox.question(
                    self, '加载布局',
                    '加载布局会把当前剧情图**整张换掉**（只改内存，磁盘不动）。'
                    '\n\n确定继续？') != QMessageBox.Yes:
                return ''
        old = self.graph.copy() if self.graph is not None else None
        res = self._session.load_layout(source)
        if not res.get('ok'):
            self._log(f'[布局] 加载失败：{res.get("error")}')
            self._status(f'加载布局失败：{res.get("error")}')
            return ''
        try:
            from storygraph.graph.commands import ReplaceGraphCommand
            if old is None:
                self._set_graph(res['graph'])
            else:
                self._run_command(ReplaceGraphCommand(self, res['graph'], old))
        except Exception:                       # noqa: BLE001
            self._set_graph(res['graph'])
        # 加载布局是"我要看这张新图" —— 显式适应窗口。
        # （连线/撤销那种重建默认**保住**缩放，见 `GraphCanvas.set_graph`；
        # 这里是少数几个"换了看什么"的场景，所以主动 fit 一次。）
        try:
            self._canvas.fit_to_content()
        except Exception:                       # noqa: BLE001
            swallow('main_window.load_layout')
        name = Path(res['path']).name
        self._log(f'[布局] 已加载 {name}：{res["nodes"]} 个模块 / '
                  f'{res["edges"]} 条连线')
        self._status(f'📂 已加载布局 {name}（{res["nodes"]} 块 / '
                     f'{res["edges"]} 线）')
        return res['path']

    def export_control_flow(self, target=None, wrapper=None):
        """
        导出 Ren'Py 控制流脚本（每个模块一个 `<label>_control`）。

        用户 2026-10-04："我的旧版本有导出控制流图.rpy文件，label末尾加
        _control 的那个……现在这个新版本呢？"
        """
        if self.graph is None or not getattr(self.graph, 'nodes', None):
            self._status('剧情图是空的，没有可导出的控制流。')
            return ''
        if target is None:
            start_dir = (self._project_root / 'game') if self._project_root \
                else Path.cwd()
            guess = str(Path(start_dir) / 'story_graph_control.rpy')
            target, _ = QFileDialog.getSaveFileName(
                self, "导出控制流（Ren'Py）", guess, "Ren'Py 脚本 (*.rpy)")
            if not target:
                return ''
        res = self._session.export_control_flow(self.graph, target,
                                                wrapper=wrapper)
        if not res.get('ok'):
            self._log(f'[控制流] 导出失败：{res.get("error")}')
            self._status(f'导出控制流失败：{res.get("error")}')
            return ''
        name = Path(res['path']).name
        self._log(f'[控制流] 已生成 {res["count"]} 个标签'
                  f'（{res.get("mode", "")}）→ '
                  f'{res["path"]}（原 label 末尾要能正常 return）')
        self._status(f'⤓ 控制流已导出：{name}（{res["count"]} 个标签，'
                     f'{res.get("mode", "")}）')
        for w in (res.get('warnings') or []):
            self._log(f'[控制流] ⚠ {w}')
        return res['path']

    def _refresh_choices_now(self):
        """旧版「刷新下拉候选（从资源表重读）」：手动的那个入口。"""
        self._refresh_asset_choices()
        n = 0
        for field in self.ASSET_FIELD_KINDS:
            combo = self._panel._widgets.get(field)
            # 背景/音乐/语音是下拉；音效那一格是文本框（没有候选表），
            # 所以这里只统计真下拉。
            n += combo.count() if isinstance(combo, QComboBox) else 0
        self._log(f'[资源] 已从资源表重读下拉候选（背景/音乐/语音共 {n} 条）')
        self._status(f'🔄 下拉候选已刷新（共 {n} 条）')

    # ------------------------------------------------------------------
    #  更多工具：多引擎导出 / 资产统计 / 协作 / 校验
    # ------------------------------------------------------------------

    def _show_more_tools(self):
        """把次要入口收进一个菜单（按钮本身不动图，只是入口）。"""
        menu = QMenu(self)
        menu.addAction('⤓ 导出到其他引擎（Unity / Godot / JSON / CSV）…',
                       self.export_other_engine)
        menu.addAction('📈 资产 / 角色统计…', self.show_asset_overview)
        menu.addAction('✅ 校验当前章节（三级校验）', self.validate_current_script)
        menu.addAction('➕ 补登缺失资源（扫描磁盘 → 写进资产表）…',
                       self.register_missing_assets)
        menu.addAction('⤓ 导出控制流 .rpy（按章节图，补 _control）…',
                       lambda: self.export_control_flow(wrapper=True))
        menu.addAction('⤓ 导出控制流 .rpy（按控制流图原样导出）…',
                       lambda: self.export_control_flow(wrapper=False))
        menu.addSeparator()
        menu.addAction('👥 协作库（SQLite ↔ Excel）…', self.show_collab)
        btn = getattr(self, '_more_btn', None)
        pos = (btn.mapToGlobal(btn.rect().bottomLeft()) if btn is not None
               else self.mapToGlobal(self.rect().center()))
        menu.exec(pos)

    def export_other_engine(self, engine=None):
        """
        多引擎导出：`pipeline.engine_exporters.export_ir`。

        没给 `engine` 就先问一句（Unity / Godot / 中立 JSON / CSV / Ren'Py 清单），
        再选输出目录。
        """
        from PySide6.QtWidgets import QInputDialog
        if not self._session.beats:
            self._status('先在卡片页载入一章，再导出到其他引擎。')
            return ''
        if engine is None:
            engine, ok = QInputDialog.getItem(
                self, '导出到其他引擎', '目标格式：',
                ['unity', 'godot', 'json', 'csv', 'renpy'], 0, False)
            if not ok or not engine:
                return ''
        out_dir = QFileDialog.getExistingDirectory(
            self, '导出到哪个目录',
            str(self._project_root or Path.cwd()))
        if not out_dir:
            return ''
        res = self._session.export_ir(engine, out_dir)
        if not res.get('ok'):
            self._log(f'[导出] 其他引擎（{engine}）失败：{res.get("error")}')
            self._status(f'导出失败：{res.get("error")}')
            return ''
        names = '、'.join(Path(f).name for f in res['files'][:4])
        self._log(f'[导出] {engine}：{len(res["files"])} 个文件 → '
                  f'{res["out_dir"]}（{names}）')
        self._status(f'✔ 已导出 {engine} 格式（{len(res["files"])} 个文件）')
        return res['out_dir']

    def show_asset_overview(self):
        """资产 / 角色统计（只读）→ 写进控制台 + 弹一个可滚动的明细窗。"""
        res = self._session.asset_overview()
        if not res.get('ok'):
            self._log(f'[资产] {res.get("error")}')
            self._status(res.get('error') or '拿不到资产统计')
            return
        stats = res.get('statistics') or {}
        lines = ['【资产统计】']
        for key, value in stats.items():
            lines.append(f'  {key}：{value}')
        cats = res.get('categories') or {}
        if cats:
            lines.append('【分类】')
            for key, value in list(cats.items())[:20]:
                lines.append(f'  {key}：{value}')
        tops = res.get('top_characters') or []
        if tops:
            lines.append('【角色台词量】')
            for row in tops:
                if isinstance(row, dict):
                    lines.append(f'  {row.get("name") or row.get("character")}：'
                                 f'{row.get("count") or row.get("lines")}')
                else:
                    lines.append(f'  {row}')
        for title, key in (('未定义角色', 'undefined'),
                           ('没出过场的角色', 'unused_characters'),
                           ('未使用的资产', 'unused_assets'),
                           ('断裂引用', 'broken')):
            rows = res.get(key) or []
            lines.append(f'【{title}】{len(rows)} 条')
            for row in rows[:30]:
                if isinstance(row, dict):
                    label = (row.get('asset_id') or row.get('name')
                             or row.get('character') or row)
                    extra = row.get('category') or row.get('kind') or ''
                    lines.append(f'  {label}{("（" + str(extra) + "）") if extra else ""}')
                else:
                    lines.append(f'  {row}')
        text = '\n'.join(lines)
        self._log(f'[资产] 统计：{stats.get("images", "?")} 图 / '
                  f'{stats.get("music", "?")} 乐；未使用 '
                  f'{len(res.get("unused_assets") or [])} 条，断裂引用 '
                  f'{len(res.get("broken") or [])} 条')
        try:
            from storygraph.ui_qt.dialogs import TextViewDialog
            dlg = TextViewDialog(self, '资产 / 角色统计', text)
            dlg.exec()
        except Exception:                     # noqa: BLE001
            self._status('资产统计已写进控制台。')
        return text

    def register_missing_assets(self):
        """
        把磁盘上有、资产表里没登记的资源**补进资产表**（写前自动备份）。

        用户 2026-10-05："先查背景图为何没有被登记进去，然后，让背景图被登记进去。"
        """
        if not self._ensure_registry():
            return
        if QMessageBox.question(
                self, '补登缺失资源',
                '会扫描项目里的图片 / 音乐 / 音效，把**表里还没有的**追加进\n'
                f'{self._project_root}/game/asset_tables/（写前备份成 *.bak.xlsx，'
                '已登记的行一个都不动）。\n\n继续？') != QMessageBox.Yes:
            return
        res = self._session.register_missing_assets()
        if not res.get('ok'):
            self._log(f'[资源] 补登失败：{res.get("error")}')
            self._status(f'补登失败：{res.get("error")}')
            return
        kinds = '、'.join(f'{k} {v}' for k, v in (res.get('by_kind') or {}).items()
                          if v)
        if not res.get('added'):
            self._log('[资源] 磁盘上有的资源都已经登记过了，没有需要补的')
            self._status('没有需要补登的资源。')
            return
        self._refresh_asset_choices()
        self._log(f'[资源] 已补登 {res["added"]} 条（{kinds}）；'
                  f'备份 = {res.get("backup") or "（新建表）"}')
        self._status(f'✔ 已补登 {res["added"]} 条资源（{kinds}）')

    def validate_current_script(self):
        """
        三级校验当前章节（`pipeline.validate.validate_all`）→ 控制台。

        契约：它收的是 **beats**（不是路径），返回的 `ValidationReport` 有
        `errors` / `warnings` / `summary`；`Issue` 是 dict（level/code/
        severity/message/where/hint），**没有** `is_blocking`。
        """
        if not self._session.beats:
            self._status('还没有载入章节，没有可校验的内容。')
            return
        try:
            from storygraph.pipeline.validate import validate_all
            report = validate_all(self._session.beats,
                                  registry=self._registry_provider(),
                                  graph=self.graph,
                                  project_root=self._project_root)
        except Exception as e:                # noqa: BLE001
            self._log(f'[校验] 失败：{e}')
            self._status(f'校验失败：{e}')
            return
        errors = list(getattr(report, 'errors', []) or [])
        warnings = list(getattr(report, 'warnings', []) or [])
        name = Path(self._file_path).name if self._file_path else '（未命名）'

        def _msg(issue):
            if isinstance(issue, dict):
                return f"[{issue.get('level', '')}] {issue.get('message', '')}"
            return f'[{getattr(issue, "level", "")}] ' \
                   f'{getattr(issue, "message", issue)}'

        self._log(f'[校验] {name}：{len(errors)} 个错误 / {len(warnings)} 条提醒')
        for issue in (errors + warnings)[:20]:
            self._log(f'    · {_msg(issue)[:160]}')
        self._status(f'✅ 校验完成：{len(errors)} 个错误 / {len(warnings)} 条提醒'
                     '（明细见控制台）')

    def show_collab(self):
        """
        协作库入口（`collab.collab_store`：SQLite ↔ Excel）。

        刻意只做"登记 / 拉取 / 提交 / 看状态"四件事：冲突规则在
        `collab_store.push` 里（被锁、revision 更新一律**不静默覆盖**），
        界面不重复实现一遍。
        """
        from storygraph.ui_qt.dialogs import CollabDialog
        dlg = CollabDialog(self, session=self._session,
                           project_root=self._project_root,
                           log=self._log, status=self._status)
        dlg.exec()
        return dlg

    # ------------------------------------------------------------------
    #  图级撤销 / 重做（旧版 `history` + Ctrl+Z / Ctrl+Y）
    # ------------------------------------------------------------------
    #
    # 用户 2026-10-04："为何模块编辑器的功能缺失那么多？？？"
    # 旧版结构页有完整撤销栈（`graph.commands.HistoryManager` + 十来个
    # 命令），Qt 版**一个都没接** —— 于是删一个块只能靠重新导入。
    # 命令对象全在（`commands.py`），它们要的"视图接口"就下面这几个方法。

    def _on_node_moved(self, nid, ox, oy, nx, ny):
        """
        拖完一个块 → 记进撤销栈（用户 2026-10-05："将撤销做好"）。

        模型里的坐标在拖动过程中已经被 `NodeItem.itemChange` 实时写好了，
        所以这里**不重复 execute**、只记历史（`_record_applied`）——否则
        命令的 `old_ops/old_x` 会记成拖完之后的坐标，撤销等于没动。
        """
        from storygraph.graph.commands import MoveNodeCommand
        self._record_applied(MoveNodeCommand(self, nid, ox, oy, nx, ny))
        # 拖远了就把场景矩形撑大：否则那块"看得见、到不了"
        # （平移范围由场景矩形决定），表现为"拖画布时灵时不灵"。
        try:
            self._canvas.note_content_changed()
        except Exception:                     # noqa: BLE001
            swallow('main_window._on_node_moved')

    def on_graph_structure_changed(self):
        """命令改完图结构后的回调（命令对象约定的视图接口之一）。"""
        self._set_graph(self.graph)

    def on_graph_content_changed(self, nid=None):
        """命令改了块内容（变量）后的回调。"""
        self._set_graph(self.graph)

    def on_current_changed(self):
        """「设为当前章节」变了之后的回调。"""
        self._set_graph(self.graph)

    def update_node_position(self, nid):
        """命令把块的坐标改了 → 画布同步（整图重建，够快也够简单）。"""
        self._set_graph(self.graph)

    def _apply_layout_data(self, data):
        """`LoadLayoutCommand` 要的接口：把布局 dict 变成当前的图。"""
        from storygraph.app.editor import graph_from_layout_dict
        self._set_graph(graph_from_layout_dict(data))

    def _run_command(self, command):
        """执行一条图命令并记进历史（所有结构改动都要走这里）。"""
        try:
            self._history.execute_command(command)
        except Exception as e:                # noqa: BLE001
            self._log(f'[撤销] 这条操作没能记进历史：{e}')
            return False
        self._sync_history_buttons()
        return True

    def undo_graph(self):
        """图级撤销（Ctrl+Z，结构页）。"""
        if not self._history.undo_stack:
            self._status('没有可撤销的图操作。')
            return
        self._history.undo()
        self._set_graph(self.graph)
        self._sync_history_buttons()
        self._log('[撤销] 已撤销上一步图操作')
        self._status('↩ 已撤销上一步图操作')

    def redo_graph(self):
        """图级重做（Ctrl+Y，结构页）。"""
        if not self._history.redo_stack:
            self._status('没有可重做的图操作。')
            return
        self._history.redo()
        self._set_graph(self.graph)
        self._sync_history_buttons()
        self._log('[重做] 已重做上一步图操作')
        self._status('↪ 已重做上一步图操作')

    def _sync_history_buttons(self):
        for name, stack in (('_undo_graph_btn', self._history.undo_stack),
                            ('_redo_graph_btn', self._history.redo_stack)):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.setEnabled(bool(stack))

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
        except Exception:                     # noqa: BLE001
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

    def _report_missing_chapters(self, graph):
        """把"被跳到、但文件还没导入"的章节写进控制台（并提示补全）。"""
        try:
            info = self._session.missing_chapter_files(graph)
        except Exception:                     # noqa: BLE001
            return
        files = info.get('files') or []
        unknown = info.get('unknown') or []
        if files:
            names = '、'.join(Path(p).name for p in files[:4])
            more = f' 等 {len(files)} 个' if len(files) > 4 else ''
            self._log(f'[提示] 图里还有 {len(files)} 个章节文件没导入，'
                      f"它们和现在这些章节之间有跳转：{names}{more}"
                      f'　→　点工具栏「🔗 补全相关章节」一起导入，'
                      f'连线才会画出来（或右键空白 → 补全相关章节）')
        if unknown:
            names = '、'.join(unknown[:6])
            more = f' 等 {len(unknown)} 个' if len(unknown) > 6 else ''
            self._log(f'[提示] 有 {len(unknown)} 个跳转目标在项目里找不到'
                      f'对应文件（可能是脚本里写错了，或用了动态跳转）：'
                      f'{names}{more}')

    def complete_related_chapters(self):
        """
        「🔗 补全相关章节」：把"图上被跳到、但还没导入"的文件一起导进来。

        这样"导入 → 看到线"就闭环了：用户导入 `chapter1_1/1_2/1_3` 后，
        一点就把 `chapter_controller.rpy` 补进来，9 条连线立刻出现。
        """
        try:
            info = self._session.missing_chapter_files(self.graph)
        except Exception as e:                # noqa: BLE001
            self._status(f'查找相关章节失败：{e}')
            return
        files = list(info.get('files') or [])
        if not files:
            self._status('没有需要补全的章节 —— 图上被跳到的 label 都有来源了'
                         '（或者项目里根本找不到它们）。')
            return
        self._log(f'[补全] 导入相关章节 {len(files)} 个…')
        self.import_chapter_files(files)

    def reload_imported_chapters(self):
        """重新解析**已导入的那些**文件（范围不变，用于跟上磁盘上的改动）。"""
        files = self._session.graph_files(self.graph)
        if not files:
            self._status('剧情图还是空的 —— 先用「➕ 导入章节 .rpy」加几章。')
            return
        self._log(f'[重载] 重新解析已导入的 {len(files)} 个章节…')
        self.import_chapter_files(files)

    def clear_graph(self):
        """清空剧情图（**只清图**：资源表、项目根、会话都不动；可撤销）。"""
        n = len(getattr(self.graph, 'nodes', {}) or {})
        if not n:
            self._status('剧情图本来就是空的。')
            return
        from storygraph.graph.commands import ClearGraphCommand
        self._run_command(ClearGraphCommand(self))
        self._log(f'[清空] 剧情图已清空（原来 {n} 个模块）；'
                  f'资源表与项目设置没动。')
        self._status(f'已清空剧情图（{n} 个模块）。'
                     f'资源表与项目设置没动；要恢复点「↩ 撤销」或重新导入。')

    def remove_chapter_from_graph(self, nid):
        """把某个模块**所属的那一章**从图上移除（不动磁盘上的 .rpy）。"""
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        if node is None:
            return
        fp = getattr(node, 'file_path', None)
        if not fp:
            # 手工新建的块（没有源文件）→ 只删这一个（走命令，可撤销）
            from storygraph.graph.commands import RemoveNodeCommand
            self._run_command(RemoveNodeCommand(self, nid))
            self._log(f'[移除] {node.label_name}（没有源文件，只删这一个块）')
            self._status(f'已从图上移除「{node.label_name}」（它没有源文件）。')
            return
        name = Path(str(fp)).name
        siblings = sum(1 for n in (self.graph.nodes or {}).values()
                       if str(getattr(n, 'file_path', '') or '') == str(fp))
        if siblings > 1:
            if QMessageBox.question(
                    self, '从剧情图移除这一章',
                    f'「{name}」在图上有 {siblings} 个模块（同一个文件里的多个 '
                    f'label），要一起移除吗？\n\n'
                    f'（只改内存里的图，不动磁盘上的 .rpy）'
            ) != QMessageBox.Yes:
                return
        res = self._session.drop_chapters(self.graph, [fp])
        self._set_graph(self.graph)
        self._log(f'[移除] {name} → 移除 {res["removed_nodes"]} 个模块；'
                  f'图上还剩 {res["nodes"]} 个模块')
        self._status(f'已从剧情图移除「{name}」的 {res["removed_nodes"]} 个模块；'
                     f'图上还剩 {res["nodes"]} 个模块。')

    # ------------------------------------------------------------------
    #  载入单章 → 卡片（**不进图**）
    # ------------------------------------------------------------------

    def _on_load(self):
        start = str(Path(self._project_root or '.') / 'game')
        path, _ = QFileDialog.getOpenFileName(
            self, '直接编辑一章 .rpy（不进剧情图）', start,
            "Ren'Py 脚本 (*.rpy)")
        if path:
            self.load_script(path)

    def load_script(self, path=None, goto_card: bool = True):
        """
        载入一章（**语义全在 EditorSession.load**）。

        `goto_card`：载入是"要在卡片界面里改这一章"，所以默认**跳进卡片页**；
        构造窗口时的自动载入传 False —— 第一屏必须是剧情结构（用户明确要求）。
        """
        target = path or self._session.script_path
        if not target:
            return
        mark = self._swallow_mark()
        try:
            n = self._session.load(target)
            self._panel.set_symbols(self._session.symbols)
            self._refresh_asset_choices()
            self._reload_list()
            self._show_index(0)
            self._sync_graph_highlight()
            if goto_card:
                self._set_page(self.PAGE_CARD)
            else:
                self._update_crumb()
            self._log(f'[载入] {Path(target).name}：{n} 拍'
                      f'（{"进入卡片编辑器" if goto_card else "留在剧情结构页"}）')
            self._status(f'已载入 {Path(target).name}：{n} 拍')
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '载入失败', str(e))
        finally:
            self._report_swallowed(mark)

    # ------------------------------------------------------------------
    #  "静默失败"要看得见（见 `core.safeguard` 与不变量 58）
    # ------------------------------------------------------------------

    def _swallow_mark(self) -> int:
        """记一个"现在有多少条静默失败"，用来只看**本次操作**新增的那些。"""
        from storygraph.core import safeguard
        return safeguard.total()

    def _report_swallowed(self, mark: int = 0) -> int:
        """
        把本次操作**新产生**的静默失败写进控制台（没有就什么都不做）。

        为什么要在界面上露出来：审计那条结论（99 处 `except: pass`）的真正
        代价不是"代码难看"，而是**功能没反应时没有任何痕迹**。
        现在每一次载入 / 导出结束都会把它们列出来，条数与位置一目了然。
        """
        from storygraph.core import safeguard
        if safeguard.total() <= mark:
            return 0
        text = safeguard.report()
        for line in text.splitlines():
            self._log(f'[静默失败] {line}')
        n = safeguard.total() - mark
        self._status(f'⚠ 本次有 {n} 处错误被跳过（明细见控制台「静默失败」）')
        return n

    def _sync_graph_highlight(self):
        """卡片编辑器正开着的那一章 → 图上对应模块标成"当前"并居中。"""
        if self.graph is None or not self._session.script_path:
            return
        target = Path(self._session.script_path).resolve()
        for node in (self.graph.nodes or {}).values():
            fp = getattr(node, 'file_path', None)
            if fp and Path(str(fp)).resolve() == target:
                self._canvas.highlight_node(node.id)
                self._canvas.select_node(node.id)
                self._on_canvas_select(node.id)
                return

    def _reload_list(self):
        self._list.blockSignals(True)
        self._list.clear()
        for i in range(len(self._session)):
            self._list.addItem(self._make_item(i))
        self._list.blockSignals(False)

        self._who.clear()
        for cid in self._session.participants():
            self._who.addItem(QListWidgetItem(cid))

    def _make_item(self, i) -> QListWidgetItem:
        e = self._session.list_entry(i)
        item = QListWidgetItem(
            f"{i + 1:>3} {e['prefix']}{e['who']}：{e['text'][:24]}")
        item.setData(Qt.UserRole, i)
        return item

    def refresh_list_row(self, i: int):
        """
        单独重画列表里的第 i 行（保存一拍之后调用）。

        为什么需要它：改完台词只更新**卡片**的话，左边列表还挂着旧文本 ——
        用户看到的列表和卡片不一致，会以为没保存上（tkinter 版有这一步，
        搬过来时漏了）。整表重画会丢掉滚动位置与选中态，所以只画一行。
        """
        if not self._session.has(i):
            return
        item = self._list.item(i)
        if item is None:
            return
        e = self._session.list_entry(i)
        self._list.blockSignals(True)
        try:
            item.setText(f"{i + 1:>3} {e['prefix']}{e['who']}：{e['text'][:24]}")
            item.setData(Qt.UserRole, i)
        finally:
            self._list.blockSignals(False)

    # ------------------------------------------------------------------
    #  模块 ↔ 卡片
    # ------------------------------------------------------------------

    def _on_node_open(self, nid):
        """进卡片编辑器打开这一章（右栏「✎ 打开本章剧本」/ 右键菜单）。"""
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        if node is None:
            return
        fp = getattr(node, 'file_path', None)
        if not fp or not Path(str(fp)).exists():
            self._status(f'「{node.label_name}」没有关联到源文件'
                         f'（这个块是手工新建的，或者在图上但磁盘上已删）——'
                         f'用「➕ 导入章节 .rpy」重新导入那个文件即可。')
            return
        self.load_script(fp, goto_card=True)
        self._canvas.highlight_node(nid)

    def open_node_in_editor(self, nid):
        """旧版右键菜单里的「在编辑器中打开」：用系统默认程序打开那个 .rpy。"""
        from storygraph.graph.editor_utils import open_with_system_default
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        fp = getattr(node, 'file_path', None) if node else None
        if not fp or not Path(str(fp)).exists():
            self._status('这个块没有关联到磁盘上的 .rpy，打不开。')
            return
        if open_with_system_default(fp):
            self._log(f'[打开] 已用系统默认程序打开 {Path(str(fp)).name}')
        else:
            self._status(f'没能调用系统默认程序，请手动打开：{fp}')

    def build_node_menu(self, nid=None) -> QMenu:
        """
        拼出右键菜单（**不 exec**，所以能测"哪些项被置灰了"）。

        条目与旧版 `GraphView.menu` 对齐。旧版有而上一版被我漏掉的：
        「在编辑器中打开」。旧版叫「改本章台词（卡片）」的那条保持原名
        （用户认得这个名字）。
        """
        menu = QMenu(self)
        node = (self.graph.nodes or {}).get(nid) if (self.graph and nid) else None
        if node is not None:
            name = getattr(node, 'label_name', '')
            fp = getattr(node, 'file_path', None)
            has_src = bool(fp and Path(str(fp)).exists())

            targets = [
                menu.addAction(f'改本章台词（卡片）「{name}」',
                               (lambda: self._on_node_open(nid)) if has_src
                               else None),
                menu.addAction('在编辑器中打开',
                               (lambda: self.open_node_in_editor(nid))
                               if has_src else None),
                menu.addAction('用 Excel 打开此章节',
                               (lambda: self.open_node_in_excel(nid))
                               if has_src else None),
            ]
            if not has_src:
                # 没有源文件的块点了也没用 —— 置灰比"点完再弹一句"友好
                for a in targets:
                    a.setEnabled(False)
                    a.setToolTip('该块未关联源文件'
                                 '（手工新建的块，或先「➕ 导入章节」）')
            menu.addSeparator()
            menu.addAction('设为当前章节',
                           lambda: self._canvas.highlight_node(nid))
            menu.addAction('编辑剧情变量…', lambda: self.edit_node_ops(nid))
            menu.addAction('从这里连一条到…', lambda: self.add_edge(from_id=nid))
            menu.addSeparator()
            # 「移除这一章」按**文件**整章移除（用户心智里一章 = 一个 .rpy）；
            # 「删除这个块」只删这一个 label。两者都在，别混为一谈。
            if fp:
                menu.addAction(f'从剧情图移除「{Path(str(fp)).name}」整章',
                               lambda: self.remove_chapter_from_graph(nid))
            menu.addAction('删除这个块…', lambda: self._delete_node(nid))
        else:
            menu.addAction('添加新章节…', self.add_node)
            menu.addAction('添加连线…', self.add_edge)
            menu.addSeparator()
            menu.addAction('自动布局', self._canvas.auto_layout)
            menu.addAction('适应窗口', self._canvas.fit_to_content)
            menu.addAction('检查逻辑问题', self.check_logic)
            menu.addSeparator()
            # 画布右键 = 剧情图的家务事，所以放"导入章节 / 清空图"，
            # **不放**资源表的动作（那是另一条路，混在一起用户会以为
            # "重扫项目"会把整部戏画出来 —— 正是上一版最大的误解）。
            menu.addAction('导入章节 .rpy…', self.import_chapters)
            menu.addAction('补全相关章节', self.complete_related_chapters)
            menu.addAction('重载已导入章节', self.reload_imported_chapters)
            menu.addAction('清空剧情图', self.clear_graph)
        return menu

    def _on_node_menu(self, nid, global_pos):
        self.build_node_menu(nid).exec(global_pos)

    def add_node_at(self, x, y):
        """双击空白 = 在那个场景坐标新建章节（旧版 `on_double_click` 的分支）。"""
        return self.add_node(at=(x, y))

    def add_node(self, at=None):
        """
        新建一个章节模块（**校验在对话框里**，不合法就地提示）。

        `at` 给场景坐标时放在那里（双击空白 = 在这里新建，旧版行为）；
        不给就放视野中央（工具栏按钮）。
        """
        from storygraph.ui_qt.dialogs import AddNodeDialog
        existing = [getattr(n, 'label_name', '')
                    for n in ((self.graph.nodes or {}).values()
                              if self.graph else [])]
        dlg = AddNodeDialog(self, existing=existing)
        if dlg.exec() != QDialog.Accepted:
            return
        if self.graph is None:
            from storygraph.graph.models import Graph
            self._set_graph(Graph())
        if at is None:
            center = self._canvas.mapToScene(
                self._canvas.viewport().rect().center())
            at = (center.x(), center.y())
        from storygraph.graph.commands import AddNodeCommand
        cmd = AddNodeCommand(self, dlg.name, float(at[0]), float(at[1]),
                             operations=([dlg.operations]
                                         if dlg.operations else None))
        self._run_command(cmd)
        # 命令 execute 之后才知道新块的 id（它自己 add_node 拿的）
        nid = getattr(cmd, 'node_id', None)
        if nid is not None:
            self._canvas.highlight_node(nid)
            self._canvas.select_node(nid, center=False)
        self._status(f'已新建章节「{dlg.name}」（在内存里；'
                     f'点「⤓ 导出」才会进脚本）。Ctrl+Z 可撤销。')

    def add_edge(self, from_id=None, to_id=None):
        """
        加一条连线。

        两个入口共用这一份：**Shift + 左键从 A 拖到 B**（旧版做法）和
        右键菜单「从这里连一条到…」/「添加连线…」。
        """
        from storygraph.ui_qt.dialogs import AddEdgeDialog
        if self.graph is None or len(self.graph.nodes or {}) < 2:
            self._status('至少要有两个模块才能连线'
                         '（先用「➕ 导入章节 .rpy」加几章，或新建章节）。')
            return
        if from_id is None:
            from_id = self._canvas.selected_node_id()
        dlg = AddEdgeDialog(self, nodes=self.graph.nodes, from_id=from_id,
                            to_id=to_id)
        if dlg.exec() != QDialog.Accepted:
            return
        from storygraph.graph.commands import AddEdgeCommand
        self.selected_node = dlg.from_id
        self.selected_edge = None
        self._run_command(AddEdgeCommand(self, dlg.from_id, dlg.to_id,
                                         dlg.etype, dlg.condition,
                                         dlg.option_text))
        names = {nid: getattr(n, 'label_name', '')
                 for nid, n in (self.graph.nodes or {}).items()}
        self._log(f'[连线] {names.get(dlg.from_id, dlg.from_id)} → '
                  f'{names.get(dlg.to_id, dlg.to_id)}（{dlg.etype}'
                  f'{("，" + dlg.condition) if dlg.condition else ""}'
                  f'{("「" + dlg.option_text + "」") if dlg.option_text else ""}）')
        self._status(f'已连线 {dlg.from_id} → {dlg.to_id}（{dlg.etype}）。')

    def delete_edge(self, eid):
        if self.graph is None or eid not in (self.graph.edges or {}):
            return
        from storygraph.graph.commands import RemoveEdgeCommand
        self._run_command(RemoveEdgeCommand(self, eid))
        self._status('已删除这条连线（Ctrl+Z 可撤销）。')

    def _delete_node(self, nid):
        if self.graph is None:
            return
        node = self.graph.nodes.get(nid)
        if node is None:
            return
        if QMessageBox.question(
                self, '删除块',
                f'删除「{node.label_name}」及其连线？（只改内存里的图，不动文件；'
                f'Ctrl+Z 可撤销）'
        ) != QMessageBox.Yes:
            return
        from storygraph.graph.commands import RemoveNodeCommand
        self._run_command(RemoveNodeCommand(self, nid))

    # ------------------------------------------------------------------
    #  增 / 删卡片
    # ------------------------------------------------------------------

    def insert_card(self, after: bool = True):
        """在当前这一拍前/后插入一张空卡片（语义在 `EditorSession.insert_beat`）。"""
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('还没有载入章节，没法插入卡片。')
            return
        self.apply_current_beat()             # 先把这一拍的表单提交，免得丢改动
        res = self._session.insert_beat(idx, after=after)
        if not res.get('ok'):
            self._status(res.get('error') or '插入失败')
            return
        self._reload_list()
        self._show_index(res['index'])
        self._sync_structural_buttons()
        self._log(f"[卡片] 已在第 {idx + 1} 拍{'后' if after else '前'}插入一张空卡片"
                  f"（现在是第 {res['index'] + 1} / {res['count']} 拍）")
        self._status(f"✔ 已插入一张空卡片（第 {res['index'] + 1} 拍）——"
                     f"填完按「💾 保存这一拍」，导出时才落盘。")

    def delete_card(self):
        """删掉当前这一拍（**先问一声**：它没有多级撤销，只有单级恢复）。"""
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('还没有载入章节，没有可删的卡片。')
            return
        beat = self._session.beats[idx]
        who = str(getattr(beat, 'speaker', '') or '') or '（旁白）'
        text = (str(getattr(beat, 'text', '') or '')[:60] or '（没有台词）')
        if QMessageBox.question(
                self, '删除这张卡片',
                f'删掉第 {idx + 1} 拍？\n\n{who}：{text}\n\n'
                f'（只改内存里的章节；删完可以点「↩ 撤销删除」放回来，'
                f'导出前不落盘）'
        ) != QMessageBox.Yes:
            return
        res = self._session.delete_beat(idx)
        if not res.get('ok'):
            self._status(res.get('error') or '删除失败')
            return
        self._reload_list()
        self._show_index(res['index'])
        self._sync_structural_buttons()
        self._log(f'[卡片] 已删除第 {idx + 1} 拍（{who}：{text}）——'
                  f'还剩 {res["count"]} 拍；点「↩ 撤销删除」可以放回来')
        self._status(f'🗑 已删除第 {idx + 1} 拍（还剩 {res["count"]} 拍）。')

    def restore_card(self):
        """把刚删掉的那张卡片放回原位。"""
        res = self._session.restore_last_removed()
        if not res.get('ok'):
            self._status(res.get('error') or '没有可恢复的卡片。')
            return
        self._reload_list()
        self._show_index(res['index'])
        self._sync_structural_buttons()
        self._log(f'[卡片] 已把删掉的卡片放回第 {res["index"] + 1} 拍')
        self._status(f'↩ 已恢复那一张卡片（第 {res["index"] + 1} 拍）。')

    def _sync_structural_buttons(self):
        """「撤销删除」只在真删过东西之后可用。"""
        for btn, on in ((getattr(self, '_restore_btn', None),
                         self._session.has_removed()),
                        (getattr(self, '_delete_btn', None),
                         len(self._session) > 1),
                        (getattr(self, '_insert_btn', None),
                         len(self._session) > 0),
                        (getattr(self, '_insert_before_btn', None),
                         len(self._session) > 0)):
            if btn is not None:
                btn.setEnabled(bool(on))

    # ------------------------------------------------------------------
    #  卡片 ↔ 数据（全部委托给会话）
    # ------------------------------------------------------------------

    def _show_index(self, idx: int):
        if not self._session.has(idx):
            self._idx = -1
            self._session.mark_loaded(-1)
            self._update_nav()
            self._sync_undo_button()
            return
        self._idx = idx
        # 告诉会话"前端现在显示这一拍"（`undo()` 不带参数时的默认目标）
        self._session.mark_loaded(idx)
        self._panel.load_fields(self._session.fields(idx),
                                self._session.cards(idx))
        self._panel.set_speakers(self._session.characters(),
                                 self._session.beats[idx].speaker or '')
        # 「有台词却没有说话人」→ 导出后会变成旁白，在这里就说清楚。
        # 文案由 `app.beats.speaker_missing_warning` 算（前端只负责显示）。
        from storygraph.app import beats as _beats
        self._panel.set_note(
            _beats.speaker_missing_warning(self._session.beats[idx]))
        # 当前舞台（这一拍生效的状态）+ 继承上一拍的提示。
        # 两份文案都来自语义层（stage_snapshot / app.beats），前端不自己拼。
        self._panel.set_stage_digest(self._session.stage_digest_text(idx),
                                     self._session.inherited_hint(idx))

        self._list.blockSignals(True)
        self._list.setCurrentRow(idx)
        self._list.blockSignals(False)
        self._status_bar.set_dirty(False, self._file_path)
        self._update_nav()
        self._sync_undo_button()
        self._sync_structural_buttons()
        # 相邻几拍的立绘先解进缓存（翻卡片不空等；见 `_prefetch_neighbours`）
        self._prefetch_neighbours(idx)

    #: 预取范围：往后 2 拍（往前翻也给 1 拍）。
    PREFETCH_AHEAD, PREFETCH_BEHIND = 2, 1

    def _prefetch_neighbours(self, idx: int):
        """
        预取**相邻几拍**的立绘缩略图（用户 2026-10-05："卡牌界面图片加载慢"）。

        只做"把图解进缓存"，不碰界面：真正翻过去时 `ThumbLoader.request`
        命中内存缓存 → 0 延迟。范围很小（默认往后 2 拍 / 往前 1 拍），
        且预取本身有张数上限（见 `SpriteGridWidget.prefetch`），
        不会把线程池占满、反把当前这一拍挤慢。
        """
        panel = getattr(self, '_panel', None)
        if panel is None or not self._session.has(idx):
            return
        cards = []
        lo = max(0, int(idx) - self.PREFETCH_BEHIND)
        hi = int(idx) + self.PREFETCH_AHEAD
        for j in range(lo, hi + 1):
            if j == idx or not self._session.has(j):
                continue
            try:
                cards.extend(self._session.cards(j) or ())
            except Exception:                # noqa: BLE001 - 预取失败无所谓
                continue
        if cards:
            panel.prefetch_sprites(cards)

    def apply_current_beat(self):
        """表单 → 当前拍（语义在 `EditorSession.apply`，这里只递数据）。"""
        if not self._session.has(self._loaded_idx):
            return None
        return self._session.apply(
            self._loaded_idx,
            self._panel.collect_fields(),
            self._panel.sprite_cards(),
            advanced_open=self._panel.is_advanced_open(),
            # 载入时那一格的原文：会话靠它判断"用户手改了 Stage 没有"
            stage_loaded=self._panel.stage_loaded())

    def save_current_beat(self):
        """
        **保存这张卡片的修改**（卡片上的 💾 按钮 / Ctrl+S / 工具栏同名按钮）。

        走 `EditorSession.save`：它会顺手判"到底改了没有"，
        只有真改了才压撤销栈 —— 所以"翻页顺带提交"不会把撤销按钮点亮。
        """
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('还没有载入章节，没什么可保存。')
            return None
        res = self._session.save(
            idx,
            self._panel.collect_fields(),
            cards=self._panel.sprite_cards(),
            advanced_open=self._panel.is_advanced_open(),
            stage_loaded=self._panel.stage_loaded())

        # 保存成功 = 表单内容成为新基线；列表那一行也要跟着变，
        # 否则左边还挂着旧台词，看起来像"没保存上"。
        self._panel.reset_baseline()
        self.refresh_list_row(idx)
        self._sync_undo_button()
        self._status_bar.set_dirty(True, self._file_path)

        if res['changed']:
            self._status(f'✔ 已保存到这一拍（{res["line_id"]}）。'
                         f'改动在内存里，点「⤓ 导出 .rpy」时才落盘。')
        else:
            self._status(f'这一拍没有改动，不需要保存（{res["line_id"]}）。')
        return res['beat']

    def undo_current_beat(self):
        """
        **撤销这张卡片的修改** —— 恢复成上一次保存时的样子。

        只影响这一拍（不碰别的卡片）。撤销不可用时**明说**，
        不要静默什么都不做 —— 用户按了按钮却"没反应"最难排查。
        """
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('还没有载入章节，没有可撤销的修改。')
            return
        if not self._session.has_undo(idx):
            self._status('这一拍没有可撤销的修改'
                         '（撤销只能回到"上一次保存"之前）。')
            return
        self._session.undo(idx)
        # 数据变了 → 必须重新载入表单与立绘卡（含 chip 与缩略图）
        self._show_index(idx)
        self.refresh_list_row(idx)
        self._sync_undo_button()
        self._status_bar.set_dirty(True, self._file_path)
        self._status('↩ 已撤销这一拍的修改，恢复成上一次保存时的样子。')

    def _sync_undo_button(self):
        """撤销按钮的可用性跟着会话的撤销栈走。"""
        try:
            self._panel.set_undo_enabled(
                self._session.has_undo(self._loaded_idx))
        except Exception:                       # noqa: BLE001
            swallow('main_window._sync_undo_button')

    # ------------------------------------------------------------------
    #  导航（**纯导航**：翻页不碰别的拍）
    # ------------------------------------------------------------------

    def _update_nav(self):
        n = len(self._session)
        i = self._loaded_idx
        # 翻拍按钮同样只在卡片页可用：它们在结构页上没有意义
        # （键盘那条路同样由 `eventFilter` 判页号）。
        on_card = self._page == self.PAGE_CARD
        if hasattr(self, '_prev_action'):
            self._prev_action.setEnabled(on_card and i > 0)
        if hasattr(self, '_next_action'):
            self._next_action.setEnabled(on_card and 0 <= i < n - 1)
        if hasattr(self, '_pos'):
            self._pos.setText(f'{i + 1 if i >= 0 else 0} / {n}')
        self._update_crumb()

    def _sync_nav(self):
        if hasattr(self, '_prev_action'):
            self._update_nav()

    def _on_select(self, row: int):
        if row >= 0 and row != self._loaded_idx:
            self.apply_current_beat()
            self._show_index(row)

    def next_beat(self):
        """
        前进一拍（先提交当前拍，再翻页）。

        **越界必须有守卫**：第一版直接 `_show_index(self._loaded_idx + 1)`，
        走到最后一拍会 `_show_index(n)` → 内部把 `_idx` 设成 `-1`。
        后果不是"没反应"，而是**静默丢改动**：卡片上还显示着最后一拍、
        用户改完按「💾 保存这一拍」，`apply_current_beat` 一看
        `has(-1)` 是假就什么都不做 —— 用户以为存了。
        （tkinter 版的 `_update_nav_state` 就是干这个的，搬过来时漏了。）
        """
        i = self._loaded_idx
        if not self._session.has(i):
            return
        self.apply_current_beat()
        if i < len(self._session) - 1:
            self._show_index(i + 1)

    def prev_beat(self):
        """后退一拍（同样先提交、同样不许越界）。"""
        i = self._loaded_idx
        if not self._session.has(i):
            return
        self.apply_current_beat()
        if i > 0:
            self._show_index(i - 1)

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
        """
        键盘契约（**必须真的装上**，见 `_install_shortcuts`）。

        历史坑一：这个方法一直在，但从来没有人 `installEventFilter` ——
        于是 Ctrl+S / 回车翻页 / Alt+↑↓ **全都是死的**。
        历史坑二（两页导航之后）：这些键**只在卡片页有意义**。
        在结构图上按回车应该"进这一章的卡片"，而不是翻上一章留下的拍号 ——
        所以卡片页才有的键在这里按页分流。
        """
        from PySide6.QtCore import QEvent
        # **窗口失活 = 手势与按键状态全部作废**（用户 2026-10-05："拖拽画布
        # 时灵时不灵"）。切走窗口（Alt+Tab / 弹了别的程序）时，空格键的
        # KeyRelease 根本收不到 —— `_space_down` 会**卡在 True**：之后每次
        # 左键拖块都变成平移（块拖不动、点选像没反应）；反过来若卡在 False，
        # 就是"空格 + 左键拖没反应"。鼠标释放同样会丢 → `_panning` 卡住。
        # 所以失活时必须显式清干净，恢复时状态才是确定的。
        if event.type() == QEvent.WindowDeactivate:
            self._canvas.set_space_down(False)
            self._canvas.cancel_interaction()
            return super().eventFilter(obj, event)
        # 空格 = "按住可平移画布"（旧版绑在整窗上）。必须在**窗口级**跟踪：
        # 画布的 `keyPressEvent` 只在它有键盘焦点时才收到，而用户按空格时
        # 焦点往往在工具栏/右栏 —— 那就是"空格+左键没反应"的原因。
        # 但在文本框里敲空格必须照常输入，所以下面会跳过文本控件。
        if event.type() in (QEvent.KeyPress, QEvent.KeyRelease) \
                and event.key() == Qt.Key_Space \
                and not (event.modifiers() & ~Qt.KeypadModifier):
            pressed = event.type() == QEvent.KeyPress
            if not self._text_input_has_focus(obj):
                self._canvas.set_space_down(pressed)
                if pressed:
                    return True          # 不让空格继续传（免得上层再滚一屏）
                return super().eventFilter(obj, event)
            self._canvas.set_space_down(False)
            return super().eventFilter(obj, event)
        if event.type() != QEvent.KeyPress:
            return super().eventFilter(obj, event)
        # 别人家的窗口（弹出的对话框、下拉列表）在忙，不抢键。
        # 但**本窗口的控件树**要放行 —— 台词框就在这棵树里。
        if not self._event_belongs_here(obj):
            return super().eventFilter(obj, event)

        key = event.key()
        mods = event.modifiers()
        on_card = self._page == self.PAGE_CARD

        # Esc：从卡片页回结构页（结构页不拦）
        if key == Qt.Key_Escape:
            if on_card:
                self._back_to_graph()
                return True
            return super().eventFilter(obj, event)

        if not on_card:
            # 结构页：回车 = 进选中模块的卡片；Delete = 删块
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._open_selected_in_card()
                return True
            if key == Qt.Key_Delete:
                nid = self._canvas.selected_node_id()
                if nid is not None:
                    self._delete_node(nid)
                    return True
            # 旧版结构页的 Ctrl+S / Ctrl+O（保存 / 加载布局）与 Ctrl+A（分析）——
            # 「功能缺失那么多」里有它们，这里按页补回（卡片页的 Ctrl+S/Z 另算）。
            if key == Qt.Key_S and mods & Qt.ControlModifier:
                self.save_layout()
                return True
            if key == Qt.Key_O and mods & Qt.ControlModifier:
                self.load_layout()
                return True
            if key == Qt.Key_A and mods & Qt.ControlModifier:
                self.check_logic()
                return True
            # 图级撤销 / 重做（旧版 Ctrl+Z / Ctrl+Y）
            if key == Qt.Key_Z and mods & Qt.ControlModifier:
                self.undo_graph()
                return True
            if key == Qt.Key_Y and mods & Qt.ControlModifier:
                self.redo_graph()
                return True
            return super().eventFilter(obj, event)

        # Ctrl+S：与工具栏 action 的快捷键同一个落点（两处都能按，语义只有一份）
        if key == Qt.Key_S and mods & Qt.ControlModifier:
            self.save_current_beat()
            return True
        # Ctrl+Z = 撤销这一拍的修改（与"保存这一拍"配对）。
        # 刻意**不接** Ctrl+Shift+Z / Ctrl+Y：那是"重做"，
        # 而这里只有单级撤销 —— 摆一个假的重做按钮比没有更糟。
        if key == Qt.Key_Z and mods & Qt.ControlModifier:
            self.undo_current_beat()
            return True        # 增 / 删卡片（结构编辑）：Ctrl+Insert / Ctrl+Delete。
        # 用组合键而不是裸 Insert/Delete：裸键留给"编辑文字"的习惯，
        # 免得在台词框里按 Delete 就把整张卡片删了。
        if key == Qt.Key_Insert and mods & Qt.ControlModifier:
            self.insert_card()
            return True
        if key == Qt.Key_Delete and mods & Qt.ControlModifier:
            self.delete_card()
            return True
        # **回车 = 下一拍**（用户明确："回车是下一张卡片的意思"）
        if key in (Qt.Key_Return, Qt.Key_Enter) and \
                not (mods & Qt.ShiftModifier):
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

        if key not in (Qt.Key_Down, Qt.Key_Up):
            return super().eventFilter(obj, event)

        # 焦点在章节列表里时**完全放行**：列表自己移动选择 = 换一拍，
        # 我们再翻一次就会一次跳两拍（tkinter 版踩过这个坑）。
        fw = self.focusWidget()
        if isinstance(fw, QListWidget) and fw in (self._list, self._who):
            return super().eventFilter(obj, event)
        # 多行台词框：光标不在边上时把 ↑/↓ 让给移动光标
        if isinstance(fw, QPlainTextEdit) and \
                not self._caret_at_edge(fw, key == Qt.Key_Down):
            return super().eventFilter(obj, event)
        # 别的控件（单行输入 / 组合框 / 卡片）里的一行文本没有"上下"可言
        # → 直接翻页。单行控件要放行的话，↑/↓ 会变成"切换下拉项"。
        if isinstance(fw, (QComboBox, QLineEdit)):
            if key == Qt.Key_Down:
                self.next_beat()
                return True
            self.prev_beat()
            return True
        if key == Qt.Key_Down:
            self.next_beat()
            return True
        self.prev_beat()
        return True

    def _event_belongs_here(self, obj) -> bool:
        """这个按键事件是不是属于本窗口的控件树？（弹出对话框不算）"""
        try:
            w = obj if isinstance(obj, QWidget) else None
            if w is None:
                return False
            if w is self or self.isAncestorOf(w):
                return True
            # 组合框的下拉列表 / 卡片里的子控件：往上找有没有本窗口
            p = w.parentWidget()
            while p is not None:
                if p is self:
                    return True
                p = p.parentWidget()
            return False
        except Exception:                       # noqa: BLE001
            return False

    def _install_shortcuts(self):
        """
        把 `eventFilter` 真的挂上去（**不挂就等于没有键盘**）。

        挂两次是故意的：
          · QApplication —— 覆盖"焦点在台词框/列表里"的按键；
          · self        —— 兜住"窗口自己收到"的按键（没有子控件吃走时）。
        两者都可能被调用，但每个按键只会命中一次（事件顺序是
        app 过滤器 → 对象过滤器 → 控件自己）。所以要在两处都装，
        并在命中后 `return True` 终止传播。
        """
        try:
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(self)
        except Exception:                       # noqa: BLE001
            swallow('main_window._install_shortcuts')
        self.installEventFilter(self)

    @staticmethod
    def _caret_at_edge(edit, down: bool) -> bool:
        try:
            cur = edit.textCursor().blockNumber()
            return (cur >= edit.blockCount() - 1) if down else cur <= 0
        except Exception:                       # noqa: BLE001
            return True

    # ------------------------------------------------------------------

    def _pick_image(self, current: str) -> str:
        """挑一张立绘图（走 Qt 版资产选择器）。取消或没有资源表返回 ''。"""
        from storygraph.ui_qt.asset_picker import pick_image
        if not self._ensure_registry():
            return ''
        return pick_image(self, self._registry_provider(), current=current,
                          project_root=self._project_root) or ''

    #: 卡片上那三格 → 资产种类（选择器要用）
    ASSET_FIELD_KINDS = {'Scene': 'image', 'BGM': 'music', 'Voice': 'voice',
                         'SFX': 'sound'}

    def _refresh_asset_choices(self):
        """
        把资源表里的候选灌进卡片的三格下拉（背景 / 音乐 / 语音）。

        没有资源表时清空候选（而不是留一堆过期值）。
        """
        panel = getattr(self, '_panel', None)
        if panel is None:
            return
        for field, kind in self.ASSET_FIELD_KINDS.items():
            try:
                choices = self._session.asset_choices(kind)
            except Exception:                  # noqa: BLE001
                choices = []
            panel.set_asset_choices(field, choices)

    def _pick_asset_for(self, field: str, current: str) -> str:
        """
        卡片「选择…」的回调：从**已登记的资产**里挑一个填进这一格。

        背景走 `pick_image`（要的是 image spec，不是资产 ID）；
        音乐 / 语音走 `pick_asset`（要的就是资产 ID —— 渲染器会把它翻成
        真实文件路径，查不到会写 `# TODO: 缺少资源`，所以写 ID 是安全的）。
        """
        from storygraph.ui_qt.asset_picker import pick_asset, pick_image
        if not self._ensure_registry():
            return ''
        kind = self.ASSET_FIELD_KINDS.get(field, 'image')
        if kind == 'image':
            return pick_image(self, self._registry_provider(), current=current,
                              project_root=self._project_root) or ''
        return pick_asset(self, self._registry_provider(), asset_type=kind,
                          current=current, project_root=self._project_root) or ''

    # ------------------------------------------------------------------
    #  导出 / 体检 / 预览 / 说明
    # ------------------------------------------------------------------

    def export_script(self):
        """导出这一章（编译 + 增量补丁 + 安全门禁，全在会话里）。"""
        if not len(self._session):
            self._status('没有可导出的内容。')
            return
        mark = self._swallow_mark()
        self.apply_current_beat()
        try:
            self._session.compile()
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '编译失败', str(e))
            self._report_swallowed(mark)
            return

        default = self._session.default_output_path()
        try:
            default.parent.mkdir(parents=True, exist_ok=True)
        except Exception:                       # noqa: BLE001
            swallow('main_window.export_script')
        path, _ = QFileDialog.getSaveFileName(self, '导出 .rpy',
                                              str(default), "Ren'Py (*.rpy)")
        if not path:
            return
        try:
            res = self._session.export(path)
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '导出失败', str(e))
            self._report_swallowed(mark)
            return
        if not res.get('ok'):
            blocking = res.get('blocking') or ['（未知原因）']
            QMessageBox.warning(self, '生成被门禁拦截', '\n'.join(blocking))
            self._report_swallowed(mark)
            return
        self._status_bar.set_dirty(False, str(res.get('path') or path))
        todos = res.get('todos') or 0
        warn = f'，缺资源 {todos} 处（见 TODO）' if todos else ''
        self._log(f'[导出] 已写入 {Path(path).name}'
                  f'（{res.get("note", "")}{warn}）')
        self._status(f'✔ 已导出 {Path(path).name}（{res.get("note", "")}{warn}）')
        self._report_swallowed(mark)

    # ------------------------------------------------------------------
    #  剧本表（Excel 双向）
    # ------------------------------------------------------------------

    def open_node_in_excel(self, nid=None):
        """
        右键章节模块 → 用它对应的 .rpy 生成演出表（同名 .xlsx）并打开。

        这是"用表格编演出"的入口：表里一行一句台词，立绘/背景/音乐/
        变换都是下拉框，候选直接来自资产表。

        **逻辑全在 `app.tables`**（那是从 tkinter 的 141 行 UI 方法里
        搬出来的，语义一条没改），这里只挑路径 + 展示结果。
        """
        from storygraph.app import tables
        from storygraph.graph.editor_utils import open_with_system_default

        nid = nid if nid is not None else self._canvas.selected_node_id()
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        script = getattr(node, 'file_path', None) if node else None
        if not script:
            script, _ = QFileDialog.getOpenFileName(
                self, '选择要生成演出表的 .rpy',
                str(Path(self._project_root or '.') / 'game'),
                "Ren'Py 脚本 (*.rpy)")
            if not script:
                return

        res = tables.generate_table(script, registry=self._registry_provider(),
                                    project_root=self._project_root)
        if not res['ok']:
            QMessageBox.critical(self, '生成剧本表失败', res['error'])
            return
        table_path = res['table_path']
        tables.remember_table_mtime(self._table_mtimes, table_path)

        if not open_with_system_default(table_path):
            QMessageBox.information(
                self, '已生成剧本表',
                f'未能自动调用 Excel，请手动打开：\n{table_path}')
        self._status(f'✔ 已生成 {table_path.name}'
                     f'（改完保存，再用「⤒ 从剧本表写回 .rpy」）')

    def export_from_table(self):
        """
        选定一张剧本表 → 写回 .rpy（导入 + 增量补丁 + 安全门禁）。

        顺序刻意是**先算、再问、最后写** —— 旧实现就是这么做的：
        不在用户点头前落任何盘，被门禁拦下时目标文件一个字节都不动。
        """
        from storygraph.app import tables

        start = str(Path(self._project_root or '.') / 'game')
        path, _ = QFileDialog.getOpenFileName(
            self, '选择剧本表（.xlsx）', start,
            'Excel 工作簿 (*.xlsx);;所有文件 (*)')
        if not path:
            return
        table_path = Path(path)

        # 这张表被外部改过（另存/换机器/Excel 保存）→ 先自动刷一次候选，
        # 让人不必记得点「刷新候选」。失败只提示，不打断导出。
        if tables.table_changed_since(self._table_mtimes, table_path):
            self._refresh_table_candidates(table_path)
            tables.remember_table_mtime(self._table_mtimes, table_path)

        try:
            res = tables.export_table(
                table_path, registry=self._registry_provider(),
                project_root=self._project_root,
                source_rpy=self._source_rpy_for_table(table_path))
        except Exception as e:                    # noqa: BLE001
            QMessageBox.critical(self, '读取剧本表失败', str(e))
            return

        if res.get('error'):
            QMessageBox.warning(self, '无法导出', res['error'])
            return

        if res['unregistered']:
            self._show_unregistered(res['unregistered'])

        if res['blocking']:
            # 拒写，但产物已另存一份供对比（`app.tables` 负责写副本）
            review = res.get('review_path')
            QMessageBox.critical(
                self, '拒绝导出：生成的脚本会让游戏跑不起来',
                f'发现 {res["blocking_total"]} 条致命问题，**没有写入目标文件**'
                f'（原文与演出表都没动）。\n\n'
                + '\n'.join('· ' + m for m in res['blocking'])
                + (f'\n\n产物已另存一份供对比：\n{review}' if review
                   else '\n\n（产物副本也没能写出，请检查目录权限）'))
            self._status(f'⛔ 导出被拦下（{res["blocking_total"]} 条致命问题）')
            return

        tip = (f'将生成 {res["beats"]} 个演出单元到：\n{res["out_path"]}\n')
        tip += f'\n{res["note"]}\n' if res.get('note') else ''
        if res['warnings']:
            tip += ('\n提醒（不阻止导出）：\n'
                    + '\n'.join('· ' + m for m in res['warnings']) + '\n')
        else:
            tip += '\n生成安全门禁：无问题。\n'
        tip += '\n继续？'
        if QMessageBox.question(self, '确认导出', tip) != QMessageBox.Yes:
            return

        done = tables.apply_export(res, project_root=self._project_root)
        if not done.get('ok'):
            QMessageBox.critical(
                self, '导出失败',
                done.get('error') or f'写不出去：\n{done.get("out_path")}')
            return
        self._log(f'[剧本表] 已生成脚本 {Path(done["out_path"]).name}'
                  f'（{done["beats"]} 个演出单元）')
        self._status(f'✔ 已由剧本表写回 {Path(done["out_path"]).name}'
                     f'（{done["beats"]} 个演出单元）')

    def _source_rpy_for_table(self, table_path):
        """
        增量补丁的底稿 .rpy。

        优先用**画布上当前章节**的源文件（最准：表可能被另存到别处）；
        其次让 `app.tables` 退回"表旁边同名 .rpy"。
        """
        nid = self._canvas.selected_node_id()
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        fp = getattr(node, 'file_path', None) if node else None
        if fp and Path(fp).exists():
            return Path(fp)
        return None

    def _refresh_table_candidates(self, table_path):
        """
        资源表变了 → 把这张演出表的候选下拉刷一遍，不动 Beats 里的用户数据。

        失败只提示、不打断：刷候选是辅助动作，不该拦住导出主流程。
        """
        registry = self._registry_provider()
        if registry is None:
            return
        try:
            from storygraph.story.excel_export import refresh_candidate_sheets
            refresh_candidate_sheets(table_path, registry)
            self._status(f'已刷新 {Path(table_path).name} 的下拉候选。')
        except Exception as e:                    # noqa: BLE001
            self._status(f'刷新候选失败（不影响导出）：{e}')

    def _show_unregistered(self, unregistered):
        """演出表里引用了、但资源表未登记的资产 —— 提示文案去登记。"""
        from storygraph.ui_qt.dialogs import AnalysisDialog
        lines = [f'{cat}：{aid}' for cat, aid in list(unregistered)[:80]]
        more = (f'\n… 还有 {len(unregistered) - 80} 条'
                if len(unregistered) > 80 else '')
        AnalysisDialog(
            self, '演出表里引用了、但资源表未登记的资产',
            f'共 {len(unregistered)} 条。它们**不影响导出**，'
            f'但建议补进资源表（否则下拉里选不到）：\n\n'
            + '\n'.join(lines) + more).exec()

    # ------------------------------------------------------------------

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
        self._log(f'[体检] 问题 {r["total"]} 条'
                  + (f'：{r["issues"][0][:80]}' if r['issues'] else '（干净）'))
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
            swallow('main_window._status')

    def closeEvent(self, event):
        # 自己摘掉应用级事件过滤器：不摘的话，窗口关了过滤器还活着，
        # 之后（同一进程里的下一个窗口 / 测试用例）按键会被死人接走。
        for fn in (lambda: self._remove_shortcuts(),
                   lambda: self._loader.shutdown(),
                   lambda: self._notifier and self._notifier.close()):
            try:
                fn()
            except Exception:                   # noqa: BLE001
                swallow('main_window.closeEvent')
        super().closeEvent(event)

    def _remove_shortcuts(self):
        """逆着 `_install_shortcuts` 摘干净（幂等）。"""
        for target in (QApplication.instance(), self):
            if target is None:
                continue
            try:
                target.removeEventFilter(self)
            except Exception:                   # noqa: BLE001
                swallow('main_window._remove_shortcuts')


#: 迁移期别名（测试与 `main_qt` 都引过它）
ScriptTableEditorWindowQt = MainWindowQt


def open_editor(engine, script_path=None, project_root=None, graph=None):
    win = MainWindowQt(engine, script_path, project_root, graph)
    win.show()
    return win
