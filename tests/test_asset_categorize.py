"""
tests/test_asset_categorize.py
==============================
**资源归类**（用户 2026-10-05："现在的 ui bg 一类的仍然不太准其实"）。

他把两张对账截图拍过来质问之后，我们把自动归类和**他项目里 2890 行
人工整理过的表**逐行对了一遍，发现的真错（都是**整条路径做子串匹配**造成的）：

  · `game/photo/music_room_photo/*.png` → `music`（图片被判成音乐，16 行）
  · `game/pomodorro/images/dororo_displeased.png` → `sound`
    （`se` 命中了 `displea**se**d`，12 行）
  · `game/gui/button/choice_hover_background.png` → `background`（16 行）
  · `game/gui/CG_frame.png` / `gui/dutui/**/CG鉴赏.png` → `cg`（14 行）
  · `game/charactercenter/base.png` → `character`（界面图被当立绘，32 行）
  · `pomodorro/images/dororo_*.png` 的 Tag 编成了目录名（应为 `dororo`）
  · `characters/lan/dynamic_images/*.png` 的 Tag 编成了 `dynamic images`
    （应为 `lan`，22 行）
  · `characters/avatar/lan.png` 的 Tag 编成空（应为 `avatar`）

本文件把这些**真实路径**钉成用例 —— 它们就是回归网。
"""

from __future__ import annotations

import pytest

from storygraph.assets.asset_registry import (_detect_category,
                                              _detect_image_tag)


def _cat(rel: str) -> str:
    from pathlib import Path
    return _detect_category(Path(rel))


def _tag(rel: str) -> str:
    from pathlib import Path
    p = Path(rel)
    return _detect_image_tag(Path('/proj') / p, p, _detect_category(p))


#: (路径, 期望类别, 期望 tag) —— 全部来自真实项目
CASES = [
    # —— 正常的三类 ——
    ('game/images/bg/bg1.1.png', 'background', 'bg'),
    ('game/images/bg_1.jpg', 'background', 'bg'),
    ('game/images/cg/cg01.png', 'cg', 'cg'),
    ('game/images/cg01.png', 'cg', 'cg'),
    ('game/images/characters/ling/happy.png', 'character', 'ling'),
    ('game/images/eileen_happy.png', 'character', 'eileen'),   # 平铺约定
    # —— 归错过的（子串匹配的锅）——
    ('game/photo/music_room_photo/music_list.png', 'other', ''),
    ('game/photo/music_room_photo/niddle.png', 'other', ''),
    ('game/pomodorro/images/dororo_displeased.png', 'character', 'dororo'),
    ('game/pomodorro/images/dororo_neutral.png', 'character', 'dororo'),
    ('game/gui/button/choice_hover_background.png', 'ui', 'ui'),
    ('game/gui/button/choice_idle_background.png', 'ui', 'ui'),
    ('game/gui/CG_frame.png', 'ui', 'ui'),
    ('game/gui/dutui/Frame 24/CG鉴赏.png', 'ui', 'ui'),
    ('game/gui/dutui/Frame 26/RETURE.png', 'ui', 'ui'),
    ('game/charactercenter/base.png', 'ui', 'ui'),
    ('game/charactercenter/card_lan.png', 'ui', 'ui'),
    ('game/charactercenter/background.png', 'ui', 'ui'),
    # —— tag 推错过的 ——
    ('game/images/characters/lan/dynamic_images/lan_body.png',
     'character', 'lan'),
    ('game/images/characters/avatar/lan.png', 'character', 'avatar'),
    ('game/images/characters/xing/base_arms_akimbo.png', 'character', 'xing'),
    # —— 连写的 bg（迷你游戏里全是这种）——
    ('game/pomodorro/images/bgbed.jpg', 'background', 'bg'),
    ('game/pomodorro/images/bgforestblur.jpg', 'background', 'bg'),
    # —— 界面/流程用的图，不该被当成谁的角色 ——
    ('game/pomodorro/images/start_menu_glamour.jpg', 'ui', 'ui'),
    ('game/minigamepieces/ball_moving_game/start.jpg', 'ui', 'ui'),
    ('game/minigamepieces/ball_moving_game/ball.png', 'other', ''),
    ('game/minigamepieces/logo/mini_ball.png', 'ui', 'ui'),
    # —— 根目录零散图：不编 tag ——
    ('about_collection_smoke.png', 'other', ''),
    ('game/pomodorro/images/dwhite.jpg', 'other', ''),
]


class TestCategoryAndTagOnRealPaths:
    @pytest.mark.parametrize('rel,want_cat,want_tag', CASES)
    def test_case(self, rel, want_cat, want_tag):
        got_cat = _cat(rel)
        got_tag = _tag(rel)
        assert (got_cat, got_tag) == (want_cat, want_tag), (
            f'{rel}\n  期望 {want_cat}/{want_tag!r}\n  实际 {got_cat}/{got_tag!r}')


class TestNoImageIsEverMusicOrSound:
    """
    **图片不可能属于音乐/音效** —— 这是"子串匹配"最刺眼的后遗症
    （实测 28 行图片被标成 music/sound，音乐候选里就多出 28 张图）。
    """

    def test_music_and_sound_words_in_paths_never_leak(self):
        for rel in ('game/photo/music_room_photo/music_list.png',
                    'game/pomodorro/images/dororo_displeased.png',
                    'game/soundtrack/cover.png',
                    'game/audio/night_bg.png',
                    'game/se/effect_icon.png',
                    'game/images/music_note.png'):
            assert _cat(rel) not in ('music', 'sound'), rel

    def test_only_known_categories_are_returned(self):
        for rel, _c, _t in CASES:
            assert _cat(rel) in ('background', 'cg', 'character', 'ui',
                                 'other'), rel


class TestUiSuffixAndTokenMatching:
    """
    UI 目录的识别：**词元精确匹配 + 少量后缀**。

    不能拿 `'ui' in segment` 判：`build` / `guide` / `fruit` 都含 `ui`；
    但 `dutui` / `charactercenter` 这种复合词要靠后缀收进来。
    """

    @pytest.mark.parametrize('seg', ['gui', 'ui', 'dutui', 'button', 'icons',
                                     'menu', 'my_ui_assets', 'charactercenter',
                                     'mainmenu', 'hud', 'skin', 'widgets'])
    def test_ui_segments(self, seg):
        from storygraph.assets.asset_registry import _segment_is_ui
        assert _segment_is_ui(seg) is True, seg

    @pytest.mark.parametrize('seg', ['build', 'guide', 'fruit', 'characters',
                                     'images', 'bg', 'cg', 'voice'])
    def test_non_ui_segments(self, seg):
        from storygraph.assets.asset_registry import _segment_is_ui
        assert _segment_is_ui(seg) is False, seg
