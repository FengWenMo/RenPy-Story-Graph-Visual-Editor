# -*- coding: utf-8 -*-
"""
分级删除 · **清空一张卡片**（用户 2026-10-25）。

    > 当我删掉卡片的时候……我会顺带把一些代码块什么的也删掉……
    > 能不能说：只删掉句子等……而不删掉代码块呢……？
    > 这样有没有意义？你帮我想想该如何分……

## 为什么不是"按行删"

一张卡片 = **一句话 + 它附带的代码行**。导出本来就是"按拍的字段渲染成行"，
所以"删什么"落在**字段**上就够了 —— 不需要去动源文件的行号、对位、块结构
（那些是补丁的活，工具已经为此写了三轮审计）。

## 三档（前两档在这里，第三档是现成的 `delete_beat`）

| 档 | 清掉什么 | 留下什么 |
|---|---|---|
| `text` | 台词 / 菜单字（`台词`·`菜单` 通道） | **代码块、舞台、音频** 全留 |
| `text_stage` | 加上舞台指令与音频（`舞台`·`音乐`·`音效`·`语音`） | **代码块**（含原样块）全留 |
| `whole` | —— 就是"整拍删除"，不归这里 | —— |

## 拿什么保证"没多删一个字"

清空是**在副本上做的**，做完把副本再渲染一遍，然后比：

    keep_before == keep_after      ← 逐行、逐字节，列表相等

`keep_*` 是"不该被清掉的那些通道渲染出来的行"。这条判据一旦成立，
就证明了"除了那几个通道，产物一个字都没动"——不靠我枚举字段自觉。
枚举漏了字段 → 校验红 → **拒绝**（宁可不动，也不做半个）。

## 什么卡不许清空

* **控制流卡**（`call` / `jump` / `return`）：那是图上的边 / 出口，
  删了等于改流程，只能整拍删；
* **块头**（`menu:` / `while` / `for` / 自定义语句头 / 原样块头）：
  清它等于把骨架拆了，只能整拍删（连块体那条显式路径）；
* **清完什么都不剩**的卡（比如本来就只有一句台词）：那本来就是"整拍删"，
  别造空卡片。
"""
from __future__ import annotations

from copy import copy
from dataclasses import dataclass, field

#: 档位（`whole` 只用于界面排序与说明，本模块不执行）
MODE_TEXT = 'text'
MODE_TEXT_STAGE = 'text_stage'
MODE_WHOLE = 'whole'

#: 界面顺序 + 人话（三档都在这里，界面直接读）
MODES = (
    (MODE_TEXT, '只清空文字', '代码块和舞台都留着，只把"这句话"去掉'),
    (MODE_TEXT_STAGE, '清空文字和舞台', '连 show / 音乐一起去掉，**代码块留着**'),
    (MODE_WHOLE, '整个删掉', '连代码块一起删（现在的行为）'),
)

#: 本模块能执行的两档
CLEARABLE = (MODE_TEXT, MODE_TEXT_STAGE)

#: 渲染 trace 里的**通道标签**（与 `beat_render` 的 `emit(..., '通道')` 一一对应）
TEXT_SOURCES = frozenset({'台词', '菜单'})
STAGE_SOURCES = frozenset({'舞台'})
AUDIO_SOURCES = frozenset({'音乐', '音效', '语音'})
CODE_SOURCES = frozenset({'代码块', '原样块'})

#: 各档清掉的通道
_DROP_SOURCES = {
    MODE_TEXT: TEXT_SOURCES,
    MODE_TEXT_STAGE: TEXT_SOURCES | STAGE_SOURCES | AUDIO_SOURCES,
}

#: 各档要清零的字段（**校验会兜住枚举遗漏**，见模块 docstring）
_TEXT_FIELDS = ('text', 'say_tail')
_STAGE_FIELDS = ('scene', 'stage', 'show', 'hide', 'at', 'scene_at',
                 'with_', 'transform', 'stage_order', 'op_order')
_AUDIO_FIELDS = ('bgm', 'sfx', 'music', 'sound', 'voice',
                 'music_params', 'sound_params')
_FIELDS = {
    MODE_TEXT: _TEXT_FIELDS,
    MODE_TEXT_STAGE: _TEXT_FIELDS + _STAGE_FIELDS + _AUDIO_FIELDS,
}

#: 这些字段是**列表**，清空给 `[]` 而不是 `''`
_LIST_FIELDS = frozenset({'ordered', 'code_blocks'})


