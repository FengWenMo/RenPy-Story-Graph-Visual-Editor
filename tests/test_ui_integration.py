"""
tests/test_ui_integration.py
============================
UI 层的可测部分（不需要真的弹出窗口）。

2026-10-01：tkinter 的 `graph_view.py` 已删除，界面的唯一实现是
`storygraph/ui_qt/`。本文件跟着改：

  · `_open_with_system_default`（"用 Excel 打开"）→
    搬到了 `storygraph/graph/editor_utils.open_with_system_default`
    （它本来就是**纯平台逻辑**，一行 tkinter 都没有）；
  · `open_file_at_line` 的通知断言保持原样（后端走 `app.notify`）；
  · 「按钮指向的方法必须存在」这条静态接线检查改成扫 **Qt 主窗口**
    （tkinter 用 `command=`，Qt 用 `act(...)` / `connect(...)`）。
"""

import ast
from pathlib import Path

import pytest

from _source_paths import legacy_editor_source, main_window_source


# ------------------------------------------------------------------
#  打开外部程序（平台逻辑，与 UI 框架无关）
# ------------------------------------------------------------------

class TestOpenWithSystemDefault:
    def test_missing_file_returns_false(self, tmp_path):
        from storygraph.graph.editor_utils import open_with_system_default
        assert open_with_system_default(tmp_path / 'nope.xlsx') is False

    def test_existing_file_invokes_opener(self, tmp_path, monkeypatch):
        import storygraph.graph.editor_utils as eu

        calls = []

        def fake_popen(cmd, *a, **k):
            calls.append(cmd)
            return object()

        def fake_startfile(p):
            calls.append(['startfile', str(p)])

        monkeypatch.setattr(eu.os, 'startfile', fake_startfile,
                            raising=False)
        monkeypatch.setattr(eu.subprocess, 'Popen', fake_popen)

        f = tmp_path / 'sheet.xlsx'
        f.write_bytes(b'x')
        assert eu.open_with_system_default(f) is True
        assert calls

    def test_opener_exception_returns_false(self, tmp_path, monkeypatch):
        """唤起失败要如实上报，调用方才能提示用户手动打开。"""
        import storygraph.graph.editor_utils as eu

        def boom(cmd, *a, **k):
            raise OSError('no opener')

        def boom_startfile(p):
            raise OSError('no opener')

        monkeypatch.setattr(eu.os, 'startfile', boom_startfile, raising=False)
        monkeypatch.setattr(eu.subprocess, 'Popen', boom)

        f = tmp_path / 'sheet.xlsx'
        f.write_bytes(b'x')
        assert eu.open_with_system_default(f) is False


def test_editor_utils_opens_existing_file(monkeypatch, tmp_path, notify_calls):
    """open_file_at_line 对存在的文件不应报错。"""
    import storygraph.graph.editor_utils as editor_utils

    f = tmp_path / 'a.rpy'
    f.write_text('x', encoding='utf-8')

    def fake_popen(cmd, *a, **k):
        return object()

    monkeypatch.setattr(editor_utils.subprocess, 'Popen', fake_popen)
    editor_utils.open_file_at_line(f, 1)
    assert notify_calls.of('error') == []


def test_editor_utils_reports_missing_file(tmp_path, notify_calls):
    """文件不存在时要如实上报（走通知端口，不再直接弹 tkinter 框）。"""
    import storygraph.graph.editor_utils as editor_utils

    editor_utils.open_file_at_line(tmp_path / 'ghost.rpy', 1)
    assert notify_calls.of('error')


# ------------------------------------------------------------------
#  静态接线检查：按钮/菜单的回调必须真的存在
# ------------------------------------------------------------------

#: 真正"挂回调"的调用点（Qt 的写法）。
_REGISTER_CALLS = frozenset({
    'connect', 'act', 'addAction', 'clicked', 'triggered',
})


