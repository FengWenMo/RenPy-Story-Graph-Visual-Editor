"""
tests/test_stage_geometry_exact.py
==================================
舞台几何的**逐条 Ren'Py 语义**（2026-10-20，B2）。

为什么单独钉：预览要"跟真正编译的一模一样"，第一道关不是画得好不好看，
而是**同一个 transform 算出来的框是否与引擎一致**。这里把容易错的几条
全部写成可手算的断言：

  · 小数 = 屏幕/图像比例，整数 = 像素（写法决定，不按大小猜）；
  · `xpos` 只改位置，`xcenter` 改位置并把 anchor 设成 0.5；
  · `xalign` 同时改位置和 anchor；`xanchor` 最后写就最后赢；
  · `ypos 1200` 是**顶边**在 1200，不是底边（旧实现正好反了）；
  · `zoom` 同时改 x/y，`xzoom`/`yzoom` 可以不等比；
  · `left` / `right` / `truecenter` 是引擎内置 transform 的真实值。
"""

from __future__ import annotations

import pytest

from storygraph.story.atl import parse_transforms, placement_of


SCREEN = (1920, 1080)
NATURAL = (400, 600)


def _place(src: str, name: str = 't'):
    return placement_of(name, parse_transforms(src), screen=SCREEN)


class TestXAxis:
    def test_int_xpos_is_pixels_with_default_anchor_zero(self):
        p = _place('transform t:\n    xpos 300\n')
        assert p.box(NATURAL, SCREEN) == (300.0, 0.0, 400.0, 600.0)

    def test_float_xpos_is_fraction_of_screen(self):
        p = _place('transform t:\n    xpos 0.5\n')
        assert p.box(NATURAL, SCREEN)[0] == pytest.approx(960.0)

    def test_xcenter_int_centers_the_image(self):
        p = _place('transform t:\n    xcenter 320\n')
        assert p.box(NATURAL, SCREEN)[0] == pytest.approx(120.0)

    def test_xcenter_float_uses_screen_fraction(self):
        p = _place('transform t:\n    xcenter 0.25\n')
        assert p.box(NATURAL, SCREEN)[0] == pytest.approx(0.25 * 1920 - 200)

    def test_xalign_moves_both_pos_and_anchor(self):
        left = _place('transform t:\n    xalign 0.0\n')
        right = _place('transform t:\n    xalign 1.0\n')
        assert left.box(NATURAL, SCREEN)[0] == 0.0
        assert right.box(NATURAL, SCREEN)[0] == pytest.approx(1920 - 400)

    def test_explicit_xanchor_overrides_xcenter(self):
        p = _place('transform t:\n    xcenter 960\n    xanchor 0.0\n')
        assert p.box(NATURAL, SCREEN)[0] == 960.0

    def test_xoffset_is_added_after_position(self):
        p = _place('transform t:\n    xpos 100\n    xoffset 30\n')
        assert p.box(NATURAL, SCREEN)[0] == 130.0

    def test_pixel_anchor_is_not_a_fraction(self):
        p = _place('transform t:\n    xpos 100\n    xanchor 40\n')
        assert p.box(NATURAL, SCREEN)[0] == 60.0


class TestYAxis:
    def test_int_ypos_is_the_top_edge(self):
        """旧实现把 `ypos 1200` 当底边 → 整张图往上跑 600px。"""
        p = _place('transform t:\n    ypos 1200\n')
        assert p.box(NATURAL, SCREEN)[1] == 1200.0

    def test_float_ypos_is_a_fraction(self):
        p = _place('transform t:\n    ypos 0.75\n')
        assert p.box(NATURAL, SCREEN)[1] == pytest.approx(0.75 * 1080)

    def test_yanchor_one_puts_the_bottom_at_ypos(self):
        p = _place('transform t:\n    ypos 1000\n    yanchor 1.0\n')
        assert p.box(NATURAL, SCREEN)[1] == 400.0

    def test_yalign_one_puts_the_bottom_at_screen_bottom(self):
        p = _place('transform t:\n    yalign 1.0\n')
        assert p.box(NATURAL, SCREEN)[1] == pytest.approx(480.0)

    def test_ycenter_centers_the_image(self):
        p = _place('transform t:\n    ycenter 540\n')
        assert p.box(NATURAL, SCREEN)[1] == 240.0


