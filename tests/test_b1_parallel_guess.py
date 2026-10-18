"""
tests/test_b1_parallel_guess.py
===============================
B1（用户 2026-10-19）：`parallel:` / 关键帧里的位置**算不出来，就必须说出来**。

用户原话：

> 现在的问题不是"算不出"，是"算不出还不说"，那比算错更糟。
> B1 | 预览横条标「这个 transform 含 parallel/关键帧，位置是猜的」

判据分三层：

① `parse_transforms` 认得出这些结构（`parallel:` / `block:` / 动画帧）；
② `placement_of` → `Placement.uncertain` 有那句话（算出来了但只算了一部分，
   和"一个属性都没落到最终态、只能按名字猜"要说成两种话）；
③ 预览横条真的显示（`app.stage.placement_notes` + `StagePreviewDialog._summary_text`）。

"有把握时**一个字都不许多**"也在这里钉住 —— 到处标警告等于没警告。
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
#  ① 认得出结构
# ==================================================================

class TestParseMarksTheUncertainOnes:
    def test_parallel(self):
        idx = _idx('transform p:\n    parallel:\n        xpos 100\n')
        assert idx['p'].uncertain == 'parallel'

    def test_a_block_of_keyframes(self):
        idx = _idx('transform b:\n    block:\n        xpos 100\n        xpos 200\n')
        assert idx['b'].uncertain == '关键帧'

    def test_a_keyframe_in_the_body(self):
        idx = _idx('transform k:\n    xalign 0.5\n    linear 0.3 alpha 0.0\n')
        assert idx['k'].uncertain == '关键帧'

    def test_both_are_named_in_order(self):
        idx = _idx('transform m:\n    parallel:\n        xpos 1\n'
                   '    linear 0.2 alpha 0\n')
        assert idx['m'].uncertain == 'parallel/关键帧'

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
        assert idx['loop'].uncertain == '关键帧'


# ==================================================================
#  ② 位置带着那句话
# ==================================================================

class TestPlacementCarriesTheNote:
    def test_parallel_only_falls_back_and_says_it_was_a_name_guess(self):
        from storygraph.story.atl import placement_of
        idx = _idx('transform i11:\n    parallel:\n        xpos 100\n')
        p = placement_of('i11', idx, screen=(1920, 1080))
        assert p.uncertain, '只写在 parallel 里的位置必须标"猜的"'
        assert '含 parallel' in p.uncertain
        assert '按名字猜' in p.uncertain, p.uncertain

    def test_a_computed_transform_with_keyframes_is_flagged_too(self):
        from storygraph.story.atl import placement_of
        idx = _idx('transform mix:\n    xalign 0.25\n    linear 0.3 alpha 0\n')
        p = placement_of('mix', idx, screen=(1920, 1080))
        assert p.from_atl
        assert '含关键帧' in p.uncertain, p.uncertain
        assert p.x == pytest.approx(0.25)

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
#  ③ 横条真的显示
# ==================================================================

class TestThePreviewStripShowsIt:
    @staticmethod
    def _beats():
        from storygraph.story.beat_model import Beat, assign_identity
        beats = [Beat(seq=1, label='t', speaker='e', text='一',
                      show='ye happy@par')]
        assign_identity(beats)
        return beats

    def test_a_parallel_sprite_is_named_in_the_strip(self):
        from storygraph.app.stage import build_frames, placement_notes
        idx = _idx('transform par:\n    parallel:\n        xpos 100\n')
        f = build_frames(self._beats(), transform_index=idx,
                         screen=(1920, 1080))[0]
        notes = placement_notes(f)
        assert len(notes) == 1, notes
        assert notes[0].startswith('ye happy：'), notes[0]
        assert '这个 transform 含 parallel' in notes[0]
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

        idx = _idx('transform par:\n    parallel:\n        xpos 100\n')
        f = build_frames(self._beats(), transform_index=idx,
                         screen=(1920, 1080))[0]
        stub = SimpleNamespace(
            _canvas=SimpleNamespace(missing_assets=lambda: []),
            _audio=SimpleNamespace(note=lambda: ''),
            _sound_cb=SimpleNamespace(isChecked=lambda: True),
        )
        text = StagePreviewDialog._summary_text(stub, f, {})
        assert '⚠ ye happy：这个 transform 含 parallel' in text, text
        assert '猜' in text
