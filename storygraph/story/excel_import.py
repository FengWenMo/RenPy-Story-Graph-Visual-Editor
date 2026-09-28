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
from storygraph.assets.asset_registry import RENPY_BUILTIN_TRANSFORMS

from storygraph.story.beat_build import hydrate_from_dsl
from storygraph.story.beat_model import (
    _assign_identity,
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
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        ws = wb['Beats'] if 'Beats' in wb.sheetnames else wb.active
        beats = _read_beats_from_ws(ws)
        # 手工新加的行没有 LineId/ContentHash → 这里补上，
        # 让"Excel 前端 + 数据库后端"拿到的主键不空（八）
        _assign_identity(beats)
        hydrate_from_dsl(beats, make_symbols(registry))
        return beats
    finally:
        wb.close()



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
