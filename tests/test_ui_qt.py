"""
tests/test_ui_qt.py
===================
Qt（PySide6）前端的测试。**offscreen 跑真 Qt**（`QT_QPA_PLATFORM=offscreen`）——
比 tkinter 桩更接近真实（真事件、真布局、真字体）。

锁的东西：
  · `cards_to_show` 与 tkinter 版 `_collect_sprites` **同一条语义**
    （按出场顺序 / extras 不丢 / 多属性立绘不被压成一个属性 / 位置只有统一才整拍）
  · 键盘契约：回车 = 下一拍；多行台词里 ↓/↑ 要到边才翻页
  · 真实章节冒烟：载入、翻页、保存、写回 show
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

pytest.importorskip('PySide6')
from PySide6.QtGui import QTextCursor        # noqa: E402
from PySide6.QtWidgets import QApplication   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REAL = Path(r'D:\千连万理\RenPyTest\sr_general')


@pytest.fixture(scope='session')
def app():
    a = QApplication.instance() or QApplication([])
    yield a


# ==================================================================
#  一、卡片 → show 文本（与 tkinter 版同一条语义）
# ==================================================================

class TestCardsToShow:
    def test_order_index_is_preserved(self):
        from storygraph.ui_qt.sprite_grid import cards_to_show
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
        from storygraph.ui_qt.sprite_grid import cards_to_show
        cards = [{'image': 'ling base_arms_akimbo sigh',
                  'expression': 'base_arms_akimbo 无语', 'position': '',
                  'at': '', 'order_index': 0, 'extra_rows': []}]
        out = cards_to_show(cards)
        assert 'base_arms_akimbo' in out
        assert 'ling ' in out
        assert len(out.split()) >= 2, out

    def test_extras_are_not_lost(self):
        """同一角色的其余条目（换表情保留的）必须带回。"""
        from storygraph.ui_qt.sprite_grid import cards_to_show
        cards = [{'image': 'ling close_happy', 'expression': 'close_happy',
                  'position': '', 'at': '', 'order_index': 0,
                  'extra_rows': [{'image': 'ling base_arms_akimbo close_happy'}]}]
        out = cards_to_show(cards)
        assert 'ling base_arms_akimbo close_happy' in out

    def test_position_joined_with_at(self):
        from storygraph.ui_qt.sprite_grid import cards_to_show
        cards = [{'image': 'ling happy', 'expression': '', 'position': 'left',
                  'at': '', 'order_index': 0, 'extra_rows': []}]
        assert cards_to_show(cards) == 'ling happy@left'

    def test_uniform_position_only_when_all_same(self):
        from storygraph.ui_qt.sprite_grid import uniform_position_of
        same = [{'position': 'i11', 'at': ''}, {'position': 'i11', 'at': ''}]
        diff = [{'position': 'i11', 'at': ''}, {'position': '', 'at': ''}]
        assert uniform_position_of(same) == 'i11'
        assert uniform_position_of(diff) == '', \
            '位置不统一时必须留空，否则会兜底套到别的立绘头上'


# ==================================================================
#  二、键盘契约（纯逻辑，不开窗）
# ==================================================================

class TestCaretAtEdge:
    def _edit(self, app, text):
        from PySide6.QtGui import QTextCursor  # noqa: F401
        from PySide6.QtWidgets import QPlainTextEdit
        w = QPlainTextEdit()
        w.setPlainText(text)
        return w

    def test_single_line_is_always_at_edge(self, app):
        from storygraph.ui_qt.main_window import ScriptTableEditorWindowQt
        w = self._edit(app, '一行')
        w.moveCursor(QTextCursor.MoveOperation.Start)
        assert ScriptTableEditorWindowQt._caret_at_edge(w, True)
        assert ScriptTableEditorWindowQt._caret_at_edge(w, False)

    def test_multiline_middle_is_not_at_edge(self, app):
        from storygraph.ui_qt.main_window import ScriptTableEditorWindowQt
        w = self._edit(app, '第一行\n第二行\n第三行')
        w.moveCursor(QTextCursor.MoveOperation.Start)
        assert not ScriptTableEditorWindowQt._caret_at_edge(w, True)
        w.moveCursor(QTextCursor.MoveOperation.End)
        assert ScriptTableEditorWindowQt._caret_at_edge(w, True)


# ==================================================================
#  三、真实章节冒烟（跳过本机没有的项目）
# ==================================================================

@pytest.mark.skipif(not (REAL / 'game' / 'chapter' / 'chapter1_3.rpy').exists(),
                    reason='本机没有真实章节')
class TestQtEditorOnRealChapter:
    def _win(self, app):
        from storygraph.main_qt import _EngineShim
        from storygraph.ui_qt.main_window import ScriptTableEditorWindowQt
        engine = _EngineShim(REAL)
        win = ScriptTableEditorWindowQt(
            engine, REAL / 'game' / 'chapter' / 'chapter1_3.rpy')
        win.load_script()
        return win

    def test_loads_305_beats(self, app):
        win = self._win(app)
        assert len(win.beats) == 305
        win.close()

    def test_navigation_is_pure(self, app):
        """翻页是纯导航：别的拍一个字符都不许动。"""
        win = self._win(app)
        snap = [(b.speaker, b.text, b.kind, b.show) for b in win.beats]
        win._show_index(0)
        win.next_beat()
        win.next_beat()
        after = [(b.speaker, b.text, b.kind, b.show) for b in win.beats]
        assert snap == after, '翻页改了别的拍的数据'
        win.close()

    def test_save_current_beat_writes_back(self, app):
        win = self._win(app)
        win._show_index(42)
        win._panel._speaker.setCurrentText('jc')
        win._panel._text.setPlainText('保存测试')
        win.save_current_beat()
        assert win.beats[42].speaker == 'jc'
        assert win.beats[42].text == '保存测试'
        assert '已保存到这一拍' in win.statusBar().currentMessage()
        win.close()

    def test_sprite_card_shows_real_image_spec(self, app):
        win = self._win(app)
        win._show_index(42)
        cards = win._panel._grid.cards()
        assert cards and cards[0]['image'] == 'ling oh'
        win.close()
