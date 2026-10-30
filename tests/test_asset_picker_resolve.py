# -*- coding: utf-8 -*-
"""
tests/test_asset_picker_resolve.py
==================================
资产选择器**选中即校验**（用户 2026-10-30，④ 的第二半）。

用户原话：

> 只有文件名是角色名的可以匹配
> 不能直接放立绘图，直接放角色那个头像图就能动
> 立绘怎么一堆单独的分类，什么 relaxing，resigned —— 这不都是凌的表情吗

这一片要钉住的四件事（判定层由 S1 的 `asset_identity` / `resolve_pick`
提供，这里只测**界面有没有把判定摆出来、有没有照它拦人**）：

  1. 左侧分类**角色优先**：`立绘：凌跃捷（ling）` 里放这个角色的全部表情，
     不再每个表情一个分类；预览图/头像单独归组；
  2. **用途过滤**（全部/立绘/背景/CG/界面/角色预览图/其他）按 `Identity.kind`；
  3. 格子显示**人话名**，原始资产 ID / 相对路径在 tooltip；
  4. 选预览图 → 自动换真立绘并说明；换不到 / 认不出 → **禁止确定**并给原因。

判据只用真 Qt（offscreen）＋真注册表，不 mock 判定层 —— 这样 S1 的实现
真坏了这里会红。
"""

from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

# **先确认有 PySide6 再 import 任何 ui_qt 模块**（否则 CI 收集阶段整批 ERROR）。
pytest.importorskip('PySide6')

from PySide6.QtCore import QSettings  # noqa: E402


# ==================================================================
#  夹具：一个"像真项目"的最小工程
# ==================================================================

def _write_png(path: Path):
    """写一张**真能解码**的小 PNG（假文件头会让 Qt 刷 libpng 报错）。"""
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', (8, 8), (200, 30, 30, 255)).save(path, 'PNG')


#: 资源表里的图（覆盖 立绘 / 预览图 / 头像 / 背景 / CG / 界面 / 未识别）：
#: `(资产 ID, Tag, Attributes, 相对路径)`。
IMAGE_ROWS = [
    ('ling_happy', 'ling', 'happy',
     'game/images/characters/ling/happy.png'),
    ('ling_sad', 'ling', 'sad',
     'game/images/characters/ling/sad.png'),
    ('ling_refusing', 'ling', 'refusing',
     'game/images/characters/ling/refusing.png'),
    # ↓ 预览副本：Tag 空、靠路径 `<角色>/<表情>.png` 认出，且资源表里
    #   另有一行 Tag 非空、同 tag+同文件名 —— 契约里的"通用副本检测"。
    ('refusing', '', '', 'game/preview/characters/ling/refusing.png'),
    # ↓ 头像：目录段 avatar + 文件名恰好是角色 tag。
    ('avatar_ling', '', '', 'game/preview/characters/avatar/ling.png'),
    # ↓ 预览副本，但这个角色**没有同名表情的真立绘** → 必须拒绝（不许瞎写）
    ('unmatched_face', '', '',
     'game/preview/characters/ling/unmatched_face.png'),
    ('bg_room', 'bg', 'room', 'game/images/bg/room.png'),
    ('cg_01', 'cg', '01', 'game/images/cg/cg01.png'),
    ('ui_button', 'ui', 'button', 'game/gui/button.png'),
    ('mystery', '', '', 'game/art/mystery.png'),
    # ↓ `.rpy` 里 `image logo blink:` 字面量声明过（贴图在 ATL 块里，Path 空）：
    #   名字在引擎里真实存在，**必须仍然能从选择器加**（老行为不许回归）。
    ('logo_blink', 'logo', 'blink', ''),
]

#: 角色表（`characters.xlsx` 的 registry 形态）：显示名 + 立绘 tag。
CHARACTER_ROWS = {
    'ling': {'ID': 'ling', 'Name': '凌跃捷', 'ImageTag': 'ling',
             'Color': '#c8ffc8', 'Aliases': '凌|lyj', 'Notes': ''},
}


def _registry():
    from storygraph.assets.asset_registry import AssetRegistry
    reg = AssetRegistry()
    reg.images = {}
    for aid, tag, attrs, rel in IMAGE_ROWS:
        row = {'ID': aid, 'Tag': tag, 'Attributes': attrs, 'Path': rel}
        if not rel:
            # `Path` 空 + `_atl` 标记 = `.rpy` 里的 `image 名:` 块形式
            row['_atl'] = True
        reg.images[aid] = row
    reg.characters = dict(CHARACTER_ROWS)
    reg.music = {}
    reg.sounds = {}
    reg.voices = {}
    reg.transforms = {}
    return reg


