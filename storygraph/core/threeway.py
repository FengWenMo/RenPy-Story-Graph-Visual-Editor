# -*- coding: utf-8 -*-
"""
storygraph.core.threeway
========================
**按 key 逐条**的三方合并 —— "存盘前先对表"的那条判据，只有一份实现。

## 它解决的那类事故（2026-11-07，两次探针）

    甲窗：改 A 的名字 → 存
    乙窗：改 B 的开关 → 存          ← 乙的 base 里 A 还是旧名字
    结果：A 变回旧名字，两次都报"保存成功"

整份覆盖的写盘法在这种时候**一个错都不报**：它只是把"我看到的整个世界"
写下去，而别人的改动恰好不在我看到的那个世界里。

## 三条判据（决策表）

    mine == theirs        → 一样，取谁都行
    mine == base          → **我没动** → 取文件里的（收进别人的改动 = absorbed）
    theirs == base        → **别人没动** → 取我的
    两边都变了            → **冲突**：abort 一个字都不写；mine/theirs 按它取

"没动"必须用**规范形**比（两边的 dict 走同一套序列化，比如 `to_dict()`）：
拿内存对象比文件里的 JSON，每一条都会被当成"改过" → 全是假冲突。

## 删除怎么表达

`picks[key] is None` = **这条该删**。于是"我删掉了 A、别人改了 A"也是冲突
（别人可能正指望它），而"我删掉 A、别人没动"照删。

## 谁在用

  · `ext.registry.save()` —— `storygraph.ext.json`（扩展点，按 id）；
  · `story.statement_registry.declare_calls` / `save_calls` ——
    `storygraph.rules.json` 的 `calls` 段（按 head）。

两处**不许各写一份**：这正是当初"扩展平台修好了、rules.json 还是老样子"
的由来 —— 同一个病（整份覆盖）、同一个药方（这张表）。
"""
from __future__ import annotations


def merge_by_key(base: dict, mine: dict, theirs: dict,
                 on_conflict: str = 'abort') -> tuple:
    """
    三方合并（**纯函数**，不碰文件）→ `(picks, conflicts, absorbed)`。

    参数都是 `{key: 值}`；`值` 可以是 dict / str / None。`None` 表示这条不存在。
    `on_conflict` 只认 `abort` / `mine` / `theirs`（别的值由调用方先拦，
    这里**不猜**：猜错的代价是把别人的改动悄悄丢掉）。
    """
    if on_conflict not in ('abort', 'mine', 'theirs'):
        raise ValueError(f'on_conflict 只认 abort/mine/theirs，收到 {on_conflict!r}')
    picks: dict = {}
    conflicts: list = []
    absorbed: list = []
    # 顺序：文件里的顺序（`theirs`）优先，我新加的那些接在后面
    for key in [*theirs, *(k for k in mine if k not in theirs)]:
        want, had, now = mine.get(key), base.get(key), theirs.get(key)
        if want == now:
            picks[key] = want
        elif want == had:
            picks[key] = now                    # 我没动：听文件的
            if now != had:
                absorbed.append(key)
        elif now == had:
            picks[key] = want                   # 别人没动：听我的
        else:
            conflicts.append(key)
            picks[key] = want if on_conflict in ('abort', 'mine') else now
    return picks, conflicts, absorbed


def compose_rows(rows: list, picks: dict, mine: dict) -> list:
    """
    合并结果 → 写回文件的那份**列表**（照文件顺序；认不出的原样；新增的接在后面）。

    `rows` 是文件里的原样列表：`[(key, 原值)]`，认不出的条目 key 写 `''`
    （它没有 id，不参与合并，**原样写回去**）。
    """
    out, seen = [], set()
    for key, raw in rows:
        seen.add(key)
        if not key:
            out.append(raw)                     # 认不出的条目：原样写回去
        elif picks.get(key) is not None:
            out.append(picks[key])
    for key in mine:
        if key not in seen and picks.get(key) is not None:
            out.append(picks[key])              # 我新加的那几条
    return out


__all__ = ['compose_rows', 'merge_by_key']
