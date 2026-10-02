"""
storygraph.app.thumbs —— 缩略图的**框架无关内核**（缓存键 + 后台解码）。

为什么单独一个模块
------------------
"缩略图要快"这件事有两个前端都要满足的约束：

  · 立绘常见 1500×2200，而卡片上只显示 88×118（预览里更小）——
    **绝不许在 UI 线程同步解码**；
  · 同一张图反复要（切拍、点 chip 换表情）必须命中缓存，且素材被
    替换后要**失效**（键里带 mtime/size，否则用户看到旧图）。

这些跟"用 tkinter 还是 Qt"毫无关系。2026-10-01 清理时发现：Qt 的
`ui_qt.thumb_loader` 为了拿 `cache_key` 去 `from storygraph.ui.thumb_loader
import cache_key` —— 于是 **Qt 前端间接依赖了 tkinter**。所以把内核搬到
这一层：

    app.thumbs  ←  ui_qt.thumb_loader（QThreadPool + QImage）
                ←  （原 ui.thumb_loader，已随 tkinter 一起删除）

`resolve_thumb_source` / `thumb_cache_dir` 本来就在 story 层
（`story.excel_export`）—— 这里只**转发**，让两个前端有同一个取用点，
不在本模块重写一遍路径解析规则。

**禁止**在本模块里 import 任何 UI 框架或 GUI 库。
"""
from __future__ import annotations

from storygraph.core.safeguard import swallow
import hashlib
from pathlib import Path

#: 转发（唯一实现在 story.excel_export；"Excel 导出"与"界面预览"
#: 必须用同一套路径解析，否则同一张图两边找到的不是同一个文件）。
from storygraph.story.excel_export import resolve_thumb_source, thumb_cache_dir  # noqa: F401


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


def _downscale(im, max_size):
    """
    把 PIL 图缩到 `max_size` 以内（**保留比例**，返回 RGBA 图）。

    用户 2026-10-05：

    > 现在先优化之前提到的……卡牌界面图片加载慢的问题

    慢在哪（实测真实项目 `lan/happy.png`：**4320×7680 RGBA / 9.4 MB**）：

      · 纯解码（PNG 解压）          ≈ 224 ms —— 这段省不掉；
      · `convert('RGBA')`           ≈ 100 ms —— 图**本来就是** RGBA 时纯属白拷
        一份 133 MB 的缓冲；
      · 一步 `thumbnail()`（BICUBIC）≈ 240 ms —— 在 3300 万像素上做三次卷积。

    所以这里按"**先粗后精**"缩：先用 `reduce()`（BOX 均值，逐块平均，
    比 BICUBIC 快一个量级）把边长砍掉一个整数倍，再对已经很小的小图做
    `thumbnail()`（BICUBIC 收边）。实测 562 ms → 349 ms（1.6×），
    缩略图肉眼无差（逐通道平均差 1.2/255）。

    `reduce(f)` 的 f 取"最大还不会小于目标"的整数倍；`f < 2` 就跳过
    （小图不需要粗缩，也避免 `reduce(1)` 白拷一份）。
    """
    if max_size:
        try:
            target = (max(1, int(max_size[0])), max(1, int(max_size[1])))
        except (TypeError, ValueError, IndexError):
            target = None
    else:
        target = None
    if im.mode != 'RGBA':
        # P / LA / RGB → RGBA（带透明）。已经是 RGBA 时**不转**：
        # `Image.convert` 同模式也会整图拷一份。
        im = im.convert('RGBA')
    if target is None:
        return im.copy()
    factor = min(im.width // target[0], im.height // target[1])
    if factor >= 2:
        im = im.reduce(int(factor))
    im.thumbnail(target)
    return im.copy()


def decode_sync(path, max_size, project_root=None):
    """
    **在工作线程里**解码一张图，返回 PIL Image（失败返回 None）。

    绝不碰任何 GUI 对象——那些只能在主线程构造。Qt 版的 `_Job.run`
    直接用它（产物本来就是线程安全的 PIL Image），省掉"两套解码逻辑
    各自踩图像格式的坑"。

    没有 Pillow 时返回 None（界面走占位，不崩）。
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
            if max_size:
                try:
                    im.draft('RGB', tuple(max_size))
                except Exception:           # noqa: BLE001 - PNG 不支持
                    swallow('thumbs.decode_sync')
            out = _downscale(im, max_size)
        if cache_file is not None:
            try:
                out.save(cache_file, 'PNG')
            except OSError as e:
                swallow('thumbs.decode_sync', e)
        return out
    except Exception:                       # noqa: BLE001 - 损坏文件不阻断界面
        return None


#: 旧名（tkinter 版内部叫 `_decode_sync`）。保留别名只为少改一处调用点。
_decode_sync = decode_sync

#: 图片原始尺寸缓存（只读文件头，代价 ~1ms）
_SIZE_CACHE: dict = {}


def natural_size(path, project_root=None, use_cache: bool = True):
    """
    图片的**原始像素尺寸** `(w, h)`（只读文件头，不解码整图）；读不到返回 None。

    为什么演出预览需要它：立绘在屏幕上的大小 = **原图尺寸 × zoom**（见
    `app.stage.sprite_box`）。真实项目的立绘是 4320×7680，`zoom 0.30` 之后仍有
    1296×2304 —— 不解出"原图多大"，位置和大小都算不出来。而整图解码要几百
    毫秒，**只问尺寸**读头部就够（实测 <1ms），所以这里单开一条路。
    """
    try:
        from PIL import Image
    except Exception:                       # noqa: BLE001 - 没装 Pillow 就放弃
        return None
    real = resolve_thumb_source(path, project_root)
    if real is None:
        return None
    key = str(real)
    if use_cache and key in _SIZE_CACHE:
        return _SIZE_CACHE[key]
    try:
        with Image.open(real) as im:
            size = (int(im.width), int(im.height))
    except Exception:                       # noqa: BLE001 - 损坏文件
        return None
    if use_cache:
        if len(_SIZE_CACHE) > 512:
            _SIZE_CACHE.clear()
        _SIZE_CACHE[key] = size
    return size


__all__ = ['cache_key', 'decode_sync', 'natural_size', 'resolve_thumb_source',
           'thumb_cache_dir']
