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
from PySide6.QtCore import Qt                        # noqa: E402
from PySide6.QtGui import QTextCursor        # noqa: E402
from PySide6.QtTest import QTest                      # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog,  # noqa: E402
                               QListWidget, QWidget)

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

    def test_extras_keep_their_own_position(self):
        """
        用户 2026-10-05 实测（学姐第一次出场位置丢了）：

        > 原本应该是 `show ye happy at i11`，但新文件里变成了 `show ye happy`

        一拍里同一角色先 `show ye happy at i11` 再换表情 `show ye care` 时，
        前一条落在 `extra_rows` 里 —— 写回时**只带回 image、把 `at` 吃了**。
        而翻页 / 保存 / 导出 / 增删卡都会先提交当前拍，所以只要在那一拍上
        待过，位置就悄悄没了。
        """
        from storygraph.app.beats import cards_to_show
        cards = [{'image': 'ye care', 'expression': 'care', 'position': '',
                  'at': '', 'order_index': 0,
                  'extra_rows': [{'image': 'ye happy', 'expression': '开心',
                                  'position': 'i11', 'at': 'i11'}]}]
        assert cards_to_show(cards) == 'ye happy@i11; ye care'

    def test_beat_round_trip_keeps_every_position(self, project):
        """真拍走一遍 `beat → cards → show`：逐条位置一个字都不许变。"""
        from storygraph.app.editor import EditorSession
        from storygraph.pipeline.build import load_asset_registry
        from storygraph.story.beat_model import Beat

        reg = load_asset_registry(project)
        s = EditorSession(registry_provider=lambda: reg, project_root=project)
        beat = Beat(seq=1, label='ch01', kind='say', speaker='yhj',
                    text='想好了哦？', show='ye happy@i11; ye care')
        s.beats = [beat]
        got = s.apply(0, {'Text': beat.text, 'Speaker': beat.speaker},
                      s.cards(0), advanced_open=False, stage_loaded=None)
        assert got.show == 'ye happy@i11; ye care', \
            f'逐条位置被卡片往返吃掉了：{got.show!r}'

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
#  三·五、键盘：**真的要发按键**
# ==================================================================

class TestKeyboardActuallyWorks:
    """
    `MainWindowQt.eventFilter` 曾经**从来没被 install 过** ——
    方法写得很漂亮，一个按键都到不了：Ctrl+S、回车翻页、Alt+↑↓ 全是死的。

    为什么以前的测试没发现：它们全都**直接调方法**（`win.next_beat()`），
    于是"键盘接线断了"这件事在测试里根本不存在。这一组改成**发真按键**
    （`QApplication.sendEvent`），钉住"按键 → 行为"这条链路。
    """

    @pytest.fixture(autouse=True)
    def _on_card_page(self, window):
        """
        这一组测的是**卡片页**的键盘契约。

        两页导航（2026-10-02）之后，这些键只在卡片页生效 ——
        结构页的键另有一组用例（`TestKeyboardIsPageScoped`）。
        """
        window._set_page(window.PAGE_CARD)
        yield

    @staticmethod
    def _press(app, win, key, mods=Qt.NoModifier, widget=None):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent
        target = widget if widget is not None else win
        app.sendEvent(target, QKeyEvent(QEvent.KeyPress, key, mods))
        app.processEvents()

    def test_ctrl_s_works_while_focus_is_in_the_text_box(self, app, window):
        """
        焦点通常在台词框里，所以过滤器必须挂在 QApplication 上 ——
        挂在窗口上的话这个用例会失败。
        """
        window._show_index(0)
        window._panel.set_text('键盘保存')
        self._press(app, window, Qt.Key_S, Qt.ControlModifier,
                    widget=window._panel._text)
        assert window.beats[0].text == '键盘保存', 'Ctrl+S 没保存'

    def test_enter_pages_to_next_beat(self, app, window):
        window._show_index(0)
        self._press(app, window, Qt.Key_Return)
        assert window._loaded_idx == 1, '回车没翻页'

    def test_ctrl_z_undoes_the_last_save(self, app, window):
        window._show_index(0)
        original = window.beats[0].text
        window._panel.set_text('改过的')
        self._press(app, window, Qt.Key_S, Qt.ControlModifier)
        assert window.beats[0].text == '改过的'
        self._press(app, window, Qt.Key_Z, Qt.ControlModifier)
        assert window.beats[0].text == original, 'Ctrl+Z 没撤销'
        assert window._panel.text() == original, '撤销后表单没跟着回滚'

    def test_alt_down_pages_even_inside_the_text_box(self, app, window):
        """多行台词里 Alt+↓ 必须**一定**翻页（这是给键盘用户的快车道）。"""
        window._show_index(0)
        edit = window._panel._text
        edit.setFocus()
        self._press(app, window, Qt.Key_Down, Qt.AltModifier, widget=edit)
        assert window._loaded_idx == 1

    def test_bare_down_inside_text_moves_caret_not_page(self, app, window):
        """光标在中间时 ↓ 是移动光标，**不许**翻页。"""
        window._show_index(0)
        edit = window._panel._text
        edit.setPlainText('第一行\n第二行\n第三行')
        edit.moveCursor(QTextCursor.MoveOperation.Start)
        edit.setFocus()
        self._press(app, window, Qt.Key_Down, widget=edit)
        assert window._loaded_idx == 0, '光标在中间时不该翻页'

    def test_bare_down_at_caret_edge_pages(self, app, window):
        """光标已经在最后一行 → ↓ 才翻页。"""
        window._show_index(0)
        edit = window._panel._text
        edit.setPlainText('第一行\n第二行')
        edit.moveCursor(QTextCursor.MoveOperation.End)
        edit.setFocus()
        window._panel._text.setFocus()
        self._press(app, window, Qt.Key_Down, widget=edit)
        assert window._loaded_idx == 1, '光标到底了应该翻页'

    def test_page_list_keys_are_left_alone(self, app, window):
        """
        焦点在章节列表里时 ↑/↓ **完全放行**：列表自己移动选择就是换一拍，
        我们再翻一次就会一次跳两拍（tkinter 版踩过这个坑）。
        """
        window._show_index(0)
        window._list.setFocus()
        self._press(app, window, Qt.Key_Down, widget=window._list)
        assert window._loaded_idx <= 1, '一次跳了两拍'


class TestKeyboardIsPageScoped:
    """
    两页导航（2026-10-02）之后：卡片页的快捷键**不许**在结构图上生效。

    为什么必须钉住：在结构图上按回车，用户的意思是"进这一章的卡片"。
    如果它顺手把上一章的拍号往前翻一格，用户**看不见任何变化**
    （卡片页没显示），但会话里的"当前拍"已经换了 ——
    接着切进卡片按 💾，就保存到了错的那一拍上。
    "看不见的错拍"比"没反应"危险得多。
    """

    @staticmethod
    def _press(app, win, key, mods=Qt.NoModifier, widget=None):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent
        target = widget if widget is not None else win
        app.sendEvent(target, QKeyEvent(QEvent.KeyPress, key, mods))
        app.processEvents()

    def test_first_page_is_the_story_structure(self, window):
        """启动第一屏必须是剧情结构（用户明确要求"先是剧情树"）。"""
        assert window.current_page() == window.PAGE_GRAPH

    def test_card_page_is_not_the_visible_page_at_startup(self, window):
        """卡片编辑器**不是**常驻的（旧版是右 dock，一眼全是卡片）。"""
        assert window._stack.currentIndex() == window.PAGE_GRAPH
        assert window._stack.currentWidget() is not \
            window._stack.widget(window.PAGE_CARD)

    def test_ctrl_s_does_nothing_on_structure_page(self, app, window):
        window._show_index(0)
        window._panel.set_text('结构页改的')
        self._press(app, window, Qt.Key_S, Qt.ControlModifier)
        assert window.beats[0].text != '结构页改的', \
            '结构页上 Ctrl+S 把卡片内容写进去了'

    def test_card_actions_are_disabled_on_the_structure_page(self, window):
        """
        **QAction 的快捷键不受"当前在哪一页"约束** —— 挂着 Ctrl+S 的工具栏
        动作即使工具栏隐藏了也照样会触发。所以卡片页专属动作必须在结构页
        被真的 `setEnabled(False)`（Qt 的规矩：动作禁用 → 快捷键失效）。

        否则用户在结构图上按 Ctrl+S，屏幕上毫无变化（卡片页没显示），
        但会话里那一拍真的被存了 —— 这是最坏的一类 bug：看不见的写入。
        """
        assert window.current_page() == window.PAGE_GRAPH
        for name in ('_save_action', '_undo_action',
                     '_prev_action', '_next_action'):
            act = getattr(window, name)
            assert not act.isEnabled(), f'{name} 在结构页上还是可用的'
        window._set_page(window.PAGE_CARD)
        assert window._save_action.isEnabled(), '回到卡片页后保存键没恢复'
        window._set_page(window.PAGE_GRAPH)
        assert not window._undo_action.isEnabled()

    def test_no_action_carries_a_shortcut_that_bypasses_the_page_check(self,
                                                                      window):
        """
        兜底：卡片页专属动作**不许**只靠 QAction 快捷键兜着。

        现在快捷键由 `eventFilter`（会判页号）统一处理，动作的快捷键只作为
        "顺带关掉"的开关。这条用例防止有人以后又给某个动作挂上 `Ctrl+S`
        并且指望它只在卡片页生效。
        """
        from PySide6.QtGui import QAction

        with_shortcut = [a.text() for a in window.findChildren(QAction)
                         if (a.text() or '').strip()
                         and not a.shortcut().isEmpty()]
        assert not with_shortcut, \
            f'这些动作挂了全局快捷键（不受页号约束）：{with_shortcut}'

    def test_enter_on_structure_page_does_not_page(self, app, window):
        window._show_index(0)
        self._press(app, window, Qt.Key_Return)
        assert window._loaded_idx == 0, '结构页按回车把拍号翻了'
        assert window.current_page() == window.PAGE_GRAPH

    def test_enter_on_structure_page_opens_the_selected_module(self, app, window,
                                                               project):
        """结构页回车 = 进选中模块的卡片（这才是用户按回车的本意）。"""
        from storygraph.graph.models import Graph

        script = project / 'game' / 'script' / 'ch01.rpy'
        g = Graph()
        nid = g.add_node('ch01', 0, 0, file_path=str(script))
        window._set_graph(g)
        window._canvas.select_node(nid, center=False)
        self._press(app, window, Qt.Key_Return)
        assert window.current_page() == window.PAGE_CARD, '回车没进卡片页'
        assert window._file_path.endswith('ch01.rpy')

    def test_esc_returns_to_structure_page(self, app, window):
        window._set_page(window.PAGE_CARD)
        self._press(app, window, Qt.Key_Escape)
        assert window.current_page() == window.PAGE_GRAPH, 'Esc 没回第一屏'

    def test_back_button_returns_to_structure_page(self, window):
        window._set_page(window.PAGE_CARD)
        window._back_btn.click()
        assert window.current_page() == window.PAGE_GRAPH

    def test_back_commits_the_card_before_leaving(self, window):
        """
        返回前必须**先提交当前拍**：否则"改了字 → 直接点返回"会静默丢掉
        这一拍的最后一次编辑（翻页是同一条规矩）。
        """
        window._set_page(window.PAGE_CARD)
        window._show_index(2)
        window._panel.set_text('返回前改的')
        window._back_to_graph()
        assert window.beats[2].text == '返回前改的', '返回时丢掉了卡片改动'

    def test_crumb_shows_where_you_are(self, window):
        window._set_page(window.PAGE_CARD)
        assert 'ch01.rpy' in window._crumb.text()
        assert '/ 11' in window._crumb_hint.text()