@dataclass
class ClearPlan:
    """清空一张卡片的**纯计划**（不改传进来的 beat）。"""

    ok: bool = False
    mode: str = MODE_TEXT
    #: 会被清零的字段名（回执要说出来，撤销也要用它还原）
    fields: tuple = ()
    #: 将**消失**的行（产物里）
    drop_lines: list = field(default_factory=list)
    #: 将**留下**的行（产物里，逐字节）
    keep_lines: list = field(default_factory=list)
    #: 不能清的原因（`ok=False` 时非空）
    blocking: str = ''
    #: 清空之后这张卡还剩什么通道（人话）
    remains: str = ''

    def to_dict(self) -> dict:
        return {'ok': self.ok, 'mode': self.mode,
                'fields': list(self.fields),
                'drop_lines': list(self.drop_lines),
                'keep_lines': list(self.keep_lines),
                'blocking': self.blocking, 'remains': self.remains}


def normalize_mode(mode) -> str:
    text = str(mode or '').strip().lower()
    if text in (MODE_TEXT, 'text_only', 'sentence', 'say'):
        return MODE_TEXT
    if text in (MODE_TEXT_STAGE, 'stage', 'text+stage', 'text_and_stage'):
        return MODE_TEXT_STAGE
    if text in (MODE_WHOLE, 'all', 'delete'):
        return MODE_WHOLE
    return MODE_TEXT


def _render_with_sources(beat) -> list:
    """这一拍渲染出来的 `[(行, 通道)]`（与导出一模一样的路）。"""
    from storygraph.story.beat_render import render_beat_to_rpy
    trace: list = []
    render_beat_to_rpy(beat, trace=trace)
    return [(str(t.get('code') or ''), str(t.get('source') or ''))
            for t in trace]


def why_not(beat) -> str:
    """这张卡为什么不能"只清空"？能清返回空串。"""
    from storygraph.story.flow import parse_flow
    try:
        if parse_flow(beat) is not None:
            return ('这是控制流拍（call / jump）—— 它是图上的边，'
                    '只能整个删掉（或者在图上去掉这条线）')
    except Exception:                      # noqa: BLE001 - 认不出就按普通拍走
        pass
    # `parse_flow` 只认 `kind in ('narration','flow')`；手工造的 `kind='say'`
    # 控制流卡它认不出 —— 但那种卡同样不能"只清空"（清完什么也不剩）。
    # 所以这里再看一眼**单行的 raw**：
    #   · `call screen X` **不算**控制流（那是界面调用，属于代码通道），
    #     所以 `call` 要看第二个词。
    raw = str(getattr(beat, 'raw', '') or '').strip()
    rows = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(rows) == 1:
        words = rows[0].split(None, 1)
        verb = words[0] if words else ''
        rest = words[1].strip() if len(words) > 1 else ''
        if verb in ('jump', 'return'):
            return (f'这是控制流拍（{verb} 出口）—— 它是流程的一部分，'
                    '只能整个删掉（或者在图上去掉这条线）')
        if verb == 'call' and not rest.startswith('screen'):
            return ('这是控制流拍（call 调用）—— 它是图上的边，'
                    '只能整个删掉（或者在图上去掉这条线）')

    # 块头：清它等于把骨架拆了（块体还在，却没有头）
    for attr, label in (('opaque', '原样块（工具不认识的块语句）'),
                        ('spec_head', '自定义语句头'),
                        ('loop_header', '循环块头（while / for）'),
                        ('camera_header', 'camera 块头')):
        if str(getattr(beat, attr, '') or '').strip():
            return f'这是{label} —— 清掉它会把块体和头拆开，只能整个删掉'
    try:
        from storygraph.story.beat_model import KIND_MENU
        if str(getattr(beat, 'kind', '') or '') == KIND_MENU:
            return ('这是菜单头 —— 清掉它，下面的选项就变成块外的普通台词，'
                    '只能整个删掉（或连块体一起删）')
    except Exception:                      # noqa: BLE001
        pass
    return ''


