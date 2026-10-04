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
