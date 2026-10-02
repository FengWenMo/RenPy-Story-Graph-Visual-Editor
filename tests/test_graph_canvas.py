"""
tests/test_graph_canvas.py
==========================
剧情结构图（第一屏）的画布层测试。

为什么单独一个文件：这一层承担"把项目结构画出来给人看"，历史上出过
三类**看不见**的问题（界面上不报错，只是变得没法用）：

  1. **排版**：节点从 120×60 放大到 208×104，`layout.py` 的间距却还是
     为旧尺寸调好的 180/60 → 同层相邻的块垂直重叠 36px，"糊成一坨"；
  2. **连线**：画了曲线却没画箭头（方向靠猜），条件/选项文本也不显示；
  3. **内容**：块上直接铺 `operations[:3]`，把 `renpy.transition(...)` 这种
     技术代码堆上去，真正的剧情变量被淹（真实项目一个块挂 55 条）。

这三条都不会抛异常，只能靠断言钉住。
"""

from __future__ import annotations

import pytest

from storygraph.graph.models import Graph

pytest.importorskip('PySide6')


@pytest.fixture
def canvas(qapp):
    from storygraph.ui_qt.graph_canvas import GraphCanvas
    c = GraphCanvas()
    c.resize(900, 700)
    return c


def _graph(n=6):
    g = Graph()
    ids = [g.add_node(f'n{i}', 0, 0) for i in range(n)]
    for i in range(n - 1):
        g.add_edge(ids[i], ids[i + 1])
    return g, ids


# ==================================================================
#  一、排版：间距必须由节点尺寸算出来
# ==================================================================

class TestLayoutGeometry:
    def test_gaps_are_derived_from_node_size(self):
        """间距不许写死 —— 改节点尺寸时它必须跟着变。"""
        from storygraph.ui_qt import graph_canvas as gc

        layer_gap, node_gap = gc._layout_gaps()
        assert layer_gap > gc.NODE_W + 40, '层间距不够，连线会糊在块上'
        assert node_gap > gc.NODE_H + 20, '同层间距不够，块会垂直重叠'

    def test_nodes_never_overlap_on_a_realistic_graph(self, canvas):
        """真实规模的图上，任意两块都不许相交（这正是"排版没了"的判据）。"""
        canvas.set_graph(_graph(24)[0])
        items = list(canvas._items.values())
        bad = []
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if items[i].sceneBoundingRect().intersects(
                        items[j].sceneBoundingRect()):
                    bad.append((items[i].node.label_name,
                                items[j].node.label_name))
        assert not bad, f'这些块重叠了：{bad}'

    def test_same_layer_nodes_are_a_full_gap_apart(self, canvas):
        """同层相邻块的 y 间距 >= 节点高度（自环偏移曾经正好压掉这一半）。"""
        canvas.set_graph(_graph(16)[0])
        from storygraph.ui_qt.graph_canvas import NODE_H

        by_x: dict = {}
        for item in canvas._items.values():
            by_x.setdefault(round(item.node.x, 3), []).append(item.node.y)
        for x, ys in by_x.items():
            ys.sort()
            for a, b in zip(ys, ys[1:]):
                assert b - a >= NODE_H, \
                    f'x={x} 处同层两块只差 {b - a}px（块高 {NODE_H}）'

    def test_saved_positions_are_kept_when_every_node_is_placed(self, canvas):
        """每个块都有各自的坐标（含用户拖过的）→ 不许被自动布局冲掉。"""
        g, ids = _graph(3)
        for i, nid in enumerate(ids):
            g.nodes[nid].x, g.nodes[nid].y = 100 + i * 400, 250
        canvas.set_graph(g)
        assert [(g.nodes[n].x, g.nodes[n].y) for n in ids] == \
            [(100, 250), (500, 250), (900, 250)]

    def test_partially_unplaced_graph_is_laid_out(self, canvas):
        """
        旧判据是"所有块都在同一点才排版"，漏掉这一半：

        先导入第一章（排好了），再导入第二章时新块落在默认坐标上 ——
        旧判据看到"位置并不都相同"就以为布局已存在，于是**不排版**，
        新块直接压在旧块身上。只要还有块没摆过就必须重排。
        """
        g, ids = _graph(3)
        g.nodes[ids[0]].x, g.nodes[ids[0]].y = 1000, 800
        # ids[1]、ids[2] 还停在解析器默认坐标 (200, 200)
        canvas.set_graph(g)
        pts = [(g.nodes[n].x, g.nodes[n].y) for n in ids]
        assert len(set(pts)) == 3, f'还有块叠在一起：{pts}'

    def test_new_node_at_parser_default_forces_relayout(self, canvas):
        """累加导入：新块落在 (200,200) 时，整图重排（不会压住别人）。"""
        g, ids = _graph(2)
        for i, nid in enumerate(ids):
            g.nodes[nid].x, g.nodes[nid].y = i * 400, 0
        canvas.set_graph(g)
        # 模拟"又导入了一章"：解析器把新 label 建成默认坐标
        new_id = g.add_node('ch_new', 200, 200)
        canvas.set_graph(g)
        items = list(canvas._items.values())
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                assert not items[i].sceneBoundingRect().intersects(
                    items[j].sceneBoundingRect()), '新导入的块压在别人身上'


