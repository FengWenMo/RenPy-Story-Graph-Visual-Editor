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
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QTableWidget, QTableWidgetItem,
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

    2026-10-23 三处修正（用户截图原话："这个控制流卡的 UI 不对，下拉……
    缺失"）：
      · **目标清单以前只有当前这个文件**（实测真机 `chapter0_0.rpy` 里
        下拉只有 `chapter0_0` 一项）—— 想 call 的小 label 一个都不在。
        现在由调用方从项目目录给全（`window_card._flow_target_labels`），
        这里只负责显示与校验；
      · **目标默认空着**：从前默认选中第一项，手快一点就插了一张
        `call <这一章自己>`（等于在章头递归调自己）；
      · 补上「参数」「from」两格 —— `insert_flow_beat` 与卡片上的控制流条
        本来就支持，只有这个插入入口缺，用户得"插完再回去改一遍"。
    """

    def __init__(self, parent=None, labels=(), default_type='call'):
        super().__init__(parent, '插入控制流卡')
        self.flow_type = 'call'
        self.target = ''
        self.args = ''
        self.from_clause = ''
        # 252×179 是实测出来的默认尺寸：三行表单挤成一条，提示语被折成两行。
        self.resize(520, 260)
        self.setMinimumWidth(460)

        form = QFormLayout()
        form.setSpacing(T.SPACE['sm'])
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self._type = QComboBox()
        self._type.addItem('↳ 调用（执行完会返回）', 'call')
        self._type.addItem('→ 跳转（不返回）', 'jump')
        idx = self._type.findData(str(default_type or 'call'))
        self._type.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow('类型', self._type)

        names = [str(x) for x in (labels or ()) if str(x).strip()]
        self._target = QComboBox()
        self._target.setEditable(True)
        self._target.setInsertPolicy(QComboBox.NoInsert)
        self._target.addItems(names)
        # **不预选第一项**：空着 + placeholder，逼用户明确选一次。
        self._target.setCurrentIndex(-1)
        self._target.setPlaceholderText('目标 label 名（支持 main.route）')
        self._target.setMinimumWidth(320)
        # 下拉列表本身也别比输入框窄：长 label 名在弹层里被截掉等于没有。
        self._target.view().setMinimumWidth(320)
        self._target.setToolTip(
            '从项目里的 label 里选，或直接写目标 label 名；\n'
            '跨文件调用、点号分段（main.route）都可以手写。')
        form.addRow('目标 label', self._target)

        self._args = QLineEdit()
        self._args.setPlaceholderText('（可空）例如 (1, flag)')
        self._args.setToolTip(
            '`call side(1, 2)` 里括号那一段。留空 = 不传参。\n'
            '括号必须配平 —— 不配平的写法会让整份脚本解析不了。')
        form.addRow('参数', self._args)

        self._from = QLineEdit()
        self._from.setPlaceholderText('（可空）例如 _call_side_1')
        self._from.setToolTip(
            '`call side from _call_side_1` 里 from 后面那个标识。\n'
            '它是存档点：同一个 label 被多处 call 时靠它区分。\n'
            '不确定就留空 —— Ren\'Py 会自己生成。')
        form.addRow('from', self._from)
        self._root.addLayout(form)

        count = len(names)
        self._count = QLabel(
            f'项目里共 {count} 个 label 可选' if count
            else '项目里没找到别的 label —— 直接手写目标名也可以')
        self._count.setProperty('role', 'caption')
        self._count.setWordWrap(True)
        self._root.addWidget(self._count)

        self._hint = QLabel('')
        self._hint.setProperty('role', 'caption')
        self._hint.setWordWrap(True)
        self._root.addWidget(self._hint)
        self._on_type_changed()

        self._type.currentIndexChanged.connect(self._on_type_changed)
        self._add_buttons('插入')
        self._target.setFocus()

    def _on_type_changed(self):
        """`jump` 不接受参数与 from（Ren'Py 语法）→ 直接禁用，别让人白填。"""
        is_call = str(self._type.currentData() or 'call') == 'call'
        for field in (self._args, self._from):
            field.setEnabled(is_call)
        if not is_call:
            self._args.clear()
            self._from.clear()
        self._hint.setText(
            '插入的位置在当前这一拍后面。\n'
            + ('call：目标 label 执行到 return 后回到这里继续。'
               if is_call else
               'jump：不会返回 —— 这一章在跳转处结束（参数与 from 不适用）。'))

    def _on_accept(self):
        target = self._target.currentText().strip()
        if not valid_label(target):
            self._fail('目标 label 不合法：只能字母/数字/下划线，可用点号分段，'
                       '且不能以数字开头。')
            return
        kind = str(self._type.currentData() or 'call')
        args = self._args.text().strip() if kind == 'call' else ''
        from_clause = self._from.text().strip() if kind == 'call' else ''
        # **渲染 → 再解析**一次当校验：括号不配平、from 不合法这些
        # 在这里就挡住，而不是等导出时才崩（`render_flow_line` 只拼字符串）。
        from storygraph.story.flow import parse_flow_line, render_flow_line
        if parse_flow_line(render_flow_line(kind, target, args,
                                            from_clause)) is None:
            self._fail('参数要写成配平的一对括号（例如 (1, flag)）；'
                       'from 只能是标识符（例如 _call_side_1）。')
            return
        self.flow_type = kind
        self.target = target
        self.args = args
        self.from_clause = from_clause
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
        #: `{新操作下标: 落点}` —— 交回会话，按条决定插进哪个块。
        #: 落点有三种（session 的 `anchor_of` 认得全部）：
        #:   · `-1` = 用户明确选了"本章末尾"；
        #:   · `拍号` = 那一拍拍内容的末尾（块体内，默认）；
        #:   · `(拍号, 'head')` = 那一拍拍内容的**最前面**（"块首"）。
        #: 只传一个 `insert_beat` 时，往两个块里各插一条会全落到同一拍，
        #: 块内插入就名存实亡了。
        self.anchors: dict = {}
        self._anchors: dict = {}
        self._blocks: dict = {}
        self._on_check = on_check
        self._first_beat = data.get('first_beat')
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
        # 2026-10-22：这一段 `python:` 块在这里只能当"一条操作"看 —— 想按条
        # 改里面的赋值，得先把它们搬到脚本层（右栏「⤴ 搬出 python 块…」）。
        # 不说这一句，用户会以为"工作台里改不了块里的赋值"是工具坏了。
        n_block_ops = sum(1 for op in self.variables if '\n' in str(op))
        if n_block_ops:
            hint = QLabel(
                f'⚠ 有 {n_block_ops} 条操作是**一整段 python 块**'
                f'（块里的行这里只能当一段看，改一条得整段重写）。'
                f'关掉本窗口后点右栏「⤴ 搬出 python 块…」，'
                f'可以把块里的单行赋值搬成 `$` 行，再回来就能逐条改。')
            hint.setProperty('role', 'caption')
            hint.setWordWrap(True)
            self._root.addWidget(hint)

        self._editor = QLineEdit()
        self._editor.setPlaceholderText('选中上面一条操作，或直接在这里写一条新的')
        self._root.addWidget(self._editor)

        place_row = QHBoxLayout()
        place_row.addWidget(QLabel('新增放在'))
        self._place = QComboBox()
        self._place.addItem('选中这条之后（同一块内）', 'anchor')
        self._place.addItem('块首（选中这条的块最前面）', 'head')
        self._place.addItem('本章末尾（顶层）', 'end')
        # 这一章**有哪些块**（2026-10-22）：从前"块首"只能跟着选中那条走，
        # 想往另一个 `if` 的块首插，得先在那堆操作里找到块里的某条赋值。
        # 把块本身列出来 → 不用先选中，也不会选错块。
        self._blocks_list = [dict(b) for b in (data.get('blocks') or ())
                             if isinstance(b, dict) and b.get('text')]
        if self._blocks_list:
            self._place.insertSeparator(self._place.count())
            for blk in self._blocks_list:
                text = str(blk.get('text') or '').strip()
                anchor = blk.get('anchor')
                # 显示**落点拍**（循环体的第一拍），与右侧那句"会插进第 N 拍"
                # 同一口径 —— 两处说两个拍号只会让人以为插错了地方。
                ref = anchor if anchor is not None else blk.get('beat')
                try:
                    beat_no = int(ref) + 1
                except (TypeError, ValueError):
                    beat_no = 0
                where = f'（第 {beat_no} 拍）' if beat_no else ''
                self._place.addItem(f'块首：{text}{where}',
                                    ('block', 'head', anchor, text))
                self._place.addItem(f'块尾：{text}{where}',
                                    ('block', 'end', anchor, text))
        self._place.setToolTip(
            '默认跟着选中的那条走：它在 if / while 里，新增就落在同一个块内；'
            '选「块首」则插到那个块的第一行；'
            '选「本章末尾」则放到 label 结束前（顶层）。'
            '下面列的是**这一章的所有块** —— 选一个就能直接插进去，不用先选中。')
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
        anchor = self._anchors.get(i)
        if (isinstance(anchor, (tuple, list)) and len(anchor) > 1
                and str(anchor[1]) == 'head'):
            where += ' · 块首'
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

    def _head_beat(self):
        """
        "块首"落在哪一拍：

          · 选中的那条在 `if` / `for` 里 → **它所在的那一拍**（块体就是这一拍，
            插到 `code` 最前面就是块的第一行）；
          · 选中的那条在顶层 → **本章第一拍**（= label 开头，那条赋值会在
            第一句台词之前执行）。

        返回 `None` = 判不出来（**没选**、这条对不上拍、或数据里没给第一拍）。
        没选时**不猜成"本章开头"**：块首的语义就是"跟着选中那条所在的块"，
        没有选中就没有块 —— 硬塞一个落点只会让产物与提示对不上。
        """
        i = self._selected_index()
        if i < 0:
            return None
        beat = self._beat_by_index.get(i)
        block = str(self._blocks.get(i) or '')
        if block:
            # 声明了在块里却对不上拍：不许悄悄退到"本章开头"（那是另一个位置）
            try:
                return int(beat) if beat is not None else None
            except (TypeError, ValueError):
                return None
        first = self._first_beat
        try:
            return int(first) if first is not None else None
        except (TypeError, ValueError):
            return None

    def _place_mode(self):
        """「新增放在」当前选的是什么：`'anchor'` / `'head'` / `'end'` / 块元组。"""
        widget = getattr(self, '_place', None)
        if widget is None:
            return 'anchor'
        data = widget.currentData()
        return 'anchor' if data is None else data

    def _chosen_block(self):
        """用户**指定**了哪个块（没指定返回 None）→ `(锚点拍号, 'head'|'end', 块头)`。"""
        mode = self._place_mode()
        if not (isinstance(mode, (tuple, list)) and len(mode) >= 3
                and str(mode[0]) == 'block'):
            return None
        try:
            beat = int(mode[2])
        except (TypeError, ValueError):
            return None
        text = str(mode[3]) if len(mode) > 3 else ''
        return beat, ('head' if str(mode[1]) == 'head' else 'end'), text

    def _anchor_for_new(self):
        """
        新增的那一条要落成什么锚点（见 `self.anchors` 的三种形态）。

        `-1` / 拍号 / `(拍号, 'head')` —— 由「新增放在」下拉与当前选中决定。
        `None` = 用户选了「块首」但**对不上哪一拍**：不许悄悄退到"本章末尾"
        （那是另一个位置），调用方会拒绝这次新增并说明原因。
        """
        chosen = self._chosen_block()
        if chosen is not None:
            beat, where, _text = chosen
            return (beat, 'head') if where == 'head' else beat
        mode = self._place_mode()
        if mode == 'end':
            return -1
        if mode == 'head':
            beat = self._head_beat()
            return (beat, 'head') if beat is not None else None
        beat = self._target_beat()
        return -1 if beat is None else beat

    def _update_target_hint(self, *_args):
        """「新增会放到哪」：跟随选中的那条与「新增放在」下拉。"""
        label = getattr(self, '_target_hint', None)
        if label is None:
            return
        chosen = self._chosen_block()
        if chosen is not None:
            beat, where, text = chosen
            side = '最前面' if where == 'head' else '最后面'
            label.setText(f'会插进第 {beat + 1} 拍的「{text}」块{side}')
            return
        i = self._selected_index()
        mode = self._place_mode()
        if mode == 'end':
            label.setText('会放到本章末尾（顶层）')
            return
        if mode == 'head':
            if i < 0:
                label.setText('先选一条：块首跟着它所在的块走')
                return
            beat = self._head_beat()
            if beat is None:
                label.setText('这一条对不上哪一拍，放不了块首 —— 换「本章末尾」')
                return
            block = str(self._blocks.get(i) or '')
            if block:
                label.setText(f'会放到第 {beat + 1} 拍的「{block}」块最前面')
            else:
                label.setText(f'会放到第 {beat + 1} 拍的最前面（顶层）')
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
        anchor = self._anchor_for_new()
        if anchor is None:
            self._hint.setText(
                '「块首」得先选一条操作（块首跟着它所在的块走）。'
                '也可以把「新增放在」换成「本章末尾」。')
            return
        src = self._selected_index()
        i = len(self.variables)
        beat = anchor[0] if isinstance(anchor, tuple) else anchor
        self.insert_beat = None if beat in (None, -1) else int(beat)
        self.variables.append(f'{name} = 0')
        self._beat_by_index[i] = self.insert_beat
        self._anchors[i] = anchor
        chosen = self._chosen_block()
        self._blocks[i] = (chosen[2] if chosen
                           else self._new_op_block(self.insert_beat, src))
        self._rebuild_ops()
        self._update_target_hint()
        self._hint.setText(f'✔ 已追加 {name} = 0（点「保存变量」生效）。')

    def _add_op(self):
        text, ok = QInputDialog.getText(
            self, '新增变量操作', '例如 love += 1 或 flag = True：')
        text = str(text or '').strip()
        if not ok or not text:
            return
        anchor = self._anchor_for_new()
        if anchor is None:
            self._hint.setText(
                '「块首」得先选一条操作（块首跟着它所在的块走）。'
                '也可以把「新增放在」换成「本章末尾」。')
            return
        src = self._selected_index()
        i = len(self.variables)
        beat = anchor[0] if isinstance(anchor, tuple) else anchor
        self.insert_beat = None if beat in (None, -1) else int(beat)
        self.variables.append(text)
        self._beat_by_index[i] = self.insert_beat
        self._anchors[i] = anchor
        chosen = self._chosen_block()
        self._blocks[i] = (chosen[2] if chosen
                           else self._new_op_block(self.insert_beat, src))
        self._rebuild_ops()
        self._ops.setCurrentRow(self._ops.count() - 1)
        self._update_target_hint()
        block = self._blocks.get(i) or ''
        placed = self._beat_by_index.get(i)
        head = (isinstance(anchor, tuple) and len(anchor) > 1
                and str(anchor[1]) == 'head')
        if head and block:
            where = f'会插进「{block}」块最前面'
        elif head:
            where = f'会放到第 {int(placed) + 1} 拍最前面（顶层）' \
                if placed is not None else '会放到本章末尾（顶层）'
        elif block:
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
#  把 python 块里的赋值搬回脚本层
# ======================================================================

