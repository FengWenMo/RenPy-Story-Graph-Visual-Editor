"""
storygraph.core.vocabulary
==========================
**共享词表的唯一真源**：引擎/表格里那些"约定俗成的名字集合"。

为什么要有这个模块（2026-09-28 收敛）：

"资产类型 → registry 集合属性"这件事曾经有 **5 处**各写一份：

    asset_registry._TYPE_ATTR          （6 项）
    validate._TYPE_ATTR                （6 项，与上面重复）
    dsl.Symbols.has() 里内联的 dict     （6 项，又一份）
    dsl.Symbols._AUDIO_ATTR            （3 项子集）
    collab_store._FIELD_ATTR           （另一件事：Beat 字段名映射，不在此列）

"非角色 tag"则有 **2 份且内容不同**：`dsl.NON_ACTOR_TAGS` 与
`stage_snapshot._NON_ACTOR_TAGS`。同一个画面，编译器与快照可能给出
不同结论——这不是重复，是**分歧**。

约定：这个模块**零项目内依赖**（只放纯数据），谁都可以 import，
不产生环。加新词表请加在这里，不要就地再写一份。
"""
from __future__ import annotations

# ==================================================================
#  资产类型
# ==================================================================

#: 资产类型（脚本与表格里的**单数**名）→ `AssetRegistry` 的集合属性名（复数）。
#: 注意 `voice → voices`、`sound → sounds` 是复数，`music` 是单数（历史命名）。
ASSET_TYPE_ATTR = {
    'character': 'characters',
    'image': 'images',
    'transform': 'transforms',
    'music': 'music',
    'sound': 'sounds',
    'voice': 'voices',
}

#: 音频类资产 → 集合属性名（`ASSET_TYPE_ATTR` 的子集，便于按通道取行）。
AUDIO_TYPE_ATTR = {
    'music': 'music',
    'sound': 'sounds',
    'voice': 'voices',
}


# ==================================================================
#  舞台词汇
# ==================================================================

#: **非角色图层** tag：会被 `show` 出来，但不是"台上站着谁"。
#: 快照靠它把 `show cg cg01` / `show bg rooftop` 挡在 actors 之外
#: （否则快照会显示"台上站着一个 bg"），编译器靠它判断"要不要查角色表"。
#:
#: 2026-09-28 合并了原先两份内容不同的集合（`dsl.NON_ACTOR_TAGS` 与
#: `stage_snapshot._NON_ACTOR_TAGS`），取并集——两边都有的名字保持原语义，
#: 只多出 `background / movie / video / overlay / title / splash` 与 `bg_`，
#: 这些在 Ren'Py 里同样不是立绘 tag。
NON_ACTOR_TAGS = frozenset({
    'bg', 'bg_', 'background', 'cg', 'ui', 'gui', 'black', 'white',
    'logo', 'text', 'screen', 'movie', 'video', 'overlay', 'title',
    'splash',
})

#: `show X ...` 里 X **不是图像名**的写法（Screen Language / 图层操作）：
#: `show screen msg` / `show layer master` / `show expression F(x)`。
#: 它们不参与"图像引用"判定，也永远不算立绘。
NON_ACTOR_SHOW_TARGETS = frozenset({
    'screen', 'layer', 'expression', 'movie', 'text',
})

#: 非角色图层 tag → 给人看的中文名（快照「本拍变化」列里用）。
#: 缺失的 key 由调用方兜底成"图层"。
LAYER_CN_TAG = {
    'cg': 'CG',
    'ui': '界面',
    'gui': '界面',
    'movie': '影片',
    'video': '影片',
    'logo': '标志',
    'overlay': '叠加层',
    'black': '纯色',
    'white': '纯色',
    'screen': '画面',
    'text': '文字',
}


# ==================================================================
#  内置变换 / 转场 / 位置
# ==================================================================

#: Ren'Py 自带的变换 / 转场 / 位置。脚本里出现这些名字是正常的，
#: 不该把它们当成"引用了不存在的资产"，否则一次扫描就报上百处断裂引用。
RENPY_BUILTIN_TRANSFORMS = frozenset({
    # 转场
    'dissolve', 'fade', 'pixellate', 'move', 'moveinright', 'moveinleft',
    'moveintop', 'moveinbottom', 'moveoutright', 'moveoutleft',
    'moveouttop', 'moveoutbottom', 'ease', 'zoomin', 'zoomout',
    'hpunch', 'vpunch', 'blinds', 'squares', 'wipeleft', 'wiperight',
    'wipeup', 'wipedown', 'slideleft', 'slideright', 'slideup',
    'slidedown', 'slideawayleft', 'slideawayright', 'slideawayup',
    'slideawaydown', 'pushright', 'pushleft', 'pushup', 'pushdown',
    'irisin', 'irisout', 'cropmove', 'pixellate', 'None', 'null',
    # 默认位置
    'left', 'right', 'center', 'truecenter', 'top', 'bottom',
    'topleft', 'topright', 'bottomleft', 'bottomright',
    'offscreenleft', 'offscreenright', 'reset', 'default',
})


__all__ = [
    'ASSET_TYPE_ATTR', 'AUDIO_TYPE_ATTR',
    'NON_ACTOR_TAGS', 'NON_ACTOR_SHOW_TARGETS', 'LAYER_CN_TAG',
    'RENPY_BUILTIN_TRANSFORMS',
]
