#!/usr/bin/env python
"""
把 `MainWindowQt`（3452 行 / 120 方法）按职责拆成六个 mixin。

与拆 `EditorSession` 用的是同一套方法（`ast` 按**名字**定位 + 脚本自检）：

  1. 按名字定位，不手写行号（行号会漂，名字是契约）；
  2. **装饰器算进起始行**（`@property` 在上面一行 —— 前两次搬迁都因此
     留下过孤立装饰器，把**后面**那个方法静默变成静态方法）；
  3. 搬完自检：两边都能 `ast.parse`、没有孤立装饰器、方法集合恰好对、
     原处无残留、基类顺序正确。

这次的额外难点：**Qt 的 `__init__` 必须留在主类**（它调 `super().__init__()`
链到 `QMainWindow`），而且 `eventFilter` / `closeEvent` 是 Qt 重写点，
也只能留在主类。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'storygraph' / 'ui_qt' / 'main_window.py'

#: `目标文件名 → (mixin 类名, [方法名…], 模块 docstring 摘要)`
#: **按职责分组**，同一组内按原文件顺序搬。
GROUPS = {
    'window_graph_ops.py': (
        'GraphOpsMixin',
        (
            # 剧情变量
            '_edit_selected_vars', 'edit_node_ops',
            # 结构分析
            '_write_analysis', '_run_analysis', 'check_logic',
            'show_analysis', 'clear_analysis', '_show_analysis_text',
            # 资源表
            'open_project', 'open_project_path', 'rescan_project',
            # 图的范围
            'import_chapters', 'import_chapter_files',
            '_report_missing_chapters', 'complete_related_chapters',
            'reload_imported_chapters', 'clear_graph',
            'remove_chapter_from_graph',
            # 画布布局
            'save_layout', 'load_layout', 'export_control_flow',
            '_refresh_choices_now',
            # 更多工具
            '_show_more_tools', 'export_other_engine', 'show_asset_overview',
            'register_missing_assets', 'validate_current_script',
            'show_collab',
            # 图级撤销
            '_on_node_moved', 'on_graph_structure_changed',
            'on_graph_content_changed', 'on_current_changed',
            'update_node_position', '_apply_layout_data', '_run_command',
            'undo_graph', 'redo_graph', '_sync_history_buttons',
        ),
        '剧情图那一半：结构分析 / 章节导入导出 / 布局存取 / 图级撤销。',
    ),
    'window_card.py': (
        'CardPageMixin',
        (
            '_on_load', 'load_script',
            '_sync_graph_highlight', '_reload_list', '_make_item',
            'refresh_list_row',
            '_show_index', '_prefetch_neighbours',
            'apply_current_beat', 'save_current_beat', 'undo_current_beat',
            '_sync_undo_button',
            '_update_nav', '_sync_nav', '_on_select',
            'next_beat', 'prev_beat', '_fill_speaker',
            'insert_card', 'delete_card', 'restore_card',
            '_sync_structural_buttons',
        ),
        '卡片页那一半：载入单章 / 拍列表 / 导航 / 增删卡片 / 卡片级撤销。',
    ),
    'window_nodes.py': (
        'NodeEditMixin',
        (
            '_selected_node', '_on_canvas_select', '_on_connect_requested',
            '_on_node_open', 'open_node_in_editor', 'build_node_menu',
            '_on_node_menu', 'add_node_at', 'add_node', 'add_edge',
            'delete_edge', '_delete_node',
        ),
        '模块 ↔ 卡片、画布上的增删连边与右键菜单。',
    ),
    'window_export.py': (
        'WindowExportMixin',
        (
            'export_script', '_confirm_force_overwrite',
            'open_node_in_excel', 'export_from_table',
            '_source_rpy_for_table', '_refresh_table_candidates',
            '_show_unregistered', 'show_health', 'open_stage_preview',
            'show_help',
        ),
        '导出 / 体检 / 舞台预览 / 剧本表 —— 与外部世界打交道的那一批。',
    ),
    'window_assets.py': (
        'WindowAssetsMixin',
        ('_pick_image', '_refresh_asset_choices', '_pick_asset_for'),
        '资产选择：图片 / 下拉候选（资源表经会话取，不自己读表）。',
    ),
    'window_keyboard.py': (
        'WindowKeyboardMixin',
        (
            'eventFilter', '_event_belongs_here', '_install_shortcuts',
            '_caret_at_edge', '_remove_shortcuts',
        ),
        '键盘与快捷键：回车下一拍、↑/↓ 到边才翻页、Ctrl+Z/Y 等。',
    ),
}

#: **Qt 自己提供的**成员 —— 不算"宿主依赖"，`REQUIRES` 里不该有它们。
#:
#: 怎么来的：生成器扫 `self.X` 的**读**，而 `self.statusBar()` /
#: `self.focusWidget()` 都是 `QMainWindow` 的方法，不是本项目的成员。
#: 不排掉的话每个 mixin 的 `REQUIRES` 会混进 3~8 个 Qt 名字，
#: 守卫就会报"宿主根本没有 `focusWidget`"（第一版实测 4 个 mixin 全红）。
#:
#: 判据取"Qt 真实成员"白名单，**不用前缀猜** —— 本项目的私有成员
#: 大量以下划线开头，按前缀排除会连 `_canvas` / `_session` 一起排掉。
QT_MEMBERS = {
    # 窗口 / 控件
    'statusBar', 'setWindowTitle', 'windowTitle', 'resize', 'close',
    'setCentralWidget', 'centralWidget', 'addToolBar', 'addDockWidget',
    'menuBar', 'setStatusBar', 'setWindowIcon', 'setMinimumSize',
    'setAcceptDrops', 'setMouseTracking', 'rect', 'size', 'width', 'height',
    'parent', 'parentWidget', 'window', 'geometry', 'pos',
    # 事件 / 过滤器（`eventFilter` 是我们自己重写的，不算）
    'installEventFilter', 'removeEventFilter', 'focusWidget', 'rect',
    'mapToGlobal', 'mapFromGlobal', 'mapTo', 'mapFrom',
    'isAncestorOf', 'setFocus', 'hasFocus', 'update', 'repaint', 'setEnabled',
    'isEnabled', 'setVisible', 'isVisible', 'show', 'hide', 'setParent',
    'findChild', 'findChildren', 'setShortcut', 'children', 'deleteLater',
    'setAttribute', 'setCursor', 'setToolTip', 'setWhatsThis',
    # 控件树操作
    'setLayout', 'layout',
}

KEEP = {
    '__init__': 'Qt 的 super().__init__() 必须在这里链到 QMainWindow',
    '_registry_provider': '构造 EditorSession 时就要传进去，最早的一环',
    '_ensure_registry': '上面那个的配套',
    'beats': '属性：会话拍的快捷读法，全窗口都在用',
    '_loaded_idx': '属性',
    '_file_path': '属性',
    '_symbols': '属性',
    'current_page': '属性',
    '_boot': '启动时序（QTimer.singleShot 调它）',
    '_set_graph': '图的所有权在这里（换图要连带重置选中/历史）',
    '_build': '搭整个界面骨架',
    '_build_graph_page': '第 0 页构建',
    '_build_card_page': '第 1 页构建',
    '_build_toolbars': '两组工具栏（随页切换）',
    '_set_page': '页切换：两组工具栏跟着显隐',
    '_focus_into_card_page': '页切换的配套',
    '_sync_action_states': '全局动作可用性（它读的东西最多）',
    '_update_crumb': '面包屑',
    '_back_to_graph': '回结构页',
    '_open_selected_in_card': '页切换 + 载入，两头都碰',
    '_on_zoom_changed': '画布缩放 → 状态栏',
    '_log': '控制台，谁都能写',
    'clear_console': '控制台',
    '_swallow_mark': '静默失败标记（`core.safeguard`），谁都能用',
    '_report_swallowed': '上面那个的配套',
    '_is_text_widget': '事件过滤器要用，但语义是"界面探针"',
    '_text_input_has_focus': '同上',
    '_record_applied': '一次编辑的落账（图 + 卡片都调）',
    '_status': '状态栏，谁都能写',
    'closeEvent': 'Qt 重写点：关闭时收尾（快捷键、缩略图线程）',
    'PAGE_GRAPH': '类常量：页索引',
    'PAGE_CARD': '类常量：页索引',
    'EMPTY_HINT': '类常量：空态提示',
    'ASSET_FIELD_KINDS': '类常量：字段→资产种类映射（主类与资产块都用）',
    'PREFETCH_AHEAD': '类常量：往后预取几拍（卡片块读它）',
    'PREFETCH_BEHIND': '类常量：往前预取几拍（卡片块读它）',
}

MIXIN_HEADER = '''"""
storygraph.ui_qt.{mod}
{underline}
`MainWindowQt` 的**{summary}**

