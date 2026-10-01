"""
storygraph.ui_qt.asset_picker
=============================
**资产选择器**（PySide6）—— 对齐用户最在意的那一句：

> 资产列是**下拉框**，不用手打 ID

左：类别列表（立绘：<角色> / 背景 / CG / 音乐 / 音效 / 语音 / 变换）
中：候选网格（真缩略图，走 QThreadPool 异步解码）
右：选中项的详情（transform 会显示它的 ATL 与注释）
上：搜索框（前缀 / 子串 / 模糊，规则来自 `score_candidates` 唯一真源）

语义全在 `storygraph.app.picker`，这里只画控件、收事件。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem as _Item,
                               QSplitter, QVBoxLayout, QWidget)

from storygraph.app import picker as P
from storygraph.ui import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.thumb_loader import ThumbLoader

THUMB = (104, 74)


class AssetPickerDialog(QDialog):
    """
    选一条资产。`selection` 是 `(asset_type, asset_id)`，取消时为 `None`。

    `want_image_spec=True` 时（选立绘/背景图用），确认后 `image_spec`
    给出可写进卡片的 `show` 片段（`ling happy`），否则给资产 ID。
    """

    def __init__(self, parent=None, registry=None, project_root=None,
                 current: str = '', types=None, want_image_spec: bool = False,
                 title: str = '选择资产'):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 620)
        theme.apply_to(self)

        self._registry = registry
        self._project_root = project_root
        self._types = list(types) if types else None
        self._want_spec = bool(want_image_spec)
        self.selection = None
        self.image_spec = ''
        self._filter = ''
        self._loader = ThumbLoader(self, max_size=THUMB,
                                   project_root=project_root)
        self._build()
        self._load_groups()
        self._preselect(current)

    # ------------------------------------------------------------------

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])

        top = QHBoxLayout()
        top.setSpacing(T.SPACE['sm'])
        lab = QLabel('搜索')
        lab.setProperty('role', 'caption')
        top.addWidget(lab, 0)
        self._search = QLineEdit()
        self._search.setPlaceholderText('输入 ID 前缀 / 片段（留空显示全部）')
        self._search.textChanged.connect(self._on_search)
        top.addWidget(self._search, 1)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        # —— 左：类别 ——
        self._cats = QListWidget()
        self._cats.setMaximumWidth(220)
        self._cats.currentItemChanged.connect(lambda *_: self._load_items())
        split.addWidget(self._cats)
        # —— 中：候选 ——
        self._items = QListWidget()
        self._items.setViewMode(QListWidget.IconMode)
        self._items.setIconSize(self._items.iconSize())
        self._items.setResizeMode(QListWidget.Adjust)
        self._items.setMovement(QListWidget.Static)
        self._items.setSpacing(6)
        self._items.setWordWrap(True)
        self._items.currentItemChanged.connect(lambda *_: self._show_detail())
        self._items.itemDoubleClicked.connect(lambda *_: self.accept())
        split.addWidget(self._items)
        # —— 右：详情 ——
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(T.SPACE['sm'], 0, 0, 0)
        rl.setSpacing(T.SPACE['xs'])
        cap = QLabel('详情')
        cap.setProperty('role', 'section')
        rl.addWidget(cap)
        self._detail = QLabel('（选中一条看详情）')
        self._detail.setWordWrap(True)
        self._detail.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._detail.setProperty('role', 'note')
        rl.addWidget(self._detail, 1)
        right.setMinimumWidth(200)
        right.setMaximumWidth(300)
        split.addWidget(right)
        split.setSizes([200, 460, 240])
        root.addWidget(split, 1)

        self._hint = QLabel('')
        self._hint.setProperty('role', 'caption')
        root.addWidget(self._hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('确定')
        buttons.button(QDialogButtonBox.Ok).setProperty('variant', 'primary')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------
    #  数据
    # ------------------------------------------------------------------

    def _load_groups(self):
        self._groups = P.groups(self._registry, project_root=self._project_root)
        self._cats.clear()
        item_all = _Item('全部')
        item_all.setData(Qt.UserRole, None)
        self._cats.addItem(item_all)
        for name in self._groups:
            it = _Item(name)
            it.setData(Qt.UserRole, name)
            self._cats.addItem(it)
        if self._cats.count():
            self._cats.setCurrentRow(0)

    def _current_group(self):
        it = self._cats.currentItem()
        return it.data(Qt.UserRole) if it is not None else None

    def _pairs(self) -> list:
        """当前该显示的 `(asset_type, asset_id)` 列表。"""
        group = self._current_group()
        if self._filter:
            pairs = P.search(self._registry, self._filter, limit=300,
                             project_root=self._project_root)
        elif group is None:
            pairs = [(t, a) for v in self._groups.values() for (t, a) in v]
        else:
            pairs = list(self._groups.get(group) or [])
        if self._types:
            pairs = [(t, a) for (t, a) in pairs if t in self._types]
        return pairs

    def _load_items(self):
        pairs = self._pairs()
        self._items.clear()
        for atype, aid in pairs:
            it = _Item(aid)
            it.setData(Qt.UserRole, (atype, aid))
            it.setToolTip(P.detail(self._registry, atype, aid,
                                   self._project_root)[:400])
            self._items.addItem(it)
            if atype == 'image':
                self._request_thumb(it, aid)
        n = len(pairs)
        self._hint.setText(f'{n} 条' + ('（搜索中）' if self._filter else ''))

    def _request_thumb(self, item, asset_id):
        path = P.thumbnail_path(self._registry, 'image', asset_id,
                                self._project_root)
        if not path:
            return
        slot = ('picker', id(item), asset_id)

        def apply(pixmap, _p):
            if pixmap is not None:
                item.setIcon(pixmap)

        self._loader.request(slot, str(path), apply, max_size=THUMB)

    def _show_detail(self):
        it = self._items.currentItem()
        if it is None:
            self._detail.setText('（选中一条看详情）')
            return
        atype, aid = it.data(Qt.UserRole)
        text = P.detail(self._registry, atype, aid, self._project_root)
        self._detail.setText(f'[{atype}] {aid}\n\n{text or "（无更多信息）"}')

    def _on_search(self, text):
        self._filter = (text or '').strip()
        self._load_items()

    def _preselect(self, current: str):
        """尽力把当前值选上（让用户知道"原来是哪个"）。"""
        current = (current or '').strip()
        if not current:
            return
        head = current.split()[0]
        for i in range(self._items.count()):
            it = self._items.item(i)
            _, aid = it.data(Qt.UserRole)
            if aid == current or aid == head or aid.split('_')[0] == head:
                self._items.setCurrentItem(it)
                self._items.scrollToItem(it)
                return

    # ------------------------------------------------------------------

    def accept(self):
        it = self._items.currentItem()
        if it is None:
            self._hint.setText('还没选中任何一条。')
            return
        atype, aid = it.data(Qt.UserRole)
        self.selection = (atype, aid)
        if self._want_spec and atype == 'image':
            row = P.row_of(self._registry, atype, aid, self._project_root)
            self.image_spec = P.image_spec_of(aid, row)
        else:
            self.image_spec = aid
        super().accept()

    def closeEvent(self, event):
        try:
            self._loader.shutdown()
        except Exception:                    # noqa: BLE001
            pass
        super().closeEvent(event)


# ----------------------------------------------------------------------

def pick_image(parent=None, registry=None, current: str = '',
               project_root=None) -> str:
    """
    选一张**立绘/背景图**，返回可写进卡片的 image spec（取消返回 ''）。

    这是主窗口与立绘网格直接调用的入口。
    """
    if registry is None:
        return ''
    dlg = AssetPickerDialog(parent, registry=registry,
                            project_root=project_root, current=current,
                            types=['image'], want_image_spec=True,
                            title='选择立绘 / 背景图')
    if dlg.exec() == QDialog.Accepted:
        return dlg.image_spec
    return ''


def pick_asset(parent=None, registry=None, asset_type: str = '',
               current: str = '', project_root=None) -> str:
    """选一条资产（音频/变换等），返回资产 ID（取消返回 ''）。"""
    dlg = AssetPickerDialog(parent, registry=registry,
                            project_root=project_root, current=current,
                            types=[asset_type] if asset_type else None,
                            want_image_spec=False,
                            title='选择资产')
    if dlg.exec() == QDialog.Accepted and dlg.selection:
        return dlg.selection[1]
    return ''


__all__ = ['AssetPickerDialog', 'pick_image', 'pick_asset']
