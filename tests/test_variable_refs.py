# -*- coding: utf-8 -*-
"""
变量对不上：**条件读的** 与 **节点写的** 差一个字符（2026-10-28 真实项目 P0）。

现场：12 个章节节点里写 `Plotnode214=1`……，5 条分支条件里写
`Plotnodes214 + Plotnodes224 + Plotnodes244 == 3`。语法检查（`ast.parse`）
两行都过，Ren'Py lint 也不报（`plottree.rpy` 里有 `default`），于是 4 条路线
永远不成立、全部掉进兜底分支 —— 玩家永远进不去那四条线。

这里钉住三件事：

  · 抽变量要**准**（`persistent.x` / `d["k"]` 不算给 `x` / `d` 赋值）；
  · "读了没人写"要报，并且**点名**那个只差一个字符的已写变量；
  · 正常的 `love` / `flag` 不许被指手画脚。
"""
from __future__ import annotations

from storygraph.graph.models import Graph
from storygraph.story.variable_refs import (assignment_variables,
                                            condition_variables,
                                            describe_near_miss,
                                            describe_unassigned)


def _graph(*, written, conditions):
    g = Graph()
    a = g.add_node('a', 0, 0)
    b = g.add_node('b', 0, 0)
    g.nodes[a].set_operations(list(written))
    for cond in conditions:
        g.add_edge(a, b, condition=cond)
    return g


# ===========================================================================
#  一、抽名字
# ===========================================================================

class TestExtraction:
    def test_condition_reads(self):
        got = condition_variables(
            '(Plotnodes211 + Plotnodes221 + Plotnodes231 != 3)')
        assert got == {'Plotnodes211', 'Plotnodes221', 'Plotnodes231'}

    def test_condition_ignores_literals(self):
        assert condition_variables('flag == True and None is not False') == {
            'flag'}

    def test_condition_broken_syntax_falls_back(self):
        # 括号不配平：AST 失败，正则兜底也得把名字捞出来
        assert 'love' in condition_variables('love +')

    def test_assignment_plain(self):
        assert assignment_variables('Plotnode244=1') == {'Plotnode244'}

    def test_assignment_aug_and_annotated(self):
        assert assignment_variables('love += 1') == {'love'}
        assert assignment_variables('n: int = 3') == {'n'}

    def test_assignment_unpacking(self):
        assert assignment_variables('a, b = 1, 2') == {'a', 'b'}

    def test_attribute_and_subscript_are_not_variables(self):
        assert assignment_variables('persistent.x = 1') == set()
        assert assignment_variables('d["k"] = 1') == set()

    def test_tight_dollar_marker_is_stripped(self):
        from storygraph.core.statement import MARKER
        assert assignment_variables(MARKER + 'flag=True') == {'flag'}


# ===========================================================================
#  二、对不上就说
# ===========================================================================

class TestUnassigned:
    def test_typo_is_reported_with_the_written_name(self):
        g = _graph(written=['Plotnode244=1'],
                   conditions=['Plotnodes244 == 1'])
        msgs = describe_unassigned(g)
        assert msgs, '差一个字符必须报'
        assert 'Plotnodes244' in msgs[0] and 'Plotnode244' in msgs[0]
        assert '拼错' in msgs[0]

    def test_matching_names_say_nothing(self):
        g = _graph(written=['Plotnode244=1'],
                   conditions=['Plotnode244 == 1'])
        assert describe_unassigned(g) == []

    def test_unrelated_unassigned_still_reported(self):
        g = _graph(written=['love += 1'], conditions=['affection > 3'])
        msgs = describe_unassigned(g)
        assert msgs and 'affection' in msgs[0]
        assert '拼错' not in msgs[0]      # 没有像的名字，不许乱猜

    def test_a_name_written_by_any_node_counts(self):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.nodes[a].set_operations(['flag = 1'])
        g.add_edge(b, c, condition='flag == 1')
        assert describe_unassigned(g) == []


# ===========================================================================
#  三、变量对话框里那一下「语法检查」
# ===========================================================================

class TestNearMiss:
    def test_typing_the_typo_is_caught_immediately(self):
        g = _graph(written=[], conditions=['Plotnodes244 == 1'])
        msgs = describe_near_miss(g, ['Plotnode244=1'])
        assert msgs and 'Plotnodes244' in msgs[0]

    def test_typing_the_right_name_is_quiet(self):
        g = _graph(written=[], conditions=['Plotnodes244 == 1'])
        assert describe_near_miss(g, ['Plotnodes244=1']) == []

    def test_ordinary_variables_are_not_nagged(self):
        g = _graph(written=[], conditions=['Plotnodes244 == 1'])
        assert describe_near_miss(g, ['love += 1', 'flag = True']) == []
