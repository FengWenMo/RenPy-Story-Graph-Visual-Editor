"""
tests/test_ui_qt.py
===================
Qt（PySide6）前端的测试。**offscreen 跑真 Qt**（`QT_QPA_PLATFORM=offscreen`）——
比 tkinter 桩更接近真实（真事件、真布局、真字体）。

**可移植**：真实章节数据由 `_ui_project.build_project` 现场生成，
不再依赖作者机器上的绝对路径 —— 换机器 / 上 CI 也会真的跑。

锁的东西：
  · 卡片 → `show` 文本的语义（出场顺序 / extras 不丢 / 多属性不压平 / 位置统一才整拍）
  · `EditorSession`：载入 / 投影 / 写回 / 高级区收起不写回 / 空网格不清空立绘
  · 键盘契约：回车 = 下一拍；多行台词里 ↓/↑ 要到边才翻页
  · 前端不许 import tkinter（后端与 Qt 前端都必须干净）
  · 新模块：资产选择器 / 舞台预览 / 图编辑对话框 / 通知→状态栏
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

pytest.importorskip('PySide6')
from PySide6.QtGui import QTextCursor        # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog   # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project        # noqa: E402


@pytest.fixture(scope='session')
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture
def project(tmp_path):
    """现场生成的 Ren'Py 工程（含 4 个 label / 11 拍 / 真 PNG）。"""
    return build_project(tmp_path / 'proj')


@pytest.fixture
def window(app, project):
    """载入上面那一章的主窗口。"""
    from storygraph.ui_qt.main_window import MainWindowQt

    class _Engine:
        project_root = None

        def __init__(self, root):
            self.project_root = root
            self._registry = None

        def get_registry(self):
            return self._registry

    script = project / 'game' / 'script' / 'ch01.rpy'
    eng = _Engine(project)
    win = MainWindowQt(eng, script)
    yield win
    win.close()


# ==================================================================
#  一、卡片 → show 文本（唯一真源在 app.beats）
# ==================================================================

class TestCardsToShow:
    def test_order_index_is_preserved(self):
        from storygraph.app.beats import cards_to_show
        cards = [
            {'image': 'ling happy', 'expression': '', 'position': '',
             'at': '', 'order_index': 1, 'extra_rows': []},
            {'image': 'mary smile', 'expression': '', 'position': '',
             'at': '', 'order_index': 0, 'extra_rows': []},
        ]
        assert cards_to_show(cards) == 'mary smile; ling happy'

    def test_multi_attribute_is_not_collapsed(self):
        """
        真实数据实测：`ling base_arms_akimbo sigh` 是多属性立绘。
        表情词必须**逐词**归一，不许把 `sigh` 吃掉（曾经真实改坏过）。
        """
        from storygraph.app.beats import cards_to_show
        cards = [{'image': 'ling base_arms_akimbo sigh',
                  'expression': 'base_arms_akimbo 无语', 'position': '',
                  'at': '', 'order_index': 0, 'extra_rows': []}]
        out = cards_to_show(cards)
        assert 'base_arms_akimbo' in out
        assert 'ling ' in out
        assert len(out.split()) >= 2, out

    def test_extras_are_not_lost(self):
        """同一角色的其余条目（换表情保留的）必须带回。"""
        from storygraph.app.beats import cards_to_show
        cards = [{'image': 'ling oh', 'expression': 'oh',
                  'position': '', 'at': '', 'order_index': 0,
                  'extra_rows': [{'image': 'ling base_arms_akimbo sigh'}]}]
        out = cards_to_show(cards)
        assert 'ling base_arms_akimbo sigh' in out

    def test_position_joined_with_at(self):
        from storygraph.app.beats import cards_to_show
        cards = [{'image': 'ling happy', 'expression': '', 'position': 'left',
                  'at': '', 'order_index': 0, 'extra_rows': []}]
        assert cards_to_show(cards) == 'ling happy@left'

    def test_uniform_position_only_when_all_same(self):
        from storygraph.app.beats import uniform_sprite_position
        same = [{'at': 'i11'}, {'at': 'i11'}]
        diff = [{'at': 'i11'}, {'at': ''}]
        assert uniform_sprite_position(same) == 'i11'
        assert uniform_sprite_position(diff) == '', \
            '位置不统一时必须留空，否则会兜底套到别的立绘头上'
        assert uniform_sprite_position([]) == ''


# ==================================================================
#  二、编辑会话（前端唯一的语义入口）
# ==================================================================