class PythonLiftDialog(_BaseDialog):
    """
    「搬出 python 块」：把块里的单行赋值变成 `$` 行（2026-10-22）。

    用户 2026-10-22：

    > 真机那 26 条里 21 条是脚本层 `$` 就写得出来的赋值。它们待在 python 块里
    > 不只是"工具管不着"，而是有害的 —— 存档粒度是整块。
    > ……正确的方向不是"工具去适应 python 块"，是"工具帮用户把语句从 python
    > 块里搬出来"。

    这里只做**选择与预览**：判定（哪些行能搬、为什么不能）全在
    `story.python_lift.analyze_block`，落地在
    `session.apply_python_lift`。对话框里每一行都写清依据 ——
    "这条为什么不能搬"比"能搬的都勾上"更重要：用户得知道边界在哪儿。

    一个块一行标题（第几拍 / 第几行 / 能否搬），下面它的语句逐行列出：
    可搬的给勾选框，不可搬的置灰并写原因（含注释 / 嵌套 / 调用 / 多行）。
    """

    def __init__(self, parent=None, label_name: str = '', data=None):
        super().__init__(parent, f'搬出 python 块 · {label_name}')
        self.resize(960, 640)
        self.picks: list = []
        data = dict(data or {})
        self.label_name = str(label_name or data.get('label') or '')
        self._data = data
        #: `(QTableWidgetItem, block, candidate)`：勾选状态 → picks 的来源。
        self._rows: list = []

        stats = dict(data.get('stats') or {})
        head = QLabel(
            f'章节：{self.label_name}'
            + (f'　·　{Path(data.get("file")).name}' if data.get('file') else '')
            + f'　·　python 块 {int(stats.get("blocks") or 0)} 个，'
              f'其中 {int(stats.get("safe_lines") or 0)} 条赋值可以搬到脚本层')
        head.setProperty('role', 'section')
        head.setWordWrap(True)
        self._root.addWidget(head)

        why = QLabel(
            '搬到脚本层（`$` 行）之后，这些赋值会变成**表格里能看见、能改**的'
            '一条操作，导出到 IR / Unity 时也是一个标量事件。'
            '块里那些调用 / 控制流 / 嵌套语句原位保留 —— 工具不进去改它们。')
        why.setProperty('role', 'caption')
        why.setWordWrap(True)
        self._root.addWidget(why)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(['搬', '拍', '块', '语句 / 为什么不能搬'])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 42)
        self._table.setColumnWidth(1, 56)
        self._table.setColumnWidth(2, 150)
        for block in (data.get('blocks') or ()):
            self._add_block(block)
        self._table.itemChanged.connect(self._refresh_preview)
        self._root.addWidget(self._table, 3)

        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setFont(QFont(T.FONTS['mono'][0], T.FONTS['mono'][1]))
        self._preview.setMaximumHeight(150)
        self._preview.setPlaceholderText('勾选上面的条目，这里会写出搬完之后的样子')
        self._root.addWidget(self._preview, 1)

        row = QHBoxLayout()
        all_btn = QPushButton('全选可搬')
        all_btn.clicked.connect(lambda: self._set_all(True))
        row.addWidget(all_btn)
        none_btn = QPushButton('全不选')
        none_btn.clicked.connect(lambda: self._set_all(False))
        row.addWidget(none_btn)
        row.addStretch(1)
        self._hint = QLabel('')
        self._hint.setProperty('role', 'caption')
        row.addWidget(self._hint)
        self._root.addLayout(row)

        self._add_buttons('搬出去')
        self._set_all(True)

    # ---------------- 建表 ----------------

    def _add_block(self, block: dict):
        """一个块：标题行（第几拍 / 行区间 / 结论）+ 语句行。"""
        plan = dict(block.get('plan') or {})
        beat_no = int(block.get('beat_no') or 0)
        start, end = int(block.get('start') or 0), int(block.get('end') or 0)
        stmt_ok = int(block.get('stmt_index', -1)) >= 0
        n_safe = len(plan.get('lift') or ())
        if not stmt_ok:
            verdict = '⚠ 对不上图节点 —— 先点「🔁 刷新已导入章节」再试'
        elif plan.get('ok'):
            verdict = f'✔ 可搬 {n_safe} 条'
        else:
            verdict = f'✕ 不搬：{plan.get("why") or "没有可搬的语句"}'
        i = self._table.rowCount()
        self._table.insertRow(i)
        title = QTableWidgetItem(
            f'第 {beat_no} 拍 · python 块（第 {start}-{end} 行）· {verdict}')
        title.setFlags(Qt.ItemIsEnabled)          # 不可选 / 不可勾
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        self._table.setItem(i, 0, title)
        self._table.setSpan(i, 0, 1, 4)

        # 语句：可搬的（带勾选）在前、不搬的在后 —— 与块里的行序无关，
        # 因为"能搬哪几条"才是这个对话框要回答的事。
        lift = list(plan.get('lift') or ())
        cands = {int(c['line']): c for c in (plan.get('candidates') or ())}
        for line in lift:
            cand = dict(cands.get(int(line)) or {})
            row = self._table.rowCount()
            self._table.insertRow(row)
            box = QTableWidgetItem('')
            box.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            box.setCheckState(Qt.Checked if stmt_ok else Qt.Unchecked)
            self._table.setItem(row, 0, box)
            self._table.setItem(row, 1, QTableWidgetItem(str(beat_no)))
            kind = '自增 / 自减' if cand.get('kind') == 'augassign' else '赋值'
            self._table.setItem(row, 2, QTableWidgetItem(kind))
            text = str(cand.get('text') or '').strip()
            item = QTableWidgetItem(f'$ {text}')
            item.setData(Qt.UserRole, int(line))
            self._table.setItem(row, 3, item)
            if stmt_ok:
                self._rows.append((box, block, line))
            else:
                box.setFlags(Qt.ItemIsEnabled)
        for row_info in (plan.get('skipped') or ()):
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem('—'))
            self._table.setItem(row, 1, QTableWidgetItem(str(beat_no)))
            text = str(row_info.get('text') or '').strip()
            item = QTableWidgetItem(f'{text}　·　{row_info.get("why") or ""}')
            item.setFlags(Qt.ItemIsEnabled)
            item.setToolTip('这一行留在 python 块里 —— 搬出去会改语义')
            self._table.setItem(row, 3, item)

    # ---------------- 勾选 / 预览 ----------------

    def _set_all(self, on: bool):
        self._table.blockSignals(True)
        try:
            for box, _block, _line in self._rows:
                box.setCheckState(Qt.Checked if on else Qt.Unchecked)
        finally:
            self._table.blockSignals(False)
        self._refresh_preview()

    def _checked(self) -> list:
        return [(b, l) for box, b, l in self._rows
                if box.checkState() == Qt.Checked]

    def _refresh_preview(self, *_args):
        chosen = self._checked()
        per_block: dict = {}
        for block, line in chosen:
            per_block.setdefault(block.get('id'), []).append(int(line))
        lines = []
        for block in (self._data.get('blocks') or ()):
            picked = per_block.get(block.get('id'))
            if not picked:
                continue
            plan = dict(block.get('plan') or {})
            cands = {int(c['line']): c for c in (plan.get('candidates') or ())}
            lines.append(f'# 第 {int(block.get("beat_no") or 0)} 拍'
                         f' · python 块（第 {block.get("start")}-{block.get("end")} 行）')
            keep = list(plan.get('keep_ranges') or ())
            if keep:
                lines.append(f'python:   # 块里还剩 {sum(b - a + 1 for a, b in keep)} 行')
            for line in sorted(picked):
                text = str((cands.get(line) or {}).get('text') or '').strip()
                lines.append(f'$ {text}')
        self._preview.setPlainText('\n'.join(lines))
        self._hint.setText(f'已勾 {len(chosen)} 条' if chosen
                           else '一条都没勾 —— 那就什么都不会改')

    def _on_accept(self):
        chosen = self._checked()
        if not chosen:
            self._fail('一条都没勾。要搬哪几条就勾哪几条；不想改就点「取消」。')
            return
        per_block: dict = {}
        for block, line in chosen:
            key = (int(block['beat']), int(block['start']), int(block['end']))
            per_block.setdefault(key, []).append(int(line))
        self.picks = [{'beat': bi, 'start': start, 'end': end, 'lift': rows}
                      for (bi, start, end), rows in per_block.items()]
        self.accept()


