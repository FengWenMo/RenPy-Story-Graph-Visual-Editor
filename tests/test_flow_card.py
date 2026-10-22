"""
tests/test_flow_card.py
=======================
P0-4 第一阶段：**大 label 里的 `call` / `jump` 要看得见、改得动、导出正确**。

这一阶段不把 Beat 升级成 `kind='flow'`（那是第二阶段），只做：

  · `story.flow` 从 `raw` 里认出控制流，并能只改目标 / 类型；
  · 卡片顶部出现「↳ 调用 side / 返回后继续第 N 拍」的控制流条；
  · 保存后 `Beat.raw` 变成新的 `call/jump`，导出仍是同一行；
  · `call` 改成 `jump` 时必须明确确认"不会返回"。

判据都看数据落点（`Beat.raw` / `rendered_lines_of` / 控件值），不看截图。
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
pytest.importorskip('PySide6')

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project  # noqa: E402

FLOW_SCRIPT = '''label main:
    e "开始"
    call side
    e "调用之后"
    jump ending

label side:
    e "支线"
    return

label ending:
    return
'''


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
    root = build_project(tmp_path / 'proj')
    (root / 'game' / 'script' / 'flow.rpy').write_text(
        FLOW_SCRIPT, encoding='utf-8')
    return root


@pytest.fixture
def window(app, project):
    from storygraph.ui_qt.main_window import MainWindowQt
    script = project / 'game' / 'script' / 'flow.rpy'
    win = MainWindowQt(_Engine(project), script)
    yield win
    win.close()


def _beat_of(window, raw):
    return next(b for b in window.beats if b.raw == raw)


# ======================================================================
#  一、纯数据层：解析 / 改写
# ======================================================================

class TestParseFlowLine:
    @pytest.mark.parametrize('line,expected', [
        ('call side', ('call', 'side', '', '')),
        ('jump main.route', ('jump', 'main.route', '', '')),
        ('call side(1, two=True)', ('call', 'side', '(1, two=True)', '')),
        ('call side from _call_side',
         ('call', 'side', '', '_call_side')),
        ('  call side  ', ('call', 'side', '', '')),
    ])
    def test_parses_static_flow(self, line, expected):
        from storygraph.story.flow import parse_flow_line
        info = parse_flow_line(line)
        assert info is not None
        assert (info['type'], info['target'], info['args'],
                info['from_clause']) == expected

    @pytest.mark.parametrize('line', [
        'call screen map',
        'jump expression target',
        'call side garbage',
        'jump side(1)',
        'e "call side"',
    ])
    def test_refuses_what_it_does_not_understand(self, line):
        from storygraph.story.flow import parse_flow_line
        assert parse_flow_line(line) is None

    def test_parse_flow_only_accepts_a_pure_flow_beat(self):
        from storygraph.story.beat_model import Beat
        from storygraph.story.flow import parse_flow
        b = Beat(kind='narration', raw='call side')
        assert parse_flow(b) is not None
        b.text = '还带一句台词'
        assert parse_flow(b) is None
        b = Beat(kind='say', raw='call side')
        assert parse_flow(b) is None
        b = Beat(kind='narration', raw='call side\nplay music x')
        assert parse_flow(b) is None

    def test_update_preserves_args_and_from(self):
        from storygraph.story.beat_model import Beat
        from storygraph.story.flow import update_beat_flow
        b = Beat(kind='narration', raw='call side(1) from _call_side')
        res = update_beat_flow(b, target='other')
        assert res['ok'] and res['changed']
        assert b.raw == 'call other(1) from _call_side'

    def test_update_drops_args_and_from_when_switching_to_jump(self):
        from storygraph.story.beat_model import Beat
        from storygraph.story.flow import update_beat_flow
        b = Beat(kind='narration', raw='call side(1) from _call_side')
        res = update_beat_flow(b, flow_type='jump')
        assert res['ok']
        assert b.raw == 'jump side'

    def test_invalid_target_does_not_touch_the_beat(self):
        from storygraph.story.beat_model import Beat
        from storygraph.story.flow import update_beat_flow
        b = Beat(kind='narration', raw='call side')
        res = update_beat_flow(b, target='bad name')
        assert not res['ok']
        assert b.raw == 'call side'


class TestFlowInfo:
    def test_call_says_where_it_returns(self):
        from storygraph.story.beat_model import Beat
        from storygraph.story.flow import flow_info
        beats = [Beat(kind='say', label='main', text='a'),
                 Beat(kind='narration', label='main', raw='call side'),
                 Beat(kind='say', label='main', text='b'),
                 Beat(kind='say', label='side', text='s')]
        info = flow_info(beats[1], 1, beats)
        assert info['target'] == 'side'
        assert info['return_index'] == 2
        assert '继续第 3 拍' in info['return_note']

    def test_call_at_label_end_does_not_point_at_next_label(self):
        from storygraph.story.beat_model import Beat
        from storygraph.story.flow import flow_info
        beats = [Beat(kind='narration', label='main', raw='call side'),
                 Beat(kind='say', label='side', text='s')]
        info = flow_info(beats[0], 0, beats)
        assert info['return_index'] is None
        assert '本章末尾' in info['return_note']

    def test_jump_says_it_does_not_return(self):
        from storygraph.story.beat_model import Beat
        from storygraph.story.flow import flow_info
        info = flow_info(Beat(kind='narration', raw='jump ending'), 0, [])
        assert '不返回' in info['return_note']
        assert info['return_index'] is None


# ======================================================================
#  二、会话层：保存后 raw 真的变了
# ======================================================================

class TestSessionApply:
    def test_apply_flow_target_rewrites_raw(self, project):
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=project)
        s.load(project / 'game' / 'script' / 'flow.rpy')
        idx = next(i for i, b in enumerate(s.beats) if b.raw == 'call side')

        s.apply(idx, {'FlowType': 'call', 'FlowTarget': 'ending'})

        assert s.beats[idx].raw == 'call ending'
        assert s.beats[idx].code == ''

    def test_apply_jump_rewrites_raw(self, project):
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=project)
        s.load(project / 'game' / 'script' / 'flow.rpy')
        idx = next(i for i, b in enumerate(s.beats) if b.raw == 'jump ending')

        s.apply(idx, {'FlowType': 'jump', 'FlowTarget': 'side'})

        assert s.beats[idx].raw == 'jump side'

    def test_change_count_tracks_raw_only(self, project):
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=project)
        s.load(project / 'game' / 'script' / 'flow.rpy')
        idx = next(i for i, b in enumerate(s.beats) if b.raw == 'call side')

        res = s.save(idx, {'FlowType': 'call', 'FlowTarget': 'ending'})

        assert res['changed']
        assert s.beats[idx].raw == 'call ending'
        assert s.rendered_lines_of(s.beats[idx]) == ['call ending']


# ======================================================================
#  三、卡片：看得见、改得动、保存后导出正确
# ======================================================================

class TestFlowCard:
    def _call_index(self, window):
        return next(i for i, b in enumerate(window.beats)
                    if b.raw == 'call side')

    def test_call_beat_shows_flow_bar_not_loose_line(self, window):
        idx = self._call_index(window)
        window._show_index(idx)

        assert not window._panel._flow_bar.isHidden()
        assert '调用' in window._panel._flow_title.text()
        assert 'side' in window._panel._flow_title.text()
        assert '继续第 3 拍' in window._panel._flow_detail.text()
        assert window._panel._loose_label.isHidden()
        assert window._panel._loose_label.text() == ''

    def test_jump_beat_says_it_does_not_return(self, window):
        idx = next(i for i, b in enumerate(window.beats)
                   if b.raw == 'jump ending')
        window._show_index(idx)

        assert window._panel._flow_type.currentData() == 'jump'
        assert '不返回' in window._panel._flow_detail.text()

    def test_flow_bar_is_hidden_for_a_normal_beat(self, window):
        window._show_index(0)

        assert window._panel._flow_bar.isHidden()
        assert window._panel.flow_fields() == {}

    def test_saving_target_rewrites_raw_and_export_line(self, window):
        idx = self._call_index(window)
        window._show_index(idx)
        window._panel._flow_target.setCurrentText('ending')

        window.save_current_beat()

        beat = window.beats[idx]
        assert beat.raw == 'call ending'
        assert beat.code == ''
        assert window._session.rendered_lines_of(beat) == ['call ending']
        fields = window._panel.flow_fields()
        # 第二阶段多了参数 / from 两格；只钉"这几项的值"，不钉键的总数
        # （否则以后再加一格就红，而那条红不说明任何行为退化）。
        assert fields['FlowType'] == 'call'
        assert fields['FlowTarget'] == 'ending'
        assert fields['FlowArgs'] == ''
        assert fields['FlowFrom'] == ''
        # 保存之后不应再提示"有未保存改动"
        assert not window._panel.is_modified()

    def test_invalid_target_is_refused_without_saving(self, window):
        idx = self._call_index(window)
        window._show_index(idx)
        window._panel._flow_target.setCurrentText('bad name')

        res = window.save_current_beat()

        assert res is None
        assert window.beats[idx].raw == 'call side'
        assert '不合法' in window._status_bar.currentMessage()

    def test_empty_target_does_not_destroy_raw_on_navigation(self, window):
        idx = self._call_index(window)
        window._show_index(idx)
        window._panel._flow_target.setCurrentText('')

        window.next_beat()

        assert window.beats[idx].raw == 'call side'
        assert window.beats[idx].code == ''

    def test_switching_call_to_jump_requires_confirmation(self, window,
                                                          monkeypatch):
        idx = self._call_index(window)
        window._show_index(idx)
        window._panel._flow_type.setCurrentIndex(
            window._panel._flow_type.findData('jump'))
        asked = []
        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.No))

        res = window.save_current_beat()

        assert asked, 'call→jump 没有问一声'
        assert res is None
        assert window.beats[idx].raw == 'call side'

    def test_switching_call_to_jump_after_confirm(self, window, monkeypatch):
        idx = self._call_index(window)
        window._show_index(idx)
        window._panel._flow_type.setCurrentIndex(
            window._panel._flow_type.findData('jump'))
        monkeypatch.setattr(
            QMessageBox, 'question',
            staticmethod(lambda *a, **k: QMessageBox.Yes))

        window.save_current_beat()

        assert window.beats[idx].raw == 'jump side'

    def test_saving_target_retargets_graph_edge(self, window, project):
        script = project / 'game' / 'script' / 'flow.rpy'
        window.import_chapter_files([script])
        idx = self._call_index(window)
        window._show_index(idx)
        window._panel._flow_target.setCurrentText('ending')

        window.save_current_beat()

        names = {n.id: n.label_name for n in window.graph.nodes.values()}
        call_edges = [(names[e.from_id], names[e.to_id]) for e in
                      window.graph.edges.values() if e.type == 'call']
        assert ('main', 'ending') in call_edges
        assert ('main', 'side') not in call_edges

    def test_compiled_export_contains_the_new_target(self, window):
        idx = self._call_index(window)
        window._show_index(idx)
        window._panel._flow_target.setCurrentText('ending')

        window.save_current_beat()
        compiled = window._session.compile()

        assert 'call ending' in compiled.rpy_text
        assert 'call side' not in compiled.rpy_text

    def test_saving_with_advanced_area_open_keeps_raw(self, window):
        idx = self._call_index(window)
        window._show_index(idx)
        if not window._panel.is_advanced_open():
            window._panel._toggle_adv()
        window._panel._flow_target.setCurrentText('ending')

        window.save_current_beat()

        beat = window.beats[idx]
        assert beat.raw == 'call ending'
        assert beat.code == ''
        assert not window._panel.is_modified()

    def test_navigation_commit_also_retargets_the_edge(self, window, project):
        script = project / 'game' / 'script' / 'flow.rpy'
        window.import_chapter_files([script])
        idx = self._call_index(window)
        window._show_index(idx)
        window._panel._flow_target.setCurrentText('ending')

        window.next_beat()

        names = {n.id: n.label_name for n in window.graph.nodes.values()}
        call_edges = [(names[e.from_id], names[e.to_id]) for e in
                      window.graph.edges.values() if e.type == 'call']
        assert ('main', 'ending') in call_edges

    def test_list_row_shows_flow_instead_of_no_dialogue(self, window):
        idx = self._call_index(window)
        text = window._row_text(window._session.list_entry(idx))
        assert '调用' in text and 'side' in text
        assert '无台词' not in text


# ======================================================================
#  四、插入控制流卡：大 label 里 call 小 label 再回来
# ======================================================================

class TestInsertFlowCard:
    def test_session_inserts_a_flow_beat_after_current(self, project):
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=project)
        s.load(project / 'game' / 'script' / 'flow.rpy')
        idx = next(i for i, b in enumerate(s.beats) if b.raw == 'call side')

        res = s.insert_flow_beat(idx, after=True, flow_type='call',
                                 target='ending')

        assert res['ok']
        beat = s.beats[res['index']]
        assert beat.label == 'main'
        assert beat.raw == 'call ending'
        assert s.rendered_lines_of(beat) == ['call ending']

    def test_dialog_accepts_a_target_and_refuses_an_invalid_one(
            self, qapp):
        from storygraph.ui_qt.dialogs import FlowCardDialog
        dlg = FlowCardDialog(None, labels=['side', 'ending'])
        dlg._target.setCurrentText('side')
        dlg._on_accept()
        assert dlg.result() == QDialog.Accepted
        assert (dlg.flow_type, dlg.target) == ('call', 'side')

        bad = FlowCardDialog(None, labels=['side'])
        bad._target.setCurrentText('bad name')
        bad._on_accept()
        assert bad.result() != QDialog.Accepted
        assert bad.target == ''

    def test_ui_inserts_and_selects_the_new_flow_card(self, window,
                                                      monkeypatch):
        import storygraph.ui_qt.dialogs as dialogs
        idx = next(i for i, b in enumerate(window.beats)
                   if b.raw == 'call side')
        window._show_index(idx)
        window._set_page(window.PAGE_CARD)

        class _FakeDialog:
            flow_type = 'call'
            target = 'ending'

            def __init__(self, *a, **k):
                pass

            def exec(self):
                return QDialog.Accepted

        monkeypatch.setattr(dialogs, 'FlowCardDialog', _FakeDialog)

        window.insert_flow_card()

        assert window.current_page() == window.PAGE_CARD
        beat = window.beats[window._loaded_idx]
        assert beat.raw == 'call ending'
        assert window._panel._flow_bar.isHidden() is False

    def test_ui_insertion_adds_the_graph_edge(self, window, project,
                                              monkeypatch):
        import storygraph.ui_qt.dialogs as dialogs
        script = project / 'game' / 'script' / 'flow.rpy'
        window.import_chapter_files([script])
        idx = next(i for i, b in enumerate(window.beats)
                   if b.raw == 'call side')
        window._show_index(idx)

        class _FakeDialog:
            flow_type = 'call'
            target = 'ending'

            def __init__(self, *a, **k):
                pass

            def exec(self):
                return QDialog.Accepted

        monkeypatch.setattr(dialogs, 'FlowCardDialog', _FakeDialog)

        window.insert_flow_card()

        names = {n.id: n.label_name for n in window.graph.nodes.values()}
        assert any(names[e.from_id] == 'main'
                   and names[e.to_id] == 'ending'
                   and e.type == 'call'
                   for e in window.graph.edges.values())
