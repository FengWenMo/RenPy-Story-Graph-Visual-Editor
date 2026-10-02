"""
tests/test_graph_statements.py
==============================
**结构债迁移**的回归测试（用户 2026-10-05："开始迁移，处理结构债"）。

背景（审计结论"双 IR 并存"）：`core.Statement` 是结构化 IR，而
`graph.Node` 只存 `operations: list[str]`（裸代码字符串），下游靠正则/AST
反解。迁移后的形状：

    node.statements : list[Statement]   ← 结构化真源（含出处 / 条件）
    node.operations : list[str]          ← 兼容视图（派生，老调用方照旧）

本文件锁四件事：

  1. **往返逐字节一致**：字符串 → 语句 → 字符串（含行首 `\\x01` 紧凑写法、
     多行、空串、Unicode），一个字符都不许变；
  2. **兼容视图没有"共享引用"这类暗坑**：读出来是新列表（改它不影响节点）；
     要增删得走 `add_operation` / `add_statement` / `clear_operations`；
  3. **结构化之后多拿到的信息真的留住了**：`source`（文件/行号）与
     `condition`（这条代码原本在哪个 if 里）—— 变量编辑器保存一次也不许丢；
  4. **老文件照旧能读**：只有 `operations` 的 `.json` 布局文件不受影响。
"""

from __future__ import annotations

import ast
from pathlib import Path

from storygraph.core.statement import (MARKER, Statement, StatementKind,
                                       is_code_statement, operation_source,
                                       python_operation)
from storygraph.graph.models import Graph, Node

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / 'storygraph'


def test_operations_round_trip_is_byte_exact():
    """字符串 → 语句 → 字符串：一个字符都不许变（含标记与多行）。"""
    ops = [
        'love += 1',
        MARKER + 'persistent.lock["cg1"]= True',      # 紧凑 `$x` 写法
        'x = 1\ny = 2',                                # 多行 python 块
        '',                                            # 空串（老数据里有）
        '# 注释一行',
        'renpy.transition(Dissolve(0.1), layer="master")',
        'msg_add("L|叶怀瑾|images/fnm5.png|你想好了要来青蓝实验室吗？")',
    ]
    node = Node(1, 'ch')
    node.set_operations(ops)
    assert node.operations == ops
    # 再来两遍，仍然一样（幂等）
    node.operations = list(node.operations)
    node.operations = list(node.operations)
    assert node.operations == ops
    assert all(is_code_statement(s) for s in node.statements)


def test_tight_dollar_marker_survives_as_a_statement_field():
    """紧凑写法这一位在语句里也要保得住（`dollar_space` 语义）。"""
    stmt = python_operation(MARKER + 'x += 1')
    assert stmt.kind == StatementKind.PYTHON
    assert stmt.payload['code'] == MARKER + 'x += 1'
    assert operation_source(stmt) == MARKER + 'x += 1'
    plain = python_operation('x += 1')
    assert plain.payload['code'] == 'x += 1'


def test_operations_view_returns_a_fresh_list():
    """
    读出来是**新列表** —— 于是 `node.operations.append(x)` 那种写法会静默失效
    （最阴的坑）。要改就走 `add_operation`。守卫见 `test_no_direct_list_mutation`。
    """
    node = Node(1, 'ch', )
    node.add_operation('x = 1')
    view = node.operations
    view.append('y = 2')
    assert node.operations == ['x = 1'], '视图被改了却影响到了节点'


def test_add_and_clear_helpers():
    node = Node(1, 'ch')
    node.add_operation('a = 1')
    node.add_operation('b = 2', index=0)
    assert node.operations == ['b = 2', 'a = 1']
    node.add_statement(Statement.make_python('c = 3'), index=1)
    assert node.operations == ['b = 2', 'c = 3', 'a = 1']
    node.clear_operations()
    assert node.operations == [] and node.statements == []


def test_statements_carry_source_and_condition():
    stmt = Statement.make_python('flag = True', 'love > 5',
                                 source=('/p/ch.rpy', 42))
    node = Node(7, 'ch')
    node.add_statement(stmt)
    got = node.statements[0]
    assert got.condition == 'love > 5'
    assert tuple(got.source) == ('/p/ch.rpy', 42)
    # 兼容视图只取代码文本 —— 与迁移前完全一致
    assert node.operations == ['flag = True']


def test_graph_copy_deep_copies_statements():
    g = Graph()
    nid = g.add_node('a', 0, 0, operations=['x = 1'])
    g.nodes[nid].add_statement(Statement.make_python('y = 2', 'cond',
                                                     source=('f.rpy', 9)))
    other = g.copy()
    other.nodes[nid].add_statement(Statement.make_python('z = 3'))
    assert g.nodes[nid].operations == ['x = 1', 'y = 2']
    assert g.nodes[nid].statements[1].condition == 'cond'
    assert tuple(g.nodes[nid].statements[1].source) == ('f.rpy', 9)


