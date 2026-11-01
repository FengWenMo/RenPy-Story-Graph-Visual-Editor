# -*- coding: utf-8 -*-
"""
S0：边的"第几拍"徽标 + 悬停上下文。

测的是**纯逻辑**（`flow.beat_context` / `edge_label_text` / `edge_tooltip`），
不建 Qt 控件 —— 控件那部分只有"把值塞进 label"一句，不值得为它起窗口。

判据里钉住的三件事：

  1. 位置只在**有拍身份**时给拍号（tooltip / 编辑连线窗口）；线外只留
     条件/菜单项，草图必须标「草图」（P0，2026-10-24；位置移出线外是
     2026-10-24 晚）
  2. 控制流拍用 `raw`（`call side`），不是"旁白"——摘要不能比原文更模糊
  3. 首/末拍的"上一拍 / 下一拍"要说"章首 / 章末"，不是留空
"""
from __future__ import annotations

import sys
from pathlib import Path

from storygraph.story.beat_model import Beat
from storygraph.story.flow import beat_brief, beat_context
from storygraph.ui_qt.graph_canvas import edge_label_text, edge_tooltip


class FakeEdge:
    def __init__(self, line_id='', option_text='', condition='', type='jump',
                 origin='source', position='chapter_end'):
        self.line_id = line_id
        self.option_text = option_text
        self.condition = condition
        self.type = type
        self.origin = origin
        self.position = position
        self.position_line_id = line_id if position == 'beat' else ''


def _beats():
    return [
        Beat(line_id='ch01.0001', kind='say', speaker='e', text='我们出发吧'),
        Beat(line_id='ch01.0002', kind='say', speaker='m', text='等等，带上武器'),
        Beat(line_id='ch01.0003', kind='narration', raw='call side'),
        Beat(line_id='ch01.0004', kind='say', speaker='e', text='到了'),
    ]


# ---------------------------------------------------------------------------
#  beat_context
# ---------------------------------------------------------------------------

def test_context_numbers_beats_from_one_and_gives_neighbours():
    ctx = beat_context(_beats())
    item = ctx['ch01.0003']
    assert item['index'] == 3
    assert item['total'] == 4
    assert '带上武器' in item['prev']
    assert item['text'] == 'call side'
    assert '到了' in item['next']


def test_first_and_last_beat_have_no_neighbour():
    ctx = beat_context(_beats())
    assert ctx['ch01.0001']['prev'] == ''
    assert ctx['ch01.0004']['next'] == ''


def test_beats_without_line_id_are_skipped_not_numbered():
    """没有身份 = 没法被边引用。硬编号会让别的边指向错的那一拍。"""
    beats = [Beat(line_id='', kind='say', text='孤儿'), *_beats()]
    ctx = beat_context(beats)
    assert '' not in ctx
    assert ctx['ch01.0003']['index'] == 4      # 位置按真实顺序算，仍从 1 起
    assert ctx['ch01.0003']['total'] == 5


def test_flow_beat_summary_prefers_raw_over_kind():
    ctx = beat_context(_beats())
    assert ctx['ch01.0003']['text'] == 'call side'
    assert beat_brief(Beat(line_id='x', kind='narration', raw='jump ch02')) == 'jump ch02'


def test_long_text_is_truncated_with_ellipsis():
    beat = Beat(line_id='x', kind='say', text='很长' * 40)
    brief = beat_brief(beat)
    assert brief.endswith('…')
    assert len(brief) <= 33


# ---------------------------------------------------------------------------
#  标签与 tooltip
# ---------------------------------------------------------------------------

def test_label_only_shows_the_option_not_the_position():
    edge = FakeEdge(line_id='ch01.0003', option_text='去屋顶')
    info = beat_context(_beats())['ch01.0003']
    text = edge_label_text(edge, info)
    assert text == '「去屋顶」'
    assert '第 3 拍' not in text, '位置已经挪进「编辑连线」窗口'


def test_label_without_option_is_empty_not_the_position():
    """线上没有条件/菜单项就留空：位置靠双击看，不挂在线外面。"""
    edge = FakeEdge(line_id='ch01.0003')
    info = beat_context(_beats())['ch01.0003']
    assert edge_label_text(edge, info) == ''


def test_manual_edge_is_explicitly_marked_as_a_sketch():
    """草图在剧本里没有那一行 —— 必须一眼看出"未写入"。"""
    edge = FakeEdge(line_id='', option_text='去屋顶', origin='manual')
    text = edge_label_text(edge, None)
    assert text.startswith('草图'), text
    assert '「去屋顶」' in text, text
    assert '章末' not in text, '落点已经挪进「编辑连线」窗口'


def test_tooltip_tells_where_and_what():
    edge = FakeEdge(line_id='ch01.0003', type='call')
    info = beat_context(_beats())['ch01.0003']
    tip = edge_tooltip(edge, info)
    assert '第 3 拍 / 共 4 拍' in tip
    assert '调用' in tip and '会回来' in tip
    assert '上一拍' in tip and '下一拍' in tip
    assert '源文件里的控制流' in tip


def test_tooltip_keeps_the_sketch_warning():
    """加了位置之后，"这条线还没写进脚本"这句不能丢。"""
    edge = FakeEdge(origin='manual')
    assert '不写进 .rpy' in edge_tooltip(edge, None)


# ---------------------------------------------------------------------------
#  接线守卫：窗口层必须真的把上下文灌进画布
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
#  S0.5：双击边 → 进卡片页的那一拍
# ---------------------------------------------------------------------------

