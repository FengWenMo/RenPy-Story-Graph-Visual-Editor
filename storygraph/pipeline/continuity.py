# -*- coding: utf-8 -*-
"""
storygraph/pipeline/continuity.py
=================================
**跨章连续性体检（只读）**：上一章留下的背景 / 立绘 / CG / BGM，
下一章（和玩家看到的第一句台词）有没有接住。

为什么要它
----------
真实项目 `sr_general` 实测：1.4 章末还在播 `夜晚，断电之后…mp3`、背景是
`scene black`，2.1 整章一条音乐/舞台语句都没有 —— 玩家接着听那首曲子、
盯着黑屏看完整章。这种"章与章之间"的问题，单章预览和项目体检都看不见：
它们各自只看一章（或只看语法），没人回答"上一章演完，下一章开场是什么样"。

判据（用户 2026-10-28 两轮澄清后的最终口径：**只看后继的单入边**）
----------------------------------------------------------------------
**N 恰好只有一条入边 → 判 P→N 的继承；不看前驱出边。**

    N 单入边 → 判（哪怕 P 是分支点，各分支章都从**同一份** P 的章末
                状态出发 —— 这是确定的，不是猜）
    N 多入边（分支汇聚）→ **不判**；各前驱章末状态当事实列出来，不合并、
                不排序、不猜"反正一样"
    环 / 动态跳转 / Python 音频 / 未建模声道 / 旁支 / 入口 → 不判

为什么先前版本要求"两边都只有一条边"（2026-10-28 第一轮）又改回来：
第一轮按用户"分支后第一章不看"的口径做了保守版，第二轮用户澄清
**"本意就是只要节点单入边就判、不管前驱出边"** —— 分支点自己的出口状态
是确定的（它演出完是什么样，不取决于玩家接下来选哪条），所以每个分支
后代继承的都是同一份状态；判它符合"尽最大程度保留详细信息"的要求。

三条硬边界
----------
  · **不写盘**：不碰 `.rpy` / `.xlsx` / `.rpyc`，不改项目一个字节；
  · **不自动修**：只告诉人"去哪一章、哪一拍、该看什么"，改不改是人的决定；
  · **不改舞台语义**：跨章状态用 `story.stage_snapshot.stage_exit_state`
    推导（与预览/快照同一套规则），不另写一套"推断"。
"""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.core.statement import StatementKind as K
from storygraph.parsing.discover import discover_scripts
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_model import (
    KIND_EXTEND,
    KIND_MENU,
    KIND_MENU_OPTION,
    KIND_NARRATION,
    KIND_SAY,
    menu_caption_text,
    split_hide_items,
    split_show_items,
)
from storygraph.story.exits import ENTRY_LABELS
from storygraph.story.stage_snapshot import EXIT_STATE_KEYS, stage_exit_state

#: 有这些语句的 label 才算"内容章"（演出真的发生在里面）。
CONTENT_KINDS = frozenset({
    K.SAY, K.EXTEND, K.SHOW, K.SCENE, K.HIDE, K.PLAY, K.VOICE,
})

#: 动态跳转的几种写法（静态判不出去哪）。
_DYNAMIC_TARGET_RE = re.compile(r'^expression\b|^renpy\.', re.IGNORECASE)

#: 章节里直接调 Python 音频 API 的写法 —— 工具**判不出来**，单独列。
PYTHON_AUDIO_RE = re.compile(
    r'\brenpy\s*\.\s*(music|sound|audio)\s*\.', re.IGNORECASE)

#: 已建模的音频通道（`play ambience` 这类未建模声道单独列）。
MODELED_CHANNELS = frozenset({'music', 'sound', 'voice'})

#: 元素 → 人话（报告用）。
ELEMENT_ZH = {
    'music': '音乐',
    'scene': '背景',
    'actors': '立绘',
    'layers': 'CG/图层',
}

#: 问题/提示的级别。
LEVEL_WARN = 'warn'
LEVEL_INFO = 'info'

#: 不判定的原因分类。
UND_BRANCH = 'branch'
UND_MERGE = 'merge'
UND_CYCLE = 'cycle'
UND_DYNAMIC = 'dynamic'
UND_PYTHON_AUDIO = 'python_audio'
UND_CHANNEL = 'unmodeled_channel'
UND_SIDE = 'side_branch'
UND_ENTRY = 'entry_unknown'
UND_CONDITION = 'condition'
UND_INTERNAL = 'internal_flow'
UND_COMPLEX = 'complex_route'
UND_NO_DIALOGUE = 'no_dialogue'
UND_UNREACHED = 'unreached'
UND_FALLTHROUGH = 'fallthrough'

_LEVEL_ORDER = {LEVEL_WARN: 0, LEVEL_INFO: 1}


# ======================================================================
#  数据结构
# ======================================================================

def _unknown_state() -> dict:
    """"什么都不知道"的入口状态（给链根 / 循环 / 汇聚用）。"""
    return {
        'scene': '', 'actors': [], 'layers': {}, 'music': '', 'voice': '',
        'unknown': set(EXIT_STATE_KEYS),
    }


def _state_to_dict(state: dict) -> dict:
    """状态 → 可 JSON 化的形态（`unknown` 集合 → 排序列表）。"""
    out = {}
    for key in EXIT_STATE_KEYS:
        value = state.get(key)
        if key == 'actors':
            out[key] = [[str(a), str(at)] for a, at in (value or ())]
        elif key == 'layers':
            out[key] = {str(k): str(v) for k, v in (value or {}).items()}
        else:
            out[key] = str(value or '')
    out['unknown'] = sorted(str(x) for x in (state.get('unknown') or ()))
    return out


def _short_image(name: str) -> str:
    """图像显示短名：`bg bg9_2` / `cg cg07` → 去掉重复的 tag 前缀。"""
    text = str(name or '').strip()
    parts = text.split(None, 1)
    if len(parts) == 2 and parts[0].lower() in (
            'bg', 'background', 'cg', 'ui', 'movie', 'video', 'overlay'):
        return parts[1]
    return text


def _short_audio(name: str) -> str:
    """音频名 → 显示用的短名（去目录、保留文件名；判据仍用全名）。"""
    text = str(name or '').strip().replace('\\', '/')
    if '/' in text:
        text = text.rsplit('/', 1)[1]
    return text


def _state_summary(state: dict) -> str:
    """
    章末状态 → 一句话（报告用）。

    未知元素**明说"未知"**，绝不显示成空 —— 空串在这里是"没有任何音乐/
    没有背景"这个**确定的事实**，两者混在一起就会骗人。
    """
    if not state:
        return '（未知）'
    unknown = set(state.get('unknown') or ())
    parts = []
    if 'scene' in unknown:
        parts.append('背景未知')
    elif state.get('scene'):
        parts.append(f"背景 {_short_image(state['scene'])}")
    else:
        parts.append('无背景')
    if 'music' in unknown:
        parts.append('BGM 未知')
    elif state.get('music'):
        parts.append(f"BGM {_short_audio(state['music'])}")
    else:
        parts.append('BGM 静音')
    if 'actors' in unknown:
        parts.append('立绘未知')
    else:
        actors = state.get('actors') or []
        if actors:
            parts.append('立绘 ' + '、'.join(str(a[0]) for a in actors))
        else:
            parts.append('立绘 0')
    layers = state.get('layers') or {}
    if layers:
        parts.append('CG ' + '、'.join(_short_image(v)
                                       for v in layers.values()))
    return ' · '.join(parts)


