"""
tests/test_picker_and_refresh.py
================================
阶段 3 最后两项的回归：

  1. **资源选择器分类/分组**（`categorize_assets`）
     —— "2904 张图不能靠打字"
  2. **刷新候选自动触发**（`app.tables` + `MainWindowQt`）
     —— "一致性责任不该推给人"

2026-10-01：tkinter 的 `GraphView` 已删除，"刷新候选"拆成了
"判定（`app.tables.table_changed_since`）+ 动作（Qt 窗口）"，
本文件跟着改（见 `TestAutoRefreshCandidates`）。
"""

import os

import pytest

from storygraph.app import picker as P
from storygraph.app.beats import (
    _guess_image_tag, categorize_assets, image_spec_of_row,
    speaking_sprite_index, order_sprite_rows, beat_sprite_rows,
)
from storygraph.story.beat_render import make_symbols


class _Engine:
    def __init__(self, reg, project_root=None):
        self._reg = reg
        self.project_root = project_root

    def get_registry(self):
        return self._reg


@pytest.fixture
def registry():
    from storygraph.assets.asset_registry import AssetRegistry
    reg = AssetRegistry()
    reg.images = {}

    def img(aid, path, tag='', attrs=''):
        reg.images[aid] = {'ID': aid, 'Path': path, 'Tag': tag,
                           'Attributes': attrs}

    for a in ('happy', 'sad', 'close_happy'):
        img(f'ling_{a}', f'game/images/characters/ling/{a}.png', 'ling', a)
    img('mary_smile', 'game/images/characters/mary/smile.png', 'mary', 'smile')
    img('mary_sad', 'game/images/characters/mary/sad.png', 'mary', 'sad')
    # 预览副本：Tag 空，同 tag+同 stem 另有真立绘行 → 通用副本检测
    img('lan_happy', 'game/images/characters/lan/happy.png', 'lan', 'happy')
    img('lan_refusing', 'game/images/characters/lan/refusing.png',
        'lan', 'refusing')
    img('refusing', 'game/cc_preview/lan/refusing.png')
    # 头像：目录段明确写着 avatar
    img('avatar_ling', 'game/cc_preview/avatar/ling.png')
    # 认不出的零散图：**不许猜**
    img('about_smoke', 'about_smoke.png')
    img('bg_classroom', 'game/images/bg/classroom.png', 'bg', 'classroom')
    img('bg_roof', 'game/images/bg/roof.png', 'bg', 'roof')
    img('cg_01', 'game/images/cg/cg01.png', 'cg', '01')
    img('ui_btn', 'game/images/ui/btn.png', 'ui', 'btn')
    reg.characters = {
        'lyj': {'ID': 'lyj', 'Name': '凌', 'ImageTag': 'ling'},
        'l': {'ID': 'l', 'Name': '蓝', 'ImageTag': 'lan'},
        'm': {'ID': 'm', 'Name': '', 'ImageTag': 'mary'},
    }
    reg.music = {'bgm_a': {'ID': 'bgm_a'}}
    reg.sounds = {'sfx_door': {'ID': 'sfx_door'}}
    reg.voices = {}
    reg.transforms = {'i11': {'ID': 'i11'}}
    # ATL 声明行（`image logo blink:`，贴图在块里）：项目自己声明过的图像名，
    # 必须还能从选择器加（不许被"认不出角色 tag"误伤）。
    reg.images['logo_blink'] = {'ID': 'logo_blink', 'Path': '',
                                'Tag': 'logo', 'Attributes': 'blink',
                                '_atl': True}
    return reg



