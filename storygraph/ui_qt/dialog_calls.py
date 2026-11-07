"""
「技术调用」小窗（**高级**，2026-10-30；2026-11-07 第四轮改参数表）
==================================================================

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

2026-11-07 第四轮改了两件事（都是"能骗人"的地方）
--------------------------------------------------
1. **参数那一格不再是私有语法**：从前要人写 `转场@0; 层=layer`，同一个
   `columns` 形状在扩展窗口是字段表、在这里要背符号。现在是
   `call_params.CallParamsDialog` 那张小表（位置 / 关键字两格分开填）。
2. **保存落到"生效的那个文件"里**：探针
   `.pip-tmp/probe_call_owner.py` 实测过 —— 一个 head 如果在
   `storygraph.ext.json` 里声明过，这里改完写进 `storygraph.rules.json`
   是**永远读不到**的（装配顺序扩展优先），而界面还回一句"已保存"。
   现在按 `origin` 分流（`statement_registry.save_calls`）：归扩展 / 插件管的
   那几条**不写**，并说清去哪个窗口改（`_go_extensions`）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout,
                               QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from storygraph.core.safeguard import swallow
from storygraph.story import statement_registry as SR
from storygraph.ui_qt import theme
from storygraph.ui_qt import tokens as T

#: 表格的列（**后两列只读**：一列是参数的摘要、一列说"这条归谁管"）。
_HEADERS = ['调用名（如 my_wipe）', '中文名（可空，如 闪白转场）',
            '等价效果（可空，如 Dissolve）',
            '时长取第几个参数（可空）',
            '参数（点下面「改这一行的参数…」）', '这条归谁']

#: 参数摘要 / 来源两列的下标（读的人别数错；`built_calls` 也照这个读）
_COL_NAME, _COL_LABEL, _COL_EFFECT, _COL_SECONDS, _COL_PARAMS, _COL_OWNER = \
    0, 1, 2, 3, 4, 5

#: 行数据（`Qt.UserRole`）里放的东西：`{'columns': [...], 'origin': '...'}`
_ROW_DATA = Qt.UserRole


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setProperty('role', 'caption')
    return lab


def parse_columns_text(text: str) -> list:
    """
    `转场@0; 层=layer` → `[{'name','position'}, {'name','kw'}]`（纯函数）。

    ⚠ 2026-11-07 第四轮起**界面不再让人写这个**（参数是
      `call_params.CallParamsDialog` 那张表）。留着它是因为：
      ① 老判据 / 命令行 / 剪贴板里可能还有这种一行写法；
      ② 表格里那一列**摘要**就是它生成的（同一份写法，只用于显示）。

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
    """`[{'name','position'|'kw'}]` → 一行可读摘要（`转场@0; 层=layer`）。"""
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


def owner_text(origin) -> str:
    """`origin` → 界面上"这条归谁"（`规则文件` / `扩展` / `插件：xxx.json`）。"""
    owner = SR.call_owner(origin)
    text = str(origin or '')
    if owner == 'plugin':
        return '插件：' + text.split(':', 1)[-1]
    return SR.CALL_OWNER_LABELS.get(owner, owner)


