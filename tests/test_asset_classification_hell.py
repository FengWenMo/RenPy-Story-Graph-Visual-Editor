# -*- coding: utf-8 -*-
"""
tests/test_asset_classification_hell.py
=======================================
**资源筛选/管理的地狱测试**（用户 2026-11-02：「资源筛选与管理系统写得不够硬！
太粗糙了……真实的项目确实就是会有这么多乱七八糟的资源」）。

为什么另开一个文件（`test_asset_categorize.py` 已有 30 条真实路径）：那 30 条
是"修过的坑"，一次一条；这个文件造的是**一整棵乱七八糟的树**——多层嵌套、
jpg/png/jpeg/webp 混着来、角色/背景/CG/UI/垃圾素材混在一个 `images/` 下面，
然后从三个层次同时验：

  ① `_detect_category` / `_detect_image_tag`（路径 → 类别/tag，唯一真源）；
  ② `scan_assets`（真扫一遍磁盘，看表里被写成什么）；
  ③ 身份层（`project_context` + `identities`）+ 选择器分组（`categorize_assets`）
     —— 用户真正看到的"用途"和"分组"。

三条**绝不能破**的底线（用户点名的两种错）：

  · 立绘不许进 UI（`Identity.kind == 'ui'`）—— `images/characters/**`、
    `images/<角色>/**`、`cc_preview/**` 里的图都算；
  · 背景不许进 UI，也不许被编成某个角色（`bg` 的图 Tag 必须是 `bg`）；
  · 认不出就 `other` + **不编 tag**（`Tag == ''`），不许拿目录名硬编
    `nbg` / `try` / `rhythm game` 这种脚本里根本不存在的 tag。

最后一段是**真机回归**：这个仓库的实测项目（`sr_general`）里被归错的四个
目录（`images/nbg` 42 行 / `images/try` 17 行 / `images/rhythm_game` 4 行 /
`cc_preview/**` 159 行）原样钉在这里 —— 它们是"乱七八糟的真实资源"长什么样
的第一手证据。
"""

from __future__ import annotations

import os

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


# ==================================================================
#  整棵"乱七八糟"的树：(相对路径, 期望类别, 期望 tag)
#
#  期望值是**人看这张图会怎么用**定的，不是照着现在的实现抄的。
#  `''` = 认不出（不许编一个 show 里不存在的 tag）。
# ==================================================================

