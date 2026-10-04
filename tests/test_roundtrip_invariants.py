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
from storygraph.story.beat_model import (
    KIND_MENU_OPTION,
    KIND_NARRATION,
    Beat,
)


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

class TestMenuGroupingUnderEdits:
    """
    P1-2：**菜单分组是"重建"而不是"保持"** —— 策划在 Excel 里改一拍，
    分组会不会错乱？

    `split_beat_units` / `_take_menu_run` 靠「拍的顺序 + 条件（写表时补的
    `True`）」重建 `menu:` 块，而 `_menu_member`（解析器标的真结构位）
    **存不进 Excel**。所以真正要守的是"用 Excel 的拍序列还能不能还原"。

    判据（这一族错误都是**静默**的，产物能跑、不报错）：
      · **少吞一拍** → 分支体掉到菜单外，**无条件执行**；
      · **多吞一拍** → 块外那句被塞进最后一个选项，**只有选中它才播**。
    """

    #: 一条带块体的菜单 + 块外一句（最容易被吞/被漏的形状）
    SOURCE = (
        'label a:\n'
        '    "开场"\n'
        '    menu:\n'
        '        "选项A":\n'
        '            "A 的内容"\n'
        '        "选项B":\n'
        '            "B 的内容"\n'
        '    "菜单之后"\n'
        '    return\n'
    )

    def _render_from_beats(self, beats):
        return render_beats_to_rpy(beats, label_name='a')

    def test_baseline_menu_blocks_are_intact(self, tmp_path):
        """先确认基线：两个选项各自成块，块外那句在菜单外。"""
        _o, _p, props = roundtrip(self.SOURCE, tmp_path, 'mg_base')
        rpy = props['rpy']
        assert rpy.count('menu:') == 1, rpy
        assert '"选项A":' in rpy and '"选项B":' in rpy, rpy
        # 两句内容都要**缩进在选项体里**
        for text in ('"A 的内容"', '"B 的内容"'):
            line = next(ln for ln in rpy.splitlines() if text in ln)
            assert line.startswith(' ' * 12), rpy
        # 菜单之后那句必须在 menu 块**外**（缩进回到 label 那一级）
        after = next(ln for ln in rpy.splitlines() if '"菜单之后"' in ln)
        assert after.startswith(' ' * 4), rpy

    def test_inserting_a_beat_between_options_keeps_the_menu(self, tmp_path):
        """
        策划在**两个选项之间**插一拍（最典型的"改剧情"动作）。

        插进来的拍不带选项条件（条件是空串），所以它**不属于**任何选项体：
        正确的还原是"它把菜单切成两半"或"它进不了菜单"，但**绝不能**
        把它当成某个选项的块体吞掉（那是"只有选中才播"的静默错误）。

        实测（2026-10-05）修复前：那一拍让 `_take_menu_run` 当场收尾，
        **后面的选项全部掉出菜单** —— 产物里 `"选项B"` 变成一句普通旁白、
        它的块体成了无条件执行的 `if True:`。整块菜单被一拍拆散。

        修复后：菜单结构完整（组只收在"下一个选项/成员拍之前"），
        那一拍按**原位置**收进它前面的选项体，并加一句 `# TODO` 让它可见
        —— Ren'Py 的 `menu:` 里除选项外没有合法位置，只能这么放，
        但**不能悄悄放**。
        """
        _o, _p, props = roundtrip(self.SOURCE, tmp_path, 'mg_insert')
        beats = list(props['back'])
        # 找到第一张选项拍，在它**后面**插一句旁白（模拟"选项A 之后加一句"）
        idx = next(i for i, b in enumerate(beats)
                   if b.kind == KIND_MENU_OPTION)
        beats.insert(idx + 1, Beat(label='a', kind=KIND_NARRATION,
                                   text='插进来的旁白'))
        rpy = self._render_from_beats(beats)
        # 结构不许散：还是一个菜单、两个选项
        assert rpy.count('menu:') == 1, rpy
        assert '"选项A":' in rpy and '"选项B":' in rpy, rpy
        # 插进来的旁白按原位置排在选项A 体内，并且**看得见警告**
        assert '插进来的旁白' in rpy, rpy
        assert '# TODO' in rpy, rpy
        # 它的块体不能再被降级成无条件执行的裸台词
        assert 'if True:\n    "A 的内容"' not in rpy, rpy
        # 菜单之后那句仍在菜单外
        after = next(ln for ln in rpy.splitlines() if '"菜单之后"' in ln)
        assert after.startswith(' ' * 4), rpy

    def test_appending_a_beat_after_the_menu_keeps_it_outside(self, tmp_path):
        """
        菜单**之后**再加一句 —— 它必须留在菜单外（不能被最后一个选项吞掉）。

        这是"多吞一拍"的方向，也是最危险的方向：被吞之后那句话
        **只有选中最后一个选项才会播**。
        """
        _o, _p, props = roundtrip(self.SOURCE, tmp_path, 'mg_append')
        beats = list(props['back'])
        beats.append(Beat(label='a', kind=KIND_NARRATION, text='新加的结尾'))
        rpy = self._render_from_beats(beats)
        line = next(ln for ln in rpy.splitlines() if '新加的结尾' in ln)
        assert line.startswith(' ' * 4), (
            f'菜单之后新加的拍被吞进选项体了：{line!r}\n{rpy}')

    def test_reordering_options_keeps_both_blocks(self, tmp_path):
        """策划把两个选项**对调**：两个块体都要跟着自己的选项走。"""
        _o, _p, props = roundtrip(self.SOURCE, tmp_path, 'mg_swap')
        beats = list(props['back'])
        opt_idx = [i for i, b in enumerate(beats)
                   if b.kind == KIND_MENU_OPTION]
        assert len(opt_idx) == 2, [b.kind for b in beats]
        i, j = opt_idx
        # 交换两张选项拍（块体不动）—— 归一化之后块体应当各跟各的选项
        beats[i], beats[j] = beats[j], beats[i]
        rpy = self._render_from_beats(beats)
        assert rpy.count('menu:') == 1, rpy
        a = next(ln for ln in rpy.splitlines() if 'A 的内容' in ln)
        b = next(ln for ln in rpy.splitlines() if 'B 的内容' in ln)
        assert a.startswith(' ' * 12) and b.startswith(' ' * 12), rpy

    def test_deleting_an_option_keeps_the_rest(self, tmp_path):
        """删掉一个选项及其块体：另一个选项必须还在菜单里。"""
        _o, _p, props = roundtrip(self.SOURCE, tmp_path, 'mg_del')
        beats = list(props['back'])
        idx = next(i for i, b in enumerate(beats)
                   if b.kind == KIND_MENU_OPTION)
        # 删掉选项A 以及紧跟的块体
        end = idx + 1
        while end < len(beats) and beats[end].kind != KIND_MENU_OPTION:
            end += 1
        del beats[idx:end]
        rpy = self._render_from_beats(beats)
        assert rpy.count('menu:') == 1, rpy
        assert '"选项B":' in rpy, rpy
        assert '"选项A":' not in rpy, rpy
        b = next(ln for ln in rpy.splitlines() if 'B 的内容' in ln)
        assert b.startswith(' ' * 12), rpy
        after = next(ln for ln in rpy.splitlines() if '菜单之后' in ln)
        assert after.startswith(' ' * 4), rpy

    def test_menu_after_menu_stays_two_menus(self, tmp_path):
        """连续两个菜单**不许**并成一块（并了就是两个菜单的文字混在一起）。"""
        script = (
            'label a:\n'
            '    menu:\n'
            '        "第一个 A":\n'
            '            "A 体"\n'
            '        "第一个 B":\n'
            '            "B 体"\n'
            '    menu:\n'
            '        "第二个 A":\n'
            '            "C 体"\n'
            '    return\n'
        )
        _o, _p, props = roundtrip(script, tmp_path, 'mg_two')
        rpy = props['rpy']
        assert rpy.count('menu:') == 2, rpy
        # 第二个菜单的第一个选项不能被接到第一个菜单里
        for text in ('"A 体"', '"B 体"', '"C 体"'):
            line = next(ln for ln in rpy.splitlines() if text in ln)
            assert line.startswith(' ' * 12), rpy


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