def _canvas_with_one_edge(qapp, origin='source', line_id='ch01.0003'):
    from storygraph.graph.models import Graph
    from storygraph.ui_qt.graph_canvas import GraphCanvas

    canvas = GraphCanvas()
    g = Graph()
    a = g.add_node('A', 0, 0)
    b = g.add_node('B', 200, 0)
    g.add_edge(a, b, etype='call', origin=origin, line_id=line_id)
    canvas.set_graph(g)
    return canvas


class _FakeEvent:
    def accept(self):
        pass


def test_double_click_on_an_edge_opens_its_own_properties(qapp):
    canvas = _canvas_with_one_edge(qapp)
    seen = []
    canvas.edge_edit_requested.connect(seen.append)

    canvas._edges[0].mouseDoubleClickEvent(_FakeEvent())

    assert len(seen) == 1
    assert seen[0].line_id == 'ch01.0003'


def test_double_click_on_a_sketch_also_opens_properties(qapp):
    """草图没有对应的拍，但双击仍然要打开它自己的属性，不能静默。"""
    canvas = _canvas_with_one_edge(qapp, origin='manual', line_id='')
    seen = []
    canvas.edge_edit_requested.connect(seen.append)

    canvas._edges[0].mouseDoubleClickEvent(_FakeEvent())

    assert len(seen) == 1 and seen[0].line_id == ''


def _main_window_src() -> str:
    """
    **整个前端**的源码（入口 + 全部 mixin）。

    2026-11-01：画布那十来条 `connect` 随第 0 页构建器搬进了
    `ui_qt/window_graph_page.py` —— 只读 `main_window.py` 会误报"没接这根线"。
    取法统一走 `tests/_source_paths.main_window_source_text()`（它就是为这件事
    建的：扫源码的用例不许硬编码单个文件）。
    """
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _source_paths import main_window_source_text
    return main_window_source_text()


def test_main_window_listens_for_edge_edit_and_keeps_beat_locate():
    src = _main_window_src()
    assert 'edge_edit_requested.connect(self.edit_edge)' in src
    assert 'edge_open_requested.connect(self._open_edge_in_card)' in src
    assert 'def _open_edge_in_card' in src


def test_open_edge_loads_the_chapter_before_resolving_the_beat_index():
    """
    跨章边：必须先载入 from 那一章，**再**取拍号。
    反了就会用上一章的拍号去定位，跳到完全无关的位置（多章并存时必现）。
    """
    src = _main_window_src()
    body = src[src.find('def _open_edge_in_card'):]
    load_at = body.find('self._on_node_open(nid)')
    ctx_at = body.find('self._beat_context()')
    assert load_at > 0, '找不到载入那一步'
    assert ctx_at > load_at, '必须先载入章节再取拍号'


# ---------------------------------------------------------------------------
#  D3：只看选中模块的连线（纯视图，不删数据）
# ---------------------------------------------------------------------------

def _two_edge_canvas(qapp):
    from storygraph.graph.models import Graph
    from storygraph.ui_qt.graph_canvas import GraphCanvas

    canvas = GraphCanvas()
    g = Graph()
    a = g.add_node('A', 0, 0)
    b = g.add_node('B', 200, 0)
    c = g.add_node('C', 400, 0)
    g.add_edge(a, b, etype='jump', line_id='A.0001')
    g.add_edge(b, c, etype='jump', line_id='B.0001')
    canvas.set_graph(g)
    return canvas, (a, b, c)


def test_focus_hides_edges_not_touching_the_node(qapp):
    canvas, (_a, b, _c) = _two_edge_canvas(qapp)

    res = canvas.set_edge_focus(b)
    assert res['focus'] == b
    assert res['shown'] == 2 and res['total'] == 2, 'B 与两条边都相连'
    assert all(ei.isVisible() for ei in canvas._edges)

    res = canvas.set_edge_focus(_a)
    assert res['shown'] == 1 and res['total'] == 2
    assert sum(1 for ei in canvas._edges if ei.isVisible()) == 1


def test_focus_is_a_toggle_and_never_deletes(qapp):
    canvas, (a, _b, _c) = _two_edge_canvas(qapp)
    before = len(canvas._edges)

    canvas.set_edge_focus(a)
    res = canvas.set_edge_focus(a)          # 再点一次 = 恢复

    assert res['focus'] is None
    assert all(ei.isVisible() for ei in canvas._edges)
    assert len(canvas._edges) == before, '视图操作不许删边'


def test_focus_survives_a_graph_rebuild(qapp):
    """连线/刷新会重建边 —— 重建之后过滤要还在，否则用户白点。"""
    canvas, (a, _b, _c) = _two_edge_canvas(qapp)
    canvas.set_edge_focus(a)
    before = sum(1 for ei in canvas._edges if ei.isVisible())

    canvas.set_graph(canvas.graph, keep_view=False)

    assert sum(1 for ei in canvas._edges if ei.isVisible()) == before


def test_main_window_feeds_beat_context_after_set_graph():
    src = (Path(__file__).resolve().parents[1]
           / 'storygraph' / 'ui_qt' / 'main_window.py').read_text(encoding='utf-8')
    set_graph_at = src.find('self._canvas.set_graph(graph')
    feed_at = src.find('self._canvas.set_beat_context(', set_graph_at)
    assert set_graph_at > 0, '找不到 _set_graph 里的画布调用'
    assert feed_at > set_graph_at, (
        '必须先 set_graph 再灌上下文 —— 反过来，重建出来的线拿不到位置'
    )
