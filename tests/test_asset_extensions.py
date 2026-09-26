"""
tests/test_asset_extensions.py
==============================
资产快照、变更追踪、依赖分析、重命名补丁、补全、面板模型、工作区测试。
"""

import pytest

from asset_registry import AssetRegistry
from asset_extensions import (
    AssetSnapshot, AssetChange, AssetChangeTracker,
    DependencyAnalyzer, generate_rename_patch,
    AssetCompletionProvider, AssetPanelModel, AssetWorkspace,
)


@pytest.fixture
def registry():
    reg = AssetRegistry()
    reg.images = {
        'bg_room': {'ID': 'bg_room', 'Path': 'images/bg/room.png',
                    'Tag': 'bg', 'Attributes': 'room',
                    'Category': 'background', 'Notes': ''},
        'eileen': {'ID': 'eileen', 'Path': 'images/eileen.png',
                   'Tag': 'eileen', 'Attributes': 'eileen',
                   'Category': 'character', 'Notes': ''},
    }
    reg.music = {'bgm_theme': {'ID': 'bgm_theme', 'Path': 'audio/a.ogg',
                               'Loop': True, 'Volume': 1.0, 'Notes': ''}}
    reg.sounds = {'sfx_click': {'ID': 'sfx_click', 'Path': 'audio/c.ogg',
                                'Notes': ''}}
    reg.transforms = {'leftish': {'ID': 'leftish', 'Path': '', 'Notes': ''}}
    reg.characters = {'e': {'ID': 'e', 'Name': 'Eileen', 'ImageTag': 'eileen',
                            'Notes': ''}}
    reg.custom = {'props': {'k1': {'ID': 'k1', 'Value': 'v'}}}
    reg.references['bg_room'] = ['script.rpy:1']
    reg.references['ghost'] = ['script.rpy:9']
    return reg


# ------------------------------------------------------------------
#  快照
# ------------------------------------------------------------------

class TestSnapshot:
    def test_populated_from_registry(self, registry):
        snap = AssetSnapshot(registry)
        assert set(snap.data.keys()) == set(AssetSnapshot.REGISTRY_NAMES)
        assert 'bg_room' in snap.data['images']

    def test_custom_and_references_captured(self, registry):
        snap = AssetSnapshot(registry)
        assert 'k1' in snap.custom['props']
        assert snap.references['bg_room'] == ['script.rpy:1']

    def test_save_and_load_roundtrip(self, registry, tmp_path):
        snap = AssetSnapshot(registry)
        path = tmp_path / 'sub' / 'snap.json'
        snap.save(path)
        back = AssetSnapshot.load(path)
        assert back.data == snap.data
        assert back.timestamp == snap.timestamp

    def test_snapshot_is_isolated_from_registry(self, registry):
        snap = AssetSnapshot(registry)
        registry.images['bg_room']['Notes'] = 'changed'
        assert snap.row('images', 'bg_room')['Notes'] == ''

    def test_all_ids_and_row(self, registry):
        snap = AssetSnapshot(registry)
        assert snap.all_ids('images') == {'bg_room', 'eileen'}
        assert snap.row('images', 'nope') is None

    def test_find_by_path(self, registry):
        snap = AssetSnapshot(registry)
        found = snap.find_by_path('images/bg/room.png')
        assert found == [('images', 'bg_room')]

    def test_json_safe_conversion(self):
        reg = AssetRegistry()
        reg.images = {'a': {'ID': 'a', 'Weird': {1, 2}}}
        snap = AssetSnapshot(reg)
        assert isinstance(snap.data['images']['a']['Weird'], str)


# ------------------------------------------------------------------
#  变更追踪
# ------------------------------------------------------------------