@dataclass
class Chapter:
    """一个内容章节（被编排层 call 的那段演出）。"""

    label: str
    file: str = ''
    line: int = 0
    order: int = 0
    beats: list = field(default_factory=list, repr=False)
    dialogue_count: int = 0
    first_dialogue: int = -1       # beats 下标；-1 = 整章没有台词
    music_play: int = 0
    music_stop: int = 0
    scene_ops: int = 0
    show_ops: int = 0
    hide_ops: int = 0
    conditional_ops: int = 0
    python_audio: int = 0
    internal_flow: int = 0
    entry: dict = field(default_factory=dict, repr=False)
    exit: dict = field(default_factory=dict, repr=False)
    in_edges: list = field(default_factory=list, repr=False)   # [Seam]
    out_edges: list = field(default_factory=list, repr=False)  # [Seam]
    root: bool = False
    cycle: bool = False
    side: bool = False             # 旁支：从入口走不到（不判）
    chain_end: str = ''            # 非空 = 链尾原因（return / 文件末尾…）

    @property
    def in_degree(self) -> int:
        return len(self.in_edges)

    @property
    def out_degree(self) -> int:
        return len(self.out_edges)

    @property
    def zero_music(self) -> bool:
        return self.music_play == 0 and self.music_stop == 0

    @property
    def zero_stage(self) -> bool:
        return self.scene_ops == 0 and self.show_ops == 0 and self.hide_ops == 0

    def role_zh(self) -> str:
        tags = []
        if self.side:
            tags.append('旁支（不判）')
        if self.root:
            tags.append('链根')
        if self.in_degree > 1:
            tags.append(f'汇聚（{self.in_degree} 条入边）')
        if self.out_degree > 1:
            tags.append(f'分支（{self.out_degree} 条出边）')
        if self.cycle:
            tags.append('环')
        if self.chain_end:
            tags.append(f'链尾：{self.chain_end}')
        return ' · '.join(tags)

    def to_dict(self) -> dict:
        return {
            'label': self.label,
            'file': self.file,
            'line': self.line,
            'root': self.root,
            'cycle': self.cycle,
            'chain_end': self.chain_end,
            'in_degree': self.in_degree,
            'out_degree': self.out_degree,
            'in': [s.from_label for s in self.in_edges],
            'out': [s.to_label for s in self.out_edges],
            'entry': _state_to_dict(self.entry) if self.entry
            else _state_to_dict(_unknown_state()),
            'exit': _state_to_dict(self.exit) if self.exit
            else _state_to_dict(_unknown_state()),
            'counts': {
                'beats': len(self.beats),
                'dialogues': self.dialogue_count,
                'first_dialogue': self.first_dialogue,
                'music_play': self.music_play,
                'music_stop': self.music_stop,
                'scene_ops': self.scene_ops,
                'show_ops': self.show_ops,
                'hide_ops': self.hide_ops,
                'conditional_ops': self.conditional_ops,
                'python_audio': self.python_audio,
                'internal_flow': self.internal_flow,
            },
            'zero_music': self.zero_music,
            'zero_stage': self.zero_stage,
            'role': self.role_zh(),
            'exit_summary': _state_summary(self.exit)
            if self.exit else '（未知）',
        }


@dataclass
class Seam:
    """两个章节之间的接缝（P 的章末 → N 的开场）。"""

    from_label: str
    to_label: str
    kind: str = 'linear'          # linear / menu / condition / call
    condition: str = ''
    option: str = ''
    judged: bool = False
    reason: str = ''
    #: 元素 → `ok` / `leak` / `short` / `unknown` / `uncertain` / `n/a`
    outcomes: dict = field(default_factory=dict)
    details: list = field(default_factory=list)   # 人话细节（每条一个元素）

    def to_dict(self) -> dict:
        return {
            'from': self.from_label,
            'to': self.to_label,
            'kind': self.kind,
            'condition': self.condition,
            'option': self.option,
            'judged': self.judged,
            'reason': self.reason,
            'outcomes': dict(self.outcomes),
            'details': list(self.details),
        }


@dataclass
class Finding:
    """需要人看的问题 / 值得知道的事实。"""

    code: str
    level: str
    title: str
    message: str
    chapters: list = field(default_factory=list)
    suggestion: str = ''

    def to_dict(self) -> dict:
        return {
            'code': self.code, 'level': self.level, 'title': self.title,
            'message': self.message, 'chapters': list(self.chapters),
            'suggestion': self.suggestion,
        }


@dataclass
class Undetermined:
    """**不判定**的东西：说清楚"为什么不知道"，不塞进问题里。"""

    kind: str
    title: str
    message: str
    chapters: list = field(default_factory=list)
    detail: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'kind': self.kind, 'title': self.title,
            'message': self.message, 'chapters': list(self.chapters),
            'detail': list(self.detail),
        }


@dataclass
class ContinuityReport:
    """一次跨章连续性体检的全部结果（JSON 可序列化 + 人话渲染）。"""

    root: str = ''
    files: list = field(default_factory=list)
    labels_total: int = 0
    route_labels: int = 0
    entries: list = field(default_factory=list)
    chapters: list = field(default_factory=list)
    seams: list = field(default_factory=list)
    chains: list = field(default_factory=list)      # [[label, ...]]
    findings: list = field(default_factory=list)
    undetermined: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    elapsed: float = 0.0

    # ---------------- 查询 ----------------
    def chapter(self, label: str):
        for ch in self.chapters:
            if ch.label == str(label):
                return ch
        return None

    def chapter_map(self) -> dict:
        return {ch.label: ch for ch in self.chapters}

    def judged_seams(self) -> list:
        return [s for s in self.seams if s.judged]

    # ---------------- 输出 ----------------
    def stats(self) -> dict:
        judged = self.judged_seams()
        return {
            'chapters': len(self.chapters),
            'judged_seams': len(judged),
            'branch_nodes': sum(1 for c in self.chapters if c.out_degree > 1),
            'merge_nodes': sum(1 for c in self.chapters if c.in_degree > 1),
            'chains': len(self.chains),
            'warnings': sum(1 for f in self.findings
                            if f.level == LEVEL_WARN),
            'infos': sum(1 for f in self.findings if f.level == LEVEL_INFO),
            'undetermined': len(self.undetermined),
            'errors': len(self.errors),
        }

    def to_dict(self) -> dict:
        return {
            'root': self.root,
            'files': list(self.files),
            'labels_total': self.labels_total,
            'route_labels': self.route_labels,
            'entries': list(self.entries),
            'chapters': [c.to_dict() for c in self.chapters],
            'seams': [s.to_dict() for s in self.seams],
            'chains': [list(c) for c in self.chains],
            'findings': [f.to_dict() for f in self.findings],
            'undetermined': [u.to_dict() for u in self.undetermined],
            'errors': list(self.errors),
            'elapsed': round(float(self.elapsed), 3),
            'stats': self.stats(),
        }

    def render_text(self) -> str:
        """人话报告（CLI / 报告窗口共用同一份，不各拼一套）。"""
        st = self.stats()
        lines = ['跨章连续性体检（只读，未修改任何文件）']
        head = (f"{self.root or '（未指定项目根）'} · "
                f"{len(self.files)} 个 .rpy / {self.labels_total} 个 label"
                f" · 章节 {st['chapters']} 个 · 编排层 {self.route_labels} 个")
        if self.entries:
            head += ' · 入口：' + '、'.join(self.entries)
        lines.append(head)
        lines.append('判据：下一章只有一条入边就判继承（不看前一章有几条出边 ——'
                     '分支点自己的章末状态是确定的，每个分支章继承同一份状态）；'
                     '分支汇聚不判。')

        # ---- 先给结论：需要人看 / 事实 ----
        # （章节表很长，放最前面会把这些结论挤到几十行之后 —— 报告的第一屏
        #   应该是"有没有要改的"，不是"章节目录"。）
        warns = [f for f in self.findings if f.level == LEVEL_WARN]
        infos = [f for f in self.findings if f.level == LEVEL_INFO]

        def _print_findings(findings, mark):
            """
            同一来源的**同类问题**只展开一次。

            分支点有几个分支章，就有几条"同一种漏章"；每条都展开会把结论
            埋进重复段落里。说明：JSON 里**一条不少**（每章都有），
            完整细节在【可判定接缝】里逐条列着。
            """
            full_keys = set()
            for f in findings:
                from_label = f.chapters[0] if f.chapters else ''
                tail = f.title.split('：', 1)[-1]
                key = (f.code, from_label, tail)
                if key in full_keys:
                    targets = '、'.join(f.chapters[1:]) or from_label
                    lines.append(f'  {mark} [{f.code}] {from_label} → '
                                 f'{targets}：{tail}（细节同上）')
                    continue
                full_keys.add(key)
                lines.append(f'  {mark} [{f.code}] {f.title}')
                for ln in f.message.splitlines():
                    lines.append(f'     {ln}')
                if f.suggestion and mark == '[!]':
                    lines.append(f'     怎么办：{f.suggestion}')

        lines.append('')
        lines.append(f'【需要人看】{len(warns)} 条')
        if not warns:
            lines.append('  （没有发现"漏章"级别的连续性问题）')
        _print_findings(warns, '[!]')
        if infos:
            lines.append('')
            lines.append(f'【事实（不算错）】{len(infos)} 条')
            _print_findings(infos, '[i]')

        # ---- 确定链 ----
        lines.append('')
        lines.append('【确定链】（每一节后继都只有一条入边 —— 这些才连成链）')
        if not self.chains:
            lines.append('  （没有可判定的连续链：入口或分支结构让继承不可判）')
        for i, chain in enumerate(self.chains, 1):
            lines.append(f'  {i}. ' + ' → '.join(chain))

        # ---- 可判定的接缝结果 ----
        judged = self.judged_seams()
        meaningful = [s for s in judged
                      if any(v not in ('unknown', 'n/a')
                             for v in s.outcomes.values())]
        unknown_only = [s for s in judged if s not in meaningful]
        lines.append('')
        lines.append(f'【可判定接缝】{len(judged)} 条'
                     f'（其中有状态的 {len(meaningful)} 条）')
        for seam in meaningful:
            tag = self._seam_tag(seam)
            lines.append(f'  · {seam.from_label} → {seam.to_label}：{tag}')
        if unknown_only:
            lines.append(f'  · 另有 {len(unknown_only)} 条接缝已判（后继单入边），'
                         '但前驱的章末状态在上游分支/汇聚处被截断成了"未知"'
                         '—— 没有结论')

        # ---- 章节事实（长表放最后）----
        lines.append('')
        lines.append('【章节（出口 = 这一章演完时的舞台状态）】')
        if not self.chapters:
            lines.append('  （没有识别到章节 —— 这个项目可能没有 *control 编排层，'
                         '或跳转写法不在工具模型内）')
        for ch in self.chapters:
            roles = ch.role_zh()
            head_line = f"  {ch.label:<22s}"
            if roles:
                head_line += f' [{roles}]'
            lines.append(head_line)
            summary = _state_summary(ch.exit) if ch.exit else '（未知）'
            counts = (f"音乐 {ch.music_play}/{ch.music_stop}"
                      f" · 舞台 {ch.scene_ops + ch.show_ops + ch.hide_ops}")
            if ch.zero_music and ch.zero_stage:
                counts += '（整章零音乐/零舞台）'
            lines.append(f'      出口：{summary} | {counts}'
                         f' | 台词 {ch.dialogue_count} 句')

        # ---- 不判定 ----
        lines.append('')
        lines.append(f'【不判定（不猜）】{len(self.undetermined)} 条')
        if not self.undetermined:
            lines.append('  （没有需要声明"判不出来"的地方）')
        for u in self.undetermined:
            lines.append(f'  · {u.title}')
            for ln in u.message.splitlines():
                lines.append(f'      {ln}')
            for d in u.detail:
                lines.append(f'      - {d}')

        # ---- 读不出来的文件 ----
        if self.errors:
            lines.append('')
            lines.append(f'【读不出来的文件】{len(self.errors)} 个'
                         '（只影响这些文件，其余结论照出）')
            for err in self.errors:
                lines.append(f"  · {err.get('file')}：{err.get('error')}")
        return '\n'.join(lines)

    @staticmethod
    def _seam_tag(seam: Seam) -> str:
        names = {'ok': '干净', 'n/a': '无继承', 'short': '只差第 1 句',
                 'leak': '漏章', 'unknown': '未知', 'uncertain': '不确定'}
        parts = []
        for element in ('music', 'scene', 'actors', 'layers'):
            outcome = seam.outcomes.get(element)
            if not outcome or outcome == 'n/a':
                continue
            # 图层几乎每一章都是"未知"（项目很少显式 show/hide CG），
            # 每条接缝都挂一句"CG/图层未知"只是刷屏 —— 只在它真有结论时提。
            if element == 'layers' and outcome == 'unknown':
                continue
            parts.append(f'{ELEMENT_ZH[element]}{names.get(outcome, outcome)}')
        return ' · '.join(parts) if parts else '无继承状态'


