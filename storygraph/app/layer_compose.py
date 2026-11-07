"""
`storygraph.app.layer_compose`
=============================
把**分离的立绘层**用 PIL 合成一张图（并落盘缓存），给预览当"一张图"用。

设计文档：`docs/archive/设计_合成立绘合成与姿势切换_2026-10-05.md`（P2）。

为什么落盘而不是直接给 PIL 图：
预览那条路（`StageCanvas`）本来就是"拿路径画图"，缩略图也有现成的
磁盘缓存/字节预算 LRU；把合成结果写成一张缓存图，**零改动**接进现有管线，
也不会在每次重画时重新合成（4320×7680 的两层合成不便宜）。

层语义（与 Ren'Py 一致，见设计文档 3.2）：

    AlphaMask(图, 蒙版)  → 只保留"图的像素中，蒙版 alpha > 0"的那些
    Crop(box, 图)        → 先裁
    叠加顺序             → 按 `plan.layers` 顺序往上一层一层贴

## 2026-11-05：合成**按尺寸**做，不再每次都是原图分辨率

真机实测（`sr_general`，卡片上点一下服装 chip）：

    解析 0.6ms · 解码两张 4320×7680 PNG ~0.6s · 合成 ops ~0.4s
    · `canvas.save()` **~1.0s**（PNG 编码 3300 万像素）
    → **一次新的组合 ≈ 2.4s，全压在 GUI 线程上**（用户："切换属性明显卡顿"）

而卡片上那张图只有 88×118、舞台预览也就几百像素高 —— 按原图分辨率合成、
再让缩略图那条路把它缩到 1/40，是纯浪费。所以现在多一个 `max_size` 参数：

  · 给了 `max_size`（缩略图那条路）：**从缩略图缓存里取每一层**（热了 ~1ms/张），
    裁切/蒙版/位移按下采样比例一起缩，产物边长不超过 `max_size`；
  · 没给：仍按原图分辨率合成，但**最长边封顶 `MAX_SIDE`** —— 预览画不了
    1400px 以上，再大只是让编码变慢。

缓存键里带上目标尺寸（`_sig`），所以"同一张图两种尺寸"不会互相盖掉。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 合成结果缓存目录名（放在现成的缩略图缓存目录里）
_CACHE_SUBDIR = 'composed'

#: 合成结果的**最长边**上限（没给 `max_size` 时的默认）。
#:
#: 为什么是 1400：舞台预览的可用高度是几百像素（`StageCanvas` 再大也放大），
#: 而 `sr_general` 的立绘是 4320×7680 —— 按原图分辨率编码一次 PNG 要 ~1s。
#: 1400 仍比屏幕上能显示的更大，肉眼无差，编码降到 ~0.1s。
MAX_SIDE = 1400


def _norm_box(max_size) -> tuple:
    """`max_size` → `(w, h)` 正整数二元组；给不出就 `()`（= 用默认上限）。"""
    try:
        w, h = (tuple(max_size) + (0, 0))[:2]
        w, h = int(w), int(h)
    except (TypeError, ValueError):
        return ()
    return (w, h) if w > 0 and h > 0 else ()


def _sig(plan, box=()) -> str:
    """
    层列表 → 稳定签名（含 mtime/size，源图改了要重合成）。

    ⚠⚠ **属性选择也算进签名**（2026-10-16 用户实担心的那个坑）：
    "换服装仍可能命中旧缓存"。同一个组里两个取值完全可能解到**同一张图**
    （`group outfit` 的两个值指向同一个文件、或某一层被条件跳过），
    那时只按"层路径 + mtime"算签名会给它们**同一个缓存文件** ——
    换了维度、路径却一模一样，界面看不出变化。

    所以签名包含 `plan.attrs_used` / `plan.attrs_chosen`：**选了什么**本身就是
    合成结果的一部分。源图没变、层没变、但选法变了 → 重新合成。
    幂等性不受影响（同一组属性 → 同一份签名 → 命中同一张缓存）。

    尺寸也进签名（2026-11-05）：卡片要 240 宽的、预览要 1400 的，
    同一组属性两种产物，不能互相命中。
    """
    parts = [f'box:{box}']
    parts += [f'attr:{a}' for a in (getattr(plan, 'attrs_used', None) or ())]
    parts += [f'chose:{a}' for a in (getattr(plan, 'attrs_chosen', None) or ())]
    for ly in plan.layers or ():
        for key in ('path', 'mask'):
            p = ly.get(key)
            if not p:
                parts.append(f'{key}:-')
                continue
            try:
                st = Path(p).stat()
                parts.append(f'{key}:{Path(p).name}:{st.st_size}:{int(st.st_mtime)}')
            except OSError:
                parts.append(f'{key}:{Path(p).name}:?')
        parts.append(f'crop:{ly.get("crop")}|off:{ly.get("offset")}')
    return hashlib.sha1('|'.join(parts).encode('utf-8')).hexdigest()[:24]


def compose_sprite(plan, cache_dir=None, max_size=None):
    """
    `LayerPlan` → 合成后的图片路径（失败返回 None，调用方退回单图）。

    只有**至少两层**（或有 mask/crop）才合成；单层无蒙版的情况直接返回原图，
    省一次无意义的拷贝。

    `max_size=(w, h)`：产物的长边不超过它（缩略图那条路传屏幕上的格子尺寸）。
    不传时按原图分辨率合成、最长边封顶 `MAX_SIDE`。
    """
    layers = [ly for ly in (getattr(plan, 'layers', None) or ())
              if ly.get('path')]
    if not layers:
        return None
    if len(layers) == 1 and not layers[0].get('mask') \
            and not layers[0].get('crop') and not layers[0].get('offset'):
        return Path(layers[0]['path'])
    try:
        from PIL import Image  # noqa: F401 - 只用来确认装没装
    except Exception as e:                  # noqa: BLE001 - 没装 PIL 就不合成
        swallow('app.layer_compose.compose_sprite.pil', e)
        return None
    box = _norm_box(max_size) or (MAX_SIDE, MAX_SIDE)
    cache_dir = Path(cache_dir) if cache_dir else _default_cache_dir()
    out = None
    try:
        cache_dir = cache_dir / _CACHE_SUBDIR
        cache_dir.mkdir(parents=True, exist_ok=True)
        out = cache_dir / f'{_sig(plan, box)}.png'
        if out.is_file():
            return out
    except OSError as e:                     # noqa: BLE001 - 缓存目录用不了
        swallow('app.layer_compose.compose_sprite.cache_dir', e)
        out = None

    try:
        canvas = _compose_canvas(layers, box)
        if canvas is None or out is None:
            return None
        canvas.save(out, 'PNG')
        return out
    except Exception as e:                  # noqa: BLE001 - 合成失败别炸预览
        swallow('app.layer_compose.compose_sprite', e)
        return None


def _scale_for(layers, box) -> tuple:
    """
    `(factor, (target_w, target_h))` —— 把所有层缩到 `box` 以内要用的比例。

    以**面积最大那层**的原图尺寸为准（Ren'Py 的 layeredimage 各层通常同尺寸；
    不同尺寸的层在下面按目标尺寸对齐，与"原图分辨率"那条路的行为一致）。
    """
    from storygraph.app.thumbs import natural_size
    big = (0, 0)
    for ly in layers:
        size = natural_size(ly['path'])
        if not size:
            continue
        if size[0] * size[1] > big[0] * big[1]:
            big = tuple(int(v) for v in size)
    if not big[0] or not big[1]:
        return 1.0, ()
    factor = min(1.0, float(max(box)) / float(max(big)))
    target = (max(1, int(round(big[0] * factor))),
              max(1, int(round(big[1] * factor))))
    return factor, target


def _layer_image(ly, factor, target):
    """
    一层 → PIL 图（**按目标尺寸解码**；缩略图缓存命中时 ~1ms）。

    读不出来返回 None（调用方跳过这一层，与从前一致）。
    """
    try:
        return _load_layer_image(ly['path'], factor, target)
    except (OSError, ValueError) as e:      # 单层坏了就跳过（图格式/IO 错）
        swallow('app.layer_compose.compose_sprite.open', e)
        return None


def _load_layer_image(path, factor, target):
    """真正打开一层：能按目标尺寸解就按目标尺寸解，解不动就整图开。"""
    from PIL import Image
    if factor >= 1.0 or not target:
        return Image.open(path).convert('RGBA')
    from storygraph.app.thumbs import decode_sync
    img = decode_sync(path, target)
    if img is None:
        return Image.open(path).convert('RGBA')
    if tuple(img.size) != tuple(target):
        img = img.resize(target)
    return img


def _layer_mask(ly, size):
    """这一层的蒙版（只取 alpha 通道）；没有 / 读不出 → None。"""
    if not ly.get('mask'):
        return None
    from storygraph.app.thumbs import decode_sync
    try:
        mask = decode_sync(ly['mask'], size)
        if mask is None:
            return None
        if tuple(mask.size) != tuple(size):
            mask = mask.resize(size)
        return mask.split()[-1]                  # **只取 alpha 通道**
    except (OSError, ValueError) as e:      # 蒙版坏了就不裁（不影响其它层）
        swallow('app.layer_compose.compose_sprite.mask', e)
        return None


def _compose_canvas(layers, box):
    """逐层裁切 / 蒙版 / 叠加 → 一张 RGBA 画布（失败 / 空返回 None）。"""
    from PIL import Image

    factor, target = _scale_for(layers, box)
    canvas = None
    for ly in layers:
        img = _layer_image(ly, factor, target)
        if img is None:
            continue
        crop = ly.get('crop')
        if crop:
            try:
                box_ = tuple(int(round(float(v) * factor)) for v in crop) \
                    if factor < 1.0 else tuple(int(v) for v in crop)
                img = img.crop(box_)
            except Exception as e:          # noqa: BLE001 - 裁不了就用整图
                swallow('app.layer_compose.compose_sprite.crop', e)
        mask = _layer_mask(ly, img.size)
        if mask is not None:
            # ⚠ **把蒙版真的应用到这一层**（Ren'Py `AlphaMask(图, 蒙版)`
            # 的语义：结果只保留"蒙版 alpha > 0"的像素）。
            # 实测踩过：只把 mask 存下来、叠图时用 `alpha_composite`
            # —— 那用的是**图自己的 alpha**，蒙版形同虚设，于是"面部层"
            # 被整张贴上去又被身体层盖住，合成结果 ≈ 只有身体（无头）。
            # 用乘法保留原图 alpha 的抗锯齿边缘（蒙版 alpha × 图 alpha）。
            try:
                from PIL import ImageChops
                base_alpha = img.getchannel('A')
                img = img.copy()
                img.putalpha(ImageChops.multiply(base_alpha, mask))
            except Exception as e:          # noqa: BLE001 - 退到直接换 alpha
                swallow('app.layer_compose.compose_sprite.mask_apply', e)
                try:
                    img.putalpha(mask)
                except Exception as e2:     # noqa: BLE001 - 换不了就不裁
                    swallow('app.layer_compose.compose_sprite.putalpha', e2)
        if canvas is None:
            canvas = Image.new('RGBA', img.size, (0, 0, 0, 0))
        elif canvas.size != img.size:
            # 层尺寸不一致：按画布尺寸对齐（大的那层定画布）
            if img.size[0] * img.size[1] > canvas.size[0] * canvas.size[1]:
                bigger = Image.new('RGBA', img.size, (0, 0, 0, 0))
                bigger.alpha_composite(canvas)
                canvas = bigger
            else:
                img = img.resize(canvas.size) if img.size != canvas.size \
                    else img
        off = ly.get('offset') or (0, 0)
        if factor < 1.0:
            off = (float(off[0]) * factor, float(off[1]) * factor)
        canvas.alpha_composite(img, (int(round(off[0])), int(round(off[1]))))
    return canvas


def _default_cache_dir() -> Path:
    try:
        from storygraph.app.thumbs import thumb_cache_dir
        base = Path(thumb_cache_dir())
        return base.parent if base.name == 'thumbs' else base
    except Exception as e:                  # noqa: BLE001 - 缓存目录退到 temp 是正常兜底
        # 退到 temp 不算错，但这也意味着缩略图缓存不跟着项目走（换项目会串）。
        # 留一条痕，免得以后查"为什么缩略图没了"时找不到方向。
        from storygraph.core.safeguard import swallow
        swallow('app.layer_compose.default_cache_dir', e)
        import tempfile
        return Path(tempfile.gettempdir()) / 'storygraph_thumbs'


__all__ = ['MAX_SIDE', 'compose_sprite']
