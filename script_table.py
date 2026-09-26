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
from asset_registry import RENPY_BUILTIN_TRANSFORMS


# ===================== 数据结构 =====================

BEAT_FIELDS = [
    'LineId', 'Seq', 'Label', 'Condition', 'Comment', 'Kind', 'Speaker',
    'Text',
    'Scene', 'Show', 'Hide', 'At', 'With', 'Transform', 'StageOrder',
    'Raw', 'CodeBlock',
    'Music', 'MusicParams', 'MusicAction',
    'Sound', 'SoundParams', 'SoundAction', 'Voice', 'Notes',
    'ContentHash',
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
    """一个演出单元：说这句话时，舞台上是什么样。

    除了结构化的舞台/音频列，还保留三类"原样保真"列，保证
    rpy↔表格 往返不丢程序员的代码与文案的标注：
      · comment   文案/美术的整行注释（`# 表情A6`）
      · raw       未建模但属于演出流的语句（`with dissolve` / `pause`）
      · code      `$ ...` / python 逻辑，导出时原样回写
    """
    seq: int = 0
    label: str = ''
    # 稳定身份 + 内容指纹：协作（DB 主键/锁）与翻译单元要用，
    # 详细理由见 _assign_identity。
    line_id: str = ''
    content_hash: str = ''
    condition: str = ''
    comment: str = ''
    kind: str = KIND_SAY
    speaker: str = ''
    text: str = ''
    scene: str = ''
    show: str = ''
    hide: str = ''
    at: str = ''
    with_: str = ''
    transform: str = ''
    stage_order: str = ''          # 舞台指令出现顺序，如 'hide,show'（保真用）
    raw: str = ''
    code: str = ''
    music: str = ''
    music_params: str = ''
    music_action: str = 'play'     # 'play' / 'queue' / 'stop'
    sound: str = ''
    sound_params: str = ''
    sound_action: str = 'play'     # 'play' / 'queue' / 'stop'
    voice: str = ''
    notes: str = ''

    def to_row(self) -> list:
        return [
            self.line_id, self.seq, self.label, self.condition, self.comment,
            self.kind,
            self.speaker, self.text, self.scene, self.show, self.hide,
            self.at, self.with_, self.transform, self.stage_order,
            self.raw, self.code,
            self.music, self.music_params, self.music_action,
            self.sound, self.sound_params, self.sound_action,
            self.voice, self.notes,
            self.content_hash,
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
            line_id=g('LineId'),
            content_hash=g('ContentHash'),
            condition=g('Condition'),
            comment=g('Comment'),
            kind=kind,
            speaker=g('Speaker'),
            text=g('Text'),
            scene=g('Scene'),
            show=g('Show'),
            hide=g('Hide'),
            at=g('At'),
            with_=g('With'),
            transform=g('Transform'),
            stage_order=g('StageOrder'),
            raw=g('Raw'),
            code=g('CodeBlock'),
            music=g('Music'),
            music_params=g('MusicParams'),
            music_action=g('MusicAction') or 'play',
            sound=g('Sound'),
            sound_params=g('SoundParams'),
            sound_action=g('SoundAction') or 'play',
            voice=g('Voice'),
            notes=g('Notes'),
        )

    def is_empty(self) -> bool:
        """这一行没有任何内容（用户留空的行不该生成代码）。

        纯 `stop music` / `stop sound` 没有"要播的资产"，但它是明确的
        演出指令（转场 / 进战斗的关键节点），必须算有内容，否则会被丢掉。
        注释 / 原始语句 / 代码块同理——它们都是要回写的演出内容。
        """
        return not any((
            self.text, self.speaker, self.scene, self.show, self.hide,
            self.music, self.sound, self.voice, self.notes,
            self.comment, self.raw, self.code,
            self.music_action == 'stop',
            self.sound_action == 'stop',
        ))


# ===================== rpy -> Beat =====================

# show 子句里用来分隔"图像名"与修饰关键字的分界词
_SHOW_ARG_KEYWORDS = frozenset(
    {'at', 'as', 'with', 'behind', 'onlayer', 'zorder'})


def _clause_image(clause: str) -> str:
    """从一个 show 子句（已去掉前导 'show '）里取出图像名。"""
    parts = []
    for w in clause.split():
        if w in _SHOW_ARG_KEYWORDS:
            break
        parts.append(w.rstrip(','))
    return ' '.join(parts)


def _fmt_seconds(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:.1f}" if f == int(f) else f"{f:g}"


def _audio_params(payload: dict) -> str:
    """把音频参数（loop/fadein/fadeout）还原成可回写的后缀文本。"""
    parts = []
    if payload.get('loop'):
        parts.append('loop')
    fi = payload.get('fadein') or 0
    fo = payload.get('fadeout') or 0
    if fi:
        parts.append(f"fadein {_fmt_seconds(fi)}")
    if fo:
        parts.append(f"fadeout {_fmt_seconds(fo)}")
    return ' '.join(parts)


def _show_clause_args(payload: dict) -> str:
    """
    把 show 语句 payload 还原成 `show ` 之后的完整参数。

    只由 image+at 组成的简单 show 不走这里（用紧凑的 `图像@变换`），
    只有带 as/behind/onlayer/zorder/with/expression 的复杂 show 才
    序列化成完整子句——既保住 Excel 的可读性，又不丢修饰符。
    """
    if payload.get('is_expression'):
        return f"expression {payload.get('image', '')}"
    parts = [payload.get('image', '')]
    if payload.get('as_'):
        parts += ['as', payload['as_']]
    at = payload.get('at') or []
    if at:
        parts += ['at'] + list(at)
    behind = payload.get('behind') or []
    if behind:
        parts += ['behind', ', '.join(behind)]
    if payload.get('onlayer'):
        parts += ['onlayer', payload['onlayer']]
    if payload.get('zorder') is not None:
        parts += ['zorder', str(payload['zorder'])]
    if payload.get('with_'):
        parts += ['with', payload['with_']]
    return ' '.join(p for p in parts if p != '')


def _show_item_text(payload: dict) -> str:
    """单个 show 语句 -> Show 单元格里的一项。"""
    image = payload.get('image', '') or ''
    at = payload.get('at') or []
    simple = not any((
        payload.get('as_'), payload.get('with_'), payload.get('onlayer'),
        payload.get('behind'), payload.get('is_expression'),
        payload.get('zorder') is not None,
    ))
    if simple:
        return f"{image}@{' '.join(at)}" if at else image
    return _show_clause_args(payload)


def _join_show_items(items: list) -> str:
    """把多个 show 项拼成 `图像@变换; 图像2; cg x at y behind z`。"""
    parts = []
    for it in items:
        if it.get('clause'):
            parts.append(it['clause'])
        elif it.get('at'):
            parts.append(f"{it['image']}@{it['at']}")
        else:
            parts.append(it['image'])
    return '; '.join(parts)


def _split_show_items(text: str) -> list:
    """
    `bg@left; eileen happy; cg cg02 at t behind ye`
      -> [{'clause': '', 'image': 'bg', 'at': 'left'},
          {'clause': '', 'image': 'eileen happy', 'at': ''},
          {'clause': 'cg cg02 at t behind ye', 'image': 'cg cg02', 'at': ''}]

    逗号后面也可能跟 at 之外的写法，这里只认 `图像@变换`（紧凑式）与
    "含修饰关键字的完整子句"两种；其余当纯图像名。
    """
    items = []
    for part in (text or '').split(';'):
        part = part.strip()
        if not part:
            continue
        if '@' in part:
            image, _, at = part.partition('@')
            items.append({'clause': '', 'image': image.strip(),
                          'at': at.strip()})
        elif any(w in _SHOW_ARG_KEYWORDS for w in part.split()):
            items.append({'clause': part, 'image': _clause_image(part),
                          'at': ''})
        else:
            items.append({'clause': '', 'image': part, 'at': ''})
    return items


def _split_hide_items(text: str) -> list:
    """`ye; lan@dissolve` -> [{'image': 'ye', 'with': ''},
                             {'image': 'lan', 'with': 'dissolve'}]"""
    items = []
    for part in (text or '').split(';'):
        part = part.strip()
        if not part:
            continue
        if '@' in part:
            image, _, w = part.partition('@')
            items.append({'image': image.strip(), 'with': w.strip()})
        else:
            items.append({'image': part, 'with': ''})
    return items


def _join_hide_items(items: list) -> str:
    parts = []
    for it in items:
        if it.get('with'):
            parts.append(f"{it['image']}@{it['with']}")
        else:
            parts.append(it['image'])
    return '; '.join(parts)


def _append_line(existing: str, line: str) -> str:
    return (existing + '\n' + line) if existing else line


def beats_from_statements(statements: Iterable[Statement],
                          label: str = '',
                          labels: Optional[list] = None) -> list:
    """
    把顺序语句流折叠成 Beat 序列。

    规则：舞台指令（scene/show/hide/music/sound/voice）吸附在**紧随其后**
    的第一句台词上。若一段指令后面没有台词（比如结尾淡出），
    会自成一个 Beat，保证不丢。

    注释 / 原始语句（with、pause…）/ python 代码同样吸附到下一拍，
    分别进 Comment / Raw / CodeBlock 列，导出时原样回写。

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

    def mark_stage(verb):
        """记录舞台指令在本拍中的出现顺序，供导出时保序。"""
        order = pending.stage_order.split(',') if pending.stage_order else []
        if verb not in order:
            order.append(verb)
        pending.stage_order = ','.join(order)

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
            mark_stage('scene')

        elif kind == StatementKind.SHOW:
            items = _split_show_items(pending.show)
            items.append({'clause': _show_item_text(payload),
                          'image': payload.get('image', '') or '',
                          'at': ''})
            pending.show = _join_show_items(items)
            if payload.get('with_') and not payload.get('at'):
                # 简单 show 的 with 提到拍级；复杂 show 的 with 已在子句里
                pending.with_ = payload['with_']
            pending.condition = stmt.condition or pending.condition
            pending_has_stage = True
            mark_stage('show')

        elif kind == StatementKind.HIDE:
            items = _split_hide_items(pending.hide)
            items.append({'image': payload.get('image', '') or '',
                          'with': payload.get('with_', '') or ''})
            pending.hide = _join_hide_items(items)
            pending_has_stage = True
            mark_stage('hide')

        elif kind == StatementKind.PLAY:
            channel = payload.get('channel', '')
            name = payload.get('name', '') or ''
            action = payload.get('action', 'play')
            params = _audio_params(payload)
            if channel == 'music':
                if pending.music or pending.music_action == 'stop':
                    # 同通道已有 play/stop，新指令会覆盖它——先落拍，
                    # 否则 play sound 紧跟 stop sound 时中间的 play 会丢
                    flush_stage_only()
                pending.music = name               # stop 时 name 为空
                pending.music_action = action      # 'stop' 如实存
                pending.music_params = params
                pending_has_stage = True
            else:
                if pending.sound or pending.sound_action == 'stop':
                    flush_stage_only()
                pending.sound = name
                pending.sound_action = action
                pending.sound_params = params
                pending_has_stage = True

        elif kind == StatementKind.VOICE:
            if pending.voice:
                flush_stage_only()
            pending.voice = payload.get('name', '') or ''
            pending_has_stage = True

        elif kind == StatementKind.PYTHON:
            # $ / python 逻辑进 CodeBlock，导出时原样回写，不再丢。
            code = (payload.get('code', '') or '').strip()
            if code:
                pending.code = _append_line(pending.code, code)
                pending_has_stage = True

        elif kind == StatementKind.COMMENT:
            text = (payload.get('text', '') or '').strip()
            if text:
                pending.comment = _append_line(pending.comment, text)
                pending_has_stage = True

        elif kind == StatementKind.RAW:
            text = (payload.get('text', '') or '').strip()
            if text:
                pending.raw = _append_line(pending.raw, text)
                pending_has_stage = True

        elif kind in (StatementKind.JUMP, StatementKind.CALL):
            # 跳转/调用是控制流，进 Raw 原样保留，绝不丢。
            flush_stage_only()
            target = payload.get('target', '')
            seq += 1
            beats.append(Beat(
                seq=seq, label=current_label,
                condition=stmt.condition or '',
                kind=KIND_NARRATION,
                raw=f"{kind} {target}",
            ))

        elif kind == StatementKind.RETURN:
            flush_stage_only()

    flush_stage_only()
    _assign_identity(beats)
    return beats


def _assign_identity(beats: list):
    """
    给每条 Beat 补上稳定身份：LineId + ContentHash。

    为什么要身份：
      · 团队协作（数据库后端）需要主键——按"第几行"当主键，别人插一行
        你的锁就串了；按内容哈希当主键，改一个错别字就换身份，锁和
        状态全丢。所以用 `label.序号` 这种**位置稳定**的 ID，
        再配一个 ContentHash 记录"当时内容是什么"，用来判冲突。
      · 多语言/翻译单元、存档埋点、QA 反馈都要能指向具体某一句话。

    LineId 已有则不覆盖（保留 Excel/DB 传来的身份）；
    ContentHash **一律按当前内容重算**——它的语义是"这条现在长什么样"，
    用表里带过来的旧值会导致"改了内容却判成没变"，push 静默丢改动。
    """
    for b in beats:
        if not b.line_id:
            label = b.label or 'start'
            b.line_id = f"{label}.{b.seq:04d}"
        b.content_hash = _beat_content_hash(b)


def _beat_content_hash(beat: 'Beat') -> str:
    """Beat 语义内容的短哈希（只参与冲突检测，不参与相等判断）。"""
    import hashlib
    parts = [
        beat.kind, beat.speaker, beat.text, beat.scene, beat.show, beat.hide,
        beat.at, beat.with_, beat.transform, beat.stage_order, beat.raw,
        beat.code, beat.music, beat.music_params, beat.music_action,
        beat.sound, beat.sound_params, beat.sound_action, beat.voice,
        beat.condition, beat.comment,
    ]
    blob = '\x1f'.join('' if p is None else str(p) for p in parts)
    return hashlib.md5(blob.encode('utf-8')).hexdigest()[:10]


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

    registry 非空时会额外生成 角色/图片/变换/音乐/音效/语音 六张候选表，
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

    widths = {'LineId': 14, 'Seq': 6, 'Label': 18, 'Condition': 18,
              'Comment': 30,
              'Kind': 12, 'Speaker': 14, 'Text': 52, 'Scene': 18,
              'Show': 30, 'Hide': 16, 'At': 14, 'With': 12,
              'Transform': 14, 'Raw': 30, 'CodeBlock': 30,
              'Music': 16, 'MusicParams': 16, 'MusicAction': 12,
              'Sound': 16, 'SoundParams': 16, 'SoundAction': 12,
              'Voice': 16, 'Notes': 26, 'ContentHash': 12}
    for i, name in enumerate(BEAT_FIELDS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(name, 16)
    ws.freeze_panes = 'D2'      # 冻结到 Label 列，长表横向滚动时还能看见归属

    if registry is not None and include_dropdowns:
        ranges = _write_candidate_sheets(wb, registry)
        _attach_dropdowns(ws, ranges, len(beats),
                          names=_defined_names_for(ranges))

    # 舞台快照：把"每一拍舞台上是什么样"算出来另写一张表。
    # 文案不用再去脑补 scene/show/hide 叠加的结果（二）。
    _write_stage_snapshot_sheet(wb, beats)

    wb.save(output_path)
    return output_path


def _write_stage_snapshot_sheet(wb, beats):
    """写「舞台快照」派生表（只读视图，可随时重算）。"""
    from stage_snapshot import snapshot_rows

    ws = wb.create_sheet('舞台快照')
    headers = ['LineId', 'Seq', 'Label', '说话人', '台词',
               '背景', '立绘（谁·在哪）', '音乐', '音效', '语音', '备注']
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
    for r in snapshot_rows(beats):
        ws.append([r['line_id'], r['seq'], r['label'], r['speaker'],
                   r['text'], r['scene'], r['actors'], r['music'],
                   r['sound'], r['voice'], r['note']])
    for col, w in zip('ABCDEFGHIJK',
                      (14, 6, 18, 14, 40, 18, 34, 16, 16, 16, 24)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'A2'
    return ws


# 候选表顶部说明：让用户知道这是资源表的镜像，源头在资源表
_CANDIDATE_NOTE = ('↓ 本表是资源表的镜像（images/music/sounds/voices/'
                   'transforms/characters）。'
                   'A 列是可选值，B 列是说明；新增资产请先在资源表登记，'
                   '再到编辑器点【刷新候选】。')


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
    return (f"=OFFSET({quoted}!$A$3,0,0,"
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
    from asset_registry import describe_asset

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
        ws.cell(row=1, column=1, value=_CANDIDATE_NOTE)
        ws.cell(row=2, column=1, value='ID')
        ws.cell(row=2, column=2, value='说明')
        ws.cell(row=2, column=3, value='参数/路径')

        ids = sorted(data.keys())
        for i, aid in enumerate(ids, start=3):
            ws.cell(row=i, column=1, value=aid)
            row = data.get(aid) or {}
            ws.cell(row=i, column=2, value=describe_asset(asset_type, row))
            if asset_type == 'transform':
                extra = row.get('Params') or ''
            else:
                # 音乐/音效/语音/图片都把真实文件路径带出来：脚本里最终要
                # 写的是能被 Ren'Py 解析的引用，光有内部 ID 不够。
                extra = row.get('Path') or ''
            if extra:
                ws.cell(row=i, column=3, value=extra)

        ws.column_dimensions['A'].width = 28
        ws.column_dimensions['B'].width = 48
        ws.column_dimensions['C'].width = 30
        if ids:
            ranges[asset_type] = f"'{sheet_name}'!$A$3:$A${len(ids) + 2}"

    # 登记动态名称：以后在候选表里追加资产行，下拉自动跟着扩
    _add_dynamic_names(wb, ranges)
    return ranges


def refresh_candidate_sheets(table_path, registry) -> Path:
    """
    仅重写候选表（Candidates_*），不动 Beats 表里的任何用户数据。

    用于"资源表新增了资产 → 演出表下拉看不见"的场景：点一下刷新，
    下拉就跟上，而不必重新导出 .rpy、丢掉文案改好的台词/立绘。
    """
    table_path = Path(table_path)
    wb = openpyxl.load_workbook(table_path)
    for ws in list(wb.worksheets):
        if ws.title.startswith('Candidates_'):
            del wb[ws.title]

    ranges = _write_candidate_sheets(wb, registry)

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
    """
    if row_count < 1:
        return
    names = names or {}
    last_row = row_count + 1
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
        dv = DataValidation(
            type='list',
            formula1=formula1,
            allow_blank=True,
            showDropDown=False,      # False 才会在 Excel 里显示箭头
        )
        dv.error = '请从下拉列表中选择已登记的资产'
        dv.errorTitle = '未登记的资产'
        dv.prompt = '从资产表中选择（候选表新增的资产会自动出现）'
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
        beats = _read_beats_from_ws(ws)
        # 手工新加的行没有 LineId/ContentHash → 这里补上，
        # 让"Excel 前端 + 数据库后端"拿到的主键不空（八）
        _assign_identity(beats)
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
    from asset_registry import resolve_references

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


# ===================== Beat -> rpy =====================

def _needs_quote(name: str) -> bool:
    return bool(name) and not re.match(r'^[A-Za-z_][\w.]*$', name)


def _audio_ref(name: str) -> str:
    """
    音频/语音引用：简单标识符原样输出，含路径分隔符或非 ASCII 的
    必须加引号——否则 `play music bgm/夜晚.mp3` 是非法 Ren'Py。
    """
    name = (name or '').strip()
    return _quote(name) if _needs_quote(name) else name


def _split_lines(text: str) -> list:
    """多行单元格（Comment/Raw/CodeBlock）按行拆开，去空行。"""
    return [ln.rstrip() for ln in (text or '').split('\n') if ln.strip()]


def _emit_image_name(name: str, resolver=None) -> str:
    """
    决定生成脚本时图像名用哪种写法。

    历史行为是"空格一律换成下划线"（`show bg room` → `show bg_room`），
    因为资产表 ID 是文件名形态。但这个改写有个致命前提：项目里必须有
    `bg_room` 这个名字。对用 Ren'Py 原生 `tag + attribute` 命名
    （`images/eileen/happy.png` → `show eileen happy`）的项目，
    改成 `eileen_happy` 反而把能跑的脚本改坏了。

    所以：给了 resolver（有资源表）时，**优先原样保留作者写的形态**——
    只要它能在资源表里解析出来；解析不了才退回下划线形态（那是文案
    从下拉里选的 ID 形态，本来就带下划线）。没给 resolver 时保持旧行为，
    兼容单测与"无资源表"场景。
    """
    name = (name or '').strip()
    if not name or '"' in name or "'" in name:
        return name
    underscored = name.replace(' ', '_')
    if resolver is None:
        return underscored
    try:
        if name == underscored or resolver(name):
            return name
        if resolver(underscored):
            return underscored
    except Exception:
        return underscored
    return name


def render_beat_to_rpy(beat: Beat, indent: str = '    ',
                       name_resolver=None) -> list:
    """
    一个 Beat 渲染成 Ren'Py 语句。返回不带换行符的字符串列表。

    生成顺序遵循 Ren'Py 的呈现习惯：
      注释 → 舞台（scene/show/hide）→ with → raw → 代码块 → 音频 → 台词
    条件（Condition）包成 if 块（注释留在 if 之外，行首对齐）。

    name_resolver: 可选，`name -> bool`，判断图像名能否在资源表里解析。
      传了它就能保住作者原本的 `tag attribute` 写法（见 _emit_image_name）。
    """
    # 注释：原样回写（保留 '# '），在 if 之外。
    pre = []
    for c in _split_lines(beat.comment):
        pre.append(c if c.startswith('#') else '# ' + c)

    lines = []

    # —— 舞台指令按 StageOrder 保序输出 ——
    # 扁平的三列（Scene/Show/Hide）本身没有顺序信息，而
    # `hide ye` + `show ye happy`（重置立绘）与反序语义完全不同：
    # 反序会把刚显示的立绘又藏起来。StageOrder 记录源顺序来解决。
    scene_lines = []
    if beat.scene:
        line = f"scene {_emit_image_name(beat.scene, name_resolver)}"
        if beat.at:
            line += f" at {beat.at}"
        if beat.with_:
            line += f" with {beat.with_}"
        scene_lines.append(line)

    show_lines = []
    for item in _split_show_items(beat.show):
        if item['clause']:
            # 复杂 show（带 behind/as/onlayer/zorder/with/expression）
            # 原样回写，不做下划线归一，避免破坏修饰符
            show_lines.append(f"show {item['clause']}")
            continue
        line = f"show {_emit_image_name(item['image'], name_resolver)}"
        if item['at']:
            line += f" at {item['at']}"
        show_lines.append(line)

    hide_lines = []
    for item in _split_hide_items(beat.hide):
        line = f"hide {_emit_image_name(item['image'], name_resolver)}"
        if item['with']:
            line += f" with {item['with']}"
        hide_lines.append(line)

    stage = {'scene': scene_lines, 'show': show_lines, 'hide': hide_lines}
    order = [v for v in (beat.stage_order.split(',')
                         if beat.stage_order else []) if v in stage]
    for v in ('scene', 'show', 'hide'):
        if v not in order:
            order.append(v)
    for v in order:
        lines.extend(stage[v])

    if beat.transform and not beat.at:
        # 单独填了 Transform 又没填 At：当成这一拍的转场
        lines.append(f"with {beat.transform}")

    # 未建模但属于演出流的语句，原样回写
    for r in _split_lines(beat.raw):
        lines.append(r)

    # $ / python 代码块，原样回写（已带 $ 的不重复加）
    for c in _split_lines(beat.code):
        lines.append(c if c.startswith('$') else f"$ {c}")

    if beat.music or beat.music_action == 'stop':
        mp = f" {beat.music_params}".rstrip() if beat.music_params else ''
        if beat.music_action == 'stop':
            lines.append('stop music')
        elif beat.music_action == 'queue':
            lines.append(f"queue music {_audio_ref(beat.music)}{mp}")
        else:
            lines.append(f"play music {_audio_ref(beat.music)}{mp}")
    if beat.sound or beat.sound_action == 'stop':
        sp = f" {beat.sound_params}".rstrip() if beat.sound_params else ''
        if beat.sound_action == 'stop':
            lines.append('stop sound')
        elif beat.sound_action == 'queue':
            lines.append(f"queue sound {_audio_ref(beat.sound)}{sp}")
        else:
            lines.append(f"play sound {_audio_ref(beat.sound)}{sp}")
    if beat.voice:
        lines.append(f"voice {_audio_ref(beat.voice)}")

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
        return pre

    if beat.condition:
        return pre + [f"if {beat.condition}:"] + [
            indent + ln for ln in lines
        ]
    return pre + lines


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


def collect_used_transforms(beats: list) -> list:
    """
    收集演出表里引用的自定义变换（排除 Ren'Py 内置）。

    来源：At 列、Show 复杂子句里的 `at xxx`、Transform/With 列。
    这些 ID 若在 transforms.xlsx 里有 ATL 定义，导出时就会生成
    `transform xxx:` 代码块（改进文档第四节）。
    """
    used = set()

    def _add(name):
        name = (name or '').strip()
        if name and name not in RENPY_BUILTIN_TRANSFORMS:
            used.add(name)

    for b in beats:
        for t in (b.at or '').split():
            _add(t)
        for item in _split_show_items(b.show):
            if item['clause']:
                m = re.search(r'\bat\s+([\w.]+)', item['clause'])
                if m:
                    _add(m.group(1))
            else:
                for t in (item['at'] or '').split():
                    _add(t)
        _add(b.transform)
        _add(b.with_)
        for item in _split_hide_items(b.hide):
            _add(item['with'])
    return sorted(used)


def render_transform_definitions(registry, used_ids) -> list:
    """
    为用到的自定义变换生成 `transform id(params):` + ATL 体。

    transforms.xlsx 的约定列（可选）：
      · ATL       ATL 定义体（多行文本），如 "xalign 0.0\\nlinear 0.5 xalign 1.0"
      · Params    参数列表，如 "duration=0.5, distance=200"
      · Category  分类（入场/退场/强调…），仅用于分组，不参与生成

    没有 ATL 的 transform 不生成（可能由项目别处定义）。
    """
    if registry is None:
        return []
    transforms = getattr(registry, 'transforms', {}) or {}
    blocks = []
    for tid in used_ids:
        row = transforms.get(tid)
        if not row:
            continue
        atl_lines = [ln.rstrip() for ln in (row.get('ATL') or '').splitlines()]
        while atl_lines and not atl_lines[0].strip():
            atl_lines.pop(0)
        while atl_lines and not atl_lines[-1].strip():
            atl_lines.pop()
        if not atl_lines:
            continue

        # 整体去掉公共缩进再统一缩进 4：ATL 可能是"从脚本里解析出来的
        # （自带 4 空格）"，也可能是"人手填进 Excel 的（没有缩进）"。
        # 直接 strip 会把第一行的缩进吃掉，导致块体第一行顶格、后面缩进——
        # 生成出来的 transform 直接是语法错误。
        indents = [len(ln) - len(ln.lstrip())
                   for ln in atl_lines if ln.strip()]
        base = min(indents) if indents else 0

        # Doc 与 ATL 首个字符串重复时不再重复输出注释
        first_str = ''
        for ln in atl_lines:
            s = ln.strip()
            if not s or s.startswith('#'):
                continue
            if len(s) >= 2 and s[0] == s[-1] and s[0] in '"\'':
                first_str = s[1:-1]
            break

        doc = (row.get('Doc') or '').strip()
        if doc and doc != first_str:
            for ln in doc.splitlines():
                if ln.strip():
                    blocks.append(f"# {ln.strip()}")
        params = (row.get('Params') or '').strip()
        blocks.append(f"transform {tid}({params}):" if params
                      else f"transform {tid}:")
        for ln in atl_lines:
            if ln.strip():
                blocks.append('    ' + ln[base:])
        blocks.append('')
    return blocks


def render_beats_to_rpy(beats: list, label_name: Optional[str] = None,
                        indent: str = '    ', registry=None) -> str:
    """
    Beat 列表 → 完整 .rpy 文本。

    label_name 为空时按 Beat.label 分段生成多个 label；
    指定了则全部塞进同一个 label（便于替换单个章节）。

    registry 非空时，会把演出表用到的自定义变换（transforms.xlsx 里
    带 ATL 的）生成 `transform` 定义前置到文件头，让产物自包含。
    """
    out = ['# 由剧本表自动生成，请勿在此文件手工改动结构']

    if registry is not None:
        used = collect_used_transforms(beats)
        defs = render_transform_definitions(registry, used)
        if defs:
            out.append('')
            out.append('# —— 自定义 transform（来自 transforms.xlsx）——')
            out.extend(defs)

    out.append('')

    # 有资源表时，图像名优先用作者原生写法（见 _emit_image_name）
    resolver = None
    if registry is not None:
        from asset_registry import resolve_references
        resolver = lambda name, _reg=registry: bool(  # noqa: E731
            resolve_references(name, _reg))

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
            body.extend(render_beat_to_rpy(beat, indent=indent,
                                           name_resolver=resolver))
        if body:
            out.extend(indent + ln for ln in body)
        else:
            out.append(indent + 'pass')
        out.append(indent + 'return')
        out.append('')

    return '\n'.join(out).rstrip() + '\n'


def write_beats_to_rpy(beats: list, output_path,
                       label_name: Optional[str] = None,
                       registry=None) -> str:
    """渲染并落盘，返回生成的文件路径。"""
    text = render_beats_to_rpy(beats, label_name=label_name,
                               registry=registry)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding='utf-8')
    return str(output_path)