# ==================================================================
#  二、连线：箭头 + 语义标签
# ==================================================================

class TestEdges:
    def test_every_edge_draws_an_arrow(self, canvas):
        g, ids = _graph(3)
        canvas.set_graph(g)
        assert canvas._edges, '没建出连线'
        for ei in canvas._edges:
            assert ei._arrow is not None and ei._arrow.count() == 3, \
                '连线没有箭头（方向只能靠猜）'

    def test_edge_label_says_option_or_condition(self, canvas):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(a, b, etype='menu', option_text='去图书馆')
        g.add_edge(b, c, etype='jump', condition='love >= 5')
        canvas.set_graph(g)
        labels = [ei._label.text() for ei in canvas._edges]
        assert any('去图书馆' in t for t in labels), labels
        assert any('love >= 5' in t for t in labels), labels
        assert all(ei._label.isVisible() for ei in canvas._edges), \
            '有标签却没显示'

    def test_self_loop_does_not_crash_and_keeps_a_path(self, canvas):
        g = Graph()
        a = g.add_node('a', 0, 0)
        g.add_edge(a, a)
        canvas.set_graph(g)
        ei = canvas._edges[0]
        assert not ei.path().isEmpty(), '自环没画出路径'
        assert ei._arrow.count() == 3

    def test_dragging_a_node_moves_its_edges(self, canvas):
        """
        拖块时连线必须跟着走。

        第一版只把坐标写回模型（`itemChange`），没有刷新连线 ——
        拖一下所有线都留在原地，图立刻变得没法看。
        """
        g, ids = _graph(2)
        canvas.set_graph(g)
        ei = canvas._edges[0]
        before = ei.path().boundingRect()
        canvas._items[ids[0]].setPos(500, 400)
        after = ei.path().boundingRect()
        assert before != after, '拖动块之后连线没跟着走'

    def test_endpoints_are_block_centers_not_edges(self, canvas):
        """
        用户 2026-10-03：

        > 把箭头放在中间，把连线点放在块的里面而不是边缘

        端点在块的**中心**，而块的填充不透明、边的 z 更低 ——
        视觉上连线就是从块里面连出来的。
        """
        g, ids = _graph(2)
        canvas.set_graph(g)
        ei = canvas._edges[0]
        for end, nid in ((ei._p1, ids[0]), (ei._p2, ids[1])):
            c = canvas._items[nid].sceneBoundingRect().center()
            assert abs(end.x() - c.x()) < 0.01 and abs(end.y() - c.y()) < 0.01, \
                f'端点不在块中心：{end} vs {c}'

    def test_arrow_sits_at_the_midpoint(self, canvas):
        """箭头画在连线**中点**（不是终点）。"""
        g, ids = _graph(2)
        canvas.set_graph(g)
        ei = canvas._edges[0]
        mid_x = (ei._p1.x() + ei._p2.x()) / 2
        mid_y = (ei._p1.y() + ei._p2.y()) / 2
        ax = sum(p.x() for p in ei._arrow) / 3
        ay = sum(p.y() for p in ei._arrow) / 3
        assert abs(ax - mid_x) < 8 and abs(ay - mid_y) < 8, \
            f'箭头不在中点：({ax:.0f},{ay:.0f}) vs ({mid_x:.0f},{mid_y:.0f})'

    def test_edges_are_straight_lines(self, canvas):
        """直线（不再用大圈贝塞尔），所以路径的包围盒就是两端点的矩形。"""
        g, ids = _graph(2)
        canvas.set_graph(g)
        ei = canvas._edges[0]
        r = ei.path().boundingRect()
        assert r.width() <= abs(ei._p2.x() - ei._p1.x()) + 2
        assert r.height() <= abs(ei._p2.y() - ei._p1.y()) + 2


