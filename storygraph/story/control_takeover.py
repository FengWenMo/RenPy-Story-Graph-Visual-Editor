# -*- coding: utf-8 -*-
"""
**接管进入点**：让一章真的从编排层进入（`jump X` → `jump X_control`）。

## 为什么需要（2026-10-24）

编排层的形状是：

    label X_control:
        call X
        jump Y_control

可项目里原来的入口写的是 `jump X` —— 它**绕过**了 `X_control`。于是：

  · `X_control` 里那几条路由没人执行，是一条**死线**；
  · 图上"我已经把章末接好了"的线看起来成立，跑起来却不生效；
  · 分支（条件 / 菜单）写在 `X_control` 里也一样不会执行。

补一个没人跳进来的 `X_control` 不是"接管"，是加一条死代码。所以接管
必须**两件事一起做**：

  1. 在编排层里补 `label X_control: call X; return`（如果还没有）；
  2. 把全项目里**所有** `jump X` 改成 `jump X_control`。

`call X` **不改**：`X_control` 里的路由可能 `jump` 走，调用点就永远回不来。
这种入口只报告、不改写 —— 那要人决定"这个 call 点是不是也该换成 jump"。

## 多文件原子写 + diff 预览

改的可能是三五个文件（几章剧本 + 编排层）。这里先把每个文件的
`old_text → new_text` 都算出来、给一份 unified diff；真正落盘前**再读一遍**
所有文件，任何一个和计划时不同就整体拒绝。写盘过程中某个文件失败，会把
已经写过的文件按 `old_text` 回滚（尽力而为，并把回滚失败如实报出来）。

## 不做什么

- 不动 `call X`（理由见上）；
- 不动 `jump expression …` / `jump X_control`；
- 不猜 label 名：`X` 必须在项目里有 `label X` 声明；
- 不新建"没有入口"的包装：一个 `jump X` 都没找到时只给 note，不假装接管成功。
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.story.control_route import (control_label, find_control_file)

#: 行首 `jump <label>`（允许行尾注释；不认 `jump expression`）。
_RE_JUMP = re.compile(r'^(\s*)jump\s+([^\s#]+)(\s*#.*)?$')
_RE_LABEL = re.compile(r'^\s*label\s+([^\s:(#]+)', re.MULTILINE)


def default_control_path(project_root) -> Path:
    """项目还没有编排层时，新文件放哪：`game/chapter_controller.rpy`。"""
    root = Path(str(project_root or '.'))
    game = root / 'game'
    base = game if game.is_dir() else root
    return base / 'chapter_controller.rpy'


def _label_files(project_root) -> dict:
    """`{label: path}`（项目里每个 label 的声明文件；同名只记第一处）。"""
    from storygraph.parsing.discover import discover_scripts
    out = {}
    try:
        paths = discover_scripts(Path(str(project_root)))
    except Exception as e:                         # noqa: BLE001 - 扫不出来就空
        from storygraph.core.safeguard import swallow
        swallow('story.control_takeover._label_files.scan', e)
        return out
    for path in paths:
        try:
            # `utf-8-sig`：中文项目的首行常带 BOM，`^\s*label` 认不出 BOM。
            text = Path(path).read_text(encoding='utf-8-sig',
                                        errors='replace')
        except OSError:
            continue
        for m in _RE_LABEL.finditer(text):
            out.setdefault(m.group(1).strip(), Path(path))
    return out


@dataclass
class FileEdit:
    """一个文件要改什么（**纯数据**，不写盘）。"""

    path: str = ''
    old_text: str = ''
    new_text: str = ''
    #: 这个文件是本次接管**新建**的（撤销时删掉，不留一个空文件）
    creates: bool = False
    #: `[(行号, 旧行, 新行)]`，只给人看 / 测试
    changes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {'path': self.path, 'changes': list(self.changes),
                'creates': self.creates,
                'old_lines': len(self.old_text.splitlines()),
                'new_lines': len(self.new_text.splitlines())}


@dataclass
class TakeoverPlan:
    """一次"接管进入点"的**纯计划**。"""

    ok: bool = False
    control_path: str = ''
    creates_control: bool = False
    labels: list = field(default_factory=list)
    #: 新补的 `X_control`（按顺序）
    wrappers: list = field(default_factory=list)
    edits: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    blocking: list = field(default_factory=list)
    reason: str = ''
    diff: str = ''

    def to_dict(self) -> dict:
        return {
            'ok': self.ok, 'control_path': self.control_path,
            'creates_control': self.creates_control,
            'labels': list(self.labels), 'wrappers': list(self.wrappers),
            'edits': [e.to_dict() for e in self.edits],
            'notes': list(self.notes), 'blocking': list(self.blocking),
            'reason': self.reason, 'diff': self.diff,
        }


def _wrapper_block(label: str, indent: str = '    ') -> str:
    """`X_control` 包装的文本（语义等价：`jump X_control` → `call X` → return）。"""
    name = control_label(label)
    return f'label {name}:\n{indent}call {label}\n{indent}return\n'


def _append_wrapper(text: str, label: str) -> str:
    body = str(text or '')
    if body and not body.endswith('\n'):
        body += '\n'
    if body.strip():
        body += '\n'
    return body + _wrapper_block(label)


def _apply_jumps(text: str, labels: set):
    """把 `jump X` 改成 `jump X_control`；返回 `(新文本, changes)`。"""
    out, changes = [], []
    for no, line in enumerate(str(text or '').splitlines(), 1):
        m = _RE_JUMP.match(line)
        if m and m.group(2) in labels:
            old_target = m.group(2)
            new_target = control_label(old_target)
            new_line = f'{m.group(1)}jump {new_target}{m.group(3) or ""}'
            out.append(new_line)
            changes.append((no, line, new_line))
        else:
            out.append(line)
    new_text = '\n'.join(out)
    if str(text or '').endswith('\n'):
        new_text += '\n'
    return new_text, changes


def plan_takeover(project_root, labels, control_path=None) -> TakeoverPlan:
    """
    算"把这几章的入口接进编排层"要改哪些文件。**不碰磁盘。**

    `labels` 是章节 label（`X`，不是 `X_control`）。
    """
    root = Path(str(project_root or '.'))
    plan = TakeoverPlan()
    labels = [str(x or '').strip() for x in (labels or ())]
    labels = [x for x in dict.fromkeys(labels) if x]
    plan.labels = list(labels)
    if not labels:
        plan.reason = '没有指定要接管的章节'
        plan.blocking.append(plan.reason)
        return plan
    if not root.is_dir():
        plan.reason = f'项目目录不存在：{root}'
        plan.blocking.append(plan.reason)
        return plan

    declared = _label_files(root)
    missing = [x for x in labels if x not in declared]
    if missing:
        plan.reason = ('项目里找不到这些 label 的声明：' + '、'.join(missing)
                       + ' —— 先修源文件再接管')
        plan.blocking.append(plan.reason)
        return plan

    found = find_control_file(root, labels[0])
    path = Path(control_path) if control_path else (
        Path(found) if found else default_control_path(root))
    plan.control_path = str(path)
    plan.creates_control = not path.exists()
    from storygraph.story.source_style import read_text
    if path.exists():
        old_control, style = read_text(path)
    else:
        old_control, style = '', None

    # —— 1. 补包装（只补没有的）——
    new_control = old_control
    existing_control = set(_RE_LABEL.findall(new_control))
    for label in labels:
        name = control_label(label)
        if name in existing_control:
            continue
        new_control = _append_wrapper(new_control, label)
        plan.wrappers.append(label)
        existing_control.add(name)

    # —— 2. 改全项目的 `jump X` ——
    wanted = set(labels)
    from storygraph.parsing.discover import discover_scripts
    try:
        paths = [Path(p) for p in discover_scripts(root)]
    except Exception as exc:                       # noqa: BLE001
        plan.reason = f'扫不了项目脚本：{exc}'
        plan.blocking.append(plan.reason)
        return plan
    control_resolved = (path.resolve() if path.exists()
                        else path.absolute().resolve())
    for fp in paths:
        try:
            if fp.resolve() == control_resolved:
                continue
            text, _style = read_text(fp)
        except OSError:
            continue
        new_text, changes = _apply_jumps(text, wanted)
        if not changes:
            continue
        plan.edits.append(FileEdit(path=str(fp), old_text=text,
                                   new_text=new_text, changes=changes))

    # 只报告、不改写的入口
    call_refs = _call_refs(paths, wanted, control_resolved)
    for ref in call_refs:
        plan.notes.append(
            f'`call {ref["label"]}`（{Path(ref["path"]).name}:{ref["line"]}）'
            f'没有改 —— 包裝可能 jump 走，调用点会永远回不来；要改得由人决定')
    if not plan.edits:
        plan.notes.append(
            '一个 `jump ' + '/jump '.join(labels) + '` 入口都没找到：'
            '包装补上了，但如果这一章不是从 `jump` 进入的，它可能仍不会执行')

    # 控制文件也进 edits（放在最前面：先有包装，再有入口）
    if new_control != old_control or not path.exists():
        plan.edits.insert(0, FileEdit(path=str(path), old_text=old_control,
                                      new_text=new_control,
                                      creates=plan.creates_control,
                                      changes=[]))
        if plan.creates_control:
            from storygraph.story.generation_guard import ensure_generator_marker
            edit = plan.edits[0]
            edit.new_text = ensure_generator_marker(edit.new_text)
            plan.notes.append(f'会新建编排层：{path}')

    plan.ok = not plan.blocking
    plan.reason = ('接管 ' + '、'.join(labels)
                   + f'：补 {len(plan.wrappers)} 个包装，改 '
                   + f'{sum(len(e.changes) for e in plan.edits)} 处入口')
    plan.diff = format_takeover_diff(plan)
    return plan


def _call_refs(paths, labels: set, exclude: Path) -> list:
    out = []
    for fp in paths:
        try:
            if fp.resolve() == Path(exclude).resolve():
                continue
            text = Path(fp).read_text(encoding='utf-8-sig', errors='replace')
        except OSError:
            continue
        for no, line in enumerate(text.splitlines(), 1):
            raw = line.strip()
            if raw.startswith('call '):
                target = raw[5:].split()[0] if len(raw) > 5 else ''
                if target in labels:
                    out.append({'path': str(fp), 'line': no, 'label': target})
    return out


def format_takeover_diff(plan: TakeoverPlan, limit: int = 240) -> str:
    """人看的 unified diff（每个文件一段；太长就截断）。"""
    chunks, used = [], 0
    for edit in plan.edits:
        before = edit.old_text.splitlines()
        after = edit.new_text.splitlines()
        diff = list(difflib.unified_diff(
            before, after, fromfile=edit.path, tofile=edit.path, lineterm='',
            n=2))
        if not diff:
            continue
        chunks.append('\n'.join(diff))
        used += len(diff)
        if used > limit:
            chunks.append(f'…（还有更多改动，已省略）')
            break
    return '\n\n'.join(chunks)


def apply_takeover(plan) -> dict:
    """
    真的写盘：**先核对所有文件都没变，再逐个写**；中途失败按 old_text 回滚。

    返回 `{ok, written, error, rolled_back}`。
    """
    if not isinstance(plan, TakeoverPlan) or not plan.ok:
        return {'ok': False, 'written': [], 'error': '计划无效',
                'rolled_back': []}
    from storygraph.story.paths import write_text_atomic
    from storygraph.story.source_style import read_text

    # —— 写前核对（所有文件）——
    for edit in plan.edits:
        p = Path(edit.path)
        current = ''
        if p.exists():
            current, _style = read_text(p)
        if current != edit.old_text:
            return {'ok': False, 'written': [], 'rolled_back': [],
                    'error': f'{p.name} 在计划之后又被改过；为了不覆盖新改动，'
                             f'这次一个文件都没写'}

    written = []
    for edit in plan.edits:
        p = Path(edit.path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            style = (read_text(p)[1] if p.exists() else None)
            from storygraph.story.source_style import PLAIN
            write_text_atomic(p, (style or PLAIN).apply(edit.new_text))
            written.append(str(p))
        except OSError as exc:
            # 回滚已经写过的（尽力而为）
            rolled = []
            for done in reversed(written):
                edit = next((e for e in plan.edits if e.path == done), None)
                if edit is None:
                    continue
                try:
                    d = Path(done)
                    if edit.creates:
                        d.unlink(missing_ok=True)
                        rolled.append(done)
                        continue
                    style = read_text(d)[1]
                    write_text_atomic(d, style.apply(edit.old_text))
                    rolled.append(done)
                except OSError as rollback_exc:
                    # 回滚失败只能记账：调用方仍会如实说"已回滚 N 个"，
                    # 但不会假装这一个也回去了。
                    from storygraph.core.safeguard import swallow
                    swallow('story.control_takeover.rollback', rollback_exc)
            return {'ok': False, 'written': written, 'rolled_back': rolled,
                    'error': f'写 {p.name} 失败：{exc}；'
                             f'已回滚 {len(rolled)} 个文件'}
    return {'ok': True, 'written': written, 'error': '', 'rolled_back': []}


__all__ = ['FileEdit', 'TakeoverPlan', 'apply_takeover', 'default_control_path',
           'format_takeover_diff', 'plan_takeover']