def _build_project(tmp_path) -> Path:
    proj = tmp_path / 'proj'
    (proj / 'game').mkdir(parents=True)
    for _aid, _tag, _attrs, rel in IMAGE_ROWS:
        if rel:
            _write_png(proj / rel)
    return proj


def _make_picker(tmp_path, monkeypatch, **kwargs):
    """造一个选择器（偏好写临时 ini；参数透传，便于测 `purpose=` 预选）。"""
    from storygraph.ui_qt import asset_picker as AP

    ini = tmp_path / f'prefs-{len(str(tmp_path))}.ini'
    monkeypatch.setattr(
        AP, 'settings_factory',
        lambda: QSettings(str(ini), QSettings.IniFormat))
    proj = _build_project(tmp_path)
    return AP.AssetPickerDialog(None, registry=_registry(), project_root=proj,
                                types=['image'], want_image_spec=True, **kwargs)


@pytest.fixture
def picker(qapp, tmp_path, monkeypatch):
    """
    一个"选立绘"的选择器（`want_image_spec=True`）。

    ⚠ 偏好存储换成**临时 ini**：Windows 上 QSettings 的路径设置不一定生效，
    不换的话测试会改写用户真实的"底色"偏好。
    """
    dlg = _make_picker(tmp_path, monkeypatch)
    yield dlg
    dlg.close()


# ------------------------------------------------------------------
#  小工具
# ------------------------------------------------------------------

def _group_names(picker) -> list:
    return [picker._cats.item(i).text()
            for i in range(picker._cats.count())]


def _sprite_groups(picker) -> list:
    return [n for n in _group_names(picker) if n.startswith('立绘：')]


def _item_ids(picker) -> list:
    return [picker._items.item(i).data(0x0100)[1]          # Qt.UserRole
            for i in range(picker._items.count())]


def _select(picker, asset_id: str):
    for i in range(picker._items.count()):
        it = picker._items.item(i)
        if it.data(0x0100)[1] == asset_id:
            picker._items.setCurrentItem(it)
            return it
    raise AssertionError(f'网格里没有 {asset_id}（当前 {_item_ids(picker)}）')


def _item_of(picker, asset_id: str):
    for i in range(picker._items.count()):
        it = picker._items.item(i)
        if it.data(0x0100)[1] == asset_id:
            return it
    raise AssertionError(f'网格里没有 {asset_id}')


# ==================================================================
#  ① 分类角色优先：一个角色一个「立绘：」组
# ==================================================================

class TestGroupsAreRoleFirst:
    def test_one_group_per_character_not_per_expression(self, picker):
        groups = _sprite_groups(picker)
        assert len(groups) == 1, f'一个角色只该有一个立绘组：{groups}'
        name = groups[0]
        assert '凌跃捷' in name and 'ling' in name, name

    def test_the_group_holds_every_expression(self, picker):
        name = _sprite_groups(picker)[0]
        ids = [a for _t, a in picker._groups.get(name, [])]
        assert set(ids) >= {'ling_happy', 'ling_sad', 'ling_refusing'}, ids

    def test_previews_and_avatars_are_not_in_the_sprite_group(self, picker):
        name = _sprite_groups(picker)[0]
        ids = {a for _t, a in picker._groups.get(name, [])}
        assert 'refusing' not in ids, '预览副本不许混进立绘组'
        assert 'avatar_ling' not in ids, '头像不许混进立绘组'

    def test_preview_and_avatar_have_their_own_visible_group(self, picker):
        names = _group_names(picker)
        assert any(n.startswith('角色预览图') or n.startswith('角色头像')
                   for n in names), names

    def test_no_single_image_sprite_groups_left(self, picker):
        """旧版 110 个"每个表情一个分类"就是这一条炸的。"""
        for name in _sprite_groups(picker):
            assert len(picker._groups.get(name, [])) >= 2, (
                f'{name} 又是单图分组（表情被拆开卖了）')


# ==================================================================
#  ② 用途过滤（按 Identity.kind，不按分组名猜）
# ==================================================================

