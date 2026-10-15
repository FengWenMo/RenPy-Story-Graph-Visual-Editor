"""
结构位（菜单选项体）门禁 —— 交付审查 A 档止血，见不变量 74。

**为什么要有它**：全量路径会把"选项体里有第二条语句 / 嵌套块"的菜单**压平** ——

    原文                          全量产物
    menu:                         menu:
        "选项":          →            "选项":
            if flag:                      pass          ← 选项体被掏空
                e "选了"         if flag:               ← 提到 label 级：
            e "收尾"                 e "选了"             **选没选都会执行**
                                 if True:
                                     e "收尾"

产物语法合法、能跑，`validate` 一句话都不说（2026-10-05 实测 `exit=0`）。
本仓库最贵的一类缺陷就是"静默改剧情"，所以先让它**现形**。

**为什么是 warn 不是 block**（这条取舍别再改回去）：
"结构变了"有两种可能 —— ① 被压平（缺陷）；② 人**有意**在表里/卡片里
给某个选项加了内容（正常操作）。只比"源 vs 产物"分不出这两者，
而"假阳性比漏报更贵"（本项目既有立场）—— 拦错一次就会让人学会忽略这条警告。
等结构位进了表（方案 B，`docs/archive/嵌套与行尾注释_修复方案_2026-10-05.md`），
判据才能变成"产物 vs 表里说的结构"，那时才可以升成 block。

**为什么只比菜单、不比全部嵌套**：纯 `if` 嵌套走"条件合取"近似
（`if a:` 套 `if b:` → `if (a) and (b):`）是**设计内**的，语义大体等价，
真实项目里有 109 处 —— 报它就是刷屏，等于没报。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from storygraph.story.generation_guard import (menu_structure, structure_loss,
                                               write_generation)
from storygraph.pipeline.compile import CompileResult, write_compiled

REAL_PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')

#: 选项体只有一条语句（正常形状）
FLAT_MENU = '''label a:
    e "开始"
    menu:
        "选项":
            e "选了"
    e "结束"
'''

#: 选项体里有嵌套块（全量路径会压平的那种）
RICH_MENU = '''label a:
    e "开始"
    menu:
        "选项":
            if flag:
                e "选了且 flag"
            e "选项内收尾"
    e "结束"
'''

#: 实测的全量坏形状（`tools/probe_nesting_min.py`）：
#: 选项体变 `pass`，内容提到菜单外面 —— 选没选都会执行
FLATTENED = '''label a:
    e "开始"
    menu:
        "选项":
            pass
    if flag:
        e "选了且 flag"
    if True:
        e "选项内收尾"
    e "结束"
'''

#: 纯 `if` 嵌套（设计内的合取近似）—— **不许报**
IF_NESTED = '''label a:
    if a1:
        if a2:
            e "深层"
        if a3:
            e "另一支"
    e "结束"
'''

IF_FLATTENED = '''label a:
    if (a1) and (a2):
        e "深层"
    if (a1) and (a3):
        e "另一支"
    e "结束"
'''


class TestFingerprint:
    def test_flat_menu_body_is_one_statement(self):
        assert menu_structure(FLAT_MENU) == {'a': [[(1, 0)]]}

    def test_rich_menu_body_counts_statements_and_blocks(self):
        # 选项体 = `if flag:`（1 块）+ 里面的 `e`（1 句）+ 外面的 `e`（1 句）
        assert menu_structure(RICH_MENU) == {'a': [[(3, 1)]]}

    def test_pure_if_nesting_has_no_menu_fingerprint(self):
        """反面对照：没有菜单就是空指纹 —— 这是"不许误报"的地基。"""
        assert menu_structure(IF_NESTED) == {'a': []}

    def test_self_compare_is_empty(self):
        assert structure_loss(RICH_MENU, RICH_MENU) == []

    def test_flattened_body_is_detected(self):
        lost = structure_loss(RICH_MENU, FLATTENED)
        assert len(lost) == 1, lost
        label, want, got = lost[0]
        assert label == 'a'
        assert want == [[(3, 1)]] and got == [[(1, 0)]], (want, got)

    def test_pure_if_flattening_is_not_reported(self):
        """
        **合取近似不是缺陷**：`if a:` 套 `if b:` → `if (a) and (b):`。

        真实项目 109 处这种嵌套。这条用例是"不刷屏"的守卫 ——
        谁把判据改成"比全部嵌套深度"，它就会红。
        """
        assert structure_loss(IF_NESTED, IF_FLATTENED) == []

    def test_missing_label_is_someone_elses_business(self):
        """产物里没这个 label 是别的门禁管的 —— 这里不重复报（免刷屏）。"""
        assert structure_loss(RICH_MENU, 'label z:\n    e "x"\n') == []


class TestWriteGenerationGate:
    """门禁层：warn 出来，但**照写**（不拦）。"""

    def test_flattened_structure_warns_but_still_writes(self, tmp_path):
        out = tmp_path / 'out.rpy'
        art = write_generation(FLATTENED, out, source_text=RICH_MENU,
                               source_path='ch.rpy')
        codes = {i.code for i in art.issues}
        assert 'STRUCTURE_LOST' in codes, [i.format() for i in art.issues]
        assert art.written is True, '结构对不上是 warn，不该拦写盘'
        assert out.exists()
        issue = next(i for i in art.issues if i.code == 'STRUCTURE_LOST')
        assert not issue.is_blocking
        assert '`a`' in issue.message and '选项' in issue.message

    def test_faithful_product_is_silent(self, tmp_path):
        out = tmp_path / 'ok.rpy'
        art = write_generation(RICH_MENU, out, source_text=RICH_MENU,
                               source_path='ch.rpy')
        assert 'STRUCTURE_LOST' not in {i.code for i in art.issues}
        assert art.written is True

    def test_no_source_no_judgement(self, tmp_path):
        """没给源就无从比较 —— 不许凭空报（CLI 全量无 `--source` 那条路）。"""
        art = write_generation(FLATTENED, tmp_path / 'n.rpy')
        assert 'STRUCTURE_LOST' not in {i.code for i in art.issues}


class TestCompilePlumbing:
    """落盘口：警告要进 `CompileResult.guard_warnings` → 进 summary（人能看见）。"""

    def test_warning_reaches_the_cli_summary(self, tmp_path):
        out = tmp_path / 'p.rpy'
        result = CompileResult(rows=[], labels=[], rpy_text=FLATTENED)
        write_compiled(result, out, source_path=None)
        # 直接给结果挂上"源"，走同一条落盘口（不依赖补丁能不能对上）
        result2 = CompileResult(rows=[], labels=[], rpy_text=FLATTENED)
        write_compiled(result2, tmp_path / 'p2.rpy',
                       source_path=tmp_path / 'src.rpy')
        (tmp_path / 'src.rpy').write_text(RICH_MENU, encoding='utf-8')
        result3 = CompileResult(rows=[], labels=[], rpy_text=FLATTENED)
        write_compiled(result3, tmp_path / 'p3.rpy',
                       source_path=tmp_path / 'src.rpy')
        codes = {i.code for i in result3.guard_warnings}
        assert 'STRUCTURE_LOST' in codes, [i.format()
                                           for i in result3.guard_warnings]
        assert '菜单结构对不上' in result3.summary(), result3.summary()


@pytest.mark.skipif(not REAL_PROJECT.exists(), reason='真实项目不在本机')
class TestRealProject:
    """
    真实项目：**该报的报、不该报的一句都不许多**。

    先说清楚数量（`tools/probe_structure_risk.py` 实测）：7 个菜单 / 24 个选项里，
    复杂选项体只有 **3 处**（`script_pomo.rpy` 2、`pomodorro.rpy` 1）。
    """

    def test_finds_the_known_spots_and_stays_quiet_elsewhere(self):
        from storygraph.story.beat_build import beats_from_parse_result
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.story.beat_render import render_beats_to_rpy

        files = sorted(f for f in (REAL_PROJECT / 'game').rglob('*.rpy')
                       if not f.name.endswith('.generated.rpy'))
        flagged = []
        for f in files:
            src = f.read_text(encoding='utf-8', errors='replace')
            pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
            prod = render_beats_to_rpy(beats_from_parse_result(pr))
            if structure_loss(src, prod):
                flagged.append(f.name)
        # 该报的：那两个文件必须被点到
        assert any('pomo' in n for n in flagged), (
            f'那两处已知的复杂选项体没被点到：{flagged}')
        # 不该报的：绝大多数文件必须是安静的（真实项目 40 个文件）
        assert len(flagged) <= 4, (
            f'报太多了（{len(flagged)} 个文件）—— 很可能判据退化成了"比全部嵌套"：'
            f'{flagged}')
