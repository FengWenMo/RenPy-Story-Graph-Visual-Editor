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

**card item 的数据契约**（与 tkinter 版一致）：
  {image: str, expression: str, position: str, at: str, speaking: bool,
   order_index: int, extra_rows: list}
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

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

from storygraph.core.safeguard import swallow

CARD_W = T.CARD['w'] + 26            # 卡片宽（含 chip 行）
THUMB_W = T.CARD['thumbW'] + 8
THUMB_H = T.CARD['thumbH'] + 8


def sprite_thumb_size() -> tuple:
    """卡片上立绘缩略图的解码尺寸（**唯一的真源**）。"""
    return (THUMB_W, THUMB_H)


def resolve_sprite_thumb_path(spec, resolver=None, project_root=None):
    """
    立绘 image spec → 磁盘上的真实图片路径（卡片与预取共用这一份）。

    两级查找，**第二级是关键**：

      1. 主窗口注入的解析器（`session.path_of_image`）—— 真源是资源表的
         `Path` 列，最准；
      2. 它查不到时**退回按 Ren'Py 的目录约定猜**
         （`<tag> <attrs>` → `game/images/<tag>/<attrs>.png`）。

    为什么必须有第 2 级：CG 这类图**往往不在资源表里**（实测
    `sr_general`：`cg03.png` 在磁盘上，但 2828 条图片记录里一条 cg 都没有）。
    以前一拿到 None 就放弃，`_guess_image_path` 永远走不到 ——
    于是"CG 很难出现"，而立绘（在表里）正常，看起来像"只有 CG 坏了"。
    """
    spec = str(spec or '').strip()
    if not spec:
        return None
    if resolver is not None:
        try:
            found = resolver(spec)
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.sprite_grid.resolve_sprite_thumb_path', e)
            found = None
        if found:
            return found
    parts = [p for p in spec.split() if p]
    if len(parts) < 2 or not project_root:
        return None
    rel = '/'.join([parts[0], '_'.join(parts[1:])])
    try:
        from storygraph.story.excel_export import resolve_thumb_source
        for cand in (f'game/images/{rel}.png', f'game/images/{rel}.webp',
                     f'images/{rel}.png', f'{rel}.png'):
            found = resolve_thumb_source(cand, project_root)
            if found:
                return found
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.sprite_grid.resolve_sprite_thumb_path', e)
        return None
    return None


