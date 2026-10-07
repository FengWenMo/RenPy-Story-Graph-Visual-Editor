"""
循环的**作用域**：块体进语句流之后，边界必须"不多不少"
=====================================================

这是"给拍模型加作用域"（2026-10-07 第三轮）的判据文件。为什么单独一个文件：
**边界错了不会报错，只会静默改剧情**，而且循环比菜单更狠：

  · **少吞一拍** → 那一句掉到循环外面，**只执行一次**；
  · **多吞一拍** → 循环后面那一句被卷进循环体，**执行多次**。

两种产物都语法合法、门禁拦不住、游戏能跑。菜单那次（P0-1）栽的就是这个，
所以这里的夹具刻意**脏**：夹空行、夹注释、相邻同构块、嵌套菜单 / 嵌套循环。

判据分四组：

  ① **结构位**：块体语句带 `loop:n`，头带外层路径（它是组头，不在自己里面）；
  ② **边界**：块体前一句 / 后一句都不许被卷进去；
  ③ **嵌套**：循环里套菜单、菜单里套循环、循环里套循环，缩进逐层正确；
  ④ **条件**：`if flag:` 包着 `while` 时，`if` 写在循环**外面**，
     块体台词不许各自再套一个 `if`（条件滚雪球）；
  ⑤ **退化**：结构位丢了（隐藏列 `Nest` 被清空）→ 给显式 TODO，
     不许静默拍平、更不许写出 `while …: pass` 那种**死循环**。
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.pipeline.compile import compile_beats
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_model import BEAT_FIELDS
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel


def _pr(tmp_path, src, name='a.rpy'):
    f = tmp_path / name
    f.write_text(src, encoding='utf-8')
    return IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)[0]


def _out(tmp_path, src, name='a.rpy'):
    """`.rpy` → 拍 → .rpy（纯渲染路，不经过表）。"""
    return render_beats_to_rpy(beats_from_parse_result(_pr(tmp_path, src, name)))


def _out_via_excel(tmp_path, src, name='a.rpy'):
    """`.rpy` → 拍 → 表 → 拍 → .rpy（**产品主路**）。"""
    beats = beats_from_parse_result(_pr(tmp_path, src, name))
    x = tmp_path / (Path(name).stem + '.xlsx')
    export_beats_to_excel(beats, x)
    return compile_beats(import_beats_from_excel(x)).rpy_text


def _paths(tmp_path, src):
    pr = _pr(tmp_path, src)
    return [(getattr(s, 'nest_path', None), s.kind,
             (s.payload.get('text') or s.payload.get('header') or '')[:28])
            for s in pr.statements]


# ---------------------------------------------------------------------------
#  ① 结构位：块体在 `loop:n` 里，头在**外层**
# ---------------------------------------------------------------------------

def test_body_statements_carry_the_loop_scope(tmp_path):
    src = ('label a:\n'
           '    "前"\n'
           '    while n < 3:\n'
           '        $ n += 1\n'
           '        e "循环里"\n'
           '    "后"\n')
    got = _paths(tmp_path, src)
    by_kind = {}
    for path, kind, _t in got:
        by_kind.setdefault(kind, []).append(path)
    assert by_kind['loop'] == [''], by_kind          # 组头在外层
    assert by_kind['python'] == ['loop:1'], by_kind  # 块体在里面
    assert by_kind['say'] == ['', 'loop:1', ''], by_kind  # 前 / 里 / 后


def test_head_is_outside_its_own_scope(tmp_path):
    """组头**不在**自己的作用域里（与菜单头同一个约定）。"""
    src = ('label a:\n'
           '    while n < 3:\n'
           '        $ n += 1\n')
    assert _paths(tmp_path, src)[0][0] == ''


# ---------------------------------------------------------------------------
#  ② 边界：不多不少
# ---------------------------------------------------------------------------

BOUNDARY = (
    'label a:\n'
    '    "循环之前"\n'
    '    # 循环的上方注释\n'
    '    while n < 3:\n'
    '        $ n += 1\n'
    '\n'
    '        # 块体里的注释\n'
    '        e "只该执行多次"\n'
    '    "循环之后"\n'
    '    return\n'
)


def _body_of_loop(out: str) -> list:
    """产物里 `while` 那一行到它块体结束（回到同层缩进）之间的行。"""
    lines = out.split('\n')
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith('while '))
    head_indent = len(lines[start]) - len(lines[start].lstrip())
    body = []
    for ln in lines[start + 1:]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= head_indent:
            break
        body.append(ln)
    return body


@pytest.mark.parametrize('renderer', ['pure', 'excel'])
def test_loop_body_does_not_swallow_the_next_line(tmp_path, renderer):
    """**循环后面那一句不许被卷进循环体**（卷进去 = 执行多次）。"""
    out = (_out(tmp_path, BOUNDARY) if renderer == 'pure'
           else _out_via_excel(tmp_path, BOUNDARY))
    body = '\n'.join(_body_of_loop(out))
    assert '只该执行多次' in body, out
    assert '循环之后' not in body, f'后面那句被吞进循环了：\n{out}'
    # 而且它在循环**之后**
    assert out.index('循环之后') > out.index('只该执行多次'), out


@pytest.mark.parametrize('renderer', ['pure', 'excel'])
def test_loop_body_does_not_drop_the_last_line(tmp_path, renderer):
    """**块体最后一句不许掉出去**（掉出去 = 只执行一次）。"""
    out = (_out(tmp_path, BOUNDARY) if renderer == 'pure'
           else _out_via_excel(tmp_path, BOUNDARY))
    body = '\n'.join(_body_of_loop(out))
    assert '只该执行多次' in body, out
    assert '$ n += 1' in body, out


def test_empty_loop_body_gets_a_pass(tmp_path):
    """空块体补 `pass`（Ren'Py 要求缩进块非空，否则产物解析不了）。"""
    src = ('label a:\n'
           '    while n < 3:\n'
           '        pass\n')
    out = _out(tmp_path, src)
    assert 'while n < 3:' in out, out
    assert 'pass' in out, out


