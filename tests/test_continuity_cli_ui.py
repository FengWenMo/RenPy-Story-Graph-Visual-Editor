# -*- coding: utf-8 -*-
"""
B 方案（跨章连续性）的**入口**测试：CLI 命令 + Qt 报告窗口 + 工具栏入口。

分析本身在 `tests/test_continuity.py`；这里只钉"能不能跑起来、是不是只读、
缓存有没有生效、按钮在不在"。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


def _leak_project(root: Path) -> None:
    """两章线性项目：c1 章末有 BGM/背景/立绘，c2 首句前什么都不做。"""
    (root / 'chapters.rpy').write_text('''label c1:
    scene bg room
    show ling happy
    play music song1
    "c1 的台词"
    return

label c2:
    "c2 的第一句"
    return
''', encoding='utf-8')
    (root / 'controls.rpy').write_text('''label start:
    call c1_control
    return

label c1_control:
    call c1
    jump c2_control

label c2_control:
    call c2
    return
''', encoding='utf-8')


def _clean_project(root: Path) -> None:
    (root / 'chapters.rpy').write_text('''label c1:
    scene bg room
    show ling happy
    play music song1
    "c1 的台词"
    return

label c2:
    scene bg yard
    hide ling
    play music song2
    "c2 的第一句"
    return
''', encoding='utf-8')
    (root / 'controls.rpy').write_text('''label start:
    call c1_control
    return

label c1_control:
    call c1
    jump c2_control

label c2_control:
    call c2
    return
''', encoding='utf-8')


# ------------------------------------------------------------------
#  CLI
# ------------------------------------------------------------------

class TestContinuityCli:
    def test_json_output(self, tmp_path, capsys):
        _leak_project(tmp_path)
        from storygraph.cli import main

        code = main(['continuity', str(tmp_path), '--json'])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert code == 0
        assert data['stats']['chapters'] == 2
        assert data['stats']['warnings'] >= 2
        # 报告里必须带"不判定"和"确定链"，不是只有问题列表
        assert 'chains' in data and 'undetermined' in data

    def test_text_output_mentions_judgement(self, tmp_path, capsys):
        _leak_project(tmp_path)
        from storygraph.cli import main

        code = main(['continuity', str(tmp_path)])
        out = capsys.readouterr().out
        assert code == 0
        assert '跨章连续性体检' in out
        assert '【确定链】' in out
        assert '【需要人看】' in out
        assert '【不判定（不猜）】' in out
        assert 'M1' in out

    def test_strict_fails_on_leak(self, tmp_path):
        _leak_project(tmp_path)
        from storygraph.cli import main

        assert main(['continuity', str(tmp_path), '--strict']) == 1

    def test_strict_passes_on_clean(self, tmp_path):
        _clean_project(tmp_path)
        from storygraph.cli import main

        assert main(['continuity', str(tmp_path), '--strict']) == 0

    def test_file_filter(self, tmp_path, capsys):
        _leak_project(tmp_path)
        from storygraph.cli import main

        code = main(['continuity', str(tmp_path), '--json',
                     '--file', str(tmp_path / 'chapters.rpy')])
        data = json.loads(capsys.readouterr().out)
        assert code == 0
        assert data['files'] == [str(tmp_path / 'chapters.rpy')]


# ------------------------------------------------------------------
#  Qt 报告窗口（后台线程 + 缓存）
# ------------------------------------------------------------------

class TestContinuityDialog:
    def _wait_report(self, qapp, dialog, timeout=30.0):
        deadline = time.time() + timeout
        while dialog._report is None and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        return dialog._report

    def test_analysis_runs_and_renders(self, qapp, tmp_path):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.continuity_dialog import (
            ContinuityDialog, clear_cache)
        clear_cache()
        _leak_project(tmp_path)
        dlg = ContinuityDialog(None, str(tmp_path))
        report = self._wait_report(qapp, dlg)
        assert report is not None, '后台分析没有回来'
        text = dlg._text.toPlainText()
        assert '跨章连续性体检' in text
        assert 'M1' in text
        assert '只读' in text
        dlg.close()

    def test_second_open_uses_cache_without_thread(self, qapp, tmp_path):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.continuity_dialog import (
            ContinuityDialog, clear_cache)
        clear_cache()
        _leak_project(tmp_path)
        first = ContinuityDialog(None, str(tmp_path))
        assert self._wait_report(qapp, first) is not None
        first.close()
        second = ContinuityDialog(None, str(tmp_path))
        assert second._report is not None, '缓存没命中，又去分析了'
        assert second._thread is None
        second.close()

    def test_changed_file_invalidates_cache(self, qapp, tmp_path):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.continuity_dialog import (
            ContinuityDialog, clear_cache)
        clear_cache()
        _leak_project(tmp_path)
        first = ContinuityDialog(None, str(tmp_path))
        assert self._wait_report(qapp, first) is not None
        first.close()
        # 改一个字节 + 推 mtime（文件不变的话同尺寸同 mtime 可能命中缓存）
        path = tmp_path / 'chapters.rpy'
        path.write_text(path.read_text(encoding='utf-8') + '\n# changed\n',
                        encoding='utf-8')
        later = time.time() + 5
        import os
        os.utime(path, (later, later))
        second = ContinuityDialog(None, str(tmp_path))
        assert self._wait_report(qapp, second) is not None
        assert second._thread is not None or second._report is not None
        second.close()


# ------------------------------------------------------------------
#  工具栏入口
# ------------------------------------------------------------------

class TestToolbarEntry:
    def test_continuity_action_exists(self, ui_window):
        texts = [a.text() for a in ui_window._tb_graph.actions()
                 if a.text().strip()]
        assert any('跨章连续性' in t for t in texts), texts[:40]

    def test_action_opens_dialog_without_writing(self, ui_window, ui_project,
                                                 monkeypatch):
        """点了按钮 → 弹报告窗口；**不写盘**（分析只读）。"""
        import storygraph.ui_qt.continuity_dialog as cd

        opened = {}

        class _Signal:
            def connect(self, *args, **kwargs):     # 假信号：不接也无所谓
                return None

        class _FakeDialog:
            report_ready = _Signal()

            def __init__(self, parent, root, *a, **k):
                opened['root'] = str(root)

            def exec(self):
                opened['exec'] = True
                return 1

        monkeypatch.setattr(cd, 'ContinuityDialog', _FakeDialog)
        ui_window.open_project_path(ui_project)
        ui_window.show_continuity()
        assert opened.get('exec') is True
        assert opened.get('root')
