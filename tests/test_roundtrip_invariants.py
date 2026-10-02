"""
tests/test_roundtrip_invariants.py
==================================
**往返不变式**：`.rpy → 拍 → Excel → 拍 → .rpy'` 之后，语句多重集必须相等。

为什么单独一个文件（评估报告 2026-10-02 第 5.4 节的结论）
--------------------------------------------------------
当时 1731 个测试全绿，却让**两个 P0 静默通过**：

  · `menu:` 整块在往返后消失（选项变裸旁白、第二分支成死代码）；
  · `Beat.condition` 不落 Excel（所有 `if` 分支被抹平成顺序执行）。

原因不是测试少，而是**测的是半程**：

    tests/test_script_table.py
        def test_condition_from_if_block(...):
            beats = beats_from_parse_result(result)
            assert beats[0].condition == 'love > 5'      ← 止步于此

    def test_menu_option_becomes_own_beat(...):
        beats = beats_from_parse_result(result)
        assert beats[0].kind == KIND_MENU_OPTION          ← 止步于此

两条都断言到"拍"就结束：**没有导出 Excel、没有回导、没有重新编译**。
而"Excel 双向同步"恰恰是本项目对外承诺的核心能力 —— 它的**后半程**
（落表 / 回导 / 再编译）一直没有不变式守着。

本文件的判据只有一条，但覆盖整条链路：

    原文语句多重集  ==  往返产物语句多重集

它一旦存在，上面两个 P0 在补上的当天就会自动暴露（事实如此）。

判据刻意用**多重集**而不是"文本完全相等"：拍是"一句台词 + 这一句时的
舞台状态"的折叠，`if` 块被解析器展开成"条件链"（`elif` → `(b) and not (a)`），
所以中间形态**本来就不该逐字节相同** —— 要守的是**语句层面的语义**。
"""

from __future__ import annotations

import collections

import pytest

from storygraph.parsing.renpy_parser import (RenPyParser, ParseResultBuilder)
from storygraph.pipeline.compile import compile_beats
from storygraph.story.beat_build import beats_from_statements
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel
from storygraph.story.script_table import render_beats_to_rpy


# ------------------------------------------------------------------
#  比对口径
# ------------------------------------------------------------------

#: 这些 kind 的**条件不参与**指纹比对。
#:
#: 为什么：解析器把 `elif` / `else` **展开**成前一条件的否定合取
#: （`(breakline == 3) and not ((breakline == 1) or (breakline == 2))`），
#: 而往返之后它仍然是同一个表达式 —— 但 `menu_option` 的条件在
#: "选项 vs 选项体"两处出现，重编译时可能落在不同的语句上。
#: 条件本身由 `test_condition_survives_*` 那几条专门守。
def _key(stmt):
    """语句的可比较指纹（kind + payload；忽略位置与 content_hash）。"""
    p = stmt.payload or {}
    kind = stmt.kind
    if kind == 'say':
        return ('say', p.get('speaker', ''), p.get('text', ''))
    if kind == 'extend':
        return ('extend', p.get('text', ''))
    if kind == 'menu':
        return ('menu', (p.get('caption') or '').strip())
    if kind == 'menu_option':
        return ('menu_option', (p.get('text') or ''))
    if kind == 'show':
        return ('show', p.get('image', ''), tuple(p.get('at') or ()),
                p.get('as_', ''), p.get('with_', ''), p.get('onlayer', ''),
                tuple(p.get('behind') or ()), bool(p.get('is_expression')))
    if kind == 'scene':
        return ('scene', p.get('image', ''), tuple(p.get('at') or ()),
                p.get('with_', ''))
    if kind == 'hide':
        return ('hide', p.get('image', ''), p.get('with_', ''))
    if kind == 'play':
        return ('play', p.get('action'), p.get('channel'), p.get('name'),
                bool(p.get('loop')), float(p.get('fadein') or 0),
                float(p.get('fadeout') or 0))
    if kind == 'voice':
        return ('voice', p.get('name', ''))
    if kind == 'python':
        return ('python', (p.get('code') or '').strip())
    if kind == 'comment':
        return ('comment', (p.get('text') or '').strip())
    if kind == 'raw':
        return ('raw', (p.get('text') or '').strip())
    if kind == 'jump':
        return ('jump', p.get('target', ''))
    if kind == 'return':
        return ('return',)
    return (kind, str(sorted(p.items())))


