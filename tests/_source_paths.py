"""
tests/_source_paths.py
======================
**源码路径的唯一取法**：按模块名问 Python 要真实文件。

为什么不硬编码相对路径：2026-09-28 把 33 个扁平模块装进了 `storygraph` 包
（按功能分层），当天就有 12 个"扫源码"的测试因为 `ROOT / 'graph_view.py'`
失效而红。以后再搬、再改名，`module_source()` 都不会坏。

用法：
    from _source_paths import module_source, main_window_source

    src = main_window_source().read_text(encoding='utf-8')
    src = module_source('storygraph.app.beats').read_text(encoding='utf-8')
"""

import importlib
import inspect
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: 真实项目验收对象（本机是 `sr_general`：40 个脚本 / 2900 张图 / 146 个变换）。
#:
#: 换机器时用环境变量指到自己那份，相关用例就不会静默 skip：
#:     Windows:  set STORYGRAPH_REAL_PROJECT=D:\path\to\my_renpy_project
#:     bash:     export STORYGRAPH_REAL_PROJECT=/path/to/my_renpy_project
REAL_PROJECT = Path(os.environ.get(
    'STORYGRAPH_REAL_PROJECT', r'D:\千连万理\RenPyTest\sr_general'))

#: 真实项目里的"保真基准章节"：一批用例拿它当固定输入。
REAL_CHAPTER = REAL_PROJECT / 'game' / 'chapter' / 'chapter1_3.rpy'


def module_source(dotted: str) -> Path:
    """模块的点路径 → 它的真实源码文件（搬移/改名都不会失效）。"""
    return Path(inspect.getfile(importlib.import_module(dotted)))


def main_window_source() -> Path:
    """
    Qt 主窗口源码（**唯一前端的入口**）。

    2026-10-01：`storygraph/ui/`（tkinter）整个删除，界面只剩
    `storygraph/ui_qt/`。以前这里叫 `graph_view_source()`，
    指向已被删掉的 tkinter 文件 —— 名字也跟着改，免得"扫源码"的测试
    还在读一个不存在的路径。

    ⚠ **2026-10-05 起它只是"入口"，不再是"全部"**：`MainWindowQt`
    按职责拆成了五个 mixin（`window_graph_ops` / `window_card` /
    `window_nodes` / `window_export` / `window_assets` /
    `window_keyboard`）。**扫源码的测试要用 `main_window_sources()`**
    —— 它是"整个前端"的源码集合；只用这一个文件会看不到 90 个方法
    （实测 7 条用例因此误报"回调不存在"）。
    """
    return module_source('storygraph.ui_qt.main_window')


#: `MainWindowQt` 拆出去的 mixin 模块（与 `main_window.py` 的基类列表同源）。
#: **加一块就加一行** —— 扫源码的测试靠它拿到"整个前端"。
WINDOW_MIXIN_MODULES = (
    'storygraph.ui_qt.window_graph_ops',
    'storygraph.ui_qt.window_card',
    'storygraph.ui_qt.window_nodes',
    'storygraph.ui_qt.window_export',
    'storygraph.ui_qt.window_assets',
    'storygraph.ui_qt.window_keyboard',
)


def main_window_sources() -> list:
    """
    **整个前端**的源码文件（入口 + 全部 mixin）。

    为什么要有它：拆分之后"某方法在不在"必须看**所有**文件 ——
    `eventFilter` 在 `window_keyboard.py`、`export_script` 在
    `window_export.py`…… 只扫 `main_window.py` 会得出"这些回调都不存在"
    的错误结论，而那正是拆分当天 7 条用例红掉的原因。

    按**实际文件**取（`module_source`），所以以后再拆一块、改个名，
    这里跟着 `WINDOW_MIXIN_MODULES` 走，不会因为硬编码路径失效。
    """
    return [main_window_source()] + [module_source(m)
                                     for m in WINDOW_MIXIN_MODULES]


def main_window_source_text() -> str:
    """
    上面那些文件拼起来的源码（喂给 `ast.parse` / 字符串匹配用）。

    拼接是**安全**的：这些用例只做"名字在不在""某个写法有没有出现"
    这类检查，不关心它落在哪个文件。要定位具体文件时用
    `main_window_sources()` 自己逐个读。
    """
    return '\n'.join(p.read_text(encoding='utf-8')
                     for p in main_window_sources())


def legacy_editor_source() -> Path:
    """遗留单文件版（已归档到 legacy/，不在包里）。"""
    return PROJECT_ROOT / 'legacy' / 'RenPy_Story_Graph_Visual_Editor.py'


__all__ = ['PROJECT_ROOT', 'module_source', 'main_window_source',
           'main_window_sources', 'main_window_source_text',
           'WINDOW_MIXIN_MODULES', 'legacy_editor_source']