class TestPurposeFilter:
    def test_choosing_sprites_hides_backgrounds_and_previews(self, picker):
        from storygraph.ui_qt.asset_picker import PURPOSE_SPRITE
        picker._purpose_box.setCurrentText(PURPOSE_SPRITE)
        ids = _item_ids(picker)
        assert 'ling_happy' in ids, ids
        assert 'bg_room' not in ids, ids
        assert 'refusing' not in ids, ids
        assert 'cg_01' not in ids, ids

    def test_choosing_previews_shows_only_preview_kinds(self, picker):
        from storygraph.ui_qt.asset_picker import PURPOSE_PREVIEW
        picker._purpose_box.setCurrentText(PURPOSE_PREVIEW)
        ids = set(_item_ids(picker))
        assert ids, '预览图过滤后一条都不剩'
        assert ids <= {'refusing', 'avatar_ling', 'unmatched_face'}, ids

    def test_choosing_background_shows_only_background(self, picker):
        from storygraph.ui_qt.asset_picker import PURPOSE_BG
        picker._purpose_box.setCurrentText(PURPOSE_BG)
        assert set(_item_ids(picker)) == {'bg_room'}

    def test_irrelevant_groups_disappear_from_the_left_list(self, picker):
        from storygraph.ui_qt.asset_picker import PURPOSE_SPRITE
        picker._purpose_box.setCurrentText(PURPOSE_SPRITE)
        names = _group_names(picker)
        assert not any(n.startswith('背景') for n in names), names
        assert not any(n.startswith('CG') for n in names), names

    def test_back_to_all_restores_everything(self, picker):
        from storygraph.ui_qt.asset_picker import PURPOSE_ALL, PURPOSE_SPRITE
        before = set(_item_ids(picker))
        picker._purpose_box.setCurrentText(PURPOSE_SPRITE)
        picker._purpose_box.setCurrentText(PURPOSE_ALL)
        assert set(_item_ids(picker)) == before

    def test_purpose_argument_preselects_the_filter(self, qapp, tmp_path,
                                                    monkeypatch):
        """`pick_image(purpose='sprite')` 一打开就只看见立绘（第二阶段接线用）。"""
        dlg = _make_picker(tmp_path, monkeypatch, purpose='sprite')
        try:
            assert dlg._purpose_box.currentText() == '立绘'
            ids = _item_ids(dlg)
            assert 'ling_happy' in ids and 'bg_room' not in ids, ids
        finally:
            dlg.close()

    def test_an_unknown_purpose_value_falls_back_to_all(self, qapp, tmp_path,
                                                        monkeypatch):
        dlg = _make_picker(tmp_path, monkeypatch, purpose='不认识')
        try:
            assert dlg._purpose_box.currentText() == '全部'
            assert 'bg_room' in _item_ids(dlg)
        finally:
            dlg.close()

    def test_purpose_of_only_looks_at_kind(self):
        """纯函数：用途只由 `Identity.kind` 决定（分组名/路径不参与）。"""
        from dataclasses import dataclass

        from storygraph.ui_qt.asset_picker import (PURPOSE_BG, PURPOSE_CG,
                                                  PURPOSE_OTHER,
                                                  PURPOSE_PREVIEW,
                                                  PURPOSE_SPRITE, PURPOSE_UI,
                                                  purpose_of)

        @dataclass(frozen=True)
        class _I:
            kind: str

        assert purpose_of(_I('sprite')) == PURPOSE_SPRITE
        assert purpose_of(_I('preview')) == PURPOSE_PREVIEW
        assert purpose_of(_I('avatar')) == PURPOSE_PREVIEW
        assert purpose_of(_I('bg')) == PURPOSE_BG
        assert purpose_of(_I('cg')) == PURPOSE_CG
        assert purpose_of(_I('ui')) == PURPOSE_UI
        assert purpose_of(_I('other')) == PURPOSE_OTHER
        assert purpose_of(_I('unknown')) == PURPOSE_OTHER
        assert purpose_of(None) == PURPOSE_OTHER

    @pytest.mark.parametrize('raw,want', [
        ('', '全部'), ('all', '全部'), ('sprite', '立绘'), ('立绘', '立绘'),
        ('preview', '角色预览图'), ('avatar', '角色预览图'),
        ('bg', '背景'), ('背景', '背景'), ('cg', 'CG'), ('ui', '界面'),
        ('不认识的值', '全部'),
    ])
    def test_purpose_label_normalises_caller_values(self, raw, want):
        from storygraph.ui_qt.asset_picker import purpose_label
        assert purpose_label(raw) == want


# ==================================================================
#  ③ 人话名 + tooltip 里的原始 ID / 路径
# ==================================================================

