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
from io import BytesIO
from pathlib import Path
from typing import Iterable, Optional

import openpyxl
from openpyxl.drawing.image import Image as XLImage
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
    # StageOrder 放在最后并**隐藏整列**：它记录"这一拍先 hide 再 show"
    # 这类**源顺序**，属于运行时语义（`hide X` + `show X` = 重置立绘，
    # 反序会把立绘藏掉），不该由策划填。但也不能丢——丢了就退回
    # `scene→show→hide` 兜底，顺序变了演出就变了。
    #
    # 为什么是隐藏列而不是另开元数据表：它是**逐行**属性，藏在同一行上
    # 才能跟着复制/拖动/排序走；另开一张表一被手改就错位。
    # 用户 2026-09-28 拍板：「由工具维护，不暴露给策划」。
    'StageOrder',
    # OpOrder = 本拍**全部操作**（舞台/音乐/音效/语音）按源码出现的顺序。
    # 为什么还需要它（StageOrder 不够）：舞台指令之间能靠 StageOrder 保序，
    # 但**跨类别**的相对顺序没人记——`play music X` 写在 `show Y` 之前时，
    # 渲染固定按"舞台→音频→语音"输出，音频就跑到立绘后面去了。
    # 这在同一次交互里不改变最终画面，但回放/时间轴看起来是错的顺序。
    'OpOrder',
]