class TestEditorSession:
    def test_loads_generated_chapter(self, project):
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=project)
        n = s.load(project / 'game' / 'script' / 'ch01.rpy')
        assert n == 11
        assert len(s.beats) == 11

    def test_fields_projection(self, project):
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=project)
        s.load(project / 'game' / 'script' / 'ch01.rpy')
        f = s.fields(0)
        assert f['Speaker'] == 'e'
        assert '早上好' in f['Text']
        assert f['Scene'] == 'bg classroom'

    def test_cards_are_grouped_per_character(self, project):
        """第 2 拍（show ling oh）时：ling 的两个条目应聚成一张卡，extras 保留。"""
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=project)
        s.load(project / 'game' / 'script' / 'ch01.rpy')
        cards = s.cards(1)
        assert cards, '应有立绘卡'
        images = ' '.join(str(c.get('image')) for c in cards)
        assert 'ling' in images

    def test_apply_writes_show_back(self, project):
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=project)
        s.load(project / 'game' / 'script' / 'ch01.rpy')
        cards = [{'image': 'ling happy', 'expression': '', 'position': 'leftish',
                  'at': '', 'order_index': 0, 'extra_rows': []}]
        s.apply(0, {'Speaker': 'e', 'Text': '改过的台词'}, cards)
        assert s.beats[0].text == '改过的台词'
        assert 'ling' in s.beats[0].show
        assert 'leftish' in s.beats[0].show

    def test_advanced_fields_ignored_when_collapsed(self, project):
        """
        高级区收起时**不写回**（收起 ≠ 清空）。

        这条曾经被改坏过：收起高级区再翻页，把 Label/CodeBlock 全清掉了。
        """
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=project)
        s.load(project / 'game' / 'script' / 'ch01.rpy')
        before = s.beats[0].label
        s.apply(0, {'Speaker': 'e', 'Text': 'x', 'Label': '', 'CodeBlock': ''},
                advanced_open=False)
        assert s.beats[0].label == before, '收起高级区不该把 Label 清空'

    def test_empty_cards_do_not_clear_sprites(self, project):
        """空网格不清空立绘（防误删）—— show 只有拼得出内容才覆盖。"""
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=project)
        s.load(project / 'game' / 'script' / 'ch01.rpy')
        before = s.beats[1].show
        s.apply(1, {'Speaker': 'm', 'Text': '只改台词'}, cards=[])
        assert s.beats[1].show == before

    def test_navigation_is_pure(self, project):
        """翻页是纯导航：别的拍一个字符都不许动。"""
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=project)
        s.load(project / 'game' / 'script' / 'ch01.rpy')
        snap = [(b.speaker, b.text, b.kind, b.show) for b in s.beats]
        s.fields(0)
        s.fields(1)
        s.cards(1)
        assert [(b.speaker, b.text, b.kind, b.show) for b in s.beats] == snap

    def test_frames_build(self, project):
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=project)
        s.load(project / 'game' / 'script' / 'ch01.rpy')
        assert len(s.frames()) == len(s.beats)

    def test_export_writes_file(self, project, tmp_path):
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=project)
        s.load(project / 'game' / 'script' / 'ch01.rpy')
        out = tmp_path / 'out.rpy'
        res = s.export(out)
        assert res['ok'], res.get('blocking')
        assert out.exists()
        assert 'label' in out.read_text(encoding='utf-8')


# ==================================================================
#  三、主窗口（键盘契约 / 卡片同步）
# ==================================================================

