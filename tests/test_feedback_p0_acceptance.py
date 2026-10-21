"""
tests/test_feedback_p0_acceptance.py
====================================
上一阶段（2026-10-22 P0/P1）的**验收测试**：用户实际会走的那几步，
落点是不是真的对。

`test_feedback_p0_label_source.py` 已经钉住了几条核心路径；这一份补的是
当时只靠手工点、没有自动判据的边角：

  · 预览不仅接收 `start_index`，主窗口**真的把当前拍传进去**；
  · label 下拉 / 面包屑 / 图上高亮三者跟着同一个 label 走；
  · 无源四态在右栏按钮与右键菜单上都给出正确入口；
  · 源文件丢失能在原路径重建，也能重新定位到别的文件；
  · 在工具内追加 label 时，原有字节不动、重名/非法名不写盘；
  · 新建章节走主窗口时，节点真的拿到 `file_path`；
  · 点号 label 在创建、发现、引用扫描三处都认。

不测截图，只测数据落点（`_loaded_idx` / `focus_label` / `file_path` /
磁盘字节 / 按钮文字与可用性）。
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
pytest.importorskip('PySide6')

from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog,  # noqa: E402
                               QInputDialog, QMessageBox)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project  # noqa: E402


@pytest.fixture(scope='session')
def app():
    a = QApplication.instance() or QApplication([])
    yield a


class _Engine:
    def __init__(self, root):
        self.project_root = root
        self._registry = None

    def get_registry(self):
        return self._registry


@pytest.fixture
def project(tmp_path):
    return build_project(tmp_path / 'proj')


@pytest.fixture
def window(app, project):
    from storygraph.ui_qt.main_window import MainWindowQt
    script = project / 'game' / 'script' / 'ch01.rpy'
    win = MainWindowQt(_Engine(project), script)
    yield win
    win.close()


def _node(window, label):
    return next(n for n in window.graph.nodes.values()
                if n.label_name == label)


def _graph_with(window, *nodes):
    """`nodes` 是 `(label, file_path)`；返回 `{label: node_id}`。"""
    from storygraph.graph.models import Graph
    g = Graph()
    ids = {label: g.add_node(label, 0, 0, file_path=fp)
           for label, fp in nodes}
    window._set_graph(g)
    return ids


# ======================================================================
#  一、预览：主窗口传的是当前拍
# ======================================================================

class TestPreviewUsesCurrentBeat:
    def test_open_preview_receives_the_loaded_index(self, window, monkeypatch):
        from storygraph.ui_qt import stage_preview
        seen = {}

        def fake_open(parent, frames, **kwargs):
            seen['parent'] = parent
            seen['start_index'] = kwargs.get('start_index')
            seen['frames'] = len(frames)
            return None

        monkeypatch.setattr(stage_preview, 'open_preview', fake_open)
        window._show_index(5)

        window.open_stage_preview()

        assert seen['parent'] is window
        assert seen['start_index'] == 5
        assert seen['frames'] == len(window.beats)

    def test_preview_follows_next_beat_navigation(self, window, monkeypatch):
        from storygraph.ui_qt import stage_preview
        seen = {}

        def fake_open(parent, frames, **kwargs):
            seen['start_index'] = kwargs.get('start_index')

        monkeypatch.setattr(stage_preview, 'open_preview', fake_open)
        window._show_index(2)
        window.next_beat()

        window.open_stage_preview()

        assert seen['start_index'] == 3


# ======================================================================
#  二、同一个 .rpy 里的多个 label
# ======================================================================

class TestLabelNavigation:
    def test_combo_lists_every_label_in_file_order(self, window):
        labels = [window._label_combo.itemText(i)
                  for i in range(window._label_combo.count())]
        assert labels == ['ch01', 'ch01_roof', 'ch01_class', 'ch01_end']
        assert window._label_combo.isEnabled()

    def test_loading_a_label_updates_combo_and_crumb(self, window, project):
        script = project / 'game' / 'script' / 'ch01.rpy'
        window.load_script(script, goto_card=True, label='ch01_roof')

        assert window._session.focus_label == 'ch01_roof'
        assert window._loaded_idx == window._session.index_of_label('ch01_roof')
        assert window._label_combo.currentText() == 'ch01_roof'
        assert window._crumb.text().endswith('ch01.rpy › ch01_roof')

    def test_switching_label_in_combo_lands_on_its_first_beat(self, window):
        idx = window._label_combo.findText('ch01_class')
        assert idx >= 0

        window._on_label_changed(idx)

        assert window._session.focus_label == 'ch01_class'
        assert window._loaded_idx == window._session.index_of_label(
            'ch01_class')

    def test_unknown_label_falls_back_to_first_beat_and_first_label(self,
                                                                    window,
                                                                    project):
        script = project / 'game' / 'script' / 'ch01.rpy'
        window.load_script(script, goto_card=True, label='not_a_label')

        assert window._loaded_idx == 0
        assert window._session.focus_label == 'ch01'
        assert window._label_combo.currentText() == 'ch01'

    def test_single_label_file_disables_the_combo(self, window, project):
        one = project / 'game' / 'script' / 'one.rpy'
        one.write_text('label only:\n    return\n', encoding='utf-8')

        window.load_script(one, goto_card=True)

        assert window._label_combo.count() == 1
        assert window._label_combo.currentText() == 'only'
        assert not window._label_combo.isEnabled()

    def test_graph_highlight_follows_the_focused_label(self, window, project):
        script = project / 'game' / 'script' / 'ch01.rpy'
        ids = _graph_with(window, ('ch01', str(script)),
                          ('ch01_roof', str(script)))

        window.load_script(script, goto_card=True, label='ch01_roof')

        assert getattr(window.graph.nodes[ids['ch01_roof']], 'is_current',
                       False)
        assert not getattr(window.graph.nodes[ids['ch01']], 'is_current', True)
        assert window._canvas.selected_node_id() == ids['ch01_roof']

        window._on_label_changed(window._label_combo.findText('ch01'))

        assert getattr(window.graph.nodes[ids['ch01']], 'is_current', False)
        assert not getattr(window.graph.nodes[ids['ch01_roof']], 'is_current',
                           True)
        assert window._canvas.selected_node_id() == ids['ch01']


# ======================================================================
#  三、无源节点的四态与修复入口
# ======================================================================

class TestSourceStates:
    def test_bound_node_offers_open_script(self, window, project):
        script = project / 'game' / 'script' / 'ch01.rpy'
        ids = _graph_with(window, ('ch01', str(script)))
        window._canvas.select_node(ids['ch01'], center=False)

        assert window._source_info(window.graph.nodes[ids['ch01']])['kind'] \
            == 'bound'
        assert '打开本章剧本' in window._open_script_btn.text()
        assert window._open_script_btn.isEnabled()

    def test_missing_file_offers_repair_not_open(self, window, project):
        missing = project / 'game' / 'gone.rpy'
        ids = _graph_with(window, ('gone', str(missing)))
        window._canvas.select_node(ids['gone'], center=False)

        assert window._source_info(window.graph.nodes[ids['gone']])['kind'] \
            == 'missing'
        assert '源文件丢失' in window._chapter_label.text()
        assert '建/绑源文件' in window._open_script_btn.text()
        assert window._open_script_btn.isEnabled()

    def test_missing_file_can_be_recreated_at_the_same_path(self, window,
                                                             project,
                                                             monkeypatch):
        missing = project / 'game' / 'gone.rpy'
        ids = _graph_with(window, ('gone', str(missing)))
        monkeypatch.setattr(window, '_repair_choice', lambda *a, **k: 'new')

        window._on_node_open(ids['gone'])

        assert missing.exists()
        assert missing.read_text(encoding='utf-8') == (
            'label gone:\n    return\n')
        node = _node(window, 'gone')
        assert node.file_path == str(missing)
        assert window._source_info(node)['kind'] == 'bound'
        assert window.current_page() == window.PAGE_CARD
        assert window._session.focus_label == 'gone'

    def test_missing_file_can_be_relocated(self, window, project, tmp_path,
                                           monkeypatch):
        missing = project / 'game' / 'gone.rpy'
        other = tmp_path / 'outside' / 'other.rpy'
        other.parent.mkdir(parents=True)
        other.write_text('label gone:\n    return\n', encoding='utf-8')
        ids = _graph_with(window, ('gone', str(missing)))
        monkeypatch.setattr(window, '_repair_choice', lambda *a, **k: 'locate')
        monkeypatch.setattr(
            QFileDialog, 'getOpenFileName',
            staticmethod(lambda *a, **k: (str(other), '')))

        window._on_node_open(ids['gone'])

        assert _node(window, 'gone').file_path == str(other)
        assert window.current_page() == window.PAGE_CARD
        assert window._session.focus_label == 'gone'

    def test_manual_node_can_bind_an_existing_file_outside_project(
            self, window, project, tmp_path, monkeypatch):
        outside = tmp_path / 'outside' / 'orphan.rpy'
        outside.parent.mkdir(parents=True)
        outside.write_text('label orphan:\n    return\n', encoding='utf-8')
        ids = _graph_with(window, ('orphan', None))
        monkeypatch.setattr(
            QFileDialog, 'getOpenFileName',
            staticmethod(lambda *a, **k: (str(outside), '')))

        window._locate_and_bind('orphan')

        assert _node(window, 'orphan').file_path == str(outside)
        assert window._session.focus_label == 'orphan'

    def test_binding_a_file_without_the_label_is_refused(self, window,
                                                         project, tmp_path,
                                                         monkeypatch):
        wrong = tmp_path / 'outside' / 'wrong.rpy'
        wrong.parent.mkdir(parents=True)
        wrong.write_text('label somebody_else:\n    return\n',
                         encoding='utf-8')
        ids = _graph_with(window, ('orphan', None))
        monkeypatch.setattr(
            QFileDialog, 'getOpenFileName',
            staticmethod(lambda *a, **k: (str(wrong), '')))

        window._locate_and_bind('orphan')

        assert _node(window, 'orphan').file_path is None
        assert '没有 label' in window._status_bar.currentMessage()

    def test_menu_offers_repair_and_disables_open_actions(self, window):
        ids = _graph_with(window, ('orphan', None))
        window._canvas.select_node(ids['orphan'], center=False)

        actions = {a.text(): a for a in
                   window.build_node_menu(ids['orphan']).actions()}

        assert '新建 / 绑定源文件…' in actions
        assert actions['新建 / 绑定源文件…'].isEnabled()
        for text in ('改本章台词（卡片）「orphan」', '在编辑器中打开'):
            assert text in actions
            assert not actions[text].isEnabled()

    def test_menu_offers_add_label_only_when_source_is_bound(self, window,
                                                             project):
        script = project / 'game' / 'script' / 'ch01.rpy'
        ids = _graph_with(window, ('ch01', str(script)))
        window._canvas.select_node(ids['ch01'], center=False)

        actions = [a.text() for a in
                   window.build_node_menu(ids['ch01']).actions()]

        assert '在本文件新建 label…' in actions
        assert '新建 / 绑定源文件…' not in actions

    def test_dotted_placeholder_is_found_in_the_project(self, window, project,
                                                        monkeypatch):
        main = project / 'game' / 'script' / 'main.rpy'
        main.write_text('label main.route:\n    return\n', encoding='utf-8')
        ids = _graph_with(window, ('main.route', None))

        info = window._source_info(window.graph.nodes[ids['main.route']])
        assert info['kind'] == 'found'
        assert Path(info['file']).name == 'main.rpy'

        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda *a, **k: QMessageBox.Yes))
        window._on_node_open(ids['main.route'])

        assert _node(window, 'main.route').file_path == str(main)
        assert window.current_page() == window.PAGE_CARD
        assert window._session.focus_label == 'main.route'


# ======================================================================
#  四、在工具内追加第二个 label
# ======================================================================

class TestAddLabel:
    def _bound_ch01(self, window, project):
        script = project / 'game' / 'script' / 'ch01.rpy'
        ids = _graph_with(window, ('ch01', str(script)))
        window._canvas.select_node(ids['ch01'], center=False)
        return script, ids['ch01']

    def test_appends_dotted_label_and_opens_it(self, window, project,
                                               monkeypatch):
        script, nid = self._bound_ch01(window, project)
        before = script.read_bytes()
        monkeypatch.setattr(
            QInputDialog, 'getText',
            staticmethod(lambda *a, **k: ('main.route', True)))

        window.add_label_to_file(nid)

        after = script.read_bytes()
        assert after.startswith(before.rstrip(b'\r\n'))
        assert b'label main.route:' in after
        node = _node(window, 'main.route')
        assert node.file_path == str(script)
        assert window.current_page() == window.PAGE_CARD
        assert window._session.focus_label == 'main.route'
        assert window._loaded_idx == window._session.index_of_label(
            'main.route')

    def test_invalid_label_does_not_touch_the_file(self, window, project,
                                                   monkeypatch):
        script, nid = self._bound_ch01(window, project)
        before = script.read_bytes()
        monkeypatch.setattr(
            QInputDialog, 'getText',
            staticmethod(lambda *a, **k: ('bad name', True)))

        window.add_label_to_file(nid)

        assert script.read_bytes() == before
        assert '不合法' in window._status_bar.currentMessage()

    def test_duplicate_graph_label_does_not_touch_the_file(self, window,
                                                           project,
                                                           monkeypatch):
        script, nid = self._bound_ch01(window, project)
        before = script.read_bytes()
        monkeypatch.setattr(
            QInputDialog, 'getText',
            staticmethod(lambda *a, **k: ('ch01', True)))

        window.add_label_to_file(nid)

        assert script.read_bytes() == before
        assert '图上已经有' in window._status_bar.currentMessage()

    def test_label_already_in_file_but_not_in_graph_is_refused(
            self, window, project, monkeypatch):
        script, nid = self._bound_ch01(window, project)
        before = script.read_bytes()
        monkeypatch.setattr(
            QInputDialog, 'getText',
            staticmethod(lambda *a, **k: ('ch01_roof', True)))

        window.add_label_to_file(nid)

        assert script.read_bytes() == before
        assert '已经有 label' in window._status_bar.currentMessage()

    def test_sourceless_node_goes_to_repair_instead_of_crashing(self, window,
                                                                monkeypatch):
        ids = _graph_with(window, ('orphan', None))
        window._canvas.select_node(ids['orphan'], center=False)
        called = []
        monkeypatch.setattr(window, '_repair_node_source',
                            lambda nid, info=None: called.append(nid))

        window.add_label_to_file(ids['orphan'])

        assert called == [ids['orphan']]

    def test_add_node_dialog_accepts_a_dotted_label(self, project, qapp):
        from storygraph.ui_qt.dialogs import AddNodeDialog
        dlg = AddNodeDialog(None, existing=(),
                            default_dir=str(project / 'game'))

        dlg._name.setText('main.route')
        dlg._on_accept()

        assert dlg.result() == QDialog.Accepted
        assert dlg.name == 'main.route'

    def test_add_node_writes_source_and_binds_the_new_node(
            self, window, project, monkeypatch):
        import storygraph.ui_qt.dialogs as dialogs

        target = project / 'game' / 'new_chapter.rpy'

        class _FakeDialog:
            name = 'new_chapter'
            operations = ''
            file_path = str(target)

            def __init__(self, *a, **k):
                pass

            def exec(self):
                return QDialog.Accepted

        monkeypatch.setattr(dialogs, 'AddNodeDialog', _FakeDialog)

        window.add_node(at=(10, 20))

        assert target.read_text(encoding='utf-8') == (
            'label new_chapter:\n    return\n')
        node = _node(window, 'new_chapter')
        assert node.file_path == str(target)

    def test_add_node_known_files_hides_deleted_scripts(self, window, project,
                                                        monkeypatch):
        import storygraph.ui_qt.dialogs as dialogs

        existing = project / 'game' / 'script' / 'ch01.rpy'
        gone = project / 'game' / 'gone.rpy'
        _graph_with(window, ('ch01', str(existing)), ('gone', str(gone)))
        captured = {}

        class _RejectDialog:
            def __init__(self, *a, **k):
                captured['known'] = list(k.get('known_files') or ())

            def exec(self):
                return QDialog.Rejected

        monkeypatch.setattr(dialogs, 'AddNodeDialog', _RejectDialog)

        window.add_node()

        assert captured['known'] == [str(existing)]


# ======================================================================
#  五、点号 label：创建 / 发现 / 引用扫描三处同源
# ======================================================================

class TestDottedLabels:
    @pytest.mark.parametrize('name,ok', [
        ('main.route', True),
        ('side.choice.ending', True),
        ('main.route2', True),
        ('_private.label', True),
        ('1main', False),
        ('main..route', False),
        ('.route', False),
        ('main.', False),
        ('main route', False),
        ('main-route', False),
    ])
    def test_valid_label_matrix(self, name, ok):
        from storygraph.ui_qt.dialogs import valid_label
        assert valid_label(name) is ok

    def test_has_label_matches_dotted_names_exactly(self):
        from storygraph.ui_qt.window_nodes import _has_label
        text = 'label main.route:\n    return\nlabel mainXroute:\n'
        assert _has_label(text, 'main.route')
        assert not _has_label(text, 'main')

    def test_discovery_finds_indented_dotted_declarations(self, tmp_path):
        from storygraph.parsing.discover import find_label_files
        game = tmp_path / 'game'
        game.mkdir()
        (game / 'nested.rpy').write_text(
            '    label main.route:\n        return\n', encoding='utf-8')

        found = find_label_files(tmp_path, ['main.route'])

        assert found['main.route'].name == 'nested.rpy'

    def test_reference_scan_finds_dotted_jump_and_call(self, tmp_path):
        from storygraph.parsing.discover import (
            find_files_referencing_labels)
        game = tmp_path / 'game'
        game.mkdir()
        (game / 'hub.rpy').write_text(
            'label hub:\n'
            '    call main.route from hub_call\n'
            '    jump side.choice.ending\n',
            encoding='utf-8')
        (game / 'unrelated.rpy').write_text(
            'label u:\n    jump mainXroute\n', encoding='utf-8')

        refs = find_files_referencing_labels(
            tmp_path, ['main.route', 'side.choice.ending'])

        assert [p.name for p in refs['main.route']] == ['hub.rpy']
        assert [p.name for p in refs['side.choice.ending']] == ['hub.rpy']
