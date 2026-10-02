"""
storygraph.story.beat_model
===========================
**Beat（演出单元）数据结构**，以及它与表格单元格之间的编解码原语。

一个 Beat = 一行演出（"一句台词 + 这一拍要做的事"）。这里放三样东西：

  · 列契约：`BEAT_FIELDS`（列顺序唯一真源）、中文表头、下拉列、老表迁移列名
  · `Beat`：数据类本体（`to_row` / `from_row` 逐列对应）
  · 单元格原语：`compose_*` 把结构化字段写成格内文本，`_split_*` 反向拆开

刻意**不含**渲染与 Excel IO —— 那些在 beat_render / excel_export 里。
这一层只依赖 core 与 excel.cells，是全 story 层的地基。
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional

from storygraph.core.statement import MARKER





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
    #: 本拍对应的**源文件行区间**（1-based，闭区间）。
    #: 只用于**增量导出**（把原文件对应区间替换成新生成的行），
    #: 是运行时元数据：不入表、不进 ContentHash，也不影响渲染。
    src_first: int = 0
    src_last: int = 0
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
        #
        # **参数列始终从结构化字段走 `compose_audio_cell`**（2026-09-28 修）：
        # 原来文本列优先时把参数硬编码成 `''`，于是
        # `stop music fadeout 1.0`（文本列 = `[停止音乐]`）的参数**静默丢失**，
        # 读回来变成裸 `stop music`。真实章节里 6 条全中招。
        _bgm_cell, bgm_params = compose_audio_cell(self, 'music')
        _sfx_cell, sfx_params = compose_audio_cell(self, 'sound')
        bgm = self.bgm or _bgm_cell
        sfx = self.sfx or _sfx_cell
        return [
            self.line_id, self.seq, self.label, self.kind,
            self.speaker, self.comment, self.text,
            compose_scene_cell(self),
            compose_show_cell(self),
            self.at, self.with_,
            compose_hide_cell(self),
            bgm, bgm_params, sfx, sfx_params, self.voice,
            stage, compose_code_cell(self),
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
        """
        本拍操作的**类别**顺序（`['audio', 'stage']`）——`order_tokens` 的粗粒度视图。

        保留它是因为已有调用方（快照/校验/测试）只关心"音频在舞台前还是后"。
        要逐条还原请用 `order_tokens`。
        """
        cats = []
        for t in self.order_tokens:
            c = 'stage' if t in ('scene', 'show', 'hide') else t
            if c not in cats:
                cats.append(c)
        return cats

    @property
    def order_tokens(self) -> list:
        """
        本拍全部操作的**verb 级**顺序，用于逐条还原（保住同类内交错）。

        形如 `['scene', 'audio', 'show']`——这正是
        `scene bg X / play music Y / show ling happy` 的源码顺序。

        为什么不能只记类别（`stage,audio`）：那样渲染时会得到一个
        "整块舞台 + 整块音频"，把 `play` 挤到 `show` 后面
        （demo ch01 实测：`play music night` 原本在 `show eileen happy` 之前）。
        只记类别 + 成组输出 = 顺序错了但看起来"差不多"，
        而顺序本身是演出语义。

        兼容两种存法：
          · **verb 级**（新）：`scene,audio,show` —— 直接用；
          · **类别级**（老表）：`stage,audio` —— 用 `stage_order` 把
            `stage` 展开成具体 verb，尽量还原。
        完全没有顺序信息时返回空，让调用方走历史固定顺序。
        """
        raw = [t.strip() for t in (self.op_order or '').split(',') if t.strip()]
        if not raw and not (self.stage_order or '').strip():
            return []

        stage_seq = [t.strip() for t in (self.stage_order or '').split(',')
                     if t.strip()]
        out = []
        used = 0
        seen_audio = seen_voice = False
        for t in raw:
            if t in ('scene', 'show', 'hide'):
                out.append(t)
                used += 1
            elif t == 'stage':
                # 老格式：展开 StageOrder 里还没用掉的下一个 verb
                if used < len(stage_seq):
                    out.append(stage_seq[used])
                    used += 1
                else:
                    for v in ('scene', 'show', 'hide'):
                        if v not in out:
                            out.append(v)
            elif t == 'audio' and not seen_audio:
                out.append('audio')
                seen_audio = True
            elif t == 'voice' and not seen_voice:
                out.append('voice')
                seen_voice = True
        # stage_order 里还剩的 verb 补在最后
        for v in stage_seq[used:]:
            out.append(v)
        return out

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
    from storygraph.story.dsl import Symbols, stage_word_if_lossless
    symbols = symbols if isinstance(symbols, Symbols) else None

    ops = []
    if beat.scene:
        line = f"scene {beat.scene}"
        # 背景自己的变换是 `scene_at`（`scene bg bg9_5 at bgfit`）。
        # 这里曾错用 `beat.at`——那是**立绘**的位置，于是同一张表里
        # "Stage 列显示的"和"生成的 .rpy"对不上（背景变换丢失、
        # 立绘位置串到背景上）。渲染走 `beat_render._stage_lines`，
        # 两边必须同源。
        if beat.scene_at:
            line += f" at {beat.scene_at}"
        if beat.with_:
            line += f" with {beat.with_}"
        ops.append(('scene', line))
    for item in _split_show_items(beat.show):
        if item.get('clause'):
            ops.append(('show', f"show {item['clause']}"))
            continue
        line = f"show {item['image']}"
        # 立绘的位置：自带 `@` 优先，其次才是整拍的「位置」列——
        # 与 `_stage_lines` 一致（少这一档就会出现"表里没位置、生成的有"）。
        at = (item.get('at') or '').strip() or (beat.at or '').strip()
        if at:
            line += f" at {at}"
        ops.append(('show', line))
    for item in _split_hide_items(beat.hide):
        line = f"hide {item['image']}"
        if item.get('with'):
            line += f" with {item['with']}"
        ops.append(('hide', line))
    # 转场没背景可挂时挂哪儿：最后一条立绘；连立绘都没有就单独成行。
    # 与 `beat_render._stage_lines` 一致 —— 那边是生成 .rpy 的真源，
    # 少这一档就会出现"Stage 列看不到转场、生成的脚本里有"。
    if beat.with_ and not beat.scene:
        show_at = [i for i, (v, _l) in enumerate(ops) if v == 'show']
        hide_at = [i for i, (v, _l) in enumerate(ops) if v == 'hide']
        if show_at:
            i = show_at[-1]
            ops[i] = ('show', ops[i][1] + f" with {beat.with_}")
        elif not hide_at:
            ops.append(('show', f"with {beat.with_}"))
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

    **参数列必须照写，哪怕动作是停止**（2026-09-28 实测的数据丢失）：

        `stop music fadeout 1.0`  ——(导出 xlsx)→ 音乐=`（停止音乐）`、参数=空
                                  ——(读回)→ `stop music`
                                  ——(导出 .rpy)→ **`stop music`（fadeout 丢了）**

    原来这里在伪值分支直接 `return back.get(action, ''), ''`，
    把 `params` 吃掉了；读回时参数列是空的，`_fill_audio_from_cells`
    又用空参数覆盖掉已经解析出来的 `fadeout 1.0`。
    真实章节 `chapter1_3` 里 **6 条 `stop music fadeout 1.0` 全中招**。
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
        return back.get(action, ''), params or ''
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
        from storygraph.story.dsl import parse_audio
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
    # 参数列为空时**不要**覆盖已经解析出来的参数（老表没这列）。
    # 2026-09-28：`stop music fadeout 1.0` 因为这一句把 fadeout 丢了。
    keep = (params or '').strip()
    if not keep:
        keep = (beat.music_params if channel == 'music'
                else beat.sound_params) or ''
    if channel == 'music':
        beat.music, beat.music_params, beat.music_action = \
            name, keep, action
    else:
        beat.sound, beat.sound_params, beat.sound_action = \
            name, keep, action



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
    from storygraph.story.dsl import Symbols, stage_word_if_lossless
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


#: 内部标记：`$code`（`$` 后无空格）这一位用它承载（见 Statement.make_python）。
#: **绝不能写进 Excel** —— openpyxl 拒绝控制字符：
#: `IllegalCharacterError: ... cannot be used in worksheets`（实测踩过）。
#:
#: 2026-10-05：改为从 `core.statement` 取（约定只有一份，见那里的 `MARKER`）。
_MARKER = MARKER


def compose_code_cell(beat: 'Beat') -> str:
    """
    写进「代码」列**单元格**的文本 = `compose_code` 且剥掉内部标记。

    渲染走 `compose_code`（保留标记，才能还原 `$code` 紧写形式）；
    写表走这里（`)` 是控制字符，openpyxl 会直接报错）。
    两者只在"是否保留标记"这一点上不同，内容完全一致。
    """
    return (compose_code(beat) or '').replace(_MARKER, '')



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
            # `$x`（`$` 后无空格）在**原文件里**很常见，而生成器默认写 `$ x`。
            # 逐字节保留这一位，否则每个 `$` 行都成一个 diff 块。
            # 用标记前缀承载（见 `Statement.make_python` / `core.statement.MARKER`）。
            if s.startswith('$') and not s[1:2].isspace() and len(s) > 1:
                marked = MARKER + s[1:]
                code.append(marked)
                ordered.append(('code', marked))
            else:
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



def show_item_part(item: dict) -> str:
    """
    一个 show 项 → **规范片段**（`图像@变换` / 复杂子句原文 / 纯图像名）。

    这是 show 项在单元格里的唯一编码形式，`_join_show_items` 与
    `show_item_parts` 都用它 —— 编码只有一份，比较才有意义。
    """
    if item.get('clause'):
        return ' '.join(str(item['clause']).split())
    image = ' '.join(str(item.get('image') or '').split())
    at = str(item.get('at') or '').strip()
    if not image:
        return ''
    return f'{image}@{at}' if at else image


def show_item_parts(text: str) -> set:
    """
    show 文本 → 它包含的**规范片段集合**（给"这条 Stage 指令是否已被
    结构化列承载"用，见 `beat_render` 的舞台尾段）。
    """
    return {p for p in (show_item_part(it) for it in _split_show_items(text)) if p}


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
        part = show_item_part(it)
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



def hide_item_part(item: dict) -> str:
    """一个退场项 → 规范片段（`图像@转场` / 纯图像名）。"""
    image = ' '.join(str(item.get('image') or '').split())
    w = str(item.get('with') or '').strip()
    if not image:
        return ''
    return f'{image}@{w}' if w else image


def hide_item_parts(text: str) -> set:
    """退场文本 → 规范片段集合（同 `show_item_parts`）。"""
    return {p for p in (hide_item_part(it) for it in _split_hide_items(text)) if p}


def _join_hide_items(items: list) -> str:
    """
    `hide` 项 → `ye; lan@dissolve`。**同样要做"同名同参数去重"**。

    为什么必须去重（2026-09-30 真实数据实测）：`hydrate_from_dsl` 是追加
    语义，而 Stage 列常常把结构化列的内容原样又写了一遍。show 那条路早就
    有去重（见 `_join_show_items`），hide 这条**漏了**，于是同一条退场指令
    被吸两次就翻倍：`hide cg03` → `cg03; cg03`。**逐句校对必然踩中**
    （chapter1_3 实测 11 拍被改写）。
    """
    parts, seen = [], set()
    for it in items:
        part = hide_item_part(it)
        if not part or part in seen:
            continue
        seen.add(part)
        parts.append(part)
    return '; '.join(parts)



def _append_line(existing: str, line: str) -> str:
    return (existing + '\n' + line) if existing else line




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



def _split_lines(text: str) -> list:
    """多行单元格（Comment/Raw/CodeBlock）按行拆开，去空行。"""
    return [ln.rstrip() for ln in (text or '').split('\n') if ln.strip()]



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
