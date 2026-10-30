"""
tests/test_stage_transition_integration.py
==========================================
**③ 过场接入**（S5 第二波后半段）的判据：项目 `define` 的转场从 Beat 一路
走到**卡片摘要**与**演出预览的动画**。

三段链路：

  ① `app.stage`：`StageFrame.transitions`（三路派生）+ `StageFrame.effects`
     （项目转场目录解析，按项目根 + mtime **缓存**，不每帧扫盘）；
  ② `app.stage_render.transition_notes`：卡片「当前舞台」/演出预览共用的
     那一行「过场：…」（一拍多条只读不编号；`unknown` 只写名字 + 无法精确预览）；
  ③ `ui_qt.stage_preview`：跨拍播放时按 `Effect` 演中间帧（`app/transition_fx`）；
     `unknown` / 演不出来 → 硬切、只显示文字，**不假装淡入**；老式
     `with dissolve`（没派生到效果）保留原来的淡入。

判据分五类：
  A. 帧字段：三路都进 `transitions`，自定义名经项目目录解析成 `effects`；
  B. 缓存：同一项目反复问只扫一次；项目改了会重扫；
  C. 摘要行：构造器 / 自定义名 / unknown / 多条上限 / 没有就不加行；
  D. 动画：帧数按 `fps × 时长`，时长未知兜底 350ms，`unknown` 不演；
  E. 不回归：`StageFrame.fades`（老淡入判据）行为不变。
"""

from __future__ import annotations

import time

import pytest

from storygraph.app import stage_render as R
from storygraph.app.stage import (build_frame_at, build_frames,
                                  clear_transition_cache, project_transitions)
from storygraph.story.beat_model import Beat
from storygraph.story.transition_catalog import Effect, effect_of
from storygraph.story.transition_expr import parse_with_clause

#: 项目 define 的两个转场：一个渐变、一个"认得名字但不会演"的怪构造器
PROJECT_SRC = (
    'define 闪黑 = Fade(0.5)\n'
    'define 怪 = WeirdTransition(1.0)\n'
)


@pytest.fixture(autouse=True)
def _fresh_transition_cache():
    """每个用例从干净的转场目录缓存开始（模块级缓存不跨用例串味）。"""
    clear_transition_cache()
    yield
    clear_transition_cache()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'proj'
    game = root / 'game' / 'scripts'
    game.mkdir(parents=True)
    (game / 'transitions.rpy').write_text(PROJECT_SRC, encoding='utf-8')
    return root


def _beats():
    return [
        Beat(seq=1, label='t', speaker='e', text='一',
             with_='Dissolve(0.1)'),
        Beat(seq=2, label='t', speaker='e', text='二', with_='闪黑'),
        Beat(seq=3, label='t', speaker='e', text='三',
             code='renpy.transition(Fade(0.25), layer="master")'),
        Beat(seq=4, label='t', speaker='e', text='四', with_='怪'),
        Beat(seq=5, label='t', speaker='e', text='五'),
    ]


# ---------------------------------------------------------------------------
#  A. 帧字段
# ---------------------------------------------------------------------------

def test_frames_carry_transitions_and_effects(project):
    """三路来路都进 `transitions`；项目里 define 的名字解析成 `effects`。"""
    frames = build_frames(_beats(), project_root=project)
    assert len(frames) == 5
    first = frames[0]
    assert [call.expr for call in first.transitions] == ['Dissolve(0.1)']
    assert (first.effects[0].kind, first.effects[0].duration) == \
        ('dissolve', 0.1)
    # 自定义名 → 走项目目录翻成"怎么演"（不是 unknown）
    second = frames[1]
    assert second.effects[0].kind == 'fade'
    assert '闪黑' in second.effects[0].label
    # `$ renpy.transition(…, layer="master")` 的层要带出来
    assert frames[2].transitions[0].layer == 'master'
    # 项目里 define 了但构造器认不出 → unknown（不假装淡入）
    assert frames[3].effects[0].kind == 'unknown'
    # 没有过场的一拍：两个字段都是空（老调用方语义）
    assert frames[4].transitions == () and frames[4].effects == ()


def test_frames_without_project_root_still_carry_transitions():
    """不传项目根：裸构造器照常解析；自定义名如实 unknown（不猜）。"""
    frames = build_frames(_beats())
    assert frames[0].effects[0].kind == 'dissolve'
    assert frames[1].effects[0].kind == 'unknown'
    assert '闪黑' in frames[1].effects[0].label


