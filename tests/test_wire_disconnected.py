# -*- coding: utf-8 -*-
"""
「接一条线」= **真的写进剧本**，不管这一章现在通不通得过去。

2026-10-27 真机（用户原话，连问两次）：

    1. "2_4 到 2_4_1，我明明没有勾选草图！为什么还是只能画草图？？？"
    2. "总之我要 45 章全部都能连起来（就算是断裂的局部也能不画草图直接连），
       不需要你替我连，但你要能做到这个效果！草图想什么时候用就什么时候用
       ……哪怕它是断掉的！它也能够直接连"

根因在 `plan_control_init(role='source')`：源章没有 `chapter2_4_control`
（因为**没有任何地方**进入 `chapter2_4`）时，它一律拒绝 —— 理由是"补出来
是死代码"。于是：

    · 图上孤立的章节永远接不出线，只剩草图；
    · 而人写剧本根本不是拓扑序：先把这一段的岔路搭好，最后才回去接入口。

现在的判据：**能写就写**。到达不到由 `reachable` / `notes` 如实说，
但绝不因为"暂时到不了"就降级成草图。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

sys.path.insert(0, str(Path(__file__).resolve().parent))

from storygraph.graph.commands import ControlRouteCommand  # noqa: E402
from storygraph.graph.models import Graph  # noqa: E402
from storygraph.story.control_route import (  # noqa: E402
    plan_control_init, plan_control_route, wire_plan_writable)
from storygraph.ui_qt.dialogs import AddEdgeDialog  # noqa: E402
from storygraph.ui_qt.window_nodes import NodeEditMixin  # noqa: E402

#: 只有 `ch99` 有包装；`ch01`/`ch02`/……都还没有（真机项目的常态）
CONTROLLER = 'label ch99_control:\n    call ch99\n    return\n'


def _project(tmp_path, controller=CONTROLLER,
             chapters=('ch99', 'ch01', 'ch02', 'ch03')):
    root = tmp_path / 'proj'
    script = root / 'game' / 'script'
    script.mkdir(parents=True)
    for name in chapters:
        (script / f'{name}.rpy').write_text(
            f'label {name}:\n    e "{name} 的台词"\n    return\n',
            encoding='utf-8')
    if controller is not None:
        (script / 'chapter_controller.rpy').write_text(controller,
                                                       encoding='utf-8')
    return root


def _controller(root):
    return root / 'game' / 'script' / 'chapter_controller.rpy'


class _Host:
    """窗口层的最小替身（和 `test_control_init._Host` 同一套接法）。"""

    def __init__(self, root, graph):
        self._project_root = root
        self.graph = graph
        self.status = []
        self.logs = []
        self.last_error = ''

    def _status(self, msg):
        self.status.append(str(msg))

    def _log(self, msg):
        self.logs.append(str(msg))

    def _set_graph(self, graph, keep_view=None):
        self.graph = graph

    def _run_command(self, cmd):
        try:
            cmd.execute()
        except RuntimeError as exc:
            self.last_error = str(exc)
            return False
        return True

    def on_graph_structure_changed(self):
        pass

    def _refresh_flow_edges(self):
        return {}


_Host._control_route_plan = NodeEditMixin._control_route_plan
_Host.plan_wire_landing = NodeEditMixin.plan_wire_landing
_Host._apply_wire = NodeEditMixin._apply_wire
_Host._apply_control_route = NodeEditMixin._apply_control_route
_Host._write_dialog_edge = NodeEditMixin._write_dialog_edge


def _graph(*labels):
    g = Graph()
    ids = [g.add_node(name, 0, 0) for name in labels]
    return g, ids


# ===========================================================================
#  一、规划层：源章没有 `_control` ⇒ 先补它，再接线（不再拒写）
# ===========================================================================

def test_source_without_wrapper_gets_an_init_plan(tmp_path):
    root = _project(tmp_path)
    plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
    assert plan.ok is False                    # 还没写：得先补包装
    assert plan.init is not None and plan.init.ok is True
    assert plan.init.role == 'source'
    assert plan.init.control_label == 'ch01_control'
    assert plan.init.reachable is False        # 这一章现在没人从编排层进来
    assert 'ch01_control' in plan.reason


def test_plan_is_writable_as_soon_as_the_init_is_available(tmp_path):
    """窗口层把它包成 `control_init` ⇒ 对话框按「创建」是真写。"""
    root = _project(tmp_path)
    g, (a, b) = _graph('ch01', 'ch02')
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    assert plan['kind'] == 'control_init', plan
    assert wire_plan_writable(plan) is True


def test_no_control_layer_at_all_still_refuses(tmp_path):
    """一个 `_control` 都没有的项目：不新建文件（那不是接线，是建编排层）。"""
    root = _project(tmp_path, controller=None)
    plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
    assert plan.ok is False
    assert plan.init is None or plan.init.ok is False


# ===========================================================================
#  二、一次落盘：两头的包装都缺时，一次把两个都补上
# ===========================================================================

def test_one_command_writes_both_wrappers_and_the_route(tmp_path):
    root = _project(tmp_path)
    g, (a, b) = _graph('ch01', 'ch02')
    host = _Host(root, g)
    before = _controller(root).read_text(encoding='utf-8')

    plan = host.plan_wire_landing(a, b)
    res = host._apply_wire(plan)
    assert res.get('ok') is True, res

    text = _controller(root).read_text(encoding='utf-8')
    assert 'label ch01_control:\n    call ch01\n    jump ch02_control' in text
    assert 'label ch02_control:\n    call ch02\n    return' in text
    assert text.count('label ch01_control:') == 1
    # 章节剧本一个字没改
    assert (root / 'game/script/ch01.rpy').read_text(encoding='utf-8') \
        == 'label ch01:\n    e "ch01 的台词"\n    return\n'
    # 图上真的多了这条线
    edges = list(g.edges.values())
    assert len(edges) == 1 and edges[0].origin == 'control'

    # 一次 Ctrl+Z：包装和路由都回去
    host._last_control_cmd.undo()
    assert _controller(root).read_text(encoding='utf-8') == before
    assert not g.edges


def test_status_bar_says_it_is_not_reachable_yet(tmp_path):
    """写了就要说清"现在还没人走到它" —— 不能让人以为游戏里已经通了。"""
    root = _project(tmp_path)
    g, (a, b) = _graph('ch01', 'ch02')
    host = _Host(root, g)
    host._apply_wire(host.plan_wire_landing(a, b))
    joined = '\n'.join(host.status)
    assert '已接到编排层' in joined
    assert '没有任何地方进入' in joined, joined


def test_redo_after_undo_rebuilds_both_wrappers(tmp_path):
    root = _project(tmp_path)
    g, (a, b) = _graph('ch01', 'ch02')
    host = _Host(root, g)
    host._apply_wire(host.plan_wire_landing(a, b))
    cmd = host._last_control_cmd
    cmd.undo()
    cmd.execute()
    text = _controller(root).read_text(encoding='utf-8')
    assert text.count('label ch01_control:') == 1
    assert text.count('label ch02_control:') == 1
    assert '    jump ch02_control' in text
    assert any(e.origin == 'control' for e in g.edges.values())


# ===========================================================================
#  三、断掉的局部：先接下一段，回头接上一段，整条链路自然就通了
# ===========================================================================

def test_a_broken_fragment_can_be_wired_from_its_own_tail(tmp_path):
    """
    `ch01 → ch02 → ch03` 全都没入口时，从后往前接也能写。

    接完 `ch02 → ch03` 之后，`ch02_control` 只是暂时没人走；再接
    `ch01 → ch02` 时它已经是现成的出口，**不再补第二份包装**。
    """
    root = _project(tmp_path)
    g, (a, b, c) = _graph('ch01', 'ch02', 'ch03')
    host = _Host(root, g)

    assert host._apply_wire(host.plan_wire_landing(b, c)).get('ok') is True
    text = _controller(root).read_text(encoding='utf-8')
    assert 'label ch02_control:\n    call ch02\n    jump ch03_control' in text

    assert host._apply_wire(host.plan_wire_landing(a, b)).get('ok') is True
    text = _controller(root).read_text(encoding='utf-8')
    assert text.count('label ch02_control:') == 1      # 没有第二份
    assert 'label ch01_control:\n    call ch01\n    jump ch02_control' in text


def test_source_entered_by_a_direct_jump_is_written_and_explained(tmp_path):
    """
    这一章现在是被 `jump ch01` **直接**进入的：照样写，但说明要改接入口。

    以前这条被判成"死代码"直接拒写 —— 而真机项目里 25 章就卡在这。
    """
    root = _project(tmp_path)
    (root / 'game' / 'script' / 'main.rpy').write_text(
        'label start:\n    jump ch01\n', encoding='utf-8')
    init = plan_control_init(root, 'ch01', role='source')
    assert init.ok is True and init.reachable is False
    assert any('直接' in n and 'jump ch01' in n for n in init.notes), init.notes

    g, (a, b) = _graph('ch01', 'ch02')
    host = _Host(root, g)
    assert host._apply_wire(host.plan_wire_landing(a, b)).get('ok') is True
    joined = '\n'.join(host.status)
    assert '直接' in joined and 'jump ch01' in joined, joined


def test_reachable_source_gets_no_caveat(tmp_path):
    """已经在编排层里的章节：照旧，别加多余的警告。"""
    root = _project(tmp_path, controller=(
        'label ch01_control:\n    call ch01\n    return\n\n'
        'label ch02_control:\n    call ch02\n    return\n'))
    g, (a, b) = _graph('ch01', 'ch02')
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    assert plan['kind'] == 'control', plan
    host._apply_wire(plan)
    assert not any('没有任何地方进入' in s for s in host.status), host.status


# ===========================================================================
#  四、对话框：按钮文字 / 提示都要跟"真会发生什么"一致
# ===========================================================================

def test_dialog_button_says_create_and_hint_names_the_wrapper(tmp_path, qapp):
    root = _project(tmp_path)
    g = Graph()
    a = g.add_node('ch01', 0, 0)
    b = g.add_node('ch02', 0, 0)
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    dlg = AddEdgeDialog(None, nodes=g.nodes, from_id=a, to_id=b,
                        plan_fn=lambda *_args: plan)
    from PySide6.QtWidgets import QDialogButtonBox
    btn = dlg._buttons.button(QDialogButtonBox.Ok)
    assert btn.text() == '创建', btn.text()

    text = dlg.plan_hint_text()
    assert '会写进编排层' in text
    assert 'ch01_control' in text          # 点名会给这一章补的入口
    assert '还没有入口' in text            # 也要说清暂时到不了


def test_dialog_accept_really_writes(tmp_path, qapp):
    root = _project(tmp_path)
    g = Graph()
    a = g.add_node('ch01', 0, 0)
    b = g.add_node('ch02', 0, 0)
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    dlg = AddEdgeDialog(None, nodes=g.nodes, from_id=a, to_id=b,
                        plan_fn=lambda *_args: plan)
    dlg._on_accept()
    assert dlg.write is True and dlg.draft is False
    assert host._write_dialog_edge(dlg) is True
    text = _controller(root).read_text(encoding='utf-8')
    assert 'label ch01_control:' in text
    assert '    jump ch02_control' in text
    assert len(g.edges) == 1


# ===========================================================================
#  五、草图仍然是**显式选择**：勾了才是草稿，没勾就一定是写
# ===========================================================================

def test_draft_checkbox_is_still_the_only_way_to_only_draw(tmp_path, qapp):
    root = _project(tmp_path)
    g = Graph()
    a = g.add_node('ch01', 0, 0)
    b = g.add_node('ch02', 0, 0)
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    dlg = AddEdgeDialog(None, nodes=g.nodes, from_id=a, to_id=b,
                        plan_fn=lambda *_args: plan)
    before = _controller(root).read_text(encoding='utf-8')
    dlg._draft.setChecked(True)
    from PySide6.QtWidgets import QDialogButtonBox
    assert dlg._buttons.button(QDialogButtonBox.Ok).text() == '画草稿'
    dlg._on_accept()
    assert dlg.write is False and dlg.draft is True
    assert _controller(root).read_text(encoding='utf-8') == before


def test_command_reports_itself_when_the_route_cannot_be_planned(tmp_path):
    """写不成的时候必须抛清楚原因，不许静默返回"成功"。"""
    root = _project(tmp_path)
    g, (a, b) = _graph('ch01', 'nope')
    host = _Host(root, g)
    plan = plan_control_route(root, 'ch01', 'nope', 'jump')
    assert plan.ok is False
    cmd = ControlRouteCommand(host, plan, from_id=a, to_id=b, etype='jump',
                              project_root=root)
    with pytest.raises(RuntimeError):
        cmd.execute()
    assert not g.edges