class CallDialog(QDialog):
    """技术调用的声明表（调用名 / 中文名 / 等价效果 / 时长参数 / 参数表）。"""

    def __init__(self, parent=None, project_root=None):
        super().__init__(parent)
        self.setWindowTitle('技术调用（高级）')
        theme.apply_to(self)
        self._root = project_root
        self.changed = False
        #: 打开时读到的东西：`{head: origin}` —— 用来在保存时说清"这条归谁"，
        #: 以及"你在表格里删掉的那条其实删不掉"（它住在扩展 / 插件里）。
        self._loaded: dict = {}

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
            '不用手写参数：点「扫一遍剧本」，工具会把剧本里真实出现的'
            '`$ 名字(...)` 找出来、参数按逗号拆好填进去；要改就选中那一行点'
            '「改这一行的参数…」（一张小表：位置 / 关键字分两格填）。'
            '等价效果留空 = 预览如实标「认不出」，不假装淡入。'))
        lay.addWidget(_hint(
            '中文名填了之后，`with …`（过场）那一格的下拉里就叫这个名字 —— '
            '★ 连下拉里找不到的写法也能在「⋯」候选框里搜到。'
            '（映射表里登记过的同名条目优先，见 `assets/vocabulary.py`）'))

        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        hh = self.table.horizontalHeader()
        for col in (_COL_NAME, _COL_LABEL, _COL_EFFECT, _COL_SECONDS,
                    _COL_OWNER):
            hh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(_COL_PARAMS, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(150)
        self.table.itemSelectionChanged.connect(self._on_row_picked)
        lay.addWidget(self.table, 1)

        tools = QHBoxLayout()
        add = QPushButton('＋ 加一条')
        # ⚠ `clicked` 会带一个 bool 实参；直接接 `add_row(call=None)` 会把
        #   False 当成 `call`（Qt 接线审计会红）。包一层。
        add.clicked.connect(lambda: self.add_row())
        rm = QPushButton('删掉选中')
        rm.clicked.connect(self.remove_row)
        self.params_btn = QPushButton('改这一行的参数…')
        self.params_btn.setToolTip(
            '一张小表：参数名 / 显示名 / 位置（0、1、2…）/ 关键字名 —— '
            '位置和关键字二选一')
        self.params_btn.clicked.connect(lambda: self.edit_params())
        tools.addWidget(add)
        tools.addWidget(rm)
        tools.addWidget(self.params_btn)
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
        lay.addLayout(tools)
        # 状态单独一行（2026-11-07 第四轮）：保存完那一句可能有四五行的信息量
        # （写了几条 / 哪几条归扩展管 / 清掉了什么），塞在按钮那一行里会把
        # 表格挤窄 —— 而这扇窗的表格本来就有 6 列。
        self.status = _hint('')
        lay.addWidget(self.status)

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
        # 反过来的一条路：这条归扩展管时，形状在那边改（见 `edit_params`）
        self.ext_btn = QPushButton('这条在扩展窗口里改…')
        self.ext_btn.setToolTip(
            '打开「扩展」窗口并选中这一条 —— 归扩展 / 插件管的调用，'
            '参数形状住在那边（字段表的「位置 / 关键字名」那一列就是'
            '这里的位置和关键字），写进规则文件是读不到的')
        self.ext_btn.clicked.connect(lambda: self._go_extensions())
        bottom.addWidget(self.ext_btn)
        bottom.addStretch(1)
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        self.save_btn = QPushButton('保存到项目清单')
        self.save_btn.setProperty('variant', 'primary')
        self.save_btn.clicked.connect(self._save)
        bottom.addWidget(self.save_btn)
        lay.addLayout(bottom)

        self.resize(980, 520)
        self._load()

    # ---- 读 / 写 ------------------------------------------------------
    def _load(self):
        """项目里已有的 calls → 表格。读不到就空表（不炸窗）。"""
        self._loaded = {}
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
            if isinstance(call, dict):
                self._loaded[str(call.get('head') or '')] = str(
                    call.get('origin') or '')
            self.add_row(call)

    def add_row(self, call=None):
        call = call if isinstance(call, dict) else {}
        origin = str(call.get('origin') or '')
        columns = [dict(c) for c in (call.get('columns') or ())
                   if isinstance(c, dict)]
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, _COL_NAME, QTableWidgetItem(
            str(call.get('head') or '')))
        self.table.setItem(row, _COL_LABEL, QTableWidgetItem(
            str(call.get('label') or '')))
        self.table.setItem(row, _COL_EFFECT, QTableWidgetItem(
            str(call.get('effect') or '')))
        seconds = call.get('seconds_arg')
        self.table.setItem(row, _COL_SECONDS, QTableWidgetItem(
            '' if seconds is None else str(seconds)))
        self._set_columns(row, columns, origin)

    def _set_columns(self, row: int, columns, origin: str):
        """把参数 / 来源落到那一行（并**按归谁管**决定这一行能不能改）。"""
        item = QTableWidgetItem(format_columns_text(columns))
        item.setData(_ROW_DATA, {'columns': [dict(c) for c in columns],
                                 'origin': origin})
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, _COL_PARAMS, item)
        owner = QTableWidgetItem(owner_text(origin))
        owner.setFlags(owner.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, _COL_OWNER, owner)
        self._set_row_editable(row, SR.call_owner(origin) == 'rules')

    def _set_row_editable(self, row: int, editable: bool):
        """
        这一行**能不能在这里改**。

        归扩展 / 插件管的调用，形状住在别的文件里：在这里改只会在内存里变一下，
        保存时按 `save_calls` 被挡住 —— 那正是"改了没反应"。所以干脆把格子
        设成只读，并在悬停里说清去哪改。
        """
        why = ('这条的形状住在别处：点下面「这条在扩展窗口里改…」'
               '（写进规则文件会被扩展盖住，读不到）')
        for col in (_COL_NAME, _COL_LABEL, _COL_EFFECT, _COL_SECONDS):
            item = self.table.item(row, col)
            if item is None:
                continue
            flags = item.flags()
            if editable:
                item.setFlags(flags | Qt.ItemIsEditable)
            else:
                item.setFlags(flags & ~Qt.ItemIsEditable)
                item.setToolTip(why)

    def _on_row_picked(self):
        """
        选中一行就说清**这一行能不能在这里改**。

        为什么不在保存时才说：那时人已经改完了。选中即提示，省一次白改
        （而且上一句提示会跟着换掉 —— 别让"归扩展管"那句挂在一行规则文件
        的条目上）。
        """
        row = self.table.currentRow()
        if row < 0:
            return
        data = self._row_data(row)
        head = self._cell(row, _COL_NAME)
        if SR.call_owner(data.get('origin')) == 'rules':
            self.status.setText(f'「{head}」归规则文件管 —— '
                                f'改完点「保存到项目清单」才写盘。')
            return
        self.status.setText(
            f'「{head}」归{owner_text(data.get("origin"))}管 —— 这条请点下面'
            f'「这条在扩展窗口里改…」，在这里写不会生效。')

    def _row_data(self, row: int) -> dict:
        item = self.table.item(row, _COL_PARAMS)
        data = item.data(_ROW_DATA) if item is not None else None
        return dict(data) if isinstance(data, dict) else {}

    def _cell(self, row: int, col: int) -> str:
        item = self.table.item(row, col)
        return (item.text() if item is not None else '').strip()

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
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
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
            head = self._cell(row, _COL_NAME)
            if head:
                have.add(head)
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

    def edit_params(self, row=None):
        """
        选中那一行的参数 → `CallParamsDialog`（一张小表，不是一行语法）。

        归扩展 / 插件管的行**不开编辑器**：形状在那边，在这里改不会生效
        （`save_calls` 会挡住）—— 与其让人改完发现没变，不如当场指路。
        """
        from storygraph.ui_qt.call_params import CallParamsDialog

        row = self.table.currentRow() if row is None else int(row)
        if row < 0 or row >= self.table.rowCount():
            self.status.setText('先在表里点一行，再改它的参数。')
            return None
        data = self._row_data(row)
        owner = SR.call_owner(data.get('origin'))
        head = self._cell(row, _COL_NAME)
        if owner != 'rules':
            self.status.setText(
                f'「{head}」归{owner_text(data.get("origin"))}管 —— 参数形状在'
                f'「扩展」窗口里：选这一条，字段表的「位置 / 关键字名」那一列'
                f'填 0/1/2 或 layer（左边那个按钮直接送你过去）。')
            return None
        dlg = CallParamsDialog(data.get('columns') or (), head=head,
                               parent=self)
        if dlg.exec() != QDialog.Accepted:
            return None
        columns, bad = dlg.built()
        if bad:
            self.status.setText('参数没改：' + '；'.join(bad[:3]))
            return None
        self._set_columns(row, columns, data.get('origin') or '')
        self.status.setText(f'「{head}」的参数改成 {len(columns)} 个 —— '
                            f'点「保存到项目清单」才写盘。')
        return columns

    def built_calls(self) -> list:
        """表格 → `calls` 列表（纯；坏行跳过并留在状态里说清）。"""
        out = []
        bad = []
        for row in range(self.table.rowCount()):
            head = self._cell(row, _COL_NAME)
            if not head:
                bad.append(f'第 {row + 1} 行没有调用名')
                continue
            data = self._row_data(row)
            raw_seconds = self._cell(row, _COL_SECONDS)
            seconds_arg = None
            if raw_seconds:
                if raw_seconds.isdigit():
                    seconds_arg = int(raw_seconds)
                else:
                    bad.append(f'第 {row + 1} 行的"时长参数"不是数字')
            # 参数以行数据为准（编辑器写的）；没有就退回那一列的文字摘要 ——
            # 老判据 / 手写的表格行只有文字，退回去才不会一条都读不出。
            columns = [dict(c) for c in (data.get('columns') or ())
                       if isinstance(c, dict)]
            if not columns:
                columns = parse_columns_text(self._cell(row, _COL_PARAMS))
            if not columns:
                bad.append(f'第 {row + 1} 行没有参数列')
            call = {'head': head, 'label': self._cell(row, _COL_LABEL),
                    'columns': columns,
                    'effect': self._cell(row, _COL_EFFECT),
                    'seconds_arg': seconds_arg}
            if data.get('origin'):
                call['origin'] = str(data['origin'])
            out.append(call)
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

    def _go_extensions(self, head: str = ''):
        """
        「这条的形状在扩展里」→ 打开扩展窗口并**选中那一条**（不是干巴巴一句"去那边"）。

        这是这一轮的另一半：两个窗口不再互相盖：谁的文件说了算，谁就是编辑它
        的地方，另一个窗口负责把人送过去。
        """
        if not self._root:
            self.status.setText('先打开项目 —— 扩展清单在项目根目录。')
            return None
        row = self.table.currentRow()
        if not head and row >= 0:
            head = self._cell(row, _COL_NAME)
        try:
            from storygraph.ui_qt.dialog_extensions import ExtensionDialog
        except ImportError as e:                  # 打包漏文件时只说话，不炸窗
            self.status.setText(f'打不开扩展窗口：{e}')
            return None
        dlg = ExtensionDialog(self, project_root=self._root, focus_id=head)
        dlg.exec()
        if getattr(dlg, 'changed', False):
            self.changed = True
            self._reload()
        return dlg

    def _reload(self):
        """重新读一遍项目（扩展里改了 → 这里必须跟着变，别显示旧形状）。"""
        self.table.setRowCount(0)
        self._load()

    def _save(self):
        if not self._root:
            self.status.setText('先打开项目 —— 清单要写进项目根目录。')
            return None
        calls = self.built_calls()
        bad = list(getattr(self, '_bad', None) or ())
        if bad:
            self.status.setText('还不能保存：' + '；'.join(bad[:3]))
            return None
        res = SR.save_calls(self._root, calls)
        if not res.get('ok'):
            self.status.setText('没写进项目清单：' + str(res.get('reason') or '?'))
            return res
        written = list(res.get('written') or ())
        blocked = list(res.get('blocked') or ())
        deleted = list(res.get('deleted') or ())
        if written or deleted:
            self.changed = True
        self.status.setText(self._save_note(written, blocked, deleted))
        return res

    @staticmethod
    def _save_note(written, blocked, deleted) -> str:
        """保存完之后那一句话（**照实说**：写了几条、哪几条没写、为什么）。"""
        parts = []
        if written:
            parts.append(f'已保存 {len(written)} 条到规则文件'
                         f'（重新打开那几拍就能看到新的参数格）。')
        if blocked:
            owners = {}
            for item in blocked:
                owners.setdefault(str(item.get('owner') or ''), []).append(
                    str(item.get('head') or ''))
            told = []
            for owner, heads in owners.items():
                told.append(f'{len(heads)} 条归'
                            f'{SR.CALL_OWNER_LABELS.get(owner, owner)}管'
                            f'（{"、".join(heads[:3])}'
                            f'{"…" if len(heads) > 3 else ""}）')
            parts.append('没写：' + '；'.join(told)
                         + ' —— 形状在那边，请点「这条在扩展窗口里改…」。')
        if deleted:
            parts.append(f'顺手清掉了 {len(deleted)} 条写在规则文件里、但被扩展'
                         f'盖住的旧条目（{"、".join(deleted[:3])}'
                         f'{"…" if len(deleted) > 3 else ""}）。')
        if not parts:
            return '什么也没写 —— 表格里没有可保存的调用。'
        return ''.join(parts)


__all__ = ['CallDialog', 'format_columns_text', 'owner_text',
           'parse_columns_text']
