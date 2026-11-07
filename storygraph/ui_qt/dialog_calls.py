"""
「技术调用」小窗（**高级**，2026-10-30）
========================================

用户原话："自定义模块一定要支撑起复杂的转场调用一类的。"

有些项目的过场不是 Ren'Py 的 `with`，而是自己写的 Python 包装：

    $ my_wipe("left", 0.6)
    $ film_transition("fade", 0.4, layer="screens")

它们住在 `Beat.code` 里，不是"语句"，所以「自定义语句」那套格子装不下。
这个小窗只做一件事：**把包装函数的形状说清楚**，让工具能：

  · 在卡片/Excel 上给它的参数长出可编辑的列（只改那一段，别的一个字节不动）；
  · 预览时按声明的"等价效果"演中间帧（没声明 effect 就如实标认不出）。

⚠ 这是**高级**入口：默认不需要打开。没有这条声明时，`$ my_wipe(...)`
仍然逐字保留（工具只读、不改）；声明只是"多给一双眼睛"。
"""

from __future__ import annotations

from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout,
                               QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from storygraph.core.safeguard import swallow
from storygraph.story import statement_registry as SR
from storygraph.ui_qt import theme
from storygraph.ui_qt import tokens as T


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setProperty('role', 'caption')
    return lab


def parse_columns_text(text: str) -> list:
    """
    `转场@0; 层=layer` → `[{'name','position'}, {'name','kw'}]`（纯函数）。

    语法（写错就跳过，不猜）：
      · `列名@0` = 第 0 个位置参数；
      · `列名=layer` = 关键字参数 `layer=`；
      · 只写列名 = 第 0 个位置参数；
      · 分隔符 `;` / `，` / 换行。
    """
    out = []
    for raw in str(text or '').replace('，', ';').replace('\n', ';').split(';'):
        item = raw.strip()
        if not item:
            continue
        if '=' in item:
            name, _sep, kw = item.partition('=')
            name, kw = name.strip(), kw.strip()
            if name and kw:
                out.append({'name': name, 'kw': kw})
            continue
        if '@' in item:
            name, _sep, pos = item.partition('@')
            name, pos = name.strip(), pos.strip()
            if name and pos.isdigit():
                out.append({'name': name, 'position': int(pos)})
            continue
        out.append({'name': item, 'position': 0})
    return out


def format_columns_text(columns) -> str:
    """`[{'name','position'|'kw'}]` → 一行可编辑文本（`转场@0; 层=layer`）。"""
    parts = []
    for col in (columns or ()):
        if not isinstance(col, dict):
            continue
        name = str(col.get('name') or '').strip()
        if not name:
            continue
        kw = str(col.get('kw') or '').strip()
        if kw:
            parts.append(f'{name}={kw}')
        else:
            parts.append(f'{name}@{int(col.get("position") or 0)}')
    return '; '.join(parts)


