# -*- coding: utf-8 -*-
"""
storygraph.ext.archive
======================
**把声明带走**：一份可移植的扩展档案（导出 / 导入）。

## 为什么要有它（2026-11-07 第七轮）

扩展平台装的是"这个项目的写法、字段、开关"，都住在
`<项目>/storygraph.ext.json` 里。想把这套东西用到**另一个项目**上，此前只有
一条路：手工打开两个文件、抄 JSON、自己决定"同名的是覆盖还是留着"。

抄错的代价不小：`id` 撞了就是"谁后存谁赢"，而两边都以为自己存进去了。

## 档案长什么样

```json
{
  "schema": "storygraph.ext.archive/1",
  "name": "闪电动画包",
  "exported": "2026-11-07T23:10:00+08:00",
  "tool": "storygraph 1.0.0",
  "extensions": [ { "id": "my_wipe", "kind": "call", ... }, ... ]
}
```

  · 顶层只多 `name` / `exported` / `tool` 三样说明，**`extensions` 就是
    `storygraph.ext.json` 里那一份**（同一个 `parse_extension` 读它）；
  · **不写绝对路径、不写项目名**：档案要能进 git、发给别人；
  · `source` / `override` **不导出**：前者是"这条从哪读来的"，换台机器就
    不对了；后者是"只带开关、形状在别处"，带走了也是一个空壳；
  · 只导出**形状真的住在文件里**的那两种来源（`project` / `plugin`）：
    内置（`renpy.transition`）与旧清单投影（`rules.json`）的形状在别处，
    抄一份过来就是两份说法。

## 导入怎么判（**不是**三方合并，只有两方）

三方合并要 `base`；导入没有"我原来那版"，只有"项目现在的"与"档案里的"：

    项目里没有这个 id       → 加
    两边形状一模一样         → 什么都不做（`same`）
    同名、形状不同           → **冲突**：默认整份不导入，只报哪几条
                               `replace` = 用档案的覆盖；`keep` = 留项目的

## 与"保存"的分工

导入只改**内存里的注册表**，落盘仍是 `ExtensionRegistry.save()`（三方合并 +
跨进程锁）。于是"导入 + 保存"拆成两步：人能先看见冲突，再决定要不要存。
"""
from __future__ import annotations

import json
from pathlib import Path

from storygraph.ext.schema import (ExtError, EXT_SCHEMA, parse_extension)

#: 档案的格式版本（与 `storygraph.ext.json` 的 `EXT_SCHEMA` **不是**一回事：
#: 那个是"扩展点清单"，这个是"可移植的包"）。
ARCHIVE_SCHEMA = 'storygraph.ext.archive/1'

#: 建议的档案后缀（文件对话框用它筛）。
ARCHIVE_SUFFIX = '.sg-ext.json'

#: 能带走的来源：形状真的住在文件里（见模块文档）。
#: `plugin:` 开头的是插件文件（`plugin:闪电动画.json`）—— 它同样是"写下来
#: 的声明"，只是文件名更长；按**前缀**判，不按等号判（第七轮判据抓到过一次：
#: 写成 `('project','plugin')` 时插件那几条被静默跳过，导出结果看着"正常"）。
PORTABLE_SOURCES = ('project', 'plugin')


def _portable(point) -> bool:
    """这一条形状是不是真的住在文件里（`project` 或 `plugin:<文件>`）。"""
    source = str(getattr(point, 'source', '') or '')
    if source == 'project':
        return True
    return source == 'plugin' or source.startswith('plugin:')


def build_archive(points, *, name: str = '', now: str = '', tool: str = '',
                  only=None) -> dict:
    """
    一组扩展点 → 档案 dict（**纯函数**，不读盘不写盘）。

    `only` 给了就只带走这些 id（`None` = 全带）。被跳过的（内置 / 旧清单投影 /
    只带开关的 `override` 记录）不是错误 —— 它们的形状在别处，带走了也只是个
    空壳，所以**跳过并且不假装**（`report` 里列出来，见 `describe_skipped`）。
    """
    keep_ids = None if only is None else {str(i) for i in only}
    out = []
    for point in points or ():
        if not _portable(point) or point.override:
            continue
        if keep_ids is not None and point.id not in keep_ids:
            continue
        out.append(point.to_dict())
    return {'schema': ARCHIVE_SCHEMA,
            'name': str(name or ''),
            'exported': str(now or ''),
            'tool': str(tool or ''),
            'extensions': out}


def describe_skipped(points) -> list:
    """档案里**没带**的那些条 → `[{'id','why'}]`（导出后给人一句交代）。"""
    out = []
    for point in points or ():
        if not _portable(point):
            out.append({'id': point.id,
                        'why': f'形状在别处（{point.source}），带走的是空壳'})
        elif point.override:
            out.append({'id': point.id,
                        'why': '只带开关（override），形状在别处'})
    return out


