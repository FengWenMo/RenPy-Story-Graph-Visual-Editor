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
from storygraph.core.cell_limits import CELL_TEXT_LIMIT
from storygraph.core.safeguard import swallow
from storygraph.story.flow import is_valid_label, render_flow_line
from storygraph.story.spec_args import is_dynamic_column
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt.code_edit import CodeEditor
from storygraph.ui_qt.labeled_choices import (as_choices, combo_value,
                                              fill_combo, legacy_pairs,
                                              select_value)
from storygraph.ext import card_fields as CF
from storygraph.ext.schema import FieldSpec
from storygraph.ui_qt import card_rows, field_widgets
from storygraph.ui_qt.mapping_status import refresh_mapping_status
from storygraph.ui_qt.size_panel import SizePanel
from storygraph.ui_qt.sprite_grid import SpriteGridWidget
from storygraph.ui_qt.stage_thumb import BeatStageThumb
from storygraph.ui_qt.text_html import renpy_text_to_html
from storygraph.ui_qt.wheel_guard import WheelSafeComboBox
from storygraph.ui_qt.window_layout import CARD_STAGE_THUMB
from storygraph.ui_qt.wrap_label import NarrowWrapLabel


#: `with` 后面**真正**该出现的那些 —— Ren'Py 内置 transition。
#:
#: 为什么写死这一份不算"猜项目"（2026-11-03）：它们是**引擎的标准词汇**
#: （官方文档的 `with` 示例就是这几个），任何项目都能用；而项目自己的
#: `transform` 是另一回事 —— 真机 `sr_general` 有 167 个 transform，
#: 里面混着 `i11`（位置）、`ui_hover_nudge`（界面动效）、`bounce_duration`
#: （动画）—— 拿 `with` 去套它们基本都不是作者的意图。所以：内置排前面，
#: 项目 transform 全量排在后面（用户自己认出哪个是转场）。
BUILTIN_TRANSITIONS = (
    'dissolve', 'fade', 'vpunch', 'hpunch', 'flash',
    'wipeleft', 'wiperight', 'wipeup', 'wipedown',
    'irisin', 'irisout', 'pixellate', 'squares', 'move',
)


#: 纯 `with <名字>` 的形态（`with dissolve` / `with my.trans`）。
_PURE_WITH_RE = re.compile(r'^with\s+([\w.]+)$')


def split_say_tail(tail: str) -> str:
    """
    存的 `say_tail` → **控件里显示**的那串（纯转场剥掉 `with `，其余原样）。

    `with dissolve` → `dissolve`；`nointeract` → 原样；
    `(what_color="#f00") with dissolve` → **原样**（它不是"一个转场名"，
    剥了就没法编辑回原样了）。
    """
    text = str(tail or '').strip()
    m = _PURE_WITH_RE.match(text)
    return m.group(1) if m else text


def join_say_tail(text: str) -> str:
    """
    控件里的那串 → 写回 `say_tail` 的**整段**（纯转场补回 `with `）。

    这是"用户不用打整句"的关键：下拉选了 `dissolve`，写出去就是
    `with dissolve`；手打 `(what_color="#f00") with dissolve` 或 `nointeract`
    则原样保留（**不猜**它是不是转场）。
    """
    text = str(text or '').strip()
    if not text:
        return ''
    if text == 'nointeract' or text.startswith('with ') or text.startswith('('):
        return text
    # 单个标识符（`dissolve` / `my.trans`）→ 当成转场名补 `with `
    if re.fullmatch(r'[\w.]+', text):
        return f'with {text}'
    return text                              # 别的写法一律原样（不猜）


