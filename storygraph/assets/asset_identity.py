"""
storygraph.assets.asset_identity
================================
**"这张图是谁"的唯一真源**：把一行图片资产解析成
`Identity(kind, tag, attrs, display, source, showable)`。

为什么必须有它（2026-10-30 用户实测的 P0）
------------------------------------------
用户原话：

> 立绘怎么一堆单独的分类，什么 relaxing，resigned —— 这不都是凌的表情吗
> 只有文件名是角色名的可以匹配；直接放角色那个头像图就能动

根因不是"界面没做"，而是**没有"图像 → 角色/属性"的唯一真源**：
旧实现只看资源表「标签」列，Tag 空的行（全部 `cc_preview/<角色>/<表情>.png`
副本、头像）就只剩资产 ID，于是：

  · 每个表情被当成一个独立角色分类（110 个单图分类）；
  · 选中预览图写出非法的 `show refusing`（引擎里没有这张图）。

真实项目里**路径本身就把答案写着**：`<任意目录>/<角色 tag>/<表情>.png`。
所以这里只做一件事：**按字面量证据**（映射文件 → 资源表 Tag 列 → 路径目录段
→ 文件名 stem）判断身份；**认不出就 `unknown`，绝不猜**（猜一个名字比
"不知道"坏得多 —— 会静默产出一条游戏里找不到图的 `show`）。

判定顺序（契约 §1.1，只认字面量）
----------------------------------
⓪ **项目自己声明过的图像名**（`image 名:` / ATL 块 / 脚本合并进来的
   `_from_script` 行）—— 名字是项目 `.rpy` 里的字面量，直接 `showable=True`；
① 映射精确覆盖（asset ID / 项目相对路径）
② 映射 glob 规则（按数组顺序，先中先得）
③ 资源表「Tag」列（是**已知角色 tag** 才算 sprite；非角色词走 kind 判定）
④ 路径**目录段**命中已知角色 tag：`.../<tag>/<表情>.png`
⑤ 文件 stem 恰等于已知角色 tag（`.../ling.png`）
⑥ `unknown`（不猜，`showable=False`）

kind 判定
---------
`kind ∈ sprite / preview / avatar / bg / cg / ui / other / unknown`。

  · 映射指定 > tag 词表（bg/cg/ui）> 角色 tag（sprite）；
  · **通用"副本检测"**（不写死 `cc_preview`）：同 tag、同 stem 还**另有一行
    资源表 Tag 非空** → 这张是副本 → `preview`；目录名里带
    `preview / thumb / thumbnail` 也算（同样是通用词，不是项目名）；
  · avatar 只认映射指定或**目录段明确写着 `avatar`**；宁可 `unknown`。

`showable` 是"能不能直接出现在 `show <spec>` 里"：真立绘 / 真背景 / 真-CG /
真界面图层为真；预览图、头像、认不出的图一律为假（界面据此拒绝或自动换真图）。

**禁止**在本模块 import 任何 UI 框架（契约 §1.1：纯函数层）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Optional

from storygraph.core.safeguard import swallow
from storygraph.core.vocabulary import NON_ACTOR_TAGS
from storygraph.assets.asset_dimensions import row_values
from storygraph.assets.atl_block import is_atl_row

#: 身份类别（唯一真源；界面/分组/映射都按它分流）。
KINDS = ('sprite', 'preview', 'avatar', 'bg', 'cg', 'ui', 'other', 'unknown')

#: 能直接写进 `show <spec>` 的类别（其余一律先换真图或拒绝）。
SHOWABLE_KINDS = frozenset({'sprite', 'bg', 'cg', 'ui'})

#: 来源标记（对照表要显示"这个结论是谁给的"）。
SOURCES = ('mapping', 'mapping-rule', 'tag-column', 'folder', 'stem', 'unknown')

#: 副本/缩略图目录的**通用词**（不是项目名！）。
#: 只用来判 kind=preview；判 tag 仍然只靠目录段命中的已知角色 tag。
_PREVIEW_DIR_WORDS = frozenset({
    'preview', 'previews', 'thumb', 'thumbs', 'thumbnail', 'thumbnails',
})

#: 头像目录的**通用词**：只有目录段明确写着它才给 kind=avatar（宁可 unknown）。
_AVATAR_DIR_WORDS = frozenset({'avatar', 'avatars'})

#: 名字两侧的引号/空格（角色表里显示名常写成 `「凌跃捷」`）。
_QUOTE_CHARS = '「」『』""''\u3000 '

#: 别名分隔符（角色表 `Aliases` 一格里可写多个：`MOM|母亲|老妈`）。
_ALIAS_SEP = re.compile(r'[|｜,，、;；\s]+')

#: 目录名分词（与 `asset_registry_scan._segment_tokens` 同口径）。
_SEG_SPLIT = re.compile(r'[_\-.()\[\]\s]+')


@dataclass(frozen=True)
class Identity:
    """
    一张图片资产的**身份**。

    · `kind`：见 `KINDS`；
    · `tag`：角色 tag（**必须是项目已知 tag**；非角色图层给 `bg`/`cg`/`ui`，
      认不出 = ''）；
    · `attrs`：属性词（`Attributes`/`Value` 拆出来的原词，保序去重；
      路径推断时是文件名 stem）；
    · `display`：角色显示名（characters 表），没有 = ''；
    · `source`：结论来源，见 `SOURCES`（对照表要显示"谁说的"）；
    · `showable`：能不能直接写进 `show <spec>`。
    """

    kind: str = 'unknown'
    tag: str = ''
    attrs: tuple = ()
    display: str = ''
    source: str = ''
    showable: bool = False

    def __post_init__(self):
        # 属性统一成 tuple[str, ...]（调用方可能传 list；frozen 也允许在这里
        # 用 object.__setattr__ 归一，避免下游各自 .split() 一遍）。
        attrs = tuple(str(a).strip() for a in (self.attrs or ())
                      if str(a).strip())
        object.__setattr__(self, 'attrs', attrs)
        object.__setattr__(self, 'kind', str(self.kind or 'unknown'))
        object.__setattr__(self, 'tag', str(self.tag or '').strip())
        object.__setattr__(self, 'display', str(self.display or '').strip())
        object.__setattr__(self, 'source', str(self.source or ''))
        object.__setattr__(self, 'showable', bool(self.showable))


# ---------------------------------------------------------------------------
#  路径 / 行 的基础读取
# ---------------------------------------------------------------------------

_DRIVE_RE = re.compile(r'^[A-Za-z]:$')


def normalize_relpath(path: str, project_root: str = '') -> str:
    """
    任意写法（绝对路径 / `sr_general/game/...` / Windows 反斜杠）→
    **项目相对 POSIX 路径**（`game/images/characters/ling/happy.png`）。

    为什么必须归一：资源表的 `Path` 有四种真实写法（2026-10-05 实测）：

      · `game/images/x.png`   —— 相对项目根；
      · `images/x.png`        —— 相对 `game/`；
      · `sr_general/game/images/x.png` —— 带项目目录名（74 行真实存在）；
      · 绝对路径。

    映射文件的 glob 是**项目相对**写的（`game/images/<tag>/*.png`），
    不归一就永远匹配不上。这里只做字符串层面的归一，**不 stat 磁盘**
    （纯函数、可在 UI 线程任意调用）。
    """
    text = str(path or '').strip().replace('\\', '/')
    if not text:
        return ''
    root_name = ''
    if project_root:
        try:
            root = Path(str(project_root))
            root_name = root.name.lower()
            if root.is_absolute() and _looks_absolute(text):
                try:
                    rel = Path(text).resolve().relative_to(root.resolve())
                    return '/'.join(rel.parts)
                except (ValueError, OSError) as e:
                    # 不在项目根下：继续走下面的"去盘符"逻辑（映射对不上是
                    # 事实，不编一个假的相对路径）。记一笔而不是静默吞掉。
                    swallow('assets.asset_identity.normalize_relpath', e)
        except (OSError, ValueError):
            root_name = ''

    parts = [p for p in text.split('/') if p not in ('', '.')]
    if parts and _DRIVE_RE.match(parts[0]):
        parts = parts[1:]
    # `sr_general/game/...` → 去掉开头的项目目录名（只认**第一段**：
    # 深层的同名目录可能是项目自己的资产目录，不能乱砍）。
    if root_name and parts and parts[0].lower() == root_name:
        parts = parts[1:]
    clean = []
    for part in parts:
        if part == '..':
            if clean:
                clean.pop()
            continue
        clean.append(part)
    return '/'.join(clean)


def _looks_absolute(text: str) -> bool:
    return (text.startswith('/') or text.startswith('\\\\')
            or bool(_DRIVE_RE.match(text.split('/')[0])))


def asset_id_of(row: Mapping | None) -> str:
    """资产行 → 资产 ID（`ID` 列；没有就空串）。"""
    return str((row or {}).get('ID') or '').strip()


def _row_relpath(row: Mapping) -> str:
    """行内 `Path` → 归一路径（不带 project_root，只做字符串归一）。"""
    return normalize_relpath(str((row or {}).get('Path') or ''))


def _iter_rows(rows) -> Iterable[dict]:
    """
    行集合 → 行迭代器。

    同时接受两种真实形态（调用方不用关心）：
      · `registry.images` 这种 `{asset_id: row}` 映射；
      · `[row, ...]` 列表（测试 / 临时构造）。
    """
    if not rows:
        return []
    if isinstance(rows, Mapping):
        return [dict(r) for r in rows.values() if isinstance(r, Mapping)]
    out = []
    for r in rows:
        if isinstance(r, Mapping):
            out.append(dict(r))
    return out


def _dirs_of(relpath: str) -> list:
    """项目相对路径 → 目录段（小写；不含文件名）。"""
    parts = PurePosixPath(str(relpath or '')).parts
    return [str(p).lower() for p in parts[:-1]]


def _stem_of(relpath: str) -> str:
    """项目相对路径 / 资产 ID → 文件名 stem（`.../ling/happy.png` → `happy`）。"""
    text = str(relpath or '').strip().replace('\\', '/')
    if not text:
        return ''
    return PurePosixPath(text).stem


def _tokens(segment: str) -> list:
    """目录段 → 词元（`cc_preview` → `['cc', 'preview']`）。"""
    return [t for t in _SEG_SPLIT.split(str(segment or '').lower()) if t]


def _has_dir_word(relpath: str, words) -> bool:
    """路径的**任一目录段**含 `words` 里的词（按词元精确匹配）。"""
    for seg in _dirs_of(relpath):
        toks = _tokens(seg)
        if seg in words or any(t in words for t in toks):
            return True
    return False


# ---------------------------------------------------------------------------
#  词表 / 角色表
# ---------------------------------------------------------------------------

def kind_from_tag_word(word: str) -> str:
    """
    一个 tag 词 → 它能代表的 kind（认不出 = ''）。

    只查**共享词表**（`core.vocabulary.NON_ACTOR_TAGS` 与扫描模块的
    bg/cg/ui 词表，都是全项目共用的那一份），不为任何具体项目加分支。
    """
    low = str(word or '').strip().lower()
    if not low:
        return ''
    if low in ('bg', 'bg_', 'background', 'backdrop', 'scene'):
        return 'bg'
    if low in ('cg', 'event', 'cutscene', 'illust', 'illustration', 'gallery'):
        return 'cg'
    if low in _PREVIEW_DIR_WORDS:
        return 'preview'
    if low in _AVATAR_DIR_WORDS:
        return 'avatar'
    if low in NON_ACTOR_TAGS:
        return 'ui'
    return ''


def _clean_display(text) -> str:
    """角色显示名去掉两侧的引号/空格（`「凌跃捷」` → `凌跃捷`）。"""
    return str(text or '').strip().strip(_QUOTE_CHARS).strip()


def _split_aliases(raw) -> tuple:
    """角色别名格 → 别名元组（保序去重；认不出分隔符就当一个）。"""
    words = [w for w in _ALIAS_SEP.split(str(raw or '')) if w]
    out = []
    for w in words:
        if w not in out:
            out.append(w)
    return tuple(out)


def known_characters(registry) -> dict:
    """
    资源表「角色」→ `{tag: {'tag','display','aliases','char_id','avatar'}}`。

    tag 取 `ImageTag`（`show` 要写的那一列）；老表没有该列时退回角色变量名
    （`dsl.Symbols.actor_image_tag` 的同一口径：`ImageTag or ID`）。
    **非角色 tag（bg/cg/ui…）一律排除** —— 它们不可能是台上的人。

    这是"已知角色 tag"的第一来源（另外两个：映射文件 `characters`、
    引擎事实里带属性的 tag；见 `known_tags_of` / `engine_fact_tags`）。
    """
    out: dict = {}
    chars = getattr(registry, 'characters', None) or {}
    if not isinstance(chars, Mapping):
        return out
    for cid, row in chars.items():
        row = row if isinstance(row, Mapping) else {}
        var = str(cid or '').strip()
        tag = str(row.get('ImageTag') or '').strip() or var
        if not tag or tag.lower() in NON_ACTOR_TAGS:
            continue
        entry = {
            'tag': tag,
            'char_id': var,
            'display': _clean_display(row.get('Name')),
            'aliases': _split_aliases(row.get('Aliases')),
            'avatar': '',
        }
        prev = out.get(tag)
        if prev is None:
            out[tag] = entry
            continue
        # 同一个 tag 有多个角色变量（别名角色）：只补空缺，不覆盖已有显示名。
        if not prev.get('display') and entry.get('display'):
            prev['display'] = entry['display']
        merged = list(prev.get('aliases') or ())
        for alias in entry.get('aliases') or ():
            if alias not in merged:
                merged.append(alias)
        prev['aliases'] = tuple(merged)
    return out


def _norm_chars(known_chars) -> dict:
    """`known_chars` 两种形态都认：`{tag: 显示名}` 或 `{tag: {...}}`。"""
    if not known_chars:
        return {}
    if not isinstance(known_chars, Mapping):
        return {}
    return dict(known_chars)


def _display_of(known_chars, tag: str) -> str:
    """已知角色表里这个 tag 的显示名（认不出 = ''；**不猜**）。"""
    chars = _norm_chars(known_chars)
    if not chars or not tag:
        return ''
    entry = chars.get(tag)
    if entry is None:
        for key, value in chars.items():
            if str(key).lower() == str(tag).lower():
                entry = value
                break
    if isinstance(entry, Mapping):
        return _clean_display(entry.get('display') or entry.get('Name')
                              or entry.get('name') or '')
    return _clean_display(entry)


def _tag_map(known_tags, known_chars=None) -> dict:
    """已知 tag 集合 → `{小写: 原始写法}`（大小写不敏感匹配，回报原写法）。"""
    out: dict = {}
    sources = []
    if known_tags:
        sources.append(known_tags)
    if isinstance(known_chars, Mapping):
        sources.append(known_chars.keys())
    for source in sources:
        try:
            items = source.keys() if isinstance(source, Mapping) else source
        except AttributeError:
            continue
        for tag in items or ():
            word = str(tag or '').strip()
            if not word or word.lower() in NON_ACTOR_TAGS:
                continue
            out.setdefault(word.lower(), word)
    return out


def known_tags_of(registry, mapping=None) -> set:
    """
    **项目已知角色 tag**：characters 表（`ImageTag or ID`）+ 映射 `characters`。

    第三个来源（引擎事实里带属性的 tag）在 `engine_fact_tags` 里单独取 ——
    它要读文件，跟这张表分开，调用方按需合并（`app.picker.pick_context`
    就是三路合并后的结果）。
    """
    tags = set(known_characters(registry))
    chars = (mapping or {}).get('characters') if isinstance(mapping, Mapping) else None
    if isinstance(chars, Mapping):
        for tag in chars:
            word = str(tag or '').strip()
            if word:
                tags.add(word)
    return {t for t in tags if t.lower() not in NON_ACTOR_TAGS}


def engine_fact_tags(project_root) -> set:
    """
    引擎事实（`<项目>/storygraph.engine_facts.json`）里**带属性的 tag**。

    为什么它算"已知角色 tag"：引擎自己枚举出了这个 tag 的属性
    （`layeredimage` 的 group）—— 能带属性，说明它是立绘/图像 tag，
    不是背景那种单张图。没有事实文件 / 事实不可用时返回空集（**不猜**）。
    """
    root = str(project_root or '')
    if not root:
        return set()
    try:
        from storygraph.core import engine_facts as EF
        path = EF.facts_path(root)
        if not path.is_file():
            return set()
        facts = EF.loads(path.read_text(encoding='utf-8'))
        ok, _why = EF.is_collectable(facts)
        if not ok:
            return set()
        attrs = EF.value_of(facts, 'attributes', {}) or {}
        tags = EF.value_of(facts, 'tags', []) or []
        if not isinstance(attrs, Mapping):
            return set()
        out = set()
        for tag in tags:
            name = str(tag or '').strip()
            if not name or name.lower() in NON_ACTOR_TAGS:
                continue
            rows = attrs.get(name) or []
            if any(isinstance(r, Mapping) and str(r.get('attribute') or '').strip()
                   for r in rows):
                out.add(name)
        return out
    except Exception as e:                      # noqa: BLE001 - 事实坏了不该组不出图
        swallow('assets.asset_identity.engine_fact_tags', e)
        return set()


# ---------------------------------------------------------------------------
#  副本检测（通用：不认项目名）
# ---------------------------------------------------------------------------

def build_copy_index(rows) -> dict:
    """
    `rows` → `{tag_lower: {stem_lower, ...}}`：**有 Tag 的行**的"可对应名字"索引。

    每个 Tag 非空的行贡献三种写法（都是**字面量**，不做模糊匹配）：
      · 文件 stem（`game/images/characters/ling/happy.png` → `happy`）；
      · 资产 ID（`ling_happy`）；
      · ID 去掉 `tag_` 前缀后的部分（`ling_happy` → `happy`）；
      · `Attributes`/`Value` 里的词。

    为什么要这么宽：真实项目里**真立绘行**的 Path 可能叫
    `.../ling/lan_happy.png`（stem 带角色前缀），而副本行叫
    `cc_preview/ling/happy.png`（stem 只有表情）——只比 stem 会漏。
    但匹配的仍然只是"同一 tag 下这个字符串确实存在"，不是猜。
    """
    idx: dict = {}
    for row in _iter_rows(rows):
        tag = str(row.get('Tag') or '').strip()
        if not tag:
            continue
        bucket = idx.setdefault(tag.lower(), set())
        path = _row_relpath(row)
        stem = _stem_of(path)
        if stem:
            bucket.add(stem.lower())
        aid = asset_id_of(row)
        if aid:
            bucket.add(aid.lower())
            prefix = tag.lower() + '_'
            if aid.lower().startswith(prefix) and len(aid) > len(prefix):
                bucket.add(aid[len(prefix):].lower())
        for word in row_values(row):
            word = str(word or '').strip()
            if word:
                bucket.add(word.lower())
    return idx


def _is_copy(tag: str, stem: str, copy_index) -> bool:
    """这张（Tag 空的）图是不是"另一行 Tag 非空"行注册过的同 tag+同 stem 副本。"""
    if not tag or not stem or not copy_index:
        return False
    pool = copy_index.get(str(tag).strip().lower()) or ()
    return str(stem).strip().lower() in pool


# ---------------------------------------------------------------------------
#  kind 的弱线索（只在 Tag 列 / 目录段都给不出结论时才用）
# ---------------------------------------------------------------------------

def path_kind(relpath: str) -> str:
    """
    路径 → 弱 kind 线索（`bg` / `cg` / `ui`，认不出 = ''）。

    复用扫描器已经逐条对齐过真实项目的 `_detect_category`
    （就是 `test_asset_categorize.py` 钉住的那套规则：目录词元 > 文件名
    线索 > other）。**同包内**复用，不另造第二套目录词表。
    只有 `background/cg/ui` 会变成 kind；`character` 没有已知 tag 时
    **不猜是谁**（交给上层 unknown）。
    """
    rel = str(relpath or '')
    if not rel:
        return ''
    try:
        from storygraph.assets.asset_registry_scan import _detect_category
        cat = _detect_category(PurePosixPath(rel))
    except Exception as e:                      # noqa: BLE001
        swallow('assets.asset_identity.path_kind', e)
        return ''
    return {'background': 'bg', 'cg': 'cg', 'ui': 'ui'}.get(cat, '')


# ---------------------------------------------------------------------------
#  核心：一行的身份
# ---------------------------------------------------------------------------

def resolve_image(row: dict, *, relpath: str = '', known_tags=None,
                  known_chars: Mapping | None = None, mapping=None,
                  rows=None, copy_index=None) -> Identity:
    """
    一行图片资产 → `Identity`（判定顺序见模块 docstring）。

    参数
    ----
    row         资源表的一行（`ID/Path/Tag/Attributes/Value/...`）。
    relpath     项目相对路径；不给就用 `row['Path']` 现归一。
    known_tags  已知角色 tag（`app.picker.pick_context` 会给三路合并后的集合）。
    known_chars `{tag: 显示名}` 或 `{tag: {'display': ...}}`（只要显示名）。
    mapping     映射文件数据（`asset_mapping.load_mapping` 的 dict）。
    rows        全部图片行（用于**副本检测**）；支持 dict / list。
    copy_index  `build_copy_index(rows)` 的结果；批量调用时传它可避免 O(n²)。
    """
    row = dict(row or {})
    aid = asset_id_of(row)
    rel = normalize_relpath(relpath or str(row.get('Path') or ''))
    tag_map = _tag_map(known_tags, known_chars)
    attrs = tuple(row_values(row))

    # ① / ② 映射优先（精确覆盖 > 规则；规则内部按数组顺序）
    if isinstance(mapping, Mapping) and mapping:
        try:
            from storygraph.assets.asset_mapping import resolve_with_mapping
            hit = resolve_with_mapping(mapping, relpath=rel, asset_id=aid)
        except Exception as e:                  # noqa: BLE001 - 映射坏了退到推断
            swallow('assets.asset_identity.resolve_image.mapping', e)
            hit = None
        if hit is not None:
            if not hit.display:
                hit = replace(hit, display=_display_of(known_chars, hit.tag))
            return hit

    # ⓪ 项目自己声明过的图像名（ATL 块 / 脚本合并行）：名字是 `.rpy` 里的
    # 字面量 —— 不许因为"Tag 不在角色表里"就把一条**原本能用的 show** 封掉
    # （用户 2026-10-30 的映射表要治理的是"猜"，不是"项目自己声明过的"）。
    if _is_declared_image(row):
        return _from_declared(row, attrs, tag_map, known_chars)

    idx = copy_index
    if idx is None and rows is not None:
        idx = build_copy_index(rows)

    tag_col = str(row.get('Tag') or '').strip()
    if tag_col:
        return _from_tag_column(row, tag_col, rel, attrs, tag_map,
                                known_chars, idx, aid)
    return _from_path(row, rel, attrs, tag_map, known_chars, idx, aid)


def _is_declared_image(row: Mapping) -> bool:
    """
    这一行是不是项目 `.rpy` 里**字面量声明过**的图像名？

    三条证据（都来自项目数据，不是猜）：
      · `_atl` / `Notes` 里的 ATL 标记 → `image 名:` + 块体（贴图在块里）；
      · `_from_script` → 解析脚本时合并进来的 `image` 声明行；
      · `_layered` → `layeredimage <名>:` 声明。

    普通"磁盘扫描出来的图"这三条都没有 —— 所以这里不会把认不出的
    零散图片一股脑放行。
    """
    row = row or {}
    if row.get('_atl') or row.get('_from_script') or row.get('_layered'):
        return True
    try:
        return bool(is_atl_row(row))
    except Exception as e:                      # noqa: BLE001 - 标记坏了不该拦住选择
        swallow('assets.asset_identity._is_declared_image', e)
        return False


def _from_declared(row, attrs: tuple, tag_map: dict, known_chars) -> Identity:
    """
    声明过的图像名 → `Identity`（`showable=True`：名字在引擎里真实存在）。

    kind 仍按同一套词表判（`bg`/`cg`/`ui`/`sprite`/`other`），**不改 show 的
    合法性**；`tag` 就是声明名的第一个词、`attrs` 是剩下的词
    （`image logo blink:` → `logo blink`）。
    """
    tag_col = str(row.get('Tag') or '').strip()
    if not tag_col:
        # 名字都拆不出来：不编，按认不出处理。
        return Identity(kind='unknown', tag='', attrs=(), source='unknown',
                        showable=False)
    low = tag_col.lower()
    if low in tag_map:
        tag = tag_map[low]
        return Identity(kind='sprite', tag=tag, attrs=attrs,
                        display=_display_of(known_chars, tag),
                        source='tag-column', showable=True)
    kind = kind_from_tag_word(low) or 'other'
    return Identity(kind=kind, tag=tag_col, attrs=attrs,
                    source='tag-column', showable=True)


def _from_tag_column(row, tag_col: str, rel: str, attrs: tuple,
                     tag_map: dict, known_chars, copy_index, aid: str) -> Identity:
    """③ 资源表「Tag」列：是已知角色 tag → sprite；否则按词表判非角色 kind。"""
    low = tag_col.lower()
    if low in tag_map:
        tag = tag_map[low]
        return Identity(kind='sprite', tag=tag, attrs=attrs,
                        display=_display_of(known_chars, tag),
                        source='tag-column', showable=True)
    kind = kind_from_tag_word(low)
    if kind in ('bg', 'cg', 'ui'):
        # 非角色图层：Tag 列明确写着它是哪一类 → 可以 show（真图层）。
        return Identity(kind=kind, tag=tag_col, attrs=attrs,
                        source='tag-column', showable=True)
    if kind == 'avatar' or _has_dir_word(rel, _AVATAR_DIR_WORDS):
        tag = _char_hint(rel, attrs, tag_map)
        return Identity(kind='avatar', tag=tag, attrs=(),
                        display=_display_of(known_chars, tag),
                        source='tag-column', showable=False)
    if kind == 'preview' or _has_dir_word(rel, _PREVIEW_DIR_WORDS):
        tag = _char_hint(rel, attrs, tag_map)
        return Identity(kind='preview', tag=tag, attrs=attrs,
                        display=_display_of(known_chars, tag),
                        source='tag-column', showable=False)
    # Tag 写了、但不是已知角色 tag、也不是 bg/cg/ui —— 不猜它是谁。
    return Identity(kind='other', tag='', attrs=attrs,
                    source='tag-column', showable=False)


def _from_path(row, rel: str, attrs: tuple, tag_map: dict, known_chars,
               copy_index, aid: str) -> Identity:
    """④ 目录段 / ⑤ stem（+ 头像目录、副本检测、弱 kind 线索）。"""
    dirs = _dirs_of(rel)
    stem = _stem_of(rel)
    preview_path = _has_dir_word(rel, _PREVIEW_DIR_WORDS)

    # 头像：目录段明确写着 avatar（契约：宁可 unknown，也不把立绘猜成头像）
    if _has_dir_word(rel, _AVATAR_DIR_WORDS):
        tag = _char_hint(rel, attrs, tag_map)
        return Identity(kind='avatar', tag=tag, attrs=(),
                        display=_display_of(known_chars, tag),
                        source='folder', showable=False)

    # ④ 路径里**最近的**已知角色 tag 目录段（`.../<tag>/<表情>.png`）
    for i in range(len(dirs) - 1, -1, -1):
        tag = tag_map.get(dirs[i])
        if not tag:
            continue
        stem_attrs = _attrs_from_stem(stem, tag)
        copy = preview_path or _is_copy(tag, stem, copy_index)
        kind = 'preview' if copy else 'sprite'
        return Identity(kind=kind, tag=tag, attrs=stem_attrs,
                        display=_display_of(known_chars, tag),
                        source='folder', showable=(kind == 'sprite'))

    # 预览目录、但目录里没有已知角色 tag：tag 只能从 stem 精确命中
    if preview_path:
        tag = tag_map.get(stem.lower(), '')
        attrs_out = () if tag else (stem,)
        return Identity(kind='preview', tag=tag, attrs=attrs_out,
                        display=_display_of(known_chars, tag),
                        source='folder', showable=False)

    # ⑤ 文件 stem 恰等于已知角色 tag（`.../images/ling.png` 这种整图约定）
    tag = tag_map.get(stem.lower(), '')
    if tag:
        copy = _is_copy(tag, stem, copy_index)
        kind = 'preview' if copy else 'sprite'
        rest = tuple(a for a in attrs
                     if a.lower() not in (tag.lower(), stem.lower()))
        return Identity(kind=kind, tag=tag, attrs=rest,
                        display=_display_of(known_chars, tag),
                        source='stem', showable=(kind == 'sprite'))

    # 弱线索：路径像 bg/cg/ui（没有 Tag 列时也能归到对应组，但**不 show** ——
    # 没有 Tag/映射就说不出 `show` 该写什么，写 ID 等于骗人）。
    kind = path_kind(rel)
    if kind:
        return Identity(kind=kind, tag='', attrs=(stem,) if stem else (),
                        source='folder', showable=False)

    # ⑥ 认不出 = unknown（**不猜**）
    return Identity(kind='unknown', tag='', attrs=(), source='unknown',
                    showable=False)


def _attrs_from_stem(stem: str, tag: str) -> tuple:
    """
    `<角色目录>/<文件名>.png` → 属性元组。

    `happy.png` → `('happy',)`；`ling_happy.png` → `('happy',)`（去掉重复的
    角色 tag 前缀 —— 真实项目两种命名都有，去掉前缀后 `show ling happy`
    才是对的）；`_hd_cut.png`（下划线开头）原样保留。
    """
    stem = str(stem or '').strip()
    tag = str(tag or '').strip()
    if not stem or not tag:
        return (stem,) if stem else ()
    low_stem, low_tag = stem.lower(), tag.lower()
    if low_stem == low_tag:
        return ()
    for sep in ('_', '-', ' '):
        prefix = low_tag + sep
        if low_stem.startswith(prefix) and len(stem) > len(prefix):
            rest = stem[len(prefix):].strip()
            return (rest,) if rest else ()
    return (stem,)


# ---------------------------------------------------------------------------
#  批量解析 / 项目上下文（app 层与 UI 共用，避免各写一套"已知 tag"）
# ---------------------------------------------------------------------------

def resolve_images(rows, *, known_tags=None, known_chars=None, mapping=None,
                   project_root: str = '', copy_index=None) -> dict:
    """
    一批图片行 → `{asset_id: Identity}`（**批量入口**，一次建好副本索引）。

    为什么要有它：选择器分组 / 用途过滤都要遍历全部图片；逐行调
    `resolve_image` 会每行重建一次副本索引（1720 行 = O(n²)）。这里
    先建索引再逐行解析，结果与单行调用完全一致。
    """
    row_list = _iter_rows(rows)
    out: dict = {}
    if not row_list:
        return out
    if copy_index is None:
        copy_index = build_copy_index(row_list)
    root = str(project_root or '')
    for row in row_list:
        aid = asset_id_of(row)
        if not aid:
            continue
        rel = normalize_relpath(str(row.get('Path') or ''), root)
        out[aid] = resolve_image(row, relpath=rel, known_tags=known_tags,
                                 known_chars=known_chars, mapping=mapping,
                                 rows=row_list, copy_index=copy_index)
    return out


def project_context(registry, project_root: str = '', *, mapping=None) -> dict:
    """
    装配"解析身份"要用的项目上下文（**唯一组装处**，界面不用自己读表）。

    返回::

        {'known_tags': frozenset[str],   # 角色表 + 映射 characters + 引擎事实
         'known_chars': dict,            # known_characters() 的结果
         'mapping': dict,                # 没有映射 = {}
         'mapping_path': str,            # 没有 = ''
         'warnings': tuple[str, ...],    # 映射文件的问题（不抛）
         'rows': dict,                   # registry.images（{id: row}）
         'copy_index': dict,             # 批量解析用的副本索引
         'project_root': str}

    已知角色 tag 三个来源（规划 §2.4）：characters 表「立绘 tag」、
    映射 `characters`、引擎事实里**带属性**的 tag。三路都只是"候选集合"，
    真正的身份判定仍在 `resolve_image`（只认字面量）。
    """
    root = str(project_root or '')
    known_chars = known_characters(registry)
    warnings: tuple = ()
    mapping_path = ''
    if mapping is None:
        data = {}
        if root:
            try:
                from storygraph.assets.asset_mapping import mapping_cached
                data, warn, mapping_path = mapping_cached(root)
                warnings = tuple(warn or ())
            except Exception as e:              # noqa: BLE001 - 映射坏了不该打不开
                swallow('assets.asset_identity.project_context.mapping', e)
                data = {}
        mapping = data
    known = set(known_chars)
    chars = mapping.get('characters') if isinstance(mapping, Mapping) else None
    if isinstance(chars, Mapping):
        known |= {str(tag).strip() for tag in chars if str(tag).strip()}
    try:
        known |= engine_fact_tags(root)
    except Exception as e:                      # noqa: BLE001
        swallow('assets.asset_identity.project_context.facts', e)
    known_tags = frozenset(t for t in known
                           if t and t.lower() not in NON_ACTOR_TAGS)
    rows = getattr(registry, 'images', None) if registry is not None else None
    rows = dict(rows) if isinstance(rows, Mapping) else {}
    return {
        'known_tags': known_tags,
        'known_chars': known_chars,
        'mapping': mapping if isinstance(mapping, Mapping) else {},
        'mapping_path': str(mapping_path or ''),
        'warnings': warnings,
        'rows': rows,
        'copy_index': build_copy_index(rows),
        'project_root': root,
    }


def _char_hint(rel: str, attrs, tag_map: dict) -> str:
    """
    头像/预览目录里的文件名 → 角色 tag（只认**精确命中**已知 tag）。

    顺序：文件名 stem > 属性词 > 目录段（最近的）
    （`cc_preview/avatar/ling.png` → ling；`characters/avatar/lan.png`
    的 Tag 列写着 avatar、Attributes 写着 lan → lan）。认不出 = ''。
    """
    stem = _stem_of(rel)
    if stem and stem.lower() in tag_map:
        return tag_map[stem.lower()]
    for word in attrs or ():
        if str(word).strip().lower() in tag_map:
            return tag_map[str(word).strip().lower()]
    for seg in reversed(_dirs_of(rel)):
        if seg in tag_map:
            return tag_map[seg]
    return ''


# ---------------------------------------------------------------------------
#  预览图/头像 → 真立绘
# ---------------------------------------------------------------------------

def _pixels_of(row: Mapping, dimensions) -> int:
    """这一行的像素数（`dimensions` 给了才比；认不出 = 0）。"""
    if not isinstance(row, Mapping):
        return 0
    rid = asset_id_of(row)
    rel = _row_relpath(row)
    raw = str(row.get('Path') or '').strip()
    candidates = [rid, rel, raw, PurePosixPath(rel).name if rel else '']
    for key in candidates:
        if not key or not isinstance(dimensions, Mapping):
            continue
        value = dimensions.get(key)
        if value is None:
            continue
        if isinstance(value, Mapping):
            try:
                return int(value.get('width') or value.get('w') or 0) * \
                    int(value.get('height') or value.get('h') or 0)
            except (TypeError, ValueError):
                continue
        if isinstance(value, (tuple, list)) and len(value) >= 2:
            try:
                return int(value[0]) * int(value[1])
            except (TypeError, ValueError):
                continue
    for wk, hk in (('Width', 'Height'), ('width', 'height'),
                   ('W', 'H'), ('w', 'h')):
        try:
            w, h = int(row.get(wk) or 0), int(row.get(hk) or 0)
        except (TypeError, ValueError):
            continue
        if w and h:
            return w * h
    return 0


def find_real_sprite(identity: Identity, rows, *,
                     dimensions=None) -> Optional[dict]:
    """
    预览图 / 头像 → **同一 tag 的真立绘行**（能直接 `show` 的那一行）。

    契约（§1.1）：优先 Tag 非空 → 属性对得上 → 像素更大（`dimensions` 给了才比）。

    认不出 / 没有候选 → `None`（调用方据此**拒绝**并给一句话，
    绝不拿预览图的名字硬写一条 `show`）。
    """
    if identity is None or identity.kind not in ('preview', 'avatar'):
        return None
    tag = str(identity.tag or '').strip()
    if not tag:
        return None
    want = [str(a).strip().lower() for a in identity.attrs if str(a).strip()]
    want_set = set(want)
    # 预览图要的是**同一个表情**：属性对不上的行不能当"对应的真立绘"
    # （否则会拿一个不存在的表情名去撞另一张脸）；头像没有属性，任何同 tag
    # 的真立绘都行。
    require_attr = (identity.kind == 'preview' and bool(want_set))
    best = None
    best_key = None
    for row in _iter_rows(rows):
        row_tag = str(row.get('Tag') or '').strip()
        if not row_tag or row_tag.lower() != tag.lower():
            continue
        words = {str(w).strip().lower() for w in row_values(row) if str(w).strip()}
        stem = _stem_of(str(row.get('Path') or row.get('ID') or '')).lower()
        aid = asset_id_of(row).lower()
        prefix = tag.lower() + '_'
        stripped = (aid[len(prefix):] if aid.startswith(prefix)
                    and len(aid) > len(prefix) else '')
        # 同一张脸可能有多种登记写法：真行的 `Attributes` 偶尔写成
        # `ling_happy`（连 ID 一起），而预览行 stem 只有 `happy` ——
        # 把 ID 去掉 tag 前缀后的部分也算一个可对应名字（仍是字面量）。
        tokens = set(words) | {stem, aid}
        if stripped:
            tokens.add(stripped)
        exact = 1 if want_set and (tokens & want_set) else 0
        hit = len(tokens & want_set)
        if require_attr and not exact and not hit:
            continue
        pixels = _pixels_of(row, dimensions)
        # 排序：文件名/属性**精确命中** > 命中词多 > 像素大 > 属性少 > ID 稳定
        key = (-exact, -hit, -pixels, len(words), aid or stem)
        if best_key is None or key < best_key:
            best, best_key = row, key
    return best


# ---------------------------------------------------------------------------
#  spec / 展示
# ---------------------------------------------------------------------------

def spec_for_identity(identity: Identity, row: dict | None = None) -> str:
    """
    `Identity` → 写回卡片的那段 **image spec**（`ling happy` / `bg rooftop`）。

    · 有 tag → `tag 属性…`（属性保序、去重、不重复 tag 本身）；
    · 没 tag → 退回资产 ID（宁可写 ID，也不凭空编一个 tag）；
    · 什么都没有 → ''。
    """
    if identity is None:
        return asset_id_of(row)
    tag = str(identity.tag or '').strip()
    if not tag:
        return asset_id_of(row)
    parts = [tag]
    seen = {tag.lower()}
    for word in identity.attrs or ():
        word = str(word or '').strip()
        if word and word.lower() not in seen:
            seen.add(word.lower())
            parts.append(word)
    return ' '.join(parts)


def group_key_for_identity(identity: Identity) -> str:
    """
    选择器分类名（**角色优先**）：同一角色的所有表情归一组。

    `立绘：<显示名>（<tag>）`（没有显示名退 `立绘：<tag>`）；
    其余类别各自一组（预览图/头像 **不能上舞台**，所以名字里写明）。
    """
    if identity is None:
        return '未识别（不猜）'
    kind = identity.kind
    if kind == 'sprite':
        tag = str(identity.tag or '').strip()
        if not tag:
            return '立绘：未识别'
        display = _clean_display(identity.display)
        return f'立绘：{display}（{tag}）' if display else f'立绘：{tag}'
    return {
        'preview': '角色预览图（不能直接上舞台）',
        'avatar': '角色头像（不能直接上舞台）',
        'bg': '背景',
        'cg': 'CG',
        'ui': '界面/其他图层',
        'other': '其他图片',
        'unknown': '未识别（不猜）',
    }.get(kind, '未识别（不猜）')


#: 非立绘图片分组的展示顺序（立绘组永远在最前，其余按这张表）。
GROUP_ORDER = (
    '背景', 'CG', '界面/其他图层',
    '角色预览图（不能直接上舞台）', '角色头像（不能直接上舞台）',
    '其他图片', '未识别（不猜）',
)


__all__ = [
    'KINDS', 'SHOWABLE_KINDS', 'SOURCES', 'GROUP_ORDER', 'Identity',
    'normalize_relpath', 'asset_id_of', 'kind_from_tag_word',
    'known_characters', 'known_tags_of', 'engine_fact_tags',
    'build_copy_index', 'path_kind', 'resolve_image', 'resolve_images',
    'project_context', 'find_real_sprite', 'spec_for_identity',
    'group_key_for_identity',
]
