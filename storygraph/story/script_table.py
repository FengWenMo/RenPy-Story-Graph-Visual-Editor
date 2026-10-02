"""
storygraph.story.script_table —— 剧本演出表（**兼容门面**）
=========================================================
2026-09-28 起本模块**只做转发**，实现分在：

    beat_model        Beat 数据结构 + 单元格编解码原语
    beat_build        .rpy / DSL → Beat
    beat_render       Beat → Ren'Py 代码
    excel_export      Beat → Excel（主表 + 派生视图 + 图库）
    excel_import      Excel → Beat
    generation_guard  生成安全门禁 + 落盘
    paths             项目路径判定（门禁与缩略图共用）

为什么这么拆：它曾经是一个 **3.8k 行的上帝模块**（模型 + Excel IO + 渲染 +
门禁 + 图库混在一起），也是"模块间互相 import 成环"的汇聚点——`story` 与
`pipeline` 只能靠函数内延迟 import 压着。拆开后下游可以直接 import 具体
模块，环消失；本门面保留全部旧名字，**既有 import 一行都不用改**。

新增代码请直接 import 上面的具体模块，不要往这里加实现。
"""

from __future__ import annotations
from pathlib import Path

from storygraph.story.beat_build import (  # noqa: F401
    _absorb_native,
    _category_from_tag,
    _hydrate_audio,
    _hydrate_stage,
    _image_id_from_name,
    _source_note,
    _split_image_name,
    apply_script_definitions,
    beats_from_parse_result,
    beats_from_statements,
    hydrate_from_dsl,
)
from storygraph.story.beat_model import (  # noqa: F401
    ACTION_COLUMNS,
    ACTION_HINTS,
    AUDIO_PSEUDO,
    BEAT_FIELDS,
    BEAT_HEADERS,
    Beat,
    DROPDOWN_COLUMNS,
    HIDDEN_BEAT_COLUMNS,
    KIND_EXTEND,
    KIND_MENU,
    KIND_MENU_OPTION,
    KIND_NARRATION,
    KIND_SAY,
    LEGACY_AUDIO_COLUMNS,
    LEGACY_STAGE_COLUMNS,
    MENU_NO_CAPTION,
    _AUDIO_PSEUDO_REVERSE,    _HEADER_ALIASES,
    _NATIVE_TAIL_RE,
    _RENPY_STATEMENT_HEADS,
    _SHOW_ARG_KEYWORDS,
    _append_line,
    _assign_identity,
    _audio_params,
    _beat_content_hash,
    _clause_image,
    _fill_audio_from_cells,
    _fill_audio_legacy,
    _fmt_seconds,
    _is_bare_renpy_verb,
    _join_hide_items,
    _join_show_items,
    _looks_like_renpy_statement,
    _ordered_code_lines,
    _parse_native_stage_tail,
    _show_clause_args,
    _show_item_text,
    _spec_at,
    _spec_image,
    _split_code_cell,
    _split_hide_items,
    _split_lines,
    _split_show_items,
    _split_spec_list,
    compose_audio_cell,
    compose_audio_text,
    compose_code,
    compose_hide_cell,
    compose_scene_cell,
    compose_show_cell,
    compose_stage,
    compose_stage_extra,
    menu_caption_text,
)
from storygraph.story.beat_render import (  # noqa: F401
    _audio_ref,
    _emit_image_name,
    _needs_quote,
    _normalize_image_name,
    _quote,
    _render_audio_from_fields,
    _render_code_block,
    _render_stage_from_fields,
    _resolve_audio_ref,
    _resolver_for,
    collect_used_transforms,
    make_symbols,
    render_beat_to_rpy,
    render_beats_to_rpy,
    render_menu_block,
    render_transform_definitions,
    split_beat_units,
)
from storygraph.story.excel_export import (  # noqa: F401
    COMMON_TRANSITIONS,
    _CANDIDATE_NOTE,
    _CHANGED_FILL,
    _DEFINED_NAME_PREFIX,
    _GALLERY_HEADERS,
    _GALLERY_MAX_THUMBS,
    _GALLERY_ROW_PX,
    _GALLERY_SECTIONS,
    _GALLERY_THUMB_PX,
    _GALLERY_TITLE,
    _HEADER_FILL,
    _HEADER_FONT,
    _INHERITED_FONT,
    _NAME_BY_TYPE,
    _SECTION_FILL,
    _SECTION_FONT,
    _SYSTEM_NAME_RE,
    _THUMB_EXTS,
    _add_dynamic_names,
    _attach_dropdowns,
    _autosize_beat_rows,
    _defined_names_for,
    _dynamic_range_formula,
    _embed_gallery_thumbnail,
    _hide_columns,
    _looks_like_system_name,
    _read_beats_from_ws,
    _resolve_thumb_source,
    _resolver_available,
    _sheet_name_of,
    _snapshot_row_height,
    _thumbnail_bytes,
    _write_asset_gallery,
    _write_candidate_sheets,
    _write_help_sheet,
    _write_keyword_sheet,
    _write_stage_snapshot_sheet,
    _write_usage_sheet,
    export_beats_to_excel,
    refresh_candidate_sheets,
    thumb_cache_dir,
)
from storygraph.story.excel_import import (  # noqa: F401
    find_unregistered_assets,
    import_beats_from_excel,
)
from storygraph.story.generation_guard import (  # noqa: F401
    GeneratedArtifact,
    GenerationBlocked,
    GuardIssue,
    _ASCII_IDENT_RE,
    _EXPR_CHARS,
    _ImageRef,
    _STAGE_VERB_RE,
    _TRAILING_PUNCT,
    _declared_labels,
    _image_refs,
    _is_valid_ident,
    evaluate_generation,
    generation_guard,
    occupied_labels,
    planned_output_path,
    write_beats_to_rpy,
    write_generation,
)
from storygraph.story.paths import (  # noqa: F401
    _guess_project_root,
    inside_game_dir,
)

