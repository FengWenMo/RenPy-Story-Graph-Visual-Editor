# -*- coding: utf-8 -*-
"""
storygraph.app.asset_catalog
============================
资源侧**只读查询**的服务层（`AssetCatalog`）。

为什么有它（2026-11-01，长期目标第 (5) 条"EditorSession 服务化第一刀"）
--------------------------------------------------------------------------
`AssetRegistryMixin` 的 26 个方法混着三件事：

  ① **打开项目时的副作用**（建表 / 补登 / 重读表）—— 必须留在会话里；
  ② **从 registry 读**：候选、角色名、词汇表 —— 纯查询，没有副作用；
  ③ 改表 / 改名 / 写盘 —— 另一刀，还没切。

这一刀只搬 ②。判据是"**只拿 registry + project_root 能不能回答**"：
能就搬进来。于是它可以

    AssetCatalog(fake_registry, project_root='/tmp/x')

直接建出来单测 —— 不用造 `EditorSession`、不用 Qt、不用真的资源表文件
（`tests/test_asset_catalog.py` 里就是这么做的）。

**刻意不搬**：`registry()` 的回落链（`_loaded_registry` → provider）留在宿主
—— 那是"资源从哪来"，不是"资源是什么"。服务只接收**已经拿到**的那一份；
拿不到就是 `None`，服务回答"空"而不是自己去要。
"""
from __future__ import annotations

from storygraph.core.safeguard import swallow


__all__ = ['ASSET_KINDS', 'AssetCatalog']


#: 资产种类 → 资源表字段名（与 `_write_candidate_sheets` 的映射同源）。
#: 会话侧 `AssetRegistryMixin.ASSET_KINDS` 指向同一个 dict。
ASSET_KINDS = {'character': 'characters', 'image': 'images',
               'transform': 'transforms', 'music': 'music',
               'sound': 'sounds', 'voice': 'voices'}


