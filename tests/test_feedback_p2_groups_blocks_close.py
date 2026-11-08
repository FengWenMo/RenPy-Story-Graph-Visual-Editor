"""
tests/test_feedback_p2_groups_blocks_close.py
=============================================
2026-10-22 体验报告「建议下一步」三条的离线判据：

  ① 卡片左栏按 label 分组、可折叠（标题行不是拍，行号 ≠ 拍号）；
  ② 变量工作台能把新增操作插进 `if` / `for` 的块内（每条各自带锚点）；
  ③ 关窗三选一：保存 / 不保存 / 取消（取消真的不关）。

都**不靠截图**：断言列表项的数据、`Beat.code`、编译出来的 `.rpy` 文本。
"""

# W1a（2026-10-23）：版式从项目根搬进 `.storygraph/`，断言跟着走。
LAYOUT_REL = '.storygraph/layout.json'

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
pytest.importorskip('PySide6')

from PySide6.QtCore import Qt                                  # noqa: E402
from PySide6.QtWidgets import QApplication                     # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project                          # noqa: E402

MULTI_LABEL = '''label main_a:
    e "one"
    e "two"
    e "three"

label side_b:
    e "four"
    e "five"
'''

BLOCK_SCRIPT = '''default love = 0

label vars_main:
    $ love += 1
    if love > 3:
        $ route = "roof"
        $ flag = True
    for i in range(2):
        $ counter += i
    e "done"
    return
'''


@pytest.fixture(scope='session')
def app():
    a = QApplication.instance() or QApplication([])
    yield a


class _Engine:
    def __init__(self, root=None):
        self.project_root = root
        self._registry = None

    def get_registry(self):
        return self._registry


def _window(root, script=None):
    from storygraph.ui_qt.main_window import MainWindowQt
    return MainWindowQt(_Engine(root), script)


# ======================================================================
#  ① 卡片左栏：label 分组 / 折叠
# ======================================================================

@pytest.fixture
def multi(app, tmp_path):
    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / 'multi.rpy'
    script.write_text(MULTI_LABEL, encoding='utf-8')
    win = _window(root, script)
    yield win
    win._collapsed_labels = frozenset()
    win.close()


class TestLabelGroups:
    def test_group_rows_are_not_beats(self, multi):
        win = multi
        assert len(win._session) == 5
        assert win._list.count() == 7, '多 label 时应有 2 行分组标题'
        assert set(win._group_items) == {'main_a', 'side_b'}
        head = win._group_items['side_b']
        assert 'side_b' in head.text()
        assert head.data(Qt.UserRole) == -1, '标题行不能冒充某一拍'
        assert not (head.flags() & Qt.ItemIsSelectable), '标题行不该被选中'
        assert win._row_of_beat(0) == 1
        assert win._list.item(win._row_of_beat(0)).data(Qt.UserRole) == 0

    def test_clicking_header_folds_and_unfolds(self, multi):
        win = multi
        head = win._group_items['main_a']
        win._on_list_item_clicked(head)
        hidden = [win._list.item(win._row_of_beat(i)).isHidden()
                  for i in range(3)]
        assert hidden == [True, True, True], '折叠没藏起 main_a 的拍'
        assert not win._list.item(win._row_of_beat(4)).isHidden(), \
            '别的 label 不该跟着藏'
        assert win._list.currentRow() == 0, '当前拍被折进去后应停在标题行'
        assert '▸' in head.text()

        win._on_list_item_clicked(head)
        assert not any(win._list.item(win._row_of_beat(i)).isHidden()
                       for i in range(3))
        assert '▾' in head.text()

    def test_navigating_expands_the_folded_group(self, multi):
        win = multi
        win._on_list_item_clicked(win._group_items['main_a'])
        assert win._list.item(win._row_of_beat(1)).isHidden()

        win._show_index(1)
        assert win._loaded_idx == 1
        assert not win._list.item(win._row_of_beat(1)).isHidden(), \
            '跳到折叠组里的拍时必须自动展开'
        assert win._list.currentRow() == win._row_of_beat(1)
        assert '▾' in win._group_items['main_a'].text()

    def test_folding_survives_a_list_reload(self, multi):
        """插卡 / 删卡会重建列表：折叠状态是视图状态，不该被它抹掉。"""
        win = multi
        win._on_list_item_clicked(win._group_items['main_a'])
        win._reload_list()
        assert win._list.item(win._row_of_beat(0)).isHidden()
        assert '▸' in win._group_items['main_a'].text()

    def test_clicking_a_beat_row_loads_that_beat(self, multi):
        """点标题下面第一行 → 进第 1 拍（不是"第 1 行"碰巧对上的那拍）。"""
        win = multi
        win._list.setCurrentRow(win._row_of_beat(4))
        assert win._loaded_idx == 4
        win._list.setCurrentRow(win._row_of_beat(0))
        assert win._loaded_idx == 0

    def test_single_label_file_has_no_group_rows(self, app, tmp_path):
        root = build_project(tmp_path / 'proj')
        script = root / 'game' / 'script' / 'one.rpy'
        script.write_text('label only_one:\n    e "x"\n    e "y"\n',
                          encoding='utf-8')
        win = _window(root, script)
        try:
            assert len(win._session) == 2
            assert win._list.count() == 2, '单 label 不该多一行标题'
            assert list(win._beat_rows) == [0, 1]
            assert win._row_of_beat(1) == 1
        finally:
            win.close()


