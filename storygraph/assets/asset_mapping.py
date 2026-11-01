"""
storygraph.assets.asset_mapping
===============================
**项目级资源映射文件**（`storygraph.mapping.json` / `.xlsx`，
schema `storygraph.mapping/1`）。

定位（规划 §2.6）
-----------------
与 `storygraph.rules.json` 同一个模式：**跟项目走、可提交、可 diff**。
它让项目开发者自己维护"哪张图是哪个角色的哪个表情"；工具在**没有映射**时
按文件夹推断（`assets.asset_identity`），映射只做**覆盖 / 纠正**。

两条定义途径（用户 2026-10-30）
------------------------------
  · **JSON**（`storygraph.mapping.json`）：可 diff、可 review、可单独发人；
  · **Excel**（`storygraph.mapping.xlsx`）：1720 行的大表，人在 Excel 里维护
    才是现实的做法 —— 表结构 / 单元格约定 / 为什么"只读不回写"全在
    `assets/mapping_excel.py` 的模块 docstring 里。

**同一目录里 Excel 优先于 JSON**，并且这份优先级**每处都说得出来**：
`mapping_sources()` 返回按优先级排好的全部候选，`mapping_cached()` 在两份都在时
给一条警告，界面状态行显示当前生效的那份路径。

为什么是 JSON 而不是别的（用户 2026-10-30 拍板）
-----------------------------------------------
  · 可以单独发给用户 / 发剧本群（"载入映射…"吃任意路径的 JSON / xlsx）；
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
from storygraph.assets.identity_core import (
    KINDS,
    SHOWABLE_KINDS,
    Identity,
    asset_id_of,
    build_copy_index,
    normalize_relpath,
)
from storygraph.assets.mapping_schema import (
    KNOWN_ENTRY_KEYS,
    KNOWN_TOP_KEYS,
    MAPPING_SCHEMA,
    MappingError,
    validate_mapping,
    xls_unsupported,
)
#: ⚠ 这一条是 `assets` 层**剩下唯一**的 `asset_mapping → asset_identity` 边
#: （`draft_mapping` 逐行算身份时用）。反方向的
#: `asset_identity → asset_mapping`（映射层 / `project_context` 取缓存）还没拆，
#: 所以那两个模块**仍然是 2 个模块的环**（见 `docs/开发日志.md` §82）。
from storygraph.assets.asset_identity import resolve_image


#: JSON 映射的文件名（项目根，或 `game/` 下同名 —— 两者都接受）。
MAPPING_FILENAME = 'storygraph.mapping.json'

#: Excel 映射的文件名（同位置**优先于** JSON，见模块 docstring）。
MAPPING_XLSX_FILENAME = 'storygraph.mapping.xlsx'

#: Excel 后缀（`.xlsm` 读法一样；老式 `.xls` 读不了，由 `mapping_excel` 说人话）。
WORKBOOK_SUFFIXES = ('.xlsx', '.xlsm')

#: 同一个目录里按这个顺序找（Excel 优先）。
MAPPING_NAMES = (MAPPING_XLSX_FILENAME, MAPPING_FILENAME)


#: 只缓存最近这么多份映射（多项目切换时不会无限长）。
_CACHE_MAX = 32
_CACHE: dict = {}


# ---------------------------------------------------------------------------
#  查找 / 读取
# ---------------------------------------------------------------------------

def is_workbook(path) -> bool:
    """这个路径是 Excel 映射吗（按后缀；`.xls` 不算 —— 它读不了）。"""
    return str(path or '').strip().lower().endswith(WORKBOOK_SUFFIXES)


def file_format(path) -> str:
    """映射文件的格式：`'excel'` / `'json'` / `''`（认不出）。界面拿它说人话。"""
    suffix = Path(str(path or '').strip()).suffix.lower()
    if suffix in WORKBOOK_SUFFIXES:
        return 'excel'
    if suffix == '.json':
        return 'json'
    return ''


def mapping_sources(project_root: str) -> list:
    """
    项目里**所有**候选映射文件（绝对路径，按生效优先级排好）。

    顺序是「项目根」整组在前、「`game/`」整组在后，每组里 `xlsx → json`：
    所以"项目根那份 JSON"胜过"`game/` 里那份 Excel"—— 目录的远近先于格式，
    这符合人对"项目根是我的主目录"的直觉。
    """
    root = str(project_root or '').strip()
    if not root:
        return []
    base = Path(root)
    found = []
    for folder in (base, base / 'game'):
        for name in MAPPING_NAMES:
            candidate = folder / name
            try:
                if candidate.is_file():
                    found.append(str(candidate.resolve()))
            except OSError:
                continue
    return found


def find_mapping_path(project_root: str) -> Optional[str]:
    """
    项目里**当前生效**的映射文件 → 绝对路径；没有返回 `None`。

    先找项目根，再找 `game/` 下；同一目录里 `xlsx` 优先于 `json`（见模块
    docstring）。返回绝对路径，方便直接显示 / 打开。
    """
    found = mapping_sources(project_root)
    return found[0] if found else None


def load_mapping(path: str) -> tuple:
    """
    读一份映射 → `(data, warnings)`（按后缀分派：JSON / Excel）。

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
    if p.suffix.lower() == '.xls':
        raise MappingError(xls_unsupported(p))
    if is_workbook(p):
        # 惰性 import：只有用 Excel 的项目才需要 openpyxl 的读取路径，
        # JSON 项目连这个模块都不加载（启动不多花一毫秒）。
        from storygraph.assets.mapping_excel import read_workbook

        return read_workbook(p)
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
        data, warnings = cached[1], list(cached[2])
    else:
        try:
            data, warnings = load_mapping(path)
        except MappingError as e:
            data, warnings = {}, [str(e)]
        _CACHE[path] = (stamp, data, list(warnings))
        if len(_CACHE) > _CACHE_MAX:
            for key in list(_CACHE)[:-_CACHE_MAX]:
                _CACHE.pop(key, None)
    # "还有另一份"这条**不进缓存**：它取决于别的文件在不在，
    # 而缓存键只有这一份的 mtime+size（多一份文件不会让缓存失效）。
    shadow = shadow_note(root, path)
    if shadow:
        warnings.append(shadow)
    return data, warnings, path


