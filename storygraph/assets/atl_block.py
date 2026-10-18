"""
storygraph.assets.atl_block
===========================
`image 名:` + 块体（**ATL / 块形式**的图像声明）的**只读定位**。

为什么只读、不做编辑（用户 2026-10-19 定的线）
--------------------------------------------
`image logo blink:` 这类块体是程序写的动画（`contains` / 表达式 / 多图层），
策划改不动；程序也不需要卡片 —— 他直接开 `.rpy`。给它一个多行文本框
（"改完写回文件级保真表"）会立刻造出**第二个真源**：资源表一份、源文件一份，
谁赢说不清 —— 而这个项目最贵的教训全部来自双真源。

所以这里只做三件事：**找到那一块、读出块体给人看、给出源文件行号让人一键打开**。
块体本身仍然只存在于 `.rpy` 里（唯一真源），这里每次都是**现读**、不缓存。

判据（可复跑）
-------------
· `locate_block()` 只认"块头下一段**缩进更深**的行"，遇到同缩进/更浅的行就停 ——
  与 Ren'Py 的块结构同一口径；
· 读不出来（文件搬走 / 行号过期 / 编码坏）时**不猜**，返回 `reason` 让人看见；
· 不认识 ATL 语法（不做语义分析）：这里给的永远是**原文**。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ['AtlBlock', 'block_of_row', 'is_atl_row', 'locate_block',
           'source_of_row', 'MAX_BODY_LINES']

#: 块体最多显示多少行（够看清定义；再长也只是"贴图看源文件"）
MAX_BODY_LINES = 40

#: Notes 里那句标记（`beat_build` 写进去的）：有它 = 这一条是 ATL 块形式
ATL_NOTE_MARK = 'ATL 定义'


def _indent_of(line: str) -> int:
    """缩进宽度（tab 按 4 算 —— 与 Ren'Py 默认的 tab 口径一致）。"""
    n = 0
    for ch in line:
        if ch == ' ':
            n += 1
        elif ch == '\t':
            n += 4
        else:
            break
    return n


def _read_text(path: Path) -> str:
    """
    读一段源码文本（UTF-8 优先，BOM 自动剥；GBK 兜底）。

    读不出来**原样抛出**：调用方要能把"读不出来"说出来，而不是拿一份空文本
    假装"这块是空的"。
    """
    raw = path.read_bytes()
    for enc in ('utf-8-sig', 'utf-8', 'gbk'):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if '\x00' in text:
            continue
        return text
    raise UnicodeDecodeError('utf-8', raw, 0, min(1, len(raw)),
                             f'既不是 UTF-8 也不是 GBK：{path}')


@dataclass(frozen=True)
class AtlBlock:
    """
    一块 `image 名:` 的定位结果。

    `file` 是**绝对路径**（找不到时保留候选路径，配合 `reason` 说明为什么）；
    `line` 是块头那一行的 1-based 行号；`body` 是去掉基准缩进后的块体。
    """

    name: str = ''
    file: str = ''
    line: int = 0
    body: tuple = ()
    reason: str = ''

    @property
    def found(self) -> bool:
        """块体读到了吗（`reason` 非空 = 没读到，且原因已经写成人话）。"""
        return not self.reason and bool(self.body)

    def location(self) -> str:
        """`defs.rpy:12`（只给文件名 + 行号；完整路径在 tooltip 里）。"""
        if not self.file:
            return ''
        return f'{Path(self.file).name}:{self.line}' if self.line else Path(self.file).name

    def text(self, indent: str = '    ') -> str:
        """块体 → 一段可直接显示的文本（每行同一个缩进）。"""
        return '\n'.join(indent + ln for ln in self.body)


def is_atl_row(row) -> bool:
    """
    这一行是不是"ATL 块形式"的图片声明（`image 名:` + 块体，贴图在块里）。

    `_atl` 是解析器/`beat_build` 新写的标记；老表没有它时看 `Notes` 里那句
    标记 + `Path` 为空 —— 两个条件都要，避免把"有贴图但备注里提到 ATL"的行
    误判成 ATL（那会把一张真的图说成"没有贴图"）。
    """
    row = row or {}
    if row.get('_atl'):
        return True
    return (not str(row.get('Path') or '').strip()
            and ATL_NOTE_MARK in str(row.get('Notes') or ''))