class CallDialog(QDialog):
    """技术调用的声明表（调用名 / 中文名 / 等价效果 / 时长参数 / 参数列）。"""

    def __init__(self, parent=None, project_root=None):
        super().__init__(parent)
        self.setWindowTitle('技术调用（高级）')
        theme.apply_to(self)
        self._root = project_root
        self.changed = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(T.SPACE['lg'], T.SPACE['md'],
                               T.SPACE['lg'], T.SPACE['md'])
        lay.setSpacing(T.SPACE['sm'])
        title = QLabel('$ 函数调用（高级）')
        title.setProperty('role', 'panelTitle')
        lay.addWidget(title)
        lay.addWidget(_hint(
            '剧本里写成 `$ my_wipe("left", 0.6)` 的包装函数，不是 Ren\'Py 语句。'
            '在这里声明它的参数形状，卡片/Excel 就会给这些参数长出可编辑的列。'))
        lay.addWidget(_hint(
            '不用手写参数列：点下面「扫一遍剧本」，工具会把剧本里真实出现的'
            '`$ 名字(...)` 找出来、参数按逗号拆好填进去 —— 你只需要改中文名。'
            '（也可以手写：`转场@0` = 第 0 个位置参数，`层=layer` = 关键字参数，'
            '分号隔开。等价效果留空 = 预览如实标「认不出」，不假装淡入。）'))
        lay.addWidget(_hint(
            '中文名填了之后，`with …`（过场）那一格的下拉里就叫这个名字 —— '
            '★ 连下拉里找不到的写法也能在「⋯」候选框里搜到。'
            '（映射表里登记过的同名条目优先，见 `assets/vocabulary.py`）'))

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ['调用名（如 my_wipe）', '中文名（可空，如 闪白转场）',
             '等价效果（可空，如 Dissolve）',
             '时长取第几个参数（可空）', '参数列'])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(140)
        lay.addWidget(self.table, 1)

        tools = QHBoxLayout()
        add = QPushButton('＋ 加一条')
        # ⚠ `clicked` 会带一个 bool 实参；直接接 `add_row(call=None)` 会把
        #   False 当成 `call`（Qt 接线审计会红）。包一层。
        add.clicked.connect(lambda: self.add_row())
        rm = QPushButton('删掉选中')
        rm.clicked.connect(self.remove_row)
        tools.addWidget(add)
        tools.addWidget(rm)
        # ⭐ 2026-11-07：**别再让人写 `转场@0; 层=layer`**。剧本里怎么写的，
        #   就照那个填 —— 参数按逗号拆、名字取原文，人只改中文名。
        self.scan_btn = QPushButton('扫一遍剧本，把用到的调用列出来')
        self.scan_btn.setProperty('variant', 'primary')
        self.scan_btn.setToolTip(
            '扫项目里所有 `$ 名字(...)`，把工具还没登记的那些补进表里'
            '（只加不改，已有的一行都不动）')
        self.scan_btn.clicked.connect(lambda: self.scan_project())
        tools.addWidget(self.scan_btn)
        tools.addStretch(1)
        self.status = _hint('')
        tools.addWidget(self.status, 1)
        lay.addLayout(tools)

        bottom = QHBoxLayout()
        # 2026-11-07：这里只管"这个调用长什么样"，不管"要不要给全章加上"。
        # 但人会先跑到这个窗口来找"批量的入口"（"自定义模块里能不能定义这玩意"）
        # —— 所以给一条路过去，别让人回菜单里再找一遍。
        self.batch_btn = QPushButton('按规则批量加这一行…')
        self.batch_btn.setToolTip(
            '去「按规则批处理」：选"什么时候加"（例如每处立绘变化后）+ '
            '"加什么"，那行代码由工具拼，不用手写')
        self.batch_btn.clicked.connect(lambda: self._go_batch())
        bottom.addWidget(self.batch_btn)
        bottom.addStretch(1)
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        self.save_btn = QPushButton('保存到项目清单')
        self.save_btn.setProperty('variant', 'primary')
        self.save_btn.clicked.connect(self._save)
        bottom.addWidget(self.save_btn)
        lay.addLayout(bottom)

        self.resize(860, 480)
        self._load()

    # ---- 读 / 写 ------------------------------------------------------
    def _load(self):
        """项目里已有的 calls → 表格。读不到就空表（不炸窗）。"""
        try:
            reg = (SR.StatementRegistry.for_project(self._root)
                   if self._root else None)
            # ⚠ 不能写 `if reg`：`StatementRegistry.__bool__` 判的是
            #   **语句表**，只有 calls 的项目是 falsy（MEMORY 里那条
            #   "有 __len__ 的对象别用真值判断"同一个坑）。
            calls = (list(getattr(reg, 'calls', None) or ())
                     if reg is not None else [])
        except Exception as e:                    # noqa: BLE001 - 读不到就空
            swallow('ui_qt.dialog_calls.load', e)
            calls = []
        for call in calls:
            self.add_row(call)

    def add_row(self, call=None):
        call = call if isinstance(call, dict) else {}
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(
            str(call.get('head') or '')))
        self.table.setItem(row, 1, QTableWidgetItem(
            str(call.get('label') or '')))
        self.table.setItem(row, 2, QTableWidgetItem(
            str(call.get('effect') or '')))
        seconds = call.get('seconds_arg')
        self.table.setItem(row, 3, QTableWidgetItem(
            '' if seconds is None else str(seconds)))
        self.table.setItem(row, 4, QTableWidgetItem(
            format_columns_text(call.get('columns') or ())))

    def scan_project(self) -> int:
        """
        扫剧本里 `$ 名字(...)` → 把**还没在表里**的补进来（**只加不改**）。

        为什么是"扫"而不是"让人填"：`$ my_wipe("left", 0.6)` 的形状，
        剧本里已经写得清清楚楚；参数列的 `left@0` 只是它的另一种写法。
        让人把前者翻译成后者，纯属白干 —— 而且很容易写错（写成 `left=0`
        就成了关键字参数，读值那侧会去找 `left=`）。

        返回补了几条（0 = 没有新东西 / 没打开项目，状态栏会说）。
        """
        if not self._root:
            self.status.setText('先打开项目 —— 扫描要读项目里的 .rpy。')
            return 0
        # 大项目要读几百个文件（真机 88 个 rpy 约 0.2s，上千个就 1~2s）——
        # 按钮先变个样，别让人以为"点了没反应"然后连点三下。
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText('正在扫描…')
        self.status.setText('正在读项目里的脚本…')
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
        except (ImportError, RuntimeError):
            pass
        try:
            return self._scan_project_inner()
        finally:
            self.scan_btn.setText('扫一遍剧本，把用到的调用列出来')
            self.scan_btn.setEnabled(True)

    def _scan_project_inner(self) -> int:
        """`scan_project` 的主体（拆出来只为让"扫描中"的收尾走 `finally`）。"""
        from storygraph.authoring.autospec import call_columns, guess_call_heads
        from storygraph.parsing.discover import discover_scripts
        from storygraph.story.source_style import read_text

        try:
            files = list(discover_scripts(self._root))[:400]
        except OSError as e:
            self.status.setText(f'扫不了项目脚本：{e}')
            return 0
        texts = []
        for path in files:
            try:
                # ⚠ `read_text` 返回 **(文本, 风格)** 二元组 —— 直接 append 它，
                #   后面拿到的是 `"('label …', SourceStyle(…))"`，正则一条都匹配
                #   不上，而界面上只显示"没看到任何调用"（判据当场抓到）。
                text, _style = read_text(path)
            except (OSError, UnicodeError, ValueError):
                continue
            if text:
                texts.append(text)
        cands = guess_call_heads(texts)
        if not cands:
            self.status.setText('剧本里没看到"项目自己的函数调用"'
                                '（`$ 名字(...)` 那种）。')
            return 0
        have = set()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.text().strip():
                have.add(item.text().strip())
        added = 0
        for rec in cands:
            head = str(rec.get('head') or '')
            if not head or head in have:
                continue
            columns = call_columns(str(rec.get('sample') or ''), head)
            if not columns:
                continue
            self.add_row({'head': head, 'label': '', 'effect': '',
                          'columns': columns})
            have.add(head)
            added += 1
            if added >= 20:                   # 别把表撑成一面墙
                break
        if added:
            self.status.setText(
                f'从剧本里补了 {added} 条（参数已经拆好，改中文名就行）——'
                '不用的点「删掉选中」。')
        else:
            self.status.setText('剧本里用到的调用都已经在表里了。')
        return added

    def remove_row(self):
        row = self.table.currentRow()
        if row < 0 and self.table.rowCount():
            row = self.table.rowCount() - 1
        if row >= 0:
            self.table.removeRow(row)

    def built_calls(self) -> list:
        """表格 → `calls` 列表（纯；坏行跳过并留在状态里说清）。"""
        out = []
        bad = []
        for row in range(self.table.rowCount()):
            def cell(idx):
                item = self.table.item(row, idx)
                return (item.text() if item else '').strip()
            head = cell(0)
            if not head:
                bad.append(f'第 {row + 1} 行没有调用名')
                continue
            name = cell(1)
            effect = cell(2)
            raw_seconds = cell(3)
            seconds_arg = None
            if raw_seconds:
                if raw_seconds.isdigit():
                    seconds_arg = int(raw_seconds)
                else:
                    bad.append(f'第 {row + 1} 行的"时长参数"不是数字')
            columns = parse_columns_text(cell(4))
            if not columns:
                bad.append(f'第 {row + 1} 行没有参数列')
            out.append({'head': head, 'label': name,
                        'columns': columns,
                        'effect': effect,
                        'seconds_arg': seconds_arg})
        self._bad = bad
        return out

    def _go_batch(self):
        """
        「这一行怎么批量加」→ 交给 `BatchDialog`（**唯一实现，不在这里再写一份**）。

        为什么不开一个新窗口自己拼文本：同一个意图如果两个界面各拼一遍，
        迟早出现"这里写 `layer="master"`、那里写 `layer = master`" ——
        而两边都看不出错。批处理那层的 `batch_recipes.build_text` 是唯一一份。
        """
        if not self._root:
            self.status.setText('先打开项目 —— 批处理要扫项目里的 .rpy。')
            return None
        try:
            from storygraph.ui_qt.dialog_batch import BatchDialog
        except ImportError as e:                  # 打包漏文件时只说话，不炸窗
            self.status.setText(f'打不开批处理窗口：{e}')
            return None
        dlg = BatchDialog(self._root, parent=self)
        if not dlg._items:                        # 一条规则都没有就先摆一条默认的
            dlg.add_rule()
        dlg.exec()
        return dlg

    def _save(self):
        if not self._root:
            self.status.setText('先打开项目 —— 清单要写进项目根目录。')
            return
        calls = self.built_calls()
        bad = list(getattr(self, '_bad', None) or ())
        if bad:
            self.status.setText('还不能保存：' + '；'.join(bad[:3]))
            return
        res = SR.declare_calls(self._root, calls, replace=True)
        if not res.get('ok'):
            self.status.setText('没写进项目清单：' + str(res.get('reason') or '?'))
            return
        self.changed = True
        self.status.setText(f'已保存 {res.get("count", 0)} 条技术调用。'
                            f'重新打开那几拍就能看到新的参数格。')


__all__ = ['CallDialog', 'format_columns_text', 'parse_columns_text']