class TestHumanLabels:
    def test_a_sprite_reads_as_display_name_plus_attribute(self, picker):
        texts = [picker._items.item(i).text()
                 for i in range(picker._items.count())]
        assert any('凌跃捷' in t and 'happy' in t for t in texts), texts
        # 不许再把内部 ID 当名字摆出来（`ling_happy` / `happy` 那种）
        assert not any(t == 'ling_happy' for t in texts), texts

    def test_a_preview_is_labelled_as_a_preview(self, picker):
        assert '预览图' in _item_of(picker, 'refusing').text()

    def test_the_tooltip_keeps_the_raw_id_and_relative_path(self, picker):
        tip = _item_of(picker, 'ling_happy').toolTip()
        assert 'ling_happy' in tip, tip
        assert 'game/images/characters/ling/happy.png' in tip, tip
        assert '凌跃捷' in tip or '立绘' in tip, tip

    def test_the_detail_panel_explains_the_verdict(self, picker):
        _select(picker, 'ling_happy')
        text = picker._detail.text()
        assert '判定' in text and '立绘' in text, text
        assert '依据' in text, text
        assert '能' in text, text

    def test_search_still_works_after_the_rewrite(self, picker):
        picker._search.setText('happy')
        assert _item_ids(picker) == ['ling_happy'], _item_ids(picker)
        picker._search.setText('')
        assert 'bg_room' in _item_ids(picker)


# ==================================================================
#  ④ 选中即校验：预览图自动换真立绘 / 认不出就禁止确定
# ==================================================================

class TestPreviewIsSwappedForTheRealSprite:
    def test_selecting_a_preview_says_which_sprite_is_used(self, picker):
        _select(picker, 'refusing')
        hint = picker._pick_hint.text()
        assert '已用对应的立绘' in hint, hint
        assert 'ling refusing' in hint, hint
        assert picker._ok.isEnabled(), '能换成真立绘时不该拦人'

    def test_confirming_writes_the_real_sprite_spec(self, picker):
        _select(picker, 'refusing')
        picker.accept()
        assert picker.image_spec == 'ling refusing', picker.image_spec
        assert picker.selection == ('image', 'ling_refusing'), picker.selection
        assert '已用对应的立绘' in picker.replaced_message

    def test_an_avatar_also_gets_swapped(self, picker):
        _select(picker, 'avatar_ling')
        assert picker._ok.isEnabled(), picker._pick_hint.text()
        picker.accept()
        assert picker.image_spec.startswith('ling'), picker.image_spec

    def test_a_normal_sprite_says_what_will_be_written(self, picker):
        _select(picker, 'ling_sad')
        assert '将写入：ling sad' in picker._pick_hint.text(), \
            picker._pick_hint.text()


class TestUnrecognizedIsRefusedOutLoud:
    def test_an_unknown_image_disables_ok_and_says_why(self, picker):
        _select(picker, 'mystery')
        assert not picker._ok.isEnabled(), '认不出的图不许加'
        hint = picker._pick_hint.text()
        assert '⚠' in hint and '认不出' in hint, hint

    def test_pressing_ok_anyway_does_not_write_anything(self, picker):
        _select(picker, 'mystery')
        picker.accept()
        assert picker.image_spec == '', '拒绝的图不许写出 spec'
        assert picker.selection is None

    def test_a_preview_without_a_real_sprite_is_refused(self, picker):
        """预览图换不到真立绘 = 拒绝（用户："换不到就拒绝并给一句话原因"）。"""
        _select(picker, 'unmatched_face')
        assert not picker._ok.isEnabled(), picker._pick_hint.text()
        hint = picker._pick_hint.text()
        assert '没找到对应的真立绘' in hint, hint
        picker.accept()
        assert picker.image_spec == ''

    def test_a_background_is_still_selectable(self, picker):
        _select(picker, 'bg_room')
        assert picker._ok.isEnabled(), picker._pick_hint.text()
        picker.accept()
        assert picker.image_spec == 'bg room', picker.image_spec


# ==================================================================
#  ⑤ 纯函数：人话名 / tooltip / 用途映射（不依赖 Qt，最便宜的一层）
# ==================================================================

