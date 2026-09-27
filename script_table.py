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


# ===================== 写单元格的安全封装 =====================

def _set_text(ws, row, col, value):
    """
    写一个单元格，但**强制按文本存储**。

    否则凡以 ``=`` 开头的字符串（典型如 transform 说明里的
    ``=========== Transform … ===========`` 分隔线、或用户注释里随手写的
    ``=TODO``）都会被 openpyxl / Excel 当成公式，导致 Excel 打开时报
    "已删除的公式" 记录。

    原理：openpyxl 给以 ``=`` 开头的字符串自动打上 ``data_type='f'``
    （公式）；我们写入后再把类型掰回 ``'s'``（字符串），序列化时就以
    ``<v>`` 而不是 ``<f>`` 落盘，Excel 当作普通文本读入。
    """
    cell = ws.cell(row=row, column=col)
    cell.value = value
    if isinstance(value, str) and value.startswith('='):
        cell.data_type = 's'
    return cell


def _append_text_row(ws, values):
    """
    用 `_set_text` 整行写入，避免任意以 ``=`` 开头的用户文本被当公式。

    与 `ws.append` 等价，但每行每个字符串单元格都会经过安全封装。
    """
    row = ws.max_row + 1 if ws.max_row else 1
    for col, value in enumerate(values, start=1):
        _set_text(ws, row, col, value)
    return row




# ===================== 数据结构 =====================

BEAT_FIELDS = [
    'LineId', 'Seq', 'Label', 'Kind', 'Speaker', 'Comment', 'Text',
    'Scene', 'Show', 'At', 'With', 'Hide',
    'BGM', 'BGMParams', 'SFX', 'SFXParams', 'Voice',
    'Stage', 'CodeBlock', 'ContentHash',
]

# 写进 Excel 的表头（中文）。策划只看中文；程序侧键名仍是英文，
# 读取时通过 _HEADER_ALIASES 把中文表头映射回键名，两边都不别扭。
BEAT_HEADERS = {
    'LineId': '行号', 'Seq': '序号', 'Label': '章节', 'Kind': '类型',
    'Speaker': '说话人', 'Comment': '注释', 'Text': '台词',
    'Scene': '背景', 'Show': '立绘', 'At': '位置', 'With': '转场',
    'Hide': '退场',
    'BGM': '音乐', 'BGMParams': '音乐参数', 'SFX': '音效',
    'SFXParams': '音效参数', 'Voice': '语音',
    'Stage': '舞台（高级）', 'CodeBlock': '代码',
    'ContentHash': '内容指纹',
}
_HEADER_ALIASES = {v: k for k, v in BEAT_HEADERS.items()}
# 兼容英文表头（旧表 / 程序自己生成的表）
_HEADER_ALIASES.update({f: f for f in BEAT_FIELDS})

# 需要下拉框的列 -> 资产类型。
# **下拉是"方便"，不是"牢笼"**：Excel 的数据验证只做提示、不拦截
# （showErrorMessage=False），所以 `bg room@bgfit` 这种扩展写法照样能手打。
DROPDOWN_COLUMNS = {
    'Speaker': 'character',
    'Scene': 'image',
    'Show': 'image',
    'Hide': 'image',
    'At': 'transform',
    'With': 'transform',
    'BGM': 'music',
    'SFX': 'sound',
    'Voice': 'voice',
}

# 音频列里的"伪值"：动作写在同一个下拉里，策划不用再记一列动作。
# 用书名号包起来，永远不会和真实资产 ID 撞。
AUDIO_PSEUDO = {
    'BGM': {'（停止音乐）': 'stop', '（排队播放）': 'queue'},
    'SFX': {'（停止音效）': 'stop', '（排队播放）': 'queue'},
}
_AUDIO_PSEUDO_REVERSE = {k: v for k, v in
                         (('stop', '（停止音乐）'), ('queue', '（排队播放）'))}

# 旧版表格的列名：读旧表时自动迁移
LEGACY_STAGE_COLUMNS = ('Show', 'Hide', 'At', 'With', 'Transform',
                        'StageOrder', 'Raw')
LEGACY_AUDIO_COLUMNS = ('Music', 'MusicParams', 'MusicAction',
                        'Sound', 'SoundParams', 'SoundAction')

# 内联下拉（不依赖资源表）：列 -> 允许值
ACTION_COLUMNS = {
    'Kind': ['say', 'narration', 'menu', 'menu_option', 'extend'],
}
# 内联下拉值的含义提示
ACTION_HINTS = {
    'Kind': 'say=角色说话 / narration=旁白 / menu=菜单提示 / '
            'menu_option=菜单选项 / extend=接着上一句',
}

KIND_SAY = 'say'
KIND_NARRATION = 'narration'
KIND_MENU = 'menu'
KIND_MENU_OPTION = 'menu_option'
KIND_EXTEND = 'extend'