HELL: list[tuple[str, str, str]] = [
    # ── 背景：目录名、目录名后缀（`nbg` 这种把 bg 缩在词尾的）、文件名，
    #     三条路都得认 ────────────────────────────────────────────
    ('game/images/bg/bg1.1.png', 'background', 'bg'),
    ('game/images/bg/room.jpg', 'background', 'bg'),
    # 中文目录（中文项目的常态：`game/背景/`、`game/场景/`）
    ('game/背景/学校.png', 'background', 'bg'),
    ('game/场景/教室.jpg', 'background', 'bg'),
    ('game/images/nbg/bg1.1.png', 'background', 'bg'),
    ('game/images/nbg/school.png', 'background', 'bg'),
    ('game/images/hdbg/night.jpg', 'background', 'bg'),
    ('game/images/new_backgrounds/city.jpg', 'background', 'bg'),
    ('game/images/bg_1.jpg', 'background', 'bg'),
    ('game/images/bg2.webp', 'background', 'bg'),
    ('game/bgs/beach.webp', 'background', 'bg'),
    ('game/minigamepieces/puzzle/Background.png', 'background', 'bg'),

    # ── CG ────────────────────────────────────────────────────
    ('game/images/cg/cg01.png', 'cg', 'cg'),
    ('game/images/cg/photo.JPG', 'cg', 'cg'),
    ('game/images/cg01.jpg', 'cg', 'cg'),
    ('game/cg/event1.png', 'cg', 'cg'),
    ('game/插画/cg02.png', 'cg', 'cg'),

    # ── 立绘：容器 / `images/<角色>/` / 平铺 `<角色>_<表情>.png` ──
    ('game/images/characters/lan/happy.png', 'character', 'lan'),
    ('game/images/characters/lan/dynamic_images/eye_white.png',
     'character', 'lan'),
    ('game/images/characters/avatar/lan.png', 'character', 'avatar'),
    ('game/images/eileen/happy.png', 'character', 'eileen'),
    ('game/images/eileen_happy.png', 'character', 'eileen'),
    ('game/images/star_girl/smile.png', 'character', 'star_girl'),
    ('game/art/chars/mary/sad.png', 'character', 'mary'),
    # 中文容器：`game/立绘/<角色>/…`、`game/图片/<角色>/…`
    ('game/立绘/lan/smile.png', 'character', 'lan'),
    ('game/图片/lan/angry.png', 'character', 'lan'),
    # 预览副本：**是立绘素材（category=character），但不是能写进 show 的名字**
    ('game/cc_preview/lan/angry.png', 'character', ''),
    ('game/cc_preview/avatar/lan.png', 'character', ''),
    ('game/images/preview/ling/happy.png', 'character', ''),

    # ── UI：目录语义压过文件名（`CG_frame.png` 在 gui 里就是 UI）──
    ('game/gui/button/ok.png', 'ui', 'ui'),
    ('game/gui/CG_frame.png', 'ui', 'ui'),
    ('game/gui/dutui/Frame 24/CG鉴赏.png', 'ui', 'ui'),
    ('game/gui/phone/preview/bubble.png', 'ui', 'ui'),
    ('game/charactercenter/base.png', 'ui', 'ui'),
    ('game/minigamepieces/logo/mini.png', 'ui', 'ui'),
    ('game/screens/main_menu.jpg', 'ui', 'ui'),
    ('game/ui/icons/heart.png', 'ui', 'ui'),
    ('game/界面/标题.png', 'ui', 'ui'),
    ('game/pomodorro/images/start_menu_glamour.jpg', 'ui', 'ui'),
    ('game/phone_msg/ling.png', 'ui', 'ui'),

    # ── 认不出 = other，**不许编 tag** ──────────────────────────
    ('game/images/try/changeover_lan.png', 'other', ''),
    ('game/images/rhythm_game/down.png', 'other', ''),
    ('game/images/effects/spark.png', 'other', ''),
    ('game/minigamepieces/ball_moving_game/ball.png', 'other', ''),
    ('game/photo/music_room_photo/music_list.png', 'other', ''),
    ('game/pomodorro/images/dwhite.jpg', 'other', ''),
    ('about_collection_smoke.png', 'other', ''),
    # 下载来的散图：连字符 / 编号 / 括号 / 空格 —— **不许当成 `<tag>_<attr>`**
    # （真机 12 张 pexels 图一度成了"角色 pexels / nature / homodoro"）
    ('game/images/pexels-photo-107937.jpeg', 'other', ''),
    ('game/images/nature-flowers-balcony (1).jpg', 'other', ''),
    ('game/images/Homodoro (9).png', 'other', ''),
    ('game/images/eileen-happy.png', 'other', ''),
    ('game/images/选项框2.png', 'other', ''),
    # 缩略图文件（`thumbnail_*`）是预览素材，不是谁的表情
    ('game/images/thumbnail_cafe.png', 'other', ''),
    # 标题画面素材 → 界面（`title` 是界面词，`title_bg` 仍按 `_bg` 归背景）
    ('game/images/titleimage.png', 'ui', 'ui'),
    ('game/images/title_bg.png', 'background', 'bg'),
]


