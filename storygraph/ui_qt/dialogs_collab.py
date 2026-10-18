"""
storygraph.ui_qt.dialogs_collab
===============================
「你和 TA 改了同一句」—— 协作冲突的处理界面（用户 2026-10-19，C2）。

界面口径（用户原话，逐条照做）
----------------------------
· **两栏，不显示 base**：编剧不需要看"原来是什么"，她要决定"最后留什么"。
· **默认选 TA 的**（较新的那份），一路回车就能过；真要判断的才停下来看。
· **不用"冲突"这个词**：说「你和 TA 改了同一句」——"冲突"听起来像报错。
· **「两句都留」必须有**：视觉小说的冲突里很大比例是两个人都加了台词，
  正确答案是两句都要。它还能消掉一半的"自己拼一版"。
· **「自己拼一版」永远给预填内容**，不给空框。
· **可以只交一部分**：有 3 处要人、200 行自动合并时，那 200 行先提交，
  这 3 处回头再说（`跳过这句`）。

这一层只**收集选择**（`resolutions`），落地全在
`collab.collab_store.resolve_conflicts` —— 界面不碰 SQL，也不碰 Excel。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFrame, QGroupBox,
                               QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QRadioButton, QScrollArea,
                               QVBoxLayout, QWidget)

from storygraph.ui_qt import tokens as T

__all__ = ['CollabConflictDialog', 'conflict_choices']

#: 两栏里每边最多显示多少字（**只是显示**：提交的永远是全文）。
#:
#: 为什么要截（2026-10-18）：Excel 的上限是 32767 字，一句 3 万字的独白
#: 完全合法 —— 但两栏并排放两块 3 万字的 QLabel，窗口会被撑到屏幕外，
#: 而人要做的决定其实与"多长"无关（选哪边）。所以显示开头一段，
#: 后面写清"共 N 字 · 看全文"，全文放进一个可滚动的只读窗口。
#: 用户的话："一条 3w 多字，整个屏幕估计都装不下。"
DISPLAY_LIMIT = 300


def _split_display(text: str) -> tuple:
    """`(显示用文本, 总字数)` —— 没超就原样，超了给开头 + 省略号。"""
    t = str(text or '')
    if len(t) <= DISPLAY_LIMIT:
        return t, len(t)
    return t[:DISPLAY_LIMIT].rstrip() + ' …', len(t)


class _FullTextDialog(QDialog):
    """「看全文」：只读、可滚动。改内容请用「自己拼一版」。"""

    def __init__(self, title: str, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 420)
        lay = QVBoxLayout(self)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(str(text or ''))
        lay.addWidget(view, 1)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        lay.addWidget(box)


def _name_of(c) -> str:
    """对方的称呼（没记名字就叫 TA —— 不编一个）。"""
    return str(c.get('updated_by') or '').strip() or 'TA'


def _text_of(payload, field='Text') -> str:
    return str((payload or {}).get(field) or '')


class _FieldRow(QWidget):
    """一个字段的两栏选择：你写的 / TA 写的（默认选 TA）。"""

    def __init__(self, field, mine, theirs, label='', parent=None):
        super().__init__(parent)
        self.field = field
        self._custom = ''
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T.SPACE['xs'])
        cap = QLabel(label)
        cap.setProperty('role', 'section')
        lay.addWidget(cap)

        row = QHBoxLayout()
        row.setSpacing(T.SPACE['md'])
        self._rb_mine = QRadioButton('')
        self._rb_theirs = QRadioButton('')
        # ⚠ 两个单选按钮**在不同父控件里**（各自一张卡片），Qt 的自动互斥
        # 只对同一个 parent 生效 —— 不显式分组的话它们会同时被选中，
        # 界面上看起来"两个都选了"。
        self._group = QButtonGroup(self)
        self._group.addButton(self._rb_mine, 0)
        self._group.addButton(self._rb_theirs, 1)
        for rb, text, role in ((self._rb_mine, mine, 'mine'),
                               (self._rb_theirs, theirs, 'theirs')):
            box = QFrame()
            box.setProperty('role', 'card')
            bl = QVBoxLayout(box)
            bl.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                                  T.SPACE['sm'], T.SPACE['sm'])
            head = QLabel('你写的' if role == 'mine' else 'TA 写的')
            head.setProperty('role', 'caption')
            shown, total = _split_display(text)
            body = QLabel(shown or '（空）')
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            bl.addWidget(head)
            bl.addWidget(body)
            if total > DISPLAY_LIMIT:
                # 只显示开头，但**提交的是全文**：这句话必须写出来，
                # 否则人会以为"留下的是这 300 字"。
                more = QPushButton(f'共 {total:,} 字 · 看全文')
                more.setFlat(True)
                more.setProperty('role', 'link')
                more.setCursor(Qt.PointingHandCursor)
                more.clicked.connect(
                    lambda _=False, t=text, n=role: self._show_full(t, n))
                bl.addWidget(more, 0, Qt.AlignLeft)
            bl.addWidget(rb, 0, Qt.AlignLeft)
            row.addWidget(box, 1)
            rb.setProperty('side', role)
        self._rb_mine.toggled.connect(self._sync)
        lay.addLayout(row)
        self._rb_theirs.setChecked(True)          # 默认 = 较新的那份（TA）
        self._mine, self._theirs = mine, theirs

    def _show_full(self, text, role):
        who = '你写的' if role == 'mine' else 'TA 写的'
        dlg = _FullTextDialog(f'{who}（全文）', text, self)
        dlg.exec()

    # ---- 选择 ----

    def choose(self, side: str):
        if side == 'mine':
            self._rb_mine.setChecked(True)
        else:
            self._rb_theirs.setChecked(True)

    def set_custom(self, text: str):
        self._custom = ''
        self._rb_theirs.setChecked(True)          # 先落单选，再放自定义文本
        self._custom = str(text)

    def value(self) -> str:
        """`mine` / `theirs` / 自己拼的那段文本（空 = 还没决定）。"""
        if self._custom:
            return self._custom
        return 'mine' if self._rb_mine.isChecked() else 'theirs'

    def _sync(self, *_):
        # 手动改选后，上一次"自己拼的"就不再代表这次选择
        self._custom = ''


class CollabConflictDialog(QDialog):
    """
    「你和 TA 改了同一句」的列表。

    `conflicts` 就是 `collab_store.push()` 返回的 `result['conflicts']`。
    确定后 `self.resolutions` 是 `resolve_conflicts()` 的入参。
    """

    def __init__(self, parent=None, conflicts=None, title=None):
        super().__init__(parent)
        items = list(conflicts or ())
        self._conflicts = [c for c in items
                           if str(c.get('kind') or 'fields')
                           in ('fields', 'insert_same_slot')]
        self._others = [c for c in items
                        if str(c.get('kind') or 'fields')
                        not in ('fields', 'insert_same_slot')]
        self.resolutions = []
        self.setWindowTitle(title or '你和 TA 改了同一句')
        self.resize(860, 640)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])

        tip = QLabel('下面这些句子，你和 TA 都动过。选一个留下的版本；'
                     '也可以两句都留，或者自己拼一版。\n'
                     '没想好的先跳过 —— 其余的改动已经提交了。')
        tip.setWordWrap(True)
        tip.setProperty('role', 'note')
        root.addWidget(tip)

        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget()
        self._list = QVBoxLayout(inner)
        self._list.setSpacing(T.SPACE['md'])
        self._rows = {}
        for i, c in enumerate(self._conflicts):
            self._list.addWidget(self._build_group(i, c))
        if self._others:
            self._list.addWidget(self._build_others())
        self._list.addStretch(1)
        area.setWidget(inner)
        root.addWidget(area, 1)

        bar = QHBoxLayout()
        self._summary = QLabel('')
        self._summary.setProperty('role', 'caption')
        bar.addWidget(self._summary, 1)
        root.addLayout(bar)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('应用选择并提交')
        buttons.button(QDialogButtonBox.Ok).setProperty('variant', 'primary')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh_summary()

    def _build_group(self, index: int, c: dict) -> QGroupBox:
        who = _name_of(c)
        kind = str(c.get('kind') or 'fields')
        title = (f'你和 {who} 在同一个位置各插了一句'
                 if kind == 'insert_same_slot'
                 else f'你和 {who} 改了同一句')
        g = QGroupBox(f'{title}（{c.get("line_id", "")}）')
        lay = QVBoxLayout(g)
        lay.setSpacing(T.SPACE['sm'])
        skip = QCheckBox('这次先跳过这一句（回头再说）')
        rows = {}
        if kind == 'insert_same_slot':
            mine_p = c.get('mine') or {}
            theirs_p = c.get('theirs') or {}
            fr = _FieldRow('Text', str(mine_p.get('Text') or ''),
                           str(theirs_p.get('Text') or ''), label='台词')
            rows['Text'] = fr
            lay.addWidget(fr)
        else:
            for f in (c.get('fields') or ()):
                field = str(f.get('field') or '')
                from storygraph.collab.collab_store import field_label
                fr = _FieldRow(field, f.get('mine', ''), f.get('theirs', ''),
                               label=field_label(field))
                rows[field] = fr
                lay.addWidget(fr)

        # 「自己拼一版」：**永远预填**（默认那一份），不给空框。
        # 两边各插一行时没有"拼"这回事（两行本来就是两行）→ 不给这个按钮。
        tools = QHBoxLayout()
        if kind != 'insert_same_slot':
            merge_btn = QPushButton('自己拼一版…')
            merge_btn.setToolTip('打开一个编辑框，里面已经填好当前选中的版本，'
                                 '改完确认即可。')
            merge_btn.clicked.connect(lambda *_, i=index: self._custom_merge(i))
            tools.addWidget(merge_btn)

        keep_btn = QPushButton('两句都留 ▾')
        keep_btn.setToolTip('这一句留一份，另一份作为新的一行插在它旁边。')
        order = QComboBox()
        order.addItem('排在我后面', 'mine_first')
        order.addItem(f'排在 {who} 后面', 'theirs_first')
        keep_btn.clicked.connect(
            lambda *_, i=index, o=order: self._keep_both(i, o))
        tools.addWidget(keep_btn)
        tools.addWidget(order)
        tools.addStretch(1)
        lay.addLayout(tools)
        lay.addWidget(skip)

        self._rows[index] = {'group': g, 'fields': rows, 'skip': skip,
                             'conflict': c, 'keep_both': None}
        return g

    def _build_others(self) -> QGroupBox:
        g = QGroupBox('其余需要你知道的（这些不能自动处理）')
        lay = QVBoxLayout(g)
        for c in self._others:
            lab = QLabel('· ' + str(c.get('hint') or c.get('kind') or ''))
            lab.setWordWrap(True)
            lay.addWidget(lab)
        return g

    # ---- 交互 ----

    def _custom_merge(self, index: int):
        """预填编辑框：先放当前选中的那一版（不给人一个空框）。"""
        info = self._rows[index]
        c = info['conflict']
        rows = info['fields']
        seed = ''
        for field, fr in rows.items():
            if field == 'Text' or not seed:
                seed = _text_of(c.get('mine' if fr.value() == 'mine'
                                      else 'theirs'), field)
        dlg = QDialog(self)
        dlg.setWindowTitle('自己拼一版')
        dlg.resize(620, 420)
        lay = QVBoxLayout(dlg)
        hint = QLabel('下面是当前选中的版本，直接改就行。')
        hint.setProperty('role', 'caption')
        lay.addWidget(hint)
        edit = QPlainTextEdit(seed)
        lay.addWidget(edit, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText('用这一版')
        bb.button(QDialogButtonBox.Cancel).setText('取消')
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        text = edit.toPlainText()
        # 只有一个字段冲突时直接定为文本；多字段时写进 Text（最常见的那格）
        target = 'Text' if 'Text' in rows else next(iter(rows))
        rows[target].set_custom(text)

    def _keep_both(self, index: int, order: QComboBox):
        info = self._rows[index]
        info['keep_both'] = str(order.currentData() or 'mine_first')
        info['skip'].setChecked(False)
        self._refresh_summary()

    def _refresh_summary(self):
        n = len(self._conflicts)
        skipped = sum(1 for i in self._rows if self._rows[i]['skip'].isChecked())
        self._summary.setText(f'{n} 句要你决定 · 跳过 {skipped} 句' if n else '')

    # ---- 结果 ----

    def accept(self):
        self.resolutions = conflict_choices(self._rows)
        super().accept()


def conflict_choices(rows: dict) -> list:
    """
    `_rows` → `resolve_conflicts` 的入参（纯函数，便于无头测试）。

    · 勾了「跳过」→ 不产出（这一句留在本地，下次再提交）
    · 选了「两句都留」→ `{'kind': 'keep_both', 'order': …}`
    · 其余 → `{'kind': 'fields', 'values': {字段: 'mine'|'theirs'|文本}}`
    """
    out = []
    for index in sorted(rows or ()):
        info = rows[index]
        if info.get('skip') is not None and info['skip'].isChecked():
            continue
        c = info.get('conflict') or {}
        lid = str(c.get('line_id') or '').strip()
        if not lid:
            continue
        kind = str(c.get('kind') or 'fields')
        if info.get('keep_both'):
            # 两边各插一行时"都留"不是拼文本，是把我的新行排到 TA 那行旁边
            out.append({'line_id': lid,
                        'kind': 'insert_both' if kind == 'insert_same_slot'
                                else 'keep_both',
                        'order': info['keep_both'],
                        'theirs_line_id': c.get('theirs_line_id', '')})
            continue
        values = {}
        for field, fr in (info.get('fields') or {}).items():
            got = fr.value()
            if got:
                values[field] = got
        if kind == 'insert_same_slot':
            # 没选「两句都留」= 用 TA 那一行（我的新行不进库，本地也删掉），
            # 否则下一次 push 还会报同一个位置。
            if values.get('Text') == 'theirs':
                out.append({'line_id': lid, 'kind': 'insert_drop',
                            'theirs_line_id': c.get('theirs_line_id', '')})
            continue
        if values:
            out.append({'line_id': lid, 'kind': 'fields', 'values': values})
    return out
