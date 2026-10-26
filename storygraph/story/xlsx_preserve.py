"""
storygraph.story.xlsx_preserve
==============================
**保住用户在剧本表里手写的东西**。

为什么需要（2026-10-25《不安全因素》第六轮，P0-E）

`export_beats_to_excel()` 从前是 `openpyxl.Workbook()` —— **整份新建**，
再 `save()` 到原路径。而这张 xlsx 是策划的**主工作界面**（本项目的产品前提：
"对着表格就能编排演出"）。于是"脚本改了几行 → 再点一次「生成演出表」"
会把用户在同一份工作簿里的一切抹掉：

  · 自己加的页（`角色设定` / `大纲` / `排期` —— 往往是整个项目的文档）；
  · Beats 页里自己加的列（`配音状态` / `审核` / `翻译状态`）；
  · 批注、底色、冻结窗格、列宽；
  · 而且**没有任何备份**。

审计探针 `tools/_p0_audit_round6_xlsx.py` 实测：6 条判据 **0/6 通过**。

为什么"保住"而不是"先问一句"：这一刀**每次都发生**（每次重新摊表），
问一次就变成每次都问，用户会养成闭眼点"是"的习惯。真正该做的是
**重建工具自己那几张表，把用户的东西原样带过去**。

三件事：
  1. `backup_table()` —— 覆盖前先留一份 `*.bak-<时间戳>.xlsx`（兜底）；
  2. `snapshot_user_content()` / `restore_user_content()` —— 用户自加的**整页**
     逐格带过去（值 + 样式 + 批注 + 列宽 + 合并 + 冻结）；
  3. Beats 页里用户自加的**列**，按 `LineId`（隐藏的"行号"列）回填 ——
     行增删之后也能对上，因为身份是 `LineId` 而不是行号。
     对不上的（新拍的、旧表没有的）留空，并把条数报出来。
"""

from __future__ import annotations

import shutil
from copy import copy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 工具自己维护的工作表（重建时整张重写，用户不该在这里写东西）。
#: 判据是"这张表由 `export_beats_to_excel` 造出来" —— 与写入函数一一对应，
#: 别的（用户自加的）一律当"人的东西"。
TOOL_SHEETS = frozenset({
    'Beats',
    '文件级保真',
    '长文本保真',
    '舞台快照',
    '演出时间轴',
    '列说明',
    '资源图库',
    '资源引用（活）',
    '词表',
    '使用说明',
    '表信息',
    # 协作层（`collab pull` 自己会重写它）：不当"用户的页"搬运，
    # 否则每次 pull 都要把这张表整份复制一遍再删掉。
    '协作信息',
})

#: 候选表前缀（`Candidates_角色` / `Candidates_图片` …）—— 同样由工具重建。
CANDIDATE_PREFIX = 'Candidates_'

#: Beats 页里"行身份"那一列的表头（`beat_fields` 里 `LineId` 的 header）。
LINE_ID_HEADER = '行号'


def _norm_path(value) -> str:
    text = str(value or '').strip().replace('\\', '/')
    return text.lstrip('./').lower()


def is_tool_sheet(name) -> bool:
    """这张表是不是工具自己造的（重建时会被整张重写）。"""
    text = str(name or '')
    return text in TOOL_SHEETS or text.startswith(CANDIDATE_PREFIX)


# ----------------------------------------------------------------------
#  1. 备份
# ----------------------------------------------------------------------

def backup_table(path, *, keep: int = 3) -> 'Path | None':
    """
    把 `path` 复制成 `名字.bak-YYYYmmdd-HHMMSS.xlsx`，只保留最近 `keep` 份。

    读不出来的旧表也照备份（`shutil.copy2` 是字节复制，不需要解析）——
    "先备份"这一步不该因为"表坏了"而跳过，恰恰是表坏了才更需要备份。
    """
    p = Path(path)
    if not p.is_file():
        return None
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    bak = p.with_name(f'{p.stem}.bak-{stamp}{p.suffix}')
    try:
        shutil.copy2(p, bak)
    except OSError as e:                       # noqa: BLE001
        swallow('story.xlsx_preserve.backup_table', e)
        return None
    try:
        olds = sorted(p.parent.glob(f'{p.stem}.bak-*{p.suffix}'))
        for extra in olds[:-keep] if keep > 0 else []:
            extra.unlink(missing_ok=True)
    except OSError as e:                       # noqa: BLE001 - 清旧备份失败不影响本次
        swallow('story.xlsx_preserve.backup_table.prune', e)
    return bak


# ----------------------------------------------------------------------
#  2. 快照 / 还原
# ----------------------------------------------------------------------