def _reparse(text: str, tmp_path, stem: str):
    path = tmp_path / f'{stem}.rpy'
    path.write_text(text, encoding='utf-8')
    return ParseResultBuilder().build(RenPyParser().parse(path))


def roundtrip(script: str, tmp_path, stem: str = 'rt'):
    """
    走完整条链路，返回 `(原文语句, 产物语句, props)`。

    链路严格按用户真实用法：
        parse → beats → **xlsx** → beats → compile(.rpy) → re-parse
    """
    src = tmp_path / f'{stem}_src.rpy'
    src.write_text(script, encoding='utf-8')
    original = ParseResultBuilder().build(RenPyParser().parse(src))

    beats = beats_from_statements(original.statements,
                                  labels=original.statement_labels)
    xlsx = tmp_path / f'{stem}.xlsx'
    export_beats_to_excel(beats, xlsx)
    back = import_beats_from_excel(xlsx)
    result = compile_beats(back)
    produced = _reparse(result.rpy_text, tmp_path, f'{stem}_out')
    return original, produced, {'rpy': result.rpy_text, 'beats': beats,
                                'back': back}


def assert_same_multiset(original, produced, props=None):
    a = collections.Counter(_key(s) for s in original.statements)
    b = collections.Counter(_key(s) for s in produced.statements)
    lost, added = a - b, b - a
    detail = ''
    if lost or added:
        rpy = (props or {}).get('rpy', '')
        detail = (f'\n--- 丢失 {sum(lost.values())} ---\n'
                  + '\n'.join(f'  {k} x{v}' for k, v in lost.items())
                  + f'\n--- 新增 {sum(added.values())} ---\n'
                  + '\n'.join(f'  {k} x{v}' for k, v in added.items())
                  + '\n--- 产物 ---\n' + rpy)
    assert not lost and not added, detail


# ------------------------------------------------------------------
#  用例
# ------------------------------------------------------------------

class TestMenuRoundtrip:
    """
    菜单是**块结构**，而拍是扁平的一行 —— 这是全线最容易损坏的地方
    （P0-1：往返后整块消失、分支变死代码）。
    """

    BARE = (
        'label a:\n'
        '    "开场"\n'
        '    menu:\n'
        '        "选项A":\n'
        '            jump b\n'
        '        "选项B":\n'
        '            jump c\n'
        '    return\n'
    )

    CAPTIONED = (
        'label a:\n'
        '    menu "要往哪边走？":\n'
        '        "左边":\n'
        '            jump l\n'
        '        "右边":\n'
        '            jump r\n'
        '    return\n'
    )

    CONDITIONAL = (
        'label a:\n'
        '    menu:\n'
        '        "去图书馆" if love >= 5:\n'
        '            jump lib\n'
        '        "回家":\n'
        '            jump home\n'
        '    return\n'
    )

    WITH_BODY = (
        'label a:\n'
        '    menu:\n'
        '        "进去":\n'
        '            "他推开门。"\n'
        '            jump inner\n'
        '        "离开":\n'
        '            e "下次吧。"\n'
        '    "剧情继续"\n'
        '    return\n'
    )

    def test_bare_menu_survives(self, tmp_path):
        original, produced, props = roundtrip(self.BARE, tmp_path, 'menu_bare')
        assert_same_multiset(original, produced, props)

    def test_captioned_menu_survives(self, tmp_path):
        original, produced, props = roundtrip(self.CAPTIONED, tmp_path,
                                              'menu_cap')
        assert_same_multiset(original, produced, props)

    def test_conditional_option_survives(self, tmp_path):
        original, produced, props = roundtrip(self.CONDITIONAL, tmp_path,
                                              'menu_cond')
        assert_same_multiset(original, produced, props)
        # 选项条件必须还在产物里（`if love >= 5:`）
        assert 'love >= 5' in props['rpy'], props['rpy']

    def test_menu_with_body_survives(self, tmp_path):
        """选项体里的台词/跳转必须**留在选项块里**，不能掉到块外。"""
        original, produced, props = roundtrip(self.WITH_BODY, tmp_path,
                                              'menu_body')
        assert_same_multiset(original, produced, props)
        lines = [ln.rstrip() for ln in props['rpy'].splitlines()]
        assert '    menu:' in lines, props['rpy']
        assert '        "进去":' in lines, props['rpy']
        # 分支体缩进在选项下面（8 空格 = label 4 + 选项 4）
        assert '            "他推开门。"' in lines, props['rpy']
        assert '            jump inner' in lines, props['rpy']
        # 块外那句旁白必须落回 label 一级，不能被吞进菜单
        assert '    "剧情继续"' in lines, props['rpy']

    def test_two_menus_in_a_row_stay_separate(self, tmp_path):
        """连续两个菜单不能被并成一块（真实项目 `script_pomo.rpy` 有这种写法）。"""
        script = (
            'label a:\n'
            '    menu:\n'
            '        "第一个":\n'
            '            jump one\n'
            '    menu:\n'
            '        "第二个":\n'
            '            jump two\n'
            '    return\n'
        )
        original, produced, props = roundtrip(script, tmp_path, 'menu_two')
        assert_same_multiset(original, produced, props)
        assert props['rpy'].count('menu:') == 2, props['rpy']

    def test_option_without_body_gets_pass(self, tmp_path):
        """空块在 Ren'Py 里是语法错误 —— 必须补 `pass`，且结果仍可解析。"""
        script = (
            'label a:\n'
            '    menu:\n'
            '        "什么也不做":\n'
            '        "走":\n'
            '            jump go\n'
            '    return\n'
        )
        _original, _produced, props = roundtrip(script, tmp_path, 'menu_pass')
        assert 'pass' in props['rpy'], props['rpy']
        # 产物必须能被自己的解析器吃回去且不报错
        parser = RenPyParser()
        out = tmp_path / 'menu_pass_out.rpy'
        parser.parse(out)
        assert not parser.errors, parser.errors


