"""
**嵌套菜单的渲染**（菜单套在选项体里）—— 见不变量 81。

为什么单开一份用例：`tests/` 里以前**一条嵌套菜单都没有**，
而"菜单套在选项体里"这条路上有整整三个缺陷叠在一起：

  1. `_menu_segments_by_path` 第二趟"凡 `menu_option` 一律跳过" →
     **嵌套菜单的选项行被静默丢掉**（菜单头还在、选项行没了 → 那层塌进外层体）；
  2. 选项体渲染把"非记账条件"的拍一律**扁平渲染**（`_indent_beat_lines`），
     嵌套菜单的头/选项行永远走不到递归的 `render_menu_block` → 变成一句台词；
  3. `_nest_plan` 用"数第几张 `KIND_MENU`"给菜单编号，可它会被递归调用在
     **子列表**上（选项体那一段），局部次序与 label 文档序对不上 →
     切组只切到"菜单头"一拍（`probe_nested_render.py` 的调用轨迹抓到的）。

三条都在 `tools/hell_nest_inline.py`（真实项目里 **0 个嵌套菜单**，
所以只靠真实项目回归测不到）。

判据：**逐字**比期望产物 + 两条路（直接渲染 / 过表）必须一致。
"""

from __future__ import annotations

import textwrap

import pytest

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

HEAD = '# 由剧本表自动生成，请勿在此文件手工改动结构'


