"""
刁钻形态：结构嵌套 + 混合内容（截图里 torture test 抓到的那两个）
================================================================

这两个形态**真实项目里一次都没出现**（真实项目的分支内都是纯舞台指令），
所以 `audit_branch_scope.py` 显示 12→13 全保住 —— 它是**合成用例**才暴露的。
这正是"构造真实数据没出现过的形态"的价值。

  · Bug 1：`if` 里包 `menu` —— 条件 / caption / 两个 jump 全丢，
    选项被降级成裸旁白（`kind` 都不是 `menu_option`）；
  · Bug 2：分支内"`show` + 台词混合" —— `elif` 分支整体消失。

两处都发生在**解析层**（连 Excel 都没到）。
"""

from __future__ import annotations

import pytest

from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder
from storygraph.pipeline.compile import compile_beats
from storygraph.story.beat_build import beats_from_statements
from storygraph.story.beat_render import render_beats_to_rpy


def _parse(script: str, tmp_path):
    src = tmp_path / 'a.rpy'
    src.write_text(script, encoding='utf-8')
    return ParseResultBuilder().build(RenPyParser().parse(src))


def _beats(pr):
    return beats_from_statements(pr.statements,
                                 labels=pr.statement_labels)


def _render(beats):
    return render_beats_to_rpy(beats, label_name='a')


# ---------------------------------------------------------------------------
#  Bug 1：`if` 里包 `menu`
# ---------------------------------------------------------------------------

class TestMenuInsideIf:
    """`if flag:` 里包一个 `menu:` —— 整块结构都要活下来。"""

    SCRIPT = (
        'label a:\n'
        '    if flag:\n'
        '        menu:\n'
        '            "左边":\n'
        '                jump left\n'
        '            "右边":\n'
        '                jump right\n'
        '    return\n'
    )

    def test_parser_emits_menu_and_options(self, tmp_path):
        """
        解析层就要认出 `menu` 拍 + 两张 `menu_option` 拍。

        修复前：只出 2 条 `narration`（"左边" / "右边" 降级成裸旁白），
        `menu_option` 一个都没有。
        """
        pr = _parse(self.SCRIPT, tmp_path)
        kinds = [s.kind for s in pr.statements]
        assert 'menu' in kinds, f'没有 menu 拍：{kinds}'
        assert kinds.count('menu_option') == 2, f'选项拍数不对：{kinds}'

    def test_condition_survives(self, tmp_path):
        """`if flag:` 的条件要落在 menu 拍（或选项拍）上，不能消失。"""
        pr = _parse(self.SCRIPT, tmp_path)
        conds = [s.condition for s in pr.statements if s.condition]
        assert any('flag' in c for c in conds), conds

    def test_jumps_survive(self, tmp_path):
        """两个选项体里的 `jump` 不许丢。"""
        pr = _parse(self.SCRIPT, tmp_path)
        targets = [s.payload.get('target') for s in pr.statements
                   if s.kind == 'jump']
        assert 'left' in targets and 'right' in targets, targets

    def test_render_keeps_menu_shape(self, tmp_path):
        """产物要还是 `if` + `menu` + 两个选项 + 两个 jump。"""
        pr = _parse(self.SCRIPT, tmp_path)
        out = _render(_beats(pr))
        assert 'menu:' in out, out
        assert '"左边":' in out and '"右边":' in out, out
        assert 'jump left' in out and 'jump right' in out, out
        # 选项要缩进在 menu 里、menu 要缩进在 if 里
        lines = out.splitlines()
        mi = next(i for i, ln in enumerate(lines) if ln.strip() == 'menu:')
        assert lines[mi].startswith(' ' * 8), out

    def test_statement_multiset_is_preserved(self, tmp_path):
        """真正的判据：解析 → 渲染 → 再解析，语句多重集相等。"""
        import collections
        from tests.test_roundtrip_invariants import _key
        pr = _parse(self.SCRIPT, tmp_path)
        out = _render(_beats(pr))
        src2 = tmp_path / 'a2.rpy'
        src2.write_text(out, encoding='utf-8')
        pr2 = ParseResultBuilder().build(RenPyParser().parse(src2))
        a = collections.Counter(_key(s) for s in pr.statements)
        b = collections.Counter(_key(s) for s in pr2.statements)
        assert a == b, f'丢失 {a - b}\n新增 {b - a}\n--- 产物 ---\n{out}'


# ---------------------------------------------------------------------------
#  Bug 2：分支内 `show` + 台词混合
# ---------------------------------------------------------------------------

