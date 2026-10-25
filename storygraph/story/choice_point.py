# -*- coding: utf-8 -*-
"""
**选择点**：让策划在一章中间放一个分支，而剧本只多一行 `call`。

## 为什么选择点单独一个文件（2026-10-24，实测推翻定案）

定案里写的是"menu 塞进控制流文件"。**实测不成立**：

    往 `chapter_controller.rpy` 手加一个 `label ch01_choice_1:`，
    再点一次「导出控制流」→ 那个 label **消失了**。

原因在 `renpy_exporter.py`：控制流产物是 `'\n'.join(lines)` **整份重生成**，
`lines` 完全由 graph 推出来，不读目标文件里已有的内容。
它和剧本导出一样是"产物"，**不能当容器用**。

所以选择点住一个**工具独占、别人不碰**的文件：

    game/<章节目录>/<章节名>_choices.rpy     ← 只有本模块写

它能安全活下来，是因为两个导出器各有各的目标：
  · `export_control_flow` 只写 `*_control.rpy`
  · 剧本导出只写剧本文件
  · 这个文件两边都不碰

> 它仍然是"编排层"：不产台词、不产舞台，只有 `menu` + `call`。
> "menu 只有一个家 = 编排层"这条没变；变的是**编排层可以有多个文件**。

## 剧本侧只加一行

```renpy
label ch01:
    e "今天有件事想问你。"
    call ch01_choice_1 from _call_ch01_choice_1    ← 只多这一行
    e "算了，先上课吧。"
    return
```

## 不做什么

- 不改剧本文件（插的那一行进的是**内存里的拍**，落盘仍走「导出」）；
- 不猜选项文字（默认给占位，改字是人的事）；
- 不覆盖已有的选择点文件（追加，读-改-写，原子替换）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: 选择点文件的命名后缀（同目录、和章节区分开）。
CHOICES_SUFFIX = '_choices'

#: 默认占位选项。**不猜语义**：给两个中性的，改字由人来做。
DEFAULT_OPTIONS = ('选项一', '选项二')

#: 新文件的首行说明（`write_generation` 还会自己加生成物标记）。
FILE_BANNER = ('# 本章的选择点（由 StoryGraph 生成；改这里不会影响剧本正文）')


def _label_re(name: str) -> str:
    return rf'^label\s+{re.escape(name)}\s*(\(|:)'

def used_labels(project_root) -> set:
    """
    项目里**已经被占用**的 label 名（跨文件）。

    为什么要扫全项目：`label` 在 Ren'Py 里是全局命名空间，
    两个文件里各有一个同名 label 会让引擎直接报错。
    """
    from storygraph.parsing.discover import labels_in_file, discover_scripts

    out = set()
    try:
        for path in discover_scripts(Path(project_root)):
            out.update(str(x) for x in labels_in_file(path))
    except Exception as e:                            # noqa: BLE001 - 扫不出来就少避开
        from storygraph.core.safeguard import swallow
        swallow('story.choice_point.used_labels', e)
        return out
    return out


def choices_path_for(chapter_path) -> Path:
    """`game/chapter/ch01.rpy` → `game/chapter/ch01_choices.rpy`（同目录）。"""
    p = Path(chapter_path)
    return p.with_name(f'{p.stem}{CHOICES_SUFFIX}.rpy')


def _unique_label(base: str, taken: set) -> str:
    """`ch01_choice_1` / `_2` / … 挑第一个没被占用的。"""
    n = 1
    while True:
        name = f'{base}_{n}'
        if name not in taken:
            return name
        n += 1


def build_menu_block(label: str, options, notes: str = '') -> str:
    """
    一个选择点的**文件片段**（不含别的 label）。

    选项体里放 `pass`：Ren'Py 允许空分支，而"空着等人填"比"先猜一个 jump"
    安全得多 —— 猜错了就是静默改剧情。
    """
    lines = []
    if notes:
        lines.append(f'# {notes}')
    lines.append(f'label {label}:')
    lines.append('    menu:')
    opts = [str(o).strip() for o in (options or ()) if str(o).strip()]
    for text in opts or DEFAULT_OPTIONS:
        safe = text.replace('\\', '\\\\').replace('"', '\\"')
        lines.append(f'        "{safe}":')
        lines.append('            pass')
    lines.append('    return')
    return '\n'.join(lines) + '\n'


@dataclass
class ChoicePointPlan:
    """一个选择点**会写成什么** —— 纯计算，不碰磁盘。"""

    ok: bool = False
    #: 新 label 名（`ch01_choice_1`）
    label: str = ''
    #: 写哪个文件
    path: str = ''
    #: 文件里要追加的片段（已有的内容不动）
    block: str = ''
    #: 剧本里要插的那一行
    call_line: str = ''
    #: `from` 子句（存档返回点）
    from_clause: str = ''
    #: 这个文件是新建还是追加
    creates_file: bool = True
    #: 不能做的原因（空 = 能做）
    blocking: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'ok': self.ok, 'label': self.label, 'path': self.path,
            'block': self.block, 'call_line': self.call_line,
            'from_clause': self.from_clause,
            'creates_file': self.creates_file,
            'blocking': list(self.blocking), 'notes': list(self.notes),
        }


def plan_choice_point(project_root, chapter_path, chapter_label,
                      options=None, notes: str = '') -> ChoicePointPlan:
    """
    算出一个选择点要写什么。**不落盘、不改模型。**

    参数：
      · `chapter_path`：剧本文件（决定选择点文件放哪个目录）；
      · `chapter_label`：在哪一章的开头挂名字（用来生成 `ch01_choice_1`）；
      · `options`：选项文字；不给就用中性占位。

    会被拦住（`ok=False`）的情况：
      · 章节文件不存在；
      · 章节 label 名不合法（没法拼出合法的 Ren'Py label）。
    """
    plan = ChoicePointPlan()
    root = Path(str(project_root or '.'))
    ch = Path(str(chapter_path or ''))
    label_root = str(chapter_label or '').strip()

    if not ch.is_file():
        plan.blocking.append(f'章节文件不存在：{ch}')
        return plan
    if not label_root:
        plan.blocking.append('这一章还没有 label 名，没法生成选择点')
        return plan
    if not re.fullmatch(r'[A-Za-z_][\w]*', label_root):
        # 中文 label 在 Ren'Py 里其实合法，但拼 `_choice_N` 之后要能过 lint；
        # 保守起见只允许 ASCII 起始 —— 不行就让用户先改 label 名。
        plan.blocking.append(
            f'章节 label「{label_root}」不适合当选择点前缀（要字母或下划线开头）')
        return plan

    taken = used_labels(root)
    plan.label = _unique_label(f'{label_root}_choice', taken)
    plan.path = str(choices_path_for(ch))
    plan.creates_file = not Path(plan.path).exists()
    plan.block = build_menu_block(plan.label, options, notes)
    plan.from_clause = f'_call_{plan.label}'
    plan.call_line = f'call {plan.label} from {plan.from_clause}'
    plan.ok = True
    plan.notes.append(
        f'选择点写在 {Path(plan.path).name}（工具独占；导出控制流/导出剧本都不会碰它）')
    return plan


def apply_choice_point(plan: ChoicePointPlan, project_root=None) -> dict:
    """
    落盘：把选择点片段写进 `plan.path`（新建或**追加**）。

    返回 `{ok, path, created, error}`。

    ⚠ 这里**不碰剧本**：剧本那一行由调用方插进"拍"（`insert_flow_beat`），
    落盘仍走导出。理由与 S1 一致 —— 剧本的写入口只有一处。

    ⚠ 追加走 `write_text_atomic`（读-改-写、原子替换）：
    已存在的选择点文件里可能有人手写的东西，**不许整份重生成**。
    """
    if not plan or not plan.ok:
        return {'ok': False, 'path': '', 'created': False,
                'error': '；'.join((plan.blocking if plan else [])) or '计划无效'}

    target = Path(plan.path)
    if plan.creates_file or not target.exists():
        from storygraph.story.generation_guard import (ensure_generator_marker,
                                                       write_generation)
        # **新文件必须带生成标记**（2026-10-25，审计 P1-3）：
        # `FILE_BANNER` 只是一句人话，不含 `GENERATOR_MARKER` ——
        # 于是这个文件在护栏眼里是"手写的"。今天没有流程会重写它，
        # 但将来任何"重算选择点"走 `write_generation`，第一件事就是被
        # `HANDWRITTEN_TARGET` 拒写（与 `control_takeover` 同一套做法）。
        text = ensure_generator_marker(f'{FILE_BANNER}\n\n{plan.block}')
        try:
            art = write_generation(text, target, project_root=project_root)
        except Exception as exc:                     # noqa: BLE001
            return {'ok': False, 'path': str(target), 'created': False,
                    'error': str(exc)}
        written = bool(getattr(art, 'written', True))
        if not written:
            why = '；'.join(i.format() for i in
                            (getattr(art, 'blocking', None) or [])[:3])
            return {'ok': False, 'path': str(target), 'created': False,
                    'error': f'门禁拦下了这份选择点文件（{why or "见产物"}）'}
        return {'ok': True, 'path': str(target), 'created': True, 'error': ''}

    # —— 追加：按**原文件的载体风格**接一段（2026-10-25，审计 P0-1）——
    #
    # 从前是 `target.read_text(encoding='utf-8')` + `write_text_atomic(...)`：
    # 裸读会把 CRLF 折成 LF、裸写就是 LF —— **整份文件的换行被静默改写**，
    # 而用户只加了一个选择点。Windows 中文项目里 CRLF 是常态，
    # `source_style` 的模块 docstring 记着同一类事故（tutorial 23/23 带 CRLF，
    # 工具写出来是 LF，逐字节相同率 0/23）；BOM 同理会被丢掉。
    # 追加路径是那一轮**漏网的一处**，现在与 `control_route` / `patcher`
    # 走同一套：读时记住风格、写前 `style.apply` 还原。
    #
    # ⚠ 这里**不用** `source_style.read_text`（它 `errors='replace'`）：
    # 追加是"读回来再写回去"，坏字节被替换成 `\ufffd` 就是**改坏用户文件**。
    # 所以严格 UTF-8 解码，解不开就报错（见审计 P1-2 的第二条路）。
    from storygraph.story.paths import write_text_atomic
    from storygraph.story.source_style import detect as _detect_style
    try:
        raw_bytes = target.read_bytes()
        old = raw_bytes.decode('utf-8')
    except UnicodeDecodeError as exc:
        return {'ok': False, 'path': str(target), 'created': False,
                'error': f'这个选择点文件不是 UTF-8（Ren\'Py 只认 UTF-8）：'
                         f'{target}（第 {exc.start} 字节）。'
                         f'先另存为 UTF-8 再插入。'}
    except OSError as exc:
        return {'ok': False, 'path': str(target), 'created': False,
                'error': f'读不了已有的选择点文件：{exc}'}
    style = _detect_style(target)
    if old.startswith('\ufeff'):
        old = old[1:]
    old = old.replace('\r\n', '\n').replace('\r', '\n')
    sep = '' if old.endswith('\n\n') else ('\n' if old.endswith('\n') else '\n\n')
    merged = old + sep + plan.block.replace('\r\n', '\n')
    try:
        write_text_atomic(target, style.apply(merged))
    except OSError as exc:
        return {'ok': False, 'path': str(target), 'created': False,
                'error': f'写不进去：{exc}'}
    return {'ok': True, 'path': str(target), 'created': False, 'error': ''}


__all__ = [
    'CHOICES_SUFFIX', 'DEFAULT_OPTIONS', 'FILE_BANNER',
    'ChoicePointPlan', 'apply_choice_point', 'build_menu_block',
    'choices_path_for', 'plan_choice_point', 'used_labels',
]