class TestBranchScopedStage:
    """
    P1-1：**分支内的舞台指令必须留在自己的分支里**。

    这是评估报告之后那份清单里"静默改演出"的第二例，根因与 menu 一模一样：
    扁平拍装不下嵌套结构。

    实测样本（`pomodorro/script_pomo.rpy`）：

        if pomoplace == "beach":
            show bgocean with dissolve
        elif pomoplace == "cafe":
            show bgcafe with dissolve
        elif pomoplace == "library":
            show bglibrary with dissolve
        show qing base at h11

    解析器**明明给每条 `show` 都带了条件**（`pomoplace == "beach"` /
    `(pomoplace == "cafe") and not (…)` / `(pomoplace == "library") and not (…)`），
    但 `beats_from_statements` 把这三条舞台指令**吸进同一张拍**，而一张拍
    只有一个 `condition`。结果产物变成三个背景依次无条件 show，
    最后一个永远生效 —— **玩家永远看到 library**，且不报任何错。
    """

    SCRIPT = (
        'label pomodoro:\n'
        '    hide homo\n'
        '    if pomoplace == "beach":\n'
        '        show bgocean with dissolve\n'
        '    elif pomoplace == "cafe":\n'
        '        show bgcafe with dissolve\n'
        '    elif pomoplace == "library":\n'
        '        show bglibrary with dissolve\n'
        '    show qing base at h11\n'
        '    return\n'
    )

    def test_each_show_keeps_its_own_condition(self, tmp_path):
        _original, _produced, props = roundtrip(self.SCRIPT, tmp_path,
                                                'branchstage')
        rpy = props['rpy']
        # 三条 show 必须各自被 `if` 包住 —— 不能无条件执行
        for img in ('bgocean', 'bgcafe', 'bglibrary'):
            assert f'show {img}' in rpy, rpy
            guarded = any(
                ln.strip().startswith('if ') and img in later
                for ln, later in zip(
                    rpy.splitlines(),
                    rpy.splitlines()[1:] + [''])
            )
            assert guarded, f'`show {img}` 没有被 if 包住：\n{rpy}'
        # 条件本身必须还在（`pomoplace` 至少出现 3 次）
        assert rpy.count('pomoplace') >= 3, rpy
        # 块外那条 `show qing base` 不许被套上任何分支条件
        lines = rpy.splitlines()
        idx = next(i for i, ln in enumerate(lines) if 'show qing' in ln)
        assert not lines[idx - 1].strip().startswith('if '), rpy

    def test_branch_stage_roundtrips_through_excel(self, tmp_path):
        """同样的事必须活过 Excel 一圈（条件列 + 分组）。"""
        original, produced, props = roundtrip(self.SCRIPT, tmp_path,
                                              'branchstage_x')
        assert_same_multiset(original, produced, props)
        conds = [b.condition for b in props['back'] if b.condition]
        assert len(conds) >= 3, conds

    def test_produced_script_parses(self, tmp_path):
        _original, _produced, props = roundtrip(self.SCRIPT, tmp_path,
                                                'branchstage_p')
        out = tmp_path / 'branchstage_p_out.rpy'
        parser = RenPyParser()
        parser.parse(out)
        assert not parser.errors, parser.errors

    def test_block_stage_not_swallowed_by_next_branch(self, tmp_path):
        """
        **块外的舞台指令不许被后一个分支的台词吸进去**。

        实测 `script_pomo.rpy` 第 194 行：

            193:        show bglibrary with dissolve     ← elif 分支里
            194:    show qing base at h11                 ← **块外**（无条件）
            196:    if breakline == 1:
            197:        q "时间到了……"                    ← 条件 breakline == 1

        修 P1-1 之前，第 194 行的舞台指令和第 197 行的台词被吸进**同一张拍**，
        而那张拍的条件是 `breakline == 1` —— 于是 `show qing base`
        跟着台词一起被 `if breakline == 1:` 包住：只有 `breakline == 1`
        时立绘才出现。原文里它**无条件执行**。
        """
        script = (
            'label a:\n'
            '    show qing base at h11\n'
            '    if breakline == 1:\n'
            '        q "一"\n'
            '    return\n'
        )
        _original, _produced, props = roundtrip(script, tmp_path, 'swallow')
        lines = props['rpy'].splitlines()
        idx = next(i for i, ln in enumerate(lines) if 'show qing' in ln)
        assert not lines[idx - 1].strip().startswith('if '), props['rpy']
        assert lines[idx - 1].strip() != 'if breakline == 1:', props['rpy']

    def test_stage_split_from_say_keeps_order(self, tmp_path):
        """拆开之后，舞台指令仍然在台词**之前**（顺序不许乱）。"""
        script = (
            'label a:\n'
            '    show qing base at h11\n'
            '    if breakline == 1:\n'
            '        q "一"\n'
            '    return\n'
        )
        original, produced, props = roundtrip(script, tmp_path, 'order')
        assert_same_multiset(original, produced, props)
        lines = props['rpy'].splitlines()
        show_i = next(i for i, ln in enumerate(lines) if 'show qing' in ln)
        say_i = next(i for i, ln in enumerate(lines) if 'q "一"' in ln)
        assert show_i < say_i, props['rpy']


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
    """台词内容本身（含首尾空格）必须逐字还回来。"""

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

    def test_leading_space_is_not_eaten(self, tmp_path):
        """
        **首部空格也是台词内容**（2026-10-05 修）。

        `beat.text` 是字符串字面量的**内容**，里面的空格就是内容：

            jc " 听起来很有意思，来吧！"
               ^ 这个空格是台词的一部分

        `from_row` 以前对它 `lstrip()`（注释里写的理由是"Ren'Py 里台词
        字符串前面的空格没有语义"——**这个判断是错的**），于是往返之后
        变成 `"听起来很有意思，来吧！"`。真实项目 `script_pomo.rpy`
        一处就是这么丢的（审计里 6 条之一）。
        """
        script = ('label a:\n'
                  '    jc " 听起来很有意思，来吧！"\n'
                  '    return\n')
        _original, _produced, props = roundtrip(script, tmp_path, 'lead')
        assert props['beats'][0].text == ' 听起来很有意思，来吧！', \
            repr(props['beats'][0].text)
        assert props['back'][0].text == ' 听起来很有意思，来吧！', \
            repr(props['back'][0].text)
        assert 'jc " 听起来很有意思，来吧！"' in props['rpy'], props['rpy']

    def test_backslashes_and_quotes_survive(self, tmp_path):
        """
        台词里的**反斜杠与引号**不许被二次转义。

        真实样本（`chapter1_2.rpy`，程序员的调试输出被写成了旁白）：

            "file \\"c:\\\\admin_script.py\\", line 24, in <module>"

        渲染时 `_quote` 会把 `\\` → `\\\\`、`"` → `\\"`（这是**对的**：
        写进 .rpy 必须转义），但读回来必须**反转义一次且只一次**。
        少了这段，审计里那一条就是"原文一次转义、产物两次转义"。
        """
        script = ('label a:\n'
                  '    "file \\"c:\\\\\\\\admin_script.py\\", line 24, "\n'
                  '    "in <module>"\n'
                  '    return\n')
        original, produced, props = roundtrip(script, tmp_path, 'escape')
        assert_same_multiset(original, produced, props)

    def test_menu_caption_escapes_do_not_accumulate(self, tmp_path):
        """
        **菜单标题**里的转义必须自反（2026-10-05 修）。

        `tools/hell_edit_rounds.py` 的 A2 轴实测：标题里的 `\\n` 每导出一轮
        就翻一倍 ——

            第一轮: menu "第一行\\n第二行":
            第二轮: menu "第一行\\\\n第二行":
            第三轮: menu "第一行\\\\\\\\n第二行":

        根因是渲染侧 `_quote` 转义了、解析侧 `_scan_menu_header` 原样切片
        （台词那条路有 `_pick_str` 反转义，所以只有标题不对称）。
        影响不只是显示：标题里的 `\\n` 强制换行退化成字面量，而且那一行
        **每轮都变** → 增量的对位每轮都失败。
        """
        for stem, caption in (('cap_nl', r'第一行\n第二行'),
                              ('cap_bs', r'路径 C:\\new'),
                              ('cap_both', r'A\\nB\nC')):
            script = ('label a:\n'
                      f'    menu "{caption}":\n'
                      '        "Yes":\n'
                      '            e "体"\n'
                      '    return\n')
            _original, produced, props = roundtrip(script, tmp_path, stem)
            assert_same_multiset(_original, produced, props)
            # 渲染 → 再解析 → 再渲染：**逐字节**不动点
            second = compile_beats(beats_from_statements(
                produced.statements,
                labels=produced.statement_labels)).rpy_text
            assert second == props['rpy'], \
                f'{stem}：第二轮变了\n{props["rpy"]}\n---\n{second}'

    def test_option_text_escapes_do_not_accumulate(self, tmp_path):
        """选项文案与台词同理（`_parse_menu_option` 用 `_pick_str`，本来是对的，
        这条是**钉住**它别退化）。"""
        for stem, opt in (('opt_nl', r'选项\n第二行'),
                          ('opt_bs', r'路径 C:\\new')):
            script = ('label a:\n'
                      '    menu "标题":\n'
                      f'        "{opt}":\n'
                      '            e "体"\n'
                      '    return\n')
            original, produced, props = roundtrip(script, tmp_path, stem)
            assert_same_multiset(original, produced, props)
            second = compile_beats(beats_from_statements(
                produced.statements,
                labels=produced.statement_labels)).rpy_text
            assert second == props['rpy'], f'{stem}：第二轮变了'

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