为什么拆（P3-14，2026-10-05）
-----------------------------
`MainWindowQt` 原本是 3452 行 / 120 方法的上帝对象 —— `EditorSession`
（已拆成 5 块）的另一半。它比那边更难拆，因为：

  · 它是 **UI 层**：行为测试薄得多，而且要真起 Qt（`test_ui_qt.py`）；
  · 方法之间靠 **Qt 信号**耦合，不像会话那样能按"主语是什么"干净切开。

所以顺序是**先补网、再拆**：`tests/test_main_window_snapshot.py`
（63 步操作序列 golden）先把外部可观察行为钉住，
拆分期间它必须一直绿。

**做成 mixin 而不是独立对象**：调用方（菜单、工具栏、信号、
`open_editor`）一行都不用改 —— 这是**搬家**不是改契约。

与主类的隐式依赖
----------------
见每个类的 `REQUIRES`：那是**实测**（静态扫 `self.*`）出来的，
由 `tests/test_main_window_mixins.py` 守着。
"""
from __future__ import annotations

from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QSplitter, QStackedWidget, QToolBar,
                               QVBoxLayout, QWidget)

from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.graph_canvas import GraphCanvas
from storygraph.ui_qt.sprite_grid import SpriteGridWidget
from storygraph.ui_qt.status import Notifier, Toast
from storygraph.ui_qt.thumb_loader import ThumbLoader


__all__ = ['{cls}']
'''

MIXIN_CLASS_HEADER = '''

class {cls}:
    """
    {summary}

    它要求宿主（`MainWindowQt`）提供 `REQUIRES` 里那些成员 ——
    多半是**主类刻意留下**的：界面骨架、页切换、状态栏、
    静默失败上报、图的所有权。
    """

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    REQUIRES = {requires}

'''


def _assign_names(node) -> list:
    """
    一条赋值的**所有**目标名，含元组解包。

    `PREFETCH_AHEAD, PREFETCH_BEHIND = 2, 1`（类体里真有这一条）的目标是
    `ast.Tuple` 而不是 `ast.Name` —— 第一版只认 `ast.Name`，
    于是这两个类常量**既没被搬走、也没被算进覆盖面**，
    脚本报"一个都不漏"，实际却把它们**从主类里删掉了**：
    搬到别处的 `self.PREFETCH_AHEAD` 会在运行时 `AttributeError`。

    这类"清单漏项"是自动搬迁最危险的失败模式：脚本自检全绿，
    错误只在跑到那条路时出现。所以这里**两个方向都补**：
    取值时认元组，覆盖面核对时也算进去。
    """
    out = []
    for tg in node.targets:
        if isinstance(tg, ast.Name):
            out.append(tg.id)
        elif isinstance(tg, (ast.Tuple, ast.List)):
            out.extend(e.id for e in tg.elts if isinstance(e, ast.Name))
    return out


def _spans(text: str) -> dict:
    """`MainWindowQt` 的成员名 → (起, 止)；**起行把装饰器算进去**。"""
    tree = ast.parse(text)
    cls = [n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == 'MainWindowQt'][0]
    out = {}
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list)
            out[node.name] = (start, node.end_lineno)
        elif isinstance(node, ast.Assign):
            for name in _assign_names(node):
                out[name] = (node.lineno, node.end_lineno)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target,
                                                            ast.Name):
            out[node.target.id] = (node.lineno, node.end_lineno)
    return out


def _orphans(text: str) -> list:
    lines = text.splitlines()
    bad = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s.startswith('@') or s.startswith('@dataclass'):
            continue
        if i > 0 and lines[i - 1].strip().startswith('@'):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        nxt = lines[j].lstrip() if j < len(lines) else ''
        if not (nxt.startswith('def ') or nxt.startswith('class ')
                or nxt.startswith('@')):
            bad.append((i + 1, s))
    return bad


def _self_attrs(node) -> tuple:
    """(`self.X` 的读集合, 写集合) —— 用来生成 REQUIRES 的候选。"""
    loads, stores = set(), set()
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == 'self'):
            (loads if isinstance(sub.ctx, ast.Load) else stores).add(sub.attr)
    return loads, stores


def main():
    text = SRC.read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)
    spans = _spans(text)

    # —— 名字核对：每个要搬的方法都必须真的存在 ——
    planned = [(dst, cls, m) for dst, (cls, ms, _s) in GROUPS.items()
               for m in ms]
    missing = [m for _d, _c, m in planned if m not in spans]
    unknown_keep = [k for k in KEEP if k not in spans]
    if missing:
        print('** 这些方法在 MainWindowQt 里找不到：')
        for m in missing:
            print('   ', m)
        return 1
    if unknown_keep:
        print('** KEEP 里这些名字不在类里（清单过期了）：', unknown_keep)
        return 1

    # —— 覆盖面核对：一个成员都不许漏 ——
    moved = {m for _d, _c, m in planned}
    all_members = set(spans)
    uncovered = all_members - moved - set(KEEP)
    if uncovered:
        print('** 这些成员既没搬走也没写进 KEEP（会留在主类里，'
              '但没人知道该不该留）：')
        for m in sorted(uncovered):
            print('   ', m)
        return 1

    # —— 区间重叠检查 ——
    picked = sorted((spans[m][0], spans[m][1], m) for m in moved)
    for (a1, b1, n1), (a2, _b2, n2) in zip(picked, picked[1:]):
        if a2 <= b1:
            print(f'** 区间重叠：{n1}({a1}-{b1}) 与 {n2}({a2}-…)')
            return 1

    print(f'原 {len(lines)} 行 / 类里 {len(all_members)} 个成员')
    print(f'  搬走 {len(moved)} 个 → {len(GROUPS)} 个 mixin；'
          f'留下 {len(KEEP)} 个')

    # —— 逐组生成新文件 ——
    tree = ast.parse(text)
    cls_node = [n for n in tree.body if isinstance(n, ast.ClassDef)
                and n.name == 'MainWindowQt'][0]
    by_name = {n.name: n for n in cls_node.body
               if isinstance(n, ast.FunctionDef)}

    for dst, (cls, methods, summary) in GROUPS.items():
        chunks = sorted(((spans[m][0], spans[m][1]) for m in methods))
        body = ''.join(''.join(lines[a - 1:b]) for a, b in chunks)

        # REQUIRES：实测"读了、但不是自己提供的、也不是 Qt 自己的"
        own = set(methods)
        need = set()
        for m in methods:
            loads, _stores = _self_attrs(by_name[m])
            for attr in loads:
                if attr not in own and attr not in QT_MEMBERS:
                    need.add(attr)
        requires = tuple(sorted(need))

        underline = '=' * len(f'storygraph.ui_qt.{dst}')
        blob = (MIXIN_HEADER.format(mod=dst, underline=underline,
                                    summary=summary, cls=cls)
                + MIXIN_CLASS_HEADER.format(
                    cls=cls, summary=summary,
                    requires='(' + ', '.join(repr(r) for r in requires)
                             + (',' if len(requires) == 1 else '') + ')')
                + body)
        try:
            ast.parse(blob)
        except SyntaxError as e:
            print(f'** {dst} 语法错误：{e}')
            return 1
        o = _orphans(blob)
        if o:
            print(f'** {dst} 有孤立装饰器：{o}')
            return 1
        got = {n.name for n in ast.parse(blob).body
               if isinstance(n, ast.ClassDef) and n.name == cls}
        if not got:
            print(f'** {dst} 里没有 {cls}')
            return 1
        (SRC.parent / dst).write_text(blob, encoding='utf-8')
        print(f'  ✓ {dst:26} {len(blob.splitlines()):5} 行  '
              f'{len(methods):2} 方法  REQUIRES={len(requires)}')

    # —— 从主文件里删掉搬走的部分 ——
    kept = list(lines)
    for a, b, _m in sorted(picked, key=lambda t: -t[0]):
        del kept[a - 1:b]
    kept_text = ''.join(kept)
    try:
        ast.parse(kept_text)
    except SyntaxError as e:
        print(f'** 剩余 main_window.py 语法错误：{e}')
        return 1
    o = _orphans(kept_text)
    if o:
        print(f'** 剩余 main_window.py 有孤立装饰器：{o}')
        return 1
    left = set()
    for node in ast.walk(ast.parse(kept_text)):
        if isinstance(node, ast.FunctionDef):
            left.add(node.name)
        elif isinstance(node, ast.Assign):
            left |= set(_assign_names(node))
    dup = moved & left
    if dup:
        print('** main_window.py 里没删干净：', sorted(dup))
        return 1

    # —— 反向核对：**主类原有的成员一个都不许凭空消失** ——
    #
    # 这就是上面 `_assign_names` 那个 bug 的补丁：覆盖面核对只保证
    # "124 个成员都被分配了去处"，但如果 `_spans` 自己**看不见**某个
    # 成员（元组解包那次），它就不在 124 里 —— 脚本照样报"不漏"。
    # 所以再从**原文件**独立取一遍名字，与"搬走的 + 留下的"对账。
    orig_names = set()
    for node in cls_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            orig_names.add(node.name)
        elif isinstance(node, ast.Assign):
            orig_names |= set(_assign_names(node))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target,
                                                            ast.Name):
            orig_names.add(node.target.id)
    accounted = moved | set(KEEP)
    vanished = sorted(orig_names - accounted)
    if vanished:
        print('** 这些主类成员既没搬走也没进 KEEP，'
              '但脚本却没报（`_spans` 可能看不见它们）：', vanished)
        return 1

    SRC.write_text(kept_text, encoding='utf-8')
    print(f'  ✓ main_window.py {len(kept_text.splitlines())} 行'
          f'（原 {len(lines)}）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