class TestPathRulesOnTheHellTree:
    """① 路径 → 类别 / tag（纯函数，一次一条）。"""

    @pytest.mark.parametrize('rel,want_cat,want_tag', HELL)
    def test_case(self, rel, want_cat, want_tag):
        got_cat, got_tag = _cat(rel), _tag(rel)
        assert (got_cat, got_tag) == (want_cat, want_tag), (
            f'{rel}\n  期望 {want_cat}/{want_tag!r}\n  实际 {got_cat}/{got_tag!r}')

    def test_flat_name_rule_only_for_files_directly_under_a_root(self):
        """
        `images/<tag>_<attr>.png` 这条平铺约定**只对"就躺在 images/ 里"的文件**成立。

        旧实现只要路径里出现过 `images` 就套这条 —— 于是
        `images/try/changeover_lan.png` 被判成"立绘 try"（真机 17 行）。
        """
        assert _cat('game/images/eileen_happy.png') == 'character'
        assert _cat('game/images/try/changeover_lan.png') == 'other'
        assert _tag('game/images/try/changeover_lan.png') == ''

    def test_a_directory_is_never_a_tag_when_it_says_it_is_not_a_character(self):
        """目录名里带 `try` / `game` / `effects` 这类词 → 不许当角色目录。"""
        for rel in ('game/images/try/lan.png',
                    'game/images/rhythm_game/down.png',
                    'game/images/effects/spark.png'):
            assert _cat(rel) == 'other', rel

    def test_preview_directories_are_sprite_material_but_not_show_names(self):
        """
        预览副本（`cc_preview/**`、`**/preview/**`、`**/thumb/**`）：
        **category 是 character**（它是立绘素材），但 **tag 留空** ——
        它的文件名不是能写进 `show` 的名字（真机 159 行）。
        """
        for rel in ('game/cc_preview/lan/angry.png',
                    'game/cc_preview/avatar/lan.png',
                    'game/images/preview/ling/happy.png',
                    'game/thumbs/eileen/sad.png'):
            assert _cat(rel) == 'character', rel
            assert _tag(rel) == '', rel

    def test_preview_inside_a_ui_folder_stays_ui(self):
        """`gui/phone/preview/x.png` —— 预览目录在**界面目录里**，还是界面。"""
        assert _cat('game/gui/phone/preview/bubble.png') == 'ui'
        assert _cat('game/gui/thumb/bar.png') == 'ui'


class TestExtensionsAreTreatedTheSame:
    """jpg / jpeg / webp / 大写后缀 不许因为扩展名不同就归到别的类。"""

    @pytest.mark.parametrize('ext', ['png', 'jpg', 'jpeg', 'webp', 'PNG', 'JPG'])
    def test_same_stem_same_answer(self, ext):
        rel = f'game/images/characters/lan/happy.{ext}'
        assert (_cat(rel), _tag(rel)) == ('character', 'lan')
        rel_bg = f'game/images/bg/room.{ext}'
        assert (_cat(rel_bg), _tag(rel_bg)) == ('background', 'bg')


# ==================================================================
#  ② 真扫一遍磁盘：整棵树 → 表行
# ==================================================================

def _build_tree(root):
    """把 HELL 里的路径**真建出来**（每个文件写几个字节，够扫描认）。"""
    made = []
    for rel, _cat, _tag in HELL:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'\x89PNG\r\n\x1a\n')
        made.append(rel)
    # 噪声：截图 / Excel 锁文件 / 跳过目录 —— 一条都不许进表
    for rel in ('game/images/misc/Clipboard_Screenshot.png',
                'game/images/misc/屏幕截图 2026-01-01.png',
                'game/images/misc/~$lock.png',
                'game/images/tmp/half_done.png',
                'game/saves/save1.png'):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'\x89PNG\r\n\x1a\n')
    return made


@pytest.fixture(scope='module')
def hell(tmp_path_factory):
    root = tmp_path_factory.mktemp('hell_project')
    made = _build_tree(root)
    from storygraph.assets.asset_registry import scan_assets
    scanned = scan_assets(root, include_ui=True)
    return {'root': root, 'made': made, 'rows': scanned['images']}


def _by_path(rows) -> dict:
    """
    扫描结果 → `{相对路径: 行}`。

    ⚠ **按小写比**：`_scan_file_items` 的索引键是相对小写路径（跨平台可复现的
    取舍），而 HELL 表里写的是磁盘上的真实大小写（`CG_frame.png` /
    `Frame 24/CG鉴赏.png`）—— 2026-11-10 起 `Path` 写的是**真名**，
    所以这里只用小写做 `= 同一个文件` 的比较，不再假定表里是小写。
    """
    return {str((r or {}).get('Path') or '').replace('\\', '/').lower(): r
            for r in rows}


