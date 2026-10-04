"""
C：**行尾注释进表**（隐藏列 `Inline`）—— 见不变量 78。

病根与"嵌套"那条**不是同一个**：`RenPyLexer._strip_inline_comment` 在**词法器**
就把注释扔了（IR 里根本没有这个字段），所以下游怎么改都没用 ——
不是"表里没地方放"，是"源头没留下"。

这一版把它留下来、随表走、渲染时贴回**原来那一行**。规矩是"**说不清就不贴**"：

  · 多行逻辑语句（括号续行 / 反斜杠续行）不采集 —— 注释挂在哪一行说不清；
  · 一拍渲染出多行时不贴 —— 贴到第一行会变成"整个块"的注释；
  · 单元格被清洗过（有换行/控制字符）就丢掉 —— 残缺的注释不如没有。
"""

from __future__ import annotations

from pathlib import Path

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer, RenPyLexer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_model import BEAT_FIELDS, HIDDEN_BEAT_COLUMNS, Beat
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

SRC = '''label a:
    e "顶层"                       # 备注：顶层台词
    menu:
        "选项 A":                  # 备注：菜单选项
            e "选项体"              # 备注：选项体里
    jump other                     # 备注：跳转
    stop                           # 备注：停
'''

REAL_PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')


def _beats(text: str, tmp: Path) -> list:
    f = tmp / 'ch.rpy'
    f.write_text(text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return beats_from_parse_result(pr)


def _note_lines(rpy: str) -> list:
    return [ln.strip() for ln in rpy.splitlines() if '  # ' in ln]


class TestTheLexerKeepsIt:
    def test_single_line_captures_the_raw_text(self):
        assert RenPyLexer._inline_comment('e "x"  # 备注') == '# 备注'
        assert RenPyLexer._inline_comment('e "x"') == ''
        # 字符串里的 `#` 不算注释（走的是引号感知的剥法）
        assert RenPyLexer._inline_comment('e "a # b"') == ''

    def test_multi_line_statement_is_not_captured(self):
        """多行逻辑语句不采集 —— 注释属于哪一行说不清。"""
        assert RenPyLexer._inline_comment('f(\n  a,  # 备注\n)') == ''

    def test_it_does_not_change_the_code_text(self):
        """采集归采集，`text` 照样是**剥掉注释之后**的代码（既有契约不变）。"""
        line = RenPyLexer().tokenize_text('label a:\n    e "x"  # 备注\n')
        assert line[0].subblock[0].text.strip() == 'e "x"'
        assert line[0].subblock[0].inline_comment == '# 备注'


class TestItReachesTheBeat:
    def test_say_jump_option_lines_all_carry_it(self, tmp_path):
        beats = _beats(SRC, tmp_path)
        got = [b.inline_comment for b in beats
               if getattr(b, 'inline_comment', '')]
        assert got, '一个都没采到'
        notes = sorted(got)
        assert '# 备注：顶层台词' in notes
        assert '# 备注：菜单选项' in notes, '选项行自己的注释也要带上'
        assert '# 备注：选项体里' in notes
        assert '# 备注：跳转' in notes

    def test_the_menu_head_line_has_no_note_when_it_has_none(self, tmp_path):
        beats = _beats(SRC, tmp_path)
        head = next(b for b in beats if b.kind == 'menu')
        assert head.inline_comment == ''

    def test_a_statement_without_a_note_stays_empty(self, tmp_path):
        beats = _beats('label a:\n    e "没注释"\n', tmp_path)
        assert beats[0].inline_comment == ''


class TestTheColumn:
    def test_it_is_hidden_and_single_line(self):
        assert 'Inline' in BEAT_FIELDS
        assert 'Inline' in HIDDEN_BEAT_COLUMNS

    def test_roundtrip_through_the_workbook(self, tmp_path):
        beats = _beats(SRC, tmp_path)
        xl = tmp_path / 'ch.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)
        want = [b.inline_comment for b in beats]
        assert [b.inline_comment for b in back] == want

    def test_a_cleaned_cell_is_dropped_not_trusted(self):
        """单元格被清洗过 → 丢掉（`g` 会留痕；残缺的注释不如没有）。"""
        row = dict(zip(BEAT_FIELDS, Beat(seq=1, label='a', text='x').to_row()))
        row['Inline'] = '# 备注\n下一行'
        back = Beat.from_row(row)
        assert back.inline_comment == ''
        assert 'Inline' in (back.sanitized_cells or []), '清洗必须留痕（W114）'


class TestRendering:
    def test_notes_land_on_the_right_lines(self, tmp_path):
        rpy = render_beats_to_rpy(_beats(SRC, tmp_path), label_name='a')
        lines = _note_lines(rpy)
        assert any(ln.startswith('e "顶层"') for ln in lines), rpy
        assert any(ln.startswith('"选项 A":') for ln in lines), rpy
        assert any(ln.startswith('e "选项体"') for ln in lines), rpy
        assert any(ln.startswith('jump other') for ln in lines), rpy

    def test_a_multi_line_beat_gets_no_note(self):
        """一拍渲染出两行 → 不贴（贴到第一行会变成整个块的注释）。"""
        b = Beat(seq=1, label='a', kind='say', speaker='e', text='台词',
                 inline_comment='# 备注')
        b.show = 'ling happy'          # 会让这一拍多渲染出 show 那行
        rpy = render_beats_to_rpy([b], label_name='a')
        assert '# 备注' not in rpy, rpy

    def test_the_table_path_keeps_them(self, tmp_path):
        """**这条才是重点**：全量（表那条路）以前必丢，现在保住。"""
        beats = _beats(SRC, tmp_path)
        xl = tmp_path / 'ch.xlsx'
        export_beats_to_excel(beats, xl)
        rpy = render_beats_to_rpy(import_beats_from_excel(xl))
        assert _note_lines(rpy) == _note_lines(
            render_beats_to_rpy(beats, label_name='a')), rpy

    def test_rendering_twice_does_not_duplicate(self, tmp_path):
        """渲染出来的产物再解析一遍，注释**不许翻倍**。"""
        first = render_beats_to_rpy(_beats(SRC, tmp_path), label_name='a')
        again = _beats(first, tmp_path)
        second = render_beats_to_rpy(again, label_name='a')
        assert _note_lines(second) == _note_lines(first), (first, second)


class TestPatchPath:
    def test_in_place_patch_keeps_each_note_once(self, tmp_path):
        """
        原地增量：**没改的行**由源文件原样保留（注释在），**改过的行**由生成器
        写出（注释也在）—— 两边都不许重复。
        """
        from storygraph.app.editor import EditorSession
        src = tmp_path / 'ch.rpy'
        src.write_text(SRC, encoding='utf-8')
        s = EditorSession(project_root=tmp_path)
        s.load(src)
        target = next(b for b in s.beats if b.text == '顶层')
        target.text = '顶层（改过）'
        res = s.export(src, force=True)
        assert res['ok'], res
        body = src.read_text(encoding='utf-8')
        assert '顶层（改过）' in body
        for note in ('# 备注：顶层台词', '# 备注：菜单选项', '# 备注：选项体里',
                     '# 备注：跳转'):
            assert body.count(note) == 1, f'{note} 出现 {body.count(note)} 次：\n{body}'


class TestRealProject:
    def test_the_multi_line_case_is_the_only_remaining_gap(self):
        """
        真实项目：line 里的行尾注释**大多**保住了；剩下的那 1 处是
        **多行 `python:` 块体里**的注释（说不清挂哪一行 → 刻意不采集）。
        """
        files = sorted(f for f in (REAL_PROJECT / 'game').rglob('*.rpy')
                       if not f.name.endswith('.generated.rpy'))
        kept = total = 0
        for f in files:
            pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
            beats = beats_from_parse_result(pr)
            notes = [b.inline_comment for b in beats
                     if getattr(b, 'inline_comment', '')]
            if not notes:
                continue
            out = render_beats_to_rpy(beats)
            total += len(notes)
            kept += sum(1 for n in notes if n in out)
        if not total:
            return                      # 本机没有真实项目
        assert kept == total, f'{total - kept}/{total} 条注释没贴上'