class SpriteCardWidget(QFrame):
    """一张立绘卡：缩略图 + 名字 + 表情 chip + 位置 chip + ✕。"""

    clicked = Signal(object)          # self
    remove_requested = Signal(object)

    def __init__(self, item: dict, symbols=None, loader=None,
                 parent=None, on_change=None, on_resolve_path=None,
                 choice_groups=None, choice_groups_for=None,
                 assets_ready: bool = False):
        super().__init__(parent)
        self.item = dict(item or {})
        self._symbols = symbols
        self._loader = loader
        self._on_change = on_change
        self._on_resolve_path = on_resolve_path
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
            return
        self._expr_btn = None
        for row in rows:
            group = str(row.get('group') or '')
            btn = self._new_expr_btn()
            btn.clicked.connect(lambda _=False, g=group: self._cycle_slot(g))
            self._slot_lay.addWidget(btn)
            self._slot_btns.append(btn)
        self._slot_box.show()

    def _slot_rows(self) -> list:
        """
        这一格的**引擎分组行**（一维一格）；拿不到就 `[]`（→ 老路）。

        ⚠ **不新增注入通道**：窗口已经把 `set_choice_groups_provider(_for_tag)`
        接好了，`_for_tag` 上挂着 `attr_groups` / `facts` / `engine_facts` 三个
        属性（见 `window_card._sync_choice_groups`）。这里只读它们 —— 少一个方法
        就少一处"忘了转发"的机会（`main_window` 的转发层就踩过这个坑）。
        """
        provider = self._choice_groups_for
        if provider is None:
            return []
        spec = str(self.item.get('image') or '').strip()
        if not spec:
            return []
        facts = getattr(provider, 'engine_facts', None) or {}
        groups = getattr(provider, 'attr_groups', None) or []
        # ⚠ 2026-10-16：没有引擎事实时，`attr_slot_rows` 要退回项目自己的
        #   `layeredimage` —— 那条路**必须知道项目根**，所以窗口把它挂在
        #   provider 上（`window_card._sync_choice_groups`）。从前这里不传，
        #   于是"没采过引擎事实"的项目永远退回单格（用户报的"粘在一起了"）。
        layer_model = getattr(provider, 'layer_model', None)
        if layer_model is None:
            root = getattr(provider, 'project_root', None)
            if root:
                try:
                    from storygraph.story.layered_model import (
                        load_layer_model_cached)
                    layer_model = load_layer_model_cached(root)
                except Exception as e:       # noqa: BLE001 - 读不到就走老路
                    swallow('ui_qt.sprite_grid._slot_rows.layers', e)
                    layer_model = None
        try:
            return list(attr_slot_rows(spec, self._symbols, groups=groups,
                                       facts=facts,
                                       layer_model=layer_model) or [])
        except Exception as e:               # noqa: BLE001 - 分组拿不到就走老路
            swallow('ui_qt.sprite_grid._slot_rows', e)
            return []

    def _cycle_slot(self, group: str):
        """点某一维的 chip → **只换那一维的词**，其它维度原样保留。"""
        rows = self._slot_rows()
        hit = next((r for r in rows if str(r.get('group') or '') == str(group or '')), None)
        if hit is None:
            return
        opts = list(hit.get('options') or [])
        cur = str(hit.get('value') or '')
        if not opts:
            return
        # 当前值不在候选里（项目自定义写法）→ 第一次点 = **固定住它**（值不变），
        # 与位置 chip 同一条规矩：绝不悄悄改掉一个我们认不出的值。
        if cur and cur not in opts:
            self._set_slot_value(group, cur)
            return
        if not cur:
            nxt = opts[0]
        else:
            nxt = opts[(opts.index(cur) + 1) % len(opts)]
        self._set_slot_value(group, nxt)

    def _set_slot_value(self, group: str, value: str):
        """某一维换成 `value` → 合成新的 `image`（其余维度不丢）→ 通知窗口重投影。"""
        spec = str(self.item.get('image') or '').strip()
        provider = self._choice_groups_for
        facts = getattr(provider, 'engine_facts', None) or {}
        groups = getattr(provider, 'attr_groups', None) or []
        layer_model = getattr(provider, 'layer_model', None)
        if layer_model is None and getattr(provider, 'project_root', None):
            try:
                from storygraph.story.layered_model import (
                    load_layer_model_cached)
                layer_model = load_layer_model_cached(provider.project_root)
            except Exception as e:           # noqa: BLE001 - 读不到就按老路
                swallow('ui_qt.sprite_grid._set_slot_value.layers', e)
                layer_model = None
        try:
            new = apply_attr_slot(spec, group, value, symbols=self._symbols,
                                  groups=groups, facts=facts,
                                  layer_model=layer_model)
        except Exception as e:               # noqa: BLE001 - 认不出就别改
            swallow('ui_qt.sprite_grid._set_slot_value', e)
            return
        if new and new != spec:
            self._apply_image_spec(new)

    # ------------------------------------------------------------------

    def refresh(self):
        spec = str(self.item.get('image') or '').strip()
        self._name.setText(spec if spec else '（点击换图）')
        expr = str(self.item.get('expression') or '').strip()
        pos = str(self.item.get('position') or '').strip()
        inherited = str(self.item.get('inherited_at') or '').strip()
        # 一维一格（引擎分组）；只有一条维度时 `rows` 为空 → 回到单个表情格。
        rows = self._slot_rows()
        if rows and len(rows) != len(self._slot_btns):
            # 维度个数变了（换图 / 引擎事实重采）→ **先重建再写字**，
            # 否则新造的按钮会空着（第一版就踩了：先 zip 后 rebuild）。
            self._rebuild_slot_chips(rows)
        if rows:
            for btn, row in zip(self._slot_btns, rows):
                shown = str(row.get('display') or row.get('value') or '')
                btn.setText(
                    f"{row.get('label') or row.get('group') or '属性'}："
                    f"{shown or '—'}")
                tip = (f"这一段是引擎的 `{row.get('group')}` 组"
                       f"（共 {len(row.get('options') or [])} 个候选）。\n"
                       '左键 = 换下一个 · **右键 = 搜索选择**\n'
                       '点它只改这一维，其余维度（服装/表情/…）原样保留。')
                if not shown:
                    # ⚠ 空着的那一格要说清它**不是坏了**：这一拍自己没写这一维，
                    #   画面上按引擎的默认走；点一下才把它固定到这一拍。
                    tip = (f"这一拍**没写** `{row.get('group')}` 这一维"
                           f"（画面上按引擎的默认走，不是坏了）。\n" + tip)
                btn.setToolTip(tip)
        else:
            if getattr(self, '_expr_btn', None) is None or self._slot_btns:
                self._rebuild_slot_chips([])
            self._expr_btn.setText(f'表情：{expr or "—"}')
            self._expr_btn.setToolTip(
                '左键 = 换下一个候选 · **右键 = 搜索选择**'
                '（候选就是项目里真实的属性名）')
        # 位置**继承**自更早的拍（Ren'Py 的 `at` 是持续状态）时也要显示出来 ——
        # 用户 2026-10-03："明明说了在 i11，卡片上却是空的"。
        # 标一个「继承」是为了不让人以为"这一拍自己写了这个位置"：
        # 保存时**只写这一拍真正有的值**，点一下 chip 才会把它固定下来。
        if pos:
            self._pos_btn.setText(f'位置：{pos}')
            self._pos_btn.setToolTip('这一拍自己写了位置。\n'
                                     '左键 = 换下一个候选 · **右键 = 搜索选择**')
        elif inherited:
            self._pos_btn.setText(f'位置：{inherited}（继承）')
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
            getattr(self._loader, 'project_root', None))

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
        """
        from storygraph.ui_qt.choice_picker import choose_from
        spec = str(self.item.get('image') or '').strip()
        tag = (spec.split() or [''])[0]
        if not tag:
            return
        facts = getattr(self._choice_groups_for, 'engine_facts', None) or {}
        groups = getattr(self._choice_groups_for, 'attr_groups', None) or []
        grouped = []
        for row in rows:
            vals = []
            for opt in (row.get('options') or ()):
                try:
                    full = apply_attr_slot(spec, row.get('group'), opt,
                                           symbols=self._symbols,
                                           groups=groups, facts=facts)
                except Exception as e:        # noqa: BLE001
                    swallow('ui_qt.sprite_grid._pick_slot_combo', e)
                    continue
                if full and full not in vals:
                    vals.append(full)
            if vals:
                grouped.append({'group': str(row.get('group') or ''),
                                'label': str(row.get('label') or ''), 'values': vals})
        if not grouped:
            return
        value = choose_from(
            self,
            f"选择立绘组合 —— {sum(len(g['values']) for g in grouped)} 条"
            f"（{len(grouped)} 组）",
            [v for g in grouped for v in g['values']], spec, groups=grouped)
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
    """立绘网格 + 末尾「＋ 添加立绘」。"""

    add_requested = Signal()
    change = Signal(object)           # 任意一张卡被改过（通知主窗口重投影）

    def __init__(self, symbols=None, loader=None, parent=None,
                 on_pick_image=None):
        super().__init__(parent)
        self._symbols = symbols
        self._loader = loader
        self._on_pick_image = on_pick_image
        #: `image spec → 真实图片路径` 的解析器（主窗口注入；真源是资源表）
        self._on_resolve_path = None
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setSpacing(T.SPACE['sm'])
        self._cards: list = []
        self._cols = 4
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
        """
        self._choice_groups_for = fn
        for card in self._cards:
            card._choice_groups_for = fn

    def set_symbols(self, symbols):
        self._symbols = symbols

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
            path = resolve_sprite_thumb_path(spec, self._on_resolve_path, root)
            if path:
                loader.prefetch(str(path), max_size=sprite_thumb_size())

    def load(self, items: list):
        """items：卡 item 列表（见模块 docstring 的数据契约）。"""
        for c in self._cards:
            c.setParent(None)
            c.deleteLater()
        self._cards = []
        for it in items:
            card = SpriteCardWidget(it, symbols=self._symbols,
                                    loader=self._loader,
                                    on_change=self._on_card_change,
                                    on_resolve_path=self._on_resolve_path,
                                    choice_groups=self._choice_groups,
                                    choice_groups_for=self._choice_groups_for,
                                    assets_ready=self._assets_ready)
            card.remove_requested.connect(self._on_remove)
            card.clicked.connect(self._on_click)
            self._cards.append(card)
        self._layout()

    def _layout(self):
        while self._grid.count():
            w = self._grid.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
        for i, card in enumerate(self._cards):
            self._grid.addWidget(card, i // self._cols, i % self._cols)
        add = QPushButton('＋ 添加立绘')
        add.setStyleSheet(
            f"border: 2px dashed {theme.t('border.dashed')};"
            f"border-radius: {T.RADIUS['card']}px; padding: 30px 8px;"
            f"color: {theme.t('text.tertiary')};")
        add.clicked.connect(self._on_add)
        self._grid.addWidget(add, len(self._cards) // self._cols,
                             len(self._cards) % self._cols)

    # ------------------------------------------------------------------

    def _on_add(self):
        if self._on_pick_image is not None:
            spec = self._on_pick_image('')
            if not spec:
                return
            self._cards.append(SpriteCardWidget(
                {'image': spec, 'expression': '', 'position': '', 'at': '',
                 'speaking': False, 'order_index': len(self._cards),
                 'extra_rows': []},
                symbols=self._symbols, loader=self._loader,
                on_change=self._on_card_change,
                on_resolve_path=self._on_resolve_path,
                assets_ready=self._assets_ready))
            self._cards[-1].remove_requested.connect(self._on_remove)
            self._cards[-1].clicked.connect(self._on_click)
            self._layout()
        else:
            self.add_requested.emit()
        self.change.emit(self)

    def _on_remove(self, card):
        if card in self._cards:
            self._cards.remove(card)
            card.setParent(None)
            card.deleteLater()
            self._layout()
            self.change.emit(self)

    def _on_click(self, card):
        if self._on_pick_image is not None:
            spec = self._on_pick_image(card.item.get('image') or '')
            if spec:
                # 把新图的属性同步进表情，并**把几条维度重新算一遍**
                # （`_apply_image_spec` → `refresh` → `_slot_rows`），
                # 于是换图之后 chip 个数跟着新图的分组走。
                card._apply_image_spec(spec)
                self.change.emit(self)

    def _on_card_change(self, _card):
        self.change.emit(self)
