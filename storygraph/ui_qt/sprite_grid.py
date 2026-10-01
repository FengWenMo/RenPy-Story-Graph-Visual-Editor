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
from storygraph.ui import tokens as T
from storygraph.ui_qt import theme

CARD_W = T.CARD['w'] + 26            # 卡片宽（含 chip 行）
THUMB_W = T.CARD['thumbW'] + 8
THUMB_H = T.CARD['thumbH'] + 8


def uniform_position_of(cards: list) -> str:
    """只有"所有立绘位置一致"才返回它（否则整拍位置留空 —— 防立绘错位）。"""
    rows = [{'at': (str(c.get('position') or '').strip()
                    or str(c.get('at') or '').strip())} for c in cards]
    return uniform_sprite_position(rows)


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
        self._expr_btn.setText(f'表情：{expr or "—"}')
        self._pos_btn.setText(f'位置：{pos or "—"}')
        self.setProperty('speaking', 'true' if self.item.get('speaking') else 'false')
        self.style().unpolish(self)
        self.style().polish(self)
        self._load_thumb(spec)

    def _load_thumb(self, spec):
        if self._loader is None or not spec:
            self._thumb.setText('＋')
            return
        slot = ('card', id(self), spec)
        path = self._thumb_path(spec)
        if not path:
            self._thumb.setText(spec.split()[0])
            return

        def apply(pixmap, _p):
            if pixmap is not None:
                self._thumb.setPixmap(pixmap)
            else:
                self._thumb.setText(spec.split()[0])
        self._loader.request(slot, str(path), apply,
                             max_size=(THUMB_W, THUMB_H))

    def _thumb_path(self, spec):
        """
        立绘 image spec → 磁盘上的真实图片路径。

        为什么不能只看 `symbols`：`symbols`（`make_symbols` 的结果）
        **没有** `registry` 属性，所以早期写法
        `getattr(self._symbols, 'registry', None)` 恒为 None，
        → 缩略图永远加载不出来（卡片只剩名字，静默失效）。

        现在由主窗口注入 `on_resolve_path`（真源是资源表的 `Path` 列）；
        没注入时退回"按 Ren'Py 的 images 目录约定猜一下"，
        猜不到就显示名字 —— **降级可见，不假装成功**。
        """
        if self._on_resolve_path is not None:
            try:
                return self._on_resolve_path(spec) or None
            except Exception:                # noqa: BLE001
                return None
        return self._guess_image_path(spec)

    def _guess_image_path(self, spec):
        """兜底：`ling happy` → `<项目>/game/images/ling/happy.png`（逐个试）。"""
        parts = [p for p in str(spec or '').split() if p]
        if len(parts) < 2:
            return None
        root = getattr(self._loader, 'project_root', None)
        if not root:
            return None
        rel = '/'.join([parts[0], '_'.join(parts[1:])])
        try:
            from storygraph.story.excel_export import resolve_thumb_source
            for cand in (f'game/images/{rel}.png', f'game/images/{rel}.webp',
                         f'images/{rel}.png', f'{rel}.png'):
                found = resolve_thumb_source(cand, root)
                if found:
                    return found
        except Exception:                    # noqa: BLE001
            return None
        return None

    def _cycle(self, field):
        """
        点 chip → 换下一个候选（表情 / 位置）。

        候选由 `app.beats.choices_for_image` 给出 —— 拿这一张卡自己的
        image spec 去问符号表，问不到就什么都不做。
        """
        opts = choices_for_image(str(self.item.get('image') or ''),
                                 self._symbols)
        choices = list(opts.get(field) or [])
        cur = str(self.item.get(field) or '').strip()
        if not choices:
            return
        if cur in choices:
            nxt = choices[(choices.index(cur) + 1) % len(choices)]
        else:
            nxt = choices[0]
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
