"""
tests/test_stage_preview_start.py
=================================
**演出预览从当前那一拍开始**，不是永远从第一拍。

用户 2026-10-22 连说两次：

    "剩下的，我看编辑 label 内部的卡片的效果还可以，就是演出预览总是从第一拍
     而不是当前拍展示。"

    "就是我在修改 200 拍的时候，按演出预览，然后出来的是第一拍，
     需要手动跳到第 200 拍！！！应当从当前拍开始预览……"

根因：`StagePreviewWindow.__init__` 里写死 `self.idx = 0`，`open_preview`
也不收起始拍 —— 谁调都一样从第 0 帧开始。

判据（都看 `win.idx`，不看界面）：
  1. 给了 `start_index` → 就是那一拍；
  2. 索引超过末尾 → 夹到最后一拍（不崩、不空屏）；
  3. 负数 / 垃圾值 → 夹到 0；
  4. 空 frames → 0（能开窗，只是没内容）；
  5. `open_preview(..., start_index=…)` 透传到窗口。
"""

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def _frames(n=6):
    """造 n 拍真帧（走工具自己的链路，避免手搓对象跟真实现漂移）。"""
    from storygraph.app.stage import build_frames
    from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
    from storygraph.story.beat_build import beats_from_parse_result

    src = 'label ch01:\n'
    for i in range(n):
        src += f'    e "第 {i} 句"\n'
    src += '    return\n'
    import tempfile
    from pathlib import Path
    td = Path(tempfile.mkdtemp())
    fp = td / 'ch.rpy'
    fp.write_text(src, encoding='utf-8')
    beats = beats_from_parse_result(
        ParseResultBuilder().build(RenPyParser().parse(str(fp))))
    return build_frames(beats)


@pytest.mark.parametrize('start,expected', [
    (0, 0),
    (3, 3),
    (5, 5),      # 最后一拍
    (99, 5),     # 越界 → 夹到最后一拍
    (-4, 0),     # 负数 → 0
])
def test_window_starts_at_the_given_frame(qapp, start, expected):
    from storygraph.ui_qt.stage_preview import StagePreviewWindow
    frames = _frames(6)
    win = StagePreviewWindow(None, frames, sound=False, start_index=start,
                             real_images=False)
    try:
        assert win.idx == expected
    finally:
        win.close()


def test_garbage_index_falls_back_to_zero(qapp):
    from storygraph.ui_qt.stage_preview import StagePreviewWindow
    win = StagePreviewWindow(None, _frames(3), sound=False,
                             start_index='不是数字', real_images=False)
    try:
        assert win.idx == 0
    finally:
        win.close()


def test_empty_frames_do_not_crash(qapp):
    from storygraph.ui_qt.stage_preview import StagePreviewWindow
    win = StagePreviewWindow(None, [], sound=False, start_index=7,
                             real_images=False)
    try:
        assert win.idx == 0
    finally:
        win.close()


def test_open_preview_passes_the_index_through(qapp):
    from storygraph.ui_qt.stage_preview import open_preview
    win = open_preview(None, _frames(8), sound=False, real_images=False,
                       start_index=6)
    try:
        assert win.idx == 6
    finally:
        win.close()
