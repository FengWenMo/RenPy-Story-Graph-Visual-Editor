# -*- coding: utf-8 -*-
"""
身份词汇的**唯一真源**：`KINDS` / `Identity` / 路径归一 / 基础行读取。

为什么单独一个模块（2026-11-01）：`asset_identity`（推断）与
`asset_mapping`（映射）互相 import 久了，`mapping_excel` 又两边都指 ——
三个模块绕成一圈（`debt_report.py --cycles` 里那个 `[3]`）。
把"两边都要、谁都不依赖对方"的这层基础词汇挪到叶子之后，指向关系是：

    asset_identity ─┐
                    ├→ identity_core
    asset_mapping ──┘

`asset_identity` 继续再导出这些名字（`Identity` / `KINDS` 的 140 多处
`from storygraph.assets.asset_identity import ...` 一行都不用改）。

**禁止**在这里 import `asset_identity` / `asset_mapping` / `mapping_excel` /
任何 UI 框架（它是叶子）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping

from storygraph.core.safeguard import swallow
from storygraph.assets.asset_dimensions import row_values

#: 身份类别（唯一真源；界面/分组/映射都按它分流）。
KINDS = ('sprite', 'preview', 'avatar', 'bg', 'cg', 'ui', 'other', 'unknown')

#: 能直接写进 `show <spec>` 的类别（其余一律先换真图或拒绝）。
SHOWABLE_KINDS = frozenset({'sprite', 'bg', 'cg', 'ui'})


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
    #: 覆盖留痕（append-only；`core.authority.Override`）。P0-3：
    #: `source` 只回答"赢家是谁"，这里回答"原来是什么、被谁盖的"。
    overrides: tuple = ()

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
        object.__setattr__(self, 'overrides', tuple(self.overrides or ()))

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
                    swallow('assets.identity_core.normalize_relpath', e)
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


def _stem_of(relpath: str) -> str:
    """项目相对路径 / 资产 ID → 文件名 stem（`.../ling/happy.png` → `happy`）。"""
    text = str(relpath or '').strip().replace('\\', '/')
    if not text:
        return ''
    return PurePosixPath(text).stem


def asset_id_of(row: Mapping | None) -> str:
    """资产行 → 资产 ID（`ID` 列；没有就空串）。"""
    return str((row or {}).get('ID') or '').strip()


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
