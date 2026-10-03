"""
storygraph.app.session_assets
=============================
`EditorSession` 的**资产那一半**：资源表的重读、候选、概览、缩略图路径。

为什么把它拆出来（P2-6 第三步，2026-10-05）
--------------------------------------------
`EditorSession` 原本是 1900 行的上帝对象（74 个方法），混装四块职责。
2026-10-05 先拆走了「剧情图」（`session_graph.GraphSessionMixin`，13 方法），
这一块是第二步：**资产**（11 方法）。

它比剧情图那块风险略高一点 —— 会碰 `self._loaded_registry` 这个**状态**，
而剧情图那块几乎纯转发。所以拆之前先确认两层网都在
（`test_editor_session_behavior` 行为级 + `test_editor_session_snapshot` 内部快照）。

**MRO 位置**：排在 `EditorSession` 基类列表里，与其它 mixin 平级。
`EditorSession` 自己**不许**再有同名方法（`test_editor_session_split`
会当场红 —— 子类同名会**静默**覆盖 mixin 的那份）。

职责边界
--------
**属于这里**：资源表（registry）的读取与重载、下拉候选、资产概览、
  角色/参与角色、立绘缩略图的路径解析、"磁盘上有但表里没登记"的补录。

**不属于这里**（`EditorSession` 保留，见那边的说明）：

  · `registry()` —— 它同时服务**导出**（渲染要符号表）与**剧情图**
    （`import_chapters` 合并脚本 `define` 后要 `refresh_symbols`）。
    它是"资源从哪来"的**基建**，不是资产管理本身；放进这个 mixin 会让
    另外两块反向依赖它。所以留在这里**只调用不分管**。
  · `source()` / `refresh_symbols()` —— 同理，基建。
  · `preview`（`frames` / `transform_index` / `screen_size`）—— 是"预览"
    那一块，不是资产（虽然 `frames` 会拿 registry 当输入）。

与宿主的隐式依赖（只有读/写一个属于宿主的字段）
-----------------------------------------------
  · `self.registry()` —— 由 `EditorSession` 提供（见上）；
  · `self.project_root` —— 找资源表 / 扫脚本用；
  · `self._loaded_registry` —— `load_assets` **写**、`assets_registry` 读。
    这个字段由 `EditorSession.__init__` 初始化（写在这里会让 mixin
    没法单独实例化，本来也不打算）。
"""
from __future__ import annotations

from pathlib import Path

from storygraph.core.safeguard import swallow

from storygraph.app import beats as B


__all__ = ['AssetRegistryMixin']