def registry_from_parse_result(parse_result, base=None):
    """
    从脚本自身的解析结果造一份最小注册表（角色 + 自定义 transform）。

    用途：用户还没「打开项目」（没有资源表）时，"用 Excel 打开此章节"
    依然要能把脚本里已定义的 transform / 角色做成下拉候选——否则
    `At` 列只能手打，正是用户抱怨的"变换选不到"。
    """
    from asset_registry import AssetRegistry

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
                      label: str = ''):
    """
    把一个 .rpy 模块解析成演出表：这是"用 Excel 打开 rpy"的正向路径。

    registry 为空时，用脚本自身解析出的角色/变换造一份最小注册表，
    保证表格仍有候选下拉（不然 At / Transform 列没法选）。
    """
    from renpy_parser import RenPyParser, ParseResultBuilder

    script_file = Path(script_file)
    if output_path is None:
        output_path = script_file.with_suffix('.xlsx')

    nodes = RenPyParser().parse(script_file)
    result = ParseResultBuilder().build(nodes)
    if registry is None:
        registry = registry_from_parse_result(result)
    beats = beats_from_parse_result(result, label=label or script_file.stem)
    export_beats_to_excel(beats, output_path, registry=registry)
    return output_path


def export_table_to_rpy(table_path, output_path=None,
                        label_name: Optional[str] = None, registry=None):
    """
    把演出表写回 .rpy：这是反向路径。

    registry 非空时一并生成演出表引用到的自定义 transform 定义。
    """
    table_path = Path(table_path)
    if output_path is None:
        output_path = table_path.with_suffix('.rpy')

    beats = import_beats_from_excel(table_path)
    write_beats_to_rpy(beats, output_path, label_name=label_name,
                       registry=registry)
    return output_path
