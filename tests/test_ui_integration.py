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


# ------------------------------------------------------------------
# 静态接线检查：按钮/菜单的 command 必须真的存在
# ------------------------------------------------------------------

def _iter_command_targets(tree):
    """找出源码里所有 `command=self.xxx` / `command=lambda: ...self.xxx(`。"""
    import ast

    targets = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != 'command':
                continue
            v = kw.value
            # command=self.foo
            if (isinstance(v, ast.Attribute)
                    and isinstance(v.value, ast.Name)
                    and v.value.id == 'self'):
                targets.add(v.attr)
            # command=lambda: self.foo(...)
            if isinstance(v, ast.Lambda):
                for sub in ast.walk(v):
                    if (isinstance(sub, ast.Attribute)
                            and isinstance(sub.value, ast.Name)
                            and sub.value.id == 'self'):
                        targets.add(sub.attr)
    return targets


class TestUiWiring:
    """
    「批量导入章节」按钮曾经挂在 `self.batch_import_chapters` 上，而这个
    方法根本不在 UI 类里（函数住在 renpy_parser 里）——点一下 AttributeError。
    无头环境实例化不了 GraphView，所以这里做静态接线检查：凡是挂在
    command= 上的 self.xxx，类里必须真有这个方法。
    """

    def _missing(self, path):
        import ast
        from pathlib import Path

        src = Path(path).read_text(encoding='utf-8')
        tree = ast.parse(src)
        defined = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name)
        return sorted(_iter_command_targets(tree) - defined)

    def test_graph_view_commands_all_exist(self):
        from pathlib import Path
        import graph_view
        missing = self._missing(Path(graph_view.__file__))
        assert missing == [], f'按钮/菜单指向了不存在的方法：{missing}'

    def test_main_entry_commands_all_exist(self):
        from pathlib import Path
        import RenPy_Story_Graph_Visual_Editor as legacy
        missing = self._missing(Path(legacy.__file__))
        assert missing == [], f'遗留单文件版同样有悬空 command：{missing}'
