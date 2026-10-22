"""
「协作锁」—— 谁在改哪几句，以及**我这一句先占住**。
==========================================================================

用户 2026-10-22（体验报告 P0-3，原话）：

> 一批"实现了但没接线"的能力。这个模式你已经踩过一次
> （路线图 §七：「"实现了"与"能用"是两件事」），现在又出现了：
> locks（collab_store）实测在**"只有测试在用"**清单里 → 能力悬空。

所以这一层**不写任何新规则**：锁的语义（租约 / 过期可接手 / 只读成员不能上锁
/ 强制解锁只有所有者能做）全在 `collab.collab_store`，界面只做三件事：

```
看：列表（哪一句 · 谁 · 还有多久到期 · 是不是我的）
占：锁住当前这一拍 / 锁住整章
收：续租我的 / 解锁（选中的） / 强制解锁（所有者）
```

## 为什么"锁"值得接到界面上（而不是留着当摆设）

`push` 的规则是**被锁着就拒绝提交**（不静默覆盖）。可如果没人上锁，
这条规则永远不触发 —— 两个人照旧同时改同一句，后提交的那个在
`push` 时才发现"你锁着我"（而她从没锁过任何东西，界面上也没这个动作）。

锁的价值不在"挡住别人"，在于**先手**：我改这一句的这 15 分钟里，
你提交它会被拒，而不是我的改动被静默覆盖。

## 三条界面口径

1. **说人话**：列表里显示台词摘要，不只显示 `ch01.0007` ——
   编号对不上人的记忆，摘要能让她一眼认出"哦是这句"；
2. **过期是说出来的**：租约还剩几分钟直接写出来（"还有 12 分钟"），
   不让人猜"这锁还有效吗"；
3. **强制解锁要说明代价**：那是推翻别人的锁，只有所有者能做，
   做了会进审计（`collab_store._audit`）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from storygraph.core.safeguard import swallow
from storygraph.ui_qt import prefs
from storygraph.ui_qt import tokens as T

__all__ = ['CollabLockDialog', 'lease_text', 'lock_row_text']

#: 上一次用的协作库 / 用户名（"这台机器的这个人"，所以是**偏好**不是项目字段）
_DB_KEY = 'collab/db_path'
_ACTOR_KEY = 'collab/actor'


def lease_text(row: dict) -> str:
    """一行锁 → "还有几分钟"（过期了就说"已过期，谁都能接手"）。"""
    if row.get('expired'):
        return '已过期（谁都能接手）'
    secs = int(row.get('expires_in') or 0)
    if secs <= 0:
        return '已过期（谁都能接手）'
    if secs < 60:
        return f'还有 {secs} 秒'
    return f'还有 {max(1, secs // 60)} 分钟'


def lock_row_text(row: dict) -> str:
    """一行锁 → "哪一句"（摘要优先，没有摘要退回 line_id）。"""
    text = str(row.get('text') or '').strip()
    if text:
        return text
    return str(row.get('line_id') or '')


class CollabLockDialog(QDialog):
    """协作锁（见模块 docstring）。"""

    def __init__(self, parent=None, session=None, project_root=None,
                 db_path: str = '', actor: str = '', label: str = '',
                 line_id: str = '', log=None):
        super().__init__(parent)
        self.setWindowTitle('协作锁（谁在改哪几句）')
        self.resize(760, 560)
        self._session = session
        self._log_fn = log
        self._label = str(label or '')
        self._line_id = str(line_id or '')
        self._rows = []
        self._build(Path(project_root) if project_root else Path.cwd(),
                    db_path, actor)

    # ---- 建界面 ----

    def _build(self, root: Path, db_path: str, actor: str):
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                    T.SPACE['md'], T.SPACE['md'])
        root_lay.setSpacing(T.SPACE['sm'])

        tip = QLabel('锁住的句子，别人提交时会被拒（工具不静默覆盖）；'
                     '锁 15 分钟后自动过期，过期了谁都能接手。\n'
                     '改之前先锁一句 = 这 15 分钟里这一句是你改的。')
        tip.setWordWrap(True)
        tip.setProperty('role', 'note')
        root_lay.addWidget(tip)

        form = QFormLayout()
        form.setSpacing(T.SPACE['xs'])
        self.db_path = QLineEdit(str(db_path or prefs.load_json(_DB_KEY, '')
                                     or root / 'collab.sqlite3'))
        row = QHBoxLayout()
        row.addWidget(self.db_path, 1)
        browse = QPushButton('浏览…')
        browse.clicked.connect(self._pick_db)
        row.addWidget(browse, 0)
        from PySide6.QtWidgets import QWidget
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        form.addRow('协作库', holder)

        self.actor = QLineEdit(str(actor or prefs.load_json(_ACTOR_KEY, '')))
        self.actor.setPlaceholderText('你的名字（与「协作库」里登记的一致）')
        form.addRow('当前用户', self.actor)
        root_lay.addLayout(form)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['哪一句', '谁锁的', '租约'])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.Stretch)
        head.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        root_lay.addWidget(self.table, 1)

        acts = QHBoxLayout()
        acts.setSpacing(T.SPACE['xs'])
        for text, slot, tip in (
                ('🔒 锁住当前这一拍', self._lock_current,
                 '把正在改的这一句占住（别人提交它会被拒）'),
                ('🔒 锁住整章', self._lock_chapter,
                 '这一章我在改 —— 已被别人锁着的句子会跳过并报数'),
                ('↻ 续租我的锁', self._renew_mine,
                 '把租约重新推到 15 分钟后（选中的那一行，或我的全部）'),
                ('🔓 解锁选中的', self._unlock_selected, '放开选中的那一句'),
                ('🛠 强制解锁', self._force_unlock,
                 '推翻别人的锁（只有所有者能做，会写进审计）'),
                ('🔄 刷新', self.refresh, '重新读一遍锁列表')):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            acts.addWidget(btn)
        acts.addStretch(1)
        root_lay.addLayout(acts)

        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setFixedHeight(96)
        self.out.setPlaceholderText('操作结果会写在这里…')
        self.out.setFont(QFont(T.FONTS['mono'][0], T.FONTS['mono'][1]))
        root_lay.addWidget(self.out)

        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        root_lay.addWidget(box)
        self.refresh()

    # ---- 小工具 ----

    def _say(self, text: str):
        line = str(text or '')
        self.out.appendPlainText(line)
        if callable(self._log_fn):
            try:
                self._log_fn(f'[协作锁] {line}')
            except Exception as e:              # noqa: BLE001
                swallow('ui_qt.dialog_locks.log', e)

    def _db(self) -> str:
        return self.db_path.text().strip()

    def _me(self) -> str:
        return self.actor.text().strip()

    def _pick_db(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '选协作库', self._db() or str(Path.cwd()),
            'SQLite (*.sqlite3 *.db);;所有文件 (*)')
        if path:
            self.db_path.setText(path)
            self.refresh()

    def _remember(self):
        """记住路径与用户名（下次不用再填；换项目时路径会跟着新项目默认走）。"""
        prefs.save_json(_DB_KEY, self._db())
        prefs.save_json(_ACTOR_KEY, self._me())

    def _require(self) -> bool:
        """"库在、人是谁"先说清楚 —— 不然每个动作都只会回一句"没有这一行"。"""
        if self._session is None:
            self._say('没接上会话 —— 这个窗口是从哪打开的？')
            return False
        db = self._db()
        if not db or not Path(db).is_file():
            self._say(f'还没有协作库：{db or "（没填）"}\n'
                      f'先在「🧰 更多工具 → 协作库」里建库并登记成员。')
            return False
        if not self._me():
            self._say('先填"当前用户" —— 锁记在这个名字上。')
            return False
        return True

    # ---- 读 ----

    def refresh(self):
        """重读锁列表（读不出来就说原因，不留一张空表让人以为"没人锁"）。"""
        self.table.setRowCount(0)
        self._rows = []
        if not self._db() or not Path(self._db()).is_file():
            return
        res = (self._session.collab_locks(self._db())
               if self._session is not None else {})
        if not res.get('ok'):
            self._say(f'读不到锁列表：{res.get("error") or "（没接上会话）"}')
            return
        self._rows = list(res.get('locks') or ())
        me = self._me()
        self.table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            who = str(row.get('locked_by') or '')
            mine = bool(me) and who == me
            cells = [lock_row_text(row),
                     ('你' if mine else (who or '（空）')),
                     lease_text(row)]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(f'{row.get("line_id") or ""}' if col == 0
                                else text)
                if mine:
                    # 我的锁**看得见**（不然列表里全是别人的，人以为没锁上）
                    item.setForeground(Qt.GlobalColor.darkGreen)
                self.table.setItem(i, col, item)

    # ---- 写 ----

    def _selected_line_id(self) -> str:
        row = self.table.currentRow()
        if 0 <= row < len(self._rows):
            return str(self._rows[row].get('line_id') or '')
        return ''

    def _lock_current(self):
        if not self._require():
            return
        if not self._line_id:
            self._say('还没有打开某一拍 —— 先去卡片页选中一句，再回来锁。')
            return
        res = self._session.collab_lock(self._db(), self._line_id, self._me(),
                                        note='界面-锁住当前这一拍')
        if not res.get('ok'):
            self._say(f'没锁上：{res.get("error")}')
            return
        self._remember()
        self._say(f'✔ 已锁住 {self._line_id}（'
                  f'{lease_text({"expires_in": res["result"].get("lease_seconds")})}）')
        self.refresh()

    def _lock_chapter(self):
        if not self._require():
            return
        if not self._label:
            self._say('不知道是哪个 label —— 先打开这一章再回来锁整章。')
            return
        res = self._session.collab_lock_range(self._db(), self._label, self._me())
        if not res.get('ok'):
            self._say(f'没锁上：{res.get("error")}')
            return
        r = res.get('result') or {}
        self._remember()
        self._say(f'✔ 「{self._label}」：锁住 {len(r.get("locked") or ())} 句'
                  + (f'，{len(r.get("skipped") or ())} 句已被别人锁着（跳过）'
                     if r.get('skipped') else ''))
        self.refresh()

    def _renew_mine(self):
        if not self._require():
            return
        targets = [self._selected_line_id()] if self._selected_line_id() else []
        if not targets:
            me = self._me()
            targets = [str(r.get('line_id') or '') for r in self._rows
                       if str(r.get('locked_by') or '') == me]
        if not targets:
            self._say('你现在没有锁着的句子。')
            return
        ok = 0
        for lid in targets:
            res = self._session.collab_renew_lock(self._db(), lid, self._me())
            if res.get('ok'):
                ok += 1
            else:
                self._say(f'· {lid}：{res.get("error")}')
        self._say(f'✔ 续租 {ok}/{len(targets)} 句')
        self.refresh()

    def _unlock_selected(self):
        if not self._require():
            return
        lid = self._selected_line_id()
        if not lid:
            self._say('先在列表里选中要解锁的那一句。')
            return
        res = self._session.collab_unlock(self._db(), lid, self._me())
        if not res.get('ok'):
            self._say(f'没解开：{res.get("error")}')
            return
        self._say(f'✔ 已解锁 {lid}')
        self.refresh()

    def _force_unlock(self):
        if not self._require():
            return
        lid = self._selected_line_id()
        if not lid:
            self._say('先在列表里选中要强制解锁的那一句。')
            return
        from PySide6.QtWidgets import QMessageBox
        owner = next((str(r.get('locked_by') or '') for r in self._rows
                      if str(r.get('line_id') or '') == lid), '')
        ans = QMessageBox.question(
            self, '强制解锁',
            f'这句现在是 {owner or "别人"} 锁着的。强制解开，'
            f'对方就可能在不知情的情况下提交失败。\n\n确定要解开吗？',
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if ans != QMessageBox.Yes:
            return
        res = self._session.collab_unlock(self._db(), lid, self._me(),
                                          force=True)
        if not res.get('ok'):
            self._say(f'没解开：{res.get("error")}')
            return
        self._say(f'✔ 已强制解锁 {lid}（审计里记了一笔）')
        self.refresh()