class TestChangeTracker:
    def _changes(self, old_reg, new_reg):
        return AssetChangeTracker.diff(AssetSnapshot(old_reg),
                                       AssetSnapshot(new_reg))

    def test_added(self, registry):
        import copy
        new = copy.deepcopy(registry)
        new.images['newbie'] = {'ID': 'newbie', 'Path': 'n.png'}
        changes = self._changes(registry, new)
        assert ('added', 'newbie') in [(c.change_type, c.asset_id)
                                       for c in changes]

    def test_removed(self, registry):
        import copy
        new = copy.deepcopy(registry)
        del new.images['bg_room']
        changes = self._changes(registry, new)
        assert ('removed', 'bg_room') in [(c.change_type, c.asset_id)
                                          for c in changes]

    def test_renamed(self, registry):
        import copy
        new = copy.deepcopy(registry)
        row = new.images.pop('bg_room')
        row = dict(row, ID='room_bg')
        new.images['room_bg'] = row
        changes = self._changes(registry, new)
        renamed = [c for c in changes if c.change_type == 'renamed']
        assert renamed and renamed[0].new_id == 'room_bg'

    def test_moved(self, registry):
        import copy
        new = copy.deepcopy(registry)
        new.images['bg_room'] = dict(new.images['bg_room'],
                                     Path='images/bg/v2/room.png')
        changes = self._changes(registry, new)
        assert ('moved', 'bg_room') in [(c.change_type, c.asset_id)
                                        for c in changes]

    def test_modified(self, registry):
        import copy
        new = copy.deepcopy(registry)
        new.images['bg_room'] = dict(new.images['bg_room'], Notes='edit')
        changes = self._changes(registry, new)
        change = [c for c in changes if c.change_type == 'modified'][0]
        assert 'Notes' in change.describe()

    def test_unchanged(self, registry):
        import copy
        assert self._changes(registry, copy.deepcopy(registry)) == []

    def test_describe_renamed(self):
        ch = AssetChange('renamed', 'images', 'old', {}, {}, new_id='new')
        assert ch.display_id == 'old -> new'
        assert '重命名' in ch.describe()

    def test_describe_moved(self):
        ch = AssetChange('moved', 'images', 'a',
                         {'Path': 'x.png'}, {'Path': 'y.png'})
        assert 'x.png' in ch.describe() and 'y.png' in ch.describe()

    def test_internal_fields_ignored_in_diff(self):
        ch = AssetChange('modified', 'images', 'a',
                         {'Notes': 'v', '_custom': {'a': 1}},
                         {'Notes': 'v', '_custom': {'a': 2}})
        assert ch.describe().strip() == '[修改] images/a'

    def test_format_report_empty(self):
        assert AssetChangeTracker.format_report([]) == "无变更。"

    def test_format_report_groups(self, registry):
        import copy
        new = copy.deepcopy(registry)
        del new.images['bg_room']
        changes = self._changes(registry, new)
        report = AssetChangeTracker.format_report(changes)
        assert '--- removed (1) ---' in report


# ------------------------------------------------------------------
#  依赖分析
# ------------------------------------------------------------------

class TestDependencyAnalyzer:
    def test_references_of(self, registry):
        da = DependencyAnalyzer(registry)
        assert da.references_of('bg_room') == ['script.rpy:1']
        assert da.references_of('nope') == []

    def test_find_broken(self, registry):
        da = DependencyAnalyzer(registry)
        assert [aid for aid, _ in da.find_broken()] == ['ghost']

    def test_find_unused(self, registry):
        da = DependencyAnalyzer(registry)
        unused = da.find_unused()
        assert ('images', 'eileen') in unused
        assert ('images', 'bg_room') not in unused

    def test_find_unused_respects_categories(self, registry):
        da = DependencyAnalyzer(registry)
        unused = da.find_unused(categories=['characters'])
        assert ('characters', 'e') in unused

    def test_reference_count(self, registry):
        da = DependencyAnalyzer(registry)
        assert da.reference_count('bg_room') == 1

    def test_summary(self, registry):
        da = DependencyAnalyzer(registry)
        s = da.summary()
        assert s['total_assets'] == 6
        assert s['broken_count'] == 1


# ------------------------------------------------------------------
#  重命名补丁
# ------------------------------------------------------------------

class TestRenamePatch:
    def test_replaces_with_word_boundary(self, write_rpy):
        fp = write_rpy('show hero\nshow hero_happy\n')
        result = generate_rename_patch('hero', 'protagonist', [fp],
                                       dry_run=False, backup=False)
        text = fp.read_text(encoding='utf-8')
        assert 'protagonist' in text
        assert 'hero_happy' in text
        assert result['modified']

    def test_no_match_returns_empty(self, write_rpy):
        fp = write_rpy('nothing here\n')
        result = generate_rename_patch('hero', 'x', [fp])
        assert result['matched_files'] == []
        assert result['modified'] == []

    def test_missing_file_skipped(self, tmp_path):
        result = generate_rename_patch('a', 'b', [tmp_path / 'nope.rpy'])
        assert result['modified'] == []

    def test_backup_created(self, write_rpy):
        fp = write_rpy('show hero\n')
        generate_rename_patch('hero', 'x', [fp], dry_run=False, backup=True)
        assert fp.with_suffix(fp.suffix + '.bak').exists()


