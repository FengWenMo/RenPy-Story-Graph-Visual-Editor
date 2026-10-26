"""
「🧹 清理导入残留」弹窗（`ui_qt`）
=================================

用户 2026-10-26 的原话：

> 我一个个删 vscode 里面的 .bak 和 blocked 里面的文件老狼狈了……

纯逻辑（扫什么、什么算残留、怎么安全删）全在
`storygraph.authoring.import_cleanup`（那里有 20 多条判据）。本文件只做三件事：

  1. **把清单摆出来**：按四类分组、每类多少 / 占多大；逐条路径也能看；
  2. **让人自己勾**：默认勾"确定可以丢"的三类（时间戳备份 / 对照副本 /
     历史遗留），`.rpy.bak` **默认不勾** —— 它可能是覆盖手写文件前留下的
     **唯一**旧版本（`write_text_atomic` 里"第一份备份最值钱"那段），
     默认帮人删掉它是会出事的；
  3. **删完如实报**：删了几个、哪个没删掉、为什么（失败绝不静默）。

为什么不用 `QMessageBox.question` 一句话确认：一屏"要删 37 个文件吗？"
既看不见是哪些文件，也没法只删其中一类 —— 用户要的正是"别再一个个删"，
那这个入口就必须比在 vscode 里删更省事，而不是同样盲。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout,
                               QLabel, QPlainTextEdit, QVBoxLayout)

from storygraph.authoring import import_cleanup as IC
from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme

#: 预览里最多列多少条（再多也没人看，路径会写进日志）
MAX_PREVIEW = 200

#: 默认**勾上**的类：都是"确定可以丢"的（时间戳备份本来只留 5 代、
#: 对照副本是门禁拦下时的临时对照、历史遗留现在已不再产生）。
#: `KIND_BAK` 刻意不在这里：它可能是用户手写文件的**唯一**旧版本。
DEFAULT_CHECKED = (IC.KIND_BAK_STAMPED, IC.KIND_BLOCKED, IC.KIND_BAK_RPY)


class ImportJunkDialog(QDialog):
    """
    导入残留清单 + 删除确认。

    用法（`window_graph_ops.cleanup_import_junk`）：

        dlg = ImportJunkDialog(self, project_root)
        if dlg.exec() == QDialog.Accepted:
            ... 读 dlg.deleted / dlg.failed 记账 ...

    构造时就扫一遍（`files` 给了就用给的，方便测试/复用）。
    """

    def __init__(self, parent=None, project_root=None, files=None):
        super().__init__(parent)
        self.setWindowTitle('清理导入残留')
        theme.apply_to(self)
        self.resize(880, 620)
        self.project_root = Path(project_root) if project_root else None
        self.files = list(files) if files is not None \
            else IC.scan_import_junk(project_root)
        self.summary = IC.summarize(self.files)
        self.deleted: list = []
        self.failed: list = []
        self._boxes = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])

        head = QLabel(
            f'找到 {self.summary["total"]} 个导入残留'
            f'（共 {self.summary["human_bytes"]}）—— 它们都不参与游戏运行'
            f'（`.blocked.rpy` 在 `build/review/` 里，Ren\'Py 不扫描；'
            f'`.rpy.bak` 也不是 `.rpy` 脚本），删掉不影响工程。\n'
            f'每类单独勾选；删除不可恢复，先看清楚再点。')
        head.setObjectName('junkHead')
        head.setWordWrap(True)
        root.addWidget(head)

        for kind in IC.ALL_KINDS:
            items = self.summary['by_kind'].get(kind) or []
            box = QCheckBox(self._kind_text(kind, items))
            box.setObjectName(f'junkKind_{kind}')
            box.setEnabled(bool(items))
            box.setChecked(bool(items) and kind in DEFAULT_CHECKED)
            box.toggled.connect(self._refresh)
            self._boxes[kind] = box
            root.addWidget(box)

        if self.summary['counts'].get(IC.KIND_BAK):
            warn = QLabel('⚠ 「自动备份（.rpy.bak）」可能是覆盖手写文件前留下的'
                          '唯一旧版本 —— 确认不再需要回滚再勾它。')
            warn.setObjectName('junkBakWarning')
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {T.t('state.warning')};")
            root.addWidget(warn)

        self.preview = QPlainTextEdit()
        self.preview.setObjectName('junkPreview')
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont(T.FONTS['mono'][0], T.FONTS['mono'][1]))
        root.addWidget(self.preview, 1)

        self._error = QLabel('')
        self._error.setObjectName('junkError')
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color: {T.t('state.error')};")
        self._error.setVisible(False)
        root.addWidget(self._error)

        row = QHBoxLayout()
        self._summary = QLabel('')
        self._summary.setObjectName('junkSummary')
        row.addWidget(self._summary)
        row.addStretch(1)
        root.addLayout(row)

        self._buttons = QDialogButtonBox()
        self._ok = self._buttons.addButton('删除选中的', QDialogButtonBox.AcceptRole)
        self._ok.setProperty('variant', 'primary')
        self._ok.setObjectName('junkDelete')
        self._cancel = self._buttons.addButton('取消', QDialogButtonBox.RejectRole)
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        self._refresh()

    # ------------------------------------------------------------------
    #  勾选 / 预览
    # ------------------------------------------------------------------

    def _kind_text(self, kind: str, items) -> str:
        size = sum(int(getattr(i, 'size', 0) or 0) for i in items)
        return (f'{IC.KIND_LABELS.get(kind, kind)}：{len(items)} 个'
                f' · {IC.human_size(size)}')

    def selected_files(self) -> list:
        """当前勾选出来的残留（按 kind 固定顺序、每条按路径排序）。"""
        out = []
        for kind in IC.ALL_KINDS:
            box = self._boxes[kind]
            if box.isChecked():
                out.extend(self.summary['by_kind'].get(kind) or ())
        out.sort(key=lambda it: str(it.path))
        return out

    def _refresh(self, *_a):
        items = self.selected_files()
        self._preview_text(items)
        self._summary.setText(
            f'选中的 {len(items)} 个'
            f'（共 {IC.human_size(sum(int(getattr(i, "size", 0) or 0) for i in items))}）')
        self._ok.setText(f'删除选中的 {len(items)} 个' if items else '删除选中的')
        self._ok.setEnabled(bool(items))
        self._ok.setProperty('variant', 'primary')

    def _preview_text(self, items):
        if not items:
            self.preview.setPlainText('（没有勾选任何一类）')
            return
        lines = []
        for item in items[:MAX_PREVIEW]:
            rel = self._relative(item.path)
            lines.append(f'[{IC.KIND_LABELS.get(item.kind, item.kind)}] {rel}')
        if len(items) > MAX_PREVIEW:
            lines.append(f'……还有 {len(items) - MAX_PREVIEW} 个（日志里会全列）')
        self.preview.setPlainText('\n'.join(lines))

    def _relative(self, path) -> str:
        try:
            if self.project_root is not None:
                return str(Path(path).relative_to(self.project_root))
        except (ValueError, OSError) as e:      # noqa: BLE001 - 只影响显示
            swallow('ui_qt.dialog_cleanup._relative', e)
        return str(path)

    # ------------------------------------------------------------------
    #  删除
    # ------------------------------------------------------------------

    def _on_accept(self):
        items = self.selected_files()
        if not items:
            self._show_error('什么都没勾选 —— 至少勾一类，或者点「取消」。')
            return
        deleted, failed = IC.delete_import_junk(items)
        self.deleted = list(deleted)
        self.failed = list(failed)
        if not deleted and failed:
            # 一个都没删掉：别关窗（关了就只剩一行日志，用户会以为删成功了）
            self._show_error(
                f'{len(failed)} 个都没删掉（可能被占用/权限不够）：'
                + '；'.join(f'{Path(p).name}：{why}' for p, why in failed[:3]))
            return
        self.accept()

    def _show_error(self, message: str):
        self._error.setText(message)
        self._error.setVisible(True)


__all__ = ['DEFAULT_CHECKED', 'ImportJunkDialog', 'MAX_PREVIEW']
