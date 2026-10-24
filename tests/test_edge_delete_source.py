# -*- coding: utf-8 -*-
"""
S1：删边 = 删剧本里那一行。

这是本项目**第一次让"图上的操作"真的改剧本**，所以判据要比别处狠：

  一、计划（纯查询，不改状态）
     · 草图 → 只删图；源边 → 删拍
     · `call ... from` 的存档提示要**点名那个返回点**
     · `jump` **不许**提示存档（假警报一多，真警报没人看）
     · 找不到拍 / 只剩一拍 → 拒绝，且**一个字都不改**

  二、执行（真的动内存里的章节）
     · 删完那一拍真没了，**别的拍一个字都没动**
     · 图上的 line 跟着没了（以前只删投影，下次派生又长回来）
     · Ctrl+Z 能把拍**逐字节**放回来

  三、边界
     · 草图边不碰剧本（md5 不变）
     · 打不开 / 删不掉时状态栏要说话，不许静默
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')

from PySide6.QtWidgets import QMessageBox  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project  # noqa: E402

from storygraph.app.editor import EditorSession  # noqa: E402
from storygraph.graph.models import Graph  # noqa: E402
from storygraph.ui_qt.window_card import CardPageMixin  # noqa: E402
from storygraph.ui_qt.window_nodes import NodeEditMixin  # noqa: E402


SCRIPT = '''# 删边测试用
label main:
    e "开始"
    call side from _call_side_1
    e "回来之后"
    jump outro

label side:
    e "支线内容"
    return

label outro:
    e "结尾"
    return
'''


# ---------------------------------------------------------------------------
#  最小宿主：只绑"删边"用到的那几个方法，不建真窗口
# ---------------------------------------------------------------------------

class _Host:
    """够 `delete_edge` 跑完的最小上下文（不建 Qt 窗口，省几秒）。"""

    def __init__(self, session, graph):
        self._session = session
        self.graph = graph
        self.status: list = []
        self.logs: list = []
        self.renders = 0

    def _status(self, msg):
        self.status.append(str(msg))

    def _log(self, msg):
        self.logs.append(str(msg))

    def _set_graph(self, graph, keep_view=None):
        self.graph = graph
        self.renders += 1

    def _delete_line_note(self, preview):
        return ''

    def _run_command(self, cmd):
        cmd.execute()

    def on_graph_structure_changed(self):
        pass


_Host.edge_delete_plan = NodeEditMixin.edge_delete_plan
_Host._edge_archive_note = NodeEditMixin._edge_archive_note
_Host.delete_edge = NodeEditMixin.delete_edge
_Host._confirm_and_delete_source_edge = NodeEditMixin._confirm_and_delete_source_edge
_Host._refresh_flow_edges = CardPageMixin._refresh_flow_edges


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def project(tmp_path):
    root = build_project(tmp_path / 'proj')
    (root / 'game' / 'script' / 'edges.rpy').write_text(SCRIPT, encoding='utf-8')
    return root


def _open(project):
    """载入一章 → `(session, graph, host, script_path)`。"""
    script = project / 'game' / 'script' / 'edges.rpy'
    session = EditorSession(project_root=project)
    res = session.import_chapters([script], Graph())
    assert res['ok'], res.get('error')
    graph = res['graph']
    # `import_chapters` 只建图；**拍要单独载入**（删边是按拍定位的）
    session.load(script)
    assert session.beats, '载入之后应该有拍'
    return session, graph, _Host(session, graph), script


def _edge_of(graph, etype='call', origin='source'):
    for edge in graph.edges.values():
        if edge.type == etype and edge.origin == origin:
            return edge
    raise AssertionError(f'图上没有 {etype}/{origin} 边：'
                         f'{[(e.type, e.origin) for e in graph.edges.values()]}')


def _snapshot(beats) -> list:
    """
    拍的**逐字段快照**（用来判"别的拍一个字都没动"）。

    不用 `to_dict`（`Beat` 没有这个方法），也不用 `to_row`（那会带上表头语义）。
    这里只取"会进产物"的字段：身份、种类、原文、台词、说话人、条件。
    """
    return [(str(getattr(b, 'line_id', '') or ''),
             str(getattr(b, 'kind', '') or ''),
             str(getattr(b, 'raw', '') or ''),
             str(getattr(b, 'text', '') or ''),
             str(getattr(b, 'speaker', '') or ''),
             str(getattr(b, 'condition', '') or ''))
            for b in beats]


def _say_preview(messages):
    """确认框里到底写了什么（打桩时记下来）。"""
    return '\n'.join(messages)


@pytest.fixture
def yes(monkeypatch):
    seen: list = []

    def _q(*args, **kwargs):
        seen.append(' '.join(str(a) for a in args[1:] if isinstance(a, str)))
        return QMessageBox.Yes

    for name in ('question', 'warning', 'information'):
        monkeypatch.setattr(QMessageBox, name, staticmethod(_q))
    return seen


# ===========================================================================
#  一、计划：纯查询，不改任何状态
# ===========================================================================

class TestPlan:
    def test_source_edge_points_at_its_beat(self, app, project):
        session, graph, host, _ = _open(project)
        edge = _edge_of(graph, 'call')
        plan = host.edge_delete_plan(edge)

        assert plan['ok'] is True and plan['kind'] == 'source', plan
        assert plan['total'] == len(session.beats)
        beat = session.beats[plan['index']]
        assert beat.line_id == edge.line_id
        assert 'call side' in plan['text']

    def test_plan_does_not_change_anything(self, app, project):
        session, graph, host, _ = _open(project)
        before = _snapshot(session.beats)
        edge = _edge_of(graph, 'call')

        host.edge_delete_plan(edge)

        assert _snapshot(session.beats) == before

    def test_manual_edge_is_a_sketch(self, app, project):
        session, graph, host, _ = _open(project)
        nid_a, nid_b = list(graph.nodes)[:2]
        eid = graph.add_edge(nid_a, nid_b, etype='jump', origin='manual')
        plan = host.edge_delete_plan(graph.edges[eid])
        assert plan['kind'] == 'manual'
        assert plan['index'] == -1

    def test_edge_with_unknown_beat_is_refused(self, app, project):
        session, graph, host, _ = _open(project)
        edge = _edge_of(graph, 'call')
        edge.line_id = 'main.9999'
        plan = host.edge_delete_plan(edge)
        assert plan['ok'] is False
        assert '不在这章里' in plan['error']

    def test_call_with_from_names_the_save_point(self, app, project):
        """这是三层边界第①层：删之前必须说清会破坏什么。"""
        session, graph, host, _ = _open(project)
        plan = host.edge_delete_plan(_edge_of(graph, 'call'))
        note = plan['archive_note']
        assert '_call_side_1' in note, note
        assert '回不到原处' in note

    def test_call_without_from_says_it_may_break(self, app, project):
        session, graph, host, _ = _open(project)
        edge = _edge_of(graph, 'call')
        edge.from_clause = ''
        # 让计划读到没有 from 的那一拍
        beat = session.beats[1]
        beat.raw = 'call side'
        plan = host.edge_delete_plan(edge)
        assert '可能回不到' in plan['archive_note']

    def test_jump_has_no_save_warning(self, app, project):
        """D2：jump 没有返回点身份 —— 提示"存档会坏"是假警报。"""
        session, graph, host, _ = _open(project)
        plan = host.edge_delete_plan(_edge_of(graph, 'jump'))
        assert plan['archive_note'] == ''


# ===========================================================================
#  二、执行：真的动章节
# ===========================================================================

class TestApply:
    def test_deleting_a_call_edge_removes_exactly_that_beat(self, app, project,
                                                            yes):
        session, graph, host, _ = _open(project)
        before = _snapshot(session.beats)
        edge = _edge_of(graph, 'call')
        target_id = edge.line_id

        host.delete_edge(edge.id)

        after = _snapshot(session.beats)
        assert len(after) == len(before) - 1
        assert target_id not in [x[0] for x in after]
        # **别的拍一个字都没动**：逐字段、按顺序完全相等
        assert after == [x for x in before if x[0] != target_id]

    def test_deleting_a_jump_edge_removes_exactly_that_beat(self, app, project,
                                                            yes):
        session, graph, host, _ = _open(project)
        before = _snapshot(session.beats)
        target_id = _edge_of(graph, 'jump').line_id

        host.delete_edge(_edge_of(graph, 'jump').id)

        after = _snapshot(session.beats)
        assert len(after) == len(before) - 1
        assert target_id not in [x[0] for x in after]
        assert after == [x for x in before if x[0] != target_id]

    def test_edge_is_gone_after_delete_and_does_not_come_back(self, app, project,
                                                              yes):
        """
        以前的老毛病：只删投影 → 下次 `flow_edges` 从拍派生，线又长回来。
        现在删的是本体 —— 派生一遍也不该再出现。
        """
        session, graph, host, _ = _open(project)
        target_id = _edge_of(graph, 'call').line_id
        host.delete_edge(_edge_of(graph, 'call').id)

        assert all(e.line_id != target_id for e in host.graph.edges.values())

        # 再派生一次（模拟刷新），线仍然不该回来
        from storygraph.app.session_graph import rebuild_source_edges
        rebuild_source_edges(host.graph, session.beats)
        assert all(getattr(e, 'line_id', '') != target_id
                   for e in host.graph.edges.values())

    def test_undo_restores_the_beat_byte_for_byte(self, app, project, yes):
        session, graph, host, _ = _open(project)
        before = _snapshot(session.beats)

        host.delete_edge(_edge_of(graph, 'call').id)
        assert len(session.beats) == len(before) - 1

        undo = getattr(session, 'undo_structural', None)
        assert callable(undo), '删拍必须进结构撤销栈'
        res = undo()
        assert res.get('ok', True), res

        after = _snapshot(session.beats)
        assert after == before, '撤销必须逐字段还原（身份、原文、台词、条件）'

    def test_manual_edge_delete_never_touches_the_script(self, app, project,
                                                         yes):
        session, graph, host, script = _open(project)
        md5_before = script.read_bytes()
        beats_before = _snapshot(session.beats)

        nid_a, nid_b = list(graph.nodes)[:2]
        eid = graph.add_edge(nid_a, nid_b, etype='jump', origin='manual')
        host.delete_edge(eid)

        assert script.read_bytes() == md5_before, '草图不该碰剧本文件'
        assert _snapshot(session.beats) == beats_before
        assert eid not in host.graph.edges


    def test_export_after_delete_drops_exactly_that_line(self, app, project,
                                                         yes, tmp_path):
        """
        落盘验收：删前导一份、删后再导一份，**产物只少那一行**。

        为什么用"导出到新路径"而不是写回原文件：原文件是手写的，
        门禁（`HANDWRITTEN_TARGET`）会拦全量覆盖 —— 那是正确行为，
        不该为了测试把它绕过去。这里验的是"删掉的拍没进产物"。
        """
        session, graph, host, _ = _open(project)

        base = session.export(tmp_path / 'base.rpy')
        assert base['ok'], base
        base_lines = (tmp_path / 'base.rpy').read_text(encoding='utf-8').splitlines()
        assert any('call side' in ln for ln in base_lines), '基线里应该有这行'

        host.delete_edge(_edge_of(graph, 'call').id)

        after = session.export(tmp_path / 'after.rpy')
        assert after['ok'], after
        after_lines = (tmp_path / 'after.rpy').read_text(encoding='utf-8').splitlines()

        gone = [ln for ln in base_lines if ln not in set(after_lines)]
        assert len(gone) == 1, f'只该少一行，实际少了 {gone}'
        assert 'call side' in gone[0]
        # 其余台词一句没少（这是"删一拍不牵连别人"的最后一道证据）
        for keep in ('开始', '回来之后', '支线内容', '结尾'):
            assert any(keep in ln for ln in after_lines), f'{keep} 被牵连删掉了'


# ===========================================================================
#  三、拒绝与反馈：不许静默
# ===========================================================================

class TestRefusal:
    def test_refused_when_beat_is_gone(self, app, project, yes):
        session, graph, host, _ = _open(project)
        edge = _edge_of(graph, 'call')
        edge.line_id = 'main.9999'
        beats_before = _snapshot(session.beats)

        host.delete_edge(edge.id)

        assert _snapshot(session.beats) == beats_before
        assert any('不在这章里' in s for s in host.status), host.status

    def test_cancel_keeps_everything(self, app, project, monkeypatch):
        session, graph, host, _ = _open(project)
        beats_before = _snapshot(session.beats)
        monkeypatch.setattr(QMessageBox, 'question',
                            staticmethod(lambda *a, **k: QMessageBox.No))

        host.delete_edge(_edge_of(graph, 'call').id)

        assert _snapshot(session.beats) == beats_before
        assert _edge_of(graph, 'call') is not None

    def test_dialog_says_which_beat_and_which_save_point(self, app, project, yes):
        session, graph, host, _ = _open(project)
        host.delete_edge(_edge_of(graph, 'call').id)
        text = _say_preview(yes)
        assert '第 2 拍' in text
        assert '_call_side_1' in text
        assert 'Ctrl+Z' in text

    def test_refusal_from_preview_is_surfaced_without_a_dialog(self, app,
                                                               project, yes,
                                                               monkeypatch):
        """
        删不了的时候：**不弹确认框**（别让用户确认完才被告知删不了），
        状态栏要说原因，章节一个字节都不动。
        """
        session, graph, host, _ = _open(project)
        before = _snapshot(session.beats)
        monkeypatch.setattr(
            session, 'delete_preview',
            lambda *a, **k: {'ok': False, 'can_cascade': False,
                             'error': '一章至少要留一拍'})

        host.delete_edge(_edge_of(graph, 'call').id)

        assert _snapshot(session.beats) == before
        assert any('至少要留一拍' in s for s in host.status), host.status
        assert not yes, '删不了就不该弹确认框'
