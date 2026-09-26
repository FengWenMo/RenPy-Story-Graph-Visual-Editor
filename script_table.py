"""
script_table.py
================
剧本演出表：把 .rpy 变成一张"一行一句演出"的 Excel，并把它变回 .rpy。

设计目标（这是整个编辑器的价值所在）：
  · 文案在 Excel 里改台词、换立绘、挑音乐，不用碰代码
  · 资产列是**下拉框**，候选来自资产表，杜绝手打错 ID
  · 改完导出，生成的 .rpy 是能直接跑的 Ren'Py 脚本
  · 反向也成立：手改 .rpy 后再导出，表格跟着变

一行 = 一个 Beat（演出单元）。单元格里可以写多个立绘，用 `;` 分隔，
每项支持 `图像@变换` 的写法，例如：

    bg_classroom; eileen_happy@left; eileen_sad@right

角色列填的是角色变量名（脚本里的 `e`），不是显示名——因为生成的
代码要用它。下拉里两者都有，显示名只作提示。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter

from core.statement import Statement, StatementKind


# ===================== 数据结构 =====================

BEAT_FIELDS = [
    'Seq', 'Label', 'Condition', 'Kind', 'Speaker', 'Text',
    'Scene', 'Show', 'Hide', 'At', 'With', 'Transform',
    'Music', 'MusicAction', 'Sound', 'SoundAction', 'Voice', 'Notes',
]

# 需要下拉框的列 -> 资产类型
DROPDOWN_COLUMNS = {
    'Speaker': 'character',
    'Scene': 'image',
    'Show': 'image',
    'At': 'transform',
    'Transform': 'transform',
    'With': 'transform',
    'Music': 'music',
    'Sound': 'sound',
    'Voice': 'voice',
}

# 动作列：内联固定列表（不依赖资源表）
ACTION_COLUMNS = {
    'MusicAction': ['play', 'queue', 'stop'],
    'SoundAction': ['play', 'queue', 'stop'],
}

KIND_SAY = 'say'
KIND_NARRATION = 'narration'
KIND_MENU = 'menu'
KIND_MENU_OPTION = 'menu_option'
KIND_EXTEND = 'extend'


@dataclass
class Beat:
    """一个演出单元：说这句话时，舞台上是什么样。"""
    seq: int = 0
    label: str = ''
    condition: str = ''
    kind: str = KIND_SAY
    speaker: str = ''
    text: str = ''
    scene: str = ''
    show: str = ''
    hide: str = ''
    at: str = ''
    with_: str = ''
    transform: str = ''
    music: str = ''
    music_action: str = 'play'     # 'play' / 'queue' / 'stop'
    sound: str = ''
    sound_action: str = 'play'     # 'play' / 'queue' / 'stop'
    voice: str = ''
    notes: str = ''

    def to_row(self) -> list:
        return [
            self.seq, self.label, self.condition, self.kind,
            self.speaker, self.text, self.scene, self.show, self.hide,
            self.at, self.with_, self.transform,
            self.music, self.music_action, self.sound, self.sound_action,
            self.voice, self.notes,
        ]

    @classmethod
    def from_row(cls, row: dict) -> 'Beat':
        def g(key, default=''):
            v = row.get(key, default)
            if v is None:
                return default
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            return str(v).strip()

        seq = row.get('Seq', 0)
        try:
            seq = int(seq) if seq not in (None, '') else 0
        except (TypeError, ValueError):
            seq = 0

        kind = g('Kind') or KIND_SAY
        if kind not in (KIND_SAY, KIND_NARRATION, KIND_MENU,
                        KIND_MENU_OPTION, KIND_EXTEND):
            kind = KIND_SAY

        return cls(
            seq=seq,
            label=g('Label'),
            condition=g('Condition'),
            kind=kind,
            speaker=g('Speaker'),
            text=g('Text'),
            scene=g('Scene'),
            show=g('Show'),
            hide=g('Hide'),
            at=g('At'),
            with_=g('With'),
            transform=g('Transform'),
            music=g('Music'),
            music_action=g('MusicAction') or 'play',
            sound=g('Sound'),
            sound_action=g('SoundAction') or 'play',
            voice=g('Voice'),
            notes=g('Notes'),
        )

    def is_empty(self) -> bool:
        """这一行没有任何内容（用户留空的行不该生成代码）。

        纯 `stop music` / `stop sound` 没有"要播的资产"，但它是明确的
        演出指令（转场 / 进战斗的关键节点），必须算有内容，否则会被丢掉。
        """
        return not any((
            self.text, self.speaker, self.scene, self.show, self.hide,
            self.music, self.sound, self.voice, self.notes,
            self.music_action == 'stop',
            self.sound_action == 'stop',
        ))


# ===================== rpy -> Beat =====================

def _join_show_items(items: list) -> str:
    """把多个 show 目标拼成 `图像@变换; 图像2`。"""
    parts = []
    for image, at in items:
        if at:
            parts.append(f"{image}@{at}")
        else:
            parts.append(image)
    return '; '.join(parts)


def beats_from_statements(statements: Iterable[Statement],
                          label: str = '',
                          labels: Optional[list] = None) -> list:
    """
    把顺序语句流折叠成 Beat 序列。

    规则：舞台指令（scene/show/hide/music/sound/voice）吸附在**紧随其后**
    的第一句台词上。若一段指令后面没有台词（比如结尾淡出），
    会自成一个 Beat，保证不丢。

    labels 与 statements 一一对应（每条语句所属 label）；不传时
    全部使用 label 参数。
    """
    statements = list(statements)
    if labels is None:
        labels = [label] * len(statements)

    beats = []
    pending = Beat(label=label)
    pending_has_stage = False
    current_label = label
    seq = 0

    def flush_stage_only():
        """把只含舞台指令、没有台词的指令段单独落成一行。"""
        nonlocal pending, pending_has_stage, seq
        if pending_has_stage and not pending.text:
            seq += 1
            pending.seq = seq
            beats.append(pending)
        pending = Beat(label=current_label)
        pending_has_stage = False

    for stmt, stmt_label in zip(statements, labels):
        if stmt_label:
            current_label = stmt_label
            pending.label = current_label
        kind = stmt.kind
        payload = stmt.payload or {}

        if kind == StatementKind.SAY:
            speaker = payload.get('speaker', '') or ''
            pending.speaker = speaker
            pending.text = payload.get('text', '') or ''
            pending.kind = KIND_SAY if speaker else KIND_NARRATION
            pending.condition = stmt.condition or pending.condition
            seq += 1
            pending.seq = seq
            beats.append(pending)
            pending = Beat(label=current_label)
            pending_has_stage = False

        elif kind == StatementKind.EXTEND:
            seq += 1
            beats.append(Beat(
                seq=seq, label=current_label,
                condition=stmt.condition or '',
                kind=KIND_EXTEND,
                text=payload.get('text', '') or '',
            ))

        elif kind == StatementKind.MENU:
            caption = payload.get('caption', '') or ''
            if caption:
                seq += 1
                beats.append(Beat(
                    seq=seq, label=current_label,
                    condition=stmt.condition or '',
                    kind=KIND_MENU, text=caption,
                ))

        elif kind == StatementKind.MENU_OPTION:
            seq += 1
            beats.append(Beat(
                seq=seq, label=current_label,
                condition=stmt.condition or '',
                kind=KIND_MENU_OPTION,
                text=payload.get('text', '') or '',
            ))
            flush_stage_only()

        elif kind == StatementKind.SCENE:
            if pending.scene:
                # 同拍已有背景，新背景会覆盖它——先落拍，保留两条指令
                flush_stage_only()
            pending.scene = payload.get('image', '') or ''
            if payload.get('with_'):
                pending.with_ = payload['with_']
            if payload.get('at'):
                pending.at = ' '.join(payload['at'])
            pending.condition = stmt.condition or pending.condition
            pending_has_stage = True

        elif kind == StatementKind.SHOW:
            image = payload.get('image', '') or ''
            at = ' '.join(payload.get('at') or [])
            existing = []
            if pending.show:
                existing = _split_show_items(pending.show)
            existing.append((image, at))
            pending.show = _join_show_items(existing)
            if payload.get('with_'):
                pending.with_ = payload['with_']
            pending.condition = stmt.condition or pending.condition
            pending_has_stage = True

        elif kind == StatementKind.HIDE:
            pending.hide = payload.get('image', '') or ''
            pending_has_stage = True

        elif kind == StatementKind.PLAY:
            channel = payload.get('channel', '')
            name = payload.get('name', '') or ''
            action = payload.get('action', 'play')
            if channel == 'music':
                if pending.music or pending.music_action == 'stop':
                    # 同通道已有 play/stop，新指令会覆盖它——先落拍，
                    # 否则 play sound 紧跟 stop sound 时中间的 play 会丢
                    flush_stage_only()
                pending.music = name               # stop 时 name 为空
                pending.music_action = action      # 'stop' 如实存
                pending_has_stage = True
            else:
                if pending.sound or pending.sound_action == 'stop':
                    flush_stage_only()
                pending.sound = name
                pending.sound_action = action
                pending_has_stage = True

        elif kind == StatementKind.VOICE:
            if pending.voice:
                flush_stage_only()
            pending.voice = payload.get('name', '') or ''
            pending_has_stage = True

        elif kind == StatementKind.PYTHON:
            # 变量操作不进表格，但记到 Notes 提醒编辑者这里还有逻辑
            code = (payload.get('code', '') or '').strip()
            if code:
                if pending.notes:
                    pending.notes += f' | $ {code}'
                else:
                    pending.notes = f'$ {code}'
                pending_has_stage = True

        elif kind in (StatementKind.JUMP, StatementKind.CALL):
            flush_stage_only()
            target = payload.get('target', '')
            seq += 1
            beats.append(Beat(
                seq=seq, label=current_label,
                condition=stmt.condition or '',
                kind=KIND_NARRATION,
                notes=f'{kind} {target}',
            ))

        elif kind == StatementKind.RETURN:
            flush_stage_only()

    flush_stage_only()
    return beats


def _split_show_items(text: str) -> list:
    """`bg@left; eileen` -> [('bg', 'left'), ('eileen', '')]"""
    items = []
    for part in (text or '').split(';'):
        part = part.strip()
        if not part:
            continue
        if '@' in part:
            image, _, at = part.partition('@')
            items.append((image.strip(), at.strip()))
        else:
            items.append((part, ''))
    return items


def beats_from_parse_result(parse_result, label: str = '') -> list:
    """入口：ParseResult → Beat 列表。"""
    return beats_from_statements(
        parse_result.statements,
        label=label,
        labels=getattr(parse_result, 'statement_labels', None),
    )


# ===================== Beat -> Excel =====================

_HEADER_FILL = PatternFill('solid', fgColor='DDEBF7')
_HEADER_FONT = Font(bold=True)


def export_beats_to_excel(beats: list, output_path,
                          registry=None, include_dropdowns: bool = True):
    """
    把 Beat 列表写成 Excel。

    registry 非空时会额外生成 角色/图片/变换/音乐/音效 五张候选表，
    并给主表相应列挂上下拉验证——文案只需要点选，不会打错 ID。
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Beats'

    ws.append(BEAT_FIELDS)
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical='center')

    for beat in beats:
        ws.append(beat.to_row())

    widths = {'Seq': 6, 'Label': 18, 'Condition': 18, 'Kind': 12,
              'Speaker': 14, 'Text': 52, 'Scene': 18, 'Show': 30,
              'Hide': 14, 'At': 14, 'With': 12, 'Transform': 14,
              'Music': 16, 'MusicAction': 12, 'Sound': 16,
              'SoundAction': 12, 'Voice': 16, 'Notes': 26}
    for i, name in enumerate(BEAT_FIELDS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(name, 16)
    ws.freeze_panes = 'A2'

    if registry is not None and include_dropdowns:
        ranges = _write_candidate_sheets(wb, registry)
        _attach_dropdowns(ws, ranges, len(beats))

    wb.save(output_path)
    return output_path


def _write_candidate_sheets(wb, registry) -> dict:
    """
    写出候选表，返回 {资产类型: "Sheet!$A$2:$A$N"} 形式的引用区间。
    下拉公式里引用的是另一个 sheet，所以 sheet 名要防注入（去方括号等）。
    """
    mapping = [
        ('character', 'Candidates_角色',
         sorted((registry.characters or {}).keys())),
        ('image', 'Candidates_图片',
         sorted((registry.images or {}).keys())),
        ('transform', 'Candidates_变换',
         sorted((registry.transforms or {}).keys())),
        ('music', 'Candidates_音乐',
         sorted((registry.music or {}).keys())),
        ('sound', 'Candidates_音效',
         sorted((registry.sounds or {}).keys())),
        ('voice', 'Candidates_语音',
         sorted((registry.voices or {}).keys())),
    ]

    ranges = {}
    for asset_type, sheet_name, ids in mapping:
        ws = wb.create_sheet(sheet_name)
        ws.append(['ID'] + [''])
        ws.cell(row=1, column=1, value='ID')
        for i, aid in enumerate(ids, start=2):
            ws.cell(row=i, column=1, value=aid)
        ws.column_dimensions['A'].width = 28
        if ids:
            ranges[asset_type] = f"'{sheet_name}'!$A$2:$A${len(ids) + 1}"
    return ranges


def _attach_dropdowns(ws, ranges: dict, row_count: int):
    """给指定列挂上下拉验证。空候选表不挂（Excel 会报无效引用）。"""
    if row_count < 1:
        return
    last_row = row_count + 1
    for idx, col_name in enumerate(BEAT_FIELDS, start=1):
        asset_type = DROPDOWN_COLUMNS.get(col_name)
        if not asset_type or asset_type not in ranges:
            continue
        dv = DataValidation(
            type='list',
            formula1=ranges[asset_type],
            allow_blank=True,
            showDropDown=False,      # False 才会在 Excel 里显示箭头
        )
        dv.error = '请从下拉列表中选择已登记的资产'
        dv.errorTitle = '未登记的资产'
        dv.prompt = '从资产表中选择'
        dv.promptTitle = col_name
        ws.add_data_validation(dv)
        letter = get_column_letter(idx)
        dv.add(f"{letter}2:{letter}{max(last_row, 200)}")

    # 动作列：内联下拉（play/queue/stop），不依赖资源表
    for col_name, values in ACTION_COLUMNS.items():
        if col_name not in BEAT_FIELDS:
            continue
        idx = BEAT_FIELDS.index(col_name) + 1
        letter = get_column_letter(idx)
        dv = DataValidation(
            type='list',
            formula1='"' + ','.join(values) + '"',
            allow_blank=True,
            showDropDown=False,      # False 才会在 Excel 里显示箭头
        )
        dv.prompt = '播放方式：play / queue / stop'
        dv.promptTitle = col_name
        ws.add_data_validation(dv)
        dv.add(f"{letter}2:{letter}{max(last_row, 200)}")


# ===================== Excel -> Beat =====================

def import_beats_from_excel(path) -> list:
    """读回 Beats 表。空行跳过（Excel 里常见末尾空行）。"""
    path = Path(path)
    if not path.exists():
        return []
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        ws = wb['Beats'] if 'Beats' in wb.sheetnames else wb.active
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
        return beats
    finally:
        wb.close()


# ===================== Beat -> rpy =====================

def _needs_quote(name: str) -> bool:
    return bool(name) and not re.match(r'^[A-Za-z_][\w.]*$', name)


def render_beat_to_rpy(beat: Beat, indent: str = '    ') -> list:
    """
    一个 Beat 渲染成 Ren'Py 语句。返回不带换行符的字符串列表。

    生成顺序遵循 Ren'Py 的呈现习惯：
      舞台（scene/show/hide）→ 音频 → 台词
    条件（Condition）包成 if 块。
    """
    lines = []

    if beat.scene:
        line = f"scene {_normalize_image_name(beat.scene)}"
        if beat.at:
            line += f" at {beat.at}"
        if beat.with_:
            line += f" with {beat.with_}"
        lines.append(line)

    for image, at in _split_show_items(beat.show):
        line = f"show {_normalize_image_name(image)}"
        if at:
            line += f" at {at}"
        if beat.with_:
            line += f" with {beat.with_}"
        lines.append(line)

    if beat.hide:
        lines.append(f"hide {_normalize_image_name(beat.hide)}")

    if beat.transform and not beat.at:
        # 单独填了 Transform 又没填 At：当成这一拍的转场
        lines.append(f"with {beat.transform}")

    if beat.music or beat.music_action == 'stop':
        if beat.music_action == 'stop':
            lines.append('stop music')
        elif beat.music_action == 'queue':
            lines.append(f"queue music {beat.music}")
        else:
            lines.append(f"play music {beat.music}")
    if beat.sound or beat.sound_action == 'stop':
        if beat.sound_action == 'stop':
            lines.append('stop sound')
        elif beat.sound_action == 'queue':
            lines.append(f"queue sound {beat.sound}")
        else:
            lines.append(f"play sound {beat.sound}")
    if beat.voice:
        lines.append(f"voice {beat.voice}")

    if beat.kind == KIND_MENU_OPTION:
        lines.append(f'"{beat.text}"')
    elif beat.kind == KIND_EXTEND:
        lines.append(f"extend {_quote(beat.text)}")
    elif beat.text:
        if beat.speaker:
            lines.append(f"{beat.speaker} {_quote(beat.text)}")
        else:
            lines.append(_quote(beat.text))
    elif beat.kind == KIND_MENU and beat.text:
        lines.append(_quote(beat.text))

    if not lines:
        return []

    if beat.condition:
        return [f"if {beat.condition}:"] + [indent + ln for ln in lines]
    return lines


def _normalize_image_name(name: str) -> str:
    """
    渲染时把图像名里的空格换成下划线，与资产表的 ID 对齐。

    Ren'Py 的 `show bg 000` 与 `show bg_000` 等价（引擎内部就是这个规则），
    但资产表里存的是文件名 `bg_000`。统一成下划线后，脚本与表可以
    直接对得上，引用分析也不用再猜。
    """
    name = (name or '').strip()
    if not name or '"' in name or "'" in name:
        return name
    return name.replace(' ', '_')


def _quote(text: str) -> str:
    """Ren'Py 字符串字面量。含双引号时用单引号包。"""
    if text is None:
        return '""'
    escaped = str(text).replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def render_beats_to_rpy(beats: list, label_name: Optional[str] = None,
                        indent: str = '    ') -> str:
    """
    Beat 列表 → 完整 .rpy 文本。

    label_name 为空时按 Beat.label 分段生成多个 label；
    指定了则全部塞进同一个 label（便于替换单个章节）。
    """
    out = ['# 由剧本表自动生成，请勿在此文件手工改动结构']
    out.append('')

    groups = []
    if label_name:
        groups.append((label_name, list(beats)))
    else:
        current = None
        bucket = []
        for b in beats:
            name = b.label or 'start'
            if current is None:
                current = name
            if name != current:
                groups.append((current, bucket))
                current, bucket = name, []
            bucket.append(b)
        if bucket:
            groups.append((current, bucket))
        if not groups:
            groups.append(('start', []))

    for name, group in groups:
        out.append(f"label {name}:")
        body = []
        for beat in group:
            body.extend(render_beat_to_rpy(beat, indent=indent))
        if body:
            out.extend(indent + ln for ln in body)
        else:
            out.append(indent + 'pass')
        out.append(indent + 'return')
        out.append('')

    return '\n'.join(out).rstrip() + '\n'


def write_beats_to_rpy(beats: list, output_path,
                       label_name: Optional[str] = None) -> str:
    """渲染并落盘，返回生成的文件路径。"""
    text = render_beats_to_rpy(beats, label_name=label_name)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding='utf-8')
    return str(output_path)


# ===================== 一体化入口 =====================

def open_rpy_as_table(script_file, output_path=None, registry=None,
                      label: str = ''):
    """
    把一个 .rpy 模块解析成演出表：这是"用 Excel 打开 rpy"的正向路径。
    """
    from renpy_parser import RenPyParser, ParseResultBuilder

    script_file = Path(script_file)
    if output_path is None:
        output_path = script_file.with_suffix('.xlsx')

    nodes = RenPyParser().parse(script_file)
    result = ParseResultBuilder().build(nodes)
    beats = beats_from_parse_result(result, label=label or script_file.stem)
    export_beats_to_excel(beats, output_path, registry=registry)
    return output_path


def export_table_to_rpy(table_path, output_path=None,
                        label_name: Optional[str] = None):
    """
    把演出表写回 .rpy：这是反向路径。
    """
    table_path = Path(table_path)
    if output_path is None:
        output_path = table_path.with_suffix('.rpy')

    beats = import_beats_from_excel(table_path)
    write_beats_to_rpy(beats, output_path, label_name=label_name)
    return output_path
