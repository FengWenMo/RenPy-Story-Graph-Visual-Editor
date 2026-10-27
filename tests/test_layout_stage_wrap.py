"""
tests/test_layout_stage_wrap.py
===============================
「折进舞台」的两种折法（2026-10-27，两轮）。

第一轮用户说：

> 自动布局的逻辑不太行……仍然需要修改（因为现在有些太长了，你应该使得
> 尽可能多的标签整齐排列在舞台内部），而不是一路长排下去……

量出来的"老样子"——两个数说的是同一件事：布局只朝**一个方向**长，而舞台有边：

    ▤ 按文件顺序排列   12334 × 2650   44 列并排，一屏只看得到 4 个块
    ⤢ 自动布局          4156 × 2918   15 层并排，一屏看得到 6 个

第一轮把两条路都折成了"格子"（`_flow_into_pages`）。第二轮用户否掉了
**自动布局**那半边：

> 这个自动布局不好！！！因为无法体现结构……你还是改回去吧（原来那个比现在
> 要好得多，因为展现了结构流程图）我的意思其实是：能不能在保持尽量横向编辑
> 的流程的同时，自动实现感知到头就拐弯呢……？（分叉一类的还是并排放）

于是两条路**分开**：

  · `layout_by_file` —— 像排字一样折（一列 6 块、一页 3 列），块与块之间只
    有先后关系，按格子平铺不丢信息；
  · `layout_graph` —— **层还是左右排开**，只在碰到舞台右边时拐到下面一排，
    下一排反着走（蛇形）。同一层的分叉仍然竖着挨在一起。

本文件的判据：

  ① 文件顺序：一列排满换右边、一页排满换下面；同文件同列、从上到下正序；
  ② 自动布局：层与层左右排开、到右边才拐弯、拐弯后同一列接得上、
     分叉不被打散、块不重叠、整图不超舞台宽；
  ③ 不传 `stage` 时**逐字节保持老布局**（纯数据调用 / 老用例走这条）；
  ④ 画布真的把**可视区**传下去了，且装不下时从左上角开始看；
  ⑤ 两套摘要文案说的是同一件事（状态栏 / 控制台共用）。
"""

from __future__ import annotations

import pytest

from storygraph.graph.models import Graph
from storygraph.graph.layout import (DEFAULT_STAGE, describe_bands,
                                     describe_wrap, layout_by_file,
                                     layout_graph)

#: 与 `ui_qt.graph_canvas._layout_gaps()` 一致（块 208×104 + 间距 74/30）
COLW, ROWH = 282, 134
NODE_W, NODE_H = 208, 104


# ----------------------------------------------------------------------
#  小工具
# ----------------------------------------------------------------------

def _chain(n: int, prefix: str = 'n') -> Graph:
    """n 个块一条链（一层放一个 → 老布局就是并排 n 列）。"""
    g = Graph()
    ids = [g.add_node(f'{prefix}{i}', 0, 0) for i in range(n)]
    for a, b in zip(ids, ids[1:]):
        g.add_edge(a, b)
    return g


def _fan(n: int) -> Graph:
    """一个入口分出 n 个分支（同一层 n 个块）。"""
    g = Graph()
    root = g.add_node('root', 0, 0)
    for i in range(n):
        g.add_edge(root, g.add_node(f'b{i}', 0, 0))
    return g


def _one_file(n: int, name: str = 'big.rpy') -> Graph:
    """一个 `.rpy` 里 n 个 label（行号 1, 11, 21 …）。"""
    g = Graph()
    for i in range(n):
        nid = g.add_node(f'L{i:02d}', 0, 0)
        g.nodes[nid].file_path = name
        g.nodes[nid].line_number = i * 10 + 1
    return g