class TestZoom:
    def test_zoom_scales_both_axes(self):
        p = _place('transform t:\n    zoom 0.5\n')
        assert p.box(NATURAL, SCREEN)[2:] == (200.0, 300.0)

    def test_xzoom_and_yzoom_are_independent(self):
        p = _place('transform t:\n    xzoom 0.5\n    yzoom 2.0\n')
        assert p.box(NATURAL, SCREEN)[2:] == (200.0, 1200.0)

    def test_anchor_scales_with_the_image(self):
        p = _place('transform t:\n    yalign 1.0\n    zoom 0.5\n')
        assert p.box(NATURAL, SCREEN)[1] == pytest.approx(1080 - 300)


class TestBuiltins:
    def test_left_is_flush_left_not_twenty_percent(self):
        assert placement_of('left', None).box(NATURAL, SCREEN)[0] == 0.0

    def test_right_is_flush_right(self):
        assert placement_of('right', None).box(NATURAL, SCREEN)[0] == \
            pytest.approx(1520.0)

    def test_truecenter_is_center_both_axes(self):
        p = placement_of('truecenter', None)
        left, top, _w, _h = p.box(NATURAL, SCREEN)
        assert left == pytest.approx(760.0)
        assert top == pytest.approx(240.0)

    @pytest.mark.parametrize('name', ['left', 'right', 'center'])
    def test_standing_positions_put_feet_on_the_screen_bottom(self, name):
        """SDK 的 `transform center: xpos .5 xanchor .5 ypos 1.0 yanchor 1.0`。"""
        assert placement_of(name, None).box(NATURAL, SCREEN)[1] == \
            pytest.approx(1080 - 600)

    def test_top_is_horizontally_centered_and_flush_top(self):
        left, top, _w, _h = placement_of('top', None).box(NATURAL, SCREEN)
        assert left == pytest.approx(760.0)
        assert top == 0.0

    def test_offscreenleft_is_fully_outside_the_left_edge(self):
        """`xpos 0 xanchor 1` → left = -width；旧实现给的是 -width/2。"""
        assert placement_of('offscreenleft', None).box(NATURAL, SCREEN)[0] == \
            pytest.approx(-400.0)

    def test_offscreenright_starts_at_the_right_edge(self):
        assert placement_of('offscreenright', None).box(NATURAL, SCREEN)[0] == \
            pytest.approx(1920.0)

    def test_no_at_is_the_default_transform(self):
        """
        `show x`（没有 at）→ `config.default_transform`，即中心 + 贴底。

        真机实测：720 高的窗口里 700 高的立绘顶边在 20px（2026-10-20）。
        """
        assert placement_of('', None).box(NATURAL, SCREEN) == \
            (760.0, 480.0, 400.0, 600.0)

    def test_a_transform_that_only_sets_x_does_not_inherit_the_default(self):
        """
        自带 transform **不**继承 default 的贴底——只设 `xalign` 时 y 仍是 0。

        真机实测（2026-10-20）：`transform sg_x: xalign 0.5` 的立绘顶边 0px，
        而 `show` 不带 at 时顶边 20px。两者在引擎里确实不同。
        """
        p = _place('transform t:\n    xalign 0.5\n')
        assert p.box(NATURAL, SCREEN)[1] == 0.0


class TestUncertain:
    def test_function_is_the_only_guess(self):
        p = _place('transform t:\n    function foo\n')
        assert not p.from_atl
        assert '含 function' in p.uncertain

    def test_repeat_keeps_the_last_computable_state_and_says_so(self):
        p = _place('transform t:\n    xpos 100\n    repeat\n')
        assert p.box(NATURAL, SCREEN)[0] == 100.0
        assert 'repeat' in p.uncertain
