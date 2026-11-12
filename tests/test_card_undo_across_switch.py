"""卡片改完 → 切到下一拍 → 切回来，撤销按钮还该亮着。

用户 2026-11-12 实测：

    > 有时候有个 bug，当我先改完了一张卡片后，切换到下一张卡片，再回来，
    > 我就发现我已经无法按撤销修改的按钮了……导致我都不知道原来的那个卡
    > 改了什么，误改错误了无法改回去

翻页会**顺带提交**（`apply_current_beat`，把表单写回拍）。这一步从前走的是
`EditorSession.apply` —— 它是"无脑写回"，**不记撤销快照**（只有
`save()` 会先拍快照存进 `_last_saved`）。于是：

  · 用户改完卡片、没点 💾、直接翻页 → 内容进了内存，撤销栈却是空的；
  · 翻回来 `has_undo(idx)` 为假，撤销按钮熄灭、Ctrl+Z 也说"没有可撤销的修改"。

这就是"有时候"的来源 —— 点了 💾 再切走的人不会遇到，直接切走的人必遇到。

这个文件钉住两件事：
  1. **切走时的顺带提交也要留下撤销记录**（能退回改之前的样子）；
  2. **只是翻页（没改任何东西）不许点亮撤销** —— 否则翻一遍全章就攒出
     一堆"可撤销"，按钮亮得毫无意义。
"""

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication                 # noqa: E402

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project                      # noqa: E402


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    from storygraph.ui_qt.main_window import MainWindowQt

    project = build_project(tmp_path / 'proj')
    script = project / 'game' / 'script' / 'ch01.rpy'

    class _Engine:
        def __init__(self, root):
            self.project_root = root
            self._registry = None

        def get_registry(self):
            return self._registry

    win = MainWindowQt(_Engine(project), script)
    yield win
    win.close()


def test_edit_then_switch_away_and_back_keeps_undo_armed(window):
    """改完不点保存、直接翻页 → 翻回来仍能撤销（退回到改之前那句）。"""
    window._show_index(0)
    original = window.beats[0].text
    window._panel.set_text('切走之前改的')
    assert window._panel.is_modified(), '前置条件：表单里确实改了字'

    window.next_beat()                       # 顺带提交 + 翻到下一拍
    assert window.beats[0].text == '切走之前改的', \
        '前置条件：翻页要把表单提交进拍（不提交就没这回事）'

    window.prev_beat()                       # 翻回来
    assert window._session.has_undo(0), \
        '切走时的顺带提交没留撤销记录 —— 用户再也退不回去了'
    assert window._panel._undo_btn.isEnabled(), '撤销按钮该亮起来'

    window._panel._undo_btn.click()
    assert window.beats[0].text == original, \
        f'撤销该退回改之前的「{original}」'


def test_two_cards_each_keep_their_own_undo(window):
    """先改 A 再改 B，翻回 A 时 A 的撤销不能被 B 顶掉。"""
    window._show_index(0)
    a_before = window.beats[0].text
    window._panel.set_text('A 改过了')
    window.next_beat()

    window._panel.set_text('B 也改过了')
    window.next_beat()
    assert window._session.has_undo(0), 'A 的撤销记录被顶掉了'
    assert window._session.has_undo(1), 'B 该有自己的撤销记录'

    window._show_index(0)
    window._panel._undo_btn.click()
    assert window.beats[0].text == a_before
    assert window.beats[1].text == 'B 也改过了', '撤 A 不许连 B 一起撤'


def test_walking_the_chapter_without_editing_does_not_arm_undo(window):
    """只是翻页（一个字没改）→ 每一拍都不该出现"可撤销"。"""
    window._show_index(0)
    for _ in range(len(window._session) - 1):
        window.next_beat()
    armed = [i for i in range(len(window._session))
             if window._session.has_undo(i)]
    assert armed == [], f'光翻页就点亮了撤销：{armed}'
    assert not window._panel._undo_btn.isEnabled()


def test_sprite_only_edit_also_survives_the_switch(window):
    """只改了个立绘表情（一个字没改）→ 切走再回来，同样要能撤销。

    立绘卡的改动不进表单字段（`is_modified` 看不见），所以这一条单独钉：
    漏了它，`cards_modified` 就白写了。
    """
    window._show_index(0)
    show_before = str(getattr(window.beats[0], 'show', '') or '')
    card = window._panel._grid._cards[0]
    card._set_field('expression', 'happy')
    assert window._panel.cards_modified(), '改表情之后网格该是"改脏"的'

    window.next_beat()
    assert str(getattr(window.beats[0], 'show', '') or '') != show_before, \
        '前置条件：翻页要把立绘改动提交进拍'

    window.prev_beat()
    assert window._session.has_undo(0), '立绘改动被提交了却没留撤销记录'
    window._panel._undo_btn.click()
    assert str(getattr(window.beats[0], 'show', '') or '') == show_before