class TestConditionRoundtrip:
    """
    P0-2：`Beat.condition` 必须**落 Excel 并回得来**。

    以前 `to_row` / `from_row` 两头都没有它，于是 `.rpy → .rpy` 看着正常，
    一经过 Excel 就全丢 —— 所有 `if` 分支被抹平成顺序执行。
    """

    IF_ELIF_ELSE = (
        'label a:\n'
        '    "开场"\n'
        '    if love > 5:\n'
        '        "高好感"\n'
        '    elif love > 2:\n'
        '        "中好感"\n'
        '    else:\n'
        '        "低好感"\n'
        '    "结束"\n'
        '    return\n'
    )

    NESTED_IF = (
        'label a:\n'
        '    if flag:\n'
        '        if love > 5:\n'
        '            "两重条件"\n'
        '        "只在外层"\n'
        '    "外面"\n'
        '    return\n'
    )

    def test_condition_column_exists(self):
        from storygraph.story.script_table import BEAT_FIELDS
        assert 'Condition' in BEAT_FIELDS

    def test_condition_survives_excel(self, tmp_path):
        original, _produced, props = roundtrip(self.IF_ELIF_ELSE, tmp_path,
                                               'cond')
        conds = [b.condition for b in props['beats'] if b.condition]
        back_conds = [b.condition for b in props['back'] if b.condition]
        assert conds, '解析出来的条件不该为空'
        # 核心断言：**落表回导之后条件一条不少，内容也不变**
        assert back_conds == conds, (conds, back_conds)

    def test_if_lines_survive(self, tmp_path):
        _original, _produced, props = roundtrip(self.IF_ELIF_ELSE, tmp_path,
                                                'cond_if')
        if_lines = [ln for ln in props['rpy'].splitlines()
                    if ln.strip().startswith('if ')]
        assert len(if_lines) == 3, props['rpy']

    def test_nested_condition_preserved(self, tmp_path):
        _original, _produced, props = roundtrip(self.NESTED_IF, tmp_path,
                                                'cond_nest')
        back_conds = [b.condition for b in props['back'] if b.condition]
        assert any('flag' in c for c in back_conds), back_conds
        assert any('love > 5' in c for c in back_conds), back_conds


class TestTextFidelityRoundtrip:
    """台词内容本身（含尾部空格）必须逐字还回来。"""

    def test_trailing_space_is_not_eaten(self, tmp_path):
        """
        P1-3：`'元神，启动！ '` 的尾空格。

        严重性低（不影响 Ren'Py 语义），但它与项目自己定的"逐字节不变"
        契约冲突，而且会让"打开→导出"产生无意义的 diff。
        """
        script = ('label a:\n'
                  '    e "元神，启动！ "\n'
                  '    return\n')
        _original, _produced, props = roundtrip(script, tmp_path, 'trail')
        assert props['beats'][0].text == '元神，启动！ '
        assert props['back'][0].text == '元神，启动！ ', \
            repr(props['back'][0].text)

    def test_tight_dollar_survives_excel(self, tmp_path):
        """
        紧凑 `$x` 与 `$ x` 必须分别保真。

        标记本身不能是控制字符：`openpyxl` 拒收控制字符，写表时只能剥掉，
        读回来就认不出紧凑写法（真实项目 22 条代码因此被改写）。
        """
        script = ('label a:\n'
                  '    $persistent.lock= True\n'
                  '    $ love = 1\n'
                  '    e "x"\n'
                  '    return\n')
        _original, _produced, props = roundtrip(script, tmp_path, 'dollar')
        body = [ln.strip() for ln in props['rpy'].splitlines()
                if ln.strip().startswith('$')]
        assert '$persistent.lock= True' in body, body
        assert '$ love = 1' in body, body


