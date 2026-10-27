# -*- coding: utf-8 -*-
"""
编排层以**终局语句**收尾时，接一条线要**改接**，不能画成草图。

    真机（2026-10-27，`sr_general`，用户原话）：
    "不对！为什么只能生成草图……？我明明没有用草稿"

`chapter_controller.rpy` 里最后一章的包装长这样（旧版编辑器生成的）：

    label chapter1_4_control:
        call chapter1_4 from _call_chapter1_4
        $ renpy.full_restart()          ← "演完游戏就结束"

想把它接到下一章（`chapter2_1_control`），`plan_control_route` 走完
jump / call / return 三档都不认识这一行，落到最后那条兜底：

    `chapter1_4_control` 的最后一条是「$ renpy.full_restart()」—— 判不出安全的插入点

于是 `kind='control_blocked'` ⇒ 写不进 ⇒ 对话框只能画一条草图。**可这恰恰是
最该写成跳转的一种**：终局语句自己就是"到此结束"，后面插什么都执行不到，
新路由只能替掉它 —— 而这也正是"这一章演完接着去 B"的唯一写法。

## 判据边界（不改）

  · 只认 `story.exits.terminating_call` 那三样：`renpy.full_restart()` /
    `renpy.quit()` / `renpy.jump_out_of_context(...)`；
  · 别的一律不动：`$ persistent.seen = True` 这种普通 python 语句结尾，
    仍然"判不出安全的插入点"（替掉它等于删掉作者写的一行）；
  · `call` 边不替换：插在终局语句**之前**（call 完回来，再结束，语义不变）。
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
    apply_control_route, plan_control_route)
from storygraph.ui_qt.dialogs import AddEdgeDialog  # noqa: E402
from storygraph.ui_qt.window_nodes import NodeEditMixin  # noqa: E402

#: 真机里那一份（`sr_general/game/chapter/chapter_controller.rpy` 的缩样）。
CONTROLLER_TERMINAL = '''label ch01_control:
    call ch01 from _call_ch01
    $ renpy.full_restart()

label ch02_control:
    call ch02
    return
'''


def _project(tmp_path, controller=CONTROLLER_TERMINAL):
    root = tmp_path / 'proj'
    script = root / 'game' / 'chapter'
    script.mkdir(parents=True)
    for name in ('ch01', 'ch02'):
        (script / f'{name}.rpy').write_text(
            f'label {name}:\n    e "{name} 的台词"\n    return\n',
            encoding='utf-8')
    (script / 'chapter_controller.rpy').write_text(controller,
                                                   encoding='utf-8')
    return root


def _controller(root):
    return root / 'game' / 'chapter' / 'chapter_controller.rpy'


def _text(root) -> str:
    return _controller(root).read_text(encoding='utf-8')


class _Host:
    def __init__(self, root, graph):
        self._project_root = root
        self.graph = graph
        self.status: list = []
        self.logs: list = []

    def _status(self, msg):
        self.status.append(str(msg))

    def _log(self, msg):
        self.logs.append(str(msg))

    def _set_graph(self, graph, keep_view=None):
        self.graph = graph

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


def _graph_with_two():
    g = Graph()
    a = g.add_node('ch01', 0, 0)
    b = g.add_node('ch02', 100, 0)
    return g, a, b


# ===========================================================================
#  一、计划：终局语句 ⇒ replace
# ===========================================================================

def test_jump_replaces_a_full_restart_ending(tmp_path):
    root = _project(tmp_path)
    plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
    assert plan.ok is True, plan.blocking
    assert plan.kind == 'replace'
    assert plan.old_line.strip() == '$ renpy.full_restart()'
    assert plan.new_lines == ['    jump ch02_control']
    # 必须点名"换掉了什么"，不能只说"改接"
    assert 'full_restart' in plan.reason and '改接' in plan.reason
    assert any('Ctrl+Z' in n for n in plan.notes), plan.notes


@pytest.mark.parametrize('ending', ['$ renpy.quit()',
                                    '$ renpy.jump_out_of_context("x")'])
def test_other_terminal_endings_are_replaceable_too(tmp_path, ending):
    root = _project(tmp_path, controller=(
        f'label ch01_control:\n    call ch01\n    {ending}\n\n'
        'label ch02_control:\n    call ch02\n    return\n'))
    plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
    assert plan.ok is True, plan.blocking
    assert plan.kind == 'replace'
    assert plan.new_lines == ['    jump ch02_control']


def test_an_ordinary_python_line_is_still_not_touched(tmp_path):
    """普通 python 语句结尾：替掉它等于删作者的一行 —— 仍然不写。"""
    root = _project(tmp_path, controller=(
        'label ch01_control:\n    call ch01\n    $ persistent.seen = True\n\n'
        'label ch02_control:\n    call ch02\n    return\n'))
    plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
    assert plan.ok is False
    assert plan.kind == 'blocked'
    assert '判不出安全的插入点' in plan.reason


def test_call_is_inserted_before_the_terminal_line(tmp_path):
    """`call` 不改接：插在终局语句之前，call 回来照旧结束。"""
    root = _project(tmp_path)
    plan = plan_control_route(root, 'ch01', 'ch02', 'call')
    assert plan.ok is True, plan.blocking
    assert plan.kind == 'insert'
    assert plan.new_lines == ['    call ch02']
    assert apply_control_route(plan)['ok']
    text = _text(root)
    assert ('    call ch02\n    $ renpy.full_restart()'
            in text), text          # 两句都在，顺序没变


# ===========================================================================
#  二、落盘：真的换掉那一行，别的一个字节不动
# ===========================================================================

def test_applying_it_really_swaps_the_line(tmp_path):
    root = _project(tmp_path)
    before = _text(root)
    chapter = (root / 'game' / 'chapter' / 'ch01.rpy')
    chapter_before = chapter.read_text(encoding='utf-8')

    plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
    assert apply_control_route(plan)['ok']
    after = _text(root)

    block = after.split('label ch02_control')[0]
    assert 'jump ch02_control' in block
    assert 'full_restart' not in block, block      # 原来那行没了
    # 只改一行、行数不变、别的 label 原样
    assert len(after.splitlines()) == len(before.splitlines())
    assert after.split('label ch02_control')[1] == \
        before.split('label ch02_control')[1]
    assert chapter.read_text(encoding='utf-8') == chapter_before, '章节源文件不许被改'


def test_undo_puts_the_ending_back(tmp_path):
    root = _project(tmp_path)
    before = _text(root)
    from storygraph.graph.commands import ControlRouteCommand
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
    cmd = ControlRouteCommand(host, plan, from_id=a, to_id=b, etype='jump')
    cmd.execute()
    assert 'full_restart' not in _text(root)
    cmd.undo()
    assert _text(root) == before


# ===========================================================================
#  三、窗口 / 对话框：这次是"写进去"，不是"画草图"
# ===========================================================================

def test_window_writes_instead_of_drawing_a_sketch(tmp_path):
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    assert plan['kind'] == 'control' and plan['ok'] is True, plan
    res = host._apply_wire(plan)
    assert res.get('ok') is True, res
    assert 'jump ch02_control' in _text(root)
    edges = [e for e in (g.edges or {}).values()]
    assert len(edges) == 1 and edges[0].origin == 'control', edges
    assert not any('草图' in s for s in host.status), host.status


def test_dialog_says_the_old_ending_will_be_replaced(tmp_path, qapp):
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    dlg = AddEdgeDialog(None, nodes=g.nodes, from_id=a, to_id=b,
                        plan_fn=lambda *_args: plan)
    text = dlg.plan_hint_text()
    assert '会写进编排层' in text
    assert 'full_restart' in text, text            # 点名要换掉的那一条
    dlg._on_accept()
    assert dlg.write is True
    assert dlg._buttons.button(dlg._buttons.StandardButton.Ok).text() == '创建'


def test_ok_button_says_it_only_draws_a_sketch_when_it_cannot_write(tmp_path,
                                                                   qapp):
    """
    按钮上写「创建」、实际只画一条线 —— 这就是用户那句"我明明没有用草稿"。

    写不进的落点，按钮自己就该叫「只画草图」；勾了草稿就叫「画草稿」。
    """
    root = _project(tmp_path, controller=(
        'label ch01_control:\n    call ch01\n    $ persistent.seen = True\n\n'
        'label ch02_control:\n    call ch02\n    return\n'))
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    assert plan['kind'] == 'control_blocked'
    dlg = AddEdgeDialog(None, nodes=g.nodes, from_id=a, to_id=b,
                        plan_fn=lambda *_args: plan)
    ok = dlg._buttons.button(dlg._buttons.StandardButton.Ok)
    assert ok.text() == '只画草图'
    assert '判不出安全的插入点' in ok.toolTip()
    # 勾上草稿 → 换一种说法（这次是**人自己**要草稿）
    dlg._draft.setChecked(True)
    assert ok.text() == '画草稿'


def test_a_writable_plan_keeps_the_button_saying_create(tmp_path, qapp):
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    dlg = AddEdgeDialog(None, nodes=g.nodes, from_id=a, to_id=b,
                        plan_fn=lambda *_args: plan)
    ok = dlg._buttons.button(dlg._buttons.StandardButton.Ok)
    assert ok.text() == '创建'


def test_a_plan_that_cannot_be_computed_says_so_up_front(tmp_path, qapp):
    """算不出落点时以前一个字都不说（只有一行灰色小字都没有）—— 现在明说。"""
    g, a, b = _graph_with_two()
    dlg = AddEdgeDialog(None, nodes=g.nodes, from_id=a, to_id=b,
                        plan_fn=lambda *_args: {})
    text = dlg.plan_hint_text()
    assert '草图' in text and '算不出' in text
    ok = dlg._buttons.button(dlg._buttons.StandardButton.Ok)
    assert ok.text() == '只画草图'
