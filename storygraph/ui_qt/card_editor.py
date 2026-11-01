"""
storygraph.ui_qt.card_editor
============================
**卡片编辑器**本体：一句台词 / 一个动作的"表单"（`BeatCardEditor`）。

2026-11-01 从 `ui_qt.main_window` 搬出来。这一块 1559 行，是主窗口里
最后一大块**不属于任何 mixin** 的代码 —— 搬之前 `main_window.py` 有 4040 行，
其中 39% 是卡片表单。

为什么它进不了 mixin：mixin 是"主窗口的职责分片"，而 `BeatCardEditor`
是一个**独立的 QWidget**（`MainWindowQt` 只是持有它的实例 `self._editor`），
不是窗口的方法。混进 mixin 会让 `REQUIRES` 白名单长出一堆"这个 mixin 需要
宿主有一个 _editor"的假依赖。

搬完的收益不只是行数：`main_window` 那 35 个方法位的预算（见
`tests/test_main_window_mixins.py`）终于松出两格给 `_build_card_page` /
`_build_toolbars` —— 它们正是把本模块**拼起来**的那两个函数。
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS
from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt.code_edit import CodeEditor
from storygraph.ui_qt.labeled_choices import (as_choices, combo_value,
                                              fill_combo, legacy_pairs,
                                              select_value)
from storygraph.ui_qt.mapping_status import refresh_mapping_status
from storygraph.ui_qt.size_panel import SizePanel
from storygraph.ui_qt.sprite_grid import SpriteGridWidget
from storygraph.ui_qt.stage_thumb import BeatStageThumb
from storygraph.ui_qt.text_html import renpy_text_to_html
from storygraph.ui_qt.wheel_guard import WheelSafeComboBox
from storygraph.ui_qt.window_layout import CARD_STAGE_THUMB
from storygraph.ui_qt.wrap_label import NarrowWrapLabel


class BeatCardEditor(QWidget):
    """
    一张**拍**的卡片：说话人 / 台词 / 立绘网格 / 背景 / 音乐 / 语音 / 备注
    + 可折叠高级区。

    它**不认识剧情语义** —— 只做 `load_fields(dict)` 与 `collect_fields()`。
    表情/位置是立绘网格上的 chip，不在这里。

    公共接口（窗口只用这些，不碰 `_` 开头的成员）：
        load_fields / collect_fields / sprite_cards / set_symbols
        set_speaker / speaker / set_text / text
        set_stage_loaded / set_stage_digest / show_stage_frame
        set_stage_size / size_panel / clear_stage_frame
        stage_thumb / shutdown_stage_thumb / is_modified / set_undo_enabled
    """

    #: 「保存这张卡片」/「撤销修改」两个按钮被按下时发出
    save_requested = Signal()
    undo_requested = Signal()

    def __init__(self, symbols=None, loader=None, parent=None,
                 on_pick_image=None, on_pick_asset=None,
                 on_pick_image_result=None, on_open_mapping=None,
                 on_notice=None):
        super().__init__(parent)
        self._symbols = symbols
        self._widgets: dict = {}
        self._adv_open = False
        self._on_pick_image = on_pick_image
        #: `(字段名, 当前值) -> 新值`：背景/音乐/语音那三格的「选择…」用它。
        #: 卡片不认识资源表与选择器 —— 那条路由主窗口注入（同一个回调模式）。
        self._on_pick_asset = on_pick_asset
        #: 立绘网格的"带结论"选择器回调（`PickResult`，提示条要用）。
        self._on_pick_image_result = on_pick_image_result
        #: 卡片上的「去『资源映射』修」入口（主窗口注入）。
        self._on_open_mapping = on_open_mapping
        #: 网格里"没写成"的事（选择器拒绝…）越级上报给主窗口（控制台 + 状态栏）。
        self._on_notice = on_notice
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
        #: 当前这一拍的控制流信息（`call` / `jump`）；None = 不是控制流拍。
        #: 见 `set_flow`（2026-10-22，P0-4 第一阶段）。
        self._flow: dict | None = None
        self._build(loader)
        self._connect_dirty_tracking()

    # ---------- 界面 ----------

    def _section(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setProperty('role', 'section')
        return lab

    def _build_flow_bar(self, root):
        """
        控制流卡（`call` / `jump`）：这一拍**是什么**、目标能不能改、返回哪。

        为什么单独一条（2026-10-22，P0-4）：`call side` 以前是一张"没有台词
        的空旁白卡"，只有一行"这一拍还会原样执行：call side"，目标只能去
        高级区手打。这里把"调用 / 跳转"变成卡片上的一等公民，且类型切换
        会明确说"跳转不返回"。
        """
        self._flow_bar = QFrame()
        self._flow_bar.setObjectName('flowBar')
        self._flow_bar.setProperty('role', 'card')
        lay = QVBoxLayout(self._flow_bar)
        lay.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                               T.SPACE['sm'], T.SPACE['sm'])
        lay.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(T.SPACE['xs'])
        self._flow_title = NarrowWrapLabel('')
        self._flow_title.setProperty('role', 'section')
        self._flow_title.setWordWrap(True)
        head.addWidget(self._flow_title, 1)
        self._flow_type = QComboBox()
        self._flow_type.addItem('↳ 调用（会返回）', 'call')
        self._flow_type.addItem('→ 跳转（不返回）', 'jump')
        self._flow_type.setToolTip(
            '调用：执行目标 label，遇到 return 回到这里继续\n'
            '跳转：这一章在这里结束，不返回')
        head.addWidget(self._flow_type, 0)
        lay.addLayout(head)

        self._flow_detail = NarrowWrapLabel('')
        self._flow_detail.setProperty('role', 'caption')
        self._flow_detail.setWordWrap(True)
        lay.addWidget(self._flow_detail)

        target_row = QHBoxLayout()
        target_row.setSpacing(T.SPACE['xs'])
        cap = QLabel('目标')
        cap.setProperty('role', 'caption')
        target_row.addWidget(cap, 0)
        self._flow_target = QComboBox()
        self._flow_target.setEditable(True)
        self._flow_target.setInsertPolicy(QComboBox.NoInsert)
        self._flow_target.setMinimumWidth(220)
        self._flow_target.setToolTip(
            '从本文件 / 项目里已有的 label 里选，或直接写目标 label 名；\n'
            '点号分段的写法（main.route）也可以手写。')
        target_row.addWidget(self._flow_target, 1)
        lay.addLayout(target_row)

        # **参数 `(…)` 与 `from …`**（2026-10-22 第二阶段）：
        # 从前这两段只能在「高级区 · 代码」里手写，卡片上根本看不见 ——
        # 用户改得了目标、改不了传参，还得回源码找。现在它们和目标是同一条。
        # `jump` 不接受这两样（Ren'Py 语法），所以切到 jump 时直接禁用。
        self._flow_args = QLineEdit()
        self._flow_args.setPlaceholderText('参数（可空），例如 (1, flag)')
        self._flow_args.setToolTip(
            '`call side(1, 2)` 里括号那一段。留空 = 不传参。\n'
            '括号必须配平 —— 不配平的写法会让整份脚本解析不了。')
        args_row = QHBoxLayout()
        args_row.setSpacing(T.SPACE['xs'])
        cap_args = QLabel('参数')
        cap_args.setProperty('role', 'caption')
        args_row.addWidget(cap_args, 0)
        args_row.addWidget(self._flow_args, 1)
        lay.addLayout(args_row)

        self._flow_from = QLineEdit()
        self._flow_from.setPlaceholderText(
            'from 标识（可空），例如 _call_side_1')
        self._flow_from.setToolTip(
            '`call side from _call_side_1` 里 from 后面那个标识。\n'
            '它是**存档点**：同一个 label 被多处 call 时靠它区分，\n'
            '一改老存档就找不到返回点 —— 不确定就别动。')
        from_row = QHBoxLayout()
        from_row.setSpacing(T.SPACE['xs'])
        cap_from = QLabel('from')
        cap_from.setProperty('role', 'caption')
        from_row.addWidget(cap_from, 0)
        from_row.addWidget(self._flow_from, 1)
        lay.addLayout(from_row)

        self._flow_type.currentIndexChanged.connect(self._on_flow_edited)
        self._flow_target.currentIndexChanged.connect(self._on_flow_edited)
        self._flow_target.editTextChanged.connect(self._on_flow_edited)
        self._flow_args.textChanged.connect(self._on_flow_edited)
        self._flow_from.textChanged.connect(self._on_flow_edited)

        self._flow_bar.setVisible(False)
        root.addWidget(self._flow_bar)

    def _build(self, loader):
        """
        把**一张卡片的全部控件**按"作者填写的心流"从上到下建出来。

        顺序不是随手定的，每挪一次都对应一句用户原话（各节注释里有原话）：

            控制流卡 → 语句条 → 语句参数 → 台词区 → 当前舞台 + 舞台图
            → 立绘 / 背景 / 音乐 → 备注 → 条件 → 高级区 → 保存/撤销 → 提示条

        两条贯穿全卡的规矩：

          · **跟内容走**：控制流卡（`call` / `jump`）没有台词、没有立绘，
            那几块要收起来 —— 否则用户看到一屏空框，以为自己"还没填"；
          · **先给能改的**：语句参数排在台词**上面**，因为语句拍的参数
            就是它的正文，放在台词下面那版实测要滚一整屏才看得见。

        ⚠ **这里只建控件、不填数据。** 填数据是另一组入口
        （`set_text` / `set_speaker` / `set_flow` / `set_spec_args` / …）。
        两者一旦混在一起，构造期就会读到还没建好的控件 ——
        `_refresh_preview` 早于控件创建那条老坑就是这么来的。
        """
        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])

        # —— 控制流卡（call / jump）——
        self._build_flow_bar(root)

        self._build_statement_area(root)

        self._build_dialogue_area(root)
        self._build_stage_now_area(root)
        self._build_stage_area(root, loader)

        self._build_meta_area(root)

        self._build_footer(root)

    def _build_dialogue_area(self, root) -> None:
        """说话人 / 台词 / 字数 / 富文本预览 / 台词后缀（拆自 `_build`，2026-11-01）。"""
        # —— 台词区（说话人 / 台词 / 字数 / 富文本预览 / 后缀）——
        #
        # 整块套一个容器，为的是**控制流卡能把它收起来**（2026-10-23）：
        # 一张 `call side` 没有台词、没有立绘、没有舞台，但卡片把这三块
        # 原样铺开 —— 用户看到一屏空框，以为自己"还没填"。
        # 收起来之后，控制流卡上只剩：调用条 + 备注 + 条件 + 高级区。
        self._dialogue_box = QWidget()
        dlg_lay = QVBoxLayout(self._dialogue_box)
        dlg_lay.setContentsMargins(0, 0, 0, 0)
        dlg_lay.setSpacing(T.SPACE['xs'])
        root.addWidget(self._dialogue_box)

        # —— 说话人 ——（一行，右边留白，视觉上"标签 + 值"）
        row1 = QHBoxLayout()
        row1.setSpacing(T.SPACE['sm'])
        row1.addWidget(self._section('说话人'))
        # ⚠ 这格必须是 `WheelSafeComboBox`（2026-10-30 用户："滚动会导致选择切换"）。
        #   它是可编辑组合框 —— 实测这种控件**连焦点都不用**就会被滚轮换值，
        #   而且把滚动吃掉，于是滚页面时"人换了、页面没动"。
        self._speaker = WheelSafeComboBox()
        self._speaker.setEditable(True)
        self._speaker.setMinimumWidth(160)
        self._speaker.setMaximumWidth(240)
        self._widgets['Speaker'] = self._speaker
        row1.addWidget(self._speaker, 0)
        row1.addStretch(1)
        dlg_lay.addLayout(row1)

        # —— 台词（这张卡的主角，给足高度）——
        self._text = QPlainTextEdit()
        self._text.setPlaceholderText('这一拍要说的台词…')
        self._text.setFixedHeight(3 * 24 + 16)
        self._widgets['Text'] = self._text
        dlg_lay.addWidget(self._text)

        # —— 字数：超长台词在游戏里会溢出对话框 ——
        # tkinter 版有这一项，搬 Qt 时漏了。它属于"作者看不见的坑"：
        # 编辑器里换行好看，进游戏可能被裁掉，所以必须提示。
        self._char_label = QLabel('')
        self._char_label.setProperty('role', 'caption')
        self._char_label.setAlignment(Qt.AlignRight)
        dlg_lay.addWidget(self._char_label)

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
        dlg_lay.addWidget(self._rich_preview)
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
        dlg_lay.addWidget(self._say_tail_label)

    def _build_stage_now_area(self, root) -> None:
        """「当前舞台」这一拍的只读小图 + 摘要 + 尺寸格（拆自 `_build`，2026-11-01）。"""
        # —— 「当前舞台」+ 这一拍的图（**永远可见**，2026-10-27）——
        #
        # 位置与大小是用户当面指正的（第一版放错了两处）：
        #
        #   > 不对！！！你这个不能放卡片界面啊！！！应该缩小放舞台界面！
        #   > 当前舞台下面那一栏……缩小！再缩小，直到能看到全貌！不然你现在
        #   > 显示都显示不全何来调整啊？？？
        #
        # 所以：`_stage_now` = 「当前舞台（这一拍生效）」标题 + **一行**
        # （左：288×162 的整帧小图；右：这一拍舞台的文字摘要）。
        # 图与摘要**并排**而不是上下叠：摘要本来就要占那几行，图因此几乎
        # 不吃额外高度 —— 卡片留给"能改的东西"的地方才够。
        #
        # 为什么这一块**不在 `_stage_box` 里面**（那一块装的是能改的输入）：
        # 它是**只读的"现在台上什么样"**，和"这一拍要不要改舞台"无关 ——
        # 真机一章 285 拍里 210 拍没有舞台改动，而"这一拍台上是谁、在哪、
        # 什么曲子"对**那些**拍最有价值；跟着一起收起，就等于最该看的时候
        # 什么都没有（用户 2026-10-27 第一次要这张图的理由就是这个）。
        # 2026-10-29 起 `_stage_box` 也常显了（见下面那段），但两者仍然分开：
        # 一个是"看"，一个是"改"。
        self._stage_now = QWidget()
        now_lay = QVBoxLayout(self._stage_now)
        now_lay.setContentsMargins(0, 0, 0, 0)
        now_lay.setSpacing(T.SPACE['xs'])
        # 用户 2026-10-03："为何不写明现在的舞台情况是什么样的，旧版明明都有"
        # 旧版预览面板用的是 `stage_snapshot.stage_digest`，卡片搬 Qt 时没接上。
        now_lay.addWidget(self._section('当前舞台（这一拍生效）'))
        now_row = QHBoxLayout()
        now_row.setSpacing(T.SPACE['sm'])
        self._stage_thumb = BeatStageThumb(parent=self, compact=True)
        # 卡片里这张小图再收窄一档（240×135，仍是 16:9）：288+45+8 的整行最小宽
        # 341 会把卡片地板顶在 365px，超过"卡片最小宽 ≤340"的预算。
        # 见模块顶部 `CARD_STAGE_THUMB` 的说明。
        self._stage_thumb.setFixedSize(*CARD_STAGE_THUMB)
        now_row.addWidget(self._stage_thumb, 0, Qt.AlignTop)
        self._stage_label = NarrowWrapLabel('')
        self._stage_label.setProperty('role', 'note')
        self._stage_label.setWordWrap(True)
        self._stage_label.setFont(QFont(T.FONTS['mono'][0],
                                        T.FONTS['mono'][1]))
        self._stage_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._stage_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        now_row.addWidget(self._stage_label, 1)
        now_lay.addLayout(now_row)
        # —— 「尺寸」一格（P1，2026-10-31）——
        #
        # 用户二轮反馈："针对 bg 和 cg 不能改像素的问题……那个超清像素导入导致
        # 的错位"。尺寸是**持续状态**（背景一直是那个 at，直到下一次 scene），
        # 所以这一格不挂在"这一拍写的 scene/show"上，而是每个**台上能看见的
        # 对象**一行：背景 + 各 CG/UI 图层，行说明里写清是哪一拍设的。
        #
        # 面板只是一张表（`ui_qt/size_panel.py`）：它不认识会话，选好之后发
        # `size_requested`，计划 / diff 确认 / 写回全在窗口层（`_on_card_size_requested`）。
        self._size_panel = SizePanel()
        self._size_box = QWidget()
        size_lay = QVBoxLayout(self._size_box)
        size_lay.setContentsMargins(0, 0, 0, 0)
        size_lay.setSpacing(T.SPACE['xs'])
        size_lay.addWidget(self._section('尺寸'))
        size_lay.addWidget(self._size_panel)
        # 这一拍台上没有背景/图层时**整格收起**（`set_stage_size([])`）：
        # 空着还要占三行"没有尺寸可调"，在一张控制流卡上就是纯噪音 ——
        # 「当前舞台」那一块已经写着「背景：（未设置）」了。
        self._size_box.setVisible(False)
        now_lay.addWidget(self._size_box)
        root.addWidget(self._stage_now)

    def _build_stage_area(self, root, loader) -> None:
        """立绘网格 + 背景 / 音乐 / 语音三格（拆自 `_build`，2026-11-01）。"""
        # —— 立绘 / 背景 / 音乐（**永远完整显示**，2026-10-29 用户要求）——
        #
        # 这里曾经是"没有舞台内容 → 收成一行说明 + 一个「＋ 改舞台」按钮"
        # （2026-10-18：不是每一拍都跟图片有关，空的立绘网格会把真正能改的
        # 参数顶到看不见）。用户 2026-10-29 的反馈是**入口根本找不到**：
        #
        #   > 用户表示找不到入口！！！不要这个 + 改舞台/音乐这个了！直接完整
        #   > 显示……因为这是基本信息！默认不应当收起……
        #
        # 这个理由也对得上：背景 / 立绘 / 音乐就是"这一拍的舞台"，**空着本身
        # 也是信息**（这一拍没换景、没换人、没换曲 ⇒ 沿用上一拍），收起来反而
        # 要人先猜"里面藏了什么"。所以这一块不再有收起/展开两种状态：普通卡片
        # 上一直完整铺开，想加什么就直接点（控制流卡整体收起，见 `set_flow`）。
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
        self._grid = SpriteGridWidget(
            symbols=self._symbols, loader=loader,
            on_pick_image=self._on_pick_image,
            on_pick_image_result=self._on_pick_image_result,
            on_open_mapping=self._on_open_mapping)
        # 网格里"没写成"的事（选择器拒绝 / 给不出 spec）一路报给主窗口
        self._grid.notice.connect(self._on_grid_notice)
        stage_lay.addWidget(self._grid)

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
                # ⚠ **滚轮不许改这三格的值**（用户 2026-10-29："不要让滚轮上下
                # 滑动调整背景，音乐和语音这三个东西……太容易误触了！！！"）。
                # 卡片在滚动区里，鼠标扫过字段时滚轮就会把值换掉，而且换的是
                # 自由文本（图名 / bgm id），改错了不一定看得出来。
                # `WheelSafeComboBox`：只有点进去（有焦点）才响应滚轮，否则把
                # 滚轮转交给父级滚动区 —— 滚页面照常滚。规则写在那个模块里。
                combo = WheelSafeComboBox()
                combo.setEditable(True)
                self._widgets[name] = combo
                line.addWidget(combo, 1)
                # ⚠ 2026-10-30（用户第 5 条反馈）：从「选择…」缩成 24px「⋯」。
                # 三个「选择…」按钮的结构性成本是 58px/个（含 2 个一行的音乐+语音
                # 行 = 116px），而卡片地板被它顶在 380px —— 右栏拖窄就出横向滚动条。
                # 按钮**必须看得出能点**：保留 hover/边框样式 + tooltip 写全
                # （"从资源表里挑背景（分类 / 搜索 / 预览）"）。
                pick = QPushButton('⋯')
                pick.setFixedSize(T.CONTROL['iconW'], T.CONTROL['iconW'])  # 24×24
                # ⚠ **必须 `padding:0`**：默认 QSS 的内边距有十几 px，24px 宽的
                # 按钮里那个「⋯」会被裁成**一片空白**（offscreen 截图实测），
                # 用户看到的是"一个点了没反应的方块" —— 正是本项目最怕的形态。
                # 判据：`tests/test_ui_width.py::test_the_asset_pickers_are_icon_sized`。
                pick.setStyleSheet('padding:0;')
                pick.setToolTip(f'从资源表里挑{label}（分类 / 搜索 / 预览）')
                pick.setAccessibleName(f'选择{label}')
                pick.clicked.connect(lambda _=False, f=name: self._choose_asset(f))
                self._asset_btns[name] = pick
                line.addWidget(pick, 0)
                box.addLayout(line)
                row.addLayout(box, 1)
            return holder

        stage_lay.addWidget(_asset_row([('Scene', '背景')], 'assetRowScene'))
        stage_lay.addWidget(_asset_row([('BGM', '音乐'), ('Voice', '语音')],
                                       'assetRowAudio'))

    def _build_statement_area(self, root) -> None:
        """语句条 / 零散行 / 语句参数宿主（拆自 `_build`，2026-11-01）。"""
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
        self._stmt_title = NarrowWrapLabel('')
        self._stmt_title.setProperty('role', 'section')
        self._stmt_title.setWordWrap(True)
        bar.addWidget(self._stmt_title)
        self._stmt_head = NarrowWrapLabel('')
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
        self._loose_label = NarrowWrapLabel('')
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

    def _build_meta_area(self, root) -> None:
        """备注 / 条件 / 高级区（拆自 `_build`，2026-11-01）。"""
        # —— 备注 ——
        row3 = QHBoxLayout()
        row3.setSpacing(T.SPACE['sm'])
        cap = QLabel('备注')
        cap.setProperty('role', 'caption')
        row3.addWidget(cap, 0)
        self._comment = QLineEdit()
        self._comment.setPlaceholderText(
            '写进脚本的注释（不用自己加 #）：例 bg1.2 bgm13 / 场景备忘 / '
            '给同事的说明')
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
        # 标签只写"高级区"（2026-10-30：原来那句 51 字的长标签一个控件就把
        # 卡片最小宽顶到 356px、整卡地板 380px —— 右栏一窄就出横向滚动条）。
        # "里面有什么"放进 tooltip，信息一点没少。
        self._adv_btn.setToolTip(
            '代码 / Label / 类型 / Notes / Stage / 台词后缀 / 菜单头'
            '\n（收起时这些字段不会被写回）')
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
            # **CodeBlock 用带行号栏的编辑器**（用户 2026-10-25："跟 IDE 那种
            # 一样，左边写个数字……不然我都不知道它到底在哪儿"）。
            # 行号是**源文件里的真实行号**（`load_fields` 把这一拍的
            # `src_first/src_last` 传下去）；新建的卡片没有源行 → 退回 1/2/3…
            if name == 'CodeBlock':
                w = CodeEditor()
            elif name in ('Stage', 'Notes'):
                w = QPlainTextEdit()
            else:
                w = QLineEdit()
            if isinstance(w, QPlainTextEdit):
                w.setFixedHeight(3 * 20 + 12)
            self._widgets[name] = w
            box.addWidget(w, 1)
            adv.addLayout(box)
        root.addWidget(self._adv_host)

    def _build_footer(self, root) -> None:
        """保存 / 撤销 / 防御性提示（拆自 `_build`，2026-11-01）。"""
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
        # 标签只留「高级区」：里面有什么写在 tooltip（`_build` 里），
        # 因为**最小宽度由文本决定** —— 51 字的长标签会把整张卡片的地板
        # 顶到 380px（`_adv_btn` 单独 minHint=356），右栏一窄就出横向滚动条。
        # 判据：`tests/test_ui_width.py::test_card_min_width_budget`。
        return ('▾' if self._adv_open else '▸') + ' 高级区'

    # ---------- 语句条（2026-10-18）----------
    #
    # ⚠ 这里原本还有一道"舞台块收放"（`_toggle_stage` / `_apply_stage_visibility`
    # / `_has_stage_content`，2026-10-18 加、2026-10-29 拆）。拆的理由见
    # `_build` 里「立绘 / 背景 / 音乐（永远完整显示）」那段：那是基本信息，
    # 不该藏在「＋ 改舞台」按钮后面。舞台块现在只有"普通卡显示 / 控制流卡收起"
    # 两种状态，由 `set_flow` 决定（`load_fields` 负责把普通卡放出来）。

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

    # ---------- 控制流卡（call / jump）----------

    def set_flow(self, info, targets=(), brief=None):
        """
        显示 / 收起控制流条。

        `info` 来自 `story.flow.flow_info()`（目标、类型、返回哪一拍）；
        `targets` 是本文件已有的 label（可编辑下拉的候选，手写照样能过）；
        `brief` 是目标那一章的摘要（`{count, line, file, same_file, callers}`，
        见 `window_card._flow_target_brief`）—— 只用来在条上写
        "side · 6 拍 · 第 173 行 · 本章 2 处调用"，不参与任何写回。
        """
        info = dict(info) if info else None
        self._flow = info
        self._flow_brief = dict(brief or {}) if info else {}
        if not info:
            self._flow_bar.setVisible(False)
            # 回到普通卡片：台词区与舞台块都放出来。舞台块**平时常显**
            # （理由见 `_build` 里「立绘 / 背景 / 音乐（永远完整显示）」），
            # 这里只要把控制流卡收掉的再放出来就行。
            self._dialogue_box.setVisible(True)
            self._stage_now.setVisible(True)
            self._stage_box.setVisible(True)
            if self._loaded_fields:
                self._loaded_fields.pop('FlowType', None)
                self._loaded_fields.pop('FlowTarget', None)
                self._loaded_fields.pop('FlowArgs', None)
                self._loaded_fields.pop('FlowFrom', None)
            return
        self._loading = True
        try:
            idx = self._flow_type.findData(str(info.get('type') or 'call'))
            self._flow_type.setCurrentIndex(idx if idx >= 0 else 0)
            self._flow_target.blockSignals(True)
            try:
                self._flow_target.clear()
                for name in (targets or ()):
                    name = str(name or '').strip()
                    if name and self._flow_target.findText(name) < 0:
                        self._flow_target.addItem(name)
                self._flow_target.setCurrentText(str(info.get('target') or ''))
            finally:
                self._flow_target.blockSignals(False)
            self._flow_args.blockSignals(True)
            self._flow_from.blockSignals(True)
            try:
                self._flow_args.setText(str(info.get('args') or ''))
                self._flow_from.setText(str(info.get('from_clause') or ''))
            finally:
                self._flow_args.blockSignals(False)
                self._flow_from.blockSignals(False)
        finally:
            self._loading = False
        self._flow_bar.setVisible(True)
        # **控制流卡没有台词与演出**（2026-10-23，用户："这个控制流卡的 UI 不对"）：
        # 一张 `call side` 却铺开"说话人 + 台词 + 立绘 + 舞台"四个空块，
        # 看着像"还有一屏没填"。把它们收起来，卡片上只剩调用条 + 备注 +
        # 条件 + 高级区 —— 这正好是 call/jump 能拥有的全部内容。
        self._dialogue_box.setVisible(False)
        self._stage_box.setVisible(False)
        # 「当前舞台」那一行（图 + 摘要）也跟着收起：控制流卡**没有演出**，
        # 一张 `call` 卡上留着一幅"跳转时台上什么样"的图，纯属占掉半屏
        # （用户 2026-10-27 反复强调右栏的空间要留给能改的东西）。
        # 回到普通卡时上面那个分支会把它放出来。
        self._stage_now.setVisible(False)
        self._refresh_flow_text()
        # 载入时的控制流值也进基线，否则一打开就显示"有未保存的改动"。
        if self._loaded_fields:
            self._loaded_fields.update(self.flow_fields())
        self.refresh_dirty()

    def flow_fields(self) -> dict:
        """控制流条当前值（不是控制流拍 → 空 dict）。"""
        if not self._flow:
            return {}
        flow_type = str(self._flow_type.currentData()
                        or self._flow_type.currentText() or 'call')
        return {'FlowType': flow_type,
                'FlowTarget': self._flow_target.currentText().strip(),
                'FlowArgs': self._flow_args.text().strip(),
                'FlowFrom': self._flow_from.text().strip()}

    def flow_change(self) -> dict | None:
        """控制流目标 / 类型改了吗？返回 `{old,new,target,type,...}` 或 None。"""
        if not self._flow:
            return None
        new = self.flow_fields()
        old = {'FlowType': str(self._flow.get('type') or ''),
               'FlowTarget': str(self._flow.get('target') or ''),
               'FlowArgs': str(self._flow.get('args') or ''),
               'FlowFrom': str(self._flow.get('from_clause') or '')}
        if new == old:
            return None
        return {
            'old': dict(self._flow),
            'new': new,
            'target': new['FlowTarget'],
            'type': new['FlowType'],
            'type_changed': new['FlowType'] != old['FlowType'],
            'call_to_jump': (old['FlowType'] == 'call'
                             and new['FlowType'] == 'jump'),
        }

    def _flow_code_synced(self, fields: dict) -> dict:
        """
        高级区「代码」格还停在原来那行 `call …` 时，跟着目标一起改。

        不这么做的话：保存时 `CodeBlock` 仍是旧行 `call side`，而 raw 已经
        变成 `call other` → 会话会判"代码格被手改了"，把 raw 降级成 code。
        **只在代码格确实等于原控制流行时同步**；用户手写过别的代码就不碰。
        """
        if not self._flow or 'CodeBlock' not in fields:
            return fields
        if str(fields.get('CodeBlock') or '').strip() != self._flow.get('line'):
            return fields
        from storygraph.story.flow import is_valid_label, render_flow_line
        vals = self.flow_fields()
        if not is_valid_label(vals.get('FlowTarget')):
            # 非法目标先别动代码格：让保存路径的校验去拒绝它，
            # 不要因为"翻页顺带提交"把 raw 降级成 code。
            return fields
        fields['CodeBlock'] = render_flow_line(
            vals['FlowType'], vals['FlowTarget'],
            vals.get('FlowArgs') or '', vals.get('FlowFrom') or '')
        return fields

    def _refresh_flow_text(self):
        if not self._flow:
            return
        flow_type = str(self._flow_type.currentData()
                        or self._flow_type.currentText() or 'call')
        target = self._flow_target.currentText().strip()
        verb = '调用' if flow_type == 'call' else '跳转'
        arrow = '↳' if flow_type == 'call' else '→'
        self._flow_title.setText(
            f'{arrow} {verb} {target or "（还没填目标 label）"}')
        if flow_type == 'call':
            detail = str(self._flow.get('call_return_note') or '')
            if self._flow.get('type') != 'call':
                detail += '（原来写的是 jump，改成 call 后会返回这里）'
        else:
            detail = str(self._flow.get('jump_return_note') or '')
            if self._flow.get('type') == 'call':
                detail += '（原来写的是 call，改成 jump 后不会再回到这里）'
        names = {self._flow_target.itemText(i)
                 for i in range(self._flow_target.count())}
        if target and target not in names:
            detail += '\n目标不在本文件的 label 里；仍可手写（可能来自其他文件）'
        # **目标那一章长什么样**（2026-10-23，用户："人不能什么都去翻卡片"）：
        # 在调用条上直接写"几拍 / 哪一行 / 本章几处在调它" —— 不点进去也能判断
        # "这个 call 是不是我要的"。数据由 `_sync_flow_card` 算好递进来。
        brief = self._flow_brief or {}
        if target and brief:
            bits = []
            if brief.get('count'):
                bits.append(f"{brief['count']} 拍")
            if brief.get('line'):
                bits.append(f"第 {brief['line']} 行")
            if brief.get('file') and not brief.get('same_file'):
                bits.append(str(brief['file']))
            if bits:
                detail += f'\n目标 {target}：' + ' · '.join(bits)
            if brief.get('callers'):
                detail += f"（本章 {brief['callers']} 处调用）"
            elif brief.get('same_file'):
                detail += '（本章还没人调它）'
        # 目标是个**有名 menu** 时，把选项清单（各自去哪）整段摆出来 ——
        # 用户 2026-10-25："它没有显示 menu 的内容是什么……又跳去哪儿……"。
        if target and (brief.get('menu_text')):
            detail += '\n' + str(brief['menu_text'])
        detail += ('\n这张卡没有台词与演出 —— 台词 / 立绘 / 舞台那几块已收起；'
                   '要加一句台词请另插一张普通卡片。')
        # `jump` 不接受参数 / from（Ren'Py 语法）—— 灰掉比"填了也不生效"诚实。
        is_call = flow_type == 'call'
        self._flow_args.setEnabled(is_call)
        self._flow_from.setEnabled(is_call)
        if not is_call:
            self._flow_args.setPlaceholderText('jump 不接受参数')
            self._flow_from.setPlaceholderText('jump 不接受 from')
        else:
            self._flow_args.setPlaceholderText(
                '参数（可空），例如 (1, flag)')
            self._flow_from.setPlaceholderText(
                'from 标识（可空），例如 _call_side_1')
        self._flow_detail.setText(detail)

    def _on_flow_edited(self, *_):
        if self._loading or not self._flow:
            return
        self._refresh_flow_text()
        self.refresh_dirty()

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
                    opts = as_choices(choices.get(col.get('param'), ()) or ())
                    value = str(values.get(header, '') or '')
                    # 颜色值：先给色块，再给输入框（色块跟着输入实时变）
                    sw = self._make_swatch(value)
                    if sw is not None:
                        self._spec_swatches[header] = sw
                        box.addWidget(sw, 0)
                    if opts:
                        w = QComboBox()
                        w.setEditable(True)          # 手打新值照样能过（同 Excel）
                        # 候选可能是人话（`1人，近处`），**选出来的是真值**
                        fill_combo(w, opts, '', blank=True)
                        w.setToolTip('从下拉里选，或直接写一个项目里在用的值'
                                     '\n（候选里带人话名字时，悬停能看到引擎写法）')
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
                    if opts:
                        # 「⋯」= **可搜索**的候选列表（151 个变换靠下拉翻不动）。
                        # 与资产那三格的「⋯」同一个形态、同一个 24px 尺寸。
                        pick = QPushButton('⋯')
                        pick.setFixedSize(T.CONTROL['iconW'], T.CONTROL['iconW'])
                        pick.setStyleSheet('padding:0;')
                        pick.setToolTip(f'从候选里搜索挑选（共 {len(opts)} 个）')
                        pick.setAccessibleName(f'挑选{header}')
                        pick.clicked.connect(
                            lambda _=False, h=header, box_w=w, items=opts:
                            self._pick_spec_value(h, box_w, items))
                        box.addWidget(pick, 0)
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
                    select_value(w, value)
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

    def _pick_spec_value(self, header: str, combo, options):
        """
        参数格右边的「⋯」：**可搜索**的候选列表（用户 2026-10-31 第 5 条）。

        为什么要它：`with` 的转场候选在真实项目里有 150+ 个（变换 + 词汇表里
        登记的过场调用），靠下拉翻页找 `Fade(...)` 是不可能的；`choose_from`
        早就有"输入即筛"，只是一直只服务立绘 chip。选中后**写回下拉的真值**
        （屏幕上显示人话），与手点下拉是同一条路。
        """
        from storygraph.ui_qt.choice_picker import choose_from
        items = as_choices(options)
        if not items:
            return
        picked = choose_from(self,
                             f'选择{header} —— 共 {len(items)} 个候选'
                             '（输入即筛，可搜中文名或原名）',
                             items, combo_value(combo))
        if picked:
            select_value(combo, picked)
            self.refresh_dirty()

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
            # 显示的可能是人话（`18 嗯？`）、**存的是真值**（`bgm/嗯？.mp3`）
            select_value(w, value)
        elif isinstance(w, QPlainTextEdit):
            w.setPlainText(value)
        elif isinstance(w, QLineEdit):
            w.setText(value)

    def _get_value(self, name) -> str:
        """
        这一格**现在代表什么**（写进剧本的那个值，不是屏幕上那句）。

        ⚠ 下拉一定走 `labeled_choices.combo_value`：词汇表让下拉显示
        `1人，近处`，而写回 beat 的必须还是 `i11`。从前这里读
        `currentText()` —— 一接上是直接往剧本里写中文。
        """
        w = self._widgets.get(name)
        if w is None:
            return ''
        if isinstance(w, QComboBox):
            return combo_value(w)
        if isinstance(w, QPlainTextEdit):
            return w.toPlainText().strip()
        if isinstance(w, QLineEdit):
            return w.text().strip()
        return ''

    def set_symbols(self, symbols):
        self._symbols = symbols
        self._grid.set_symbols(symbols)
        # 项目刚打开 / 资源表刚读完：**顺手把资源映射状态加载进来**（一次，按项目根缓存）。
        # 为什么不放在 `open_project_path` 里：那是 S1 的 mixin 文件，本轮不动；
        # 而"符号表到位"这件事正好是"资源表读完了"的那一刻（同一次打开项目里，
        # `_panel.set_symbols` 紧跟 `session.load_assets()` 之后被调用）。
        win = self.window()
        if win is not None:
            refresh_mapping_status(win)

    def _on_grid_notice(self, message: str):
        """网格的"没写成"通知 → 主窗口（控制台 + 状态栏）。"""
        fn = self._on_notice
        if callable(fn):
            try:
                fn(str(message or ''))
            except Exception as e:                # noqa: BLE001 - 提示失败不拦操作
                swallow('ui_qt.main_window.grid_notice', e)

    def set_asset_choices(self, field: str, choices, display=None):
        """
        给某一格装候选（从资源表来）：`[(值, 说明), ...]`。

        保留用户当前正在编辑的值（即使它不在候选里 —— 手写的自定义值不许被
        下拉清空）。说明挂成候选项的 tooltip，挑的时候能看清是什么效果。

        **显示值 ≠ 真值**（2026-10-31）：候选也可以是 `Choice(值, 显示)` /
        `(值, 显示, 提示)` —— 那样下拉里写着 `18 嗯？`，选出来存的仍是
        `bgm/嗯？.mp3`。

        `display(值)`：**当前值不在候选里**时屏幕上该显示什么（音乐那格的值
        常常是复合的 `[音乐] night fadein 1.0`，靠它才翻得过去）。

        ⚠ 二元组在这里**仍是 `(值, 说明)`**（老契约，说明 = tooltip），所以
        先把它换成 `Choice(值, '', 说明)` 再交给 `fill_combo`
        —— `labeled_choices` 那边二元组是 `(值, 显示)`，两种解释不许混。
        """
        combo = self._widgets.get(field)
        if not isinstance(combo, QComboBox):
            return
        # 老契约的 `(值, 说明)` 在 `legacy_pairs` 里换算（全库唯一一处）
        fill_combo(combo, legacy_pairs(choices), display=display)

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
        """说话人那一格的真值（角色 tag：`DY`，不是屏幕上那句「大爷」）。"""
        return combo_value(self._speaker)

    def set_speaker(self, value):
        select_value(self._speaker, str(value or ''))

    def set_speakers(self, values, current=''):
        """
        重填说话人下拉（不触发信号，避免把用户正在改的拍写脏）。

        `values` 可以是 `['DY', …]`，也可以是 `[Choice('DY', '大爷'), …]`
        —— 后者屏幕上写人话、写回剧本的仍是 tag。
        """
        fill_combo(self._speaker, values, str(current or ''), blank=True)

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

    def load_fields(self, fields: dict, sprite_items: list,
                    source_lines=None):
        """
        把一拍的内容填进卡片。

        `_loading` 期间屏蔽脏标记计算：填控件本身会触发一串
        `textChanged`，不加这道闸，"刚载入"会被判成"已修改"。

        `source_lines=(first, last)`：这一拍在源 `.rpy` 里的行号范围 ——
        给 CodeBlock 的**行号栏**用（用户要"精确到行"，好回编辑器里定位）。
        不给（新建卡片 / 单测）→ 行号退回 1/2/3…
        """
        self._loading = True
        try:
            # 控制流条由 `_sync_spec_args` 在 load_fields 之后按这一拍重建；
            # 先收起来，避免上一拍的 `call side` 闪一下。
            self._flow = None
            self._flow_bar.setVisible(False)
            for name in self._all_names():
                self._set_value(name, fields.get(name, ''))
            self._grid.load(sprite_items)
            _code = self._widgets.get('CodeBlock')
            if isinstance(_code, CodeEditor):
                first, last = (source_lines or (None, None))
                _code.set_source_lines(first, last)
            self._loaded_fields = {n: str(fields.get(n, '') or '')
                                   for n in self._all_names()}
            # Stage 那一格的原文单独记一份（判"手改过没有"要用它）
            self._stage_loaded = str(fields.get('Stage', '') or '')
            # 舞台块（立绘 / 背景 / 音乐 / 语音）**常显**（2026-10-29）：
            # 载入一张普通卡片时确保它在上面 —— 上一拍可能是控制流卡
            # （`set_flow(info)` 会把整块收起来），不能让它一直收着。
            # 真正的控制流卡由随后的 `set_flow(info)` 再收起，见那里的说明。
            self._stage_box.setVisible(True)
            self._stage_now.setVisible(True)
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
        # 控制流条也一直可见（只在控制流拍出现），所以始终写回。
        out.update(self.flow_fields())
        self._flow_code_synced(out)
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
        out.update(self.flow_fields())
        self._flow_code_synced(out)
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
        # ⚠ 文案**必须短**（2026-10-30 第 7 条实测）：这句 9 个字（99px）和
        # 「保存这张卡片」「撤销修改」两个按钮同一行，量下来那一行最小宽
        # 117+89+99+间距 = 321px，加上卡片留白 24 = **345** —— 右栏下限 340
        # 放不下，卡片又被顶宽 5px（用户："所有图案不得超出右边框！"）。
        # 现在这句只写「● 未保存」（~60px），那行 290px；完整说明在 tooltip。
        self._dirty_label.setText('● 未保存' if modified else '')
        self._dirty_label.setToolTip(
            '这张卡片改过了、还没保存。点「💾 保存这张卡片」或 Ctrl+S。'
            if modified else '')
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

    def set_stage_size(self, rows):
        """
        卡片上的「尺寸」一格刷成这一拍的**可调对象**（P1，2026-10-31）。

        `rows` 来自 `app.stage_render.size_rows`（背景 + 各 CG/UI 图层，
        每行带预设的匹配结果）—— 语义不在前端：前端只画表、递选择。
        `[]` / None = 这一拍台上没有背景和图层：**整格收起**（不留一个
        "没有尺寸可调"的空框；「当前舞台」已经说清背景/立绘都空着）。
        """
        try:
            rows = list(rows or ())
            self._size_panel.set_rows(rows)
            self._size_box.setVisible(bool(rows))
        except Exception as e:                    # noqa: BLE001 - 尺寸格坏不拦卡片
            swallow('ui_qt.main_window.set_stage_size', e)

    def size_panel(self):
        """「尺寸」那一格控件（测试与诊断用）。"""
        return self._size_panel

    def show_stage_frame(self, idx: int, session):
        """
        卡片上那张**这一拍的舞台图**刷成第 `idx` 拍（2026-10-27）。

        与「当前舞台」文字**同一时刻**刷新：两处都是"这一拍生效的舞台"，
        分开刷就会出现"图是新的、字是旧的"（或者反过来）。
        渲染在后台线程做（`ui_qt.stage_thumb`），这里只递会话，不阻塞卡片。

        `idx < 0`（没有载入任何一章）→ 清空并写清原因，不画一张空图。
        """
        try:
            self._stage_thumb.show_beat(idx, session)
        except Exception as e:                    # noqa: BLE001 - 图刷不出来不该拦卡片
            swallow('main_window.show_stage_frame', e)

    def clear_stage_frame(self, note: str = ''):
        """没有"当前这一拍"了（空会话 / 关章节）：把图连同说明一起清掉。"""
        try:
            self._stage_thumb.clear(note)
        except Exception as e:                    # noqa: BLE001
            swallow('main_window.clear_stage_frame', e)

    def stage_thumb(self):
        """这张卡片上"这一拍的舞台图"控件（测试与诊断用）。"""
        return self._stage_thumb

    def shutdown_stage_thumb(self):
        """窗口关闭：停掉舞台图的后台渲染（正在跑的会跑完并把结果丢掉）。"""
        try:
            self._stage_thumb.shutdown()
        except Exception as e:                    # noqa: BLE001
            swallow('main_window.shutdown_stage_thumb', e)

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
