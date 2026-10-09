"""
storygraph.ui_qt.window_keyboard.py
===================================
`MainWindowQt` 的**键盘与快捷键：回车下一拍、↑/↓ 到边才翻页、Ctrl+Z/Y 等。**

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


__all__ = ['WindowKeyboardMixin']


class WindowKeyboardMixin:
    """
    键盘与快捷键：回车下一拍、↑/↓ 到边才翻页、Ctrl+Z/Y 等。

    它要求宿主（`MainWindowQt`）提供 `REQUIRES` 里那些成员 ——
    多半是**主类刻意留下**的：界面骨架、页切换、状态栏、
    静默失败上报、图的所有权。
    """

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    REQUIRES = ('PAGE_CARD', '_back_to_graph', '_canvas', '_delete_node', '_list', '_open_selected_in_card', '_page', '_text_input_has_focus', '_who', 'check_logic', 'delete_card', 'insert_card', 'load_layout', 'next_beat', 'prev_beat', 'redo_graph', 'save_current_beat', 'save_layout', 'undo_current_beat', 'undo_graph')

    def _filter_structural_keys(self, event, key, mods, obj, on_card):
        """结构页那批按键（回车/Delete/Ctrl+S…）—— eventFilter 里“不在卡片页”那一支（M4 搬出，行为一字不改）。块内**每条路径都 return**，所以外面直接 return 等价。"""
        if not on_card:
            # 结构页：回车 = 进选中模块的卡片；Delete = 删块
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._open_selected_in_card()
                return True
            if key == Qt.Key_Delete:
                nid = self._canvas.selected_node_id()
                if nid is not None:
                    self._delete_node(nid)
                    return True
            # 旧版结构页的 Ctrl+S / Ctrl+O（保存 / 加载布局）与 Ctrl+A（分析）——
            # 「功能缺失那么多」里有它们，这里按页补回（卡片页的 Ctrl+S/Z 另算）。
            if key == Qt.Key_S and mods & Qt.ControlModifier:
                self.save_layout()
                return True
            if key == Qt.Key_O and mods & Qt.ControlModifier:
                self.load_layout()
                return True
            if key == Qt.Key_A and mods & Qt.ControlModifier:
                self.check_logic()
                return True
            # 图级撤销 / 重做（旧版 Ctrl+Z / Ctrl+Y）
            if key == Qt.Key_Z and mods & Qt.ControlModifier:
                self.undo_graph()
                return True
            if key == Qt.Key_Y and mods & Qt.ControlModifier:
                self.redo_graph()
                return True
            return super().eventFilter(obj, event)

    def eventFilter(self, obj, event):
        """
        键盘契约（**必须真的装上**，见 `_install_shortcuts`）。

        历史坑一：这个方法一直在，但从来没有人 `installEventFilter` ——
        于是 Ctrl+S / 回车翻页 / Alt+↑↓ **全都是死的**。
        历史坑二（两页导航之后）：这些键**只在卡片页有意义**。
        在结构图上按回车应该"进这一章的卡片"，而不是翻上一章留下的拍号 ——
        所以卡片页才有的键在这里按页分流。
        """
        from PySide6.QtCore import QEvent
        # **窗口失活 = 手势与按键状态全部作废**（用户 2026-10-05："拖拽画布
        # 时灵时不灵"）。切走窗口（Alt+Tab / 弹了别的程序）时，空格键的
        # KeyRelease 根本收不到 —— `_space_down` 会**卡在 True**：之后每次
        # 左键拖块都变成平移（块拖不动、点选像没反应）；反过来若卡在 False，
        # 就是"空格 + 左键拖没反应"。鼠标释放同样会丢 → `_panning` 卡住。
        # 所以失活时必须显式清干净，恢复时状态才是确定的。
        if event.type() == QEvent.WindowDeactivate:
            self._canvas.set_space_down(False)
            self._canvas.cancel_interaction()
            return super().eventFilter(obj, event)
        # 空格 = "按住可平移画布"（旧版绑在整窗上）。必须在**窗口级**跟踪：
        # 画布的 `keyPressEvent` 只在它有键盘焦点时才收到，而用户按空格时
        # 焦点往往在工具栏/右栏 —— 那就是"空格+左键没反应"的原因。
        # 但在文本框里敲空格必须照常输入，所以下面会跳过文本控件。
        if event.type() in (QEvent.KeyPress, QEvent.KeyRelease) \
                and event.key() == Qt.Key_Space \
                and not (event.modifiers() & ~Qt.KeypadModifier):
            pressed = event.type() == QEvent.KeyPress
            if not self._text_input_has_focus(obj):
                self._canvas.set_space_down(pressed)
                if pressed:
                    return True          # 不让空格继续传（免得上层再滚一屏）
                return super().eventFilter(obj, event)
            self._canvas.set_space_down(False)
            return super().eventFilter(obj, event)
        if event.type() != QEvent.KeyPress:
            return super().eventFilter(obj, event)
        # 别人家的窗口（弹出的对话框、下拉列表）在忙，不抢键。
        # 但**本窗口的控件树**要放行 —— 台词框就在这棵树里。
        if not self._event_belongs_here(obj):
            return super().eventFilter(obj, event)

        key = event.key()
        mods = event.modifiers()
        on_card = self._page == self.PAGE_CARD

        # Esc：从卡片页回结构页（结构页不拦）
        if key == Qt.Key_Escape:
            if on_card:
                self._back_to_graph()
                return True
            return super().eventFilter(obj, event)

        if not on_card:
            return self._filter_structural_keys(event, key, mods, obj, on_card)

        # Ctrl+S：与工具栏 action 的快捷键同一个落点（两处都能按，语义只有一份）
        if key == Qt.Key_S and mods & Qt.ControlModifier:
            self.save_current_beat()
            return True
        # Ctrl+Z = 撤销这一拍的修改（与"保存这一拍"配对）。
        # 刻意**不接** Ctrl+Shift+Z / Ctrl+Y：那是"重做"，
        # 而这里只有单级撤销 —— 摆一个假的重做按钮比没有更糟。
        if key == Qt.Key_Z and mods & Qt.ControlModifier:
            self.undo_current_beat()
            return True        # 增 / 删卡片（结构编辑）：Ctrl+Insert / Ctrl+Delete。
        # 用组合键而不是裸 Insert/Delete：裸键留给"编辑文字"的习惯，
        # 免得在台词框里按 Delete 就把整张卡片删了。
        if key == Qt.Key_Insert and mods & Qt.ControlModifier:
            self.insert_card()
            return True
        if key == Qt.Key_Delete and mods & Qt.ControlModifier:
            self.delete_card()
            return True
        # **回车 = 下一拍**（用户明确："回车是下一张卡片的意思"）
        if key in (Qt.Key_Return, Qt.Key_Enter) and \
                not (mods & Qt.ShiftModifier):
            self.next_beat()
            return True
        # 翻页优先用 Alt+↑/↓：多行台词框里的 ↑/↓ 必须留给移动光标
        if mods & Qt.AltModifier:
            if key == Qt.Key_Down:
                self.next_beat()
                return True
            if key == Qt.Key_Up:
                self.prev_beat()
                return True

        if key not in (Qt.Key_Down, Qt.Key_Up):
            return super().eventFilter(obj, event)

        # 焦点在章节列表里时**完全放行**：列表自己移动选择 = 换一拍，
        # 我们再翻一次就会一次跳两拍（tkinter 版踩过这个坑）。
        fw = self.focusWidget()
        if isinstance(fw, QListWidget) and fw in (self._list, self._who):
            return super().eventFilter(obj, event)
        # 多行台词框：光标不在边上时把 ↑/↓ 让给移动光标
        if isinstance(fw, QPlainTextEdit) and \
                not self._caret_at_edge(fw, key == Qt.Key_Down):
            return super().eventFilter(obj, event)
        # 别的控件（单行输入 / 组合框 / 卡片）里的一行文本没有"上下"可言
        # → 直接翻页。单行控件要放行的话，↑/↓ 会变成"切换下拉项"。
        if isinstance(fw, (QComboBox, QLineEdit)):
            if key == Qt.Key_Down:
                self.next_beat()
                return True
            self.prev_beat()
            return True
        if key == Qt.Key_Down:
            self.next_beat()
            return True
        self.prev_beat()
        return True
    def _event_belongs_here(self, obj) -> bool:
        """这个按键事件是不是属于本窗口的控件树？（弹出对话框不算）"""
        try:
            w = obj if isinstance(obj, QWidget) else None
            if w is None:
                return False
            if w is self or self.isAncestorOf(w):
                return True
            # 组合框的下拉列表 / 卡片里的子控件：往上找有没有本窗口
            p = w.parentWidget()
            while p is not None:
                if p is self:
                    return True
                p = p.parentWidget()
            return False
        except Exception as e:                       # noqa: BLE001
            swallow('ui_qt.window_keyboard._event_belongs_here', e)
            return False
    def _install_shortcuts(self):
        """
        把 `eventFilter` 真的挂上去（**不挂就等于没有键盘**）。

        挂两次是故意的：
          · QApplication —— 覆盖"焦点在台词框/列表里"的按键；
          · self        —— 兜住"窗口自己收到"的按键（没有子控件吃走时）。
        两者都可能被调用，但每个按键只会命中一次（事件顺序是
        app 过滤器 → 对象过滤器 → 控件自己）。所以要在两处都装，
        并在命中后 `return True` 终止传播。
        """
        try:
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(self)
        except Exception:                       # noqa: BLE001
            swallow('main_window._install_shortcuts')
        self.installEventFilter(self)
    @staticmethod
    def _caret_at_edge(edit, down: bool) -> bool:
        try:
            cur = edit.textCursor().blockNumber()
            return (cur >= edit.blockCount() - 1) if down else cur <= 0
        except Exception as e:                       # noqa: BLE001
            swallow('ui_qt.window_keyboard._caret_at_edge', e)
            return True
    def _remove_shortcuts(self):
        """逆着 `_install_shortcuts` 摘干净（幂等）。"""
        for target in (QApplication.instance(), self):
            if target is None:
                continue
            try:
                target.removeEventFilter(self)
            except Exception:                   # noqa: BLE001
                swallow('main_window._remove_shortcuts')
