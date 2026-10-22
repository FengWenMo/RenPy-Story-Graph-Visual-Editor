"""
tests/test_feedback_p4_block_target.py
=====================================
2026-10-22 体验报告 §十 第一条（"块首插入插到指定的块"）的离线判据，
外加这轮复查抓到的**撤销一致性** bug 的判据：

  A. 变量工作台多一条「这一章的所有块」：不用先选中某条操作，
     也能把新增的变量操作插进指定 `if` / `while` 的块首或块尾；
  B. 变量工作台 / 变量框的改动**同时**落在图节点与拍上 ——
     Ctrl+Z（图级撤销）必须两边一起退回去，否则"工作台说没了、
     导出里还在"（实测过的假撤销，见第六阶段文档补记）。

都不靠截图：断言 `variable_workbench()` 的数据、下拉项、锚点元组、
编译出来的 `.rpy` 文本、以及撤销后 `compile()` 里还有没有那一行。
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
pytest.importorskip('PySide6')

from PySide6.QtCore import QSettings                              # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog                # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project                             # noqa: E402

#: 只用 Ren'Py 真有的块语句（`if` / `while`）：
#: 8.4.1 的 lint 对 label 顶层的 `for` 报 "expected statement"，
#: 拿它当"块"会把测试建立在引擎读不了的写法上（踩过一次）。
BLOCKS = '''default love = 0

label vars_main:
    $ love += 1
    if love > 3:
        $ route = "roof"
        $ flag = True
    while love < 9:
        $ love += 2
    e "done"
    return

label plain:
    e "没有块"
    return
'''

NO_BLOCK = '''label only_say:
    e "一"
    e "二"
    return
'''


@pytest.fixture(scope='session')
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture(autouse=True)
def pref_store(tmp_path, monkeypatch):
    """折叠偏好写临时 ini（与 p3 同一条理由：别拿用户注册表当夹具）。"""
    from storygraph.ui_qt import prefs
    monkeypatch.setattr(
        prefs, 'settings_factory',
        staticmethod(lambda: QSettings(str(tmp_path / 'prefs.ini'),
                                      QSettings.IniFormat)))


class _Engine:
    def __init__(self, root=None):
        self.project_root = root
        self._registry = None

    def get_registry(self):
        return self._registry


def _session(tmp_path, text, name='blocks.rpy', label='vars_main'):
    from storygraph.app.editor import EditorSession
    from storygraph.graph.models import Graph

    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / name
    script.write_text(text, encoding='utf-8')
    session = EditorSession(project_root=root)
    res = session.import_chapters([script], Graph())
    node = next(n for n in res['graph'].nodes.values()
                if n.label_name == label)
    session.load(script, label=label)
    return session, res['graph'], node, script, root


def _window(root, script):
    from storygraph.ui_qt.main_window import MainWindowQt
    return MainWindowQt(_Engine(root), script)


def _place_index(dlg, prefix):
    for i in range(dlg._place.count()):
        if str(dlg._place.itemText(i)).startswith(prefix):
            return i
    raise AssertionError(f'下拉里没有以 {prefix!r} 开头的项：'
                         + ' | '.join(str(dlg._place.itemText(i))
                                      for i in range(dlg._place.count())))


# ======================================================================
#  A. 块清单与"插到指定的块"
# ======================================================================

class TestChapterBlocks:
    def test_blocks_come_from_beats_not_from_single_operations(
            self, app, tmp_path):
        session, graph, node, _script, _root = _session(tmp_path, BLOCKS)
        data = session.variable_workbench(graph, node.id)

        assert [b['text'] for b in data['blocks']] == [
            'if love > 3:', 'while love < 9:'], data['blocks']
        if_blk, loop_blk = data['blocks']
        assert if_blk['anchor'] == if_blk['beat'], 'if 的块体就在头那一拍'
        assert loop_blk['anchor'] == loop_blk['beat'] + 1, \
            '循环体在下一拍：锚点必须往后挪，行才落得进循环'
        assert loop_blk['kind'] == 'loop' and if_blk['kind'] == 'if'

    def test_blocks_are_scoped_to_the_label(self, app, tmp_path):
        session, graph, node, _script, _root = _session(tmp_path, BLOCKS)
        data = session.variable_workbench(graph, node.id)
        assert all(b['beat'] < 7 for b in data['blocks']), \
            '`plain` 这个 label 的拍不该混进 vars_main 的块清单'

    def test_a_chapter_without_blocks_lists_nothing(self, app, tmp_path):
        session, graph, node, _script, _root = _session(
            tmp_path, NO_BLOCK, name='plain.rpy', label='only_say')
        data = session.variable_workbench(graph, node.id)
        assert data['blocks'] == []


class TestBlockTargetInTheDialog:
    def test_dialog_lists_block_entries_after_a_separator(self, app, tmp_path):
        from storygraph.ui_qt.dialogs import VariableWorkbenchDialog

        session, graph, node, _script, _root = _session(tmp_path, BLOCKS)
        data = session.variable_workbench(graph, node.id)
        dlg = VariableWorkbenchDialog(None, label_name='vars_main', data=data)

        texts = [str(dlg._place.itemText(i)) for i in range(dlg._place.count())]
        assert texts[:3] == ['选中这条之后（同一块内）',
                             '块首（选中这条的块最前面）',
                             '本章末尾（顶层）'], texts
        assert '块首：if love > 3:（第 2 拍）' in texts, texts
        assert '块尾：while love < 9:（第 4 拍）' in texts, \
            '循环块的拍号要写成**落点拍**（与右侧提示同一口径）'
        assert any(not t for t in texts), '块清单前面应当有一个分隔项'

    def test_block_target_works_without_selecting_an_operation(
            self, app, tmp_path, monkeypatch):
        """以前"块首"必须先在列表里点一条；现在选块就行。"""
        from storygraph.ui_qt import dialogs as D

        session, graph, node, _script, _root = _session(tmp_path, BLOCKS)
        data = session.variable_workbench(graph, node.id)
        dlg = D.VariableWorkbenchDialog(None, label_name='vars_main', data=data)
        dlg._ops.setCurrentRow(-1)

        dlg._place.setCurrentIndex(_place_index(dlg, '块首：if love > 3:'))
        assert '会插进第 2 拍的「if love > 3:」块最前面' \
            in dlg._target_hint.text(), dlg._target_hint.text()

        monkeypatch.setattr(
            D.QInputDialog, 'getText',
            staticmethod(lambda *a, **k: ('chosen = 1', True)))
        dlg._add_op()
        i = len(dlg.variables) - 1
        dlg._on_accept()
        assert dlg.anchors[i] == (1, 'head'), dlg.anchors
        assert '块最前面' in dlg._hint.text(), dlg._hint.text()

    def test_block_tail_anchors_on_the_body_beat(self, app, tmp_path):
        from storygraph.ui_qt import dialogs as D

        session, graph, node, _script, _root = _session(tmp_path, BLOCKS)
        data = session.variable_workbench(graph, node.id)
        dlg = D.VariableWorkbenchDialog(None, label_name='vars_main', data=data)
        dlg._place.setCurrentIndex(_place_index(dlg, '块尾：while'))
        dlg._ops.setCurrentRow(-1)

        assert dlg._anchor_for_new() == 3, \
            'while 的块体在第 4 拍（下标 3），块尾要落进循环里面'

    def test_old_modes_are_unchanged(self, app, tmp_path):
        """老三项（跟随选中 / 块首 / 本章末尾）的语义不许被这轮改掉。"""
        from storygraph.ui_qt import dialogs as D

        session, graph, node, _script, _root = _session(tmp_path, BLOCKS)
        data = session.variable_workbench(graph, node.id)
        dlg = D.VariableWorkbenchDialog(None, label_name='vars_main', data=data)

        dlg._place.setCurrentIndex(dlg._place.findData('end'))
        assert dlg._anchor_for_new() == -1
        dlg._place.setCurrentIndex(dlg._place.findData('head'))
        dlg._ops.setCurrentRow(-1)
        assert dlg._anchor_for_new() is None, '没选中就不能猜块首的落点'
        assert '先选一条' in dlg._target_hint.text()

    def test_inserts_into_the_chosen_block_without_a_selection(
            self, app, tmp_path):
        from storygraph.ui_qt.dialogs import VariableWorkbenchDialog

        session, graph, node, _script, _root = _session(tmp_path, BLOCKS)
        data = session.variable_workbench(graph, node.id)
        dlg = VariableWorkbenchDialog(None, label_name='vars_main', data=data)
        dlg._ops.setCurrentRow(-1)
        dlg._place.setCurrentIndex(_place_index(dlg, '块首：if love > 3:'))
        dlg.variables.append('chosen = 1')
        dlg._anchors[len(dlg.variables) - 1] = dlg._anchor_for_new()
        dlg._on_accept()

        out = session.apply_node_variable_edits(
            graph, node.id, dlg.variables, anchors=dlg.anchors)
        assert out['ok'] and out['unplaced'] == 0
        assert out['inserted'] == [1], out
        text = str(session.compile().rpy_text or '')
        assert ('if love > 3:\n        $ chosen = 1\n'
                '        $ route = "roof"') in text, text

    def test_window_path_uses_the_chosen_block(self, app, tmp_path,
                                               monkeypatch):
        from storygraph.ui_qt import dialogs as D

        session, graph, node, script, root = _session(tmp_path, BLOCKS)
        data = session.variable_workbench(graph, node.id)
        old = list(data['operations'])
        # 选中的是**循环**的块尾（body 那一拍 = 下标 3），一次走完窗口那条路。
        anchors = {len(old): 3}

        win = _window(root, script)
        try:
            win._set_graph(graph)

            class _Fake:
                def __init__(self, parent=None, label_name='', data=None,
                             on_check=None):
                    self.variables = list(data['operations']) + ['tail = 2']
                    self.jump_index = None
                    self.insert_beat = None
                    self.anchors = anchors

                def exec(self):
                    return D.QDialog.Accepted

            monkeypatch.setattr(D, 'VariableWorkbenchDialog', _Fake)
            win.show_variable_workbench(node.id)

            text = str(win._session.compile().rpy_text or '')
            assert ('while love < 9:\n        $ love += 2\n'
                    '        $ tail = 2\n') in text, text
        finally:
            win._ask_close_action = lambda state: 'discard'
            win.close()


# ======================================================================
#  B. 撤销一致性：节点与拍必须一起退（这轮复查找出来的假撤销）
# ======================================================================

class TestWorkbenchUndoKeepsBeatsInSync:
    def _applied(self, app, tmp_path, monkeypatch):
        from storygraph.ui_qt import dialogs as D

        session, graph, node, script, root = _session(tmp_path, BLOCKS)
        win = _window(root, script)
        win._set_graph(graph)
        data = session.variable_workbench(graph, node.id)
        old = list(data['operations'])
        anchors = {len(old): (1, 'head')}          # if 的块首

        class _Fake:
            def __init__(self, parent=None, label_name='', data=None,
                         on_check=None):
                self.variables = list(data['operations']) + ['picked = 1']
                self.jump_index = None
                self.insert_beat = None
                self.anchors = anchors

            def exec(self):
                return D.QDialog.Accepted

        monkeypatch.setattr(D, 'VariableWorkbenchDialog', _Fake)
        win.show_variable_workbench(node.id)
        # **窗口自己的会话**才是被改的那一份（测试里那份只是用来建图的）。
        return win, win._session, node

    def test_undo_takes_the_beat_back_too(self, app, tmp_path, monkeypatch):
        win, session, node = self._applied(app, tmp_path, monkeypatch)
        try:
            assert 'picked = 1' in str(session.compile().rpy_text)
            assert session.has_unsaved_edits()

            win.undo_graph()

            assert 'picked = 1' not in str(session.compile().rpy_text), \
                '图撤销了、拍没退：导出还会把这条写出去（假撤销）'
            assert all('picked = 1' not in op
                       for op in win.graph.nodes[node.id].operations)
            assert not session.has_unsaved_edits(), \
                '退回到与磁盘一致之后，状态栏不该还喊"未导出"'
        finally:
            win._ask_close_action = lambda state: 'discard'
            win.close()

    def test_redo_puts_both_back(self, app, tmp_path, monkeypatch):
        win, session, node = self._applied(app, tmp_path, monkeypatch)
        try:
            win.undo_graph()
            win.redo_graph()

            assert 'picked = 1' in str(session.compile().rpy_text), \
                '重做之后拍上那条要回来'
            assert any('picked = 1' in op
                       for op in win.graph.nodes[node.id].operations)
        finally:
            win._ask_close_action = lambda state: 'discard'
            win.close()

    def test_undo_after_a_reload_does_not_stamp_onto_other_beats(
            self, app, tmp_path, monkeypatch):
        """原地导出会重新解析文件（拍数 / 粒度可能变）—— 那时的旧快照按拍号
        硬还原会摊到别人的拍上。判据：对不上就跳过，绝不改错拍。"""
        win, session, node = self._applied(app, tmp_path, monkeypatch)
        try:
            # 模拟"导出 → 重新对齐到磁盘"：从文件重读，拍的来源换了一份。
            session.load(node.file_path, label='vars_main')

            win.undo_graph()          # 不许抛、也不许把旧快照写到新拍上

            text = str(session.compile().rpy_text or '')
            assert 'picked = 1' not in text, '磁盘那份里没有它，别凭空写回去'
            assert 'route = "roof"' in text and 'love += 2' in text, \
                '别的拍被旧快照改坏了'
        finally:
            win._ask_close_action = lambda state: 'discard'
            win.close()

