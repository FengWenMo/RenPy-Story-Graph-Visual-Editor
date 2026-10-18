"""
32767 这条线：全工具**只有一个数**、只有一种处置（2026-10-18）
===========================================================

用户拍板：

> 算了……还是把字数改回 excel 的单元格上限吧……不然太容易出问题了
> （一条 3w 多字，整个屏幕估计都装不下）

这个文件守四件事：

  1. 上限在哪都同一个数（`story.cell_limits.CELL_TEXT_LIMIT`，32767）；
  2. 边界：32767 字**写得进去**、32768 字**明确报错**（不是截断）；
  3. 报错点到"哪一行、哪一格、多出多少字"，并且**一个文件都不留**；
  4. 兜底在 `excel.cells.set_text` —— 任何一条漏网的写表路径都拦。

为什么不再"搬进隐藏表"：那条路（`长文本保真`）把一行 payload 撑到 64KB，
协作的 `BasePayload` 抄进单元格照样被截，第二次协作起字段级合并全部退化。
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.excel.cells import append_text_row, set_text        # noqa: E402
from storygraph.story.beat_model import (CELL_TEXT_LIMIT,           # noqa: E402
                                         Beat,
                                         join_file_blocks)
from storygraph.core.cell_limits import (CellTextTooLong,          # noqa: E402
                                          over_limit,
                                          row_offenders)
from storygraph.story.excel_export import export_beats_to_excel     # noqa: E402


def _one(text, lid='ch01.0001'):
    return [Beat(seq=1, label='ch01', kind='say', speaker='e',
                 text=text, line_id=lid)]


class TestTheNumberIsOne:
    def test_the_limit_is_excels_limit(self):
        assert CELL_TEXT_LIMIT == 32767

    def test_over_limit_counts_characters(self):
        assert over_limit('x' * (CELL_TEXT_LIMIT - 1)) == 0
        assert over_limit('x' * CELL_TEXT_LIMIT) == 0
        assert over_limit('x' * (CELL_TEXT_LIMIT + 1)) == CELL_TEXT_LIMIT + 1

    def test_non_strings_are_not_capped(self):
        """数字/空值不会被 Excel 截断，不该在这里被拦。"""
        assert over_limit(None) == 0
        assert over_limit(12345) == 0

    def test_row_offenders_name_the_field(self):
        got = row_offenders(['短', 'x' * 40000], ['Text', 'Comment'], 'a.0001')
        assert got == [{'where': 'a.0001', 'field': 'Comment', 'length': 40000}]


class TestBoundary:
    def test_32767_chars_round_trips(self, tmp_path):
        text = '边' * CELL_TEXT_LIMIT
        path = tmp_path / 'ok.xlsx'
        export_beats_to_excel(_one(text), path, include_dropdowns=False)
        from storygraph.story.excel_import import import_beats_from_excel
        back = import_beats_from_excel(path)
        assert back[0].text == text, (
            f'32767 字往返变了：{len(back[0].text)} 字')

    def test_32768_chars_is_refused_and_leaves_no_file(self, tmp_path):
        path = tmp_path / 'no.xlsx'
        with pytest.raises(CellTextTooLong) as err:
            export_beats_to_excel(_one('x' * (CELL_TEXT_LIMIT + 1)), path,
                                  include_dropdowns=False)
        msg = str(err.value)
        assert '32768' in msg and '台词' in msg and 'ch01.0001' in msg, msg
        assert not path.exists()

    def test_the_message_says_how_much_is_over(self, tmp_path):
        with pytest.raises(CellTextTooLong) as err:
            export_beats_to_excel(_one('x' * (CELL_TEXT_LIMIT + 7233)),
                                  tmp_path / 'no.xlsx',
                                  include_dropdowns=False)
        assert '多出 7233 字' in str(err.value), str(err.value)


class TestLastMileBackstop:
    """`set_text` 是最后一道：任何一张表、任何一条路径写超长格都会被拦。"""

    def test_set_text_refuses_an_over_long_value(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        with pytest.raises(CellTextTooLong) as err:
            set_text(ws, 2, 3, 'x' * (CELL_TEXT_LIMIT + 1))
        assert 'R2C3' in str(err.value) or '第 2 行第 3 列' in str(err.value)

    def test_append_text_row_refuses_too(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        with pytest.raises(CellTextTooLong):
            append_text_row(ws, ['ok', 'x' * (CELL_TEXT_LIMIT + 1)])


class TestFileBlocksSheetStillHasALimit:
    """
    「文件级保真」是"一行一格"，**不是**无上限通道。

    整段顶层文本当然可以超过 32767（它被拆成多行）—— 但**单行**不行：
    那本来就该拆成多行，而不是塞进一个格子让 Excel 去截。
    """

    def test_single_line_over_the_limit_is_refused(self, tmp_path):
        blocks = join_file_blocks([('init python:',
                                    'x = 1  # ' + 'y' * (CELL_TEXT_LIMIT + 5))])
        beats = _one('短句')
        beats[0].file_blocks = blocks
        with pytest.raises(CellTextTooLong) as err:
            export_beats_to_excel(beats, tmp_path / 'fb.xlsx',
                                  include_dropdowns=False)
        assert '文件级保真' in str(err.value), str(err.value)

    def test_many_short_lines_fit_even_if_the_block_is_huge(self, tmp_path):
        """6000 行 × 20 字 = 12 万字，**照样写**（表没有"整段"上限）。"""
        body = '\n'.join(f'    persistent.k{i} = {i}' for i in range(6000))
        beats = _one('短句')
        beats[0].file_blocks = join_file_blocks([('init python:', body)])
        path = tmp_path / 'fb_ok.xlsx'
        export_beats_to_excel(beats, path, include_dropdowns=False)
        wb = openpyxl.load_workbook(path)
        try:
            from storygraph.story.beat_model import FILE_BLOCK_SHEET
            assert FILE_BLOCK_SHEET in wb.sheetnames
            assert wb[FILE_BLOCK_SHEET].max_row >= 6001
        finally:
            wb.close()


class TestEditorCatchesItEarlier:
    """卡片编辑器在保存那一刻就说 —— 不等导出。"""

    def _stub(self, fields):
        class _Panel:
            def collect_all_fields(self_inner):
                return dict(fields)

        class _Obj:
            _panel = _Panel()

        return _Obj()

    def test_save_is_blocked_for_over_long_fields(self):
        from storygraph.ui_qt.window_card import CardPageMixin

        obj = self._stub({'Text': 'x' * (CELL_TEXT_LIMIT + 1),
                          'Comment': '## 短'})
        over = CardPageMixin._too_long_fields(obj)
        assert over == [('Text', CELL_TEXT_LIMIT + 1)]

    def test_normal_text_passes(self):
        from storygraph.ui_qt.window_card import CardPageMixin

        obj = self._stub({'Text': 'x' * CELL_TEXT_LIMIT})
        assert CardPageMixin._too_long_fields(obj) == []