def test_build_frame_at_matches_build_frames(project):
    """卡片走的单拍入口也要带过场，且与整章结果逐字段一致。"""
    beats = _beats()
    one = build_frame_at(beats, 1, project_root=project)
    full = build_frames(beats, project_root=project)[1]
    assert [c.expr for c in one.transitions] == [c.expr for c in full.transitions]
    assert [e.kind for e in one.effects] == [e.kind for e in full.effects]
    assert one.with_ == full.with_


def test_old_caller_can_construct_stage_frame_without_new_fields():
    """默认值兼容：老调用方（手工构造 StageFrame）不传新字段也不炸。"""
    from storygraph.app.stage import StageFrame

    frame = StageFrame(index=0, line_id='x', label='x', speaker='',
                       text='', scene='')
    assert frame.transitions == () and frame.effects == ()


# ---------------------------------------------------------------------------
#  B. 转场目录缓存（不每帧扫盘）
# ---------------------------------------------------------------------------

def test_catalog_is_scanned_once_per_project(project, monkeypatch):
    """同一项目反复问 → 只扫一次（真机 96 文件 0.1s，×1022 拍不可接受）。"""
    import storygraph.app.stage as stage_mod

    calls = []
    real = stage_mod.scan_project_transitions

    def counting(root):
        calls.append(root)
        return real(root)

    monkeypatch.setattr(stage_mod, 'scan_project_transitions', counting)
    clear_transition_cache()
    one = project_transitions(project)
    two = project_transitions(project)
    assert '闪黑' in one and one is two
    assert len(calls) == 1


def test_catalog_rescans_after_project_changes(project):
    """项目里改了 `.rpy` → 签名变了就重扫（不会一直拿旧效果演）。"""
    first = project_transitions(project)
    assert first['闪黑'].kind == 'fade'
    path = project / 'game' / 'scripts' / 'transitions.rpy'
    path.write_text('define 闪黑 = Pixellate(0.3)\n', encoding='utf-8')
    # refresh_seconds=0：跳过 TTL，直接比签名
    second = project_transitions(project, refresh_seconds=0)
    assert second['闪黑'].kind == 'pixellate'
    # 重扫就是读**现在**的文件：被删掉的 define 不该还留在目录里
    assert '怪' not in second


# ---------------------------------------------------------------------------
#  C. 卡片摘要行（stage_render.transition_notes）
# ---------------------------------------------------------------------------

def test_summary_line_for_constructor_and_layer(project):
    """`$ renpy.transition(Dissolve(0.1), layer="master")` → 拍板样例。"""
    frame = build_frames(_beats(), project_root=project)[2]
    lines = R.transition_notes(frame, project_root=project)
    assert lines == ['过场：Fade 0.25（master）']


def test_summary_line_dissolve_constructor_without_layer():
    frame = build_frames(_beats())[0]
    assert R.transition_notes(frame) == ['过场：Dissolve 0.1']


def test_summary_line_for_custom_project_name(project):
    """自定义名 → 人话效果名（`闪黑 1s（0.5 出 + 0 停 + 0.5 入）`）。"""
    frame = build_frames(_beats(), project_root=project)[1]
    line = R.transition_notes(frame, project_root=project)[0]
    assert line.startswith('过场：闪黑 1s')
    assert '出' in line and '入' in line


def test_summary_line_for_unknown_says_so(project):
    """认不出的转场只写名字 + "无法精确预览"（绝不写成"淡入"）。"""
    frame = build_frames(_beats(), project_root=project)[3]
    line = R.transition_notes(frame, project_root=project)[0]
    assert line == '过场：怪（无法精确预览）'


def test_summary_line_lists_at_most_two_then_counts():
    """一拍多条：最多列 2 条，后面用（…共 N 条）收尾（只读、不编号）。"""
    frame = build_frames(_beats())[0]
    extra = tuple(parse_with_clause('Fade(0.2)')) + \
        tuple(parse_with_clause('Pixellate(0.3)'))
    frame.transitions = frame.transitions + extra
    frame.effects = ()
    line = R.transition_notes(frame)[0]
    assert line.count('、') == 1, line
    assert '…共 3 条' in line


def test_no_transition_no_summary_line():
    """没有过场的一拍**一行都不加**（不做"硬切"占位）。"""
    frame = build_frames(_beats())[4]
    assert R.transition_notes(frame) == []
    assert R.transition_notes(None) == []


def test_summary_line_falls_back_to_with_field_for_old_frames():
    """老式帧只有 `with_`（没有派生过场）→ 原样写出来，不装效果。"""
    from storygraph.app.stage import StageFrame

    frame = StageFrame(index=0, line_id='x', label='x', speaker='',
                       text='', scene='', with_='dissolve')
    assert R.transition_notes(frame) == ['过场：dissolve']


