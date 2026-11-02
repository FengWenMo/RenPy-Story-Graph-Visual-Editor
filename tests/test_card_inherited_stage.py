# -*- coding: utf-8 -*-
"""
tests/test_card_inherited_stage.py
==================================
**「当前舞台」的持续状态填进卡片表单**（用户 2026-11-02）。

用户原话：

> 我知道当前舞台界面确实有一大堆的 BGM 和背景和立绘……但是当我随便点入一个
> 卡片编辑器内的时候……我会很疑惑：为什么当前舞台是有的，但是当前卡片却没有
> 显示现在的 BGM 等东西呢……？所以我建议：把当前舞台的持续状态填充到前端
> （后端自己知道这一列跟前面一样，不做任何改变就行，只有确实已经改了，才再做
> 真正的程序导出）这样才合理吧……？不然太空了，每次都还要自己重新导入
> （比方说我换个人物表情，还需要自己先重新点击导入立绘）

这一片钉住两件事，**互为一对**：

  ① **看得见**：这一拍没写背景/音乐/立绘时，卡片表单里填的是"台上沿用的值"
     （背景框 + 「沿用上一拍」标记 + 那几张立绘卡），并且能直接改表情；
  ② **不改就不写**：没碰过的沿用值 `collect_fields()` 会还原成空串，
     `sprite_cards()` 会滤掉 `inherited` 的卡 —— 保存时一个字节都不多写。

判据分三层：会话（`stage_state` / `cards`）→ 表单（`BeatCardEditor`）
→ 写回（`FieldWriter`）。
"""

from __future__ import annotations

import pytest

from storygraph.story.script_table import Beat

#: 一句话：第一拍把背景/音乐/人摆上，第二拍什么都没写。
_SRC = ('label ch:\n'
        '    scene black\n'
        '    play music "bgm/夜晚.mp3"\n'
        '    show ye care at i11\n'
        '    "一"\n'
        '    "二"\n'
        '    return\n')


@pytest.fixture()
def session(write_rpy):
    from storygraph.app.editor import EditorSession
    s = EditorSession()
    s.load(write_rpy(_SRC, 'ch.rpy'))
    return s


# ==================================================================
#  ① 会话层：这一拍"屏幕上生效"的是什么
# ==================================================================

class TestStageState:
    def test_scene_music_actors_from_the_snapshot(self, session):
        got = session.stage_state(1)
        assert got['Scene'] == 'black', got
        assert got['BGM'] == 'bgm/夜晚.mp3', got
        assert [a['image'] for a in got['actors']] == ['ye'], got
        assert got['actors'][0]['expression'] == 'care'
        assert got['actors'][0]['at'] == 'i11'

    def test_second_beat_writes_nothing_of_its_own(self, session):
        """第二拍自己的字段是空的 —— 这正是"卡片看起来太空"的来源。"""
        assert session.fields(1).get('Scene', '') == ''
        assert session.fields(1).get('BGM', '') == ''

    def test_cards_include_the_actor_as_inherited(self, session):
        cards = session.cards(1)
        assert len(cards) == 1, cards
        card = cards[0]
        assert card['tag'] == 'ye'
        assert card['image'] == 'ye care'
        assert card['inherited'] is True
        assert card['inherited_at'] == 'i11'

    def test_cards_of_the_authoring_beat_are_not_inherited(self, session):
        cards = session.cards(0)
        assert len(cards) == 1
        assert not cards[0].get('inherited')


# ==================================================================
#  ② 表单层：填进去看得见，但"没改就不写"
# ==================================================================

@pytest.fixture()
def panel(qapp, session):
    from storygraph.ui_qt.card_editor import BeatCardEditor
    p = BeatCardEditor()
    p.load_fields(session.fields(1), session.cards(1),
                  inherited=session.stage_state(1))
    yield p
    p.deleteLater()


class TestTheFormShowsTheInheritedStage:
    def test_scene_and_music_are_filled(self, panel):
        assert panel._get_value('Scene') == 'black'
        assert panel._get_value('BGM') == 'bgm/夜晚.mp3'

    def test_the_caption_says_it_is_inherited(self, panel):
        assert '沿用' in panel._asset_caps['Scene'].text()
        assert '沿用' in panel._asset_caps['BGM'].text()
        assert '沿用' not in panel._asset_caps['Voice'].text(), \
            '语音是逐句的，不该沿用'

    def test_untouched_values_are_not_written_back(self, panel):
        got = panel.collect_fields()
        assert got.get('Scene', '') == ''
        assert got.get('BGM', '') == ''

    def test_changing_the_scene_makes_it_this_beats_own(self, panel):
        panel._widgets['Scene'].setEditText('bg room')
        assert panel.collect_fields().get('Scene') == 'bg room'

    def test_reloading_another_beat_drops_the_old_inheritance(self, panel,
                                                              session):
        """翻到这一拍自己写了背景的那一张 → 不该还挂着「沿用」。"""
        panel.load_fields(session.fields(0), session.cards(0),
                          inherited=session.stage_state(0))
        assert panel._get_value('Scene') == 'black'
        assert '沿用' not in panel._asset_caps['Scene'].text()
        # 这一拍自己写了 → 照常写回
        assert panel.collect_fields().get('Scene') == 'black'


class TestTheGridShowsTheInheritedSprites:
    def test_the_card_is_there_and_marked(self, panel):
        cards = panel._grid.cards()
        assert len(cards) == 1
        assert cards[0]['inherited'] is True
        label = panel._grid._cards[0]._name.text()
        assert '沿用' in label, label

    def test_untouched_inherited_card_is_not_written_back(self, panel):
        assert panel.sprite_cards() == []

    def test_editing_the_expression_makes_it_local(self, panel):
        """用户那句"换个人物表情还要先重新导入立绘"—— 现在点一下就改。"""
        card = panel._grid._cards[0]
        card._set_field('expression', 'happy')
        assert not card.item.get('inherited')
        got = panel.sprite_cards()
        assert len(got) == 1
        assert got[0]['image'].startswith('ye ')
        assert 'happy' in got[0]['image'], got

    def test_editing_the_position_also_makes_it_local(self, panel):
        card = panel._grid._cards[0]
        card._set_field('position', 'f11')
        assert not card.item.get('inherited')
        assert panel.sprite_cards()


# ==================================================================
#  ③ 写回层：就算调用方把"沿用卡"塞进来，也不许物化
# ==================================================================

class TestSavingDoesNotMaterializeTheInheritedStage:
    def test_save_with_inherited_cards_keeps_show_empty(self, session):
        """`save(idx, ..., cards=session.cards(idx))` 是容易踩的那条路。"""
        before = str(getattr(session.beats[1], 'show', '') or '')
        session.save(1, session.fields(1), cards=session.cards(1))
        assert str(getattr(session.beats[1], 'show', '') or '') == before == ''

    def test_save_with_an_edited_card_writes_it(self, session):
        cards = session.cards(1)
        cards[0]['inherited'] = False          # 用户改过了
        cards[0]['expression'] = 'happy'
        cards[0]['image'] = 'ye happy'
        session.save(1, session.fields(1), cards=cards)
        assert 'ye happy' in str(getattr(session.beats[1], 'show', '') or '')

    def test_inherited_flag_survives_a_round_trip_of_beats(self, session):
        """没有背景/立绘的那一拍，存完仍然"什么都没写"（表里也是空的）。"""
        session.save(1, session.fields(1), cards=session.cards(1))
        beat = session.beats[1]
        assert str(getattr(beat, 'show', '') or '') == ''
        assert str(getattr(beat, 'scene', '') or '') == ''
        assert isinstance(beat, Beat)
