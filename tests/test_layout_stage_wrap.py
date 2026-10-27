"""
tests/test_layout_stage_wrap.py
===============================
自动布局"**折进舞台**"（2026-10-27）。

用户原话：

> 自动布局的逻辑不太行……仍然需要修改（因为现在有些太长了，你应该使得
> 尽可能多的标签整齐排列在舞台内部），而不是一路长排下去……

真实项目（`sr_general`，45 个 `.rpy` / 65 个块，舞台 990×847）量出来的
"老样子" —— 两个数说的是同一件事：布局只朝**一个方向**长，而舞台有边：

    ▤ 按文件顺序排列   12334 × 2650   44 列并排，一屏只看得到 4 个块
    ⤢ 自动布局          4156 × 2918   15 层并排，一屏看得到 6 个

判据：

  ① **一个方向不许无限长**：整图宽 ≤ 舞台宽，每页高 ≤ 舞台高；
  ② **舞台里装最多的块**：一页排满（块数 == 列 × 行）才换下一页；
  ③ **列内顺序不变**：同一个文件的 label 从上到下就是文件里的先后
     （`2026-10-22` 那条老承诺，折行不许把它弄丢）；
  ④ **不传 `stage` 时逐字节保持老布局**（纯数据调用 / 老用例走这条）；
  ⑤ 画布真的把**可视区**传下去了，且装不下时从左上角开始看。
"""

from __future__ import annotations

import pytest

from storygraph.graph.models import Graph
from storygraph.graph.layout import (DEFAULT_STAGE, describe_wrap,
                                     layout_by_file, layout_graph)

#: 与 `ui_qt.graph_canvas._layout_gaps()` 一致（块 208×104 + 间距 74/30）
COLW, ROWH = 282, 134


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


def _one_file(n: int, name: str = 'big.rpy') -> Graph:
    """一个 `.rpy` 里 n 个 label（行号 1, 11, 21 …）。"""
    g = Graph()
    for i in range(n):
        nid = g.add_node(f'L{i:02d}', 0, 0)
        g.nodes[nid].file_path = name
        g.nodes[nid].line_number = i * 10 + 1
    return g


def _grid(graph) -> dict:
    """`{(列, 行): label}` —— 按坐标反推格子；**顺便钉住"不许重叠"**。"""
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
#  ① 一个方向不许无限长
# ----------------------------------------------------------------------

