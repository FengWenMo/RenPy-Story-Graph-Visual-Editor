"""
tests/test_analysis.py
======================
静态分析（区间抽象）测试。

覆盖：
  · ValueRange 的 join / intersect / 四则运算 / 空集（bottom）语义
  · VariableState merge
  · apply_operation：赋值、增量、不可解析语句的保守降级
  · condition_possible：条件在当前区间下是否可达
  · apply_condition：条件成立后的区间收窄，矛盾时返回 None
  · analyze_reachability：不可达节点与"下落风险"告警
"""

import math

import pytest

from models import Graph
from analysis import (
    ValueRange, VariableState, IntervalEvaluator,
    apply_operation, condition_possible, apply_condition,
    analyze_reachability, visualize_analysis_results,
)


def _state(**ranges):
    st = VariableState(list(ranges.keys()))
    for k, v in ranges.items():
        st.ranges[k] = ValueRange(v[0], v[1])
    return st


# ------------------------------------------------------------------
#  ValueRange
# ------------------------------------------------------------------

class TestValueRange:
    def test_is_const(self):
        assert ValueRange(3, 3).is_const()
        assert not ValueRange(3, 4).is_const()

    def test_is_top(self):
        assert ValueRange(-math.inf, math.inf).is_top()
        assert not ValueRange(0, math.inf).is_top()

    def test_is_bottom(self):
        assert ValueRange(1, -1).is_bottom()
        assert not ValueRange(0, 0).is_bottom()

    def test_join(self):
        r = ValueRange(0, 1).join(ValueRange(5, 9))
        assert (r.lo, r.hi) == (0, 9)

    def test_join_respects_bottom(self):
        bottom = ValueRange(1, -1)
        assert ValueRange(2, 3).join(bottom) == ValueRange(2, 3)
        assert bottom.join(ValueRange(2, 3)) == ValueRange(2, 3)

    def test_intersect(self):
        r = ValueRange(0, 5).intersect(ValueRange(3, 9))
        assert (r.lo, r.hi) == (3, 5)

    def test_intersect_disjoint_is_bottom(self):
        assert ValueRange(0, 2).intersect(ValueRange(5, 9)).is_bottom()

    def test_add_is_scalar_shift(self):
        r = ValueRange(1, 2).add(10)
        assert (r.lo, r.hi) == (11, 12)

    def test_add_preserves_special_ranges(self):
        top = ValueRange(-math.inf, math.inf).add(3)
        assert top.is_top()
        bottom = ValueRange(1, -1).add(3)
        assert bottom.is_bottom()

    def test_mul(self):
        r = ValueRange(2, 3).mul(4)
        assert (r.lo, r.hi) == (8, 12)

    def test_mul_negative(self):
        r = ValueRange(2, 3).mul(-1)
        assert (r.lo, r.hi) == (-3, -2)

    def test_mul_across_zero(self):
        r = ValueRange(-2, 3).mul(-1)
        assert (r.lo, r.hi) == (-3, 2)

    def test_mul_by_zero(self):
        assert ValueRange(-2, 3).mul(0) == ValueRange(0, 0)

    def test_mul_with_infinite_bound_goes_top(self):
        r = ValueRange(0, math.inf).mul(2)
        assert r.is_top()

    def test_copy_is_independent(self):
        r = ValueRange(0, 1)
        c = r.copy()
        c.lo = 99
        assert r.lo == 0

    def test_equality(self):
        assert ValueRange(1, 2) == ValueRange(1, 2)
        assert ValueRange(1, 2) != ValueRange(1, 3)


# ------------------------------------------------------------------
#  VariableState
# ------------------------------------------------------------------

class TestVariableState:
    def test_copy_is_deep(self):
        st = _state(x=(0, 1))
        c = st.copy()
        c.ranges['x'] = ValueRange(9, 9)
        assert st.ranges['x'].lo == 0

    def test_is_bottom_any_empty_range(self):
        """
        语义：任一变量取值集合为空 → 该路径不可行 → 整个状态是 bottom。

        这是正确性要求而非口味：若只在"所有变量都空"时才算 bottom，
        带有一个已知变量的分支会被错误判定为可达。
        """
        st = _state(x=(0, 1), y=(1, -1))
        assert st.is_bottom()

    def test_merge_unions_ranges(self):
        a = _state(x=(0, 1))
        b = _state(x=(5, 6))
        merged = a.merge(b)
        assert (merged.ranges['x'].lo, merged.ranges['x'].hi) == (0, 6)

    def test_merge_with_bottom(self):
        a = VariableState(['x'])
        a.ranges['x'] = ValueRange(1, -1)
        b = _state(x=(0, 2))
        merged = a.merge(b)
        assert (merged.ranges['x'].lo, merged.ranges['x'].hi) == (0, 2)

    def test_set_top(self):
        st = _state(x=(0, 1))
        st.set_top('x')
        assert st.ranges['x'].is_top()

    def test_str_renders_top_and_bottom(self):
        st = _state(x=(0, 1))
        st.ranges['y'] = ValueRange(1, -1)
        st.ranges['z'] = ValueRange(-math.inf, math.inf)
        text = str(st)
        assert 'x' in text
        assert '⊥' in text or '[1, -1]' in text