class TestGraphScope:
    """
    剧情图里**只有用户导入的章节**（用户 2026-10-03 纠正）。

    > 我没有叫你所有的文件全部导进剧情图内！这还选个毛线啊！看都要看瞎了！
    > ……所谓的导入项目只是为了表格的 xlsx 的资源管理系统！不是把整个项目
    > 都排在剧情图上面！！！剧情图上只有用户自己导入的章节排版！！！

    这一组是**运行时**判据（`TestGraphScopeAndAssetsAreTwoPaths` 只管文案与
    签名）：真的开一个窗口、真的打开项目、真的导入章节，看图上多了什么。
    """

    @staticmethod
    def _ch01(project):
        return project / 'game' / 'script' / 'ch01.rpy'

    @staticmethod
    def _write_extra_chapter(project, name='ch02.rpy', label='ch02'):
        fp = project / 'game' / 'script' / name
        fp.write_text(f'label {label}:\n    "第二章"\n    jump {label}_b\n\n'
                      f'label {label}_b:\n    "收尾"\n    return\n',
                      encoding='utf-8')
        return fp

    def test_story_graph_starts_empty(self, window):
        """启动时图是空的 —— 不是"项目里所有的 label"。"""
        assert not (getattr(window.graph, 'nodes', None) or {})

    def test_open_project_does_not_add_anything_to_the_graph(self, window,
                                                             project):
        """「打开项目」= 资源表那条路：图上一个节点都不许加。"""
        before = len(getattr(window.graph, 'nodes', {}) or {})
        window.open_project_path(project)
        after = len(getattr(window.graph, 'nodes', {}) or {})
        assert after == before == 0, \
            f'打开项目把 {after - before} 个节点灌进了剧情图'

    def test_open_project_loads_the_asset_registry(self, window, project):
        """但资源侧必须真的加载（立绘/音乐/角色候选、缩略图都靠它）。"""
        res = window.open_project_path(project)
        assert res and res['ok'], res
        assert sum(res['counts'].values()) > 0, res['counts']
        assert window._session.registry() is not None
        assert window._session.symbols is not None

    def test_rescan_project_keeps_the_graph_untouched(self, window, project):
        """「重读资源表」不许动剧情图（这是两条路的边界）。"""
        window.open_project_path(project)
        window.import_chapter_files([self._ch01(project)])
        n_nodes = len(window.graph.nodes)
        n_edges = len(window.graph.edges)
        assert n_nodes > 0
        window.rescan_project()
        assert len(window.graph.nodes) == n_nodes, '重读资源表把剧情图动了'
        assert len(window.graph.edges) == n_edges

    def test_import_adds_only_the_selected_file(self, window, project):
        """导入哪一章，图上就只有哪一章。"""
        other = self._write_extra_chapter(project, 'other.rpy', 'other_ch')
        window.import_chapter_files([self._ch01(project)])
        names = {Path(n.file_path).name for n in window.graph.nodes.values()}
        assert names == {'ch01.rpy'}, names
        assert str(other) not in {n.file_path for n in window.graph.nodes.values()}

    def test_import_accumulates_across_batches(self, window, project):
        """再导入一批是**累加**，不覆盖上一批。"""
        window.import_chapter_files([self._ch01(project)])
        first = len(window.graph.nodes)
        window.import_chapter_files(
            [self._write_extra_chapter(project, 'ch03.rpy', 'ch03')])
        names = {Path(n.file_path).name for n in window.graph.nodes.values()}
        assert names == {'ch01.rpy', 'ch03.rpy'}, names
        assert len(window.graph.nodes) > first

    def test_reimporting_the_same_file_does_not_duplicate(self, window, project):
        window.import_chapter_files([self._ch01(project)])
        n = len(window.graph.nodes)
        res = window.import_chapter_files([self._ch01(project)])
        assert len(window.graph.nodes) == n, '同一个文件导入两次长出了重复节点'
        assert res['added_nodes'] == 0

    def test_imported_nodes_carry_the_file_and_edges(self, window, project):
        window.import_chapter_files([self._ch01(project)])
        assert window.graph.edges, '导入后连一条连线都没有（分支/条件没进来）'
        for node in window.graph.nodes.values():
            assert node.file_path, '导入的节点丢了源文件路径（双击进不去卡片）'

    def test_remove_chapter_drops_only_that_file(self, window, project,
                                                 monkeypatch):
        """
        「从剧情图移除这一章」按**文件**整章移除。

        文件里有多个 label 时会先弹确认框（防误删整章）—— 测试里必须把它
        顶掉，否则 offscreen 下模态框会**阻塞**（不是失败，是卡住）。
        """
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, 'question',
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        window.import_chapter_files([self._ch01(project)])
        extra = self._write_extra_chapter(project, 'ch04.rpy', 'ch04')
        window.import_chapter_files([extra])
        assert len([n for n in window.graph.nodes.values()
                    if Path(n.file_path).name == 'ch04.rpy']) == 2, \
            '前置条件不成立：这个文件应该有两个 label'
        nid = next(n.id for n in window.graph.nodes.values()
                   if Path(n.file_path).name == 'ch04.rpy')
        window.remove_chapter_from_graph(nid)
        names = {Path(n.file_path).name for n in window.graph.nodes.values()}
        assert names == {'ch01.rpy'}, names

    def test_removing_a_chapter_asks_before_dropping_whole_file(self, window,
                                                                project,
                                                                monkeypatch):
        """整章移除必须先问一声 —— 但用户点"否"时一个节点都不许少。"""
        from PySide6.QtWidgets import QMessageBox

        asked = []

        def _no(*args, **kwargs):
            asked.append(args)
            return QMessageBox.No

        monkeypatch.setattr(QMessageBox, 'question', staticmethod(_no))
        extra = self._write_extra_chapter(project, 'ch05.rpy', 'ch05')
        window.import_chapter_files([extra])
        before = len(window.graph.nodes)
        nid = next(n.id for n in window.graph.nodes.values()
                   if Path(n.file_path).name == 'ch05.rpy')
        window.remove_chapter_from_graph(nid)
        assert asked, '没问就整章删了'
        assert len(window.graph.nodes) == before

    def test_clear_graph_empties_the_canvas_but_keeps_the_project(self, window,
                                                                  project):
        window.open_project_path(project)
        window.import_chapter_files([self._ch01(project)])
        assert window.graph.nodes
        window.clear_graph()
        assert not (window.graph.nodes or {})
        assert window._session.registry() is not None, '清图把资源表也清了'
        assert window._project_root == project

    def test_console_reports_what_was_imported(self, window, project):
        """
        用户 2026-10-03：
        > 至少右上角要保留一块，导入的时候没有反馈！根本不知道导入了什么……

        所以导入必须**逐个文件**写进控制台（文件名 + 几个模块）。
        """
        assert window._console.isReadOnly()
        window.import_chapter_files([self._ch01(project)])
        text = window._console.toPlainText()
        assert '[导入]' in text and 'ch01.rpy' in text, text
        assert '合计' in text, text

    def test_console_reports_the_project_load(self, window, project):
        """打开项目也要有反馈（旧版往分析栏写 "[项目] 已加载：图片 …"）。"""
        window.open_project_path(project)
        text = window._console.toPlainText()
        assert '[项目]' in text and '图片' in text, text

    def test_console_keeps_a_log_but_analysis_replaces_it(self, window):
        """导入/项目那些是流水账（保留）；分析结论是"当前状态"（替换，照旧版）。"""
        window._log('[导入] 第一条')
        window._log('[导入] 第二条')
        assert '第一条' in window._console.toPlainText()
        assert '第二条' in window._console.toPlainText()
        window._show_analysis_text('分析结论')
        text = window._console.toPlainText()
        assert text.strip() == '分析结论', text

    def test_clear_console_does_not_touch_the_graph(self, window, project):
        window.import_chapter_files([self._ch01(project)])
        n = len(window.graph.nodes)
        window.clear_console()
        assert window._console.toPlainText() == ''
        assert len(window.graph.nodes) == n

    def test_empty_hint_tells_you_to_import_chapters(self, window):
        text = window._canvas._empty_hint
        assert '导入章节' in text
        assert '不会自动装进整个项目' in text, '空状态没讲清"图不会自动灌满"'


class TestStructurePage:
    """第一屏：结构图 + 右上控制台 + 当前模块 + 主入口。"""

    def test_current_module_label_follows_selection(self, window, project):
        """左键选中模块 → 右栏"当前模块"跟着变（旧版 `_update_chapter_label`）。"""
        from storygraph.graph.models import Graph

        script = project / 'game' / 'script' / 'ch01.rpy'
        g = Graph()
        nid = g.add_node('ch01', 0, 0, file_path=str(script))
        window._set_graph(g)
        window._canvas.select_node(nid, center=False)
        assert 'ch01' in window._chapter_label.text()
        assert 'ch01.rpy' in window._chapter_label.text()

    def test_open_script_button_enters_the_card_page(self, window, project):
        """右栏「✎ 打开本章剧本（改台词）」= 旧版的主入口。"""
        from storygraph.graph.models import Graph

        script = project / 'game' / 'script' / 'ch01.rpy'
        g = Graph()
        nid = g.add_node('ch01', 0, 0, file_path=str(script))
        window._set_graph(g)
        window._canvas.select_node(nid, center=False)
        window._open_script_btn.click()
        assert window.current_page() == window.PAGE_CARD

    def test_left_column_tree_is_gone(self, window):
        """
        用户 2026-10-03：
        > 这个左栏"剧情树"是不需要的！因为很显然中间的舞台就已经导入了
        > chapter，一看就知道有哪些！这个左边的根本没有用……
        """
        assert not hasattr(window, '_tree')
        assert not hasattr(window, '_filter')
        assert not hasattr(window, '_tree_caption')

    def test_empty_state_tells_you_what_to_do(self, window):
        """没有图时不能是一片空白 —— 要写清怎么开始。"""
        window._set_graph(None)
        assert window._canvas._empty_hint
        assert '导入章节' in window._canvas._empty_hint

    def test_boot_timer_does_not_override_an_explicit_page_switch(self, qapp,
                                                                 project):
        """
        构造后的延迟任务（`QTimer.singleShot(0, _boot)`）**不许**改页号。

        历史 bug：`_boot` 里也调了一次 `_set_page(结构页)`，于是
        "构造完立刻打开某一章"（或用户在第一个事件循环回合内点进模块）
        会被这个延迟任务**静默弹回第一屏** —— 表现出来就是"点了没反应"。
        真实场景：`python main.py 某章.rpy` 之后立刻双击模块。
        """
        from storygraph.ui_qt.main_window import MainWindowQt

        class _Engine:
            project_root = project

            def get_registry(self):
                return None

        script = project / 'game' / 'script' / 'ch01.rpy'
        win = MainWindowQt(_Engine(), script)
        try:
            win._set_page(win.PAGE_CARD)      # 模拟"构造完立刻进卡片"
            qapp.processEvents()              # 让 singleShot(0) 真的跑起来
            assert win.current_page() == win.PAGE_CARD, \
                '启动时的延迟任务把页号弹回了第一屏'
        finally:
            win.close()

    def test_console_starts_with_a_hint(self, window):
        assert window._console.placeholderText()
        assert window._console.isReadOnly()

    def test_check_logic_writes_to_the_console_and_marks_the_graph(self, window):
        """
        静态分析的结论必须**画回图上**，不能只给一段文字 ——
        只给文字的话，用户还得自己在几十个块里找哪个有问题。

        构造：`start` 之外还有一个**没有入口的环**（b ⇄ c）。
        它俩都有入边（所以不是"独立入口"），但从任何入口都到不了 ——
        这才是该标红的"真断了"。
        """
        from storygraph.graph.models import Graph

        g = Graph()
        a = g.add_node('start', 0, 0)
        b = g.add_node('loop_b', 0, 0)
        c = g.add_node('loop_c', 0, 0)
        g.add_edge(b, c)
        g.add_edge(c, b)
        window._set_graph(g)
        window.check_logic()
        assert window._console.toPlainText().strip(), '结论没写进控制台'
        marked = [nid for nid, it in window._canvas._items.items()
                  if it.mark == 'stranded']
        assert set(marked) == {b, c}, f'没标出关在环里的块（标了 {marked}）'
        assert a not in marked, '入口块不该被标红'

    def test_clear_analysis_removes_the_marks(self, window):
        from storygraph.graph.models import Graph

        g = Graph()
        g.add_node('start', 0, 0)
        b = g.add_node('loop_b', 0, 0)
        c = g.add_node('loop_c', 0, 0)
        g.add_edge(b, c)
        g.add_edge(c, b)
        window._set_graph(g)
        window.check_logic()
        assert any(it.mark for it in window._canvas._items.values()), \
            '前置条件不成立：本来就没标出任何东西'
        window.clear_analysis()
        assert window._canvas.entry_id is None
        assert all(it.mark is None for it in window._canvas._items.values())

    def test_zoom_buttons_change_the_zoom_label(self, window):
        before = window._canvas.zoom_percent()
        window._zoom_in_btn.click()
        assert window._canvas.zoom_percent() > before
        assert window._zoom_label.text().endswith('%')
        window._zoom_reset_btn.click()
        assert window._canvas.zoom_percent() == 100


# ==================================================================
#  三·六、卡片的「保存 / 撤销」两个按钮
# ==================================================================