def say_tail_choices(symbols) -> list:
    """
    台词后缀的候选 `[(显示, 真值), …]`；第一项是「（无）」。

    顺序：**内置 transition（引擎标准）→ 项目自己的 transform（字母序）→
    `nointeract`**。

    为什么两段（2026-11-03 用户："还是来个候选变换表吧"）：只列项目 transform
    会有 169 条噪声（真机 167 个里大部分是位置/界面动效，`with` 套上去没有
    意义）；只列内置又会漏掉项目自造的转场（`ImageEmergeOnRight` 这种）。
    两段并排，常用的在上面，自定义的在下面。

    读不到资源表（没打开项目 / 表坏了）→ 只剩内置 + 「（无）」+ `nointeract`，
    **手打仍然可用**（这一格始终是可编辑下拉）。
    """
    out: list = [('（无）', '')]
    seen = set()
    for name in BUILTIN_TRANSITIONS:
        # 显示/真值都是**转场名**（`dissolve`）—— 写回时由 `join_say_tail`
        # 补成 `with dissolve`。用户不用自己打 `with`。
        out.append((name, name))
        seen.add(name.lower())
    rows = {}
    try:
        reg = getattr(symbols, 'registry', None)
        rows = getattr(reg, 'transforms', None) or {}
    except Exception as e:                     # noqa: BLE001 - 候选坏了不该挡卡片
        swallow('ui_qt.card_editor.say_tail_choices', e)
        rows = {}
    names = sorted({str(k).strip() for k in (rows or {}) if str(k).strip()})
    out += [(n, n) for n in names if n.lower() not in seen][:150]
    out.append(('nointeract', 'nointeract'))
    return out


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
        cards_modified（立绘网格改脏了没有 —— `is_modified` 只管表单字段）
    """

    #: 「保存这张卡片」/「撤销修改」两个按钮被按下时发出
    save_requested = Signal()
    undo_requested = Signal()
    #: 「✂ 句中换表情」：参数是**台词框里的光标位置**（字符下标），
    #: 由会话在那一处把这句话断成"前半{nw} + 接续卡"（2026-11-14）。
    split_requested = Signal(int)

    #: 能"句中断开"的卡（与 `EditorSession.SPLIT_KINDS` 同源）。
    #: 这里只决定**按钮出不出现**；认不认由会话判 —— 前端不另立一套规矩。
    SPLIT_KINDS = ('say', 'narration', 'extend')

    #: 按钮上那行字（平时）。按下之后它变成「✂ 就断在这儿」—— 第二次才是真断，
    #: 第一步是"让你在台词里点一个字"（见 `_build_sentence_split` 的说明）。
    SPLIT_BTN_TEXT = '✂ 句中换表情…'
    SPLIT_BTN_CONFIRM = '✂ 就断在这儿'

    #: 这两格在**这一拍没写**时可以从"当前舞台"沿用过来显示（改了才写回）。
    #: 语音刻意不在里面：它是逐句的，沿用上一句的语音没有意义。
    INHERITED_FIELDS = (('Scene', '背景'), ('BGM', '音乐'))

    def __init__(self, symbols=None, loader=None, parent=None,
                 on_pick_image=None, on_pick_asset=None,
                 on_pick_image_result=None, on_open_mapping=None,
                 on_notice=None):
        super().__init__(parent)
        self._symbols = symbols
        self._widgets: dict = {}
        #: 高级区**默认展开**（2026-11-07 按 Unity：`Foldout` 默认是开的）。
        #:
        #: 折叠只是"这一屏我看完了"的临时动作，不是"藏起来不用管"——
        #: 收起之后里面的字段照样写回（见 `collect_fields`）。
        self._adv_open = True
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
        #: 载入时**能写回这一拍**的立绘卡（判"网格被改脏了没有"）。
        #: 卡片上的 chip 改动直接写在 `item` 字典上、不进表单字段，
        #: 所以要和 `_loaded_fields` 分开记（见 `cards_modified`）。
        #: None = 还没载入过任何一拍。
        self._loaded_cards: list | None = None
        #: `{字段名: 沿用来的值}`：这一拍没写、从"当前舞台"填进来的显示值。
        #: 没改过就**不写回**（见 `_drop_inherited`）。
        self._inherited_fields: dict = {}
        #: 背景/音乐/语音三格的标题 QLabel（沿用时要改成「背景（沿用上一拍）」）
        self._asset_caps: dict = {}
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

        # —— 说话人 ——（**一行一条**：标签右对齐到全局那条竖线，见 `card_rows`）
        #
        # ⚠ 这格必须是 `WheelSafeComboBox`（2026-10-30 用户："滚动会导致选择切换"）。
        #   它是可编辑组合框 —— 实测这种控件**连焦点都不用**就会被滚轮换值，
        #   而且把滚动吃掉，于是滚页面时"人换了、页面没动"。
        self._speaker = WheelSafeComboBox()
        self._speaker.setEditable(True)
        self._speaker.setMinimumWidth(160)
        self._speaker.setMaximumWidth(240)
        self._widgets['Speaker'] = self._speaker
        # `stretch=0`：说话人是短值，不该拉满整行（Unity 的 ObjectField 同理）
        dlg_lay.addWidget(card_rows.row('说话人', self._speaker, stretch=0))

        # —— 台词（这张卡的主角，给足高度）——
        self._text = QPlainTextEdit()
        self._text.setPlaceholderText('这一拍要说的台词…')
        self._text.setFixedHeight(3 * 24 + 16)
        self._widgets['Text'] = self._text
        dlg_lay.addWidget(card_rows.row('台词', self._text))

        # —— 字数：超长台词在游戏里会溢出对话框 ——
        # tkinter 版有这一项，搬 Qt 时漏了。它属于"作者看不见的坑"：
        # 编辑器里换行好看，进游戏可能被裁掉，所以必须提示。
        self._char_label = QLabel('')
        self._char_label.setProperty('role', 'caption')
        self._char_label.setAlignment(Qt.AlignRight)
        dlg_lay.addWidget(self._char_label)

        # —— 句中换表情（2026-11-14）——
        self._build_sentence_split(dlg_lay)

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

        # —— 台词转场（`with dissolve`）：紧贴台词，不藏进高级区 ——
        #
        # 2026-11-03 用户："说话人是 say、摆在上面的；然而我想要 with 的 say
        # 却必须要在下面的高级模式点，并且还要重新打整句，这不合理吧？要不你
        # 给个 say 后面紧跟一个 with？"
        #
        # 说得对：`with` 是**这一句怎么说**的一部分，和台词同层。所以：
        #   · 控件就在台词下面（不算高级区）；
        #   · 候选是**转场名**（`dissolve`），显示不带 `with` ——
        #     写回时由 `join_say_tail` 补成 `with dissolve`；
        #   · 仍是可编辑下拉：`(what_color="#f00") with dissolve` 这种
        #     组合子句照原样存（`split/join` 都认"不是纯 with 形式"）。
        self._say_tail = WheelSafeComboBox()
        self._say_tail.setEditable(True)
        self._say_tail.setMinimumWidth(180)
        for label, value in say_tail_choices(self._symbols):
            self._say_tail.addItem(label, value)
        self._say_tail.setCurrentIndex(0)
        self._say_tail.setToolTip(
            '这一句的转场（say 的后缀）：选一个就行，会写成 `"台词" with dissolve`。\n'
            '也可以手打其它子句：`(what_color="#f00") with dissolve` / `nointeract`。')
        self._widgets['SayTail'] = self._say_tail
        dlg_lay.addWidget(card_rows.row('转场', self._say_tail, stretch=0))

        # —— 台词后缀的**只读提示**（样式类子句，`(what_color=…)` / `id`）——
        # 转场有下拉了，但 kwargs / `id` 这类没有候选，写了就照样给一行提示，
        # 免得"这句台词带样式"只存在于下拉框的输入框里。
        self._say_tail_label = QLabel('')
        self._say_tail_label.setProperty('role', 'caption')
        self._say_tail_label.setWordWrap(True)
        self._say_tail_label.setVisible(False)
        dlg_lay.addWidget(self._say_tail_label)

    def _build_sentence_split(self, lay) -> None:
        """
        「✂ 句中换表情」按钮 + 说明行 + **选择断开点**那一条（2026-11-14）。

        为什么是**动作**而不是教语法（用户原话）：

        > 在同一句中需要反复变换表情的情况，我们的编辑器可否再做得更直接
        > 一点……我理解 Renpy 是写 {nw} 后接 extend，新建语句然后再分表情
        > 的……但要是剧本不明白怎么办？

        为什么是**两步**（2026-11-14 当晚用户实测报）：

        > 我点击了这个句中换表情一点反应都没有！也没有让我精确到哪个字去
        > 换表情啊……？？？有bug

        第一版是"一步到位"：按钮直接用台词框里的光标位置断开。毛病不在断开，
        在**断开点看不见** —— 卡片刚载入时光标在句首（0），界面和我脑子里那个
        "中间"长得一模一样；点下去要么被拒（只在状态栏说一句，卡片里纹丝不动，
        看着就是死按钮），要么断在他没想过的位置。

        所以改成两步，中间那步就是"让你指到哪个字"：

        1. 按一下 → 进**选择模式**：按钮变成「✂ 就断在这儿」，旁边出现「取消」，
           台词框描一圈主色、拿走焦点（光标看得见），下面实时预览断完之后的两半，
           并且**点名**是断在哪个字前面；
        2. 在台词里点那个字 → 预览立刻更新；再按一下 → 真断。

        只有按了这个按钮才会写 `{nw}`；手写 `{nw}` 的人照旧，两不相干。
        """
        #: 选择模式的状态。`_split_blocked` = 这一拍**为什么不能拆**（会话给的
        #: 理由，空串 = 能）；`_split_kind` / `_split_text` = 载入时这一拍是什么卡、
        #: 什么台词（按钮出不出现看它们，不看控件 —— 见 `set_sentence_context`）。
        self._picking = False
        self._split_kind = ''
        self._split_text = ''
        self._split_blocked = ''
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        #: 「取消」只在选择模式里出现 —— 那时候按钮已经改行"确认"了，
        #: 没有退路的话点错一次就只能断完再撤销（多一步、还容易忘）。
        self._split_cancel = QPushButton('取消')
        self._split_cancel.setFlat(True)
        self._split_cancel.setVisible(False)
        self._split_cancel.clicked.connect(self.cancel_split)
        row.addWidget(self._split_cancel)
        self._split_btn = QPushButton(self.SPLIT_BTN_TEXT)
        self._split_btn.setToolTip(
            '一句话中间换立绘表情：\n'
            '  1) 按一下 → 在台词里点要断开的那个字（断在它前面）；\n'
            '  2) 再按一下 → 前半句留在这一张（末尾加 {nw}），后半句进'
            '「接续」卡（同一个对话框）。\n'
            '在接续卡上改立绘表情，就得到 Ren\'Py 的\n'
            '  l "前半句{nw}"\n'
            '  show l 新表情\n'
            '  extend "后半句"')
        # 只有台词卡才出现（`set_sentence_context` 按这一拍判；不认识的卡上
        # 留着这个按钮只会让人点出一个错误提示）。
        self._split_btn.setVisible(False)
        self._split_btn.clicked.connect(self._begin_split)
        row.addWidget(self._split_btn)
        lay.addLayout(row)
        #: 选择模式里的**实时预览**：断在哪个字前面、两半各是什么。
        #: 行数固定 3 行左右，平时 `setVisible(False)` 不占版面。
        self._split_preview = QLabel('')
        self._split_preview.setProperty('role', 'caption')
        self._split_preview.setWordWrap(True)
        self._split_preview.setVisible(False)
        lay.addWidget(self._split_preview)
        #: 「这句没说完 / 这张是接续 / 为什么不能拆」那一行说明
        #: （纯文字，QLabel 不认 Markdown）。
        self._sentence_label = QLabel('')
        self._sentence_label.setProperty('role', 'caption')
        self._sentence_label.setWordWrap(True)
        self._sentence_label.setVisible(False)
        lay.addWidget(self._sentence_label)
        # 光标一动、台词一改，选择模式里的预览就跟着变
        # （平时它先 return，等于没连）。
        self._text.cursorPositionChanged.connect(self._refresh_split_preview)
        self._text.textChanged.connect(self._refresh_split_preview)
        # 台词框里有没有字，决定按钮出不出现 —— 边打边看得到那颗按钮，
        # 不用"存一下再翻回来"（用户 2026-11-14 的下一步就会撞上这个）。
        self._text.textChanged.connect(self._refresh_split_availability)

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
        now_lay.addWidget(card_rows.section('当前舞台（这一拍生效）'))
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
        size_lay.addWidget(card_rows.section('尺寸'))
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
        # 标题 + 一句提示：走 `card_rows.section(hint=…)`，不再自己拼一行
        stage_lay.addWidget(card_rows.section(
            '立绘', hint='点 chip 换表情/位置 · 双击卡换图'))
        self._grid = SpriteGridWidget(
            symbols=self._symbols, loader=loader,
            on_pick_image=self._on_pick_image,
            on_pick_image_result=self._on_pick_image_result,
            on_open_mapping=self._on_open_mapping)
        # 网格里"没写成"的事（选择器拒绝 / 给不出 spec）一路报给主窗口
        self._grid.notice.connect(self._on_grid_notice)
        stage_lay.addWidget(self._grid)

        # —— 背景 / 音乐 / 语音（**两行**：背景一行，音乐 + 语音一行）——
        #
        # 每格 = 下拉候选（从**资源表**来）+ 「⋯」按钮（打开资产选择器）。
        # 用户 2026-10-03："添加立绘，添加语音，添加音乐，全部都没有反应！"
        #   以前这三格是**空的可编辑下拉** —— 没有任何候选可挑。
        # 用户 2026-10-04："把三格改成两行布局（背景一行、音乐+语音一行）"。
        #
        # ⚠ 2026-11-07：这一段的"下拉 + ⋯"**不再自己拼布局**，改走
        #   `card_rows.picker_row`（唯一实现，语句参数格也用它）。滚轮守卫、
        #   24px 按钮、`padding:0` 那些坑都写在那个函数里了 —— 从前它们散在
        #   这里，谁想复用得再抄一遍；用户原话："相似或者相同的功能，应当
        #   放在一处！不许东一块西一块的"。
        self._asset_btns = {}

        def _asset_row(specs, row_name):
            """
            一行资产格（背景一行；音乐 + 语音一行）。

            外面套一层 QWidget 并给 objectName：布局本身没法被断言，
            有了具名容器，测试才能钉住"哪几格在同一行"。
            并排时用**窄标签**（`NARROW_LABEL_WIDTH`）：两个 88px 会吃掉半屏。
            """
            holder = QWidget()
            holder.setObjectName(row_name)
            lay = QHBoxLayout(holder)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(T.SPACE['sm'])
            for name, label in specs:
                # ⚠ 滚轮不许改这三格的值（用户 2026-10-29："不要让滚轮上下
                # 滑动调整背景，音乐和语音这三个东西……太容易误触了！！！"）。
                combo = WheelSafeComboBox()
                combo.setEditable(True)
                self._widgets[name] = combo
                cell = card_rows.compact_picker(
                    label, combo, lambda f=name: self._choose_asset(f),
                    hint=f'从资源表里挑{label}（分类 / 搜索 / 预览）')
                self._asset_caps[name] = cell._label
                pick = cell.findChild(QPushButton)
                if pick is not None:
                    pick.setAccessibleName(f'选择{label}')
                    self._asset_btns[name] = pick
                lay.addWidget(cell, 1)
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
        # —— 备注 / 条件显示 ——（一行一条，标签右对齐到全局那条竖线）
        #
        # 2026-11-07 第三轮：这两格的标签、灰字提示、tooltip 都在
        # `ext.card_fields` 那张表里（从前是这里写死的字符串）—— 于是"字段表 →
        # 界面"在最后一米没断：扩展窗口里凭空多出来的一格也能有提示。
        #
        # 2026-11-08 第四轮：它们**还要能重建**。从前这两行直接挂在 root 上，
        # 而 `set_card_field_specs` 只重建高级区 —— 于是字段表里改
        # `Comment` / `condition`（那两格就在"能改的键"列表里）存完之后，
        # 卡片**一个像素都不变**，也没有任何提示。现在给它们一个容器，
        # 与高级区同一条重建链（判据：`test_hell_extension_card_display.py`）。
        self._meta_host = QWidget()
        self._meta_lay = QVBoxLayout(self._meta_host)
        self._meta_lay.setContentsMargins(0, 0, 0, 0)
        self._meta_lay.setSpacing(T.SPACE['xs'])
        root.addWidget(self._meta_host)
        self._build_meta_rows()

        # —— 语句参数组已经挪到卡片顶部（见上面的 `_spec_host`）——

        # —— 高级区（**默认展开**，可折叠）——
        # Unity 的 `Foldout` 默认是开的：折叠是"这屏看完了"的临时动作，
        # 不是"藏起来不用管"。收起只影响显示，字段照样写回。
        self._adv_btn = QPushButton(self._adv_label())
        self._adv_btn.setProperty('variant', 'chip')
        self._adv_btn.setFlat(True)
        # 标签只写"高级区"（2026-10-30：原来那句 51 字的长标签一个控件就把
        # 卡片最小宽顶到 356px、整卡地板 380px —— 右栏一窄就出横向滚动条）。
        # "里面有什么"放进 tooltip，信息一点没少 —— 而且**由字段表生成**：
        # 从前这里写死过 `… / Notes / 台词后缀 / …`，那两格早就不在高级区了。
        self._adv_btn.setToolTip(self._adv_tooltip())
        self._adv_btn.clicked.connect(self._toggle_adv)
        root.addWidget(self._adv_btn, 0, Qt.AlignLeft)

        self._adv_host = QWidget()
        self._adv_lay = QVBoxLayout(self._adv_host)
        self._adv_lay.setContentsMargins(0, 0, 0, 0)
        self._adv_lay.setSpacing(T.SPACE['xs'])
        self._adv_host.setVisible(True)
        self._build_advanced_rows()
        root.addWidget(self._adv_host)

    #: 主区那两格（由字段表画；顺序 = 卡片上的顺序）。
    META_FIELDS = ('Comment', 'condition')

    def _build_meta_rows(self) -> None:
        """
        按字段表重建「备注 / 条件」两格（与 `_build_advanced_rows` 同一套画法）。

        为什么要单独一个函数：这两格是**主区**的，不在 `ADVANCED_FIELDS` 里，
        所以高级区那条重建链到不了它们。和高级区一样，先拆旧控件（`setParent`
        才真的从画面拿掉）再按新 schema 画，值由调用方搬（`set_card_field_specs`）。
        """
        lay = self._meta_lay
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        specs = self._card_field_specs()
        for name in self.META_FIELDS:
            spec = specs.get(name) or FieldSpec(key=name)
            w = field_widgets.make_editor(spec)
            self._widgets[name] = w
            self._hook_dirty(w)
            if name == 'Comment':
                self._comment = w
            else:
                self._condition = w
            lay.addWidget(card_rows.row(spec.shown, w,
                                        label_tip=spec.help or ''))

    def _adv_tooltip(self) -> str:
        """「高级区」按钮的 tooltip（里面有哪些格 —— **由字段表生成**）。"""
        return (' / '.join(self._adv_names())
                + '\n（收起只是不显示 —— 里面的字段照样会写回）')

    def _adv_names(self) -> list:
        """高级区那一串名字（tooltip 用；从 schema 来，不再写死）。"""
        specs = self._card_field_specs()
        return [specs[n].shown if n in specs else n for n in ADVANCED_FIELDS]

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
        # 这一拍是哪条语句（`{}` = 台词拍）。**留着**是为了 `set_flow(None)`
        # 能把"该收哪几块"按**这一拍**重放一遍（见 `set_flow` 的说明）。
        self._statement = info
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
            # 回到普通卡片：把**这一拍自己的**组放回来 —— 不是无脑全放。
            #
            # ⚠ 2026-11-08 第四轮：从前这里是 `setVisible(True)` 两连发。
            #   而每张卡都会走到这里（`_sync_flow_card` 对普通拍传的是
            #   `None`），于是 `_sync_spec_args` 刚按语句收起的台词 / 舞台块
            #   被**紧接着**放回来 —— 语句拍照样铺着四个空块。
            #   面板单测全绿（`test_card_field_groups.py` 不经过 `set_flow`），
            #   真窗口里却是另一回事；判据：
            #   `tests/test_hell_extension_card_display.py` 的 B 组。
            self._stage_now.setVisible(True)
            statement = getattr(self, '_statement', None)
            if statement is None:
                # 还没载入过任何一拍（单测里直接建面板）：保持老行为
                self._dialogue_box.setVisible(True)
                self._stage_box.setVisible(True)
            else:
                self._sync_field_groups(statement)
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

    #: 能让"舞台块"有意义的字段：任意一个有值，就说明这一拍真的动了舞台。
    STAGE_FIELDS = ('Scene', 'Show', 'Hide', 'Stage')

    #: 声明里的格子类型 → 参数格用哪种控件（**唯一一份**）。
    #:
    #: ⚠ 2026-11-07 第三轮：`SPEC_EDITOR_KIND` 已经删了。它曾是"kind → 控件"
    #: 的**第二份**实现（第一份在 `dynamic_form._make_editor`），两份行为还不
    #: 一样 —— 声明里的 `transition` 在这边是下拉、在扩展窗口里是输入框，于是
    #: 用户说的"定义出来的模块和卡片上那一格对不上"就出在这里。现在两处都调
    #: `ui_qt.field_widgets.make_editor`，控件由 `ext.schema.KIND_TO_WIDGET`
    #: 推出来（判据：`tests/test_ext_card_fields.py` 直接比控件类）。

    def _card_field_specs(self) -> dict:
        """
        卡片上"由字段表画出来"的那几格：`{字段名: FieldSpec}`（**唯一一份**）。

        初始化时取一次（`ext.card_fields.card_field_specs`）：内置 schema 加上
        项目里 `kind: "field"` 的扩展点。主窗口在打开项目时注入（
        `set_card_field_specs`），这里只是保管 —— 卡片不认识项目根。
        """
        specs = getattr(self, '_field_specs', None)
        if not specs:
            specs = CF.builtin_card_fields()
            self._field_specs = specs
        return specs

    def set_card_field_specs(self, specs) -> None:
        """
        换一套卡片字段 schema（内置 + 项目扩展点），**按新 schema 重建那几行**。

        重建的是**两处**：主区那两格（`Comment` / `condition`，见
        `_build_meta_rows`）与高级区（`_build_advanced_rows`）。字段表把它们
        都算"能改的键"，少一处就是"保存成功了、卡片没变"（2026-11-08 实测）。

        为什么要重建而不是改现有控件的标签：类型可能从"输入框"变成"代码框"
        （`field` 扩展点最常用的用法），换控件就得换人；而且旧值不能丢 ——
        重建前后按名字把值搬过去（`_get_value` → `_set_value`）。

        ⚠ **形状没变就一行都不动**（`CF.signature`）：这一条不是优化，是
        交互状态 —— 重建会把正在编辑的那一格换掉（光标 / 选区 / 输入法丢），
        窗口的初始焦点也会跑到别的格上（判据当场抓到过）。
        """
        merged = dict(CF.builtin_card_fields())
        merged.update(dict(specs or {}))
        if CF.signature(merged) == CF.signature(self._card_field_specs()):
            self._field_specs = merged
            return                            # 形状没变：不重建（保住光标）
        self._field_specs = merged
        if not hasattr(self, '_adv_host') or not hasattr(self, '_meta_lay'):
            return                            # 还没建界面：`_build` 会用它
        old = {name: self._get_value(name) for name in CF.overridable_keys()
               if name in self._widgets}
        # 主区两格（Comment / condition）与高级区**一起**重建：字段表里它们
        # 都是"能改的键"，少重建一边就是"改完保存什么都不变"（2026-11-08）。
        self._build_meta_rows()
        self._build_advanced_rows()
        for name, value in old.items():
            if value:
                self._set_value(name, value)
        self._adv_btn.setToolTip(self._adv_tooltip())
        self.refresh_dirty()

    def _build_advanced_rows(self) -> None:
        """
        按 schema 重建「高级区」那一块（**字段表 → 界面**的最后一步）。

        从前这里是三条 if/elif：标签 `'台词后缀' if name == 'SayTail'`、控件
        `if name == 'CodeBlock': CodeEditor()` …… 而且 `SayTail` / `Notes`
        两个分支**已经是死代码**（`SayTail` 搬去主区、`Notes` 取消了），没人
        发现。现在"这一格长什么样"只在 `ext.card_fields` 说一次。

        `CodeBlock` 那一格带行号（用户 2026-10-25："跟 IDE 那种一样，左边写个
        数字……不然我都不知道它到底在哪儿"）—— 那是 `kind: "code"` 的画法，
        现在由字段表说，不再在这里 `if name == 'CodeBlock'`。
        """
        lay = self._adv_lay
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # `setParent(None)` 才是立刻从画面拿掉（只 `deleteLater` 会留下
                # 一排孤儿控件，与 `dynamic_form.rebuild` 同一个坑）
                w.setParent(None)
                w.deleteLater()
        specs = self._card_field_specs()
        for name in ADVANCED_FIELDS:
            spec = specs.get(name)
            if spec is None:
                # schema 没声明这一格 → **仍然画出来**（普通输入框）：
                # 少一格等于这一拍的这个字段没法改，比"外观不对"糟得多。
                spec = FieldSpec(key=name)
            w = field_widgets.make_editor(spec)
            self._widgets[name] = w
            self._hook_dirty(w)
            lay.addWidget(card_rows.row(spec.shown, w,
                                        label_tip=spec.help or ''))

    def _sync_field_groups(self, statement=None) -> None:
        """
        右栏的**组**跟着"这一拍实际是什么"走（2026-11-07，用户原话）：

        > 我觉得不应该在代码的那个高级上面配置吧……？自定义模块归自定义模块，
        > 别挪到高级那里去……而且工具本来就认识啊！这是标准 renpy 写法！
        > 这么整理太乱了！！！所以我才三番五次说卡片编辑器右边的 UI 和
        > 自定义模块定义出的模块一定要对应！UI 要重构……

        用户说的"乱"具体是这样：一条**自定义语句**（`泳装切换 小明 泳装 开心`）
        的卡片上，仍然铺着"说话人""台词""背景""立绘"四个空块 —— 那些字段跟
        这条语句**毫无关系**，却占掉了右栏大半，而真正的三格参数挤在顶上。

        规则只两条，都按**有没有值 / 是不是语句拍**判，不按 `kind` 猜：

          · 台词区：**没有台词就收起**（语句拍、`show` 拍都没台词）——
            但"什么都没有"的空卡片例外：刚 `insert_card()` 插出来那一拍，
            台词框就是人要打字的地方（2026-11-08 补，见下面的 `has_other`）；
          · 舞台块（背景/立绘/音乐/语音的输入）：**语句拍且没有舞台动作**时收起
            —— 一条 `泳装切换 …` 不会顺带改背景，留着那几格只是噪音。
            普通台词拍**不变**（仍常显），因为"说着话顺手 show 一个"是真写法。

        ⚠ **只是不显示，不是不收集**：`collect_fields()` 按 `PRIMARY_FIELDS`
        遍历，不看可见性 —— 收起不会丢数据。这一点和高级区**不同**（那边
        "展开才收"，`SayTail` 曾因此静默丢过），判据里钉了这条。

        ⚠ 控制流卡（`set_flow`）自己会把三块全收起，且它在这之后调用 ——
        最后说话的是它，这里不跟它抢。
        """
        is_statement = bool(str((statement or {}).get('head') or '').strip())
        # ⚠ 判据读**载入时的字段快照**（`_loaded_fields`），不读控件：
        #   `Show` 那一格背后是立绘网格（chip），`_get_value('Show')` 在
        #   没有 sprite items 时恒为空 —— 用它判就会把"这一拍真的在改立绘"
        #   当成"没动舞台"（判据当场抓到）。
        data = dict(getattr(self, '_loaded_raw', {}) or {})
        has_text = bool(str(data.get('Text') or '').strip())
        has_stage = any(str(data.get(name) or '').strip()
                        for name in self.STAGE_FIELDS)
        # 台词区：没有台词通常收起，但**空卡片**要留着 —— 那是人要打字的地方。
        #
        # ⚠ 2026-11-08（收起第一次真正生效之后量到）：`insert_card()` 插出来的
        #   拍什么内容都没有，按"没有台词就收起"办，那张卡片的台词框直接不见、
        #   一个字也写不进去。所以判据不是"有没有台词"，而是
        #   "**这一拍是不是已经有别的样子**"（语句 / 舞台动作 / 音频 / 代码）。
        has_other = has_stage or any(
            str(data.get(name) or '').strip()
            for name in ('BGM', 'SFX', 'Voice', 'CodeBlock'))
        self._dialogue_box.setVisible(
            has_text or (not is_statement and not has_other))
        self._stage_box.setVisible(has_stage or not is_statement)

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
        self._sync_field_groups(statement)
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

        ⚠ 2026-11-07：**不再自己拼布局** —— 走 `card_rows.row()`，与台词区 /
        舞台块 / 高级区用同一个构造口。从前这里是 `QHBoxLayout` + 标签
        `setMinimumWidth(72)`，而全局标签宽是 88 —— 就差 16px，于是参数格那几行
        的竖线**和上面所有行都错开一点**（用户说的"折磨"里最难查的一类）。
        """
        header = str(col.get('header') or '')
        if not header:
            return
        opts = as_choices(choices.get(col.get('param'), ()) or ())
        value = str(values.get(header, '') or '')
        # 颜色值：先给色块，再给输入框（色块跟着输入实时变）
        sw = self._make_swatch(value)
        if sw is not None:
            self._spec_swatches[header] = sw
        kind = str(col.get('kind') or '')
        editor = self._spec_cell_editor(header, opts, sw, kind,
                                        placeholder=str(col.get('placeholder')
                                                        or ''))
        tail = []
        if opts and kind != 'keyword':
            tail.append(self._spec_cell_pick(header, editor, opts))
        if col.get('multi'):
            tail.append(self._spec_multi_hint())
        host = card_rows.row(
            str(col.get('label') or col.get('param') or header), editor,
            lead=((sw,) if sw is not None else ()), tail=tuple(tail),
            label_tip=self._spec_label_tip(col, header))
        self._spec_form.addWidget(host)

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
        lab.setToolTip(self._spec_label_tip(col, header))
        return lab

    @staticmethod
    def _spec_label_tip(col, header: str) -> str:
        """
        这一格标签的 tooltip：**位置参数还是具名参数**。

        ⚠ 两者在脚本里的写法不同（`se` vs `gain=-6`），而卡片上长得一模一样 ——
        人改错了只会得到一条"参数过多"的报错。措辞只有这一处。
        """
        pos = col.get('positional')
        if pos is None:
            return (f'{header}（具名参数，脚本里写成 '
                    f'{col.get("param")}=值）')
        return f'{header}（位置参数：按顺序写，第 {int(pos) + 1} 位）'

    def _spec_cell_editor(self, header: str, opts, sw, kind: str = '',
                          placeholder: str = ''):
        """
        输入控件：走 `ui_qt.field_widgets.make_editor`（**唯一一处画法**）。

        2026-11-07 第三轮：这里原来是第二份"kind → 控件"（`SPEC_EDITOR_KIND`），
        而第一份在 `dynamic_form._make_editor`。两份行为还不一样，于是
        "定义出来的模块"和"卡片上那一格"长得不一样 —— 用户说的"一定要对应"。

        `placeholder`（2026-11-07 第七轮）：**声明里写的那句提示**。
        从前这一格只有按类型的默认灰字（`留空 = 不带这个参数`），声明里
        写的 `placeholder` 到不了这里 —— 字段表上的提示在"最后一米"断掉。
        """
        raw_kind = str(kind or '').strip()
        if not raw_kind and opts:
            # 老声明**没写类型，但给了候选**（项目词汇表 / `choices_from`）：
            # 从前这里靠 `combo if opts else text` 兜底成下拉，现在明说成
            # `choice` —— 判据 `test_undeclared_kind_with_choices_is_still_a_combo`。
            raw_kind = 'choice'
        spec = FieldSpec(key=header, kind=raw_kind or 'variable',
                         choices=tuple(str(c.value) for c in opts),
                         placeholder=str(placeholder or ''))
        # ⚠ 固定字那格的 tooltip 是**卡片特有**的：它得说清"去哪改"
        #   （扩展窗口里那句话不成立 —— 那里本来就是改声明的地方）。
        tip = ''
        if spec.fixed:
            tip = (f'这一格是**固定字**（剧本里原样出现，不能改）：{header}\n'
                   '要改请去「🧩 自定义语句」改那条声明。')
        w = field_widgets.make_editor(spec, choices=opts, parser_only=True,
                                      tip=tip)
        if sw is not None:
            # 色块跟着输入实时变（连接方式按控件类，不用在这里再分派一遍）
            field_widgets.connect_change(
                w, lambda *a, s=sw, ed=w: self._paint_swatch(
                    s, field_widgets.text_of(ed)))
        self._spec_widgets[header] = w
        self._widgets[header] = w            # `_set_value` / `_get_value` 复用
        return w

    def _spec_cell_pick(self, header: str, editor, opts) -> QPushButton:
        """
        「⋯」= **可搜索**的候选列表（151 个变换靠下拉翻不动）。

        与资产那三格的「⋯」同一个形态、同一个 24px 尺寸。
        """
        # ⚠ 按钮本体走 `card_rows.picker_button()`（24px / `padding:0` /
        #   吞掉 clicked 的 bool —— 三条坑都在那儿）。这里只负责"点了要干嘛"。
        pick = card_rows.picker_button(
            lambda: self._pick_spec_value(header, editor, opts),
            hint=f'从候选里搜索挑选（共 {len(opts)} 个）')
        pick.setAccessibleName(f'挑选{header}')
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
            elif isinstance(w, QPlainTextEdit):
                w.setPlainText(value)       # `code` 格（CodeEditor 也是它）
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

        ⚠ `choose_from` **必须在这里 import**（不能提到模块顶层）：它是个
        模态对话框，判据（`test_vocabulary_wiring.py`）只能靠替换
        `choice_picker.choose_from` 来"不弹真窗"。顶层 import 会把名字在
        模块加载时**绑死**，替换定义处就再也影响不到这里 —— 实测症状是
        用例挂住 300s（真弹窗在等鼠标）。
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
        if name == 'SayTail':
            # 台词转场：控件里显示的是**转场名**（`dissolve`），写回的是
            # **整段子句**（`with dissolve`）—— 见 `split_say_tail` / `join_say_tail`
            value = split_say_tail(value)
        field_widgets.write_value(w, value)

    def _get_value(self, name) -> str:
        """
        这一格**现在代表什么**（写进剧本的那个值，不是屏幕上那句）。

        ⚠ 读值走 `field_widgets.read_value`（按**控件类**分派，与画法同源）：
        下拉一定按**真值**读（词汇表让下拉显示 `1人，近处`，写回 beat 的必须
        还是 `i11`）；只有两件事是卡片自己的业务后处理 ——
        `SayTail` 要拼回整段、参数格要折成一行。
        """
        w = self._widgets.get(name)
        if w is None:
            return ''
        raw = field_widgets.read_value(w)
        if isinstance(raw, bool) or isinstance(raw, list):
            # 卡片上的格都是**字符串**（写回 `Beat` 的字段 / 折进语句头）。
            # 开关和多选读出来不是字符串 —— 到这一步说明有人给卡片字段声明了
            # 这两种类型（`ext.card_fields` 会拒；这里兜底不让它写坏数据）。
            return ''
        text = str(raw or '')
        if name == 'SayTail':
            return join_say_tail(text)
        text = text.strip()
        if name in (getattr(self, '_spec_widgets', None) or {}):
            # 参数格**折成一行**：它最终要折回语句头，多行会让那条语句语法坏掉。
            # 高级区的 `CodeBlock` 不在这里，保持原样多行（它就是块体）。
            text = ' '.join(part.strip() for part in text.splitlines()
                            if part.strip())
        return text

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

    # ---------- 句中换表情（2026-11-14） ----------

    def text_cursor_position(self) -> int:
        """
        台词框里光标的位置（**选了字就是选区起点**）——「句中换表情」从这里断。

        为什么用选区起点而不是"选区的结尾"：人圈住"哇！"再点断开，想的是
        "这一段单独成一口"——那么断开点就在这段**前面**。
        """
        cursor = self._text.textCursor()
        if cursor.hasSelection():
            return int(cursor.selectionStart())
        return int(cursor.position())

    def set_sentence_context(self, kind: str, text: str,
                             next_kind: str = '', blocked: str = '') -> None:
        """
        按"这一拍是不是一句话的一口"来摆那个按钮和说明行。

        `kind`      这一拍的 `kind`（`say` / `narration` / `extend` / …）
        `text`      这一拍的台词原文（判"结尾有没有 `{nw}`"用它，不看控件，
                    因为控件里可能还没提交）
        `next_kind` 下一拍的 `kind` —— 用来点破那个常见的坑：
                    写了 `{nw}` 却没有「接续」卡跟在后面。
        `blocked`   这一拍**为什么不能拆**（条件块 / 循环 / 菜单里；空串 = 能）。
                    由会话的 `split_blocked_reason` 给 —— 前端不另编一套理由。
                    按下按钮时**摆在按钮下面**，不能只往状态栏扔一句：
                    用户 2026-11-14 报的"点了没反应"，一半就是那句话没人看见。

        ⚠ 说明行是 **QLabel**：不渲染 Markdown，只写纯文字（`**` 会原样显示）。
        """
        kind = str(kind or '').strip()
        body = str(text or '')
        self._split_kind, self._split_text = kind, body
        self._split_blocked = str(blocked or '').strip()
        # 换了一张卡 = 上一次"选到一半"作废：不退出来的话，第二次点击会拿着
        # **上一张卡**的位置去断**这一张**的句子（比没反应严重得多）。
        self._exit_pick()
        can_split = kind in self.SPLIT_KINDS and bool(body.strip())
        self._split_btn.setVisible(can_split)
        self._split_btn.setEnabled(can_split)
        ends_nw = body.rstrip().endswith('{nw}')
        msg = ''
        if kind == 'extend':
            msg = '这张接上一句：和上一张是同一个对话框，不会另起一句。'
            if ends_nw:
                msg += '这句也还没说完，后面的「接续」卡接着讲。'
        elif ends_nw:
            if str(next_kind or '').strip() == 'extend':
                msg = '这句不等点击就往下走（{nw}）：下一张「接续」卡接着说。'
            else:
                # 真坑：`{nw}` 后面没有 extend，那句话会直接跳到下一句去
                msg = ('注意：这句写了 {nw}（不等点击），但下一拍不是「接续」卡'
                       ' —— 后面那半句会直接跳到下一句去。')
        self._sentence_label.setText(msg)
        self._sentence_label.setVisible(bool(msg))

    # ---------- 「选断开点」那一步（2026-11-14 晚上加） ----------

    def is_picking_split_point(self) -> bool:
        """现在是不是"等你在台词里点一个字"那一步（窗口 / 测试要看这个状态）。"""
        return bool(self._picking)

    def _begin_split(self, *_):
        """按下「✂ 句中换表情」：**第一次**进选择模式，**第二次**才真断。

        `*_` 收下 `QPushButton.clicked` 那个 `checked=False` 参数 —— Qt 的
        信号是带参的，槽签名不收会在某些绑定下抛 TypeError（只打 stderr、
        界面不动，正是"点了没反应"那一类）。
        """
        if self._picking:
            self._confirm_split()
            return
        if not self._can_split():
            # 按钮本该隐藏着（`set_sentence_context` 按卡的 kind 判）。真走到
            # 这里说明状态不同步 —— 也要出声，不能让它"点了没反应"。
            self.show_split_note('这一张没有"一句台词"可以断（只有对白 / 旁白 / '
                                 '接续卡能这样拆）。')
            return
        if self._split_blocked:
            # 条件块 / 循环 / 菜单里不能拆：理由摆在按钮下面，不是只扔状态栏。
            self.show_split_note(self._split_blocked)
            return
        self._enter_pick()

    def _can_split(self) -> bool:
        """这一拍现在能不能拆（种类对 + 台词框里真有字）。"""
        return (self._split_kind in self.SPLIT_KINDS
                and bool(self.text()))

    def _refresh_split_availability(self, *_):
        """打字时跟着改按钮的可见性（台词从空变成有字，按钮就该出现）。"""
        if self._picking:
            return
        ok = self._can_split()
        self._split_btn.setVisible(ok)
        self._split_btn.setEnabled(ok)

    def _enter_pick(self) -> None:
        """进「选断开点」模式：光标看得见、预览会说话、按钮变成"确认"。"""
        self._picking = True
        self._split_btn.setText(self.SPLIT_BTN_CONFIRM)
        self._split_cancel.setVisible(True)
        # 台词框描一圈主色：告诉人"要在这上面点"，别去别处找。
        self._text.setStyleSheet(
            f'QPlainTextEdit {{ border: 2px solid {T.t("brand.primary")}; }}')
        self._text.setFocus(Qt.OtherFocusReason)     # 光标得亮着，否则还是看不见
        self._refresh_split_preview()

    def _exit_pick(self) -> None:
        """退出选择模式（取消 / 断开之后 / 换卡）。**一个字都不改。**

        ⚠ 这里必须把按钮重新置为可用：选择模式里"光标在句首"那会儿它是灰的，
        取消之后不恢复就是**又一颗点不动的按钮**（正是这一轮要修的那个病）。
        """
        self._picking = False
        self._split_btn.setText(self.SPLIT_BTN_TEXT)
        self._split_btn.setEnabled(self._can_split())
        self._split_cancel.setVisible(False)
        self._split_preview.setVisible(False)
        self._text.setStyleSheet('')

    def cancel_split(self, *_):
        """「取消」：退出选择模式，什么都不动。"""
        self._exit_pick()

    def _pick_position(self) -> int:
        """
        光标落在**去掉首尾空白**的台词里是第几个字（预览与断开都用它）。

        为什么要减 `lead`：光标下标是控件里那串原文的，而写回 / 断开用的是
        `text()`（`strip` 过的）。两者差的就是行首空白那几个字 —— 不减的话
        断点会整体往后挪。
        """
        raw = self._text.toPlainText()
        lead = len(raw) - len(raw.lstrip())
        return max(0, min(len(self.text()),
                          int(self.text_cursor_position()) - lead))

    def _refresh_split_preview(self, *_):
        """选择模式里实时说：断在哪个字前面、两半各是什么。"""
        if not self._picking:
            return
        body = self.text()
        cut = self._pick_position()
        if not 0 < cut < len(body):
            # 光标在两头断不出两半 —— 不猜（铁律 4），把该怎么做说出来。
            self._split_preview.setText(
                '第 1 步：在台词里点要断开的那个字（就断在它前面）。\n'
                '点好之后按「✂ 就断在这儿」。\n'
                '现在光标在句首/句尾，那里断不出两半。')
            self._split_btn.setEnabled(False)
        else:
            head, tail = body[:cut].rstrip(), body[cut:].lstrip()
            self._split_preview.setText(
                f'断在「{body[cut]}」前面：\n'
                f'前半（留在这张卡）  {head}{{nw}}\n'
                f'后半（接续卡，可换表情）  {tail}')
            self._split_btn.setEnabled(True)
        self._split_preview.setVisible(True)

    def _confirm_split(self, *_):
        """第二步：真断。位置要在退出选择模式**之前**取（退出会动按钮/焦点）。"""
        cut = self._pick_position()
        self._exit_pick()
        self.split_requested.emit(int(cut))

    def show_split_note(self, msg: str) -> None:
        """把"为什么没断 / 为什么不能拆"摆在按钮下面（状态栏太远，会被当成没反应）。"""
        text = str(msg or '').strip()
        self._sentence_label.setText(text)
        self._sentence_label.setVisible(bool(text))

    def is_advanced_open(self) -> bool:
        return bool(self._adv_open)

    def sprite_cards(self) -> list:
        """
        能**写回这一拍**的立绘卡。

        `inherited=True` 的是"台上沿用着的"（`session.cards()` 按舞台快照补的
        只读卡）—— 用户没动它就不能写回，否则"点一下保存"会把上一拍的人
        物/表情整个物化进这一拍的 `show` 里（那不是他改了，是界面替他改了）。
        在卡片上改一次表情/位置，`SpriteCardWidget` 就会把 `inherited` 清掉，
        它才出现在这里。
        """
        return [it for it in self._grid.cards() if not it.get('inherited')]

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
                    source_lines=None, inherited: dict = None):
        """
        把一拍的内容填进卡片。

        `_loading` 期间屏蔽脏标记计算：填控件本身会触发一串
        `textChanged`，不加这道闸，"刚载入"会被判成"已修改"。

        `source_lines=(first, last)`：这一拍在源 `.rpy` 里的行号范围 ——
        给 CodeBlock 的**行号栏**用（用户要"精确到行"，好回编辑器里定位）。
        不给（新建卡片 / 单测）→ 行号退回 1/2/3…

        `inherited`：这一拍**屏幕上生效**、但**不是这一拍写的**舞台值
        （`app.session_table.stage_state(idx)` 的结果）。用户 2026-11-02：

        > 当前舞台是有的，但是当前卡片却没有显示现在的 BGM 等东西呢……？
        > 把当前舞台的持续状态填充到前端（后端自己知道这一列跟前面一样，
        > 不做任何改变就行，只有确实已经改了，才再做真正的程序导出）

        所以：**背景/音乐这一拍没写 → 填上沿用的值**，并在标题上写明
        「沿用上一拍」；`collect_fields()` 会把"没改过的沿用值"还原成空串，
        后端照样只写这一拍真有的东西。
        """
        #: 这一拍**源文件里的原始字段**（不是控件读回值）。`_sync_field_groups`
        #: 靠它判"有没有台词 / 有没有舞台动作" —— `Show` 那格背后是立绘网格，
        #: 不在 `_all_names()` 里，从 `_loaded_fields` 取会永远为空。
        self._loaded_raw = {str(k): str(v or '') for k, v in (fields or {}).items()}
        self._loading = True
        try:
            # 控制流条由 `_sync_spec_args` 在 load_fields 之后按这一拍重建；
            # 先收起来，避免上一拍的 `call side` 闪一下。
            self._flow = None
            self._flow_bar.setVisible(False)
            for name in self._all_names():
                self._set_value(name, fields.get(name, ''))
            self._fill_inherited_fields(fields, inherited)
            self._grid.load(sprite_items)
            _code = self._widgets.get('CodeBlock')
            if isinstance(_code, CodeEditor):
                first, last = (source_lines or (None, None))
                _code.set_source_lines(first, last)
            self._loaded_fields = {n: str(fields.get(n, '') or '')
                                   for n in self._all_names()}
            # **填进去的"沿用"值也算基线**：它们显示在控件里，但这一拍没写。
            # 不并进基线的话，翻页提交时"控件里有 black、字段里没有"会被判成
            # "表单被改了"（关窗就会问"你改了 X 拍"，而那只是翻了一章）。
            self._loaded_fields.update(self._inherited_fields)
            # Stage 那一格的原文单独记一份（判"手改过没有"要用它）
            self._stage_loaded = str(fields.get('Stage', '') or '')
            # 立绘卡的基线同样要在**载入完**之后记（`_grid.load` 可能补默认值）
            self._loaded_cards = self._card_state()
            # 舞台块（立绘 / 背景 / 音乐 / 语音）**常显**（2026-10-29）：
            # 载入一张普通卡片时确保它在上面 —— 上一拍可能是控制流卡
            # （`set_flow(info)` 会把整块收起来），不能让它一直收着。
            # 真正的控制流卡由随后的 `set_flow(info)` 再收起，见那里的说明。
            self._stage_box.setVisible(True)
            self._stage_now.setVisible(True)
            # 台词后缀：**下拉表示不了的那几类**才提示。
            #
            # 2026-11-03：纯 `with xxx` 现在就在台词下面有下拉，不用再提示
            # （从前这句还写着"在「高级区 · 台词后缀」里改"—— 那句已经不成立）。
            # 剩下 `(what_color="#f00")` / `id 名字` / `nointeract` 这些
            # 手写的子句没有候选，才给一行只读提示。
            tail = str(fields.get('SayTail', '') or '').strip()
            extra = '' if not tail or _PURE_WITH_RE.match(tail) else tail
            self._say_tail_label.setText(
                f'这一句还带了手写子句：{extra}'
                f'（在台词下面那格的输入框里改）' if extra else '')
            self._say_tail_label.setVisible(bool(extra))
        finally:
            self._loading = False
        self.refresh_dirty()

    @classmethod
    def _all_names(cls) -> tuple:
        """卡片上**全部**字段名（主区 + 高级区 + 额外控件）。"""
        return tuple(PRIMARY_FIELDS) + tuple(ADVANCED_FIELDS) + cls.EXTRA_WIDGETS

    def collect_fields(self) -> dict:
        out = {n: self._get_value(n) for n in PRIMARY_FIELDS}
        # ⚠ 2026-11-07：**收起高级区也照旧写回**。
        #
        # 从前这里是 `if self._adv_open:` —— "收了高级区"就等于"这些字段不算数"，
        # `SayTail` 曾经因此**静默丢**过一次（当时靠把它挪到主区绕过去，根因没修）。
        # Unity 的 Foldout 正是这个语义：折叠只影响**显示**，不改变数据。
        #
        # ⚠⚠ **收集与落地还没统一**（2026-11-07 第三轮记在案）：数据层
        #   `FieldWriter._apply_advanced_fields` 仍按 `advanced_open` 过滤
        #   （"收起 ≠ 清空"）。也就是说收起时这里收齐了，落地仍不动那几格。
        #   实际影响很小（收起时人看不见、也编辑不到高级区），但两边口径
        #   不一致这件事要写出来，别让下一轮的人以为已经统一了。
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
        self._drop_inherited(out)
        return out

    # ---------- 「沿用上一拍」的显示值（改了才写） ----------

    def _fill_inherited_fields(self, fields: dict, inherited: dict = None) -> None:
        """
        这一拍**没写**的背景/音乐 → 填上屏幕上沿用的值（只显示）。

        值本身要和"真写了"区分开：标题后面加「沿用上一拍」，tooltip 写清
        "这一拍没写它"。改动任何一格之后，它就成了这一拍的真值
        （`_drop_inherited` 只清掉**没改过**的那些）。
        """
        self._inherited_fields = {}
        data = inherited if isinstance(inherited, dict) else {}
        for name, label in self.INHERITED_FIELDS:
            own = str(fields.get(name) or '').strip()
            value = str(data.get(name) or '').strip()
            cap = self._asset_caps.get(name)
            if own or not value:
                if cap is not None:
                    cap.setText(label)
                    cap.setToolTip('')
                continue
            self._set_value(name, value)
            self._inherited_fields[name] = value
            if cap is not None:
                cap.setText(f'{label}（沿用上一拍）')
                cap.setToolTip(
                    f'这一拍**没写**{label} —— 画面沿用 `{value}`。\n'
                    '直接改它就等于「这一拍换掉它」（会真的写进这一拍）；'
                    '不改则一个字都不写。')

    def _drop_inherited(self, out: dict) -> None:
        """
        填进来的"沿用"值：**没改过就不写回**（后端只写这一拍真有的东西）。

        判据是"控件里的值 == 载入时填的沿用值"。用户一改就不相等 → 当这一拍
        的真值交出去（这才是"确实已经改了才导出"）。
        """
        for name, value in list((self._inherited_fields or {}).items()):
            if str(out.get(name) or '').strip() == value:
                out[name] = ''

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
        # 立绘卡也算进"已提交状态"：保存 / 顺带提交之后，网格不再是"改脏"的
        self._loaded_cards = self._card_state()
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

    #: 立绘卡上**用户能改**的那几格（判"改没改"只比这些）。
    #: `effective_at` / `inherited_at` 是**显示派生**（舞台快照一变就跟着变），
    #: 拿它们当判据会把"翻了一页"判成"改了立绘"。
    CARD_EDIT_KEYS = ('image', 'expression', 'position', 'at', 'speaking',
                      'order_index', 'extra_rows')

    def _card_state(self) -> list:
        """要写回这一拍的立绘卡，投影成可比对的不可变列表。"""
        out = []
        for it in self.sprite_cards():
            row = []
            for key in self.CARD_EDIT_KEYS:
                value = (it or {}).get(key)
                # `extra_rows` 是格子行列表：转成不可变形式才好比
                row.append(repr(value) if isinstance(value, (list, dict))
                           else value)
            out.append(tuple(row))
        return out

    def cards_modified(self) -> bool:
        """
        立绘卡和"载入时 / 上一次提交时"相比变了吗？

        `is_modified()` 只管表单字段；立绘的改动直接写在卡片自己的 `item`
        字典上（见 `SpriteCardWidget._set_field`），所以单独判 —— 没有这一条，
        "改了个表情就翻页"会被当成"什么都没改"：内容写进了拍，却不留撤销。

        只比**能写回**的卡（`sprite_cards()` 会把"台上沿用、用户没动"的滤掉）：
        那些卡本来就不该因为翻页而物化进这一拍。
        """
        if self._loaded_cards is None:
            return False
        return self._card_state() != self._loaded_cards

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

    def _dirty_hook(self):
        """一个"输入变了 → 刷新未保存提示"的回调（**载入期间静默**）。"""
        def hook(*_a):
            if not self._loading:
                self.refresh_dirty()
        return hook

    def _hook_dirty(self, w) -> None:
        """给一个控件挂上未保存提示（按**信号名**试，不按控件类分派）。"""
        for sig in ('textChanged', 'currentTextChanged'):
            s = getattr(w, sig, None)
            if s is None:
                continue
            try:
                s.connect(self._dirty_hook())
            except Exception:                # noqa: BLE001 - 挂不上不拦操作
                swallow('main_window._connect_dirty_tracking')

    def _connect_dirty_tracking(self):
        """任何输入变化都刷新一次"未保存"提示。"""
        for w in self._widgets.values():
            self._hook_dirty(w)
        self._grid.change.connect(self._dirty_hook())