# ------------------------------------------------------------------
#  apply_operation
# ------------------------------------------------------------------

class TestApplyOperation:
    def test_assign_constant(self):
        st = apply_operation(_state(x=(0, 0)), 'x = 5')
        assert (st.ranges['x'].lo, st.ranges['x'].hi) == (5, 5)

    def test_assign_from_other_var(self):
        st = apply_operation(_state(x=(0, 0), y=(2, 4)), 'x = y')
        assert (st.ranges['x'].lo, st.ranges['x'].hi) == (2, 4)

    def test_augassign_add(self):
        st = apply_operation(_state(x=(1, 2)), 'x += 10')
        assert (st.ranges['x'].lo, st.ranges['x'].hi) == (11, 12)

    def test_augassign_add_range(self):
        st = apply_operation(_state(x=(0, 0), y=(1, 3)), 'x += y')
        assert (st.ranges['x'].lo, st.ranges['x'].hi) == (1, 3)

    def test_augassign_sub(self):
        st = apply_operation(_state(x=(5, 10)), 'x -= 3')
        assert (st.ranges['x'].lo, st.ranges['x'].hi) == (2, 7)

    def test_augassign_unknown_op_goes_top(self):
        st = apply_operation(_state(x=(1, 2)), 'x *= 3')
        assert st.ranges['x'].is_top()

    def test_top_stays_top_on_increment(self):
        st = _state(x=(-math.inf, math.inf))
        out = apply_operation(st, 'x += 1')
        assert out.ranges['x'].is_top()

    def test_expression_with_top_operand(self):
        st = _state(x=(0, 0), y=(-math.inf, math.inf))
        out = apply_operation(st, 'x = y + 1')
        assert out.ranges['x'].is_top()

    def test_unparsable_statement_degrades_variables_to_top(self):
        """
        无法解析的语句必须让**涉及的变量**退化为 TOP。

        保守性要求：如果语法错误导致什么都没做，后续会拿着过期的精确
        区间去断言"某分支不可达"，给出错误结论。
        """
        st = apply_operation(_state(x=(1, 2)), 'x = (((')
        assert st.ranges['x'].is_top()

    def test_unknown_variable_not_created(self):
        st = apply_operation(_state(x=(1, 2)), 'zzz = 1')
        assert 'zzz' not in st.ranges

    def test_non_assign_statement_conservative(self):
        st = apply_operation(_state(x=(1, 2)), 'foo(x)')
        assert st.ranges['x'].is_top()

    def test_dollar_prefix_stripped(self):
        st = apply_operation(_state(x=(0, 0)), '$ x = 7')
        assert st.ranges['x'].lo == 7

    def test_min_max(self):
        st = apply_operation(_state(x=(0, 0), y=(1, 5), z=(3, 9)),
                             'x = max(y, z)')
        assert (st.ranges['x'].lo, st.ranges['x'].hi) == (3, 9)

    def test_division_goes_top(self):
        st = apply_operation(_state(x=(1, 2)), 'x = x / 2')
        assert st.ranges['x'].is_top()

    def test_input_state_not_mutated(self):
        st = _state(x=(0, 0))
        apply_operation(st, 'x = 5')
        assert st.ranges['x'].lo == 0


# ------------------------------------------------------------------
#  condition_possible
# ------------------------------------------------------------------

class TestConditionPossible:
    def test_empty_condition(self):
        assert condition_possible(_state(x=(0, 0)), '') is True

    def test_unsatisfiable_equality(self):
        assert condition_possible(_state(x=(5, 5)), 'x == 3') is False

    def test_satisfiable_equality(self):
        assert condition_possible(_state(x=(0, 10)), 'x == 3') is True

    def test_gt_boundary(self):
        assert condition_possible(_state(x=(0, 5)), 'x > 5') is False
        assert condition_possible(_state(x=(0, 5)), 'x > 4') is True

    def test_and_short_circuit(self):
        st = _state(x=(5, 5))
        assert condition_possible(st, 'x == 5 and x > 100') is False

    def test_or(self):
        st = _state(x=(5, 5))
        assert condition_possible(st, 'x == 1 or x == 5') is True

    def test_top_is_always_possible(self):
        st = _state(x=(-math.inf, math.inf))
        assert condition_possible(st, 'x == 999999') is True

    def test_syntax_error_returns_possible(self):
        assert condition_possible(_state(x=(1, 1)), 'x ====') is True

    def test_constant_false(self):
        assert condition_possible(_state(x=(1, 1)), 'False') is False


# ------------------------------------------------------------------
#  apply_condition
# ------------------------------------------------------------------

