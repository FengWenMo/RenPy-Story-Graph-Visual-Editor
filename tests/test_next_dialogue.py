"""
「只改台词」快捷路径判据（用户 2026-09-28 要的，2026-10-11 补做）
====================================================================

用户要的是"快速过一遍台词"：一句一句跳，不必每次都在列表里找。

⚠ 两条设计约束（都在判据里钉住）：
  ① **不做列表过滤**：卡片下标要映射到 `session.beats[idx]`，
     把没台词的拍藏起来会让下标与拍错位 —— 那是"存错拍"级别的隐患，
     比"找不到"严重得多。所以只**移动选中**，一个都不藏。
  ② **能点就必须有反应**：跳到头了要**说一句**，不许默默不动。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class _Beat:
    def __init__(self, kind: str, text: str = '', speaker: str = ''):
        self.kind = kind
        self.text = text
        self.speaker = speaker


class _Session:
    """最小会话：只要 `beats` / `has`（`_show_index` 会用）。"""

    def __init__(self, beats):
        self.beats = beats
        self._loaded = set()

    def has(self, idx) -> bool:
        return 0 <= int(idx) < len(self.beats)

    def mark_loaded(self, idx):
        self._loaded.add(int(idx))


def _mixer(beats, *, idx=-1):
    """造一个只有 `next_dialogue` 依赖的那几样的"卡片页"（不建 Qt 窗口）。"""
    from storygraph.ui_qt.window_card import CardPageMixin

    class _Page(CardPageMixin):
        def __init__(self):
            self._session = _Session(beats)
            self._idx = idx
            self.shown = []
            self.said = []

        def _show_index(self, i):
            self._idx = int(i)
            self.shown.append(int(i))

        def _focus_dialogue(self):
            self.said.append('focus')

        def _status(self, message):
            self.said.append(str(message))

    return _Page()


def test_it_jumps_to_the_next_beat_with_dialogue():
    """从"没有台词"的拍上跳过去，落在下一句台词上，并把光标放进台词框。"""
    beats = [_Beat('SCENE'), _Beat('SHOW'), _Beat('SAY', '你好', 'lyj'),
             _Beat('HIDE'), _Beat('SAY', '再见', 'lan')]
    page = _mixer(beats, idx=0)
    page.next_dialogue()
    assert page.shown == [2], page.shown
    assert 'focus' in page.said, page.said
    assert '第 3 句台词' in page.said[-1], page.said


def test_it_continues_from_the_current_beat():
    """已经在某句台词上 → 跳**下一句**（不是停在原地）。"""
    beats = [_Beat('SAY', '一', 'lyj'), _Beat('SAY', '二', 'lyj')]
    page = _mixer(beats, idx=0)
    page.next_dialogue()
    assert page.shown == [1], page.shown


def test_a_speaker_only_line_counts_as_dialogue():
    """只要有人在说话（有 speaker）就算台词 —— 别把 `speaker + 空文本` 漏掉。"""
    beats = [_Beat('SAY', '', 'lyj'), _Beat('SAY', '有词', '')]
    page = _mixer(beats, idx=-1)
    page.next_dialogue()
    assert page.shown == [1], page.shown


def test_the_end_says_so_instead_of_doing_nothing():
    """⭐ 跳到头了要**明确说一句**（能点就必须有反应）。"""
    beats = [_Beat('SAY', '唯一一句', 'lyj')]
    page = _mixer(beats, idx=0)
    page.next_dialogue()
    assert page.shown == [], '不该乱跳'
    assert '没有台词了' in page.said[-1], page.said


def test_it_never_filters_the_list():
    """
    ⭐⭐ **设计约束**：这条快捷路径**只移动选中**，绝不"藏起没台词的拍" ——
    藏了会让卡片下标与 `beats` 错位（存错拍级别的事故）。
    判据：一次跳跃里 `shown` 只有一个下标，且**等于真实拍下标**。
    """
    beats = [_Beat('SHOW'), _Beat('SAY', 'x', 'lyj'), _Beat('SAY', 'y', 'lan')]
    page = _mixer(beats, idx=-1)
    page.next_dialogue()
    assert page.shown == [1], page.shown          # 真实下标，不是"过滤后的第几个"
    assert len(page.shown) == 1, '不该顺手重建列表'


def test_no_beats_says_so(tmp_path):
    """一章都没载入 → 也要说话（不许异常、不许静默）。"""
    page = _mixer([], idx=-1)
    page.next_dialogue()
    assert '还没载入' in page.said[-1], page.said


def test_the_toolbar_has_the_entry():
    """入口要在（用户："我找不到入口"是历史教训 —— 之前 4 项功能做完却找不到）。"""
    src = (ROOT / 'storygraph' / 'ui_qt' / 'main_window.py').read_text(
        encoding='utf-8')
    assert '下一句台词' in src, '工具栏里没有这个入口'
    assert 'self.next_dialogue' in src, '按钮没连到方法'