def shadow_note(project_root: str, active: str) -> str:
    """
    项目里还有别的映射文件时，说清"当前用的是哪份、别改错文件"。

    两份都在不是错误（人试一次 Excel、或从别人那儿收了一份 JSON 都会这样），
    但**必须说出来** —— 否则"我改了 JSON 怎么没生效"要查半天。
    """
    try:
        others = [p for p in mapping_sources(project_root) if p != active]
    except OSError as e:                      # noqa: BLE001 - 查不到就算了
        swallow('assets.asset_mapping._shadow_note', e)
        return ''

    def rel(value):
        try:
            return str(Path(value).relative_to(Path(project_root)))
        except (ValueError, OSError):
            return str(value)

    if not others:
        return ''
    return ('项目里还有 ' + '、'.join(rel(p) for p in others)
            + f'，但当前以 {rel(active)} 为准 —— 改别的那份不会生效')


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
                         asset_id: str = '', exact: bool = True,
                         rules: bool = True) -> Optional[Identity]:
    """
    映射 → `Identity`（**没有命中**返回 `None`，交给字面量推断）。

    顺序（契约 §1.2）：`images` 精确覆盖（asset ID / 相对路径）优先，
    然后 `rules` **按数组顺序**先中先得。

    `exact` / `rules` 是**分层开关**（2026-10-31，真源与优先级 §4.3）：
    精确覆盖与 glob 规则**不是同一层** —— 前者是人指着某一张图说"它叫
    这个"，后者是"这一类大概都这样"。`asset_identity.resolve_image` 要在
    两者**之间**插进"项目自己声明过的名字"，所以必须能单独取一层：

        resolve_with_mapping(..., rules=False)   # 只要 mapping-exact
        resolve_with_mapping(..., exact=False)   # 只要 mapping-rule

    两个都为 False → 恒 `None`（调用方拿它当"这一层没话可说"）。
    """
    if not isinstance(mapping, Mapping) or not mapping:
        return None
    rel = normalize_relpath(relpath)
    aid = str(asset_id or '').strip()

    if exact:
        hit = _match_exact_image(mapping.get('images'), aid, rel)
        if hit is not None:
            return hit

    if not rules:
        return None

    rule_list = mapping.get('rules')
    if isinstance(rule_list, list):
        for rule in rule_list:
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


def resolve_exact_image(mapping, *, relpath: str = '',
                        asset_id: str = '') -> Optional[Identity]:
    """只要**精确覆盖**那一层（人点名某一张图）。没有就 `None`。"""
    return resolve_with_mapping(mapping, relpath=relpath, asset_id=asset_id,
                                rules=False)


def resolve_rule_image(mapping, *, relpath: str = '',
                       asset_id: str = '') -> Optional[Identity]:
    """只要 **glob 规则**那一层。没有就 `None`。"""
    return resolve_with_mapping(mapping, relpath=relpath, asset_id=asset_id,
                                exact=False)


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


# ---------------------------------------------------------------------------
#  写回（原子 + 旧文件 .bak）
# ---------------------------------------------------------------------------

def save_mapping(path: str, data):
    """
    把映射写回磁盘（按后缀分派）：**原子替换 + 旧文件 `.bak`**。

    · `.json` → 本函数写（见下面三条纪律），返回 `None`；
    · `.xlsx` / `.xlsm` → 交给 `assets.mapping_excel.write_workbook` 整份重建，
      返回"写了多少行"的计数 dict —— 那是"新建一份表"（导出初稿 / 模板），
      **不是**给人做的表来回覆盖（见 `mapping_excel` 模块 docstring ③）；
    · 失败抛 `MappingError`，由调用方决定怎么提示。

    JSON 这条路的三条纪律：
    · 旧文件存在就先逐字节备份成 `<文件名>.bak`；备份失败 → **不写**
      （宁可没改，也不让人失去上一版）；
    · 新内容先写临时文件、`os.replace` 一步换上去 —— 中途断电不会留下
      半份 JSON（这份文件会进 git，半份 = 一次坏提交）；
    """
    if not isinstance(data, Mapping):
        raise MappingError('映射数据必须是 JSON 对象')
    target = Path(str(path or '').strip())
    if not str(path or '').strip() or not target.name:
        raise MappingError('没有给出映射文件路径')
    if is_workbook(target):
        from storygraph.assets.mapping_excel import write_workbook

        return write_workbook(str(target), data)
    if target.suffix.lower() == '.xls':
        raise MappingError(
            f'{target.name} 是老式 .xls 格式，写不了 —— 存成 .xlsx 或 .json。')
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
    return None


__all__ = [
    'MAPPING_SCHEMA', 'MAPPING_FILENAME', 'MAPPING_XLSX_FILENAME',
    'MAPPING_NAMES', 'WORKBOOK_SUFFIXES', 'KNOWN_TOP_KEYS', 'KNOWN_ENTRY_KEYS',
    'MappingError', 'is_workbook', 'file_format', 'mapping_sources',
    'shadow_note', 'find_mapping_path', 'load_mapping', 'validate_mapping',
    'mapping_cached', 'resolve_with_mapping', 'resolve_exact_image',
    'resolve_rule_image', 'draft_mapping', 'save_mapping',
]