class TestScanWritesTheRightRows:
    """② `scan_assets` 写进表的 Category / Tag 必须和路径规则一致。"""

    def test_every_file_lands_exactly_once(self, hell):
        paths = _by_path(hell['rows'])
        missing = [rel for rel in hell['made'] if rel.lower() not in paths]
        assert not missing, f'扫漏了 {len(missing)} 个：{missing[:5]}'
        dupes = [rel for rel in paths if rel not in {x.lower() for x in hell['made']}]
        assert not dupes, f'多扫了：{dupes[:5]}'

    @pytest.mark.parametrize('rel,want_cat,want_tag', HELL)
    def test_row_category_and_tag(self, hell, rel, want_cat, want_tag):
        row = _by_path(hell['rows']).get(rel.lower())
        assert row is not None, f'{rel} 没进表'
        assert (row.get('Category'), row.get('Tag')) == (want_cat, want_tag), (
            f'{rel}\n  期望 {want_cat}/{want_tag!r}\n'
            f'  实际 {row.get("Category")}/{row.get("Tag")!r}')

    def test_path_is_stored_with_its_original_case(self, hell):
        paths = {str(r.get('Path') or '') for r in hell['rows']}
        assert 'game/gui/CG_frame.png' in paths, sorted(paths)[:5]

    def test_every_path_matches_the_disk_name_exactly(self, hell):
        """
        **2026-11-10 修**：`Path` 一律按磁盘**真名**写（不再规范化成小写）。

        为什么（CI 在 Linux 上抓到）：小写路径在区分大小写的文件系统上是
        "另一个文件" —— `打开源文件` / 缩略图 / 下一轮对账的"解析不到就删行"
        全都会栽在它上面。真机 1720 行全小写，其中两条图片在 Linux 上被判
        "文件不存在"、行被删掉（`test_asset_table_guards` 那条红）。
        跨平台可复现靠的是**小写索引键**（`_scan_file_items`），不是把真名改写掉。

        逐行比 `scandir`（不是 `is_file()`）：Windows 不区分大小写，
        `is_file()` 在上面的 bug 上照样返回 True，钉不住它。
        """
        root = hell['root']
        bad = []
        for row in hell['rows']:
            rel = str(row.get('Path') or '').replace('\\', '/')
            cand = root / rel
            if not cand.is_file():
                bad.append(f'{rel}（磁盘上没有这个文件）')
                continue
            if cand.name not in {e.name for e in os.scandir(cand.parent)}:
                bad.append(f'{rel}（大小写与磁盘不符）')
        assert not bad, bad[:5]

    def test_noise_never_reaches_the_table(self, hell):
        paths = set(_by_path(hell['rows']))
        for bad in ('Clipboard_Screenshot.png', '屏幕截图 2026-01-01.png',
                    '~$lock.png', 'half_done.png', 'save1.png'):
            assert not [p for p in paths if p.endswith(bad)], bad

    def test_ids_are_unique(self, hell):
        ids = [r['ID'] for r in hell['rows']]
        assert len(ids) == len(set(ids)), '同一棵树里出现了重复资产 ID'


# ==================================================================
#  ③ 身份层 + 选择器分组：用户真正看到的"用途"
# ==================================================================