# ======================================================================
#  加连线
# ======================================================================

EDGE_TYPES = [('jump', 'jump（跳过去，不回来）'),
              ('call', 'call（调过去，还会回来）')]


class AddEdgeDialog(_BaseDialog):
    """
    在两个章节之间加一条连线（`jump` / `call` + 条件 / 菜单项 + 插入位置）。

    版面就是旧版「添加连线」：从 / 到 / 类型 / 插入位置 / 条件 / 菜单项；
    左下角多一个**默认不勾的「草稿」勾选**。没有第二张表单、没有"落点说明"段。

    插入位置决定这条线写到剧本的哪一拍（默认章末，列表精确到第 N 拍）：
    窗口层用 `plan_fn` 算落点，能写就写进 `.rpy`（有编排层时写 `X_control`），
    写不进才画成草图（图上标「草图」）。勾了「草稿」则**只画线、不写剧本**，
    位置仍记下来，以后可以右键「写入剧本…」转正。条件 / 菜单项是分支，
    **写在编排层**（`X_control` 的 `call X` 之后），章节剧本不动。
    """

    def __init__(self, parent=None, nodes=None, from_id=None, to_id=None,
                 plan_fn=None, open_fn=None, positions_fn=None,
                 suggest_from_fn=None):
        super().__init__(parent, '添加连线')
        nodes = dict(nodes or {})
        self._nodes = nodes
        self.from_id = from_id
        self.to_id = to_id
        self.etype = 'jump'
        self.condition = ''
        self.option_text = ''
        self.from_clause = ''
        self.plan = {}
        self.position = 'chapter_end'
        self.position_line_id = ''
        self.write = False
        self.draft = False
        self.branch = False
        self.reason = ''
        self._plan_fn = plan_fn
        self._positions_fn = positions_fn
        #: `open_fn` / `suggest_from_fn` 是旧调用方还在传的参数；新窗口不用，
        #: 但保留签名，别让调用方为一个已经不显示的控件改代码。
        self._open_fn = open_fn
        self._suggest_from_fn = suggest_from_fn
        self._plan_takes_etype = self._accepts_etype(plan_fn)

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

        #: 插入位置：章末 / 第 N 拍。选项由窗口层现算；算不出时只剩章末。
        self._pos = QComboBox()
        self._pos.addItem('章末（章节文件）', ('chapter_end', ''))
        form.addRow('插入位置', self._pos)

        self._cond = QLineEdit()
        self._cond.setPlaceholderText('可选，例如 score > 3（留空 = 无条件）')
        form.addRow('条件', self._cond)

        self._opt = QLineEdit()
        self._opt.setPlaceholderText('可选：填了就当成菜单选项文字，'
                                     '例如「去屋顶看看」')
        form.addRow('菜单项', self._opt)
        self._root.addLayout(form)

        hint = QLabel('填了「条件」/「菜单项」= 分支，写在编排层 `X_control` 里'
                      '（章节剧本一个字不动）；项目还没有编排层时会先带你'
                      '接管进入点。\n'
                      '勾「草稿」= 只画线、不写剧本。')
        hint.setProperty('role', 'caption')
        hint.setWordWrap(True)
        self._root.addWidget(hint)

        #: 这一行是**按当前落点实时算出来的**"按「创建」会发生什么"。
        #: 为什么要有（2026-10-27 真机）：`plan_wire_landing` 的
        #: `reason` 以前只进状态栏 / 调用方，对话框里一个字都不显示 ——
        #: 于是"写不进剧本、只会画草图"和"会顺手补一个包装"这两种很不一样
        #: 的结果，在按按钮之前长得一模一样。
        self._plan_hint = QLabel('')
        self._plan_hint.setProperty('role', 'caption')
        self._plan_hint.setWordWrap(True)
        self._plan_hint.setVisible(False)
        self._root.addWidget(self._plan_hint)

        # 左下角的「草稿」：默认不勾。勾上就不写剧本，只画一条虚线草图
        # （位置仍记在 `position/position_line_id`，以后可以「写入剧本…」转正）。
        draft_row = QHBoxLayout()
        draft_row.setContentsMargins(0, 0, 0, 0)
        self._draft = QCheckBox('草稿（只画线，不写剧本）')
        self._draft.setToolTip(
            '勾上就先画成虚线草稿（图上标「草图」），剧本一个字不改；'
            '以后右键这条线「写入剧本…」可以转正。'
            '不勾 = 按插入位置直接写进剧本。')
        self._draft.setChecked(False)
        draft_row.addWidget(self._draft)
        draft_row.addStretch(1)
        self._root.addLayout(draft_row)

        self._add_buttons('创建')
        self.setMinimumWidth(520)

        self._src.currentIndexChanged.connect(self._sync_positions)
        self._dst.currentIndexChanged.connect(self._sync_position)
        self._type.currentIndexChanged.connect(self._sync_position)
        self._pos.currentIndexChanged.connect(self._sync_position)
        self._sync_positions()

    # ---------------- 落点 ----------------

    @staticmethod
    def _accepts_etype(fn) -> bool:
        """`plan_fn` 收不收 `etype`（可选第 5 个参数）。"""
        if not callable(fn):
            return False
        try:
            import inspect
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return False
        if 'etype' in params:
            return True
        return any(p.kind == inspect.Parameter.VAR_POSITIONAL
                   for p in params.values())

    def _call_plan(self, from_id, to_id):
        if not callable(self._plan_fn):
            return {}
        args = [from_id, to_id, self.position, self.position_line_id]
        if self._plan_takes_etype:
            args.append(str(self._type.currentData() or 'jump'))
        return self._plan_fn(*args) or {}

    def _sync_positions(self, *_args):
        """
        起点章变了 → 重建「插入位置」下拉框。

        只列窗口层说能接的位置；算不出（章没打开 / 没有顶层拍）就只剩
        「章末」，并置灰，不凭章名或拍数去猜一个。
        """
        from_id = self._src.currentData()
        data = {}
        if callable(self._positions_fn) and from_id is not None:
            try:
                data = self._positions_fn(from_id) or {}
            except Exception as e:            # noqa: BLE001 - 下拉框坏不该拦住接线
                swallow('ui_qt.dialogs.AddEdgeDialog.positions', e)
                data = {}
        items = (list(data.get('items') or ())
                 if isinstance(data, dict) else [])
        control = bool(data.get('control')) if isinstance(data, dict) else False

        self._pos.blockSignals(True)
        self._pos.clear()
        self._pos.addItem('章末（编排层）' if control else '章末（章节文件）',
                          ('chapter_end', ''))
        for it in items:
            if not isinstance(it, dict) or not it.get('line_id'):
                continue
            number = it.get('number')
            if not isinstance(number, int):
                try:
                    number = int(it.get('index') or 0) + 1
                except (TypeError, ValueError):
                    number = 0
            text = str(it.get('text') or '')
            label = f'第 {number} 拍 · {text}' if text else f'第 {number} 拍'
            note = str(it.get('note') or '')
            if not it.get('selectable'):
                label += f'（{note or "不能接在这里"}）'
            self._pos.addItem(label, ('beat', str(it['line_id'])))
            if not it.get('selectable'):
                row = self._pos.count() - 1
                try:
                    item = self._pos.model().item(row)
                    if item is not None:
                        item.setEnabled(False)
                except Exception as e:        # noqa: BLE001 - 置灰失败仍可显示
                    swallow('ui_qt.dialogs.AddEdgeDialog.gray_position', e)
        self._pos.setCurrentIndex(0)
        self._pos.blockSignals(False)
        self._pos.setEnabled(True)
        self._pos.setToolTip(str(data.get('reason') or '')
                             if isinstance(data, dict) else '')
        self.position, self.position_line_id = 'chapter_end', ''
        self._sync_position()

    def _sync_position(self, *_args):
        """下拉框一改，位置和计划立刻跟着变（不必等按「创建」）。"""
        self.position, self.position_line_id = self._current_position()
        from_id, to_id = self._src.currentData(), self._dst.currentData()
        if from_id is None or to_id is None:
            self.plan = {}
            self._show_plan_hint()
            return
        try:
            self.plan = self._call_plan(from_id, to_id)
        except Exception as e:                # noqa: BLE001 - 算不出也要能画草图
            swallow('ui_qt.dialogs.AddEdgeDialog.sync_position', e)
            self.plan = {}
        self._show_plan_hint()

    def _label_of(self, combo) -> str:
        """下拉框里那一项对应的 label 名（读不到就退回列表文字）。"""
        node = self._nodes.get(combo.currentData())
        name = str(getattr(node, 'label_name', '') or '')
        if name:
            return name
        text = str(combo.currentText() or '')
        return text.split(':', 1)[-1].strip() if ':' in text else text

    def plan_hint_text(self) -> str:
        """
        对话框里那句**"按「创建」会发生什么"**（空 = 说不清就不说）。

        判据与窗口层同源：能不能写看 `wire_plan_writable`，写到哪儿看 kind。
        """
        from storygraph.story.control_route import wire_plan_writable
        plan = self.plan if isinstance(self.plan, dict) else {}
        if not plan:
            return ''
        kind = str(plan.get('kind') or '')
        why = str(plan.get('reason') or '').strip()
        src, dst = self._label_of(self._src), self._label_of(self._dst)
        if kind == 'control':
            return (f'✔ 会写进编排层：把「{src}」的出口接到「{dst}」'
                    f'（章节剧本一个字不动）。')
        if kind == 'control_init':
            return (f'✔ 会写进编排层：先补「{dst}_control」包装，'
                    f'再把「{src}」接过去（章节剧本一个字不动）。')
        if kind in ('chapter_end', 'beat'):
            where = '章末' if kind == 'chapter_end' else '选好的那一拍'
            return (f'✔ 会写进章节文件「{src}」的{where}'
                    f'（内存改动，「⤓ 导出」才落盘）。')
        if not wire_plan_writable(plan):
            tail = f'：{why}' if why else '。'
            return (f'✎ 现在写不进剧本 —— 按「创建」只会画一条**草图**'
                    f'（虚线，图上标「草图」）{tail}')
        return ''

    def _show_plan_hint(self):
        text = ''
        try:
            text = self.plan_hint_text()
        except Exception as e:                # noqa: BLE001 - 提示算不出不该拦住接线
            swallow('ui_qt.dialogs.AddEdgeDialog.plan_hint', e)
            text = ''
        if not hasattr(self, '_plan_hint'):
            return
        self._plan_hint.setText(text)
        self._plan_hint.setVisible(bool(text))

    def _current_position(self):
        data = self._pos.currentData()
        if isinstance(data, (tuple, list)) and len(data) == 2:
            if str(data[0]) == 'beat' and data[1]:
                return 'beat', str(data[1])
        return 'chapter_end', ''

    # ---------------- 确定 ----------------

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
        self.draft = bool(self._draft.isChecked())
        self.position, self.position_line_id = self._current_position()
        self.from_clause = ''

        try:
            self.plan = self._call_plan(self.from_id, self.to_id)
        except Exception as e:                # noqa: BLE001 - 算不出也要能画草图
            swallow('ui_qt.dialogs.AddEdgeDialog.plan', e)
            self.plan = {}
        # 判据与窗口层**同一份**（`wire_plan_writable`）：`control_init`
        # （目标章缺 `_control` 包装）的 `ok` 是 False，但它是能写的 ——
        # 这里只写 `ok` 的话「创建」会按不下去；反过来窗口只看 `ok` 的话，
        # 按下去以后什么都不发生（2026-10-27 真机"连线不显示"）。
        from storygraph.story.control_route import wire_plan_writable
        writable = wire_plan_writable(self.plan)
        branch = bool(self.condition or self.option_text)
        self.branch = branch
        self.write = bool(writable)
        self.reason = str(self.plan.get('reason') or '')

        # 条件 / 菜单项是**章末**分支：写在 `X_control` 的 `call X` 之后。
        # 指定"第 N 拍"时没有编排层落点（往块体里插会破坏缩进与语义），
        # 当场让人改回章末，不静默改成别的意思。
        if branch and self.position == 'beat':
            self.write = False
            self._fail('条件 / 菜单项是章末分支，只能写在编排层；'
                       '把「插入位置」改回「章末」，或清空条件 / 菜单项。')
            return

        # 勾了「草稿」：这一条只画线，**不写剧本**；位置照样记下来，
        # 以后可以右键「写入剧本…」转正。因此这里不检查"这一拍写不写得进"。
        if self.draft:
            self.write = False
            self.reason = '勾了「草稿」：只画线，不写剧本'
            self.accept()
            return

        # 明确选了"某一拍"却写不进去：不许悄悄退化成草图 —— 草图的落点
        # 就是那一拍，那一拍写不进，草图也就永远转不了正。当场让人换位置。
        if self.position == 'beat' and not writable and not branch:
            self._fail('这个拍位置现在写不进：'
                       + (self.reason or '找不到可插的落点')
                       + '。换一个位置，或把「插入位置」改回章末。')
            return
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
        except Exception as e:                # noqa: BLE001
            swallow('ui_qt.dialogs._condition_ok', e)
            return True