class TestStageRoundtrip:
    """舞台 / 音频 / 代码块这类"结构化列与高级列双通道"的保真。"""

    def test_scene_show_hide_audio_survive(self, tmp_path):
        script = (
            'label a:\n'
            '    scene bg classroom at bgfit\n'
            '    show ling happy at left\n'
            '    play music night fadein 1.0\n'
            '    e "一句"\n'
            '    hide ling with dissolve\n'
            '    stop music fadeout 2.0\n'
            '    "旁白"\n'
            '    return\n'
        )
        original, produced, props = roundtrip(script, tmp_path, 'stage')
        assert_same_multiset(original, produced, props)
        assert 'fadein 1.0' in props['rpy'], props['rpy']
        assert 'fadeout 2.0' in props['rpy'], props['rpy']
        assert 'at bgfit' in props['rpy'], props['rpy']

    def test_comment_survives(self, tmp_path):
        script = ('label a:\n'
                  '    # 给美术的标注：这张要重画\n'
                  '    e "一句"\n'
                  '    return\n')
        original, produced, props = roundtrip(script, tmp_path, 'comment')
        assert_same_multiset(original, produced, props)

    def test_code_block_interleaving_survives(self, tmp_path):
        """`$ f()` 与 `show screen`/`pause` 的**交错顺序**必须还原。"""
        script = (
            'label a:\n'
            '    $ msg_add("hello")\n'
            '    show screen msg\n'
            '    pause\n'
            '    hide screen msg\n'
            '    $ msg_add("world")\n'
            '    return\n'
        )
        _original, _produced, props = roundtrip(script, tmp_path, 'interleave')
        seq = [ln.strip() for ln in props['rpy'].splitlines()
               if ln.strip() and not ln.strip().startswith('#')
               and not ln.strip().startswith('label')
               and ln.strip() != 'return']
        assert seq == ['$ msg_add("hello")', 'show screen msg', 'pause',
                       'hide screen msg', '$ msg_add("world")'], seq


class TestFullChapterRoundtrip:
    """一条"什么都有"的章节：整条链路的兜底不变式。"""

    CHAPTER = (
        'define e = Character("Eileen")\n'
        '\n'
        'label ch01:\n'
        '    # 开场\n'
        '    scene bg classroom\n'
        '    show eileen happy at left\n'
        '    play music bgm_theme fadein 1.0\n'
        '    e "早上好。"\n'
        '    $ love = 0\n'
        '    if love > 5:\n'
        '        e "我很高兴。"\n'
        '    else:\n'
        '        e "嗯。"\n'
        '    menu:\n'
        '        "去屋顶" if love >= 0:\n'
        '            show eileen smile\n'
        '            jump roof\n'
        '        "回教室":\n'
        '            jump classroom\n'
        '    return\n'
        '\n'
        'label roof:\n'
        '    scene bg rooftop\n'
        '    hide eileen with dissolve\n'
        '    stop music fadeout 1.5\n'
        '    "风很大。"\n'
        '    return\n'
    )

    def test_nothing_is_lost(self, tmp_path):
        original, produced, props = roundtrip(self.CHAPTER, tmp_path, 'full')
        assert_same_multiset(original, produced, props)

    def test_produced_script_parses_without_errors(self, tmp_path):
        _original, _produced, props = roundtrip(self.CHAPTER, tmp_path,
                                                'full_parse')
        out = tmp_path / 'full_parse_out.rpy'
        parser = RenPyParser()
        parser.parse(out)
        assert not parser.errors, parser.errors

    def test_stable_across_two_roundtrips(self, tmp_path):
        """往返两次的结果必须与往返一次相同（幂等，不累积漂移）。"""
        script = self.CHAPTER
        src = tmp_path / 'twice_src.rpy'
        src.write_text(script, encoding='utf-8')
        result = ParseResultBuilder().build(RenPyParser().parse(src))
        beats = beats_from_statements(result.statements,
                                      labels=result.statement_labels)

        first = render_beats_to_rpy(beats, label_name='ch01')
        second = None
        for i in range(2):
            p = tmp_path / f'twice_{i}.rpy'
            p.write_text(first if i == 0 else second, encoding='utf-8')
            pr = ParseResultBuilder().build(RenPyParser().parse(p))
            b2 = beats_from_statements(pr.statements, labels=pr.statement_labels)
            second = render_beats_to_rpy(b2, label_name='ch01')
        assert first == second, f'第一次:\n{first}\n第二次:\n{second}'


