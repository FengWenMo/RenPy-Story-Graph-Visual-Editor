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
import re
from io import BytesIO
from pathlib import Path
import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter
from storygraph.assets.asset_registry import RENPY_BUILTIN_TRANSFORMS

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
    _assign_identity,
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



def export_beats_to_excel(beats: list, output_path,
                          registry=None, include_dropdowns: bool = True,
                          include_thumbnails: bool = False):
    """
    把 Beat 列表写成 Excel。

    registry 非空时会额外生成 角色/图片/变换/音乐/音效/语音 六张候选表、
    一张「资源图库」，并给主表相应列挂上下拉验证——文案只需要点选，
    不会打错 ID。

    include_thumbnails：图库里的缩略图（给策划"看图选 ID"用）。
    **默认关**——首次导出要为每张图解码缩放（真实项目冷缓存实测 ~90s），
    CI / CLI 不该为此变慢。编辑器走后台线程并默认打开。
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Beats'

    ws.append([BEAT_HEADERS.get(f, f) for f in BEAT_FIELDS])
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 30

    symbols = make_symbols(registry) if registry is not None else None
    for beat in beats:
        _append_text_row(ws, beat.to_row(symbols))

    # 列宽：台词最宽，技术列压窄。'ContentHash' 是协作指纹，给程序员看的，
    # 压到 12 并放到最后一列，避免它跟内容列抢视觉重心。
    widths = {'LineId': 14, 'Seq': 5, 'Label': 14, 'Kind': 10,
              'Speaker': 12, 'Comment': 24, 'Text': 48,
              'Scene': 20, 'Show': 26, 'At': 14, 'With': 14, 'Hide': 16,
              'BGM': 18, 'BGMParams': 14, 'SFX': 18, 'SFXParams': 14,
              'Voice': 16, 'Stage': 36, 'CodeBlock': 30,
              'ContentHash': 12}
    for i, name in enumerate(BEAT_FIELDS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(name, 16)
    # 内部元数据列整列隐藏（见 HIDDEN_BEAT_COLUMNS 的说明）：
    # 数据照旧读写，只是不占策划的视线。
    _hide_columns(ws, HIDDEN_BEAT_COLUMNS)
    # 冻结到「台词」列（G）：横向滚动时行号/章节/说话人/台词始终可见，
    # 这是"看表"最要紧的一条——之前冻结在 D2，说话人和台词会滚出去。
    ws.freeze_panes = 'G2'
    # 每行按内容估高：多行的 Stage/CodeBlock 不设高会被压成一行看不清
    _autosize_beat_rows(ws, len(beats))
    ws.auto_filter.ref = f'A1:{get_column_letter(len(BEAT_FIELDS))}{max(len(beats) + 1, 2)}'

    if registry is not None and include_dropdowns:
        ranges = _write_candidate_sheets(wb, registry)
        _attach_dropdowns(ws, ranges, len(beats),
                          names=_defined_names_for(ranges))
        _write_keyword_sheet(wb, registry)
        # 资源图库：给策划"看图选 ID"用的派生视图（不是符号表）
        _write_asset_gallery(
            wb, registry,
            project_root=(getattr(registry, 'project_root', None)
                          or _guess_project_root(output_path)),
            with_thumbnails=include_thumbnails)

    # 舞台快照：把"每一拍舞台上是什么样 + 本拍变了什么"算出来另写两张表。
    # 文案不用再去脑补 scene/show/hide 叠加的结果（二）。
    _write_stage_snapshot_sheet(wb, beats)
    _write_usage_sheet(wb, registry, beats, bool(include_dropdowns))

    wb.save(output_path)
    return output_path



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
except Exception:                        # noqa: BLE001
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
        import hashlib
        digest = hashlib.md5(key.encode('utf-8', 'replace')).hexdigest()
        cdir = thumb_cache_dir()
        if cdir is not None:
            cache_file = cdir / (digest + '.png')
            if cache_file.is_file():
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
                pass
            im = im.convert('RGBA')
            im.thumbnail((_GALLERY_THUMB_PX, _GALLERY_THUMB_PX))
            buf = BytesIO()
            im.save(buf, format='PNG')
            data = buf.getvalue()
    except Exception:                     # noqa: BLE001 - 坏图不能挡住导表
        return None
    if cache_file is not None:
        try:
            cache_file.write_bytes(data)
        except OSError:
            pass
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
    except Exception:                     # noqa: BLE001
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
        beats = _read_beats_from_ws(ws)
        hydrate_from_dsl(beats, make_symbols(registry))
        _write_stage_snapshot_sheet(wb, beats)
    except Exception:
        pass

    wb.save(table_path)
    return table_path



def _read_beats_from_ws(ws) -> list:
    """从已打开的 Beats 工作表读回 Beat（refresh 内部用）。"""
    headers = [c.value for c in ws[1]]
    beats = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if all(v is None or str(v).strip() == '' for v in row):
            continue
        row_dict = {}
        for i, header in enumerate(headers):
            if header is None:
                continue
            row_dict[str(header)] = row[i] if i < len(row) else None
        beat = Beat.from_row(row_dict)
        if not beat.is_empty():
            beats.append(beat)
    _assign_identity(beats)
    return beats



def _attach_dropdowns(ws, ranges: dict, row_count: int, names: dict = None):
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
        _add(letter, formula1, BEAT_HEADERS.get(col_name, col_name), hint)

    # 内联下拉（不依赖资源表）：类型、动作等固定取值
    for col_name, values in ACTION_COLUMNS.items():
        if col_name not in BEAT_FIELDS:
            continue
        idx = BEAT_FIELDS.index(col_name) + 1
        _add(get_column_letter(idx), '"' + ','.join(values) + '"',
             BEAT_HEADERS.get(col_name, col_name),
             ACTION_HINTS.get(col_name, '从下拉里选'))



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