# ------------------------------------------------------------------
#  补全
# ------------------------------------------------------------------

class TestCompletion:
    def test_prefix_match(self, registry):
        cp = AssetCompletionProvider(registry)
        results = cp.complete('bg')
        assert ('image', 'bg_room') in results

    def test_substring_match(self, registry):
        cp = AssetCompletionProvider(registry)
        results = cp.complete('room')
        assert ('image', 'bg_room') in results

    def test_fuzzy_match(self, registry):
        cp = AssetCompletionProvider(registry)
        results = cp.complete('ellein', fuzzy=True, min_score=0.3)
        assert results and results[0][1] == 'eileen'

    def test_fuzzy_disabled_excludes_substring(self, registry):
        cp = AssetCompletionProvider(registry)
        assert cp.complete('room', fuzzy=False) == []

    def test_type_filter(self, registry):
        cp = AssetCompletionProvider(registry)
        results = cp.complete('', asset_types=['music'])
        assert results == [('music', 'bgm_theme')]

    def test_limit_respected(self, registry):
        cp = AssetCompletionProvider(registry)
        assert len(cp.complete('', limit=2)) == 2

    def test_prefix_group_ranked_before_substring(self, registry):
        """前缀命中组整体排在子串命中组之前。"""
        registry.images['room_bg'] = {'ID': 'room_bg', 'Path': 'r.png',
                                      'Tag': 'bg', 'Category': 'background'}
        cp = AssetCompletionProvider(registry)
        candidates = [aid for _, aid in cp.complete('bg')]
        assert candidates.index('bg_room') < candidates.index('room_bg')

    def test_within_prefix_group_shorter_first(self, registry):
        cp = AssetCompletionProvider(registry)
        assert cp.complete('e')[0] == ('character', 'e')

    def test_get_all_and_types(self, registry):
        cp = AssetCompletionProvider(registry)
        assert cp.get_all('music') == ['bgm_theme']
        assert 'image' in cp.suggest_types()

    def test_custom_table_in_cache(self, registry):
        cp = AssetCompletionProvider(registry)
        assert cp.get_all('custom:props') == ['k1']

    def test_refresh_picks_up_changes(self, registry):
        cp = AssetCompletionProvider(registry)
        registry.images['brand_new'] = {'ID': 'brand_new'}
        assert cp.get_all('image') == ['bg_room', 'eileen']
        cp.refresh()
        assert 'brand_new' in cp.get_all('image')


# ------------------------------------------------------------------
#  面板模型
# ------------------------------------------------------------------

class TestPanelModel:
    def test_tree_grouped_by_category(self, registry):
        pm = AssetPanelModel(registry)
        tree = pm.get_tree(group_by='category')
        assert 'bg_room' in tree['background']['bg']
        assert 'eileen' in tree['character']['eileen']

    def test_tree_grouped_by_tag(self, registry):
        pm = AssetPanelModel(registry)
        tree = pm.get_tree(group_by='tag')
        assert 'bg_room' in tree['bg']['background']

    def test_tree_grouped_by_folder(self, registry):
        """
        folder 分组的 key 必须是 posix 风格，不能带 Windows 分隔符，
        否则同一份资产表在两个平台上的树会不一样。
        """
        pm = AssetPanelModel(registry)
        tree = pm.get_tree(group_by='folder')
        assert tree['images/bg']['background'] == ['bg_room']

    def test_audio_aggregated_into_tree(self, registry):
        pm = AssetPanelModel(registry)
        tree = pm.get_tree()
        assert tree['music']['_'] == ['bgm_theme']
        assert tree['sound']['_'] == ['sfx_click']

    def test_detail(self, registry):
        pm = AssetPanelModel(registry)
        detail = pm.get_detail('bg_room')
        assert detail['category'] == 'images'
        assert detail['references'] == ['script.rpy:1']

    def test_detail_custom(self, registry):
        pm = AssetPanelModel(registry)
        assert pm.get_detail('k1')['category'] == 'custom:props'

    def test_detail_unknown(self, registry):
        assert AssetPanelModel(registry).get_detail('nope') is None

    def test_search(self, registry):
        pm = AssetPanelModel(registry)
        results = pm.search('room')
        assert ('images', 'bg_room') in results

    def test_search_by_path(self, registry):
        pm = AssetPanelModel(registry)
        assert pm.search('images/bg')

    def test_search_empty_query(self, registry):
        assert AssetPanelModel(registry).search('') == []

    def test_search_limit(self, registry):
        pm = AssetPanelModel(registry)
        assert len(pm.search('_', limit=1)) == 1

    def test_statistics(self, registry):
        stats = AssetPanelModel(registry).get_statistics()
        assert stats['images'] == 2
        assert stats['custom_tables'] == 1
        assert stats['referenced'] == 1      # bg_room
        assert stats['broken_refs'] == 1     # ghost

    def test_category_breakdown(self, registry):
        bd = AssetPanelModel(registry).get_category_breakdown()
        assert bd == {'background': 1, 'character': 1}


