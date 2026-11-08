# -*- coding: utf-8 -*-
"""
P2（2026-10-24）：章末接线写进**编排层**，章节源文件一个字不动。

核心不是"多一个落点"，而是**有 `_control` 包装时不能写源文件**：

    X_control: call X → X 里 jump Y → Y return → 回到 X_control
               → X_control 又 jump Y_control → Y 被跑第二遍

所以判据分三层：

  一、计划：`return` / `jump` / `call` / 块体 / 缺 label，各自落到哪；
  二、落盘：只改那一行，BOM/CRLF/别的 label 一个字节不动；
  三、图：写成功才加 `origin='control'` 的边；删线只删编排层那一行。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

sys.path.insert(0, str(Path(__file__).resolve().parent))

from storygraph.graph.models import Graph  # noqa: E402
from storygraph.story.control_route import (  # noqa: E402
    apply_control_route, apply_control_unroute, control_label,
    find_control_file, plan_control_route, plan_control_unroute)
from storygraph.ui_qt.graph_canvas import edge_label_text, edge_tooltip  # noqa: E402
from storygraph.ui_qt.window_nodes import NodeEditMixin  # noqa: E402

CONTROLLER = '''label ch01_control:
    call ch01
    return

label ch02_control:
    call ch02
    return

label ch03_control:
    call ch03
    return
'''


def _project(tmp_path, controller=CONTROLLER):
    root = tmp_path / 'proj'
    script = root / 'game' / 'script'
    script.mkdir(parents=True)
    for name in ('ch01', 'ch02', 'ch03'):
        (script / f'{name}.rpy').write_text(
            f'label {name}:\n    e "{name} 的台词"\n    return\n',
            encoding='utf-8')
    (script / 'chapter_controller.rpy').write_text(controller,
                                                   encoding='utf-8')
    return root


def _controller(root):
    return root / 'game' / 'script' / 'chapter_controller.rpy'


# ===========================================================================
#  一、计划：纯计算，不碰磁盘
# ===========================================================================

class TestPlan:
    def test_finds_the_dedicated_controller_by_content(self, tmp_path):
        root = _project(tmp_path)
        assert find_control_file(root) == _controller(root)

    def test_jump_replaces_return(self, tmp_path):
        root = _project(tmp_path)
        plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
        assert plan.ok is True, plan.blocking
        assert plan.kind == 'replace'
        assert plan.old_line.strip() == 'return'
        assert plan.new_lines == ['    jump ch02_control']
        assert 'return' in plan.reason

    def test_jump_replaces_an_existing_route_and_says_so(self, tmp_path):
        root = _project(tmp_path)
        plan = plan_control_route(root, 'ch01', 'ch03', 'jump')
        # 把 ch01 的 return 先改成 jump ch02_control，再来一次
        first = plan_control_route(root, 'ch01', 'ch02', 'jump')
        assert apply_control_route(first)['ok']
        again = plan_control_route(root, 'ch01', 'ch03', 'jump')
        assert again.ok is True
        assert again.kind == 'replace'
        assert '改接' in again.reason
        assert 'jump ch02_control' in again.old_route
        assert again.new_lines == ['    jump ch03_control']

    def test_the_same_route_is_already_done(self, tmp_path):
        root = _project(tmp_path)
        assert apply_control_route(
            plan_control_route(root, 'ch01', 'ch02', 'jump'))['ok']
        plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
        assert plan.ok is True and plan.kind == 'exists'
        assert plan.new_lines == []

    def test_call_lands_before_return_and_comes_back(self, tmp_path):
        root = _project(tmp_path)
        plan = plan_control_route(root, 'ch01', 'ch02', 'call')
        assert plan.ok is True, plan.blocking
        assert plan.kind == 'insert'
        assert plan.old_line.strip() == 'return'
        assert plan.new_lines == ['    call ch02']
        assert '返回后继续原路' in plan.reason

    def test_call_lands_before_an_existing_jump(self, tmp_path):
        root = _project(tmp_path)
        assert apply_control_route(
            plan_control_route(root, 'ch01', 'ch02', 'jump'))['ok']
        plan = plan_control_route(root, 'ch01', 'ch03', 'call')
        assert plan.ok is True
        assert plan.kind == 'insert'
        assert plan.old_line.strip() == 'jump ch02_control'
        assert plan.new_lines == ['    call ch03']

    def test_call_carries_from_when_given(self, tmp_path):
        root = _project(tmp_path)
        plan = plan_control_route(root, 'ch01', 'ch02', 'call',
                                  from_clause='_call_ch02_1')
        assert plan.new_lines == ['    call ch02 from _call_ch02_1']

    def test_call_is_refused_when_the_route_already_jumps_there(self, tmp_path):
        """已经 jump B_control 时再 call B，会把 B 跑两遍。"""
        root = _project(tmp_path)
        assert apply_control_route(
            plan_control_route(root, 'ch01', 'ch02', 'jump'))['ok']
        plan = plan_control_route(root, 'ch01', 'ch02', 'call')
        assert plan.ok is False
        assert '跑两遍' in plan.reason

    def test_call_already_there_is_a_noop(self, tmp_path):
        root = _project(tmp_path)
        assert apply_control_route(
            plan_control_route(root, 'ch01', 'ch02', 'call'))['ok']
        plan = plan_control_route(root, 'ch01', 'ch02', 'call')
        assert plan.ok is True and plan.kind == 'exists'

    def test_missing_source_control_label_is_blocked(self, tmp_path):
        root = _project(tmp_path, controller='label other_control:\n    return\n')
        plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
        assert plan.ok is False
        assert 'ch01_control' in plan.reason

    def test_missing_target_control_label_is_blocked(self, tmp_path):
        root = _project(tmp_path, controller=(
            'label ch01_control:\n    call ch01\n    return\n'))
        plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
        assert plan.ok is False
        assert 'ch02_control' in plan.reason
        assert '还没有' in plan.reason

    def test_menu_tail_is_not_touched(self, tmp_path):
        root = _project(tmp_path, controller=(
            'label ch01_control:\n    call ch01\n    menu:\n'
            '        "走":\n            jump ch02_control\n'
            'label ch02_control:\n    return\n'))
        plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
        assert plan.ok is False
        assert '分支归控制流图/卡片' in plan.reason

    def test_dynamic_jump_is_not_touched(self, tmp_path):
        root = _project(tmp_path, controller=(
            'label ch01_control:\n    call ch01\n'
            '    jump expression target\n'
            'label ch02_control:\n    return\n'))
        plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
        assert plan.ok is False
        assert '动态跳转' in plan.reason

    def test_empty_project_root_does_not_scan_the_current_directory(self):
        """`Path('')` 是 `.`：空根曾把整个仓库 rglob 一遍，界面卡死。"""
        assert find_control_file(None) is None
        assert find_control_file('') is None

    def test_no_controller_is_not_an_error(self, tmp_path):
        root = tmp_path / 'plain'
        (root / 'game').mkdir(parents=True)
        (root / 'game' / 'script.rpy').write_text(
            'label start:\n    e "hi"\n    return\n', encoding='utf-8')
        assert find_control_file(root) is None
        plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
        assert plan.ok is False and plan.path == ''

    def test_control_label_is_a_plain_suffix(self):
        assert control_label('ch01') == 'ch01_control'
        assert control_label('') == ''


# ===========================================================================
#  二、落盘：只改那一行
# ===========================================================================

class TestApply:
    def test_writes_exactly_one_line(self, tmp_path):
        root = _project(tmp_path)
        path = _controller(root)
        before = path.read_bytes()
        plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
        res = apply_control_route(plan)
        assert res['ok'] is True, res
        after = path.read_text(encoding='utf-8')
        assert '    jump ch02_control' in after
        # 只动了 ch01_control 的最后一行；ch02/ch03 原样
        assert after == (
            'label ch01_control:\n    call ch01\n    jump ch02_control\n\n'
            'label ch02_control:\n    call ch02\n    return\n\n'
            'label ch03_control:\n    call ch03\n    return\n')
        assert before != path.read_bytes()

    def test_preserves_bom_and_crlf(self, tmp_path):
        root = _project(tmp_path)
        path = _controller(root)
        path.write_bytes(b'\xef\xbb\xbf' + CONTROLLER.replace('\n', '\r\n')
                         .encode('utf-8'))
        plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
        assert apply_control_route(plan)['ok']
        raw = path.read_bytes()
        assert raw.startswith(b'\xef\xbb\xbf')
        assert b'\r\n' in raw
        assert b'\n    jump ch02_control\r\n' in raw

    def test_stale_anchor_writes_nothing(self, tmp_path):
        """
        锚点那一行**本身**被改过 → 拒写、逐字节不动。

        ⚠ 2026-11-08（第十四轮）判据校准：这一条从前喂的是"在锚点上面插
        一行"（行号漂了）。现在那种局面**能认回来**（下一条
        `test_anchor_shifted_down_is_found_again`），所以这里换成真的改坏
        锚点内容 —— "认不出就不动"那一半必须还在。
        """
        root = _project(tmp_path)
        path = _controller(root)
        plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
        path.write_text(CONTROLLER.replace('    return\n',
                                           '    jump ch09_control\n', 1),
                        encoding='utf-8')
        before = path.read_bytes()
        res = apply_control_route(plan)
        assert res['ok'] is False
        assert '不在了' in res['error'], res['error']
        assert path.read_bytes() == before

    def test_anchor_shifted_down_is_found_again(self, tmp_path):
        """
        别人在锚点**上面**插了一行 → 按内容把它认回来，他那一行留着。

        这是第十四轮的正题：`return` 在每个 `_control` 里都有一条，所以只
        在**它自己那个 label 块**里认（`_label_block`）；认不出才拒写。
        """
        root = _project(tmp_path)
        path = _controller(root)
        plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
        path.write_text(CONTROLLER.replace('    call ch01\n',
                                           '    call ch01\n    $ x = 1\n'),
                        encoding='utf-8')
        res = apply_control_route(plan)
        assert res['ok'] is True, res
        after = path.read_text(encoding='utf-8')
        assert '    $ x = 1\n' in after, '别人插的那一行被吞了'
        assert ('label ch01_control:\n    call ch01\n    $ x = 1\n'
                '    jump ch02_control\n') in after
        assert after.count('    call ch01\n') == 1, '别把锚点认到别的 label 里'

    def test_undo_restores_the_exact_old_text(self, tmp_path):
        root = _project(tmp_path)
        path = _controller(root)
        before = path.read_text(encoding='utf-8')
        res = apply_control_route(plan_control_route(root, 'ch01', 'ch02',
                                                     'jump'))
        assert res['ok']
        from storygraph.story.paths import write_text_atomic
        write_text_atomic(path, res['old_text'])
        assert path.read_text(encoding='utf-8') == before


# ===========================================================================
#  三、删编排线：只删一行，必要时补 return
# ===========================================================================

class TestUnroute:
    def test_removes_a_jump_and_leaves_a_return(self, tmp_path):
        root = _project(tmp_path)
        assert apply_control_route(
            plan_control_route(root, 'ch01', 'ch02', 'jump'))['ok']
        plan = plan_control_unroute(root, 'ch01', 'ch02', 'jump')
        assert plan.ok is True, plan.blocking
        assert plan.old_route == 'jump ch02_control'
        assert plan.new_lines == ['    return']
        assert apply_control_unroute(plan)['ok']
        text = _controller(root).read_text(encoding='utf-8')
        assert 'jump ch02_control' not in text
        assert 'label ch01_control:\n    call ch01\n    return' in text

    def test_removing_a_call_keeps_the_next_route(self, tmp_path):
        root = _project(tmp_path)
        assert apply_control_route(
            plan_control_route(root, 'ch01', 'ch02', 'jump'))['ok']
        assert apply_control_route(
            plan_control_route(root, 'ch01', 'ch03', 'call'))['ok']
        plan = plan_control_unroute(root, 'ch01', 'ch03', 'call')
        assert plan.ok is True
        assert apply_control_unroute(plan)['ok']
        text = _controller(root).read_text(encoding='utf-8')
        ch01 = text.split('label ch02_control')[0]
        assert 'call ch03' not in ch01
        assert 'jump ch02_control' in ch01

    def test_missing_route_is_reported(self, tmp_path):
        root = _project(tmp_path)
        plan = plan_control_unroute(root, 'ch01', 'ch03', 'jump')
        assert plan.ok is False
        assert '找不到' in plan.reason


# ===========================================================================
#  四、窗口层：计划 → 写编排层 → 加控制边
# ===========================================================================

class _Host:
    def __init__(self, root, graph):
        self._project_root = root
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

    def _run_command(self, cmd):
        cmd.execute()
        return True

    def on_graph_structure_changed(self):
        pass

    def _refresh_flow_edges(self):
        return {}


_Host._control_route_plan = NodeEditMixin._control_route_plan
_Host.plan_wire_landing = NodeEditMixin.plan_wire_landing
_Host._apply_wire = NodeEditMixin._apply_wire
_Host._apply_control_route = NodeEditMixin._apply_control_route
_Host.edge_delete_plan = NodeEditMixin.edge_delete_plan
_Host._edge_archive_note = NodeEditMixin._edge_archive_note
_Host.delete_edge = NodeEditMixin.delete_edge
_Host._confirm_and_delete_control_edge = \
    NodeEditMixin._confirm_and_delete_control_edge


def _graph_with_two():
    g = Graph()
    a = g.add_node('ch01', 0, 0)
    b = g.add_node('ch02', 100, 0)
    return g, a, b


def test_plan_wire_landing_prefers_the_control_layer(tmp_path):
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    assert plan['ok'] is True and plan['kind'] == 'control', plan
    assert plan['control_plan'].new_lines == ['    jump ch02_control']
    assert '编排层' in plan['reason'] or 'jump ch02_control' in plan['reason']


def test_applying_a_control_route_writes_file_and_adds_a_control_edge(tmp_path):
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    res = host._apply_wire(plan)
    assert res.get('ok') is True, res
    text = _controller(root).read_text(encoding='utf-8')
    assert '    jump ch02_control' in text
    edge = next(e for e in g.edges.values() if e.origin == 'control')
    assert edge.from_id == a and edge.to_id == b
    assert edge.position == 'control_end'
    assert 'jump ch02_control' not in (root / 'game/script/ch01.rpy') \
        .read_text(encoding='utf-8'), '章节源文件不许被改'
    assert any('编排层' in s for s in host.status), host.status


def test_control_edge_label_and_tooltip_do_not_call_it_a_sketch(tmp_path):
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    host._apply_wire(host.plan_wire_landing(a, b))
    edge = next(e for e in g.edges.values() if e.origin == 'control')
    label = edge_label_text(edge)
    tip = edge_tooltip(edge)
    assert label == '', '线外只留条件/菜单项；编排层落点在窗口/提示里'
    assert '编排层' in tip and '草图' not in tip


def test_control_edge_plan_can_be_deleted_without_touching_the_chapter(tmp_path):
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    assert host._apply_wire(host.plan_wire_landing(a, b)).get('ok')
    edge = next(e for e in g.edges.values() if e.origin == 'control')
    plan = host.edge_delete_plan(edge)
    assert plan['ok'] is True and plan['kind'] == 'control', plan
    assert 'jump ch02_control' in plan['text']
    assert (root / 'game/script/ch01.rpy').read_text(encoding='utf-8') \
        .count('jump') == 0


def test_control_command_undo_restores_the_file_and_removes_the_edge(tmp_path):
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    before = _controller(root).read_text(encoding='utf-8')
    plan = host.plan_wire_landing(a, b)
    cplan = plan['control_plan']
    from storygraph.graph.commands import ControlRouteCommand
    cmd = ControlRouteCommand(host, cplan, from_id=a, to_id=b, etype='jump')
    cmd.execute()
    assert 'jump ch02_control' in _controller(root).read_text(encoding='utf-8')
    cmd.undo()
    assert _controller(root).read_text(encoding='utf-8') == before
    assert all(e.origin != 'control' for e in g.edges.values())


def test_control_command_redo_puts_the_same_route_back(tmp_path):
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    from storygraph.graph.commands import ControlRouteCommand
    cmd = ControlRouteCommand(host, plan['control_plan'], from_id=a, to_id=b,
                              etype='jump')
    cmd.execute()
    cmd.undo()
    cmd.execute()
    assert 'jump ch02_control' in _controller(root).read_text(encoding='utf-8')
    assert any(e.origin == 'control' for e in g.edges.values())


def test_export_control_flow_regenerates_a_call_route(tmp_path):
    """
    编排层的 call 边导出时必须是 `call 章节本身`（不是 `call B_control`），
    而且 call 返回后要自己 `return`，不许掉进下一个 label。
    """
    root = _project(tmp_path)
    from storygraph.app.editor import EditorSession
    session = EditorSession(project_root=root)
    g = Graph()
    a = g.add_node('ch01', 0, 0)
    b = g.add_node('ch02', 0, 0)
    c = g.add_node('ch03', 0, 0)
    g.add_edge(a, b, 'call', origin='control', position='control_end')
    target = tmp_path / 'all.rpy'   # 不落在 game/ 里，避免与现有 controller 重名
    res = session.export_control_flow(g, target)
    assert res['ok'] is True, res.get('error')
    text = target.read_text(encoding='utf-8')
    block = text.split('label ch01_control:')[1].split('label ch02_control:')[0]
    assert '    call ch01\n' in block
    assert '    call ch02\n' in block
    assert '    return\n' in block
    assert 'call ch02_control' not in block


def test_control_edge_survives_a_layout_roundtrip(tmp_path):
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    assert host._apply_wire(host.plan_wire_landing(a, b)).get('ok')

    from storygraph.app.session_graph import (graph_from_layout_dict,
                                              layout_dict)
    again = graph_from_layout_dict(layout_dict(g))
    edge = next(e for e in again.edges.values() if e.origin == 'control')
    assert edge.position == 'control_end'
    assert edge.from_id == a and edge.to_id == b
    # 重新加载之后，删线仍能找到编排层那一行
    host2 = _Host(root, again)
    plan = host2.edge_delete_plan(edge)
    assert plan['ok'] is True and plan['kind'] == 'control', plan


def test_control_edge_is_in_the_thin_layout(tmp_path):
    """瘦版式也要存编排线：它不在章节源文件里，不存就会从图上消失。"""
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    assert host._apply_wire(host.plan_wire_landing(a, b)).get('ok')

    from storygraph.app.layout_store import apply_placement, placement_dict
    data = placement_dict(g, root)
    assert any(e.get('origin') == 'control' for e in data['edges'])
    again = Graph()
    na = again.add_node('ch01', 0, 0)
    nb = again.add_node('ch02', 0, 0)
    res = apply_placement(again, data, root)
    assert res['ok'] and res['edges_added'] == 1
    edge = next(e for e in again.edges.values() if e.origin == 'control')
    assert edge.position == 'control_end'
    assert edge.from_id == na and edge.to_id == nb


def test_control_edge_delete_uses_the_command(tmp_path, monkeypatch):
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    assert host._apply_wire(host.plan_wire_landing(a, b)).get('ok')
    edge = next(e for e in g.edges.values() if e.origin == 'control')

    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    host.delete_edge(edge.id)

    text = _controller(root).read_text(encoding='utf-8')
    assert 'jump ch02_control' not in text
    assert all(e.origin != 'control' for e in g.edges.values())