@pytest.fixture(scope='module')
def hell_identity(hell):
    """扫描结果 → 注册表（再补一张角色表）→ 身份解析。"""
    from storygraph.assets.asset_identity import project_context
    from storygraph.assets.asset_registry import AssetRegistry

    reg = AssetRegistry()
    reg.images = {row['ID']: row for row in hell['rows']}
    reg.characters = {
        'l': {'ID': 'l', 'Name': '蓝', 'ImageTag': 'lan'},
        'ell': {'ID': 'ell', 'Name': 'Eileen', 'ImageTag': 'eileen'},
        'm': {'ID': 'm', 'Name': 'Mary', 'ImageTag': 'mary'},
        'lg': {'ID': 'lg', 'Name': '凌', 'ImageTag': 'ling'},
        # tag 带下划线：`layeredimage star_girl:` 里 tag 就叫这个 —— 目录名
        # 转 tag 时**不许**把 `_` 换成空格（否则写出 `show star girl`）
        'sg': {'ID': 'sg', 'Name': 'Star Girl', 'ImageTag': 'star_girl'},
    }
    ctx = project_context(reg, str(hell['root']))
    from storygraph.app import picker as P
    idents = P.identities(reg, str(hell['root']), context=ctx)
    paths = {(str((r or {}).get('Path') or '')).replace('\\', '/').lower(): aid
             for aid, r in reg.images.items()}
    return {'root': hell['root'], 'reg': reg, 'ctx': ctx, 'idents': idents,
            'paths': paths}


class TestIdentityKindsOnTheHellTree:
    """③ 身份层：立绘不能被当成 UI，背景不能被编成角色。"""

    @pytest.mark.parametrize('rel,want_kind', [
        ('game/images/characters/lan/happy.png', 'sprite'),
        ('game/images/eileen/happy.png', 'sprite'),
        ('game/images/eileen_happy.png', 'sprite'),
        ('game/images/bg/room.jpg', 'bg'),
        ('game/images/nbg/bg1.1.png', 'bg'),
        ('game/images/cg/cg01.png', 'cg'),
        ('game/gui/button/ok.png', 'ui'),
        ('game/gui/CG_frame.png', 'ui'),
        ('game/charactercenter/base.png', 'ui'),
        ('game/cc_preview/lan/angry.png', 'preview'),
        ('game/images/preview/ling/happy.png', 'preview'),
    ])
    def test_kind(self, hell_identity, rel, want_kind):
        aid = hell_identity['paths'][rel.lower()]
        got = hell_identity['idents'][aid]
        assert got.kind == want_kind, (
            f'{rel} → kind={got.kind!r} tag={got.tag!r}')

    def test_nobody_who_is_a_character_ends_up_as_ui(self, hell_identity):
        """用户点名的错：立绘被分进 UI。"""
        bad = []
        for rel, aid in hell_identity['paths'].items():
            if not rel.endswith(('.png', '.jpg', '.jpeg', '.webp',
                                 '.PNG', '.JPG')):
                continue
            if '/characters/' not in '/' + rel and not rel.startswith(
                    ('game/images/eileen/', 'game/cc_preview/',
                     'game/images/preview/', 'game/images/star_girl/',
                     'game/art/chars/')):
                continue
            ident = hell_identity['idents'][aid]
            if ident.kind == 'ui':
                bad.append((rel, ident.kind, ident.tag))
        assert not bad, bad

    def test_no_background_becomes_a_character(self, hell_identity):
        bad = []
        for rel, aid in hell_identity['paths'].items():
            if not any(part in rel for part in ('/bg/', '/nbg/', '/bgs/',
                                                '/backgrounds/')):
                continue
            ident = hell_identity['idents'][aid]
            if ident.kind not in ('bg', 'unknown', 'other'):
                bad.append((rel, ident.kind, ident.tag))
        assert not bad, bad

    def test_junk_directories_produce_no_fake_sprite_tag(self, hell_identity):
        """`try` / `rhythm_game` / `effects` 里的图不许带出一个假 tag。"""
        for rel, aid in hell_identity['paths'].items():
            if not any(part in rel for part in ('/try/', '/rhythm_game/',
                                                '/effects/')):
                continue
            ident = hell_identity['idents'][aid]
            assert ident.kind != 'sprite', (rel, ident)
            assert ident.tag not in ('try', 'rhythm game', 'rhythm_game',
                                     'effects'), (rel, ident)

    def test_a_preview_copy_with_a_character_tag_is_still_not_stageable(self):
        """
        **表里写错了也不许放行**：手改过的表/旧扫描器可能给预览副本写了
        `Tag=lan`，而 `cc_preview/lan/angry.png` 是 1/4 预览图 ——
        身份层必须照**目录**判 preview，否则 `show lan angry` 指到一张小图上。
        """
        from storygraph.assets.asset_identity import resolve_image

        chars = {'lan': {'display': '蓝'}}
        got = resolve_image(
            {'ID': 'x', 'Path': 'game/cc_preview/lan/angry.png',
             'Tag': 'lan', 'Attributes': 'angry'},
            relpath='game/cc_preview/lan/angry.png',
            known_tags={'lan'}, known_chars=chars)
        assert (got.kind, got.tag, got.showable) == ('preview', 'lan', False)

        avatar = resolve_image(
            {'ID': 'y', 'Path': 'game/cc_preview/avatar/lan.png',
             'Tag': 'lan', 'Attributes': 'lan'},
            relpath='game/cc_preview/avatar/lan.png',
            known_tags={'lan'}, known_chars=chars)
        assert (avatar.kind, avatar.tag) == ('avatar', 'lan')


