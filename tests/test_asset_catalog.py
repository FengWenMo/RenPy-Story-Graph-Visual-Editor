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
