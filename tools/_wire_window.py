#!/usr/bin/env python
"""
窗口拆分的**接线**步骤：生成器只搬方法，不动 import 与基类列表 ——
这个脚本补上那三处（并写清为什么基类顺序不能反）。

单独做成脚本（而不是手改）的理由：`main_window.py` 是中文文件，
`edit` 工具在这种文件上容易踩"file changed since it was read"，
而 `_extract_window.py` 每次重跑都会把文件还原成生成器的样子 ——
接线必须是**可重放**的一步，否则重做一次就要手改一次。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MW = ROOT / 'storygraph' / 'ui_qt' / 'main_window.py'

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

OLD_IMPORT = "from storygraph.ui_qt.thumb_loader import ThumbLoader"

OLD_CLS = "class MainWindowQt(QMainWindow):"
NEW_CLS = ("class MainWindowQt(GraphOpsMixin, CardPageMixin, NodeEditMixin,\n"
           "                   WindowExportMixin, WindowAssetsMixin,\n"
           "                   WindowKeyboardMixin, QMainWindow):")

#: 主类 docstring 的开头（生成器搬完方法后它是**原始那段**，
#: 里面写着"QMainWindow 排第一"是错的 —— 实测栽过，改成讲拆分那段）。
OLD_DOC_HEAD = """    \"\"\"
    Qt 版主窗口 —— **两页**：剧情结构图（第一屏）→ 卡片编辑器（点进模块才进）。"""

NEW_DOC_HEAD = '''    """
    Qt 版主窗口 —— **两页**：剧情结构图（第一屏）→ 卡片编辑器（点进模块才进）。

    **职责拆分**（P3-14，2026-10-05）
    --------------------------------
    原本是 3452 行 / 126 个成员的上帝对象（`EditorSession` 的另一半）。
    现在六块各住一个文件，全部做成 mixin —— **调用方一行都不用改**
    （这是搬家不是改契约：菜单、工具栏、信号、`open_editor` 全都没动）：

      · `GraphOpsMixin`（`window_graph_ops.py`，38）——
        结构分析 / 章节导入导出 / 布局存取 / 图级撤销 / 资源表；
      · `CardPageMixin`（`window_card.py`，22）——
        载入单章 / 拍列表 / 导航 / 增删卡片 / 卡片级撤销；
      · `NodeEditMixin`（`window_nodes.py`，12）——
        模块 ↔ 卡片 / 画布增删连边 / 右键菜单；
      · `WindowExportMixin`（`window_export.py`，10）——
        导出落盘 / 体检 / 舞台预览 / 剧本表；
      · `WindowAssetsMixin`（`window_assets.py`，3）—— 图片与下拉候选；
      · `WindowKeyboardMixin`（`window_keyboard.py`，5）—— 键盘与快捷键。

    **本类自己留 36 个成员**，全是"界面骨架 + 页切换 + 谁都要用的工具"：
    三个页构建器（`_build*`）、`_set_page` / `_update_crumb` /
    `_sync_action_states`、状态栏与日志（`_status` / `_log`）、
    静默失败上报（`_swallow_mark` / `_report_swallowed`）、
    图的所有权（`_set_graph`）、属性（`beats` / `_loaded_idx` …）、
    两个预取常量，以及 Qt 重写点（`closeEvent`）。

    **基类顺序：六个 mixin 必须排在 `QMainWindow` 前面**（实测踩过）
    --------------------------------------------------------------------------
    `eventFilter` / `closeEvent` 是 **Qt 虚函数** —— `QObject` 自己就定义了。
    基类写成 `(QMainWindow, WindowKeyboardMixin, …)` 时 MRO 是
    `MainWindowQt → QMainWindow → … → QObject → WindowKeyboardMixin`，
    于是 `QObject.eventFilter` **赢**、mixin 那份成了死代码：

        QTest.keyPress(...)  →  一个键都没接上
        （实测 13 条键盘用例全红：回车、Ctrl+S、裸↓、空格拖拽…，
         而 `hasattr(MainWindowQt, 'eventFilter')` 照样是 True）

    反过来写 MRO 仍然合法（C3 线性化），`super().__init__()` 也照样链到
    `QMainWindow` —— 六个 mixin 都**没有**自己的 `__init__`，链上一路直通。
    这类事故是"搬家不是改契约"里最容易漏的一种：方法**在**（`hasattr` 真），
    **谁先被找到**却变了。由 `tests/test_main_window_mixins.py` 守。

    五个 mixin 的成员在本类里**不许**有同名 —— 同名会让子类那份**静默**赢。'''


def main() -> int:
    t = MW.read_text(encoding='utf-8')
    if OLD_IMPORT not in t:
        print('** 找不到 import 插入点（是不是已经接过线了？）')
        return 1
    t = t.replace(OLD_IMPORT, IMPORTS, 1)

    if OLD_CLS not in t:
        print('** 找不到类定义')
        return 1
    t = t.replace(OLD_CLS, NEW_CLS, 1)

    if OLD_DOC_HEAD not in t:
        print('** 找不到类 docstring 开头（已经改过了？）')
    else:
        t = t.replace(OLD_DOC_HEAD, NEW_DOC_HEAD, 1)

    MW.write_text(t, encoding='utf-8')
    print('接线完成：import + 基类列表 + 类 docstring')
    return 0


if __name__ == '__main__':
    sys.exit(main())
