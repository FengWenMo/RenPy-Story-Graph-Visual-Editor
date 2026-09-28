"""
tests/_source_paths.py
======================
**源码路径的唯一取法**：按模块名问 Python 要真实文件。

为什么不硬编码相对路径：2026-09-28 把 33 个扁平模块装进了 `storygraph` 包
（按功能分层），当天就有 12 个"扫源码"的测试因为 `ROOT / 'graph_view.py'`
失效而红。以后再搬、再改名，`module_source()` 都不会坏。

用法：
    from _source_paths import module_source, graph_view_source

    src = module_source('storygraph.ui.graph_view').read_text(encoding='utf-8')
"""

import importlib
import inspect
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def module_source(dotted: str) -> Path:
    """模块的点路径 → 它的真实源码文件（搬移/改名都不会失效）。"""
    return Path(inspect.getfile(importlib.import_module(dotted)))


def graph_view_source() -> Path:
    return module_source('storygraph.ui.graph_view')


def legacy_editor_source() -> Path:
    """遗留单文件版（已归档到 legacy/，不在包里）。"""
    return PROJECT_ROOT / 'legacy' / 'RenPy_Story_Graph_Visual_Editor.py'


__all__ = ['PROJECT_ROOT', 'module_source', 'graph_view_source',
           'legacy_editor_source']
