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

"不重叠"还不够：合出来那份**能不能启动**（2026-11-08 第十六轮）
------------------------------------------------------------------
行级合并只认行、不认缩进。两边各自都合法、却又**不重叠**的改动，合起来
可以是错的 —— 第十六轮地狱测试（`tests/test_hell_merge_structure.py`）抓到：

  · 他删掉 `    menu:` 那一行、我改菜单项 → 合出来是

        label a:
                "改过的左边":     ← 8 个空格，上面没有块头

    一个**两边谁都没写过**的文件。Ren'Py 一启动就报缩进错，而判定说"合上了"。
    现在判 `CLASH_REPARENTED`：**我的每一段改动，合完之后必须还嵌在同一个
    父语句下面**（父语句被删/被改到别的层级 → 拒写）。父语句只是**换了个
    写法**（`menu:` → `menu 选项:`，同一缩进、仍以冒号收尾）不算 —— 块还在。

  · 两边各删一半 → 合并结果是**空文件**。`''` 同时是"空结果"和"合不了"的
    哨兵，从前会拒写却说"撞在同一处"。现在判 `CLASH_ERASED`，说清楚。

  · 基线末尾没有换行、他往文件末尾追加一行（带换行）→ 合并结果按**基线**的
    结尾拼，他那行的 `\\n` 被吞掉（`git diff` 报 "\\ No newline at end of file"）。
    现在末尾那个换行**归收尾那一行的来源**。

这几种都**拒写**，而且措辞必须分开：说成"撞在同一处"在它们身上是假话
（他根本没碰我改的那一行），用户会去找一个不存在的"他改了我这句"。
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass

#: 行边界**只认 `\n`**（第十五轮地狱测试抓到的坑）：
#: `str.splitlines()` 还把 U+2028 / U+2029 / U+0085 / `\x0b` / `\x0c` 都当换行 ——
#: 从 Word / 网页粘一句台词最容易带进 U+2028。用它切，同一份文本在这里是 6 行、
#: 在解析器/补丁那边是 5 行，diff 就整体错位：**该合的判成冲突**（实测），
#: `_clash_of` 报出来的行号也是错的。与 `patcher` 同一个助手，别再各写一份。
from storygraph.core.textlines import split_lines

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

    ⚠ 这条判据**故意**把"插入点正好落在替换段的端点上"（他刚在你改的那一行
    上面/下面加了一行）也算冲突 —— 第十四轮就这么定的，见
    `test_merge_commit.py::test_adjacent_insert_is_refused_on_purpose`。
    但那种情况**不是**"改到了同一处"，措辞由 `_clash_kind` 分开
    （第十五轮地狱测试：原来两种都报"撞在同一处"，用户看到的是一句假话）。
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


#: 两边**真的**动到了同一行（或者两个插入落在同一点）
CLASH_OVERLAP = 'overlap'
#: 只是**紧挨着**：他加/改的那一行贴着你改的那一行，没有共用任何基线行
CLASH_TOUCHING = 'touching'
#: 撞是没撞，但**你的改动会被挪到别的块里**（第十六轮地狱测试抓到的）
CLASH_REPARENTED = 'reparented'
#: 两边各删一部分，合起来整份文件会变成空的（第十六轮）
CLASH_ERASED = 'erased'

#: 合不了的各种原因 → 一句人话（`Commit.detail`；不许各写一遍）。
MERGE_PROBLEM_CN = {
    CLASH_OVERLAP: '两边的改动落在同一处，认不出该听谁的',
    CLASH_TOUCHING: '两边的改动紧挨着，中间没有一行能把它们分开',
    CLASH_REPARENTED: '你改的那几行所在的块，开头那一行被别人改掉了',
    CLASH_ERASED: '两边各删了一部分，合起来整份文件会变成空的',
    '': '两边的改动落在一起，认不出该听谁的',      # 兜底：判定说不行、原因不详
}