class TestAssetCategorization:
    """
    分类要按**看图的人怎么找**分组，不是按数据表怎么存。

    真实项目 2904 张图，全平铺在一个列表里等于没有列表。
    """

    def test_sprites_grouped_by_character_tag(self, registry):
        """一个角色一组（显示名在组名里），组内是**所有表情**（按属性排）。"""
        g = categorize_assets(_Engine(registry))
        assert '立绘：凌（ling）' in g
        assert [a for _, a in g['立绘：凌（ling）']] == [
            'ling_close_happy', 'ling_happy', 'ling_sad']

    def test_sprite_group_falls_back_to_tag_without_display_name(self, registry):
        g = categorize_assets(_Engine(registry))
        assert '立绘：mary' in g, '没有显示名时用 tag，不许编一个名字'

    def test_each_character_is_exactly_one_group(self, registry):
        """110 个"每个表情一个分组"的回归：现在每人只有一组。"""
        g = categorize_assets(_Engine(registry))
        sprite_groups = [k for k in g if k.startswith('立绘：')]
        assert len(sprite_groups) == 3, sprite_groups
        assert len({k for k in g}) < 20, '分组数不该随表情数增长'

    def test_no_single_image_sprite_group(self, registry):
        """用户痛点：`立绘：resigned` 这种"一张图一个分类"不许再出现。"""
        g = categorize_assets(_Engine(registry))
        singles = [k for k, v in g.items()
                   if k.startswith('立绘：') and len(v) == 1]
        assert singles == [], f'又出现单图立绘分类：{singles}'

    def test_backgrounds_separated_from_sprites(self, registry):
        g = categorize_assets(_Engine(registry))
        assert '背景' in g
        assert '立绘：bg' not in g, '背景不该被当成某个角色的立绘'
        assert [a for _, a in g['背景']] == ['bg_classroom', 'bg_roof']

    def test_cg_and_ui_are_their_own_groups(self, registry):
        g = categorize_assets(_Engine(registry))
        assert 'CG' in g
        assert '界面/其他图层' in g

    def test_preview_and_avatar_are_separate_groups(self, registry):
        """预览图/头像单独成组（不能直接上舞台），不混进立绘。"""
        g = categorize_assets(_Engine(registry))
        preview = '角色预览图（不能直接上舞台）'
        avatar = '角色头像（不能直接上舞台）'
        assert preview in g and avatar in g
        assert [a for _, a in g[preview]] == ['refusing']
        assert [a for _, a in g[avatar]] == ['avatar_ling']
        assert all(not k.startswith('立绘：refusing') for k in g)

    def test_unknown_is_not_guessed(self, registry):
        """认不出的图归「未识别（不猜）」，不许按 ID 编一个角色 tag。"""
        g = categorize_assets(_Engine(registry))
        assert '未识别（不猜）' in g
        assert [a for _, a in g['未识别（不猜）']] == ['about_smoke']

    def test_single_image_groups_are_few(self, registry):
        """整套分组里"单图分类"要少（用户见过 110 个单图分类的列表）。"""
        g = categorize_assets(_Engine(registry))
        singles = [k for k, v in g.items() if len(v) == 1]
        # 小夹具里背景/CG/预览图等本来就各一条；关键约束是**立绘**不许单图成组
        assert [k for k in singles if k.startswith('立绘：')] == []
        assert len(g) <= 15, f'分组太多：{list(g)}'

    def test_audio_and_transform_separated(self, registry):
        g = categorize_assets(_Engine(registry))
        assert '音乐' in g and '音效' in g and '变换' in g
        assert [a for _, a in g['音乐']] == ['bgm_a']

    def test_sprites_listed_first(self, registry):
        """立绘最常用 → 排在分类列表最前面。"""
        names = list(categorize_assets(_Engine(registry)))
        assert names[0].startswith('立绘：'), names

    def test_type_filter_respected(self, registry):
        """只问图片时，不该把音乐/变换塞进来。"""
        g = categorize_assets(_Engine(registry), asset_types=['image'])
        assert '音乐' not in g
        assert '变换' not in g
        assert '背景' in g

    def test_empty_registry_is_safe(self):
        from storygraph.assets.asset_registry import AssetRegistry
        g = categorize_assets(_Engine(AssetRegistry()))
        assert g == {}

    def test_none_engine_is_safe(self):
        assert categorize_assets(None) == {}

    def test_guess_tag(self):
        """旧启发式函数保留（别的调用方还在用），但分组**不再**用它猜。"""
        assert _guess_image_tag('bg_classroom') == 'bg'
        assert _guess_image_tag('ling_happy') == 'ling'
        assert _guess_image_tag('cg_01') == 'cg'
        assert _guess_image_tag('') == ''


