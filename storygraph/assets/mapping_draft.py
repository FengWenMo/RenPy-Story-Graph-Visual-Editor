"""
storygraph.assets.mapping_draft
===============================
**映射初稿生成**：从实际目录 + 资源表自动汇总一份 `storygraph.mapping.json`。

为什么单独一个模块（2026-11-01，技术债 §83）
-------------------------------------------
初稿要**先用"没有映射"的身份决议**（`asset_identity.resolve_image`）逐行走一遍，
才有资格归纳规则 —— 也就是说它是 `asset_identity` 的**下游**；而
`asset_mapping`（映射的读写 / 校验 / 规则命中）是它的**上游**。这三样原来挤在
`asset_mapping` 里，就成了 `asset_mapping ⇄ asset_identity` 两模块环；把初稿这
一层搬到两者**之上**，两个下层谁也不指谁。

分工（一句话）：本模块**只写初稿**。
  · 一条图是什么        → `assets.asset_identity.resolve_image`；
  · 规则 / 精确覆盖怎么命中 → `assets.asset_mapping`；
  · 文件长什么样、怎么校验 → `assets.mapping_schema`。

**禁止**在本模块 import 任何 UI 框架（契约 §2：assets 层是纯逻辑）。
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Mapping, Optional

from storygraph.assets.asset_identity import resolve_image
from storygraph.assets.asset_mapping import identity_from_entry, rule_match
from storygraph.assets.identity_core import (Identity, asset_id_of,
                                             build_copy_index,
                                             normalize_relpath)
from storygraph.assets.mapping_schema import MAPPING_SCHEMA

#: 初稿里规则 kind 的先后（越靠前越"特殊"，规则按顺序先中先得）。
_KIND_RULE_RANK = {'avatar': 0, 'preview': 1, 'sprite': 2, 'bg': 3,
                   'cg': 4, 'ui': 5, 'other': 6}


def _normalize_chars(known_chars) -> dict:
    """已知角色两种形态 → `{tag: {'display','aliases','avatar'}}`。"""
    out: dict = {}
    if not isinstance(known_chars, Mapping):
        return out
    for tag, value in known_chars.items():
        name = str(tag or '').strip()
        if not name:
            continue
        if isinstance(value, Mapping):
            entry = dict(value)
        else:
            entry = {'display': str(value or '')}
        aliases = entry.get('aliases')
        if isinstance(aliases, str):
            aliases = [aliases]
        entry['aliases'] = [str(a) for a in (aliases or ()) if str(a).strip()]
        out[name] = entry
    return out


def _dir_pattern_with_tag(rel: str, tag: str):
    """
    项目相对路径 + 角色 tag → `(目录模板, 是否把 tag 换成占位符)`。

    `game/images/characters/ling/happy.png` + `ling`
      → `('game/images/characters/<tag>', True)`；
    目录里找不到这个 tag 段时退回"整条目录"（`False`）。
    """
    path = PurePosixPath(rel)
    parts = list(path.parts)
    if not parts:
        return '', False
    dirs = list(parts[:-1])
    low_tag = str(tag or '').lower()
    for i in range(len(dirs) - 1, -1, -1):
        if str(dirs[i]).lower() == low_tag:
            dirs[i] = '<tag>'
            return '/'.join(dirs), True
    return '/'.join(dirs), False


def _rule_for_identity(ident: Identity, rel: str) -> Optional[dict]:
    """
    一个已解析的 `Identity` + 它的路径 → 一条映射规则（认不出 = None）。

    规则只从**实际路径结构**里来（角色 tag 目录 + 文件名 stem）——
    不为任何项目写死目录名。角色规则用**字面量 tag 目录**
    （`art/full/eileen/*.png`）而不是 `<tag>` 占位：占位会把目录下所有
    子目录都吞成角色，审起来也看不出到底覆盖了谁。

    只在**目录里真的有这个角色 tag 段**时才出目录规则；
    平铺布局（`images/eileen_happy.png`，目录里认不出 tag 段）规则会
    张冠李戴（同目录别的角色也被吞进去），所以退回 `images` 精确覆盖。
    """
    if ident is None or not rel:
        return None
    path = PurePosixPath(rel)
    ext = path.suffix
    tag = str(ident.tag or '').strip()
    kind = ident.kind
    if kind in ('sprite', 'preview'):
        if not tag:
            return None
        dirs, replaced = _dir_pattern_with_tag(rel, tag)
        if not dirs or not replaced:
            return None
        # 用**字面量 tag 目录**而不是 `<tag>` 占位：占位规则会把这个目录下的
        # 所有角色名都吞进来（真机 `images/characters/qing/` 的 Tag 不在
        # characters 表里，<tag> 规则会把它硬认成 sprite）。精确规则更好审。
        literal_dirs = dirs.replace('<tag>', tag)
        return {'glob': f'{literal_dirs}/*{ext}', 'kind': kind, 'char': tag,
                'attr': '<stem>'}
    if kind == 'avatar':
        dirs = '/'.join(path.parts[:-1])
        if not dirs:
            return None
        return {'glob': f'{dirs}/<stem>{ext}', 'kind': 'avatar',
                'char': '<stem>'}
    if kind in ('bg', 'cg', 'ui'):
        # 非角色图层：只声明"这个目录是背景/CG/界面"，tag 用约定名。
        dirs = '/'.join(path.parts[:-1])
        if not dirs:
            return None
        return {'glob': f'{dirs}/*{ext}', 'kind': kind, 'char': kind,
                'attr': '<stem>'}
    return None


def _first_matching_rule(rules, relpath: str):
    """规则数组按**顺序**匹配：`(规则, 捕获组)`；都不中 → `(None, None)`。"""
    for rule in rules or ():
        if not isinstance(rule, Mapping):
            continue
        groups = rule_match(str(rule.get('glob') or ''), relpath)
        if groups is not None:
            return rule, groups
    return None, None


def _rules_consistent(rules, candidate, resolved) -> bool:
    """
    验收一条候选规则：**所有**被它（在整组规则里）命中的行，结论必须与
    无映射推断一致（kind / tag / showable）。

    为什么必须有这一步：目录级 glob 很容易"吞掉"同目录的别的资源
    （`pomodorro/images/*.png` 里既有界面图也有小游戏角色图）。规则一旦
    认错人，就是**静默写错 show** —— 宁可不出这条规则、退回逐条精确覆盖。
    """
    for _row, rel, ident in resolved:
        rule, groups = _first_matching_rule(rules, rel)
        if rule is not candidate:
            continue
        mapped = identity_from_entry(rule, relpath=rel, groups=groups or {},
                                      source='mapping-rule')
        if mapped is None:
            return False
        if mapped.kind != ident.kind:
            return False
        if ident.kind in ('sprite', 'preview', 'avatar'):
            if (ident.tag and mapped.tag
                    and mapped.tag.lower() != ident.tag.lower()):
                return False
            if mapped.showable != ident.showable:
                return False
        else:
            # bg / cg / ui：规则里的 tag 是约定名，必须与推断一致，
            # 而且"能不能 show"也要一致（弱路径线索的行不许被规则升成可 show）。
            if (str(mapped.tag or '').lower()
                    != str(ident.tag or '').lower()):
                return False
            if mapped.showable != ident.showable:
                return False
    return True


def draft_mapping(project_root: str, rows, known_chars, *,
                  project_name: str = '') -> dict:
    """
    从**实际目录 + 资源表**自动汇总一份映射初稿（JSON 可直接 `save_mapping`）。

    · 规则从目录结构来：把路径里的角色 tag 段换成 `<tag>`、文件名换成 `<stem>`
      （见 `_rule_for_identity`）—— **不写死任何项目目录名**；
    · 每条候选规则先**验收**（`_rules_consistent`）：规则命中的每一行，
      结论必须与无映射推断一致；一个目录混用多种资源时宁可不出规则；
    · 规则表达不了、但**有依据**的行（Tag 列 / 副本检测 / avatar 目录）
      退回 `images` 精确覆盖；认不出的行**不硬编**；
    · `characters` 从 `known_chars` 来（显示名 / 别名 / 头像路径），
      头像路径是**项目相对**路径（文件里不许出现本机绝对路径）；
    · 导出后能被 `load_mapping` **原样读回**（round-trip 判据在同一批测试里）。

    参数：`rows` 接受 `registry.images`（`{id: row}`，推荐）或 `[row, ...]`；
    `known_chars` 接受 `{tag: 显示名}` 或 `{tag: {...}}`（`known_characters`）。
    """
    root = str(project_root or '').strip()
    chars = _normalize_chars(known_chars)
    row_list = []
    if isinstance(rows, Mapping):
        raw_rows = list(rows.values())
    else:
        raw_rows = list(rows or ())
    for row in raw_rows:
        if not isinstance(row, Mapping):
            continue
        row_list.append({str(k): v for k, v in row.items()})
    for row in row_list:
        row['_rel'] = normalize_relpath(str(row.get('Path') or ''), root)

    copy_index = build_copy_index(row_list)
    resolved = []
    for row in row_list:
        rel = row.get('_rel') or ''
        if not rel:
            continue
        ident = resolve_image(row, relpath=rel, known_tags=set(chars),
                              known_chars=chars, mapping=None, rows=row_list,
                              copy_index=copy_index)
        resolved.append((row, rel, ident))

    # 1) 候选规则：按"实际目录结构"汇总（同一 glob + 同一结论合并成一条）
    buckets: dict = {}
    for row, rel, ident in resolved:
        rule = _rule_for_identity(ident, rel)
        if rule is None:
            continue
        key = (rule['glob'], rule['kind'], rule.get('char', ''),
               rule.get('attr', ''))
        buckets.setdefault(key, dict(rule))

    candidates = list(buckets.values())
    # avatar（一个文件一个人）> 预览 > 立绘 > bg/cg/ui；同一 kind 里越"特殊"
    # 的 glob 越靠前（先中先得，避免 <tag> 把 avatar 目录吞掉）。
    candidates.sort(key=lambda r: (_KIND_RULE_RANK.get(r['kind'], 9),
                                   _glob_specificity(r['glob']),
                                   r['glob'], r['kind']))

    # 2) 逐条验收；不通过的直接不出规则（被它挡住的行走精确覆盖/不覆盖）。
    rules: list = []
    accepted_globs: set = set()
    for candidate in candidates:
        if candidate['glob'] in accepted_globs:
            continue
        trial = rules + [candidate]
        if not _rules_consistent(trial, candidate, resolved):
            continue
        rules.append(candidate)
        accepted_globs.add(candidate['glob'])

    # 3) 规则覆盖不到、但有依据的行 → `images` 精确覆盖
    images: dict = {}
    for row, rel, ident in resolved:
        rule, _groups = _first_matching_rule(rules, rel)
        if rule is not None:
            continue
        _draft_exact_entry(images, row, ident)

    characters = {}
    for tag in sorted(chars):
        entry = chars[tag]
        display = str(entry.get('display') or '').strip() or tag
        item: dict = {'display': display}
        aliases = [a for a in (entry.get('aliases') or ()) if str(a).strip()]
        if aliases:
            item['aliases'] = aliases
        avatar = _draft_avatar_path(resolved, tag)
        if avatar:
            item['avatar'] = avatar
        characters[tag] = item

    base_name = str(project_name or '').strip()
    return {
        'schema': MAPPING_SCHEMA,
        'name': f'{base_name} · 资源映射' if base_name else '资源映射',
        'characters': characters,
        'rules': rules,
        'images': images,
        # 代号两段（服装字母 / 表情编号）**不从目录结构推**：初稿里先空着，
        # 由「逐条编辑」页的「按项目自动填」去数剧本注释（`sprite_codes`）。
        'outfit_codes': [],
        'expression_codes': [],
        'vocabulary': [],
        'attributes': {},
    }


def _glob_specificity(glob: str) -> tuple:
    """规则排序用：占位/通配越少越"特殊"，应排在前面先中。"""
    text = str(glob or '')
    wild = text.count('*') + text.count('?') + len(re.findall(r'<[^>]+>', text))
    return (wild, text)


def _draft_exact_entry(images: dict, row, ident: Identity) -> None:
    """
    规则表达不了的行 → `images` 精确覆盖（**只写有依据的字段**）。

    · 角色行：`{kind, char, attr}`（Tag 列 / 副本检测 / avatar 目录给的）；
    · 非角色图层：只有 Tag 列**明确写着** `bg/cg/ui` 才写（弱路径线索
      说不出 `show` 名字，不硬编；这类行留在"未覆盖"里，等人补）。
    """
    aid = asset_id_of(row)
    if not aid or ident is None:
        return
    if ident.kind in ('sprite', 'preview') and ident.tag:
        item = {'kind': ident.kind, 'char': ident.tag}
        if ident.attrs:
            item['attr'] = ' '.join(ident.attrs)
        images[aid] = item
    elif ident.kind == 'avatar' and ident.tag:
        images[aid] = {'kind': 'avatar', 'char': ident.tag}
    elif (ident.kind in ('bg', 'cg', 'ui') and ident.tag
          and ident.source == 'tag-column'):
        images[aid] = {'kind': ident.kind}


def _draft_avatar_path(resolved, tag: str) -> str:
    """初稿 characters 段用的头像路径（**项目相对**；没有 = ''）。"""
    for row, rel, ident in resolved:
        if ident.kind == 'avatar' and ident.tag.lower() == str(tag).lower():
            return rel
    return ''


__all__ = ['draft_mapping']
