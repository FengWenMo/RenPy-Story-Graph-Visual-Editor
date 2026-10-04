"""
B 阶段二：**渲染器优先用结构列建树** —— 见不变量 76。

阶段二只改"**怎么判**"，不改"判成什么"：
  · 结构位齐全 → 菜单组边界与选项归属**由块路径直接决定**；
  · 有一条"不知道"（`nest_path is None`：老表 / 手工加的行）→ **那一份整份**
    退回老推断（`_menu_member` / 条件 / 行号）。混着用会出现"半截菜单"。

三件必须钉住的事：

  ① **真实产物零变化**：老推断在真实项目上本来就摆对了，切过去不许动产物
     （`tools/render_hash.py` 实测 40 个文件、两条路逐字节一致）；
  ② **老推断摆错的地方要修对**：`menu>if` 这种"选项体里有第二条语句 / 嵌套块"
     的形状，以前会产出 `"选项": pass` + 内容提到菜单外（**选没选都会执行**）；
  ③ **对拍器仍要有意义**：它算"老推断会怎么摆"时必须**显式走老路**
     （`use_paths=False`），否则成了拿路径比路径、永远 0 冲突。
"""

from __future__ import annotations

from pathlib import Path

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story import beat_render as br
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_model import (Beat, KIND_MENU, KIND_MENU_OPTION,
                                         KIND_NARRATION)
from storygraph.story.beat_render import (nest_placement_conflicts,
                                          render_beats_to_rpy)
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

#: `menu>if`：选项体里有嵌套 `if`（老路会把它压平）
MENU_THEN_IF = '''label a:
    e "开场"
    menu:
        "选项A":
            if flag:
                e "选了且 flag"
            e "选项内收尾"
    e "菜单之后"
'''

#: `if>menu`：菜单后面、`if` 里面还有一句（老路会把它吸进选项体）
IF_THEN_MENU = '''label a:
    e "开场"
    if flag:
        menu:
            "选项A":
                e "选了"
        e "if 内收尾"
    e "菜单之后"
'''

#: 无条件菜单：老推断的收组逻辑会把"手工插进来的拍"收进组里（回归用例用它）
PLAIN_MENU = '''label a:
    e "开场"
    menu:
        "选项A":
            "A 的内容"
        "选项B":
            "B 的内容"
    "菜单之后"
'''

#: 选项体里"普通拍与条件拍交错"（老路的两趟渲染会换序）
INTERLEAVED = '''label a:
    menu:
        "选项A":
            e "第一句"
            if c1:
                e "第二句"
            e "第三句"
'''


