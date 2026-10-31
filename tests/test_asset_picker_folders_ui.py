# -*- coding: utf-8 -*-
"""
tests/test_asset_picker_folders_ui.py
=====================================
资产选择器左栏的**两种口径，随时能切**（用户 2026-10-30）。

用户原话：

> 文件筛选与分类方面……要提供两种分类方式，一种就是按文件夹分类排序，
> 另一种就是原来的按图片，背景，UI 等那种分类排序……这两种方式要能够
> 随时切换（卡片编辑器导入立绘点进去的那个界面）

纯逻辑（`folder_of_row` / `folder_groups`）在 `tests/test_picker_folders.py`；
这一份只钉**界面**用没用上它：

  1. 一打开还是**按分类**（既有样子不变：组名照旧，不莫名其妙多出数字）；
  2. 切「按文件夹」→ 左栏变成真实目录树（缩进 + 每层张数），不是一堆平铺的名字；
  3. 切过去**还指着同一批图**（交集最大那组），而不是把人甩回「全部」；
  4. 切回「按分类」同样停住；
  5. 用途过滤在两种口径下都还在（选了「立绘」就不会冒出一堆界面图）。
"""

from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip('PySide6')

from PySide6.QtCore import QSettings, Qt  # noqa: E402


# ==================================================================
#  夹具：一个有**两层子目录**的最小工程
# ==================================================================

def _write_png(path: Path):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', (8, 8), (200, 30, 30, 255)).save(path, 'PNG')


#: `(资产 ID, Tag, Attributes, 相对路径)`
IMAGE_ROWS = [
    ('ling_happy', 'ling', 'happy',
     'game/images/characters/ling/happy.png'),
    ('ling_sad', 'ling', 'sad',
     'game/images/characters/ling/sad.png'),
    # ↓ 子文件夹：验"点上一层就看得见下层的图"
    ('ling_blink', 'ling', 'blink',
     'game/images/characters/ling/expressions/blink.png'),
    ('bg_room', 'bg', 'room', 'game/images/bg/room.png'),
    ('ui_button', 'ui', 'button', 'game/gui/button.png'),
    # ↓ 两个**同名**文件夹（真实项目里 `gui/dutui/overlay` 与
    #   `gui/phone/overlay` 都叫 overlay）：左栏要保持能分清
    ('ov_dutui', 'ui', 'overlay', 'game/gui/dutui/overlay/RETURE.png'),
    ('ov_phone', 'ui', 'overlay', 'game/gui/phone/overlay/RETURE.png'),
    # ↓ 又一个同名叶子：`gui/ling` 与 `images/characters/ling` 都叫 ling，
    #   但前者是界面图 —— 选了「用途：立绘」时它会被藏起来，那就不该再补尾巴
    ('ui_ling_icon', 'ui', 'icon', 'game/gui/ling/icon.png'),
    # ↓ 没有 Path（ATL 块形式）：文件夹口径下必须仍然看得见
    ('logo_blink', 'logo', 'blink', ''),
]

CHARACTER_ROWS = {
    'ling': {'ID': 'ling', 'Name': '凌跃捷', 'ImageTag': 'ling',
             'Color': '#c8ffc8', 'Aliases': '凌', 'Notes': ''},
}


def _registry():
    from storygraph.assets.asset_registry import AssetRegistry
    reg = AssetRegistry()
    reg.images = {}
    for aid, tag, attrs, rel in IMAGE_ROWS:
        row = {'ID': aid, 'Tag': tag, 'Attributes': attrs, 'Path': rel}
        if not rel:
            row['_atl'] = True
        reg.images[aid] = row
    reg.characters = dict(CHARACTER_ROWS)
    reg.music = {}
    reg.sounds = {}
    reg.voices = {}
    reg.transforms = {}
    return reg


@pytest.fixture
def picker(qapp, tmp_path, monkeypatch):
    from storygraph.ui_qt import asset_picker as AP
    ini = tmp_path / 'prefs.ini'
    monkeypatch.setattr(
        AP, 'settings_factory',
        lambda: QSettings(str(ini), QSettings.IniFormat))
    proj = tmp_path / 'proj'
    (proj / 'game').mkdir(parents=True)
    for _aid, _tag, _attrs, rel in IMAGE_ROWS:
        if rel:
            _write_png(proj / rel)
    dlg = AP.AssetPickerDialog(None, registry=_registry(),
                               project_root=proj, types=['image'],
                               want_image_spec=True)
    yield dlg
    dlg.close()


