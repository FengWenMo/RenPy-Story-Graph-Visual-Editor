"""
storygraph.app.stage_render
===========================
**离屏渲染一帧真实舞台画面**（Ren'Py 语义，Pillow 合成）。

用户 2026-10-20：

> 舞台的预览效果必须更真实！最好跟真正编译的一模一样……目前先做 Renpy 的吧！

和旧预览的区别：旧预览在 960×540 的 `QWidget` 上"示意"地摆立绘，名条和
台词在画布**外面**用 `QLabel` 拼；本模块按项目自己的分辨率（`gui.init`）
合成一张完整画面：

  背景 → 立绘（按 `story.atl.Placement.box` 的真实几何）→ CG/图层 →
  文本框底图 → 名条底图 + 角色名 → 台词（自动换行、颜色、描边、`{tag}`）。

所有排版参数来自 `story.renpy_gui`（`gui.*` / 角色定义），不是编辑器主题。
找不到的素材/字体**明说**（`RenderResult.warnings`），绝不静默画错。

本模块**不 import Qt**：纯 Pillow，可无头测试，也可被 CLI/其它前端复用。
Pillow 是可选依赖：没装时 `available()` 为 False，调用方退回旧画布。
"""

from __future__ import annotations

import math
import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from storygraph.core.safeguard import swallow

try:                                    # Pillow 是可选依赖
    from PIL import Image, ImageColor, ImageDraw, ImageFont
    _PIL_OK = True
except Exception as e:                  # noqa: BLE001 - 没装就退回旧预览
    swallow('stage_render.import_pillow', e)
    Image = ImageColor = ImageDraw = ImageFont = None
    _PIL_OK = False

from storygraph.story.atl import placement_box, placement_of


def available() -> bool:
    """这个环境能不能离屏渲染（Pillow 装没装）。"""
    return bool(_PIL_OK)


# ----------------------------------------------------------------------
#  文字标签
# ----------------------------------------------------------------------

_TAG_RE = re.compile(r'\{([^{}]*)\}')


def _split_tag(tag: str) -> tuple:
    tag = str(tag or '').strip()
    if '=' in tag:
        name, _, value = tag.partition('=')
        return name.strip().lower(), value.strip()
    return tag.lower(), ''


def _color(value, fallback=(255, 255, 255, 255)):
    """`#rgb` / `#rrggbb` / `#rrggbbaa` / 英文色名 → RGBA。"""
    text = str(value or '').strip().strip('"\'')
    if not text:
        return fallback
    if text.startswith('#'):
        try:
            got = ImageColor.getrgb(text)
            return tuple(got) + ((255,) if len(got) == 3 else ())
        except (ValueError, TypeError):
            return fallback
    try:
        got = ImageColor.getrgb(text)
        return tuple(got) + ((255,) if len(got) == 3 else ())
    except (ValueError, TypeError):
        return fallback


@dataclass
class _TextStyle:
    color: tuple = (255, 255, 255, 255)
    size: float = 33.0
    bold: bool = False
    italic: bool = False


def text_runs(text: str, base_size: float, base_color) -> list:
    """
    Ren'Py 文本标签 → `[(文字, _TextStyle), …]`。

    支持 `{color}` / `{b}` / `{i}` / `{size}` 与对应闭合标签；
    `{p}` 转换行、`{w}` 转空格；其余认不出的标签**丢掉标签本身**
    （宁可少一个特效，也不把 `{color=#f00}` 源码画给用户看）。
    """
    base = _TextStyle(color=tuple(base_color), size=float(base_size))
    state = _TextStyle(**base.__dict__)
    stack = []
    out = []
    buf = []

    def flush():
        if buf:
            out.append((''.join(buf), _TextStyle(**state.__dict__)))
            buf.clear()

    i = 0
    raw = str(text or '')
    while i < len(raw):
        if raw.startswith('{{', i):
            buf.append('{')
            i += 2
            continue
        if raw.startswith('}}', i):
            buf.append('}')
            i += 2
            continue
        if raw[i] == '{':
            end = raw.find('}', i + 1)
            if end < 0:
                buf.append(raw[i])
                i += 1
                continue
            flush()
            name, value = _split_tag(raw[i + 1:end])
            if name == 'color':
                stack.append(('color', state.color))
                state.color = _color(value, state.color)
            elif name == '/color' and stack:
                _restore(stack, state)
            elif name == 'size':
                stack.append(('size', state.size))
                try:
                    if value.startswith(('+', '-')):
                        state.size = max(1.0, base.size + float(value))
                    else:
                        state.size = max(1.0, float(value))
                except ValueError as e:
                    # `{size=…}` 里是表达式（`gui.text_size` 之类）——
                    # 算不出来就保持原字号，但记一笔（不画错、也不吞掉）。
                    swallow('stage_render.tag_size', e)
            elif name == '/size' and stack:
                _restore(stack, state)
            elif name == 'b':
                stack.append(('bold', state.bold))
                state.bold = True
            elif name == '/b' and stack:
                _restore(stack, state)
            elif name == 'i':
                stack.append(('italic', state.italic))
                state.italic = True
            elif name == '/i' and stack:
                _restore(stack, state)
            elif name in ('p', 'par'):
                buf.append('\n')
            elif name == 'w':
                buf.append(' ')
            # `{nw}` / `{fast}` / `{cps=…}` / 图像标签：不画，也不留源码
            i = end + 1
            continue
        buf.append(raw[i])
        i += 1
    flush()
    return out


def _restore(stack, state):
    while stack:
        key, old = stack.pop()
        if key == 'color':
            state.color = old
            return
        if key == 'size':
            state.size = old
            return
        if key == 'bold':
            state.bold = old
            return
        if key == 'italic':
            state.italic = old
            return


# ----------------------------------------------------------------------
#  字体
# ----------------------------------------------------------------------

def _font_variant(path: str, bold: bool, italic: bool) -> str:
    """同目录里找 `-Bold` / `-Italic` 变体；找不到就用原字体（合成粗体）。"""
    if not path:
        return path
    p = Path(path)
    if not p.is_file():
        return path
    stem = p.stem
    suffix = p.suffix
    cands = []
    if bold and italic:
        cands += [stem + '-BoldItalic', stem + '-BoldOblique',
                  stem.replace('-Regular', '-BoldItalic')]
    if bold:
        cands += [stem + '-Bold', stem.replace('-Regular', '-Bold'),
                  stem.replace('Regular', 'Bold')]
    if italic:
        cands += [stem + '-Italic', stem + '-Oblique',
                  stem.replace('-Regular', '-Italic')]
    for c in cands:
        for ext in (suffix, '.otf', '.ttf'):
            q = p.with_name(c + ext)
            if q.is_file():
                return str(q)
    return path


@lru_cache(maxsize=64)
def _load_font(path: str, size: int, bold: bool = False,
               italic: bool = False):
    if not _PIL_OK:
        return None
    chosen = _font_variant(path, bold, italic) if path else ''
    try:
        if chosen:
            return ImageFont.truetype(chosen, max(1, int(size)))
    except OSError as e:
        swallow('stage_render.load_font', e)
    for fallback in (r'C:\Windows\Fonts\msyh.ttc',
                     r'C:\Windows\Fonts\simhei.ttf',
                     r'C:\Windows\Fonts\arial.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(fallback, max(1, int(size)))
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=max(1, int(size)))
    except TypeError:
        return ImageFont.load_default()


# ----------------------------------------------------------------------
#  图片缓存
# ----------------------------------------------------------------------

#: `_load_scaled` 缓存默认预算（2026-10-30，⑦ P0）。
#:
#: 旧实现是 `lru_cache(maxsize=48)` —— 按**张数**淘汰。一张 8K 背景
#: 4320×7680 RGBA = **132.7MB**，48 张理论上 6.4GB；真实项目当前素材
#: （4K CG 33.2MB / 2K 背景 ~13MB / 立绘缩图 11.9MB）满槽也要 1GB 左右。
#: "高分辨率图会出问题"的风险点就在这。现在按**字节预算**淘汰：
#: 默认 256MB，可用环境变量调（`STORYGRAPH_STAGE_CACHE_BYTES` 字节，
#: 或 `STORYGRAPH_STAGE_CACHE_MB` 兆）。
DEFAULT_CACHE_BYTES = 256 * 1024 * 1024
CACHE_BYTES_ENV = 'STORYGRAPH_STAGE_CACHE_BYTES'
CACHE_MB_ENV = 'STORYGRAPH_STAGE_CACHE_MB'


def _int_env(name: str, scale: int = 1):
    """环境变量 → 非负整数；没设返回 None，坏值**留痕**后也返回 None。"""
    raw = (os.environ.get(name) or '').strip()
    if not raw:
        return None
    try:
        return max(0, int(float(raw) * scale))
    except (TypeError, ValueError) as e:      # 手改环境变量写错不该炸渲染
        swallow('stage_render.cache_env_budget', e)
        return None


def _env_cache_budget() -> int:
    """环境变量里的缓存预算（字节）；没设/坏值就用默认 256MB。"""
    got = _int_env(CACHE_BYTES_ENV)
    if got is None:
        got = _int_env(CACHE_MB_ENV, 1024 * 1024)
    return DEFAULT_CACHE_BYTES if got is None else got


class _ImageCache:
    """
    按**字节预算**淘汰的 LRU 图片缓存（线程安全）。

    为什么不是 `lru_cache`：它只认"张数"，而一张图多大完全取决于项目素材
    （8K 立绘 132MB vs 界面小图 0.1MB）—— 张数上限根本约束不住内存。
    """

    def __init__(self):
        self._items: OrderedDict = OrderedDict()
        self._bytes = 0
        self._explicit = None            # 测试/调用方显式设的预算（None = 环境变量）
        self._hits = 0
        self._misses = 0
        self._lock = threading.RLock()

    def budget(self) -> int:
        with self._lock:
            return (self._explicit if self._explicit is not None
                    else _env_cache_budget())

    def configure(self, max_bytes=None) -> int:
        """显式设预算（`None` = 回到环境变量/默认值）；返回生效的预算。"""
        with self._lock:
            self._explicit = (None if max_bytes is None
                              else max(0, int(max_bytes)))
            self._evict()
            return self.budget()

    def get(self, key):
        with self._lock:
            item = self._items.get(key)
            if item is None:
                self._misses += 1
                return None
            self._items.move_to_end(key)
            self._hits += 1
            return item[0]

    def put(self, key, img) -> None:
        size = int(getattr(img, 'width', 0)) * int(getattr(img, 'height', 0)) * 4
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._bytes -= old[1]
            budget = self.budget()
            if size > budget:
                # 单张就超预算：不缓存（但仍返回给调用方画），否则会立刻被淘汰
                return
            self._items[key] = (img, size)
            self._bytes += size
            self._evict()

    def _evict(self) -> None:
        budget = self.budget()
        while self._bytes > budget and self._items:
            _key, (_img, size) = self._items.popitem(last=False)
            self._bytes -= size

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0
            self._hits = 0
            self._misses = 0

    def info(self) -> dict:
        with self._lock:
            return {'entries': len(self._items), 'bytes': self._bytes,
                    'budget': self.budget(), 'hits': self._hits,
                    'misses': self._misses}


_SCALED_CACHE = _ImageCache()


def cache_info() -> dict:
    """当前图片缓存的状态（测试/诊断用）：条目数、字节数、预算、命中数。"""
    return _SCALED_CACHE.info()


def cache_clear() -> None:
    """清空图片缓存（测试/诊断用）。"""
    _SCALED_CACHE.clear()


def configure_cache(max_bytes=None) -> int:
    """设置图片缓存预算（`None` = 回到环境变量/默认 256MB）；返回生效值。"""
    return _SCALED_CACHE.configure(max_bytes)


