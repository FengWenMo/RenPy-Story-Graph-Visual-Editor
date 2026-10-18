"""
C：**行尾注释进表**（隐藏列 `Inline`）—— 见不变量 78。

病根与"嵌套"那条**不是同一个**：`RenPyLexer._strip_inline_comment` 在**词法器**
就把注释扔了（IR 里根本没有这个字段），所以下游怎么改都没用 ——
不是"表里没地方放"，是"源头没留下"。

这一版把它留下来、随表走、渲染时贴回**原来那一行**。规矩是"**说不清就不贴**"：

  · 多行逻辑语句（括号续行 / 反斜杠续行）不采集 —— 注释挂在哪一行说不清；
  · 一拍渲染出多行时不贴 —— 贴到第一行会变成"整个块"的注释；
  · 单元格被清洗过（有换行/控制字符）就丢掉 —— 残缺的注释不如没有。

**收尾第 2 批**（2026-10-05，用户："这几个剩下的一并修复了吧"）又补了三处：

  · `python:` **块体**里逐行的注释（块体是拿 `.text` 拼的，原来整块丢）；
  · **括号续行**里的注释（`positions = [` … `]` 会被并成一条逻辑行，
    所以 `LogicalLine.inline_comments` 是**逐物理行**的一张表）；
  · **`if / elif / else` 头那一行**的注释（`if` 不是拍，它是拍上的条件 ——
    存在 `Beat.cond_comment`，贴到这一拍渲染出来的 `if …:` 那一行）。
"""

from __future__ import annotations

from pathlib import Path

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer, RenPyLexer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_model import BEAT_FIELDS, HIDDEN_BEAT_COLUMNS, Beat
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

#: ⚠ 最后一行是 `stop music`，不是裸 `stop`（2026-10-18）：`stop` **必须**带
#: 声道，裸写是 Ren'Py 语法错 —— 引擎闸门 fail-closed 之后这份样本当场被拦
#: （`stop requires a channel`）。从前它是"闸门放行 + 产物跑不起来"，
#: 而这条用例只想测注释，不该顺带携带一个坏语法。
SRC = '''label a:
    e "顶层"                       # 备注：顶层台词
    menu:
        "选项 A":                  # 备注：菜单选项
            e "选项体"              # 备注：选项体里
    jump other                     # 备注：跳转
    stop music                     # 备注：停
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
        """
        一拍渲染出两行 → **不贴到任何一行**（贴错比不贴更坏）。

        ⚠ 2026-10-18 改口径：不贴 ≠ 丢掉。多行拍贴不上时，注释**单独成行**
        放在这一拍最前面 —— 位置从行尾挪到头顶，字一个不少。
        （从前这里是 `assert '# 备注' not in rpy`，等于把"丢字"钉成了契约。）
        """
        b = Beat(seq=1, label='a', kind='say', speaker='e', text='台词',
                 inline_comment='# 备注')
        b.show = 'ling happy'          # 会让这一拍多渲染出 show 那行
        rpy = render_beats_to_rpy([b], label_name='a')
        assert '# 备注' in rpy, rpy
        assert 'show ling happy  # 备注' not in rpy, '贴错了行'
        assert '"台词"  # 备注' not in rpy, '贴错了行'
        # 独立一行，且在 show 之前
        assert rpy.index('# 备注') < rpy.index('show ling happy'), rpy

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


BLOCK_SRC = '''label a:
    python:
        sequence_lengths = [5, 7]  # 每轮序列长度
        debug = True  # 用于跳过暂停
'''

BRACKET_SRC = '''label a:
    python:
        positions = [
            (100, 100),  # 第一行
            (200, 200)   # 第二行
        ]
'''

BACKSLASH_SRC = '''label a:
    python:
        total = 1 + \\
            2  # 反斜杠续行里的注释
'''

IF_SRC = '''label a:
    if is_title:            # 备注：主标题
        e "标题"
    elif is_sub:            # 备注：副标题
        e "副标题"
    else:                   # 备注：普通文本
        e "正文"