def _pos(graph) -> dict:
    """`{label: (x, y)}` —— 顺手钉住"块不许重叠"。"""
    out = {}
    nodes = list(graph.nodes.values())
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            assert not (abs(a.x - b.x) < NODE_W - 0.5
                        and abs(a.y - b.y) < NODE_H - 0.5), (
                f'{a.label_name} 和 {b.label_name} 压在一起了：'
                f'({a.x}, {a.y}) / ({b.x}, {b.y})')
    for node in nodes:
        out[node.label_name] = (node.x, node.y)
    return out


def _grid(graph) -> dict:
    """`{(列, 行): label}` —— 按坐标反推格子（只给"按文件顺序"那条路用）。"""
    out = {}
    for node in graph.nodes.values():
        col = round(node.x / COLW)
        row = round(node.y / ROWH)
        assert abs(node.x - col * COLW) < 0.5, \
            f'{node.label_name} 的 x 不在格子点上：{node.x}'
        assert abs(node.y - row * ROWH) < 0.5, \
            f'{node.label_name} 的 y 不在格子点上：{node.y}'
        assert (col, row) not in out, (
            f'两个块占了同一个格子：{out[(col, row)]} / {node.label_name}')
        out[(col, row)] = node.label_name
    return out


def _columns(grid: dict) -> dict:
    """`{列: [从上到下的 label]}`。"""
    cols: dict = {}
    for (col, row), name in grid.items():
        cols.setdefault(col, []).append((row, name))
    return {col: [name for _r, name in sorted(items)]
            for col, items in cols.items()}


# ----------------------------------------------------------------------
#  ① 按文件顺序：排满一列换右边、排满一页换下面
# ----------------------------------------------------------------------

