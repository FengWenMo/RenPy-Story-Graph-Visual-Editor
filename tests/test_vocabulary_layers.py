"""
tests/test_vocabulary_layers.py
===============================
**P0-2 词汇表显式层** + **P0-3 覆盖留痕**（2026-10-31）。

用户当时指的是这两条（截图里打 ❌ 的）：

  · P0-2 `Vocabulary.plus()` 靠**调用顺序**决定胜负 —— 写反了"映射优先"
    就静默反过来，没有任何地方会红；
  · P0-3 "谁覆盖了谁"说不出来 —— `Term.source` 只回答赢家是谁，
    回答不了"原来是什么、被哪一层盖掉的"。

这里的判据只有三条：

  ① **打乱 layers 的先后，结果逐字段相同**（顺序无关 = 层号真的生效）；
  ② 每盖掉一个**值不同**的旧词条，就多一条 `Override`（值相同不算覆盖）；
  ③ `explain()` 说出一句人话，且和判据用的是同一份记录。
"""

from __future__ import annotations

import pytest

from storygraph.assets.vocabulary import (KIND_CUE, KIND_TRANSFORM,
                                          VOCAB_LAYERS, Term, Vocabulary,
                                          from_mapping)
from storygraph.core.authority import Override, rank


def _cue(ident: str, label: str, layer: str = '') -> Term:
    return Term(KIND_CUE, ident, label=label, source=layer)


def _tr(ident: str, label: str, layer: str = '') -> Term:
    return Term(KIND_TRANSFORM, ident, label=label, source=layer)


class TestMergeIgnoresCallOrder:
    def test_mapping_beats_calls_either_way_round(self):
        calls = [_cue('my_wipe', '调用层')]
        mapping = [_cue('my_wipe', '映射层')]
        one = Vocabulary.merged([('calls', calls), ('mapping', mapping)])
        two = Vocabulary.merged([('mapping', mapping), ('calls', calls)])
        assert one.find(KIND_CUE, 'my_wipe').label == '映射层'
        assert two.find(KIND_CUE, 'my_wipe').label == '映射层'
        assert one.as_terms() == two.as_terms()
        assert one.overrides == two.overrides

    def test_three_layers_shuffled_give_the_same_table(self):
        builtin = [_tr('left', '左边（内置）')]
        learned = [_tr('i11', '1人，近处（学来的）')]
        mapping = [_tr('left', '左侧'), _tr('i11', '1人，近处')]
        layers = [('builtin', builtin), ('learned', learned),
                  ('mapping', mapping)]
        import itertools
        results = []
        for order in itertools.permutations(layers):
            vocab = Vocabulary.merged(list(order))
            results.append(([(t.kind, t.id, t.label, t.source)
                             for t in vocab.terms], vocab.overrides))
        assert all(r == results[0] for r in results)
        # 映射（mapping-exact）压过内置（engine）？不 —— engine 是天花板。
        assert results[0][0][0][2] == '左边（内置）'

    def test_layers_fill_gaps_without_overwriting(self):
        vocab = Vocabulary.merged([
            ('calls', [_cue('my_wipe', '擦除')]),
            ('mapping', [_cue('Fade(0.2, 0.0, 0.2)', '闪白')]),
        ])
        assert vocab.label(KIND_CUE, 'my_wipe') == '擦除'       # 只有 calls 有
        assert vocab.label(KIND_CUE, 'Fade(0.2, 0.0, 0.2)') == '闪白'

    def test_unknown_layer_is_rejected_loudly(self):
        with pytest.raises(ValueError):
            Vocabulary.merged([('乱写的层', [_cue('x', 'y')])])

    def test_same_layer_later_wins_but_is_not_an_override(self):
        vocab = Vocabulary.merged([
            ('mapping', [_cue('my_wipe', '旧名'), _cue('my_wipe', '新名')]),
        ])
        assert vocab.label(KIND_CUE, 'my_wipe') == '新名'
        assert vocab.overrides == ()


class TestOverrideRecords:
    def test_a_changed_value_is_recorded(self):
        vocab = Vocabulary.merged([
            ('calls', [_cue('my_wipe', '擦除')]),
            ('mapping', [_cue('my_wipe', '闪白')]),
        ])
        assert len(vocab.overrides) == 1
        got = vocab.overrides[0]
        assert isinstance(got, Override)
        # 记的是**权威层名**（不是词汇表的层名），这样一句话能直接读成中文
        assert got.winner_layer == 'mapping-exact'
        assert got.loser_layer == 'declared'
        assert got.winner_value == '闪白' and got.loser_value == '擦除'
        assert got.domain == 'naming'
        assert got.match == (KIND_CUE, 'my_wipe', '')

    def test_the_same_value_is_not_an_override(self):
        vocab = Vocabulary.merged([
            ('calls', [_cue('my_wipe', '擦除')]),
            ('mapping', [_cue('my_wipe', '擦除')]),
        ])
        assert vocab.overrides == ()

    def test_override_sentence_is_one_line_of_human_text(self):
        vocab = Vocabulary.merged([
            ('calls', [_cue('my_wipe', '擦除')]),
            ('mapping', [_cue('my_wipe', '闪白')]),
        ])
        text = vocab.overrides[0].sentence()
        assert 'my_wipe' in text
        assert '映射' in text and '项目声明' in text
        assert '「闪白」' in text and '「擦除」' in text
        assert '\n' not in text

    def test_explain_returns_the_same_sentence(self):
        vocab = Vocabulary.merged([
            ('calls', [_cue('my_wipe', '擦除')]),
            ('mapping', [_cue('my_wipe', '闪白')]),
        ])
        assert vocab.explain(KIND_CUE, 'my_wipe') == \
            vocab.overrides[0].sentence()
        assert vocab.overrides_of(KIND_CUE, 'my_wipe') == list(vocab.overrides)

    def test_explain_is_empty_when_nothing_was_overridden(self):
        vocab = Vocabulary([_cue('my_wipe', '擦除')])
        assert vocab.explain(KIND_CUE, 'my_wipe') == ''
        assert vocab.overrides_of(KIND_CUE, 'my_wipe') == []

    def test_overrides_are_append_only_and_keep_order(self):
        vocab = Vocabulary.merged([
            ('calls', [_cue('a', '甲'), _cue('b', '乙')]),
            ('mapping', [_cue('a', 'A'), _cue('b', 'B')]),
        ])
        assert [o.key for o in vocab.overrides] == ['cue a', 'cue b']


class TestTermsCarryTheirLayer:
    def test_from_mapping_tags_every_term(self):
        vocab = from_mapping({'vocabulary': [
            {'kind': 'cue', 'id': 'my_wipe', 'label': '擦除'}]})
        term = vocab.find(KIND_CUE, 'my_wipe')
        assert term.source == 'mapping'
        assert term.label == '擦除'

    def test_merged_sets_the_layer_it_came_from(self):
        vocab = Vocabulary.merged([('calls', [_cue('my_wipe', '擦除')])])
        assert vocab.find(KIND_CUE, 'my_wipe').source == 'calls'

    def test_every_declared_layer_maps_to_a_real_authority_layer(self):
        for name, layer in VOCAB_LAYERS.items():
            assert rank(layer) <= rank('inferred'), f'{name} → {layer} 不在表里'
