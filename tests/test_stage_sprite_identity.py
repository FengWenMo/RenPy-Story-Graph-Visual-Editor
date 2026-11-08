"""
tests/test_stage_sprite_identity.py
===================================
**"这条 show 在舞台上到底是谁"** —— 立绘身份回归（2026-11-08）

用户截图（`sr_general`，第 95 拍）：

> bug！这是为什么啊？？？不是一个 en 的表情吗？
> 怎么绑到另一个人上去了？？？qing 的 en 表情与图片不对应！

卡片上写着 `qing en`，舞台小图上站着的却是**星桥理子**（`xing/en.png`）。
根因不在渲染，在"认人"这一步：

  ① `stage.image_index_lookup` 把多词写法的**最后一个词**（`en`）单独丢进
     全盘索引里撞 —— 索引按文件名认图，`en.png` 每个角色都可能有，
     第一个撞上的就是别人的那张；
  ② `stage_render` 这条路**没有分层这一级**（卡片那条 `ui_qt.sprite_thumbs`
     有）：`show qing en` 不是资源表里的一行，于是落到 ①，把
     `xing/en.png` 当成了它。引擎里它显示的是
     `layeredimage qing` 的 `en → thinking`；
  ③ 合成图默认最长边封顶 1400，舞台几何若拿它算，人会小一大截
     （`natural × zoom` 必须按**原图**）。

这份用**自造项目**把三件事钉死（不依赖真机、颜色只用来回答"画的是谁"）：
A. 认人：属性件只许是"这个 tag 的"（`tag/属性` 或 tag 目录里的那份）；
B. 舞台与卡片同一个答案（都按项目自己的 `layeredimage` 合成）；
C. 几何按原图算，不按合成产物算。
"""

from __future__ import annotations

import pytest

from PIL import Image

from storygraph.app.stage import (SpriteLayout, StageFrame, image_index,
                                  image_index_lookup, resolve_stage_asset)
from storygraph.app.stage_render import available, render_frame
from storygraph.story.atl import placement_of

pytestmark = pytest.mark.skipif(not available(), reason='没装 Pillow')

SCREEN = (800, 450)

#: 两张图只有 `gui.init` —— 舞台渲染要项目自己的分辨率。
GUI = 'init python:\n    gui.init(800, 450)\n'

#: 两个角色都声明过 `layeredimage`；`rival` 有一张 `en.png`，`hero` 没有
#: （`hero` 的 `en` 是分层声明里指向 `contemplating` 的属性）。
#: 这正是真机 `qing` / `xing` 的形状。
RPY = '''
layeredimage hero:
    image_format 'images/hero/{image}.png'
    attribute base default:
        'body'

    group expressions:
        attribute glad:
            'body'
        attribute en:
            'contemplating'

layeredimage rival:
    image_format 'images/rival/{image}.png'
    attribute base default:
        'body'

    group expressions:
        attribute en:
            'en'
'''

HERO_BLUE = (0, 0, 255, 255)
HERO_GREEN = (0, 255, 0, 255)
RIVAL_GRAY = (128, 128, 128, 255)
RIVAL_RED = (255, 0, 0, 255)


def _png(path, size, color):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', size, color).save(path)


def _project(root, size=(400, 600)):
    game = root / 'game'
    game.mkdir(parents=True, exist_ok=True)
    (game / 'gui.rpy').write_text(GUI, encoding='utf-8')
    (game / 'definitions.rpy').write_text(RPY, encoding='utf-8')
    _png(game / 'images' / 'hero' / 'body.png', size, HERO_BLUE)
    _png(game / 'images' / 'hero' / 'contemplating.png', size, HERO_GREEN)
    _png(game / 'images' / 'rival' / 'body.png', size, RIVAL_GRAY)
    _png(game / 'images' / 'rival' / 'en.png', size, RIVAL_RED)
    return root


@pytest.fixture()
def project(tmp_path):
    return _project(tmp_path)


def _frame(image):
    place = placement_of('center', None, screen=SCREEN)
    sprite = SpriteLayout(image=image, tag=image.split()[0], at='center',
                          x=0.5, place=place, order_index=0)
    return StageFrame(index=0, line_id='a', label='a', speaker='',
                      text='', scene='', sprites=[sprite])


def _colors(img):
    return set(img.convert('RGBA').getdata())


def _sprite_center(got):
    x, y, w, h = got.sprite_boxes[0]
    return got.image.convert('RGBA').getpixel(
        (int(x + w / 2), max(0, int(y + h / 2))))


# ---------------------------------------------------------------------------
#  A. 认人：属性件不许绑到别的 tag 身上
# ---------------------------------------------------------------------------

def test_bare_attribute_never_binds_to_another_character(project):
    """
    `hero en`：项目里只有 `rival/en.png`，**不许**把它当成 hero 的 en。

    旧行为（用户截图的根因）：`parts[-1]` = `en` 第一个撞上就是 `rival/en.png`。
    """
    idx = image_index(project, refresh_seconds=0)
    assert 'hero/en' in idx or 'hero/contemplating' in idx, '索引没带上目录前缀'
    assert image_index_lookup('hero en', idx) is None
    assert resolve_stage_asset('hero en', {}, str(project), idx) is None


def test_tag_attribute_file_is_still_found(project):
    """没有分层声明、但图就在**自己目录**里的项目（`hero/en.png`）照旧认。"""
    _png(project / 'game' / 'images' / 'hero' / 'en.png', (8, 8), HERO_GREEN)
    idx = image_index(project, refresh_seconds=0)
    hit = image_index_lookup('hero en', idx)
    assert hit is not None
    assert 'hero' in hit.replace('\\', '/').split('/')


