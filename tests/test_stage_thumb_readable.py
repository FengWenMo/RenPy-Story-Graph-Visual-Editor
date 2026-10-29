"""
tests/test_stage_thumb_readable.py
==================================
卡片「当前舞台」缩略图上**按可读字号重画名字/台词**的回归线（2026-10-29）。

用户报「渲染有问题」：288×162 的小图里名字条和台词糊成方块。根因是尺度 ——
整帧按 1920×1080 画（台词 26~30px），缩到 288 宽只剩 3~4px。修法**不是**
把字号改小（那样舞台预览窗口/整宽图/导出就全错了），而是：整帧照旧渲染，
**缩到 target 之后**再用 `stage_render.draw_readable_say` 把名字/台词按缩略图
自己的可读字号重画一遍。

判据按"看得清 + 不骗人"写：

  · 纯函数：尺寸不变、字号 ≥ 可读下限、条带区域真的重画了（不是只缩了图）；
  · 没窗 / 没有文本框 / 没有字时**逐像素不变**（不做无谓的改动）；
  · 项目字体为空、或项目字体根本画不了汉字时，走系统中文字体：
    不画方块、不抛异常；
  · 前后缀 / 颜色口径与整帧渲染的 `_draw_say` 一致；
  · 名条贴在文本框**上方**（真机 `gui.name_ypos = -58`）时：名条要被
    不透明盖平并重画进小标签里、台词不被名字往下挤、旧台词一个像素不透；
  · 名条的底**跟着字长**（第三版）：引擎名条框缩到 576 target 只剩 77×24，
    名字按它的宽高会被压到 14px（288 控件上 7px）—— 所以名条从左/顶向右/下
    长到包住可读字号的名字；长到画面边缘还放不下才缩字号（下限 9px），
    名条与墨迹都不许出画面；
  · 接线：compact 小图走重画（同步 `render_now` 与后台线程两条路），
    整宽图**行为不变**（仍走原来的引擎排版）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytest.importorskip('PIL')

from PIL import Image, ImageChops, ImageDraw                     # noqa: E402

from storygraph.app import stage_render as R                     # noqa: E402
from storygraph.app.stage import StageFrame                      # noqa: E402
from storygraph.story.renpy_gui import CharacterStyle, GuiProfile  # noqa: E402

#: 整帧分辨率（Ren'Py 桌面模板的常见值）。
SCREEN = (1920, 1080)
#: 测试用的 target：比 288×162 的控件大一圈（真机上 compact 的 target 也是
#: 480×270 这个量级），"缩完再画"的可读字号就在这个尺度上算。
TARGET = (576, 324)


# ==================================================================
#  合成一帧（不碰磁盘：矩形 / 帧 / 排版全给"假"的，纯函数该只依赖这些）
# ==================================================================

TEXTBOX = (0, 802, 1920, 278)          # gui.textbox_height = 278，贴底
NAME_BOX = (360, 802, 160, 53)         # gui.name_xpos = 360、名条高度约 53
TEXT_AREA = (402, 877, 1116, 66)       # gui.dialogue_xpos / _ypos / _width


def _canvas() -> Image.Image:
    """
    假装是渲染好的 1920×1080 整帧：深色舞台 + 一条半透明文本框 + 名字条。

    为什么不用纯色：纯色条带铺完"平均色底"和原像素一样，就只能靠文字像素
    证明重画；带纹理的条带能同时验证"底色确实重铺了一层"。
    """
    img = Image.new('RGBA', SCREEN, (18, 18, 24, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 802, SCREEN[0], SCREEN[1] - 1), fill=(10, 12, 20, 235))
    # 加点横向渐变，别让"条带平均色"和每个像素都相等
    for i in range(0, SCREEN[0], 4):
        shade = 8 + int(10 * abs((i % 200) - 100) / 100.0)
        draw.rectangle((i, 802, i + 3, SCREEN[1] - 1),
                       fill=(shade, shade + 2, shade + 8, 240))
    draw.rectangle((360, 802, 519, 854), fill=(30, 34, 48, 235))
    return img


def _frame(text: str = '第一句台词', speaker: str = 'e',
           name: str = '爱莲') -> StageFrame:
    """一帧台词（`shows_window` 由 text 决定，与真引擎的语义一致）。"""
    return StageFrame(index=0, line_id='t.0001', label='start',
                      speaker=speaker, speaker_name=name, text=text,
                      scene='')


def _result(img: Image.Image | None = None) -> R.RenderResult:
    """整帧渲染结果（矩形与默认 gui 模板一致，尺寸按 1920×1080）。"""
    got = R.RenderResult(width=SCREEN[0], height=SCREEN[1])
    got.image = img if img is not None else _canvas()
    got.textbox_rect = TEXTBOX
    got.name_rect = NAME_BOX
    got.text_rect = TEXT_AREA
    return got


def _profile(**values) -> GuiProfile:
    """默认排版（没读任何项目）：`font_for` 会返回空路径 → 走系统中文字体。"""
    return GuiProfile(project_root='', values=dict(values))


def _ready():
    """整帧 → 缩到 target 的 RGBA 图（真实链路里就是这一步喂给重画）。"""
    return _result(), R.scale_result_image(_result(), TARGET)


def _changed_pixels(a: Image.Image, b: Image.Image, box: tuple) -> int:
    """`box` 范围内 a/b 有多少像素不同（判"真的重画了"）。"""
    x, y, w, h = box
    area = (x, y, x + w, y + h)
    diff = ImageChops.difference(a.convert('RGB').crop(area),
                                 b.convert('RGB').crop(area))
    return sum(1 for px in diff.getdata() if px != (0, 0, 0))


def _has_color(img: Image.Image, box: tuple, rgb: tuple, tol: int = 40) -> bool:
    """`box` 里有没有接近 `rgb` 的像素（判"颜色口径对不对"）。"""
    x, y, w, h = box
    for px in img.convert('RGB').crop((x, y, x + w, y + h)).getdata():
        if all(abs(int(a) - int(b)) <= tol for a, b in zip(px, rgb)):
            return True
    return False


# ==================================================================
#  一、纯函数：缩略图上按可读字号重画
# ==================================================================

class TestReadableSayOnTheThumbnail:
    def test_keeps_the_size_and_redraws_at_a_readable_size(self):
        """
        尺寸不变、字号 ≥ 可读下限、条带区域真的变了（不是把缩放图原样返回）。
        """
        result, img = _ready()
        pure = img.copy()
        out, info = R.draw_readable_say(img, result, _frame(), _profile())

        assert out is not img, '不许就地改传进来的图（调用方还要拿它做对比）'
        assert out.size == TARGET, '重画改变了缩略图尺寸'
        assert info['drawn'] is True
        assert info['name_px'] >= R.MIN_READABLE_PX['name'], info
        assert info['dialogue_px'] >= R.MIN_READABLE_PX['dialogue'], info
        assert info['name_lines'] == 1 and info['dialogue_lines'] >= 1, info
        assert _changed_pixels(out, pure, info['strip']) > 40, \
            '条带区域几乎没有变化，像是没重画'
        # 传进来的图必须原样（像素级），否则缓存里的"纯缩放图"会被改脏
        assert ImageChops.difference(img, pure).getbbox() is None

    def test_the_text_is_laid_out_inside_the_textbox_strip(self):
        """字只能落在文本框条带里（不能画到脸上/画面中间）。"""
        result, img = _ready()
        out, info = R.draw_readable_say(img, result, _frame(), _profile())
        x, y, w, h = info['strip']
        assert (x, y, w, h) == TEXTBOX or (
            abs(y - round(TEXTBOX[1] * TARGET[1] / SCREEN[1])) <= 1), info
        # 条带外的底部（letterbox 区）不许被改
        outside = ImageChops.difference(out, img).getbbox()
        assert outside is not None
        assert outside[3] <= y + h, '重画越出了文本框条带'

    def test_a_long_line_is_clipped_with_an_ellipsis(self):
        """条带里放不下的台词要截断并明确标「…」，不能画出框外。"""
        result, img = _ready()
        long_text = '这是一句非常非常长的台词，' * 40
        out, info = R.draw_readable_say(
            img, result, _frame(text=long_text), _profile())
        assert info['drawn'] is True
        assert info['ellipsis'] is True, info
        assert info['dialogue_lines'] <= 2, info

    def test_a_fake_profile_and_frame_still_work(self):
        """只有最少属性的假 profile（没有 font_for/character_for）也能画。"""
        class _BareProfile:
            pass

        result, img = _ready()
        out, info = R.draw_readable_say(img, result, _frame(),
                                        _BareProfile())
        assert info['drawn'] is True and out.size == TARGET

    def test_scale_result_image_gives_the_target_size(self):
        """先缩放的那一步：尺寸就是 target，且返回 RGBA。"""
        img = R.scale_result_image(_result(), TARGET)
        assert img.size == TARGET and img.mode == 'RGBA'
        assert R.scale_result_image(None, TARGET) is None


# ==================================================================
#  二、没有要画的东西时**逐像素不变**
# ==================================================================

class TestLeavesThePictureAlone:
    def test_no_say_window_is_returned_untouched(self):
        """`shows_window=False`（没有台词）→ 原对象、逐像素不变。"""
        result, img = _ready()
        before = img.copy()
        out, info = R.draw_readable_say(img, result, _frame(text=''), _profile())
        assert info['drawn'] is False and '窗口' in info['reason'], info
        assert out is img, '没窗时不该造一张新图'
        assert ImageChops.difference(out, before).getbbox() is None

    def test_no_textbox_rect_is_returned_untouched(self):
        """矩形无效（RenderResult 没算出文本框）→ 原样返回。"""
        result, img = _ready()
        result.textbox_rect = (0, 0, 0, 0)
        before = img.copy()
        out, info = R.draw_readable_say(img, result, _frame(), _profile())
        assert info['drawn'] is False
        assert ImageChops.difference(out, before).getbbox() is None

    def test_none_inputs_do_not_raise(self):
        """缺图 / 缺结果 → 原样返回 None，绝不抛异常拖垮卡片。"""
        out, info = R.draw_readable_say(None, None, None, None)
        assert out is None and info['drawn'] is False


# ==================================================================
#  三、字体：中文绝不能退化成方块
# ==================================================================

class TestChineseMustNotBecomeBoxes:
    def test_a_cjk_font_exists_to_fall_back_to(self):
        """Windows 上必须能找到一个中文字体（找不到就只能画方块了）。"""
        if os.name != 'nt':
            pytest.skip('系统字体路径只对 Windows 有意义')
        path = R._cjk_font_path()
        assert path and Path(path).is_file(), path

    def test_empty_font_path_draws_with_the_cjk_font(self):
        """项目字体路径为空（`font_for` 返回 ''）→ 走系统中文字体，不抛异常。"""
        profile = _profile()
        assert profile.font_for('e', '爱莲', 'what')[0] == ''
        result, img = _ready()
        out, info = R.draw_readable_say(
            img, result, _frame(text='中文台词'), profile)
        assert info['drawn'] is True and info['dialogue_lines'] >= 1, info
        assert ImageChops.difference(out, img).getbbox() is not None
        if R._cjk_font_path():
            font = R._load_readable_font('', 12)
            assert str(getattr(font, 'path', '')) == R._cjk_font_path(), \
                '回退到的不是系统中文字体（Pillow 默认字体会把中文画成方块）'

    def test_a_latin_only_project_font_falls_back_to_chinese(self):
        """
        项目字体能开、但没有汉字（模板默认的 DejaVuSans）→ 缩略图换中文字体。

        为什么必须管：Ren'Py 模板默认 `gui.text_font = "DejaVuSans.ttf"`，
        SDK 里能摸到、`font_for` 会给出真实路径。直接用它画中文，Pillow 画的
        就是一个个 notdef 方块 —— 正是用户说的"方块"。
        """
        arial = Path(r'C:\Windows\Fonts\arial.ttf')
        if not arial.is_file():
            pytest.skip('这台机器没有 arial.ttf（拉丁字体样本）')
        profile = _profile()
        profile.text_font_path = str(arial)
        result, img = _ready()
        out, info = R.draw_readable_say(
            img, result, _frame(text='中文台词'), profile)
        assert info['drawn'] is True
        assert info['font_fallback'] is True, info
        assert ImageChops.difference(out, img).getbbox() is not None


# ==================================================================
#  四、内容 / 颜色口径与 `_draw_say` 一致
# ==================================================================

class TestSameWordsSameColours:
    def test_prefixes_come_from_the_character_style(self):
        """名字/台词的前后缀来自角色样式（与整帧渲染同一个口径）。"""
        style = CharacterStyle(id='e', who_prefix='【', who_suffix='】',
                               what_prefix='「', what_suffix='」')
        profile = _profile()
        profile.characters = {'e': style}
        who, text, got = R._readable_payload(_frame(text='你好'), profile)
        assert got is style
        assert who == '【爱莲】', who
        assert text == '「你好」', text

    def test_character_colours_win_over_the_profile_default(self):
        """角色自己的 `who_color` / `what_color` 优先于 gui 默认色。"""
        style = CharacterStyle(id='e', who_color='#ffcc00',
                               what_color='#3366ff')
        profile = _profile(text_color='#00ff00', name_text_color='#ff0000')
        profile.characters = {'e': style}
        result, img = _ready()
        out, info = R.draw_readable_say(img, result, _frame(), profile)
        strip = info['strip']
        assert _has_color(out, strip, (255, 204, 0)), '名字没用角色的 who_color'
        assert _has_color(out, strip, (51, 102, 255)), '台词没用角色的 what_color'

    def test_white_text_on_a_light_textbox_gets_a_dark_backing(self):
        """
        浅色文本框（白纸黑字主题）：白字不能白成一片 —— 底色要压暗。

        标题那句"保证在浅色/深色文本框上都看得清"就是这条：底色的颜色取自
        项目文本框（采样平均色），只在对比不够时调亮度，不写死编辑器主题色。
        """
        light = Image.new('RGBA', SCREEN, (18, 18, 24, 255))
        ImageDraw.Draw(light).rectangle(
            (0, 802, SCREEN[0], SCREEN[1] - 1), fill=(245, 245, 240, 255))
        result = _result(light)
        img = R.scale_result_image(result, TARGET)
        out, info = R.draw_readable_say(img, result, _frame(), _profile())
        x, y, w, h = info['strip']
        lums = [sum(px) / 3.0 for px in
                out.convert('RGB').crop((x, y, x + w, y + h)).getdata()]
        assert min(lums) < 90, '白字底下没压暗，浅色文本框上会看不清'
        assert max(lums) > 200, '白字没画出来（或颜色被底色吃掉了）'


# ==================================================================
#  五、名条贴在文本框**上方**时也不能画两遍（真机 sr_general 的形状）
# ==================================================================
#
# 2026-10-29 第二版：真项目里 `gui.name_ypos = -58`，名条整个在文本框上方
# （`name_rect` 与 `textbox_rect` 不重叠）。第一版只铺了文本框条带：
#   · 名条里的原名字还在，重画的名字又被挤到条带里 —— 像写了两遍名字；
#   · 条带底只有 96% 不透明，原台词透出来一层"鬼影"。
# 现在的约定：条带和名条**各自**用自己区域的平均色、不透明铺平；名字画在
# 名条里（字号按名条高度再夹一次）；名条在上方时台词从条带顶部起，不被挤。

#: 名条在文本框上方的排版（与真项目 sr_general 的 gui.rpy 一致）。
TAG_TEXTBOX = (0, 803, 1920, 277)
TAG_NAME_BOX = (575, 745, 257, 80)
TAG_TEXT_AREA = (595, 842, 1116, 40)
#: 条带里的两处"原台词像素"（真项目坐标）：颜色对调、总面积不变，
#: 所以两张图的条带平均色**完全相等** —— 差异只可能来自"没盖干净"。
GHOST_A = (600, 1040, 30, 8)
GHOST_B = (700, 1040, 30, 8)


def _tag_canvas(swap_ghost: bool = False) -> Image.Image:
    """
    一张"名条在文本框上方"的整帧：条带带纹理、名条里还画着旧名字。

    为什么要假旧名字：真机上 `name_rect` 区域里有引擎画的原名字；盖不盖得住
    只能靠"这些像素变没变"来判。
     """
    img = Image.new('RGBA', SCREEN, (18, 18, 24, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle((TAG_TEXTBOX[0], TAG_TEXTBOX[1], SCREEN[0] - 1,
                    SCREEN[1] - 1), fill=(214, 224, 236, 255))
    for i in range(0, SCREEN[0], 6):           # 纹理：平均色 ≠ 每个像素
        shade = 10 if (i // 6) % 2 else 0
        draw.rectangle((i, TAG_TEXTBOX[1], i + 5, SCREEN[1] - 1),
                       fill=(214 - shade, 224 - shade, 236 - shade, 255))
    draw.rectangle((TAG_NAME_BOX[0], TAG_NAME_BOX[1],
                    TAG_NAME_BOX[0] + TAG_NAME_BOX[2] - 1,
                    TAG_NAME_BOX[1] + TAG_NAME_BOX[3] - 1),
                   fill=(46, 150, 92, 255))
    for k in range(3):                         # 名条里的"原名字"笔画
        draw.rectangle((TAG_NAME_BOX[0] + 14 + k * 30, TAG_NAME_BOX[1] + 20,
                        TAG_NAME_BOX[0] + 28 + k * 30, TAG_NAME_BOX[1] + 60),
                       fill=(8, 40, 20, 255))
    colors = ((0, 0, 0, 255), (255, 255, 255, 255))
    if swap_ghost:
        colors = colors[::-1]
    for rect, color in ((GHOST_A, colors[0]), (GHOST_B, colors[1])):
        draw.rectangle((rect[0], rect[1], rect[0] + rect[2] - 1,
                        rect[1] + rect[3] - 1), fill=color)
    return img


def _tag_result(img: Image.Image | None = None,
                name_rect: tuple = TAG_NAME_BOX) -> R.RenderResult:
    got = R.RenderResult(width=SCREEN[0], height=SCREEN[1])
    got.image = img if img is not None else _tag_canvas()
    got.textbox_rect = TAG_TEXTBOX
    got.name_rect = name_rect
    got.text_rect = TAG_TEXT_AREA
    return got


def _tag_frame(text: str = '挺精致的实验室哎，橱窗里的展品好丰富。',
               name: str = '「凌跃捷」') -> StageFrame:
    """真项目那一拍：说话人 lyj、显示名带「」、台词里有中文标点。"""
    return StageFrame(index=0, line_id='t.0001', label='start',
                      speaker='lyj' if name else '', speaker_name=name,
                      text=text, scene='')


def _covered_rects(img: Image.Image, info: dict):
    """`info['covered']` 里**不是**平均色、也不在新字框内的像素（应为空）。"""
    rgb = img.convert('RGB')
    px = rgb.load()
    bases = {tuple(info['strip']): tuple(info['strip_base'])}
    name_tag = tuple(info.get('name_tag') or (0, 0, 0, 0))
    if name_tag[3] > 0 and info.get('name_base'):
        bases[name_tag] = tuple(info['name_base'])
    glyphs = []
    for key in ('name_text_box', 'dialogue_text_box'):
        b = tuple(info.get(key) or (0, 0, 0, 0))
        if b[2] > 0 and b[3] > 0:
            glyphs.append((b[0] - 2, b[1] - 2, b[0] + b[2] + 2,
                           b[1] + b[3] + 2))
    bad = []
    for rect in info['covered']:
        for y in range(rect[1], rect[1] + rect[3]):
            for x in range(rect[0], rect[0] + rect[2]):
                base = None
                for hit in info['covered']:    # 后铺的赢（名条盖在条带上）
                    if hit[0] <= x < hit[0] + hit[2] and \
                            hit[1] <= y < hit[1] + hit[3]:
                        base = bases.get(tuple(hit))
                if base is None or px[x, y] == base:
                    continue
                if any(g[0] <= x <= g[2] and g[1] <= y <= g[3]
                       for g in glyphs):
                    continue
                bad.append((x, y, px[x, y], base))
    return bad


class TestNameTagAboveTheTextbox:
    def test_the_tag_is_covered_and_the_name_is_redrawn_inside_it(self):
        """
        名条区域必须和"纯缩放图"不一样（旧名字被盖、新名字重画），
        而且新名字的字形落在**长到包住字**的名条 `name_tag` 内（±2px）。

        第三版改了口径：名条的底允许比引擎原框大（`name_tag` ≥ `name_box`，
        同左/顶、向右/下长）—— 原框只有 77×24，装不下可读字号的「凌跃捷」。
        """
        result = _tag_result()
        img = R.scale_result_image(result, TARGET)
        pure = img.copy()
        out, info = R.draw_readable_say(img, result, _tag_frame(), _profile())
        nb, nt = info['name_box'], info['name_tag']
        assert nb[3] > 0 and nt[3] > 0, info
        # 名条换算对了（745 * 324/1080 ≈ 224）
        assert abs(nb[1] - round(TAG_NAME_BOX[1] * TARGET[1] / SCREEN[1])) <= 1
        assert nb[1] < info['strip'][1], '这张合成图的名条应该在条带上方'
        assert nt[0] == nb[0] and nt[1] == nb[1], (nt, nb)
        assert nt[2] >= nb[2] and nt[3] >= nb[3], (nt, nb)
        assert nt[0] + nt[2] <= TARGET[0] and nt[1] + nt[3] <= TARGET[1], nt
        assert _changed_pixels(out, pure, nt) > 40, '名条区域没被盖/重画'
        ntb = info['name_text_box']
        assert ntb[2] > 0 and ntb[3] > 0, (ntb, info)
        assert ntb[0] >= nt[0] - 2 and ntb[1] >= nt[1] - 2, (ntb, nt)
        assert ntb[0] + ntb[2] <= nt[0] + nt[2] + 2, (ntb, nt)
        assert ntb[1] + ntb[3] <= nt[1] + nt[3] + 2, (ntb, nt)
        # 字号按**可读字号**（不再被 77px 宽 / 24px 高的引擎名条框卡住）
        assert info['name_px'] >= 16, info
        expect = R._readable_px('name', TARGET[0], info['strip'][3])
        assert info['name_px'] == expect, (info['name_px'], expect)
        # 除了新名字的笔画，名条区域应该已经被平均色铺平（旧名字一个像素不透）
        assert _covered_rects(out, info) == [], _covered_rects(out, info)[:5]

    def test_the_cover_is_opaque_and_reported(self):
        """铺底是不透明的，而且 `covered` 明确列出条带 + 名条（测试/调试可查）。"""
        result = _tag_result()
        img = R.scale_result_image(result, TARGET)
        out, info = R.draw_readable_say(img, result, _tag_frame(), _profile())
        assert info['covered'] == [info['strip'], info['name_tag']], info
        x, y, w, h = info['strip']
        probe = out.getpixel((x + 4, y + h - 4))
        assert probe[3] == 255, f'条带底不是不透明：{probe}'
        assert tuple(probe[:3]) == tuple(info['strip_base']), \
            '条带底不是铺的条带平均色'
        nt = info['name_tag']
        corner = out.getpixel((nt[0] + 1, nt[1] + 1))
        assert tuple(corner[:3]) == tuple(info['name_base']), \
            '名条底不是铺的名条平均色'

    def test_the_dialogue_is_not_pushed_down_by_a_name_above_the_tag(self):
        """
        名条在条带上方时，台词从条带顶部起 —— 名条不再把台词往下挤。

        判据：同一帧"有名字 / 没名字"重画后，台词起点必须相同；第一版会把
        台词推到名条下面（差出十几像素），这条就会红。
        """
        result = _tag_result()
        img = R.scale_result_image(result, TARGET)
        _o1, with_name = R.draw_readable_say(
            img, result, _tag_frame(), _profile())
        _o2, no_name = R.draw_readable_say(
            img, result, _tag_frame(name=''), _profile())
        assert with_name['dialogue_text_box'][3] > 0, with_name
        assert no_name['dialogue_text_box'][3] > 0, no_name
        assert with_name['dialogue_text_box'][1] == \
            no_name['dialogue_text_box'][1], (with_name, no_name)
        # 名条在条带上方：台词起点在条带内，而不是被挤到条带外
        assert with_name['dialogue_text_box'][1] >= with_name['strip'][1]
        assert with_name['dialogue_text_box'][1] <= \
            with_name['strip'][1] + with_name['strip'][3] // 2

    def test_the_old_dialogue_does_not_show_through(self):
        """
        两张只有"原台词像素"颜色对调、其余完全相同的合成帧：重画之后，
        条带里的差异只能落在**新台词的字形**上（旧字已经被不透明铺底盖掉）。

        为什么这样造：对调两处等面积的黑/白块，条带的平均色**逐位相同**，
        所以"差异"不可能是平均色不同造成的 —— 只可能是旧字残留透出来。
        """
        got1 = _tag_result(_tag_canvas(swap_ghost=False))
        got2 = _tag_result(_tag_canvas(swap_ghost=True))
        img1 = R.scale_result_image(got1, TARGET)
        img2 = R.scale_result_image(got2, TARGET)
        out1, info1 = R.draw_readable_say(
            img1, got1, _tag_frame(text='第一句'), _profile())
        out2, info2 = R.draw_readable_say(
            img2, got2, _tag_frame(text='第二句台词'), _profile())
        x, y, w, h = info1['strip']
        area = (x, y, x + w, y + h)
        diff = ImageChops.difference(out1.convert('RGB').crop(area),
                                     out2.convert('RGB').crop(area))
        assert diff.getbbox() is not None, '两帧新台词不同，条带里应该有差异'
        # 允许差异的范围 = 两帧新台词排版框（±2px，描边/抗锯齿）
        allowed = []
        for info in (info1, info2):
            b = info['dialogue_text_box']
            allowed.append((b[0] - 2, b[1] - 2, b[0] + b[2] + 2,
                            b[1] + b[3] + 2))
        dpx = diff.load()
        stray = []
        for yy in range(diff.height):
            for xx in range(diff.width):
                if dpx[xx, yy] == (0, 0, 0):
                    continue
                gx, gy = xx + x, yy + y
                if any(a[0] <= gx <= a[2] and a[1] <= gy <= a[3]
                       for a in allowed):
                    continue
                stray.append((gx, gy, dpx[xx, yy]))
        assert not stray, f'旧台词残留透出来了：{stray[:5]}'
        # 那两处旧台词像素现在必须等于条带平均色（一个像素都不透）
        base = tuple(info1['strip_base'])
        sx = TARGET[0] / float(SCREEN[0])
        sy = TARGET[1] / float(SCREEN[1])
        for rect in (GHOST_A, GHOST_B):
            for gx in range(int(round(rect[0] * sx)),
                            int(round((rect[0] + rect[2]) * sx))):
                for gy in range(int(round(rect[1] * sy)),
                                int(round((rect[1] + rect[3]) * sy))):
                    got = tuple(out1.convert('RGB').getpixel((gx, gy)))
                    assert got == base, (gx, gy, got)

    def test_without_a_name_rect_it_falls_back_into_the_strip(self):
        """名条矩形无效（老项目 / 没名条图）→ 退回"条带内第一行"的老画法。"""
        result = _tag_result(name_rect=(0, 0, 0, 0))
        img = R.scale_result_image(result, TARGET)
        out, info = R.draw_readable_say(img, result, _tag_frame(), _profile())
        assert info['name_box'] == (0, 0, 0, 0), info
        assert info['name_tag'] == (0, 0, 0, 0), info
        assert info['covered'] == [info['strip']], info
        assert info['name_px'] >= R.MIN_READABLE_PX['name'], info
        assert info['name_text_box'][2] > 0 and info['name_text_box'][3] > 0
        assert info['dialogue_lines'] >= 1

    def test_a_name_too_wide_for_the_picture_shrinks_instead_of_overflowing(self):
        """
        名字长到"画面右边缘都放不下"：先逐级缩字号（不低于 9px），实在放不下
        才加「…」——名条与墨迹都不许出画面（第三版：名条可以长，但不许出界）。
        """
        result = _tag_result()
        img = R.scale_result_image(result, TARGET)
        frame = _tag_frame(name='名' * 50)
        out, info = R.draw_readable_say(img, result, frame, _profile())
        nt, ntb = info['name_tag'], info['name_text_box']
        assert info['name_px'] == R._NAME_BOX_MIN_PX, info
        assert info['ellipsis'] is True, info
        assert nt[2] > info['name_box'][2], '名条应该先长到画面边缘再缩字号'
        assert nt[0] + nt[2] <= TARGET[0] and nt[1] + nt[3] <= TARGET[1], (nt, info)
        assert ntb[0] >= nt[0] - 2 and ntb[0] + ntb[2] <= nt[0] + nt[2] + 2
        assert ntb[1] >= nt[1] - 2 and ntb[1] + ntb[3] <= nt[1] + nt[3] + 2


# ==================================================================
#  六、接线：compact 重画、整宽图 / 预览窗口不变
# ==================================================================

SCRIPT = '''define e = Character("爱莲", color="#c8ffc8")

label ch:
    scene bg red
    e "第一句"
'''


@pytest.fixture
def readable_project(tmp_path):
    """一个 1920×1080 的红底工程（颜色可断言、只有一拍台词）。"""
    root = tmp_path / 'proj'
    game = root / 'game'
    (game / 'script').mkdir(parents=True)
    (game / 'images' / 'bg').mkdir(parents=True)
    Image.new('RGB', SCREEN, (200, 30, 40)).save(
        game / 'images' / 'bg' / 'red.png')
    (game / 'script' / 'ch.rpy').write_text(SCRIPT, encoding='utf-8')
    return root


@pytest.fixture
def session(readable_project):
    from storygraph.app.editor import EditorSession

    s = EditorSession(project_root=readable_project)
    s.load(readable_project / 'game' / 'script' / 'ch.rpy')
    return s


def _spin(ms: int) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


def _wait(cond, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        _spin(20)
    return bool(cond())


def _spy_redraw(monkeypatch):
    """把 `draw_readable_say` 换成"记一笔再照常画"的探针。"""
    calls = []
    real = R.draw_readable_say

    def spy(img, result, frame, profile=None):
        calls.append({'size': img.size, 'text': frame.text,
                      'result_w': result.width})
        return real(img, result, frame, profile)

    monkeypatch.setattr(R, 'draw_readable_say', spy)
    return calls


@pytest.fixture
def compact_thumb(qapp):
    from storygraph.ui_qt.stage_thumb import COMPACT_H, COMPACT_W, BeatStageThumb

    w = BeatStageThumb(compact=True)
    w.resize(COMPACT_W, COMPACT_H)
    w.show()
    qapp.processEvents()
    try:
        yield w
    finally:
        w.shutdown()
        w.close()


@pytest.fixture
def wide_thumb(qapp):
    from storygraph.ui_qt.stage_thumb import BeatStageThumb

    w = BeatStageThumb()                 # compact=False：整宽图（行为不变那条）
    w.resize(480, 270)
    w.show()
    qapp.processEvents()
    try:
        yield w
    finally:
        w.shutdown()
        w.close()


class TestThumbnailWiring:
    def test_compact_thumb_redraws_after_scaling(self, compact_thumb, session,
                                                 monkeypatch):
        """
        `render_now`（同步那条路）：**先缩到 target 再重画**，
        重画拿到的图已经是缩略图尺度（不是 1920×1080）。
        """
        calls = _spy_redraw(monkeypatch)
        compact_thumb.show_beat(0, session)
        pix = compact_thumb.render_now()
        assert pix is not None and not pix.isNull()
        assert calls, 'compact 小图没有走可读重画'
        assert calls[0]['size'] == (480, 270), calls[0]
        assert calls[0]['result_w'] == SCREEN[0], '得拿整帧结果换算矩形'
        assert calls[0]['text'] == '第一句'

    def test_the_background_job_redraws_too(self, compact_thumb, session,
                                            monkeypatch):
        """防抖 + 后台线程那条路也要重画（正式使用时走的就是它）。"""
        calls = _spy_redraw(monkeypatch)
        compact_thumb.show_beat(0, session)
        assert _wait(lambda: compact_thumb.pixmap() is not None), \
            f'后台渲染没出图（note={compact_thumb.note()!r}）'
        assert calls, '后台线程那条路没有重画'
        assert calls[0]['size'] == (480, 270)

    def test_wide_picture_keeps_the_engine_layout(self, wide_thumb, session,
                                                  monkeypatch):
        """整宽图（compact=False）**行为不变**：仍走原来的整帧缩放。"""
        calls = _spy_redraw(monkeypatch)
        wide_thumb.show_beat(0, session)
        pix = wide_thumb.render_now()
        assert pix is not None and not pix.isNull()
        assert not calls, '整宽图不该重画文字（那是舞台预览的活）'
        assert (pix.width(), pix.height()) == (480, 270)

    def test_a_broken_redraw_still_shows_the_picture(self, compact_thumb,
                                                     session, monkeypatch):
        """
        可读重画挂了 → **退回纯缩放的整帧图**，不能让卡片停在"正在渲染"。

        这条与 `test_show_beat_returns_immediately` 同一个理由：缩略图是
        "锦上添花"，它的失败绝不该升级成"这一拍没有图"。
        """
        def boom(*_args, **_kwargs):
            raise RuntimeError('重画挂了')

        monkeypatch.setattr(R, 'draw_readable_say', boom)
        compact_thumb.show_beat(0, session)
        pix = compact_thumb.render_now()
        assert pix is not None and not pix.isNull(), '重画失败把整张图也丢了'


# ==================================================================
#  七、stage_preview 抽出来的 `image_to_qimage`（行为不变）
# ==================================================================

class TestImageToQImage:
    def test_converts_a_pil_image_and_scales_to_the_target(self):
        from storygraph.ui_qt.stage_preview import image_to_qimage

        img = Image.new('RGBA', (200, 100), (10, 20, 30, 255))
        same = image_to_qimage(img)
        assert (same.width(), same.height()) == (200, 100)
        scaled = image_to_qimage(img, (100, 50))
        assert (scaled.width(), scaled.height()) == (100, 50)
        assert image_to_qimage(None) is None

    def test_result_to_qimage_keeps_its_old_contract(self):
        """`result_to_qimage` 改为调用 `image_to_qimage` 之后行为不变。"""
        from storygraph.ui_qt.stage_preview import result_to_qimage

        result = R.RenderResult(width=200, height=100,
                                image=Image.new('RGBA', (200, 100),
                                                (1, 2, 3, 255)))
        assert (result_to_qimage(result).width(),
                result_to_qimage(result).height()) == (200, 100)
        scaled = result_to_qimage(result, (100, 50))
        assert (scaled.width(), scaled.height()) == (100, 50)
        assert result_to_qimage(R.RenderResult()) is None


# ==================================================================
#  八、名条的底长到包住字（第三版：名字不再被引擎名条框卡小）
# ==================================================================
#
# 真机 sr_general 的名条在 1920×1080 下 257×80，缩到 576 target 只有 77×24。
# 第二版把名字塞进这个框：字号被压到 14px（画到 288 控件只剩 7px），用户原话
# "名字条糊成方块"一点没改善。第三版：先按可读字号定名字大小，名条的底从左/
# 顶向右/下长到包住字；只有一路长到画面边缘还放不下，才反过来缩字号（下限
# 9px）—— 名条和名字都不许出画面。

#: 真机换算后的名条框：1920 下 (575, 745, 257, 80) → 576 target 上 77×24。
NARROW_TAG = (575, 745, 257, 80)


class TestWidenedNameTag:
    def test_a_77px_engine_tag_no_longer_caps_the_name_size(self):
        """
        真机那个 77px 宽的名条：名字实际字号 ≥16、名条的底比原框宽，
        墨迹完全落在**加长后**的名条里（左/顶仍照引擎，只向右/下长）。
        """
        result = _tag_result(name_rect=NARROW_TAG)
        img = R.scale_result_image(result, TARGET)
        out, info = R.draw_readable_say(img, result, _tag_frame(), _profile())
        nb, nt = info['name_box'], info['name_tag']
        assert nb[2] == round(NARROW_TAG[2] * TARGET[0] / SCREEN[0]), nb
        assert info['name_px'] >= 16, info
        assert nt[2] > nb[2], f'名条没有长到包住字：{nt} vs {nb}'
        assert nt[0] == nb[0] and nt[1] == nb[1], (nt, nb)
        assert nt[0] + nt[2] <= TARGET[0], nt
        ntb = info['name_text_box']
        assert ntb[2] > 0 and ntb[3] > 0, (ntb, info)
        assert ntb[0] >= nt[0] and ntb[1] >= nt[1], (ntb, nt)
        assert ntb[0] + ntb[2] <= nt[0] + nt[2], (ntb, nt)
        assert ntb[1] + ntb[3] <= nt[1] + nt[3], (ntb, nt)

    def test_the_name_is_readable_at_the_288_widget_size(self):
        """576 target 上的名字字号，画到 288 的控件上还有 ≥12px（原来 7px）。"""
        result = _tag_result(name_rect=NARROW_TAG)
        img = R.scale_result_image(result, TARGET)
        _out, info = R.draw_readable_say(img, result, _tag_frame(), _profile())
        widget_px = info['name_px'] * 288.0 / TARGET[0]
        assert widget_px >= 12.0, (info['name_px'], widget_px)
        nt = info['name_tag']
        assert nt[2] * 288.0 / TARGET[0] >= 40, nt

    def test_the_tag_never_runs_off_the_picture(self):
        """名条贴在画面右边缘：先逐级缩字号，仍然一个像素都不许出画面。"""
        result = _tag_result(name_rect=(1750, 745, 257, 80))
        img = R.scale_result_image(result, TARGET)
        out, info = R.draw_readable_say(img, result, _tag_frame(), _profile())
        nt, ntb = info['name_tag'], info['name_text_box']
        assert nt[0] + nt[2] <= TARGET[0], (nt, TARGET)
        assert nt[1] + nt[3] <= TARGET[1], nt
        assert info['name_px'] >= R._NAME_BOX_MIN_PX, info
        assert ntb[0] >= nt[0] - 2 and ntb[0] + ntb[2] <= nt[0] + nt[2] + 2
        assert ntb[1] + ntb[3] <= nt[1] + nt[3] + 2

    def test_a_name_tag_inside_the_strip_pushes_the_dialogue_below_it(self):
        """名条在条带里（模板写法）：台词接在**加长后**的名条底下，不会被盖。"""
        result, img = _ready()
        out, with_name = R.draw_readable_say(img, result, _frame(), _profile())
        _o, no_name = R.draw_readable_say(
            img, result, _frame(speaker='', name=''), _profile())
        nb, nt = with_name['name_box'], with_name['name_tag']
        assert nb[1] >= with_name['strip'][1], '这帧的名条应该在条带里'
        assert nt[3] >= nb[3], (nt, nb)
        dlg = with_name['dialogue_text_box']
        assert dlg[3] > 0, with_name
        assert dlg[1] >= nt[1] + nt[3] - 2, (dlg, nt)
        assert dlg[1] > no_name['dialogue_text_box'][1] + 4, \
            '名条加长后台词没有跟着往下让'