# ======================================================================
#  解析结果 → 模型（拓扑 + 状态）
# ======================================================================

def _is_dialogue_beat(beat) -> bool:
    """这一拍有没有**台词/菜单文字**（玩家看到的第一句的锚点）。"""
    kind = str(getattr(beat, 'kind', '') or '')
    text = str(getattr(beat, 'text', '') or '').strip()
    speaker = str(getattr(beat, 'speaker', '') or '').strip()
    if kind in (KIND_SAY, KIND_NARRATION, KIND_EXTEND):
        return bool(text or speaker)
    if kind == KIND_MENU:
        return bool(menu_caption_text(text).strip())
    if kind == KIND_MENU_OPTION:
        return bool(text)
    return False


def _music_op(beat) -> str:
    """这一拍的音乐动作：`play` / `queue` / `stop` / ''（没有音乐语句）。"""
    action = str(getattr(beat, 'music_action', '') or 'play')
    name = str(getattr(beat, 'music', '') or '').strip()
    if name:
        return 'stop' if action == 'stop' else ('queue' if action == 'queue'
                                                else 'play')
    if action == 'stop':
        return 'stop'
    return ''


def _stage_has_scene(beat) -> bool:
    """这一拍有没有换背景（`scene` 语句，或 `show bg …` 这种）。"""
    if str(getattr(beat, 'scene', '') or '').strip():
        return True
    for item in split_show_items(str(getattr(beat, 'show', '') or '')):
        tag = str(item.get('image', '') or '').split()[0] if item.get('image') \
            else ''
        if tag in ('bg', 'background'):
            return True
    return False


def _tags_of(items) -> list:
    tags = []
    for item in items:
        image = str((item or {}).get('image', '') or '').strip()
        if not image:
            continue
        tag = image.split()[0]
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _python_audio_locations(beats) -> list:
    """章节里直接调 `renpy.music/sound/audio` 的拍序号（判不出来）。"""
    out = []
    for i, beat in enumerate(beats or ()):
        code = str(getattr(beat, 'code', '') or '')
        if code and PYTHON_AUDIO_RE.search(code):
            out.append(i)
    return out


@dataclass
class _ScanOutcome:
    """走到"接下来会发生什么"的一个出口。"""

    kind: str                       # content / end / unknown / fallthrough
    target: str = ''
    condition: str = ''
    option: str = ''
    reason: str = ''
    file: str = ''
    line: int = 0


class _LabelGraph:
    """label 级跳转图 + 章节级展开（内部用，不对外）。"""

    def __init__(self, parse_result):
        self.statements = list(getattr(parse_result, 'statements', None) or [])
        self.statement_labels = list(
            getattr(parse_result, 'statement_labels', None) or [])
        self.statement_options = list(
            getattr(parse_result, 'statement_menu_options', None) or [])
        self.labels = dict(getattr(parse_result, 'labels', None) or {})
        n = len(self.statements)
        if not self.statement_labels:
            self.statement_labels = [''] * n
        if not self.statement_options:
            self.statement_options = [''] * n
        self.by_label = defaultdict(list)
        for i, name in enumerate(self.statement_labels):
            self.by_label[str(name or '')].append(i)
        #: 真正被扫描过的 label（落空检查只报这些，别把旁支的噪声全算进来）
        self.touched = set()
        self._score_labels()

    # ---------------- 分类 ----------------
    def _score_labels(self):
        content = set()
        self.route_labels = set()
        for label, idxs in self.by_label.items():
            if not label:
                continue
            kinds = {str(self.statements[i].kind) for i in idxs}
            has_caption_menu = any(
                str(self.statements[i].kind) == str(K.MENU)
                and str((self.statements[i].payload or {}).get('caption', '')
                        or '').strip()
                for i in idxs if str(self.statements[i].kind) == str(K.MENU))
            if (kinds & {str(k) for k in CONTENT_KINDS}) or has_caption_menu:
                content.add(label)
            else:
                self.route_labels.add(label)
        self.content_labels = content
        # 没有任何语句的 label（`label a:` 后直接 EOF）也算 route 末端
        for label in self.labels:
            if label not in content and label not in self.route_labels:
                self.route_labels.add(label)

    def next_label_in_file(self, label: str) -> str:
        """同文件里的下一个 label（Ren'Py 落空规则：撞进下一段）。"""
        info = self.labels.get(label)
        if not isinstance(info, (tuple, list)) or len(info) < 2:
            return ''
        try:
            path, line = str(info[0]), int(info[1])
        except (TypeError, ValueError):
            return ''
        best = ('', 10 ** 9)
        for other, meta in self.labels.items():
            if other == label or not isinstance(meta, (tuple, list)):
                continue
            try:
                opath, oline = str(meta[0]), int(meta[1])
            except (TypeError, ValueError, IndexError):
                continue
            if opath != path or oline <= line:
                continue
            if oline < best[1]:
                best = (other, oline)
        return best[0]

    def _tail_terminates_in_menu(self, idxs) -> bool:
        """
        label 末尾是不是"一个 menu，且每个选项都被 jump/return 带走"？

        这种 label 没有 return 也**不会落空** —— 每个选项各自终止
        （`*_control` 编排层最常见的就是这个形状：call 完本章 → menu 分岔）。
        不认这一条，就会把"分支点"误报成"会撞进下一个 label"。
        """
        if not idxs:
            return False
        groups = {}          # 选项文本 → 该选项最后一条有效语句的 kind
        for gi in reversed(idxs):
            st = self.statements[gi]
            kind = str(getattr(st, 'kind', '') or '')
            opt = (self.statement_options[gi]
                   if gi < len(self.statement_options) else '')
            opt = str(opt or '')
            if kind in (str(K.MENU_OPTION), str(K.COMMENT)):
                continue
            if opt:
                groups.setdefault(opt, kind)
                continue
            if kind == str(K.MENU):
                return bool(groups) and all(
                    v in (str(K.JUMP), str(K.RETURN)) for v in groups.values())
            return False     # menu 之后还有普通语句 → 不是纯 menu 收尾
        return False

    # ---------------- 扫描 ----------------
    def scan_label(self, label: str, start: int = 0,
                   depth: int = 0, seen=None) -> list:
        """
        从 `label` 的第 `start` 条语句开始扫：**接下来最先遇到什么内容**。

        只回答"下一个内容 label / 链尾 / 判不出来"——不展开被调章节内部
        （它的后继由它自己的 call 点那条路单独算，避免重复与递归爆炸）。
        """
        seen = set(seen or ())
        if label in seen or depth > 10:
            return [_ScanOutcome('unknown', reason='编排层成环或嵌套过深')]
        seen.add(label)
        self.touched.add(label)
        idxs = self.by_label.get(label) or []
        out = []
        pos = max(0, int(start or 0))
        while pos < len(idxs):
            gi = idxs[pos]
            st = self.statements[gi]
            kind = str(getattr(st, 'kind', '') or '')
            payload = getattr(st, 'payload', None) or {}
            target = str(payload.get('target', '') or '').strip()
            cond = str(getattr(st, 'condition', '') or '')
            option = self.statement_options[gi] \
                if gi < len(self.statement_options) else ''
            option = str(option or '')
            origin = getattr(st, 'source', ('', 0)) or ('', 0)
            file_of = str(origin[0] or '') if len(origin) > 0 else ''
            line_of = int(origin[1] or 0) if len(origin) > 1 else 0

            if kind == str(K.CALL):
                if target in self.content_labels:
                    out.append(_ScanOutcome('content', target=target,
                                            condition=cond, option=option,
                                            file=file_of, line=line_of))
                    if option:
                        pos += 1
                        continue
                    return out
                if target in self.route_labels:
                    nested = self.scan_label(target, 0, depth + 1, seen)
                    out.extend(nested)
                    if any(o.kind == 'content' for o in nested) or not option:
                        return out
                    pos += 1
                    continue
                out.append(_ScanOutcome(
                    'unknown', reason=f'call 了剧本里不存在的 label：{target}',
                    file=file_of, line=line_of))
                if option:
                    pos += 1
                    continue
                return out

            if kind == str(K.JUMP):
                if not target or _DYNAMIC_TARGET_RE.match(target):
                    out.append(_ScanOutcome(
                        'unknown', reason='动态跳转（运行时才知道去哪）',
                        file=file_of, line=line_of))
                    # 条件跳转后面还有别的分支；普通动态跳转就到此为止
                    if option or cond:
                        pos += 1
                        continue
                    return out
                if target in self.content_labels:
                    out.append(_ScanOutcome('content', target=target,
                                            condition=cond, option=option,
                                            file=file_of, line=line_of))
                    if option or cond:
                        pos += 1
                        continue
                    return out
                if target in self.route_labels:
                    out.extend(self.scan_label(target, 0, depth + 1, seen))
                    if option or cond:
                        pos += 1
                        continue
                    return out
                out.append(_ScanOutcome(
                    'unknown', reason=f'jump 到剧本里不存在的 label：{target}',
                    file=file_of, line=line_of))
                if option or cond:
                    pos += 1
                    continue
                return out

            if kind == str(K.RETURN):
                out.append(_ScanOutcome(
                    'end', reason='return（回到调用者）',
                    condition=cond, option=option,
                    file=file_of, line=line_of))
                if option or cond:
                    pos += 1
                    continue
                return out

            if kind == str(K.PYTHON):
                code = str((getattr(st, 'payload', None) or {})
                           .get('code', '') or '')
                if re.search(r'renpy\s*\.\s*(jump|call)\s*\(', code):
                    out.append(_ScanOutcome(
                        'unknown', reason='Python 动态跳转 '
                                          '（renpy.jump/call，运行时才知道去哪）',
                        file=file_of, line=line_of))
                    if option or cond:
                        pos += 1
                        continue
                    return out

            # 其它语句（python / menu / menu_option / comment / raw / loop…）
            pos += 1

        # 走到 label 末尾：Ren'Py 会撞进同文件的下一个 label
        if self._tail_terminates_in_menu(idxs):
            return out       # 每个菜单选项都跳走了 —— 不是落空
        nxt = self.next_label_in_file(label)
        if nxt:
            out.append(_ScanOutcome(
                'fallthrough', target=nxt,
                reason=f'label 末尾没有 return/jump —— 会撞进「{nxt}」'))
        else:
            out.append(_ScanOutcome('end', reason='label 结束，落到文件末尾'))
        return out