# ======================================================================
#  ② 变量工作台：if / for 块内插入
# ======================================================================

@pytest.fixture
def blocks(app, tmp_path):
    from storygraph.app.editor import EditorSession
    from storygraph.graph.models import Graph

    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / 'blocks.rpy'
    script.write_text(BLOCK_SCRIPT, encoding='utf-8')

    session = EditorSession(project_root=root)
    res = session.import_chapters([script], Graph())
    node = next(n for n in res['graph'].nodes.values()
                if n.label_name == 'vars_main')
    session.load(script, label='vars_main')
    return session, res['graph'], node, script, root


class TestBlockInsertion:
    def test_workbench_reports_the_enclosing_block(self, blocks):
        session, graph, node, _script, _root = blocks
        data = session.variable_workbench(graph, node.id)
        ops = {op['text']: op for row in data['variables']
               for op in row['operations']}

        assert ops['love += 1']['block'] == ''
        assert ops['route = "roof"']['block'] == 'if love > 3:'
        assert ops['counter += i']['block'] == 'for i in range(2):'
        assert ops['route = "roof"']['indent'] == 8
        assert ops['love += 1']['indent'] == 4

    def test_anchors_put_each_new_op_into_its_own_block(self, blocks):
        session, graph, node, _script, _root = blocks
        data = session.variable_workbench(graph, node.id)
        old = list(data['operations'])
        beat_if = next(op['beat_index'] for row in data['variables']
                       for op in row['operations']
                       if op['text'] == 'route = "roof"')
        beat_for = next(op['beat_index'] for row in data['variables']
                        for op in row['operations']
                        if op['text'] == 'counter += i')

        out = session.apply_node_variable_edits(
            graph, node.id, old + ['luck = 1', 'total += 1'],
            anchors={len(old): beat_if, len(old) + 1: beat_for})

        assert out['ok'] and out['unplaced'] == 0
        assert out['inserted'] == [beat_if, beat_for]
        text = str(session.compile().rpy_text or '')
        assert '\n        $ luck = 1\n' in text, '没落进 if 块（缩进 8）'
        assert '\n        $ total += 1\n' in text, '没落进 for 块（缩进 8）'
        assert text.index('$ luck = 1') > text.index('if love > 3:')
        assert text.index('$ total += 1') > text.index('for i in range(2):')

    def test_end_placement_stays_top_level(self, blocks):
        """用户选"本章末尾"时用一个越界锚点（-1）明确表达，不许退回某一块。

        ⚠ 2026-11-08 改判据：从前这条断言的是"末尾 = **最后一拍**"，而那一拍
        是 `return`（控制流）—— 落在它上面的代码**永远执行不到**（真机
        `$ Plotnode223=1` 就是这么被写进 `演出剧本/chapter2_2_2.rpy` 的，
        见 `docs/开发日志.md` §114）。现在"本章末尾"= 最后一个**能装代码**的拍
        （`return` 前面那一张）；不变的是"不许退回 `if` / `for` 块里"。
        """
        session, graph, node, _script, _root = blocks
        data = session.variable_workbench(graph, node.id)
        old = list(data['operations'])
        out = session.apply_node_variable_edits(
            graph, node.id, old + ['ending = True'],
            insert_beat=1, anchors={len(old): -1})

        flow_at = next(i for i, b in enumerate(session.beats)
                       if str(getattr(b, 'kind', '')) == 'flow')
        assert out['inserted'] and out['inserted'][0] == flow_at - 1, \
            f'本章末尾 = `return` 前面那一拍（第 {flow_at - 1} 拍）'
        text = str(session.compile().rpy_text or '')
        assert '\n    $ ending = True\n' in text, '顶层缩进应是 4'
        assert text.index('$ ending = True') < text.index('return'), \
            '不能落在 `return` 后面（死代码）'

    def test_block_header_with_a_trailing_comment_is_still_a_block(self, app,
                                                                   tmp_path):
        from storygraph.app.editor import EditorSession
        from storygraph.graph.models import Graph

        root = build_project(tmp_path / 'proj')
        script = root / 'game' / 'script' / 'comment_block.rpy'
        script.write_text('label a:\n    if flag:  # 说明\n        $ x = 1\n'
                          '    e "z"\n', encoding='utf-8')
        session = EditorSession(project_root=root)
        res = session.import_chapters([script], Graph())
        node = next(n for n in res['graph'].nodes.values()
                    if n.label_name == 'a')
        data = session.variable_workbench(res['graph'], node.id)
        op = next(op for row in data['variables']
                  for op in row['operations'] if op['text'] == 'x = 1')
        assert op['block'] == 'if flag:', op

    def test_dialog_keeps_per_op_anchors(self, blocks, monkeypatch):
        from storygraph.ui_qt import dialogs as D

        session, graph, node, _script, _root = blocks
        data = session.variable_workbench(graph, node.id)
        dlg = D.VariableWorkbenchDialog(None, label_name='vars_main', data=data)

        rows = [dlg._ops.item(i).text() for i in range(dlg._ops.count())]
        assert any('在 if love > 3:' in t for t in rows), rows

        # 选中 if 里那条 → 新增一条
        if_row = next(i for i, t in enumerate(rows) if 'route = "roof"' in t)
        dlg._ops.setCurrentRow(if_row)
        assert 'if love > 3:' in dlg._target_hint.text()

        answers = iter(['luck = 1', 'total += 1'])
        monkeypatch.setattr(
            D.QInputDialog, 'getText',
            staticmethod(lambda *a, **k: (next(answers), True)))
        dlg._add_op()

        # 再选中 for 里那条 → 新增另一条
        for_row = next(i for i in range(dlg._ops.count())
                       if 'counter += i' in dlg._ops.item(i).text())
        dlg._ops.setCurrentRow(for_row)
        dlg._add_op()

        # 删掉前面一条 → 下标整体前移，锚点必须跟着走
        dlg._ops.setCurrentRow(0)              # love += 1
        dlg._delete_op()
        dlg._on_accept()

        assert dlg.variables[0] == 'route = "roof"'
        first_new = len(dlg.variables) - 2
        assert dlg.anchors[first_new] == beat_if_of(data), \
            '删了一条之后锚点没重排'
        assert dlg.anchors[first_new + 1] == beat_of(data, 'counter += i')

    def test_window_writes_block_ops_through_the_workbench(self, blocks,
                                                          monkeypatch):
        from storygraph.ui_qt import dialogs as D

        session, graph, node, script, root = blocks
        data = session.variable_workbench(graph, node.id)
        old = list(data['operations'])
        anchors = {len(old): beat_if_of(data),
                   len(old) + 1: beat_of(data, 'counter += i')}
        win = _window(root, script)
        try:
            win._set_graph(graph)
            seen = {}

            class _Fake:
                def __init__(self, parent=None, label_name='', data=None,
                             on_check=None):
                    seen['data'] = data
                    self.variables = list(data['operations']) + [
                        'luck = 1', 'total += 1']
                    self.jump_index = None
                    self.insert_beat = None
                    self.anchors = anchors

                def exec(self):
                    return D.QDialog.Accepted

            monkeypatch.setattr(D, 'VariableWorkbenchDialog', _Fake)
            win.show_variable_workbench(node.id)

            assert seen['data']['variables'], '工作台没拿到数据'
            codes = '\n'.join(b.code for b in win._session.beats)
            assert 'luck = 1' in codes and 'total += 1' in codes
            text = str(win._session.compile().rpy_text or '')
            assert '\n        $ luck = 1\n' in text
            assert '\n        $ total += 1\n' in text
        finally:
            win.close()


