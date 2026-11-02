# -*- coding: utf-8 -*-
"""
tests/test_declared_layered_identity.py
=======================================
**项目自己声明的 `layeredimage` = 角色**（用户 2026-11-02）。

用户原话：

> 新bug：qing 明明是个角色……？为何她没被登记……？？？并且她明明有预览图……
> 为什么不能导入……？实际上这个预览图就是她的立绘……

根因两条，都在"项目数据没人读"上：

  ① 已知角色 tag 只有三路（角色表「立绘 tag」/ 映射 `characters` / 引擎事实）。
     但角色表登记的是**会说话的人**：她由别的角色出声，自己只有一条
     `layeredimage` 声明 —— 于是她的立绘、预览图、头像全被归成"其他图片"，
     选择器里连"立绘：她"这一组都不存在；
  ② 她的属性名和图片文件名**不是同一个词**（`attribute curious: 'stare'`）。
     换真立绘时按文件名硬写 `show her stare`，引擎不认 —— 又是一个
     "看着像对、其实非法"的 show。

这里钉住：项目 `.rpy` 声明过的 tag 进"已知角色"、预览图能换到真立绘、
换出来写的是**声明过的属性词**，以及"项目没声明时行为一个字节都不变"。

⚠ 通用性护栏：fixture 一个真实项目的名字都不用（角色叫 `delta`，
目录叫 `game/preview/`），专门证明"换个项目也认"。
"""

from __future__ import annotations

import pytest

from storygraph.app import picker as P
from storygraph.assets import asset_identity as AI
from storygraph.assets.asset_registry import AssetRegistry


#: 一个"角色表只登记会说话的人"的项目：`delta` 由 `d` 出声，
#: 自己只有一条 `layeredimage`，而且**属性名 != 图片文件名**。
_LAYERED_RPY = '''\
layeredimage delta:
    image_format 'images/characters/delta/{image}.png'

    attribute plain default:
        'normal'

    group mood:
        attribute shy:
            'quiet'
        attribute cross default:
            'quiet'
'''


def _rows() -> dict:
    """资源表行：真立绘（Tag 是 delta）+ 预览副本（Tag 空）+ 角色表里的出声人。"""
    def img(aid, path, tag='', attrs=''):
        return {'ID': aid, 'Path': path, 'Tag': tag, 'Attributes': attrs}

    return {
        'characters_quiet': img('characters_quiet',
                                'game/images/characters/delta/quiet.png',
                                'delta', 'quiet'),
        'characters_normal': img('characters_normal',
                                 'game/images/characters/delta/normal.png',
                                 'delta', 'normal'),
        'delta_quiet': img('delta_quiet', 'game/preview/delta/quiet.png',
                           '', 'quiet'),
        'normal': img('normal', 'game/preview/delta/normal.png', ''),
    }


def _registry(rows=None) -> AssetRegistry:
    reg = AssetRegistry()
    reg.images = dict(rows if rows is not None else _rows())
    # 出声的角色变量没有 image=（`ImageTag` 空）→ 退回变量名 `d`；
    # 真立绘的 tag 是 `delta` —— 这正是"角色表认不出他"的那种项目。
    reg.characters = {'d': {'ID': 'd', 'Name': 'Delta', 'ImageTag': ''}}
    return reg


@pytest.fixture()
def declared(tmp_path):
    """有 `layeredimage delta:` 声明的项目根。"""
    game = tmp_path / 'game'
    game.mkdir()
    (game / 'defs.rpy').write_text(_LAYERED_RPY, encoding='utf-8')
    return tmp_path


@pytest.fixture()
def undeclared(tmp_path):
    """**同一个工程、但没有这条声明**（改前行为必须一字节不变）。"""
    (tmp_path / 'game').mkdir()
    return tmp_path


# ==================================================================
#  ① 项目声明过的 tag 就是"已知角色"
# ==================================================================

