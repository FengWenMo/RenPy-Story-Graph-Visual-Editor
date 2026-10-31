"""
storygraph/story/size_presets.py
================================
**尺寸预设**：人话（原样 / 铺满 / 适应 / 50%）↔ 项目里已有的 transform（2026-10-31）。

用户二轮反馈的原话是「bg 和 cg 不能改像素」，而真机量出来的第一个原因不是
"没入口"，是**尺寸是持续状态**（见 `app/stage.plan_at_edit` 的 `source_index`）。
第二个原因是这里：人能说清的是"这张图太大，铺满屏幕"，能写进 `.rpy` 的却必须是
transform 名（`bgfit` / `cg38402160`）。这一层负责把前者翻成后者。

## 唯一的判据是**效果**，不是名字

把这个 transform 套在这张图 + 这个屏幕上，用 `atl.placement_box` 算出屏幕上的
框 `(left, top, w, h)`，与预设想要的框**逐像素相同**（±`TOLERANCE`）才算匹配。
名字、注释、参数写法一律不看 —— 谁做到同样的事就用谁（`bgfit` 可以是
`fit "cover"`，也可以是 `xysize (1920, 1080)`，结果一样就该被认出来）。

## 不猜的几条

  · 带动画 / 事件块的 transform **不当尺寸变换**（`atl.is_static_transform`）：
    「改尺寸」不该顺手给人加一段 `easein`；
  · 几何算不出来（`placement.uncertain`）、改透明度/旋转的 → 不参与匹配；
  · 一个都匹配不上时**不编一个**：返回 None + 一句人话，由上层去提示
    （"生成 sg_*"是下一步的事，护栏就绪前这里什么都不写）；
  · 多条几何等价时先按**语义**、再按名字取第一条（`_preference`）：一张
    3840×2160 的图放在 1920×1080 屏幕上，「50%」与「铺满」的框逐像素相同
    （半个 4K 正好是 1080p），纯按名字会选到 `fit "cover"` 那条 —— 结果一样，
    但写进脚本的意思是另一回事；
  · `nearest` 只影响采样，不影响几何，所以不排除（真机两个项目里
    `bgfit` / `bgnearfit` 就是这样）。

## 「原样」= 不套变换

它不是一条 transform，而是**去掉 `at`**（写回时由 `plan_at_edit(mode='clear')`
处理）。所以 `target_box('as_is')` 给的是"引擎默认摆法"那个框（左上角、原尺寸），
只用于告诉人"现在会是什么样"，不用于匹配。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow
from storygraph.story.atl import (DEFAULT_SCREEN, Placement, is_static_transform,
                                  placement_box, placement_of)

#: 框比较的容差（虚拟像素）。取 1.5：能吸收 `xsize 1920` 与 `fit` 的浮点尾数，
#: 又不会把"差 4 像素"的两条误判成同一条。
TOLERANCE = 1.5

#: 预设的固定顺序（UI 与文档都按这个来；改顺序要改判据）。
PRESET_ORDER = ('as_is', 'cover', 'contain', 'half')


def preset_label(key: str) -> str:
    """预设的人话名（**唯一措辞**：UI / 确认框 / 日志共用）。"""
    return {
        'as_is': '原样（不套变换）',
        'cover': '铺满（裁掉多余）',
        'contain': '适应（留黑边）',
        'half': '50%',
    }.get(str(key or ''), str(key or ''))


def preset_note(key: str) -> str:
    """这句预设**会做什么**（tooltip / 说明用；不含具体 transform 名）。"""
    return {
        'as_is': '去掉这一行的 at：按引擎默认摆法（左上角、原尺寸）显示。',
        'cover': '按比例放大到盖住屏幕，多出来的部分裁掉。',
        'contain': '按比例缩到能完整看见，不够的地方留黑边。',
        'half': '缩到原图的一半（左上角对齐，与不带 at 的摆法同源）。',
    }.get(str(key or ''), '')


def atl_body(key: str, zoom: float = None) -> str:
    """
    预设 → transform 定义**体**（生成 `sg_*` 时用它，见下一步的护栏）。

        cover   → `fit "cover"`
        contain → `fit "contain"`
        half    → `zoom 0.5`
        自定义  → `zoom <数>`

    「原样」没有体（它不生成任何东西）→ 空串。
    """
    key = str(key or '')
    if key == 'cover':
        return 'fit "cover"'
    if key == 'contain':
        return 'fit "contain"'
    if key in ('half', 'custom'):
        z = 0.5 if key == 'half' else float(zoom if zoom is not None else 1.0)
        return f'zoom {z:g}'
    return ''


def target_zoom(key: str, zoom: float = None):
    """这个预设对应的缩放倍数（`cover` / `contain` / `as_is` → None）。"""
    key = str(key or '')
    if key == 'half':
        return 0.5
    if key == 'custom':
        try:
            return max(0.01, float(zoom))
        except (TypeError, ValueError):
            return None
    return None


def target_box(key: str, natural, screen=DEFAULT_SCREEN, zoom: float = None):
    """
    预设想要的屏幕框 `(left, top, w, h)`；认不出返回 None。

    ⚠ 位置**一律左上角 (0,0)**：这是引擎不给位置时的默认摆法，也是
    `scene bg X at bgfit` 真机上出现的样子。要居中是另一件事（那是 `xalign`），
    不在这里替人决定。
    """
    if not natural or len(natural) < 2:
        return None
    key = str(key or '')
    z = target_zoom(key, zoom)
    if z is None and key not in ('cover', 'contain', 'as_is'):
        return None
    fit = {'cover': 'cover', 'contain': 'contain'}.get(key)
    place = Placement(x_px=0.0, x_kind='left', xanchor=0.0,
                      y_px=0.0, y_kind='top', yanchor=0.0,
                      xzoom=z or 1.0, yzoom=z or 1.0, fit=fit,
                      source='preset')
    try:
        return placement_box(place, natural, screen)
    except Exception as e:                      # noqa: BLE001 - 坏尺寸就当算不出
        swallow('story.size_presets.target_box', e)
        return None


def _same_box(box, target) -> bool:
    """两个框在容差内相同吗（四边都要比：只看 w/h 会把"挪了位置"当成匹配）。"""
    if not box or not target:
        return False
    try:
        return all(abs(float(a) - float(b)) <= TOLERANCE
                   for a, b in zip(box, target))
    except (TypeError, ValueError):
        return False


def _brief(place) -> str:
    """一条 transform 的**效果人话**（`fit "cover"` / `zoom 0.5` / `位置`）。"""
    fit = str(getattr(place, 'fit', '') or '')
    if fit:
        return f'fit "{fit}"'
    xz = float(getattr(place, 'xzoom', 1.0) or 1.0)
    yz = float(getattr(place, 'yzoom', 1.0) or 1.0)
    if abs(xz - yz) < 1e-6:
        if abs(xz - 1.0) > 1e-6:
            return f'zoom {xz:g}'
    else:
        return f'xzoom {xz:g}，yzoom {yz:g}'
    xs = getattr(place, 'xsize', None)
    ys = getattr(place, 'ysize', None)
    if xs is not None or ys is not None:
        bits = []
        if xs is not None:
            bits.append(f'xsize {float(xs):g}')
        if ys is not None:
            bits.append(f'ysize {float(ys):g}')
        return '，'.join(bits)
    return '位置'


def transform_options(index, natural=None, screen=DEFAULT_SCREEN) -> list:
    """
    项目里**可以列进「自定义」**的 transform：`[{'name','brief','box',
    'changes_size'}, …]`（按"会改尺寸的在前、同组按名字"排序）。

    带动画的、几何算不出的、改透明度/旋转的一律不列 —— 那都不是"改尺寸"。
    `natural` 给了才带 `box`（有框才能拿来做几何匹配）。
    """
    index = index or {}
    screen_pair = _screen_of(screen)
    out = []
    for name in sorted(index):
        defn = index[name]
        if not is_static_transform(defn, index):
            continue
        try:
            place = placement_of(name, index, screen=screen_pair)
        except Exception as e:                 # noqa: BLE001 - 坏定义跳过
            swallow('story.size_presets.transform_options.place', e)
            continue
        if place is None or str(getattr(place, 'source', '')) == 'center':
            # `center` = 什么都没落下来（名字也没认出来）：不列，避免"选了个未知"
            continue
        if getattr(place, 'uncertain', ''):
            continue
        try:
            if abs(float(getattr(place, 'alpha', 1.0)) - 1.0) > 1e-6:
                continue
            if abs(float(getattr(place, 'rotate', 0.0))) > 1e-6:
                continue
        except (TypeError, ValueError) as e:
            # 透明度/旋转读不出明确数值 → 不当尺寸变换（不猜）
            swallow('story.size_presets.transform_options.flags', e)
            continue
        box = None
        if natural:
            try:
                box = placement_box(place, natural, screen_pair)
            except Exception as e:             # noqa: BLE001 - 算不出框就不带框
                swallow('story.size_presets.transform_options.box', e)
                box = None
        xz = float(getattr(place, 'xzoom', 1.0) or 1.0)
        yz = float(getattr(place, 'yzoom', 1.0) or 1.0)
        changes = bool(getattr(place, 'fit', None)
                       or getattr(place, 'xsize', None) is not None
                       or getattr(place, 'ysize', None) is not None
                       or abs(xz - 1.0) > 1e-6 or abs(yz - 1.0) > 1e-6)
        out.append({'name': name, 'brief': _brief(place), 'box': box,
                    'fit': str(getattr(place, 'fit', '') or ''),
                    'zoom': xz, 'changes_size': changes})
    out.sort(key=lambda e: (not e['changes_size'], e['name']))
    return out


def _screen_of(screen) -> tuple:
    try:
        sw, sh = (tuple(screen) + DEFAULT_SCREEN)[:2]
        return (max(1, int(sw)), max(1, int(sh)))
    except (TypeError, ValueError):
        return DEFAULT_SCREEN


def _preference(key: str, opt: dict, zoom: float = None) -> tuple:
    """
    多条几何等价时**先看语义、再看名字**（确定性，不随机）。

    为什么不是纯按名字（2026-10-31 当场想到的反例）：一张 3840×2160 的图放在
    1920×1080 屏幕上，「50%」和「铺满」算出来的框**逐像素相同**（半个 4K 正好
    就是 1080p）。只按名字排会选到 `bgfit`（`fit "cover"`）—— 对这张图结果一样，
    可它写进脚本的意思是"铺满"，将来这一行换成别的图就会变形。所以先按
    **语义**挑：铺满/适应优先 `fit` 对得上的，50%/自定义优先 `zoom` 对得上的；
    同为 0 分才按名字。
    """
    key = str(key or '')
    fit = str(opt.get('fit') or '')
    try:
        z = float(opt.get('zoom') or 1.0)
    except (TypeError, ValueError):
        z = 1.0
    if key in ('cover', 'contain'):
        score = 0 if fit == key else 1
    elif key in ('half', 'custom'):
        want = target_zoom(key, zoom)
        score = 0 if (not fit and want is not None
                      and abs(z - float(want)) <= 1e-6) else 1
    else:
        score = 0
    return (score, str(opt.get('name') or ''))


def match_preset(key: str, natural, screen=DEFAULT_SCREEN, index=None,
                 zoom: float = None):
    """
    预设 → 项目里**效果相同**的 transform；没有返回 None（**绝不编一个**）。

    返回 `{'name', 'brief', 'box', 'target'}`；`box` 是实际框（与 `target`
    在容差内相同），给 UI 说"用它会长这样"。
    """
    target = target_box(key, natural, screen, zoom=zoom)
    if target is None:
        return None
    hits = [opt for opt in transform_options(index, natural, screen)
            if _same_box(opt.get('box'), target)]
    if not hits:
        return None
    hits.sort(key=lambda opt: _preference(key, opt, zoom))
    opt = hits[0]
    return {'name': opt['name'], 'brief': opt['brief'],
            'box': opt.get('box'), 'target': target}


def describe_match(key: str, hit) -> str:
    """一条匹配结果的人话（下拉条目文本；没匹配上也说得清楚）。"""
    label = preset_label(key)
    if not hit:
        return f'{label}（项目里没有几何匹配的 transform）'
    text = f'{label} → {hit["name"]}（{hit["brief"]}）'
    brief = str(hit.get('brief') or '')
    other = {'cover': 'contain', 'contain': 'cover'}.get(str(key or ''))
    if other and f'fit "{other}"' in brief:
        # 只有"这张图与屏幕同比例"时 cover / contain 才会算出同一个框。
        # 不解释的话，人看到「适应（留黑边）→ bgfit（fit "cover"）」会以为选错了。
        text += '（这张图与屏幕比例相同，铺满/适应结果一样）'
    elif str(key or '') in ('half', 'custom') and 'fit "' in brief:
        text += '（这张图缩小后正好铺满屏幕，和「铺满」结果一样）'
    return text


__all__ = ['PRESET_ORDER', 'TOLERANCE', 'atl_body', 'describe_match',
           'match_preset', 'preset_label', 'preset_note', 'target_box',
           'target_zoom', 'transform_options']
