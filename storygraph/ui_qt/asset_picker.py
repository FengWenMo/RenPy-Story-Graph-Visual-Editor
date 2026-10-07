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
from PySide6.QtCore import QPoint, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem as _Item,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from storygraph.app import picker as P
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.thumb_loader import (PRIORITY_BACKGROUND, ThumbLoader)

THUMB = (104, 74)
#: 每格的尺寸（缩略图 + 文字位）。**条目也必须按它设 sizeHint** ——
#: 只设 `setGridSize` 是不够的，见 `_load_items` 里的说明。
CELL = QSize(THUMB[0] + 26, THUMB[1] + 44)

#: 底色偏好（浅色 / 深色）存在这里 —— 用户切一次就该记住。
SETTINGS_ORG = 'storygraph'
SETTINGS_APP = 'editor'
SETTING_DARK = 'picker/dark_tiles'

#: 后台补齐：每次推进多少条 / 间隔多久（只在没有更要紧的请求时跑）。
FILL_CHUNK = 40
FILL_INTERVAL_MS = 220
#: 一次打开最多补齐多少条（避免为了"全绿"把 CPU 烧几分钟）。
FILL_BUDGET = 600


def _settings():
    """
    底色偏好的存储。

    ⚠ **做成可替换的工厂**（测试里换成临时 ini 文件）：

      Windows 上 `QSettings.setPath(IniFormat, ...)` + `setDefaultFormat`
      **不一定生效** —— 实测 `QSettings('storygraph','editor')` 仍然落在
      注册表 `HKEY_CURRENT_USER\\Software\\storygraph\\editor`。测试直接建
      对话框就会读写**用户真实的偏好**（把"深色"写进用户的配置）。
      所以这里留一个注入点，测试用 `monkeypatch.setattr(
      asset_picker, 'settings_factory', ...)` 换成临时文件。
    """
    return settings_factory()


def settings_factory():
    """默认存储：系统偏好（注册表 / ini 由 Qt 决定）。"""
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def _load_dark_pref() -> bool:
    """读"暗色底"偏好（读不到就用 False = 浅色）。"""
    try:
        return bool(_settings().value(SETTING_DARK, False, type=bool))
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.asset_picker._load_dark_pref', e)
        return False


def _save_dark_pref(dark: bool):
    try:
        _settings().setValue(SETTING_DARK, bool(dark))
    except Exception as e:                        # noqa: BLE001
        swallow('ui_qt.asset_picker._save_dark_pref', e)


