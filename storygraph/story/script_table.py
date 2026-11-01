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
import re
from pathlib import Path

# 2026-11-01：`registry_from_parse_result` 已搬去 `assets/asset_registry`
# （它只造 `AssetRegistry`，不碰 story 层；`assets` 反过来 import 本模块
# 曾是 29 模块环上的一条反馈边）。这里保留旧名转发，调用方不用改。
from storygraph.assets.asset_registry import (  # noqa: F401
    registry_from_parse_result,
)
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
    CODE_BLOCK_BODY,
    CODE_BLOCK_OPEN,
    _AUDIO_PSEUDO_REVERSE,
    _HEADER_ALIASES,
    _NATIVE_TAIL_RE,
    _RENPY_STATEMENT_HEADS,
    _append_line,
    assign_identity,
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
    _split_code_lines,
    _split_hide_items,
    _split_lines,
    _split_show_items,
    _split_spec_list,
    compose_audio_cell,
    compose_audio_text,
    compose_code,
    compose_code_blocks_cell,
    compose_hide_cell,
    compose_scene_cell,
    compose_show_cell,
    compose_stage,
    compose_stage_extra,
    menu_caption_text,
    parse_code_blocks_cell,
    unmask_code_block_markers,
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
    labels_in_danger,
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


#: 门面的**公共面**（风险清单 P2-7）。
#:
#: 为什么必须显式写出来：这个模块为了当门面，从 `beat_model` /
#: `beat_render` 等处 re-export 了 **90+ 个下划线名**。没有 `__all__` 时，
#: "对外契约"就等于"这个文件恰好 import 了什么"——
#:
#:   · `from storygraph.story.script_table import *` 会把 92 个私有 helper
#:     一起拖出来（实测）；
#:   · IDE / 文档工具分不出"给你用的"和"实现细节"；
#:   · 加一个内部 helper 会**无声地**变成公共 API，没人会注意到。
#:
#: 私有 re-export **保留**（`beat_build` / `excel_export` 这些同层模块
#: 靠它拿到共享 helper，是刻意的单一来源），但它们从此**不在公共面上**。
#: 契约由 `tests/test_facade_surface.py` 守着。
__all__ = [
    # —— 数据模型 ——
    'Beat',
    'BEAT_FIELDS',
    'BEAT_HEADERS',
    'HIDDEN_BEAT_COLUMNS',
    'DROPDOWN_COLUMNS',
    'ACTION_COLUMNS',
    'ACTION_HINTS',
    'AUDIO_PSEUDO',
    'COMMON_TRANSITIONS',
    'LEGACY_AUDIO_COLUMNS',
    'LEGACY_STAGE_COLUMNS',
    'KIND_SAY',
    'KIND_NARRATION',
    'KIND_MENU',
    'KIND_MENU_OPTION',
    'KIND_EXTEND',
    # —— 表格 ↔ Beat ——
    'export_beats_to_excel',
    'import_beats_from_excel',
    'open_rpy_as_table',
    'beats_from_statements',
    'beats_from_parse_result',
    'registry_from_parse_result',
    'hydrate_from_dsl',
    'apply_script_definitions',
    'find_unregistered_assets',
    'refresh_candidate_sheets',
    # —— 渲染 ——
    'render_beats_to_rpy',
    'render_beat_to_rpy',
    'render_menu_block',
    'render_transform_definitions',
    'collect_used_transforms',
    'split_beat_units',
    'menu_caption_text',
    # —— 单元格级编码（结构化列 ↔ 文本）——
    'compose_show_cell',
    'compose_hide_cell',
    'compose_scene_cell',
    'compose_stage',
    'compose_stage_extra',
    'compose_code',
    'compose_code_blocks_cell',
    'compose_audio_cell',
    'compose_audio_text',
    'parse_code_blocks_cell',
    'unmask_code_block_markers',
    'CODE_BLOCK_OPEN',
    'CODE_BLOCK_BODY',
    'MENU_NO_CAPTION',
    # —— 生成门禁 ——
    'generation_guard',
    'evaluate_generation',
    'write_generation',
    'write_beats_to_rpy',
    'GeneratedArtifact',
    'GenerationBlocked',
    'GuardIssue',
    'planned_output_path',
    'inside_game_dir',
    'occupied_labels',
    'labels_in_danger',
    'thumb_cache_dir',
    'make_symbols',
]






