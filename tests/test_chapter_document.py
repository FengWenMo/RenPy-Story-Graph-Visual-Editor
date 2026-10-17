"""
章节文档（`story.chapter.ChapterDocument`）与排序权威（P0-2 / P0-3）
====================================================================

P0-2 的根因不是"某处忘了转移顶层块"，而是**章节级的东西住在一个可删除的
对象上**：`file_blocks` 挂在第一张拍，`delete_beat` 只是 `beats.pop()`，
于是删第一张卡 = `init python` / `default` / `define` 整块消失。

P0-3 的根因是**两个排序信号只有一个被信**：`Seq` 看起来是排序键，导入却
按行序；渲染也按行序。Excel 里按说话人排一下 / 拖动一行，剧情顺序就静默
变了，而 validate 只查重复和断号，不查倒序。

这个文件守：
  · 删 / 插 / 恢复拍，顶层块**一个字节都不丢、也不重复**；
  · `ChapterDocument` 是章节级真源，拍上只是传输副本（sync 幂等）；
  · 行序与 `Seq` 冲突时按 Seq 重排（缺号行留在原位），并留下 W203 的提示；
  · Excel 往返之后剧情顺序与 Seq 一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story.beat_model import Beat                 # noqa: E402
from storygraph.story.chapter import (ChapterDocument,      # noqa: E402
                                      order_beats_by_seq)

FILE_BLOCK = 'init python:\n    persistent.x = 1'


def _chapter_src() -> str:
    return ('init python:\n    persistent.x = 1\n\n'
            'define e = Character("Eileen")\n\n'
            'label a:\n    e "one"\n    e "two"\n    return\n')


def _load(tmp_path):
    from storygraph.app.editor import EditorSession
    src = tmp_path / 'a.rpy'
    src.write_text(_chapter_src(), encoding='utf-8')
    session = EditorSession()
    session.load(src)
    return session


class TestChapterDocumentOwnsFileBlocks:

    def test_from_beats_adopts_and_reassigns_the_carrier(self):
        """拍上带来的顶层块 → 收编为文档真源，并只留在第一拍（传输副本）。"""
        beats = [Beat(seq=1, label='a'),
                 Beat(seq=2, label='a', file_blocks=FILE_BLOCK)]
        doc = ChapterDocument.from_beats(beats)
        assert doc.file_blocks == FILE_BLOCK
        assert beats[0].file_blocks == FILE_BLOCK
        assert beats[1].file_blocks == ''
        # 幂等：再 sync 一次结果一样（不会翻倍、不会换拍）
        before = doc.file_blocks
        doc.sync()
        assert doc.file_blocks == before
        assert beats[0].file_blocks == FILE_BLOCK

    def test_sync_collects_blocks_that_appear_on_later_beats(self):
        """
        外部整体替换拍列表（解析 / Excel 导入 / 单测直接赋值）时，
        新带来的块要被收编 —— 不能因为"文档里没有"就丢掉。
        """
        doc = ChapterDocument.from_beats([Beat(seq=1, label='a')])
        doc.beats = [Beat(seq=1, label='a'),
                     Beat(seq=2, label='a', file_blocks=FILE_BLOCK)]
        doc.sync()
        assert doc.file_blocks == FILE_BLOCK
        assert doc.beats[0].file_blocks == FILE_BLOCK
        assert doc.beats[1].file_blocks == ''

    def test_duplicate_blocks_are_written_once(self):
        """同一段块同时挂在两张拍上（历史数据 / 撤销恢复）→ 产物里只出现一次。"""
        doc = ChapterDocument.from_beats([
            Beat(seq=1, label='a', file_blocks=FILE_BLOCK),
            Beat(seq=2, label='a', file_blocks=FILE_BLOCK),
        ])
        assert doc.file_blocks.count('persistent.x') == 1


class TestStructuralEditsKeepFileBlocks:

    def test_deleting_the_first_beat_keeps_init_python(self, tmp_path):
        """调研 P0-2 的原始复现：删第一拍后 `init python` 必须还在。"""
        session = _load(tmp_path)
        assert 'init python' in session.chapter_file_blocks()
        assert session.delete_beat(0)['ok']
        assert 'init python' in session.chapter_file_blocks()
        assert 'persistent.x = 1' in session.beats[0].file_blocks
        text = session.compile().rpy_text
        assert 'persistent.x = 1' in text, '删第一拍后顶层 init python 消失了'
        assert 'define e = Character' in text

    def test_inserting_before_the_first_beat_keeps_it(self, tmp_path):
        session = _load(tmp_path)
        session.insert_beat(0, after=False)
        text = session.compile().rpy_text
        assert text.count('persistent.x = 1') == 1
        assert 'define e = Character' in text

    def test_restoring_the_removed_beat_does_not_duplicate(self, tmp_path):
        """删了再恢复：块既不能丢，也不能因为两张拍各带一份而写两遍。"""
        session = _load(tmp_path)
        session.delete_beat(0)
        session.restore_last_removed()
        text = session.compile().rpy_text
        assert text.count('persistent.x = 1') == 1

    def test_structural_undo_keeps_it(self, tmp_path):
        session = _load(tmp_path)
        session.insert_beat(0, after=False)
        session.undo_structural()
        text = session.compile().rpy_text
        assert text.count('persistent.x = 1') == 1


class TestSeqIsTheOrderAuthority:

    def test_swapped_rows_are_put_back_in_seq_order(self):
        """调研 P0-3 的原始复现：交换两行 → 按 Seq 还原 + 一条警告。"""
        beats = [Beat(seq=2, label='a', text='TWO'),
                 Beat(seq=1, label='a', text='ONE')]
        ordered, notes = order_beats_by_seq(beats)
        assert [b.text for b in ordered] == ['ONE', 'TWO']
        assert notes and 'Seq' in notes[0]

    def test_rows_already_in_order_produce_no_warning(self):
        beats = [Beat(seq=1, label='a'), Beat(seq=2, label='a')]
        ordered, notes = order_beats_by_seq(beats)
        assert [b.seq for b in ordered] == [1, 2]
        assert notes == []

    def test_rows_without_seq_stay_where_they_are(self):
        """
        手工新加、还没编号的行（Seq=0）**不参与排序** ——
        否则"插在中间的新行"会被甩到段尾。
        """
        beats = [Beat(seq=1, label='a', text='ONE'),
                 Beat(seq=0, label='a', text='NEW'),
                 Beat(seq=2, label='a', text='TWO')]
        ordered, notes = order_beats_by_seq(beats)
        # 有号的是 1、2，行序已经是 1,2 → 不动；0 留在原位
        assert [b.text for b in ordered] == ['ONE', 'NEW', 'TWO']
        assert notes == []

    def test_duplicate_seq_is_not_guessed(self):
        """重复 Seq 分不出先后 → 不排（交给 validate 报 E203），不瞎猜。"""
        beats = [Beat(seq=2, label='a', text='B'),
                 Beat(seq=1, label='a', text='A'),
                 Beat(seq=1, label='a', text='A2')]
        ordered, notes = order_beats_by_seq(beats)
        assert [b.text for b in ordered] == ['B', 'A', 'A2']
        assert notes == []

    def test_labels_are_sorted_independently(self):
        """只在同 label 的连续段内排：两章各自的 Seq 互不干扰。"""
        beats = [Beat(seq=2, label='a', text='a2'),
                 Beat(seq=1, label='a', text='a1'),
                 Beat(seq=4, label='b', text='b4'),
                 Beat(seq=3, label='b', text='b3')]
        ordered, notes = order_beats_by_seq(beats)
        assert [b.text for b in ordered] == ['a1', 'a2', 'b3', 'b4']
        assert len(notes) == 2


class TestExcelImportUsesSeq:

    def test_swapped_excel_rows_do_not_reorder_the_story(self, tmp_path):
        """
        Excel 里交换两行（模拟"按说话人排序 / 拖动行"）→ 导入必须按 Seq
        还原剧情顺序，并在拍上留下"工具替你重排了"的提示。
        """
        import openpyxl

        from storygraph.story.excel_export import export_beats_to_excel
        from storygraph.story.excel_import import import_beats_from_excel
        from storygraph.story.script_table import BEAT_FIELDS

        beats = [Beat(seq=1, label='ch01', kind='say', speaker='e',
                      text='ONE', line_id='ch01.0001'),
                 Beat(seq=2, label='ch01', kind='say', speaker='e',
                      text='TWO', line_id='ch01.0002')]
        path = tmp_path / 't.xlsx'
        export_beats_to_excel(beats, path)
        wb = openpyxl.load_workbook(path)
        ws = wb['Beats']
        rows = [[c.value for c in r] for r in ws.iter_rows(min_row=2, max_row=3)]
        for i, row in enumerate(reversed(rows)):
            for j, value in enumerate(row, start=1):
                ws.cell(row=2 + i, column=j, value=value)
        wb.save(path)

        back = import_beats_from_excel(path)
        assert [(b.seq, b.text) for b in back] == [(1, 'ONE'), (2, 'TWO')]
        assert back[0].runtime_notes, '重排了却没说 —— 人以为拖动生效了'
        assert 'Seq' in back[0].runtime_notes[0]

    def test_excel_column_order_follows_the_row_not_seq(self, tmp_path):
        """反向：表里本来就是按 Seq 排的，导入不许把它弄乱。"""
        from storygraph.story.excel_export import export_beats_to_excel
        from storygraph.story.excel_import import import_beats_from_excel

        beats = [Beat(seq=1, label='ch01', text='ONE', line_id='ch01.0001'),
                 Beat(seq=2, label='ch01', text='TWO', line_id='ch01.0002')]
        path = tmp_path / 't.xlsx'
        export_beats_to_excel(beats, path)
        back = import_beats_from_excel(path)
        assert [b.text for b in back] == ['ONE', 'TWO']
        assert back[0].runtime_notes == []