def _adjacent_only(a, b) -> bool:
    """
    这两段碰上了，但**没有共用任何基线行**吗？

    只有"插入点落在替换段的端点上"算：他插在你改的那一行前面 / 后面。
    两段都插在同一点**不算** —— 先后说不准，那是真的同一处。
    """
    a1, a2 = int(a[0]), int(a[1])
    b1, b2 = int(b[0]), int(b[1])
    if a1 == a2 and b1 == b2:
        return False
    if a1 == a2:
        return b1 != b2 and a1 in (b1, b2)
    if b1 == b2:
        return a1 != a2 and b1 in (a1, a2)
    return False


def _clash_kind(base_lines, disk_lines, draft_lines) -> str:
    """
    撞车属于哪一种（只用于**说人话**，不参与判定）。

    * `overlap`  —— 两边真动到同一行（或两个插入落在同一点）；
    * `touching` —— 只是紧挨着（他加/改的那一行贴着你改的那一行）；
    * `''`       —— 其实没撞（不该发生：走到这里就说明 `merge_text` 说不成）。
    """
    hard = soft = False
    for a in edits_between(base_lines, disk_lines):
        for b in edits_between(base_lines, draft_lines):
            if not _touch(a, b):
                continue
            if _adjacent_only(a, b):
                soft = True
            else:
                hard = True
    return CLASH_OVERLAP if hard else (CLASH_TOUCHING if soft else '')


# ---------------------------------------------------------------------------
#  合并之后**你的那几行还挂在同一个块里吗**（第十六轮）
# ---------------------------------------------------------------------------

def _indent_of(line) -> int:
    """行首空格数（制表符按 1 算 —— Ren'Py 的块只认缩进，这里不需要更精确）。"""
    text = str(line or '')
    return len(text) - len(text.lstrip(' \t'))


def _is_skippable(line) -> bool:
    """空行与整行注释：它们不构成"块"，找父语句要跳过。"""
    text = str(line or '').strip()
    return not text or text.startswith('#')


def _is_block_opener(line) -> bool:
    """这一行是不是"块头"（`menu:` / `if x:` / `"选项":` 都以冒号收尾）。"""
    return str(line or '').rstrip().endswith(':')


def _parent_at(lines, idx: int, depth: int) -> int:
    """
    `idx` **上面**最近的一行"缩进比 `depth` 浅"的语句 → 它的下标；没有 → `-1`。

    这就是 Ren'Py 里的"父语句"：块靠缩进嵌套，父语句一换，块的身份就换了。
    空行 / 整行注释不构成块，跳过（否则他删个空行就会被当成"块头没了"）。
    """
    for j in range(int(idx) - 1, -1, -1):
        if _is_skippable(lines[j]):
            continue
        if _indent_of(lines[j]) < int(depth):
            return j
    return -1


def _assemble(base, left, right) -> tuple:
    """
    把两边的改动落到基线上 → `(merged, mine, tail_from)`。

    `mine` = 我这一侧的每一段在 `merged` 里的位置 `(起始下标, 基线里的插入点)`
    —— "合完之后我的改动还挂在同一个块里吗"那道闸要它。
    `tail_from` = 文件最后一段来自哪一侧（决定末尾那个换行符听谁的）。
    """
    merged: list = []
    mine: list = []
    tail_from = 'base'
    pos = 0
    for start, end, lines, is_mine in sorted(left + right,
                                             key=lambda o: (o[0], o[0] == o[1])):
        merged.extend(base[pos:start])
        at = len(merged)
        merged.extend(lines)
        if is_mine and lines:
            mine.append((at, start))
        if lines:
            tail_from = 'draft' if is_mine else 'disk'
        pos = end
    if pos < len(base):
        merged.extend(base[pos:])
        tail_from = 'base'
    return merged, mine, tail_from


