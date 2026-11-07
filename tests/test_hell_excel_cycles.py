# -*- coding: utf-8 -*-
"""
Excel 那几条路的**地狱测试**（2026-11-01，第二轮审查收尾）
=========================================================

`test_p0_round6_xlsx_preserve.py` 证明的是"重建一次，人的东西还在"。这个文件问
的是**第二十次之后**：

  · 同一章 `rpy → xlsx → rpy` 连做 25 遍，第 25 遍的字节还和第 1 遍**完全一样**吗？
    （往返收敛 = 不震荡；不收敛的工具会越导越歪，用户第一周看不出来）
  · 在表里改一格、导回源、再连导 20 遍 —— 改动**只落那一行**，之后不再漂；
  · 重建 20 次，Beats 的列会不会一代比一代多（用户列被当成工具列再写一遍）？
  · 用户自己加的页 / 列 / 批注 / 底色，是**每一次**都在，还是只有第一次在？
  · 备份会不会无限长大（每导一次多一份 xlsx）？
  · 资源映射（`storygraph.mapping.xlsx`）写→读→写 10 轮，结构会不会漂？
  · 病态工作簿：BOM+CRLF、公式样文本、超长文本、控制字符、2000 行规模。

判据一律是**性质**（幂等 / 逐字节 / 有界），不是快照。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip('openpyxl')
import openpyxl                                          # noqa: E402
from openpyxl.comments import Comment                     # noqa: E402
from openpyxl.styles import PatternFill                   # noqa: E402

from storygraph.core.vocabulary import GENERATOR_MARKER  # noqa: E402
from storygraph.pipeline.compile import compile_table     # noqa: E402
from storygraph.story.script_table import open_rpy_as_table  # noqa: E402

#: 一章"什么都有"的剧本：注释 / define / default / init python / transform /
#: scene+with / show+at / 台词 / if / menu / jump / return。
TRICKY = '''# 头注释：这一行不该动
define e = Character("Eileen")
default flag = False

init python:
    persistent.x = 1

transform left:
    xalign 0.0

label ch01:
    scene bg room with dissolve
    show eileen happy at left
    e "第一句"
    if flag:
        e "第二句"
    menu:
        "去左边":
            jump left_1
    return
'''


def _sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _make_chapter(tmp_path: Path, text: str = TRICKY, name: str = 'ch01') -> Path:
    game = tmp_path / 'game'
    game.mkdir(parents=True, exist_ok=True)
    ch = game / f'{name}.rpy'
    ch.write_bytes(text.encode('utf-8'))
    return ch


def _cycle(ch: Path) -> tuple:
    """跑一轮 `rpy → xlsx → rpy`，返回 `(CompileResult, 现在的字节)`。"""
    xlsx = ch.with_suffix('.xlsx')
    open_rpy_as_table(ch, xlsx)
    _rpy, _sheet, res = compile_table(xlsx, rpy_path=ch, source_path=ch,
                                      write_sheet=False, engine_lint=False)
    return res, ch.read_bytes()


# ---------------------------------------------------------------------------
#  一、往返收敛：连做 25 遍，第 25 遍 == 第 1 遍 == 原文
# ---------------------------------------------------------------------------

class TestRoundTripConverges:
    def test_twenty_five_cycles_are_byte_identical(self, tmp_path):
        """每一次都得逐字节相同 —— 第 2 遍就开始漂，说明增量补丁在"自己改自己"。"""
        ch = _make_chapter(tmp_path)
        original = ch.read_bytes()
        first = None
        for i in range(25):
            res, now = _cycle(ch)
            assert res.written, f'第 {i + 1} 轮没落盘：{res.guard_issues}'
            assert res.used_patch, f'第 {i + 1} 轮退回了全量生成：{res.patch_note}'
            assert res.changed_lines == 0, f'第 {i + 1} 轮自己改了自己 {res.changed_lines} 行'
            if first is None:
                first = now
            assert now == first, f'第 {i + 1} 轮开始漂（sha {_sha(now)} != {_sha(first)}）'
        assert first == original, '连导 25 遍之后和原文不一样了'

    def test_bom_crlf_chapter_survives_cycles(self, tmp_path):
        """BOM + CRLF + 末尾没换行：往返不许把编码或换行"顺手规范一下"。"""
        raw = ('label ch01:\r\n    e "第一句"\r\n    return').encode('utf-8')
        ch = _make_chapter(tmp_path)
        ch.write_bytes(b'\xef\xbb\xbf' + raw)
        original = ch.read_bytes()
        first = None
        for i in range(8):
            res, now = _cycle(ch)
            assert now.startswith(b'\xef\xbb\xbf'), f'第 {i + 1} 轮把 BOM 弄丢了'
            if first is None:
                first = now
            assert now == first, f'第 {i + 1} 轮开始漂'
        assert first == original, 'BOM/CRLF 章节往返之后字节变了'

    def test_scale_two_thousand_lines_stay_exact(self, tmp_path):
        """2000 句台词：一轮往返之后逐字节相同，且**没有**被拍平成一行。"""
        body = '# 长章\nlabel ch_big:\n' + ''.join(
            f'    e "第 {i} 句"\n' for i in range(2000)) + '    return\n'
        ch = _make_chapter(tmp_path, body, name='ch_big')
        original = ch.read_bytes()
        res, now = _cycle(ch)
        assert res.written
        assert now == original, f'2000 行的章节往返后变了：{len(now)} != {len(original)}'
        assert now.count('第 '.encode('utf-8')) == 2000


# ---------------------------------------------------------------------------
#  二、改一格 → 只落那一行 → 之后不再漂
# ---------------------------------------------------------------------------

def _set_first_dialogue(xlsx: Path, old: str, new: str) -> bool:
    """在 Beats 表里把「台词」列值为 `old` 的那一格改成 `new`。"""
    wb = openpyxl.load_workbook(xlsx)
    try:
        ws = wb['Beats']
        headers = [str(c.value or '') for c in ws[1]]
        col = headers.index('台词') + 1
        for row in range(2, ws.max_row + 1):
            cell = ws.cell(row=row, column=col)
            if str(cell.value or '') == old:
                cell.value = new
                wb.save(xlsx)
                return True
        return False
    finally:
        wb.close()


class TestOneCellEdit:
    def test_the_edit_lands_on_one_line_and_then_stops_moving(self, tmp_path):
        ch = _make_chapter(tmp_path)
        original = ch.read_bytes()
        _res, before = _cycle(ch)
        assert before == original

        xlsx = ch.with_suffix('.xlsx')
        assert _set_first_dialogue(xlsx, '第一句', '第一句（改过）')
        _rpy, _sheet, res = compile_table(xlsx, rpy_path=ch, source_path=ch,
                                          write_sheet=False, engine_lint=False)
        assert res.written
        assert res.changed_lines == 1, f'改了 1 格却动了 {res.changed_lines} 行'
        after_edit = ch.read_bytes()
        body = after_edit
        assert '第一句（改过）'.encode() in after_edit
        # 第一次**真正写盘**会补两行生成标记（契约 §5.3）：这是**说出来的**，
        # 不是悄悄加的 —— `marker_added` 必须为真，而且加的就是那两行。
        if res.marker_added:
            assert body.startswith(GENERATOR_MARKER.encode('utf-8')), \
                'marker_added 说是加了标记，可首行不是那句'
            head, _sep, rest = body.partition(b'\n\n')
            assert head.decode('utf-8') == GENERATOR_MARKER
            body = rest
        # 逐行比（去掉标记之后）：只有那一行不同
        old_lines = before.decode('utf-8').split('\n')
        new_lines = body.decode('utf-8').split('\n')
        assert len(old_lines) == len(new_lines), '改一格却让行数变了'
        diff = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
        assert len(diff) == 1, f'动了 {len(diff)} 行：{diff}'

        # 之后再连导 15 遍：**逐字节**稳定（标记留下来就不再动），改动还在
        for i in range(15):
            _res2, now = _cycle(ch)
            assert now == after_edit, f'改完之后第 {i + 1} 轮又漂了'
            assert '第一句（改过）'.encode() in now


# ---------------------------------------------------------------------------
#  三、重建 20 次：列不许长、用户内容每次都在、备份有界
# ---------------------------------------------------------------------------

def _add_user_stuff(xlsx: Path) -> None:
    wb = openpyxl.load_workbook(xlsx)
    try:
        ws = wb['Beats']
        col = ws.max_column + 1
        ws.cell(row=1, column=col, value='配音状态')
        ws.cell(row=2, column=col, value='已录')
        ws.cell(row=2, column=1).fill = PatternFill('solid', fgColor='FFF2CC')
        ws.cell(row=2, column=1).comment = Comment('这句要重录', '策划')
        sheet = wb.create_sheet('角色设定')
        sheet['A1'] = '小雨'
        wb.save(xlsx)
    finally:
        wb.close()


class TestRebuildStaysStable:
    def test_headers_do_not_accumulate_over_twenty_rebuilds(self, tmp_path):
        ch = _make_chapter(tmp_path)
        xlsx = ch.with_suffix('.xlsx')
        open_rpy_as_table(ch, xlsx)
        headers = None
        for i in range(20):
            open_rpy_as_table(ch, xlsx)
            wb = openpyxl.load_workbook(xlsx)
            try:
                now = [str(c.value or '') for c in wb['Beats'][1]]
                if headers is None:
                    headers = now
                assert now == headers, f'第 {i + 2} 次重建后表头变了（列在长？）'
                assert len(now) == len(set(now)), f'第 {i + 2} 次重建出现重名列：{now}'
            finally:
                wb.close()

    def test_user_content_is_there_every_time_not_just_the_first(self, tmp_path):
        ch = _make_chapter(tmp_path)
        xlsx = ch.with_suffix('.xlsx')
        open_rpy_as_table(ch, xlsx)
        _add_user_stuff(xlsx)
        for i in range(12):
            open_rpy_as_table(ch, xlsx)
            wb = openpyxl.load_workbook(xlsx)
            try:
                assert '角色设定' in wb.sheetnames, f'第 {i + 2} 次重建把用户页丢了'
                assert wb['角色设定']['A1'].value == '小雨'
                ws = wb['Beats']
                names = [str(c.value or '') for c in ws[1]]
                assert names.count('配音状态') == 1, f'第 {i + 2} 次重建用户列没了/重了'
                col = names.index('配音状态') + 1
                assert ws.cell(row=2, column=col).value == '已录'
                assert ws.cell(row=2, column=1).comment is not None, '批注丢了'
                assert str(ws.cell(row=2, column=1).fill.fgColor.rgb) == '00FFF2CC'
            finally:
                wb.close()

    def test_backups_stay_bounded_and_readable(self, tmp_path):
        ch = _make_chapter(tmp_path)
        xlsx = ch.with_suffix('.xlsx')
        open_rpy_as_table(ch, xlsx)
        for _ in range(15):
            open_rpy_as_table(ch, xlsx)
        baks = sorted(xlsx.parent.glob('ch01.bak*.xlsx'))
        assert baks, '重建 15 次一次备份都没有'
        assert len(baks) <= 6, f'备份无限长大：{[b.name for b in baks]}'
        for b in baks:
            wb = openpyxl.load_workbook(b)          # 每一份都得能打开
            try:
                assert 'Beats' in wb.sheetnames, f'备份 {b.name} 打不开'
            finally:
                wb.close()


# ---------------------------------------------------------------------------
#  四、资源映射工作簿：写→读→写 10 轮不漂
# ---------------------------------------------------------------------------

MAPPING_DATA = {
    'schema': 'storygraph.mapping/1',
    'name': '地狱映射',
    'characters': {
        'lan': {'display': '岚', 'aliases': ['小岚'],
                'avatar': 'game/images/char/lan.png'},
        'ye': {'display': '叶'},
    },
    'rules': [
        {'glob': 'images/char/<tag>/<stem>.png', 'kind': 'sprite',
         'char': '<tag>', 'showable': True},
    ],
    'images': {
        'bg room': {'kind': 'bg', 'attr': 'room', 'showable': True,
                    'display': '房间'},
    },
    'outfit_codes': [
        {'char': 'lan', 'code': 'A', 'attr': 'aa', 'label': '普通'},
        {'char': 'lan', 'code': 'B', 'attr': 'base_hand_up', 'label': '举手'},
    ],
    'expression_codes': [
        {'char': 'lan', 'no': '7', 'attr': 'indifferent', 'label': '不在意'},
        {'char': 'lan', 'no': '2', 'attr': 'close_happy', 'label': '闭眼微笑'},
    ],
    'vocabulary': [{'kind': 'position', 'id': 'left', 'label': '左'}],
    'attributes': {'mood': '情绪'},
}


class TestMappingWorkbookCycles:
    def test_ten_write_read_cycles_are_idempotent(self, tmp_path):
        from storygraph.assets.mapping_excel import read_workbook, write_workbook

        xlsx = tmp_path / 'storygraph.mapping.xlsx'
        write_workbook(xlsx, MAPPING_DATA)
        first, warnings = read_workbook(xlsx)
        assert warnings == [], warnings

        for i in range(10):
            write_workbook(xlsx, first)
            back, warns = read_workbook(xlsx)
            assert warns == [], f'第 {i + 2} 轮警告变了：{warns}'
            assert back == first, f'第 {i + 2} 轮结构漂了'

        # 关键内容真的在（不是"两边都空所以相等"）
        assert first['characters']['lan']['display'] == '岚'
        assert len(first['outfit_codes']) == 2
        assert first['expression_codes'][0]['no'] == '7'

    def test_mapping_write_refuses_a_script_path(self, tmp_path):
        """写映射也走同一条后缀闸：传 .rpy 必须拒绝且不动文件。"""
        from storygraph.assets.asset_mapping import MappingError
        from storygraph.assets.mapping_excel import write_workbook

        script = tmp_path / 'ch01.rpy'
        script.write_bytes(b'label ch01:\n    return\n')
        with pytest.raises(MappingError):
            write_workbook(script, dict(MAPPING_DATA))
        assert script.read_bytes() == b'label ch01:\n    return\n'

    def test_unknown_column_and_missing_code_are_reported_not_silent(self, tmp_path):
        from storygraph.assets.mapping_excel import read_workbook, write_workbook

        xlsx = tmp_path / 'm.xlsx'
        write_workbook(xlsx, MAPPING_DATA)
        wb = openpyxl.load_workbook(xlsx)
        try:
            ws = wb['服装代号']
            ws.cell(row=1, column=ws.max_column + 1, value='美术说')
            ws.cell(row=2, column=ws.max_column, value='待定')
            # 再插一行"没填代号"的：必须被报出来，而不是安静地吞掉
            ws.cell(row=ws.max_row + 1, column=1, value='ye')
        finally:
            wb.save(xlsx)
        data, warnings = read_workbook(xlsx)
        assert any('不认识' in w for w in warnings), warnings
        assert any('没填' in w for w in warnings), warnings


# ---------------------------------------------------------------------------
#  五、病态工作簿：公式样文本、超长、控制字符
# ---------------------------------------------------------------------------

class TestPathologicalCells:
    def test_text_that_looks_like_a_formula_stays_text(self, tmp_path):
        """`=TODO` / `=SUM(A1:A2)` 必须是字符串，不能被 Excel 当公式。"""
        from storygraph.core import atomicio
        from storygraph.excel.cells import set_text

        xlsx = tmp_path / 'f.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        for i, value in enumerate(['=TODO', '=SUM(A1:A2)', '+1+1', '-1', '@echo'],
                                  start=1):
            set_text(ws, i, 1, value)
        atomicio.save_workbook_atomic(wb, xlsx)

        wb2 = openpyxl.load_workbook(xlsx)
        try:
            ws2 = wb2.active
            for i, value in enumerate(['=TODO', '=SUM(A1:A2)', '+1+1', '-1',
                                       '@echo'], start=1):
                cell = ws2.cell(row=i, column=1)
                assert cell.data_type != 'f', f'{value!r} 被写成了公式'
                assert str(cell.value) == value
        finally:
            wb2.close()

    def test_over_limit_text_is_refused_not_truncated(self):
        """32767 上限：超了要抛，不许静默截断（截断就是改用户台词）。"""
        from storygraph.core.cell_limits import CellTextTooLong
        from storygraph.excel.cells import set_text

        wb = openpyxl.Workbook()
        with pytest.raises(CellTextTooLong):
            set_text(wb.active, 1, 1, 'x' * 40000)
        assert wb.active['A1'].value is None, '拒绝之后还写进去了半截'

    def test_illegal_control_chars_are_escaped_and_recorded(self):
        """xlsx 装不下的控制字符：**写成 `\\xNN` 转义**（否则整张表都写不出），
        并且留痕。

        ⚠ 2026-11-02 判据改过：从前是"剜掉"，但那让 `.rpy → 表 → .rpy`
        一趟就**永久少字符**（真机 4 章 9 处 `U+0005`）。改成转义之后，
        读表时 `xml_unescape_row` 会还原成真字符 —— 逐字节保真。
        见 `tests/test_excel_illegal_chars.py` 的往返用例。
        """
        from storygraph.core import safeguard
        from storygraph.excel.cells import set_text

        safeguard.clear()
        wb = openpyxl.Workbook()
        set_text(wb.active, 1, 1, '台词\x05里有\x07脏字符')
        assert wb.active['A1'].value == '台词\\x05里有\\x07脏字符'
        assert any('U+0005' in (r['samples'][0] if r['samples'] else '')
                   for r in safeguard.records() if isinstance(r, dict)) or \
            safeguard.total() >= 1, safeguard.report()