@dataclass
class Beat:
    """一个演出单元：说这句话时，舞台上是什么样。

    **Excel 那一面是 20 列结构化列**（见 BEAT_FIELDS），演出相关的是：

        场景 / 立绘 / 位置 / 转场 / 退场      每列一个值，各挂词表下拉
        音乐 / 音效                          各自的「参数」列放 fadein 之类
        舞台（高级）Stage                     一格多指令的 DSL 逃生通道
        语音 / 代码 / 注释 / 台词 / 说话人 …

    已经不再有独立的 Show/Hide/At/With/Transform/Music/Sound 六列
    （那是 26 列时代的历史包袱），但它们仍是 Beat 上的**内部字段**：
    舞台快照、图、校验、多引擎导出都需要"能计算"的形态。

    Stage 与结构化列**互为投影**，方向固定：

        结构化列 ──compose_stage()──▶ Stage 文本
        Stage 文本 ──hydrate_from_dsl()──▶ 结构化字段

    `to_row` 写结构化列 + Stage 两份，`from_row` 反过来 hydrate 回来，
    所以两条通道不会各输出一次（会重复执行转场的那个坑）。

    保真后门只有一个：CodeBlock。里面有东西就**原封不动**抄进脚本，
    不解析、不校验——程序的逃生舱口。
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
    # —— DSL 文本列（策划写这里）——
    stage: str = ''
    bgm: str = ''
    sfx: str = ''
    # —— 由 DSL 解析出来的内部字段（不直接暴露成列）——
    show: str = ''
    hide: str = ''
    at: str = ''                   # 「位置」列：作用于立绘
    scene_at: str = ''             # 背景专属变换（背景列里 `图@变换`）
    with_: str = ''                # 「转场」列
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

    def to_row(self, symbols=None) -> list:
        """
        写进 Excel 的一行。

        stage/bgm/sfx 为空时（例如 Beat 来自 .rpy 解析），现场从结构化
        字段**合成** DSL 文本；给了符号表就尽量合成人话（`[显示] 凌跃捷
        开心 左`），合不成就保留原生写法（`show ling close_happy`）。
        两者都能被编译器原样吃回去，不会丢信息。
        """
        stage = self.stage or compose_stage_extra(self, symbols)
        # 音频列：文本列（beat.bgm / beat.sfx）是真相源，有就优先写它；
        # 没有再从结构化字段合成（.rpy 解析来的 Beat 才有结构化字段）。
        # 否则策划手写在「音乐」列的人话/原生写法会在"导出→导入"往返里丢。
        if self.bgm:
            bgm, bgm_params = self.bgm, ''
        else:
            bgm, bgm_params = compose_audio_cell(self, 'music')
        if self.sfx:
            sfx, sfx_params = self.sfx, ''
        else:
            sfx, sfx_params = compose_audio_cell(self, 'sound')
        return [
            self.line_id, self.seq, self.label, self.kind,
            self.speaker, self.comment, self.text,
            compose_scene_cell(self),
            compose_show_cell(self),
            self.at, self.with_,
            compose_hide_cell(self),
            bgm, bgm_params, sfx, sfx_params, self.voice,
            stage, compose_code(self),
            self.content_hash,
        ]

    @classmethod
    def from_row(cls, row: dict) -> 'Beat':
        # 中文表头 → 英文键名（策划看中文，程序用英文，两边都不别扭）
        row = {_HEADER_ALIASES.get(str(k).strip(), k):
               v for k, v in (row or {}).items()}

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

        # Notes 已经取消：老表里有内容就并进注释，绝不丢字
        comment = g('Comment')
        notes = g('Notes')
        if notes:
            comment = f'{comment}\n{notes}'.strip() if comment else notes

        beat = cls(
            seq=seq,
            label=g('Label'),
            line_id=g('LineId'),
            content_hash=g('ContentHash'),
            comment=comment,
            kind=kind,
            speaker=g('Speaker'),
            text=g('Text'),
            code=g('CodeBlock') or g('Raw'),
            voice=g('Voice'),
        )

        # —— 结构化列 → 内部字段 ——
        # 新版表：背景 / 立绘 / 位置 / 转场 / 退场 / 音乐 / 音效 各自一列，
        # 每列都能下拉。旧版表（Show/Hide/At/…）的列名在 LEGACY_* 里，
        # 这里一并兼容，用户手上的老表升级后照样读得出来。
        beat.scene = _spec_image(g('Scene'))
        beat.scene_at = _spec_at(g('Scene'))
        beat.at = g('At')
        beat.with_ = g('With')
        beat.show = _join_show_items([
            {'clause': '', 'image': img, 'at': at}
            for img, at in _split_spec_list(g('Show'))
        ])
        beat.hide = _join_hide_items([
            {'image': img, 'with': w}
            for img, w in _split_spec_list(g('Hide'), sep='@')
        ])
        # 「舞台（高级）」列：结构化列表达不了的指令（复杂 show 子句、
        # 自带位置的立绘、带转场的退场）都在这里；hydrate 会把它并回
        # 结构化字段，渲染时再靠去重避免重复生成。读表时必须读它，否则
        # 位置变换这类信息在"打开 rpy → 导出"往返里会丢。
        beat.stage = g('Stage')
        if g('StageOrder'):
            beat.stage_order = g('StageOrder')

        # —— 音频列 → 内部字段（含「停止/排队」伪值）——
        _fill_audio_from_cells(beat, 'music', g('BGM'), g('BGMParams'))
        _fill_audio_from_cells(beat, 'sound', g('SFX'), g('SFXParams'))

        # 旧表（26 列）自动迁移
        if any(k in row for k in LEGACY_STAGE_COLUMNS):
            if not beat.show:
                beat.show = g('Show')
            if not beat.hide:
                beat.hide = g('Hide')
            beat.raw = g('Raw')
        if any(k in row for k in LEGACY_AUDIO_COLUMNS):
            if not beat.music and not beat.sound:
                _fill_audio_legacy(beat, g)

        # 「音乐」/「音效」文本列（beat.bgm / beat.sfx）是真相源：从已填好的
        # 结构化字段**合成回** DSL 文本，保证"老表迁移"与"打开 rpy → 导出"
        # 两种来源下文本列都有一致的人话/原生写法，便于策划复核与二次编辑。
        beat.bgm = compose_audio_text(beat, 'music')
        beat.sfx = compose_audio_text(beat, 'sound')
        return beat

    def is_empty(self) -> bool:
        """这一行没有任何内容（用户留空的行不该生成代码）。

        纯 `stop music` / `stop sound` 没有"要播的资产"，但它是明确的
        演出指令（转场 / 进战斗的关键节点），必须算有内容，否则会被丢掉。
        注释 / 代码块同理——它们都是要回写的演出内容。
        """
        return not any((
            self.text, self.speaker, self.scene, self.show, self.hide,
            self.stage, self.bgm, self.sfx,
            self.music, self.sound, self.voice, self.notes,
            self.comment, self.raw, self.code,
            self.music_action == 'stop',
            self.sound_action == 'stop',
        ))


# ===================== 结构化字段 ⇄ DSL 文本 =====================

def compose_stage(beat: 'Beat', symbols=None) -> str:
    """
    结构化舞台字段 → Stage 单元格文本。

    给了符号表就优先合成人话（`[显示] 凌跃捷 开心 左`），但**只在往返
    无损时才这么做**（见 dsl.stage_word_if_lossless）——猜错一个表情，
    比显示程序写法糟糕得多。
    """
    from dsl import Symbols, stage_word_if_lossless
    symbols = symbols if isinstance(symbols, Symbols) else None

    ops = []
    if beat.scene:
        line = f"scene {beat.scene}"
        if beat.at:
            line += f" at {beat.at}"
        if beat.with_:
            line += f" with {beat.with_}"
        ops.append(('scene', line))
    for item in _split_show_items(beat.show):
        if item.get('clause'):
            ops.append(('show', f"show {item['clause']}"))
            continue
        line = f"show {item['image']}"
        if item.get('at'):
            line += f" at {item['at']}"
        ops.append(('show', line))
    for item in _split_hide_items(beat.hide):
        line = f"hide {item['image']}"
        if item.get('with'):
            line += f" with {item['with']}"
        ops.append(('hide', line))
    if beat.transform and not beat.at:
        ops.append(('with', f"with {beat.transform}"))
    # 注意：raw（`with dissolve` / `pause` / `jump x` 这类原生语句）**不进
    # Stage**——它们属于代码块后门（compose_code），两条通道各出一份，
    # 否则同一行语句会被生成两次。

    # 顺序按 StageOrder 还原（hide+show 重置立绘与反序语义相反）
    if beat.stage_order:
        order = [v.strip() for v in beat.stage_order.split(',') if v.strip()]
    else:
        order = []
    if order:
        buckets = {}
        for verb, line in ops:
            key = verb if verb in ('scene', 'show', 'hide') else 'raw'
            buckets.setdefault(key, []).append((verb, line))
        ordered = []
        for key in order:
            ordered.extend(buckets.pop(key, []))
        if 'raw' in buckets:
            ordered.extend(buckets.pop('raw'))
        for key in ('scene', 'show', 'hide'):
            ordered.extend(buckets.pop(key, []))
        ops = ordered

    if symbols is not None:
        out = []
        for verb, line in ops:
            if verb in ('show', 'hide'):
                head = line.split(None, 1)[1] if ' ' in line else ''
                spec, at, with_ = _parse_native_stage_tail(head)
                human = stage_word_if_lossless(symbols, verb, spec, at, with_)
                if human:
                    out.append(human)
                    continue
            out.append(line)
        return '; '.join(out)
    return '; '.join(line for _v, line in ops)


_NATIVE_TAIL_RE = re.compile(
    r'^(?P<spec>.+?)(?:\s+at\s+(?P<at>[\w.]+))?'
    r'(?:\s+with\s+(?P<with>[\w.]+))?$')


def _parse_native_stage_tail(head: str) -> tuple:
    """从 `ling close_happy at left with dissolve` 里拆出 (spec, at, with)。"""
    head = (head or '').strip()
    if not head or any(k in head.split()
                       for k in ('behind', 'as', 'onlayer', 'zorder',
                                 'expression')):
        return '', '', ''
    m = _NATIVE_TAIL_RE.match(head)
    if not m:
        return '', '', ''
    return (m.group('spec') or '').strip(), (m.group('at') or '').strip(), \
        (m.group('with') or '').strip()


def compose_audio_text(beat: 'Beat', channel: str) -> str:
    """结构化音频字段 → 一列 DSL 文本（旧接口，供兼容与测试使用）。"""
    name, params = compose_audio_cell(beat, channel)
    label = '音乐' if channel == 'music' else '音效'
    # 纯动作（停止 / 排队）：结构化字段里 name 为空、action 标记了动作。
    # 必须产出能被 parse_audio 原样认回去的写法，否则停在「音乐」列的
    # 文本会在"导出→导入"里变回普通播放。
    if not name:
        action = beat.music_action if channel == 'music' else beat.sound_action
        if action == 'stop':
            return f'[停止{label}]'
        if action == 'queue':
            return f'[{label}(queue)]'
        return ''
    if name in AUDIO_PSEUDO['BGM'] or name in AUDIO_PSEUDO['SFX']:
        # name 形如 `（停止音乐）` / `（排队播放）`：翻成干净动词
        # （`[停止音乐]` / `[音乐(queue)]`），能被 parse_audio 认回去。
        if name.startswith('（停止'):
            return f'[停止{label}]'
        return f'[{label}(queue)]'
    verb = label
    action = beat.music_action if channel == 'music' else beat.sound_action
    if action == 'queue':
        verb = f'{label}(queue)'
    text = f'[{verb}] {name}'
    if params:
        text += f' {params}'
    return text


# ---------------------------------------------------------------------------
#  结构化列 ⇄ 内部字段（每列都能下拉，所以拆开写；语义都写在这里）
# ---------------------------------------------------------------------------

def _split_spec_list(text: str, sep: str = '@') -> list:
    """
    `ling happy@f11; lan sideeye` → [('ling happy', 'f11'), ('lan sideeye', '')]

    `@` 后面在立绘/背景列里是**位置变换**，在退场列里是**转场**——
    同一个符号，两列各自的直觉含义。
    """
    out = []
    for part in (text or '').replace('\n', ';').split(';'):
        part = part.strip()
        if not part:
            continue
        if sep in part:
            img, _, extra = part.partition(sep)
            out.append((img.strip(), extra.strip()))
        else:
            out.append((part, ''))
    return out


def _spec_image(spec: str) -> str:
    """`bg room@bgfit` → `bg room`"""
    return _split_spec_list(spec)[0][0] if (spec or '').strip() else ''


def _spec_at(spec: str) -> str:
    """`bg room@bgfit` → `bgfit`"""
    items = _split_spec_list(spec)
    return items[0][1] if items else ''


def compose_scene_cell(beat: 'Beat') -> str:
    """
    背景列：`bg room`；背景有自己的位置变换时写 `bg room@bgfit`。

    背景的变换刻意**不跟「位置」列共用**：策划写「位置=左」时想的一定是
    立绘站左边，不是把背景挪走。所以背景要变换就在这一列里用 `@`。
    """
    if not beat.scene:
        return ''
    return f"{beat.scene}@{beat.scene_at}" if beat.scene_at else beat.scene


def compose_show_cell(beat: 'Beat') -> str:
    """
    立绘列：能进这列的都进去，进不去的留给「舞台（高级）」列。

    复杂 show 子句（behind/as/onlayer/zorder/expression）以及**自带位置**
    的立绘没有对应的结构列，硬塞会把修饰符弄丢，所以它们退回 Stage 列。
    """
    parts = []
    for item in _split_show_items(beat.show):
        if item.get('clause'):
            continue
        if item.get('at'):
            continue
        parts.append(item['image'])
    return '; '.join(p for p in parts if p)


def compose_hide_cell(beat: 'Beat') -> str:
    """退场列：`ye`，带转场写 `ye@dissolve`。"""
    parts = []
    for item in _split_hide_items(beat.hide):
        img = (item.get('image') or '').strip()
        if not img:
            continue
        parts.append(f"{img}@{item['with']}" if item.get('with') else img)
    return '; '.join(parts)


def compose_audio_cell(beat: 'Beat', channel: str) -> tuple:
    """
    音频字段 → (音乐/音效列, 参数列)。

    停止 / 排队 这两种"动作"用**伪值**塞进同一个下拉列
    （`（停止音乐）` / `（排队播放）`）。策划少记一列，也不用手打动作词。
    """
    if channel == 'music':
        name, params, action = beat.music, beat.music_params, \
            beat.music_action
        pseudo = AUDIO_PSEUDO['BGM']
    else:
        name, params, action = beat.sound, beat.sound_params, \
            beat.sound_action
        pseudo = AUDIO_PSEUDO['SFX']
    if action in ('stop', 'queue') and not name:
        back = {v: k for k, v in pseudo.items()}
        return back.get(action, ''), ''
    return name, params


def _fill_audio_from_cells(beat, channel: str, value: str, params: str):
    """音乐/音效列 + 参数列 → 内部字段（含伪值识别）。"""
    pseudo = AUDIO_PSEUDO['BGM' if channel == 'music' else 'SFX']
    raw = (value or '').strip()

    # 单元格是空的：**不**改写任何结构化字段——保持调用方已有的状态。
    # 否则会把下面的"无值"误当"清空"，把直接构造的 Beat 上已经标好的
    # music_action='stop' 之类冲掉，导致 stop 指令凭空消失。
    if not raw:
        return

    # 兼容上一版把整句 DSL 写在音频列里的表（`[音乐] spring fadein 1.0`）
    if raw.startswith('['):
        from dsl import parse_audio
        ops = parse_audio(raw, channel)
        if ops:
            name = ops[0].name
            action = ops[0].action if ops[0].action != 'unknown' else 'play'
            params = ops[0].params or params
            if channel == 'music':
                beat.music, beat.music_params, beat.music_action = \
                    name, (params or '').strip(), action
            else:
                beat.sound, beat.sound_params, beat.sound_action = \
                    name, (params or '').strip(), action
            return

    if raw in pseudo:
        action, name = pseudo[raw], ''
    elif raw.lower() in ('stop', 'queue'):
        action, name = raw.lower(), ''
    else:
        action, name = 'play', raw
    if channel == 'music':
        beat.music, beat.music_params, beat.music_action = \
            name, (params or '').strip(), action
    else:
        beat.sound, beat.sound_params, beat.sound_action = \
            name, (params or '').strip(), action


def _fill_audio_legacy(beat, g):
    """旧表的 Music/Sound 三列组 → 内部字段。"""
    beat.music = g('Music')
    beat.music_params = g('MusicParams')
    beat.music_action = g('MusicAction') or 'play'
    beat.sound = g('Sound')
    beat.sound_params = g('SoundParams')
    beat.sound_action = g('SoundAction') or 'play'


def compose_stage_extra(beat: 'Beat', symbols=None) -> str:
    """
    结构化列**表达不了**的舞台指令 → 「舞台（高级）」列。

    只有这些会落到这里：复杂 show 子句、自带位置的立绘、带转场的退场、
    `pause`、以及从脚本里读到的原生语句。能进结构化列的一律不进——
    否则同一句 show 会被两列各输出一次。
    """
    from dsl import Symbols, stage_word_if_lossless
    symbols = symbols if isinstance(symbols, Symbols) else None

    ops = []
    for item in _split_show_items(beat.show):
        if item.get('clause'):
            ops.append(('show', f"show {item['clause']}"))
        elif item.get('at'):
            ops.append(('show', f"show {item['image']} at {item['at']}"))
    for item in _split_hide_items(beat.hide):
        if item.get('with'):
            ops.append(('hide', f"hide {item['image']} with {item['with']}"))
    if not ops:
        return ''

    if symbols is not None:
        out = []
        for verb, line in ops:
            head = line.split(None, 1)[1] if ' ' in line else ''
            spec, at, with_ = _parse_native_stage_tail(head)
            human = stage_word_if_lossless(symbols, verb, spec, at, with_)
            out.append(human or line)
        return '; '.join(out)
    return '; '.join(line for _v, line in ops)



def compose_code(beat: 'Beat') -> str:
    """
    CodeBlock 单元格 = 原生语句（raw）+ python 代码（code），原样拼。

    这是唯一的"保真后门"：里面有东西，编译器就原封不动抄进脚本。
    """
    parts = [ln for ln in _split_lines(beat.raw)]
    parts += [ln for ln in _split_lines(beat.code)]
    return '\n'.join(parts)


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
    # 合成 DSL 文本：表格里看到的舞台/音频是"人话"，而不是拆散的六七列。
    # 这里没有符号表，所以合成的是原生写法（`show ling close_happy at left`）
    # ——它是无损的，编译时原样直通；打开项目后会用符号表重合成中文写法。
    for b in beats:
        # 结构化列（背景/立绘/位置/转场/退场）已经承载了主要舞台指令，
        # 「舞台（高级）」列只放结构化列表达不了的部分（复杂 show 子句、
        # 自带位置的立绘、带转场的退场）。否则同一句 show 会被结构化列
        # 和 Stage 列各输出一次，生成重复代码。
        b.stage = compose_stage_extra(b, None)
        b.bgm = compose_audio_text(b, 'music')
        b.sfx = compose_audio_text(b, 'sound')
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
    """Beat 语义内容的短哈希（只参与冲突检测，不参与相等判断）。

    刻意把 stage/bgm/sfx 的**原文**也算进去：策划在 Stage 里改了一个表情词，
    哪怕解析器暂时认不出来，也必须让 ContentHash 变——否则 push 会把
    "改了内容"判成"未变"，静默丢改动。
    """
    import hashlib
    parts = [
        beat.kind, beat.speaker, beat.text, beat.scene, beat.show, beat.hide,
        beat.at, beat.scene_at, beat.with_, beat.transform, beat.stage_order,
        beat.raw,
        beat.code, beat.music, beat.music_params, beat.music_action,
        beat.sound, beat.sound_params, beat.sound_action, beat.voice,
        beat.condition, beat.comment,
        beat.stage, beat.bgm, beat.sfx,
    ]
    blob = '\x1f'.join('' if p is None else str(p) for p in parts)
    return hashlib.md5(blob.encode('utf-8')).hexdigest()[:10]


def hydrate_from_dsl(beats: list, symbols) -> list:
    """
    把「舞台（高级）」/ 「音乐」/「音效」列里的 DSL 文本解析成结构化字段。

    为什么分两步：单元格里是**人话或原生写法**，解析它需要符号表（角色
    显示名 → image tag、表情词 → 属性、资产 ID → 文件路径）。而
    `Beat.from_row` 读表时不该依赖资源表。所以：
      · 读表 → 结构化列直接落字段，DSL 原文先不动
      · 有资源表时 → hydrate 把 DSL 原文**追加**成字段

      "追加"而不是"替换"：结构化列与高级列是同一条流水线的两段输入，
      替换会把策划在下拉列里填的立绘吃掉。

    幂等：同一段原文只解释一次（`_hydrated_stage` 记着上次处理过的文本），
    否则 import → compile 两次 hydrate 会把立绘输出两遍。
    """
    from dsl import parse_stage, parse_audio, Symbols, Audit, TODO

    symbols = symbols if isinstance(symbols, Symbols) else Symbols(symbols)
    audit = Audit(symbols)

    for beat in beats or ():
        if beat.stage and getattr(beat, '_hydrated_stage', None) != beat.stage:
            _hydrate_stage(beat, symbols, audit, parse_stage)
            try:
                beat._hydrated_stage = beat.stage
            except Exception:
                pass
        if beat.bgm:
            _hydrate_audio(beat, 'music', symbols, parse_audio)
        if beat.sfx:
            _hydrate_audio(beat, 'sound', symbols, parse_audio)
    return beats


def _hydrate_stage(beat, symbols, audit, parse_stage):
    """
    Stage DSL → beat.scene / show / hide / at / with_ / stage_order。

    原生写法的指令也要拆进结构化字段（舞台快照才看得见演员），但**不写回
    raw**——raw 属于代码块后门，同一行语句被输出两次就会生成重复代码。
    拆不动就忽略：`beat.stage` 原文还在，渲染时它会原样出去。
    """
    from dsl import compile_stage

    parsed = parse_stage(beat.stage)
    show_items, hide_items, order = [], [], []
    scene = None
    at = ''            # 立绘的位置
    scene_at = ''      # 背景自己的变换
    with_ = ''

    for op, (_intent, code, status) in zip(
            parsed.ops, compile_stage(beat.stage, symbols, audit)):
        if op.native:
            _absorb_native(op, show_items, hide_items, order)
            head = op.raw.split()[0] if op.raw.split() else ''
            if head == 'scene':
                spec, _at, _w = _parse_native_stage_tail(
                    op.raw[6:].strip())
                scene = spec or scene
                scene_at = _at or scene_at
                with_ = _w or with_
            elif head == 'with' and not with_:
                parts = op.raw.split()
                with_ = parts[1] if len(parts) > 1 else ''
            continue

        if op.verb == 'show':
            if code.startswith('# TODO'):
                continue
            head = code.split(None, 1)[1] if ' ' in code else ''
            spec, _at, _w = _parse_native_stage_tail(head)
            if spec:
                show_items.append({'clause': '', 'image': spec,
                                   'at': _at or ''})
            order.append('show')
        elif op.verb == 'hide':
            if code.startswith('# TODO'):
                continue
            head = code.split(None, 1)[1] if ' ' in code else ''
            spec, _at, _w = _parse_native_stage_tail(head)
            if spec:
                hide_items.append({'image': spec, 'with': _w or ''})
            order.append('hide')
        elif op.verb == 'scene':
            if code.startswith('# TODO'):
                continue
            head = code.split(None, 1)[1] if ' ' in code else ''
            spec, _at, _w = _parse_native_stage_tail(head)
            scene = spec or scene
            scene_at = _at or scene_at
            with_ = _w or with_
            order.append('scene')
        elif op.verb == 'with':
            with_ = with_ or op.with_
        elif op.verb == 'pause':
            order.append('raw')

    # 追加到结构化列已经填好的内容后面（高级列是"补充通道"）
    if show_items:
        beat.show = _join_show_items(_split_show_items(beat.show)
                                     + show_items)
    if hide_items:
        beat.hide = _join_hide_items(_split_hide_items(beat.hide)
                                     + hide_items)
    if scene is not None:
        beat.scene = scene
    if at:
        beat.at = at
    if scene_at:
        beat.scene_at = scene_at
    if with_:
        beat.with_ = with_
    dedup = []
    for v in order:
        if v in ('scene', 'show', 'hide') and v not in dedup:
            dedup.append(v)
    if dedup:
        beat.stage_order = ','.join(dedup)


def _absorb_native(op, show_items, hide_items, order):
    """
    原生舞台写法 → 结构化字段（供舞台快照/校验使用）。

    简单的 `show tag attr at 位置` 会折成紧凑形式（`tag attr@位置`），
    和"从语句流折叠"出来的写法保持同一种编码——否则同一张表里会出现
    两种 show 形态，后续比较、去重都要写两套。带 behind/as/onlayer/
    zorder/expression 的复杂子句原样保留。
    """
    head = op.raw.split()[0] if op.raw.split() else ''
    if head == 'show':
        rest = op.raw[len('show'):].strip()
        spec, at, with_ = _parse_native_stage_tail(rest)
        if spec and not with_:
            show_items.append({'clause': '', 'image': spec, 'at': at})
        else:
            for it in _split_show_items(rest):
                show_items.append(it)
        order.append('show')
    elif head == 'hide':
        rest = op.raw[len('hide'):].strip()
        spec, at, with_ = _parse_native_stage_tail(rest)
        if spec and not at:
            hide_items.append({'image': spec, 'with': with_})
        else:
            for it in _split_hide_items(rest):
                hide_items.append({'image': it['image'],
                                   'with': it.get('with', '')})
        order.append('hide')


def _hydrate_audio(beat, channel, symbols, parse_audio):
    from dsl import compile_audio, TODO

    text = beat.bgm if channel == 'music' else beat.sfx
    ops = parse_audio(text, channel)
    compiled = compile_audio(text, channel, symbols)
    for op, (_intent, code, status) in zip(ops, compiled):
        if status == TODO or code.startswith('# TODO'):
            continue
        if code.startswith('stop '):
            action, name = 'stop', ''
        elif code.startswith('queue '):
            action, name = 'queue', op.name
        else:
            action, name = 'play', op.name
        if channel == 'music':
            beat.music, beat.music_params, beat.music_action = \
                name, op.params, action
        else:
            beat.sound, beat.sound_params, beat.sound_action = \
                name, op.params, action



def apply_script_definitions(registry, parse_result) -> dict:
    """
    把脚本里 `define` / `image` / `transform` 声明合并进资源表（内存）。

    这是"程序敲代码 → 表格跟着变"的核心一步：
      · `image bg bg1_1 = "..."` 声明 → 图像清单（`scene black`、
        `show bg bg1_1` 引用的名字就在这里，不合并就会被报"未登记"）
      · `transform bounce_quick:` → 变换清单（否则表格里选不到自定义变换）
      · `define x = DynamicCharacter(...)` → 角色清单

    已登记的同名条目**不覆盖**（人在表里写的说明/分类是权威），只补空缺。

    返回 {'images': n, 'transforms': n, 'characters': n} 表示各类新增数量。
    """
    if registry is None or parse_result is None:
        return {'images': 0, 'transforms': 0, 'characters': 0}

    added = {'images': 0, 'transforms': 0, 'characters': 0}

    # ---------- 图像声明 ----------
    for name, info in (getattr(parse_result, 'images', {}) or {}).items():
        info = info or {}
        if info.get('layered'):
            # 合成立绘：以 tag 本身为 ID，属性清单单独存，
            # 校验时按"tag 存在 + 属性都声明过"判定（见 validate）。
            if name in registry.images:
                row = registry.images[name]
                if info.get('attrs'):
                    row.setdefault('_attrs', list(info['attrs']))
            else:
                registry.images[name] = {
                    'ID': name,
                    'Path': info.get('expr') or '',
                    'Tag': name,
                    'Attributes': '',
                    'Category': 'character',
                    'Notes': _source_note(info),
                    '_layered': True,
                    '_attrs': list(info.get('attrs') or []),
                    '_from_script': True,
                }
                added['images'] += 1
            continue

        image_id = _image_id_from_name(name)
        if not image_id:
            continue
        tag, attrs = _split_image_name(name)
        row = {
            'ID': image_id,
            'Path': info.get('path') or '',
            'Tag': tag,
            'Attributes': attrs,
            'Category': _category_from_tag(tag),
            'Notes': _source_note(info),
            '_from_script': True,
        }
        if info.get('solid') and not row['Path']:
            row['Solid'] = info['solid']
        if image_id in registry.images:
            existing = registry.images[image_id]
            for key, val in row.items():
                if key.startswith('_'):
                    continue
                if not existing.get(key) and val:
                    existing[key] = val
        else:
            registry.images[image_id] = row
            added['images'] += 1

    # ---------- 变换 ----------
    for name, tf in (getattr(parse_result, 'transforms', {}) or {}).items():
        row = {
            'ID': name,
            'Path': '',
            'ATL': getattr(tf, 'body', ''),
            'Params': getattr(tf, 'params', ''),
            'Doc': getattr(tf, 'doc', ''),
            'Category': 'custom',
            'Notes': (f"{getattr(tf, 'source_file', '')}:"
                      f"{getattr(tf, 'source_line', 0)}"),
            '_from_script': True,
        }
        if name in registry.transforms:
            existing = registry.transforms[name]
            for key, val in row.items():
                if key.startswith('_'):
                    continue
                if not existing.get(key) and val:
                    existing[key] = val
        else:
            registry.transforms[name] = row
            added['transforms'] += 1

    # ---------- 角色 ----------
    for var, ch in (getattr(parse_result, 'characters', {}) or {}).items():
        row = {
            'ID': var,
            'Name': getattr(ch, 'display_name', '') or var,
            'Color': getattr(ch, 'color', ''),
            'WhatColor': getattr(ch, 'what_color', ''),
            'ImageTag': getattr(ch, 'image_tag', ''),
            'Kind': getattr(ch, 'kind', ''),
            'VoiceTag': getattr(ch, 'voice_tag', ''),
            'SideImage': getattr(ch, 'side_image', ''),
            'WhatPrefix': getattr(ch, 'what_prefix', ''),
            'WhatSuffix': getattr(ch, 'what_suffix', ''),
            'WhoPrefix': getattr(ch, 'who_prefix', ''),
            'WhoSuffix': getattr(ch, 'who_suffix', ''),
            'DefaultTransform': '',
            'Notes': (f"{getattr(ch, 'source_file', '')}:"
                      f"{getattr(ch, 'source_line', 0)}"),
            '_dialogue_count': getattr(ch, 'dialogue_count', 0),
            '_from_script': True,
        }
        if var in registry.characters:
            existing = registry.characters[var]
            for key, val in row.items():
                if key.startswith('_'):
                    if key == '_dialogue_count':
                        existing[key] = val
                    continue
                if not existing.get(key) and val:
                    existing[key] = val
        else:
            registry.characters[var] = row
            added['characters'] += 1

    return added


def _source_note(info) -> str:
    info = info or {}
    src = f"{info.get('source_file', '')}:{info.get('source_line', 0)}"
    return src if info.get('source_file') else ''


def _image_id_from_name(name: str) -> str:
    """Ren'Py 图像名 → 资产 ID：空格转下划线。`bg bg1_1` → `bg_bg1_1`"""
    return '_'.join(str(name or '').split())