# ==================================================================
#  三、块上的内容：给人看的摘要，不是技术代码
# ==================================================================

class TestNodeContent:
    def test_summary_counts_only_narrative_variables(self, canvas):
        """
        块上只讲**剧情变量**。

        用户 2026-10-03：
        > 这个所谓的"技术代码"，它不是变量，是具体的实现细节！……
        > 编辑变量是为了让策划和编剧调整和编排剧情分支用的地方！
        """
        g = Graph()
        nid = g.add_node('ch1', 0, 0, operations=[
            'love += 1', 'flag = True',
            'renpy.transition(Dissolve(0.1))', 'persistent.x = True'])
        canvas.set_graph(g)
        item = canvas._items[nid]
        assert item._summary.text() == '2 剧情变量', item._summary.text()

    def test_technical_code_is_not_shown_on_the_block(self, canvas):
        """只有实现细节的块，摘要**什么都不显示**（连条数都不显示）。"""
        g = Graph()
        nid = g.add_node('ch1', 0, 0, operations=[
            'renpy.transition(Dissolve(0.1), layer="master")'])
        canvas.set_graph(g)
        item = canvas._items[nid]
        shown = ' '.join(t.text() for t in (item._title, item._meta,
                                           item._summary, item._note))
        assert 'renpy.transition' not in shown, shown
        assert '技术' not in shown and '代码' not in shown, shown
        assert item._summary.text() == '', item._summary.text()

    def test_node_shows_source_file_and_line(self, canvas):
        g = Graph()
        nid = g.add_node('ch1', 0, 0, file_path='D:/p/game/script/ch01.rpy',
                         line_number=42)
        canvas.set_graph(g)
        meta = canvas._items[nid]._meta.text()
        assert 'ch01.rpy' in meta and '42' in meta, meta

    def test_long_names_are_elided_not_overflowed(self, canvas):
        g = Graph()
        nid = g.add_node('很长很长很长' * 20, 0, 0)
        canvas.set_graph(g)
        title = canvas._items[nid]._title.text()
        assert len(title) < len('很长很长很长' * 20), '长名字没有被截断'
        assert title.endswith('…'), title

    def test_tooltip_lists_narrative_variables_only(self, canvas):
        """提示里也只有剧情变量（技术代码的名字不许出现在变量相关的地方）。"""
        g = Graph()
        nid = g.add_node('ch1', 0, 0, operations=['love += 1',
                                                  'renpy.notify("x")'])
        canvas.set_graph(g)
        tip = canvas._items[nid].toolTip()
        assert 'love += 1' in tip
        assert '剧情变量' in tip
        assert 'renpy.notify' not in tip, '技术代码的名字漏进了变量提示'


