# -*- coding: utf-8 -*-
"""
注释的 `#` 补全（2026-10-28，用户）
==================================
用户原话：

> 在这个舞台注释里面，如果它没有写注释的 `#` 号，那就帮忙补上
> （这个检查一下有没有，没有就加上吧）

先查了现状：

  · **「注释」列 / 卡片备注**：导出 `.rpy` 时渲染层**早就**会逐行补 `# `
    （`render_beat_to_rpy` ①），所以这里不用改；这个文件把它钉住，
    防止以后有人"优化"掉；
  · **「舞台（高级）」列**：以前是**原生直通**——裸备忘（`bg1.2 bgm13`）
    会被原样当成代码输出，`bg1.2 bgm13` 这种行在 Ren'Py 里是非法语句。
    现在：**像注释的裸文本**（判据见 `dsl._looks_like_stage_note`）补成
    `# bg1.2 bgm13`；合法原生写法 / 自定义语句 / DSL 一律不动。

⚠ 补 `#` 发生在**产物（.rpy）**这一层：Excel 格子 / 卡片里作者打的字
**原样返回**（项目有一条 P0 契约：格内文字读回来必须逐字不变，
`test_hell_carrier_attack` 用恶意 payload 钉着）。所以这里同时钉
"渲染补 #" 与 "表格不偷偷改字"。
"""

from __future__ import annotations

from storygraph.story.beat_model import BEAT_FIELDS, Beat
from storygraph.story.dsl import parse_stage
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel
from storygraph.story.script_table import render_beats_to_rpy


def _render(beat, **kw):
    return render_beats_to_rpy([beat], label_name='t', **kw)


# ------------------------------------------------------------------
#  一、「注释」列：渲染时逐行补 #（本来就有的行为，现在钉住）
# ------------------------------------------------------------------

class TestCommentRender:
    def test_missing_hash_is_added_per_line(self):
        out = _render(Beat(seq=1, label='t', text='台词',
                           comment='bg1.2 bgm13\n（淡出）'))
        assert '    # bg1.2 bgm13' in out, out
        assert '    # （淡出）' in out, out
        assert '    ## ' not in out, out

    def test_existing_hash_is_not_doubled(self):
        out = _render(Beat(seq=1, label='t', text='台词', comment='# bg1.2'))
        assert '    # bg1.2' in out, out
        assert '##' not in out, out

    def test_empty_comment_emits_nothing(self):
        out = _render(Beat(seq=1, label='t', text='台词'))
        assert '# ' not in out.replace('# 由剧本表', ''), out


# ------------------------------------------------------------------
#  二、「舞台（高级）」列：像注释的裸文本补 #
# ------------------------------------------------------------------

class TestStageNotes:
    def _ops(self, text):
        return parse_stage(text).ops

    def test_bare_note_becomes_comment(self):
        ops = self._ops('bg1.2 bgm13')
        assert ops[0].verb == 'comment', ops[0]
        assert ops[0].raw == '# bg1.2 bgm13', ops[0].raw

    def test_chinese_note_becomes_comment(self):
        ops = self._ops('白色转场')
        assert ops[0].verb == 'comment'
        assert ops[0].raw == '# 白色转场'

    def test_fullwidth_punctuation_still_note(self):
        assert self._ops('bg1.2（淡出）')[0].verb == 'comment'

    def test_colon_and_comma_still_note(self):
        for text in ('bg1.2: 淡出', 'bg1.2, bgm13'):
            assert self._ops(text)[0].verb == 'comment', text

    def test_known_statement_heads_stay_code(self):
        for text in ('show ling happy', 'hide ling', 'scene bg room',
                     'pause 1.0', 'with dissolve', 'window hide',
                     'play music bgm03', 'stop music fadeout 1.0'):
            op = self._ops(text)[0]
            assert op.verb != 'comment', (text, op)
            assert op.native is True, (text, op)

    def test_code_punctuation_stays_code(self):
        for text in ('mycue "x"', '$ x = 1', 'target = ling',
                     'renpy.jump(x)', 'foo[0]'):
            assert self._ops(text)[0].verb != 'comment', text

    def test_existing_hash_is_not_doubled(self):
        op = self._ops('# bg1.2')[0]
        assert op.verb != 'comment', op
        assert op.raw == '# bg1.2', op.raw

    def test_dsl_is_untouched(self):
        op = self._ops('[显示] 凌跃捷 开心')[0]
        assert op.verb == 'show' and op.native is False, op

    def test_render_stage_note_as_comment(self):
        out = _render(Beat(seq=1, label='t', text='台词',
                           stage='bg1.2 bgm13'))
        assert '    # bg1.2 bgm13' in out, out
        assert '\n    bg1.2 bgm13' not in out, out

    def test_render_native_stage_unchanged(self):
        out = _render(Beat(seq=1, label='t', text='台词',
                           stage='show ling happy'))
        assert '    show ling happy' in out, out

    def test_render_keeps_other_native_lines(self):
        out = _render(Beat(seq=1, label='t', text='台词',
                           stage='bg1.2 bgm13\nshow ling happy\n$ x = 1'))
        assert '    # bg1.2 bgm13' in out, out
        assert '    show ling happy' in out, out
        assert '    $ x = 1' in out, out


