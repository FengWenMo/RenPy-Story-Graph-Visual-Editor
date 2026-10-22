"""
窗口**崩溃恢复**：定时抄一份，下次打开时问一句。
==========================================================================

用户 2026-10-22（体验报告 P0-2，原话）：

> 拍的改动没有自动保存 —— 关窗会问，崩了就三拍没了。
> 50 人 × 每天几小时，这件事一定会发生。

存哪儿 / 什么形状全在 `core/autosave.py`，"拍 ↔ JSON"在
`app/session_recover.py`。**这个文件只做界面那一侧的三件事**：

```
编辑中 ──每 30 秒──► 脏？→ session.autosave_capture()   （状态栏右上角显时间）
打开一章 ──────────► 这一章有快照？→ 比一比 → 问「恢复 / 不要了 / 稍后」
导出成功 / 没改动就关窗 ─► 删掉快照（已经没有可恢复的东西了）
```

## 三个"不"

1. **不自动恢复**：只问，不替人决定。她可能已经在编辑器里改过那段，
   静默摊回去就是"工具把别人的活盖了"。
2. **不拦人**：写不进去（盘满 / 只读）、读不回来（版本不认识）都只记台账，
   编辑照常。**这条功能的价值是"多一份保险"，不是"多一道门"。**
3. **不在看不见的窗口里干活**：`isVisible()` 为假时（测试 / 嵌入 / 启动
   失败清理）既不写盘也不弹框 —— 否则跑一次全量测试，真实目录里会攒下
   几百个 `tmp_path` 的快照，下次开工具就问你要不要恢复它们。

## 为什么"恢复之后仍然显示未导出"

快照摊回会话时**不**重设基线（见 `app/session_recover` 的模块说明）：
用户必须**看过**、按「⤓ 导出 .rpy」那一下才写文件。恢复完就显示「✔ 已同步」
等于骗她"已经存好了"，随手一关就真没了 —— 那正是这条功能要防的事。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from storygraph.core import autosave as AS
from storygraph.core.safeguard import swallow

__all__ = ['RecoverMixin']


class RecoverMixin:
    """崩溃恢复的界面那一半（见模块 docstring）。"""

    #: 本 mixin 要求宿主（或其它 mixin）提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    REQUIRES = ('_file_path', '_log', '_project_root', '_session', '_status',
                '_status_bar', 'adopt_loaded_session', 'isVisible',
                'load_script')

    #: 自动保存间隔（毫秒）。30 秒是权衡：更短会在一章 1800 拍上
    #: 反复花 20–30 ms 序列化（打字时能感觉到顿），更长则崩一次丢得更多。
    AUTOSAVE_MS = 30000

    #: 这一次运行里已经问过 / 跳过恢复的章节（同一章不反复弹）。
    _recovery_seen: set = frozenset()

    #: 这个窗口当前有没有一份**还在生效**的快照（脏→写一份→置真；
    #: 干净了→删掉→置假）。只是为了让"每分钟删一次不存在的文件"不发生。
    _autosave_live: bool = False

    # ------------------------------------------------------------------
    #  定时抄一份
    # ------------------------------------------------------------------

    def autosave_timer(self) -> QTimer:
        """取（必要时建）自动保存定时器；窗口关掉时它跟着 QT 父子关系一起没。"""
        timer = getattr(self, '_autosave_timer', None)
        if isinstance(timer, QTimer):
            return timer
        timer = QTimer(self)
        timer.setInterval(int(self.AUTOSAVE_MS))
        timer.timeout.connect(self._autosave_tick)
        try:
            timer.start()
        except Exception as e:                  # noqa: BLE001 - 起不来就不自动存
            swallow('ui_qt.window_recover.timer_start', e)
        self._autosave_timer = timer
        return timer

    def _autosave_tick(self):
        """定时点：**有未导出的改动**才抄一份（干净时一个字节都不写）。"""
        if not AS.enabled():
            return
        try:
            if not self.isVisible():
                return
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_recover.tick.visible', e)
            return
        session = getattr(self, '_session', None)
        if session is None or not len(session):
            return
        try:
            if not session.has_unsaved_edits():
                # 干净了 → 上一次那份快照已经没有可恢复的东西（刚导出过，
                # 或者用户把改动**撤销回了原样**）。不在这里收掉的话，
                # 下次打开会问"有 1 拍没导出"，而那一拍早就撤掉了 ——
                # 假警报会训练用户闭眼点"恢复"。
                if getattr(self, '_autosave_live', False):
                    self.autosave_clear()
                return
        except Exception as e:                  # noqa: BLE001 - 判不出来就别写
            swallow('ui_qt.window_recover.tick.dirty', e)
            return
        self.autosave_now()

    def autosave_now(self) -> dict:
        """立刻抄一份（定时器 / 手动 / 测试都走这里），并把结果显示在状态栏。"""
        session = getattr(self, '_session', None)
        if session is None:
            return {'ok': False, 'reason': '还没有会话'}
        try:
            res = session.autosave_capture()
        except Exception as e:                  # noqa: BLE001 - 自动保存绝不炸窗口
            swallow('ui_qt.window_recover.now', e)
            res = {'ok': False, 'reason': f'{type(e).__name__}: {e}'}
        self._show_autosave_state(res)
        return res

    def _show_autosave_state(self, res: dict):
        """
        状态栏那一小格：成功显示**最后一次的钟点**，失败显示原因。

        为什么成功也要显示：用户得能一眼确认"这东西真的在存"
        （不然它和"没有这个功能"在体验上没区别）。失败则必须说出来 ——
        自动保存悄悄失败，比没有自动保存更坏。
        """
        bar = getattr(self, '_status_bar', None)
        setter = getattr(bar, 'set_autosave', None)
        if not callable(setter):
            return
        try:
            if (res or {}).get('ok'):
                self._autosave_live = True
                when = datetime.now().strftime('%H:%M')
                setter(when, str((res or {}).get('path') or ''))
            else:
                setter('', '', error=str((res or {}).get('reason') or ''))
        except Exception as e:                  # noqa: BLE001 - 显示失败不拦人
            swallow('ui_qt.window_recover.show_state', e)

    # ------------------------------------------------------------------
    #  打开一章时：问一句
    # ------------------------------------------------------------------

    def maybe_offer_recovery(self):
        """
        载入一章**之后**调：这一章有快照、且它确实比磁盘多东西 → 问一句。

        为什么放在"之后"而不是"之前"：恢复是**在磁盘那一版之上换掉拍列表**
        （见 `app/session_recover.autosave_restore` 的说明），所以必须先
        按磁盘载入过一次；顺带这一版还能白拿一份基线，用来算"改了几拍"。
        """
        if not AS.enabled():
            return
        try:
            if not self.isVisible():
                return
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_recover.offer.visible', e)
            return
        chapter = str(getattr(self, '_file_path', '') or '')
        if not chapter:
            return
        if chapter in (self._recovery_seen or ()):
            return
        session = getattr(self, '_session', None)
        if session is None:
            return
        try:
            entries = session.autosave_entries(chapter) or []
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_recover.offer.entries', e)
            return
        if not entries:
            return
        entry = entries[0]
        self._recovery_seen = frozenset(set(self._recovery_seen or ()) | {chapter})
        try:
            if self._recovery_is_noop(entry):
                # 崩溃前那次改动已经被撤销 / 已经导出过 → 快照与磁盘一样。
                # 这时**悄悄清掉**，别用一个"要恢复吗"去问一件没有差别的事。
                session.autosave_discard(chapter)
                self._log(f'[自动保存] {Path(chapter).name} 的快照与磁盘一致，'
                          f'已清理。')
                return
        except Exception as e:                  # noqa: BLE001 - 判不出来就照常问
            swallow('ui_qt.window_recover.offer.noop', e)
        self._ask_recovery(entry)

    def _recovery_is_noop(self, entry) -> bool:
        """
        快照摊回去**会不会有差别**？（`True` = 没差别，不用问）

        为什么值得花这 100–200 ms：真实的错位场景是"崩在导出成功的下一瞬"或
        "改完又撤销回去再崩" —— 那时文件已经是快照那版，弹出"有 3 拍没导出"
        是**假警报**，而假警报会训练用户闭眼点"恢复"。
        """
        from storygraph.app.session_table import beat_edits_print
        from storygraph.app.session_recover import beats_from_snapshot
        got = AS.read((entry or {}).get('path'))
        if not got.get('ok'):
            return False
        beats = beats_from_snapshot(got['data'].get('beats'), drop_src=True)
        session = getattr(self, '_session', None)
        if not beats or session is None:
            return False
        fallback = getattr(session, 'rendered_lines_of', None)
        cur = [beat_edits_print(b, fallback) for b in session.beats]
        new = [beat_edits_print(b, fallback) for b in beats]
        return cur == new

    def _ask_recovery(self, entry) -> bool:
        """
        「上次的改动还在」三选一：恢复 / 不要了 / 稍后。

        措辞按用户 2026-10-19 的口径（"不用技术词、把下一步说清楚"）：
        正文说的是**哪一章、改了几拍、什么时候存的**，而不是"快照"。
        """
        session = getattr(self, '_session', None)
        text = AS.entry_line(entry)
        stale = ''
        try:
            stale = session.autosave_stale_reason(entry) if session else ''
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_recover.ask.stale', e)
        box = QMessageBox(self)
        box.setWindowTitle('上次的改动还在')
        box.setIcon(QMessageBox.Question)
        box.setText(text + '\n\n上次工具没正常退出，这些改动还没写进 .rpy 文件。')
        hint = ('恢复后状态栏仍然是「● 未导出」——你看过、点「⤓ 导出 .rpy」'
                '才会写文件。')
        if stale:
            hint = f'⚠ {stale}。\n恢复会用快照里的那一版覆盖内存里的拍（' \
                   f'磁盘上的文件现在不动）；导出时才写文件。\n\n' + hint
        box.setInformativeText(hint)
        restore = box.addButton(('仍然恢复（可能盖掉新改动）' if stale
                                 else '恢复这些改动'), QMessageBox.AcceptRole)
        drop = box.addButton('不要了，丢掉', QMessageBox.DestructiveRole)
        later = box.addButton('稍后（这次先不管）', QMessageBox.RejectRole)
        box.setDefaultButton(restore)
        box.setEscapeButton(later)
        box.exec()
        clicked = box.clickedButton()
        if clicked is restore:
            return self._apply_recovery(entry)
        if clicked is drop:
            if session is not None:
                session.autosave_discard((entry or {}).get('chapter'))
            self._log('[自动保存] 按你的选择丢掉了上次那份快照。')
            self._status('已丢掉上次没导出的改动（磁盘上的文件没动）。')
            return False
        self._log('[自动保存] 这次先不恢复 —— 快照还在，下次打开这一章再问。')
        return False

    def _apply_recovery(self, entry) -> bool:
        """恢复：摊回会话 → 刷新卡片页 → 停在**第一处改动**那一拍。"""
        session = getattr(self, '_session', None)
        if session is None:
            return False
        try:
            res = session.autosave_restore(entry)
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_recover.apply', e)
            res = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
        if not res.get('ok'):
            QMessageBox.warning(
                self, '这份快照恢复不了',
                f'{res.get("error") or "（未知原因）"}\n\n'
                f'快照文件没有动，你可以在文件管理器里打开它看原文：\n'
                f'{entry.get("path") or ""}')
            return False
        index = res.get('first_change')
        try:
            self.adopt_loaded_session(index=(index if isinstance(index, int)
                                             and index >= 0 else None),
                                      goto_card=True)
        except Exception as e:                  # noqa: BLE001 - 刷新失败不影响恢复
            swallow('ui_qt.window_recover.apply.refresh', e)
        self._refresh_dirty_after_recovery()
        where = (f'从第 {int(index) + 1} 拍起' if isinstance(index, int)
                 and index >= 0 else '')
        self._log(f'[自动保存] 已恢复 {res.get("beats")} 拍'
                  f'{"（源文件变过，行号已重新对位）" if res.get("realigned") else ""}'
                  f' —— 还没导出。')
        self._status(f'✔ 已恢复上次没导出的改动（{res.get("beats")} 拍）'
                     f'{where} · 记得点「⤓ 导出 .rpy」写回文件')
        return True

    def _refresh_dirty_after_recovery(self):
        """状态栏 / 标题切回「● 未导出」（**恢复不等于已保存**）。"""
        setter = getattr(self._status_bar, 'set_dirty', None)
        if callable(setter):
            try:
                setter(True, str(getattr(self, '_file_path', '') or ''))
            except Exception as e:              # noqa: BLE001
                swallow('ui_qt.window_recover.refresh_dirty', e)

    # ------------------------------------------------------------------
    #  清：没有可恢复的东西了
    # ------------------------------------------------------------------

    def autosave_clear(self, chapter=None):
        """
        删掉某一章的快照 —— **三个调用点都是同一个意思：已经没有可恢复的东西了**。

          · 导出成功（含关窗前那次写回）；
          · 关窗时用户选了"不保存，直接退出"（她明确不要了，留着下次还会问）；
          · 关窗时本来就没有未导出的改动。

        判据不看"刚才那次是不是导出"，而是"这一份是不是已经没必要留" ——
        留着它的唯一价值就是"崩了能拿回来"，而这三种情况下拿回来的都是
        用户已经表过态的那一版。
        """
        session = getattr(self, '_session', None)
        if session is None or not AS.enabled():
            return
        target = chapter if chapter is not None else getattr(
            self, '_file_path', '')
        if not target:
            return
        try:
            session.autosave_discard(target)
            self._autosave_live = False
        except Exception as e:                  # noqa: BLE001 - 删不掉不拦人
            swallow('ui_qt.window_recover.clear', e)

    # ------------------------------------------------------------------
    #  手动入口
    # ------------------------------------------------------------------

    def show_recovery(self):
        """
        菜单「🕘 上次没导出的改动…」：把**这个项目所有**快照列出来。

        与"打开一章时自动问"的区别：这条是用户主动想看（也可能只是想清掉），
        所以列出全部章节，并且给「全部删掉」。
        """
        session = getattr(self, '_session', None)
        if session is None or not getattr(self, '_project_root', None):
            self._status('先打开一个项目 —— 自动保存的快照是按项目分的。')
            return
        try:
            entries = session.autosave_entries()
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_recover.show', e)
            entries = []
        if not entries:
            self._status('这个项目没有自动保存的快照。'
                         '（编辑时有未导出的改动，每 '
                         f'{int(self.AUTOSAVE_MS / 1000)} 秒存一份）')
            return
        box = QMessageBox(self)
        box.setWindowTitle('上次没导出的改动')
        box.setIcon(QMessageBox.Information)
        box.setText(f'这个项目有 {len(entries)} 份自动保存：')
        box.setInformativeText('\n'.join('· ' + AS.entry_line(e) for e in entries)
                               + '\n\n「恢复」只换内存里的拍，导出才写文件；'
                                 '可以之后再点「⤓ 导出 .rpy」。')
        first = entries[0]
        restore = box.addButton('恢复最新那份', QMessageBox.AcceptRole)
        clean = box.addButton('全部删掉', QMessageBox.DestructiveRole)
        close = box.addButton('关闭', QMessageBox.RejectRole)
        box.setDefaultButton(restore)
        box.setEscapeButton(close)
        box.exec()
        clicked = box.clickedButton()
        if clicked is restore:
            self._open_recovery_entry(first)
        elif clicked is clean:
            res = AS.discard_project(getattr(self, '_project_root', ''))
            self._log(f'[自动保存] 删掉 {res.get("removed", 0)} 份快照。')
            self._status(f'已删掉 {res.get("removed", 0)} 份自动保存。')

    def _open_recovery_entry(self, entry):
        """
        从菜单恢复**另一章**的快照：先按磁盘载入那一章，再摊回。

        顺序不能反（见 `app.session_recover.autosave_restore`）：恢复是
        "在磁盘那一版之上换掉拍列表"，没有磁盘那一版就没有基线。
        """
        chapter = str((entry or {}).get('chapter') or '')
        if not chapter:
            self._status('这份快照里没记是哪一章，恢复不了。')
            return
        stale = ''
        try:
            stale = self._session.autosave_stale_reason(entry)
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_recover.open_entry.stale', e)
        current = str(getattr(self, '_file_path', '') or '')
        if Path(current) != Path(chapter) and self._session.has_unsaved_edits():
            # 当前这一章也有没导出的改动：先问，再换 —— 换过去等于把
            # 内存里这份丢掉（它有可能还没被自动保存过）。
            ans = QMessageBox.question(
                self, '当前这一章还有没导出的改动',
                f'「{Path(current).name}」有没导出的改动。\n'
                f'现在要打开「{Path(chapter).name}」并恢复它吗？\n\n'
                f'当前这一章的改动**会失去**（建议先导出或点「取消」）。',
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if ans != QMessageBox.Yes:
                return
        self.load_script(chapter, goto_card=False, label=None)
        if stale:
            self._log(f'[自动保存] ⚠ {stale} —— 恢复时行号会重新对位。')
        self._apply_recovery(entry)

    # ------------------------------------------------------------------
    #  菜单
    # ------------------------------------------------------------------

    def add_recovery_menu(self, menu):
        """把这一条的入口挂到"更多工具"菜单上（`_build_toolbars` 调）。"""
        if menu is None:
            return
        menu.addAction('🕘 上次没导出的改动…', self.show_recovery).setStatusTip(
            '崩溃 / 断电之后回到上次编辑的位置：列出自动保存的快照，可恢复或删掉')
