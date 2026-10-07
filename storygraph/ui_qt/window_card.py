"""
storygraph.ui_qt.window_card.py
===============================
`MainWindowQt` 的**卡片页那一半：载入单章 / 拍列表 / 导航 / 增删卡片 / 卡片级撤销。**

为什么拆（P3-14，2026-10-05）
-----------------------------
`MainWindowQt` 原本是 3452 行 / 120 方法的上帝对象 —— `EditorSession`
（已拆成 5 块）的另一半。它比那边更难拆，因为：

  · 它是 **UI 层**：行为测试薄得多，而且要真起 Qt（`test_ui_qt.py`）；
  · 方法之间靠 **Qt 信号**耦合，不像会话那样能按"主语是什么"干净切开。

所以顺序是**先补网、再拆**：`tests/test_main_window_snapshot.py`
（63 步操作序列 golden）先把外部可观察行为钉住，
拆分期间它必须一直绿。

**做成 mixin 而不是独立对象**：调用方（菜单、工具栏、信号、
`open_editor`）一行都不用改 —— 这是**搬家**不是改契约。

与主类的隐式依赖
----------------
见每个类的 `REQUIRES`：那是**实测**（静态扫 `self.*`）出来的，
由 `tests/test_main_window_mixins.py` 守着。
"""
from __future__ import annotations

from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QSplitter, QStackedWidget, QToolBar,
                               QVBoxLayout, QWidget)

from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.graph_canvas import GraphCanvas
from storygraph.ui_qt.sprite_grid import SpriteGridWidget
from storygraph.ui_qt.status import Notifier, Toast
from storygraph.ui_qt.thumb_loader import ThumbLoader


__all__ = ['CardPageMixin']


