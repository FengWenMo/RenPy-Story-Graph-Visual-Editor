"""
tests/test_projection_single_source.py
======================================
「一条舞台指令只能有一个真源」的回归测试。

背景（2026-09-28 实测事故）：
真实项目 `sr_general/game/chapter/chapter1_4.rpy` 走一趟
`.rpy → .xlsx → .rpy` 之后，生成了 18 行**非法中文图像 tag**，
show 从 90 条膨胀到 108 条，还有 32 条**带位置的 show 直接丢掉**。

根因是三个函数对"谁承载哪条指令"的划分互相矛盾：

    compose_show_cell   把"带 at 的立绘"**跳过** → 位置丢了
    compose_stage_extra 把"带 at 的立绘"写成 `show X at Y` → 同一句第二遍
    compose_stage_extra 把"带转场的退场"也写一遍 → 退场重复

修好之后必须**严格互补**：合起来恰好覆盖每条指令一次。
这个文件锁的就是这条契约——它是 §2.2 那场事故的防复发测试。

契约表：

    立绘（普通）        → 「立绘」列 `ling happy`
    立绘（带位置）      → 「立绘」列 `ling happy@left`
    退场（普通）        → 「退场」列 `ye`
    退场（带转场）      → 「退场」列 `ye@dissolve`
    复杂 show 子句      → 「舞台（高级）」列（唯一进 Stage 的舞台指令）
"""

from pathlib import Path

import openpyxl
import pytest

from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.script_table import (
    BEAT_FIELDS, Beat, compose_hide_cell, compose_show_cell,
    compose_stage_extra, import_beats_from_excel, make_symbols,
    open_rpy_as_table, render_beats_to_rpy, beats_from_parse_result,
)

#: 真实章节：用于"第 43 / 67 拍"这两个固定用例（两种相反的 stage_order）。
#: 路径统一从 `_source_paths` 取（环境变量 STORYGRAPH_REAL_PROJECT 可覆盖）。
from _source_paths import REAL_CHAPTER as REAL       # noqa: E402


# ==================================================================
#  一、三列的所有权契约
# ==================================================================

class TestCellOwnership:
    def test_show_with_position_stays_in_show_cell(self):
        """`图@位置` 是「立绘」列的形式——它必须留下，不能丢给 Stage。"""
        beat = Beat(seq=1, label='t', text='x', show='ye hi@i11')
        assert compose_show_cell(beat) == 'ye hi@i11'
        assert compose_stage_extra(beat) == ''

    def test_bare_show_stays_in_show_cell(self):
        beat = Beat(seq=1, label='t', text='x', show='ye hi')
        assert compose_show_cell(beat) == 'ye hi'
        assert compose_stage_extra(beat) == ''

    def test_hide_with_transition_stays_in_hide_cell(self):
        """`图@转场` 是「退场」列的形式，Stage 不再重复写。"""
        beat = Beat(seq=1, label='t', text='x', hide='ye@dissolve')
        assert compose_hide_cell(beat) == 'ye@dissolve'
        assert compose_stage_extra(beat) == ''

    def test_multiple_shows_with_positions_all_survive(self):
        beat = Beat(seq=1, label='t', text='x',
                    show='ye hi@i11; lan close_happy@r22')
        assert compose_show_cell(beat) == 'ye hi@i11; lan close_happy@r22'
        assert compose_stage_extra(beat) == ''

    def test_complex_show_clause_is_the_only_stage_owner(self):
        """复杂 show 子句结构化列表达不了 → 这一条必须进 Stage。"""
        beat = Beat(seq=1, label='t', text='x',
                    show='cg cg02@; ')
        beat.show = ''      # 只看 clause 分支
        beat.show = 'cg cg02_2 behind ye'
        cell = compose_show_cell(beat)
        extra = compose_stage_extra(beat)
        # clause 形态：立绘列不收，Stage 收
        assert 'behind ye' in extra
        assert 'behind ye' not in cell


# ==================================================================
#  二、写出去、读回来，不能再翻倍
# ==================================================================

