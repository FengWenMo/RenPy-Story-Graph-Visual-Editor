"""
Excel 装不下的控制字符：**不炸整张表、也不改一个字**（2026-10-28 → 2026-11-02 改判据）
==============================================================================

真机现场（用户项目 `chapter3_3_3.rpy` 第 79 行）——台词里混进两个 `U+0005`
（不可见），导出剧本表时 `openpyxl` 直接抛：

    openpyxl.utils.exceptions.IllegalCharacterError:
    “成为机器是一种怎样的感受？” cannot be used in worksheets.

后果不是"这一格丢了"，而是**整份表一行都导不出来**：五万行里的一格把
整个项目卡死，用户看到的还是一段 openpyxl 的英文 traceback。

⚠ **2026-11-02 判据改了：从"剥掉"改成"写成 `\\xNN` 转义"**
------------------------------------------------------------
剥掉能救"表导不出来"，但它把内容**永久**改了。真机 `sr_general` 实测：
96 个章节里有 **4 个 / 9 处**含 `U+0005`，`.rpy → 表 → .rpy` 一趟回来
那 9 个字符就没了（`tools/fidelity_report.py` 报"内容不同"），而用户只会在
`safeguard` 日志里看到一行。这与"没改的行一个字节都不动"直接冲突。

转义是**语义无损**的：Ren'Py 的字符串字面量本来就认 `\\x05`，
引擎读出来与原来的控制字符**完全相同**；读表时 `xml_unescape` 还原成真字符，
于是 `原文 == 产物` 逐字节成立。

这个文件守五件事：

  1. 只动 **xlsx/XML 1.0 明令禁止**的那批（`U+0000~08` / `0B` / `0C` /
     `0E~1F`）；`\\t` `\\n` `\\r` 是**合法字符**，必须原样留着
     （它们是"多行台词 / 缩进"的载体，动一下就毁内容）；
  2. 转义后的文本照样写得进去（32767 的长度闸门仍然生效）；
  3. **每转义一次都留痕** —— `safeguard` 会记下 `Beats!R61C7` 这种坐标，
     诊断面板的「静默失败」里看得见；
  4. **往返必须逐字节还原**（`xml_unescape(xml_escape(x)) == x`）——
     这是"表只是中转站"这个架构的底线；
  5. 非字符串（数字 / `None`）不参与这件事。

⚠ 与 `test_cell_control_chars.py` 的分工：那份管"单行列里的换行把产物
拆断"（`sanitize_single_line_cell` + W114），这份管"Excel 装不下 → 整张
表导不出来"。两者都是控制字符，坏在不同地方。
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.core.safeguard import report_since, snapshot     # noqa: E402
from storygraph.excel.cells import (set_text, xml_escape,         # noqa: E402
                                    xml_safe, xml_unescape)
from storygraph.story.beat_model import Beat                     # noqa: E402
from storygraph.story.excel_export import export_beats_to_excel  # noqa: E402
from storygraph.story.excel_import import import_beats_from_excel  # noqa: E402

#: xlsx 规范（XML 1.0）禁止的控制字符，全在 `xml_escape` 的处理范围里。
FORBIDDEN = ['\x00', '\x01', '\x05', '\x08', '\x0b', '\x0c', '\x0e', '\x1f']
#: 合法：制表 / 换行 / 回车 —— 动它们就等于毁掉多行台词与缩进。
LEGAL = ['\t', '\n', '\r']


def _one(text, lid='ch01.0001'):
    return [Beat(seq=1, label='ch01', kind='say', speaker='e',
                 text=text, line_id=lid)]


def _back_beats(path):
    back = import_beats_from_excel(path)
    return back[0] if isinstance(back, tuple) else back


class TestWhatCountsAsIllegal:
    @pytest.mark.parametrize('ch', FORBIDDEN)
    def test_forbidden_chars_become_visible_escapes(self, ch):
        """不再是"剥掉"，而是写成看得见的 `\\xNN`。"""
        assert xml_escape(f'a{ch}b') == f'a\\x{ord(ch):02x}b'

    @pytest.mark.parametrize('ch', FORBIDDEN)
    def test_escape_then_unescape_is_lossless(self, ch):
        text = f'前{ch}后'
        assert xml_unescape(xml_escape(text)) == text

    @pytest.mark.parametrize('ch', LEGAL)
    def test_tab_newline_cr_survive(self, ch):
        assert xml_escape(f'a{ch}b') == f'a{ch}b'

    def test_clean_text_is_returned_unchanged(self):
        text = '“成为机器是一种怎样的感受？”'
        assert xml_escape(text) is text        # 原对象，不做无谓拷贝

    def test_non_strings_pass_through(self):
        assert xml_escape(None) is None
        assert xml_escape(12345) == 12345
        assert xml_unescape(None) is None
        assert xml_unescape(12345) == 12345

    def test_legal_escapes_are_not_decoded_back(self):
        """`\\x41` 是可见字符 `A`，不在"装不下"那一段 —— 不猜，原样留着。"""
        assert xml_unescape('\\x41') == '\\x41'
        assert xml_unescape('\\t') == '\\t'    # 制表符本来就合法，不该被还原成 tab

    def test_old_name_is_the_same_function(self):
        """`xml_safe` 语义已从"剥掉"变成"转义"，老调用点不用改。"""
        assert xml_safe is xml_escape


class TestWritingNeverExplodes:
    def test_set_text_escapes_and_still_writes(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Beats'
        set_text(ws, 1, 1, 'a\x05b')
        assert ws.cell(row=1, column=1).value == 'a\\x05b'

    def test_escaping_is_recorded_with_the_cell(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Beats'
        before = snapshot()
        set_text(ws, 61, 7, 'a\x05b')
        msg = report_since(before)
        assert 'Beats!R61C7' in msg, msg       # 点名到"哪张表哪一格"
        assert 'U+0005' in msg, msg            # 还说是哪个字符

    def test_legal_multiline_text_is_not_recorded(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        before = snapshot()
        set_text(ws, 1, 1, '第一行\n第二行\t缩进')
        assert report_since(before) == ''


class TestEndToEnd:
    def test_export_and_import_a_chapter_with_a_control_char(self, tmp_path):
        """
        整条链路不能因为一个不可见字符断掉，**也不能把它吃掉**：

            beats → xlsx（这里从前抛 IllegalCharacterError）→ 读回来（逐字节相同）
        """
        text = '“成为机器是一种怎样的感受？”\x05'
        out = tmp_path / 'x.xlsx'
        export_beats_to_excel(_one(text), out)
        assert out.is_file()

        wb = openpyxl.load_workbook(out)
        cells = [c.value for c in wb['Beats'][2]]
        # 表里是**看得见的转义**，而不是消失
        assert '“成为机器是一种怎样的感受？”\\x05' in cells, cells

        back = _back_beats(out)
        assert len(back) == 1
        assert back[0].text == text            # ← 2026-11-02：从前这里是丢字符的

    def test_legal_chars_round_trip_unchanged(self, tmp_path):
        """别动过头：`\\n` / `\\t` 必须原样回来（多行台词靠它们）。"""
        out = tmp_path / 'y.xlsx'
        text = '第一行\n第二行\t缩进'
        export_beats_to_excel(_one(text), out)
        assert _back_beats(out)[0].text == text

    def test_literal_backslash_x_in_dialogue_is_not_mangled(self, tmp_path):
        """
        用户台词里**真的写着** `\\x41` 时，读回来还是 `\\x41`（不是 `A`），
        而控制字符那条路仍然还原 —— 两条路不互相污染。
        """
        text = '路径是 C:\\x41 不是 A\x05'
        out = tmp_path / 'z.xlsx'
        export_beats_to_excel(_one(text), out)
        assert _back_beats(out)[0].text == text
