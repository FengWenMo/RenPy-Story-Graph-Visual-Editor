"""
tests/test_feedback_p0_label_source.py
======================================
2026-10-22 用户实测的两条 P0：

  1. "点同一个 .rpy 里第二个 label 的节点，打开的永远是文件第一拍"；
  2. "新建节点显示无源文件……怎么才能把无源搞成有源，打开章节剧本不能用"。

这里不测界面截图，只看**数据落点**：

  · `EditorSession.index_of_label()` 能找到 label 的第一拍；
  · `_on_node_open()` 把 `label_name` 传下去，落在那个 label 的拍上；
  · 左栏 label 下拉与面包屑跟着当前 label；
  · 无源手工节点能新建源文件，变成 `BOUND` 并直接打开；
  · 占位块能在项目里找到声明它的文件，一键导入绑定；
  · 源文件丢失时不再"可点但报错"，而是给出修复入口。
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
pytest.importorskip('PySide6')

from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog,  # noqa: E402
                               QMessageBox)

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


# ======================================================================
#  一、label 定位
# ======================================================================

class TestOpenTheRightLabel:
    def test_index_of_label_finds_the_first_beat_of_each_label(self, window):
        labels = window._session.label_names()
        assert labels == ['ch01', 'ch01_roof', 'ch01_class', 'ch01_end'], labels
        seen = set()
        for name in labels:
            idx = window._session.index_of_label(name)
            assert idx >= 0
            assert window.beats[idx].label == name
            assert idx not in seen
            seen.add(idx)

    def test_missing_label_returns_minus_one(self, window):
        assert window._session.index_of_label('no_such_label') == -1

    def test_opening_second_label_node_lands_on_its_first_beat(
            self, window, project):
        """用户原话：第二个 label 建了，点了还是第一章。"""
        from storygraph.graph.models import Graph
        res = window._session.import_chapters(
            [project / 'game' / 'script' / 'ch01.rpy'], Graph())
        assert res['ok']
        window._set_graph(res['graph'])
        nid = _node(window, 'ch01_roof').id
        expected = window._session.index_of_label('ch01_roof')

        window._on_node_open(nid)

        assert window.current_page() == window.PAGE_CARD
        assert window._loaded_idx == expected
        assert window._session.focus_label == 'ch01_roof'
        assert 'ch01_roof' in window._crumb.text()
        assert window._label_combo.currentText() == 'ch01_roof'

    def test_label_combo_switches_back_to_the_first_label(self, window):
        script = window._file_path
        window.load_script(script, goto_card=True, label='ch01_roof')
        assert window._session.focus_label == 'ch01_roof'
        window._on_label_changed(window._label_combo.findText('ch01'))
        assert window._loaded_idx == 0
        assert window._session.focus_label == 'ch01'


# ======================================================================
#  二、无源节点修复
# ======================================================================

class TestRepairMissingSource:
    def test_manual_node_is_manual_and_its_button_offers_repair(
            self, window):
        from storygraph.graph.models import Graph
        g = Graph()
        nid = g.add_node('orphan', 0, 0)
        window._set_graph(g)
        window._canvas.select_node(nid, center=False)

        assert window._source_info(g.nodes[nid])['kind'] == 'manual'
        assert '手工节点' in window._chapter_label.text()
        assert '建/绑源文件' in window._open_script_btn.text()
        assert window._open_script_btn.isEnabled(), '修复入口不该被禁用'

    def test_manual_node_can_create_a_source_file_and_open_it(
            self, window, project, monkeypatch):
        from storygraph.graph.models import Graph
        g = Graph()
        nid = g.add_node('orphan', 0, 0)
        window._set_graph(g)
        target = project / 'game' / 'script' / 'orphan.rpy'

        # 用户点主按钮 → 选择"新建 .rpy" → 在保存对话框确认默认路径
        monkeypatch.setattr(window, '_repair_choice',
                            lambda *a, **k: 'new')
        monkeypatch.setattr(
            QFileDialog, 'getSaveFileName',
            staticmethod(lambda *a, **k: (str(target), '')))

        window._on_node_open(nid)

        assert target.exists(), '没有写出源文件'
        assert target.read_text(encoding='utf-8') == (
            'label orphan:\n    return\n')
        node = _node(window, 'orphan')
        assert node.file_path and Path(node.file_path).exists()
        assert window._source_info(node)['kind'] == 'bound'
        assert window.current_page() == window.PAGE_CARD
        assert window._session.focus_label == 'orphan'
        assert window._loaded_idx == window._session.index_of_label('orphan')

    def test_placeholder_is_found_in_the_project_and_bound(
            self, window, project, monkeypatch):
        """别的章节 jump 到一个还没导入的 label：占位块应能一键导入。"""
        from storygraph.graph.models import Graph
        g = Graph()
        nid = g.add_node('ch01_roof', 0, 0)       # 没有 file_path
        window._set_graph(g)
        info = window._source_info(g.nodes[nid])
        assert info['kind'] == 'found'
        assert info['file'].endswith('ch01.rpy')

        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda *a, **k: QMessageBox.Yes))
        window._on_node_open(nid)

        node = _node(window, 'ch01_roof')
        assert node.file_path and Path(node.file_path).exists()
        assert window.current_page() == window.PAGE_CARD
        assert window._session.focus_label == 'ch01_roof'

    def test_missing_file_is_reported_as_missing_not_as_manual(
            self, window, tmp_path):
        from storygraph.graph.models import Graph
        g = Graph()
        nid = g.add_node('gone', 0, 0, file_path=str(tmp_path / 'gone.rpy'))
        window._set_graph(g)
        info = window._source_info(g.nodes[nid])
        assert info['kind'] == 'missing'
        window._canvas.select_node(nid, center=False)
        assert '源文件丢失' in window._chapter_label.text()
        assert window._open_script_btn.isEnabled()


# ======================================================================
#  三、点号 label：解析器认，创建/补全也要认
# ======================================================================

class TestDottedLabels:
    def test_valid_label_accepts_dotted_segments(self):
        from storygraph.ui_qt.dialogs import valid_label
        assert valid_label('main.route')
        assert valid_label('side.choice.ending')
        assert not valid_label('main..route')
        assert not valid_label('.route')
        assert not valid_label('main.')

    def test_discovery_finds_dotted_declarations_and_references(self, tmp_path):
        from storygraph.parsing.discover import (
            find_files_referencing_labels, find_label_files)
        game = tmp_path / 'game'
        game.mkdir()
        (game / 'main.rpy').write_text(
            'label main.route:\n    "route"\n    return\n',
            encoding='utf-8')
        (game / 'hub.rpy').write_text(
            'label hub:\n    call main.route\n    jump main.route\n',
            encoding='utf-8')

        assert find_label_files(tmp_path, ['main.route'])['main.route'].name \
            == 'main.rpy'
        refs = find_files_referencing_labels(tmp_path, ['main.route'])
        assert [p.name for p in refs['main.route']] == ['hub.rpy']


# ======================================================================
#  四、在工具内给已有 .rpy 新建第二个 label
# ======================================================================

class TestNewLabelInExistingFile:
    def test_add_node_dialog_can_pick_an_imported_file(
            self, tmp_path, qapp):
        from storygraph.ui_qt.dialogs import AddNodeDialog
        target = tmp_path / 'game' / 'ch.rpy'
        target.parent.mkdir(parents=True)
        target.write_text('label a:\n    return\n', encoding='utf-8')

        dlg = AddNodeDialog(None, existing=('a',),
                            default_dir=str(tmp_path / 'game'),
                            known_files=[target])
        dlg._known.setCurrentIndex(1)          # 选「ch.rpy」
        assert dlg._file.text() == str(target)
        dlg._name.setText('b')
        dlg._on_accept()

        assert dlg.result() == QDialog.Accepted
        assert dlg.file_path == str(target)

    def test_context_action_adds_a_second_label_and_opens_it(
            self, window, project, monkeypatch):
        from PySide6.QtWidgets import QInputDialog
        from storygraph.graph.models import Graph
        script = project / 'game' / 'script' / 'ch01.rpy'
        g = Graph()
        nid = g.add_node('ch01', 0, 0, file_path=str(script))
        window._set_graph(g)
        window._canvas.select_node(nid, center=False)

        labels = [a.text() for a in window.build_node_menu(nid).actions()]
        assert any('在本文件新建 label' in t for t in labels), labels

        monkeypatch.setattr(
            QInputDialog, 'getText',
            staticmethod(lambda *a, **k: ('ch01_extra', True)))
        window.add_label_to_file(nid)

        text = script.read_text(encoding='utf-8')
        assert 'label ch01_extra:' in text
        node = _node(window, 'ch01_extra')
        assert Path(node.file_path) == script
        assert window.current_page() == window.PAGE_CARD
        assert window._session.focus_label == 'ch01_extra'
        assert window._loaded_idx == window._session.index_of_label(
            'ch01_extra')


