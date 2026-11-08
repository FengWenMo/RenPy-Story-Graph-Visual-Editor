# -*- coding: utf-8 -*-
"""
「同一个角色只该退场一次」（2026-11-08 第十九轮）。

用户原话 + 截图（`演出剧本/chapter2_2_2.rpy` 第 268 / 270 行两条 `hide ling`）：

> hide 了两遍！（为什么我能做到……？因为我一开始先做了一次，是在后面一拍
> 操作的，然后又在前头再点了一次 hide……两个就重复了！！！bug，改！）

根因：点 ✕ 只改**当前那一拍**的 `beat.hide`（`_apply_hide_cards` 的"只加不减"），
两次操作各改各的拍、谁也不看对方 —— 同一章里就躺着两条 `hide ling`。

现在的判据（`app.table_edits.hide_flow`，纯函数）：看**中间有没有再 `show` 上来**。

  · 前面已经把他赶下去了 → 这一拍不再写（说一句为什么）；
  · 后面还留着一条多余的 → 摘掉（说一句"并掉了哪一条"）。

这一组钉住 A~G：两种操作顺序、重新上台、别的角色、`@转场` 条目、
会话级（`session.save` 的 `notes`）、以及真卡片控件点两下 ✕。
"""
from __future__ import annotations

import pytest

from storygraph.app.table_edits import FieldWriter, drop_hide_tag, hide_flow

_SRC = ('label c1:\n'
        '    show ling happy\n'
        '    lyj "一"\n'
        '    lyj "二"\n'
        '    "三"\n'
        '    "四"\n'
        '    "完"\n'
        '    return\n')


def _hide(tag='ling'):
    return {'image': tag, 'hide': True, 'tag': tag}


def _write(beat):
    """造一个最小项目，返回 `(session, 拍列表)`。"""
    return beat


@pytest.fixture()
def beats(tmp_path):
    from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
    from storygraph.story.beat_build import beats_from_parse_result

    ch = tmp_path / 'game' / 'c1.rpy'
    ch.parent.mkdir(parents=True)
    ch.write_text(_SRC, encoding='utf-8')
    pr = ParseResultBuilder().build(RenPyParser().parse(str(ch)))
    return list(beats_from_parse_result(pr))


def _apply(beats, idx, cards, notes=None):
    FieldWriter(None, beats).write(beats[idx], {'Text': beats[idx].text},
                                   cards=cards, notes=notes)


class TestTwoClicksBecomeOneHide:
    def test_hiding_the_later_beat_first_then_the_earlier_one(self, beats):
        """用户的顺序：先在后一拍点 ✕，再跑到前面一拍点 ✕。"""
        notes = []
        _apply(beats, 3, [_hide()], notes)
        assert beats[3].hide == 'ling'
        _apply(beats, 2, [_hide()], notes)
        assert beats[2].hide == 'ling'
        assert beats[3].hide == '', '后面那条多余的退场要一并去掉'
        assert any('第 4 拍' in n and '多余' in n for n in notes), notes

    def test_hiding_the_earlier_beat_first_then_the_later_one(self, beats):
        """反方向：已经退场了，后面再点一次 = 不该再写一行。"""
        notes = []
        _apply(beats, 2, [_hide()], notes)
        assert beats[2].hide == 'ling'
        _apply(beats, 3, [_hide()], notes)
        assert beats[2].hide == 'ling'
        assert beats[3].hide == ''
        assert any('第 3 拍' in n and '没有再写' in n for n in notes), notes

    def test_a_reappearance_makes_the_second_hide_legitimate(self, beats):
        """中间 `show` 回来过 → 后面那次退场是正经的，一个字都不许动。"""
        beats[3].show = 'ling happy'
        _apply(beats, 2, [_hide()])
        notes = []
        _apply(beats, 4, [_hide()], notes)
        assert beats[2].hide == 'ling'
        assert beats[4].hide == 'ling'
        assert not notes

    def test_other_characters_are_untouched(self, beats):
        beats[3].hide = 'ye'
        notes = []
        _apply(beats, 2, [_hide()], notes)
        assert beats[2].hide == 'ling'
        assert beats[3].hide == 'ye', '别的角色的退场跟这次操作无关'
        assert not notes

    def test_a_transition_variant_of_the_same_tag_is_the_same_person(self, beats):
        """`hide ling@dissolve` 也是"ling 下台" —— 不许再写一条裸 `hide ling`。"""
        beats[3].hide = 'ling@dissolve'
        notes = []
        _apply(beats, 2, [_hide()], notes)
        assert beats[2].hide == 'ling'
        assert beats[3].hide == '', beats[3].hide

    def test_only_the_matching_tag_is_dropped(self, beats):
        """摘的时候只摘同一个 tag，块里其它条目原样留着。"""
        beats[3].hide = 'ye; ling@dissolve'
        _apply(beats, 2, [_hide()])
        assert beats[3].hide == 'ye', beats[3].hide


