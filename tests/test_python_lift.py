"""
tests/test_python_lift.py
=========================
2026-10-22 用户定下的方向（不是"工具去适应 `python:` 块"）：

> 真机那 26 条里 21 条是脚本层 `$` 就写得出来的赋值。它们待在 python 块里
> 不只是"工具管不着"，而是有害的 —— 存档粒度是整块。
> ……正确的方向不是"工具去适应 python 块"，是"工具帮用户把语句从 python
> 块里搬出来"。

判据分四层，都不靠截图：

  ① `story.python_lift.analyze_block`（纯函数）：哪些行能搬、哪些不能、为什么；
  ② 会话的 `python_lift_report`：块 / 拍 / 图节点语句三者对上号，且**只读**；
  ③ `apply_python_lift`：编译文本真的变成 `$` 行、块标记与节点语句一起改、
     注释留住、撤销 / 重做两边一致、导出文本再解析**同构**；
  ④ 界面入口：右栏按钮 + 右键菜单，走同一条 `lift_python_assignments`。
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
pytest.importorskip('PySide6')

from PySide6.QtWidgets import QApplication, QDialog                # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project                              # noqa: E402

#: 三块：纯赋值 / 带调用与嵌套 / 带注释
SCRIPT = '''label ch_lift:
    $ love = 0
    e "a"
    python:
        route = "roof"
        love += 2
        flag = True
    e "b"
    python:
        if love > 3:
            love = 3
        msg_add("x")
        love += 5
    python:
        # 这条注释需要一条语句陪着
        a = 1
        b = 2
    e "c"
'''

#: 条件块（`if` 外面那一层）：搬出来之后要仍在 `if` 里面
COND_SCRIPT = '''label ch_cond:
    if love > 3:
        python:
            c = 1
            d = 2
        e "y"
'''


# ======================================================================
#  ① 纯函数：判定
# ======================================================================

class TestAnalyzeBlock:
    def test_pure_assignments_all_move(self):
        from storygraph.story.python_lift import analyze_block
        plan = analyze_block(['route = "roof"', 'love += 2', 'flag = True'])
        assert plan['ok']
        assert plan['lift'] == [0, 1, 2]
        assert plan['keep_ranges'] == []
        assert [r['target'] for r in plan['candidates']] == \
            ['route', 'love', 'flag']

    def test_calls_and_nested_lines_stay(self):
        from storygraph.story.python_lift import analyze_block
        plan = analyze_block(['if love > 3:', '    love = 3',
                              'msg_add("x")', 'love += 5'])
        assert plan['lift'] == [3], '只该搬最后那条顶层赋值'
        assert plan['keep_ranges'] == [(0, 2)]
        reasons = {r['line']: r['why'] for r in plan['skipped']}
        assert '嵌套' in reasons[1]

    def test_comment_needs_a_statement_beside_it(self):
        """夹在中间/开头的注释要留一条语句陪着（`python:` 里只有注释是语法错）。"""
        from storygraph.story.python_lift import analyze_block
        plan = analyze_block(['a = 1', '# note', 'b = 2', 'c = 3'])
        assert plan['ok']
        assert 0 not in plan['lift'], 'a=1 得留下陪注释'
        assert plan['lift'] == [2, 3]
        assert plan['keep_ranges'] == [(0, 1)]

    def test_comment_at_the_end_blocks_everything(self):
        from storygraph.story.python_lift import analyze_block
        plan = analyze_block(['a = 1', '# note at end'])
        assert plan['ok'] is False
        assert '注释' in plan['why']

    def test_multiline_assignment_is_not_moved(self):
        from storygraph.story.python_lift import analyze_block
        plan = analyze_block(['x = [1,', '     2]', 'y = 3'])
        assert plan['lift'] == [2]
        assert plan['keep_ranges'] == [(0, 1)]

    def test_attribute_unpack_and_private_targets_are_not_moved(self):
        from storygraph.story.python_lift import analyze_block
        plan = analyze_block(['persistent.x = 1', 'a, b = 1, 2',
                              '_tmp = 3', 'love = 4'])
        assert plan['lift'] == [3]

    def test_blank_lines_do_not_block(self):
        from storygraph.story.python_lift import analyze_block
        plan = analyze_block(['a = 1', '', 'b = 2'])
        assert plan['lift'] == [0, 2]
        assert plan['keep_ranges'] == []

    def test_broken_python_is_refused_with_a_reason(self):
        from storygraph.story.python_lift import analyze_block
        plan = analyze_block(['if x', '    y = 1'])
        assert plan['ok'] is False
        assert '解析' in plan['why'] or '语法' in plan['why']

    def test_block_of_only_calls_has_nothing_to_move(self):
        from storygraph.story.python_lift import analyze_block
        plan = analyze_block(['msg_add("x")', 'renpy.notify("y")'])
        assert plan['ok'] is False
        assert plan['lift'] == []

    def test_single_line_compound_statement_is_not_an_assignment(self):
        from storygraph.story.python_lift import analyze_block
        plan = analyze_block(['for i in range(2): love += i', 'x = 1'])
        assert plan['lift'] == [1]


# ======================================================================
#  ②③ 会话：报告与落地
# ======================================================================

@pytest.fixture(scope='session')
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture
def chapter(app, tmp_path):
    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / 'lift.rpy'
    script.write_text(SCRIPT, encoding='utf-8')
    from storygraph.app.editor import EditorSession
    from storygraph.graph.models import Graph
    session = EditorSession(project_root=root)
    res = session.import_chapters([script], Graph())
    node = next(n for n in res['graph'].nodes.values()
                if n.label_name == 'ch_lift')
    return session, res['graph'], node, script


def _all_picks(report):
    return [{'beat': b['beat'], 'start': b['start'], 'end': b['end'],
             'lift': list(b['plan']['lift'])}
            for b in report['blocks'] if b['plan'].get('ok')]


class TestReport:
    def test_blocks_map_to_beats_and_statements(self, chapter):
        session, graph, node, _script = chapter
        rep = session.python_lift_report(graph, node.id)
        assert rep['ok']
        assert rep['stats']['blocks'] == 3
        assert rep['stats']['safe_lines'] == 3 + 1 + 1  # 见 SCRIPT：第 3 块只搬 b
        ids = [b['id'] for b in rep['blocks']]
        # 后两个 `python:` 块中间没有台词/演出 → 被折叠进**同一拍**
        # （`code_blocks` 是 [(1,4), (5,7)]），所以 beat 下标都是 2。
        assert ids == ['1:1-3', '2:1-4', '2:5-7']
        for b in rep['blocks']:
            assert b['stmt_index'] >= 0, '块没对上图节点语句'
            assert b['header_line'] > 0

    def test_report_is_read_only(self, chapter):
        session, graph, node, _script = chapter
        assert session.script_path is None and not session.beats
        session.python_lift_report(graph, node.id)
        assert session.script_path is None, '报告把当前编辑的章节换掉了'
        assert not session.beats

    def test_node_without_source_file_says_so(self, chapter):
        from storygraph.graph.models import Graph
        session, _graph, _node, _script = chapter
        g = Graph()
        nid = g.add_node('no_file', 0, 0)
        rep = session.python_lift_report(g, nid)
        assert rep['ok'] is False and '源文件' in rep['error']


class TestApply:
    def test_lift_turns_them_into_dollar_lines(self, chapter):
        session, graph, node, _script = chapter
        rep = session.python_lift_report(graph, node.id)
        res = session.apply_python_lift(graph, node.id, _all_picks(rep))
        assert res['ok'] and res['lifted'] == 5
        text = session.compile().rpy_text
        assert '$ route = "roof"' in text
        assert '$ love += 2' in text
        assert '$ flag = True' in text
        assert 'python:\n        route = "roof"' not in text, '块还被包着'
        # 有调用/嵌套的那块继续是 python 块，且只留下它自己的行
        assert 'python:\n        if love > 3:' in text
        assert '$ love += 5' in text
        # 注释陪着的块：只搬了 b = 2
        assert 'python:\n        # 这条注释需要一条语句陪着\n        a = 1' in text
        assert '$ b = 2' in text

    def test_node_statements_are_split_with_source_lines(self, chapter):
        session, graph, node, _script = chapter
        rep = session.python_lift_report(graph, node.id)
        old = [s.payload.get('code') for s in node.statements]
        session.apply_python_lift(graph, node.id, _all_picks(rep))
        codes = [s.payload.get('code') for s in node.statements]
        assert codes != old
        assert 'route = "roof"' in codes and 'flag = True' in codes
        # 每条都能对到一行源文件（工作台"定位到这一拍"要靠它）
        lines = [int((s.source or ('', 0))[1]) for s in node.statements]
        assert all(n > 0 for n in lines)
        assert lines == sorted(lines), '拆出来的语句顺序/行号乱了'

    def test_partial_lift_keeps_the_block(self, chapter):
        session, graph, node, _script = chapter
        rep = session.python_lift_report(graph, node.id)
        block = rep['blocks'][1]                 # 有调用/嵌套的那块
        last = block['plan']['lift'][-1]
        res = session.apply_python_lift(graph, node.id, [{
            'beat': block['beat'], 'start': block['start'],
            'end': block['end'], 'lift': [last]}])
        assert res['ok'] and res['lifted'] == 1
        text = session.compile().rpy_text
        assert 'python:\n        if love > 3:\n            love = 3\n        msg_add("x")' in text
        assert '$ love += 5' in text

    def test_conditional_block_stays_inside_the_if(self, app, tmp_path):
        root = build_project(tmp_path / 'proj')
        script = root / 'game' / 'script' / 'cond.rpy'
        script.write_text(COND_SCRIPT, encoding='utf-8')
        from storygraph.app.editor import EditorSession
        from storygraph.graph.models import Graph
        session = EditorSession(project_root=root)
        res = session.import_chapters([script], Graph())
        node = next(n for n in res['graph'].nodes.values()
                    if n.label_name == 'ch_cond')
        rep = session.python_lift_report(res['graph'], node.id)
        session.apply_python_lift(res['graph'], node.id, _all_picks(rep))
        text = session.compile().rpy_text
        assert 'if love > 3:\n        $ c = 1\n        $ d = 2' in text
        assert all(s.condition == 'love > 3' for s in node.statements)

    def test_undo_and_redo_both_sides(self, chapter):
        from storygraph.graph.commands import LiftPythonStatementsCommand
        session, graph, node, _script = chapter
        rep = session.python_lift_report(graph, node.id)
        before_ops = list(node.operations)
        res = session.apply_python_lift(graph, node.id, _all_picks(rep))
        after = {i: session.snapshot(i) for i in res['beats']}

        class _View:
            def __init__(self, g):
                self.graph = g

            def on_graph_content_changed(self, _nid=None):
                pass

        cmd = LiftPythonStatementsCommand(
            _View(graph), node.id, res['old_statements'],
            res['new_statements'],
            {'session': session, 'before': res['beat_snapshots'],
             'after': after})
        assert 'route = "roof"' in node.operations
        cmd.undo()
        assert node.operations == before_ops, '节点语句没退回去'
        assert 'python:\n        route = "roof"' in session.compile().rpy_text, \
            '块标记没退回去（界面说撤了、导出还是 $）'
        cmd.execute()
        assert '$ route = "roof"' in session.compile().rpy_text
        assert 'route = "roof"' in node.operations

    def test_exported_text_reparses_to_itself(self, chapter, tmp_path):
        session, graph, node, script = chapter
        rep = session.python_lift_report(graph, node.id)
        session.apply_python_lift(graph, node.id, _all_picks(rep))
        text1 = session.compile().rpy_text
        out = tmp_path / 'again.rpy'
        out.write_text(text1, encoding='utf-8')

        from storygraph.app.editor import EditorSession
        again = EditorSession(project_root=script.parents[2])
        again.load(out)
        assert again.compile().rpy_text == text1, '搬完导出再解析，文本变了'
        assert all(not b.code_blocks for b in again.beats
                   if 'route' in str(b.code)), '搬出去的行又被解析回块里了'


# ======================================================================
#  ④ 界面入口
# ======================================================================

class _Engine:
    def __init__(self, root=None):
        self.project_root = root

    def get_registry(self):
        return None


def _window(root):
    from storygraph.ui_qt.main_window import MainWindowQt
    return MainWindowQt(_Engine(root), None)


def _close(win):
    try:
        win._ask_close_action = lambda state: 'discard'
        win.close()
    except Exception:                      # noqa: BLE001 - 收尾失败不影响判据
        pass


class TestWindowEntry:
    def test_button_says_what_it_does(self, app, tmp_path):
        win = _window(None)
        try:
            assert 'python' in win._lift_btn.text()
            assert '搬出' in win._lift_btn.text()
        finally:
            _close(win)

    def test_lift_from_the_window_marks_and_logs(self, app, tmp_path,
                                                 monkeypatch):
        root = build_project(tmp_path / 'proj')
        script = root / 'game' / 'script' / 'lift.rpy'
        script.write_text(SCRIPT, encoding='utf-8')
        win = _window(root)
        try:
            win.import_chapter_files([script])
            node = next(n for n in win.graph.nodes.values()
                        if n.label_name == 'ch_lift')
            from storygraph.ui_qt import dialogs

            def fake_exec(self):
                self._set_all(True)
                self._on_accept()
                return QDialog.Accepted
            monkeypatch.setattr(dialogs.PythonLiftDialog, 'exec', fake_exec)

            win.lift_python_assignments(node.id)
            text = win._session.compile().rpy_text
            assert '$ route = "roof"' in text
            assert '已搬出' in win._status_bar.currentMessage()
            assert '[搬出]' in win._console.toPlainText()
            # 撤销要把两边一起退（这是第七阶段定下的规矩）
            win.undo_graph()
            assert 'python:\n        route = "roof"' in win._session.compile().rpy_text
            assert 'route = "roof"\nlove += 2' in (
                win.graph.nodes[node.id].operations)[1]
        finally:
            _close(win)

    def test_chapter_without_python_blocks_says_so(self, app, tmp_path):
        root = build_project(tmp_path / 'proj')
        script = root / 'game' / 'script' / 'plain.rpy'
        script.write_text('label plain:\n    e "x"\n', encoding='utf-8')
        win = _window(root)
        try:
            win.import_chapter_files([script])
            node = next(n for n in win.graph.nodes.values()
                        if n.label_name == 'plain')
            win.lift_python_assignments(node.id)
            assert '没有 python 块' in win._status_bar.currentMessage()
        finally:
            _close(win)

    def test_help_and_workbench_point_at_it(self, app):
        """说明里要写"方向"，工作台里要写"为什么这里改不了块里的行"。"""
        from storygraph.ui_qt import help_dialog as H
        assert '搬出 python 块' in H._GRAPH
        assert '不搬' in H._GRAPH

        from storygraph.ui_qt.dialogs import VariableWorkbenchDialog
        from PySide6.QtWidgets import QLabel
        dlg = VariableWorkbenchDialog(
            None, label_name='x',
            data={'label': 'x', 'file': '', 'variables': [],
                  'operations': ['a = 1', 'b = 2\nc = 3']})
        try:
            texts = [w.text() for w in dlg.findChildren(QLabel)]
            assert any('python 块' in t and '搬出' in t for t in texts), texts
        finally:
            dlg.close()
