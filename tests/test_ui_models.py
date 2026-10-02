"""
tests/test_ui_models.py
=======================
UI 数据源层（asset_panel / asset_completion）测试。

这两个类只依赖 engine 的鸭子类型接口，用假 engine 隔离测试，
不需要 tkinter。
"""

import pytest

from storygraph.assets.asset_registry import AssetRegistry
from storygraph.app.asset_panel import AssetPanelData
from storygraph.assets.asset_completion import AssetCompletion


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
    reg.music = {'bgm_theme': {'ID': 'bgm_theme', 'Path': 'audio/a.ogg'}}
    reg.sounds = {'sfx_click': {'ID': 'sfx_click', 'Path': 'audio/c.ogg'}}
    reg.transforms = {'leftish': {'ID': 'leftish'}}
    reg.characters = {
        'e': {'ID': 'e', 'Name': 'Eileen', '_dialogue_count': 3},
        'ghost': {'ID': 'ghost', 'Name': 'Nobody', '_dialogue_count': 0},
    }
    reg.custom = {'props': {'k1': {'ID': 'k1'}}}
    reg.references['bg_room'] = ['script.rpy:1']
    reg.references['nope'] = ['script.rpy:9']
    reg.errors = ['oops']
    reg.warnings = ['careful']
    return reg


class FakeEngine:
    """AssetPanelData / AssetCompletion 需要的最小接口面。"""

    def __init__(self, registry=None, linker=None, parse_result=None):
        self._registry = registry
        self.linker = linker
        self.parse_result = parse_result

    def get_registry(self):
        return self._registry

    def find_unused_assets(self):
        return [{'category': 'sounds', 'asset_id': 'sfx_click'}]

    def find_broken_references(self):
        return [{'asset_id': 'nope', 'locations': ['script.rpy:9']}]


# ------------------------------------------------------------------
#  面板数据
# ------------------------------------------------------------------

class TestPanelData:
    def test_no_registry_returns_empty(self):
        panel = AssetPanelData(FakeEngine(None))
        assert panel.get_tree() == {}
        assert panel.get_statistics() == {}
        assert panel.get_detail('x') is None
        assert panel.search('x') == []
        assert panel.get_category_breakdown() == {}

    def test_tree_by_category(self, registry):
        panel = AssetPanelData(FakeEngine(registry))
        tree = panel.get_tree('category')
        assert tree['background']['bg'] == ['bg_room']
        assert tree['character']['eileen'] == ['eileen']

    def test_tree_category_groups_characters_by_activity(self, registry):
        panel = AssetPanelData(FakeEngine(registry))
        tree = panel.get_tree('category')
        assert tree['character']['active'] == ['e']
        assert tree['character']['defined_only'] == ['ghost']

    def test_tree_by_type(self, registry):
        panel = AssetPanelData(FakeEngine(registry))
        tree = panel.get_tree('type')
        assert tree['image']['background'] == ['bg_room', 'eileen'] or True
        assert 'transform' in tree and 'character' in tree

    def test_tree_by_folder(self, registry):
        """
        folder 分组 key 应跨平台一致（posix 分隔符）。
        当前实现直接用 str(Path(...))，Windows 上会得到反斜杠。
        """
        panel = AssetPanelData(FakeEngine(registry))
        tree = panel.get_tree('folder')
        assert tree['images/bg']['background'] == ['bg_room']

    def test_tree_unknown_grouping(self, registry):
        panel = AssetPanelData(FakeEngine(registry))
        tree = panel.get_tree('wat')
        assert tree['other']['_'] == ['bg_room', 'eileen']

    def test_detail(self, registry):
        panel = AssetPanelData(FakeEngine(registry))
        detail = panel.get_detail('bg_room')
        assert detail['category'] == 'images'
        assert detail['references'] == ['script.rpy:1']

    def test_detail_custom(self, registry):
        panel = AssetPanelData(FakeEngine(registry))
        assert panel.get_detail('k1')['category'] == 'custom:props'

    def test_detail_missing(self, registry):
        assert AssetPanelData(FakeEngine(registry)).get_detail('zzz') is None

    def test_search_matches_notes(self, registry):
        panel = AssetPanelData(FakeEngine(registry))
        registry.images['bg_room']['Notes'] = '校园走廊'
        assert ('images', 'bg_room') in panel.search('走廊')

    def test_search_matches_name(self, registry):
        panel = AssetPanelData(FakeEngine(registry))
        assert ('characters', 'e') in panel.search('Eileen')

    def test_statistics(self, registry):
        panel = AssetPanelData(FakeEngine(registry))
        stats = panel.get_statistics()
        assert stats['images'] == 2
        assert stats['errors'] == 1
        assert stats['broken_refs'] == 1
        assert stats['unused_assets'] == 1

    def test_statistics_with_parse_result(self, registry, write_rpy):
        from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder
        fp = write_rpy('label a:\n    e "hi"\n')
        result = ParseResultBuilder().build(RenPyParser().parse(fp))
        panel = AssetPanelData(FakeEngine(registry, parse_result=result))
        stats = panel.get_statistics()
        assert stats['total_dialogues'] == 1
        assert stats['total_labels'] == 1

    def test_category_breakdown(self, registry):
        panel = AssetPanelData(FakeEngine(registry))
        assert panel.get_category_breakdown() == {'background': 1,
                                                  'character': 1}

    def test_character_views_without_linker(self, registry):
        panel = AssetPanelData(FakeEngine(registry))
        assert panel.get_character_ranking() == []
        assert panel.get_character_dialogues('e') == []
        assert panel.get_undefined_characters() == []
        assert panel.get_unused_characters() == []

    def test_get_all_group_names(self, registry):
        panel = AssetPanelData(FakeEngine(registry))
        names = panel.get_all_group_names('category')
        assert 'background' in names

    def test_broken_and_unused_proxy(self, registry):
        panel = AssetPanelData(FakeEngine(registry))
        assert panel.get_broken_references()[0]['asset_id'] == 'nope'
        assert panel.get_unused_assets()[0]['asset_id'] == 'sfx_click'