class CardPageMixin:
    """
    卡片页那一半：载入单章 / 拍列表 / 导航 / 增删卡片 / 卡片级撤销。

    它要求宿主（`MainWindowQt`）提供 `REQUIRES` 里那些成员 ——
    多半是**主类刻意留下**的：界面骨架、页切换、状态栏、
    静默失败上报、图的所有权。
    """

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    REQUIRES = ('PAGE_CARD', 'PREFETCH_AHEAD', 'PREFETCH_BEHIND', '_canvas', '_file_path', '_list', '_loaded_idx', '_log', '_next_action', '_on_canvas_select', '_page', '_panel', '_pos', '_prev_action', '_project_root', '_refresh_asset_choices', '_report_swallowed', '_session', '_set_page', '_status', '_status_bar', '_swallow_mark', '_update_crumb', '_who', 'graph')

    def _on_load(self):
        start = str(Path(self._project_root or '.') / 'game')
        path, _ = QFileDialog.getOpenFileName(
            self, '直接编辑一章 .rpy（不进剧情图）', start,
            "Ren'Py 脚本 (*.rpy)")
        if path:
            self.load_script(path)
    def load_script(self, path=None, goto_card: bool = True):
        """
        载入一章（**语义全在 EditorSession.load**）。

        `goto_card`：载入是"要在卡片界面里改这一章"，所以默认**跳进卡片页**；
        构造窗口时的自动载入传 False —— 第一屏必须是剧情结构（用户明确要求）。
        """
        target = path or self._session.script_path
        if not target:
            return
        mark = self._swallow_mark()
        try:
            n = self._session.load(target)
            self._panel.set_symbols(self._session.symbols)
            self._refresh_asset_choices()
            self._reload_list()
            self._show_index(0)
            self._sync_graph_highlight()
            if goto_card:
                self._set_page(self.PAGE_CARD)
            else:
                self._update_crumb()
            self._log(f'[载入] {Path(target).name}：{n} 拍'
                      f'（{"进入卡片编辑器" if goto_card else "留在剧情结构页"}）')
            self._status(f'已载入 {Path(target).name}：{n} 拍')
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '载入失败', str(e))
        finally:
            self._report_swallowed(mark)
    def _sync_graph_highlight(self):
        """卡片编辑器正开着的那一章 → 图上对应模块标成"当前"并居中。"""
        if self.graph is None or not self._session.script_path:
            return
        target = Path(self._session.script_path).resolve()
        for node in (self.graph.nodes or {}).values():
            fp = getattr(node, 'file_path', None)
            if fp and Path(str(fp)).resolve() == target:
                self._canvas.highlight_node(node.id)
                self._canvas.select_node(node.id)
                self._on_canvas_select(node.id)
                return
    def _reload_list(self):
        self._list.blockSignals(True)
        self._list.clear()
        for i in range(len(self._session)):
            self._list.addItem(self._make_item(i))
        self._list.blockSignals(False)

        self._who.clear()
        for cid in self._session.participants():
            self._who.addItem(QListWidgetItem(cid))
    def _make_item(self, i) -> QListWidgetItem:
        e = self._session.list_entry(i)
        item = QListWidgetItem(
            f"{i + 1:>3} {e['prefix']}{e['who']}：{e['text'][:24]}")
        item.setData(Qt.UserRole, i)
        return item
    def refresh_list_row(self, i: int):
        """
        单独重画列表里的第 i 行（保存一拍之后调用）。

        为什么需要它：改完台词只更新**卡片**的话，左边列表还挂着旧文本 ——
        用户看到的列表和卡片不一致，会以为没保存上（tkinter 版有这一步，
        搬过来时漏了）。整表重画会丢掉滚动位置与选中态，所以只画一行。
        """
        if not self._session.has(i):
            return
        item = self._list.item(i)
        if item is None:
            return
        e = self._session.list_entry(i)
        self._list.blockSignals(True)
        try:
            item.setText(f"{i + 1:>3} {e['prefix']}{e['who']}：{e['text'][:24]}")
            item.setData(Qt.UserRole, i)
        finally:
            self._list.blockSignals(False)
    def insert_card(self, after: bool = True):
        """在当前这一拍前/后插入一张空卡片（语义在 `EditorSession.insert_beat`）。"""
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('还没有载入章节，没法插入卡片。')
            return
        self.apply_current_beat()             # 先把这一拍的表单提交，免得丢改动
        res = self._session.insert_beat(idx, after=after)
        if not res.get('ok'):
            self._status(res.get('error') or '插入失败')
            return
        self._reload_list()
        self._show_index(res['index'])
        self._sync_structural_buttons()
        self._log(f"[卡片] 已在第 {idx + 1} 拍{'后' if after else '前'}插入一张空卡片"
                  f"（现在是第 {res['index'] + 1} / {res['count']} 拍）")
        self._status(f"✔ 已插入一张空卡片（第 {res['index'] + 1} 拍）——"
                     f"填完按「💾 保存这一拍」，导出时才落盘。")
    def delete_card(self):
        """删掉当前这一拍（**先问一声**：它没有多级撤销，只有单级恢复）。"""
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('还没有载入章节，没有可删的卡片。')
            return
        beat = self._session.beats[idx]
        who = str(getattr(beat, 'speaker', '') or '') or '（旁白）'
        text = (str(getattr(beat, 'text', '') or '')[:60] or '（没有台词）')
        if QMessageBox.question(
                self, '删除这张卡片',
                f'删掉第 {idx + 1} 拍？\n\n{who}：{text}\n\n'
                f'（只改内存里的章节；删完可以点「↩ 撤销结构操作」放回来，'
                f'导出前不落盘）'
        ) != QMessageBox.Yes:
            return
        res = self._session.delete_beat(idx)
        if not res.get('ok'):
            self._status(res.get('error') or '删除失败')
            return
        self._reload_list()
        self._show_index(res['index'])
        self._sync_structural_buttons()
        self._log(f'[卡片] 已删除第 {idx + 1} 拍（{who}：{text}）——'
                  f'还剩 {res["count"]} 拍；点「↩ 撤销结构操作」可以放回来')
        self._status(f'🗑 已删除第 {idx + 1} 拍（还剩 {res["count"]} 拍）。')
    def restore_card(self):
        """
        撤销**最近一次结构编辑**（插卡或删卡，可连按多级）。

        优先走结构撤销栈（`undo_structural`）；栈空时退回旧的单级
        `restore_last_removed`（老表/老调用方仍能拿到那个行为）。
        """
        if self._session.can_undo_structural():
            res = self._session.undo_structural()
        else:
            res = self._session.restore_last_removed()
        if not res.get('ok'):
            self._status(res.get('error') or '没有可恢复的卡片。')
            return
        self._reload_list()
        self._show_index(res['index'])
        self._sync_structural_buttons()
        self._log(f'[卡片] 已把删掉的卡片放回第 {res["index"] + 1} 拍')
        self._status(f'↩ 已恢复那一张卡片（第 {res["index"] + 1} 拍）。')
    def resync_after_export(self, res: dict) -> None:
        """
        导出成功之后刷新卡片列表（2026-10-05）。

        为什么需要：**原地导出**会把内存重新对齐到刚写出的那份文件
        （行号与**粒度**都由解析器定，见 `session_export._reload_after_inplace_write`）
        —— 刚插的那张"只有注释/代码"的卡可能和后面那张并成一张。
        列表不刷新的话，界面还显示旧的那些行，用户点哪一张都对不上，
        `_loaded_idx` 也可能越界。

        只在"拍数真的变了"时才动：没变就别打扰 —— 刷新会丢掉用户
        在列表里的选择状态，而"改一句台词再导出"是最常见的操作。
        """
        count = int(res.get('count') or 0) or len(self._session)
        if count == self._list.count():
            return
        self._reload_list()
        self._show_index(max(0, min(int(self._loaded_idx), count - 1)))
        self._sync_structural_buttons()
        self._log(f'[导出] 写回之后重新对齐到磁盘：'
                  f'{count} 拍（结构由文件决定）')

    def _sync_structural_buttons(self):
        """「撤销结构操作」在**结构栈非空**（或还留着刚删的拍）时可用。"""
        for btn, on in ((getattr(self, '_restore_btn', None),
                         self._session.can_undo_structural()
                         or self._session.has_removed()),
                        (getattr(self, '_delete_btn', None),
                         len(self._session) > 1),
                        (getattr(self, '_insert_btn', None),
                         len(self._session) > 0),
                        (getattr(self, '_insert_before_btn', None),
                         len(self._session) > 0)):
            if btn is not None:
                btn.setEnabled(bool(on))
    def _show_index(self, idx: int):
        if not self._session.has(idx):
            self._idx = -1
            self._session.mark_loaded(-1)
            self._update_nav()
            self._sync_undo_button()
            return
        self._idx = idx
        # 告诉会话"前端现在显示这一拍"（`undo()` 不带参数时的默认目标）
        self._session.mark_loaded(idx)
        self._panel.load_fields(self._session.fields(idx),
                                self._session.cards(idx))
        self._panel.set_speakers(self._session.characters(),
                                 self._session.beats[idx].speaker or '')
        self.refresh_derived_panels(idx)
        self._list.blockSignals(True)
        self._list.setCurrentRow(idx)
        self._list.blockSignals(False)
        self._status_bar.set_dirty(False, self._file_path)
        self._update_nav()
        self._sync_undo_button()
        self._sync_structural_buttons()
        # 相邻几拍的立绘先解进缓存（翻卡片不空等；见 `_prefetch_neighbours`）
        self._prefetch_neighbours(idx)

    def refresh_derived_panels(self, idx: int = None):
        """
        重算这一拍的**只读派生面板**：警告条 / 当前舞台 / 继承提示。

        为什么不只靠 `_show_index`（2026-10-05 用户实测报）：

        > 我已经保存该卡片了，可是当前舞台变化没有生效，并且舞台演出预览没有变化

        "当前舞台"是**载入那一刻**算好写上去的（`set_stage_digest`）。用户
        加了一张立绘、按了 💾 —— 内存里的拍已经变了，可那一块还是载入时的
        旧文案：「立绘：（台上没人）」。于是他合理地认为"保存没生效"，
        而去点演出预览也一样（那个窗口读的是刚打开时的帧……见 `open_preview`）。

        这些标签都是**只读**的（不碰输入控件），所以保存/提交之后重算一次
        是安全的，不会把用户正在编辑的内容冲掉。
        """
        if idx is None:
            idx = self._loaded_idx
        if not self._session.has(idx):
            return
        from storygraph.app import beats as _beats
        beat = self._session.beats[idx]
        try:
            # 「有台词却没有说话人」→ 导出后会变成旁白，在这里就说清楚。
            # 说话人**没登记**时同理要说（否则导出会丢掉它，用户以为工具坏了）。
            # 文案由 `app.beats.speaker_missing_warning` 算（前端只负责显示）。
            self._panel.set_note(
                _beats.speaker_missing_warning(beat, self._session.symbols))
            # 当前舞台（这一拍生效的状态）+ 继承上一拍的提示。
            # 两份文案都来自语义层（stage_snapshot / app.beats），前端不自己拼。
            self._panel.set_stage_digest(self._session.stage_digest_text(idx),
                                         self._session.inherited_hint(idx))
        except Exception as e:                # noqa: BLE001 - 派生面板刷不动不该拦住保存
            swallow('ui_qt.window_card.refresh_derived_panels', e)
    def _prefetch_neighbours(self, idx: int):
        """
        预取**相邻几拍**的立绘缩略图（用户 2026-10-05："卡牌界面图片加载慢"）。

        只做"把图解进缓存"，不碰界面：真正翻过去时 `ThumbLoader.request`
        命中内存缓存 → 0 延迟。范围很小（默认往后 2 拍 / 往前 1 拍），
        且预取本身有张数上限（见 `SpriteGridWidget.prefetch`），
        不会把线程池占满、反把当前这一拍挤慢。
        """
        panel = getattr(self, '_panel', None)
        if panel is None or not self._session.has(idx):
            return
        cards = []
        lo = max(0, int(idx) - self.PREFETCH_BEHIND)
        hi = int(idx) + self.PREFETCH_AHEAD
        for j in range(lo, hi + 1):
            if j == idx or not self._session.has(j):
                continue
            try:
                cards.extend(self._session.cards(j) or ())
            except Exception as e:                # noqa: BLE001 - 预取失败无所谓
                swallow('ui_qt.window_card._prefetch_neighbours', e)
                continue
        if cards:
            panel.prefetch_sprites(cards)
    def apply_current_beat(self):
        """表单 → 当前拍（语义在 `EditorSession.apply`，这里只递数据）。"""
        if not self._session.has(self._loaded_idx):
            return None
        beat = self._session.apply(
            self._loaded_idx,
            self._panel.collect_fields(),
            self._panel.sprite_cards(),
            advanced_open=self._panel.is_advanced_open(),
            # 载入时那一格的原文：会话靠它判断"用户手改了 Stage 没有"
            stage_loaded=self._panel.stage_loaded())
        self._sync_kind_field(beat)
        return beat

    def _sync_kind_field(self, beat):
        """
        把归一化后的「类型」推回面板（说话人一改，那一格立刻跟着变）。

        用户 2026-10-05："明明卡牌编辑器内我已经编写了说话人，kind 仍然
        显示 narrator"。数据层已经会重算（`app.beats.kind_from_speaker`），
        但控件里还是载入时的旧值 —— 那一格在骗人，而它恰好是用户唯一能
        看到"这句算台词还是旁白"的地方。
        """
        if beat is None:
            return
        try:
            self._panel.set_kind(getattr(beat, 'kind', ''))
        except Exception as e:                  # noqa: BLE001 - 界面同步失败不该拦住导出
            swallow('ui_qt.window_card._sync_kind_field', e)
    def save_current_beat(self):
        """
        **保存这张卡片的修改**（卡片上的 💾 按钮 / Ctrl+S / 工具栏同名按钮）。

        走 `EditorSession.save`：它会顺手判"到底改了没有"，
        只有真改了才压撤销栈 —— 所以"翻页顺带提交"不会把撤销按钮点亮。
        """
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('还没有载入章节，没什么可保存。')
            return None
        res = self._session.save(
            idx,
            self._panel.collect_fields(),
            cards=self._panel.sprite_cards(),
            advanced_open=self._panel.is_advanced_open(),
            stage_loaded=self._panel.stage_loaded())

        # 保存成功 = 表单内容成为新基线；列表那一行也要跟着变，
        # 否则左边还挂着旧台词，看起来像"没保存上"。
        self._sync_kind_field(res['beat'])
        self._panel.reset_baseline()
        self.refresh_list_row(idx)
        # 只读派生面板也要跟着变（当前舞台 / 继承提示 / 警告条）——
        # 用户 2026-10-05："我已经保存该卡片了，可是当前舞台变化没有生效"。
        self.refresh_derived_panels(idx)
        self._sync_undo_button()
        self._status_bar.set_dirty(True, self._file_path)

        if res['changed']:
            self._status(f'✔ 已保存到这一拍（{res["line_id"]}）。'
                         f'改动在内存里，点「⤓ 导出 .rpy」时才落盘。')
        else:
            self._status(f'这一拍没有改动，不需要保存（{res["line_id"]}）。')
        return res['beat']
    def undo_current_beat(self):
        """
        **撤销这张卡片的修改** —— 恢复成上一次保存时的样子。

        只影响这一拍（不碰别的卡片）。撤销不可用时**明说**，
        不要静默什么都不做 —— 用户按了按钮却"没反应"最难排查。
        """
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('还没有载入章节，没有可撤销的修改。')
            return
        if not self._session.has_undo(idx):
            # ⚠ **刻意不退到结构撤销**（2026-10-05 定稿）：结构撤销只走
            # 「撤销结构操作」那颗按钮 —— Ctrl+Z 按一下就把卡片删掉/放回来
            # 太意外了（golden 快照第 56 步当场抓到：连按撤销会凭空多一张卡）。
            self._status('这一拍没有可撤销的修改'
                         '（撤销只能回到"上一次保存"之前）。')
            return
        self._session.undo(idx)
        # 数据变了 → 必须重新载入表单与立绘卡（含 chip 与缩略图）
        self._show_index(idx)
        self.refresh_list_row(idx)
        self._sync_undo_button()
        self._status_bar.set_dirty(True, self._file_path)
        self._status('↩ 已撤销这一拍的修改，恢复成上一次保存时的样子。')
    def _sync_undo_button(self):
        """撤销按钮的可用性跟着会话的撤销栈走。"""
        try:
            self._panel.set_undo_enabled(
                self._session.has_undo(self._loaded_idx))
        except Exception:                       # noqa: BLE001
            swallow('main_window._sync_undo_button')
    def _update_nav(self):
        n = len(self._session)
        i = self._loaded_idx
        # 翻拍按钮同样只在卡片页可用：它们在结构页上没有意义
        # （键盘那条路同样由 `eventFilter` 判页号）。
        on_card = self._page == self.PAGE_CARD
        if hasattr(self, '_prev_action'):
            self._prev_action.setEnabled(on_card and i > 0)
        if hasattr(self, '_next_action'):
            self._next_action.setEnabled(on_card and 0 <= i < n - 1)
        if hasattr(self, '_pos'):
            self._pos.setText(f'{i + 1 if i >= 0 else 0} / {n}')
        self._update_crumb()
    def _sync_nav(self):
        if hasattr(self, '_prev_action'):
            self._update_nav()
    def _on_select(self, row: int):
        if row >= 0 and row != self._loaded_idx:
            self.apply_current_beat()
            self._show_index(row)
    def next_beat(self):
        """
        前进一拍（先提交当前拍，再翻页）。

        **越界必须有守卫**：第一版直接 `_show_index(self._loaded_idx + 1)`，
        走到最后一拍会 `_show_index(n)` → 内部把 `_idx` 设成 `-1`。
        后果不是"没反应"，而是**静默丢改动**：卡片上还显示着最后一拍、
        用户改完按「💾 保存这一拍」，`apply_current_beat` 一看
        `has(-1)` 是假就什么都不做 —— 用户以为存了。
        （tkinter 版的 `_update_nav_state` 就是干这个的，搬过来时漏了。）
        """
        i = self._loaded_idx
        if not self._session.has(i):
            return
        self.apply_current_beat()
        if i < len(self._session) - 1:
            self._show_index(i + 1)
    def prev_beat(self):
        """后退一拍（同样先提交、同样不许越界）。"""
        i = self._loaded_idx
        if not self._session.has(i):
            return
        self.apply_current_beat()
        if i > 0:
            self._show_index(i - 1)
    def _fill_speaker(self, item):
        cid = item.text() if item else ''
        if not cid or not self._session.has(self._loaded_idx):
            return
        self._session.set_speaker(self._loaded_idx, cid)
        self._panel.set_speaker(cid)
        self._status_bar.set_dirty(True, self._file_path)
        self._status(f'已把「{cid}」填进这一拍的说话人。')
