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
import json
from pathlib import Path
from typing import Optional

from script_table import BEAT_FIELDS
from stage_snapshot import stage_snapshots

SCHEMA_VERSION = '1.0'


# ==================================================================
#  1. Beat → 引擎中立叙事 IR
# ==================================================================

def build_neutral_ir(beats, registry=None, project: str = '',
                     title: str = '') -> dict:
    """
    演出表 → 引擎中立叙事数据。

    结构：
      {
        "schema": "renpy-story-ir/1.0",
        "project": "...", "title": "...",
        "characters": [{"id","name","color","image_tag","voice_tag"}...],
        "lines": [ {line_id,label,seq,kind,speaker,speaker_name,text,
                    condition,scene,actors[],music,music_action,
                    sound,sound_action,voice,comment,raw,code,
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
            if row.get('Doc'):
                entry['doc'] = row['Doc']
            rows.append(entry)
        out[attr] = rows
    return out


# ==================================================================
#  2. 适配器
# ==================================================================

def to_unity(ir: dict) -> dict:
    """
    Unity 适配：扁平、字段名英文小驼峰、无嵌套字典。

    目标是 `JsonUtility.FromJson<DialogueData>(json)` 能直接吃下去，
    所以数组元素只能是"标量字段 + 字符串数组"。
    """
    return {
        'schema': ir.get('schema', ''),
        'project': ir.get('project', ''),
        'characters': [{
            'id': c['id'], 'displayName': c['name'], 'color': c['color'],
            'imageTag': c['image_tag'], 'voiceTag': c['voice_tag'],
        } for c in ir.get('characters', [])],
        'lines': [{
            'lineId': l['line_id'],
            'label': l['label'],
            'seq': l['seq'],
            'kind': l['kind'],
            'speakerId': l['speaker'],
            'speakerName': l['speaker_name'],
            'text': l['text'],
            'condition': l['condition'],
            'background': l['scene'],
            'poses': l['actors'],
            'bgm': l['music'],
            'bgmAction': l['music_action'],
            'sfx': l['sound'],
            'sfxAction': l['sound_action'],
            'voice': l['voice'],
            'lineHash': l['line_hash'],
        } for l in ir.get('lines', [])],
        'assets': _unity_assets(ir.get('assets', {})),
    }


def _unity_assets(assets: dict) -> list:
    """Unity 侧更习惯一个扁平的资源数组（配合 Addressables key）。"""
    rows = []
    for group, items in (assets or {}).items():
        for it in items:
            rows.append({
                'id': it.get('id', ''),
                'type': it.get('type', group.rstrip('s')),
                'path': it.get('path', ''),
                'tag': it.get('tag', ''),
                'category': it.get('category', ''),
                'doc': it.get('doc', ''),
            })
    return rows


def to_godot(ir: dict) -> dict:
    """Godot 适配：保留嵌套（GDScript 的 JSON.parse 能吃），key 用 snake_case。"""
    return {
        'schema': ir.get('schema', ''),
        'project': ir.get('project', ''),
        'characters': [{
            'id': c['id'], 'display_name': c['name'], 'color': c['color'],
            'image_tag': c['image_tag'], 'voice_tag': c['voice_tag'],
        } for c in ir.get('characters', [])],
        'lines': [{
            'line_id': l['line_id'],
            'label': l['label'],
            'seq': l['seq'],
            'speaker_id': l['speaker'],
            'speaker_name': l['speaker_name'],
            'text': l['text'],
            'condition': l['condition'],
            'stage': {
                'background': l['scene'],
                'poses': l['actors'],
                'bgm': l['music'],
                'bgm_action': l['music_action'],
                'sfx': l['sound'],
                'voice': l['voice'],
            },
            'line_hash': l['line_hash'],
        } for l in ir.get('lines', [])],
        'assets': ir.get('assets', {}),
    }


GODOT_LOADER_SAMPLE = '''# dialogue_importer.gd —— 读 STORY_IR.json（由编辑器导出）
# 用法：把本文件与 STORY_IR.json 放进项目，附到一个 Node 上即可。
extends Node

const IR_PATH := "res://STORY_IR.json"

var lines_by_id: Dictionary = {}
var labels: Dictionary = {}
var characters: Dictionary = {}

func _ready() -> void:
    var f := FileAccess.open(IR_PATH, FileAccess.READ)
    assert(f != null, "找不到 STORY_IR.json")
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


def export_ir(engine: str, beats, out_dir, registry=None,
              project: str = '', title: str = '') -> list:
    """
    把演出表导出成目标引擎需要的东西，返回写出的文件路径列表。

    engine: renpy / unity / godot / json / csv
    renpy 由 script_table.write_beats_to_rpy 负责（这里只导出 IR + 清单）。
    """
    engine = (engine or 'json').lower()
    if engine not in SUPPORTED_ENGINES:
        raise ValueError(f'不支持的引擎：{engine}（可选 '
                         f'{"/".join(SUPPORTED_ENGINES)}）')

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ir = build_neutral_ir(beats, registry=registry, project=project,
                          title=title)
    written = []

    if engine == 'csv':
        written.append(_write_csv(ir, out_dir / 'story_lines.csv'))
        written.append(_write_json(ir, out_dir / 'story_ir.json'))
        return written

    written.append(_write_json(ir, out_dir / 'story_ir.json'))

    if engine == 'unity':
        written.append(_write_json(to_unity(ir),
                                   out_dir / 'UnityDialogueData.json'))
        written.append(_write_text(
            _unity_notes(ir), out_dir / 'UNITY_IMPORT.md'))
    elif engine == 'godot':
        written.append(_write_json(to_godot(ir),
                                   out_dir / 'STORY_IR.json'))
        written.append(_write_text(GODOT_LOADER_SAMPLE,
                                   out_dir / 'dialogue_importer.gd'))
    return written


def _write_json(obj, path) -> str:
    path = Path(path)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                    encoding='utf-8')
    return str(path)


def _write_text(text: str, path) -> str:
    path = Path(path)
    path.write_text(text, encoding='utf-8')
    return str(path)


def _write_csv(ir: dict, path) -> str:
    """扁平 CSV：给翻译公司 / 表格工具 / 别的脚本用。"""
    path = Path(path)
    cols = ['line_id', 'label', 'seq', 'kind', 'speaker', 'speaker_name',
            'text', 'condition', 'scene', 'actors', 'music', 'sound',
            'voice', 'line_hash']
    with path.open('w', encoding='utf-8-sig', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for l in ir.get('lines', []):
            row = []
            for c in cols:
                v = l.get(c, '')
                if isinstance(v, list):
                    v = '; '.join(str(x) for x in v)
                row.append(v)
            w.writerow(row)
    return str(path)


def _unity_notes(ir: dict) -> str:
    """Unity 接入说明（写进导出目录，别让程序去猜字段含义）。"""
    n = ir.get('stats', {})
    return f"""# Unity 接入说明

产物：
- `UnityDialogueData.json` —— 用 `JsonUtility.FromJson<DialogueData>(text)` 直接读。
  字段全部是标量或 `string[]`，没有嵌套字典（JsonUtility 不支持）。
- `story_ir.json` —— 引擎中立 IR（更完整，含 stage 快照与资源清单），
  需要自定义解析时用它。

数据规模：{n.get('lines', 0)} 行演出 / {n.get('labels', 0)} 个 label /
{n.get('characters', 0)} 个角色 / 文本 {n.get('chars', 0)} 字。

## 建议的 C# 结构

```csharp
[Serializable] public class DialogueLine {{
    public string lineId, label, kind, speakerId, speakerName, text,
                  condition, background, bgm, bgmAction, sfx, sfxAction,
                  voice, lineHash;
    public int seq;
    public string[] poses;
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

## 两点约定

1. **不要自己叠加舞台状态**：`background` / `poses` / `bgm` 已经是算好的
   舞台快照，"这句台词时台上是谁、在哪"直接读，不要重新实现 scene/show/hide。
2. **lineId 是主键**：存档、埋点、配音对轨、翻译都指向它。不要用数组下标。
"""
