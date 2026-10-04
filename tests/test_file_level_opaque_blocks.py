"""
边界③：顶层 `init python:` 不许在往返里**整块消失**
=====================================================

交付评审（2026-10-04）列的第三条边界：

> **`init python:`（顶层）会整块丢**；label 内的 `python:` **保住** ✓。
> 与 ② 同源（第三类不进 Beats），不是新 bug。

"与②同源"只解释了一半。②说的是"第三类只在增量路径保住"，于是当时的
结论是"要么走 CodeBlock 保真，要么 validate 阶段报"。**沉默不是选项**。

真实项目实测（`tools/probe_python_block.py`，只读）：

    whackamole.rpy   原文 2 个顶层 `init python:` / 67 行 → 产物 0 个，
                     56 行游戏逻辑（`def mole_click` / `mole_tick` …）整块没了
    mini_game1.rpy   原文 6 个块 → 产物 5 个

**为什么它比"build 失败"危险得多**：`build` 失败时门禁把人拦住了（安全但
不可用）；而这里是"产物语法完全合法、门禁拦不住、游戏能跑但逻辑没了" ——
本仓库最怕那一类（契约 H1：静默改数据比报错坏得多）。

这一版的做法是**保真**（用户给的二选一里更彻底的那个）：

  1. 解析器不再把顶层 `init python:` 当"未建模块"跳过 ——
     `ParseResult.file_blocks` 记下块头 + 块体（`FileBlock`）；
  2. 它随**第一张拍**走进 Excel 的隐藏列 `FileBlocks`（表是"拍"的单位，
     信息只能搭在拍上）；
  3. `render_beats_to_rpy` 把它原样写回**文件级**（所有 label 之前）。

于是"表 → 新文件"这条纯全量路径也能保住它 —— 而这条路径以前**没有
任何增量可言**（没有源 .rpy 可补）。

顺带钉住**不许倒退**的一条：label 内的 `python:` 一直是对的
（走 `CodeBlock` / `CodeBlocks`），不能被这次改动弄坏。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel
from storygraph.pipeline.compile import compile_beats


def _parse_beats(tmp_path, src_text: str, name: str = 's'):
    f = tmp_path / f'{name}.rpy'
    f.write_text(src_text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return pr, beats_from_parse_result(pr)


def _roundtrip(tmp_path, src_text: str) -> str:
    """`.rpy` → 表 → `.rpy`（**纯全量**那条路，没有源可增量）。"""
    _pr, beats = _parse_beats(tmp_path, src_text)
    xl = tmp_path / 's.xlsx'
    export_beats_to_excel(beats, xl)
    return compile_beats(import_beats_from_excel(xl)).rpy_text


#: 几种真实存在的顶层 python 块形态。真实项目里全是第一种。
CASES = {
    'init_python': (
        'init python:\n'
        '    import random\n'
        '\n'
        '    def mole_tick():\n'
        '        return random.randint(1, 3)\n'
        '\n'
        'label a:\n'
        '    e "hi"\n'
    ),
    'init_numbered': (
        'init 1 python:\n'
        '    def helper():\n'
        '        return 1\n'
        '\n'
        'label a:\n'
        '    e "hi"\n'
    ),
    'python_early': (
        'python early:\n'
        '    def early_thing():\n'
        '        return 2\n'
        '\n'
        'label a:\n'
        '    e "hi"\n'
    ),
    'two_blocks': (
        'init python:\n'
        '    import random\n'
        '\n'
        'default score = 0\n'
        '\n'
        'init python:\n'
        '    def mole_set(v):\n'
        '        store.score = v\n'
        '\n'
        'label a:\n'
        '    e "hi"\n'
    ),
    'python_only_file': (
        'init python:\n'
        '    def lone():\n'
        '        return 42\n'
    ),
}


def _python_body_lines(text: str) -> list:
    """
    所有 `python:` 块（含 `init python:` / `python early:`）的**块体行**。

    判据只认块体，不认"文件里出现过的所有行" —— `default score = 0`
    那一类顶层语句**不在本文件管辖范围内**（它们不进 Beats，
    靠增量路径 / `OPAQUE_LOST` 门禁守，见 `test_cli_incremental_source.py`）。
    """
    out, cur, base = [], None, None
    for ln in str(text or '').splitlines():
        s = ln.strip()
        if re.match(r'^(?:init(?:\s+\d+)?\s+)?python(?:\s+early)?'
                    r'(?:\s+in\s+[\w.]+)?\s*:$', s):
            cur, base = [], None
            continue
        if cur is None:
            continue
        if not s:
            continue
        if base is None:
            base = len(ln) - len(ln.lstrip())
        if (len(ln) - len(ln.lstrip())) < base:
            cur = None
            continue
        out.append(s)
    return out


class TestTopLevelPythonSurvives:
    @pytest.mark.parametrize('name', sorted(CASES))
    def test_block_body_survives_the_table(self, tmp_path, name):
        """块体的**每一行**都必须在产物里（比对块数不够：可以整块没了）。"""
        src = CASES[name]
        out = _roundtrip(tmp_path, src)
        want = _python_body_lines(src)
        assert want, f'{name} 样本里没解析出块体，判据写坏了'
        got = _python_body_lines(out)
        lost = [ln for ln in want if ln not in got]
        assert not lost, (
            f'{name}：顶层 python 块丢了 {len(lost)}/{len(want)} 行 —— '
            f'产物语法合法、门禁拦不住、游戏能跑但逻辑没了\n'
            f'  丢了：{lost[:6]}\n{out}')

    def test_header_form_is_preserved(self, tmp_path):
        """`init 1 python:` / `python early:` 的块头原文也要保住。"""
        for header in ('init 1 python:', 'python early:'):
            out = _roundtrip(tmp_path, CASES[
                'init_numbered' if 'init' in header else 'python_early'])
            assert header in out, f'{header} 被改写或丢了：\n{out}'

    def test_blocks_are_emitted_at_file_level(self, tmp_path):
        """块必须在**所有 label 之前**（源文件里"定义在前"的顺序）。"""
        out = _roundtrip(tmp_path, CASES['init_python'])
        assert out.index('init python:') < out.index('label a:'), out

    def test_two_blocks_both_survive(self, tmp_path):
        out = _roundtrip(tmp_path, CASES['two_blocks'])
        assert out.count('init python:') == 2, out
        assert 'def mole_set(v):' in out

    def test_empty_body_is_not_invented(self, tmp_path):
        """块体为空时不产出半截 `init python:`（宁可不写也不写坏的）。"""
        out = _roundtrip(tmp_path, 'init python:\n\nlabel a:\n    e "hi"\n')
        assert 'init python:' not in out, out


class TestParserRecordsThem:
    """解析层：这些块以前**连 AST 节点都不产生**（只有一条 warning）。"""

    def test_file_blocks_are_collected(self, tmp_path):
        """
        解析层现在按**原文**收整个顶层区间 —— 所以**不止**两个 python 块。

        2026-10-05（消掉"第三类内容会丢"那条缺口）：以前只收 `python:` 类块，
        现在 `default` / `define` / `screen` / 注释 … 全都收（它们以前
        **要么没进 AST、要么进了 AST 但原文丢了**）。所以这条断言改成
        "两个 `init python:` 都在，且按源顺序"，而不是数块数。
        """
        pr, _beats = _parse_beats(tmp_path, CASES['two_blocks'])
        heads = [fb.header for fb in pr.file_blocks]
        assert heads.count('init python:') == 2, heads
        assert any(h.startswith('default score') for h in heads), heads
        bodies = '\n'.join(fb.code for fb in pr.file_blocks)
        assert 'import random' in bodies and 'def mole_set(v):' in bodies
        lines = [fb.source_line for fb in pr.file_blocks]
        assert lines == sorted(lines), f'顶层块必须按源顺序：{lines}'

    def test_top_level_content_is_all_captured(self, tmp_path):
        """顶层那一整段（`define` / `screen` / 注释 …）都要被收进来。"""
        src = ('# 文件头注释\n'
               'define e = Character("E")\n'
               'default score = 0\n'
               '\n'
               'screen hud():\n'
               '    text "hi"\n'
               '\n'
               'label a:\n'
               '    e "hi"\n')
        pr, _beats = _parse_beats(tmp_path, src)
        joined = '\n'.join(f'{fb.header}\n{fb.code}'
                           for fb in pr.file_blocks)
        for token in ('# 文件头注释', 'define e = Character("E")',
                      'default score = 0', 'screen hud():', 'text "hi"'):
            assert token in joined, f'{token!r} 没被收进顶层块：\n{joined}'
        assert 'label a' not in joined, 'label 那段该由"拍"负责，不该进顶层块'

    def test_top_level_blocks_do_not_become_beats(self, tmp_path):
        """它们不进"拍"：进来的话会被渲染进 `label start:` 里（语义不对）。"""
        _pr, beats = _parse_beats(tmp_path, CASES['init_python'])
        labels = {b.label for b in beats}
        assert labels == {'a'}, labels

    def test_in_label_python_still_goes_to_beats(self, tmp_path):
        """label 内的 `python:` **一直**是对的，别被这次改动弄坏。"""
        _pr, beats = _parse_beats(tmp_path, CASES['init_python'])
        assert not any('def mole_tick' in (b.code or '') for b in beats), \
            '顶层块跑进拍里了（那会被渲染进 label）'

        src = ('label a:\n'
               '    python:\n'
               '        x = 1\n'
               '        y = 2\n'
               '    e "hi"\n')
        out = _roundtrip(tmp_path, src)
        for token in ('x = 1', 'y = 2'):
            assert token in out, f'label 内的 python 块丢了 {token}：\n{out}'

    def test_python_only_file_keeps_its_code(self, tmp_path):
        """
        只有 `init python:`、没有 label 的文件：**也需要一张拍来携带它**。

        以前这种文件解析出 0 张拍 → 表是空的 → 产物里只剩 `label start`。
        """
        out = _roundtrip(tmp_path, CASES['python_only_file'])
        assert 'def lone():' in out, f'纯 python 文件的内容没了：\n{out}'
        assert 'return 42' in out

    def test_definition_only_file_gets_no_stub_label(self, tmp_path):
        """
        **纯定义文件不许凭空多一个空壳 `label start: pass return`**。

        携带块的那张拍是空拍，照常分组就会给它配一个 label —— 而源文件里
        根本没有 label。空壳的危害是把这种文件导出到 `game/` 时与项目里
        真正的 `label start` 重名（那一种 `LABEL_COLLISION` 门禁能拦住）。
        只做定义的文件没有 label 才是对的。
        """
        out = _roundtrip(tmp_path, CASES['python_only_file'])
        assert 'label ' not in out, f'纯定义文件多出了 label：\n{out}'

    def test_merged_build_has_no_duplicate_labels(self, tmp_path):
        """
        合并产物的**内部**不许有重复 label（回归网）。

        门禁 `LABEL_COLLISION` 只查"产物 vs 项目其他 .rpy"，
        **同一个产物内部**的重名它看不出来 —— 而那同样会让 Ren'Py 起不来。
        这条用例钉住"现在没有"，也顺带记录了那个缺口
        （真实项目检查入口：`tools/check_duplicate_labels.py`）。
        """
        from storygraph.pipeline.build import build_project

        proj = tmp_path / 'proj'
        (proj / 'game').mkdir(parents=True)
        # 顺序刻意让"纯定义文件"夹在中间：它要是产生了 label，
        # 就会和 z_start.rpy 的 `label start` 分成两组 → 重名。
        (proj / 'game' / 'a_defs.rpy').write_text(
            'init python:\n    LONE = 1\n', encoding='utf-8')
        (proj / 'game' / 'm_ch1.rpy').write_text(
            'label ch1:\n    e "one"\n', encoding='utf-8')
        (proj / 'game' / 'z_start.rpy').write_text(
            'label start:\n    e "go"\n', encoding='utf-8')

        out_dir = tmp_path / 'out'
        res = build_project(proj, out_dir=out_dir)
        assert res.ok, res.summary()
        text = (out_dir / 'story.generated.rpy').read_text(encoding='utf-8')
        names = re.findall(r'^label\s+([^\s:#]+)', text, re.M)
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f'产物内部重复 label：{dupes}\n{text}'
        assert 'LONE = 1' in text, '定义文件的内容没进合并产物'


class TestEditorExportNamesTheLoss:
    """
    编辑器这条导出也必须把"丢了哪几行"说出来（交付审查第 1 条缺口）。

    背景：`write_generation` 的 `OPAQUE_LOST` 门禁判据是"源里有、产物里没有的
    顶层语句"，**不给源就无从说起**。CLI 那条路一直传了 `source_text`
    （`compile.write_compiled`），编辑器这条一直没传 —— 于是同一个隐患在 UI 里
    只能给一句笼统的警告，在 CLI 里却能指名道姓。

    这条门禁在编辑器这条路上**确实够得着**：补丁对不上 + 目标是新路径时，
    导出会退回"全量重生成"（见 `session_export.export` 里那段判断），
    而全量那份文本是不是真的带上了顶层内容，正是它要验的。
    """

    def _session(self, project, path):
        from storygraph.app.editor import EditorSession
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.story.beat_build import beats_from_parse_result
        s = EditorSession(project_root=project)
        s.script_path = path
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
        s.beats = beats_from_parse_result(pr)
        return s

    @staticmethod
    def _break_the_patch(s, monkeypatch):
        """把补丁那条路按成"对不上" → 导出会退回全量。"""
        monkeypatch.setattr(s, '_patch_note', lambda _text: {
            'used_patch': False, 'text': '',
            'blocking': ['对位率不足（模拟）'],
            'text_note': '（模拟：补丁对不上）', 'had_source': True})

    def test_loss_is_named_not_vague(self, tmp_path, monkeypatch):
        src = tmp_path / 'ch.rpy'
        src.write_text('# 文件头\nscreen hud():\n    text "hi"\n\n'
                       'label a:\n    e "hi"\n', encoding='utf-8')
        s = self._session(tmp_path, src)
        assert s.beats and s.beats[0].file_blocks, '顶层块没进拍，样本就错了'

        self._break_the_patch(s, monkeypatch)
        s.beats[0].file_blocks = ''            # 模拟"携带块丢了"
        res = s.export(tmp_path / 'out.rpy')   # 新路径 → 退回全量那条路

        assert res['ok'] is False, (
            f'少了 `screen hud` 却报告成功（门禁没拿到源文本）：{res}')
        assert 'OPAQUE_LOST' in res['codes'], res['codes']
        said = ' '.join(res.get('blocking') or [])
        assert 'screen hud' in said, f'没有指名道姓说丢了哪一行：{said}'

    def test_force_lets_it_through_but_still_reports(self, tmp_path, monkeypatch):
        """用户明确要求时放行 —— 但**清单照报**（沉默地丢才是 P0）。"""
        src = tmp_path / 'ch.rpy'
        src.write_text('screen hud():\n    text "hi"\n\nlabel a:\n    e "hi"\n',
                       encoding='utf-8')
        s = self._session(tmp_path, src)
        self._break_the_patch(s, monkeypatch)
        s.beats[0].file_blocks = ''
        out = tmp_path / 'out.rpy'
        res = s.export(out, force=True)
        assert res['ok'] is True, res
        assert out.exists()

    def test_a_faithful_full_path_is_not_blocked(self, tmp_path, monkeypatch):
        """
        **反面对照**：顶层块带着的时候，同样的退回全量必须放行。

        没有这一条，"门禁永远拦"也能让上面两条变绿 —— 那就成了"事事提醒"。
        """
        src = tmp_path / 'ch.rpy'
        src.write_text('screen hud():\n    text "hi"\n\nlabel a:\n    e "hi"\n',
                       encoding='utf-8')
        s = self._session(tmp_path, src)
        self._break_the_patch(s, monkeypatch)
        out = tmp_path / 'out.rpy'
        res = s.export(out)                    # 不 force，也不该被拦
        assert res['ok'] is True, res
        text = out.read_text(encoding='utf-8')
        assert 'screen hud():' in text and 'text "hi"' in text, text


class TestHiddenColumnContract:
    """
    载体 = **隐藏表**，不是隐藏列（2026-10-05 实测改的）。

    为什么：Excel 单元格上限 **32767 字符**，而真实文件的顶层区间轻松超过它
    —— `ball_moving_game.rpy` 38492、`my_perferences.rpy` 50426，塞进单元格
    **静默截断**（读回只剩 32767），于是"导出到新路径"丢东西且不报错。
    表是一行一行存的，没有这个长度限制。
    """

    def test_no_file_blocks_column(self):
        from storygraph.story.beat_model import (BEAT_FIELDS,
                                                 HIDDEN_BEAT_COLUMNS)
        assert 'FileBlocks' not in BEAT_FIELDS, BEAT_FIELDS
        assert 'FileBlocks' not in HIDDEN_BEAT_COLUMNS

    def test_sheet_roundtrip_through_the_workbook(self, tmp_path):
        """
        走完整工作簿往返：块必须一字不差地回来（含**超长**的那种）。

        比对时只看**行**（末尾那个换行不算）：载体是"一行一格"，
        末行的换行不承载任何语义 —— 文件拼接时本来也会再补一个。
        """
        from storygraph.story.beat_model import Beat
        from storygraph.story.excel_export import export_beats_to_excel
        from storygraph.story.excel_import import import_beats_from_excel

        # 造一个**超过单元格上限**的顶层区间（32767 字符）——
        # 这条用例存在的理由就是它
        big = 'init python:\n' + ''.join(
            f'    V{i} = {i}   # 填充，让这一段超过单元格上限\n'
            for i in range(1200))
        assert len(big) > 32767, len(big)

        beat = Beat(seq=1, label='a', text='x', file_blocks=big)
        xl = tmp_path / 'big.xlsx'
        export_beats_to_excel([beat], xl)
        back = import_beats_from_excel(xl)
        assert back, '表里一张拍都没有？'
        got = back[0].file_blocks or ''
        assert got.splitlines() == big.splitlines(), (
            f'超长顶层块在 Excel 往返里被改了/截断了：'
            f'{len(got)} vs {len(big)} 字符')
        assert len(got) > 32767, '读回来短于单元格上限 → 说明还是走了列那条路'

    def test_sheet_is_hidden_and_named(self, tmp_path):
        import openpyxl

        from storygraph.story.beat_model import (FILE_BLOCK_SHEET, Beat,
                                                 join_file_blocks)
        from storygraph.story.excel_export import export_beats_to_excel

        text = join_file_blocks([('init python:', 'def f():\n    return 1')])
        xl = tmp_path / 's.xlsx'
        export_beats_to_excel([Beat(seq=1, label='a', text='x',
                                   file_blocks=text)], xl)
        wb = openpyxl.load_workbook(xl)
        assert FILE_BLOCK_SHEET in wb.sheetnames, wb.sheetnames
        assert wb[FILE_BLOCK_SHEET].sheet_state == 'hidden', '这张表该整张隐藏'

    def test_candidate_refresh_keeps_the_carrier(self, tmp_path):
        """
        「刷新候选表」是**手改流程**里最常点的一个按钮 —— 它不许动这张表。

        `refresh_candidate_sheets` 只重写 `Candidates_*` / 词表 / 使用说明 /
        舞台快照，并在 Beats 表上重挂下拉。文件级保真表**不在**它管的范围里：
        一旦被顺手删掉，"刷新一下下拉"就会静默毁掉顶层内容。
        """
        from storygraph.story.beat_model import Beat, join_file_blocks
        from storygraph.story.excel_export import (export_beats_to_excel,
                                                   refresh_candidate_sheets)
        from storygraph.story.excel_import import import_beats_from_excel

        text = join_file_blocks([('init python:', 'def f():\n    return 1')])
        xl = tmp_path / 'r.xlsx'
        export_beats_to_excel([Beat(seq=1, label='a', text='x',
                                   file_blocks=text)], xl)

        class _EmptyRegistry:
            """空资源表：候选表写出来全是空下拉，够用就行。"""
            characters = images = transforms = {}
            music = sounds = voices = {}

        refresh_candidate_sheets(xl, _EmptyRegistry())
        back = import_beats_from_excel(xl)
        assert back and 'def f():' in (back[0].file_blocks or ''), (
            f'刷新候选表之后顶层块没了：{[b.file_blocks for b in back]}')

    def test_legacy_column_still_readable(self, tmp_path):
        """老表（那一版把整段塞在隐藏列里）仍要读得回来。"""
        import openpyxl

        from storygraph.story.beat_model import (LEGACY_FILE_BLOCK_COLUMN, Beat,
                                                 BEAT_FIELDS, BEAT_HEADERS)
        from storygraph.story.excel_export import export_beats_to_excel
        from storygraph.story.excel_import import import_beats_from_excel

        xl = tmp_path / 'old.xlsx'
        export_beats_to_excel([Beat(seq=1, label='a', text='x')], xl)
        wb = openpyxl.load_workbook(xl)
        ws = wb['Beats']
        # 手工补一列老格式的隐藏列（今天写表已不产出它）
        ws.cell(row=1, column=len(BEAT_FIELDS) + 1).value = \
            BEAT_HEADERS.get(LEGACY_FILE_BLOCK_COLUMN,
                             LEGACY_FILE_BLOCK_COLUMN)
        ws.cell(row=2, column=len(BEAT_FIELDS) + 1).value = \
            'init python:\n    LEGACY = 1'
        wb.save(xl)

        back = import_beats_from_excel(xl)
        assert back and 'LEGACY = 1' in (back[0].file_blocks or ''), \
            f'老表里的文件级块没读回来：{[b.file_blocks for b in back]}'

    def test_a_block_only_row_is_not_empty(self):
        """只有文件级块的行**不是空行** —— 否则"刷新候选表"会静默删掉它。"""
        from storygraph.story.beat_model import Beat
        assert not Beat(seq=1, label='', file_blocks='init python:\n    a = 1'
                        ).is_empty()

    def test_cell_does_not_carry_control_characters(self, tmp_path):
        """单元格里不许出现控制字符（`openpyxl` 会直接拒收）。"""
        from storygraph.story.beat_model import join_file_blocks
        text = join_file_blocks([('init python:', 'a = 1'),
                                 ('python early:', 'b = 2')])
        assert not re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', text), repr(text)

    def test_no_duplicate_emission(self, tmp_path):
        """同一份块被复制到两张拍上时，产物里**只写一次**（别重复定义）。"""
        from storygraph.story.beat_model import Beat, join_file_blocks
        from storygraph.story.beat_render import render_beats_to_rpy
        text = join_file_blocks([('init python:', 'def f():\n    return 1')])
        beats = [Beat(seq=1, label='a', text='x', file_blocks=text),
                 Beat(seq=2, label='a', text='y', file_blocks=text)]
        out = render_beats_to_rpy(beats)
        assert out.count('init python:') == 1, out
