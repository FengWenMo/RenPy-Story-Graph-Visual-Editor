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

from storygraph.app.beats import choices_for_image, uniform_sprite_position
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
                 parent=None, on_change=None, on_resolve_path=None):
        super().__init__(parent)
        self.item = dict(item or {})
        self._symbols = symbols
        self._loader = loader
        self._on_change = on_change
        self._on_resolve_path = on_resolve_path
        self.setProperty('role', 'spriteCard')
        self._build()
        self.refresh()

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
        self._expr_btn = QPushButton()
        self._expr_btn.setProperty('variant', 'chip')
        self._expr_btn.setStyleSheet(
            'padding:2px 6px;font-size:8pt;border-radius:8px;')
        self._expr_btn.clicked.connect(lambda: self._cycle('expression'))
        self._pos_btn = QPushButton()
        self._pos_btn.setStyleSheet(
            'padding:2px 6px;font-size:8pt;border-radius:8px;')
        self._pos_btn.clicked.connect(lambda: self._cycle('position'))
        chips.addStretch(1)
        chips.addWidget(self._expr_btn)
        chips.addWidget(self._pos_btn)
        chips.addStretch(1)
        lay.addLayout(chips)
        lay.addStretch(1)

    # ------------------------------------------------------------------

    def refresh(self):
        spec = str(self.item.get('image') or '').strip()
        self._name.setText(spec if spec else '（点击换图）')
        expr = str(self.item.get('expression') or '').strip()
        pos = str(self.item.get('position') or '').strip()
        inherited = str(self.item.get('inherited_at') or '').strip()
        self._expr_btn.setText(f'表情：{expr or "—"}')
        # 位置**继承**自更早的拍（Ren'Py 的 `at` 是持续状态）时也要显示出来 ——
        # 用户 2026-10-03："明明说了在 i11，卡片上却是空的"。
        # 标一个「继承」是为了不让人以为"这一拍自己写了这个位置"：
        # 保存时**只写这一拍真正有的值**，点一下 chip 才会把它固定下来。
        if pos:
            self._pos_btn.setText(f'位置：{pos}')
            self._pos_btn.setToolTip('这一拍自己写了位置')
        elif inherited:
            self._pos_btn.setText(f'位置：{inherited}（继承）')
            self._pos_btn.setToolTip(
                f'这一拍没写位置，屏幕上沿用的是更早那一拍的 `at {inherited}`。\n'
                f'点一下即可把它固定到这一拍。')
        else:
            self._pos_btn.setText('位置：—')
            self._pos_btn.setToolTip('这一拍没有位置（也没人可继承）')
        self.setProperty('speaking', 'true' if self.item.get('speaking') else 'false')
        self.style().unpolish(self)
        self.style().polish(self)
        self._load_thumb(spec)

    def _load_thumb(self, spec):
        if self._loader is None or not spec:
            self._thumb.setText('＋')
            self._thumb.setToolTip('')
            return
        slot = ('card', id(self), spec)
        path = self._thumb_path(spec)
        tag = spec.split()[0]
        if not path:
            self._thumb.setText(tag)
            self._thumb.setToolTip(
                f'找不到这张图：{spec}\n'
                f'（资源表里没有，按 game/images/{tag}/ 也没猜出来；'
                f'检查文件名或补进资源表）')
            return

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
            else:
                self._thumb.setText(tag)
                self._thumb.setToolTip(f'这张图读不出来（可能损坏或格式不支持）：\n{path}')
        self._thumb.setText(f'{tag} …')
        self._thumb.setToolTip(f'正在加载：{path}')
        self._loader.request(slot, str(path), apply,
                             max_size=(THUMB_W, THUMB_H),
                             placeholder=loading)

    def _thumb_path(self, spec):
        """立绘 image spec → 磁盘路径（规则在 `resolve_sprite_thumb_path`）。"""
        return resolve_sprite_thumb_path(
            spec, self._on_resolve_path,
            getattr(self._loader, 'project_root', None))

    def _cycle(self, field):
        """
        点 chip → 换下一个候选（表情 / 位置）。

        候选由 `app.beats.choices_for_image` 给出 —— 拿这一张卡自己的
        image spec 去问符号表，问不到就什么都不做。

        位置那一格有个特例：显示的是**继承**来的值（更早那一拍的 `at`），
        而候选表里往往没有这个写法（项目自定义 transform 如 `i11`）。
        这时第一次点 = **把它固定到这一拍**（值不变，但从此写进这一拍），
        而不是硬跳到候选表的第一项把位置改掉。
        """
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
        nxt = choices[(choices.index(cur) + 1) % len(choices)]
        self.item[field] = nxt
        self.refresh()
        if self._on_change is not None:
            self._on_change(self)

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

    # ------------------------------------------------------------------

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
                                    on_resolve_path=self._on_resolve_path)
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
                on_resolve_path=self._on_resolve_path))
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
                card.item['image'] = spec
                # 把新图的属性同步进表情（与 tkinter 版 `_sync_expression_from_spec` 同语义）
                parts = spec.split()
                if len(parts) > 1:
                    card.item['expression'] = ' '.join(parts[1:])
                card.refresh()
                self.change.emit(self)

    def _on_card_change(self, _card):
        self.change.emit(self)
