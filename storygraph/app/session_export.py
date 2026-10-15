"""
storygraph.app.session_export
=============================
`EditorSession` 的**导出那一半**：编译、增量补丁、安全门禁、多引擎导出、
预览数据、项目体检。

为什么把它拆出来（P2-6 第四步，2026-10-05）
--------------------------------------------
`EditorSession` 原本是 1900 行的上帝对象（74 个方法）。已拆走三块：
剧情图（13）、资产（11）、**导出+协作（14，本文件与 `session_collab.py`）**。

这一块里有**这个项目最危险的一段代码**（`export` + `_patch_note`）——
它管着"patch 失败不许静默退回全量覆盖"那个 P0
（`docs/archive/P0_静默全量覆盖_2026-10-03.md`）。所以它既是"最独立的一块"，
也是**最不能动坏的一块**；搬的时候一个字都没改，只是换了住址。

职责边界
--------
**属于这里**：编译（`compile` 的**下游**）、落盘与门禁、增量补丁、
  多引擎导出、预览帧（`frames` / `transform_index` / `screen_size`）、
  项目体检（`health`）。

**不属于这里**（留在 `EditorSession`，见那边的说明）：
  · `compile()` —— 它是"拍 → 编译结果"的**基建**：`export` 用它，
    别的地方也会直接用（前端预览、表格侧）。搬进来会让宿主反向依赖本块。
  · `registry()` / `source()` / `refresh_symbols()` —— 同前两块，
    是"资源从哪来"的基建。
  · 表格/拍编辑那一整块（`load` / `fields` / `cards` / `apply` /
    `insert` / `delete` / 撤销）—— 核心，网最密，留到最后。

`EXPORT_ENGINES` 放在这里而不是宿主：它只有 `export_ir` 用
（"支持哪些引擎"是导出这一块的词表）。
"""
from __future__ import annotations

import re
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.app.stage import build_frames
# 门禁这一套（`export` / `_patch_note` 全靠它们）：
#   · `evaluate_generation` + `labels_in_danger` —— 落盘**前**预检；
#   · `write_generation` —— 落盘那一步的兜底门禁；
#   · `would_destroy_opaque` —— "覆盖它会不会毁掉不进表的东西"（P0 的判据）；
#   · `ensure_generator_marker` —— 产物必须带生成标记，否则护栏认不出自家产物。
from storygraph.story.generation_guard import (ensure_generator_marker,
                                               evaluate_generation,
                                               labels_in_danger,
                                               merging_onto,
                                               would_destroy_opaque,
                                               write_generation)


__all__ = ['ExportMixin']


