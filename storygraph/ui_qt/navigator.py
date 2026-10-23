"""
`storygraph.ui_qt.navigator` —— 左侧「章节导航」（项目目录 + 搜索）。
====================================================================

照参考界面学的那一栏（2026-10-23）：常驻左边，一个搜索框 + 一棵
「文件 → 章（label）」的树，**永远看得见整个项目里有什么、现在在哪一章**。

在此之前，想打开别的文件只有两条路：剧情图（只显示导入过的 label），
或者顶上那个下拉框（只显示当前文件里的 label）。项目里几十个文件在界面上
等于不存在 —— 用户得先去 `game/` 里翻文件名。

它不读磁盘、不认识剧本
----------------------
数据全部来自 `app.chapter_index.build_index()`（那是"扫成目录"的唯一实现）；
刷新时机由窗口决定（打开项目 / 手动点 ↻ / 导入章节之后）。这里只做三件事：
**排版、过滤、发信号**：

    open_requested(path, label)    双击 / 回车 → 在卡片界面打开这一章
    refresh_requested()            点 ↻
    import_requested([path, ...])  右键 → 导入到剧情图

两个刻意的取舍
--------------
· **点一下就打开是错的**：这一栏紧贴拍列表，手一滑就会"打开另一章"，
  而换章会重读磁盘。所以打开只走双击 / 回车（与文件管理器一致），
  单击只选中。
· **刷新要有人点**：磁盘上的新文件不会自己冒出来。`↻` 常驻在标题行，
  提示写清"重新扫描 game/ 下的 .rpy"。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMenu, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from storygraph.app import chapter_index as CI
from storygraph.core.safeguard import swallow
from storygraph.ui_qt import theme
from storygraph.ui_qt import tokens as T

#: 搜索的防抖（毫秒）。逐字重建整棵树会让人打字发涩，120ms 后用户已经停手。
SEARCH_DEBOUNCE_MS = 120

#: 真源角色：`Qt.UserRole` 里放 `('file', 路径, '')` / `('label', 路径, label)`。
ROLE_KIND = 'kind'
ROLE_PATH = 'path'
ROLE_LABEL = 'label'


class ChapterNavigator(QWidget):
    """左侧那一栏。**没有项目时也合法**（显示一句怎么开始）。"""

    open_requested = Signal(str, str)          # (绝对路径, label 或 '')
    refresh_requested = Signal()
    import_requested = Signal(list)            # [绝对路径, ...]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._index = CI.ChapterIndex()
        self._root = ''
        self._imported: set = set()            # 已经在剧情图里的 label 名
        self._current = ('', '')               # (路径, label)
        self._collapsed: set = set()           # 用户折叠过的文件（刷新后保持）
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._rebuild)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                               T.SPACE['sm'], T.SPACE['sm'])
        lay.setSpacing(T.SPACE['xs'])

        # ⚠ **不自带标题行**：外面是 `QDockWidget`，它的标题栏已经写着
        # 「章节」。这里再加一行「章节」就是同一个词并排出现两次
        # （2026-10-23 第一版截图里正是这样，白占 24px）。
        # 数量写进 dock 的标题（`window_nav` 负责），`↻` 贴在搜索框右边 ——
        # 那里本来就该有"我搜不到，是不是没刷新"的答案。
        self._summary = ''
        search_row = QHBoxLayout()
        search_row.setSpacing(T.SPACE['xs'])
        self._search = QLineEdit()
        self._search.setPlaceholderText('查找章节 / label…')
        self._search.setClearButtonEnabled(True)
        self._search.setToolTip('按文件名、相对路径或 label 名过滤（大小写不敏感）')
        self._search.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self._search, 1)
        self._refresh_btn = QPushButton('刷新')
        self._refresh_btn.setToolTip(
            '重新扫描 game/ 下的 .rpy（新增/删掉的文件才会出现）')
        self._refresh_btn.setFixedWidth(46)
        self._refresh_btn.clicked.connect(lambda: self.refresh_requested.emit())
        search_row.addWidget(self._refresh_btn, 0)
        lay.addLayout(search_row)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setColumnCount(2)
        self._tree.setIndentation(12)
        self._tree.setUniformRowHeights(True)
        # ⚠ **不要 Qt 自带的展开三角**：整个应用套着 QSS，`QTreeView::branch`
        #   没有样式时分支箭头会被吞掉（2026-10-23 截图里文件行左侧一片空白，
        #   想折叠都找不到点哪儿）。改成自己在文件名前写 `▾ / ▸`，
        #   行单击 = 折叠（与拍列表里 label 分组标题同一套手势）。
        self._tree.setRootIsDecorated(False)
        # 双击不切换展开：单击文件行已经负责这件事，两处都绑会"点了没反应"
        self._tree.setExpandsOnDoubleClick(False)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.itemActivated.connect(self._on_activated)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemCollapsed.connect(self._on_collapsed)
        self._tree.itemExpanded.connect(self._on_expanded)
        self._tree.header().setStretchLastSection(False)
        self._tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.Fixed)
        self._tree.setColumnWidth(1, 64)
        lay.addWidget(self._tree, 1)

        self._hint = QLabel('')
        self._hint.setProperty('role', 'caption')
        self._hint.setWordWrap(True)
        lay.addWidget(self._hint, 0)

        self._now = QLabel('当前：—')
        self._now.setProperty('role', 'caption')
        self._now.setWordWrap(True)
        lay.addWidget(self._now, 0)

        self._apply_empty_hint()

    # ------------------------------------------------------------------
    #  数据入口
    # ------------------------------------------------------------------

    def set_project_root(self, root):
        """换项目根（空 = 关掉项目）。**不自动扫** —— 调用方决定何时 refresh。"""
        self._root = str(root or '')
        self._index = CI.ChapterIndex()
        self._collapsed = set()
        self._rebuild()
        self._apply_empty_hint()
        return self._index

    def index(self) -> CI.ChapterIndex:
        return self._index

    def refresh(self) -> CI.ChapterIndex:
        """重扫项目。**不抛**：扫盘失败会体现在 `index.errors` 里。"""
        if not self._root:
            self._index = CI.ChapterIndex()
            self._rebuild()
            self._apply_empty_hint()
            return self._index
        try:
            self._index = CI.build_index(self._root)
        except Exception as e:                  # noqa: BLE001 - 扫不出来也要能用
            swallow('ui_qt.navigator.refresh', e)
            self._index = CI.ChapterIndex(
                root=self._root, errors=(('', f'扫描失败：{e}'),))
        self._rebuild()
        self._apply_empty_hint()
        return self._index

    def set_imported(self, labels):
        """哪些 label 已经在剧情图里（画 `●`，让人一眼知道"图和项目"的差）。"""
        self._imported = {str(x) for x in (labels or ()) if str(x)}
        self._apply_marks()

    def set_current(self, path, label=''):
        """当前打开的是哪一章（加粗 + 高亮 + 滚到可见）。"""
        key = (str(path or ''), str(label or ''))
        if key == self._current:
            # 翻拍会**每一拍**调一次；同一章内没变化就什么都不做 ——
            # 每次 `scrollToItem` 会把用户手动滚到的位置抢回去。
            return
        self._current = key
        self._apply_marks()
        self._show_current_text()

    def clear(self):
        self._root = ''
        self._index = CI.ChapterIndex()
        self._imported = set()
        self._current = ('', '')
        self._collapsed = set()
        self._rebuild()
        self._apply_empty_hint()

    # ------------------------------------------------------------------
    #  查询（给测试 / 窗口用，不必深入内部）
    # ------------------------------------------------------------------

    def visible_rows(self) -> list:
        """目前树上可见的 `(层级, 主文字, 副文字)` —— 判"有没有过滤对"用。"""
        out: list = []

        def walk(item, depth):
            out.append((depth, item.text(0), item.text(1)))
            for i in range(item.childCount()):
                walk(item.child(i), depth + 1)

        for i in range(self._tree.topLevelItemCount()):
            walk(self._tree.topLevelItem(i), 0)
        return out

    def current_item(self):
        return self._tree.currentItem()

    def focus_search(self):
        self._search.setFocus()
        self._search.selectAll()

    # ------------------------------------------------------------------
    #  排版
    # ------------------------------------------------------------------

    def _query(self) -> str:
        return str(self._search.text() or '').strip()

    def _on_search_changed(self, text: str):
        """
        `textChanged(str)` → 防抖后重建。

        **清空搜索框时不防抖**：那是"我要看全部"这个动作，立刻还原全表
        才跟手（120ms 的等待在"删完最后一个字"时感觉得到）。
        """
        if not str(text or '').strip():
            self._search_timer.stop()
            self._rebuild()
            return
        self._search_timer.start()

    def _rebuild(self):
        hits = self._index.search(self._query())
        self._tree.blockSignals(True)
        try:
            self._tree.clear()
            for hit in hits:
                self._add_file(hit)
        finally:
            self._tree.blockSignals(False)
        self._summary = (f'{self._index.file_count()} 个文件 · '
                         f'{self._index.label_count()} 章'
                         if self._index else '')
        self._apply_empty_hint()

    def summary_text(self) -> str:
        """标题行要显示的数（`1 个文件 · 4 章`）。"""
        return self._summary

    def _add_file(self, hit: CI.SearchHit):
        f = hit.file
        item = QTreeWidgetItem(self._tree)
        item.setText(0, f.name)
        item.setText(1, f'{len(hit.labels)} 章' if len(hit.labels) == f.label_count
                     else f'{len(hit.labels)}/{f.label_count} 章')
        item.setData(0, Qt.UserRole, ('file', f.path, ''))
        item.setToolTip(0, f'{f.rel}\n单击：折叠/展开 · 双击：打开第一个 label ·'
                           f'右键：导入到剧情图')
        item.setToolTip(1, item.toolTip(0))
        self._style_file(item)

        for label in hit.labels:
            child = QTreeWidgetItem(item)
            child.setText(0, label.name)
            child.setText(1, label.describe())
            child.setData(0, Qt.UserRole, ('label', f.path, label.name))
            child.setToolTip(
                0, f'{label.name} · {f.rel} 第 {label.line} 行\n'
                   f'双击：在卡片界面打开这一章')
            child.setToolTip(1, child.toolTip(0))

        # 默认全展开（项目一般十几个文件）；用户折过的保持折着。
        # 搜索时**一律展开** —— 搜完还得点开才看得到结果，等于白搜。
        folded = f.path in self._collapsed and not self._query()
        item.setExpanded(not folded)
        self._apply_row_marks(item)

    def _style_file(self, item: QTreeWidgetItem):
        font = QFont(item.font(0))
        font.setBold(True)
        item.setFont(0, font)
        try:
            item.setForeground(1, QBrush(QColor(theme.t('text.tertiary'))))
        except Exception as e:                  # noqa: BLE001 - 取色失败不影响导航
            swallow('ui_qt.navigator.style_file', e)

    def _apply_marks(self):
        for i in range(self._tree.topLevelItemCount()):
            self._apply_row_marks(self._tree.topLevelItem(i))

    def _apply_row_marks(self, file_item: QTreeWidgetItem):
        """`●` = 图里有；加粗高亮 = 现在打开的就是这一章。"""
        data = file_item.data(0, Qt.UserRole) or ('', '', '')
        path = str(data[1] if len(data) > 1 else '')
        cur_path, cur_label = self._current
        is_current_file = bool(path) and path == cur_path

        any_in = False
        for i in range(file_item.childCount()):
            child = file_item.child(i)
            cdata = child.data(0, Qt.UserRole) or ('', '', '')
            label = str(cdata[2] if len(cdata) > 2 else '')
            inside = label in self._imported
            any_in = any_in or inside
            mark = '● ' if inside else ''
            child.setText(0, f'{mark}{label}')
            font = QFont(child.font(0))
            is_current = is_current_file and label == cur_label
            font.setBold(bool(is_current))
            child.setFont(0, font)
            try:
                color = ('brand.primary' if is_current
                         else 'text.primary' if inside else 'text.secondary')
                child.setForeground(0, QBrush(QColor(theme.t(color))))
            except Exception as e:              # noqa: BLE001
                swallow('ui_qt.navigator.mark_label', e)
            if is_current:
                self._tree.setCurrentItem(child)
                self._tree.scrollToItem(child)

        self._set_file_text(file_item, any_in=any_in)
        self._style_file(file_item)

    def _set_file_text(self, file_item: QTreeWidgetItem, any_in: bool = None):
        """文件行 = `▾ ● ch01.rpy`（箭头 / 已在图里 / 文件名）。"""
        data = file_item.data(0, Qt.UserRole) or ('', '', '')
        path = str(data[1] if len(data) > 1 else '')
        if any_in is None:
            any_in = any(
                str((c.data(0, Qt.UserRole) or ('', '', ''))[2]) in self._imported
                for c in (file_item.child(i) for i in range(file_item.childCount())))
        arrow = '▾' if file_item.isExpanded() else '▸'
        mark = '● ' if any_in else ''
        file_item.setText(0, f'{arrow} {mark}{Path(path).name if path else ""}')

    def _show_current_text(self):
        path, label = self._current
        if not path:
            self._now.setText('当前：—（还没载入章节）')
            return
        name = Path(path).name
        self._now.setText(f'当前：{name}' + (f' · {label}' if label else ''))
        self._now.setToolTip(path)

    def _apply_empty_hint(self):
        """空状态要说"下一步做什么"，不能只是空白。"""
        if self._tree.topLevelItemCount():
            self._hint.setVisible(False)
            self._hint.setText('')
        else:
            self._hint.setVisible(True)
            if not self._root:
                self._hint.setText('还没打开项目。\n用「📁 打开项目」选项目根之后，'
                                   '这里会列出 game/ 下的章节。')
            elif self._query():
                self._hint.setText(f'没有匹配「{self._query()}」的章节。'
                                   f'清空搜索框看全部 {self._index.file_count()} 个文件。')
            elif self._index.errors:
                rel, why = self._index.errors[0]
                self._hint.setText(f'没找到能打开的章（{rel or "项目"}：{why}）。'
                                   f'点「刷新」重新扫描。')
            else:
                self._hint.setText('这个项目里没找到带 label 的 .rpy。'
                                   '章要写成 `label 名字:` 才列得出来。')
        self._show_current_text()

    # ------------------------------------------------------------------
    #  交互
    # ------------------------------------------------------------------

    def _on_collapsed(self, item):
        data = item.data(0, Qt.UserRole) or ('', '', '')
        if data and str(data[1] or ''):
            self._collapsed.add(str(data[1]))
            self._set_file_text(item)

    def _on_expanded(self, item):
        data = item.data(0, Qt.UserRole) or ('', '', '')
        if data and str(data[1] or ''):
            self._collapsed.discard(str(data[1]))
            self._set_file_text(item)

    def _on_item_clicked(self, item, _column=0):
        """单击**文件行** = 折叠 / 展开（它的子项才是章，行本身不是）。"""
        data = item.data(0, Qt.UserRole) or ('', '', '')
        if data and str(data[0] or '') == 'file':
            item.setExpanded(not item.isExpanded())

    def _on_activated(self, item, _column=0):
        """双击 / 回车：只在 label 行上算"打开"。

        **文件行不打开**：双击文件行的直觉是"折叠/展开"（与拍列表里
        label 分组标题一致）。从前这里写的是"打开第一个 label"——
        想收起来的人会莫名其妙被换掉当前这一章。
        """
        data = item.data(0, Qt.UserRole) or None
        if not data:
            return
        kind, path, label = (list(data) + ['', '', ''])[:3]
        if kind == 'label':
            self.open_requested.emit(str(path), str(label))

    def _on_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.UserRole) or ('', '', '')
        kind, path, label = (list(data) + ['', '', ''])[:3]
        menu = QMenu(self)
        if kind == 'label':
            menu.addAction('✎ 在卡片界面打开这一章',
                           lambda: self.open_requested.emit(str(path), str(label)))
            menu.addAction('➕ 导入到剧情图',
                           lambda: self.import_requested.emit([str(path)]))
        else:
            menu.addAction('➕ 把这一章的号都导入剧情图',
                           lambda: self.import_requested.emit([str(path)]))
        menu.addSeparator()
        menu.addAction('↻ 重新扫描', lambda: self.refresh_requested.emit())
        try:
            menu.exec(self._tree.viewport().mapToGlobal(pos))
        except Exception as e:                  # noqa: BLE001 - 弹不出来不该炸
            swallow('ui_qt.navigator.context_menu', e)


__all__ = ['ChapterNavigator', 'SEARCH_DEBOUNCE_MS']
