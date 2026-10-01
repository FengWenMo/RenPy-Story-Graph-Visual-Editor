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

import re

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from storygraph.ui import tokens as T
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
    """编辑一个章节块的变量操作（`$ ...` 或 `python:` 块）。"""

    def __init__(self, parent=None, label_name: str = '', operations=None):
        super().__init__(parent, f'编辑变量操作 · {label_name}')
        self.operations = list(operations or [])

        head = QLabel(f'章节：{label_name}')
        head.setProperty('role', 'section')
        self._root.addWidget(head)

        self._text = QPlainTextEdit()
        self._text.setPlainText('\n'.join(self.operations))
        self._text.setMinimumHeight(220)
        self._root.addWidget(self._text)

        hint = QLabel('一行一条。多行代码请用 `python:` 起一个块'
                      '（保存时会按原样写进生成的 .rpy）。')
        hint.setProperty('role', 'caption')
        hint.setWordWrap(True)
        self._root.addWidget(hint)
        self._add_buttons('保存')
        self._text.setFocus()

    def _on_accept(self):
        raw = self._text.toPlainText()
        self.operations = [ln for ln in raw.splitlines() if ln.strip()]
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
        except Exception:                    # noqa: BLE001
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