class TestCustomTransformRoundtrip:
    """
    P2-4：**自定义 transform 的链路到底是通的吗**。

    清单（`docs/问题清单与优化路线_2026-10-03.md` 第一批第 3 项）问的是这个。
    复验结论：**通，但不是走 `beat.transform`** ——

      · 每个立绘自己的变换（`show eileen happy at leftish`）走 `beat.at`，
        编码成 `立绘` 列的 `eileen happy@leftish`，往返完整；
      · 背景自己的变换（`scene bg room at bgfit`）走 `beat.scene_at`；
      · 整拍的转场（`with Dissolve(0.5)`）走 `beat.with_`。

    `beat.transform` 是**旧模型的残留**：它只在渲染时被读
    （`with <transform>`），解析与 Excel 回导**都不写它**，所以真实项目
    2013 拍里有值 0 次。这几条测试把"真正在用的那条路"钉住。
    """

    def test_per_sprite_custom_transform_survives(self, tmp_path):
        script = ('label a:\n'
                  '    show eileen happy at leftish\n'
                  '    e "一句"\n'
                  '    return\n')
        original, produced, props = roundtrip(script, tmp_path, 'tf_sprite')
        assert_same_multiset(original, produced, props)
        assert 'at leftish' in props['rpy'], props['rpy']
        assert props['beats'][0].show == 'eileen happy@leftish', \
            props['beats'][0].show

    def test_scene_transform_survives(self, tmp_path):
        script = ('label a:\n'
                  '    scene bg room at bgfit\n'
                  '    "旁白"\n'
                  '    return\n')
        original, produced, props = roundtrip(script, tmp_path, 'tf_scene')
        assert_same_multiset(original, produced, props)
        assert 'at bgfit' in props['rpy'], props['rpy']

    def test_expression_transform_survives(self, tmp_path):
        """
        表达式变换（`at Transform(xysize=(1920, 1080))`）位置不许丢。

        **括号里的逗号也必须逐字保留**（2026-10-05 修）：

            show image puzzle_path at Transform(xysize=(1920, 1080))

        分词器以前把逗号一律当分隔符，于是 `at` 收成
        `['Transform(xysize=(1920', '1080))']`，再拼回去（空格连接多目标）
        变成 `at Transform(xysize=(1920 1080))` —— **Python 语法错误**，
        产物直接跑不起来。

        注意这条**多重集断言抓不住**：`_key` 会把 `at` 列表归一化，两边
        看着"一样"。所以这里额外断言产物的**逐字文本**。
        真实项目 `mini_game_puzzle.rpy` 第 262/284 行就是这个写法。
        """
        script = ('label a:\n'
                  '    show image puzzle_path at Transform(xysize=(1920, 1080))\n'
                  '    "旁白"\n'
                  '    return\n')
        original, produced, props = roundtrip(script, tmp_path, 'tf_expr')
        assert_same_multiset(original, produced, props)
        assert 'at Transform(xysize=(1920, 1080))' in props['rpy'], props['rpy']

    def test_multi_target_at_keeps_its_comma(self, tmp_path):
        """多目标 `at left, right` 里的逗号同样不许并成空格。"""
        script = ('label a:\n'
                  '    show ling at left, right\n'
                  '    "旁白"\n'
                  '    return\n')
        original, produced, props = roundtrip(script, tmp_path, 'tf_multi')
        assert_same_multiset(original, produced, props)
        assert 'at left, right' in props['rpy'], props['rpy']

    def test_bare_with_transition_survives(self, tmp_path):
        script = ('label a:\n'
                  '    e "一句"\n'
                  '    with Dissolve(0.5)\n'
                  '    e "两句"\n'
                  '    return\n')
        original, produced, props = roundtrip(script, tmp_path, 'tf_with')
        assert_same_multiset(original, produced, props)
        assert 'with Dissolve(0.5)' in props['rpy'], props['rpy']


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

    def test_blank_line_inside_a_python_block_survives(self, tmp_path):
        """
        `python:` 块**内部**的空行必须活下来。

        实测来源（2026-10-05，真实项目 `mini_game_puzzle.rpy`）：
        多行调用的参数表中间有一个空行

            renpy.store.drag_puzzle_game = drag_puzzle(
                        puzzle_path,
                        rows=2,
                        cols=3,
                                                     ← 这个空行
                        grid_width=800,
                        )

        两个地方会把它吃掉，两个都修了：

          1. `_render_marked_code_block` 收集块体时**遇到空行就收尾**
             （块标记是行首字符，空行没有行首字符）—— 于是空行之后那几行
             掉出块体、`python:` 被从中间截断。判据改成"往前看"：
             空行后面还有块体行才算块内空行。
          2. `render_beats_to_rpy` 收尾时 `[ln for ln in body if ln.strip()]`
             **把整个 label 的空行全扔了**。改成只去首尾。

        空行在 Python 里没有语义，但**丢掉它就是丢掉作者手写的排版**，
        而往返审计按逐行文本比 —— 算一条丢失 + 一条新增。
        """
        script = (
            'label a:\n'
            '    python:\n'
            '        game = drag(\n'
            '            path,\n'
            '            rows=2,\n'
            '\n'
            '            cols=3)\n'
            '    "x"\n'
            '    return\n'
        )
        _original, _produced, props = roundtrip(script, tmp_path, 'blankline')
        rpy = props['rpy']
        body = [ln for ln in rpy.splitlines() if 'rows=2,' in ln
                or 'cols=3)' in ln or 'path,' in ln]
        assert body, rpy
        # 块内那三行之间必须还有一个空行
        idx_rows = next(i for i, ln in enumerate(rpy.splitlines())
                        if 'rows=2,' in ln)
        idx_cols = next(i for i, ln in enumerate(rpy.splitlines())
                        if 'cols=3)' in ln)
        between = rpy.splitlines()[idx_rows + 1:idx_cols]
        assert any(not ln.strip() for ln in between), (
            f'块内空行被吃掉了：{between!r}\n{rpy}')

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
        # python 代码与 Ren'Py 语句必须**同时**在产物里（单行 python 块
        # 生成 `$ x` 是等价的 —— 见 `CodeBlocks` 的记录口径）
        assert '    $ game = 1' in rpy.splitlines(), rpy

    def test_two_python_blocks_in_one_cell_stay_separate(self, tmp_path):
        """
        一个格子里混着两条 `python:` 语句时，**块边界靠标记与隐藏列说话**。

        没有它就只能猜缩进，而"两个块的首行都在第 0 列"完全分不出来 ——
        猜错的代价是两块被并成一个（`for` 的体跑到另一个块的变量上）。
        """
        script = (
            'label a:\n'
            '    $ a = 1\n'
            '    python:\n'
            '        for i in range(2):\n'
            '            a += i\n'
            '    python:\n'
            '        for j in range(2):\n'
            '            a -= j\n'
            '    "x"\n'
            '    return\n'
        )
        original, produced, props = roundtrip(script, tmp_path, 'twoblocks')
        assert_same_multiset(original, produced, props)
        lines = props['rpy'].splitlines()
        # 两个块各自成 python:，嵌套层级都在
        assert lines.count('    python:') == 2, props['rpy']
        assert '        for i in range(2):' in lines, props['rpy']
        assert '        for j in range(2):' in lines, props['rpy']
        assert '            a += i' in lines, props['rpy']
        assert '            a -= j' in lines, props['rpy']


