# -*- coding: utf-8 -*-
"""
P1（2026-10-24）：边的「拍位置」真正可选 + `call` 的存档返回点。

P0 只做到"章末"这一个落点；这一批补两件事：

1. **位置选择器**：接线窗口里可以选「第 N 拍」，计划/写回/草图转正
   都按同一个 `line_id` 走。选在块体里、块头上、已有 jump 的拍后面
   → 一律拒绝，并说清为什么（不让下拉框给出一个写不进的位置）。
2. **`call X from Y`**：`from` 是存档返回点。窗口可以自动命名
   （`_call_<目标>_<n>`，全项目查重），也可以手填；`jump` 没有这一格。

这两件事都不是"再加一个字段"：判据必须和写回共用一份，否则会出现
"界面说能写、点完只画了一条草图"——那正是 P0 之前的病。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QDialog, QDialogButtonBox  # noqa: E402

from storygraph.graph.models import Graph  # noqa: E402
from storygraph.story.flow import (call_counts, collect_from_clauses,  # noqa: E402
                                   from_stem, next_from_clause)
from storygraph.ui_qt import window_nodes  # noqa: E402
from storygraph.ui_qt.dialogs import AddEdgeDialog  # noqa: E402
from storygraph.ui_qt.graph_canvas import (edge_label_text,  # noqa: E402
                                           edge_tooltip)
from storygraph.ui_qt.window_nodes import NodeEditMixin  # noqa: E402

from test_wire_landing import (_Host, _nodes, _open,  # noqa: E402
                               TAIL_IF, TAIL_JUMP, TAIL_RETURN)

_Host.edge_position_choices = NodeEditMixin.edge_position_choices
_Host.suggest_call_from = NodeEditMixin.suggest_call_from
_Host.write_manual_edge = NodeEditMixin.write_manual_edge

LOOP = ("label ch01:\n"
        "    e \"前面\"\n"
        "    while flag:\n"
        "        e \"循环里\"\n"
        "    return\n"
        "\n"
        "label ch02:\n"
        "    return\n")


def _ok(dlg):
    return dlg._buttons.button(QDialogButtonBox.Ok)


# ===========================================================================
#  一、计划：指定第 N 拍
# ===========================================================================

class TestBeatPositionPlan:
    def test_can_wire_after_an_earlier_beat_when_the_chapter_ends_with_jump(
            self, tmp_path):
        """
        章末已有 jump 时，默认落点是"有歧义"；但指定第 1 拍位置后，
        这里就是**能写**的 —— 这是 P1 的核心理由。
        """
        session, graph, host, _ = _open(tmp_path, TAIL_JUMP)
        ids = _nodes(graph)
        first = next(b for b in session.beats
                     if b.label == 'ch01' and b.kind == 'say')

        plan = host.plan_wire_landing(
            ids['ch01'], ids['ch02'], position='beat',
            position_line_id=first.line_id)

        assert plan['ok'] is True, plan
        assert plan['kind'] == 'beat'
        assert plan['after'] is True
        assert plan['index'] == 0
        assert '第 1 拍' in plan['reason']

    def test_applying_a_beat_position_puts_the_line_right_after_it(
            self, tmp_path):
        session, graph, host, _ = _open(tmp_path, TAIL_JUMP)
        ids = _nodes(graph)
        first = next(b for b in session.beats
                     if b.label == 'ch01' and b.kind == 'say')
        plan = host.plan_wire_landing(
            ids['ch01'], ids['ch02'], position='beat',
            position_line_id=first.line_id)

        res = host._apply_wire({**plan, 'etype': 'jump'})

        assert res.get('ok'), res
        added = session.beats[int(res['index'])]
        assert added.raw.strip() == 'jump ch02'
        assert getattr(added, 'nest_path', '') == ''
        assert session.beats[int(res['index']) - 1].text == '第一句', \
            '新行没有紧跟选中的那一拍'

    def test_return_position_inserts_before_the_return(self, tmp_path):
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        ret = next(b for b in session.beats
                   if b.label == 'ch01' and b.raw.strip() == 'return')

        plan = host.plan_wire_landing(
            ids['ch01'], ids['ch02'], position='beat',
            position_line_id=ret.line_id)

        assert plan['ok'] is True and plan['after'] is False
        res = host._apply_wire({**plan, 'etype': 'call'})
        assert res.get('ok'), res
        raws = [b.raw.strip() for b in session.beats if b.label == 'ch01']
        assert raws[-1] == 'return'
        assert raws[-2].startswith('call ch02'), raws

    def test_a_beat_inside_a_block_body_is_refused(self, tmp_path):
        session, graph, host, _ = _open(tmp_path, TAIL_IF)
        ids = _nodes(graph)
        body = next(b for b in session.beats
                    if getattr(b, 'nest_path', ''))
        plan = host.plan_wire_landing(
            ids['ch01'], ids['ch02'], position='beat',
            position_line_id=body.line_id)

        assert plan['ok'] is False
        assert plan['kind'] == 'bad_position'
        assert '块体' in plan['reason']

    def test_a_block_head_beat_is_refused(self, tmp_path):
        session, graph, host, _ = _open(tmp_path, LOOP)
        ids = _nodes(graph)
        head = next(b for b in session.beats
                    if str(getattr(b, 'loop_header', '') or ''))
        assert head.line_id in session.block_head_line_ids()

        plan = host.plan_wire_landing(
            ids['ch01'], ids['ch02'], position='beat',
            position_line_id=head.line_id)

        assert plan['ok'] is False
        assert '块开头' in plan['reason']

    def test_a_jump_beat_cannot_carry_another_line_behind_it(self, tmp_path):
        session, graph, host, _ = _open(tmp_path, TAIL_JUMP)
        ids = _nodes(graph)
        jump = next(b for b in session.beats
                    if b.label == 'ch01' and b.raw.strip() == 'jump ch02')

        plan = host.plan_wire_landing(
            ids['ch01'], ids['ch02'], position='beat',
            position_line_id=jump.line_id)

        assert plan['ok'] is False
        assert '已经有去向' in plan['reason']

    def test_a_deleted_beat_is_reported_as_stale(self, tmp_path):
        _session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        plan = host.plan_wire_landing(
            ids['ch01'], ids['ch02'], position='beat',
            position_line_id='ch01.9999')

        assert plan['ok'] is False
        assert plan['kind'] == 'stale_position'
        assert '不在这一章' in plan['reason']

    def test_beat_position_without_a_line_id_is_refused(self, tmp_path):
        _session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        plan = host.plan_wire_landing(
            ids['ch01'], ids['ch02'], position='beat', position_line_id='')

        assert plan['ok'] is False
        assert plan['kind'] == 'bad_position'


# ===========================================================================
#  二、下拉框：列哪些、哪些置灰
# ===========================================================================

class TestPositionChoices:
    def test_lists_only_top_level_beats(self, tmp_path):
        session, graph, host, _ = _open(tmp_path, TAIL_IF)
        ids = _nodes(graph)
        data = host.edge_position_choices(ids['ch01'])

        assert data['ok'] is True
        listed = [it['line_id'] for it in data['items']]
        top = [b.line_id for b in session.beats
               if b.label == 'ch01' and not getattr(b, 'nest_path', '')]
        assert listed == top
        assert all(it['selectable'] for it in data['items'])

    def test_marks_a_loop_head_as_not_selectable(self, tmp_path):
        session, graph, host, _ = _open(tmp_path, LOOP)
        ids = _nodes(graph)
        head = next(b for b in session.beats
                    if str(getattr(b, 'loop_header', '') or ''))
        data = host.edge_position_choices(ids['ch01'])
        item = next(it for it in data['items']
                    if it['line_id'] == head.line_id)

        assert item['selectable'] is False
        assert '块开头' in item['note']

    def test_positions_come_from_the_source_file_not_the_open_chapter(
            self, tmp_path):
        """
        用户 2026-10-24 抓到的 bug：从 `chapter0_0` 拖（它是打开的）能选第 N 拍，
        从 `chapter1_2` 拖（没打开）只剩章末 —— 同一张窗口两种表现。

        判据：位置属于**起点章**，不属于"现在打开的那一章"。没打开时只读解析
        源文件，照样列出它的拍；写回时才按需载入。
        """
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        session.beats = [b for b in session.beats if b.label == 'ch02']
        session.script_path = Path(str(session.script_path)).with_name(
            'chapter_other.rpy')

        data = host.edge_position_choices(ids['ch01'])

        assert data['ok'] is True, data
        assert data['loaded'] is False
        assert data['source_path'].endswith('ch01.rpy')
        assert any(it['line_id'].startswith('ch01.') for it in data['items'])
        assert all(it['line_id'].startswith('ch01.') for it in data['items']), \
            '不能把当前打开那一章的拍混进来'

    def test_plan_marks_the_source_chapter_for_loading(self, tmp_path):
        """起点章没打开：计划仍算得出来，但带 `needs_load`，写回前先载入。"""
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        session.beats = [b for b in session.beats if b.label == 'ch02']
        session.script_path = Path(str(session.script_path)).with_name(
            'chapter_other.rpy')

        plan = host.plan_wire_landing(ids['ch01'], ids['ch02'])

        assert plan['ok'] is True, plan
        assert plan['kind'] == 'chapter_end'
        assert plan['needs_load'].endswith('ch01.rpy')


# ===========================================================================
#  三、对话框：选位置、拒绝无效位置
# ===========================================================================

POSITIONS = {'ok': True, 'items': [
    {'line_id': 'A.0001', 'index': 0, 'number': 1, 'total': 3,
     'text': 'e：第一句', 'selectable': True, 'note': '接在第 1 拍之后'},
    {'line_id': 'A.0002', 'index': 1, 'number': 2, 'total': 3,
     'text': 'jump B', 'selectable': False,
     'note': '第 2 拍已经有去向（jump B），后面接不上'},
]}


def _dialog(qapp, plan_fn=None, positions=None, suggest=None):
    g = Graph()
    a = g.add_node('A', 0, 0)
    b = g.add_node('B', 0, 0)
    calls = []

    def _plan(f, t, position='chapter_end', line_id=''):
        calls.append((f, t, position, line_id))
        if plan_fn is not None:
            return plan_fn(position, line_id)
        if position == 'beat':
            return {'ok': True, 'kind': 'beat', 'index': 0, 'after': True,
                    'reason': '接在第 1 拍之后'}
        return {'ok': True, 'kind': 'chapter_end',
                'reason': '插在「A」的 return 之前'}

    dlg = AddEdgeDialog(None, nodes=g.nodes, from_id=a, to_id=b,
                        plan_fn=_plan, positions_fn=lambda _nid: positions,
                        suggest_from_fn=suggest)
    return dlg, calls, a, b


def test_dialog_lists_positions_and_writes_with_the_selected_one(qapp):
    dlg, calls, _a, _b = _dialog(qapp, positions=POSITIONS)

    assert dlg._pos.count() == 3  # 章末 + 两条
    assert dlg._pos.model().item(2).isEnabled() is False, '不能接的拍要置灰'

    dlg._pos.setCurrentIndex(1)
    assert dlg.position == 'beat'
    assert dlg.position_line_id == 'A.0001'
    assert calls[-1][2:] == ('beat', 'A.0001')
    assert dlg.plan.get('ok') is True
    assert _ok(dlg).text() == '创建'

    dlg._on_accept()
    assert dlg.result() == QDialog.Accepted
    assert dlg.write is True
    assert dlg.position == 'beat'
    assert dlg.position_line_id == 'A.0001'


def test_switching_back_to_chapter_end_restores_the_write_mode(qapp):
    def plan(position, _line_id):
        if position == 'beat':
            return {'ok': False, 'kind': 'bad_position', 'reason': '这一拍写不进'}
        return {'ok': True, 'kind': 'chapter_end', 'reason': '插在 return 之前'}

    dlg, _calls, _a, _b = _dialog(qapp, plan_fn=plan, positions=POSITIONS)
    dlg._pos.setCurrentIndex(1)
    assert dlg.position == 'beat'
    assert dlg.plan.get('ok') is False

    dlg._pos.setCurrentIndex(0)
    assert dlg.position == 'chapter_end'
    assert dlg.plan.get('ok') is True


def test_real_window_choices_reach_the_dialog(qapp, tmp_path):
    """接真窗口的 `edge_position_choices`，确认下拉框里真的有这一章的拍。"""
    session, graph, host, _ = _open(tmp_path, TAIL_JUMP)
    ids = _nodes(graph)
    first = next(b for b in session.beats
                 if b.label == 'ch01' and b.kind == 'say')

    dlg = AddEdgeDialog(
        None, nodes=graph.nodes, from_id=ids['ch01'], to_id=ids['ch02'],
        plan_fn=host.plan_wire_landing,
        positions_fn=host.edge_position_choices)

    assert dlg._pos.count() >= 2, '这一章有可接的拍，下拉框却不给选'
    assert dlg._pos.itemData(1) == ('beat', first.line_id)

    dlg._pos.setCurrentIndex(1)
    assert dlg.position == 'beat'
    assert dlg.plan.get('ok') is True
    assert _ok(dlg).text() == '创建'


def test_real_jump_tail_starts_as_a_sketch_then_a_beat_position_unlocks_write(
        qapp, tmp_path):
    """
    P1 的核心体验：章末已有 jump → 一开始写不进（只能草图）；
    **选一个前面的拍**之后就能写，不用关窗重来。
    """
    _session, graph, host, _ = _open(tmp_path, TAIL_JUMP)
    ids = _nodes(graph)
    dlg = AddEdgeDialog(
        None, nodes=graph.nodes, from_id=ids['ch01'], to_id=ids['ch02'],
        plan_fn=host.plan_wire_landing,
        positions_fn=host.edge_position_choices)

    assert dlg._pos.isEnabled() is True
    assert dlg.plan.get('ok') is not True, '章末有 jump，默认写不进'
    assert 'jump' in str(dlg.plan.get('reason') or '')

    dlg._pos.setCurrentIndex(1)  # 第 1 拍（say）
    assert dlg.plan.get('ok') is True
    assert dlg.position == 'beat'

    dlg._on_accept()
    assert dlg.result() == QDialog.Accepted
    assert dlg.write is True
    assert dlg.position == 'beat'


# ===========================================================================
#  四、草图转正：按记下的位置写
# ===========================================================================

class TestSketchPromotion:
    def test_manual_sketch_is_written_where_it_says(self, tmp_path):
        session, graph, host, _ = _open(tmp_path, TAIL_JUMP)
        ids = _nodes(graph)
        first = next(b for b in session.beats
                     if b.label == 'ch01' and b.kind == 'say')
        eid = graph.add_edge(
            ids['ch01'], ids['ch02'], etype='jump', origin='manual',
            position='beat', position_line_id=first.line_id)
        edge = graph.edges[eid]

        host.write_manual_edge(edge)

        raws = [b.raw.strip() for b in session.beats if b.label == 'ch01']
        assert raws[1] == 'jump ch02', raws
        assert eid not in graph.edges, '转正之后草图应当消失'
        assert any('已把草图写成剧本' in s for s in host.status)

    def test_a_sketch_with_a_stale_position_does_not_touch_the_script(
            self, tmp_path, monkeypatch):
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        eid = graph.add_edge(
            ids['ch01'], ids['ch02'], etype='jump', origin='manual',
            position='beat', position_line_id='ch01.9999')
        shown = []
        monkeypatch.setattr(
            window_nodes.QMessageBox, 'information',
            lambda *a, **k: shown.append(a))

        before = [b.raw for b in session.beats]
        host.write_manual_edge(graph.edges[eid])

        assert [b.raw for b in session.beats] == before
        assert eid in graph.edges
        assert shown and '不在这一章' in str(shown[0])


# ===========================================================================
#  五、call 的存档返回点（from）
# ===========================================================================

class TestCallFrom:
    def test_collect_and_generate_from_clauses(self):
        used = collect_from_clauses([
            'call side from _call_side_1',
            'call other from _call_other_2',
            'jump x',
        ])
        assert used == {'_call_side_1', '_call_other_2'}
        assert next_from_clause('B', used) == '_call_B_1'
        assert next_from_clause('B', {'_call_B_1', '_call_B_2'}) == '_call_B_3'
        assert next_from_clause('main.route', set()) == '_call_main_route_1'
        assert from_stem('main.route') == '_call_main_route'

    def test_counts_calls_even_without_an_explicit_from(self):
        """Ren'Py 会给裸 call 自动生成 `_call_X_n`，自动命名要避开这些名字。"""
        counts = call_counts([
            'call side',
            'call side from _call_side_1',
            'call other',
        ])
        assert counts == {'side': 2, 'other': 1}

    def test_apply_wire_writes_the_from_clause(self, tmp_path):
        session, graph, host, _ = _open(tmp_path, TAIL_RETURN)
        ids = _nodes(graph)
        plan = host.plan_wire_landing(ids['ch01'], ids['ch02'])

        res = host._apply_wire({**plan, 'etype': 'call',
                                'from_clause': '_call_ch02_1'})

        assert res.get('ok'), res
        added = session.beats[int(res['index'])]
        assert added.raw.strip() == 'call ch02 from _call_ch02_1'
        edge = next((e for e in graph.edges.values()
                     if e.line_id == added.line_id), None)
        assert edge is not None, '写完 call 之后图上没有派生出一条源边'
        assert edge.from_clause == '_call_ch02_1'

    def test_suggestion_scans_the_project_for_taken_names(self, tmp_path):
        _session, graph, host, root = _open(tmp_path, TAIL_RETURN)
        extra = Path(root) / 'game' / 'script' / 'extra.rpy'
        extra.write_text('label extra:\n    call ch02 from _call_ch02_1\n'
                         '    return\n', encoding='utf-8')
        host._project_root = root

        name = host.suggest_call_from('ch02')

        assert name == '_call_ch02_2', name


# ===========================================================================
#  六、图上的字：位置 + 存档点
# ===========================================================================

class TestEdgeText:
    def test_manual_sketch_label_is_marked_but_position_moves_to_the_window(self):
        edge = SimpleNamespace(
            origin='manual', position='beat', position_line_id='A.0003',
            condition='', option_text='', type='jump', from_clause='')
        info = {'index': 3, 'total': 5, 'label': 'A', 'text': 'x',
                'prev': '', 'next': ''}

        text = edge_label_text(edge, info)

        assert text.startswith('草图')
        assert '第 3 拍' not in text, '落点应由「编辑连线」窗口显示'
        tip = edge_tooltip(edge, info)
        assert '第 3 拍' in tip, '提示里仍要能看见落点'

    def test_call_tooltip_shows_the_archive_point(self):
        edge = SimpleNamespace(
            origin='source', position='beat', line_id='A.0003',
            condition='', option_text='', type='call',
            from_clause='_call_side_1')
        info = {'index': 3, 'total': 5, 'label': 'A', 'text': 'call side',
                'prev': '', 'next': ''}

        tip = edge_tooltip(edge, info)

        assert '存档点：from _call_side_1' in tip