@dataclass
class UserSheet:
    """用户自加的一整张表（值 + 能带走的格式）。"""

    name: str
    cells: list = field(default_factory=list)      # [(row, col, value, style, comment)]
    widths: dict = field(default_factory=dict)     # {'A': 14.5}
    heights: dict = field(default_factory=dict)    # {1: 30}
    merged: list = field(default_factory=list)     # ['A1:B2']
    freeze: str = ''

    def is_empty(self) -> bool:
        return not any(v not in (None, '') for _, _, v, _, _ in self.cells)


@dataclass
class UserColumn:
    """Beats 页里用户自加的一列（按 LineId 回填，行增删也能对上）。"""

    header: str
    values: dict = field(default_factory=dict)     # {line_id: value}
    width: float = 0.0


@dataclass
class Snapshot:
    sheets: list = field(default_factory=list)     # [UserSheet]
    columns: list = field(default_factory=list)    # [UserColumn]
    #: Beats 页上"人标过"的格子：`{line_id: {表头: {'fill':…, 'font':…, 'comment':…}}}`
    #:
    #: 为什么按 `LineId` 而不按行号：行会增删（脚本改了），行号对不上；
    #: 而"这句要重录"是**跟着那句话说**的，不是跟着第 3 行说的。
    #: 为什么按**表头**记列：工具可能新加动态列（`with 的转场` 之类），
    #: 列号会整体右移 —— 表头才是稳定坐标。
    row_formats: dict = field(default_factory=dict)
    #: Beats 页原来的冻结窗格（用户自己调过就尊重他）
    beats_freeze: str = ''

    def is_empty(self) -> bool:
        return not (self.sheets or self.columns or self.row_formats
                    or self.beats_freeze)


def snapshot_user_content(table_path, *, tool_headers=()) -> 'Snapshot | None':
    """
    读旧表的"人的东西" → `Snapshot`。

    读不出来（没有这张表 / 不是 xlsx / 文件坏了 / 正被 Excel 独占）→ `None`：
    这时候**不能假装"没有用户内容"**，调用方要据 `None` 决定是否拒绝覆盖
    （见 `export_beats_to_excel`：读不出 + 备份不成就拒写）。
    """
    p = Path(table_path)
    if not p.is_file():
        return None
    # **一次完整读取**（2026-10-25 第六轮）：一张 400 拍的剧本表约 1.5 秒。
    # 试过"先流式探一眼、没内容就跳过"，省下的那点时间换来的是
    # "格子上有批注也探测不到"（read_only 模式拿不到 comment）——
    # 一个 P0 换 1 秒不值得。这是一次手动动作（每章重新摊表时一次），
    # 慢一点可以接受。
    try:
        import openpyxl
        wb = openpyxl.load_workbook(p, data_only=False)
    except Exception as e:                     # noqa: BLE001 - 见 docstring
        swallow('story.xlsx_preserve.snapshot.open', e)
        return None

    snap = Snapshot()
    try:
        known = {str(h or '') for h in (tool_headers or ())}
        for name in wb.sheetnames:
            ws = wb[name]
            if is_tool_sheet(name):
                if name == 'Beats':
                    snap.columns = _snapshot_user_columns(ws, known)
                    snap.row_formats = _snapshot_row_formats(ws)
                    snap.beats_freeze = str(ws.freeze_panes or '')
                continue
            snap.sheets.append(_snapshot_sheet(ws, name))
    finally:
        try:
            wb.close()
        except Exception as e:                 # noqa: BLE001
            swallow('story.xlsx_preserve.snapshot.close', e)
    return snap


def _snapshot_sheet(ws, name) -> UserSheet:
    sheet = UserSheet(name=name, freeze=str(ws.freeze_panes or ''))
    # Excel 的残留格式能把 `max_row` 撑到几十万行（一个远处的底色就够）——
    # 连续 200 行全空就收手，别为幽灵行白扫几秒（也会把内存撑起来）。
    blanks = 0
    for row in ws.iter_rows():
        touched = False
        for cell in row:
            if cell.value is None and not cell.has_style and not cell.comment:
                continue
            touched = True
            sheet.cells.append((cell.row, cell.column, cell.value,
                                copy(cell._style) if cell.has_style else None,
                                copy(cell.comment) if cell.comment else None))
        blanks = 0 if touched else blanks + 1
        if blanks >= 200 and sheet.cells:
            break
    for letter, dim in (ws.column_dimensions or {}).items():
        if getattr(dim, 'width', None):
            sheet.widths[str(letter)] = dim.width
    for idx, dim in (ws.row_dimensions or {}).items():
        if getattr(dim, 'height', None):
            sheet.heights[idx] = dim.height
    try:
        sheet.merged = [str(rng) for rng in ws.merged_cells.ranges]
    except Exception as e:                     # noqa: BLE001
        swallow('story.xlsx_preserve.snapshot.merged', e)
    return sheet


