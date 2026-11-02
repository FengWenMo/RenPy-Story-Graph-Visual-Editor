"""
storygraph.ui_qt.asset_picker
=============================
**资产选择器**（PySide6）—— 对齐用户最在意的那几句：

> 资产列是**下拉框**，不用手打 ID
> 立绘怎么一堆单独的分类，什么 relaxing，resigned —— 这不都是凌的表情吗
> 只有文件名是角色名的可以匹配……直接放角色那个头像图就能动

左：类别列表（**角色优先**）：`立绘：凌跃捷（ling）` / 角色预览图 / 背景 / CG /
    界面 / 音乐 / 音效 / 语音 / 变换
    左上角一个下拉能在两种口径间**随时切**（用户 2026-10-30）：
    「按分类」（上面那套用途归类）⇄「按文件夹」（`images/ling` 这种真实目录，
    见 `app.picker.folder_groups`）。切的时候尽量停在同一批图上（交集最大）。
中：候选网格（真缩略图，走 QThreadPool 异步解码）
右：选中项的详情（不止原文，还写清"这是什么东西"：kind / tag / 属性 / 依据 /
    能不能直接写进 `show`）
上：搜索框 + **用途过滤**（全部 / 立绘 / 背景 / CG / 界面 / 角色预览图 / 其他）

## 这一层只画控件、收事件 —— 判定都在别处

  · 谁和谁一类（分组）                → `app.beats.categorize_assets`（角色优先）
  · 一条图是什么（kind/tag/属性/依据）→ `assets.asset_identity.resolve_image`
  · 能不能写进 `show`、预览图换成哪张 → `app.picker.resolve_pick`

**不许**在这里重写第二套判定（按路径猜 tag、认 `cc_preview` 这项目专名、
把预览图当立绘写回）—— 2026-10-30 用户实测：159 张预览副本混进立绘被拆成 110 个
单图分类，选中后还真写出 `show refusing`（引擎里没有这张图，游戏运行时"找不到图"）。

用户拍板：**预览图允许选，但必须自动换成对应的真立绘**，底部提示条说明
"已用对应的立绘 xxx"；换不到 / 认不出 → **禁止「确定」**并给一句原因。

其它细节说明散落在各处，都是踩过的坑（底色 / 预热 / 只加载可见项 / 后台补齐）。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow
from PySide6.QtCore import QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget,
                               QListWidgetItem as _Item, QPushButton, QSplitter,
                               QVBoxLayout, QWidget)

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

# ==================================================================
#  用途过滤（用户 2026-10-30：1193 张图全挤在一个列表里，找不到、容易误选）
# ==================================================================

PURPOSE_ALL = '全部'
PURPOSE_SPRITE = '立绘'
PURPOSE_BG = '背景'
PURPOSE_CG = 'CG'
PURPOSE_UI = '界面'
PURPOSE_PREVIEW = '角色预览图'
PURPOSE_OTHER = '其他'

#: 下拉里能选的档（顺序 = 用户找东西的顺序：立绘最常用）。
PURPOSES = (PURPOSE_ALL, PURPOSE_SPRITE, PURPOSE_BG, PURPOSE_CG, PURPOSE_UI,
            PURPOSE_PREVIEW, PURPOSE_OTHER)

#: kind → 用途档。**只按 `Identity.kind` 归**（不按分组名/路径/项目专名猜）。
PURPOSE_BY_KIND = {
    'sprite': PURPOSE_SPRITE,
    'bg': PURPOSE_BG,
    'cg': PURPOSE_CG,
    'ui': PURPOSE_UI,
    'preview': PURPOSE_PREVIEW,
    'avatar': PURPOSE_PREVIEW,     # 头像和预览图一样：不能直接上舞台
    'other': PURPOSE_OTHER,
    'unknown': PURPOSE_OTHER,
}

#: 调用方可能传 kind / 英文 / 中文 —— 都认，认不出就当「全部」（**不猜**）。
_PURPOSE_ALIASES = {
    '': PURPOSE_ALL, 'all': PURPOSE_ALL, '全部': PURPOSE_ALL,
    'sprite': PURPOSE_SPRITE, '立绘': PURPOSE_SPRITE, 'char': PURPOSE_SPRITE,
    'bg': PURPOSE_BG, 'background': PURPOSE_BG, '背景': PURPOSE_BG,
    'cg': PURPOSE_CG, 'CG': PURPOSE_CG,
    'ui': PURPOSE_UI, 'gui': PURPOSE_UI, '界面': PURPOSE_UI,
    'preview': PURPOSE_PREVIEW, 'avatar': PURPOSE_PREVIEW,
    '角色预览图': PURPOSE_PREVIEW, '预览图': PURPOSE_PREVIEW, '头像': PURPOSE_PREVIEW,
    'other': PURPOSE_OTHER, '其他': PURPOSE_OTHER,
}

#: `Identity.kind` → 人话（详情/tooltip/对照表共用）。
KIND_LABELS = {
    'sprite': '立绘',
    'preview': '角色预览图（不能直接上舞台）',
    'avatar': '角色头像（不能直接上舞台）',
    'bg': '背景',
    'cg': 'CG',
    'ui': '界面',
    'other': '其他图片',
    'unknown': '未识别',
}

#: `Identity.source` → 人话（"凭什么这么判"）。对照表与 tooltip 共用。
SOURCE_LABELS = {
    'declared': '项目自己的声明（.rpy）',
    'mapping': '映射（精确覆盖）',
    'mapping-rule': '映射（路径规则）',
    'tag-column': '资源表「Tag」列',
    'folder': '文件夹推断',
    'stem': '文件名推断',
    'unknown': '没认出来',
}

#: 重建分类列表时"保留当前选中项"的哨兵（`None` 表示「全部」那一行）。
_KEEP_CURRENT = object()

# ==================================================================
#  左栏的两种口径（用户 2026-10-30）
# ==================================================================
#
#  > 文件筛选与分类方面……要提供两种分类方式，一种就是按文件夹分类排序，
#  > 另一种就是原来的按图片 / 背景 / UI 等那种分类排序……这两种方式要能够
#  > 随时切换
#
# 两种都是"怎么找图"，只是口径不同：分类回答"这是什么"，文件夹回答"它在哪"。
# 谁都不许替用户拍板，所以做成一个**随时能按**的下拉，而不是二选一的偏好。

GROUP_BY_CATEGORY = '按分类'
GROUP_BY_FOLDER = '按文件夹'
GROUP_MODES = (GROUP_BY_CATEGORY, GROUP_BY_FOLDER)

#: 文件夹那一档里，每一层缩进多少（`QListWidget` 没有层级，靠空格画树）。
FOLDER_INDENT = '  '
#: 最多缩进几层（再深就贴边了，路径全写在 tooltip 里）。
FOLDER_INDENT_MAX = 6


# ==================================================================
#  判定/标签 —— 纯函数（判定本身不在这里，见模块 docstring）
# ==================================================================

def purpose_label(value: str) -> str:
    """把调用方给的用途参数规范成下拉里的一档（认不出的当「全部」，不猜）。"""
    raw = str(value or '').strip()
    key = raw.lower()
    if key in _PURPOSE_ALIASES:
        return _PURPOSE_ALIASES[key]
    if raw in PURPOSES:
        return raw
    return PURPOSE_ALL


def purpose_of(identity) -> str:
    """
    一条图的用途档。

    **只按 `Identity.kind` 归**（用户 2026-10-30："立绘/背景/CG/界面混在一起"）：
    kind 是判定层唯一真源给的，分组名只是显示文案，不能拿来做机器判断。
    """
    kind = str(getattr(identity, 'kind', '') or '')
    return PURPOSE_BY_KIND.get(kind, PURPOSE_OTHER)


def pick_context(registry, project_root, *, mapping=None) -> dict:
    """
    装配 `resolve_image` / `resolve_pick` 需要的**项目上下文**。

    唯一实现在 `app.picker.pick_context` —— 界面只拿得到 registry + 项目根，
    而"哪些 tag 是known 角色、显示名是什么、映射文件在哪"属于语义层。
    这里不重写第二套（否则两处口径必然漂移）。判定层不可用时返回 `{}`。
    """
    try:
        return P.pick_context(registry, project_root, mapping=mapping) or {}
    except Exception as e:                    # noqa: BLE001 - 判定层坏了也要能开窗
        swallow('ui_qt.asset_picker.pick_context', e)
        return {}


def identity_map(registry, project_root, *, mapping=None) -> dict:
    """
    **全部**图片行 → `{asset_id: Identity}`（kind / tag / attrs / display /
    source / showable）。

    判定 100% 在 `assets.asset_identity`（唯一真源）：这里只装配项目上下文
    （已知角色 tag / 显示名 / 映射 / 副本索引），然后走它的**批量入口** ——
    `resolve_image` 的 docstring 明说"UI 不要逐条调"（逐条会重建副本索引，
    1720 行 = O(n²)）。判定层不可用时返回 `{}`，界面按"没认出来"处理。
    """
    if registry is None:
        return {}
    root = str(project_root or '')
    images = getattr(registry, 'images', None) or {}
    try:
        from storygraph.assets.asset_identity import (project_context,
                                                      resolve_image)
        ctx = project_context(registry, root, mapping=mapping)
        idents = dict(P.identities(registry, root, context=ctx))
    except Exception as e:                    # noqa: BLE001 - 不让判定炸掉选择器
        swallow('ui_qt.asset_picker.identity_map', e)
        return {}
    # 批量入口以**行里的 ID** 为 key，而资源表字典的 key 偶尔不同（真实项目
    # 实测有 `'bg_2 '` 带尾空格、行 ID 是 `bg_2` 的行）。这里按表 key 补齐，
    # 保证"表里每一行都有结论" —— 否则那几行在选择器里会变成"未识别"。
    for key in images:
        key_s = str(key)
        if key_s in idents:
            continue
        row = images.get(key) or {}
        rid = str((row or {}).get('ID') or '').strip()
        if rid and rid in idents:
            idents[key_s] = idents[rid]
            continue
        try:
            idents[key_s] = resolve_image(
                row, relpath=str(row.get('Path') or ''),
                known_tags=ctx['known_tags'], known_chars=ctx['known_chars'],
                mapping=ctx['mapping'], rows=images,
                copy_index=ctx.get('copy_index'),
                sprite_words=ctx.get('sprite_words') or {})
        except Exception as e:                # noqa: BLE001 - 单行失败不影响别的
            swallow('ui_qt.asset_picker.identity_map.row', e)
    return idents


def identity_of(registry, project_root, asset_id, *, row=None):
    """
    一条图片行的 `Identity`（单条；批量请用 `identity_map`）。

    返回 `None` = 判定层不可用（没有注册表 / 判定抛错）—— 界面按"没认出来"
    处理，**不自己猜**。
    """
    if registry is None:
        return None
    try:
        return P.identity_of(registry, asset_id, row,
                             project_root=str(project_root or ''))
    except Exception as e:                    # noqa: BLE001 - 不让判定炸掉选择器
        swallow('ui_qt.asset_picker.identity_of', e)
        return None


def _fold_dup_halves(text: str) -> str:
    """
    把"前后半段完全相同"的重复折掉一半。

    真实项目里有 `Tag == Attributes`（两栏都填了 `Frame 13`）的行，拼出来是
    `Frame 13 Frame 13`。只折叠这种**整段重复**，不做通用去重（免得误伤
    `ling happy happy` 这种真写法）。
    """
    words = str(text or '').split()
    half = len(words) // 2
    if half and words[:half] == words[half:]:
        return ' '.join(words[:half])
    return ' '.join(words)


def _short_spec(asset_id: str, row: dict = None) -> str:
    """资产 ID + 行 → 可写进卡片的 image spec（`ling happy`）；取不到就用 ID。"""
    try:
        spec = P.image_spec_of(asset_id, row or {})
    except Exception as e:                    # noqa: BLE001
        swallow('ui_qt.asset_picker._short_spec', e)
        spec = ''
    return _fold_dup_halves(str(spec or '').strip() or str(asset_id or ''))


def human_label(identity, asset_id: str = '', row: dict = None) -> str:
    """
    候选格子里显示的**人话名**。

      · 角色相关（立绘 / 预览图 / 头像）：`凌跃捷 refusing`
        （显示名来自 characters.xlsx；没有显示名就退 tag；预览图/头像带前缀）；
      · 背景 / CG / 界面：沿用能写进卡片的 image spec（`bg bg9_5`）；
      · 未识别：明写「未识别」，不装成某个角色。

    原始资产 ID / 相对路径一律放 tooltip —— 格子里只放"人怎么叫它"。
    """
    aid = str(asset_id or '').strip()
    row = row or {}
    if identity is None:
        return _short_spec(aid, row) or aid

    kind = str(getattr(identity, 'kind', '') or '')
    name = (str(getattr(identity, 'display', '') or '').strip()
            or str(getattr(identity, 'tag', '') or '').strip())
    attrs = ' '.join(str(a) for a in (getattr(identity, 'attrs', ()) or ())
                     if str(a).strip())
    if kind in ('sprite', 'preview', 'avatar'):
        head = name or aid
        if kind == 'preview':
            head = f'预览图 · {head}'
        elif kind == 'avatar':
            head = f'头像 · {head}'
        return _fold_dup_halves(f'{head} {attrs}'.strip())

    spec = _short_spec(aid, row) or aid
    if kind == 'unknown':
        return f'未识别 · {spec}'
    label = KIND_LABELS.get(kind, '')
    return f'{label} · {spec}' if label else spec


def override_lines(identity) -> list:
    """
    这一条身份**覆盖了谁**（P0-3）→ 一列人话（可能空）。

    数据来自判定层（`Identity.overrides`，append-only）；这里只做格式化，
    不重新推一遍"谁赢"（那会变成第二个判据）。
    """
    if identity is None:
        return []
    try:
        from storygraph.core.authority import describe
        return describe(getattr(identity, 'overrides', ()) or ())
    except Exception as e:                      # noqa: BLE001 - 说明文字不该拦界面
        swallow('ui_qt.asset_picker.override_lines', e)
        return []


def identity_tooltip(identity, asset_id: str = '', row: dict = None,
                     detail: str = '') -> str:
    """
    格子的 tooltip：**原始 ID / 相对路径 + 凭什么这么判 + 能不能 show**。

    用户 2026-10-30 的原话是"只有文件名是角色名的可以匹配"，根因就是界面
    从不把"依据"摆出来 —— 人只能对着文件名猜。这里把依据写成一行，另外
    原始 ID / 路径也留着（要复制、要回资源表查时有用）。
    """
    aid = str(asset_id or '').strip()
    row = row or {}
    lines = [f'资产 ID：{aid}']
    rel = str(row.get('Path') or '').strip()
    lines.append(f'文件：{rel}' if rel else '文件：（资源表里没有登记路径）')
    if identity is not None:
        kind = str(getattr(identity, 'kind', '') or '')
        src = str(getattr(identity, 'source', '') or '')
        lines.append(f'判定：{KIND_LABELS.get(kind, kind)}'
                     f'（依据：{SOURCE_LABELS.get(src, src or "—")}）')
        covers = override_lines(identity)
        if covers:
            lines.append('覆盖了：' + '；'.join(covers))
        tag = str(getattr(identity, 'tag', '') or '')
        attrs = ' '.join(str(a) for a in (getattr(identity, 'attrs', ()) or ())
                         if str(a).strip())
        lines.append(f'角色 tag：{tag or "（没有）"}    属性：{attrs or "（没推断出）"}')
        showable = bool(getattr(identity, 'showable', False))
        spec = ' '.join([tag] + (attrs.split() if attrs else [])).strip()
        if showable:
            lines.append('能不能直接写进 `show`：能'
                         + (f'（会写成 `{spec}`）' if spec else ''))
        else:
            lines.append('能不能直接写进 `show`：不能'
                         '（预览图/头像/未识别 —— 选了会被自动换掉或拒绝）')
    if detail:
        lines += ['', str(detail)]
    return '\n'.join(lines)


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

    **选中即校验**（用户拍板 2026-10-30）：图片选中后走
    `app.picker.resolve_pick` ——
      · 能 show → 底部提示条写"将写入 xxx"；
      · 预览图/头像 → 自动换成真立绘，提示条写"已用对应的立绘 xxx"；
      · 换不到/认不出 → **「确定」置灰**，提示条给一句原因。
    这样"看起来加上了、保存后写出非法 show"这条老路走不通了。
    """

    #: 预热缩略图的进度 / 结果 —— **必须定义在类上**：工作线程 emit，
    #: Qt 会把它排队回这个对象所属的线程（GUI 线程），槽里才敢碰控件。
    warm_progress = Signal(int, int)
    warm_done = Signal(object)

    def __init__(self, parent=None, registry=None, project_root=None,
                 current: str = '', types=None, want_image_spec: bool = False,
                 title: str = '选择资产', purpose: str = ''):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 620)
        theme.apply_to(self)

        self._registry = registry
        self._project_root = project_root
        self._types = list(types) if types else None
        self._want_spec = bool(want_image_spec)
        #: 用途过滤只有"选图片"才有意义（选音乐/变换时藏起来，别占地方）。
        self._show_purpose = (self._want_spec and
                              (self._types is None or 'image' in self._types))
        self._purpose = (purpose_label(purpose) if self._show_purpose
                         else PURPOSE_ALL)
        self.selection = None
        self.image_spec = ''
        #: 预览图被自动换真立绘时的那句话（第二阶段卡片警告条/状态栏要用）
        self.replaced_message = ''
        #: 选中那一刻的判定结果（`app.picker.PickResult`；选音频等为 None）
        self.pick_result = None
        self._filter = ''
        self._warming = False
        #: `asset_id → AtlBlock | None`（'image 名:' 块形式的只读定位；非 ATL 为 None）
        self._atl_cache: dict = {}
        #: 当前详情对应的源文件位置 `(path, line)`；没有 = 「打开源文件」置灰
        self._detail_loc = None
        #: `asset_id → Identity`：**一次算全量**（判定层批量入口），第一次用到时才算
        self._identities = None
        #: `asset_id → PickResult | None`：选中即校验的结果（切来切去不重复算）
        self._pick_cache: dict = {}
        #: `分组名 → 用途档`（只用于"选了用途就藏起不相关的分组"）
        self._group_purpose_cache: dict = {}
        #: 左栏口径（`GROUP_MODES` 之一）。**不落盘**：这是"这一眼怎么找"，
        #: 不是长期偏好；真记住它反而会让"上次切过文件夹"变成看不见的状态。
        self._group_by = GROUP_BY_CATEGORY
        #: `{文件夹: [(类型, id), …]}` —— 按文件夹那种口径（第一次切过去才算，
        #: 真实项目 74 个文件夹 / 1977 张图约 20ms，但没切就不该付这个钱）。
        self._folder_groups = None
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
        """整块界面：顶栏 → 三栏（左分类 / 中候选 / 右详情）→ 两条提示 → 按钮。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])
        self._build_top_bar(root)
        self._build_splitter(root)
        self._build_hints(root)
        self._build_buttons(root)

    def _build_top_bar(self, root) -> None:
        """搜索 + 用途过滤 + 底色切换 + 预热缩略图。"""
        top = QHBoxLayout()
        top.setSpacing(T.SPACE['sm'])
        lab = QLabel('搜索')
        lab.setProperty('role', 'caption')
        top.addWidget(lab, 0)
        self._search = QLineEdit()
        # 现在格子显示的是**人话名**（`凌跃捷 refusing`），所以搜索也按
        # "名字 / ID 片段"来提示；底层 `P.search` 仍按资产 ID 打分。
        self._search.setPlaceholderText('输入名字 / ID 前缀 / 片段（留空显示全部）')
        self._search.textChanged.connect(self._on_search)
        top.addWidget(self._search, 1)
        # **用途过滤**（用户 2026-10-30："1193 张图全在一个列表里，找不到"）：
        # 一个下拉就够，不做复杂交互；档位只由 `Identity.kind` 决定。
        if self._show_purpose:
            lab_purpose = QLabel('用途')
            lab_purpose.setProperty('role', 'caption')
            top.addWidget(lab_purpose, 0)
            self._purpose_box = QComboBox()
            self._purpose_box.setObjectName('purpose')
            for name in PURPOSES:
                self._purpose_box.addItem(name)
            self._purpose_box.setSizeAdjustPolicy(
                QComboBox.AdjustToContents)          # 别把「角色预览图」截成「角色预览」
            self._purpose_box.setMinimumWidth(120)
            self._purpose_box.setCurrentText(self._purpose)
            self._purpose_box.setToolTip(
                '只显示某一类图：立绘 / 背景 / CG / 界面 / 角色预览图。\n'
                '「角色预览图」是**不能直接上舞台**的副本 —— 选了会自动换成'
                '对应的真立绘，换不到就不让加。')
            self._purpose_box.currentTextChanged.connect(self._on_purpose)
            top.addWidget(self._purpose_box, 0)
        else:
            self._purpose_box = None
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

    def _build_splitter(self, root) -> None:
        """三栏：左分类 / 中候选 / 右详情（尺寸按"中栏最宽"分）。"""
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._build_left_pane())
        split.addWidget(self._build_items_pane())
        split.addWidget(self._build_right_pane())
        split.setSizes([200, 460, 240])
        root.addWidget(split, 1)

    def _build_left_pane(self):
        """左：分类方式 + 类别。下拉贴着它影响的东西，别当成全窗口筛选器。"""
        # —— 左：分类方式 + 类别 ——
        #
        # 分类方式的下拉**贴着左栏**（不塞进顶部搜索那一条）：它改的是"左栏
        # 按什么排"，放在它影响的东西上面，人才不会以为它是全窗口的筛选器。
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, T.SPACE['sm'], 0)
        ll.setSpacing(T.SPACE['xs'])
        head = QHBoxLayout()
        head.setSpacing(T.SPACE['xs'])
        cap_cat = QLabel('分类方式')
        cap_cat.setProperty('role', 'caption')
        head.addWidget(cap_cat, 0)
        self._group_by_box = QComboBox()
        self._group_by_box.setObjectName('group_by')
        self._group_by_box.addItems(list(GROUP_MODES))
        self._group_by_box.setCurrentText(self._group_by)
        self._group_by_box.setMinimumWidth(110)
        self._group_by_box.setToolTip(
            '左栏按什么排：\n'
            '· 按分类 —— 立绘：<角色> / 背景 / CG / 界面 / 音乐 …（这是什么）\n'
            '· 按文件夹 —— 资源表里 Path 的真实目录（它在哪），\n'
            '  每一层都含它下面所有子文件夹的图。\n'
            '两个口径随时切，切的时候尽量停在同一批图上。')
        self._group_by_box.currentTextChanged.connect(self._on_group_by)
        head.addWidget(self._group_by_box, 1)
        ll.addLayout(head)
        self._cats = QListWidget()
        self._cats.currentItemChanged.connect(lambda *_: self._load_items())
        ll.addWidget(self._cats, 1)
        left.setMaximumWidth(260)
        left.setMinimumWidth(170)
        return left

    def _build_items_pane(self):
        """中：候选（严格网格 + 只解看得见的缩略图）。"""
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
        self._items.currentItemChanged.connect(self._on_current_changed)
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
        return self._items

    def _build_right_pane(self):
        """右：详情 + 打开源文件（ATL 块形式唯一的"看原文"入口）。"""
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
        # **打开源文件**（用户 2026-10-19，A1）：ATL 块形式的图没有贴图可看，
        # 唯一有价值的动作是"把定义它的那几行原文摆出来"，第二有价值的是
        # "直接跳过去改"。只在当前这条**定位得到源文件**时才可点。
        self._open_btn = QPushButton('打开源文件')
        self._open_btn.setToolTip(
            '用系统默认的代码编辑器打开定义这一块的 .rpy，并定位到块头那一行。\n'
            '（ATL 块形式没法在表里编辑：改它等于改代码。）')
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._open_source)
        rl.addWidget(self._open_btn, 0)
        right.setMinimumWidth(200)
        right.setMaximumWidth(300)
        return right

    def _build_hints(self, root) -> None:
        """两条提示：选中即校验那条（含为什么不能加）+ 计数条。"""
        # **选中即校验的提示条**（预览图自动换真立绘 / 换不到为什么不能加）。
        # 单独一条，不去挤"N 条 · 已加载 M"那条计数（后者一直有用）。
        self._pick_hint = QLabel('')
        self._pick_hint.setObjectName('pick_hint')
        self._pick_hint.setWordWrap(True)
        self._pick_hint.setProperty('role', 'note')
        self._pick_hint.setVisible(False)
        root.addWidget(self._pick_hint)

        self._hint = QLabel('')
        self._hint.setProperty('role', 'caption')
        root.addWidget(self._hint)

    def _build_buttons(self, root) -> None:
        """确定 / 取消（`accept` / `reject`）。"""
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        self._ok.setText('确定')
        self._ok.setProperty('variant', 'primary')
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
        #
        # 分组口径（角色优先 / 预览图单独一组）由 `app.beats.categorize_assets`
        # 决定，界面只展示它的名字 —— 不在这里做机器判断。
        self._groups = P.groups_with_plain(self._registry, types=self._types,
                                           project_root=self._project_root)
        # 资源表换了 → 文件夹口径（按 Path 算的）也必须重算。
        self._folder_groups = None
        self._group_purpose_cache.clear()
        self._refresh_categories(keep=None)

    def _mode_groups(self) -> dict:
        """
        当前口径下要展示的组：分类 ⇄ 文件夹（两种都保留，切换只是换这一眼）。

        文件夹口径**现算**（`app.picker.folder_groups`，纯字符串归一、不 stat
        磁盘）：真实项目 1977 张图 → 74 个文件夹约 20ms，切过去才付这笔钱。
        """
        if self._group_by != GROUP_BY_FOLDER:
            return self._groups
        if self._folder_groups is None:
            self._folder_groups = P.folder_groups(
                self._registry, project_root=self._project_root,
                types=self._types or ['image'])
        return self._folder_groups

    def _on_group_by(self, text):
        """切口径：**别把人甩到「全部」去**（尽量停在同一批图上）。"""
        mode = str(text or '').strip()
        if mode not in GROUP_MODES:
            mode = GROUP_BY_CATEGORY
        if mode == self._group_by:
            return
        old_cur = self._current_group()
        old_pairs = list(self._mode_groups().get(old_cur) or ()) if old_cur else []
        self._group_by = mode
        self._group_purpose_cache.clear()      # 组的用途是按组内前几条算的，换口径要重算
        self._refresh_categories(keep=self._match_group(old_pairs))

    def _match_group(self, pairs):
        """
        老口径里那批图 → 新口径里**交集最大**的那一组（没有就回「全部」）。

        切过去还指着同一批图，人才不会觉得"一切就把我丢了"；找不到对应关系
        就老实回「全部」，不硬凑一个空组。

        交集一样大时取**更具体**的那个（文件夹口径下"更深"= 少一层囊括）：
        不然 `pomodorro` 会抢走 `pomodorro/images` 的位置，切过去看到的图比
        原来那批多一圈。
        """
        want = set(pairs or ())
        if not want:
            return None
        best, best_key = None, (0, 0)
        for name, group in self._mode_groups().items():
            n = len(want & set(group or ()))
            if not n:
                continue
            key = (n, str(name).count('/'))
            if key > best_key:
                best, best_key = name, key
        return best

    def _folder_labels(self, names=None) -> dict:
        """
        `{文件夹: 左栏那一行文字}`（文件夹口径，**一次算全**）。

        `names` 是**左栏真的要显示的那些组**（用途过滤已经过一遍）—— 只按屏幕
        上真出现的行算重名，否则"被过滤掉的那个同名文件夹"会让这一行白白多一段
        `← characters` 尾巴（真机上"用途：立绘"时就出现过）。

        缩进画层级、后缀写张数；**叶子名撞车时补一段父目录** —— 真实项目里
        `gui/dutui/overlay` 与 `gui/phone/overlay` 都叫 `overlay`，只写叶子名
        就是"两行长得一模一样、点开才知道是哪一个"。补到能区分的最短一段
        （`← dutui`），不是无脑写全路径。

        分类口径**一个字都不改**（`立绘：凌跃捷（ling）` 就是它本来的样子：
        那套名字自己带角色名，再挂数字只是噪声）。
        """
        groups = self._mode_groups()
        special = (P.ROOT_GROUP, P.NO_PATH_GROUP)
        shown = [n for n in (groups if names is None else names)]
        labels: dict = {}
        names_plain = [n for n in shown if n not in special]
        #: 叶子名（小写）→ 叫这个名字的文件夹们
        same_leaf: dict = {}
        for n in names_plain:
            same_leaf.setdefault(str(n).rsplit('/', 1)[-1].lower(),
                                 []).append(n)
        for n in names_plain:
            depth = min(str(n).count('/'), FOLDER_INDENT_MAX)
            leaf = str(n).rsplit('/', 1)[-1]
            text = f'{FOLDER_INDENT * depth}{leaf}（{len(groups.get(n) or ())}）'
            twins = same_leaf.get(leaf.lower()) or []
            if len(twins) > 1:
                hint = self._parent_hint(n, twins)
                if hint:
                    text += f' ← {hint}'
            labels[n] = text
        for n in special:
            if n in shown:
                labels[n] = f'{n}（{len(groups.get(n) or ())}）'
        return labels

    @staticmethod
    def _parent_hint(name: str, twins) -> str:
        """叶子重名时，从近到远补父目录，补到**能和同名的其他几个分开**为止。"""
        parts = str(name).split('/')[:-1]
        others = [o for o in twins if o != name]
        for k in range(1, len(parts) + 1):
            tail = [p.lower() for p in parts[-k:]]
            if all([p.lower() for p in str(o).split('/')[:-1]][-k:] != tail
                   for o in others):
                return '/'.join(parts[-k:])
        return ''

    def _refresh_categories(self, keep=_KEEP_CURRENT):
        """
        重建左侧分类列表。

        用途过滤会改变"哪些组还看得见"：选了「只看立绘」再点「背景」只会看到
        空列表 —— 那不是用户要的。组归哪档由 `_group_purpose()` 按**组内前
        几条的 `Identity.kind`** 定（不按组名字面量猜）。
        """
        if keep is _KEEP_CURRENT:
            keep = self._current_group()
        self._cats.blockSignals(True)
        try:
            self._cats.clear()
            item_all = _Item(PURPOSE_ALL)
            item_all.setData(Qt.UserRole, None)
            self._cats.addItem(item_all)
            groups = self._mode_groups()
            shown = [n for n in groups if self._group_matches_purpose(n)]
            labels = self._folder_labels(shown) \
                if self._group_by == GROUP_BY_FOLDER else {}
            for name in shown:
                it = _Item(labels.get(name) or name)
                it.setData(Qt.UserRole, name)
                if name not in (P.ROOT_GROUP, P.NO_PATH_GROUP):
                    it.setToolTip(f'{name}\n{len(groups.get(name) or ())} 条'
                                  + ('（含子文件夹）'
                                     if self._group_by == GROUP_BY_FOLDER else ''))
                self._cats.addItem(it)
            row = 0
            for i in range(self._cats.count()):
                if self._cats.item(i).data(Qt.UserRole) == keep:
                    row = i
                    break
            self._cats.setCurrentRow(row)
        finally:
            self._cats.blockSignals(False)
        self._load_items()

    def _group_purpose(self, name: str) -> str:
        """一个分组主要是什么用途（看组内前几条的判定；算一次记住）。"""
        if name in self._group_purpose_cache:
            return self._group_purpose_cache[name]
        counts: dict = {}
        for atype, aid in (self._mode_groups().get(name) or [])[:8]:
            if atype != 'image':
                continue
            p = purpose_of(self._identity(aid))
            counts[p] = counts.get(p, 0) + 1
        best = max(counts, key=lambda k: counts[k]) if counts else ''
        self._group_purpose_cache[name] = best
        return best

    def _group_matches_purpose(self, name: str) -> bool:
        if self._purpose == PURPOSE_ALL:
            return True
        return self._group_purpose(name) == self._purpose

    def _on_purpose(self, text):
        self._purpose = purpose_label(text)
        self._refresh_categories()

    def _current_group(self):
        it = self._cats.currentItem()
        return it.data(Qt.UserRole) if it is not None else None

    def _pairs(self) -> list:
        """当前该显示的 `(asset_type, asset_id)` 列表。"""
        group = self._current_group()
        groups = self._mode_groups()
        if self._filter:
            pairs = P.search(self._registry, self._filter, limit=300,
                             project_root=self._project_root)
        elif group is None:
            pairs = [(t, a) for v in groups.values() for (t, a) in v]
        else:
            pairs = list(groups.get(group) or [])
        if self._types:
            pairs = [(t, a) for (t, a) in pairs if t in self._types]
        # 用途过滤：只留这一档的图（按 kind 判，**不按分组名/路径**）。
        # 选"全部"时不过滤 —— 音频/变换等非图片资产也照旧看得见。
        if self._purpose != PURPOSE_ALL:
            pairs = [(t, a) for (t, a) in pairs
                     if t == 'image' and purpose_of(self._identity(a)) == self._purpose]
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
        self._atl_tiles = 0
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
            # 显示**人话名**：角色显示「显示名 + 属性」（`凌跃捷 refusing`），
            # 背景/CG/界面显示可写进卡片的 image spec（`bg bg9_5`）；
            # 原始资产 ID / 相对路径 / 判定依据全在 tooltip 里，不挤在格子里。
            row = self._row(aid) if atype == 'image' else None
            ident = self._identity(aid) if atype == 'image' else None
            label = self._short_label(atype, aid)
            it = _Item(label)
            it.setData(Qt.UserRole, (atype, aid))
            has_file = atype != 'image' or bool(self._image_path(aid))
            atl = self._atl_block(aid) if atype == 'image' else None
            if atype == 'image' and not has_file:
                if atl is not None:
                    # **ATL 块形式**（`_atl`）：`Path` 本来就是空的（贴图在块里），
                    # 写成"没有图片文件"是在报一个不存在的错 —— 用户 2026-10-19：
                    # "卡片看不见"（只剩一句假警报）。这里直接说清它是什么。
                    it.setText(f'{label}\n（ATL 定义，无贴图）')
                else:
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
            # ATL 行的详情里就是块体原文，别在 400 字处切断（块体常超）
            cap = 2000 if atl is not None else 400
            if atype == 'image' and not has_file and atl is None:
                detail = '资源表里登记了，但按 Path 找不到图片文件。'
            else:
                detail = P.detail(self._registry, atype, aid,
                                  self._project_root)[:cap]
            # tooltip = 原始 ID / 路径 + 判定依据 + 能不能 show + 旧详情。
            it.setToolTip(identity_tooltip(ident, aid, row, detail))
            self._items.addItem(it)
            if atype == 'image' and not has_file:
                if atl is not None:
                    self._atl_tiles += 1
                else:
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
        self._update_pick_hint()
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
        if self._show_purpose and self._purpose != PURPOSE_ALL:
            parts.append(f'用途：{self._purpose}')
        if searching:
            parts.append('搜索中')
        if n:
            parts.append(f'已加载 {len(self._loaded)}')
        if self._no_image:
            parts.append(f'{self._no_image} 条没有图片文件')
        if self._atl_tiles:
            # 这一类**本来就没有贴图**（ATL 块形式，贴图在块里）：
            # 不单列的话它会被算进上面那句"没有图片文件"，变成假警报。
            parts.append(f'{self._atl_tiles} 条是 ATL 定义（无贴图）')
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

        实现全在 `human_label`（角色显示名 + 属性 / 背景 CG 的可写 spec）；
        这里只负责"非图片类型原样显示资产 ID"。
        """
        if atype != 'image':
            return str(aid)
        return human_label(self._identity(aid), aid, self._row(aid))

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

    # ------------------------------------------------------------------
    #  判定（Identity / PickResult）—— 只装配、不重写规则
    # ------------------------------------------------------------------

    def _row(self, aid):
        """资源表里这条图片的原始行（没有返回 None）。"""
        try:
            return P.row_of(self._registry, 'image', aid, self._project_root)
        except Exception as e:                     # noqa: BLE001
            swallow('ui_qt.asset_picker._row', e)
            return None

    def _identity_map(self) -> dict:
        """`asset_id → Identity`（判定层批量结果；一次打开只算一次）。"""
        if self._identities is None:
            self._identities = identity_map(self._registry, self._project_root)
        return self._identities

    def _identity(self, aid):
        """一条图片的判定结论（`Identity`；判定层不可用时为 None）。"""
        return self._identity_map().get(str(aid or ''))

    def _pick_result(self, aid):
        """
        选中一条图时的判定：能 show 吗 / 预览图换成哪张真立绘 / 为什么不能加。

        判定 100% 在 `app.picker.resolve_pick`（唯一真源；传 registry 让它自己
        装配上下文）。返回 `None` = 判定层不可用（没有注册表）—— 这时保留
        老行为：直接把 spec 给它（无头测试/极老的项目仍能用）。
        """
        aid = str(aid or '')
        if aid in self._pick_cache:
            return self._pick_cache[aid]
        if self._registry is None:
            return None
        try:
            res = P.resolve_pick(aid, self._row(aid) or {},
                                 project_root=self._project_root,
                                 registry=self._registry)
        except Exception as e:                     # noqa: BLE001 - 不让判定炸掉界面
            swallow('ui_qt.asset_picker._pick_result', e)
            res = None
        self._pick_cache[aid] = res
        return res

    @staticmethod
    def _repolish(widget):
        """`role` 属性变了要重新 polish 一次，QSS 才会换色。"""
        try:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        except Exception as e:                     # noqa: BLE001
            swallow('ui_qt.asset_picker._repolish', e)

    def _on_current_changed(self, *_):
        self._show_detail()
        self._update_pick_hint()

    def _update_pick_hint(self):
        """
        底部提示条 + 「确定」能不能按。

        用户拍板（2026-10-30）：**预览图允许选，但必须自动换成真立绘**；
        换不到 / 认不出 → 不许加（从前是"看起来加上了"，保存后写出非法的
        `show refusing`，游戏里运行时找不到图）。
        """
        if not self._show_purpose:
            self._pick_hint.setVisible(False)
            self._ok.setEnabled(True)
            return
        it = self._items.currentItem()
        if it is None:
            # 没点任何一条：保留老行为（点「确定」会提示"还没选中"）。
            self._pick_hint.setVisible(False)
            self._ok.setEnabled(True)
            return
        atype, aid = it.data(Qt.UserRole)
        if atype != 'image':
            self._pick_hint.setVisible(False)
            self._ok.setEnabled(True)
            return
        res = self._pick_result(aid)
        if res is None:
            # 判定层不可用：不假报错，退回老行为（直接写 spec）。
            self._pick_hint.setVisible(False)
            self._ok.setEnabled(True)
            return
        self._pick_hint.setVisible(True)
        if res.ok and res.spec:
            self._ok.setEnabled(True)
            if res.replaced_to:
                msg = res.message or '已换成对应的真立绘'
                self._pick_hint.setText(f'{msg}；将写入：{res.spec}')
            else:
                self._pick_hint.setText(f'将写入：{res.spec}')
            self._pick_hint.setProperty('role', 'note')
        elif res.ok:
            self._ok.setEnabled(False)
            self._pick_hint.setText(
                '⚠ 这张图认不出该写成什么名字（可在资源映射里登记）。')
            self._pick_hint.setProperty('role', 'warn')
        else:
            self._ok.setEnabled(False)
            self._pick_hint.setText(f'⚠ {res.message or "这一张不能加"}')
            self._pick_hint.setProperty('role', 'warn')
        self._repolish(self._pick_hint)

    @staticmethod
    def _identity_block(identity) -> str:
        """详情面板最上面那几行"这是什么东西"。"""
        if identity is None:
            return ''
        kind = str(getattr(identity, 'kind', '') or '')
        src = str(getattr(identity, 'source', '') or '')
        tag = str(getattr(identity, 'tag', '') or '')
        attrs = ' '.join(str(a) for a in (getattr(identity, 'attrs', ()) or ())
                         if str(a).strip())
        showable = bool(getattr(identity, 'showable', False))
        covers = override_lines(identity)
        return (f'判定：{KIND_LABELS.get(kind, kind)}'
                f'（依据：{SOURCE_LABELS.get(src, src or "—")}）\n'
                + (f'覆盖了：{"；".join(covers)}\n' if covers else '')
                + f'角色 tag：{tag or "（没有）"}    属性：{attrs or "（没推断出）"}\n'
                + f'能不能直接写进 show：{"能" if showable else "不能"}\n\n')

    def _atl_block(self, asset_id):
        """
        这一条图片是不是 **ATL 块形式**？是就返回 `AtlBlock`（含块体 + 定位），
        否则 `None`。

        结果按 `asset_id` 记住：网格滚动 / 重新选中会反复问，而它每次都读磁盘。
        （缓存只活在这个对话框里 —— 源文件改了，重开对话框就是新的。）
        """
        if asset_id in self._atl_cache:
            return self._atl_cache[asset_id]
        blk = None
        try:
            from storygraph.assets.atl_block import block_of_row, is_atl_row
            row = P.row_of(self._registry, 'image', asset_id, self._project_root)
            if row is not None and is_atl_row(row):
                blk = block_of_row(row, self._project_root)
        except Exception as e:                     # noqa: BLE001 - 不该拦选择
            swallow('ui_qt.asset_picker._atl_block', e)
        if len(self._atl_cache) > 20000:
            self._atl_cache.clear()
        self._atl_cache[asset_id] = blk
        return blk

    def _show_detail(self):
        it = self._items.currentItem()
        if it is None:
            self._detail.setText('（选中一条看详情）')
            self._detail_loc = None
            self._open_btn.setEnabled(False)
            return
        atype, aid = it.data(Qt.UserRole)
        self._detail_loc = None
        blk = self._atl_block(aid) if atype == 'image' else None
        ident = self._identity(aid) if atype == 'image' else None
        head = self._identity_block(ident)
        if blk is not None:
            # ATL 块形式：详情 = 块体原文 + 行号（`app.beats.asset_detail`
            # 已经把"读不到"的原因写成了一句人话）。
            self._detail.setText(
                head + f'[image] {aid}\n\n'
                + (P.detail(self._registry, atype, aid, self._project_root)
                   or '（无更多信息）'))
            if blk.found and blk.file:
                self._detail_loc = (blk.file, blk.line)
            self._open_btn.setEnabled(bool(self._detail_loc))
            return
        if atype == 'image' and not self._image_path(aid):
            self._detail.setText(
                head + f'[image] {aid}\n\n'
                f'⚠ 资源表里登记了这一条，但按它的 Path 在项目里找不到图片文件。\n'
                f'（不是"还在加载"—— 这一格永远不会有图。）')
            self._open_btn.setEnabled(False)
            return
        text = P.detail(self._registry, atype, aid, self._project_root)
        self._detail.setText(head + f'[{atype}] {aid}\n\n'
                             f'{text or "（无更多信息）"}')
        self._open_btn.setEnabled(False)

    def _open_source(self):
        """「打开源文件」：用系统默认代码编辑器打开并定位到块头那一行。"""
        loc = self._detail_loc
        if not loc:
            return
        from storygraph.graph.editor_utils import open_file_at_line
        open_file_at_line(loc[0], loc[1])

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
        if self._want_spec and atype == 'image':
            res = self._pick_result(aid)
            if res is not None and not res.ok:
                # 理论上「确定」已经置灰；双保险：真按了也不许关窗、不许写。
                self._update_pick_hint()
                self._pick_hint.setVisible(True)
                self._pick_hint.setText(f'⚠ 不能选这一张：{res.message}')
                self._pick_hint.setProperty('role', 'warn')
                self._repolish(self._pick_hint)
                return
            self.pick_result = res
            if res is not None and res.spec:
                self.image_spec = res.spec
            else:
                self.image_spec = P.image_spec_of(aid, self._row(aid) or {})
            # 选了预览图/头像时：selection 记**真正要用的那一张**（真立绘），
            # 这样调用方拿到的东西和 image_spec 一致。
            if res is not None and res.replaced_to:
                self.selection = (atype, res.replaced_to)
                self.replaced_message = res.message or '已换成对应的真立绘'
            else:
                self.selection = (atype, aid)
        else:
            self.selection = (atype, aid)
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
               project_root=None, purpose: str = '') -> str:
    """
    选一张**立绘/背景图**，返回可写进卡片的 image spec（取消返回 ''）。

    这是主窗口与立绘网格直接调用的入口。

    `purpose` 给初始用途过滤（`'sprite'` / `'立绘'` / `'bg'` / `'preview'`…），
    例如立绘网格的"加一张立绘"用 `purpose='sprite'`，一打开就只看见立绘。
    """
    if registry is None:
        return ''
    dlg = AssetPickerDialog(parent, registry=registry,
                            project_root=project_root, current=current,
                            types=['image'], want_image_spec=True,
                            title='选择立绘 / 背景图', purpose=purpose)
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


__all__ = ['AssetPickerDialog', 'pick_image', 'pick_asset',
           'PURPOSE_ALL', 'PURPOSE_SPRITE', 'PURPOSE_BG', 'PURPOSE_CG',
           'PURPOSE_UI', 'PURPOSE_PREVIEW', 'PURPOSE_OTHER', 'PURPOSES',
           'KIND_LABELS', 'SOURCE_LABELS',
           'purpose_label', 'purpose_of', 'pick_context', 'identity_of',
           'identity_map', 'human_label', 'identity_tooltip']
