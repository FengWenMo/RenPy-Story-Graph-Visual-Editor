"""
tests/test_feedback_p1_workbench_sync.py
========================================
2026-10-22 体验报告里 P1/P2 的离线判据（不靠截图）：

  · P1-2 变量工作台：变量清单 / `default` 初值 / 每条赋值落在第几拍；
  · P1-3 连线语义：手工连线 = 草图（数据层 `origin='manual'`，状态栏说清）；
  · P1-4 补全 / 刷新：执行前有预览，名字能预测行为；
  · P1-5 诊断：按代码 / 环境 / 资源分组、同类聚合、只报本次新增；
  · P2-2 新建章节的变量操作写进骨架文件；
  · P2-3 默认章节目录跟随项目已有章节；
  · P2-4 剧情图有未保存标记，关窗自动存一份布局。
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

from PySide6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project  # noqa: E402

VARS_SCRIPT = '''default love = 0
define flag = True

label vars_main:
    $ love += 1
    if love > 3:
        $ route = "roof"
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


@pytest.fixture
def project(tmp_path):
    root = build_project(tmp_path / 'proj')
    (root / 'game' / 'script' / 'vars.rpy').write_text(
        VARS_SCRIPT, encoding='utf-8')
    return root


# ======================================================================
#  P1-2 变量工作台
# ======================================================================

class TestVariableWorkbenchData:
    def test_lists_initial_values_and_beat_locations(self, project):
        from storygraph.app.editor import EditorSession
        from storygraph.graph.models import Graph

        script = project / 'game' / 'script' / 'vars.rpy'
        s = EditorSession(project_root=project)
        res = s.import_chapters([script], Graph())
        assert res['ok']
        node = next(n for n in res['graph'].nodes.values()
                    if n.label_name == 'vars_main')

        data = s.variable_workbench(res['graph'], node.id)

        assert data['ok']
        by_name = {v['name']: v for v in data['variables']}
        assert 'love' in by_name and by_name['love']['initial'] == '0'
        assert by_name['love']['declared_by'] == 'default'
        assert 'flag' in by_name, '只声明、没赋值的 define 也要列出来'
        assert by_name['flag']['initial'] == 'True'

        love_op = by_name['love']['operations'][0]
        assert love_op['text'] == 'love += 1'
        assert love_op['beat_index'] is not None
        assert love_op['beat_no'] >= 1

    def test_operations_stay_in_source_order(self, project):
        from storygraph.app.editor import EditorSession
        from storygraph.graph.models import Graph

        s = EditorSession(project_root=project)
        res = s.import_chapters(
            [project / 'game' / 'script' / 'vars.rpy'], Graph())
        node = next(n for n in res['graph'].nodes.values()
                    if n.label_name == 'vars_main')
        data = s.variable_workbench(res['graph'], node.id)
        assert data['operations'] == ['love += 1', 'route = "roof"']

    def test_edits_reach_the_beats_that_export_reads(self, project):
        from storygraph.app.editor import EditorSession
        from storygraph.graph.models import Graph

        s = EditorSession(project_root=project)
        res = s.import_chapters(
            [project / 'game' / 'script' / 'vars.rpy'], Graph())
        node = next(n for n in res['graph'].nodes.values()
                    if n.label_name == 'vars_main')

        out = s.apply_node_variable_edits(
            res['graph'], node.id, ['love += 5', 'route = "roof"'])

        assert out['ok'] and out['unplaced'] == 0
        codes = [b.code for b in s.beats if b.label == 'vars_main']
        assert any('love += 5' in c for c in codes), codes
        assert all('love += 1' not in c for c in codes)
        assert 'love += 5' in node.operations

    def test_workbench_saves_into_beats_from_the_window(self, app, project,
                                                       monkeypatch):
        """端到端：窗口打开工作台 → 改一条 → 拍上的代码真的变了。"""
        from storygraph.graph.models import Graph
        from storygraph.ui_qt import dialogs as D
        from storygraph.ui_qt.main_window import MainWindowQt

        script = project / 'game' / 'script' / 'vars.rpy'
        win = MainWindowQt(_Engine(project), script)
        try:
            res = win._session.import_chapters([script], Graph())
            win._set_graph(res['graph'])
            node = next(n for n in res['graph'].nodes.values()
                        if n.label_name == 'vars_main')
            seen = {}

            class _FakeWorkbench:
                def __init__(self, parent=None, label_name='', data=None,
                             on_check=None):
                    seen['data'] = data
                    self.variables = list(data['operations'])
                    self.variables[0] = 'love += 7'
                    self.jump_index = None
                    self.insert_beat = None

                def exec(self):
                    return D.QDialog.Accepted

            monkeypatch.setattr(D, 'VariableWorkbenchDialog', _FakeWorkbench)
            win.show_variable_workbench(node.id)

            assert seen['data']['variables'][0]['name'] == 'love'
            codes = [b.code for b in win._session.beats
                     if b.label == 'vars_main']
            assert any('love += 7' in c for c in codes), codes
        finally:
            win.close()

    def test_dialog_shows_table_and_can_jump_to_beat(self, app, project):
        from storygraph.app.editor import EditorSession
        from storygraph.graph.models import Graph
        from storygraph.ui_qt.dialogs import VariableWorkbenchDialog

        s = EditorSession(project_root=project)
        res = s.import_chapters(
            [project / 'game' / 'script' / 'vars.rpy'], Graph())
        node = next(n for n in res['graph'].nodes.values()
                    if n.label_name == 'vars_main')
        data = s.variable_workbench(res['graph'], node.id)

        dlg = VariableWorkbenchDialog(None, label_name='vars_main', data=data)
        assert dlg._table.rowCount() >= 3        # love / route / flag
        assert dlg._table.item(0, 2).text() != '—'

        dlg._ops.setCurrentRow(0)
        dlg._locate()
        assert dlg.jump_index == data['variables'][0]['operations'][0]['beat_index']


# ======================================================================
#  P1-5 诊断摘要
# ======================================================================

class TestDiagnosticsSummary:
    def test_groups_and_aggregates(self):
        from storygraph.core import safeguard
        from storygraph.ui_qt.diagnostics import (category_of, format_report,
                                                  summary_line)

        safeguard.clear()
        for _ in range(173):
            safeguard.swallow('thumb.loader.request', OSError('没图'))
        safeguard.swallow('sdk.find', FileNotFoundError('没 SDK'))
        safeguard.swallow('app.editor.apply', ValueError('真 bug'))
        items = safeguard.recent(100)

        assert category_of('thumb.loader.request') == 'resource'
        assert category_of('sdk.find') == 'environment'
        assert category_of('app.editor.apply') == 'code'
        text = summary_line(items)
        assert '1 个代码问题' in text
        assert '173 条资源未加载' in text
        report = format_report(items)
        assert 'thumb.loader.request ×173' in report
        assert report.count('thumb.loader.request') == 1, '同类只该出现一次'
        safeguard.clear()

    def test_report_since_only_reports_new_failures(self):
        from storygraph.core import safeguard

        safeguard.clear()
        safeguard.swallow('demo.one', ValueError('一'))
        mark = safeguard.snapshot()
        assert safeguard.report_since(mark) == ''

        safeguard.swallow('demo.two', ValueError('二'))
        text = safeguard.report_since(mark)
        assert 'demo.two' in text and 'demo.one' not in text
        safeguard.clear()


# ======================================================================
#  P2-2 / P2-3 新建章节
# ======================================================================

class TestNewChapterSkeleton:
    def test_variable_operations_are_written_into_the_skeleton(self, tmp_path):
        from storygraph.ui_qt.window_nodes import _write_new_chapter

        class _View:
            def _status(self, _text):
                pass

        target = tmp_path / 'game' / 'new.rpy'
        _write_new_chapter(_View(), target, 'new_ch', 'score = 0\n# 备注')
        text = target.read_text(encoding='utf-8')
        assert 'label new_ch:' in text
        assert '$ score = 0' in text
        assert '# 备注' in text
        assert text.rstrip().endswith('return')

    def test_append_keeps_existing_bytes_and_adds_operations(self, tmp_path):
        from storygraph.ui_qt.window_nodes import _write_new_chapter

        class _View:
            def _status(self, _text):
                pass

        target = tmp_path / 'game' / 'one.rpy'
        target.parent.mkdir(parents=True)
        target.write_text('label one:\n    return\n', encoding='utf-8')
        _write_new_chapter(_View(), target, 'two', 'flag = True')
        text = target.read_text(encoding='utf-8')
        assert text.startswith('label one:\n    return\n')
        assert 'label two:' in text and '$ flag = True' in text

    def test_default_dir_follows_existing_chapter_folder(self, project):
        from storygraph.ui_qt.window_nodes import _default_chapter_dir
        assert Path(_default_chapter_dir(project)) == project / 'game' / 'script'


# ======================================================================
#  P1-4 补全 / 刷新预览
# ======================================================================

class TestSyncPreview:
    def test_reload_shows_preview_before_importing(self, app, project,
                                                   monkeypatch):
        from storygraph.ui_qt.main_window import MainWindowQt
        from storygraph.ui_qt import dialogs as D

        script = project / 'game' / 'script' / 'ch01.rpy'
        win = MainWindowQt(_Engine(project), script)
        try:
            win.import_chapter_files([script])
            seen = {}

            class _FakeDialog:
                def __init__(self, parent=None, title='', intro='', detail='',
                             ok_text='', warn=''):
                    seen.update(title=title, intro=intro, detail=detail,
                                warn=warn)

                def exec(self):
                    return D.QDialog.Accepted

            called = []
            monkeypatch.setattr(D, 'SyncPreviewDialog', _FakeDialog)
            monkeypatch.setattr(win, 'import_chapter_files',
                                lambda paths: called.append(list(paths)))
            win.reload_imported_chapters()

            assert seen['title'] == '刷新已导入章节'
            assert called and called[0] == [str(script)]
        finally:
            win.close()


# ======================================================================
#  P1-3 / P2-4 图与布局
# ======================================================================

class TestGraphPersistence:
    def test_manual_edge_is_marked_as_sketch(self, app, project):
        from storygraph.graph.models import Graph
        from storygraph.ui_qt.main_window import MainWindowQt

        win = MainWindowQt(_Engine(project))
        try:
            g = Graph()
            a = g.add_node('a', 0, 0)
            b = g.add_node('b', 0, 0)
            e = g.add_edge(a, b, 'jump', origin='manual')
            win._set_graph(g)
            assert g.edges[e].origin == 'manual'
            assert '草图' in win._graph_hint.text()
        finally:
            win.close()

    def test_graph_dirty_flag_and_close_autosave(self, app, project):
        from storygraph.graph.models import Graph
        from storygraph.ui_qt.main_window import MainWindowQt

        win = MainWindowQt(_Engine(project))
        try:
            g = Graph()
            g.add_node('a', 0, 0)
            win._set_graph(g)
            assert win._graph_dirty is True
            win._mark_graph_saved(str(project / LAYOUT_REL))
            assert win._graph_dirty is False

            # 只是"高亮当前块"不算改动（is_current 不进指纹）
            win._canvas.highlight_node(1)
            win._set_graph(g)
            assert win._graph_dirty is False

            g.nodes[1].x = 99
            win._set_graph(g)
            assert win._graph_dirty is True
            win.close()
        finally:
            if not win.isHidden():
                win.close()
        assert (project / LAYOUT_REL).exists(), \
            '关窗时应该自动存一份布局，图不该关掉就没'