#: 这些列整列隐藏（工具维护的内部元数据，策划不该看见也不该填）。
#: 隐藏（hidden=True）在 Excel/WPS 里都是"列被收起"而不是"没有这一列"，
#: 数据照旧读写，所以往返不会丢——这是选隐藏列而非"不写这一列"的原因。
HIDDEN_BEAT_COLUMNS = ('StageOrder', 'OpOrder')

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
    'StageOrder': '舞台顺序（内部）',
    'OpOrder': '操作顺序（内部）',
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

    **Excel 那一面是 21 列**（见 BEAT_FIELDS）：20 列结构化编辑面 + 1 列
    **隐藏**的 `StageOrder`。演出相关的是：

        场景 / 立绘 / 位置 / 转场 / 退场      每列一个值，各挂词表下拉
        音乐 / 音效                          各自的「参数」列放 fadein 之类
        舞台（高级）Stage                     一格多指令的 DSL 逃生通道
        语音 / 代码 / 注释 / 台词 / 说话人 …

    `StageOrder` 记录"先 hide 再 show"这类**源顺序**（运行时语义，反序会把
    立绘藏掉），由工具写、工具读，整列隐藏，不暴露给策划。

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
    #: 本拍**全部操作**按源码出现的顺序，如 `'stage,music,sound,voice'`。
    #: 见 `BeatsFromStatements` 里的记录点与 `render_beat_to_rpy` 的消费点。
    op_order: str = ''
    raw: str = ''
    code: str = ''
    # CodeBlock 里"原生语句"与"python 代码"的**交错顺序**：
    # `[('raw', 'pause'), ('code', 'msg_add(…)'), ('raw', 'show screen …')]`。
    # raw / code 两个字段各自保序，但**两者之间**的顺序只有这份记录能还原。
    # 不记的话导出会把 `$ f()` / `show screen` / `pause` 的交替演出排成
    # "先全部 show/pause，再全部 $ f()"——同一张表导回去代码却变了样。
    ordered: list = field(default_factory=list)
    music: str = ''
    music_params: str = ''
    music_action: str = 'play'     # 'play' / 'queue' / 'stop'
    sound: str = ''
    sound_params: str = ''
    sound_action: str = 'play'     # 'play' / 'queue' / 'stop'
    voice: str = ''
    notes: str = ''
    # 这一拍是**从 .rpy 解析出来的**（而不是策划在 Excel 里新写的）。
    # 解析来的图像名是作者的原文（`show ling base_arms_akimbo close_happy`），
    # 导出时必须逐字还回去；不能因为"资源表里查不到这个组合"就改写成
    # 下划线形态 `ling_base_arms_akimbo_close_happy`——那是把能跑的脚本改坏。
    _from_script: bool = False

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
            # 隐藏列：源顺序。工具写、工具读，策划的界面里看不到它们。
            self.stage_order,
            self.op_order,
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
        # CodeBlock 单元格里 raw 与 code 是混着写的（一个格子装两样）。
        # 读回来时按"看起来像不像一条 Ren'Py 语句"重新分类，并把交错顺序
        # 记进 ordered——否则 `$ f()` 与 `show screen`/`pause` 交替的演出
        # 会被排成"先全部 show/pause 再全部 $ f()"，导出的代码和源脚本不一样。
        beat.raw, beat.code, beat.ordered = _split_code_cell(beat.code)

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
        if g('OpOrder'):
            beat.op_order = g('OpOrder')

        # —— 音频列 → 内部字段（含「停止/排队」伪值）——
        _fill_audio_from_cells(beat, 'music', g('BGM'), g('BGMParams'))
        _fill_audio_from_cells(beat, 'sound', g('SFX'), g('SFXParams'))

        # 旧表（26 列）自动迁移。
        #
        # 注意判据：`Show/Hide/At/With` **当前表也有**（它们是结构化下拉列），
        # 所以不能拿 `LEGACY_STAGE_COLUMNS` 里任意一个键当"这是老表"的信号
        # ——那样每一行新表都会走进来，然后 `beat.raw = g('Raw')` 把从
        # CodeBlock 拆出来的 raw 行（`show screen` / `pause` / `with`）
        # **整批清空**，导出的代码就少了这些语句。只认真正的老表专属列。
        legacy_keys = set(LEGACY_STAGE_COLUMNS) - set(BEAT_FIELDS)
        if any(k in row for k in legacy_keys):
            if not beat.show:
                beat.show = g('Show')
            if not beat.hide:
                beat.hide = g('Hide')
            # 老表的 Raw 列是**额外**内容，追加而不是覆盖（老表可能同时
            # 有 CodeBlock 与 Raw 两列）。
            legacy_raw = g('Raw')
            if legacy_raw:
                beat.raw = _append_line(beat.raw, legacy_raw)
                beat.ordered.extend(
                    ('raw', ln) for ln in _split_lines(legacy_raw))
        if any(k in row for k in LEGACY_AUDIO_COLUMNS):
            if not beat.music and not beat.sound:
                _fill_audio_legacy(beat, g)

        # 「音乐」/「音效」文本列（beat.bgm / beat.sfx）是真相源：从已填好的
        # 结构化字段**合成回** DSL 文本，保证"老表迁移"与"打开 rpy → 导出"
        # 两种来源下文本列都有一致的人话/原生写法，便于策划复核与二次编辑。
        beat.bgm = compose_audio_text(beat, 'music')
        beat.sfx = compose_audio_text(beat, 'sound')
        return beat

    @property
    def order_ops(self) -> list:
        """本拍全部操作的源顺序（`['audio', 'stage']` 这样）。

        空表示"没有记录"（老表 / 手工新增行）——消费方要退回历史固定顺序，
        **不能当成"顺序是空的"**，否则会一条都不输出。
        """
        return [t.strip() for t in (self.op_order or '').split(',') if t.strip()]

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
    立绘列：`ling happy`；自带位置写 `ling happy@left`。

    **这一列承载"自带位置的立绘"**（`图@位置`）。`compose_stage_extra`
    不再重复写它——两者必须严格互补，见那里的说明。

    （历史坑：这里曾经 `if item.get('at'): continue`，把带位置的立绘
    整条跳过，指望「舞台（高级）」列兜底。结果是**位置信息在 Excel
    往返里直接丢掉**：真实项目 `chapter1_4` 往返掉了 32 条带 `at` 的
    show，而且和 `compose_stage_extra` 一起构成"两列各写一遍"的双写。
    一列漏写 + 另一列重复写，是同一个根因的两面。）
    """
    parts = []
    for item in _split_show_items(beat.show):
        if item.get('clause'):
            continue
        img = (item.get('image') or '').strip()
        if not img:
            continue
        at = (item.get('at') or '').strip()
        parts.append(f'{img}@{at}' if at else img)
    return '; '.join(parts)


def compose_hide_cell(beat: 'Beat') -> str:
    """
    退场列：`ye`，带转场写 `ye@dissolve`。

    **这一列承载"带转场的退场"**（`图@转场`），`compose_stage_extra` 不再
    重复写它。理由与立绘列的 `图@位置` 完全一样：一个格子装不下两个语义，
    而两列都写就会各输出一次，读回来变成
    `hide='e@dissolve; e@dissolve'`（实测到的重复退场）。
    """
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

    规则与 `compose_show_cell` / `compose_hide_cell` **严格互补**，
    合起来必须恰好覆盖每个舞台指令一次（这是一条契约，不是约定俗成）：

        复杂 show 子句（clause）  → 只进 Stage
        带 `at` 的立绘            → 只进「立绘」列的 `图@位置`
        带转场的退场              → 只进「退场」列的 `图@转场`
        其余（pause / 原生语句）  → 只进 Stage

    **为什么这条互补关系必须成立**（2026-09-28 实测事故）：
    同一句舞台指令被两列各写一次时，读回来会往 `beat.show` / `beat.hide`
    里**追加两份**（一份来自结构化列、一份来自 Stage 的 hydrate），
    渲染时两条都出——真实项目 `chapter1_4.xlsx` 里这种情况有 10 行，
    生成的脚本里就出现了 `show ye hi at i11` + `show 叶怀瑾 hi at i11`
    这种**一条重复 + 一条非法中文 tag** 的组合。

    所以本函数**只**输出"结构化列表达不了"的东西。历史上它多输出了
    `show X at Y` 与 `hide X with Y` 两种**其实表达得了**的指令，
    那正是双写的来源。
    """
    from dsl import Symbols, stage_word_if_lossless
    symbols = symbols if isinstance(symbols, Symbols) else None

    # 只收"结构化列表达不了"的：目前只有复杂 show 子句。
    # 带 `at` 的立绘走「立绘」列，带转场的退场走「退场」列，
    # 两者都由结构化列承载，这里一律不碰（见函数 docstring）。
    ops = []
    for item in _split_show_items(beat.show):
        if item.get('clause'):
            ops.append(('show', f"show {item['clause']}"))
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

    有 `ordered` 时按**源码里的交错顺序**拼（`$ f()` 与 `show screen`/`pause`
    交替的演出必须逐行还原）；没有 `ordered`（表里读回来的 Beat）就退回
    "raw 在前、code 在后"的历史行为，保证老表照旧。
    """
    ordered = _ordered_code_lines(beat)
    if ordered is not None:
        return '\n'.join(ordered)
    parts = [ln for ln in _split_lines(beat.raw)]
    parts += [ln for ln in _split_lines(beat.code)]
    return '\n'.join(parts)


def _ordered_code_lines(beat: 'Beat') -> Optional[list]:
    """
    raw / code 按 `beat.ordered` 还原成交错行；无法可信还原时返回 None。

    为什么不能只靠"raw 拼 code"：真实项目里手机消息那种演出是
    `$ msg_add(…)` → `show screen` → `pause` → `hide screen` → `$ msg_add(…)`
    交替出现的。raw 与 code 各自保序，但**两者之间**的顺序只有 ordered 有；
    丢掉它导出的代码就变成"先全部 show/pause、再全部 $ msg_add"——同一张表
    导回去演出全乱。

    实现是把 `ordered` 当**消费序列**：逐条按记录的类型去对应的行队列里取。
    某类型的行不够了就说明 ordered 与字段不同步（表被手改过），
    此时返回 None 走历史行为，绝不猜。
    """
    ordered = getattr(beat, 'ordered', None) or []
    if not ordered:
        return None
    pools = {'raw': list(_split_lines(beat.raw)),
             'code': list(_split_lines(beat.code))}
    pos = {'raw': 0, 'code': 0}
    out = []
    for item in ordered:
        try:
            kind, line = item
        except (TypeError, ValueError):
            return None
        pool = pools.get(kind)
        if pool is None:
            return None
        i = pos[kind]
        if i >= len(pool) or pool[i] != line:
            return None
        out.append(line)
        pos[kind] = i + 1
    # ordered 必须是"两个队列都恰好取完"，否则说明它不完整
    if pos['raw'] != len(pools['raw']) or pos['code'] != len(pools['code']):
        return None
    return out


def _split_code_cell(cell: str) -> tuple:
    """
    CodeBlock 单元格 → (raw, code, ordered)。

    表里只有**一个**格子装"原生语句 + python 代码"，所以读回来时必须重新
    分类。分类规则刻意保守（只认确定是语句的头），因为猜错的代价是
    生成语法错误的脚本：

      · `with dissolve` / `show screen x` / `pause` / `jump x` … → raw
      · `$ f()` / `f()` 裸表达式 / `python:` 块 → code

    ordered 一并给出，让渲染能按**原来的交错顺序**出代码（见
    `_ordered_code_lines`）。
    """
    lines = _split_lines(cell)
    if not lines:
        return '', '', []
    raw, code, ordered = [], [], []
    for ln in lines:
        s = ln.strip()
        if s.startswith('$') or (s and not _looks_like_renpy_statement(s)
                                 and not s.endswith(':')
                                 and not _is_bare_renpy_verb(s)):
            code.append(ln)
            ordered.append(('code', ln))
        else:
            raw.append(ln)
            ordered.append(('raw', ln))
    return '\n'.join(raw), '\n'.join(code), ordered


def _is_bare_renpy_verb(line: str) -> bool:
    """`pause` / `with dissolve` 这类没有参数也成立的裸语句（别被当 python）。"""
    head = line.split(None, 1)[0] if line.split() else ''
    return head.rstrip(':') in _RENPY_STATEMENT_HEADS


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
    """
    把多个 show 项拼成 `图像@变换; 图像2; cg x at y behind z`。

    **同名同参数的去重**：`hydrate_from_dsl` 是"追加"语义（结构化列 +
    高级列是同一条流水线的两段输入），而 Stage 列往往已经把结构化列的内容
    原样写了一遍，于是 hydrate 会把同一条 show 又追加一次。不去重的话
    导出会生成两遍 `show ling close_happy at i11`（重复执行转场）。
    """
    parts, seen = [], set()
    for it in items:
        if it.get('clause'):
            part = it['clause']
        elif it.get('at'):
            part = f"{it['image']}@{it['at']}"
        else:
            part = it['image']
        if not part or part in seen:
            continue
        seen.add(part)
        parts.append(part)
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
    #: 最后一个说出台词的人，用来给 `extend` 标注"谁在续说"（显示用）。
    last_speaker = ''
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

    def mark_op(kind):
        """
        记录**全部操作**在本拍中的出现顺序（舞台 / 音乐 / 音效 / 语音）。

        与 `mark_stage` 的区别：`mark_stage` 只管舞台内部（scene/show/hide）
        的相对顺序；这里管**跨类别**——`play music X` 写在 `show Y` 之前时，
        必须记住 audio 在前，否则渲染固定按"舞台→音频→语音"输出，
        音频会跑到立绘后面（回放/时间轴看到的顺序就错了）。

        同类只记一次：同一拍里两条 `play sound` 已经被 flush 拆成了两拍
        （见 PLAY 分支），所以这里不需要计数。
        """
        order = pending.op_order.split(',') if pending.op_order else []
        if kind not in order:
            order.append(kind)
        pending.op_order = ','.join(order)

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
            # 记住最后一个说话人，给紧随其后的 `extend` 用（见 EXTEND 分支）。
            # **旁白也算"最后一句"**：`"旁白。"` 之后跟 `extend` 续的是旁白，
            # 所以这里要把 last_speaker 清空，不能让它继承更早那个角色
            # （否则旁白续写会被安上说话人，把旁白变成台词）。
            last_speaker = speaker

        elif kind == StatementKind.EXTEND:
            seq += 1
            # `extend` 在 Ren'Py 里是**续上一句**（渲染成 `extend "…"`，
            # 不写说话人），但表里/卡片上必须让策划看出**这是谁在续说**——
            # 否则像 `#52 extend "铁汁我好像就是妹子啊…"` 这种看起来
            # 跟旁白一模一样，分不清是谁。用户 2026-09-28 报的"幽灵旁白"
            # 就是这个现象。
            #
            # 只是在 Beat 上「显示用」地填 speaker，渲染仍然走 `extend`
            # 分支（见 render_beat_to_rpy），**不会**生成 `jc "…"`。
            beats.append(Beat(
                seq=seq, label=current_label,
                condition=stmt.condition or '',
                kind=KIND_EXTEND,
                speaker=last_speaker,
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
                # 菜单把说话链打断了：选项之后的第一句该谁说是新语境
                last_speaker = ''

        elif kind == StatementKind.MENU_OPTION:
            seq += 1
            beats.append(Beat(
                seq=seq, label=current_label,
                condition=stmt.condition or '',
                kind=KIND_MENU_OPTION,
                text=payload.get('text', '') or '',
            ))
            flush_stage_only()
            last_speaker = ''

        elif kind == StatementKind.SCENE:
            if pending.scene:
                # 同拍已有背景，新背景会覆盖它——先落拍，保留两条指令
                flush_stage_only()
            pending.scene = payload.get('image', '') or ''
            if payload.get('with_'):
                pending.with_ = payload['with_']
            if payload.get('at'):
                # 背景自己的变换（`scene bg bg9_5 at bgfit`）要落到 scene_at。
                # 落到 `at`（立绘位置）会同时错两处：背景的 at 丢失，
                # 而这一拍后面的 show 会莫名其妙被套上 bgfit。
                pending.scene_at = ' '.join(payload['at'])
            pending.condition = stmt.condition or pending.condition
            pending_has_stage = True
            mark_stage('scene')
            mark_op('stage')
            # 换景 = 换了说话语境，`extend` 的显示归属到此为止（见下）
            last_speaker = ''

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
            mark_op('stage')

        elif kind == StatementKind.HIDE:
            items = _split_hide_items(pending.hide)
            items.append({'image': payload.get('image', '') or '',
                          'with': payload.get('with_', '') or ''})
            pending.hide = _join_hide_items(items)
            pending_has_stage = True
            mark_stage('hide')
            mark_op('stage')
            # 退场也算"说话人离开"：`A say → hide A → extend` 时，
            # 那句 extend 归属谁是不确定的（A 已经不在台上了）。
            # 宁可标成空（策划会去补），也不要安错人。
            last_speaker = ''

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
                mark_op('audio')
            else:
                if pending.sound or pending.sound_action == 'stop':
                    flush_stage_only()
                pending.sound = name
                pending.sound_action = action
                pending.sound_params = params
                pending_has_stage = True
                mark_op('audio')

        elif kind == StatementKind.VOICE:
            if pending.voice:
                flush_stage_only()
            pending.voice = payload.get('name', '') or ''
            pending_has_stage = True
            mark_op('voice')

        elif kind == StatementKind.PYTHON:
            # $ / python 逻辑进 CodeBlock，导出时原样回写，不再丢。
            code = (payload.get('code', '') or '').strip()
            if code:
                pending.code = _append_line(pending.code, code)
                pending.ordered.extend(('code', ln)
                                       for ln in _split_lines(code))
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
                pending.ordered.extend(('raw', ln)
                                       for ln in _split_lines(text))
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
            # 控制流走了，后面的 extend 不再属于任何已说话的 Beat
            last_speaker = ''

        elif kind == StatementKind.RETURN:
            flush_stage_only()
            # 一章结束：`extend` 不可能跨 label 续写，归属清零
            last_speaker = ''

    flush_stage_only()
    _assign_identity(beats)
    # 标记"解析来的 Beat"：导出时图像名要逐字还原，不做下划线改写。
    for b in beats:
        b._from_script = True
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
        beat.raw, beat.code,
        # raw/code 的交错顺序也算内容：顺序变了演出就变了
        repr(getattr(beat, 'ordered', None) or []),
        beat.music, beat.music_params, beat.music_action,
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
                          or _guess_project_root()),
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
    from stage_snapshot import snapshot_rows, snapshot_summary

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


def _guess_project_root():
    """
    猜一个项目根给缩略图定位用。

    Excel 导出时调用方没传 project_root，而资源表里的 Path 基准不统一，
    所以这里只给"当前工作目录"这个兜底；真实定位靠
    `_resolve_thumb_source` 的多种基准尝试。
    """
    return None


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
    from asset_registry import describe_asset

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


def _thumbnail_bytes(path, project_root=None):
    """
    把一张图缩成 PNG 字节；读不了返回 None（绝不抛异常）。

    **带磁盘缓存**：缩略图只跟「源文件路径 + mtime + 尺寸」有关，
    同一张图第二次导出直接用缓存。没有缓存时真实项目一次导出要几分钟
    （2902 张图逐张解码缩放），策划等不起。
    """
    if _PILImage is None:
        return None
    real = _resolve_thumb_source(path, project_root)
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


def _resolve_thumb_source(path, project_root=None):
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

    def emit_stage():
        _render_stage_from_fields(beat, emit,
                                  _resolver_for(audit, name_resolver),
                                  audit, emitted_stage)
        if beat.stage:
            from dsl import compile_stage
            for intent, code, status in compile_stage(beat.stage, symbols,
                                                      audit):
                if code in emitted_stage:
                    # 结构化列已出过同一条指令（Stage 原文被 hydrate 并回）
                    continue
                emit(code, '舞台', intent, status)

        # 旧表带过来的 Transform 列（现已取消）：当成这一拍的转场
        if beat.transform and not beat.with_:
            emit(f"with {beat.transform}", '舞台', beat.transform)

    # ---------------- 代码块（保真后门） ----------------
    def emit_code():
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

    def emit_audio():
        _render_audio_from_fields(beat, emit, audit)

    def emit_voice():
        if not beat.voice:
            return
        if audit is not None:
            from dsl import TODO as _TODO
            if audit.audio('voice', beat.voice) == _TODO:
                todo('语音', beat.voice,
                     f"缺少资源 语音 '{beat.voice}'")
                return
            emit(f"voice {_quote(audit.audio_ref('voice', beat.voice))}",
                 '语音', beat.voice)
        else:
            emit(f"voice {_audio_ref(beat.voice)}", '语音', beat.voice)

    # ---------------- 按**源码里的顺序**输出 ----------------
    # 这一节修的是「跨类别顺序」差异：`play music X` 写在 `show Y` 之前时，
    # 以前固定按"舞台→音频→语音"输出，音频会跑到立绘后面。
    # 同一次交互里最终画面一样，但回放/时间轴看到的顺序是错的——
    # 而顺序本身就是演出语义（"音乐先起还是立绘先亮"）。
    #
    # `op_order` 只记**舞台 / 音频 / 语音**三类；代码块（CodeBlock 后门）
    # 不在其中，所以按历史契约紧跟在舞台之后输出——它内部有自己更细的
    # `beat.ordered`（raw/code 交错顺序）负责保真。
    _ops = {'stage': emit_stage, 'audio': emit_audio, 'voice': emit_voice}
    order = [k for k in beat.order_ops if k in _ops]
    if order:
        # 老表/手工新增行可能只记了部分（甚至没记）——把漏掉的按历史顺序补在
        # 后面，**绝不因为顺序信息不全就丢掉操作**。
        order += [k for k in ('stage', 'audio', 'voice') if k not in order]
    else:
        order = ['stage', 'audio', 'voice']
    for kind in order:
        _ops[kind]()
        if kind == 'stage':
            # 代码块固定跟舞台走（历史契约，见上）
            emit_code()

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

    有符号表就用 `Symbols.image_spec_ok`——它比 `image_ok`（校验用）宽松：
    只要 `tag` 存在，`show ling base_arms_akimbo close_happy` 就算能跑，
    于是生成时**保留作者写法**，不会因为"资源表里没有这个属性组合"
    就被改写成下划线形态。判定结果决定写 `show eileen happy` 还是
    `show eileen_happy`——对 `tag attribute` 命名的项目，前者才跑得起来。
    """
    if name_resolver is not None:
        return name_resolver
    symbols = getattr(audit, 'symbols', None)
    if symbols is None:
        return None
    return lambda name: bool(symbols.image_spec_ok(name))


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

    从 .rpy 解析来的 Beat（`_from_script`）**逐字还原图像名**：作者的原文
    就是能跑的那份，不拿资源表的解析结果去改写它。
    """
    preserve = bool(getattr(beat, '_from_script', False))

    def art(name):
        return name if preserve else _emit_image_name(name, name_resolver)

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



# ===================== 生成安全门禁 =====================
#
# 为什么必须有这道闸（2026-09-28 实测事故）：
# 真实项目 `sr_general/game/chapter/` 里躺着 18 行 `show <中文名>`——
# 而 Ren'Py 规定图像 tag/属性只能是字母、数字、下划线（见官方 Quickstart），
# 那 18 行是**语法非法**的；同一份生成文件还和原文重复定义了 `label chapter1_4`。
# 根因是"立绘列与舞台列双写"（见 `compose_show_cell` / `compose_stage_extra`），
# 但只要生成器还能**静默把坏脚本写进 game/**，这个坑就会反复被人踩回来。
#
# 所以这里刻意做成"纯函数 + 明确分级"：
#   · block 级 = 确定会让游戏跑不起来 → 拒写；
#   · warn  级 = 可疑但不一定错 → 照写，但报出来。
# 宁可少报也不能把正常脚本拦下来（记忆文件里的校验分级原则同样适用）。

#: Ren'Py 允许的图像 tag / 属性 / transform 名：字母数字下划线（官方规则）。
#: 中文 tag 不是"风格问题"，是语法错误——所以按"非 ASCII 即非法"判。
_ASCII_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


@dataclass(frozen=True)
class GuardIssue:
    """生成安全门禁的一条发现。"""

    code: str
    level: str                  # 'block' | 'warn'
    message: str
    line_no: int = 0

    @property
    def is_blocking(self) -> bool:
        return self.level == 'block'

    def format(self) -> str:
        where = f'第 {self.line_no} 行' if self.line_no else '整体'
        return f'[{self.level}] {self.code}（{where}）：{self.message}'


class GenerationBlocked(Exception):
    """生成门禁拦下了落盘（含 block 级问题清单）。"""

    def __init__(self, out_path, issues):
        self.out_path = Path(out_path)
        self.issues = list(issues)
        detail = '\n'.join('  ' + i.format() for i in self.issues)
        super().__init__(
            f'生成的脚本有 {len(self.issues)} 条致命问题，已拒绝写入 '
            f'{self.out_path}：\n{detail}')

    def format(self) -> str:
        return str(self)

    def __str__(self):
        detail = '\n'.join('  ' + i.format() for i in self.issues)
        return (f'生成门禁拦下 {self.out_path}：'
                f'{len(self.issues)} 条致命问题\n{detail}')


def occupied_labels(project_root=None, exclude=()) -> set:
    """
    扫项目里**其他** .rpy 已经定义的 label 名（生成前用来判重名）。

    Ren'Py 会把 game/ 下所有 .rpy 当成"一个大脚本"（官方 Quickstart 原话），
    所以不同文件里重名 label 是硬错误。`build.discover_scripts` 已经排除了
    `tl/` 与 `*.generated.rpy`，正好复用它的口径。

    project_root 为空、或项目里没有 game/ 时返回空集合——**宁可少报**，
    不要让没有项目的单测/CLI 场景被误拦。
    """
    if not project_root:
        return set()
    try:
        from build import discover_scripts
    except Exception:
        return set()

    exclude = {Path(p).resolve() for p in (exclude or ()) if p}
    found = set()
    try:
        scripts = discover_scripts(project_root)
    except Exception:
        return set()
    for fp in scripts:
        try:
            if Path(fp).resolve() in exclude:
                continue
            found.update(_declared_labels(fp))
        except OSError:
            continue
    return found


def _declared_labels(path) -> set:
    """文件里声明的 label 名（含中文 label——Ren'Py 的 label 名允许非 ASCII）。"""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return set()
    return set(re.findall(r'^label\s+([^\s:#]+)', text, re.M))


