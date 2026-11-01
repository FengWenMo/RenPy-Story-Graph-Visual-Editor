# -*- coding: utf-8 -*-
"""
**项目形状**探测：声明放哪、控制流放哪、哪些文件是内容 —— 纯只读。

## 为什么要这一层（2026-10-23，G0 的另一半）

工具能"用多深"，取决于用户怎么摆文件：

| 用户的摆法 | 工具该怎么做 |
|---|---|
| 声明独立、控制流独立、章末 return | 全功能：能接线、能包 `_control` |
| 声明混在章节里、跳转内联 | 图只能看；改动归卡片 |
| 三件套挤在一个文件（原型阶段） | 别新建文件，别假装有编排层 |

这些是**事实**，不是策略。探测出来、给人看一眼、记进清单，之后按清单分派 ——
而不是每次打开项目都问一遍"你想怎么组织"。七个形状样本在
`tests/fixtures/placement/`，每个带 `expected.json`。

## 判据（每条都可解释，没有"看着像"）

1. **声明**：顶层 `default 名字 =` / `define 名字 =`。
   `define gui.x` / `define config.x` **不算** —— 那是 Ren'Py 框架配置，
   不是剧情变量（不排除的话，任何项目的 `gui.rpy` 都会被当成声明文件）。
2. **内容文件**：至少有一个 label 含演出语句（say / scene / show / hide / play /
   voice / menu / raw）。`raw` 算演出，因为 `pause` / `with` / `window hide` 是演出。
3. **独立控制流文件**：所有 label 都**没有演出语句**，且至少有一条 `jump` / `call`。
   名字叫不叫 `_control` 无所谓（`flow.rpy`、`剧情控制.rpy` 一样认）。
4. **单文件项目**：如果只有一个文件，它自己在做**顶层编排跳转**
   （不在 menu 选项体里的 jump/call）→ `control_flow` 指向它自己，
   形状记 `single_file`；没有顶层编排跳转 → `control_flow` 为空，
   形状记 `dedicated`（"分散"至少要两个文件才谈得上）。
5. **content_globs**：单文件 → 精确路径；多文件 → 按目录分组、去掉文件名尾部
   数字后求共同词干（`chapter1_1` / `chapter1_2` → `chapter*`）。
   ⚠ 这是**能覆盖全部内容文件的最短模式**，不保证不误伤（`chapter*.rpy`
   也会匹到 `chapter_controller.rpy`）—— 所以它只用于"大致范围"，
   精确清单看 `content_files`。

## 不做什么

- 不猜"这个文件是干什么的"：只按语句构成判；
- 不改文件；不自动建清单（写完由调用方决定要不要落 `storygraph.rules.json`）。
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.story import scan_cache
# `FileFacts` / `scan_file` 搬到了叶子模块（2026-11-01，技术债：解
# `placement ↔ scan_cache` 的导入环）。这里 re-export，老调用点与 `__all__` 不动。
from storygraph.story.file_facts import (  # noqa: F401 - re-export
    PERFORM_KINDS, RE_DECL, FileFacts, scan_file)

# ---------------------------------------------------------------------------
#  常量
# ---------------------------------------------------------------------------

DECL_DEDICATED = 'dedicated'
DECL_MIXED = 'mixed_with_content'
DECL_SINGLE = 'single_file'


# ---------------------------------------------------------------------------
#  文件级事实
# ---------------------------------------------------------------------------
#
# `FileFacts` / `scan_file` / `PERFORM_KINDS` / `RE_DECL` 都在
# `storygraph.story.file_facts`（叶子）：`scan_cache` 也要用它们，放在这里
# 会和 `placement → scan_cache` 那条边合成环。本模块顶部已 re-export。


# ---------------------------------------------------------------------------
#  扫描
# ---------------------------------------------------------------------------
#
# `scan_file` 在 `storygraph.story.file_facts`（叶子，见上面的说明）。


# ---------------------------------------------------------------------------
#  形状推导
# ---------------------------------------------------------------------------

def _strip_tail_number(stem: str) -> str:
    """
    `chapter1_1` → `chapter`、`act2` → `act`、`第一章` → `第`、`第1章` → `第`。

    为什么要去掉编号：同一批章节常常只差编号（`chapter1_1` / `chapter1_2`），
    共同词干才是"这一类文件"的名字。

    数字**不只在尾部**：中文是"第 + 一 + 章"，编号夹在中间，所以按全局去掉
    `数字（可带量词）`，而不是只削尾巴。去完为空就退回原名 —— 否则 `1.rpy`
    会变成 `*.rpy`，那是把整个目录都算成内容文件。
    """
    s = re.sub(r'[_\-\s]*[0-9]+', '', stem)
    s = re.sub(r'[一二三四五六七八九十百零]+[章节回幕场]?', '', s)
    s = s.strip('_- ')
    return s or stem


def _globs_for(content_files) -> list:
    """内容文件 → 能覆盖它们的最短模式（单文件就用精确路径）。"""
    if not content_files:
        return []
    by_dir = {}
    for rel in content_files:
        by_dir.setdefault(os.path.dirname(rel), []).append(rel)

    out = []
    for directory, items in sorted(by_dir.items()):
        if len(items) == 1:
            out.append(items[0])
            continue
        stems = {_strip_tail_number(os.path.splitext(os.path.basename(x))[0])
                 for x in items}
        if len(stems) == 1:
            stem = stems.pop()
            out.append(f'{directory}/{stem}*.rpy' if directory else f'{stem}*.rpy')
        else:
            # 词干不一致（比如 `a.rpy` + `b.rpy`）→ 逐个精确列，
            # 不要编一个会误伤的 `*.rpy`
            out.extend(sorted(items))
    return out


@dataclass
class Placement:
    """一个项目的形状。前四个键与 `tests/fixtures/placement/*/expected.json` 对齐。"""

    #: 声明落在哪：单个文件 → 字符串；多个 → 列表（排序）。没有 → `''`
    declarations: object = ''
    #: 独立控制流文件；没有独立控制流 → `''`（内联在内容里）
    control_flow: str = ''
    #: 内容文件的大致模式（能覆盖全部内容文件的最短模式）
    content_globs: list = field(default_factory=list)
    #: `dedicated` / `mixed_with_content` / `single_file`
    declaration_style: str = ''

    # ---------------- 比约定更细的事实（工具内部用） ----------------

    #: 精确的内容文件清单
    content_files: list = field(default_factory=list)
    #: 有顶层编排跳转的文件（控制流混在内容里 → 图只能读）
    inline_flow_files: list = field(default_factory=list)
    #: 每个结论的依据（报告里可以直接显示"我凭什么这么说"）
    evidence: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'declarations': self.declarations,
            'control_flow': self.control_flow,
            'content_globs': list(self.content_globs),
            'declaration_style': self.declaration_style,
            'content_files': list(self.content_files),
            'inline_flow_files': list(self.inline_flow_files),
            'evidence': dict(self.evidence),
            'warnings': list(self.warnings),
        }

    # ---------------- 人话 ----------------

    def summary(self) -> str:
        """一句话说清"你是哪种项目"。界面/报告直接用。"""
        decl = self.declarations
        files = list(self.evidence.get('declaration_files') or [])
        content = set(self.content_files)
        in_content = [f for f in files if f in content]

        if self.declaration_style == DECL_SINGLE:
            return (f'单文件原型：声明、内容、编排都在同一个文件里'
                    f'（{decl}）—— 工具不会给你新建文件')

        flow = self.control_flow or '没有独立控制流（跳转写在内容里）'
        if self.declaration_style == DECL_MIXED:
            decl_text = (f'声明散在各处：{len(files)} 个文件有声明，'
                         f'其中 {len(in_content)} 个就是章节本身')
        elif isinstance(decl, list):
            decl_text = f'声明分在 {len(decl)} 个文件里'
        elif decl:
            decl_text = f'声明集中在 {decl}'
        else:
            decl_text = '没找到声明'
        return f'{decl_text} · 控制流：{flow} · {len(self.content_files)} 个内容文件'


def detect_placement(project_root, files=None) -> Placement:
    """
    探测项目形状。**只读，不写盘，不建文件。**

    `files` 传了就用它（测试/局部），否则用 `discover_scripts` 发现的全部脚本。

    ## 为什么套了两层缓存（2026-10-26）

    真机上这一步要**整份解析每个 `.rpy`**（`sr_general` 91 个文件 1.4 秒），
    而接线窗口一次操作会问它两遍以上（`find_control_file` 里还要再问）。
    结论只由"文件内容"决定，所以：

      · `scan_cache.file_facts` 按 `(mtime_ns, size)` 缓存单个文件的解析结果 ——
        改过的文件才重新解析；
      · 整个 `Placement` 按项目指纹缓存 —— 文件一个没变就直接给上一次的结论。

    返回的是拷贝：调用方改自己的 `.warnings` / 列表不影响缓存。
    """
    root = Path(project_root)
    if files is None:
        paths = [Path(p) for p in scan_cache.discover(root)]
    else:
        paths = [Path(p) for p in files]
    fp = scan_cache.fingerprint(root, paths)
    placement = scan_cache.memo(
        'detect_placement', root, len(paths), fp,
        lambda: _build_placement(root, paths))
    return _copy_placement(placement)


def _copy_placement(placement: Placement) -> Placement:
    """`Placement` 里几个 list/dict —— 交出去之前拷一份，别让调用方改到缓存。"""
    try:
        decl = placement.declarations
        return replace(placement,
                       declarations=(list(decl) if isinstance(decl, list)
                                     else decl),
                       content_globs=list(placement.content_globs or ()),
                       content_files=list(placement.content_files or ()),
                       inline_flow_files=list(placement.inline_flow_files or ()),
                       evidence=dict(placement.evidence or {}),
                       warnings=list(placement.warnings or ()))
    except Exception as exc:                     # noqa: BLE001 - 拷不动就原样给
        swallow('story.placement._copy_placement', exc)
        return placement


def _build_placement(root: Path, paths) -> Placement:
    """真正的探测（`detect_placement` 的纯计算部分，结果会被缓存）。"""
    facts = []
    for path in paths:
        try:
            facts.append(scan_cache.file_facts(path, root))
        except Exception as exc:                      # noqa: BLE001
            facts.append(FileFacts(rel=str(path), abs_path=str(path)))
            _ = exc

    decl_files = sorted(f.rel for f in facts if f.has_decl)
    content_files = sorted(f.rel for f in facts if f.is_content)
    control_only = sorted(f.rel for f in facts if f.is_control_only)
    inline_flow = sorted(f.rel for f in facts if f.top_flow_stmts > 0)

    placement = Placement(
        content_files=content_files,
        inline_flow_files=inline_flow,
        content_globs=_globs_for(content_files),
    )

    # ---- 声明 ----
    if len(decl_files) == 1:
        placement.declarations = decl_files[0]
    elif decl_files:
        placement.declarations = decl_files
    else:
        placement.declarations = ''
        placement.warnings.append('没找到任何 default / define 声明')

    # ---- 控制流 ----
    # 多文件项目：只认"纯控制流文件"（全文件没有演出语句）；有多个取第一个。
    # 单文件项目：它自己在做顶层编排跳转 → 落点重合，记它自己。
    if control_only:
        placement.control_flow = control_only[0]
        if len(control_only) > 1:
            placement.warnings.append(
                '有多个文件都像控制流：' + '、'.join(control_only)
                + f'；先按第一个（{control_only[0]}）算')
    elif len(facts) == 1 and facts[0].top_flow_stmts > 0:
        placement.control_flow = facts[0].rel
    else:
        placement.control_flow = ''

    # ---- 形状 ----
    decl_set = set(decl_files)
    content_set = set(content_files)
    if (len(facts) == 1 and placement.control_flow
            and decl_set == content_set == {placement.control_flow}):
        placement.declaration_style = DECL_SINGLE
    elif decl_set & content_set and len(content_files) > 1:
        placement.declaration_style = DECL_MIXED
    else:
        # 只有一个内容文件时"分散"无从谈起 —— 记 dedicated（官方模板 `script.rpy`
        # 就是这种：声明和剧情同文件，但它只有一个文件）
        placement.declaration_style = DECL_DEDICATED

    placement.evidence = {
        'declaration_files': decl_files,
        'content_files': content_files,
        'control_only_files': control_only,
        'inline_flow_files': inline_flow,
        'file_count': len(facts),
        'note': ('声明判据：顶层 default/define 且名字不带点号（排除 gui./config.）；'
                 '内容判据：label 里有演出语句；'
                 '控制流判据：全部 label 无演出且有 jump/call'),
    }
    return placement


def matches_globs(rel_path: str, globs) -> bool:
    """某个文件是否落在 `content_globs` 里（测试与界面勾选共用同一条判据）。"""
    p = rel_path.replace('\\', '/')
    return any(fnmatch.fnmatch(p, g) for g in (globs or []))


__all__ = [
    'PERFORM_KINDS', 'RE_DECL', 'DECL_DEDICATED', 'DECL_MIXED', 'DECL_SINGLE',
    'FileFacts', 'Placement', 'scan_file', 'detect_placement', 'matches_globs',
]
