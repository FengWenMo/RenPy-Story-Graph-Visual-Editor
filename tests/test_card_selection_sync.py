"""
tests/test_card_selection_sync.py
================================
「列表高亮」与「卡片显示」必须同步 —— 而且是**数据安全**问题，不只是显示。

背景（用户 2026-09-28 截图暴露）：
列表高亮第 50 行，卡片却显示第 49 拍的内容。当时表现为"说话人丢了"，
真正危险的是：提交时**从列表读 index 往回写**，两者错开时会把
**卡片里那一拍的内容写进列表选中的另一拍** —— 改错数据。

修法：`self._loaded_idx` 是"卡片现在显示哪一拍"的唯一真源；
载入一律走 `_show_index`，回写一律用 `_loaded_idx`。

2026-10-01：tkinter 的 `ScriptTableEditorWindow` 已删除，这套纪律整体
搬到了 Qt 的 `MainWindowQt`（同一批方法名：`_show_index` / `_on_select` /
`apply_current_beat` / `next_beat` / `prev_beat`）。本文件改为**跑真 Qt 窗口**
（offscreen），工程由夹具现场生成，不再用假 Listbox / 假 Panel 桩 ——
桩最容易"比真控件聪明"，把真正的错位藏起来。
"""

import os
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication                      # noqa: E402

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project                            # noqa: E402


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
def win(app, tmp_path):
    """载入现场生成的工程（4 label / 11 拍）的 Qt 主窗口。"""
    project = build_project(tmp_path / 'proj')
    from storygraph.ui_qt.main_window import MainWindowQt
    w = MainWindowQt(_Engine(project),
                     project / 'game' / 'script' / 'ch01.rpy')
    yield w
    w.close()


def _texts(w):
    """当前会话里每一拍的台词（用来断言"哪一拍被改了"）。"""
    return [b.text for b in w.beats]


# ==================================================================
#  一、_loaded_idx 是唯一真源
# ==================================================================

class TestLoadedIndexIsSourceOfTruth:
    def test_show_index_loads_that_beat_and_highlights_it(self, win):
        win._show_index(3)
        assert win._loaded_idx == 3
        assert win._panel.text() == win.beats[3].text
        assert win._list.currentRow() == 3, '列表高亮没跟着走'

    def test_out_of_range_is_ignored(self, win):
        win._show_index(99)
        assert win._loaded_idx == -1, '越界不该改状态'

    def test_on_select_follows_user_click(self, win):
        """用户点列表 → 载入那一拍。"""
        win._list.setCurrentRow(2)          # 触发 currentRowChanged → _on_select
        assert win._loaded_idx == 2

    def test_loading_a_beat_fills_the_form_from_that_beat(self, win):
        """列表第 0 行与卡片内容必须来自**同一拍**。"""
        win._show_index(0)
        first = win._panel.text()
        win._show_index(1)
        assert win._panel.text() == win.beats[1].text
        assert win._panel.text() != first or win.beats[0].text == win.beats[1].text


# ==================================================================
#  二、回写用 _loaded_idx，不用列表高亮
# ==================================================================

class TestWriteBackUsesLoadedIndexNotListHighlight:
    """
    **这条是数据安全的核心回归**：即使列表高亮和卡片显示错开，
    回写也必须落在**卡片显示的那一拍**。
    """

    def test_writes_to_loaded_beat_when_list_differs(self, win):
        win._show_index(1)                  # 卡片显示第 2 拍
        win._panel.set_text('改过的')
        # 让列表高亮跑到第 5 行（模拟错开）—— 回写**不许**听它的
        win._list.blockSignals(True)
        win._list.setCurrentRow(4)
        win._list.blockSignals(False)

        win.apply_current_beat()
        assert win.beats[1].text == '改过的', '没写进卡片显示的那一拍'
        assert win.beats[4].text != '改过的', \
            '写错行了！列表高亮的另一拍被覆盖 —— 这正是修之前的 bug'

    def test_next_beat_writes_before_advancing(self, win):
        win._show_index(0)
        win._panel.set_text('改过的')
        win.next_beat()
        assert win.beats[0].text == '改过的', '前进前没保存当前拍'
        assert win._loaded_idx == 1
        assert win._panel.text() == win.beats[1].text

    def test_prev_beat_exists_and_works(self, win):
        win._show_index(2)
        win._panel.set_text('改过的')
        win.prev_beat()
        assert win.beats[2].text == '改过的', '后退前没保存当前拍'
        assert win._loaded_idx == 1

    def test_prev_at_first_beat_does_not_go_negative(self, win):
        win._show_index(0)
        win._panel.set_text('改过的')
        win.prev_beat()
        assert win._loaded_idx == 0
        assert win.beats[0].text == '改过的'

    def test_nothing_loaded_is_a_noop(self, win):
        win._idx = -1
        before = _texts(win)
        win.apply_current_beat()            # 不该抛，也不该改任何东西
        assert _texts(win) == before

    def test_navigation_never_touches_neighbour_beats(self, win):
        """
        翻页是**纯导航**：只提交当前拍，别的拍一个字都不许动。
        （历史上"自动轮流填说话人"就是从这条路静默改邻居的。）
        """
        before = [(b.speaker, b.text) for b in win.beats]
        win._show_index(0)
        for _ in range(len(win.beats) - 1):
            win.next_beat()
        after = [(b.speaker, b.text) for b in win.beats]
        assert before == after, '翻页改了数据'


# ==================================================================
#  三、列表行改完要跟着变
# ==================================================================

class TestListRowRefreshesAfterEdit:
    """
    改完台词，列表那一行也要跟着变，否则"列表和卡片不一致" ——
    用户会以为没保存上（tkinter 版有这一步，搬过来时漏了）。

    刷新发生在**保存**时（`save_current_beat`），不是 `apply_current_beat`
    —— 后者是"翻页顺带提交"，那条路不该顺手动列表。
    """

    def test_row_text_updates(self, win):
        win._show_index(2)
        win._panel.set_text('改过的')
        win.save_current_beat()
        assert '改过的' in win._list.item(2).text()

    def test_other_rows_untouched(self, win):
        win._show_index(2)
        before = [win._list.item(i).text() for i in (0, 4)]
        win._panel.set_text('改过的')
        win.save_current_beat()
        assert [win._list.item(i).text() for i in (0, 4)] == before
