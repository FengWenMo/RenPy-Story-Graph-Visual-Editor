"""
`storygraph.story.line_identity` —— `LineId` 的**分配规则**（唯一实现）。
==========================================================================

契约见 [`docs/契约_行身份.md`](../../docs/契约_行身份.md)。这个模块只做三件事：

  · `parse(line_id)`    —— `'ch01.0007'` → `('ch01', 7)`；认不出返回 `None`；
  · `allocate(used, label)` —— 在**已用集合**里给这个 label 找一个没用过的号
    （`max + 1`，从 1 开始），返回 `'label.NNNN'`；
  · `unique_ids(beats)` —— 把一批拍上**重复/缺失**的 ID 收拾干净，返回"改过没有"。

为什么单列一个模块（2026-10-23）：

  从前分配规则散在四处（`beat_model.assign_identity` / `app.editor._renumber`
  / `pipeline.compile` 的兜底 / `collab._unique_line_id`），各写各的。结果是
  **同一个"稳定身份"在四条路上语义不同**：

    · `_renumber` 按位置重写全部 ID —— 插一张卡，后面每一行都换身份；
    · `assign_identity` 只在空时生成，但 `label.seq` 可能与既存重复；
    · 协作库自己有一套 `_unique_line_id`（加后缀），Excel 侧没有。

  身份一旦不稳，TTS 语音缓存、XLIFF 的 `trans-unit/@id`、Unity 行映射、
  协作库主键全部错位 —— 而且都是**静默**的。

⚠ 这里只管"发号"，不管"内容"。内容指纹是 `Beat.beat_fingerprint`
（`beat_model._beat_content_hash`），两者刻意分开：
改一个字不该换身份，而"这条现在长什么样"要跟着内容变。
"""
from __future__ import annotations

import re

#: `label.NNNN`（4 位起；老表里有 5 位也不会认错）。
LINE_ID_RE = re.compile(r'^(?P<label>.+)\.(?P<num>\d{4,})$')


def parse(line_id) -> 'tuple[str, int] | None':
    """`'ch01.0007'` → `('ch01', 7)`；不是这个形状（空 / 别的写法）→ None。"""
    m = LINE_ID_RE.match(str(line_id or '').strip())
    if not m:
        return None
    try:
        return m.group('label'), int(m.group('num'))
    except ValueError:                          # 理论上到不了（\d+ 已保证）
        return None


def allocate(used, label: str) -> str:
    """
    给 `label` 找一个**没用过**的号，返回 `f'{label}.{n:04d}'`。

    `used` 是"已用 ID"的集合（字符串）。规则是 **max + 1**：
      · 不回收：删掉的号不会因为"空出来了"被再发一次（只要 `used` 里还留着它，
        或者它是当前最大值被删——这一档见契约 §3 的 I3 局限）；
      · 不撞车：就算 `used` 里已经有人手写过 `ch01.0007`，也会跳过。
    """
    label = str(label or '').strip() or 'start'
    top = 0
    prefix = label + '.'
    for lid in used or ():
        got = parse(lid)
        if got and got[0] == label and got[1] > top:
            top = got[1]
    n = top + 1
    while f'{label}.{n:04d}' in (used or ()):
        n += 1
    return f'{label}.{n:04d}'


def unique_ids(beats) -> bool:
    """
    把一批拍里**重复**的 LineId 重新发号（保留第一次出现的那个）。

    返回"有没有改过"。空 ID **不在这里补** —— 补号需要知道 `label`，
    属于调用方的事（`beat_model.assign_identity` / `editor._renumber`）。
    """
    seen = set()
    changed = False
    for beat in beats or ():
        lid = str(getattr(beat, 'line_id', '') or '').strip()
        if not lid:
            continue
        if lid in seen:
            new = allocate(seen, parse(lid)[0] if parse(lid) else 'start')
            beat.line_id = new
            seen.add(new)
            changed = True
        else:
            seen.add(lid)
    return changed


__all__ = ['LINE_ID_RE', 'allocate', 'parse', 'unique_ids']
