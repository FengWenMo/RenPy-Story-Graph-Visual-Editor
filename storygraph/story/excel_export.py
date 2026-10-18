"""
storygraph.story.excel_export
=============================
**Beat → Excel**：演出表本体 + 四张派生视图 + 资源图库（含缩略图）。

  · `export_beats_to_excel`：写出 Beats 主表（20 列结构化 + 2 列隐藏顺序）
    与「舞台快照」「演出时间轴」「列说明」「资源图库」
  · `refresh_candidate_sheets`：只重建候选表与下拉（保留用户数据）
  · 缩略图：Pillow 可选，走磁盘缓存（键 = 路径|mtime|size|尺寸）并对总量封顶

派生视图一律**只读**（改了没用，重算即覆盖）——别再往 Beats 里加派生列。
"""

from __future__ import annotations
from storygraph.core.safeguard import swallow
import re
from io import BytesIO
from pathlib import Path
import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter
from storygraph.core.vocabulary import (RENPY_BUILTIN_TRANSFORMS,
                                        classify_show_words)

from storygraph.story.beat_build import hydrate_from_dsl
from storygraph.story.beat_model import (
    ACTION_COLUMNS,
    ACTION_HINTS,
    AUDIO_PSEUDO,
    BEAT_FIELDS,
    BEAT_HEADERS,
    Beat,
    DROPDOWN_COLUMNS,
    HIDDEN_BEAT_COLUMNS,
    assign_identity,
)
from storygraph.story.beat_render import make_symbols
from storygraph.story.paths import _guess_project_root
from storygraph.excel.cells import set_text as _set_text
from storygraph.excel.cells import append_text_row as _append_text_row



# ===================== Beat -> Excel =====================

_HEADER_FILL = PatternFill('solid', fgColor='DDEBF7')

_HEADER_FONT = Font(bold=True)



def _hide_columns(ws, names) -> None:
    """
    按列名把整列隐藏（工具维护的内部元数据用）。

    隐藏 ≠ 不写：列还在、值还在、`from_row` 照旧读得到，
    只是策划展开工作表时看不见它，也就不会去填。
    """
    for name in names or ():
        if name not in BEAT_FIELDS:
            continue
        letter = get_column_letter(BEAT_FIELDS.index(name) + 1)
        ws.column_dimensions[letter].hidden = True



def _resolve_statements(statements, output_path):
    """
    拿项目声明的语句注册表：显式给的优先，否则从产物路径**反推项目根**。

    反推不出来（临时目录、非项目路径）就返回 `None` —— 少一条提示，
    好过写错一处候选。
    """
    if statements is not None:
        return statements
    try:
        from storygraph.story.statement_registry import (
            StatementRegistry, root_of)
        _root = root_of(output_path)
        return StatementRegistry.for_project(_root) if _root else None
    except Exception as e:                      # noqa: BLE001
        from storygraph.core.safeguard import swallow
        swallow('story.excel_export.spec_annotations', e)
        return None


def _annotate_spec_heads(ws, beats, statements) -> int:
    """
    给「自定义语句·头行」格子挂**候选注释**（D1，2026-10-07）。

    为什么是**注释**而不是下拉：Excel 的数据验证是**按列**绑定的，而这些
    参数名是**每条语句各不相同**的（`mycue` 有 `intensity`，`battle` 有
    `difficulty`）—— 一列里装不下两套枚举。
    做"每参数一列"就得让 `BEAT_FIELDS` 动态化，那是一次列契约变更，
    不该混在这一次里（见文档 §5.4 的说明）。注释是当下**真能落地**的那一步：
    鼠标一悬停就看到能填什么，而且不影响任何一格的数据。

    ⚠ 2026-10-07 第二轮：「每参数一列 + 原生下拉」已经做了（见
    `_write_spec_arg_columns`），所以这一格注释现在的职责只剩**兜底** ——
    候选太长（Excel 内联下拉 255 字符上限）、或者这条声明只有头行没有
    参数形状时，人还能从这里读到"能填什么"。

    返回挂了注释的格子数（可测）。
    """
    if not statements:
        return 0
    try:
        col = BEAT_FIELDS.index('SpecHead') + 1
    except ValueError:                          # pragma: no cover - 列被删了
        return 0
    from openpyxl.comments import Comment
    from storygraph.story.statement_registry import spec_columns
    n = 0
    for i, beat in enumerate(beats or (), start=2):
        name = str(getattr(beat, 'spec_name', '') or '')
        if not name:
            continue
        cols = spec_columns(statements, name)
        if not cols:
            continue
        spec = statements.match(name)
        title = f'这条语句由项目声明过：{name}'
        if spec is not None and getattr(spec, 'label', ''):
            title += f'（{spec.label}）'
        lines = [title]
        for c in cols:
            piece = f'  {c["name"]}'
            if c['label']:
                piece += f'（{c["label"]}）'
            if c['choices']:
                piece += '：' + ' / '.join(c['choices'])
            if c['required']:
                piece += '  [必需]'
            lines.append(piece)
        lines.append('改这一格 = 改这条语句的参数；不改则逐字节原样导出。')
        lines.append('（也可以改它自己那一列：上面那几列是一参数一列，'
                     '带原生下拉。）')
        cell = ws.cell(row=i, column=col)
        cm = Comment('\n'.join(lines), 'storygraph')
        cm.width = 320
        cm.height = 150
        cell.comment = cm
        n += 1
    return n


# ⚠ 2026-10-16：`_SHOW_KEYWORDS` 那张表**已删**。
#   它从前是"`_unknown_show_words` 自己拿一个集合去 split()"的产物；
#   现在切分与"哪个词是什么角色"全在 `core.vocabulary.classify_show_words`
#   一处。历史教训（保留在案，免得有人又想加回来一张表）：
#     · 它曾经抄着一份规范集、**漏了 `onlayer`**；
#     · 又曾经多带一个 `on`（官方 show 语法里根本没有、真机 0 次），
#       那是 `62dfaa2`"E2 第三切片"顺手带的、无任何依据 ——
#       留一个无依据的成员正是这批词表各自漂移的成因。


def _unknown_show_words(show_text, facts) -> list:
    """
    一格 `show` 文本 → `[(tag, 属性), …]`：其中**引擎没认**的那些。

    ⚠ 解析就地写、只依赖 `core` —— `story` 层**不能** import `app`
    （契约 H1 的分层方向；`app/beats.py` 里有等价实现，那是给卡片用的）。

    ⚠ 碰到 `at` / `with` / `as` / `behind` / `onlayer` / `zorder` 就**停**：
    它们后面是变换/图层，不是属性（`show cg at center` 的 `at` 不是属性）。

    ⚠⚠ 2026-10-16（用户排期 C 项）：这里从前自己写了一遍"碰到关键字就 break"
    的循环 —— 现在**切分本体交给 `core.vocabulary.classify_show_words`**，
    调用方只声明"引入型目标（`show screen X` / `show layer Y` / `show expression …`）
    **没有立绘属性**可言，直接跳过"。
    """
    from storygraph.core import engine_facts as EF

    out: list = []
    from storygraph.core.vocabulary import split_stage_items
    for item in split_stage_items(str(show_text or '')):
        roles = classify_show_words(item)
        if roles.intro_kind != 'image':
            # 非图像目标：没有"立绘属性"这件事（真机 112 行 `show screen …`）
            continue
        toks: list = []
        for word in roles.target.split():
            # `close_happy@i11` 里的 `@` 后面是**位置变换**，也不算属性
            word = word.split('@', 1)[0]
            if word:
                toks.append(word)
        if len(toks) < 2:
            continue
        tag, attrs = toks[0], toks[1:]
        for attr in EF.unrecognized_attrs(facts, tag, attrs):
            if (tag, attr) not in out:
                out.append((tag, attr))
    return out


def _annotate_unknown_attrs(ws, beats, facts) -> int:
    """
    给「立绘」格挂**批注**：这一格里哪些属性**引擎没认**。

    为什么是"批注"，而不是"过滤掉"或"另开一列"（2026-10-11 定的口径）：
      · **候选以实际存在的资源为准** —— 资源表里有的、引擎不认的
        （掩码 / 切图这类内部件）**照样可选、照样能导入**。
        拿引擎的枚举去删候选，会把"合法的、只是引擎还没认的"一起删掉，
        那正是"东西被错误过滤、无法导入"的来源；
      · 但人要**知道**选了它会怎样（引擎会报
        `'lan lan_angry' is not an image`）；
      · 批注**不占列、不改任何一格的数据**，鼠标一悬停就看到 ——
        与 `_annotate_spec_heads` 同一套做法（那条已经在用了）；
      · **资源表一行都不动**：那是人工维护的资源登记，
        机器不往里写"语义判断"（用户明确要求）。
    ⚠ 事实不可用（没采过 / 采失败 / `is_collectable` 为假）→ **一个批注都不挂**。

    返回挂了几格（可测）。
    """
    if not facts:
        return 0
    try:
        col = BEAT_FIELDS.index('Show') + 1
    except ValueError:                          # pragma: no cover - 列被删了
        return 0
    from openpyxl.comments import Comment

    n = 0
    for i, beat in enumerate(beats or (), start=2):
        cell = ws.cell(row=i, column=col)
        # ⚠ 从**这一格自己的内容**解析（不是从 `beat.show` 模型值）：
        # 这一格可能是空的（那些项走别的列），拿模型值去挂批注就会
        # "格子是空的、批注却在说别的东西"（实测踩到）。
        hits = _unknown_show_words(cell.value, facts)
        if not hits:
            continue
        lines = ['这一格里有引擎**没认**的属性：']
        lines += [f'  {tag} {attr}' for tag, attr in hits[:8]]
        if len(hits) > 8:
            lines.append(f'  …另有 {len(hits) - 8} 项')
        lines.append('可能是"文件在、但不是可选属性"（掩码 / 切图这类内部件）。')
        lines.append('⚠ 只是提示：候选与导入**仍以实际存在的资源为准**，不拦你。')
        lines.append(f'引擎会报 `\'{hits[0][0]} {hits[0][1]}\' is not an image`。')
        cm = Comment('\n'.join(lines), 'storygraph')
        cm.width = 340
        cm.height = 130
        cell.comment = cm
        n += 1
    return n


#: Excel 数据验证里内联候选串的长度上限。
#:
#: 超了 Excel 会**直接拒绝**这个验证规则（表还是能存，但下拉没了、
#: 或者更糟：整个文件的验证被 Excel 判为损坏）。官方口径是公式 255 字符，
#: 这里留一点余量，超了就不挂下拉、退回注释（"少一个便利，不要坏一个文件"）。
_MAX_INLINE_CHOICES = 240


def _spec_choices_formula(choices) -> str:
    """候选 → Excel 内联下拉的公式串（`"a,b,c"`）；放不下返回空串。"""
    vals = [str(c).strip() for c in (choices or ()) if str(c).strip()]
    if not vals:
        return ''
    # 候选里出现逗号/引号就没法内联表达（Excel 会误解），那种一律走注释
    if any((',' in v) or ('"' in v) for v in vals):
        return ''
    formula = '"' + ','.join(vals) + '"'
    return formula if len(formula) <= _MAX_INLINE_CHOICES else ''