def _imports_tkinter(tree) -> bool:
    """这棵 AST 里有没有真的 `import tkinter`（字符串搜索会被文档字符串骗）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == 'tkinter' or a.name.startswith('tkinter.')
                   for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            if mod == 'tkinter' or mod.startswith('tkinter.'):
                return True
        elif isinstance(node, ast.Call):
            # `importlib.import_module('tkinter')` 这类动态导入也拦
            f = node.func
            name = getattr(f, 'attr', None) or getattr(f, 'id', None)
            if name in ('import_module', '__import__'):
                for a in node.args:
                    if isinstance(a, ast.Constant) and \
                            str(a.value).startswith('tkinter'):
                        return True
    return False


def _is_registration_call(node: ast.Call) -> bool:
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id in _REGISTER_CALLS
    if isinstance(fn, ast.Attribute):
        if fn.attr in _REGISTER_CALLS:
            return True
        if isinstance(fn.value, ast.Attribute):
            return fn.value.attr in _REGISTER_CALLS
    return False


def _called_names(node) -> set:
    """`node` 里所有 `self.foo(...)` 形式的**调用**名。"""
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                and isinstance(sub.func.value, ast.Name) \
                and sub.func.value.id == 'self':
            out.add(sub.func.attr)
    return out


def _iter_command_targets(tree):
    """
    找出所有被挂成回调的 `self.xxx`。

    Qt 的两种写法都要认：
      · `act('文字', self.foo, …)` —— 位置参数，函数引用；
      · `menu.addAction('文字', lambda: self.foo(nid))` —— lambda 里的调用。
    """
    targets = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_registration_call(node):
            continue
        for arg in list(node.args) + [k.value for k in node.keywords]:
            if isinstance(arg, ast.Attribute) and \
                    isinstance(arg.value, ast.Name) and \
                    arg.value.id == 'self':
                targets.add(arg.attr)
            if isinstance(arg, ast.Lambda):
                targets |= _called_names(arg)
    return targets


class TestUiWiring:
    """
    「批量导入章节」按钮曾经挂在 `self.batch_import_chapters` 上，而这个
    方法根本不在 UI 类里（函数住在 renpy_parser 里）——点一下 AttributeError。

    这类**悬空回调**在无头环境里点不出来（tkinter 时代靠桩，Qt 时代
    点一下也只是静默），所以这里做静态接线检查：凡是挂成回调的
    `self.xxx`，类里必须真有这个方法。
    """

    @staticmethod
    def _missing(path) -> list:
        src = Path(path).read_text(encoding='utf-8')
        tree = ast.parse(src)
        defined = {n.name for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        return sorted(_iter_command_targets(tree) - defined)

    def test_qt_window_callbacks_all_exist(self):
        missing = self._missing(main_window_source())
        assert missing == [], f'按钮/菜单指向了不存在的方法：{missing}'

    def test_qt_window_has_no_tkinter_style_command(self):
        """
        `command=self.xxx` 是 tkinter 的写法。Qt 里出现它就说明有人把
        老代码粘回来了 —— 而 Qt **不认**这个关键字，等于根本没接线
        （这正是"点了没反应"最容易的来源）。
        """
        src = Path(main_window_source()).read_text(encoding='utf-8')
        assert 'command=self.' not in src
        assert 'import tkinter' not in src

    def test_whole_codebase_is_tkinter_free(self):
        """
        **整套源码**里不许再 `import tkinter`。

        这是"彻底清除 tkinter"的守门测试：`storygraph/ui/` 已删除，
        如果哪天有人为了图方便又 `import tkinter` 进后端，
        这一条会当场炸（而不是等到 CI 上无头 import 失败）。

        判据用 **AST**，不用字符串搜索：app 层好几个模块的文档字符串里
        就写着"禁止 import tkinter"，字符串搜索会把说明文字当成违规
        （假警报会把这条守卫逼成摆设）。
        """
        root = Path(__file__).resolve().parent.parent / 'storygraph'
        offenders = []
        for path in root.rglob('*.py'):
            try:
                tree = ast.parse(path.read_text(encoding='utf-8'))
            except SyntaxError:              # 语法错由别的测试负责
                continue
            if _imports_tkinter(tree):
                offenders.append(str(path.relative_to(root.parent)))
        assert not offenders, f'这些文件还在 import tkinter：{offenders}'

    def test_no_tkinter_modules_are_loaded(self):
        """
        跑完整个测试会话后，`sys.modules` 里不该有 tkinter。

        比静态扫描更强的一条：静态只看得到字面 import，
        而动态 import（`importlib.import_module('tkinter')`）只有这里能抓。
        """
        import sys
        loaded = [m for m in sys.modules if m == 'tkinter'
                  or m.startswith('tkinter.')]
        assert not loaded, f'tkinter 被真的加载了：{loaded}'

    def test_legacy_editor_commands_all_exist(self):
        """
        遗留单文件版已归档到 `legacy/`（**不在包里、不参与构建**），
        但仍留在仓库里供参考，所以保留这条静态检查：万一有人从归档里
        把代码复制回去，悬空 `command=` 会被当场发现。

        2026-09-28：以前这里是 `import RenPy_Story_Graph_Visual_Editor`
        （靠根目录在 sys.path 上），归档后该 import 不再成立 ——
        改为按归档路径读源码。
        """
        path = legacy_editor_source()
        if not path.exists():
            pytest.skip('遗留单文件版已移除')
        src = path.read_text(encoding='utf-8')
        tree = ast.parse(src)
        defined = {n.name for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        # 老文件用 tkinter 的 `command=self.xxx`
        import re
        targets = set(re.findall(r'command=self\.([A-Za-z_]\w*)', src))
        missing = sorted(t for t in targets if t not in defined)
        assert missing == [], f'遗留单文件版有悬空 command：{missing}'