def _resample_of(value):
    """`'nearest'` / 其它 → Pillow 重采样枚举（默认 LANCZOS）。"""
    if not _PIL_OK:
        return None
    text = str(value or '').strip().lower()
    if text in ('nearest', '1', 'true'):
        return Image.Resampling.NEAREST
    return Image.Resampling.LANCZOS


def _load_scaled(path: str, mtime: int, w: int, h: int, crop=None,
                 resample=None, full=None):
    """
    按目标像素尺寸解码；`crop` 时只返回目标坐标里的那一块（可见区域）。

    参数含义（2026-10-30，⑦ P0：让"解码目标"和"完整绘制尺寸"分开）：

      · `w` / `h` —— **要返回的像素数**（调用方已按屏幕可见区域封顶）；
      · `crop` —— 可见块在**完整绘制坐标**里的 `(x0, y0, x1, y1)`；
      · `full` —— 完整绘制尺寸 `(fw, fh)`（决定 zoom 比例；缺省 = (w, h)）。

    为什么要有 `crop`/`full`：没有 `at` 的大图按引擎语义是 **1:1 贴左上、
    右/下裁掉**。整图 8K 解出来 132.7MB，而画布上真正看得见的只有
    1920×1080（8.3MB）—— 先按比例把源图裁成可见那一块再缩，缓存里就只留
    屏幕大小；配合 `_ImageCache` 的字节预算，内存有上界。

    实现顺序：原图 →（按 full 比例映射到）源区域裁切 → 缩到可见尺寸。
    没给 `crop` 时等价于"整图缩到 w×h"（老行为）。
    """
    if not _PIL_OK:
        return None
    try:
        fw_out = max(1, int(round(float(w))))
        fh_out = max(1, int(round(float(h))))
        fw_full, fh_full = ((int(round(float(full[0]))),
                             int(round(float(full[1]))))
                            if full else (fw_out, fh_out))
        fw_full = max(1, fw_full)
        fh_full = max(1, fh_full)
    except (TypeError, ValueError):
        return None
    box = None
    if crop:
        try:
            x0, y0, x1, y1 = [int(round(float(v))) for v in crop]
        except (TypeError, ValueError):
            return None
        x0 = max(0, min(fw_full, x0))
        y0 = max(0, min(fh_full, y0))
        x1 = max(x0, min(fw_full, x1))
        y1 = max(y0, min(fh_full, y1))
        if x1 <= x0 or y1 <= y0:
            return None
        box = (x0, y0, x1, y1)
    mode = _resample_of(resample)
    key = (str(path), int(mtime), fw_out, fh_out, fw_full, fh_full, box,
           int(mode or 0))
    cached = _SCALED_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        with Image.open(path) as src:
            img = src.convert('RGBA')
            nw, nh = img.size
            if box is None:
                if (fw_out, fh_out) != img.size:
                    img = img.resize((fw_out, fh_out), mode)
                return _cache_put(key, img.copy())
            x0, y0, x1, y1 = box
            # 完整绘制坐标 → 源坐标：裁切框按"完整绘制尺寸/原图尺寸"的比例
            # 缩回源图，这样"先缩再裁"与"先裁再缩"几何等价，而内存只用
            # 可见那么大。
            sx = float(nw) / float(fw_full)
            sy = float(nh) / float(fh_full)
            src_box = (max(0, min(nw, int(round(x0 * sx)))),
                       max(0, min(nh, int(round(y0 * sy)))),
                       max(0, min(nw, int(round(x1 * sx)))),
                       max(0, min(nh, int(round(y1 * sy)))))
            if src_box[2] <= src_box[0] or src_box[3] <= src_box[1]:
                return None
            piece = img.crop(src_box)
            size = (fw_out, fh_out)
            if piece.size != size:
                piece = piece.resize(size, mode)
            return _cache_put(key, piece)
    except Exception as e:                    # noqa: BLE001 - 坏图不炸预览
        swallow('stage_render.load_scaled', e)
        return None


def _cache_put(key, img):
    """把解码结果放进字节预算缓存并返回它（单张超预算时不缓存）。"""
    _SCALED_CACHE.put(key, img)
    return img


def _mtime(path) -> int:
    try:
        return int(Path(path).stat().st_mtime_ns)
    except OSError:
        return 0


@lru_cache(maxsize=8)
def _image_cached(path: str, mtime: int):
    """
    只读一次盘的 RGBA 图（文本框底图 / 名条底图这种"每拍都一样"的素材）。

    为什么需要：`_draw_say` 从前**每一拍**都 `Image.open` + `convert('RGBA')`
    一次文本框底图（真机上的 textbox.png 是整屏宽、1080p 级别）。播放时
    一拍一次，纯属白读；缓存按 `(路径, mtime)` 失效 —— 作者改了底图照样生效。
    """
    if not _PIL_OK:
        return None
    try:
        with Image.open(path) as src:
            return src.convert('RGBA')
    except Exception as e:                    # noqa: BLE001 - 坏图不炸预览
        swallow('stage_render.image_cached', e)
        return None


@lru_cache(maxsize=16)
def _nine_slice_cached(path: str, mtime: int, w: int, h: int, borders: tuple):
    """`_nine_slice` 的缓存版（名条底图每拍一样，九宫格切一次就够）。"""
    img = _image_cached(path, mtime)
    if img is None:
        return None
    try:
        return _nine_slice(img, int(w), int(h), borders)
    except Exception as e:                    # noqa: BLE001
        swallow('stage_render.nine_slice_cached', e)
        return None


def _fill(canvas, color):
    """
    整屏刷一个颜色（`color` 收 Ren'Py 写法 `#rrggbb`，也收现成的 RGBA 元组）。

    **不透明时**走 `paste`：逐像素直写，比"造一张整屏 RGBA 再 alpha 混合"
    快一个量级（1920×1080 每帧都要刷底色，这是实打实的一拍一次）。
    半透明色仍然必须 `alpha_composite`（直写会把底下的层抠掉）。

    ⚠ 元组**不能**走 `_color`：它按字符串解析（`str((0,0,0,255))` 认不出，
    会落到白色 fallback —— 黑场会变成白屏，是最难查的那种"看起来没坏"）。
    """
    if isinstance(color, (tuple, list)):
        vals = list(color)
        rgba = tuple(vals[:3]) + ((vals[3],) if len(vals) > 3 else (255,))
    else:
        rgba = _color(color)
    if rgba[3] >= 255:
        canvas.paste(tuple(rgba[:3]), (0, 0, canvas.width, canvas.height))
    else:
        canvas.alpha_composite(Image.new('RGBA', canvas.size, rgba))


def _paste(canvas, img, left: float, top: float, alpha: float = 1.0):
    """把 RGBA 图按屏幕坐标叠上去（自动裁掉画面外部分）。"""
    if img is None:
        return
    if alpha < 0.999:
        img = img.copy()
        a = img.getchannel('A').point(lambda v: int(v * alpha))
        img.putalpha(a)
    x, y = int(round(left)), int(round(top))
    cw, ch = canvas.size
    iw, ih = img.size
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(cw, x + iw), min(ch, y + ih)
    if x1 <= x0 or y1 <= y0:
        return
    crop = img.crop((x0 - x, y0 - y, x1 - x, y1 - y))
    canvas.alpha_composite(crop, (x0, y0))


# ----------------------------------------------------------------------
#  结果
# ----------------------------------------------------------------------

@dataclass
class RenderResult:
    """一次离屏渲染的产物（PIL 图 + 本帧各块的位置）。"""

    image: object = None
    width: int = 0
    height: int = 0
    sprite_boxes: dict = field(default_factory=dict)   # 原 sprites 下标 -> box
    #: 背景在画布上的框 `(left, top, w, h)`（虚拟像素；2026-10-30 ⑦ P0，
    #: 给提示行/诊断用：`w > 屏幕宽` 就是"游戏里会裁切"）。
    background_box: tuple = (0, 0, 0, 0)
    #: 各非角色图层的框：`{tag: (left, top, w, h)}`。
    layer_boxes: dict = field(default_factory=dict)
    textbox_rect: tuple = (0, 0, 0, 0)
    name_rect: tuple = (0, 0, 0, 0)
    text_rect: tuple = (0, 0, 0, 0)
    warnings: list = field(default_factory=list)
    missing: list = field(default_factory=list)


@dataclass
class _Ctx:
    profile: object
    registry: object
    project_root: object
    screen: tuple
    transform_index: dict
    image_index: dict
    layer_model: object


# ----------------------------------------------------------------------
#  背景 / 立绘 / 图层
# ----------------------------------------------------------------------

def _resolve_path(spec, ctx):
    from storygraph.app.stage import resolve_stage_asset, resolve_solid_color
    path = resolve_stage_asset(spec, getattr(ctx.registry, 'images', {}) or {},
                               ctx.project_root, ctx.image_index)
    if path is not None:
        return path
    return None


def _solid(spec, ctx):
    from storygraph.app.stage import resolve_solid_color
    return resolve_solid_color(spec, getattr(ctx.registry, 'images', {}) or {},
                               ctx.project_root)


def _real_geometry(place) -> bool:
    """这个 `Placement` 是**真算出来**的吗（而不是按名字猜的）？"""
    return (place is not None
            and getattr(place, 'x_px', None) is not None
            and getattr(place, 'y_px', None) is not None)


def _at_box(at, ctx, natural, result, what, spec=''):
    """
    一张图的 `at` → 画布框 `(left, top, w, h)`（Ren'Py 语义）。

    · 没有 `at` → 原图尺寸 @ (0,0)（引擎就是这样：超出屏幕的右/下被裁）；
    · `at` 能算（项目 transform / 内置位置 / fit / zoom）→ `placement_box`；
    · `at` **认不出**（名字猜的）→ 不猜位置：按原图左上画，并明说可能与
      游戏不一致（"算不出还不说"比算错更糟）。
    """
    if not at:
        return 0.0, 0.0, float(natural[0]), float(natural[1])
    place = None
    try:
        place = placement_of(at, ctx.transform_index, ctx.registry, ctx.screen)
        box = placement_box(place, natural, ctx.screen)
    except Exception as e:                    # noqa: BLE001 - 坏几何不该炸预览
        swallow('stage_render.at_box', e)
        box = None
    if box is not None and _real_geometry(place):
        return box
    who = f'{what}「{spec}」' if spec else what
    result.warnings.append(
        f'{who} 的变换「{at}」认不出，预览按原图左上画（可能与游戏不一致）')
    return 0.0, 0.0, float(natural[0]), float(natural[1])


def _box_for(spec, at, ctx, natural):
    """背景框（兼容旧接口；`_at_box` 需要 `result` 记警告，这里只算几何）。"""
    if not at:
        return 0.0, 0.0, float(natural[0]), float(natural[1])
    try:
        place = placement_of(at, ctx.transform_index, ctx.registry, ctx.screen)
        box = placement_box(place, natural, ctx.screen)
    except Exception as e:                    # noqa: BLE001
        swallow('stage_render.background_box', e)
        box = None
    if box is not None and _real_geometry(place):
        return box
    return 0.0, 0.0, float(natural[0]), float(natural[1])


def _clip_to_screen(left, top, w, h, screen):
    """
    盒子裁到画布：返回 `(x, y, iw, ih, crop, fw, fh)`；完全不可见返回 None。

    `crop` 是**目标像素坐标**里的可见子矩形（给 `_load_scaled` 只解这一块）；
    `fw/fh` 是完整的绘制尺寸（它决定 zoom 比例，不能丢）。
    """
    try:
        cw = max(1, int(screen[0]))
        ch = max(1, int(screen[1]))
        x = int(round(float(left)))
        y = int(round(float(top)))
        fw = max(1, int(round(float(w))))
        fh = max(1, int(round(float(h))))
    except (TypeError, ValueError):
        return None
    x0 = max(0, -x)
    y0 = max(0, -y)
    x1 = min(fw, cw - x)
    y1 = min(fh, ch - y)
    if x1 <= x0 or y1 <= y0:
        return None
    return x + x0, y + y0, x1 - x0, y1 - y0, (x0, y0, x1, y1), fw, fh