from storygraph.excel.cells import (  # noqa: F401
    append_text_row as _append_text_row,
    set_text as _set_text,
)



# 写单元格的安全封装：唯一实现在 `storygraph.excel.cells`，
# 本模块顶部已以 `_set_text` / `_append_text_row` 别名导入（见 re-export 区）。



def registry_from_parse_result(parse_result, base=None):
    """
    从脚本自身的解析结果造一份最小注册表（角色 + 自定义 transform）。

    用途：用户还没「打开项目」（没有资源表）时，"用 Excel 打开此章节"
    依然要能把脚本里已定义的 transform / 角色做成下拉候选——否则
    `At` 列只能手打，正是用户抱怨的"变换选不到"。
    """
    from storygraph.assets.asset_registry import AssetRegistry

    reg = base if base is not None else AssetRegistry()
    for var, ch in (getattr(parse_result, 'characters', {}) or {}).items():
        if var in reg.characters:
            continue
        reg.characters[var] = {
            'ID': var,
            'Name': getattr(ch, 'display_name', '') or var,
            'Color': getattr(ch, 'color', ''),
            'WhatColor': getattr(ch, 'what_color', ''),
            'ImageTag': getattr(ch, 'image_tag', ''),
            'Kind': getattr(ch, 'kind', ''),
            'VoiceTag': getattr(ch, 'voice_tag', ''),
            'Notes': f"{getattr(ch, 'source_file', '')}:"
                     f"{getattr(ch, 'source_line', 0)}",
            '_dialogue_count': getattr(ch, 'dialogue_count', 0),
        }
    for name, tf in (getattr(parse_result, 'transforms', {}) or {}).items():
        if name in reg.transforms:
            continue
        reg.transforms[name] = {
            'ID': name,
            'Path': '',
            'ATL': getattr(tf, 'body', ''),
            'Params': getattr(tf, 'params', ''),
            'Doc': getattr(tf, 'doc', ''),
            'Category': 'custom',
            'Notes': f"{getattr(tf, 'source_file', '')}:"
                     f"{getattr(tf, 'source_line', 0)}",
        }
    return reg



# ===================== 一体化入口 =====================

def open_rpy_as_table(script_file, output_path=None, registry=None,
                      label: str = '', with_thumbnails: bool = False):
    """
    把一个 .rpy 模块解析成演出表：这是"用 Excel 打开 rpy"的正向路径。

    registry 为空时，用脚本自身解析出的角色/变换造一份最小注册表，
    保证表格仍有候选下拉（不然 Stage 的词表和 Speaker 下拉都是空的）。

    有符号表时会**重合成人话**：`show ling close_happy at left` 变成
    `[显示] 凌跃捷 开心 左`——但只在往返无损时才换（见 dsl 自检），
    猜不准就保留程序写法，绝不猜错一个表情。
    """
    from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder

    script_file = Path(script_file)
    if output_path is None:
        output_path = script_file.with_suffix('.xlsx')

    nodes = RenPyParser().parse(script_file)
    result = ParseResultBuilder().build(nodes)
    if registry is None:
        registry = registry_from_parse_result(result)
    symbols = make_symbols(registry)
    beats = beats_from_parse_result(result, label=label or script_file.stem)
    for b in beats:
        # 结构化列（背景/立绘/位置/转场/退场）承载主要舞台指令，「舞台（高级）」
        # 列只放结构化列表达不了的部分；和 beats_from_statements / to_row 一致，
        # 否则同一句 show 会在结构化列与 Stage 列各输出一次。
        b.stage = compose_stage_extra(b, symbols) or b.stage
        b.bgm = b.bgm or compose_audio_text(b, 'music')
        b.sfx = b.sfx or compose_audio_text(b, 'sound')
    export_beats_to_excel(beats, output_path, registry=registry,
                          include_thumbnails=with_thumbnails)
    return output_path



def __getattr__(name):
    """
    高层编排入口的**按需转发**（PEP 562）。

    `export_table_to_rpy` 本质是编译流水线的第一层封装，实现已移到
    `storygraph.pipeline.compile`：留在本模块会让 **story 层反向依赖
    pipeline 层**（只好用函数内 import 压着）。按需转发既保住
    `from script_table import export_table_to_rpy` 的老写法，
    又让本模块顶层不引入 pipeline。
    """
    if name == 'export_table_to_rpy':
        from storygraph.pipeline.compile import export_table_to_rpy
        return export_table_to_rpy
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