class TestDeclaredTagIsAKnownCharacter:
    def test_layered_tag_joins_known_tags(self, declared):
        ctx = AI.project_context(_registry(), str(declared))
        assert 'delta' in ctx['known_tags']
        assert 'd' in ctx['known_tags'], '角色表的那个 tag 仍然在'

    def test_without_the_declaration_it_is_not_known(self, undeclared):
        """没有声明时**不猜**：`delta` 不是已知角色（改前就是这样）。"""
        ctx = AI.project_context(_registry(), str(undeclared))
        assert 'delta' not in ctx['known_tags']
        assert ctx['sprite_words'] == {}

    def test_words_come_from_the_declaration(self, declared):
        words = AI.declared_sprite_words(str(declared))
        assert words['delta'] == {'normal': 'plain', 'quiet': 'cross'}, words
        assert set(AI.declared_sprite_tags(str(declared))) == {'delta'}

    def test_empty_root_is_empty(self):
        assert AI.declared_sprite_words('') == {}
        assert AI.declared_sprite_tags('') == set()


# ==================================================================
#  ② 身份：真立绘的属性词换成**声明过的写法**
# ==================================================================

class TestDeclaredWordsOnIdentity:
    def test_real_sprite_uses_declared_attribute(self, declared):
        reg = _registry()
        got = P.identity_of(reg, 'characters_quiet', project_root=str(declared))
        assert got.kind == 'sprite' and got.tag == 'delta'
        assert got.attrs == ('cross',), got.attrs
        assert AI.spec_for_identity(got) == 'delta cross'

    def test_preview_keeps_the_raw_word(self, declared):
        """
        预览图/头像的属性词**不许换**：`find_real_sprite` 要拿它和行里的原词
        （`Attributes` 列 / 文件名）对表 —— 换了就找不到那张真立绘。
        """
        reg = _registry()
        got = P.identity_of(reg, 'delta_quiet', project_root=str(declared))
        assert got.kind == 'preview' and got.tag == 'delta'
        assert got.attrs == ('quiet',), got.attrs

    def test_no_declaration_keeps_the_file_name(self, undeclared):
        got = AI.resolve_image(
            _rows()['characters_quiet'],
            relpath='game/images/characters/delta/quiet.png')
        assert got.attrs == ('quiet',), '没有声明就该按文件名，不许编属性词'


# ==================================================================
#  ③ 选中预览图 → 换成真立绘，写出的 spec 引擎认
# ==================================================================

class TestPickPreviewOfADeclaredCharacter:
    def _pick(self, reg, aid, root):
        return P.resolve_pick(aid, reg.images.get(aid), registry=reg,
                              project_root=str(root))

    def test_preview_becomes_the_real_sprite_with_declared_word(self, declared):
        reg = _registry()
        got = self._pick(reg, 'delta_quiet', declared)
        assert got.ok is True, got.message
        assert got.spec == 'delta cross', got.spec
        assert got.replaced_from == 'delta_quiet'
        assert got.replaced_to == 'characters_quiet'
        assert '已用对应的立绘' in got.message

    def test_default_attribute_is_used_for_the_default_face(self, declared):
        reg = _registry()
        got = self._pick(reg, 'normal', declared)
        assert got.ok is True, got.message
        assert got.spec == 'delta plain', got.spec

    def test_without_the_declaration_it_is_still_refused(self, undeclared):
        """改前的行为：认不出这个人 → 拒绝并给一句话（**不许**瞎写 show）。"""
        reg = _registry()
        got = self._pick(reg, 'delta_quiet', undeclared)
        assert got.ok is False
        assert got.spec == ''
        assert '没找到' in got.message

    def test_pick_context_carries_the_words(self, declared):
        ctx = P.pick_context(_registry(), str(declared))
        assert ctx['sprite_words']['delta'] == {
            'normal': 'plain', 'quiet': 'cross'}

    def test_categorize_puts_her_in_a_sprite_group(self, declared):
        """选择器的"立绘：delta"这一组必须真的出现（用户的直接诉求）。"""
        from storygraph.app.beats import categorize_assets

        class _Engine:
            def __init__(self, reg, root):
                self._reg, self.project_root = reg, root

            def get_registry(self):
                return self._reg

        groups = categorize_assets(_Engine(_registry(), str(declared)))
        assert '立绘：delta' in groups, list(groups)
        # 组内按**属性**排（cross < plain），不是按 ID
        assert [a for _, a in groups['立绘：delta']] == [
            'characters_quiet', 'characters_normal']