def _snapshot_user_columns(ws, tool_headers) -> list:
    """Beats 页里**不在工具列集合**里的列 → 按 LineId 收值。"""
    header_row = next(ws.iter_rows(min_row=1, max_row=1), ())
    headers = [str(c.value or '').strip() for c in header_row]
    if LINE_ID_HEADER not in headers:
        return []                              # 老表没有行号列：没法对行，不搬
    id_idx = headers.index(LINE_ID_HEADER)
    out = []
    for col, header in enumerate(headers, start=1):
        if not header or header in tool_headers:
            continue
        values, seen = {}, 0
        for row in ws.iter_rows(min_row=2):
            if col - 1 >= len(row):
                break
            lid = row[id_idx].value if id_idx < len(row) else None
            val = row[col - 1].value
            if val in (None, ''):
                continue
            seen += 1
            key = str(lid or '').strip()
            if key:
                values[key] = val
        if seen:
            width = 0.0
            try:
                from openpyxl.utils import get_column_letter
                dim = ws.column_dimensions.get(get_column_letter(col))
                width = float(getattr(dim, 'width', 0) or 0)
            except Exception as e:             # noqa: BLE001
                swallow('story.xlsx_preserve.snapshot.width', e)
            out.append(UserColumn(header=header, values=values, width=width))
    return out


def restore_user_content(wb, snap: 'Snapshot | None', *, beats=()) -> dict:
    """
    把 `Snapshot` 铺回**新建的**工作簿 `wb`。返回回执用的计数。

    搬三样东西：
      · 用户自加的**整张页**（值 + 样式 + 批注 + 列宽 + 合并 + 冻结）；
      · Beats 里用户自加的**列**（按 `LineId` 回填，行增删也对得上）；
      · Beats 上人**标过**的格子（底色 / 字色 / 批注，同样按 `LineId` + 表头定位）
        以及他自己调过的冻结窗格。

    `beats` 用于把 `LineId` 落到新行号上；对不上的行留空并计数。
    """
    stats = {'sheets': 0, 'columns': 0, 'filled': 0, 'unmatched': 0,
             'formats': 0}
    if snap is None or wb is None:
        return stats

    for sheet in snap.sheets:
        try:
            ws = wb.create_sheet(sheet.name)
        except Exception as e:                 # noqa: BLE001 - 重名等，跳过一个
            swallow('story.xlsx_preserve.restore.create', e)
            continue
        for row, col, value, style, comment in sheet.cells:
            cell = ws.cell(row=row, column=col, value=value)
            if style is not None:
                cell._style = copy(style)
            if comment is not None:
                cell.comment = copy(comment)
        for letter, width in sheet.widths.items():
            try:
                ws.column_dimensions[letter].width = width
            except Exception as e:             # noqa: BLE001
                swallow('story.xlsx_preserve.restore.width', e)
        for idx, height in sheet.heights.items():
            try:
                ws.row_dimensions[idx].height = height
            except Exception as e:             # noqa: BLE001
                swallow('story.xlsx_preserve.restore.height', e)
        for rng in sheet.merged:
            try:
                ws.merge_cells(rng)
            except Exception as e:             # noqa: BLE001
                swallow('story.xlsx_preserve.restore.merge', e)
        if sheet.freeze:
            ws.freeze_panes = sheet.freeze
        stats['sheets'] += 1

    if snap.columns and beats:
        ws = wb['Beats'] if 'Beats' in wb.sheetnames else wb.active
        header = [str(c.value or '').strip() for c in ws[1]]
        if LINE_ID_HEADER in header:
            id_col = header.index(LINE_ID_HEADER) + 1
            ids = {}
            for row in range(2, ws.max_row + 1):
                ids[row] = str(ws.cell(row=row, column=id_col).value or '').strip()
            start = ws.max_column + 1
            for offset, col in enumerate(snap.columns):
                target = start + offset
                head = ws.cell(row=1, column=target, value=col.header)
                ref = ws.cell(row=1, column=1)
                try:
                    head._style = copy(ref._style)
                except Exception as e:         # noqa: BLE001
                    swallow('story.xlsx_preserve.restore.head_style', e)
                if col.width:
                    try:
                        from openpyxl.utils import get_column_letter
                        ws.column_dimensions[get_column_letter(target)].width = col.width
                    except Exception as e:     # noqa: BLE001
                        swallow('story.xlsx_preserve.restore.col_width', e)
                for row, lid in ids.items():
                    if not lid:
                        continue
                    val = col.values.get(lid)
                    if val in (None, ''):
                        continue
                    try:
                        ws.cell(row=row, column=target, value=val)
                        stats['filled'] += 1
                    except Exception as e:     # noqa: BLE001
                        swallow('story.xlsx_preserve.restore.cell', e)
                # 旧表里有值、新表里没有这一行（拍被删了）→ 记账，不静默
                live = {v for v in ids.values() if v}
                stats['unmatched'] += sum(
                    1 for lid in col.values if lid and lid not in live)
                stats['columns'] += 1
        stats['formats'] = _restore_row_formats(ws, snap.row_formats)
        # 冻结窗格：用户自己调过就尊重他（纯视图，不影响任何判据）
        if snap.beats_freeze:
            try:
                ws.freeze_panes = snap.beats_freeze
            except Exception as e:             # noqa: BLE001
                swallow('story.xlsx_preserve.restore.freeze', e)
    return stats