def _beats(text: str, tmp: Path) -> list:
    f = tmp / 'ch.rpy'
    f.write_text(text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return beats_from_parse_result(pr)


def _via_table(beats: list, tmp: Path) -> list:
    xl = tmp / 'ch.xlsx'
    export_beats_to_excel(beats, xl)
    return import_beats_from_excel(xl)


def _menu_unit(beats: list, use_paths: bool = True) -> list:
    """拍序列里第一个菜单组。"""
    for kind, unit in br.split_beat_units(beats, use_paths=use_paths):
        if kind == 'menu':
            return unit
    raise AssertionError('这一份里没有菜单组')


def _body_lines(rpy: str, option: str) -> list:
    """产物里 `option` 这个选项体的正文（去掉缩进、去掉空行）。"""
    lines = rpy.splitlines()
    i = next(i for i, ln in enumerate(lines) if ln.strip() == f'"{option}":')
    base = len(lines[i]) - len(lines[i].lstrip())
    out = []
    for ln in lines[i + 1:]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= base:
            break
        if ln.strip():
            out.append(ln.strip())
    return out


class TestPathModeIsUsed:
    def test_a_table_sourced_menu_is_cut_by_path(self, tmp_path):
        beats = _via_table(_beats(IF_THEN_MENU, tmp_path), tmp_path)
        plan = br._nest_plan(beats)
        start = next(i for i, b in enumerate(beats) if b.kind == KIND_MENU)
        assert plan['known'].get('a') is True, '表里带回来的结构位应当是齐的'
        got = br._take_menu_run_by_path(beats, start, plan)
        assert got is not None, '结构位齐了却没走路径'
        run, _end = got
        assert all(getattr(b, 'nest_path', None) is not None for b in run)

    def test_one_unknown_beat_falls_back_without_losing_it(self, tmp_path):
        """
        **一条"不知道"就整份退回老路**，而且那一拍**不许丢**。

        这是回归用例：第一版只在"算前缀"那一趟里查 `None`，后面的落拍循环
        用 `str(None)` 得到字面量 `'None'` → 静默跳过 → 拍没了
        （`test_roundtrip_invariants.test_inserting_a_beat_between_options...`
        当场抓到）。

        用的是"无条件菜单"那个形状 —— 老推断的收组逻辑在这一形状下**会**把
        手工插进来的那一拍收进组里（另一形状下它压根不进组，那属于老推断
        自己的边界，不是这一版管的）。
        """
        beats = _via_table(_beats(PLAIN_MENU, tmp_path), tmp_path)
        idx = next(i for i, b in enumerate(beats)
                   if b.kind == KIND_MENU_OPTION)
        beats.insert(idx + 1,
                     Beat(label='a', kind=KIND_NARRATION, text='手工插的一句'))
        unit = _menu_unit(beats)
        assert any(b.text == '手工插的一句' for b in unit), \
            f'老路应当把这一拍收进组里：{[b.text for b in unit]}'
        plan = br._nest_plan(beats)
        start = next(i for i, b in enumerate(beats) if b.kind == KIND_MENU)
        assert br._take_menu_run_by_path(beats, start, plan) is None, \
            '有一条不知道，就不该走路径'
        assert br._menu_segments_by_path(unit) is None, \
            '分段也必须整组退回老路（第一版就是漏了这一条 → 拍被静默丢掉）'
        rpy = render_beats_to_rpy(beats, label_name='a')
        assert '手工插的一句' in rpy, rpy


class TestTheBrokenShapesAreFixed:
    """老推断压平的那几个形状：选项体里**内容在、顺序对**。"""

    def test_menu_then_if_keeps_the_body_inside(self, tmp_path):
        beats = _beats(MENU_THEN_IF, tmp_path)
        body = _body_lines(render_beats_to_rpy(beats, label_name='a'), '选项A')
        assert body == ['if flag:', 'e "选了且 flag"', 'e "选项内收尾"'], body

    def test_if_then_menu_does_not_swallow_the_tail(self, tmp_path):
        beats = _beats(IF_THEN_MENU, tmp_path)
        rpy = render_beats_to_rpy(beats, label_name='a')
        assert _body_lines(rpy, '选项A') == ['e "选了"'], rpy
        # `if 内收尾` 在**菜单之外**（缩进回到 `if flag:` 那一级），
        # 而不是被吸进选项体里（吸进去就变成"只有选中才播"）
        tail = next(ln for ln in rpy.splitlines() if 'if 内收尾' in ln)
        assert tail.startswith(' ' * 4), rpy
        assert 'if 内收尾' not in _body_lines(rpy, '选项A'), rpy

    def test_interleaved_body_keeps_source_order(self, tmp_path):
        beats = _beats(INTERLEAVED, tmp_path)
        body = _body_lines(render_beats_to_rpy(beats, label_name='a'), '选项A')
        assert body == ['e "第一句"', 'if c1:', 'e "第二句"', 'e "第三句"'], body

    def test_it_also_works_after_an_excel_roundtrip(self, tmp_path):
        """**这才是主战场**：表格往返之后 `_menu_member` 没了，只剩结构列。"""
        beats = _via_table(_beats(MENU_THEN_IF, tmp_path), tmp_path)
        assert all(getattr(b, '_menu_member', None) is None for b in beats), \
            '前提：往返之后老那位就没了'
        body = _body_lines(render_beats_to_rpy(beats, label_name='a'), '选项A')
        assert body == ['if flag:', 'e "选了且 flag"', 'e "选项内收尾"'], body


class TestFallbackKeepsTheOldBehaviour:
    """
    结构位全被抹掉（老表）时**退回老推断** —— 老形状照旧，不假装能修。

    这条同时说明"修好"依赖的是什么：没有结构位，`menu>if` 仍旧被压平。
    """

    def test_without_structure_the_old_shape_comes_back(self, tmp_path):
        beats = _via_table(_beats(MENU_THEN_IF, tmp_path), tmp_path)
        for b in beats:
            b.nest_path = None
        rpy = render_beats_to_rpy(beats, label_name='a')
        assert _body_lines(rpy, '选项A') == ['pass'], \
            f'老路应当是压平的形状：\n{rpy}'
        # 内容没丢，但掉到菜单外（无条件执行）—— 这正是 A 档门禁要报的东西
        assert '选了且 flag' in rpy

    def test_the_flag_means_exactly_the_old_way(self, tmp_path):
        """
        `use_paths=False` ⇔ "当作结构位全都没有"。对拍器靠这个才**真的**
        在比"老推断 vs 结构列"（否则就成了拿路径比路径，永远 0 冲突）。

        比的是**切出来的形状**（按 `seq` 认拍）—— 两次解析出来的 Beat 对象
        值不一样（结构位那一栏不同），拿列表直接比会假红。
        """
        beats = _via_table(_beats(MENU_THEN_IF, tmp_path), tmp_path)
        stripped = _via_table(_beats(MENU_THEN_IF, tmp_path), tmp_path)
        for b in stripped:
            b.nest_path = None

        def shape(units):
            return [(kind, [b.seq for b in unit]) for kind, unit in units]

        assert (shape(br.split_beat_units(beats, use_paths=False))
                == shape(br.split_beat_units(stripped)))
        old = _menu_unit(beats, use_paths=False)
        assert ([b.seq for b in _menu_unit(stripped)] == [b.seq for b in old])

    def test_the_comparator_still_sees_the_old_way(self, tmp_path):
        """
        对拍器在"结构列齐了、但老推断会摆错"的形状上**必须报** ——
        这就是它作为阶段三（退掉 `stamp_menu_bodies`）安全网的价值。
        """
        beats = _beats(MENU_THEN_IF, tmp_path)
        # 手工把选项体里那两拍的路径改成"在顶层"（模拟表里的结构位被改坏）
        for b in beats:
            if b.nest_path and '>A' in b.nest_path:
                b.nest_path = ''
        assert nest_placement_conflicts(beats), '对拍器没看出"表里说的"不对'
