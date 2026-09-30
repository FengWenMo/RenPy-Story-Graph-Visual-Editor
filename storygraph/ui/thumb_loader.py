"""
storygraph.ui.thumb_loader —— 缩略图**异步加载器**（防抖 + 后台解码 + 缓存）

为什么需要它（2026-09-29，用户实测）
------------------------------------
改立绘名时，每敲一个字符都会触发一次缩略图刷新，而那次刷新是
**在 UI 线程里同步解码整张大图**（立绘常见 1500×2200，显示只要 88×118）：

  · 打 `ling` 三个字母 = 三次全尺寸解码 → 输入卡顿；
  · 来回切拍时同一张图被反复解码（无内存缓存）；
  · Excel 导出那条路早有磁盘缓存与 `draft()`，UI 这条路**一条都没用上**。

本模块把"解码"从 UI 线程挪走，并让它只发生一次：

    输入变化 ──防抖 120ms──▶ 命中内存缓存？──是──▶ 直接贴图
                                   │
                                   否
                                   ▼
                        线程池(2) 解码 ──▶ 主线程转 PhotoImage ──▶ 贴图
                                   │
                        复用 Excel 侧磁盘缓存（键含 mtime/size/尺寸）

硬约束（tkinter 的脾气）
------------------------
· `PhotoImage` **只能在主线程构造**，所以工作线程只返回 PIL Image；
· 回调全部经 `widget.after()` 回主线程执行；
· 被 GC 的 PhotoImage 会让图变空白 —— 调用方必须持有引用（本类也持有缓存引用）。
"""
from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from storygraph.story.excel_export import resolve_thumb_source, thumb_cache_dir
from storygraph.ui import tokens as T


def cache_key(path, max_size, stamp=None) -> str:
    """
    缩略图缓存键：**路径 | mtime | size | 目标尺寸**。

    `mtime`/`size` 必须在键里：素材被替换后若不失效，用户会看到旧图
    （Excel 侧踩过这个坑）。纯函数，便于单测。
    """
    w, h = (tuple(max_size) + (0, 0))[:2] if max_size else (0, 0)
    if stamp is None:
        try:
            st = Path(path).stat()
            stamp = (int(st.st_mtime), st.st_size)
        except OSError:
            stamp = (0, 0)
    mtime, size = (tuple(stamp) + (0, 0))[:2]
    return f'{path}|{mtime}|{size}|{w}x{h}'


def _decode_sync(path, max_size, project_root=None):
    """
    **在工作线程里**解码一张图，返回 PIL Image（失败返回 None）。
    绝不碰任何 tk 对象——那只能在主线程做。
    """
    try:
        from PIL import Image
    except Exception:                       # noqa: BLE001 - 没装 Pillow 走降级
        return None

    real = resolve_thumb_source(path, project_root)
    if real is None:
        return None

    cache_file = None
    try:
        st = real.stat()
        key = cache_key(real, max_size, (int(st.st_mtime), st.st_size))
        cdir = thumb_cache_dir()
        if cdir is not None:
            digest = hashlib.md5(key.encode('utf-8', 'replace')).hexdigest()
            cache_file = cdir / (digest + '.png')
            if cache_file.is_file():
                with Image.open(cache_file) as cached:
                    return cached.convert('RGBA').copy()
    except OSError:
        cache_file = None

    try:
        with Image.open(real) as im:
            # draft：JPEG 让解码器**直接按缩小尺寸**解码，不必先把
            # 几千万像素读进内存再缩（冷缓存下是数量级差别）
            try:
                im.draft('RGB', tuple(max_size))
            except Exception:               # noqa: BLE001 - PNG 不支持
                pass
            im = im.convert('RGBA')
            im.thumbnail(tuple(max_size))
            out = im.copy()
        if cache_file is not None:
            try:
                out.save(cache_file, 'PNG')
            except OSError:
                pass
        return out
    except Exception:                       # noqa: BLE001 - 损坏文件不阻断界面
        return None