def test_effect_of_dissolve_duration_is_honest():
    """`effect_of` 对裸构造器 `Dissolve(0.1)` → 时长 0.1（不是默认 0.5）。"""
    call = parse_with_clause('Dissolve(0.1)')[0]
    effect = effect_of(call, {})
    assert (effect.kind, effect.duration) == ('dissolve', 0.1)


# ---------------------------------------------------------------------------
#  D. 预览动画（不拉起整窗口，只测可无头验证的部件）
# ---------------------------------------------------------------------------

def _preview():
    import storygraph.ui_qt.stage_preview as SP
    return SP


def test_first_renderable_effect_skips_unknown(project):
    SP = _preview()
    frames = build_frames(_beats(), project_root=project)
    assert SP.first_renderable_effect(frames[0], project) is not None
    assert SP.first_renderable_effect(frames[3], project) is None   # unknown
    assert SP.first_renderable_effect(frames[4], project) is None   # 没有过场


def test_effect_frame_count_follows_duration_and_caps():
    SP = _preview()
    dissolve = Effect(kind='dissolve', duration=0.5)
    assert SP.effect_frame_count(dissolve) == 10        # 0.5s × 20fps
    seconds, fallback = SP.effect_seconds(
        Effect(kind='dissolve', duration=0.0))
    assert fallback is True
    assert seconds == R.FX_FALLBACK_SECONDS              # 时长未知 → 350ms
    assert SP.effect_frame_count(Effect(kind='dissolve')) == 7
    long = Effect(kind='dissolve', duration=10.0)
    assert SP.effect_frame_count(long) == SP.FX_MAX_FRAMES


def test_qimage_to_pil_roundtrip():
    """QImage ⇄ PIL 的往返要逐像素一致（过场 before/after 走这条路）。"""
    SP = _preview()
    Image = pytest.importorskip('PIL.Image')
    source = Image.new('RGBA', (8, 6), (12, 34, 56, 78))
    source.putpixel((3, 2), (200, 10, 5, 255))
    qimg = SP.image_to_qimage(source)
    back = SP.qimage_to_pil_image(qimg)
    assert back is not None
    assert back.mode == 'RGBA' and back.size == (8, 6)
    assert back.getpixel((3, 2)) == (200, 10, 5, 255)
    assert back.getpixel((0, 0)) == (12, 34, 56, 78)
    assert SP.qimage_to_pil_image(None) is None


