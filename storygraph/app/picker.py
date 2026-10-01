"""
storygraph.app.picker
=====================
**资产选择器的语义层**（框架无关）—— 界面只负责把结果画成网格。

为什么单独一层
--------------
"该给策划看什么、按什么分组、默认选哪个"是**产品判断**，不是画控件：

  · 按**看图的人怎么找**分组（立绘：<角色> / 背景 / CG / 音乐 / 音效 / 语音），
    而不是按数据表怎么存；
  · 图片要给出**真实缩略图路径**（立绘在 `game/images/<tag>/<attr>.png`）；
  · 选中项回填的是**image spec**（`ling happy`），不是资产 ID
    （`ling_happy`）—— 两者差一个字，写错就是"图不存在"。

这些都放在这里，Qt 版与 tkinter 版共用同一份；换前端不用改语义。

**禁止**在本模块里 import 任何 UI 框架。
"""

from __future__ import annotations

from typing import Optional

from storygraph.app import beats as B


class RegistrySource:
    """
    把"一个 registry（+ 项目根）"适配成 `AssetCompletion` 需要的"engine"。

    `AssetCompletion` 只用 `engine.get_registry()`；而 `asset_preview_path`
    还要 `engine.project_root` 才能把资源表里的相对路径拼成绝对路径
    （否则缩略图永远显示不出来）。所以这里两个都提供 ——
    不需要真的有一个 AssetEngine，无头测试也因此变得容易。
    """

    def __init__(self, registry=None, project_root=None):
        self._registry = registry
        self.project_root = project_root

    def get_registry(self):
        return self._registry


def groups(registry, limit: int = 3000, project_root=None) -> dict:
    """分类候选：`{类别名: [(asset_type, asset_id), ...]}`（可分组的唯一真源）。"""
    if registry is None:
        return {}
    return B.categorize_assets(RegistrySource(registry, project_root),
                               limit=limit)


def search(registry, query: str = '', asset_types=None, limit: int = 50,
           project_root=None) -> list:
    """按前缀/模糊搜索：`[(asset_type, asset_id), ...]`。"""
    if registry is None:
        return []
    return B.search_assets(RegistrySource(registry, project_root), query=query,
                           asset_types=asset_types, limit=limit)


def detail(registry, asset_type: str, asset_id: str, project_root=None) -> str:
    """一条资产的详情文字（给选择器右侧/悬浮提示用）。"""
    if registry is None:
        return ''
    return B.asset_detail(RegistrySource(registry, project_root),
                          asset_type, asset_id)


def thumbnail_path(registry, asset_type: str, asset_id: str,
                   project_root=None):
    """
    这条资产有没有可显示的图片路径（没有就 None —— 界面显示文字兜底）。
    """
    if registry is None or asset_type != 'image':
        return None
    return B.asset_preview_path(RegistrySource(registry, project_root),
                                asset_type, asset_id)


def row_of(registry, asset_type: str, asset_id: str, project_root=None):
    """一条资产的原始行（写回 image spec 时要用它的 Tag / Attributes）。"""
    if registry is None:
        return None
    return B.asset_row(RegistrySource(registry, project_root),
                       asset_type, asset_id)


def image_spec_of(asset_id: str, row: Optional[dict] = None, *,
                  expression: str = '', position: str = '') -> str:
    """
    资产 ID + 行数据 → 写回卡片要用的 **image spec**。

    例：`ling_happy` + `{Tag: ling, Attributes: happy}` → `ling happy`。

    规则（与编译器口径一致）：
      · 有 Tag 就用 Tag 开头（`ling`），后面接 Attributes；
      · 没有 Attributes 就只用 Tag（`show ling`）；
      · 都没有就原样返回 ID（宁可写 ID，也不凭空编一个 tag）；
      · expression/position 给了就覆盖/追加（`@位置`）。
    """
    row = row or {}
    tag = str(row.get('Tag') or '').strip()
    attrs = str(row.get('Attributes') or '').strip()
    aid = str(asset_id or '').strip()

    if tag:
        parts = [tag] + ([expression] if expression else
                         (attrs.split() if attrs else []))
        spec = ' '.join(p for p in parts if p)
    else:
        spec = aid
        if expression:
            first = spec.split()[0] if spec else ''
            rest = spec.split()[1:] if spec else []
            spec = ' '.join([first, expression] + rest) if first else expression
    spec = spec.strip()
    pos = str(position or '').strip()
    if spec and pos and '@' not in spec:
        spec = f'{spec}@{pos}'
    return spec


__all__ = ['RegistrySource', 'groups', 'search', 'detail', 'thumbnail_path',
           'row_of', 'image_spec_of']