# ------------------------------------------------------------------
#  三、格内文字原样回来（补 # 只在产物层，不动用户打的字）
# ------------------------------------------------------------------

class TestCellTextIsUntouched:
    def test_comment_cell_roundtrips_verbatim(self):
        beat = Beat(seq=1, label='t', text='台词', comment='bg1.2 bgm13')
        row = dict(zip(BEAT_FIELDS, beat.to_row()))
        assert row['Comment'] == 'bg1.2 bgm13', row['Comment']
        assert Beat.from_row(row).comment == 'bg1.2 bgm13'

    def test_stage_cell_roundtrips_verbatim(self):
        beat = Beat(seq=1, label='t', text='台词')
        row = dict(zip(BEAT_FIELDS, beat.to_row()))
        row['Stage'] = 'bg1.2 bgm13'
        assert Beat.from_row(row).stage == 'bg1.2 bgm13'
        assert dict(zip(BEAT_FIELDS, Beat.from_row(row).to_row()))['Stage'] == \
            'bg1.2 bgm13'

    def test_multiline_comment_cell_not_rewritten(self):
        """`'## A\\nB'` 这种"第二行没写 #"的多行格子：**原样读回**。"""
        beat = Beat(seq=1, label='t', text='台词', comment='## A\nB')
        row = dict(zip(BEAT_FIELDS, beat.to_row()))
        assert Beat.from_row(row).comment == '## A\nB'


# ------------------------------------------------------------------
#  四、端到端：Excel 往返 + 导出 .rpy
# ------------------------------------------------------------------

class TestEndToEnd:
    def test_excel_roundtrip_keeps_cell_then_render_adds_hash(self,
                                                               tmp_path):
        xlsx = tmp_path / 'a.xlsx'
        export_beats_to_excel(
            [Beat(seq=1, label='t', text='台词', comment='bg1.2 bgm13',
                  stage='bg1.2 bgm13')], xlsx)
        back = import_beats_from_excel(xlsx)
        # 格内文字原样；产物里两条都成了注释
        assert back[0].comment == 'bg1.2 bgm13', back[0].comment
        assert back[0].stage == 'bg1.2 bgm13', back[0].stage
        out = render_beats_to_rpy(back, label_name='t')
        assert '# bg1.2 bgm13' in out, out
        assert '# bg1.2 bgm13\n' in out, out
        assert '##' not in out, out
        assert '\n    bg1.2 bgm13' not in out, out

    def test_rpy_comment_roundtrip_is_byte_stable(self, tmp_path):
        """源文件里本来就带 `#` 的注释：打开→导出逐字不变（不多加、不双写）。"""
        from storygraph.story.script_table import open_rpy_as_table

        src = ('label t:\n'
               '    # bg1.2 bgm13\n'
               '    #bgm03\n'
               '    e "台词"\n'
               '    return\n')
        rpy = tmp_path / 'a.rpy'
        rpy.write_text(src, encoding='utf-8')
        table = tmp_path / 'a.xlsx'
        open_rpy_as_table(rpy, table)
        out = render_beats_to_rpy(import_beats_from_excel(table))
        assert '# bg1.2 bgm13' in out, out
        assert '#bgm03' in out, out
        assert '##' not in out, out
