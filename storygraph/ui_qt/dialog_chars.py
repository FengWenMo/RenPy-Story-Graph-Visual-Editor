"""
角色决策弹窗（`ui_qt`）
========================

用户 2026-10-10 的方案：

> 在转换时搜索已定义的相似字符 …… 然后写一个弹窗，问是不是已定义的这个角色？
> 如果是，自动标上别名，如果不是，自动生成新角色 define 并输出。

**这个弹窗的全部价值在于"人点一下"** —— 所以它的设计目标只有三条：

  1. **让他看得见判断依据**：每个候选都带**证据文字**（"忽略大小写后完全相同" /
     "写法相近（80%）"），并且**贴出原文那几行**（`MOM"跟妈客气啥"` 才知道是妈妈）；
  2. **让他点得动**：一屏把本轮所有未映射写法列完（不是一个一个弹），
     每行三个动作（指到已有角色 / 建新角色 / 先跳过），默认值就是**系统的建议**；
  3. **不许点出静默错误**：某个选择不可行时**当场标黄并说清为什么**
     （例如"`LJK` 与变量名 `ljk` 只差大小写 —— 加别名解决不了"），
     而不是等写盘时失败。

真正的判断逻辑一行都不在这里 —— 全在纯函数模块
`storygraph.authoring.char_decisions`（那里有 28 条判据）。本文件只负责
"把 `Proposal` 摆出来、把人选的记下来"。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from storygraph.authoring import char_decisions as CD
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme

#: 三个动作（顺序就是下拉里的顺序；"指到已有角色"排第一 = 最常见）
ACT_EXISTING = 'existing'
ACT_NEW = 'new'
ACT_SKIP = 'skip'

_ACT_LABEL = {
    ACT_EXISTING: '指到已有角色',
    ACT_NEW: '建新角色',
    ACT_SKIP: '先跳过',
}

#: 一轮里最多铺多少行（真机 5 章只有 8 个未映射写法，超过这个数说明
#: 输入可能根本不是这个项目的剧本 —— 那种情况该让人先看一眼清单）
MAX_ROWS = 200


def default_action(proposal: CD.Proposal) -> str:
    """
    系统建议的默认动作（**人只需要确认或改**）。

      · 有别名计划 → 指到已有角色（`MOM` 这种有候选的，直接就是它）；
      · 没别名但能建 → 建新角色（`BAOAN` 这种表里没有的）；
      · 都不行 → 先跳过（撞车/需改名那类，跳过比乱指安全）。
    """
    if proposal.alias and proposal.alias.ok:
        return ACT_EXISTING
    if proposal.new and proposal.new.ok:
        return ACT_NEW
    return ACT_SKIP


def default_target(proposal: CD.Proposal) -> str:
    """默认指向谁：别名计划的目标 > 第一个候选 > 空。"""
    if proposal.alias and proposal.alias.ok:
        return proposal.alias.var
    if proposal.suggestion.candidates:
        return proposal.suggestion.candidates[0].var
    return ''


class _RowEditor(QWidget):
    """一行未映射 token 的编辑区（动作下拉 + 目标输入 + 局部说明）。"""

    def __init__(self, proposal: CD.Proposal, rows, all_vars: list = None,
                 parent=None):
        super().__init__(parent)
        self.proposal = proposal
        #: 角色表（`{变量: 行}`）—— 只用来**显示**显示名，判定一律走纯逻辑模块
        self._rows = dict(rows or {})
        self._vars = list(all_vars if all_vars is not None else self._rows)
        self._submitting = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.SPACE['xs'])

        top = QHBoxLayout()
        top.setSpacing(T.SPACE['sm'])
        self.action = QComboBox()
        for act in (ACT_EXISTING, ACT_NEW, ACT_SKIP):
            self.action.addItem(_ACT_LABEL[act], act)
        self.action.setCurrentIndex(
            self.action.findData(default_action(proposal)))
        self.action.currentIndexChanged.connect(self._on_action)
        top.addWidget(self.action)

        # 「指到已有角色」用：候选（带证据）在前，全表在后（要能手工挑）
        #
        # ⚠ 下拉里**必须带显示名**（`mm（妈妈）`）：作者认得出的是"妈妈"，
        # 不是 `mm`；只给变量名的下拉等于让人猜（用户 2026-10-10 指出）。
        self.target = QComboBox()
        self.target.setEditable(False)
        seen = set()
        for cand in proposal.suggestion.candidates:
            if cand.var in seen:
                continue
            seen.add(cand.var)
            self.target.addItem(f'{self._label(cand.var)} —— {cand.reason}',
                                cand.var)
        # ⚠ 不指定 list(rows) 的位置参数：`_is_filler_name` 之类走的是
        # `suggestions` 已经算好的候选，这里只用"全表"给手工挑选兜底。
        # 顺序：有中文显示名的排前面（真角色多半有中文名，`aaa`/`dhyab` 这类
        # 拼出来的组合行排后面）—— 28 行表里这一步能省不少找的时间。
        rest = [v for v in dict.fromkeys(self._vars) if v not in seen]
        rest.sort(key=lambda v: (not CD._has_han(self._name_of(v)), v))
        for var in rest:
            seen.add(var)
            self.target.addItem(self._label(var), var)
        want = default_target(proposal)
        idx = self.target.findData(want)
        if idx < 0 and want:
            self.target.addItem(self._label(want), want)
            idx = self.target.count() - 1
        if idx >= 0:
            self.target.setCurrentIndex(idx)
        top.addWidget(self.target, 1)
        lay.addLayout(top)

        # 「建新角色」用：变量名 / 显示名（默认都是 token 原样，可改）
        #
        # ⚠ 这些控件**必须带 parent**（`QLineEdit(self)`）：不带 parent 时它们
        # 属于**对话框**而不是这一行 —— 既不受这一行的布局管理，`setVisible(False)`
        # 也管不住它们，于是"指到已有角色"那一行照样飘着两个输入框（实机截图抓到）。
        self.new_row = QWidget(self)
        new_row = QHBoxLayout(self.new_row)
        new_row.setContentsMargins(0, 0, 0, 0)
        new_row.setSpacing(T.SPACE['sm'])
        self.new_var = QLineEdit(proposal.suggestion.token, self.new_row)
        self.new_name = QLineEdit(proposal.suggestion.token, self.new_row)
        self.new_var.setPlaceholderText('变量名')
        self.new_name.setPlaceholderText('显示名')
        self.lab_var = QLabel('变量', self.new_row)
        self.lab_name = QLabel('显示名', self.new_row)
        new_row.addWidget(self.lab_var)
        new_row.addWidget(self.new_var, 1)
        new_row.addWidget(self.lab_name)
        new_row.addWidget(self.new_name, 1)
        lay.addWidget(self.new_row)

        self.note = QLabel('')
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

        self.new_var.textChanged.connect(self._refresh_note)
        self.new_name.textChanged.connect(self._refresh_note)
        self.target.currentIndexChanged.connect(self._refresh_note)
        self.action.currentIndexChanged.connect(self._refresh_note)
        self._refresh_note()

    # ---- 显示用的小工具（**只影响显示，不参与判定**） ----

    def _name_of(self, var: str) -> str:
        return str((self._rows.get(var) or {}).get('Name') or '').strip()

    def _label(self, var: str) -> str:
        """`mm（妈妈）` —— 作者认得出的是显示名，不是变量名。"""
        name = self._name_of(var)
        return f'{var}（{name}）' if name and name != var else var

    # ---- 与纯逻辑对齐：把当前选择变成"计划"，并显示它可行不可行 ----

    def plan(self) -> CD.Proposal:
        """当前这一行的人为选择 → 新的 `Proposal`（复算，不用旧结论）。"""
        act = self.action.currentData()
        if act == ACT_SKIP:
            return CD.Proposal(suggestion=self.proposal.suggestion,
                               action=ACT_SKIP, note='先跳过')
        if act == ACT_NEW:
            return CD.propose_new_character(
                self.proposal.rows, self.proposal.suggestion.token,
                var=self.new_var.text(), name=self.new_name.text())
        target = self.target.currentData() or ''
        p = CD.propose_alias(self.proposal.rows, self.proposal.suggestion.token,
                             target)
        if not p.alias_ok and p.note:
            # 别名走不通时给第二次机会：按当前输入试建新角色（与
            # `propose_one` 的兜底一致，否则这一格会变成死路）
            fallback = CD.propose_new_character(
                self.proposal.rows, self.proposal.suggestion.token)
            if fallback.new_ok:
                fallback.action = ACT_EXISTING
                return fallback
        return p

    def decision(self) -> dict:
        """给人看的决定（写进结果里，便于回读/日志）。"""
        act = self.action.currentData()
        if act == ACT_SKIP:
            return {'token': self.proposal.suggestion.token, 'action': ACT_SKIP}
        if act == ACT_NEW:
            return {'token': self.proposal.suggestion.token, 'action': ACT_NEW,
                    'var': self.new_var.text().strip(),
                    'name': self.new_name.text().strip()}
        return {'token': self.proposal.suggestion.token, 'action': ACT_EXISTING,
                'target': self.target.currentData() or ''}

    def _on_action(self, _idx):
        self._refresh_note()

    def _refresh_note(self):
        act = self.action.currentData()
        self.target.setVisible(act == ACT_EXISTING)
        # 整块显示/隐藏（连"变量/显示名"两个标签一起）—— 只藏输入框会留下
        # 两个孤零零的标签（实机截图抓到过）。
        self.new_row.setVisible(act == ACT_NEW)
        if act == ACT_SKIP:
            # ⚠ "跳过"也必须说清**为什么这里没有可行方案**（例如撞车）——
            # 否则用户看到"先跳过"会以为只是默认值，切到别的动作又白试一次。
            why = (self.proposal.note or '').strip()
            self.note.setText(
                ('先跳过：' + why) if why else
                '这一处不处理：产物里仍是原文写法（未映射告警保留）')
            self.note.setStyleSheet(f"color: {T.t('state.warn')};")
            return
        plan = self.plan()
        if plan.action == ACT_NEW:
            ok = plan.new_ok
            self.note.setText(plan.note or ('将新建角色 ' + plan.new.row.get('ID', '')))
        else:
            ok = plan.alias_ok
            self.note.setText(plan.note or '')
        colour = T.t('state.ok') if ok else T.t('state.warn')
        self.note.setStyleSheet(f"color: {colour};")


class CharDecisionDialog(QDialog):
    """
    未映射角色 → 逐条确认（用户 2026-10-10 定的形状）。

    用法（典型的调用方是"导入纯文字"那条路）::

        dlg = CharDecisionDialog(parent, proposals, rows)
        if dlg.exec() == QDialog.Accepted:
            dlg.result_proposals()   # 判定后的 [Proposal]
            dlg.decisions()          # 给人看的决定清单

    `rows` 是角色表（`{变量: 行}`，行里有 `Name`）—— 只用于**显示**
    （下拉里显示 `mm（妈妈）` 而不是光秃秃的 `mm`：作者认得出的是显示名）。
    """

    def __init__(self, parent=None, proposals=None, rows=None,
                 title: str = '确认角色', summary: str = ''):
        super().__init__(parent)
        self.setWindowTitle(title)
        theme.apply_to(self)
        self._proposals = list(proposals or [])[:MAX_ROWS]
        self._rows = dict(rows or {})
        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])

        head = QLabel(summary or (
            '这些写法在角色表里没有确定对应。每条选一个动作 —— '
            '「指到已有角色」会写进 characters.xlsx 的「别名」列；'
            '「建新角色」会追加一行（define 交给项目的同步链路）。'))
        head.setWordWrap(True)
        root.addWidget(head)

        hint = QLabel('⚠ 归一化撞车（只差大小写等多个人）的写法不能靠别名解决 —— '
                      '那种行会标黄，请改脚本里的写法或变量名。')
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {T.t('state.warn')};")
        root.addWidget(hint)

        self.table = QTableWidget(len(self._proposals), 4)
        self.table.setHorizontalHeaderLabels(['原文写法', '处数', '原文（判断依据）',
                                              '怎么处理'])
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self._editors = []
        for i, p in enumerate(self._proposals):
            self.table.setItem(i, 0, QTableWidgetItem(p.suggestion.token))
            self.table.setItem(i, 1, QTableWidgetItem(str(p.suggestion.count)))
            self.table.setItem(i, 2, QTableWidgetItem(self._context_text(p)))
            editor = _RowEditor(p, p.rows or self._rows)
            self._editors.append(editor)
            self.table.setCellWidget(i, 3, editor)
            self.table.setRowHeight(i, 96)
        root.addWidget(self.table, 1)
        self.resize(980, 560)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText('按这些决定写入角色表')
        box.button(QDialogButtonBox.Ok).setProperty('variant', 'primary')
        box.button(QDialogButtonBox.Cancel).setText('取消（什么都不改）')
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    @staticmethod
    def _context_text(proposal: CD.Proposal) -> str:
        """该 token 的原文样例（**人凭这个才知道是不是同一个人**）。"""
        lines = [f'第{no}行：{raw}' for no, raw in proposal.suggestion.samples[:3]]
        if proposal.suggestion.note:
            lines.append(proposal.suggestion.note)
        return '\n'.join(lines) or '（原文样例缺失）'

    def result_proposals(self) -> list:
        """按当前选择**重算**后的 `[Proposal]`（可行性与写盘用的是同一套判据）。"""
        return [ed.plan() for ed in self._editors
                if ed.action.currentData() != ACT_SKIP]

    def decisions(self) -> list:
        return [ed.decision() for ed in self._editors]


__all__ = ['ACT_EXISTING', 'ACT_NEW', 'ACT_SKIP', 'CharDecisionDialog',
           'default_action', 'default_target']
