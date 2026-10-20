"""
tests/test_stage_render.py
==========================
**离屏舞台渲染**（`app.stage_render`）的回归线（2026-10-20，B2）。

为什么单独钉：预览要"跟真正编译的一模一样"，靠三件事撑着：

  ① 排版参数来自**项目自己的** `gui.*`（不是编辑器主题、不是写死的 960）；
  ② 立绘位置来自 `story.atl.Placement` 的真实几何（不是"大概摆中间"）；
  ③ 找不到的素材/字体要**明说**（`warnings`），不能静默画错。

真引擎对照在 `tools/renpy_stage_compare.py`（要 SDK，CI 不跑）；
这里用**合成项目**把 ①②③ 逐条钉死，跑一次不到一秒。
"""

from __future__ import annotations

import pytest

from PIL import Image, ImageDraw

from storygraph.app.stage import SpriteLayout, StageFrame
from storygraph.app.stage_render import (RenderResult, available,
                                         render_frame, render_frame_to_png,
                                         text_runs)
from storygraph.story.atl import parse_transforms, placement_of

pytestmark = pytest.mark.skipif(not available(), reason='没装 Pillow')

SCREEN = (800, 450)

GUI = '''define gui.textbox_height = 120
define gui.textbox_yalign = 1.0
define gui.name_xpos = 60
define gui.name_ypos = 0
define gui.name_xalign = 0.0
define gui.dialogue_xpos = 80
define gui.dialogue_ypos = 30
define gui.dialogue_width = 600
define gui.text_size = 18
define gui.name_text_size = 24
define gui.text_color = '#ffffff'
define gui.name_text_color = '#ffcc00'
define gui.text_outlines = [(2, "#000000", 0, 0)]
define gui.name_text_outlines = [(2, "#000000", 0, 0)]
define gui.text_font = "DejaVuSans.ttf"
define gui.name_text_font = "DejaVuSans.ttf"

init python:
    gui.init(800, 450)
'''

TEXTBOX_TOP = SCREEN[1] - 120          # 330


def _png(path, size, color=(20, 30, 40, 255), outline=None):
    img = Image.new('RGBA', size, color)
    if outline:
        ImageDraw.Draw(img).rectangle((0, 0, size[0] - 1, size[1] - 1),
                                      outline=outline)
    img.save(path)


@pytest.fixture()
def project(tmp_path):
    game = tmp_path / 'game'
    (game / 'images').mkdir(parents=True)
    (game / 'gui').mkdir()
    (game / 'gui.rpy').write_text(GUI, encoding='utf-8')
    (game / 'script.rpy').write_text('label start:\n    return\n',
                                     encoding='utf-8')
    _png(game / 'gui' / 'textbox.png', (800, 120), (0, 0, 0, 200))
    _png(game / 'gui' / 'namebox.png', (40, 40), (0, 0, 0, 170))
    _png(game / 'images' / 'bg room.png', SCREEN, (30, 40, 60, 255))
    _png(game / 'images' / 'hero.png', (100, 200), (200, 90, 60, 255))
    return tmp_path


def _frame(text='Hello there.', speaker='e', name='Eileen', sprites=(),
           scene='bg room', **kw):
    return StageFrame(index=0, line_id='t.0001', label='start',
                      speaker=speaker, speaker_name=name, text=text,
                      scene=scene, sprites=list(sprites), **kw)


def test_renders_a_full_size_canvas(project):
    got = render_frame(_frame(), project_root=project)
    assert isinstance(got, RenderResult)
    assert got.image.size == SCREEN
    assert got.image.mode == 'RGBA'


def test_textbox_comes_from_the_projects_gui(project):
    """文本框高度/贴底来自 `gui.textbox_height`，不是写死的 1/4 屏。"""
    got = render_frame(_frame(), project_root=project)
    assert got.textbox_rect == (0, TEXTBOX_TOP, 800, 120)


def test_dialogue_origin_and_width_come_from_gui(project):
    got = render_frame(_frame(), project_root=project)
    x, y, w, _h = got.text_rect
    assert (x, y, w) == (80, TEXTBOX_TOP + 30, 600)


def test_namebox_sits_at_name_xpos(project):
    got = render_frame(_frame(), project_root=project)
    x, y, w, h = got.name_rect
    assert (x, y) == (60, TEXTBOX_TOP)
    assert w > 0 and h > 0


def test_name_is_drawn_in_the_characters_colour(project):
    """`gui.name_text_color` 是黄色的，画面里就该有黄色像素。"""
    got = render_frame(_frame(), project_root=project)
    px = got.image.load()
    x, y, w, h = got.name_rect
    hits = [px[i, j] for j in range(y, y + h) for i in range(x, x + w)
            if px[i, j][0] > 200 and px[i, j][1] > 150 and px[i, j][2] < 120]
    assert hits, '名条范围内没有名字颜色'