def beat_if_of(data) -> int:
    return next(op['beat_index'] for row in data['variables']
                for op in row['operations'] if op['text'] == 'route = "roof"')


def beat_of(data, text) -> int:
    return next(op['beat_index'] for row in data['variables']
                for op in row['operations'] if op['text'] == text)


# ======================================================================
#  ③ 关窗三选一
# ======================================================================

class TestCloseConfirm:
    def _dirty_window(self, app, root):
        from storygraph.graph.models import Graph
        win = _window(root)
        graph = Graph()
        graph.add_node('a', 0, 0)
        graph.add_node('b', 400, 0)
        win._set_graph(graph)
        win.show()
        app.processEvents()
        assert win._graph_dirty, '测试前提：图有未保存改动'
        return win

    def test_cancel_really_keeps_the_window(self, app, tmp_path):
        root = build_project(tmp_path / 'proj')
        win = self._dirty_window(app, root)
        win._ask_close_graph_action = lambda: 'cancel'
        win.close()
        app.processEvents()

        assert win.isVisible(), '点了取消，窗口不该关'
        assert win._graph_dirty  is True
        assert not (root / LAYOUT_REL).exists(), \
            '取消不该偷偷写文件'
        win._ask_close_graph_action = lambda: 'discard'
        win.close()

    def test_discard_closes_without_writing(self, app, tmp_path):
        root = build_project(tmp_path / 'proj')
        win = self._dirty_window(app, root)
        win._ask_close_graph_action = lambda: 'discard'
        win.close()
        app.processEvents()

        assert not win.isVisible()
        assert not (root / LAYOUT_REL).exists(), \
            '选了不保存还写盘，等于骗人'

    def test_save_writes_the_layout(self, app, tmp_path):
        import json
        root = build_project(tmp_path / 'proj')
        win = self._dirty_window(app, root)
        win._ask_close_graph_action = lambda: 'save'
        win.close()
        app.processEvents()

        target = root / LAYOUT_REL
        assert not win.isVisible()
        assert target.exists(), '选了保存却没写文件'
        data = json.loads(target.read_text(encoding='utf-8'))
        assert data.get('nodes'), data

    def test_save_failure_keeps_the_window_open(self, app, tmp_path,
                                                monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        root = build_project(tmp_path / 'proj')
        win = self._dirty_window(app, root)
        win._ask_close_graph_action = lambda: 'save'
        win._save_layout_to = lambda target: {'ok': False, 'error': '磁盘满了'}
        monkeypatch.setattr(QMessageBox, 'warning', staticmethod(lambda *a: None))

        win.close()
        app.processEvents()
        assert win.isVisible(), '保存失败还关窗 = 图直接没了'
        win._ask_close_graph_action = lambda: 'discard'
        win.close()

    def test_hidden_window_does_not_prompt(self, app, tmp_path):
        """没显示过的窗口（测试 / 嵌入）不弹框：沿用自动保存，别把进程挂住。"""
        from storygraph.graph.models import Graph
        root = build_project(tmp_path / 'proj')
        win = _window(root)
        graph = Graph()
        graph.add_node('a', 0, 0)
        win._set_graph(graph)

        def _boom():
            raise AssertionError('没显示过的窗口不该弹三选一')

        win._ask_close_graph_action = _boom
        win.close()
        app.processEvents()
        assert (root / LAYOUT_REL).exists()

    def test_clean_graph_closes_without_prompt(self, app, tmp_path):
        root = build_project(tmp_path / 'proj')
        win = _window(root)
        win.show()
        app.processEvents()
        assert win._graph_dirty is False
        win._ask_close_graph_action = lambda: (_ for _ in ()).throw(
            AssertionError('干净的图不该问'))
        win.close()
        app.processEvents()
        assert not win.isVisible()
