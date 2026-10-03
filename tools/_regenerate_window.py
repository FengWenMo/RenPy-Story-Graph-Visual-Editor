#!/usr/bin/env python
"""
一次性重做窗口拆分：还原原始 `main_window.py` → 跑修好的生成器 →
把接线改动再打回去。

为什么要这样：第一版生成器把 **Qt 自己的成员**（`statusBar` /
`focusWidget` / `installAncestorOf`…）也算成了"宿主依赖"，于是六个
mixin 的 `REQUIRES` 里混进了宿主根本没有的名字，守卫直接报错。
生成器已修（见 `QT_MEMBERS`），但要**重新生成**才能生效。

接线改动（生成器不管的部分，因为它只搬方法）：
  1. 顶部 import 六个 mixin；
  2. `class MainWindowQt(...)` 的基类列表；
  3. 主类 docstring 换成讲拆分的那一段。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MW = ROOT / 'storygraph' / 'ui_qt' / 'main_window.py'
BAK = ROOT / 'storygraph' / 'ui_qt' / 'main_window.py.bak'

if not BAK.exists():
    print('** 找不到 .bak（原始文件）—— 不能重做')
    sys.exit(1)

# 1. 还原
MW.write_text(BAK.read_text(encoding='utf-8'), encoding='utf-8')
print('已还原 main_window.py')

# 2. 重新生成
r = subprocess.run([sys.executable, str(ROOT / 'tools' / '_extract_window.py')],
                   cwd=ROOT, capture_output=True, text=True,
                   encoding='utf-8', errors='replace')
sys.stdout.write(r.stdout)
if r.returncode != 0:
    sys.stderr.write(r.stderr)
    print('** 生成失败')
    sys.exit(r.returncode)

# 3. 打回接线
t = MW.read_text(encoding='utf-8')

IMPORTS = """from storygraph.ui_qt.thumb_loader import ThumbLoader
# `MainWindowQt` 的五块职责各住一个文件（P3-14，2026-10-05）。
# **基类顺序**：六个 mixin 必须排在 `QMainWindow` **前面** ——
# `eventFilter` / `closeEvent` 是 Qt 虚函数（`QObject` 自己就定义了），
# 排在后面就会被它抢先、mixin 那份成死代码（实测 13 条键盘用例全红，
# 而 `hasattr(MainWindowQt, 'eventFilter')` 照样是 True）。
# 由 `tests/test_main_window_mixins.py` 守。
from storygraph.ui_qt.window_assets import WindowAssetsMixin
from storygraph.ui_qt.window_card import CardPageMixin
from storygraph.ui_qt.window_export import WindowExportMixin
from storygraph.ui_qt.window_graph_ops import GraphOpsMixin
from storygraph.ui_qt.window_keyboard import WindowKeyboardMixin
from storygraph.ui_qt.window_nodes import NodeEditMixin"""

OLD_IMP = "from storygraph.ui_qt.thumb_loader import ThumbLoader"
assert OLD_IMP in t, '找不到 import 插入点'
t = t.replace(OLD_IMP, IMPORTS, 1)

OLD_CLS = "class MainWindowQt(QMainWindow):"
NEW_CLS = ("class MainWindowQt(GraphOpsMixin, CardPageMixin, NodeEditMixin,\n"
           "                   WindowExportMixin, WindowAssetsMixin,\n"
           "                   WindowKeyboardMixin, QMainWindow):")
assert OLD_CLS in t, '找不到类定义'
t = t.replace(OLD_CLS, NEW_CLS, 1)

MW.write_text(t, encoding='utf-8')
print('已打回接线（import + 基类列表）')
