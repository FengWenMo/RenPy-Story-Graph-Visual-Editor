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

import re

#: Ren'Py 文字标签：`{color=#f00}` / `{/color}` / `{i}` / `{w=0.5}` …
_RENPY_TAG_RE = re.compile(r'\{([^{}]*)\}')
#: 预览认得的颜色值（只放行"看着像颜色"的东西 —— 预览是 HTML）
_PREVIEW_COLOR_RE = re.compile(r'^(?:#[0-9a-fA-F]{3,8}|rgba?\([\d\s.,%]+\))$')


def renpy_text_to_html(text: str) -> tuple:
    """
    Ren'Py 台词 →（预览用 HTML，是否含标签）。

    只认最常用的几种：`color` / `b` / `i` / `u` / 绝对 `size`。
    其余标签（`{w}` / `{p}` / `{nw}` / `{alpha=…}`…）**剥掉不显示**
    （它们不改变"截一张静态图看到的样子"），`[变量]` 原样留字。
    先转义再拼标签：文本里出现 `<` 也只是普通字符。
    """
    from html import escape as _esc

    src = str(text or '')
    if '{' not in src:
        return _esc(src), False
    out = []
    pos = 0
    has_tag = False
    for m in _RENPY_TAG_RE.finditer(src):
        out.append(_esc(src[pos:m.start()]))
        pos = m.end()
        tag = m.group(1).strip()
        if not tag:
            continue
        closing = tag.startswith('/')
        name = (tag[1:] if closing else tag).strip().lower()
        base = name.split('=', 1)[0].strip()
        if base in ('b', 'i', 'u'):
            out.append(f'</{base}>' if closing else f'<{base}>')
            has_tag = True
        elif base == 'color':
            if closing:
                out.append('</span>')
            else:
                val = name.split('=', 1)[1].strip().strip('"\'')
                if _PREVIEW_COLOR_RE.match(val):
                    out.append(f'<span style="color:{val}">')
                # 颜色不是合法值 → 不产出 style（但标签本身算数：
                # 预览会出来，让人看得见"这句带样式"，而 Ren'Py 不报错）
                has_tag = True
        elif base == 'size':
            if closing:
                out.append('</span>')
            else:
                val = name.split('=', 1)[1].strip()
                if val.isdigit():
                    out.append(f'<span style="font-size:{int(val)}px">')
                has_tag = True
    out.append(_esc(src[pos:]))
    return ''.join(out), has_tag


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
# `MainWindowQt` 的五块职责各住一个文件（P3-14，2026-10-05）。
# **基类顺序**：六个 mixin 必须排在 `QMainWindow` **前面** ——
# `eventFilter` / `closeEvent` 是 Qt 虚函数（`QObject` 自己就定义了），
# 排在后面就会被它抢先、mixin 那份成死代码（实测 13 条键盘用例全红，
# 而 `hasattr(MainWindowQt, 'eventFilter')` 照样是 True）。
# 由 `tests/test_main_window_mixins.py` 守。
from storygraph.ui_qt.window_assets import WindowAssetsMixin
from storygraph.ui_qt.window_card import CardPageMixin
from storygraph.ui_qt.window_export import (WindowExportMixin,
                                             overwritable as _overwritable)
