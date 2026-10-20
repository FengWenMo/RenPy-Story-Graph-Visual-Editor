"""
舞台预览的**速度与不黑屏**（用户 2026-10-21）

> 预览窗口需要优化：使用引擎预览时，一拍一拍渲染太慢了！需要优化，
> 现在很难加载（尤其面对连续播放时速度不够常常变黑屏）

三条判据，都对着"用户看到的症状"，而不是对着实现：

  · 换拍时**画面上有东西**（上一张顶着），不是底板色 —— 黑屏是可测的像素；
  · 往后几拍**提前合成**好了（轮到它时是缓存命中，同步出图）；
  · 连按方向键时，**过期任务不算**（不会把 10 张没人看的整帧都算一遍）。

另外两条是"改了渲染器但画面对不上"的老风险：
  · `_fill` 收元组时**不能**变成白色（`_color` 按字符串解析，元组会落白）；
  · 文本框/名条底图改用缓存之后，**每拍不再重读同一个文件**。
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip('PySide6')

from PIL import Image                                        # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer                # noqa: E402
from PySide6.QtGui import QColor                             # noqa: E402


def _spin(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


def _wait(cond, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        _spin(20)
    return bool(cond())


def _window(app, project):
    from storygraph.app.editor import EditorSession
    from storygraph.pipeline.build import load_asset_registry
    from storygraph.ui_qt.stage_preview import StagePreviewWindow

    s = EditorSession(project_root=project)
    s.load(project / 'game' / 'script' / 'ch01.rpy')
    return StagePreviewWindow(None, s.frames(), project_root=project,
                              registry=load_asset_registry(project),
                              sound=False)


@pytest.fixture
def slow_render(monkeypatch):
    """
    让"合成一帧"变慢（0.25s），用来验"慢渲染时用户看到什么"。

    真机上一次合成 25~300ms（碰上大盘立绘解码是 1 秒级），这里取中间值，
    既能让"还没画好"这个状态稳定出现，又不至于把测试拖慢。
    """
    from storygraph.app import stage_render as R

    real = R.render_frame
    calls = []

    def _slow(*args, **kwargs):
        calls.append(1)
        time.sleep(0.25)
        return real(*args, **kwargs)

    monkeypatch.setattr(R, 'render_frame', _slow)
    return calls


# ==================================================================
#  一、渲染器本身：颜色与缓存（无头，毫秒级）
# ==================================================================

class TestRendererBasics:
    def test_fill_with_a_tuple_never_turns_black_into_white(self):
        """
        ⚠ 这条是**回归判据**：`_fill` 一开始只认字符串，`(0,0,0,255)` 走
        `_color` 会落到白色 fallback —— 也就是"没有背景的拍"整屏刷白。
        这种错不会报错、不会崩，只会让人以为"这章就是白底"。
        """
        from storygraph.app import stage_render as R

        canvas = Image.new('RGBA', (4, 4), (1, 2, 3, 255))
        R._fill(canvas, (0, 0, 0, 255))
        assert canvas.getpixel((2, 2)) == (0, 0, 0, 255)
        R._fill(canvas, (18, 18, 24, 255))
        assert canvas.getpixel((2, 2)) == (18, 18, 24, 255)

    def test_fill_is_pixel_identical_to_alpha_composite(self):
        """不透明色走 `paste` 的那个优化**不许改像素**（半透明色仍然混合）。"""
        from storygraph.app import stage_render as R

        for color in ((10, 20, 30, 255), '#0a141e', (255, 255, 255, 128)):
            plain = Image.new('RGBA', (6, 6), (5, 6, 7, 8))
            ref = plain.copy()
            rgba = color if isinstance(color, tuple) else R._color(color)
            ref.alpha_composite(Image.new('RGBA', ref.size, rgba))
            R._fill(plain, color)
            assert plain.tobytes() == ref.tobytes(), f'{color} 像素不一致'

    def test_a_frame_without_a_scene_is_black(self):
        """没有背景的拍 → 纯黑（不是白、不是透明）。"""
        from storygraph.app import stage_render as R
        from storygraph.app.stage import StageFrame

        f = StageFrame(index=0, line_id='x', label='x', speaker='', text='',
                       scene='')
        got = R.render_frame(f, project_root=None, registry=None)
        assert got is not None and got.image is not None
        assert got.image.getpixel((5, 5)) == (0, 0, 0, 255)

    def test_images_are_read_from_disk_once(self, tmp_path):
        """
        文本框/名条底图每拍都一样：**缓存之后不许再读第二遍**。

        从前 `_draw_say` 每一拍 `Image.open` + `convert('RGBA')` 一次整屏宽的
        文本框底图 —— 播放时一拍一次，纯属白读。
        """
        from storygraph.app import stage_render as R

        path = tmp_path / 'textbox.png'
        Image.new('RGBA', (8, 8), (9, 9, 9, 255)).save(path)
        opened = []
        real_open = R.Image.open

        def _counting(*args, **kwargs):
            opened.append(args)
            return real_open(*args, **kwargs)

        R._image_cached.cache_clear()
        R._nine_slice_cached.cache_clear()
        try:
            R.Image.open = _counting
            a = R._image_cached(str(path), R._mtime(path))
            b = R._image_cached(str(path), R._mtime(path))
            c = R._nine_slice_cached(str(path), R._mtime(path), 40, 20,
                                     (2.0, 2.0, 2.0, 2.0))
            d = R._nine_slice_cached(str(path), R._mtime(path), 40, 20,
                                     (2.0, 2.0, 2.0, 2.0))
        finally:
            R.Image.open = real_open
        assert a is b, '同一张图第二次读盘了'
        assert c is d, '同尺寸的九宫格第二次切了'
        assert len(opened) == 1, f'读盘 {len(opened)} 次（应该 1 次）'


# ==================================================================
#  二、预览窗口：不黑屏 / 提前合成 / 不白算（真 Qt，offscreen）
# ==================================================================

class TestPreviewSpeed:
    def test_slow_render_holds_the_previous_picture(self, qapp, ui_project,
                                                    slow_render):
        """
        **用户看到的黑屏**：换拍时这一拍还没合成完，画面就空了。

        现在：`_rendered_pix`（这一拍）为 None，但画布画的是 `_last_good_pix`
        （上一张）—— 判据是**像素**不是标志位，取一块避开正中央/角标的区域。
        """
        from storygraph.ui_qt import tokens as T

        w = _window(qapp, ui_project)
        canvas = w._canvas
        assert canvas.engine_render
        assert _wait(lambda: canvas._rendered_pix is not None), '第一拍没渲染出来'

        last = len(w.frames) - 1
        assert last >= 2, '这条判据要一章够长的脚本'
        canvas.show_frame(w.frames[last], last)      # 不给 ahead：必定还没好
        assert canvas._rendered_pix is None
        assert canvas._last_good_pix is not None

        shot = canvas.grab().toImage()
        px = shot.pixelColor(shot.width() // 4, shot.height() // 4)
        assert px.name().lower() != QColor(
            T.t('stage.letterbox')).name().lower(), \
            f'换拍时黑屏了（像素 {px.name()}）'
        assert _wait(lambda: canvas._rendered_pix is not None), '最后一拍没渲染出来'
        w.close()

    def test_engine_mode_renders_the_next_beats_ahead(self, qapp, ui_project):
        """
        引擎排版的"预取"= **提前合成整帧**：翻到第 0 拍之后，往后几拍就该
        在后台画起来（轮到它们时是缓存命中，同步出图）。
        """
        from storygraph.ui_qt import stage_preview as SP

        w = _window(qapp, ui_project)
        canvas = w._canvas
        want = min(len(w.frames), 1 + SP.ENGINE_AHEAD)
        ok = _wait(lambda: sum(1 for f in w.frames[:want]
                               if canvas._cache_get(f) is not None) >= 2, 10)
        assert ok, f'往后几拍没有提前合成：缓存 {canvas.cached_frames()} 帧'
        w.close()

    def test_rapid_flipping_does_not_render_every_beat(self, qapp, ui_project,
                                                       slow_render):
        """
        连按方向键：只有**真的还在看的那几拍**该被合成，排队的过期任务丢掉。

        从前的写法是"每次换拍都往全局线程池塞一个任务"，翻 10 下一个不留，
        10 张 1920×1080 全算完再被 `token` 丢掉 —— 机器越慢，卡得越久。
        """
        w = _window(qapp, ui_project)
        canvas = w._canvas
        assert _wait(lambda: canvas._rendered_pix is not None)
        slow_render.clear()
        n = min(len(w.frames), 10)
        for i in range(1, n):
            w.show_frame(i)                  # 不 spin：模拟快速连按
        started = len(slow_render)
        assert started <= 4, \
            f'连翻 {n - 1} 拍却开算了 {started} 张整帧（过期任务没被丢掉）'
        w.close()

    def test_playback_keeps_going_with_a_slow_renderer(self, qapp, ui_project,
                                                       slow_render):
        """
        播放**不许被渲染卡住**：时间到了就往下走，画面用上一张顶着。
        （从前每拍都清空，慢机器上就是"黑一下、图一下"地抽搐。）
        """
        w = _window(qapp, ui_project)
        canvas = w._canvas
        assert _wait(lambda: canvas._rendered_pix is not None)
        w.start_play()
        try:
            for _ in range(20):
                _spin(80)
                assert (canvas._rendered_pix is not None
                        or canvas._last_good_pix is not None), '播放中出现了全黑'
        finally:
            w.stop_play()
        assert w.idx >= 1, '播放没往前走（被渲染卡住了）'
        w.close()


# ==================================================================
#  三、无头 / CI：真实画面也要能自动验（用户 2026-10-21）
# ==================================================================

class TestHeadlessRendering:
    """
    "无头环境出不了图、一直被'正在渲染…'占住，CI 只能覆盖算法层"。

    根因不是渲染坏了，是**等法不对**：正常播放走后台线程 + Qt 信号，
    结果要事件循环转起来才回到 GUI 线程；"构造完立刻断言"当然永远没有图。
    同步那条路是 `StageCanvas.render_now()` —— 这里钉住它，CI 从此能看像素。
    """

    def test_render_now_produces_a_real_picture_without_an_event_loop(
            self, qapp, ui_project):
        from storygraph.app.editor import EditorSession
        from storygraph.ui_qt import tokens as T
        from storygraph.ui_qt.stage_preview import StageCanvas

        s = EditorSession(project_root=ui_project)
        s.load(str(ui_project / 'game' / 'script' / 'ch01.rpy'))
        frames = s.frames()
        canvas = StageCanvas(None, project_root=ui_project, engine_render=True)
        canvas.resize(896, 504)

        got = canvas.render_now(frames[0], 0)      # **不转事件循环**
        assert got is not None and got.image is not None, \
            '同步渲染没出图（CI 就永远只能测算法层）'
        assert canvas._rendered_pix is not None
        assert not canvas.is_pending()

        shot = canvas.grab().toImage()
        px = shot.pixelColor(shot.width() // 4, shot.height() // 4)
        assert px.name().lower() != QColor(
            T.t('stage.letterbox')).name().lower(), \
            f'画出来还是底板色（{px.name()}）'

    def test_render_now_reports_warnings_headlessly(self, qapp, ui_project):
        """缺图名单在无头下也拿得到（横条上那条警告不是只在真机上有效）。"""
        from storygraph.app.editor import EditorSession
        from storygraph.ui_qt.stage_preview import StageCanvas

        s = EditorSession(project_root=ui_project)
        s.load(str(ui_project / 'game' / 'script' / 'ch01.rpy'))
        frames = s.frames()
        canvas = StageCanvas(None, project_root=ui_project, engine_render=True)
        canvas.resize(896, 504)
        canvas.render_now(frames[0], 0)
        assert isinstance(canvas.render_warnings(), list)

