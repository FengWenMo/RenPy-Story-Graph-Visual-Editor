"""
tests/test_flow_edges_rebuild.py
================================
第二阶段 B：**图边从 flow 拍派生**（`rebuild_source_edges`）。

第一阶段的图边是"解析时顺手建的"，改卡片里的 target 靠 `_sync_flow_edge`
**手动**改那一条 —— 两份真源，靠约定同步。这一层把它收成一份：
`flow_edges(beats)` 是唯一真源，导入之后按它对齐图上的 `source` 边。

判据（都看 `graph.edges` 的实际内容）：

  A. **两次 call 同一目标 = 两条边**，各带自己的 `line_id`
     （老做法按 `from+type+to` 找第一条，那种场景会指错 —— 待办 #4）；
  B. **幂等**：同一份源导入两次，边数不变（不重复画）；
  C. **改目标后重新导入 → 边改指**，而不是"新旧两条"；
  D. **不碰 `manual`**：用户画的草图一条都不动；
  E. **不碰菜单选项的边**（`option_text` 非空）—— 那不是纯 call/jump；
  F. **目标不在图上 → 跳过并记在 `skipped`**（不硬造幽灵节点）。

刻意**不验**"源里删掉一个 call → 边消失"：这一版不做删除（`rebuild_source_edges`
的 docstring 里写了原因 —— 删除要知道"上次有哪些 line_id"，和 P2-1 一起做）。
"""

import pytest

from storygraph.app.editor import EditorSession
from storygraph.app.session_graph import rebuild_source_edges
from storygraph.graph.models import Graph
from storygraph.story.beat_build import beats_from_parse_result


def _project(tmp_path, body):
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'a.rpy').write_text(body, encoding='utf-8')
    return root, root / 'game' / 'a.rpy'


def _beats(rpy):
    from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                 RenPyParser)
    return beats_from_parse_result(
        ParseResultBuilder().build(RenPyParser().parse(str(rpy))))


def _edgelist(graph):
    names = {nid: n.label_name for nid, n in graph.nodes.items()}
    return sorted((names[e.from_id], names[e.to_id], e.type,
                   getattr(e, 'line_id', ''), getattr(e, 'origin', ''))
                  for e in graph.edges.values())


class TestTwoCallsToOneTarget:
    def test_two_calls_are_two_edges(self, tmp_path):
        root, rpy = _project(tmp_path, (
            'label main:\n    e "a"\n    call side\n    e "b"\n'
            '    call side\n    return\n'))
        g = Graph()
        for name in ('main', 'side'):
            g.add_node(name, 0, 0)
        res = rebuild_source_edges(g, _beats(rpy))
        rows = [r for r in _edgelist(g) if r[0] == 'main' and r[1] == 'side']
        assert len(rows) == 2, rows
        # 两条边的 line_id 必须不同 —— 那才是"哪一拍"的身份
        assert len({r[3] for r in rows}) == 2, rows
        assert all(r[4] == 'source' for r in rows)
        assert res['added'] + res['identified'] >= 1

    def test_rebuild_is_idempotent(self, tmp_path):
        root, rpy = _project(tmp_path, (
            'label main:\n    e "a"\n    call side\n'
            '    call side\n    return\n'))
        g = Graph()
        for name in ('main', 'side'):
            g.add_node(name, 0, 0)
        beats = _beats(rpy)
        rebuild_source_edges(g, beats)
        first = _edgelist(g)
        out = rebuild_source_edges(g, beats)
        assert _edgelist(g) == first, '第二次派生把边改动/重复了'
        assert out['added'] == 0, out

    def test_changed_target_retargets_instead_of_duplicating(self, tmp_path):
        """把 `call side` 改成 `call other` 后重新派生 → 改指，不是新旧两条。"""
        root, rpy = _project(tmp_path, 'label main:\n    call side\n    return\n')
        g = Graph()
        for name in ('main', 'side', 'other'):
            g.add_node(name, 0, 0)
        beats = _beats(rpy)
        rebuild_source_edges(g, beats)
        line = [e for e in g.edges.values()][0].line_id
        assert line

        # 模拟用户把目标改掉（卡片保存后 raw 变了）
        beats[0].raw = 'call other'
        out = rebuild_source_edges(g, beats)
        assert out['retargeted'] == 1, out
        rows = _edgelist(g)
        assert len(rows) == 1, rows
        assert rows[0][1] == 'other', rows
        assert rows[0][3] == line, '改目标不该换掉 line_id'


class TestWhatRebuildMustNotTouch:
    def test_manual_edges_are_untouched(self, tmp_path):
        root, rpy = _project(tmp_path, 'label main:\n    call side\n    return\n')
        g = Graph()
        for name in ('main', 'side', 'draft'):
            g.add_node(name, 0, 0)
        # 用户画的草图：main → draft
        mid = {n.label_name: nid for nid, n in g.nodes.items()}
        g.add_edge(mid['main'], mid['draft'], 'jump', origin='manual')
        rebuild_source_edges(g, _beats(rpy))
        manual = [e for e in g.edges.values() if e.origin == 'manual']
        assert len(manual) == 1, '手工边被派生删掉了'
        assert manual[0].to_id == mid['draft']

    def test_menu_option_edges_are_untouched(self, tmp_path):
        """菜单选项的 jump 边带 `option_text`，不是纯控制流边。"""
        root, rpy = _project(tmp_path, 'label main:\n    call side\n    return\n')
        g = Graph()
        for name in ('main', 'side', 'roof'):
            g.add_node(name, 0, 0)
        mid = {n.label_name: nid for nid, n in g.nodes.items()}
        g.add_edge(mid['main'], mid['roof'], 'jump',
                   option_text='去屋顶看看', origin='source')
        rebuild_source_edges(g, _beats(rpy))
        menu = [e for e in g.edges.values() if e.option_text]
        assert len(menu) == 1, '菜单边被当成控制流边处理了'

    def test_target_not_on_graph_is_skipped_not_invented(self, tmp_path):
        root, rpy = _project(tmp_path, (
            'label main:\n    call nowhere\n    return\n'))
        g = Graph()
        g.add_node('main', 0, 0)
        before = len(g.nodes)
        out = rebuild_source_edges(g, _beats(rpy))
        assert len(g.nodes) == before, '凭空造了一个节点'
        assert len(out['skipped']) == 1
        assert out['skipped'][0]['to'] == 'nowhere'


class TestThroughImportChapters:
    def test_import_reports_the_rebuild(self, tmp_path):
        """真实入口：`import_chapters` 之后边已经有 line_id 了。"""
        root, rpy = _project(tmp_path, (
            'label main:\n    e "a"\n    call side\n    call side\n'
            '    return\n'))
        sess = EditorSession()
        g = Graph()
        for name in ('main', 'side'):
            g.add_node(name, 0, 0)
        res = sess.import_chapters([rpy], g)
        assert res['ok'], res
        assert 'edge_rebuild' in res
        lines = [e.line_id for e in g.edges.values() if e.line_id]
        assert len(lines) >= 1, '导入之后边还是没有 line_id'