# ------------------------------------------------------------------
#  工作区
# ------------------------------------------------------------------

class TestWorkspace:
    def test_load_requires_valid_dir(self, asset_env):
        ws = AssetWorkspace(asset_env['asset_dir'],
                            asset_env['project_root'])
        ws.load(script_files=[asset_env['game_dir'] / 'script.rpy'])
        assert ws.registry is not None
        assert ws.panel is not None
        assert ws.completion is not None

    def test_snapshot_and_diff(self, asset_env):
        ws = AssetWorkspace(asset_env['asset_dir'],
                            asset_env['project_root'])
        ws.load(script_files=[asset_env['game_dir'] / 'script.rpy'])
        ws.snapshot(tag='one')
        assert len(ws.list_snapshots()) == 1
        # 无变化时不应有 diff
        ws.snapshot(tag='two')
        assert ws.diff_latest_two() == []

    def test_snapshot_diff_detects_change(self, asset_env):
        ws = AssetWorkspace(asset_env['asset_dir'],
                            asset_env['project_root'])
        ws.load(script_files=[asset_env['game_dir'] / 'script.rpy'])
        ws.snapshot(tag='one')
        ws.registry.images['brand_new'] = {'ID': 'brand_new',
                                           'Path': 'n.png'}
        ws.snapshot(tag='two')
        changes = ws.diff_latest_two()
        assert any(c.change_type == 'added' for c in changes)

    def test_prune_snapshots(self, asset_env):
        ws = AssetWorkspace(asset_env['asset_dir'],
                            asset_env['project_root'])
        ws.load(script_files=[asset_env['game_dir'] / 'script.rpy'])
        for i in range(5):
            ws.snapshot(tag=f's{i}')
        ws.prune_snapshots(keep=2)
        assert len(ws.list_snapshots()) == 2

    def test_snapshot_before_load_raises(self, asset_env):
        ws = AssetWorkspace(asset_env['asset_dir'],
                            asset_env['project_root'])
        with pytest.raises(RuntimeError):
            ws.snapshot()

    def test_rename_through_workspace(self, asset_env):
        script = asset_env['game_dir'] / 'script.rpy'
        ws = AssetWorkspace(asset_env['asset_dir'],
                            asset_env['project_root'])
        ws.load(script_files=[script])
        result = ws.rename_asset_in_scripts('bgm_theme', 'theme_song',
                                            [script], dry_run=False)
        assert 'theme_song' in script.read_text(encoding='utf-8')
        assert result['modified']

    def test_preview_rename_does_not_write(self, asset_env):
        script = asset_env['game_dir'] / 'script.rpy'
        before = script.read_text(encoding='utf-8')
        ws = AssetWorkspace(asset_env['asset_dir'],
                            asset_env['project_root'])
        ws.load(script_files=[script])
        ws.preview_rename('bgm_theme', 'theme_song', [script])
        assert script.read_text(encoding='utf-8') == before

    def test_build_change_report(self, asset_env):
        ws = AssetWorkspace(asset_env['asset_dir'],
                            asset_env['project_root'])
        ws.load(script_files=[asset_env['game_dir'] / 'script.rpy'])
        report = ws.build_change_report([])
        assert isinstance(report, str)
