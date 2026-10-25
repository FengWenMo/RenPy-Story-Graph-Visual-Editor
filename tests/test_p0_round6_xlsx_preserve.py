# -*- coding: utf-8 -*-
"""
第六轮 P0 判据（2026-10-25）：**重新摊表不许抹掉用户在 xlsx 里手写的东西**。

这张工作簿是本产品的主界面（"对着表格就能编排演出"），所以判据只有一条：
**重建工具自己那几张表，人的东西原样带过去**。逐条钉死：

  · 自己加的整张页（角色设定 / 大纲 / 排期）
  · Beats 里自己加的列（配音状态 / 审核），并按 `LineId` 回填（行增删也对得上）
  · 格子上的底色 / 字色 / 批注
  · 自己调过的冻结窗格
  · 覆盖前有备份（openpyxl 搬不动的东西——图表 / 透视表——靠它兜）
  · 带用户列的表照样能导回 .rpy（未知列不许影响读取）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip('openpyxl')
import openpyxl                                        # noqa: E402
from openpyxl.comments import Comment                  # noqa: E402
from openpyxl.styles import PatternFill                # noqa: E402

from storygraph.story.excel_import import import_beats_from_excel  # noqa: E402
from storygraph.story.script_table import open_rpy_as_table        # noqa: E402

SRC = '''# 头
label ch1:
    jc "第一句"
    jc "第二句"
    return
'''


def _fixture(tmp_path):
    game = tmp_path / 'game'
    game.mkdir(parents=True)
    ch = game / 'ch1.rpy'
    ch.write_bytes(SRC.encode('utf-8'))
    xlsx = ch.with_suffix('.xlsx')
    open_rpy_as_table(ch, xlsx)
    return ch, xlsx


def _add_user_stuff(xlsx):
    wb = openpyxl.load_workbook(xlsx)
    ws = wb['Beats']
    col = ws.max_column + 1
    ws.cell(row=1, column=col, value='配音状态')
    ws.cell(row=2, column=col, value='已录')
    ws.cell(row=3, column=col, value='待录')
    ws.cell(row=2, column=1).fill = PatternFill('solid', fgColor='FFF2CC')
    ws.cell(row=2, column=1).comment = Comment('这句要重录', '策划')
    sheet = wb.create_sheet('角色设定')
    sheet['A1'] = '小雨'
    sheet['B1'] = '17 岁，怕水'
    ws.freeze_panes = 'A2'
    wb.save(xlsx)


class TestUserContentSurvivesRebuild:
    def test_user_sheet_is_carried_over(self, tmp_path):
        ch, xlsx = _fixture(tmp_path)
        _add_user_stuff(xlsx)
        open_rpy_as_table(ch, xlsx)
        wb = openpyxl.load_workbook(xlsx)
        assert '角色设定' in wb.sheetnames
        assert wb['角色设定']['A1'].value == '小雨'
        assert wb['角色设定']['B1'].value == '17 岁，怕水'

    def test_user_column_is_carried_over_by_line_id(self, tmp_path):
        ch, xlsx = _fixture(tmp_path)
        _add_user_stuff(xlsx)
        open_rpy_as_table(ch, xlsx)
        ws = openpyxl.load_workbook(xlsx)['Beats']
        headers = [str(c.value or '') for c in ws[1]]
        assert '配音状态' in headers, '用户自加的列被抹掉了'
        col = headers.index('配音状态') + 1
        values = [ws.cell(row=r, column=col).value for r in (2, 3)]
        assert values == ['已录', '待录'], values

    def test_cell_marks_and_freeze_are_carried_over(self, tmp_path):
        ch, xlsx = _fixture(tmp_path)
        _add_user_stuff(xlsx)
        open_rpy_as_table(ch, xlsx)
        ws = openpyxl.load_workbook(xlsx)['Beats']
        assert ws.cell(row=2, column=1).comment is not None, '批注没了'
        assert ws.cell(row=2, column=1).comment.text == '这句要重录'
        assert str(ws.cell(row=2, column=1).fill.fgColor.rgb) == '00FFF2CC'
        assert ws.freeze_panes == 'A2', ws.freeze_panes

    def test_old_table_is_backed_up(self, tmp_path):
        ch, xlsx = _fixture(tmp_path)
        _add_user_stuff(xlsx)
        open_rpy_as_table(ch, xlsx)
        baks = list(xlsx.parent.glob('ch1.bak-*.xlsx'))
        assert baks, '覆盖旧表却没备份'

    def test_unknown_column_does_not_break_import(self, tmp_path):
        ch, xlsx = _fixture(tmp_path)
        _add_user_stuff(xlsx)
        beats = import_beats_from_excel(xlsx)
        assert [b.text for b in beats][:2] == ['第一句', '第二句']

    def test_preserve_stats_report_what_was_kept(self, tmp_path):
        from storygraph.story.excel_export import last_preserve_stats
        ch, xlsx = _fixture(tmp_path)
        _add_user_stuff(xlsx)
        open_rpy_as_table(ch, xlsx)
        kept = last_preserve_stats()
        assert kept.get('sheets') == 1
        assert kept.get('columns') == 1
        assert kept.get('backup')

    def test_new_table_has_no_backup_noise(self, tmp_path):
        from storygraph.story.excel_export import last_preserve_stats
        ch, xlsx = _fixture(tmp_path)          # 首次摊表：本来就没有旧表
        assert not list(xlsx.parent.glob('ch1.bak-*.xlsx'))
        assert last_preserve_stats().get('backup') == ''


# ---------------------------------------------------------------------------
#  隐藏列「来源指纹」：让"表比源码旧"有直接证据
# ---------------------------------------------------------------------------

class TestSourceFingerprint:
    def _project(self, tmp_path):
        game = tmp_path / 'game'
        game.mkdir(parents=True)
        ch = game / 'ch1.rpy'
        ch.write_bytes(SRC.encode('utf-8'))
        xlsx = ch.with_suffix('.xlsx')
        open_rpy_as_table(ch, xlsx)
        return ch, xlsx

    def test_fingerprint_is_recorded_in_the_hidden_sheet(self, tmp_path):
        from storygraph.story.table_meta import KEY_SHA1, fingerprint_of, read_meta
        ch, xlsx = self._project(tmp_path)
        assert read_meta(xlsx).get(KEY_SHA1) == fingerprint_of(ch)

    def test_stale_note_speaks_only_when_the_source_changed(self, tmp_path):
        from storygraph.story.table_meta import staleness_note
        ch, xlsx = self._project(tmp_path)
        # 表刚摊完、源码没动 → 不算旧（哪怕要删行，那也是用户自己清空的）
        assert staleness_note(xlsx, ch, removed_count=2) == ''
        # 源码被改过 → 旧
        ch.write_bytes((SRC + '# 程序员加的\n').encode('utf-8'))
        note = staleness_note(xlsx, ch, removed_count=2)
        assert '比源码旧' in note
        # 没删行时不啰嗦（新旧都无所谓）
        assert staleness_note(xlsx, ch, removed_count=0) == ''

    def test_old_table_without_fingerprint_does_not_guess(self, tmp_path):
        import openpyxl
        from storygraph.story.table_meta import KEY_SHA1, staleness_note
        ch, xlsx = self._project(tmp_path)
        wb = openpyxl.load_workbook(xlsx)
        ws = wb['表信息']
        for row in ws.iter_rows(min_row=2):
            if row[0].value == KEY_SHA1:
                row[0].value = None
                row[1].value = None
        wb.save(xlsx)
        ch.write_bytes((SRC + '# 改过\n').encode('utf-8'))
        assert staleness_note(xlsx, ch, removed_count=1) == ''


# ---------------------------------------------------------------------------
#  removed_count 必须和"预览里有几条"对得上（拍内删行也算）
# ---------------------------------------------------------------------------

class TestRemovedCountIsHonest:
    def test_lines_dropped_inside_a_beat_are_counted(self, tmp_path):
        """
        `removed_count` 必须把"拍内跟着删掉的行"算进去。

        从前的写法是 `removed_lines + shrunk_total`，而 `shrunk_total` 是
        `patch_export` 里的局部变量 —— 只初始化、从不累加（真正的累加在
        `st.shrunk_total`）。于是这一类永远算 0：预览里明明有 2 条，
        count 却是 0。回执靠"预览兜底"凑巧说对了，但任何只看 count 的判据
        （比如"表比源码旧"）都**静默失效**。

        走**真实的表 → 源**那条路（与 CLI `table xlsx2rpy` 同一条），
        不手搓 beat。
        """
        from storygraph.pipeline.compile import compile_table

        game = tmp_path / 'game'
        game.mkdir()
        ch = game / 'ch1.rpy'
        base = ('# 头\n'
                'label ch1:\n'
                '    jc "第一句"\n'
                '    jc "第二句"\n'
                '    return\n')
        ch.write_bytes(base.encode('utf-8'))
        xlsx = ch.with_suffix('.xlsx')
        open_rpy_as_table(ch, xlsx)            # 表里没有那两行

        # 载入之后，程序员往同一个文件里加了两行表里没有的
        ch.write_bytes(base.replace(
            '    jc "第二句"\n',
            '    jc "第二句"\n    $ flag = True\n'
            '    play music "bgm.ogg" fadein 1.0\n').encode('utf-8'))

        _rpy, _sheet, res = compile_table(
            xlsx, rpy_path=ch, source_path=ch, write_sheet=False,
            engine_lint=False)
        assert res.used_patch, res.patch_note
        assert res.removed_count == 2, (
            f'count={res.removed_count}（拍内删行没算进去？）'
            f' preview={res.removed_preview}')
        assert len(res.removed_preview) == 2
        # 同一趟里"表比源码旧"也必须被说出来（指纹是直接证据）
        assert '比源码旧' in res.stale_note, res.stale_note