__all__ = ['CANDIDATE_PREFIX', 'LINE_ID_HEADER', 'Snapshot', 'TOOL_SHEETS',
           'UserColumn', 'UserSheet', 'backup_table', 'is_tool_sheet',
           'restore_user_content', 'snapshot_user_content']


# ----------------------------------------------------------------------
#  3. Beats 页上"人标过的格子"
# ----------------------------------------------------------------------

def _cell_mark(cell):
    """这一格有没有"人留下的痕迹"（底色 / 字色 / 批注）。没有返回 None。"""
    # **先看一个廉价的开关**（性能）：`ws.append` 写出来的格子没有样式，
    # `has_style` 直接是 False —— 绝大多数格子在这里就返回了，
    # 不用去构造 `fill` / `font` 的代理对象（400 拍 × 35 列 = 14000 次）。
    if not cell.has_style and not cell.comment:
        return None
    try:
        fill = getattr(cell.fill, 'fgColor', None)
        rgb = str(getattr(fill, 'rgb', '') or '')
        has_fill = bool(rgb) and rgb not in ('00000000', 'FFFFFFFF')
        font = cell.font
        has_font = bool(getattr(font, 'color', None) is not None
                        and str(getattr(font.color, 'rgb', '') or '')
                        not in ('', 'FF000000', '00000000'))
        if not (has_fill or has_font or cell.comment):
            return None
        return {'fill': rgb if has_fill else '',
                'font': copy(font) if has_font else None,
                'comment': copy(cell.comment) if cell.comment else None}
    except Exception as e:                      # noqa: BLE001 - 判不了就当没痕迹
        swallow('story.xlsx_preserve.asset.cell_mark', e)
        return None


def _snapshot_row_formats(ws) -> dict:
    from openpyxl.utils import get_column_letter

    header_row = next(ws.iter_rows(min_row=1, max_row=1), ())
    headers = [str(c.value or '').strip() for c in header_row]
    if LINE_ID_HEADER not in headers:
        return {}
    id_idx = headers.index(LINE_ID_HEADER) + 1
    out = {}
    # `max_row` 会被 Excel 的残留格式撑到几十万行（真实发生过）——
    # 扫到连续这么多空行就收手，别为一个幽灵行把摊表卡住。
    blanks = 0
    last = min(int(ws.max_row or 0), 2 + 1000 * 200)
    for r in range(2, last + 1):
        lid = str(ws.cell(row=r, column=id_idx).value or '').strip()
        if not lid:
            blanks += 1
            if blanks >= 200:
                break
            continue
        blanks = 0
        marks = {}
        for c in range(1, len(headers) + 1):
            header = headers[c - 1]
            if not header:
                continue
            mark = _cell_mark(ws.cell(row=r, column=c))
            if mark:
                marks[header] = mark
        if marks:
            out[lid] = marks
    return out


def _restore_row_formats(ws, row_formats) -> int:
    """按 `LineId` + 表头把"人标过的格子"铺回去，返回铺了几格。"""
    if not row_formats:
        return 0
    from openpyxl.styles import PatternFill

    header = [str(c.value or '').strip() for c in ws[1]]
    if LINE_ID_HEADER not in header:
        return 0
    id_col = header.index(LINE_ID_HEADER) + 1
    col_of = {name: i + 1 for i, name in enumerate(header) if name}
    applied = 0
    for r in range(2, ws.max_row + 1):
        lid = str(ws.cell(row=r, column=id_col).value or '').strip()
        marks = row_formats.get(lid)
        if not marks:
            continue
        for name, mark in marks.items():
            c = col_of.get(name)
            if not c:
                continue                      # 工具这一版没有这一列了
            cell = ws.cell(row=r, column=c)
            try:
                if mark.get('fill'):
                    cell.fill = PatternFill('solid', fgColor=mark['fill'])
                if mark.get('font') is not None:
                    cell.font = copy(mark['font'])
                if mark.get('comment') is not None:
                    cell.comment = copy(mark['comment'])
                applied += 1
            except Exception as e:             # noqa: BLE001 - 一格挂不上不影响别的
                swallow('story.xlsx_preserve.restore.row_format', e)
    return applied