class TestBranchWithShowAndSay:
    """`if` / `elif` 两个分支里各有 `show` + 台词 —— 两个分支都要活。"""

    SCRIPT = (
        'label a:\n'
        '    if place == "cafe":\n'
        '        show bgcafe with dissolve\n'
        '        "咖啡馆"\n'
        '    elif place == "library":\n'
        '        show bglibrary with dissolve\n'
        '        "图书馆"\n'
        '    return\n'
    )

    def test_both_branches_are_parsed(self, tmp_path):
        pr = _parse(self.SCRIPT, tmp_path)
        texts = [s.payload.get('text') for s in pr.statements
                 if s.kind in ('say', 'narration')]
        assert '咖啡馆' in texts, texts
        assert '图书馆' in texts, f'library 分支丢了：{texts}'
        shows = [s.payload.get('image') for s in pr.statements
                 if s.kind == 'show']
        assert 'bgcafe' in shows and 'bglibrary' in shows, shows

    def test_both_conditions_survive(self, tmp_path):
        pr = _parse(self.SCRIPT, tmp_path)
        conds = ' | '.join(s.condition for s in pr.statements)
        assert 'cafe' in conds and 'library' in conds, conds

    def test_no_stray_with_dissolve(self, tmp_path):
        """
        产物开头不许凭空多出一条**裸 `with dissolve`**。

        `with` 是 `show … with dissolve` 的一部分，被剥出来单独成行就
        成了"整拍转场"，语义变了。
        """
        pr = _parse(self.SCRIPT, tmp_path)
        out = _render(_beats(pr))
        bare = [ln for ln in out.splitlines()
                if ln.strip() == 'with dissolve']
        assert not bare, f'凭空多出裸 with：\n{out}'

    def test_statement_multiset_is_preserved(self, tmp_path):
        import collections
        from tests.test_roundtrip_invariants import _key
        pr = _parse(self.SCRIPT, tmp_path)
        out = _render(_beats(pr))
        src2 = tmp_path / 'a2.rpy'
        src2.write_text(out, encoding='utf-8')
        pr2 = ParseResultBuilder().build(RenPyParser().parse(src2))
        a = collections.Counter(_key(s) for s in pr.statements)
        b = collections.Counter(_key(s) for s in pr2.statements)
        assert a == b, f'丢失 {a - b}\n新增 {b - a}\n--- 产物 ---\n{out}'


class TestBeatsStayInSourceOrder:
    """
    **拍必须按源码顺序落盘**（2026-10-05，brutal test 的 F1 抓到）。

    真实形态（`brutal_test.py` 随机生成的 seed 30）：

        label g0:
            hide bg cafe        ← 只有舞台指令，会被攒进 pending
            menu:
                "B1": jump lbl1
            play music "…"
            return

    修复前：`menu` / `menu_option` 的拍是**直接 `beats.append`** 的，落盘前
    **没有先把 `pending` 里攒的 `hide bg cafe` 放出来**。后果有两个：

      1. 那一拍一直攒到章尾才落，序号排在最后 → 拍序列的 label 变成
         `g0, g1, g0`，而 `render_beats_to_rpy` 按"相邻同 label 分组"，
         产物里出现**两个 `label g0:`**（重定义）；
      2. `order_menu_bodies` 按行号归位块体时，那一拍的 `src_last`
         被 menu / menu_option 的行号**顶到了后面**，于是被判成"认不出
         属于哪个选项"，搬到了菜单组末尾。
    """

    SCRIPT = (
        'label g0:\n'
        '    hide bg cafe\n'
        '    menu:\n'
        '        "B1":\n'
        '            jump lbl1\n'
        '    play music "bgm/1.mp3"\n'
        '    return\n'
        'label g1:\n'
        '    q "台词"\n'
        '    return\n'
    )

    def test_label_order_is_monotonic(self, tmp_path):
        """
        拍序列里的 label **不许回头**（`g0, g1, g0` 就是产物出现两个
        `label g0:` 的直接原因）。
        """
        beats = _beats(_parse(self.SCRIPT, tmp_path))
        labels = [b.label for b in beats]
        seen, cur = set(), None
        for name in labels:
            if name != cur:
                assert name not in seen, (
                    f'label 顺序回头了：{labels}\n'
                    '这会让产物里出现两个同名 label（重定义）。')
                seen.add(name)
                cur = name

    def test_seq_is_monotonic(self, tmp_path):
        """`seq` 必须随序列单调递增 —— 它是"源码顺序"的载体。"""
        beats = _beats(_parse(self.SCRIPT, tmp_path))
        seqs = [b.seq for b in beats]
        assert seqs == sorted(seqs), f'seq 乱序：{seqs}'

    def test_render_has_one_label_per_name(self, tmp_path):
        """产物里每个 label 名**只出现一次**（重复定义是语法错误）。"""
        out = _render(_beats(_parse(self.SCRIPT, tmp_path)))
        names = [ln.split()[1].rstrip(':') for ln in out.splitlines()
                 if ln.startswith('label ')]
        assert len(names) == len(set(names)), (
            f'产物里有重复 label：{names}\n--- 产物 ---\n{out}')

    def test_hide_stays_before_the_menu(self, tmp_path):
        """
        `hide bg cafe` 必须排在 `menu:` **之前**（它的位置在源码里就是前面）。

        这是最实质的一条：修复前它被搬到菜单组末尾，只剩下一句
        "认不出归属"的警告，而它在原文里是**无条件**执行、且在菜单之前。
        """
        out = _render(_beats(_parse(self.SCRIPT, tmp_path)))
        lines = out.splitlines()
        hide_i = next(i for i, ln in enumerate(lines) if 'hide bg cafe' in ln)
        menu_i = next(i for i, ln in enumerate(lines) if ln.strip() == 'menu:')
        assert hide_i < menu_i, f'hide 跑到菜单后面了：\n{out}'
        assert lines[hide_i].startswith(' ' * 4), out

    def test_no_ambiguous_member_warning(self, tmp_path):
        """不属于菜单的拍**不该**被塞进选项体（那会带上"认不出归属"的警告）。"""
        out = _render(_beats(_parse(self.SCRIPT, tmp_path)))
        assert '# TODO' not in out, f'不该有 TODO 警告：\n{out}'