class _Window:
    """N 章"从开场到第一句台词（含）"这一段里发生了什么。"""

    def __init__(self, beats: list, first: int):
        self.beats = beats
        self.first = first                  # 第一句台词所在拍；-1 = 没有台词
        self.last = first if first >= 0 else len(beats) - 1
        window = beats[:self.last + 1]
        self.music = [_music_op(b) for b in window]
        self.has_music = any(self.music)
        self.scene = [_stage_has_scene(b) for b in window]
        self.has_scene = any(self.scene)
        self.hide_tags = []
        self.show_tags = []
        for b in window:
            self.hide_tags.extend(_tags_of(
                split_hide_items(str(getattr(b, 'hide', '') or ''))))
            self.show_tags.extend(_tags_of(
                split_show_items(str(getattr(b, 'show', '') or ''))))
        self.conditional = any(
            str(getattr(b, 'condition', '') or '').strip()
            and (bool(_music_op(b)) or _stage_has_scene(b)
                 or str(getattr(b, 'show', '') or '').strip()
                 or str(getattr(b, 'hide', '') or '').strip())
            for b in window)
        self.python_audio = bool(_python_audio_locations(window))

    # ---- "第一句之后才换"的判定 ----
    def later_music(self, beats) -> int:
        for i in range(self.last + 1, len(beats)):
            if _music_op(beats[i]):
                return i
        return -1

    def later_scene(self, beats) -> int:
        for i in range(self.last + 1, len(beats)):
            if _stage_has_scene(beats[i]):
                return i
        return -1

    def later_hide(self, beats, tags) -> int:
        want = set(tags)
        for i in range(self.last + 1, len(beats)):
            if _stage_has_scene(beats[i]):
                return i
            got = set(_tags_of(split_hide_items(
                str(getattr(beats[i], 'hide', '') or ''))))
            if want & got:
                return i
        return -1


def _first_dialogue(beats: list) -> int:
    for i, beat in enumerate(beats or ()):
        if _is_dialogue_beat(beat):
            return i
    return -1


def _chapter_from_beats(label: str, beats: list, meta,
                        order: int) -> Chapter:
    ch = Chapter(label=label, order=order)
    if isinstance(meta, (tuple, list)) and len(meta) >= 2:
        ch.file = str(meta[0] or '')
        try:
            ch.line = int(meta[1] or 0)
        except (TypeError, ValueError):
            ch.line = 0
    ch.beats = list(beats or ())
    ch.dialogue_count = sum(1 for b in ch.beats if _is_dialogue_beat(b))
    ch.first_dialogue = _first_dialogue(ch.beats)
    for b in ch.beats:
        op = _music_op(b)
        if op == 'stop':
            ch.music_stop += 1
        elif op:
            ch.music_play += 1
        if _stage_has_scene(b):
            ch.scene_ops += 1
        ch.show_ops += len(split_show_items(str(
            getattr(b, 'show', '') or '')))
        ch.hide_ops += len(split_hide_items(str(
            getattr(b, 'hide', '') or '')))
        if str(getattr(b, 'condition', '') or '').strip() and (
                op or _stage_has_scene(b)
                or str(getattr(b, 'show', '') or '').strip()
                or str(getattr(b, 'hide', '') or '').strip()):
            ch.conditional_ops += 1
    ch.python_audio = len(_python_audio_locations(ch.beats))
    return ch


# ======================================================================
#  主分析
# ======================================================================