class TestPythonBlockRoundtrip:
    """
    `python:` 块是**缩进敏感**的结构，而「代码」列只有一个格子。

    这里守住两件事（2026-10-05 修，都是真实项目实测出来的）：

      · **嵌套层级不许被压平** —— `for` 里的 `if` 里的语句必须还在第三层；
        压平之后 Ren'Py 直接语法错误 / 语义全变。
      · **块里的行不许被拆成一条条 `$` 语句** —— `$` 在 Ren'Py 里是
        单行语句，`$ for i in ...:` 后面跟 `$ body` 根本不是合法 python。
    """

    NESTED = (
        'label a:\n'
        '    python:\n'
        '        total = 0\n'
        '        for i in range(3):\n'
        '            if i > 0:\n'
        '                total += i\n'
        '            total += 1\n'
        '    "done"\n'
        '    return\n'
    )

    def test_nested_indentation_survives(self, tmp_path):
        original, produced, props = roundtrip(self.NESTED, tmp_path, 'pyblock')
        assert_same_multiset(original, produced, props)
        lines = props['rpy'].splitlines()
        assert '    python:' in lines, props['rpy']
        assert '        for i in range(3):' in lines, props['rpy']
        assert '            if i > 0:' in lines, props['rpy']
        assert '                total += i' in lines, props['rpy']
        assert '            total += 1' in lines, props['rpy']

    def test_multiline_statement_keeps_alignment(self, tmp_path):
        """
        括号续行的对齐要保住：`positions = [` 后面几行不能被逐行补 `$`
        （那样 `$ Call(...)` 就成了一条独立语句 —— 语法错误）。
        """
        script = (
            'label a:\n'
            '    $ positions = [\n'
            '        (1, 2),\n'
            '        (3, 4),\n'
            '    ]\n'
            '    "x"\n'
            '    return\n'
        )
        _original, _produced, props = roundtrip(script, tmp_path, 'multiline')
        rpy = props['rpy']
        assert 'positions = [' in rpy, rpy
        # 续行必须跟在第一行后面（同一条语句），而不是各带一个 `$`
        assert rpy.count('$ (1, 2)') == 0, rpy
        assert rpy.count('$ (3, 4)') == 0, rpy

    def test_show_screen_not_swallowed_into_python_block(self, tmp_path):
        """
        `window hide` / `hide screen` 这类 Ren'Py 语句**绝不能**被包进
        `python:` 块（那是语法错误、整段演出消失）。

        实测来源：`mini_game_dfinder.rpy` —— 代码格里混着多行 `show screen`
        语句（续行带缩进），旧实现只看"有没有缩进"就把整格包成了 `python:`。
        """
        script = (
            'label a:\n'
            '    python:\n'
            '        game = 1\n'
            '    window hide\n'
            '    hide screen prompt\n'
            '    show screen prompt("again", act=[\n'
            '        Call("again"),\n'
            '    ])\n'
            '    "x"\n'
            '    return\n'
        )
        _original, _produced, props = roundtrip(script, tmp_path, 'mixed')
        rpy = props['rpy']
        # Ren'Py 语句不能在 python 块里
        for line in rpy.splitlines():
            if line.strip().startswith(('window hide', 'hide screen')):
                assert not line.startswith('        '), rpy
        assert 'window hide' in rpy, rpy
        assert 'hide screen prompt' in rpy, rpy
        assert 'show screen prompt(' in rpy, rpy