def source_of_row(row) -> tuple:
    """
    资源表的一行 → `(源文件, 行号)`；没有来源信息返回 `('', 0)`。

    两个来源：
      1. `_source_file` / `_source_line`（`beat_build` 新写的，最准）；
      2. `Notes` 里 `文件:行号`（老表 / 人手动写的，兜底）。

    ⚠ 不要拿 `Path`（贴图）当源文件：ATL 块形式本来就是 `Path=''`。
    """
    row = row or {}
    f = str(row.get('_source_file') or '').strip()
    try:
        line = int(row.get('_source_line') or 0)
    except (TypeError, ValueError):
        line = 0
    if f:
        return f, line
    note = str(row.get('Notes') or '').strip()
    if note:
        head = note.split('；')[0].split(';')[0].strip()
        # Windows 路径里也有 `:`，所以从**右**边切一次
        if ':' in head:
            maybe_file, _, maybe_line = head.rpartition(':')
            if maybe_line.strip().isdigit():
                return maybe_file.strip(), int(maybe_line.strip())
    return '', 0


def locate_block(file, line: int, *, max_lines: int = MAX_BODY_LINES) -> tuple:
    """
    读 `file` 第 `line` 行（块头）下面那一块 → `(body, reason)`。

    `body` 是**去基准缩进**后的行（空行保留在中间，尾部空行去掉）；
    `reason` 非空表示没读到，原因已经写成一句人话（文件不在了 / 行号越界 /
    读不出来 / 那一行后面没有缩进块）。

    只认"缩进更深"的行：这与 Ren'Py 的块结构同一口径，也是"块到哪儿结束"
    唯一可靠的判据（空行不算结束 —— 块里可以有空行）。
    """
    if not file:
        return (), '资源表里这一条没有记源文件位置'
    p = Path(file)
    if not p.exists():
        return (), f'源文件不在了：{p.name}（可能已改名或搬出项目）'
    try:
        text = _read_text(p)
    except OSError as e:
        return (), f'源文件读不出来：{e}'
    except UnicodeDecodeError as e:
        return (), f'源文件编码认不出：{e}'
    lines = text.splitlines()
    if not lines:
        return (), f'源文件是空的：{p.name}'
    if line < 1 or line > len(lines):
        return (), f'行号对不上（{p.name} 只有 {len(lines)} 行，表里记的是第 {line} 行）'
    head = lines[line - 1]
    base = _indent_of(head)
    out = []
    for raw in lines[line:]:
        stripped = raw.strip()
        if not stripped:
            out.append('')
            continue
        if _indent_of(raw) <= base:
            break
        out.append(raw)
    # 尾部空行不算块体
    while out and not out[-1].strip():
        out.pop()
    if not out:
        return (), f'{p.name}:{line} 那一行后面没有缩进块（不是块形式？）'
    # 去基准缩进（块里常见的"每行都多缩一层"）
    indents = [_indent_of(x) for x in out if x.strip()]
    cut = min(indents) if indents else 0
    body = []
    for raw in out:
        if not raw.strip():
            body.append('')
        else:
            # 按字符切：tab 的宽度是显示层的事，这里只去公共前缀
            i = 0
            width = 0
            while i < len(raw) and width < cut and raw[i] in ' \t':
                width += 4 if raw[i] == '\t' else 1
                i += 1
            body.append(raw[i:])
    if len(body) > max_lines:
        rest = len(body) - max_lines
        body = body[:max_lines] + [f'…（还有 {rest} 行，打开源文件看）']
    return tuple(body), ''


def block_of_row(row, project_root=None, *,
                 max_lines: int = MAX_BODY_LINES) -> AtlBlock:
    """
    资源表的一行（`_atl` 的 image 行）→ `AtlBlock`。

    `project_root` 用来把表里的相对路径拼成绝对路径（表里存 `game/defs.rpy`
    或 `defs.rpy` 两种都见过）。
    """
    row = row or {}
    name = str(row.get('ID') or row.get('name') or '').strip()
    src, line = source_of_row(row)
    if not src:
        return AtlBlock(name=name, reason='资源表里这一条没有记源文件位置')
    path = _resolve_source(src, project_root)
    body, reason = locate_block(path, line, max_lines=max_lines)
    return AtlBlock(name=name, file=str(path), line=line, body=body,
                    reason=reason)


def _resolve_source(src: str, project_root=None) -> Path:
    """
    表里的源文件路径 → 项目里**真实存在**的那个候选（都不存在就用第一个候选）。

    为什么试这么多种：`ParseResult` 里的 `source_file` 可能是绝对路径，也可能是
    `game/defs.rpy`、`defs.rpy`（取决于调用方怎么传的）。拼错就永远"找不到源文件"，
    而这条链上没有人会告诉你 —— 所以这里把见过的形态都试一遍。
    """
    p = Path(str(src))
    if p.is_absolute():
        return p
    root = Path(project_root) if project_root else None
    cands = []
    if root is not None:
        cands += [root / p, root / 'game' / p]
        if p.parts and p.parts[0] == 'game':
            cands.append(root / Path(*p.parts[1:]))
        cands.append(root.parent / p)
    else:
        cands.append(p)
    for c in cands:
        try:
            if c.exists():
                return c
        except OSError:
            continue
    return cands[0] if cands else p