# ------------------------------------------------------------------
#  小工具
# ------------------------------------------------------------------

def _labels(picker) -> list:
    return [picker._cats.item(i).text() for i in range(picker._cats.count())]


def _names(picker) -> list:
    """左栏每一行背后的**组名**（`UserRole`；「全部」是 `None`）。"""
    return [picker._cats.item(i).data(Qt.UserRole)
            for i in range(picker._cats.count())]


def _select(picker, name):
    for i in range(picker._cats.count()):
        if picker._cats.item(i).data(Qt.UserRole) == name:
            picker._cats.setCurrentRow(i)
            return
    raise AssertionError(f'左栏里没有 {name!r}：{_names(picker)}')


def _tile_ids(picker) -> list:
    return [picker._items.item(i).data(Qt.UserRole)[1]
            for i in range(picker._items.count())]


# ==================================================================
#  ① 默认还是按分类（既有样子不变）
# ==================================================================

class TestTheDefaultIsStillCategories:
    def test_the_switch_exists_and_starts_on_categories(self, picker):
        assert picker._group_by_box.currentText() == '按分类'
        assert picker._group_by == '按分类'

    def test_the_group_names_are_unchanged(self, picker):
        """分类那一档不许因为加了开关就长出「（3）」这种尾巴。"""
        assert '立绘：凌跃捷（ling）' in _labels(picker), _labels(picker)
        assert not any('（3）' in t for t in _labels(picker)), _labels(picker)

    def test_the_choice_is_not_written_into_the_prefs(self, picker):
        """
        "这一眼按什么看"不是长期偏好：记住它会让**上次切过文件夹**变成一个
        看不见的状态（下次打开一屏目录，人还不知道为什么）。
        """
        picker._group_by_box.setCurrentText('按文件夹')
        from storygraph.ui_qt import asset_picker as AP
        s = AP._settings()
        assert not s.contains('picker/group_by')


# ==================================================================
#  ② 切到文件夹：真实目录树
# ==================================================================

class TestTheFolderMode:
    def test_it_lists_the_real_directories(self, picker):
        picker._group_by_box.setCurrentText('按文件夹')
        names = _names(picker)
        assert 'images' in names and 'images/characters/ling' in names
        assert 'images/characters/ling/expressions' in names
        assert 'gui' in names

    def test_the_labels_are_an_indented_tree_with_counts(self, picker):
        picker._group_by_box.setCurrentText('按文件夹')
        labels = _labels(picker)
        assert 'images（4）' in labels, labels
        assert '  characters（3）' in labels, labels
        # `ling` 有两个（`images/characters/ling` 与 `gui/ling`）⇒ 带尾巴
        assert '    ling（3） ← characters' in labels, labels
        assert '      expressions（1）' in labels, labels

    def test_a_folder_shows_its_subfolder_images_too(self, picker):
        picker._group_by_box.setCurrentText('按文件夹')
        _select(picker, 'images/characters/ling')
        assert sorted(_tile_ids(picker)) == ['ling_blink', 'ling_happy',
                                             'ling_sad']

    def test_the_parent_folder_shows_everything_below(self, picker):
        picker._group_by_box.setCurrentText('按文件夹')
        _select(picker, 'images')
        assert sorted(_tile_ids(picker)) == ['bg_room', 'ling_blink',
                                             'ling_happy', 'ling_sad']

    def test_a_folder_without_a_path_is_still_reachable(self, picker):
        """ATL 块形式（Path 空）不许在文件夹口径下消失。"""
        from storygraph.app import picker as P
        picker._group_by_box.setCurrentText('按文件夹')
        _select(picker, P.NO_PATH_GROUP)
        assert _tile_ids(picker) == ['logo_blink']

    def test_a_bucket_that_would_be_empty_is_not_shown(self, picker):
        """「根目录」只为"真的躺在 game/ 下"的图出现 —— 空桶不占一行。"""
        from storygraph.app import picker as P
        picker._group_by_box.setCurrentText('按文件夹')
        assert P.ROOT_GROUP not in _names(picker), _labels(picker)

    def test_two_folders_with_the_same_leaf_name_are_told_apart(self, picker):
        """
        `gui/dutui/overlay` 与 `gui/phone/overlay` 只写叶子名就是两行一模一样
        —— 补一段父目录，补到能分开就够（不是无脑写全路径）。
        """
        picker._group_by_box.setCurrentText('按文件夹')
        labels = _labels(picker)
        assert '    overlay（1） ← dutui' in labels, labels
        assert '    overlay（1） ← phone' in labels, labels

    def test_a_unique_leaf_name_does_not_get_a_hint(self, picker):
        picker._group_by_box.setCurrentText('按文件夹')
        labels = _labels(picker)
        assert '      expressions（1）' in labels, labels
        assert not any('←' in t and 'expressions' in t for t in labels), labels

    def test_a_hidden_twin_does_not_add_a_hint(self, picker):
        """
        尾巴只为**屏幕上真同时出现**的两行加。`gui/ling` 被"用途：立绘"藏起来
        之后，`images/characters/ling` 就不该再挂着 `← characters` 的尾巴。
        """
        picker._group_by_box.setCurrentText('按文件夹')
        picker._purpose_box.setCurrentText('立绘')
        labels = _labels(picker)
        ling = [t for t in labels if t.strip().startswith('ling')]
        assert ling == ['    ling（3）'], labels