class EditEdgeDialog(_BaseDialog):
    """
    一条**已有连线**的属性（旧版「编辑连线」的 Qt 版）。

    版面与「添加连线」同一张表单：从 / 到 / 类型 / 插入位置 / 条件 / 菜单项。

    保存**不是"只改图"**（用户 2026-10-24 抓到的第二个问题）：
      · 源边 → 改类型 / 到，直接改写剧本里的那一行（`jump B` ↔ `call B`）；
        改「插入位置」= 把那一行挪到本章另一拍之后（导出才落盘）；
      · 编排线 → 改类型 / 到 / **条件 / 菜单项**，直接改写 `X_control` 里
        对应的标记段（普通路由会被换成条件分支）；
      · 草图 → 只改图上的"打算"（它本来就不在剧本里）。

    源边的条件 / 菜单项仍然只读：那两样挂在它所在的 `if` / `menu` 上，
    不是这一行自己能改的字段（窗口里写明原因，不给假输入框）。
    """

    def __init__(self, parent=None, edge=None, from_label='', to_label='',
                 position_text='', positions=None, open_fn=None,
                 targets=None, target_id=None):
        super().__init__(parent, '编辑连线')
        self.edge = edge
        self.origin = str(getattr(edge, 'origin', 'source') or 'source')
        self.etype = str(getattr(edge, 'type', 'jump') or 'jump')
        self.condition = str(getattr(edge, 'condition', '') or '')
        self.option_text = str(getattr(edge, 'option_text', '') or '')
        self.from_clause = str(getattr(edge, 'from_clause', '') or '')
        self.changed = False
        self._control = self.origin == 'control'
        self._manual = self.origin == 'manual'
        self._source = not self._control and not self._manual
        self.position = str(getattr(edge, 'position', 'chapter_end')
                            or 'chapter_end')
        self.position_line_id = (str(getattr(edge, 'position_line_id', '') or '')
                                 if self.position == 'beat' else '')
        if self._source:
            # 源边的"位置"就是它那一拍的行身份；窗口里可以把它挪到
            # 本章另一个顶层拍之后（移动会改写剧本，和卡片编辑一样导出落盘）。
            lid = str(getattr(edge, 'line_id', '') or '')
            self.position = 'beat' if lid else 'chapter_end'
            self.position_line_id = lid
        self._pos_editable = False
        self.to_id = (target_id if target_id is not None
                      else getattr(edge, 'to_id', None))

        form = QFormLayout()
        form.setSpacing(T.SPACE['sm'])

        self._src = QComboBox()
        self._src.addItem(str(from_label or '?'), 'from')
        self._src.setEnabled(False)
        form.addRow('从', self._src)

        #: 到：源边 / 编排线可以改（会改写文件里那一行）；草图两端只读。
        self._dst = QComboBox()
        pairs = []
        for item in (targets or ()):
            try:
                nid, name = item
            except (TypeError, ValueError):
                continue
            pairs.append((nid, str(name or '')))
        if self._control or self._source:
            rows = list(pairs)
            if self.to_id is not None \
                    and self.to_id not in [nid for nid, _n in rows]:
                rows.insert(0, (self.to_id, str(to_label or '?')))
            if not rows:
                rows = [(self.to_id, str(to_label or '?'))]
            for nid, name in rows:
                self._dst.addItem(f'{nid}: {name}', nid)
            i = self._dst.findData(self.to_id)
            if i >= 0:
                self._dst.setCurrentIndex(i)
            self._dst.setEnabled(len(rows) > 1)
        else:
            self._dst.addItem(str(to_label or '?'), 'to')
            self._dst.setEnabled(False)
        form.addRow('到', self._dst)

        self._type = QComboBox()
        for value, label in EDGE_TYPES:
            self._type.addItem(label, value)
        i = self._type.findData(self.etype)
        if i >= 0:
            self._type.setCurrentIndex(i)
        form.addRow('类型', self._type)

        self._pos = QComboBox()
        self._fill_positions(positions, position_text)
        self._pos.setEnabled(self._manual or self._pos_editable)
        form.addRow('插入位置', self._pos)

        self._cond = QLineEdit(self.condition)
        self._cond.setPlaceholderText('可选，例如 score > 3（留空 = 无条件）')
        self._opt = QLineEdit(self.option_text)
        self._opt.setPlaceholderText('可选：填了就当成菜单选项文字，'
                                     '例如「去屋顶看看」')
        # 条件 / 菜单项：编排线可以真的改写（按标记段换 / 换掉原来那一行
        # 普通路由）；草图只改图上的"打算"；源边的那两样挂在 if / menu 上，
        # 不是这一行能改的，明确写原因。
        editable_branch = self._manual or self._control
        self._cond.setEnabled(editable_branch)
        self._opt.setEnabled(editable_branch)
        form.addRow('条件', self._cond)
        form.addRow('菜单项', self._opt)
        self._root.addLayout(form)

        if self._manual:
            hint = QLabel('填了「菜单项」= 这一拍是个分支（menu）；'
                          '只填「条件」= 条件跳转；都不填 = 直接 jump/call。')
        elif self._control:
            hint = QLabel(
                '这条线是编排层里的一段：改「类型 / 到 / 条件 / 菜单项」会'
                '直接改写 `X_control` 里的对应段（jump 去 B_control，'
                'call 去 B）；章节剧本一个字不动。\n'
                '清空条件 / 菜单项请删掉这条线再用普通接线重连。')
        else:
            hint = QLabel(
                '这条线是剧本里的一行：改「类型 / 到」会直接改写那一行；'
                '「插入位置」可以把它挪到本章另一个顶层拍之后（导出才落盘）。\n'
                '条件 / 菜单项挂在它所在的 if / menu 上，不是这一行能改的 —— '
                '要改请双击那一拍进卡片。')
        hint.setProperty('role', 'caption')
        hint.setWordWrap(True)
        self._root.addWidget(hint)

        self._open_btn = QPushButton('打开编排层文件')
        self._open_fn = open_fn
        self._open_btn.setVisible(bool(open_fn) and self._control)
        self._open_btn.clicked.connect(self._open_control)
        self._root.addWidget(self._open_btn)

        self._add_buttons('保存')
        self.setMinimumWidth(520)

    # ---------------- 插入位置 ----------------

    def _fill_positions(self, positions, position_text):
        if self._control:
            self._pos.addItem('章末（编排层）', ('chapter_end', ''))
            return
        if self._source:
            # 源边：列出本章顶层拍，选中当前那一拍；能改成"接到另一拍之后"。
            items = (positions.get('items') or ()
                     if isinstance(positions, dict) else ())
            if not items:
                text = str(position_text or '').strip() or '位置未载入'
                self._pos.addItem(text, ('current', ''))
                return
            for it in items:
                if not isinstance(it, dict) or not it.get('line_id'):
                    continue
                number = it.get('number')
                if not isinstance(number, int):
                    try:
                        number = int(it.get('index') or 0) + 1
                    except (TypeError, ValueError):
                        number = 0
                text = str(it.get('text') or '')
                label = f'第 {number} 拍 · {text}' if text else f'第 {number} 拍'
                self._pos.addItem(label, ('beat', str(it['line_id'])))
                if not it.get('selectable'):
                    row = self._pos.count() - 1
                    try:
                        item = self._pos.model().item(row)
                        if item is not None:
                            item.setEnabled(False)
                    except Exception as e:    # noqa: BLE001 - 置灰失败仍可显示
                        swallow('ui_qt.dialogs.EditEdgeDialog.gray_source',
                                e)
            self._pos_editable = True
            self._select_position()
            return
        self._pos.addItem('章末（章节文件）', ('chapter_end', ''))
        items = (positions.get('items') or ()
                 if isinstance(positions, dict) else ())
        for it in items:
            if not isinstance(it, dict) or not it.get('line_id'):
                continue
            number = it.get('number')
            if not isinstance(number, int):
                try:
                    number = int(it.get('index') or 0) + 1
                except (TypeError, ValueError):
                    number = 0
            text = str(it.get('text') or '')
            label = f'第 {number} 拍 · {text}' if text else f'第 {number} 拍'
            if not it.get('selectable'):
                label += f'（{it.get("note") or "不能接在这里"}）'
            self._pos.addItem(label, ('beat', str(it['line_id'])))
            if not it.get('selectable'):
                row = self._pos.count() - 1
                try:
                    item = self._pos.model().item(row)
                    if item is not None:
                        item.setEnabled(False)
                except Exception as e:        # noqa: BLE001 - 置灰失败仍可显示
                    swallow('ui_qt.dialogs.EditEdgeDialog.gray_position', e)
        self._select_position()

    def _select_position(self):
        """选中当前落点；当前拍不在列表里就补一条只读项，不改章末。"""
        want = ('beat', self.position_line_id) \
            if self.position == 'beat' else ('chapter_end', '')
        for row in range(self._pos.count()):
            data = self._pos.itemData(row)
            if isinstance(data, (tuple, list)) and tuple(data) == want:
                self._pos.setCurrentIndex(row)
                return
        if self.position == 'beat' and self.position_line_id:
            self._pos.addItem('指定拍（未载入，保留原值）', want)
            self._pos.setCurrentIndex(self._pos.count() - 1)

    def _current_position(self):
        if not (self._manual or (self._source and self._pos_editable)):
            return self.position, self.position_line_id
        data = self._pos.currentData()
        if isinstance(data, (tuple, list)) and len(data) == 2:
            if str(data[0]) == 'beat' and data[1]:
                return 'beat', str(data[1])
        return 'chapter_end', ''

    # ---------------- 保存 ----------------

    def _open_control(self):
        if callable(self._open_fn):
            try:
                self._open_fn()
            except Exception as e:            # noqa: BLE001 - 打不开不该崩
                swallow('ui_qt.dialogs.EditEdgeDialog.open_control', e)
        self.accept()

    def _on_accept(self):
        etype = str(self._type.currentData() or 'jump')
        editable_branch = self._manual or self._control
        cond = (self._cond.text().strip() if editable_branch
                else self.condition)
        if editable_branch and cond and not AddEdgeDialog._condition_ok(cond):
            self._fail(f'条件「{cond}」看起来永远不成立，'
                       f'检查一下变量名和比较符。')
            return
        new_opt = (self._opt.text().strip() if editable_branch
                   else self.option_text)
        new_from = self.from_clause if etype == 'call' else ''
        new_pos, new_lid = self._current_position()
        new_to = (self._dst.currentData()
                  if (self._control or self._source) else self.to_id)
        old_type = str(getattr(self.edge, 'type', 'jump') or 'jump')
        old_to = getattr(self.edge, 'to_id', None)
        self.changed = (etype != old_type
                        or str(new_to) != str(old_to)
                        or cond != self.condition
                        or new_opt != self.option_text
                        or new_from != self.from_clause
                        or new_pos != self.position
                        or new_lid != self.position_line_id)
        self.etype = etype
        self.condition = cond
        self.option_text = new_opt
        self.from_clause = new_from
        self.position = new_pos
        self.position_line_id = new_lid
        self.to_id = new_to
        self.accept()

    @staticmethod
    def _condition_ok(cond: str) -> bool:
        return AddEdgeDialog._condition_ok(cond)