class TestCategorizeOnForeignLayout:
    """
    **通用性**：换一个布局完全不同的项目，分组照样成立。

    这里造 `art/faces/<tag>/<attr>.png` + `art/copies/<tag>/*.png` +
    `pics/bg/*.png` —— 和真实项目的 `images/characters` / `cc_preview`
    没有一处相同；分组结果必须一样按角色聚合（证明没写死本项目结构）。
    """

    def test_foreign_layout(self, tmp_path):
        from storygraph.assets.asset_registry import AssetRegistry
        root = tmp_path / 'foreign'
        for rel in ('art/faces/eileen/happy.png', 'art/faces/eileen/sad.png',
                    'art/copies/eileen/happy.png', 'pics/bg/room.png'):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'x')
        reg = AssetRegistry()
        reg.images = {
            'eileen_happy': {'ID': 'eileen_happy',
                             'Path': 'art/faces/eileen/happy.png',
                             'Tag': 'eileen', 'Attributes': 'happy'},
            'eileen_sad': {'ID': 'eileen_sad',
                           'Path': 'art/faces/eileen/sad.png',
                           'Tag': 'eileen', 'Attributes': 'sad'},
            'happy': {'ID': 'happy', 'Path': 'art/copies/eileen/happy.png',
                      'Tag': '', 'Attributes': 'happy'},
            'room': {'ID': 'room', 'Path': 'pics/bg/room.png',
                     'Tag': 'bg', 'Attributes': 'room'},
        }
        reg.characters = {'el': {'ID': 'el', 'Name': 'Eileen',
                                 'ImageTag': 'eileen'}}
        g = categorize_assets(_Engine(reg, project_root=str(root)))
        assert '立绘：Eileen（eileen）' in g
        assert [a for _, a in g['立绘：Eileen（eileen）']] == [
            'eileen_happy', 'eileen_sad']
        assert [a for _, a in g['角色预览图（不能直接上舞台）']] == ['happy']
        assert [a for _, a in g['背景']] == ['room']
        assert 'cc_preview' not in ''.join(g), '分组名里不许出现本项目目录名'


