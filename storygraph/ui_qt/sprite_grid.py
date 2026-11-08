"""
storygraph.ui_qt.sprite_grid
============================
Qt 版**立绘网格**（真实缩略图 + 卡内 chip 换表情/位置）。

数据与语义全部来自**后端纯函数**（tkinter 版同名那批）：
  · `group_sprites_by_tag`（按角色聚合，多表情条目收进 extras，**不丢数据**）
  · `lookup_image_id` / 预览路径解析（与预览窗口同一套）
  · `expression_choices` / `position_choices` / `normalize_expression_words`

本模块只做 Qt 的**显示 + 交互**：
  · `SpriteCardWidget` —— 一张卡（缩略图 + 名字 + 表情 chip + 位置 chip + ✕）
  · `SpriteGridWidget` —— 网格（QGridLayout）+ 末尾「＋ 添加立绘」
  · `cards_to_show(cards, symbols)` —— 卡 → `show` 文本（写回）

**2026-10-30（④⑥ 第二阶段）：加图 / 换图这条路要"看得见判定"**
------------------------------------------------------------------
选择器在 `accept()` 时已经能判"这张图能不能进 show"（`app.picker.resolve_pick`，
S1 的唯一真源）。这里负责**把判定结果摆到卡片上、不许静默写**：

  · 选了预览图 / 头像 → 选择器自动换成真立绘，卡片上出现一条提示条，
    写明"原资产 → 实际写进卡片的 spec"（`PickResult.replaced_from/to/message`）；
  · spec 首词**不是已知角色 tag** → 卡片上给**警告**提示条，并给一个
    「去『资源映射』修」的按钮（真源是 `Symbols.is_actor_tag`）；
  · 判不了（项目没打开 / 符号表为空）时**不警告** —— 那会是假警报，
    与缩略图"没有"那条同一个纪律（见 `SpriteCardWidget._load_thumb`）。

**card item 的数据契约**（与 tkinter 版一致）：
  {image: str, expression: str, position: str, at: str, speaking: bool,
   order_index: int, extra_rows: list}
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLayout, QPushButton, QVBoxLayout, QWidget)

from storygraph.app.beats import (apply_attr_slot, attr_slot_rows,
                                  choices_for_image,
                                  normalize_expression_words,
                                  uniform_sprite_position)
# `uniform_position_of` 以前在这里**又写了一份**（与 `app/session_table.py`
# 里那份逐字节相同）—— 2026-10-05 收敛：它是"卡数组 → 整拍位置"的**唯一规则**，
# 而 `apply()` 写回 `beat.at` 用的正是它。两份一旦漂移，
# "卡片上显示的位置"与"存进 .rpy 的位置"就会不一致
# （那正是当年"立绘错位"事故的形态）。所以这里只 re-export，不再自带实现。
from storygraph.app.session_table import uniform_position_of  # noqa: F401
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.labeled_choices import labeled
from storygraph.ui_qt import sprite_hide as SH
from storygraph.ui_qt.sprite_codes_view import (art_code, code_of,
                                                slot_choices,
                                                slot_kind as _slot_kind)

from storygraph.core.safeguard import swallow
# 立绘卡的**提示语**与**缩略图解析**各是一个叶子模块（2026-11-03 / 2026-11-05
# 两次拆分：本文件原本 1498 行，两次都顶到了 1500 行技术债红线）。
# 这里各 re-export 一次，`from ...sprite_grid import ...` 的老调用点（含测试）
# 一个字都不用改。
from storygraph.ui_qt.sprite_hints import known_tag_warning  # noqa: F401
from storygraph.ui_qt.sprite_thumbs import (  # noqa: F401
    resolve_sprite_thumb_path)

CARD_W = T.CARD['w'] + 26            # 卡片宽（含 chip 行）
THUMB_W = T.CARD['thumbW'] + 8
THUMB_H = T.CARD['thumbH'] + 8


def sprite_thumb_size() -> tuple:
    """卡片上立绘缩略图的解码尺寸（**唯一的真源**）。"""
    return (THUMB_W, THUMB_H)


def sprite_compose_size() -> tuple:
    """
    分层合成时用的目标尺寸 = **2× 格子**（2026-11-05）。

    为什么不是 1×：合成图之后还要被 `ThumbLoader` 再缩一次，2× 留够余量
    （HiDPI 也不糊），而 PNG 编码量只有 240×320 级别 —— 比原图 4320×7680
    快三个数量级（真机：一次合成 2.4s → 十几毫秒）。
    """
    return (THUMB_W * 2, THUMB_H * 2)


#: chip 上最多显示这么多个字（右栏 340px 是硬预算，长名字会顶宽卡片）。
CHIP_TEXT_MAX = 10


def _short(text, limit: int = CHIP_TEXT_MAX) -> str:
    """chip 上的短名字：超长截断 + `…`（全文放 tooltip，见 `_raw_tip`）。"""
    got = str(text if text is not None else '').strip()
    return got if len(got) <= limit else got[:limit - 1] + '…'


def _raw_tip(kind: str, ident, vocabulary=None) -> str:
    """
    "词汇表里它叫什么 + 引擎里写的是什么"那两行提示。

    ⚠ chip 上的名字会被截断（右栏 340px），**全文必须留在 tooltip 里** ——
    否则长名字的人就只能看到「一个特别特别特…」。
    """
    vocab = vocabulary
    if vocab is None:
        return ''
    try:
        shown = vocab.label(kind, ident)
    except Exception as e:                     # noqa: BLE001 - 提示失败不算错
        swallow('ui_qt.sprite_grid._raw_tip', e)
        return ''
    raw = str(ident if ident is not None else '').strip()
    if not shown or shown == raw:
        return ''
    return f'词汇表里叫：{shown}\n引擎里写的是 `{raw}`\n'


def _accepts_one_argument(fn) -> bool:
    """
    这个回调**收不收一个参数**（决定「去『资源映射』修」要不要带上 spec）。

    为什么要判而不是直接 `fn(spec)`：这个入口是主窗口注入的，
    测试里常写成无参 lambda；直接传参会 `TypeError`，而"先试一次再退回去"
    等于**可能执行两遍**。签名看不懂就当它不收参数（退回老行为，最安全）。
    """
    if not callable(fn):
        return False
    import inspect
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return False
    kinds = (inspect.Parameter.POSITIONAL_ONLY,
             inspect.Parameter.POSITIONAL_OR_KEYWORD,
             inspect.Parameter.VAR_POSITIONAL)
    return any(p.kind in kinds for p in params)


def pick_spec_and_result(value) -> tuple:
    """
    选择器回调的返回值 → `(spec, PickResult | None)`（两种契约都认）。

    历史原因：这条路上存在**两代回调**
      · 老：`on_pick_image(current) -> str`（`window_assets._pick_image`）；
      · 新：`on_pick_image_result(current) -> PickResult | str`（本轮接线，
        多了 `replaced_from/to/message`，卡片提示条要用）。
    把它们归一在这一个函数里，`_on_add` / `_on_click` 两条路就不用各判一次
    —— 也避免"老回调还能用、新回调没接上"这种半接线状态。
    """
    if value is None:
        return '', None
    if isinstance(value, str):
        return value.strip(), None
    spec = str(getattr(value, 'spec', '') or '').strip()
    return spec, value



class SpriteCardWidget(QFrame):
    """一张立绘卡：缩略图 + 名字 + 表情 chip + 位置 chip + ✕。"""

    clicked = Signal(object)          # self
    remove_requested = Signal(object)

    def __init__(self, item: dict, symbols=None, loader=None,
                 parent=None, on_change=None, on_resolve_path=None,
                 choice_groups=None, choice_groups_for=None,
                 assets_ready: bool = False, on_open_mapping=None):
        super().__init__(parent)
        self.item = dict(item or {})
        self._symbols = symbols
        self._loader = loader
        self._on_change = on_change
        self._on_resolve_path = on_resolve_path
        #: 「去『资源映射』修」的入口（主窗口注入；None = 不显示那个按钮）
        self._on_open_mapping = on_open_mapping
        #: 这个入口要不要"带上这一条 spec"（见 `_accepts_one_argument`）。
        self._mapping_wants_spec = _accepts_one_argument(on_open_mapping)
        #: 选择器刚给的三件事（`PickResult.message` / 换图前 / 换图后）。
        #: 只在"这次选中"之后显示一次；关掉提示条或再换一次图就没了。
        self._pick_note = ''
        self._pick_note_tip = ''
        #: 现在提示条显示的是什么（`_refresh_hint` 维护；判据读它）
        self._hint_text = ''
        #: **现在有没有"判得了"的底子**（项目打开过 / 资源表读过）。
        #:
        #: 用户 2026-10-13："如果在卡牌编辑器内打开时，若找不到对应的文件，
        #: 我希望你能给出提示（不必是弹窗，直接在立绘那里显示没有）"。
        #: ⚠ 反过来同样重要（**通用性**）：项目都没打开时谁也判不了 ——
        #: 那时**不许**把每张立绘都写成"没有"（那是假警报，比不提示更糟）。
        self._assets_ready = bool(assets_ready)
        #: 这次画出来的是"没有 / 读不出"吗（`_mark_missing` 维护）
        self._missing = False
        #: 项目声明的候选分栏（`[{'name','label','options'}]`）——**只当改名层**
        #: （`outfit` → 「服装」）；分组结构本身来自引擎，见 `_slot_rows`。
        self._choice_groups = dict(choice_groups or {})
        #: `tag -> [{'group','label','values'}]`（窗口注入；现取，见 `_groups_for`）
        self._choice_groups_for = choice_groups_for
        #: **一维一格**的 chip（引擎分组：`outfit` / `expressions`…）；
        #: 空 = 这张图只有一条维度，走从前的单格老路（`sprite_grid.attr_slot_rows`）
        self._slot_btns: list = []
        self.setProperty('role', 'spriteCard')
        self._build()
        self.refresh()

    def set_choice_groups(self, groups):
        """窗口在（重新）读注册表后推过来：项目声明的候选分栏分组。"""
        self._choice_groups = dict(groups or {})

    def set_assets_ready(self, ready: bool):
        """
        「现在判得了吗」—— 项目打开 / 资源表读完之后置真，然后**重画缩略图**。

        为什么要重画：项目是**后**打开的（卡片先画出来），那时缩略图已经画成
        "没有"了；不重画的话那个提示会一直挂着（反过来也一样：真的缺图，
        但项目刚打开、提示还没出来）。
        """
        ready = bool(ready)
        if ready == self._assets_ready:
            return
        self._assets_ready = ready
        try:
            self._load_thumb(str(self.item.get('image') or ''))
        except Exception as e:                     # noqa: BLE001 - 重画失败不该炸卡片
            swallow('ui_qt.sprite_grid.set_assets_ready', e)

    def set_symbols(self, symbols):
        """
        符号表晚到了（项目打开 / 重读资源表）→ 重判"首词是不是角色 tag"。

        为什么卡片也要收：网格 `set_symbols` 从前只换自己那份，已经画出来的
        卡片还拿着旧符号表 —— 于是"项目刚打开"之后卡片仍按"判不了"沉默，
        要翻页重画才出提示（看起来像时有时无的 bug）。
        """
        self._symbols = symbols
        self.refresh()

    # ---------- 判定提示条（④⑥ 第二阶段） ----------

    def show_pick_hint(self, result):
        """
        选择器给的结论 → 卡片提示条（`PickResult`；也接受纯 spec 字符串）。

        用户能看懂的只有两件事：**我选的被换成了什么**、**为什么**。
        所以文案优先用 `PickResult.message`（S1 写好的那句话），
        换过图时再把 `replaced_from → replaced_to` 放进 tooltip（要查时查得到）。
        """
        if result is None or isinstance(result, str):
            return                                  # 老回调：没有结论可摆
        msg = str(getattr(result, 'message', '') or '').strip()
        was = str(getattr(result, 'replaced_from', '') or '').strip()
        now = str(getattr(result, 'replaced_to', '') or '').strip()
        if now:
            note = msg or f'已自动换成真立绘：{now}'
            tip = (f'原资产：{was or "（未知）"}\n'
                   f'自动改用：{now}\n'
                   f'（预览图 / 头像不能直接进 show，写进卡片的是真立绘）')
        elif msg:
            note, tip = msg, ''
        else:
            self._pick_note = self._pick_note_tip = ''
            self._refresh_hint()                    # 没有话说 = 收回上一条
            return
        self._pick_note, self._pick_note_tip = note, tip
        self._refresh_hint()

    def dismiss_hint(self):
        """用户手动收起提示条（不读、不改任何值）。"""
        self._pick_note = self._pick_note_tip = ''
        self._hint.setVisible(False)
        self._hint_text = ''

    def _vocabulary(self):
        """
        这张卡片能用的**词汇表**（窗口挂在 `_for_tag` provider 上）。

        为什么不在这里 import 项目映射：卡片是"只认传进来的东西"的一层
        （`BeatCardEditor` / 网格都不碰项目根）。词汇表跟 `engine_facts` /
        `layer_model` 一样，由窗口挂上去 —— 少一个注入通道，少一次"忘了转发"。
        """
        from storygraph.assets import vocabulary as V
        return getattr(self._choice_groups_for, 'vocabulary', None) \
            or V.EMPTY_VOCABULARY

    def _say(self, kind: str, ident, char: str = '') -> str:
        """
        引擎写法 → 屏幕上那串字（查不到原样）。

        ⚠ **只截断词汇表起的名字**：原名（`base_handdown` / `i11`）一个字都不许
        少 —— 那是用户对着剧本核对的锚点，截了就成了另一张图
        （实测：把 `base_handdown` 截成 `base_hand…` 立刻有三条判据红）。
        """
        raw = str(ident if ident is not None else '').strip()
        text = self._vocabulary().label(kind, ident, char)
        return _short(text) if text != raw else text

    def _open_mapping(self):
        fn = self._on_open_mapping
        if not callable(fn):
            return
        spec = str(self.item.get('image') or '').strip()
        try:
            if self._mapping_wants_spec:
                fn(spec)
            else:
                fn()
        except Exception as e:                 # noqa: BLE001 - 入口坏了不该炸卡片
            swallow('ui_qt.sprite_grid.open_mapping', e)

    def _refresh_hint(self):
        """
        提示条的唯一决定点（`refresh()` 每次都过一遍）：

          1. 首词不是已知角色 tag → **警告** + 「去『资源映射』修」按钮；
          2. 否则这次选中换过图 → 普通提示 + tooltip 里的"原 → 新"；
          3. 都没有 → 隐藏。

        ⚠ 顺序不能反：换图提示（"已换成真立绘"）只说明发生过什么，
        而"首词不是角色 tag"是**还没解决**的事 —— 后者必须压在上面。
        """
        spec = str(self.item.get('image') or '').strip()
        warning = known_tag_warning(spec, self._symbols)
        if warning:
            text, warn = warning, True
            tip = warning
        elif self._pick_note:
            text, warn = self._pick_note, False
            tip = self._pick_note_tip
        else:
            self._hint_text = ''
            self._hint.setVisible(False)
            return
        if text == self._hint_text and self._hint.isVisible():
            return                                  # 同一条，不折腾样式/重排
        self._hint_text = text
        self._hint_label.setText(text)
        self._hint_label.setToolTip(tip or text)
        self._hint.setToolTip(tip or text)
        if warn:
            self._hint.setStyleSheet(
                f"QFrame#spriteCardHint {{"
                f" background: {theme.t('state.warningBg')};"
                f" border: 1px dashed {theme.t('border.dashed')};"
                f" border-radius: {T.RADIUS['control']}px; }}")
            self._hint_label.setStyleSheet(
                f"color: {theme.t('state.warning')}; background: transparent;")
        else:
            self._hint.setStyleSheet(
                f"QFrame#spriteCardHint {{"
                f" background: {theme.t('bg.skeleton')};"
                f" border-radius: {T.RADIUS['control']}px; }}")
            self._hint_label.setStyleSheet(
                f"color: {theme.t('text.secondary')}; background: transparent;")
        # 入口按钮：只有"判不了/判错了"这种要人去修的情况才给（换图提示给了反而误导）
        self._hint_mapping_btn.setVisible(
            bool(warn) and callable(self._on_open_mapping))
        self._hint.setVisible(True)

    # ------------------------------------------------------------------

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self._thumb = QLabel()
        self._thumb.setFixedSize(THUMB_W, THUMB_H)
        self._thumb.setAlignment(Qt.AlignCenter)
        self._thumb.setProperty('role', 'caption')
        self._thumb.setStyleSheet(
            f"background: {theme.t('bg.skeleton')};"
            f"border-radius: {T.RADIUS['control']}px;")
        top.addWidget(self._thumb, 0, Qt.AlignCenter)

        close = QPushButton('✕')
        close.setFixedSize(20, 20)
        close.setProperty('variant', 'danger')
        close.setStyleSheet('padding:0;border-radius:10px;font-size:10px;')
        close.clicked.connect(lambda: self.remove_requested.emit(self))
        top.addWidget(close, 0, Qt.AlignTop | Qt.AlignRight)
        #: 「沿用」卡不给删（这一拍没写过它，删它该走 `hide`）—— 见 `refresh`
        self._close_btn = close
        lay.addLayout(top)

        self._name = QLabel()
        self._name.setProperty('role', 'caption')
        self._name.setWordWrap(True)
        self._name.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._name)

        chips = QHBoxLayout()
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(4)
        # 属性 chip（表情 / 服装…）**个数不固定**（一个引擎分组一个），
        # 所以放在一个子布局里、由 `_rebuild_slot_chips` 现造。
        self._chips = chips
        self._slot_box = QWidget()
        self._slot_lay = QHBoxLayout(self._slot_box)
        self._slot_lay.setContentsMargins(0, 0, 0, 0)
        self._slot_lay.setSpacing(4)
        chips.addStretch(1)
        chips.addWidget(self._slot_box)
        self._pos_btn = QPushButton()
        self._pos_btn.setStyleSheet(
            'padding:2px 6px;font-size:8pt;border-radius:8px;')
        self._pos_btn.clicked.connect(lambda: self._cycle('position'))
        # **左键 = 换下一个；右键 = 可搜索的候选列表**（用户 2026-10-05：
        # "既然变换如此多，那为什么不加个右键列表筛选跟搜索功能呢？"）——
        # 真实项目 151 个位置候选，靠点 40 次才轮到 `i11` 不叫能用。
        self._pos_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self._pos_btn.customContextMenuRequested.connect(
            lambda _pos: self._pick_from_list('position'))
        chips.addWidget(self._pos_btn)
        chips.addStretch(1)
        lay.addLayout(chips)

        # —— 判定提示条（默认隐藏；选择器给了结论 / 首词不是角色 tag 才出现）——
        #
        # 为什么放在卡片里而不是弹窗：用户 2026-10-30 的诉求是"加图这条路要
        # 说得清"，弹窗会打断连续加图；卡片上一条字 + 一个入口按钮，
        # 加完立刻看得见、也能当场去修。
        #
        # ⚠ 宽度：这一栏**不能**把卡片最小宽顶上去（右栏 340 是硬预算，
        # 见 `tests/test_ui_width.py`）—— 所以文字必须 `wordWrap`、
        # 按钮单独一行、提示条本身默认 hidden（隐藏的控件不参与布局最小宽）。
        self._hint = QFrame()
        self._hint.setObjectName('spriteCardHint')
        self._hint.setVisible(False)
        hint_lay = QVBoxLayout(self._hint)
        hint_lay.setContentsMargins(4, 2, 4, 2)
        hint_lay.setSpacing(2)
        self._hint_label = QLabel('')
        self._hint_label.setObjectName('spriteCardHintText')
        self._hint_label.setWordWrap(True)
        self._hint_label.setProperty('role', 'caption')
        self._hint_label.setTextFormat(Qt.PlainText)
        self._hint_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._hint_label.setMinimumWidth(1)
        hint_lay.addWidget(self._hint_label)
        hint_row = QHBoxLayout()
        hint_row.setContentsMargins(0, 0, 0, 0)
        hint_row.setSpacing(4)
        self._hint_mapping_btn = QPushButton('去「资源映射」修')
        self._hint_mapping_btn.setObjectName('spriteCardMappingBtn')
        self._hint_mapping_btn.setProperty('variant', 'chip')
        self._hint_mapping_btn.setStyleSheet(
            'padding:2px 6px;font-size:8pt;border-radius:8px;')
        self._hint_mapping_btn.setToolTip(
            '打开「🗂 资源映射」：把这张图认成某个角色/立绘，或看一眼它是怎么被认出来的')
        self._hint_mapping_btn.clicked.connect(self._open_mapping)
        self._hint_mapping_btn.setVisible(False)
        hint_row.addWidget(self._hint_mapping_btn, 0)
        hint_row.addStretch(1)
        self._hint_close_btn = QPushButton('✕')
        self._hint_close_btn.setObjectName('spriteCardHintClose')
        self._hint_close_btn.setFixedSize(18, 18)
        self._hint_close_btn.setStyleSheet(
            'padding:0;border-radius:9px;font-size:9px;')
        self._hint_close_btn.setToolTip('收起这条提示（什么都没改）')
        self._hint_close_btn.clicked.connect(self.dismiss_hint)
        hint_row.addWidget(self._hint_close_btn, 0)
        hint_lay.addLayout(hint_row)
        lay.addWidget(self._hint)

        lay.addStretch(1)
        self._rebuild_slot_chips()

    def _new_expr_btn(self, text: str = '') -> QPushButton:
        """造一个属性 chip（表情 / 服装…同一套样式与交互）。"""
        btn = QPushButton(text)
        btn.setProperty('variant', 'chip')
        btn.setStyleSheet('padding:2px 6px;font-size:8pt;border-radius:8px;')
        btn.setContextMenuPolicy(Qt.CustomContextMenu)
        btn.customContextMenuRequested.connect(lambda _pos: self._pick_from_list('expression'))
        return btn

    def _rebuild_slot_chips(self, rows=None):
        """
        按**引擎分组**重建属性 chip：一维一格（用户 2026-10-12）。

        从前只有**一个**表情格，于是 `show ye base_handdown gratified` 的两个词
        挤在同一格里：当前值（两个词）不在候选表里 → 点 chip 静默无效；从下拉里
        挑一个词又会把另一个维度吃掉（详见 `beats.attr_slot_rows` 的 docstring）。

        现在：`[服装: base_handdown] [表情: gratified] [位置: —]`，
        点哪格只换那一格的词。

        ⚠ 2026-10-16 口径改了：**建不建格看"引擎给了几个组"**，不看
        "这一拍写了几个词"。从前 `show ling happy`（一个词）会退回老路、
        连服装格都没有，于是同一套配置的卡片之间不一致
        （用户原话："为何一张卡片有服装，另一张卡片却没有啊？？？"）。
        现在引擎给了 ≥2 个组就每组一格；认不出引擎分组/项目没有
        `layeredimage` 时才走老路（`rows == []`）。
        """
        rows = self._slot_rows() if rows is None else list(rows or ())
        for btn in self._slot_btns:
            btn.setParent(None)
            btn.deleteLater()
        self._slot_btns = []
        #: 上一回画出来的「组名 + 标题」签名 —— **换图后组数相同、组名不同**
        #: 也必须重建按钮（否则闭包还指着旧组名，点表情改的是服装）。
        self._slot_signature = []
        while self._slot_lay.count():
            item = self._slot_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if not rows:
            self._expr_btn = self._new_expr_btn()
            self._expr_btn.clicked.connect(lambda: self._cycle('expression'))
            self._slot_lay.addWidget(self._expr_btn)
            self._slot_box.show()
            self._slot_signature = []
            return
        self._expr_btn = None
        for row in rows:
            group = str(row.get('group') or '')
            btn = self._new_expr_btn()
            btn.clicked.connect(lambda _=False, g=group: self._cycle_slot(g))
            self._slot_lay.addWidget(btn)
            self._slot_btns.append(btn)
        self._slot_signature = [
            (str(r.get('group') or ''), str(r.get('label') or ''))
            for r in rows]
        self._slot_box.show()

    def _slot_rows(self, spec=None) -> list:
        """
        这一格的**引擎分组行**（一维一格）；拿不到就 `[]`（→ 老路）。

        `spec` 可以不传（= 卡片自己那张图）。传别的图是给**右键候选**用的：
        那里要问的是"这条候选图有几个维度"，不是"卡片现在这张"。
        两者混着用会得出"候选里 18 条全都没有服装格"这种假结论。

        ⚠ **不新增注入通道**：窗口已经把 `set_choice_groups_provider(_for_tag)`
        接好了，`_for_tag` 上挂着 `attr_groups` / `facts` / `engine_facts` 三个
        属性（见 `window_card._sync_choice_groups`）。这里只读它们 —— 少一个方法
        就少一处"忘了转发"的机会（`main_window` 的转发层就踩过这个坑）。
        """
        provider = self._choice_groups_for
        if provider is None:
            return []
        spec = str(spec if spec is not None
                   else (self.item.get('image') or '')).strip()
        if not spec:
            return []
        facts = getattr(provider, 'engine_facts', None) or {}
        groups = getattr(provider, 'attr_groups', None) or []
        try:
            return list(attr_slot_rows(spec, self._symbols, groups=groups,
                                       facts=facts,
                                       layer_model=self._layer_model()) or [])
        except Exception as e:               # noqa: BLE001 - 分组拿不到就走老路
            swallow('ui_qt.sprite_grid._slot_rows', e)
            return []

    def _layer_model(self):
        """
        项目自己的 `layeredimage` 模型（没采过引擎事实时，维度只靠它认）。

        ⚠ 2026-10-16：那条路**必须知道项目根**，所以窗口把 `project_root`
        挂在 provider 上（`window_card._sync_choice_groups`）。三个地方都要它
        （维度行 / 换一维 / 右键候选），所以只留这一个入口 —— 少一份
        "忘了转发"的副本。
        """
        provider = self._choice_groups_for
        model = getattr(provider, 'layer_model', None)
        if model is not None:
            return model
        root = getattr(provider, 'project_root', None)
        if not root:
            return None
        try:
            from storygraph.story.layered_model import load_layer_model_cached
            return load_layer_model_cached(root)
        except Exception as e:               # noqa: BLE001 - 读不到就走老路
            swallow('ui_qt.sprite_grid._layer_model', e)
            return None

    def _art_code(self, spec=None, rows=None) -> tuple:
        """卡片标题的美术写法（`A7`）+ 拆解；规则在 `sprite_codes_view.art_code`。"""
        spec = str(spec if spec is not None
                   else (self.item.get('image') or '')).strip()
        toks = spec.split()
        rows = self._slot_rows(spec) if rows is None else list(rows or ())
        return art_code(rows, self._vocabulary(), toks[0] if toks else '')

    def _slot_apply(self, spec: str, group: str, value: str) -> str:
        """
        换一维的词 → 新 spec（认不出就**原样返回**，宁可不改也不猜）。

        `_set_slot_value`（点 chip）与右键候选共用这一条 —— 从前两处各写一份
        `apply_attr_slot(...)` 参数，右键那份漏了 `layer_model`，于是没采过
        引擎事实的项目右键列表一条都拼不出来（空列表＝"点了没反应"）。
        """
        provider = self._choice_groups_for
        try:
            return apply_attr_slot(
                spec, group, value, symbols=self._symbols,
                groups=getattr(provider, 'attr_groups', None) or [],
                facts=getattr(provider, 'engine_facts', None) or {},
                layer_model=self._layer_model())
        except Exception as e:               # noqa: BLE001 - 认不出就别改
            swallow('ui_qt.sprite_grid._slot_apply', e)
            return spec

    def _fallback_label(self) -> str:
        """
        没有引擎分组时那唯一一个 chip 的标题。

        项目**声明过分栏**（`attr_groups`）却认不出引擎结构时，那个格子里的词
        可能既不是表情也不是服装 —— 叫「属性」比硬说「表情」诚实（用户
        2026-10-18："服装与动作混为一谈"）。没声明过的项目保持原来的「表情」。
        """
        groups = getattr(self._choice_groups_for, 'attr_groups', None) or []
        return '属性' if groups else '表情'

    def _cycle_slot(self, group: str):
        """
        点某一维的 chip → **只换那一维的词**，其它维度原样保留。

        「空」也在圈里（用户 2026-11-02）：

        > 这个属性，如果点了一次就没法变成默认那个空了……这是为什么呢？

        从前候选只有引擎枚举出来的那几个词，`—`（这一维不写）**不在圈里**，
        于是"点了一下就再也回不去"。现在 `''` 排在最后：从任何一个词一直点，
        会走到空 → 再回到第一个词。空排最后（不是最前）是为了保住老行为 ——
        没有值的拍点一下仍然先认第一个候选，而不是原地不动（那看起来就像
        "点了没反应"，正是 2026-10-05 报过的那个症状）。

        ⚠ 空**不等于**组里第一个词：不写这一维 = 交给 layeredimage 的默认
        （`attribute x default` 或组里第一个），写上去则是把那一件钉死到这一拍。
        对 `sr_general` 的 `lan` 来说两者**画面上就不一样**：有服装走裁脸+衣层，
        没服装走全身表情差分（见 `definitions.rpy` 的 `when` 条件）。
        """
        rows = self._slot_rows()
        hit = next((r for r in rows if str(r.get('group') or '') == str(group or '')), None)
        if hit is None:
            return
        opts = list(hit.get('options') or [])
        cur = str(hit.get('value') or '')
        if not opts:
            return
        cycle = opts + ['']                       # 最后一个是「不写这一维」
        # 当前值不在候选里（项目自定义写法）→ 第一次点 = **固定住它**（值不变），
        # 与位置 chip 同一条规矩：绝不悄悄改掉一个我们认不出的值。
        if cur and cur not in cycle:
            self._set_slot_value(group, cur)
            return
        self._set_slot_value(group, cycle[(cycle.index(cur) + 1) % len(cycle)])

    def _set_slot_value(self, group: str, value: str):
        """某一维换成 `value`（`''` = 不写这一维）→ 重投影，其余维度不丢。"""
        spec = str(self.item.get('image') or '').strip()
        new = self._slot_apply(spec, group, value)
        if new and new != spec:
            self._apply_image_spec(new)

    # ------------------------------------------------------------------

    def refresh(self):
        spec = str(self.item.get('image') or '').strip()
        tag = spec.split()[0] if spec else ''
        is_hide = SH.is_hide(self.item)
        if hasattr(self, '_close_btn'):
            # **✕ 一直都在**（2026-11-08）：沿用卡也删得掉，删 = 这一拍
            # `hide` 掉 ta（语义在 `ui_qt.sprite_hide`）；退场卡变 ↺ 撤销。
            self._close_btn.setVisible(True)
            self._close_btn.setText(SH.close_text(self.item))
            self._close_btn.setToolTip(SH.close_tip(self.item))
        # —— 卡片标题 ——
        #
        # 有美术代号就显示 **`lan A3`**（用户 2026-11-02："你根本看不出来是
        # A3 呀！！！"—— 映射填了却只有那一页自己看得见）；没有代号的项目
        # 照旧显示引擎写法（`lan base_hand_up angry`），一个字节都不变。
        # 引擎写法本身**不丢**：chip 还在、tooltip 也写着，随时能核对。
        #
        # 分组行只算一次（标题要用它算代号，chip 也要用它）—— `attr_slot_rows`
        # 会去读项目的 `layeredimage`，一张卡算两遍不值当。
        rows = self._slot_rows()
        code, code_tip = self._art_code(spec, rows) if spec else ('', '')
        base_text = f'{tag} {code}' if code else (spec or '（点击换图）')
        self._name.setText(SH.title(self.item, base_text))
        _tip = ((f'引擎里写的是：`{spec}`'
                 + (f'\n\n{code_tip}\n（这张卡在脚本里仍然写引擎写法）' if code_tip
                    else '')) if spec else '')
        _tip += SH.title_tip(self.item)
        self._name.setToolTip(_tip)
        # 退场卡上的表情/位置没有意义（人不在台上）→ 置灰，点不动。
        for btn in list(getattr(self, '_slot_btns', ())) + [
                b for b in (getattr(self, '_expr_btn', None), self._pos_btn)
                if b is not None]:
            btn.setEnabled(not is_hide)
        expr = str(self.item.get('expression') or '').strip()
        pos = str(self.item.get('position') or '').strip()
        inherited = str(self.item.get('inherited_at') or '').strip()
        # 一维一格（引擎分组）；只有一条维度时 `rows` 为空 → 回到单个表情格。
        signature = [(str(r.get('group') or ''), str(r.get('label') or ''))
                     for r in rows]
        if rows and signature != self._slot_signature:
            # ⚠ 判据是**组名+标题的签名**，不是"个数"：同一张图换成
            #   `expressions,outfit` 顺序的另一套时，两个按钮的文字都更新了，
            #   可旧闭包还绑着旧组名 —— 点"表情"实际改的是服装（静默改数据）。
            self._rebuild_slot_chips(rows)
        if rows:
            for btn, row in zip(self._slot_btns, rows):
                raw = str(row.get('value') or '')
                shown = self._say(_slot_kind(row), raw, tag) if raw else (
                    str(row.get('display') or ''))
                btn.setText(
                    f"{row.get('label') or row.get('group') or '属性'}："
                    f"{shown or '—'}")
                tip = (f"这一段是引擎的 `{row.get('group')}` 组"
                       f"（共 {len(row.get('options') or [])} 个候选）。\n"
                       '左键 = 换下一个（点到底会回到「不写这一维」）· '
                       '**右键 = 搜索选择**\n'
                       '点它只改这一维，其余维度（服装/表情/…）原样保留。')
                # 这一维在美术需求表里的代号（`A` / `3`）也写进提示：
                # 卡片标题那个 `A7` 就是这两半拼起来的，点开能对上。
                kind = _slot_kind(row)
                half = code_of(self._vocabulary(), kind, raw, tag) if raw else ''
                if half:
                    tip = (f'美术需求表里这一维是「{half}」'
                           f'（{"服装代号" if kind == "costume" else "表情编号"}）。\n'
                           + tip)
                if raw and shown != raw:
                    tip = f'引擎里写的是 `{raw}`（屏幕上显示的是词汇表起的名字）。\n' + tip
                if not shown:
                    # ⚠ 空着的那一格要说清它**不是坏了**：这一拍自己没写这一维，
                    #   画面上按引擎的默认走；点一下才把它固定到这一拍。
                    tip = (f"这一拍**没写** `{row.get('group')}` 这一维"
                           f"（画面上按引擎的默认走，不是坏了）。\n"
                           f'⚠ 空**不等于**组里第一个词：不写就交给引擎的默认，'
                           f'写了就是把那一件钉死到这一拍。\n' + tip)
                btn.setToolTip(tip)
        else:
            if getattr(self, '_expr_btn', None) is None or self._slot_btns:
                self._rebuild_slot_chips([])
            self._expr_btn.setText(
                f'{self._fallback_label()}：'
                f'{self._say("expression", expr, tag) if expr else "—"}')
            self._expr_btn.setToolTip(
                '左键 = 换下一个候选 · **右键 = 搜索选择**'
                '（候选就是项目里真实的属性名）')
        # 位置**继承**自更早的拍（Ren'Py 的 `at` 是持续状态）时也要显示出来 ——
        # 用户 2026-10-03："明明说了在 i11，卡片上却是空的"。
        # 标一个「继承」是为了不让人以为"这一拍自己写了这个位置"：
        # 保存时**只写这一拍真正有的值**，点一下 chip 才会把它固定下来。
        if pos:
            self._pos_btn.setText(f'位置：{self._say("transform", pos)}')
            self._pos_btn.setToolTip(
                _raw_tip('transform', pos, self._vocabulary())
                + '这一拍自己写了位置。\n'
                  '左键 = 换下一个候选 · **右键 = 搜索选择**')
        elif inherited:
            self._pos_btn.setText(
                f'位置：{self._say("transform", inherited)}（继承）')
            self._pos_btn.setToolTip(
                f'这一拍没写位置，屏幕上沿用的是更早那一拍的 `at {inherited}`。\n'
                f'点一下即可把它固定到这一拍。\n'
                f'左键 = 换下一个候选 · **右键 = 搜索选择**')
        else:
            self._pos_btn.setText('位置：—')
            self._pos_btn.setToolTip('这一拍没有位置（也没人可继承）。\n'
                                     '左键 = 认第一个候选 · **右键 = 搜索选择**')
        self.setProperty('speaking', 'true' if self.item.get('speaking') else 'false')
        self.style().unpolish(self)
        self.style().polish(self)
        self._load_thumb(spec)
        # 判定提示条跟着这一张图走（换图 / 符号表晚到 / 撤销都要重判）
        self._refresh_hint()

    def _thumb_bg(self) -> str:
        """
        缩略图底色：跟**选择器里那个"底色"开关**同一个偏好。

        用户 2026-10-05："加一个背景切换颜色按钮，把白背景变成暗色背景，
        防止有的照片被挡住" —— 白底/透明底的立绘画在浅色卡片上同样看不见，
        所以卡片这边读同一个开关（一个开关，两个地方一起生效）。
        """
        try:
            from storygraph.ui_qt.asset_picker import _load_dark_pref
            return '#1e2024' if _load_dark_pref() else theme.t('bg.skeleton')
        except Exception as e:                     # noqa: BLE001 - 读不到就用主题色
            swallow('ui_qt.sprite_grid._thumb_bg', e)
            return theme.t('bg.skeleton')

    def _load_thumb(self, spec):
        if self._loader is None or not spec:
            self._thumb.setText('＋')
            self._thumb.setToolTip('')
            self._mark_missing(False)
            return
        try:
            self._thumb.setStyleSheet(
                f'background: {self._thumb_bg()};'
                f'border-radius: {T.RADIUS["control"]}px;')
        except Exception as e:                     # noqa: BLE001
            swallow('ui_qt.sprite_grid._load_thumb.bg', e)
        slot = ('card', id(self), spec)
        path = self._thumb_path(spec)
        tag = spec.split()[0]
        if not path:
            # ⚠ “这一格不是图像名”（`show expression …` / `screen` / `layer`）：
            #   预览判不了，**不许写“没有”** —— 那是在说项目缺图，而它压根
            #   不是按图像名解析的（用户 2026-11-05：“有的时候这些东西也不是
            #   图片”）。写清楚“判不了”，不猜。
            from storygraph.ui_qt.sprite_hints import _image_part_of_spec
            if not _image_part_of_spec(spec):
                self._thumb.setText('非图像')
                self._thumb.setToolTip(
                    f'{spec}\n'
                    f'（这一格不是图像名：`show expression` / `screen` / '
                    f'`layer` 这类形态，预览判不了 —— 不猜。）')
                self._mark_missing(False)
                return
            # ⚠ 判不了（项目还没打开）时**不许**说"没有" —— 那是假警报。
            #   这时照旧显示 tag 名（一眼看得出"还没解析"，不吓人）。
            if not self._assets_ready:
                self._thumb.setText(tag)
                self._thumb.setToolTip(
                    f'{spec}\n（还没打开项目 —— 打开项目之后才能判断这张图在不在）')
                self._mark_missing(False)
                return
            # ⭐ 用户要的那一条：**直接在立绘那里显示"没有"**（不弹窗）。
            self._thumb.setText('没有')
            self._thumb.setToolTip(
                f'找不到这张图：{spec}\n'
                f'（资源表里没有，脚本的 image 声明里也没有；'
                f'按 game/images/{tag}/ 也没猜出来）\n'
                f'检查文件名、或把它补进资源表 / 用 image 声明一下')
            self._mark_missing(True)
            return
        self._mark_missing(False)

        # 先给"正在加载"的可见反馈：立绘 1500×2200、CG 更大，解码要时间。
        # 用户 2026-10-03："有立绘时图片一开始没有显示" —— 以前这里只是
        # 显示一个标签名，看起来就像坏了；现在写明在加载什么。
        #
        # 这个"骨架"走独立的 `placeholder` 回调（2026-10-05）：以前它是
        # loader 用 `apply(None)` 触发的，而 `apply(None)` 在本卡里表示
        # "这张图读不出来" —— 于是图还在解的时候卡片就已经写着"可能损坏"了。
        # 真实项目里立绘是 4320×7680 / 9.4 MB，这一等是肉眼可见的。
        def loading():
            self._thumb.setText(f'{tag} …')
            self._thumb.setToolTip(f'正在加载：{path}')

        def apply(pixmap, _p):
            if pixmap is not None:
                self._thumb.setPixmap(pixmap)
                self._thumb.setToolTip(f'{spec}\n{path}')
                self._mark_missing(False)
            else:
                # ⚠ 路径找到了、图却读不出来（损坏 / 格式不支持）：这与"找不到"
                #   是**两件事**，提示必须分开（否则人会去改文件名，而问题在文件本身）。
                self._thumb.setText('读不出')
                self._thumb.setToolTip(
                    f'这张图读不出来（可能损坏或格式不支持）：\n{path}')
                self._mark_missing(True)
        self._mark_missing(False)
        self._thumb.setText(f'{tag} …')
        self._thumb.setToolTip(f'正在加载：{path}')
        self._loader.request(slot, str(path), apply,
                             max_size=(THUMB_W, THUMB_H),
                             placeholder=loading)

    def _mark_missing(self, missing: bool):
        """
        立绘格子的"**没有**"状态 —— 换底色 + 换个字符色，**不弹窗、不拦人**。

        ⚠ 为什么要有这个方法（不是直接 `setText`）：同一个"没有"要能被**清掉**。
        项目是后打开的（卡片先画出来），打开之后 `set_assets_ready(True)` 会重画；
        重画成功时这里把警告底色收回去 —— 否则一张好图会一直顶着红底。

        颜色一律走 `tokens` / `theme`（本仓不许在 UI 里写死颜色）。
        """
        self._missing = bool(missing)
        try:
            if missing:
                # ⚠ 用**警告**色（`state.warning`）而不是错误色：缺一张图
                #   不是"出错了、你干不了活"，而是"这里缺东西，看一眼"。
                self._thumb.setStyleSheet(
                    f"background: {theme.t('state.warningBg')};"
                    f"color: {theme.t('state.warning')};"
                    f"border: 1px dashed {theme.t('border.dashed')};"
                    f"border-radius: {T.RADIUS['control']}px;")
            else:
                self._thumb.setStyleSheet(
                    f"background: {theme.t('bg.skeleton')};"
                    f"border-radius: {T.RADIUS['control']}px;")
        except Exception as e:                     # noqa: BLE001 - 画不出警告色也别炸
            swallow('ui_qt.sprite_grid.mark_missing', e)

    def _thumb_path(self, spec):
        """立绘 image spec → 磁盘路径（规则在 `resolve_sprite_thumb_path`）。"""
        return resolve_sprite_thumb_path(
            spec, self._on_resolve_path,
            getattr(self._loader, 'project_root', None),
            max_size=sprite_compose_size())

    def _apply_image_spec(self, spec: str):
        """
        新的 image spec 落进这一格（**同步 `expression` 与 `image`**）。

        为什么必须同步（用户 2026-10-05 实测报"表情找不到 angry"）：卡片标题写的
        是 `item['image']`，而保存时走的是 `cards_to_show`（tag + `expression`）——
        两者不同步，就会"看到的和写下去的不是一回事"。

        ⚠ 换一维时**整串属性一起存**（`base_handdown gratified`），不拆：
        `cards_to_show` 本来就是逐词归一后原样写回，拆成两处存只会又出现
        "一处知道服装、另一处不知道"的旧问题。
        """
        parts = [p for p in str(spec or '').split() if p]
        if not parts:
            return
        self.item['image'] = ' '.join(parts)
        self.item['expression'] = ' '.join(parts[1:])
        self.item.pop('inherited', None)   # 动了它就归这一拍（看见 ≠ 写过）
        self._pick_note = self._pick_note_tip = ''   # 换过图了，上一条"换过"的说明作废
        self.refresh()
        if self._on_change is not None:
            self._on_change(self)

    def _cycle(self, field):
        """
        点 chip → 换下一个候选（表情 / 位置）。

        候选由 `app.beats.choices_for_image` 给出 —— 拿这一张卡自己的
        image spec 去问符号表，问不到就什么都不做。

        ⚠ **这张图有多条维度（引擎分组）时，`expression` 那一格由
        `_cycle_slot` 负责**（一维一格）—— 老路只认**单个**词，多词一定
        "当前值不在候选里" → 点一下静默无效（用户 2026-10-12 实测）。

        位置那一格有个特例：显示的是**继承**来的值（更早那一拍的 `at`），
        而候选表里往往没有这个写法（项目自定义 transform 如 `i11`）。
        这时第一次点 = **把它固定到这一拍**（值不变，但从此写进这一拍），
        而不是硬跳到候选表的第一项把位置改掉。
        """
        if field == 'expression':
            rows = self._slot_rows()
            if rows:
                self._cycle_slot(str(rows[0].get('group') or ''))
                return
        opts = choices_for_image(str(self.item.get('image') or ''),
                                 self._symbols)
        choices = list(opts.get(field) or [])
        cur = str(self.item.get(field) or '').strip()
        if not cur and field == 'position':
            cur = str(self.item.get('inherited_at') or '').strip()
        if not cur and not choices:
            return
        # 当前值不在候选表里（项目自定义的写法，如 `i11`）：
        # 点一下 = 把它**固定到这一拍**（值不变，但从此会写进这一拍）。
        # 顺序很重要：候选表为空时也要能固定，所以这一支放在"没有候选就返回"之前。
        if cur and cur not in choices:
            self.item[field] = cur
            self.item.pop('inherited', None)   # 动了它就归这一拍（看见 ≠ 写过）
            self.refresh()
            if self._on_change is not None:
                self._on_change(self)
            return
        if not choices:
            return
        # **没有当前值时点一下 = 认第一个候选**（2026-10-05 用户实测报：
        # "之前这个角色没有登场，位置无法选择设定和调整，整个都是空的，
        # 也不知道会放哪儿"）。
        #
        # 全新加的那张立绘 `position == ''`，而下一行原本写的是
        # `choices.index(cur)` —— 空串不在候选里 → **ValueError** → 异常在 Qt
        # 槽里被吞掉 → 用户看到的就是"点了没反应"（表情 chip 同理：新卡的
        # `expression` 也是空的）。这次改动让 chip 从"第一项"开始往前走。
        if not cur:
            nxt = choices[0]
        else:
            nxt = choices[(choices.index(cur) + 1) % len(choices)]
        self._set_field(field, nxt)

    def _set_field(self, field, value):
        """
        把某一格的值定下来（**左键换下一个、右键从列表里挑**共用这一条路）。

        表情那格还要**同步图像名**（卡片标题显示的就等于会写进脚本的）：

        用户 2026-10-05 实测报："还有就是这个表情，找不到 angry"：卡片标题写着
        `ling angry`（那是 `item['image']`），而表情 chip 被点成了
        `disappointed`（`item['expression']`）—— 保存时 `cards_to_show`
        用 **tag + 表情** 拼图像名，于是脚本里写 `ling disappointed`，
        **卡片却还显示 angry**：看得见的和写下去的不是一回事。

        ⚠ 存**归一后的属性**，不是 chip 上那个人话词 —— 直接拼会把中文写进
        图像名（`ling 无语`），而脚本里写的是 `ling base_arms_akimbo_sigh`
        （`test_ui_qt` 当场抓到）。
        """
        self.item[field] = value
        self.item.pop('inherited', None)   # 动了它就归这一拍（看见 ≠ 写过）
        if field == 'expression':
            tag = str(self.item.get('image') or '').split()
            if tag:
                attrs = normalize_expression_words(
                    str(value), str(self.item.get('image') or ''), self._symbols)
                # ⚠ **别在这里丢维度**（2026-10-12 实测的现场）：
                #   `show ye base_handdown gratified` 挑一个 `angry` →
                #   从这里拼出 `ye angry`，**抱臂姿势被静默吃掉**。
                #   所以只把 `expression` 交给这条老路时，`image` 的其余词要留住：
                #   多词 spec 一律由 `_apply_image_spec` / `apply_attr_slot` 走
                #   "一维一格"那条路（见 `_cycle` / `_pick_from_list`）。
                self.item['image'] = ' '.join([tag[0]] + attrs) if attrs \
                    else tag[0]
        self._pick_note = self._pick_note_tip = ''   # 用户自己改了值：上一条说明作废
        self.refresh()
        if self._on_change is not None:
            self._on_change(self)

    def _pick_from_list(self, field):
        """
        **右键 chip → 可搜索的候选列表**（用户 2026-10-05：

        > 既然变换如此多，那为什么不加个右键列表筛选跟搜索功能呢？

        真实项目的位置候选有 151 个（146 个自定义 transform + 内置位置），
        靠"点一下换下一个"要按 40 次才轮到 `i11`。这里弹一个"输入即筛"的
        弹窗（见 `ui_qt.choice_picker`），候选与左键那条路**同一份**
        （`choices_for_image`）—— 两条路不可能给出不一样的名字。

        **分组**（用户 2026-10-10）：项目在 `rules.json` 的 `attr_groups`
        里声明了候选分栏时，这里会在列表里插组标题（`服装` / `表情`）。
        没声明 → 与从前一模一样（一个平铺列表）。

        **多条维度时**（2026-10-12）：候选是**完整路径**（`base_handdown
        gratified`），按服装分栏 —— 因为只给一个词根本没法和另一个维度组合；
        选中的是整串，直接落成 `image`（其余维度随选中项一起，不会丢）。
        """
        from storygraph.ui_qt.choice_picker import choose_from
        if field == 'expression':
            rows = self._slot_rows()
            if rows:
                self._pick_slot_combo(rows)
                return
        choices = list((choices_for_image(str(self.item.get('image') or ''),
                                          self._symbols).get(field) or []))
        if not choices:
            return
        label = '表情' if field == 'expression' else '位置'
        # **位置**显示人话（`i11` → 「1人，近处」），选出来仍是 `i11`；
        # 表情候选是项目自己的词（`project_words`），那边已经是人说得出的话了。
        if field == 'position':
            choices = labeled(choices, self._vocabulary(), 'transform')
        groups = list(self._groups_for(field))
        value = choose_from(self,
                            f'选择{label} —— 共 {len(choices)} 个候选'
                            + (f'（{len(groups)} 组）' if groups else ''),
                            choices, str(self.item.get(field) or ''),
                            groups=groups)
        if value:
            self._set_field(field, value)

    def _pick_slot_combo(self, rows: list):
        """
        **多维度**图的右键候选：把每一维的候选**合成完整 spec** 再分栏。

        实测（sr_general）：`ye` 有 `outfit`(2) × `expressions`(18) →
        36 条，按服装分两栏。这比"给一堆单词、挑一个就把别的维度吃掉"有用；
        也给了一条把作者手写的组合**原样点回来**的路。

        2026-11-02 补两处（用户"看不出来是 A3 / 点了就回不到空"）：每条候选
        前面挂**美术代号**（`A7｜base_hand_up angry`），每一栏顶上多一条
        **「✕ 不写这一维」**。拼法与显示在 `sprite_codes_view.slot_choices`，
        这里只负责问数据和弹框。
        """
        from storygraph.ui_qt.choice_picker import choose_from
        spec = str(self.item.get('image') or '').strip()
        tag = (spec.split() or [''])[0]
        if not tag:
            return
        grouped = slot_choices(spec, tag, rows, self._vocabulary(),
                               lambda g, v: self._slot_apply(spec, g, v))
        if not grouped:
            return
        value = choose_from(
            self,
            f"选择立绘组合 —— {sum(len(g['values']) for g in grouped)} 条"
            f"（{len(grouped)} 组）",
            [c for g in grouped for c in g['values']], spec, groups=grouped)
        if value:
            self._apply_image_spec(value)

    def _groups_for(self, field) -> list:
        """
        这一格要显示的**分组候选**（没有就返回空 → 平铺）。

        ⚠ 分组值可能依赖"这张卡的 tag"（同一个词在不同角色下的显示名不同），
        所以由窗口注入的 `choice_groups_for(tag)` **现取**，而不是提前算好一份
        缓存 —— 缓存了就会出现"切了角色、下拉还是上一个人的词"。
        """
        if field != 'expression':
            return []                        # 目前只有属性（表情/服装）要分栏
        fn = self._choice_groups_for
        if fn is None:
            return []
        tag = str(self.item.get('tag') or '').strip()
        if not tag:
            spec = str(self.item.get('image') or '').strip().split()
            tag = spec[0] if spec else ''
        try:
            return list(fn(tag) or [])
        except Exception as e:               # noqa: BLE001 - 分组拿不到就平铺
            from storygraph.core.safeguard import swallow
            swallow('ui_qt.sprite_grid._groups_for', e)
            return []

    def mouseDoubleClickEvent(self, event):
        self.clicked.emit(self)
        super().mouseDoubleClickEvent(event)


class SpriteGridWidget(QWidget):
    """立绘网格 + 末尾「＋ 添加立绘」。

    **列数随可用宽度自适应**（2026-10-30，用户反馈第 5 条「UI 宽窄可变」）
    ------------------------------------------------------------------
    以前是写死 4 列（`_cols = 4`），后果是一张卡片的最小宽 = 4 列卡片的宽：
    右栏只有 420px 时，**2 张立绘卡就把卡片最小宽顶到 519px** —— 横向滚动条
    必然出现，而右端恰恰是「选择…」按钮。用户看到的就是"背景那几格要拖到
    最右边才看得见"。

    现在：列数 = f(可用宽度)（`_cols_for_width`，1~4 列），窄栏自动变 1 列竖排，
    宽栏仍然 4 列。同时把布局的"最小宽约束"关掉（`QLayout.SetNoConstraint`）
    并让 `minimumSizeHint()` 只报**一列卡片**的宽 —— 否则宽栏时（4 列布局）
    那个最小宽会反过来把右栏顶宽，横向滚动条又回来了。
    """

    add_requested = Signal()
    change = Signal(object)           # 任意一张卡被改过（通知主窗口重投影）
    #: 选择器**拒绝**了这张图 / 别的"没写成"的事，但卡片里没有地方摆 →
    #: 抛给主窗口去写控制台 + 状态栏（**不许静默什么都不做**）。
    notice = Signal(str)

    #: 最多排几列（宽屏上限）。再多就留白，不把卡片拉得稀稀拉拉。
    MAX_COLS = 4

    def __init__(self, symbols=None, loader=None, parent=None,
                 on_pick_image=None, on_pick_image_result=None,
                 on_open_mapping=None):
        super().__init__(parent)
        self._symbols = symbols
        self._loader = loader
        #: **老回调**：`current -> image spec`（`window_assets._pick_image`）。
        self._on_pick_image = on_pick_image
        #: **新回调**：`current -> PickResult | str`。多带"预览图被换成了哪张真立绘"
        #: 这类结论，卡片提示条要用（见 `show_pick_hint`）。两个都给时**优先它**。
        self._on_pick_image_result = on_pick_image_result
        #: 「去『资源映射』修」的入口（主窗口注入；None = 不显示那个按钮）
        self._on_open_mapping = on_open_mapping
        #: 这个入口要不要"带上这一条 spec"（见 `_accepts_one_argument`）。
        self._mapping_wants_spec = _accepts_one_argument(on_open_mapping)
        #: `image spec → 真实图片路径` 的解析器（主窗口注入；真源是资源表）
        self._on_resolve_path = None
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setSpacing(T.SPACE['sm'])
        # ⚠ 列数自适应 ⇒ **不许**让布局把"当前列数的最小宽"写成控件的最小宽
        # （默认的 SetDefaultConstraint 会这么做：4 列时 808px，正好把右栏顶宽）。
        # 最小宽由本类的 `minimumSizeHint()` 显式给"一列卡片"的宽度。
        self._grid.setSizeConstraint(QLayout.SetNoConstraint)
        self._cards: list = []
        #: 当前列数（**由 `_sync_cols()` 按宽度算出来**；测试与探针读它）
        self._cols = 1
        #: 重排防重入（`resizeEvent` → `_layout()` → 布局失效 → 又一次 resize）
        self._relayouting = False
        #: 项目声明的候选分栏（空 = 平铺；见 `set_choice_groups`）
        self._choice_groups: dict = {}
        #: `tag -> 分组候选`（窗口注入；见 `set_choice_groups_provider`）
        self._choice_groups_for = None
        #: 「现在判得了吗」（见 `set_assets_ready`）—— 传给每一张卡
        self._assets_ready = False

    # ------------------------------------------------------------------

    def set_assets_ready(self, ready: bool):
        """
        项目打开 / 资源表读完之后说一声：**现在判得了"这张图在不在"了**。

        用户 2026-10-13 要的"找不到就在立绘那里显示没有"必须建立在
        "判得了"之上 —— 项目还没打开时谁也判不了，那时把每张立绘写成
        "没有"是**假警报**（`SpriteCardWidget._load_thumb` 里那条分支）。
        """
        self._assets_ready = bool(ready)
        for card in self._cards:
            card.set_assets_ready(self._assets_ready)

    def set_choice_groups(self, groups):
        """
        项目声明的**候选分栏** → 每张卡。

        用户 2026-10-10："实际上需要分组显示……因为我这个要求通用！" ——
        所以这里只递数据：分不分栏由**项目声明**决定；空 = 平铺。
        """
        self._choice_groups = dict(groups or {})
        for card in self._cards:
            card.set_choice_groups(self._choice_groups)

    def set_choice_groups_provider(self, fn):
        """
        注入 `tag -> [{'group','label','values'}]`（分组值可能依赖 tag 的
        显示名，所以**现取**；见 `SpriteCardWidget._groups_for`）。

        ⚠ 换完**要重画一遍卡片**：provider 上挂着词汇表（美术代号 `A7` 就是
        从那儿来的）。只换引用不重画，用户刚在「🗂 资源映射」里存完映射，
        卡片上还是老样子 —— 那正是 2026-11-02 报的"映射归映射"。
        """
        self._choice_groups_for = fn
        for card in self._cards:
            card._choice_groups_for = fn
            try:
                card.refresh()
            except Exception as e:               # noqa: BLE001 - 一张卡重画失败不算错
                swallow('ui_qt.sprite_grid.set_choice_groups_provider', e)

    def set_symbols(self, symbols):
        """
        符号表（项目打开 / 重读资源表后到）→ 自己 + **已画出来的每张卡**。

        从前只换自己那份、卡片还捧着旧的：于是"首词是不是角色 tag"必须翻页
        才重新判一次，看起来像时灵时不灵。
        """
        self._symbols = symbols
        for card in self._cards:
            card.set_symbols(symbols)

    def set_path_resolver(self, resolver):
        """
        注入"立绘 → 图片路径"的解析器，并让已有卡片重取缩略图。

        为什么要有这个钩子：`symbols` 里**没有**资源表，光靠它取不到
        路径（早期写法就是这么静默失败的）。路径的真源是资源表的
        `Path` 列，而那个只有主窗口/会话拿得到。
        """
        self._on_resolve_path = resolver
        for card in self._cards:
            card._on_resolve_path = resolver
            card.refresh()

    def cards(self) -> list:
        return [c.item for c in self._cards]

    def prefetch(self, items: list, limit: int = 8):
        """
        后台**预取**几拍之后的立绘缩略图（不画任何卡）。

        用户 2026-10-05：

        > 现在先优化之前提到的……卡牌界面图片加载慢的问题

        真实项目的立绘是 4320×7680 / 9.4 MB 的 PNG：**冷解码约 350~600 ms**
        （磁盘缓存命中后 ~1 ms）。翻拍时每张新脸都要现解，一卡就是半秒。
        所以主窗口在看这一拍时，把**相邻几拍**的图先解进 loader 的缓存 ——
        等真翻过去，`request` 直接命中内存缓存，0 延迟。

        路径与尺寸都走**卡片自己那两条**（`resolve_sprite_thumb_path` +
        `sprite_thumb_size`），否则缓存键对不上，预取等于白做。
        `limit` 卡住"一次最多预取几张"，免得把线程池全占了、反把当前这一拍
        挤到后面。
        """
        loader = self._loader
        if loader is None or not items:
            return
        root = getattr(loader, 'project_root', None)
        seen = set()
        for it in items:
            if len(seen) >= max(0, int(limit)):
                break
            spec = str((it or {}).get('image') or '').strip()
            if not spec or spec in seen:
                continue
            seen.add(spec)
            path = resolve_sprite_thumb_path(
                spec, self._on_resolve_path, root,
                max_size=sprite_compose_size())
            if path:
                loader.prefetch(str(path), max_size=sprite_thumb_size())

    def load(self, items: list):
        """items：卡 item 列表（见模块 docstring 的数据契约）。"""
        for c in self._cards:
            c.setParent(None)
            c.deleteLater()
        self._cards = []
        for it in items:
            self._cards.append(self._new_card(it))
        # 先按**当前宽度**定列数再排（否则窄栏第一帧会按 4 列排一次，
        # 多一次可见的重排；宽栏同理）。
        self._sync_cols()
        self._layout()

    def _new_card(self, item: dict):
        """造一张卡并接好三条回调（**唯一入口**：`load` 与「＋ 添加立绘」共用）。"""
        card = SpriteCardWidget(
            item, symbols=self._symbols, loader=self._loader,
            on_change=self._on_card_change,
            on_resolve_path=self._on_resolve_path,
            choice_groups=self._choice_groups,
            choice_groups_for=self._choice_groups_for,
            assets_ready=self._assets_ready,
            on_open_mapping=self._on_open_mapping)
        card.remove_requested.connect(self._on_remove)
        card.clicked.connect(self._on_click)
        return card

    # ---------------- 列数自适应 ----------------

    def _cols_for_width(self, avail: int) -> int:
        """
        可用宽度 → 列数（1~`MAX_COLS`）。

        算法：扣掉网格左右边距，每列按 `CARD_W + spacing` 排，能放几列算几列。
        `avail<=0`（还没布局）时返回 1 —— **宁可先竖排**，也别按 4 列把右栏顶宽。
        """
        gap = max(0, int(self._grid.spacing()))
        margins = self._grid.contentsMargins()
        inner = int(avail) - margins.left() - margins.right()
        if inner <= 0:
            return 1
        cols = (inner + gap) // max(1, CARD_W + gap)
        return max(1, min(int(self.MAX_COLS), int(cols)))

    def _sync_cols(self) -> bool:
        """按当前宽度重算列数；**变了才返回 True**（调用方决定要不要重排）。"""
        if getattr(self, '_grid', None) is None:   # 构造期（布局还没建）
            return False
        width = int(self.width())
        if width <= 0:
            return False
        cols = self._cols_for_width(width)
        if cols == self._cols:
            return False
        self._cols = cols
        return True

    def minimumSizeHint(self) -> QSize:           # noqa: N802 - Qt 接口
        """
        最小宽 = **一列卡片**（+边距）。高度仍交给布局（要能纵向滚）。

        为什么必须覆盖：列数是按宽度算的，布局内部的"当前列数最小宽"是**结果**
        而不是**约束**。不覆盖的话，宽栏下 4 列布局会把 808px 写成卡片的
        最小宽 —— 右栏又被迫变宽，横向滚动条在**窗口变宽**时反而出现。
        """
        hint = super().minimumSizeHint()
        grid = getattr(self, '_grid', None)
        if grid is None:                          # 构造期（还没建布局）
            return hint
        margins = grid.contentsMargins()
        return QSize(CARD_W + margins.left() + margins.right(), hint.height())

    def resizeEvent(self, event):                 # noqa: N802 - Qt 接口
        super().resizeEvent(event)
        if getattr(self, '_relayouting', False):
            return
        if self._sync_cols():
            self._layout()

    def _layout(self):
        if getattr(self, '_relayouting', False):
            return
        self._relayouting = True
        try:
            while self._grid.count():
                w = self._grid.takeAt(0).widget()
                if w is not None:
                    w.setParent(None)
            cols = max(1, int(self._cols))
            for i, card in enumerate(self._cards):
                self._grid.addWidget(card, i // cols, i % cols)
            add = QPushButton('＋ 添加立绘')
            add.setStyleSheet(
                f"border: 2px dashed {theme.t('border.dashed')};"
                f"border-radius: {T.RADIUS['card']}px; padding: 30px 8px;"
                f"color: {theme.t('text.tertiary')};")
            add.clicked.connect(self._on_add)
            self._grid.addWidget(add, len(self._cards) // cols,
                                 len(self._cards) % cols)
        finally:
            self._relayouting = False

    # ------------------------------------------------------------------

    def _pick(self, current: str = '') -> tuple:
        """
        问主窗口要一张立绘 → `(spec, PickResult | None)`（取消 = `('', None)`）。

        **有"带结论"的回调就优先它**：提示条要 `replaced_from/to/message`，
        老回调只有一个字符串 —— 那只能悄悄写、没法说"你选的被换成了谁"，
        正是本轮要消灭的形态。
        """
        if self._on_pick_image_result is not None:
            return pick_spec_and_result(self._on_pick_image_result(current))
        if self._on_pick_image is not None:
            return pick_spec_and_result(self._on_pick_image(current))
        return '', None

    def _has_picker(self) -> bool:
        return (self._on_pick_image_result is not None
                or self._on_pick_image is not None)

    def _refuse(self, result):
        """选择器拒绝 / 没给出可写 spec → **说出来**，一格都不写。"""
        reason = str(getattr(result, 'message', '') or '').strip()
        if not reason:
            reason = '这张图不能加进立绘（选择器没给出可写进脚本的图像名）。'
        self.notice.emit(reason)

    # ------------------------------------------------------------------

    def _on_add(self):
        if not self._has_picker():
            self.add_requested.emit()
            self.change.emit(self)                  # 老行为：没有选择器也通知一声
            return
        spec, result = self._pick('')
        if result is not None and not getattr(result, 'ok', True):
            self._refuse(result)
            return
        if not spec:
            return                                  # 用户取消：什么都不发生
        card = self._new_card(
            {'image': spec, 'expression': '', 'position': '', 'at': '',
             'speaking': False, 'order_index': len(self._cards),
             'extra_rows': []})
        self._cards.append(card)
        self._layout()
        if result is not None:
            card.show_pick_hint(result)             # 换过图 / 有话说 → 摆出来
        self.change.emit(self)

    def _on_remove(self, card):
        """
        右上角那颗按钮：**删卡** / **退场** / **撤销退场** 三件事。

        沿用卡不拿掉 —— 角色是上一拍 show 上来的，光把卡删掉什么都不会写
        （用户 2026-11-08："一旦变成沿用的……就没法删了"）。语义在
        `ui_qt.sprite_hide.toggle`，这里只管重画与提示。
        """
        item = card.item if isinstance(card.item, dict) else {}
        how = SH.toggle(item)
        if how:
            card.refresh()
            if how == 'hide':
                tag = SH.tag_of(item)
                self.notice.emit(
                    f'「{tag}」这一拍退场（保存后写出 `hide {tag}`）—— '
                    f'再点 ↺ 可以撤销。')
            self.change.emit(self)
            return
        if card in self._cards:
            self._cards.remove(card)
            card.setParent(None)
            card.deleteLater()
            self._layout()
            self.change.emit(self)

    def _on_click(self, card):
        if not self._has_picker():
            return
        spec, result = self._pick(card.item.get('image') or '')
        if result is not None and not getattr(result, 'ok', True):
            self._refuse(result)                    # 拒绝：**保留**这一格原样
            return
        if not spec:
            return
        # 把新图的属性同步进表情，并**把几条维度重新算一遍**
        # （`_apply_image_spec` → `refresh` → `_slot_rows`），
        # 于是换图之后 chip 个数跟着新图的分组走。
        card._apply_image_spec(spec)
        if result is not None:
            card.show_pick_hint(result)             # `_apply_image_spec` 会清旧提示 → 再摆新的
        self.change.emit(self)

    def _on_card_change(self, _card):
        self.change.emit(self)
