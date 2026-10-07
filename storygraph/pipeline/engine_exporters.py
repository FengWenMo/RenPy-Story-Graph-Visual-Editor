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
from contextlib import suppress
from pathlib import Path

from storygraph.story.stage_snapshot import stage_snapshots

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
        return [CSV_NAME, NEUTRAL_IR_NAME]
    if engine == 'unity':
        return [NEUTRAL_IR_NAME, UNITY_DATA_NAME, UNITY_NOTES_NAME]
    if engine == 'godot':
        return [NEUTRAL_IR_NAME, GODOT_DATA_NAME, GODOT_LOADER_NAME]
    return [NEUTRAL_IR_NAME]


def export_ir(engine: str, beats, out_dir, registry=None,
              project: str = '', title: str = '') -> list:
    """
    把演出表导出成目标引擎需要的东西，返回写出的文件路径列表。

    engine: renpy / unity / godot / json / csv
    renpy 由 script_table.write_beats_to_rpy 负责（这里只导出 IR + 清单）。

    **返回的每个路径都指向一个真实存在的文件**（交付质检要求）：文件名全部
    取自本模块顶部的常量，`expected_files()` 是同一份事实的另一种陈述。
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

    written.append(_write_json(ir, out_dir / NEUTRAL_IR_NAME))

    if engine == 'csv':
        written.append(_write_csv(ir, out_dir / CSV_NAME))
    elif engine == 'unity':
        written.append(_write_json(to_unity(ir),
                                   out_dir / UNITY_DATA_NAME))
        written.append(_write_text(
            _unity_notes(ir), out_dir / UNITY_NOTES_NAME))
    elif engine == 'godot':
        written.append(_write_json(to_godot(ir), out_dir / GODOT_DATA_NAME))
        written.append(_write_text(GODOT_LOADER_SAMPLE,
                                   out_dir / GODOT_LOADER_NAME))
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
    for l in ir.get('lines', []):
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