class TestCallFromClauseRoundtrip:
    """
    `call X from Y` 的 `from` 是 **Ren'Py 存档点标识**。

    往返里丢掉它的代价不是"少几个字符"：同一个 label 被多处 call 时，
    老存档靠 `_call_xxx` 定位；一旦退化成裸 `call`，**玩家读档会落到别处**。
    真实项目 `chapter_controller.rpy` 实测 7 条全中招。
    """

    SCRIPT = (
        'label controller:\n'
        '    call chapter0_0 from _call_chapter0_0\n'
        '    "middle"\n'
        '    call chapter1_1 from _call_chapter1_1\n'
        '    return\n'
    )

    def test_from_clause_survives(self, tmp_path):
        original, produced, props = roundtrip(self.SCRIPT, tmp_path, 'callfrom')
        assert_same_multiset(original, produced, props)
        assert 'call chapter0_0 from _call_chapter0_0' in props['rpy']
        assert 'call chapter1_1 from _call_chapter1_1' in props['rpy']

    def test_plain_call_stays_plain(self, tmp_path):
        """没有 `from` 的 call 不能凭空长出一个存档点标识。"""
        script = ('label a:\n'
                  '    call other\n'
                  '    return\n')
        _original, _produced, props = roundtrip(script, tmp_path, 'callplain')
        assert 'call other' in props['rpy']
        assert ' from ' not in props['rpy']


class TestShowExpressionRoundtrip:
    """
    `show expression <表达式>` 的图像名是一段 **Python 代码**
    （`holo("lan base")`），里面可能有空格/括号/引号。

    它不能用 `图像@位置` 那种紧凑编码，否则位置（`at i11`）与别名（`as q`）
    会在写表时丢掉 —— 真实项目 `script.rpy` 实测 5 条。
    """

    SCRIPT = (
        'label a:\n'
        '    show expression holo("lan base") at i11\n'
        '    "hi"\n'
        '    show expression holo("qing base") as qing at i11\n'
        '    "bye"\n'
        '    return\n'
    )

    def test_expression_keeps_keyword_at_as(self, tmp_path):
        original, produced, props = roundtrip(self.SCRIPT, tmp_path, 'showexpr')
        assert_same_multiset(original, produced, props)
        assert 'show expression holo("lan base") at i11' in props['rpy']
        assert ('show expression holo("qing base") as qing at i11'
                in props['rpy']), props['rpy']

    def test_expression_cell_is_readable(self, tmp_path):
        """写进「立绘」列的是**自描述子句**，不是 `图像@位置`（读不出来）。"""
        _original, _produced, props = roundtrip(self.SCRIPT, tmp_path, 'sexpr2')
        cell = props['back'][0].show
        assert cell.startswith('expression '), cell
        assert 'at i11' in cell, cell


class TestEmptySayIsNotAStatement:
    """
    空台词（`who` 与 `what` 都为空）**不是演出内容**，不该产出语句。

    它来自空行与三引号文档字符串（`eyes_tracker.rpy` 实测 3 条全在
    `\"\"\"…\"\"\"` 里）。留着它的代价不是"多一行"：`Beat.is_empty()` 会把
    那张拍丢掉，整个文件因此退化成 `label start: pass`（实测产物就是这样），
    而这张拍在 Excel 往返里必然对不上。
    """

    def test_docstring_and_blank_lines_produce_nothing(self, tmp_path):
        script = (
            '"""\n'
            '模块文档：这里是一段说明\n'
            '\n'
            '"""\n'
            'screen foo():\n'
            '    pass\n'
        )
        original, _produced, props = roundtrip(script, tmp_path, 'docstr')
        assert not [s for s in original.statements if s.kind == 'say'], \
            [s.payload for s in original.statements if s.kind == 'say']
        assert props['beats'] == []

    def test_real_dialogue_still_parsed(self, tmp_path):
        """过滤器不能误伤真空台词（包括说话人为空、只有文字的旁白）。"""
        script = ('label a:\n'
                  '    "旁白"\n'
                  '    e "台词"\n'
                  '    return\n')
        original, produced, props = roundtrip(script, tmp_path, 'realsay')
        assert_same_multiset(original, produced, props)
        assert len(props['beats']) == 2


@pytest.mark.parametrize('kind', ['raw', 'comment', 'python'])
class TestStatementKindPlaceholders:
    """语句流的兼容视图（`Node.operations`）与结构化 IR 必须同源。"""

    def test_operation_source_roundtrip(self, kind):
        from storygraph.core.statement import (Statement, operation_source,
                                               python_operation)
        code = 'love += 1'
        stmt = python_operation(code)
        assert operation_source(stmt) == code
        stmt2 = Statement.make_raw('pause 1.0')
        assert operation_source(stmt2) == 'pause 1.0'
        # 不是"操作"的 kind 一律返回空串（调用方该看 kind）
        assert operation_source(Statement.make_say('e', 'x')) == ''
