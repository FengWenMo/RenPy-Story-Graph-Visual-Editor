"""
tests/test_ui_fit_and_flow.py
=============================
2026-10-23 用户报的三件事，逐条钉住：

  ① **界面太宽 —— 全屏都看不全，文字跑到界面外**
     根因有三个，都在这里有用例：
       · 窗口按 `sizeHint()`（2720px，工具栏整宽）打开 → `fit_to_available`；
       · 面包屑右边那句是普通 QLabel，最小宽度 = 整句宽度 → `ElidedLabel`；
       · Qt 的「放不下」按钮（`qt_toolbar_ext_button`）在本项目 QSS 下
         是**一条看不见的空白**（12px、无字）→ 给它「⋯」。

  ② **控制流卡的下拉"缺失"**
     实测：`insert_flow_card` 只拿 `session.label_names()`（当前文件）。
     真机 `chapter0_0.rpy` 里只有 1 个 label → 下拉里只有它自己。
     修法是 `_flow_target_labels()`：本文件 + 图节点 + **项目目录**。

  ③ **控制流卡的 UI 不对**
     修了两处：插入对话框补「参数 / from」并默认不预选目标；
     控制流卡收起台词区与舞台块（`call side` 没有台词与演出）。

不测截图：判据都在可观察状态上（控件可见性 / 宽度下限 / 候选清单）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
pytest.importorskip('PySide6')

from PySide6.QtCore import QRect                                    # noqa: E402
from PySide6.QtWidgets import QApplication                          # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project                               # noqa: E402

from storygraph.ui_qt.elide import ElidedLabel                      # noqa: E402
from storygraph.ui_qt.window_state import fit_to_available          # noqa: E402

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


@pytest.fixture(scope='session', autouse=True)
def app():
    """**autouse**：没有 QApplication 时建 QWidget 是直接 abort（不是抛异常）。"""
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
    win = MainWindowQt(_Engine(project), project / 'game' / 'script' / 'flow.rpy')
    yield win
    win.close()


# ==================================================================
#  ① 宽度：窗口 / 省略标签 / 「⋯」
# ==================================================================

class TestFitToAvailable:
    def test_a_window_bigger_than_the_screen_is_shrunk(self):
        w, h = fit_to_available(2720, 1500, QRect(0, 0, 1600, 900))
        assert (w, h) == (1600 - 48, 900 - 48)

    def test_a_window_that_fits_is_left_alone(self):
        assert fit_to_available(1200, 800, QRect(0, 0, 1600, 900)) == (1200, 800)

    def test_it_never_returns_a_negative_size(self):
        w, h = fit_to_available(10, 10, QRect(0, 0, 1600, 900))
        assert (w, h) == (10, 10)


class TestElidedLabel:
    def test_minimum_width_is_not_the_whole_text(self):
        """QLabel 的最小宽度是整句宽 —— 那正是"按钮被挤出窗口"的原因。"""
        label = ElidedLabel('很长很长的一句话' * 20)
        assert label.minimumSizeHint().width() <= 32

    def test_it_elides_when_squeezed(self, app):
        label = ElidedLabel('很长很长的一句话' * 20)
        label.resize(120, 20)
        label.show()
        app.processEvents()
        assert label.full_text().startswith('很长')
        assert label.text().endswith('…')
        assert label.toolTip() == label.full_text()

    def test_before_layout_it_shows_the_full_text(self):
        """还没量宽时不许先画 `…`（测试会读到一串省略号，用户看到闪一下）。"""
        label = ElidedLabel('一句不算长的话')
        assert label.text() == '一句不算长的话'


class TestWindowWidth:
    def test_the_window_is_never_wider_than_the_screen(self, window):
        screen = QApplication.primaryScreen()
        assert window.width() <= screen.availableGeometry().width()

    def test_the_crumb_hint_can_be_squeezed(self, window):
        window._set_page(window.PAGE_CARD)
        assert window._crumb_hint.minimumSizeHint().width() <= 32
        assert '/' in window._crumb_hint.full_text()

    def test_the_card_button_row_fits(self, window, app):
        window._set_page(window.PAGE_CARD)
        window.resize(1366, 820)
        window.show()
        app.processEvents()
        row = window._back_btn.parentWidget()
        assert row.width() >= 1000, f'这一行没拿到宽度（{row.width()}），量不到'
        assert row.minimumSizeHint().width() <= row.width(), (
            f'卡片页那条按钮行的最小宽度 {row.minimumSizeHint().width()} '
            f'大于可用 {row.width()} —— 又会有按钮被裁在窗口外')


# ==================================================================
#  ② 控制流卡的目标候选
# ==================================================================

class TestFlowTargetLabels:
    def test_it_lists_labels_from_the_whole_project(self, window):
        labels = window._flow_target_labels()
        # 本文件（flow.rpy）的
        for name in ('main', 'side', 'ending'):
            assert name in labels, f'{name} 不在目标候选里：{labels}'
        # 同一个项目、别的文件（ch01.rpy）的
        for name in ('ch01', 'ch01_roof', 'ch01_class', 'ch01_end'):
            assert name in labels, f'{name}（另一个文件）不在候选里：{labels}'

    def test_the_current_chapter_comes_first(self, window):
        labels = window._flow_target_labels()
        assert labels[0] == 'main'

    def test_no_duplicates(self, window):
        labels = window._flow_target_labels()
        assert len(labels) == len(set(labels))


class TestFlowDialog:
    def test_it_starts_with_no_target_selected(self, app):
        """默认选中第一项 = 手快一点就插了一张 `call <这一章自己>`。"""
        from storygraph.ui_qt.dialogs import FlowCardDialog
        dlg = FlowCardDialog(None, labels=['side', 'ending'])
        assert dlg._target.currentIndex() == -1
        assert dlg._target.currentText() == ''

    def test_it_writes_args_and_from(self, app):
        from PySide6.QtWidgets import QDialog
        from storygraph.ui_qt.dialogs import FlowCardDialog
        dlg = FlowCardDialog(None, labels=['side'])
        dlg._target.setCurrentText('side')
        dlg._args.setText('(1, flag)')
        dlg._from.setText('_call_side_1')
        dlg._on_accept()
        assert dlg.result() == QDialog.Accepted
        assert (dlg.flow_type, dlg.target) == ('call', 'side')
        assert (dlg.args, dlg.from_clause) == ('(1, flag)', '_call_side_1')

    def test_unbalanced_args_are_refused_here(self, app):
        """不能等到导出才炸：`call side(a` 会让整份脚本解析不了。"""
        from PySide6.QtWidgets import QDialog
        from storygraph.ui_qt.dialogs import FlowCardDialog
        dlg = FlowCardDialog(None, labels=['side'])
        dlg._target.setCurrentText('side')
        dlg._args.setText('(a')
        dlg._on_accept()
        assert dlg.result() != QDialog.Accepted
        assert dlg.target == '', '校验失败却把目标记下来了'
        # 对话框没 show 时 `isVisible()` 全是 False，判"错误行真的写上了"
        assert '配平' in dlg._error.text()

    def test_jump_disables_args_and_from(self, app):
        from storygraph.ui_qt.dialogs import FlowCardDialog
        dlg = FlowCardDialog(None, labels=['side'])
        dlg._type.setCurrentIndex(1)          # jump
        assert dlg._type.currentData() == 'jump'
        assert not dlg._args.isEnabled()
        assert not dlg._from.isEnabled()


class TestInsertFlowBeatValidation:
    def test_the_session_refuses_unbalanced_args(self, window):
        window._set_page(window.PAGE_CARD)
        res = window._session.insert_flow_beat(0, args='(a')
        assert res['ok'] is False
        assert '不合法' in res.get('error', '')

    def test_good_args_are_written_as_one_line(self, window):
        window._set_page(window.PAGE_CARD)
        res = window._session.insert_flow_beat(
            0, target='side', args='(1)', from_clause='_c1')
        assert res['ok'] is True
        beat = window._session.beats[res['index']]
        assert beat.raw == 'call side(1) from _c1'


# ==================================================================
#  ③ 控制流卡不该显示台词 / 舞台
# ==================================================================

class TestFlowCardHidesDialogue:
    def _beat_index(self, window, raw):
        return next(i for i, b in enumerate(window._session.beats)
                    if getattr(b, 'raw', '') == raw)

    def _say_index(self, window, text):
        """台词拍的 `raw` 是空的（只有保真行才有 raw）—— 按正文找。"""
        return next(i for i, b in enumerate(window._session.beats)
                    if str(getattr(b, 'text', '') or '') == text)

    def test_dialogue_and_stage_are_hidden_for_a_call_card(self, window, app):
        window._set_page(window.PAGE_CARD)
        window._show_index(self._beat_index(window, 'call side'))
        app.processEvents()
        panel = window._panel
        assert not panel._flow_bar.isHidden()
        assert panel._dialogue_box.isHidden(), '控制流卡还显示着说话人/台词区'
        assert panel._stage_box.isHidden()
        assert panel._stage_empty.isHidden()
        assert panel._stage_toggle.isHidden()

    def test_they_come_back_on_the_next_normal_beat(self, window, app):
        window._set_page(window.PAGE_CARD)
        window._show_index(self._beat_index(window, 'call side'))
        app.processEvents()
        window._show_index(self._say_index(window, '调用之后'))
        app.processEvents()
        panel = window._panel
        assert panel._flow_bar.isHidden()
        assert not panel._dialogue_box.isHidden(), '回到普通卡后台词区没回来'

    def test_the_flow_strip_explains_where_the_fields_went(self, window, app):
        window._set_page(window.PAGE_CARD)
        window._show_index(self._beat_index(window, 'call side'))
        app.processEvents()
        assert '没有台词与演出' in window._panel._flow_detail.text()