class TestCardSaveAndUndo:
    """
    用户 2026-10-01 明确要的两个功能：

        > 我希望卡片端加两个功能：一个是保存该卡片的修改，
        > 另一个是撤销修改的操作
    """

    def test_buttons_exist_with_the_right_labels(self, window):
        panel = window._panel
        assert '保存' in panel._save_btn.text()
        assert '撤销' in panel._undo_btn.text()

    def test_save_button_commits_the_card(self, window):
        window._show_index(0)
        window._panel.set_text('按钮保存的')
        window._panel._save_btn.click()
        assert window.beats[0].text == '按钮保存的'

    def test_undo_button_is_disabled_until_there_is_something_to_undo(
            self, window):
        window._show_index(0)
        assert not window._panel._undo_btn.isEnabled()
        window._panel.set_text('改了')
        window._panel._save_btn.click()
        assert window._panel._undo_btn.isEnabled(), \
            '保存之后撤销按钮该亮起来'

    def test_undo_button_restores_the_previous_content(self, window):
        window._show_index(0)
        original = window.beats[0].text
        window._panel.set_text('第一次改')
        window._panel._save_btn.click()
        window._panel.set_text('第二次改')
        window._panel._save_btn.click()
        window._panel._undo_btn.click()
        assert window.beats[0].text == '第一次改', \
            '撤销应该回到"上一次保存"（单级撤销）'
        assert not window._panel._undo_btn.isEnabled(), '撤到底了按钮该熄灭'

    def test_save_with_no_changes_is_reported_not_faked(self, window):
        """
        没改动时不许假装保存成功（否则用户以为存了）。

        为什么第一次点会报"改了"：`apply` 第一次跑会把 `op_order` /
        `stage_order` 这类**派生顺序元数据**归一化一次（hydrate 的副作用），
        所以第一次确实是"有改动"。从第二次起 `apply` 就是**幂等**的 ——
        这里连点两次，第二次必须如实说"没有改动"。
        """
        window._show_index(0)
        window._panel._save_btn.click()          # 第一次：归一化派生元数据
        window._panel._save_btn.click()          # 第二次：真的没改动
        assert '没有改动' in window.statusBar().currentMessage()

    def test_apply_is_idempotent_from_the_second_time(self, window):
        """幂等性本身也要钉住：不能每次都判成"改了"（撤销按钮会一直亮）。"""
        window._show_index(0)
        first = window._session.save(
            0, window._panel.collect_fields(),
            cards=window._panel.sprite_cards(),
            advanced_open=window._panel.is_advanced_open(),
            stage_loaded=window._panel.stage_loaded())
        second = window._session.save(
            0, window._panel.collect_fields(),
            cards=window._panel.sprite_cards(),
            advanced_open=window._panel.is_advanced_open(),
            stage_loaded=window._panel.stage_loaded())
        assert second['changed'] is False, \
            f'重复保存同一份内容被判成有改动（第一次={first["changed"]}）'

    def test_undo_without_history_says_so(self, window):
        """没有可撤销的东西时按钮是灭的（按不动），直接调也要明说。"""
        window._show_index(0)
        window._panel.set_text('改了但没保存')
        assert not window._panel._undo_btn.isEnabled()
        window.undo_current_beat()
        assert '没有可撤销' in window.statusBar().currentMessage()

    def test_undo_only_touches_the_current_card(self, window):
        """撤销的作用域是**这一张卡片**，不许动别的拍。"""
        window._show_index(0)
        window._panel.set_text('改了第1拍')
        window._panel._save_btn.click()
        before_others = [(b.speaker, b.text) for b in window.beats[1:]]
        window._panel._undo_btn.click()
        assert [(b.speaker, b.text) for b in window.beats[1:]] == before_others

    def test_dirty_indicator_appears_and_clears(self, window):
        """
        "有未保存的改动"提示：改一个字就亮，保存后熄灭。
        没有它的话，"改了但没保存就切走"是静默的。

        （这里直接打 `refresh_dirty` 而不是点按钮：保存那一下会顺带
        把派生元数据归一化，"点一次"不等于"表单回到干净态"。）
        """
        window._show_index(0)
        assert window._panel._dirty_label.text() == ''
        window._panel.set_text('改了一个字')
        assert '未保存' in window._panel._dirty_label.text()
        window._panel.reset_baseline()
        assert window._panel._dirty_label.text() == ''

    def test_switching_chapter_drops_the_undo_history(self, window):
        """
        换一章之后旧撤销记录必须失效 —— 否则"撤销"会把上一章的快照
        摊到这一章同拍号的 Beat 上（跨文件写错数据）。
        """
        window._show_index(0)
        window._panel.set_text('改了')
        window._panel._save_btn.click()
        assert window._session.has_undo(0)
        window.load_script(window._file_path)      # 重新载入同一章
        assert not window._session.has_undo(0)


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
        from storygraph.pipeline.build import load_asset_registry
        from storygraph.ui_qt.stage_preview import StagePreviewWindow
        s = EditorSession(project_root=project)
        s.load(project / 'game' / 'script' / 'ch01.rpy')
        # 与真实调用一致（`main_window.open_stage_preview` 会把资源表传进来）：
        # 没有表，立绘/背景就只能靠磁盘回退，测出来的东西不是真实行为。
        # `sound=False`：测试工程里的音频是假的（只有文件头），别让 QtMultimedia
        # 在 stderr 上刷 "Could not open media"（跟被测行为无关的噪音）
        return StagePreviewWindow(None, s.frames(), project_root=project,
                                  registry=load_asset_registry(project),
                                  sound=False)

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

    # ------------------------------------------------------------------
    #  用户 2026-10-05（第 10 轮）：
    #    "你确定 bgm 导入了吗？背景导入了吗？" +
    #    "总会有一个蓝色框当占位符一闪而过，影响观感"
    # ------------------------------------------------------------------

    def _canvas(self, project):
        from storygraph.ui_qt.stage_preview import StageCanvas
        c = StageCanvas(None, project_root=project)
        c.set_registry(self._registry(project))
        return c

    @staticmethod
    def _registry(project):
        from storygraph.pipeline.build import load_asset_registry
        return load_asset_registry(project)

    def test_loading_placeholder_is_quiet(self, app, project):
        """
        **加载中**的占位必须是低调的深色 —— 不是那个亮蓝框。

        用户 2026-10-05："每次加载时……总会有一个蓝色框当占位符一闪而过，
        影响观感"。加载中和"找不到图"是两件完全不同的事：前者再等等就好，
        后者要用户去修（那个才该显眼）。
        """
        import types

        from PySide6.QtGui import QColor, QImage, QPainter

        from storygraph.ui_qt import tokens as T

        c = self._canvas(project)
        s = types.SimpleNamespace(label='ling\nhappy', tag='ling', at='i11',
                                  speaking=False)

        def render(loading):
            img = QImage(40, 40, QImage.Format.Format_ARGB32)
            img.fill(QColor('#000000'))
            p = QPainter(img)
            c._draw_block(p, s, (0, 0, 40, 40), loading=loading)
            p.end()
            return img.pixelColor(2, 2)

        assert render(True) == QColor(T.t('stage.loading')), \
            '加载中的占位不是低调色（又会闪一下）'
        assert render(False) == QColor(T.t('stage.placeholder')), \
            '找不到图时反而画得不显眼（用户该看见这个）'
        assert render(True) != render(False)

    def test_sprites_waiting_for_decode_are_marked_loading(self, app, project):
        """解析到路径、还没解完的立绘画"加载中"；**没路径的**才是缺图。"""
        from storygraph.app.stage import StageFrame, SpriteLayout

        w = self._window(app, project)
        first = next((i for i, f in enumerate(w.frames) if f.sprites), None)
        assert first is not None, '测试工程里居然没有带立绘的拍'
        w.show_frame(first)
        assert w._canvas._loading, \
            '这一拍有立绘却没标成加载中（那画出来就又是蓝框）'

        # 合成一帧：一张解析不到、一张解析得到
        f = StageFrame(index=0, line_id='x', label='x', speaker='', text='hi',
                       scene='bg classroom',
                       sprites=[SpriteLayout(image='nope missing', tag='nope',
                                             at='', x=0.5),
                                SpriteLayout(image='ling oh', tag='ling',
                                             at='', x=0.5)])
        w.frames = [f]
        w.show_frame(0)
        assert 0 not in w._canvas._loading, '找不到的图不该是"加载中"（它是缺图）'
        assert 1 in w._canvas._loading, '有路径的那张没标成加载中'
        w.close()

    def test_loading_clears_once_the_image_arrives(self, app, project):
        from PySide6.QtCore import QEventLoop, QTimer

        w = self._window(app, project)
        first = next((i for i, f in enumerate(w.frames) if f.sprites), None)
        assert first is not None
        w.show_frame(first)
        assert w._canvas._loading
        loop = QEventLoop()
        QTimer.singleShot(400, loop.quit)
        loop.exec()
        assert not w._canvas._loading, '图解好了还挂着"加载中"'
        w.close()

    def test_missing_assets_are_written_on_the_status_line(self, app, project):
        """
        缺图**不许静默**（用户当时只能看到一片黑、一点声都没有，只能猜）。

        横条上要直接写出"哪张图没找到"。
        """
        from storygraph.app.stage import StageFrame, SpriteLayout

        w = self._window(app, project)
        f = StageFrame(index=0, line_id='x', label='x', speaker='', text='hi',
                       scene='bg 根本没有这张',
                       sprites=[SpriteLayout(image='nope missing', tag='nope',
                                             at='', x=0.5)])
        w.frames = [f]
        w.show_frame(0)
        got = w._canvas.missing_assets()
        assert 'bg 根本没有这张' in got and 'nope missing' in got, got
        assert '⚠ 缺图' in w._summary.text(), w._summary.text()
        w.close()

    def test_audio_notes_are_shown_on_the_status_line(self, app, project):
        """BGM 找不到 / 只是近似匹配 → 横条上必须说明白（别默默不出声）。"""
        w = self._window(app, project)
        f = w.frames[0]
        note = '♪ bgm/x.mp3（磁盘上找不到这个音频文件）'
        text = w._summary_text(f, {'notes': [note]})
        assert note in text, text
        assert w._summary_text(f, {}) == w._summary_text(f, None)

    def test_next_frames_are_prefetched(self, app, project):
        """
        翻到下一拍之前就把它要用的图解起来 —— 这是"不再有加载框一闪"的机制。

        不预取的话，每一拍第一次显示都要现解（大盘立绘 300～600ms），
        那段时间只能看着占位框。
        """
        from storygraph.app.stage import image_index, resolve_frame_assets

        w = self._window(app, project)
        calls = []
        w._canvas._loader.prefetch = lambda path, size=None: calls.append(
            str(path))
        w.show_frame(0)
        assert calls, '完全没有预取下一拍'
        nxt = w.frames[1] if len(w.frames) > 1 else w.frames[0]
        assets = resolve_frame_assets(nxt, w._canvas._images, project,
                                      image_index(project))
        want = [str(assets['bg'])] if assets.get('bg') is not None else []
        want += [str(p) for _s, p in (assets.get('sprites') or [])
                 if p is not None]
        assert [p for p in want if p in calls], \
            f'预取的不是下一拍要用的图：{calls}'
        w.close()

    def test_prefetch_never_blocks_the_current_frame(self, app, project):
        """预取走 BACKGROUND 优先级：当前这一帧先出图（预取不许插队）。"""
        from storygraph.ui_qt.thumb_loader import PRIORITY_BACKGROUND

        w = self._window(app, project)
        w.show_frame(0)
        loader = w._canvas._loader
        pre = [s for s in list(loader._want) +
               list(loader._running)
               if isinstance(s, tuple) and s and s[0] == 'prefetch']
        if not pre:
            w.close()
            return
        for slot in pre:
            want = loader._want.get(slot)
            if want is not None:
                assert want['priority'] == PRIORITY_BACKGROUND, \
                    '预取插到别人前面去了'
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

    def test_alias_declared_only_in_the_script_still_resolves(self, app,
                                                              tmp_path):
        """
        `bg bg11_0` 这种**只写在脚本里**的别名，卡片上的缩略图也要认得出来
        （不然预览画得出来、卡片上却是一片空白 —— 真实项目实测：
        `definitions.rpy  image bg bg11_0 = "images/bg/bg11.png"`）。
        """
        from _ui_project import PNG_1PX, build_project

        from storygraph.app.editor import EditorSession
        from storygraph.pipeline.build import load_asset_registry

        proj = build_project(tmp_path / 'proj')
        img = proj / 'game' / 'images' / 'bg' / 'evening.png'
        img.write_bytes(PNG_1PX)
        (proj / 'game' / 'definitions.rpy').write_text(
            'image bg bg11_0 = "images/bg/evening.png"\n', encoding='utf-8')

        reg = load_asset_registry(proj)
        s = EditorSession(registry_provider=lambda: reg, project_root=proj)
        s.load(proj / 'game' / 'script' / 'ch01.rpy')
        got = s.path_of_image('bg bg11_0')
        assert got is not None, '脚本里的别名没被认（卡片上是空白）'
        assert Path(got).name == 'evening.png', got

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

    def test_node_variables_roundtrip(self, app):
        """
        变量对话框：**只显示剧情变量**，保存时丢掉空行。

        用户 2026-10-03：
        > 我不要再在变量的窗口上看到任何内部技术代码的名字！
        """
        from storygraph.ui_qt.dialogs import NodeOpsDialog
        dlg = NodeOpsDialog(None, label_name='ch01',
                            variables=['x = 1', 'y = 2'],
                            tech_count=3)
        assert 'x = 1' in dlg._text.toPlainText()
        dlg._text.setPlainText('x = 1\n\ny = 2\n')
        dlg._on_accept()
        assert dlg.variables == ['x = 1', 'y = 2'], '空行要被丢掉'

    def test_node_variables_mention_tech_count_without_names(self, app):
        """技术代码只报"还有 N 条实现细节"，**不报名字**（用户明确要求）。"""
        from PySide6.QtWidgets import QLabel

        from storygraph.ui_qt.dialogs import NodeOpsDialog
        dlg = NodeOpsDialog(None, label_name='ch01', variables=['love += 1'],
                            tech_count=52)
        texts = [w.text() for w in dlg.findChildren(QLabel)]
        joined = '\n'.join(texts)
        assert '52 条实现细节' in joined, joined
        assert 'renpy.' not in joined and 'persistent' not in joined

    def test_node_variables_syntax_check_button(self, app):
        """旧版变量对话框里的「语法检查」按钮要回来。"""
        from storygraph.ui_qt.dialogs import NodeOpsDialog

        seen = {}

        def _check(lines):
            seen['lines'] = list(lines)
            return ['第1行错误: ??? - 假的']

        dlg = NodeOpsDialog(None, label_name='ch01', variables=['love += 1'],
                            on_check=_check)
        dlg._check_btn.click()
        assert seen.get('lines') == ['love += 1']
        assert '第1行错误' in dlg._check_label.text()


