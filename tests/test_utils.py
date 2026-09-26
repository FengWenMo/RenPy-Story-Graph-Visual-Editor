"""
tests/test_utils.py
===================
utils.extract_variables / collect_all_variables 测试。

这些规则是"注释里写死的行为契约"，属于易回归区：
  · 属性访问只收最内层
  · 函数名不收，参数收
  · 关键字参数名不收
  · 无法解析返回空列表（不是抛异常）
"""

from models import Graph
from utils import extract_variables, collect_all_variables


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