def analyze_parse_result(parse_result, *, project_root: str = '',
                         files=None) -> ContinuityReport:
    """
    已解析的项目 → 跨章连续性报告（纯分析，不写盘、不 import Qt/图）。

    参数：
      · `parse_result`：`ParseResult`（`IncrementalRenPyAnalyzer` 的产物；
        与剧本表/剧情图**同一份 IR** —— 不另解析一遍项目）；
      · `files`：参与分析的文件（只用于展示与错误归属）。
    """
    t0 = time.time()
    report = ContinuityReport(
        root=str(project_root or ''),
        files=[str(p) for p in (files or ())],
        labels_total=len(getattr(parse_result, 'labels', None) or {}),
    )
    lg = _LabelGraph(parse_result)
    report.route_labels = len(lg.route_labels)

    # ---- 拍（与剧本表同源）----
    all_beats = []
    try:
        all_beats = beats_from_parse_result(parse_result, spec_registry={})
    except Exception as e:                       # noqa: BLE001 - 出不了拍也要给出口检查
        report.errors.append({'file': '(beats)', 'error': str(e)})
        swallow('pipeline.continuity.beats', e)
    beats_by_label = defaultdict(list)
    for beat in all_beats:
        beats_by_label[str(getattr(beat, 'label', '') or '')].append(beat)

    # ---- 章节候选：被"编排层"call / jump 到的内容 label ----
    chapter_labels = []
    seen = set()
    for label in (lg.route_labels or ()):
        for gi in lg.by_label.get(label) or []:
            st = lg.statements[gi]
            kind = str(getattr(st, 'kind', '') or '')
            if kind not in (str(K.CALL), str(K.JUMP)):
                continue
            tgt = str((getattr(st, 'payload', None) or {}).get('target', '')
                      or '').strip()
            if tgt in lg.content_labels and tgt not in seen:
                seen.add(tgt)
                chapter_labels.append(tgt)
    # 没有任何编排层时退回"入口 + 被跳转到的内容"（普通线性项目）
    if not chapter_labels:
        for label in lg.content_labels:
            if label in ENTRY_LABELS:
                chapter_labels.append(label)
                continue
            for gi in lg.by_label.get(label) or []:
                st = lg.statements[gi]
                if str(getattr(st, 'kind', '')) in (str(K.CALL), str(K.JUMP)):
                    tgt = str((getattr(st, 'payload', None) or {})
                              .get('target', '') or '').strip()
                    if tgt in lg.content_labels and tgt not in seen:
                        seen.add(tgt)
                        chapter_labels.append(tgt)
                        break

    # 章节按源文件/行号排序（稳定、可复现）
    def _sort_key(label):
        meta = lg.labels.get(label)
        try:
            return (str(meta[0]), int(meta[1]))
        except (TypeError, ValueError, IndexError):
            return ('', 0)
    chapter_labels.sort(key=_sort_key)

    chapters = {}
    for i, label in enumerate(chapter_labels):
        ch = _chapter_from_beats(label, beats_by_label.get(label) or [],
                                 lg.labels.get(label), i)
        chapters[label] = ch
    report.chapters = list(chapters.values())

    # ---- 章节图（P 的 call 点之后会到哪一章）----
    call_sites = defaultdict(list)      # chapter → [(caller_label, stmt_idx)]
    for label, idxs in lg.by_label.items():
        for gi in idxs:
            st = lg.statements[gi]
            if str(getattr(st, 'kind', '')) != str(K.CALL):
                continue
            tgt = str((getattr(st, 'payload', None) or {}).get('target', '')
                      or '').strip()
            if tgt in chapters:
                call_sites[tgt].append((label, gi))

    seams = []
    edge_seen = set()
    chain_end = defaultdict(list)

    def _add_edge(p_label, outcome, fallback_reason=''):
        tgt = outcome.target
        if outcome.kind == 'content' and tgt in chapters:
            key = (p_label, tgt, outcome.condition, outcome.option,
                   outcome.kind)
            if key in edge_seen:
                return
            edge_seen.add(key)
            seam = Seam(from_label=p_label, to_label=tgt,
                        kind=('menu' if outcome.option
                              else ('condition' if outcome.condition
                                    else 'linear')),
                        condition=outcome.condition, option=outcome.option)
            seams.append(seam)
            chapters[p_label].out_edges.append(seam)
            chapters[tgt].in_edges.append(seam)
            return
        if outcome.kind == 'content':
            # 内容 label，但不是章节候选（旁支内容）
            report.undetermined.append(Undetermined(
                kind=UND_SIDE,
                title=f'{p_label} 会进入非章节内容「{tgt}」',
                message='它不是被编排层 call 的章节，按"旁支内容"处理，'
                        '跨章继承不判。',
                chapters=[p_label, tgt]))
            return
        if outcome.kind == 'fallthrough':
            chain_end[p_label].append(outcome.reason or '落空')
            return
        if outcome.kind == 'end':
            chain_end[p_label].append(outcome.reason or '链尾')
            return
        # unknown
        report.undetermined.append(Undetermined(
            kind=UND_DYNAMIC,
            title=f'{p_label} 的出口判不出来',
            message=(fallback_reason or '出口不是静态可解的跳转')
                    + ' —— 这一条支路到此为止，不猜它去哪。',
            chapters=[p_label],
            detail=[outcome.reason] if outcome.reason else []))

    for label, chapter in chapters.items():
        sites = call_sites.get(label) or []
        outcomes = []
        for caller, gi in sites:
            cond = str(getattr(lg.statements[gi], 'condition', '') or '')
            option = (lg.statement_options[gi]
                      if gi < len(lg.statement_options) else '')
            idxs = lg.by_label.get(caller) or []
            try:
                start = idxs.index(gi) + 1
            except ValueError:                   # pragma: no cover - 平行数组错位
                start = len(idxs)
            nxt = lg.scan_label(caller, start)
            for o in nxt:
                o.condition = (f'({cond}) and ({o.condition})'
                               if cond and o.condition
                               else (cond or o.condition))
                o.option = option or o.option
                outcomes.append(o)
        if not sites:
            outcomes = lg.scan_label(label, 0)
        else:
            # 章节内部**自己写**的跳转（菜单/条件跳转）也算出口，但标低置信度
            inner = [o for o in lg.scan_label(label, 0)
                     if o.kind == 'content']
            if inner:
                chapter.internal_flow = len(inner)
                outcomes.extend(inner)
                report.undetermined.append(Undetermined(
                    kind=UND_INTERNAL,
                    title=f'{chapter.label} 章节内部有跳转',
                    message='这一章不是一条直线（内部有 jump/call 到别的章节）。'
                            '工具按"可能性"把目标都算作出边，**不判**它们的顺序与汇合。',
                    chapters=[chapter.label]))
        for o in outcomes:
            _add_edge(label, o)
        reasons = [r for r in (chain_end.get(label) or []) if r]
        if reasons:
            if chapter.out_edges:
                # 有明确去向、却有"条件全不成立才到"的收尾（if/elif 链后面
                # 还留着 return）：工具不判断条件是否穷尽 —— 单独声明，不混进链尾。
                report.undetermined.append(Undetermined(
                    kind='condition_exhaustive',
                    title=f'{chapter.label} 的条件分支是否穷尽，判不出来',
                    message='它有多个明确去向，但后面还留着一条收尾（'
                            + ' / '.join(reasons) + '）—— 那条收尾只有在'
                            '所有条件都不成立时才到。工具不算条件，不判定。',
                    chapters=[label]))
                chain_end[label] = []
            else:
                chapter.chain_end = ' / '.join(dict.fromkeys(reasons))

    # ---- 链根：Ren'Py 入口 label 会走到哪些章节 ----
    # 有 `start` 就只从 `start` 出发（真正的游戏主线）；没有才用全部入口。
    # 不这么做的话，同项目里的"另一个小应用"（番茄钟的 beginstory 等）会被
    # 当成第二条主线 —— 实测把 pomodoro 的剧本拉进了跨章报告。
    entry_labels = [label for label in lg.labels if label in ENTRY_LABELS]
    preferred = [label for label in entry_labels if label == 'start'] \
        or entry_labels
    for label in preferred:
        for o in lg.scan_label(label, 0):
            if o.kind == 'content' and o.target in chapters:
                chapters[o.target].root = True
        # 入口 label 自己就是内容章（没有编排层的线性项目）：它才是链根
        if label in chapters and label in lg.content_labels:
            chapters[label].root = True
    report.entries = preferred

    # 没有入口能走到的（例如纯测试项目）：把入度为 0 的章节当链根
    if not any(ch.root for ch in chapters.values()):
        for ch in chapters.values():
            if ch.in_degree == 0:
                ch.root = True

    # ---- 可达章节（旁支剔除出主分析，但保留在"不判定"里说明）----
    reachable = set()
    queue = deque(ch.label for ch in chapters.values() if ch.root)
    while queue:
        cur = queue.popleft()
        if cur in reachable:
            continue
        reachable.add(cur)
        for seam in chapters[cur].out_edges:
            queue.append(seam.to_label)
    side = [ch for ch in chapters.values() if ch.label not in reachable]
    for ch in side:
        ch.side = True
        report.undetermined.append(Undetermined(
            kind=UND_SIDE,
            title=f'旁支：{ch.label}',
            message='从入口走不到（可能是小游戏 / 调试 / gallery 的入口），'
                    '不判它的跨章继承。',
            chapters=[ch.label]))

    # ---- 状态推导（拓扑序；成环 → 全部未知）----
    active = [ch for ch in chapters.values() if ch.label in reachable]
    report.chapters = sorted(active, key=lambda c: c.order)
    # **旁支的边不算进主链的入度/出度**：一条"从入口走不到"的调试路径
    # 也能指向主线章节，把它算成第二前驱会让主线莫名其妙变成"汇聚"。
    # 判继承只认**可达章节之间**的边（与 `report.seams` 的过滤同一条口径）。
    active_labels = {ch.label for ch in active}
    for ch in active:
        ch.in_edges = [s for s in ch.in_edges
                       if s.from_label in active_labels
                       and s.to_label in active_labels]
        ch.out_edges = [s for s in ch.out_edges
                        if s.from_label in active_labels
                        and s.to_label in active_labels]
    indeg = {ch.label: 0 for ch in active}
    for ch in active:
        for seam in ch.out_edges:
            if seam.to_label in indeg:
                indeg[seam.to_label] += 1
    ready = deque(ch.label for ch in active if indeg[ch.label] == 0)
    done = set()
    topo = []
    while ready:
        cur = ready.popleft()
        if cur in done:
            continue
        done.add(cur)
        topo.append(cur)
        for seam in chapters[cur].out_edges:
            nxt = seam.to_label
            if nxt not in indeg:
                continue
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
    cycle_labels = [ch.label for ch in active if ch.label not in done]
    for label in cycle_labels:
        chapters[label].cycle = True
    if cycle_labels:
        report.undetermined.append(Undetermined(
            kind=UND_CYCLE,
            title='这些章节在环里（jump 回了自己/绕圈）',
            message='环里的状态取决于转了几圈，静态判不出来 —— 整环按"未知"处理，'
                    '不判继承。',
            chapters=cycle_labels))

    def _entry_of(label: str) -> dict:
        ch = chapters[label]
        if ch.root or ch.cycle:
            return _unknown_state()
        preds = [s.from_label for s in ch.in_edges if s.from_label in chapters]
        # **只看单一入边**（用户 2026-10-28）：有多个前驱就是分支汇聚，
        # 不管"恰好一样不一样"都当未知 —— 不猜"反正一样"。
        if len(preds) != 1:
            return _unknown_state()
        pred = chapters.get(preds[0])
        if pred is None or not pred.exit:
            return _unknown_state()
        return pred.exit

    # 拓扑序遍历；环里的章节用"未知入口"也能算出本地出口
    ordered = topo + cycle_labels
    for label in ordered:
        ch = chapters[label]
        ch.entry = _entry_of(label)
        try:
            ch.exit = stage_exit_state(ch.beats, initial_state=ch.entry)
        except Exception as e:                   # noqa: BLE001 - 一章推导失败不炸整轮
            report.errors.append({'file': ch.file or ch.label,
                                  'error': f'舞台状态推导失败：{e}'})
            ch.exit = _unknown_state()

    # ---- 接缝判定 ----
    active_labels = {ch.label for ch in active}
    report.seams = [s for s in seams
                    if s.from_label in active_labels
                    and s.to_label in active_labels]
    for seam in report.seams:
        p = chapters.get(seam.from_label)
        n = chapters.get(seam.to_label)
        if p is None or n is None:
            continue
        _judge_seam(seam, p, n, report)

    # ---- 本地事实 / 分支 / 汇聚 / 判不出来的声明 ----
    _collect_local_facts(report, active, chapters)
    _collect_topology_notes(report, active, chapters)
    _collect_condition_notes(report, active)
    _collect_python_audio(report, lg, chapters)
    _collect_unmodeled_channels(report, lg)
    _collect_fallthrough(report, lg)

    # ---- 确定链（只由"可判定接缝"串起来）----
    report.chains = _build_chains(report, chapters, active)

    # 排序：问题按严重度，不判定按发现顺序保持
    report.findings.sort(key=lambda f: (_LEVEL_ORDER.get(f.level, 9),
                                        f.chapters[:1], f.code))
    report.seams = [s for s in seams]
    report.elapsed = time.time() - t0
    return report


