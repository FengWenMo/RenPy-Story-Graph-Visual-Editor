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

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme

#: Ren'Py label 名：字母/数字/下划线，不以数字开头；允许点号分段
#: （`main.route` / `side.choice` —— 真实项目里用点号做层级很常见，
#: 解析器 `[\w.]+` 本来就认，创建这边不该比解析器更窄）。
#: （中文、空格、短横线都会生成**跑不起来**的脚本，必须在这里挡住。）
_LABEL_RE = re.compile(
    r'^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$')


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


class SyncPreviewDialog(_BaseDialog):
    """
    「补全缺失章节 / 刷新已导入章节」执行**之前**给人看的那一屏。

    用户 2026-10-22：
    > 补全相关章节和重载已导入章节到底谁让无源变有源？我没搞明白。

    两个动作以前都是"点完才知道发生了什么"。这里把"将导入哪些文件、
    哪些 label 会新增 / 消失"先摆出来；名字也改成用户能预测的说法：

      · 补全缺失章节… —— 从项目里把缺的文件找回来；
      · 刷新已导入章节… —— 重读已有文件，源里删掉的 label 会从图上移除。
    """

    def __init__(self, parent=None, title: str = '同步剧情图',
                 intro: str = '', detail: str = '',
                 ok_text: str = '继续', warn: str = ''):
        super().__init__(parent, title)
        self.resize(720, 520)
        head = QLabel(intro)
        head.setWordWrap(True)
        head.setProperty('role', 'section')
        self._root.addWidget(head)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setPlainText(str(detail or ''))
        self._text.setFont(QFont(T.FONTS['mono'][0], T.FONTS['mono'][1]))
        self._root.addWidget(self._text, 1)

        if warn:
            warning = QLabel(warn)
            warning.setWordWrap(True)
            warning.setProperty('role', 'warn')
            self._root.addWidget(warning)
        self._add_buttons(ok_text)


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
        from storygraph.collab.collab_store import auto_merge_summary
        self._say(f'✔ 已提交：新增 {len(r.get("added") or ())} / '
                  f'更新 {len(r.get("updated") or ())} / '
                  f'未变 {len(r.get("unchanged") or ())} / '
                  f'复活 {len(r.get("revived") or ())}')
        # **工具替她做了什么，必须说**（用户 2026-10-19："自动合并要报告"）
        self._say(f'   {auto_merge_summary(r)}')
        kept = r.get('kept') or []
        if kept:
            self._say(f'   ⚠ 你删过、但对方改过的 {len(kept)} 句已保留：'
                      + '；'.join(str(k.get('hint') or '') for k in kept[:3]))
        rejected = r.get('rejected') or []
        if rejected:
            self._say(f'   ⚠ 被锁着没法提交 {len(rejected)} 句：'
                      + '；'.join(str(x.get('reason') or '') for x in rejected[:3]))

        conflicts = [c for c in (r.get('conflicts') or ())
                     if str(c.get('kind') or '') == 'fields']
        others = [c for c in (r.get('conflicts') or ())
                  if str(c.get('kind') or '') != 'fields']
        if conflicts:
            # **两栏 + 三个按钮**（不是三栏 diff）：用户 2026-10-19 的口径
            from storygraph.ui_qt.dialogs_collab import CollabConflictDialog
            dlg = CollabConflictDialog(self, conflicts)
            if dlg.exec() == QDialog.Accepted and dlg.resolutions:
                done = self._session.collab_resolve(
                    self.db_path.text(), self.sheet_path.text(), actor,
                    dlg.resolutions)
                rr = done.get('result') or {}
                if not done.get('ok'):
                    self._say(f'   ✗ 应用选择失败：{done.get("error")}')
                else:
                    self._say(f'   ✔ 已应用 {len(rr.get("applied") or ())} 句的选择'
                              + (f'，两句都留 {len(rr.get("both_kept") or ())} 处'
                                 if rr.get('both_kept') else ''))
                    for err in (rr.get('errors') or ())[:3]:
                        self._say(f'   ⚠ {err.get("line_id")}：{err.get("error")}')
        for c in others:
            self._say(f'   ⚠ {c.get("hint") or c.get("kind")}')

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

    def __init__(self, parent=None, existing=(), default_dir=None,
                 known_files=()):
        super().__init__(parent, '添加新章节')
        self._existing = set(existing or ())
        self.name = ''
        self.operations = ''
        #: 要写出的 `.rpy`（空串 = 只在图里画结构，不建文件）。
        self.file_path = ''
        #: 用户手动改过"写入文件"那一格之后，不再跟着章节名自动变。
        self._file_edited = False

        form = QFormLayout()
        form.setSpacing(T.SPACE['sm'])
        self._name = QLineEdit()
        self._name.setPlaceholderText('英文 label 名，例如 chapter_02 或 main.route')
        self._name.textChanged.connect(lambda *_: self._sync_default_file())
        self._name.textChanged.connect(lambda *_: self._ok())
        form.addRow('章节名（label）', self._name)

        # "写入文件"：**浏览按钮 + 已导入文件下拉**，不再要求用户手打
        # 完整路径（2026-10-22 用户："我就不打开编辑器！我就要在工具内改！"）。
        self._file = QLineEdit()
        self._file.setPlaceholderText(
            '留空 = 只在图里画结构（不建文件，双击后没有剧本可编辑）')
        self._file.textChanged.connect(self._on_file_edited)
        file_row = QWidget()
        file_lay = QHBoxLayout(file_row)
        file_lay.setContentsMargins(0, 0, 0, 0)
        file_lay.setSpacing(T.SPACE['xs'])
        file_lay.addWidget(self._file, 1)
        self._browse_btn = QPushButton('选已有…')
        self._browse_btn.setToolTip('选一个已有 .rpy（会往它末尾追加这个 label）')
        self._browse_btn.clicked.connect(self._pick_existing_file)
        file_lay.addWidget(self._browse_btn, 0)
        self._new_btn = QPushButton('新建…')
        self._new_btn.setToolTip('选一个新的 .rpy 位置（不存在才新建）')
        self._new_btn.clicked.connect(self._pick_new_file)
        file_lay.addWidget(self._new_btn, 0)
        form.addRow('写入文件', file_row)

        self._known = QComboBox()
        self._known.setToolTip(
            '从剧情图里已经导入的 .rpy 里选一个；选了就往它末尾追加 label')
        self._known.addItem('（新建文件 / 手动填写）', '')
        for path in (known_files or ()):
            p = str(path or '')
            if p:
                self._known.addItem(Path(p).name, p)
        self._known.currentIndexChanged.connect(self._on_known_changed)
        form.addRow('已导入文件', self._known)

        self._ops = QPlainTextEdit()
        self._ops.setPlaceholderText('可选：初始化代码，一行一条，例如\n'
                                     'score = 0\n'
                                     'visited_roof = False')
        self._ops.setFixedHeight(96)
        form.addRow('变量操作', self._ops)
        self._root.addLayout(form)

        # 章节名 与 默认文件路径 的联动：`chapter_02` → `<default_dir>/chapter_02.rpy`
        self._default_dir = str(default_dir or '').rstrip('/\\')

        hint = QLabel(
            "章节名会成为 Ren'Py 的 label —— "
            "只能字母/数字/下划线（可用点号分段，如 main.route），"
            "且不能以数字开头。\n"
            "「写入文件」非空时会先把它写进脚本（`label 名:` + 变量操作 + "
            "`return`），双击就能编辑；填一个**已有的 .rpy** 就往那个文件末尾"
            "追加第二个 label（一个文件放多章是合法的）；留空则只是图上的一个"
            "方块，之后用「新建 / 绑定源文件」再给它配文件。\n"
            "「变量操作」会跟着写进脚本，不只是记在图上。")
        hint.setProperty('role', 'caption')
        hint.setWordWrap(True)
        self._root.addWidget(hint)
        if self._default_dir:
            where = QLabel(f'默认目录：{self._default_dir}'
                           f'（跟随项目里已有章节的位置）')
            where.setProperty('role', 'caption')
            where.setWordWrap(True)
            self._root.addWidget(where)
        self._add_buttons('创建')
        self._name.setFocus()

    # ------------------------------------------------------------------

    def _pick_existing_file(self):
        """选一个已有 `.rpy`，填进「写入文件」（会追加 label，不覆盖）。"""
        current = self._file.text().strip()
        start = current or (str(Path(self._default_dir) / 'chapter.rpy')
                            if self._default_dir else '')
        path, _ = QFileDialog.getOpenFileName(
            self, '选一个已有 .rpy（会往末尾追加 label）', start,
            "Ren'Py 脚本 (*.rpy)")
        if path:
            self._file.setText(path)
            self._file_edited = True

    def _pick_new_file(self):
        """选一个新的 `.rpy` 位置，填进「写入文件」。"""
        current = self._file.text().strip()
        start = current or (str(Path(self._default_dir) / 'chapter.rpy')
                            if self._default_dir else '')
        path, _ = QFileDialog.getSaveFileName(
            self, '新建一个 .rpy', start, "Ren'Py 脚本 (*.rpy)")
        if path:
            self._file.setText(path)
            self._file_edited = True

    def _on_known_changed(self, index: int):
        """从"已导入文件"下拉选一个 → 写进路径格，并锁住自动跟随。"""
        path = str(self._known.itemData(index) or '') if index >= 0 else ''
        if not path:
            return
        self._file.setText(path)
        self._file_edited = True

    def _sync_default_file(self):
        """章节名变了 → 默认文件跟着变（除非用户手动改过那一格）。"""
        if self._file_edited:
            return
        name = self._name.text().strip()
        if not name or not self._default_dir:
            self._file.blockSignals(True)
            self._file.setText('')
            self._file.blockSignals(False)
            return
        # 用 `Path` 拼，别手写 `/` —— Windows 上会写出 `game/xxx.rpy` 这种
        # 混着分隔符的路径（能用，但用户复制出去很别扭）。
        from pathlib import Path as _P
        self._file.blockSignals(True)
        self._file.setText(str(_P(self._default_dir) / f'{name}.rpy'))
        self._file.blockSignals(False)
        self._known.blockSignals(True)
        self._known.setCurrentIndex(0)
        self._known.blockSignals(False)

    def _on_file_edited(self, *_):
        # 只有**用户**改这一格才锁住联动；`_sync_default_file` 用 blockSignals
        # 写入的不会走到这里。
        self._file_edited = True

    def _on_accept(self):
        name = self._name.text().strip()
        if not valid_label(name):
            self._fail("章节名不合法：只能用字母/数字/下划线，且不能以数字开头"
                       "（中文和空格会生成跑不起来的脚本）。")
            return
        if name in self._existing:
            self._fail(f'已经有一个叫「{name}」的章节了。')
            return
        path = self._file.text().strip()
        if path and not path.lower().endswith('.rpy'):
            self._fail('「写入文件」必须以 .rpy 结尾（Ren\'Py 只读这种脚本）。')
            return
        if path:
            from pathlib import Path as _P
            _p = _P(path)
            if _p.exists():
                # **允许往已有文件里追加第二个 label**（2026-10-21 用户问的
                # "我想在一个 rpy 文件里写出第二个 label 该怎么操作？"）——
                # 一个文件放多章在 Ren'Py 里完全合法。只拦**同名 label**：
                # 重名会让 Ren'Py 直接起不来。
                try:
                    import re as _re
                    from storygraph.story.source_style import read_text as _read
                    text = _read(_p)[0]
                except (OSError, UnicodeDecodeError) as e:
                    self._fail(f'读不了 {_p.name}（{e}），不敢往里追加。')
                    return
                if _re.search(rf'^label\s+{_re.escape(name)}\s*(?:\(|:)',
                              text, _re.M):
                    self._fail(f'{_p.name} 里已经有 label {name} 了 —— '
                               f'换个章节名，或把「写入文件」指向别的文件。')
                    return
        self.name = name
        self.operations = self._ops.toPlainText().strip()
        # 变量操作现在会**写进 .rpy**，所以先在对话框里挡掉语法错误 ——
        # 不能等写到文件里才由 Ren'Py 报（那时用户已经离开这个界面了）。
        bad = self._operations_error(self.operations)
        if bad:
            self._fail(bad)
            return
        self.file_path = path
        self.accept()

    @staticmethod
    def _operations_error(text: str) -> str:
        """一行一条变量操作；返回第一条语法错误的说明（空串 = 都能解析）。"""
        import ast
        for i, raw in enumerate(str(text or '').splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('$'):
                line = line[1:].strip()
            try:
                ast.parse(line, mode='exec')
            except SyntaxError as e:
                return (f'「变量操作」第 {i} 行不是合法的 Python：{raw.strip()}\n'
                        f'{e.msg}（这里一行只能写一条简单操作，'
                        f'例如 love += 1）')
        return ''


# ======================================================================
#  插入控制流卡（call / jump）
# ======================================================================

class FlowCardDialog(_BaseDialog):
    """
    在大 label 的某一拍后面插入一张 `call` / `jump` 卡。

    用户 2026-10-22 的核心诉求："在一个大 label 里面 call 或 jump 其他小
    label，然后再回到这个大 label。" 第一阶段先让已有 call 可见可改；
    这个对话框补上**插入**入口，否则用户还是得回源文件手写。
    """

    def __init__(self, parent=None, labels=(), default_type='call'):
        super().__init__(parent, '插入控制流卡')
        self.flow_type = 'call'
        self.target = ''

        form = QFormLayout()
        form.setSpacing(T.SPACE['sm'])
        self._type = QComboBox()
        self._type.addItem('↳ 调用（执行完会返回）', 'call')
        self._type.addItem('→ 跳转（不返回）', 'jump')
        idx = self._type.findData(str(default_type or 'call'))
        self._type.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow('类型', self._type)

        self._target = QComboBox()
        self._target.setEditable(True)
        self._target.setInsertPolicy(QComboBox.NoInsert)
        self._target.addItems([str(x) for x in (labels or ()) if str(x)])
        self._target.setPlaceholderText('目标 label 名（支持 main.route）')
        self._target.setToolTip(
            '从本文件已有的 label 里选，或直接写目标 label 名；\n'
            '跨文件调用也可以直接手写。')
        form.addRow('目标 label', self._target)
        self._root.addLayout(form)

        hint = QLabel(
            '插入的位置在当前这一拍后面。\n'
            'call：目标 label 执行到 return 后回到这里继续；\n'
            'jump：不会返回，这一章在跳转处结束。')
        hint.setProperty('role', 'caption')
        hint.setWordWrap(True)
        self._root.addWidget(hint)
        self._add_buttons('插入')
        self._target.setFocus()

    def _on_accept(self):
        target = self._target.currentText().strip()
        if not valid_label(target):
            self._fail('目标 label 不合法：只能字母/数字/下划线，可用点号分段，'
                       '且不能以数字开头。')
            return
        self.flow_type = str(self._type.currentData() or 'call')
        self.target = target
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


class VariableWorkbenchDialog(_BaseDialog):
    """
    变量工作台（P1-2）：**变量清单 + 初值 + 每条操作落在第几拍**。

    用户 2026-10-22：

    > 检测和修改变量似乎是不行……我没学会用。

    症结不是"不会用"，是界面只有一整段 Python 文本框：看不到变量名清单、
    看不到 `default` / `define` 的初值、也看不到某条赋值发生在第几拍。
    这个对话框把同一份数据拆成两层：

      · 上面 = 变量表（初值 / 操作条数 / 出现位置）；
      · 下面 = 按源顺序排列的操作，选中一条能改、能删、能**跳到那一拍**。

    保存仍然只交接**剧情变量列表**（技术代码由 `set_node_variables` 原位
    保留）；`jump_index` 非空表示用户点了"定位到这一拍"。
    """

    def __init__(self, parent=None, label_name: str = '', data=None,
                 on_check=None):
        super().__init__(parent, f'变量工作台 · {label_name}')
        self.resize(860, 620)
        data = dict(data or {})
        self.label_name = str(label_name or data.get('label') or '')
        self.variables = list(data.get('operations') or [])
        self.jump_index = None
        #: 新增操作要放在哪一拍后面（用户选中哪条就跟着哪条；None = 本章末尾）
        self.insert_beat = None
        #: `{新操作下标: 拍下标}` —— 交回会话，按条决定插进哪个块
        #: （`-1` = 用户明确选了"本章末尾"）。只传一个 `insert_beat` 时，
        #: 往两个块里各插一条会全落到同一拍，块内插入就名存实亡了。
        self.anchors: dict = {}
        self._anchors: dict = {}
        self._blocks: dict = {}
        self._on_check = on_check
        self._beat_by_index = {}
        for row in (data.get('variables') or ()):
            for op in (row.get('operations') or ()):
                i = int(op.get('index', -1))
                self._beat_by_index[i] = op.get('beat_index')
                self._blocks[i] = str(op.get('block') or '')

        head = QLabel(f'章节：{self.label_name}'
                      + (f'　·　{Path(data.get("file")).name}'
                         if data.get('file') else ''))
        head.setProperty('role', 'section')
        self._root.addWidget(head)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ['变量', '初值', '本拍操作', '出现位置'])
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 150)
        self._table.setColumnWidth(1, 150)
        self._table.setColumnWidth(2, 360)
        for row in (data.get('variables') or ()):
            ops = list(row.get('operations') or ())
            texts = '；'.join(str(op.get('text') or '') for op in ops[:3])
            if len(ops) > 3:
                texts += f' 等 {len(ops)} 条'
            places = sorted({int(op.get('beat_no') or 0) for op in ops
                             if op.get('beat_no')})
            where = ('第 ' + ' / '.join(str(n) for n in places) + ' 拍'
                     if places else ('—' if not ops else '未对位'))
            i = self._table.rowCount()
            self._table.insertRow(i)
            name_item = QTableWidgetItem(str(row.get('name') or ''))
            name_item.setData(Qt.UserRole, str(row.get('name') or ''))
            self._table.setItem(i, 0, name_item)
            init_item = QTableWidgetItem(str(row.get('initial') or '') or '—')
            if row.get('declared_by'):
                init_item.setToolTip(
                    f"{row.get('declared_by')} · 第 {row.get('decl_line')} 行")
            self._table.setItem(i, 1, init_item)
            self._table.setItem(i, 2, QTableWidgetItem(texts or '—'))
            self._table.setItem(i, 3, QTableWidgetItem(where))
        self._root.addWidget(self._table, 2)

        cap = QLabel('这一章的变量操作（按源顺序；选中一条可以改 / 删 / 定位）')
        cap.setProperty('role', 'caption')
        self._root.addWidget(cap)
        self._ops = QListWidget()
        self._root.addWidget(self._ops, 3)

        self._editor = QLineEdit()
        self._editor.setPlaceholderText('选中上面一条操作，或直接在这里写一条新的')
        self._root.addWidget(self._editor)

        place_row = QHBoxLayout()
        place_row.addWidget(QLabel('新增放在'))
        self._place = QComboBox()
        self._place.addItem('选中这条之后（同一块内）', 'anchor')
        self._place.addItem('本章末尾（顶层）', 'end')
        self._place.setToolTip(
            '默认跟着选中的那条走：它在 if / for 里，新增就落在同一个块内；'
            '选「本章末尾」则放到 label 结束前（顶层）。')
        self._place.currentIndexChanged.connect(self._update_target_hint)
        place_row.addWidget(self._place, 1)
        self._target_hint = QLabel('')
        self._target_hint.setProperty('role', 'caption')
        self._target_hint.setWordWrap(True)
        place_row.addWidget(self._target_hint, 2)
        self._root.addLayout(place_row)

        row = QHBoxLayout()
        for text, slot, tip in (
                ('保存这条修改', self._save_op, '只改这一条，不碰其它赋值'),
                ('删除这条', self._delete_op, '从本章变量操作里移除这一条'),
                ('＋ 新增变量…', self._add_var, '追加一条 name = 0'),
                ('＋ 新增操作…', self._add_op, '追加一条自由写法，例如 love += 2'),
                ('定位到这一拍', self._locate, '关掉本窗口并跳到那条赋值所在的拍'),
                ('语法检查', self._check, '逐条检查是否能被 Python 解析')):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch(1)
        self._root.addLayout(row)

        self._hint = QLabel('')
        self._hint.setProperty('role', 'caption')
        self._hint.setWordWrap(True)
        self._root.addWidget(self._hint)
        if data.get('warning'):
            self._hint.setText(f'⚠ {data["warning"]}')

        self._table.itemSelectionChanged.connect(self._filter_ops)
        self._ops.currentRowChanged.connect(self._load_op)
        self._ops.currentRowChanged.connect(self._update_target_hint)
        self._add_buttons('保存变量')
        self._rebuild_ops()
        self._update_target_hint()

    # ------------------------------------------------------------------

    def _item_for(self, i: int) -> QListWidgetItem:
        beat = self._beat_by_index.get(i)
        if beat is not None:
            where = f'第 {beat + 1} 拍'
        else:
            where = '本章末尾' if i in self._anchors else '（未对位）'
        block = str(self._blocks.get(i) or '')
        if block:
            where += f' · 在 {block}'
        item = QListWidgetItem(f'{where} · {self.variables[i]}')
        item.setData(Qt.UserRole, i)
        item.setToolTip(block or '顶层（label 里直接执行）')
        return item

    def _rebuild_ops(self):
        current = self._ops.currentRow()
        self._ops.blockSignals(True)
        self._ops.clear()
        for i in range(len(self.variables)):
            self._ops.addItem(self._item_for(i))
        self._ops.blockSignals(False)
        if 0 <= current < self._ops.count():
            self._ops.setCurrentRow(current)

    def _selected_index(self):
        item = self._ops.currentItem()
        return int(item.data(Qt.UserRole)) if item is not None else -1

    def _load_op(self, _row: int):
        i = self._selected_index()
        if 0 <= i < len(self.variables):
            self._editor.setText(str(self.variables[i]))

    def _filter_ops(self):
        """选中变量行 → 只留下涉及它的操作（不选 = 全部）。"""
        name = ''
        items = self._table.selectedItems()
        if items:
            name = str(items[0].data(Qt.UserRole) or '')
        self._ops.blockSignals(True)
        self._ops.clear()
        from storygraph.core.utils import extract_variables
        for i, text in enumerate(self.variables):
            if name and name not in (extract_variables(text) or []):
                continue
            self._ops.addItem(self._item_for(i))
        self._ops.blockSignals(False)

    def _save_op(self):
        i = self._selected_index()
        text = self._editor.text().strip()
        if i < 0 or not text:
            self._hint.setText('先在下面选中一条操作，再写内容。')
            return
        self.variables[i] = text
        self._rebuild_ops()
        self._ops.setCurrentRow(i)
        self._hint.setText(f'✔ 已改第 {i + 1} 条（还没写盘，点「保存变量」生效）。')

    def _delete_op(self):
        i = self._selected_index()
        if i < 0:
            self._hint.setText('先选中一条操作。')
            return
        del self.variables[i]
        self._beat_by_index = {
            (k - 1 if k > i else k): v
            for k, v in self._beat_by_index.items() if k != i}
        self._anchors = {(k - 1 if k > i else k): v
                         for k, v in self._anchors.items() if k != i}
        self._blocks = {(k - 1 if k > i else k): v
                        for k, v in self._blocks.items() if k != i}
        self._rebuild_ops()
        self._hint.setText('已删掉这一条（点「保存变量」生效）。')

    def _new_op_block(self, beat, src_index) -> str:
        """新增那条会落在哪个块里（用来在界面上说清楚，不影响写回）。"""
        if beat is None or int(beat) < 0:
            return ''
        if 0 <= int(src_index) < len(self.variables):
            text = str(self._blocks.get(int(src_index)) or '')
            if text:
                return text
        for i, b in self._beat_by_index.items():
            if b is not None and int(b) == int(beat):
                return str(self._blocks.get(i) or '')
        return ''

    def _update_target_hint(self, *_args):
        """「新增会放到哪」：跟随选中的那条与「新增放在」下拉。"""
        label = getattr(self, '_target_hint', None)
        if label is None:
            return
        i = self._selected_index()
        if (getattr(self, '_place', None) is not None
                and self._place.currentData() == 'end'):
            label.setText('会放到本章末尾（顶层）')
            return
        beat = self._beat_by_index.get(i) if i >= 0 else self.insert_beat
        if beat is None:
            label.setText('会放到本章末尾（顶层）')
            return
        block = self._new_op_block(beat, i)
        label.setText(f'会放到第 {int(beat) + 1} 拍'
                      + (f'的「{block}」块内' if block else '（顶层）'))

    def _target_beat(self):
        """新增操作放在哪一拍后面：选中操作的拍 → 选中变量的最后一条 → None。"""
        i = self._selected_index()
        if i >= 0 and self._beat_by_index.get(i) is not None:
            return self._beat_by_index.get(i)
        return self.insert_beat

    def _add_var(self):
        name, ok = QInputDialog.getText(
            self, '新增变量', '变量名（会追加一条 `名字 = 0`）：')
        name = str(name or '').strip()
        if not ok or not name:
            return
        if not re.match(r'^[A-Za-z_]\w*$', name):
            self._hint.setText('变量名不合法：只能字母/数字/下划线，且不能以数字开头。')
            return
        beat = self._target_beat()
        if self._place.currentData() == 'end':
            beat = -1
        src = self._selected_index()
        i = len(self.variables)
        self.insert_beat = None if beat in (None, -1) else beat
        self.variables.append(f'{name} = 0')
        self._beat_by_index[i] = None if beat in (None, -1) else beat
        self._anchors[i] = beat if beat is not None else -1
        self._blocks[i] = self._new_op_block(beat, src)
        self._rebuild_ops()
        self._update_target_hint()
        self._hint.setText(f'✔ 已追加 {name} = 0（点「保存变量」生效）。')

    def _add_op(self):
        text, ok = QInputDialog.getText(
            self, '新增变量操作', '例如 love += 1 或 flag = True：')
        text = str(text or '').strip()
        if not ok or not text:
            return
        beat = self._target_beat()
        if self._place.currentData() == 'end':
            beat = -1
        src = self._selected_index()
        i = len(self.variables)
        self.insert_beat = None if beat in (None, -1) else beat
        self.variables.append(text)
        self._beat_by_index[i] = None if beat in (None, -1) else beat
        self._anchors[i] = beat if beat is not None else -1
        self._blocks[i] = self._new_op_block(beat, src)
        self._rebuild_ops()
        self._ops.setCurrentRow(self._ops.count() - 1)
        self._update_target_hint()
        block = self._blocks.get(i) or ''
        placed = self._beat_by_index.get(i)
        if block:
            where = f'会插进「{block}」块内'
        elif placed is not None:
            where = f'会放到第 {int(placed) + 1} 拍（顶层）'
        else:
            where = '会放到本章末尾（顶层）'
        self._hint.setText(f'✔ 已追加：{where}（点「保存变量」生效）。')

    def _locate(self):
        i = self._selected_index()
        beat = self._beat_by_index.get(i)
        if i < 0 or beat is None:
            self._hint.setText('这条操作没有对上哪一拍（可能是图上手工加的）。')
            return
        self.jump_index = int(beat)
        self.accept()

    def _check(self):
        errors = []
        if self._on_check is not None:
            try:
                errors = list(self._on_check(self.variables) or [])
            except Exception as e:                # noqa: BLE001
                errors = [f'检查失败：{e}']
        if errors:
            self._hint.setText('⚠ ' + '；'.join(errors))
        else:
            self._hint.setText(f'✔ {len(self.variables)} 条都能解析。')

    def _on_accept(self):
        if self._on_check is not None:
            try:
                errors = list(self._on_check(self.variables) or [])
            except Exception as e:                # noqa: BLE001
                errors = [f'检查失败：{e}']
            if errors:
                self._fail('有语法错误，先修再保存：' + '；'.join(errors[:3]))
                return
        # 空行会被滤掉 → 下标全变；anchor 必须跟着重排，否则会插错拍。
        kept = [(i, str(v)) for i, v in enumerate(self.variables)
                if str(v).strip()]
        self.variables = [text for _i, text in kept]
        self.anchors = {new_i: self._anchors[old_i]
                        for new_i, (old_i, _text) in enumerate(kept)
                        if old_i in self._anchors}
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

        hint = QLabel(
            '⚠ 图上的连线是草图：它只改剧情图，不会写进任何 .rpy，'
            '也不会生成新 label。\n'
            '要让脚本真的 call / jump，请打开卡片，用「↳ 插入控制流卡」；'
            '那条控制流会同时在图上出现实线。\n'
            '填了「菜单项」= 这一拍是个分支（menu）；'
            '只填「条件」= 条件跳转；都不填 = 直接 ' + 'jump/call' + '。')
        hint.setProperty('role', 'caption')
        hint.setWordWrap(True)
        self._root.addWidget(hint)
        self._add_buttons('创建草图连线')

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


__all__ = ['valid_label', 'AddNodeDialog', 'NodeOpsDialog',
           'VariableWorkbenchDialog', 'AddEdgeDialog', 'AnalysisDialog',
           'SyncPreviewDialog', 'confirm', 'EDGE_TYPES']
