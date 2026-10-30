"""
tests/test_transition_fx.py
===========================
**过场效果帧渲染引擎**（`storygraph.app.transition_fx`）的判据。

`transition_catalog` 只回答"这个过场是什么"（kind / 时长 / 颜色 / 遮罩 /
子段），这一层回答"**每一帧画什么**"：把 `Effect` 演成 N 张 RGBA 中间帧，
供卡片缩略图与演出预览使用。它是纯函数、不 import Qt，所以这里用 8×8 /
64×64 的小图直接采样像素断言，不依赖界面。

判据分六类：
  ① **帧契约**：RGBA / 统一尺寸 / 帧数 / 帧中心采样（不画首尾）；
  ② **各 kind 演得对**：dissolve 半透明交叉、fade 中段变暗、pixellate 中段
    块化、imagedissolve 用 ramp 分半、multiple 分段、pause/solid；
  ③ **诚实**：unknown **不给帧**；ramp 缺失 → 退化 dissolve 且 `degraded`；
     认不出的颜色 / 空尺寸 / 空输入 —— 降级要标注，不许假装成功；
  ④ **真实项目口径**：从临时项目的 `define` + 遮罩 PNG 一路走到帧（不写死
     任何项目常量）；
  ⑤ **输入宽容**：numpy 输入、尺寸不同、`None` 画面、1 帧 / 0 帧不炸；
  ⑥ **纯模块**：不 import Qt；不写文件。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from PIL import Image

from storygraph.app import transition_fx
from storygraph.app.transition_fx import (
    FxSequence, available, frame_times, load_ramp_image, render_call,
    render_effect)
from storygraph.story.transition_catalog import (Effect,
                                                 scan_project_transitions)
from storygraph.story.transition_expr import parse_with_clause

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)


# ---------------------------------------------------------------------------
#  小工具：造图 / 采样 / 帧契约
# ---------------------------------------------------------------------------

def _solid(size, color):
    return Image.new('RGBA', size, color)


def _gradient(size):
    """水平 0→255 灰阶（相邻像素差很小，块化一眼能测出来）。"""
    width, height = size
    img = Image.new('RGBA', size)
    pixels = img.load()
    for x in range(width):
        value = round(255 * x / max(1, width - 1))
        for y in range(height):
            pixels[x, y] = (value, value, value, 255)
    return img


def _close(actual, expected, tolerance=2):
    """RGBA 元组逐通道近似相等（PIL 的 blend 会四舍五入）。"""
    assert len(actual) == len(expected), f'{actual} vs {expected}'
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


def _assert_frames(seq, size, count=None):
    """帧契约：RGBA / 统一尺寸 / 帧数（`FxSequence.size` 也要对得上）。"""
    assert isinstance(seq, FxSequence)
    if count is not None:
        assert len(seq.frames) == count, f'{len(seq.frames)} != {count}'
    assert seq.size == tuple(size)
    for frame in seq.frames:
        assert isinstance(frame, Image.Image)
        assert frame.mode == 'RGBA'
        assert frame.size == tuple(size)


def _longest_run(frame, y=0):
    """第 y 行最长的"连续同色"长度（块化 → 变长）。"""
    width = frame.width
    row = [frame.getpixel((x, y)) for x in range(width)]
    best = run = 1
    for i in range(1, width):
        run = run + 1 if row[i] == row[i - 1] else 1
        best = max(best, run)
    return best


def _max_neighbour_diff(frame, y=0):
    """第 y 行相邻像素的最大通道差（块边界 → 变大）。"""
    width = frame.width
    row = [frame.getpixel((x, y))[0] for x in range(width)]
    return max(abs(row[i] - row[i - 1]) for i in range(1, width))


# ---------------------------------------------------------------------------
#  ① 帧契约
# ---------------------------------------------------------------------------

def test_pillow_is_available():
    """能跑这个测试文件就说明 Pillow 在；`available()` 要与之一致。"""
    assert available() is True


def test_frame_times_are_frame_centres_never_endpoints():
    """帧中心采样：0.1/0.3/0.5/0.7/0.9 —— 不画首尾（首尾调用方已有）。"""
    assert frame_times(5) == (0.1, 0.3, 0.5, 0.7, 0.9)
    assert frame_times(1) == (0.5,)
    assert frame_times(0) == ()
    assert frame_times(-2) == ()
    assert frame_times('3') == (1 / 6, 0.5, 5 / 6)


def test_sequence_metadata_and_to_dict():
    seq = render_effect(_solid((8, 8), RED), _solid((8, 8), BLUE),
                        Effect(kind='dissolve', duration=0.5, label='溶解 0.5s'),
                        count=3)
    assert (seq.kind, seq.label, seq.size) == ('dissolve', '溶解 0.5s', (8, 8))
    assert seq.ok is True and seq.degraded is False and seq.note == ''
    data = seq.to_dict()
    assert data['frames'] == 3 and data['kind'] == 'dissolve'
    assert data['size'] == [8, 8] and data['degraded'] is False


# ---------------------------------------------------------------------------
#  ② 各 kind 演得对
# ---------------------------------------------------------------------------

def test_dissolve_middle_is_half_blend():
    """线性 alpha 交叉：正中间恰好一半旧一半新。"""
    before, after = _solid((16, 16), RED), _solid((16, 16), BLUE)
    seq = render_effect(before, after, Effect(kind='dissolve', duration=0.5),
                        count=5)
    _assert_frames(seq, (16, 16), count=5)
    mid = seq.frames[2].getpixel((0, 0))
    assert _close(mid, (128, 0, 128, 255))
    assert seq.degraded is False


def test_dissolve_channels_move_monotonically():
    """旧画面的红一路降、新画面的蓝一路升（不是突然跳变）。"""
    before, after = _solid((8, 8), RED), _solid((8, 8), BLUE)
    seq = render_effect(before, after, Effect(kind='dissolve'),
                        count=5)
    reds = [frame.getpixel((0, 0))[0] for frame in seq.frames]
    blues = [frame.getpixel((0, 0))[2] for frame in seq.frames]
    assert reds == sorted(reds, reverse=True)
    assert blues == sorted(blues)
    assert reds[0] > reds[-1] and blues[-1] > blues[0]


def test_dissolve_blends_alpha_channel_too():
    """before 全透明、after 不透明 → 中段 alpha ≈ 128（不是只混 RGB）。"""
    before = Image.new('RGBA', (8, 8), (255, 0, 0, 0))
    after = Image.new('RGBA', (8, 8), (0, 0, 255, 255))
    seq = render_effect(before, after, Effect(kind='dissolve'), count=3)
    assert abs(seq.frames[1].getpixel((0, 0))[3] - 128) <= 2


def test_fade_dark_middle_with_out_hold_in():
    """
    `Fade(0.25, 0.5, 0.25)`：出 0.25 → 停 0.5（全黑）→ 入 0.25。

    catalog 已把三段时长塞进 `Effect.out_time / hold_time / in_time`，
    引擎要按它演，不是把总时长一均分。
    """
    effect = Effect(kind='fade', duration=1.0, color='#000000',
                    out_time=0.25, hold_time=0.5, in_time=0.25)
    white = _solid((8, 8), WHITE)
    seq = render_effect(white, white, effect, count=5)
    _assert_frames(seq, (8, 8), count=5)
    for index in (1, 2, 3):                # 停段：全黑
        assert seq.frames[index].getpixel((0, 0)) == BLACK
    assert 100 <= seq.frames[0].getpixel((0, 0))[0] <= 200   # 出段：半暗
    assert 100 <= seq.frames[4].getpixel((0, 0))[0] <= 200   # 入段：半暗
    assert seq.degraded is False


def test_fade_without_split_falls_back_to_half_half():
    """只有 `duration`（老数据）→ 出/入各一半；正中间仍是全黑。"""
    effect = Effect(kind='fade', duration=1.0, color='#000')
    white = _solid((8, 8), WHITE)
    seq = render_effect(white, white, effect, count=5)
    assert seq.frames[2].getpixel((0, 0)) == BLACK
    assert seq.frames[1].getpixel((0, 0)) != BLACK     # 半程不是一上来就黑


def test_fade_middle_shows_the_color():
    """停段够长时，中段帧应该是 `color` 本身（不是黑）。"""
    effect = Effect(kind='fade', duration=1.0, color='#400000',
                    out_time=0.1, hold_time=0.8, in_time=0.1)
    seq = render_effect(_solid((8, 8), WHITE), _solid((8, 8), WHITE),
                        effect, count=5)
    assert seq.frames[2].getpixel((0, 0)) == (0x40, 0, 0, 255)


def test_fade_unknown_color_degrades_to_black():
    effect = Effect(kind='fade', duration=1.0, color='not-a-color',
                    out_time=0.4, hold_time=0.2, in_time=0.4)
    seq = render_effect(_solid((8, 8), WHITE), _solid((8, 8), WHITE),
                        effect, count=5)
    assert seq.degraded is True
    assert '认不出颜色' in seq.note
    assert seq.frames[2].getpixel((0, 0)) == BLACK


def test_pixellate_middle_is_blocky():
    """前半段越切越碎、后半段解块化：正中间是最大块。"""
    before = _gradient((64, 64))
    effect = Effect(kind='pixellate', duration=0.5)
    seq = render_effect(before, before, effect, count=5)
    _assert_frames(seq, (64, 64), count=5)
    mid = seq.frames[2]
    # 64 宽、最大块 32 → 一行只有 2 个色块：左半同色、跨块跳变
    assert mid.getpixel((0, 0)) == mid.getpixel((31, 31))
    assert mid.getpixel((0, 0)) != mid.getpixel((32, 0))
    assert _longest_run(mid) >= 16
    assert _longest_run(mid) > _longest_run(seq.frames[0])
    assert _max_neighbour_diff(mid) > 2 * _max_neighbour_diff(seq.frames[0])


def test_pixellate_first_and_last_frames_are_less_blocky():
    """进度两端块小 → 细节多；中间块大 → 细节少（块化是渐进的）。"""
    gradient = _gradient((64, 64))
    seq = render_effect(gradient, gradient,
                        Effect(kind='pixellate'), count=5)
    colours = [len(set(frame.getdata())) for frame in seq.frames]
    assert colours[2] < colours[0]
    assert colours[1] <= colours[0]


def test_imagedissolve_uses_ramp_halves():
    """遮罩图左黑右白 → 中段左半已经切到 after、右半还是 before。"""
    ramp = Image.new('L', (16, 16), 0)
    ramp.paste(255, (8, 0, 16, 16))
    effect = Effect(kind='imagedissolve', duration=0.5,
                    ramp='images/wipe.png', ramplen=64)
    seq = render_effect(_solid((16, 16), RED), _solid((16, 16), BLUE),
                        effect, ramp=ramp, count=5)
    _assert_frames(seq, (16, 16), count=5)
    mid = seq.frames[2]
    assert mid.getpixel((4, 8)) == BLUE     # 黑（先切）→ 新画面
    assert mid.getpixel((12, 8)) == RED     # 白（后切）→ 旧画面
    assert seq.degraded is False


def test_imagedissolve_ramplen_quantises_steps():
    """`ramplen` 把阈值切成有限档：进度相近的帧应落回同一档（不炸即可）。"""
    ramp = Image.new('L', (8, 8), 0)
    ramp.paste(255, (4, 0, 8, 8))
    effect = Effect(kind='imagedissolve', ramp='w.png', ramplen=4)
    seq = render_effect(_solid((8, 8), RED), _solid((8, 8), BLUE),
                        effect, ramp=ramp, count=4)
    assert len(seq.frames) == 4
    assert seq.degraded is False


def test_missing_ramp_degrades_to_dissolve_and_says_so():
    """ramp 图缺失：退化成普通溶解，但必须 `degraded=True` + 写明原因。"""
    effect = Effect(kind='imagedissolve', duration=0.5,
                    ramp='images/nope.png', label='图像溶解 0.5s')
    seq = render_effect(_solid((8, 8), RED), _solid((8, 8), BLUE),
                        effect, project_root=Path('.'), count=5)
    _assert_frames(seq, (8, 8), count=5)
    assert seq.degraded is True
    assert '找不到遮罩图' in seq.note and '退化为普通溶解' in seq.note
    assert _close(seq.frames[2].getpixel((0, 0)), (128, 0, 128, 255))


def test_imagedissolve_without_ramp_name_degrades():
    effect = Effect(kind='imagedissolve', duration=0.5, ramp='')
    seq = render_effect(_solid((8, 8), RED), _solid((8, 8), BLUE), effect,
                        count=3)
    assert seq.degraded is True
    assert '没有遮罩图' in seq.note


def test_multiple_segments_and_frame_ranges():
    """`multiple`：三段等长 → 帧也三等分；区间写进 `FxSequence.segments`。"""
    segments = (Effect(kind='dissolve', duration=1.0, label='溶解 1s'),
                Effect(kind='pause', duration=1.0, label='停顿 1s'),
                Effect(kind='dissolve', duration=1.0, label='溶解 1s'))
    effect = Effect(kind='multiple', duration=3.0, segments=segments,
                    label='组合：溶解 → 停顿 → 溶解')
    seq = render_effect(_solid((8, 8), RED), _solid((8, 8), BLUE), effect,
                        count=9)
    _assert_frames(seq, (8, 8), count=9)
    assert isinstance(seq.segments, tuple) and len(seq.segments) == 3
    assert [seg[:2] for seg in seq.segments] == [(0, 3), (3, 6), (6, 9)]
    assert seq.segments[-1][1] == len(seq.frames)
    assert seq.frames[4].getpixel((0, 0)) == BLACK       # 组合里的停顿=全黑
    assert seq.degraded is False


def test_multiple_frames_follow_segment_durations():
    """帧数按时长比例分：1s/2s/1s、8 帧 → 2/4/2。"""
    segments = (Effect(kind='dissolve', duration=1.0),
                Effect(kind='dissolve', duration=2.0),
                Effect(kind='dissolve', duration=1.0))
    effect = Effect(kind='multiple', duration=4.0, segments=segments)
    seq = render_effect(_solid((4, 4), RED), _solid((4, 4), BLUE), effect,
                        count=8)
    assert [seg[:2] for seg in seq.segments] == [(0, 2), (2, 6), (6, 8)]


def test_multiple_with_unknown_segment_is_degraded():
    """组合里有一段认不出 → 只演认得的，并如实标降级（不假装淡入）。"""
    segments = (Effect(kind='dissolve', duration=1.0),
                Effect(kind='unknown', label='怪转场（无法精确预览）'))
    effect = Effect(kind='multiple', duration=1.0, segments=segments)
    seq = render_effect(_solid((4, 4), RED), _solid((4, 4), BLUE), effect,
                        count=6)
    # 认不出的段不产帧（不是拿它当溶解）：帧会比请求的少，且如实标降级
    assert 0 < len(seq.frames) < 6
    assert seq.degraded is True
    assert '无法精确预览' in seq.note


def test_multiple_without_segments_is_honest():
    effect = Effect(kind='multiple', segments=(), label='组合转场')
    seq = render_effect(_solid((4, 4), RED), _solid((4, 4), BLUE), effect,
                        count=5)
    assert seq.frames == ()
    assert seq.degraded is True
    assert '没有可识别的过渡段' in seq.note


def test_pause_standalone_holds_the_frame():
    """单独一发 `with Pause(0.5)`：只是等，画面不变（不换纯色）。"""
    before, after = _solid((8, 8), RED), _solid((8, 8), BLUE)
    seq = render_effect(before, after, Effect(kind='pause', duration=0.5),
                        count=4)
    _assert_frames(seq, (8, 8), count=4)
    assert all(frame.getpixel((0, 0)) == RED for frame in seq.frames)
    assert '画面保持不变' in seq.note


def test_solid_renders_its_color():
    seq = render_effect(_solid((8, 8), RED), _solid((8, 8), BLUE),
                        Effect(kind='solid', color='#123456'), count=3)
    assert seq.frames
    assert all(frame.getpixel((0, 0)) == (0x12, 0x34, 0x56, 255)
               for frame in seq.frames)


def test_unknown_effect_gives_no_frames():
    """unknown **不给帧**：调用方只能显示 label，绝不套默认淡入。"""
    label = 'WeirdTransition（无法精确预览）'
    seq = render_effect(_solid((8, 8), RED), _solid((8, 8), BLUE),
                        Effect(kind='unknown', label=label), count=5)
    assert seq.frames == ()
    assert len(seq) == 0
    assert seq.degraded is True
    assert seq.label == label
    assert seq.kind == 'unknown'
    assert '无法精确预览' in seq.note


# ---------------------------------------------------------------------------
#  ③ 输入宽容 / 边界
# ---------------------------------------------------------------------------

def test_none_canvases_still_render_transparent_frames():
    seq = render_effect(None, None, Effect(kind='dissolve'), size=(16, 12),
                        count=3)
    _assert_frames(seq, (16, 12), count=3)
    assert seq.frames[1].getpixel((0, 0))[3] == 0


def test_zero_size_falls_back_to_the_canvas_size():
    before = _solid((8, 8), RED)
    seq = render_effect(before, None, Effect(kind='dissolve'), size=(0, 0),
                        count=3)
    _assert_frames(seq, (8, 8), count=3)


def test_no_usable_size_fails_honestly():
    seq = render_effect(None, None, Effect(kind='dissolve'), size=(0, 0),
                        count=3)
    assert seq.frames == ()
    assert seq.degraded is True
    assert '尺寸' in seq.note


def test_zero_frames_and_one_frame_do_not_crash():
    zero = render_effect(_solid((4, 4), RED), _solid((4, 4), BLUE),
                         Effect(kind='dissolve'), count=0)
    assert zero.frames == () and zero.degraded is False
    assert '帧数' in zero.note
    one = render_effect(_solid((4, 4), RED), _solid((4, 4), BLUE),
                        Effect(kind='dissolve'), count=1)
    _assert_frames(one, (4, 4), count=1)
    assert _close(one.frames[0].getpixel((0, 0)), (128, 0, 128, 255))


def test_mismatched_sizes_are_fitted_to_one_target():
    before = _solid((8, 8), RED)
    after = _solid((32, 32), BLUE)
    seq = render_effect(before, after, Effect(kind='dissolve'), count=3)
    _assert_frames(seq, (8, 8), count=3)


def test_non_square_target_size():
    seq = render_effect(_solid((8, 8), RED), _solid((8, 8), BLUE),
                        Effect(kind='dissolve'), size=(24, 8), count=2)
    _assert_frames(seq, (24, 8), count=2)


def test_numpy_arrays_are_accepted():
    """numpy 输入（Pillow 的 `fromarray` 路径）；返回值仍是 PIL 帧。"""
    numpy = pytest.importorskip('numpy')
    before = numpy.zeros((8, 8, 4), dtype='uint8')
    before[..., 0] = 255
    before[..., 3] = 255
    after = numpy.zeros((8, 8, 4), dtype='uint8')
    after[..., 2] = 255
    after[..., 3] = 255
    seq = render_effect(before, after, Effect(kind='dissolve'), count=3)
    _assert_frames(seq, (8, 8), count=3)
    assert _close(seq.frames[1].getpixel((0, 0)), (128, 0, 128, 255))


# ---------------------------------------------------------------------------
#  ④ 真实项目口径：define → effect → 帧
# ---------------------------------------------------------------------------

def _write_project(root, src):
    path = root / 'game' / 'scripts' / 'transitions.rpy'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(src, encoding='utf-8')
    return root


def _write_ramp(root, rel, width=16, height=16):
    """左黑右白的遮罩图：真机 wipe 图就是这种"灰度渐变"。"""
    path = root / 'game' / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    ramp = Image.new('L', (width, height), 0)
    ramp.paste(255, (width // 2, 0, width, height))
    ramp.save(path)
    return path


def test_project_define_and_wipe_png_to_frames(tmp_path):
    """临时项目：`define wipe = ImageDissolve(...)` + 遮罩 PNG → 真帧。"""
    project = _write_project(tmp_path / 'proj', (
        'define wipe = ImageDissolve("images/menu/wipe.png", 0.5, '
        'ramplen=64)\n'))
    ramp_path = _write_ramp(project, 'images/menu/wipe.png')
    assert load_ramp_image('images/menu/wipe.png', project) is not None
    catalog = scan_project_transitions(project)
    calls = parse_with_clause('wipe')
    seq = render_call(_solid((16, 16), RED), _solid((16, 16), BLUE),
                      calls[0], catalog=catalog, project_root=project,
                      count=5)
    _assert_frames(seq, (16, 16), count=5)
    assert seq.degraded is False
    mid = seq.frames[2]
    assert mid.getpixel((4, 8)) == BLUE
    assert mid.getpixel((12, 8)) == RED
    assert ramp_path.is_file()


def test_render_call_with_inline_constructor():
    calls = parse_with_clause('Dissolve(0.5)')
    assert len(calls) == 1
    seq = render_call(_solid((8, 8), RED), _solid((8, 8), BLUE), calls[0],
                      count=5)
    _assert_frames(seq, (8, 8), count=5)
    assert _close(seq.frames[2].getpixel((0, 0)), (128, 0, 128, 255))


def test_render_call_with_unknown_name_is_honest():
    calls = parse_with_clause('没有定义的转场')
    seq = render_call(_solid((8, 8), RED), _solid((8, 8), BLUE), calls[0],
                      count=5)
    assert seq.frames == ()
    assert seq.degraded is True


# ---------------------------------------------------------------------------
#  ⑤ 纯模块纪律
# ---------------------------------------------------------------------------

def test_module_does_not_import_qt():
    """纯函数模块：不许把 Qt 拖进无界面路径（渲染层要能直接 import）。"""
    source = inspect.getsource(transition_fx)
    assert 'PySide6' not in source
    assert 'ui_qt' not in source


def test_rendering_is_pure_memory_and_does_not_touch_disk(tmp_path):
    """渲染后目录里没有新文件（纯内存）。"""
    before_dir = set(tmp_path.iterdir())
    render_effect(_solid((8, 8), RED), _solid((8, 8), BLUE),
                  Effect(kind='dissolve'), count=3)
    assert set(tmp_path.iterdir()) == before_dir