def _draw_picture(canvas, path, box, screen, alpha: float = 1.0,
                  resample=None) -> bool:
    """
    把一张图按 `box` 画到画布上；**只解码画布上真正看得见的那一块**。

    这是背景 / 图层 / 立绘共用的贴图入口（2026-10-30 ⑦ P0）：
    `_clip_to_screen` 先把框裁到屏幕，`_load_scaled(crop=…)` 就只解/只缓存
    可见部分（8K 背景贴左上时缓存的是屏幕大小，不是 132MB 整图）。
    """
    if not box:
        return False
    clipped = _clip_to_screen(box[0], box[1], box[2], box[3], screen)
    if clipped is None:
        return False
    px, py, pw, ph, crop, fw, fh = clipped
    img = _load_scaled(path, _mtime(path), pw, ph, crop=crop,
                       resample=resample, full=(fw, fh))
    if img is None:
        return False
    _paste(canvas, img, px, py, alpha)
    return True


def _draw_background(canvas, frame, ctx, result):
    scene = str(getattr(frame, 'scene', '') or '')
    if not scene:
        _fill(canvas, (0, 0, 0, 255))
        return
    color = None
    path = _resolve_path(scene, ctx)
    if path is None:
        color = _solid(scene, ctx)
    if path:
        try:
            with Image.open(path) as src:
                natural = src.size
        except Exception as e:                # noqa: BLE001
            swallow('stage_render.background_size', e)
            natural = None
        if natural:
            at = str(getattr(frame, 'scene_at', '') or '')
            box = _at_box(at, ctx, natural, result, '背景', scene)
            result.background_box = tuple(float(v) for v in box)
            if _draw_picture(canvas, path, box, ctx.screen):
                return
    if color:
        _fill(canvas, color)
        return
    _fill(canvas, (18, 18, 24, 255))
    if scene:
        result.warnings.append(f'背景找不到：{scene}')
        result.missing.append(scene)


def _draw_sprites(canvas, frame, ctx, result):
    sprites = list(getattr(frame, 'sprites', []) or [])
    # 按 `snapshot_rows` 的原始顺序叠（先 show 的在下面）；说话人高亮
    # 只是 UI 信息，不能改变遮挡关系。
    order = sorted(range(len(sprites)),
                   key=lambda i: (getattr(sprites[i], 'order_index', i), i))
    for i in order:
        s = sprites[i]
        spec = str(getattr(s, 'image', '') or '')
        path = _resolve_path(spec, ctx)
        if not path:
            color = _solid(spec, ctx)
            if color:
                continue                     # 纯色立绘极少见；背景已处理
            result.warnings.append(f'立绘找不到：{spec}')
            result.missing.append(spec)
            continue
        place = getattr(s, 'place', None)
        try:
            with Image.open(path) as src:
                natural = src.size
        except Exception:                     # noqa: BLE001
            result.warnings.append(f'立绘读不出：{spec}')
            continue
        try:
            box = placement_box(place, natural, ctx.screen)
            if box is None:
                box = (0.0, 0.0, natural[0], natural[1])
        except Exception as e:                # noqa: BLE001
            swallow('stage_render.sprite_box', e)
            box = (0.0, 0.0, natural[0], natural[1])
        left, top, w, h = box
        # 画面外的立绘不 decode：省内存也省时间
        if left >= ctx.screen[0] or top >= ctx.screen[1] or \
                left + w <= 0 or top + h <= 0:
            result.warnings.append(f'立绘在画面外：{spec}')
            continue
        angle = float(getattr(place, 'rotate', 0.0) or 0.0)
        alpha = float(getattr(place, 'alpha', 1.0) or 1.0)
        if angle:
            # 旋转必须先拿整图（旋转后包围盒会变），走老路。
            img = _load_scaled(path, _mtime(path), int(w), int(h))
            if img is None:
                result.warnings.append(f'立绘解码失败：{spec}')
                continue
            img = img.rotate(-angle, expand=True,
                             resample=Image.Resampling.BICUBIC)
            left -= (img.width - w) / 2.0
            top -= (img.height - h) / 2.0
            _paste(canvas, img, left, top, alpha)
        else:
            resample = 'nearest' if getattr(place, 'nearest', False) else None
            if not _draw_picture(canvas, path, box, ctx.screen,
                                 alpha=alpha, resample=resample):
                result.warnings.append(f'立绘解码失败：{spec}')
                continue
        result.sprite_boxes[i] = (left, top, w, h)


def _draw_layers(canvas, frame, ctx, result):
    raw = str(getattr(frame, 'layers', '') or '')
    if not raw:
        return
    ats = dict(getattr(frame, 'layers_at', None) or {})
    for spec in [p.strip() for p in raw.split(';') if p.strip()]:
        tag = spec.split()[0] if spec.split() else spec
        at = str(ats.get(tag) or ats.get(spec) or '')
        path = _resolve_path(spec, ctx)
        color = None
        if path is None:
            color = _solid(spec, ctx)
        if path:
            try:
                with Image.open(path) as src:
                    natural = src.size
            except Exception as e:            # noqa: BLE001
                swallow('stage_render.layer_size', e)
                natural = None
            if natural:
                what = 'CG' if tag == 'cg' else f'图层 {tag}'
                box = _at_box(at, ctx, natural, result, what, spec)
                result.layer_boxes[tag] = tuple(float(v) for v in box)
                _draw_picture(canvas, path, box, ctx.screen)
                continue
        if color:
            canvas.alpha_composite(Image.new('RGBA', canvas.size,
                                             _color(color)))
            continue
        result.warnings.append(f'图层找不到：{spec}')
        result.missing.append(spec)


# ----------------------------------------------------------------------
#  尺寸提示（人话）—— P1
# ----------------------------------------------------------------------

#: 图片"原始像素尺寸"缓存（只读文件头，不解码整图）。
_NATURAL_SIZE_CACHE: dict = {}
_NATURAL_SIZE_LIMIT = 256
#: 项目 transform 表缓存（`scan_transforms` 要读几十~几百个 .rpy；提示行每拍
#: 都要问，不能每拍扫盘）。预览窗口有时会把自己的表传进来，优先用那个。
_TRANSFORM_INDEX_CACHE: dict = {}


def _natural_size(path):
    """图片原始尺寸 `(w, h)`；读不到返回 None（只读文件头，<1ms）。"""
    if not _PIL_OK or not path:
        return None
    try:
        st = Path(path).stat()
        key = (str(path), int(st.st_mtime_ns))
    except OSError:
        return None
    got = _NATURAL_SIZE_CACHE.get(key)
    if got is not None:
        return got
    try:
        with Image.open(path) as im:
            got = (int(im.width), int(im.height))
    except Exception as e:                     # noqa: BLE001 - 坏图不该拦住提示
        swallow('stage_render.natural_size', e)
        return None
    if len(_NATURAL_SIZE_CACHE) >= _NATURAL_SIZE_LIMIT:
        _NATURAL_SIZE_CACHE.clear()
    _NATURAL_SIZE_CACHE[key] = got
    return got


def _transform_index_of(project_root, index=None):
    """项目 transform 表；`index` 显式给了就用它（预览窗口自己扫好的那份）。"""
    if index is not None:
        return index
    if not project_root:
        return {}
    key = str(project_root)
    got = _TRANSFORM_INDEX_CACHE.get(key)
    if got is not None:
        return got
    try:
        from storygraph.story.atl import scan_transforms
        got = scan_transforms(project_root) or {}
    except Exception as e:                     # noqa: BLE001 - 提示失败不影响画面
        swallow('stage_render.transform_index', e)
        got = {}
    if len(_TRANSFORM_INDEX_CACHE) >= 32:
        _TRANSFORM_INDEX_CACHE.clear()
    _TRANSFORM_INDEX_CACHE[key] = got
    return got


def _at_phrase(at, place) -> str:
    """`at` 的人话：`无 at` / `at bgfit（fit "cover"）` / `at x（认不出…）`。"""
    at = str(at or '').strip()
    if not at:
        return '无 at'
    if not _real_geometry(place):
        return f'at {at}（认不出，预览按原图左上画，可能与游戏不一致）'
    bits = []
    fit = str(getattr(place, 'fit', '') or '')
    if fit:
        bits.append(f'fit "{fit}"')
    xs = getattr(place, 'xsize', None)
    ys = getattr(place, 'ysize', None)
    if xs is not None:
        bits.append(f'xsize {float(xs):g}')
    if ys is not None:
        bits.append(f'ysize {float(ys):g}')
    xz = float(getattr(place, 'xzoom', 1.0) or 1.0)
    yz = float(getattr(place, 'yzoom', 1.0) or 1.0)
    if abs(xz - yz) < 1e-6:
        if abs(xz - 1.0) > 1e-6:
            bits.append(f'zoom {xz:g}')
    else:
        bits.append(f'xzoom {xz:g}，yzoom {yz:g}')
    if not bits:
        bits.append('位置')
    return f'at {at}（{"，".join(bits)}）'


def _relation_phrase(box, screen) -> str:
    """框 vs 屏幕：`正好铺满` / `游戏里会被裁切` / `游戏里会留黑边`。"""
    if not box:
        return '尺寸未知'
    try:
        sw, sh = float(screen[0]), float(screen[1])
        left, top, w, h = [float(v) for v in box]
    except (TypeError, ValueError):
        return '尺寸未知'
    over = (left < -0.5 or top < -0.5 or left + w > sw + 0.5
            or top + h > sh + 0.5)
    under = (w < sw - 0.5 or h < sh - 0.5 or left > 0.5 or top > 0.5)
    if over and under:
        return '游戏里会裁切并留黑边'
    if over:
        return '游戏里会被裁切'
    if under:
        return '游戏里会留黑边'
    return '正好铺满'


def _size_note(label, spec, natural, at, index, registry,
               project_root, screen) -> str:
    """一条人话尺寸提示（提示行/测试共用；纯文本）。"""
    place = None
    box = None
    at = str(at or '').strip()
    if at:
        try:
            place = placement_of(at, index, registry, screen)
            box = placement_box(place, natural, screen)
        except Exception as e:                 # noqa: BLE001
            swallow('stage_render.size_note', e)
            place, box = None, None
    if box is None:
        box = (0.0, 0.0, float(natural[0]), float(natural[1]))
    sw, sh = int(screen[0]), int(screen[1])
    return (f'{label} {spec} {natural[0]}×{natural[1]} → 屏幕 {sw}×{sh}'
            f'（{_at_phrase(at, place)}：{_relation_phrase(box, screen)}）')


