"""
engine_exporters.py
===================
多引擎导出：让"这份演出数据"不只属于 Ren'Py。

背景（项目所有者要求）：团队现在用 Ren'Py，但后续可能迁 Unity 或更复杂的
引擎，所以演出数据必须能脱离 Ren'Py 语法独立存在。

架构选择：**先做引擎中立 IR，再做适配器**，而不是写两个互不相干的导出器。

    Beat(演出表) ──► 叙事 IR（中立 JSON）──┬─► Ren'Py  : 由 script_table 生成
                                          ├─► Unity   : lines[] + 资源清单
                                          ├─► Godot   : lines[] + GDScript 载入示例
                                          └─► CSV/JSON: 给翻译、给别的工具

中立 IR 的形状刻意做得"平"（list of dict，字段全是标量或字符串列表）：
  · Unity 的 JsonUtility **不能反序列化嵌套字典**，所以不要用 dict-of-dict；
  · Godot 的 JSON.parse 完整支持，但平结构更好写加载器；
  · 平结构也让 diff 好看（团队会把它提交进 git 做 review）。

IR 里每条 line 都带 LineId：这是存档、埋点、翻译、协作锁共同的主键。
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from contextlib import suppress
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.pipeline.ir_loss import LOSS_REPORT_NAME, report_markdown
from storygraph.pipeline.ir_validate import (CHECK_REPORT_NAME,
                                             crash_report as check_crashed,
                                             report_markdown as check_report)
from storygraph.story.beat_model import split_hide_items, split_show_items
from storygraph.story.stage_snapshot import stage_snapshots

SCHEMA_VERSION = '1.1'          # 1.1（2026-10-19）：+ structure / timeline


#: `jump <name>` / `call <name>` —— 一行里可能有多跳（`call screen` 不算接线）。
_JUMP_RE = re.compile(r'^\s*(jump|call)\s+([A-Za-z_]\w*)', re.M)


def structure_of(beat) -> dict:
    """
    这一拍的**结构上下文**（给 Unity/Godot 重建 if / menu / loop 用）。

    2026-10-19（跨引擎第一片）：此前只有 `nest` 一列字符串，别的引擎拿到
    `menu:1>B` 不知道它是什么；这里补 `kind`（`if` / `menu` / `loop` /
    `option`）与 `known`（`False` = 老表不知道，别拿它当"顶层"）。
    """
    path = getattr(beat, 'nest_path', None)
    known = path is not None
    text = str(path or '')
    kind = ''
    if text:
        last = text.split('>')[-1]
        if ':' in last:
            kind = last.split(':', 1)[0]
        elif last:
            kind = 'option'              # 选项体的位置键（A/B/…）
    elif getattr(beat, 'kind', '') == 'menu_option':
        kind = 'option'
    return {'path': text, 'kind': kind, 'known': bool(known)}


def timeline_of(beat) -> list:
    """
    这一拍的**演出时间轴**：按作者写的顺序列出舞台/音频/转场操作。

    为什么需要（跨引擎第一片）：Unity 侧只拿到 `scene` / `poses` / `bgm`
    这类"最终值"不够 —— 它不知道 `show` 和 `hide` 谁先谁后、`with`
    转场挂在哪一步。这里把 `Beat.op_tokens_full()`（与 Ren'Py 渲染器
    同一条顺序真源）摊成扁平的 `[{op,target,…,order}]`。

    认不出的操作**不丢**：没被任何 token 点到的字段会补在末尾；
    `raw`/`code` 仍然原样留在 line 上，不在这里猜语义。
    """
    show_items = [i for i in split_show_items(getattr(beat, 'show', '')) if i]
    hide_items = [i for i in split_hide_items(getattr(beat, 'hide', '')) if i]
    scene = str(getattr(beat, 'scene', '') or '')
    scene_at = str(getattr(beat, 'scene_at', '') or '')
    with_ = str(getattr(beat, 'with_', '') or '')
    at = str(getattr(beat, 'at', '') or '')
    transform = str(getattr(beat, 'transform', '') or '')
    events: list = []
    emitted = set()

    def push(op, target='', **extra):
        item = {'op': str(op), 'target': str(target or ''),
                'order': len(events)}
        item.update({k: str(v or '') for k, v in extra.items()})
        events.append(item)

    def emit_show(idx=None):
        items = ([show_items[idx]] if idx is not None
                 and 0 <= idx < len(show_items) else show_items)
        for it in items:
            if not it or not str(it.get('image') or ''):
                continue
            key = ('show', str(it.get('image') or ''))
            if key in emitted:
                continue
            emitted.add(key)
            push('show', it.get('image'), at=it.get('at'),
                 clause=it.get('clause'), transition=it.get('with'))

    def emit_hide(idx=None):
        items = ([hide_items[idx]] if idx is not None
                 and 0 <= idx < len(hide_items) else hide_items)
        for it in items:
            if not it or not str(it.get('image') or ''):
                continue
            key = ('hide', str(it.get('image') or ''))
            if key in emitted:
                continue
            emitted.add(key)
            push('hide', it.get('image'), transition=it.get('with'))

    # **顺序真源**：`op_tokens_full()` 与 Ren'Py 渲染器同源；拿不到就按字段补。
    tokens = []
    try:
        tokens = list(beat.op_tokens_full() or ())
    except Exception as e:                    # noqa: BLE001 - 手工构造的拍可能没有
        swallow('pipeline.engine_exporters.timeline_tokens', e)
    for verb, idx in tokens:
        if verb == 'scene' and scene and ('scene', scene) not in emitted:
            emitted.add(('scene', scene))
            push('scene', scene, at=scene_at)
        elif verb == 'show':
            emit_show(idx)
        elif verb == 'hide':
            emit_hide(idx)
        elif verb == 'with' and with_ and ('with', with_) not in emitted:
            emitted.add(('with', with_))
            push('with', with_)
        elif verb in ('music', 'sound', 'audio'):
            action = (getattr(beat, 'music_action', '') if verb == 'music'
                      else getattr(beat, 'sound_action', ''))
            target = (getattr(beat, 'music', '') if verb == 'music'
                      else getattr(beat, 'sound', ''))
            params = (getattr(beat, 'music_params', '') if verb == 'music'
                      else getattr(beat, 'sound_params', ''))
            if target and (verb, target) not in emitted:
                emitted.add((verb, target))
                push(verb, target, action=action, params=params)
        elif verb == 'voice':
            voice = str(getattr(beat, 'voice', '') or '')
            if voice and ('voice', voice) not in emitted:
                emitted.add(('voice', voice))
                push('voice', voice)

    # —— 补齐：有内容却没被 token 点到的操作（绝不因为顺序不全就丢）——
    if scene and ('scene', scene) not in emitted:
        push('scene', scene, at=scene_at)
    for it in show_items:
        if it and it.get('image'):
            key = ('show', str(it.get('image')))
            if key not in emitted:
                emit_show(show_items.index(it))
    for it in hide_items:
        if it and it.get('image'):
            key = ('hide', str(it.get('image')))
            if key not in emitted:
                emit_hide(hide_items.index(it))
    if with_ and ('with', with_) not in emitted:
        push('with', with_)
    music = str(getattr(beat, 'music', '') or '')
    if music and ('music', music) not in emitted:
        push('music', music, action=getattr(beat, 'music_action', ''),
             params=getattr(beat, 'music_params', ''))
    sound = str(getattr(beat, 'sound', '') or '')
    if sound and ('sound', sound) not in emitted:
        push('sound', sound, action=getattr(beat, 'sound_action', ''),
             params=getattr(beat, 'sound_params', ''))
    voice = str(getattr(beat, 'voice', '') or '')
    if voice and ('voice', voice) not in emitted:
        push('voice', voice)
    if at:
        push('at', at)
    if transform:
        push('transform', transform)
    return events


def jump_targets_of(beat) -> list:
    """
    这一拍引用的**跳转目标**（`jump` / `call` 的 label 名，保序去重）。

    为什么要单独一个字段（2026-10-08 修）：适配层（Unity/Godot）只搬了
    "台词/说话人/背景"这些**演出字段**，`jump`/`call` 活在 `raw`/`code` 里
    没被搬过去 —— 换引擎后**接线断了**（真实项目 fixture 上量到 unity/godot
    各丢 5~12 跳，`tools/hell_multi_engine.py`）。接线是叙事图最要紧的东西，
    必须成为**一等字段**，不能只藏在原文里。
    """
    out = []
    for src in (getattr(beat, 'raw', '') or '',
                getattr(beat, 'code', '') or ''):
        for m in _JUMP_RE.finditer(str(src)):
            verb, name = m.group(1), m.group(2)
            if verb == 'call' and name in ('screen', 'expression'):
                continue
            if name not in out:
                out.append(name)
    return out


# ==================================================================
#  1. Beat → 引擎中立叙事 IR
# ==================================================================

def build_neutral_ir(beats, registry=None, project: str = '',
                     title: str = '') -> dict:
    """
    演出表 → 引擎中立叙事数据。

    结构：
      {
        "schema": "renpy-story-ir/1.1",
        "project": "...", "title": "...",
        "characters": [{"id","name","color","image_tag","voice_tag"}...],
        "lines": [ {line_id,label,seq,kind,speaker,speaker_name,text,
                    condition,scene,actors[],music,music_action,
                    sound,sound_action,voice,comment,raw,code,
                    structure:{path,kind,known},
                    timeline:[{op,target,at,clause,transition,action,
                               params,order}...],
                    source:{file,line}} ... ],
        "assets": {"images":[{id,path,tag,category}...], "music":[...], ...},
        "stage": [ {line_id,scene,actors,music} ... ],
        "stats": {...}
      }
    """
    snaps = stage_snapshots(beats)
    snap_by_id = {s['line_id']: s for s in snaps}

    characters = []
    char_name = {}
    if registry is not None:
        for cid, row in sorted((getattr(registry, 'characters', {}) or {})
                               .items()):
            row = row or {}
            name = str(row.get('Name') or cid)
            char_name[cid] = name
            characters.append({
                'id': cid,
                'name': name,
                'color': str(row.get('Color') or ''),
                'image_tag': str(row.get('ImageTag') or ''),
                'voice_tag': str(row.get('VoiceTag') or ''),
                'dialogue_count': int(row.get('_dialogue_count') or 0),
            })

    lines = []
    for b in beats or []:
        snap = snap_by_id.get(getattr(b, 'line_id', ''), {}) or {}
        lines.append({
            'line_id': getattr(b, 'line_id', '') or '',
            'label': b.label,
            'seq': int(b.seq or 0),
            'kind': b.kind,
            'speaker': b.speaker,
            'speaker_name': char_name.get(b.speaker, b.speaker),
            'text': b.text,
            'condition': b.condition,
            # DSL 原文也带上：Unity/Godot 侧想还原"策划原本想干什么"，
            # 比只给编译后的 scene/actors 更有用（也便于将来换引擎）。
            'stage': getattr(b, 'stage', '') or '',
            'bgm': getattr(b, 'bgm', '') or '',
            'sfx': getattr(b, 'sfx', '') or '',
            'scene': snap.get('scene', b.scene),
            'actors': [a for a in (snap.get('actors') or '').split('; ')
                       if a],
            'music': snap.get('music', b.music),
            'music_action': b.music_action,
            'music_params': b.music_params,
            'sound': b.sound,
            'sound_action': b.sound_action,
            'sound_params': b.sound_params,
            'voice': b.voice,
            'at': b.at,
            'with': b.with_,
            'transform': b.transform,
            'comment': b.comment,
            'raw': b.raw,
            'code': b.code,
            # 「结构位」（嵌套层级 / 选项归属）与「接线」（跳转目标）都是一等字段：
            # 前者让别的引擎能重建菜单/条件树，后者让剧情图接得上
            # （2026-10-08 修，见 `jump_targets_of`）。
            'nest': getattr(b, 'nest_path', None) or '',
            # 跨引擎第一片（2026-10-19）：结构上下文 + 演出时间轴。
            # `structure.known=False` = 老表不知道这一拍在哪，别当顶层。
            'structure': structure_of(b),
            'timeline': timeline_of(b),
            'targets': jump_targets_of(b),
            'line_hash': getattr(b, 'content_hash', '') or '',
        })

    return {
        'schema': f'renpy-story-ir/{SCHEMA_VERSION}',
        'project': project,
        'title': title or project,
        'characters': characters,
        'lines': lines,
        'assets': _asset_manifest(registry),
        'stage': [{'line_id': s['line_id'], 'scene': s['scene'],
                   'actors': [a for a in (s['actors'] or '').split('; ')
                              if a],
                   'music': s['music']} for s in snaps],
        'stats': {
            'lines': len(lines),
            'labels': len({l['label'] for l in lines if l['label']}),
            'characters': len(characters),
            'spoken_lines': sum(1 for l in lines if l['text']),
            'chars': sum(len(l['text']) for l in lines),
        },
    }


def _asset_manifest(registry) -> dict:
    if registry is None:
        return {}
    from storygraph.assets.asset_dimensions import dimensions_of_row
    from storygraph.core.safeguard import swallow
    out = {}
    for attr, kind in (('images', 'image'), ('music', 'music'),
                       ('sounds', 'sound'), ('voices', 'voice'),
                       ('transforms', 'transform')):
        rows = []
        for aid, row in sorted((getattr(registry, attr, {}) or {}).items()):
            row = row or {}
            entry = {'id': aid, 'type': kind}
            for key in ('Path', 'Tag', 'Category', 'Loop', 'Volume'):
                if row.get(key) not in (None, ''):
                    entry[key.lower()] = row[key]
            if kind == 'image':
                # **结构化维度**（2026-10-19）：`{outfit: base_swimsuit,
                # expressions: happy}`。别的引擎要的是"这一维选了什么"，
                # 不是一串只能靠猜词边界的属性名。
                # 认不出维度的词放 `attribute_values`，**一个都不丢**。
                meta_dims = []
                try:
                    dims = row.get('_dims') or dimensions_of_row(row)
                    # 置信度（2026-10-20）：维度是"表里写的"还是"猜的"，
                    # 目标引擎要能区别对待 —— `attribute_meta` 带 source/confidence。
                    meta_dims = row.get('_dim_meta') or dimensions_of_row(
                        row, meta=True)
                except Exception as e:            # noqa: BLE001
                    swallow('pipeline.engine_exporters.dimensions', e)
                    dims = []
                    meta_dims = []
                grouped: dict = {}
                loose: list = []
                for d in (dims or ()):
                    group = str((d or {}).get('group') or '')
                    value = str((d or {}).get('value') or '')
                    if not value:
                        continue
                    if group:
                        # 一个维度只该有一个值；若表里写了两个（W115 的
                        # 现场），**第一个进结构化字段，其余进 loose** ——
                        # 绝不静默丢词。
                        if group not in grouped:
                            grouped[group] = value
                        elif value not in loose:
                            loose.append(value)
                    elif value not in loose:
                        loose.append(value)
                if grouped:
                    entry['attributes'] = grouped
                if loose:
                    entry['attribute_values'] = loose
                if meta_dims:
                    entry['attribute_meta'] = [
                        {'group': str(d.get('group') or ''),
                         'value': str(d.get('value') or ''),
                         'source': str(d.get('source') or ''),
                         'confidence': float(d.get('confidence') or 0.0)}
                        for d in meta_dims]
                if row.get('Group') not in (None, ''):
                    entry['group'] = row['Group']
                if row.get('Value') not in (None, ''):
                    entry['value'] = row['Value']
            if row.get('Doc'):
                entry['doc'] = row['Doc']
            rows.append(entry)
        out[attr] = rows
    return out


# ==================================================================
#  2. 适配器
# ==================================================================

def _rows(value) -> list:
    """
    `lines` / 资源分组这种"应该是一组对象"的值 → 只留 `dict`。

    适配器必须是**总函数**（2026-10-20 修，P1）：IR 是外部数据（别人手改过、
    老版本写的、或者生成时就坏了）。从前 `to_unity({'lines': 'nope'})` 直接
    `TypeError: string indices must be integers`，而它在 `export_ir` 的写盘
    流程里（自检报告要调它）—— 于是**坏 IR 让整次导出崩掉**，
    `IR_CHECK.md` 出不来，用户拿不到任何"哪里坏了"的信息。
    现在一律收敛成空值，让自检去报 `E-IR-LINE-SHAPE` / `E-AD-UNITY-KEYS`。
    """
    if not isinstance(value, (list, tuple)):
        return []
    return [row for row in value if isinstance(row, dict)]


def _scalar(value, default=''):
    """标量字段原样保留；对象/数组/None 降级成默认值（适配产物只要标量）。"""
    if isinstance(value, (str, int, float, bool)):
        return value
    return default


def _strings(value) -> list:
    """字符串数组字段（`poses` / `targets`）：非数组 → 空，元素一律转字符串。"""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v) for v in value if isinstance(v, (str, int, float))]


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def to_unity(ir: dict) -> dict:
    """
    Unity 适配：扁平、字段名英文小驼峰、无嵌套字典。

    目标是 `JsonUtility.FromJson<DialogueData>(json)` 能直接吃下去，
    所以数组元素只能是"标量字段 + 字符串数组"。
    """
    ir = _mapping(ir)
    return {
        'schema': _scalar(ir.get('schema')),
        'project': _scalar(ir.get('project')),
        'characters': [{
            'id': _scalar(c.get('id')),
            'displayName': _scalar(c.get('name')),
            'color': _scalar(c.get('color')),
            'imageTag': _scalar(c.get('image_tag')),
            'voiceTag': _scalar(c.get('voice_tag')),
        } for c in _rows(ir.get('characters'))],
        'lines': [{
            'lineId': _scalar(l.get('line_id')),
            'label': _scalar(l.get('label')),
            'seq': _scalar(l.get('seq'), 0),
            'kind': _scalar(l.get('kind')),
            'speakerId': _scalar(l.get('speaker')),
            'speakerName': _scalar(l.get('speaker_name')),
            'text': _scalar(l.get('text')),
            'condition': _scalar(l.get('condition')),
            'background': _scalar(l.get('scene')),
            'poses': _strings(l.get('actors')),
            'bgm': _scalar(l.get('music')),
            'bgmAction': _scalar(l.get('music_action')),
            'sfx': _scalar(l.get('sound')),
            'sfxAction': _scalar(l.get('sound_action')),
            'voice': _scalar(l.get('voice')),
            'nest': _scalar(l.get('nest')),
            # 结构上下文 + 演出时间轴（2026-10-19，跨引擎第一片）。
            # 刻意**摊平**：`structure` 拆成三个标量，`timeline` 是
            # 扁平对象数组（JsonUtility 能反序列化；不要嵌套字典）。
            'structurePath': _scalar(_mapping(l.get('structure')).get('path')),
            'structureKind': _scalar(_mapping(l.get('structure')).get('kind')),
            'structureKnown': bool(_mapping(l.get('structure')).get('known')),
            'timeline': _rows(l.get('timeline')),
            'transition': _scalar(l.get('with')),
            'targets': _strings(l.get('targets')),
            'lineHash': _scalar(l.get('line_hash')),
        } for l in _rows(ir.get('lines'))],
        'assets': _unity_assets(ir.get('assets', {})),
    }


def _unity_assets(assets: dict) -> list:
    """
    Unity 侧更习惯一个扁平的资源数组（配合 Addressables key）。

    `assets` 坏了（不是对象 / 分组不是数组 / 条目不是对象）→ 跳过错的那一层，
    不抛：这份列表是给 Addressables 的，少一条比整份读不出来强。
    """
    rows = []
    for group, items in _mapping(assets).items():
        for it in _rows(items):
            rows.append({
                'id': _scalar(it.get('id')),
                'type': _scalar(it.get('type'),
                                str(group or '').rstrip('s')),
                'path': _scalar(it.get('path')),
                'tag': _scalar(it.get('tag')),
                'category': _scalar(it.get('category')),
                'doc': _scalar(it.get('doc')),
            })
    return rows


def to_godot(ir: dict) -> dict:
    """Godot 适配：保留嵌套（GDScript 的 JSON.parse 能吃），key 用 snake_case。"""
    ir = _mapping(ir)
    return {
        'schema': _scalar(ir.get('schema')),
        'project': _scalar(ir.get('project')),
        'characters': [{
            'id': _scalar(c.get('id')),
            'display_name': _scalar(c.get('name')),
            'color': _scalar(c.get('color')),
            'image_tag': _scalar(c.get('image_tag')),
            'voice_tag': _scalar(c.get('voice_tag')),
        } for c in _rows(ir.get('characters'))],
        'lines': [{
            'line_id': _scalar(l.get('line_id')),
            'label': _scalar(l.get('label')),
            'seq': _scalar(l.get('seq'), 0),
            'kind': _scalar(l.get('kind')),
            'speaker_id': _scalar(l.get('speaker')),
            'speaker_name': _scalar(l.get('speaker_name')),
            'text': _scalar(l.get('text')),
            'condition': _scalar(l.get('condition')),
            'nest': _scalar(l.get('nest')),
            'structure': _mapping(l.get('structure')),
            'timeline': _rows(l.get('timeline')),
            'transition': _scalar(l.get('with')),
            'targets': _strings(l.get('targets')),
            'stage': {
                'background': _scalar(l.get('scene')),
                'poses': _strings(l.get('actors')),
                'bgm': _scalar(l.get('music')),
                'bgm_action': _scalar(l.get('music_action')),
                'sfx': _scalar(l.get('sound')),
                'sfx_action': _scalar(l.get('sound_action')),
                'voice': _scalar(l.get('voice')),
            },
            'line_hash': _scalar(l.get('line_hash')),
        } for l in _rows(ir.get('lines'))],
        'assets': _mapping(ir.get('assets')),
    }


#: 产物**文件名**的唯一真源（"同一件事只许有一处实现"）。
#:
#: 为什么必须集中（2026-10-05 交付质检实测的缺陷）：Godot 那条路原先写
#: `STORY_IR.json`，而上面第 307 行刚写过 `story_ir.json`。在 **Windows /
#: macOS 这种大小写不敏感**的文件系统上，这两个名字**是同一个文件** ——
#: 第二刀把引擎中立 IR 原地覆盖掉了，于是：
#:
#:     · `_write_json(to_godot(ir), …)` 之后目录里**根本没有中立 IR**，
#:       而返回值却报了两个不同路径（消费者照单去读会读到错的那份）；
#:     · 在**大小写敏感**的 Linux 上则反过来 —— 中立 IR 活着，但
#:       `dialogue_importer.gd` 里写的 `res://STORY_IR.json` 与适配文件
#:       同名不同物，两边行为**不一致**（同一份产物跨平台不一样）。
#:
#: 现在给 Godot 适配文件一个**独立**的名字，与 Unity 的
#: `UnityDialogueData.json` 对齐（PascalCase + 引擎名），
#: 并且加载器脚本里的路径**从同一个常量生成** —— 改名不会再漂移。
#: 守卫：`tests/test_pipeline_batch.py::TestEngineExporters`
#: （`test_no_two_product_names_collide_on_case_insensitive_fs` /
#:  `test_godot_data_is_distinct_and_loader_points_at_it`）。
NEUTRAL_IR_NAME = 'story_ir.json'
UNITY_DATA_NAME = 'UnityDialogueData.json'
UNITY_NOTES_NAME = 'UNITY_IMPORT.md'
GODOT_DATA_NAME = 'GodotDialogueData.json'
GODOT_LOADER_NAME = 'dialogue_importer.gd'
CSV_NAME = 'story_lines.csv'

#: 本适配产物与 Godot 加载器共用同一个**中立 IR**：适配文件是"给 GDScript
#: 直接吃的扁平结构"，中立 IR 是"给自定义解析用的全量数据"，两份都要在。
GODOT_LOADER_SAMPLE = '''# dialogue_importer.gd —— 读 GodotDialogueData.json（由编辑器导出）
# 用法：把本文件与 GodotDialogueData.json 放进项目，附到一个 Node 上即可。
#
# 同目录下还有一份 `story_ir.json`（引擎中立 IR）：字段更全（含 stage 快照、
# 资源清单、DSL 原文），需要自定义解析时用它。本加载器读的是**扁平适配版**。
extends Node

const IR_PATH := "res://GodotDialogueData.json"

var lines_by_id: Dictionary = {}
var labels: Dictionary = {}
var characters: Dictionary = {}

func _ready() -> void:
    var f := FileAccess.open(IR_PATH, FileAccess.READ)
    assert(f != null, "找不到 GodotDialogueData.json")
    var data: Dictionary = JSON.parse_string(f.get_as_text())
    for c in data.get("characters", []):
        characters[c["id"]] = c
    for l in data.get("lines", []):
        lines_by_id[l["line_id"]] = l
        if not labels.has(l["label"]):
            labels[l["label"]] = []
        labels[l["label"]].append(l["line_id"])
    print("载入 %d 行 / %d 个 label" % [lines_by_id.size(), labels.size()])

# 按 line_id 取一条演出数据；stage 里已经是"算好的舞台状态"，
# 不需要再自己叠加 scene/show/hide。
func get_line(line_id: String) -> Dictionary:
    return lines_by_id.get(line_id, {})

func label_lines(label: String) -> Array:
    return labels.get(label, [])
'''


# ==================================================================
#  3. 落盘
# ==================================================================

SUPPORTED_ENGINES = ('renpy', 'unity', 'godot', 'json', 'csv')


def expected_files(engine: str) -> list:
    """
    某个引擎**应该**产出哪些文件（相对文件名，按写出顺序）。

    为什么要有它（交付质检的教训）：上面那个大小写碰撞之所以能活到最后，
    是因为没有任何一处把"这个引擎该出什么"写成可被断言的事实 ——
    测试只查了 `story_ir.json` 存不存在，Unity 只数了 `len(files) >= 2`。
    有了这张表，测试就能逐条对账，而不是挑几个名字抽查。
    """
    engine = (engine or 'json').lower()
    if engine == 'csv':
        return [CSV_NAME, NEUTRAL_IR_NAME, CHECK_REPORT_NAME]
    if engine == 'unity':
        return [NEUTRAL_IR_NAME, UNITY_DATA_NAME, UNITY_NOTES_NAME,
                LOSS_REPORT_NAME.format(engine='unity'), CHECK_REPORT_NAME]
    if engine == 'godot':
        return [NEUTRAL_IR_NAME, GODOT_DATA_NAME, GODOT_LOADER_NAME,
                LOSS_REPORT_NAME.format(engine='godot'), CHECK_REPORT_NAME]
    return [NEUTRAL_IR_NAME, CHECK_REPORT_NAME]


def _merge_by_key(old_items, new_items, key, replace_scope=None) -> list:
    """
    按 `key` 合并：**老的定顺序，新的定内容**。

      · 同一 key → 留**新**那一份（"再导一次"就是把这一章按现在的表重写）；
      · 新 key → 追加在老条目后面（多章导同一目录不丢章节）；
      · `replace_scope(old_item) -> bool`：老条目属于**本次重导的那一章**、
        而新一份里已经没有它 → 删掉（表里删了一行，IR 里也必须消失）。

    为什么"同 key 用新值"是底线（2026-10-20 真机实测，P0）：从前这里把老条目
    放前面、同 key 的新条目**直接跳过**。而 `line_id` 是 `label.序号`
    （`beat_model.assign_identity`）—— **改一句台词不会换 id**，于是
    "改完再导一次"恰好落在"同 key 用旧值"上：`story_ir.json` 与
    `UnityDialogueData.json` 里还是旧台词，`IR_CHECK.md` 对这份**自洽的旧
    数据**算出来"0 错 0 警告"，回执 `ok: True`。这比报错更坏 ——
    用户以为改动已经导出去了。
    """
    new_rows = [it for it in (new_items or []) if isinstance(it, dict)]
    new_by_key: dict = {}
    for it in new_rows:
        k = str(it.get(key) or '')
        if k:
            # 同一份导出里 key 重复：以**最后一条**为准（同"最后一次导出为准"）。
            new_by_key[k] = it
    out, seen = [], set()
    for it in (old_items or []):
        if not isinstance(it, dict):
            continue
        k = str(it.get(key) or '')
        if k and k in seen:
            continue
        if k and k in new_by_key:
            seen.add(k)
            out.append(new_by_key[k])
            continue
        if replace_scope is not None and replace_scope(it):
            continue
        seen.add(k)
        out.append(it)
    for it in new_rows:
        k = str(it.get(key) or '')
        if k and k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _merge_ir(old: dict, new: dict) -> dict:
    """
    把一份新的中立 IR **并进**已存在的那份（多章导同一目录时不覆盖）。

    为什么必须合并（2026-10-08 修，`tools/hell_multi_engine.py` M8 实测）：
    UI 是"选一个目录 → 导出当前这一章"，而 `export_ir` 写**固定文件名**
    （`story_ir.json`）。40 章的项目里用户点四次，**后一章静默覆盖前一章**，
    每次还都报"导出成功"。合并之后同一目录里的中立 IR 累积成"整部戏"。

    **合并的单位是"章"，不是"行"**（2026-10-20 修，P0）：`new` 里出现了
    哪些 `label`，就认为"这一章被重导了" —— 那一章的行/快照以 `new` 为准：

      · 同一 `line_id` → 用 `new` 的内容（改台词不会换 id，见下方 `_merge_by_key`）；
      · `new` 里没有、但属于这一章的 `line_id` → **删掉**（表里删了行）；
      · 别的章 → 原样保留（多章累积）。

    只按 `line_id` 合并是不够的：那只能"改"，不能"删"—— 删掉的台词会永远
    留在 IR（和目标引擎）里，而编辑器里已经看不到了。按 `label` 划章靠的是
    `line_id = label.序号`（`beat_model.assign_identity`）这个既有约定。
    """
    if not isinstance(old, dict) or not old.get('lines'):
        return new
    merged = dict(new)
    new_lines = [l for l in (new.get('lines') or []) if isinstance(l, dict)]
    # 本次导出覆盖了哪些章（`label`）。空集合 = 这一份没有行 → 不删任何东西。
    new_labels = {str(l.get('label') or '') for l in new_lines
                  if str(l.get('label') or '')}
    old_label_of = {}
    for row in (old.get('lines') or []):
        if isinstance(row, dict):
            old_label_of[str(row.get('line_id') or '')] = str(
                row.get('label') or '')

    def _line_in_scope(row) -> bool:
        return str(row.get('label') or '') in new_labels

    def _stage_in_scope(row) -> bool:
        # 快照行只有 `line_id`：用**老 IR 里的** label 反查（别现拆 id）。
        label = old_label_of.get(str(row.get('line_id') or '')) or ''
        return bool(label) and label in new_labels

    merged['project'] = old.get('project') or new.get('project', '')
    merged['title'] = old.get('title') or new.get('title', '')
    merged['lines'] = _merge_by_key(old.get('lines'), new.get('lines'),
                                    'line_id', _line_in_scope)
    merged['characters'] = _merge_by_key(old.get('characters'),
                                         new.get('characters'), 'id')
    merged['stage'] = _merge_by_key(old.get('stage'), new.get('stage'),
                                    'line_id', _stage_in_scope)
    groups = set((old.get('assets') or {})) | set((new.get('assets') or {}))
    merged['assets'] = {
        g: _merge_by_key((old.get('assets') or {}).get(g),
                         (new.get('assets') or {}).get(g), 'id')
        for g in groups
    } or new.get('assets', {})
    lines = merged['lines']
    merged['stats'] = {
        'lines': len(lines),
        'labels': len({l.get('label') for l in lines if l.get('label')}),
        'characters': len(merged['characters']),
        'spoken_lines': sum(1 for l in lines if l.get('text')),
        'chars': sum(len(l.get('text') or '') for l in lines),
    }
    return merged


def export_ir(engine: str, beats, out_dir, registry=None,
              project: str = '', title: str = '') -> list:
    """
    把演出表导出成目标引擎需要的东西，返回写出的文件路径列表。

    engine: renpy / unity / godot / json / csv
    renpy 由 script_table.write_beats_to_rpy 负责（这里只导出 IR + 清单）。

    **返回的每个路径都指向一个真实存在的文件**（交付质检要求）：文件名全部
    取自本模块顶部的常量，`expected_files()` 是同一份事实的另一种陈述。

    ⚠ **同目录重复导出是"合并"不是"覆盖"**（2026-10-08）：见 `_merge_ir`。
    """
    engine = (engine or 'json').lower()
    if engine not in SUPPORTED_ENGINES:
        raise ValueError(f'不支持的引擎：{engine}（可选 '
                         f'{"/".join(SUPPORTED_ENGINES)}）')

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ir = build_neutral_ir(beats, registry=registry, project=project,
                          title=title)
    neutral_path = out_dir / NEUTRAL_IR_NAME
    # 目录里已经有一份中立 IR → 并进去（多章导同一目录不丢章节）。
    if neutral_path.is_file():
        try:
            old = json.loads(neutral_path.read_text(encoding='utf-8'))
            ir = _merge_ir(old, ir)
        except (OSError, ValueError, UnicodeDecodeError) as e:
            # 坏文件不挡新导出（下面会把它覆盖成好的）—— 但要**留痕**（M6：
            # 全仓不许 `except: pass`，health_check 会抓）。
            swallow('pipeline.engine_exporters.export_ir.merge', e)
    written = []

    written.append(_write_json(ir, neutral_path))

    if engine == 'csv':
        written.append(_write_csv(ir, out_dir / CSV_NAME))
    elif engine == 'unity':
        written.append(_write_json(to_unity(ir),
                                   out_dir / UNITY_DATA_NAME))
        written.append(_write_text(
            _unity_notes(ir), out_dir / UNITY_NOTES_NAME))
        # **损失报告**：这一份 IR 里哪些字段 Unity 侧没有（§18）。
        written.append(_write_text(
            report_markdown(ir, 'unity'),
            out_dir / LOSS_REPORT_NAME.format(engine='unity')))
    elif engine == 'godot':
        written.append(_write_json(to_godot(ir), out_dir / GODOT_DATA_NAME))
        written.append(_write_text(GODOT_LOADER_SAMPLE,
                                   out_dir / GODOT_LOADER_NAME))
        written.append(_write_text(
            report_markdown(ir, 'godot'),
            out_dir / LOSS_REPORT_NAME.format(engine='godot')))
    # **自检报告**（§20）：这份 IR 自己有没有矛盾（重复主键 / 统计不符 /
    # 快照指向不存在的行 / Unity 产物里的嵌套字典）。放在最后写：
    # Unity 的适配产物已经生成，检查能连"适配器是否违反 JsonUtility 约束"
    # 一起覆盖。每个引擎都写 —— 中立 IR 是每次导出都有的产物。
    #
    # **自检自己崩了也必须留下报告**（2026-10-20 修，P1）：从前这里直接调，
    # 坏形状的 IR 会让 `to_unity` 抛 TypeError → 整次导出崩在最后一步，
    # 目录里连 `IR_CHECK.md` 都没有。自检的价值就是"诊断坏数据"，
    # 它自己成了崩溃点等于这个功能不存在。现在写一份"自检没跑成"的报告：
    # 错误照样算一条，看得到、查得着。
    try:
        check_text = check_report(ir, engine)
    except Exception as e:                    # noqa: BLE001 - 报告不许反噬产物
        swallow('pipeline.engine_exporters.export_ir.check', e)
        check_text = check_crashed(engine, e)
    written.append(_write_text(check_text, out_dir / CHECK_REPORT_NAME))
    return written


def _atomic_write(path, data, *, newline=None) -> str:
    """
    **先写临时文件再改名**：磁盘满/被中断/权限不足时，目标文件要么是旧的
    完整版本、要么是新的完整版本，**绝不会是半截**。

    为什么值得（交付质检加的）：产物是给 Unity/Godot/翻译公司/别的脚本
    直接消费的 —— 一份**截断的 JSON 比没有文件更坏**：消费方读它会
    得到"语法错误"，而人看到的是"导出成功过、文件明明在"。
    引擎里其它落盘（`beat_render` / `write_generation`）走的是各自的
    路径，这里只管**多引擎导出**这一块。

    临时文件与目标**同目录**（`os.replace` 要求同卷才能原子改名），
    并以 `.tmp` 结尾 —— 万一进程被杀，留下的是能被一眼认出的残留。
    正常失败路径（改名失败）会**自己把临时文件清掉**：留着它，
    下一次导出目录里就多一份 `story_ir.json.tmp`，用户会以为那是产物。
    """
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    try:
        with tmp.open('w', encoding='utf-8', newline=newline) as fh:
            fh.write(data)
        # Windows 上 `os.replace` 覆盖已存在文件是允许的（Posix 语义），
        # 但如果目标正被别的程序独占打开（用户拿 Excel 开着那张表），
        # 这里会抛 PermissionError —— 那是**该报出来**的失败，不吞。
        os.replace(tmp, path)
    except OSError:
        with suppress(OSError):
            tmp.unlink()
        raise
    return str(path)


def _write_json(obj, path) -> str:
    return _atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2))


def _write_text(text: str, path) -> str:
    return _atomic_write(path, text)


def _write_csv(ir: dict, path) -> str:
    """扁平 CSV：给翻译公司 / 表格工具 / 别的脚本用。"""
    path = Path(path)
    cols = ['line_id', 'label', 'seq', 'kind', 'speaker', 'speaker_name',
            'text', 'condition', 'scene', 'actors', 'music', 'sound',
            'voice', 'line_hash']
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    # `_rows`：坏 IR 里 `lines` 是字符串 / 有条目不是对象时别崩在这里 ——
    # 这份 CSV 是给翻译公司和表格工具的，能出一行是一行。
    for l in _rows(_mapping(ir).get('lines')):
        row = []
        for c in cols:
            v = l.get(c, '')
            if isinstance(v, list):
                v = '; '.join(str(x) for x in v)
            row.append(v)
        w.writerow(row)
    # Excel 要 BOM 才认 UTF-8（老规矩，`test_csv_has_utf8_bom_for_excel` 守着）
    return _atomic_write(path, '\ufeff' + buf.getvalue(), newline='')


def _unity_notes(ir: dict) -> str:
    """Unity 接入说明（写进导出目录，别让程序去猜字段含义）。"""
    n = ir.get('stats', {})
    return f"""# Unity 接入说明