'''


class TestBlockBodies:
    """`python:` 块体里**逐行**的注释（收尾第 2 批）。"""

    def test_line_notes_survive(self, tmp_path):
        rpy = render_beats_to_rpy(_beats(BLOCK_SRC, tmp_path), label_name='a')
        assert '# 每轮序列长度' in rpy, rpy
        assert '# 用于跳过暂停' in rpy, rpy

    def test_bracketed_continuation_notes_survive(self, tmp_path):
        """括号续行会被并成一条逻辑行 —— 注释按**物理行**对齐贴回去。"""
        rpy = render_beats_to_rpy(_beats(BRACKET_SRC, tmp_path), label_name='a')
        assert '# 第一行' in rpy, rpy
        assert '# 第二行' in rpy, rpy
        first = next(ln for ln in rpy.splitlines() if '第一行' in ln)
        assert '(100, 100)' in first, first

    def test_backslash_continuation_keeps_its_single_note(self, tmp_path):
        """
        反斜杠续行是用**空格**拼成一条逻辑行的 —— 逐行对齐表因此用不上
        （`inline_comments` 刻意留空），但拼出来的那一行**本身就是单行**，
        注释跟着它走，所以那一条注释照样保住。

        真正会丢的只有"一条逻辑行里挂了**多**条注释"：反斜杠拼法做不到
        （拼完只有一行），括号续行才可能（那种走逐行表，见上一条用例）。
        """
        rpy = render_beats_to_rpy(_beats(BACKSLASH_SRC, tmp_path), label_name='a')
        assert '# 反斜杠续行里的注释' in rpy, rpy
        line = next(ln for ln in rpy.splitlines() if '反斜杠续行里的注释' in ln)
        assert line.strip().startswith('$') and '1 +' in line, line


class TestConditionNotes:
    """`if / elif / else` 头那一行的注释：贴到渲染出来的 `if …:` 那一行。"""

    def test_it_lands_on_the_if_lines(self, tmp_path):
        rpy = render_beats_to_rpy(_beats(IF_SRC, tmp_path), label_name='a')
        heads = [ln for ln in rpy.splitlines() if ln.strip().startswith('if ')]
        assert len(heads) == 3, rpy
        assert '# 备注：主标题' in heads[0], heads
        assert '# 备注：副标题' in heads[1], heads
        assert '# 备注：普通文本' in heads[2], heads

    def test_the_table_path_keeps_them(self, tmp_path):
        beats = _beats(IF_SRC, tmp_path)
        xl = tmp_path / 'ch.xlsx'
        export_beats_to_excel(beats, xl)
        assert (render_beats_to_rpy(import_beats_from_excel(xl))
                == render_beats_to_rpy(beats, label_name='a'))

    def test_two_slots_in_one_cell_round_trip(self):
        """同一拍上"自己那行的注释"与"条件行的注释"共用一个单元格，不许串。"""
        from storygraph.story.beat_model import (compose_inline_cell,
                                                 parse_inline_cell)
        b = Beat(seq=1, label='a', text='x',
                 inline_comment='# 自己的', cond_comment='# 条件的')
        own, cond = parse_inline_cell(compose_inline_cell(b))
        assert (own, cond) == ('# 自己的', '# 条件的')
        # 只有条件注释时也要能解回来（前缀是唯一判据）
        b2 = Beat(seq=1, label='a', text='x', cond_comment='# 只有条件的')
        assert parse_inline_cell(compose_inline_cell(b2)) == ('', '# 只有条件的')
        # 只有自己的注释：就是老格式，一个字都不多
        b3 = Beat(seq=1, label='a', text='x', inline_comment='# 只有自己的')
        assert compose_inline_cell(b3) == '# 只有自己的'

    def test_a_lone_separator_in_the_body_is_inert(self):
        """
        正文里**孤立**的分隔符不许把注释劈开（2026-10-05 修，
        `tools/hell_edit_rounds.py` 的 A 轴实测）。

        只在 `SEP + '@if '` 这个组合处切：`compose_inline_cell` 写出来的
        分隔符后面永远紧跟 `@if `，所以组合判据是自反的。U+E004 打不出来，
        但**粘贴**可能带进来 —— 而劈开之后后半会被当成条件注释贴到 `if` 行上
        （错位，还会污染下一格）。
        """
        from storygraph.story.beat_model import (INLINE_COND_SEP,
                                                 compose_inline_cell,
                                                 parse_inline_cell)
        sep = INLINE_COND_SEP
        body = f'# 正文里有分隔符{sep}后面还有字'
        b = Beat(seq=1, label='a', text='x', inline_comment=body)
        assert compose_inline_cell(b) == body, '没有条件注释时不该多写东西'
        assert parse_inline_cell(compose_inline_cell(b)) == (body, '')

        # 两个槽都在、且**两段正文都带孤立分隔符**：仍然要各归各位
        b2 = Beat(seq=1, label='a', text='x',
                  inline_comment=f'# 自己的{sep}尾巴',
                  cond_comment=f'# 条件的{sep}尾巴')
        own, cond = parse_inline_cell(compose_inline_cell(b2))
        assert (own, cond) == (f'# 自己的{sep}尾巴', f'# 条件的{sep}尾巴')

        # 手改掉前缀的老单元格：整段当成"自己的注释"，不再造出半个条件注释
        assert parse_inline_cell(f'# 自己的{sep}后面还有字') == \
            (f'# 自己的{sep}后面还有字', '')