class TestLabelHelpersArePure:
    def _ident(self, **kw):
        from storygraph.assets.asset_identity import Identity
        return Identity(**kw)

    def test_a_sprite_label_is_display_name_plus_attribute(self):
        from storygraph.ui_qt.asset_picker import human_label
        ident = self._ident(kind='sprite', tag='ling', attrs=('happy',),
                            display='凌跃捷', source='tag-column', showable=True)
        assert human_label(ident, 'ling_happy') == '凌跃捷 happy'

    def test_no_display_name_falls_back_to_tag_not_asset_id(self):
        from storygraph.ui_qt.asset_picker import human_label
        ident = self._ident(kind='sprite', tag='ling', attrs=('happy',),
                            source='tag-column', showable=True)
        assert human_label(ident, 'ling_happy') == 'ling happy'

    def test_preview_and_avatar_labels_say_what_they_are(self):
        from storygraph.ui_qt.asset_picker import human_label
        preview = self._ident(kind='preview', tag='ling', attrs=('happy',),
                              display='凌跃捷', source='folder')
        assert human_label(preview, 'x') == '预览图 · 凌跃捷 happy'
        avatar = self._ident(kind='avatar', tag='ling', display='凌跃捷',
                             source='folder')
        assert human_label(avatar, 'x') == '头像 · 凌跃捷'

    def test_unknown_is_labelled_as_unknown_not_dressed_up(self):
        from storygraph.ui_qt.asset_picker import human_label
        ident = self._ident(kind='unknown', source='unknown')
        text = human_label(ident, 'mystery', {'ID': 'mystery'})
        assert text.startswith('未识别'), text

    def test_the_tooltip_carries_id_path_and_the_basis(self):
        from storygraph.ui_qt.asset_picker import identity_tooltip
        ident = self._ident(kind='sprite', tag='ling', attrs=('happy',),
                            display='凌跃捷', source='tag-column',
                            showable=True)
        tip = identity_tooltip(ident, 'ling_happy',
                               {'ID': 'ling_happy',
                                'Path': 'game/images/characters/ling/happy.png'})
        assert 'ling_happy' in tip
        assert 'game/images/characters/ling/happy.png' in tip
        assert '资源表「Tag」列' in tip          # "凭什么这么判"
        assert '能' in tip                       # "能不能 show"

    def test_a_none_identity_still_shows_the_raw_id_and_path(self):
        from storygraph.ui_qt.asset_picker import identity_tooltip
        tip = identity_tooltip(None, 'ghost', {'Path': 'game/nope/ghost.png'})
        assert 'ghost' in tip and 'game/nope/ghost.png' in tip

    def test_identity_map_covers_rows_whose_table_key_differs_from_the_id(self):
        """
        真实项目里有 `'bg_2 '`（尾空格）这种表 key、行 ID 是 `bg_2` 的行。

        批量判定以**行 ID** 为 key；界面按表 key 取，不补齐的话那几行会变成
        "未识别"（用户看到的会是"明明有 Tag，怎么认不出"）。
        """
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.ui_qt.asset_picker import identity_map

        reg = AssetRegistry()
        reg.images = {'ling_happy ': {
            'ID': 'ling_happy', 'Tag': 'ling', 'Attributes': 'happy',
            'Path': 'game/images/characters/ling/happy.png'}}
        reg.characters = dict(CHARACTER_ROWS)
        idents = identity_map(reg, '')
        assert 'ling_happy ' in idents, '表 key 必须有结论'
        assert idents['ling_happy '].kind == 'sprite', idents['ling_happy ']


# ==================================================================
#  ⑥ 项目自己声明过的图像（ATL 块形式）不许被封掉
# ==================================================================

class TestDeclaredAtlImagesCanStillBeSelected:
    """
    老行为：`.rpy` 里写了 `image logo blink:` 的图，选择器里能选中并写回
    `logo blink`（贴图在块里、`Path` 空是正常的）。

    新校验**不能**把它当"认不出"封掉 —— 名字在引擎里是真实存在的。
    """

    def test_the_tile_is_still_marked_as_an_atl_definition(self, picker):
        it = _item_of(picker, 'logo_blink')
        assert 'ATL 定义' in it.text(), it.text()
        assert '没有图片文件' not in it.text(), it.text()

    def test_it_can_be_confirmed_and_writes_the_declared_spec(self, picker):
        _select(picker, 'logo_blink')
        assert picker._ok.isEnabled(), picker._pick_hint.text()
        assert 'logo blink' in picker._pick_hint.text(), picker._pick_hint.text()
        picker.accept()
        assert picker.image_spec == 'logo blink', picker.image_spec


# ==================================================================
#  非图片选择器：老行为不许回归
# ==================================================================

class TestNonImagePickerKeepsOldBehaviour:
    def test_no_purpose_filter_for_audio(self, qapp, tmp_path):
        from storygraph.ui_qt.asset_picker import AssetPickerDialog
        reg = _registry()
        reg.music = {'bgm_theme': {'ID': 'bgm_theme', 'Path': 'game/audio/x.ogg'}}
        dlg = AssetPickerDialog(None, registry=reg,
                                project_root=tmp_path / 'proj',
                                types=['music'], want_image_spec=False)
        try:
            assert dlg._purpose_box is None
            assert dlg._ok.isEnabled()
            dlg._items.setCurrentRow(0)
            dlg.accept()
            assert dlg.image_spec == 'bgm_theme', dlg.image_spec
        finally:
            dlg.close()
