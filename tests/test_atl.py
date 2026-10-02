"""
tests/test_atl.py
=================
**transform → 舞台真实位置**（`storygraph.story.atl`）的回归。

用户 2026-10-05：

> 演出的预览界面可能仍旧有点问题……第二个是……我觉得这个 transform 未对应到
> 舞台真实位置

原来只会在资源表那一行里找 `xpos` 字面量，找不到就按名字首字母猜，
而真实项目的写法是**链式调用**：

    transform f21:
        focus(320)
    transform focus(x=640, z=0.30):
        xcenter x xoffset 400 yoffset 1200 yanchor 1.0 ypos 1.03 zoom z*1.00

所以 `f21` / `f22` 都落到"首字母 f → 0.65"，两个人叠在一起。这个文件钉住
解析与求值的每一条规则（含 Ren'Py 的"整数=像素、小数=比例"）。
"""

from __future__ import annotations

import pytest

from storygraph.story.atl import (DEFAULT_SCREEN, eval_number, parse_transforms,
                                  placement_of, project_screen, scan_transforms)

#: 真实项目里的那几行（`sr_general/game/transform.rpy`）
REAL_SNIPPET = '''
transform focus(x=640, z=0.30):
    xcenter x xoffset 400 yoffset 1200 yanchor 1.0 ypos 1.03 zoom z*1.00 alpha 1.00 subpixel True
    #yanchor 1.0 ypos 1.03 subpixel True
    on show:
        zoom z*0.95 alpha 0.00
        xcenter x xoffset 400 yoffset 1200
        easein .25 yoffset 1200 zoom z*1 alpha 1.00
    on replace:
        alpha 1.00

transform tinstant(x=640, z=0.30):
    xcenter x xoffset 400 yoffset 1200 zoom z*1.00  yanchor 1.0 ypos 1.03
    alpha 0.5
    easein .25 alpha 1

transform f21:
    focus(320)

transform f22:
    focus(960)

transform leftin(x=640, z=0.30):
    xcenter -300 xoffset 400 yoffset 1200 yanchor 1.0 ypos 1.03 zoom z*1.00 alpha 1.00 subpixel True
    easein .25 xcenter x

transform l11:
    leftin(640)

transform onlyzoom:
    zoom 0.5
'''


class TestParseTransforms:
    def test_finds_definitions_with_params_and_defaults(self):
        defs = parse_transforms(REAL_SNIPPET)
        assert set(defs) >= {'focus', 'tinstant', 'f21', 'f22', 'leftin',
                             'l11', 'onlyzoom'}
        focus = defs['focus']
        assert focus.params == ['x', 'z']
        assert focus.defaults == {'x': '640', 'z': '0.30'}

    def test_event_blocks_are_not_part_of_the_body(self):
        """
        `on show:` / `on replace:` 里的行**不算**顶层语句。

        否则"最终静止态"会被动画中间帧覆盖（`zoom z*0.95 alpha 0.00`），
        位置/大小就全错了。
        """
        body = parse_transforms(REAL_SNIPPET)['focus'].body
        assert body and 'on show' not in ' '.join(body)
        assert all('alpha 0.00' not in line for line in body)
        assert len(body) == 1, body          # 只剩那一条静态行

    def test_comments_are_stripped(self):
        body = parse_transforms(REAL_SNIPPET)['focus'].body
        assert all('#' not in line for line in body)

    def test_chained_call_is_the_only_body_line(self):
        assert parse_transforms(REAL_SNIPPET)['f21'].body == ['focus(320)']


class TestEvalNumber:
    def test_simple_expressions(self):
        assert eval_number('z*1.00', {'z': 0.30}) == pytest.approx(0.30)
        assert eval_number('-300', {}) == -300
        assert eval_number('1.03', {}) == pytest.approx(1.03)
        assert eval_number('z * 1.2', {'z': 0.30}) == pytest.approx(0.36)

    def test_unknown_names_and_garbage(self):
        assert eval_number('x', {}) is None          # 名字没绑上
        assert eval_number('', {}) is None
        assert eval_number('hello world', {}) is None

    def test_arbitrary_code_is_refused(self):
        """项目源码是**数据**，不许当代码跑（白名单求值）。"""
        for bad in ("__import__('os').getcwd()", 'open("x")', '[1,2][0]',
                    'lambda: 1', '1 if 1 else 2'):
            assert eval_number(bad, {}) is None, bad


