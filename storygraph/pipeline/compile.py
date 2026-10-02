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
        if self.guard_issues:
            text += (f"\n生成门禁：{len(self.guard_issues)} 条致命问题，"
                     f"产物**未写入目标路径**")
            if self.review_path:
                text += (f"（已留 {getattr(self.review_path, 'name', '')} "
                         f"供对照）")
        return text


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

    return CompileResult(rows=rows, rpy_text=rpy_text, labels=labels)


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
                  sheet_path=None) -> tuple:
    """
    一步到位：演出表 → (.rpy, 编译结果表)。

    返回 (rpy_path, sheet_path_or_None, CompileResult)。
    不修改输入的演出表。

    落盘走**生成门禁**（`write_generation`）：命中 block 级问题（中文图像名、
    或生成的 label 与项目其他 .rpy 重名）时**不写目标路径**，改写
    `<stem>.blocked.rpy` 供人对照，结论记在 `result.written` /
    `result.guard_issues` / `result.review_path` 上。

    2026-09-28 修：这里以前直接 `write_text`，**绕过了门禁** ——
    而"表 → 脚本"正是最常用的一条落盘路径。
    """
    from storygraph.story.excel_import import import_beats_from_excel
    from storygraph.story.generation_guard import write_generation

    table_path = Path(table_path)
    beats = import_beats_from_excel(table_path, registry=registry)
    result = compile_beats(beats, registry=registry, label_name=label_name)

    if rpy_path is None:
        rpy_path = table_path.with_suffix('.rpy')
    rpy_path = Path(rpy_path)

    artifact = write_generation(result.rpy_text, rpy_path)
    result.written = artifact.written
    result.out_path = artifact.out_path
    result.review_path = artifact.review_path
    result.guard_issues = list(artifact.blocking)

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
                        registry=None, write_export_sheet: bool = True):
    """
    把演出表编译回 .rpy：**反向路径的便捷入口**（表路径 → .rpy 路径）。

    2026-09-28 从 `story.script_table` 挪到这里：它本质是编译流水线的
    第一层封装，留在 story 层会让 `story → pipeline` 成为反向依赖。
    `story.script_table` 仍以模块级 `__getattr__` 转发本函数，所以
    `from script_table import export_table_to_rpy` 的老写法继续可用。

    找不到资源不报错，只在生成的代码里留 `# TODO: 缺少资源 …`；
    顺手再写一张编译结果表（与脚本同源，必然一致）。原始演出表一个字不改。
    """
    table_path = Path(table_path)
    if output_path is None:
        output_path = table_path.with_suffix('.rpy')
    rpy_path, _sheet, _result = compile_table(
        table_path, rpy_path=output_path, registry=registry,
        label_name=label_name, write_sheet=write_export_sheet)
    return Path(rpy_path)