@dataclass(frozen=True)
class _ImageRef:
    """生成文本里的一条舞台指令：动词 + 图像名 + 行号。"""

    verb: str
    image: str
    line_no: int


#: `scene` / `show` / `hide` 后面跟的那个"图像名"只取**第一个 token**：
#: `show ling happy at left` 的 tag 部分就是 `ling`，
#: 而 `show ling_close_happy` 整体是一个 token。
_STAGE_VERB_RE = re.compile(r'^(\s*)(show|hide|scene)\s+(\S+)(.*)$')

#: 图像名后面挂的标点：`show icon:`（Screen Language 的属性写法）要当成 `icon`。
_TRAILING_PUNCT = ':,;'

#: 出现这些字符说明这不是"一个图像名"而是**表达式**——
#: `Transform(zoom=scale)`、`config.x`、`image_info[0]` 都合法，一律放行。
#: 宁可少报：把程序手写的 UI 代码拦下来，比漏掉一行中文 tag 更贵。
_EXPR_CHARS = '([{.'


def _is_valid_ident(name: str) -> bool:
    """
    这个名字像"合法的 Ren'Py 图像名 / transform 名"吗？

    Ren'Py 的规则是「字母开头，只含字母数字下划线」（官方 Quickstart）。
    但真实项目里 `at` 后面还经常跟**表达式**（`Transform(zoom=scale)`、
    `eye_tracking_transform(...)`）和 Screen Language 的 `at transform:`，
    这些都不是标识符却完全合法——所以先剥标点，再对表达式整体放行。
    """
    text = (name or '').strip().lstrip('\ufeff')
    text = text.rstrip(_TRAILING_PUNCT)
    if not text:
        return True
    if any(ch in text for ch in _EXPR_CHARS):
        return True
    return bool(_ASCII_IDENT_RE.match(text))