class TestOldInteractionModel:
    """
    操作**必须照 tkinter 版**（用户 2026-10-03）：

    > 明明 shift+鼠标左键是连线图，左键也可以直接选中模块进行变量赋值和操作……
    > 为什么你非要改到右键呢？你要遵照我旧版的操作来！不要乱改……

    这一组是"按键 → 行为"的契约（真发鼠标事件，不直接调方法）——
    上一版正是在这里把操作改掉的（双击变"进卡片"、滚轮要 Ctrl、连线挪到右键）。
    """

    @staticmethod
    def _view_pos(canvas, nid):
        """块中心 → 视口坐标（发鼠标事件用）。"""
        from PySide6.QtCore import QPointF
        r = canvas._items[nid].sceneBoundingRect()
        return canvas.mapFromScene(QPointF(r.center().x(), r.center().y()))

    def test_scene_rect_keeps_room_for_panning(self, canvas):
        """
        场景矩形要在内容四周留余量 —— 否则"拖画布没反应"。

        用户 2026-10-05：
        > 有个拖拽画布没反应的bug你忘记修啦！

        根因不在手势（空格+左键 / 中键两条路都对），而在**场景矩形恰好等于
        内容+40px**：小图 `fitInView` 后内容刚好铺满视口 → 滚动条范围≈0 →
        拖了也没有东西可动。
        """
        g, ids = _graph(3)
        canvas.set_graph(g)
        canvas.resize(900, 600)
        content = canvas.scene().itemsBoundingRect()
        scene = canvas.sceneRect()
        assert scene.width() > content.width() * 1.5, \
            f'场景矩形没留横向余量：{scene} vs 内容 {content}'
        assert scene.height() > content.height() * 1.5, \
            f'场景矩形没留纵向余量：{scene} vs 内容 {content}'
        # 适应窗口之后仍然要有可滚动的空间
        canvas.fit_to_content()
        hs = canvas.horizontalScrollBar()
        assert hs.maximum() > hs.minimum(), '适应窗口后横向上没有可拖动的空间'

    def test_dragging_a_node_reports_the_move(self, canvas):
        """
        拖完一个块要发 `node_moved(nid, 原 x, 原 y, 新 x, 新 y)`。

        用户 2026-10-05："将撤销做好" —— 没有这个信号，拖动就进不了撤销栈
        （模型坐标在 `itemChange` 里已经改了，撤销栈无从知道"原来在哪"）。
        """
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        g, ids = _graph(2)
        canvas.set_graph(g)
        seen = []
        canvas.node_moved.connect(
            lambda nid, ox, oy, nx, ny: seen.append((nid, ox, oy, nx, ny)))
        before = (float(canvas._items[ids[0]].pos().x()),
                  float(canvas._items[ids[0]].pos().y()))
        vp = canvas.viewport()
        start = self._view_pos(canvas, ids[0])
        QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(vp, start + QPoint(60, 40))
        QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier,
                           start + QPoint(60, 40))
        assert seen, '拖动没有发出 node_moved'
        nid, ox, oy, nx, ny = seen[-1]
        assert nid == ids[0]
        assert (ox, oy) == before, f'记录的"原位置"不对：{(ox, oy)} vs {before}'
        assert (nx, ny) != (ox, oy), '新位置与原位置相同，等于没动'

    def test_shift_left_drag_creates_a_connection(self, canvas):
        """**Shift + 左键**从一个块拖到另一个块 → `connect_requested(A, B)`。"""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        g, ids = _graph(2)
        canvas.set_graph(g)
        seen = []
        canvas.connect_requested.connect(lambda a, b: seen.append((a, b)))
        vp = canvas.viewport()
        a, b = self._view_pos(canvas, ids[0]), self._view_pos(canvas, ids[1])
        QTest.mousePress(vp, Qt.LeftButton, Qt.ShiftModifier, a)
        QTest.mouseMove(vp, b)
        QTest.mouseRelease(vp, Qt.LeftButton, Qt.ShiftModifier, b)
        assert seen == [(ids[0], ids[1])], f'Shift+左键拖动没连成线：{seen}'
        assert canvas._temp_line is None, '临时红线没清掉'

    def test_plain_left_click_selects_without_connecting(self, canvas):
        """普通左键 = 只选中（旧版 `on_left_down`），不许触发连线。"""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        g, ids = _graph(2)
        canvas.set_graph(g)
        seen, picked = [], []
        canvas.connect_requested.connect(lambda a, b: seen.append((a, b)))
        canvas.selection_changed.connect(picked.append)
        vp = canvas.viewport()
        a, b = self._view_pos(canvas, ids[0]), self._view_pos(canvas, ids[1])
        QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, a)
        QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, b)
        assert not seen, '普通左键拖动**不该**连线（那是 Shift 的活）'
        assert canvas.selected_node_id() == ids[0]
        assert ids[0] in picked

    def test_double_click_on_node_asks_for_variable_editing(self, canvas):
        """**双击块 = 编辑变量操作**（旧版 `edit_operations_dialog`），不是进卡片。"""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        g, ids = _graph(2)
        canvas.set_graph(g)
        seen = []
        canvas.node_ops_requested.connect(seen.append)
        QTest.mouseDClick(canvas.viewport(), Qt.LeftButton, Qt.NoModifier,
                          self._view_pos(canvas, ids[1]))
        assert seen == [ids[1]], seen

    def test_double_click_on_empty_asks_for_a_new_node(self, canvas):
        """双击空白 = 在那个位置新建章节（旧版行为）。"""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        canvas.set_graph(_graph(1)[0])
        seen = []
        canvas.node_add_requested.connect(lambda x, y: seen.append((x, y)))
        QTest.mouseDClick(canvas.viewport(), Qt.LeftButton, Qt.NoModifier,
                          canvas.mapFromScene(600.0, 600.0))
        assert seen, '双击空白没有请求新建章节'

    def test_wheel_zooms_without_ctrl(self, canvas):
        """**滚轮直接缩放**（旧版 `on_mousewheel` 里没有修饰键判断）。"""
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent

        canvas.set_graph(_graph(3)[0])
        before = canvas.zoom_percent()
        ev = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0),
                         QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                         Qt.ScrollPhase.NoScrollPhase, False)
        canvas.wheelEvent(ev)
        assert canvas.zoom_percent() > before, '不按 Ctrl 滚轮没缩放'

    def test_left_drag_moves_a_node(self, canvas):
        """左键拖动块 = 移动位置（旧版 `on_left_drag` 的 drag_data）。"""
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        g, ids = _graph(2)
        canvas.set_graph(g)
        item = canvas._items[ids[0]]
        before = (item.node.x, item.node.y)
        vp = canvas.viewport()
        start = self._view_pos(canvas, ids[0])
        QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(vp, start + QPoint(80, 60))
        QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier,
                           start + QPoint(80, 60))
        assert (item.node.x, item.node.y) != before, '左键拖动没有移动块'

    def test_zoom_percent_label_updates(self, canvas):
        """缩放要给用户看得见的百分比（工具栏那个标签读它）。"""
        seen = []
        canvas.zoom_changed.connect(seen.append)
        canvas.set_graph(_graph(2)[0])
        canvas.zoom_in()
        assert seen and seen[-1] == canvas.zoom_percent()


