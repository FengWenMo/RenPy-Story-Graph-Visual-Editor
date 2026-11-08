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

import json
import re
from pathlib import Path

from storygraph.core.safeguard import swallow
# 导出的**纯判定**（默认路径 / 门禁代码 / 能不能强写）搬去 `export_plan`
# （2026-11-01，服务化第三刀）：那几个判据只认路径、问题对象、结果字典，
# 与 Qt 和会话状态都无关，所以能脱开界面单测。
from storygraph.app import export_plan
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


__all__ = ['ExportMixin', 'removal_note']

# 实现收在 `story/patcher.py`（补丁结果的持有者）：CLI / 表格 / 编辑器
# 三个出口都用同一句话，措辞不许各写一份（P0-D）。
from storygraph.story.patcher import removal_note  # noqa: E402


# ---------------------------------------------------------------------------
#  扩展批处理（导出之后补那几行 —— 2026-11-08）
# ---------------------------------------------------------------------------

#: 扩展批处理"什么都没做"的返回（调用方只读，别原地改）。
_NO_BATCH = {'applied': 0, 'rule': '', 'note': '', 'warnings': []}

#: `rules_from_extensions` / `plan_batch` 可能抛的这些 —— 批处理坏了不该把
#: 导出弄挂；宽捕获的守卫也认"把 e 拼进给人看的话"这条记录通道。
_BATCH_ERRORS = (ImportError, OSError, ValueError, TypeError, KeyError,
                 AttributeError, RuntimeError)


def _missing_texts(beats, lines) -> list:
    """这一章的台词里有哪几句**在 `lines` 里根本找不到**（例：'…'，最多取前三）。"""
    if not lines:
        return []
    out = []
    for b in beats or ():
        text = str(getattr(b, 'text', '') or '').strip()
        if not text:
            continue
        if not any(text in ln for ln in lines):
            out.append(text[:16])
    return out


def _batch_rel_target(project_root, target) -> str:
    """导出目标 → 项目内相对路径（不在项目内 / 不在 `game/` 下 → `''`）。"""
    root = str(project_root or '')
    tgt = Path(str(target or ''))
    if not root or not tgt.is_file():
        return ''
    try:
        rel = tgt.resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return ''
    return rel if rel.startswith('game/') else ''


def _declared_batch_rules(project_root) -> tuple:
    """扩展声明 → `(规则列表, 要人看一眼的话)`；坏的不连坐（一条不合法其余照跑）。"""
    from storygraph.app import batch_edit as BE

    try:
        rules, problems = BE.rules_from_extensions(project_root)
    except _BATCH_ERRORS as e:
        return [], [f'扩展批处理没跑起来：{e}']
    warnings = [f'扩展批处理规则不合法（这次一条都没补）：{p}'
                for p in problems]
    return rules, warnings


def _batch_plan(project_root, rules, rel, warnings) -> object:
    """算这一章的计划；算不出来 → `None`（原因进 `warnings`）。"""
    from storygraph.app import batch_edit as BE

    try:
        plan = BE.plan_batch(project_root, rules, chapters=[rel])
    except _BATCH_ERRORS as e:
        warnings.append(f'扩展批处理算不出计划：{e}')
        return None
    warnings.extend(f'扩展批处理：{msg}' for msg in plan.errors)
    return plan


def _apply_batch_plan(plan, warnings) -> int:
    """落盘 → **改了几行**（插入 + 替换 + 删除）。写不进去的进 `warnings`。"""
    from storygraph.app import batch_edit as BE

    done = BE.apply_plan(plan)
    changed = sum(int(done.get(key) or 0)
                  for key in ('inserted', 'replaced', 'deleted'))
    for name, why in done.get('failed') or ():
        warnings.append(f'扩展批处理没写进去（{name}）：{why}')
    counts = {}
    for fp in plan.files:
        for sk in (fp.skipped or ()):
            reason = str(sk.get('reason') or '')
            counts[reason] = counts.get(reason, 0) + 1
    if not changed and not warnings:
        already = counts.get('已经是目标内容', 0)
        if already:
            warnings.append(f'扩展批处理：{already} 处都已经是目标内容，没有重复补')
    # **保护性跳过要说出来**（2026-11-08，地狱测试）：规则打中的行在
    # `python:` 块里 / 是注释行时，补进去是改坏脚本 —— 工具拒绝是对的，
    # 但导出若只说"文件没有被改动"，人只会以为规则没生效。哪怕别处补上了，
    # 这几处也要点名（否则永远查不出"为什么这一条没生效"）。
    guards = [(reason, n) for reason, n in counts.items()
              if reason in getattr(BE, 'GUARD_SKIPS', ())]
    if guards:
        detail = '；'.join(f'{n} 处：{reason}' for reason, n in guards)
        warnings.append(f'扩展批处理没有补 {detail} —— 为不改坏脚本')
    return changed


def _span_cn(start, end) -> str:
    """`(4, 4)` → `第 4 行`；`(4, 6)` → `第 4–6 行`（1 基、闭区间）。"""
    start, end = int(start or 0), int(end or 0)
    return f'第 {start} 行' if start == end else f'第 {start}–{end} 行'


