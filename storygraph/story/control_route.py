# -*- coding: utf-8 -*-
"""
**章末接线写进编排层**：只改 `<label>_control` 段里的那一两行，剧本一个字不动。

## 为什么必须有这一层（P2，2026-10-24）

P0/P1 把章末接线写进了**章节源文件**（`return` 前插一行 `jump`）。那在
"没有编排层"的项目里是对的；但只要项目里存在 `chapter_controller.rpy`
（`X_control` 里 `call X` + `jump Y_control`），往 `X` 末尾插 `jump Y`
就会变成：

    X_control: call X → X 里 jump Y → Y 结束 return → 回到 X_control
               → X_control 又 jump Y_control → 把 Y 跑了第二遍

所以有编排层时，章末接线**必须写编排层**。这条判据不是"更优雅"，是
"不写就会跑两遍"。

## 只打一个补丁，不整份重生成

`export_to_renpy` 是整份重生成；这里只动目标 label 的那一行。判据：

  · 目标 label 不存在 → 不写（不自动建编排层）；
  · 最后一个顶层语句是 `return` → 替换成 `jump`（jump 不会回来）；
  · 最后一个顶层语句是 `jump X_control` → **替换**（会明说是改接，不静默）；
  · 最后一个顶层语句是 `menu:` / `if` / 块体 → 不写（归控制流图/卡片）；
  · `call` 不改接：插在最后一条路由**之前**，让子过程返回后继续原路。

## 与导出的关系

写进去的那条边同时进图（`origin='control'`），所以下次「导出控制流」时
`export_to_renpy` 会按图生成同一条 `jump`，不会把这次改动冲掉。

## P2.5：补**目标章**的缺失包装（2026-10-24 追加）

"自动补 `_control` label" 不是一句 `label X_control:` 就完事：**得有人进来**。
所以只补一种：项目里已经有编排层，缺的是**目标章**的包装。这时新写的
`jump B_control` 就是它的第一个入口，补出来的包装一定被执行。

反过来的"源章缺包装"不自动补：那一章现在多半是被 `jump X` 直接进入的，
补一个没人跳进来的 `X_control` 只会是死代码 —— 要补得先接管入口（P3）。

包装的形状是**语义等价**的：

    label B_control:
        call B
        return

`jump B_control` → `call B`（B 结束 return 回到这里）→ `return`，和原来
`jump B` 直接执行到 B 的 `return` 落到同一个调用帧。之后 `plan_control_route`
会把最后这条 `return` 换成 `jump C_control`，正好是"跑完 B 再去 C"。

## 不做什么

- 不新建**没有入口**的控制流文件（那只会是死代码）；
- 不整份重写；只追加一个包装 / 替换、插入**一行**（call 可能两行）；
- 不碰章节源文件。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.textlines import split_lines
from storygraph.story import scan_cache
from storygraph.story.source_style import SourceNotUtf8

CONTROL_SUFFIX = '_control'


def control_label(label: str) -> str:
    """章节 label → 它在编排层里的包装 label（`ch01` → `ch01_control`）。"""
    name = str(label or '').strip()
    return f'{name}{CONTROL_SUFFIX}' if name else ''


def _label_re(name: str):
    return re.compile(rf'^label\s+{re.escape(name)}\s*(?:\(|:)', re.MULTILINE)


def _blocks(text: str) -> list:
    """
    顶层 label 块：`[{name, header, start, end}]`（`start`/`end` 是 0 基行号，
    块体是 `[start+1, end)`）。只认行首无缩进的 `label`，块在下一个行首
    label 或文件尾结束。
    """
    lines = split_lines(text)
    starts = []
    for i, line in enumerate(lines):
        m = re.match(r'^label\s+([^\s:(#]+)\s*(?:\(|:)', line)
        if m:
            starts.append((i, m.group(1)))
    out = []
    for n, (i, name) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        out.append({'name': name, 'header': i, 'start': i, 'end': end})
    return out


def _block_for(text: str, name: str):
    for block in _blocks(text):
        if block['name'] == name:
            return block
    return None


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(' \t'))


def _top_lines(lines: list, block) -> list:
    """
    块内**顶层语句**的行号（最小缩进、非空、非注释）。

    控制流文件的顶层缩进通常就是 4 空格；用"块内最小缩进"而不是写死 4，
    是为了容忍两空格 / 制表符的项目 —— 判据仍是"这一层是顶层"。
    """
    body = [(i, lines[i]) for i in range(block['start'] + 1, block['end'])
            if lines[i].strip() and not lines[i].lstrip().startswith('#')]
    if not body:
        return []
    base = min(_indent(ln) for _i, ln in body)
    return [(i, ln) for i, ln in body if _indent(ln) == base]


def _route_of(text: str):
    """`'jump X_control'` → `('jump', 'X_control')`；认不出 → `None`。"""
    text = str(text or '').strip()
    if text.startswith('jump '):
        target = text[5:].strip()
        if not target or target.startswith('expression '):
            return ('dynamic', target)
        return ('jump', target.split()[0])
    if text.startswith('call '):
        rest = text[5:].strip()
        if not rest or rest.startswith('expression '):
            return ('dynamic', rest)
        return ('call', rest.split()[0])
    if text == 'return' or text.startswith('return '):
        return ('return', '')
    return None


def _terminating_of(text: str) -> str:
    """
    这一行是不是"**游戏到此为止**"（`$ renpy.full_restart()` / `$ renpy.quit()`）？

    是就返回那个调用名，否则空串。判据不在这里：问 `story.exits
    .terminating_call`（"终局语句"只有那一份定义），这里只负责把行首的
    `$` 剥掉 —— 编排层里它是 `$ renpy.full_restart()`，而出入口分析那边
    拿到的是代码本身（没有 `$`）。
    """
    raw = str(text or '').strip()
    if raw.startswith('$'):
        raw = raw[1:].strip()
    elif raw == 'python' or raw.startswith('python '):
        return ''            # `python:` 块不是单行，这里不猜
    from storygraph.story.exits import terminating_call
    return terminating_call(raw)


@dataclass
class ControlRoutePlan:
    """一次"写到编排层"的**纯计划**（不碰磁盘）。"""

    ok: bool = False
    #: `insert` / `replace` / `exists` / `remove` / `blocked`
    kind: str = 'blocked'
    path: str = ''
    #: 起点章 label 与它在编排层里的 `_control`
    src_label: str = ''
    control_label: str = ''
    #: 目标（jump 用 `B_control`；call 用 `B`）
    dst_label: str = ''
    target_label: str = ''
    etype: str = 'jump'
    #: 要替换/插在哪一行（1 基；`insert` 是插在这一行之前）
    line_no: int = 0
    old_line: str = ''
    new_lines: list = field(default_factory=list)
    #: 这一段原来的路由（给人看："原来 jump C_control"）
    old_route: str = ''
    reason: str = ''
    blocking: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    #: 目标章缺包装时的"补计划"（P2.5；`ok=True` 才能一键补）
    init: object = None
    #: 源章缺包装时的"补计划"（只作解释：源章补了也是死代码）
    src_init: object = None

    def to_dict(self) -> dict:
        return {
            'ok': self.ok, 'kind': self.kind, 'path': self.path,
            'src_label': self.src_label, 'control_label': self.control_label,
            'dst_label': self.dst_label, 'target_label': self.target_label,
            'etype': self.etype, 'line_no': self.line_no,
            'old_line': self.old_line, 'new_lines': list(self.new_lines),
            'old_route': self.old_route, 'reason': self.reason,
            'blocking': list(self.blocking), 'notes': list(self.notes),
            'init': self.init.to_dict() if self.init is not None else None,
            'src_init': (self.src_init.to_dict()
                         if self.src_init is not None else None),
        }


#: `label 名字:` 的正则**不再在这里**：唯一的定义在 `story.scan_cache.LABEL_RE`
#: （`_labels_of` 直接读缓存）。这里保留注释只是给搜代码的人一个指针 ——
#: 历史上有三份副本（本文件 / `control_takeover` / `control_write`），
#: 新代码请用 `scan_cache.labels_of`，别再抄第四份。


def _labels_of(path) -> list:
    """
    这个 `.rpy` 里有哪些 label —— 走 `scan_cache`（同一份文件只读一次）。

    为什么不再自己读：`find_control_file` 每问一次就要扫全部脚本，而一次
    接线里它被问好几遍（真机 91 个文件、每遍 50 ms 的读盘+正则）。
    """
    return list(scan_cache.labels_of(path))


def find_control_file(project_root, src_label: str = ''):
    """
    项目里的编排层文件（没有就 `None`）。

    判据比 `detect_placement` **宽一档**：只要文件里有 `*_control` label
    就算候选。原因（实测）：控制流里一旦出现 `menu:` 或
    `jump expression x`，`placement` 的"全部 label 无演出"判据就不认它了 ——
    可它仍然是编排层，章末接线照样不能写源文件。

    排序：有 `src_label_control` 的优先；其次 `detect_placement` 认出的那个；
    再其次文件名带 `control` / `controller` 的；最后按路径排序（不猜内容）。

    ## 两个让它变快的判据（2026-10-26，都是**结论不变**的短路）

    1. **只有一个候选** → 直接给它。排序的输入只有一个元素时，排不排一样；
       而"排"要跑 `detect_placement`（整份解析全部脚本，真机 1.4 秒）。
       绝大多数项目只有一个编排层文件，这一条把 1.5 s 变成 ~40 ms。
    2. 整个结论按**项目指纹**缓存（`scan_cache.memo`）：文件没变就直接给
       上一次的答案，不管调用方连着问几遍。
    """
    if not str(project_root or '').strip():
        # ⚠ `Path('')` 是当前目录：空项目根会被当成"整个仓库"，rglob 扫到
        # 天荒地老（实测卡 8 秒以上还没扫完）。没有项目根就直接说没有。
        return None
    root = Path(str(project_root))
    if not root.is_dir():
        return None
    src = str(src_label or '').strip()
    fp = scan_cache.fingerprint(root)
    return scan_cache.memo('find_control_file', root, src, fp,
                           lambda: _find_control_file_uncached(root, src))


def _find_control_file_uncached(root: Path, src: str):
    """`find_control_file` 的纯计算部分（结果按指纹缓存）。"""
    try:
        paths = scan_cache.discover(root)
    except Exception as exc:                  # noqa: BLE001 - 扫不出来就只试清单
        from storygraph.core.safeguard import swallow
        swallow('story.control_route.find_control_file.scan', exc)
        paths = []

    with_control = []
    for path in paths:
        labels = _labels_of(path)
        if any(lb.endswith(CONTROL_SUFFIX) for lb in labels):
            with_control.append((path, labels))
    if not with_control:
        return None
    if len(with_control) == 1:
        # 只有一个候选：下面三步（精确包装 / placement / 文件名）都是在
        # **几个候选里挑一个**，一个候选谈不上挑 —— 结论必然相同。
        return with_control[0][0]

    if src:
        exact = [p for p, labels in with_control
                 if control_label(src) in labels]
        if exact:
            return exact[0]

    preferred = None
    try:
        from storygraph.story.placement import detect_placement
        rel = str(getattr(detect_placement(root), 'control_flow', '') or '')
        if rel:
            want = Path(rel)
            if not want.is_absolute():
                want = root / want
            preferred = next((p for p, _ in with_control
                              if p.resolve() == want.resolve()), None)
    except Exception as exc:                  # noqa: BLE001 - 只是排序
        from storygraph.core.safeguard import swallow
        swallow('story.control_route.find_control_file.placement', exc)
        preferred = None
    if preferred is not None:
        return preferred

    def _rank(item):
        path = item[0]
        name = path.name.lower()
        return (0 if ('control' in name or 'controller' in name) else 1,
                str(path).lower())

    return sorted(with_control, key=_rank)[0][0]


_RE_LABEL_LINE = re.compile(
    r'^label\s+([^\s:(#]+)\s*(?:\(([^)]*)\))?\s*:')
_RE_LABEL_NAME = re.compile(r'^[\w\u4e00-\u9fff][\w\u4e00-\u9fff.]*$')


def _label_decl(project_root, name: str):
    """
    项目里 `label 名字` 的声明（第一处）：`{path, line_no, params, text}`。

    只认行首无缩进的 `label`：缩进的同名行不是顶层 label。

    结论按**项目指纹**缓存（`scan_cache.memo`）：`plan_control_init` 一次要问
    两个名字，而每问一个都要读一遍全部脚本 —— 真机上那是 1.2 MB × 2。
    """
    if not str(project_root or '').strip():
        return None
    want = str(name or '').strip()
    if not want:
        return None
    root = Path(str(project_root))
    fp = scan_cache.fingerprint(root)
    found = scan_cache.memo('label_decl', root, want, fp,
                            lambda: _label_decl_uncached(root, want))
    return dict(found) if found else None


def _label_decl_uncached(root: Path, want: str):
    """`_label_decl` 的纯计算部分（结果按指纹缓存）。"""
    try:
        paths = scan_cache.discover(root)
    except Exception as exc:                  # noqa: BLE001 - 扫不出来就当没有
        from storygraph.core.safeguard import swallow
        swallow('story.control_route._label_decl.scan', exc)
        return None
    for path in paths:
        try:
            text, _style = _read(path)
        except OSError:
            continue
        for i, line in enumerate(split_lines(text)):
            m = _RE_LABEL_LINE.match(line)
            if m and m.group(1) == want:
                return {'path': str(path), 'line_no': i + 1,
                        'params': str(m.group(2) or '').strip(),
                        'text': line}
    return None


def _read(path):
    """读源文件 → `(text, style)`；走 `scan_cache`（同一次接线里会读很多遍）。"""
    return scan_cache.read_text(path)


def _incoming_refs(project_root, name: str) -> list:
    """
    谁 `jump` / `call` 了这个 label（含 `jump X_control`）。

    `call X from Y` 的目标仍是 X —— 用 `_route_of` 判，不另写一套正则。

    同样按项目指纹缓存：`plan_control_init` 一次要问"直接进入的"和
    "走 `_control` 进入的"两个名字，问法一样，答案只跟文件内容有关。
    """
    refs = []
    if not str(project_root or '').strip():
        return refs
    want = str(name or '').strip()
    if not want:
        return refs
    root = Path(str(project_root))
    fp = scan_cache.fingerprint(root)
    found = scan_cache.memo('incoming_refs', root, want, fp,
                            lambda: _incoming_refs_uncached(root, want))
    return [dict(r) for r in (found or ())]


def _incoming_refs_uncached(root: Path, want: str) -> list:
    """`_incoming_refs` 的纯计算部分（结果按指纹缓存）。"""
    refs = []
    try:
        paths = scan_cache.discover(root)
    except Exception as exc:                  # noqa: BLE001
        from storygraph.core.safeguard import swallow
        swallow('story.control_route._incoming_refs.scan', exc)
        return refs
    for path in paths:
        try:
            text, _style = _read(path)
        except OSError:
            continue
        for i, line in enumerate(split_lines(text)):
            raw = line.strip()
            if not raw or raw.startswith('#'):
                continue
            route = _route_of(raw)
            if route and route[0] in ('jump', 'call') and route[1] == want:
                refs.append({'path': str(path), 'line_no': i + 1,
                             'etype': route[0], 'line': line})
    return refs


@dataclass
class ControlInitPlan:
    """补一个 `<label>_control` 包装的**纯计划**（不碰磁盘）。"""

    ok: bool = False
    #: `exists`（已经有了）/ `append`（追加包装）/ `blocked`
    kind: str = 'blocked'
    path: str = ''
    chapter_label: str = ''
    control_label: str = ''
    #: `target`（目标章，补了立刻有人走）/ `source`（源章，暂时可能到不了）
    role: str = 'target'
    old_text: str = ''
    new_text: str = ''
    reason: str = ''
    blocking: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    #: 谁进入这一章（判断"补了会不会是死代码"）
    incoming: list = field(default_factory=list)
    safe: bool = False
    #: 补出来的包装**现在有没有人走**（源章缺入口时为 False：写了也不执行）
    reachable: bool = True

    def to_dict(self) -> dict:
        return {
            'ok': self.ok, 'kind': self.kind, 'path': self.path,
            'chapter_label': self.chapter_label,
            'control_label': self.control_label, 'role': self.role,
            'reason': self.reason, 'blocking': list(self.blocking),
            'notes': list(self.notes), 'incoming': list(self.incoming),
            'safe': self.safe, 'reachable': bool(self.reachable),
        }


def plan_control_init(project_root, label, role='target') -> ControlInitPlan:
    """
    给 `label` 补一个 `label_control` 包装。**不碰磁盘。**

    `role='target'`：正在给**目标章**接一条新入口 —— 补出来的包装会被
    新写的 `jump B_control` 进入，所以安全。

    `role='source'`：源章缺包装。照样补（追加 `call X` / `return`），但
    `notes` 里会写清这一条现在**有没有人走** —— 没人进入这一章、或者它是被
    `jump X` 直接进入的，补出来的盒子暂时执行不到。仍然写，是因为人在图上
    搭故事时是"先接分支、后接入口"，拒绝只会让图上留一堆草图。
    """
    src = str(label or '').strip()
    role = 'source' if str(role or '') == 'source' else 'target'
    plan = ControlInitPlan(chapter_label=src, role=role)
    plan.control_label = control_label(src)

    if not src:
        plan.reason = '没有 label 名'
        plan.blocking.append(plan.reason)
        return plan
    if src.endswith(CONTROL_SUFFIX):
        plan.reason = f'`{src}` 已经是编排层 label，不能再包一层'
        plan.blocking.append(plan.reason)
        return plan
    if not _RE_LABEL_NAME.match(src):
        plan.reason = f'`{src}` 不像一个合法的 label 名'
        plan.blocking.append(plan.reason)
        return plan

    decl = _label_decl(project_root, src)
    if decl is None:
        plan.reason = (f'项目里找不到 `label {src}` —— 目标章还没导入/没有源文件，'
                       f'不能凭空补一个 call {src}')
        plan.blocking.append(plan.reason)
        return plan
    if decl['params']:
        plan.reason = (f'`label {src}({decl["params"]})` 带参数：自动包装不知道'
                       f'该传什么，不能猜。请在编排层里手写这一块')
        plan.blocking.append(plan.reason)
        return plan

    existing = _label_decl(project_root, plan.control_label)
    if existing is not None:
        plan.ok = True
        plan.kind = 'exists'
        plan.safe = True
        plan.path = existing['path']
        try:
            text, _style = _read(existing['path'])
            plan.old_text = text
            plan.new_text = text
        except OSError as exc:                # noqa: BLE001 - 已有 label 就够，读不到只是没全文
            from storygraph.core.safeguard import swallow
            swallow('story.control_route.plan_control_init.read_existing', exc)
        plan.reason = (f'`{plan.control_label}` 已经存在'
                       f'（{Path(existing["path"]).name}:{existing["line_no"]}）')
        return plan

    path = find_control_file(project_root, src)
    if path is None:
        path = find_control_file(project_root)
    if path is None:
        plan.reason = (f'项目里没有任何 `*_control` label —— 现在新建一个 '
                       f'`{plan.control_label}` 也没有入口会跳进来，只会是死代码。'
                       f'先导出一次控制流，或手写一个入口，再补包装')
        plan.blocking.append(plan.reason)
        return plan
    plan.path = str(path)

    try:
        text, _style = _read(path)
    except OSError as exc:
        plan.reason = f'读不了编排层：{exc}'
        plan.blocking.append(plan.reason)
        return plan
    if not text.strip():
        plan.reason = f'编排层文件是空的：{Path(path).name}'
        plan.blocking.append(plan.reason)
        return plan

    jump_direct = _incoming_refs(project_root, src)
    jump_ctrl = _incoming_refs(project_root, plan.control_label)
    plan.incoming = jump_direct + jump_ctrl
    direct_jumps = [r for r in jump_direct if r['etype'] == 'jump']
    direct_calls = [r for r in jump_direct if r['etype'] == 'call']

    if role == 'source':
        # 源章缺包装 ⇒ **照样能补**，只是要记账"这一条线现在有没有人走"。
        #
        # 2026-10-27 真机（用户原话）：「2_4 到 2_4_1，我明明没有勾选草图！
        # 为什么还是只能画草图？？？」「就算是断裂的局部也能不画草图直接连」。
        # 以前这里一律拒绝（"补出来是死代码"），于是一个还没接进入口的章节
        # 永远接不出线 —— 而写剧本的顺序根本不是拓扑序：人先把这一段的
        # 分支搭好，最后才去接入口。拒绝等于让图上留一堆草图。
        #
        # 现在改成"写 + 明说"：包装照样追加，`notes` 里讲清它暂时到不了；
        # 等进入这一章的入口接上（或改接到 `X_control`），它立刻生效。
        plan.safe = True
        plan.reachable = bool(jump_ctrl)
        if jump_ctrl and not direct_jumps and not direct_calls:
            pass                              # 有人跳 X_control，但 label 丢了：修好它
        elif direct_jumps or direct_calls:
            how = []
            if direct_jumps:
                how.append(f'{len(direct_jumps)} 处 `jump {src}`')
            if direct_calls:
                how.append(f'{len(direct_calls)} 处 `call {src}`')
            plan.notes.append(
                f'`{src}` 现在是被 ' + '、'.join(how) + ' **直接**进入的：'
                f'补出来的 `{plan.control_label}` 暂时没人走 —— 等那些入口'
                f'改接到 `{plan.control_label}`，这条线才会真的执行')
        else:
            plan.notes.append(
                f'项目里现在没有任何地方进入 `{src}`：补出来的 '
                f'`{plan.control_label}` 暂时到不了 —— 等你在别处接上进入'
                f'这一章的线，它立刻生效')
    else:
        plan.safe = True
        if direct_jumps or direct_calls:
            plan.notes.append(
                '还有 ' + '、'.join(
                    ([f'{len(direct_jumps)} 处 `jump {src}`'] if direct_jumps else [])
                    + ([f'{len(direct_calls)} 处 `call {src}`'] if direct_calls else [])
                ) + f' 直接进入 `{src}`，它们不经过这个包装；'
                    f'要让它们也走编排层，得改接那些入口')

    block = [
        '',
        f'# 故事图编辑器补的编排入口：等价于直接进入「{src}」',
        f'# （call 完接着走这个盒子里的下一句）',
        f'label {plan.control_label}:',
        f'    call {src}',
        '    return',
        '',
    ]
    body = text
    if body and not body.endswith('\n'):
        body += '\n'
    if body and not body.endswith('\n\n'):
        body += '\n'
    plan.old_text = text
    plan.new_text = body + '\n'.join(block[1:]) + '\n'
    plan.ok = True
    plan.kind = 'append'
    plan.reason = (f'在 {Path(path).name} 末尾补 `{plan.control_label}`'
                   f'（call {src} / return，行为与直接进入 {src} 等价）')
    return plan


def apply_control_init(plan: ControlInitPlan) -> dict:
    """
    真的把包装追加上去。返回形状同 `apply_control_route`。

    写前核对**全文**：文件在算计划之后被改过就拒写。补 label 是追加，
    没有"锚点行"可对；整份比对是最直接的一致判据。
    """
    if not isinstance(plan, ControlInitPlan) or not plan.ok:
        return {'ok': False, 'path': '', 'old_text': '', 'new_text': '',
                'line_no': 0, 'error': '计划无效'}
    if plan.kind == 'exists':
        return {'ok': True, 'path': plan.path, 'old_text': plan.old_text,
                'new_text': plan.old_text, 'line_no': 0, 'error': ''}
    path = Path(plan.path)
    try:
        text, style = _read(path)
    except OSError as exc:
        return {'ok': False, 'path': str(path), 'old_text': '',
                'new_text': '', 'line_no': 0, 'error': f'读不了编排层：{exc}'}
    if text != plan.old_text:
        return {'ok': False, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': 0,
                'error': '编排层刚被改过（全文对不上）；重新算一次'}
    try:
        from storygraph.story.paths import write_text_atomic
        write_text_atomic(path, style.apply(plan.new_text, source=path))
    except SourceNotUtf8 as exc:
        return {'ok': False, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': 0, 'error': str(exc)}
    except OSError as exc:
        return {'ok': False, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': 0,
                'error': f'写不进编排层：{exc}'}
    return {'ok': True, 'path': str(path), 'old_text': text,
            'new_text': plan.new_text, 'line_no': 0, 'error': ''}


#: 一次接线里最多补几个 `_control` 包装（实际最多两个：源章一个、目标章一个）
INIT_CHAIN_ROUNDS = 3


def apply_init_chain(plan, replan, rounds=INIT_CHAIN_ROUNDS) -> dict:
    """
    把计划里带的 `_control` 包装**先补上**，再重算，直到计划自己能写。

    为什么要有"链"（2026-10-27 真机 `chapter2_4 → chapter2_4_1`）：一节
    新章通常两头的包装都缺 —— 源章没有 `chapter2_4_control`（没人从编排层
    进过它），目标章也没有 `chapter2_4_1_control`。补完源章那一个，重算出来
    的计划还需要再补目标章那一个。以前命令层只肯补**一个**，于是这种最常见
    的接线永远差一步。

    参数
    ----
    plan : 任何带 `init` / `src_init` 的计划（路由 / 分支 / 改接都行）
    replan : `() -> plan`，补完之后按同一份输入重算
    rounds : 最多补几轮（防病态输入死循环）

    返回 `{ok, plan, error, snapshots}`：`snapshots` 是
    `[(path, 补之前全文, 补之后全文)]`，交给 `rollback_wrapper_writes`
    回滚、交给命令的 `old_text` 做撤销。**不碰图，也不弹提示。**
    """
    snapshots = []
    if plan is None:
        return {'ok': False, 'plan': None, 'error': '计划为空',
                'snapshots': snapshots}
    for _round in range(max(1, int(rounds))):
        if getattr(plan, 'ok', False):
            break
        init = _next_init(plan)
        if init is None:
            break
        res = apply_control_init(init)
        if not res.get('ok'):
            return {'ok': False, 'plan': plan,
                    'error': str(res.get('error') or '补包装失败'),
                    'snapshots': snapshots}
        path = str(res.get('path') or getattr(init, 'path', '') or '')
        if path and str(getattr(init, 'kind', '') or '') == 'append':
            snapshots.append((path, str(res.get('old_text') or ''),
                              str(res.get('new_text') or '')))
        try:
            plan = replan()
        except Exception as exc:              # noqa: BLE001 - 重算失败要能回滚
            from storygraph.core.safeguard import swallow
            swallow('story.control_route.apply_init_chain.replan', exc)
            return {'ok': False, 'plan': plan,
                    'error': f'补完包装后重算失败：{exc}',
                    'snapshots': snapshots}
        if plan is None:
            return {'ok': False, 'plan': None,
                    'error': '补完包装后重算不出计划', 'snapshots': snapshots}
    if not getattr(plan, 'ok', False):
        return {'ok': False, 'plan': plan,
                'error': str(getattr(plan, 'reason', '') or '补完包装后仍然写不了'),
                'snapshots': snapshots}
    return {'ok': True, 'plan': plan, 'error': '', 'snapshots': snapshots}


def _next_init(plan):
    """
    这一轮该补哪个包装：先 `init`（当前这条线要落地的那一步），再 `src_init`。

    `kind='exists'` 的补计划**不算**（包装已经在文件里了，再"补"是空转，
    空转就会一直重算 —— 那是死循环）。
    """
    for name in ('init', 'src_init'):
        init = getattr(plan, name, None)
        if init is None or not getattr(init, 'ok', False):
            continue
        if str(getattr(init, 'kind', '') or '') == 'exists':
            continue
        return init
    return None


def rollback_wrapper_writes(snapshots) -> list:
    """
    把 `apply_init_chain` 补过的包装**退回去**（按写入顺序倒着退）。

    只退**没被别人改过**的那些：文件现在的全文必须还是自己刚写的那一版，
    否则说明中间有别的改动，覆盖它就是删别人的代码 —— 那就别动，记账。

    返回真正退回去的路径列表。
    """
    restored = []
    for item in reversed(list(snapshots or ())):
        try:
            path, old_text, new_text = item
        except (TypeError, ValueError):
            continue
        if not path or not old_text:
            continue
        target = Path(str(path))
        try:
            now, style = _read(target)
            if new_text and now != new_text:
                continue
            from storygraph.story.paths import write_text_atomic
            write_text_atomic(target,
                              style.apply(str(old_text), source=target))
            restored.append(str(target))
        except OSError as exc:                # noqa: BLE001 - 回滚失败只能记账
            from storygraph.core.safeguard import swallow
            swallow('story.control_route.rollback_wrapper_writes', exc)
    return restored


def plan_control_route(project_root, src_label, dst_label, etype='jump',
                       from_clause='') -> ControlRoutePlan:
    """
    算"从 `src_label` 章末接到 `dst_label`"在编排层里怎么写。**不碰磁盘。**

    返回的 `ok=False` 也要带 `reason`：界面要能说清"为什么没写编排层"，
    而不是悄悄退回源文件（那正是会跑两遍的写法）。
    """
    src = str(src_label or '').strip()
    dst = str(dst_label or '').strip()
    etype = str(etype or 'jump').strip() or 'jump'
    plan = ControlRoutePlan(src_label=src, dst_label=dst, etype=etype)

    if not src or not dst:
        return _block(plan, '起点或目标没有 label 名')
    if src == dst:
        return _block(plan, '起点和终点是同一章')
    if etype not in ('jump', 'call'):
        return _block(plan, '类型只能是 jump / call')

    found = _route_source_side(project_root, src, plan)
    if found is None:
        return plan
    text, block = found
    if not _route_target_side(project_root, dst, plan, text):
        return plan
    return _route_dispatch(plan, text, block, from_clause)


def _block(plan: ControlRoutePlan, reason: str) -> ControlRoutePlan:
    """把"不写"记在计划上（原因同时是 reason 与 blocking 的第一条）。"""
    plan.blocking.append(reason)
    plan.reason = reason
    return plan


def _route_source_side(project_root, src, plan):
    """
    找编排层文件、读它、确认 `src` 的 `_control` 包装在不在。

    返回 `(text, block)`；`None` 表示 `plan` 已经被写成"不写"，调用方直接
    返回它（原因与 blocking 都填好了）。
    """
    path = find_control_file(project_root, src)
    if path is None:
        plan.reason = ('项目里没有独立控制流文件（没有任何 `*_control` label）；'
                       '章末接线会写章节源文件 —— 那是另一条落点，不是这里')
        plan.blocking.append(plan.reason)
        return None
    plan.path = str(path)

    # 走 `_read`（= `scan_cache`）：一次接线里这份编排层会被算好几遍，
    # 文件没变就不该重新读盘（真机 1.2 MB 的项目里这一步是纯浪费）。
    text, _style = _read(path)
    if not text.strip():
        plan.reason = f'编排层文件是空的：{path.name}'
        plan.blocking.append(plan.reason)
        return None

    plan.control_label = control_label(src)
    block = _block_for(text, plan.control_label)
    if block is not None:
        return text, block
    # 源章还没有 `_control` 包装 —— 先补，再接线（2026-10-27 真机：
    # 以前这里一律拒写，于是"还没有进入口"的章节永远接不出线）。
    plan.src_init = plan_control_init(project_root, src, role='source')
    if (plan.src_init.ok and plan.src_init.kind == 'exists'
            and Path(plan.src_init.path) != Path(path)):
        # 同一 label 声明在两个编排层文件里：Ren'Py 自己就会报重复。
        plan.src_init.ok = False
        plan.src_init.kind = 'blocked'
        plan.src_init.reason = (
            f'`{plan.src_init.control_label}` 已经在另一个文件里'
            f'（{Path(plan.src_init.path).name}），但又不在这个编排层'
            f'文件里；同一 label 不能两处声明。先合并这两份编排层，再接线')
        plan.src_init.blocking.append(plan.src_init.reason)
    if plan.src_init.ok:
        # `init` 就是"接线前要先落地的那一步"：命令层先补、再重算、再写。
        plan.init = plan.src_init
        plan.reason = (f'编排层里还没有 `{plan.control_label}`；'
                       f'先把「{src}」的编排入口补上'
                       f'（{plan.src_init.reason}），再接这条线')
        plan.notes.extend(plan.src_init.notes)
    else:
        plan.reason = (f'编排层里没有 `{plan.control_label}`，'
                       f'而且补不了：{plan.src_init.reason}')
    plan.blocking.append(plan.reason)
    return None


def _route_target_side(project_root, dst, plan, text) -> bool:
    """
    定 `plan.target_label`；jump 的目标章缺包装就补（能补就把 init 带上）。

    返回 False = 目标这一侧走不通，`plan` 已经是"不写"。
    """
    # 目标 label：jump 去 `B_control`，call 去 `B`。
    plan.target_label = (control_label(dst) if plan.etype == 'jump' else dst)
    if plan.etype != 'jump' or _block_for(text, plan.target_label) is not None:
        return True
    # P2.5：目标章缺包装。补出来的 `jump B_control` 就是它的第一个入口，
    # 所以这一种可以安全补；补完再算一次路由（行号不变，只是文件变长）。
    plan.init = plan_control_init(project_root, dst, role='target')
    if (plan.init.ok and plan.init.kind == 'exists'
            and Path(plan.init.path) != Path(plan.path)):
        # 同一个 `_control` 出现在两个文件里：Ren'Py 本身就会报重复。
        # 这时候"已经有了"帮不上忙，让人先合并，别把路由写进另一个文件。
        plan.init.ok = False
        plan.init.kind = 'blocked'
        plan.init.reason = (
            f'`{plan.init.control_label}` 已经在另一个文件里'
            f'（{Path(plan.init.path).name}），但又不在这个编排层文件里；'
            f'同一 label 不能两处声明。先合并这两份编排层，再接线')
        plan.init.blocking.append(plan.init.reason)
    if plan.init.ok:
        plan.reason = (f'编排层里还缺 `{plan.target_label}`（还没有目标章'
                       f'包装）；可以先补上它（{plan.init.reason}），再接线')
    else:
        plan.reason = (f'编排层里没有 `{plan.target_label}`，'
                       f'而且补不了：{plan.init.reason}')
    plan.blocking.append(plan.reason)
    return False


def _route_dispatch(plan, text, block, from_clause):
    """最后一条语句决定怎么写：空块 / call / jump。"""
    lines = split_lines(text)
    tops = _top_lines(lines, block)
    if not tops:
        # 空 label 块：直接把路由写进去（`jump` 不需要 return；call 也不返回）。
        plan.kind = 'insert'
        plan.line_no = block['start'] + 2        # 1 基，紧跟 header
        plan.old_line = ''
        plan.new_lines = ['    ' + _render_route(plan.etype, plan.target_label,
                                                 from_clause)]
        plan.ok = True
        plan.reason = f'在空白的 `{plan.control_label}` 里写路由'
        return plan

    last_i, last_line = tops[-1]
    raw = last_line.strip()
    plan.old_route = raw
    plan.line_no = last_i + 1

    if raw.startswith('menu:') or raw.startswith('if ') \
            or raw.startswith('elif ') or raw.startswith('else:'):
        return _block(plan, f'`{plan.control_label}` 以「{raw}」收尾 —— 分支归'
                            f'控制流图/卡片，不能把一条线插进块体')
    route = _route_of(raw)
    if route and route[0] == 'dynamic':
        return _block(plan, f'`{plan.control_label}` 的最后一条是动态跳转'
                            f'（{raw}）—— 工具判不出目标，不往里写')
    if plan.etype == 'call':
        return _route_call(plan, lines, tops, raw, last_i, from_clause)
    return _route_jump(plan, lines, raw, route, last_i, from_clause)


def _route_call(plan, lines, tops, raw, last_i, from_clause):
    """`call` 这一档：已 call 过 / 已 jump 过去 / 插在最后一条路由之前。"""
    # 要在**整段**里查，不只看最后一条：上一轮已经插过 `call B`，
    # 后面还留着 `return` 时，最后一条仍是 return。
    routes = [_route_of(ln.strip()) for _i, ln in tops]
    if ('call', plan.target_label) in routes:
        plan.kind = 'exists'
        plan.ok = True
        plan.new_lines = []
        plan.reason = f'编排层已经 call 过 `{plan.target_label}`，无需再写'
        return plan
    dst_control = control_label(plan.dst_label)
    # 已经 jump 到同一章 → 再 call 一次会让目标章跑两遍。
    if ('jump', dst_control) in routes:
        return _block(plan, f'`{plan.control_label}` 已经 jump 到 '
                            f'`{dst_control}`；再插一条 call {plan.dst_label} '
                            f'会让这一章跑两遍')
    # call 不改接：插在最后一条路由之前；返回后继续走原来的去向。
    plan.kind = 'insert'
    plan.old_line = lines[last_i]
    plan.new_lines = ['    ' + _render_route('call', plan.target_label,
                                             from_clause)]
    plan.ok = True
    plan.reason = (f'在 `{plan.control_label}` 的「{raw}」之前接 '
                   f'call {plan.target_label}（返回后继续原路）')
    return plan


def _route_jump(plan, lines, raw, route, last_i, from_clause):
    """`jump` 这一档：已接上 / 改接原出口 / 替掉 return / 替掉终局语句。"""
    if route and route[0] == 'jump':
        if route[1] == plan.target_label:
            plan.kind = 'exists'
            plan.ok = True
            plan.new_lines = []
            plan.reason = f'编排层已经接到 `{plan.target_label}`，无需再写'
            return plan
        plan.kind = 'replace'
        plan.old_line = lines[last_i]
        plan.new_lines = ['    ' + _render_route('jump', plan.target_label,
                                                 from_clause)]
        plan.ok = True
        plan.reason = (f'改接：`{plan.control_label}` 原来是 '
                       f'{raw}，改成 jump {plan.target_label}')
        plan.notes.append('这会替换编排层里原有的出口；Ctrl+Z 可以放回')
        return plan
    if route and route[0] == 'return':
        # `jump` 不会回来，留着 return 是死代码；直接替换它（与导出器
        # 生成 `call A; jump B_control` 的形状一致）。
        plan.kind = 'replace'
        plan.old_line = lines[last_i]
        plan.new_lines = ['    ' + _render_route('jump', plan.target_label,
                                                 from_clause)]
        plan.ok = True
        plan.reason = f'把 `{plan.control_label}` 的 return 接成 ' \
                      f'jump {plan.target_label}'
        return plan

    # 编排层以**终局语句**收尾（`$ renpy.full_restart()` / `$ renpy.quit()`）：
    # 它自己就是"到此结束"，后面再插什么都执行不到 —— 新路由只能**替掉它**。
    # 这也正是把"这一章演完游戏就结束"改成"演完接着去 B"的唯一写法。
    #
    # 为什么必须认这一档（2026-10-27 真机）：旧版编辑器生成的编排层，最后一章
    # 的 `_control` 就是 `call 这一章` + `$ renpy.full_restart()`。于是
    # 「接上下一章」这个最自然的动作落到这里被判成"判不出安全的插入点"，
    # 对话框只能画一条草图 —— 用户原话："为什么只能生成草图？我明明没有用草稿"。
    terminal = _terminating_of(raw)
    if terminal:
        plan.kind = 'replace'
        plan.old_line = lines[last_i]
        plan.new_lines = ['    ' + _render_route('jump', plan.target_label,
                                                 from_clause)]
        plan.ok = True
        plan.reason = (f'改接：`{plan.control_label}` 原来以「{raw}」收尾'
                       f'（演完就结束），换成 jump {plan.target_label}')
        plan.notes.append('原来那条「结束」会被换掉；Ctrl+Z 可以放回')
        return plan

    return _block(plan, f'`{plan.control_label}` 的最后一条是「{raw}」—— '
                        f'判不出安全的插入点，不写')


def _render_route(etype: str, target: str, from_clause: str = '') -> str:
    line = f'{etype} {target}'
    clause = str(from_clause or '').strip()
    if etype == 'call' and clause:
        line += f' from {clause}'
    return line


def apply_control_route(plan: ControlRoutePlan) -> dict:
    """
    真的把计划写进编排层文件（**只改这一行**，原子替换）。

    返回 `{ok, path, old_text, new_text, line_no, error}`。
    `old_text` / `new_text` 给撤销命令用；调用方不该自己再读一遍文件。
    """
    if not isinstance(plan, ControlRoutePlan) or not plan.ok:
        return {'ok': False, 'path': '', 'old_text': '', 'new_text': '',
                'line_no': 0, 'error': '计划无效'}
    path = Path(plan.path)
    from storygraph.story.source_style import read_text
    text, style = read_text(path)
    if not text.strip():
        return {'ok': False, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': 0,
                'error': '编排层文件是空的'}

    lines = split_lines(text)
    idx = int(plan.line_no) - 1
    if not (0 <= idx <= len(lines)):
        return {'ok': False, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': plan.line_no,
                'error': '计划里的行号已经越界；重新算一次'}

    if plan.kind == 'exists':
        return {'ok': True, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': plan.line_no, 'error': ''}

    if plan.kind == 'insert':
        if plan.old_line:
            if idx >= len(lines) or lines[idx] != plan.old_line:
                return {'ok': False, 'path': str(path), 'old_text': text,
                        'new_text': text, 'line_no': plan.line_no,
                        'error': '编排层刚被改过（锚点行对不上）；重新接线'}
            out = lines[:idx] + list(plan.new_lines) + lines[idx:]
        else:
            out = lines[:idx] + list(plan.new_lines) + lines[idx:]
    elif plan.kind == 'replace':
        if idx >= len(lines) or lines[idx] != plan.old_line:
            return {'ok': False, 'path': str(path), 'old_text': text,
                    'new_text': text, 'line_no': plan.line_no,
                    'error': '编排层刚被改过（要替换的行对不上）；重新接线'}
        out = lines[:idx] + list(plan.new_lines) + lines[idx + 1:]
    else:
        return {'ok': False, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': plan.line_no,
                'error': f'不认识的计划类型：{plan.kind}'}

    # 保留原文件"末尾有没有换行"：没有就别硬加。
    body = '\n'.join(out)
    if text.endswith('\n'):
        body += '\n'
    try:
        from storygraph.story.paths import write_text_atomic
        write_text_atomic(path, style.apply(body, source=path))
    except SourceNotUtf8 as exc:
        # 非 UTF-8：读的时候坏字节已变成 U+FFFD，写回去就是改坏它（P0-B）
        return {'ok': False, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': plan.line_no,
                'error': str(exc)}
    except OSError as exc:
        return {'ok': False, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': plan.line_no,
                'error': f'写不进编排层：{exc}'}
    return {'ok': True, 'path': str(path), 'old_text': text,
            'new_text': body, 'line_no': plan.line_no, 'error': ''}


def plan_control_unroute(project_root, src_label, dst_label,
                         etype='jump') -> ControlRoutePlan:
    """
    算"删掉编排层里的那条路由"（只删一行，必要时补 `return`）。不碰磁盘。

    用途：删图上的编排线。找不到/有多条 → `ok=False`，不猜。
    """
    src = str(src_label or '').strip()
    dst = str(dst_label or '').strip()
    etype = str(etype or 'jump').strip() or 'jump'
    plan = ControlRoutePlan(src_label=src, dst_label=dst, etype=etype,
                            kind='remove')
    path = find_control_file(project_root, src)
    if path is None:
        plan.reason = '项目里没有独立控制流文件'
        plan.blocking.append(plan.reason)
        return plan
    plan.path = str(path)
    from storygraph.story.source_style import read_text
    text, _style = read_text(path)
    plan.control_label = control_label(src)
    block = _block_for(text, plan.control_label)
    if block is None:
        plan.reason = f'编排层里没有 `{plan.control_label}`'
        plan.blocking.append(plan.reason)
        return plan
    plan.target_label = (control_label(dst) if etype == 'jump' else dst)
    lines = split_lines(text)
    tops = _top_lines(lines, block)
    matches = [(i, ln) for i, ln in tops
               if _route_of(ln.strip()) == (etype, plan.target_label)]
    if not matches:
        plan.reason = (f'`{plan.control_label}` 里找不到 '
                       f'{etype} {plan.target_label}')
        plan.blocking.append(plan.reason)
        return plan
    if len(matches) > 1:
        plan.reason = (f'`{plan.control_label}` 里有 {len(matches)} 条 '
                       f'{etype} {plan.target_label}，不知道删哪一条')
        plan.blocking.append(plan.reason)
        return plan
    idx, old = matches[0]
    plan.line_no = idx + 1
    plan.old_line = old
    plan.old_route = old.strip()
    # 删掉最后一条 jump 后，label 会"掉"进下一个 label；补一个 return 收尾。
    new_lines = []
    if etype == 'jump':
        last_route = tops[-1][0] if tops else -1
        if idx == last_route and _route_of(tops[-1][1].strip())[0] == 'jump':
            new_lines = ['    return']
    plan.new_lines = new_lines
    plan.ok = True
    plan.reason = f'删掉 `{plan.control_label}` 里的 {old.strip()}'
    return plan


def apply_control_unroute(plan: ControlRoutePlan) -> dict:
    """真的删掉编排层里那一行（原子替换）。返回形状同 `apply_control_route`。"""
    if not isinstance(plan, ControlRoutePlan) or not plan.ok:
        return {'ok': False, 'path': '', 'old_text': '', 'new_text': '',
                'line_no': 0, 'error': '计划无效'}
    path = Path(plan.path)
    from storygraph.story.source_style import read_text
    text, style = read_text(path)
    lines = split_lines(text)
    idx = int(plan.line_no) - 1
    if not (0 <= idx < len(lines)) or lines[idx] != plan.old_line:
        return {'ok': False, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': plan.line_no,
                'error': '编排层刚被改过（要删的行对不上）；重新算一次'}
    out = lines[:idx] + list(plan.new_lines) + lines[idx + 1:]
    body = '\n'.join(out)
    if text.endswith('\n'):
        body += '\n'
    try:
        from storygraph.story.paths import write_text_atomic
        write_text_atomic(path, style.apply(body, source=path))
    except SourceNotUtf8 as exc:
        # 非 UTF-8：读的时候坏字节已变成 U+FFFD，写回去就是改坏它（P0-B）
        return {'ok': False, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': plan.line_no,
                'error': str(exc)}
    except OSError as exc:
        return {'ok': False, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': plan.line_no,
                'error': f'写不进编排层：{exc}'}
    return {'ok': True, 'path': str(path), 'old_text': text,
            'new_text': body, 'line_no': plan.line_no, 'error': ''}


@dataclass
class ControlEditPlan:
    """改一条**已有**编排路由（类型 / 目标）的纯计划（不碰磁盘）。"""

    ok: bool = False
    #: `replace` / `noop` / `blocked`
    kind: str = 'blocked'
    path: str = ''
    src_label: str = ''
    control_label: str = ''
    old_dst_label: str = ''
    old_etype: str = 'jump'
    new_dst_label: str = ''
    new_etype: str = 'jump'
    #: 要替换的那一行（原文，含缩进）与 1 基行号
    old_line: str = ''
    line_no: int = 0
    new_lines: list = field(default_factory=list)
    reason: str = ''
    blocking: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    #: 新目标是 jump、但编排层里还没有 `C_control` 时的补包装计划
    init: object = None

    def to_dict(self) -> dict:
        return {
            'ok': self.ok, 'kind': self.kind, 'path': self.path,
            'src_label': self.src_label,
            'control_label': self.control_label,
            'old_dst_label': self.old_dst_label, 'old_etype': self.old_etype,
            'new_dst_label': self.new_dst_label, 'new_etype': self.new_etype,
            'old_line': self.old_line, 'line_no': self.line_no,
            'new_lines': list(self.new_lines), 'reason': self.reason,
            'blocking': list(self.blocking), 'notes': list(self.notes),
            'init': self.init.to_dict() if self.init is not None else None,
        }


def plan_control_edit(project_root, src_label, old_dst_label,
                      old_etype='jump', new_dst_label=None,
                      new_etype=None) -> ControlEditPlan:
    """
    算"把编排层里这条路由改成新类型 / 新目标"该怎么写。**不碰磁盘。**

    用户 2026-10-24：双击编排线，窗口里类型/到全是灰的，只能"打开编排层
    文件"自己找那一行。这里把那一行**原地改掉**，不整份重写：

      · `jump B_control` → `jump C_control`：只换目标；
      · `call B` → `call C`：只换目标（`from` / 参数原样保留）；
      · `jump B_control` → `call C`：换成 `call C`；若它原来是这一段的
        **最后一条**，再补一个 `return` —— call 会回来，没有下一句就会
        掉进下一个 label；
      · `call B` → `jump C_control`：只有它后面再没有顶层语句时才允许
        （否则后面的路由会变成永远不执行的死代码，不能替人拍板）。

    新目标是 `jump` 时必须有 `C_control` 包装；缺了就带一个 `init` 补包装
    计划（与"添加连线"同一条规则），命令先补再改、撤销一并回滚。
    """
    src = str(src_label or '').strip()
    old_dst = str(old_dst_label or '').strip()
    old_etype = str(old_etype or 'jump').strip() or 'jump'
    new_dst = str(new_dst_label or old_dst).strip()
    new_etype = str(new_etype or old_etype).strip() or 'jump'
    plan = ControlEditPlan(src_label=src, old_dst_label=old_dst,
                           old_etype=old_etype, new_dst_label=new_dst,
                           new_etype=new_etype)
    if not src or not old_dst or not new_dst:
        plan.reason = '起点 / 原目标 / 新目标里有一个没有 label 名'
        plan.blocking.append(plan.reason)
        return plan
    if src == new_dst:
        plan.reason = '不能把这一章接到它自己（那会是个死循环）'
        plan.blocking.append(plan.reason)
        return plan
    if old_etype not in ('jump', 'call') or new_etype not in ('jump', 'call'):
        plan.reason = '类型只能是 jump / call'
        plan.blocking.append(plan.reason)
        return plan

    path = find_control_file(project_root, src)
    if path is None:
        plan.reason = '项目里没有独立控制流文件（编排层）'
        plan.blocking.append(plan.reason)
        return plan
    plan.path = str(path)
    try:
        text, _style = _read(path)
    except OSError as exc:
        plan.reason = f'读不了编排层：{exc}'
        plan.blocking.append(plan.reason)
        return plan
    plan.control_label = control_label(src)
    block = _block_for(text, plan.control_label)
    if block is None:
        plan.reason = f'编排层里没有 `{plan.control_label}`'
        plan.blocking.append(plan.reason)
        return plan

    old_target = control_label(old_dst) if old_etype == 'jump' else old_dst
    lines = split_lines(text)
    tops = _top_lines(lines, block)
    matches = [(i, ln) for i, ln in tops
               if _route_of(ln.strip()) == (old_etype, old_target)]
    if not matches:
        plan.reason = (f'`{plan.control_label}` 里找不到 '
                       f'{old_etype} {old_target}')
        plan.blocking.append(plan.reason)
        return plan
    if len(matches) > 1:
        plan.reason = (f'`{plan.control_label}` 里有 {len(matches)} 条 '
                       f'{old_etype} {old_target}，不知道改哪一条')
        plan.blocking.append(plan.reason)
        return plan

    idx, old_line = matches[0]
    plan.line_no = idx + 1
    plan.old_line = old_line
    indent = old_line[:len(old_line) - len(old_line.lstrip())]
    new_target = control_label(new_dst) if new_etype == 'jump' else new_dst
    is_last_top = bool(tops) and tops[-1][0] == idx

    if new_etype == old_etype and new_dst == old_dst:
        plan.kind = 'noop'
        plan.ok = True
        plan.reason = '类型和目标都没变'
        return plan

    if new_etype == 'call' and _label_decl(project_root, new_dst) is None:
        plan.reason = (f'项目里找不到 `label {new_dst}` —— call 一个不存在的'
                       f' label，Ren\'Py 会直接报错')
        plan.blocking.append(plan.reason)
        return plan

    if new_etype == 'jump':
        if _block_for(text, new_target) is None:
            plan.init = plan_control_init(project_root, new_dst,
                                          role='target')
            if plan.init.kind == 'exists' \
                    and Path(plan.init.path) != Path(path):
                plan.reason = (f'`{new_target}` 在另一个文件里'
                               f'（{Path(plan.init.path).name}），'
                               f'不在这个编排层里；先合并再改')
                plan.blocking.append(plan.reason)
                return plan
            if not plan.init.ok:
                plan.reason = (f'编排层里没有 `{new_target}`，而且补不了：'
                               f'{plan.init.reason}')
                plan.blocking.append(plan.reason)
                return plan
        if old_etype == 'call' and not is_last_top:
            plan.reason = ('这条 call 后面还有路由；改成 jump 会让它们'
                           '永远不执行 —— 先把后面那些删掉再来改')
            plan.blocking.append(plan.reason)
            return plan
        plan.kind = 'replace'
        plan.ok = True
        plan.new_lines = [f'{indent}jump {new_target}']
        if old_etype == 'call' and is_last_top:
            plan.reason = (f'把 `{plan.control_label}` 的 {old_line.strip()} '
                           f'改成 jump {new_target}（call 会回来；它原来是'
                           f'最后一句，所以直接跳到目标章）')
        else:
            plan.reason = (f'改接：`{plan.control_label}` 里的 '
                           f'{old_line.strip()} → jump {new_target}')
        return plan

    # new_etype == 'call'
    if old_etype == 'jump':
        plan.new_lines = [f'{indent}call {new_target}']
        if is_last_top:
            # call 会回来；这一段没有下一句 → 补 return 收尾，否则掉进下一个 label
            plan.new_lines.append(f'{indent}return')
            plan.reason = (f'把 `{plan.control_label}` 的 {old_line.strip()} '
                           f'改成 call {new_target} + return'
                           f'（调过去，回来就结束这一段）')
        else:
            plan.reason = (f'把 `{plan.control_label}` 的 {old_line.strip()} '
                           f'改成 call {new_target}（返回后继续后面那几条路由）')
    else:
        old_info = {}
        try:
            from storygraph.story.flow import parse_flow_line
            old_info = parse_flow_line(old_line.strip()) or {}
        except Exception as exc:              # noqa: BLE001 - 只是保留参数/from
            from storygraph.core.safeguard import swallow
            swallow('story.control_route.plan_control_edit.parse', exc)
            old_info = {}
        args = str(old_info.get('args') or '')
        frm = str(old_info.get('from_clause') or '')
        line = f'call {new_target}{args}'
        if frm:
            line += f' from {frm}'
        plan.new_lines = [indent + line]
        plan.reason = (f'把 `{plan.control_label}` 里的 {old_line.strip()} '
                       f'改成 {line.strip()}（存档返回点保留）')
    plan.kind = 'replace'
    plan.ok = True
    return plan


def apply_control_edit(plan: ControlEditPlan) -> dict:
    """真的把那一行换掉（原子替换）。返回形状同 `apply_control_route`。"""
    if not isinstance(plan, ControlEditPlan) or not plan.ok:
        return {'ok': False, 'path': '', 'old_text': '', 'new_text': '',
                'line_no': 0, 'error': '计划无效'}
    path = Path(plan.path)
    from storygraph.story.source_style import read_text
    try:
        text, style = read_text(path)
    except OSError as exc:
        return {'ok': False, 'path': str(path), 'old_text': '',
                'new_text': '', 'line_no': plan.line_no,
                'error': f'读不了编排层：{exc}'}
    if plan.kind == 'noop':
        return {'ok': True, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': plan.line_no, 'error': ''}
    if plan.init is not None and str(getattr(plan.init, 'kind', '')) == 'append':
        return {'ok': False, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': plan.line_no,
                'error': '新目标还缺 `_control` 包装：先补包装，再改这一行'}
    lines = split_lines(text)
    idx = int(plan.line_no) - 1
    if not (0 <= idx < len(lines)) or lines[idx] != plan.old_line:
        return {'ok': False, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': plan.line_no,
                'error': '编排层刚被改过（要替换的行对不上）；重新算一次'}
    out = lines[:idx] + list(plan.new_lines) + lines[idx + 1:]
    body = '\n'.join(out)
    if text.endswith('\n'):
        body += '\n'
    try:
        from storygraph.story.paths import write_text_atomic
        write_text_atomic(path, style.apply(body, source=path))
    except SourceNotUtf8 as exc:
        # 非 UTF-8：读的时候坏字节已变成 U+FFFD，写回去就是改坏它（P0-B）
        return {'ok': False, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': plan.line_no,
                'error': str(exc)}
    except OSError as exc:
        return {'ok': False, 'path': str(path), 'old_text': text,
                'new_text': text, 'line_no': plan.line_no,
                'error': f'写不进编排层：{exc}'}
    return {'ok': True, 'path': str(path), 'old_text': text,
            'new_text': body, 'line_no': plan.line_no, 'error': ''}


def wire_plan_writable(plan) -> bool:
    """
    「接线落点计划」**能不能真的写进去** —— 这条判据只有这一份。

    输入是 `window_nodes.plan_wire_landing(...)` 的返回值（`kind` 见那里的
    表）。`ok=True` 的一律能写；`kind='control_init'` **虽然 `ok=False`**
    也能写 —— 它的含义是"还没写，因为目标章缺 `_control` 包装；先补包装
    再写路由"，而 `_apply_control_route` 本来就接得住这条计划。

    ## 为什么必须只有一份（2026-10-27 真机）

    对话框 `AddEdgeDialog._on_accept` 早就认 `control_init`（所以「创建」
    键按得下去、窗口正常关闭），窗口层 `_write_dialog_edge` 却只看
    `plan['ok']` —— 于是"目标章还没有 `_control` 包装"的那些线：

        Shift+左键拖出来 → 弹窗 → 点「创建」→ 窗口关闭
        → **图上一条线都没有**，只有状态栏一行转瞬即逝的字

    用户原话："我连线了好多次……没有显示"。真机项目（`sr_general`）
    7 个新章节里 42 对连线有 **12 对**落在这条死路上 —— 恰好都是
    "接到刚写、还没进编排层的新章"这种最常见的接线。

    判据写在这里、调用方（对话框 / 窗口层 / 草图转正）都引用它，
    就是为了不再出现"对话框说能写、窗口说写不了"。
    """
    if not isinstance(plan, dict):
        return False
    if plan.get('ok'):
        return True
    return str(plan.get('kind') or '') == 'control_init'


__all__ = [
    'CONTROL_SUFFIX', 'ControlEditPlan', 'ControlInitPlan',
    'ControlRoutePlan', 'INIT_CHAIN_ROUNDS', 'apply_control_edit',
    'apply_control_init', 'apply_control_route', 'apply_control_unroute',
    'apply_init_chain', 'control_label', 'find_control_file',
    'plan_control_edit', 'plan_control_init', 'plan_control_route',
    'plan_control_unroute', 'rollback_wrapper_writes', 'wire_plan_writable',
]
