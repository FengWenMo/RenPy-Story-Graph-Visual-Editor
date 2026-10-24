# -*- coding: utf-8 -*-
"""
「编辑连线」窗口：双击一条线看**这条线**的属性。

判据（按用户 2026-10-24 的原话，不拿测试反推需求）：

  1. 版面与「添加连线」同一张表单：从 / 到 / 类型 / **插入位置** /
     条件 / 菜单项；类型能选 jump 还是 call；
  2. 线上只留条件/菜单项，位置只在窗口和 tooltip 里；单击线只选中；
  3. 源边 / 编排线可以在这个窗口里直接改**类型 / 到**，保存改写那一行；
     编排线的条件 / 菜单项也能改（按标记段换），源边的那两样挂在
     if / menu 上，窗口里写明原因；源边的「插入位置」可以挪到另一拍；
  4. 编排线仍保留「打开编排层文件」，但不再是"全都只读"。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QMenu,  # noqa: E402
                               QWidget)

from storygraph.graph.commands import UpdateEdgeCommand  # noqa: E402
from storygraph.graph.models import Graph  # noqa: E402
from storygraph.ui_qt.dialogs import EditEdgeDialog  # noqa: E402
from storygraph.ui_qt.window_nodes import NodeEditMixin  # noqa: E402


def _edge(**kw):
    base = dict(id='e1', type='call', condition='trust > 2',
                option_text='去屋顶', from_clause='_call_roof_1',
                origin='source', line_id='A.0003', from_id=1, to_id=2,
                position='beat', position_line_id='')
    base.update(kw)
    return SimpleNamespace(**base)


def _dialog(qapp, edge=None, **kw):
    return EditEdgeDialog(None, edge=edge or _edge(), from_label='A',
                          to_label='B', position_text='第 3 拍 / 共 5 拍',
                          **kw)


def _positions():
    return {'ok': True, 'items': [
        {'line_id': 'A.0002', 'number': 2, 'text': '第二拍', 'selectable': True},
        {'line_id': 'A.0003', 'number': 3, 'text': '块体里', 'selectable': False},
    ]}


def test_dialog_shows_every_property_including_the_position(qapp):
    dlg = _dialog(qapp)
    assert dlg._type.currentData() == 'call'
    assert dlg._cond.text() == 'trust > 2'
    assert dlg._opt.text() == '去屋顶'
    assert dlg._pos.currentText() == '第 3 拍 / 共 5 拍'
    assert dlg._pos.isEnabled() is False, '源边的落点只显示，不可改'
    assert dlg._src.isEnabled() is False and dlg._dst.isEnabled() is False
    assert dlg._cond.isEnabled() is False, '条件挂在 if / menu 上，不在这条线上'
    assert dlg._opt.isEnabled() is False
    assert dlg._buttons.button(QDialogButtonBox.Ok).text() == '保存'


def test_saving_a_source_edge_collects_only_its_own_fields(qapp):
    """
    源边能改的是**这一行自己的东西**（类型）；条件 / 菜单项在 if / menu 上，
    不该给一个"填了也不会改剧本"的格子。
    """
    dlg = _dialog(qapp)
    dlg._type.setCurrentIndex(dlg._type.findData('jump'))
    dlg._on_accept()
    assert dlg.result() == QDialog.Accepted
    assert dlg.etype == 'jump'
    assert dlg.condition == 'trust > 2', '不能悄悄清掉剧本里原有的条件'
    assert dlg.option_text == '去屋顶'
    assert dlg.from_clause == '', 'jump 不该留下存档返回点'
    assert dlg.changed is True


def test_source_edge_can_move_its_line_to_another_beat(qapp):
    """源边的「插入位置」不再只显示：选中另一拍，保存后会把那一行挪过去。"""
    edge = _edge(origin='source', line_id='A.0003', position='beat',
                 position_line_id='A.0003', type='jump', condition='',
                 option_text='', from_clause='')
    dlg = EditEdgeDialog(None, edge=edge, from_label='A', to_label='B',
                         position_text='第 3 拍 / 共 5 拍',
                         positions=_positions())
    assert dlg._pos.isEnabled() is True
    assert dlg._pos.currentData() == ('beat', 'A.0003')
    dlg._pos.setCurrentIndex(0)                 # 第 2 拍之后
    dlg._on_accept()
    assert dlg.position == 'beat'
    assert dlg.position_line_id == 'A.0002'
    assert dlg.changed is True


def test_source_edge_without_positions_keeps_position_read_only(qapp):
    edge = _edge(origin='source', line_id='A.0003', position='beat',
                 position_line_id='A.0003')
    dlg = EditEdgeDialog(None, edge=edge, from_label='A', to_label='B',
                         position_text='第 3 拍 / 共 5 拍')
    assert dlg._pos.isEnabled() is False
    assert dlg._pos.currentText() == '第 3 拍 / 共 5 拍'


def test_manual_edge_position_is_editable_in_the_same_window(qapp):
    edge = _edge(origin='manual', position='chapter_end',
                 position_line_id='', type='jump', condition='',
                 option_text='', from_clause='', line_id='')
    dlg = EditEdgeDialog(None, edge=edge, from_label='A', to_label='B',
                         position_text='', positions=_positions())
    assert dlg._pos.currentData() == ('chapter_end', '')
    assert dlg._pos.model().item(2).isEnabled() is False, '不能接的拍要置灰'
    dlg._pos.setCurrentIndex(1)
    dlg._on_accept()
    assert dlg.position == 'beat'
    assert dlg.position_line_id == 'A.0002'
    assert dlg.changed is True


def test_jump_option_still_selectable(qapp):
    """用户原话：原来那个是可以自选 jump 或者 call 的，不能做成只读。"""
    dlg = _dialog(qapp)
    assert dlg._type.isEnabled() is True
    dlg._type.setCurrentIndex(dlg._type.findData('jump'))
    assert dlg._type.currentData() == 'jump'
    dlg._type.setCurrentIndex(dlg._type.findData('call'))
    assert dlg._type.currentData() == 'call'


def test_control_edge_can_change_type_and_target_in_the_same_window(qapp):
    """
    用户 2026-10-24：编排线的属性窗口全是灰的，只能"打开编排层文件"。

    现在类型、「到」、条件、菜单项都能改：保存会改写 `X_control` 里对应
    那一小段（分支按标记段换，普通路由直接换成条件分支）。
    """
    opened = []
    dlg = _dialog(
        qapp,
        edge=_edge(origin='control', condition='', option_text='',
                   from_clause=''),
        targets=[(1, 'A'), (2, 'B'), (3, 'C')],
        open_fn=lambda: opened.append(True))
    assert dlg._type.isEnabled() is True
    assert dlg._dst.isEnabled() is True
    assert dlg._dst.currentData() == 2
    assert dlg._cond.isEnabled() is True
    assert dlg._opt.isEnabled() is True
    assert dlg._pos.isEnabled() is False
    assert dlg._open_btn.isVisibleTo(dlg) is True
    dlg._dst.setCurrentIndex(dlg._dst.findData(3))
    dlg._cond.setText('score > 3')
    dlg._on_accept()
    assert dlg.changed is True and dlg.to_id == 3
    assert dlg.condition == 'score > 3'
    dlg._open_btn.click()
    assert opened == [True]


def test_control_edge_without_changes_reports_no_change(qapp):
    dlg = _dialog(
        qapp,
        edge=_edge(origin='control', condition='', option_text='',
                   from_clause=''),
        targets=[(1, 'A'), (2, 'B')])
    dlg._on_accept()
    assert dlg.changed is False
    assert dlg.to_id == 2


def test_double_click_on_the_edge_label_does_not_fall_into_new_chapter(qapp):
    """
    用户实测的 bug：双击线的**标签** → 弹「添加新章节」。

    标签是 EdgeItem 的子项，双击冒到 view；view 只查了块和空白。
    这里直接调 view 的 double-click，钉住"标签也算这条线"。
    """
    from storygraph.ui_qt.graph_canvas import GraphCanvas

    canvas = GraphCanvas()
    g = Graph()
    a = g.add_node('A', 0, 0)
    b = g.add_node('B', 300, 0)
    g.add_edge(a, b, origin='source', option_text='去屋顶')
    canvas.set_graph(g)
    canvas.show()
    qapp.processEvents()

    ei = canvas._edges[0]
    pos = canvas.mapFromScene(ei._label.sceneBoundingRect().center())
    seen = []
    canvas.edge_edit_requested.connect(seen.append)
    created = []
    canvas.node_add_requested.connect(lambda *_: created.append(True))

    class Ev:
        def position(self):
            return QPointF(pos)

        def accept(self):
            pass

    canvas.mouseDoubleClickEvent(Ev())
    assert len(seen) == 1
    assert not created, '双击线的标签不许再落到「新建章节」'
    canvas.close()
    canvas.deleteLater()
    qapp.processEvents()


def test_single_click_on_an_edge_only_selects_it(qapp):
    """单击边 = 选中；不许弹窗。位置只进「编辑连线」窗口。"""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from storygraph.ui_qt.graph_canvas import GraphCanvas

    canvas = GraphCanvas()
    g = Graph()
    a = g.add_node('A', 0, 0)
    b = g.add_node('B', 300, 0)
    g.add_edge(a, b, origin='source', line_id='A.0001')
    canvas.set_graph(g)
    canvas.show()
    qapp.processEvents()

    ei = canvas._edges[0]
    mid = canvas.mapFromScene(QPointF(
        (ei._p1.x() + ei._p2.x()) / 2.0,
        (ei._p1.y() + ei._p2.y()) / 2.0))
    opened = []
    canvas.edge_edit_requested.connect(opened.append)
    QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=QPoint(mid))
    qapp.processEvents()

    assert opened == [], '单击边不该弹「编辑连线」'
    assert ei.isSelected() is True
    canvas.close()
    canvas.deleteLater()
    qapp.processEvents()


def test_edge_menu_does_not_print_the_position_outside(qapp):
    """线外只显示条件/菜单；右键菜单也不许把"第几拍"顶在开头。"""
    from storygraph.graph.models import Graph as _Graph

    g = _Graph()
    a = g.add_node('A', 0, 0)
    b = g.add_node('B', 0, 0)
    eid = g.add_edge(a, b, 'jump', '', '', '')

    class Host(QWidget):
        graph = g

        def plan_wire_landing(self, *args, **kwargs):
            return {'ok': True}

        def _status(self, *_args):
            pass

        def _log(self, *_args):
            pass

        def edit_edge(self, *_args):
            pass

        def open_control_layer(self, *_args):
            pass

        def delete_edge(self, *_args):
            pass

        def _open_edge_in_card(self, *_args):
            pass

    menu = NodeEditMixin.build_edge_menu(Host(), g.edges[eid])
    texts = [a.text() for a in menu.actions()]
    assert texts[0] == '编辑连线…'
    assert not any('第 3 拍' in t or '章末' in t or '源边' in t for t in texts), texts


def test_window_edit_edge_saves_through_the_command(qapp, monkeypatch):
    """窗口层只做"开窗 → 命令"；对话框自身不碰 graph（草图走 UpdateEdge）。"""
    g = Graph()
    a = g.add_node('A', 0, 0)
    b = g.add_node('B', 0, 0)
    eid = g.add_edge(a, b, 'jump', '', '', '', origin='manual')

    class Host:
        graph = g

        def __init__(self):
            self.status = []
            self.logs = []

        def _status(self, msg):
            self.status.append(str(msg))

        def _log(self, msg):
            self.logs.append(str(msg))

        def _run_command(self, cmd):
            cmd.execute()
            return True

        def on_graph_content_changed(self, _nid):
            pass

        def _edge_position_text(self, edge):
            return '第 2 拍 / 共 3 拍'

        def change_edge_position(self, edge):
            return False

        def open_control_layer(self, edge):
            return False

    class Dlg:
        def __init__(self, *args, **kwargs):
            self.etype = 'call'
            self.condition = 'x > 1'
            self.option_text = '去'
            self.from_clause = '_call_b_1'
            self.position = 'chapter_end'
            self.position_line_id = ''
            self.to_id = b
            self.changed = True

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr('storygraph.ui_qt.dialogs.EditEdgeDialog', Dlg)
    host = Host()
    edge = g.edges[eid]
    assert NodeEditMixin.edit_edge(host, edge) is True
    edge = g.edges[eid]
    assert (edge.type, edge.condition, edge.option_text, edge.from_clause) == \
        ('call', 'x > 1', '去', '_call_b_1')
    assert any('已更新' in s for s in host.status), host.status


def test_update_edge_command_carries_from_clause_and_undoes(qapp):
    g = Graph()
    a = g.add_node('A', 0, 0)
    b = g.add_node('B', 0, 0)
    eid = g.add_edge(a, b, 'call', '', '', '_call_old')

    class View:
        graph = g

        def on_graph_content_changed(self, _nid):
            pass

    cmd = UpdateEdgeCommand(View(), eid, etype='jump', condition='x > 1',
                            option_text='去', from_clause='',
                            position='beat', position_line_id='A.0002')
    cmd.execute()
    edge = g.edges[eid]
    assert (edge.type, edge.condition, edge.option_text, edge.from_clause) == \
        ('jump', 'x > 1', '去', '')
    assert (edge.position, edge.position_line_id) == ('beat', 'A.0002')
    cmd.undo()
    edge = g.edges[eid]
    assert (edge.type, edge.condition, edge.option_text, edge.from_clause) == \
        ('call', '', '', '_call_old')
    assert edge.position == 'chapter_end'
