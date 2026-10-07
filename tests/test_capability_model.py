"""
A：工具能力元模型（Capability Meta-Model）
==========================================

用户 2026-10-07 的要求，原话：

> 你的文档把保真性、可编辑性、可教性当作三个独立属性来描述，但它们实际上是
> 一个单一"工具能力模型"的三个投影。…… 这个元模型不改变你的四层分级，
> 但它给每一层增加了生命周期——语句可以在等级之间移动，移动的路径和条件
> 是可定义、可测试、可报告的。

所以这个文件要钉的**不是**"分级有几个"，而是三条更要紧的东西：

  ① **判定顺序**：`modeled > declared > known_head > 都不满足`。
     顺序错了就会出现"项目声明一下就把 `show` 抢走"这类事故；
  ② **两条硬不变量**（这才是元模型存在的意义）：
     · 降级不改字节；· 精化不改字节。
     ⚠ 这两条**必须同时断言"等级真的动了"**，否则等式两边是同一条路，
     测试是空转的（本仓库被"判据太松"坑过好几次）；
  ③ **覆盖率**（CAP 视角）与**逃逸舱口**（每一级都要告诉人怎么升级）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.core import capability as cap
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.pipeline.compile import compile_beats
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel
from storygraph.story.statement_registry import (RegisteredStatement,
                                                 StatementRegistry,
                                                 capability_of,
                                                 clear_cache,
                                                 coverage_of_statements)

SCRIPT = (
    'label a:\n'
    '    "第一句"\n'
    '    mycue "闪电" intensity=3:\n'
    '        target = ling\n'
    '        hold 0.5\n'
    '    while n < 3:\n'
    '        $ n += 1\n'
    '    "第二句"\n'
    '    return\n'
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()
    yield
    clear_cache()


def _project(tmp_path, manifest):
    root = tmp_path / 'proj'
    game = root / 'game'
    game.mkdir(parents=True, exist_ok=True)
    f = game / 'ch.rpy'
    f.write_text(SCRIPT, encoding='utf-8')
    if manifest is not None:
        (root / 'storygraph.rules.json').write_text(
            json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    return root, f


def _render(tmp_path, path):
    pr, beats = (lambda f: (IncrementalRenPyAnalyzer().analyze(
        files=[f], graph=None)[0], None))(path)
    beats = beats_from_parse_result(pr)
    return pr, beats, compile_beats(beats).rpy_text


def _roundtrip(tmp_path, path):
    """`.rpy` → 拍 → 表 → 表 → `.rpy`（纯全量那条路，判"字节"就用它）。"""
    pr, beats = IncrementalRenPyAnalyzer().analyze(files=[path],
                                                  graph=None)
    beats = beats_from_parse_result(pr)
    x = tmp_path / 'o.xlsx'
    export_beats_to_excel(beats, x)
    return compile_beats(import_beats_from_excel(x)).rpy_text


# ---------------------------------------------------------------------------
#  ① 判定顺序（顺序即优先级）
# ---------------------------------------------------------------------------

class TestStateJudgement:

    def test_order_is_modeled_then_declared_then_known(self):
        # 内置建模最优先：**项目声明也不能把 `show` 抢走**
        assert cap.state_of(modeled=True, declared=True, projected=True,
                            known_head=True) == cap.T0
        # 声明优先于"只是认识形状"：`while` 被声明后应当升到 T1
        assert cap.state_of(declared=True, known_head=True) == cap.T1B
        assert cap.state_of(declared=True, projected=True,
                            known_head=True) == cap.T1A
        # 只是认识形状 → T2
        assert cap.state_of(known_head=True) == cap.T2
        # 什么都不知道 → T3
        assert cap.state_of() == cap.T3

    def test_projected_is_what_separates_t1a_from_t1b(self):
        """T1a / T1b 的唯一区别就是"有没有投影"（columns / label）。"""
        assert cap.state_of(declared=True, projected=False) == cap.T1B
        assert cap.state_of(declared=True, projected=True) == cap.T1A

    def test_provenance_records_who_said_so(self):
        assert cap.provenance_of(modeled=True) == cap.PROV_BUILTIN
        assert cap.provenance_of(declared_source='declared') == \
            cap.PROV_DECLARED
        assert cap.provenance_of(declared_source='host-registry') == \
            cap.PROV_REGISTRY
        assert cap.provenance_of(known_head=True) == cap.PROV_SHAPE
        assert cap.provenance_of() == cap.PROV_UNKNOWN

    def test_editable_verbatim_and_readonly_are_consistent(self):
        """
        三份集合**不是划分关系**：`T1b` **同时**是"可编辑"与"逐字透传"。

        ⚠ 这条判据原本写成了"每个等级只属于一边"，是**我错了**
        （2026-10-07，J3 先怀疑判据）：`T1b` 的定义恰恰是"项目声明过
        （工具知道它是什么，所以能替你改、能定位），但没给参数形状
        （所以只能整块原文改、逐字写回）"。两件事同时成立。
        真正该划的界是**只读**（`T2`/`T3`：工具既不编辑也不重排）。
        """
        # 每个等级至少要落在"可编辑"或"逐字"之一（不许两不管）
        assert set(cap.LEVELS) == cap.EDITABLE_LEVELS | cap.VERBATIM_LEVELS
        # T1b 的"两栖"是定义的一部分，不是重复计数
        assert cap.T1B in cap.EDITABLE_LEVELS
        assert cap.T1B in cap.VERBATIM_LEVELS
        # 只读 = 逐字 − 可编辑，而且正好是 T2 / T3
        assert cap.READONLY_LEVELS == {cap.T2, cap.T3}
        # 可编辑与只读**才是**划分（这两句合起来正好是全部）
        assert cap.EDITABLE_LEVELS | cap.READONLY_LEVELS == set(cap.LEVELS)
        assert not (cap.EDITABLE_LEVELS & cap.READONLY_LEVELS)


# ---------------------------------------------------------------------------
#  ② 转换规则 + 两条硬不变量
# ---------------------------------------------------------------------------

class TestTransitions:

    def test_refine_and_degrade_are_labelled_correctly(self):
        for t in cap.TRANSITIONS:
            assert t.kind in ('refine', 'degrade'), t

    def test_nothing_points_back_into_t0(self):
        """
        **T0 不降级**：只有"工具新增内置分支"才能进 T0，而且那不是
        等级移动（那是改代码）。声明过的东西不许把自己变成原生语句。
        """
        into_t0 = cap.transitions_to(cap.T0)
        assert [t for t in into_t0 if t.kind == 'degrade'] == []
        # 只有『工具自己把它接得更全』这一种升级能进 T0：
        # 从 T2（只认识形状）或 T0B（头行结构化、块体逐字）。
        assert all(t.frm in (cap.T2, cap.T0B) for t in into_t0), into_t0

    def test_every_level_has_an_escape(self):
        """每个等级都要能回答"我怎么升级" —— 没答案的等级等于死路。"""
        for lv in cap.LEVELS:
            assert cap.escape_for(lv).strip(), f'{lv} 没有逃逸舱口'

    def test_every_level_is_reachable_in_the_table(self):
        """等级表不许有"够不着"的等级（写进文档却没人能到 = 空话）。"""
        reachable = {cap.T0}
        reachable |= {t.frm for t in cap.TRANSITIONS}
        reachable |= {t.to for t in cap.TRANSITIONS}
        assert set(cap.LEVELS) <= reachable


class TestBytePreservationInvariants:
    """
    **元模型存在的全部意义**：等级只决定"工具能不能替你编辑"，
    不决定"内容长什么样"。

    ⚠ 每条都要**同时**断言"等级真的动了" —— 只比字节的话，
    两次走同一条路也会相等，测试就是空转的。
    """

    def _levels(self, tmp_path, path):
        pr = IncrementalRenPyAnalyzer().analyze(files=[path],
                                               graph=None)[0]
        reg = StatementRegistry.for_project(path.parent.parent)
        cov = coverage_of_statements(pr.statements, reg)
        return pr, cov

    def test_refinement_is_byte_preserving(self, tmp_path):
        """T3 → T1：**教它**之后，产物必须与教之前逐字相同。"""
        root, path = _project(tmp_path, None)
        before = _roundtrip(tmp_path, path)
        _pr, cov_before = self._levels(tmp_path, path)
        assert cov_before.count_of(cap.T3) >= 1, cov_before.counts

        (root / 'storygraph.rules.json').write_text(
            json.dumps({'statements': [{'name': 'mycue', 'block': True}]},
                       ensure_ascii=False), encoding='utf-8')
        clear_cache()
        after = _roundtrip(tmp_path, path)
        _pr2, cov_after = self._levels(tmp_path, path)

        assert cov_after.count_of(cap.T1B) >= 1, '等级没有真的升上去'
        assert cov_after.count_of(cap.T3) < cov_before.count_of(cap.T3), (
            'T3 的条数没有减少 —— 说明这次"升级"没生效，等式是空转的')
        assert before == after, (
            '**精化改了字节** —— 这是不可接受的\n'
            f'--- 教之前 ---\n{before}\n--- 教之后 ---\n{after}')

    def test_degradation_is_byte_preserving(self, tmp_path):
        """T1 → T3：**把声明删掉**之后，产物必须与之前逐字相同。"""
        root, path = _project(tmp_path, {'statements': [{'name': 'mycue',
                                                         'block': True}]})
        declared = _roundtrip(tmp_path, path)
        _pr, cov_declared = self._levels(tmp_path, path)
        assert cov_declared.count_of(cap.T1B) >= 1, cov_declared.counts

        (root / 'storygraph.rules.json').unlink()
        clear_cache()
        degraded = _roundtrip(tmp_path, path)
        _pr2, cov_degraded = self._levels(tmp_path, path)

        assert cov_degraded.count_of(cap.T3) >= 1, '等级没有真的降下去'
        assert declared == degraded, (
            '**降级改了字节** —— 等级不许影响内容\n'
            f'--- 有声明 ---\n{declared}\n--- 删声明 ---\n{degraded}')


# ---------------------------------------------------------------------------
#  ③ 覆盖率与逃逸舱口
# ---------------------------------------------------------------------------

class TestCoverage:

    def _coverage(self, tmp_path, manifest):
        root, path = _project(tmp_path, manifest)
        pr = IncrementalRenPyAnalyzer().analyze(files=[path],
                                               graph=None)[0]
        reg = StatementRegistry.for_project(root)
        return coverage_of_statements(pr.statements, reg)

    def test_counts_and_ratio(self, tmp_path):
        cov = self._coverage(tmp_path, {'statements': ['mycue']})
        assert cov.total > 0
        # `mycue` 被声明 → T1b；`while` 现在**块体也结构化了**
        # （2026-10-07 第三轮：作用域）→ T0。
        # ⚠ `T0b` 只留给"整块逐字躺在 Opaque 里"的老形态
        # （`test_legacy_verbatim_loop_is_still_t0b` 钉着那一档）。
        assert cov.count_of(cap.T1B) == 1, cov.counts
        assert cov.count_of(cap.T0B) == 0, cov.counts
        assert cov.count_of(cap.T2) == 0, cov.counts
        assert cov.count_of(cap.T0) > 0, cov.counts
        # 这份夹具里每一级都是可编辑的（T0 / T1b），所以是 1.0；
        # "比例真的会小于 1" 由 `test_declaring_everything_raises_the_ratio`
        # 那条覆盖（那里有未声明的自定义块 → T3）。
        assert 0.0 < cov.ratio <= 1.0
        # 可编辑 + **只读** = 全部（不是 + verbatim：`T1b` 两边都算）
        assert cov.editable + cov.readonly == cov.total, cov.to_dict()
        assert cov.readonly == cov.count_of(cap.T2) + cov.count_of(cap.T3)

    def test_declaring_everything_raises_the_ratio(self, tmp_path):
        """覆盖率**确实是个度量**：多声明一条，比例就该上去。"""
        low = self._coverage(tmp_path / 'a', None)
        high = self._coverage(tmp_path / 'b', {'statements': ['mycue']})
        assert high.ratio > low.ratio, (low.to_dict(), high.to_dict())

    def test_report_leads_with_the_number_and_tells_you_what_to_do(
            self, tmp_path):
        cov = self._coverage(tmp_path, None)
        text = cap.format_capability_report(cov)
        assert text.splitlines()[0].startswith('工具可编辑覆盖率')
        assert 'mycue' in text, text
        assert 'rules.json' in text, 'T3 的升级入口要说出来'

    def test_empty_coverage_is_not_a_zero(self):
        """没有语句时覆盖率是 1.0（不是 0）—— 别把"没事"报成"全废"。"""
        assert cap.Coverage().ratio == 1.0
        assert '没有需要' in cap.format_capability_report(cap.Coverage())


# ---------------------------------------------------------------------------
#  ④ capability_of：把事实译成等级（唯一一处）
# ---------------------------------------------------------------------------

class TestCapabilityOf:

    def test_modeled_statement_is_t0(self):
        c = capability_of('', None, modeled=True)
        assert c.level == cap.T0 and c.provenance == cap.PROV_BUILTIN
        assert c.editable and not c.verbatim

    def test_known_flow_block_is_t2_with_our_todo(self):
        c = capability_of('while', None, raw_kind='known_block')
        assert c.level == cap.T2 and c.provenance == cap.PROV_SHAPE
        assert '工具作者' in c.escape or '标准流程块' in c.escape

    def test_declared_without_projection_is_t1b(self):
        reg = StatementRegistry(declared=[
            RegisteredStatement(name='mycue', source='declared')])
        c = capability_of('mycue', reg, raw_kind='unknown_block')
        assert c.level == cap.T1B and c.provenance == cap.PROV_DECLARED
        assert c.editable, 'T1b 是可编辑的（整块原文）'

    def test_declared_with_projection_is_t1a(self):
        reg = StatementRegistry(declared=[
            RegisteredStatement(name='mycue', source='declared',
                                columns=[{'name': 'intensity'}])])
        c = capability_of('mycue', reg, raw_kind='unknown_block')
        assert c.level == cap.T1A

    def test_projection_can_come_from_label_too(self):
        """`label`（人话说明）也算投影 —— 它让这一行在表格里可读。"""
        reg = StatementRegistry(declared=[
            RegisteredStatement(name='mycue', source='declared',
                                label='闪电')])
        c = capability_of('mycue', reg, raw_kind='unknown_block')
        assert c.level == cap.T1A

    def test_unknown_is_t3_with_the_escape_written_out(self):
        c = capability_of('totally_custom', None, raw_kind='unknown_block')
        assert c.level == cap.T3 and c.provenance == cap.PROV_UNKNOWN
        assert 'rules.json' in c.escape
