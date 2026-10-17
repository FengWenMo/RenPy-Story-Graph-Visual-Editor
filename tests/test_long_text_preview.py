"""
超长文本预览格：编辑冲突必须**停下来**（调研 P1-3）
==================================================

未提交的超长文本方案是：主表格子写「200 字预览 + ⟪全文见「长文本保真」表⟫」，
全文放隐藏表。导入侧 `_restore_long_text()` 从前**只要看到标记就用全文覆盖**：

    用户把预览格改成 "USER EDIT …⟪全文见…⟫"
    导入后：40000 字全文赢，'USER EDIT' 一个字不剩（静默吃掉编辑）

反过来直接用改过的预览也不行：全文会在下次导出时被**静默截成 200 字**。
两条路都是静默改内容，所以现在检测"预览与全文前缀对不上"并抛
`LongTextPreviewEdited`，让人二选一（恢复预览 / 删掉标记）。

这个文件守：正常往返仍然逐字保真；改过预览 → 报错且说清是哪行哪格；
删掉标记 = 明确放弃全文，不再报错（那条路是用户的选择，不是静默）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story.beat_model import (Beat, LONG_TEXT_MARK,     # noqa: E402
                                         LongTextPreviewEdited)
from storygraph.story.excel_export import export_beats_to_excel     # noqa: E402
from storygraph.story.excel_import import import_beats_from_excel   # noqa: E402
from storygraph.story.script_table import BEAT_FIELDS               # noqa: E402

LONG = '甲' * 40000


def _export(tmp_path, text=LONG):
    beats = [Beat(seq=1, label='ch01', kind='say', speaker='e', text=text,
                  line_id='ch01.0001')]
    path = tmp_path / 't.xlsx'
    export_beats_to_excel(beats, path)
    return path


def _text_cell(ws):
    return ws.cell(row=2, column=BEAT_FIELDS.index('Text') + 1)


class TestLongTextPreviewIsProtected:

    def test_normal_roundtrip_is_byte_exact(self, tmp_path):
        path = _export(tmp_path)
        back = import_beats_from_excel(path)
        assert back[0].text == LONG, '没改过的表也必须逐字还原全文'

    def test_editing_the_preview_raises_instead_of_silently_losing(self, tmp_path):
        path = _export(tmp_path)
        wb = openpyxl.load_workbook(path)
        ws = wb['Beats']
        cell = _text_cell(ws)
        assert LONG_TEXT_MARK in str(cell.value)
        cell.value = 'USER EDIT ' + str(cell.value)   # 用户直接在预览格里改
        wb.save(path)

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
        path = _export(tmp_path)
        wb = openpyxl.load_workbook(path)
        ws = wb['Beats']
        _text_cell(ws).value = '重写的一句话'
        wb.save(path)
        back = import_beats_from_excel(path)
        assert back[0].text == '重写的一句话'

    def test_candidate_refresh_does_not_die_on_a_preview_conflict(self, tmp_path,
                                                                  monkeypatch):
        """
        刷新候选表/舞台快照是**派生视图**：它不改内容，不该因为一张预览
        冲突就整次失败（`strict_long_text=False`）。内容导入仍然严格。
        """
        from storygraph.story import excel_export

        path = _export(tmp_path)
        wb = openpyxl.load_workbook(path)
        ws = wb['Beats']
        _text_cell(ws).value = 'USER EDIT ' + str(_text_cell(ws).value)
        wb.save(path)

        # 直接验那个内部开关：严格模式抛，宽松模式不抛
        wb2 = openpyxl.load_workbook(path)
        ws2 = wb2['Beats']
        from storygraph.story.excel_export import _read_long_text
        long_text = _read_long_text(wb2)
        with pytest.raises(LongTextPreviewEdited):
            excel_export._read_beats_from_ws(ws2, long_text=long_text)
        got = excel_export._read_beats_from_ws(
            ws2, long_text=long_text, strict_long_text=False)
        assert got and got[0].text.startswith('USER EDIT')
