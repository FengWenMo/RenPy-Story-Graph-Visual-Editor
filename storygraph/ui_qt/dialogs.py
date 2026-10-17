"""
storygraph.ui_qt.dialogs
========================
Qt 版**图编辑对话框**：加章节 / 编辑变量操作 / 加连线 / 删连线 / 变量分析。

迁移背景（对齐 `ui/graph_view.py` 的 tkinter 对话框）
----------------------------------------------------
tkinter 版的这些对话框是 `Toplevel` + `Entry` + `Text` 拼的，Qt 版换成
`QDialog`，但**校验语义一字不改**：

  · 章节名**必须**是合法的 Ren'Py label（中文/空格/短横线都会生成坏 label）；
  · 连线**不许自环**（`jump` 到自己 = 死循环）；
  · `condition` 走 `analysis.condition_possible` 的同一套判断（不另写一套）。

**校验失败时把原因写在对话框里**（而不是弹一个框再让你重填）——
用户 2026-09-30 抱怨过"点确定以后啥也没了，还得重新打一遍"。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow
import re
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme

#: Ren'Py label 名：字母/数字/下划线，不以数字开头。
#: （中文、空格、短横线都会生成**跑不起来**的脚本，必须在这里挡住。）
_LABEL_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def valid_label(name: str) -> bool:
    return bool(_LABEL_RE.match((name or '').strip()))


class _BaseDialog(QDialog):
    """带"错误行"的对话框基类：校验失败就地提示，不弹二次框。"""

    def __init__(self, parent=None, title: str = ''):
        super().__init__(parent)
        self.setWindowTitle(title)
        theme.apply_to(self)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                      T.SPACE['md'], T.SPACE['md'])
        self._root.setSpacing(T.SPACE['sm'])
        self._error = QLabel('')
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color: {T.t('state.error')};")
        self._error.setVisible(False)
        self._buttons = None

    def _fail(self, message: str):
        self._error.setText(message)
        self._error.setVisible(True)
        return False

    def _ok(self):
        self._error.setVisible(False)

    def _add_buttons(self, ok_text: str = '确定'):
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText(ok_text)
        box.button(QDialogButtonBox.Ok).setProperty('variant', 'primary')
        box.button(QDialogButtonBox.Cancel).setText('取消')
        box.accepted.connect(self._on_accept)
        box.rejected.connect(self.reject)
        self._buttons = box
        self._root.addWidget(box)
        self._root.addWidget(self._error)
        return box

    def _on_accept(self):
        self.accept()


# ======================================================================
#  只读文本窗（统计 / 分析结果这类"给人看的一段文字"）
# ======================================================================

class TextViewDialog(_BaseDialog):
    """
    一段只读文字 + 「复制」「关闭」。

    为什么不弹 `QMessageBox`：统计与分析动辄几十行，消息框会把它挤成
    一条滚动条（还得手动滚）。这里给一个能选中、能复制的正经文本框。
    """

    def __init__(self, parent=None, title: str = '详情', text: str = '',
                 width: int = 760, height: int = 560):
        super().__init__(parent, title)
        self.resize(width, height)
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText(str(text or ''))
        box.setFont(QFont(T.FONTS['mono'][0], T.FONTS['mono'][1]))
        self.text = box.toPlainText
        self._box = box
        self._root.addWidget(box, 1)

        row = QHBoxLayout()
        copy = QPushButton('复制全部')
        copy.clicked.connect(self._copy)
        row.addWidget(copy)
        row.addStretch(1)
        close = QPushButton('关闭')
        close.setProperty('variant', 'primary')
        close.clicked.connect(self.accept)
        row.addWidget(close)
        self._root.addLayout(row)

    def _copy(self):
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(self._box.toPlainText())
            self._error.setText('已复制到剪贴板。')
            self._error.setStyleSheet(f"color: {T.t('state.ok')};")
            self._error.setVisible(True)
        except Exception:                     # noqa: BLE001
            swallow('dialogs._copy')


# ======================================================================
#  协作库（SQLite ↔ Excel）
# ======================================================================

class CollabDialog(_BaseDialog):
    """
    协作库入口：**登记 / 拉取 / 提交 / 看状态**。

    冲突规则全在 `collab.collab_store.push`（行被别人锁着 → 拒；
    库里的 revision 比你拉取时更新 → 冲突并列出来），界面**不重写**这套规则 ——
    它只负责把结果显示清楚（"不静默覆盖"是这一层的底线）。

    数据库与 Excel 路径都可以手填；Excel 默认取当前章节导出的剧本表。
    """

    def __init__(self, parent=None, session=None, project_root=None,
                 log=None, status=None):
        super().__init__(parent, '协作库（SQLite ↔ Excel）')
        self.resize(720, 560)
        self._session = session
        self._log_fn = log
        self._status_fn = status
        root_dir = Path(project_root) if project_root else Path.cwd()

        form = QFormLayout()
        form.setSpacing(T.SPACE['sm'])

        self.db_path = QLineEdit(str(root_dir / 'collab.sqlite3'))
        db_row = QHBoxLayout()
        db_row.addWidget(self.db_path, 1)
        db_browse = QPushButton('浏览…')
        db_browse.clicked.connect(lambda: self._pick(self.db_path, save=True,
                                                     ext='SQLite (*.sqlite3 *.db)'))
        db_row.addWidget(db_browse, 0)
        form.addRow('协作数据库', self._wrap(db_row))

        self.sheet_path = QLineEdit(str(root_dir / 'game' / 'script.xlsx'))
        sh_row = QHBoxLayout()
        sh_row.addWidget(self.sheet_path, 1)
        sh_browse = QPushButton('浏览…')
        sh_browse.clicked.connect(lambda: self._pick(self.sheet_path, save=False,
                                                     ext='Excel (*.xlsx)'))
        sh_row.addWidget(sh_browse, 0)
        form.addRow('剧本表（Excel）', self._wrap(sh_row))

        self.actor = QLineEdit('')
        self.actor.setPlaceholderText('你的名字（锁 / revision 都记在它上面）')
        form.addRow('当前用户', self.actor)
        self._root.addLayout(form)

        acts = QHBoxLayout()
        for text, slot, tip in (
                ('🗃 建库并登记成员', self._init_db, '建表 + 登记用户（可重复执行）'),
                ('⬇ 从库导出到 Excel', self._pull, '数据库 → Excel（带锁与 revision）'),
                ('⬆ 提交 Excel 到库', self._push, 'Excel → 数据库（冲突不覆盖）'),
                ('📊 查看状态', self._status, '谁锁了多少 / 各状态多少 / 最近改动')):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            acts.addWidget(btn)
        acts.addStretch(1)
        self._root.addLayout(acts)

        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setPlaceholderText('操作结果会写在这里…')
        self.out.setFont(QFont(T.FONTS['mono'][0], T.FONTS['mono'][1]))
        self._root.addWidget(self.out, 1)
        self._add_buttons('关闭')

    @staticmethod
    def _wrap(layout):
        from PySide6.QtWidgets import QWidget
        w = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(layout)
        return w

    def _pick(self, line: QLineEdit, save: bool, ext: str):
        from PySide6.QtWidgets import QFileDialog
        if save:
            path, _ = QFileDialog.getSaveFileName(self, '选择文件',
                                                  line.text(), ext)
        else:
            path, _ = QFileDialog.getOpenFileName(self, '选择文件',
                                                  line.text(), ext)
        if path:
            line.setText(path)

    # ---- 四个动作（都委托会话，界面不碰 sqlite）----

    def _say(self, text: str):
        self.out.appendPlainText(str(text))
        if callable(self._log_fn):
            self._log_fn(str(text))

    def _init_db(self):
        users = [u.strip() for u in (self.actor.text() or '').split(',')
                 if u.strip()]
        res = self._session.collab_init(self.db_path.text(), users=users)
        if not res.get('ok'):
            self._say(f'建库失败：{res.get("error")}')
            return
        self._say(f'✔ 已建库：{res["path"]}'
                  + (f'（登记 {len(users)} 个用户）' if users else ''))

    def _pull(self):
        res = self._session.collab_pull(self.db_path.text(),
                                        self.sheet_path.text(),
                                        actor=self.actor.text().strip())
        if not res.get('ok'):
            self._say(f'导出失败：{res.get("error")}')
            return
        self._say(f'✔ 已从数据库导出到 Excel：{self.sheet_path.text()}')
        self._say(f'   {res.get("result")}')

    def _push(self):
        actor = self.actor.text().strip()
        if not actor:
            self._say('请先填"当前用户"——锁与 revision 要记在它上面。')
            return
        res = self._session.collab_push(self.db_path.text(),
                                        self.sheet_path.text(), actor=actor)
        if not res.get('ok'):
            self._say(f'提交失败：{res.get("error")}')
            return
        r = res.get('result') or {}
        self._say(f'✔ 已提交：新增 {r.get("added")} / 更新 {r.get("updated")} / '
                  f'未变 {r.get("unchanged")} / 复活 {r.get("revived")}')
        for key in ('conflicts', 'rejected'):
            rows = r.get(key) or []
            if rows:
                self._say(f'   ⚠ {key}（{len(rows)}）：{rows[:5]}')

    def _status(self):
        res = self._session.collab_status(self.db_path.text())
        if not res.get('ok'):
            self._say(f'看不了状态：{res.get("error")}')
            return
        self._say(f'状态总览：{res.get("summary")}')


# ======================================================================
#  加章节
# ======================================================================

class AddNodeDialog(_BaseDialog):
    """
    新建一个章节块。

    `name`：label 名。`operations`：可选的多行初始化代码（`$ x = 1`）。
    """

    def __init__(self, parent=None, existing=()):
        super().__init__(parent, '添加新章节')
        self._existing = set(existing or ())
        self.name = ''
        self.operations = ''

        form = QFormLayout()
        form.setSpacing(T.SPACE['sm'])
        self._name = QLineEdit()
        self._name.setPlaceholderText('英文 label 名，例如 chapter_02')
        self._name.textChanged.connect(lambda *_: self._ok())
        form.addRow('章节名（label）', self._name)
        self._ops = QPlainTextEdit()
        self._ops.setPlaceholderText('可选：初始化代码，一行一条，例如\n'
                                     'score = 0\n'
                                     'visited_roof = False')
        self._ops.setFixedHeight(96)
        form.addRow('变量操作', self._ops)
        self._root.addLayout(form)

        hint = QLabel("章节名会成为 Ren'Py 的 label —— "
                      "只能用字母/数字/下划线，且不能以数字开头。")
        hint.setProperty('role', 'caption')
        hint.setWordWrap(True)
        self._root.addWidget(hint)
        self._add_buttons('创建')
        self._name.setFocus()

    def _on_accept(self):
        name = self._name.text().strip()
        if not valid_label(name):
            self._fail("章节名不合法：只能用字母/数字/下划线，且不能以数字开头"
                       "（中文和空格会生成跑不起来的脚本）。")
            return
        if name in self._existing:
            self._fail(f'已经有一个叫「{name}」的章节了。')
            return
        self.name = name
        self.operations = self._ops.toPlainText().strip()
        self.accept()


# ======================================================================
#  编辑变量操作
# ======================================================================

class NodeOpsDialog(_BaseDialog):
    """
    编辑一个章节的**剧情变量**（策划 / 编剧调整剧情分支用的地方）。

    用户 2026-10-03：

    > 这个所谓的"技术代码"，它不是变量，是具体的实现细节！……
    > 我不要再在变量的窗口上看到任何内部技术代码的名字！

    所以这里**只**列 `story_ops.narrative_operations`。
    `renpy.transition(...)` / `persistent.x = ...` 这一批连名字都不出现 ——
    它们不是变量，是引擎实现细节。

    ⚠ 保存时**不能**用编辑器里的内容替换整份 operations：那样会把看不见的
    技术代码一起删掉（`EditorSession.set_node_variables` 负责原位保留）。

    另外把旧版的**「语法检查」**按钮补回来了：变量写错一个符号，整章的
    区间分析就会跑偏 —— 在写回之前先说一声。
    """

    def __init__(self, parent=None, label_name: str = '', variables=None,
                 tech_count: int = 0, on_check=None):
        super().__init__(parent, f'编辑剧情变量 · {label_name}')
        self.variables = list(variables or [])
        # 旧版这个对话框是 700×500；太小的话多行变量看不清。
        self.resize(620, 460)
        #: 可选：`on_check(lines) -> [错误文本]`（语义在会话里，对话框只显示）
        self._on_check = on_check

        head = QLabel(f'章节：{label_name}')
        head.setProperty('role', 'section')
        self._root.addWidget(head)

        self._text = QPlainTextEdit()
        self._text.setPlainText('\n'.join(self.variables))
        self._text.setMinimumHeight(240)
        self._text.setFont(QFont('Consolas', 10))
        self._root.addWidget(self._text)

        # 注意：这里是**纯文本** QLabel（不是说明窗口那种富文本），
        # 所以文案里不要写 Markdown 的 `**`，否则星号会原样显示给用户。
        hint = QLabel('一行一个剧情变量赋值（例如 love += 1、flag = True）。'
                      '它们决定剧情分支走向，是给策划 / 编剧编排剧情用的。')
        hint.setProperty('role', 'caption')
        hint.setWordWrap(True)
        self._root.addWidget(hint)

        if tech_count:
            # 只报"还有 N 条实现细节"，**不报它们的名字** —— 用户明确要求
            # 在变量窗口里看不到任何内部技术代码。
            tech = QLabel(f'（这个章节还有 {tech_count} 条实现细节，'
                          f'不属于变量，不在这里编辑；保存时原样保留。）')
            tech.setProperty('role', 'caption')
            tech.setWordWrap(True)
            self._root.addWidget(tech)

        self._check_label = QLabel('')
        self._check_label.setWordWrap(True)
        self._check_label.setProperty('role', 'caption')
        self._root.addWidget(self._check_label)

        bar = QHBoxLayout()
        self._check_btn = QPushButton('语法检查')
        self._check_btn.setToolTip('逐行检查这些变量能不能被解析（只检查，不改数据）')
        self._check_btn.clicked.connect(self._check)
        bar.addWidget(self._check_btn)
        bar.addStretch(1)
        self._root.addLayout(bar)

        self._add_buttons('保存')
        self._text.setFocus()

    def _lines(self) -> list:
        return [ln.strip() for ln in self._text.toPlainText().splitlines()
                if ln.strip()]

    def _check(self):
        lines = self._lines()
        errors = []
        if self._on_check is not None:
            try:
                errors = list(self._on_check(lines) or [])
            except Exception as e:                # noqa: BLE001
                errors = [f'检查失败：{e}']
        if errors:
            self._check_label.setText('⚠ ' + '；'.join(errors))
            self._check_label.setProperty('role', 'warn')
        else:
            self._check_label.setText(f'✔ {len(lines)} 行都能解析。')
            self._check_label.setProperty('role', 'caption')
        try:
            self._check_label.style().unpolish(self._check_label)
            self._check_label.style().polish(self._check_label)
        except Exception:                         # noqa: BLE001
            swallow('dialogs._check')

    def _on_accept(self):
        self.variables = self._lines()
        self.accept()


# ======================================================================
#  加连线
# ======================================================================

EDGE_TYPES = [('jump', 'jump（跳过去，不回来）'),
              ('call', 'call（调过去，还会回来）')]


class AddEdgeDialog(_BaseDialog):
    """在两个章节之间加一条连线（`jump` / `call` + 可选条件 / 菜单项文字）。"""

    def __init__(self, parent=None, nodes=None, from_id=None, to_id=None):
        super().__init__(parent, '添加连线')
        nodes = dict(nodes or {})
        self.from_id = from_id
        self.to_id = to_id
        self.etype = 'jump'
        self.condition = ''
        self.option_text = ''

        form = QFormLayout()
        form.setSpacing(T.SPACE['sm'])

        self._src = QComboBox()
        self._dst = QComboBox()
        for nid, node in nodes.items():
            lbl = f'{nid}: {getattr(node, "label_name", "")}'
            self._src.addItem(lbl, nid)
            self._dst.addItem(lbl, nid)
        if from_id is not None:
            i = self._src.findData(from_id)
            if i >= 0:
                self._src.setCurrentIndex(i)
        if to_id is not None:
            i = self._dst.findData(to_id)
            if i >= 0:
                self._dst.setCurrentIndex(i)
        form.addRow('从', self._src)
        form.addRow('到', self._dst)

        self._type = QComboBox()
        for value, label in EDGE_TYPES:
            self._type.addItem(label, value)
        form.addRow('类型', self._type)

        self._cond = QLineEdit()
        self._cond.setPlaceholderText('可选，例如 score > 3（留空 = 无条件）')
        form.addRow('条件', self._cond)

        self._opt = QLineEdit()
        self._opt.setPlaceholderText('可选：填了就当成菜单选项文字，'
                                     '例如「去屋顶看看」')
        form.addRow('菜单项', self._opt)
        self._root.addLayout(form)

        hint = QLabel('填了「菜单项」= 这一拍是个分支（menu）；'
                      '只填「条件」= 条件跳转；都不填 = 直接 ' + 'jump/call' + '。')
        hint.setProperty('role', 'caption')
        hint.setWordWrap(True)
        self._root.addWidget(hint)
        self._add_buttons('创建')

    def _on_accept(self):
        self.from_id = self._src.currentData()
        self.to_id = self._dst.currentData()
        if self.from_id is None or self.to_id is None:
            self._fail('两端都要选。')
            return
        if self.from_id == self.to_id:
            self._fail('不能连到自己（那会是个死循环）。')
            return
        self.etype = self._type.currentData() or 'jump'
        cond = self._cond.text().strip()
        if cond and not self._condition_ok(cond):
            self._fail(f'条件「{cond}」看起来永远不成立，'
                       f'检查一下变量名和比较符。')
            return
        self.condition = cond
        self.option_text = self._opt.text().strip()
        self.accept()

    @staticmethod
    def _condition_ok(cond: str) -> bool:
        """
        条件"可能成立"吗？（复用 `analysis.condition_possible`，不另写一套）

        这里只用**空状态**判断 —— 目的是挡住"变量名打错 / 比较符写反"
        这类一眼可见的错误，不是做完整的可达性分析（那是体检的活）。
        校验器本身不可用（导入失败等）时**放过**，绝不因为校验器坏了
        就拦住用户的正常操作。
        """
        try:
            from storygraph.graph.analysis import (VariableState,
                                                   condition_possible)
            return condition_possible(VariableState(), cond) is not False
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.dialogs._condition_ok', e)
            return True


# ======================================================================
#  删除确认
# ======================================================================

def confirm(parent, title: str, message: str) -> bool:
    return QMessageBox.question(
        parent, title, message,
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No) == QMessageBox.Yes


# ======================================================================
#  变量分析（只读）
# ======================================================================

class AnalysisDialog(QDialog):
    """
    变量与可达性分析结果（**只读**）。

    以前这个结果只能用 tkinter 弹一个 `ScrolledText`；Qt 版做成
    可搜索的只读文本 + 「复制」按钮（策划要贴到群里讨论）。
    """

    def __init__(self, parent=None, title: str = '分析结果', text: str = ''):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 620)
        theme.apply_to(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])

        self._filter = QLineEdit()
        self._filter.setPlaceholderText('输入关键字过滤（例如某个变量名）')
        root.addWidget(self._filter)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setPlainText(text or '')
        root.addWidget(self._text, 1)

        bar = QHBoxLayout()
        copy = QPushButton('复制全部')
        copy.clicked.connect(self._copy)
        bar.addWidget(copy)
        bar.addStretch(1)
        close = QPushButton('关闭')
        close.setProperty('variant', 'primary')
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        root.addLayout(bar)

        self._all = text or ''
        self._filter.textChanged.connect(self._apply_filter)

    def _apply_filter(self, needle):
        needle = (needle or '').strip()
        if not needle:
            self._text.setPlainText(self._all)
            return
        lines = [ln for ln in self._all.splitlines() if needle in ln]
        self._text.setPlainText('\n'.join(lines) or f'（没有含「{needle}」的行）')

    def _copy(self):
        from PySide6.QtWidgets import QApplication
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self._text.toPlainText())


__all__ = ['valid_label', 'AddNodeDialog', 'NodeOpsDialog', 'AddEdgeDialog',
           'AnalysisDialog', 'confirm', 'EDGE_TYPES']