class TestConditionInsideMenuOption:
    """
    选项体里再嵌一层 `if/else`（2026-10-05，brutal test 的 F4 抓到）。

    `_menu_member` 是**运行时元数据、存不进 Excel**，所以表格往返之后
    "嵌套 if 的那几拍"只能靠**条件**认出来。认不出的后果不是少一句警告：

      · 渲染层不再写 `if deepest:` → **两个分支都被无条件执行**（静默改剧情）；
      · 还会多出一句 `# TODO: 这一拍不确定属于哪个选项`。

    另外两处排版缺陷同一轮修掉（都属"缩进算错"，产物是语法错误）：
      · `_render_unit_lines` 逐行贴 `indent`，把 `if` 与它的块体压成同一层；
      · `render_beat_to_rpy` 的条件分支用调用方的 `indent` 而不是"再加一层"。
    """

    SCRIPT = (
        'label a:\n'
        '    if outer:\n'
        '        menu:\n'
        '            "选项":\n'
        '                show bg_deep\n'
        '                if deepest:\n'
        '                    e "深层"\n'
        '                else:\n'
        '                    e "深层的 else"\n'
        '                jump deep_end\n'
        '    return\n'
    )

    def test_nested_if_is_emitted(self, tmp_path):
        """`if deepest` 必须以 `if` 行出现（条件可能被累积，所以按词找）。"""
        import re
        out = _render(_beats(_parse(self.SCRIPT, tmp_path)))
        assert re.search(r'^\s*if\b[^\n]*\bdeepest\b', out, re.M), out
        assert re.search(r'^\s*if\b[^\n]*\bnot\b[^\n]*\bdeepest\b', out, re.M), out

    def test_no_ambiguous_warning_for_nested_if(self, tmp_path):
        out = _render(_beats(_parse(self.SCRIPT, tmp_path)))
        assert '# TODO' not in out, f'嵌套 if 被误判成"认不出归属"：\n{out}'

    def test_block_body_is_indented_deeper_than_if(self, tmp_path):
        """
        `if` 的块体必须**更深** —— 齐平在 Ren'Py 里是语法错误。

        这是本轮真正的硬伤：产物虽然"有那一行"，但缩进错了根本跑不起来。
        """
        out = _render(_beats(_parse(self.SCRIPT, tmp_path)))
        lines = out.splitlines()
        i = next(k for k, ln in enumerate(lines)
                 if ln.strip().startswith('if') and 'deepest' in ln)
        body = lines[i + 1]
        assert body.strip().startswith('e "深层"'), out
        assert (len(body) - len(body.lstrip())
                > len(lines[i]) - len(lines[i].lstrip())), (
            f'`if` 的块体没有更深：\n{lines[i]!r}\n{body!r}')

    def test_multiset_preserved(self, tmp_path):
        import collections
        from tests.test_roundtrip_invariants import _key
        pr = _parse(self.SCRIPT, tmp_path)
        out = _render(_beats(pr))
        src2 = tmp_path / 'a2.rpy'
        src2.write_text(out, encoding='utf-8')
        pr2 = ParseResultBuilder().build(RenPyParser().parse(src2))
        a = collections.Counter(_key(s) for s in pr.statements)
        b = collections.Counter(_key(s) for s in pr2.statements)
        assert a == b, f'丢失 {a - b}\n新增 {b - a}\n--- 产物 ---\n{out}'


class TestConditionRefinement:
    """`_condition_is_refinement`：认出"同一分支的加强版"。"""

    def test_nested_and_is_a_refinement(self):
        from storygraph.story.beat_render import _condition_is_refinement as R
        assert R('((a) and (b)) and (c)', '(a) and (b)')
        assert R('((a) and (b)) and not ((c))', '(a) and (b)')

    def test_unrelated_condition_is_not(self):
        from storygraph.story.beat_render import _condition_is_refinement as R
        assert not R('not ((a))', '(a) and (b)')
        assert not R('x', 'outer')
        assert not R('(a) or (b)', '(a) and (b)')

    def test_same_condition_is_a_refinement(self):
        from storygraph.story.beat_render import _condition_is_refinement as R
        assert R('(a) and (b)', '(a) and (b)')
        assert R('a', 'a')

    def test_empty_base_is_not_a_refinement(self):
        """基准为空时**不认**（保守）—— 否则任何条件都会被当成块体。"""
        from storygraph.story.beat_render import _condition_is_refinement as R
        assert not R('anything', '')
