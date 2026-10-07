"""
`storygraph.app.layer_compose`
=============================
把**分离的立绘层**用 PIL 合成一张图（并落盘缓存），给预览当"一张图"用。

设计文档：`docs/设计_合成立绘合成与姿势切换_2026-10-05.md`（P2）。

为什么落盘而不是直接给 PIL 图：
预览那条路（`StageCanvas`）本来就是"拿路径画图"，缩略图也有现成的
磁盘缓存/字节预算 LRU；把合成结果写成一张缓存图，**零改动**接进现有管线，
也不会在每次重画时重新合成（4320×7680 的两层合成不便宜）。

层语义（与 Ren'Py 一致，见设计文档 3.2）：

    AlphaMask(图, 蒙版)  → 只保留"图的像素中，蒙版 alpha > 0"的那些
    Crop(box, 图)        → 先裁
    叠加顺序             → 按 `plan.layers` 顺序往上一层一层贴
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 合成结果缓存目录名（放在现成的缩略图缓存目录里）
_CACHE_SUBDIR = 'composed'


def _sig(plan) -> str:
    """层列表 → 稳定签名（含 mtime/size，源图改了要重合成）。"""
    parts = []
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


def compose_sprite(plan, cache_dir=None):
    """
    `LayerPlan` → 合成后的图片路径（失败返回 None，调用方退回单图）。

    只有**至少两层**（或有 mask/crop）才合成；单层无蒙版的情况直接返回原图，
    省一次无意义的拷贝。
    """
    layers = [ly for ly in (getattr(plan, 'layers', None) or ())
              if ly.get('path')]
    if not layers:
        return None
    if len(layers) == 1 and not layers[0].get('mask') \
            and not layers[0].get('crop') and not layers[0].get('offset'):
        return Path(layers[0]['path'])
    try:
        from PIL import Image
    except Exception as e:                  # noqa: BLE001 - 没装 PIL 就不合成
        swallow('app.layer_compose.compose_sprite.pil', e)
        return None
    cache_dir = Path(cache_dir) if cache_dir else _default_cache_dir()
    try:
        cache_dir = cache_dir / _CACHE_SUBDIR
        cache_dir.mkdir(parents=True, exist_ok=True)
        out = cache_dir / f'{_sig(plan)}.png'
        if out.is_file():
            return out
    except OSError as e:                     # noqa: BLE001 - 缓存目录用不了
        swallow('app.layer_compose.compose_sprite.cache_dir', e)
        out = None

    try:
        canvas = None
        for ly in layers:
            try:
                img = Image.open(ly['path']).convert('RGBA')
            except Exception as e:          # noqa: BLE001 - 单层坏了就跳过
                swallow('app.layer_compose.compose_sprite.open', e)
                continue
            crop = ly.get('crop')
            if crop:
                try:
                    img = img.crop(tuple(int(v) for v in crop))
                except Exception as e:      # noqa: BLE001 - 裁不了就用整图
                    swallow('app.layer_compose.compose_sprite.crop', e)
            mask = None
            if ly.get('mask'):
                try:
                    mask = Image.open(ly['mask']).convert('RGBA')
                    if mask.size != img.size:
                        mask = mask.resize(img.size)
                    mask = mask.split()[-1]          # **只取 alpha 通道**
                except Exception as e:      # noqa: BLE001 - 蒙版坏了就不裁
                    swallow('app.layer_compose.compose_sprite.mask', e)
                    mask = None
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
                except Exception as e:      # noqa: BLE001 - 退到直接换 alpha
                    swallow('app.layer_compose.compose_sprite.mask_apply', e)
                    try:
                        img.putalpha(mask)
                    except Exception as e2:  # noqa: BLE001 - 换不了就不裁
                        swallow('app.layer_compose.compose_sprite.putalpha',
                                e2)
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
            canvas.alpha_composite(img, (int(off[0]), int(off[1])))
        if canvas is None:
            return None
        if out is None:
            return None
        canvas.save(out, 'PNG')
        return out
    except Exception as e:                  # noqa: BLE001 - 合成失败别炸预览
        from storygraph.core.safeguard import swallow
        swallow('app.layer_compose.compose_sprite', e)
        return None


def _default_cache_dir() -> Path:
    try:
        from storygraph.app.thumbs import thumb_cache_dir
        base = Path(thumb_cache_dir())
        return base.parent if base.name == 'thumbs' else base
    except Exception:                       # noqa: BLE001
        import tempfile
        return Path(tempfile.gettempdir()) / 'storygraph_thumbs'


__all__ = ['compose_sprite']
