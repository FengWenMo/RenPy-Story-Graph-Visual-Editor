# -*- coding: utf-8 -*-
"""
删一条**没在当前编辑器里打开**的源边：只删源文件里那一行，不切章节。

## 为什么需要（P2，2026-10-24）

删源边 = 删剧本里那一拍。可图上的边可能来自另一章 —— 那一章没打开时，
`edge_delete_plan` 只能回答"这一拍不在这章里"，用户得先切过去再删。
切过去会丢掉当前未保存的编辑（或者打断心流）。

这里用**临时会话**读那个文件，按 `line_id` 找到拍，再用拍上的运行期真迹
`src_first` / `src_last` 删掉**物理行**。它不导出、不重生成、不写生成标记，
所以手写文件仍然是手写文件。

## 判据（每条都可解释）

1. 文件不存在 → 拒绝；
2. 临时会话里找不到这个 `line_id` → 拒绝（说明导入之后文件被改过，
   行身份已经对不上；先重载再删，不猜"大概是这一行"）；
3. 那一拍不是 `jump` / `call` → 拒绝（源边只可能由控制流拍派生）；
4. 删完这个 label 一拍不剩 → 拒绝（一章至少要留一拍）；
5. 真正写盘前再读一次文件，内容必须和计划时**逐字节相同**，否则拒绝。

## 不做什么

- 不碰别的行、不加头注释、不改换行/BOM（`source_style` 负责还原）；
- 不切当前会话、不改当前章节；
- 找不到就拒绝，不按"目标 label 相同"猜一行。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SourceLinePlan:
    """删一条跨章源边的**纯计划**。"""

    ok: bool = False
    path: str = ''
    line_id: str = ''
    label: str = ''
    index: int = -1
    #: 物理行号（1 基，闭区间）
    start: int = 0
    end: int = 0
    text: str = ''
    old_text: str = ''
    new_text: str = ''
    reason: str = ''
    blocking: list = field(default_factory=list)
    #: 这条边在块体里、块里又只有它 → 连**整块**一起删（`if` / 选项体 / menu）
    cascade: bool = False
    block_start: int = 0
    block_end: int = 0
    #: 不能整块删时，往空块体里补的一行（`pass`；保持产物可解析）
    pass_line: str = ''

    def to_dict(self) -> dict:
        return {
            'ok': self.ok, 'path': self.path, 'line_id': self.line_id,
            'label': self.label, 'index': self.index, 'start': self.start,
            'end': self.end, 'text': self.text, 'reason': self.reason,
            'blocking': list(self.blocking),
            'cascade': self.cascade, 'block_start': self.block_start,
            'block_end': self.block_end, 'pass_line': self.pass_line,
        }


def _indent_of(line: str) -> int:
    return len(str(line)) - len(str(line).lstrip(' \t'))


def _is_content(line: str) -> bool:
    """空行 / 纯注释不算块体内容（Ren'Py 眼里也一样）。"""
    body = str(line or '').strip()
    return bool(body) and not body.startswith('#')


def _enclosing_header(lines: list, index: int):
    """`index` 这一行的**上一级块头**（0 基）；顶层 / label 返回 `None`。"""
    if index <= 0:
        return None
    base = _indent_of(lines[index])
    j = index - 1
    while j >= 0:
        line = lines[j]
        if not _is_content(line):
            j -= 1
            continue
        if _indent_of(line) < base:
            body = line.strip()
            if body.startswith('label '):
                return None
            return j if body.endswith(':') else None
        j -= 1
    return None


def _block_span(lines: list, head: int) -> tuple:
    """块头 `head` 到**块尾**（0 基，闭区间）：缩进回到 <= 块头为止。"""
    base = _indent_of(lines[head])
    last = head
    i = head + 1
    while i < len(lines):
        if not str(lines[i]).strip():
            i += 1
            continue
        if _indent_of(lines[i]) <= base:
            break
        last = i
        i += 1
    return head, last


