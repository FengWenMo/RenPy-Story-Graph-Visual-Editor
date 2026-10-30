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
         'mapping': dict, 'all_rows': dict[str, dict], 'dimensions': dict|None}

    · `known_tags`：characters 表「立绘 tag」+ 映射 `characters` + 引擎事实
      里带属性的 tag（三路合并，来自项目自己的数据）；
    · `all_rows`：`registry.images`（`{asset_id: row}`）—— 副本检测要用"另一行"；
    · `mapping`：项目根 / `game/` 下的 `storygraph.mapping.json` **或**
      `storygraph.mapping.xlsx`（同目录 Excel 优先，见 `assets/mapping_excel.py`）；
      没有 = `{}`；
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


def identity_of(registry, asset_id: str, row: Optional[dict] = None, *,
                project_root=None, context=None) -> Identity:
    """一条图片资产的身份（UI 的用途过滤 / 人话名都用它）。"""
    known_tags, known_chars, mapping, rows = _context_parts(context)
    if context is None:
        known_tags, known_chars, mapping, rows = _context_parts(
            project_context(registry, str(project_root or '')))
    aid = str(asset_id or '').strip()
    row = row or (rows or {}).get(aid) or {}
    if not row:
        return Identity(kind='unknown', source='unknown', showable=False)
    rel = normalize_relpath(row.get('Path') or '', str(project_root or ''))
    return resolve_image(row, relpath=rel, known_tags=known_tags,
                         known_chars=known_chars, mapping=mapping,
                         rows=rows, copy_index=build_copy_index(rows))


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
                          project_root=root, copy_index=copy_index)


def resolve_pick(asset_id: str, row: Optional[dict], *,
                 project_root: str = '', known_tags=frozenset(),
                 known_chars: Optional[dict] = None, mapping=None,
                 all_rows=None, dimensions=None, registry=None,
                 copy_index=None) -> PickResult:
    """
    "选中这张图，到底能不能加、加什么" → `PickResult`（契约 §1.1b）。

    行为（用户 2026-10-30 抱怨的"选预览图写出非法 show"就断在这里）：

      · 能直接 `show` 的图（真立绘 / 背景 / CG / 界面）→ 给规范 spec；
      · **预览图 / 头像** → 自动找同 tag+属性的真立绘：找到就换成它并
        在 `message` 里说明"已用对应的立绘 xxx"；找不到 → `ok=False` + 原因；
      · 认不出的图（`unknown`）→ `ok=False` + 一句话（不写非法 show）；
      · 非角色图层但说不出名字 → `ok=False` + 一句话。

    `registry` 给了就用 `pick_context` 自动补齐 `known_tags / known_chars /
    mapping / all_rows`（UI 可以只传 registry + project_root）。
    """
    aid = str(asset_id or '').strip()
    if registry is not None:
        ctx = pick_context(registry, project_root, mapping=mapping,
                           dimensions=dimensions)
        if not known_tags:
            known_tags = ctx['known_tags']
        if known_chars is None:
            known_chars = ctx['known_chars']
        if mapping is None:
            mapping = ctx['mapping']
        if all_rows is None:
            all_rows = ctx['all_rows']
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
                          rows=rows, copy_index=copy_index)

    if ident.showable:
        spec = spec_for_identity(ident, row)
        if spec:
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
                mapping=mapping, rows=rows, copy_index=copy_index)
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


__all__ = ['RegistrySource', 'groups', 'groups_with_plain', 'search',
           'detail', 'thumbnail_path', 'row_of', 'image_spec_of',
           'PickResult', 'pick_context', 'identity_of', 'identities',
           'resolve_pick']
