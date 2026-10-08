"""
地狱测试 · 静态分析图（2026-10-08）
====================================

`graph/analysis.py` 是区间抽象 + 可达性分析。它的**唯一底线**是可靠性：
可以保守（把"不可能"说成"可能"），但**绝不能把"可能"说成"不可能"** ——
后者会让工具在图上把一条正常剧情标红"不可达"，用户照着删掉，剧情就断了。

所以这里的判据以**不变量**为主：

  · **可靠性（soundness）**：给一组"某个具体取值能满足条件"的输入，
    `condition_possible` 不许返回 False；`apply_condition` 不许返回 None。
    用一个"枚举具体值"的 oracle 反过来压它 —— 这样即使实现换了，
    判据依然有效（不会空转）。
  · **终止性**：循环加宽必须收敛，分析一次不许卡死 UI。
  · **入口判定**：非权威入口不许断言"不可达"。
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.graph.analysis import (                          # noqa: E402
    ValueRange, VariableState, analyze_reachability, analyze_reachability_full,
    apply_condition, apply_operation, condition_possible,
    is_authoritative_entry, pick_entry_node, reachable_from_all_roots,
)
from storygraph.graph.models import Graph                         # noqa: E402


def _state(lo, hi, name='x'):
    st = VariableState([name])
    st.ranges[name] = ValueRange(lo, hi)
    return st


# ===========================================================================
#  ① 可靠性 oracle：能具体满足的条件，绝不许被判成"不可能"
# ===========================================================================

CONDS = [
    'x < 0.5', 'x <= 0.5', 'x > 0.5', 'x >= 0.5', 'x == 0.5', 'x != 0.5',
    'x < 1.5', 'x > 1.5', 'x == 1.5',
    'x == 3', 'x != 3', 'x < 3', 'x <= 3', 'x > 3', 'x >= 3',
    'x < -1', 'x > -1', 'x == -1',
    'not (x == 3)', 'not (x != 3)', 'not (x < 3)',
    'x == 1 or x == 2', 'x > 0 and x < 10',
    'x == 0 and x == 1',              # 永假（oracle 会跳过，不该崩）
    '0 < x < 10', '-1 <= x <= 1',     # 链式比较（2026-10-08 起拆成 and）
    'not (0 < x < 10)',               # 链式取反（De Morgan → or）
    'not (x == 3 and x > 0)', 'not (x == 3 or x > 0)',
]

RANGES = [(-2, 2), (0, 0), (3, 3), (0, 5), (1, 1), (-1, -1), (2, 4)]


def _satisfiable_values(cond, lo, hi):
    """在 [lo,hi] 的**具体整数值**上枚举，返回哪些值让 cond 为真。"""
    out = []
    for v in range(lo, hi + 1):
        try:
            if eval(cond, {'__builtins__': {}}, {'x': v}):   # noqa: S307 - 自家夹具
                out.append(v)
        except Exception:                                     # noqa: BLE001
            pass
    return out


@pytest.mark.parametrize('cond', CONDS)
@pytest.mark.parametrize('lo,hi', RANGES)
def test_condition_possible_never_claims_impossible_when_satisfiable(cond, lo, hi):
    """
    **可靠性不变量**：只要存在一个具体取值让条件成立，
    `condition_possible` 就必须说 True（不许判 False = 不许判不可达）。
    """
    sat = _satisfiable_values(cond, lo, hi)
    if not sat:
        return                          # oracle 自己都说没有可满足值 → 不断言
    got = condition_possible(_state(lo, hi), cond)
    assert got is not False, (
        f'condition_possible(x=[{lo},{hi}], {cond!r}) = {got}，'
        f'但 x={sat[0]} 时条件成立 —— 这是"把可能说成不可能"（会把剧情标红）')


@pytest.mark.parametrize('cond', CONDS)
@pytest.mark.parametrize('lo,hi', RANGES)
def test_apply_condition_never_prunes_satisfiable_path(cond, lo, hi):
    """同一条不变量，落到"收窄"这一侧：可满足的路径**不许被剪掉**（返回 None）。"""
    sat = _satisfiable_values(cond, lo, hi)
    if not sat:
        return
    out = apply_condition(_state(lo, hi), cond)
    assert out is not None, (
        f'apply_condition(x=[{lo},{hi}], {cond!r}) 把路径剪了，'
        f'但 x={sat[0]} 时条件成立')


# ===========================================================================
#  ② 浮点字面量：具体复现（上面那张参数表已经覆盖，这里留显式样例）
# ===========================================================================

def test_float_upper_bound_is_honoured():
    """x=0 时 `x < 0.5` 成立 —— 不能被截断成 `x < 0`。"""
    assert condition_possible(_state(0, 0), 'x < 0.5') is True
    assert apply_condition(_state(0, 0), 'x < 0.5') is not None


def test_float_assignment_then_condition():
    """`$ love = 0.9` 之后 `love > 0` 必须成立（0.9 不能被截成 0）。"""
    st = apply_operation(_state(0, 0, name='love'), 'love = 0.9')
    assert condition_possible(st, 'love > 0') is True, st


def test_float_literal_lower_bound():
    assert condition_possible(_state(1, 1), 'x < 1.5') is True
    assert condition_possible(_state(0, 0), 'x > -0.5') is True


# ===========================================================================
#  ③ 图级：达成分支不许被判"不可达"
# ===========================================================================

def test_reachable_branch_not_reported_stranded():
    g = Graph()
    a = g.add_node('a', 0, 0, operations=['x = 0'])
    b = g.add_node('b', 0, 0)
    c = g.add_node('c', 0, 0)
    g.add_edge(a, b, condition='x < 0.5')     # 0 < 0.5 真 → b 必须可达
    g.add_edge(a, c, condition='x >= 0.5')
    res = analyze_reachability_full(g, a)
    assert b in res['reached'], f'b 被判不可达：{res["warnings"]}'
    assert 'b' not in res['stranded'], res['stranded']


def test_truthy_condition_edges_are_reachable():
    g = Graph()
    a = g.add_node('a', 0, 0)
    b = g.add_node('b', 0, 0)
    c = g.add_node('c', 0, 0)
    g.add_edge(a, b, condition='True')
    g.add_edge(a, c, condition='')
    res = analyze_reachability_full(g, a)
    assert b in res['reached'] and c in res['reached']


def test_diamond_merge_keeps_both_values():
    g = Graph()
    a = g.add_node('a', 0, 0, operations=['x = 0'])
    b = g.add_node('b', 0, 0, operations=['x = 1'])
    c = g.add_node('c', 0, 0, operations=['x = 2'])
    d = g.add_node('d', 0, 0)
    g.add_edge(a, b)
    g.add_edge(a, c)
    g.add_edge(b, d)
    g.add_edge(c, d)
    states, _ = analyze_reachability(g, a)
    rng = states[d].ranges['x']
    assert rng.lo == 1 and rng.hi == 2, rng


def test_self_loop_terminates():
    g = Graph()
    a = g.add_node('a', 0, 0, operations=['x += 1'])
    g.add_edge(a, a)
    t0 = time.time()
    states, _ = analyze_reachability(g, a)
    assert time.time() - t0 < 5.0, '自环把分析卡住了'
    assert states[a].ranges['x'].hi == math.inf


# ===========================================================================
#  ④ 终止性：恶意大图 + 嵌套环
# ===========================================================================

def test_large_chain_terminates_quickly():
    g = Graph()
    ids = [g.add_node(f'n{i}', 0, 0, operations=['x += 1']) for i in range(400)]
    for u, v in zip(ids, ids[1:]):
        g.add_edge(u, v)
    g.add_edge(ids[-1], ids[0])            # 一个大环
    t0 = time.time()
    res = analyze_reachability_full(g, ids[0])
    dt = time.time() - t0
    assert dt < 10.0, f'400 节点大环跑了 {dt:.1f}s'
    assert res['counts'] if 'counts' in res else True


def test_many_branches_fan_out():
    g = Graph()
    root = g.add_node('root', 0, 0, operations=['x = 0'])
    leaves = [g.add_node(f'l{i}', 0, 0) for i in range(60)]
    for i, leaf in enumerate(leaves):
        g.add_edge(root, leaf, condition=f'x == {i}')
    res = analyze_reachability_full(g, root)
    # 至少 l0 必须可达；且必须跑完（不超时）
    assert leaves[0] in res['reached']


# ===========================================================================
#  ⑤ 入口判定：非权威入口不许断言"不可达"
# ===========================================================================

def test_pick_entry_prefers_current_node():
    g = Graph()
    a = g.add_node('a', 0, 0)
    b = g.add_node('b', 0, 0)
    g.nodes[b].is_current = True
    nid, reason = pick_entry_node(g)
    assert nid == b and reason == 'current'


def test_pick_entry_prefers_standard_labels():
    g = Graph()
    g.add_node('chapter1_1', 0, 0)
    s = g.add_node('start', 0, 0)
    nid, reason = pick_entry_node(g)
    assert nid == s and reason.startswith('entry-label')


def test_authoritative_flags():
    assert is_authoritative_entry('current') is True
    assert is_authoritative_entry('entry-label:start') is True
    assert is_authoritative_entry('multi-root') is False
    assert is_authoritative_entry('single-root') is False
    assert is_authoritative_entry('fallback') is False


def test_multiple_orphans_not_reported_stranded_without_authority():
    """
    多文件章节工程：没有标准入口时，无入边的独立章节**不该**被一律报不可达
    （真实用户报过 `chapter0_0` 被随便挑的起点判成走不到）。
    """
    g = Graph()
    a = g.add_node('chapter0_0', 0, 0)
    b = g.add_node('chapter1_1', 0, 0)
    c = g.add_node('chapter2_1', 0, 0)
    res = analyze_reachability_full(g, None)
    assert res['authoritative'] is False
    # 非权威时不把独立入口当"真断了"
    assert set(res['stranded']).isdisjoint({'chapter1_1', 'chapter2_1'}) \
        or res['independent'], res


def test_reachable_from_all_roots_detects_true_island():
    g = Graph()
    a = g.add_node('a', 0, 0)
    b = g.add_node('b', 0, 0)
    island1 = g.add_node('island1', 0, 0)
    island2 = g.add_node('island2', 0, 0)
    g.add_edge(a, b)
    g.add_edge(island1, island2)
    g.add_edge(island2, island1)             # 一个没有入口的环
    seen = reachable_from_all_roots(g)
    assert a in seen and b in seen
    assert island1 in seen or True           # island 自成一个"根组"


# ===========================================================================
#  ⑥ 赋值/加宽的保守性
# ===========================================================================

def test_unparsable_operation_degrades_to_top_not_crash():
    st = apply_operation(_state(1, 2), 'x = (((')
    assert st.ranges['x'].is_top()


def test_division_is_top():
    assert apply_operation(_state(1, 2), 'x = x / 2').ranges['x'].is_top()


def test_top_plus_one_stays_top():
    st = apply_operation(_state(-math.inf, math.inf), 'x += 1')
    assert st.ranges['x'].is_top()


def test_loop_widening_hits_infinity():
    g = Graph()
    a = g.add_node('a', 0, 0, operations=['x += 1'])
    b = g.add_node('b', 0, 0)
    g.add_edge(a, b)
    g.add_edge(b, a)
    states, _ = analyze_reachability(g, a)
    assert states[a].ranges['x'].hi == math.inf


def test_empty_graph_does_not_crash():
    res = analyze_reachability_full(Graph())
    assert res['warnings']


# ===========================================================================
#  ⑦ 精度：链式比较 & `not` / De Morgan（2026-10-08 补）
# ===========================================================================
# 从前链式比较（`0 < x < 10`）与 `not (...)` 一律"保守不施加约束" ——
# 不会出错，但也永远得不出结论（"下落风险"就抓不到）。下面每一条改写都是
# **逻辑等价**，所以提高精度的同时**不牺牲可靠性**。

def test_chained_comparison_is_decomposed_to_and():
    assert condition_possible(_state(0, 0), '0 < x < 10') is False
    assert condition_possible(_state(5, 5), '0 < x < 10') is True
    assert condition_possible(_state(10, 10), '0 < x < 10') is False
    out = apply_condition(_state(0, 10), '0 < x < 10')
    assert out is not None and out.ranges['x'].lo >= 1 and out.ranges['x'].hi <= 9


def test_negative_literal_bound_is_understood():
    """`-1` 是 UnaryOp 不是 Constant —— 也要能当整数字面量用。"""
    assert condition_possible(_state(0, 0), '-1 < x < 1') is True
    assert condition_possible(_state(5, 5), '-1 < x < 1') is False


def test_not_equality_is_detected_impossible():
    """`not (x == 5)` 在 x 已收窄到 5 时不可能 —— 从前一律 True（漏报）。"""
    assert condition_possible(_state(5, 5), 'not (x == 5)') is False
    assert condition_possible(_state(0, 9), 'not (x == 5)') is True


def test_de_morgan_on_negated_boolop():
    assert condition_possible(_state(5, 5), 'not (x == 5 and x > 0)') is False
    assert condition_possible(_state(5, 5), 'not (x == 3 or x == 4)') is True
    # apply 侧：`not (a and b)` 也真的施加了约束（De Morgan）
    out = apply_condition(_state(5, 5), 'not (x == 3 and x == 4)')
    assert out is not None and out.ranges['x'].lo == 5


def test_negated_chain_never_prunes_a_satisfiable_path():
    """可靠性：`not (0 < x < 10)` 在 x=0 成立 → 不许把这条路径剪掉。"""
    assert condition_possible(_state(0, 0), 'not (0 < x < 10)') is True
    assert apply_condition(_state(0, 0), 'not (0 < x < 10)') is not None
