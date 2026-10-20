"""
结构位（`Nest` 列）—— **唯一结构真源**（2026-10-19 收口）。

历史：B 阶段一曾把"这条拍属于哪个选项"同时写进表和解析器，再用对拍器
（`nest_placement_conflicts`）证明两者处处一致。`the_question` 的
`jump` 选项体证明老推断会摆错（结构列 `menu:1>B`，老推断摆到顶层），
于是 2026-10-19 定案：**留结构位、删对拍器**。渲染在路径齐全时直接按
结构位建树；老推断只在"整份 label 都没有结构位"（老表）时兜底。

三层各测一遍：
  · **解析器**：`Statement.nest_path` 的取值（块路径 DSL）；
  · **列**：`Nest` 随 Excel 往返（含"顶层"与"不知道"必须分得开）；
  · **路径权威**：往返之后 `jump` 仍在选项体里；对拍器已不存在。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_model import (BEAT_FIELDS, BEAT_HEADERS,
                                         SINGLE_LINE_BEAT_FIELDS, Beat,
                                         HIDDEN_BEAT_COLUMNS)
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

REAL_PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')

SRC = '''label a:
    e "顶层"
    if outer:
        menu:
            "选项 A":
                e "体 1"
                if flag:
                    e "更深"
            "选项 B":
                e "体 2"
    e "收尾"
'''

#: 期望的块路径（从左到右就是文件的书写顺序）
WANT_PATHS = ['', 'if:1', 'if:1>menu:1', 'if:1>menu:1>A',
              'if:1>menu:1>A>if:2', 'if:1>menu:1', 'if:1>menu:1>B', '']


def _beats_of(text: str, tmp: Path) -> list:
    f = tmp / 'ch.rpy'
    f.write_text(text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return beats_from_parse_result(pr)


class TestParserWritesThePath:
    def test_paths_are_document_order(self, tmp_path):
        beats = _beats_of(SRC, tmp_path)
        assert [b.nest_path for b in beats] == WANT_PATHS

    def test_option_line_is_in_the_menu_but_not_in_a_body(self, tmp_path):
        """选项行 = `…>menu:1`；选项体里的拍 = `…>menu:1>A`（多一段）。"""
        beats = _beats_of(SRC, tmp_path)
        opt = next(b for b in beats if b.text == '选项 A')
        body = next(b for b in beats if b.text == '体 1')
        assert opt.nest_path == 'if:1>menu:1'
        assert body.nest_path == 'if:1>menu:1>A'

    def test_nested_menu_keeps_the_outer_chain(self, tmp_path):
        src = ('label a:\n'
               '    menu:\n'
               '        "外":\n'
               '            menu:\n'
               '                "内":\n'
               '                    e "深处"\n')
        beats = _beats_of(src, tmp_path)
        deep = next(b for b in beats if b.text == '深处')
        # 外层菜单是第 1 个，内层是第 2 个（文档序在**整个 label** 里数）
        assert deep.nest_path == 'menu:1>A>menu:2>A', [b.nest_path for b in beats]

    def test_it_is_a_runtime_attribute_not_a_field(self, tmp_path):
        """
        结构位**不是** `Statement` 的 dataclass 字段（B 阶段一沿用
        `_menu_member` 的做法）：Statement 的相等/哈希是"内容指纹"契约的一部分，
        结构位进去会改指纹 —— 那是另一条会静默伤人的路。
        """
        from dataclasses import fields

        from storygraph.core.statement import Statement
        names = {f.name for f in fields(Statement)}
        assert 'nest_path' not in names
        assert '_menu_member' not in names


class TestTheColumn:
    def test_column_is_hidden_single_line_and_last(self):
        # `Inline`（C）/ `Opaque`（M1）/ `SpecName`（M3）/ `SpecHead`（M4）/
        # `LoopHeader`（D2）/ `CameraHeader`（2026-10-07）/ `SayTail`
        # （台词后缀，2026-10-18）/ `LabelHeader`（label 参数，同日晚）/
        # `MenuName` + `MenuArgs`（菜单头，2026-10-19）
        # 依次排在 `Nest` 后面，所以这里只要求"在尾部几列里"，
        # 并钉住最新那一列是谁。
        assert 'Nest' in BEAT_FIELDS[-12:], BEAT_FIELDS[-12:]
        assert BEAT_FIELDS[-1] == 'MenuArgs', BEAT_FIELDS
        assert 'Nest' in HIDDEN_BEAT_COLUMNS
        assert 'Nest' in SINGLE_LINE_BEAT_FIELDS, '路径必须是一行'
        assert BEAT_HEADERS['Nest']

    def test_top_level_and_unknown_are_distinguishable(self):
        """
        `''`（顶层，**有信息**）与 `None`（不知道，老表）必须分得开 ——
        对拍器的判据全靠这个（拿"不知道"当"顶层"会天天假报）。
        """
        top = Beat(seq=1, label='a', text='x', nest_path='')
        unknown = Beat(seq=2, label='a', text='y', nest_path=None)
        row_top = dict(zip(BEAT_FIELDS, top.to_row()))
        row_unk = dict(zip(BEAT_FIELDS, unknown.to_row()))
        assert row_top['Nest'] == '-', '顶层要写成一个**有信息**的记号'
        assert row_unk['Nest'] == '', '不知道要留空'
        assert Beat.from_row(row_top).nest_path == ''
        assert Beat.from_row(row_unk).nest_path is None

    def test_dirty_cell_means_unknown(self):
        """
        结构位被清洗过（有换行/控制字符）→ 按"**不知道**"处理。

        拿一条残缺路径去指认"结构对不上"就是假阳性，而假阳性比漏报贵。
        """
        row = dict(zip(BEAT_FIELDS, Beat(seq=1, label='a', text='x').to_row()))
        row['Nest'] = 'if:1\nmenu:2'
        back = Beat.from_row(row)
        assert back.nest_path is None
        assert 'Nest' in (back.sanitized_cells or []), '清洗必须留痕（W114）'

    def test_roundtrip_through_the_workbook(self, tmp_path):
        beats = _beats_of(SRC, tmp_path)
        xl = tmp_path / 'ch.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)
        assert [b.nest_path for b in back] == WANT_PATHS


class TestPathAuthority:
    """
    **2026-10-19 收口**：结构位是唯一结构真源，老推断不再当第二套判据。

    背景（`the_question` 实测）：选项体里只有一条 `jump` 时，表格往返之后
    `_menu_member` 没了、`Condition` 也是空的 —— 老推断把它摆到**顶层**，
    而结构列写的是 `menu:1>B`。哪个对？**结构列对**：渲染出来的
    `jump` 确实在选项体里。旧的对拍器（`nest_placement_conflicts`）就是
    拿"会摆错的那一套"去质疑结构位，已随旧口径一起删除。
    """

    def test_no_second_oracle_exists(self):
        from storygraph.story import beat_render
        assert not hasattr(beat_render, 'nest_placement_conflicts')
        assert not hasattr(beat_render, '_actual_chains')

    def test_jump_only_option_body_stays_inside_after_roundtrip(self, tmp_path):
        """
        `the_question/script.rpy:28-37` 的最小复刻：菜单标题 + 两个只含
        `jump` 的选项体。表格往返之后，`jump` 必须在**选项体里面**。
        """
        src = ('label start:\n'
               '    menu:\n'
               '\n'
               '        "As soon as she catches my eye, I decide...":\n'
               '\n'
               '        "To ask her right away.":\n'
               '\n'
               '            jump rightaway\n'
               '\n'
               '        "To ask her later.":\n'
               '\n'
               '            jump later\n')
        beats = _beats_of(src, tmp_path)
        xl = tmp_path / 'ch.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)
        assert all(getattr(b, '_menu_member', None) is None for b in back), \
            '前提：表格往返之后老推断的信息没了'
        rpy = render_beats_to_rpy(back, label_name='start')
        lines = rpy.splitlines()
        i = next(n for n, ln in enumerate(lines) if ln.strip() == 'menu:')
        body = [ln.strip() for ln in lines[i + 1:] if ln.strip()]
        # 两个选项行 + 两个 jump，且 jump 的缩进必须比选项行深
        jump_lines = [ln for ln in lines[i + 1:] if 'jump rightaway' in ln]
        assert len(jump_lines) == 1, rpy
        opt = next(n for n, ln in enumerate(lines)
                   if ln.strip() == '"To ask her right away.":')
        jump = lines.index(jump_lines[0])
        assert jump > opt, rpy
        assert len(lines[jump]) - len(lines[jump].lstrip()) > \
            len(lines[opt]) - len(lines[opt].lstrip()), rpy

    def test_a_malformed_top_level_path_is_not_silently_lost(self, tmp_path):
        """
        路径**自相矛盾**时（菜单组中间出现一条顶层路径）不能静默丢拍：
        渲染退回老推断把它收进选项体，至少内容还在。

        这不是"第二套判断"复活：只有在这一份 label 的路径**根本解释不通**
        （菜单跨度里夹着顶层拍）时才会走到；`the_question` 那种
        "路径完整、老推断会摆错"的形状**不会**触发它（见上一条）。
        """
        beats = _beats_of(SRC, tmp_path)
        body = next(b for b in beats if b.text == '体 1')
        body.nest_path = ''                       # 人为制造自相矛盾
        rpy = render_beats_to_rpy(beats, label_name='a')
        assert 'e "体 1"' in rpy, rpy


@pytest.mark.skipif(not REAL_PROJECT.exists(), reason='真实项目不在本机')
class TestRealProjectAgrees:
    """真实项目的结构位必须**每拍都有**（没有路径 = 没法用路径建树）。"""

    def test_every_beat_agrees_and_has_a_path(self):
        files = sorted(f for f in (REAL_PROJECT / 'game').rglob('*.rpy')
                       if not f.name.endswith('.generated.rpy'))
        missing = []
        for f in files:
            pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
            beats = beats_from_parse_result(pr)
            if not beats:
                continue
            # 有源码行号、却没有结构位 = 解析器漏记（携带拍本来就没有源语句，
            # 不算）
            missing += [(f.name, b.kind, b.label) for b in beats
                        if getattr(b, 'nest_path', None) is None
                        and getattr(b, 'src_first', 0)]
        assert not missing, f'这些拍有源行号却没有结构位：{missing[:5]}'


class TestACommentNeverStealsTheStructure:
    """
    注释**不许改写它所搭载那一拍的结构位**（2026-10-05 修）。

    真实形态（`mini_game1.rpy` 第 116-119 行）：

        if fail_round >= 3:          ← 分支体（有条件的那一段）
            python:
                fsflag = True
        # 生成选项（1个正确，3个错误）  ← **顶层**注释
        python:
            wrong_answers = []

    从前 `_emit_comment` 既不比条件、也不落拍，于是那条顶层注释被吸进
    **上面那张条件拍**的 pending 里，两个后果：

      · 那一拍的结构位被注释的路径**覆盖**（条件还是分支的，位置却成了顶层）
        —— 隐藏列 `Nest` 说的位置就不真了；
      · 注释渲染在该拍 `if …:` 的**前面**（位置从"分支之后"漂到"分支之前"），
        产物再解析时它又会被拆成单独一拍。
    """

    SRC = '''label t:
    if fail:
        python:
            fsflag = True
    # 生成选项
    python:
        wrong = []
'''

    def test_the_path_stays_with_the_executable_statement(self, tmp_path):
        beats = _beats_of(self.SRC, tmp_path)
        code = next(b for b in beats if 'fsflag' in (b.code or ''))
        assert code.condition == 'fail'
        assert code.nest_path == 'if:1', \
            f'条件在分支里，结构位却成了 {code.nest_path!r}'
        assert '生成选项' not in (code.comment or ''), \
            '顶层注释不该并进条件拍'

    def test_the_comment_keeps_its_place_in_the_product(self, tmp_path):
        rpy = render_beats_to_rpy(_beats_of(self.SRC, tmp_path), label_name='t')
        lines = [ln for ln in rpy.splitlines() if ln.strip()]
        i_comment = next(i for i, ln in enumerate(lines) if '生成选项' in ln)
        i_if = next(i for i, ln in enumerate(lines) if ln.strip() == 'if fail:')
        # 注释在 `if` **块之后**（原文就在分支后面），而不是被搬到 `if` 前面
        assert i_comment > i_if, rpy

    def test_it_is_a_roundtrip_fixed_point(self, tmp_path):
        def _strip(text: str) -> str:
            # 生成标记行每次渲染都会重写，比对前滤掉（`hell_nest_inline._norm`
            # 同一口径），其余一个字都不放过
            lines = [ln.rstrip() for ln in text.splitlines()
                     if not ln.startswith('# 由剧本表自动生成')]
            while lines and not lines[0]:
                lines.pop(0)
            while lines and not lines[-1]:
                lines.pop()
            return '\n'.join(lines)

        beats = _beats_of(self.SRC, tmp_path)
        first = render_beats_to_rpy(beats, label_name='t')
        again = render_beats_to_rpy(_beats_of(first, tmp_path), label_name='t')
        assert _strip(again) == _strip(first), f'{first}\n---\n{again}'

    def test_the_two_paths_agree_through_the_workbook(self, tmp_path):
        beats = _beats_of(self.SRC, tmp_path)
        xl = tmp_path / 'ch.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)
        assert (render_beats_to_rpy(back, label_name='t')
                == render_beats_to_rpy(beats, label_name='t'))


class TestAStatementCannotStealTheStructure:
    """
    **位置不同的语句不许并进同一拍**（2026-10-05 修，结构守卫）。

    报到的形态是"选项文案带换行 → 第二轮选项体变 `pass`、丢内容"，
    探针（`tools/probe_body_leak.py`）打出的是更狠的同根因：

        label a:
            menu:
                "选项":
                    show bg room      ← 路径 `menu:1>A`（选中才播）
            e "体"                    ← 路径 `''`
            return

    两者**条件都是空串**，而 `branch_guard` 只比条件 → 并成一拍 →
    整拍的 `Nest` 被最后那条语句覆盖成 `''` → 生效时

        menu:
            "选项":
                pass              ← 选项体被掏空
        show bg room              ← **无条件执行**（选没选都会换背景）

    这条缺陷**不是**"对拍器漏报"：`_menu_member` 与 `Nest` 都被同一条语句
    覆盖，两份载体"自洽"地说了同一个谎；对拍器已删，这类由构建层挡住
    （`struct_guard`）。
    """

    SRC = '''label a:
    menu:
        "选项":
            show bg room
    e "体"
    return
'''

    def test_the_stage_stays_inside_the_option_body(self, tmp_path):
        beats = _beats_of(self.SRC, tmp_path)
        show = next(b for b in beats if b.show)
        assert show.nest_path == 'menu:1>A', \
            f'舞台指令漏出选项体了：{show.nest_path!r}'
        assert show.text == '', '它不该被并进菜单外那句台词里'
        say = next(b for b in beats if b.text == '体')
        assert say.nest_path == ''

    def test_the_product_keeps_it_conditional(self, tmp_path):
        rpy = render_beats_to_rpy(_beats_of(self.SRC, tmp_path), label_name='a')
        lines = [ln for ln in rpy.splitlines() if ln.strip()]
        i_menu = next(i for i, ln in enumerate(lines) if ln.strip() == 'menu:')
        i_body = next(i for i, ln in enumerate(lines)
                      if 'show bg room' in ln)
        i_say = next(i for i, ln in enumerate(lines) if ln.strip() == 'e "体"')
        assert i_menu < i_body < i_say, rpy
        # 选项体那一层缩进必须比 `menu:`/台词深（在体内，不在菜单外）
        indent = len(lines[i_body]) - len(lines[i_body].lstrip())
        assert indent >= 12, rpy
        assert 'pass' not in rpy, f'选项体被掏空了：\n{rpy}'

    def test_it_survives_the_excel_roundtrip(self, tmp_path):
        beats = _beats_of(self.SRC, tmp_path)
        xl = tmp_path / 'ch.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)
        direct = render_beats_to_rpy(beats, label_name='a')
        via_table = render_beats_to_rpy(back, label_name='a')
        assert via_table == direct, f'{direct}\n---\n{via_table}'
        # 过表那条路同样要**在体内**（缩进 ≥12 = menu 里选项体那一层）
        body = next(ln for ln in via_table.splitlines() if 'show bg room' in ln)
        assert len(body) - len(body.lstrip()) >= 12, via_table

    def test_a_filler_pass_does_not_leak_out(self, tmp_path):
        """
        空选项体补的 `pass` 在**体内**，不许被并进菜单外那张拍 ——
        它的形态是"第二轮凭空多出一个顶层 `pass`"（不动点过不去）。
        """
        src = ('label a:\n'
               '    menu:\n'
               '        "空的":\n'
               '    e "体"\n'
               '    return\n')
        beats = _beats_of(src, tmp_path)
        rpy = render_beats_to_rpy(beats, label_name='a')
        again = render_beats_to_rpy(_beats_of(rpy, tmp_path), label_name='a')

        def _norm(text: str) -> str:
            lines = [ln.rstrip() for ln in text.splitlines()
                     if not ln.startswith('# 由剧本表自动生成')]
            while lines and not lines[0]:
                lines.pop(0)
            while lines and not lines[-1]:
                lines.pop()
            return '\n'.join(lines)

        assert _norm(again) == _norm(rpy), f'{rpy}\n---\n{again}'
        assert rpy.count('pass') == 1, rpy