# ==================================================================
#  四、静态分析结论画回图上
# ==================================================================

class TestAnalysisMarks:
    def test_marks_are_set_and_cleared(self, canvas):
        g, ids = _graph(3)
        canvas.set_graph(g)
        canvas.set_analysis(entry_id=ids[0], problem_ids=[ids[2]],
                            independent_ids=[ids[1]])
        assert canvas._items[ids[2]].mark == 'stranded'
        assert canvas._items[ids[1]].mark == 'independent'
        assert canvas.entry_id == ids[0]
        canvas.clear_analysis()
        assert canvas.entry_id is None
        assert all(it.mark is None for it in canvas._items.values())

    def test_problem_node_is_visibly_labeled(self, canvas):
        g, ids = _graph(2)
        canvas.set_graph(g)
        canvas.set_analysis(problem_ids=[ids[1]])
        note = canvas._items[ids[1]]._note.text()
        assert '走不到' in note, note
        assert canvas._items[ids[1]]._note.isVisible()


# ==================================================================
#  五、视口 / 查找 / 空状态
# ==================================================================

class TestViewport:
    def test_zoom_is_clamped(self, canvas):
        from storygraph.ui_qt.graph_canvas import ZOOM_MAX, ZOOM_MIN
        g, _ = _graph(3)
        canvas.set_graph(g)
        for _ in range(40):
            canvas.zoom_in()
        assert canvas.zoom_percent() <= int(ZOOM_MAX * 100) + 1
        for _ in range(60):
            canvas.zoom_out()
        assert canvas.zoom_percent() >= int(ZOOM_MIN * 100) - 1

    def test_zoom_signal_reports_percent(self, canvas):
        g, _ = _graph(2)
        canvas.set_graph(g)
        seen = []
        canvas.zoom_changed.connect(seen.append)
        canvas.zoom_in()
        assert seen and seen[-1] > 100

    def test_big_graph_starts_at_100_percent_not_fitted(self, canvas):
        """
        50 多个块硬 fit 进窗口会缩到 0.4 以下，字全成蚂蚁 ——
        所以"装不下就 100% 停在入口块"，而不是无脑适应窗口。
        """
        canvas.set_graph(_graph(60)[0])
        assert canvas.zoom_percent() == 100, canvas.zoom_percent()

    def test_find_nodes_matches_name_and_file(self, canvas):
        g = Graph()
        a = g.add_node('chapter1_1', 0, 0, file_path='x/chapter1.rpy')
        b = g.add_node('mini_game', 0, 0, file_path='x/mini.rpy')
        canvas.set_graph(g)
        assert canvas.find_nodes('chapter1') == [a]
        assert canvas.find_nodes('mini.rpy') == [b]
        assert set(canvas.find_nodes('')) == {a, b}

    def test_empty_hint_is_drawn_only_without_nodes(self, canvas):
        canvas.set_graph(None)
        canvas.set_empty_hint('① 打开项目')
        assert canvas._empty_hint