# ---------------------------------------------------------------------------
#  ③ 嵌套
# ---------------------------------------------------------------------------

NESTED_LOOPS = (
    'label a:\n'
    '    while i < 2:\n'
    '        while j < 2:\n'
    '            $ j += 1\n'
    '        $ i += 1\n'
    '    "完"\n'
)


def test_nested_loops_are_both_blocks(tmp_path):
    out = _out(tmp_path, NESTED_LOOPS)
    lines = out.split('\n')
    outer = next(ln for ln in lines if ln.strip() == 'while i < 2:')
    inner = next(ln for ln in lines if ln.strip() == 'while j < 2:')
    body = next(ln for ln in lines if ln.strip() == '$ j += 1')
    after = next(ln for ln in lines if ln.strip() == '$ i += 1')
    ind = lambda s: len(s) - len(s.lstrip())      # noqa: E731
    assert ind(inner) > ind(outer), out
    assert ind(body) > ind(inner), out
    # `$ i += 1` 在**外层**循环体里（与 `while j` 同层）
    assert ind(after) == ind(inner), out


NESTED_MENU = (
    'label a:\n'
    '    while n < 3:\n'
    '        $ n += 1\n'
    '        menu:\n'
    '            "继续":\n'
    '                e "继续了"\n'
    '            "退出":\n'
    '                jump out\n'
    '    "完"\n'
)


def test_menu_inside_loop_is_nested(tmp_path):
    out = _out(tmp_path, NESTED_MENU)
    ind = lambda s: len(s) - len(s.lstrip())      # noqa: E731
    while_ln = next(ln for ln in out.split('\n') if ln.strip() == 'while n < 3:')
    menu_ln = next(ln for ln in out.split('\n') if ln.strip() == 'menu:')
    opt_ln = next(ln for ln in out.split('\n') if ln.strip().startswith('"继续"'))
    say_ln = next(ln for ln in out.split('\n') if ln.strip() == 'e "继续了"')
    assert ind(while_ln) < ind(menu_ln) < ind(opt_ln) < ind(say_ln), out


NESTED_LOOP_IN_MENU = (
    'label a:\n'
    '    menu:\n'
    '        "来一轮":\n'
    '            while n < 3:\n'
    '                $ n += 1\n'
    '        "算了":\n'
    '            e "算了"\n'
)


def test_loop_inside_menu_is_nested(tmp_path):
    out = _out(tmp_path, NESTED_LOOP_IN_MENU)
    ind = lambda s: len(s) - len(s.lstrip())      # noqa: E731
    menu_ln = next(ln for ln in out.split('\n') if ln.strip() == 'menu:')
    opt_ln = next(ln for ln in out.split('\n') if ln.strip().startswith('"来一轮"'))
    while_ln = next(ln for ln in out.split('\n') if ln.strip() == 'while n < 3:')
    body = next(ln for ln in out.split('\n') if ln.strip() == '$ n += 1')
    assert ind(menu_ln) < ind(opt_ln) < ind(while_ln) < ind(body), out


# ---------------------------------------------------------------------------
#  ④ 条件：整块的条件写在循环外面，块体不许各套一个 `if`
# ---------------------------------------------------------------------------

COND_LOOP = (
    'label a:\n'
    '    if flag:\n'
    '        while n < 3:\n'
    '            $ n += 1\n'
    '            e "循环里"\n'
    '    "之后"\n'
)


def test_loop_condition_is_written_outside_and_not_repeated(tmp_path):
    out = _out(tmp_path, COND_LOOP)
    lines = [ln.strip() for ln in out.split('\n') if ln.strip()]
    assert lines.count('if flag:') == 1, out
    # 块体里那句台词**不**再套一层 `if flag:`
    assert lines.count('e "循环里"') == 1, out
    idx_if = lines.index('if flag:')
    idx_while = next(i for i, ln in enumerate(lines) if ln.startswith('while '))
    assert idx_if < idx_while, out


