# -*- coding: utf-8 -*-
"""
导出控制流之前的**守门**：这份产物会不会把文件里已有的章冲掉？

## 为什么必须有这一层（2026-10-24，实测到的 P0）

`export_to_renpy` 是**按图整份重生成**：`lines` 全由图推出来，
然后 `text = '\\n'.join(lines)` 覆盖目标文件。

于是这个顺序会静默丢东西：

    第 1 天：导入 ch01/ch02/ch03 → 导出 → 文件里有三块 `_control` ✅
    第 2 天：只导入 ch01/ch02 → 再点导出 → **ch03_control 消失**，全程无警告
            （`tests/test_control_write.py` 直接复现这件事）

用户完全可能只导了两章就点一下导出（甚至只是"看看效果"）。这不是"误操作"，
是产品的默认路径太容易踩到 —— 所以要在写盘前拦住，并且**说清缺哪几章**。

## 判据（三条，都可解释）

1. 目标文件还不存在 → 可以写（新建）；
2. 文件里有 `X_control`，但图里既没有 `X` 也没有 `X_control` → **拒绝**，列出缺的；
3. 文件里有**不叫 `*_control` 的 label** → **拒绝**：那说明有人往里手写过东西，
   整份重生成会把它们抹掉（和 S2 那个实验同源）。

## 不做什么

- 不替你补节点（缺的章要让用户先导入，工具不猜内容）；
- 不自动备份（`write_generation` 被拦时会留 `.blocked.rpy`，那是另一条路）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: 生成物 label 的后缀（`export_to_renpy` 的 `control_name`）。
CONTROL_SUFFIX = '_control'

#: 扫 label 用的正则 —— **故意比 `discover._LABEL_RE` 宽**：这里要能认出
#: 中文 label（真机 `第一章` 就是），因为目的是"这个文件里到底有什么"，
#: 不是"哪些文件算章节"。两处用途不同，不是两套判据。
_RE_LABEL = re.compile(r'^\s*label\s+([^\s:(#]+)', re.MULTILINE)
#: 行首 label（解析"每一块归属哪个 label"用；必须从第 0 列开始）
_RE_LABEL_LINE = re.compile(r'^label\s+([^\s:(#]+)\s*:')


def _label_blocks(text: str) -> dict:
    """
    `label X:` → 它的块体行 `[(1-based 行号, 原文), …]`。

    缩进为界：遇到下一行顶格的非空行就结束当前块（label / init / define…）。
    """
    out: dict = {}
    cur = None
    for lineno, raw in enumerate(str(text or '').splitlines(), 1):
        m = _RE_LABEL_LINE.match(raw)
        if m:
            cur = m.group(1).strip()
            out.setdefault(cur, [])
            continue
        if cur is None:
            continue
        if raw[:1] not in ('', ' ', '\t'):
            cur = None
            continue
        if raw.strip():
            out[cur].append((lineno, raw))
    return out


def _is_structural(line: str) -> bool:
    """
    这一行是**控制流脚本的结构形状**（生成器会写的）吗？

    只回答"它像不像跳转/菜单/条件/注释" —— 像就放过，不像再由"生成文本里
    有没有同一行"来决定是不是手写内容。**不是**白名单：`$` / `show` /
    `play` 这类操作行故意不在这里（它们既可能是图里的操作，也可能是人加的）。
    """
    s = str(line or '').strip()
    if not s or s.startswith('#'):
        return True
    if s in ('return', 'pass', 'else:'):
        return True
    if s.startswith(('call ', 'jump ')):
        return True
    if s.startswith(('if ', 'elif ')) and s.endswith(':'):
        return True
    if s == 'menu:' or (s.startswith('menu ') and s.endswith(':')):
        return True
    # 菜单选项行：引号包住文案，后面可跟 ` if 条件`，以冒号结尾
    if s[0] in ('"', "'"):
        q, i = s[0], 1
        while i < len(s):
            if s[i] == '\\':
                i += 2
                continue
            if s[i] == q:
                break
            i += 1
        tail = s[i + 1:].strip() if i < len(s) else ''
        return tail == ':' or (tail.startswith('if ') and tail.endswith(':'))
    return False


def find_manual_lines(text: str, generated_text: str) -> list:
    """
    目标文件里**不像工具生成**的行（`_control` 块内）。

    判据两条：
      · 形状不是跳转/菜单/条件/注释（见 `_is_structural`）；
      · 这次将要生成的同名 label 块里**没有这一行**（逐字、去首尾空白）。
        这条把"图里本来就有的操作行"排除掉（控制流图模式下 `show` /
        `play` / `$` 也是生成的），留下的就是人手加进去的。

    返回 `[{'label', 'line', 'text'}, …]`（行号是 1-based，给人看）。
    """
    old_blocks = _label_blocks(text)
    new_blocks = _label_blocks(generated_text)
    found = []
    for name, rows in old_blocks.items():
        if not name.endswith(CONTROL_SUFFIX):
            continue
        if name not in new_blocks:
            # 这一块这次根本不会生成（缺章 / 模式不同）：由 `missing_chapters`
            # 那条判据说话，别在这里重复报一遍。
            continue
        allowed = {ln.strip() for _n, ln in new_blocks[name] if ln.strip()}
        for lineno, raw in rows:
            s = raw.strip()
            if not s or _is_structural(s) or s in allowed:
                continue
            found.append({'label': name, 'line': lineno, 'text': s})
    return found


def _labels_in(path: Path) -> list:
    """文件里所有 label（按出现顺序，去重；支持中文）。"""
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return []
    out = []
    for m in _RE_LABEL.finditer(text):
        name = m.group(1).strip()
        if name and name not in out:
            out.append(name)
    return out


@dataclass
class ControlWritePlan:
    """导出控制流**会发生什么** —— 纯计算，不写盘。"""

    ok: bool = False
    target: str = ''
    creates_file: bool = True
    #: 目标文件里已有的 `_control` label
    existing_controls: list = field(default_factory=list)
    #: 目标文件里**不叫** `_control` 的 label（有人手写过的证据）
    foreign_labels: list = field(default_factory=list)
    #: 目标有、图里没有的章节（写下去会丢）
    missing_chapters: list = field(default_factory=list)
    #: 图里有、目标里还没有的章节（会被新增）
    new_chapters: list = field(default_factory=list)
    blocking: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    #: `_control` 块里**不像工具生成**的行（手写内容，整份重生成会删掉）
    manual_lines: list = field(default_factory=list)
    #: 只有"手写内容"这一条拦截时，允许用户显式确认后覆盖
    forceable: bool = False

    def to_dict(self) -> dict:
        return {
            'ok': self.ok, 'target': self.target,
            'creates_file': self.creates_file,
            'existing_controls': list(self.existing_controls),
            'foreign_labels': list(self.foreign_labels),
            'missing_chapters': list(self.missing_chapters),
            'new_chapters': list(self.new_chapters),
            'blocking': list(self.blocking),
            'notes': list(self.notes),
            'manual_lines': list(self.manual_lines),
            'forceable': self.forceable,
        }


def _chapter_of(label: str) -> str:
    """`ch01_control` → `ch01`；不以 `_control` 结尾就原样返回。"""
    name = str(label or '')
    if name.endswith(CONTROL_SUFFIX) and len(name) > len(CONTROL_SUFFIX):
        return name[:-len(CONTROL_SUFFIX)]
    return name


def plan_control_write(project_root, graph, target, wrapper=None) -> ControlWritePlan:
    """
    算一次"导出控制流"会不会丢东西。`graph` 是**当前要导出的图**。

    `wrapper` 与 `export_to_renpy` 同义（None = 按图自身模式）；必须传同一份，
    否则"将要生成的文本"和真正写下去的文本不是一份，手写内容会误判。
    """
    plan = ControlWritePlan(target=str(target))
    path = Path(str(target or ''))
    if not str(target or '').strip():
        plan.blocking.append('没给导出路径')
        return plan

    graph_labels = {str(getattr(n, 'label_name', '') or '')
                    for n in (getattr(graph, 'nodes', {}) or {}).values()}
    graph_labels.discard('')

    if not path.exists():
        plan.creates_file = True
        plan.ok = bool(graph_labels)
        if not graph_labels:
            plan.blocking.append('图是空的，没有可导出的章节')
        plan.notes.append('目标文件还不存在：这次是**新建**，不会覆盖任何东西')
        return plan

    plan.creates_file = False
    try:
        existing = _labels_in(path)
    except Exception as exc:                        # noqa: BLE001
        plan.blocking.append(f'读不了已有的控制流文件：{exc}')
        return plan

    plan.existing_controls = [x for x in existing if x.endswith(CONTROL_SUFFIX)]
    plan.foreign_labels = [x for x in existing if not x.endswith(CONTROL_SUFFIX)]

    chapters = [_chapter_of(x) for x in plan.existing_controls]
    # 图里可能是"章节图"（有 `X`），也可能是"控制流图"（有 `X_control`）——
    # 两种都算覆盖，别因为导出模式不同就误报。
    plan.missing_chapters = [
        c for c in chapters
        if c not in graph_labels and f'{c}{CONTROL_SUFFIX}' not in graph_labels
    ]
    plan.new_chapters = sorted(
        x for x in graph_labels
        if x not in chapters and not x.endswith(CONTROL_SUFFIX)
        and f'{x}{CONTROL_SUFFIX}' not in plan.existing_controls
    )

    # —— 第四条：同名 `_control` 块里有没有人手写的内容（P1-1）——
    #
    # 三条老判据只认 **label 名**：用户在 `ch01_control` 里加一句
    # `$ persistent.seen_ch1 = True`，名字对得上 → 放行 → 整份重生成，
    # 那行静默消失。文件头的"请勿手改"是告知，不是保护。
    # 判据见 `find_manual_lines`：形状 + "这次要生成的同名块里有没有这一行"。
    try:
        if not str(target or '').strip():
            raise ValueError('没有目标路径')
        from storygraph.pipeline.renpy_exporter import render_control_text
        generated = render_control_text(graph, wrapper=wrapper)
        plan.manual_lines = find_manual_lines(
            path.read_text(encoding='utf-8', errors='replace'), generated)
    except Exception as exc:                        # noqa: BLE001 - 算不出就别误拦
        plan.notes.append(f'手写内容检查跳过：{exc}')

    if plan.missing_chapters:
        plan.blocking.append(
            '图里没有这些章：' + '、'.join(plan.missing_chapters)
            + ' —— 写下去会把它们在这个文件里的控制流**整块抹掉**。'
              '先把这些章导入剧情图，或者换一个导出目标。')
    if plan.foreign_labels:
        plan.blocking.append(
            '这个文件里还有不叫 `*_control` 的 label：'
            + '、'.join(plan.foreign_labels)
            + ' —— 整份重生成会把它们删掉（那不是工具写的东西）。'
              '请把它们挪到别的文件，或换一个导出目标。')
    if plan.manual_lines:
        shown = plan.manual_lines[:5]
        preview = '；'.join(
            f"{m['label']} 第 {m['line']} 行 `{m['text']}`" for m in shown)
        more = (f' 等共 {len(plan.manual_lines)} 行'
                if len(plan.manual_lines) > len(shown) else '')
        plan.blocking.append(
            '`*_control` 块里有**不像工具生成**的内容：' + preview + more
            + ' —— 导出是整份重生成，这些行会被删掉。'
              '确认可以丢再明确选择覆盖。')

    # 只有"手写内容"这一条拦截时可以确认覆盖；缺章 / 外来 label 不行
    # （前者会丢别的章，后者会删整个手写文件）。
    plan.forceable = (bool(plan.manual_lines)
                      and not plan.missing_chapters
                      and not plan.foreign_labels)
    plan.ok = not plan.blocking
    if plan.ok:
        if plan.new_chapters:
            plan.notes.append('会新增：' + '、'.join(plan.new_chapters))
        if plan.existing_controls:
            plan.notes.append(
                f'会重写已有的 {len(plan.existing_controls)} 块控制流'
                '（章都在图里，内容按当前图重新生成）')
    return plan


__all__ = ['CONTROL_SUFFIX', 'ControlWritePlan', 'plan_control_write']
