"""
storygraph.app.picker
=====================
**资产选择器的语义层**（框架无关）—— 界面只负责把结果画成网格。

为什么单独一层
--------------
"该给策划看什么、按什么分组、默认选哪个"是**产品判断**，不是画控件：

  · 按**看图的人怎么找**分组（立绘：<角色> / 背景 / CG / 音乐 / 音效 / 语音），
    而不是按数据表怎么存；
  · 图片要给出**真实缩略图路径**（立绘在 `game/images/<tag>/<attr>.png`）；
  · 选中项回填的是**image spec**（`ling happy`），不是资产 ID
    （`ling_happy`）—— 两者差一个字，写错就是"图不存在"。

资产身份（2026-10-30 用户 P0：选预览图写出非法 `show refusing`）
----------------------------------------------------------------
"这张图是谁"的唯一真源是 `storygraph.assets.asset_identity`（纯函数）。
本模块只做**装配与决策**：

  · `pick_context()`：一次装好 `known_tags / known_chars / mapping /
    all_rows / dimensions`，可直接 `**` 展开给 `resolve_pick`；
  · `identity_of()` / `identities()`：给 UI 的用途过滤（按 `Identity.kind`）；
  · `resolve_pick()`：能 show 的直接出 spec；预览图/头像自动换成真立绘
    （换不到就**拒绝**并给一句话）；认不出**不猜**。

这些都放在这里，Qt 版与 tkinter 版共用同一份；换前端不用改语义。

**禁止**在本模块里 import 任何 UI 框架。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from storygraph.app import beats as B
from storygraph.core.vocabulary import ASSET_TYPE_ATTR
from storygraph.assets.asset_identity import (
    Identity,
    asset_id_of,
    build_copy_index,
    find_real_sprite,
    normalize_relpath,
    project_context,
    resolve_image,
    resolve_images,
    spec_for_identity,
    spec_tag_problem,
)


class RegistrySource:
    """
    把"一个 registry（+ 项目根）"适配成 `AssetCompletion` 需要的"engine"。

    `AssetCompletion` 只用 `engine.get_registry()`；而 `asset_preview_path`
    还要 `engine.project_root` 才能把资源表里的相对路径拼成绝对路径
    （否则缩略图永远显示不出来）。所以这里两个都提供 ——
    不需要真的有一个 AssetEngine，无头测试也因此变得容易。
    """

    def __init__(self, registry=None, project_root=None):
        self._registry = registry
        self.project_root = project_root

    def get_registry(self):
        return self._registry

    # `app.asset_panel.AssetPanelData` 要求传入对象带这两个查询（历史上只有
    # `AssetEngine` 有）。实现是纯函数、只看 registry，所以这里转发即可 ——
    # 于是"资产统计 / 未使用资产 / 断裂引用"在**没有引擎**的 Qt 会话里也能用。
    def find_unused_assets(self) -> list:
        from storygraph.assets.asset_registry import unused_assets
        return unused_assets(self._registry)

    def find_broken_references(self, include_transforms: bool = False) -> list:
        from storygraph.assets.asset_registry import broken_references
        return broken_references(self._registry, include_transforms)


@dataclass(frozen=True)
class PickResult:
    """
    "选中一张图"的完整结论（契约 §1.1b）。

    · `spec`：写回卡片的那段 image spec（`ling happy`）；拒绝时为空；
    · `identity`：最终采用的资产身份（换了真立绘时是**真立绘**的身份）；
    · `ok=False`：**不能加**，`message` 是给人看的一句话原因；
    · `replaced_from/to`：选了预览图/头像、被自动换成真立绘时，
      原资产 ID / 换成的真立绘资产 ID（用于提示条与审计）；
    · `message`：提示条文案（空串 = 直接可用，无需说明）。
    """

    spec: str = ''
    identity: Optional[Identity] = None
    ok: bool = True
    replaced_from: str = ''
    replaced_to: str = ''
    message: str = ''


def groups(registry, limit: int = 3000, project_root=None) -> dict:
    """分类候选：`{类别名: [(asset_type, asset_id), ...]}`（可分组的唯一真源）。"""
    if registry is None:
        return {}
    return B.categorize_assets(RegistrySource(registry, project_root),
                               limit=limit)


#: 非图片类型 → （资源表字段名, 中文名）。
#: 它们**没有分类组**（分类是给 2828 张图用的），所以各自成一组。
PLAIN_ASSET_TYPES = (('music', 'music', '音乐'),
                     ('sound', 'sounds', '音效'),
                     ('voice', 'voices', '语音'),
                     ('transform', 'transforms', '变换'),
                     ('character', 'characters', '角色'))


def groups_with_plain(registry, types=None, project_root=None,
                      limit: int = 3000) -> dict:
    """
    分类组（图片那套）**加上**音频 / 变换 / 角色各自的整组。

    为什么必须补这一层（用户 2026-10-05）：

    > 音乐跟语音的选择没有绑定到音乐上……选择按钮全是立绘……根本就没有音乐

    `groups()` 走的是 `categorize_assets`，而它**只按图片的 Category/Tag 分组** ——
    音乐 / 音效 / 语音 / 变换 / 角色根本不在返回结果里。于是选择器（分类列表 +
    "全部"）里永远只有立绘：想选音乐要么搜到零条，要么看到一屏立绘。

    `types` 给了就只补用户要的那几类（`pick_asset` 会传）。
    """
    out = dict(groups(registry, limit=limit, project_root=project_root))
    if registry is None:
        return out
    if types and 'image' not in types:
        # 只想要音频/变换时，把图片分类组去掉：留着它们只会让人点进一个
        # 空列表（"怎么全是立绘/怎么一条都没有"就是这么来的）。
        out = {k: v for k, v in out.items()
               if not any(t == 'image' for t, _ in (v or []))}
    # 已经出现过的 (类型, id)：`categorize_assets` 有时**已经**给了音乐/角色
    # 之类的分组，再补一遍就会出现"同一首歌列两次"（合成资源表实测 3 条变 6 条）。
    seen = {(t, a) for rows in out.values() for (t, a) in (rows or [])}
    for asset_type, field, label in PLAIN_ASSET_TYPES:
        if types and asset_type not in types:
            continue
        rows = getattr(registry, field, None) or {}
        if not rows:
            continue
        missing = [(asset_type, aid) for aid in sorted(rows.keys())
                   if (asset_type, aid) not in seen]
        if not missing:
            continue                         # 已经有这一组了
        name = f'{label}（全部）'
        while name in out:
            name += '＋'
        out[name] = missing
        seen.update(missing)
    return out


# ---------------------------------------------------------------------------
#  另一种口径：**按文件夹**（用户 2026-10-30）
# ---------------------------------------------------------------------------

#: 资源表里"文件路径"可能用的列名。`Path` 是资源表自己的列（唯一主用法），
#: 其余是引擎/映射那边见过的写法 —— 都认，认不出就当"没有路径"（不猜）。
FOLDER_PATH_KEYS = ('Path', 'path', 'File', 'file', 'Filename', 'filename')

#: 两条兜底分组：文件直接在 `game/` 下 / 这一行根本没说文件在哪。
ROOT_GROUP = '（根目录）'
NO_PATH_GROUP = '（没有路径）'

#: Ren'Py 项目目录名。资源表的 `Path` 有两种真实写法（`game/images/x.png`
#: 与 `images/x.png` —— 后者相对 `game/`，见 `asset_identity.normalize_relpath`
#: 的 docstring）。按文件夹分组时把这一段去掉，两种写法才落到同一个文件夹。
GAME_DIR_SEGMENT = 'game'


def folder_of_row(row, project_root=None) -> Optional[str]:
    """
    一条资产**所在文件夹**（相对 `game/` 的 POSIX 路径）。

    返回值有三种，别混：

      · `'images/ling'` —— 正常文件夹；
      · `''`            —— 文件**直接躺在 `game/` 下**（没有文件夹）；
      · `None`          —— 这一行**没写文件路径**（不是错误：角色/变换这类
        本来就没有文件，资源表里也不是每行都填 `Path`）。

    只做字符串归一（`normalize_relpath` 是纯函数、**不 stat 磁盘**），
    所以两三千行也不会卡住 UI 线程。
    """
    raw = ''
    for key in FOLDER_PATH_KEYS:
        val = (row or {}).get(key) if isinstance(row, dict) else None
        if isinstance(val, str) and val.strip():
            raw = val.strip()
            break
    if not raw:
        return None
    rel = normalize_relpath(raw, str(project_root or ''))
    parts = [p for p in rel.split('/') if p]
    if not parts:
        return None
    if len(parts) > 1 and parts[0].lower() == GAME_DIR_SEGMENT:
        parts = parts[1:]
    return '/'.join(parts[:-1])


def _folder_prefixes(folder: str) -> list:
    """`'a/b/c'` → `['a', 'a/b', 'a/b/c']`（含每一层祖先）。"""
    parts = [p for p in str(folder or '').split('/') if p]
    return ['/'.join(parts[:i + 1]) for i in range(len(parts))]


def _folder_sort_key(name: str):
    """文件夹排序：根目录最前 → 逐段按名字（父在子前）→ 没有路径的垫底。"""
    if name == ROOT_GROUP:
        return (0, ())
    if name == NO_PATH_GROUP:
        return (2, ())
    return (1, tuple(seg.lower() for seg in str(name).split('/')))


def folder_groups(registry, project_root=None, *, types=None, limit: int = 3000,
                  include_subfolders: bool = True) -> dict:
    """
    按**文件所在文件夹**分组：`{文件夹: [(asset_type, asset_id), ...]}`。

    用户 2026-10-30（这是"分类"之外的**第二种口径**，两种要能随时切）：

    > 文件筛选与分类方面……要提供两种分类方式，一种就是按文件夹分类排序，
    > 另一种就是原来的按图片 / 背景 / UI 等那种分类排序……这两种方式要能够
    > 随时切换

    口径与 `groups()` **并列、不互相覆盖**：`groups()` 回答"这是什么"
    （立绘/背景/UI…），这里回答"它躺在哪个文件夹"。所以两者都保留，
    界面给一个开关。

    `include_subfolders=True`（默认）时，**每一层都含它下面所有子文件夹的图**
    —— 点 `images` 就看到 `images/**` 全部。这样每一层都可点、都不会是
    空列表（真实项目里 `game/images` 下面常常只有子文件夹、一个文件都没有）。
    要"只看这一层文件"传 `False`。

    `limit` 是行数上限（和 `groups()` 一样，防极端项目把内存吃满）。
    """
    if registry is None:
        return {}
    root = str(project_root or '')
    wanted = list(types) if types else list(ASSET_TYPE_ATTR)
    out: dict = {}
    no_path: list = []
    seen = set()
    #: `小写路径 → 实际写法`。Windows 上 `GUI/` 与 `gui/` 是**同一个文件夹**
    #: （真项目里就同时出现过），分组时合成一行，显示先用到的那个写法。
    canon: dict = {}

    def _add(key: str, pair):
        real = canon.setdefault(key.lower(), key)
        out.setdefault(real, []).append(pair)

    for atype in wanted:
        attr = ASSET_TYPE_ATTR.get(atype)
        table = getattr(registry, attr, {}) if attr else {}
        for aid in sorted(table or {}):
            if len(seen) >= limit:
                break
            pair = (atype, aid)
            if pair in seen:
                continue
            seen.add(pair)
            folder = folder_of_row((table or {}).get(aid) or {}, root)
            if folder is None:
                no_path.append(pair)
                continue
            keys = _folder_prefixes(folder) if include_subfolders else [folder]
            if not keys:                       # 直接在 game/ 下
                keys = [ROOT_GROUP]
            for key in keys:
                _add(key, pair)
    if no_path:
        # 宁可多一组"没有路径"，也不让这些资产在文件夹口径下**凭空消失**
        # （角色 / 变换这类本来就没有文件，用户仍要能选到）。
        out[NO_PATH_GROUP] = no_path
    return {k: out[k] for k in sorted(out, key=_folder_sort_key)}


def search(registry, query: str = '', asset_types=None, limit: int = 50,
           project_root=None) -> list:
    """按前缀/模糊搜索：`[(asset_type, asset_id), ...]`。"""
    if registry is None:
        return []
    return B.search_assets(RegistrySource(registry, project_root), query=query,
                           asset_types=asset_types, limit=limit)


def detail(registry, asset_type: str, asset_id: str, project_root=None) -> str:
    """一条资产的详情文字（给选择器右侧/悬浮提示用）。"""
    if registry is None:
        return ''
    return B.asset_detail(RegistrySource(registry, project_root),
                          asset_type, asset_id)


def thumbnail_path(registry, asset_type: str, asset_id: str,
                   project_root=None):
    """
    这条资产有没有可显示的图片路径（没有就 None —— 界面显示文字兜底）。
    """
    if registry is None or asset_type != 'image':
        return None
    return B.asset_preview_path(RegistrySource(registry, project_root),
                                asset_type, asset_id)


def row_of(registry, asset_type: str, asset_id: str, project_root=None):
    """一条资产的原始行（写回 image spec 时要用它的 Tag / Attributes）。"""
    if registry is None:
        return None
    return B.asset_row(RegistrySource(registry, project_root),
                       asset_type, asset_id)


# ---------------------------------------------------------------------------
#  身份解析的"项目上下文"（唯一装配处：UI 不许自己读 characters.xlsx）
# ---------------------------------------------------------------------------

def pick_context(registry=None, project_root=None, *, mapping=None,
                 dimensions=None) -> dict:
    """
    装配 `resolve_pick` / `resolve_image` 的关键字参数（**可直接 `**` 展开**）：

        {'known_tags': frozenset[str], 'known_chars': dict,
         'mapping': dict, 'all_rows': dict[str, dict], 'dimensions': dict|None,
         'sprite_words': dict}

    · `known_tags`：characters 表「立绘 tag」+ 映射 `characters` + 引擎事实
      里带属性的 tag + 项目 `layeredimage` 声明过的 tag（四路合并，来自项目
      自己的数据）；
    · `all_rows`：`registry.images`（`{asset_id: row}`）—— 副本检测要用"另一行"；
    · `mapping`：项目根 / `game/` 下的 `storygraph.mapping.json` **或**
      `storygraph.mapping.xlsx`（同目录 Excel 优先，见 `assets/mapping_excel.py`）；
      没有 = `{}`；
    · `sprite_words`：`{tag: {图片文件名: 声明的属性名}}` —— 选中立绘时用它
      写出项目 `layeredimage` **认的那个属性词**（`stare.png` 在 `qing` 名下
      要写成 `curious`）；
    · `dimensions`：可选 `{asset_id 或相对路径: (w, h)}`，只有"换真立绘时
      优先像素更大"会用到；不传不影响正确性（只是少一条排序依据）。

    纯读：不写盘、不 import Qt。
    """
    ctx = project_context(registry, str(project_root or ''), mapping=mapping)
    return {
        'known_tags': ctx['known_tags'],
        'known_chars': ctx['known_chars'],
        'mapping': ctx['mapping'],
        'all_rows': ctx['rows'],
        'dimensions': dimensions,
        'sprite_words': ctx.get('sprite_words') or {},
    }


def _context_parts(context) -> tuple:
    """`pick_context(...)` 或 `project_context(...)` 的返回 → 四个常用片段。"""
    ctx = context or {}
    rows = ctx.get('rows')
    if not isinstance(rows, dict):
        rows = ctx.get('all_rows')
    return (ctx.get('known_tags') or frozenset(),
            ctx.get('known_chars') or {},
            ctx.get('mapping') if isinstance(ctx.get('mapping'), dict) else {},
            rows if isinstance(rows, dict) else {})


def _sprite_words_of(context) -> dict:
    """
    上下文里的**声明属性词表**（`{tag: {图片文件名: 属性名}}`）。

    为什么单取一个函数而不是塞进 `_context_parts`：那四个片段是"每条判定都
    要用"的，这张表只有"要写出一条 `show`"时才用得上（`spec_for_identity`
    那一步）；没有它也能判定，只是属性词会退回文件名。
    """
    words = (context or {}).get('sprite_words') if isinstance(context, dict) else None
    return words if isinstance(words, dict) else {}


def identity_of(registry, asset_id: str, row: Optional[dict] = None, *,
                project_root=None, context=None) -> Identity:
    """一条图片资产的身份（UI 的用途过滤 / 人话名都用它）。"""
    if context is None:
        context = project_context(registry, str(project_root or ''))
    known_tags, known_chars, mapping, rows = _context_parts(context)
    aid = str(asset_id or '').strip()
    row = row or (rows or {}).get(aid) or {}
    if not row:
        return Identity(kind='unknown', source='unknown', showable=False)
    rel = normalize_relpath(row.get('Path') or '', str(project_root or ''))
    return resolve_image(row, relpath=rel, known_tags=known_tags,
                         known_chars=known_chars, mapping=mapping,
                         rows=rows, copy_index=build_copy_index(rows),
                         sprite_words=_sprite_words_of(context))


def identities(registry, project_root=None, *, context=None) -> dict:
    """
    全部图片行 → `{asset_id: Identity}`（**一次算全量**，供分组/用途过滤）。

    UI 不要逐条调 `resolve_image`：那样每条都会重建一次副本索引。
    """
    root = str(project_root or '')
    if context is None:
        context = project_context(registry, root)
    known_tags, known_chars, mapping, rows = _context_parts(context)
    copy_index = context.get('copy_index') if isinstance(context, dict) else None
    if not isinstance(copy_index, dict):
        copy_index = build_copy_index(rows)
    return resolve_images(rows, known_tags=known_tags,
                          known_chars=known_chars, mapping=mapping,
                          project_root=root, copy_index=copy_index,
                          sprite_words=_sprite_words_of(context))


def _context_or_none(registry, project_root, mapping, dimensions, *,
                     known_tags, known_chars, all_rows, sprite_words, row):
    """
    需要现建上下文时才建（`None` = 不用建 / 建不了）。

    ⚠ **判据是"真缺东西"**（2026-11-03）：`pick_context` 里有两件重活 ——
    `build_copy_index`（1979 行的两两比较）与 `load_layer_model_cached`
    （扫全项目 `.rpy`），实测 97 ms/次。资源选择器是**逐张**调 `resolve_pick`
    的；从前只要给了 `registry` 就无条件重建，1979 张图 = 3.2 分钟纯等待
    （改完 2.4 ms/张，40×）。
    """
    if registry is None:
        return None
    need = (not known_tags) or (known_chars is None) or (mapping is None) \
        or (all_rows is None) or (sprite_words is None) or (row is None)
    if not need:
        return None
    return pick_context(registry, project_root, mapping=mapping,
                        dimensions=dimensions)


def resolve_pick(asset_id: str, row: Optional[dict], *,
                 project_root: str = '', known_tags=frozenset(),
                 known_chars: Optional[dict] = None, mapping=None,
                 all_rows=None, dimensions=None, registry=None,
                 copy_index=None, sprite_words=None,
                 context=None) -> PickResult:
    """
    "选中这张图，到底能不能加、加什么" → `PickResult`（契约 §1.1b）。

    行为（用户 2026-10-30 抱怨的"选预览图写出非法 show"就断在这里）：

      · 能直接 `show` 的图（真立绘 / 背景 / CG / 界面）→ 给规范 spec；
      · **预览图 / 头像** → 自动找同 tag+属性的真立绘：找到就换成它并
        在 `message` 里说明"已用对应的立绘 xxx"；找不到 → `ok=False` + 原因；
      · 认不出的图（`unknown`）→ `ok=False` + 一句话（不写非法 show）；
      · 非角色图层但说不出名字 → `ok=False` + 一句话。

    `registry` 给了就用 `pick_context` 自动补齐 `known_tags / known_chars /
    mapping / all_rows / sprite_words`（UI 可以只传 registry + project_root）。

    `sprite_words`（项目 `layeredimage` 的属性反查表）决定立绘那条 spec 里的
    **属性词怎么写**：`stare.png` 在 `qing` 名下要写成 `curious`，否则
    (`show qing stare`) 引擎不认 —— 又是一个"看着像对、其实非法"的 show。
    """
    aid = str(asset_id or '').strip()
    if context is None:
        context = _context_or_none(
            registry, project_root, mapping, dimensions,
            known_tags=known_tags, known_chars=known_chars,
            all_rows=all_rows, sprite_words=sprite_words, row=row)
    # ⚠ **有 context 就用，不管有没有 registry**（2026-11-03 判据抓到）：
    # 调用方可能提前把上下文备好（选择器就是这么做的），而 registry 只在
    # "要不要现建一份"时才有用。从前这里要求两者同时给，于是
    # `resolve_pick(..., context=ctx)` 单独用时上下文被**静默忽略**，
    # 同一条图得出两个不同结论。
    if context is not None:
        ctx = context
        if not known_tags:
            known_tags = ctx['known_tags']
        if known_chars is None:
            known_chars = ctx['known_chars']
        if mapping is None:
            mapping = ctx['mapping']
        if all_rows is None:
            all_rows = ctx['all_rows']
        if sprite_words is None:
            sprite_words = ctx['sprite_words']
        if copy_index is None:
            copy_index = ctx.get('copy_index')
        if row is None:
            row = (all_rows or {}).get(aid)
    if not row:
        return PickResult(ok=False, message='找不到这条图片资源（资源表里没有它）')
    aid = aid or asset_id_of(row)
    rows = all_rows if all_rows is not None else {aid: row}
    if copy_index is None:
        copy_index = build_copy_index(rows)
    root = str(project_root or '')
    rel = normalize_relpath(row.get('Path') or '', root)
    ident = resolve_image(row, relpath=rel, known_tags=known_tags,
                          known_chars=known_chars, mapping=mapping,
                          rows=rows, copy_index=copy_index,
                          sprite_words=sprite_words)

    if ident.showable:
        spec = spec_for_identity(ident, row)
        if spec:
            # **生成器先过一遍校验器**（2026-11-03，地狱测试 I7）：
            # 映射里 `char` 填成显示名（「青 base」）时，这里就不许产出 ——
            # 否则 `show 青 base` 是一句引擎会报错的话，而旁边那条提示
            # 还会说"这不行"，同一件事两个结论。
            bad = spec_tag_problem(spec, known_tags)
            if bad:
                return PickResult(identity=ident, ok=False, message=bad)
            return PickResult(spec=spec, identity=ident, ok=True)
        return PickResult(identity=ident, ok=False,
                          message='这张图认不出该写什么名字（可在资源映射里登记）')

    if ident.kind in ('preview', 'avatar'):
        real = find_real_sprite(ident, rows, dimensions=dimensions)
        if real is not None:
            real_id = asset_id_of(real)
            real_ident = resolve_image(
                real, relpath=normalize_relpath(real.get('Path') or '', root),
                known_tags=known_tags, known_chars=known_chars,
                mapping=mapping, rows=rows, copy_index=copy_index,
                sprite_words=sprite_words)
            spec = spec_for_identity(real_ident, real)
            if not spec:
                spec = image_spec_of(real_id, real)
            label = (real_ident.display or real_ident.tag or spec
                     or real_id)
            what = '角色预览图' if ident.kind == 'preview' else '角色头像'
            return PickResult(
                spec=spec, identity=real_ident, ok=True,
                replaced_from=aid, replaced_to=real_id,
                message=f'这是{what}，已用对应的立绘「{label}」'
                        f'（{spec}）')
        what = '角色预览图' if ident.kind == 'preview' else '角色头像'
        return PickResult(
            identity=ident, ok=False, replaced_from=aid,
            message=f'这是{what}，不是能直接上舞台的立绘；'
                    f'没找到对应的真立绘（可在资源映射里登记）')

    if ident.kind == 'unknown':
        return PickResult(
            identity=ident, ok=False,
            message='这张图认不出是哪个角色（可在资源映射里登记）')
    return PickResult(
        identity=ident, ok=False,
        message='这张图说不出该写什么名字（不是能直接上舞台的立绘；'
                '可在资源映射里登记）')


def image_spec_of(asset_id: str, row: Optional[dict] = None, *,
                  expression: str = '', position: str = '') -> str:
    """
    资产 ID + 行数据 → 写回卡片要用的 **image spec**。

    例：`ling_happy` + `{Tag: ling, Attributes: happy}` → `ling happy`。

    规则（与编译器口径一致）：
      · 有 Tag 就用 Tag 开头（`ling`），后面接 Attributes；
      · 没有 Attributes 就只用 Tag（`show ling`）；
      · 都没有就原样返回 ID（宁可写 ID，也不凭空编一个 tag）；
      · expression/position 给了就覆盖/追加（`@位置`）。
    """
    row = row or {}
    tag = str(row.get('Tag') or '').strip()
    attrs = str(row.get('Attributes') or '').strip()
    aid = str(asset_id or '').strip()

    if tag:
        parts = [tag] + ([expression] if expression else
                         (attrs.split() if attrs else []))
        spec = ' '.join(p for p in parts if p)
    else:
        spec = aid
        if expression:
            first = spec.split()[0] if spec else ''
            rest = spec.split()[1:] if spec else []
            spec = ' '.join([first, expression] + rest) if first else expression
    spec = spec.strip()
    pos = str(position or '').strip()
    if spec and pos and '@' not in spec:
        spec = f'{spec}@{pos}'
    return spec


__all__ = ['RegistrySource', 'groups', 'groups_with_plain', 'folder_groups',
           'folder_of_row', 'NO_PATH_GROUP', 'ROOT_GROUP', 'search',
           'detail', 'thumbnail_path', 'row_of', 'image_spec_of',
           'PickResult', 'pick_context', 'identity_of', 'identities',
           'resolve_pick']
