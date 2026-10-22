"""
tests/test_flow_edges_rebuild.py
================================
第二阶段 B：**图边从 flow 拍派生**（`rebuild_source_edges`）。

第一阶段的图边是"解析时顺手建的"，改卡片里的 target 靠 `_sync_flow_edge`
**手动**改那一条 —— 两份真源，靠约定同步。第三阶段已把它拆掉，统一收成
`flow_edges(beats)` 这一份真源，导入 / 保存 / 插删卡之后都按它对齐
图上的 `source` 边。

判据（都看 `graph.edges` 的实际内容）：

  A. **两次 call 同一目标 = 两条边**，各带自己的 `line_id`
     （老做法按 `from+type+to` 找第一条，那种场景会指错 —— 待办 #4）；
  B. **幂等**：同一份源导入两次，边数不变（不重复画）；
  C. **改目标后重新导入 → 边改指**，而不是"新旧两条"；
  D. **不碰 `manual`**：用户画的草图一条都不动；
  E. **不碰菜单选项的边**（`option_text` 非空）—— 那不是纯 call/jump；
  F. **目标不在图上 → 跳过并记在 `skipped`**（不硬造幽灵节点）；
  G. **源里删掉一个 call → 边消失**（只删认领过、带 line_id 的派生边）；
  H. **插 / 删卡后 line_id 重排 → 就地重认，不重复画边**。

P2-1「重载删幽灵」也在这里验：源文件里删掉的 label 节点，以及它带走的边，
重载后不留在图上。解析有错时**不删**（见 `allow_remove`），本文件用纯数据
层钉住这条边界。
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


class TestDeleteAndReidentify:
    def test_deleting_the_flow_beat_removes_its_source_edge(self, tmp_path):
        """源里删掉 `call side` → 重载后那条边消失，不是留在图上当幽灵。"""
        root, rpy = _project(tmp_path, (
            'label main:\n    e "a"\n    call side\n    return\n'
            'label side:\n    e "s"\n    return\n'))
        g = Graph()
        for name in ('main', 'side'):
            g.add_node(name, 0, 0)
        beats = _beats(rpy)
        rebuild_source_edges(g, beats)
        assert len(g.edges) == 1

        beats = [b for b in beats if b.raw != 'call side']
        out = rebuild_source_edges(g, beats)

        assert out['removed'] == 1, out
        assert not g.edges, [(e.from_id, e.to_id, e.line_id) for e in g.edges.values()]

    def test_removed_line_id_is_reidentified_not_duplicated(self, tmp_path):
        """插 / 删卡后 line_id 整体重排：旧边要改挂新 ID，不能新旧各一条。"""
        root, rpy = _project(tmp_path, 'label main:\n    call side\n    return\n')
        g = Graph()
        for name in ('main', 'side'):
            g.add_node(name, 0, 0)
        beats = _beats(rpy)
        rebuild_source_edges(g, beats)
        edge = next(iter(g.edges.values()))
        old_line = edge.line_id
        assert old_line

        # 模拟"在第 1 拍前插了一张卡"：拍号整体后移，line_id 变成新值
        beats[0].line_id = 'main.0099'
        out = rebuild_source_edges(g, beats)

        assert out['added'] == 0, out
        assert out['identified'] == 1, out
        assert len(g.edges) == 1, '重排之后长出了第二条边'
        assert edge.line_id == 'main.0099'

    def test_deleting_an_unrelated_beat_keeps_the_manual_edge(self, tmp_path):
        root, rpy = _project(tmp_path, (
            'label main:\n    e "a"\n    call side\n    return\n'))
        g = Graph()
        for name in ('main', 'side', 'draft'):
            g.add_node(name, 0, 0)
        mid = {n.label_name: nid for nid, n in g.nodes.items()}
        g.add_edge(mid['main'], mid['draft'], 'jump', origin='manual')
        beats = _beats(rpy)
        rebuild_source_edges(g, beats)

        beats = [b for b in beats if b.raw != 'call side']
        rebuild_source_edges(g, beats)

        manual = [e for e in g.edges.values() if e.origin == 'manual']
        assert len(manual) == 1, '删源边时把用户画的草图也删了'
        assert manual[0].to_id == mid['draft']

    def test_parse_error_path_can_keep_the_stale_edge(self, tmp_path):
        """`allow_remove=False`：解析有错时只增改，旧边一条都不删。"""
        root, rpy = _project(tmp_path, 'label main:\n    call side\n    return\n')
        g = Graph()
        for name in ('main', 'side'):
            g.add_node(name, 0, 0)
        rebuild_source_edges(g, _beats(rpy))
        assert len(g.edges) == 1

        from storygraph.story.beat_model import Beat
        # 这份"解析结果"只看到 main、没有 call（像半份 AST）——旧边必须留着
        out = rebuild_source_edges(
            g, [Beat(seq=1, label='main')], allow_remove=False)

        assert out['removed'] == 0, out
        assert out['unmatched'] == 1, out
        assert len(g.edges) == 1, '解析出错时把旧边删了'


class TestReloadRemovesGhosts:
    def _project(self, tmp_path, body):
        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True)
        rpy = root / 'game' / 'a.rpy'
        rpy.write_text(body, encoding='utf-8')
        return root, rpy

    def _names(self, graph):
        return {n.label_name for n in (graph.nodes or {}).values()}

    def test_reload_removes_a_label_deleted_from_source(self, tmp_path):
        root, rpy = self._project(tmp_path, (
            'label main:\n    e "a"\n    return\n'
            'label side:\n    e "s"\n    return\n'))
        sess = EditorSession(project_root=root)
        g = Graph()
        sess.import_chapters([rpy], g)
        assert {'main', 'side'} <= self._names(g)

        rpy.write_text('label main:\n    e "a"\n    return\n', encoding='utf-8')
        res = sess.import_chapters([rpy], g)

        assert res['removed_nodes'] == ['side'], res
        assert 'side' not in self._names(g), '源里已经删掉的 label 还留在图上'
        assert not any(
            'side' in (e.from_id, e.to_id) for e in g.edges.values())

    def test_reload_removes_edges_of_a_deleted_call(self, tmp_path):
        root, rpy = self._project(tmp_path, (
            'label main:\n    e "a"\n    call side\n    return\n'
            'label side:\n    e "s"\n    return\n'))
        sess = EditorSession(project_root=root)
        g = Graph()
        sess.import_chapters([rpy], g)
        assert any(e.type == 'call' for e in g.edges.values())

        rpy.write_text((
            'label main:\n    e "a"\n    return\n'
            'label side:\n    e "s"\n    return\n'), encoding='utf-8')
        res = sess.import_chapters([rpy], g)

        assert res['edge_rebuild']['removed'] == 1, res
        assert not any(e.type == 'call' for e in g.edges.values()), \
            '源里删掉的 call 还在图上留着边'

    def test_reload_removes_a_deleted_menu_option_edge(self, tmp_path):
        """菜单选项里的 `jump` 也是一条源边：删掉选项，边跟着消失。"""
        src = (
            'label main:\n    e "a"\n    menu:\n'
            '        "去屋顶":\n            jump roof\n'
            '        "留下":\n            jump stay\n\n'
            'label roof:\n    e "r"\n    return\n\n'
            'label stay:\n    e "s"\n    return\n')
        root, rpy = self._project(tmp_path, src)
        sess = EditorSession(project_root=root)
        g = Graph()
        sess.import_chapters([rpy], g)
        menu = [e for e in g.edges.values() if e.option_text]
        assert len(menu) == 2, [(e.option_text, e.line_id) for e in menu]

        rpy.write_text(src.replace(
            '        "留下":\n            jump stay\n', ''), encoding='utf-8')
        res = sess.import_chapters([rpy], g)

        assert res['edge_rebuild']['removed'] == 1, res
        left = [e.option_text for e in g.edges.values() if e.option_text]
        assert left == ['去屋顶'], left

    def test_reload_with_parse_error_keeps_ghosts(self, tmp_path):
        """源没读全时宁可不删：少一个 label 可能只是这次解析没读到。"""
        root, rpy = self._project(tmp_path, (
            'label main:\n    e "a"\n    return\n'
            'label side:\n    e "s"\n    return\n'))
        sess = EditorSession(project_root=root)
        g = Graph()
        sess.import_chapters([rpy], g)

        rpy.write_text((
            'label main:\n    e "a"\n    return\n'
            'label broken\n    return\n'), encoding='utf-8')
        res = sess.import_chapters([rpy], g)

        assert res['errors'], '这个用例需要一次真的解析错误'
        assert res['removed_nodes'] == [], '解析出错时删了旧节点'
        assert 'side' in self._names(g), '解析出错时把没读到的 label 当删除了'

    def test_reload_keeps_other_files_edges(self, tmp_path):
        root, rpy = self._project(tmp_path, (
            'label main:\n    e "a"\n    call side\n    return\n'
            'label side:\n    e "s"\n    return\n'))
        other = root / 'game' / 'b.rpy'
        other.write_text('label other:\n    e "o"\n    jump side\n    return\n',
                         encoding='utf-8')
        sess = EditorSession(project_root=root)
        g = Graph()
        sess.import_chapters([rpy, other], g)
        assert len(g.edges) == 2

        rpy.write_text((
            'label main:\n    e "a"\n    return\n'
            'label side:\n    e "s"\n    return\n'), encoding='utf-8')
        sess.import_chapters([rpy], g)

        rows = [(n.label_name, e.type) for e in g.edges.values()
                for n in g.nodes.values() if n.id == e.from_id]
        assert ('other', 'jump') in rows, '重载 a.rpy 误删了 b.rpy 的边'


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