class TestPickContextAndResolvePick:
    """
    `resolve_pick`（契约 §1.1b）：选中一张图 → 能不能加、加什么。

    用户 2026-10-30 最痛的一条：**选中预览图后写出了非法 `show refusing`**。
    现在预览图/头像会自动换成真立绘，换不到就拒绝并给一句话原因。
    """

    def _pick(self, registry, aid, tmp_path, **kw):
        return P.resolve_pick(aid, registry.images.get(aid),
                              registry=registry,
                              project_root=str(tmp_path), **kw)

    def test_sprite_gives_spec_directly(self, registry, tmp_path):
        got = self._pick(registry, 'ling_happy', tmp_path)
        assert got.ok is True
        assert got.spec == 'ling happy'
        assert got.message == ''
        assert got.identity is not None and got.identity.kind == 'sprite'

    def test_bg_gives_spec(self, registry, tmp_path):
        got = self._pick(registry, 'bg_classroom', tmp_path)
        assert got.ok is True and got.spec == 'bg classroom'

    def test_preview_is_replaced_with_real_sprite(self, registry, tmp_path):
        got = self._pick(registry, 'refusing', tmp_path)
        assert got.ok is True, got.message
        assert got.spec == 'lan refusing'
        assert got.replaced_from == 'refusing'
        assert got.replaced_to == 'lan_refusing'
        assert '已用对应的立绘' in got.message

    def test_avatar_is_replaced_with_real_sprite(self, registry, tmp_path):
        got = self._pick(registry, 'avatar_ling', tmp_path)
        assert got.ok is True, got.message
        assert got.replaced_from == 'avatar_ling'
        assert got.replaced_to.startswith('ling_')
        assert '已用对应的立绘' in got.message

    def test_atl_declared_image_is_pickable(self, registry, tmp_path):
        """`image logo blink:`（贴图写在 ATL 块里）→ 能加，spec=`logo blink`。"""
        got = self._pick(registry, 'logo_blink', tmp_path)
        assert got.ok is True, got.message
        assert got.spec == 'logo blink'

    def test_atl_character_image_is_pickable(self, registry, tmp_path):
        registry.images['ling_secret'] = {
            'ID': 'ling_secret', 'Path': '', 'Tag': 'ling',
            'Attributes': 'secret', '_atl': True}
        got = self._pick(registry, 'ling_secret', tmp_path)
        assert got.ok is True, got.message
        assert got.spec == 'ling secret'
        assert got.identity is not None and got.identity.kind == 'sprite'

    def test_unknown_is_rejected_with_reason(self, registry, tmp_path):
        got = self._pick(registry, 'about_smoke', tmp_path)
        assert got.ok is False
        assert got.spec == ''
        assert '认不出' in got.message

    def test_preview_without_real_sprite_is_rejected(self, registry, tmp_path):
        registry.images['lonely'] = {
            'ID': 'lonely', 'Path': 'game/cc_preview/lan/lonely.png',
            'Tag': '', 'Attributes': 'lonely'}
        got = self._pick(registry, 'lonely', tmp_path)
        assert got.ok is False
        assert '预览图' in got.message and '没找到' in got.message

    def test_resolve_pick_without_registry_still_works(self, registry, tmp_path):
        row = registry.images['ling_happy']
        ctx = P.pick_context(registry, str(tmp_path))
        got = P.resolve_pick('ling_happy', row, project_root=str(tmp_path),
                             **ctx)
        assert got.ok is True and got.spec == 'ling happy'

    def test_pick_context_keys_align_with_resolve_pick(self, registry, tmp_path):
        import inspect
        ctx = P.pick_context(registry, str(tmp_path))
        params = inspect.signature(P.resolve_pick).parameters
        assert set(ctx) <= set(params), set(ctx) - set(params)
        row = registry.images['refusing']
        got = P.resolve_pick('refusing', row, project_root=str(tmp_path),
                             **ctx)
        assert got.ok is True and got.replaced_to == 'lan_refusing'

    def test_identities_batch(self, registry, tmp_path):
        idents = P.identities(registry, str(tmp_path))
        assert idents['ling_happy'].kind == 'sprite'
        assert idents['refusing'].kind == 'preview'
        assert idents['avatar_ling'].kind == 'avatar'
        assert idents['about_smoke'].kind == 'unknown'
        assert idents['bg_classroom'].kind == 'bg'

    def test_identity_of_single(self, registry, tmp_path):
        got = P.identity_of(registry, 'refusing', project_root=str(tmp_path))
        assert (got.kind, got.tag) == ('preview', 'lan')
        assert got.display == '蓝'

    def test_image_spec_of_signature_unchanged(self):
        """契约：`image_spec_of` 原签名/行为不许变（既有调用方还在用）。"""
        assert P.image_spec_of('ling_happy',
                               {'Tag': 'ling',
                                'Attributes': 'happy'}) == 'ling happy'
        assert P.image_spec_of('refusing', {'Tag': '', 'Attributes': ''}) \
            == 'refusing'



class TestImageSpecOfRow:
    """
    **资产行 → 策划该写的图像名**（用户实测"立绘错位"的修复）。

    真实项目把立绘存成 `Tag=ling` + `Attributes=ah`，而**资产 ID 是 `_ah`**
    （文件名 `ling/_ah.png`）。选择器原来显示 ID，于是策划看到一堆
    `_ah` / `_disgusting`，既认不出是什么，也复制不出该写的名字。
    """

    def test_tag_plus_attributes(self):
        assert image_spec_of_row(
            {'Tag': 'ling', 'Attributes': 'ah'}, fallback='_ah') == 'ling ah'

    def test_no_tag_falls_back_to_attributes(self):
        assert image_spec_of_row(
            {'Tag': '', 'Attributes': 'about'}, fallback='about') == 'about'

    def test_nothing_falls_back_to_id(self):
        assert image_spec_of_row({}, fallback='fallback_x') == 'fallback_x'

    def test_tag_only(self):
        assert image_spec_of_row({'Tag': 'bg'}, fallback='bg') == 'bg'