# ----------------------------------------------------------------------
#  4. 平铺表（资源表 / 角色表 / 变换表）：按 ID 保住人的东西
# ----------------------------------------------------------------------
#
#  为什么单独一套（P0-F，2026-10-25 第六轮下半）：
#  `write_asset_excel()` 用 `write_dict_to_excel` **整份重建**四张资源表
#  （images / music / sounds / voices）。代码自己的 docstring 就记着代价：
#  "2828 行会掉到 349 行"。而这些表是策划维护的主数据 ——
#  自加的列（`配音演员` / `审核`）、自加的页、格子上的颜色批注、
#  手写的行（还没有素材的立绘、临时占位），一次「扫描资源」全没。
#
#  与剧本表（Beats）的差别：这里的行身份是 **`编号`(ID)**，不是 `LineId`；
#  而且"行该不该留"多一条判据：**记了真实文件、文件又真的不在了** → 删
#  （那是表在跟随磁盘）；**没记文件**（人写的占位）→ 留。

@dataclass
class AssetSnapshot:
    """一张平铺资源表的"人的东西"。"""

    #: 旧表表头（含人的列），按原顺序
    headers: list = field(default_factory=list)
    #: 旧表的行：`[{表头: 值}]`，按行序（含没有 ID 的行）
    rows: list = field(default_factory=list)
    #: 不在工具列里的表头（人自加的列）
    user_headers: list = field(default_factory=list)
    #: 人标过的格子：`{ID: {表头: {'fill'/'font'/'comment'}}}`
    marks: dict = field(default_factory=dict)
    #: 额外的工作表（工具只写第一张，其余都是人的）
    sheets: list = field(default_factory=list)
    #: 这次扫描没有产出、但按判据该留下的行 / 该删掉的行数
    kept_rows: list = field(default_factory=list)
    dropped_rows: int = 0
    #: 认不出身份列（`编号`）—— 行只能按 `真实文件` 对，不能按 ID 对
    id_missing: bool = False
    #: 这份快照是 **zip 级抢救**回来的（openpyxl 打不开、sheet XML 还在）。
    #: 抢救行只许进 keep 通道（人的行），**永远不许覆盖扫描行** ——
    #: 抢救值的类型可能与 openpyxl 读出的不一致（数字变串）。
    #: 批注 / 底色 / 额外页**救不到**（要 styles/workbook 元数据），
    #: 回执里必须点名"这三样未能抢救"，不许装作保住了。
    rescued: bool = False

    def user_values(self, asset_id, path=None) -> dict:
        """
        这一行的人在自加列里填的值：**先按 ID，再按 `真实文件`**。

        为什么要有路径这一档：用户把「编号」列改名之后，ID 对不上，
        但"这个文件的配音演员是谁"仍然跟着**文件**走 —— 只要路径还在，
        人的数据就不该丢。
        """
        key = str(asset_id or '')
        if key and not self.id_missing:
            for row in self.rows:
                if str(row.get(self.id_header, '') or '') == key:
                    return {h: row.get(h, '') for h in self.user_headers}
        want = _norm_path(path)
        if want:
            for row in self.rows:
                if _norm_path(row.get(self.path_header, '')) == want:
                    return {h: row.get(h, '') for h in self.user_headers}
        return {}

    #: 由 `snapshot_asset_table` 填（`编号` / `编号` 的英文键）
    id_header: str = '编号'
    path_header: str = '真实文件'


#: 最近一次资源表写入"保住了什么"（给回执用，与 `last_preserve_stats` 同型）
_LAST_ASSET_STATS: dict = {}


def last_asset_stats() -> dict:
    return dict(_LAST_ASSET_STATS)


