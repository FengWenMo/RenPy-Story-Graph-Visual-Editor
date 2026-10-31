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

def _edits(base_lines, other_lines) -> list:
    """`base` → `other` 的改动段：`[(起, 止, 新行), …]`（0 基，止不含）。"""
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

    left = _edits(base, disk)
    right = _edits(base, draft)
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


__all__ = ['ADOPT', 'CONFLICT', 'REBASE', 'STATE_CN', 'Verdict', 'classify',
           'merge_text', 'sha1_16']