class TestApplyCondition:
    def test_empty_returns_copy(self):
        st = _state(x=(0, 1))
        out = apply_condition(st, '')
        assert out != st
        assert (out.ranges['x'].lo, out.ranges['x'].hi) == (0, 1)

    def test_eq_narrows(self):
        out = apply_condition(_state(x=(0, 10)), 'x == 4')
        assert (out.ranges['x'].lo, out.ranges['x'].hi) == (4, 4)

    def test_contradiction_returns_none(self):
        assert apply_condition(_state(x=(5, 5)), 'x == 3') is None

    def test_gt_narrows_lower_bound(self):
        out = apply_condition(_state(x=(0, 10)), 'x > 3')
        assert out.ranges['x'].lo >= 3

    def test_syntax_error_returns_copy(self):
        st = _state(x=(0, 10))
        out = apply_condition(st, 'x =====')
        assert (out.ranges['x'].lo, out.ranges['x'].hi) == (0, 10)

    def test_and_both_applied(self):
        out = apply_condition(_state(x=(0, 100)), 'x > 10 and x < 20')
        assert 10 <= out.ranges['x'].lo <= out.ranges['x'].hi <= 20


# ------------------------------------------------------------------
#  analyze_reachability
# ------------------------------------------------------------------

class _GraphFactory:
    @staticmethod
    def build():
        g = Graph()
        return g


class TestAnalyzeReachability:
    def test_unknown_start_node(self):
        g = Graph()
        states, warnings = analyze_reachability(g, 999)
        assert states == {} and warnings

    def test_reachable_chain(self):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(a, b)
        g.add_edge(b, c)
        states, warnings = analyze_reachability(g, a)
        assert not any('不可达' in w for w in warnings)

    def test_unreachable_node_reported(self):
        g = Graph()
        a = g.add_node('a', 0, 0)
        orphan = g.add_node('orphan', 0, 0)
        states, warnings = analyze_reachability(g, a)
        assert any('orphan' in w for w in warnings)

    def test_impossible_branch_not_propagated(self):
        g = Graph()
        a = g.add_node('a', 0, 0, operations=['x = 5'])
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(a, b, condition='x == 5')
        g.add_edge(a, c, condition='x == 99')
        states, warnings = analyze_reachability(g, a)
        assert states[c].is_bottom()
        assert not states[b].is_bottom()

    def test_fallthrough_warning(self):
        g = Graph()
        a = g.add_node('a', 0, 0, operations=['x = 5'])
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b, condition='x == 99')
        states, warnings = analyze_reachability(g, a)
        assert any('下落风险' in w for w in warnings)

    def test_start_state_is_initialized_to_zero(self):
        g = Graph()
        a = g.add_node('a', 0, 0, operations=['x = 1'])
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b)
        states, _ = analyze_reachability(g, a)
        assert (states[a].ranges['x'].lo, states[a].ranges['x'].hi) == (0, 0)

    def test_propagates_operations(self):
        g = Graph()
        a = g.add_node('a', 0, 0, operations=['x = 7'])
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b)
        states, _ = analyze_reachability(g, a)
        assert states[b].ranges['x'].lo == 7

    def test_loop_terminates(self):
        """
        循环里的自增会让区间一路发散，必须靠加宽（widening）收敛，
        否则 UI 分析一次就卡死。
        """
        g = Graph()
        a = g.add_node('a', 0, 0, operations=['x += 1'])
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b)
        g.add_edge(b, a)
        states, warnings = analyze_reachability(g, a)
        assert states[a].ranges['x'].hi != -math.inf

    def test_loop_widens_to_infinity(self):
        """加宽后上界应被推到 +inf，而不是停在某个具体数字上。"""
        g = Graph()
        a = g.add_node('a', 0, 0, operations=['x += 1'])
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b)
        g.add_edge(b, a)
        states, _ = analyze_reachability(g, a)
        assert states[a].ranges['x'].hi == math.inf

    def test_mutual_recursion_with_condition_terminates(self):
        g = Graph()
        a = g.add_node('a', 0, 0, operations=['x += 2'])
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b, condition='x < 100')
        g.add_edge(b, a, condition='x > 0')
        g.add_edge(b, b, condition='x == 50')
        states, warnings = analyze_reachability(g, a)
        assert a in states and b in states

    def test_converging_loop_stays_precise(self):
        """加宽阈值内的小次数迭代不该被过早抽象。"""
        g = Graph()
        a = g.add_node('a', 0, 0, operations=['x = 1'])
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b)
        g.add_edge(b, a)
        states, _ = analyze_reachability(g, a)
        assert states[b].ranges['x'].hi == 1


# ------------------------------------------------------------------
#  visualize
# ------------------------------------------------------------------

class TestVisualize:
    def test_report_contains_nodes(self):
        g = Graph()
        a = g.add_node('start', 0, 0)
        states, warnings = analyze_reachability(g, a)
        text = visualize_analysis_results(g, states, warnings)
        assert 'start' in text
        assert '=== 变量区间分析结果 ===' in text
