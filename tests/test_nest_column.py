"""
结构位（`Nest` 列）与**对拍器** —— B 阶段一，见不变量 75。

阶段一的目标只有一句话：**把"这条拍属于哪个选项"写进表、并且证明它和老推断
处处一致**。这一步**不改任何行为**（渲染仍按老推断走），因为刚验收过的
往返契约不该在同一轮里被换掉。

三层各测一遍：
  · **解析器**：`Statement.nest_path` 的取值（块路径 DSL）；
  · **列**：`Nest` 随 Excel 往返（含"顶层"与"不知道"必须分得开）；
  · **对拍器**：`nest_placement_conflicts` —— 一致时不报、不一致时报到拍级，
    而且**只对结构位齐全的 label 判**（老表整份跳过，绝不拿"不知道"当"顶层"）。
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
from storygraph.story.beat_render import (nest_placement_conflicts,
                                          render_beats_to_rpy)
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
        # `LoopHeader`（D2）/ `CameraHeader`（2026-10-07）依次排在 `Nest`
        # 后面，所以这里只要求"在尾部几列里"，并钉住最新那一列是谁。
        assert 'Nest' in BEAT_FIELDS[-8:], BEAT_FIELDS[-8:]
        assert BEAT_FIELDS[-1] == 'CameraHeader', BEAT_FIELDS
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


class TestComparator:
    def test_consistent_source_is_silent(self, tmp_path):
        assert nest_placement_conflicts(_beats_of(SRC, tmp_path)) == []

    def test_unknown_structure_skips_the_whole_label(self, tmp_path):
        """老表（没有结构位）→ 整份跳过，**不许**报。"""
        beats = _beats_of(SRC, tmp_path)
        for b in beats:
            b.nest_path = None
        assert nest_placement_conflicts(beats) == []

    def test_a_lying_column_is_reported_per_beat(self, tmp_path):
        """
        结构列说'这条拍不在任何选项体里'，而老推断把它摆进了选项体 → 报出来。

        （现实里方向通常相反 —— 表丢了结构、产物把选项体压平；两个方向都算
        "两套判断不一致"，所以都要报。）
        """
        beats = _beats_of(SRC, tmp_path)
        body = next(b for b in beats if b.text == '体 1')
        body.nest_path = ''                       # 谎称在顶层
        conf = nest_placement_conflicts(beats)
        assert conf, '谎报的结构位没被抓到'
        name, idx, claim, got = conf[0]
        assert name == 'a'
        assert claim == () and got == ((1, 'A'),), (claim, got)

    def test_excel_roundtrip_keeps_the_comparison_honest(self, tmp_path):
        """表格往返之后（`_menu_member` 已经没了）对拍仍然一致。"""
        beats = _beats_of(SRC, tmp_path)
        xl = tmp_path / 'ch.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)
        assert all(getattr(b, '_menu_member', None) is None for b in back), \
            '前提：往返之后老那位就没了'
        assert nest_placement_conflicts(back) == []


@pytest.mark.skipif(not REAL_PROJECT.exists(), reason='真实项目不在本机')
class TestRealProjectAgrees:
    """
    **阶段一的验收判据**：40 个真实文件里，老推断与结构列**处处一致**。

    全绿 → 阶段二（让渲染改用结构列）才是安全的；有一条不一致就得先查清
    是"解析器记错"还是"老推断摆错"。
    """

    def test_every_beat_agrees_and_has_a_path(self):
        files = sorted(f for f in (REAL_PROJECT / 'game').rglob('*.rpy')
                       if not f.name.endswith('.generated.rpy'))
        conflicts, missing = [], []
        for f in files:
            pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
            beats = beats_from_parse_result(pr)
            if not beats:
                continue
            conflicts += [(f.name, c) for c in nest_placement_conflicts(beats)]
            # 有源码行号、却没有结构位 = 解析器漏记（携带拍本来就没有源语句，
            # 不算）
            missing += [(f.name, b.kind, b.label) for b in beats
                        if getattr(b, 'nest_path', None) is None
                        and getattr(b, 'src_first', 0)]
        assert not missing, f'这些拍有源行号却没有结构位：{missing[:5]}'
        assert not conflicts, f'老推断与结构列对不上：{conflicts[:5]}'


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

    对拍器**看不见**这一类：`_menu_member` 与 `Nest` 都被同一条语句覆盖，
    两份载体"自洽"地说了同一个谎。所以它得由构建层挡住（`struct_guard`）。
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

