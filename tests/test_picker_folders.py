# -*- coding: utf-8 -*-
"""
tests/test_picker_folders.py
============================
资产选择器的**第二种口径：按文件夹**（用户 2026-10-30）。

用户原话：

> 文件筛选与分类方面……要提供两种分类方式，一种就是按文件夹分类排序，
> 另一种就是原来的按图片，背景，UI 等那种分类排序……这两种方式要能够
> 随时切换

这一份只钉**纯逻辑**（`app.picker.folder_of_row` / `folder_groups`）——
界面开关在 `tests/test_asset_picker_folders_ui.py`。

要钉住的四件事：

  1. **两种写法落进同一个文件夹**：资源表的 `Path` 实测有 `game/images/x.png`
     与 `images/x.png`（后者相对 `game/`）两种，还有 `sr_general/game/...`
     这种带项目目录名的 —— 按文件夹看的时候不该被拆成三个"不同的文件夹"；
  2. **每一层都含它下面所有子文件夹的图**（点 `images` 就看见 `images/**`），
     真实项目里 `game/images` 下面常常一个文件都没有、全是子目录；
  3. **排序**：父在子前、逐段比名字、「根目录」最前、「没有路径」垫底；
  4. **没有路径的资产不许消失**（角色 / 变换 / ATL 块这类本来就没有文件）。
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ==================================================================
#  夹具：一个"四种 Path 写法都占全"的最小注册表
# ==================================================================

def _registry():
    from storygraph.assets.asset_registry import AssetRegistry
    reg = AssetRegistry()
    reg.images = {
        # `game/` 前缀写法（真实项目里 1977 行绝大多数是这种）
        'ling_happy': {'ID': 'ling_happy', 'Tag': 'ling', 'Attributes': 'happy',
                       'Path': 'game/images/characters/ling/happy.png'},
        'ling_sad': {'ID': 'ling_sad', 'Tag': 'ling', 'Attributes': 'sad',
                     'Path': 'game/images/characters/ling/sad.png'},
        # 子文件夹（用来验"每一层都含子文件夹"）
        'ling_blink': {'ID': 'ling_blink', 'Tag': 'ling', 'Attributes': 'blink',
                       'Path': 'game/images/characters/ling/expressions/'
                               'blink.png'},
        # `game/` 相对写法（同一种文件位置的另一种写法）
        'bg_room': {'ID': 'bg_room', 'Tag': 'bg', 'Attributes': 'room',
                    'Path': 'images/bg/room.png'},
        # `ui` = "界面"，`ui/` = 文件夹 —— 两种口径在同一个项目里共存
        'ui_button': {'ID': 'ui_button', 'Tag': 'ui', 'Path': 'game/gui/button.png'},
        # 带项目目录名的写法（真实项目 74 行是这样）
        'dutui_frame': {'ID': 'dutui_frame', 'Tag': 'ui',
                        'Path': 'sr_general/game/gui/dutui/Frame 26/RETURE.png'},
        # 文件直接躺在 game/ 下
        '0': {'ID': '0', 'Tag': 'ui', 'Path': 'game/0.png'},
        # 没有 Path（ATL 块形式 / 角色 / 变换都长这样）
        'logo_blink': {'ID': 'logo_blink', 'Tag': 'logo', 'Attributes': 'blink',
                       'Path': ''},
    }
    reg.music = {'bgm_main': {'ID': 'bgm_main', 'Path': 'game/bgm/main.ogg'}}
    reg.sounds = {}
    reg.voices = {}
    reg.transforms = {'left': {'ID': 'left'}}
    reg.characters = {'ling': {'ID': 'ling', 'Name': '凌跃捷'}}
    return reg


PROJECT = Path(r'D:\千连万理\renpytest\sr_general')   # 只为"去项目目录名"那一条


# ==================================================================
#  ① 一条资产在哪个文件夹
# ==================================================================

class TestFolderOfRow:
    def test_the_two_real_path_conventions_land_in_one_folder(self):
        """`game/images/bg` 与 `images/bg` 是同一个文件夹（别拆成两个）。"""
        from storygraph.app import picker as P
        a = P.folder_of_row({'Path': 'game/images/bg/room.png'})
        b = P.folder_of_row({'Path': 'images/bg/room.png'})
        assert a == b == 'images/bg'

    def test_a_duplicated_project_dir_name_is_dropped(self):
        """`sr_general/game/gui/...` → `gui/...`（跟着 `normalize_relpath` 走）。"""
        from storygraph.app import picker as P
        got = P.folder_of_row(
            {'Path': 'sr_general/game/gui/dutui/Frame 26/RETURE.png'},
            str(PROJECT))
        assert got == 'gui/dutui/Frame 26'

    def test_an_absolute_path_inside_the_project_is_relative(self, tmp_path):
        from storygraph.app import picker as P
        proj = tmp_path / 'proj'
        img = proj / 'game' / 'images' / 'ling' / 'happy.png'
        img.parent.mkdir(parents=True)
        img.write_bytes(b'')
        assert P.folder_of_row({'Path': str(img)}, str(proj)) == 'images/ling'

    def test_backslashes_are_fine(self):
        from storygraph.app import picker as P
        assert P.folder_of_row(
            {'Path': r'game\images\ling\happy.png'}) == 'images/ling'

    def test_a_file_directly_under_game_has_no_folder(self):
        """`''`（空字符串）与 `None` 是两件事，见 `folder_of_row` 的文档。"""
        from storygraph.app import picker as P
        assert P.folder_of_row({'Path': 'game/0.png'}) == ''

    def test_no_path_is_none_not_empty(self):
        from storygraph.app import picker as P
        assert P.folder_of_row({'Path': ''}) is None
        assert P.folder_of_row({}) is None
        assert P.folder_of_row(None) is None

    def test_a_file_column_is_understood_too(self):
        """资源表主列是 `Path`，但引擎/映射那边有 `File` 写法 —— 认它，不猜。"""
        from storygraph.app import picker as P
        assert P.folder_of_row({'File': 'game/audio/bgm/main.ogg'}) == 'audio/bgm'


# ==================================================================
#  ② + ③ 分组：每一层都含子文件夹 / 排序
# ==================================================================

class TestFolderGroups:
    def test_every_level_contains_its_subfolders(self):
        from storygraph.app import picker as P
        g = P.folder_groups(_registry())
        assert [a for _, a in g['images']] == [
            'bg_room', 'ling_blink', 'ling_happy', 'ling_sad']
        assert [a for _, a in g['images/characters/ling']] == [
            'ling_blink', 'ling_happy', 'ling_sad']
        assert [a for _, a in g['images/characters/ling/expressions']] == [
            'ling_blink']

    def test_direct_only_mode_keeps_one_level(self):
        from storygraph.app import picker as P
        g = P.folder_groups(_registry(), include_subfolders=False)
        assert 'images' not in g                     # 这一层一个文件都没有
        assert [a for _, a in g['images/bg']] == ['bg_room']

    def test_parents_come_before_children(self):
        from storygraph.app import picker as P
        names = list(P.folder_groups(_registry()))
        assert names.index('images') < names.index('images/characters')
        assert names.index('images/characters') < \
            names.index('images/characters/ling')
        assert names.index('images/characters/ling') < \
            names.index('images/characters/ling/expressions')

    def test_root_folder_first_and_missing_path_last(self):
        from storygraph.app import picker as P
        names = list(P.folder_groups(_registry()))
        assert names[0] == P.ROOT_GROUP
        assert names[-1] == P.NO_PATH_GROUP

    def test_assets_without_a_path_do_not_vanish(self):
        """角色 / 变换 / ATL 块没有文件 —— 但用户仍然要能选到它们。"""
        from storygraph.app import picker as P
        g = P.folder_groups(_registry(), types=['image'])
        assert [a for _, a in g[P.NO_PATH_GROUP]] == ['logo_blink']

    def test_all_types_put_role_and_transform_rows_in_the_same_bucket(self):
        """不指定 types 时角色/变换也在（它们本来就没有文件，不能凭空消失）。"""
        from storygraph.app import picker as P
        g = P.folder_groups(_registry())
        assert sorted(a for _, a in g[P.NO_PATH_GROUP]) == [
            'left', 'ling', 'logo_blink']

    def test_sorting_is_per_segment_not_per_string(self):
        """逐段比名字：`art` 要排在 `images` 前（不是按整串字符比）。"""
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.app import picker as P
        reg = AssetRegistry()
        reg.images = {
            'i': {'Path': 'game/images/x.png'},
            'a': {'Path': 'game/art/x.png'},
            'b': {'Path': 'game/images/b/x.png'},
        }
        names = list(P.folder_groups(reg))
        assert names.index('art') < names.index('images')
        assert names.index('images') < names.index('images/b')

    def test_the_case_of_folder_names_does_not_split_them(self):
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.app import picker as P
        reg = AssetRegistry()
        reg.images = {
            'a': {'Path': 'game/gui/x.png'},
            'b': {'Path': 'game/GUI/y.png'},
        }
        # 大小写不同 = 同一个文件夹（Windows 上就是同一个），不拆成两行
        names = [n for n in P.folder_groups(reg) if n.lower() == 'gui']
        assert len(names) == 1, names
        assert sorted(a for _, a in P.folder_groups(reg)[names[0]]) == ['a', 'b']

    def test_types_filter(self):
        from storygraph.app import picker as P
        g = P.folder_groups(_registry(), types=['music'])
        assert list(g) == ['bgm']
        assert g['bgm'] == [('music', 'bgm_main')]

    def test_limit_stops_runaway_projects(self):
        from storygraph.app import picker as P
        g = P.folder_groups(_registry(), limit=2)
        assert sum(len(v) for v in g.values()) <= 2 * 4   # 累计口径会重复计

    def test_an_empty_registry_gives_nothing(self):
        from storygraph.app import picker as P
        assert P.folder_groups(None) == {}

    def test_it_never_stats_the_disk(self, monkeypatch):
        """纯字符串归一：两千行也不该卡 UI 线程（不许在分组里读盘）。"""
        from storygraph.app import picker as P
        import os

        def boom(*_a, **_k):
            raise AssertionError('按文件夹分组不许 stat 磁盘')

        monkeypatch.setattr(os.path, 'exists', boom)
        monkeypatch.setattr(os.path, 'getsize', boom)
        monkeypatch.setattr(Path, 'is_file', boom)
        g = P.folder_groups(_registry())
        assert g['images']


# ==================================================================
#  ④ 两种口径并存（分类那个不能被我改坏）
# ==================================================================

def test_the_category_grouping_is_still_there():
    """同一个注册表，两套分组同时可用 —— 界面切换只是在两者之间换。"""
    from storygraph.app import picker as P
    reg = _registry()
    folders = P.folder_groups(reg)
    cats = P.groups_with_plain(reg, types=['image'])
    assert 'images/characters/ling' in folders
    assert any(name.startswith('立绘：') for name in cats), list(cats)


@pytest.mark.parametrize('bad', ['', None, '   '])
def test_a_row_with_a_blank_path_is_not_a_folder(bad):
    from storygraph.app import picker as P
    assert P.folder_of_row({'Path': bad}) is None