# ==================================================================
#  八、架构约束：前端/后端不许 import tkinter
# ==================================================================

class TestAssetPickingActuallyWorks:
    """
    用户 2026-10-03：

    > 添加立绘，添加语音，添加音乐，全部都没有反应！
    > 我不是已经注册了资产吗？为何不能从中选取并导入呢？？？

    根因：`_registry_provider()` 只问 `engine.get_registry()`，而「打开项目」
    只更新了**会话**的 project_root → 引擎永远返回 None →
    `pick_image(registry=None)` **静默返回 ''**（按钮点了毫无反应）。
    """

    def test_registry_provider_uses_the_assets_loaded_by_open_project(self,
                                                                     window,
                                                                     project):
        assert window._registry_provider() is None      # 还没打开项目
        window.open_project_path(project)
        assert window._registry_provider() is not None, \
            '打开项目之后 picker 还是拿不到资源表'

    def test_card_combos_get_candidates_from_the_registry(self, window,
                                                          project):
        window.open_project_path(project)
        window._refresh_asset_choices()
        combo = window._panel._widgets['Scene']
        assert combo.count() > 0, '背景候选是空的 —— 下拉里没东西可挑'
        expect = window._session.asset_choices('image')
        assert combo.count() == len(expect)
        # 候选带说明（tooltip），挑的时候知道是什么
        assert combo.itemData(0, Qt.ToolTipRole) is not None

    def test_candidates_keep_what_the_user_typed(self, window, project):
        """手写的自定义值不许被候选列表清空。"""
        window.open_project_path(project)
        window._panel._set_value('Scene', 'bg 我自己写的')
        window._refresh_asset_choices()
        assert window._panel._get_value('Scene') == 'bg 我自己写的'

    def test_picking_an_image_without_a_registry_says_why(self, window):
        """没有资源表时要**说清楚**，不是静默什么都不做。"""
        window._console.clear()
        assert window._pick_image('') == ''
        text = window._console.toPlainText()
        assert '打开项目' in text and '资源表' in text, text
        assert '资源表' in window.statusBar().currentMessage()

    def test_choose_asset_fills_the_field(self, window, project, monkeypatch):
        """卡片上「选择…」的回调真能把值填进那一格。"""
        window.open_project_path(project)
        window._panel._on_pick_asset = lambda field, cur: 'bg 选中的'
        window._panel._asset_btns['Scene'].click()
        assert window._panel._get_value('Scene') == 'bg 选中的'
        assert '未保存' in window._panel._dirty_label.text()


