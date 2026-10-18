"""
storygraph.ui_qt.dialog_asset_rename
====================================
「资源改名」—— 把资源表里的新名字**传播到所有引用处**（E2，用户 2026-10-19）。

用户原话：

> 真正缺的是"资源表改名之后，Beats 里的旧值怎么办"。你已经有
> `asset_extensions._apply_rename_patch`（plan → apply(confirm=) → rollback），
> 但它按 D3' 没接 Qt。E2：把 rename patch 接进 UI（2 天）。

所以这个对话框只做三件事，顺序不能反：

  ① **预览**（`plan`，纯函数）：哪些文件、哪几行会变 —— 一个字节都不写；
  ② **应用**（`apply(confirm=True)`）：写前留 `.bak`；
  ③ **回滚**（`rollback`）：按 `.bak` 逐位还原。

判据和口径：**词边界**匹配（`hero` 不会改到 `hero_happy`），
写盘走原子写、保留换行（`.rpy` 落盘的统一要求）。
"""
from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from storygraph.ui_qt import tokens as T

__all__ = ['AssetRenameDialog']

#: 资产种类 → 资源表字段（与 `session_assets.ASSET_KINDS` 同源）
_KINDS = (('image', 'images', '图片'), ('music', 'music', '音乐'),
          ('sound', 'sounds', '音效'), ('voice', 'voices', '语音'),
          ('transform', 'transforms', '变换'))


class AssetRenameDialog(QDialog):
    """预览 → 应用 → 回滚（三步都看得见）。"""

    def __init__(self, parent=None, session=None, registry=None):
        super().__init__(parent)
        self._session = session
        self._registry = registry
        self._plan = None
        self._applied = None
        self.setWindowTitle('资源改名（传播到脚本）')
        self.resize(820, 620)
        self._build()
        self._load_ids()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['md'], T.SPACE['md'],
                                T.SPACE['md'], T.SPACE['md'])
        root.setSpacing(T.SPACE['sm'])
        tip = QLabel('资源表里改完名字之后，脚本里还写着旧名字 —— 游戏里就会'
                     '"找不到图/找不到音乐"。\n'
                     '这里把旧名字**按词边界**换成新名字：先看预览，确认后再写；'
                     '写前自动备份，随时可以回滚。')
        tip.setWordWrap(True)
        tip.setProperty('role', 'note')
        root.addWidget(tip)

        form = QFormLayout()
        form.setSpacing(T.SPACE['sm'])
        self._kind = QComboBox()
        for _, _, label in _KINDS:
            self._kind.addItem(label)
        self._kind.currentIndexChanged.connect(lambda *_: self._load_ids())
        form.addRow('资产种类', self._kind)
        self._old = QComboBox()
        self._old.setEditable(True)
        self._old.setInsertPolicy(QComboBox.NoInsert)
        form.addRow('旧名（表里现在的 ID）', self._old)
        self._new = QLineEdit()
        self._new.setPlaceholderText('例：ling_happy → ling_smile')
        form.addRow('新名', self._new)
        root.addLayout(form)

        acts = QHBoxLayout()
        self._preview_btn = QPushButton('① 预览影响')
        self._preview_btn.setToolTip('只算不写：列出哪些文件、哪几行会变。')
        self._preview_btn.clicked.connect(self.preview)
        acts.addWidget(self._preview_btn)
        self._apply_btn = QPushButton('② 应用（写盘，先备份）')
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self.apply)
        acts.addWidget(self._apply_btn)
        self._undo_btn = QPushButton('③ 回滚')
        self._undo_btn.setEnabled(False)
        self._undo_btn.setToolTip('按写前的 .bak 逐位还原（确定性回滚，不做猜测）')
        self._undo_btn.clicked.connect(self.rollback)
        acts.addWidget(self._undo_btn)
        acts.addStretch(1)
        root.addLayout(acts)

        self._out = QPlainTextEdit()
        self._out.setReadOnly(True)
        self._out.setPlaceholderText('预览结果会写在这里…')
        self._out.setFont(QFont(T.FONTS['mono'][0], T.FONTS['mono'][1]))
        root.addWidget(self._out, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText('关闭')
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _load_ids(self):
        self._old.clear()
        if self._registry is None:
            return
        _, field, _ = _KINDS[self._kind.currentIndex()]
        rows = getattr(self._registry, field, {}) or {}
        for aid in sorted(rows.keys()):
            self._old.addItem(str(aid))
        if rows and self._old.currentIndex() < 0:
            self._old.setCurrentIndex(0)         # 打开就选中第一条，免得"预览"白点
        self._say(f'（{len(rows)} 个候选）')

    def _say(self, text: str):
        self._out.appendPlainText(str(text))

    # ---- 三步 ----

    def preview(self):
        old = (self._old.currentText() or '').strip()
        new = (self._new.text() or '').strip()
        res = self._session.asset_rename_plan(old, new) if self._session else {}
        if not res.get('ok'):
            self._plan = None
            self._apply_btn.setEnabled(False)
            self._say(f'✗ {res.get("error") or "算不出预览"}')
            return
        self._plan = res
        files = res.get('files') or []
        self._say(f'旧名 {res["old_id"]} → 新名 {res["new_id"]}：'
                  f'{len(files)} 个文件 / {res.get("total_lines", 0)} 行会变。')
        for f in files:
            self._say(f'\n{f["path"]}')
            for line_no, before, after in (f.get('changed') or ())[:40]:
                self._say(f'  {line_no}: {before.strip()}')
                self._say(f'  {" " * len(str(line_no))}→ {after.strip()}')
            if len(f.get('changed') or ()) > 40:
                self._say(f'  …（还有 {len(f["changed"]) - 40} 行）')
        if not files:
            self._say('（脚本里没有引用这个旧名字 —— 不需要改任何 .rpy）')
        self._apply_btn.setEnabled(bool(files))

    def apply(self):
        if not self._plan:
            return
        n = self._plan.get('total_lines', 0)
        if QMessageBox.question(
                self, '确认写入脚本',
                f'将修改 {len(self._plan.get("files") or ())} 个 .rpy / {n} 行，'
                f'并把原文件备份成 .bak。\n\n继续？') != QMessageBox.Yes:
            return
        res = self._session.asset_rename_apply(self._plan) if self._session else {}
        if not res.get('ok'):
            self._say(f'✗ 写入失败：{res.get("error")}')
            return
        self._applied = res
        self._say(f'\n✔ 已改 {len(res.get("modified") or ())} 个文件'
                  f'（备份 {len(res.get("backup_files") or ())} 个 .bak）')
        self._undo_btn.setEnabled(bool(res.get('backup_files')))
        self._apply_btn.setEnabled(False)

    def rollback(self):
        if not self._applied:
            return
        res = (self._session.asset_rename_rollback(self._applied)
               if self._session else {})
        self._say(f'\n↩ 已还原 {len(res.get("restored") or ())} 个文件'
                  + (f'，失败 {len(res.get("failed") or ())}'
                     if res.get('failed') else ''))
        if res.get('failed'):
            self._say('  失败：' + '、'.join(res['failed']))
        self._undo_btn.setEnabled(False)
