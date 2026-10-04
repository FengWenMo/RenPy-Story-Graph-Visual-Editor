"""
compile.py
==========
表 → 代码的**编译流水线**：策划表 → Ren'Py 脚本 + 编译结果表。

它只做三件事，而且**从不校验、从不报错、从不拦人**：

    1. 读 Beats（人话）→ 编译成 Ren'Py 代码
    2. 把每条代码行连同"它来自哪个原始意图"写进 Export_RenPy 表
    3. 落盘：一份 .rpy + 一份编译结果工作簿

刻意不做的事（这是上层建筑的纪律，不是遗漏）：

    · 不修改策划的原始表（Sheet 1）——编译结果写到**新文件**里
    · 不做全局校验——找不到资源就在代码里留一行 `# TODO: 缺少资源 …`
      然后继续。想体检请用 `python cli.py validate`（可选工具）

为什么坚持"不报错"：真实的 2600 张图项目，一个全局校验能刷出 1073 条
红字，人的反应是不看。而一行贴着位置的 TODO 注释，人一定会去改。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from storygraph.excel.cells import set_row

EXPORT_SHEET = 'Export_RenPy'
EXPORT_HEADERS = ['LineId', '来源', '原始意图', '编译后的 Ren\'Py 代码',
                  '状态']

STATUS_LABEL = {'ok': '正常', 'todo': '待补资源', 'pass': '直通'}


@dataclass
class CompileResult:
    """一次编译的产物：结果行 + 脚本文本 + 统计 + 门禁结论。"""
    rows: list = field(default_factory=list)
    rpy_text: str = ''
    labels: list = field(default_factory=list)
    #: 产物是否真的写到了目标路径（False = 被生成门禁拦下）
    written: bool = True
    out_path: object = None
    review_path: object = None
    #: 被拦下的致命问题（`script_table.GuardIssue`）；空 = 门禁放行
    guard_issues: list = field(default_factory=list)
    #: 门禁的**警告**（放行了，但有事必须说出来）。
    #:
    #: 为什么要单独一栏（2026-10-05 拓展边界②）：`--force` 时
    #: `OPAQUE_LOST` 会降级成 warn —— 如果调用方只读 `guard_issues`，
    #: 那一刀就成了**静默覆盖**（正是 P0 事故的形态）。所以"写成功了"
    #: 与"有没有话要说"必须分开承载。
    guard_warnings: list = field(default_factory=list)
    #: **这一份产物是不是"在源 .rpy 上增量合并"出来的**（2026-10-05，
    #: 拓展边界②）。True = 原文逐字节保留、只换改过的行，第三类内容
    #: （`default` / `init python` / `transform` …）全都还在。
    used_patch: bool = False
    #: 没走增量时的原因（人话）。`used_patch=False` 时**必须报出来** ——
    #: 偷偷退回全量会丢掉第三类内容（P0 事故形态）。
    patch_note: str = ''
    #: **调用方到底有没有要求走增量**（给了 `--source` 才算）。
    #:
    #: 为什么要这一位：没给源时"没走增量"是**正常默认**，每次都喊一句
    #: "增量未采用"只会变成噪音（用户会开始忽略这类提示，而那正是
    #: `OPAQUE_LOST` 最需要被看见的时候）。给了源却没走成，才是必须说的。
    patch_attempted: bool = False
    #: 增量真正改动的行数（全量时恒 0）
    changed_lines: int = 0
    #: 输入表里一行都没有（`skip_empty=True` 时由调用方据此给退出码）
    empty: bool = False
    #: **读表时被清洗过的单元格数**（值里的换行/制表符被压成空格）。
    #:
    #: 为什么要在这里也带一句（2026-10-05 评审意见）：清洗是**静默改数据**
    #: —— 产物能跑，值却跟表里写的不一样了。体检那条路由
    #: `validate` 的 W114 兜；但"直接导出、从不体检"的人（`cli.py compile`
    #: / 编辑器导出）也得看见，所以这里再报一次。
    sanitized_cells: int = 0

    @property
    def todo_count(self) -> int:
        return sum(1 for r in self.rows if r.get('status') == 'todo')

    @property
    def code_count(self) -> int:
        return sum(1 for r in self.rows if not r['code'].lstrip()
                   .startswith('#'))

    def summary(self) -> str:
        text = (f"编译完成：{len(self.rows)} 行代码 · "
                f"{len(self.labels)} 个 label · "
                f"{self.todo_count} 处待补资源")
        if self.sanitized_cells:
            # 走 `warn` 通道而不是 `block`：产物是**能跑**的，
            # 只是值被改过 —— 该让人核对，不该拦下来。
            text += ('\n读表时 %d 个单元格里有换行/制表符，已按「一行一个值」'
                     '压成空格（**值被改过**，核对一下；体检里是 W114）'
                     % self.sanitized_cells)
        if self.used_patch:
            text += (f"\n增量补丁：只改了 {self.changed_lines} 行，"
                     f"原文其余部分（含 default / init python / transform）"
                     f"逐字节保留")
        elif self.patch_attempted:
            text += f"\n增量未采用（{self.patch_note}）"
        if self.guard_issues:
            text += (f"\n生成门禁：{len(self.guard_issues)} 条致命问题，"
                     f"产物**未写入目标路径**")
            if self.review_path:
                text += (f"（已留 {getattr(self.review_path, 'name', '')} "
                         f"供对照）")
        for w in self.guard_warnings:
            text += f"\n{w.format()}"
        return text


# ---------------------------------------------------------------------------
#  增量合并：有源 .rpy 时，产物**不是**重新生成的，而是补上去的
# ---------------------------------------------------------------------------

@dataclass
class IncrementalOutcome:
    """一次"要不要走增量、走了之后文本是什么"的结论。"""

    text: str
    used_patch: bool = False
    #: 没走增量的人话原因（走了增量时为空）
    reason: str = ''
    changed_lines: int = 0
    coverage: float = 0.0


def merge_incrementally(beats: list, generated_text: str, source_path=None,
                        *, registry=None, symbols=None) -> IncrementalOutcome:
    """
    **"这份产物该不该增量合并"的唯一判据**（2026-10-05，拓展边界②）。

    为什么必须只有一处：这个决定同时决定"要不要保住第三类内容"和
    "门禁那条手写护栏要不要让路"。两处各判一次，迟早一处改了一处没改 ——
    而症状是**静默丢掉 `init python`**（本仓库最贵的一类缺陷）。

    背景（交付评审的三条边界之二）：

        CLI 的 `table xlsx2rpy` 输入只有 `.xlsx`、**没有源 .rpy** →
        patcher 无源 → 永远走全量 → 只能用于**新建**；
        改已有章节只能走 UI 卡牌编辑器。

    有了 `source_path`，命令行这条路就和编辑器**等价**了：原文逐字节保留，
    只替换改过的那几行，`default` / `init python` / `transform` 一个不丢。

    语义（**安全方向不可逆**）：

      · 给了源、补丁对位成功 → `used_patch=True`，`text` 是合并后的全文；
      · 补丁没成功（对位率不足 / 校验不过 / 源不存在）→ `used_patch=False`，
        `text` 回退成传入的全量文本，`reason` 说明为什么。
        **不抛异常、不静默** —— 原因要报到 CLI 上，因为退回全量意味着
        目标若存在就会被门禁拦下（而不是被覆盖）。
    """
    if not source_path:
        return IncrementalOutcome(generated_text, False, '没有给源 .rpy')
    src = Path(source_path)
    if not src.exists():
        return IncrementalOutcome(generated_text, False,
                                  f'源 .rpy 不存在：{src}')
    if not beats:
        return IncrementalOutcome(generated_text, False,
                                  '这一章没有解析出任何拍')

    from storygraph.story.patcher import patch_export

    res = patch_export(beats, src, generated_text,
                       registry=registry, symbols=symbols)
    if res.ok:
        return IncrementalOutcome(res.text, True, '',
                                  changed_lines=res.changed_lines,
                                  coverage=res.coverage)
    reason = '；'.join(res.reasons) or f'对位率不足（{res.coverage:.0%}）'
    return IncrementalOutcome(generated_text, False, reason,
                              coverage=res.coverage)


def write_compiled(result: CompileResult, rpy_path, *, beats=None,
                   source_path=None, registry=None, symbols=None,
                   force: bool = False) -> CompileResult:
    """
    把编译结果落盘 —— **表 → .rpy 的唯一落盘口**。

    两条路，由 `merge_incrementally` 判：

      · **增量**（给了 `source_path` 且对位成功）→ 写合并后的全文，
        过一遍 `ensure_generator_marker`（护栏靠首行标记认自家产物），
        再走 `write_generation(merged_from_source=True)` 让那条"手写护栏"
        让路（原文一字未丢，覆盖它毁不掉任何东西）；
      · **全量**（新建文件 / 没给源 / 对位失败）→ 原样走 `write_generation`，
        该拦就拦（目标已存在的手写 .rpy 一律拒写）。

    为什么抽成函数：`cli.py table xlsx2rpy` 与 `cli.py compile` 曾经各写
    一遍同样的四行（P0 文档 §11.4 收口过一次，这次把增量也一起收进来）——
    门禁字段名、`force`、`source` 的传递改一处漏一处，而漏的那处
    症状就是"静默退回全量"。
    """
    inc = merge_incrementally(beats, result.rpy_text, source_path,
                              registry=registry, symbols=symbols)
    result.used_patch = inc.used_patch
    result.patch_note = inc.reason
    result.patch_attempted = bool(source_path)
    result.changed_lines = inc.changed_lines

    from storygraph.story.generation_guard import (
        ensure_generator_marker,
        write_generation,
    )

    if inc.used_patch:
        text = ensure_generator_marker(inc.text)
    else:
        text = result.rpy_text

    # 给了源就把原文交给门禁：它要**算**"这一份产物是不是丢了顶层语句"
    # （`define` / `init python` / `transform` …）。补丁没配上时，这是
    # 唯一能拦住"静默退回全量"的地方 —— 目标是新路径时手写护栏不适用。
    source_text = None
    if source_path:
        try:
            source_text = Path(source_path).read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            source_text = None

    artifact = write_generation(text, rpy_path, force=force,
                                merged_from_source=inc.used_patch,
                                source_text=source_text,
                                source_path=source_path)
    result.written = artifact.written
    result.out_path = artifact.out_path
    result.review_path = artifact.review_path
    result.guard_issues = list(artifact.blocking)
    result.guard_warnings = list(artifact.warnings)
    return result


def compile_beats(beats: list, registry=None, symbols=None,
                  label_name=None, include_transforms: bool = True
                  ) -> CompileResult:
    """
    Beat 列表 → 编译结果。

    结果行的来源与生成代码**同源**：都是走同一个渲染器（带 trace），
    所以"编译结果表"不可能和 `.rpy` 不一致——它们是一次渲染的两个视图。
    """
    from storygraph.story.dsl import Audit
    from storygraph.story.beat_build import hydrate_from_dsl
    from storygraph.story.beat_render import (
        make_symbols,
        render_beat_to_rpy,
        render_beats_to_rpy,
        render_menu_block,
        split_beat_units,
    )

    beats = list(beats or ())
    if symbols is None:
        symbols = make_symbols(registry)
    audit = Audit(symbols)

    # 先把 DSL 文本解释成结构化字段（舞台快照/导出器要用的形态）
    hydrate_from_dsl(beats, symbols)

    rows = []
    # 菜单按**块**走同一条渲染路径（与 `render_beats_to_rpy` 同一套分组）：
    # 少了这一步，"编译结果表"会把菜单显示成一行行裸语句，而 .rpy 里是
    # `menu:` 块 —— 两个视图就对不上了，而"必然一致"正是这张表存在的理由。
    for kind, unit in split_beat_units(beats):
        if kind == 'menu':
            trace: list = []
            render_menu_block(unit, audit=audit, trace=trace)
            line_id = unit[0].line_id or f"{unit[0].label}.{unit[0].seq:04d}"
            for item in trace:
                rows.append({
                    'line_id': line_id,
                    'source': item.get('source', ''),
                    'intent': item.get('intent', ''),
                    'code': item.get('code', ''),
                    'status': item.get('status', 'ok'),
                })
            continue
        beat = unit[0]
        trace = []
        render_beat_to_rpy(beat, audit=audit, trace=trace)
        for item in trace:
            rows.append({
                'line_id': beat.line_id or f"{beat.label}.{beat.seq:04d}",
                'source': item.get('source', ''),
                'intent': item.get('intent', ''),
                'code': item.get('code', ''),
                'status': item.get('status', 'ok'),
            })

    rpy_text = render_beats_to_rpy(beats, label_name=label_name,
                                   registry=registry, symbols=symbols,
                                   include_transforms=include_transforms)
    labels = []
    for ln in rpy_text.splitlines():
        if ln.startswith('label '):
            labels.append(ln[len('label '):].rstrip(':'))

    # 读表时被清洗过的格子（值里的换行被压成空格）：**必须报出来**，
    # 否则"救了用户却没告诉他"= 静默改数据（见 `CompileResult.sanitized_cells`）。
    sanitized = sum(len(getattr(b, 'sanitized_cells', None) or [])
                    for b in beats)

    return CompileResult(rows=rows, rpy_text=rpy_text, labels=labels,
                         sanitized_cells=sanitized)


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------

def write_export_sheet(source_table, result: CompileResult,
                       output_path=None) -> Path:
    """
    把编译结果写成一张**独立的工作簿**（默认 `<表名>.export.xlsx`）。

    刻意不写回原表：策划的表是他自己的地盘，工具只读不写。
    需要合并进同一份工作簿时用 `merge_export_sheet`。
    """
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    output_path = Path(output_path) if output_path else \
        Path(source_table).with_suffix('.export.xlsx')
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = EXPORT_SHEET
    _fill_export_sheet(ws, result, openpyxl, Font, PatternFill, Alignment)
    wb.save(output_path)
    return output_path


def merge_export_sheet(table_path, result: CompileResult) -> Path:
    """
    把编译结果**作为一张新表**并进策划表所在的工作簿（不碰 Beats）。

    「Sheet 2」的工作流：策划看 Beats，程序看 Export_RenPy。
    """
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    table_path = Path(table_path)
    wb = openpyxl.load_workbook(table_path)
    if EXPORT_SHEET in wb.sheetnames:
        del wb[EXPORT_SHEET]
    ws = wb.create_sheet(EXPORT_SHEET)
    _fill_export_sheet(ws, result, openpyxl, Font, PatternFill, Alignment)
    wb.save(table_path)
    return table_path


def _fill_export_sheet(ws, result, openpyxl, Font, PatternFill, Alignment):
    head_fill = PatternFill('solid', fgColor='FFF2CC')
    head_font = Font(bold=True)
    todo_font = Font(color='C00000')

    for col, head in enumerate(EXPORT_HEADERS, start=1):
        cell = ws.cell(row=1, column=col, value=head)
        cell.fill = head_fill
        cell.font = head_font
        cell.alignment = Alignment(vertical='center')

    for i, row in enumerate(result.rows, start=2):
        values = [row.get('line_id', ''), row.get('source', ''),
                  row.get('intent', ''), row.get('code', ''),
                  STATUS_LABEL.get(row.get('status', 'ok'), '正常')]
        # 安全写（`=` 开头的用户文本当字符串，别被 Excel 当公式）：
        # 唯一实现在 `storygraph.excel.cells.set_row`
        for cell in set_row(ws, i, values):
            if row.get('status') == 'todo':
                cell.font = todo_font

    for col, width in zip('ABCDE', (16, 10, 34, 56, 12)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = 'A2'


def compile_table(table_path, rpy_path=None, registry=None,
                  label_name=None, write_sheet: bool = True,
                  sheet_path=None, force: bool = False,
                  source_path=None, skip_empty: bool = False) -> tuple:
    """
    一步到位：演出表 → (.rpy, 编译结果表)。

    返回 (rpy_path, sheet_path_or_None, CompileResult)。
    不修改输入的演出表。

    `source_path`（2026-10-05 拓展边界②）：**这一章原来那个 .rpy**。
    给了它就走增量合并（原文逐字节保留，第三类内容不丢），
    见 `merge_incrementally`；不给就还是全量生成（新建文件的正当路径）。

    `skip_empty=True`：表里一行都没有时**不写盘**，`result.empty=True`
    ——给"空表要给退出码 2、而不是写出一个空 label"的 CLI 用。

    落盘走**生成门禁**（`write_generation`）：命中 block 级问题（中文图像名、
    或生成的 label 与项目其他 .rpy 重名）时**不写目标路径**，改写
    `<stem>.blocked.rpy` 供人对照，结论记在 `result.written` /
    `result.guard_issues` / `result.review_path` 上。

    2026-09-28 修：这里以前直接 `write_text`，**绕过了门禁** ——
    而"表 → 脚本"正是最常用的一条落盘路径。

    `force=True`（2026-10-05 补，方案 3 的逃生舱）：目标已存在的手写 `.rpy`
    也允许覆盖。**默认拒绝** —— 全量生成只认得"拍"，
    `default` / `init python` / `transform` 一行都留不下
    （见 `docs/P0_静默全量覆盖_2026-10-03.md`）。
    """
    from storygraph.story.excel_import import import_beats_from_excel

    table_path = Path(table_path)
    beats = import_beats_from_excel(table_path, registry=registry)
    if rpy_path is None:
        rpy_path = table_path.with_suffix('.rpy')
    rpy_path = Path(rpy_path)

    if not beats and skip_empty:
        return rpy_path, None, CompileResult(empty=True, out_path=rpy_path,
                                             written=False)

    result = compile_beats(beats, registry=registry, label_name=label_name)
    write_compiled(result, rpy_path, beats=beats, source_path=source_path,
                   registry=registry, force=force)

    sheet = None
    if write_sheet:
        sheet = write_export_sheet(table_path, result, sheet_path)
    return rpy_path, sheet, result


def format_compile_result(result: CompileResult, max_rows: int = 12) -> str:
    """给人看的编译输出（CLI 用）。"""
    lines = [result.summary(), '']
    todos = [r for r in result.rows if r.get('status') == 'todo']
    if todos:
        lines.append('缺少资源（脚本里找不到的引用；新生成/改动的行会写成 # TODO 注释，源文件里原有的行按原样保留）：')
        for r in todos[:max_rows]:
            lines.append(f"  · [{r['line_id']}] {r['code']}")
        if len(todos) > max_rows:
            lines.append(f"  …还有 {len(todos) - max_rows} 处")
        lines.append('')
    if result.labels:
        lines.append('label：' + '、'.join(result.labels[:12])
                     + ('…' if len(result.labels) > 12 else ''))
    return '\n'.join(lines).rstrip()


def export_table_to_rpy(table_path, output_path=None, label_name=None,
                        registry=None, write_export_sheet: bool = True,
                        force: bool = False, source_path=None):
    """
    把演出表编译回 .rpy：**反向路径的便捷入口**（表路径 → .rpy 路径）。

    2026-09-28 从 `story.script_table` 挪到这里：它本质是编译流水线的
    第一层封装，留在 story 层会让 `story → pipeline` 成为反向依赖。
    `story.script_table` 仍以模块级 `__getattr__` 转发本函数，所以
    `from script_table import export_table_to_rpy` 的老写法继续可用。

    找不到资源不报错，只在生成的代码里留 `# TODO: 缺少资源 …`；
    顺手再写一张编译结果表（与脚本同源，必然一致）。原始演出表一个字不改。

    `force=True`：允许覆盖已存在的手写 `.rpy`（逃生舱，见 `compile_table`）。
    `source_path`：这一章原来的 `.rpy` —— 给了就走增量合并，
    保住 `default` / `init python` 等不进表的东西（见 `merge_incrementally`）。
    """
    table_path = Path(table_path)
    if output_path is None:
        output_path = table_path.with_suffix('.rpy')
    rpy_path, _sheet, _result = compile_table(
        table_path, rpy_path=output_path, registry=registry,
        label_name=label_name, write_sheet=write_export_sheet, force=force,
        source_path=source_path)
    return Path(rpy_path)