def _moved_block(base, merged, mine):
    """
    我的改动有没有被**挪进别的块里** → 返回被改掉的那条父语句的行号（1 基），
    没问题时返回 `None`。

    行级合并不认缩进：他要是删/改了我这一块的**开头那一行**（`menu:` /
    `if flag:` / `label x:`），我的那几行就会挂到另一个块下面 —— 产物是
    一个**两边谁都没写过**的文件，Ren'Py 启动就报缩进错，而判定说"合上了"。
    块头只是**换了个写法**（同缩进、仍以冒号收尾）不算：块还在。
    """
    for at, start in mine:
        head = next((k for k, line in enumerate(merged[at:])
                     if not _is_skippable(line)), None)
        if head is None:
            continue
        depth = _indent_of(merged[at + head])
        old_parent = _parent_at(base, start, depth)
        new_parent = _parent_at(merged, at + head, depth)
        old_line = base[old_parent] if old_parent >= 0 else ''
        new_line = merged[new_parent] if new_parent >= 0 else ''
        if new_line == old_line:
            continue
        if (old_line and new_line and _is_block_opener(old_line)
                and _is_block_opener(new_line)
                and _indent_of(old_line) == _indent_of(new_line)):
            continue
        return old_parent + 1 if old_parent >= 0 else 0
    return None


def _merge_plan(base_text, disk_text, draft_text) -> tuple:
    """
    `(合并结果, 合不了的原因, 相关行号)` —— **合并的唯一实现**
    （`merge_text` / `merge_three_way` 都从这里拿）。

    原因：`''`（能合）/ `CLASH_OVERLAP` / `CLASH_TOUCHING` / `CLASH_REPARENTED`
    / `CLASH_ERASED`。`merge_text` 的公开契约只有"`''` = 合不了"，
    要分清是**哪一种**合不了（措辞不同）必须拿这一份。

    `相关行号`：`CLASH_REPARENTED` 时是**被改掉的父语句**在基线里的行号
    （1 基；0 = 父语句在文件开头，没有行号可用）。
    """
    base_text = str(base_text if base_text is not None else '')
    disk_text = str(disk_text if disk_text is not None else '')
    draft_text = str(draft_text if draft_text is not None else '')
    base = split_lines(base_text)
    disk = split_lines(disk_text)
    draft = split_lines(draft_text)

    left = [(s, e, lines, False) for s, e, lines in edits_between(base, disk)]
    right = [(s, e, lines, True) for s, e, lines in edits_between(base, draft)]
    for a in left:
        for b in right:
            if _touch(a, b):
                return '', _clash_kind(base, disk, draft), 0

    merged, mine, tail_from = _assemble(base, left, right)
    if not merged:
        # 两边把它拆光了。**不许**当成"合不了"以外的东西：`''` 同时是
        # "空文件"和"合不了"的哨兵，说清楚是哪种才不会写出一份空文件。
        return '', (CLASH_ERASED if base else ''), 0

    moved = _moved_block(base, merged, mine)
    if moved is not None:
        return '', CLASH_REPARENTED, moved

    text = '\n'.join(merged)
    # 末尾那个换行符听谁的（第十六轮）：谁的那一行收尾，就按谁的原文。
    #
    # 从前一律按**基线**：基线末尾没有换行、而他往文件末尾追加了一行（带换行）
    # 时，合并结果会把他那一行的换行吞掉 —— 写回盘上就是"他没碰过的地方少了
    # 一个字节"，`git diff` 报 "\ No newline at end of file"。
    if tail_from == 'draft':
        tail = draft_text.endswith('\n')
    else:
        # 收尾的是基线（或他那份）里的同一行 → 盘上那份的结尾就是正确答案
        tail = disk_text.endswith('\n')
    if text and tail:
        text += '\n'
    return text, '', 0


