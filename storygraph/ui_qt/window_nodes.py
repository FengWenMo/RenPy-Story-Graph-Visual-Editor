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
                               QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSplitter, QStackedWidget, QToolBar,
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
            except Exception as e:            # noqa: BLE001 - 状态栏坏了不影响判断
                swallow('ui_qt.window_nodes.dup_label_status', e)
            return ''
        body = text.rstrip('\n') + f"\n\nlabel {label}:\n    return\n"
        write_text_atomic(p, _detect(p).apply(body))
        return str(p)
    except OSError as e:                      # 权限 / 磁盘：说出来，不静默
        try:
            view._status(f'章节建好了，但文件没写出来：{e}')
        except Exception as e2:               # noqa: BLE001 - 状态栏坏了也得往下走
            swallow('ui_qt.window_nodes.write_status', e2)
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
    REQUIRES = ('_canvas', '_chapter_label', '_log', '_project_root', '_run_command', '_session', '_set_graph', '_status', 'check_logic', 'clear_graph', 'complete_related_chapters', 'edit_node_ops', 'graph', 'import_chapter_files', 'import_chapters', 'load_script', 'open_node_in_excel', 'reload_imported_chapters', 'remove_chapter_from_graph')

    def _selected_node(self):
        nid = self._canvas.selected_node_id()
        return (self.graph.nodes or {}).get(nid) if (self.graph and nid) else None
    def _source_info(self, node) -> dict:
        """
        这个节点**现在**属于哪种来源状态（2026-10-22 用户反馈）。

        从前只用一个 `file_path` 布尔值，把三种完全不同的情况混成
        「（无源文件）」：

          · `bound`   有路径且文件在 → 能打开卡片；
          · `found`   没有路径，但项目里能找到声明它的 `.rpy`
                      （别的章节 jump/call 过来的占位块）→ 可一键导入绑定；
          · `manual`  没有路径，项目里也找不到 → 手工节点，要新建/绑定；
          · `missing` 有路径，但磁盘上文件没了 → 要重新定位或新建。

        状态不同，右栏主按钮就该不同 —— 不能再出现"可点但必然报错"。
        """
        label = str(getattr(node, 'label_name', '') or '')
        fp = getattr(node, 'file_path', None)
        if fp and Path(str(fp)).exists():
            return {'kind': 'bound', 'label': label, 'file': str(fp)}
        if fp:
            return {'kind': 'missing', 'label': label, 'file': str(fp)}
        root = getattr(self, '_project_root', None)
        if root and label:
            try:
                from storygraph.parsing.discover import find_label_files
                found = find_label_files(root, [label])
            except Exception as e:                # noqa: BLE001 - 扫描失败按手工处理
                swallow('ui_qt.window_nodes.source_info', e)
                found = {}
            path = found.get(label)
            if path:
                return {'kind': 'found', 'label': label, 'file': str(path)}
        return {'kind': 'manual', 'label': label, 'file': ''}

    def _on_canvas_select(self, nid):
        """左键选中模块 → 右栏"当前模块"和**主按钮**跟着状态变。"""
        # 命令对象要读 `selected_node`（旧版视图接口），这里跟着同步
        self.selected_node = nid
        self.selected_edge = None
        node = (self.graph.nodes or {}).get(nid) if (self.graph and nid) else None
        info = self._source_info(node) if node is not None else {}
        self._selected_source_info = info
        name = getattr(node, 'label_name', '') if node else ''
        if node is None:
            self._chapter_label.setText('（在图上点一个模块）')
        elif info.get('kind') == 'bound':
            self._chapter_label.setText(
                f'{name}　·　{Path(str(info["file"])).name}')
        elif info.get('kind') == 'found':
            self._chapter_label.setText(
                f'{name}　·　未绑定（在 {Path(str(info["file"])).name} 里找到）')
        elif info.get('kind') == 'missing':
            self._chapter_label.setText(
                f'{name}　·　源文件丢失：{Path(str(info["file"])).name}')
        else:
            self._chapter_label.setText(f'{name}　·　手工节点，还没有源文件')

        btn = getattr(self, '_open_script_btn', None)
        if btn is not None:
            kind = info.get('kind')
            if kind == 'bound':
                btn.setText('✎ 打开本章剧本（改台词）')
                btn.setToolTip('进入卡片编辑器改这一章的台词（也可右键模块 → 改本章台词）')
                btn.setEnabled(True)
            elif kind == 'found':
                btn.setText('🔗 导入并绑定源文件')
                btn.setToolTip(
                    f'项目里的 {Path(str(info["file"])).name} 声明了「{name}」；'
                    f'点一下导入该文件并把节点绑上去')
                btn.setEnabled(True)
            elif kind in ('manual', 'missing'):
                btn.setText('🩹 给这个节点建/绑源文件')
                btn.setToolTip('还没有可编辑的 .rpy：可以新建一个，或绑定已有文件')
                btn.setEnabled(True)
            else:
                btn.setText('✎ 打开本章剧本（改台词）')
                btn.setEnabled(False)

        edit_btn = getattr(self, '_edit_vars_btn', None)
        if edit_btn is not None:
            edit_btn.setEnabled(node is not None)

    def _on_connect_requested(self, from_id, to_id):
        """Shift + 左键从一个块拖到另一个块 → 弹旧版那张"选择跳转类型"对话框。"""
        self.add_edge(from_id=from_id, to_id=to_id)

    def _on_node_open(self, nid):
        """进卡片编辑器打开这一章（右栏主按钮 / 右键菜单）。"""
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        if node is None:
            return
        info = self._source_info(node)
        if info.get('kind') != 'bound':
            # 没有源文件时**不要**先报一句错再让用户自己找入口：
            # 主按钮就是修复入口（2026-10-22 用户："怎么把无源搞成有源？"）。
            self._repair_node_source(nid, info)
            return
        self.load_script(info['file'], goto_card=True, label=node.label_name)
        self._canvas.highlight_node(nid)

    def _repair_node_source(self, nid, info=None):
        """
        无源节点的修复入口（可重复调用）：

          · 占位块（项目里找得到声明）→ 导入那个文件并绑定；
          · 手工节点 → 新建 `.rpy` / 绑定已有文件；
          · 源文件丢失 → 在原路径新建 / 重新定位。
        """
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        if node is None:
            return
        info = info or self._source_info(node)
        kind = info.get('kind')
        label = str(getattr(node, 'label_name', '') or '')

        if kind == 'found':
            path = info.get('file') or ''
            ans = QMessageBox.question(
                self, '导入并绑定源文件',
                f'「{label}」在项目里的这个文件里：\n{path}\n\n'
                f'现在导入它，并把这个节点绑上去？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ans == QMessageBox.Yes:
                self._import_and_open(path, label)
            return

        if kind == 'missing':
            path = info.get('file') or ''
            choice = self._repair_choice(
                '源文件丢失',
                f'「{label}」记着这个文件，但磁盘上已经不在了：\n{path}\n\n'
                f'要在原路径重新建一个最小骨架，还是重新定位到别的文件？',
                [('new', '在原路径新建'), ('locate', '重新定位…'),
                 ('cancel', '取消')])
            if choice == 'new':
                wrote = _write_new_chapter(self, path, label)
                if wrote:
                    self._import_and_open(wrote, label)
            elif choice == 'locate':
                self._locate_and_bind(label, start=Path(path).parent)
            return

        # 手工节点：先看项目里有没有同名 label —— 有就直接给"导入并绑定"，
        # 没有才让用户新建/绑定。
        root = getattr(self, '_project_root', None)
        candidates = []
        if root:
            try:
                from storygraph.parsing.discover import find_label_files
                found = find_label_files(root, [label])
                if found.get(label):
                    candidates = [str(found[label])]
            except Exception as e:                # noqa: BLE001
                swallow('ui_qt.window_nodes.repair_find', e)
        if candidates:
            ans = QMessageBox.question(
                self, '导入并绑定源文件',
                f'项目里已经有「{label}」：\n{candidates[0]}\n\n'
                f'导入它并绑定到这个节点？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ans == QMessageBox.Yes:
                self._import_and_open(candidates[0], label)
            return

        choice = self._repair_choice(
            '给节点配源文件',
            f'「{label}」现在只是图上的一个块，还没有 `.rpy`。\n\n'
            f'新建一个源文件，或者绑定项目里已有的文件？',
            [('new', '新建 .rpy…'), ('locate', '绑定已有文件…'),
             ('cancel', '取消')])
        if choice == 'new':
            self._create_source_for_node(label, nid)
        elif choice == 'locate':
            self._locate_and_bind(label, start=None)

    def _repair_choice(self, title: str, text: str, buttons) -> str:
        """带自定义按钮文字的询问框 → 返回按钮 key（没有则 'cancel'）。"""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Question)
        made = {}
        for key, label in buttons:
            role = (QMessageBox.Cancel if key == 'cancel'
                    else QMessageBox.AcceptRole)
            made[key] = box.addButton(label, role)
        box.exec()
        for key, button in made.items():
            if box.clickedButton() is button:
                return key
        return 'cancel'

    def _create_source_for_node(self, label: str, nid=None):
        """给手工节点新建一个最小 `.rpy`，导入并打开。"""
        root = getattr(self, '_project_root', None)
        default = (str(Path(root) / 'game' / f'{label}.rpy')
                   if root else f'{label}.rpy')
        path, _ = QFileDialog.getSaveFileName(
            self, '给节点新建源文件', default, "Ren'Py 脚本 (*.rpy)")
        if not path:
            return
        if not path.lower().endswith('.rpy'):
            self._status('源文件必须以 .rpy 结尾。')
            return
        wrote = _write_new_chapter(self, path, label)
        if wrote:
            self._import_and_open(wrote, label)

    def _locate_and_bind(self, label: str, start=None):
        """选一个已有的 `.rpy`，确认里面真有这个 label，再导入绑定。"""
        start_dir = str(start or (Path(self._project_root or '.') / 'game'))
        path, _ = QFileDialog.getOpenFileName(
            self, f'找到声明「{label}」的 .rpy', start_dir,
            "Ren'Py 脚本 (*.rpy)")
        if not path:
            return
        try:
            from storygraph.story.source_style import read_text
            text = read_text(Path(path))[0]
        except OSError as e:
            self._status(f'读不了这个文件：{e}')
            return
        if not _has_label(text, label):
            self._status(f'{Path(path).name} 里没有 label「{label}」——'
                         f'没有绑定（避免把节点指到错的文件）。')
            return
        self._import_and_open(path, label)

    def _import_and_open(self, path: str, label: str):
        """导入一个文件（会更新同名节点的 `file_path`），再打开那个 label。"""
        res = self.import_chapter_files([path]) or {}
        if not res.get('ok', True):
            self._status(f'导入 {Path(path).name} 失败：'
                         f'{res.get("error") or "未知原因"}')
            return
        node = next((n for n in ((self.graph.nodes or {}).values()
                                 if self.graph else [])
                     if str(getattr(n, 'label_name', '') or '') == label),
                    None)
        if node is None:
            self._status(f'{Path(path).name} 导入完了，但图里没找到「{label}」——'
                         f'检查文件里 label 的写法。')
            return
        self.load_script(path, goto_card=True, label=label)
        self._canvas.highlight_node(node.id)
        self._status(f'✔ 已把「{label}」绑定到 {Path(path).name}，'
                     f'并打开它的第一拍。')
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
                # 没有源文件的块：**先给修复入口**，再把三条"打开"置灰。
                # 从前只有置灰，用户完全不知道该点哪里（2026-10-22 实测）。
                info = self._source_info(node)
                label = ('导入并绑定源文件…' if info.get('kind') == 'found'
                         else '新建 / 绑定源文件…')
                menu.addAction(label, lambda: self._repair_node_source(nid))
                # 没有源文件的块点了也没用 —— 置灰比"点完再弹一句"友好
                for a in targets:
                    a.setEnabled(False)
                    a.setToolTip('该块未关联源文件'
                                 '（手工新建的块，或先「➕ 导入章节」）')
            else:
                # 同一个 .rpy 里加第二个 label：不用离开工具、不用手打路径。
                menu.addAction('在本文件新建 label…',
                               lambda: self.add_label_to_file(nid))
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
        known = []
        try:
            known = self._session.graph_files(self.graph) if self.graph else []
        except Exception as e:                  # noqa: BLE001 - 下拉没有也不影响手填
            swallow('ui_qt.window_nodes.known_files', e)
        dlg = AddNodeDialog(self, existing=existing, default_dir=default_dir,
                            known_files=known)
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
                         f'右键它 →「新建 / 绑定源文件…」，'
                         f'或点右栏主按钮就能配源文件）。Ctrl+Z 可撤销。')

    def add_label_to_file(self, nid=None):
        """
        在**这个节点所属的同一个 `.rpy`** 里追加一个新 label，并打开它。

        用户 2026-10-22：
        > 我想在一个 rpy 文件里写出第二个 label 该怎么操作？
        > 我就不打开编辑器！我就要在这个工具内改！

        与「新建章节 → 写入文件填已有路径」是同一个落盘动作，但这里
        文件已经确定，只问 label 名 —— 不需要用户手打完整路径。
        """
        node = ((self.graph.nodes or {}).get(nid) if (self.graph and nid)
                else self._selected_node())
        if node is None:
            self._status('先在图上点一个模块，再在它的文件里新建 label。')
            return
        fp = getattr(node, 'file_path', None)
        if not fp or not Path(str(fp)).exists():
            self._status('这个块还没有源文件 —— 先用「新建 / 绑定源文件…」。')
            self._repair_node_source(node.id)
            return
        from storygraph.ui_qt.dialogs import valid_label
        name, ok = QInputDialog.getText(
            self, '在本文件新建 label',
            f'文件：{Path(str(fp)).name}\n新 label 名（可以用点号，如 main.route）：')
        name = str(name or '').strip()
        if not ok or not name:
            return
        if not valid_label(name):
            self._status('label 名不合法：只能字母/数字/下划线，'
                         '可用点号分段，且不能以数字开头。')
            return
        if any(str(getattr(n, 'label_name', '') or '') == name
               for n in (self.graph.nodes or {}).values()):
            self._status(f'图上已经有「{name}」了 —— 换个名字。')
            return
        try:
            from storygraph.story.source_style import read_text
            text = read_text(Path(fp))[0]
        except OSError as e:
            self._status(f'读不了 {Path(fp).name}：{e}')
            return
        if _has_label(text, name):
            self._status(f'{Path(fp).name} 里已经有 label「{name}」了。')
            return
        wrote = _write_new_chapter(self, str(fp), name)
        if not wrote:
            return
        from storygraph.graph.commands import AddNodeCommand
        cmd = AddNodeCommand(self, name, float(node.x) + 60.0,
                             float(node.y) + 160.0, file_path=str(fp))
        self._run_command(cmd)
        if getattr(cmd, 'node_id', None) is not None:
            self._canvas.highlight_node(cmd.node_id)
            self._canvas.select_node(cmd.node_id, center=False)
        self.load_script(str(fp), goto_card=True, label=name)
        self._status(f'✔ 已在 {Path(fp).name} 里追加 label「{name}」，'
                     f'并打开它的第一拍。Ctrl+Z 只撤图上的块，文件改动需手动撤。')

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
