# -*- coding: utf-8 -*-
"""
服务层单测：`app.asset_catalog.AssetCatalog`（2026-11-01，服务化第一刀）。

**这个文件里没有 `EditorSession`、没有 Qt、没有真的资源表文件** ——
那正是这一刀要证明的事：资源侧的只读判据只依赖 `registry` + `project_root`，
拿一个 `SimpleNamespace` 当 registry 就能测。

会话侧（`AssetRegistryMixin`）只剩转发，它自己的契约由
`tests/test_editor_session_split.py` 与行为测试守着；这里补一条"转发真的
接到了服务上"的用例（不然两边可以各自飘）。
"""
from types import SimpleNamespace

from storygraph.app.asset_catalog import ASSET_KINDS, AssetCatalog


def registry(**fields):
    """一个够用的假 registry：缺的字段一律空字典。"""
    base = {'characters': {}, 'images': {}, 'music': {}, 'sounds': {},
            'voices': {}, 'transforms': {}}
    base.update(fields)
    return SimpleNamespace(**base)


class TestCharacters:
    def test_ids_are_sorted(self):
        cat = AssetCatalog(registry(characters={'ling': {}, 'mei': {}, 'a': {}}))
        assert cat.characters() == ['a', 'ling', 'mei']

    def test_no_registry_answers_empty_instead_of_raising(self):
        cat = AssetCatalog(None)
        assert cat.characters() == []
        assert cat.character_labels() == {}
        assert cat.asset_choices('image') == []

    def test_broken_registry_is_swallowed(self):
        class Boom:
            @property
            def characters(self):
                raise RuntimeError('表读坏了')
        assert AssetCatalog(Boom()).characters() == []


class TestCharacterLabels:
    def test_table_name_is_used(self):
        cat = AssetCatalog(registry(characters={
            'ling': {'name': '凌'},
            'mei': {'name': '   '},          # 空白名不算
            'n': 'not-a-dict',               # 脏行也不该炸
        }))
        assert cat.character_labels() == {'ling': '凌'}

    def test_vocabulary_overrides_table_name(self):
        """
        词汇表盖表里的名字（与"三档来源，前面的盖后面的"同一条规矩）。

        这里换掉 `vocabulary()` 这一个缝 —— 它的实现要读项目映射文件，
        不是这条用例要验的东西；要验的是**合并顺序**。
        """
        class Term:
            def __init__(self, tid, shown):
                self.id, self._shown = tid, shown

            def display(self):
                return self._shown

        class Vocab:
            def of_kind(self, kind):
                assert kind == 'character'
                # 第二个是"名字与 ID 相同"的情形：display 不额外给信息
                return [Term('ling', '凌（映射）'), Term('same', 'same')]

        class Cat(AssetCatalog):
            def vocabulary(self):
                return Vocab()

        cat = Cat(registry(characters={'ling': {'name': '凌'}, 'mei': {'name': '梅'}}))
        labels = cat.character_labels()
        assert labels['ling'] == '凌（映射）'
        # 名字与 ID 相同 → 不占一格（调用方原样显示 tag）
        assert 'same' not in labels
        # 表里有、词汇表没提的角色不受影响
        assert labels['mei'] == '梅'


class TestAssetChoices:
    def test_image_rows_become_image_specs(self):
        cat = AssetCatalog(registry(images={
            'ling_happy': {'Tag': 'ling', 'Attributes': 'happy',
                           'Category': 'character'},
            'bg_lab': {'Tag': 'bg', 'Attributes': 'lab', 'Category': 'background'},
        }))
        values = [v for v, _desc in cat.asset_choices('image')]
        assert values == ['bg lab', 'ling happy']

    def test_audio_rows_use_the_asset_id(self):
        """音频写的是**资产 ID**（真实路径由渲染器翻），不是路径也不是文件名。"""
        cat = AssetCatalog(registry(music={'bgm_dawn': {'Path': 'audio/dawn.ogg'},
                                           'bgm_dusk': {}}))
        values = [v for v, _desc in cat.asset_choices('music')]
        assert values == ['bgm_dawn', 'bgm_dusk']

    def test_unknown_kind_and_empty_table(self):
        cat = AssetCatalog(registry())
        assert cat.asset_choices('nonsense') == []
        assert cat.asset_choices('') == []
        assert cat.asset_choices('image') == []

    def test_description_comes_from_describe_asset(self):
        cat = AssetCatalog(registry(images={
            'ling_happy': {'Tag': 'ling', 'Attributes': 'happy',
                           'Category': 'character'}}))
        (_value, desc), = cat.asset_choices('image')
        assert '立绘' in desc or 'ling' in desc


class TestOneSourceOfTruth:
    def test_kind_map_is_shared_with_the_session(self):
        """`ASSET_KINDS` 只该有一份 —— 会话侧那个属性必须**是同一个对象**。"""
        from storygraph.app.session_assets import AssetRegistryMixin
        assert AssetRegistryMixin.ASSET_KINDS is ASSET_KINDS

    def test_session_methods_delegate_to_the_service(self):
        """会话上的四个名字仍然是转发（不重新实现一遍判据）。"""
        from storygraph.app.editor import EditorSession
        reg = registry(characters={'ling': {'name': '凌'}},
                       music={'bgm_dawn': {}})
        session = EditorSession(registry_provider=lambda: reg)
        assert session.characters() == ['ling']
        assert session.character_labels() == {'ling': '凌'}
        assert [v for v, _d in session.asset_choices('music')] == ['bgm_dawn']
        assert session._image_spec_of('ling_happy', {
            'Tag': 'ling', 'Attributes': 'happy'}) == 'ling happy'


