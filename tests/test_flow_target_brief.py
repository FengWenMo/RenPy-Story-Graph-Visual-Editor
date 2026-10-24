"""
tests/test_flow_target_brief.py
===============================
控制流卡上的**目标摘要**（"side：6 拍 · 第 173 行 · 本章 2 处调用"）。

为什么要有它（2026-10-23，用户）：

> 卡片页看的归卡片页看的！但人不能什么都去翻卡片！！！
> 只能在卡牌页面内决定 label 跳转逻辑是非常麻烦的！

用户的意思是：**读**的时候也不该逼人点进去。调用卡上直接写清"目标有多大、
在哪一行、本章有几处在调它"，不点进去也能判断"这个 call 是不是我要的"。

判据分两层：
  · 纯函数（`story.flow.label_brief` / `callers_of`）—— 数得对；
  · 卡片（`BeatCardEditor.set_flow` 的 `brief`）—— 条上真的写出来。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
pytest.importorskip('PySide6')

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _ui_project import build_project                      # noqa: E402
from storygraph.story.flow import callers_of, label_brief   # noqa: E402

FLOW_SCRIPT = '''label main:
    e "开始"
    call side
    e "调用之后"
    call side
    jump ending

label side:
    e "支线一"
    e "支线二"
    return

label ending:
    return
'''


class _Beat:
    def __init__(self, label, src_first=0, raw='', kind='say', text=''):
        self.label = label
        self.src_first = src_first
        self.raw = raw
        self.kind = kind
        self.text = text


def _beats():
    return [
        _Beat('main', 1, text='开始'),
        _Beat('main', 2, raw='call side', kind='narration'),
        _Beat('main', 3, text='调用之后'),
        _Beat('main', 4, raw='call side', kind='narration'),
        _Beat('main', 5, raw='jump ending', kind='narration'),
        _Beat('side', 7, text='支线一'),
        _Beat('side', 8, text='支线二'),
        _Beat('ending', 10, text=''),
    ]


# ==================================================================
#  纯函数
# ==================================================================

class TestLabelBrief:
    def test_counts_beats_and_reports_the_first_source_line(self):
        brief = label_brief(_beats(), 'side')
        assert brief == {'count': 2, 'line': 7}

    def test_unknown_label_is_empty(self):
        assert label_brief(_beats(), 'nowhere') == {}

    def test_empty_target_is_empty(self):
        assert label_brief(_beats(), '') == {}


class TestCallersOf:
    def test_counts_every_call_in_this_chapter(self):
        assert callers_of(_beats(), 'side') == [1, 3]

    def test_jump_counts_too(self):
        assert callers_of(_beats(), 'ending') == [4]

    def test_nobody_calls_it(self):
        assert callers_of(_beats(), 'main') == []


# ==================================================================
#  卡片上真的写出来
# ==================================================================

@pytest.fixture(scope='session')
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def project(tmp_path):
    root = build_project(tmp_path / 'proj')
    (root / 'game' / 'script' / 'flow.rpy').write_text(
        FLOW_SCRIPT, encoding='utf-8')
    (root / 'game' / 'script' / 'extra.rpy').write_text(
        'label extra_target:\n    e "另一个文件里的章"\n    return\n',
        encoding='utf-8')
    return root


@pytest.fixture
def window(app, project):
    from storygraph.ui_qt.main_window import MainWindowQt

    class _Engine:
        project_root = project
        _registry = None

        def get_registry(self):
            return None

    win = MainWindowQt(_Engine(), project / 'game' / 'script' / 'flow.rpy')
    win.refresh_chapter_nav()
    yield win
    win.close()


def _show_call(window, raw='call side'):
    window._set_page(window.PAGE_CARD)
    idx = next(i for i, b in enumerate(window._session.beats)
               if getattr(b, 'raw', '') == raw)
    window._show_index(idx)
    return window._panel


class TestFlowCardSummary:
    def test_same_file_summary_is_written_on_the_strip(self, window):
        panel = _show_call(window)
        text = panel._flow_detail.text()
        assert '目标 side' in text, text
        # side 本身 2 句台词 + 一条 return 拍（2026-10-23 起 return 也落卡）
        assert '3 拍' in text, text
        # `src_first` 是**台词那一行**（不是 label 那一行）：文件里 label 在 8、首句在 9
        assert '第 9 行' in text, text
        assert '本章 2 处调用' in text, text

    def test_cross_file_summary_uses_the_project_index(self, window):
        panel = _show_call(window, raw='jump ending')
        # ending 在本文件里；换一个确定在别的文件里的目标来验"跨文件"那一支
        brief = window._flow_target_brief('extra_target')
        assert brief.get('file') == 'extra.rpy', brief
        assert brief.get('line') == 1, brief
        assert brief.get('same_file') is False

    def test_no_target_writes_no_summary(self, window):
        panel = _show_call(window)
        panel._flow_target.setCurrentText('')
        panel._refresh_flow_text()
        assert '目标 ' not in panel._flow_detail.text()

    def test_brief_never_raises_for_garbage_target(self, window):
        assert window._flow_target_brief('') == {}
        assert window._flow_target_brief('不存在的 label') == {}
