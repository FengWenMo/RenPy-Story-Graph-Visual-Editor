"""
storygraph.assets.asset_mapping
===============================
**项目级资源映射文件**（`storygraph.mapping.json`，schema `storygraph.mapping/1`）。

定位（规划 §2.6）
-----------------
与 `storygraph.rules.json` 同一个模式：**跟项目走、可提交、可 diff**。
它让项目开发者自己维护"哪张图是哪个角色的哪个表情"；工具在**没有映射**时
按文件夹推断（`assets.asset_identity`），映射只做**覆盖 / 纠正**。

为什么是 JSON 而不是别的（用户 2026-10-30 拍板）
-----------------------------------------------
  · 可以单独发给用户 / 发剧本群（"载入映射…"吃任意路径的 JSON）；
  · 可以提交进游戏仓库、可以 review diff；
  · 坏文件不会把工具搞崩：`load_mapping` 抛 `MappingError`，
    `mapping_cached` 把它降级成 warnings 里的**一句话**。

文件格式（四个键，未知键**警告不失败**）
---------------------------------------
```json
{
  "schema": "storygraph.mapping/1",
  "name": "某项目 · 资源映射",
  "characters": {
    "ling": {"display": "凌跃捷", "aliases": ["凌", "LTJ"],
             "avatar": "game/art/faces/ling.png"}
  },
  "rules": [
    {"glob": "game/art/full/<tag>/*.png", "kind": "sprite",
     "char": "<tag>", "attr": "<stem>"},
    {"glob": "game/art/copies/<tag>/*.png", "kind": "preview",
     "char": "<tag>", "attr": "<stem>"},
    {"glob": "game/art/faces/<stem>.png", "kind": "avatar",
     "char": "<stem>"},
    {"glob": "game/images/bg/*.png", "kind": "bg", "attr": "<stem>"}
  ],
  "images": {
    "13.2": {"kind": "cg"},
    "refusing": {"kind": "preview", "char": "ling", "attr": "refusing"}
  },
  "attributes": {"expressions": {"label": "表情"}}
}
```

匹配规则
--------
  · `images`（精确覆盖）**优先于** `rules`，key 可以是资产 ID（`refusing`）
    或项目相对路径（`game/.../x.png`，Windows 反斜杠会归一）；
  · `rules` **按数组顺序**，先中先得；
  · `glob` 支持 `*`（不跨 `/`）、`**`（跨 `/`）、`?` 和**命名占位**
    `<tag>` / `<stem>` / `<char>` / `<attr>`（匹配后把文件名 stem 自动放进
    `<stem>`）；`char` / `attr` 字段里的占位用匹配到的值替换；
  · `kind` 必须是 `asset_identity.KINDS` 之一。

**禁止**在本模块 import 任何 UI 框架（契约 §2：assets 层是纯逻辑）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Mapping, Optional

from storygraph.core.safeguard import swallow
from storygraph.assets.asset_identity import (
    KINDS,
    SHOWABLE_KINDS,
    Identity,
    asset_id_of,
    build_copy_index,
    normalize_relpath,
    resolve_image,
)

#: 映射文件的结构版本。**改结构就 +1**（读取方据此判断能不能认）。
MAPPING_SCHEMA = 'storygraph.mapping/1'

#: 文件名（项目根，或 `game/` 下同名 —— 两者都接受）。
MAPPING_FILENAME = 'storygraph.mapping.json'

#: 认识的顶层键（多出来的**警告不失败**：别的工具/未来版本加的键不该拦读）。
KNOWN_TOP_KEYS = ('schema', 'name', 'characters', 'rules', 'images',
                  'attributes')

#: `images` / `rules` 条目里认识的键（多出来同样只警告）。
KNOWN_ENTRY_KEYS = ('glob', 'kind', 'char', 'tag', 'attr', 'attrs',
                    'showable', 'display')

#: 只缓存最近这么多份映射（多项目切换时不会无限长）。
_CACHE_MAX = 32
_CACHE: dict = {}


class MappingError(ValueError):
    """映射文件读不出来 / 不是合法 JSON / 顶层不是对象。"""


# ---------------------------------------------------------------------------
#  查找 / 读取
# ---------------------------------------------------------------------------

def find_mapping_path(project_root: str) -> Optional[str]:
    """
    项目里的映射文件 → **绝对路径**；没有返回 `None`。

    先找项目根，再找 `game/` 下（两种放法都见得到：有的团队喜欢把配置
    全塞进 `game/`）。返回绝对路径，方便直接显示/打开。
    """
    root = str(project_root or '').strip()
    if not root:
        return None
    base = Path(root)
    for candidate in (base / MAPPING_FILENAME,
                      base / 'game' / MAPPING_FILENAME):
        try:
            if candidate.is_file():
                return str(candidate.resolve())
        except OSError:
            continue
    return None


def load_mapping(path: str) -> tuple:
    """
    读一份映射 → `(data, warnings)`。

    · 文件不存在 / 读不出 / 不是合法 JSON / 顶层不是对象 → 抛 `MappingError`
      （**这是配置错误**，界面要明说，不能当成"没有映射"）；
    · 未知键、缺 `schema`、类型不对 → 只进 `warnings`，能读的部分照样用。
    """
    text_path = str(path or '').strip()
    if not text_path:
        raise MappingError('没有给出映射文件路径')
    p = Path(text_path)
    if not p.is_file():
        raise MappingError(f'映射文件不存在：{p}')
    try:
        text = p.read_text(encoding='utf-8-sig')
    except OSError as e:
        raise MappingError(f'映射文件读不出来：{p}（{e}）') from e
    except UnicodeDecodeError as e:
        raise MappingError(f'映射文件不是 UTF-8：{p}（{e}）') from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise MappingError(f'映射文件不是合法 JSON：{e}') from e
    if not isinstance(data, dict):
        raise MappingError('映射文件的顶层必须是一个 JSON 对象')
    return data, validate_mapping(data)


def validate_mapping(data) -> list:
    """
    映射结构检查 → **警告列表**（空 = 没问题）。纯函数、不改数据。

    为什么警告不失败：这份文件是**人写的、会随项目长大**；多一个 `notes`
    之类的键就拒绝加载，等于逼人删信息。但也不能静默 —— 认不出的键
    要报出来，否则"我写进去了怎么没生效"永远查不出。
    """
    warnings = []
    if not isinstance(data, dict):
        return ['映射顶层不是对象']
    for key in data:
        if key not in KNOWN_TOP_KEYS:
            warnings.append(f'未知顶层键 {key!r}（已忽略；认识的键：'
                            + '、'.join(KNOWN_TOP_KEYS) + '）')
    schema = data.get('schema')
    if schema is None:
        warnings.append(f'缺 schema 字段（本工具按 {MAPPING_SCHEMA} 读）')
    elif schema != MAPPING_SCHEMA:
        warnings.append(f'schema 不认识：{schema!r}（本工具只认 '
                        f'{MAPPING_SCHEMA}，仍尽量按同结构读）')
    chars = data.get('characters')
    if chars is not None and not isinstance(chars, dict):
        warnings.append('characters 不是对象（应为 {tag: {...}}）')
    elif isinstance(chars, dict):
        for tag, entry in chars.items():
            if not str(tag or '').strip():
                warnings.append('characters 里有空 tag')
            elif not isinstance(entry, dict):
                warnings.append(f'characters[{tag!r}] 不是对象')
    rules = data.get('rules')
    if rules is not None and not isinstance(rules, list):
        warnings.append('rules 不是数组')
    elif isinstance(rules, list):
        for i, rule in enumerate(rules):
            if not isinstance(rule, dict):
                warnings.append(f'rules[{i}] 不是对象')
                continue
            for key in rule:
                if key not in KNOWN_ENTRY_KEYS:
                    warnings.append(f'rules[{i}] 未知键 {key!r}（已忽略）')
            glob = str(rule.get('glob') or '').strip()
            if not glob:
                warnings.append(f'rules[{i}] 缺 glob（这条规则用不了）')
            elif _looks_outside_project(glob):
                warnings.append(
                    f'rules[{i}].glob 像是绝对路径：{glob!r}'
                    '（映射里的路径应写**项目相对**，否则换台机器就失效）')
            kind = str(rule.get('kind') or '').strip()
            if kind and kind not in KINDS:
                warnings.append(f'rules[{i}] kind 不认识：{kind!r}')
    images = data.get('images')
    if images is not None and not isinstance(images, dict):
        warnings.append('images 不是对象（应为 {asset_id 或相对路径: {...}}）')
    elif isinstance(images, dict):
        for key, entry in images.items():
            if not str(key or '').strip():
                warnings.append('images 里有空 key')
                continue
            if not isinstance(entry, dict):
                warnings.append(f'images[{key!r}] 不是对象（简短写法不支持，'
                                '请写 {"kind": ...}）')
                continue
            for field in entry:
                if field not in KNOWN_ENTRY_KEYS:
                    warnings.append(f'images[{key!r}] 未知键 {field!r}（已忽略）')
            kind = str(entry.get('kind') or '').strip()
            if kind and kind not in KINDS:
                warnings.append(f'images[{key!r}] kind 不认识：{kind!r}')
    attributes = data.get('attributes')
    if attributes is not None and not isinstance(attributes, dict):
        warnings.append('attributes 不是对象（应为 {维度: {"label": ...}}）')
    return warnings


def _looks_outside_project(text: str) -> bool:
    """这个写法是不是绝对路径（映射里应写项目相对路径）。"""
    value = str(text or '').strip().replace('\\', '/')
    return (value.startswith('/') or bool(re.match(r'^[A-Za-z]:', value))
            or ':/' in value)


def mapping_cached(project_root: str) -> tuple:
    """
    按 `mtime_ns + size` 缓存的映射读取 → `(data, warnings, path)`。

    · 项目没有映射文件 → `({}, [], '')`（**不是错误**：绝大多数项目不需要它，
      靠文件夹推断就能用）；
    · 文件坏了 → `({}, [一句人话], path)`（**不抛** —— 界面/选择器不该因为
      一份配置坏掉就整个打不开；`load_mapping` 才是"要报错"的入口）。
    """
    root = str(project_root or '').strip()
    if not root:
        return {}, [], ''
    path = find_mapping_path(root)
    if not path:
        return {}, [], ''
    try:
        st = os.stat(path)
    except OSError as e:
        return {}, [f'映射文件 stat 失败：{e}'], path
    stamp = (int(st.st_mtime_ns), int(st.st_size))
    cached = _CACHE.get(path)
    if cached and cached[0] == stamp:
        return cached[1], list(cached[2]), path
    try:
        data, warnings = load_mapping(path)
    except MappingError as e:
        data, warnings = {}, [str(e)]
    _CACHE[path] = (stamp, data, list(warnings))
    if len(_CACHE) > _CACHE_MAX:
        for key in list(_CACHE)[:-_CACHE_MAX]:
            _CACHE.pop(key, None)
    return data, list(warnings), path


# ---------------------------------------------------------------------------
#  glob → 正则（映射规则唯一实现）
# ---------------------------------------------------------------------------

#: 命名占位：`<tag>` / `<stem>` / `<char>` / `<attr>` ……
_PLACEHOLDER_RE = re.compile(r'^[A-Za-z_][\w-]*$')


@lru_cache(maxsize=256)
def _rule_regex(glob: str):
    """
    glob → 编译好的正则（缓存：1720 行 × 十几条规则不能每行重编一次）。

    `*` 不跨目录、`**` 跨目录、`?` 单字符；`<name>` 变成 **命名捕获组**
    （`(?P<name>[^/]+)`），`char` / `attr` 模板随后用捕获值替换。
    """
    text = str(glob or '').replace('\\', '/').strip()
    out = []
    used = set()
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '<':
            j = text.find('>', i + 1)
            name = text[i + 1:j].strip() if j > i + 1 else ''
            if name and _PLACEHOLDER_RE.match(name):
                if name in used:            # 同名占位出现两次 → 非捕获回落
                    out.append('(?:[^/]+)')
                else:
                    used.add(name)
                    out.append(f'(?P<{name}>[^/]+)')
                i = j + 1
                continue
        if text.startswith('**', i):
            out.append('.*')
            i += 2
            continue
        if ch == '*':
            out.append('[^/]*')
            i += 1
            continue
        if ch == '?':
            out.append('[^/]')
            i += 1
            continue
        if ch == '[':
            j = text.find(']', i + 1)
            if j > i + 1:
                inner = text[i + 1:j]
                # glob 的否定字符类是 `[!abc]`，正则要写成 `[^abc]`。
                if inner.startswith('!'):
                    inner = '^' + inner[1:]
                out.append('[' + inner + ']')
                i = j + 1
                continue
        out.append(re.escape(ch))
        i += 1
    try:
        return re.compile('^' + ''.join(out) + '$', re.IGNORECASE)
    except re.error:
        # 用户手写的坏 glob：编译不过就当它匹配不了（load 时已警告 kind/glob）
        return re.compile(r'(?!)')


def _rule_match(glob: str, relpath: str) -> Optional[dict]:
    """一条规则 glob 对项目相对路径 → 捕获组（不中 = None；自动填 stem/name）。"""
    rel = normalize_relpath(relpath)
    if not rel:
        return None
    m = _rule_regex(glob).match(rel)
    if not m:
        return None
    groups = {k: v for k, v in m.groupdict().items() if v is not None}
    path = PurePosixPath(rel)
    for key, value in (('stem', path.stem), ('name', path.name),
                       ('filename', path.name), ('file', path.name),
                       ('ext', path.suffix.lstrip('.'))):
        groups.setdefault(key, value)
    return groups


def _subst(template, groups: Mapping) -> str:
    """
    `char` / `attr` 模板里的 `<name>` → 捕获值。

    认不出的占位**替换成空串**（宁可空着让人看见，也不把 `<unknown>` 原样
    写进 tag / 属性 —— 那会造出一个引擎里不存在的名字）。
    """
    if template is None:
        return ''
    if isinstance(template, (list, tuple)):
        return ' '.join(_subst(t, groups) for t in template)
    text = str(template)

    def repl(match):
        return str((groups or {}).get(match.group(1), '') or '')

    return re.sub(r'<([A-Za-z_][\w-]*)>', repl, text).strip()


def _split_attrs(raw) -> tuple:
    """属性字段 → 元组（字符串按空白/逗号拆；列表原样展开；保序去重）。"""
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        words = [str(x) for x in raw]
    else:
        words = re.split(r'[\s,，、;；|]+', str(raw))
    out = []
    for word in words:
        word = str(word or '').strip()
        if word and word not in out:
            out.append(word)
    return tuple(out)


def _identity_from_entry(entry, *, relpath: str, groups: Mapping,
                         source: str) -> Optional[Identity]:
    """映射条目（`images` 或 `rules` 的一条）→ `Identity`。"""
    if not isinstance(entry, Mapping):
        return None
    kind = str(entry.get('kind') or '').strip().lower()
    if kind not in KINDS:
        kind = 'other'
    char = _subst(entry.get('char') if entry.get('char') is not None
                  else entry.get('tag'), groups or {})
    attrs = _split_attrs(_subst(entry.get('attr') if entry.get('attr') is not None
                                else entry.get('attrs'), groups or {}))
    tag = char
    if not tag and kind in ('bg', 'cg', 'ui'):
        # 只写了 kind 的目录规则（如 `game/images/bg/*.png` → bg）：
        # tag 就是约定俗成的 `bg`/`cg`/`ui`（映射作者已经声明了用途）。
        tag = kind
    if not attrs and kind in ('bg', 'cg', 'ui'):
        stem = str((groups or {}).get('stem') or '').strip()
        attrs = (stem,) if stem else ()
    showable = entry.get('showable')
    if showable is None:
        showable = kind in SHOWABLE_KINDS and bool(tag)
    return Identity(kind=kind, tag=tag, attrs=attrs,
                    display=str(entry.get('display') or '').strip(),
                    source=source, showable=bool(showable))


def resolve_with_mapping(mapping, *, relpath: str = '',
                         asset_id: str = '') -> Optional[Identity]:
    """
    映射 → `Identity`（**没有命中**返回 `None`，交给字面量推断）。

    顺序（契约 §1.2）：`images` 精确覆盖（asset ID / 相对路径）优先，
    然后 `rules` **按数组顺序**先中先得。
    """
    if not isinstance(mapping, Mapping) or not mapping:
        return None
    rel = normalize_relpath(relpath)
    aid = str(asset_id or '').strip()

    hit = _match_exact_image(mapping.get('images'), aid, rel)
    if hit is not None:
        return hit

    rules = mapping.get('rules')
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, Mapping):
                continue
            glob = str(rule.get('glob') or '').strip()
            if not glob:
                continue
            groups = _rule_match(glob, rel)
            if groups is None:
                continue
            return _identity_from_entry(rule, relpath=rel, groups=groups,
                                        source='mapping-rule')
    return None


def _match_exact_image(images, asset_id: str, rel: str) -> Optional[Identity]:
    """`images` 精确覆盖：先按资产 ID，再按项目相对路径（大小写不敏感）。"""
    if not isinstance(images, Mapping) or not images:
        return None
    entry = None
    if asset_id:
        entry = images.get(asset_id)
        if entry is None:
            for key, value in images.items():
                if str(key).strip().lower() == asset_id.lower():
                    entry = value
                    break
    if entry is None and rel:
        entry = images.get(rel)
        if entry is None:
            low = rel.lower()
            for key, value in images.items():
                if str(key).strip().replace('\\', '/').lower() == low:
                    entry = value
                    break
    if entry is None:
        return None
    return _identity_from_entry(entry, relpath=rel, groups={'stem': PurePosixPath(rel).stem},
                                source='mapping')


# ---------------------------------------------------------------------------
#  导出初稿（从实际目录自动汇总规则）
# ---------------------------------------------------------------------------

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
        groups = _rule_match(str(rule.get('glob') or ''), relpath)
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
        mapped = _identity_from_entry(rule, relpath=rel, groups=groups or {},
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


# ---------------------------------------------------------------------------
#  写回（原子 + 旧文件 .bak）
# ---------------------------------------------------------------------------

def save_mapping(path: str, data) -> None:
    """
    把映射写回磁盘：**原子替换 + 旧文件 `.bak`**。

    · 旧文件存在就先逐字节备份成 `<文件名>.bak`；备份失败 → **不写**
      （宁可没改，也不让人失去上一版）；
    · 新内容先写临时文件、`os.replace` 一步换上去 —— 中途断电不会留下
      半份 JSON（这份文件会进 git，半份 = 一次坏提交）；
    · 失败抛 `MappingError`，由调用方决定怎么提示。
    """
    if not isinstance(data, Mapping):
        raise MappingError('映射数据必须是 JSON 对象')
    target = Path(str(path or '').strip())
    if not str(path or '').strip() or not target.name:
        raise MappingError('没有给出映射文件路径')
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise MappingError(f'建不出映射文件目录：{e}') from e
    text = json.dumps(dict(data), ensure_ascii=False, indent=2) + '\n'
    if target.exists():
        backup = target.with_name(target.name + '.bak')
        try:
            shutil.copy2(target, backup)
        except OSError as e:
            raise MappingError(f'旧映射备份失败（未写，宁可没改）：{e}') from e
    tmp = target.with_name(target.name + '.tmp')
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
        os.replace(tmp, target)
    except OSError as e:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:                         # noqa: BLE001 - 清理失败不掩盖真错误
            swallow('assets.asset_mapping.save_mapping.cleanup', e)
        raise MappingError(f'写映射文件失败：{e}') from e


__all__ = [
    'MAPPING_SCHEMA', 'MAPPING_FILENAME', 'KNOWN_TOP_KEYS', 'KNOWN_ENTRY_KEYS',
    'MappingError', 'find_mapping_path', 'load_mapping', 'validate_mapping',
    'mapping_cached', 'resolve_with_mapping', 'draft_mapping', 'save_mapping',
]