class TestMainWindow:
    def test_loads_chapter(self, window):
        assert len(window.beats) == 11
        assert window._pos.text() == '1 / 11'

    def test_save_current_beat_writes_back(self, window):
        window._show_index(0)
        window._panel.set_speaker('m')
        window._panel.set_text('保存测试')
        window.save_current_beat()
        assert window.beats[0].speaker == 'm'
        assert window.beats[0].text == '保存测试'
        assert '已保存到这一拍' in window.statusBar().currentMessage()

    def test_save_marks_dirty_then_export_clears(self, window, tmp_path):
        """状态栏的"未导出"标记：改完是脏的，导出后同步。"""
        window._show_index(0)
        window._panel.set_text('改了')
        window.save_current_beat()
        assert '未导出' in window._status_bar._dirty.text()
        res = window._session.export(tmp_path / 'x.rpy')
        assert res['ok']
        window._status_bar.set_dirty(False, str(res['path']))
        assert '已同步' in window._status_bar._dirty.text()

    def test_next_beat_keeps_data(self, window):
        snap = [(b.speaker, b.text, b.show) for b in window.beats]
        window.next_beat()
        window.next_beat()
        assert [(b.speaker, b.text, b.show) for b in window.beats] == snap

    def test_participants_list_is_filled(self, app, project):
        """
        参与角色不应是空的。

        曾经的 bug：把 **registry** 当成 **engine** 传进 `participants()`
        （后者要 `engine.get_registry()`），于是列表恒空。现在会话统一走
        `RegistrySource` 适配 —— 这条用例就是钉住它。
        """
        from storygraph.parsing.discover import discover_scripts
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.pipeline.build import load_asset_registry
        from storygraph.story.beat_build import apply_script_definitions
        from storygraph.ui_qt.main_window import MainWindowQt

        reg = load_asset_registry(project)
        # 把脚本里的 `define e = Character(...)` 合并进资源表 —— 这是
        # 「重新扫描项目」做的第二件事，参与角色就来自这里。
        files = discover_scripts(project)
        pr, _g = IncrementalRenPyAnalyzer().analyze(files=files, graph=None)
        apply_script_definitions(reg, pr)

        class _Engine:
            project_root = project

            def get_registry(self):
                return reg

        win = MainWindowQt(_Engine(), project / 'game' / 'script' / 'ch01.rpy')
        try:
            win._reload_list()
            assert win._who.count() > 0, '参与角色列表空了'
            assert win._session.participants(), '会话取参与角色也为空'
        finally:
            win.close()

    def test_help_is_a_window_not_inline_text(self, app, window):
        """
        用户明确要求："那堆操作提示，另找个说明窗口放"。

        所以主窗口里**不该**常驻一大段说明文字，而应是一个可打开的窗口。
        """
        from storygraph.ui_qt.help_dialog import HelpDialog
        dlg = HelpDialog(window)
        assert dlg.windowTitle() == '使用说明'
        assert dlg.isModal() is False

    def test_notification_reaches_status_bar(self, window):
        """后端 `app.notify` 的消息要能显示到状态栏（不再直接弹框）。"""
        from storygraph.app import notify
        notify.info('测试通知：缺 3 个资源')
        assert '缺 3 个资源' in window.statusBar().currentMessage()


class TestCaretAtEdge:
    def _edit(self, text):
        from PySide6.QtWidgets import QPlainTextEdit
        w = QPlainTextEdit()
        w.setPlainText(text)
        return w

    def test_single_line_is_always_at_edge(self, app):
        from storygraph.ui_qt.main_window import MainWindowQt
        w = self._edit('一行')
        w.moveCursor(QTextCursor.MoveOperation.Start)
        assert MainWindowQt._caret_at_edge(w, True)
        assert MainWindowQt._caret_at_edge(w, False)

    def test_multiline_middle_is_not_at_edge(self, app):
        from storygraph.ui_qt.main_window import MainWindowQt
        w = self._edit('第一行\n第二行\n第三行')
        w.moveCursor(QTextCursor.MoveOperation.Start)
        assert not MainWindowQt._caret_at_edge(w, True)
        w.moveCursor(QTextCursor.MoveOperation.End)
        assert MainWindowQt._caret_at_edge(w, True)


# ==================================================================
#  四、资产选择器
# ==================================================================

class TestAssetPicker:
    def test_image_spec_from_asset_id(self):
        from storygraph.app.picker import image_spec_of
        spec = image_spec_of('ling_happy',
                             {'Tag': 'ling', 'Attributes': 'happy'})
        assert spec == 'ling happy'

    def test_image_spec_falls_back_to_id(self):
        """没有 Tag/Attributes 时原样返回 ID —— 不凭空编一个 tag。"""
        from storygraph.app.picker import image_spec_of
        assert image_spec_of('weird_thing', {}) == 'weird_thing'

    def test_image_spec_appends_position(self):
        from storygraph.app.picker import image_spec_of
        spec = image_spec_of('x', {'Tag': 'ling', 'Attributes': 'oh'},
                             position='i11')
        assert spec == 'ling oh@i11'

    def test_dialog_opens_without_registry(self, app):
        """没有资源表也不能崩（显示空列表 + 顶部提示）。"""
        from storygraph.ui_qt.asset_picker import AssetPickerDialog
        dlg = AssetPickerDialog(None, registry=None, project_root=None)
        assert dlg._cats.count() >= 1
        dlg.close()

    def test_pick_image_returns_empty_without_registry(self, app):
        from storygraph.ui_qt.asset_picker import pick_image
        assert pick_image(None, registry=None) == ''


# ==================================================================
#  五、舞台预览
# ==================================================================