def _judge_seam(seam: Seam, p: Chapter, n: Chapter,
                report: ContinuityReport) -> None:
    """
    判定一条接缝能不能继承：**只看后继是否只有一条入边**。

    用户 2026-10-28 第二轮澄清的最终口径：

    > 本意就是只要节点"单入边就判、不管前驱出边"。

    也就是**不看** `p.out_degree`：分支点自己的章末状态是确定的（它演完是
    什么样，不取决于玩家接下来选哪条），所以每个分支后代继承的都是同一份
    状态 —— 这是事实，不是猜。真正不判的只有：**N 有多条入边（汇聚）**、
    环、入口未知等。
    """
    if n.in_degree != 1:
        seam.judged = False
        seam.reason = (f'{n.label} 有 {n.in_degree} 条入边' if n.in_degree > 1
                       else f'{n.label} 没有入边（链根，入口状态未知）')
        seam.outcomes = {k: 'unknown' for k in EXIT_STATE_KEYS}
        return
    if p.cycle or n.cycle:
        seam.judged = False
        seam.reason = '在环里（转几圈不确定），不判'
        seam.outcomes = {k: 'unknown' for k in EXIT_STATE_KEYS}
        return
    seam.judged = True
    seam.reason = '后继只有一条入边'
    _compare_inheritance(seam, p, n, report)


def _compare_inheritance(seam: Seam, p: Chapter, n: Chapter,
                         report: ContinuityReport) -> None:
    """比较 P 的章末状态与 N 的开场处理，产出问题/事实。"""
    window = _Window(n.beats, n.first_dialogue)
    pe = p.exit or {}
    unknown = set(pe.get('unknown') or ())
    outcomes = {}
    for element in EXIT_STATE_KEYS:
        if element == 'voice':
            continue
        if element in unknown:
            outcomes[element] = 'unknown'
            continue
        if element == 'music':
            outcomes[element] = _judge_music(seam, p, n, pe, window, report)
        elif element == 'scene':
            outcomes[element] = _judge_scene(seam, p, n, pe, window, report)
        elif element == 'actors':
            outcomes[element] = _judge_actors(seam, p, n, pe, window, report)
        elif element == 'layers':
            outcomes[element] = _judge_layers(seam, p, n, pe, window, report)
    seam.outcomes = outcomes


def _dialogue_anchor(n: Chapter) -> str:
    """第一句台词落在**本章第几拍**（章节内相对拍号，不是全局 seq）。"""
    if n.first_dialogue < 0:
        return '整章没有台词'
    return f"第 {n.first_dialogue + 1} 拍的第一句"


def _later_line(n: Chapter, index: int) -> str:
    if index < 0:
        return ''
    return f'第 {index + 1} 拍才换'


def _judge_music(seam, p, n, pe, window, report) -> str:
    track = str(pe.get('music') or '')
    if not track:
        return 'n/a'                        # 上一章章末没有音乐 → 没有可继承的
    display = _short_audio(track)
    if window.has_music:
        return 'ok'                         # 开场就换/停了
    if window.python_audio:
        return 'uncertain'                  # 直接调 Python 音频，看不出来
    if window.conditional:
        return 'uncertain'                  # 条件语句，可能换可能不换
    later = window.later_music(n.beats)
    anchor = _dialogue_anchor(n)
    short = _later_line(n, later)
    if later >= 0:
        report.findings.append(Finding(
            code='M0', level=LEVEL_INFO,
            title=f'{p.label} → {n.label}：第 1 句台词时还在播上一章的音乐',
            message=(f'{p.label} 章末在播「{display}」；{n.label} 的{anchor}之前'
                     f'没有 play/stop 音乐语句（{short}）。\n'
                     '只影响第 1 句台词，之后换掉了。'),
            chapters=[p.label, n.label],
            suggestion='如果这一句就该听到新曲子，把这句台词放到换曲之后；'
                       '否则可以不管。'))
        return 'short'
    tail = _music_trail(p.label, n, track, report)
    report.findings.append(Finding(
        code='M1', level=LEVEL_WARN,
        title=f'{p.label} → {n.label}：音乐漏章（这首曲子会一直播）',
        message=(f'{p.label} 章末还在播「{display}」；{n.label} 整章没有 play/stop '
                 f'音乐语句 → {anchor}时听的是上一章的曲子。\n'
                 + tail),
        chapters=[p.label, n.label],
        suggestion=f'在 {n.label} 的第一句台词前补 stop music（或换成本章 BGM）；'
                   '也可以在上一章章末收掉。'))
    return 'leak'


def _music_trail(p_label: str, n: Chapter, track: str,
                 report: ContinuityReport) -> str:
    """"一直播"能确定到哪一章（顺着可判定接缝往后看，一断就停）。"""
    cmap = report.chapter_map()
    cur = n
    trail = [n.label]
    seen = {p_label, n.label}
    while True:
        out = [s for s in cur.out_edges]
        if len(out) != 1:
            break
        nxt = cmap.get(out[0].to_label)
        if nxt is None or nxt.label in seen:
            break
        if not out[0].judged:
            break
        if nxt.music_play or nxt.music_stop:
            break
        seen.add(nxt.label)
        trail.append(nxt.label)
        cur = nxt
    if not trail:
        return ''
    last = trail[-1]
    if cur.out_edges and len(cur.out_edges) == 1:
        tail = '再往后是分支/汇聚/链尾，不再判定'
    elif cur.out_degree > 1:
        tail = '再往后是分支点，不再判定'
    elif cur.chain_end:
        tail = f'然后到链尾（{cur.chain_end}）'
    else:
        tail = '再往后不判定'
    if len(trail) > 1:
        return (f'能确定的范围：{p_label} 章末 → 一直播到 {last} 章末'
                f'（中间 {len(trail) - 1} 章都没有音乐语句）。{tail}。')
    return f'能确定的范围：{n.label} 整章（{tail}）。'