def test_two_characters_with_same_attribute_pick_their_own(project):
    """`hero en` 与 `rival en` 都有文件时，各认各的（先撞上的不许通吃）。"""
    _png(project / 'game' / 'images' / 'hero' / 'en.png', (8, 8), HERO_GREEN)
    idx = image_index(project, refresh_seconds=0)
    hero_hit = image_index_lookup('hero en', idx)
    rival_hit = image_index_lookup('rival en', idx)
    assert hero_hit and 'hero' in hero_hit
    assert rival_hit and 'rival' in rival_hit
    assert hero_hit != rival_hit


def test_background_alias_with_dot_still_resolves(project):
    """老写法 `bg bg6_2` → `images/bg/bg6.2.png`（tag 目录那条路没被改坏）。"""
    _png(project / 'game' / 'images' / 'bg' / 'bg6.2.png', (8, 8), (0, 0, 0, 255))
    idx = image_index(project, refresh_seconds=0)
    hit = image_index_lookup('bg bg6_2', idx)
    assert hit is not None and hit.replace('\\', '/').endswith('images/bg/bg6.2.png')


# ---------------------------------------------------------------------------
#  B. 舞台画的是项目分层声明里的那个人（与卡片同一条真源）
# ---------------------------------------------------------------------------

def test_stage_draws_the_layered_character_not_the_lookalike(project):
    """
    `hero en` 必须按 `layeredimage hero` 合成（body + contemplating → 绿），
    不能是 `rival/en.png`（红）。
    """
    got = render_frame(_frame('hero en'), project_root=str(project),
                       screen=SCREEN)
    assert not list(got.missing), f'不该判"找不到"：{got.missing}'
    assert _sprite_center(got) == HERO_GREEN, _sprite_center(got)
    assert RIVAL_RED not in _colors(got.image), '画面上出现了别人的红图'


def test_stage_and_card_agree_on_who_it_is(project):
    """卡片缩略图那条路（`resolve_sprite_thumb_path`）与舞台必须同一个答案。"""
    from storygraph.ui_qt.sprite_thumbs import resolve_sprite_thumb_path
    thumb = resolve_sprite_thumb_path('hero en', project_root=str(project),
                                      max_size=(40, 60))
    assert thumb is not None
    with Image.open(thumb) as im:
        assert HERO_GREEN in _colors(im), '卡片的 en 也不是 hero 自己'
    got = render_frame(_frame('hero en'), project_root=str(project),
                       screen=SCREEN)
    assert _sprite_center(got) == HERO_GREEN


def test_without_layeredimage_the_old_road_still_works(tmp_path):
    """没有 `layeredimage` 的项目：一个字节都不改，照旧按文件认。"""
    game = tmp_path / 'game'
    game.mkdir(parents=True, exist_ok=True)
    (game / 'gui.rpy').write_text(GUI, encoding='utf-8')
    _png(game / 'images' / 'solo' / 'en.png', (400, 600), HERO_GREEN)
    got = render_frame(_frame('solo en'), project_root=str(tmp_path),
                       screen=SCREEN)
    assert _sprite_center(got) == HERO_GREEN, _sprite_center(got)


def test_attribute_not_in_the_declaration_is_not_another_file(project):
    """
    分层声明里没有的属性（`dance`）：按项目里的老约定当"不改变任何一层"
    （真机 `aa` 就是这种），但**绝不许**换成别人的同名文件。
    """
    got = render_frame(_frame('hero dance'), project_root=str(project),
                       screen=SCREEN)
    assert not list(got.missing), got.missing
    assert RIVAL_RED not in _colors(got.image)
    assert _sprite_center(got) == HERO_BLUE, _sprite_center(got)


# ---------------------------------------------------------------------------
#  C. 几何按原图算，不按合成产物算
# ---------------------------------------------------------------------------

def test_composed_sprite_keeps_engine_geometry(tmp_path):
    """
    1600×2400 的立绘、`MAX_SIDE=1400` 的合成产物 —— 画出来仍必须是
    1600×2400（原图 × zoom），不是 933×1400（合成图尺寸）。
    """
    root = _project(tmp_path, size=(1600, 2400))
    got = render_frame(_frame('hero en'), project_root=str(root),
                       screen=SCREEN)
    _x, _y, w, h = got.sprite_boxes[0]
    assert (w, h) == (1600.0, 2400.0), got.sprite_boxes[0]


# ---------------------------------------------------------------------------
#  D. 真机（不在就 skip，不静默通过）
# ---------------------------------------------------------------------------

REAL_ROOT = None
for _cand in (r'D:\千连万理\sr_general', r'D:\千连万理\RenPyTest\sr_general'):
    from pathlib import Path as _Path
    if (_Path(_cand) / 'game' / 'definitions.rpy').is_file():
        REAL_ROOT = _Path(_cand)
        break


@pytest.mark.skipif(REAL_ROOT is None, reason='没有真机项目（换机器正常跳过）')
class TestRealProject:
    def test_qing_en_is_not_xing_en(self):
        """真机：`qing en` 不是 `xing/en.png`；分层声明指向的是 qing 自己的层。"""
        idx = image_index(str(REAL_ROOT), refresh_seconds=0)
        assert image_index_lookup('qing en', idx) is None
        assert resolve_stage_asset('qing en', {}, str(REAL_ROOT), idx) is None
        from storygraph.story.layered_model import (load_layer_model_cached,
                                                    resolve_layers)
        model = load_layer_model_cached(str(REAL_ROOT))
        assert model is not None and model.has('qing')
        plan = resolve_layers(model, 'qing', ['en'], str(REAL_ROOT))
        assert plan.ok, plan
        paths = [str(ly.get('path') or '') for ly in plan.layers]
        assert paths and all('qing' in p.replace('\\', '/') for p in paths), paths