class ThumbLoader:
    """
    缩略图加载器。一个面板一个实例（内部有线程池与缓存）。

    用法::

        loader = ThumbLoader(self, max_size=(88, 118))
        loader.request(slot=card, path='game/images/ling_ah.png',
                       apply=lambda photo, path: card.set_photo(photo))
    """

    def __init__(self, widget, max_size=(88, 118), project_root=None,
                 debounce_ms=None, cache_limit=60, workers=2):
        self._widget = widget
        self._max_size = tuple(max_size)
        self._project_root = project_root
        self._debounce = (T.MOTION['debounce'] if debounce_ms is None
                          else debounce_ms)
        self._limit = max(8, int(cache_limit))
        self._cache = {}                    # key -> PhotoImage
        self._order = []                    # LRU 顺序（最旧在前）
        self._pending = {}                  # slot -> after id
        self._jobs = {}                     # slot -> future
        #: 线程池**惰性创建**：没真正请求过缩略图就不建线程。
        #: 这不只是省资源——`ThreadPoolExecutor` 的工作线程是 non-daemon，
        #: 一旦建过，进程退出时要把它们收干净；测试进程里如果没人调用
        #: `shutdown()` 就会一直挂着（实测：pytest 跑到超时被 SIGTERM）。
        self._pool = None
        self._workers = max(1, int(workers))
        self._closed = False
        self._lock = threading.Lock()
        #: 有没有事件循环？没有（测试里的 tkinter 桩、已销毁的控件）就
        #: **完全不加载** —— 不能退化成"直接在线程池里解码"：那样每个
        #: 实例化面板的用例都会建线程 + 解码真实大图，累积起来会把测试
        #: 拖到超时（实测：全量 pytest 卡在 41% 被 SIGTERM）。
        self._can_schedule = callable(getattr(widget, 'after', None))

    def _get_pool(self):
        """取线程池（首次使用时才建，并登记退出时收尾）。"""
        if self._pool is None:
            self._pool = ThreadPoolExecutor(
                max_workers=self._workers, thread_name_prefix='sg-thumb')
            try:
                import atexit
                atexit.register(self._shutdown_pool)
            except Exception:                # noqa: BLE001
                pass
        return self._pool

    def _shutdown_pool(self):
        pool, self._pool = self._pool, None
        if pool is not None:
            try:
                pool.shutdown(wait=False)
            except Exception:                # noqa: BLE001
                pass

    # ---------- 对外接口 ----------

    def request(self, slot, path, apply, max_size=None):
        """
        请求一张缩略图（可反复调用，旧的会被取消/取代）。

        slot:   调用方给的稳定标识（通常是卡片对象）——用来取消过期任务
        path:   图片路径或图像名（解析交给 `resolve_thumb_source`）
        apply:  回调，`apply(photo_or_None, path)`，**在主线程**执行
        max_size: 本张图的目标尺寸（不给就用实例的默认值）。

        为什么允许**逐张指定尺寸**：演出预览要同时要"舞台背景"
        （整屏）和"立绘"（窄高）两种尺寸，而一个 `ThumbLoader` 一个
        线程池就够 —— 缓存键本来就带尺寸（`cache_key`），
        按请求传尺寸比再建一个加载器干净。
        """
        if self._closed or not self._can_schedule:
            return
        prev = self._pending.pop(slot, None)
        if prev is not None:
            try:
                self._widget.after_cancel(prev)
            except Exception:               # noqa: BLE001 - 桩环境/已销毁
                pass

        # 注意 `str(...)`：**调用方传进来的可能是 `Path`**（`asset_preview_path`
        # 就返回 Path）。第一版这里直接 `.strip()`，真实数据下抛
        # `AttributeError: 'WindowsPath' object has no attribute 'strip'`，
        # 而它发生在卡片构造里 → 整个 `load_beat` 崩掉 → **立绘区一片空白**
        # （用户 2026-09-30 报的"已经导入了的立绘没有显示"就是这个）。
        path = str(path or '').strip()
        size = tuple(max_size) if max_size else self._max_size
        if not path:
            self._safe_apply(apply, None, path)
            return

        try:
            self._pending[slot] = self._widget.after(
                self._debounce, lambda: self._start(slot, path, apply, size))
        except Exception:                   # noqa: BLE001 - 控件已销毁
            # 事件循环挂了就**不加载**，绝不退化成同步解码：
            # 那会把 UI 线程重新卡住，正是这个加载器要消灭的问题
            return

    def forget(self, slot):
        """卡片被销毁时调用：取消挂起的任务。"""
        prev = self._pending.pop(slot, None)
        if prev is not None:
            try:
                self._widget.after_cancel(prev)
            except Exception:               # noqa: BLE001
                pass
        self._jobs.pop(slot, None)

    def shutdown(self):
        """关闭线程池（面板销毁时调用）。"""
        self._closed = True
        self._shutdown_pool()
        self._cache.clear()
        self._order.clear()

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    # ---------- 内部 ----------

    def _mem_key(self, path, size=None):
        return cache_key(path, size or self._max_size)

    def _start(self, slot, path, apply, size):
        self._pending.pop(slot, None)
        if self._closed:
            return
        size = tuple(size or self._max_size)
        key = self._mem_key(path, size)
        with self._lock:
            photo = self._cache.get(key)
            if photo is not None:           # 内存命中：直接贴，0 延迟
                try:
                    self._order.remove(key)
                except ValueError:
                    pass
                self._order.append(key)
        if photo is not None:
            self._safe_apply(apply, photo, path)
            return

        self._safe_apply(apply, None, path)  # 先给骨架/占位，界面不空等
        try:
            fut = self._get_pool().submit(_decode_sync, path, size,
                                          self._project_root)
        except Exception:                    # noqa: BLE001 - 池已关闭
            return
        self._jobs[slot] = fut
        self._poll(slot, fut, key, path, apply)

    def _poll(self, slot, fut, key, path, apply, tries=0):
        if self._closed:
            return
        if not fut.done():
            if tries > 200:                  # 兜底：最长约 6 秒后放弃
                self._jobs.pop(slot, None)
                return
            try:
                self._widget.after(
                    30, lambda: self._poll(slot, fut, key, path, apply,
                                           tries + 1))
            except Exception:                # noqa: BLE001
                pass
            return

        if self._jobs.get(slot) is not fut:
            return                           # 已被更新的任务取代 → 丢弃结果
        self._jobs.pop(slot, None)

        try:
            image = fut.result()
        except Exception:                    # noqa: BLE001
            image = None
        if image is None:
            self._safe_apply(apply, None, path)
            return

        photo = self._to_photo(image, path)
        if photo is not None:
            self._remember(key, photo)
        self._safe_apply(apply, photo, path)

    def _to_photo(self, image, path):
        """PIL Image → PhotoImage（**必须在主线程**）；失败退回 tk 原生读法。"""
        try:
            from PIL import ImageTk
            return ImageTk.PhotoImage(image)
        except Exception:                    # noqa: BLE001
            return self._fallback(path)

    def _fallback(self, path):
        """
        无 Pillow 时的降级：`tk.PhotoImage` 只能读 png/gif，且只能整数倍缩小。
        读不了就返回 None（界面显示占位文字，不报错）。
        """
        real = resolve_thumb_source(path, self._project_root)
        if real is None:
            return None
        try:
            import tkinter as tk
            if not hasattr(tk, 'PhotoImage'):       # 测试桩
                return None
            photo = tk.PhotoImage(file=str(real))
            w = max(int(photo.width()), 1)
            h = max(int(photo.height()), 1)
            factor = max(1, (w + self._max_size[0] - 1) // self._max_size[0],
                         (h + self._max_size[1] - 1) // self._max_size[1])
            if factor > 1:
                photo = photo.subsample(factor, factor)
            return photo
        except Exception:                       # noqa: BLE001
            return None

    def _remember(self, key, photo):
        with self._lock:
            self._cache[key] = photo
            try:
                self._order.remove(key)
            except ValueError:
                pass
            self._order.append(key)
            while len(self._order) > self._limit:
                old = self._order.pop(0)
                self._cache.pop(old, None)

    def _safe_apply(self, apply, photo, path):
        try:
            apply(photo, path)
        except Exception:                       # noqa: BLE001 - 回调不该影响加载
            pass


__all__ = ['ThumbLoader', 'cache_key']