def test_fx_job_renders_middle_frames_headlessly(project):
    """过场 worker：不用 GUI 事件循环也能算出中间帧（Pillow 在后台跑）。"""
    SP = _preview()
    Image = pytest.importorskip('PIL.Image')
    before = Image.new('RGBA', (16, 12), (200, 10, 10, 255))
    after = Image.new('RGBA', (16, 12), (10, 10, 200, 255))
    effect = Effect(kind='dissolve', duration=0.5)          # 10 帧交叉

    class _Done:
        def __init__(self):
            self.calls = []

        def emit(self, *args):
            self.calls.append(args)

    class _Signals:
        def __init__(self):
            self.done = _Done()

    signals = _Signals()
    job = SP._FxJob(signals, 7, 42, SP.image_to_qimage(before),
                    SP.image_to_qimage(after), effect, (16, 12),
                    SP.effect_frame_count(effect), project)
    job.run()
    assert len(signals.done.calls) == 1
    gen, key, frames, note, degraded = signals.done.calls[0]
    assert (gen, key) == (7, 42)
    assert len(frames) == SP.effect_frame_count(effect)   # 0.5s 淡入 → 10 帧
    assert all(q is not None and not q.isNull() for q in frames)
    assert degraded is False and note == ''
    # 中间帧确实在交叉（不是一张图复制 10 份）
    mid = SP.qimage_to_pil_image(frames[len(frames) // 2]).getpixel((4, 4))
    assert mid[0] < 200 and mid[2] > 10


def test_fx_job_reports_degraded_not_garbage():
    """遮罩图缺失的 image dissolve → worker 如实回 degraded + 退化帧。"""
    SP = _preview()
    Image = pytest.importorskip('PIL.Image')
    before = Image.new('RGBA', (8, 8), (200, 10, 10, 255))
    after = Image.new('RGBA', (8, 8), (10, 10, 200, 255))
    effect = Effect(kind='imagedissolve', duration=0.2,
                    ramp='images/nope.png', ramplen=64)

    class _Done:
        def __init__(self):
            self.calls = []

        def emit(self, *args):
            self.calls.append(args)

    class _Signals:
        def __init__(self):
            self.done = _Done()

    signals = _Signals()
    job = SP._FxJob(signals, 1, 2, SP.image_to_qimage(before),
                    SP.image_to_qimage(after), effect, (8, 8), 5, None)
    job.run()
    _gen, _key, frames, note, degraded = signals.done.calls[0]
    assert frames and degraded is True
    assert '遮罩' in note


def test_canvas_plays_effect_then_falls_back_to_normal_frame(qapp, project):
    """
    画布级：切到有 `Effect` 的一拍 → 后台演完中间帧 → 播完交回普通帧。

    不拉起整个窗口（无头里事件循环不转），所以用 `processEvents` 等信号。
    """
    SP = _preview()
    Image = pytest.importorskip('PIL.Image')
    beats = _beats()
    frames = build_frames(beats, project_root=project)
    canvas = SP.StageCanvas(engine_render=True, project_root=project)
    try:
        canvas.resize(320, 180)
        # 第一拍：直接同步渲染，成为"上一张图"
        canvas.render_now(frames[0], 0)
        assert canvas._last_good_pix is not None
        # 切到有 dissolve 的第二拍：pending 已挂上，但 after 图还没好
        canvas.show_frame(frames[1], 1)
        assert canvas._fx_pending is not None
        # 模拟 after 图就绪（正式播放由后台 `_RenderJob` 走到同一条路）
        canvas._rendered_pix = canvas._last_good_pix.copy()
        canvas._render_pending = False
        canvas._render_failed = False
        canvas._maybe_start_effect()
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not canvas.effect_active():
            qapp.processEvents()
            time.sleep(0.01)
        assert canvas.effect_active(), '过场中间帧没算出来'
        assert len(canvas._fx_frames) >= 2
        # 画一帧（不要求窗口可见：直接画到一张测试画布上）
        from PySide6.QtGui import QImage, QPainter
        target = QImage(320, 180, QImage.Format.Format_ARGB32)
        painter = QPainter(target)
        try:
            assert canvas._paint_fx_frame(painter, 320, 180) is True
        finally:
            painter.end()
        # 时间走完 → 停表、清空中间帧（落回"这一拍"的正常画面）
        canvas._fx_started = time.monotonic() - 999
        canvas._tick_fx()
        assert canvas.effect_active() is False
    finally:
        canvas.shutdown()


def test_canvas_unknown_effect_does_not_animate(qapp, project):
    """`unknown` 拍的画布**不演**任何中间帧（只显示文字，不假装淡入）。"""
    SP = _preview()
    frames = build_frames(_beats(), project_root=project)
    canvas = SP.StageCanvas(engine_render=True, project_root=project)
    try:
        canvas.resize(160, 90)
        canvas.render_now(frames[0], 0)
        canvas.show_frame(frames[3], 3)          # with_='怪' → unknown
        assert canvas._fx_pending is None
        canvas._rendered_pix = canvas._last_good_pix.copy()
        canvas._maybe_start_effect()
        assert canvas.effect_active() is False
    finally:
        canvas.shutdown()


# ---------------------------------------------------------------------------
#  E. 不回归：老淡入判据 / 摘要 / 快照文字
# ---------------------------------------------------------------------------

def test_old_fades_property_is_unchanged():
    """`StageFrame.fades` 仍是"老式 with_ 淡入"判据，跟新字段无关。"""
    fade = build_frames([Beat(seq=1, label='t', text='一',
                              with_='dissolve')])[0]
    assert fade.fades is True
    hard = build_frames([Beat(seq=1, label='t', text='一')])[0]
    assert hard.fades is False
    # 自定义名（不认识）以前不算 fade，现在也不算 —— 不因为新字段改变行为
    custom = build_frames([Beat(seq=1, label='t', text='一',
                                with_='close_eyes')])[0]
    assert custom.fades is False


def test_preview_summary_line_uses_project_catalog(project):
    """预览横条（`_summary_text`）要真的带上「过场：…」那一行。"""
    from types import SimpleNamespace

    SP = _preview()
    frame = build_frames(_beats(), project_root=project)[1]
    stub = SimpleNamespace(
        _canvas=SimpleNamespace(missing_assets=lambda: []),
        _audio=SimpleNamespace(note=lambda: ''),
        _sound_cb=SimpleNamespace(isChecked=lambda: False),
        _project_root=project,
    )
    text = SP.StagePreviewWindow._summary_text(stub, frame, {})
    assert '过场：闪黑 1s' in text


def test_preview_summary_has_no_transition_line_for_plain_frame():
    from types import SimpleNamespace

    SP = _preview()
    frame = build_frames(_beats())[4]
    stub = SimpleNamespace(
        _canvas=SimpleNamespace(missing_assets=lambda: []),
        _audio=SimpleNamespace(note=lambda: ''),
        _sound_cb=SimpleNamespace(isChecked=lambda: False),
    )
    text = SP.StagePreviewWindow._summary_text(stub, frame, {})
    assert '过场' not in text