class TestPlacementRealChain:
    """`f21 → focus(320)`：手算 320 + 400 = 720 px → 720/1920 = 0.375。"""

    @pytest.fixture
    def index(self):
        return parse_transforms(REAL_SNIPPET)

    def test_chained_call_resolves_to_real_pixels(self, index):
        p = placement_of('f21', index, screen=(1920, 1080))
        assert p.from_atl
        assert p.x_kind == 'center' and p.x_px == pytest.approx(320)
        assert p.xoffset == pytest.approx(400)
        assert p.x == pytest.approx(0.375)
        assert p.zoom == pytest.approx(0.30)

    def test_two_siblings_are_not_on_top_of_each_other(self, index):
        """原来 f21/f22 都算成 0.65（叠在一起）—— 现在必须分开。"""
        a = placement_of('f21', index, screen=(1920, 1080))
        b = placement_of('f22', index, screen=(1920, 1080))
        assert a.x == pytest.approx(0.375, abs=1e-3)
        assert b.x == pytest.approx(0.708, abs=1e-3)
        assert abs(a.x - b.x) > 0.2

    def test_animations_end_state_wins(self, index):
        """`leftin` 从屏幕外进来（xcenter -300），**停下来的位置**才是预览要的。"""
        p = placement_of('l11', index, screen=(1920, 1080))
        assert p.x_px == pytest.approx(640), f'拿到的是起始帧：{p.x_px}'
        assert p.x == pytest.approx((640 + 400) / 1920)

    def test_zoom_survives_the_chain(self, index):
        assert placement_of('tinstant', index, screen=(1920, 1080)).zoom == \
            pytest.approx(0.30)
        assert placement_of('onlyzoom', index, screen=(1920, 1080)).zoom == \
            pytest.approx(0.5)


class TestFractionVersusPixels:
    """
    Ren'Py 的位置属性**按写法**分辨：整数 = 像素，小数 = 屏幕比例。

    真实项目 `focus` 里就有 `ypos 1.03`（= 屏幕高的 103%）—— 当成像素就会
    把角色放到屏幕外，当成比例才对。
    """

    def test_float_ypos_is_a_fraction(self):
        index = parse_transforms(
            'transform a:\n    ypos 1.03\n    xpos 0.5\n')
        p = placement_of('a', index, screen=(1920, 1080))
        assert p.y_kind == 'align' and p.y_px == pytest.approx(1.03)
        assert p.x_kind == 'align' and p.x_px == pytest.approx(0.5)

    def test_int_pos_is_pixels(self):
        index = parse_transforms('transform b:\n    ypos 1200\n    xpos 300\n')
        p = placement_of('b', index, screen=(1920, 1080))
        assert p.y_kind == 'bottom' and p.y_px == pytest.approx(1200)
        assert p.x_kind == 'left' and p.x_px == pytest.approx(300)

    def test_chained_int_argument_stays_pixels(self):
        """
        `xcenter x` 里 x=320（整数）**必须**还是像素。

        链路绑定拿到的是 float（320.0），代回源码时若不还原成 `320`，
        就会被判成比例 —— 实测踩过这个坑（整片位置跑到屏幕外）。
        """
        index = parse_transforms(
            'transform base(x=640):\n    xcenter x\n'
            'transform kid:\n    base(320)\n')
        p = placement_of('kid', index, screen=(1920, 1080))
        assert p.x_kind == 'center', f'被当成比例了：{p.x_kind}'
        assert p.x_px == pytest.approx(320)


class TestFallbacks:
    def test_words_and_letters(self):
        assert placement_of('left', None).x < 0.5
        assert placement_of('right', None).x > 0.5
        assert placement_of('center', None).x == pytest.approx(0.5)
        assert placement_of('i11', None).x < 0.5
        assert placement_of('f11', None).x > 0.5
        assert placement_of('i11', None).source == 'letter'

    def test_unknown_is_centered_not_guessed(self):
        p = placement_of('zzz_whatever', None)
        assert p.x == pytest.approx(0.5) and p.source == 'center'
        assert not p.from_atl

    def test_registry_atl_is_still_honoured(self):
        """项目源码不在（或没这个名）时，退回资源表那一行的 `xpos`。"""
        class _Reg:
            transforms = {'mine': {'ATL': 'xpos 0.33'}}

        assert placement_of('mine', None, _Reg()).x == pytest.approx(0.33)


class TestProjectScreen:
    def test_reads_gui_init(self, tmp_path):
        (tmp_path / 'game').mkdir()
        (tmp_path / 'game' / 'gui.rpy').write_text(
            'gui.init(2560, 1440)\n', encoding='utf-8')
        assert project_screen(tmp_path) == (2560, 1440)

    def test_reads_config_screen_size(self, tmp_path):
        (tmp_path / 'game').mkdir()
        (tmp_path / 'game' / 'options.rpy').write_text(
            'define config.screen_width = 1280\n'
            'define config.screen_height = 720\n', encoding='utf-8')
        assert project_screen(tmp_path) == (1280, 720)

    def test_defaults_when_missing(self, tmp_path):
        assert project_screen(tmp_path) == DEFAULT_SCREEN
        assert project_screen(None) == DEFAULT_SCREEN


class TestScanTransforms:
    def test_scans_game_folder(self, tmp_path):
        (tmp_path / 'game').mkdir()
        (tmp_path / 'game' / 'transform.rpy').write_text(
            'transform focus(x=640):\n    xcenter x\n', encoding='utf-8')
        (tmp_path / 'game' / 'chapter').mkdir()
        (tmp_path / 'game' / 'chapter' / 'c.rpy').write_text(
            'transform f21:\n    focus(320)\n', encoding='utf-8')
        index = scan_transforms(tmp_path)
        assert 'focus' in index and 'f21' in index
        p = placement_of('f21', index, screen=(1920, 1080))
        assert p.x == pytest.approx(320 / 1920)

    def test_missing_root_is_empty(self):
        assert scan_transforms(None) == {}
        assert scan_transforms('/definitely/not/here') == {}