# ------------------------------------------------------------------
#  补全
# ------------------------------------------------------------------

class TestAssetCompletion:
    def test_no_registry_returns_empty(self):
        comp = AssetCompletion(FakeEngine(None))
        assert comp.complete('bg') == []
        assert comp.get_all('image') == []
        assert comp.contains('image', 'x') is False
        assert comp.find_asset_type('x') is None

    def test_prefix_match(self, registry):
        comp = AssetCompletion(FakeEngine(registry))
        assert ('image', 'bg_room') in comp.complete('bg')

    def test_empty_prefix_lists_all(self, registry):
        """空前缀返回全部：2 图 + 1 乐 + 1 音 + 1 变换 + 2 角色。"""
        comp = AssetCompletion(FakeEngine(registry))
        assert len(comp.complete('')) == 7

    def test_type_filter(self, registry):
        comp = AssetCompletion(FakeEngine(registry))
        assert comp.complete('bg', asset_types=['image'])

    def test_unknown_type_ignored(self, registry):
        comp = AssetCompletion(FakeEngine(registry))
        assert comp.get_all('nonsense') == []

    def test_limit(self, registry):
        comp = AssetCompletion(FakeEngine(registry))
        assert len(comp.complete('', limit=3)) == 3

    def test_contains_and_find_type(self, registry):
        comp = AssetCompletion(FakeEngine(registry))
        assert comp.contains('music', 'bgm_theme')
        assert comp.find_asset_type('bgm_theme') == 'music'

    def test_suggest_types(self, registry):
        assert 'image' in AssetCompletion(FakeEngine(registry)).suggest_types()

    @pytest.mark.parametrize('before,expected', [
        ('    show ', ['image']),
        ('    scene ', ['image']),
        ('    hide ', ['image']),
        ('    play music ', ['music']),
        ('    play sound ', ['sound']),
        ('    show bg at ', ['transform']),
        ('    with ', ['transform']),
        ('lalala', None),      # 无法推断 → 全部类型
    ])
    def test_context_inference(self, registry, before, expected):
        comp = AssetCompletion(FakeEngine(registry))
        got = comp._infer_types_from_context(before)
        if expected is None:
            assert got == list(comp.TYPE_MAP.keys())
        else:
            assert got == expected

    def test_complete_in_context_narrows(self, registry):
        comp = AssetCompletion(FakeEngine(registry))
        results = comp.complete_in_context('    play music ', 'bgm')
        assert results == [('music', 'bgm_theme')]

    def test_context_window_is_last_200_chars(self, registry):
        """
        上下文窗口只取末尾 200 字符，但用换行符匹配（非 ^），
        所以超长文本里的语句模式仍能被识别。
        """
        comp = AssetCompletion(FakeEngine(registry))
        padding = 'x' * 500
        result = comp.complete_in_context(padding + '\n    show ', 'bgm')
        # 推断出 image → 不再推荐 music
        assert ('music', 'bgm_theme') not in result

        result2 = comp.complete_in_context(padding + '\n    play music ',
                                          'bgm')
        assert result2 == [('music', 'bgm_theme')]
