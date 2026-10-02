"""
tests/test_utils.py
===================
utils.extract_variables / collect_all_variables / declared_variables 测试。

这些规则是"注释里写死的行为契约"，属于易回归区：
  · 属性访问只收最内层
  · 函数名不收，参数收
  · 关键字参数名不收
  · 无法解析返回空列表（不是抛异常）
  · `declared_variables` 只收**被赋值的名字**（`default` 行的唯一依据）
"""

from storygraph.graph.models import Graph
from storygraph.core.utils import (collect_all_variables, declared_variables,
                                   extract_variables)


class TestExtractVariables:
    def test_simple_assign_and_augassign(self):
        assert extract_variables('x = 10') == ['x']
        assert extract_variables('x += 5') == ['x']

    def test_keeps_first_occurrence_order(self):
        got = extract_variables('b = a + c')
        assert got == ['b', 'a', 'c']

    def test_builtins_excluded(self):
        assert extract_variables('result = abs(value)') == ['result', 'value']
        assert extract_variables('min(a, b)') == ['a', 'b']
        assert extract_variables('len(x)') == ['x']

    def test_literals_excluded(self):
        assert extract_variables('x = True') == ['x']
        assert extract_variables('None') == []

    def test_attribute_access_keeps_innermost(self):
        assert extract_variables('a.b.c') == ['a']
        assert extract_variables('store.x.y = 1') == ['store']

    def test_subscript(self):
        got = set(extract_variables('arr[i] = arr[j] + 1'))
        assert got == {'arr', 'i', 'j'}

    def test_keyword_argument_name_excluded(self):
        assert extract_variables('f(x=1)') == []

    def test_nested_calls(self):
        assert set(extract_variables('x = f(y) + g(z)')) == {'x', 'y', 'z'}

    def test_empty_and_whitespace(self):
        assert extract_variables('') == []
        assert extract_variables('   ') == []
        assert extract_variables(None) == []

    def test_invalid_syntax_returns_empty(self):
        assert extract_variables('### 无效语法 ###') == []
        assert extract_variables('x =') == []

    def test_exec_mode_multi_line(self):
        got = set(extract_variables('x = 1\ny = x + 2'))
        assert got == {'x', 'y'}

    def test_ddollar_operators(self):
        assert extract_variables('is_ok = love > 5 and money >= 100') == [
            'is_ok', 'love', 'money']

    def test_no_duplicates(self):
        assert extract_variables('x = x + x') == ['x']


class TestCollectAllVariables:
    def _graph(self):
        g = Graph()
        a = g.add_node('start', 0, 0, operations=['score = 1', 'love = 2'])
        b = g.add_node('end', 0, 0, operations=['money += score'])
        g.add_edge(a, b, 'jump', 'gold > 10')
        return g, a, b

    def test_collects_ops_and_conditions(self):
        g, a, b = self._graph()
        got = collect_all_variables(g)
        assert {'score', 'love', 'money', 'gold'} <= got

    def test_empty_graph(self):
        assert collect_all_variables(Graph()) == set()

    def test_deduplicated(self):
        g, a, b = self._graph()
        g.nodes[a].operations.append('score = 9')
        result = collect_all_variables(g)
        assert len([v for v in result if v == 'score']) == 1


class TestDeclaredVariables:
    """
    `declared_variables` 是**控制流导出写 `default x = 0` 的唯一依据**。

    2026-10-05 审计"双 IR 并存 / 两套生成器"时，用真实项目导出实测到产物里有

        default renpy = 0
        default persistent = 0
        default config = 0
        default time = 0
        default i = 0

    `default renpy = 0` 会把引擎模块遮蔽成 int → 整章跑不起来。
    根因是导出用了"出现过的名字"（`collect_all_variables`）。
    """

    def test_only_assignment_targets(self):
        assert declared_variables(['love += 1']) == ['love']
        assert declared_variables(['x = f(y)']) == ['x']
        assert declared_variables(['score = love + 1']) == ['score']

    def test_engine_modules_are_never_declared(self):
        assert declared_variables(['renpy.transition(Dissolve(0.1))']) == []
        assert declared_variables(['persistent.a = 1']) == []
        assert declared_variables(['config.developer = True']) == []
        assert declared_variables(['import random\nx = random.randint(1, 3)']) \
            == ['x']
        assert declared_variables([], conditions=['renpy.foo and bar']) == []

    def test_loop_temporaries_are_not_declared(self):
        ops = ['for i in range(3):\n    total += i',
               'vals = [j for j in range(3)]']
        assert declared_variables(ops) == ['total', 'vals']

    def test_tuple_unpacking(self):
        assert declared_variables(['a, b = pair']) == ['a', 'b']

    def test_tight_dollar_marker_is_stripped_first(self):
        """`$love += 1`（紧凑写法）也要认出来，否则它是**写不进 default** 的。"""
        assert declared_variables(['\x01love += 1']) == ['love']

    def test_conditions_do_not_declare(self):
        """条件里出现的名字只是被读，不该被 `default`。"""
        assert declared_variables(['x = 1'], conditions=['love > 5']) == ['x']

    def test_graph_helper(self):
        from storygraph.core.utils import declared_variables_of_graph
        g = Graph()
        a = g.add_node('a', 0, 0, operations=['love += 1',
                                              'renpy.transition(x)'])
        b = g.add_node('b', 0, 0, operations=['flag = True'])
        g.add_edge(a, b, 'jump', 'love > 5')
        assert declared_variables_of_graph(g) == ['love', 'flag']
