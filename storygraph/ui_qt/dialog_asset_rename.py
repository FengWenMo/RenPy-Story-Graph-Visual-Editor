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

from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T

__all__ = ['AssetRenameDialog']

#: 资产种类 → 资源表字段（与 `session_assets.ASSET_KINDS` 同源）
_KINDS = (('image', 'images', '图片'), ('music', 'music', '音乐'),
          ('sound', 'sounds', '音效'), ('voice', 'voices', '语音'),
          ('transform', 'transforms', '变换'))


class AssetRenameDialog(QDialog):
    """预览 → 应用 → 回滚（三步都看得见）。"""

    def __init__(self, parent=None, session=None, registry=None,
                 on_applied=None):
        super().__init__(parent)
        self._session = session
        self._registry = registry
        #: 写盘**成功之后**的回调（窗口用它把这一章从磁盘重读，见
        #: `window_assets._after_asset_rename` —— 不重读，下一次导出会把
        #: 旧名字又写回去，改名等于没做）。
        self._on_applied = on_applied
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
        self._say_side_effects(res)
        self._apply_btn.setEnabled(bool(files))

    def _say_side_effects(self, res: dict):
        """
        把"**没改**的那些命中"和"**读不了**的文件"说清楚（P1-4 / P2-2）。

        为什么必须说：改名是往用户的 `.rpy` 里写东西。两件事都会让"改名
        等于没做完"，而它们的正确做法完全相反 —— 前者要人自己看一眼
        （那是代码/注释/字符串，不是资产引用），后者要人修好文件再来一次
        （编码不对 / 权限 / 文件不在了）。
        """
        rejected = res.get('rejected') or []
        total = int(res.get('rejected_total') or
                    sum(len(r.get('hits') or ()) for r in rejected))
        if total:
            self._say(f'\n⚠ 另有 {total} 处命中了旧名、但看着**不是资产引用位**'
                      f'（代码 / 注释 / 字符串），没有改：')
            shown = 0
            for r in rejected:
                for line_no, raw, why in (r.get('hits') or ()):
                    if shown >= 8:
                        break
                    self._say(f'  {Path(str(r.get("path") or "")).name}:{line_no} '
                              f'{raw.strip()[:60]}')
                    self._say(f'       ↳ 没改：{why}')
                    shown += 1
                if shown >= 8:
                    break
            if total > shown:
                self._say(f'  …（还有 {total - shown} 处）')
            self._say('  真要改它们，请自己确认后手动改（改名只碰资产引用位）。')
        skipped = res.get('skipped_files') or []
        if skipped:
            self._say(f'\n⚠ 有 {len(skipped)} 个文件**读不了**，改名会不完整'
                      f'（游戏里可能"找不到图"）：')
            for item in skipped[:8]:
                self._say(f'  {item.get("path")}：{item.get("reason")}')

    def _touches_current_chapter(self) -> bool:
        """这次改名会改到**手上正开着的这一章**吗？"""
        if self._session is None:
            return False
        path = str(getattr(self._session, 'script_path', '') or '')
        if not path:
            return False
        want = Path(path)
        try:
            return any(Path(str(f.get('path') or '')).resolve()
                       == want.resolve()
                       for f in (self._plan or {}).get('files') or ())
        except OSError as e:                    # noqa: BLE001 - 解析不了就保守当"会"
            swallow('ui_qt.dialog_asset_rename.touches', e)
            return True

    def _block_if_unsaved(self) -> bool:
        """
        「这一章还有没导出的改动」→ **先别改名**（返回 True = 拦住）。

        顺序真的重要：改名写的是文件，而会话内存里那份还是旧的。
        先改名后导出 = 把旧名字写回去（改名白做）；先导出后改名才是对的。
        所以这里不弹"要不要继续"，而是直接说清顺序 —— 让用户先导一次，
        只多按一下，但没有"悄悄把刚改好的名字写回去"的可能。
        """
        if not self._touches_current_chapter():
            return False
        try:
            dirty = bool(self._session.has_unsaved_edits())
        except Exception as e:                  # noqa: BLE001 - 判不出来就不拦
            swallow('ui_qt.dialog_asset_rename.unsaved', e)
            return False
        if not dirty:
            return False
        QMessageBox.warning(
            self, '先导出这一章，再改名',
            '这一章还有没导出的改动。\n\n'
            '改名的顺序不能反：先改名、再导出 = 会话把**旧名字**写回文件，'
            '刚才那次改名等于白做。\n\n'
            '请先回到卡片页点「⤓ 导出 .rpy」，然后再来改名。')
        return True

    def apply(self):
        if not self._plan:
            return
        if self._block_if_unsaved():
            return
        n = self._plan.get('total_lines', 0)
        if QMessageBox.question(
                self, '确认写入脚本',
                f'将修改 {len(self._plan.get("files") or ())} 个 .rpy / {n} 行，'
                f'并把原文件备份成 .bak。\n\n继续？') != QMessageBox.Yes:
            return
        res = self._session.asset_rename_apply(self._plan) if self._session else {}
        modified = list(res.get('modified') or ())
        if modified:
            self._applied = res
            self._say(f'\n✔ 已改 {len(modified)} 个文件'
                      f'（备份 {len(res.get("backup_files") or ())} 个 .bak）')
            self._undo_btn.setEnabled(bool(res.get('backup_files')))
            self._apply_btn.setEnabled(False)
        # —— 下面三种"没全成"都要说清（P0-3 / P2-1）——
        # 从前 `apply` 无脑报 ok，写失败的文件也显示"改名成功"。
        stale = res.get('changed_since_plan') or []
        if stale:
            self._say(f'\n⚠ 这 {len(stale)} 个文件在你**预览之后被改过**，'
                      f'已拒写（原样未动 —— 重新点一次「预览」再应用）：')
            for p in stale[:8]:
                self._say(f'  {p}')
        failed = res.get('failed') or []
        if failed:
            self._say(f'\n⚠ 这 {len(failed)} 个文件没能写入：')
            for item in failed[:8]:
                self._say(f'  {item.get("path")}：{item.get("reason")}')
        if not res.get('ok') and not modified:
            self._say(f'✗ 写入失败：{res.get("error") or res.get("reason")}')
            return
        if not res.get('ok'):
            # 部分成功：回执要如实（别让用户以为全都改好了）
            self._say('以上是**部分成功**：已写的可以回滚，被拒写的请按提示重试。')
            return
        # 窗口要拿这个回调把"手上这一章"从磁盘重读（见 `_on_applied` 的说明）。
        if callable(self._on_applied):
            try:
                self._on_applied(res)
            except Exception as e:              # noqa: BLE001 - 重读失败不该吞掉结果
                swallow('ui_qt.dialog_asset_rename.on_applied', e)
                self._say(f'⚠ 这一章没能自动重读：{e}（请手动重开这一章）')

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
