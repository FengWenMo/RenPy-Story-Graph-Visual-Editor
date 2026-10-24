"""
tests/test_feedback_p3_head_fold_close.py
=========================================
2026-10-22 体验报告 §十「再往前一步」三条的离线判据：

  ① 变量工作台能把新增操作放在**块首**（而不是只能"选中那条之后"）；
  ② 左栏 label 分组的折叠状态**记住**（存偏好，不存布局 JSON）；
  ③ 关窗询问覆盖**卡片页没导出的改动**（保存 / 不保存 / 取消三路，
     外加"表单改了还没按保存这一拍"）。

都**不靠截图**：断言 `Beat.code`、编译出来的 `.rpy` 文本、列表项状态、
`unsaved_edits()` 的计数与磁盘上那份文件的内容。
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

from PySide6.QtCore import QSettings, Qt                        # noqa: E402
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


@pytest.fixture(autouse=True)
def fold_store(tmp_path, monkeypatch):
    """
    折叠偏好写**临时 ini**，不碰用户真实注册表。

    `prefs.settings_factory` 存在的意义就是这个注入点（见 `prefs` 模块
    docstring）：测试里创建一个窗口就会读偏好，不换掉就等于拿用户的配置
    当测试夹具（而测试还会往里写）。
    """
    from storygraph.ui_qt import prefs
    ini = tmp_path / 'prefs.ini'
    monkeypatch.setattr(
        prefs, 'settings_factory',
        staticmethod(lambda: QSettings(str(ini), QSettings.IniFormat)))
    return ini


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
#  ① 块首插入
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


def beat_of(data, text) -> int:
    return next(op['beat_index'] for row in data['variables']
                for op in row['operations'] if op['text'] == text)


class TestBlockHeadInsertion:
    def test_head_anchor_puts_the_op_first_inside_the_block(self, blocks):
        session, graph, node, _script, _root = blocks
        data = session.variable_workbench(graph, node.id)
        old = list(data['operations'])
        beat_if = beat_of(data, 'route = "roof"')

        out = session.apply_node_variable_edits(
            graph, node.id, old + ['luck = 1'],
            anchors={len(old): (beat_if, 'head')})

        assert out['ok'] and out['unplaced'] == 0
        assert out['inserted'] == [beat_if]
        text = str(session.compile().rpy_text or '')
        assert '\n        $ luck = 1\n' in text, '没落进 if 块（缩进 8）'
        assert (text.index('if love > 3:') < text.index('$ luck = 1')
                < text.index('$ route = "roof"')), \
            '块首插入应该紧跟在块头下面、原来的第一条之前'

    def test_two_head_inserts_keep_the_order_they_were_entered(self, blocks):
        session, graph, node, _script, _root = blocks
        data = session.variable_workbench(graph, node.id)
        old = list(data['operations'])
        beat_if = beat_of(data, 'route = "roof"')

        session.apply_node_variable_edits(
            graph, node.id, old + ['a1 = 1', 'a2 = 2'],
            anchors={len(old): (beat_if, 'head'),
                     len(old) + 1: (beat_if, 'head')})

        text = str(session.compile().rpy_text or '')
        assert (text.index('$ a1 = 1') < text.index('$ a2 = 2')
                < text.index('$ route = "roof"')), '块首那几条的顺序反了'

    def test_head_on_a_top_level_op_goes_to_the_first_beat(self, blocks):
        """顶层没有"块首"，它等于"本章第一拍的最前面"（label 一开始就执行）。"""
        session, graph, node, _script, _root = blocks
        data = session.variable_workbench(graph, node.id)
        assert data['first_beat'] == 0
        old = list(data['operations'])

        out = session.apply_node_variable_edits(
            graph, node.id, old + ['opening = True'],
            anchors={len(old): (data['first_beat'], 'head')})

        assert out['inserted'] == [0]
        text = str(session.compile().rpy_text or '')
        assert '\n    $ opening = True\n' in text, '顶层缩进应是 4'
        assert text.index('$ opening = True') < text.index('$ love += 1')

    def test_old_int_anchor_still_appends(self, blocks):
        """老契约（只给一个整数）= 块体末尾，不能被"块首"顺手改掉。"""
        session, graph, node, _script, _root = blocks
        data = session.variable_workbench(graph, node.id)
        old = list(data['operations'])
        beat_if = beat_of(data, 'route = "roof"')

        session.apply_node_variable_edits(
            graph, node.id, old + ['luck = 1'], anchors={len(old): beat_if})

        text = str(session.compile().rpy_text or '')
        assert (text.index('$ luck = 1') > text.index('$ flag = True')), \
            '整数锚点应该追加在块体末尾'

    def test_head_insert_keeps_a_python_block_intact(self, app, tmp_path):
        """块首插一行不许把同一个块里的 `python:` 块边界挤乱。"""
        from storygraph.app.editor import EditorSession
        from storygraph.graph.models import Graph

        root = build_project(tmp_path / 'proj')
        script = root / 'game' / 'script' / 'pyblock.rpy'
        script.write_text(
            'label x:\n'
            '    if flag:\n'
            '        python:\n'
            '            a = 1\n'
            '            b = 2\n'
            '        $ c = 3\n'
            '    e "z"\n'
            '    return\n', encoding='utf-8')
        session = EditorSession(project_root=root)
        res = session.import_chapters([script], Graph())
        node = next(n for n in res['graph'].nodes.values()
                    if n.label_name == 'x')
        session.load(script, label='x')
        data = session.variable_workbench(res['graph'], node.id)
        old = list(data['operations'])

        session.apply_node_variable_edits(
            res['graph'], node.id, old + ['luck = 1'],
            anchors={len(old): (0, 'head')})

        text = str(session.compile().rpy_text or '')
        assert 'if flag:\n        $ luck = 1\n        python:\n' in text, text
        assert '\n            a = 1\n            b = 2\n' in text, text

    def test_dialog_head_mode_records_a_tuple_anchor(self, blocks, monkeypatch):
        from storygraph.ui_qt import dialogs as D

        session, graph, node, _script, _root = blocks
        data = session.variable_workbench(graph, node.id)
        dlg = D.VariableWorkbenchDialog(None, label_name='vars_main', data=data)

        rows = [dlg._ops.item(i).text() for i in range(dlg._ops.count())]
        if_row = next(i for i, t in enumerate(rows) if 'route = "roof"' in t)
        dlg._ops.setCurrentRow(if_row)
        dlg._place.setCurrentIndex(dlg._place.findData('head'))
        assert '块最前面' in dlg._target_hint.text(), dlg._target_hint.text()

        monkeypatch.setattr(
            D.QInputDialog, 'getText',
            staticmethod(lambda *a, **k: ('luck = 1', True)))
        dlg._add_op()
        i = len(dlg.variables) - 1
        assert '块首' in dlg._ops.item(dlg._ops.count() - 1).text()

        dlg._on_accept()
        assert dlg.anchors[i] == (beat_of(data, 'route = "roof"'), 'head'), \
            dlg.anchors

    def test_dialog_head_on_top_level_uses_the_first_beat(self, blocks,
                                                          monkeypatch):
        from storygraph.ui_qt import dialogs as D

        session, graph, node, _script, _root = blocks
        data = session.variable_workbench(graph, node.id)
        dlg = D.VariableWorkbenchDialog(None, label_name='vars_main', data=data)

        rows = [dlg._ops.item(i).text() for i in range(dlg._ops.count())]
        top_row = next(i for i, t in enumerate(rows) if 'love += 1' in t)
        dlg._ops.setCurrentRow(top_row)
        dlg._place.setCurrentIndex(dlg._place.findData('head'))
        assert '第 1 拍' in dlg._target_hint.text(), dlg._target_hint.text()

        monkeypatch.setattr(
            D.QInputDialog, 'getText',
            staticmethod(lambda *a, **k: ('opening = True', True)))
        dlg._add_op()
        dlg._on_accept()
        i = len(dlg.variables) - 1
        assert dlg.anchors[i] == (0, 'head'), dlg.anchors

    def test_dialog_head_without_a_selection_refuses_to_add(self, blocks,
                                                           monkeypatch):
        """块首的语义是"跟着选中那条所在的块"：没选就别硬塞一个落点。"""
        from storygraph.ui_qt import dialogs as D

        session, graph, node, _script, _root = blocks
        data = session.variable_workbench(graph, node.id)
        dlg = D.VariableWorkbenchDialog(None, label_name='vars_main', data=data)
        dlg._ops.setCurrentRow(-1)
        dlg._place.setCurrentIndex(dlg._place.findData('head'))
        before = list(dlg.variables)

        monkeypatch.setattr(
            D.QInputDialog, 'getText',
            staticmethod(lambda *a, **k: ('luck = 1', True)))
        dlg._add_op()

        assert dlg.variables == before, '没选中却加了一条（落点只能靠猜）'
        assert '块首' in dlg._hint.text()

    def test_window_writes_head_ops_through_the_workbench(self, blocks,
                                                          monkeypatch):
        from storygraph.ui_qt import dialogs as D

        session, graph, node, script, root = blocks
        data = session.variable_workbench(graph, node.id)
        old = list(data['operations'])
        anchors = {len(old): (beat_of(data, 'route = "roof"'), 'head')}
        win = _window(root, script)
        try:
            win._set_graph(graph)

            class _Fake:
                def __init__(self, parent=None, label_name='', data=None,
                             on_check=None):
                    self.variables = list(data['operations']) + ['luck = 1']
                    self.jump_index = None
                    self.insert_beat = None
                    self.anchors = anchors

                def exec(self):
                    return D.QDialog.Accepted

            monkeypatch.setattr(D, 'VariableWorkbenchDialog', _Fake)
            win.show_variable_workbench(node.id)

            text = str(win._session.compile().rpy_text or '')
            assert 'if love > 3:\n        $ luck = 1\n' in text, text
        finally:
            win._ask_close_action = lambda state: 'discard'
            win.close()


# ======================================================================
#  ② 折叠状态记住
# ======================================================================

@pytest.fixture
def multi(app, tmp_path):
    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / 'multi.rpy'
    script.write_text(MULTI_LABEL, encoding='utf-8')
    return root, script


def _open_visits(win, idx):
    win._show_index(idx)


class TestFoldedLabelsRemembered:
    def test_folded_group_survives_reopening_the_file(self, app, multi):
        root, script = multi
        win = _window(root, script)
        try:
            win._on_list_item_clicked(win._group_items['side_b'])
            assert win._list.item(win._row_of_beat(4)).isHidden()
        finally:
            win.close()

        again = _window(root, script)
        try:
            assert again._collapsed_labels == frozenset({'side_b'})
            assert '▸' in again._group_items['side_b'].text()
            assert again._list.item(again._row_of_beat(4)).isHidden(), \
                '重开之后 side_b 又全展开了 —— 偏好没读回来'
            # 落在 main_a（第 1 拍）→ 它必须是开着的
            assert not again._list.item(again._row_of_beat(0)).isHidden()
        finally:
            again.close()

    def test_navigating_into_a_folded_group_is_remembered(self, app, multi):
        """"最后看到的样子"才是记忆：跳进折着的组 → 它开着，下次也开着。"""
        root, script = multi
        win = _window(root, script)
        try:
            win._on_list_item_clicked(win._group_items['side_b'])
            win._show_index(4)                  # 导航自动展开
            assert 'side_b' not in win._collapsed_labels
        finally:
            win.close()

        again = _window(root, script)
        try:
            assert 'side_b' not in again._collapsed_labels
            assert not again._list.item(again._row_of_beat(4)).isHidden()
        finally:
            again.close()

    def test_folding_is_remembered_per_file(self, app, multi, tmp_path):
        root, script = multi
        other = root / 'game' / 'script' / 'other.rpy'
        other.write_text(MULTI_LABEL, encoding='utf-8')   # 同样的 label 名

        win = _window(root, script)
        try:
            win._on_list_item_clicked(win._group_items['side_b'])
        finally:
            win.close()

        win2 = _window(root, other)
        try:
            assert win2._collapsed_labels == frozenset(), \
                '同一份偏好串到另一个文件上了'
        finally:
            win2.close()

    def test_single_label_file_writes_no_preference(self, app, tmp_path,
                                                    fold_store):
        root = build_project(tmp_path / 'proj')
        script = root / 'game' / 'script' / 'one.rpy'
        script.write_text('label only_one:\n    e "x"\n    e "y"\n',
                          encoding='utf-8')
        win = _window(root, script)
        try:
            win._show_index(1)
        finally:
            win.close()
        text = fold_store.read_text(encoding='utf-8') if fold_store.exists() else ''
        assert 'only_one' not in text


# ======================================================================
#  ③ 关窗询问覆盖"卡片页没导出的改动"
# ======================================================================

@pytest.fixture
def chapter(app, tmp_path):
    root = build_project(tmp_path / 'proj')
    script = root / 'game' / 'script' / 'ch01.rpy'
    return root, script


def _edited_window(app, root, script):
    """打开一章、改一拍并**保存到内存**（还没导出）。"""
    win = _window(root, script)
    win.show()
    app.processEvents()
    win._show_index(0)
    win._panel.set_text('改过的台词')
    win.save_current_beat()
    assert win._session.has_unsaved_edits(), '测试前提：内存里有未导出的改动'
    return win


class TestCloseAsksAboutUnsavedBeats:
    def test_the_chapter_dirty_state_is_visible(self, app, chapter):
        root, script = chapter
        win = _edited_window(app, root, script)
        try:
            state = win._close_dirty_state()
            assert state['beats'] is True
            assert state['changed'] == 1, state
            assert state['file'].endswith('ch01.rpy')
            assert '1 拍内容改了' in win._beats_state_text(state)
        finally:
            win._ask_close_action = lambda state: 'discard'
            win.close()

    def test_cancel_really_keeps_the_window(self, app, chapter):
        root, script = chapter
        before = script.read_text(encoding='utf-8')
        win = _edited_window(app, root, script)
        win._ask_close_action = lambda state: 'cancel'
        win.close()
        app.processEvents()

        assert win.isVisible(), '点了取消，窗口不该关'
        assert win._session.has_unsaved_edits()
        assert script.read_text(encoding='utf-8') == before, '取消不该写盘'
        win._ask_close_action = lambda state: 'discard'
        win.close()

    def test_discard_closes_without_writing(self, app, chapter):
        root, script = chapter
        before = script.read_text(encoding='utf-8')
        win = _edited_window(app, root, script)
        win._ask_close_action = lambda state: 'discard'
        win.close()
        app.processEvents()

        assert not win.isVisible()
        assert script.read_text(encoding='utf-8') == before, \
            '选了不保存还写盘，等于骗人'

    def test_save_writes_the_chapter_back(self, app, chapter):
        root, script = chapter
        win = _edited_window(app, root, script)
        win._ask_close_action = lambda state: 'save'
        win.close()
        app.processEvents()

        assert not win.isVisible()
        assert '改过的台词' in script.read_text(encoding='utf-8'), \
            '选了保存却没写回这一章'
        assert not win._session.has_unsaved_edits(), '写完还报"未导出"'

    def test_save_failure_keeps_the_window_open(self, app, chapter,
                                                monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        root, script = chapter
        win = _edited_window(app, root, script)
        win._ask_close_action = lambda state: 'save'
        win._save_chapter_for_close = lambda: False
        monkeypatch.setattr(QMessageBox, 'warning', staticmethod(lambda *a: None))

        win.close()
        app.processEvents()
        assert win.isVisible(), '写不出去还关窗 = 改动直接没了'
        win._ask_close_action = lambda state: 'discard'
        win.close()

    def test_unsaved_form_counts_even_before_pressing_save(self, app, chapter):
        """只改了表单、没按「保存这一拍」也算没保存（从前会静默丢）。"""
        root, script = chapter
        win = _window(root, script)
        win.show()
        app.processEvents()
        try:
            win._show_index(0)
            win._panel.set_text('只在表单里')
            state = win._close_dirty_state()
            assert state['form'] is True, state
            assert state['beats'] is False, state
        finally:
            win._ask_close_action = lambda state: 'discard'
            win.close()

    def test_saving_the_form_on_close_writes_it_out(self, app, chapter):
        root, script = chapter
        win = _window(root, script)
        win.show()
        app.processEvents()
        win._show_index(0)
        win._panel.set_text('关窗时保存的表单')
        win._ask_close_action = lambda state: 'save'
        win.close()
        app.processEvents()

        assert not win.isVisible()
        assert '关窗时保存的表单' in script.read_text(encoding='utf-8')

    def test_status_bar_stops_lying_after_navigation(self, app, chapter):
        """翻页不该把「● 未导出」翻回「✔ 已同步」（2026-10-22 修的那条）。"""
        root, script = chapter
        win = _edited_window(app, root, script)
        try:
            assert '未导出' in win._status_bar._dirty.text()
            win._show_index(1)
            assert '未导出' in win._status_bar._dirty.text(), \
                '翻了一页就显示"已同步"——内存里那份其实还没导出'
        finally:
            win._ask_close_action = lambda state: 'discard'
            win.close()

    def test_walking_the_chapter_is_not_an_edit(self, app, chapter):
        """
        翻完一章再关窗**不许**问"你改了 X 拍"。

        翻页会顺带 `apply_current_beat()`（把表单提交到拍），而 apply 会把
        `At` 这类**投影列**规范化（'' → 'i11'）—— 只比字段快照的话，
        这里会冒出"改了 3 拍"。判据必须比**渲染出来的行**（产物真的变了吗），
        见 `_beat_edits_print`。
        """
        root, script = chapter
        win = _window(root, script)
        win.show()
        app.processEvents()
        try:
            for _ in range(len(win._session) - 1):
                win.next_beat()
            app.processEvents()
            state = win._close_dirty_state()
            assert state['beats'] is False, state
            assert state['form'] is False, state
        finally:
            win._ask_close_action = lambda state: 'discard'
            win.close()

    def test_saving_both_the_chapter_and_the_graph(self, app, chapter):
        """图与这一章都脏时，一个对话框里"保存"要两样都落盘。"""
        from storygraph.graph.models import Graph
        root, script = chapter
        win = _window(root, script)
        graph = Graph()
        graph.add_node('a', 0, 0)
        win._set_graph(graph)
        win.show()
        app.processEvents()
        win._show_index(0)
        win._panel.set_text('改过的台词')
        win.save_current_beat()

        state = win._close_dirty_state()
        assert state['beats'] is True and state['graph'] is True, state

        win._ask_close_action = lambda state: 'save'
        win.close()
        app.processEvents()

        assert not win.isVisible()
        assert '改过的台词' in script.read_text(encoding='utf-8'), '章节没写'
        assert (root / LAYOUT_REL).exists(), '图没写'

    def test_hidden_window_with_unsaved_beats_does_not_prompt(self, app,
                                                              chapter):
        """没显示过（测试 / 嵌入）不弹框：拍改动没有安全落点，放弃而不是挂住。"""
        root, script = chapter
        win = _window(root, script)
        win._show_index(0)
        win._panel.set_text('改过的台词')
        win.save_current_beat()
        assert win._session.has_unsaved_edits()

        win._ask_close_action = lambda state: (_ for _ in ()).throw(
            AssertionError('没显示过的窗口不该弹框'))
        win.close()
        app.processEvents()
        assert not win.isVisible()
        assert '改过的台词' not in script.read_text(encoding='utf-8')

    def test_clean_window_closes_without_prompt(self, app, chapter):
        root, script = chapter
        win = _window(root, script)
        win.show()
        app.processEvents()
        win._ask_close_action = lambda state: (_ for _ in ()).throw(
            AssertionError('什么都没改，不该问'))
        win.close()
        app.processEvents()
        assert not win.isVisible()


# ======================================================================
#  会话层：`unsaved_edits` 的计数（关窗那句"几拍"靠它）
# ======================================================================

class TestUnsavedEditsCounts:
    def test_insert_then_undo_is_clean_again(self, app, chapter):
        from storygraph.app.editor import EditorSession

        root, script = chapter
        session = EditorSession(project_root=root)
        session.load(script)
        assert session.unsaved_edits()['dirty'] is False

        session.insert_beat(1)
        info = session.unsaved_edits()
        assert info['added'] == 1 and info['changed'] == 0, info

        session.undo_structural()
        assert session.unsaved_edits()['dirty'] is False, \
            '插了又撤销，等于没改 —— 不该还报"未导出"'

    def test_delete_counts_as_removed(self, app, chapter):
        from storygraph.app.editor import EditorSession

        root, script = chapter
        session = EditorSession(project_root=root)
        session.load(script)
        session.delete_beat(1)
        info = session.unsaved_edits()
        assert info['removed'] == 1 and info['changed'] == 0, info
