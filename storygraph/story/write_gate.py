# -*- coding: utf-8 -*-
"""
storygraph.story.write_gate
===========================
写入前的**三态判定**：采纳 / rebase / 真冲突。

为什么不是"哈希不符就拒绝"（二态）
----------------------------------
`touch`、`git checkout`、备份工具重写都会改 mtime 甚至整个文件时间戳，
**内容一个字没变**。每次都弹冲突，用户很快就学会无视它 —— 那就等于没有检查。
决策文档 §6.3b 把"校验失败"拆成三种情形分别处理：

| 磁盘当前内容 | 判定 | 处理 |
|---|---|---|
| 等于本次要写的**草稿** | `ADOPT` | 直接采纳（盘上已经是这个内容） |
| 等于**基线**（只是被 touch） | `REBASE` | 不算冲突，推进基线后按正常路写 |
| 与基线、草稿**都不同** | `CONFLICT` | 才算冲突，交给人 |

还有一条同样重要：**冲突只在写入时判定**（不打断正在编辑的人）。
所以这是个纯函数：调用方在真要写的那一刻才问它。

签名现在定死（§8.4 第 2 条）
---------------------------
    classify(disk_bytes, base_sha1, draft_text, base_text=None)

以后加 base（原文快照）时**只多传一个参数**，调用方不用改：
`base_text` 给了 → 多一条"自动三路合并"的候选（`merged_text`），
但那仍然属于第三态 —— **合不合并由人决定**，工具不替人拍板。

`commit_text()`（2026-11-08 第十四轮）是"判定 + 落盘"那一步
--------------------------------------------------------------
上一段话留了一个洞：`classify(..., base_text=)` 从 2026-10-31 写到现在，
**生产代码里一个调用方都没有**（探针 `probe_r14_stale_edit.py` 实测：
`base_text=` 只出现在本文件里，`merge_text` 只有测试在调）。
于是"别人改了别处"时，所有原地小改家族只剩一条路：**拒写**，
用户改不了、还得重新载入再来一遍 —— 而这明明是能合的。

`commit_text(path, base_text, draft_text)` 把三件事收成一处：
**读现在这份 → 判定 → （必要时）合并 → 落盘只走 `paths.write_text_atomic`**。
结局四种（不是三种）：`same` / `wrote` / `merged` / `conflict`。

为什么要 `same` 这一种：盘上已经是草稿时再写一遍，会白烧一代历史版本、
让 mtime 跳一下 —— 这正是第十轮给 `ext.registry.save` 立的规矩
（"一样就不碰"）。从前这里会判成 `CONFLICT` 然后拒写，比现在还差。

为什么合并**可以自动做**（不弹框问）：
  · 只有两边的改动**不重叠**才合（`merge_text` 的判据），重叠一律拒写；
  · 合完必须**说出来**（`Commit.note()` → 状态栏），不是静默；
  · `Commit.final_text` **不许**拿去当撤销的"期望内容" —— 撤销要的是
    我写的草稿（`draft_text`）。拿着合并结果去比，`undo` 那道闸就会
    "盘上还是我写的那份"放行，然后把别人那几行一起抹掉。这条在
    `Commit` 的字段注释里也写了一遍，因为它是这一轮最容易踩的坑。
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass

ADOPT = 'adopt'
REBASE = 'rebase'
CONFLICT = 'conflict'

#: 三态 → 人话（报告/状态栏共用；不许各写一遍）。
STATE_CN = {
    ADOPT: '采纳（盘上已经是要写的内容）',
    REBASE: 'rebase（内容仍是基线，只是元数据变了）',
    CONFLICT: '真冲突（和基线、草稿都不同）',
}


def sha1_16(data) -> str:
    """内容 → 16 位 sha1（与 `table_meta.来源指纹` / `_fingerprint_of` 同一个口径）。"""
    if isinstance(data, str):
        data = data.encode('utf-8')
    return hashlib.sha1(bytes(data or b'')).hexdigest()[:16]


@dataclass(frozen=True)
class Verdict:
    """一次判定的结果（纯数据，能直接进日志/状态栏）。"""

    state: str = CONFLICT
    disk_sha1: str = ''
    base_sha1: str = ''
    draft_sha1: str = ''
    detail: str = ''
    #: 只有给了 `base_text` 且两边改动不重叠时才有值 —— 它是"合并"那个选项的
    #: 候选内容，不是"可以直接写"的结论（仍要人确认）。
    merged_text: str = ''

    @property
    def ok(self) -> bool:
        """前两态都算"不冲突"，可以按正常路写。"""
        return self.state in (ADOPT, REBASE)

    @property
    def is_conflict(self) -> bool:
        return self.state == CONFLICT

    def sentence(self) -> str:
        text = STATE_CN.get(self.state, self.state)
        return f'{text}：{self.detail}' if self.detail else text

    def as_dict(self) -> dict:
        return {'state': self.state, 'disk_sha1': self.disk_sha1,
                'base_sha1': self.base_sha1, 'draft_sha1': self.draft_sha1,
                'detail': self.detail, 'merged': bool(self.merged_text)}


def classify(disk_bytes, base_sha1, draft_text, base_text=None) -> Verdict:
    """
    磁盘上那份 vs 基线 vs 本次要写的草稿 → 三态之一。

      · `disk_bytes`：磁盘上现在的**原始字节**（读不到就给 `b''`，
        调用方自己决定那算不算冲突；这里只做判定，不猜"文件不在了"的语义）；
      · `base_sha1`：载入/摊表那一刻记下的指纹（16 位；空 = 没有基线）；
      · `draft_text`：本次准备写进去的正文；
      · `base_text`：**可选**，原文快照 —— 有它才能自动三路合并。
    """
    disk = sha1_16(disk_bytes)
    draft = sha1_16(draft_text)
    base = str(base_sha1 or '').strip()

    if disk == draft:
        return Verdict(ADOPT, disk, base, draft, '盘上已经是要写的内容')
    if base and disk == base:
        return Verdict(REBASE, disk, base, draft,
                       '内容仍是载入时那份（只是被 touch / 元数据变了）')

    detail = '磁盘上的内容既不是基线、也不是本次要写的那份'
    merged = ''
    if base_text is not None:
        merged = merge_text(base_text,
                            bytes(disk_bytes or b'').decode('utf-8', 'replace'),
                            draft_text)
        detail += ('；有一份自动合并的候选（要人确认）' if merged
                   else '；两边的改动重叠，自动合并也做不到')
    return Verdict(CONFLICT, disk, base, draft, detail, merged)


# ---------------------------------------------------------------------------
#  三路合并（只有在 `base_text` 给了以后才会被调用）
# ---------------------------------------------------------------------------

def edits_between(base_lines, other_lines) -> list:
    """`base` → `other` 的改动段：`[(起, 止, 新行), …]`（0 基，止不含）。

    2026-11-08 第十四轮从私有 `_edits` 提为公开：`commit_text` 要把
    "别人改了哪几段"翻译成人话（`Kept`），两边必须**同一份** diff ——
    各写一遍就会出现"报的行号和实际合并的行号对不上"。
    """
    matcher = difflib.SequenceMatcher(a=base_lines, b=other_lines,
                                      autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        out.append((i1, i2, list(other_lines[j1:j2])))
    return out


def _touch(a, b) -> bool:
    """
    两段改动在基线上是否**碰到同一处**（碰到就不许自动合并）。

    插入（起 == 止）当成一个点：落在别人替换段里、或两个插入在同一点，
    都算冲突 —— 宁可让人二选一，也别猜一个顺序。
    """
    a1, a2 = int(a[0]), int(a[1])
    b1, b2 = int(b[0]), int(b[1])
    if a1 == a2 and b1 == b2:
        return a1 == b1
    if a1 == a2:
        return b1 <= a1 <= b2
    if b1 == b2:
        return a1 <= b1 <= a2
    return max(a1, b1) < min(a2, b2)


def merge_text(base_text, disk_text, draft_text) -> str:
    """
    两边对同一份 base 的改动**不重叠**时 → 合并结果；否则 `''`（= 合不了）。

    行级合并（`difflib` 的 opcode），不逐字符：这个项目的文本单位就是"行"，
    逐字符合并会把缩进/引号拼成谁也没写过的样子。
    """
    base_text = str(base_text if base_text is not None else '')
    base = base_text.splitlines()
    disk = str(disk_text if disk_text is not None else '').splitlines()
    draft = str(draft_text if draft_text is not None else '').splitlines()

    left = edits_between(base, disk)
    right = edits_between(base, draft)
    for a in left:
        for b in right:
            if _touch(a, b):
                return ''

    merged: list = []
    pos = 0
    for start, end, lines in sorted(left + right,
                                    key=lambda e: (e[0], e[0] == e[1])):
        merged.extend(base[pos:start])
        merged.extend(lines)
        pos = end
    merged.extend(base[pos:])

    text = '\n'.join(merged)
    if base_text.endswith('\n') and text:
        text += '\n'
    return text


# ---------------------------------------------------------------------------
#  判定 + 落盘（第十四轮）：`commit_text`
# ---------------------------------------------------------------------------

#: 盘上已经是草稿 → 一个字节都不用写（也不再烧一代历史版本）
SAME = 'same'
#: 盘上仍是基线（或只是被 touch）→ 照计划写
WROTE = 'wrote'
#: 盘上被人改过，但改的不是同一处 → 两边都落上
MERGED = 'merged'
#: 撞在同一处 → **拒写**（一个字不动，交给人）
GATE_CONFLICT = 'conflict'

#: 落盘结局 → 人话（回执/状态栏共用；不许各写一遍）。
COMMIT_CN = {
    SAME: '不用写（盘上已经是这份内容）',
    WROTE: '按计划写好了',
    MERGED: '两边都落上了',
    GATE_CONFLICT: '撞在同一处，没有写',
}


def _fmt_span(start: int, end: int) -> str:
    """`(1, 1)` → `第 1 行`；`(3, 5)` → `第 3–5 行`（1 基、闭区间）。"""
    start, end = int(start or 0), int(end or 0)
    return f'第 {start} 行' if start == end else f'第 {start}–{end} 行'


@dataclass(frozen=True)
class Kept:
    """**别人**改的一段（合并后保住的那几行）。位置是**基线**里的坐标。"""

    start: int = 0          #: 基线里第几行起（1 基；0 = 文件开头，只在插入时出现）
    end: int = 0            #: 基线里第几行止（1 基、闭区间；插入时 == start）
    lines: tuple = ()       #: 他写的那几行（原文）

    @property
    def is_insert(self) -> bool:
        return self.start == self.end

    @property
    def head(self) -> str:
        for line in self.lines:
            text = str(line or '').strip()
            if text:
                return text if len(text) <= 40 else text[:39] + '…'
        return ''

    @property
    def label(self) -> str:
        head = f'「{self.head}」' if self.head else ''
        if self.is_insert:
            where = '文件开头' if self.start <= 0 else f'第 {self.start} 行后面'
            return f'{where}加了 {len(self.lines)} 行{head}'
        return f'{_fmt_span(self.start, self.end)}改成了{head or "别的写法"}'

    def as_dict(self) -> dict:
        return {'start': self.start, 'end': self.end,
                'lines': list(self.lines), 'label': self.label}


@dataclass(frozen=True)
class Commit:
    """一次"判定 + 落盘"的结局（纯数据，能直接进回执/状态栏）。"""

    state: str = GATE_CONFLICT
    path: str = ''
    #: 写之前盘上那份（已归一化成 LF；`same` 时等于草稿）
    disk_text: str = ''
    #: 真正落盘的内容（`conflict` 时是空串 —— 一个字都没写）
    final_text: str = ''
    #: 合并时**别人**的改动（保住的那几段）；`wrote` / `same` 时为空
    kept: tuple = ()
    #: 撞在一起的范围（1 基闭区间，基线坐标）—— 只在 `conflict` 时有
    clash: tuple = ()
    detail: str = ''

    @property
    def ok(self) -> bool:
        return self.state in (SAME, WROTE, MERGED)

    @property
    def merged(self) -> bool:
        return self.state == MERGED

    @property
    def wrote_bytes(self) -> bool:
        """这一趟到底有没有碰盘。"""
        return self.state in (WROTE, MERGED)

    def note(self) -> str:
        """
        写成功后给用户的**短句**（没合并就返回空串 —— 别为正常路加噪音）。

        ⚠ **必须短**：这句要去 Toast（`ui_qt/status.Toast` 是 `wordWrap=False`
        的一行浮层，超宽就被裁掉一半）。第十四轮第一版写了 110 个字，
        截图里正好被裁在"界面要重新载入…"中间 —— 用户看到的是半句话。
        细节（第几行、什么内容）走 `full_note()` → 窗口的日志面板。
        """
        if self.state != MERGED:
            return ''
        return (f'别人也改过这个脚本（不是同一处）：两边都落上了，'
                f'他改的 {len(self.kept)} 处也保住了')

    def full_note(self) -> str:
        """
        给**日志**看的长句（谁改在第几行、以及"界面还是旧的"这件事）。

        为什么把这两句分开：一句进 Toast（一行，会被裁），一句进日志（可以长）。
        合成一句的结果是"两处都放不下"。
        """
        if self.state != MERGED:
            return ''
        parts = '、'.join(k.label for k in self.kept[:3])
        more = len(self.kept) - 3
        if more > 0:
            parts += f' 等 {len(self.kept)} 处'
        return (f'两边都落上了：他在{parts}。'
                f'那几处已经写进磁盘，界面要重新载入这一章才看得到。')

    def error(self) -> str:
        """写不成时给用户的**一句话**（成功时返回空串）。"""
        if self.state != GATE_CONFLICT:
            return ''
        where = '、'.join(_fmt_span(a, b) for a, b in self.clash[:2]) or '同一处'
        return (f'这个脚本在你算完之后又被改过，而且正撞在{where}；'
                f'为了不误删，这次没有写。重新载入之后再改一次。')

    def sentence(self) -> str:
        text = COMMIT_CN.get(self.state, self.state)
        return f'{text}：{self.detail}' if self.detail else text

    def as_dict(self) -> dict:
        return {'state': self.state, 'path': self.path,
                'kept': [k.as_dict() for k in self.kept],
                'clash': [list(c) for c in self.clash], 'detail': self.detail}


def _kept_of(base_lines, disk_lines) -> tuple:
    """`base → disk` 的改动段（= 别人写的那几段），坐标换回**基线**（1 基）。"""
    out = []
    for i1, i2, lines in edits_between(base_lines, disk_lines):
        if i1 == i2:                      # 插入：`i1` = 插在第 i1 行后面
            out.append(Kept(start=i1, end=i1, lines=tuple(lines)))
        else:
            out.append(Kept(start=i1 + 1, end=i2, lines=tuple(lines)))
    return tuple(out)


def _clash_of(base_lines, disk_lines, draft_lines) -> tuple:
    """
    撞在一起的那几段（**基线**坐标，1 基闭区间）。

    只用于说人话（"正撞在第 4 行"），不参与判定 —— 判定唯一的地方是
    `merge_text` 返回空串这个事实。
    """
    touched = set()
    for a in edits_between(base_lines, disk_lines):
        for b in edits_between(base_lines, draft_lines):
            if _touch(a, b):
                for i1, i2 in ((a[0], a[1]), (b[0], b[1])):
                    touched.update(range(i1, max(i2, i1 + 1)))
    if not touched:
        return ()
    nums = sorted(touched)
    spans, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        spans.append((start + 1, prev + 1))
        start = prev = n
    spans.append((start + 1, prev + 1))
    return tuple(spans)


def commit_text(path, base_text, draft_text, *, history: bool = True,
                backup: bool = False) -> Commit:
    """
    把 `base_text → draft_text` 这次改动落进 `path`（**写盘只走这一处**）。

    三种局面（"别人也动了这个文件"时才有后两种）：

    | 盘上是 | 结局 | 写法 |
    |---|---|---|
    | 草稿 | `same` | **不写**（不烧历史版本、不动 mtime） |
    | 基线 | `wrote` | 照草案写（与从前逐字节一致） |
    | 两边都改过、且不重叠 | `merged` | 写合并结果，`kept` 里说清保住了谁的那几行 |
    | 两边都改过、撞在一起 | `conflict` | **一个字不写**，`clash` 指出撞在哪 |

    `history=True` 走 `story.paths.write_text_atomic`（`.rpy` 写前留旧版本）；
    其余文件（`.json` / `.xlsx` 那些）传 `False`。

    读不出来 / 写不进去 / 不是合法 UTF-8 一律**抛**（`OSError` /
    `SourceNotUtf8`），由调用方换成它自己那套信封 —— 这一层不猜"该不该报错"。
    """
    from pathlib import Path

    from storygraph.story.paths import write_text_atomic
    from storygraph.story.source_style import read_text

    p = Path(path)
    base = str(base_text if base_text is not None else '')
    draft = str(draft_text if draft_text is not None else '')
    disk, style = read_text(p)

    if disk == draft:
        return Commit(SAME, str(p), disk, disk, detail='盘上已经是这份内容')
    if disk == base:
        write_text_atomic(p, style.apply(draft, source=p),
                          history=history, backup=backup)
        return Commit(WROTE, str(p), disk, draft, detail='盘上还是你算计划时那一份')

    merged = merge_text(base, disk, draft)
    if merged:
        kept = _kept_of(base.splitlines(), disk.splitlines())
        write_text_atomic(p, style.apply(merged, source=p),
                          history=history, backup=backup)
        return Commit(MERGED, str(p), disk, merged, kept=kept,
                      detail=f'另有 {len(kept)} 处别人的改动')
    clash = _clash_of(base.splitlines(), disk.splitlines(),
                      draft.splitlines())
    return Commit(GATE_CONFLICT, str(p), disk, '', clash=clash,
                  detail='两边的改动落在同一处，认不出该听谁的')


__all__ = ['ADOPT', 'COMMIT_CN', 'CONFLICT', 'Commit', 'GATE_CONFLICT',
           'Kept', 'MERGED', 'REBASE', 'SAME', 'STATE_CN', 'Verdict',
           'WROTE', 'classify', 'commit_text', 'edits_between', 'merge_text',
           'sha1_16']