def _judge_scene(seam, p, n, pe, window, report) -> str:
    scene = str(pe.get('scene') or '')
    if not scene:
        return 'n/a'
    display = _short_image(scene)
    if window.has_scene:
        return 'ok'
    if window.python_audio or window.conditional:
        return 'uncertain'
    later = window.later_scene(n.beats)
    anchor = _dialogue_anchor(n)
    if later >= 0:
        report.findings.append(Finding(
            code='S0', level=LEVEL_INFO,
            title=f'{p.label} → {n.label}：第 1 句台词时背景还是上一章的',
            message=(f'{p.label} 章末背景是 {display}；{n.label} 的{anchor}之前'
                     f'没有换景（{_later_line(n, later)}）。\n'
                     '只影响第 1 句台词，之后换掉了。'),
            chapters=[p.label, n.label],
            suggestion='如果这句台词时不该看到旧背景，把换景提前到这句之前。'))
        return 'short'
    report.findings.append(Finding(
        code='S1', level=LEVEL_WARN,
        title=f'{p.label} → {n.label}：背景漏章（沿用上一章的场景）',
        message=(f'{p.label} 章末背景是 {display}；{n.label} 整章没有换景语句'
                 f' → {anchor}时看到的还是上一章的场景。'),
        chapters=[p.label, n.label],
        suggestion=f'在 {n.label} 的第一句台词前补 scene；'
                   '或确认这一章就该用同一个背景（那就把这个提示当作已确认）。'))
    return 'leak'


def _judge_actors(seam, p, n, pe, window, report) -> str:
    actors = pe.get('actors') or []
    tags = []
    for image, _at in actors:
        tag = str(image or '').split()[0]
        if tag and tag not in tags:
            tags.append(tag)
    if not tags:
        return 'n/a'
    if window.has_scene:
        return 'ok'                          # scene 会清空立绘
    if window.conditional or window.python_audio:
        return 'uncertain'
    leftover = [t for t in tags if t not in window.hide_tags]
    if not leftover:
        return 'ok'
    later = window.later_hide(n.beats, leftover)
    anchor = _dialogue_anchor(n)
    if later >= 0:
        report.findings.append(Finding(
            code='C0', level=LEVEL_INFO,
            title=f'{p.label} → {n.label}：第 1 句台词时上一章的立绘还站着',
            message=(f'{p.label} 章末台上还有 ' + '、'.join(leftover)
                     + f'；{n.label} 的{anchor}之前没有退场/换景'
                     f'（{_later_line(n, later)}）。'),
            chapters=[p.label, n.label],
            suggestion='把这句台词放到退场之后，或在这一句之前补 hide/scene。'))
        return 'short'
    report.findings.append(Finding(
        code='C1', level=LEVEL_WARN,
        title=f'{p.label} → {n.label}：立绘残留（上一章的人还在台上说话）',
        message=(f'{p.label} 章末台上还有 ' + '、'.join(leftover)
                 + f'；{n.label} 整章没有 scene / 退场语句'
                 f' → {anchor}时上一章的人还站着。'),
        chapters=[p.label, n.label],
        suggestion=f'在 {n.label} 开场补 hide 或 scene；'
                   '或确认这一章就是接着演（那就把提示当作已确认）。'))
    return 'leak'


def _judge_layers(seam, p, n, pe, window, report) -> str:
    layers = pe.get('layers') or {}
    tags = list(layers.keys())
    if not tags:
        return 'n/a'
    if window.has_scene:
        return 'ok'
    if window.conditional or window.python_audio:
        return 'uncertain'
    leftover = [t for t in tags if t not in window.hide_tags]
    if not leftover:
        return 'ok'
    later = window.later_hide(n.beats, leftover)
    anchor = _dialogue_anchor(n)
    names = '、'.join(str(layers.get(t)) for t in leftover)
    if later >= 0:
        report.findings.append(Finding(
            code='G0', level=LEVEL_INFO,
            title=f'{p.label} → {n.label}：第 1 句台词时 CG/图层还挂着',
            message=(f'{p.label} 章末还挂着 {names}；{n.label} 的{anchor}之前'
                     f'没有撤下（{_later_line(n, later)}）。'),
            chapters=[p.label, n.label],
            suggestion='把撤图提前到这句台词之前。'))
        return 'short'
    report.findings.append(Finding(
        code='G1', level=LEVEL_WARN,
        title=f'{p.label} → {n.label}：CG/图层残留',
        message=(f'{p.label} 章末还挂着 {names}；{n.label} 整章没有 scene / '
                 f'撤图层语句 → {anchor}时那张 CG 还盖在屏幕上。'),
        chapters=[p.label, n.label],
        suggestion=f'在 {n.label} 开场补 hide；或确认就该盖着。'))
    return 'leak'


# ----------------------------------------------------------------------
#  本地事实 / 拓扑说明
# ----------------------------------------------------------------------

def _collect_local_facts(report: ContinuityReport, active: list,
                         chapters: dict) -> None:
    zero_music = [ch for ch in active if ch.zero_music]
    zero_both = [ch for ch in active if ch.zero_music and ch.zero_stage]
    if not active:
        return
    if zero_music:
        names = '、'.join(ch.label for ch in zero_music[:6])
        more = f' 等 {len(zero_music)} 个' if len(zero_music) > 6 else ''
        report.findings.append(Finding(
            code='L1', level=LEVEL_INFO,
            title=f'本地事实：{len(zero_music)} 个章节整章没有任何音乐语句',
            message=(f'{names}{more}。\n'
                     '这不是缺陷（很多章本就该静音/只换演出），但意味着它们的'
                     '音乐全靠上游继承 —— 上面的"漏章"判断就是从这里来的。'),
            chapters=[ch.label for ch in zero_music]))
    if zero_both:
        names = '、'.join(ch.label for ch in zero_both[:6])
        more = f' 等 {len(zero_both)} 个' if len(zero_both) > 6 else ''
        report.findings.append(Finding(
            code='L2', level=LEVEL_INFO,
            title=(f'本地事实：{len(zero_both)} 个章节整章没有任何音乐/舞台语句'),
            message=f'{names}{more}。\n舞台与音乐全靠上游继承（或本来就该黑屏）。',
            chapters=[ch.label for ch in zero_both]))
    no_dialogue = [ch for ch in active if ch.first_dialogue < 0]
    if no_dialogue:
        report.undetermined.append(Undetermined(
            kind=UND_NO_DIALOGUE,
            title='这些章节没有台词（找不到"开场第一句"这个锚点）',
            message='工具用"第一句台词"判断开场时观众看到什么；没有台词就没有'
                    '这个时间点 —— 只报章末状态，不判"开场是否接住"。',
            chapters=[ch.label for ch in no_dialogue]))


def _collect_topology_notes(report: ContinuityReport, active: list,
                            chapters: dict) -> None:
    for ch in active:
        if ch.out_degree > 1:
            targets = [s.to_label for s in ch.out_edges]
            report.undetermined.append(Undetermined(
                kind=UND_BRANCH,
                title=f'分支点：{ch.label}（{ch.out_degree} 条出边）',
                message=('它演完的章末状态是**一份**；每个分支章只要自己'
                         '只有一条入边就判继承（继承的是同一份状态）。'
                         '这里只列去向，不合并、不排序：'
                         + '、'.join(targets) + '。'),
                chapters=[ch.label] + targets,
                detail=[f'章末状态：{_state_summary(ch.exit)}']))
        if ch.in_degree > 1:
            preds = [s.from_label for s in ch.in_edges]
            detail = []
            for label in preds:
                pred = chapters.get(label)
                if pred is not None:
                    detail.append(f'{label}：{_state_summary(pred.exit)}')
            report.undetermined.append(Undetermined(
                kind=UND_MERGE,
                title=f'分支汇聚：{ch.label}（{ch.in_degree} 条入边）',
                message=('汇进来的接缝**不判**（不合并、不排序、不猜"反正一样"）。'
                         '各前驱的章末状态如下，人自己看：'),
                chapters=[ch.label] + preds,
                detail=detail))
    roots = [ch for ch in active if ch.root]
    for ch in roots:
        report.undetermined.append(Undetermined(
            kind=UND_ENTRY,
            title=f'入口状态未知：{ch.label}',
            message='它是第一个章节，前面是引擎入口（start 等）—— 入口 label '
                    '在它之前设置的舞台/音乐没有建模，所以**不判**它开场的继承。',
            chapters=[ch.label]))


def _collect_condition_notes(report: ContinuityReport, active: list) -> None:
    rows = [ch for ch in active if ch.conditional_ops]
    if not rows:
        return
    report.undetermined.append(Undetermined(
        kind=UND_CONDITION,
        title='这些章节有"带条件"的音乐/舞台语句',
        message=('条件成立与否要到运行时才知道；工具把条件语句按"可能发生"处理，'
                 '因此这些章的出口状态只能算**可能是这样**，接缝遇到它们会标'
                 '"不确定"。'),
        chapters=[ch.label for ch in rows],
        detail=[f'{ch.label}：{ch.conditional_ops} 条带条件的音乐/舞台语句'
                for ch in rows]))