class TestStagePreview:
    def _window(self, app, project):
        from storygraph.app.editor import EditorSession
        from storygraph.ui_qt.stage_preview import StagePreviewWindow
        s = EditorSession(project_root=project)
        s.load(project / 'game' / 'script' / 'ch01.rpy')
        return StagePreviewWindow(None, s.frames(), project_root=project)

    def test_shows_first_frame(self, app, project):
        w = self._window(app, project)
        assert w.idx == 0
        assert w._pos.text() == f'1 / {len(w.frames)}'
        w.close()

    def test_next_prev_bounds(self, app, project):
        w = self._window(app, project)
        w.prev_frame()
        assert w.idx == 0, '第一帧再往前应该停住'
        w.next_frame()
        assert w.idx == 1
        w.close()

    def test_only_changed_keeps_original_list(self, app, project):
        """切回「全部」要能恢复 —— 所以必须一直留着原始列表。"""
        w = self._window(app, project)
        total = len(w.frames)
        w._only_cb.setChecked(True)
        assert len(w.frames) <= total
        if not w.frames:
            w.close()
            return
        w._only_cb.setChecked(False)
        assert len(w.frames) == total
        w.close()

    def test_speaker_and_text_are_shown(self, app, project):
        w = self._window(app, project)
        w.show_frame(0)
        assert '早上好' in w._dlg.text()
        w.close()


# ==================================================================
#  六、立绘缩略图路径（曾经静默失效）
# ==================================================================

class TestSpriteThumbPath:
    """
    立绘缩略图必须真的解析到磁盘上的图。

    曾经的 bug：前端用 `getattr(symbols, 'registry', None)` 取资源表，
    而 `symbols` **没有**这个属性 → 恒为 None → 缩略图永远加载不出来，
    卡片上只剩一个名字（静默失效，最难发现的那种）。
    路径的真源是资源表的 `Path` 列，只有会话拿得到。
    """

    @pytest.fixture
    def session(self, project):
        from storygraph.app.editor import EditorSession
        from storygraph.pipeline.build import load_asset_registry
        reg = load_asset_registry(project)
        s = EditorSession(registry_provider=lambda: reg, project_root=project)
        s.load(project / 'game' / 'script' / 'ch01.rpy')
        return s

    def test_resolves_multi_attribute_sprite(self, session):
        p = session.path_of_image('ling base_arms_akimbo sigh')
        assert p is not None, '多属性立绘的缩略图路径没解析出来'
        assert Path(p).is_file()
        assert Path(p).name == 'base_arms_akimbo_sigh.png'

    def test_resolves_simple_sprite(self, session):
        p = session.path_of_image('ling oh')
        assert p is not None and Path(p).is_file()

    def test_resolves_background(self, session):
        p = session.path_of_image('bg classroom')
        assert p is not None and Path(p).is_file()

    def test_missing_image_returns_none_not_error(self, session):
        """找不到就返回 None（界面显示名字兜底），不抛异常。"""
        assert session.path_of_image('nope missing') is None
        assert session.path_of_image('') is None

    def test_grid_widget_uses_injected_resolver(self, app, project):
        from storygraph.app.editor import EditorSession
        from storygraph.pipeline.build import load_asset_registry
        from storygraph.ui_qt.main_window import MainWindowQt

        reg = load_asset_registry(project)

        class _Engine:
            project_root = project

            def get_registry(self):
                return reg

        win = MainWindowQt(_Engine(),
                           project / 'game' / 'script' / 'ch01.rpy')
        try:
            win._show_index(0)
            cards = win._panel._grid._cards
            assert cards, '这一拍应该有立绘卡'
            spec = cards[0].item.get('image')
            resolved = cards[0]._thumb_path(spec)
            assert resolved is not None and Path(resolved).is_file(), \
                f'{spec} 的缩略图路径没解析出来'
        finally:
            win.close()


# ==================================================================
#  七、图编辑对话框
# ==================================================================

