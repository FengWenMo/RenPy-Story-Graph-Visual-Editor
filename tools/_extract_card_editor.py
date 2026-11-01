"""一次性手术刀：把 `BeatCardEditor` 从 main_window.py 切到 ui_qt/card_editor.py。

只搬运，不改函数体。脚本自己算"这段代码用到了哪些顶层导入名"，再生成新模块的
import 头 —— 手抄 30 个导入名是这类搬运最容易出错的地方。
"""
from __future__ import annotations

import ast
import builtins
import pathlib

MW = pathlib.Path('storygraph/ui_qt/main_window.py')
OUT = pathlib.Path('storygraph/ui_qt/card_editor.py')

HEADER = '''"""
storygraph.ui_qt.card_editor
============================
**卡片编辑器**本体：一句台词 / 一个动作的"表单"（`BeatCardEditor`）。

2026-11-01 从 `ui_qt.main_window` 搬出来。这一块 1559 行，是主窗口里
最后一大块**不属于任何 mixin** 的代码 —— 搬之前 `main_window.py` 有 4040 行，
其中 39% 是卡片表单。

为什么它进不了 mixin：mixin 是"主窗口的职责分片"，而 `BeatCardEditor`
是一个**独立的 QWidget**（`MainWindowQt` 只是持有它的实例 `self._editor`），
不是窗口的方法。混进 mixin 会让 `REQUIRES` 白名单长出一堆"这个 mixin 需要
宿主有一个 _editor"的假依赖。

搬完的收益不只是行数：`main_window` 那 35 个方法位的预算（见
`tests/test_main_window_mixins.py`）终于松出两格给 `_build_card_page` /
`_build_toolbars` —— 它们正是把本模块**拼起来**的那两个函数。
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS
from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt.code_edit import CodeEditor
from storygraph.ui_qt.labeled_choices import (as_choices, combo_value,
                                              fill_combo, legacy_pairs,
                                              select_value)
from storygraph.ui_qt.mapping_status import refresh_mapping_status
from storygraph.ui_qt.size_panel import SizePanel
from storygraph.ui_qt.sprite_grid import SpriteGridWidget
from storygraph.ui_qt.stage_thumb import BeatStageThumb
from storygraph.ui_qt.text_html import renpy_text_to_html
from storygraph.ui_qt.wheel_guard import WheelSafeComboBox
from storygraph.ui_qt.window_layout import CARD_STAGE_THUMB
from storygraph.ui_qt.wrap_label import NarrowWrapLabel


'''


def free_names(src: str, drop: set[str]) -> set[str]:
    """这段源码里以"全局名"身份被读到的标识符（排除局部/参数/属性）。"""
    tree = ast.parse(src)
    bound: set[str] = set()
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name)
            a = node.args
            for arg in (list(a.posonlyargs) + list(a.args)
                        + list(a.kwonlyargs)):
                bound.add(arg.arg)
            if a.vararg:
                bound.add(a.vararg.arg)
            if a.kwarg:
                bound.add(a.kwarg.arg)
        elif isinstance(node, ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.Lambda):
            a = node.args
            for arg in (list(a.posonlyargs) + list(a.args)
                        + list(a.kwonlyargs)):
                bound.add(arg.arg)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            else:
                used.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for al in node.names:
                bound.add(al.asname or al.name.split('.')[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    return used - bound - drop


def header_names(src: str) -> set[str]:
    tree = ast.parse(src)
    out: set[str] = set()
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.add(a.asname or a.name.split('.')[0])
    return out


def main() -> None:
    src = MW.read_text(encoding='utf-8')
    tree = ast.parse(src)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == 'BeatCardEditor')
    print(f'BeatCardEditor: 行 {cls.lineno}..{cls.end_lineno}'
          f'（{cls.end_lineno - cls.lineno + 1} 行）')
    lines = src.splitlines(keepends=True)
    body = ''.join(lines[cls.lineno - 1:cls.end_lineno])

    need = free_names(body, drop={'__name__', '__file__', 'self', 'cls'})
    need -= set(dir(builtins))                    # 内建名不用 import
    provided = header_names(HEADER) - {'annotations'}
    print('要用到但头部没给的：', sorted(need - provided))
    print('头部给了但没用到的：', sorted(provided - need))
    assert need <= provided, sorted(need - provided)

    OUT.write_text(HEADER + body.rstrip('\n') + '\n', encoding='utf-8')

    note = (
        '# 卡片编辑器（一句话一张表）搬到 `ui_qt.card_editor`（2026-11-01）：\n'
        '# 它是一个**独立 QWidget**，不是主窗口的方法 —— 1559 行塞在主类文件里，\n'
        '# 只为"少一个文件"。这里 re-export，`from ...main_window import\n'
        '# BeatCardEditor` 的老写法（9 处测试 + 工具）不变。\n'
        'from storygraph.ui_qt.card_editor import BeatCardEditor  # noqa: F401\n'
    )
    new = ''.join(lines[:cls.lineno - 1]) + note + ''.join(lines[cls.end_lineno:])
    MW.write_text(new, encoding='utf-8')

    out_text = OUT.read_text(encoding='utf-8')
    print(f'card_editor.py: {len(out_text.splitlines())} 行')
    print(f'main_window.py: {len(lines)} -> {len(new.splitlines())} 行')
    # 静默吞掉"切完就少了个类"的误操作
    assert 'class BeatCardEditor' in out_text
    assert 'class BeatCardEditor' not in new


if __name__ == '__main__':
    main()
