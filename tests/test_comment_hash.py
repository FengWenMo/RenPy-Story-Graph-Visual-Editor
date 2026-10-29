# -*- coding: utf-8 -*-
"""
注释的 `#`：**只有备注列补**，舞台列一个字都不动（2026-10-28 起，10-29 改）
==============================================================================

## 现在的规矩（用户 2026-10-29 拍板）

> 我的补#号只能发生在备注上……高级区 stage 不应当补#

  · **「备注」列 / 卡片备注**：导出 `.rpy` 时逐行补 `# `
    （`render_beat_to_rpy` ①）。这个文件把它钉住，防止以后有人"优化"掉；
  · **「舞台（高级）」列**：**原样直通，不补 `#`**。那一列是**代码** ——
    写什么就出什么。看着不像语句的行由 `story.stage_suspect` 指认，
    导出前提一句、问人要不要挪到备注（`ui_qt.dialog_stage_suspect`）。

## 为什么撤掉了"舞台自动补 #"

2026-10-28 曾经做过（`dsl._looks_like_stage_note`）：判据看字面猜"这行像不像
备忘"，像就补 `#`。第二天撤掉，因为字面判不开：

    bg1.2 bgm13      ← 作者想让它是备忘
    weather6 rain    ← fork 引擎的自定义语句，必须执行

两者字面上同构。自动补 `#` 的结果是把真语句注释掉 —— 而卡片上看着还是
原文，只有导出后才变，属于"最难查"的一类静默丢行为。

⚠ 补 `#`（备注列）发生在**产物（.rpy）**这一层：Excel 格子 / 卡片里作者
打的字**原样返回**（项目有一条 P0 契约：格内文字读回来必须逐字不变，
`test_hell_carrier_attack` 用恶意 payload 钉着）。所以这里同时钉
"备注补 #" 与 "表格不偷偷改字"。
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
#  二、「舞台（高级）」列：**不补 #**，原样直通（2026-10-29）
# ------------------------------------------------------------------

class TestStageIsNeverCommented:
    """
    这一列是**代码**：写什么出什么。

    "看着不像语句"的行由 `story.stage_suspect` 指认、导出前问人 ——
    **渲染层一个字都不许改**（改了就是静默丢行为）。
    """

    def _ops(self, text):
        return parse_stage(text).ops

    def test_bare_note_stays_code(self):
        """看着像备忘的裸文本也**不**补 #。"""
        op = self._ops('bg1.2 bgm13')[0]
        assert op.verb != 'comment', op
        assert op.native is True, op
        assert op.raw == 'bg1.2 bgm13', op.raw

    def test_chinese_note_stays_code(self):
        op = self._ops('白色转场')[0]
        assert op.verb != 'comment', op
        assert op.raw == '白色转场', op.raw

    def test_fullwidth_and_colon_stay_code(self):
        for text in ('bg1.2（淡出）', 'bg1.2: 淡出', 'bg1.2, bgm13'):
            op = self._ops(text)[0]
            assert op.verb != 'comment', (text, op)
            assert op.raw == text, (text, op.raw)

    def test_custom_statement_survives(self):
        """fork 引擎的自定义语句必须原样活下来（这次回退的正因）。"""
        for text in ('weather6 rain', 'mycue bg1.2', 'mycam shake 0.5 loop'):
            op = self._ops(text)[0]
            assert op.verb != 'comment', (text, op)
            assert op.raw == text, (text, op.raw)

    def test_known_statement_heads_stay_code(self):
        for text in ('show ling happy', 'hide ling', 'scene bg room',
                     'pause 1.0', 'with dissolve', 'window hide',
                     'play music bgm03', 'stop music fadeout 1.0'):
            op = self._ops(text)[0]
            assert op.verb != 'comment', (text, op)
            assert op.native is True, (text, op)

    def test_existing_hash_stays_as_written(self):
        op = self._ops('# bg1.2')[0]
        assert op.raw == '# bg1.2', op.raw

    def test_dsl_is_untouched(self):
        op = self._ops('[显示] 凌跃捷 开心')[0]
        assert op.verb == 'show' and op.native is False, op

    def test_render_keeps_every_stage_line(self):
        out = _render(Beat(seq=1, label='t', text='台词',
                           stage='bg1.2 bgm13\nshow ling happy\n$ x = 1'))
        assert '\n    bg1.2 bgm13' in out, out
        assert '\n    show ling happy' in out, out
        assert '\n    $ x = 1' in out, out
        assert '# bg1.2 bgm13' not in out, out

    def test_render_never_adds_hash(self):
        for text in ('white fade', 'bg1.2 bgm13', '白色转场'):
            out = _render(Beat(seq=1, label='t', text='台词', stage=text))
            assert f'\n    {text}' in out, out
            assert f'# {text}' not in out, out


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
        """同一段文字：写「备注」→ 变注释；写「舞台」→ 原样出（两列分工）。"""
        xlsx = tmp_path / 'a.xlsx'
        export_beats_to_excel(
            [Beat(seq=1, label='t', text='台词', comment='bg1.2 bgm13',
                  stage='bg1.2 bgm13')], xlsx)
        back = import_beats_from_excel(xlsx)
        # 格内文字原样（两列都不许偷偷改字）
        assert back[0].comment == 'bg1.2 bgm13', back[0].comment
        assert back[0].stage == 'bg1.2 bgm13', back[0].stage
        out = render_beats_to_rpy(back, label_name='t')
        # 备注 → 补成注释；舞台 → 原样一行（不补、不删）
        assert '\n    # bg1.2 bgm13' in out, out
        assert '\n    bg1.2 bgm13' in out, out
        assert '##' not in out, out

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