class TakeoverDialog(_BaseDialog):
    """
    「接管进入点」的**多文件 diff 预览**（接管前必须看见会改什么）。

    只读文本，不在这里改路径 / 内容：要改就去改源文件，再来一次。
    确定 = 执行接管（命令层负责原子写 + 撤销）。
    """

    def __init__(self, parent=None, plan=None):
        super().__init__(parent, '接管进入点 · 编排层')
        self.plan = plan
        reason = str(getattr(plan, 'reason', '') or '')
        summary = QLabel(reason or '没有可接管的内容。')
        summary.setWordWrap(True)
        self._root.addWidget(summary)

        path = str(getattr(plan, 'control_path', '') or '')
        where = QLabel(f'编排层：{path}' if path else '还没有编排层文件')
        where.setProperty('role', 'caption')
        where.setWordWrap(True)
        self._root.addWidget(where)

        notes = list(getattr(plan, 'notes', None) or ())
        if notes:
            note_label = QLabel('· ' + '\n· '.join(str(x) for x in notes))
            note_label.setProperty('role', 'caption')
            note_label.setWordWrap(True)
            self._root.addWidget(note_label)

        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText(str(getattr(plan, 'diff', '') or
                             '（这个计划没有文件改动）'))
        box.setFont(QFont(T.FONTS['mono'][0], T.FONTS['mono'][1]))
        self._root.addWidget(box, 1)
        self._add_buttons('接管')
        self.resize(880, 560)


