"""
tests/test_stage_fidelity_golden.py
===================================
**把真机量出来的数字钉成回归线**（2026-10-20，B2）。

`tools/renpy_stage_compare.py` 会开真 Ren'Py 引擎截图、逐项对账（要 SDK，
CI 不跑）。它量出来的值就是这个文件里的"金标准"：

    the_question（1280×720，gui.textbox_height=185，name_xpos=240，
    dialogue_xpos=268 / ypos=50 / width=744，text_size=22，name_text_size=30）

    · 立绘 `at center`（334×700 的图）→ 框 (473, 20, 334, 700)
    · 名条 → x=240, y=535（文本框顶）
    · 名字文字 → 起点 (247, 545)
    · 台词文字 → 起点 (270, 589)
    · 改 gui 后（文本框 260 / 名条 600 / 台词 620,60,560）→ 全部跟着走

这里用**合成项目**复刻同一套 gui 数值（不依赖 SDK 素材），把上面这些
位置写成断言。字体用引擎会用的那一份（SDK 的 DejaVuSans）；机器上找不到
就跳过 —— 和 `test_renpy_gui_profile.py` 里的真实项目用例同一套办法。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from PIL import Image, ImageDraw

from storygraph.app.stage import SpriteLayout, StageFrame
from storygraph.app.stage_render import available, render_frame
from storygraph.story.atl import placement_of
from storygraph.story.renpy_gui import _font_candidates, load_gui_profile

pytestmark = pytest.mark.skipif(not available(), reason='没装 Pillow')

SCREEN = (1280, 720)

#: `the_question/game/gui.rpy` 里真正生效的那几条（桌面那套）。
THE_QUESTION_GUI = '''init python:
    gui.init(1280, 720)

define gui.textbox_height = 185
define gui.name_xpos = 240
define gui.name_ypos = 0
define gui.name_xalign = 0.0
define gui.dialogue_xpos = 268
define gui.dialogue_ypos = 50
define gui.dialogue_width = 744
define gui.text_size = 22
define gui.name_text_size = 30
define gui.text_color = '#ffffff'
define gui.name_text_color = '#c8ffc8'
define gui.text_outlines = [(2, "#000000", 0, 0)]
define gui.text_font = "DejaVuSans.ttf"
define gui.name_text_font = "DejaVuSans.ttf"
define gui.namebox_borders = Borders(5, 5, 5, 5)
'''


def _png(path, size, color=(0, 0, 0, 255)):
    Image.new('RGBA', size, color).save(path)


@pytest.fixture(scope='module')
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp('golden') / 'proj'
    game = root / 'game'
    (game / 'images').mkdir(parents=True)
    (game / 'gui').mkdir()
    (game / 'gui.rpy').write_text(THE_QUESTION_GUI, encoding='utf-8')
    (game / 'script.rpy').write_text('label start:\n    return\n',
                                     encoding='utf-8')
    _png(game / 'images' / 'bg room.png', SCREEN, (10, 20, 30, 255))
    _png(game / 'images' / 'sylvie.png', (334, 700), (200, 120, 60, 255))
    _png(game / 'gui' / 'textbox.png', (1280, 185), (0, 0, 0, 170))
    _png(game / 'gui' / 'namebox.png', (40, 40), (0, 0, 0, 120))
    return root


@pytest.fixture(scope='module')
def profile(project):
    p = load_gui_profile(project, force=True)
    if not p.text_font_path:
        pytest.skip('没有可用字体，量不出字宽')
    return p


def _frame(text='Hello from the engine.', **kw):
    place = placement_of('center', None, screen=SCREEN)
    sprite = SpriteLayout(image='sylvie', tag='sylvie', at='center',
                          x=0.5, place=place, order_index=0)
    return StageFrame(index=0, line_id='g.0001', label='start', speaker='s',
                      speaker_name='Sylvie', speaker_color='#c8ffc8',
                      text=text, scene='bg room', sprites=[sprite], **kw)


class TestTheQuestionNumbers:
    """引擎实测值（`report.txt`）→ 断言。"""

    def test_textbox_is_the_project_height_at_the_bottom(self, project):
        got = render_frame(_frame(), project_root=project)
        assert got.textbox_rect == (0, 535, 1280, 185)

    def test_sprite_box_matches_the_engine(self, project):
        got = render_frame(_frame(), project_root=project)
        assert got.sprite_boxes[0] == (473.0, 20.0, 334.0, 700.0)

    def test_name_box_starts_at_name_xpos(self, project):
        got = render_frame(_frame(), project_root=project)
        assert got.name_rect[:2] == (240, 535)

    def test_dialogue_origin_matches_the_engine(self, project):
        got = render_frame(_frame(), project_root=project)
        assert got.text_rect[:3] == (268, 585, 744)

    def test_first_glyph_lands_where_the_engine_puts_it(self, profile,
                                                       project):
        """
        引擎里名字的绿字从 x=247、y=545 起；台词的第一个像素在 (270, 589)。

        字宽会因为"引擎在 2x 下渲染再缩回"差几个 px，所以只钉**起点**和
        大致高度。
        """
        got = render_frame(_frame(), project_root=project)
        px = got.image.load()
        name_xs, name_ys, say_xs, say_ys = [], [], [], []
        for y in range(535, 600):
            for x in range(200, 500):
                r, g, b = px[x, y][:3]
                if g > 150 and g - r > 20 and g - b > 20:
                    name_xs.append(x)
                    name_ys.append(y)
        for y in range(585, 620):
            for x in range(240, 1100):
                r, g, b = px[x, y][:3]
                if r > 200 and g > 200 and b > 200:
                    say_xs.append(x)
                    say_ys.append(y)
        # 引擎量到的是 (247, 545) / (270, 589)；允许 1px 的取整差
        # （引擎在 2x 下排版再缩回，字形的第一个像素会差一行）。
        assert 246 <= min(name_xs) <= 248
        assert 544 <= min(name_ys) <= 546
        assert 269 <= min(say_xs) <= 271
        assert 588 <= min(say_ys) <= 590


class TestCustomGuiFollowsTheProject:
    """同一帧、换一套 gui：所有位置都必须跟着走（不能写死）。"""

    GUI = '''init python:
    gui.init(1280, 720)

define gui.textbox_height = 260
define gui.name_xpos = 600
define gui.dialogue_xpos = 620
define gui.dialogue_ypos = 60
define gui.dialogue_width = 560
define gui.text_size = 28
define gui.name_text_size = 34
define gui.text_font = "DejaVuSans.ttf"
define gui.name_text_font = "DejaVuSans.ttf"
'''

    @pytest.fixture()
    def custom(self, tmp_path):
        root = tmp_path / 'custom'
        game = root / 'game'
        (game / 'images').mkdir(parents=True)
        (game / 'gui').mkdir()
        (game / 'gui.rpy').write_text(self.GUI, encoding='utf-8')
        (game / 'script.rpy').write_text('label start:\n    return\n',
                                         encoding='utf-8')
        _png(game / 'images' / 'bg room.png', SCREEN)
        _png(game / 'images' / 'sylvie.png', (334, 700))
        _png(game / 'gui' / 'textbox.png', (1280, 185), (0, 0, 0, 170))
        _png(game / 'gui' / 'namebox.png', (40, 40), (0, 0, 0, 120))
        return root

    def test_geometry_tracks_the_new_gui(self, custom):
        place = placement_of('left', None, screen=SCREEN)
        sprite = SpriteLayout(image='sylvie', tag='sylvie', at='left',
                              x=0.0, place=place, order_index=0)
        frame = StageFrame(index=0, line_id='c', label='start', speaker='s',
                           speaker_name='Sylvie', text='Hello.',
                           scene='bg room', sprites=[sprite])
        got = render_frame(frame, project_root=custom)
        assert got.textbox_rect == (0, 460, 1280, 260)
        assert got.name_rect[:2] == (600, 460)
        assert got.text_rect[:3] == (620, 520, 560)
        assert got.sprite_boxes[0] == (0.0, 20.0, 334.0, 700.0)


class TestFontIsTheEnginesFont:
    def test_project_font_resolution_matches_the_engine(self, project):
        """the_question 不自带 DejaVu：解析结果必须是**同名的**那个文件。"""
        p = load_gui_profile(project, force=True)
        if not p.text_font_path:
            pytest.skip('本机没有 DejaVuSans')
        assert Path(p.text_font_path).name.lower() == 'dejavusans.ttf'
        assert p.fonts_substituted == []

    def test_candidates_prefer_sdk_common(self, project):
        cands = _font_candidates(project, 'DejaVuSans.ttf')
        if not cands:
            pytest.skip('本机没有 DejaVuSans')
        assert 'renpy' in Path(cands[0]).parts or \
            Path(cands[0]).name.lower() == 'dejavusans.ttf'