def _image_refs(text: str) -> list:
    """抽出生成文本里所有的 show/hide/scene 图像名（跳过注释行）。"""
    refs = []
    for i, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        m = _STAGE_VERB_RE.match(line)
        if m:
            refs.append(_ImageRef(m.group(2), m.group(3), i))
    return refs


def generation_guard(text: str, out_path, *,
                     project_root=None,
                     occupied_labels: set = None) -> list:
    """
    生成安全检查。**纯函数、不写盘、无 UI 依赖**，所以能被无头测试直接调用。

    规则（每条都会带上原文行号，方便对着表改）：

      · `CJK_IN_IMAGE_TAG`（block）—— show/hide/scene 的图像名不是
        "字母/数字/下划线"，也就是 Ren'Py 语法上根本不存在这个图像。
        中文名出现在这里是**事故**，不是风格问题。
      · `CJK_IN_TRANSFORM`（block）—— `at <非标识符>`，同理。
      · `LABEL_COLLISION`（block）—— 要写的 label 在项目其他 .rpy 里已存在。
        这类文件一旦落进 game/，游戏直接起不来。
      · `UNRESOLVED_TODO`（warn）—— 生成结果里有 `# TODO: 缺少资源 …`。
        这条本身是设计内的兜底（编译不因缺资源而失败），
        它提醒的是"这份脚本还不完整，别当成成品发出去"。
    """
    issues = []
    out_path = Path(out_path) if out_path else None

    # —— 1. 图像名 / transform 名的合法性 ——
    for ref in _image_refs(text):
        if not _is_valid_ident(ref.image):
            issues.append(GuardIssue(
                'CJK_IN_IMAGE_TAG', 'block',
                f'`{ref.verb} {ref.image}`：图像名含非法字符'
                f'（Ren\'Py 只允许字母/数字/下划线）。'
                f'常见原因：把角色**显示名**当成了 image tag，'
                f'或某个结构化列与「舞台（高级）」列写了同一拍的两份内容。',
                ref.line_no))
    # at 子句单独扫一遍：图像名与变换名是两个不同的合法性域。
    # 注意 `at` 后面可以跟**多个**变换（`at left slide_in`），所以逐 token
    # 判会误报——基准是"整个列表里至少有一个是合法标识符就算有人管"；
    # 一个都没有（`at 左边`）才说明这是自由文本混进了代码。
    for i, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        parts = line.split()
        if 'at' not in parts:
            continue
        after_at = parts[parts.index('at') + 1:]
        stopwords = ('with', 'behind', 'onlayer', 'zorder', 'as')
        spec = []
        for tok in after_at:
            if tok in stopwords:
                break
            spec.append(tok)
        if spec and not any(_is_valid_ident(tok) for tok in spec):
            issues.append(GuardIssue(
                'CJK_IN_TRANSFORM', 'block',
                f'`at {" ".join(spec)}`：变换名没有一个合法'
                f'（Ren\'Py 的 transform 名必须是 Python 标识符）。',
                i))

    # —— 2. label 重名 ——
    if occupied_labels:
        for name in re.findall(r'^label\s+([^\s:#]+)', text, re.M):
            if name in occupied_labels:
                issues.append(GuardIssue(
                    'LABEL_COLLISION', 'block',
                    f'`label {name}` 在项目其他 .rpy 里已经定义过。'
                    f'Ren\'Py 把 game/ 下所有 .rpy 当同一个脚本，'
                    f'重名 label 会让游戏直接起不来。'
                    f'请把产物写到 build/ 目录，或改 label 名。'))

    # —— 3. TODO 未解决 ——
    todo_lines = [i for i, line in enumerate(text.splitlines(), start=1)
                  if '# TODO:' in line]
    if todo_lines:
        issues.append(GuardIssue(
            'UNRESOLVED_TODO', 'warn',
            f'生成结果里有 {len(todo_lines)} 条 `# TODO: 缺少资源`'
            f'（第 {"、".join(str(n) for n in todo_lines[:8])}'
            f'{"…" if len(todo_lines) > 8 else ""} 行）：'
            f'这些语句没有被生成，脚本还不完整。'))

    # —— 4. 产物位置：落在 Ren'Py 扫描目录里要提醒 ——
    # 只在"项目里真有 game/ 且产物确实写进它"时报：其余情况（临时目录、
    # 单测的 tmp_path、非 Ren'Py 项目）一律不报，否则会变成噪音。
    if project_root and out_path is not None:
        try:
            game_dir = (Path(project_root) / 'game').resolve()
            resolved_out = out_path.resolve()
            if game_dir.is_dir() and (
                    resolved_out == game_dir
                    or game_dir in resolved_out.parents):
                issues.append(GuardIssue(
                    'OUTSIDE_BUILD_DIR', 'warn',
                    f'产物写在 Ren\'Py 会扫描的 game/ 目录里（{out_path}）。'
                    f'建议写到 build/renpy/：放在 game/ 下的生成文件会和'
                    f'原文抢同一批 label。'))
        except OSError:
            pass

    return issues


