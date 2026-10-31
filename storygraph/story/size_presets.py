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

## 「自定义倍数」：人填的数也是预设（2026-10-31）

用户原话要的是「自定义 0.75×」——`50%` 只是 `0.5` 的特例，人想填 0.75 / 1.25
就得能填。这条路和上面几条**同一条判据**：先按几何在项目里找效果一样的
（`match_preset('custom', …, zoom=)`），找不到才由上层去生成
`sg_zoom_075`（`story.sg_transforms` 的三条护栏）。

这一层只多两件事：**认写法**（`parse_zoom`）与**说范围**
（`zoom_error`，0.05–5）—— 两件都是纯函数，面板、计划、确认框共用
（"输入框说能填" 和 "写盘前觉得不能写" 绝不能是两套判断）。
"""

from __future__ import annotations

import re

from storygraph.core.safeguard import swallow
from storygraph.story.atl import (DEFAULT_SCREEN, Placement, is_static_transform,
                                  placement_box, placement_of)

#: 框比较的容差（虚拟像素）。取 1.5：能吸收 `xsize 1920` 与 `fit` 的浮点尾数，
#: 又不会把"差 4 像素"的两条误判成同一条。
TOLERANCE = 1.5

#: 预设的固定顺序（UI 与文档都按这个来；改顺序要改判据）。
PRESET_ORDER = ('as_is', 'cover', 'contain', 'half')

#: 能生成 `sg_*` 的预设。`PRESET_ORDER` 是下拉里**固定那几项**；「自定义倍数」
#: 不进去（它的值是人填的，列不出有限几项），但"可生成"这件事和它们同级 ——
#: `story.sg_transforms.plan_define` 只认这个集合。
GENERATABLE = ('cover', 'contain', 'half', 'custom')

#: 「自定义倍数」允许的范围（2026-10-31）。
#:
#: Ren'Py 的 `zoom` 什么数都收：0 等于看不见；100 倍会把一张 4K 背景解成
#: 30 万像素宽 —— 预览进程直接卡死，而 `.rpy` 里那行"看起来"完全合法。
#: 所以范围**写在提示里、越界就拒绝**，不悄悄夹一个数（夹了人以为自己填的是
#: 那个值）。下限 0.05 也是"还看得见"的下限；上限 5 是"再大就没意义"。
ZOOM_MIN = 0.05
ZOOM_MAX = 5.0

#: 输入框认的写法：`0.75` / `0.75x` / `0.75×` / `0.75倍` / `75%`。
#: `75`（不带百分号）**不认** —— 它是 75 倍，但人多半想写 75%，猜错一次就是
#: 往项目文件里写一条 `zoom 75`。错误句里把两种写法都给他。
_ZOOM_TEXT = re.compile(r'^(\d+(?:\.\d+)?|\.\d+)([xX×倍%])?$')


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


def zoom_label(zoom) -> str:
    """倍数的人话（`0.75×`）；数不出来就写「自定义倍数」，**不编一个数**。"""
    try:
        return f'{float(zoom):g}×'
    except (TypeError, ValueError):
        return '自定义倍数'


def zoom_error(value) -> str:
    """
    这个倍数能不能写进 `.rpy`（`''` = 能，否则一句人话原因）。

    只有这一条判据，三处共用：面板（按下去之前）、`plan_define`（写盘之前）、
    确认框（说明里）。**绝不悄悄夹**：`0.001` 不会变成 `0.01`。
    """
    try:
        z = float(value)
    except (TypeError, ValueError):
        return '倍数得是一个数（例如 0.75）'
    if z != z:                                     # NaN：任何比较都是 False
        return '倍数得是一个数（例如 0.75）'
    if z == float('inf') or z == float('-inf'):
        return f'倍数得是一个有限数（{ZOOM_MIN:g}–{ZOOM_MAX:g}）'
    if z < ZOOM_MIN - 1e-9:
        return (f'{z:g} 倍太小了（允许 {ZOOM_MIN:g}–{ZOOM_MAX:g}）：'
                '再小预览和游戏里都只剩几个像素')
    if z > ZOOM_MAX + 1e-9:
        return (f'{z:g} 倍太大了（允许 {ZOOM_MIN:g}–{ZOOM_MAX:g}）：'
                f'一张 4K 背景会被解成 {int(3840 * z)} 像素宽'
                '（若你写的是百分比，请写成 75% 或 0.75 这样）')
    return ''


def parse_zoom(text):
    """
    输入框里那串字 → `(zoom, 错误句)`；一个成功另一个必为空。

    认这几种写法：`0.75` / `0.75x` / `0.75×` / `0.75倍` / `75%`。
    `75`（不带百分号）**不认** —— 它是 75 倍，但人多半想写 75%，猜错一次
    就要往项目文件里写一条 `zoom 75`。所以明说两种写法，不替人换算。

    值统一 `round(…, 3)`：`0.750` 与 `0.75` 必须是同一个数（否则 transform
    名会分成 `sg_zoom_075` 和 `sg_zoom_0p75` 两条）。
    """
    raw = str(text or '').strip().replace(' ', '')
    if not raw:
        return None, f'先填一个倍数（{ZOOM_MIN:g}–{ZOOM_MAX:g}），例如 0.75'
    hit = _ZOOM_TEXT.match(raw)
    if hit is None:
        return None, (f'这个写法认不出来：「{raw}」—— 填 0.75（也可以写 '
                      '0.75× 或 75%）')
    value = float(hit.group(1))
    if hit.group(2) == '%':
        value = value / 100.0
    value = round(value, 3)
    err = zoom_error(value)
    if err:
        return None, err
    return value, ''


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


#: 判"不用图尺寸也能比"时用的两个**参考**图尺寸（都不是真图，互不相同）。
#: 纯缩放的框是图尺寸的**一次函数**：两个不同的值都比同一框 ⇒ 任何图尺寸都同框。
#: （`fit` / `xsize` 那些是分段/常数函数，不在这个比较范围内 —— 它们本来
#: 就有真图可比，不需要这条路。）
_REF_NATURALS = ((1280, 720), (3200, 1800))


def match_without_natural(key: str, screen=DEFAULT_SCREEN, index=None,
                          zoom: float = None):
    """
    **不需要知道图多大**的预设匹配（只对倍数类预设：`half` / `custom`）。

    什么时候需要它：这一行的图找不到（`natural=None`），可"项目里已经有一条
    `zoom 0.5`"这件事其实和图片大小无关 —— 真机 `sr_general` 里那条就叫
    `cg38402160`（`zoom 0.5; anchor (0,0)`）。拿不到图就查重不到，白写一条
    `sg_zoom_050`，正是护栏 ② 要防的重复。

    判据仍然是**效果**：纯缩放（没有 `fit` / `xsize` / `ysize`）的框是图尺寸的
    一次函数，所以在 `_REF_NATURALS` 两个参考尺寸上框都相同 ⇒ 任何图尺寸都
    相同。**位置参与比较** —— `zoom 0.6` 但居中放的那条不会冒充"左上角 0.6"。

    多条等价时取名字排序第一个（确定性；候选全是纯缩放，"语义"这一层没有
    可挑的差别 —— `_preference` 在这里用不上）。

    返回 `{'name', 'brief'}`；没有 → None。
    """
    key = str(key or '')
    z = target_zoom(key, zoom)
    if z is None or key == 'as_is':
        return None
    target = Placement(x_px=0.0, x_kind='left', xanchor=0.0,
                       y_px=0.0, y_kind='top', yanchor=0.0,
                       xzoom=z, yzoom=z, fit=None, source='preset')
    screen_pair = _screen_of(screen)
    for name in sorted(index or {}):
        defn = index[name]
        if not is_static_transform(defn, index):
            continue
        try:
            place = placement_of(name, index, screen=screen_pair)
        except Exception as e:                 # noqa: BLE001 - 坏定义跳过
            swallow('story.size_presets.match_without_natural.place', e)
            continue
        if place is None or str(getattr(place, 'uncertain', '') or ''):
            continue
        if getattr(place, 'fit', None) \
                or getattr(place, 'xsize', None) is not None \
                or getattr(place, 'ysize', None) is not None:
            continue                           # 框不是图尺寸的一次函数
        try:
            if abs(float(getattr(place, 'alpha', 1.0)) - 1.0) > 1e-6:
                continue
            if abs(float(getattr(place, 'rotate', 0.0))) > 1e-6:
                continue
        except (TypeError, ValueError) as e:
            swallow('story.size_presets.match_without_natural.flags', e)
            continue
        same = True
        for natural in _REF_NATURALS:
            try:
                got = placement_box(place, natural, screen_pair)
                want = placement_box(target, natural, screen_pair)
            except Exception as e:             # noqa: BLE001 - 算不出就是不等
                swallow('story.size_presets.match_without_natural.box', e)
                same = False
                break
            if not _same_box(got, want):
                same = False
                break
        if same:
            return {'name': name, 'brief': _brief(place)}
    return None


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


__all__ = ['GENERATABLE', 'PRESET_ORDER', 'TOLERANCE', 'ZOOM_MAX', 'ZOOM_MIN',
           'atl_body', 'describe_match', 'match_preset',
           'match_without_natural', 'parse_zoom', 'preset_label',
           'preset_note', 'target_box', 'target_zoom', 'transform_options',
           'zoom_error', 'zoom_label']