class EdgePositionDialog(_BaseDialog):
    """
    改一条**草图**的落点（P2）：章末 / 第 N 拍。

    只改图上的"打算写在哪"，不碰剧本 —— 要真写进去仍走「写入剧本…」。
    位置列表由窗口层现算（`edge_position_choices`），不能接的拍照样列出来
    但置灰；这一份判据与接线窗口、写回共用。
    """

    def __init__(self, parent=None, positions=None, current_position='chapter_end',
                 current_line_id=''):
        super().__init__(parent, '改草图落点')
        data = positions if isinstance(positions, dict) else {}
        self.position = str(current_position or 'chapter_end')
        self.position_line_id = str(current_line_id or '') \
            if self.position == 'beat' else ''

        form = QFormLayout()
        form.setSpacing(T.SPACE['sm'])
        self._pos = QComboBox()
        self._pos.addItem('章末', ('chapter_end', ''))
        items = list(data.get('items') or ())
        for it in items:
            if not isinstance(it, dict) or not it.get('line_id'):
                continue
            number = it.get('number')
            if not isinstance(number, int):
                try:
                    number = int(it.get('index') or 0) + 1
                except (TypeError, ValueError):
                    number = 0
            text = str(it.get('text') or '')
            label = f'第 {number} 拍 · {text}' if text else f'第 {number} 拍'
            note = str(it.get('note') or '')
            if not it.get('selectable'):
                label += f'（{note or "不能接在这里"}）'
            elif '之前' in note:
                label += '（插在它前面）'
            self._pos.addItem(label, ('beat', str(it['line_id'])))
            if not it.get('selectable'):
                try:
                    item = self._pos.model().item(self._pos.count() - 1)
                    if item is not None:
                        item.setEnabled(False)
                except Exception as e:            # noqa: BLE001 - 置灰失败仍可显示
                    swallow('ui_qt.dialogs.EdgePositionDialog.gray', e)
        # 选中当前落点；当前那一拍不在列表里（未载入 / 已删）时保留章末，
        # 并在下面说明，不偷偷改成一个别的拍。
        want = ('beat', self.position_line_id) \
            if self.position == 'beat' and self.position_line_id else \
            ('chapter_end', '')
        idx = -1
        for i in range(self._pos.count()):
            data = self._pos.itemData(i)
            if not (isinstance(data, (tuple, list)) and len(data) == 2
                    and (str(data[0]), str(data[1])) == want):
                continue
            item = self._pos.model().item(i)
            if item is not None and not item.isEnabled():
                continue        # 当前那一拍现在已经不能接了 → 按"丢了"处理
            idx = i
            break
        if idx >= 0:
            self._pos.setCurrentIndex(idx)
        self._lost_current = (self.position == 'beat' and idx < 0)

        form.addRow('落点', self._pos)
        self._root.addLayout(form)

        self._note = QLabel('')
        self._note.setWordWrap(True)
        self._note.setProperty('role', 'caption')
        if self._lost_current:
            self._note.setText(
                '这条线原来记的那一拍现在不在本章（没载入或已删除）；'
                '下拉框里只剩能选的位置。')
        elif not items and str(data.get('reason') or ''):
            self._note.setText(str(data.get('reason')))
        self._root.addWidget(self._note)
        hint = QLabel('只改这条草图"打算写在哪"；剧本一个字节都不动。'
                      '要写进去，右键这条线 →「写入剧本…」。')
        hint.setWordWrap(True)
        hint.setProperty('role', 'caption')
        self._root.addWidget(hint)
        self._add_buttons('改落点')
        self._pos.currentIndexChanged.connect(self._sync)

    def _sync(self, *_args):
        data = self._pos.currentData()
        if isinstance(data, (tuple, list)) and len(data) == 2:
            self.position, self.position_line_id = str(data[0]), str(data[1])
        else:
            self.position, self.position_line_id = 'chapter_end', ''
        if self.position != 'beat':
            self.position_line_id = ''

    def _on_accept(self):
        self._sync()
        if self.position == 'beat' and not self.position_line_id:
            self._fail('选了「某一拍」，但没说是哪一拍。')
            return
        self.accept()


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
           'VariableWorkbenchDialog', 'AddEdgeDialog', 'EditEdgeDialog',
           'EdgePositionDialog', 'AnalysisDialog',
           'SyncPreviewDialog', 'confirm', 'EDGE_TYPES']