class TestThePureHelpers:
    def test_hide_flow_reads_backwards_and_forwards(self, beats):
        beats[1].hide = 'ling'
        beats[3].hide = 'ling'
        assert hide_flow(beats, 2, 'ling') == (1, [3])
        assert hide_flow(beats, 0, 'ling') == (None, [1, 3])

    def test_drop_hide_tag_clears_the_field_when_nothing_is_left(self, beats):
        beats[2].hide = 'ling'
        assert drop_hide_tag(beats[2], 'ling') == [{'image': 'ling', 'with': ''}]
        assert beats[2].hide == ''


class TestThroughTheSession:
    def test_save_reports_what_it_merged(self, tmp_path):
        """`session.save` 要把"替你并掉了"放进 `notes`（界面靠它说一句）。"""
        from storygraph.app.editor import EditorSession

        ch = tmp_path / 'game' / 'c1.rpy'
        ch.parent.mkdir(parents=True)
        ch.write_text(_SRC, encoding='utf-8')
        s = EditorSession(project_root=tmp_path)
        s.load(str(ch))
        # 先在后一拍退场（模拟第一次点 ✕ 后保存）
        r1 = s.save(3, {'Text': s.beats[3].text}, cards=[_hide()])
        assert r1['changed'] and s.beats[3].hide == 'ling'
        assert not r1.get('notes')
        # 再到前一拍退场（第二次点 ✕ 后保存）
        r2 = s.save(2, {'Text': s.beats[2].text}, cards=[_hide()])
        assert r2['changed']
        assert s.beats[2].hide == 'ling'
        assert s.beats[3].hide == ''
        assert r2.get('notes'), '并掉了一条却一声不吭 = 静默改用户的演出'

    def test_export_writes_exactly_one_hide(self, tmp_path):
        """落在磁盘上的产物里只有一条 `hide ling`（用户截图里的样子要消失）。"""
        from storygraph.app.editor import EditorSession

        ch = tmp_path / 'game' / 'c1.rpy'
        ch.parent.mkdir(parents=True)
        ch.write_text(_SRC, encoding='utf-8')
        s = EditorSession(project_root=tmp_path)
        s.load(str(ch))
        s.save(3, {'Text': s.beats[3].text}, cards=[_hide()])
        s.save(2, {'Text': s.beats[2].text}, cards=[_hide()])
        res = s.export(str(ch))
        assert res['ok'], res
        text = ch.read_text(encoding='utf-8')
        assert text.count('hide ling') == 1, text


def test_the_real_card_grid_button_ends_with_one_hide(qapp, tmp_path):
    """**真控件**：在卡片网格上对「沿用」卡点 ✕（后一拍先点、前一拍再点），
    存完只留一条 `hide ling` —— 而且前一拍点完之后，后一拍的网格里
    **已经没有那张沿用卡了**（人已经下台，点第二次都点不到）。"""
    from storygraph.ui_qt.sprite_grid import SpriteGridWidget

    from storygraph.app.editor import EditorSession

    ch = tmp_path / 'game' / 'c1.rpy'
    ch.parent.mkdir(parents=True)
    ch.write_text(_SRC, encoding='utf-8')
    s = EditorSession(project_root=tmp_path)
    s.load(str(ch))

    def ling_card(idx):
        grid = SpriteGridWidget()
        grid.set_symbols(s.symbols)
        grid.load(s.cards(idx))
        found = None
        for card in grid._cards:
            if str((card.item or {}).get('tag') or '').startswith('ling'):
                found = card
                break
        return grid, found

    def press(idx):
        grid, card = ling_card(idx)
        assert card is not None, [c.item for c in grid._cards]
        grid._on_remove(card)
        s.save(idx, {'Text': s.beats[idx].text},
               cards=[c.item for c in grid._cards])
        grid.close()

    press(3)                                  # 用户：先在后一拍点
    assert s.beats[3].hide == 'ling'
    press(2)                                  # 再跑到前面一拍点
    assert s.beats[2].hide == 'ling'
    assert s.beats[3].hide == '', '后一拍那条多余的退场要并掉'
    assert [b.hide for b in s.beats].count('ling') == 1
    # 人已经下台 → 后一拍不再有"沿用"卡可点
    grid, card = ling_card(3)
    try:
        assert card is None, [c.item for c in grid._cards]
    finally:
        grid.close()