# ----------------------------------------------------------------------
#  第四刀：缩略图路径 / 资源概览（"读 + 缓存"那一半）
# ----------------------------------------------------------------------

class TestSource:
    def test_source_is_memoized(self):
        """`AssetPanelData` 一次概览要问它六七遍，别每次新建一个。"""
        cat = AssetCatalog(registry(), project_root='.')
        assert cat.source() is cat.source()

    def test_invalidate_brings_a_new_one(self):
        cat = AssetCatalog(registry(), project_root='.')
        first = cat.source()
        cat.invalidate()
        assert cat.source() is not first

    def test_an_empty_catalog_still_has_one(self):
        """没有 registry 时也给一个空源（界面降级 ≠ 崩）。"""
        assert AssetCatalog(None).source() is not None


class TestResolveIds:
    def test_the_index_is_built_once_and_reused(self):
        """
        索引要缓存：真实项目 3149 行、建一次 ~0.4 s，而 `image_path` 会被
        卡片**逐张**调用。判据 = 两次调用拿到**同一个** `AssetIndex` 对象。
        """
        cat = AssetCatalog(registry(images={
            'ling_happy': {'Tag': 'ling', 'Attributes': 'happy'}}))
        cat.resolve_ids('ling_happy')
        idx = cat._index
        assert idx is not None, '没建索引（或没存下来）'
        cat.resolve_ids('ling_happy')
        assert cat._index is idx, '索引被重建了 —— 缓存等于没有'

    def test_invalidate_drops_it(self):
        cat = AssetCatalog(registry(images={
            'ling_happy': {'Tag': 'ling', 'Attributes': 'happy'}}))
        cat.resolve_ids('ling_happy')
        cat.invalidate()
        assert cat._index is None

    def test_no_registry_answers_empty(self):
        assert AssetCatalog(None).resolve_ids('ling_happy') == []


class TestImagePath:
    def test_empty_spec_and_no_registry_answer_none(self):
        assert AssetCatalog(registry()).image_path('') is None
        assert AssetCatalog(registry()).image_path('   ') is None
        assert AssetCatalog(None).image_path('ling happy') is None

    def test_a_registered_row_resolves_to_the_file_on_disk(self, tmp_path):
        """表里那一行的 `Path` 就是缩略图的真源（不猜、不扫目录）。"""
        img = tmp_path / 'game' / 'images' / 'characters' / 'ling' / 'happy.png'
        img.parent.mkdir(parents=True)
        img.write_bytes(b'\x89PNG\r\n\x1a\n')
        reg = registry(images={'ling_happy': {
            'ID': 'ling_happy', 'Tag': 'ling', 'Attributes': 'happy',
            'Path': 'game/images/characters/ling/happy.png'}})
        cat = AssetCatalog(reg, project_root=str(tmp_path))
        hit = cat.image_path('ling happy')
        assert hit is not None and str(hit).endswith('happy.png')

    def test_a_missing_file_answers_none_instead_of_a_guess(self, tmp_path):
        reg = registry(images={'ling_happy': {
            'ID': 'ling_happy', 'Tag': 'ling', 'Attributes': 'happy',
            'Path': 'game/images/characters/ling/happy.png'}})
        cat = AssetCatalog(reg, project_root=str(tmp_path))
        assert cat.image_path('ling happy') is None

    def test_a_project_without_layeredimage_takes_the_old_path(self, tmp_path):
        """没有分层声明 → `composed_sprite_path` 诚实返回 None（老项目不变）。"""
        cat = AssetCatalog(registry(), project_root=str(tmp_path))
        assert cat.composed_sprite_path('hero glad plain') is None


class TestOverview:
    def test_no_registry_says_so(self):
        res = AssetCatalog(None).overview()
        assert res['ok'] is False and '还没有资源表' in res['error']
        assert res['statistics'] == {} and res['broken'] == []

    def test_it_reports_the_panel_sections(self, tmp_path):
        """六个查询 + 排名 + 维度置信度，全都是"拿表就能答"的。"""
        reg = registry(images={'ling_happy': {
            'ID': 'ling_happy', 'Tag': 'ling', 'Attributes': 'happy',
            'Category': 'character'}})
        res = AssetCatalog(reg, project_root=str(tmp_path)).overview(top=3)
        assert res['ok'] is True
        for key in ('statistics', 'categories', 'top_characters', 'undefined',
                    'unused_characters', 'unused_assets', 'broken'):
            assert key in res, key
        assert len(res['top_characters']) <= 3
        assert 'groups' in res['dimension_confidence']

    def test_a_broken_query_does_not_take_the_whole_overview_down(self, tmp_path):
        """一个查询炸了，其余照出 —— 只是把那条记进 `warnings`。"""
        reg = registry()
        cat = AssetCatalog(reg, project_root=str(tmp_path))
        cat.source().get_statistics = _boom      # 只弄坏第一个查询
        res = cat.overview()
        assert res['ok'] is True
        assert res['statistics'] == {}
        assert any('statistics' in w for w in res['warnings'])


def _boom(*_a, **_k):
    raise RuntimeError('这个查询坏了')
