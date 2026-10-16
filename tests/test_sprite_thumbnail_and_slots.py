'''
卡片的立绘缩略图：**分层合成** + **一维一格的口径统一**
=====================================================

用户 2026-10-16 报的两个 bug（附截图）：

> bug！我换了服装，但显然你的图片没有跟着随即切换……还是原来那张……不合理！
> 另一个 bug 是这个：我明明配置了全局的 show 表情……为何一张卡片有服装，
> 另一张卡片却没有啊？？？这完全不合理啊？？？

① **换了服装图不变**：`app/session_assets.path_of_image` 在多属性那一支
   一次只试**一个**属性、命中即返回（当年为修"无头骑士"改的），于是
   `ling close_happy base_swimsuit` 永远命中 `close_happy.png`，
   第二维（服装）**从不参与** → 换哪个服装都是同一张图。
   修法：卡片这条路也走**项目自己的 layeredimage 结构 + `compose_sprite` 合成**
   （演出预览那条路早在用了，卡片这条路漏了）。

② **同一套配置，卡片之间格子不一样**：`app/beats.attr_slot_rows` 从前判的是
   "这一拍写了几个词"（有值的维度 < 2 → 返回 `[]` 走单格老路），于是
   `ling close_happy base_swimsuit`（两个词）有两格、
   `ling happy`（一个词）**一个格都没有** → 服装根本改不了。
   修法：判据改成"**引擎给了几个组**"（≥2 组 → 每个组一格，没写的那一格
   显示 `—`）；没有引擎事实/读不到 `layeredimage` 的项目照旧走老路。
'''

from __future__ import annotations

import pytest

pytest.importorskip('PIL')          # 合成要真图片；没装 PIL 就 skip（产品里也是退化）

#: 自造项目（通用性护栏：一个真实项目的名字都不用）
_RPY = '''
layeredimage hero:
    image_format 'images/hero/{image}.png'

    group mood:
        attribute glad default:
            'glad'
        attribute sad:
            'sad'

    group outfit:
        attribute plain default:
            'plain'
        attribute gown:
            'gown'
'''


def _png(path, rgba=(255, 0, 0, 255), size=(8, 8)):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', size, rgba).save(path)


@pytest.fixture()
def hero_project(tmp_path):
    from PIL import Image, ImageDraw                      # noqa: F401
    game = tmp_path / 'game'
    d = game / 'images' / 'hero'
    _png(d / 'glad.png', (255, 0, 0, 255))
    _png(d / 'sad.png', (0, 255, 0, 255))
    _png(d / 'plain.png', (0, 0, 255, 128))
    _png(d / 'gown.png', (255, 255, 0, 128))
    (game / 'defs.rpy').write_text(_RPY, encoding='utf-8')
    return tmp_path


class _FakeSession:
    """只给 `path_of_image` 需要的两件东西（宿主接口是 `registry()` + project_root）。"""

    def __init__(self, root):
        from storygraph.assets.asset_registry import AssetRegistry
        reg = AssetRegistry()
        reg.images = {
            'glad': {'ID': 'glad', 'Tag': 'hero', 'Attributes': 'glad',
                     'Path': 'game/images/hero/glad.png'},
            'gown': {'ID': 'gown', 'Tag': 'hero', 'Attributes': 'gown',
                     'Path': 'game/images/hero/gown.png'},
        }
        reg.project_root = str(root)
        self._reg = reg
        self.project_root = str(root)

    def registry(self):
        return self._reg


# ----------------------------------------------------------------------
#  ① 换了服装，图必须跟着变
# ----------------------------------------------------------------------

def test_换服装预览必须跟着变(hero_project, tmp_path):
    """
    用户原始 bug：换服装 → 图不变。真因不是缓存，是**解析出来就是同一个文件**
    （多属性那一支一次只试一个词）。这条把"两个不同服装必须给出两个不同图片"
    钉死 —— 也顺带钉住合成缓存键**含属性选择**（否则换了维度、路径却一样）。
    """
    from storygraph.app.session_assets import AssetRegistryMixin
    s = _FakeSession(hero_project)
    composed_dir = tmp_path / 'composed'

    one = AssetRegistryMixin._composed_sprite_path(
        s, 'hero glad plain')
    two = AssetRegistryMixin._composed_sprite_path(
        s, 'hero glad gown')
    assert one is not None and two is not None, (one, two)
    assert str(one) != str(two), '换了服装，合成结果还是同一个文件'
    assert one.is_file() and two.is_file()

    # **两张图的像素不一样**（不是"路径不同、内容相同"那种假修复）
    from PIL import Image
    with Image.open(one) as a, Image.open(two) as b:
        assert a.tobytes() != b.tobytes(), '换服装以后像素一模一样'

    # 换 **表情** 同理（另一维）
    three = AssetRegistryMixin._composed_sprite_path(s, 'hero sad plain')
    assert three is not None and str(three) != str(one)

    # 幂等：同一组属性 → 同一个缓存文件（不会每次都重合成）
    again = AssetRegistryMixin._composed_sprite_path(s, 'hero glad plain')
    assert str(again) == str(one)

    # 缓存键含属性：同一组里两个取值**解到同一张源图**时也不能混
    from storygraph.app import layer_compose
    from storygraph.story.layered_model import (LayerPlan,
                                                load_layer_model_cached)
    model = load_layer_model_cached(str(hero_project))
    plan_a = LayerPlan(tag='hero', attrs_used=['glad', 'plain'],
                       attrs_chosen=['glad', 'plain'])
    plan_b = LayerPlan(tag='hero', attrs_used=['glad', 'gown'],
                       attrs_chosen=['glad', 'gown'])
    plan_a.layers = plan_b.layers = [{'path': str(hero_project / 'game' /
                                                  'images' / 'hero' / 'glad.png')}]
    assert layer_compose._sig(plan_a) != layer_compose._sig(plan_b), \
        '属性选择没进合成签名 —— 换了维度会命中同一份缓存'


