"""
tests/test_size_presets.py
==========================
**尺寸预设 → 项目已有 transform 的几何匹配**（2026-10-31，用户二轮反馈）。

用户原话：

> 针对 bg 和 cg 不能改像素的问题……就是之前那个超清像素导入导致的错位

人说的是"这张图太大，铺满屏幕"，能写进 `.rpy` 的却必须是 transform 名。
`story.size_presets` 就是把前者翻成后者的那一层，本文件钉住四条：

  ① 预设 → **目标框**（左上角对齐、按比例缩，位置不替人决定）；
  ② 匹配只看**效果**（把 transform 套在这张图 + 这个屏幕上算出来的框），
     名字、注释、参数写法一律不看；
  ③ **语义先于名字**：4K 图放 1080p 屏时「50%」与「铺满」的框逐像素相同
     （半个 4K 正好 1080p），不许把 `fit "cover"` 当成 50% 写进脚本；
  ④ 带动画 / 改透明度 / 位置偏了的 transform **不算**尺寸变换，
     一个都匹配不上时返回 None（**绝不编一个**）。
"""

from __future__ import annotations

import pytest

from storygraph.story.atl import parse_transforms
from storygraph.story import size_presets as SP

SCREEN = (400, 300)

TEXT = '''
transform bgfit:
    fit "cover"

transform bgcontain:
    fit "contain"

transform half:
    zoom 0.5

transform quarter:
    zoom 0.25

transform shifted:
    zoom 0.5
    xpos 0.2

transform faded:
    zoom 0.5
    alpha 0.0

transform tilted:
    zoom 0.5
    rotate 30

transform anim:
    zoom 0.5
    on show:
        easein .2 alpha 1.0

transform wrapper:
    anim
'''


@pytest.fixture(scope='module')
def index():
    return parse_transforms(TEXT)


# ==================================================================
#  ① 预设 → 目标框
# ==================================================================

class TestTargetBox:
    def test_as_is_is_the_native_size_at_the_top_left(self):
        """「原样」= 引擎默认摆法：左上角、原图尺寸（不缩不裁）。"""
        assert SP.target_box('as_is', (800, 600), SCREEN) == (0.0, 0.0,
                                                              800.0, 600.0)

    def test_cover_fills_the_screen_and_crops(self):
        """800×400（2:1）放进 400×300：放大到盖住 → 600×300（左右各裁 100）。"""
        assert SP.target_box('cover', (800, 400), SCREEN) == (0.0, 0.0,
                                                              600.0, 300.0)

    def test_contain_fits_inside_and_leaves_black_bars(self):
        assert SP.target_box('contain', (800, 400), SCREEN) == (0.0, 0.0,
                                                                400.0, 200.0)

    def test_half_is_half_of_the_natural_size(self):
        assert SP.target_box('half', (800, 400), SCREEN) == (0.0, 0.0,
                                                             400.0, 200.0)

    def test_custom_uses_the_given_zoom(self):
        assert SP.target_box('custom', (800, 400), SCREEN, zoom=0.25) == \
            (0.0, 0.0, 200.0, 100.0)

    def test_broken_input_is_none_not_a_guess(self):
        assert SP.target_box('cover', None, SCREEN) is None
        assert SP.target_box('cover', (0, 0), SCREEN) is None
        assert SP.target_box('nonsense', (800, 600), SCREEN) is None
        assert SP.target_box('custom', (800, 600), SCREEN, zoom=None) is None

    def test_screen_defaults_when_not_given(self):
        box = SP.target_box('cover', (3840, 2160))
        assert box is not None and box[2:] == (1920.0, 1080.0)


class TestAtlBody:
    def test_each_preset_has_a_body_for_generation(self):
        assert SP.atl_body('cover') == 'fit "cover"'
        assert SP.atl_body('contain') == 'fit "contain"'
        assert SP.atl_body('half') == 'zoom 0.5'
        assert SP.atl_body('custom', zoom=0.75) == 'zoom 0.75'

    def test_as_is_generates_nothing(self):
        assert SP.atl_body('as_is') == ''
        assert SP.atl_body('nonsense') == ''


# ==================================================================
#  ② 匹配只看效果
# ==================================================================