class TestNoDuplicationOnRoundTrip:
    def test_hide_with_transition_is_not_duplicated(self):
        """
        实测过的坏形态：`hide='e@dissolve; e@dissolve'`。
        来源是「退场」列与 Stage 列各写一次。
        """
        beat = Beat(seq=1, label='t', text='x', hide='ye@dissolve')
        beat.stage = compose_stage_extra(beat) or beat.stage
        assert compose_hide_cell(beat) == 'ye@dissolve'
        assert beat.hide.count('@dissolve') == 1

    def test_show_with_position_renders_exactly_once(self):
        """
        `at` 只能产生一条 show。
        修好之前这里是 `show ye_hi at i11` + `show ye hi at i11` 两条。
        """
        beat = Beat(seq=1, label='t', text='x', show='ye hi@i11')
        beat.stage = compose_stage_extra(beat) or beat.stage
        out = render_beats_to_rpy([beat])
        shows = [l for l in out.splitlines() if l.strip().startswith('show')]
        assert len(shows) == 1, shows
        assert 'at i11' in shows[0]


# ==================================================================
#  三、真实形态的整章往返：语句数量不许变
# ==================================================================

CHAPTER = '''label t:
    scene bg room
    show e a
    e "one"
    show e b at left
    e "two"
    hide e
    show e c at right
    e "three"
    hide e with dissolve
    show f x at left
    with fade
    e "four"
    return
'''


class TestChapterRoundTrip:
    def test_statement_counts_are_preserved(self, write_rpy, tmp_path):
        """
        **这是整场事故的验收测试**：整章往返后
        show / hide / scene 的条数必须和原文一致。

        修好之前：show 90→108（双写）且带 at 的丢 32 条。
        """
        src = write_rpy(CHAPTER, 'proj/game/t.rpy')
        tbl = tmp_path / 't.xlsx'
        open_rpy_as_table(src, tbl)
        out = render_beats_to_rpy(import_beats_from_excel(tbl))

        def count(text, verb):
            return sum(1 for l in text.splitlines()
                       if l.strip().split()[:1] == [verb])

        for verb in ('show', 'hide', 'scene'):
            assert count(CHAPTER, verb) == count(out, verb), (
                f'{verb} 条数变了：原文 {count(CHAPTER, verb)} '
                f'→ 产物 {count(out, verb)}\n{out}')

    def test_no_position_is_lost(self, write_rpy, tmp_path):
        """所有 `at <位置>` 都必须活过 Excel 一圈。"""
        src = write_rpy(CHAPTER, 'proj/game/t.rpy')
        tbl = tmp_path / 't.xlsx'
        open_rpy_as_table(src, tbl)
        out = render_beats_to_rpy(import_beats_from_excel(tbl))
        for pos in ('left', 'right'):
            assert f'at {pos}' in out, f'位置 {pos} 在往返里丢了\n{out}'

    def test_no_illegal_chinese_image_tag(self, write_rpy, tmp_path):
        """往返产物里不许出现中文图像 tag（那 18 行的教训）。"""
        src = write_rpy(CHAPTER, 'proj/game/t.rpy')
        tbl = tmp_path / 't.xlsx'
        open_rpy_as_table(src, tbl)
        out = render_beats_to_rpy(import_beats_from_excel(tbl))
        for line in out.splitlines():
            parts = line.strip().split()
            if parts[:1] in (['show'], ['hide'], ['scene']) and len(parts) > 1:
                assert all(ord(c) < 128 for c in parts[1]), line


# ==================================================================
#  四、StageOrder：源顺序往返不丢
# ==================================================================