# ==================================================================
#  ③ 切来切去，别把人甩走
# ==================================================================

class TestSwitchingKeepsMeOnTheSameImages:
    def test_categories_to_folders(self, picker):
        _select(picker, '立绘：凌跃捷（ling）')
        assert sorted(_tile_ids(picker)) == ['ling_blink', 'ling_happy',
                                             'ling_sad']
        picker._group_by_box.setCurrentText('按文件夹')
        # 那三张图都在 `images/characters/ling`：切过去就该停在这一格，
        # 而不是"回全部"或者停在某个只有一张图的父目录上。
        assert picker._cats.currentItem().data(Qt.UserRole) == \
            'images/characters/ling'
        assert sorted(_tile_ids(picker)) == ['ling_blink', 'ling_happy',
                                             'ling_sad']

    def test_folders_to_categories(self, picker):
        picker._group_by_box.setCurrentText('按文件夹')
        _select(picker, 'images/bg')
        picker._group_by_box.setCurrentText('按分类')
        assert picker._cats.currentItem().data(Qt.UserRole) == '背景'
        assert _tile_ids(picker) == ['bg_room']

    def test_no_counterpart_falls_back_to_all(self, picker):
        """找不到对应关系就老实回「全部」，不硬凑一个空组。"""
        assert picker._match_group([]) is None
        assert picker._match_group(None) is None
        # 交集为零的那些组不会被选中（`_match_group` 要求至少有一条重合）
        picker._group_by_box.setCurrentText('按文件夹')
        assert picker._match_group([('image', '不存在的图')]) is None


# ==================================================================
#  ④ 用途过滤在两种口径下都还在
# ==================================================================

class TestThePurposeFilterStillWorks:
    def test_it_narrows_the_folder_list(self, picker):
        picker._group_by_box.setCurrentText('按文件夹')
        picker._purpose_box.setCurrentText('立绘')
        names = _names(picker)
        assert 'images/characters/ling' in names, names
        assert 'gui' not in names, names           # 界面图那一栏被藏起来了

    def test_it_narrows_the_tiles(self, picker):
        picker._group_by_box.setCurrentText('按文件夹')
        _select(picker, 'images')
        picker._purpose_box.setCurrentText('背景')
        assert _tile_ids(picker) == ['bg_room']


# ==================================================================
#  ⑤ 别的资产类型（音乐 / 音效 / 语音）也有文件夹，别只给图片做
# ==================================================================

class TestOtherAssetTypes:
    def test_a_music_picker_has_folders_too(self, qapp, tmp_path, monkeypatch):
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.ui_qt import asset_picker as AP
        ini = tmp_path / 'prefs.ini'
        monkeypatch.setattr(
            AP, 'settings_factory',
            lambda: QSettings(str(ini), QSettings.IniFormat))
        reg = AssetRegistry()
        reg.images = {}
        reg.music = {'bgm_main': {'ID': 'bgm_main', 'Path': 'game/bgm/main.ogg'},
                     'bgm_boss': {'ID': 'bgm_boss',
                                  'Path': 'game/bgm/boss/boss.ogg'}}
        dlg = AP.AssetPickerDialog(None, registry=reg, project_root=tmp_path,
                                   types=['music'])
        try:
            assert dlg._purpose_box is None          # 选音乐没有"用途"这一档
            dlg._group_by_box.setCurrentText('按文件夹')
            assert 'bgm' in _names(dlg), _names(dlg)
            _select(dlg, 'bgm')
            assert sorted(_tile_ids(dlg)) == ['bgm_boss', 'bgm_main']
        finally:
            dlg.close()