from storygraph.ui_qt.window_graph_ops import GraphOpsMixin
from storygraph.ui_qt.window_keyboard import WindowKeyboardMixin
from storygraph.ui_qt.window_state import WindowStateMixin
from storygraph.ui_qt.window_nodes import NodeEditMixin


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
        #: 「语句参数」那一组的控件：`{动态列名: 控件}`（跟着这一拍的语句走，
        #: 没有参数就整组隐藏）。见 `set_spec_args`。
        self._spec_widgets: dict = {}
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

        # —— 语句条：**这一拍是什么**（2026-10-18）——
        #
        # 为什么要有它：卡片从前**假设每一拍都是一句台词**。于是一条
        # `play_music track=bgm file="bgm/theme.ogg"` 或者 `styled "红色" "#ff0000"`
        # 在界面上长成"一个空的说话人 + 一个空的台词框 + 一张空立绘网格"，
        # 而它真正能改的那几格（参数）被埋在卡片最底下 —— 要滚过一整屏
        # 与它无关的东西才看得到。用户 2026-10-18：**"不是每个写法都跟 show
        # 一样与图片相关"**（有的可能是声音、是文字），排版得跟着内容走。
        self._stmt_bar = QFrame()
        self._stmt_bar.setObjectName('statementBar')
        self._stmt_bar.setProperty('role', 'card')
        bar = QVBoxLayout(self._stmt_bar)
        bar.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                               T.SPACE['sm'], T.SPACE['sm'])
        bar.setSpacing(2)
        self._stmt_title = QLabel('')
        self._stmt_title.setProperty('role', 'section')
        self._stmt_title.setWordWrap(True)
        bar.addWidget(self._stmt_title)
        self._stmt_head = QLabel('')
        self._stmt_head.setProperty('role', 'caption')
        self._stmt_head.setFont(QFont(T.FONTS['mono'][0], T.FONTS['mono'][1]))
        self._stmt_head.setWordWrap(True)
        self._stmt_head.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bar.addWidget(self._stmt_head)
        self._stmt_bar.setVisible(False)
        root.addWidget(self._stmt_bar)

        #: 这一拍里**工具没建模、但会原样执行**的那些行（`play ambience` /
        #: `show screen` / 自定义块）—— 它们不属于任何一格，从前只躺在
        #: 折叠着的高级区里，卡片看起来像"什么都没有"。
        #:
        #: ⚠ **不放进语句条**（2026-10-18）：台词拍也可能有这种行
        #: （`play ambience` 后面紧跟一句台词就是同一拍），而语句条在没有
        #: 语句时是隐藏的 —— 放进去就会跟着一起消失。
        self._loose_label = QLabel('')
        self._loose_label.setProperty('role', 'caption')
        self._loose_label.setWordWrap(True)
        self._loose_label.setVisible(False)
        root.addWidget(self._loose_label)

        # —— 语句参数（**跟着这一拍的语句走**；没有参数就整组隐藏）——
        #
        # ⚠ 位置在**台词之前**（2026-10-18 挪上来的）：语句拍的参数就是它的
        # 正文，而台词是可选的附加物。放在台词下面那版实测要滚一整屏才看得见。
        #
        # 为什么要有这一组（用户 2026-10-07 的意见）：动态参数列**只在 Excel**
        # 里做了，卡片上还是"候选注释"（`app.beats.spec_arg_choices_for`
        # 一直是"只有测试在用"）。可编剧是**在卡片上写**的 —— 表里有下拉、
        # 卡片上没有，等于这条能力只对着会开 Excel 的人。
        #
        # 这里一格一个参数：有候选就是**可编辑下拉**（手打新值照样能过，
        # 与 Excel 的 `showErrorMessage=False` 同一条口径），没候选就是普通
        # 输入框。值写回的路径与 Excel **完全同一条**
        # （`app.beats.apply_spec_arg_fields` → `beat_model.fold_spec_arg_columns`），
        # 所以"卡片上改"与"表里改"的结果必然一致。
        self._spec_host = QWidget()
        self._spec_form = QVBoxLayout(self._spec_host)
        self._spec_form.setContentsMargins(0, 0, 0, 0)
        self._spec_form.setSpacing(T.SPACE['xs'])
        self._spec_host.setVisible(False)
        #: `{列名: 颜色小方块}`（值是颜色时才建，见 `set_spec_args`）
        self._spec_swatches: dict = {}
        root.addWidget(self._spec_host)

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

        # —— 内联标签**预览**（2026-10-18）——
        #
        # Ren'Py 的文字样式写在台词里：`{color=#f00}红{/color}` /
        # `{i}斜{/i}` / `{b}粗{/b}`。用户在卡片上看到的是**源码**（那是正确的：
        # 他改的就是源码），但"这句话最终长什么样"只能进游戏才知道 ——
        # 于是这里给一行真·富文本预览。只在文本里**确实有标签**时出现，
        # 平时不占版面。
        self._rich_preview = QLabel('')
        self._rich_preview.setObjectName('richPreview')
        self._rich_preview.setTextFormat(Qt.RichText)
        self._rich_preview.setWordWrap(True)
        self._rich_preview.setProperty('role', 'caption')
        self._rich_preview.setVisible(False)
        root.addWidget(self._rich_preview)
        # 输入时实时更新预览（只在这句话真有标签时才显示）
        self._text.textChanged.connect(self._refresh_rich_preview)

        # —— 台词后缀（`(what_color="#f00")` / `with` / `id`）：真有时才露 ——
        #
        # 它就是"给这句台词上色/加样式"那一类写法。从前它在导出时被静默
        # 吃掉（`_RE_SAY` 只 match 到引号），现在它有自己的隐藏列
        # （`SayTail`），高级区里能改；这里再给一行**看得见**的提示，
        # 免得"这句台词带样式"这件事只存在于折叠区里。
        self._say_tail_label = QLabel('')
        self._say_tail_label.setProperty('role', 'caption')
        self._say_tail_label.setWordWrap(True)
        self._say_tail_label.setVisible(False)
        root.addWidget(self._say_tail_label)

        # —— 舞台（**没有改动时只占一行**）——
        #
        # 用户 2026-10-18：不是每个写法都跟图片相关。一条 `play_music` 或
        # `styled "红色" "#f00"` 的拍，舞台那三块（立绘网格 / 当前舞台 /
        # 背景·音乐·语音）**一个字都没有**，却占了整整一屏 —— 真正能改的
        # 参数反而要滚过去找。所以：没有舞台内容 → 收成一行说明 + 一个
        # 「改舞台」按钮；有内容 → 与从前一模一样。
        self._stage_has = True          # 载入前不收起（构造期间没有"这一拍"）
        self._stage_open = False
        self._stage_toggle = QPushButton('＋ 改舞台（背景 / 立绘 / 音乐）')
        self._stage_toggle.setProperty('variant', 'chip')
        self._stage_toggle.setFlat(True)
        self._stage_toggle.setToolTip('这一拍没有舞台改动。点开可以加立绘、'
                                      '换背景、放音乐。')
        self._stage_toggle.clicked.connect(self._toggle_stage)
        self._stage_toggle.setVisible(False)
        root.addWidget(self._stage_toggle, 0, Qt.AlignLeft)
        self._stage_empty = QLabel('舞台：这一拍没有改动（背景 / 立绘 / '
                                   '音乐都沿用上一拍）')
        self._stage_empty.setProperty('role', 'caption')
        self._stage_empty.setWordWrap(True)
        self._stage_empty.setVisible(False)
        root.addWidget(self._stage_empty)

        self._stage_box = QWidget()
        stage_lay = QVBoxLayout(self._stage_box)
        stage_lay.setContentsMargins(0, 0, 0, 0)
        stage_lay.setSpacing(T.SPACE['xs'])
        root.addWidget(self._stage_box)

        # —— 立绘网格 ——
        head = QHBoxLayout()
        head.setSpacing(T.SPACE['xs'])
        head.addWidget(self._section('立绘'))
        hint = QLabel('点 chip 换表情/位置 · 双击卡换图')
        hint.setProperty('role', 'caption')
        head.addWidget(hint, 0)
        head.addStretch(1)
        stage_lay.addLayout(head)
        self._grid = SpriteGridWidget(symbols=self._symbols, loader=loader,
                                      on_pick_image=self._on_pick_image)
        stage_lay.addWidget(self._grid)

        # —— 当前舞台（这一拍生效的状态）——
        # 用户 2026-10-03："为何不写明现在的舞台情况是什么样的，旧版明明都有"
        # 旧版预览面板用的是 `stage_snapshot.stage_digest`，卡片搬 Qt 时没接上。
        stage_lay.addWidget(self._section('当前舞台（这一拍生效）'))
        self._stage_label = QLabel('')
        self._stage_label.setProperty('role', 'note')
        self._stage_label.setWordWrap(True)
        self._stage_label.setFont(QFont(T.FONTS['mono'][0],
                                        T.FONTS['mono'][1]))
        self._stage_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        stage_lay.addWidget(self._stage_label)

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

        stage_lay.addWidget(_asset_row([('Scene', '背景')], 'assetRowScene'))
        stage_lay.addWidget(_asset_row([('BGM', '音乐'), ('Voice', '语音')],
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

        # —— 语句参数组已经挪到卡片顶部（见上面的 `_spec_host`）——

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
        # 标签里列的是"这里**能改什么**"。2026-10-19 补上菜单头
        # （`menu 名(args):` 的名与参数）—— 不写进来，人不会知道菜单拍
        # 也能改这两样（它们只出现在菜单拍上）。
        return (('▾' if self._adv_open else '▸')
                + ' 高级区（代码 / Label / 类型 / Notes / Stage / 台词后缀 / 菜单头）')

    # ---------- 语句条 + 舞台收放（2026-10-18）----------

    def _toggle_stage(self):
        """「改舞台」：把收起来的舞台块展开/再收起。"""
        self._stage_open = not self._stage_open
        self._apply_stage_visibility()

    def _apply_stage_visibility(self):
        """按"这一拍有没有舞台内容 / 用户是不是手动展开了"决定显不显示。"""
        show = bool(self._stage_has or self._stage_open)
        self._stage_box.setVisible(show)
        self._stage_empty.setVisible(not show)
        self._stage_toggle.setVisible(not show)
        if not show:
            self._stage_toggle.setText('＋ 改舞台（背景 / 立绘 / 音乐）')

    @staticmethod
    def _has_stage_content(fields: dict, cards: list) -> bool:
        """
        这一拍**有舞台内容**吗（立绘 / 背景 / 音乐 / 音效 / 语音 / 舞台指令）？

        `Stage` 那一格是结构化列的**投影**（`compose_stage`），所以它非空
        就等于"这一拍有 scene/show/hide/with"——不必再逐列判。
        """
        if cards:
            return True
        for key in ('Scene', 'BGM', 'SFX', 'Voice', 'Stage'):
            if str((fields or {}).get(key, '') or '').strip():
                return True
        return False

    def _set_statement(self, statement, loose):
        """填顶部的「语句条」+ 下面那行"原样行"提示（各自独立显隐）。"""
        info = dict(statement or {})
        name = str(info.get('name') or '').strip()
        label = str(info.get('label') or '').strip()
        head = str(info.get('head') or '').strip()
        rows = [str(x).strip() for x in (loose or ()) if str(x).strip()]
        # —— 原样行（与语句条无关：台词拍也可能有）——
        if rows:
            # **全列出来**（2026-10-18 改）：从前只说第一行 + "共 N 行"，
            # 一张拍里同时挂着 `pause` / `with` / `window hide` / 自定义块体时，
            # 人只看得到 `pause 1.0 hard` —— 另外四行"会发生什么"是看不见的。
            # 这些行都会**原样执行**，所以它们属于"这一拍是什么"的一部分。
            # 太多时截到 6 行，并明说还有几行（高级区里能看全）。
            shown = rows[:6]
            text = '这一拍还会原样执行：\n' + '\n'.join(f'· {r}' for r in shown)
            if len(rows) > len(shown):
                text += f'\n… 还有 {len(rows) - len(shown)} 行'
            text += '\n（这些行在「高级区 · 代码」里改）'
            self._loose_label.setText(text)
            self._loose_label.setToolTip('\n'.join(rows))
            self._loose_label.setVisible(True)
        else:
            self._loose_label.setText('')
            self._loose_label.setVisible(False)
        # —— 语句条 ——
        if not name:
            self._stmt_bar.setVisible(False)
            self._stmt_title.setText('')
            self._stmt_head.setText('')
            self._text.setPlaceholderText('这一拍要说的台词…')
            return
        title = f'◆ {name}'
        if label:
            title += f' · {label}'
        if not head:
            title += '（这一拍是一条语句，不是台词）'
        self._stmt_title.setText(title)
        # 原文那行**折行显示**（2026-10-18）：项目声明的语句可以很长
        # （七八个参数 + 中文备注），单行 QLabel 会被窗口**静默截断** ——
        # 而这一行是"这一拍到底写了什么"的唯一凭据，看不见就等于没有。
        self._stmt_head.setText(f'原文：{head}' if head else '')
        self._stmt_head.setWordWrap(True)
        self._stmt_head.setToolTip(head)
        self._stmt_head.setVisible(bool(head))
        self._text.setPlaceholderText('这一拍还可以说一句台词（可选）…')
        self._stmt_bar.setVisible(True)

    #: 颜色值（`#rrggbb` / `#rgb` / `rgb()`）—— 那一格旁边给个色块，
    #: 否则 `#ff0000` 对编剧只是一串乱码（用户 2026-10-18 说的"有色字体"）。
    _COLOR_RE = re.compile(r'^(?:#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))$')

    def _make_swatch(self, value: str):
        """给颜色值做一个小色块（不是颜色 → 不建）。"""
        if not self._COLOR_RE.match(str(value or '').strip()):
            return None
        sw = QFrame()
        sw.setObjectName('colorSwatch')
        sw.setFixedSize(14, 14)
        self._paint_swatch(sw, value)
        return sw

    @staticmethod
    def _paint_swatch(sw, value: str):
        color = str(value or '').strip()
        sw.setProperty('color', color)
        sw.setStyleSheet(f'background:{color}; border:1px solid #666;'
                         f' border-radius:3px;')
        sw.setToolTip(f'这一格的值是颜色：{color}')

    # ---------- 内联标签预览（`{color=…}` / `{i}` / `{b}`） ----------

    def _refresh_rich_preview(self):
        """
        台词框里的 Ren'Py 文字标签 → 一行**真·富文本预览**。

        为什么（用户 2026-10-18："自定义块我可以要求一句话中只有部分关键词
        变色"）：卡片上看到的是源码 `{color=#ff0000}红{/color}` —— 那是**对的**
        （他改的就是源码），但"这句最终长什么样"只能进游戏才知道。真机里
        标签写错（少了 `{/color}`、颜色拼错）是很常见的，而它**不报错**，
        只是那句话在游戏里颜色不对。预览能把这件事提前到编辑时。

        只在**确实有标签**时显示；没有标签的普通台词不占版面。
        """
        try:
            raw = self._text.toPlainText() if hasattr(self, '_text') else ''
        except Exception as e:                   # noqa: BLE001 - 界面不该因此炸
            swallow('ui_qt.main_window._refresh_rich_preview', e)
            raw = ''
        html, has_tag = renpy_text_to_html(raw)
        self._rich_preview.setText(html if has_tag else '')
        self._rich_preview.setVisible(bool(has_tag))
        if has_tag:
            self._rich_preview.setToolTip(
                '这是这句话在游戏里的样子（只预览 color / b / i / u / size，'
                '其余标签与 [变量] 不在预览里展开）')

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
        """
        values = dict(values or {})
        choices = dict(choices or {})
        self._loading = True
        try:
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
            # **按"这一格属于哪条语句"分组**（2026-10-18）。
            #
            # 为什么必须分组：一张拍可以同时带着好几条语句 —— 声明过的
            # `mycue …`、它的块体、跟在后面的 `pause 1.0` / `with fade` /
            # `window hide`。这些语句的参数列会一起长出来，从前**没有任何
            # 归属标记**，卡片上就是"内容 / 强度 / 秒数 / 转场 / 动作"一串，
            # 人根本不知道「秒数」改的是哪一行。一条语句一组、组前写明那一行
            # （原文），才谈得上"自适应排版"。
            groups: list = []
            for col in (columns or ()):
                key = str(col.get('name') or '')
                if not groups or groups[-1][0] != key:
                    groups.append((key, []))
                groups[-1][1].append(col)
            multi = len(groups) > 1
            loose_lines = [str(x).strip() for x in (loose or ())
                           if str(x).strip()]
            stmt_name = str((statement or {}).get('name') or '').strip()
            stmt_head = str((statement or {}).get('head') or '').strip()

            def _caption_of(name: str) -> str:
                """这一组的标题：优先用**源码里那一行**，其次才是语句名。"""
                if name and name == stmt_name and stmt_head:
                    # 头行末尾的 `:` 属于"块开始"，当标题时去掉（少一点噪声）
                    return stmt_head[:-1].rstrip() if stmt_head.endswith(':') \
                        else stmt_head
                for line in loose_lines:
                    if line.split(None, 1)[0:1] == [name]:
                        return line
                return name

            for gname, gcols in groups:
                if multi:
                    cap = QLabel(f'◆ {_caption_of(gname)}')
                    cap.setProperty('role', 'caption')
                    cap.setToolTip(
                        f'下面这几格改的是这一行：{_caption_of(gname)}')
                    cap.setStyleSheet('margin-top:4px; font-weight:600;')
                    self._spec_form.addWidget(cap)
                for col in gcols:
                    header = str(col.get('header') or '')
                    if not header:
                        continue
                    box = QHBoxLayout()
                    box.setSpacing(T.SPACE['xs'])
                    lab = QLabel(str(col.get('label') or col.get('param') or header))
                    lab.setProperty('role', 'caption')
                    lab.setMinimumWidth(72)
                    # **这一格是位置参数还是具名参数**（2026-10-18）：
                    # 两者在脚本里的写法不同（`se` vs `gain=-6`），而卡片上
                    # 长得一模一样 —— 人改错了只会得到一条"参数过多"的报错。
                    # 提示写进 tooltip，不占版面。
                    _pos = col.get('positional')
                    if _pos is None:
                        lab.setToolTip(
                            f'{header}（具名参数，脚本里写成 '
                            f'{col.get("param")}=值）')
                    else:
                        lab.setToolTip(
                            f'{header}（位置参数：按顺序写，第 {int(_pos) + 1} 位）')
                    box.addWidget(lab, 0)
                    opts = [str(o) for o in (choices.get(col.get('param'), ()) or ())]
                    value = str(values.get(header, '') or '')
                    # 颜色值：先给色块，再给输入框（色块跟着输入实时变）
                    sw = self._make_swatch(value)
                    if sw is not None:
                        self._spec_swatches[header] = sw
                        box.addWidget(sw, 0)
                    if opts:
                        w = QComboBox()
                        w.setEditable(True)          # 手打新值照样能过（同 Excel）
                        w.addItem('')
                        w.addItems(opts)
                        w.setToolTip('从下拉里选，或直接写一个项目里在用的值')
                    else:
                        w = QLineEdit()
                        w.setPlaceholderText('留空 = 不带这个参数')
                    if sw is not None:
                        # 实时跟着输入变（不重建控件，光标不会被冲掉）
                        if isinstance(w, QComboBox):
                            w.editTextChanged.connect(
                                lambda text, s=sw: self._paint_swatch(s, text))
                        else:
                            w.textChanged.connect(
                                lambda text, s=sw: self._paint_swatch(s, text))
                    self._spec_widgets[header] = w
                    self._widgets[header] = w         # `_set_value` / `_get_value` 复用
                    box.addWidget(w, 1)
                    if col.get('multi'):
                        # "这一格能装好几个词"（多音轨 / 多声部）：**说出来**，
                        # 否则人只会填一个，而另一个在脚本里其实也能写。
                        more = QLabel('可写多个（空格分隔）')
                        more.setProperty('role', 'caption')
                        more.setToolTip('例：`se se2` —— 这一格能连着写多个值')
                        box.addWidget(more, 0)
                    self._spec_form.addLayout(box)
            for header, w in self._spec_widgets.items():
                value = str(values.get(header, '') or '')
                if isinstance(w, QComboBox):
                    w.setCurrentText(value)
                else:
                    w.setText(value)
            self._spec_host.setVisible(bool(self._spec_widgets))
        finally:
            self._loading = False
        self._set_statement(statement, loose)
        # ⚠⚠ **参数格是"载入"的一部分**（2026-10-18 修）：
        #   它们是在 `load_fields` **之后**才建出来的（`_sync_spec_args`），
        #   于是 `_loaded_fields` 里没有这几个键 → `is_modified()` 一上来就真
        #   → 卡片右下角写着"有未保存的改动"、💾 亮着（截图当场看到）。
        #   这里把**这一拍的**参数值并进基线（上一拍留下的那些键先删掉）。
        if self._loaded_fields:
            from storygraph.story.spec_args import is_dynamic_column
            for stale in [k for k in self._loaded_fields
                          if is_dynamic_column(k)]:
                self._loaded_fields.pop(stale, None)
            self._loaded_fields.update(self._spec_values())
        self.refresh_dirty()

    def _spec_values(self) -> dict:
        """这一组控件当前的值（`{列名: 值}`）—— 与 Excel 的动态列**同名**。"""
        return {h: self._get_value(h)
                for h in (getattr(self, '_spec_widgets', None) or {})}

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

    def set_choice_groups_provider(self, fn):
        """
        注入 `tag → 分组候选`（右键候选列表分栏显示"服装 / 表情"这类）。

        ⚠ 面板是窗口唯一持有网格的对象，所以**每个网格方法都要在这里转一手**
        （`set_symbols` / `set_path_resolver` 同）。漏了就是
        `'BeatCardEditor' object has no attribute …`，而这个异常在
        `load_script` 的 `except` 里会变成**模态弹窗** —— 测试挂住、用户看到卡死。
        判据：`tests/test_custom_statement_entry.py` 里直接查面板有没有这个方法。
        """
        self._grid.set_choice_groups_provider(fn)

    def set_choice_groups(self, groups):
        """同上的直推版本（窗口一次性拿到全部分栏时用）。"""
        self._grid.set_choice_groups(groups)

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
            # 舞台块收不收起来：跟着**这一拍有没有舞台内容**走（每次载入重判，
            # 手动展开状态也复位 —— 翻到下一拍时不该继承上一拍的展开）。
            self._stage_has = self._has_stage_content(fields, sprite_items)
            self._stage_open = False
            self._apply_stage_visibility()
            # 台词后缀：有才提示（它就是"给这句台词上色/加样式"那一类写法）
            tail = str(fields.get('SayTail', '') or '').strip()
            self._say_tail_label.setText(
                f'这句台词带后缀（文字样式 / 控制）：{tail}'
                f' —— 在「高级区 · 台词后缀」里改' if tail else '')
            self._say_tail_label.setVisible(bool(tail))
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
        # **语句参数**同理：它一直可见（有参数才出现），所以始终写回。
        # 键与 Excel 的动态列**同名**，折叠走同一条路（见 `EditorSession.apply`）。
        out.update(self._spec_values())
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
        out = {n: self._get_value(n) for n in self._all_names()}
        out.update(self._spec_values())
        return out

    def set_kind(self, kind: str) -> None:
        """
        把（归一化后的）「类型」推回面板，**同时同步基线**（不制造"未保存"）。

        为什么需要它：`say` / `narration` 这一对里，类型是**说话人的投影**
        （见 `app.beats.kind_from_speaker`）。用户填了说话人之后，高级区
        那一格必须立刻跟着变 —— 否则它一直写着 `narration`，用户合理地
        以为"我填的说话人没生效"（2026-10-05 实测报的）。左栏那一行的
        前缀（☁）同源，由 `refresh_list_row` 负责。
        """
        self._loading = True
        try:
            self._set_value('Kind', str(kind or ''))
            if self._loaded_fields:
                self._loaded_fields['Kind'] = str(kind or '')
        finally:
            self._loading = False
        self.refresh_dirty()

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

        两条线，**不是同一条**（2026-10-18）：

          · 200 字（`CHAR_WARN`）= 进游戏**对话框可能溢出**的提醒（软）；
          · 32767 字（`CELL_TEXT_LIMIT`）= Excel 单元格**装不下**的硬上限，
            导出会被拒绝 —— 这里提前说，别让人写到三万字才被打回。
        """
        from storygraph.core.cell_limits import CELL_TEXT_LIMIT

        text = str(self._text.toPlainText() or '')
        raw = len(text)
        n = len(''.join(text.split()))
        if raw > CELL_TEXT_LIMIT:
            self._char_label.setText(
                f'{raw} 字 —— 超过 Excel 单元格上限 {CELL_TEXT_LIMIT}，'
                f'这样存不进表；拆成几拍')
            self._char_label.setProperty('role', 'warn')
        elif n > self.CHAR_WARN:
            self._char_label.setText(
                f'{n} 字 —— 超过 {self.CHAR_WARN} 字，游戏里可能溢出对话框')
            self._char_label.setProperty('role', 'warn')
        elif n:
            self._char_label.setText(f'{n} 字')
            self._char_label.setProperty('role', 'caption')
        else:
            self._char_label.setText('')
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


# `_overwritable` 已搬到 `window_export.py`（唯一的使用者在那儿，见那边的
# 说明：自由函数不在 `REQUIRES` 契约里，搬 mixin 时漏了它 = 导出被拦下时
# `NameError`、界面什么都不发生）。这里保留 `_overwritable` 这个名字，
# 是因为它曾经是 `main_window` 的公共面（测试与文档都按那个名字引用）。


class MainWindowQt(GraphOpsMixin, CardPageMixin, NodeEditMixin,
                   WindowExportMixin, WindowAssetsMixin,
                   WindowKeyboardMixin, WindowStateMixin, QMainWindow):
    """
    Qt 版主窗口 —— **两页**：剧情结构图（第一屏）→ 卡片编辑器（点进模块才进）。

    **职责拆分**（P3-14，2026-10-05）
    --------------------------------
    原本是 3452 行 / 126 个成员的上帝对象（`EditorSession` 的另一半）。
    现在七块各住一个文件，全部做成 mixin —— **调用方一行都不用改**
    （这是搬家不是改契约：菜单、工具栏、信号、`open_editor` 全都没动）：

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
        （下次打开回到上次那个项目 + 摆回布局，2026-10-13 用户要求）。

    **本类自己留 36 个成员**，全是"界面骨架 + 页切换 + 谁都要用的工具"：
    三个页构建器（`_build*`）、`_set_page` / `_update_crumb` /
    `_sync_action_states`、状态栏与日志（`_status` / `_log`）、
    静默失败上报（`_swallow_mark` / `_report_swallowed`）、
    图的所有权（`_set_graph`）、属性（`beats` / `_loaded_idx` …）、
    两个预取常量，以及 Qt 重写点（`closeEvent`）。

    **基类顺序：六个 mixin 必须排在 `QMainWindow` 前面**（实测踩过）
    --------------------------------------------------------------------------
    `eventFilter` / `closeEvent` 是 **Qt 虚函数** —— `QObject` 自己就定义了。
    基类写成 `(QMainWindow, WindowKeyboardMixin, …)` 时 MRO 是
    `MainWindowQt → QMainWindow → … → QObject → WindowKeyboardMixin`，
    于是 `QObject.eventFilter` **赢**、mixin 那份成了死代码：

        QTest.keyPress(...)  →  一个键都没接上
        （实测 13 条键盘用例全红：回车、Ctrl+S、裸↓、空格拖拽…，
         而 `hasattr(MainWindowQt, 'eventFilter')` 照样是 True）

    反过来写 MRO 仍然合法（C3 线性化），`super().__init__()` 也照样链到
    `QMainWindow` —— 六个 mixin 都**没有**自己的 `__init__`，链上一路直通。
    这类事故是"搬家不是改契约"里最容易漏的一种：方法**在**（`hasattr` 真），
    **谁先被找到**却变了。由 `tests/test_main_window_mixins.py` 守。

    五个 mixin 的成员在本类里**不许**有同名 —— 同名会让子类那份**静默**赢。

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
        # ⚠ **命令行的意图要先记下来，再 `_build()`**（2026-10-13）：
        #   `_build()` 会建菜单，而菜单要读"上次的项目"给勾选框定状态；
        #   而 `_boot`（第一个事件循环回合）里的会话记忆要能回答
        #   "命令行是不是已经明确指定了项目/章节"—— 那个判断就靠这两个属性。
        #   放到 `_build()` 之后设，就是"恢复跑完了才知道命令行给了什么"。
        self._pending_script = Path(script_path) if script_path else None
        self.setWindowTitle('剧情编辑器 · 剧情结构图 + 卡片界面')
        self.resize(1600, 950)
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
        # ⚠ 存成实例属性：**会话记忆要摆回分栏宽度**（`window_state.py`）。
        #   以前只在局部变量里，退出时谁也拿不到它 —— 布局也就无从记起。
        self._split_graph = split
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
        # 用户 2026-10-05："在「变量分析」右边加一个检查资源完整性的功能，
        # 后出示检查报告"。
        self._health_btn = QPushButton('📦 资源体检')
        self._health_btn.setToolTip(
            '检查资源表与项目资源是否一一对应（缺失 / 未登记 / 重复 / '
            '指向 game 之外），并出示报告')
        self._health_btn.clicked.connect(self.check_asset_health)
        brow.addWidget(self._health_btn)
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
        # ⚠ 包 lambda：`clicked` 会发一个 `bool`，直接 connect 会让它变成
        # `save_layout(target=False)` —— "没给路径就弹另存为"那条分支走不到，
        # 接着拿 `False` 当路径写文件（TypeError）。三处同理（见下面的
        # `load_layout` / `export_control_flow`，以及工具栏的 `act()`）。
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
        self._export_flow_btn = QPushButton('⤓ 导出控制流 .rpy')
        self._export_flow_btn.setToolTip(
            '按图生成 Ren\'Py 控制流脚本：每个模块一个 `<label>_control`，'
            '里面 call 原 label，再按连线写 jump/call/菜单/条件')
        # 同上：`clicked` 的 bool 会变成 `target=False`，于是"没给路径就弹
        # 「保存为」"那条分支走不到（拿 False 当路径 → TypeError）。
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
        self._insert_btn.clicked.connect(lambda: self.insert_card(after=True))
        bh.addWidget(self._insert_btn)
        self._insert_before_btn = QPushButton('＋ 插到前面')
        self._insert_before_btn.setToolTip('在当前这一拍**前面**插入一张空卡片')
        self._insert_before_btn.clicked.connect(lambda: self.insert_card(after=False))
        bh.addWidget(self._insert_before_btn)
        self._restore_btn = QPushButton('↩ 撤销结构操作')
        self._restore_btn.setToolTip(
            '撤销最近一次增删卡片（可连按多级）：删掉的放回原位、插进去的撤掉')
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
        self._split_card = split          # 同上：会话记忆要摆回它
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

        # —— 第一屏：剧情结构 ——
        #
        # ⚠ 两条路**必须分开**（用户 2026-10-03 纠正）：
        #   · 「打开项目」= 资源表（xlsx）那条路：立绘/音乐候选、缩略图、
        #     参与角色。**它不往剧情图里放任何东西**。
        #   · 「导入章节」= 剧情图那条路：图里只有用户自己导入的章节。
        # 以前把两者写成一句话（"打开项目 → 建出结构图"），40 个脚本一次性
        # 灌进图里变成 51 个块 —— 用户："这还选个毛线啊！看都要看瞎了！"
        act('🧩 自定义语句', self.learn_custom_statement,
            '教工具认识项目自己的写法（swim_suit / mycue 这类）：'
            '先扫出"工具还不认识的写法"，挑一个、说清它后面跟什么，'
            '粘一行剧本切一下看看，保存就写进 storygraph.rules.json —— '
            '之后这些格就是表格列、候选值就是下拉。'
            '候选分栏与项目清单（删除 / 重置）在同一个弹窗里。',
            primary=True)
        # ⭐ 「导出」在最上面那一栏（用户 2026-10-21：
        #    "导出应当放在最上面那一栏……因为太常用了"）。
        #
        #   它跟"现在在哪一页"**无关** —— 导的是**已载入的那一章**，所以两页
        #   顶部都有它。位置也有讲究：这条工具栏在窄窗口里会溢出进「»」菜单
        #   （实测 1500px 宽时装不下，需要 2349px），排在后面的按钮等于不存在；
        #   所以它排在第一个（「🧩 自定义语句」之后，用户 2026-10-10 要求那个
        #   入口在最上方，判据 `test_the_toolbar_entry_is_the_first_action`）。
        self._export_action = act('⤓ 导出 .rpy', self.export_script,
                                  '把这一章写回磁盘（编译 + 增量补丁 + 安全门禁）。\n'
                                  '最常用的落盘动作：**两页顶部都有它**，'
                                  '不用先回卡片页。\n'
                                  '路径默认是这张表该去的地方，可以改到 build/ 下先看一眼。',
                                  primary=True)
        # ⚠ 放在「自定义动作」**后面**：用户早先明确要求这个入口在**最上方**
        #   （2026-10-10："至少我想要个能够自定义的模块入口放在最上方"），
        #   判据 `test_the_toolbar_entry_is_the_first_action` 钉着这一条。
        act('🔑 AI 助手 · 填 API key', self.open_ai_key_dialog,
            'key 只存在你这台机器上（不写进项目、不上传）。填一次就能让 AI 干活；'
            '随时可以回来改或清掉。另：本工具也能作为 MCP server 被别的 AI 客户端连上'
            '（那时 key 是那个客户端的）')
        act('🤖 AI 助手', self.open_ai_panel,
            '跟它说要干什么（例：看看项目里有哪些我不认识的写法）。'
            '它会自己读项目、提议怎么改；**要写盘时会先弹窗给你看 diff**，'
            '你点头它才写', primary=True)
        act('📁 打开项目', self.open_project,
            '只加载资源表（xlsx：立绘 / 音乐 / 语音 / 变换 / 角色）'
            '与缩略图 —— **不往剧情图里放任何东西**')
        act('➕ 导入章节 .rpy', self.import_chapters,
            '可多选；图里只出现你导入过的章节（累加，不覆盖已导入的）',
            primary=True)
        act('📝 导入纯文字章节', self.import_plain_text,
            '选 .txt（作者写的纯文字）→ 自动转成 .rpy 落进 game/ 并导入；'
            '有认不出的角色写法会先问你一句（确认后写进 characters.xlsx）')
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
        act('📦 资源体检', self.check_asset_health,
            '检查资源表与项目资源是否一一对应，并出示报告（只读）')
        act('🩺 项目体检', self.show_health, '三级校验，只读检查，不写盘')
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
        self._back_action = act('← 返回剧情结构', self._back_to_graph,
                                '回到第一屏（Esc）')
        self._save_action = act('💾 保存这一拍', self.save_current_beat,
                                '把卡片改动提交到当前拍（Ctrl+S）', primary=True)
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