# ===================== 一体化入口 =====================

def _require_script_file(script_file) -> Path:
    """
    校验"这确实是一份存在的 `.rpy` 文件"，不是就**抛**。

    为什么不返回 `None` / 空表（2026-11-01）：坏输入的三种错法各说各的，
    但都必须**出声**。从前这里把不存在的路径交给解析器，解析器返回空节点，
    于是产出一张只有表头的表 —— 调用方看到的是"成功"。审查报告把这一类
    叫"假绿"，它比报错更坏：人不会再查。

    `OSError` 家族（不存在 / 是目录）与 `ValueError`（后缀不对）分开，
    是为了让调用方能按"路径错了"和"类型错了"分别给建议。
    """
    p = Path(str(script_file))
    if not p.exists():
        raise FileNotFoundError(
            f'找不到剧本文件：{p}（这个路径不存在 —— 检查一下路径或拼写）')
    if p.is_dir():
        raise IsADirectoryError(
            f'{p} 是一个目录，不是剧本文件（要传 .rpy 文件的路径）')
    if p.suffix.lower() != '.rpy':
        raise ValueError(f'{p.name} 不是 .rpy 文件（这里只收 .rpy）')
    return p


def open_rpy_as_table(script_file, output_path=None, registry=None,
                      label: str = '', with_thumbnails: bool = False,
                      facts=None):
    """
    把一个 .rpy 模块解析成演出表：这是"用 Excel 打开 rpy"的正向路径。

    registry 为空时，用脚本自身解析出的角色/变换造一份最小注册表，
    保证表格仍有候选下拉（不然 Stage 的词表和 Speaker 下拉都是空的）。

    有符号表时会**重合成人话**：`show ling close_happy at left` 变成
    `[显示] 凌跃捷 开心 左`——但只在往返无损时才换（见 dsl 自检），
    猜不准就保留程序写法，绝不猜错一个表情。

    2026-11-01（外部审查 H1）：**解析出的错误不再被丢掉**。从前
    `open_rpy_as_table` 只返回路径，`ParseResult.errors` 谁也不看 ——
    于是"引号没闭合""缩进错了"都会安静地生成一张缺内容的表。
    现在把结果记进 `last_open_stats()`，CLI 据此给非 0 退出码。

    2026-11-01（地狱测试补）：**路径本身不合法时直接抛**，不再"安静地摊一张空表"。
    从前 `RenPyParser().parse(不存在的路径)` 返回空节点 → 一张只有表头的表 →
    调用方看到"成功"。CLI 那道闸（`_require_file`）只管 CLI；界面 / MCP /
    别的程序化调用一样会踩到。判据见 `tests/test_hell_guardrails.py`。
    """
    from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder

    script_file = _require_script_file(script_file)
    if output_path is None:
        output_path = script_file.with_suffix('.xlsx')

    nodes = RenPyParser().parse(script_file)
    result = ParseResultBuilder().build(nodes)
    _remember_open_stats(script_file, result)
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
    # 目标是**已存在的表**（"从 .rpy 重新摊表覆盖"）：先把存量身份接回来，
    # 否则每一行的 LineId 都会按位置重算（契约 I4，2026-10-23）。
    _reuse_existing_ids(beats, output_path)
    export_beats_to_excel(beats, output_path, registry=registry,
                          include_thumbnails=with_thumbnails, facts=facts,
                          # 表里记下来源：导回时自动走增量（审计 P1-1）
                          source_file=str(script_file))
    return output_path


#: 上一次 `open_rpy_as_table` 的解析统计（CLI / 体检拿它判断"这次真的读到东西了吗"）
_LAST_OPEN_STATS: dict = {}


def _remember_open_stats(script_file, result) -> None:
    global _LAST_OPEN_STATS
    _LAST_OPEN_STATS = {
        'source': str(script_file),
        'labels': len(getattr(result, 'labels', {}) or ()),
        'statements': len(getattr(result, 'statements', ()) or ()),
        'errors': [str(e) for e in (getattr(result, 'errors', ()) or ())],
        'warnings': [str(w) for w in (getattr(result, 'warnings', ()) or ())],
        'suspect_say': _suspect_unclosed_say(getattr(result, 'statements', ())),
    }


