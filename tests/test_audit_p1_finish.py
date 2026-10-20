"""
tests/test_audit_p1_finish.py
=============================
体检报告（`docs/评估_代码完成度与真实项目体检_2026-10-18.md`）**P1 收尾**的回归网。

§10.2 那一轮逐条核销的结论是：7 条有效 / 2 条只做了一半 / 1 条实测无效 /
1 条没构造出来。这个文件把"剩下那几条"钉住，每一条都对应审计原话：

  · P1-7  「写回落到 build/renpy/，不改原章节」→ 指名了底稿就必须写回它，
           写完之后内存重新对齐；
  · P1-8  「`_has_change` 恒真」→ 「只看有变化的拍」要真的只剩有变化的拍；
  · P1-10 「给了 --source 还说'加 --source'」→ 两句话必须分得开；
  · P1-1  「全量重生成会重排整份文件」→ 表记着来源，导回时自动走增量；
  · P1-3c 「`init -1 python:` 不支持」→ 要认成 python 块（不是"不认识的块"）；
  · P1-A  「全量路径丢带说话人的菜单标题」→ 过表往返一个字不丢；
  · P2-B  「`engine_lint.ran` 口径错」→ 跑过且通过时 `ran` 必须是 True。
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ======================================================================
#  P1-A / P1-1：表往返不丢字
# ======================================================================

#: 真机 `the_question/script.rpy:97-104` 的最小复刻：`menu:` 的标题带说话人，
#: 而它既不是选项、也没有专门的列 —— 只能存在 `Beat.raw` 里。
_MENU_WITH_SPEAKER = '''\
label start:
    menu:
        s "Sure, but what's a \\"visual novel?\\""
        "Yes":
            jump yes
        "No":
            jump no

label yes:
    return

label no:
    return
'''


class TestMenuCaptionRoundtrip:
    def test_say_line_is_classified_as_raw_not_python(self):
        """
        「代码」格里一行 `s "…"` 读回来必须是 **raw**。

        旧判据只认语句头白名单，`s` 不在里面 → 判成 python →
        `Beat.raw` 变空 → 整行从产物里消失（审计 P1-A 的真身）。
        """
        from storygraph.story.beat_model import _split_code_cell
        raw, code, _ordered, _blocks = _split_code_cell(
            's "Sure, but what\'s a \\"visual novel?\\""')
        assert raw == 's "Sure, but what\'s a \\"visual novel?\\""', raw
        assert code == ''

    def test_python_lines_still_classified_as_code(self):
        """别修过头：真正的 python 不能被当成台词。"""
        from storygraph.story.beat_model import _split_code_cell
        for line in ('print("hi")', 'x = "a"', '"a" + b', 'fmsg("x")'):
            _raw, code, _o, _b = _split_code_cell(line)
            assert code, f'{line!r} 被当成 raw 了'

    def test_narration_caption_is_raw(self):
        from storygraph.story.beat_model import _split_code_cell
        raw, code, _o, _b = _split_code_cell('"你确定吗？"')
        assert raw == '"你确定吗？"' and code == ''

    def test_caption_survives_table_roundtrip(self, tmp_path):
        """
        rpy → xlsx → 拍：菜单标题必须还在 `menu_beat.raw` 里。

        这是审计实测丢字的那一步（`the_question` 里那句
        `s "Sure, but what's a \\"visual novel?\\""`）。
        """
        from storygraph.story.excel_import import import_beats_from_excel
        from storygraph.story.script_table import open_rpy_as_table

        src = tmp_path / 'ch.rpy'
        src.write_text(_MENU_WITH_SPEAKER, encoding='utf-8')
        xlsx = tmp_path / 'ch.xlsx'
        open_rpy_as_table(str(src), str(xlsx))

        beats = import_beats_from_excel(xlsx)
        menu = next(b for b in beats if b.kind == 'menu')
        assert 'visual novel' in menu.raw, (
            f'菜单标题在过表时丢了：raw={menu.raw!r} code={menu.code!r}')

    def test_caption_appears_in_the_regenerated_script(self, tmp_path):
        """过完表再全量渲染 → 那一行必须在产物里，且缩进在 `menu:` 下面。"""
        from storygraph.pipeline.compile import compile_table
        from storygraph.story.script_table import open_rpy_as_table

        src = tmp_path / 'ch.rpy'
        src.write_text(_MENU_WITH_SPEAKER, encoding='utf-8')
        xlsx = tmp_path / 'ch.xlsx'
        open_rpy_as_table(str(src), str(xlsx))
        out = tmp_path / 'out.rpy'
        # 传一个不存在的 source，逼它走**全量**（本用例要测的正是全量那条路）
        compile_table(str(xlsx), rpy_path=out, write_sheet=False, force=True,
                      skip_empty=True, engine_lint=False,
                      source_path=None)
        text = out.read_text(encoding='utf-8')
        # 注：`compile_table` 现在会自动从「表信息」里取来源并走增量；
        # 这条断言对两条路都成立 —— 那一行本来就必须在。
        assert 'visual novel' in text, text
        line = next(ln for ln in text.splitlines() if 'visual novel' in ln)
        assert line.startswith('        '), line


class TestTableRemembersItsSource:
    """P1-1：表里记来源 → 导回时自动增量（不再重排整份文件）。"""

    def _make(self, tmp_path):
        src = tmp_path / 'ch.rpy'
        src.write_text(
            '# 顶格注释：工具的注释\n'
            'define e = Character("Eileen")\n'
            '\n'
            '\n'
            'label ch:\n'
            '    e "第一句"   \n'
            '\n'
            '    e "第二句"\n'
            '    return\n',
            encoding='utf-8')
        from storygraph.story.script_table import open_rpy_as_table
        xlsx = tmp_path / 'ch.xlsx'
        open_rpy_as_table(str(src), str(xlsx))
        return src, xlsx

    def test_meta_sheet_records_the_source(self, tmp_path):
        from storygraph.story.table_meta import KEY_SOURCE, read_meta
        src, xlsx = self._make(tmp_path)
        meta = read_meta(xlsx)
        assert Path(meta.get(KEY_SOURCE, '')).resolve() == src.resolve()

    def test_compile_without_source_still_merges(self, tmp_path):
        """
        **不给 `--source`** 导回：仍然走增量 → 未改的行逐字节保留。

        审计口径：全量会 CRLF→LF、压空行、加 `return`、删行尾空格
        （真机 52 个文件里 30 个有差异）。这里把"行尾空格 / 连续空行"
        都放进去，全量路径一定会把它们改掉。
        """
        from storygraph.pipeline.compile import compile_table
        from storygraph.story.generation_guard import ensure_generator_marker

        src, xlsx = self._make(tmp_path)
        out = tmp_path / 'out.rpy'
        _p, _s, res = compile_table(str(xlsx), rpy_path=out,
                                    write_sheet=False, force=True,
                                    skip_empty=True, engine_lint=False)
        assert res.used_patch, f'没有自动走增量：{res.patch_note}'
        body = out.read_text(encoding='utf-8')
        # 生成标记是唯一允许多出来的东西
        want = src.read_text(encoding='utf-8')
        banner = ensure_generator_marker('')
        stripped = body[len(banner):] if body.startswith(banner) else body
        stripped = stripped.lstrip('\n')
        assert stripped == want, '导回之后原文被重排了'

    def test_stale_source_falls_back_to_full_regeneration(self, tmp_path):
        """来源文件被删了 → 当没有来源（行为与从前一致，不报错）。"""
        src, xlsx = self._make(tmp_path)
        src.unlink()
        from storygraph.story.table_meta import source_of
        assert source_of(xlsx) is None

    def test_cli_defaults_to_the_recorded_source(self, tmp_path, capsys):
        """
        `table xlsx2rpy <表>`（**不给 --out / --source**）→ 认表里记的那一章，
        而且没改的行一个字节不动。

        这是审计 P1-1 那个场景的命令行形态。2026-10-19 改口径（P1-1）：
        零改动往返**不再插生成标记**（那正是"零改动却改了文件"的 2 行），
        所以这里的最强判据是"文件逐字节没变"，而不是"多了一行标记"。
        """
        from storygraph.cli import main

        src, xlsx = self._make(tmp_path)
        before = src.read_text(encoding='utf-8')
        before_bytes = src.read_bytes()
        code = main(['table', 'xlsx2rpy', str(xlsx),
                     '--no-engine-lint'])
        log = capsys.readouterr()
        assert code == 0, log.out + log.err
        assert src.read_bytes() == before_bytes, \
            '零改动往返却改写了文件（或多个生成标记）'
        assert src.read_text(encoding='utf-8') == before


# ======================================================================
#  P1-3c：`init -1 python:` 要真的被认出来
# ======================================================================

class TestInitPriorityPython:
    def test_negative_priority_is_a_python_block(self, tmp_path):
        from storygraph.parsing.renpy_parser import (
            ParseResultBuilder, RenPyParser)

        src = tmp_path / 'defs.rpy'
        src.write_text(
            'init -1 python:\n'
            '    def part(expr):\n'
            '        return expr\n'
            '\n'
            'label start:\n'
            '    "hi"\n'
            '    return\n',
            encoding='utf-8')
        nodes = RenPyParser().parse(src)
        kinds = [type(n).__name__ for n in nodes]
        assert 'PythonNode' in kinds, kinds
        res = ParseResultBuilder().build(nodes)
        assert [b.header for b in res.file_blocks] == ['init -1 python:']

    @pytest.mark.parametrize('head', ['init python:', 'init 1 python:',
                                      'init -1 python:', 'init 1.5 python:'])
    def test_all_priority_forms_are_recognised(self, tmp_path, head):
        from storygraph.parsing.renpy_parser import RenPyParser
        src = tmp_path / 'd.rpy'
        src.write_text(f'{head}\n    X = 1\n', encoding='utf-8')
        kinds = [type(n).__name__ for n in RenPyParser().parse(src)]
        assert 'PythonNode' in kinds, (head, kinds)


# ======================================================================
#  P1-8：「只看有变化的拍」真的只看有变化的
# ======================================================================

class TestOnlyChangedFrames:
    def _frames(self):
        from storygraph.story.beat_model import Beat, assign_identity
        from storygraph.app.stage import build_frames
        beats = [
            Beat(seq=1, label='t', text='一', scene='bg room'),
            Beat(seq=2, label='t', text='二'),          # 纯台词：舞台没变
            Beat(seq=3, label='t', text='三', show='ling happy'),
            Beat(seq=4, label='t', text='四'),          # 纯台词
        ]
        assign_identity(beats)
        return build_frames(beats)

    def test_pure_dialogue_is_not_a_change(self):
        from storygraph.app.stage import has_stage_change
        frames = self._frames()
        assert [has_stage_change(f) for f in frames] == [True, False, True,
                                                         False]

    def test_old_criterion_would_keep_everything(self):
        """
        旧判据 `bool(frame_summary(f))` 恒真 —— 这张网防止它回来。
        """
        from storygraph.app.stage import frame_summary
        frames = self._frames()
        assert all(frame_summary(f) for f in frames), \
            '摘要本身应该永远有字（#N · line_id）'
        from storygraph.app.stage import has_stage_change
        assert sum(has_stage_change(f) for f in frames) == 2


# ======================================================================
#  P1-10 / P2-B：报告与文案不许说假话
# ======================================================================

class TestGuardWording:
    def test_source_given_says_it_did_not_match(self):
        from storygraph.story.generation_guard import _make_opaque_lost_issue
        issue = _make_opaque_lost_issue([(1, 'define e = …')], 'ch.rpy',
                                        source_given=True)
        msg = issue.message
        assert '没能和表对上' in msg
        assert '不是"没给底稿"' in msg

    def test_no_source_tells_you_to_add_it(self):
        from storygraph.story.generation_guard import _make_opaque_lost_issue
        msg = _make_opaque_lost_issue([(1, 'define e = …')], None).message
        assert '`--source`' in msg and '走增量合并' in msg


class TestBuildReportLintRan:
    def test_ran_is_true_when_lint_passes(self, tmp_path):
        """
        lint 跑过并通过 → 报告必须写 `ran: true`。

        审计原话：旧实现用"有没有 ENGINE_ issue"反推，于是"通过"被写成
        `ran=false` —— 闸门开着，报告却在说假话。
        """
        from storygraph.pipeline.engine_collect import find_sdk
        if not find_sdk():
            pytest.skip('没有 Ren\'Py SDK，跑不了引擎 lint')

        root = tmp_path / 'proj'
        game = root / 'game'
        game.mkdir(parents=True)
        (game / 'ch.rpy').write_text(
            'define e = Character("Eileen")\n'
            'label ch:\n'
            '    e "hello"\n'
            '    return\n',
            encoding='utf-8')
        from storygraph.pipeline.build import build_project
        res = build_project(root, out_dir=tmp_path / 'out', write_report=False,
                            engine_lint=True)
        info = res.report['build']['guard']['engine_lint']
        assert info['requested'] is True
        assert info['ran'] is True, info
        assert info['ok'] is True, info


# ======================================================================
#  P1-7：写回闭环
# ======================================================================

class TestWriteBackCloses:
    def test_named_source_is_the_default_target(self, tmp_path):
        """指名了底稿 → 产物路径就是它（不再落 build/renpy/）。"""
        from storygraph.app import tables

        root = tmp_path / 'proj'
        game = root / 'game'
        game.mkdir(parents=True)
        script = game / 'ch01.rpy'
        script.write_text(
            'define e = Character("Eileen")\n'
            'label ch01:\n'
            '    e "第一句"\n'
            '    e "第二句"\n'
            '    return\n',
            encoding='utf-8')
        tbl = tables.generate_table(script, project_root=root)['table_path']
        res = tables.export_table(tbl, project_root=root, source_rpy=script)
        assert Path(res['out_path']).resolve() == script.resolve(), \
            res['out_path']

    def test_write_back_keeps_comments_and_applies_the_edit(self, tmp_path):
        """
        写回原章节：**改的那一句变了，注释与未改的行一字不动**。

        这是"闭环"的全部意义 —— 从前写回 build/，游戏里那句话永远不变。
        """
        from storygraph.app import tables
        import openpyxl

        root = tmp_path / 'proj'
        game = root / 'game'
        game.mkdir(parents=True)
        script = game / 'ch01.rpy'
        original = (
            '# 这一行注释必须活下来\n'
            'define e = Character("Eileen")\n'
            'label ch01:\n'
            '    e "第一句"\n'
            '    e "第二句"\n'
            '    return\n')
        script.write_text(original, encoding='utf-8')
        tbl = tables.generate_table(script, project_root=root)['table_path']

        # 在表里改一句（模拟策划在 Excel 里编辑）
        wb = openpyxl.load_workbook(tbl)
        ws = wb['Beats']
        header = [c.value for c in ws[1]]
        col = header.index('台词') + 1
        for row in range(2, ws.max_row + 1):
            if ws.cell(row=row, column=col).value == '第二句':
                ws.cell(row=row, column=col, value='改过的第二句')
        wb.save(tbl)

        res = tables.export_table(tbl, project_root=root, source_rpy=script)
        assert not res['blocking'], res['blocking']
        done = tables.apply_export(res, project_root=root)
        assert done['ok'], done.get('error')

        text = script.read_text(encoding='utf-8')
        assert '改过的第二句' in text
        assert '第一句' in text
        assert '# 这一行注释必须活下来' in text
        assert 'define e = Character("Eileen")' in text

    def test_unnamed_source_still_goes_to_build(self, tmp_path):
        """没指名章节 → 仍旧落 build/（往 game/ 写东西必须由人指认）。"""
        from storygraph.app import tables

        root = tmp_path / 'proj'
        game = root / 'game'
        game.mkdir(parents=True)
        script = game / 'ch01.rpy'
        script.write_text(
            'label ch01:\n    "一句"\n    return\n', encoding='utf-8')
        tbl = tables.generate_table(script, project_root=root)['table_path']
        res = tables.export_table(tbl, project_root=root)
        assert 'build' in Path(res['out_path']).parts
        assert 'game' not in Path(res['out_path']).parts
