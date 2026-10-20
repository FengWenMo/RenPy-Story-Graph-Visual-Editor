"""
storygraph.pipeline.ir_loss
===========================
**中立 IR → 目标引擎：到底丢了什么，逐条写出来。**

用户 2026-10-20（本轮目标）：

> 增强多引擎 IR 与适配（校验 / 损失报告 / 更多引擎）

"导出了"和"导全了"是两件事。一份 `story_ir.json` 里有 30 个行级字段，
Unity 适配层只搬走 20 个 —— 剩下 10 个如果没人说，用户会以为
"Unity 那边和编辑器里看到的一样"。真实后果是：立绘站位（`at` /
`transform`）、BGM 参数（`fadein`）、DSL 源码（`code`）、策划备注
（`comment`）都没了，而导出回执只写"成功"。

本模块只做**一件事**：拿中立 IR 和适配器真正写出去的字段对账，产出
`FieldLoss` 列表与一份可读的 Markdown 报告。它**不删也不改** IR ——
中立 IR 永远保留全量字段，丢的是"目标引擎那一份"。

判据在 `tests/test_ir_loss.py`：

  · 适配器加了字段，这里没同步 → 测试红（不是"报告少写一行"）；
  · 适配器**没**搬的字段必须出现在报告里（不许静默消失）；
  · 资源维度的 `attributes`（§16.1 的结构化成果）Unity 没搬 → 必须点名。

文件落盘在导出目录：`IR_LOSS-unity.md` / `IR_LOSS-godot.md`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.story.paths import write_text_atomic

#: 报告文件名（`{engine}` 会替换成引擎 id）。
LOSS_REPORT_NAME = 'IR_LOSS-{engine}.md'

#: 行级字段的中文名（报告里给人看；没列的用字段名本身）。
LINE_FIELD_LABELS = {
    'line_id': '行 id',
    'label': 'label',
    'seq': '序号',
    'kind': '类型',
    'speaker': '角色变量',
    'speaker_name': '显示名',
    'text': '台词',
    'condition': '条件表达式',
    'stage': 'DSL 舞台原文',
    'bgm': 'BGM',
    'sfx': '音效',
    'scene': '背景',
    'actors': '在场立绘',
    'music': '音乐（快照）',
    'music_action': '音乐动作',
    'music_params': '音乐参数（淡入/循环）',
    'sound': '音效（`sound`）',
    'sound_action': '音效动作',
    'sound_params': '音效参数',
    'voice': '语音',
    'at': '立绘站位（`at`）',
    'with': '转场',
    'transform': 'transform 名',
    'comment': '策划备注',
    'raw': '原始行',
    'code': 'DSL 源码（`code`）',
    'nest': '嵌套路径',
    'structure': '结构上下文',
    'timeline': '演出时间轴',
    'targets': '跳转目标',
    'line_hash': '内容摘要',
}

#: 资源级字段的中文名。
ASSET_FIELD_LABELS = {
    'id': '资源 id',
    'type': '类型',
    'path': '路径',
    'tag': 'tag',
    'category': '分类',
    'doc': '说明',
    'loop': '循环',
    'volume': '音量',
    'attributes': '维度结构（`{组: 值}`）',
    'attribute_values': '认不出维度的属性值',
    'group': '显式维度',
    'value': '显式值',
}

#: 每个引擎的适配器**实际带过去**的行级字段：`{中立字段: 目标字段}`。
#:
#: `''` = 适配器明确不带（会被写进报告）。表里没有的字段同样按"没带"算 ——
#: 这是故意的：IR 新加一个字段而适配器没跟上时，报告会立刻多一条，
#: `tests/test_ir_loss.py` 会红，而不是悄悄丢。
LINE_FIELD_MAP = {
    'unity': {
        'line_id': 'lineId',
        'label': 'label',
        'seq': 'seq',
        'kind': 'kind',
        'speaker': 'speakerId',
        'speaker_name': 'speakerName',
        'text': 'text',
        'condition': 'condition',
        'scene': 'background',
        'actors': 'poses',
        'music': 'bgm',
        # `bgm` / `sfx` 是拍上**原始那两列**，`music` / `sound` 是快照算出来的
        # 播放值；适配层只带一份（快照优先），所以两者都算"带过去了"。
        'bgm': 'bgm',
        'sfx': 'sfx',
        'music_action': 'bgmAction',
        'sound': 'sfx',
        'sound_action': 'sfxAction',
        'voice': 'voice',
        'nest': 'nest',
        'structure': ('structurePath', 'structureKind', 'structureKnown'),
        'timeline': 'timeline',
        'with': 'transition',
        'targets': 'targets',
        'line_hash': 'lineHash',
        # —— 以下 Unity 侧没有对应字段 ——
        'stage': '',
        'music_params': '',
        'sound_params': '',
        'at': '',
        'transform': '',
        'comment': '',
        'raw': '',
        'code': '',
    },
    'godot': {
        'line_id': 'line_id',
        'label': 'label',
        'seq': 'seq',
        'kind': 'kind',
        'speaker': 'speaker_id',
        'speaker_name': 'speaker_name',
        'text': 'text',
        'condition': 'condition',
        'nest': 'nest',
        'structure': 'structure',
        'timeline': 'timeline',
        'with': 'transition',
        'targets': 'targets',
        'line_hash': 'line_hash',
        # Godot 把舞台字段收进 `stage` 这一层
        'scene': ('stage', 'background'),
        'actors': ('stage', 'poses'),
        'music': ('stage', 'bgm'),
        'bgm': ('stage', 'bgm'),
        'sfx': ('stage', 'sfx'),
        'music_action': ('stage', 'bgm_action'),
        'sound': ('stage', 'sfx'),
        'sound_action': ('stage', 'sfx_action'),
        'voice': ('stage', 'voice'),
        # —— 以下 Godot 侧没有对应字段 ——
        'stage': '',
        'music_params': '',
        'sound_params': '',
        'at': '',
        'transform': '',
        'comment': '',
        'raw': '',
        'code': '',
    },
}

#: 每个引擎带过去的资源级字段。
ASSET_FIELD_MAP = {
    'unity': ('id', 'type', 'path', 'tag', 'category', 'doc'),
    'godot': ('id', 'type', 'path', 'tag', 'category', 'doc', 'loop',
              'volume', 'attributes', 'attribute_values', 'group', 'value'),
}

#: 直接使用中立 IR 的引擎（没有适配层 → 没有字段损失）。
PASSTHROUGH_ENGINES = ('renpy', 'json', 'csv')

_TAG_RE = re.compile(r'\{[^{}]*\}')


@dataclass
class FieldLoss:
    """一个字段在目标引擎那一份里丢了（中立 IR 里还在）。"""

    scope: str              # 'line' / 'asset'
    field: str              # 中立字段名
    label: str              # 中文名（报告用）
    count: int              # 有多少行/多少条资源带这个字段
    examples: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {'scope': self.scope, 'field': self.field,
                'label': self.label, 'count': self.count,
                'examples': list(self.examples)}


def _blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def field_losses(ir: dict, engine: str) -> list:
    """中立 IR vs `engine` 适配器 → `[FieldLoss]`（按字段聚合）。"""
    engine = str(engine or '').lower()
    if engine in PASSTHROUGH_ENGINES:
        return []
    line_map = LINE_FIELD_MAP.get(engine)
    if line_map is None:
        return []
    lines = [l for l in (ir.get('lines') or []) if isinstance(l, dict)]
    out = []

    def collect(scope: str, rows: list, keep, labels):
        seen_fields = set()
        for row in rows:
            seen_fields.update(k for k, v in row.items() if not _blank(v))
        for field_name in sorted(seen_fields):
            if field_name in keep:
                continue
            hits = [r for r in rows if not _blank(r.get(field_name))]
            if not hits:
                continue
            out.append(FieldLoss(
                scope=scope, field=field_name,
                label=labels.get(field_name, field_name),
                count=len(hits),
                examples=[str(r.get('line_id') or r.get('id') or '')
                          for r in hits[:3]]))

    collect('line', lines, {k for k, v in line_map.items() if v},
            LINE_FIELD_LABELS)

    asset_map = ASSET_FIELD_MAP.get(engine)
    if asset_map is not None:
        assets = []
        for group_rows in (ir.get('assets') or {}).values():
            assets.extend([a for a in (group_rows or []) if isinstance(a, dict)])
        collect('asset', assets, set(asset_map), ASSET_FIELD_LABELS)
    return out


def _notes(ir: dict) -> list:
    """不算"字段丢失"，但目标引擎要自己处理的事（报告里的提醒）。"""
    notes = []
    lines = [l for l in (ir.get('lines') or []) if isinstance(l, dict)]
    tagged = [l for l in lines if _TAG_RE.search(str(l.get('text') or ''))]
    if tagged:
        notes.append(
            f'{len(tagged)} 行台词带 Ren\'Py 标签（如 {{color=…}} / {{w}}），'
            'IR 只保留原文，目标引擎要自己解析或原样显示。')
    unknown = [l for l in lines
               if (l.get('structure') or {}).get('known') is False]
    if unknown:
        notes.append(
            f'{len(unknown)} 行的嵌套位置未知（老表没有结构位），'
            '适配层会写成 structureKnown=false，别把它们当顶层。')
    no_path = []
    for group_rows in (ir.get('assets') or {}).values():
        for a in (group_rows or []):
            if isinstance(a, dict) and not str(a.get('path') or '').strip():
                no_path.append(str(a.get('id') or ''))
    if no_path:
        notes.append(f'{len(no_path)} 条资源没有 `Path`（导出后目标引擎加载不到）。')
    return notes


def summarize(ir: dict, engine: str) -> dict:
    """`{engine, lines, assets, losses, by_scope, notes}`（给 UI/CLI 用的结构）。"""
    losses = field_losses(ir, engine)
    by_scope = {}
    for loss in losses:
        by_scope.setdefault(loss.scope, []).append(loss.as_dict())
    lines = len(ir.get('lines') or [])
    assets = sum(len(v or ()) for v in (ir.get('assets') or {}).values())
    return {
        'engine': str(engine or '').lower(),
        'lines': lines,
        'assets': assets,
        'losses': [l.as_dict() for l in losses],
        'by_scope': by_scope,
        'notes': _notes(ir),
        'passthrough': str(engine or '').lower() in PASSTHROUGH_ENGINES,
    }


def report_markdown(ir: dict, engine: str) -> str:
    """一份给人看的损失报告（Markdown）。"""
    summary = summarize(ir, engine)
    engine = summary['engine']
    out = [f'# IR 损失报告 · {engine}', '',
           f'- 行：{summary["lines"]}，资源：{summary["assets"]}',
           '- 中立 IR 里字段**一个都没删**；下面是这个引擎的适配层没有'
           '搬过去的部分。', '']
    if summary['passthrough']:
        out += ['这个引擎直接使用中立 IR（`story_ir.json`），没有适配层，'
                '因此没有字段损失。', '']
        return '\n'.join(out)

    if not summary['losses']:
        out += ['没有字段损失：适配层把所有非空字段都搬过去了。', '']
    else:
        for scope, title in (('line', '行级字段'), ('asset', '资源级字段')):
            rows = summary['by_scope'].get(scope) or []
            if not rows:
                continue
            out += [f'## {title}', '',
                    '| 字段 | 含义 | 数量 | 例子 |', '|---|---|---|---|']
            for row in rows:
                examples = '、'.join(e for e in row['examples'] if e) or '—'
                out.append(f'| `{row["field"]}` | {row["label"]} | '
                           f'{row["count"]} | {examples} |')
            out.append('')

    if summary['notes']:
        out += ['## 提醒（不算字段丢失）', '']
        out += [f'- {n}' for n in summary['notes']]
        out.append('')
    out += ['## 怎么补', '',
            '- 想让目标引擎拿到某个字段：改 '
            '`pipeline/engine_exporters.py` 里对应的 `to_<engine>()`，'
            '再在 `ir_loss.LINE_FIELD_MAP` / `ASSET_FIELD_MAP` 里把它标成"带过去"。',
            '- 两边不同步时 `tests/test_ir_loss.py` 会红 —— 报告和适配器是同一份事实。']
    return '\n'.join(out)


def write_report(ir: dict, engine: str, out_dir) -> str:
    """写 `IR_LOSS-<engine>.md`，返回路径（写不进去返回空串，不炸导出）。"""
    name = LOSS_REPORT_NAME.format(engine=str(engine or '').lower())
    path = Path(out_dir) / name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 走项目**唯一的原子写原语**（`story/paths.py`）：这里写的是导出目录里
        # 的一份报告，但它同样不该出现"半截文件"（`test_single_write_entry` 守着）。
        write_text_atomic(path, report_markdown(ir, engine))
        return str(path)
    except OSError as e:
        swallow('pipeline.ir_loss.write_report', e)
        return ''


__all__ = ['ASSET_FIELD_MAP', 'ASSET_FIELD_LABELS', 'FieldLoss',
           'LINE_FIELD_LABELS', 'LINE_FIELD_MAP', 'LOSS_REPORT_NAME',
           'PASSTHROUGH_ENGINES', 'field_losses', 'report_markdown',
           'summarize', 'write_report']
