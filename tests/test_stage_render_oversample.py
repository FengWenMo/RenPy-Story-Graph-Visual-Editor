"""
tests/test_stage_render_oversample.py
=====================================
**超采样（oversampling）**：Ren'Py 的官方"超清图"解法（2026-10-31）。

用户指出的官方文档原话：

> if an image is 3480x2160 and has an oversample of 2, then each axis will be
> halved, and the image would fill the same 1920x1080 window.
> Oversampling is automatically enabled if the image ends with an @ followed by
> a number: eileen happy@2.png is 2x oversampled.

工具从前**完全不认它**（全仓 grep 只有无关的 `@2` 注释）：一张 `xxx@2.png`
的 4K 图会被按 3840×2160 画出来 —— 预览比游戏大 N 倍，看起来正好是"超清像素
导入导致的错位"。这里用像素采样钉住：

  · `@2` → 引擎显示尺寸 = 文件尺寸 / 2；
  · 没有 `@N` → 一律按原像素（老行为逐位不变）；
  · 尺寸提示行要说清"文件 4K @2（引擎按 1080p 画）"，否则人照着 4K 去调。

⚠ 只认**文件名**（Ren'Py 就是这么判的），不改任何图片字节、不自动改名。
"""

from __future__ import annotations

import pytest

from PIL import Image, ImageDraw

from storygraph.app.stage import StageFrame
from storygraph.app.stage_render import (available, display_size,
                                         oversample_of, render_frame,
                                         scale_notes)

pytestmark = pytest.mark.skipif(not available(), reason='没装 Pillow')

SCREEN = (400, 300)
RED = (255, 0, 0, 255)
GREEN = (0, 255, 0, 255)
BLUE = (0, 0, 255, 255)
YELLOW = (255, 255, 0, 255)

GUI = '''init python:
    gui.init(400, 300)

define gui.textbox_height = 90
define gui.textbox_yalign = 1.0
define gui.text_size = 18
define gui.name_text_size = 20
'''

TRANSFORMS = '''transform bgfit:
    fit "cover"
'''


def _quad(path, size):
    """四象限测试图（左上红/右上绿/左下蓝/右下黄）。"""
    w, h = size
    img = Image.new('RGBA', size, RED)
    draw = ImageDraw.Draw(img)
    draw.rectangle((w // 2, 0, w - 1, h // 2 - 1), fill=GREEN)
    draw.rectangle((0, h // 2, w // 2 - 1, h - 1), fill=BLUE)
    draw.rectangle((w // 2, h // 2, w - 1, h - 1), fill=YELLOW)
    img.save(path)


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / 'proj'
    game = root / 'game'
    (game / 'images').mkdir(parents=True)
    (game / 'gui.rpy').write_text(GUI, encoding='utf-8')
    (game / 'transform.rpy').write_text(TRANSFORMS, encoding='utf-8')
    (game / 'script.rpy').write_text(
        'image bg big = "images/big@2.png"\n'      # 超采样：显示 400×300
        'image bg plain = "images/plain.png"\n'    # 同尺寸、不带 @2：显示 800×600
        'label start:\n    return\n', encoding='utf-8')
    _quad(game / 'images' / 'big@2.png', (800, 600))
    _quad(game / 'images' / 'plain.png', (800, 600))
    return root


def _frame(**kw):
    kw.setdefault('scene', '')
    kw.setdefault('scene_at', '')
    kw.setdefault('layers', '')
    kw.setdefault('layers_at', {})
    return StageFrame(index=0, line_id='t.0001', label='t', speaker='',
                      text='', **kw)


class TestOversampleDetection:
    def test_number_suffix_is_the_factor(self):
        assert oversample_of('a/b/eileen happy@2.png') == 2
        assert oversample_of('x@3.webp') == 3

    def test_no_suffix_is_one(self):
        assert oversample_of('x.png') == 1
        assert oversample_of('a@b.png') == 1        # @ 后面不是数字 → 不认
        assert oversample_of('x@0.png') == 1        # 0 倍不是合法超采样
        assert oversample_of('') == 1
        assert oversample_of(None) == 1

    def test_display_size_divides_both_axes(self):
        assert display_size('x@2.png', (3840, 2160)) == (1920, 1080)
        assert display_size('x.png', (3840, 2160)) == (3840, 2160)
        assert display_size('x@2.png', (999, 555)) == (500, 278)


class TestRenderUsesDisplaySize:
    def test_oversampled_background_fills_the_screen_at_one_to_one(self, project):
        """800×600 @2 → 引擎显示 400×300 = 正好整屏（不是裁切）。"""
        got = render_frame(_frame(scene='bg big'), project_root=project)
        assert got is not None and got.image is not None
        assert got.background_box == (0.0, 0.0, 400.0, 300.0)
        px = got.image.load()
        assert px[0, 0] == RED
        assert px[399, 299] == YELLOW
        assert px[399, 0] == GREEN

    def test_the_same_file_without_suffix_is_cropped(self, project):
        """同一个 800×600，名字不带 @2 → 按原像素画，只露左上 1/4（老行为）。"""
        got = render_frame(_frame(scene='bg plain'), project_root=project)
        assert got is not None and got.image is not None
        assert got.background_box == (0.0, 0.0, 800.0, 600.0)
        px = got.image.load()
        assert px[0, 0] == RED
        assert px[399, 299] == RED        # 裁切：右下角仍是原图左上的红
        assert px[399, 0] == RED          # 右边界在 400，图宽 800 → 全是左半

    def test_fit_cover_uses_the_display_size_too(self, project):
        got = render_frame(_frame(scene='bg big', scene_at='bgfit'),
                           project_root=project)
        assert got is not None and got.image is not None
        assert got.background_box == (0.0, 0.0, 400.0, 300.0)


class TestScaleNotesSayItOutLoud:
    def test_note_shows_the_file_size_and_the_engine_size(self, project):
        notes = scale_notes(_frame(scene='bg big'), project_root=project,
                            screen=SCREEN)
        assert notes, '没给出提示行'
        text = notes[0]
        assert '800×600@2' in text
        assert '引擎按 400×300 画' in text
        assert '正好铺满' in text

    def test_plain_image_note_keeps_the_old_shape(self, project):
        notes = scale_notes(_frame(scene='bg plain'), project_root=project,
                            screen=SCREEN)
        assert notes and '800×600 → 屏幕 400×300' in notes[0]
        assert '@' not in notes[0].split('→')[0]
