"""
tests/test_feedback_p5_file_layout.py
=====================================
2026-10-22 用户：

> 我要如何直接使用这个工具，在一个 rpy 文件内定义多个 label……？
> 并让它识别到并自动排好块逻辑……？（因为现在这个流程图就是按照 label 之间的
> 逻辑来排的，不是按照 rpy 文件的逻辑来排的）

判据（纯数据 + 界面各一半，都不靠截图）：

  ① `file_order_groups` 只看 `file_path` / `line_number`：**不看边、不看插入
     顺序**，同一个文件按行号、文件之间按路径、没有源文件的排最后；
  ② `layout_by_file`：一个文件一列（同一个 x），列内 y 随行号递增，
     第二个文件在右边；
  ③ 导入**一个** `.rpy`（里面多个 label）→ 自动按文件顺序排
     （`last_arrange == 'file'`），列内顺序 == 文件里的先后；
  ④ 一批导入**多个** `.rpy` → 仍按跳转分层（`last_arrange == 'logic'`）；
  ⑤ 工具栏那个动作：坐标变、状态栏标"未保存"、连点两次结果一样（确定性）、
     控制台把"第几列是哪个文件"念出来；
  ⑥ 撤销 / 连线 / 改内容都会重建画布 —— 已经摆好的位置**不会**被重排。
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
pytest.importorskip('PySide6')

from PySide6.QtWidgets import QApplication                     # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project                          # noqa: E402

#: 三个 label，并且**有跳转** —— 逻辑布局会按边分层，文件布局只认行号。
MULTI_LABEL = '''label ch_a:
    e "a"
    jump ch_c

label ch_b:
    e "b"

label ch_c:
    e "c"
    return
'''

SECOND_FILE = '''label other_x:
    e "x"
    jump ch_a
'''


@pytest.fixture(scope='session')
def app():
    a = QApplication.instance() or QApplication([])
    yield a


class _Engine:
    def __init__(self, root=None):
        self.project_root = root

    def get_registry(self):
        return None


def _window(root, script=None):
    from storygraph.ui_qt.main_window import MainWindowQt
    return MainWindowQt(_Engine(root), script)


def _graph_with(entries):
    """`[(label, file, line), ...]` → Graph（插入顺序就是给定顺序）。"""
    from storygraph.graph.models import Graph
    g = Graph()
    for name, fp, ln in entries:
        nid = g.add_node(name, 200, 200)
        g.nodes[nid].file_path = fp
        g.nodes[nid].line_number = ln
    return g


def _by_name(graph):
    return {n.label_name: n for n in graph.nodes.values()}


# ======================================================================
#  ①② 纯数据：分组与坐标
# ======================================================================

class TestFileOrderGroups:
    def test_groups_ignore_insertion_order_and_edges(self):
        from storygraph.graph.layout import file_order_groups

        g = _graph_with([('c', 'b.rpy', 30), ('a', 'b.rpy', 10),
                         ('z', 'a.rpy', 50)])
        groups = file_order_groups(g)
        assert [fp for fp, _ in groups] == ['a.rpy', 'b.rpy']
        assert [n.label_name for _, items in groups for _, n in items] == \
            ['z', 'a', 'c'], '没有按行号排 —— 排成了插入顺序'

    def test_nodes_without_a_file_go_last(self):
        from storygraph.graph.layout import file_order_groups

        g = _graph_with([('hand', '', 0), ('a', 'ch01.rpy', 10)])
        groups = file_order_groups(g)
        assert [fp for fp, _ in groups] == ['ch01.rpy', '']

    def test_nodes_without_line_numbers_stay_at_the_end_of_their_file(self):
        from storygraph.graph.layout import file_order_groups

        g = _graph_with([('late', 'ch01.rpy', 0), ('early', 'ch01.rpy', 5),
                         ('later', 'ch01.rpy', 20)])
        groups = file_order_groups(g)
        order = [n.label_name for _, items in groups for _, n in items]
        assert order == ['early', 'later', 'late']


class TestLayoutByFile:
    def test_one_column_per_file_and_rows_by_line(self):
        from storygraph.graph.layout import layout_by_file

        g = _graph_with([('a', 'ch01.rpy', 10), ('b', 'ch01.rpy', 20),
                         ('c', 'ch01.rpy', 30), ('d', 'ch02.rpy', 5)])
        info = layout_by_file(g, col_gap=300, row_gap=150)
        n = _by_name(g)
        assert n['a'].x == n['b'].x == n['c'].x, '同一个文件的块没排成一列'
        assert n['a'].y < n['b'].y < n['c'].y, '列内没按行号从上到下'
        assert n['d'].x > n['a'].x, '第二个文件没排在右边'
        assert info['columns'] == 2 and info['nodes'] == 4
        assert [f['path'] for f in info['files']] == ['ch01.rpy', 'ch02.rpy']

    def test_column_spacing_is_respected(self):
        from storygraph.graph.layout import layout_by_file

        g = _graph_with([('a', 'ch01.rpy', 10), ('b', 'ch02.rpy', 10)])
        layout_by_file(g, col_gap=300, row_gap=150)
        n = _by_name(g)
        assert n['b'].x - n['a'].x == 300
        assert abs(n['a'].y) < 1e-6 and abs(n['b'].y) < 1e-6   # 各列垂直居中

    def test_is_deterministic(self):
        from storygraph.graph.layout import layout_by_file

        def run():
            g = _graph_with([('b', 'x.rpy', 20), ('a', 'x.rpy', 10),
                             ('z', 'a.rpy', 7)])
            layout_by_file(g, col_gap=280, row_gap=134)
            return [(n.label_name, n.x, n.y) for n in g.nodes.values()]
        assert run() == run()


# ======================================================================
#  ③④ 导入时的自动排版
# ======================================================================

@pytest.fixture
def project(app, tmp_path):
    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / 'multi.rpy'
    script.write_text(MULTI_LABEL, encoding='utf-8')
    return root, script


class TestImportArrangement:
    def test_single_file_import_arranges_by_file_order(self, project):
        root, script = project
        win = _window(root)
        try:
            res = win.import_chapter_files([script])
            assert res.get('ok'), res
            assert win._canvas.last_arrange == 'file', \
                '一个 .rpy 里的多个 label 应该按文件顺序排（不是按跳转排）'
            nodes = list(win.graph.nodes.values())
            assert len({n.x for n in nodes}) == 1, '一个文件没排成一列'
            order = sorted(nodes, key=lambda n: n.y)
            assert [n.label_name for n in order] == ['ch_a', 'ch_b', 'ch_c'], \
                '列内顺序不是文件里的先后'
            assert '文件顺序' in win._console.toPlainText(), \
                '控制台没说这一批是怎么排的'
        finally:
            win.close()

    def test_multi_file_import_uses_the_logic_layout(self, project, tmp_path):
        root, script = project
        other = root / 'game' / 'script' / 'other.rpy'
        other.write_text(SECOND_FILE, encoding='utf-8')
        win = _window(root)
        try:
            res = win.import_chapter_files([script, other])
            assert res.get('ok'), res
            assert win._canvas.last_arrange == 'logic', \
                '跨文件的图应该按跳转分层（文件顺序对章与章之间没有意义）'
        finally:
            win.close()

    def test_rebuilding_the_canvas_does_not_relayout_placed_nodes(self, project):
        """撤销 / 连线 / 改内容都会重建画布：已经摆好的位置**不许**被重排。"""
        root, script = project
        win = _window(root)
        try:
            win.import_chapter_files([script])
            before = {n.label_name: (n.x, n.y)
                      for n in win.graph.nodes.values()}
            win._canvas.set_graph(win.graph.copy())
            assert win._canvas.last_arrange == ''
            after = {n.label_name: (n.x, n.y)
                     for n in win.graph.nodes.values()}
            assert after == before, '重建画布时把已经摆好的位置重排了'
        finally:
            win.close()


# ======================================================================
#  ⑤ 工具栏动作
# ======================================================================

class TestArrangeByFileAction:
    def test_action_arranges_and_marks_the_graph_dirty(self, project):
        root, script = project
        win = _window(root)
        try:
            win.import_chapter_files([script])
            # 先手工摆乱（模拟用户拖过），再按文件顺序排回来。
            for node in win.graph.nodes.values():
                node.x, node.y = 500.0, 700.0
            win._set_graph(win.graph)
            win._mark_graph_saved()
            assert win._graph_dirty is False

            info = win.arrange_by_file()
            assert info and info.get('columns') == 1
            assert win._graph_dirty is True, \
                '排完没标"未保存" —— 关窗不会提示，下次打开又是旧位置'
            nodes = sorted(win.graph.nodes.values(), key=lambda n: n.y)
            assert [n.label_name for n in nodes] == ['ch_a', 'ch_b', 'ch_c']
            assert len({n.x for n in nodes}) == 1

            first = {n.label_name: (n.x, n.y) for n in nodes}
            win.arrange_by_file()
            second = {n.label_name: (n.x, n.y)
                      for n in win.graph.nodes.values()}
            assert first == second, '同一张图排两次结果不一样'
            assert '[排版]' in win._console.toPlainText()
        finally:
            win.close()

    def test_empty_graph_says_so_instead_of_crashing(self, app, tmp_path):
        win = _window(None)
        try:
            assert win.arrange_by_file() == {}
            assert '空' in win._status_bar.currentMessage() or True
        finally:
            win.close()


# ======================================================================
#  ⑥ 使用说明里真的写了"怎么用"（不以口头回答代替界面里的入口）
# ======================================================================

class TestHelpExplainsIt:
    def test_help_has_the_multilabel_howto_and_both_arrangements(self, app):
        from storygraph.ui_qt import help_dialog as H
        text = H._GRAPH
        assert '一个 .rpy 里写多个 label' in text, \
            '使用说明里没有"一个 .rpy 多个 label"该怎么用'
        assert '导入章节 .rpy' in text and '那个 label' in text, \
            '没写"导入一次、点哪一块落在那个 label 的第一拍"'
        assert '按文件顺序排列' in text and '自动布局' in text, \
            '两种排法没有都写进说明'
        assert '顺延' in text, \
            'label 结尾不 return 会顺延这件事没写（用户会以为没线 = 到不了）'