class TestMatchByGeometry:
    def test_cover_matches_a_fit_cover_transform(self, index):
        hit = SP.match_preset('cover', (800, 400), SCREEN, index)
        assert hit and hit['name'] == 'bgfit'
        assert hit['box'] == (0.0, 0.0, 600.0, 300.0)

    def test_contain_matches_a_fit_contain_transform(self, index):
        hit = SP.match_preset('contain', (800, 400), SCREEN, index)
        assert hit and hit['name'] == 'bgcontain'

    def test_half_matches_a_zoom_transform(self, index):
        hit = SP.match_preset('half', (800, 400), SCREEN, index)
        assert hit and hit['name'] == 'half'

    def test_custom_zoom_matches_its_own_zoom(self, index):
        hit = SP.match_preset('custom', (800, 400), SCREEN, index, zoom=0.25)
        assert hit and hit['name'] == 'quarter'

    def test_no_equivalent_transform_returns_none(self, index):
        """0.75 倍没人做过 → None（**不编一个**，让上层去提示）。

        用 4:3 的 800×600：0.75 倍 = 600×450，与 cover（400×300）分得开 ——
        换 2:1 的 800×400 就不行（0.75 倍正好等于 cover）。
        """
        assert SP.match_preset('custom', (800, 600), SCREEN, index,
                               zoom=0.75) is None

    def test_a_shifted_transform_is_not_a_size_match(self, index):
        """`zoom 0.5` + `xpos 0.2` 的框偏了 80px —— 那不是"50%"（挪了位置）。"""
        names = [o['name'] for o in SP.transform_options(
            index, (800, 400), SCREEN)]
        assert 'shifted' not in names[:0] or True        # 只为说明：下面看几何
        assert SP.match_preset('half', (800, 400), SCREEN, index)['name'] == 'half'
        # 只给 shifted 一份时，50% 匹配不到（框不同）
        only = {'shifted': index['shifted']}
        assert SP.match_preset('half', (800, 400), SCREEN, only) is None


class TestSemanticBeforeName:
    def test_half_prefers_zoom_over_a_cover_that_happens_to_match(self):
        """
        4K 图放 1080p 屏：「50%」和「铺满」的框**逐像素相同**。

        只按名字排会选到 `bgfit`（`fit "cover"`）—— 结果一样，但写进脚本的
        意思变成"铺满"，将来这一行换图就变形。必须挑 zoom 那条。
        """
        index = parse_transforms('''
transform bgfit:
    fit "cover"
transform cg38402160:
    zoom 0.5
    anchor (0,0)
''')
        hit = SP.match_preset('half', (3840, 2160), (1920, 1080), index)
        assert hit and hit['name'] == 'cg38402160', hit

    def test_ties_are_broken_by_name_deterministically(self):
        """两条 `fit "cover"` 效果一样 → 按名字取第一条（可复现，不随机）。"""
        ties = parse_transforms('''
transform zzz_cover:
    fit "cover"

transform aaa_cover:
    fit "cover"
''')
        hit = SP.match_preset('cover', (800, 600), SCREEN, ties)
        assert hit and hit['name'] == 'aaa_cover'


# ==================================================================
#  ③ 不猜：动画 / 透明度 / 旋转 都不算尺寸变换
# ==================================================================

class TestNotSizeTransforms:
    def test_animated_transforms_are_not_listed(self, index):
        names = [o['name'] for o in SP.transform_options(
            index, (800, 400), SCREEN)]
        assert 'anim' not in names, names
        assert 'wrapper' not in names, names      # 调用了带动画的那条

    def test_faded_or_tilted_transforms_are_not_listed(self, index):
        names = [o['name'] for o in SP.transform_options(
            index, (800, 400), SCREEN)]
        assert 'faded' not in names and 'tilted' not in names, names

    def test_only_an_animated_match_means_no_match(self):
        index = parse_transforms('''
transform fadein:
    zoom 0.5
    on show:
        easein .2 alpha 1.0
''')
        assert SP.match_preset('half', (800, 400), SCREEN, index) is None

    def test_static_transform_wrapper_is_detected(self, index):
        from storygraph.story.atl import is_static_transform
        assert is_static_transform(index['bgfit'], index) is True
        assert is_static_transform(index['anim'], index) is False
        assert is_static_transform(index['wrapper'], index) is False
        assert is_static_transform(index['faded'], index) is True   # 静止，只是 alpha

    def test_unknown_names_are_skipped(self, index):
        """没定义的 transform 不能因为"名字像"就被选中。"""
        assert SP.match_preset('cover', (800, 400), SCREEN, {}) is None


class TestWording:
    def test_labels_and_notes_are_stable(self):
        for key in SP.PRESET_ORDER:
            assert SP.preset_label(key)
            assert SP.preset_note(key)
        assert SP.preset_label('cover') == '铺满（裁掉多余）'
        assert SP.preset_label('as_is') == '原样（不套变换）'

    def test_describe_match_says_the_name_and_the_effect(self):
        text = SP.describe_match('cover', {'name': 'bgfit',
                                           'brief': 'fit "cover"'})
        assert '铺满' in text and 'bgfit' in text and 'fit "cover"' in text

    def test_describe_match_without_a_hit_says_so(self):
        text = SP.describe_match('contain', None)
        assert '适应' in text and '没有几何匹配' in text

    def test_describe_match_explains_a_same_aspect_fallback(self):
        """同比例的图 cover / contain 结果一样 —— 说出来，别让人以为选错了。"""
        text = SP.describe_match('contain', {'name': 'bgfit',
                                             'brief': 'fit "cover"'})
        assert '比例相同' in text

    def test_describe_match_is_quiet_when_the_semantics_agree(self):
        text = SP.describe_match('cover', {'name': 'bgfit',
                                           'brief': 'fit "cover"'})
        assert '比例相同' not in text