def snapshot_asset_table(table_path, tool_headers,
                         id_header: str = '编号',
                         path_header: str = '真实文件') -> 'AssetSnapshot | None':
    """
    读一张平铺资源表 → `AssetSnapshot`。读不出来返回 None（调用方退回老行为）。
    """
    p = Path(table_path)
    if not p.is_file():
        return None
    try:
        import openpyxl
        wb = openpyxl.load_workbook(p, data_only=False)
    except PermissionError:
        # **被占用 ≠ 坏表**（2026-10-26，P1 讨论④ 第一层）：Excel 开着时
        # Windows 抛 PermissionError。此时表没坏 —— 一个字节都不该动，
        # 更不该"隔离 + 重建"（现状就把锁着的表推平了，用户回来发现
        # 自己的表没了）。原样抛给调用方：写盘侧 `save_workbook` 的
        # `_save_hint` 会说人话（"Excel 开着"）。
        raise
    except Exception as e:                     # noqa: BLE001
        swallow('story.xlsx_preserve.asset.open', e)
        # **坏表先抢救，再谈重建**（第二层）：openpyxl 拒载 ≠ 行没了。
        # workbook.xml 损坏 / 缺 styles 时 sheet XML 常常完好。
        headers, rows = salvage_rows(p)
        if not headers:
            return None                        # zip 都烂了 → 调用方隔离重建
        snap = AssetSnapshot(id_header=id_header, path_header=path_header,
                             rescued=True)
        snap.headers = headers
        snap.id_missing = id_header not in snap.headers
        # 用户列全算（工具列名单由调用方传；抢救场景下我们分不清
        # 哪列是工具的 —— `write_dict_to_excel` 再按规范键裁一次）
        snap.user_headers = [h for h in headers if h]
        for row in rows:
            snap.rows.append(dict(row))
        return snap

    snap = AssetSnapshot(id_header=id_header, path_header=path_header)
    try:
        ws = wb.worksheets[0] if wb.worksheets else None
        if ws is None:
            return snap
        header_row = next(ws.iter_rows(min_row=1, max_row=1), ())
        snap.headers = [str(c.value or '').strip() for c in header_row]
        known = {str(h or '') for h in (tool_headers or ())}
        snap.user_headers = [h for h in snap.headers if h and h not in known]
        # 认不出身份列（用户把「编号」改名/删了）**不等于整张表没人的东西**：
        # 自加的页、自加的列、列里的值都还在（后者按 `真实文件` 对行）。
        snap.id_missing = id_header not in snap.headers

        blanks = 0
        for r in range(2, min(int(ws.max_row or 0), 2 + 200000) + 1):
            values = {}
            any_value = False
            for c, header in enumerate(snap.headers, start=1):
                if not header:
                    continue
                cell = ws.cell(row=r, column=c)
                values[header] = cell.value
                if cell.value not in (None, ''):
                    any_value = True
                    mark = _cell_mark(cell)
                    if mark:
                        aid = str(values.get(id_header) or '').strip()
                        if aid:
                            snap.marks.setdefault(aid, {})[header] = mark
            if not any_value:
                blanks += 1
                if blanks >= 200:
                    break
                continue
            blanks = 0
            snap.rows.append(values)

        # 额外的工作表（工具只写第一张）
        for name in wb.sheetnames[1:]:
            snap.sheets.append(_snapshot_sheet(wb[name], name))
    finally:
        try:
            wb.close()
        except Exception as e:                 # noqa: BLE001
            swallow('story.xlsx_preserve.asset.close', e)
    return snap


#: 抢救时认的表格 XML 命名空间（SpreadsheetML）
_NS_SHEET = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
#: Excel 单列上限（XFD = 16384）—— 列字母解析越界就停
_MAX_COL = 16384


def _col_letters_to_index(ref: str) -> int:
    """`B`→2 / `AA`→27（1 基）；认不出返回 0。"""
    n = 0
    for ch in ref:
        if 'A' <= ch <= 'Z':
            n = n * 26 + (ord(ch) - 64)
        elif 'a' <= ch <= 'z':
            n = n * 26 + (ord(ch) - 96)
        else:
            break
    return n if 1 <= n <= _MAX_COL else 0


