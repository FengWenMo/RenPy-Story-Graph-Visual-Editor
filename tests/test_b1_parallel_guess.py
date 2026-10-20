"""
tests/test_b1_parallel_guess.py
===============================
B1 → B2（用户 2026-10-20）：**能算准的必须算准，算不准的必须说出来**。

B1（2026-10-19）只做了"算不出来要标警告"：

> 现在的问题不是"算不出"，是"算不出还不说"，那比算错更糟。

B2 把 `parallel:` / 关键帧从"猜"升级成"算"：

  · `parallel:` 的每条分支都作用在同一个基态上，末态是属性并集；
  · `linear/easein … xpos 1` 的**终点**就是停下来时的值；
  · `block:` / `animation:` 顺序执行，最后一帧赢；
  · `on show:` 是显示完的静止态；
  · 只有 `repeat` / `function` / `choice` 确实没有唯一末态，才标"猜的"。

判据分三层：

① `parse_transforms` 认得出这些结构，并把"真正算不准"的标出来；
② `placement_of` 算出精确位置（parallel/关键帧不再带警告）；
③ 真正算不准时，预览横条仍然把话说出来。
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest  # noqa: E402


def _idx(src: str) -> dict:
    from storygraph.story.atl import parse_transforms
    return parse_transforms(src)


# ==================================================================
#  ① 结构识别 / 只剩真正算不准的
# ==================================================================

class TestParseMarksTheUncertainOnes:
    def test_parallel_is_now_confident(self):
        idx = _idx('transform p:\n    parallel:\n        xpos 100\n')
        assert idx['p'].uncertain == ''

    def test_a_block_of_keyframes_is_now_confident(self):
        idx = _idx('transform b:\n    block:\n        xpos 100\n        xpos 200\n')
        assert idx['b'].uncertain == ''

    def test_a_keyframe_in_the_body_is_now_confident(self):
        idx = _idx('transform k:\n    xalign 0.5\n    linear 0.3 alpha 0.0\n')
        assert idx['k'].uncertain == ''

    def test_repeat_and_function_are_named(self):
        idx = _idx('transform m:\n    repeat\n    function foo\n')
        assert idx['m'].uncertain == 'repeat/function'

    def test_choice_is_flagged(self):
        idx = _idx('transform c:\n    choice:\n        xpos 1\n')
        assert idx['c'].uncertain == 'choice'

    def test_a_plain_resting_transform_is_confident(self):
        idx = _idx('transform still:\n    xalign 0.5\n    zoom 1.2\n')
        assert idx['still'].uncertain == ''

    def test_on_show_with_a_definite_end_is_confident(self):
        """`on show: linear .3 xoffset 0` 的末态是明确的（P1-4 那条不能退）。"""
        idx = _idx('transform slide:\n    on show:\n        xoffset -120\n'
                   '        linear .3 xoffset 0\n')
        assert idx['slide'].uncertain == ''

    def test_on_show_with_repeat_is_not(self):
        idx = _idx('transform loop:\n    on show:\n        repeat\n')
        assert idx['loop'].uncertain == 'repeat'


# ==================================================================
#  ② 精确位置
# ==================================================================

class TestPlacementIsExact:
    def test_parallel_branches_merge(self):
        from storygraph.story.atl import placement_of
        idx = _idx('transform p:\n    xpos 100\n'
                   '    parallel:\n        ypos 200\n        xoffset 30\n')
        p = placement_of('p', idx, screen=(1920, 1080))
        assert p.from_atl
        assert p.uncertain == ''
        assert p.box((400, 600), (1920, 1080)) == (130.0, 200.0, 400.0, 600.0)

    def test_block_last_keyframe_wins(self):
        from storygraph.story.atl import placement_of
        idx = _idx('transform b:\n    block:\n        xpos 100\n'
                   '        xpos 200\n')
        p = placement_of('b', idx, screen=(1920, 1080))
        assert p.box((400, 600), (1920, 1080))[0] == 200.0

    def test_source_order_survives_parallel(self):
        """`parallel` 之后又写 `xpos` —— 后写的必须赢。"""
        from storygraph.story.atl import placement_of
        idx = _idx('transform o:\n    parallel:\n        xpos 100\n'
                   '    xpos 300\n')
        p = placement_of('o', idx, screen=(1920, 1080))
        assert p.box((400, 600), (1920, 1080))[0] == 300.0

    def test_keyframe_end_value_wins(self):
        from storygraph.story.atl import placement_of
        idx = _idx('transform k:\n    xpos 100\n    linear .3 xpos 400\n')
        p = placement_of('k', idx, screen=(1920, 1080))
        assert p.box((400, 600), (1920, 1080))[0] == 400.0

    def test_a_confident_transform_has_no_note(self):
        from storygraph.story.atl import placement_of
        idx = _idx('transform leftish:\n    xalign 0.2\n')
        assert placement_of('leftish', idx).uncertain == ''

    def test_the_wording_is_the_users(self):
        from storygraph.story.atl import uncertain_note
        assert uncertain_note('parallel/关键帧') == \
            '这个 transform 含 parallel/关键帧，位置是猜的'
        assert uncertain_note('关键帧') == '这个 transform 含关键帧，位置是猜的'
        assert uncertain_note('') == ''


# ==================================================================
#  ③ 真正算不准时，横条真的显示
# ==================================================================

class TestThePreviewStripShowsIt:
    @staticmethod
    def _beats():
        from storygraph.story.beat_model import Beat, assign_identity
        beats = [Beat(seq=1, label='t', speaker='e', text='一',
                      show='ye happy@fn')]
        assign_identity(beats)
        return beats

    def test_a_guessed_sprite_is_named_in_the_strip(self):
        from storygraph.app.stage import build_frames, placement_notes
        idx = _idx('transform fn:\n    function foo\n')
        f = build_frames(self._beats(), transform_index=idx,
                         screen=(1920, 1080))[0]
        notes = placement_notes(f)
        assert len(notes) == 1, notes
        assert notes[0].startswith('ye happy：'), notes[0]
        assert '这个transform含function' in notes[0].replace(' ', '')
        assert '猜' in notes[0]

    def test_a_confident_frame_has_no_notes(self):
        from storygraph.app.stage import build_frames, placement_notes
        idx = _idx('transform par:\n    xalign 0.3\n')
        f = build_frames(self._beats(), transform_index=idx,
                         screen=(1920, 1080))[0]
        assert placement_notes(f) == []

    def test_the_dialog_summary_text_gets_the_star(self):
        """`_summary_text` 是横条的唯一入口：警告要真的进那条字符串。"""
        from storygraph.app.stage import build_frames
        from storygraph.ui_qt.stage_preview import (
            StagePreviewWindow as StagePreviewDialog)

        idx = _idx('transform fn:\n    function foo\n')
        f = build_frames(self._beats(), transform_index=idx,
                         screen=(1920, 1080))[0]
        stub = SimpleNamespace(
            _canvas=SimpleNamespace(missing_assets=lambda: []),
            _audio=SimpleNamespace(note=lambda: ''),
            _sound_cb=SimpleNamespace(isChecked=lambda: True),
        )
        text = StagePreviewDialog._summary_text(stub, f, {})
        assert '⚠yehappy：这个transform含function' in text.replace(' ', '')
        assert '猜' in text
