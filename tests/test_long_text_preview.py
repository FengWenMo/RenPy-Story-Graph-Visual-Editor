"""
老表的超长文本：**读得回来**，而且预览被改过时必须停下（调研 P1-3）
==================================================================

背景（两次反转，都记在这里）：

  · 2026-10-16：40000 字独白写进一格 → openpyxl 静默截成 32767，产物少
    3187 字且零警告。当时的处置是"**不设上限**"：格子只放
    「200 字预览 + ⟪全文见「长文本保真」表⟫」，全文搬进隐藏表。
  · 2026-10-18：那条路自己长出新病 —— 一行 payload 64KB，协作的
    `BasePayload` 抄进单元格照样被截，第二次协作起字段级合并全部退化。
    用户拍板改回 Excel 单元格上限：**写侧不再产出那张表**，超了直接报错
    （见 `core/cell_limits.py`）。

于是现在只剩**读侧**：

  · 老表（带「长文本保真」）仍然能读回来、逐字保真；
  · 预览格被人改过 → `LongTextPreviewEdited`（两条路都是静默改内容）；
  · 标记被删掉 = 用户明确"我只要这段新写的短文本" → 不报错；
  · 读回来的 40000 字**再导出会被闸门拦住** —— 这是有意的：那句该拆成几拍，
    而不是继续在"一个格子装 4 万字"的路上滚。
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story.beat_model import (Beat, LONG_TEXT_MARK,     # noqa: E402
                                         LONG_TEXT_SHEET,
                                         LongTextPreviewEdited)
from storygraph.core.cell_limits import CellTextTooLong             # noqa: E402
from storygraph.story.excel_export import export_beats_to_excel     # noqa: E402
from storygraph.story.excel_import import import_beats_from_excel   # noqa: E402
from storygraph.story.beat_fields import BEAT_FIELDS                # noqa: E402

LONG = '甲' * 40000
PREVIEW = 200
CHUNK = 30000


def _legacy_sheet(tmp_path, text=LONG):
    """
    造一份**老版本工具**写出来的表：格子里是预览 + 标记，全文在隐藏表。

    今天已经写不出这种表了（写侧会直接报错），所以只能手工拼 —— 这正是
    "只读兼容"的含义：不是我们还要这么写，是别人手上还有这样的文件。
    """
    beats = [Beat(seq=1, label='ch01', kind='say', speaker='e',
                  text='占位', line_id='ch01.0001')]
    path = tmp_path / 'legacy.xlsx'
    export_beats_to_excel(beats, path, include_dropdowns=False)
    wb = openpyxl.load_workbook(path)
    try:
        ws = wb['Beats']
        _text_cell(ws).value = text[:PREVIEW] + LONG_TEXT_MARK
        lt = wb.create_sheet(LONG_TEXT_SHEET)
        lt.append(['行号', '字段', '序号', '片段'])
        for i in range(0, len(text), CHUNK):
            lt.append(['ch01.0001', '台词', i // CHUNK, text[i:i + CHUNK]])
        lt.sheet_state = 'hidden'
        wb.save(path)
    finally:
        wb.close()
    return path


def _text_cell(ws):
    return ws.cell(row=2, column=BEAT_FIELDS.index('Text') + 1)


class TestLegacyLongTextStillReads:
    def test_normal_roundtrip_is_byte_exact(self, tmp_path):
        path = _legacy_sheet(tmp_path)
        back = import_beats_from_excel(path)
        assert back[0].text == LONG, '没改过的老表也必须逐字还原全文'

    def test_editing_the_preview_raises_instead_of_silently_losing(self, tmp_path):
        path = _legacy_sheet(tmp_path)
        wb = openpyxl.load_workbook(path)
        try:
            ws = wb['Beats']
            cell = _text_cell(ws)
            assert LONG_TEXT_MARK in str(cell.value)
            cell.value = 'USER EDIT ' + str(cell.value)   # 用户直接在预览格里改
            wb.save(path)
        finally:
            wb.close()

        with pytest.raises(LongTextPreviewEdited) as err:
            import_beats_from_excel(path)
        msg = str(err.value)
        assert 'ch01.0001' in msg
        assert '台词' in msg
        assert '恢复' in msg and '标记' in msg, msg
        assert err.value.conflicts[0]['length'] == len(LONG)

    def test_removing_the_mark_is_an_explicit_choice(self, tmp_path):
        """
        用户删掉标记 = 明确"我只要这段新写的短文本"。
        这时**不报错**（不是静默：标记就是他删的），也不能把 40000 字塞回去。
        """
        path = _legacy_sheet(tmp_path)
        wb = openpyxl.load_workbook(path)
        try:
            _text_cell(wb['Beats']).value = '重写的一句话'
            wb.save(path)
        finally:
            wb.close()
        back = import_beats_from_excel(path)
        assert back[0].text == '重写的一句话'

    def test_candidate_refresh_does_not_die_on_a_preview_conflict(self, tmp_path):
        """
        刷新候选表/舞台快照是**派生视图**：它不改内容，不该因为一张预览
        冲突就整次失败（`strict_long_text=False`）。内容导入仍然严格。
        """
        from storygraph.story import excel_export

        path = _legacy_sheet(tmp_path)
        wb = openpyxl.load_workbook(path)
        try:
            ws = wb['Beats']
            _text_cell(ws).value = 'USER EDIT ' + str(_text_cell(ws).value)
            wb.save(path)
        finally:
            wb.close()

        wb2 = openpyxl.load_workbook(path)
        try:
            ws2 = wb2['Beats']
            long_text = excel_export._read_long_text(wb2)
            with pytest.raises(LongTextPreviewEdited):
                excel_export._read_beats_from_ws(ws2, long_text=long_text)
            got = excel_export._read_beats_from_ws(
                ws2, long_text=long_text, strict_long_text=False)
            assert got and got[0].text.startswith('USER EDIT')
        finally:
            wb2.close()


class TestAfterReadingItTheGateApplies:
    def test_re_exporting_the_restored_40k_text_is_refused(self, tmp_path):
        """读得回来 ≠ 还能写回去：那句该拆成几拍（写侧 32767 硬闸门）。"""
        path = _legacy_sheet(tmp_path)
        beats = import_beats_from_excel(path)
        assert len(beats[0].text) == len(LONG)
        with pytest.raises(CellTextTooLong) as err:
            export_beats_to_excel(beats, tmp_path / 'again.xlsx',
                                  include_dropdowns=False)
        assert '40000' in str(err.value)
        assert not (tmp_path / 'again.xlsx').exists(), (
            '报错了还留下半个表 —— 那等于"看起来成功了"')
