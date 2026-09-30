"""
tests/test_picker_and_refresh.py
================================
阶段 3 最后两项的回归：

  1. **资源选择器分类/分组**（`categorize_assets`）
     —— "2904 张图不能靠打字"
  2. **刷新候选自动触发**（`GraphView.auto_refresh_candidates`）
     —— "一致性责任不该推给人"
"""

import os

import pytest

from storygraph.ui.beat_editor import (
    _guess_image_tag, categorize_assets, image_spec_of_row,
    speaking_sprite_index, order_sprite_rows, beat_sprite_rows,
    make_symbols,
)


class _Engine:
    def __init__(self, reg):
        self._reg = reg

    def get_registry(self):
        return self._reg


@pytest.fixture
def registry():
    from storygraph.assets.asset_registry import AssetRegistry
    reg = AssetRegistry()
    reg.images = {}
    for a in ('happy', 'sad', 'close_happy'):
        reg.images[f'ling_{a}'] = {'ID': f'ling_{a}', 'Tag': 'ling',
                                   'Attributes': a}
    reg.images['mary_smile'] = {'ID': 'mary_smile', 'Tag': 'mary',
                                'Attributes': 'smile'}
    reg.images['bg_classroom'] = {'ID': 'bg_classroom', 'Tag': 'bg',
                                  'Attributes': 'classroom'}
    reg.images['bg_roof'] = {'ID': 'bg_roof', 'Tag': 'bg',
                             'Attributes': 'roof'}
    reg.images['cg_01'] = {'ID': 'cg_01', 'Tag': 'cg', 'Attributes': '01'}
    reg.images['ui_btn'] = {'ID': 'ui_btn', 'Tag': 'ui', 'Attributes': 'btn'}
    reg.music = {'bgm_a': {'ID': 'bgm_a'}}
    reg.sounds = {'sfx_door': {'ID': 'sfx_door'}}
    reg.voices = {}
    reg.transforms = {'i11': {'ID': 'i11'}}
    reg.characters = {'lyj': {'ID': 'lyj'}}
    return reg


class TestAssetCategorization:
    """
    分类要按**看图的人怎么找**分组，不是按数据表怎么存。

    真实项目 2904 张图，全平铺在一个列表里等于没有列表。
    """

    def test_sprites_grouped_by_character_tag(self, registry):
        g = categorize_assets(_Engine(registry))
        assert '立绘：ling' in g
        assert '立绘：mary' in g
        ids = [a for _, a in g['立绘：ling']]
        assert ids == ['ling_close_happy', 'ling_happy', 'ling_sad'], ids

    def test_backgrounds_separated_from_sprites(self, registry):
        g = categorize_assets(_Engine(registry))
        assert '背景' in g
        assert '立绘：bg' not in g, '背景不该被当成某个角色的立绘'
        assert [a for _, a in g['背景']] == ['bg_classroom', 'bg_roof']

    def test_cg_and_ui_are_their_own_groups(self, registry):
        g = categorize_assets(_Engine(registry))
        assert 'CG' in g
        assert '界面/其他图层' in g

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
        assert _guess_image_tag('bg_classroom') == 'bg'
        assert _guess_image_tag('ling_happy') == 'ling'
        assert _guess_image_tag('cg_01') == 'cg'
        assert _guess_image_tag('') == ''


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

    这里测的是 `GraphView.auto_refresh_candidates` 的**判定逻辑**：
    只在 mtime 变了才刷、失败不打断。
    """

    def _view(self, reg):
        from storygraph.ui.graph_view import GraphView

        v = GraphView.__new__(GraphView)
        v._table_mtimes = {}
        v.project_root = None
        v.asset_engine = _Engine(reg)
        v._logs = []
        v._append_analysis = lambda t: v._logs.append(t)
        return v

    def test_first_sight_records_and_does_not_refresh(self, tmp_path, registry):
        tbl = tmp_path / 'a.xlsx'
        tbl.write_bytes(b'x')
        v = self._view(registry)
        assert v.auto_refresh_candidates(tbl) is False
        # 记下 mtime 之后，再问一次还是 False（没变）
        assert v.auto_refresh_candidates(tbl) is False

    def test_mtime_change_triggers_refresh(self, tmp_path, registry, monkeypatch):
        tbl = tmp_path / 'a.xlsx'
        tbl.write_bytes(b'x')
        v = self._view(registry)
        v.auto_refresh_candidates(tbl)          # 记下初值

        calls = []

        def fake_refresh(path, reg):
            calls.append((path, reg))
            return path

        import storygraph.story.excel_export as ex
        monkeypatch.setattr(ex, 'refresh_candidate_sheets', fake_refresh)

        st = tbl.stat()
        os.utime(tbl, (st.st_atime, st.st_mtime + 10))
        assert v.auto_refresh_candidates(tbl) is True
        assert len(calls) == 1
        # 刷完再问一次 → 不再重复刷
        assert v.auto_refresh_candidates(tbl) is False

    def test_no_registry_means_no_refresh(self, tmp_path):
        v = self._view(None)
        v.asset_engine = None
        tbl = tmp_path / 'a.xlsx'
        tbl.write_bytes(b'x')
        assert v.auto_refresh_candidates(tbl) is False

    def test_missing_file_is_safe(self, tmp_path, registry):
        v = self._view(registry)
        assert v.auto_refresh_candidates(tmp_path / 'nope.xlsx') is False

    def test_refresh_failure_does_not_raise(self, tmp_path, registry,
                                            monkeypatch):
        """刷新候选是辅助动作：坏了只记一行日志，不打断主流程。"""
        tbl = tmp_path / 'a.xlsx'
        tbl.write_bytes(b'x')
        v = self._view(registry)
        v.auto_refresh_candidates(tbl)

        def boom(path, reg):
            raise RuntimeError('模拟刷新失败')

        import storygraph.story.excel_export as ex
        monkeypatch.setattr(ex, 'refresh_candidate_sheets', boom)

        st = tbl.stat()
        os.utime(tbl, (st.st_atime, st.st_mtime + 10))
        assert v.auto_refresh_candidates(tbl) is False    # 没抛
        assert any('自动刷新候选失败' in m for m in v._logs)
