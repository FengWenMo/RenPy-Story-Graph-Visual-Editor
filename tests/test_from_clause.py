"""
tests/test_from_clause.py
=========================
`call X from Y` 的 **from 子句**保真（用户 2026-10-05："先修复子句会丢的问题"）。

`from` 是 Ren'Py 的**存档点标识**：同一个 label 被多处 call 时靠它区分存档
属于哪一处。丢了它，导出的脚本语义等价、但**老存档会找不到 `_call_xxx`**
（回滚到上一个 label）。修前：导入就丢（`CallNode` 没这个字段）、导出更丢。
"""


def _beats(write_rpy, src):
    from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                 RenPyParser)
    from storygraph.story.beat_build import beats_from_parse_result
    fp = write_rpy(src, 'proj/game/ch01.rpy')
    pr = ParseResultBuilder().build(RenPyParser().parse(str(fp)))
    return fp, beats_from_parse_result(pr)


def test_call_from_is_captured_on_the_edge(write_rpy):
    from storygraph.graph.models import Graph
    from storygraph.parsing.renpy_parser import batch_import_chapters
    fp = write_rpy(
        'label a_control:\n'
        '    call a from _call_a\n'
        '    jump b_control\n'
        'label a:\n'
        '    "正文"\n'
        '    return\n'
        'label b_control:\n'
        '    call b from _call_b\n'
        'label b:\n'
        '    return\n', 'ctrler.rpy')
    g = Graph()
    batch_import_chapters([fp], g)
    clauses = sorted(e.from_clause for e in g.edges.values() if e.from_clause)
    assert clauses == ['_call_a', '_call_b'], clauses


def test_two_calls_with_different_from_are_not_deduped(write_rpy):
    """去重键必须带 `from_clause`，否则第二次 call 会被误去掉。"""
    from storygraph.graph.models import Graph
    from storygraph.parsing.renpy_parser import batch_import_chapters
    fp = write_rpy(
        'label one:\n'
        '    call body from _call_body_1\n'
        '    return\n'
        'label two:\n'
        '    call body from _call_body_2\n'
        '    return\n'
        'label body:\n'
        '    "正文"\n'
        '    return\n', 'twice.rpy')
    g = Graph()
    batch_import_chapters([fp], g)
    calls = [e for e in g.edges.values() if e.type == 'call']
    assert len(calls) == 2, f'两次 call 被去重成 {len(calls)} 条'
    assert {e.from_clause for e in calls} == {'_call_body_1', '_call_body_2'}


def test_exporter_writes_from(write_rpy, tmp_path):
    from storygraph.graph.models import Graph
    from storygraph.pipeline.renpy_exporter import export_to_renpy
    g = Graph()
    a = g.add_node('a', 0, 0)
    b = g.add_node('b', 0, 0)
    g.add_edge(a, b, etype='call', from_clause='_call_b')
    g.control_flow = False
    out = tmp_path / 'x.rpy'
    export_to_renpy(g, str(out))
    text = out.read_text(encoding='utf-8')
    # 章节图模式会把目标包成 `b_control`，但 `from` 必须原样带出来
    assert 'call b_control from _call_b' in text, text


def test_layout_json_keeps_from_clause(tmp_path):
    from storygraph.app.editor import EditorSession
    from storygraph.graph.models import Graph
    g = Graph()
    a = g.add_node('a', 0, 0)
    b = g.add_node('b', 0, 0)
    g.add_edge(a, b, etype='call', from_clause='_call_b')
    s = EditorSession()
    target = tmp_path / 'lay.json'
    assert s.save_layout(g, target)['ok']
    back = s.load_layout(target)
    assert back['ok']
    edge = next(iter(back['graph'].edges.values()))
    assert edge.from_clause == '_call_b', edge.from_clause


def test_round_trip_keeps_from(write_rpy, tmp_path):
    from storygraph.app.editor import EditorSession
    from storygraph.graph.models import Graph
    from storygraph.pipeline.renpy_exporter import export_to_renpy
    src = ('label a_control:\n'
           '    call a from _call_a\n'
           '    jump b_control\n'
           'label a:\n'
           '    "正文"\n'
           '    return\n'
           'label b_control:\n'
           '    return\n')
    fp = write_rpy(src, 'proj/game/c.rpy')
    s = EditorSession(project_root=tmp_path / 'proj')
    s.load_assets()
    g = Graph()
    s.import_chapters([fp], g)
    out = tmp_path / 'out.rpy'
    export_to_renpy(g, str(out))
    text = out.read_text(encoding='utf-8')
    # 这一章有正文 → 章节图模式（目标会被包一层），但 `from` 必须还在
    assert 'from _call_a' in text, text
    g2 = Graph()
    s.import_chapters([out], g2)
    assert any(e.from_clause == '_call_a' for e in g2.edges.values()), \
        '往返之后 from 丢了'