def test_没有分层声明的项目照旧走老路(hero_project):
    """
    ⚠ **通用性底线**：没有 `layeredimage` 声明的 tag（`cg cg03` 这类）
    合成不了 → 返回 None，调用方走原来的单图/资源表路径，一个字节不变。
    """
    from storygraph.app.session_assets import AssetRegistryMixin
    s = _FakeSession(hero_project)
    assert AssetRegistryMixin._composed_sprite_path(s, 'cg cg03') is None
    assert AssetRegistryMixin._composed_sprite_path(s, 'bg room') is None
    assert AssetRegistryMixin._composed_sprite_path(s, '') is None


# ----------------------------------------------------------------------
#  ② 同一套配置，"有没有格"必须一致
# ----------------------------------------------------------------------

class _SlotSym:
    def __init__(self, project_root=None):
        self.registry = type('R', (), {'project_root': project_root})()
        self.project_root = project_root
        self.expressions = {}
        self.project_words = {'expression': set(), 'position': set()}

    def attrs_of_tag(self, tag):
        return []

    def expression_word_for(self, tag, attr):
        return ''


def _hero_facts():
    """
    一份**可用**的引擎事实（两个闸门都为真 —— 与 `test_engine_choices._facts` 同形）。

    ⚠ 必须走 `build_facts`：`grouped_choices` 只认那个形状，
    自己拼一个裸 dict 会静默给出"没有分组"。
    """
    from storygraph.core import engine_facts as EF

    attrs = {'hero': [{'group': 'mood', 'attribute': 'glad'},
                      {'group': 'mood', 'attribute': 'sad'},
                      {'group': 'outfit', 'attribute': 'plain'},
                      {'group': 'outfit', 'attribute': 'gown'}]}
    cands = {'hero': ['glad', 'sad', 'plain', 'gown']}
    return EF.build_facts(
        {'attributes': [(EF.SRC_PROBE, attrs)],
         'candidates': [(EF.SRC_PROBE, cands)]},
        collection={'parse_clean': True, 'probe_complete': True})


def test_同一套配置的卡片格数必须一致():
    """
    用户原始 bug：`ling close_happy base_swimsuit` 有两格（表情 + 服装），
    可 `ling happy` / `lan happy` **一个格都没有** → 服装没法改。

    口径改成"**引擎给了几个组就几格**"：写了两个词、只写一个词，
    格数都一样（没写的那一格显示 `—`，点一下才把值固定到这一拍）。
    """
    from storygraph.app import beats
    sym = _SlotSym()
    facts = _hero_facts()

    two_words = beats.attr_slot_rows('hero glad plain', sym, facts=facts)
    one_word = beats.attr_slot_rows('hero glad', sym, facts=facts)
    other_one = beats.attr_slot_rows('hero plain', sym, facts=facts)

    assert [r['group'] for r in two_words] == ['mood', 'outfit'], two_words
    assert [r['group'] for r in one_word] == ['mood', 'outfit'], one_word
    assert [r['group'] for r in other_one] == ['mood', 'outfit'], other_one

    # 写了的维度带值，没写的维度**空着**（界面上显示 `—`），候选照样给全
    by_two = {r['group']: r for r in two_words}
    by_one = {r['group']: r for r in one_word}
    assert by_two['mood']['value'] == 'glad'
    assert by_two['outfit']['value'] == 'plain'
    assert by_one['mood']['value'] == 'glad'
    assert by_one['outfit']['value'] == '', '没写服装那一维不是"没有格"，是"格空着"'
    assert by_one['outfit']['options'] == ['plain', 'gown']
    assert by_one['outfit']['display'] == ''


def test_格空着时点一下会补上而不是吃掉别人():
    """空格子的第一次点击 = 取第一个候选（其余维度一个词都不许丢）。"""
    from storygraph.app import beats
    sym = _SlotSym()
    facts = _hero_facts()
    got = beats.apply_attr_slot('hero glad', 'outfit', 'plain', symbols=sym,
                                facts=facts)
    assert got == 'hero glad plain', got


def test_认不出引擎分组就照旧不给格():
    """没有引擎事实、也读不到 `layeredimage` → 老路（返回 `[]`，一个字节不变）。"""
    from storygraph.app import beats
    assert beats.attr_slot_rows('hero glad plain', _SlotSym()) == []
    assert beats.attr_slot_rows('bg1.1', _SlotSym(), facts=_hero_facts()) == []
