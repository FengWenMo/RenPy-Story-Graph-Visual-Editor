"""
`storygraph.app.transition_fx`
=============================
把 `Effect`（项目 `define` 的转场描述）演成**若干中间帧** —— 纯函数、纯内存、
**不 import Qt**，供卡片缩略图与演出预览（第二波接入）使用。

背景（2026-10-30 用户第 3 条反馈，续）
--------------------------------------
`story.transition_catalog` 已经能回答"这个过场是 kind / 多长 / 什么颜色 /
哪张遮罩 / 有哪些子段"，但预览层还需要"**每一帧画什么**"。这一层就是那颗
渲染引擎：输入前一张画面 `before`、后一张画面 `after`、`Effect`、目标尺寸和
帧数，输出一串 RGBA 中间帧。它不认识项目、不查资源表、不碰 Qt，所以测试里
可以用 8×8 的小图直接断言像素。

契约
----
- **输入画面**：`PIL.Image.Image`（任意模式，内部转 RGBA）；也接受 numpy 数组
  （`H×W×3/4`，Pillow 在场时走 `Image.fromarray`）；`None` 表示"那里还没有
  画面"（画成全透明）。`before` / `after` 尺寸不同 → 统一缩放到目标尺寸
  （`size` 显式给了就用它，否则取 `before` 的尺寸、再否则取 `after` 的）。
- **输出**：`FxSequence.frames` 是 `tuple[PIL.Image.Image, ...]`，**全是 RGBA、
  统一 `size`**；需要重复的帧（暂停/纯色）各自是独立副本，调用方可以直接上屏。
- **帧数语义**：`count=N` → 进度采样点 `(i + 0.5) / N`（帧中心 0.1/0.3/0.5/
  0.7/0.9…）。**不画首尾**：首帧就是 `before`、尾帧就是 `after`，调用方手上
  已有；`N=1` 取正中间（半程画面），`N=0` 给空帧（不是错误）。
- **可选参数**：`ramp`（ImageDissolve 的遮罩图：`PIL.Image` / 路径 / `None`）、
  `project_root`（遮罩图相对路径按 `<root>/game/…`、`<root>/…` 找）。

各 kind 的演法
--------------
| kind | 帧 |
|---|---|
| `dissolve` | `before`/`after` 线性交叉：`Image.blend(before, after, t)` |
| `fade` | 按 `out_time` / `hold_time` / `in_time`（缺省 → `duration` 均分；都未知 → 0.5/0/0.5 按进度）：先淡到 `color`、停、再从 `color` 淡到 `after` |
| `pixellate` | 前半段把 `before` 块化、块越变越大；后半段从最大块"解块化"回 `after`（先缩到 1/k 再放大，k 随进度增大再恢复） |
| `imagedissolve` | 遮罩图灰度当阈值：灰度 ≤ 当前阈值的像素先切到 `after`；有 `ramplen` 就量化成有限档 |
| `multiple` | 按 `segments` 分配帧数（按时长比例；时长都为 0 → 均分），逐段渲染后拼接；`FxSequence.segments` 记每段占的帧区间 |
| `pause` | 单独用 → 画面不变（保持 `before`）；在 `multiple` 里 → 纯色（该段颜色，认不出就用黑：Ren'Py 的 Pause 通常作用在 `Solid` 状态上） |
| `solid` | 纯色帧 |
| `unknown` | **不给帧**（`degraded=True`），只回 `label` / `note`：调用方只能显示文字，**绝不套默认淡入** |

诚实纪律
--------
- `ImageDissolve` 找不到遮罩图 → **退化**成普通 `dissolve`，但 `degraded=True`
  且 `note` 写明"找不到遮罩图 …，已退化为普通溶解"——降级可以，装没降不行。
- `unknown`、认不出的 `fade` 颜色、组合转场里认不出的子段 → 同样 `degraded`。
- 环境没装 Pillow → `available()` 为假，所有渲染返回空帧 + `degraded`，**不
  import 崩**（与 `stage_render` 同款约定）。
- **纯函数**：不写文件、不主动读项目（除非调用方既给遮罩图相对路径又给
  `project_root` 让它找）、不改 `Effect`。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.story.transition_catalog import (Effect, describe_effect,
                                                 effect_of)

try:                                    # Pillow 是可选依赖（同 stage_render）
    from PIL import Image, ImageColor
    _PIL_OK = True
except Exception as e:                  # noqa: BLE001 - 没装就退空帧
    swallow('app.transition_fx.import_pillow', e)
    Image = ImageColor = None
    _PIL_OK = False

#: 默认帧数（预览一眨眼的中间帧数量；奇数便于取到正中间 0.5 的帧）
DEFAULT_FRAMES = 5
#: 认不出颜色时的兜底色（Ren'Py `Fade` 的默认色就是黑）
DEFAULT_COLOR = '#000000'
#: 像素化的最大块边长上限（再大整屏只剩几个色块，看不出"过程"了）
PIXELLATE_MAX_BLOCK = 64
#: `None` 画面当成"全透明"（还没画东西），不是黑幕
EMPTY_FILL = (0, 0, 0, 0)

#: 各种 kind 对应的说明（`label` 缺失时兜底）
_KIND_LABELS = {
    'dissolve': '溶解',
    'fade': '闪屏',
    'pixellate': '像素化',
    'imagedissolve': '图像溶解',
    'multiple': '组合转场',
    'pause': '停顿',
    'solid': '纯色',
    'unknown': '无法精确预览的转场',
}


@dataclass(frozen=True)
class FxSequence:
    """一次过场要播的帧（纯内存；`frames` 全是 RGBA、统一 `size`）。"""

    frames: tuple = ()        # tuple[PIL.Image.Image, ...]
    degraded: bool = False    # True = 没按原效果演（缺遮罩图 / 认不出 / 没 Pillow）
    note: str = ''            # 给用户看的一句话（降级原因；正常为空）
    label: str = ''           # 这个过场的人话名（来自 Effect.label）
    kind: str = ''            # Effect.kind（unknown 时调用方只显示 label）
    size: tuple = ()          # (width, height)；空帧时可能是 ()
    segments: tuple = ()      # multiple 专用：((start, stop, 段 label), ...)

    def __len__(self) -> int:
        return len(self.frames)

    @property
    def ok(self) -> bool:
        """能不能真的播（有帧且没降级）。"""
        return bool(self.frames) and not self.degraded

    def to_dict(self) -> dict:
        """可 JSON 化（探针/报告用；帧本身不序列化，只留数量与尺寸）。"""
        return {
            'frames': len(self.frames),
            'degraded': self.degraded,
            'note': self.note,
            'label': self.label,
            'kind': self.kind,
            'size': list(self.size),
            'segments': [list(seg) for seg in self.segments],
        }


def available() -> bool:
    """这个环境能不能离屏演过场（Pillow 装没装）。"""
    return bool(_PIL_OK)


def frame_times(count=DEFAULT_FRAMES) -> tuple:
    """
    `count` 帧的进度采样点 `(i + 0.5) / count`（帧中心，永不取 0/1）。

    `count <= 0` → `()`；`count = 1` → `(0.5,)`（正中间那帧）。
    """
    try:
        total = int(count)
    except (TypeError, ValueError):
        total = DEFAULT_FRAMES
    if total <= 0:
        return ()
    return tuple((i + 0.5) / total for i in range(total))


# ---------------------------------------------------------------------------
#  输入画面 / 尺寸 / 颜色
# ---------------------------------------------------------------------------

def _coerce_image(img):
    """输入画面 → RGBA `Image`；`None` / 认不出 → `None`（调用方画空幕）。"""
    if img is None:
        return None
    if isinstance(img, Image.Image):
        return img if img.mode == 'RGBA' else img.convert('RGBA')
    # numpy 数组（不顶层 import numpy：它是可选依赖，本仓库只保证 Pillow）
    if hasattr(img, 'shape') and hasattr(img, 'dtype'):
        try:
            import numpy as np                 # noqa: F401 - 只为 fromarray 铺路
            made = Image.fromarray(np.asarray(img))
            return made if made.mode == 'RGBA' else made.convert('RGBA')
        except Exception as e:                 # noqa: BLE001 - 坏数组不崩预览
            swallow('app.transition_fx.coerce_numpy', e)
            return None
    return None


def _resolve_size(size, before, after) -> tuple:
    """目标尺寸：显式 `size` 优先，其次 `before`，再次 `after`；都没有 → (0, 0)。"""
    width = height = 0
    if size:
        try:
            width, height = int(size[0]), int(size[1])
        except (TypeError, ValueError, IndexError):
            width = height = 0
    if width > 0 and height > 0:
        return width, height
    for img in (before, after):
        if img is not None and img.width > 0 and img.height > 0:
            return img.size
    return 0, 0


def _fit(img, size):
    """统一到目标尺寸（`None` → 全透明空幕）。"""
    if img is None:
        return Image.new('RGBA', size, EMPTY_FILL)
    if img.size == size:
        return img
    return img.resize(size, Image.Resampling.LANCZOS)


def _parse_color(color):
    """颜色字符串 → RGBA 元组；认不出 → `None`（**不猜**）。"""
    text = str(color or '').strip()
    if not text:
        return None
    try:
        rgb = ImageColor.getrgb(text)
    except (ValueError, AttributeError, TypeError) as e:
        swallow('app.transition_fx.parse_color', e)
        return None
    if len(rgb) == 4:
        return tuple(rgb)
    return tuple(rgb) + (255,)


def _solid(size, color, notes: list):
    """
    纯色帧。颜色认不出 → 退回黑并记一条 `notes`（返回 `(颜色, 是否降级)`）。
    """
    rgba = _parse_color(color)
    if rgba is None:
        if str(color or '').strip():
            notes.append(f'认不出颜色 {color}，按黑色渲染')
        return Image.new('RGBA', size, _parse_color(DEFAULT_COLOR)), True
    return Image.new('RGBA', size, rgba), False


def _hold(img, count: int) -> list:
    """同一张画面重复 `count` 帧（各自独立副本，谁改都不串）。"""
    return [img.copy() for _ in range(max(0, int(count)))]


def _join_notes(notes) -> str:
    """多条降级说明合成一句（去重、保序、分号分隔）。"""
    seen = []
    for note in notes:
        text = str(note or '').strip()
        if text and text not in seen:
            seen.append(text)
    return '；'.join(seen)


# ---------------------------------------------------------------------------
#  各 kind 的帧
# ---------------------------------------------------------------------------

def _dissolve_frames(before, after, size, count):
    """线性 alpha 交叉：t=0 全新帧、t=1 旧帧之间的等距采样。"""
    return [Image.blend(before, after, t) for t in frame_times(count)]


def _fade_phases(effect) -> tuple:
    """
    `fade` 的三段时长 → `(出, 停, 入, 是否按秒)`。

    优先用 `Effect.out_time / hold_time / in_time`（catalog 从 `Fade(…)` 的
    参数带出来的）；没有（老数据 / 裸 `duration`）→ `duration` 均分；连
    `duration` 都没有 → 按进度 0.5 / 0 / 0.5（`是否按秒=False`）。
    """
    def _seconds(value) -> float:
        try:
            return max(0.0, float(value or 0.0))
        except (TypeError, ValueError):
            return 0.0

    out = _seconds(getattr(effect, 'out_time', 0.0))
    hold = _seconds(getattr(effect, 'hold_time', 0.0))
    into = _seconds(getattr(effect, 'in_time', 0.0))
    if out > 0 or into > 0:
        return out, hold, (into if into > 0 else out), True
    total = _seconds(getattr(effect, 'duration', 0.0))
    if total > 0:
        return total / 2.0, 0.0, total / 2.0, True
    return 0.5, 0.0, 0.5, False


def _fade_frames(effect, before, after, size, count):
    """先淡到 `color`、停 `hold`、再从 `color` 淡到 `after`。"""
    notes: list = []
    color = str(getattr(effect, 'color', '') or '') or DEFAULT_COLOR
    solid, degraded = _solid(size, color, notes)
    out, hold, into, by_seconds = _fade_phases(effect)
    span = out + hold + into
    if span <= 0:
        out, hold, into = 0.5, 0.0, 0.5
        by_seconds = False
        span = 1.0
    frames = []
    for t in frame_times(count):
        elapsed = (t * span) if by_seconds else t
        if out > 0 and elapsed < out:
            frames.append(Image.blend(before, solid, elapsed / out))
        elif elapsed < out + hold:
            frames.append(solid.copy())
        elif into > 0:
            alpha = min(1.0, max(0.0, (elapsed - out - hold) / into))
            frames.append(Image.blend(solid, after, alpha))
        else:
            frames.append(after.copy())
    return frames, degraded, notes


def _pixellate_frame(src, size, block: int):
    """把一帧按 `block` 尺寸块化（先缩到 1/k 再放大；k=1 原样返回）。"""
    block = max(1, int(block))
    width, height = size
    if block <= 1:
        return src
    small = (max(1, width // block), max(1, height // block))
    if small == (width, height):
        return src
    return src.resize(small, Image.Resampling.NEAREST).resize(
        size, Image.Resampling.NEAREST)


def _pixellate_frames(before, after, size, count):
    """前半段旧画面越切越碎，后半段新画面从最大块"解块化"回来。"""
    width, height = size
    biggest = max(2, min(PIXELLATE_MAX_BLOCK, min(width, height) // 2))
    frames = []
    for t in frame_times(count):
        if t < 0.5:
            phase = t / 0.5
            block = max(1, int(round(biggest * phase)))
            frames.append(_pixellate_frame(before, size, block))
        else:
            phase = (t - 0.5) / 0.5
            block = max(1, int(round(biggest * (1.0 - phase))))
            frames.append(_pixellate_frame(after, size, block))
    return frames


def _ramp_gray(img):
    """
    遮罩图 → 灰度 `L`（有真 alpha 通道就用 alpha：项目里的 wipe 图常是
    透明渐变 PNG，RGB 全 0 但 alpha 是渐变）。
    """
    if img.mode in ('RGBA', 'LA', 'PA'):
        alpha = img.getchannel('A')
        low, high = alpha.getextrema()
        if low != high:
            return alpha
    return img.convert('L')


def _ramp_mask(gray, t: float, ramplen: int):
    """按进度 `t` 把灰度遮罩切成"已经换到 after / 还是 before"的二值图。"""
    cut = min(1.0, max(0.0, float(t)))
    steps = int(ramplen or 0)
    if steps > 1:                          # ramplen = 遮罩分几档（Ren'Py 语义）
        cut = round(cut * (steps - 1)) / (steps - 1)
    threshold = int(round(cut * 255))
    return gray.point(lambda value: 255 if value <= threshold else 0)


def _imagedissolve_frames(effect, before, after, size, count, ramp,
                          project_root):
    """
    遮罩图灰度 ≤ 当前阈值的像素先切到 `after`。

    遮罩图找不到 → **如实退化**成普通溶解（`degraded=True` + note）。
    """
    ramp_src = ramp if ramp is not None else getattr(effect, 'ramp', '')
    ramp_img = load_ramp_image(ramp_src, project_root)
    if ramp_img is None:
        notes = []
        missing = str(getattr(effect, 'ramp', '') or '').strip()
        if missing:
            notes.append(f'找不到遮罩图 {missing}，已退化为普通溶解')
        else:
            notes.append('没有遮罩图，已退化为普通溶解')
        return _dissolve_frames(before, after, size, count), True, notes
    gray = _ramp_gray(ramp_img).resize(size, Image.Resampling.BILINEAR)
    ramplen = int(getattr(effect, 'ramplen', 0) or 0)
    frames = [Image.composite(after, before, _ramp_mask(gray, t, ramplen))
              for t in frame_times(count)]
    return frames, False, []


def _split_counts(total: int, weights) -> list:
    """
    `total` 帧按 `weights` 比例分给各段（最大余数法）。

    帧数够时**每段至少一帧**（`unknown` 段时长未知也占一份，不让它被悄悄
    挤掉），剩下的按比例分；帧数不够时按顺序一段一帧，后面的段分不到。
    """
    count = max(0, int(total))
    size = len(weights)
    if size == 0:
        return []
    if count == 0:
        return [0] * size
    if count < size:                       # 帧不够一段一帧：先给前几段
        return [1 if i < count else 0 for i in range(size)]
    clean = []
    for weight in weights:
        try:
            clean.append(max(0.0, float(weight or 0.0)))
        except (TypeError, ValueError):
            clean.append(0.0)
    counts = [1] * size                    # 每段先保底一帧
    extra = count - size
    if extra <= 0:
        return counts
    if sum(clean) <= 0:
        clean = [1.0] * size
    whole = sum(clean)
    exact = [extra * w / whole for w in clean]
    share = [int(value) for value in exact]
    rest = extra - sum(share)
    order = sorted(range(size), key=lambda i: (-(exact[i] - share[i]), i))
    for i in order[:rest]:
        share[i] += 1
    return [counts[i] + share[i] for i in range(size)]


def _multiple_frames(effect, before, after, size, count, ramp, project_root):
    """`multiple`：按 segments 分帧、逐段渲染、拼起来（记录每段帧区间）。"""
    segments = tuple(getattr(effect, 'segments', ()) or ())
    if not segments:
        return [], True, ['组合转场没有可识别的过渡段，无法预览'], ()
    weights = [max(0.0, float(getattr(seg, 'duration', 0.0) or 0.0))
               for seg in segments]
    counts = _split_counts(count, weights)
    frames: list = []
    ranges: list = []
    degraded = False
    notes: list = []
    for seg, sub_count in zip(segments, counts):
        start = len(frames)
        label = describe_effect(seg)
        if sub_count <= 0:
            if str(getattr(seg, 'kind', '') or 'unknown') == 'unknown':
                # 帧数不够，连"跳过"那帧都分不到 —— 但仍然要如实标降级
                notes.append(f'{label}：无法精确预览，已跳过')
                degraded = True
            ranges.append((start, start, label))
            continue
        part = _render_kind(seg, before, after, size, sub_count, ramp,
                            project_root, in_multiple=True)
        frames.extend(part[0])
        degraded = degraded or part[1]
        notes.extend(part[2])
        if not part[0]:
            # 认不出的子段不给帧（也不假装淡入），如实告诉调用方少演了一段
            notes.append(f'{label}：无法精确预览，已跳过')
            degraded = True
        ranges.append((start, len(frames), label))
    return frames, degraded, notes, tuple(ranges)


def _render_kind(effect, before, after, size, count, ramp, project_root,
                 in_multiple=False):
    """
    一个 `Effect` → `(帧, 是否降级, 说明列表, 段区间)`（内部统一出口）。

    `in_multiple=True` 时 `pause` 演成纯色（组合转场里的停顿通常是
    `Solid("#000"), Pause(…)`），单独出现时则保持画面不变。
    """
    kind = str(getattr(effect, 'kind', '') or 'unknown')
    segments: tuple = ()
    if kind == 'dissolve':
        return _dissolve_frames(before, after, size, count), False, [], segments
    if kind == 'fade':
        frames, degraded, notes = _fade_frames(effect, before, after, size, count)
        return frames, degraded, notes, segments
    if kind == 'pixellate':
        return (_pixellate_frames(before, after, size, count), False, [],
                segments)
    if kind == 'imagedissolve':
        frames, degraded, notes = _imagedissolve_frames(
            effect, before, after, size, count, ramp, project_root)
        return frames, degraded, notes, segments
    if kind == 'multiple':
        return _multiple_frames(effect, before, after, size, count, ramp,
                                project_root)
    if kind == 'pause':
        if in_multiple:
            notes: list = []
            color = str(getattr(effect, 'color', '') or '') or DEFAULT_COLOR
            solid, degraded = _solid(size, color, notes)
            return _hold(solid, count), degraded, notes, segments
        # 单独一发 `with Pause(…)`：画面不变（Ren'Py 语义：只是等一会儿）
        return _hold(before, count), False, ['停顿：画面保持不变'], segments
    if kind == 'solid':
        notes = []
        color = str(getattr(effect, 'color', '') or '') or DEFAULT_COLOR
        solid, degraded = _solid(size, color, notes)
        return _hold(solid, count), degraded, notes, segments
    # unknown / 空 kind：**不给帧**，只回 label —— 绝不假装淡入
    return [], True, [], segments


# ---------------------------------------------------------------------------
#  遮罩图加载（只有 ImageDissolve 用）
# ---------------------------------------------------------------------------

def _candidate_paths(ramp, project_root):
    """遮罩图的候选路径：绝对路径 → `<root>/game/…` → `<root>/…`。"""
    path = Path(str(ramp))
    if path.is_absolute():
        return [path]
    out = []
    if project_root is not None:
        root = Path(str(project_root))
        out.extend([root / 'game' / path, root / path])
    out.append(path)                       # 当前目录（探针/测试常这么给）
    return out


def load_ramp_image(ramp, project_root=None):
    """
    把 `Effect.ramp`（项目里的写法，如 `images/menu/wipe.png`）读成图片。

    接受：`PIL.Image`（原样）、路径（绝对，或相对 `project_root` 的
    `game/…` / 根目录）、numpy 数组；找不到/读不了 → `None`（调用方退化）。
    """
    if ramp is None or ramp == '':
        return None
    if not _PIL_OK:                        # 没装 Pillow：调用方按"缺遮罩"退化
        return None
    if isinstance(ramp, Image.Image):
        return ramp
    if hasattr(ramp, 'shape') and hasattr(ramp, 'dtype'):
        return _coerce_image(ramp)
    if not isinstance(ramp, (str, Path)):
        return None
    for candidate in _candidate_paths(ramp, project_root):
        try:
            if not candidate.is_file():
                continue
            with Image.open(candidate) as opened:
                return opened.convert('RGBA')
        except (OSError, ValueError, Image.DecompressionBombError) as e:
            swallow('app.transition_fx.load_ramp', e)
            continue
    return None


# ---------------------------------------------------------------------------
#  对外入口
# ---------------------------------------------------------------------------

def render_effect(before, after, effect, size=None, count=DEFAULT_FRAMES,
                  *, ramp=None, project_root=None) -> FxSequence:
    """
    `Effect` → `FxSequence`（过场的中间帧）。

    `before` / `after`：PIL RGBA、numpy 数组或 `None`（全透明空幕）；
    `size`：目标尺寸，缺省取 `before` 的；`count`：帧数（见模块 docstring
    的"帧数语义"）；`ramp` / `project_root`：ImageDissolve 的遮罩图来源。
    """
    if effect is None:
        effect = Effect(kind='unknown', label='（没有过场）')
    kind = str(getattr(effect, 'kind', '') or 'unknown')
    label = str(getattr(effect, 'label', '') or '') or \
        _KIND_LABELS.get(kind, '过场')
    if not _PIL_OK:
        return FxSequence(degraded=True, note='环境没有 Pillow，无法离屏渲染过场',
                          label=label, kind=kind)
    before_img = _coerce_image(before)
    after_img = _coerce_image(after)
    width, height = _resolve_size(size, before_img, after_img)
    if width <= 0 or height <= 0:
        return FxSequence(degraded=True, note='没有可用的画面尺寸，无法渲染过场',
                          label=label, kind=kind)
    target = (width, height)
    before_img = _fit(before_img, target)
    after_img = _fit(after_img, target)
    try:
        total = int(count)
    except (TypeError, ValueError):
        total = DEFAULT_FRAMES
    if total <= 0:
        return FxSequence(note='帧数为 0，不渲染', label=label, kind=kind,
                          size=target)
    try:
        frames, degraded, notes, segments = _render_kind(
            effect, before_img, after_img, target, total, ramp, project_root)
    except Exception as e:                 # noqa: BLE001 - 坏项目不该崩预览
        swallow('app.transition_fx.render_effect', e)
        return FxSequence(degraded=True, note='过场渲染出错，已跳过（只显示文字）',
                          label=label, kind=kind, size=target)
    notes = list(notes)
    if kind == 'unknown' or not frames:
        degraded = True
        if not notes and kind == 'unknown':
            notes.append('认不出这段转场，无法精确预览')
    return FxSequence(frames=tuple(frames), degraded=bool(degraded),
                      note=_join_notes(notes), label=label, kind=kind,
                      size=target, segments=tuple(segments))


def render_call(before, after, call, catalog=None, size=None,
                count=DEFAULT_FRAMES, *, ramp=None, project_root=None) -> FxSequence:
    """
    `TransitionCall` → `FxSequence`（内部先经 `effect_of` 翻成 `Effect`）。

    认不出这个调用 → 空帧 + `degraded`（不假装淡入），与
    `render_effect(Effect(kind='unknown'))` 同一条路。
    """
    try:
        effect = effect_of(call, catalog or {})
    except Exception as e:                 # noqa: BLE001 - 坏调用不该崩预览
        swallow('app.transition_fx.render_call', e)
        effect = Effect(kind='unknown', label='认不出这段过场，无法精确预览')
    return render_effect(before, after, effect, size, count, ramp=ramp,
                         project_root=project_root)


__all__ = [
    'DEFAULT_COLOR', 'DEFAULT_FRAMES', 'PIXELLATE_MAX_BLOCK',
    'FxSequence', 'available', 'frame_times', 'load_ramp_image',
    'render_call', 'render_effect',
]