class TestFileLayoutPacks:
    def test_first_page_is_full(self):
        """65 个块、一页 3×6 —— 第一页必须**装满 18 个**（不是空着几格）。"""
        g = _chain(65)
        info = layout_by_file(g, col_gap=COLW, row_gap=ROWH, stage=(990, 847))
        cols, rows = info['grid']
        grid = _grid(g)
        first = [name for (c, r), name in grid.items() if r < rows]
        assert len(first) == cols * rows, (
            f'第一页只摆了 {len(first)} 个块（{cols}×{rows} 的格子）')

    def test_pages_go_down_not_right(self):
        g = _chain(65)
        info = layout_by_file(g, col_gap=COLW, row_gap=ROWH, stage=(990, 847))
        _cols, rows = info['grid']
        grid = _grid(g)
        assert info['pages'] > 1
        page2 = [r for (c, r) in grid if c == 0 and r >= rows]
        assert page2, '第 2 页没有落在 x=0 这一列上 —— 页是往右排的'
        assert min(page2) == rows

    def test_grid_is_computed_not_hardcoded(self):
        small = layout_by_file(_chain(20), col_gap=COLW, row_gap=ROWH,
                               stage=(640, 400))
        assert small['grid'] == (640 // COLW, 400 // ROWH)
        big = layout_by_file(_chain(20), col_gap=COLW, row_gap=ROWH,
                             stage=(1600, 1000))
        assert big['grid'] == (1600 // COLW, 1000 // ROWH)
        assert big['grid'][0] > small['grid'][0]

    def test_a_files_labels_are_top_down_in_one_column(self):
        g = _one_file(4)
        info = layout_by_file(g, col_gap=COLW, row_gap=ROWH, stage=(990, 847))
        assert info['pages'] == 1
        cols = _columns(_grid(g))
        assert len(cols) == 1, '一个文件的 4 个 label 被拆到了几列'
        assert cols[0] == ['L00', 'L01', 'L02', 'L03']

    def test_long_file_continues_in_the_next_column(self):
        """22 个 label / 一列只放得下 6 个 → 每 6 个占一列，列**内**仍然正序。"""
        g = _one_file(22)
        info = layout_by_file(g, col_gap=COLW, row_gap=ROWH,
                              stage=(4 * COLW, 6 * ROWH))
        assert info['grid'] == (4, 6), info
        assert info['pages'] == 1
        cols = _columns(_grid(g))
        flat = [name for col in sorted(cols) for name in cols[col]]
        assert flat == [f'L{i:02d}' for i in range(22)], (
            f'折行把文件顺序弄乱了：{flat}')

    def test_files_keep_their_own_column(self):
        g = Graph()
        for fi in range(12):
            for li in range(2):
                nid = g.add_node(f'F{fi:02d}_{li}', 0, 0)
                g.nodes[nid].file_path = f'ch{fi:02d}.rpy'
                g.nodes[nid].line_number = li * 10 + 1
        info = layout_by_file(g, col_gap=COLW, row_gap=ROWH, stage=(990, 847))
        assert info['columns'] == 12 and info['pages'] >= 2
        grid = _grid(g)
        pos = {name: (c, r) for (c, r), name in grid.items()}
        for fi in range(12):
            (c0, r0), (c1, r1) = pos[f'F{fi:02d}_0'], pos[f'F{fi:02d}_1']
            assert c0 == c1, f'ch{fi:02d}.rpy 的两个块被拆到了两列'
            assert r1 > r0, f'ch{fi:02d}.rpy 的两个块上下颠倒了'


# ----------------------------------------------------------------------
#  ② 自动布局：层左右排开，到舞台右边才拐弯（蛇形）
# ----------------------------------------------------------------------

class TestLayeredLayoutTurnsAtTheEdge:
    def test_never_wider_than_the_stage(self):
        """8 个块的链：老布局是 8 列并排（2256 宽），现在不许超过舞台。"""
        g = _chain(8)
        info = layout_graph(g, layer_gap=COLW, node_gap=ROWH, stage=(990, 847))
        pos = _pos(g)
        assert max(x for x, _y in pos.values()) + NODE_W <= 990
        assert info['cols'] == 990 // COLW == 3, info
        assert info['bands'] == 3, info

    def test_layers_are_still_columns(self):
        """一层仍然是一列、同一层竖着挨着 —— 不是摊成"每格一个块"。"""
        g = _chain(6)
        layout_graph(g, layer_gap=COLW, node_gap=ROWH, stage=(990, 847))
        pos = _pos(g)
        xs = [pos[f'n{i}'][0] for i in range(6)]
        assert xs[:3] == [0, COLW, 2 * COLW], xs      # 第 1 排从左往右
        assert xs[3:] == [2 * COLW, COLW, 0], xs      # 第 2 排从右往左

    def test_branches_stay_in_one_column_and_adjacent(self):
        """分叉 5 个：必须同一条竖线、上下紧挨着（老承诺，折行不许弄丢）。"""
        g = _fan(5)
        layout_graph(g, layer_gap=COLW, node_gap=ROWH, stage=(990, 847))
        pos = _pos(g)
        xs = {pos[f'b{i}'][0] for i in range(5)}
        assert len(xs) == 1, f'5 个分支被拆到了几列：{pos}'
        ys = sorted(pos[f'b{i}'][1] for i in range(5))
        assert [b - a for a, b in zip(ys, ys[1:])] == [ROWH] * 4, ys

    def test_turning_point_lands_in_the_same_column(self):
        """
        拐弯那一步必须**在同一列**里接上（上一排最后一层 ↔ 这一排第一层）。

        这是蛇形的全部意义：层 2 在第 1 排最右，层 3 就必须在第 2 排最右，
        否则 2→3 那条边要横跨整排再折回来。
        """
        g = _chain(7)
        layout_graph(g, layer_gap=COLW, node_gap=ROWH, stage=(990, 847))
        pos = _pos(g)
        assert pos['n2'][0] == pos['n3'][0] == 2 * COLW
        assert pos['n5'][0] == pos['n6'][0] == 0

    def test_short_last_row_still_lines_up(self):
        """
        最后一排不满时也要对齐（层 4 是单数排的第一个，必须落在最右边）。

        老实现用 `len(band)` 定位 → 单层那一排被摆到最左边，
        层 3→4 的边实测从 x=564 甩回 x=0（2026-10-27 修）。
        """
        g = _chain(4)
        info = layout_graph(g, layer_gap=COLW, node_gap=ROWH, stage=(990, 847))
        assert info['bands'] == 2
        pos = _pos(g)
        assert pos['n3'][0] == pos['n2'][0] == 2 * COLW, pos

    def test_rows_are_separated_by_a_visible_gap(self):
        """两排之间要留出比"同列两块"更大的空，不然看不出这是另一排。"""
        g = _chain(6)
        layout_graph(g, layer_gap=COLW, node_gap=ROWH, stage=(990, 847))
        pos = _pos(g)
        first_row_bottom = max(pos[f'n{i}'][1] for i in range(3))
        second_row_top = min(pos[f'n{i}'][1] for i in range(3, 6))
        assert second_row_top - first_row_bottom >= ROWH, pos

    def test_wide_stage_makes_fewer_rows(self):
        narrow = layout_graph(_chain(12), layer_gap=COLW, node_gap=ROWH,
                              stage=(990, 847))
        wide = layout_graph(_chain(12), layer_gap=COLW, node_gap=ROWH,
                            stage=(1600, 847))
        assert wide['cols'] > narrow['cols']
        assert wide['bands'] < narrow['bands']

    def test_bad_stage_falls_back_to_the_default(self):
        want = int(DEFAULT_STAGE[0] // COLW)
        for bad in ((0, 0), (-5, 10), ('x', 'y')):
            g = _chain(20)
            info = layout_graph(g, layer_gap=COLW, node_gap=ROWH, stage=bad)
            assert info['cols'] == want, bad
            _pos(g)

    def test_one_row_is_reported_as_such(self):
        info = layout_graph(_chain(2), layer_gap=COLW, node_gap=ROWH,
                            stage=(990, 847))
        assert info['bands'] == 1
        assert '没有拐弯' in describe_bands(info)


# ----------------------------------------------------------------------
#  ③ 不传 stage = 老布局（纯数据调用与老用例走的这条）
# ----------------------------------------------------------------------

class TestStageNoneKeepsTheOldLayout:
    def test_layered_layout_still_puts_every_layer_in_its_own_column(self):
        g = _chain(6)
        info = layout_graph(g, layer_gap=COLW, node_gap=ROWH)
        assert info == {}, '没折行就该返回空摘要'
        assert {n.x for n in g.nodes.values()} == \
            {i * COLW for i in range(6)}

    def test_layered_layout_still_centers_each_column(self):
        """不带 stage 时竖直居中这条老规矩一个字都不许变。"""
        g = _chain(3)
        layout_graph(g, layer_gap=COLW, node_gap=ROWH)
        assert {n.y for n in g.nodes.values()} == {0.0}

    def test_file_layout_still_puts_every_file_in_its_own_column(self):
        g = Graph()
        for fi in range(5):
            nid = g.add_node(f'F{fi}', 0, 0)
            g.nodes[nid].file_path = f'ch{fi}.rpy'
            g.nodes[nid].line_number = 1
        info = layout_by_file(g, col_gap=COLW, row_gap=ROWH)
        assert 'grid' not in info or not info['grid']
        assert len({n.x for n in g.nodes.values()}) == 5


# ----------------------------------------------------------------------
#  ④ 界面：画布把**可视区**当舞台，装不下时从左上角开始看
# ----------------------------------------------------------------------

pytest.importorskip('PySide6')

from PySide6.QtCore import QPointF                                # noqa: E402


@pytest.fixture
def canvas(qapp):
    from storygraph.ui_qt.graph_canvas import GraphCanvas
    c = GraphCanvas()
    c.resize(1000, 900)
    return c


class TestCanvasUsesTheViewport:
    def test_stage_box_is_the_viewport(self, canvas):
        box = canvas._stage_box()
        vp = canvas.viewport().rect()
        assert box == (float(vp.width()), float(vp.height())), box

    def test_tiny_viewport_falls_back_to_the_default(self, canvas, qapp):
        from storygraph.graph.layout import DEFAULT_STAGE
        canvas.show()                        # 没 show 过时 Qt 不给真尺寸
        canvas.resize(120, 80)
        qapp.processEvents()
        assert canvas._stage_box() == DEFAULT_STAGE

    def test_auto_layout_wraps_into_the_visible_stage(self, canvas):
        g = _chain(40)
        canvas.set_graph(g)
        vp = canvas.viewport().rect()
        _pos(g)
        assert max(n.x for n in g.nodes.values()) + NODE_W <= vp.width(), (
            f'排到了 {max(n.x for n in g.nodes.values()) + NODE_W:.0f} 宽，'
            f'可视区只有 {vp.width()}')

    def test_arrange_by_file_wraps_too(self, canvas):
        g = _one_file(40)
        canvas.set_graph(g)
        info = canvas.arrange_by_file()
        assert info.get('grid'), '按文件顺序排列没有折行'
        assert info['pages'] > 1

    def test_big_graph_starts_from_the_top_left_corner(self, canvas):
        """
        装不下时把**内容左上角**对齐到视口左上角（2026-10-27 改）。

        ⚠ 只验"往哪儿对"（`centerOn` 拿到哪个点），不验滚动条最后停在哪：
        没显示出来的 `QGraphicsView` 滚动范围是**懒算**的，值会被夹住；
        真机截图另有人眼验收。
        """
        g = _chain(60)
        asked = []
        canvas.centerOn = lambda p: asked.append(QPointF(p))
        canvas.set_graph(g)
        assert asked, '装不下却一次都没对齐过'
        left = min(n.x for n in g.nodes.values())
        top = min(n.y for n in g.nodes.values())
        vp = canvas.viewport().rect()
        want = QPointF(left - canvas.FLUSH_MARGIN, top - canvas.FLUSH_MARGIN)
        got = QPointF(asked[-1].x() - vp.width() / 2.0,
                      asked[-1].y() - vp.height() / 2.0)
        assert abs(got.x() - want.x()) <= 41, (got, want)
        assert abs(got.y() - want.y()) <= 41, (got, want)

    def test_announces_how_many_rows(self, canvas):
        seen = []
        canvas.layout_arranged.connect(seen.append)
        canvas.set_graph(_chain(40))
        canvas.auto_layout()
        assert seen and '排' in seen[-1], seen
        assert '拐' in seen[-1], seen


# ----------------------------------------------------------------------
#  ⑤ 摘要文案（两个界面说同一句话）
# ----------------------------------------------------------------------

class TestDescribeWrap:
    def test_says_columns_rows_and_pages(self):
        text = describe_wrap({'grid': (3, 6), 'pages': 4})
        assert '每列最多 6 块' in text and '一页 3 列' in text
        assert '共 4 页' in text

    def test_one_page_is_named_as_such(self):
        text = describe_wrap({'grid': (3, 6), 'pages': 1})
        assert '一页就排完了' in text

    def test_no_wrap_says_nothing(self):
        assert describe_wrap({}) == ''
        assert describe_wrap(None) == ''
        assert describe_wrap({'grid': None, 'pages': 1}) == ''


class TestDescribeBands:
    def test_says_layers_rows_and_direction(self):
        text = describe_bands({'bands': 5, 'cols': 3, 'layers': 15})
        assert '15 层' in text and '5 排' in text and '每排 3 层' in text
        assert '拐' in text and '从右往左' in text

    def test_one_row_says_no_turn(self):
        text = describe_bands({'bands': 1, 'cols': 3, 'layers': 3})
        assert '没有拐弯' in text

    def test_nothing_to_say(self):
        assert describe_bands({}) == ''
        assert describe_bands(None) == ''
        assert describe_bands({'bands': 0}) == ''