产物：
- `UnityDialogueData.json` —— 用 `JsonUtility.FromJson<DialogueData>(text)` 直接读。
  字段是标量、`string[]` 或 `TimelineEvent[]`；**没有嵌套字典**
  （JsonUtility 不支持 Dictionary）。
- `story_ir.json` —— 引擎中立 IR（更完整，含 stage 快照与资源清单），
  需要自定义解析时用它。
- `IR_LOSS-unity.md` —— **损失报告**：这一份 IR 里哪些字段 Unity 侧没有
  （DSL 源码 / 原始行 / 策划备注 / 立绘站位 / BGM 参数……）。
  接之前先看它：里面列的东西不会自己出现在 Unity 这边。

数据规模：{n.get('lines', 0)} 行演出 / {n.get('labels', 0)} 个 label /
{n.get('characters', 0)} 个角色 / 文本 {n.get('chars', 0)} 字。

## 建议的 C# 结构

```csharp
[Serializable] public class TimelineEvent {{
    public string op, target, at, clause, transition, action, params;
    public int order;               // 同一条 line 内按它排序
}}
[Serializable] public class DialogueLine {{
    public string lineId, label, kind, speakerId, speakerName, text,
                  condition, background, bgm, bgmAction, sfx, sfxAction,
                  voice, transition, lineHash,
                  structurePath, structureKind;
    public bool structureKnown;     // false = 老表不知道，别当"顶层"
    public int seq;
    public string[] poses, targets;
    public TimelineEvent[] timeline;
}}
[Serializable] public class DialogueCharacter {{
    public string id, displayName, color, imageTag, voiceTag;
}}
[Serializable] public class DialogueAsset {{
    public string id, type, path, tag, category, doc;
}}
[Serializable] public class DialogueData {{
    public string schema, project;
    public DialogueLine[] lines;
    public DialogueCharacter[] characters;
    public DialogueAsset[] assets;
}}
```

## 演出时间轴（2026-10-19 第一片）

- `timeline[]` 是**作者写的顺序**：`op` 可能是
  `scene` / `show` / `hide` / `with` / `music` / `sound` / `voice` / `at` /
  `transform`；`order` 从 0 递增，同一行内按它排序。
- `show` 的 `target` 是图像名（`eileen happy`），`at` 是位置/变换，
  `transition` 挂在操作上；`music` / `sound` 的 `action` 是
  `play` / `queue` / `stop`，`params` 是原样参数串。
- `structurePath` 是块路径（`menu:1>B`），`structureKind` 是
  `if` / `menu` / `loop` / `option`；**只有 `structureKnown=true` 才可信**。
- `condition` 是累积条件；`targets` 是 `jump` / `call` 的接线。
- 这一片**不是播放器**：条件求值、菜单 UI、立绘合成仍由 Unity 侧实现。

## 两点约定

1. **不要自己叠加舞台状态**：`background` / `poses` / `bgm` 已经是算好的
   舞台快照，"这句台词时台上是谁、在哪"直接读，不要重新实现 scene/show/hide。
2. **lineId 是主键**：存档、埋点、配音对轨、翻译都指向它。不要用数组下标。
"""