@dataclass(frozen=True)
class GeneratedArtifact:
    """一次"生成到磁盘"的完整结果（给 UI 用的、可测的返回值）。

    为什么做成纯逻辑而不是写在 `graph_view` 里：
    `graph_view` 是 tkinter 界面，测试环境里只有桩，装配很容易悄悄接错
    （记忆文件里就有过 `command=` 指向不存在方法的先例）。
    所以"写到哪、过不过门禁、产物副本存哪"全部放在这里，
    UI 只负责把结果摆到弹窗上。
    """

    out_path: Path
    review_path: 'Path | None'
    written: bool
    issues: list

    @property
    def blocking(self) -> list:
        return [i for i in self.issues if i.is_blocking]

    @property
    def warnings(self) -> list:
        return [i for i in self.issues if not i.is_blocking]

    @property
    def blocked(self) -> bool:
        return bool(self.blocking)


def planned_output_path(table_path, script_path=None, project_root=None) -> Path:
    """
    决定生成的 .rpy 写到哪里。

    **不再写回 .rpy 旁边。** 以前是 `table.with_suffix('.generated.rpy')`，
    而表就在 game/ 里，于是产物和原文躺在同一个目录、抢同一批 label
    （Ren'Py 把 game/ 下所有 .rpy 当同一个脚本）——一次真实事故。
    有项目根就写 `<项目根>/build/renpy/`，否则退回表旁边。
    """
    stem = Path(script_path).stem if script_path else Path(table_path).stem
    if project_root:
        return Path(project_root) / 'build' / 'renpy' / f'{stem}.rpy'
    return Path(table_path).with_suffix('.generated.rpy')