#: 动态参数列的**唯一取法**（`spec_cols` 的算法在这里，写表头、定列宽、
#: 挂验证三处都用它 —— 三份各算一次迟早不一致）。
def dynamic_cols(beats, statements=None) -> list:
    """
    这批拍需要的「每参数一列」列描述（`statement_registry` 的实现）。

    `statements` 可以为 `None`：`with` / `pause` / `window` / `camera` 这类
    **标准语句的形状由工具自己声明**，没有 `rules.json` 也该有那一列。
    """
    try:
        from storygraph.story.statement_registry import dynamic_spec_columns
        return dynamic_spec_columns(beats, statements)
    except Exception as e:                      # noqa: BLE001
        from storygraph.core.safeguard import swallow
        swallow('story.excel_export.spec_columns', e)
        return []


def _write_spec_arg_columns(ws, beats, statements, row_count: int,
                            ranges: dict = None, names: dict = None) -> list:
    """
    「**每参数一列**」（M4 第二阶段）：把语句参数摊成列 + 原生下拉。

    返回写出去的列描述（`statement_registry.dynamic_spec_columns` 的形态）。

    为什么这次敢动列契约（上一次评审把它推后了）：`BEAT_FIELDS` 是
    **基础列**，动态列**追加在末尾**、且只在"这一章真的用了某条语句"
    （声明过的，或 `with`/`pause`/`window`/`camera` 这类**已识别**的）时出现
    —— 于是

      · 没有这些语句的章节：表与从前**一模一样**（列数都不变）；
      · `to_row` / `from_row` 的默认行为不变（老调用方一个都不用改）；
      · 值**折回语句本身**（头行 / 「代码」列那一行），不新增第二份数据。

    下拉是"方便"不是"牢笼"：`showErrorMessage=False`（与本项目其他下拉
    一致），手打一个没声明的值照样能过 —— 候选是常见取值，不是白名单。
    """
    cols = dynamic_cols(beats, statements)
    if not cols:
        return []
    names = names or {}
    ranges = ranges or {}
    from openpyxl.worksheet.datavalidation import DataValidation
    for idx, col in enumerate(cols):
        letter = get_column_letter(len(BEAT_FIELDS) + idx + 1)
        # ① **候选来自资源表**（`choices_from`）：直接绑到那张候选表的动态名称上
        #    —— 自伸缩、没有 255 字符上限，项目加了一个变换这里立刻就有。
        #    这一档优先于内联候选（`with` 的转场就是这样）。
        source = str(col.get('choices_from') or '')
        formula = names.get(source) if source else None
        if not formula and source and source in ranges:
            formula = ranges[source]
        # ② 内联候选（`window` 的 show/hide/auto 这种固定取值）
        if not formula:
            formula = _spec_choices_formula(col['choices'])
        if formula:
            dv = DataValidation(type='list', formula1=formula,
                                allow_blank=True, showDropDown=False)
            dv.showErrorMessage = False      # 手打新值不该被拦住
            dv.promptTitle = col['header']
            hint = (col['label'] or col['param'])
            if col['required']:
                hint += '（必需）'
            dv.prompt = f'{hint} —— 从下拉里选，或直接写一个项目里在用的值'
            ws.add_data_validation(dv)
            dv.add(f'{letter}2:{letter}{max(row_count + 1, 200)}')
            continue
        # **兜底**：候选太长 / 带逗号（内联表达不了）→ 挂注释，别让
        # "能填什么"无处可查（也不许为了便利去写一条 Excel 会拒收的规则）。
        if not col['choices']:
            continue
        from openpyxl.comments import Comment
        note = [f'{col["header"]}（{col["label"] or col["param"]}）的候选：']
        note.extend(f'  · {c}' for c in col['choices'])
        note.append('候选是常见取值、不是白名单：要写别的值直接写。')
        for r in range(2, max(row_count + 1, 2) + 1):
            cm = Comment('\n'.join(note), 'storygraph')
            cm.width = 300
            cm.height = 160
            ws.cell(row=r, column=len(BEAT_FIELDS) + idx + 1).comment = cm
    return cols


