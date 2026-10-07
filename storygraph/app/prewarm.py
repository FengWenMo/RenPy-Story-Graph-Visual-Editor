"""
storygraph.app.prewarm
======================
**缩略图预热** —— 把整个项目的缩略图先解一遍、写进磁盘缓存。

为什么值得单独一条路（2026-10-05 实测数字）：

  · 立绘/CG 是 3856×2176 … 4320×7680、3–11 MB 的 PNG，**冷解码 384–434 ms/张**；
  · 命中磁盘缓存只要 **2 ms** —— 差 200 倍；
  · 但"第一次看到某张图"这个代价**总要有人付**：不预热就是用户在滚动的
    时候一张张等（"图片加载还是慢、加载不完全"）。

用户 2026-10-05：

> 另外，可以做预热缩略图

所以给一条**显式**的批量路：跑一次，之后任何界面（选择器 / 卡片 /
图库）都是缓存命中。它是**可中断、可看进度**的 —— 真实项目 2816 张 ×
2 个尺寸，跑满几分钟，不能做成一个"卡住界面"的黑盒。

⚠ 尺寸口径必须与**界面真正要的**一致，否则预热白跑（缓存键含目标尺寸）：

  · 选择器  `ui_qt.asset_picker.THUMB`      = 104×74
  · 立绘卡  `ui_qt.sprite_grid.THUMB_*`     ≈ 160×220

这里**不 import 任何 UI 模块**（那是 `ui_qt` 的事），只把尺寸常量复述一遍，
并用一条测试钉住"和界面常量一致"（`tests/test_prewarm.py`）。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 界面真正会用的缩略图尺寸（改了界面就要改这里，测试会拦住）。
#:
#: ⚠ 实测对齐（2026-10-05）：`asset_picker.THUMB == (104, 74)`、
#: `sprite_grid.THUMB_W/H == (156, 204)`。**第一版这里写的是 (160, 220)** ——
#: 凭空记的一个数，预热出来的缓存**卡片那边一次都命中不了**（缓存键含尺寸）。
#: `tests/test_prewarm.py` 里有一条测试直接 import 那两个 UI 常量来比，
#: 就是为了不让这种"记错一个数字"再发生。
UI_THUMB_SIZES = ((104, 74), (156, 204))


def image_paths(registry, project_root=None) -> list:
    """
    资源表里**图片**那一条条的磁盘路径（去重、只留真存在的）。

    用 `resolve_thumb_source`（唯一真源）解析 —— 资源表 Path 的基准五花八门
    （`game/images/…` / `images/…` / 多一层项目目录名…）。
    """
    from storygraph.story.excel_export import resolve_thumb_source
    out = []
    seen = set()
    for aid, row in ((getattr(registry, 'images', {}) or {}) or {}).items():
        row = row or {}
        rel = str(row.get('Path') or '').strip()
        if not rel:
            continue
        real = resolve_thumb_source(rel, project_root)
        if real is None:
            continue
        key = str(real)
        if key in seen:
            continue
        seen.add(key)
        out.append(real)
    return out


def prewarm(paths, sizes=UI_THUMB_SIZES, workers=None,
            on_progress=None, should_stop=None) -> dict:
    """
    把 `paths` 的缩略图先解出来（每个尺寸一份，写进磁盘缓存）。

    返回 `{total, done, failed, sizes, seconds, stopped}`：
      · `total` = 路径数 × 尺寸数（"要解多少张缩略图"）；
      · `done`  = 成功（含"本来就有缓存"）；
      · `failed`= 解不开的（坏图 / 格式不支持）—— **不抛异常**，只计数。

    `on_progress(done, total)` 在每个缩略图完成时回调（UI 里更新进度）；
    `should_stop()` 返回 True 就尽快收工（**用户关了窗口/点了取消**）——
    已经解好的都在缓存里，下次接着来就是命中。
    """
    import time
    from storygraph.app.thumbs import decode_sync

    paths = [p for p in (paths or []) if p]
    sizes = [(int(w), int(h)) for (w, h) in (sizes or UI_THUMB_SIZES)]
    total = len(paths) * max(1, len(sizes))
    done = failed = 0
    stopped = False
    t0 = time.monotonic()

    def _one(path, size):
        try:
            return decode_sync(path, size, None) is not None
        except Exception as e:                 # noqa: BLE001 - 坏图不该中断预热
            swallow('app.prewarm.prewarm', e)
            return False

    def _bump(ok: bool):
        nonlocal done, failed
        if ok:
            done += 1
        else:
            failed += 1
        if on_progress is not None:
            try:
                on_progress(done + failed, total)
            except Exception as e:             # noqa: BLE001 - 回调坏了不影响预热
                swallow('app.prewarm.prewarm.on_progress', e)

    jobs = [(p, s) for p in paths for s in sizes]
    n_workers = int(workers or 0) or _default_workers()
    if not jobs:
        return {'total': 0, 'done': 0, 'failed': 0, 'sizes': sizes,
                'seconds': 0.0, 'stopped': False}
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_one, p, s): (p, s) for p, s in jobs}
        for fut in as_completed(futures):
            if should_stop is not None and should_stop():
                stopped = True
                for f in futures:
                    f.cancel()
                break
            try:
                ok = bool(fut.result())
            except Exception as e:             # noqa: BLE001
                swallow('app.prewarm.prewarm.result', e)
                ok = False
            _bump(ok)
    return {'total': total, 'done': done, 'failed': failed, 'sizes': sizes,
            'seconds': round(time.monotonic() - t0, 1), 'stopped': stopped}


def _default_workers() -> int:
    """并行度：核数（解码是 CPU 密集，再高只会互相抢）。"""
    try:
        import os
        return max(1, min(16, (os.cpu_count() or 4)))
    except Exception as e:                     # noqa: BLE001
        swallow('app.prewarm._default_workers', e)
        return 4


def prewarm_project(project_root, registry=None, **kw) -> dict:
    """从项目根出发的便捷入口（没给注册表就自己读资源表）。"""
    root = Path(project_root)
    if registry is None:
        from storygraph.pipeline.build import load_asset_registry
        registry = load_asset_registry(root)
    if registry is None:
        return {'total': 0, 'done': 0, 'failed': 0, 'sizes': [],
                'seconds': 0.0, 'stopped': False, 'error': '读不到资源表'}
    paths = image_paths(registry, root)
    res = prewarm(paths, **kw)
    res['paths'] = len(paths)
    return res


__all__ = ['UI_THUMB_SIZES', 'image_paths', 'prewarm', 'prewarm_project']