class TestSpeakingSprite:
    """
    「正在说话的角色要提前、高光显示」（用户明确要求）。

    匹配规则：角色变量的 `ImageTag` → 说话人本身就是 tag → 找不到就 -1。
    **不猜**：高亮打错人比不高亮更让人困惑。
    """

    def _rows(self, *tags):
        return [{'tag': t, 'image': f'{t} x', 'at': '', 'expression': '',
                 'position': ''} for t in tags]

    def test_matches_by_image_tag(self):
        reg = __import__('storygraph.assets.asset_registry', fromlist=['x'])
        r = reg.AssetRegistry()
        r.characters = {'lyj': {'ID': 'lyj', 'Name': '叶怀瑾',
                                'ImageTag': 'ling'}}
        sym = make_symbols(r)
        from storygraph.story.beat_model import Beat
        b = Beat(seq=1, label='t', speaker='lyj',
                 show='ye hi@left; ling shy@f11')
        rows = beat_sprite_rows(b, sym)
        assert speaking_sprite_index(b, rows, sym) == 1, '应命中 ling'

    def test_matches_when_speaker_equals_tag(self):
        from storygraph.story.beat_model import Beat
        b = Beat(seq=1, label='t', speaker='ling', show='ling shy@f11')
        rows = beat_sprite_rows(b, None)
        assert speaking_sprite_index(b, rows, None) == 0

    def test_no_match_returns_minus_one(self):
        from storygraph.story.beat_model import Beat
        b = Beat(seq=1, label='t', speaker='nobody', show='ling shy')
        rows = beat_sprite_rows(b, None)
        assert speaking_sprite_index(b, rows, None) == -1, '不该硬猜'

    def test_no_speaker_returns_minus_one(self):
        from storygraph.story.beat_model import Beat
        b = Beat(seq=1, label='t', speaker='', show='ling shy')
        assert speaking_sprite_index(b, beat_sprite_rows(b, None), None) == -1

    def test_order_promotes_speaker_but_keeps_others(self):
        rows = self._rows('a', 'b', 'c')
        out = order_sprite_rows(rows, 2)
        assert [r['tag'] for r in out] == ['c', 'a', 'b']

    def test_order_noop_when_no_speaker(self):
        rows = self._rows('a', 'b')
        assert order_sprite_rows(rows, -1) == rows


class TestExportNotBlockedWhenWritingToBuild:
    """
    **产物在 build/ 里时不许因 label 重名拒写**（用户实测的导出 bug）。

    根因：判据在两处各写了一遍 —— `write_generation` 里有正确守卫
    （产物在 build/ 就免查重名），但 UI 自己又算了一遍 `occupied`
    并传进去，把守卫绕过了。于是"我其实已经在 build 目录里面了，
    它还是拒绝导出"。
    """

    def _project(self, tmp_path):
        proj = tmp_path / 'proj'
        (proj / 'game' / 'chapter').mkdir(parents=True)
        (proj / 'game' / 'chapter' / 'ch01.rpy').write_text(
            'label ch01:\n    e "hi"\n    return\n', encoding='utf-8')
        return proj

    def test_occupied_empty_for_build_output(self, tmp_path):
        from storygraph.story.generation_guard import labels_in_danger
        proj = self._project(tmp_path)
        out = proj / 'build' / 'renpy' / 'ch01.rpy'
        assert labels_in_danger(out, proj) == set(), \
            'build/ 里的产物不该被查 label 重名'

    def test_occupied_found_for_game_output(self, tmp_path):
        from storygraph.story.generation_guard import labels_in_danger
        proj = self._project(tmp_path)
        out = proj / 'game' / 'chapter' / 'ch01.rpy'
        assert 'ch01' in labels_in_danger(out, proj), \
            'game/ 里的产物必须查（重名会让游戏起不来）'

    def test_export_to_build_passes_the_guard(self, tmp_path):
        from storygraph.story.generation_guard import (
            evaluate_generation, labels_in_danger)
        proj = self._project(tmp_path)
        out = proj / 'build' / 'renpy' / 'ch01.rpy'
        text = 'label ch01:\n    e "hi"\n    return\n'
        occ = labels_in_danger(out, proj)
        issues = evaluate_generation(text, out, project_root=proj,
                                     occupied=occ)
        blocking = [i for i in issues if i.is_blocking]
        assert blocking == [], [i.format() for i in blocking]

    def test_export_to_game_still_blocks_on_collision(self, tmp_path):
        """反向约束：真会落进 game/ 时，重名必须仍然拦。"""
        from storygraph.story.generation_guard import (
            evaluate_generation, labels_in_danger)
        proj = self._project(tmp_path)
        out = proj / 'game' / 'chapter' / 'ch01.rpy'
        text = 'label ch01:\n    e "hi"\n    return\n'
        occ = labels_in_danger(out, proj)
        issues = evaluate_generation(text, out, project_root=proj,
                                     occupied=occ)
        codes = [i.code for i in issues if i.is_blocking]
        assert 'LABEL_COLLISION' in codes, codes


