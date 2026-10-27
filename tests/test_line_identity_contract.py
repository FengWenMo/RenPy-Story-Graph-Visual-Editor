"""
tests/test_line_identity_contract.py
====================================
`docs/契约_行身份.md` 的**逐条判据**（R1–R9 + I1）。

为什么值得一个专门的文件：LineId 是 XLIFF `trans-unit/@id`、TTS 语音 key、
Unity 行映射、协作库主键**共用**的那一个身份。它一不稳，四个出口同时错位，
而且**都是静默的**（2026-10-23 之前 `_renumber` 就是按位置重写全部 ID）。

⚠ 这些用例在 2026-10-23 之前是**红的**（插一行 → 后面所有 ID 变），
修法是 `editor._renumber` 只给"没有身份的行"发号 + `line_identity` 统一发号。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from _ui_project import build_project                                    # noqa: E402
from storygraph.story.line_identity import allocate, parse, unique_ids    # noqa: E402


@pytest.fixture
def session(tmp_path):
    from storygraph.app.editor import EditorSession
    project = build_project(tmp_path / 'proj')
    s = EditorSession(project_root=project)
    s.load(project / 'game' / 'script' / 'ch01.rpy')
    return s


def ids(session) -> list:
    return [str(b.line_id) for b in session.beats]


# ==================================================================
#  R1–R4：改内容不改身份
# ==================================================================

class TestContentEditsKeepIdentity:
    def test_editing_text_keeps_id(self, session):
        before = ids(session)
        session.save(0, {'Text': '改过的台词'})
        assert ids(session) == before

    def test_editing_speaker_keeps_id(self, session):
        before = ids(session)
        session.save(0, {'Speaker': 'm'})
        assert str(session.beats[0].speaker) == 'm'
        assert ids(session) == before, '改说话人换了身份 —— ID 里不许含 speaker'

    def test_editing_condition_keeps_id(self, session):
        before = ids(session)
        session.save(0, {'Condition': 'love >= 5'})
        assert ids(session) == before, '改条件换了身份 —— 条件不进 ID'

    def test_editing_stage_keeps_id(self, session):
        before = ids(session)
        session.save(0, {'Stage': 'show eileen happy'})
        assert ids(session) == before


# ==================================================================
#  R5–R7：结构变了，身份只跟着"那一行"
# ==================================================================

class TestEmptyLabelInheritance:
    """
    清单 §二（P0）：空 label 必须继承**前文最近**的 label，而不是文件头。

    从前的 `_renumber` 取"文件里第一个非空 label"回填给所有空 label，
    于是第二章里插一拍，它的 label / LineId 前缀都跨回第一章。
    """

    def test_empty_label_inherits_the_nearest_preceding_label(self):
        from storygraph.app.editor import EditorSession
        from storygraph.story.beat_model import Beat

        session = EditorSession()
        session.beats = [
            Beat(label='ch01', text='第一章第一句'),
            Beat(label='', text='（ch01 里新插的一拍）'),
            Beat(label='ch02', text='第二章开场'),
            Beat(label='', text='（ch02 里新插的一拍）'),
        ]
        session._renumber()

        assert [b.label for b in session.beats] == \
            ['ch01', 'ch01', 'ch02', 'ch02']
        assert [b.line_id for b in session.beats] == \
            ['ch01.0001', 'ch01.0002', 'ch02.0001', 'ch02.0002']

    def test_leading_empty_label_is_not_guessed(self):
        """文件开头还没有 label 时保持空 —— 不替它挂到别的章上。"""
        from storygraph.app.editor import EditorSession
        from storygraph.story.beat_model import Beat

        session = EditorSession()
        session.beats = [Beat(label='', text='开场白'),
                         Beat(label='ch01', text='第一章')]
        session._renumber()

        assert session.beats[0].label == ''
        assert session.beats[1].label == 'ch01'


class TestStructuralEditsKeepOtherIdentities:
    def test_inserting_a_line_keeps_every_other_id(self, session):
        before = ids(session)
        res = session.insert_beat(1)
        assert res.get('ok')
        after = ids(session)
        assert len(after) == len(before) + 1
        new_id = after[res['index']]
        assert new_id not in before, '新行拿了既存的 ID'
        # 其余行：一条不少、顺序也没变（多出来的只有新行）
        rest = [i for i in after if i != new_id]
        assert rest == before, f'插入后其它行的 ID 变了：\n前 {before}\n后 {after}'

    def test_deleting_a_line_keeps_every_other_id(self, session):
        before = ids(session)
        victim = before[1]
        session.delete_beat(1)
        after = ids(session)
        assert victim not in after, '删掉的行 ID 还在'
        assert after == [i for i in before if i != victim], (
            f'删除后其它行的 ID 变了：\n前 {before}\n后 {after}')

    def test_moving_a_line_keeps_its_id(self, session):
        before = ids(session)
        moved = before[0]
        res = session.move_beat(0, 3)
        assert res.get('ok', True)
        after = ids(session)
        assert sorted(after) == sorted(before), '移动后身份集合变了'
        assert after.index(moved) == 3, f'被移动那一行的 ID 没有跟着它：{after}'

    def test_a_dropped_id_is_not_reused_within_the_session(self, session):
        """I3：删掉的号不再发给新行（同一文档生命周期内）。"""
        before = ids(session)
        victim = before[1]
        session.delete_beat(1)
        for _ in range(3):
            session.insert_beat(0)
        assert victim not in ids(session), '删掉的 ID 又被发出来了'


# ==================================================================
#  R8：表 ↔ .rpy 往返
# ==================================================================

class TestRoundtripKeepsIdentity:
    def test_roundtrip_through_the_script_keeps_ids(self, tmp_path):
        from storygraph.pipeline.compile import compile_table
        from storygraph.story.script_table import (
            import_beats_from_excel, open_rpy_as_table,
        )

        project = build_project(tmp_path / 'proj')
        rpy = project / 'game' / 'script' / 'ch01.rpy'
        t1 = tmp_path / 't1.xlsx'
        open_rpy_as_table(str(rpy), str(t1))
        ids1 = [b.line_id for b in import_beats_from_excel(t1)]
        assert all(ids1), '表里没有 LineId 列'

        rpy2 = tmp_path / 'out.rpy'
        _p, _s, res = compile_table(str(t1), rpy_path=rpy2, write_sheet=False,
                                    force=True, source_path=str(rpy),
                                    skip_empty=True, engine_lint=False)
        assert res.written

        t2 = tmp_path / 't2.xlsx'
        open_rpy_as_table(str(rpy2), str(t2))
        ids2 = [b.line_id for b in import_beats_from_excel(t2)]
        assert ids2 == ids1, f'表→rpy→表 之后身份变了：\n{ids1}\n{ids2}'


# ==================================================================
#  R9：重新摊表覆盖
# ==================================================================

class TestRebuildOverExistingTable:
    def test_rebuild_over_an_existing_table_keeps_ids(self, tmp_path):
        """
        重新摊表覆盖时，**存量身份**必须接回来。

        造法：先把表里的「行号」改成一批**与位置无关**的号（`ch01.9NNN`）。
        如果重摊按位置重算，它们会变回 `ch01.0001…` —— 那正是要防的。
        """
        import openpyxl
        from storygraph.story.script_table import (
            import_beats_from_excel, open_rpy_as_table,
        )

        project = build_project(tmp_path / 'proj')
        rpy = project / 'game' / 'script' / 'ch01.rpy'
        table = tmp_path / 'ch01.xlsx'
        open_rpy_as_table(str(rpy), str(table))
        before = [b.line_id for b in import_beats_from_excel(table)]
        assert all(before)

        wb = openpyxl.load_workbook(table)
        ws = wb['Beats']
        header = [c.value for c in ws[1]]
        col = header.index('行号') + 1                  # 行号 = LineId
        custom = [f'ch01.9{i:03d}' for i in range(1, ws.max_row)]
        for i, lid in enumerate(custom, start=2):
            ws.cell(row=i, column=col).value = lid
        wb.save(table)

        open_rpy_as_table(str(rpy), str(table))       # ← 重新摊表覆盖
        after = [b.line_id for b in import_beats_from_excel(table)]
        assert after == custom, (
            f'重新摊表后身份没接回来（说明是按位置重算的）：\n期望 {custom}\n实际 {after}')

    def test_a_rebuild_that_adds_rows_does_not_reuse_old_ids(self, tmp_path):
        """插过行的表再重摊：数量不等时**整批发新号**（不猜），但绝不重复。"""
        from storygraph.story.script_table import (
            import_beats_from_excel, open_rpy_as_table,
        )

        project = build_project(tmp_path / 'proj')
        rpy = project / 'game' / 'script' / 'ch01.rpy'
        table = tmp_path / 'ch01.xlsx'
        open_rpy_as_table(str(rpy), str(table))
        old = [b.line_id for b in import_beats_from_excel(table)]

        # 在 .rpy 里加一句（结构变了），再重摊
        text = rpy.read_text(encoding='utf-8')
        rpy.write_text(text.replace('label ch01:', 'label ch01:\n    "新加的一句。"'),
                       encoding='utf-8')
        open_rpy_as_table(str(rpy), str(table))
        new = [b.line_id for b in import_beats_from_excel(table)]

        assert len(new) == len(old) + 1
        assert len(set(new)) == len(new), f'重摊后出现重复 ID：{new}'


# ==================================================================
#  I1：唯一性（发号器本身）
# ==================================================================

class TestAllocator:
    def test_parse(self):
        assert parse('ch01.0007') == ('ch01', 7)
        assert parse('main.route.0012') == ('main.route', 12)
        assert parse('') is None
        assert parse('ch01') is None

    def test_ensure_ids_only_fills_empty_ones(self):
        """
        清单 §七：`assign_identity` 那个名字塞了两件事。只想补号时用
        `ensure_ids` —— 已有身份一个都不许动。
        """
        from storygraph.story.beat_model import Beat, ensure_ids

        beats = [Beat(label='ch01', seq=1, text='a'),
                 Beat(label='ch01', seq=9, text='b', line_id='ch01.0042')]
        assert ensure_ids(beats) == 1
        assert beats[0].line_id == 'ch01.0001'
        assert beats[1].line_id == 'ch01.0042', '已有身份被改掉了'

    def test_refresh_fingerprints_only_touches_the_hash(self):
        from storygraph.story.beat_model import Beat, refresh_fingerprints

        beat = Beat(label='ch01', seq=1, text='a', line_id='ch01.0001')
        before = beat.line_id
        refresh_fingerprints([beat])
        assert beat.line_id == before
        assert beat.beat_fingerprint, '指纹没有被重算'

    def test_duplicate_ids_are_reissued_using_the_beat_label(self):
        """
        清单 §十六：跨 label 搬之后 ID 前缀是历史来源，不是当前归属。
        重号时新 ID 必须挂在**这一拍自己的 label** 下。
        """
        class B:
            def __init__(self):
                self.label = 'ch02'
                self.line_id = 'ch01.0003'

        beats = [B(), B()]
        assert unique_ids(beats) is True
        assert beats[0].line_id == 'ch01.0003'      # 第一次出现的保留
        assert beats[1].line_id.startswith('ch02.'), beats[1].line_id


    def test_allocate_takes_max_plus_one(self):
        used = {'ch01.0001', 'ch01.0003'}
        assert allocate(used, 'ch01') == 'ch01.0004'

    def test_allocate_skips_used_numbers(self):
        used = {'ch01.0001', 'ch01.0002'}
        assert allocate(used, 'ch01') == 'ch01.0003'

    def test_allocate_is_per_label(self):
        used = {'ch01.0009', 'other.0002'}
        assert allocate(used, 'other') == 'other.0003'

    def test_duplicate_ids_are_reissued_keeping_the_first(self):
        class B:
            def __init__(self, lid):
                self.line_id = lid

        beats = [B('a.0001'), B('a.0002'), B('a.0001')]
        assert unique_ids(beats) is True
        assert beats[0].line_id == 'a.0001'      # 第一次出现的保留
        assert len({b.line_id for b in beats}) == 3

    # ------------------------------------------------------------------
    #  重号只许动重复的那一条（2026-10-27 审计 P0）
    # ------------------------------------------------------------------

    def test_the_duplicate_does_not_steal_a_later_rows_number(self):
        """
        审计实测的最小复现：`[0001, 0001, 0002]` 从前会变成
        `[0001, 0002, 0003]` —— **第三拍从未重号**，身份却被改掉了。

        正确的结局只有一种：重复的那条拿一个"整批都没用过"的号（0003），
        第三拍守着自己的 0002。
        """
        class B:
            def __init__(self, lid):
                self.line_id = lid
                self.label = 'ch01'

        beats = [B('ch01.0001'), B('ch01.0001'), B('ch01.0002')]
        assert unique_ids(beats) is True
        assert [b.line_id for b in beats] == \
            ['ch01.0001', 'ch01.0003', 'ch01.0002']

    def test_renumbering_never_cascades_to_innocent_rows(self):
        """
        "其余行不变"（R5/R6/R7）：**在输入里本来就唯一**的 ID 一个都不许换。

        三种顺序都试（重复在首 / 在尾 / 在中间，以及重复之后还有长尾）——
        老实现只在"重复恰好在最后"时才对（原用例正是那一种，所以它一直是绿的）。
        """
        class B:
            def __init__(self, lid):
                self.line_id = lid
                self.label = 'ch01'

        cases = (
            ['ch01.0001', 'ch01.0001', 'ch01.0002'],
            ['ch01.0002', 'ch01.0001', 'ch01.0002'],
            ['ch01.0001', 'ch01.0002', 'ch01.0001'],
            ['ch01.0001', 'ch01.0001', 'ch01.0002', 'ch01.0003'],
            ['ch01.0003', 'ch01.0004', 'ch01.0003', 'ch01.0005'],
        )
        for given in cases:
            beats = [B(i) for i in given]
            unique_ids(beats)
            got = [b.line_id for b in beats]
            assert len(set(got)) == len(got), (given, got)
            innocent = {i for i in given if given.count(i) == 1}
            assert innocent <= set(got), (
                f'这些本来唯一的行被改了身份：{sorted(innocent - set(got))}'
                f'\n输入 {given} → 输出 {got}')
            # 第一次出现的那条也不许动（保留"先到先得"）
            assert got[given.index(given[0])] == given[0], (given, got)

    def test_a_duplicate_still_gets_a_number_nobody_uses(self):
        """
        修法的另一半：新号仍要"不撞车、不回收" —— 不许为了不级联就退回
        `[0001, 0001, 0001]` 这种更坏的结果。
        """
        class B:
            def __init__(self, lid):
                self.line_id = lid
                self.label = 'ch01'

        beats = [B('ch01.0001'), B('ch01.0001'), B('ch01.0001')]
        assert unique_ids(beats) is True
        got = [b.line_id for b in beats]
        assert got[0] == 'ch01.0001'
        assert len(set(got)) == 3, got
        assert all(i.startswith('ch01.') for i in got), got