class AssetCatalog:
    """
    资源表的只读视图：说话人 / 人话名 / 词汇表 / 下拉候选。

    `registry` 是 `assets.asset_registry.Registry`（或任何有同名属性的对象
    —— 测试里就用一个 `SimpleNamespace`）。`None` = 还没打开项目，
    每个方法都回答"空"，不抛。
    """

    #: 与模块级常量同一份（类上留一份，调用方两处写法都认）
    ASSET_KINDS = ASSET_KINDS

    def __init__(self, registry=None, project_root=None):
        self._registry = registry
        self.project_root = project_root
        #: `source()` 的缓存：`RegistrySource` 很轻，但 `AssetPanelData` 会
        #: 反复问它（一次概览里六七个查询），别每次新建一个。
        self._source = None
        #: `AssetIndex` 缓存（`resolve_references` 用；真实项目建一次 ~0.4 s）。
        #: `image_path` 会被卡片**逐张**调用，绝不能每次重建。
        self._index = None

    @property
    def registry(self):
        """构造时拿到的那份 registry（`None` = 还没打开项目）。"""
        return self._registry

    def source(self):
        """
        当前资源表 → "看起来像 engine"的适配对象（`app.picker.RegistrySource`）。

        `app.beats` 那批检索函数历史上收的都是 **engine**（用
        `engine.get_registry()` 取表）；服务手上只有 registry，于是统一过一次
        适配 —— 与 `EditorSession.source()` 同一个对象、同一条理由，
        只是这一份归服务自己缓存。
        """
        if self._source is None:
            from storygraph.app.picker import RegistrySource
            self._source = RegistrySource(self._registry, self.project_root)
        return self._source

    def invalidate(self) -> None:
        """资源表换了 / 重新读过之后调用：把**索引**缓存作废（源对象也重来）。"""
        self._index = None
        self._source = None

    # ------------------------------------------------------------------
    # 缩略图路径（"这张立绘在磁盘上到底是哪个文件"）
    # ------------------------------------------------------------------

    def resolve_ids(self, name: str) -> list:
        """
        `名字 → 资产 ID` 列表（走唯一真源 `resolve_references`），带索引缓存。

        索引建一次就缓存起来（真实项目 3149 行，建一次 ~0.4 秒；`image_path`
        会被卡片反复调用，不能每次重建）。`invalidate()` 之后重建。
        """
        if self._registry is None:
            return []
        idx = self._index
        if idx is None:
            try:
                from storygraph.assets.asset_registry import AssetIndex
                idx = AssetIndex(self._registry)
                self._index = idx
            except Exception as e:                # noqa: BLE001
                swallow('app.asset_catalog.resolve_ids.index', e)
                return []
        try:
            from storygraph.assets.asset_registry import resolve_references
            return list(resolve_references(name, self._registry, idx))
        except Exception as e:                    # noqa: BLE001
            swallow('app.asset_catalog.resolve_ids', e)
            return []

    def composed_sprite_path(self, image_spec: str):
        """
        `ling close_happy base_swimsuit` → **叠好的那张图**的路径（叠不出来 None）。

        为什么要它（用户 2026-10-16）：
        > 我换了服装，但显然你的图片没有跟着随即切换……还是原来那张……不合理！

        根因不是"图没换"，是**解析出来就是同一个文件**：多属性那一支一次只试
        一个属性、命中即返回（当年为修"无头骑士"改的），于是第二维永远被吃掉。
        正确做法是按项目自己的 `layeredimage` 分组（身体件 + 脸件）叠起来 ——
        仓库里已经有这件（`app/layer_compose.compose_sprite`），只是**预览这条
        路没用上**（演出预览那条路 `stage.resolve_frame_assets` 早在用了）。

        ⚠ 两条诚实退化：
          · 项目没有 `layeredimage` 声明 / 认不出这个 tag → 返回 None，
            调用方走原来的单图路径（**老项目一个字节不变**）；
          · 只有一层（单图立绘）→ `compose_sprite` 直接返回原图，零成本。
        """
        root = getattr(self, 'project_root', None)
        if not root:
            return None
        try:
            parts = str(image_spec or '').split()
            if not parts:
                return None
            # ⚠ 一个词也照样试（`lan happy` 这种：表情有了、服装走引擎默认）——
            #   分层项目里"一个词"同样要叠出完整立绘，不能退回单张表情件
            #   （那正是当年"无头骑士"那个坑的另一面）。
            #   没有 `layeredimage` 声明的 tag（`cg cg03` / `bg room`）会在下面
            #   被 `model.has(tag)` 挡掉 → 老路，一个字都不改。
            from storygraph.story.layered_model import (load_layer_model_cached,
                                                        resolve_layers)
            model = load_layer_model_cached(root)
            tag, attrs = parts[0], parts[1:]
            if model is None or not getattr(model, 'images', None) \
                    or not model.has(tag):
                return None
            plan = resolve_layers(model, tag, attrs, root)
            if not getattr(plan, 'ok', False):
                return None
            from storygraph.app.layer_compose import compose_sprite
            path = compose_sprite(plan)
            return path if path is not None else None
        except Exception as e:                    # noqa: BLE001 - 合成不了走老路
            swallow('app.asset_catalog.composed_sprite_path', e)
            return None

    def image_path(self, image_spec: str):
        """
        立绘 image spec（`ling happy`）→ 磁盘上的真实图片路径（找不到 None）。

        **这是立绘缩略图的真源**：路径只在资源表的 `Path` 列里，
        而 `symbols` 里没有资源表 —— 早期前端拿 `symbols.registry` 去取，
        恒为 None，于是缩略图静默地永远加载不出来。
        """
        from storygraph.app.picker import thumbnail_path
        spec = str(image_spec or '').strip()
        if not spec or self._registry is None:
            return None
        try:
            # ⚠⚠ **分层合成优先**（2026-10-16 用户实测报的"换了服装图不变"）。
            #
            # 这个项目（以及 Ren'Py 的 `layeredimage` 写法）里立绘是**分层**的：
            # 身体件（`outfit`: base_swimsuit / base_arms_akimbo）与脸件
            # （`expressions`: close_happy …）各是一张图，**必须叠起来**才是完整立绘。
            #
            # 从前这里在"多个属性"那一支**一次只试一个词、命中即 return**，
            # 于是 `ling close_happy base_swimsuit` 永远命中 `close_happy.png`
            # ——换服装（那一个词）**结果必然不变**，user 看到的就是"图没跟着换"。
            # 现在先按项目自己的 `layeredimage` 结构叠（`compose_sprite` 有落盘缓存，
            # 键含**每一层**的路径与 mtime/size，所以换哪一维都会重合成）。
            composed = self.composed_sprite_path(spec)
            if composed is not None:
                return composed
            from storygraph.app.beats import lookup_image_id
            images = getattr(self._registry, 'images', {}) or {}
            parts = spec.split()
            if not parts:
                return None
            aid = None
            if len(parts) < 2:
                # ⚠ **下划线写法**（`ling_angry`）也要认（用户 2026-10-05：
                # "为什么你改一个表……还是会导致立绘不显示呢？？？"）。
                #
                # 卡片里存的是工具自己的写法（空格→下划线，见
                # `_normalize_image_name`），而 `image_path` 从前只认
                # `"ling angry"` 这种空格形式 → 卡片明明是 `ling_angry`，
                # 预览却恒为空。这里走**唯一真源**（引用解析），它顺带把
                # "某一行 ID 恰好叫 ling_angry 但没有 Tag"那种劫持也修掉了。
                for hit in self.resolve_ids(spec):
                    if hit in images:
                        aid = hit
                        break
                if aid is None:
                    return None
            else:
                tag, rest = parts[0], parts[1:]
                # `ling happy` → `ling_happy` 这类 ID，先在表里找准。
                # ⚠ 多属性时**先试表情件**（`show ye base_handdown happy` 的
                # `base_handdown` 只有身体没有头 → 无头骑士，2026-10-05）。
                from storygraph.app.beats import preferred_image_attrs
                if len(rest) > 1:
                    for attr in preferred_image_attrs(rest):
                        cand = lookup_image_id(images, tag, [attr])
                        if cand:
                            aid = cand
                            break
                if not aid:
                    aid = lookup_image_id(images, tag, rest) or \
                        '_'.join([tag] + rest)
            hit = thumbnail_path(self._registry, 'image', aid, self.project_root)
            if hit is None:
                # 表里查不到这个写法 → 照**脚本里的 `image` 声明**认（与演出预览
                # 同一条路，见 `app.stage.script_images`）。真实项目实测：
                # `bg bg11_0` 的别名只写在脚本里（`image bg bg11_0 = "…/bg11.png"`），
                # 不这么兜的话预览画得出来、卡片上却是一片空白。
                from storygraph.app.stage import image_index, resolve_stage_asset
                hit = resolve_stage_asset(spec, images, self.project_root,
                                          image_index(self.project_root))
            return hit
        except Exception as e:                    # noqa: BLE001
            swallow('app.asset_catalog.image_path', e)
            return None

    # ------------------------------------------------------------------
    # 资产概览（读，不改）
    # ------------------------------------------------------------------

    def overview(self, linker=None, top: int = 15) -> dict:
        """
        资产 / 角色的只读统计（资产面板那一套查询，界面用）。

        数据源是 `app.asset_panel.AssetPanelData`（框架无关的查询层）——
        界面**不重写**"按 category 分组 / 角色台词量排名"这类逻辑。
        `linker` 由宿主传（它要读脚本、要缓存，那属于会话 / 引擎，不是表）。

        返回 `{ok, statistics, categories, top_characters, undefined,
        unused_characters, unused_assets, broken, error}`。
        """
        from storygraph.app.asset_panel import AssetPanelData
        if self._registry is None:
            return {'ok': False, 'error': '还没有资源表 —— 先「📁 打开项目」',
                    'statistics': {}, 'categories': {}, 'top_characters': [],
                    'undefined': [], 'unused_characters': [],
                    'unused_assets': [], 'broken': []}
        panel = AssetPanelData(self.source())
        # 角色相关的查询走 linker（`AssetPanelData` 从 engine 上取）：
        # 没有它，"角色台词量 / 未定义角色 / 没出场的角色"永远是空的。
        if linker is not None:
            try:
                panel.engine.linker = linker
            except Exception:                 # noqa: BLE001
                swallow('app.asset_catalog.overview.linker')
        out = {'ok': True, 'error': ''}
        for key, fn in (('statistics', panel.get_statistics),
                        ('categories', panel.get_category_breakdown),
                        ('undefined', panel.get_undefined_characters),
                        ('unused_characters', panel.get_unused_characters),
                        ('broken', panel.get_broken_references),
                        ('unused_assets', lambda: panel.get_unused_assets())):
            try:
                out[key] = fn()
            except Exception as e:            # noqa: BLE001
                out[key] = {} if key in ('statistics', 'categories') else []
                out.setdefault('warnings', []).append(f'{key}: {e}')
        try:
            out['top_characters'] = panel.get_character_ranking()[:top]
        except Exception as e:                     # noqa: BLE001
            swallow('app.asset_catalog.overview.ranking', e)
            out['top_characters'] = []
        # **维度置信度**（2026-10-20）：资源表里的维度有多少是"表里写的"、
        # 多少是"按引擎事实/文件名猜的"。猜的那些是结构化的软肋，
        # 概览里必须能看见，不然"一维一格"会让人误以为全都可靠。
        try:
            from storygraph.assets.asset_dimensions import group_metadata
            rows = list((getattr(self._registry, 'images', {}) or {}).values())
            meta = group_metadata(rows)
            groups = sum(len(g) for g in meta.values())
            guessed = sum(1 for g in meta.values()
                          for entry in g.values() if entry.get('guessed'))
            out['dimension_confidence'] = {'groups': groups,
                                           'guessed': guessed}
        except Exception as e:                # noqa: BLE001 - 统计失败不影响概览
            swallow('app.asset_catalog.overview.dimensions', e)
            out['dimension_confidence'] = {}
        # 大列表只给前 N 条（界面是给人看的，不是 dump）
        for key in ('unused_assets', 'broken', 'undefined'):
            if isinstance(out.get(key), list) and len(out[key]) > 200:
                out[key] = out[key][:200]
        return out

    # ------------------------------------------------------------------
    # 说话人
    # ------------------------------------------------------------------

    def characters(self) -> list:
        """可用说话人 ID（排序）。"""
        try:
            return sorted((getattr(self._registry, 'characters', {}) or {}).keys())
        except Exception as e:                    # noqa: BLE001
            swallow('app.asset_catalog.characters', e)
            return []

    def character_labels(self) -> dict:
        """
        `{tag: 人话名}` —— 说话人下拉 / 参与角色列表显示用的那套名字。

        三档来源，**前面的盖后面的**（与词汇表同一条规矩）：

          1. 项目映射 `vocabulary` / `characters` 段里给的（第 4 条要的就是它）；
          2. 资源表里角色那行的 `name`（`describe_asset` 用的也是它）；
          3. 都没有 → 不出现（调用方原样显示 tag）。

        为什么不用 `Registry` 的 name 直接当唯一真源：它只覆盖"资产表里登记过
        的角色"，`narrator` / `n` 这种引擎自带的、或者脚本里就地 `define` 的
        角色根本不在表里；映射里却能随手写。两处都读，才不会有"这个角色
        怎么没翻译"。
        """
        out = {}
        try:
            for tag, row in (getattr(self._registry, 'characters', {}) or {}).items():
                name = str((row or {}).get('name') or '').strip() \
                    if isinstance(row, dict) else ''
                if name:
                    out[str(tag)] = name
        except Exception as e:                    # noqa: BLE001
            swallow('app.asset_catalog.character_labels', e)
        vocab = self.vocabulary()
        for term in vocab.of_kind('character'):
            shown = term.display()
            if shown and shown != term.id:
                out[term.id] = shown
        return out

    # ------------------------------------------------------------------
    # 词汇表
    # ------------------------------------------------------------------

    def vocabulary(self):
        """
        项目那份映射的**词汇表**（引擎写法 → 人话）。没有映射 → 空表。

        ⚠ 这是界面唯一该拿词汇表的地方：`window_card` / `window_assets` /
        参数列候选全都走它。从前 `window_card._vocabulary()` 自己读
        `mapping_cached(...)`，却把返回值当成"对象"用了（它其实是
        `(data, warnings, path)` 元组）→ 异常被吞掉 → **真机上词汇表永远是空的**，
        只有单测里注入的那份在生效。一处访问器就不可能有这个错。
        """
        from storygraph.assets import asset_mapping as AM
        from storygraph.assets import vocabulary as V

        root = str(self.project_root or '')
        if not root:
            return V.EMPTY_VOCABULARY
        data = {}
        try:
            data, _warnings, _path = AM.mapping_cached(root)
        except Exception as e:                    # noqa: BLE001 - 映射坏了不该拦界面
            swallow('app.asset_catalog.vocabulary.mapping', e)
        vocab = V.from_mapping(data)
        calls = []
        try:
            from storygraph.story.statement_registry import StatementRegistry
            calls = list(getattr(StatementRegistry.for_project(root), 'calls',
                                 None) or ())
        except Exception as e:                    # noqa: BLE001
            swallow('app.asset_catalog.vocabulary.calls', e)
        # 层号决定胜负（P0-2）：`mapping` 是命名域的正本，`calls.label` 是
        # 项目声明 —— 两者谁赢**写在 `core/authority.py`**，不看这里的书写
        # 顺序（从前 `plus()` 是"后来者优先"，写反了就静默反过来）。
        return V.Vocabulary.merged([
            ('calls', V.cue_terms(calls)),
            ('mapping', vocab.as_terms()),
        ])

    # ------------------------------------------------------------------
    # 下拉候选
    # ------------------------------------------------------------------

    def asset_choices(self, asset_type: str) -> list:
        """
        资源表 → 卡片下拉/选择器的候选：`[(值, 说明), ...]`（按值排序）。

        用户 2026-10-03：

        > 添加立绘，添加语音，添加音乐，全部都没有反应！
        > 我不是已经注册了资产吗？为何不能从中选取并导入呢？？？

        **值是"脚本里该写的那个东西"**：
          · image      → image spec（`ling happy` / `bg bg9_5`），与选择器一致
          · music/sound/voice → **资产 ID**（与 Excel 的候选表同源！真实路径
            由渲染器 `_render_audio_from_fields` 翻出来，查不到就写
            `# TODO: 缺少资源 …`，所以写 ID 是安全的）
          · transform/character → ID

        候选来源与 Excel 的下拉**同一份数据**（`registry.<复数>`），
        说明文字复用 `describe_asset` —— 不另造一套"什么算可选资产"。
        """
        field = ASSET_KINDS.get(str(asset_type or ''))
        if self._registry is None or not field:
            return []
        rows = getattr(self._registry, field, None) or {}
        out = []
        for aid in sorted(rows.keys()):
            row = rows.get(aid) or {}
            if asset_type == 'image':
                value = self.image_spec_of(aid, row)
            else:
                value = aid
            out.append((value, self.describe_asset(asset_type, row)))
        return out

    @staticmethod
    def image_spec_of(aid, row) -> str:
        """图片行 → image spec（`tag attr`）。复用 `app.picker`（选择器同源）。"""
        try:
            from storygraph.app import picker as P
            return P.image_spec_of(aid, row)
        except Exception as e:                # noqa: BLE001 - 选择器缺失也要导得出去
            # 退化成裸 id：导出表里这一格会从 `tag attr` 变成 id。
            # 不收窄类型 —— `P.image_spec_of` 是域函数，它能抛什么静态枚举不出来
            # （审计 §5.2）。但必须留痕，否则"选择器没装上"和"这行本来就只有 id"
            # 在导出结果里完全同形。
            swallow('app.asset_catalog.image_spec_of', e)
            return str(aid)

    @staticmethod
    def describe_asset(asset_type, row) -> str:
        """一行 → 人话说明（复用 `assets.asset_registry.describe_asset`）。"""
        try:
            from storygraph.assets.asset_registry import describe_asset
            return describe_asset(asset_type, row)
        except Exception as e:                     # noqa: BLE001
            swallow('app.asset_catalog.describe_asset', e)
            return ''