# ==================================================================
#  六、重建视图时**保住缩放**（用户 2026-10-05）
# ==================================================================

class TestViewIsKeptAcrossRebuild:
    """
    用户 2026-10-05：

    > 连线之后，请保持原来的缩放……现在每连一个线，缩放就变……这不好

    图的重建只有一个入口（`set_graph`：连线 / 撤销 / 改内容都走它），
    以前它**无条件** `_reset_view()` —— 于是每连一条线、每撤销一步，
    缩放和视口位置都被重新"适应窗口"。
    """

    def test_zoom_and_center_survive_a_rebuild(self, canvas):
        from PySide6.QtCore import QPointF

        g, ids = _graph(4)
        canvas.set_graph(g)
        canvas.zoom_by(2.0)                     # 用户放大到看得清某一层
        canvas.centerOn(QPointF(400, 300))
        before_zoom = canvas.zoom_percent()
        before_center = canvas.mapToScene(canvas.viewport().rect().center())

        canvas.set_graph(g)                     # 同一张图重建（= 连了一条线）

        assert canvas.zoom_percent() == before_zoom, \
            f'重建后缩放变了：{before_zoom} → {canvas.zoom_percent()}'
        after_center = canvas.mapToScene(canvas.viewport().rect().center())
        assert abs(after_center.x() - before_center.x()) < 2, \
            f'重建后视口中心漂了：{before_center} → {after_center}'
        assert abs(after_center.y() - before_center.y()) < 2, \
            f'重建后视口中心漂了：{before_center} → {after_center}'

    def test_new_unplaced_nodes_still_trigger_a_fit(self, canvas):
        """新导入的章节落在默认坐标 → 要重排 + 重新适应窗口（不能"保住"旧视图）。"""
        import storygraph.ui_qt.graph_canvas as gc

        g, ids = _graph(3)
        canvas.set_graph(g)
        canvas.zoom_by(2.5)
        # 新块用解析器的默认坐标（"还没摆过"的记号）
        new_id = g.add_node('chapter_new', gc.DEFAULT_POS[0], gc.DEFAULT_POS[1])
        canvas.set_graph(g)
        assert canvas._unplaced_ids() == set(), '新块没有被排版'
        pos = canvas._items[new_id].pos()
        assert (round(pos.x(), 3), round(pos.y(), 3)) != gc.DEFAULT_POS, \
            '新块还压在默认坐标上'
        assert not canvas._items[new_id].sceneBoundingRect().intersects(
            canvas._items[ids[0]].sceneBoundingRect()), '新块和旧块叠在一起'

    def test_explicit_keep_view_false_resets(self, canvas):
        """调用方可以说"别保、给我适应窗口"（加载布局 JSON 就是这种）。"""
        g, _ = _graph(4)
        canvas.set_graph(g)
        canvas.zoom_by(2.9)                     # 放大到接近上限
        zoomed = canvas.zoom_percent()
        assert zoomed > 150, zoomed
        canvas.set_graph(g, keep_view=False)
        assert canvas.zoom_percent() < zoomed, \
            f'keep_view=False 没有复位视图：还是 {canvas.zoom_percent()}%'

    def test_first_graph_is_fitted_not_kept(self, canvas):
        """第一次装图时没有"原来的视图"可保 —— 必须走适应/100% 那条老路。"""
        g, _ = _graph(3)
        canvas.set_graph(g)
        assert canvas.zoom_percent() > 0