class AssetRegistryMixin:
    """
    资产那一半（见模块 docstring 的职责边界）。

    它**要求宿主提供** `registry()` / `refresh_symbols()` / `project_root`
    / `_loaded_registry` —— 前三个来自 `EditorSession` 的基建部分。
    这条隐式耦合由 `test_editor_session_split.py` 显式钉住：
    每个 mixin 声明 `REQUIRES`，测试断言最终合成的类上全都有。
    """

    #: 本 mixin 要求宿主（或其它 mixin）提供的成员。
    #: **不写全的话，拆错了只会在运行时炸**，而这里列出来就能静态守住
    #: （`test_editor_session_split.py` 会断言最终合成的类上全都有）。
    #:
    #: 这 7 个是**实测**出来的（`self.*` 静态扫一遍），不是凭印象写的：
    #: 前 5 个是方法（由 `EditorSession` 的基建部分提供），
    #: 后 2 个是**实例属性**（`EditorSession.__init__` 初始化，下面读写）。
    REQUIRES = ('registry', 'refresh_symbols', 'source', 'project_root',
                '_transforms_decision', '_loaded_registry', '_linker')

    #: 资产种类 → 资源表字段名（与 `_write_candidate_sheets` 的映射同源）
    ASSET_KINDS = {'character': 'characters', 'image': 'images',
                   'transform': 'transforms', 'music': 'music',
                   'sound': 'sounds', 'voice': 'voices'}

    def load_assets(self) -> dict:
        """
        「打开项目」：**只**重读资源表（xlsx）—— 图片 / 音乐 / 音效 /
        语音 / 变换 / 角色。

        为什么单独一个方法、而且**不收 graph 参数**（用户 2026-10-03）：

        > 所谓的导入项目只是为了表格的 xlsx 的资源管理系统！
        > 不是把整个项目都排在剧情图上面！！！

        资源管理（立绘/音乐候选、缩略图、参与角色）需要整个项目；
        剧情图**只放用户自己导入的章节**。两者以前被混在一句话里
        （"打开项目 → 建出结构图"），于是 40 个脚本一次性灌进图里变成
        51 个块，"看都要看瞎了"。这里从签名上就断开这种可能：
        这个方法**拿不到图**，想灌也灌不进去。
        """
        if not self.project_root:
            return {'ok': False, 'error': '还没有选目录', 'counts': {}}
        from storygraph.pipeline.build import load_asset_registry
        try:
            reg = load_asset_registry(self.project_root)
        except Exception as e:                # noqa: BLE001
            self._loaded_registry = None
            return {'ok': False, 'error': str(e), 'counts': {}}
        if reg is None:
            self._loaded_registry = None
            return {'ok': False, 'error': '这个目录下找不到资源表（xlsx）',
                    'counts': {}}
        self._loaded_registry = reg
        self.refresh_symbols()
        counts = {k: len(getattr(reg, k, []) or [])
                  for k in ('images', 'music', 'sounds', 'voices',
                            'transforms', 'characters')}
        return {'ok': True, 'error': '', 'counts': counts, 'registry': reg}

    def characters(self) -> list:
        """可用说话人 ID（排序）。"""
        reg = self.registry()
        try:
            return sorted((getattr(reg, 'characters', {}) or {}).keys())
        except Exception as e:                    # noqa: BLE001
            swallow('app.session_assets.characters', e)
            return []

    def participants(self, limit: int = 8) -> list:
        """本项目的参与角色（含脚本声明；见 `beats.participants`）。"""
        if self.registry() is None:
            return []
        try:
            return B.participants(self.source(), limit=limit)
        except Exception as e:                    # noqa: BLE001
            swallow('app.session_assets.participants', e)
            return []

    def assets_registry(self):
        """
        「打开项目」装进来的那份资源表（**不回落** provider，见下）。

        为什么要单独一个访问器：`registry()` 会在没有已加载资源表时去问
        `registry_provider`，而主窗口的 provider 又要优先看这份 —— 两边互相
        调用就成环了。所以这里只回答"我手上有没有"，有没有都由调用方决定。
        """
        return self._loaded_registry

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
        reg = self.registry()
        field = self.ASSET_KINDS.get(str(asset_type or ''))
        if reg is None or not field:
            return []
        rows = getattr(reg, field, None) or {}
        out = []
        for aid in sorted(rows.keys()):
            row = rows.get(aid) or {}
            if asset_type == 'image':
                value = self._image_spec_of(aid, row)
            else:
                value = aid
            out.append((value, self._describe_asset(asset_type, row)))
        return out
    @staticmethod

    def _image_spec_of(aid, row) -> str:
        """图片行 → image spec（`tag attr`）。复用 `app.picker`（选择器同源）。"""
        try:
            from storygraph.app import picker as P
            return P.image_spec_of(aid, row)
        except Exception:                     # noqa: BLE001
            return str(aid)
    @staticmethod

    def _describe_asset(asset_type, row) -> str:
        try:
            from storygraph.assets.asset_registry import describe_asset
            return describe_asset(asset_type, row)
        except Exception as e:                     # noqa: BLE001
            swallow('app.session_assets._describe_asset', e)
            return ''

    def asset_linker(self):
        """
        角色 ↔ 剧本的引用关系（角色台词量排名 / 未定义角色 / 没出场的角色）。

        `AssetPanelData` 要 `engine.linker`，而 Qt 会话手上没有引擎 ——
        这里按需**解析一次全项目脚本**并缓存。成本只付一次（40 个文件约 1 秒），
        而且只在用户真的打开"资产统计"时才付。
        """
        if getattr(self, '_linker', None) is not None:
            return self._linker
        if not self.project_root:
            return None
        try:
            from storygraph.parsing.character_script_linker import \
                CharacterScriptLinker
            from storygraph.parsing.discover import discover_scripts
            from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
            analysis = IncrementalRenPyAnalyzer().analyze(
                files=discover_scripts(self.project_root), graph=None)
            parse_result = analysis[0] if isinstance(analysis, tuple) else analysis
            self._linker = CharacterScriptLinker(parse_result)
            # `link()` 才建立反向索引（台词量 / label 归属）；不调它就是全 0
            try:
                self._linker.link()
            except Exception:                 # noqa: BLE001
                swallow('editor.asset_linker')
        except Exception as e:                     # noqa: BLE001
            swallow('app.session_assets.asset_linker', e)
            self._linker = None
        return self._linker

    def asset_overview(self, top: int = 15) -> dict:
        """
        资产 / 角色的只读统计（资产面板那一套查询，界面用）。

        数据源是 `app.asset_panel.AssetPanelData`（框架无关的查询层）——
        界面**不重写**"按 category 分组 / 角色台词量排名"这类逻辑。
        返回 `{ok, statistics, categories, top_characters, undefined,
        unused_characters, unused_assets, broken, error}`。
        """
        from storygraph.app.asset_panel import AssetPanelData
        if self.registry() is None:
            return {'ok': False, 'error': '还没有资源表 —— 先「📁 打开项目」',
                    'statistics': {}, 'categories': {}, 'top_characters': [],
                    'undefined': [], 'unused_characters': [],
                    'unused_assets': [], 'broken': []}
        panel = AssetPanelData(self.source())
        # 角色相关的查询走 linker（`AssetPanelData` 从 engine 上取）：
        # 没有它，"角色台词量 / 未定义角色 / 没出场的角色"永远是空的。
        linker = self.asset_linker()
        if linker is not None:
            try:
                panel.engine.linker = linker
            except Exception:                 # noqa: BLE001
                swallow('editor.asset_overview')
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
            swallow('app.session_assets.asset_overview', e)
            out['top_characters'] = []
        # 大列表只给前 N 条（界面是给人看的，不是 dump）
        for key in ('unused_assets', 'broken', 'undefined'):
            if isinstance(out.get(key), list) and len(out[key]) > 200:
                out[key] = out[key][:200]
        return out

    def register_missing_assets(self, asset_dir=None) -> dict:
        """
        把**磁盘上有、资产表里没登记**的资源补进表，然后重新读表。

        用户 2026-10-05：

        > 先查背景图为何没有被登记进去，然后，让背景图被登记进去。

        实现只在 `assets.asset_registry.register_missing_assets`（只追加缺失行、
        写前备份、图片属性做 `.`→`_` 归一），这里只负责"改完立刻重载"。
        """
        from storygraph.assets.asset_registry import register_missing_assets
        if not self.project_root:
            return {'ok': False, 'error': '还没有项目根目录', 'added': 0}
        res = register_missing_assets(self.project_root, asset_dir=asset_dir)
        if res.get('ok') and res.get('added'):
            try:
                self.load_assets()            # 重读表：界面马上能选到新资源
            except Exception:                 # noqa: BLE001
                swallow('editor.register_missing_assets')
            self._transforms_decision = None  # 资源表变了 → 判定重算
        return res

    def path_of_image(self, image_spec: str):
        """
        立绘 image spec（`ling happy`）→ 磁盘上的真实图片路径（找不到 None）。

        **这是立绘缩略图的真源**：路径只在资源表的 `Path` 列里，
        而 `symbols` 里没有资源表 —— 早期前端拿 `symbols.registry` 去取，
        恒为 None，于是缩略图静默地永远加载不出来。
        """
        from storygraph.app.picker import thumbnail_path
        spec = str(image_spec or '').strip()
        if not spec or self.registry() is None:
            return None
        try:
            from storygraph.app.beats import lookup_image_id
            images = getattr(self.registry(), 'images', {}) or {}
            parts = spec.split()
            if not parts:
                return None
            tag, rest = parts[0], parts[1:]
            if not rest:
                return None
            # `ling happy` → `ling_happy` 这类 ID，先在表里找准
            aid = lookup_image_id(images, tag, rest)
            if not aid:
                aid = '_'.join([tag] + rest)
            hit = thumbnail_path(self.registry(), 'image', aid,
                                 self.project_root)
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
            swallow('app.session_assets.path_of_image', e)
            return None
