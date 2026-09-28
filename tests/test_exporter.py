"""
tests/test_exporter.py
======================
反向生成 .rpy 控制流脚本测试。

检查点：
  · 收集到的变量都会 default 声明
  · 每个 node 生成 <label>_control
  · 无出边 → return
  · 单一无条件边 → jump/call
  · 条件边 → if/elif
  · 菜单边 → menu 块
  · 多行 python 转成 python: 缩进块
  · 目标节点缺失时跳过该分支（不写坏 label）
"""

import pytest

from storygraph.graph.models import Graph
from storygraph.pipeline.renpy_exporter import export_to_renpy


@pytest.fixture
def export(tmp_path, write_rpy):
    def _run(graph):
        out = tmp_path / 'generated.rpy'
        export_to_renpy(graph, out)
        assert out.exists()
        return out.read_text(encoding='utf-8')
    return _run


class TestExport:
    def test_variable_defaults_emitted(self, export):
        g = Graph()
        a = g.add_node('start', 0, 0, operations=['love = 1'])
        b = g.add_node('end', 0, 0, operations=['money += 2'])
        g.add_edge(a, b)
        text = export(g)
        assert 'default love = 0' in text
        assert 'default money = 0' in text

    def test_control_label_generated(self, export):
        g = Graph()
        g.add_node('start', 0, 0)
        text = export(g)
        assert 'label start_control:' in text
        assert 'call start' in text

    def test_no_out_edge_generates_return(self, export):
        g = Graph()
        g.add_node('lonely', 0, 0)
        text = export(g)
        assert 'return' in text

    def test_unconditional_edge(self, export):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b, etype='jump')
        text = export(g)
        assert 'jump b_control' in text

    def test_call_edge_type_preserved(self, export):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b, etype='call')
        assert 'call b_control' in export(g)

    def test_conditional_edges_use_if_elif(self, export):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(a, b, condition='x > 1')
        g.add_edge(a, c, condition='x <= 1')
        text = export(g)
        assert 'if x > 1:' in text
        assert 'elif x <= 1:' in text

    def test_conditional_with_else(self, export):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(a, b, condition='x > 1')
        g.add_edge(a, c)
        text = export(g)
        assert 'else:' in text
        assert 'jump c_control' in text

    def test_menu_block(self, export):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(a, b, option_text='Yes')
        g.add_edge(a, c, option_text='No')
        text = export(g)
        assert 'menu:' in text
        assert '"Yes":' in text
        assert 'jump b_control' in text

    def test_menu_with_condition_uses_outer_if(self, export):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(a, b, condition='x > 1')
        g.add_edge(a, c, option_text='No')
        text = export(g)
        assert 'if x > 1:' in text
        assert 'else:' in text

    def test_single_line_operation(self, export):
        g = Graph()
        g.add_node('a', 0, 0, operations=['love = 5'])
        assert '$ love = 5' in export(g)

    def test_multiline_operation_uses_python_block(self, export):
        g = Graph()
        g.add_node('a', 0, 0, operations=['x = 1\ny = 2'])
        text = export(g)
        assert 'python:' in text
        assert '        x = 1' in text
        assert '        y = 2' in text

    def test_missing_target_node_skipped(self, export):
        """指向不存在节点的边不应写出坏 label。"""
        g = Graph()
        a = g.add_node('a', 0, 0)
        g.add_edge(a, 999)
        text = export(g)
        assert 'jump' not in text

    def test_empty_graph_exports_header(self, export):
        g = Graph()
        text = export(g)
        assert '自动生成的控制流' in text

    def test_export_failure_reports_error(self, export, clear_messagebox):
        g = Graph()
        g.add_node('a', 0, 0)
        export_to_renpy(g, '/nonexistent/dir/out.rpy')
        assert clear_messagebox['error']

    def test_success_reports_info(self, export, clear_messagebox, tmp_path):
        g = Graph()
        g.add_node('a', 0, 0)
        export_to_renpy(g, tmp_path / 'ok.rpy')
        assert clear_messagebox['info']