def merge_text(base_text, disk_text, draft_text) -> str:
    """
    两边对同一份 base 的改动**不重叠**时 → 合并结果；否则 `''`（= 合不了）。

    行级合并（`difflib` 的 opcode），不逐字符：这个项目的文本单位就是"行"，
    逐字符合并会把缩进/引号拼成谁也没写过的样子。

    ⚠ 切行走 `core.textlines.split_lines`（**只认 `\\n`**），不用 `splitlines()`
    —— 见文件头那条注释。

    ⚠ "合不了"不止"撞车"一种（第十六轮）：**合起来会把文件拆空**、或者
    **你的那几行会被挪进别的块**（他删/改了你那一块的开头那一行）也算。
    要分清是哪一种，用 `merge_three_way` 的 `clash_kind`（它拿 `_merge_plan`
    同一份判定，不再各算一遍）。
    """
    return _merge_plan(base_text, disk_text, draft_text)[0]



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
    #: 撞的是"同一行"还是"只是紧挨着"（`CLASH_OVERLAP` / `CLASH_TOUCHING`）。
    #: 只影响**措辞**：几种都拒写，但说成"撞在同一处"在紧挨着那种场合是**假话**。
    #:
    #: 第十六轮又添两种（也只为说人话，判定仍然只有一处）：
    #: `CLASH_REPARENTED`（他会把你改的那几行挪进别的块）/
    #: `CLASH_ERASED`（合起来整份文件会变成空的）。
    clash_kind: str = ''
    #: 被你改的那一块的**开头那一行**（1 基行号, 原文）—— 只在
    #: `CLASH_REPARENTED` 时有。`clash` 里给的也是这一行。
    parent: tuple = ()
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
        if self.clash_kind == CLASH_REPARENTED:
            # 没撞车：是**缩进/块**的问题。你的那几行本来挂在某个块下面，
            # 他把那一块的开头那行改掉/删了 —— 合起来你的改动会落进别的块，
            # 产物谁都没写过，Ren'Py 一启动就报错。所以说"撞在同一处"也是假话。
            line, head = (list(self.parent) + ['', ''])[:2]
            quote = f'「{head}」' if head else '那一行'
            at = f'你那一版第 {line} 行' if line else '那一行'
            return (f'这个脚本在你算完之后又被改过：你改的那几行本来挂在{quote}'
                    f'（{at}）下面，他把这一行改掉或删了 —— 合起来你的改动会'
                    f'落进别的块里，所以这次没有写。先看一眼那几行，或者重新'
                    f'载入这一章再改一次。')
        if self.clash_kind == CLASH_ERASED:
            return ('两边各删了一部分，合起来整份文件会变成空的 —— 那不像你要'
                    '的结果，所以这次没有写。先看一眼再决定，或者重新载入这一章。')
        if self.clash_kind == CLASH_TOUCHING:
            # 他没有动你那一行，只是**贴着你**加/改了一行。说成"撞在同一处"
            # 是假话（用户会去找"他什么时候改了我这句"，找不到）。
            # 开头那句"又被改过"两种都留着：它是这次拒写的共同事实。
            return (f'这个脚本在你算完之后又被改过：别人的改动紧挨着你改的那一行'
                    f'（{where}），中间没有一行能把两段分开 —— 谁在前说不准，'
                    f'所以这次没有写。先看一眼那几行，或者重新载入这一章再改一次。')
        return (f'这个脚本在你算完之后又被改过，而且正撞在{where}；'
                f'为了不误删，这次没有写。重新载入之后再改一次。')

    def sentence(self) -> str:
        text = COMMIT_CN.get(self.state, self.state)
        # `COMMIT_CN[conflict]` 写的是"撞在同一处" —— 另外两种合不了**不是**撞车
        # （见 `clash_kind`），照搬就是假话。
        if self.state == GATE_CONFLICT:
            text = {CLASH_REPARENTED: '没有写（合起来你的改动会落进别的块里）',
                    CLASH_ERASED: '没有写（合起来文件会变成空的）'}.get(
                        self.clash_kind, text)
        return f'{text}：{self.detail}' if self.detail else text

    def as_dict(self) -> dict:
        return {'state': self.state, 'path': self.path,
                'kept': [k.as_dict() for k in self.kept],
                'clash': [list(c) for c in self.clash],
                'clash_kind': self.clash_kind,
                'parent': list(self.parent), 'detail': self.detail}


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