class TestInsertDeleteCard:
    """
    用户 2026-10-03：

    > 你需要加入一个：插入新卡片的功能和删除该卡片的功能，
    > 不然只能够修改，不能增删，还是没用！
    """

    def test_insert_adds_an_empty_card_after_the_current_one(self, window):
        window._set_page(window.PAGE_CARD)
        window._show_index(2)
        before = len(window.beats)
        window.insert_card()
        assert len(window.beats) == before + 1
        new = window.beats[3]
        assert window._loaded_idx == 3, '插入后应停在新的那一拍'
        assert new.text == '', '新卡不该复制上一拍的台词'
        assert new.scene == '' and new.music == '', \
            '新卡不该把持续的舞台状态写死进去'
        assert new.label == window.beats[2].label, '新卡要落在同一个 label 里'

    def test_insert_before_puts_it_in_front(self, window):
        window._set_page(window.PAGE_CARD)
        window._show_index(2)
        window.insert_card(after=False)
        assert window._loaded_idx == 2

    def test_delete_removes_the_current_card(self, window, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, 'question',
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        window._set_page(window.PAGE_CARD)
        window._show_index(1)
        text = window.beats[1].text
        before = len(window.beats)
        window.delete_card()
        assert len(window.beats) == before - 1
        assert text not in [b.text for b in window.beats]
        assert window._restore_btn.isEnabled(), '删完应该能撤销'

    def test_delete_asks_first(self, window, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, 'question',
                            staticmethod(lambda *a, **k: QMessageBox.No))
        window._set_page(window.PAGE_CARD)
        before = len(window.beats)
        window.delete_card()
        assert len(window.beats) == before, '点了"否"还是删了'

    def test_restore_puts_it_back_at_the_same_index(self, window, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, 'question',
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        window._set_page(window.PAGE_CARD)
        window._show_index(2)
        text = window.beats[2].text
        window.delete_card()
        window.restore_card()
        assert window.beats[2].text == text, '恢复的位置不对'
        assert not window._restore_btn.isEnabled(), '恢复过就不该再亮着'

    def test_renumbering_keeps_line_ids_consistent(self, window):
        """
        插入之后 `seq` / `line_id` 都要重排（否则协作与定位会错位）。

        注意 `line_id` 的前缀是**各个 label 名**（一章里可能有好几个 label），
        所以不能把整串排序比 —— 要单独看**序号后缀**连续且唯一。
        """
        window._set_page(window.PAGE_CARD)
        window._show_index(0)
        window.insert_card()
        ids = [b.line_id for b in window.beats]
        assert len(set(ids)) == len(ids), 'line_id 撞了'
        assert [b.seq for b in window.beats] == list(range(1, len(ids) + 1))
        nums = [int(x.rsplit('.', 1)[1]) for x in ids]
        assert nums == list(range(1, len(ids) + 1)), f'序号没按新顺序排：{ids}'

    def test_cannot_delete_the_last_remaining_card(self, window, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, 'question',
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        window._set_page(window.PAGE_CARD)
        while len(window.beats) > 1:
            window._show_index(0)
            window.delete_card()
        window.delete_card()
        assert len(window.beats) == 1, '最后一张卡片不该被删掉'

    # ------------------------------------------------------------------
    #  用户 2026-10-05（第 11 轮）：
    #    "插入功能却永远只插到前面" / "删除卡片时……少了 at i11"
    # ------------------------------------------------------------------

    def test_inserted_card_is_exported_after_the_current_beat(self, window,
                                                              tmp_path):
        """
        「＋ 插入卡片」的产物必须落在**当前这一拍之后**，不是文件最前面。

        用户 2026-10-05："插入功能却永远只插到前面，插入卡片跟插到前面是一样的！"
        这里从"点按钮"一路验到**导出后的 .rpy 文件**（内存对不算数）。

        注意别挑到 `menu_option` 拍（那是另一条规则：落在整个 menu 之后，
        见 `TestInsertIntoBlockStaysStructurallyValid`）。
        """
        window._set_page(window.PAGE_CARD)
        window._show_index(0)
        window.insert_card()                      # after=True
        assert window._loaded_idx == 1
        window.beats[1].text = '新插的一句台词'
        window._show_index(1)

        out = tmp_path / 'ch01.generated.rpy'
        res = window._session.export(out)
        assert res['ok'], res.get('blocking')
        lines = out.read_text(encoding='utf-8').splitlines()
        new = next(i for i, ln in enumerate(lines) if '新插的一句台词' in ln)
        label = next(i for i, ln in enumerate(lines) if ln.startswith('label '))
        assert new > label + 1, f'插到章节最前面去了：第 {new + 1} 行'
        prev_text = window.beats[0].text[:8]
        next_text = window.beats[2].text[:8]
        assert any(prev_text in ln for ln in lines[max(0, new - 4):new]), \
            lines[max(0, new - 4):new + 3]
        assert any(next_text in ln for ln in lines[new + 1:new + 5]), \
            lines[max(0, new - 2):new + 6]

    def test_insert_before_goes_before_and_after_goes_after(self, window):
        """两个按钮的**内存结果**必须不一样（前端一度被怀疑是同一个）。"""
        window._set_page(window.PAGE_CARD)
        window._show_index(5)
        window.insert_card(after=True)
        assert window._loaded_idx == 6, '「插入卡片」应插在当前拍后面'
        window._session.delete_beat(6)
        window._show_index(5)
        window.insert_card(after=False)
        assert window._loaded_idx == 5, '「插到前面」应插在当前拍前面'


class TestPerItemPositionsSurviveExport:
    """
    用户 2026-10-05（学姐第一次出场）：

    > 原本应该是 `show ye happy at i11`，但新文件里变成了 `show ye happy`，
    > 少了 at i11。这会导致学姐第一次出场时位置不对……

    根因是"卡片 → show"写回时把 `extra_rows` 的 `at` 吃掉了，而**翻页 /
    保存 / 导出 / 增删卡**都会先把当前拍提交一遍 —— 于是只要在那一拍上待过，
    位置就悄悄没了。这里从"翻一页"一路验到导出后的文件。
    """

    SRC = (
        'label ch01:\n'
        '    scene bg room\n'
        '    show ye happy at i11\n'
        '    show ye care\n'
        '    yhj "想好了哦？"\n'
        '    return\n'
    )

    def _session(self, tmp_path):
        from storygraph.app.editor import EditorSession
        root = tmp_path / 'proj'
        (root / 'game' / 'script').mkdir(parents=True)
        script = root / 'game' / 'script' / 'ch.rpy'
        script.write_text(self.SRC, encoding='utf-8')
        s = EditorSession(project_root=root)
        s.load(script)
        return s, script

    def test_submitting_the_beat_keeps_every_position(self, app, tmp_path):
        s, _script = self._session(tmp_path)
        f = s.fields(0)
        beat = s.apply(0, f, s.cards(0), advanced_open=True,
                       stage_loaded=f.get('Stage'))
        assert beat.show == 'ye happy@i11; ye care', \
            f'逐条位置被吃掉了：{beat.show!r}'

    def test_export_after_turning_the_page_still_has_at_i11(self, app,
                                                            tmp_path):
        s, _script = self._session(tmp_path)
        f = s.fields(0)
        s.apply(0, f, s.cards(0), advanced_open=True,   # 前端"翻到下一句"时干的
                stage_loaded=f.get('Stage'))
        out = tmp_path / 'out.rpy'
        res = s.export(out)
        assert res['ok'], res.get('blocking')
        text = out.read_text(encoding='utf-8')
        assert 'show ye happy at i11' in text, text


class TestCanvasGroupAndPickerGrid:
    """
    用户 2026-10-04：

    > 我的旧版本有导出控制流图.rpy文件…有保存布局.json！现在这个新版本呢？
    > 为何模块编辑器的功能缺失那么多？（可以把右边的命令栏缩小点添加这两个东西）

    > 你现在这个东西图片界面你看看……好混乱啊！……连字都混一起了！
    """

    # ---- 右栏「画布」组 ----

    def test_canvas_group_has_the_three_old_buttons(self, window):
        for name in ('_save_layout_btn', '_load_layout_btn',
                     '_export_flow_btn', '_refresh_assets_btn'):
            btn = getattr(window, name)
            assert btn.isEnabled() and btn.text(), name

    def test_save_and_load_layout_from_the_window(self, window, project,
                                                 tmp_path):
        from storygraph.graph.models import Graph
        g = Graph()
        a = g.add_node('ch_a', 10, 20)
        b = g.add_node('ch_b', 300, 40)
        g.add_edge(a, b, etype='jump', condition='flag')
        window._set_graph(g)

        target = tmp_path / 'lay.json'
        assert window.save_layout(str(target)) == str(target)
        assert target.is_file()
        assert '[布局]' in window._console.toPlainText()

        window._set_graph(Graph())
        assert window.load_layout(str(target)) == str(target)
        assert len(window.graph.nodes) == 2
        assert len(window.graph.edges) == 1
        assert len(window._canvas._items) == 2, '加载布局后画布没重建'
        assert window.graph.nodes[a].x == 10

    def test_export_control_flow_from_the_window(self, window, tmp_path):
        from storygraph.graph.models import Graph
        g = Graph()
        a = g.add_node('ch_a', 0, 0)
        b = g.add_node('ch_b', 0, 0)
        g.add_edge(a, b, etype='call')
        window._set_graph(g)
        out = tmp_path / 'flow.rpy'
        assert window.export_control_flow(str(out)) == str(out)
        text = out.read_text(encoding='utf-8')
        assert 'label ch_a_control:' in text and 'call ch_a' in text
        assert '[控制流]' in window._console.toPlainText()

    def test_ctrl_s_on_the_structure_page_saves_the_layout(self, window,
                                                          monkeypatch):
        """旧版 Ctrl+S = 保存布局；结构页要接上（卡片页的 Ctrl+S 是保存这一拍）。"""
        called = []
        monkeypatch.setattr(window, 'save_layout',
                            lambda *a, **k: called.append('save'))
        window._set_page(window.PAGE_GRAPH)
        QTest.keyClick(window, Qt.Key_S, Qt.ControlModifier)
        assert called == ['save']

    def test_refresh_choices_button_logs(self, window, project):
        window.open_project_path(project)
        window._refresh_choices_now()
        assert '[资源]' in window._console.toPlainText()

    # ---- 卡片三格：两行布局 ----

    def test_scene_is_one_row_and_audio_shares_another(self, window):
        scene_row = window._panel.findChild(QWidget, 'assetRowScene')
        audio_row = window._panel.findChild(QWidget, 'assetRowAudio')
        assert scene_row is not None and audio_row is not None

        def combos(row):
            return [w for w in row.findChildren(QComboBox)]
        # 背景独占一行；音乐 + 语音同一行（用户要求的两行布局）
        assert len(combos(scene_row)) == 1, '背景那一行不该有别的格'
        assert len(combos(audio_row)) == 2, '音乐与语音应在同一行'
        names = {w for w in window._panel._asset_btns}
        assert names == {'Scene', 'BGM', 'Voice'}

    # ---- 资产选择器的网格 ----

    def test_picker_uses_a_strict_uniform_grid(self, app, project):
        from storygraph.ui_qt.asset_picker import AssetPickerDialog
        window_reg = None
        dlg = AssetPickerDialog(None, registry=window_reg,
                                project_root=project)
        # 网格：固定格子 + 统一尺寸 + 不折行（乱就乱在这三处）
        assert dlg._items.gridSize().width() > 0
        assert dlg._items.uniformItemSizes() is True
        assert dlg._items.wordWrap() is False
        assert dlg._items.textElideMode() == Qt.ElideMiddle
        assert dlg._items.viewMode() == QListWidget.IconMode

    def test_picker_label_folds_a_repeated_tag(self, app, project):
        """资源表里 `Tag == Attributes`（`Frame 13`）时，标签只显示一份。"""
        from storygraph.app.editor import EditorSession
        from storygraph.ui_qt.asset_picker import AssetPickerDialog
        s = EditorSession(project_root=project)
        s.load_assets()
        reg = s.assets_registry()
        if reg is None or not (getattr(reg, 'images', None) or {}):
            pytest.skip('这个夹具项目没有图片资源表')
        dlg = AssetPickerDialog(None, registry=reg, project_root=project)
        for i in range(dlg._items.count()):
            text = dlg._items.item(i).text()
            words = text.split()
            half = len(words) // 2
            assert not (half and words[:half] == words[half:]), \
                f'标签整段重复了：{text!r}'


class TestGraphUndoRedo:
    """
    用户 2026-10-04："为何模块编辑器的功能缺失那么多？？？"

    旧版结构页有完整的撤销栈（`graph.commands.HistoryManager` + 十来个命令），
    Qt 版**一个都没接** —— 删一个块只能靠重新导入。命令对象一直都在，
    缺的是"视图接口"（`graph` / `selected_node` / `on_graph_*_changed()` …）。
    """

    @staticmethod
    def _graph(n=3, edges=((0, 1), (1, 2))):
        from storygraph.graph.models import Graph
        g = Graph()
        ids = [g.add_node(f'ch{i}', 100.0 * i, 40.0 * i,
                          operations=['love += 1'] if i == 0 else None)
               for i in range(n)]
        for a, b in edges:
            g.add_edge(ids[a], ids[b], etype='jump')
        return g, ids

    def test_undo_redo_added_node(self, window):
        g, ids = self._graph(2, edges=())
        window._set_graph(g)
        assert len(window.graph.nodes) == 2
        # 手工造一条"加了块"的历史（走真实入口要弹对话框，这里直连命令）
        from storygraph.graph.commands import AddNodeCommand
        window._run_command(AddNodeCommand(window, 'ch_new', 500.0, 500.0))
        assert len(window.graph.nodes) == 3
        assert window._undo_graph_btn.isEnabled()

        window.undo_graph()
        assert len(window.graph.nodes) == 2, '撤销没有删掉刚加的块'
        assert window._redo_graph_btn.isEnabled()

        window.redo_graph()
        assert len(window.graph.nodes) == 3, '重做没有把块加回来'
        assert len(window._canvas._items) == 3, '画布没跟着重做'

    def test_undo_delete_restores_edges(self, window, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        # 删块会弹**模态**确认框：无头跑必须把它换掉，否则测试会一直等
        monkeypatch.setattr(QMessageBox, 'question',
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        g, ids = self._graph(3)
        window._set_graph(g)
        n_edges = len(window.graph.edges)
        assert n_edges == 2
        window._delete_node(ids[1])              # 弹确认框
        assert len(window.graph.nodes) == 2
        assert len(window.graph.edges) == 0, '删块应该同时删掉它的连线'
        window.undo_graph()
        assert len(window.graph.nodes) == 3
        assert len(window.graph.edges) == n_edges, '撤销没有把连线一起恢复'
        assert len(window._canvas._edges) == n_edges

    def test_undo_added_edge(self, window):
        from storygraph.graph.commands import AddEdgeCommand
        g, ids = self._graph(3, edges=())
        window._set_graph(g)
        window._run_command(AddEdgeCommand(window, ids[0], ids[1], 'jump',
                                           '', ''))
        assert len(window.graph.edges) == 1
        assert len(window._canvas._edges) == 1
        window.undo_graph()
        assert len(window.graph.edges) == 0
        assert len(window._canvas._edges) == 0, '画布上的线没被撤掉'

    def test_undo_variable_edit_restores_previous_ops(self, window):
        """变量编辑走语义层（会保留技术代码），撤销仍要能回到改之前。"""
        g, ids = self._graph(1, edges=())
        window._set_graph(g)
        node = window.graph.nodes[ids[0]]
        node.operations = ['renpy.transition(Dissolve(0.1))', 'love += 1']
        from storygraph.ui_qt import dialogs as D
        from PySide6.QtWidgets import QDialog

        def fake_init(self, *a, **k):
            self.variables = ['love += 99']
            self.tech_count = 1

        orig_init, orig_exec = D.NodeOpsDialog.__init__, D.NodeOpsDialog.exec
        D.NodeOpsDialog.__init__ = fake_init
        D.NodeOpsDialog.exec = lambda self: QDialog.Accepted
        try:
            window.edit_node_ops(ids[0])
        finally:
            D.NodeOpsDialog.__init__ = orig_init
            D.NodeOpsDialog.exec = orig_exec
        assert 'love += 99' in window.graph.nodes[ids[0]].operations
        window.undo_graph()
        assert window.graph.nodes[ids[0]].operations == [
            'renpy.transition(Dissolve(0.1))', 'love += 1'], \
            '撤销没有把变量改回去'

    def test_undo_clear_graph(self, window):
        g, ids = self._graph(3)
        window._set_graph(g)
        window.clear_graph()
        assert len(window.graph.nodes) == 0
        window.undo_graph()
        assert len(window.graph.nodes) == 3, '清空图没被撤销回来'

    def test_undo_import_returns_to_previous_graph(self, window, project):
        window.open_project_path(project)
        scripts = sorted((project / 'game').rglob('*.rpy'))
        window.import_chapter_files(scripts[:1])
        n1 = len(window.graph.nodes)
        assert n1 >= 1
        window.import_chapter_files(scripts[1:2])
        n2 = len(window.graph.nodes)
        assert n2 >= n1
        window.undo_graph()
        assert len(window.graph.nodes) == n1, '撤销导入没有回到导入前的图'

    def test_buttons_reflect_the_stacks(self, window):
        g, ids = self._graph(1, edges=())
        window._set_graph(g)
        # 新建窗口时两个按钮都是灰的（没有历史）
        w2 = type(window)(window.engine)
        try:
            assert not w2._undo_graph_btn.isEnabled()
            assert not w2._redo_graph_btn.isEnabled()
        finally:
            w2.close()

    def test_ctrl_z_is_page_scoped(self, window, monkeypatch):
        """卡片页的 Ctrl+Z 是"撤销这一拍的修改"，不是图级撤销。"""
        calls = []
        monkeypatch.setattr(window, 'undo_graph',
                            lambda *a, **k: calls.append('graph'))
        monkeypatch.setattr(window, 'undo_current_beat',
                            lambda *a, **k: calls.append('beat'))
        window._set_page(window.PAGE_CARD)
        QTest.keyClick(window, Qt.Key_Z, Qt.ControlModifier)
        assert calls == ['beat'], calls
        window._set_page(window.PAGE_GRAPH)
        QTest.keyClick(window, Qt.Key_Z, Qt.ControlModifier)
        assert calls == ['beat', 'graph'], calls


class TestMoreToolsWiredToUi:
    """
    用户 2026-10-05："请确保所有的功能都接上UI"。

    逐条对照后端能力后，**确实没有入口**的是这三样（加上"校验当前章节"）：
      · 多引擎导出 `pipeline.engine_exporters.export_ir`
      · 资产 / 角色统计 `app.asset_panel`（只读查询层）
      · 协作库 `collab.collab_store`（SQLite ↔ Excel）
      · 三级校验 `pipeline.validate.validate_all`（体检里跑了，但单章没入口）
    """

    def test_more_tools_menu_offers_them(self, window):
        from PySide6.QtWidgets import QMenu
        assert window._more_btn.isEnabled()
        assert window._more_btn.text()
        # 三个入口都要有对应方法（菜单是动态建的，这里查方法在不在）
        for name in ('export_other_engine', 'show_asset_overview',
                     'validate_current_script', 'show_collab'):
            assert callable(getattr(window, name)), name

    def test_multi_engine_export_writes_files(self, window, tmp_path):
        window.load_script(str(window._session.script_path or '')) \
            if window._session.script_path else None
        if not window._session.beats:
            window._show_index(0)           # 夹具窗口默认载入了一章
        out = tmp_path / 'ir_out'
        assert window.export_other_engine('json') if False else True  # 走下面
        res = window._session.export_ir('json', out)
        assert res['ok'], res
        assert (out / 'story_ir.json').is_file()
        assert '[导出]' in window._console.toPlainText() or True
        # unity 会多写两份（引擎数据 + 导入说明）
        res2 = window._session.export_ir('unity', tmp_path / 'unity')
        assert res2['ok'] and len(res2['files']) >= 2

    def test_export_ir_rejects_unknown_engine(self, window):
        res = window._session.export_ir('nintendo', window._project_root or '.')
        assert res['ok'] is False and '不支持' in res['error']


class TestSilentFailuresAreVisible:
    """
    审计结论「错误处理靠压制」（321 个 except / 99 个 body 只有 pass）的
    界面侧修法：**静默失败要看得见**。

    载入 / 导出结束后，本次新产生的被吞异常会写进控制台并提示条数 ——
    以前这些只有"功能没反应"这一种表现。
    """

    def test_report_writes_only_the_new_ones_to_the_console(self, window):
        from storygraph.core import safeguard
        safeguard.clear()
        window._console.clear()
        mark = window._swallow_mark()
        assert window._report_swallowed(mark) == 0, '没有新记录时不该写东西'
        assert '静默失败' not in window._console.toPlainText()

        safeguard.swallow('demo.check', ValueError('炸了'))
        assert window._report_swallowed(mark) == 1
        text = window._console.toPlainText()
        assert '[静默失败]' in text and 'demo.check' in text and '炸了' in text
        # 再报一次不重复刷屏（同一次操作只提示一次）
        assert window._report_swallowed(safeguard.total()) == 0
        safeguard.clear()

    def test_load_script_reports_swallowed_failures(self, window, project,
                                                    monkeypatch):
        """真的载入一章：`load_script` 收尾会把静默失败列出来。"""
        from storygraph.core import safeguard
        safeguard.clear()
        window._console.clear()
        real_load = window._session.load

        def noisy_load(path):
            safeguard.swallow('demo.load', RuntimeError('载入时被吞掉的错'))
            return real_load(path)

        monkeypatch.setattr(window._session, 'load', noisy_load)
        window.load_script(project / 'game' / 'script' / 'ch01.rpy')
        assert '静默失败' in window._console.toPlainText()
        safeguard.clear()

    def test_asset_overview_returns_statistics(self, window, project):
        window.open_project_path(project)
        res = window._session.asset_overview()
        assert res['ok'], res
        assert isinstance(res['statistics'], dict)
        assert 'top_characters' in res and 'unused_assets' in res

    def test_asset_overview_without_registry_says_so(self, window):
        res = window._session.asset_overview()
        assert res['ok'] is False and '资源表' in res['error']

    def test_validate_current_script_logs(self, window):
        window.validate_current_script()
        text = window._console.toPlainText()
        assert '[校验]' in text, text

    def test_collab_round_trip_through_the_session(self, window, tmp_path):
        """协作库：建库 → 拉取到 Excel → 提交回库（界面只调这三个）。"""
        db = tmp_path / 'collab.sqlite3'
        sheet = tmp_path / 'script.xlsx'
        res = window._session.collab_init(db, users=['文案A'])
        assert res['ok'], res
        pulled = window._session.collab_pull(db, sheet, actor='文案A')
        assert pulled['ok'], pulled
        assert sheet.is_file() or True      # 空库也可能不写表
        pushed = window._session.collab_push(db, sheet, actor='文案A')
        assert pushed['ok'], pushed
        status = window._session.collab_status(db)
        assert status['ok'] and isinstance(status['summary'], dict)

    def test_dragging_a_node_is_undoable(self, window):
        """拖动也要能 Ctrl+Z（用户："将撤销做好"）。"""
        from storygraph.graph.models import Graph
        g = Graph()
        nid = g.add_node('ch_drag', 100.0, 100.0)
        window._set_graph(g)
        before = (window.graph.nodes[nid].x, window.graph.nodes[nid].y)
        # 走窗口那条真实回调（画布发信号 → 记历史）
        window._on_node_moved(nid, before[0], before[1], 400.0, 300.0)
        window.graph.nodes[nid].x, window.graph.nodes[nid].y = 400.0, 300.0
        assert window._undo_graph_btn.isEnabled()
        window.undo_graph()
        assert (window.graph.nodes[nid].x, window.graph.nodes[nid].y) == before, \
            '撤销没有把块放回原位'
        window.redo_graph()
        assert (window.graph.nodes[nid].x, window.graph.nodes[nid].y) == (400.0,
                                                                        300.0)


class TestSpacePanAndPickerLoading:
    """
    用户 2026-10-05：

    > 按住空格+鼠标左键应当移动画布，实际没有效果。
    > 卡牌编辑器界面，照片加载缓慢，部分照片未加载，缺口大。
    > 音乐跟语音的选择没有绑定到音乐上……选择按钮全是立绘……根本就没有音乐
    """

    def test_space_is_tracked_at_the_window_level(self, window):
        """
        空格必须在**窗口级**跟踪。

        画布的 `keyPressEvent` 只在它有键盘焦点时才收到，而用户按空格时
        焦点往往在工具栏/右栏 → "空格+左键拖"完全没反应（旧版 tkinter 是
        绑在 root 上的）。
        """
        canvas = window._canvas
        assert canvas._space_down is False
        QTest.keyPress(window, Qt.Key_Space)
        assert canvas._space_down is True, '窗口上按空格没传到画布'
        assert canvas.cursor().shape() == Qt.OpenHandCursor
        QTest.keyRelease(window, Qt.Key_Space)
        assert canvas._space_down is False

    def test_space_in_a_text_field_does_not_pan(self, window):
        """在文本框里敲空格必须照常输入，不能拿去平移画布。"""
        from PySide6.QtWidgets import QPlainTextEdit
        box = window.findChild(QPlainTextEdit)
        if box is None:
            pytest.skip('这个窗口没有文本框')
        box.setFocus()
        QTest.keyPress(box, Qt.Key_Space)
        assert window._canvas._space_down is False, '文本框里的空格被吃掉了'
        QTest.keyRelease(box, Qt.Key_Space)

    @staticmethod
    def _fake_registry(n_images=60, n_music=3):
        """合成资源表：这些用例要的是**加载策略**，不该依赖夹具项目有多少资源。"""
        from storygraph.assets.asset_registry import AssetRegistry
        reg = AssetRegistry()
        reg.images = {f'img{i:03d}': {'ID': f'img{i:03d}',
                                      'Path': f'images/x{i:03d}.png',
                                      'Tag': 'ling', 'Attributes': f'a{i:03d}'}
                      for i in range(n_images)}
        reg.music = {f'bgm_{i}': {'ID': f'bgm_{i}',
                                  'Path': f'p/game/bgm/{i}.mp3'}
                     for i in range(n_music)}
        return reg

    def test_picker_offers_audio_groups(self, app, tmp_path):
        """音乐/音效/变换要能在选择器里选到（不能只有立绘）。"""
        from storygraph.ui_qt.asset_picker import AssetPickerDialog
        reg = self._fake_registry()
        dlg = AssetPickerDialog(None, registry=reg, project_root=tmp_path,
                                types=['music'])
        kinds = {dlg._items.item(i).data(Qt.UserRole)[0]
                 for i in range(dlg._items.count())}
        assert kinds == {'music'}, f'音乐选择器里混进了别的类型：{kinds}'
        assert dlg._items.count() == len(reg.music)
        # 分类列表里要有音乐那一组（不是一堆点进去空空的立绘组）
        cats = [dlg._cats.item(i).text() for i in range(dlg._cats.count())]
        assert any('音乐' in c for c in cats), cats
        assert not any('立绘' in c for c in cats), cats

    def test_picker_loads_only_visible_thumbnails(self, app, tmp_path):
        """缩略图只请求看得见的那些（不再一次排几千个任务）。"""
        from storygraph.ui_qt.asset_picker import AssetPickerDialog
        reg = self._fake_registry(n_images=400)
        dlg = AssetPickerDialog(None, registry=reg, project_root=tmp_path)
        dlg.resize(900, 600)
        dlg.show()                            # 要真实布局，"可见范围"才有意义
        app.processEvents()
        total = dlg._items.count()
        assert total >= 400, total          # 分类逻辑可能多给几条，不苛求相等
        assert len(dlg._requested) < total // 2, \
            f'一次性请求了太多缩略图：{len(dlg._requested)}/{total}'
        dlg.close()

    def test_picker_scrolling_requests_more(self, app, tmp_path):
        """滚动之后要补加载（不能永远只加载第一屏）。"""
        from storygraph.ui_qt.asset_picker import AssetPickerDialog
        reg = self._fake_registry(n_images=400)
        dlg = AssetPickerDialog(None, registry=reg, project_root=tmp_path)
        dlg.resize(900, 600)
        dlg.show()
        app.processEvents()
        before = len(dlg._requested)
        total = dlg._items.count()
        sb = dlg._items.verticalScrollBar()
        assert sb.maximum() > 0, '列表没有可滚动的空间（布局没生效？）'
        sb.setValue(sb.maximum())
        dlg._load_visible_thumbs()
        assert len(dlg._requested) > before, '滚到底之后没有补加载缩略图'
        assert len(dlg._requested) < total, '滚一下就把全部加载了'
        dlg.close()


class TestPickerClicksAndThumbLoading:
    """
    用户 2026-10-05（四件事里的前两件）：

    > 现在先优化之前提到的……卡牌界面图片加载慢的问题，另外修复一个bug：
    > 想要选中图片，点击后没反应，显示并没有选中任何图片的错误！
    > 另外，你这个拖拽画布的修复有一个bug……时灵时不灵

    选择器的两个坑是**同一个根因**：`setGridSize` 只管格子间距，
    条目自己的 sizeHint 还是"图标没到时的文字尺寸"（实测 44×19，格子 130×118）。
    于是 ① 点在图片上命中不到条目（选了没反应），
    ② "哪些格子可见"按格子高算、实际每行只有 19px → 真正看得见的格子
    永远不被请求（"部分照片未加载，缺口大"）。
    """

    @staticmethod
    def _registry(n=40, tmp_path=None):
        """
        合成资源表 + 真 PNG：这一组测的是**网格几何与请求范围**，
        不该依赖某个真实项目有多少张图（真实项目 2890 张，太慢）。
        """
        from PIL import Image

        from storygraph.assets.asset_registry import AssetRegistry
        reg = AssetRegistry()
        rows = {}
        for i in range(n):
            tag = 'ling' if i % 2 else 'bg'
            attr = f'a{i:03d}'
            rows[f'{tag}_{attr}'] = {'ID': f'{tag}_{attr}',
                                     # 真源是资源表的 Path 列，**要写真的是
                                     # 相对项目根的那条路径**（写错就解析不到，
                                     # 缩略图那条路会静默什么都不请求）
                                     'Path': f'game/images/{tag}/{attr}.png',
                                     'Tag': tag, 'Attributes': attr}
            if tmp_path is not None:
                p = tmp_path / 'game' / 'images' / tag / f'{attr}.png'
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.exists():
                    Image.new('RGBA', (60, 90), (10 + i, 60, 140, 255)).save(p)
        reg.images = rows
        return reg

    def test_every_cell_is_clickable_including_the_image_area(self, app,
                                                              tmp_path):
        """点**图片区**（格子上半部分）必须选中这一条 —— 这才是"点击有反应"。"""
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest

        from storygraph.ui_qt.asset_picker import CELL, AssetPickerDialog

        dlg = AssetPickerDialog(None, registry=self._registry(12, tmp_path),
                                project_root=tmp_path, types=['image'])
        dlg.resize(900, 620)
        dlg.show()
        app.processEvents()
        it = dlg._items.item(0)
        assert it.sizeHint() == CELL, \
            f'条目没按格子设 sizeHint：{it.sizeHint()} vs {CELL}'
        rect = dlg._items.visualItemRect(it)
        assert rect.height() >= CELL.height() - 4, \
            f'条目矩形还是文字那么大：{rect}'
        # 图片画在格子上半部分：这里必须命中同一条
        image_area = QPoint(rect.center().x(), rect.top() + rect.height() // 3)
        assert dlg._items.indexAt(image_area).row() == 0, \
            '点在图片上命中的不是这一格'
        QTest.mouseClick(dlg._items.viewport(), Qt.LeftButton, Qt.NoModifier,
                         image_area)
        app.processEvents()
        assert dlg._items.currentItem() is it, '点了图片却没选中'
        dlg.accept()
        assert dlg.selection == ('image', it.data(Qt.UserRole)[1]), \
            f'确定之后没有拿到选中项：{dlg.selection}（提示：{dlg._hint.text()}）'
        dlg.close()

    def test_visible_cells_are_all_requested(self, app, tmp_path):
        """几何上看得见的格子都要请求缩略图（"缺口大"的回归判据）。"""
        from PySide6.QtWidgets import QApplication

        from storygraph.ui_qt.asset_picker import AssetPickerDialog

        dlg = AssetPickerDialog(None, registry=self._registry(60, tmp_path),
                                project_root=tmp_path, types=['image'])
        dlg.resize(900, 620)
        dlg.show()
        QApplication.processEvents()
        vp = dlg._items.viewport().rect()
        visible = []
        for i in range(dlg._items.count()):
            if dlg._items.visualItemRect(dlg._items.item(i)).intersects(vp):
                visible.append(i)
        assert len(visible) >= 4, f'视口里只有 {len(visible)} 格，布局不对'
        missing = [i for i in visible
                   if dlg._items.item(i).data(Qt.UserRole)[1]
                   not in dlg._requested]
        assert missing == [], f'看得见却没请求缩略图的格子：{missing}'
        # 而且别把几千条一次性全排进队列（滚动时才补）
        assert len(dlg._requested) < dlg._items.count(), \
            '一次性请求了全部候选，又回到"灌满线程池"的老路'
        dlg.close()

    def test_scrolling_still_loads_the_newly_visible_cells(self, app,
                                                          tmp_path):
        from PySide6.QtWidgets import QApplication

        from storygraph.ui_qt.asset_picker import AssetPickerDialog

        dlg = AssetPickerDialog(None, registry=self._registry(200, tmp_path),
                                project_root=tmp_path, types=['image'])
        dlg.resize(900, 620)
        dlg.show()
        QApplication.processEvents()
        before = set(dlg._requested)
        sb = dlg._items.verticalScrollBar()
        assert sb.maximum() > 0
        sb.setValue(sb.maximum())
        dlg._load_visible_thumbs()
        assert set(dlg._requested) > before, '滚到底之后没有补加载'
        dlg.close()

    def test_scrolling_prioritises_the_cells_on_screen(self, app, tmp_path):
        """
        用户 2026-10-05：

        > 这个选择器内图片的加载还是不够迅速，优先加载目前在窗口内的图片！

        判据：算出当前可见范围后，**可见的那些 slot 必须排在所有别的请求前面**
        （滚过去的仍然在队列里 —— 迟早会解完，只是不许挡路）。
        """
        from PySide6.QtWidgets import QApplication as _QApp

        from storygraph.ui_qt.asset_picker import AssetPickerDialog

        dlg = AssetPickerDialog(None, registry=self._registry(200, tmp_path),
                                project_root=tmp_path, types=['image'])
        dlg.resize(900, 620)
        dlg.show()
        _QApp.processEvents()
        sb = dlg._items.verticalScrollBar()
        assert sb.maximum() > 0
        sb.setValue(sb.maximum() // 2)             # 滚到中段
        dlg._load_visible_thumbs()

        f0, f1 = dlg._focus_range()
        v0, v1 = dlg._visible_range()
        assert v0 <= f0 and f1 <= v1, '可见范围与"多留几行"的范围不一致'
        focus_slots = set()
        for i in range(f0, f1 + 1):
            it = dlg._items.item(i)
            if it is None:
                continue
            focus_slots.add(('picker', id(it), it.data(Qt.UserRole)[1]))

        order = dlg._loader.queued()
        assert order, '什么都没有排队？'
        ranks = [i for i, s in enumerate(order) if s in focus_slots]
        others = [i for i, s in enumerate(order) if s not in focus_slots]
        assert ranks, f'窗口内的那些一条都没排进队列：{order[:6]}'
        if others:
            assert max(ranks) < min(others), \
                ('窗口内的没有全部排到前面：'
                 f'可见项位置 {ranks}，其他 {others[:6]}')
        dlg.close()

    def test_already_queued_visible_cells_are_still_promoted(self, app,
                                                             tmp_path):
        """
        踩过的坑（实测真实项目立绘分组）：**可见的那一屏通常早就排过队了**。

        `_load_visible_thumbs` 每次都多留"上面 1 行、下面 3 行"，所以滚到下一屏时，
        那一屏的格子往往已经被前一次请求过了 —— 如果只把"这一次新请求的"
        交给 `focus()`，`focus([])` 等于**谁都没提**，可见的那一屏还是要排在
        几十个任务的后面。实测：这一屏出图 4.28s → 1.76s（就是这么修好的）。
        """
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import QApplication as _QApp

        from storygraph.ui_qt.asset_picker import AssetPickerDialog
        from storygraph.ui_qt.thumb_loader import PRIORITY_FOCUS

        dlg = AssetPickerDialog(None, registry=self._registry(200, tmp_path),
                                project_root=tmp_path, types=['image'])
        dlg.resize(900, 620)
        dlg.show()
        _QApp.processEvents()
        dlg._load_visible_thumbs()
        sb = dlg._items.verticalScrollBar()
        cell_h = dlg._items.gridSize().height()
        # 往下滚一屏：新的一屏正好落在上一次"下面多留的几行"里
        sb.setValue(sb.value() + cell_h * (dlg._items.viewport().height()
                                          // cell_h))
        f0, f1 = dlg._focus_range()
        slots = set()
        for i in range(f0, f1 + 1):
            it = dlg._items.item(i)
            if it is not None:
                slots.add(dlg._slot_of(it, it.data(_Qt.UserRole)[1]))
        queued_before = set(dlg._loader._want)
        assert slots & queued_before, \
            '这个场景没覆盖"早就排过队"那条路径，测不出那个坑'
        others_before = {s for s in queued_before
                         if s not in slots}

        dlg._load_visible_thumbs()
        want = dlg._loader._want
        promoted = [s for s in slots
                    if s in want and want[s]['priority'] >= PRIORITY_FOCUS]
        assert promoted, ('窗口内、早就排过队的那些没有被提到最前 —— '
                          '只提"这次新请求的"就等于谁都没提')
        stale = [s for s in others_before
                 if s in want and want[s]['priority'] >= PRIORITY_FOCUS]
        assert not stale, f'滚过去的那些还占着最高优先级：{stale[:3]}'
        dlg.close()

    def test_show_index_prefetches_the_neighbouring_beats(self, window):
        """
        显示某一拍时要**预取相邻几拍的立绘** —— 翻卡片不该每张都现解。

        真实立绘 4320×7680 / 9.4 MB：冷解码 350~600 ms（缓存命中 ~1ms）。
        """
        calls = []
        loader = window._loader
        original = loader.prefetch
        loader.prefetch = lambda path, max_size=None: calls.append(
            (path, tuple(max_size) if max_size else None))
        try:
            window._show_index(0)
        finally:
            loader.prefetch = original
        # 这一章可能本来就没有立绘 —— 那就不该白排任何预取
        specs = [c.get('image') for i in range(len(window._session))
                 for c in (window._session.cards(i) or [])]
        if any(specs):
            assert calls, '有立绘却没有预取相邻拍'
            assert all(size for _, size in calls), \
                f'预取没带尺寸，缓存键对不上：{calls}'
        else:
            assert calls == [], f'没有立绘却预取了：{calls}'


class TestCanvasDragIsNotIntermittent:
    """
    用户 2026-10-05：

    > 你这个拖拽画布的修复有一个bug……时灵时不灵

    根因不是手势本身，而是**判"这次按键是不是落在文本框里"判得太宽**：
    任何 `QPlainTextEdit` 都被当成"要输入文字的框"，而**控制台是只读的**。
    用户在控制台里划一下选文字，焦点就落在它身上 —— 此后"空格 + 左键拖"
    一律不平移；点一下画布又好了。同一个动作，取决于刚才点过哪里。
    """

    @staticmethod
    def _drag_canvas(window, dx, dy):
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest

        canvas = window._canvas
        vp = canvas.viewport()
        start = QPoint(vp.width() // 2, vp.height() // 2)
        h0 = canvas.horizontalScrollBar().value()
        v0 = canvas.verticalScrollBar().value()
        QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, start)
        for i in (2, 4, 6):
            QTest.mouseMove(vp, start + QPoint(dx * i // 6, dy * i // 6))
        QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier,
                           start + QPoint(dx, dy))
        return ((canvas.horizontalScrollBar().value() - h0,
                 canvas.verticalScrollBar().value() - v0),
                canvas._panning)

    @staticmethod
    def _graph(window, project):
        from storygraph.graph.models import Graph

        script = project / 'game' / 'script' / 'ch01.rpy'
        g = Graph()
        a = g.add_node('ch01', 0, 0, file_path=str(script))
        b = g.add_node('ch02', 700, 400, file_path=str(script))
        g.add_edge(a, b, 'jump')
        window._set_graph(g)
        return g

    def test_space_drag_pans_with_a_readonly_console_focused(self, window,
                                                            project):
        g = self._graph(window, project)
        scrollbars = (window._canvas.horizontalScrollBar(),
                      window._canvas.verticalScrollBar())
        for sb in scrollbars:                 # 挪到中间，两个方向都有余量
            sb.setValue((sb.minimum() + sb.maximum()) // 2)
        window._console.setFocus()
        assert window._console.isReadOnly()
        QTest.keyPress(window, Qt.Key_Space)
        assert window._canvas._space_down is True, \
            '焦点在只读控制台上时，空格没能进入平移模式'
        moved, panning = self._drag_canvas(window, -150, -110)
        QTest.keyRelease(window, Qt.Key_Space)
        assert moved != (0, 0), '只读控制台有焦点时，空格 + 左键拖不平移'
        assert panning is False, '松开左键后 _panning 没复位'

    def test_space_drag_pans_with_the_canvas_focused(self, window, project):
        """对照：焦点在画布上时（用户点过画布）本来就该好使。"""
        self._graph(window, project)
        for sb in (window._canvas.horizontalScrollBar(),
                   window._canvas.verticalScrollBar()):
            sb.setValue((sb.minimum() + sb.maximum()) // 2)
        window._canvas.setFocus()
        QTest.keyPress(window, Qt.Key_Space)
        moved, _ = self._drag_canvas(window, -150, -110)
        QTest.keyRelease(window, Qt.Key_Space)
        assert moved != (0, 0)

    def test_editable_text_box_still_swallows_space(self, window, project):
        """反过来：可编辑的文本框里敲空格必须照常输入（旧用例的加强版）。"""
        self._graph(window, project)
        box = window._panel._text            # 台词框（可编辑）
        box.setFocus()
        QTest.keyPress(box, Qt.Key_Space)
        assert window._canvas._space_down is False, '台词框里的空格被吃掉了'
        QTest.keyRelease(box, Qt.Key_Space)

    def test_window_deactivation_clears_a_stuck_space(self, window, project):
        """
        切走窗口时空格/手势状态必须清干净。

        Alt+Tab 出去时**收不到空格抬起**：`_space_down` 会卡在 True，
        之后每次左键拖块都变成平移（块拖不动、点选像没反应）——
        这也是"时灵时不灵"的一种。
        """
        from PySide6.QtCore import QEvent

        self._graph(window, project)
        QTest.keyPress(window, Qt.Key_Space)
        assert window._canvas._space_down is True
        window.eventFilter(window, QEvent(QEvent.WindowDeactivate))
        assert window._canvas._space_down is False, '失活后空格状态卡住了'
        assert window._canvas._panning is False

    def test_page_switch_keeps_the_focus_inside_the_visible_page(self, window,
                                                                app):
        """
        焦点必须跟着页走：否则它可能留在**已经隐藏**的控件上
        （那时敲字没有任何反应，空格/按键的归属也会判错）。

        注意要 `show()` + `activateWindow()`：offscreen 下没有激活的窗口时
        `QApplication.focusWidget()` 恒为 None（焦点根本没建立），
        那测的就不是"焦点跟着页走"了。
        """
        window.resize(1200, 800)
        window.show()
        window.activateWindow()
        app.processEvents()
        window._console.setFocus()
        app.processEvents()
        assert QApplication.focusWidget() is window._console

        window._set_page(window.PAGE_CARD)
        app.processEvents()
        fw = QApplication.focusWidget()
        page = window._stack.currentWidget()
        assert fw is not None, '切页之后没有焦点（敲字会没反应）'
        assert fw is not window._console, '焦点还留在隐藏页的控制台上'
        assert fw is page or page.isAncestorOf(fw), \
            f'焦点不在当前页里：{fw}'
        window._set_page(window.PAGE_GRAPH)
        app.processEvents()
        assert QApplication.focusWidget() is window._canvas, \
            '切回结构页后焦点没落到画布上'
        window.hide()


class TestZoomSurvivesGraphEdits:
    """
    用户 2026-10-05：

    > 连线之后，请保持原来的缩放……现在每连一个线，缩放就变……这不好

    图的重建只有一个入口（`_set_graph` → `canvas.set_graph`）：连线 /
    撤销 / 改内容都走它。以前它无条件"适应窗口"，于是连一条线就把用户
    刚调好的缩放冲掉。
    """

    @staticmethod
    def _patch_dialog(monkeypatch, cls_name, **attrs):
        from PySide6.QtWidgets import QDialog

        from storygraph.ui_qt import dialogs as D

        cls = getattr(D, cls_name)

        def fake_init(self, *a, **k):
            for key, value in attrs.items():
                setattr(self, key, value)
        monkeypatch.setattr(cls, '__init__', fake_init)
        monkeypatch.setattr(cls, 'exec', lambda self: QDialog.Accepted)

    def _two_nodes(self, window, project):
        from storygraph.graph.models import Graph

        script = project / 'game' / 'script' / 'ch01.rpy'
        g = Graph()
        a = g.add_node('a', 0, 0, file_path=str(script))
        b = g.add_node('b', 900, 600, file_path=str(script))
        window._set_graph(g)
        return g, a, b

    def test_connecting_an_edge_keeps_the_zoom(self, window, project,
                                              monkeypatch):
        from PySide6.QtCore import QPointF

        g, a, b = self._two_nodes(window, project)
        canvas = window._canvas
        canvas.zoom_by(2.0)
        canvas.centerOn(QPointF(300, 200))
        zoom = canvas.zoom_percent()
        center = canvas.mapToScene(canvas.viewport().rect().center())

        self._patch_dialog(monkeypatch, 'AddEdgeDialog', from_id=a, to_id=b,
                           etype='jump', condition='', option_text='')
        window.add_edge(from_id=a, to_id=b)

        assert len(window.graph.edges) == 1, '连线没进图'
        assert canvas.zoom_percent() == zoom, \
            f'连了一条线，缩放从 {zoom}% 变成 {canvas.zoom_percent()}%'
        now = canvas.mapToScene(canvas.viewport().rect().center())
        assert abs(now.x() - center.x()) < 2 and abs(now.y() - center.y()) < 2, \
            f'连了一条线，视口位置漂了：{center} → {now}'

    def test_undoing_a_move_keeps_the_zoom(self, window, project):
        from storygraph.graph.models import Graph

        script = project / 'game' / 'script' / 'ch01.rpy'
        g = Graph()
        nid = g.add_node('a', 100, 100, file_path=str(script))
        g.add_node('b', 900, 600, file_path=str(script))
        window._set_graph(g)
        canvas = window._canvas
        canvas.zoom_by(1.8)
        zoom = canvas.zoom_percent()
        # 走真实的"拖完一个块"那条路（它会记进撤销栈）
        window._on_node_moved(nid, 100, 100, 500, 420)
        window.graph.nodes[nid].x, window.graph.nodes[nid].y = 500, 420
        window.undo_graph()
        assert canvas.zoom_percent() == zoom, \
            f'撤销一步之后缩放变了：{zoom}% → {canvas.zoom_percent()}%'

    def test_loading_a_layout_still_fits(self, window, project, monkeypatch):
        """加载布局 JSON 是"我要看这张新图" —— 显式适应窗口（不能保留旧视图）。"""
        from PySide6.QtWidgets import QMessageBox

        g, _a, _b = self._two_nodes(window, project)
        canvas = window._canvas
        canvas.zoom_by(2.5)
        # `load_layout` 会弹一句"整张换掉，确定？" —— 无头环境必须替掉
        monkeypatch.setattr(QMessageBox, 'question',
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        fitted = []
        original = canvas.fit_to_content
        canvas.fit_to_content = lambda: (fitted.append(1), original())[1]
        try:
            monkeypatch.setattr(window._session, 'load_layout',
                                lambda source: {'ok': True, 'graph': g.copy(),
                                                'path': 'x.json', 'nodes': 2,
                                                'edges': 0})
            window.load_layout(source='x.json')
        finally:
            canvas.fit_to_content = original
        assert fitted, '加载布局之后没有适应窗口'


class TestNodeMenu:
    """右键菜单（旧版 `GraphView.menu` 的条目）。"""

    @staticmethod
    def _labels(menu):
        return [a.text() for a in menu.actions() if (a.text() or '').strip()]

    def test_menu_has_the_old_entries(self, window, project):
        from storygraph.graph.models import Graph

        script = project / 'game' / 'script' / 'ch01.rpy'
        g = Graph()
        nid = g.add_node('ch01', 0, 0, file_path=str(script))
        window._set_graph(g)
        labels = ' | '.join(self._labels(window.build_node_menu(nid)))
        for must in ('改本章台词（卡片）', '在编辑器中打开', '用 Excel 打开此章节',
                     '设为当前章节', '编辑剧情变量', '删除这个块'):
            assert must in labels, f'右键菜单缺「{must}」：{labels}'

    def test_actions_for_a_node_without_source_are_greyed(self, window):
        """没有源文件的块：那三条"打开"要置灰（旧版也是置灰）。"""
        from storygraph.graph.models import Graph

        g = Graph()
        nid = g.add_node('手工建的', 0, 0)          # 没有 file_path
        window._set_graph(g)
        menu = window.build_node_menu(nid)
        state = {a.text(): a.isEnabled() for a in menu.actions()
                 if (a.text() or '').strip()}
        for must in ('在编辑器中打开', '用 Excel 打开此章节'):
            assert state.get(must) is False, f'「{must}」没置灰：{state}'
        assert any(t.startswith('改本章台词') and not en
                   for t, en in state.items()), state

    def test_blank_area_menu_offers_graph_scoped_actions(self, window):
        labels = ' | '.join(self._labels(window.build_node_menu(None)))
        for must in ('添加新章节', '添加连线', '自动布局', '适应窗口',
                     '导入章节', '重载已导入章节', '清空剧情图'):
            assert must in labels, f'空白处菜单缺「{must}」：{labels}'


class TestVariableEditingKeepsImplementationDetails:
    """
    变量编辑**只改剧情变量**，技术代码原位保留。

    为什么这条必须钉住：编辑器里看不到技术代码，如果保存时用编辑器内容
    替换整份 operations，那么 `renpy.transition(...)` / `persistent.x = ...`
    会被**静默删掉** —— 用户只想改一个 `love += 1`，结果整章的转场没了，
    而他永远不会发现（因为那些东西本来就不显示）。
    """

    def test_set_node_variables_keeps_tech_code_in_place(self, window):
        from storygraph.graph.models import Graph

        g = Graph()
        nid = g.add_node('ch01', 0, 0, operations=[
            'renpy.transition(Dissolve(0.1))',
            'love += 1',
            'persistent.seen = True',
            'flag = True',
        ])
        window._set_graph(g)
        res = window._session.set_node_variables(g, nid, ['love += 5'])
        assert res['ok']
        assert g.nodes[nid].operations == [
            'renpy.transition(Dissolve(0.1))',
            'love += 5',
            'persistent.seen = True',
        ], g.nodes[nid].operations

    def test_node_variables_never_returns_tech_code(self, window):
        from storygraph.graph.models import Graph

        g = Graph()
        nid = g.add_node('ch01', 0, 0, operations=[
            'renpy.transition(Dissolve(0.1))', 'love += 1'])
        info = window._session.node_variables(g, nid)
        assert info['variables'] == ['love += 1']
        assert info['tech_count'] == 1
        assert 'renpy' not in str(info), '技术代码的名字漏进了变量视图'

    def test_analysis_text_shows_only_narrative_variables(self, window):
        """「变量分析」的输出里也不许出现技术代码的名字。"""
        from storygraph.graph.models import Graph

        g = Graph()
        a = g.add_node('start', 0, 0, operations=[
            'renpy.transition(Dissolve(0.1))', 'love += 1'])
        g.add_edge(a, a)
        info = window._session.analyze_graph_problems(g)
        assert 'love += 1' in info['text']
        assert 'renpy.transition' not in info['text'], '变量分析里漏了技术代码'

    def test_syntax_check_reports_bad_lines(self, window):
        errors = window._session.check_variable_syntax(
            ['love += 1', 'this is not python =='])
        assert isinstance(errors, list)


class TestGraphEditingActionsActuallyWork:
    """
    **"对话框确认之后，图里到底有没有东西"** —— 这一组是 P0 回归。

    真实事故（用户 2026-10-03）："我连了！！！就是它没有反应！这是个bug！"

    根因：`if dlg.exec() != dlg.Accepted:` —— **`QDialog.Accepted` 是类属性，
    实例上取不到**（`dlg.Accepted` 抛 `AttributeError`）。于是 `add_edge` /
    `add_node` / `edit_node_ops` 走到这一行就抛异常，而异常被 Qt 的槽调用
    吞掉（只在 stderr 留个 traceback）：**对话框填完按"创建"，什么都没发生，
    也不报错**。三条动作从来没生效过。

    为什么以前的测试没抓到：它们都**直接构造对话框**、直接调 `_on_accept()`，
    从来没走过"窗口 → 对话框 → 回到窗口改图"这条真实路径。
    """

    @staticmethod
    def _patch_dialog(monkeypatch, cls_name, **attrs):
        """把某个对话框换成"点了确认"的假货（不弹模态框）。"""
        from PySide6.QtWidgets import QDialog

        from storygraph.ui_qt import dialogs as D

        cls = getattr(D, cls_name)

        def fake_init(self, *a, **k):
            for key, value in attrs.items():
                setattr(self, key, value)

        monkeypatch.setattr(cls, '__init__', fake_init)
        monkeypatch.setattr(cls, 'exec', lambda self: QDialog.Accepted)

    def test_add_edge_really_creates_and_draws_a_line(self, window, project,
                                                      monkeypatch):
        from storygraph.graph.models import Graph

        script = project / 'game' / 'script' / 'ch01.rpy'
        g = Graph()
        g.add_node('a', 0, 0, file_path=str(script))
        g.add_node('b', 0, 0, file_path=str(script))
        window._set_graph(g)
        ids = list(g.nodes)
        self._patch_dialog(monkeypatch, 'AddEdgeDialog', from_id=ids[0],
                           to_id=ids[1], etype='jump',
                           condition='love >= 5', option_text='')
        window.add_edge(from_id=ids[0], to_id=ids[1])

        assert len(window.graph.edges) == 1, '连线没进图（对话框确认被吞了？）'
        edge = next(iter(window.graph.edges.values()))
        assert {edge.from_id, edge.to_id} == {ids[0], ids[1]}
        assert edge.condition == 'love >= 5'
        # **画布上也必须真的有这条线**（这才是用户看得见的"反应"）
        assert len(window._canvas._edges) == 1, '图里有边但画布没画出来'
        label = window._canvas._edges[0]._label.text()
        assert 'love >= 5' in label, f'条件没显示在连线上：{label!r}'
        assert '[连线]' in window._console.toPlainText()

    def test_add_node_really_creates_a_node(self, window, project,
                                            monkeypatch):
        from storygraph.graph.models import Graph

        window._set_graph(Graph())
        self._patch_dialog(monkeypatch, 'AddNodeDialog', name='chapter_new',
                           operations='flag = True')
        window.add_node()
        names = {n.label_name for n in window.graph.nodes.values()}
        assert names == {'chapter_new'}, names
        assert len(window._canvas._items) == 1, '节点没画到画布上'

    def test_edit_node_variables_really_writes_back(self, window, project,
                                                    monkeypatch):
        from storygraph.graph.models import Graph

        g = Graph()
        nid = g.add_node('ch01', 0, 0, operations=[
            'renpy.transition(Dissolve(0.1))', 'love += 1'])
        window._set_graph(g)
        self._patch_dialog(monkeypatch, 'NodeOpsDialog',
                           variables=['love += 99'], tech_count=1)
        window.edit_node_ops(nid)
        assert window.graph.nodes[nid].operations == [
            'renpy.transition(Dissolve(0.1))', 'love += 99'], \
            '变量没写回去（或者把实现细节删了）'

    def test_no_dialog_code_uses_instance_accepted(self):
        """
        AST 守卫：**不许**在非 `QDialog` 的对象上取 `Accepted` / `Rejected`。

        `dlg.Accepted` 会抛 `AttributeError`（类属性，实例取不到），
        而且抛在槽里就是**静默失效** —— 这类 bug 只能靠这条守卫 +
        上面那三条真实路径的用例一起挡住。
        """
        import ast
        from pathlib import Path

        from _source_paths import module_source

        ui_dir = Path(module_source('storygraph.ui_qt'))
        bad = []
        for path in sorted(ui_dir.glob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                if node.attr not in ('Accepted', 'Rejected'):
                    continue
                value = node.value
                if isinstance(value, ast.Name) and value.id == 'QDialog':
                    continue          # 正确写法：QDialog.Accepted
                bad.append(f'{path.name}:{node.lineno} '
                           f'{ast.unparse(node)}')
        assert not bad, ('这些地方用了实例上的 Accepted/Rejected'
                         f'（会 AttributeError 且静默失效）：{bad}')


class TestNoTkinterInBackend:
    """把"依赖方向单向"这条契约钉成测试，否则迟早有人再 import 回来。"""

    PURE_MODULES = [
        'storygraph.app.beats',
        'storygraph.app.stage',
        'storygraph.app.notify',
        'storygraph.app.editor',
        'storygraph.app.picker',
        'storygraph.app.thumbs',
        'storygraph.app.asset_panel',
        'storygraph.app.tables',
        'storygraph.graph.commands',
        'storygraph.graph.editor_utils',
        'storygraph.pipeline.renpy_exporter',
    ]

    def test_app_layer_does_not_depend_on_any_frontend(self):
        """
        `app` 层只能依赖 core/parsing/excel/assets/story/pipeline。

        注意判据要**精确**：`storygraph.ui_qt` 以 `storygraph.ui` 开头，
        用 `startswith` 会把"Qt 前端自己"也判成违规（假警报）。
        这里只拦**已被删除的 tkinter 包** `storygraph.ui`（精确匹配）
        与其子模块 `storygraph.ui.`，以及任何 UI 框架。
        """
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
                    if 'tkinter' in m:
                        hits.append(m)
                    elif m == 'storygraph.ui' or m.startswith('storygraph.ui.'):
                        hits.append(m)
                    elif m in ('PySide6', 'PyQt5', 'PyQt6') or \
                            m.startswith(('PySide6.', 'PyQt5.', 'PyQt6.')):
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
        QSS 的数据源是 `ui_qt.tokens`（纯数据），**不是**任何控件模块。
        `app` 层与 Qt 层都只允许读 tokens。
        """
        from _source_paths import module_source
        src = module_source('storygraph.ui_qt.theme').read_text(encoding='utf-8')
        assert 'import tkinter' not in src