def _body_indices(lines: list, head: int, last: int) -> list:
    return [i for i in range(head + 1, last + 1) if _is_content(lines[i])]


def _parent_header(lines: list, head: int):
    """块头的上一级块头（跳过 label）；`None` = 没有 / 到了 label。"""
    if head <= 0:
        return None
    base = _indent_of(lines[head])
    j = head - 1
    while j >= 0:
        line = lines[j]
        if not _is_content(line):
            j -= 1
            continue
        if _indent_of(line) < base:
            body = line.strip()
            if body.startswith('label '):
                return None
            return j if body.endswith(':') else None
        j -= 1
    return None


def plan_source_line_delete(project_root, file_path, line_id,
                            label: str = '') -> SourceLinePlan:
    """算"删掉源文件里这一拍"会不会成功。**不碰磁盘。**"""
    plan = SourceLinePlan(path=str(file_path or ''),
                          line_id=str(line_id or ''),
                          label=str(label or ''))
    path = Path(str(file_path or ''))
    if not path.is_file():
        plan.reason = f'源文件不存在：{path}'
        plan.blocking.append(plan.reason)
        return plan
    if not plan.line_id:
        plan.reason = '这条边没有行身份（line_id），不能直接删源文件'
        plan.blocking.append(plan.reason)
        return plan

    from storygraph.app.editor import EditorSession
    try:
        session = EditorSession(project_root=project_root)
        session.load(path)
    except Exception as exc:                      # noqa: BLE001 - 读不了就拒绝
        plan.reason = f'读不了这一章：{exc}'
        plan.blocking.append(plan.reason)
        return plan

    idx = next((i for i, b in enumerate(session.beats or ())
                if str(getattr(b, 'line_id', '') or '') == plan.line_id), None)
    if idx is None:
        plan.reason = ('这一章在导入之后改过，找不到边对应的那一拍了；'
                       '先重新加载这一章，再删这条线')
        plan.blocking.append(plan.reason)
        return plan

    beat = session.beats[idx]
    plan.index = int(idx)
    plan.label = str(getattr(beat, 'label', '') or plan.label)
    raw = str(getattr(beat, 'raw', '') or '').strip()
    if not (raw.startswith('jump ') or raw.startswith('call ')):
        plan.reason = f'这一拍不是 jump/call（{raw or "空拍"}），不能当源边删'
        plan.blocking.append(plan.reason)
        return plan

    same_label = [b for b in session.beats
                  if str(getattr(b, 'label', '') or '') == plan.label]
    if len(same_label) <= 1:
        plan.reason = f'删掉这一拍之后，`{plan.label}` 就一拍不剩了'
        plan.blocking.append(plan.reason)
        return plan

    start = int(getattr(beat, 'src_first', 0) or 0)
    end = int(getattr(beat, 'src_last', 0) or 0)
    if not (start >= 1 and end >= start):
        plan.reason = '这一拍没有可用的物理行号（src_first/src_last）'
        plan.blocking.append(plan.reason)
        return plan

    from storygraph.story.source_style import read_text
    text, _style = read_text(path)
    lines = text.splitlines()
    if end > len(lines):
        plan.reason = '拍上的行号已经超出文件范围；先重新加载这一章'
        plan.blocking.append(plan.reason)
        return plan
    segment = lines[start - 1:end]
    head = segment[0].strip() if segment else ''
    if not (head.startswith('jump ') or head.startswith('call ')):
        plan.reason = '拍上的行号和文件内容对不上；先重新加载这一章'
        plan.blocking.append(plan.reason)
        return plan

    # —— 块体里只有这一行时，删"行"会把块删成空的（`"选项":` 下面没有语句，
    #    Ren'Py 直接报语法错）。所以要先判断能不能**连整块一起删**，
    #    不能删整块就往空块体里补 `pass`。
    del_start, del_end = start, end          # 1 基、闭区间
    block = _enclosing_header(lines, start - 1)
    if block is not None:
        b_last = _block_span(lines, block)[1]
        others = [i for i in _body_indices(lines, block, b_last)
                  if not (start - 1 <= i <= end - 1)]
        cascade_ok = True
        # `if` 后面紧跟 `elif` / `else` 时不能删整个 if 块 —— 那条链会失去
        # 头（`elif` 单独存在是语法错）。这种情况只删 jump 并补 pass。
        head_text = lines[block].strip()
        if head_text.startswith('if '):
            nxt = next((lines[i] for i in range(b_last + 1, len(lines))
                        if _is_content(lines[i])), '')
            if nxt.strip().startswith(('elif ', 'else:')) \
                    and _indent_of(nxt) == _indent_of(lines[block]):
                cascade_ok = False
        if not others and cascade_ok:
            # 块里只有这条 jump：往上收，`menu:` 只剩这一个选项时也一起删。
            c_start, c_end = block, b_last
            for _ in range(4):
                parent = _parent_header(lines, c_start)
                if parent is None:
                    break
                p_last = _block_span(lines, parent)[1]
                p_others = [i for i in _body_indices(lines, parent, p_last)
                            if not (c_start <= i <= c_end)]
                if p_others:
                    break
                c_start, c_end = parent, p_last
            # 整块删完这一章一拍不剩 → 不许（label 不能是空的）：
            # 退回"只删这一行 + pass"，至少产物能解析。
            remaining = []
            for b in session.beats:
                if str(getattr(b, 'label', '') or '') != plan.label:
                    continue
                first = int(getattr(b, 'src_first', 0) or 0)
                if c_start + 1 <= first <= c_end + 1:
                    continue
                remaining.append(b)
            if remaining:
                del_start, del_end = c_start + 1, c_end + 1
                plan.cascade = True
                plan.block_start, plan.block_end = c_start + 1, c_end + 1
            else:
                plan.pass_line = (lines[start - 1][
                    :_indent_of(lines[start - 1])] + 'pass')
        elif not others:
            plan.pass_line = (lines[start - 1][
                :_indent_of(lines[start - 1])] + 'pass')

    removed = lines[del_start - 1:del_end]
    plan.start, plan.end = del_start, del_end
    plan.text = '\n'.join(removed)
    out = list(lines[:del_start - 1])
    if plan.pass_line:
        out.append(plan.pass_line)
    out.extend(lines[del_end:])
    body = '\n'.join(out)
    if text.endswith('\n'):
        body += '\n'
    plan.old_text, plan.new_text = text, body
    plan.ok = True
    if plan.cascade:
        plan.reason = (f'这一行是块体里唯一的内容：删整块'
                       f'（{Path(path).name} 第 {del_start}–{del_end} 行）')
    elif plan.pass_line:
        plan.reason = (f'删掉 {Path(path).name} 第 {start} 行，'
                       f'空出来的块体补 `pass`')
    else:
        plan.reason = (f'删掉 {Path(path).name} 第 {start} 行'
                       f'（{plan.text.strip()}）')
    return plan


def apply_source_line_delete(plan: SourceLinePlan) -> dict:
    """真的删掉那一行（原子替换）。返回 `{ok, old_text, new_text, error}`。"""
    if not isinstance(plan, SourceLinePlan) or not plan.ok:
        return {'ok': False, 'old_text': '', 'new_text': '',
                'error': '计划无效'}
    path = Path(plan.path)
    from storygraph.story.source_style import read_text
    text, style = read_text(path)
    if text != plan.old_text:
        return {'ok': False, 'old_text': text, 'new_text': text,
                'error': '源文件在计划之后又被改过；为了不误删，这次没有写'}
    try:
        from storygraph.story.paths import write_text_atomic
        write_text_atomic(path, style.apply(plan.new_text))
    except OSError as exc:
        return {'ok': False, 'old_text': text, 'new_text': text,
                'error': f'写不进源文件：{exc}'}
    return {'ok': True, 'old_text': plan.old_text, 'new_text': plan.new_text,
            'error': ''}


__all__ = ['SourceLinePlan', 'apply_source_line_delete',
           'plan_source_line_delete']