def plan_clear(beat, mode=MODE_TEXT) -> ClearPlan:
    """
    算一份"清空这张卡"的计划 —— **不改传进来的 `beat`**。

    自校验（本模块的核心判据）：在**副本**上清空 → 再渲染 → 要求
    `keep_before == keep_after`。不相等 = 我漏了字段 → 拒绝，不产出半个结果。
    """
    mode = normalize_mode(mode)
    plan = ClearPlan(mode=mode)
    if mode not in CLEARABLE:
        plan.blocking = '这一档不是"清空"（整拍删除走 delete_beat）'
        return plan
    bad = why_not(beat)
    if bad:
        plan.blocking = bad
        return plan

    before = _render_with_sources(beat)
    drop_sources = _DROP_SOURCES[mode]
    drop_lines = [ln for ln, src in before if src in drop_sources]
    keep_before = [ln for ln, src in before if src not in drop_sources]
    if not drop_lines:
        plan.blocking = ('这一拍里没有可清的内容（文字 / 舞台都已经空了）—— '
                         '要删就整个删掉')
        return plan
    if not keep_before:
        plan.blocking = ('这一拍除了这些就没有别的了 —— 清空等于删掉它。'
                         '要用「整个删掉」吗？')
        return plan

    clone = copy(beat)
    fields = _FIELDS[mode]
    for name in fields:
        try:
            setattr(clone, name, [] if name in _LIST_FIELDS else '')
        except AttributeError:             # 老版本字段名不存在：跳过（校验会兜）
            continue

    after = _render_with_sources(clone)
    keep_after = [ln for ln, _src in after]
    if keep_after != keep_before:
        plan.blocking = ('清空之后其余行变了（工具内部不一致）—— 为了不误删，'
                         '这次没有动。请把这一拍发给我看看')
        plan.blocking += (f'\n期望：{keep_before}\n实际：{keep_after}')
        return plan

    plan.ok = True
    plan.fields = fields
    plan.drop_lines = drop_lines
    plan.keep_lines = keep_after
    plan.remains = _describe_remains(after)
    return plan


def _describe_remains(rows) -> str:
    """清空之后还剩什么通道 → 一句人话（列表/卡片上标"空拍·只剩代码"用）。"""
    kinds = {src for _ln, src in rows}
    if not kinds:
        return ''
    if kinds <= CODE_SOURCES:
        return '只剩代码'
    names = []
    if kinds & CODE_SOURCES:
        names.append('代码')
    if kinds & STAGE_SOURCES:
        names.append('舞台')
    if kinds & AUDIO_SOURCES:
        names.append('音乐/音效')
    if kinds & TEXT_SOURCES:
        names.append('文字')
    return '只剩' + '、'.join(names)


def apply_clear(beat, mode=MODE_TEXT) -> dict:
    """
    按计划就地清空 `beat`，返回计划本身（`ok=False` 时一个字段都没动）。

    调用方（`session.clear_beat`）负责撤销栈与拍号；这里只管字段。
    """
    plan = plan_clear(beat, mode)
    if not plan.ok:
        return plan.to_dict()
    for name in plan.fields:
        try:
            setattr(beat, name, [] if name in _LIST_FIELDS else '')
        except AttributeError:             # noqa: PERF203 - 见 plan 的说明
            continue
    return plan.to_dict()


def snapshot_fields(beat, names) -> dict:
    """清空之前把要动的字段存一份（撤销用）。"""
    out = {}
    for name in names or ():
        try:
            value = getattr(beat, name)
        except AttributeError:
            continue
        out[name] = list(value) if isinstance(value, list) else value
    return out


def restore_fields(beat, saved: dict) -> None:
    """把 `snapshot_fields` 存下来的值放回去（撤销用）。"""
    for name, value in (saved or {}).items():
        try:
            setattr(beat, name, list(value) if isinstance(value, list) else value)
        except AttributeError:             # noqa: PERF203
            continue


__all__ = ['CLEARABLE', 'CODE_SOURCES', 'ClearPlan', 'MODES', 'MODE_TEXT',
           'MODE_TEXT_STAGE', 'MODE_WHOLE', 'apply_clear', 'normalize_mode',
           'plan_clear', 'restore_fields', 'snapshot_fields', 'why_not']


# ----------------------------------------------------------------------
#  列表上的"空拍"标记（要 O(1)，不能每行都渲染一遍）
# ----------------------------------------------------------------------

def empty_note(beat) -> str:
    """
    这张卡"清空过"没有？清过就返回一句标记（否则空串）。

    ⚠ **不许在这里调 `render_beat_to_rpy`**：列表一屏几百行，
    每行渲染一遍会把翻页拖成卡顿（真实项目 700+ 拍）。
    所以只看字段（O(1)），不渲染 —— 判据是"文字空了、别的还在"。
    """
    if str(getattr(beat, 'text', '') or '').strip():
        return ''
    code = ' '.join(str(getattr(beat, name, '') or '')
                    for name in ('code', 'raw', 'opaque', 'spec_head'))
    stage = ' '.join(str(getattr(beat, name, '') or '')
                     for name in ('stage', 'scene', 'show', 'hide',
                                  'with_', 'camera_header'))
    audio = ' '.join(str(getattr(beat, name, '') or '')
                     for name in ('bgm', 'sfx', 'voice'))
    left = [n for n, v in (('代码', code), ('舞台', stage), ('音乐', audio))
            if v.strip()]
    if not left:
        return ''                       # 什么都没剩：那是"整拍删"的事
    return '空拍·只剩' + '、'.join(left)