def card_at_lines(beats, spans) -> str:
    """
    这几行落在**第几张卡**上（1 基，带上卡里的台词好让人认出来）。

    为什么要点到卡：导出被拒时用户看到的是"第 4–5 行"，而他面对的是卡片列表
    —— 行号他不认识，卡号他认识（`docs/设计_导出这条大路_三路合并_2026-11-08.md`
    §3.4）。找不到就返回空串（不猜，交给调用方写"你改的那几行"）。
    """
    spans = tuple(spans or ())
    for i, b in enumerate(beats or ()):
        first = int(getattr(b, 'src_first', 0) or 0)
        last = int(getattr(b, 'src_last', 0) or 0)
        if not first:
            continue
        for start, end in spans:
            if first <= int(end or 0) and last >= int(start or 0):
                # 台词里万一带了换行/制表符，**不许漏进这句提示**：它是状态栏/
                # 单行浮层里的一句话（第十五轮地狱测试：`第一行\n第二行` 直接把
                # 那句话折成两行，后半截被裁掉）。
                head = ' '.join(str(getattr(b, 'text', '') or '').split())
                if len(head) > 14:
                    head = head[:13] + '…'
                return (f'第 {i + 1} 张卡「{head}」' if head
                        else f'第 {i + 1} 张卡')
    return ''


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
                #: 跨章继承（2026-10-31）：`frames()` 要给 `build_frames` 传
                #: 本章第一拍之前的舞台状态，否则预览第 1 拍是空台。
                'stage_initial_state',
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
                'source_unchanged',
                # **底稿文本**（2026-11-08 第十五轮）：`load()` 解析出这些拍
                # 的那份原文。文件被外人动过时补丁打在**它**上面（见
                # `_patch_note`），否则拍上的行号与原文不同源 —— 实测会把
                # 别人改的那一行静默改回去。写入方是 `SessionTableMixin.load`，
                # 宿主 `EditorSession.__init__` 初始化为 `''`。
                '_source_baseline_text')

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
        # **损失报告 / 自检**（§18 / §20）：适配层没搬过去的字段，回执里也要
        # 说一句；中立 IR 自己有没有矛盾（重复主键 / 统计不符 / 快照指向不
        # 存在的行），同样要说。**用落盘后的那份 IR 算**：多章导同一目录时
        # `export_ir()` 会把它并进旧文件，重算当前这一章会与 `IR_CHECK.md`
        # 的数字对不上（回执必须说真话）。
        loss, check = {}, {}
        try:
            from storygraph.pipeline.engine_exporters import (
                NEUTRAL_IR_NAME, build_neutral_ir)
            from storygraph.pipeline.ir_validate import (
                summarize as summarize_check)
            neutral = next((Path(f) for f in (files or [])
                            if Path(f).name == NEUTRAL_IR_NAME), None)
            if neutral is not None and neutral.is_file():
                ir = json.loads(neutral.read_text(encoding='utf-8'))
            else:
                ir = build_neutral_ir(
                    self.beats, registry=self.registry(),
                    project=str(self.project_root or ''),
                    title=Path(self.script_path).stem
                    if self.script_path else '')
            check = summarize_check(ir, engine)
            check['status'] = 'ok'
            if engine in ('unity', 'godot'):
                from storygraph.pipeline.ir_loss import summarize
                loss = summarize(ir, engine)
        except Exception as e:            # noqa: BLE001 - 报告失败不影响导出
            swallow('session_export.export_ir.report', e)
            # **"没跑成"必须和"零错误"长得不一样**（2026-10-20 修，P1）：
            # 从前这里只吞掉异常，回执里 `error_count` 就是 0 —— 用户看到
            # "自检 0 错"，其实是自检根本没跑完。并行的 `IR_CHECK.md` 可能
            # 是上一次导出的旧报告，两份"看起来都没事"。
            loss, check = {}, {'status': 'failed', 'error': str(e)}
        return {'ok': True, 'engine': engine, 'out_dir': str(out_dir),
                'files': [str(f) for f in (files or [])],
                'loss': {'count': len(loss.get('losses') or []),
                         'by_scope': loss.get('by_scope') or {},
                         'notes': loss.get('notes') or []},
                'check': {'status': str(check.get('status') or 'ok'),
                          'error': str(check.get('error') or ''),
                          'error_count': int(check.get('error_count') or 0),
                          'warning_count': int(
                              check.get('warning_count') or 0),
                          'by_code': check.get('by_code') or {},
                          'issues': (check.get('issues') or [])[:12]},
                'error': ''}
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
                            screen=self.screen_size(),
                            # 跨章继承（2026-10-31）：不给它就演不出"上一章
                            # 留下的背景"——预览第 1 拍会是空台，与游戏不符。
                            initial_state=self.stage_initial_state())
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
    def refresh_transform_index(self) -> dict:
        """
        丢掉缓存、**重新扫**项目里的 transform（P1，2026-10-31）。

        为什么需要它：尺寸面板会往 `game/sg_transforms.rpy` 里**写一条新定义**
        （用户确认之后）。缓存不失效的话，卡片刷新时还是"项目里没有这个
        transform"，人刚生成的东西自己看不见 —— 最典型的"做了等于没做"。
        """
        self._transform_index_cache = None
        return self.transform_index()
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
    def default_output_path(self) -> Path:
        """
        导出对话框的**默认路径**（判据在 `app.export_plan`，这里只递两个属性）。

        保留这个方法而不是让前端直接调那个函数：`script_path` /
        `project_root` 是**会话状态**，前端不该为了算一个默认路径去摸它们
        （也摸不到 —— 那是 `_session` 的私有状态）。
        """
        return export_plan.default_output_path(self.script_path,
                                               self.project_root)
    def export(self, target, force: bool = False) -> dict:
        """
        落盘导出。**所有安全检查都在这里**，前端只管挑路径。

        返回：{ok, path, note, todos, blocking, codes}
          · ok=False 时 `blocking` 是拦截原因（前端展示即可）；
          · `codes` 是门禁问题的**代码**（`HANDWRITTEN_TARGET` 等），
            前端靠它决定"要不要给用户一个强制覆盖的选项" ——
            只有手写目标这一条是**可覆盖**的，语法/重名那几条不可；
          · note 说明用了增量补丁还是全量重生成。
          · 文件被外人改过时（第十五轮）：note **开头**多一句短话
            （"别人也改过这个脚本（不是同一处）：两边都落上了…"），
            长的哪句（他在第几行改了什么）在 `warnings` 里 —— 短话进状态栏、
            长话进日志，与原地小改那条路同一个规矩（Toast 是一行，长了会被裁）。
          · ok=False 且 `review_path` 非空时，产物留在那个 `.blocked.rpy`
            副本里供人对比（沿用门禁既有机制）。

        `force=True`（逃生舱，方案 3）：允许覆盖已存在的手写 `.rpy`。
        **必须由用户显式确认过**（见 `ui_qt.main_window.export_script` 的
        "覆盖手写文件？"对话框）—— 全量生成只认得"拍"，
        `default` / `init python` / `transform` 一行都留不下
        （`docs/archive/P0_静默全量覆盖_2026-10-03.md`）。
        """

        # 2026-11-01：这个函数原来 263 行（**全项目最长**）。下面前五段各自搬进
        # 了一个方法 —— 每段都对应**一种拒绝或放行的理由**，编排留在这里，
        # 于是"导出会依次过哪几道门"一眼能读完。每段的注释都跟着代码走了。
        target = Path(target)
        result = self.compile()
        if getattr(result, 'blocking', None):
            return {'ok': False, 'path': target,
                    'blocking': list(result.blocking),
                    'codes': ['COMPILE'], 'note': '', 'todos': 0}

        text = result.rpy_text
        note = self._patch_note(text, target)
        note['text_note'] = self._with_sanitize_note(note.get('text_note', ''))
        if note['used_patch']:
            text = note['text']

        # 第二道门：和盘上那份合**（只在补丁真的用上了的时候）**。
        # 上一道门在有外人改过时把补丁打在**底稿**上（产物 = 底稿 + 我），
        # 这一步才把它和"底稿 + 他"合起来：不重叠 → 两边都落上（说出口）；
        # 撞在同一处 → **一个字节都不写**，理由点到哪一张卡。
        #
        # ⚠ 补丁**没用上**时绝不能进来合：那时的 `text` 是 `result.rpy_text`
        # （全量重生成，只认得"拍"），拿它去合并会把这个"全量"写进用户文件 ——
        # 正是 P0 那条要拦的形态。而且它还会把真正的拒因（"某句生成内容
        # 找不到"）盖成一句"撞在同一处"（实测踩到，第十五轮）。
        settled = self._settle_with_disk(
            text, target, note.get('base_text', '') if note['used_patch'] else '')
        if settled['blocking']:
            return {'ok': False, 'path': target,
                    'blocking': list(settled['blocking']),
                    'codes': ['PATCH_FAILED'], 'note': note['text_note'],
                    'todos': 0}
        text = settled['text']
        if settled['note']:
            # 短句放**最前面**：状态栏会截断，后面才是"对位 3/3 拍"这类细节。
            note['text_note'] = (f'{settled["note"]}；{note["text_note"]}'
                                 if note['text_note'] else settled['note'])
        if settled['warning']:
            note['merge_warning'] = settled['warning']

        untouched = self._untouched_export(target, text, note, result)
        if untouched is not None:
            return untouched

        # `used_patch` 与 `blocking` 互斥（补丁成功就不会有 blocking），
        # 所以这里是独立的 `if`，不是上面那个零改动分支的 `elif`。
        if note.get('blocking') and not force and would_destroy_opaque(
                target, self.script_path):
            return self._patch_failed_export(target, note)

        # **产物必须带生成标记**，补丁那条路也一样（2026-10-05 实测）。
        # 为什么（"再导出一次必然失败"那个坑）见 `_with_generator_marker`。
        text = self._with_generator_marker(text)
        blocked = self._generation_blocked(target, text, note)
        if blocked is not None:
            return blocked

        art = self._write_export(target, text, note, force)
        if not art.written:
            return self._write_refused(target, note, art)

        # **写回"我们载入的那个文件"之后，内存必须重新对齐到磁盘**
        # （为什么、以及为什么是"重新解析"而不是"猜行号"，见那个方法）。
        self._realign_memory_after_write(art, target)

        # **扩展点的「整章应用」在这里落地**（2026-11-08 用户：扩展窗口里配了
        # "允许把它批处理到整章"、导出 2-1-3 却一个字都没有）。只看扩展声明，
        # 算计划/落盘都用 `app.batch_edit` 那一份；细节见 `_batch_after_write`。
        batch = self._batch_after_write(target, note)

        # **"这一刀删掉了你写的行"要进告警**（P0-D，2026-10-25 第五轮）。
        #
        # `note` 会进状态栏和日志，但状态栏会截断、日志没人翻；而删掉程序员的
        # `$ x = 1` / `play music …` 是**写进去了、事后才发现**的那类事。
        # 塞进 `warnings` 之后：编辑器这条路会进日志的"提示"段，剧本表那条路
        # （`ui_qt.window_export` 的确认框）会把它列在"提醒"里。
        _warns = [export_plan.issue_message(i) for i in art.issues
                  if not i.is_blocking]
        # 扩展批处理要说的话（写了多少行 / 哪条规则不合法）**排最前**：
        # 它正是用户"导出没效果"时最该看到的那一句。
        _warns = list(batch.get('warnings') or ()) + _warns
        # "别人也改过这个脚本"的长句（第十五轮）放**最前面**：它是这一刀最该
        # 被看见的事（他改的那几行已经进磁盘了，界面还是旧的）。短句在 `note`。
        if note.get('merge_warning'):
            _warns.insert(0, note['merge_warning'])
        _removal = removal_note(note).lstrip('；')
        if _removal:
            _warns.insert(0, _removal.replace('**', ''))
        return {'ok': True, 'path': target, 'blocking': [], 'codes': [],
                # 成功了也可能有告警（产物写在 game/ 里、有自定义块没被建模…）
                'warnings': _warns,
                'note': note['text_note'],
                'used_patch': bool(note.get('used_patch')),
                # 重载之后拍数可能变（结构由解析器定）→ 告诉前端重新取列表
                'count': len(self.beats),
                'todos': getattr(result, 'todo_count', 0)}

    def _with_generator_marker(self, text: str) -> str:
        """
        产物**必须带生成标记**，补丁那条路也一样（2026-10-05 实测）。

        为什么：`patch_export` 的合并策略是"保留原文那一行"，
        它产出的首行是**源文件的首行**（常常是 `default …` 之类），
        于是补丁产出的文件**没有生成标记**。后果是护栏认不出自家产物：

            第一次导出 → ok=True   （补丁成功，写出去的没有标记）
            第二次导出 → 拒绝覆盖  （被判成"手写文件"）

        也就是说"再导出一次"在一个已经由工具生成的文件上必然失败 ——
        这正是 `test_allows_overwriting_its_own_output` 想防的那种事故，
        只不过它当时只在 `write_generation` 这一层测，没经过 export。
        """
        return ensure_generator_marker(text)

    def _realign_memory_after_write(self, art, target) -> None:
        """
        **写回"我们载入的那个文件"之后，内存必须重新对齐到磁盘。**

        为什么（2026-10-05 实测，`tools/acceptance_workflow.py` 抓到）：
        写回之后磁盘上那一份已经是新内容（补丁插了行、首行多了生成标记），
        而内存拍的 `src_first/src_last` 还是**改动前**那份文件的 →
        第二次原地导出对位率掉到 0~50% → 补丁失败 → P0 那条"目标就是源文件"
        的护栏拒写。用户看到 `PATCH_FAILED：对位率不足`，而它偏偏落在
        **最常用的那条循环**上：

            改一句 → 导出 → 再改一句 → 再导出   ← 第二次就断

        为什么是"重新解析"而不是"按内容猜行号"（先走错过一步，记下来）：
        第一版我用 `recover_source_ranges`（解析磁盘 + difflib 对拍）贴行号，
        它在最需要它的那个场景上**是错的** —— 解析器会把"注释 / `call screen`
        + 紧随其后的台词"吸成**一张**拍，而内存里是**两张** → 对齐给出交叠
        区间 → 对位率 50%（实测 whackamole）。合并**本来知道**每一拍写到了
        哪几行，但把它报出来要动 `patch_export` 里"替换 / 插入 / 删除"
        三套坐标系的换算（全仓最危险的一段）。而"内存 := 解析(磁盘)"
        是**零启发**的：磁盘就是真源（契约 A1），解析器怎么读、内存就怎么存。

        代价（可接受，而且本来就该如此）：若刚插了一张只有注释/代码的卡，
        重载后它会与后面那张并成一张 —— **那正是重新打开这个文件会看到的**，
        只是把"下次打开才发生的事"提前到了现在。
        """
        if art.written and self.script_path \
                and Path(target) == Path(self.script_path):
            self._reload_after_inplace_write()

    def _untouched_export(self, target, text, note, result):
        """零改动往返：不写、不加标记（`export` 的第一道门）。"""
        # —— **一个字节都没改 → 不写、不加标记**（2026-10-19，修 P1-1）——
        #
        # 与 CLI 那条路（`pipeline.compile.write_compiled`）同一条判据：
        # 零改动往返从前会被 `ensure_generator_marker` 插进 2 行标记，
        # 于是"表里一个字没改、产物逐字节还原"永远差 2 行。
        # 目标就是源文件时连写都不写（mtime 也不动）；目标在别处时
        # 按源文的载体风格原样复制 —— 复制品仍是作者那份文件。
        if not (note.get('used_patch') and self.script_path):
            return None
        try:
            from storygraph.story.source_style import detect as _style_of
            from storygraph.story.source_style import read_text as _read_src
            from storygraph.story.paths import write_text_atomic as _write
            if text == _read_src(self.script_path)[0]:
                tgt = Path(target)
                same_path = (tgt.resolve()
                             == Path(self.script_path).resolve())
                may_skip = True
                if same_path:
                    pass                # 原地往返：连写都不写（mtime 不动）
                elif not tgt.exists():
                    # 新路径：原样复制（新文件是"新建"的正当用途）
                    _write(tgt, _style_of(self.script_path).apply(
                        text, source=self.script_path))
                elif _read_src(tgt)[0] != text:
                    # ⚠ **目标里是另一份东西**（别人的手写文件、别的章节）
                    # —— 零改动也不是"可以不问就覆盖"的理由。
                    # 交给下面的门禁：它会报 HANDWRITTEN_TARGET，
                    # 由用户决定要不要 force。
                    may_skip = False
                if may_skip:
                    # **零改动也要跑扩展批处理**（2026-11-08）：表里一个字没改，
                    # 但"整章应用"那条规则可能还没落地 —— 早退之前先补，
                    # 不然"导出没效果"会永远复现（用户 2-1-3 正是这一条）。
                    batch = self._apply_declared_batch(tgt)
                    if batch.get('applied') and same_path:
                        self._reload_after_inplace_write()
                    said = [note['text_note']] if note['text_note'] else []
                    if batch.get('note'):
                        said.append(batch['note'])
                    else:
                        said.append('这一份与源文件逐字节相同：'
                                    '**文件没有被改动**')
                    return {'ok': True, 'path': target, 'blocking': [],
                            'codes': [],
                            'note': '；'.join(said),
                            'warnings': list(batch.get('warnings') or ()),
                            'used_patch': True,
                            'untouched': not batch.get('applied'),
                            'count': len(self.beats),
                            'todos': getattr(result, 'todo_count', 0)}
        except (OSError, UnicodeDecodeError) as e:
            # 读不到源就照老路走（过门禁），别把导出弄挂
            swallow('session_export.zero_change_check', e)
            return None
        return None

    def _settle_with_disk(self, patched: str, target, base_text: str) -> dict:
        """
        导出的**第二道门**：把"底稿 + 我的改动"和磁盘上那份合起来。

        什么时候真的起作用：`_patch_note` 发现文件被外人改过、于是把补丁打在
        **底稿**上（`base_text` 非空）。那时 `patched` 里**没有**他改的那几行，
        直接用它会把他那几行抹掉 —— 也就是这一轮修的那个"静默覆盖"。

        三路合并的三种结局（判定在 `write_gate.merge_three_way`，与原地小改
        那条路**同一个函数**）：

          · 不重叠 → 合并结果，短句进状态栏、长句进告警；
          · 撞在同一处 → `blocking`（**一个字节都不写**），理由点到第几张卡；
          · 盘上 == 我这份 → 合并结果就是盘上那份（下一道 `_untouched_export`
            会认出"零改动"）。

        **只有目标是"这一章的源文件本身"时才合**：导出到 `build/` 或别处时，
        产物是"我这一版"—— 他的改动不在目标文件里，合进来反而是往别人的文件里
        掺东西（那也不是他的文件，不归我们管）。

        返回 `{'blocking': […], 'text': …, 'note': 短句, 'warning': 长句}`。
        """
        idle = {'blocking': [], 'text': patched, 'note': '', 'warning': ''}
        base = str(base_text or '')
        if not base or not self.script_path:
            return idle                  # 没有底稿 = 没有"我改了什么"可依据
        try:
            same = (Path(target).resolve()
                    == Path(self.script_path).resolve())
        except OSError:
            # 路径解析不了（极少见：坏盘符 / 太长的路径）→ **当成"就是这一章的
            # 源文件"**。拿不准时合并是安全的那一边：多合一次最多是多重写两行，
            # 少合一次就是静默覆盖别人改过的行（这一轮修的就是后者）。
            # 这里不 `swallow`：它是个窄 except，兜底方向已经在上一句写清了。
            same = True
        if not same:
            return idle
        from storygraph.story.source_style import read_text
        from storygraph.story.write_gate import CONFLICT, merge_three_way
        disk = read_text(self.script_path)[0]
        if not disk:
            # 读成空：要么文件刚被清空、要么正被别的程序占着。**不猜**，也不写。
            return {'blocking': ['这一章的文件刚才读不出来（可能正被别的程序写）：'
                                 '这次没有写。'], 'text': '', 'note': '',
                    'warning': ''}
        if patched == base:
            # 我这一章**一个字都没改**（补丁产出的就是底稿）：盘上那份是他的版本，
            # 原样留着 —— 交给下一道 `_untouched_export` 说"文件没有被改动"。
            # 不要在这里说"两边都落上了"，那会让人以为我这边也写了什么。
            return {'blocking': [], 'text': disk, 'note': '', 'warning': ''}
        verdict = merge_three_way(base, disk, patched,
                                  path=str(self.script_path))
        if verdict.state == CONFLICT:
            return {'blocking': [self._merge_conflict_reason(verdict)],
                    'text': '', 'note': '', 'warning': ''}
        return {'blocking': [], 'text': verdict.final_text,
                'note': verdict.note(), 'warning': verdict.full_note()}

    def _patch_failed_facts(self, res, target=None) -> list:
        """
        补丁没对上时补一句**能看出是哪一类**的事实（2026-11-08 第十九轮）。

        为什么要有（用户原话）：

        > 工具报错说有19处不统一！不给我增量导出……查原因！

        `19 句台词在补丁结果里找不到` 这句只说了"多少"，说不出"**为什么**" ——
        而三种原因的处理办法完全不同：

          · **目标文件里根本没有这些台词**（两份不是同一版）→ 先对齐版本
            （在工具里打开**那一份**再改，或整份导成新章）；
          · 那些台词/卡片是**新写的**（载入时那份文件里没有）→ 增量只能改已有的行；
          · 只是说话人/章节变了 → 重新载入那一版再改。

        第二类判据（"目标文件里有没有"）就是真机那次的关键：用户说
        「这新一份（演出剧本）就是从它里面改的」—— 实测不是：那一份是从
        `2-2-2凌跃捷线-定稿.txt` **重新导入**的（`REPORTER`/`HOST` 没映射成
        已声明角色、`（bgX）` 变成了 `scene`、`(A7)` 变成 `# A7`），和游戏目录
        那份差 211 行。两版之间没有"行"可以对应，增量补丁无从下手。

        返回**一句话的列表**（空 = 没算出什么，不硬说）。
        """
        facts = []
        try:
            beats = list(self.beats or ())
            base = str(getattr(self, '_source_baseline_text', '') or '')
            lines = [ln for ln in base.splitlines()] if base else []
            target_text = ''
            if target is not None:
                tgt = Path(target)
                if tgt.is_file() and tgt != Path(self.script_path or ''):
                    from storygraph.story.source_style import read_text
                    target_text = read_text(tgt)[0]
            fresh_beats = [b for b in beats
                           if not int(getattr(b, 'src_first', 0) or 0)]
            if fresh_beats:
                facts.append(
                    f'工具里这一章有 {len(fresh_beats)} 张卡**在源文件里没有对应行**'
                    f'（新加的卡 / 从别处写回来的）—— 增量补丁只能改**文件里已有'
                    f'的行**，这类卡它写不进去')
            missing_base = _missing_texts(beats, lines)
            missing_tgt = _missing_texts(beats, target_text.splitlines()
                                         if target_text else [])
            if missing_tgt:
                facts.append(
                    f'{len(missing_tgt)} 句台词**目标文件里根本没有**（例：'
                    f'{missing_tgt[0]!r}）—— 工具里这一章和这个文件不是同一版；'
                    f'增量补丁只能在同一份文件上改，两版之间先对齐再导')
            elif missing_base:
                facts.append(
                    f'{len(missing_base)} 句台词在载入时那份文件里找不到（例：'
                    f'{missing_base[0]!r}）—— 它们是新写的，不是"对不上行号"')
            if getattr(res, 'total_beats', 0):
                facts.append(f'这次对位 {res.matched_beats}/{res.total_beats} 拍；'
                             f'工具里 {len(beats)} 拍')
        except (OSError, UnicodeError, ValueError, TypeError,
                AttributeError):
            # **窄捕获、不调 `swallow`**（技术债棘轮：`swallow_calls` 只许降）。
            # 这里算不出来就是"没这条事实"，不该影响拒写这件事本身。
            return facts
        return facts

    def _merge_conflict_reason(self, verdict) -> str:
        """合不了时给用户的一句话（**点到第几张卡**，并给两条退路）。"""
        from storygraph.story.write_gate import (CLASH_ERASED,
                                                 CLASH_REPARENTED,
                                                 CLASH_REPARENTED_OTHER,
                                                 CLASH_TOUCHING)
        spans = tuple(getattr(verdict, 'clash', ()) or ())
        where = '、'.join(_span_cn(a, b) for a, b in spans[:2]) or '同一处'
        who = card_at_lines(self.beats, spans) or '你改的那几行'
        kind = getattr(verdict, 'clash_kind', '')
        if kind == CLASH_REPARENTED:
            # 没撞车：他改/删的是你那一块的**开头那一行**，合起来你的几行会
            # 挂到别的块下面（第十六轮地狱测试：Ren'Py 启动就报缩进错，
            # 而判定说"合上了"）。说成"撞在同一处"同样是假话。
            line, head = (list(getattr(verdict, 'parent', ()) or ())
                          + ['', ''])[:2]
            quote = f'「{head}」' if head else '那一行'
            at = f'你那一版第 {line} 行' if line else '那一行'
            return (f'{who} 那一块的开头{quote}（{at}）被别人改掉或删了 —— '
                    f'合起来你改的那几行会落进别的块里，所以这次一个字节都没写。'
                    f'先看一眼那几行再决定：① 重新载入这一章（会显示他那版）；'
                    f'② 或导出到 build/renpy/ 下比对。')
        if kind == CLASH_REPARENTED_OTHER:
            # `CLASH_REPARENTED` 的**另一面**（第十七轮）：这一次不是他动你的块，
            # 是**你这一版**动了他那几行的块头。同一个坏文件，只看一半就会
            # "拒一个方向、把另一个方向写下去"（探针 probe_r17_symmetry.py）。
            #
            # ⚠ 这里**不点卡片**（`who`）：删掉的那一行在**我这一版里**已经不在了，
            # `card_at_lines` 认不出它，硬点只会点到一句兜底话上。
            line, head = (list(getattr(verdict, 'parent', ()) or ())
                          + ['', ''])[:2]
            quote = f'「{head}」' if head else '那一行'
            at = f'第 {line} 行' if line else '那一行'
            return (f'你这一版把{quote}（{at}）删掉或改了，而他那几行正挂在它'
                    f'下面 —— 合起来那几行会落进别的块里，所以这次一个字节都'
                    f'没写。先看一眼那几行再决定：① 重新载入这一章（会显示他那版）；'
                    f'② 或导出到 build/renpy/ 下比对。')
        if kind == CLASH_ERASED:
            return ('两边各删了一部分，合起来整份文件会变成空的 —— 那不像你要的'
                    '结果，所以这次一个字节都没写。先看一眼再决定：'
                    '① 重新载入这一章；② 或导出到 build/renpy/ 下比对。')
        if kind == CLASH_TOUCHING:
            # 他没动你那一行，只是贴着你加/改了一行 —— 说成"改的正是同一处"
            # 是假话（第十五轮地狱测试抓到：有人只是在旁边加了个注释）。
            return (f'{who}（{where}）旁边，别人也加/改了一行 —— 挨在一起时'
                    f'认不出谁在前，所以这次一个字节都没写。'
                    f'先看一眼那几行再决定：① 重新载入这一章（会显示他那版）；'
                    f'② 或导出到 build/renpy/ 下比对。')
        return (f'{who}（{where}）别人也改过，而且改的正是同一处 —— '
                f'认不出该留谁的那一版，所以这次一个字节都没写。'
                f'先看一眼那几行再决定：① 重新载入这一章（会显示他那版）；'
                f'② 或导出到 build/renpy/ 下比对。')

    def _patch_failed_export(self, target, note):
        """补丁没对上、而覆盖会毁掉不进表的东西 → 拒写（第二道门）。"""
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

    def _generation_blocked(self, target, text, note):
        """产物本身坏了（重名 / 语法 / 图像名）→ 拒写；`force` 也不越过。"""
        occupied = labels_in_danger(target, self.project_root,
                                    exclude={target, self.script_path})
        issues = evaluate_generation(text, target,
                                     project_root=self.project_root,
                                     occupied=occupied, beats=self.beats)
        blocking = [i for i in issues if i.is_blocking]
        if not blocking:
            return None
        # `force` **不**越过这几条：图像名非法 / label 重名 /
        # Python 语法错误都是"产物本身坏了"，覆盖只会把坏脚本写进 game/。
        # `force` 的语义只有一条 —— "我确认要覆盖手写文件"。
        return {'ok': False, 'path': target,
                'blocking': [export_plan.issue_message(i) for i in blocking],
                # **告警单独一格**（2026-10-25，审计第四轮 P2）：
                # 从前 warn 级也塞进 `blocking`，拒因列表里第一条是
                # "产物写在 game/ 目录里"（那需要**挪位置**，不是这次
                # 拒写的原因），真正的原因反而被挤到后面看不见。
                'warnings': [export_plan.issue_message(i)
                             for i in issues if not i.is_blocking],
                'codes': export_plan.block_codes(blocking),
                'note': note['text_note'], 'todos': 0}

    def _write_export(self, target, text, note, force):
        """过兜底门禁 + 落盘，返回 `write_generation` 的产物对象。"""
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
                               beats=self.beats,
                               # **引擎闸门**（2026-10-18，审计 P1-9）：
                               # 界面导出同样是"写进游戏工程"的动作，
                               # 必须与 CLI 走同一道判据 —— 产物引擎读不了
                               # 就不写，改留 `.blocked.rpy` 给人对照。
                               engine_lint=True,
                               lint_ignore_heads=self._declared_stmt_heads())
        return art

    def _write_refused(self, target, note, art):
        """兜底门禁拒了 → 把它的理由与产物路径原样交出去（第四道门）。"""
        _blk = [i for i in art.issues if i.is_blocking]
        _warn = [i for i in art.issues if not i.is_blocking]
        return {'ok': False, 'path': target,
                'blocking': [export_plan.issue_message(i) for i in _blk]
                            or ['生成门禁拒绝写入'],
                'warnings': [export_plan.issue_message(i) for i in _warn],
                'codes': export_plan.block_codes(_blk),
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
            # ⚠ **必须剥 BOM**（2026-10-25，审计 P0-2）：
            # `Path.read_text` 会把开头的 `\ufeff` 留成正文第一个字符，
            # 于是源文第一行是 `'\ufeff# 注释'`，而产物里 BOM 早已被
            # `source_style.read_text` 剥掉 → `opaque_loss` 判"第 1 行顶层
            # 语句没出现在产物里" → `OPAQUE_LOST`（block）→ **凡是带 BOM 的
            # 章节，卡片页原地导出必然被拒**（Windows 中文项目极常见；
            # 官方 tutorial 23/23 全带 BOM）。`pipeline/compile.py` 与
            # `app/tables.py` 早就用 `source_style.read_text` 并留着注释，
            # 这条同样的修法当初漏了这一处。
            from storygraph.story.source_style import read_text as _read_src
            return _read_src(p)[0]
        except (OSError, UnicodeDecodeError) as e:
            swallow('session_export._source_text_for_guard', e)
            return None

    def _declared_stmt_heads(self):
        """
        这个项目声明过的自定义语句名（引擎闸门的**豁免清单**）。

        为什么需要：引擎闸门在一个最小工程里跑 lint，那里没有
        `renpy.register_statement`，于是项目自己的 `mycue …` 会被报成
        `expected statement.` —— 假阳性。拿到声明清单就只跳过这些头，
        其余语法错照拦。拿不到就返回空集（宁可严一点，不放过真错）。
        """
        try:
            from storygraph.pipeline.engine_lint import declared_heads_near
            return declared_heads_near(getattr(self, 'script_path', None)
                                       or getattr(self, 'project_root', None))
        except Exception as e:                  # noqa: BLE001
            swallow('session_export._declared_stmt_heads', e)
            return set()

    def reload_script_from_disk(self) -> int:
        """
        把**当前章节**按磁盘重读一遍（返回拍数）。

        两个调用方，同一件事：
          · 原地写回之后（`_reload_after_inplace_write`）—— 磁盘上那份已经
            是新内容，内存里的行号还是旧的；
          · 放回旧版本之后（`window_recover.show_script_versions`）—— 换了一份
            内容，拍数、行号、脏标记全要跟着变。

        **失败往上抛**（"没重载成功"必须说出口）；要不要吞由调用方决定。
        """
        if not self.script_path:
            raise ValueError('还没有打开章节，没得重读')
        return self.load(self.script_path)

    def _apply_declared_batch(self, target) -> dict:
        """
        导出之后，把**开了批处理的扩展点**按规则补进这一章（2026-11-08）。

        用户 2026-11-08：扩展窗口里勾了「允许把它批处理到整章」，规则写着
        `$ renpy.transition(Dissolve(0.1), layer="master")`，导出 2-1-3
        **一个字都没有** —— 因为「是否能够批处理」那个开关从前只喂
        「批处理窗口 / CLI batch」，导出这条路从不看它。现在导出自己收口。

        只看**扩展声明**（`rules_from_extensions`）；文件里手写的
        `storygraph.batch.json` 仍归批处理窗口 —— 免得同一条规则被两条路
        各补一次（声明那几条自带 `skip_if_present`，但文件里的不一定有）。

        返回 `{'applied': 行数, 'rule': '规则 id', 'note': 一句话,
        'warnings': [...]}`。**规则不合法也照实说**（真机上第一条就是
        `insert.text 不能为空`：界面上打了字、存的时候没带上），一个字不写。
        """
        rel = _batch_rel_target(getattr(self, 'project_root', ''), target)
        if not rel:
            return _NO_BATCH
        rules, warnings = _declared_batch_rules(self.project_root)
        if not rules:
            return dict(_NO_BATCH, warnings=warnings)
        plan = _batch_plan(self.project_root, rules, rel, warnings)
        if plan is None:
            return dict(_NO_BATCH, warnings=warnings)
        done = _apply_batch_plan(plan, warnings)
        rule_ids = '、'.join(str(r.id) for r in rules)
        note = (f'扩展批处理改了 {done} 行（{rule_ids}）' if done else '')
        return {'applied': done, 'rule': rule_ids, 'note': note,
                'warnings': warnings}

    def _batch_after_write(self, target, note) -> dict:
        """
        写完之后补扩展批处理；补进去了就把内存重读、把话并进 `note`。

        为什么**必须重读**：补进去的是原样一行代码，拍的边界会变；下一次
        导出拿旧行号去对位就会掉（见 `_realign_memory_after_write`）。
        """
        batch = self._apply_declared_batch(target)
        if batch.get('applied') and self.script_path \
                and Path(target) == Path(self.script_path):
            self._reload_after_inplace_write()
        if batch.get('note'):
            note['text_note'] = '；'.join(
                x for x in (note.get('text_note', ''), batch['note']) if x)
        return batch

    def _reload_after_inplace_write(self) -> None:
        """
        原地写回之后把内存重新对齐到刚写出的那份文件（见调用点的说明）。

        失败不抛（`swallow`）：同步不了最多是"下一次导出退回问一句"，
        不该把已经成功的这一次导出变成异常。
        """
        from storygraph.core.safeguard import swallow
        try:
            self.reload_script_from_disk()
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
        # `类型` 列填了工具不认的值（2026-10-19，修 P1-2）：`Beat.kind` 会被
        # 收成 `say`，那一拍**看着导出成功、其实不是他填的那个类型**。
        # 与 CLI 那条路同一句话（`CompileResult.summary` 里）。
        from storygraph.story.beat_model import unknown_kinds
        bad = unknown_kinds(self.beats or ())
        notes = []
        if n:
            notes.append(f'读表时 {n} 个单元格里有换行/制表符，已按「一行一个值」'
                         f'压成空格（**值被改过**，核对一下；体检里是 W114）')
        if bad:
            sample = '、'.join(f'[{lid or "?"}]「{k}」' for lid, k in bad[:3])
            notes.append(f'**类型列有 {len(bad)} 行填了工具不认的值**（{sample}'
                         + ('…' if len(bad) > 3 else '')
                         + '）：这些行不会按你填的类型渲染，请改成 say / '
                           'narration / menu / menu_option / extend')
        if not notes:
            return text
        extra = '；'.join(notes)
        return f'{text}；{extra}' if text else extra

    def _patch_note(self, text: str, target=None) -> dict:
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

        **底稿**（2026-11-08 第十五轮）
        ------------------------------
        文件被外人改过时（`source_unchanged()` 为假），从前一律退成
        "只能靠内容证据认行"。可内容证据正好被用户自己改掉了（他在改的就是
        那句台词），于是退回**靠说话人认行** —— 实测把别人改的那一行
        **静默改回去**（`docs/设计_导出这条大路_三路合并_2026-11-08.md` §一）。

        现在这种情况把补丁打在**底稿**上（`base_text`，即 `load()` 解析出这些拍
        的那份文本）：行号与原文同源，`trust_ranges` 的前提从"磁盘没动过"变成
        "这份底稿没动过"。产物是"底稿 + 我的改动"，**再**由 `export` 交给
        `write_gate.merge_three_way` 与盘上那份合并 —— 那一步才知道该保住谁。

        返回值多一个 `base_text`：非空 = 这一刀是打在底稿上的，
        调用方**必须**接着合并（不许直接落盘，否则就是拿旧底稿覆盖他的文件）。
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
        expect_sha1 = ''
        try:
            trust = bool(self.source_unchanged())
            if trust:
                # 核对过的那份指纹原样交给 patcher：它读完之后会再算一遍
                # （"读完再核对"，2026-11-01）—— 窗口里被改过就拒绝增量。
                _fp = getattr(self, '_source_fingerprint', None) or ()
                expect_sha1 = str(_fp[2] if len(_fp) > 2 else '')
        except Exception as e:               # noqa: BLE001 - 判不了就当不新鲜
            swallow('session_export._patch_note.source_unchanged', e)
        # **底稿**（第十五轮）：文件被外人动过时，补丁打在"解析出这些拍的那份"
        # 上面，而不是打在现在这份盘上 —— 否则拍上的行号与原文不同源，
        # 只能靠"说话人一样就认这一拍"猜，实测会把别人改的那一行改回去。
        base_text = ''
        if not trust and src is not None:
            base_text = str(getattr(self, '_source_baseline_text', '') or '')
        try:
            res = patch_export(self.beats, src, text, registry=self.registry(),
                               trust_ranges=(trust or bool(base_text)),
                               expect_sha1=expect_sha1,
                               base_text=base_text)
            if res.ok:
                _note = (f'增量补丁：对位 {res.matched_beats}/'
                         f'{res.total_beats} 拍，'
                         f'改动 {res.changed_lines} 行')
                _note += removal_note(res)
                # **空卡片要说一句**（2026-10-25，审计第四轮 P2）：
                # 插一张空卡、导出，文件一个字节不变 —— 那是**对的**（空卡
                # 不产生行），但回执只说"逐字节相同"，用户以为插卡没生效。
                if getattr(res, 'empty_beats', 0):
                    _note += (f'；另有 {res.empty_beats} 张空卡片还没有任何内容'
                              f'（不会产生行 —— 填上台词/舞台再导出）')
                return {'used_patch': True, 'text': res.text, 'blocking': [],
                        'had_source': had_source, 'text_note': _note,
                        'base_text': base_text}
            reason = ('；'.join(res.reasons)
                      or f'对位率不足（{res.coverage:.0%}）')
            return {'used_patch': False, 'text': '',
                    'blocking': [reason] + self._patch_failed_facts(res, target),
                    'had_source': had_source, 'base_text': base_text,
                    'text_note': f'增量未采用：{reason}'}
        except Exception as e:               # noqa: BLE001
            # 异常**也是**拦截理由，不是"退回全量"的许可证：
            # patcher 崩了说明状态不可信，此时覆盖手写文件风险最大。
            return {'used_patch': False, 'text': '',
                    'blocking': [f'增量不可用：{e}'],
                    'had_source': had_source, 'base_text': base_text,
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