def _collect_python_audio(report: ContinuityReport, lg: _LabelGraph,
                          chapters: dict) -> None:
    hits = defaultdict(list)
    for gi, stmt in enumerate(lg.statements):
        kind = str(getattr(stmt, 'kind', '') or '')
        if kind != str(K.PYTHON):
            continue
        code = str((getattr(stmt, 'payload', None) or {}).get('code', '') or '')
        if not PYTHON_AUDIO_RE.search(code):
            continue
        label = (lg.statement_labels[gi]
                 if gi < len(lg.statement_labels) else '')
        hits[str(label or '（label 外）')].append(code.strip()[:60])
    if not hits:
        return
    detail = [f'{label}：{len(codes)} 处（{codes[0]}）'
              for label, codes in sorted(hits.items())]
    report.undetermined.append(Undetermined(
        kind=UND_PYTHON_AUDIO,
        title=f'判不出来：{sum(len(v) for v in hits.values())} 处直接调用 '
              'Python 音频 API',
        message=('`$ renpy.music.play(...)` / `renpy.sound.*` 这类写法不经过'
                 '剧本语句，工具不把它们当 play/stop —— 涉及这些的地方'
                 '**不判**继承。'),
        chapters=[label for label in hits if label in chapters],
        detail=detail))


def _collect_unmodeled_channels(report: ContinuityReport,
                                lg: _LabelGraph) -> None:
    """
    未建模的音频声道（`play ambience` 等）。

    解析器对这类语句走**原样块**（`RAW` / `raw_kind=unmodeled`）——
    所以两个来源都要看：PLAY 语句的 `channel`，以及原样块的第一行。
    """
    channels = defaultdict(list)
    for gi, stmt in enumerate(lg.statements):
        payload = getattr(stmt, 'payload', None) or {}
        kind = str(getattr(stmt, 'kind', '') or '')
        channel = ''
        if kind == str(K.PLAY):
            channel = str(payload.get('channel', '') or '').strip()
        elif kind == str(K.RAW):
            text = str(payload.get('text', '') or '').strip()
            m = re.match(r'(?:play|queue|stop)\s+([A-Za-z_]\w*)', text)
            if m:
                channel = m.group(1)
        if not channel or channel in MODELED_CHANNELS:
            continue
        if channel in ('expression', 'screen', 'layer', 'movie', 'voice'):
            continue
        label = (lg.statement_labels[gi]
                 if gi < len(lg.statement_labels) else '')
        channels[channel].append(str(label or '（label 外）'))
    if not channels:
        return
    detail = [f'`{channel}`：{len(labels)} 处（{"、".join(sorted(set(labels))[:6])}）'
              for channel, labels in sorted(channels.items())]
    report.undetermined.append(Undetermined(
        kind=UND_CHANNEL,
        title=f'判不出来：{len(channels)} 个未建模的音频声道',
        message=('工具只建模 music / sound / voice 三个声道。'
                 '其它声道（ambience 等）的起停不参与继承判断。'),
        detail=detail))


def _collect_fallthrough(report: ContinuityReport,
                         lg: _LabelGraph) -> None:
    """编排层末尾没有 return/jump 的（落空，低置信度）—— 留给接线检查。"""
    for label in sorted(lg.route_labels & lg.touched):
        idxs = lg.by_label.get(label) or []
        if not idxs:
            continue
        last = None
        for gi in reversed(idxs):
            if str(getattr(lg.statements[gi], 'kind', '')) != str(K.COMMENT):
                last = lg.statements[gi]
                break
        if last is None:
            continue
        if str(getattr(last, 'kind', '')) not in (str(K.RETURN), str(K.JUMP)):
            # menu 收尾的编排层很常见（每个选项都有 jump）→ 不算落空
            if any(str(getattr(lg.statements[g], 'kind', '')) == str(K.MENU_OPTION)
                   for g in idxs):
                continue
            report.undetermined.append(Undetermined(
                kind=UND_FALLTHROUGH,
                title=f'编排层末尾没有终止：{label}',
                message='最后一条不是 return/jump，Ren\'Py 会撞进同文件的下一个 '
                        'label —— 链条从这里开始不判。',
                chapters=[label]))


def _build_chains(report: ContinuityReport, chapters: dict,
                  active: list) -> list:
    """
    把"可判定接缝"串成链（给报告第一屏用）。

    链条要能接着走，必须**当前节点只有一条可判定出边**（否则下一章是谁就
    分叉了）；起点 = 没有可判定前驱、或前驱本身是分叉/汇聚（本节点只是
    其中一支）。单节点的"链"不列（它们已经在【可判定接缝】里逐条写清）。
    """
    judged_next = {}
    judged_prev = {}
    for seam in report.seams:
        if not seam.judged:
            continue
        judged_next.setdefault(seam.from_label, []).append(seam.to_label)
        judged_prev.setdefault(seam.to_label, []).append(seam.from_label)
    starts = []
    for ch in sorted(active, key=lambda c: c.order):
        outs = judged_next.get(ch.label) or []
        if not outs:
            continue
        ins = judged_prev.get(ch.label) or []
        branch_upstream = any(
            len(judged_next.get(p) or []) > 1 for p in ins)
        if not ins or len(ins) > 1 or branch_upstream:
            starts.append(ch.label)
    chains = []
    seen = set()
    for start in starts:
        if start in seen:
            continue
        chain = [start]
        seen.add(start)
        cur = start
        while True:
            outs = judged_next.get(cur) or []
            if len(outs) != 1:
                break
            nxt = outs[0]
            if nxt in seen:
                break
            chain.append(nxt)
            seen.add(nxt)
            cur = nxt
        if len(chain) > 1:
            chains.append(chain)
    report.chains = chains
    return chains


# ======================================================================
#  对外入口
# ======================================================================

def analyze_files(paths, *, project_root: str = '') -> ContinuityReport:
    """只读地分析一组 `.rpy` 文件（测试 / CLI `--file` 用）。"""
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer

    files = [str(p) for p in (paths or ())]
    report = ContinuityReport(root=str(project_root or ''), files=list(files))
    t0 = time.time()
    try:
        analysis = IncrementalRenPyAnalyzer().analyze(files=files, graph=None)
        parse_result = analysis[0] if isinstance(analysis, tuple) else analysis
    except Exception as e:                       # noqa: BLE001 - 单个坏文件不该炸整轮
        report.errors.append({'file': '(parse)', 'error': str(e)})
        report.elapsed = time.time() - t0
        return report
    res = analyze_parse_result(parse_result,
                               project_root=project_root, files=files)
    res.elapsed = time.time() - t0
    return res


def analyze_project(project_root, *, files=None) -> ContinuityReport:
    """
    扫一个项目的全部 `.rpy`，做跨章连续性体检（**只读**）。

    `files` 给了就只分析这些文件（测试 / 局部扫描），否则用
    `discover_scripts` 的发现规则。任何单个文件读不出来都只记进
    `errors`，其余结论照出。
    """
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer

    root = Path(project_root)
    paths = [str(p) for p in (files or discover_scripts(root))]
    report = ContinuityReport(root=str(root), files=list(paths))
    t0 = time.time()
    try:
        analysis = IncrementalRenPyAnalyzer().analyze(files=paths, graph=None)
        parse_result = analysis[0] if isinstance(analysis, tuple) else analysis
    except Exception as e:                       # noqa: BLE001
        report.errors.append({'file': str(root), 'error': str(e)})
        report.elapsed = time.time() - t0
        return report
    for err in getattr(parse_result, 'errors', None) or ():
        report.errors.append({'file': '(解析)', 'error': str(err)})
    try:
        res = analyze_parse_result(parse_result,
                                   project_root=str(root), files=paths)
    except Exception as e:                       # noqa: BLE001 - 报告失败不抛给调用方
        swallow('pipeline.continuity.analyze', e)
        report.errors.append({'file': '(分析)', 'error': str(e)})
        report.elapsed = time.time() - t0
        return report
    res.errors = report.errors + res.errors
    res.elapsed = time.time() - t0
    return res


def source_signature(paths) -> str:
    """
    一组源文件的**内容坐标**（路径 + mtime + size 的指纹）—— 给 UI 缓存用。

    为什么不用"读全文算 hash"：跨章分析本来就是"文件变了才失效"，而
    mtime_ns + size 对本地编辑器足够（保存一次 mtime 必变）。读全文是
    O(1.2MB)，每次打开窗口都读一遍没必要。
    """
    import hashlib

    digest = hashlib.sha1()
    for path in paths or ():
        try:
            st = Path(path).stat()
        except OSError:
            continue
        digest.update(f'{path}\0{st.st_mtime_ns}\0{st.st_size}\0'.encode(
            'utf-8', errors='replace'))
    return digest.hexdigest()


def project_source_signature(project_root) -> str:
    """整个项目（按发现规则）的源文件指纹 —— UI 的"要不要重新分析"判据。"""
    try:
        return source_signature(discover_scripts(Path(project_root)))
    except OSError:
        return ''


__all__ = [
    'Chapter', 'ContinuityReport', 'Finding', 'Seam', 'Undetermined',
    'analyze_files', 'analyze_parse_result', 'analyze_project',
    'source_signature', 'project_source_signature',
    'CONTENT_KINDS', 'ELEMENT_ZH', 'LEVEL_INFO', 'LEVEL_WARN',
    'PYTHON_AUDIO_RE', 'MODELED_CHANNELS',
]