def test_if_inside_loop_still_renders(tmp_path):
    """循环体里的 `if` 照旧渲染（不许被循环那层条件吃掉）。"""
    src = ('label a:\n'
           '    while n < 3:\n'
           '        if flag:\n'
           '            e "有条件"\n'
           '        $ n += 1\n')
    out = _out(tmp_path, src)
    lines = [ln.strip() for ln in out.split('\n') if ln.strip()]
    assert 'while n < 3:' in lines, out
    assert 'if flag:' in lines, out
    assert lines.index('if flag:') > lines.index('while n < 3:'), out


# ---------------------------------------------------------------------------
#  ⑤ 退化：结构位丢了要给**看得见**的 TODO，不许静默拍平 / 死循环
# ---------------------------------------------------------------------------

def test_legacy_loop_renders_verbatim_exactly_once():
    """
    **老形态**（`LoopHeader` + 整块 `Opaque`）必须逐字渲染，而且只渲染**一遍**。

    为什么单独钉这条：渲染层是按"**有头行、没有整块原文**"认结构化组头的
    （`_is_loop_head`）。这个判据一旦放宽成"有头行就算"，老形态会被当成
    结构化的循环头 → 走 `render_loop_block` → 它**不认识 `opaque`**，
    整块内容一个字都不吐 → 产物里 `while x < 3: pass`（**死循环**），
    而块体那些行**静默消失**。

    ⚠ 这不是假想：**D2 那一版导出的表**正是这个形状（`LoopHeader` +
    `Opaque` + `Nest` 都有、块体没有独立行）。所以夹具必须把 `nest_path`
    写实（`''` = 顶层）—— 否则 `_nest_plan` 判"结构位不齐"，整份退回老路，
    判据就成了**空转**（第一版就是这么写错的：变异验证全绿才发现）。
    """
    from storygraph.story.beat_model import Beat
    legacy = [
        Beat(seq=1, label='a', kind='narration', text='前', nest_path=''),
        Beat(seq=2, label='a', kind='narration',
             loop_header='while x < 3:',
             opaque='while x < 3:\n    $ x += 1\n    e "循环里"',
             nest_path=''),
        Beat(seq=3, label='a', kind='narration', text='后', nest_path=''),
    ]
    out = render_beats_to_rpy(legacy, label_name='a')
    assert out.count('while x < 3:') == 1, out
    assert out.count('$ x += 1') == 1, out
    assert out.count('e "循环里"') == 1, out
    assert 'pass' not in out, f'老形态被当成了空循环体：\n{out}'
    assert '# TODO' not in out, out


def test_loop_head_is_not_reported_as_empty_dialogue():
    """
    **循环头拍不许被报成"空台词行"**（真机实测的假警）。

    它是 `kind='narration'`、`text` 为空 —— 与"清空对话框的 `""`"长得
    **一模一样**，而 W113 那条判据原来只看 text / comment / raw / code /
    舞台 / 音频，**不看** `loop_header`。真机上这一条就是 +1 条假警
    （`mini_game1.1568` 被报成"空台词行"）。

    （旧形态之所以没报，只是因为它把整块 `while` 与**后面那句台词**并进了
    同一张拍 —— 那是另一个缺陷，不该被当成挡箭牌。）
    """
    from storygraph.pipeline.validate import validate_level1
    from storygraph.story.beat_model import Beat
    head = Beat(seq=1, label='a', kind='narration',
                loop_header='while x < 3:')
    assert 'W113' not in [i.code for i in validate_level1([head])]
    # 对照：真正的空台词行照样要报（别把这条修成"什么都不报"）
    blank = Beat(seq=2, label='a', kind='narration', text='')
    assert 'W113' in [i.code for i in validate_level1([blank])]


def test_missing_scope_column_degrades_loudly(tmp_path):
    """
    把隐藏列 `Nest` 清空（用户手改表 / 老工具写的表）之后：

      · **不许**写出 `while …: pass`（那是**死循环**）；
      · **不许**静默拍平（内容还在，但结构没了，必须说出口）；
      · 产物里要有一句 `# TODO` —— `UNRESOLVED_TODO` 门禁会抓到它。
    """
    src = ('label a:\n'
           '    while n < 3:\n'
           '        $ n += 1\n'
           '    "后"\n')
    beats = beats_from_parse_result(_pr(tmp_path, src))
    x = tmp_path / 'o.xlsx'
    export_beats_to_excel(beats, x)
    # 清空 Nest 列（模拟"结构位丢失"）
    wb = openpyxl.load_workbook(x)
    ws = wb['Beats']
    col = BEAT_FIELDS.index('Nest') + 1
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=col).value = None
    wb.save(x)

    back = import_beats_from_excel(x)
    assert all(getattr(b, 'nest_path', None) is None for b in back), \
        [getattr(b, 'nest_path', None) for b in back]
    out = render_beats_to_rpy(back, label_name='a')
    assert '# TODO' in out, f'退化没有任何提示：\n{out}'
    assert 'while n < 3:' not in out or 'pass' not in out, (
        f'退化成了 while…pass（死循环）：\n{out}')
    # 内容不许丢：块体那句还在产物里（只是结构降级）
    assert '$ n += 1' in out, out
