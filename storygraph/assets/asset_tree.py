"""
storygraph.assets.asset_tree
============================
**资产分组树的唯一实现**（纯函数，不碰 UI）。

为什么单列（2026-10-23，清单 §十七）：
`app.asset_panel.AssetPanelData.get_tree` 与
`assets.asset_extensions.AssetPanelModel.get_tree` 是两份几乎逐行相同的
实现（连"用 posix 分隔符"那句注释都一字不差），但复制时少抄了一支：
`group_by='type'` 只在面板那一份里存在。于是同一个参数：

    · 面板给图片分组 `image`；
    · 扩展那一份静默落进 `other`。

这比"不支持"更糟 —— 调用方看不出自己传错了。现在只有这一份；
两份旧实现都**委托**过来，判据是"同一份 registry、同一个 group_by
必须给出完全相等的 dict"（`tests/test_asset_extensions.py`）。

`group_by='folder'` 是**面板的两级树**（文件夹 → Category），key 直接取
`Path` 的目录部分、不做归一。**选择器**要的是另一种口径（归一 `game/`
前缀、每一层含子目录、按段排序、还带"没有路径"的兜底组）—— 那份在
`app.picker.folder_groups`，别把两边的口径混着改：面板的树是给"浏览"
用的，选择器那份是给"找一张图"用的（2026-10-30）。
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

#: 支持的分组维度。传别的值 → 图片落 `other`（历史行为，不静默改语义）。
GROUP_BY = ('category', 'tag', 'folder', 'type')


def group_assets(registry, group_by: str = 'category') -> dict:
    """
    `{group: {subgroup: [asset_id, ...]}}`（**已排序、稳定**）。

    `registry` 只需要有 `images` / `music` / `sounds` / `transforms` /
    `characters` 这五个映射；`None` → 空树（调用方不需要判空）。
    """
    if registry is None:
        return {}
    group_by = str(group_by or 'category')
    tree = defaultdict(lambda: defaultdict(list))

    # ---------- 图片 ----------
    for aid, row in (getattr(registry, 'images', {}) or {}).items():
        if group_by == 'category':
            g = row.get('Category', 'other') or 'other'
            sg = row.get('Tag', '') or '_'
        elif group_by == 'tag':
            g = row.get('Tag', '') or '_'
            sg = row.get('Category', 'other') or 'other'
        elif group_by == 'folder':
            path = row.get('Path', '')
            # 用 posix 分隔符：否则 Windows 上会生成反斜杠分组 key，
            # 同一张表在不同平台会长出不同的树。
            g = str(PurePosixPath(path).parent) if path else '_'
            sg = row.get('Category', 'other') or 'other'
        elif group_by == 'type':
            g = 'image'
            sg = row.get('Category', 'other') or 'other'
        else:
            g, sg = 'other', '_'
        tree[g][sg].append(aid)

    # ---------- 音频 ----------
    for name, label in (('music', 'music'), ('sounds', 'sound')):
        for aid in (getattr(registry, name, {}) or {}):
            if group_by == 'type':
                tree[label][label].append(aid)
            else:
                tree[label]['_'].append(aid)

    # ---------- 变换 ----------
    for aid in (getattr(registry, 'transforms', {}) or {}):
        if group_by == 'type':
            tree['transform']['transform'].append(aid)
        else:
            tree['transform']['_'].append(aid)

    # ---------- 角色 ----------
    for aid, row in (getattr(registry, 'characters', {}) or {}).items():
        if group_by == 'type':
            tree['character']['character'].append(aid)
        elif group_by == 'category':
            # 角色按活跃度分组：有对话 / 无对话（面板的历史语义）
            count = row.get('_dialogue_count', 0)
            sg = 'active' if count > 0 else 'defined_only'
            tree['character'][sg].append(aid)
        else:
            tree['character']['_'].append(aid)

    return {
        g: {sg: sorted(ids) for sg, ids in subs.items()}
        for g, subs in tree.items()
    }


__all__ = ['GROUP_BY', 'group_assets']