class TestNeverGrowsInOneDirection:
    def test_wide_enough_stage_is_never_exceeded(self):
        """30 个块一条链：老布局是 30 列并排（8460 宽），现在必须收进舞台。"""
        g = _chain(30)
        info = layout_graph(g, layer_gap=COLW, node_gap=ROWH,
                            stage=(990, 847))
        grid = _grid(g)
        cols = max(c for c, _r in grid) + 1
        assert cols <= 990 // COLW == 3, f'排了 {cols} 列，舞台只放得下 3 列'
        assert info['grid'] == (3, 6), info

    def test_every_page_fits_the_stage_height(self):
        g = _chain(65)
        info = layout_graph(g, layer_gap=COLW, node_gap=ROWH,
                            stage=(990, 847))
        _cols, rows = info['grid']
        grid = _grid(g)
        assert max(r for _c, r in grid) < rows * info['pages']
        # 每一页里块都落在 0..rows-1 行内（页高 = rows 行）
        for page in range(info['pages']):
            band = [r for _c, r in grid
                    if page * rows <= r < (page + 1) * rows]
            assert band and max(band) - page * rows < rows

    def test_taller_blocks_fit_fewer_rows(self):
        """舞台高多少行、宽多少列，是**算出来的**，不是写死的。"""
        g = _chain(20)
        small = layout_graph(_chain(20), layer_gap=COLW, node_gap=ROWH,
                             stage=(640, 400))
        assert small['grid'] == (640 // COLW, 400 // ROWH)
        g2 = _chain(20)
        big = layout_graph(g2, layer_gap=COLW, node_gap=ROWH,
                           stage=(1600, 1000))
        assert big['grid'] == (1600 // COLW, 1000 // ROWH)
        assert big['grid'][0] > small['grid'][0]

    def test_bad_stage_falls_back_to_the_default(self):
        """舞台盒子不合法（0 / 负数 / 不是数）→ 退回 16:9 默认值，不许炸。"""
        want = (int(DEFAULT_STAGE[0] // COLW), int(DEFAULT_STAGE[1] // ROWH))
        for bad in ((0, 0), (-5, 10), ('x', 'y')):
            info = layout_graph(_chain(20), layer_gap=COLW, node_gap=ROWH,
                                stage=bad)
            assert info['grid'] == want, bad

    def test_stage_none_means_do_not_wrap_at_all(self):
        """`stage=None` 是**另一件事**：明确"不要折行"（老布局），不是坏盒子。"""
        assert layout_graph(_chain(20), layer_gap=COLW, node_gap=ROWH,
                            stage=None) == {}


# ----------------------------------------------------------------------
#  ② 舞台里装最多的块（一页排满才换页）
# ----------------------------------------------------------------------

class TestPagesAreFullBeforeTheNextOne:
    def test_first_page_is_full(self):
        """65 个块、一页 3×6 —— 第一页必须**装满 18 个**（不是空着几格）。"""
        g = _chain(65)
        info = layout_graph(g, layer_gap=COLW, node_gap=ROWH,
                            stage=(990, 847))
        cols, rows = info['grid']
        grid = _grid(g)
        first = [name for (c, r), name in grid.items() if r < rows]
        assert len(first) == cols * rows, (
            f'第一页只摆了 {len(first)} 个块（{cols}×{rows} 的格子）')

    def test_pages_go_down_not_right(self):
        """排满一页换到**下面**一页（往下滚），不是接着往右排。"""
        g = _chain(65)
        info = layout_graph(g, layer_gap=COLW, node_gap=ROWH,
                            stage=(990, 847))
        cols, rows = info['grid']
        grid = _grid(g)
        assert info['pages'] > 1
        # 第 2 页的第 0 列 == 第 1 页的第 0 列（x 一样），只是往下 rows 行
        page2 = [r for (c, r) in grid if c == 0 and r >= rows]
        assert page2, '第 2 页没有落在 x=0 这一列上 —— 页是往右排的'
        assert min(page2) == rows

    def test_few_blocks_stay_in_one_page(self):
        g = _chain(4)
        info = layout_graph(g, layer_gap=COLW, node_gap=ROWH,
                            stage=(990, 847))
        assert info['pages'] == 1
        assert len({c for c, _r in _grid(g)}) == 1, '4 个块被折成了两列'


# ----------------------------------------------------------------------
#  ③ 列内顺序：折行不许打乱"从上到下 = 文件里的先后"
# ----------------------------------------------------------------------

class TestOrderIsKept:
    def test_a_files_labels_are_top_down_in_one_column(self):
        """一个文件 4 个 label：同一列、从上到下 = 行号顺序。"""
        g = _one_file(4)
        info = layout_by_file(g, col_gap=COLW, row_gap=ROWH,
                              stage=(990, 847))
        assert info['pages'] == 1
        cols = _columns(_grid(g))
        assert len(cols) == 1, '一个文件的 4 个 label 被拆到了几列'
        assert cols[0] == ['L00', 'L01', 'L02', 'L03']

    def test_long_file_continues_in_the_next_column(self):
        """22 个 label / 一列只放得下 6 个 → 每 6 个占一列，列**内**仍然正序。"""
        g = _one_file(22)
        # 舞台给到能放下 **4 列**：这样四段都在第一页，按列读出来就是文件顺序
        # （跨页时下一页从"上一列结尾那一侧"接着排，那是 `test_pages_*` 的事）。
        info = layout_by_file(g, col_gap=COLW, row_gap=ROWH,
                              stage=(4 * COLW, 6 * ROWH))
        assert info['grid'] == (4, 6), info
        assert info['pages'] == 1
        cols = _columns(_grid(g))
        flat = [name for col in sorted(cols) for name in cols[col]]
        assert flat == [f'L{i:02d}' for i in range(22)], (
            f'折行把文件顺序弄乱了：{flat}')
        for col, names in cols.items():
            assert names == sorted(names), f'第 {col} 列里是倒序：{names}'

    def test_files_keep_their_own_column_and_stay_readable(self):
        """多个文件、每个 2 个 label：同一个文件的两个块必须**竖着挨着**。"""
        g = Graph()
        for fi in range(12):
            for li in range(2):
                nid = g.add_node(f'F{fi:02d}_{li}', 0, 0)
                g.nodes[nid].file_path = f'ch{fi:02d}.rpy'
                g.nodes[nid].line_number = li * 10 + 1
        info = layout_by_file(g, col_gap=COLW, row_gap=ROWH,
                              stage=(990, 847))
        assert info['columns'] == 12 and info['pages'] >= 2
        grid = _grid(g)
        pos = {name: (c, r) for (c, r), name in grid.items()}
        for fi in range(12):
            (c0, r0), (c1, r1) = pos[f'F{fi:02d}_0'], pos[f'F{fi:02d}_1']
            assert c0 == c1, f'ch{fi:02d}.rpy 的两个块被拆到了两列'
            assert r1 > r0, f'ch{fi:02d}.rpy 的两个块上下颠倒了'

    def test_wrap_does_not_change_which_nodes_are_on_the_graph(self):
        g = _one_file(22)
        before = set(g.nodes)
        layout_by_file(g, col_gap=COLW, row_gap=ROWH, stage=(990, 847))
        assert set(g.nodes) == before
        assert all(isinstance(n.x, float) and isinstance(n.y, float)
                   for n in g.nodes.values())


# ----------------------------------------------------------------------
#  ④ 不传 stage = 老布局（纯数据调用与老用例走的这条）
# ----------------------------------------------------------------------

class TestStageNoneKeepsTheOldLayout:
    def test_layered_layout_still_puts_every_layer_in_its_own_column(self):
        g = _chain(6)
        info = layout_graph(g, layer_gap=COLW, node_gap=ROWH)
        assert info == {}, '没折行就该返回空摘要'
        assert len({n.x for n in g.nodes.values()}) == 6, '一层一列的老排法变了'
        assert {n.x for n in g.nodes.values()} == \
            {i * COLW for i in range(6)}

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
#  ⑤ 界面：画布把**可视区**当舞台，装不下时从左上角开始看
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
        from storygraph.ui_qt.graph_canvas import GraphCanvas
        _ = GraphCanvas
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
        cols = len({round(n.x, 3) for n in g.nodes.values()})
        assert cols <= max(1, vp.width() // COLW), (
            f'排了 {cols} 列，可视区 {vp.width()} 只放得下 {vp.width() // COLW} 列')

    def test_arrange_by_file_wraps_too(self, canvas):
        g = _one_file(40)
        canvas.set_graph(g)
        info = canvas.arrange_by_file()
        assert info.get('grid'), '按文件顺序排列没有折行'
        assert info['pages'] > 1

    def test_big_graph_starts_from_the_top_left_corner(self, canvas):
        """
        装不下时把**内容左上角**对齐到视口左上角（2026-10-27 改）。

        以前是"100% 停在入口块**正中**"：折行之后入口块本来就在左上角，
        把它摆正中等于上面空掉半屏（真实项目 990×847 里空掉 340px）。

        ⚠ 只验"往哪儿对"（`centerOn` 拿到哪个点），不验滚动条最后停在哪：
        没显示出来的 `QGraphicsView` 滚动范围是**懒算**的，值会被夹住
        （实测 offscreen 下 hscroll.max 是负数）；真机截图另有人眼验收。
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
        # 容差 41：`_reset_view` 给内容留了 40px 呼吸位（内容左上角再左/上 40）
        assert abs(got.x() - want.x()) <= 41, (got, want)
        assert abs(got.y() - want.y()) <= 41, (got, want)

    def test_announces_how_many_pages(self, canvas):
        seen = []
        canvas.layout_arranged.connect(seen.append)
        canvas.set_graph(_chain(40))
        canvas.auto_layout()
        assert seen and '页' in seen[-1], seen


# ----------------------------------------------------------------------
#  摘要文案（两个界面说同一句话）
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
