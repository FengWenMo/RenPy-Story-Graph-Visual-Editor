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

import re
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

from storygraph.story.atl import placement_of


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

@lru_cache(maxsize=48)
def _load_scaled(path: str, mtime: int, w: int, h: int):
    """按目标像素尺寸解码（大立绘 4320×7680 不会整张进内存缓存）。"""
    if not _PIL_OK:
        return None
    try:
        with Image.open(path) as src:
            img = src.convert('RGBA')
            if (w, h) != img.size:
                img = img.resize((max(1, int(w)), max(1, int(h))),
                                 Image.Resampling.LANCZOS)
            return img.copy()
    except Exception as e:                    # noqa: BLE001 - 坏图不炸预览
        swallow('stage_render.load_scaled', e)
        return None


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


def _box_for(spec, at, ctx, natural):
    if not at:
        return 0.0, 0.0, float(natural[0]), float(natural[1])
    place = placement_of(at, ctx.transform_index, ctx.registry, ctx.screen)
    return place.box(natural, ctx.screen)


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
            try:
                left, top, w, h = _box_for(scene, at, ctx, natural)
            except Exception as e:            # noqa: BLE001
                swallow('stage_render.background_box', e)
                left, top, w, h = 0, 0, natural[0], natural[1]
            img = _load_scaled(path, _mtime(path), int(w), int(h))
            if img is not None:
                _paste(canvas, img, left, top)
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
            left, top, w, h = place.box(natural, ctx.screen)
        except Exception as e:                # noqa: BLE001
            swallow('stage_render.sprite_box', e)
            left, top, w, h = 0, 0, natural[0], natural[1]
        # 画面外的立绘不 decode：省内存也省时间
        if left >= ctx.screen[0] or top >= ctx.screen[1] or \
                left + w <= 0 or top + h <= 0:
            result.warnings.append(f'立绘在画面外：{spec}')
            continue
        img = _load_scaled(path, _mtime(path), int(w), int(h))
        if img is None:
            result.warnings.append(f'立绘解码失败：{spec}')
            continue
        angle = float(getattr(place, 'rotate', 0.0) or 0.0)
        if angle:
            img = img.rotate(-angle, expand=True,
                             resample=Image.Resampling.BICUBIC)
            left -= (img.width - w) / 2.0
            top -= (img.height - h) / 2.0
        _paste(canvas, img, left, top,
               float(getattr(place, 'alpha', 1.0) or 1.0))
        result.sprite_boxes[i] = (left, top, w, h)


def _draw_layers(canvas, frame, ctx, result):
    raw = str(getattr(frame, 'layers', '') or '')
    if not raw:
        return
    for spec in [p.strip() for p in raw.split(';') if p.strip()]:
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
                img = _load_scaled(path, _mtime(path), natural[0], natural[1])
                _paste(canvas, img, 0, 0)
                continue
        if color:
            canvas.alpha_composite(Image.new('RGBA', canvas.size,
                                             _color(color)))
            continue
        result.warnings.append(f'图层找不到：{spec}')
        result.missing.append(spec)


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


__all__ = ['RenderResult', 'available', 'render_frame',
           'render_frame_to_png', 'text_runs']