def merge_three_way(base_text, disk_text, draft_text, path='') -> Commit:
    """
    **纯判定**：底稿 / 盘上 / 我这份 → 该写什么（不读盘、不写盘）。

    第十五轮提成公开的：导出那条大路（`session_export._patch_note`）和
    `commit_text` 必须是**同一份**判定，否则两处会各说一套
    （第十四轮已经吃过一次：`merge_text` 写好了半年没人调，因为每个落盘点
    都自己判了一遍"盘上变了就拒"）。

    | 盘上是 | 结局 |
    |---|---|
    | 我这份 | `same`（不写） |
    | 底稿（他其实没动） | `wrote`（照我的写） |
    | 底稿 + 他的改动、不重叠 | `merged`（写合并结果，`kept` 说清保住他哪几处） |
    | 合不了（撞车 / 会落进别的块 / 会变成空文件） | `conflict`
      （`final_text` 是空串 —— **一个字都不许写**；`clash_kind` 说清是哪一种） |

    `path` 只是回执里的路标，判定与它无关。
    """
    base = str(base_text if base_text is not None else '')
    disk = str(disk_text if disk_text is not None else '')
    draft = str(draft_text if draft_text is not None else '')
    where = str(path or '')
    if disk == draft:
        return Commit(SAME, where, disk, disk, detail='盘上已经是这份内容')
    if disk == base:
        return Commit(WROTE, where, disk, draft, detail='盘上还是你算计划时那一份')
    base_lines = split_lines(base)
    disk_lines = split_lines(disk)
    draft_lines = split_lines(draft)
    merged, problem, line = _merge_plan(base, disk, draft)
    if merged:
        kept = _kept_of(base_lines, disk_lines)
        return Commit(MERGED, where, disk, merged, kept=kept,
                      detail=f'另有 {len(kept)} 处别人的改动')
    if problem == CLASH_REPARENTED:
        head = (base_lines[line - 1].strip()
                if 0 < line <= len(base_lines) else '')
        return Commit(GATE_CONFLICT, where, disk, '',
                      clash=((line, line),) if line else (),
                      clash_kind=problem, parent=(line, head),
                      detail=MERGE_PROBLEM_CN[problem])
    if problem == CLASH_ERASED:
        return Commit(GATE_CONFLICT, where, disk, '', clash=(),
                      clash_kind=problem, detail=MERGE_PROBLEM_CN[problem])
    clash = _clash_of(base_lines, disk_lines, draft_lines)
    kind = problem or _clash_kind(base_lines, disk_lines, draft_lines)
    return Commit(GATE_CONFLICT, where, disk, '', clash=clash,
                  clash_kind=kind,
                  detail=MERGE_PROBLEM_CN.get(kind) or MERGE_PROBLEM_CN[''])


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
    disk, style = read_text(p)
    # 判定只有一处（`merge_three_way`）—— 导出那条大路用的是同一个函数，
    # 于是"两边都改了时该怎么办"不会有两套答案。
    v = merge_three_way(base_text, disk, draft_text, path=str(p))
    if v.wrote_bytes:
        write_text_atomic(p, style.apply(v.final_text, source=p),
                          history=history, backup=backup)
    return v


__all__ = ['ADOPT', 'CLASH_ERASED', 'CLASH_OVERLAP', 'CLASH_REPARENTED',
           'CLASH_TOUCHING', 'COMMIT_CN',
           'CONFLICT', 'Commit', 'GATE_CONFLICT', 'Kept', 'MERGE_PROBLEM_CN',
           'MERGED', 'REBASE', 'SAME', 'STATE_CN', 'Verdict', 'WROTE',
           'classify', 'commit_text', 'edits_between', 'merge_text',
           'merge_three_way', 'sha1_16']