def export_beats_to_excel(beats: list, output_path,
                          registry=None, include_dropdowns: bool = True,
                          include_thumbnails: bool = False,
                          statements=None, facts=None, source_file=None):
    """
    把 Beat 列表写成 Excel。

    registry 非空时会额外生成 角色/图片/变换/音乐/音效/语音 六张候选表、
    一张「资源图库」，并给主表相应列挂上下拉验证——文案只需要点选，
    不会打错 ID。

    include_thumbnails：图库里的缩略图（给策划"看图选 ID"用）。
    **默认关**——首次导出要为每张图解码缩放（真实项目冷缓存实测 ~90s），
    CI / CLI 不该为此变慢。编辑器走后台线程并默认打开。

    facts：**引擎事实**（`core.engine_facts` 那份）。给了就在「立绘」格上挂
    批注，指出"这一格里有引擎没认的属性"。
    ⚠ **只提示，绝不过滤**：候选与下拉内容一律以**实际存在的资源**为准
    （用户 2026-10-11 定的口径）；`facts` 为空 → 一个批注都不挂，行为与从前一致。

    source_file（2026-10-19，审计 P1-1）：这一章摊成表之前的 `.rpy`。
    给了就写进隐藏的「表信息」表，导回时 `compile_table` 据此**自动走增量**
    （没改的行逐字节保住），用户不必知道 `--source` 这个参数。
    不给（手工新建、协作库导出）→ 不写那张表，行为与从前完全一致。
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Beats'

    # 「每参数一列」（M4 第二阶段）的列契约 = **基础列 + 动态列**。
    # 动态列必须在**写表头之前**算出来（表头与数据行的列数要一致，
    # 见 `beat_model._row_fields`）。没有声明语句的项目 → 空列表 →
    # 表与从前**逐列相同**：这是"新功能不许动老项目"的底线。
    statements = _resolve_statements(statements, output_path)
    # **不要求项目声明过语句**：`with` / `pause` / `window` / `camera` 这类
    # 标准语句的形状由工具自己声明（`spec_args.BUILTIN_ARG_SCHEMAS`）。
    spec_cols = dynamic_cols(beats, statements)
    spec_headers = [c['header'] for c in spec_cols]
    # 「每指令一列」摊列（M3-2，用户 D4' 方案 B）：立绘/退场的**多值**
    # 摊成每项一列 —— 只有这样每一格才能挂上图像候选下拉（Excel 的数据
    # 验证一个格子只认一个值）。上限 = 这批表里该列的**最大项数**；
    # 九成拍只有一项 → 一个摊列都不加（表与从前逐列相同）。
    extra_headers = _extra_headers_for(beats)

    headers = ([BEAT_HEADERS.get(f, f) for f in BEAT_FIELDS]
               + spec_headers + extra_headers)
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 30

    symbols = make_symbols(registry) if registry is not None else None
    # ⚠ **不再给选项体补 `True` 条件**（B 阶段三，2026-10-05）：
    # 从前必须补，是因为表里没有"这拍在菜单块里"这一位，只能拿 Condition
    # 记 1 bit（`True` = 在某个选项体里 / 空 = 块外）。现在隐藏列 `Nest`
    # （块路径）随表走、渲染器也按它建树（见不变量 75/76），这一位就多余了；
    # 留着还会让产物多出一层 `if True:`、让"过表"与"直接渲染"两份产物对不上。
    # 老表（有 `True`、没有 `Nest`）照样能读：渲染退回老推断，而它要的正是那些 `True`。
    long_rows = []            # [(行号, 字段, 逐行文本)] —— 超长格搬去保真表
    for beat in beats:
        row = beat.to_row(symbols, spec_fields=spec_headers,
                          extra_fields=extra_headers)
        row, extra_long = _spill_long_cells(row, headers, beat)
        long_rows.extend(extra_long)
        _append_text_row(ws, row)

    # 自定义语句的头行候选（D1）：注册表在上面已经反推过了。
    _annotate_spec_heads(ws, beats, statements)
    # 「引擎没认的属性」→ 挂在**立绘格**的批注上（只提示、不过滤、不改数据）。
    # 没采过引擎事实时 facts 为空 → 一个批注都不挂（行为与从前一致）。
    _annotate_unknown_attrs(ws, beats, facts)

    # 列宽：台词最宽，技术列压窄。'ContentHash' 是协作指纹，给程序员看的，
    # 压到 12 并放到最后一列，避免它跟内容列抢视觉重心。
    widths = {'LineId': 14, 'Seq': 5, 'Label': 14, 'Kind': 10,
              'Speaker': 12, 'Comment': 24, 'Text': 48, 'Condition': 22,
              'Scene': 20, 'Show': 26, 'At': 14, 'With': 14, 'Hide': 16,
              'BGM': 18, 'BGMParams': 14, 'SFX': 18, 'SFXParams': 14,
              'Voice': 16, 'Stage': 36, 'CodeBlock': 30,
              'ContentHash': 12}
    for i, name in enumerate(BEAT_FIELDS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(name, 16)
    # 动态列（每参数一列）的列宽：按"参数名 + 候选"估，最少 14 ——
    # 它们默认可见（要给人填、给下拉），不能像内部列那样压到 8。
    for k, col in enumerate(spec_cols):
        letter = get_column_letter(len(BEAT_FIELDS) + k + 1)
        hint = max([len(col['param'])] + [len(c) for c in col['choices']] or [0])
        ws.column_dimensions[letter].width = max(14, min(30, hint + 6))
    # 摊列的列宽：一格一个立绘/退场项，跟 `Show`/`Hide` 差不多宽就够。
    for k, _name in enumerate(extra_headers):
        letter = get_column_letter(len(BEAT_FIELDS) + len(spec_headers) + k + 1)
        ws.column_dimensions[letter].width = 22
    # 内部元数据列整列隐藏（见 HIDDEN_BEAT_COLUMNS 的说明）：
    # 数据照旧读写，只是不占策划的视线。
    _hide_columns(ws, HIDDEN_BEAT_COLUMNS)
    # 冻结到「台词」列（G）：横向滚动时行号/章节/说话人/台词始终可见，
    # 这是"看表"最要紧的一条——之前冻结在 D2，说话人和台词会滚出去。
    ws.freeze_panes = 'G2'
    # 每行按内容估高：多行的 Stage/CodeBlock 不设高会被压成一行看不清
    _autosize_beat_rows(ws, len(beats))
    _last_col = get_column_letter(len(BEAT_FIELDS) + len(spec_cols))
    ws.auto_filter.ref = f'A1:{_last_col}{max(len(beats) + 1, 2)}'

    if registry is not None and include_dropdowns:
        ranges = _write_candidate_sheets(wb, registry)
        names = _defined_names_for(ranges)
        _attach_dropdowns(ws, ranges, len(beats), names=names,
                          extra_headers=extra_headers,
                          spec_count=len(spec_headers))
        # 每参数一列的**原生下拉**（M4 第二阶段）。放这里是因为
        # `choices_from`（`with` 的转场）要绑到**候选表**上，而候选表刚建好。
        _write_spec_arg_columns(ws, beats, statements, len(beats),
                                ranges=ranges, names=names)
        _write_keyword_sheet(wb, registry)
        # **活引用**（2026-10-18，审计 P1-6）：Beats ⇄ 资源表的真公式视图。
        # 放在候选表之后（要引用它们），不依赖 UI，也不改 Beats 的列契约。
        _live_ref_sheet(wb, beats, ranges)
        # 资源图库：给策划"看图选 ID"用的派生视图（不是符号表）
        _write_asset_gallery(
            wb, registry,
            project_root=(getattr(registry, 'project_root', None)
                          or _guess_project_root(output_path)),
            with_thumbnails=include_thumbnails)
    else:
        # 没有资源表（或明确关掉下拉）：**内联候选照样要挂** ——
        # `window` 的 show/hide/auto 不依赖资源表，`with` 的项目变换那一路
        # 挂不上就退回注释（`choices_from` 绑不到候选表时，下面会走注释兜底）。
        _write_spec_arg_columns(ws, beats, statements, len(beats))

    # 舞台快照：把"每一拍舞台上是什么样 + 本拍变了什么"算出来另写两张表。
    # 文案不用再去脑补 scene/show/hide 叠加的结果（二）。
    _write_stage_snapshot_sheet(wb, beats)
    # 文件级保真块（顶层 `define`/`screen`/`init python`/…）：**另存一张隐藏表**，
    # 因为它可能比 Excel 单元格的 32767 上限长得多（见 `_write_file_blocks_sheet`）。
    _write_file_blocks_sheet(wb, beats)
    # 超长文本：同一个问题（单元格装不下）的**另一个现场** —— 逐拍的格
    # （台词/注释/代码/舞台/条件…）也可能超过 32767（见 `CELL_TEXT_LIMIT`）。
    _write_long_text_sheet(wb, long_rows)
    _write_usage_sheet(wb, registry, beats, bool(include_dropdowns))
    # **来源信息**（2026-10-19，审计 P1-1）：这张表是从哪一份 .rpy 摊出来的。
    # 导回时靠它自动走增量（见 `table_meta` 的 docstring）——
    # 全量重生成会重排整份文件，而这件事完全可以不发生。
    try:
        from storygraph.story.table_meta import write_meta
        write_meta(wb, source_file=source_file)
    except Exception as e:                     # noqa: BLE001 - 元信息不该拦住出表
        swallow('excel_export.export_beats_to_excel.meta', e)

    wb.save(output_path)
    return output_path



def _write_file_blocks_sheet(wb, beats) -> None:
    """
    文件级保真块 → **隐藏表**（一行一行地存）。

    为什么不放隐藏列（2026-10-05 实测，第一版就是那么写的、踩了）：
    Excel 单元格上限 **32767 字符**，而真实文件的顶层区间轻松超过它 ——

        ball_moving_game.rpy   38492 字符
        my_perferences.rpy     50426 字符

    写进单元格**静默截断**（读回只剩 32767），于是"导出到新路径"丢东西、
    而且不报错 —— 正是这份代码要消灭的那一类缺陷。表没有这个限制，
    而且**一行只放一行文本**，连"单个块本身就超长"也一起解决。

    表整张隐藏：它是工具维护的搬运工，不是给人编辑的界面。
    """
    from storygraph.story.beat_model import (FILE_BLOCK_HEADERS,
                                            FILE_BLOCK_SHEET,
                                            file_blocks_to_rows,
                                            join_file_block_parts,
                                            split_file_blocks)

    # 多个拍上的块（正常只有第一张拍有）按序合并、去重 —— 与渲染时的口径一致
    seen, parts = set(), []
    for b in beats or ():
        for part in split_file_blocks(getattr(b, 'file_blocks', '')):
            if part.strip() and part not in seen:
                seen.add(part)
                parts.append(part)
    rows = file_blocks_to_rows(join_file_block_parts(parts))
    if not rows:
        return

    ws = wb.create_sheet(FILE_BLOCK_SHEET)
    ws.append(FILE_BLOCK_HEADERS)
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
    ws.column_dimensions['A'].width = 8
    ws.column_dimensions['B'].width = 120
    for bi, line in rows:
        _append_text_row(ws, [bi, line])
    ws.sheet_state = 'hidden'


def _spill_long_cells(row, headers, beat):
    """
    把**超长的格值**从行里搬走：格里留"预览 + 标记"，真身交给保真表。

    返回 `(改过的行, [(行号, 字段, 逐行文本), …])`。没超限的行**原样返回**。

    为什么不是"报错拒绝"（用户 2026-10-16 拍板）：超长演出是**合理需求**
    （整段旁白 / 系统提示 / 长独白），工具该把它存下来，而不是设个上限。
    """
    from storygraph.story.beat_model import split_long_text

    out = list(row)
    extra = []
    lid = str(getattr(beat, 'line_id', '') or '')
    for i, value in enumerate(out):
        preview, lines = split_long_text(value)
        if lines is None:
            continue
        out[i] = preview
        label = headers[i] if i < len(headers) else f'#{i + 1}'
        extra.append((lid, str(label), lines))
    return out, extra


def _write_long_text_sheet(wb, long_rows) -> None:
    """
    超长文本 → **隐藏表**（一行一格，不受 32767 限制）。

    与 `_write_file_blocks_sheet` 同一套办法（同一个病：单元格装不下）。
    整张隐藏：它是工具维护的搬运工，不是给人编辑的界面 ——
    策划看到的是台词格里的**预览 + 一句"全文见保真表"**。
    """
    from storygraph.story.beat_model import LONG_TEXT_HEADERS, LONG_TEXT_SHEET

    if not long_rows:
        return
    ws = wb.create_sheet(LONG_TEXT_SHEET)
    ws.append(LONG_TEXT_HEADERS)
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
    ws.column_dimensions['A'].width = 14
    ws.column_dimensions['B'].width = 12
    ws.column_dimensions['C'].width = 6
    ws.column_dimensions['D'].width = 120
    for lid, label, chunks in long_rows:
        for i, chunk in enumerate(chunks):
            _append_text_row(ws, [lid, label, i, chunk])
    ws.sheet_state = 'hidden'


def _read_long_text(wb) -> dict:
    """
    读保真表 → `{(行号, 字段): 全文}`（`_write_long_text_sheet` 的逆）。

    没有那张表（老表 / 本文本没超长）→ 空 dict，行为与从前逐字相同。
    ⚠ 片段**按序号拼**（不依赖表里的行序：用户可能动过那张表）。
    """
    from storygraph.story.beat_model import (LONG_TEXT_SHEET,
                                             join_long_text_parts)

    if LONG_TEXT_SHEET not in getattr(wb, 'sheetnames', ()):
        return {}
    parts = {}
    for row in wb[LONG_TEXT_SHEET].iter_rows(min_row=2, values_only=True):
        if not row or row[0] is None:
            continue
        lid = str(row[0])
        label = str(row[1]) if len(row) > 1 and row[1] is not None else ''
        try:
            idx = int(row[2]) if len(row) > 2 and row[2] is not None else 0
        except (TypeError, ValueError):
            idx = 0
        chunk = '' if len(row) < 4 or row[3] is None else str(row[3])
        parts.setdefault((lid, label), []).append((idx, chunk))
    return {k: join_long_text_parts([c for _, c in sorted(v)])
            for k, v in parts.items()}


def _write_stage_snapshot_sheet(wb, beats):
    """
    写派生视图：「舞台快照」（全量状态 + 变化着色）与「演出时间轴」（只看变化）。

    为什么要两张而不是一张：全量表回答"这句台词时台上是什么样"，但它几百行、
    绝大多数行没有变化，看不出"哪一刻切了 BGM、谁入场"。所以另出一张
    **只保留有变化的拍** 的时间轴表。

    着色约定（用户明确要的观看体验）：
      · 本拍变化的值 → 黄底
      · 从上一拍继承的值 → 灰字
    这样扫一眼就知道"谁一直站着、哪一刻变的"。
    """
    from storygraph.story.stage_snapshot import snapshot_rows, snapshot_summary

    rows = snapshot_rows(beats)

    # ---- 表 1：舞台快照（全量，带变化着色）----
    ws = wb.create_sheet('舞台快照')
    # 状态列分成 背景 / 立绘 / CG·图层 / 音乐 / 音效 / 语音 六块。
    # 「CG·图层」是必须的：只换 CG 而不动立绘的那一拍，如果快照不显示
    # CG，就会看起来"什么都没变"（假阴性）。
    headers = ['LineId', 'Seq', '章节', '说话人', '台词',
               '本拍变化', '背景', '立绘（谁·在哪）', 'CG·图层', '音乐',
               '音效', '语音', '备注']
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 26

    # 「本拍变化」是第 6 列；状态列从第 7 列起
    state_col = {'scene': 7, 'actors': 8, 'music': 10, 'sound': 11,
                 'voice': 12}
    # 非角色图层（cg/ui/movie…）统一落在第 9 列，任一图层变化就标黄
    LAYER_COL = 9
    LAYER_KINDS = ('cg', 'ui', 'gui', 'movie', 'video', 'logo', 'overlay',
                   'black', 'white', 'screen', 'text', 'layer')

    r = 2
    for row in rows:
        _append_text_row(ws, [row['line_id'], row['seq'], row['label'],
                              row['speaker'], row['text'],
                              row['display_changes'], row['scene'],
                              row['actors'], row.get('layers', ''),
                              row['music'], row['sound'],
                              row['voice'], row['note']])
        changed = row['changed']
        for name, col in state_col.items():
            cell = ws.cell(row=r, column=col)
            if name in changed:
                cell.fill = _CHANGED_FILL          # 黄底 = 本拍变的
                cell.font = Font(bold=True, color='7F4F00')
            elif cell.value not in (None, ''):
                cell.font = _INHERITED_FONT        # 灰字 = 继承来的
        lc = ws.cell(row=r, column=LAYER_COL)
        if set(changed) & set(LAYER_KINDS):
            lc.fill = _CHANGED_FILL
            lc.font = Font(bold=True, color='7F4F00')
        elif lc.value not in (None, ''):
            lc.font = _INHERITED_FONT
        # 变化清单本身也标黄，让"这一步有动作"一眼可见
        ws.cell(row=r, column=6).fill = _CHANGED_FILL
        ws.row_dimensions[r].height = _snapshot_row_height(row)
        r += 1

    for col, w in zip('ABCDEFGHIJKLM',
                      (14, 6, 16, 12, 32, 30, 20, 28, 22, 20, 16, 16, 18)):
        ws.column_dimensions[col].width = w
    for row_cells in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for c in row_cells:
            c.alignment = Alignment(vertical='top', wrap_text=True)
    ws.freeze_panes = 'F2'      # 冻结到「本拍变化」：横向滚动仍看得见归属与变化

    # ---- 表 2：演出时间轴（只保留有变化的拍）----
    tl = wb.create_sheet('演出时间轴')
    tl.append(['LineId', 'Seq', '章节', '说话人', '台词', '本拍变化'])
    for cell in tl[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical='center', wrap_text=True)
    tl.row_dimensions[1].height = 26
    tr = 2
    for row in snapshot_summary(beats):
        _append_text_row(tl, [row['line_id'], row['seq'], row['label'],
                              row['speaker'], row['text'],
                              row['display_changes']])
        tl.cell(row=tr, column=6).fill = _CHANGED_FILL
        tl.row_dimensions[tr].height = _snapshot_row_height(row)
        tr += 1
    if tr == 2:
        _append_text_row(tl, ['', '', '', '', '（整段没有任何舞台/音频变化）', ''])
    for col, w in zip('ABCDEF', (14, 6, 16, 12, 40, 38)):
        tl.column_dimensions[col].width = w
    for row_cells in tl.iter_rows(min_row=2, max_row=tl.max_row):
        for c in row_cells:
            c.alignment = Alignment(vertical='top', wrap_text=True)
    tl.freeze_panes = 'A2'
    tl.auto_filter.ref = f'A1:F{max(tl.max_row, 2)}'
    return ws



# 快照着色：变化 = 黄底；继承 = 灰字（用户明确要的"继承的灰、变化的黄"）
_CHANGED_FILL = PatternFill('solid', fgColor='FFF2CC')

_INHERITED_FONT = Font(color='909090')



def _snapshot_row_height(row: dict):
    """
    按内容行数估一个行高。

    不设行高时 Excel 会把 wrap_text 的多行内容压成固定高度，多值格只露出
    第一行——表格立刻就"看不清"了。这里按最长的那个多行格估。
    """
    longest = max([len(str(row.get('display_changes') or '').splitlines()),
                   len(str(row.get('actors') or '').splitlines()),
                   len(str(row.get('text') or '').splitlines())] or [1])
    text = str(row.get('text') or '')
    wrapped = (len(text) // 32) + 1
    lines = max(longest, min(wrapped, 4))
    return max(16.0, min(lines, 8) * 15.0)



def _autosize_beat_rows(ws, n_rows: int):
    """
    给 Beats 表按内容估行高。

    多行的 Stage / CodeBlock / 台词 在不设行高时会被 Excel 压成一行，
    恰好是"策划最需要复核"的那几列看不清。这里按列宽估一下行数。
    """
    if n_rows <= 0:
        return
    # 每列大致一行能放多少字（按列宽估）
    per_line = {'Text': 24, 'Stage': 18, 'CodeBlock': 15, 'Comment': 12,
                'Show': 13, 'Scene': 10, 'BGM': 9, 'SFX': 9}
    cols = {name: BEAT_FIELDS.index(name) + 1 for name in per_line
            if name in BEAT_FIELDS}
    for r in range(2, n_rows + 2):
        lines = 1
        for name, col in cols.items():
            v = ws.cell(row=r, column=col).value
            if not v:
                continue
            text = str(v)
            est = 0
            for part in text.split('\n'):
                est += max(1, (len(part) // per_line[name]) + 1)
            lines = max(lines, est)
        ws.row_dimensions[r].height = max(15.0, min(lines, 8) * 15.0)



def _write_usage_sheet(wb, registry, beats, has_candidates: bool):
    """
    「使用说明」的姊妹表：把"这张表怎么用、各列干什么"讲清楚。

    用户反馈的观看体验问题里，"表格列多但不知道看哪列"是主要一条。
    这里用一张小表把列分成三组（编辑区 / 高级区 / 程序区）讲明白。
    """
    ws = wb.create_sheet('列说明')
    ws.append(['列', '属于', '给谁看', '怎么填'])
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
    rows = [
        ('行号 LineId', '程序区', '协作/追溯', '自动生成，别改'),
        ('序号 Seq', '程序区', '排序', '自动生成'),
        ('章节 Label', '编辑区', '策划', '这一行属于哪个 label'),
        ('类型 Kind', '编辑区', '策划', '下拉：说话/旁白/菜单/菜单选项/延续'),
        ('说话人 Speaker', '编辑区', '策划', '下拉选角色'),
        ('注释 Comment', '编辑区', '策划', '给自己/同事看的备注，会写进脚本注释'),
        ('台词 Text', '编辑区', '策划', '这一句台词'),
        ('条件 Condition', '编辑区', '策划',
         '这一拍成立的条件（`if …:` 里的表达式）；留空 = 无条件'),
        ('背景 Scene', '编辑区', '策划', '下拉选背景；`图@变换` 可带背景变换'),
        ('立绘 Show', '编辑区', '策划', '下拉选图；`图@位置` 指定站位'),
        ('位置 At', '编辑区', '策划', '没用 @ 指定站位的立绘统一用这列'),
        ('转场 With', '编辑区', '策划', '下拉选转场'),
        ('退场 Hide', '编辑区', '策划', '下拉选图；`图@转场` 可带退场转场'),
        ('音乐 BGM', '编辑区', '策划', '下拉选音乐；也可选「（停止音乐）」'),
        ('音乐参数 BGMParams', '编辑区', '策划', '如 `fadein 1.0`'),
        ('音效 SFX', '编辑区', '策划', '下拉选音效；也可选「（停止音效）」'),
        ('音效参数 SFXParams', '编辑区', '策划', '如 `volume 0.5`'),
        ('语音 Voice', '编辑区', '策划', '下拉选语音'),
        ('舞台（高级）Stage', '高级区', '策划（进阶）', '结构化列表达不了的指令；'
                                                '人话或原生写法都行'),
        ('代码 CodeBlock', '程序区', '程序', '原样抄进脚本的逃生舱，不解析不校验'),
        ('内容指纹 ContentHash', '程序区', '协作', '自动重算，别改'),
        # 下面几列都是**隐藏列**（工具读写，策划平时看不见），
        # 列在这里是为了"表里到底有哪些格子"能一句句说清。
        ('label 参数 LabelHeader', '程序区', '程序',
         '`label 名(flag=True):` 的参数表；只记在该 label 的第一张拍上'),
        ('台词后缀 SayTail', '高级区', '程序',
         '台词引号后的样式子句（`(what_color="#f00")` / `with` / `id`）；空 = 普通台词'),
        ('菜单名 MenuName', '高级区', '程序',
         '`menu 名(args):` 的「名」。它是真 label —— `jump` / `call` 能跳进这个菜单'),
        ('菜单参数 MenuArgs', '高级区', '程序',
         '`menu 名(args):` 的「(args)」原文（含括号），喂给菜单 screen；空 = 裸 `menu:`'),
    ]
    for row in rows:
        _append_text_row(ws, list(row))
    for col, w in zip('ABCD', (22, 12, 14, 60)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'A2'
    return ws



# ---------------------------------------------------------------------------
#  资源图库：给策划看/选的派生视图（不是符号表）
# ---------------------------------------------------------------------------

# 图库分区顺序。`ui` 放最后，因为它就是"系统资源"，默认折叠在末尾。
_GALLERY_SECTIONS = (
    ('character', '立绘（按角色分组）'),
    ('background', '背景'),
    ('cg', 'CG / 事件图'),
    ('other', '其他图'),
    ('ui', '界面 / 系统资源（一般不用手写）'),
)


# 图库表头
_GALLERY_HEADERS = ('ID（可直接写进表格）', '说明', '真实文件',
                    '引用次数', '贴图预览')


#: 预览列放这么多像素宽的缩略图。太大表就散架，太小看不清。
_GALLERY_THUMB_PX = 96

_GALLERY_ROW_PX = 52


_GALLERY_TITLE = (
    '↓ 资源图库：由资源表 + 脚本自动生成，**只读**（改这里没用，请改资源表）。'
    '「引用次数」= 脚本里用到几次，0 表示还没人用。'
    '界面/系统资源统一放在最后一段，策划通常不用管。')


_SECTION_FILL = PatternFill('solid', fgColor='D9E1F2')

_SECTION_FONT = Font(bold=True, color='1F3864')



def _write_asset_gallery(wb, registry, project_root=None,
                         with_thumbnails: bool = False):
    """
    写「资源图库」工作表：按类目分组、带说明、引用次数与缩略图。

    为什么另立一张表而不是靠 Candidates_*（用户评审 第 9、10 条）：
      · Candidates_* 是**编译器的符号表**（A 列给下拉用），结构被下拉区间
        锁死，不能加图片、不能分组；
      · 真实项目 2902 张图里混着 `Frame 13` / `2026-2-1 13_20` 这类 UI 切图，
        策划在候选表里根本找不到想要的立绘——用户原话"根本无法编辑"。

    所以图库按 立绘（再按 tag 分组）/ 背景 / CG / 其他 / 界面 分段，界面段
    明确标注"一般不用手写"，且**带缩略图**（能看图就不靠记）。
    """
    from storygraph.assets.asset_registry import describe_asset

    ws = wb.create_sheet('资源图库')
    references = getattr(registry, 'references', {}) or {}

    buckets = {}
    for aid, row in (getattr(registry, 'images', {}) or {}).items():
        row = row or {}
        category = (row.get('Category') or 'other').strip() or 'other'
        if category not in dict(_GALLERY_SECTIONS):
            category = 'other'
        tag = (row.get('Tag') or '').strip()
        # 兜底归位：扫描器只看路径关键词，`charactercenter/Frame 13.png`
        # 会被判成立绘。可它的 tag 其实是文件名（Frame 13 / 2026-2-1 13_20），
        # 根本不可能是角色名——这类只能进界面段，否则立绘段全是 UI 切图，
        # 策划翻不到自己要的东西（用户评审第 10 条）。
        if category == 'character' and _looks_like_system_name(tag or aid):
            category = 'ui'
        buckets.setdefault(category, []).append((tag, aid, row))

    can_thumb = bool(with_thumbnails) and _resolver_available()
    budget = [_GALLERY_MAX_THUMBS]

    ws.append([_GALLERY_TITLE])
    ws['A1'].font = Font(italic=True, color='555555')
    ws['A1'].alignment = Alignment(vertical='center', wrap_text=True)
    ws.merge_cells(start_row=1, start_column=1, end_row=1,
                   end_column=len(_GALLERY_HEADERS))
    ws.row_dimensions[1].height = 30

    for col, w in zip('ABCDE', (30, 46, 40, 10, 15)):
        ws.column_dimensions[col].width = w

    def _section(row, title, count):
        ws.merge_cells(start_row=row, start_column=1, end_row=row,
                       end_column=len(_GALLERY_HEADERS))
        seg = ws.cell(row=row, column=1,
                      value=f'▌{title}　（{count} 项）')
        seg.font = _SECTION_FONT
        seg.fill = _SECTION_FILL
        seg.alignment = Alignment(vertical='center')
        ws.row_dimensions[row].height = 22
        return row + 1

    def _header(row):
        for ci, h in enumerate(_GALLERY_HEADERS, start=1):
            c = ws.cell(row=row, column=ci, value=h)
            c.fill = _HEADER_FILL
            c.font = _HEADER_FONT
            c.alignment = Alignment(vertical='center', wrap_text=True)
        ws.row_dimensions[row].height = 20
        return row + 1

    r = 3
    for key, title in _GALLERY_SECTIONS:
        items = buckets.get(key)
        if not items:
            continue
        items.sort(key=lambda t: (t[0], t[1]))
        r = _section(r, title, len(items))
        r = _header(r)
        last_tag = None
        for tag, aid, row in items:
            # 立绘段内再按 tag 插小标题，策划找"爱莲的所有表情"不用翻全表
            if key == 'character' and tag and tag != last_tag:
                last_tag = tag
                ws.merge_cells(start_row=r, start_column=1, end_row=r,
                               end_column=len(_GALLERY_HEADERS))
                sub = ws.cell(row=r, column=1, value=f'   ◆ {tag}')
                sub.font = Font(bold=True, color='2F5597')
                ws.row_dimensions[r].height = 18
                r += 1
            desc = describe_asset('image', row)
            used = len(references.get(aid, ()) or ())
            _append_text_row(ws, [aid, desc, row.get('Path') or '', used, ''])
            for ci in range(1, len(_GALLERY_HEADERS) + 1):
                ws.cell(row=r, column=ci).alignment = Alignment(
                    vertical='center', wrap_text=True)
            if used == 0:
                ws.cell(row=r, column=4).font = _INHERITED_FONT
            if can_thumb and key in ('character', 'background', 'cg'):
                _embed_gallery_thumbnail(ws, r, 5, row.get('Path'),
                                         project_root, aid, budget)
            ws.row_dimensions[r].height = _GALLERY_ROW_PX
            r += 1

    # 音频/变换也各来一小段（"看图选 ID"的诉求同样适用于它们）
    for kind, attr, title in (('music', 'music', '音乐'),
                              ('sound', 'sounds', '音效'),
                              ('voice', 'voices', '语音'),
                              ('transform', 'transforms', '自定义变换')):
        data = getattr(registry, attr, {}) or {}
        if not data:
            continue
        r = _section(r, title, len(data))
        r = _header(r)
        for aid in sorted(data):
            row = data[aid] or {}
            used = len(references.get(aid, ()) or ())
            _append_text_row(ws, [aid, describe_asset(kind, row),
                                  row.get('Path') or '', used, ''])
            for ci in range(1, len(_GALLERY_HEADERS) + 1):
                ws.cell(row=r, column=ci).alignment = Alignment(
                    vertical='center', wrap_text=True)
            if used == 0:
                ws.cell(row=r, column=4).font = _INHERITED_FONT
            ws.row_dimensions[r].height = 16
            r += 1

    ws.freeze_panes = 'A3'
    return ws


# ---------------------------------------------------------------------------
#  缩略图：把真实图片嵌进 Excel（Pillow 可选，没有就静默降级）
# ---------------------------------------------------------------------------

try:                                     # pragma: no cover - 依赖环境
    from PIL import Image as _PILImage
except Exception as e:                        # noqa: BLE001
    swallow('story.excel_export.<module>', e)
    _PILImage = None

#: 支持的扩展名：webp/动图之类 Pillow 不一定能读，读不了就跳过
_THUMB_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif')



def thumb_cache_dir():
    """缩略图缓存目录（放系统临时目录，不污染用户项目）。"""
    import tempfile
    d = Path(tempfile.gettempdir()) / 'renpy_sge_thumbs'
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return d


def thumb_cache_file(key: str, base=None):
    """
    缓存键 → 磁盘上的缩略图路径（**两级分片**，两级都是键的哈希前缀）。

    为什么不放在一个平目录里（2026-10-05 实测）：真实项目跑了几轮之后
    `%TEMP%\\renpy_sge_thumbs` 里堆了 **140886 个 PNG**。NTFS 单目录上万条
    之后，每次 `is_file()` 都要在那张巨大的索引里找 —— 命中缓存的"温解码"
    也因此变慢，而这笔代价**每一张缩略图都要付一次**。
    分片成 `<ab>/<cd>/<digest>.png` 之后每层最多几千条，查找恢复常数级。

    `base` 不给就用 `thumb_cache_dir()`。**留这个参数是为了保住测试/调用方的
    注入点**：`app.thumbs` 把 `thumb_cache_dir` 导进了自己的命名空间，
    测试用 `monkeypatch.setattr(thumbs, 'thumb_cache_dir', ...)` 换目录 ——
    这里若不接受 base，那条缝就断了（缓存会悄悄写到真实 %TEMP%）。

    ⚠ 旧版平铺文件（`<digest>.png`）**不再被新键命中** —— 由
    `prune_thumb_cache` 顺手清掉。
    """
    import hashlib
    digest = hashlib.md5(str(key).encode('utf-8', 'replace')).hexdigest()
    d = base if base is not None else thumb_cache_dir()
    if d is None:
        return None
    return Path(d) / digest[:2] / digest[2:4] / (digest + '.png')


def prune_thumb_cache(max_files: int = 20000, max_age_days: int = 45,
                      extra_dirs=None, budget_s: float = 2.0,
                      max_delete: int = 3000) -> int:
    """
    缩略图缓存**自我清理**：返回删掉的文件数。

    两条线（先按年龄、再多退少补按数量）：

      · 超过 `max_age_days` 没被用过的（mtime 老）→ 删；
      · 仍然超过 `max_files` 的 → 从最老的开始删到线以下。

    为什么要它（2026-10-05 实测）：缓存**从不过期也从不清理**，真实项目
    跑几轮就攒到 14 万个文件、几百 MB，而且把一个临时目录塞满对用户是
    "工具的锅"（系统清理工具也会来扫）。缓存本来就是**可再生的**，
    删掉只是下次多花一次解码。

    ⚠ **扫描必须有预算**（`budget_s`）：14 万条 `rglob + stat` 要十几秒，
    而它是在后台线程跑的 —— 一旦每开一个面板就来一遍，测试和界面都会被
    拖住（实测 pytest 直接卡住）。所以：

      · 用 `os.scandir`（比 `Path.rglob` 快得多，`stat` 结果随条目带回）；
      · 超过预算就**停止扫描**，只清已经收上来的那批（下一次再继续）；
      · 调用方（`ui_qt.thumb_loader`）另外保证**一个进程只跑一次**。
    """
    import os
    import time
    removed = 0
    try:
        d = thumb_cache_dir()
        if d is None:
            return 0
        roots = [d] + [Path(x) for x in (extra_dirs or ()) if x]
        deadline = time.monotonic() + max(0.1, float(budget_s))
        files = []
        for root in roots:
            if not root.is_dir():
                continue
            stack = [str(root)]
            while stack:
                if time.monotonic() > deadline:
                    break
                cur = stack.pop()
                try:
                    with os.scandir(cur) as it:
                        for entry in it:
                            if time.monotonic() > deadline:
                                break
                            try:
                                if entry.is_dir(follow_symlinks=False):
                                    stack.append(entry.path)
                                elif entry.name.endswith('.png'):
                                    files.append((entry.stat().st_mtime,
                                                  Path(entry.path)))
                            except OSError:
                                continue
                except OSError:
                    continue
        if not files:
            return 0
        files.sort()                                  # 最老的在前
        cutoff = time.time() - max(1, int(max_age_days)) * 86400
        keep_tail = max(0, int(max_files))
        # 过期的先删；剩下的仍然超标就从最老那头继续删
        doomed = [p for (m, p) in files if m < cutoff]
        rest = [p for (m, p) in files if m >= cutoff]
        if len(rest) > keep_tail:
            doomed.extend(rest[:len(rest) - keep_tail])
        for p in doomed:
            if removed >= max(0, int(max_delete)):
                # **每次只删一批**：14 万个文件的 unlink 要几分钟，
                # 一次性删完会把磁盘占满、把正在解码的那几张拖慢。
                # 剩下的留给下一次会话（缓存是可再生的，不急）。
                break
            try:
                p.unlink()
                removed += 1
            except OSError:
                continue
    except Exception as e:                            # noqa: BLE001
        swallow('story.excel_export.prune_thumb_cache', e)
    return removed



#: 兼容旧名：`story.script_table` 门面以 `_resolve_thumb_source` 转发它；
#: UI 侧的缩略图加载器走公开名（见 `ui/thumb_loader.py`）。
#: 用**函数包装**而不是直接赋值别名——直接赋值会踩定义顺序：本函数定义在
#: 后面，模块导入时求值就到 `NameError`（这就是刚才测试全收集失败的原因）。
def _resolve_thumb_source(path, project_root=None):
    return resolve_thumb_source(path, project_root)


def _thumbnail_bytes(path, project_root=None):
    """
    把一张图缩成 PNG 字节；读不了返回 None（绝不抛异常）。

    **带磁盘缓存**：缩略图只跟「源文件路径 + mtime + 尺寸」有关，
    同一张图第二次导出直接用缓存。没有缓存时真实项目一次导出要几分钟
    （2902 张图逐张解码缩放），策划等不起。
    """
    if _PILImage is None:
        return None
    real = resolve_thumb_source(path, project_root)
    if real is None:
        return None
    cache_file = None
    try:
        st = real.stat()
        key = '%s|%s|%s|%s' % (real, int(st.st_mtime), st.st_size,
                               _GALLERY_THUMB_PX)
        cache_file = thumb_cache_file(key)
        if cache_file is not None and cache_file.is_file():
            return cache_file.read_bytes()
    except OSError:
        cache_file = None
    try:
        with _PILImage.open(real) as im:
            # JPEG 可以让解码器**直接按缩小后的尺寸解码**（draft），
            # 不用先把几千万像素读进内存再缩——冷缓存下这是数量级的差别。
            try:
                im.draft('RGB', (_GALLERY_THUMB_PX, _GALLERY_THUMB_PX))
            except Exception:             # noqa: BLE001 - PNG 不支持，忽略
                swallow('excel_export._thumbnail_bytes')
            im = im.convert('RGBA')
            im.thumbnail((_GALLERY_THUMB_PX, _GALLERY_THUMB_PX))
            buf = BytesIO()
            im.save(buf, format='PNG')
            data = buf.getvalue()
    except Exception as e:                     # noqa: BLE001 - 坏图不能挡住导表
        swallow('story.excel_export._thumbnail_bytes', e)
        return None
    if cache_file is not None:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_bytes(data)
        except OSError as e:
            swallow('excel_export._thumbnail_bytes', e)
    return data



def resolve_thumb_source(path, project_root=None):
    """
    资源表里的 Path → 磁盘上真实存在的图。

    路径基准不统一（`sr_general/game/images/x.png` / `game/images/x.png` /
    `images/x.png`），所以逐个基准试；都找不到就放弃（不猜）。
    """
    text = str(path or '').strip().replace('\\', '/')
    if not text or not text.lower().endswith(_THUMB_EXTS):
        return None
    p = Path(text)
    candidates = []
    if p.is_absolute():
        candidates.append(p)
    else:
        # 资源表 Path 的基准五花八门：`sr_general/game/images/x.png`、
        # `game/images/x.png`、`images/x.png`、`x.png` 都出现过。逐个试，
        # 以**文件真的存在**为准——这是唯一可靠的判据。
        parts = [q for q in text.split('/') if q not in ('', '.')]
        bases = [Path.cwd()]
        if project_root:
            root = Path(project_root)
            bases = [root, root.parent, root / 'game'] + bases
        for base in bases:
            candidates.append(base / text)
            if len(parts) > 1:
                candidates.append(base / '/'.join(parts[1:]))
                candidates.append(base / 'game' / '/'.join(parts[1:]))
            candidates.append(base / 'game' / text)
    seen = set()
    for c in candidates:
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None



#: 一个工作簿最多嵌多少张缩略图。
#: 真实项目 2902 张图全嵌进去会让 xlsx 涨到几十 MB、导出要几十秒；
#: 图库是「看图选 ID」用的，截断比让人等更友好（超出部分照旧有 ID 与说明）。
#: 默认上限刻意压得比较小：首次导出要为每张图解码+缩放（真实项目
#: 2902 张图里很多是大尺寸 PNG，实测冷缓存一次要两三分钟）。有缓存后
#: 重导只要几秒，但第一印象不能让人等——所以默认只给前 N 张嵌图，
#: 其余照旧有 ID/说明/引用次数（选 ID 不受影响）。
_GALLERY_MAX_THUMBS = 150



def _embed_gallery_thumbnail(ws, row, col, path, project_root, aid,
                             budget=None):
    """
    在 (row, col) 嵌一张缩略图。

    Excel 的 `add_image` 需要**真实的文件/流**；这里用 BytesIO，
    并且失败一律静默跳过——图库是给人看的，不能因为一张坏图就让整张表写不出去。

    budget 是一个单元素列表 `[剩余额度]`：嵌满就不再嵌（见
    `_GALLERY_MAX_THUMBS`），避免真实项目写出一个几十 MB 的表。
    """
    if budget is not None and budget[0] <= 0:
        return False
    data = _thumbnail_bytes(path, project_root)
    if not data:
        return False
    try:
        img = XLImage(BytesIO(data))
        img.width = _GALLERY_THUMB_PX
        img.height = _GALLERY_THUMB_PX
        img.anchor = f'{get_column_letter(col)}{row}'
        ws.add_image(img)
    except Exception as e:                     # noqa: BLE001
        swallow('story.excel_export._embed_gallery_thumbnail', e)
        return False
    if budget is not None:
        budget[0] -= 1          # 不扣额度的话上限形同虚设
    return True



# 候选表顶部说明：它们是**编译器的符号表**，不是给策划读的表
_CANDIDATE_NOTE = ('↓ 本表是编译器的符号表（Symbol Table），由资源表镜像而来。'
                   'A 列 = 策划能写的资产 ID，B 列 = 说明，C 列 = 真实路径。'
                   '新增资产请先在资源表登记，再到编辑器点【刷新候选】。')


# 常用转场（Ren'Py 内置，可直接写在 `[转场]` 后面）
COMMON_TRANSITIONS = ('dissolve', 'fade', 'fadeblack', 'fadewhite',
                      'pixellate', 'move', 'ease', 'wipeleft', 'wiperight',
                      'irisin', 'irisout')



# 动态下拉：每个资产类型一个工作簿级名称，指向"会自动伸缩的区间"。
# 这样在候选表里往下加一行，演出表的下拉立刻能选到，**不需要**再点
# "刷新候选"（二）。名称用 ASCII，避免不同 Excel/WPS 版本对中文名的差异。
_DEFINED_NAME_PREFIX = 'CAND_'

_NAME_BY_TYPE = {
    'character': 'CAND_CHARACTER',
    'image': 'CAND_IMAGE',
    'transform': 'CAND_TRANSFORM',
    'music': 'CAND_MUSIC',
    'sound': 'CAND_SOUND',
    'voice': 'CAND_VOICE',
}



def _sheet_name_of(range_ref: str) -> str:
    """从 `'Sheet'!$A$3:$A$9` 里取出 sheet 名（去掉引号）。"""
    return range_ref.split('!', 1)[0].strip("'")



def _dynamic_range_formula(sheet_name: str) -> str:
    """
    OFFSET + COUNTA 组成的自伸缩区间：从候选表 A3 起，长度随实际条目数变化。

    用 MAX(...,1) 兜底：一个候选都没有时区间退化成 A3 单格，
    既不会让 Excel 报"引用无效"，也不会让下拉错位。
    """
    quoted = f"'{sheet_name}'"
    # 注意：defined name 的 attr_text 是公式本身，**不能**带前导 `=`，
    # 否则 Excel 会把这条命名区域当成畸形记录而"删除"（报
    # workbook.xml 的命名区域已删除）。
    return (f"OFFSET({quoted}!$A$3,0,0,"
            f"MAX(COUNTA({quoted}!$A$3:$A$5000),1),1)")



def _add_dynamic_names(wb, ranges: dict) -> dict:
    """
    给每个资产类型登记工作簿级名称（动态区间），返回 {类型: 名称}。

    openpyxl 的 defined_names 在同名重复时抛错，所以先删后加。
    """
    from openpyxl.workbook.defined_name import DefinedName

    names = {}
    for asset_type, ref in ranges.items():
        name = _NAME_BY_TYPE.get(asset_type)
        if not name:
            continue
        try:
            if name in wb.defined_names:
                del wb.defined_names[name]
            wb.defined_names.add(DefinedName(
                name, attr_text=_dynamic_range_formula(_sheet_name_of(ref))))
            names[asset_type] = name
        except Exception:
            # 个别 Excel 兼容模式写不进去时，退回固定区间下拉，不影响功能
            names.pop(asset_type, None)
    return names



def _defined_names_for(ranges: dict) -> dict:
    """已在工作簿里登记过名称的资产类型 → 名称（供挂下拉时优先使用）。"""
    return {t: _NAME_BY_TYPE[t] for t in ranges if t in _NAME_BY_TYPE}



def _beat_col(name: str) -> str:
    """`BEAT_FIELDS` 里的列名 → Excel 列字母（`Speaker` → `E`）。"""
    from openpyxl.utils import get_column_letter
    return get_column_letter(BEAT_FIELDS.index(name) + 1)


def _sheet_of_ref(ref: str) -> str:
    """`"'Candidates_角色'!$A$3:$A$20"` → `"Candidates_角色"`。"""
    return str(ref or '').split('!')[0].strip().strip("'")


def _live_ref_sheet(wb, beats, ranges: dict) -> None:
    """
    「资源引用（活）」—— Beats ⇄ 资源表之间的**真公式**（2026-10-18，审计 P1-6）。

    为什么需要它（审计原话）："下拉是**候选**，不是**引用**：改资源表，
    Beats 里已写的值不会跟着变（0 个公式）"。于是"表格之间相互引用"
    只是看起来像，实际是一堆死值。

    这张表的每一格都是**公式**（除了行号），两种连接：

      · **Beats → 资源表**：`VLOOKUP(说话人, Candidates_角色!A:C, 2, FALSE)`
        —— 资源表里把"说明"（显示名 / 路径）改掉，这张表**立刻**跟着变；
        这是"引用"而不是"候选"的可验证形态；
      · **资源表 → Beats**：`COUNTIF(Beats!$E:$E, "lj")`
        —— 每个资源被多少拍引用，改 Beats 立即重算。策划删资源前
        先看这一列就知道会不会删断。

    ⚠ 刻意**不**把 Beats 自己的格子改成公式：那会让"编辑值"变成
    "覆盖公式"，一次手滑就丢连接。引用是**旁路视图**，不是把主表变成
    电子表格程序。
    """
    cols = {name: _beat_col(name) for name in
            ('Label', 'Speaker', 'Text', 'Scene', 'Show', 'At',
             'BGM', 'SFX', 'Voice')}
    ws = wb.create_sheet('资源引用（活）')
    ws.cell(row=1, column=1).value = (
        '↓ 这张表除了行号，**全部是公式**（不是死值）：改左边资源表、'
        '或改 Beats，这里的显示名 / 路径 / 引用次数会立刻重算。')
    headers = ['行号', '章节', '说话人', '说话人（资源表）', '台词',
               '背景', '背景文件', '立绘', '立绘文件', '位置',
               '音乐', '音乐文件', '音效', '音效文件', '语音', '语音文件']
    for col, head in enumerate(headers, start=1):
        ws.cell(row=2, column=col).value = head
        ws.cell(row=2, column=col).fill = _HEADER_FILL
        ws.cell(row=2, column=col).font = _HEADER_FONT

    sheet_char = _sheet_of_ref((ranges or {}).get('character', ''))
    sheet_img = _sheet_of_ref((ranges or {}).get('image', ''))
    sheet_music = _sheet_of_ref((ranges or {}).get('music', ''))
    sheet_sound = _sheet_of_ref((ranges or {}).get('sound', ''))
    sheet_voice = _sheet_of_ref((ranges or {}).get('voice', ''))

    def look(sheet, cell_ref):
        if not sheet:
            return '""'
        return f'IFERROR(VLOOKUP({cell_ref},{sheet}!$A:$C,3,FALSE),"")'

    def look_desc(sheet, cell_ref):
        if not sheet:
            return '""'
        return f'IFERROR(VLOOKUP({cell_ref},{sheet}!$A:$C,2,FALSE),"")'

    r = 3
    for i, beat in enumerate(beats, start=1):
        src = i + 1                      # Beats 里这一拍的行号
        ws.cell(row=r, column=1).value = getattr(beat, 'line_id', '') or ''
        formulas = [
            f'=IFERROR(Beats!${cols["Label"]}{src},"")',
            f'=IFERROR(Beats!${cols["Speaker"]}{src},"")',
            '=' + look_desc(sheet_char, f'$C{r}'),
            f'=IFERROR(Beats!${cols["Text"]}{src},"")',
            f'=IFERROR(Beats!${cols["Scene"]}{src},"")',
            '=' + look(sheet_img, f'$F{r}'),
            f'=IFERROR(Beats!${cols["Show"]}{src},"")',
            '=' + look(sheet_img, f'$H{r}'),
            f'=IFERROR(Beats!${cols["At"]}{src},"")',
            f'=IFERROR(Beats!${cols["BGM"]}{src},"")',
            '=' + look(sheet_music, f'$K{r}'),
            f'=IFERROR(Beats!${cols["SFX"]}{src},"")',
            '=' + look(sheet_sound, f'$M{r}'),
            f'=IFERROR(Beats!${cols["Voice"]}{src},"")',
            '=' + look(sheet_voice, f'$O{r}'),
        ]
        for col, formula in enumerate(formulas, start=2):
            ws.cell(row=r, column=col).value = formula
        r += 1
    for col, w in zip('ABCDEFGHIJKLMNOP',
                      (12, 14, 12, 20, 40, 18, 26, 22, 26, 12,
                       16, 24, 16, 24, 14, 24)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'C3'

    # ---- 第二块：资源 → 被引用次数（公式重算）----
    r += 1
    ws.cell(row=r, column=1).value = (
        '↓ 资源被引用次数：改 Beats 就重算。删资源前先看这里 —— 不是 0 的'
        '资源还有人在用。')
    r += 1
    for col, head in enumerate(('资源表', 'ID', '说明', '被引用次数'), start=1):
        cell = ws.cell(row=r, column=col)
        cell.value = head
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
    used_col = {'character': cols['Speaker'],
                'image': cols['Show'],
                'transform': cols['At'],
                'music': cols['BGM'],
                'sound': cols['SFX'],
                'voice': cols['Voice']}
    rows_of = []
    for kind in ('character', 'image', 'transform', 'music', 'sound', 'voice'):
        if not (ranges or {}).get(kind):
            continue
        data = _asset_data_of(wb, _sheet_of_ref(ranges[kind]))
        for aid, desc in data:
            rows_of.append((kind, aid, desc))
    for kind, aid, desc in rows_of:
        r += 1
        ws.cell(row=r, column=1).value = _KIND_CN.get(kind, kind)
        # ID / 说明是**资源表那边的值**：用公式引用，改资源表立即跟着变
        sheet = _sheet_of_ref(ranges[kind])
        ws.cell(row=r, column=2).value = (
            f'=IFERROR(VLOOKUP("{aid}",{sheet}!$A:$C,1,FALSE),"{aid}")')
        ws.cell(row=r, column=3).value = (
            f'=IFERROR(VLOOKUP($B{r},{sheet}!$A:$C,2,FALSE),"")')
        col = used_col[kind]
        if kind == 'character':
            # 说话人是**整格一个 ID** → 精确匹配
            ws.cell(row=r, column=4).value = (
                f'=COUNTIF(Beats!${col}:${col},$B{r})')
        else:
            # 图像/音频/位置在 Beats 里是"一个格里多个词" → 包含匹配
            ws.cell(row=r, column=4).value = (
                f'=COUNTIF(Beats!${col}:${col},"*"&$B{r}&"*")')
    return ws


_KIND_CN = {'character': '角色', 'image': '图片', 'transform': '变换',
            'music': '音乐', 'sound': '音效', 'voice': '语音'}


def _asset_data_of(wb, sheet_name: str) -> list:
    """从候选表里读回 `[(ID, 说明)]`（只读 A/B 两列，第 3 行起）。"""
    try:
        ws = wb[sheet_name]
    except KeyError:
        return []
    out = []
    for row in ws.iter_rows(min_row=3, min_col=1, max_col=2, values_only=True):
        aid = str(row[0] or '').strip()
        if not aid:
            continue
        out.append((aid, str(row[1] or '')))
    return out


def _write_candidate_sheets(wb, registry) -> dict:
    """
    写出候选表，返回 {资产类型: "Sheet!$A$3:$A$N"} 形式的引用区间。

    第 1 行是来源说明，第 2 行是表头（ID / 说明 / 参数），第 3 行起才是
    候选（下拉引用第 3 行起，避免把说明行也变成可选项）。

    加"说明"列是刻意的：光看 ID（`leftish`、`e_happy`）文案根本不知道
    是什么效果，尤其 transform——它对应的注释解释就在这里。
    """
    from storygraph.assets.asset_registry import describe_asset

    mapping = [
        ('character', 'Candidates_角色', registry.characters or {}),
        ('image', 'Candidates_图片', registry.images or {}),
        ('transform', 'Candidates_变换', registry.transforms or {}),
        ('music', 'Candidates_音乐', registry.music or {}),
        ('sound', 'Candidates_音效', registry.sounds or {}),
        ('voice', 'Candidates_语音', registry.voices or {}),
    ]

    ranges = {}
    for asset_type, sheet_name, data in mapping:
        ws = wb.create_sheet(sheet_name)
        _set_text(ws, 1, 1, _CANDIDATE_NOTE)
        _set_text(ws, 2, 1, 'ID')
        _set_text(ws, 2, 2, '说明')
        _set_text(ws, 2, 3, '参数/路径')

        # 音频列把"动作"做进同一个下拉（伪值排在最前，容易选到）：
        # 策划不用再记一列 MusicAction，也不会打错 stop/queue。
        pseudo = []
        if asset_type == 'music':
            pseudo = list(AUDIO_PSEUDO['BGM'].items())
        elif asset_type == 'sound':
            pseudo = list(AUDIO_PSEUDO['SFX'].items())

        rows = [(word, f'动作：{action}（不播放具体音频）', '')
                for word, action in pseudo]
        for aid in sorted(data.keys()):
            row = data.get(aid) or {}
            if asset_type == 'transform':
                extra = row.get('Params') or ''
            else:
                # 音乐/音效/语音/图片都把真实文件路径带出来：脚本里最终要
                # 写的是能被 Ren'Py 解析的引用，光有内部 ID 不够。
                extra = row.get('Path') or ''
            rows.append((aid, describe_asset(asset_type, row), extra))

        for i, (aid, desc, extra) in enumerate(rows, start=3):
            _set_text(ws, i, 1, aid)
            _set_text(ws, i, 2, desc)
            if extra:
                _set_text(ws, i, 3, extra)

        ws.column_dimensions['A'].width = 28
        ws.column_dimensions['B'].width = 48
        ws.column_dimensions['C'].width = 30
        if rows:
            ranges[asset_type] = f"'{sheet_name}'!$A$3:$A${len(rows) + 2}"

    # 登记动态名称：以后在候选表里追加资产行，下拉自动跟着扩
    _add_dynamic_names(wb, ranges)
    return ranges



def _write_keyword_sheet(wb, registry) -> None:
    """
    写「词表」+「使用说明」两张辅助表。

    为什么要有词表：Stage 是一列 DSL 文本，Excel 的数据验证只能约束
    "整格"、约束不了"格内的词"。所以把合法词汇摊在一张表里——策划照着
    抄，编译器在生成结果里标记写错的词（TODO）。这是不拆列也能得到的
    严格性。
    """
    from storygraph.story.dsl import DEFAULT_EXPRESSIONS, DEFAULT_POSITIONS

    ws = wb.create_sheet('词表')
    _set_text(ws, 1, 1, '↓ 这里的词可以直接写进 Beats 的 Stage / BGM / SFX 列。'
                        '写错不会被拦，但生成的脚本里会出现 # TODO 注释。')
    for col, head in enumerate(('类别', '可写的词', '会变成', '说明'), start=1):
        _set_text(ws, 2, col, head)

    rows = []
    for var, row in (getattr(registry, 'characters', {}) or {}).items():
        name = (row or {}).get('Name') or var
        tag = (row or {}).get('ImageTag') or var
        rows.append(('角色', str(name).strip('「」『』'),
                     f'image {tag}',
                     f'[显示] {str(name).strip("「」『』")} 开心 左 → show {tag} …'))
    for word, mapped in sorted(DEFAULT_POSITIONS.items()):
        rows.append(('位置', word, f'at {mapped}' if mapped else '（不加 at）',
                     f'[显示] 角色 开心 {word}'))
    for word, mapped in sorted(DEFAULT_EXPRESSIONS.items()):
        if not mapped:
            continue
        rows.append(('表情', word, mapped,
                     '按该角色的候选属性模糊匹配（开心 → close_happy）'))
    for t in COMMON_TRANSITIONS:
        rows.append(('转场', t, f'with {t}', '[转场] %s' % t))
    for name in (getattr(registry, 'transforms', {}) or {}):
        if name not in RENPY_BUILTIN_TRANSFORMS:
            rows.append(('变换', name, f'at {name}',
                         '项目自定义变换（transforms.xlsx）'))

    r = 3
    for cat, word, target, note in rows:
        _set_text(ws, r, 1, cat)
        _set_text(ws, r, 2, word)
        _set_text(ws, r, 3, target)
        _set_text(ws, r, 4, note)
        r += 1
    for col, w in (('A', 10), ('B', 22), ('C', 22), ('D', 52)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'A3'

    _write_help_sheet(wb)



def _write_help_sheet(wb) -> None:
    """「使用说明」：把 DSL 语法和三条规矩写清楚，策划不用问人。"""
    ws = wb.create_sheet('使用说明')
    lines = [
        ('演出表怎么填（策划只看 Beats 这一张）', True),
        ('', False),
        ('一、三列写演出：', True),
        ('  Stage  舞台：出场 / 退场 / 换背景 / 转场 / 等待', False),
        ('  BGM    音乐：播放、淡入淡出、停止', False),
        ('  SFX    音效：播放、停止', False),
        ('', False),
        ('二、写法（多条用 ; 或换行分开）', True),
        ('  [显示] 凌跃捷 开心 左          → show ling close_happy at left', False),
        ('  [显示] 凌跃捷 开心 左 with dissolve  → 带转场出场', False),
        ('  [隐藏] 凌跃捷                  → hide ling', False),
        ('  [场景] 实验室 with dissolve    → scene bg 实验室 with dissolve', False),
        ('  [转场] dissolve               → with dissolve', False),
        ('  [等待] 1.0                    → pause 1.0', False),
        ('  [音乐] 春天花瓣 fadein 1.0     → play music "bgm/…" fadein 1.0', False),
        ('  [停止音乐]                    → stop music', False),
        ('  [音效] 开门                   → play sound "sfx/…"', False),
        ('  位置词可省略；表情可写多个（凌跃捷 开心 侧身）', False),
        ('', False),
        ('三、程序写复杂演出的后门：CodeBlock 列', True),
        ('  里面写什么就原封不动抄进脚本，不解析、不校验。', False),
        ('  例：renpy.transition(Dissolve(0.1), layer="master")', False),
        ('', False),
        ('四、找不到资源会怎样？', True),
        ('  不报错、不中断。生成的脚本里会出现一行 # TODO: 缺少资源 …，', False),
        ('  那句话不会生成（避免游戏运行时报错），其余照常生成。', False),
        ('', False),
        ('五、原生写法也能写（程序/老策划习惯）', True),
        ('  直接写 show ling close_happy at left，编译器原样直通、不做解析。', False),
        ('', False),
        ('六、其他工作表', True),
        ('  舞台快照    每句话时舞台上是什么样（自动算，只读）', False),
        ('  Export_RenPy 编译结果（由工具生成，不要手改）', False),
        ('  Candidates_* 编译器的符号表，不是给策划读的', False),
        ('  词表        能写在 Stage 里的全部词', False),
    ]
    for i, (text, bold) in enumerate(lines, start=1):
        cell = _set_text(ws, i, 1, text)
        if bold:
            cell.font = _HEADER_FONT
    ws.column_dimensions['A'].width = 78



def refresh_candidate_sheets(table_path, registry) -> Path:
    """
    仅重写候选表（Candidates_*），不动 Beats 表里的任何用户数据。

    用于"资源表新增了资产 → 演出表下拉看不见"的场景：点一下刷新，
    下拉就跟上，而不必重新导出 .rpy、丢掉文案改好的台词/立绘。
    """
    table_path = Path(table_path)
    wb = openpyxl.load_workbook(table_path)
    for ws in list(wb.worksheets):
        if ws.title.startswith('Candidates_') or ws.title in ('词表',
                                                              '使用说明'):
            del wb[ws.title]

    ranges = _write_candidate_sheets(wb, registry)
    _write_keyword_sheet(wb, registry)

    ws = wb['Beats'] if 'Beats' in wb.sheetnames else wb.active
    # 清掉旧下拉，避免重复叠加
    if ws.data_validations.dataValidation:
        del ws.data_validations.dataValidation[:]
    row_count = max(ws.max_row - 1, 0)
    _attach_dropdowns(ws, ranges, row_count, names=_defined_names_for(ranges))

    # 顺手把舞台快照重算一遍，让它在候选刷新后跟演出表保持一致
    try:
        if '舞台快照' in wb.sheetnames:
            del wb['舞台快照']
        beats = _read_beats_from_ws(ws, strict_long_text=False)
        hydrate_from_dsl(beats, make_symbols(registry))
        _write_stage_snapshot_sheet(wb, beats)
    except Exception as e:
        swallow('excel_export.refresh_candidate_sheets', e)

    wb.save(table_path)
    return table_path



def _restore_long_text(row_dict: dict, long_text: dict, lid_label: str,
                       conflicts: list = None) -> None:
    """
    格子里只有"预览 + 标记"时，从保真表取回**全文**（就地改 `row_dict`）。

    ⚠ 按 `(行号, 字段标签)` 取 —— 与写侧 `_spill_long_cells` 同一把钥匙。
    `long_text` 为空（老表 / 没超长）时**一个字节都不动**。

    ⚠⚠ **预览格被改过 → 不覆盖，记进 `conflicts`**（P1-3，2026-10-17）：

    写侧留下的预览是**确定**的（`全文[:200] + 标记`），所以读侧能逐字
    验算。对不上只有两种可能，而两种都不能自动处理：

      · 照旧用全文覆盖 → 用户的编辑**静默消失**（实测：把预览改成
        "USER EDIT …⟪全文见…⟫"，导入后 'USER EDIT' 一个字都不剩）；
      · 直接用改过的预览 → 40000 字全文在下次导出时**静默截成 200 字**。

    所以这里只**拒绝还原并报告**，由 `_read_beats_from_ws` 抛
    `LongTextPreviewEdited`，让人把那一格改回去（或删掉标记）。
    """
    from storygraph.story.beat_model import (LONG_TEXT_MARK,
                                             LONG_TEXT_PREVIEW)

    if not long_text:
        return
    lid = str(row_dict.get(lid_label) or '')
    for label, value in list(row_dict.items()):
        if not isinstance(value, str) or LONG_TEXT_MARK not in value:
            continue
        full = long_text.get((lid, label))
        if full is None:
            continue
        expected = str(full)[:LONG_TEXT_PREVIEW] + LONG_TEXT_MARK
        if value != expected:
            if conflicts is not None:
                conflicts.append({'line_id': lid, 'field': label,
                                  'length': len(str(full))})
            continue
        row_dict[label] = full


def _read_beats_from_ws(ws, long_text: dict = None,
                        strict_long_text: bool = True) -> list:
    """
    从已打开的 Beats 工作表读回 Beat（refresh / import 内部用）。

    `strict_long_text=True`（默认）：预览格被改过时抛
    `LongTextPreviewEdited`（见 `_restore_long_text`）—— 导入必须停下来，
    否则不是吃掉编辑就是截断全文。`refresh_candidate_sheets` 这类**派生视图**
    的刷新传 False（它不改内容，没必要为一张预览拦住整次刷新）。
    """
    headers = [c.value for c in ws[1]]
    lid_label = next((str(h) for h in headers if str(h) == '行号'), '行号')
    long_text = long_text if long_text is not None else {}
    beats = []
    conflicts: list = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if all(v is None or str(v).strip() == '' for v in row):
            continue
        row_dict = {}
        for i, header in enumerate(headers):
            if header is None:
                continue
            row_dict[str(header)] = row[i] if i < len(row) else None
        _restore_long_text(row_dict, long_text, lid_label, conflicts)
        beat = Beat.from_row(row_dict)
        if not beat.is_empty():
            beats.append(beat)
    if conflicts and strict_long_text:
        from storygraph.story.beat_model import LongTextPreviewEdited
        raise LongTextPreviewEdited(conflicts)
    # **排序权威 = Seq**（P0-3）：Excel 里按说话人排序 / 拖动行之后，行顺序
    # 与 Seq 会打架；渲染只认列表顺序，不修就会静默改剧情。同 label 连续段
    # 内按 Seq 重排（缺号行留在原位），并在第一拍上留一条 W203 的提示。
    from storygraph.story.chapter import order_beats_by_seq
    beats, seq_notes = order_beats_by_seq(beats)
    if seq_notes and beats:
        beats[0].runtime_notes.extend(seq_notes)
    assign_identity(beats)
    return beats



def _extra_headers_for(beats) -> list:
    """
    「每指令一列」摊列的表头（M3-2）：立绘/退场的第 2..n 项各占一列。

    上限取**这批表里该列的最大项数** —— 九成拍只有一项，于是**一个摊列都不加**
    （表与从前逐列相同，老项目零影响）。第 1 项仍留在 `Show`/`Hide` 原列。
    """
    from storygraph.story.beat_model import (HIDE_EXTRA_PREFIX,
                                             SHOW_EXTRA_PREFIX,
                                             extra_field_names,
                                             hide_cell_parts, show_cell_parts)
    n_show = max((len(show_cell_parts(b)) for b in beats), default=0)
    n_hide = max((len(hide_cell_parts(b)) for b in beats), default=0)
    return (extra_field_names(SHOW_EXTRA_PREFIX, n_show)
            + extra_field_names(HIDE_EXTRA_PREFIX, n_hide))


def _attach_dropdowns(ws, ranges: dict, row_count: int, names: dict = None,
                      extra_headers=None, spec_count: int = 0):
    """
    给指定列挂上下拉验证。

    names 非空时优先用工作簿级**动态名称**（区间自伸缩）；否则退回
    固定区间。空候选表不挂（Excel 会报无效引用）。

    **下拉是"方便"不是"牢笼"**：`showErrorMessage=False`，
    所以 `bg room@bgfit` 这种扩展写法照样能手打，不会被拦。
    """
    if row_count < 1:
        return
    names = names or {}
    last_row = row_count + 1

    def _add(letter, formula1, title, prompt):
        dv = DataValidation(
            type='list',
            formula1=formula1,
            allow_blank=True,
            showDropDown=False,      # False 才会在 Excel 里显示箭头
        )
        dv.showErrorMessage = False  # 手打的扩展写法不该被拦住
        dv.prompt = prompt
        dv.promptTitle = title
        ws.add_data_validation(dv)
        dv.add(f"{letter}2:{letter}{max(last_row, 200)}")

    for idx, col_name in enumerate(BEAT_FIELDS, start=1):
        asset_type = DROPDOWN_COLUMNS.get(col_name)
        if not asset_type:
            continue
        # 优先动态名称；没有名称就退回写死的区间
        formula1 = None
        if asset_type in names:
            formula1 = names[asset_type]
        elif asset_type in ranges:
            formula1 = ranges[asset_type]
        if not formula1:
            continue
        letter = get_column_letter(idx)
        hint = {'Speaker': '从角色表选（也可直接写角色变量名）',
                'Scene': '从图片表选背景；要变换写 图@变换',
                'Show': '从图片表选立绘；多个用 ; 分隔；要位置写 图@变换',
                'Hide': '要退场的立绘/图像；带转场写 图@转场',
                'At': '位置变换（立绘用）',
                'With': '转场效果',
                'BGM': '从音乐表选；停止/排队请选伪值',
                'SFX': '从音效表选；停止/排队请选伪值',
                'Voice': '从语音表选'}.get(col_name, '从资源表选择')
        # 摊列生效时，原列只装**第 1 项** —— 提示必须跟着改，
        # 否则它会教策划"多个用 ; 分隔"，那正好会把摊列绕回多值。
        if extra_headers:
            if col_name == 'Show':
                hint = '第 1 个立绘（一格一个，后续项在右边的「立绘2…」列）'
            elif col_name == 'Hide':
                hint = '第 1 个退场（一格一个，后续项在「退场2…」列）'
        _add(letter, formula1, BEAT_HEADERS.get(col_name, col_name), hint)

    # 内联下拉（不依赖资源表）：类型、动作等固定取值
    for col_name, values in ACTION_COLUMNS.items():
        if col_name not in BEAT_FIELDS:
            continue
        idx = BEAT_FIELDS.index(col_name) + 1
        _add(get_column_letter(idx), '"' + ','.join(values) + '"',
             BEAT_HEADERS.get(col_name, col_name),
             ACTION_HINTS.get(col_name, '从下拉里选'))

    # **摊列的原生下拉**（M3-2）—— 摊列的**目的**就在这一条：一格一项，
    # 于是每一格都能绑图像候选（`Show` 格是多值，绑了也只能管整格）。
    from storygraph.story.beat_model import (HIDE_EXTRA_PREFIX,
                                             SHOW_EXTRA_PREFIX,
                                             extra_index_of)
    img_formula = names.get('image') or ranges.get('image')
    for k, head in enumerate(extra_headers or ()):
        idx = extra_index_of(head, SHOW_EXTRA_PREFIX)
        what = '立绘'
        if idx is None:
            idx = extra_index_of(head, HIDE_EXTRA_PREFIX)
            what = '退场'
        if idx is None or not img_formula:
            continue
        letter = get_column_letter(len(BEAT_FIELDS) + int(spec_count or 0)
                                   + k + 1)
        _add(letter, img_formula, str(head),
             f'第 {idx} 个{what}（一格一个；要位置写 图@变换）')



#: 一眼就像"系统/界面"的名字：Frame 13、2026-2-1 13_20、纯数字、纯符号。
#: 它们不可能是角色 tag，却会被"路径含 character"的关键词规则判成立绘。
_SYSTEM_NAME_RE = re.compile(
    r'^(frame|btn|button|icon|icon_|img_?\d|image_?\d|\d+$|\d[\d\s._:-]*$'
    r'|sprite\d*$|pic\d*$)', re.I)



def _looks_like_system_name(name: str) -> bool:
    """这个名字像 UI/系统切图而不是角色 tag 吗？"""
    text = (name or '').strip()
    if not text:
        return True
    if _SYSTEM_NAME_RE.match(text):
        return True
    # 形如 `2026-2-1 13_20`：数字 + 日期分隔符，没有字母
    if re.match(r'^[\d\s._:\-]+$', text):
        return True
    return False



def _resolver_available() -> bool:
    """缩略图能力可用吗（Pillow 在 + 有能读的扩展名）。"""
    return _thumbnail_bytes is not None