class TestCodeBlocksColumn:
    """
    隐藏列 `CodeBlocks`：`python:` 块在「代码」列里的**行区间**。

    它是"结构由数据说话"的落点 —— 用户 2026-10-05：「新增的列放远点，
    甚至可默认隐藏，因为事实上策划和文案不需要」。
    """

    def test_column_exists_and_is_hidden(self):
        from storygraph.story.script_table import (BEAT_FIELDS,
                                                   HIDDEN_BEAT_COLUMNS)
        assert 'CodeBlocks' in BEAT_FIELDS
        assert 'CodeBlocks' in HIDDEN_BEAT_COLUMNS
        # "放远点"：内部元数据排在最后（`FileBlocks` 一度也是隐藏列，
        # 2026-10-05 挪去隐藏表了 —— Excel 单元格 32767 的上限会静默截断
        # 真实文件的顶层区间，见 `beat_model.FILE_BLOCK_SHEET`）
        # `Nest`（结构位，B 阶段一）/ `Inline`（行尾注释，C）排在后面。
        assert BEAT_FIELDS[-3:] == ['CodeBlocks', 'Nest', 'Inline'], BEAT_FIELDS
        assert 'Nest' in HIDDEN_BEAT_COLUMNS
        assert 'FileBlocks' not in BEAT_FIELDS, BEAT_FIELDS

    def test_encoding_is_human_readable(self):
        """区间编码要人能读（万一有人展开隐藏列）。"""
        from storygraph.story.script_table import (Beat,
                                                   compose_code_blocks_cell,
                                                   parse_code_blocks_cell)
        assert compose_code_blocks_cell(Beat(code_blocks=[(1, 11), (13, 17)])) \
            == '1-11 13-17'
        assert compose_code_blocks_cell(Beat(code_blocks=[(3, 3)])) == '3'
        assert parse_code_blocks_cell('1-11 13-17') == [(1, 11), (13, 17)]
        assert parse_code_blocks_cell('3') == [(3, 3)]
        # 手改坏了不该让表读不进来：认不出的片段直接忽略，
        # 能认出一半的（`5-` = 只有起点）按单行算
        assert parse_code_blocks_cell('abc') == []
        assert parse_code_blocks_cell(None) == []
        assert parse_code_blocks_cell('7-3') == []
        assert parse_code_blocks_cell('5-  9-11') == [(5, 5), (9, 11)]

    def test_column_survives_excel(self, tmp_path):
        """回导之后块区间必须还在（老表 / 手工行没有它也不许炸）。"""
        script = ('label a:\n'
                  '    python:\n'
                  '        x = 1\n'
                  '        y = 2\n'
                  '    "t"\n'
                  '    return\n')
        _original, _produced, props = roundtrip(script, tmp_path, 'blockcol')
        assert props['back'][0].code_blocks == [(1, 2)], \
            props['back'][0].code_blocks

    def test_missing_column_falls_back_to_inference(self, tmp_path):
        """
        老表没有这一列（或手工新建行）时，渲染层退回"按缩进推断"——
        **最坏是块结构没保住，而不是产物改坏**。
        """
        from storygraph.story.script_table import (Beat, compose_code,
                                                   render_beat_to_rpy)
        beat = Beat(seq=1, label='a', code='x = 1\ny = 2')
        beat.code_blocks = []
        lines = render_beat_to_rpy(beat)
        assert lines == ['$ x = 1', '$ y = 2'], lines
        assert compose_code(beat) == 'x = 1\ny = 2'


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
