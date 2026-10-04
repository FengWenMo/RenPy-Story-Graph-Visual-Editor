"""
storygraph.story.excel_import
=============================
**Excel → Beat**：读演出表、并以真实资源表为准找出"引用了但没登记"的资产。

`import_beats_from_excel` 读完后**一律** `hydrate_from_dsl`（见 beat_build），
否则下游看到的是空舞台。老表（26 列时代）在这里自动迁移到当前列。
"""

from __future__ import annotations
from pathlib import Path
import openpyxl
from storygraph.core.vocabulary import RENPY_BUILTIN_TRANSFORMS

from storygraph.story.beat_build import hydrate_from_dsl
from storygraph.story.beat_model import (
    assign_identity,
    _split_hide_items,
    _split_show_items,
)
from storygraph.story.beat_render import _normalize_image_name, make_symbols
from storygraph.story.excel_export import _read_beats_from_ws



# ===================== Excel -> Beat =====================

def import_beats_from_excel(path, registry=None) -> list:
    """
    读回 Beats 表。空行跳过（Excel 里常见末尾空行）。

    **一律**把 Stage / BGM / SFX 的 DSL 文本解释成结构化字段：舞台快照、
    校验、多引擎导出都要"能计算"的形态。没有资源表也能解释（只是没法
    把 `[显示] 凌跃捷 开心 左` 翻成 `show ling close_happy`，那种情况会
    原样留下）。单元格原文本身永远不丢。
    """
    path = Path(path)
    if not path.exists():
        return []
    # **`data_only=False`**（2026-10-05 修）：以 `=` 开头的**用户文本**会被
    # openpyxl / Excel 当成公式（`data_type='f'`），而 `data_only=True` 读到的
    # 是**缓存的计算结果** —— 这张表从来没被 Excel 打开算过，缓存是空的，
    # 于是那一格读回来是 `None`，用户的文本**静默消失**
    # （brutal test 的 F3「公式前缀」就是这个）。
    #
    # 我们**不打算读任何真实公式的值**：表里唯一的公式是下拉框的
    # defined name（`_dynamic_range_formula`），它不属于单元格值。
    # 所以这里要的是"格子里的原始字符串"，正是 `data_only=False` 的语义。
    wb = openpyxl.load_workbook(path, data_only=False)
    try:
        ws = wb['Beats'] if 'Beats' in wb.sheetnames else wb.active
        beats = _read_beats_from_ws(ws)
        # 文件级保真块（顶层 `define`/`screen`/`init python`/…）：在**隐藏表**里。
        # 没有那张表时退回老版本的隐藏列（见 `_read_file_blocks`）。
        blocks_text = _read_file_blocks(wb, ws)
        if blocks_text:
            from storygraph.story.beat_build import attach_file_block_text
            beats = attach_file_block_text(beats, blocks_text)
        # 手工新加的行没有 LineId/ContentHash → 这里补上，
        # 让"Excel 前端 + 数据库后端"拿到的主键不空（八）
        assign_identity(beats)
        hydrate_from_dsl(beats, make_symbols(registry))
        return beats
    finally:
        wb.close()


def _read_file_blocks(wb, ws) -> str:
    """
    文件级保真块：**优先读隐藏表**，读不到再退回老版本的隐藏列。

    为什么要退回那一条（2026-10-05）：第一版把它们塞在隐藏列 `FileBlocks` 里，
    而 Excel 单元格上限 32767 字符 —— 真实文件的顶层区间直接超限、被静默截断。
    今天写出去的表已经没有那一列了（改走隐藏表），但**那一版生成的表**还可能
    在用户手上，所以这里保留只读兜底；写侧不再产出它。
    """
    from storygraph.story.beat_model import (FILE_BLOCK_SHEET,
                                            file_blocks_from_rows)

    if FILE_BLOCK_SHEET in getattr(wb, 'sheetnames', ()):
        sheet = wb[FILE_BLOCK_SHEET]
        rows = []
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if not row or row[0] is None:
                continue
            rows.append((row[0], row[1] if len(row) > 1 else ''))
        text = file_blocks_from_rows(rows)
        if text.strip():
            return text

    # —— 老表兜底：隐藏列里那一整段（可能已被截断，但总比没有强）——
    from storygraph.core.safeguard import swallow
    from storygraph.story.beat_model import LEGACY_FILE_BLOCK_COLUMN
    try:
        headers = [c.value for c in ws[1]]
    except Exception as e:                       # noqa: BLE001
        swallow('excel_import._read_file_blocks.header', e)
        return ''
    if LEGACY_FILE_BLOCK_COLUMN not in headers:
        return ''
    col = headers.index(LEGACY_FILE_BLOCK_COLUMN) + 1
    for r in range(2, ws.max_row + 1):
        v = ws.cell(row=r, column=col).value
        if v and str(v).strip():
            return str(v)
    return ''



def find_unregistered_assets(beats: list, registry) -> list:
    """
    返回演出表中被引用、但资源表里没有的资产清单：[(asset_type, id), ...]。

    用于在"从剧本表生成 .rpy"时提示文案：这些资产还没在资源表登记，
    导出后脚本里的引用会指向不存在的 ID（断裂引用）。

    **判定规则只有一条**：`asset_registry.resolve_references`。
    以前这里自带一套"空格↔下划线归一 + 内置白名单"的规则，和
    asset_registry / asset_extensions 里的两套各自漂移，同一个引用
    在不同入口得出不同结论。现在统一走同一个解析器（一.3）。
    """
    if registry is None:
        return []
    from storygraph.assets.asset_registry import resolve_references

    found = []
    seen = set()

    def check(asset_type, name):
        name = (name or '').strip()
        if not name:
            return
        # Ren'Py 内置变换/转场（left / dissolve / …）不是"未登记的资产"，
        # 只是解析层对名字要放行。
        if asset_type == 'transform' and name in RENPY_BUILTIN_TRANSFORMS:
            return
        key = (asset_type, name)
        if key in seen:
            return
        seen.add(key)
        if not resolve_references(name, registry):
            found.append((asset_type, name))

    for beat in beats:
        if beat.speaker:
            check('character', beat.speaker)
        if beat.scene:
            check('image', _normalize_image_name(beat.scene))
        for item in _split_show_items(beat.show):
            if item['image']:
                check('image', _normalize_image_name(item['image']))
        for item in _split_hide_items(beat.hide):
            if item['image']:
                check('image', _normalize_image_name(item['image']))
        for t in (beat.at or '').split():
            check('transform', t)
        check('transform', beat.transform)
        check('transform', beat.with_)
        check('music', beat.music)
        check('sound', beat.sound)
        check('voice', beat.voice)
    return found