def scale_notes(frame, registry=None, project_root=None, screen=None,
                transform_index=None, limit: int = 3) -> list:
    """
    这一拍的**尺寸人话**（P1：用户二轮反馈"图调不了大小"）。

    形如：

        背景 bg bg7_0 2304×1296 → 屏幕 1920×1080（无 at：游戏里会被裁切）
        CG cg cg01 3840×2160 → 屏幕 1920×1080（at cg38402160（zoom 0.5）：正好铺满）

    **只读、不改任何数据**（"预览不骗人"：游戏里会裁就写会裁，不会替它 fit）。
    没装 Pillow / 图找不到 / 认不出尺寸时返回空表或跳过（缺图有别的提示）。
    """
    if frame is None or not _PIL_OK:
        return []
    from storygraph.story.atl import project_screen

    try:
        sw, sh = (tuple(screen) + (1920, 1080))[:2] if screen \
            else (project_screen(project_root) if project_root
                  else (1920, 1080))
        screen_pair = (max(1, int(sw)), max(1, int(sh)))
    except (TypeError, ValueError):
        screen_pair = (1920, 1080)
    images = getattr(registry, 'images', {}) or {}
    index = _transform_index_of(project_root, transform_index)
    from storygraph.app.stage import resolve_stage_asset

    def resolve(spec):
        try:
            return resolve_stage_asset(spec, images, project_root)
        except Exception as e:                 # noqa: BLE001 - 解析失败就不提示
            swallow('stage_render.scale_notes.resolve', e)
            return None

    notes = []
    scene = str(getattr(frame, 'scene', '') or '').strip()
    if scene:
        natural = _natural_size(resolve(scene))
        if natural:
            at = str(getattr(frame, 'scene_at', '') or '')
            notes.append(_size_note('背景', scene, natural, at, index, registry,
                                    project_root, screen_pair))
    raw = str(getattr(frame, 'layers', '') or '')
    ats = dict(getattr(frame, 'layers_at', None) or {})
    try:
        from storygraph.core.vocabulary import LAYER_CN_TAG
    except Exception as e:                     # noqa: BLE001 - 词表读不到就退 tag
        swallow('stage_render.scale_notes.layer_cn', e)
        LAYER_CN_TAG = {}
    for spec in [p.strip() for p in raw.split(';') if p.strip()][:max(0, limit)]:
        tag = spec.split()[0] if spec.split() else spec
        at = str(ats.get(tag) or ats.get(spec) or '')
        natural = _natural_size(resolve(spec))
        if not natural:
            continue
        label = LAYER_CN_TAG.get(tag, tag)
        notes.append(_size_note(label, spec, natural, at, index, registry,
                                project_root, screen_pair))
    return notes


# ---------------------------------------------------------------------------
#  过场说明（卡片「当前舞台」与演出预览共用）
# ---------------------------------------------------------------------------

#: 过场时长未知（`Effect.duration == 0`）时，预览按这个秒数近似演；
#: 说明行里也写这个数（"时长未知，按 0.35s 近似"）—— 一处常量，两处一致。
FX_FALLBACK_SECONDS = 0.35


def _fmt_transition_arg(value) -> str:
    """过场参数 → 一行里好读的字面量（认不出的参数原样写，不猜值）。"""
    from storygraph.story.transition_expr import NonLiteral

    if isinstance(value, NonLiteral):
        return str(value.text or '…')
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, (int, float)):
        return f'{value:g}' if isinstance(value, float) else str(value)
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(_fmt_transition_arg(v) for v in value) + ']'
    return str(value)


def _transition_call_text(call, effect) -> str:
    """
    一次过场调用 → 说明行里的那一项（**只读、不编号**）。

    三种长相（与用户拍板的样例一致）：
      · 构造器调用 → `Dissolve 0.1（master）`（参数是字面量才写）；
      · 项目自定义名 → 用效果的人话名，如 `闪黑 1s（0.5 出 + 0 停 + 0.5 入）`；
      · 认不出 → `名字（无法精确预览）`（**绝不写成"淡入"**）。
    """
    name = str(getattr(call, 'name', '') or '').strip()
    expr = str(getattr(call, 'expr', '') or '').strip()
    name = name or expr or '过场'
    kind = str(getattr(effect, 'kind', '') or '') if effect is not None else ''
    if effect is not None and kind in ('', 'unknown'):
        return f'{name}（无法精确预览）'
    args = tuple(getattr(call, 'args', ()) or ())
    layer = str(getattr(call, 'layer', '') or '').strip()
    if args:
        text = name + ' ' + ' '.join(_fmt_transition_arg(a) for a in args)
        return text + (f'（{layer}）' if layer else '')
    if effect is not None:
        label = str(getattr(effect, 'label', '') or '')
        if label:
            if float(getattr(effect, 'duration', 0.0) or 0.0) <= 0 \
                    and kind not in ('pause', 'solid'):
                label += f'（时长未知，按 {FX_FALLBACK_SECONDS:g}s 近似）'
            return label
    return name + (f'（{layer}）' if layer else '')


def _frame_transition_effects(frame, project_root=None) -> tuple:
    """一帧的过场效果（帧上带着的最好；有项目根就按项目目录重解析一遍）。"""
    calls = tuple(getattr(frame, 'transitions', ()) or ())
    effects = tuple(getattr(frame, 'effects', ()) or ())
    if not calls:
        return effects
    if project_root:
        try:
            from storygraph.app.stage import project_transitions
            from storygraph.story.transition_catalog import effect_of
            catalog = project_transitions(project_root)
            return tuple(effect_of(call, catalog) for call in calls)
        except Exception as e:                # noqa: BLE001 - 解析失败就退回帧上的
            swallow('stage_render.transition_effects', e)
    if effects:
        return effects
    try:
        from storygraph.story.transition_catalog import effect_of
        return tuple(effect_of(call, {}) for call in calls)
    except Exception as e:                    # noqa: BLE001 - 提示失败不影响画面
        swallow('stage_render.transition_effects.fallback', e)
        return ()


def transition_notes(frame, project_root=None, limit: int = 2) -> list:
    """
    这一拍的**过场说明行**（卡片「当前舞台」与演出预览共用，只读）。

    形如：

        过场：Dissolve 0.1（master）
        过场：闪黑 1s（0.5 出 + 0 停 + 0.5 入）
        过场：WeirdTransition（无法精确预览）

    一拍多条**只读、不编号**：最多列 `limit` 条，后面用「（…共 N 条）」收尾。
    `unknown` 只写名字 + "无法精确预览" —— 消费方只显示文字，**不套默认淡入**。
    没有过场返回 `[]`（调用方据此一行都不加，不做"硬切"占位）。

    `project_root` 给了就按项目 `define` 的转场目录重解析（帧可能是在没有
    项目根的时候构造的，自定义名当时解析不出来）。
    """
    if frame is None:
        return []
    calls = tuple(getattr(frame, 'transitions', ()) or ())
    effects = _frame_transition_effects(frame, project_root)
    items: list = []
    for index, call in enumerate(calls):
        effect = effects[index] if index < len(effects) else None
        text = _transition_call_text(call, effect)
        if text and text not in items:
            items.append(text)
    if not calls:
        # 老调用方构造的帧只有 `with_`（没有派生过场）：原样写出来，不装效果
        raw = str(getattr(frame, 'with_', '') or '').strip()
        if raw:
            return [f'过场：{raw}']
        for effect in effects:
            label = str(getattr(effect, 'label', '') or '').strip()
            if label and label not in items:
                items.append(label)
    if not items:
        return []
    total = len(items)
    shown = items[:max(1, int(limit or 1))]
    line = '、'.join(shown)
    if total > len(shown):
        line += f'（…共 {total} 条）'
    return [f'过场：{line}']


# ----------------------------------------------------------------------
#  文本框 / 名条 / 台词
# ----------------------------------------------------------------------

def _borders_key(borders) -> tuple:
    """`Frame(...)` 的边框值 → 可哈希的键（`lru_cache` 只认不可变参数）。"""
    try:
        vals = tuple(borders or (0, 0, 0, 0))
    except TypeError:
        return (0, 0, 0, 0)
    if len(vals) < 4:
        vals = (vals + (0, 0, 0, 0))[:4]
    return tuple(float(v) for v in vals[:4])


