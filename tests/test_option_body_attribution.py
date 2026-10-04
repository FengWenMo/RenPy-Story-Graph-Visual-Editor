"""
**选项体的归属**：空的选项体不许把后面那块内容"吸"到自己名下 —— 见不变量 82。

为什么单开一份：`tools/hell_nest_inline.py` 的 `empty_option_body` 用例
（真实项目 `credits_screen.rpy` / `mini_game_puzzle.rpy` 同型）实测出过

    menu:
        "空的":
            e "x"          ← **本该属于「有内容」**
        "有内容":
            pass           ← 本该属于「空的」

两个选项体**对调**：选"空的"播了台词、选"有内容"什么都不做。
游戏能跑、不报错，属于静默改剧情那一类。

根因在 `_menu_segments_by_path` 的"键 → 段"配对：它从前**只按出现顺序**
配（`[B]` → 第 1 段），而位置键 `B` 本身就是"第 2 个选项"（
`core.nest_path.option_index`）。缺键（某个选项体是空的）时顺序配对必然错位。

判据三档：
  ① 键 = 位置（`A`/`B`/`A27` + 逆函数 `option_index`）；
  ② 空体在前 / 在中间 / 在末尾都不许错位；
  ③ 表被编辑过（删了选项，存下来的键越界）时仍走**顺序配对**那条老路。
"""

from __future__ import annotations

import pytest

from storygraph.core.nest_path import option_index, option_key
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

#: 第一个选项体是空的（真实项目里最常见的形状：占位选项 + 真内容选项）
EMPTY_FIRST = '''label t:
    menu:
        "空的":
        "有内容":
            e "x"
'''

#: 空的在中间
EMPTY_MIDDLE = '''label t:
    menu:
        "第一":
            e "1"
        "空的":
        "第三":
            e "3"
'''

#: 空的在末尾
EMPTY_LAST = '''label t:
    menu:
        "第一":
            e "1"
        "空的":
'''


def _beats(text: str, tmp_path):
    f = tmp_path / 'ch.rpy'
    f.write_text(text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return beats_from_parse_result(pr)


def _via_table(text: str, tmp_path):
    beats = _beats(text, tmp_path)
    xl = tmp_path / 'ch.xlsx'
    export_beats_to_excel(beats, xl)
    return import_beats_from_excel(xl)


def _body_lines(rpy: str, option: str) -> list:
    """产物里 `option` 这个选项体的正文（去缩进、去空行）。"""
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


class TestThePositionKey:
    def test_key_and_index_are_inverses(self):
        for i in range(1, 40):
            assert option_index(option_key(i)) == i

    def test_unknown_keys_are_zero(self):
        for bad in ('', None, 'a', 'AA', 'A0', '1', 'A-2', '选项'):
            assert option_index(bad) == 0, bad

    def test_the_documented_examples(self):
        assert (option_key(1), option_key(2), option_key(27)) == ('A', 'B', 'A27')
        assert (option_index('A'), option_index('B'), option_index('A27')) == (1, 2, 27)


class TestEmptyOptionBodyDoesNotStealTheBody:
    @pytest.mark.parametrize('src, want', [
        (EMPTY_FIRST, {'空的': ['pass'], '有内容': ['e "x"']}),
        (EMPTY_MIDDLE, {'第一': ['e "1"'], '空的': ['pass'], '第三': ['e "3"']}),
        (EMPTY_LAST, {'第一': ['e "1"'], '空的': ['pass']}),
    ])
    def test_direct_render(self, src, want, tmp_path):
        rpy = render_beats_to_rpy(_beats(src, tmp_path))
        got = {opt: _body_lines(rpy, opt) for opt in want}
        assert got == want, f'{got}\n---\n{rpy}'

    @pytest.mark.parametrize('src, want', [
        (EMPTY_FIRST, {'空的': ['pass'], '有内容': ['e "x"']}),
        (EMPTY_MIDDLE, {'第一': ['e "1"'], '空的': ['pass'], '第三': ['e "3"']}),
        (EMPTY_LAST, {'第一': ['e "1"'], '空的': ['pass']}),
    ])
    def test_it_survives_the_excel_roundtrip(self, src, want, tmp_path):
        rpy = render_beats_to_rpy(_via_table(src, tmp_path))
        got = {opt: _body_lines(rpy, opt) for opt in want}
        assert got == want, f'{got}\n---\n{rpy}'

    def test_the_two_paths_are_byte_identical(self, tmp_path):
        direct = render_beats_to_rpy(_beats(EMPTY_FIRST, tmp_path))
        table = render_beats_to_rpy(_via_table(EMPTY_FIRST, tmp_path))
        assert direct == table, f'{direct}\n---\n{table}'

    def test_the_nest_column_points_at_the_right_option(self, tmp_path):
        """结构位本身也要对：台词在 `…>B`（第 2 个选项体）里。"""
        beats = _beats(EMPTY_FIRST, tmp_path)
        say = next(b for b in beats if b.text == 'x')
        assert say.nest_path == 'menu:1>B', [b.nest_path for b in beats]


class TestEditedTablesStillPairByOrder:
    """
    表被编辑过（选项行被删）时，存下来的键**越界** —— 那时候只能按
    出现顺序配对（键 `B`/`C` 还写着原位置，却已经排到第 1/2 位）。
    这一档是 `test_deleting_an_option_keeps_the_rest` 守的老行为，这里再钉一次。
    """

    def test_out_of_range_keys_fall_back_to_order(self, tmp_path):
        """
        删掉第 1 个选项行之后，剩下的键（`A`/`B`/`C`）已经**越界** ——
        结构与表对不上，只能按出现顺序配（这正是老行为，也是 `Nest` 列
        被人工编辑后唯一说得通的一档）。要求：**一块内容都不许丢**。
        """
        from storygraph.story import beat_render as br
        beats = _via_table(EMPTY_MIDDLE, tmp_path)
        idx = next(i for i, b in enumerate(beats)
                   if b.kind == 'menu_option' and b.text == '第一')
        del beats[idx]
        run = [next(b for b in beats if b.kind == 'menu')] + [
            b for b in beats if b.nest_path and b.nest_path.startswith('menu:1')]
        segments = br._menu_segments_by_path(run)
        assert segments is not None, '越界键该退回顺序配对，而不是整组作废'
        # 段数 = 剩下的选项行数；所有块体拍都被收进**某一个**段里（不丢）
        assert len(segments) == 2, [s[0].text for s in segments]
        placed = [b.text for _opt, body, _foreign in segments
                  for _p, b, _w in body]
        assert sorted(placed) == ['1', '3'], placed
        # 顺序配对：原来的第 1 个块体落在第 1 段（老行为，钉住）
        assert segments[0][0].text == '空的'
        assert [b.text for _p, b, _w in segments[0][1]] == ['1']
