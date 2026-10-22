"""
`storygraph.ui_qt.flow_notes` —— 图边派生结果的**一句话说明**。
================================================================

`rebuild_source_edges()` 返回的是计数字典（补了几条 / 改指几条 / 删了几条 /
哪些目标不在图上）。导入、保存、插卡、删卡都会拿到它，但状态栏与日志
需要的是同一句人话；各写一份早晚会漂移（同一个数在一处叫"边"、一处叫"线"）。

所以格式只留在这里：

  · `edge_rebuild_note()` —— 状态栏 / 日志用的一行（没有变化返回空串）；
  · `skipped_targets()`    —— "哪几个目标还没导入"（去重、保持出现顺序）。
"""
from __future__ import annotations


def skipped_targets(result: dict) -> list:
    """`edge_rebuild['skipped']` 里的目标 label（去重、保持源码顺序）。"""
    out = []
    for spec in ((result or {}).get('skipped') or ()):
        target = str((spec or {}).get('to') or '').strip()
        if target and target not in out:
            out.append(target)
    return out


def edge_rebuild_note(result: dict, *, limit: int = 3) -> str:
    """
    图边派生结果 → **一行**中文说明。

    没有实际变化返回 `''`（调用方据此决定要不要占状态栏）。
    `limit` 控制列出几个目标名，其余用"等 N 个"收尾。
    """
    res = dict(result or {})
    if res.get('error'):
        return f'图上的边没有同步（{res["error"]}）'

    parts = []
    removed = int(res.get('removed') or 0)
    added = int(res.get('added') or 0)
    retargeted = int(res.get('retargeted') or 0)
    if removed:
        parts.append(f'删掉 {removed} 条失效连线')
    if added:
        parts.append(f'补上 {added} 条连线')
    if retargeted:
        parts.append(f'改指 {retargeted} 条连线')

    skipped = list(res.get('skipped') or ())
    if skipped:
        names = skipped_targets(res)
        shown = '、'.join(f'「{n}」' for n in names[:limit])
        if len(names) > limit:
            shown += f' 等 {len(names)} 个'
        target_text = f'（目标{shown}不在图上）' if shown else ''
        parts.append(f'⚠ 有 {len(skipped)} 条边没画全{target_text}')
    return '；'.join(parts)


__all__ = ['edge_rebuild_note', 'skipped_targets']