def _beats(text: str, tmp_path):
    f = tmp_path / 'ch.rpy'
    f.write_text(text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return beats_from_parse_result(pr)


def _render(text: str, tmp_path, label: str = 't') -> str:
    """直接渲染（源码 → 拍 → 脚本）。"""
    return render_beats_to_rpy(_beats(text, tmp_path), label_name=label)


def _render_via_table(text: str, tmp_path) -> str:
    """过表渲染（源码 → 拍 → Excel → 拍 → 脚本）。"""
    beats = _beats(text, tmp_path)
    xl = tmp_path / 'ch.xlsx'
    export_beats_to_excel(beats, xl)
    return render_beats_to_rpy(import_beats_from_excel(xl))


def _body(rpy: str) -> str:
    lines = [ln for ln in rpy.splitlines()
             if ln.strip() and not ln.startswith(HEAD)]
    return '\n'.join(lines)


def _nested_menu_source(levels: int) -> str:
    """`levels` 层菜单套娃：每层一个选项，最里面一句台词。"""
    out = ['label t:', '    menu:', '        "L1":']
    pad = 12
    for k in range(2, levels + 1):
        out.append(' ' * pad + f'menu:')
        out.append(' ' * (pad + 4) + f'"L{k}":')
        pad += 8
    out.append(' ' * pad + 'e "最里面"')
    return '\n'.join(out) + '\n'


class TestTheExactProduct:
    """两层（最小复现）：逐字比期望产物。"""

    SRC = '''label t:
    menu:
        "外":
            e "前"
            menu:
                "内":
                    e "深深"
            e "后"
'''

    EXPECTED = '''label t:
    menu:
        "外":
            e "前"
            menu:
                "内":
                    e "深深"
            e "后"
    return'''

    def test_direct_render(self, tmp_path):
        assert _body(_render(self.SRC, tmp_path)) == self.EXPECTED

    def test_table_path_is_identical(self, tmp_path):
        assert _render_via_table(self.SRC, tmp_path) == _render(self.SRC, tmp_path)

    def test_no_line_becomes_dialogue(self, tmp_path):
        """回归：从前嵌套的 `menu:` / `"内":` 会变成一句台词（`" menu"` / `"内"`）。"""
        rpy = _render(self.SRC, tmp_path)
        assert '" menu"' not in rpy, rpy
        assert rpy.count('menu:') == 2, rpy


class TestArbitraryDepth:
    """**要几层有几层** —— 每加一层，选项行就深 8 格，最里面那句在末尾。"""

    @pytest.mark.parametrize('levels', [2, 3, 4, 5, 6])
    def test_each_level_is_indented_one_more(self, levels, tmp_path):
        rpy = _render(_nested_menu_source(levels), tmp_path)
        lines = rpy.splitlines()
        for k in range(1, levels + 1):
            want = f'"{("L%d" % k)}":'
            line = next((ln for ln in lines if ln.strip() == want), None)
            assert line is not None, f'第 {k} 层选项不在产物里：\n{rpy}'
            # 每多一层，选项行就深 **8** 格（菜单头 +4、它自己的选项体 +4）
            assert len(line) - len(line.lstrip()) == 8 + 8 * (k - 1), \
                f'第 {k} 层缩进不对：{line!r}\n{rpy}'
        # 菜单头的层数 = 层数；每个头都比它自己的选项浅 4 格
        heads = [ln for ln in lines if ln.strip() == 'menu:']
        assert len(heads) == levels, rpy
        for k, head in enumerate(heads, start=1):
            assert len(head) - len(head.lstrip()) == 4 + 8 * (k - 1), \
                f'第 {k} 层菜单头缩进不对：{head!r}\n{rpy}'
        deep = next(ln for ln in lines if '最里面' in ln)
        assert len(deep) - len(deep.lstrip()) == 12 + 8 * (levels - 1), rpy

    @pytest.mark.parametrize('levels', [2, 4, 6])
    def test_table_path_matches_at_depth(self, levels, tmp_path):
        src = _nested_menu_source(levels)
        assert _render_via_table(src, tmp_path) == _render(src, tmp_path)

    @pytest.mark.parametrize('levels', [3, 5])
    def test_it_is_a_fixed_point(self, levels, tmp_path):
        """产物再解析一遍、再渲染，必须**逐字节**不变（注释不翻倍、缩进不漂移）。"""
        first = _render(_nested_menu_source(levels), tmp_path)
        again = _render(first, tmp_path)
        assert _body(again) == _body(first), f'{_body(first)}\n---\n{_body(again)}'


class TestMixedWithConditions:
    """嵌套菜单与 `if` 交错（真实剧本里更常见的那种）。"""

    SRC = '''label t:
    if outer:
        menu:
            "选":
                if inner:
                    menu:
                        "深":
                            e "底"
'''

    def test_direct_render_keeps_both_levels(self, tmp_path):
        rpy = _render(self.SRC, tmp_path)
        assert rpy.count('menu:') == 2, rpy
        assert 'if outer:' in rpy, rpy
        assert '"深":' in rpy and 'e "底"' in rpy, rpy
        # 最里面那句必须比它上面的选项深 4 格
        deep = next(ln for ln in rpy.splitlines() if 'e "底"' in ln)
        opt = next(ln for ln in rpy.splitlines() if '"深":' in ln)
        assert len(deep) - len(deep.lstrip()) == \
            len(opt) - len(opt.lstrip()) + 4, rpy

    def test_two_paths_agree(self, tmp_path):
        assert _render_via_table(self.SRC, tmp_path) == _render(self.SRC, tmp_path)


class TestMenuConditionWrapping:
    """菜单整块挂在 `if` 下时，`if` 要在 `menu:` **外面**（嵌套时也一样）。"""

    SRC = '''label t:
    menu:
        "外":
            if flag:
                menu:
                    "内":
                        e "x"
'''

    def test_the_wrapper_goes_outside_the_nested_menu(self, tmp_path):
        rpy = _render(self.SRC, tmp_path)
        lines = [ln for ln in rpy.splitlines() if ln.strip()]
        heads = [i for i, ln in enumerate(lines) if ln.strip() == 'menu:']
        assert len(heads) == 2, rpy
        i_head = heads[1]                      # 嵌套那一层的头
        # `if flag:` 在**菜单外面**：块头比块体浅一级（Ren'Py 的缩进规则）
        prev = lines[i_head - 1]
        assert prev.strip().startswith('if '), rpy
        assert len(prev) - len(prev.lstrip()) + 4 == \
            len(lines[i_head]) - len(lines[i_head].lstrip()), rpy


class TestConditionContextIsNotRepeated:
    """
    **条件不许翻倍**（N2 不动点；2026-10-05）。

    解析器把外层条件 `_combine` 进了里层每一拍，而外层那个 `if:` 往往已经由
    结构写出来了（菜单块自己的条件写在 `menu:` 外面）。里层再写一遍
    `(flag0) and (flag2)`，产物再解析就会被**再累加**一次 → 每轮更长。
    """

    SRC = '''label t:
    if flag0:
        menu:
            "选项1":
                if flag2:
                    e "叶子"
'''

    def test_the_outer_condition_is_not_repeated(self, tmp_path):
        rpy = _render(self.SRC, tmp_path)
        conds = [ln.strip() for ln in rpy.splitlines()
                 if ln.strip().startswith('if ')]
        assert conds.count('if flag0:') == 1, rpy
        assert 'if flag2:' in conds, rpy
        assert not any('flag0' in c for c in conds if c != 'if flag0:'), rpy

    def test_it_is_a_fixed_point(self, tmp_path):
        first = _render(self.SRC, tmp_path)
        again = _render(first, tmp_path)
        assert _body(again) == _body(first), f'{_body(first)}\n---\n{_body(again)}'

    def test_the_context_strip_is_conservative(self):
        """减不干净就**原样返回** —— 宁可多写一层，也不许漏掉一个合取项。"""
        from storygraph.story.beat_render import strip_condition_context as sc
        assert sc('(a) and (b)', 'a') == 'b'
        assert sc('(a) and (b)', '(a)') == 'b'
        assert sc('a', 'a') == ''
        assert sc('(a) and (b)', 'zzz') == '(a) and (b)'   # 减不掉 → 原样
        assert sc('(a) and (b)', '') == '(a) and (b)'
        # 外层写着 a and c：那个 a 已经被外层 if 覆盖，只剩 b
        assert sc('(a) and (b)', 'a and c') == 'b'
        assert sc('(a) and (b) and (c)', 'a') == 'b and c'

    # ------------------------------------------------------------------
    #  **嵌套的合取式**（2026-10-05 修，`depth5_imimi` 的 N2 不动点）
    #
    #  条件是被 `_combine` 层层包起来的：`((a) and (b)) and (c)`。
    #  按字面 `split(' and ')` 会切进括号里面，于是：
    #    · 减不掉 → 条件每轮翻倍（不动点过不去）；
    #    · 拼回去**括号不配对** → 写进 `if` 就是产物语法错。
    # ------------------------------------------------------------------

    def test_a_nested_conjunction_is_stripped(self):
        from storygraph.story.beat_render import strip_condition_context as sc
        assert sc('((a) and (b)) and (c)', '(a) and (b)') == 'c'
        assert sc('((a) and (b)) and (((a) and (b)) and (c))',
                  '(a) and (b)') == 'c'
        assert sc('((a)) and (b)', 'a') == 'b'

    def test_not_and_parenthesised_or_are_single_atoms(self):
        """`not (a and b)` / `(a or b)` 是**一个**原子项 —— 不许被切开。"""
        from storygraph.story.beat_render import strip_condition_context as sc
        assert sc('not (a and b) and (c)', 'c') == 'not (a and b)'
        assert sc('(a or b) and (c and d)', '(a or b)') == 'c and d'
        # 减不掉就原样（`not (a and b)` 与 `a` 不是一回事）
        assert sc('(a) and (b)', 'not (a and b)') == '(a) and (b)'

    def test_an_or_atom_keeps_its_parenthesis(self):
        """
        `and` 比 `or` 紧：`x and (a or b)` **不许**被拉平成 `x and a or b`
        （那等于 `(x and a) or b`，语义改了）。
        """
        from storygraph.story.beat_render import strip_condition_context as sc
        # 整条就是它 → 顶层没有 `and`，脱括号安全
        assert sc('(x) and (a or b)', 'x') == 'a or b'
        assert sc('(x) and (a or b) and (c)', 'x') == '(a or b) and c'

    def test_the_strip_never_returns_a_broken_parenthesis(self):
        """
        产出必须**括号平衡**：旧实现在真实数据上返回过 `'(((a) and (c))'`
        —— 那是直接写进 `if …:` 的语法错，比"多写一层条件"坏得多。
        """
        from storygraph.story.beat_render import strip_condition_context as sc
        cases = [
            ('((a) and (b)) and (c)', '(a) and (b)'),
            ('((a) and (b)) and (((a) and (b)) and (c))',
             '((a) and (b)) and (b)'),
            ('((not ((a))) and (b)) and (c)', '(not ((a))) and (b)'),
        ]
        for cond, ctx in cases:
            got = sc(cond, ctx)
            assert got.count('(') == got.count(')'), (cond, ctx, got)

    def test_the_atoms_helper_does_not_recurse_forever(self):
        """
        `_condition_atoms` 必须**一份实现**、且对 `not (a and b)` 终止。

        回归：从前 `beat_render` 里有两份各自递归的实现，其中一份遇到
        `not (a and b)` 会无限递归（`RecursionError`）。
        """
        from storygraph.story.beat_render import (_condition_atoms,
                                                  _condition_is_refinement)
        assert _condition_atoms('not (a and b)') == {'not (a and b)'}
        assert _condition_atoms('((a) and (b)) and (c)') == {'a', 'b', 'c'}
        assert _condition_is_refinement('((a) and (b)) and (c)', '(a) and (b)')
        assert not _condition_is_refinement('not ((a))', '(a) and (b)')

    def test_the_five_level_mixed_nesting_is_a_fixed_point(self, tmp_path):
        """
        5 层混合嵌套（`if → menu → if → menu → if`）必须**逐字节**不动点。

        这是 `tools/hell_nest_inline.py` 的 `depth5_imimi` 用例缩小版：
        第 4 层那个 `if` 里的条件原本是 `((flag0) and (flag2)) and (flag4)`，
        再解析一轮就变成
        `((flag0) and (flag2)) and (((flag0) and (flag2)) and (flag4))`
        —— 每轮更长。
        """
        src = ('label t:\n'
               '    if flag0:\n'
               '        menu:\n'
               '            "选项1":\n'
               '                if flag2:\n'
               '                    menu:\n'
               '                        "选项1":\n'
               '                            if flag4:\n'
               '                                e "叶子"\n')
        first = _render(src, tmp_path)
        again = _render(first, tmp_path)
        assert _body(again) == _body(first), \
            f'{_body(first)}\n---\n{_body(again)}'
        # 外层结构已经写了 flag0 / flag2，里层只该剩 flag4
        assert 'if flag4:' in first, first
        assert first.count('flag0') == 1, first