def parse_archive(data) -> tuple:
    """
    档案 dict → `(extensions, meta, problems)`。

    `extensions` 是**原话**（`[{id: ...}]`，不在这一层解析）；`meta` 是
    `name` / `exported` / `tool`。`problems` 非空 = 这份档案读不懂 / 有空 id /
    条目形状坏 —— 调用方应当**整份拒绝**（"要么全对、要么完全不动"）。
    """
    if not isinstance(data, dict):
        return [], {}, ['档案顶层不是对象']
    schema = str(data.get('schema') or '')
    if schema and schema != ARCHIVE_SCHEMA:
        return [], {}, [f'档案格式不认识：{schema}（本工具认 {ARCHIVE_SCHEMA}）']
    raw = data.get('extensions')
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        return [], {}, ['档案的 extensions 不是列表']
    meta = {'name': str(data.get('name') or ''),
            'exported': str(data.get('exported') or ''),
            'tool': str(data.get('tool') or '')}
    problems, seen = [], set()
    for i, item in enumerate(raw, 1):
        ident = str(item.get('id') or '').strip() if isinstance(item, dict) else ''
        if not ident:
            problems.append(f'第 {i} 条没有 id')
            continue
        if ident in seen:
            problems.append(f'第 {i} 条的 id {ident!r} 与前面重复')
            continue
        try:
            parse_extension(item, source='project')
        except ExtError as e:
            problems.append(f'第 {i} 条（{ident}）：{e}')
            continue
        seen.add(ident)
    return raw, meta, problems


def plan_import(current: dict, incoming: list) -> dict:
    """
    `{id: 现有形状}` + 档案条目 → `{'added','same','conflicts'}`（纯函数）。

    "形状"用 `parse_extension(...).to_dict()` 的规范形比（两边同一套序列化，
    否则每条都会被当成"不一样"）。
    """
    added, same, conflicts = [], [], []
    for item in incoming or ():
        ident = str(item.get('id') or '').strip()
        if not ident:
            continue
        now = current.get(ident)
        if now is None:
            added.append(ident)
            continue
        try:
            want = parse_extension(item, source='project').to_dict()
        except ExtError:
            continue                        # 已经由 `parse_archive` 拦过
        if want == now:
            same.append(ident)
        else:
            conflicts.append(ident)
    return {'added': added, 'same': same, 'conflicts': conflicts}


def import_into(registry, incoming: list, *, on_conflict: str = 'abort') -> dict:
    """
    把档案条目**装进注册表**（内存；不落盘）→ 报告。

    `on_conflict`：`abort` = 有冲突就整份不装（只报）；`replace` = 档案覆盖；
    `keep` = 留项目里那份。冲突以外的一律照计划装（"加"与"一样"）。
    """
    if on_conflict not in ('abort', 'replace', 'keep'):
        raise ValueError('on_conflict 只认 abort/replace/keep，'
                         f'收到 {on_conflict!r}')
    current = {p.id: p.to_dict() for p in registry.all()}
    plan = plan_import(current, incoming)
    if plan['conflicts'] and on_conflict == 'abort':
        return {'ok': False, 'conflict': True, **plan,
                'reason': '这些条与项目里同名、形状不同：'
                          + '、'.join(plan['conflicts'][:4])
                          + ('…' if len(plan['conflicts']) > 4 else '')}
    replaced = []
    by_id = {str(item.get('id') or '').strip(): item for item in incoming or ()}
    for ident in plan['added'] + (plan['conflicts']
                                  if on_conflict == 'replace' else []):
        point = parse_extension(by_id[ident], source='project')
        registry.upsert(point, replace=True)
        if ident in plan['conflicts']:
            replaced.append(ident)
    return {'ok': True, 'conflict': False, 'added': list(plan['added']),
            'same': list(plan['same']), 'replaced': replaced,
            'conflicts': [], 'reason': ''}


def read_file(path) -> tuple:
    """读一份档案文件 → `parse_archive` 的三元组（坏 JSON 也是一条 problem）。"""
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding='utf-8') or '{}')
    except (OSError, ValueError) as e:
        return [], {}, [f'{target.name} 读不出来：{e}']
    return parse_archive(data)


def dump_archive(archive: dict) -> str:
    """档案 → 落盘文本（与 `storygraph.ext.json` 同一个排版口径）。"""
    payload = dict(archive)
    payload.setdefault('schema', ARCHIVE_SCHEMA)
    return json.dumps(payload, ensure_ascii=False, indent=2) + '\n'


__all__ = ['ARCHIVE_SCHEMA', 'ARCHIVE_SUFFIX', 'EXT_SCHEMA', 'PORTABLE_SOURCES',
           'build_archive', 'describe_skipped', 'dump_archive', 'import_into',
           'parse_archive', 'plan_import', 'read_file']