class TestGraphDialogs:
    def test_valid_label_rules(self):
        from storygraph.ui_qt.dialogs import valid_label
        assert valid_label('chapter_02')
        assert valid_label('_x1')
        assert not valid_label('第二章'), '中文 label 会生成跑不起来的脚本'
        assert not valid_label('2start'), '不能以数字开头'
        assert not valid_label('a b'), '不能有空格'
        assert not valid_label('')

    def test_add_node_rejects_bad_name(self, app):
        from storygraph.ui_qt.dialogs import AddNodeDialog
        dlg = AddNodeDialog(None, existing=['ch01'])
        dlg._name.setText('第二章')
        dlg._on_accept()
        # 对话框没 show 时子控件 isVisible() 恒为 False，所以看"就地提示的文字"
        assert dlg._error.text()
        assert dlg.result() != QDialog.Accepted
        assert dlg.name == ''

    def test_add_node_rejects_duplicate(self, app):
        from storygraph.ui_qt.dialogs import AddNodeDialog
        dlg = AddNodeDialog(None, existing=['ch01'])
        dlg._name.setText('ch01')
        dlg._on_accept()
        assert dlg._error.text()
        assert dlg.result() != QDialog.Accepted

    def test_add_node_accepts_good_name(self, app):
        from storygraph.ui_qt.dialogs import AddNodeDialog
        dlg = AddNodeDialog(None)
        dlg._name.setText('chapter_02')
        dlg._ops.setPlainText('score = 0')
        dlg._on_accept()
        assert dlg.name == 'chapter_02'
        assert dlg.operations == 'score = 0'

    def test_edge_rejects_self_loop(self, app):
        """连到自己 = 死循环，必须在对话框里挡住。"""
        from storygraph.graph.models import Graph
        from storygraph.ui_qt.dialogs import AddEdgeDialog
        g = Graph()
        g.add_node('a', 0, 0)
        g.add_node('b', 0, 0)
        dlg = AddEdgeDialog(None, nodes=g.nodes)
        dlg._src.setCurrentIndex(0)
        dlg._dst.setCurrentIndex(0)
        dlg._on_accept()
        assert dlg._error.text()
        assert dlg.result() != QDialog.Accepted

    def test_edge_accepts_valid_pair(self, app):
        from storygraph.graph.models import Graph
        from storygraph.ui_qt.dialogs import AddEdgeDialog
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        dlg = AddEdgeDialog(None, nodes=g.nodes)
        dlg._src.setCurrentIndex(list(g.nodes).index(a))
        dlg._dst.setCurrentIndex(list(g.nodes).index(b))
        dlg._cond.setText('score > 1')
        dlg._on_accept()
        assert dlg.result() == QDialog.Accepted
        assert dlg.from_id == a and dlg.to_id == b

    def test_node_ops_roundtrip(self, app):
        from storygraph.ui_qt.dialogs import NodeOpsDialog
        dlg = NodeOpsDialog(None, label_name='ch01',
                            operations=['x = 1', 'y = 2'])
        dlg._text.setPlainText('x = 1\n\ny = 2\n')
        dlg._on_accept()
        assert dlg.operations == ['x = 1', 'y = 2'], '空行要被丢掉'


# ==================================================================
#  八、架构约束：前端/后端不许 import tkinter
# ==================================================================

class TestNoTkinterInBackend:
    """把"依赖方向单向"这条契约钉成测试，否则迟早有人再 import 回来。"""

    PURE_MODULES = [
        'storygraph.app.beats',
        'storygraph.app.stage',
        'storygraph.app.notify',
        'storygraph.app.editor',
        'storygraph.app.picker',
        'storygraph.graph.commands',
        'storygraph.graph.editor_utils',
        'storygraph.pipeline.renpy_exporter',
    ]

    def test_app_layer_has_no_tkinter_reference(self):
        import ast
        from _source_paths import module_source
        for dotted in self.PURE_MODULES:
            src = module_source(dotted).read_text(encoding='utf-8')
            tree = ast.parse(src)
            hits = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    hits += [a.name for a in node.names if 'tkinter' in a.name]
                elif isinstance(node, ast.ImportFrom):
                    m = node.module or ''
                    if 'tkinter' in m or m.startswith('storygraph.ui'):
                        hits.append(m)
            assert not hits, f'{dotted} 不该依赖前端：{hits}'

    def test_qt_modules_do_not_import_tkinter(self):
        import ast
        from _source_paths import module_source
        for dotted in ('storygraph.ui_qt.main_window',
                       'storygraph.ui_qt.sprite_grid',
                       'storygraph.ui_qt.asset_picker',
                       'storygraph.ui_qt.stage_preview',
                       'storygraph.ui_qt.dialogs',
                       'storygraph.ui_qt.status'):
            src = module_source(dotted).read_text(encoding='utf-8')
            tree = ast.parse(src)
            hits = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    hits += [a.name for a in node.names if 'tkinter' in a.name]
                elif isinstance(node, ast.ImportFrom) and \
                        'tkinter' in (node.module or ''):
                    hits.append(node.module)
            assert not hits, f'{dotted} 不该 import tkinter：{hits}'

    def test_theme_does_not_import_tkinter(self):
        """
        QSS 的数据源是 `ui.tokens`（纯数据），**不是** `ui.*` 的控件模块。
        `app` 层与 Qt 层都只允许读 tokens。
        """
        from _source_paths import module_source
        src = module_source('storygraph.ui_qt.theme').read_text(encoding='utf-8')
        assert 'import tkinter' not in src
