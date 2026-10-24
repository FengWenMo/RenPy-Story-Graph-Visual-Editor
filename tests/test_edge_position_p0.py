# -*- coding: utf-8 -*-
"""
P0（2026-10-24）：边的“拍位置”字段 + 接线对话框默认写剧本。

这一批只钉两件事：

  1. **位置是数据**：草图默认 `chapter_end`，源边由 `line_id` 认成 `beat`；
     存/读版式、深拷贝、旧布局迁移都不能把它弄丢。
  2. **默认动作是写剧本**：能写时按「创建」就写；写不进 / 条件分支
     才落成草图（图上标「草图」）。
"""
from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QDialog, QDialogButtonBox  # noqa: E402

from storygraph.app.layout_store import apply_placement, placement_dict  # noqa: E402
from storygraph.app.session_graph import graph_from_layout_dict, layout_dict  # noqa: E402
from storygraph.graph.models import (Graph, POSITION_BEAT,  # noqa: E402
                                     POSITION_CHAPTER_END)
from storygraph.ui_qt.dialogs import AddEdgeDialog  # noqa: E402


# ---------------------------------------------------------------------------
#  一、模型：位置字段
# ---------------------------------------------------------------------------

def _graph():
    g = Graph()
    a = g.add_node('A', 0, 0)
    b = g.add_node('B', 0, 0)
    return g, a, b


def test_manual_edge_defaults_to_chapter_end():
    g, a, b = _graph()
    eid = g.add_edge(a, b, etype='jump', origin='manual')
    edge = g.edges[eid]
    assert edge.position == POSITION_CHAPTER_END
    assert edge.position_line_id == ''


def test_source_edge_with_a_line_id_reports_beat_position():
    g, a, b = _graph()
    eid = g.add_edge(a, b, etype='call', origin='source',
                     line_id='A.0007')
    edge = g.edges[eid]
    assert edge.position == POSITION_BEAT
    assert edge.position_line_id == 'A.0007'


def test_position_survives_deepcopy_and_both_layout_roundtrips(tmp_path):
    g, a, b = _graph()
    eid = g.add_edge(a, b, etype='jump', origin='manual',
                     position=POSITION_CHAPTER_END)
    assert __import__('copy').deepcopy(g.edges[eid]).position == \
        POSITION_CHAPTER_END

    # 瘦版式（自动恢复用的 .storygraph/layout.json）
    thin = placement_dict(g, tmp_path)
    g2, a2, b2 = _graph()
    apply_placement(g2, {'ok': True, **thin}, tmp_path)
    edge2 = next(iter(g2.edges.values()))
    assert edge2.position == POSITION_CHAPTER_END

    # 整图布局（Ctrl+Shift+L 的 .json）
    full = layout_dict(g)
    g3 = graph_from_layout_dict(full)
    assert g3.edges[eid].position == POSITION_CHAPTER_END, \
        '存/读整图布局把边的落点弄丢了'


def test_old_layout_without_position_defaults_to_chapter_end():
    g, a, b = _graph()
    eid = g.add_edge(a, b, etype='jump', origin='manual')
    data = layout_dict(g)
    del data['edges'][str(eid)]['position']
    del data['edges'][str(eid)]['position_line_id']
    back = graph_from_layout_dict(data)
    assert back.edges[eid].position == POSITION_CHAPTER_END


# ---------------------------------------------------------------------------
#  二、对话框：默认写剧本，分支/写不进才落草图
# ---------------------------------------------------------------------------

def _dialog(qapp, plan, positions=None):
    g, a, b = _graph()
    return AddEdgeDialog(None, nodes=g.nodes, from_id=a, to_id=b,
                         plan_fn=lambda _f, _t, *_args: plan,
                         positions_fn=(lambda _nid: positions)
                         if positions is not None else None)


def _ok_button(dlg):
    return dlg._buttons.button(QDialogButtonBox.Ok)


def test_dialog_writes_when_the_landing_is_known(qapp):
    """版面是旧版「添加连线」（按钮就写「创建」），但动作仍是默认写剧本。"""
    dlg = _dialog(qapp, {'ok': True, 'kind': 'chapter_end',
                         'reason': '插在「A」的 return 之前'})
    assert _ok_button(dlg).text() == '创建'

    dlg._on_accept()
    assert dlg.result() == QDialog.Accepted
    assert dlg.write is True
    assert dlg.position == POSITION_CHAPTER_END


def test_dialog_falls_back_to_a_sketch_when_the_landing_is_not_writable(qapp):
    dlg = _dialog(qapp, {'ok': False, 'kind': 'not_loaded',
                         'reason': '起点「A」不是当前载入的章节'})
    dlg._on_accept()
    assert dlg.result() == QDialog.Accepted
    assert dlg.write is False
    assert '不是当前载入' in dlg.reason


def test_condition_branch_is_handed_to_the_window(qapp):
    """条件分支不再被压成"只能画草图"：窗口层会把它写进编排层。"""
    dlg = _dialog(qapp, {'ok': True, 'kind': 'chapter_end',
                         'reason': '插在 return 之前'})
    dlg._cond.setText('flag')
    dlg._on_accept()
    assert dlg.write is True
    assert dlg.branch is True


def test_menu_branch_is_handed_to_the_window(qapp):
    dlg = _dialog(qapp, {'ok': True, 'kind': 'chapter_end',
                         'reason': '插在 return 之前'})
    dlg._opt.setText('去屋顶')
    dlg._on_accept()
    assert dlg.write is True
    assert dlg.branch is True


def test_a_beat_position_that_cannot_be_written_is_refused(qapp):
    """选了某一拍却写不进：不能悄悄画成永远转不了正的草图。"""
    dlg = _dialog(qapp, {'ok': False, 'kind': 'bad_position',
                         'reason': '第 2 拍在块体里'})
    dlg._pos.addItem('第 2 拍', ('beat', 'A.0002'))
    dlg._pos.setCurrentIndex(dlg._pos.count() - 1)
    dlg._on_accept()
    assert dlg.result() != QDialog.Accepted
    assert '换一个位置' in dlg._error.text()