class TestInteractionCanBeInterrupted:
    """
    手势状态卡住 = "拖拽画布时灵时不灵"的另一个来源。

    窗口失活（Alt+Tab / 弹出别的程序）时**鼠标释放与空格抬起都收不到**：
    `_panning` 卡在 True，之后每次左键拖块都变成平移；`_connect_from` 卡住，
    临时红线一直挂着。所以窗口必须能显式打断。
    """

    def test_cancel_interaction_clears_a_stale_pan(self, canvas):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        canvas.set_graph(_graph(2)[0])
        vp = canvas.viewport()
        # 空格 + 左键按下 → 进入平移，然后**不发释放**（模拟失活丢了 release）
        canvas.set_space_down(True)
        QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, QPoint(400, 300))
        assert canvas._panning is True
        canvas.cancel_interaction()
        assert canvas._panning is False, '平移状态卡住了'
        assert canvas._pan_start is None

    def test_cancel_interaction_drops_the_temp_connect_line(self, canvas):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        g, ids = _graph(2)
        canvas.set_graph(g)
        vp = canvas.viewport()
        start = TestOldInteractionModel._view_pos(canvas, ids[0])
        QTest.mousePress(vp, Qt.LeftButton, Qt.ShiftModifier, start)
        QTest.mouseMove(vp, QPoint(start.x() + 40, start.y() + 40))
        assert canvas._temp_line is not None
        canvas.cancel_interaction()
        assert canvas._temp_line is None, '临时红线没清掉'
        assert canvas._connect_from is None

    def test_scene_rect_grows_when_a_node_is_dragged_far(self, canvas):
        """
        块被拖远了 → 场景矩形要跟着长大。

        可平移范围由场景矩形决定：不长大，那块就"看得见、到不了"
        （拖画布到头也追不上）—— 这就是"时灵时不灵"的一种。
        """
        g, ids = _graph(2)
        canvas.set_graph(g)
        before = canvas.sceneRect()
        canvas._items[ids[0]].setPos(4000, 3000)   # 拖到很远
        canvas.note_content_changed()
        assert canvas.sceneRect().width() > before.width()
        assert canvas.sceneRect().contains(canvas._items[ids[0]].sceneBoundingRect()), \
            '拖远的块不在场景矩形里 —— 平移追不上它'

    def test_middle_button_drag_pans(self, canvas):
        """中键拖动 = 平移（旧版另一条路，`QTest` 对中键不可靠，手工发事件）。"""
        from PySide6.QtCore import QEvent, QPoint, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication

        canvas.set_graph(_graph(3)[0])
        vp = canvas.viewport()
        # 先把滚动条放到中间：`fitInView` 之后它往往正好贴在角上，
        # 那个方向已经到头了（不是"平移没生效"）
        hs, vs = canvas.horizontalScrollBar(), canvas.verticalScrollBar()
        for sb in (hs, vs):
            sb.setValue((sb.minimum() + sb.maximum()) // 2)
        pt = QPoint(vp.width() // 2, vp.height() // 2)
        h0, v0 = hs.value(), vs.value()
        seq = ((QEvent.MouseButtonPress, Qt.MiddleButton, Qt.MiddleButton,
                pt),
               (QEvent.MouseMove, Qt.NoButton, Qt.MiddleButton,
                pt + QPoint(-60, -40)),
               (QEvent.MouseButtonRelease, Qt.MiddleButton, Qt.NoButton,
                pt + QPoint(-60, -40)))
        for kind, btn, buttons, pos in seq:
            QApplication.sendEvent(
                vp, QMouseEvent(kind, pos, vp.mapToGlobal(pos), btn, buttons,
                                Qt.NoModifier))
        assert (hs.value(), vs.value()) != (h0, v0), '中键拖动没有平移画布'
        assert canvas._panning is False, '中键松开后 _panning 没复位'


class TestInfiniteStage:
    """
    用户 2026-10-05：

    > 我明白了……这个拖不动是因为你的这个舞台有限……可是我的tkinter做的是
    > 无限的舞台……（目前我不能整个平移）所以说需要继续修改……

    旧版 tkinter 是"世界坐标 + offset"自己画的，**没有边界**。Qt 版用
    `QGraphicsView`，可平移范围由 `sceneRect` 决定 —— 于是舞台被内容框死。
    现在改成"**可见区碰边就把矩形往外长一截**"（`_extend_scene_rect`），
    体感上与无限等价，且长的时候把视口摁住不动（画面不跳）。
    """

    @staticmethod
    def _pan(canvas, dx, dy):
        """按空格 + 左键拖一次（真鼠标事件），返回视口中心的场景位移。"""
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        vp = canvas.viewport()
        canvas.set_space_down(True)
        c0 = canvas.mapToScene(vp.rect().center())
        start = QPoint(vp.width() // 2, vp.height() // 2)
        QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, start)
        for i in (4, 7, 10):
            QTest.mouseMove(vp, start + QPoint(dx * i // 10, dy * i // 10))
        QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier,
                           start + QPoint(dx, dy))
        canvas.set_space_down(False)
        c1 = canvas.mapToScene(vp.rect().center())
        return c1.x() - c0.x(), c1.y() - c0.y()

    def test_panning_never_hits_a_wall(self, canvas):
        """连着拖十几次都不许"拖到头"（旧版就是无限的）。"""
        canvas.show()
        canvas.set_graph(_graph(3)[0])
        stuck, total = [], 0.0
        for n in range(12):
            mx, my = self._pan(canvas, -150, -110)
            total += abs(mx) + abs(my)
            if abs(mx) < 1 and abs(my) < 1:
                stuck.append(n)
        assert stuck == [], f'第 {stuck} 次拖不动了（舞台到边界了）'
        assert total > 12 * 100, f'累计位移只有 {total:.0f}，像是被夹住了'

    def test_scene_rect_grows_while_panning(self, canvas):
        """撑大的结果：可见区始终在场景矩形**里面**（不会追不上内容）。"""
        canvas.show()
        canvas.set_graph(_graph(3)[0])
        before = canvas.sceneRect()
        for _ in range(6):
            self._pan(canvas, -150, -110)
        grown = canvas.sceneRect()
        assert grown.width() > before.width() or grown.height() > before.height()
        vis = canvas.mapToScene(canvas.viewport().rect()).boundingRect()
        assert grown.contains(vis), f'可见区跑到场景矩形外面了：{vis}'

    def test_panning_works_when_zoomed_far_out(self, canvas):
        """缩得很小时可视区比场景矩形还大 —— 那时也必须能拖（"整个平移"）。"""
        canvas.show()
        canvas.set_graph(_graph(3)[0])
        canvas.zoom_by(0.05)                     # 缩到下限
        mx, my = self._pan(canvas, -150, -110)
        assert (abs(mx) + abs(my)) > 10, '缩小时拖不动画布'

    def test_extending_does_not_jump_the_view(self, canvas):
        """撑舞台时视口**不许跳**：`_extend_scene_rect` 前后中心必须是同一个点。"""
        canvas.show()
        canvas.set_graph(_graph(3)[0])
        canvas.zoom_by(0.2)
        for _ in range(4):
            self._pan(canvas, -150, -110)
        before = canvas.mapToScene(canvas.viewport().rect().center())
        grew = canvas._extend_scene_rect()
        after = canvas.mapToScene(canvas.viewport().rect().center())
        assert grew in (True, False)
        assert abs(after.x() - before.x()) < 1 and abs(after.y() - before.y()) < 1, \
            f'撑舞台把视口顶跑了：{before} → {after}'
        canvas.set_graph(_graph(2)[0])
        assert canvas._items
