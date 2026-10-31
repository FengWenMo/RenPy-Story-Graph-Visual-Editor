"""
tests/test_override_trail.py
============================
**P0-3 覆盖留痕**：每一条"谁赢了"都要能说出"原来是什么、被谁盖的"。

用户 2026-10-31 指出的缺口：全项目搜不到覆盖记录；资产选择器里那句
"凭什么这么判"用的是 `Identity.source`（只回答**赢家是谁**），不是覆盖历史。

设计文档 §六 6.4 引用的话是这件事的判据：

> "Precedence answers which value wins. Governance also needs to answer
>  where that value came from."

这里钉三条链路：

  ① 身份域：`images` 表的精确覆盖改掉了 `layeredimage` 声明 → 记一条；
  ② 命名域：词汇表 `merged()` 里映射压过 `calls.label` → 记一条（见
     `test_vocabulary_layers.py`，这里只验"界面拿到了"）；
  ③ 两条界面出口都把它显示出来：资产选择器 tooltip、下拉候选项 tooltip。
"""

from __future__ import annotations

import pytest

from storygraph.assets import asset_identity as AI
from storygraph.assets.vocabulary import KIND_CUE, Term, Vocabulary

ROW = {
    'ID': 'ling_happy',
    'Path': 'game/images/characters/ling/happy.png',
    'Tag': 'ling',
    'Attributes': 'happy',
    # 项目里 `layeredimage ling` 声明过（判定走 `_is_declared_image` 的第二条）
    '_layered': True,
}


def _resolve(mapping):
    return AI.resolve_image(dict(ROW), mapping=mapping)


class TestIdentityTrail:
    def test_exact_mapping_records_what_it_overrode(self):
        got = _resolve({'images': {'ling_happy': {'kind': 'preview',
                                                  'char': 'ling',
                                                  'attr': 'happy'}}})
        assert got.source == 'mapping'
        assert len(got.overrides) == 1
        record = got.overrides[0]
        assert record.domain == 'identity'
        assert record.winner_layer == 'mapping-exact'
        assert record.loser_layer == 'declared'
        assert record.winner_value and record.loser_value
        assert record.winner_value != record.loser_value
        assert record.match == ('ling_happy',)

    def test_the_sentence_names_both_sides(self):
        got = _resolve({'images': {'ling_happy': {'kind': 'preview',
                                                  'char': 'ling',
                                                  'attr': 'happy'}}})
        text = got.overrides[0].sentence()
        assert '映射' in text and '项目声明' in text
        assert '覆盖了' in text

    def test_nothing_is_recorded_when_the_two_layers_agree(self):
        # 两层说同一样东西（kind/tag/attrs 都相同）→ 不是覆盖，不记。
        got = _resolve({'images': {'ling_happy': {'kind': 'other',
                                                  'char': 'ling',
                                                  'attr': 'happy'}}})
        assert got.overrides == ()

    def test_undeclared_rows_have_no_trail(self):
        row = dict(ROW)
        row.pop('_layered')
        got = AI.resolve_image(row, mapping={
            'images': {'ling_happy': {'kind': 'preview', 'char': 'ling'}}})
        assert got.source == 'mapping'
        assert got.overrides == ()

    def test_plain_resolution_has_no_trail(self):
        got = AI.resolve_image(dict(ROW))
        assert got.overrides == ()


class TestPickerShowsTheTrail:
    def test_tooltip_lists_the_override(self):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.asset_picker import identity_tooltip

        got = _resolve({'images': {'ling_happy': {'kind': 'preview',
                                                  'char': 'ling',
                                                  'attr': 'happy'}}})
        text = identity_tooltip(got, 'ling_happy', ROW)
        assert '覆盖了' in text
        assert '映射' in text

    def test_tooltip_without_trail_has_no_such_line(self):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.asset_picker import identity_tooltip

        text = identity_tooltip(AI.Identity(kind='sprite', tag='ling',
                                            attrs=('happy',),
                                            source='declared', showable=True),
                                'ling_happy', ROW)
        assert '覆盖了' not in text


class TestChoiceTipsCarryTheTrail:
    def test_labeled_fills_the_tip_from_the_override(self):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.labeled_choices import labeled

        vocab = Vocabulary.merged([
            ('calls', [Term(KIND_CUE, 'my_wipe', label='擦除')]),
            ('mapping', [Term(KIND_CUE, 'my_wipe', label='闪白')]),
        ])
        choices = labeled([('my_wipe', '')], vocab, 'cue')
        assert choices[0].tip
        assert '覆盖了' in choices[0].tip
        assert '闪白' in choices[0].tip

    def test_labeled_keeps_the_callers_own_tip(self):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.labeled_choices import Choice, labeled

        vocab = Vocabulary.merged([
            ('calls', [Term(KIND_CUE, 'my_wipe', label='擦除')]),
            ('mapping', [Term(KIND_CUE, 'my_wipe', label='闪白')]),
        ])
        choices = labeled([Choice('my_wipe', tip='调用方自己的说明')],
                          vocab, 'cue')
        assert choices[0].tip == '调用方自己的说明'

    def test_no_override_means_no_tip(self):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.labeled_choices import labeled

        vocab = Vocabulary([Term(KIND_CUE, 'my_wipe', label='擦除')])
        choices = labeled([('my_wipe', '')], vocab, 'cue')
        assert choices[0].tip == ''