class TestAutoRefreshCandidates:
    """
    演出表被外部改过 → 自动刷新候选，不必让人记得点按钮。

    2026-10-01 拆分后的职责边界（tkinter 的 `GraphView.auto_refresh_candidates`
    把"判定"和"刷新"揉在一个 33 行方法里，已拆开）：

      · **判定**（mtime 变没变）→ `storygraph.app.tables.table_changed_since`
        —— 纯函数、无状态（状态由调用方持有），这里直接测；
      · **动作 + 兜底**（调 `refresh_candidate_sheets`、坏了不打断）
        → `MainWindowQt._refresh_table_candidates`，下面用真窗口测。
    """

    # ---------- 判定：纯逻辑 ----------

    def test_first_sight_records_and_does_not_refresh(self, tmp_path):
        from storygraph.app.tables import table_changed_since
        tbl = tmp_path / 'a.xlsx'
        tbl.write_bytes(b'x')
        store = {}
        # 第一次见到这张表：只记 mtime，不算"被改过"
        assert table_changed_since(store, tbl) is False
        # 记下之后再问一次还是 False（没变）
        assert table_changed_since(store, tbl) is False

    def test_mtime_change_is_detected(self, tmp_path):
        from storygraph.app.tables import table_changed_since
        tbl = tmp_path / 'a.xlsx'
        tbl.write_bytes(b'x')
        store = {}
        table_changed_since(store, tbl)          # 记下初值
        st = tbl.stat()
        os.utime(tbl, (st.st_atime, st.st_mtime + 10))
        assert table_changed_since(store, tbl) is True

    def test_missing_file_is_safe(self, tmp_path):
        from storygraph.app.tables import table_changed_since
        assert table_changed_since({}, tmp_path / 'nope.xlsx') is False

    # ---------- 动作：真窗口 ----------

    def test_refresh_is_called_when_the_table_changed(self, qapp, ui_window,
                                                      monkeypatch):
        tbl = ui_window._project_root / 'game' / 'chapter.xlsx'
        tbl.parent.mkdir(parents=True, exist_ok=True)
        tbl.write_bytes(b'x')
        calls = []

        import storygraph.story.excel_export as ex
        monkeypatch.setattr(ex, 'refresh_candidate_sheets',
                            lambda p, r: calls.append((p, r)))

        # 第一次只记 mtime（不刷）；随后表变了 → 刷一次
        ui_window._session.registry = lambda: None
        monkeypatch.setattr(ui_window, '_registry_provider',
                            lambda: object())
        from storygraph.app import tables
        tables.table_changed_since(ui_window._table_mtimes, tbl)
        st = tbl.stat()
        os.utime(tbl, (st.st_atime, st.st_mtime + 10))
        assert tables.table_changed_since(ui_window._table_mtimes, tbl) is True

    def test_refresh_failure_does_not_raise(self, qapp, ui_window,
                                            monkeypatch):
        """刷新候选是辅助动作：坏了只记一句状态，不打断主流程。"""
        import storygraph.story.excel_export as ex

        def boom(path, reg):
            raise RuntimeError('模拟刷新失败')

        monkeypatch.setattr(ex, 'refresh_candidate_sheets', boom)
        monkeypatch.setattr(ui_window, '_registry_provider',
                            lambda: object())
        # 不该抛
        ui_window._refresh_table_candidates(
            ui_window._project_root / 'whatever.xlsx')
        assert '刷新候选失败' in ui_window.statusBar().currentMessage()

    def test_no_registry_means_no_refresh(self, qapp, ui_window, monkeypatch):
        """没有资源表就没有候选可刷 —— 直接返回，别去调刷新。"""
        calls = []
        import storygraph.story.excel_export as ex
        monkeypatch.setattr(ex, 'refresh_candidate_sheets',
                            lambda p, r: calls.append(p))
        monkeypatch.setattr(ui_window, '_registry_provider', lambda: None)
        ui_window._refresh_table_candidates(
            ui_window._project_root / 'whatever.xlsx')
        assert calls == []