def salvage_rows(table_path) -> tuple:
    """
    **zip 级抢救**一张 openpyxl 打不开的 xlsx → `(headers, rows)`；
    救不了返回 `([], [])`（调用方才退到"隔离 + 重建"）。

    为什么需要（2026-10-26，P1 讨论④）：表坏了（workbook.xml 损坏 / 缺
    styles.xml / Excel 把文件写残）时，`snapshot_asset_table` 直接返回
    None → 重建时**人的列 / 行 / 页 / 批注全没**，只剩一个 `.broken-` 隔离
    副本。用户对同类问题（P0-F）明确表态过"不要做隔离备份，这解决不了
    根本问题" —— 表读不出 ≠ 行救不回：xlsx 就是个 zip，**sheet XML 常常
    还完好**。

    读法（只认字面量，不猜语义）：
      · `xl/sharedStrings.xml` → 共享字符串表（`t="s"` 的索引源）；
      · `xl/worksheets/sheet1.xml` → 逐行逐格；值分支四种：
        `t="s"`（共享表下标）/ `t="inlineStr"`（内联串，openpyxl 默认写法，
        **漏了它就读出一片空**——探针实测过）/ `t="str"` / 无 t（数字）。
      · 只要 **第一张表**（工具只写第一张；后面的页是人的，本函数救不到、
        由调用方在回执里点名"未能抢救"）。

    ⚠ 抢救行只许进 **keep 通道**（人的行），**永远不许覆盖扫描行** ——
    抢救值的类型可能与 openpyxl 读出的不一致（数字变串），拿它去覆盖
    `scan_assets` 的自动字段就是把干净数据改脏。
    """
    p = Path(table_path)
    if not p.is_file():
        return [], []
    try:
        import zipfile
        import xml.etree.ElementTree as ET

        with zipfile.ZipFile(p) as z:
            names = set(z.namelist())
            shared: list = []
            if 'xl/sharedStrings.xml' in names:
                try:
                    root = ET.fromstring(z.read('xl/sharedStrings.xml'))
                    for si in root.findall(f'{_NS_SHEET}si'):
                        # 一个 `si` 里可能有多个 `r`（富文本）→ 拼起来
                        parts = [t.text or ''
                                 for t in si.iter(f'{_NS_SHEET}t')]
                        shared.append(''.join(parts))
                except Exception as e:              # noqa: BLE001
                    swallow('story.xlsx_preserve.salvage.shared', e)
            sheet_name = next(
                (n for n in ('xl/worksheets/sheet1.xml',)
                 if n in names), None)
            if sheet_name is None:
                # 退一步：任意 sheet*.xml 里取第一个（工作簿元数据坏了时
                # 文件名未必叫 sheet1）
                sheet_name = next(
                    (n for n in sorted(names)
                     if n.startswith('xl/worksheets/sheet')
                     and n.endswith('.xml')), None)
            if sheet_name is None:
                return [], []
            try:
                root = ET.fromstring(z.read(sheet_name))
            except Exception as e:                  # noqa: BLE001
                swallow('story.xlsx_preserve.salvage.sheet', e)
                return [], []
    except Exception as e:                          # noqa: BLE001 - zip 都打不开
        swallow('story.xlsx_preserve.salvage.open', e)
        return [], []

    def _cell_text(c) -> str:
        t = c.get('t') or ''
        if t == 's':
            v = c.find(f'{_NS_SHEET}v')
            try:
                return shared[int(v.text)] if v is not None and v.text else ''
            except (ValueError, IndexError):
                return ''
        if t == 'inlineStr':
            is_el = c.find(f'{_NS_SHEET}is')
            if is_el is None:
                return ''
            return ''.join(x.text or ''
                           for x in is_el.iter(f'{_NS_SHEET}t'))
        v = c.find(f'{_NS_SHEET}v')
        return '' if v is None or v.text is None else v.text

    raw_rows: list = []
    for row in root.iter(f'{_NS_SHEET}row'):
        cells: dict = {}
        for c in row.findall(f'{_NS_SHEET}c'):
            ref = c.get('r') or ''
            # 列号优先取 `r="B3"` 里的字母；没有就按出现序（少见到但认字面）
            idx = _col_letters_to_index(''.join(
                ch for ch in ref if ch.isalpha()))
            if not idx:
                idx = len(cells) + 1
            cells[idx] = _cell_text(c)
        raw_rows.append(cells)

    if not raw_rows:
        return [], []
    # 表头 = 第一行；行集 = 其后非全空行（与 `snapshot_asset_table` 同口径：
    # 连续 200 空行截断，这里救不到"空行"概念，就只去尾部全空行）
    header_cells = raw_rows[0]
    width = max(header_cells) if header_cells else 0
    headers = [str(header_cells.get(i, '') or '').strip()
               for i in range(1, width + 1)]
    if not any(headers):
        return [], []
    rows: list = []
    for cells in raw_rows[1:]:
        values = {}
        any_value = False
        for i, h in enumerate(headers, start=1):
            if not h:
                continue
            v = cells.get(i, '')
            values[h] = v
            if v not in ('', None):
                any_value = True
        if any_value:
            rows.append(values)
    return headers, rows


def plan_kept_rows(snap: 'AssetSnapshot | None', known_ids,
                   base_dir=None) -> tuple:
    """
    旧表里**这次扫描没产出**的行，按三条判据分拣 → `(要留下的, 要删掉的行数)`。

      · 没 ID、但有别的字 → 留（那是人写的一行备注 / 草稿）；
      · 有 ID、没记真实文件 → 留（人写的占位："这个立绘还没画"）；
      · 有 ID、记了文件、**文件还在** → 留（扫描没扫到它，不代表它不存在）；
      · 有 ID、记了文件、**文件真的不在了** → 删（表在跟随磁盘，这是它的职责）。

    第 4 条是唯一会删的分支，而且只删"证据确凿"的：路径解析得到、`is_file()`
    为假。`Path.exists()` 抛异常（权限 / 路径过长）一律当"还在"，宁可留一行
    也不删错。
    """
    if snap is None:
        return [], 0
    known = {str(x or '').strip() for x in (known_ids or ())}
    keep, dropped = [], 0
    base = Path(base_dir) if base_dir else None
    for row in snap.rows:
        aid = str(row.get(snap.id_header, '') or '').strip()
        if aid and aid in known:
            continue                            # 这次写过了
        path_text = str(row.get(snap.path_header, '') or '').strip()
        if not path_text:
            keep.append(row)                    # 没记文件：人写的，留着
            continue
        p = Path(path_text)
        if not p.is_absolute() and base is not None:
            p = base / p
        try:
            alive = p.is_file()
        except OSError:                         # 判不了就当还在（宁可多留）
            alive = True
        if alive:
            keep.append(row)
        else:
            dropped += 1
    return keep, dropped