class TestStageSnapshotDisambiguation:
    """
    快照的"逻辑矛盾"消歧（用户 2026-09-28 报告）。

    用户在第 43 拍看到：变化清单里同时有
        `+ ling oh @ i11（登场）`
        `× 清空立绘（1 个）`
    最终状态却是「台上没人」，认为逻辑矛盾。

    **快照没算错**：源码就是
        show ling oh at i11
        hide cg03
        scene bg bg6_2        ← scene 清空全部立绘
        lyj "在想别的女人的事情？"
    所以"登场又立刻被清掉、最终台上没人"是**忠实还原**。

    但清单读起来像自相矛盾，所以补一句消歧说明。
    关键是**判定要精确**——下面第二个用例就是防误报的。
    """

    INTERLEAVED = '''label t:
    show ling oh at i11
    scene bg room
    lyj "台词"
'''

    #: 第 67 拍那种顺序：清空**之后**又 show，最终台上有人 → 不该加说明。
    SCENE_THEN_SHOW = '''label t:
    hide cg03
    scene bg room
    show ling shy at f11
    lyj "台词"
'''

    def _snap(self, write_rpy, tmp_path, src, name):
        from storygraph.story.stage_snapshot import snapshot_rows
        fp = write_rpy(src, f'proj/game/{name}.rpy')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(fp)))
        return beats, snapshot_rows(beats)

    def test_show_then_scene_reports_empty_with_note(self, write_rpy, tmp_path):
        beats, rows = self._snap(write_rpy, tmp_path, self.INTERLEAVED, 'a')
        r = rows[0]
        changes = r['display_changes'] or ''
        assert '+ ling oh @ i11' in changes
        assert '清空立绘' in changes
        assert r['actors'] == '', 'scene 应该清空立绘（快照语义）'
        assert '注：' in changes, '缺消歧说明，用户会以为自相矛盾'

    def test_scene_then_show_has_no_note(self, write_rpy, tmp_path):
        """`scene` 之后又 show 了 → 台上最终有人 → **不许**加消歧说明。"""
        beats, rows = self._snap(write_rpy, tmp_path,
                                 self.SCENE_THEN_SHOW, 'b')
        r = rows[0]
        changes = r['display_changes'] or ''
        assert '注：' not in changes, f'误报消歧说明：{changes}'
        assert r['actors'], 'scene 之后的 show 应该让台上有人'

    def test_stage_order_is_the_authority(self, write_rpy, tmp_path):
        """
        拍内顺序的权威是 `stage_order`，不是变化清单里各条的位置。
        同样两件事（show + scene），换个顺序结论就相反。
        """
        _b1, rows1 = self._snap(write_rpy, tmp_path, self.INTERLEAVED, 'c')
        _b2, rows2 = self._snap(write_rpy, tmp_path, self.SCENE_THEN_SHOW, 'd')
        r1, r2 = rows1[0], rows2[0]
        assert r1['actors'] == '' and r2['actors'] != '', \
            '顺序不同必须得到不同的最终状态'


@pytest.mark.skipif(not REAL.exists(), reason='本机没有 chapter1_3.rpy')
class TestRealChapterSnapshot43:
    """真实章节第 43 / 67 拍当固定用例（两种相反的 stage_order）。"""

    def _rows(self):
        from storygraph.story.stage_snapshot import snapshot_rows
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(REAL)))
        return beats, snapshot_rows(beats)

    def test_beat_43_is_empty_and_explained(self):
        beats, rows = self._rows()
        assert beats[42].stage_order == 'show,hide,scene'
        r = rows[42]
        assert r['actors'] == ''
        assert '注：' in (r['display_changes'] or '')

    def test_beat_67_keeps_actor_and_has_no_note(self):
        beats, rows = self._rows()
        assert beats[66].stage_order == 'hide,scene,show'
        r = rows[66]
        assert r['actors'], 'scene 之后的 show 应该留在台上'
        assert '注：' not in (r['display_changes'] or '')


@pytest.mark.skipif(not REAL.exists(), reason='本机没有 chapter1_3.rpy')
class TestStageOrderRoundTrip:
    def test_hide_show_order_survives(self, write_rpy, tmp_path):
        """
        `hide X` + `show X`（重置立绘）与反序语义相反——
        顺序丢了就会把立绘藏掉。它是**逐行**属性，藏在隐藏列里往返。
        """
        src = write_rpy(
            'label t:\n'
            '    show e a\n'
            '    e "before"\n'
            '    hide e\n'
            '    show e b\n'
            '    e "after"\n',
            'proj/game/order.rpy')
        tbl = tmp_path / 'order.xlsx'
        open_rpy_as_table(src, tbl)

        back = import_beats_from_excel(tbl)
        target = [b for b in back if b.text == 'after']
        assert target, [b.text for b in back]
        assert target[0].stage_order == 'hide,show', target[0].stage_order

    def test_stage_order_column_exists_and_is_hidden(self, write_rpy, tmp_path):
        """
        列必须在表里（否则往返就丢），但必须**整列隐藏**：
        用户 2026-09-28 拍板「StageOrder 由工具维护，不暴露给策划」。
        """
        src = write_rpy(CHAPTER, 'proj/game/t.rpy')
        tbl = tmp_path / 't.xlsx'
        open_rpy_as_table(src, tbl)

        ws = openpyxl.load_workbook(tbl)['Beats']
        assert 'StageOrder' in BEAT_FIELDS
        from openpyxl.utils import get_column_letter
        letter = get_column_letter(BEAT_FIELDS.index('StageOrder') + 1)
        assert ws.column_dimensions[letter].hidden is True
        # 列头仍然写着（隐藏不等于没这一列），但读表时不被当成人填的列
        assert ws[1][BEAT_FIELDS.index('StageOrder')].value
