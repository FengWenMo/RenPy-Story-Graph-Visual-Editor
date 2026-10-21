"""
storygraph.ui_qt.window_nodes.py
================================
`MainWindowQt` 的**模块 ↔ 卡片、画布上的增删连边与右键菜单。**

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
from storygraph.ui_qt.qt_args import drop_qt_bool
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


__all__ = ['NodeEditMixin']


def _write_new_chapter(view, path, label: str) -> str:
    """
    给"新建章节"落一个最小骨架（`label 名:` + `return`），返回路径（失败返回空串）。

    三种情形（2026-10-21，用户实测报的"无源文件"和"一个文件里写第二个 label"）：

      · **文件不存在** → 新建它；
      · **文件已存在、里面没有同名 label** → **追加**一段
        `\n\nlabel 名:\n    return\n`（用户原话："我想在一个 rpy 文件里写出
        第二个 label 该怎么操作？" —— 一个文件放多章在 Ren'Py 里完全合法，
        工具以前只会建新文件，把这条路堵死了）；
      · **里面有同名 label** → 一个字都不写（重名 label 会让游戏起不来）。

    追加时按文件自己的**载体风格**（BOM / CRLF）铺回去，不改已有任何一个字节。
    """
    if not path or not str(label or '').strip():
        return ''
    p = Path(path)
    try:
        from storygraph.story.paths import write_text_atomic
        from storygraph.story.source_style import detect as _detect
        from storygraph.story.source_style import read_text as _read

        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(p, f"label {label}:\n    return\n")
            return str(p)

        text, style = _read(p)
        if _has_label(text, label):
            try:
                view._status(f'{p.name} 里已经有 label {label} 了 —— 换个章节名。')
            except Exception:                 # noqa: BLE001
                pass
            return ''
        body = text.rstrip('\n') + f"\n\nlabel {label}:\n    return\n"
        write_text_atomic(p, _detect(p).apply(body))
        return str(p)
    except OSError as e:                      # 权限 / 磁盘：说出来，不静默
        try:
            view._status(f'章节建好了，但文件没写出来：{e}')
        except Exception:                     # noqa: BLE001
            pass
        return ''


def _has_label(text: str, label: str) -> bool:
    """这段源码里有没有 `label <名字>:`（含带参数 / 带 `(` 的写法）。"""
    import re as _re
    return bool(_re.search(
        rf'^label\s+{_re.escape(label)}\s*(?:\(|:)', text or '', _re.M))


class NodeEditMixin:
    """
    模块 ↔ 卡片、画布上的增删连边与右键菜单。

    它要求宿主（`MainWindowQt`）提供 `REQUIRES` 里那些成员 ——
    多半是**主类刻意留下**的：界面骨架、页切换、状态栏、
    静默失败上报、图的所有权。
    """

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    REQUIRES = ('_canvas', '_chapter_label', '_log', '_project_root', '_run_command', '_set_graph', '_status', 'check_logic', 'clear_graph', 'complete_related_chapters', 'edit_node_ops', 'graph', 'import_chapters', 'load_script', 'open_node_in_excel', 'reload_imported_chapters', 'remove_chapter_from_graph')

    def _selected_node(self):
        nid = self._canvas.selected_node_id()
        return (self.graph.nodes or {}).get(nid) if (self.graph and nid) else None
    def _on_canvas_select(self, nid):
        """左键选中模块 → 右栏"当前模块"跟着变（旧版 `_update_chapter_label`）。"""
        # 命令对象要读 `selected_node`（旧版视图接口），这里跟着同步
        self.selected_node = nid
        self.selected_edge = None
        node = (self.graph.nodes or {}).get(nid) if (self.graph and nid) else None
        name = getattr(node, 'label_name', '') if node else ''
        fp = getattr(node, 'file_path', None) if node else None
        if node is None:
            self._chapter_label.setText('（在图上点一个模块）')
        elif fp:
            self._chapter_label.setText(f'{name}　·　{Path(str(fp)).name}')
        else:
            self._chapter_label.setText(
                f'{name}　·　（这一章的文件还没导入：别的章节跳到了它）')
        for btn in (getattr(self, '_open_script_btn', None),
                    getattr(self, '_edit_vars_btn', None)):
            if btn is not None:
                btn.setEnabled(node is not None)
    def _on_connect_requested(self, from_id, to_id):
        """Shift + 左键从一个块拖到另一个块 → 弹旧版那张"选择跳转类型"对话框。"""
        self.add_edge(from_id=from_id, to_id=to_id)
    def _on_node_open(self, nid):
        """进卡片编辑器打开这一章（右栏「✎ 打开本章剧本」/ 右键菜单）。"""
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        if node is None:
            return
        fp = getattr(node, 'file_path', None)
        if not fp or not Path(str(fp)).exists():
            self._status(f'「{node.label_name}」没有关联到源文件'
                         f'（这个块是手工新建的，或者在图上但磁盘上已删）——'
                         f'用「➕ 导入章节 .rpy」重新导入那个文件即可。')
            return
        self.load_script(fp, goto_card=True)
        self._canvas.highlight_node(nid)
    def open_node_in_editor(self, nid):
        """旧版右键菜单里的「在编辑器中打开」：用系统默认程序打开那个 .rpy。"""
        from storygraph.graph.editor_utils import open_with_system_default
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        fp = getattr(node, 'file_path', None) if node else None
        if not fp or not Path(str(fp)).exists():
            self._status('这个块没有关联到磁盘上的 .rpy，打不开。')
            return
        if open_with_system_default(fp):
            self._log(f'[打开] 已用系统默认程序打开 {Path(str(fp)).name}')
        else:
            self._status(f'没能调用系统默认程序，请手动打开：{fp}')
    def build_node_menu(self, nid=None) -> QMenu:
        """
        拼出右键菜单（**不 exec**，所以能测"哪些项被置灰了"）。

        条目与旧版 `GraphView.menu` 对齐。旧版有而上一版被我漏掉的：
        「在编辑器中打开」。旧版叫「改本章台词（卡片）」的那条保持原名
        （用户认得这个名字）。
        """
        menu = QMenu(self)
        node = (self.graph.nodes or {}).get(nid) if (self.graph and nid) else None
        if node is not None:
            name = getattr(node, 'label_name', '')
            fp = getattr(node, 'file_path', None)
            has_src = bool(fp and Path(str(fp)).exists())

            targets = [
                menu.addAction(f'改本章台词（卡片）「{name}」',
                               (lambda: self._on_node_open(nid)) if has_src
                               else None),
                menu.addAction('在编辑器中打开',
                               (lambda: self.open_node_in_editor(nid))
                               if has_src else None),
                menu.addAction('用 Excel 打开此章节',
                               (lambda: self.open_node_in_excel(nid))
                               if has_src else None),
            ]
            if not has_src:
                # 没有源文件的块点了也没用 —— 置灰比"点完再弹一句"友好
                for a in targets:
                    a.setEnabled(False)
                    a.setToolTip('该块未关联源文件'
                                 '（手工新建的块，或先「➕ 导入章节」）')
            menu.addSeparator()
            menu.addAction('设为当前章节',
                           lambda: self._canvas.highlight_node(nid))
            menu.addAction('编辑剧情变量…', lambda: self.edit_node_ops(nid))
            menu.addAction('从这里连一条到…', lambda: self.add_edge(from_id=nid))
            menu.addSeparator()
            # 「移除这一章」按**文件**整章移除（用户心智里一章 = 一个 .rpy）；
            # 「删除这个块」只删这一个 label。两者都在，别混为一谈。
            if fp:
                menu.addAction(f'从剧情图移除「{Path(str(fp)).name}」整章',
                               lambda: self.remove_chapter_from_graph(nid))
            menu.addAction('删除这个块…', lambda: self._delete_node(nid))
        else:
            menu.addAction('添加新章节…', self.add_node)
            menu.addAction('添加连线…', self.add_edge)
            menu.addSeparator()
            menu.addAction('自动布局', self._canvas.auto_layout)
            menu.addAction('适应窗口', self._canvas.fit_to_content)
            menu.addAction('检查逻辑问题', self.check_logic)
            menu.addSeparator()
            # 画布右键 = 剧情图的家务事，所以放"导入章节 / 清空图"，
            # **不放**资源表的动作（那是另一条路，混在一起用户会以为
            # "重扫项目"会把整部戏画出来 —— 正是上一版最大的误解）。
            menu.addAction('导入章节 .rpy…', self.import_chapters)
            menu.addAction('补全相关章节', self.complete_related_chapters)
            menu.addAction('重载已导入章节', self.reload_imported_chapters)
            menu.addAction('清空剧情图', self.clear_graph)
        return menu
    def _on_node_menu(self, nid, global_pos):
        self.build_node_menu(nid).exec(global_pos)
    def add_node_at(self, x, y):
        """双击空白 = 在那个场景坐标新建章节（旧版 `on_double_click` 的分支）。"""
        return self.add_node(at=(x, y))
    def add_node(self, at=None):
        """
        新建一个章节模块（**校验在对话框里**，不合法就地提示）。

        `at` 给场景坐标时放在那里（双击空白 = 在这里新建，旧版行为）；
        不给就放视野中央（工具栏按钮）。
        """
        from storygraph.ui_qt.dialogs import AddNodeDialog
        existing = [getattr(n, 'label_name', '')
                    for n in ((self.graph.nodes or {}).values()
                              if self.graph else [])]
        # **默认写到 <项目根>/game/**（Ren'Py 只扫这一个目录）。
        # 没有项目根（没点「打开项目」）时填空 → 对话框里那一格留空，
        # 行为与从前一致（只在图里画结构）。
        default_dir = (str(Path(self._project_root) / 'game')
                       if getattr(self, '_project_root', None) else '')
        dlg = AddNodeDialog(self, existing=existing, default_dir=default_dir)
        if dlg.exec() != QDialog.Accepted:
            return
        # **把"无源文件"变成"有源文件"**（2026-10-21 用户实测报的那条：
        # 新建章节之后双击进去"打开章节剧本不能用"）。
        # 先把最小骨架写出来（`label 名:` + `return`），节点就带上 file_path，
        # 双击直接进编辑器。对话框里那一格留空 → 不建文件（旧行为）。
        # `getattr`：测试里喂的是**假对话框**（只实现 name/operations），
        # 真对话框才有这一格。
        _want_file = getattr(dlg, 'file_path', '') or ''
        wrote = _write_new_chapter(self, _want_file, dlg.name)
        if self.graph is None:
            from storygraph.graph.models import Graph
            self._set_graph(Graph())
        at = drop_qt_bool(at, context='ui_qt.add_node')
        if at is None:
            center = self._canvas.mapToScene(
                self._canvas.viewport().rect().center())
            at = (center.x(), center.y())
        from storygraph.graph.commands import AddNodeCommand
        cmd = AddNodeCommand(self, dlg.name, float(at[0]), float(at[1]),
                             operations=([dlg.operations]
                                         if dlg.operations else None),
                             file_path=(str(_want_file) if wrote else None))
        self._run_command(cmd)
        # 命令 execute 之后才知道新块的 id（它自己 add_node 拿的）
        nid = getattr(cmd, 'node_id', None)
        if nid is not None:
            self._canvas.highlight_node(nid)
            self._canvas.select_node(nid, center=False)
        if wrote:
            self._status(f'已新建章节「{dlg.name}」并写出 {Path(wrote).name}'
                         f'—— 现在双击它就能编辑剧本。Ctrl+Z 可撤销。')
        else:
            self._status(f'已新建章节「{dlg.name}」（只在图里，还没有源文件；'
                         f'双击后可用「导出」给它建立 .rpy）。Ctrl+Z 可撤销。')
    def add_edge(self, from_id=None, to_id=None):
        """
        加一条连线。

        两个入口共用这一份：**Shift + 左键从 A 拖到 B**（旧版做法）和
        右键菜单「从这里连一条到…」/「添加连线…」。
        """
        from storygraph.ui_qt.dialogs import AddEdgeDialog
        if self.graph is None or len(self.graph.nodes or {}) < 2:
            self._status('至少要有两个模块才能连线'
                         '（先用「➕ 导入章节 .rpy」加几章，或新建章节）。')
            return
        from_id = drop_qt_bool(from_id, context='ui_qt.add_edge')
        if from_id is None:
            from_id = self._canvas.selected_node_id()
        dlg = AddEdgeDialog(self, nodes=self.graph.nodes, from_id=from_id,
                            to_id=to_id)
        if dlg.exec() != QDialog.Accepted:
            return
        from storygraph.graph.commands import AddEdgeCommand
        self.selected_node = dlg.from_id
        self.selected_edge = None
        self._run_command(AddEdgeCommand(self, dlg.from_id, dlg.to_id,
                                         dlg.etype, dlg.condition,
                                         dlg.option_text))
        names = {nid: getattr(n, 'label_name', '')
                 for nid, n in (self.graph.nodes or {}).items()}
        self._log(f'[连线] {names.get(dlg.from_id, dlg.from_id)} → '
                  f'{names.get(dlg.to_id, dlg.to_id)}（{dlg.etype}'
                  f'{("，" + dlg.condition) if dlg.condition else ""}'
                  f'{("「" + dlg.option_text + "」") if dlg.option_text else ""}）')
        self._status(f'已连线 {dlg.from_id} → {dlg.to_id}（{dlg.etype}）。')
    def delete_edge(self, eid):
        if self.graph is None or eid not in (self.graph.edges or {}):
            return
        from storygraph.graph.commands import RemoveEdgeCommand
        self._run_command(RemoveEdgeCommand(self, eid))
        self._status('已删除这条连线（Ctrl+Z 可撤销）。')
    def _delete_node(self, nid):
        if self.graph is None:
            return
        node = self.graph.nodes.get(nid)
        if node is None:
            return
        if QMessageBox.question(
                self, '删除块',
                f'删除「{node.label_name}」及其连线？（只改内存里的图，不动文件；'
                f'Ctrl+Z 可撤销）'
        ) != QMessageBox.Yes:
            return
        from storygraph.graph.commands import RemoveNodeCommand
        self._run_command(RemoveNodeCommand(self, nid))
