"""
tests/test_card_selection_sync.py
================================
「列表高亮」与「面板显示」必须同步 —— 而且是**数据安全**问题，不只是显示。

背景（用户 2026-09-28 截图暴露）：
列表高亮第 50 行，面板却显示第 49 拍的内容。当时表现为"说话人丢了"，
真正危险的是：`apply_current_beat` 原来**从列表读 index 往回写**，
两者错开时会把**面板里那一拍的内容写进列表选中的另一拍** —— 改错数据。

修法：`self._loaded_idx` 是"面板现在显示哪一拍"的唯一真源；
载入一律走 `_show_index`，回写一律用 `_loaded_idx`。
"""

import pytest

from beat_editor import ScriptTableEditorWindow
from script_table import Beat


class _FakeList:
    """记录型 Listbox 桩。"""

    def __init__(self, rows):
        self.rows = list(rows)
        self.sel = ()

    def curselection(self):
        return self.sel

    def selection_clear(self, *a):
        self.sel = ()

    def selection_set(self, i):
        self.sel = (i,)

    def see(self, i):
        pass

    def delete(self, i, *a):
        # Listbox.delete(0,'end') 清空；delete(i) 删一行
        if isinstance(i, int) and not a:
            del self.rows[i]
        else:
            self.rows = []

    def insert(self, i, text):
        if i == 'end':
            self.rows.append(text)
        else:
            self.rows.insert(i, text)


class _FakePanel:
    """记录 load_beat / collect_beat 的桩。"""

    def __init__(self):
        self.loaded = []
        self.hints = []
        self._current = None

    def load_beat(self, beat, inherit_hint=''):
        # 签名跟随真实面板（load_beat(beat, inherit_hint='')）。
        # 桩少一个参数就会在所有调用点炸 —— 而"单独跑不炸、全量跑炸"
        # 正是这种漂移的典型症状（本次实测踩过）。
        self.loaded.append(getattr(beat, 'text', ''))
        self.hints.append(inherit_hint)
        self._current = beat

    def set_stage_snapshot(self, text):
        pass

    def collect_beat(self):
        """模拟"用户改了台词"。"""
        b = self._current or Beat()
        b.text = '改过的'
        return b


def _window(beats):
    """绕开 tkinter 建一个只有内部状态的窗口对象。"""
    from beat_editor import ScriptTableEditorWindow as W
    w = W.__new__(W)
    w.beats = beats
    w.rotation = []
    w._loaded_idx = -1
    w._list = _FakeList([f'#{i+1} {b.text}' for i, b in enumerate(beats)])
    w._panel = _FakePanel()
    return w


def _beats():
    return [Beat(seq=i + 1, label='t', text=f'第{i+1}句')
            for i in range(5)]


class TestLoadedIndexIsSourceOfTruth:
    def test_show_index_loads_that_beat_and_highlights_it(self):
        w = _window(_beats())
        w._show_index(3)
        assert w._loaded_idx == 3
        assert w._panel.loaded[-1] == '第4句'
        assert w._list.curselection() == (3,), '列表高亮没跟着走'

    def test_out_of_range_is_ignored(self):
        w = _window(_beats())
        w._show_index(99)
        assert w._loaded_idx == -1, '越界不该改状态'
        assert w._panel.loaded == []

    def test_on_select_follows_user_click(self):
        """用户点列表 → 载入那一拍。"""
        w = _window(_beats())
        w._list.sel = (2,)
        w._on_select()
        assert w._loaded_idx == 2
        assert w._panel.loaded[-1] == '第3句'


class TestWriteBackUsesLoadedIndexNotListHighlight:
    """
    **这条是数据安全的核心回归**：即使列表高亮和面板显示错开，
    回写也必须落在**面板显示的那一拍**。
    """

    def test_writes_to_loaded_beat_when_list_differs(self):
        w = _window(_beats())
        w._show_index(1)            # 面板显示第 2 句
        w._list.sel = (4,)          # 但列表高亮跑到第 5 行（模拟错开）
        w.apply_current_beat()
        assert w.beats[1].text == '改过的', '没写进面板显示的那一拍'
        assert w.beats[4].text == '第5句', \
            '写错行了！列表高亮的另一拍被覆盖 —— 这正是修之前的 bug'

    def test_next_beat_writes_before_advancing(self):
        w = _window(_beats())
        w._show_index(0)
        w.next_beat()
        assert w.beats[0].text == '改过的', '前进前没保存当前拍'
        assert w._loaded_idx == 1
        assert w._panel.loaded[-1] == '第2句'

    def test_prev_beat_exists_and_works(self):
        w = _window(_beats())
        w._show_index(2)
        w.prev_beat()
        assert w.beats[2].text == '改过的', '后退前没保存当前拍'
        assert w._loaded_idx == 1

    def test_prev_at_first_beat_does_not_go_negative(self):
        w = _window(_beats())
        w._show_index(0)
        w.prev_beat()
        assert w._loaded_idx == 0
        assert w.beats[0].text == '改过的'

    def test_next_at_last_beat_stays_put(self):
        w = _window(_beats())
        w._show_index(4)
        w.next_beat()
        assert w._loaded_idx == 4
        assert w.beats[4].text == '改过的'

    def test_nothing_loaded_is_a_noop(self):
        w = _window(_beats())
        w.apply_current_beat()      # 不该抛，也不该改任何东西
        assert [b.text for b in w.beats] == [f'第{i+1}句' for i in range(5)]


class TestListRowRefreshesAfterEdit:
    """改完台词，列表那一行也要跟着变，否则"列表和卡片不一致"。"""

    def test_row_text_updates(self):
        w = _window(_beats())
        w._show_index(2)
        w.apply_current_beat()
        assert '改过的' in w._list.rows[2]

    def test_other_rows_untouched(self):
        w = _window(_beats())
        w._show_index(2)
        w.apply_current_beat()
        assert '第1句' in w._list.rows[0]
        assert '第5句' in w._list.rows[4]