#: 「看起来是台词、但引号没闭合」的一行：`e "还没写完` / `"旁白没有收尾`
_UNCLOSED_SAY_RE = re.compile(r'^\s*(?:[A-Za-z_]\w*\s+)?"[^"]*$')


def _suspect_unclosed_say(statements) -> list:
    """
    找出**引号没闭合**的台词行：`[(行号, 原文), …]`。

    为什么要它（2026-11-01，外部审查 5.4）：这种行在解析器眼里是"工具没建模
    的写法"（`raw / unmodeled`），于是**安静地**变成一行原样文本 ——
    表里没有它作为台词的位置，而 CLI 照样报"已生成剧本表"。

    判据刻意窄（只认"还带着开头引号，却没有收尾引号"）：
    宁可漏报，也不要拿模糊的启发式去诬陷一行合法写法。
    真正的裁判仍是 Ren'Py 自己的 parser —— 这条只是让用户在
    **打开表格之前**就知道"这几行有问题"。
    """
    out = []
    for st in statements or ():
        if str(getattr(st, 'kind', '')) != 'raw':
            continue
        payload = getattr(st, 'payload', None) or {}
        text = str(payload.get('text') or '')
        if not _UNCLOSED_SAY_RE.match(text):
            continue
        src = getattr(st, 'source', ('', 0)) or ('', 0)
        out.append((int(src[1] or 0), text.strip()))
    return out


def last_open_stats() -> dict:
    """
    上一次 `open_rpy_as_table` 读到了什么（`errors` / `statements` / …）。

    为什么要有（2026-11-01，外部审查 H1）：解析器的错误从前**没有任何
    出口** —— 表照生成、CLI 照报"已生成"。CLI 拿这个字典决定退出码：
    有 `errors` → 1，一条语句都没读出来 → 1。
    """
    return dict(_LAST_OPEN_STATS)


def _reuse_existing_ids(beats, table_path) -> int:
    """
    "重新摊表覆盖已有表"时，把**能对上的行**的 LineId 接回来。返回接回几条。

    为什么要它（契约 I4）：`open_rpy_as_table` 覆盖写同一个 `.xlsx` 时，
    新拍的 `line_id` 是按解析位置新发的 —— 用户"表旧了，从 .rpy 重摊一次"
    之后，TTS 语音 / 翻译 / 协作评论指向的那批 ID 全部换人。

    匹配是**两轮**（刻意保守，宁可发新号也不乱配）：
      ① 内容完全相同（label / kind / speaker / 文本 / 条件 / 舞台 / 原文）
         → 复用它的 ID；同键多条时按出现顺序一条一条配（多重集）；
      ② 剩下的行**数量相等**、且旧表也只剩同样多 → 按相对顺序配。
         这档覆盖"只改了文案、结构没动"的重摊；数量不等就整批发新号
         （插删过的表不猜，猜错比不发号更糟）。
    """
    from pathlib import Path

    path = Path(table_path)
    if not path.is_file() or not beats:
        return 0
    try:
        old = [b for b in import_beats_from_excel(path) if str(b.line_id or '')]
    except Exception as e:                   # noqa: BLE001 - 旧表坏了就发新号
        from storygraph.core.safeguard import swallow
        swallow('story.script_table.reuse_existing_ids', e)
        return 0
    if not old:
        return 0

    def key(b):
        return (str(getattr(b, 'label', '') or ''), str(getattr(b, 'kind', '') or ''),
                str(getattr(b, 'speaker', '') or ''), str(getattr(b, 'text', '') or ''),
                str(getattr(b, 'condition', '') or ''), str(getattr(b, 'stage', '') or ''),
                str(getattr(b, 'raw', '') or ''))

    pool: dict = {}
    for row in old:
        pool.setdefault(key(row), []).append(str(row.line_id))
    reused = 0
    leftovers = []
    for b in beats:
        bucket = pool.get(key(b))
        if bucket:
            b.line_id = bucket.pop(0)
            reused += 1
        else:
            leftovers.append(b)
    rest = [lid for bucket in pool.values() for lid in bucket]
    if leftovers and len(leftovers) == len(rest):
        for b, lid in zip(leftovers, rest):
            b.line_id = lid
            reused += 1
    return reused



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
