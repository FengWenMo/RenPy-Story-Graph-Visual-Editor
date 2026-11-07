"""
立绘 image spec → **磁盘上真实要显示的那张图**（2026-11-05 第三轮拆出）
=====================================================================

为什么单开：`sprite_grid.py` 是控件层（卡片 / 网格），而“这条 show 到底
显示哪张图”是**解析**，两边混在一起让 `sprite_grid` 顶过了 1500 行技术债
红线。判据与调用点一个都没动：

  · `SpriteCardWidget._thumb_path` / `SpriteGridWidget` 的预取都走这一份；
  · `sprite_grid` 里保留同名 re-export，老调用点不用改。

三级查找（后两级是关键）：

  1. 主窗口注入的解析器（`session.path_of_image`）—— 真源是资源表的
     `Path` 列，最准；
  2. 它查不到时**按项目自己的 `layeredimage` 声明解析**（`resolve_layers`）
     —— `lan aa happy` 里 `aa` 是项目变量里的服装代号、不对应任何 png，
     但分层模型知道“它不改变任何一层”，实际显示 `happy.png`；
     `lan clothes2 happy` 还要把表情件裁脸 + 服装件叠起来。
     **这一级不许按文件名猜**：合成结构只有项目自己知道。
  3. 最后才退回按 Ren'Py 的目录约定猜
     （`<tag> <attrs>` → `game/images/<tag>/<attrs>.png`）。

为什么必须有第 2 级（2026-11-05 用户纠正）：用户要的不是“它是不是一张
png”，而是**预览里那张图改完显示对不对**。真机 `crop_lan` 那种
`AlphaMask(图, Crop(矩形, Solid(...)))` 写法解析不出来时，`clothes2`
这一维会被整个丢掉、预览永远停在 `happy.png` —— 看起来就是“换了服装
图没换”。第 2 级修的就是这个。

为什么第 3 级仍然保留：CG 这类图**往往不在资源表里**（实测
`sr_general`：`cg03.png` 在磁盘上，但 2828 条图片记录里一条 cg 都没有），
没有分层声明的老项目也只能靠目录约定。
"""
from __future__ import annotations

from storygraph.core.safeguard import swallow


def resolve_sprite_thumb_path(spec, resolver=None, project_root=None,
                              max_size=None):
    """
    立绘 image spec → 磁盘上的真实图片路径（卡片与预取共用这一份）。

    `max_size=(w, h)`：卡片那条路传屏幕上的格子尺寸 —— 分层合成会**按这个
    尺寸**做（合成一版小的、走缩略图缓存），不再每次都按 4320×7680 原图
    合成再缩（2026-11-05：那一片 `canvas.save()` 单次 ~1s，全在 GUI 线程）。
    不给 = 老行为（原图分辨率，最长边封顶 `layer_compose.MAX_SIDE`）。
    """
    spec = str(spec or '').strip()
    if not spec:
        return None
    from storygraph.ui_qt.sprite_hints import _image_part_of_spec
    # `show expression …` / 带引号括号的表达式：判不了就不画（也不许按文件名猜）。
    spec = _image_part_of_spec(spec)
    if not spec:
        return None
    parts = [p for p in spec.split() if p]
    tried_layered = False
    # ①′ **缩略图档**的分层合成先走（2026-11-05）：项目声明过 `layeredimage`
    #    时，这张图"该长什么样"只有它知道；而缩略图要的只是格子那么大。
    #    没声明 / 认不出 → 继续走下面两条老路，一个字节都不改。
    if max_size and project_root and len(parts) >= 2:
        tried_layered = True
        composed = _layered_sprite_path(parts[0], parts[1:], project_root,
                                        max_size)
        if composed:
            return composed
    if resolver is not None:
        try:
            found = resolver(spec)
        except Exception as e:                    # noqa: BLE001 - 注入的解析器坏了不算错
            swallow('ui_qt.sprite_thumbs.resolve_sprite_thumb_path', e)
            found = None
        if found:
            return found
    if not parts or not project_root:
        return None
    # ② 分层解析与 ③ 目录约定共用同一个 except：两条都是“猜不出来就退回”，
    #    不新增一处 broad except（技术债棘轮只许降）。
    try:
        if not tried_layered:
            composed = _layered_sprite_path(parts[0], parts[1:], project_root,
                                            max_size)
            if composed:
                return composed
        if len(parts) < 2:
            return None
        rel = '/'.join([parts[0], '_'.join(parts[1:])])
        from storygraph.story.excel_export import resolve_thumb_source
        for cand in (f'game/images/{rel}.png', f'game/images/{rel}.webp',
                     f'images/{rel}.png', f'{rel}.png'):
            found = resolve_thumb_source(cand, project_root)
            if found:
                return found
    except Exception as e:                        # noqa: BLE001 - 退回老路
        swallow('ui_qt.sprite_thumbs.resolve_sprite_thumb_path', e)
        return None
    return None


def _layered_sprite_path(tag, attrs, project_root, max_size=None):
    """
    项目声明过 `layeredimage <tag>` 时：这条 show **实际显示的那张图**。

    返回合成图路径（单层就是原图）；没有声明 / 有一层解析不出来 → `None`，
    调用方继续走目录约定（**认不出就退回，不画错**）。
    """
    tag = str(tag or '').strip()
    if not tag or not project_root:
        return None
    from storygraph.story.layered_model import (load_layer_model_cached,
                                                resolve_layers)
    model = load_layer_model_cached(project_root)
    if model is None or not getattr(model, 'images', None) \
            or not model.has(tag):
        return None
    plan = resolve_layers(model, tag, list(attrs or ()), project_root)
    if not getattr(plan, 'ok', False):
        return None
    from storygraph.app.layer_compose import compose_sprite
    return compose_sprite(plan, max_size=max_size)


__all__ = ['resolve_sprite_thumb_path']
