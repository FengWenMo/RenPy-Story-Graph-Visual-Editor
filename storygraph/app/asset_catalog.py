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
        except Exception:                     # noqa: BLE001
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