def _nine_slice(img, w: int, h: int, borders):
    """把 `Frame(...)` 底图缩放到 w×h（四角不拉伸）。"""
    l, t, r, b = [max(0, int(round(v))) for v in (borders or (0, 0, 0, 0))]
    iw, ih = img.size
    if iw <= 0 or ih <= 0:
        return img
    l, r = min(l, iw // 2), min(r, iw // 2)
    t, b = min(t, ih // 2), min(b, ih // 2)
    w, h = max(1, int(w)), max(1, int(h))
    if l + r >= w or t + b >= h:
        return img.resize((w, h), Image.Resampling.LANCZOS)
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    xs = [(0, l, 0, l), (l, iw - r, l, w - r), (iw - r, iw, w - r, w)]
    ys = [(0, t, 0, t), (t, ih - b, t, h - b), (ih - b, ih, h - b, h)]
    for sx0, sx1, dx0, dx1 in xs:
        if dx1 <= dx0:
            continue
        for sy0, sy1, dy0, dy1 in ys:
            if dy1 <= dy0:
                continue
            piece = img.crop((sx0, sy0, sx1, sy1))
            if piece.size != (dx1 - dx0, dy1 - dy0):
                piece = piece.resize((dx1 - dx0, dy1 - dy0),
                                     Image.Resampling.LANCZOS)
            out.alpha_composite(piece, (dx0, dy0))
    return out


def _measure(draw, text, font):
    try:
        return float(draw.textlength(text, font=font))
    except Exception:                     # noqa: BLE001 - 老 Pillow
        return float(font.getsize(text)[0])


def _units(runs, fonts):
    """标签 run → 换行单位（CJK 逐字、拉丁按词，空格单独成单位）。"""
    units = []
    for text, style in runs:
        font = fonts(style)
        buf = ''
        for ch in text:
            if ch == '\n':
                if buf:
                    units.append((buf, style, font))
                    buf = ''
                units.append(('\n', style, font))
            elif ch.isspace():
                if buf:
                    units.append((buf, style, font))
                    buf = ''
                units.append((' ', style, font))
            elif ord(ch) < 128 and (ch.isalnum() or ch in '_-'):
                buf += ch
            else:
                if buf:
                    units.append((buf, style, font))
                    buf = ''
                units.append((ch, style, font))
        if buf:
            units.append((buf, style, font))
    return units


def _wrap(units, width, draw):
    """单位 → 行（每行 `[(文字, 字体, 样式)]`）。"""
    lines = [[]]
    line_w = 0.0
    for text, style, font in units:
        if text == '\n':
            lines.append([])
            line_w = 0.0
            continue
        tw = _measure(draw, text, font)
        if text == ' ' and line_w <= 0:
            continue                      # 行首空格不占位
        if line_w + tw > width and lines[-1]:
            lines.append([])
            line_w = 0.0
            if text == ' ':
                continue
        # 超长单词：按字符硬断（否则会冲出文本框）
        if tw > width and len(text) > 1:
            for ch in text:
                cw = _measure(draw, ch, font)
                if line_w + cw > width and lines[-1]:
                    lines.append([])
                    line_w = 0.0
                lines[-1].append((ch, font, style))
                line_w += cw
            continue
        lines[-1].append((text, font, style))
        line_w += tw
    while len(lines) > 1 and not lines[-1]:
        lines.pop()
    return lines


def _draw_lines(canvas, lines, x, y, width, align, line_spacing,
                outline, result, base_size):
    draw = ImageDraw.Draw(canvas)
    # 行高用字体度量（与 Ren'Py 的 Text 一样按 ascent+descent 走）
    any_font = None
    for line in lines:
        for _t, font, _s in line:
            any_font = font
            break
        if any_font is not None:
            break
    ascent, descent = (any_font.getmetrics() if any_font is not None
                       else (int(base_size), int(base_size * 0.25)))
    line_h = max(1, int(ascent + descent))
    max_w = width
    y0 = int(round(y))
    for line in lines:
        line_w = sum(_measure(draw, t, f) for t, f, _s in line)
        if align == 0.5:
            lx = x + (max_w - line_w) / 2.0
        elif align == 1.0:
            lx = x + (max_w - line_w)
        else:
            lx = x
        cx = lx
        for text, font, style in line:
            stroke_w = 0
            stroke_fill = None
            if outline:
                stroke_w = max(1, int(round(float(outline[0][0]))))
                stroke_fill = _color(outline[0][1])
            for k, (off_x, off_y) in enumerate(
                    ((0, 0),) + (((1, 0),) if style.bold and
                                 _font_variant(
                                     getattr(font, 'path', ''), True,
                                     False) == getattr(font, 'path', '')
                                 else ())):
                draw.text((cx + off_x, y0 + off_y), text, font=font,
                          fill=style.color, stroke_width=stroke_w,
                          stroke_fill=stroke_fill, anchor='la')
            cx += _measure(draw, text, font)
        y0 += line_h + int(round(line_spacing))
    result.text_rect = (int(round(x)), int(round(y)), int(round(max_w)),
                        max(1, y0 - int(round(y))))


def _draw_say(canvas, frame, ctx, result):
    profile = ctx.profile
    if not getattr(frame, 'shows_window', False):
        return
    sw, sh = ctx.screen
    th = float(profile.textbox_height)
    top = (sh - th) * float(profile.textbox_yalign)
    result.textbox_rect = (0, int(round(top)), int(sw), int(round(th)))

    # 文本框底图：`Image(...)` 不拉伸，底边居中（模板 screens.rpy 的写法）
    if profile.textbox_image:
        tb = _image_cached(str(profile.textbox_image),
                          _mtime(profile.textbox_image))
        if tb is not None:
            x = (sw - tb.width) / 2.0
            y = top + th - tb.height
            _paste(canvas, tb, x, y)
    else:
        shade = Image.new('RGBA', (int(sw), int(th)), (8, 8, 12, 210))
        _paste(canvas, shade, 0, top)

    # 名条
    style = profile.character_for(getattr(frame, 'speaker', ''),
                                  getattr(frame, 'speaker_name', ''))
    who = str(getattr(frame, 'display_speaker', '') or '')
    if who:
        who = (style.who_prefix or '') + who + (style.who_suffix or '')
        name_color = _color(style.who_color or profile.name_text_color)
        # 名条字体：角色自己的 `who_font` 优先（与 Ren'Py 一致），否则全局。
        font_path, _src = profile.font_for(
            getattr(frame, 'speaker', ''),
            str(getattr(frame, 'display_speaker', '') or ''), 'who')
        font = _load_font(font_path or '', int(profile.name_text_size))
        tmp = Image.new('RGBA', (10, 10))
        td = ImageDraw.Draw(tmp)
        tw = _measure(td, who, font)
        pad_l, pad_t, pad_r, pad_b = profile.namebox_padding
        box_h = float(profile.values.get('namebox_height') or 0) or \
            (profile.name_text_size + pad_t + pad_b + 8)
        box_w = float(profile.values.get('namebox_width') or 0) or \
            (tw + pad_l + pad_r + 10)
        box_w, box_h = max(8, box_w), max(8, box_h)
        x = float(profile.values.get('name_xpos') or 0)
        xalign = float(profile.values.get('name_xalign') or 0)
        x -= box_w * xalign
        y = top + float(profile.values.get('name_ypos') or 0)
        if profile.namebox_image:
            nb = _nine_slice_cached(
                str(profile.namebox_image), _mtime(profile.namebox_image),
                int(box_w), int(box_h),
                _borders_key(profile.values.get('namebox_borders')))
            if nb is not None:
                _paste(canvas, nb, x, y)
        else:
            _paste(canvas, Image.new('RGBA', (int(box_w), int(box_h)),
                                     (0, 0, 0, 170)), x, y)
        # 文字在名条里垂直居中、按 name_xalign 对齐
        ascent, descent = font.getmetrics()
        ty = y + (box_h - (ascent + descent)) / 2.0
        if xalign == 1.0:
            tx = x + box_w - pad_r - tw
        elif xalign == 0.5:
            tx = x + (box_w - tw) / 2.0
        else:
            tx = x + pad_l
        draw = ImageDraw.Draw(canvas)
        stroke_w = 0
        stroke_fill = None
        outlines = profile.name_text_outlines
        if outlines:
            stroke_w = max(1, int(round(float(outlines[0][0]))))
            stroke_fill = _color(outlines[0][1])
        draw.text((tx, ty), who, font=font, fill=name_color,
                  stroke_width=stroke_w, stroke_fill=stroke_fill, anchor='la')
        result.name_rect = (int(round(x)), int(round(y)),
                            int(round(box_w)), int(round(box_h)))

    # 台词
    text = str(getattr(frame, 'text', '') or '')
    if style.what_prefix and not text.startswith(style.what_prefix):
        text = style.what_prefix + text
    if style.what_suffix and not text.endswith(style.what_suffix):
        text = text + style.what_suffix
    base_color = _color(style.what_color or profile.text_color)
    # 台词字体：角色自己的 `what_font` 优先，其次 `gui.dialogue_text_font`
    # （`gui.text_properties("dialogue")` 的解析顺序），最后才是 `gui.text_font`。
    dlg_size = profile.dialogue_text_size
    runs = text_runs(text, dlg_size, base_color)
    font_path, _src = profile.font_for(
        getattr(frame, 'speaker', ''),
        str(getattr(frame, 'display_speaker', '') or ''), 'what')
    font_path = font_path or ''
    font_cache = {}

    def fonts(style_):
        key = (style_.size, style_.bold, style_.italic)
        if key not in font_cache:
            font_cache[key] = _load_font(font_path, int(style_.size),
                                         style_.bold, style_.italic)
        return font_cache[key]

    units = _units(runs, fonts)
    width = float(profile.values.get('dialogue_width') or 1116)
    # 先量再断：`_wrap` 需要 draw
    tmp = Image.new('RGBA', (10, 10))
    lines = _wrap(units, width, ImageDraw.Draw(tmp))
    x = float(profile.values.get('dialogue_xpos') or 0)
    y = top + float(profile.values.get('dialogue_ypos') or 0)
    align = float(profile.values.get('dialogue_text_xalign') or 0)
    _draw_lines(canvas, lines, x, y, width, align,
                profile.line_spacing, profile.dialogue_text_outlines, result,
                dlg_size)


# ----------------------------------------------------------------------
#  缩略图上的可读名字 / 台词（2026-10-29）
# ----------------------------------------------------------------------
#
# 用户 2026-10-29：「渲染有问题」—— 卡片「当前舞台」那张 288×162 的小图里，
# 名字条和台词缩成了方块图案。根因不是画错了，是**尺度**：整帧按项目分辨率
# （典型 1920×1080）画，26~30px 的台词缩到 480 宽的 target 只剩 6~7px，再画
# 到 288 的控件上只剩 3~4px —— 字的笔画糊在一起就是一片方块。
#
# 所以：整帧照旧渲染（背景/立绘/文本框都是真实排版），**缩到 target 之后**
# 再把名字/台词按缩略图自己的可读字号重画一遍。只给 compact 小图用；舞台
# 预览窗口和卡片整宽图仍是"原汁原味"的引擎排版（那里的字本来就看得清）。
#
# 第二版（同一天，真机上验出来）：**先不透明铺平条带 + 名条，再画字**。
# 第一版只铺条带、alpha=245，结果 ① 缩放残留的原文透出"鬼影"；② 名条
# （`gui.name_ypos = -58` 的项目里它在文本框上方）没盖，原名字还在、重画的
# 名字又画在它下面 —— 像写了两个名字。现在的做法见 `draw_readable_say`。

#: 缩略图上名字 / 台词的**最小可读字号**（像素）。
#: 少量字的名称至少 11px、整句台词至少 9px，再小就回到"方块"了。
MIN_READABLE_PX = {'name': 11, 'dialogue': 9}

#: 字号 / 图宽的比例：288 宽的小图上名字 ≈12px、台词 ≈10px。
#:
#: 为什么用比例而不是定值：target 宽度由"控件宽 × 设备像素比"决定（夹在
#: 480~1280），最后统一画到 288 的控件上。按比例算，480 宽 target 上画的
#: 20px 字画到 288 上正好还是 12px —— 换机器/换缩放比例，看得清的程度一致。
READABLE_PX_RATIO = {'name': 0.042, 'dialogue': 0.035}

#: 字号上限：target 特别大或文本框特别矮时，别让字把条带撑爆。
MAX_READABLE_PX = {'name': 40, 'dialogue': 34}

#: 铺底色叠上去的不透明度 —— **全不透明**（2026-10-29 第二版）。
#:
#: 第一版用 245（想留一点文本框纹理），真机上被一眼看出两个毛病：
#:   ① 缩放后残留的原文会透出来一层"鬼影"，和大字叠成两层；
#:   ② 名条（`name_rect`）根本没盖 —— `gui.name_ypos = -58` 的项目里名条在
#:      文本框**上方**，原名字还在、重画的名字又在下面画一个，像写了两个名字。
#: 所以现在条带与名条**按各自区域的平均色不透明铺平**，原文字一个像素都不透。
_READABLE_BASE_ALPHA = 255

#: 名条**左右各留多少**内边距（× 名字字号）—— 名条长到包住字时用。
#:
#: 第三版（2026-10-29）：名条的底允许长得比引擎的原框更大。第一/二版把名字
#: 字号夹在名条宽高里，真机上等于没修 —— sr_general 的名条在 1920×1080 下
#: 257×80，缩到 576 target 只剩 77×24，"「凌跃捷」"5 个字形被压到 14px
#: （画到 288 控件只剩 7px），汉字还是糊的。可这张小图本来就是**缩略图专用
#: 重排**：位置/颜色照引擎，名条的底跟着字长（见 `_fit_name_tag`）。
_NAME_TAG_PAD_X_RATIO = 0.22

#: 名条**上下各留多少**内边距（× 名字字号）。只留一点点：名条常贴在文本框
#: 上方（`name_ypos = -58`），往下长得太多会压住台词第一行的字头。
_NAME_TAG_PAD_Y_RATIO = 0.10

#: 名字的最后底线字号：再小就回到"方块"了（与 `MIN_READABLE_PX['name']`
#: 同一个量级；名条长到画面边缘还放不下时，宁可缩到这里也不能出画面）。
_NAME_BOX_MIN_PX = 9

#: 小于这个字号就**不描边**。为什么：1px 描边画在 14px 的汉字上等于把笔画
#: 吃掉一半（实测名字会糊成一块灰），而铺底的平均色已经保证和字色有对比，
#: 所以小字宁可不要描边。15px 以上描边只是让轮廓更清楚。
_READABLE_STROKE_MIN_PX = 16

#: 系统里的中文字体候选，**顺序即优先级**。微软雅黑最接近 Windows 上 Ren'Py
#: 模板的观感，其次黑体/宋体；非 Windows 给几个常见的 Noto / 文泉驿。
_CJK_FONT_CANDIDATES = (
    r'C:\Windows\Fonts\msyh.ttc',
    r'C:\Windows\Fonts\msyhl.ttc',
    r'C:\Windows\Fonts\simhei.ttf',
    r'C:\Windows\Fonts\simsun.ttc',
    '/System/Library/Fonts/PingFang.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
)


@lru_cache(maxsize=1)
def _cjk_font_path() -> str:
    """
    系统里能画中文的字体路径（找不到返回空串）。

    为什么需要单独找：Pillow 自带的 `ImageFont.load_default()` **没有汉字**，
    字号又只有 9~20px，落到它上面中文全变方块 —— 那正是用户报的现象。
    """
    for path in _CJK_FONT_CANDIDATES:
        try:
            if Path(path).is_file():
                return str(path)
        except OSError as e:                  # 权限/坏链接：换下一个候选
            swallow('stage_render.cjk_font', e)
    return ''


def _truetype(path: str, size: int, bold: bool = False,
              italic: bool = False):
    """只尝试**这一支**字体（含同目录的 Bold/Italic 变体），不掺任何回退。"""
    if not path or not _PIL_OK:
        return None
    want = max(1, int(size))
    chosen = _font_variant(str(path), bold, italic)
    for cand in (chosen, str(path)):
        try:
            return ImageFont.truetype(str(cand), want)
        except (OSError, ValueError) as e:    # 坏字体/权限：换下一个候选
            swallow('stage_render.readable_font', e)
    return None


@lru_cache(maxsize=64)
def _load_readable_font(path: str, size: int, bold: bool = False,
                        italic: bool = False):
    """
    缩略图重画专用字体加载器：**绝不**悄悄落到 Pillow 默认字体。

    与 `_load_font` 的区别：那个给 1920×1080 的整帧用（字号 20~45px，默认
    字体的缺憾还能看出轮廓）；缩略图只有 9~20px，默认字体连中文都没有，
    所以这里宁可换成系统的中文字体，也不画方块。
    """
    if not _PIL_OK:
        return None
    want = max(1, int(size))
    font = _truetype(path, want, bold, italic)
    if font is not None:
        return font
    cjk = _cjk_font_path()
    if cjk:
        font = _truetype(cjk, want, bold, italic)
        if font is not None:
            return font
        if bold and Path(cjk).name.lower() == 'msyh.ttc':
            # 雅黑有独立的粗体文件；`-Bold` 变体规则找不到它（文件名不同）
            font = _truetype(str(Path(cjk).with_name('msyhbd.ttc')), want)
            if font is not None:
                return font
    # 连一个中文字体都没有（非 Windows 的 CI）：至少别抛异常。
    try:
        return ImageFont.load_default(size=want)
    except TypeError:                         # Pillow < 9.2 没有 size 参数
        return ImageFont.load_default()


#: 文本里有没有汉字（只关心"要不要挑一支能画中文的字体"）。
_CJK_RE = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]')


def _text_has_cjk(text) -> bool:
    """这段文字里有没有汉字（没有就不用操心字体缺中文）。"""
    return bool(_CJK_RE.search(str(text or '')))


@lru_cache(maxsize=16)
def _font_covers_cjk(path: str) -> bool:
    """
    这支字体到底画不画得出汉字。

    为什么需要：项目里写着 `gui.text_font = "DejaVuSans.ttf"`（Ren'Py 模板的
    默认值，SDK 里能摸到）时，字体**不算缺失**、但根本没有汉字 —— Pillow 会画
    成一个个 notdef 方块，正是用户说的"方块"。缩略图只有 288 宽，宁可换成
    系统的雅黑/黑体，也不能把中文画成方块（整帧预览/导出仍用项目字体）。
    """
    font = _truetype(path, 18)
    if font is None:
        return False
    try:
        # 缺字时 Pillow 画 .notdef；拿几乎不可能被任何字体定义的 U+FFFF 当参照。
        notdef = bytes(font.getmask('\uffff'))
        for ch in ('中', '一', '说'):
            if bytes(font.getmask(ch)) != notdef:
                return True
        return False
    except Exception as e:                    # noqa: BLE001 - 查不出来就当能画
        swallow('stage_render.glyph_probe', e)
        return True


def _pick_readable_font_path(profile, speaker: str, display: str,
                            kind: str, text: str) -> tuple:
    """
    缩略图这次该用哪支字体 → `(路径, 是否换成了系统中文字体)`。

    项目字体能画中文就用它（字形与引擎一致）；能开但画不了中文时，返回空串
    让 `_load_readable_font` 走系统中文字体 —— 字号已经够小了，再画方块就
    等于没做这次修复。
    """
    path = _font_path_for(profile, speaker, display, kind)
    if not path or not _text_has_cjk(text):
        return path, False
    if _font_covers_cjk(path):
        return path, False
    return '', True


def _readable_px(kind: str, width: int, strip_h: int) -> int:
    """缩略图上某种文字的字号（像素）：按比例算，夹在 [最小可读, 最大] 之间。"""
    low = int(MIN_READABLE_PX[kind])
    high = int(MAX_READABLE_PX[kind])
    want = int(round(float(width) * float(READABLE_PX_RATIO[kind])))
    want = max(low, min(high, want))
    if strip_h > 0:
        # 条带本身很矮时（高 DPI 下 target 可能只有几十像素高的对话区），
        # 字再大只会糊出条带外；按条带高度再封一次顶。
        want = min(want, max(low, int(round(float(strip_h) * 0.5))))
    return max(1, want)


def _scaled_rect(rect, sx: float, sy: float) -> tuple:
    """整帧坐标 → 缩略图坐标（`RenderResult` 里的矩形都按原分辨率给）。"""
    try:
        x, y, w, h = (float(v) for v in list(rect)[:4])
    except (TypeError, ValueError):
        return (0, 0, 0, 0)
    return (int(round(x * sx)), int(round(y * sy)),
            int(round(w * sx)), int(round(h * sy)))


def _clamp_rect(rect, width: int, height: int) -> tuple:
    """把矩形夹进图里（换算后可能因为四舍五入比图宽 1px）。"""
    x, y, w, h = rect
    x = max(0, min(max(0, int(width) - 1), int(x)))
    y = max(0, min(max(0, int(height) - 1), int(y)))
    w = max(0, min(int(w), int(width) - x))
    h = max(0, min(int(h), int(height) - y))
    return (x, y, w, h)


def _avg_rgb(img, rect) -> tuple:
    """条带的平均色（缩到 1×1 采样：一行代码，不用引 ImageStat）。"""
    try:
        crop = img.crop((rect[0], rect[1], rect[0] + rect[2],
                         rect[1] + rect[3]))
        if crop.width <= 0 or crop.height <= 0:
            return (0, 0, 0)
        r, g, b = crop.convert('RGB').resize(
            (1, 1), Image.Resampling.BOX).getpixel((0, 0))
        return (int(r), int(g), int(b))
    except Exception as e:                    # noqa: BLE001 - 采样失败别炸预览
        swallow('stage_render.readable_avg_color', e)
        return (0, 0, 0)


def _luma(rgb) -> float:
    """亮度（BT.601 权重，够判"深色/浅色"就行）。"""
    return 0.299 * float(rgb[0]) + 0.587 * float(rgb[1]) + \
        0.114 * float(rgb[2])


def _readable_base_rgb(img, rect, text_rgba) -> tuple:
    """
    条带底色 = 缩略图上条带的平均色；和字色对比不够时往黑/白拉开一段。

    为什么不能直接用平均色：文本框可能是浅色主题（白底黑字）。白字铺在
    白底上等于没画。这里只调**亮度**、不换色相 —— 颜色来自项目自己的
    文本框，不是编辑器主题。
    """
    base = _avg_rgb(img, rect)
    text_luma = _luma(text_rgba[:3])
    if abs(_luma(base) - text_luma) >= 96:     # 已经够对比：保留原色
        return base
    if text_luma >= 128:                       # 浅色字 → 压暗底
        return tuple(max(0, int(round(v * 0.22))) for v in base)
    return tuple(int(round(v + (255 - v) * 0.78)) for v in base)  # 深色字 → 提亮底


def _attr(obj, name: str, default=''):
    """宽厚的属性读取：profile / style 可能是假的（测试桩），别因此炸缩略图。"""
    try:
        value = getattr(obj, name, default)
        return default if value is None else value
    except Exception as e:                    # noqa: BLE001 - 属性写坏了不该拖垮画面
        swallow('stage_render.readable_attr', e)
        return default


def _cover_rect(out, rect, rgb) -> None:
    """
    把一个矩形铺成**不透明**的平均色（缩略图铺底专用）。

    为什么必须不透明：`img` 是按 LANCZOS 缩下来的整帧，原文本框里的台词会以
    "淡一层的小字鬼影"残留在这个区域里；只要底不是全不透明，它就会和新画的
    大字叠在一起（用户看到的是"字有重影"）。铺平之后这个区域只剩新字。
    """
    if rect[2] <= 0 or rect[3] <= 0:
        return
    patch = Image.new('RGBA', (int(rect[2]), int(rect[3])),
                      (int(rgb[0]), int(rgb[1]), int(rgb[2]),
                       _READABLE_BASE_ALPHA))
    try:
        out.alpha_composite(patch, (int(rect[0]), int(rect[1])))
    except Exception as e:                    # noqa: BLE001 - 铺底失败别炸预览
        swallow('stage_render.readable_base', e)


def _ink_box(draw, pos, text: str, font, stroke: int = 1) -> tuple:
    """
    这段字实际占的 `(x, y, w, h)`（含描边）—— 给测试断言"没出框"用。

    为什么不用 `draw.textlength`：那个只给宽度，而"名字冒出小标签"是高度问题。
    `textbbox` 拿的是墨迹边界（含描边）；老 Pillow 没有它就退回按字体度量估。
    """
    try:
        box = draw.textbbox((int(pos[0]), int(pos[1])), text, font=font,
                            stroke_width=int(stroke), anchor='la')
        if box and box[2] > box[0] and box[3] > box[1]:
            return (int(box[0]), int(box[1]),
                    int(box[2] - box[0]), int(box[3] - box[1]))
    except Exception as e:                    # noqa: BLE001 - 老 Pillow：按度量估
        swallow('stage_render.readable_textbbox', e)
    tw = int(round(_measure(draw, text, font)))
    ascent, descent = font.getmetrics()
    return (int(pos[0]), int(pos[1]), max(1, tw),
            max(1, int(ascent + descent)))


def _readable_stroke(px: int) -> int:
    """这个字号该不该描边（见 `_READABLE_STROKE_MIN_PX` 的说明）。"""
    return 1 if int(px) >= _READABLE_STROKE_MIN_PX else 0


def _draw_name_in_box(draw, box, text: str, font_path: str, px: int,
                      color, outline) -> tuple:
    """
    把名字画在**名条矩形里面** → `(墨迹 box, 实际字号, 是否截断)`。

    三件事一起保证"名字不会写了两遍/冒出标签"：
      · 调用方先按可读字号把名条**长到包住字**（`_fit_name_tag`），到这里时
        `box` 已经是"装得下这个名字"的尺寸；
      · 字还是太宽就**逐级缩字号**（到 9px 为止），不是硬截断 —— 名字少一半
        比小一号更糟；
      · 实在放不下（超长名字 / 名条已经顶到画面右边缘）才末尾加「…」，并且
        最后把落点夹回矩形里。
    """
    x, y, w, h = (int(v) for v in box)
    if w <= 0 or h <= 0:
        return None, 0, False
    px = max(1, int(px))
    avail = max(4, w - 4)
    font = _load_readable_font(font_path, px)
    while font is not None and px > _NAME_BOX_MIN_PX and \
            _measure(draw, text, font) > avail:
        px -= 1
        font = _load_readable_font(font_path, px)
    if font is None:
        return None, 0, False
    shown = text
    clipped = False
    if _measure(draw, shown, font) > avail:
        clipped = True
        while shown and _measure(draw, shown + '…', font) > avail:
            shown = shown[:-1]
        shown = (shown.rstrip() + '…') if shown else '…'
    ascent, descent = font.getmetrics()
    line_h = max(1, int(ascent + descent))
    text_w = int(round(_measure(draw, shown, font)))
    tx = x + max(0, (w - text_w) // 2)
    ty = y + max(0, (h - line_h) // 2)
    tx = max(x, min(x + w - text_w, tx))
    ty = max(y, min(y + h - line_h, ty))
    stroke = _readable_stroke(px)
    draw.text((tx, ty), shown, font=font, fill=color, stroke_width=stroke,
              stroke_fill=outline, anchor='la')
    return _ink_box(draw, (tx, ty), shown, font, stroke), int(px), clipped


def _fit_name_tag(draw, box, text: str, font_path: str, px: int,
                  size) -> tuple:
    """
    名条在缩略图上**长到包住可读字号的名字** → `((x, y, w, h), 字号)`。

    为什么不能直接把名字塞进引擎的名条框：那张框是 1920×1080 上的尺寸，缩到
    576 的 target 后常常只剩 77×24（真机 sr_general），可读字号 24px 放不进去，
    名字就只能被压到 14px —— 画到 288 的控件上只有 7px，汉字照样糊成方块。
    小图是"缩略图专用重排"，所以这里**保留名条的位置/颜色，让底跟着字长**：

      · 从左/顶**向右/下**长到 `字宽 + 左右 padding` / `行高 + 上下 padding`；
      · 宽高只增不减（返回的矩形**一定**盖住原 `name_box`，铺底才盖得干净）；
      · 一路长到画面右边缘（或底边）还放不下，才逐级缩字号，下限
        `_NAME_BOX_MIN_PX` —— 名条和名字都不许出画面。

    到下限仍放不下时返回"顶到画面边缘"的矩形，由 `_draw_name_in_box` 负责
    加「…」截断（超长名字才可能走到这里）。
    """
    x, y, w, h = (int(v) for v in box)
    avail_w = max(1, int(size[0]) - x)
    avail_h = max(1, int(size[1]) - y)
    px = max(_NAME_BOX_MIN_PX, int(px))
    while True:
        font = _load_readable_font(font_path, px)
        if font is None:                       # 字体加载失败：按原框画（会截断）
            return (x, y, w, h), px
        text_w = int(math.ceil(_measure(draw, text, font)))
        ascent, descent = font.getmetrics()
        line_h = max(1, int(ascent + descent))
        pad_x = max(2, int(round(float(px) * _NAME_TAG_PAD_X_RATIO)))
        pad_y = max(1, int(round(float(px) * _NAME_TAG_PAD_Y_RATIO)))
        want_w = max(w, text_w + 2 * pad_x)
        want_h = max(h, line_h + 2 * pad_y)
        tag_w = min(avail_w, want_w)
        tag_h = min(avail_h, want_h)
        if tag_w >= want_w and tag_h >= want_h:
            return (x, y, tag_w, tag_h), px
        if px <= _NAME_BOX_MIN_PX:             # 到底线了：顶边+截断
            return (x, y, tag_w, tag_h), px
        px -= 1


def _draw_name_in_strip(draw, strip, pad: int, right: int, bottom: int,
                        text: str, font_path: str, px: int,
                        color, outline) -> tuple:
    """
    没有可用名条矩形时的退路：名字画在**条带内第一行**（可借满整条宽度）。

    这是第一版的行为，只在"老项目 / 项目没写名条矩形 / 名条矩形退化"时才走；
    正常有 `name_rect` 的项目一律由 `_fit_name_tag` + `_draw_name_in_box`
    画进（长到包住字的）小标签里。
    """
    font = _load_readable_font(font_path, px)
    if font is None:
        return None
    ascent, descent = font.getmetrics()
    line_h = max(1, int(ascent + descent))
    x = strip[0] + pad
    y = max(strip[1] + pad, min(bottom - line_h, strip[1] + pad))
    shown = text
    max_w = max(8, right - x)
    if _measure(draw, shown, font) > max_w:
        while shown and _measure(draw, shown + '…', font) > max_w:
            shown = shown[:-1]
        shown = (shown.rstrip() + '…') if shown else '…'
    stroke = _readable_stroke(px)
    draw.text((x, y), shown, font=font, fill=color, stroke_width=stroke,
              stroke_fill=outline, anchor='la')
    return _ink_box(draw, (x, y), shown, font, stroke)


def _font_path_for(profile, speaker: str, display: str, kind: str) -> str:
    """这一句/这个名条实际用哪支字体（与 `_draw_say` 同一个口径）；拿不到就空串。"""
    try:
        font_for = getattr(profile, 'font_for', None)
        if callable(font_for):
            got = font_for(speaker, display, kind)
            if isinstance(got, (tuple, list)) and got:
                return str(got[0] or '')
            if isinstance(got, str):
                return got
    except Exception as e:                    # noqa: BLE001
        swallow('stage_render.readable_font_for', e)
    return ''


def _readable_payload(frame, profile):
    """
    这一拍要重画的 `(名字, 台词, 角色样式)`。

    前后缀 / 谁在说话的口径**照抄 `_draw_say`**：缩略图可以小，但不能
    "卡片上是这个名字、缩略图上写另一个"。
    """
    speaker = str(_attr(frame, 'speaker', '') or '')
    display = str(_attr(frame, 'display_speaker', '') or '')
    style = None
    try:
        character_for = getattr(profile, 'character_for', None)
        if callable(character_for):
            style = character_for(speaker, display)
    except Exception as e:                    # noqa: BLE001
        swallow('stage_render.readable_character', e)
    who = str(_attr(frame, 'display_speaker', '') or '')
    if who:
        who = (_attr(style, 'who_prefix', '') or '') + who + \
            (_attr(style, 'who_suffix', '') or '')
    text = str(_attr(frame, 'text', '') or '')
    if style is not None:
        prefix = str(_attr(style, 'what_prefix', '') or '')
        suffix = str(_attr(style, 'what_suffix', '') or '')
        if prefix and not text.startswith(prefix):
            text = prefix + text
        if suffix and not text.endswith(suffix):
            text = text + suffix
    return who, text, style


def _readable_outline(profile, kind: str) -> tuple:
    """描边颜色：项目自己的 `*_text_outlines` 优先；没有就用黑色（模板默认）。"""
    try:
        attr = 'name_text_outlines' if kind == 'name' else \
            'dialogue_text_outlines'
        outlines = getattr(profile, attr, None)
        if outlines:
            first = outlines[0]
            value = first[1] if len(first) > 1 else ''
            if isinstance(value, str):
                return _color(value)
    except Exception as e:                    # noqa: BLE001
        swallow('stage_render.readable_outline', e)
    return (0, 0, 0, 255)


def _hex_color(rgba) -> str:
    """RGBA → `#rrggbb`（`_draw_lines` 的 outline 只认字符串颜色）。"""
    return '#%02x%02x%02x' % (int(rgba[0]) & 255, int(rgba[1]) & 255,
                              int(rgba[2]) & 255)


class _RectSink:
    """
    接住 `_draw_lines` 会写回的 `text_rect`。

    缩略图重画只是"在缩小后的图上补一层字"，不改变整帧的排版结果
    （`RenderResult.text_rect` 描述的是 1920×1080 那一版），所以给它一个
    一次性对象接住，而不是把原结果改脏。
    """

    __slots__ = ('text_rect',)

    def __init__(self):
        self.text_rect = (0, 0, 0, 0)


def draw_readable_say(img, result, frame, profile=None):
    """
    在**已经缩到缩略图尺寸**的舞台图上，按可读字号把名字 / 台词重画一遍。

    参数：
      · `img`：缩小后的 RGBA 图。**不改它**，返回一张新的（没动手时返回原对象）；
      · `result`：整帧的 `RenderResult` —— 用 `textbox_rect` / `name_rect` /
        `text_rect` 按 `img.width / result.width` 换算位置；
      · `frame`：`StageFrame`（名字、台词、谁在说话）；
      · `profile`：`GuiProfile`（颜色 / 字体 / 前后缀）。传 None 也能画
        （默认白字 + 系统中文字体），只是不知道项目自己的排版。

    返回 `(img, info)`。`info` 是这次实际用了多少号字（`name_px` /
    `dialogue_px`）、几行（`name_lines` / `dialogue_lines`）、有没有截断
    （`ellipsis`）、有没有因为项目字体画不了汉字而换成系统中文字体
    （`font_fallback`），测试直接断言它，不必从像素反推。

    2026-10-29 第二版（真机上验出来的两个毛病）：铺底改成**条带 + 名条各自
    平均色、不透明**盖平 —— 原文字一个像素都不透；名条在文本框上方时台词从
    条带顶部起、不再被名字往下挤。

    2026-10-29 第三版：**名条的底跟着字长**。第二版把名字塞进引擎名条框
    （`name_rect` 缩到 576 target 只剩 77×24），"「凌跃捷」"被压到 14px ——
    真机 sr_general 上等于没修。现在先用 `_readable_px('name', …)` 定字号，
    再由 `_fit_name_tag` 把名条的底从左/顶向右/下长到包住字；只有长到画面
    边缘还放不下才逐级缩字号（下限 9px），绝不画出画面。位置/颜色/前后缀
    仍照引擎，这只是一张"缩略图专用重排"的小图。

    `info` 里供测试/调试用的键：

      · `strip` / `name_box`：本次重画的两个矩形（图坐标；名条无效时为 0；
        `name_box` 是引擎名条框换算到图上的位置，没长过）；
      · `name_tag`：**实际铺底并画字**的名条矩形 —— 从 `name_box` 的左/顶
        向右/下长到包住名字后的尺寸（名条无效时为 0）；
      · `strip_base` / `name_base`：铺底用的平均色（名条无效时 `name_base`
        为 None）；
      · `covered`：**所有**被不透明铺平的矩形（条带在前、名条 `name_tag`
        在后）；
      · `name_text_box` / `dialogue_text_box`：新名字 / 新台词的**实际墨迹**
        范围（含描边，`(x, y, w, h)`）—— 判"字没画到框外 / 没有旧字残留"。

    以下情况**原样返回、逐像素不变**：没有 say 窗口（`shows_window=False`）、
    文本框矩形无效、没有名字也没有台词。
    """
    if img is None or result is None or frame is None or not _PIL_OK:
        return img, {'drawn': False, 'reason': '没有可画的图 / 帧'}
    if not getattr(frame, 'shows_window', False):
        return img, {'drawn': False, 'reason': '这一拍没有 say 窗口'}
    src_w = int(getattr(result, 'width', 0) or 0)
    src_h = int(getattr(result, 'height', 0) or 0)
    if src_w <= 0 or src_h <= 0:
        return img, {'drawn': False, 'reason': 'RenderResult 没有分辨率'}
    sx = float(img.width) / float(src_w)
    sy = float(img.height) / float(src_h)
    strip = _clamp_rect(
        _scaled_rect(getattr(result, 'textbox_rect', (0, 0, 0, 0)), sx, sy),
        img.width, img.height)
    if strip[2] < 8 or strip[3] < 8:
        return img, {'drawn': False, 'reason': '没有可用的文本框矩形'}

    who_text, dlg_text, style = _readable_payload(frame, profile)
    if not who_text and not dlg_text:
        return img, {'drawn': False, 'reason': '这一拍没有名字 / 台词'}

    speaker = str(_attr(frame, 'speaker', '') or '')
    display = str(_attr(frame, 'display_speaker', '') or '')
    dlg_color = _color(_attr(style, 'what_color', '') or
                       _attr(profile, 'text_color', '') or '#ffffff')
    name_color = _color(_attr(style, 'who_color', '') or
                        _attr(profile, 'name_text_color', '') or '#ffffff')

    # 名条矩形：`name_rect` 可能整个贴在本框**上方**（`gui.name_ypos = -58`），
    # 所以铺底不能只铺条带 —— 不盖名条，原名字会和重画的名字叠成两个。
    name_raw = _scaled_rect(getattr(result, 'name_rect', (0, 0, 0, 0)), sx, sy)
    name_box = _clamp_rect(name_raw, img.width, img.height)
    name_ok = bool(who_text) and name_box[2] >= 8 and name_box[3] >= 8 \
        and name_raw[0] < img.width and name_raw[1] < img.height

    out = img.convert('RGBA').copy()
    draw = ImageDraw.Draw(out)
    # 名字先按**可读字号**算，再让名条的底长到包住它（第三版，见
    # `_fit_name_tag`）：引擎的名条框只是"位置 / 颜色"的参照，不是字号的笼子
    # —— 真机上 77px 宽的小标签会把「凌跃捷」压到 14px，画到 288 控件上只剩
    # 7px，等于没修。
    name_px = _readable_px('name', out.width, strip[3])
    name_path, name_fell_back = '', False
    if who_text:
        name_path, name_fell_back = _pick_readable_font_path(
            profile, speaker, display, 'who', who_text)
    name_tag = (0, 0, 0, 0)
    if name_ok:
        name_tag, name_px = _fit_name_tag(
            draw, name_box, who_text, name_path, name_px, out.size)
    # ① 铺底：条带 / 名条各自用**自己区域的平均色**、**不透明**盖平。
    #    平均色必须在画任何东西之前采（要反映项目原本的底色）；名条后盖，
    #    这样名条与文本框重叠的那条边仍是"有颜色的小标签"，不会被条带吃掉。
    #    名条的底按**加宽/加高后**的 `name_tag` 铺（它一定盖住原 `name_box`），
    #    否则长出来的那半截没有底色，字会压在原画面上。
    strip_base = _readable_base_rgb(out, strip, dlg_color)
    name_base = _readable_base_rgb(
        out, name_box, name_color) if name_ok else None
    _cover_rect(out, strip, strip_base)
    if name_base is not None:
        _cover_rect(out, name_tag, name_base)
    pad = max(2, int(round(float(strip[3]) * 0.05)))
    right = strip[0] + strip[2] - pad
    bottom = strip[1] + strip[3] - pad
    info = {'drawn': True, 'name_px': 0, 'dialogue_px': 0,
            'name_lines': 0, 'dialogue_lines': 0, 'ellipsis': False,
            'font_fallback': bool(name_fell_back), 'strip': tuple(strip),
            'strip_base': tuple(strip_base), 'name_base': name_base,
            'name_box': tuple(name_box) if name_ok else (0, 0, 0, 0),
            'name_tag': tuple(name_tag) if name_ok else (0, 0, 0, 0),
            'name_text_box': (0, 0, 0, 0),
            'dialogue_text_box': (0, 0, 0, 0),
            'covered': [tuple(strip)] + ([tuple(name_tag)] if name_ok else [])}

    # ② 名字
    #    · 有名条矩形：画进**长到包住字**的名条 `name_tag` 里（可读字号）；
    #      名条在条带里时台词接在加长后的名条底下，名条在条带**上方**时
    #      不推台词 —— 那样会把整块台词往下挤；
    #    · 没有名条矩形（老项目 / 没名条图 / 字体加载失败）：退回"条带内
    #      第一行"的画法。
    name_bottom = strip[1] + pad
    if who_text:
        name_outline = _readable_outline(profile, 'name')
        ink, used_px, clipped = None, int(name_px), False
        if name_ok:
            ink, used_px, clipped = _draw_name_in_box(
                draw, name_tag, who_text, name_path, name_px, name_color,
                name_outline)
            if ink is not None and name_tag[1] >= strip[1]:
                # 名条落在条带里（模板的写法）：台词接在名条下面。
                # 名条在条带**上方**时不推台词 —— 那样会把整块台词往下挤。
                name_bottom = max(name_bottom,
                                  name_tag[1] + name_tag[3] + max(1, pad // 2))
        if ink is None:                        # 没有名条矩形 / 字体加载失败
            used_px = int(name_px)
            ink = _draw_name_in_strip(
                draw, strip, pad, right, bottom, who_text, name_path,
                used_px, name_color, name_outline)
            if ink is not None:
                name_bottom = max(name_bottom,
                                  ink[1] + ink[3] + max(1, pad // 2))
        if ink is not None:
            info['name_px'] = int(used_px)
            info['name_lines'] = 1
            info['name_text_box'] = tuple(ink)
            info['ellipsis'] = bool(info['ellipsis'] or clipped)

    # ③ 台词：在条带内按可读字号重排（中文逐字断行），放不下就末尾加 …
    if dlg_text:
        dlg_px = _readable_px('dialogue', out.width, strip[3])
        dlg_rect = _clamp_rect(
            _scaled_rect(getattr(result, 'text_rect', (0, 0, 0, 0)), sx, sy),
            out.width, out.height)
        dlg_x = strip[0] + pad
        if dlg_rect[2] > 0:
            # 起点照引擎的 dialogue_xpos（保留它给立绘留的左边距）；
            # 换行宽度**用满文本框条带**（而不是引擎的 dialogue_width）：
            # 288 宽的小图上 dialogue_width 往往只剩不到 170px，一行十来个字
            # 就没了；既然这段文字本来就是"缩略图专用重排"，宽度就按条带给。
            dlg_x = max(strip[0] + pad, min(right - 16, dlg_rect[0]))
        dlg_w = max(16, right - dlg_x)
        dlg_y = max(strip[1] + pad, name_bottom)
        if dlg_rect[3] > 0 and dlg_rect[1] >= name_bottom - 2:
            # 引擎排版里台词起点就在名条下面时照它来（保持"名上句下"）
            dlg_y = max(dlg_y, dlg_rect[1])
        engine_px = float(_attr(profile, 'dialogue_text_size', 0.0) or 0.0) \
            or 33.0
        dlg_align = 0.0
        try:
            dlg_align = float(
                (getattr(profile, 'values', {}) or {}).get(
                    'dialogue_text_xalign') or 0)
        except (TypeError, ValueError, AttributeError) as e:
            swallow('stage_render.readable_align', e)
        # `{size=…}` 这类标签里的字号是**引擎尺度**的，按 target 的缩放比
        # 一起缩下来，否则一行里会冒出一个 45px 的巨字把条带撑爆。
        runs = text_runs(dlg_text, float(dlg_px), dlg_color)
        ratio = float(dlg_px) / max(1.0, engine_px)
        for _text, run_style in runs:
            if abs(float(run_style.size) - float(dlg_px)) > 0.01:
                run_style.size = max(
                    6.0, min(float(MAX_READABLE_PX['dialogue']) * 1.5,
                             float(run_style.size) * ratio))
        dlg_font_path, dlg_fell_back = _pick_readable_font_path(
            profile, speaker, display, 'what', dlg_text)
        info['font_fallback'] = bool(info['font_fallback'] or dlg_fell_back)
        font_cache = {}

        def _run_font(run_style):
            key = (int(round(run_style.size)), bool(run_style.bold),
                   bool(run_style.italic))
            if key not in font_cache:
                font_cache[key] = _load_readable_font(
                    dlg_font_path, key[0], key[1], key[2])
            return font_cache[key]

        base_font = _load_readable_font(dlg_font_path, int(dlg_px))
        if runs and base_font is not None:
            ascent, descent = base_font.getmetrics()
            line_h = max(1, int(ascent + descent))
            avail = max(1, bottom - dlg_y)
            max_lines = max(1, int(avail // line_h))
            lines = _wrap(_units(runs, _run_font), float(dlg_w), draw)
            clipped = False
            if len(lines) > max_lines:
                clipped = True
                lines = lines[:max_lines]
                last = lines[-1]
                last_font = last[-1][1] if last else base_font
                last_style = last[-1][2] if last else _TextStyle(
                    color=dlg_color, size=float(dlg_px))
                ell = _measure(draw, '…', last_font)
                while last and sum(_measure(draw, t, f)
                                   for t, f, _s in last) + ell > dlg_w:
                    last.pop()
                if ell <= dlg_w:
                    last.append(('…', last_font, last_style))
            if any(line for line in lines):
                sink = _RectSink()
                stroke_w = _readable_stroke(dlg_px)
                outline = ([(stroke_w,
                             _hex_color(_readable_outline(profile,
                                                          'dialogue')),
                             0, 0)] if stroke_w else [])
                _draw_lines(
                    out, lines, float(dlg_x), float(dlg_y), float(dlg_w),
                    dlg_align, 0.0, outline, sink, float(dlg_px))
                info['dialogue_px'] = int(dlg_px)
                info['dialogue_lines'] = len(lines)
                # 别把名字那边的「…」盖掉：名字与台词共用这一个标志位。
                info['ellipsis'] = bool(info['ellipsis'] or clipped)
                # 新字实际占的排版框（含行高）—— 测试拿它判"条带里除了新字
                # 没有别的像素被改动"，也判"原文字已被铺底盖掉"。
                info['dialogue_text_box'] = tuple(sink.text_rect)
    return out, info


def scale_result_image(result, target):
    """
    `RenderResult` → 缩到 `target` 的 RGBA 图（PIL 侧）。

    为什么缩放在这里而不是 Qt 侧：compact 缩略图要**先缩放、再重画** ——
    可读字号必须按最终尺寸算（见 `draw_readable_say`）。整宽模式仍走
    `stage_preview.result_to_qimage` 那条老路，行为不变。
    """
    img = getattr(result, 'image', None) if result is not None else None
    if img is None or not _PIL_OK:
        return None
    try:
        img = img.convert('RGBA')
        tw, th = int(target[0]), int(target[1])
        if tw > 0 and th > 0 and img.size != (tw, th):
            img = img.resize((tw, th), Image.Resampling.LANCZOS)
        return img
    except Exception as e:                    # noqa: BLE001
        swallow('stage_render.scale_result_image', e)
        return None


# ----------------------------------------------------------------------
#  入口
# ----------------------------------------------------------------------

def render_frame(frame, project_root=None, registry=None, profile=None,
                 screen=None, transform_index=None,
                 image_index=None, layer_model=None) -> Optional[RenderResult]:
    """
    一帧 → `RenderResult`（PIL RGBA）。Pillow 没装 / 帧为空返回 None。

    `profile` 不传就按项目读（`load_gui_profile`）；`screen` 不传就用项目
    分辨率（`gui.init`）。所有素材解析复用 `app.stage.resolve_*`，
    与「舞台快照」、生成器同源。
    """
    if not _PIL_OK or frame is None:
        return None
    from storygraph.app.stage import image_index as make_index
    from storygraph.story.renpy_gui import load_gui_profile

    profile = profile or load_gui_profile(project_root, registry=registry)
    ctx_screen = tuple(screen or getattr(profile, 'screen', None)
                       or (1920, 1080))
    ctx_screen = (max(1, int(ctx_screen[0])), max(1, int(ctx_screen[1])))
    if transform_index is None and project_root:
        try:
            from storygraph.story.atl import scan_transforms
            transform_index = scan_transforms(project_root)
        except Exception as e:                 # noqa: BLE001
            swallow('stage_render.transforms', e)
            transform_index = {}
    ctx = _Ctx(profile=profile, registry=registry,
               project_root=project_root, screen=ctx_screen,
               transform_index=transform_index or {},
               image_index=image_index if image_index is not None
               else make_index(project_root),
               layer_model=layer_model)
    canvas = Image.new('RGBA', ctx_screen, (0, 0, 0, 255))
    result = RenderResult(width=ctx_screen[0], height=ctx_screen[1])
    try:
        _draw_background(canvas, frame, ctx, result)
        _draw_sprites(canvas, frame, ctx, result)
        _draw_layers(canvas, frame, ctx, result)
        _draw_say(canvas, frame, ctx, result)
    except Exception as e:                     # noqa: BLE001 - 预览绝不崩
        swallow('stage_render.render_frame', e)
        result.warnings.append(f'渲染异常：{e.__class__.__name__}')
    result.image = canvas
    result.warnings = list(dict.fromkeys(
        [*result.warnings, *getattr(profile, 'warnings', ())]))
    # 缺图名单要去重：同一张图在一拍里出现两次不该写两遍（横条会变长）
    result.missing = list(dict.fromkeys(result.missing))
    return result


def render_frame_to_png(frame, out_path, **kwargs) -> Optional[str]:
    """渲染并写 PNG（调试/对照用）；失败返回 None。"""
    got = render_frame(frame, **kwargs)
    if got is None or got.image is None:
        return None
    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        got.image.save(str(out_path))
        return str(out_path)
    except Exception as e:                     # noqa: BLE001
        swallow('stage_render.save', e)
        return None


__all__ = ['DEFAULT_CACHE_BYTES', 'FX_FALLBACK_SECONDS', 'MAX_READABLE_PX',
           'MIN_READABLE_PX', 'READABLE_PX_RATIO', 'RenderResult', 'available',
           'cache_clear', 'cache_info', 'configure_cache', 'draw_readable_say',
           'render_frame', 'render_frame_to_png', 'scale_notes',
           'scale_result_image', 'text_runs', 'transition_notes']