def evaluate_generation(text: str, out_path, *, project_root=None,
                        occupied: set = None) -> list:
    """跑一遍生成门禁，返回问题清单。**纯函数、不写盘。**

    给"先看结果、问过用户再写"的调用方用（UI 的确认框就该是这个顺序）。
    """
    return generation_guard(text, Path(out_path), project_root=project_root,
                            occupied_labels=occupied)


def write_generation(text: str, out_path, *, review_on_block: bool = True):
    """
    落盘，并保证"绝不把会让游戏跑不起来的脚本写进目标路径"。

      · 无 block 级问题 → 写 `out_path`，`written=True`；
      · 有 block 级问题 → **不写目标文件**，改写 `<stem>.blocked.rpy`
        供人对比，`written=False`、`review_path` 指向副本。

    为什么拦下也要留副本：只报错不给人看产物，等于让人猜哪里错了；
    但绝不能写进目标路径（那正是事故的形态）。

    调用方要"先问后写"就直接用 `evaluate_generation` + 自己写，
    这个函数是给"已经确认过，但还要兜一层底"的路径用的。
    """
    out_path = Path(out_path)
    issues = generation_guard(text, out_path, project_root=None)
    blocking = [i for i in issues if i.is_blocking]
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if blocking and review_on_block:
            review = out_path.with_suffix('.blocked.rpy')
            review.write_text(text, encoding='utf-8')
            return GeneratedArtifact(out_path, review, False, issues)
        if blocking:
            return GeneratedArtifact(out_path, None, False, issues)
        out_path.write_text(text, encoding='utf-8')
        return GeneratedArtifact(out_path, None, True, issues)
    except OSError:
        # 写不出去（权限/占用）不该把门禁结论一起吞掉
        return GeneratedArtifact(out_path, None, False, issues)


def write_beats_to_rpy(beats: list, output_path,
                       label_name: Optional[str] = None,
                       registry=None) -> str:
    """
    渲染并落盘，返回生成的文件路径。

    **落盘前先过生成安全门禁**（见 `generation_guard`）：命中 block 级问题就
    拒写并抛 `GenerationBlocked`，绝不把一份会让游戏跑不起来的脚本写进项目。

    要"先看结果、问过用户再写"的调用方（例如 UI 的确认框）请用
    `evaluate_generation` + `write_generation` 自己编排顺序。
    """
    text = render_beats_to_rpy(beats, label_name=label_name,
                               registry=registry)
    output_path = Path(output_path)

    occupied = occupied_labels(project_root=_guess_project_root(),
                               exclude={output_path})
    issues = generation_guard(text, output_path, occupied_labels=occupied)
    blocking = [i for i in issues if i.is_blocking]
    if blocking:
        raise GenerationBlocked(output_path, blocking)

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
                      label: str = '', with_thumbnails: bool = False):
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
    export_beats_to_excel(beats, output_path, registry=registry,
                          include_thumbnails=with_thumbnails)
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