@pytest.fixture(scope='module')
def hell_groups(hell_identity):
    """选择器左侧分组（`categorize_assets`，一次算好给整类用）。"""
    from storygraph.app.beats import categorize_assets

    class _Engine:
        def __init__(self, reg, root):
            self._reg, self.project_root = reg, root

        def get_registry(self):
            return self._reg

    return categorize_assets(
        _Engine(hell_identity['reg'], str(hell_identity['root'])))


class TestPickerGroupsOnTheHellTree:
    """③ 选择器左侧分组：不许出现 `立绘：nbg` 这种凭空编出来的人。"""

    def test_no_fake_sprite_groups(self, hell_groups):
        fake = [name for name in hell_groups
                if name.startswith('立绘：')
                and any(bad in name for bad in
                        ('nbg', 'try', 'rhythm', 'effects', 'backgrounds'))]
        assert not fake, fake

    def test_real_characters_are_in_sprite_groups(self, hell_groups):
        names = [n for n in hell_groups if n.startswith('立绘：')]
        for who in ('lan', 'eileen', 'mary', 'star_girl'):
            assert any(who in n for n in names), (who, names)

    def test_backgrounds_are_in_the_bg_group_not_elsewhere(self, hell_groups):
        bg_ids = {aid for _t, aid in hell_groups.get('背景', [])}
        assert bg_ids, hell_groups.keys()
        for want in ('bg1.1', 'room', 'school', 'night', 'beach'):
            assert any(want in aid for aid in bg_ids), (want, sorted(bg_ids))


# ==================================================================
#  ④ 真机回归：sr_general 里被归错的四个目录（2026-11-02 实测）
# ==================================================================

REAL_MISCLASSIFIED = [
    # 42 行：`nbg` = new bg，名字里缩着 bg
    ('game/images/nbg/bg1.1.png', 'background', 'bg'),
    ('game/images/nbg/bg9.5.png', 'background', 'bg'),
    ('game/images/nbg/bg_1.jpg', 'background', 'bg'),
    ('game/images/nbg/_line.png', 'background', 'bg'),
    # 17 行：换装/转场用的试验图，不是谁能 show 的立绘
    ('game/images/try/changeover_lan.png', 'other', ''),
    ('game/images/try/Zhuanlan.png', 'other', ''),
    # 4 行：节奏小游戏的箭头
    ('game/images/rhythm_game/down.png', 'other', ''),
    ('game/images/rhythm_game/left.png', 'other', ''),
    # 159 行：画廊用的 1/4 预览副本
    ('game/cc_preview/lan/angry.png', 'character', ''),
    ('game/cc_preview/lan/dynamic_images/lan_body.png', 'character', ''),
    ('game/cc_preview/avatar/lan.png', 'character', ''),
]


class TestRealProjectMisclassifications:
    """真机实测被归错的路径 —— 每一条都对应表里几十行。"""

    @pytest.mark.parametrize('rel,want_cat,want_tag', REAL_MISCLASSIFIED)
    def test_case(self, rel, want_cat, want_tag):
        assert (_cat(rel), _tag(rel)) == (want_cat, want_tag)