class AssetPickerDialog(QDialog):
    """
    选一条资产。`selection` 是 `(asset_type, asset_id)`，取消时为 `None`。

    `want_image_spec=True` 时（选立绘/背景图用），确认后 `image_spec`
    给出可写进卡片的 `show` 片段（`ling happy`），否则给资产 ID。
    """

    #: 预热缩略图的进度 / 结果 —— **必须定义在类上**：工作线程 emit，
    #: Qt 会把它排队回这个对象所属的线程（GUI 线程），槽里才敢碰控件。
    warm_progress = Signal(int, int)
    warm_done = Signal(object)

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
        self._warming = False
        self.warm_progress.connect(self._on_prewarm_progress)
        self.warm_done.connect(self._on_prewarm_done)
        # 调度模型见 `ui_qt/thumb_loader.py`：**优先级队列**（自限流），
        # 选择器用 `focus()` 把"窗口内可见的"提到最前。
        self._loader = ThumbLoader(self, max_size=THUMB,
                                   project_root=project_root)
        self._build()
        self._load_groups()
        self._preselect(current)
        # 底色偏好要**开局就生效**（构造时 `toggled` 不会触发，
        # 不显式调一次的话"记住的深色"要等用户再点一下才出现）。
        self._on_bg_toggled(self._dark_tiles)

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
        # **底色切换**（用户 2026-10-05：
        # "先加一个背景切换颜色按钮，把白背景变成暗色背景，防止有的照片被挡住"）
        #
        # 很多立绘/背景是"白底 / 透明底"，画在浅色面板上等于看不见。
        # 切一次就记住（QSettings），下次打开还是这个底色。
        self._dark_tiles = _load_dark_pref()
        self._bg_btn = QPushButton('◐ 底色：浅色')
        self._bg_btn.setCheckable(True)
        self._bg_btn.setChecked(self._dark_tiles)
        self._bg_btn.setToolTip(
            '切换缩略图底色（浅色 ⇄ 深色）。\n'
            '白底 / 透明底的图在浅色上会被"挡住"，切深色就看得见。')
        self._bg_btn.toggled.connect(self._on_bg_toggled)
        top.addWidget(self._bg_btn, 0)
        # **预热缩略图**（用户 2026-10-05："另外，可以做预热缩略图"）——
        # 冷解码 384 ms/张 vs 缓存命中 2 ms，所以"先把这一批解好"是要紧事。
        # 跑在后台线程，进度写在提示条上；关掉窗口就停（已解好的留在缓存里）。
        self._warm_btn = QPushButton('⚡ 预热缩略图')
        self._warm_btn.setToolTip(
            '把这个项目所有立绘/CG 的缩略图**先解一遍**写进缓存。\n'
            '冷解码 ~400ms/张，之后任何界面都是 ~2ms 命中。\n'
            '可以随时关窗口中止（已解好的不会白费）。')
        self._warm_btn.clicked.connect(self.start_prewarm)
        top.addWidget(self._warm_btn, 0)
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
        #: 已经**拿到图**的资产（提示条上的"已加载 N"；重复回填不重复计数）
        self._loaded: set = set()
        #: `asset_id → 真实文件路径`（'' = 没有文件）—— 避免反复 stat 磁盘
        self._path_cache: dict = {}
        self._no_image = 0
        self._hint_total = 0
        self._fill_cursor = 0
        self._fill_deadline = 0
        self._fill_timer = None
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
    #  底色（浅色 / 深色）
    # ------------------------------------------------------------------

    #: 深色底（用户要的"暗色背景"）与配套的文字色。
    #: ⚠ 浅色那一边**必须用真实存在的 token**：`theme.t('bg.panel')` 不存在，
    #: `theme.t` 对未知键返回哨兵色 `#FF00FF`（刻意刺眼），拿它当底色就是
    #: 一片洋红。这里用 `bg.canvas`（面板底色）+ `text.primary`。
    DARK_BG = '#1e2024'
    DARK_FG = '#dfe3e8'

    def _on_bg_toggled(self, dark: bool):
        self._dark_tiles = bool(dark)
        self._bg_btn.setText('◐ 底色：深色' if self._dark_tiles else '◐ 底色：浅色')
        _save_dark_pref(self._dark_tiles)
        self._apply_tile_bg()

    def tile_background(self) -> str:
        """当前网格底色（自检/测试用）。"""
        return self.DARK_BG if self._dark_tiles else theme.t('bg.canvas')

    def tile_foreground(self) -> str:
        """当前网格文字色（深底必须配浅字，否则"换个方向看不见"）。"""
        return self.DARK_FG if self._dark_tiles else theme.t('text.primary')

    def _apply_tile_bg(self):
        """
        把底色画到网格上。

        为什么用 stylesheet 而不是调色板：`QListWidget` 的 item 选中色/文字色
        也要一起改，否则深底 + 深色文字＝更看不见（等于把问题换个方向）。
        """
        bg = self.tile_background()
        fg = self.tile_foreground()
        accent = theme.t('brand.primary')
        try:
            self._items.setStyleSheet(
                f'QListWidget{{background:{bg};color:{fg};}}'
                f'QListWidget::item{{color:{fg};}}'
                f'QListWidget::item:selected{{background:{accent};color:#ffffff;}}')
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.asset_picker._apply_tile_bg', e)

    # ------------------------------------------------------------------
    #  预热缩略图
    # ------------------------------------------------------------------

    def start_prewarm(self):
        """
        后台**把这个项目所有图片的缩略图先解一遍**（写进磁盘缓存）。

        用户 2026-10-05：

        > 另外，可以做预热缩略图

        为什么值得一条显式的路：立绘/CG 冷解码 384–434 ms/张，而缓存命中
        只要 2 ms；不预热就是用户在滚动时一张张等。真实项目 2816 张 ×
        2 个尺寸，跑几分钟 —— 所以**必须有进度、必须能中止**（关窗口即停，
        已解好的都留在缓存里）。

        跑在 `QThreadPool` 的普通任务里（不占 GUI 线程）；进度、结果都通过
        **类上定义的信号**回主线程（`warm_progress` / `warm_done`）。
        """
        if getattr(self, '_warming', False):
            return
        paths = []
        seen = set()
        # 只预热**当前这一类候选里**有图的那些（用户可能只在选立绘）
        for i in range(self._items.count()):
            it = self._items.item(i)
            if it is None:
                continue
            atype, aid = it.data(Qt.UserRole)
            if atype != 'image':
                continue
            p = self._image_path(aid)
            if p and p not in seen:
                seen.add(p)
                paths.append(p)
        if not paths:
            self._hint.setText('这里面没有可预热的图片。')
            return
        from storygraph.app.prewarm import UI_THUMB_SIZES, prewarm
        from PySide6.QtCore import QRunnable, QThreadPool

        self._warming = True
        self._warm_btn.setEnabled(False)
        self._warm_btn.setText('⚡ 预热中…')
        total = len(paths) * len(UI_THUMB_SIZES)
        self._hint.setText(f'预热中… 0 / {total}')
        dlg = self

        class _Warm(QRunnable):
            def run(self_inner):
                def on_progress(done, all_):
                    try:
                        dlg.warm_progress.emit(done, all_)   # 排队回主线程
                    except RuntimeError as e:                # 宿主已销毁
                        swallow('ui_qt.asset_picker._Warm.progress', e)

                try:
                    res = prewarm(paths, on_progress=on_progress,
                                  should_stop=lambda: not dlg._warming)
                except Exception as e:                       # noqa: BLE001
                    swallow('ui_qt.asset_picker._Warm.prewarm', e)
                    res = {'done': 0, 'failed': 0, 'seconds': 0,
                           'stopped': True, 'error': str(e)}
                try:
                    dlg.warm_done.emit(res)
                except RuntimeError as e:                    # 宿主已销毁
                    swallow('ui_qt.asset_picker._Warm.done', e)

        QThreadPool.globalInstance().start(_Warm())

    def _on_prewarm_progress(self, done: int, total: int):
        self._hint.setText(f'预热中… {done} / {total}（可以继续用，'
                           f'关窗口即中止）')

    def _on_prewarm_done(self, res: dict):
        self._warming = False
        try:
            self._warm_btn.setEnabled(True)
            self._warm_btn.setText('⚡ 预热缩略图')
        except Exception as e:                    # noqa: BLE001 - 控件没了
            swallow('ui_qt.asset_picker._on_prewarm_done', e)
        self._hint.setText(
            f'预热完成：成功 {res.get("done", 0)} · 失败 {res.get("failed", 0)}'
            f' · 用时 {res.get("seconds", 0)}s'
            + ('（已中止）' if res.get('stopped') else '')
            + ' —— 缩略图以后都是秒出')

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
        self._no_image = 0
        self._hint_total = 0
        self._fill_cursor = 0
        self._fill_done = 0
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
            label = self._short_label(atype, aid)
            it = _Item(label)
            it.setData(Qt.UserRole, (atype, aid))
            has_file = atype != 'image' or bool(self._image_path(aid))
            if atype == 'image' and not has_file:
                # **真的没有图**就当场说清楚（用户 2026-10-05："加载不完全"）。
                #
                # 从前这种条目和"还没解出来"长得一模一样（都是空格子），
                # 于是"图还在解"与"这张图根本找不到"分不清 —— 用户只能
                # 觉得"工具加载不全"。真实项目里那 74 条
                # `gui/dutui/**/RETURE.png` 就是这一类（Path 多了一层项目
                # 目录名，已在 `app.beats.asset_preview_path` 修掉）。
                it.setText(f'{label}\n（没有图片文件）')
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
            it.setToolTip(
                f'{aid}\n资源表里登记了，但按 Path 找不到图片文件。'
                if atype == 'image' and not has_file
                else P.detail(self._registry, atype, aid,
                              self._project_root)[:400])
            self._items.addItem(it)
            if atype == 'image' and not has_file:
                self._no_image += 1
        self._hint_total = len(pairs)
        self._update_hint()
        # **只给看得见的格子请求缩略图**
        #
        # 用户 2026-10-05：
        # > 照片加载缓慢，部分照片未加载，缺口大
        #
        # 以前这里对**全部**候选（真实项目 2828 张）一次性 `request()` ——
        # 线程池被灌满、可见的那几个排在几千个后面，于是"半天不出图 + 一堆空格子"。
        self._load_visible_thumbs()
        self._start_background_fill()

    def _update_hint(self, searching: bool = None):
        """
        提示条：**加载进度 + 无图条数**。

        为什么要写清楚（用户 2026-10-05："加载不完全"）：从前只有一行
        "2890 条"，于是"图还在解""这张根本没有""要等多久"三件事全靠猜。
        """
        if searching is None:
            searching = bool(self._filter)
        n = self._hint_total or self._items.count()
        parts = [f'{n} 条']
        if searching:
            parts.append('搜索中')
        if n:
            parts.append(f'已加载 {len(self._loaded)}')
        if self._no_image:
            parts.append(f'{self._no_image} 条没有图片文件')
        if self._fill_pending():
            parts.append('后台补齐中…')
        self._hint.setText(' · '.join(parts))

    # ------------------------------------------------------------------
    #  后台补齐（"加载不完全"的另一半：让它自己补完）
    # ------------------------------------------------------------------

    def _fill_pending(self) -> bool:
        """还有没请求过的图片格子吗（不含"根本没有图"的那些）。"""
        return self._fill_cursor < self._items.count()

    def _start_background_fill(self):
        """
        空闲时**逐块**把剩下的缩略图也排进队（`PRIORITY_BACKGROUND`）。

        为什么要有它：只加载"看得见的"能保证出图快，但用户滚到哪儿都得等 ——
        真正常用的几百张值得**提前**解好（用户 2026-10-05："加载不完全，
        缺口大"）。所以可见的照旧优先，剩下的按 `FILL_CHUNK` 一块一块地排队，
        每次只前进一步，把线程让给"正在看的那几张"。

        `FILL_BUDGET` 是刹车：一次打开最多补这么多条，避免为了刷满网格
        把 CPU 烧几分钟（真实项目大图冷解码 ~380 ms/张）。
        """
        if self._fill_timer is None:
            self._fill_timer = QTimer(self)
            self._fill_timer.setSingleShot(True)
            self._fill_timer.timeout.connect(self._fill_step)
        self._fill_deadline = min(self._items.count(), FILL_BUDGET)
        self._fill_timer.start(FILL_INTERVAL_MS)

    def _fill_step(self):
        """补一块（优先级最低；`focus()` 一来就把它们压下去）。"""
        if not self._items.count():
            return
        start = self._fill_cursor
        end = min(self._items.count(), start + FILL_CHUNK)
        for i in range(start, end):
            it = self._items.item(i)
            if it is None:
                continue
            atype, aid = it.data(Qt.UserRole)
            if atype != 'image' or aid in self._requested:
                continue
            if not self._image_path(aid):
                continue                          # 根本没有图：别浪费解码
            self._requested.add(aid)
            self._request_thumb(it, aid, priority=PRIORITY_BACKGROUND)
        self._fill_cursor = end
        self._update_hint()
        if self._fill_pending() and self._fill_cursor < self._fill_deadline:
            self._fill_timer.start(FILL_INTERVAL_MS)

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

    def _image_path(self, asset_id):
        """这条图片资产的**真实文件路径**（找不到返回 ''）—— 带记忆，别反复 stat。"""
        if asset_id in self._path_cache:
            return self._path_cache[asset_id]
        path = ''
        try:
            path = str(P.thumbnail_path(self._registry, 'image', asset_id,
                                        self._project_root) or '')
        except Exception as e:                     # noqa: BLE001
            swallow('ui_qt.asset_picker._image_path', e)
        if len(self._path_cache) > 20000:
            self._path_cache.clear()
        self._path_cache[asset_id] = path
        return path

    def _request_thumb(self, item, asset_id, priority=None):
        """请求一张缩略图；没有图源时**什么都不做**（调用方已按 `_image_path`
        判过，这里只是不再重复解析）。"""
        slot = self._slot_of(item, asset_id)
        path = self._image_path(asset_id)
        if not path:
            return slot

        def apply(pixmap, _p):
            if pixmap is None:
                return
            item.setIcon(pixmap)
            # 进度条（"已加载 N"）：**只在第一次拿到图时**记一笔，重复回填
            # （滚动来回）不重复计数。
            if asset_id not in self._loaded:
                self._loaded.add(asset_id)
                self._update_hint()

        self._loader.request(slot, path, apply, max_size=THUMB,
                             priority=priority)
        return slot

    def _show_detail(self):
        it = self._items.currentItem()
        if it is None:
            self._detail.setText('（选中一条看详情）')
            return
        atype, aid = it.data(Qt.UserRole)
        if atype == 'image' and not self._image_path(aid):
            self._detail.setText(
                f'[image] {aid}\n\n'
                f'⚠ 资源表里登记了这一条，但按它的 Path 在项目里找不到图片文件。\n'
                f'（不是"还在加载"—— 这一格永远不会有图。）')
            return
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
            if self._fill_timer is not None:
                self._fill_timer.stop()
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
