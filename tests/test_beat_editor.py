"""
tests/test_beat_editor.py
=========================
M5（应用内资产选择 + Beat 编辑面板）与 M6（新建资产回写）的纯逻辑测试。

tkinter 在无头环境下是桩，无法验证真实交互；这里只测抽出来的纯函数：
  · search_assets          搜索（复用 AssetCompletion）
  · beat_to_fields/fields_to_beat   Beat ↔ 面板字段映射
  · add_asset_to_registry  新建资产写回注册表 + 资源表（M6）
"""

import pytest

from asset_registry import AssetRegistry, add_asset_to_registry
from beat_editor import search_assets, beat_to_fields, fields_to_beat
from script_table import Beat
from asset_completion import AssetCompletion


@pytest.fixture
def registry():
    reg = AssetRegistry()
    reg.images = {'bg_classroom': {'ID': 'bg_classroom', 'Path': 'bg/classroom.png'}}
    reg.music = {'bgm_theme': {'ID': 'bgm_theme'}}
    reg.voices = {'v_eileen_01': {'ID': 'v_eileen_01'}}
    reg.characters = {'e': {'ID': 'e'}}
    return reg


@pytest.fixture
def engine(registry):
    """最小可用的 engine 桩，只提供 get_registry()。"""

    class _Engine:
        def get_registry(self):
            return registry

    return _Engine()


class TestSearchAssets:
    def test_prefix_match(self, engine):
        hits = search_assets(engine, 'bgm', ['music'])
        assert ('music', 'bgm_theme') in hits

    def test_type_filter(self, engine):
        hits = search_assets(engine, '', ['voice'])
        assert all(t == 'voice' for t, _ in hits)
        assert ('voice', 'v_eileen_01') in hits

    def test_no_match_returns_empty(self, engine):
        assert search_assets(engine, 'zzz_none', ['image']) == []


class TestBeatFieldsRoundTrip:
    def test_roundtrip(self):
        b = Beat(seq=3, label='ch01', kind='say', speaker='e',
                 text='你好', scene='bg classroom',
                 show='eileen happy@left', music='bgm_theme',
                 music_action='queue', voice='v_eileen_01')
        fields = beat_to_fields(b)
        back = fields_to_beat(fields, base=b)
        assert back.speaker == 'e'
        assert back.text == '你好'
        assert back.show == 'eileen happy@left'
        assert back.music_action == 'queue'
        assert back.voice == 'v_eileen_01'
        assert back.seq == 3          # base 元信息保留

    def test_missing_kind_defaults_to_say(self):
        b = fields_to_beat({'Text': 'x', 'Kind': ''})
        assert b.kind == 'say'


class TestAddAssetToRegistry:
    """M6：新建资产直接写回资源表。"""

    def test_adds_to_registry(self, registry):
        ok = add_asset_to_registry(registry, 'voice', 'v_boss_02', 'voice/boss.ogg')
        assert ok
        assert 'v_boss_02' in registry.voices
        assert registry.voices['v_boss_02']['Path'] == 'voice/boss.ogg'

    def test_writes_resource_table(self, registry, tmp_path):
        asset_dir = tmp_path / 'tables'
        asset_dir.mkdir()
        # 先放一个已有的 voices 表
        from asset_registry import write_asset_excel
        write_asset_excel(
            {'images': [], 'audio': {'music': [], 'sound': []},
             'voices': [{'ID': 'v_eileen_01', 'Path': 'voice/e.ogg',
                        'Notes': ''}]},
            asset_dir)
        ok = add_asset_to_registry(registry, 'voice', 'v_boss_02',
                                  'voice/boss.ogg', asset_dir=asset_dir)
        assert ok
        from asset_registry import read_excel_rows
        rows = read_excel_rows(asset_dir / 'voices.xlsx')
        ids = {r['ID'] for r in rows}
        assert ids == {'v_eileen_01', 'v_boss_02'}

    def test_invalid_type_returns_false(self, registry):
        assert not add_asset_to_registry(registry, 'nope', 'x')

    def test_empty_id_returns_false(self, registry):
        assert not add_asset_to_registry(registry, 'voice', '')
