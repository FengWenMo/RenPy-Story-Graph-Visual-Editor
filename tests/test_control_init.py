# -*- coding: utf-8 -*-
"""
P2.5（2026-10-24）：补**目标章**的 `_control` 包装，然后接线。

这一层解决的是一句很具体的话：项目已经有编排层，但新导入的目标章还没有
`B_control`。以前只能"先导出控制流"，否则这条线接不上。

判据只有一条：**接线必须真的写进文件**。所以：

  · 目标章缺包装 → 新写的 `jump B_control` 就是第一个入口 → 补；
  · 源章缺包装 → 也补（2026-10-27 用户口径："就算是断裂的局部也能不画
    草图直接连"）。补之前是不是已经有人进来、进来的是不是 `jump X`
    都不拦，只在 `notes` / `reachable` 里说清"这条线什么时候才会执行"；
  · 项目里一个 `_control` 都没有 → 不新建文件（没有入口的文件只会是死代码，
    而且那不是"接线"，是"建编排层"——那一步由「接管进入点」负责）。
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
    apply_control_init, plan_control_init, plan_control_route,
    wire_plan_writable)
from storygraph.ui_qt.dialogs import AddEdgeDialog  # noqa: E402
from storygraph.ui_qt.window_nodes import NodeEditMixin  # noqa: E402

CONTROLLER = 'label ch01_control:\n    call ch01\n    return\n'


def _project(tmp_path, controller=CONTROLLER, chapters=('ch01', 'ch02')):
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


# ===========================================================================
#  一、计划：补什么、补在哪、为什么不能补
# ===========================================================================

class TestPlanInit:
    def test_target_wrapper_is_appended_and_is_semantically_equivalent(self, tmp_path):
        root = _project(tmp_path)
        plan = plan_control_init(root, 'ch02')
        assert plan.ok is True, plan.blocking
        assert plan.kind == 'append'
        assert plan.safe is True
        assert plan.path == str(_controller(root))
        assert 'label ch02_control:' in plan.new_text
        assert '    call ch02\n    return\n' in plan.new_text
        # 原有内容一个字不动，只在末尾追加
        assert plan.new_text.startswith(plan.old_text.rstrip('\n'))

    def test_existing_wrapper_is_a_noop(self, tmp_path):
        root = _project(tmp_path)
        plan = plan_control_init(root, 'ch01')
        assert plan.ok is True and plan.kind == 'exists'
        assert plan.new_text == plan.old_text
        assert '已经存在' in plan.reason

    def test_no_control_layer_at_all_is_blocked_not_created(self, tmp_path):
        root = _project(tmp_path, controller=None)
        plan = plan_control_init(root, 'ch01')
        assert plan.ok is False
        assert plan.path == ''
        assert '死代码' in plan.reason
        # 不建文件
        assert not (root / 'game' / 'script' / 'chapter_controller.rpy').exists()

    def test_missing_chapter_label_is_blocked(self, tmp_path):
        root = _project(tmp_path)
        plan = plan_control_init(root, 'nope')
        assert plan.ok is False
        assert '找不到' in plan.reason

    def test_label_with_parameters_is_blocked(self, tmp_path):
        root = _project(tmp_path, controller=CONTROLLER,
                        chapters=('ch01',))
        (root / 'game' / 'script' / 'ch02.rpy').write_text(
            'label ch02(flag):\n    e "x"\n    return\n', encoding='utf-8')
        plan = plan_control_init(root, 'ch02')
        assert plan.ok is False
        assert '带参数' in plan.reason

    def test_source_wrapper_is_written_even_if_entered_directly(self, tmp_path):
        """
        源章缺包装、现在是 `jump ch01` **直接**进入的：照样补。

        以前这里拒写（"补出来是死代码"），结果一个还没接进入口的章节永远
        接不出线 —— 用户 2026-10-27：「就算是断裂的局部也能不画草图直接连」。
        写归写，但必须标明它暂时没人走。
        """
        root = _project(tmp_path, controller='label ch02_control:\n    call ch02\n    return\n')
        (root / 'game' / 'script' / 'main.rpy').write_text(
            'label start:\n    jump ch01\n', encoding='utf-8')
        plan = plan_control_init(root, 'ch01', role='source')
        assert plan.ok is True and plan.kind == 'append', plan.reason
        assert plan.reachable is False
        assert any('jump ch01' in n and '直接' in n for n in plan.notes), plan.notes
        assert 'label ch01_control:' in plan.new_text

    def test_source_wrapper_with_no_entry_at_all_is_written_with_a_note(self, tmp_path):
        """一个入口都没有的章节：也补（否则图上接不出来），并说清暂时到不了。"""
        root = _project(tmp_path,
                        controller='label ch02_control:\n    call ch02\n    return\n')
        plan = plan_control_init(root, 'ch01', role='source')
        assert plan.ok is True and plan.kind == 'append', plan.reason
        assert plan.reachable is False
        assert any('没有任何地方进入' in n for n in plan.notes), plan.notes
        # 真的能落盘
        res = apply_control_init(plan)
        assert res['ok'] is True, res
        assert 'label ch01_control:' in _controller(root).read_text(encoding='utf-8')

    def test_source_wrapper_fixes_a_broken_jump_to_control(self, tmp_path):
        """已经有人 jump ch01_control，但 label 丢了 → 补它是修 bug。"""
        root = _project(tmp_path, controller='label ch02_control:\n    call ch02\n    return\n')
        (root / 'game' / 'script' / 'main.rpy').write_text(
            'label start:\n    jump ch01_control\n', encoding='utf-8')
        plan = plan_control_init(root, 'ch01', role='source')
        assert plan.ok is True and plan.kind == 'append'
        assert plan.safe is True

    def test_target_with_a_direct_jump_gets_a_warning(self, tmp_path):
        root = _project(tmp_path)
        (root / 'game' / 'script' / 'main.rpy').write_text(
            'label start:\n    jump ch02\n', encoding='utf-8')
        plan = plan_control_init(root, 'ch02')
        assert plan.ok is True
        assert any('不经过这个包装' in n for n in plan.notes), plan.notes


# ===========================================================================
#  二、落盘：追加、保 BOM/CRLF、过期拒写
# ===========================================================================

class TestApplyInit:
    def test_append_keeps_bom_and_crlf(self, tmp_path):
        root = _project(tmp_path)
        path = _controller(root)
        path.write_bytes(b'\xef\xbb\xbf' + CONTROLLER.replace('\n', '\r\n')
                         .encode('utf-8'))
        plan = plan_control_init(root, 'ch02')
        res = apply_control_init(plan)
        assert res['ok'] is True, res
        raw = path.read_bytes()
        assert raw.startswith(b'\xef\xbb\xbf')
        assert b'label ch02_control:\r\n    call ch02\r\n    return\r\n' in raw

    def test_stale_text_writes_nothing(self, tmp_path):
        root = _project(tmp_path)
        path = _controller(root)
        plan = plan_control_init(root, 'ch02')
        path.write_text(path.read_text(encoding='utf-8') + '\n# 手改\n',
                        encoding='utf-8')
        before = path.read_bytes()
        res = apply_control_init(plan)
        assert res['ok'] is False
        assert '全文对不上' in res['error']
        assert path.read_bytes() == before

    def test_exists_is_a_successful_noop(self, tmp_path):
        root = _project(tmp_path)
        before = _controller(root).read_bytes()
        res = apply_control_init(plan_control_init(root, 'ch01'))
        assert res['ok'] is True
        assert _controller(root).read_bytes() == before


# ===========================================================================
#  三、接起来：补包装 → 重算路由 → 写路由；一次撤销全回去
# ===========================================================================

class _Graph:
    def __init__(self):
        self.edges = {}

    def add_edge(self, *args, **kwargs):
        self.edges['e1'] = object()
        return 'e1'

    def remove_edge(self, eid):
        self.edges.pop(eid, None)


class _Host:
    def __init__(self, root, graph):
        self._project_root = root
        self.graph = graph
        self.status = []
        self.logs = []
        self.renders = 0

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
        self.renders += 1

    def _refresh_flow_edges(self):
        return {}


_Host._control_route_plan = NodeEditMixin._control_route_plan
_Host.plan_wire_landing = NodeEditMixin.plan_wire_landing
_Host._apply_wire = NodeEditMixin._apply_wire
_Host._apply_control_route = NodeEditMixin._apply_control_route
_Host._write_dialog_edge = NodeEditMixin._write_dialog_edge


def _graph_with_two():
    g = Graph()
    a = g.add_node('ch01', 0, 0)
    b = g.add_node('ch02', 0, 0)
    return g, a, b


def test_window_plan_offers_to_fill_the_missing_target_wrapper(tmp_path):
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    assert plan['ok'] is False
    assert plan['kind'] == 'control_init', plan
    init = plan['control_plan'].init
    assert init.ok is True and init.chapter_label == 'ch02'
    assert '还缺' in plan['reason']


def test_applying_the_init_plan_writes_both_and_undo_restores_once(tmp_path):
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    before = _controller(root).read_text(encoding='utf-8')
    plan = host.plan_wire_landing(a, b)
    res = host._apply_wire(plan)
    assert res.get('ok') is True, res

    text = _controller(root).read_text(encoding='utf-8')
    assert 'label ch02_control:' in text
    assert '    jump ch02_control' in text
    assert 'label ch01_control:\n    call ch01\n    jump ch02_control' in text
    # 章节源文件一个字没改
    assert (root / 'game/script/ch02.rpy').read_text(encoding='utf-8') \
        == 'label ch02:\n    e "ch02 的台词"\n    return\n'
    edge = next(iter(g.edges.values()))
    assert edge.origin == 'control' and edge.position == 'control_end'

    # Ctrl+Z 一次：包装和路由都回去
    cmd = host._last_control_cmd
    assert cmd is not None
    cmd.undo()
    assert _controller(root).read_text(encoding='utf-8') == before
    assert not g.edges


def test_dialog_still_confirms_a_control_init_and_writes(tmp_path, qapp):
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    dlg = AddEdgeDialog(None, nodes=g.nodes, from_id=a, to_id=b,
                        plan_fn=lambda *_args: plan)
    from PySide6.QtWidgets import QDialogButtonBox
    btn = dlg._buttons.button(QDialogButtonBox.Ok)
    assert btn.text() == '创建', btn.text()
    dlg._on_accept()
    assert dlg.write is True
    assert dlg.plan['kind'] == 'control_init'
    assert '还缺' in dlg.reason
    assert 'ch02_control' in dlg.reason


def test_redo_after_undo_rebuilds_the_wrapper_and_route(tmp_path):
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    host._apply_wire(host.plan_wire_landing(a, b))
    host._last_control_cmd.undo()
    host._last_control_cmd.execute()
    text = _controller(root).read_text(encoding='utf-8')
    assert text.count('label ch02_control:') == 1
    assert '    jump ch02_control' in text
    assert any(e.origin == 'control' for e in g.edges.values())


def test_route_still_refuses_when_the_target_chapter_is_missing(tmp_path):
    root = _project(tmp_path, controller=CONTROLLER, chapters=('ch01',))
    plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
    assert plan.ok is False
    assert plan.init is not None and plan.init.ok is False
    assert '找不到' in plan.init.reason


def test_control_route_command_without_init_still_works(tmp_path):
    root = _project(tmp_path, controller=(
        'label ch01_control:\n    call ch01\n    return\n\n'
        'label ch02_control:\n    call ch02\n    return\n'))
    g = Graph()
    a = g.add_node('ch01', 0, 0)
    b = g.add_node('ch02', 0, 0)
    host = _Host(root, g)
    plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
    cmd = ControlRouteCommand(host, plan, from_id=a, to_id=b, etype='jump')
    cmd.execute()
    assert 'jump ch02_control' in _controller(root).read_text(encoding='utf-8')


# ===========================================================================
#  四、"对话框说能写、窗口却什么都不做"（2026-10-27 真机 P0）
#
#  用户原话："我连线了好多次……没有显示"。链路上只差一处：对话框认
#  `control_init`（`创建` 按得下去），窗口 `_write_dialog_edge` 只看
#  `plan['ok']` —— 而 `control_init` 的 ok 恰好是 False。于是窗口正常关闭、
#  图上一条线都没有。真机 7 个新章里 42 对连线有 12 对落在这条死路上。
# ===========================================================================

def test_wire_plan_writable_treats_control_init_as_writable():
    """
    判据真值表：**只有这一份**，对话框 / 窗口 / 草图转正都引用它。
    """
    assert wire_plan_writable({'ok': True, 'kind': 'control'}) is True
    assert wire_plan_writable({'ok': True, 'kind': 'chapter_end'}) is True
    # 还缺目标章包装：没写，但**能写**（`_apply_control_route` 接得住）
    assert wire_plan_writable({'ok': False, 'kind': 'control_init'}) is True
    for kind in ('control_blocked', 'not_loaded', 'bad_position',
                 'stale_position', 'ambiguous', 'cannot'):
        assert wire_plan_writable({'ok': False, 'kind': kind}) is False, kind
    assert wire_plan_writable({}) is False
    assert wire_plan_writable(None) is False


def test_creating_a_line_to_a_wrapperless_chapter_really_writes(tmp_path, qapp):
    """
    **端到端**：目标章还没有 `_control` 包装时，「创建」之后必须真有这条线。

    这条用例就是 2026-10-27 真机 bug 的判据 —— 修之前 `dlg.write is True`
    成立、`_write_dialog_edge` 却返回 False 且图上一根线都不多。
    """
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    dlg = AddEdgeDialog(None, nodes=g.nodes, from_id=a, to_id=b,
                        plan_fn=lambda *_args: plan)
    dlg._on_accept()
    assert dlg.write is True                  # 对话框允许「创建」
    assert dlg.plan['kind'] == 'control_init'

    assert host._write_dialog_edge(dlg) is True
    text = _controller(root).read_text(encoding='utf-8')
    assert 'label ch02_control:' in text      # 包装补上了
    assert '    jump ch02_control' in text    # 路由写上了
    edges = list((g.edges or {}).values())
    assert len(edges) == 1, edges
    assert edges[0].origin == 'control'       # 图上真的有这条线
    assert not any('没写进去' in s for s in host.status), host.status


def test_write_dialog_edge_still_refuses_a_blocked_plan(tmp_path, qapp,
                                                        monkeypatch):
    """
    反过来也要守：**写不进**的落点不许假装成功（否则就是"静默假完成"）。

    这里用 `control_blocked`（源章的 `_control` 结尾是一条判不出安全插入点
    的普通 python 语句）。⚠ 别用 `$ renpy.full_restart()` 当"写不进"的样本
    —— 那是终局语句，**能改接**（见 `test_control_route_terminal.py`）。
    """
    root = _project(tmp_path, controller=(
        'label ch01_control:\n    call ch01\n    $ persistent.seen = True\n\n'
        'label ch02_control:\n    call ch02\n    return\n'))
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    assert plan['kind'] == 'control_blocked' and plan['ok'] is False
    dlg = AddEdgeDialog(None, nodes=g.nodes, from_id=a, to_id=b,
                        plan_fn=lambda *_args: plan)
    dlg._on_accept()
    assert dlg.write is False                 # 对话框就该走"草图"那条路

    import storygraph.ui_qt.window_nodes as WN
    shown = []

    class _FakeBox:                            # 不弹真框
        @staticmethod
        def warning(*args, **kwargs):
            shown.append(str(args[2]) if len(args) > 2 else '')

    monkeypatch.setattr(WN, 'QMessageBox', _FakeBox)
    assert host._write_dialog_edge(dlg) is False
    assert shown and '没有这条线' in shown[0]   # 必须**弹出来**说清
    assert not g.edges


def test_dialog_says_it_will_add_the_missing_wrapper(tmp_path, qapp):
    """
    「创建」之前就要说清会发生什么 —— 尤其是**会顺手补一个包装**这件事。

    2026-10-27 真机：对话框里对"写进编排层 / 补包装 / 只画草图"三种结果
    一个字都不显示，人按完按钮只能看状态栏。
    """
    root = _project(tmp_path)
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    dlg = AddEdgeDialog(None, nodes=g.nodes, from_id=a, to_id=b,
                        plan_fn=lambda *_args: plan)
    text = dlg.plan_hint_text()
    assert '会写进编排层' in text
    assert 'ch02_control' in text          # 点名会被补上的包装
    # 对话框没 show 过，`isVisible()` 恒为 False —— 看"有没有被显式藏起来"
    assert not dlg._plan_hint.isHidden()


def test_dialog_says_a_blocked_wire_only_draws_a_sketch(tmp_path, qapp):
    root = _project(tmp_path, controller=(
        'label ch01_control:\n    call ch01\n    $ persistent.seen = True\n\n'
        'label ch02_control:\n    call ch02\n    return\n'))
    g, a, b = _graph_with_two()
    host = _Host(root, g)
    plan = host.plan_wire_landing(a, b)
    assert plan['kind'] == 'control_blocked'
    dlg = AddEdgeDialog(None, nodes=g.nodes, from_id=a, to_id=b,
                        plan_fn=lambda *_args: plan)
    text = dlg.plan_hint_text()
    assert '只会画一条' in text and '草图' in text
    assert '写不进剧本' in text
