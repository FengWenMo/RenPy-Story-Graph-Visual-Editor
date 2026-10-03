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

from storygraph.core.safeguard import swallow
from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem as _Item,
                               QSplitter, QVBoxLayout, QWidget)

from storygraph.app import picker as P
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.thumb_loader import ThumbLoader

THUMB = (104, 74)
#: 每格的尺寸（缩略图 + 文字位）。**条目也必须按它设 sizeHint** ——
#: 只设 `setGridSize` 是不够的，见 `_load_items` 里的说明。
CELL = QSize(THUMB[0] + 26, THUMB[1] + 44)


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
        # 调度模型见 `ui_qt/thumb_loader.py`：**优先级队列**（自限流），
        # 选择器用 `focus()` 把"窗口内可见的"提到最前。
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
        # —— 中：候选（**严格网格**）——
        #
        # 用户 2026-10-04：
        # > 你现在这个东西图片界面你看看……好混乱啊！……连字都混一起了，
        # > 能不能整齐一点……？
        #
        # 乱的原因全在这个列表的配置上：IconMode 默认**不固定格子**，
        # 图标尺寸又随图片长宽比变化，文字还会自动折行 —— 于是大图挤掉
        # 小图的位置、文字叠在下一行的图上。现在：
        #   · `setGridSize` 固定每格大小 → 行列必然对齐；
        #   · `setUniformItemSizes(True)` → 滚动条与布局不跳；
        #   · 图标统一缩放到同一个框（THUMB）→ 不再有大有小；
        #   · 文字**不折行、超长中间省略** → 不会溢出格子。
        self._items = QListWidget()
        self._items.setViewMode(QListWidget.IconMode)
        self._items.setIconSize(QSize(THUMB[0], THUMB[1]))
        self._items.setGridSize(CELL)
        self._items.setSpacing(4)
        self._items.setUniformItemSizes(True)
        self._items.setWordWrap(False)
        self._items.setTextElideMode(Qt.ElideMiddle)
        self._items.setResizeMode(QListWidget.Adjust)
        self._items.setMovement(QListWidget.Static)
        self._items.setSelectionMode(QListWidget.SingleSelection)
        self._items.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._items.currentItemChanged.connect(lambda *_: self._show_detail())
        self._items.itemDoubleClicked.connect(lambda *_: self.accept())
        # 滚动 → 补加载可见项的缩略图（只加载看得见的，见 `_load_visible_thumbs`）
        self._items.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._requested: set = set()
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
        # 分类组 + 音频/变换/角色各自的整组（`groups()` 只含图片那套分类，
        # 少了这一层，选音乐时列表里会一条都没有 —— 见 `groups_with_plain`）。
        self._groups = P.groups_with_plain(self._registry, types=self._types,
                                           project_root=self._project_root)
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
        # 去重保序：不同分组可能给出同一条（"全部"是各组的并集）
        seen = set()
        uniq = []
        for pair in pairs:
            if pair in seen:
                continue
            seen.add(pair)
            uniq.append(pair)
        return uniq

    def _load_items(self):
        pairs = self._pairs()
        self._items.clear()
        self._requested = set()
        # 候选整个换掉了：之前"还排在队里、没开始解"的那些不再需要
        # （正在解的会跑完并进缓存，切回去就是命中）。
        try:
            self._loader.clear_pending()
        except Exception:                     # noqa: BLE001
            swallow('asset_picker._load_items')
        for atype, aid in pairs:
            # 显示**短名**：图片显示它对应的 image spec（`bg bg9_5` /
            # `ling happy`）——那才是写进卡片的值，比内部 ID
            # （`sr_general_Frame 26`）好认得多；其余类型显示资产 ID。
            # 完整信息（ID / 路径 / 属性）在 tooltip 里，不挤在格子里。
            it = _Item(self._short_label(atype, aid))
            it.setData(Qt.UserRole, (atype, aid))
            # **条目矩形必须等于格子**（用户 2026-10-05：
            # 「想要选中图片，点击后没反应，显示并没有选中任何一条」）。
            #
            # 只设 `setGridSize` 是**不够**的：`uniformItemSizes(True)` 时
            # Qt 拿**第一条**的 sizeHint 当全网格的统一尺寸，而这条的
            # sizeHint 是委托在"图标还没到（异步解码）"时算出来的 ——
            # 只有文字那么大（实测 44×19，而格子是 130×118）。后果有两个：
            #
            #   1. 图片**画在条目矩形外**，点在图片上 `indexAt` 命中无效
            #      → 选了没反应、确定时"还没选中任何一条"；
            #   2. `_load_visible_thumbs` 按格子高算可见范围，而实际每行只有
            #      19px → 真正看得见的那些格子**永远不被请求** → "部分照片
            #      未加载，缺口大"。
            #
            # 显式设 sizeHint 之后两件事一起好了：命中区 = 整个格子，
            # 行高 = 真实行高。
            it.setSizeHint(CELL)
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            it.setToolTip(P.detail(self._registry, atype, aid,
                                   self._project_root)[:400])
            self._items.addItem(it)
        n = len(pairs)
        self._hint.setText(f'{n} 条' + ('（搜索中）' if self._filter else ''))
        # **只给看得见的格子请求缩略图**
        #
        # 用户 2026-10-05：
        # > 照片加载缓慢，部分照片未加载，缺口大
        #
        # 以前这里对**全部**候选（真实项目 2828 张）一次性 `request()` ——
        # 线程池被灌满、可见的那几个排在几千个后面，于是"半天不出图 + 一堆空格子"。
        self._load_visible_thumbs()

    def _visible_range(self, extra_rows: int = 3, pad_rows: int = 1) -> tuple:
        """
        当前视口覆盖的**条目下标区间**（含上下各多留几行）。

        为什么用"滚动位置 ÷ 格子高"算，而不是 `indexAt` 命中测试：早先那个
        网格配置下 `indexAt` 恒返回无效索引（=-1），补加载永远只覆盖第一屏。
        现在条目 sizeHint = 格子（见 `_load_items`），行高与格子高一致，
        这个算式就是准的 —— 用它是因为它**不依赖视图已经布局完**
        （首屏 `_load_items` 时还没画过）。

        `extra_rows` / `pad_rows`：`_visible_range()` 默认带"上面 1 行、下面
        3 行"的余量（滚一点就能立刻看到图）；`_focus_range()` 要的是**严格
        落在视口里**的那些（它们才是"优先加载"的对象）。
        """
        n = self._items.count()
        if not n:
            return 0, -1
        cell_h = max(1, CELL.height())
        cell_w = max(1, CELL.width())
        cols = max(1, self._items.viewport().width() // cell_w)
        top = self._items.verticalScrollBar().value()
        exact_first = max(0, top // cell_h)
        exact_rows = self._items.viewport().height() // cell_h + 1
        first_row = max(0, exact_first - max(0, int(pad_rows)))
        rows = exact_rows + max(0, int(pad_rows)) + max(0, int(extra_rows))
        i0 = first_row * cols
        i1 = min(n - 1, i0 + rows * cols)
        return max(0, i0), i1

    def _focus_range(self) -> tuple:
        """**严格在视口内**的下标区间 —— 这些格子优先出图。"""
        return self._visible_range(extra_rows=0, pad_rows=0)

    def _load_visible_thumbs(self):
        """
        给当前**视口内**（上下各多留一行）的格子请求缩略图。

        滚动时由 `valueChanged` 触发（带 60ms 防抖，快速拖动滚动条不会
        把中间掠过的几百项都排进队列）。已经请求过的不重复请求
        （`self._requested`）——否则来回滚动会反复解码同一批图。

        用户 2026-10-05：

        > 这个选择器内图片的加载还是不够迅速，优先加载目前在窗口内的图片！

        所以最后一步把**严格在视口里**的那些 slot 交给 `loader.focus()`：
        它们排到最前，滚过去的那些降回普通（仍然会解完，只是不挡路）。
        """
        if not self._items.count():
            return
        i0, i1 = self._visible_range()
        f0, f1 = self._focus_range()
        focus_slots = []
        for i in range(i0, i1 + 1):
            it = self._items.item(i)
            if it is None or it.icon() is not None and not it.icon().isNull():
                continue
            atype, aid = it.data(Qt.UserRole)
            if atype != 'image':
                continue
            if aid not in self._requested:
                self._requested.add(aid)
                self._request_thumb(it, aid)
            if f0 <= i <= f1:
                # ⚠ **不管这一格是这次新请求的、还是前几次滚过去时就排上队的**，
                # 只要它现在在窗口里、又还没有图，就要提到最前。
                # 实测（真实项目立绘分组）：按"只提这次新请求的"写，
                # 90% 位置那一屏的 48..63 全都已经被前几屏的"多留几行"
                # 提前请求过了 → `focus([])` 等于**谁都没提**，
                # 那一屏还是要等 69 个任务解完（3.5 秒）。
                focus_slots.append(self._slot_of(it, aid))
        self._loader.focus(focus_slots)

    def _on_scroll(self, *_):
        """滚动 → 稍后补加载可见项的缩略图（防抖）。"""
        if not hasattr(self, '_thumb_timer'):
            self._thumb_timer = QTimer(self)
            self._thumb_timer.setSingleShot(True)
            self._thumb_timer.timeout.connect(self._load_visible_thumbs)
        self._thumb_timer.start(60)

    def _short_label(self, atype, aid) -> str:
        """
        候选在格子里的短名（认得出就行；详情看 tooltip 与右侧面板）。

        图片优先显示它对应的 **image spec**（`ling happy`）——那才是写进卡片的
        值，比内部 ID 好认。但真实项目里有 `Tag == Attributes`（两栏都填了
        `Frame 13`）的行，拼出来会是 `Frame 13 Frame 13`：这里把**整段重复**
        折掉一半（只折叠"前后半完全相同"这种，不做通用去重，免得误伤
        `ling happy happy` 之类真的写法）。
        """
        if atype != 'image':
            return str(aid)
        try:
            row = P.row_of(self._registry, atype, aid, self._project_root)
            spec = P.image_spec_of(aid, row) if row else ''
        except Exception as e:                     # noqa: BLE001
            swallow('ui_qt.asset_picker._short_label', e)
            spec = ''
        spec = str(spec or '').strip()
        words = spec.split()
        half = len(words) // 2
        if half and words[:half] == words[half:]:
            spec = ' '.join(words[:half])
        return spec or str(aid)

    @staticmethod
    def _slot_of(item, asset_id):
        """一格对应的 loader slot（请求与 `focus()` 必须用同一个）。"""
        return ('picker', id(item), asset_id)

    def _request_thumb(self, item, asset_id):
        """请求一张缩略图；返回它的 slot，没有图源时也返回 slot（只是不入队）。"""
        slot = self._slot_of(item, asset_id)
        path = P.thumbnail_path(self._registry, 'image', asset_id,
                                self._project_root)
        if not path:
            return slot

        def apply(pixmap, _p):
            if pixmap is not None:
                item.setIcon(pixmap)

        self._loader.request(slot, str(path), apply, max_size=THUMB)
        return slot

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
            # 只说事实 + 怎么补救（用户 2026-10-05 遇到的是"点了图片却没选中"，
            # 那时候的根因是条目矩形只有文字那么大 —— 见 `_load_items`；
            # 剩下"真的没点"的情况，这里把动作说清楚）。
            self._hint.setText('还没选中任何一条 —— 先在网格里点一下图片，'
                               '再按「确定」。')
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
            swallow('asset_picker.closeEvent')
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