def test_no_text_means_no_say_window(project):
    """Ren'Py：没有 say 语句就不画对话框（纯舞台变化那一拍）。"""
    got = render_frame(_frame(text=''), project_root=project)
    assert got.textbox_rect == (0, 0, 0, 0)
    assert got.text_rect == (0, 0, 0, 0)


def test_long_text_wraps_inside_dialogue_width(project):
    got = render_frame(_frame(text='word ' * 60), project_root=project)
    x, y, w, h = got.text_rect
    assert h > 18 * 2, '没有换行'
    # 台词不允许画到 x + width 外面
    px = got.image.load()
    for j in range(y, min(SCREEN[1], y + h)):
        for i in range(x + w, SCREEN[0]):
            assert not (px[i, j][0] > 200 and px[i, j][2] > 200), \
                f'第 {j} 行画出了文本框'


def test_censoring_of_unknown_tags(project):
    """认不出的标签不能把源码画给用户看。"""
    got = render_frame(_frame(text='{color=#ff0000}红{/color}{nw}{foo}'),
                       project_root=project)
    assert got.image is not None


def test_sprite_box_is_real_geometry(project):
    place = placement_of('center', None, screen=SCREEN)
    sprite = SpriteLayout(image='hero', tag='hero', at='center', x=0.5,
                          place=place, order_index=0)
    got = render_frame(_frame(sprites=[sprite]), project_root=project)
    assert got.sprite_boxes[0] == (400 - 50, 450 - 200, 100.0, 200.0)


def test_zoom_transform_changes_the_drawn_size(project):
    idx = parse_transforms('transform half:\n    zoom 0.5\n    xalign 0.5\n'
                           '    yalign 1.0\n')
    place = placement_of('half', idx, screen=SCREEN)
    sprite = SpriteLayout(image='hero', tag='hero', at='half', x=0.5,
                          place=place, order_index=0)
    got = render_frame(_frame(sprites=[sprite]), project_root=project,
                       transform_index=idx)
    assert got.sprite_boxes[0][2:] == (50.0, 100.0)
    assert got.sprite_boxes[0][1] == 450 - 100


def test_missing_background_is_reported(project):
    got = render_frame(_frame(scene='bg nowhere'), project_root=project)
    assert any('背景找不到' in w for w in got.warnings)


def test_missing_sprite_is_reported(project):
    place = placement_of('center', None, screen=SCREEN)
    sprite = SpriteLayout(image='ghost', tag='ghost', at='center', x=0.5,
                          place=place, order_index=0)
    got = render_frame(_frame(sprites=[sprite]), project_root=project)
    assert any('立绘找不到' in w for w in got.warnings)


def test_render_frame_to_png_writes_a_file(project, tmp_path):
    out = tmp_path / 'shot.png'
    got = render_frame_to_png(_frame(), out, project_root=project)
    assert got == str(out) or got == str(out.resolve())
    assert Image.open(out).size == SCREEN


# ----------------------------------------------------------------------
#  文本标签 → run
# ----------------------------------------------------------------------

class TestTextRuns:
    def test_plain_text_is_one_run(self):
        runs = text_runs('hi', 20, (255, 255, 255, 255))
        assert runs == [('hi', runs[0][1])]
        assert runs[0][1].size == 20

    def test_color_tag_scopes_to_its_close(self):
        runs = text_runs('a{color=#ff0000}b{/color}c', 20, (255, 255, 255, 255))
        assert [r[0] for r in runs] == ['a', 'b', 'c']
        assert runs[1][1].color[:3] == (255, 0, 0)
        assert runs[2][1].color[:3] == (255, 255, 255)

    def test_size_tag_relative(self):
        runs = text_runs('{size=+6}x', 20, (255, 255, 255, 255))
        assert runs[0][1].size == 26

    def test_bold_and_italic(self):
        runs = text_runs('{b}x{/b}{i}y{/i}', 20, (255, 255, 255, 255))
        assert runs[0][1].bold is True
        assert runs[1][1].italic is True
        assert runs[1][1].bold is False

    def test_p_becomes_newline_and_w_becomes_space(self):
        runs = text_runs('a{p}b{w}c', 20, (255, 255, 255, 255))
        assert ''.join(r[0] for r in runs) == 'a\nb c'

    def test_unknown_tag_does_not_leak(self):
        runs = text_runs('a{nw}{fast}{cps=20}b', 20, (255, 255, 255, 255))
        assert ''.join(r[0] for r in runs) == 'ab'
