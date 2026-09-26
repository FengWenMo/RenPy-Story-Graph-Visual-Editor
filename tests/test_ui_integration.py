"""
tests/test_ui_integration.py
============================
UI 层的可测部分（不需要真的弹出窗口）。

graph_view 依赖 tkinter 画布，没法在无头环境里实例化，
但模块级工具函数与"打开系统程序"这类逻辑可以独立验证。
"""

import sys

import pytest


def test_graph_view_imports_without_real_tkinter():
    """
    无 GUI 环境下模块仍应可导入，否则 CI 上连语法回归都跑不了。
    """
    import graph_view
    assert hasattr(graph_view, 'GraphView')
    assert hasattr(graph_view, '_open_with_system_default')


class TestOpenWithSystemDefault:
    def test_missing_file_returns_false(self, tmp_path):
        import graph_view
        assert graph_view._open_with_system_default(
            tmp_path / 'nope.xlsx') is False

    def test_existing_file_invokes_opener(self, tmp_path, monkeypatch):
        import graph_view

        calls = []

        def fake_popen(cmd, *a, **k):
            calls.append(cmd)
            return object()

        def fake_startfile(p):
            calls.append(['startfile', str(p)])

        monkeypatch.setattr(graph_view.os, 'startfile', fake_startfile,
                            raising=False)
        monkeypatch.setattr(graph_view.subprocess, 'Popen', fake_popen)

        f = tmp_path / 'sheet.xlsx'
        f.write_bytes(b'x')
        assert graph_view._open_with_system_default(f) is True
        assert calls

    def test_opener_exception_returns_false(self, tmp_path, monkeypatch):
        """唤起失败要如实上报，调用方才能提示用户手动打开。"""
        import graph_view

        def boom(cmd, *a, **k):
            raise OSError('no opener')

        def boom_startfile(p):
            raise OSError('no opener')

        monkeypatch.setattr(graph_view.os, 'startfile', boom_startfile,
                            raising=False)
        monkeypatch.setattr(graph_view.subprocess, 'Popen', boom)

        f = tmp_path / 'sheet.xlsx'
        f.write_bytes(b'x')
        assert graph_view._open_with_system_default(f) is False


def test_editor_utils_opens_existing_file(monkeypatch, tmp_path):
    """open_file_at_line 对存在的文件不应弹错误框。"""
    import editor_utils
    import tkinter.messagebox as mb

    calls = []
    monkeypatch.setattr(mb, 'showerror', lambda *a, **k: calls.append(a))

    f = tmp_path / 'a.rpy'
    f.write_text('x', encoding='utf-8')

    def fake_popen(cmd, *a, **k):
        return object()

    monkeypatch.setattr(editor_utils.subprocess, 'Popen', fake_popen)
    editor_utils.open_file_at_line(f, 1)
    assert calls == []


def test_editor_utils_reports_missing_file(monkeypatch, tmp_path):
    import editor_utils
    import tkinter.messagebox as mb

    calls = []
    monkeypatch.setattr(mb, 'showerror', lambda *a, **k: calls.append(a))
    editor_utils.open_file_at_line(tmp_path / 'ghost.rpy', 1)
    assert calls
