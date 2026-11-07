"""
立绘 tag 的**归属**：有素材 ≠ 有角色（2026-11-03）
================================================

用户实测（截图）：卡片里写 `qing base`，提示说
「`qing` 不是已知的角色立绘标签 —— 去『资源映射』把它认成角色/立绘」，
而**预览里明明有一张 qing 的立绘**。真机数据：

| 事实 | 值 |
|---|---|
| `characters.xlsx` 里要角色的变量 | `q`（显示名「青」） |
| 它的「立绘 tag」列 | **空的** |
| 磁盘上 | `game/images/characters/qing/{normal,speechless,stare,thinking}.png`（4 张真立绘，4320×7680） |
| images.xlsx 里 ID=`qing` 的那行 | `game/phone_msg/qing.png`，分类 `ui`（目录段 `phone` 命中界面词） |

所以用户看到的"分类为界面"是**另一张图**（手机消息里的同一张头像），而
真立绘确实存在、只是**没有角色认领** —— 工具却把这两件事混成一句
"不是已知的角色立绘标签，去资源映射"。

这个文件守三条：

  1. `is_under_sprite_container` 只认**写出来的**目录名（`characters` /
     `立绘` / `sprite`…），不认"位置猜测" —— 否则 `images/nbg/` 那 42 张
     背景（被位置猜测判成 `character`）会被当成"一套立绘"；
  2. `Symbols.has_sprites` / `sprite_tags_without_owner` 用同一判据；
  3. 卡片提示分三层：角色认领（闭嘴）／有素材没认领（说清有几张、属性名
     写错时直接点出来、指向角色表）／什么都没有（老文案）。

⚠ **不猜"它属于哪个角色"**：`qing` → `q`（青）是拼音联想，那是猜。
工具只说"有 4 张、没有角色认领"，剩下的由人填（铁律：猜一个名字比说
不知道坏得多）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.assets.asset_identity import is_under_sprite_container  # noqa: E402
from storygraph.assets.asset_registry import AssetRegistry             # noqa: E402
from storygraph.story.dsl import Symbols                               # noqa: E402
from storygraph.ui_qt.sprite_grid import known_tag_warning             # noqa: E402


def _reg(images=None, characters=None):
    reg = AssetRegistry()
    for aid, row in (images or {}).items():
        reg.images[aid] = row
    for cid, row in (characters or {}).items():
        reg.characters[cid] = row
    return reg


def _sprite(tag, attr, path=None):
    return {'ID': f'{tag}_{attr}', 'Tag': tag, 'Attributes': attr,
            'Value': attr,
            'Path': path or f'game/images/characters/{tag}/{attr}.png',
            'Category': 'character'}


class TestSpriteContainerIsWrittenEvidence:
    def test_character_container(self):
        assert is_under_sprite_container(
            'game/images/characters/qing/normal.png') is True

    def test_chinese_container(self):
        assert is_under_sprite_container('game/立绘/qing/base.png') is True

    def test_sprite_word(self):
        assert is_under_sprite_container('game/sprites/lan/happy.png') is True

    def test_background_dir_is_not_a_sprite_container(self):
        """`images/nbg/bg1.1.png` 被位置猜测判成 character —— 但它不是立绘。"""
        assert is_under_sprite_container('game/images/nbg/bg1.1.png') is False

    def test_bare_images_root_is_not_enough(self):
        assert is_under_sprite_container('game/images/base.png') is False

    def test_ui_dir_is_not_a_sprite_container(self):
        assert is_under_sprite_container('game/phone_msg/qing.png') is False


class TestSymbolsKnowsWhichTagsHaveSprites:
    def _symbols(self):
        return Symbols(_reg(
            images={
                'qing_normal': _sprite('qing', 'normal'),
                'qing_speechless': _sprite('qing', 'speechless'),
                'nbg_bg11': {'ID': 'nbg_bg11', 'Tag': 'nbg',
                             'Attributes': 'bg1.1', 'Value': 'bg1.1',
                             'Path': 'game/images/nbg/bg1.1.png',
                             'Category': 'character'},
                'lan_happy': _sprite('lan', 'happy'),
            },
            characters={'l': {'ID': 'l', 'Name': '「蓝」', 'ImageTag': 'lan'}},
        ))

    def test_tag_in_sprite_dir_has_sprites(self):
        assert self._symbols().has_sprites('qing') is True

    def test_background_tag_looks_like_no_sprites(self):
        """位置猜测出来的 character 不算"有立绘"。"""
        assert self._symbols().has_sprites('nbg') is False

    def test_owner_claimed_tag_is_not_orphan(self):
        orphans = self._symbols().sprite_tags_without_owner()
        assert 'lan' not in orphans          # 角色 `l` 认领了
        assert 'qing' in orphans             # 没人认领
        assert orphans['qing'] == ['normal', 'speechless']

    def test_background_tag_never_listed_as_orphan(self):
        assert 'nbg' not in self._symbols().sprite_tags_without_owner()

    def test_empty_registry(self):
        sym = Symbols(_reg())
        assert sym.has_sprites('qing') is False
        assert sym.sprite_tags_without_owner() == {}


class TestTheWarningSaysTheUsefulThing:
    def _symbols(self):
        return Symbols(_reg(
            images={
                'qing_normal': _sprite('qing', 'normal'),
                'qing_speechless': _sprite('qing', 'speechless'),
                'lan_happy': _sprite('lan', 'happy'),
                'qing_png': {'ID': 'qing', 'Tag': 'qing',
                             'Path': 'game/phone_msg/qing.png',
                             'Category': 'ui', 'Attributes': ''},
            },
            characters={'l': {'ID': 'l', 'Name': '「蓝」', 'ImageTag': 'lan'}},
        ))

    def test_claimed_tag_is_silent(self):
        assert known_tag_warning('lan happy', self._symbols()) == ''

    def test_missing_attr_is_named(self):
        """真机那句 `qing base`：**没有 base 这个属性**，不是"不是立绘"。"""
        msg = known_tag_warning('qing base', self._symbols())
        assert 'base' in msg
        assert '没有' in msg
        assert 'normal' in msg and 'speechless' in msg   # 把它有的列出来
        assert '不是已知的角色立绘标签' not in msg

    def test_existing_attr_but_no_owner_points_at_character_table(self):
        msg = known_tag_warning('qing normal', self._symbols())
        assert '角色表' in msg
        assert '没有角色认领' in msg
        assert '资源映射' not in msg

    def test_tag_without_any_sprite_keeps_the_old_wording(self):
        msg = known_tag_warning('nosuch happy', self._symbols())
        assert '不是已知的角色立绘标签' in msg

    def test_background_tag_is_not_excused_as_a_sprite(self):
        """`nbg` 是背景（位置猜测判成 character）—— 不许说"它有立绘"。

        ⚠ 必须**带一张非空角色表**才有意义：角色表为空时 `is_actor_tag`
        走的是"无从判定就放行"那一支（任何有图的 tag 都算角色），
        根本走不到这里。
        """
        reg = _reg(
            images={
                'nbg_bg': {'ID': 'nbg_bg', 'Tag': 'nbg',
                           'Attributes': 'bg1.1',
                           'Path': 'game/images/nbg/bg1.1.png',
                           'Category': 'character'},
            },
            characters={'l': {'ID': 'l', 'ImageTag': 'lan'}},
        )
        msg = known_tag_warning('nbg bg1.1', Symbols(reg))
        assert '不是已知的角色立绘标签' in msg

    def test_no_symbols_means_no_warning(self):
        assert known_tag_warning('qing base', None) == ''

    def test_empty_spec_is_silent(self):
        assert known_tag_warning('', self._symbols()) == ''

    def test_registry_without_any_table_is_silent(self):
        """一张表都没有 → 无从判定，不许报警（假警报比不提示更糟）。

        ⚠ 有角色表、没图片表时**要**报警：那时"角色表里没有 qing"是有效结论。
        """
        assert known_tag_warning('qing base', Symbols(_reg())) == ''

    def test_characters_only_still_warns(self):
        sym = Symbols(_reg(characters={'l': {'ID': 'l', 'ImageTag': 'lan'}}))
        assert '不是已知的角色立绘标签' in known_tag_warning('qing base', sym)


class TestProjectDeclarationWins:
    """**项目脚本自己声明过** = 最硬的证据（2026-11-03 第二版）。

    第一版只看"资源表里有没有这个 tag 的图"，于是用户从选择器里选完
    （选择器写出 `qing base`，因为 `layeredimage qing:` 里
    `attribute base default: "normal"`），卡片又警告"没有 base 这个属性"
    —— 工具在**反驳自己刚生成的东西**。
    """

    DECL = (
        'layeredimage qing:\n'
        "    image_format 'images/characters/qing/{image}.png'\n"
        '    attribute base default:\n'
        '        "normal"\n'
        '\n'
        '    group expressions:\n'
        '        attribute angry:\n'
        "            'speechless'\n"
    )

    def _symbols(self, tmp_path):
        """⚠ 白盒：直接给 `Symbols` 灌声明。

        为什么不在测试里解析真 `.rpy`：`layeredimage` → `{属性: 文件}` 那一层
        由 `layered_model` 自己的用例守着（它要真实图片文件、要 image_format
        展开）；这里要钉的是**提示的判定顺序**（声明 > 角色表 > 素材 > 都没有），
        两层分开测，红了才知道是哪一层。
        """
        reg = _reg(
            images={'qing_normal': _sprite('qing', 'normal')},
            characters={'l': {'ID': 'l', 'ImageTag': 'lan'}},
        )
        sym = Symbols(reg)
        sym._declared_attrs['qing'] = ['angry', 'base']
        return sym

    def test_declared_attrs_are_read(self, tmp_path):
        sym = self._symbols(tmp_path)
        assert set(sym.declared_attrs_of('qing')) == {'base', 'angry'}

    def test_declared_attribute_is_silent(self, tmp_path):
        """核心：`qing base` 是合法的 —— 不许再警告。"""
        assert known_tag_warning('qing base', self._symbols(tmp_path)) == ''

    def test_bare_tag_is_silent(self, tmp_path):
        assert known_tag_warning('qing', self._symbols(tmp_path)) == ''

    def test_wrong_attribute_names_the_declared_ones(self, tmp_path):
        msg = known_tag_warning('qing nosuch', self._symbols(tmp_path))
        assert '没有「nosuch」这个属性' in msg
        assert 'base' in msg and 'angry' in msg

    def test_declaration_beats_the_registry_heuristic(self, tmp_path):
        """声明过的 tag 不该被"角色表没认领"那条再拦一次。"""
        msg = known_tag_warning('qing angry', self._symbols(tmp_path))
        assert msg == ''

    def test_no_declaration_falls_back_to_the_registry_layers(self):
        """没有声明 → 退回"角色表 / 素材"那两层，不崩。

        ⚠ 必须带一张**非空角色表**：角色表为空时 `is_actor_tag` 走的是
        "无从判定就放行"那一支（任何有图的 tag 都算角色），根本走不到这里。
        """
        sym = Symbols(_reg(
            images={'qing_normal': _sprite('qing', 'normal')},
            characters={'l': {'ID': 'l', 'ImageTag': 'lan'}},
        ))
        assert sym.declared_attrs_of('qing') == []
        assert known_tag_warning('qing base', sym) != ''
