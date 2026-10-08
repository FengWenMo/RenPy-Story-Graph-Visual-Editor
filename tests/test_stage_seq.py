"""
**拍内舞台指令的顺序**：作者怎么写，产物就该怎么出（缺口 4 的正面对账）
=====================================================================

背景：`tools/probe_stage_seq.py` 把"同一拍里 2~4 条舞台指令的**排列**"
全枚举了一遍（252 个组合），结果是：

  · **过表把顺序改坏：0 个** —— 当年报的"过表翻序"已经被前面的修法堵住了；
  · 但**不过表就没保住作者顺序：136 个** —— 也就是说**第一次导出就改了**，
    而表是忠实地把那（已经改过的）顺序搬来搬去。

按用户 2026-10-07 的指示（"不要绕开风险，直接解决它"），这一层正面修掉。
三条根因，三条判据：

  ① **同名 verb 被合成一组**：`show A / hide B / show C` 出成
     `show A / show C / hide B`（渲染器按 **verb 名**分组输出）。
     ⚠ 换成 `show A / hide A / show A` 就是**收尾画面反了**
     （作者要"最后是显示"，产物最后是"被 hide 掉"）。
  ② **同名同参数的重复被去重**：`_join_show_items` 按**内容**去重
     （它的本意是挡 hydrate 的影子重复），顺手把作者写两遍的那一条也吃了。
  ③ **独立的 `with X` 被排到舞台之后**：它是「代码」列那条路（`Beat.raw`），
     而代码块按固定槽位输出（"紧跟第一条 scene"或"舞台尾段之后"），
     不是作者写的位置。⚠ 换位置 = 转场作用在**另一条**指令上
     （`show A / with dissolve / show B` 是"A 淡入"，挪到后面就成了"B 淡入"）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel


def _lines(text: str) -> list:
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.startswith('#')
            and ln.strip() not in ('label t:', 'return')]


def _render(src_lines: list, tmp_path, name='t') -> tuple:
    """→ `(不过表的产物行, 过表之后的产物行)`。"""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / 'ch.rpy'
    f.write_text('label t:\n' + ''.join(f'    {ln}\n' for ln in src_lines)
                 + '    return\n', encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    direct = _lines(render_beats_to_rpy(beats))
    xl = d / 'ch.xlsx'
    export_beats_to_excel(beats, xl)
    after = _lines(render_beats_to_rpy(import_beats_from_excel(xl)))
    return direct, after


# ---------------------------------------------------------------------------
#  ① 同名 verb 不许被合成一组（这才是"翻序"的本体）
# ---------------------------------------------------------------------------

def test_hide_between_two_shows_keeps_its_position(tmp_path):
    src = ['show ling happy', 'hide jc', 'show jc angry']
    direct, after = _render(src, tmp_path)
    assert direct == src, f'不过表就翻序了：{direct}'
    assert after == src, f'过表之后翻序了：{after}'


def test_show_hide_show_of_the_same_tag_ends_visible(tmp_path):
    """
    **收尾画面判据**：`show A / hide A / show A` 的作者意图是"最后显示"。

    去重 + 分组会把最后那条 `show` 吃掉 → 产物最后是"被 hide 掉"，
    玩家看到的画面与作者要的**相反**。
    """
    src = ['show ling happy', 'hide ling', 'show ling happy']
    direct, after = _render(src, tmp_path)
    assert direct == src, f'不过表就翻序了：{direct}'
    assert after == src, f'过表之后翻序了：{after}'


def test_scene_in_the_middle_keeps_its_position(tmp_path):
    src = ['show ling happy', 'scene bg room', 'show jc angry']
    direct, after = _render(src, tmp_path)
    assert direct == src, f'不过表就翻序了：{direct}'
    assert after == src, f'过表之后翻序了：{after}'


def test_hide_scene_hide_interleaving(tmp_path):
    src = ['hide ling', 'scene bg room', 'hide jc']
    direct, after = _render(src, tmp_path)
    assert direct == src, f'不过表就翻序了：{direct}'
    assert after == src, f'过表之后翻序了：{after}'


# ---------------------------------------------------------------------------
#  ② 同名同参数的重复：作者写两遍就得留两遍
# ---------------------------------------------------------------------------

def test_duplicate_show_lines_are_kept(tmp_path):
    """
    `show ling happy` 写两遍 → 产物也两遍。

    为什么不去重（虽然 Ren'Py 语义上第二次是空操作）：
    这是**作者的脚本**，工具的本分是"不改"，而按内容去重会在
    `show A / hide A / show A` 这种交错里吃掉**语义关键**的那一条
    （见上一条判据）。宁可多一行，也不替作者删。
    """
    src = ['show ling happy', 'show ling happy']
    direct, after = _render(src, tmp_path)
    assert direct == src, f'重名被吞了：{direct}'
    assert after == src, f'过表之后重名被吞了：{after}'


def test_duplicate_hide_lines_are_kept(tmp_path):
    src = ['hide ling', 'hide ling']
    direct, after = _render(src, tmp_path)
    assert direct == src, f'重名被吞了：{direct}'
    assert after == src, f'过表之后重名被吞了：{after}'


# ---------------------------------------------------------------------------
#  ③ 独立的 `with X` 要留在作者写的位置
# ---------------------------------------------------------------------------

def test_standalone_with_keeps_its_position_between_ops(tmp_path):
    """`show A / with dissolve / show B`：转场作用在 A 上（挪了就变 B）。"""
    src = ['show ling happy', 'with dissolve', 'show jc angry']
    direct, after = _render(src, tmp_path)
    assert direct == src, f'`with` 被挪位了：{direct}'
    assert after == src, f'`with` 过表后被挪位了：{after}'


def test_standalone_with_before_everything(tmp_path):
    src = ['with dissolve', 'show ling happy']
    direct, after = _render(src, tmp_path)
    assert direct == src, f'`with` 被挪位了：{direct}'
    assert after == src, f'`with` 过表后被挪位了：{after}'


def test_standalone_with_after_scene_and_show(tmp_path):
    src = ['scene bg room', 'show ling happy', 'with dissolve']
    direct, after = _render(src, tmp_path)
    assert direct == src, f'`with` 被挪位了：{direct}'
    assert after == src, f'`with` 过表后被挪位了：{after}'


# ---------------------------------------------------------------------------
#  ⑤ **一条真序列**（缺口 4 第八轮）：raw / code 行与舞台行之间的位置
# ---------------------------------------------------------------------------
#
# 从前舞台动词与 raw/code 是**两条通道**，各自保序、彼此之间只有"槽位"
# （代码块跟在第一条 scene 之后 / 舞台尾段之后）。于是下面这些形状
# **不过表就已经错**，真机 `pomodorro` / `chapter1_1` / `chapter1_4` /
# `script_pomo` 四个文件都命中过。

def test_raw_screen_line_between_stage_ops_keeps_position(tmp_path):
    """`hide screen`（raw）夹在两条舞台指令之间 —— 位置必须逐条还原。"""
    src = ['show ling happy', 'hide screen msg', 'hide ling']
    direct, after = _render(src, tmp_path)
    assert direct == src, f'raw 行被挪位了：{direct}'
    assert after == src, f'过表后 raw 行被挪位了：{after}'


def test_raw_line_before_scene_stays_before(tmp_path):
    """真机 `pomodorro.rpy` 的形状：`hide screen map` 写在 `scene` **前面**。"""
    src = ['hide screen map', 'scene bg room', 'show ling happy']
    direct, after = _render(src, tmp_path)
    assert direct == src, f'raw 行被挪到 scene 之后了：{direct}'
    assert after == src, f'过表后 raw 行被挪位了：{after}'


def test_dollar_code_line_stays_between_stage_ops(tmp_path):
    """`$ x = 1`（code）夹在两条舞台指令之间 —— 同样要逐条还原。"""
    src = ['show ling happy', '$ game = 1', 'hide ling']
    direct, after = _render(src, tmp_path)
    assert direct == src, f'代码行被挪位了：{direct}'
    assert after == src, f'过表后代码行被挪位了：{after}'


def test_raw_pause_between_two_shows(tmp_path):
    """`pause`（raw）夹在两条 show 之间：手机消息那一段的形状。"""
    src = ['show screen msg', 'pause 0.5', 'hide screen msg']
    direct, after = _render(src, tmp_path)
    assert direct == src, f'raw 行被挪位了：{direct}'
    assert after == src, f'过表后 raw 行被挪位了：{after}'


def test_multi_line_raw_statement_keeps_position(tmp_path):
    """
    多行 raw 语句（续行）+ 舞台行：逐行令牌必须让它整段留在原位。

    真机 `mini_game_dfinder.rpy` 的形状（`show screen prompt(…, act=[…])`）。
    """
    src = ['show ling happy',
           'show screen prompt("再来", act=[',
           '    Call("again"),',
           '])',
           'hide ling']
    # ⚠ `_render` 的 `_lines()` **会把每行 strip 掉**（其它判据只用它比顺序），
    # 所以这里的基准也要 strip：续行的**缩进**不在本判据范围内
    # （它是另一个维度，`tools/probe_raw_multiline.py` 里逐字看过：8 空格/4 空格都对）。
    want = [ln.strip() for ln in src]
    direct, after = _render(src, tmp_path)
    assert direct == want, f'多行 raw 被挪位/压平了：{direct}'
    # 过表也要一样：令牌是**权威**（第九轮按令牌重贴 raw/code 类型），
    # 所以"续行被 `_split_code_cell` 判成 code"不再让计划放弃。
    assert after == want, f'过表后多行 raw 被挪位了：{after}'

# ---------------------------------------------------------------------------
#  ⑥ 过表这一趟必须**逐字节**（顺序对的那些形状也不许被表改）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('src', [
    ['with dissolve', 'show jc angry', 'hide ling'],
    ['hide jc', 'with dissolve', 'hide ling'],
    ['scene bg room', 'hide ling', 'with dissolve'],
    ['show ling happy', 'hide jc', 'with dissolve'],
])
def test_table_round_trip_is_byte_identical(tmp_path, src):
    direct, after = _render(src, tmp_path, name=src[0].split()[0] + str(len(src)))
    assert direct == after, f'过表改了顺序：\n  不过表 {direct}\n  过表后 {after}'