def _split_image_name(name: str) -> tuple:
    """`bg bg1_1` → ('bg', 'bg1_1')；单个词时 attrs 取同值。"""
    parts = str(name or '').split()
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], '_'.join(parts[1:])


def _category_from_tag(tag: str) -> str:
    tag = (tag or '').lower()
    if tag in ('bg', 'background'):
        return 'background'
    if tag in ('cg', 'event'):
        return 'cg'
    if tag in ('ui', 'gui'):
        return 'ui'
    return 'character'


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

    ws.append([BEAT_HEADERS.get(f, f) for f in BEAT_FIELDS])
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical='center')

    symbols = make_symbols(registry) if registry is not None else None
    for beat in beats:
        _append_text_row(ws, beat.to_row(symbols))

    widths = {'LineId': 15, 'Seq': 6, 'Label': 16, 'Kind': 12,
              'Speaker': 14, 'Comment': 26, 'Text': 52,
              'Scene': 20, 'Show': 26, 'At': 14, 'With': 14, 'Hide': 18,
              'BGM': 18, 'BGMParams': 16, 'SFX': 18, 'SFXParams': 16,
              'Voice': 18, 'Stage': 40, 'CodeBlock': 32,
              'ContentHash': 12}
    for i, name in enumerate(BEAT_FIELDS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(name, 16)
    ws.freeze_panes = 'D2'      # 冻结到章节列，长表横向滚动时还能看见归属

    if registry is not None and include_dropdowns:
        ranges = _write_candidate_sheets(wb, registry)
        _attach_dropdowns(ws, ranges, len(beats),
                          names=_defined_names_for(ranges))
        _write_keyword_sheet(wb, registry)

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
        _append_text_row(ws, [r['line_id'], r['seq'], r['label'], r['speaker'],
                              r['text'], r['scene'], r['actors'], r['music'],
                              r['sound'], r['voice'], r['note']])
    for col, w in zip('ABCDEFGHIJK',
                      (14, 6, 18, 14, 40, 18, 34, 16, 16, 16, 24)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'A2'
    return ws


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
    from dsl import (DEFAULT_EXPRESSIONS, DEFAULT_POSITIONS, NON_ACTOR_TAGS)

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


def _looks_like_renpy_statement(line: str) -> bool:
    """这行代码像"一条 Ren'Py 语句"（而不是裸的 python 表达式）。"""
    head = line.split(None, 1)[0] if line.split() else ''
    head = head.rstrip(':')
    return head in _RENPY_STATEMENT_HEADS


_RENPY_STATEMENT_HEADS = frozenset({
    '$', 'python', 'with', 'pause', 'jump', 'call', 'play', 'queue', 'stop',
    'scene', 'show', 'hide', 'voice', 'window', 'menu', 'return', 'pass',
    'if', 'elif', 'else', 'while', 'for', 'init', 'label', 'define',
    'default', 'transform', 'image', 'extend', 'screen', 'nvl', 'camera',
    'style', 'layeredimage', 'translate', 'on', 'at', 'renpy.', 'persistent.',
    'config.', 'gui.', 'store.', 'narrator', 'voice_sustain',
})


def _render_code_block(text: str) -> list:
    """
    CodeBlock（保真后门）→ 代码行，**不解析、不校验**。

    处理三类内容：
      · `$ x += 1`            → 原样
      · `with dissolve`       → 原样（原生语句）
      · `x += 1` / `renpy.x()` → 补 `$`（裸 python 表达式在 Ren'Py 里必须带 $）
      · 带缩进的多行（`python:` 块的块体）→ 包回 `python:` 块，保持结构
    """
    lines = _split_lines(text)
    if not lines:
        return []
    if any(ln != ln.lstrip() for ln in lines):
        return ['python:'] + [('    ' + ln) if ln.strip() else '' for ln in lines]
    out = []
    for ln in lines:
        s = ln.strip()
        if s.startswith('$') or _looks_like_renpy_statement(s):
            out.append(ln)
        else:
            out.append('$ ' + s)
    return out


def render_beat_to_rpy(beat: Beat, indent: str = '    ',
                       name_resolver=None, audit=None,
                       trace: list = None) -> list:
    """
    一个 Beat 渲染成 Ren'Py 语句。返回不带换行符的字符串列表。

    两条输入形态、两条通道：

      · `stage` / `bgm` / `sfx` 有 DSL 文本 → 走**编译器**：
        方括号形式（`[显示] 凌跃捷 开心 左`）被翻译成 `show ling …`；
        原生形式（`show ling happy`）直通不解析；
        找不到资源只写 `# TODO: 缺少资源 …`，既不报错也不生成非法语句。
      · 没有 DSL 文本（Beat 由代码直接构造，如单测）→ 读结构化字段，
        保持历史行为。

    trace 非空时逐行记录 (来源, 原始意图, 代码, 状态)，供「编译结果表」
    （Export_RenPy）使用——Sheet 2 与生成的脚本因此**必然一致**，
    而不是"另写一套逻辑再对一遍"。
    """
    pre = []
    for c in _split_lines(beat.comment):
        pre.append(c if c.startswith('#') else '# ' + c)

    lines = []
    symbols = getattr(audit, 'symbols', None)

    def emit(line, source='', intent='', status='ok'):
        lines.append(line)
        if trace is not None:
            trace.append({'source': source, 'intent': intent,
                          'code': line, 'status': status})

    def todo(source, intent, message):
        emit(f"# TODO: {message}", source, intent, 'todo')

    # ---------------- 舞台 ----------------
    # 结构化列（背景/立绘/位置/转场/退场）先按 StageOrder 出，
    # 再把「舞台（高级）」列里那些结构化列表达不了的指令**追加**在后面。
    # 两个来源可能表达同一条指令（hydrate 会把 Stage 原文并回结构化字段），
    # 所以记下来做精确去重，避免同一句 show 被生成两次。
    emitted_stage = set()
    _render_stage_from_fields(beat, emit, _resolver_for(audit, name_resolver),
                              audit, emitted_stage)
    if beat.stage:
        from dsl import compile_stage
        for intent, code, status in compile_stage(beat.stage, symbols, audit):
            if code in emitted_stage:
                # 结构化列已出过同一条指令（Stage 原文被 hydrate 并回），跳过
                continue
            emit(code, '舞台', intent, status)

    # 旧表带过来的 Transform 列（现已取消）：当成这一拍的转场
    if beat.transform and not beat.with_:
        emit(f"with {beat.transform}", '舞台', beat.transform)

    # ---------------- 代码块（保真后门） ----------------
    for c in _render_code_block(compose_code(beat)):
        emit(c, '代码块', c)

    # ---------------- 音频 ----------------
    # 音乐/音效列存的是**资产 ID / DSL 文本**（可下拉），参数单独一列；
    # 内部仍走同一套字段，停止/排队用「（停止音乐）」这类伪值表达。
    # 结构化字段（beat.music / beat.sound）为空时，从文本列（beat.bgm /
    # beat.sfx）解析一次——否则直接构造、没走过 from_row 的 Beat（如单测、
    # 内存里拼的）会丢掉 stop/play，生成不出音频语句。
    if not beat.music:
        _fill_audio_from_cells(beat, 'music', beat.bgm, beat.music_params)
    if not beat.sound:
        _fill_audio_from_cells(beat, 'sound', beat.sfx, beat.sound_params)
    _render_audio_from_fields(beat, emit, audit)

    if beat.voice:
        if audit is not None:
            from dsl import TODO as _TODO
            if audit.audio('voice', beat.voice) == _TODO:
                todo('语音', beat.voice,
                     f"缺少资源 语音 '{beat.voice}'")
            else:
                emit(f"voice {_quote(audit.audio_ref('voice', beat.voice))}",
                     '语音', beat.voice)
        else:
            emit(f"voice {_audio_ref(beat.voice)}", '语音', beat.voice)

    # ---------------- 台词 ----------------
    speaker = beat.speaker
    if speaker and audit is not None:
        from dsl import TODO as _TODO
        if audit.character(speaker) == _TODO:
            todo('说话人', speaker, f"缺少资源 角色 '{speaker}'")
            speaker = ''
    if beat.kind == KIND_MENU_OPTION:
        emit(f'"{beat.text}"', '台词', beat.text)
    elif beat.kind == KIND_EXTEND:
        emit(f"extend {_quote(beat.text)}", '台词', beat.text)
    elif beat.text:
        if speaker:
            emit(f"{speaker} {_quote(beat.text)}", '台词', beat.text)
        else:
            emit(_quote(beat.text), '台词', beat.text)
    elif beat.kind == KIND_MENU and beat.text:
        emit(_quote(beat.text), '台词', beat.text)

    if not lines:
        return pre

    if beat.condition:
        return pre + [f"if {beat.condition}:"] + [
            indent + ln for ln in lines
        ]
    return pre + lines


def _resolver_for(audit, name_resolver=None):
    """
    挑一个"图像名能不能解析"的判定函数。

    有符号表就用它（和 DSL 编译走同一个判定），否则用调用方给的
    name_resolver。判定结果决定生成脚本时写 `show eileen happy` 还是
    `show eileen_happy`——对 `tag attribute` 命名的项目，前者才跑得起来。
    """
    if name_resolver is not None:
        return name_resolver
    symbols = getattr(audit, 'symbols', None)
    if symbols is None:
        return None
    return lambda name: bool(symbols.image_ok(name))


def _render_stage_from_fields(beat, emit, name_resolver, audit,
                              emitted: set = None) -> None:
    """
    结构化列 → Ren'Py 舞台指令（顺序按 StageOrder 还原）。

    列与指令的对应关系（就是「使用说明」里写给策划的那张表）：

        背景列   `bg room` / `bg room@bgfit`     → scene bg room [at bgfit]
        立绘列   `ling happy` / `ling happy@f11` → show ling happy [at f11]
        位置列   `left`                          → 没写 @ 的立绘统一用它
        转场列   `dissolve`                      → 挂在该拍最后一条舞台指令上
        退场列   `ye` / `ye@dissolve`            → hide ye [with dissolve]
    """
    def art(name):
        return _emit_image_name(name, name_resolver)

    scene_line = ''
    if beat.scene:
        scene_line = f"scene {art(beat.scene)}"
        if beat.scene_at:
            scene_line += f" at {beat.scene_at}"

    show_lines = []
    for item in _split_show_items(beat.show):
        if item.get('clause'):
            show_lines.append(f"show {item['clause']}")
            continue
        line = f"show {art(item['image'])}"
        at = (item.get('at') or '').strip() or (beat.at or '').strip()
        if at:
            line += f" at {at}"
        show_lines.append(line)

    hide_lines = []
    for item in _split_hide_items(beat.hide):
        line = f"hide {art(item['image'])}"
        if item.get('with'):
            line += f" with {item['with']}"
        hide_lines.append(line)

    # 转场挂在该拍最后一条舞台指令上（有背景就挂背景，否则挂最后一条立绘，
    # 都没有就单独成行）——Ren'Py 里 `scene X with D` 与
    # `show X with D` 是最常见的两种写法。
    if beat.with_:
        if scene_line:
            scene_line += f" with {beat.with_}"
        elif show_lines:
            show_lines[-1] += f" with {beat.with_}"
        elif not hide_lines:
            show_lines.append(f"with {beat.with_}")

    stage = {'scene': [scene_line] if scene_line else [],
             'show': show_lines, 'hide': hide_lines}
    order = [v for v in (beat.stage_order.split(',')
                         if beat.stage_order else []) if v in stage]
    for v in ('scene', 'show', 'hide'):
        if v not in order:
            order.append(v)
    for v in order:
        for ln in stage[v]:
            if emitted is not None:
                emitted.add(ln)
            emit(ln, '舞台', ln)


def _render_audio_from_fields(beat, emit, audit=None) -> None:
    """
    音频字段 → play/queue/stop 语句。

    `audit` 非空时用它把**资产 ID 翻成真实文件路径**（`spring` →
    `"bgm/spring.mp3"`），并把查不到的引用写成 `# TODO: 缺少资源 …`
    而不是生成一条播不响的语句。
    """
    for channel, name, params, action, label in (
            ('music', beat.music, beat.music_params, beat.music_action, '音乐'),
            ('sound', beat.sound, beat.sound_params, beat.sound_action, '音效')):
        if not name and action not in ('stop', 'queue'):
            continue
        if action == 'stop':
            line = f"stop {channel}"
        elif action == 'queue':
            ref = _resolve_audio_ref(audit, channel, name)
            if ref is None:
                emit(f"# TODO: 缺少资源 {label} '{name}'", label, name, 'todo')
                continue
            line = f"queue {channel} {ref}"
        else:
            ref = _resolve_audio_ref(audit, channel, name)
            if ref is None:
                emit(f"# TODO: 缺少资源 {label} '{name}'", label, name, 'todo')
                continue
            line = f"play {channel} {ref}"
        if params:
            line += f" {params}"
        emit(line, label, name or action)


def _resolve_audio_ref(audit, channel: str, name: str):
    """音频引用文本；audit 判为"确实找不到"时返回 None。"""
    if not name:
        return None
    if audit is None:
        return _audio_ref(name)
    from dsl import TODO as _TODO
    if audit.audio(channel, name) == _TODO:
        return None
    return _audio_ref(audit.audio_ref(channel, name))



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


def make_symbols(registry=None, keywords=None):
    """从资源表造符号表（编译器查转译用的字典）。"""
    from dsl import Symbols
    return Symbols(registry, keywords=keywords)


def render_beats_to_rpy(beats: list, label_name: Optional[str] = None,
                        indent: str = '    ', registry=None,
                        symbols=None, trace: list = None) -> str:
    """
    Beat 列表 → 完整 .rpy 文本。

    label_name 为空时按 Beat.label 分段生成多个 label；
    指定了则全部塞进同一个 label（便于替换单个章节）。

    registry / symbols 非空时会：
      · 把演出表用到的自定义变换（transforms.xlsx 里带 ATL 的）生成
        `transform` 定义前置到文件头，让产物自包含；
      · 建立符号表，把 `[显示] 凌跃捷 开心 左` 翻成 `show ling close_happy at left`；
      · 找不到资源就写 `# TODO: 缺少资源 …`，**不报错、不中断**。
    """
    from dsl import Audit

    if symbols is None and registry is not None:
        symbols = make_symbols(registry)
    audit = Audit(symbols) if symbols is not None else None

    out = ['# 由剧本表自动生成，请勿在此文件手工改动结构']

    if registry is not None:
        used = collect_used_transforms(beats)
        defs = render_transform_definitions(registry, used)
        if defs:
            out.append('')
            out.append('# —— 自定义 transform（来自 transforms.xlsx）——')
            out.extend(defs)

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
            body.extend(render_beat_to_rpy(beat, indent=indent,
                                           audit=audit, trace=trace))
        if body:
            body = [ln for ln in body if ln.strip() != '']
        if body:
            out.extend(indent + ln if ln.strip() else '' for ln in body)
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
    保证表格仍有候选下拉（不然 Stage 的词表和 Speaker 下拉都是空的）。

    有符号表时会**重合成人话**：`show ling close_happy at left` 变成
    `[显示] 凌跃捷 开心 左`——但只在往返无损时才换（见 dsl 自检），
    猜不准就保留程序写法，绝不猜错一个表情。
    """
    from renpy_parser import RenPyParser, ParseResultBuilder

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
    export_beats_to_excel(beats, output_path, registry=registry)
    return output_path


def export_table_to_rpy(table_path, output_path=None,
                        label_name: Optional[str] = None, registry=None,
                        write_export_sheet: bool = True):
    """
    把演出表编译回 .rpy：反向路径。

    走的是 **compile.py 的编译流水线**——找不到资源不报错，只在生成的
    代码里留 `# TODO: 缺少资源 …`；顺手再写一张编译结果表（和脚本同源，
    必然一致）。原始演出表一个字都不改。
    """
    from compile import compile_table

    table_path = Path(table_path)
    if output_path is None:
        output_path = table_path.with_suffix('.rpy')
    rpy_path, _sheet, _result = compile_table(
        table_path, rpy_path=output_path, registry=registry,
        label_name=label_name, write_sheet=write_export_sheet)
    return Path(rpy_path)