def test_layout_json_round_trip_keeps_provenance(tmp_path):
    """存布局 → 读布局：出处与条件都要在（老格式的 `operations` 也照旧写）。"""
    from storygraph.app.editor import EditorSession
    g = Graph()
    nid = g.add_node('ch', 10, 20)
    g.nodes[nid].add_statement(Statement.make_python('flag = True', 'love > 5',
                                                     source=('/p/ch.rpy', 42)))
    g.nodes[nid].add_operation(MARKER + 'persistent.a = True')
    target = tmp_path / 'layout.json'
    s = EditorSession()
    assert s.save_layout(g, target)['ok']

    back = s.load_layout(target)
    assert back['ok']
    graph = back['graph']
    node = graph.nodes[max(graph.nodes)]
    assert node.operations == ['flag = True',
                               MARKER + 'persistent.a = True']
    assert node.statements[0].condition == 'love > 5'
    assert tuple(node.statements[0].source) == ('/p/ch.rpy', 42)


def test_old_layout_file_without_statements_still_loads(tmp_path):
    """只有 `operations` 的老文件（旧版存过的）必须照样能开。"""
    import json
    from storygraph.app.editor import EditorSession
    old = {
        'nodes': {'3': {'label': 'a', 'x': 1, 'y': 2, 'is_current': True,
                        'operations': ['x = 1', MARKER + 'y = 2'],
                        'file_path': '/p/a.rpy', 'line_number': 7}},
        'edges': {},
        'next_ids': {'node': 9, 'edge': 4},
    }
    target = tmp_path / 'old.json'
    target.write_text(json.dumps(old, ensure_ascii=False), encoding='utf-8')
    res = EditorSession().load_layout(target)
    assert res['ok'], res
    node = res['graph'].nodes[3]
    assert node.operations == ['x = 1', MARKER + 'y = 2']
    assert node.file_path == '/p/a.rpy' and node.line_number == 7


def test_set_node_variables_keeps_tech_statements_provenance():
    """
    变量编辑器保存一次之后，**技术代码那条语句还是原来那个对象** ——
    出处（文件/行号）与条件都不会因为"改了个变量"丢掉。
    """
    from storygraph.app.editor import EditorSession
    g = Graph()
    nid = g.add_node('ch', 0, 0)
    g.nodes[nid].add_statement(Statement.make_python(
        'renpy.transition(Dissolve(0.1))', source=('/p/ch.rpy', 12)))
    g.nodes[nid].add_statement(Statement.make_python(
        'love += 1', source=('/p/ch.rpy', 13)))
    s = EditorSession()
    res = s.set_node_variables(g, nid, ['love += 99'])
    assert res['ok'] and res['variables'] == 1 and res['kept_tech'] == 1
    node = g.nodes[nid]
    assert node.operations == ['renpy.transition(Dissolve(0.1))',
                               'love += 99']
    assert tuple(node.statements[0].source) == ('/p/ch.rpy', 12), \
        '技术代码的出处被变量保存吃掉了'


def test_exporter_reads_the_structured_source():
    """
    导出那条路读的是**语句表**（不是兼容视图）：非代码语句不会被写成 `$ …`。
    """
    from storygraph.pipeline.renpy_exporter import _operation_texts
    node = Node(1, 'ch')
    node.add_statement(Statement.make_python('x = 1'))
    node.add_statement(Statement.make_say('e', '你好'))     # 不是"操作"
    node.add_statement(Statement.make_raw('with dissolve'))
    assert _operation_texts(node) == ['x = 1', 'with dissolve']


def test_no_direct_list_mutation_in_the_codebase():
    """
    **守卫**：`node.operations.append(...)` 这种写法会静默失效（视图是新列表）。
    全项目不许再有 —— 要改走 `add_operation` / `set_operations` /
    `clear_operations`。

    用 AST 判（不扫文本）：注释与文档里提到这个写法是**故意的**（就是要警告别人）。
    """
    offenders = []
    for path in sorted(PKG.rglob('*.py')):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or \
                    func.attr not in ('append', 'extend', 'insert', 'remove',
                                      'pop', 'sort', 'clear'):
                continue
            target = func.value
            if isinstance(target, ast.Attribute) and target.attr == 'operations':
                offenders.append(
                    f'{path.relative_to(PKG).as_posix()}:{node.lineno}')
    assert offenders == [], (
        f'这些地方还在原地改兼容视图（会静默失效）：{offenders}')


def test_node_statements_field_is_the_truth():
    """`operations` 必须是**属性**（派生视图），不是和数据各存一份的字段。"""
    assert isinstance(Node.operations, property), \
        'Node.operations 应该是 property（否则又变成两份真源）'
    assert isinstance(Node.operations.fset, type(Node.operations.fset))
    node = Node(1, 'a')
    assert 'operations' not in node.__dict__, \
        'Node 里不该再存一份 operations（真源只有 statements）'
    assert node.__dict__.get('statements') == []