def restore_asset_marks(ws, snap: 'AssetSnapshot | None', row_of: dict,
                        id_header: str = '编号') -> int:
    """按 ID 把底色 / 字色 / 批注铺回新表，返回铺了几格。"""
    if snap is None or not snap.marks or not row_of:
        return 0
    from openpyxl.styles import PatternFill
    header = [str(c.value or '').strip() for c in ws[1]]
    col_of = {name: i + 1 for i, name in enumerate(header) if name}
    applied = 0
    for aid, marks in snap.marks.items():
        row = row_of.get(str(aid))
        if not row:
            continue
        for name, mark in marks.items():
            col = col_of.get(name)
            if not col:
                continue
            cell = ws.cell(row=row, column=col)
            try:
                if mark.get('fill'):
                    cell.fill = PatternFill('solid', fgColor=mark['fill'])
                if mark.get('font') is not None:
                    cell.font = copy(mark['font'])
                if mark.get('comment') is not None:
                    cell.comment = copy(mark['comment'])
                applied += 1
            except Exception as e:             # noqa: BLE001
                swallow('story.xlsx_preserve.asset.mark', e)
    return applied


def restore_extra_sheets(wb, snap: 'AssetSnapshot | None') -> int:
    """把人的额外工作表带进新工作簿，返回带了几张。"""
    if snap is None or not snap.sheets:
        return 0
    n = 0
    for sheet in snap.sheets:
        try:
            ws = wb.create_sheet(sheet.name)
        except Exception as e:                 # noqa: BLE001
            swallow('story.xlsx_preserve.asset.sheet', e)
            continue
        for row, col, value, style, comment in sheet.cells:
            cell = ws.cell(row=row, column=col, value=value)
            if style is not None:
                cell._style = copy(style)
            if comment is not None:
                cell.comment = copy(comment)
        for letter, width in sheet.widths.items():
            try:
                ws.column_dimensions[letter].width = width
            except Exception as e:             # noqa: BLE001
                swallow('story.xlsx_preserve.asset.sheet_width', e)
        for rng in sheet.merged:
            try:
                ws.merge_cells(rng)
            except Exception as e:             # noqa: BLE001
                swallow('story.xlsx_preserve.asset.sheet_merge', e)
        if sheet.freeze:
            ws.freeze_panes = sheet.freeze
        n += 1
    return n


def stash_asset_stats(**stats) -> None:
    global _LAST_ASSET_STATS
    _LAST_ASSET_STATS = dict(stats)


# ----------------------------------------------------------------------
#  5. 写盘失败要说人话（Excel 开着是最常见原因）
# ----------------------------------------------------------------------

def save_workbook(wb, path) -> None:
    """
    `wb.save(path)`，失败时把"Excel 把这个文件锁着"翻成人话。

    为什么（2026-10-25）：资源表/剧本表都是**用户在 Excel 里开着**的文件。
    `wb.save()` 在那种情况下抛 `PermissionError: [Errno 13] Permission denied:
    '...xlsx'` —— 这句话对编剧没有任何信息量：他不知道是"文件被占用"、
    "没权限"、还是"工具坏了"。而且这条路走的是"扫描资源 / 生成演出表"，
    用户一天点十几次。

    只包装消息，**异常类型不变**（`PermissionError` 仍是 `PermissionError`），
    调用方既有的 `except OSError` 全部照旧有效。
    """
    try:
        wb.save(path)
    except OSError as exc:
        raise type(exc)(_save_hint(exc, path)) from exc


def _save_hint(exc, path) -> str:
    p = Path(path)
    base = f'写不进 {p.name}：'
    if isinstance(exc, PermissionError):
        return (base + '这个文件可能正被 Excel / WPS 打开着（也可能是只读或被别的'
                       '程序占用）。关掉那个窗口再试一次 —— 表还在，一个字没动。')
    text = str(exc)
    if 'No space' in text or 'space' in text.lower():
        return base + '磁盘满了。表还在，一个字没动。'
    if 'Read-only' in text or 'readonly' in text.lower():
        return base + '文件或目录是只读的。改成可写（或换一个位置）再试。'
    return f'{base}{text}（表还在，一个字没动）'