class ExportMixin:
    """
    导出那一半（见模块 docstring 的职责边界）。

    它**要求宿主提供** `registry()` / `compile()` / `symbols` /
    `project_root` / `script_path` / `beats`，以及跨块共享状态
    `_transforms_decision` —— 见 `REQUIRES`。
    """

    #: 本 mixin 要求宿主（或其它 mixin）提供的成员（**实测**，不是凭印象）。
    #:
    #: `_transforms_decision` 是**跨块共享状态**（2026-10-05 补）：
    #: `AssetRegistryMixin.register_missing_assets` 在资源表变了之后把它
    #: 清成 `None`（写），本 mixin 的 `should_write_transforms` 读它当缓存。
    #:
    #: 漏声明的后果**不是报错**，而是"属性是否存在取决于之前发生过什么"：
    #: `getattr(self, '_transforms_decision', None)` 在宿主没初始化时
    #: 静默拿 `None` → 重新判定一遍 → 症状是"偶尔多写一段 transform 定义"，
    #: 没人会把这和拆分联系起来。所以它必须两头都说清：
    #: 读它的人（本 mixin）声明、写它的人（资产块）声明、
    #: 宿主 `EditorSession.__init__` 初始化。
    #: 守卫：`tests/test_editor_session_mixin_contract.py`。
    REQUIRES = ('registry', 'compile', 'symbols', 'project_root',
                'script_path', 'beats', '_transforms_decision',
                # 原地写回之后要把内存重新对齐到磁盘（见
                # `_reload_after_inplace_write`）—— 那是 `SessionTableMixin.load`。
                'load',
                # **"行号还新鲜吗"**（2026-10-05）：`_patch_note` 靠它决定要不要
                # 把"拍的源行号可信"告诉补丁（`patch_export(trust_ranges=…)`，
                # 见那边的说明：用户把一句话同时改成台词 + 换正文 + 加立绘时，
                # 对位守卫的内容证据会全没）。实现与指纹都在 `SessionTableMixin`
                # （`load` / `_fingerprint_of` 那一块）—— **由
                # `test_editor_session_mixin_contract.py` 守着**，正是它当场
                # 抓到了这次跨块调用没声明。
                'source_unchanged')

    EXPORT_ENGINES = ('unity', 'godot', 'json', 'csv', 'renpy')
    def export_ir(self, engine: str, out_dir) -> dict:
        """
        把当前章节的演出表导出成**其他引擎**要的格式。

        用户 2026-10-05："请确保所有的功能都接上UI" —— 这条以前只有
        `pipeline.engine_exporters.export_ir` 这个后端函数，Qt 版没有任何入口。

        `engine`: unity / godot / json / csv / renpy（见 `EXPORT_ENGINES`）。
        返回 `{ok, engine, out_dir, files, error}`。
        """
        engine = str(engine or '').lower()
        if engine not in self.EXPORT_ENGINES:
            return {'ok': False, 'engine': engine, 'out_dir': str(out_dir),
                    'files': [],
                    'error': f'不支持的引擎：{engine or "（空）"}'}
        if not self.beats:
            return {'ok': False, 'engine': engine, 'out_dir': str(out_dir),
                    'files': [], 'error': '还没有载入章节，没有可导出的演出表'}
        from storygraph.pipeline.engine_exporters import export_ir
        try:
            files = export_ir(engine, self.beats, Path(out_dir),
                              registry=self.registry(),
                              project=str(self.project_root or ''),
                              title=Path(self.script_path).stem
                              if self.script_path else '')
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'engine': engine, 'out_dir': str(out_dir),
                    'files': [], 'error': str(e)}
        return {'ok': True, 'engine': engine, 'out_dir': str(out_dir),
                'files': [str(f) for f in (files or [])], 'error': ''}
    def frames(self) -> list:
        """
        整章的舞台帧（供预览窗口播放）。

        `transform_index` / `screen` 来自项目源码（`transform.rpy` 的
        `transform f21: focus(320)` 与 `gui.init(1920, 1080)`）—— 用户
        2026-10-05："transform 未对应到舞台真实位置"。**按项目根缓存**：
        扫一遍源码是有代价的，但预览只在打开时取一次。
        """
        return build_frames(self.beats, self.registry(),
                            transform_index=self.transform_index(),
                            screen=self.screen_size())
    def transform_index(self) -> dict:
        """项目里的 transform 定义（按项目根缓存；换项目自动失效）。"""
        root = str(self.project_root or '')
        cached = getattr(self, '_transform_index_cache', None)
        if cached is not None and cached[0] == root:
            return cached[1]
        index = {}
        try:
            from storygraph.story.atl import scan_transforms
            index = scan_transforms(self.project_root) if root else {}
        except Exception as e:                     # noqa: BLE001 - 扫不动就退回启发式
            swallow('app.session_export.transform_index', e)
            index = {}
        self._transform_index_cache = (root, index)
        return index
    def screen_size(self) -> tuple:
        """游戏分辨率（`gui.init(w, h)`）；读不到用 1920×1080。"""
        root = str(self.project_root or '')
        cached = getattr(self, '_screen_cache', None)
        if cached is not None and cached[0] == root:
            return cached[1]
        from storygraph.story.atl import DEFAULT_SCREEN, project_screen
        size = project_screen(self.project_root) if root else DEFAULT_SCREEN
        self._screen_cache = (root, size)
        return size
    def should_write_transforms(self) -> bool:
        """
        生成物**要不要自带 transform 定义段**？

        用户 2026-10-05：

        > 全量不要重复写 transform 定义 —— 项目里已经有 definitions.rpy

        判定：这一章用到的自定义变换，**项目里别的 .rpy 已经声明过**
        （`transform <名字>:`）→ 不写（写了就是重复定义，Ren'Py 里后者
        覆盖前者，章节文件被悄悄改语义）。
        找不到就老实写上，让产物自包含。

        成本：一次正则扫项目脚本（40 个文件约 0.3s），结果按需缓存。
        """
        if getattr(self, '_transforms_decision', None) is not None:
            return self._transforms_decision
        decision = True
        try:
            reg = self.registry()
            if reg is None or not self.project_root or not self.beats:
                return True
            from storygraph.parsing.discover import discover_scripts
            from storygraph.story.beat_render import collect_used_transforms
            used = collect_used_transforms(self.beats)
            # 只关心**资源表里带 ATL 的那些**（`render_transform_definitions`
            # 会真正写出来的就是它们）——`used` 里还混着 `left` / `dissolve`
            # 这类 Ren'Py 内置变换，它们项目里当然没人声明，拿它们一起判
            # 会永远得出"其他文件没定义全 → 要写"。
            rows = getattr(reg, 'transforms', None) or {}
            used = {t for t in (used or ()) if t in rows}
            if not used:
                return True
            # 排除"正在导出的这一章"自己（它里面的 transform 定义不算数）
            target = Path(self.script_path) if self.script_path else None
            pattern = re.compile(
                r'^[ \t]*transform[ \t]+([A-Za-z_]\w*)', re.MULTILINE)
            found: set = set()
            for fp in discover_scripts(self.project_root):
                if target is not None and Path(fp) == target:
                    continue
                try:
                    text = Path(fp).read_text(encoding='utf-8',
                                              errors='replace')
                except OSError:
                    continue
                found.update(pattern.findall(text))
                if used <= found:
                    break
            decision = not (used <= found)
        except Exception as e:                     # noqa: BLE001 - 判不了就老实写
            swallow('app.session_export.should_write_transforms', e)
            decision = True
        self._transforms_decision = decision
        return decision
    @staticmethod
    def _issue_message(issue) -> str:
        """
        门禁发现 → 一句给人看的话。

        统一在这里取：`blocking` 要交给 UI 直接显示，
        而 `GuardIssue` 有 `.message`、别的实现可能只有 `__str__`
        （单测里塞过裸字符串）。三种形态都兜住，别在调用点各写一遍。
        """
        return str(getattr(issue, 'message', None) or issue)
    def default_output_path(self) -> Path:
        stem = self.script_path.stem if self.script_path else 'out'
        base = (self.project_root or Path('.')) / 'build' / 'renpy'
        return base / f'{stem}.generated.rpy'
    def export(self, target, force: bool = False) -> dict:
        """
        落盘导出。**所有安全检查都在这里**，前端只管挑路径。

        返回：{ok, path, note, todos, blocking, codes}
          · ok=False 时 `blocking` 是拦截原因（前端展示即可）；
          · `codes` 是门禁问题的**代码**（`HANDWRITTEN_TARGET` 等），
            前端靠它决定"要不要给用户一个强制覆盖的选项" ——
            只有手写目标这一条是**可覆盖**的，语法/重名那几条不可；
          · note 说明用了增量补丁还是全量重生成。
          · ok=False 且 `review_path` 非空时，产物留在那个 `.blocked.rpy`
            副本里供人对比（沿用门禁既有机制）。

        `force=True`（逃生舱，方案 3）：允许覆盖已存在的手写 `.rpy`。
        **必须由用户显式确认过**（见 `ui_qt.main_window.export_script` 的
        "覆盖手写文件？"对话框）—— 全量生成只认得"拍"，
        `default` / `init python` / `transform` 一行都留不下
        （`docs/archive/P0_静默全量覆盖_2026-10-03.md`）。
        """
        target = Path(target)
        result = self.compile()
        if getattr(result, 'blocking', None):
            return {'ok': False, 'path': target, 'blocking': list(result.blocking),
                    'codes': ['COMPILE'], 'note': '', 'todos': 0}

        text = result.rpy_text
        note = self._patch_note(text)
        note['text_note'] = self._with_sanitize_note(note.get('text_note', ''))
        if note['used_patch']:
            text = note['text']
        elif note.get('blocking') and not force and would_destroy_opaque(
                target, self.script_path):
            # **补丁失败 + 覆盖它会毁掉不进表的东西 = 拒绝写盘**（P0，2026-10-05）。
            #
            # 为什么必须拦：此时唯一的替代是"全量重生成"，而全量只认得"拍"，
            # `default` / `init python` / `transform` / `screen` 一行都留不下。
            #
            # `_would_destroy_opaque` 的判据是"**目标文件里有没有工具不认识的
            # 内容**"，而不是笼统的"补丁失败了"：
            #
            #   · 目标就是源 .rpy（原地导出）→ 那是作者手写的文件，
            #     里面很可能有 `default` / `init python` → **必须拦**；
            #   · 目标是一个**新建的**路径（导出到 `build/`、另存一章）→
            #     覆盖它不会毁掉任何东西，全量正是要做的事 → **放行**；
            #   · 目标已存在且**没有生成标记** → 别人的手写文件 → **拦**。
            #
            # 第一版只看 `had_source`（"有源 .rpy 可增量"），那会把
            # "编辑 ch01、导出到 out.rpy"这种正常操作也拦掉 ——
            # 实测 `test_export_reflects_the_edit` 当场红。
            #
            # 第二层护栏（`write_generation` 的 `HANDWRITTEN_TARGET`）仍然
            # 会兜住"凭空新建到别人的手写文件上"那种情况。
            return {'ok': False, 'path': target,
                    'blocking': list(note['blocking']) + [
                        # **给一条能走的路**（2026-10-05，验收时自己撞到的）：
                        # 以前这里只有一句"对位率不足（N%）"—— 用户看着它
                        # 不知道下一步做什么，而"重载这一章再导出"通常就能解决
                        # （行号过期是主因，见 `_resync_source_ranges`）。
                        #
                        # ③ 的措辞 2026-10-05 夜改过：它从前说"再明确要求覆盖"，
                        # 而这一条**根本没有"仍然覆盖"按钮**（`_overwritable`
                        # 只放 `HANDWRITTEN_TARGET`）—— 用户按着这句话找按钮，
                        # 找不到，就变成"为何阻止我替换旧文件"。说实话：
                        # 这条路是**刻意**关掉的（见 `_patch_note` 的 docstring）。
                        '可以试：① 重新载入这一章再导出（行号过期是最常见的原因）；'
                        '② 导出到 build/renpy/ 下的新路径（新建不受这条限制），'
                        '比对之后再把改好的那一章放回去；'
                        '③ 原地覆盖这条路**是刻意关掉的**：补丁没配上时写出去'
                        '就是丢内容（`default` / `init python` 会没）。'
                        '若这一章的结构被手工改过（例如把菜单标题写到了 '
                        '`menu:` 的下一行），增量补丁做不了这种两行合一行，'
                        '先改回"一行一个结构"再导。'],
                    'codes': ['PATCH_FAILED'],
                    'note': note['text_note'], 'todos': 0}

        # **产物必须带生成标记**，补丁那条路也一样（2026-10-05 实测）。
        #
        # 为什么：`patch_export` 的合并策略是"保留原文那一行"，
        # 它产出的首行是**源文件的首行**（常常是 `default …` 之类），
        # 于是补丁产出的文件**没有生成标记**。后果是护栏认不出自家产物：
        #
        #     第一次导出 → ok=True   （补丁成功，写出去的没有标记）
        #     第二次导出 → 拒绝覆盖  （被判成"手写文件"）
        #
        # 也就是说"再导出一次"在一个已经由工具生成的文件上必然失败 ——
        # 这正是 `test_allows_overwriting_its_own_output` 想防的那种事故，
        # 只不过它当时只在 `write_generation` 这一层测，没经过 export。
        text = ensure_generator_marker(text)

        occupied = labels_in_danger(target, self.project_root,
                                   exclude={target, self.script_path})
        issues = evaluate_generation(text, target,
                                     project_root=self.project_root,
                                     occupied=occupied, beats=self.beats)
        blocking = [i for i in issues if i.is_blocking]
        if blocking:
            # `force` **不**越过这几条：图像名非法 / label 重名 /
            # Python 语法错误都是"产物本身坏了"，覆盖只会把坏脚本写进 game/。
            # `force` 的语义只有一条 —— "我确认要覆盖手写文件"。
            return {'ok': False, 'path': target,
                    'blocking': [self._issue_message(i) for i in blocking],
                    'codes': [str(getattr(i, 'code', '')) for i in blocking],
                    'note': note['text_note'], 'todos': 0}

        # `write_generation` 自己还有一道兜底门禁（`HANDWRITTEN_TARGET`：
        # 目标已存在且不是工具生成的，拒写）。它**必须被检查** ——
        # 不检查就回到当年那个形态：文件根本没写出去，却报告"导出成功"，
        # 用户以为产物在。`review_path` 指向留给人对比的副本。
        #
        # **增量对上了的时候，要把底稿路径交下去**（2026-10-05 实测修，
        # 用户："明明只是增量的对一个卡片的修改……为何要阻止我拿新的文件
        # 去替换旧的？"）：
        #
        # 护栏的判据是"首行有没有生成标记"，而**人自己写的章节文件一个
        # 标记都没有** —— 于是"改一句台词、原地写回"这条最常用的路会被
        # 自家护栏拦下：补丁明明对位 245/245、只改 1 行（逐字节无损），
        # 却被判成"手写文件，拒绝覆盖"，用户只能看到一个
        # 「覆盖手写文件？」的吓人对话框（默认按钮还是取消）。
        #
        # CLI 那条路（`pipeline.compile.write_compiled`）早就把底稿交下去了，
        # 编辑器这条**漏了** —— 同一件事两处实现，漏的那处症状正是
        # "UI 里没法原地增量导出"。现在两边都交**路径**而不是布尔，
        # 门禁自己判"底稿是不是就是目标"（见 `generation_guard.merging_onto`），
        # 调用方无法用一句"我走了增量"越过护栏。
        #
        # `source_text` 一并交过去，是为了让**丢失清单**精确到行
        # （`OPAQUE_LOST`）：那条门禁的判据是"源里有、产物里没有的顶层语句"，
        # 不给源它就无从说起。
        #
        # 为什么现在"几乎不会触发"：顶层内容已经**随表保真**
        # （`TopLevelRegionNode` + 隐藏表），全量重生成也带得走。
        # 留着它是**兜底**：哪一天哪条路又漏了，这里会指名道姓地说出来。
        art = write_generation(text, target, project_root=self.project_root,
                               force=force,
                               merged_from_source=(self.script_path
                                                   if note.get('used_patch')
                                                   else None),
                               source_text=self._source_text_for_guard(),
                               source_path=self.script_path,
                               beats=self.beats)
        if not art.written:
            return {'ok': False, 'path': target,
                    'blocking': [self._issue_message(i) for i in art.issues]
                                or ['生成门禁拒绝写入'],
                    'codes': [str(getattr(i, 'code', '')) for i in art.issues],
                    'review_path': art.review_path,
                    'note': note['text_note'], 'todos': 0,
                    # 这一刀到底是不是"无损的增量"（前端据此说**真话**，见
                    # `ui_qt.window_export._confirm_force_overwrite`：
                    # 补丁成功时"会丢掉 default/init python"那句话是**假的**）。
                    #
                    # 判据与门禁同源（`merging_onto`）：只有"补丁对上了
                    # **且**合并底稿就是目标"时才敢说"不会丢东西" ——
                    # 编辑甲章、却往乙章的手写文件上写，是另一回事
                    # （那份产物里没有乙章的任何内容），照旧说真话。
                    'used_patch': (bool(note.get('used_patch'))
                                   and merging_onto(target,
                                                    self.script_path))}
        # **写回"我们载入的那个文件"之后，内存必须重新对齐到磁盘。**
        #
        # 为什么（2026-10-05 实测，`tools/acceptance_workflow.py` 抓到）：
        # 写回之后磁盘上那一份已经是新内容（补丁插了行、首行多了生成标记），
        # 而内存拍的 `src_first/src_last` 还是**改动前**那份文件的 →
        # 第二次原地导出对位率掉到 0~50% → 补丁失败 → P0 那条"目标就是源文件"
        # 的护栏拒写。用户看到 `PATCH_FAILED：对位率不足`，而它偏偏落在
        # **最常用的那条循环**上：
        #
        #     改一句 → 导出 → 再改一句 → 再导出   ← 第二次就断
        #
        # 为什么是"重新解析"而不是"按内容猜行号"（先走错过一步，记下来）：
        # 第一版我用 `recover_source_ranges`（解析磁盘 + difflib 对拍）贴行号，
        # 它在最需要它的那个场景上**是错的** —— 解析器会把"注释 / `call screen`
        # + 紧随其后的台词"吸成**一张**拍，而内存里是**两张** → 对齐给出交叠
        # 区间 → 对位率 50%（实测 whackamole）。合并**本来知道**每一拍写到了
        # 哪几行，但把它报出来要动 `patch_export` 里"替换 / 插入 / 删除"
        # 三套坐标系的换算（全仓最危险的一段）。而"内存 := 解析(磁盘)"
        # 是**零启发**的：磁盘就是真源（契约 A1），解析器怎么读、内存就怎么存。
        #
        # 代价（可接受，而且本来就该如此）：若刚插了一张只有注释/代码的卡，
        # 重载后它会与后面那张并成一张 —— **那正是重新打开这个文件会看到的**，
        # 只是把"下次打开才发生的事"提前到了现在。
        if art.written and self.script_path \
                and Path(target) == Path(self.script_path):
            self._reload_after_inplace_write()
        # 对拍（B 阶段一）：结构列 vs 老推断。放**重载之后**算 —— 重载会把
        # 结构列一起读回来（它就是随表走的），算的是"磁盘上这一份"的账。
        nest_notes = self._nest_conflict_notes()
        return {'ok': True, 'path': target, 'blocking': [], 'codes': [],
                'note': note['text_note'] + nest_notes,
                'structure_conflicts': self._nest_conflict_count(),
                'used_patch': bool(note.get('used_patch')),
                # 重载之后拍数可能变（结构由解析器定）→ 告诉前端重新取列表
                'count': len(self.beats),
                'todos': getattr(result, 'todo_count', 0)}

    def _source_text_for_guard(self):
        """
        当前章节文件的原文（给 `write_generation` 算"丢了哪几行"用）。

        读不到就返回 None：**门禁退化成"没有参照"而不是误报** ——
        宁可少一条提示，也不能拿半份文本去指认"你丢了东西"。
        """
        p = getattr(self, 'script_path', None)
        if not p:
            return None
        try:
            return Path(p).read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError) as e:
            swallow('session_export._source_text_for_guard', e)
            return None

    def _nest_conflict_count(self) -> int:
        """结构列 vs 老推断不一致的拍数（B 阶段一的对拍结论）。"""
        from storygraph.story.beat_render import nest_placement_conflicts
        try:
            return len(nest_placement_conflicts(self.beats))
        except Exception as e:                  # noqa: BLE001
            swallow('session_export._nest_conflict_count', e)
            return 0

    def _nest_conflict_notes(self) -> str:
        """
        把对拍结论接进导出说明（有才加，没有就是空串）。

        为什么走 `note` 而不是弹框：这是一条**给开发者/细心人的线索**
        （渲染仍按老推断，产物大概率没问题），不该打断策划的导出流程。
        """
        n = self._nest_conflict_count()
        if not n:
            return ''
        return (f'；⚠ 结构列与老推断有 {n} 条拍对不上'
                f'（`NEST_CONFLICT`，见 docs/archive/嵌套与行尾注释_修复方案_2026-10-05.md）')

    def _reload_after_inplace_write(self) -> None:
        """
        原地写回之后把内存重新对齐到刚写出的那份文件（见调用点的说明）。

        失败不抛（`swallow`）：同步不了最多是"下一次导出退回问一句"，
        不该把已经成功的这一次导出变成异常。
        """
        from storygraph.core.safeguard import swallow
        try:
            self.load(self.script_path)
        except Exception as e:                  # noqa: BLE001
            swallow('session_export._reload_after_inplace_write', e)

    def _with_sanitize_note(self, text: str) -> str:
        """
        把"读表时清洗过几个单元格"接到导出说明后面（2026-10-05 评审意见）。

        为什么必须说：清洗本身是**静默改数据** —— 值里的换行被压成空格，
        产物能跑了，但值跟他在表里写的不一样了。体检那条路由
        `validate` 的 W114 兜；可是"直接导出、从不体检"的人走的正是这条 UI
        路径，这里不说，他就永远不知道。

        （`cli.py compile` / `table xlsx2rpy` 那条命令行走的是
        `CompileResult.summary()`，同一句话在那边。）
        """
        n = sum(len(getattr(b, 'sanitized_cells', None) or [])
                for b in (self.beats or ()))
        if not n:
            return text
        extra = (f'读表时 {n} 个单元格里有换行/制表符，已按「一行一个值」'
                 f'压成空格（**值被改过**，核对一下；体检里是 W114）')
        return f'{text}；{extra}' if text else extra

    def _patch_note(self, text: str) -> dict:
        """
        试着走增量补丁。**补不上时不许装作"换了个写法"**（P0，2026-10-05）。

        为什么原来是 P0（见 `docs/archive/P0_静默全量覆盖_2026-10-03.md`）：
        以前补丁失败时这里返回 `{'used_patch': False, 'text': text}` ——
        也就是"用全量文本接着写"。而**全量生成只认得"拍"**，
        `default` / `define` / `init python` / `transform` / `screen`
        一行都留不下。于是"对位失败"这个**可恢复的小麻烦**，
        被静默放大成"用户手写的定义全没了、游戏起不来"。

        现在返回的 `blocking` 是**非空就代表这刀不能落盘**：

          · `used_patch=True`  → `blocking=[]`，`text` 是合并后的全文；
          · 补丁失败           → `blocking=[原因…]`，`text=''`
            （故意不给可用文本 —— 给了就会有人拿它去写盘，
             这正是当年的事故形态）。

        `had_source` 告诉调用方"当时到底有没有源 .rpy 可增量"：

          · `True`  → 有源文件可合并，补丁却没过 → 全量会丢东西 → **拦住**；
          · `False` → 新建章节，本来就没有可增量的源 → 全量是唯一正确做法。

        调用方 `export` 靠它区分这两种"补丁失败"（见那里的注释）。
        """
        from storygraph.story.patcher import patch_export
        src = (self.script_path
               if self.script_path and Path(self.script_path).exists()
               else None)
        had_source = src is not None
        # **行号新鲜吗？**（2026-10-05 用户实测报："我只调了一个卡片，都不能替换，
        # 那就太搞用户心态了！"）——用户把一句旁白同时改成台词、换掉正文、
        # 再加一张立绘时，对位守卫的内容证据（说话人/正文/图像名）一条都不剩，
        # 只能判"错位"→ 整份拒绝。而编辑器这条路是**解析出来的行号**：
        # 只要磁盘上那一份还是载入时那一份（`source_unchanged`），行号就是真迹，
        # 可以放心按行号替换。
        #
        # ⚠ 只有编辑器这条路能保证这一点：表格那条路的行号是
        # `recover_source_ranges` **现算的猜值**，那边必须继续依赖守卫
        # （见 `patch_export` 的 `trust_ranges`）。
        trust = False
        try:
            trust = bool(self.source_unchanged())
        except Exception as e:               # noqa: BLE001 - 判不了就当不新鲜
            swallow('session_export._patch_note.source_unchanged', e)
        try:
            res = patch_export(self.beats, src, text, registry=self.registry(),
                               trust_ranges=trust)
            if res.ok:
                return {'used_patch': True, 'text': res.text, 'blocking': [],
                        'had_source': had_source,
                        'text_note': f'增量补丁：对位 {res.matched_beats}/'
                                     f'{res.total_beats} 拍，'
                                     f'改动 {res.changed_lines} 行'}
            reason = ('；'.join(res.reasons)
                      or f'对位率不足（{res.coverage:.0%}）')
            return {'used_patch': False, 'text': '', 'blocking': [reason],
                    'had_source': had_source,
                    'text_note': f'增量未采用：{reason}'}
        except Exception as e:               # noqa: BLE001
            # 异常**也是**拦截理由，不是"退回全量"的许可证：
            # patcher 崩了说明状态不可信，此时覆盖手写文件风险最大。
            return {'used_patch': False, 'text': '',
                    'blocking': [f'增量不可用：{e}'],
                    'had_source': had_source,
                    'text_note': f'增量不可用：{e}'}
    def health(self, limit: int = 30) -> dict:
        """项目体检（**只读，不写盘**）。返回 {'issues': [...], 'total': n}。"""
        if not self.project_root:
            return {'issues': [], 'total': 0,
                    'error': '还没有打开项目'}
        from storygraph.pipeline.build import collect_report
        r = collect_report(self.project_root)
        issues = list(getattr(r, 'issues', []) or [])
        return {
            'issues': [str(getattr(i, 'message', i)) for i in issues[:limit]],
            'total': len(issues),
        }
