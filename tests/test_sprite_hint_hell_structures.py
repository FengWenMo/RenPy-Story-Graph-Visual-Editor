"""
地狱测试（第三轮，2026-11-05）：**跨文件的自定义结构** + `with` / `at` /
`transform` + **预览图到底显示哪张**
====================================================================

用户把前两轮的问题一次说清：

> 一开始的地狱测试完全没有理解我的意思 …… 必须要自定义变量/结构，还能
> 识别！并且不误报，不漏报！服装的 `show` 只是最简单的一个 …… 后面你要有
> `with`，要有 `transform` 的引擎没有的自定义结构 …… 更离谱的是还要写在
> 不同的文件里 …… 有的时候不必过于苛求它到底是不是图片！因为很多时候这些
> 东西也不是图片！（所以我之前说找到对应的图片是错误的 …… 应该说：预览里面
> 被选中的图片，修改后显示正确）

所以这一轮钉三件事：

| 组 | 钉什么 |
|---|---|
| `TestDefinitionsAcrossFiles` | 定义散在**不同目录/不同文件**、写法再怪也要认（`init python` 嵌套 dict、tuple、`define`、`image` 语句、`renpy.image()`） |
| `TestTypoTwinsAreReported` | 只差一个定义的孪生词必须点名报错；**脚本里用过不算定义**；dict 的**值**不算词；删掉定义文件后立刻改判 |
| `TestClauseWordsAreNotAttributes` | `with` / `at` / `@` / `behind` / `as` / `onlayer` / `zorder`、`expression` / `screen` / `layer` 都不许被当成属性 |
| `TestCardPreviewShowsTheRightPicture` | **真卡片**：`lan aa happy` 的缩略图 = `happy.png`；把维度改成 `coat` 后缩略图**变成叠好的那张**（像素级验证），改回来也变回 `happy.png` |
| `TestStagePreviewUsesTheSameResolution` | 舞台预览同一条解析：`sprite_plan` + `compose_sprite` 在换服装后必须换图 |
| `TestOutfitCodeWithoutAnyPng` | 真机的 `aa`：有变量、没 png、也不是 `attribute` —— 不许报错，也不许拿 lint 的 `is not an image` 推翻它 |

## 为什么预览要“像素级”

用户否掉了上一轮的验收方式：提示条写一句“实际显示 `…/indifferent.png`”
不算数 —— 要在**预览里真的看到那张图**。所以这一轮直接把卡片缩略图的
落盘路径打开，读 `(0, 0)` 与 `(0, 7)` 两个像素：

    happy.png       上红下透明
    coat.png        上透明下蓝
    → `lan coat happy` 的合成结果：上红（裁下来的脸）+ 下蓝（衣服）

换一维、图没换（旧 bug）时这两个像素不会同时成立。

## 工程零件长什么样

`game/` 下九个文件，定义分散在 `data/`、`definitions/`、`helpers/`、
`stage/`、`fx/`、`screens/`、`chapters/`：

    data/wardrobe.rpy        tab 缩进 + 中文键 + 嵌套 dict + tuple + renpy.image
    data/define.rpy          define 字面量（hat 只在这里定义）+ dict 值陷阱
    definitions/lan.rpy      layeredimage（双 expressions 组 + outfit 组）
    definitions/alias.rpy    image lan 符_77 = ...
    helpers/crop.rpy         AlphaMask(图, Crop(矩形, Solid)) —— 真机 crop_lan
    stage/poses.rpy          transform hell_pose / hell_sway(amount=…)
    fx/transitions.rpy       define hell_dissolve / hell_fade
    screens/hud.rpy          screen hell_hud
    chapters/ch1.rpy         真 show 行：with / at / @ / behind / as / onlayer / zorder

⚠ **使用不是定义**：`ch1.rpy` 里故意写了 `show lan ghost happy`（ghost 没定义）,
检测器仍必须点名 `ghost` —— 这是上一轮用户当场否掉的“剧本里用过就算认识”。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.ui_qt.sprite_hints import known_tag_warning   # noqa: E402

# ---------------------------------------------------------------------------
#  工程零件
# ---------------------------------------------------------------------------

#: 换装表：tab 缩进、中文键、嵌套 dict、tuple、运行期注册 —— 全在一个文件里。
WARDROBE = (
    "init python:\n"
    "\tＬＯＯＫＳ = {\n"
    "\t\t\"lan\": {\n"
    "\t\t\t\"普通\": [\"aa\"],\n"
    "\t\t\t\"冷\": [\"coat\"],\n"
    "\t\t},\n"
    "\t\t\"ling\": [\"hanfu\"],\n"
    "\t}\n"
    "\t_ALT = (\n"
    "\t\t\"towel\",\n"
    "\t)\n"
    "\tdef _looks_for(cid):\n"
    "\t\treturn ＬＯＯＫＳ.get(cid, {})\n"
    "\trenpy.image(\"lan sigil_77\", \"images/characters/lan/happy.png\")\n"
)

#: 顶层 `define`：`hat` **只**在这里定义；`fallback_word` 是 dict 的**值**，
#: 必须被无视（`{"label_key": "fallback_word"}` 里那个值不是属性代号）。
DEFINES = (
    "define HELL_TAGS = [\"hat\"]\n"
    "define HELL_LABELS = {\"label_key\": \"fallback_word\"}\n"
)

#: 真机 `crop_lan` 的缩小版：AlphaMask + Crop + Solid，裁出上半张脸。
CROP_HELPER = (
    "init python:\n"
    "    def crop_lan(expr):\n"
    "        return AlphaMask(\n"
    "            \"images/characters/lan/\" + expr + \".png\",\n"
    "            Crop((0, 0, 8, 4), Solid(\"#fff\")))\n"
)

#: 真机 `layeredimage lan` 的缩小版：两个 expressions 组 + 一个 outfit 组。
LAN_DEF = (
    "layeredimage lan:\n"
    "    image_format 'images/characters/lan/{image}.png'\n"
    "\n"
    "    group expressions when not coat and not clothes2:\n"
    "        attribute happy default:\n"
    "            'happy'\n"
    "        attribute indifferent:\n"
    "            'indifferent'\n"
    "\n"
    "    group expressions when coat or clothes2:\n"
    "        attribute happy:\n"
    "            crop_lan('happy')\n"
    "        attribute indifferent:\n"
    "            crop_lan('indifferent')\n"
    "\n"
    "    group outfit when coat or clothes2:\n"
    "        attribute coat:\n"
    "            'coat'\n"
    "        attribute clothes2:\n"
    "            'clothes2'\n"
)

#: 引擎认、但不是 `layeredimage` attribute 的另一种声明。
ALIAS_DEF = 'image lan 符_77 = "images/characters/lan/happy.png"\n'

#: 自定义 transform（带参数）—— 引擎没有的“新结构”，写在另一个文件。
POSES = (
    "transform hell_pose:\n"
    "    xalign 0.5\n"
    "    yalign 1.0\n"
    "\n"
    "transform hell_sway(amount=0.3):\n"
    "    xoffset amount * 10\n"
)

#: 自定义转场（`define`）—— `with` 后面跟它。
TRANSITIONS = (
    "define hell_dissolve = Dissolve(0.2)\n"
    "define hell_fade = Fade(0.1, 0.0, 0.1)\n"
)

#: 非图像 show（`screen`）—— 另一个文件。
SCREEN = (
    "screen hell_hud():\n"
    "    text \"hud\"\n"
)

#: 真 show 行：`with` / `at` / `@` / `behind` / `as` / `onlayer` / `zorder`
#: 全上；最后一行故意是**用过但没定义**的词（ghost），用来钉“使用不是定义”。
CHAPTER = (
    "label start:\n"
    "    show lan aa happy with hell_dissolve\n"
    "    show lan coat happy at hell_pose with hell_fade\n"
    "    show lan happy@hell_sway behind lan as lan_alt onlayer master"
    " zorder 5 with hell_dissolve\n"
    "    show expression holo(\"lan aa happy\") as holo_pic\n"
    "    show screen hell_hud\n"
    "    show layer master\n"
    "    show lan ghost happy\n"
    "    return\n"
)

#: 8×8 的小图：上半 / 下半两个颜色（透明用来验证“衣服层只盖下半身”）。
_HAPPY = ((255, 0, 0, 255), (0, 0, 0, 0))
_INDIFFERENT = ((128, 128, 128, 255), (0, 0, 0, 0))
_COAT = ((0, 0, 0, 0), (0, 0, 255, 255))
_CLOTHES2 = ((0, 0, 0, 0), (0, 160, 0, 255))


def _paint(path: Path, colors) -> None:
    """写一张 8×8 的 PNG：`colors` = (上半 RGBA, 下半 RGBA)。"""
    from PIL import Image
    top, bottom = colors
    img = Image.new('RGBA', (8, 8), (0, 0, 0, 0))
    for y in range(8):
        for x in range(8):
            img.putpixel((x, y), top if y < 4 else bottom)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, 'PNG')


def _make(tmp_path: Path) -> Path:
    """造一个“定义散在九个文件里”的真工程（每个用例一个独立临时项目）。"""
    game = tmp_path / 'game'
    files = {
        'data/wardrobe.rpy': WARDROBE,
        'data/define.rpy': DEFINES,
        'helpers/crop.rpy': CROP_HELPER,
        'definitions/lan.rpy': LAN_DEF,
        'definitions/alias.rpy': ALIAS_DEF,
        'stage/poses.rpy': POSES,
        'fx/transitions.rpy': TRANSITIONS,
        'screens/hud.rpy': SCREEN,
        'chapters/ch1.rpy': CHAPTER,
    }
    for rel, text in files.items():
        fp = game / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(text, encoding='utf-8')
    images = game / 'images' / 'characters' / 'lan'
    _paint(images / 'happy.png', _HAPPY)
    _paint(images / 'indifferent.png', _INDIFFERENT)
    _paint(images / 'coat.png', _COAT)
    _paint(images / 'clothes2.png', _CLOTHES2)
    return tmp_path


def _symbols(root: Path):
    """真 `AssetRegistry` + 真 `Symbols`（走完整扫描链路，不灌白盒）。"""
    from storygraph.assets.asset_registry import AssetRegistry
    from storygraph.story.dsl import Symbols

    reg = AssetRegistry()
    reg.project_root = str(root)
    # 一张表数据：`known_tag_warning` 要 `has('image')` 才肯判
    reg.images['lan_happy'] = {
        'ID': 'lan_happy', 'Tag': 'lan', 'Attributes': 'happy indifferent',
        'Path': 'game/images/characters/lan/happy.png',
        'Category': 'character',
    }
    return Symbols(reg), reg


def _hint(root: Path, spec: str) -> str:
    sym, _reg = _symbols(root)
    return known_tag_warning(spec, sym)


def _rgba(path, xy) -> tuple:
    """打开预览图读一个像素（“显示正确”的硬判据）。"""
    from PIL import Image
    with Image.open(path) as img:
        return img.convert('RGBA').getpixel(tuple(xy))


@pytest.fixture
def preview_cache(tmp_path, monkeypatch):
    """合成预览图写进临时目录（不许碰用户真实的缩略图缓存）。"""
    from storygraph.app import layer_compose
    monkeypatch.setattr(layer_compose, '_default_cache_dir',
                        lambda: tmp_path / 'preview-cache')
    return tmp_path / 'preview-cache'


# ---------------------------------------------------------------------------
#  一、定义写在哪都要认
# ---------------------------------------------------------------------------

class TestDefinitionsAcrossFiles:
    """自定义结构分散在不同文件/目录，检测器必须全部读到。"""

    def test_nested_dict_in_another_directory(self, tmp_path):
        """`aa` 只在 `data/wardrobe.rpy` 的嵌套 dict 里，没有 png。"""
        root = _make(tmp_path)
        assert not (root / 'game/images/characters/lan/aa.png').exists()
        assert _hint(root, 'lan aa happy') == ''

    def test_sibling_key_in_the_same_nested_dict(self, tmp_path):
        assert _hint(_make(tmp_path), 'lan coat happy') == ''

    def test_list_element_for_another_character(self, tmp_path):
        """`hanfu` 在 `{"ling": [...]}` 里 —— 与 tag 无关，也是项目定义的词。"""
        assert _hint(_make(tmp_path), 'lan hanfu happy') == ''

    def test_tuple_element(self, tmp_path):
        """`towel` 只在 tuple 里（不是 dict 键）。"""
        assert _hint(_make(tmp_path), 'lan towel happy') == ''

    def test_top_level_define_statement(self, tmp_path):
        """`hat` 只在一个顶层 `define [...]` 里。"""
        assert _hint(_make(tmp_path), 'lan hat happy') == ''

    def test_renpy_image_registration(self, tmp_path):
        """`renpy.image("lan sigil_77", …)` —— 引擎认，工具也要认。"""
        assert _hint(_make(tmp_path), 'lan sigil_77') == ''

    def test_image_statement_with_unicode_name(self, tmp_path):
        """`image lan 符_77 = …`（别名文件里的另一条声明）。"""
        assert _hint(_make(tmp_path), 'lan 符_77') == ''

    def test_dict_value_is_not_a_word(self, tmp_path):
        """`{"aa": "fallback_word"}` 的值不是属性代号 —— 必须报。"""
        msg = _hint(_make(tmp_path), 'lan fallback_word happy')
        assert 'fallback_word' in msg

    def test_definitions_survive_a_new_symbols_instance(self, tmp_path):
        """改完 `.rpy` 再建 `Symbols` → 新词立刻认（不吃旧缓存）。"""
        root = _make(tmp_path)
        sym, _ = _symbols(root)
        assert 'hat' in sym.defined_words()
        (root / 'game/data/define.rpy').write_text(
            'define HELL_TAGS = ["hat", "scarf"]\n', encoding='utf-8')
        sym2, _ = _symbols(root)
        assert 'scarf' in sym2.defined_words()


# ---------------------------------------------------------------------------
#  二、只差一个定义的孪生词必须点名
# ---------------------------------------------------------------------------

class TestTypoTwinsAreReported:
    """不误报的另一面是**不漏报**：没定义的词必须点名，且只点坏词。"""

    @pytest.mark.parametrize('bad, spec', [
        ('hat2', 'lan hat2 happy'),
        ('aa2', 'lan aa2 happy'),
        ('sigil_78', 'lan sigil_78'),
        ('符_78', 'lan 符_78'),
        ('ghost', 'lan ghost happy'),
        ('based', 'lan based at hell_pose'),
    ])
    def test_typo_is_named(self, tmp_path, bad, spec):
        msg = _hint(_make(tmp_path), spec)
        assert bad in msg, f'没点名 {bad}：{msg!r}'

    def test_usage_in_the_script_is_not_a_definition(self, tmp_path):
        """`ch1.rpy` 里写了 `show lan ghost happy` —— 使用不是定义。"""
        root = _make(tmp_path)
        assert 'ghost' in (root / 'game/chapters/ch1.rpy').read_text(
            encoding='utf-8')
        assert 'ghost' in _hint(root, 'lan ghost happy')

    def test_good_words_are_not_named_alongside_the_typo(self, tmp_path):
        msg = _hint(_make(tmp_path), 'lan hat happy hat2')
        assert 'hat2' in msg
        # “没有「…」这个属性”那一句里只许有坏词
        quoted = msg.split('没有「', 1)[1].split('」这个属性', 1)[0]
        assert 'hat2' in quoted
        assert 'happy' not in quoted

    def test_deleting_the_defining_file_flips_the_verdict(self, tmp_path):
        """`aa` 的定义只在 `data/wardrobe.rpy`；删掉它 → 立刻改判。"""
        root = _make(tmp_path)
        assert _hint(root, 'lan aa happy') == ''
        (root / 'game/data/wardrobe.rpy').unlink()
        assert 'aa' in _hint(root, 'lan aa happy')

    def test_transform_named_like_the_typo_does_not_rescue_it(self, tmp_path):
        """`transform based:` 是变换，不是属性 —— `lan based` 仍然要报。"""
        root = _make(tmp_path)
        (root / 'game/stage/poses.rpy').write_text(
            POSES + '\ntransform based:\n    xalign 0.5\n', encoding='utf-8')
        assert 'based' in _hint(root, 'lan based')


# ---------------------------------------------------------------------------
#  三、with / at / transform 不是属性
# ---------------------------------------------------------------------------

class TestClauseWordsAreNotAttributes:
    """整条 show 尾巴递进来时，只判图像名那一截。"""

    @pytest.mark.parametrize('spec', [
        'lan aa happy with hell_dissolve',
        'lan coat happy at hell_pose',
        'lan happy@hell_sway',
        'lan hat happy behind lan as lan_alt onlayer master zorder 5',
        'lan towel happy with hell_fade',
        'lan happy:',
        'lan happy with hell_dissolve:',
    ])
    def test_clause_tails_are_silent(self, tmp_path, spec):
        assert _hint(_make(tmp_path), spec) == ''

    @pytest.mark.parametrize('spec', [
        'expression holo("lan aa happy")',
        'screen hell_hud',
        'layer master',
        'text "hello"',
    ])
    def test_non_image_show_forms_are_silent(self, tmp_path, spec):
        """`show expression` / `show screen` / `show layer` 不是图像名。"""
        assert _hint(_make(tmp_path), spec) == ''

    def test_typo_before_the_clause_is_still_reported(self, tmp_path):
        msg = _hint(_make(tmp_path), 'lan based with hell_dissolve')
        assert 'based' in msg
        assert 'hell_dissolve' not in msg.split('没有「', 1)[1].split(
            '」这个属性', 1)[0]

    def test_custom_transforms_are_parsed_from_their_own_file(self, tmp_path):
        """transform 的定义在 `stage/poses.rpy`：解析器要真的读到它。"""
        from storygraph.parsing.renpy_parser import (
            ParseResultBuilder, RenPyParser)
        root = _make(tmp_path)
        nodes = RenPyParser().parse(str(root / 'game/stage/poses.rpy'))
        res = ParseResultBuilder().build(nodes)
        assert {'hell_pose', 'hell_sway'} <= set(res.transforms)
        assert res.transforms['hell_sway'].params == 'amount=0.3'

    def test_custom_transitions_are_read_from_their_own_file(self, tmp_path):
        """`with` 用的 `define … = Dissolve(…)` 在 `fx/transitions.rpy`：
        不只是被当子句切掉，工具要真的能说出它是什么效果。"""
        from storygraph.story.transition_catalog import (
            scan_project_transitions)
        catalog = scan_project_transitions(str(_make(tmp_path)))
        assert catalog['hell_dissolve'].kind == 'dissolve'
        assert abs(catalog['hell_dissolve'].duration - 0.2) < 1e-6
        assert catalog['hell_fade'].kind == 'fade'


# ---------------------------------------------------------------------------
#  四、真卡片：预览里显示的那张图
# ---------------------------------------------------------------------------

class _Provider:
    """窗口挂在卡片上的 `_for_tag` provider（维度结构从项目声明读）。"""

    def __init__(self, root: Path):
        from storygraph.story.layered_model import load_layer_model_cached
        self.project_root = str(root)
        self.layer_model = load_layer_model_cached(str(root))
        self.engine_facts = {}
        self.attr_groups = []


class _Loader:
    def __init__(self, root: Path):
        self.project_root = str(root)

    def request(self, *_a, **_k):
        pass


def _card(root: Path, spec: str, at: str = ''):
    """真卡片控件（卡片编辑器里那张立绘卡）。"""
    from storygraph.ui_qt.sprite_grid import SpriteCardWidget

    sym, _reg = _symbols(root)
    return SpriteCardWidget(
        {'image': spec, 'expression': '', 'position': '', 'at': at,
         'speaking': False, 'order_index': 0, 'extra_rows': []},
        symbols=sym,
        loader=_Loader(root),
        on_resolve_path=None,
        choice_groups_for=_Provider(root),
        assets_ready=True,
    )


class TestCardPreviewShowsTheRightPicture:
    """用户要的验收：**预览里被选中的图片，修改后显示正确**。

    不是问“它是不是 png”，而是打开缩略图落盘的那张图，看像素。
    """

    def test_preview_for_the_outfit_code_is_the_face_file(self, qapp, tmp_path,
                                                          preview_cache):
        root = _make(tmp_path)
        card = _card(root, 'lan aa happy')
        path = card._thumb_path('lan aa happy')
        assert path is not None
        assert Path(path).name == 'happy.png'
        assert _rgba(path, (0, 0)) == (255, 0, 0, 255)

    def test_preview_changes_after_switching_the_outfit(self, qapp, tmp_path,
                                                        preview_cache):
        root = _make(tmp_path)
        card = _card(root, 'lan aa happy')
        before = card._thumb_path('lan aa happy')
        card._apply_image_spec('lan coat happy')
        after = card._thumb_path('lan coat happy')
        assert Path(after).name != Path(before).name
        # 合成结果：上半张 = 裁下来的脸（红），下半身 = 衣服（蓝）
        assert _rgba(after, (0, 0)) == (255, 0, 0, 255)
        assert _rgba(after, (0, 7)) == (0, 0, 255, 255)

    def test_preview_goes_back_when_the_outfit_is_removed(self, qapp, tmp_path,
                                                          preview_cache):
        root = _make(tmp_path)
        card = _card(root, 'lan aa happy')
        card._apply_image_spec('lan coat happy')
        assert Path(card._thumb_path('lan coat happy')).name != 'happy.png'
        card._apply_image_spec('lan aa happy')
        path = card._thumb_path('lan aa happy')
        assert Path(path).name == 'happy.png'
        assert _rgba(path, (0, 7))[3] == 0          # 下半身透明（没有衣服层）

    def test_the_outfit_chip_changes_the_spec_and_the_preview(self, qapp,
                                                              tmp_path,
                                                              preview_cache):
        """真的点那一格（`_set_slot_value` 是 chip 的写入点）。"""
        root = _make(tmp_path)
        card = _card(root, 'lan aa happy')
        groups = {str(r.get('group') or '') for r in card._slot_rows()}
        assert {'expressions', 'outfit'} <= groups, groups
        card._set_slot_value('outfit', 'coat')
        assert 'coat' in card.item['image']
        path = card._thumb_path(card.item['image'])
        assert Path(path).name != 'happy.png'
        assert _rgba(path, (0, 7)) == (0, 0, 255, 255)

    def test_card_silences_the_custom_clauses(self, qapp, tmp_path):
        """卡片把 `at` 单独存一列；自定义 transform 名不许被当属性。"""
        root = _make(tmp_path)
        for spec, at in (
                ('lan aa happy', 'hell_pose'),
                ('lan coat happy', 'hell_sway'),
                ('lan towel happy', '')):
            card = _card(root, spec, at=at)
            assert card._hint_text == '', (spec, at, card._hint_text)

    def test_card_reports_the_typo_twin(self, qapp, tmp_path):
        card = _card(_make(tmp_path), 'lan hat2 happy')
        assert 'hat2' in card._hint_text

    def test_card_image_with_a_with_clause_still_resolves(self, qapp, tmp_path,
                                                         preview_cache):
        """整条 show 尾巴被塞进 `image` 字段时，缩略图仍按图像名那一截解析。"""
        card = _card(_make(tmp_path), 'lan aa happy with hell_dissolve')
        assert card._hint_text == ''
        assert Path(card._thumb_path(card.item['image'])).name == 'happy.png'

    def test_non_image_show_is_not_called_missing(self, qapp, tmp_path):
        """`show expression` / `screen` 不是图像名 —— 预览说“判不了”，不说“没有”。"""
        root = _make(tmp_path)
        for spec in ('expression holo("lan aa happy")', 'screen hell_hud'):
            card = _card(root, spec)
            assert card._hint_text == '', (spec, card._hint_text)
            assert card._thumb.text() == '非图像', (spec, card._thumb.text())


# ---------------------------------------------------------------------------
#  五、舞台预览走同一条解析
# ---------------------------------------------------------------------------

class TestStagePreviewUsesTheSameResolution:
    """预览不只有卡片缩略图：舞台那一帧也走 `resolve_layers`。"""

    def _plan(self, root: Path, spec: str):
        from storygraph.app.stage import sprite_plan
        from storygraph.story.layered_model import load_layer_model_cached
        model = load_layer_model_cached(str(root))
        return sprite_plan(None, types.SimpleNamespace(image=spec), model, {},
                           str(root))

    def test_outfit_code_plan_is_the_single_face_picture(self, tmp_path):
        root = _make(tmp_path)
        plan = self._plan(root, 'lan aa happy')
        assert plan.ok
        assert [Path(p).name for p in plan.paths()] == ['happy.png']

    def test_switching_outfit_gives_a_different_composed_picture(
            self, tmp_path, preview_cache):
        from storygraph.app.layer_compose import compose_sprite
        root = _make(tmp_path)
        plain = compose_sprite(self._plan(root, 'lan aa happy'))
        dressed = compose_sprite(self._plan(root, 'lan coat happy'))
        assert Path(plain).name != Path(dressed).name
        assert _rgba(dressed, (0, 0)) == (255, 0, 0, 255)
        assert _rgba(dressed, (0, 7)) == (0, 0, 255, 255)


# ---------------------------------------------------------------------------
#  六、真机结构：有变量定义、没有 png（`aa`）
# ---------------------------------------------------------------------------

class TestOutfitCodeWithoutAnyPng:
    """2026-11-05 用户纠正的真机：`aa` 是换装表里的服装代号。

    `aa` **不对应任何 png**，也不是 `layeredimage` 的 attribute；
    `show lan aa happy` 在真机里完全能跑 —— lint 的 `is not an image`
    查的是 image 注册表，不许拿它推翻项目变量。
    """

    def test_outfit_code_is_silent_even_without_a_png(self, tmp_path):
        root = _make(tmp_path)
        assert not (root / 'game/images/characters/lan/aa.png').exists()
        assert _hint(root, 'lan aa happy') == ''

    def test_outfit_code_survives_a_stale_engine_cache(self, tmp_path,
                                                       monkeypatch):
        """缓存说 `lan|aa|happy` 不是图像 —— 声明过的 tag 不许被它推翻。"""
        root = _make(tmp_path)
        from storygraph.story import dsl as _dsl
        monkeypatch.setattr(_dsl.Symbols, 'engine_bad_keys',
                            lambda self: {'lan|aa|happy'})
        assert _hint(root, 'lan aa happy') == ''

    def test_typo_points_at_the_real_picture(self, tmp_path):
        msg = _hint(_make(tmp_path), 'lan based')
        assert 'based' in msg
        assert 'happy.png' in msg, msg

    def test_card_is_silent_for_the_outfit_code(self, qapp, tmp_path):
        card = _card(_make(tmp_path), 'lan aa happy')
        assert card._hint_text == '', card._hint_text
