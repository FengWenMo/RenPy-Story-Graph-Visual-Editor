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


def _statement_of(beat, statements) -> dict:
    """
    「这一拍是哪条语句」→ `{'name','label','head'}`（台词拍 → `{}`）。

    三种来源，**顺序即优先级**：

      · `spec_name` —— 项目声明过的自定义语句（T1，卡片能改参数的就是它）；
      · `camera_header` —— 3D 舞台的 `camera …:`（头行可编辑）；
      · `loop_header` —— `while` / `for`（块头行可编辑）。

    为什么卡片需要它：这几种拍**没有台词**，从前在卡片上就是一张空白的
    对话卡（说话人 + 空台词框 + 空立绘网格），人看不出这一拍到底在干什么。
    """
    name = str(getattr(beat, 'spec_name', '') or '').strip()
    head = str(getattr(beat, 'spec_head', '') or '').strip()
    if not name:
        head = str(getattr(beat, 'camera_header', '') or '').strip()
        name = 'camera' if head else ''
    if not name:
        head = str(getattr(beat, 'loop_header', '') or '').strip()
        name = (head.split()[0] if head.split() else '')
    if not name:
        return {}
    label = ''
    try:
        spec = statements.match(name) if statements is not None else None
        label = str(getattr(spec, 'label', '') or '')
    except Exception as e:                      # noqa: BLE001 - 没有标签也能显示
        swallow('ui_qt.window_card._statement_of.label', e)
    return {'name': name, 'label': label, 'head': head}


def _loose_lines(beat) -> list:
    """
    这一拍里**工具没建模、但会原样执行**的行（卡片顶部要说得出口）。

    为什么需要（2026-10-18）：一条 `play ambience "amb/wind.ogg"`（项目自定义
    声道）或 `show screen map` 既不是台词、也不进任何结构化列 —— 它躺在
    「代码」格里，而那一格在**折叠着的高级区**里。于是卡片上看起来
    "这一拍什么都没有"，人根本不知道该去哪里改。

    取的是 `raw`（代码格里原生的那些行）与 `opaque`（整块原样行），
    去掉已经由语句条显示的那条头行（否则同一行说两遍）。
    """
    head = str(getattr(beat, 'spec_head', '') or '').strip()
    # 控制流行已经由卡片顶部那条「调用 / 跳转」条显示，不要再在
    # "这一拍还会原样执行"里说一遍（2026-10-22，P0-4）。
    from storygraph.story.flow import parse_flow
    flow = parse_flow(beat)
    flow_line = str((flow or {}).get('line') or '')
    out: list = []
    for src in (getattr(beat, 'raw', ''), getattr(beat, 'opaque', '')):
        for line in str(src or '').splitlines():
            text = line.strip()
            if text and text != head and text != flow_line and text not in out:
                out.append(text)
    return out


class CardPageMixin:
    """
    卡片页那一半：载入单章 / 拍列表 / 导航 / 增删卡片 / 卡片级撤销。

    它要求宿主（`MainWindowQt`）提供 `REQUIRES` 里那些成员 ——
    多半是**主类刻意留下**的：界面骨架、页切换、状态栏、
    静默失败上报、图的所有权。
    """

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    REQUIRES = ('PAGE_CARD', 'PREFETCH_AHEAD', 'PREFETCH_BEHIND', '_canvas', '_file_path', '_label_combo', '_list', '_loaded_idx', '_log', '_next_action', '_on_canvas_select', '_page', '_panel', '_pos', '_prev_action', '_project_root', '_refresh_asset_choices', '_report_swallowed', '_run_command', '_session', '_set_page', '_status', '_status_bar', '_swallow_mark', '_update_crumb', '_who', 'graph')

    def _on_load(self):
        start = str(Path(self._project_root or '.') / 'game')
        path, _ = QFileDialog.getOpenFileName(
            self, '直接编辑一章 .rpy（不进剧情图）', start,
            "Ren'Py 脚本 (*.rpy)")
        if path:
            self.load_script(path)
    def load_script(self, path=None, goto_card: bool = True, label=None):
        """
        载入一章（**语义全在 EditorSession.load**）。

        `goto_card`：载入是"要在卡片界面里改这一章"，所以默认**跳进卡片页**；
        构造窗口时的自动载入传 False —— 第一屏必须是剧情结构（用户明确要求）。

        `label`：同一个 `.rpy` 里有多个 label 时，落在**这个 label 的第一拍**，
        而不是文件第 1 拍。点图上的第二个 label 节点走的就是这条路。
        """
        target = path or self._session.script_path
        if not target:
            return
        mark = self._swallow_mark()
        try:
            # 只有真要定位 label 时才传第二个参数：老调用方 / 测试里的
            # `load(path)` 替身仍然能接住（少一个位置参数不破坏兼容）。
            n = (self._session.load(target, label=label) if label
                 else self._session.load(target))
            self._panel.set_symbols(self._session.symbols)
            self._sync_choice_groups()
            self._refresh_asset_choices()
            self._reload_list()
            idx = self._session.index_of_label(label) if label else 0
            if label and idx < 0:
                self._log(f'[载入] ⚠ {Path(target).name} 里没有 label '
                          f'「{label}」—— 已退到第 1 拍')
                self._status(f'{Path(target).name} 里找不到 label「{label}」，'
                             f'已从第 1 拍打开。')
                idx = 0
            self._show_index(idx)
            self._sync_label_combo()
            self._sync_graph_highlight()
            if goto_card:
                self._set_page(self.PAGE_CARD)
            else:
                self._update_crumb()
            where = f' · {label}' if label else ''
            self._log(f'[载入] {Path(target).name}{where}：{n} 拍'
                      f'（{"进入卡片编辑器" if goto_card else "留在剧情结构页"}）')
            self._status(f'已载入 {Path(target).name}{where}：{n} 拍')
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '载入失败', str(e))
        finally:
            self._report_swallowed(mark)
    def _sync_choice_groups(self):
        """
        把项目声明的**候选分栏**接成立绘网格的分组候选（分栏显示"服装 / 表情"）。

        用户 2026-10-10：

        > 实际上需要分组显示……因为我这个要求通用！所以一般人其实是没有服装这个
        > 选项的！所以事实上应当通过 B 这个自定义模块，做到把服装跟表情这种东西
        > 分开！

        所以：**分哪几栏由项目声明决定**（`rules.json` 的 `attr_groups`），
        工具不写死任何组名；没声明 → 推空 → 网格走原来的平铺候选（一字不变）。
        值是**界面显示值**（走 `expression_groups_for` 同一套翻译），
        与 chip 上看到的字面量一致。

        **E2（2026-10-10）：引擎事实优先。**
        采过 `engine_facts.json` 之后，分组与候选**都从引擎来**
        （`layeredimage` 的 group + 引擎枚举的属性），项目声明**降级为"改名"**
        （把 `outfit` 显示成「服装」）。真机实测：`lan` 的组本来就叫
        `outfit` / `expressions` —— 再让人声明一遍是重复劳动，还会两边打架。
        ⚠ 没采过引擎事实（`facts` 为空）时，**一维一格仍然要能工作** ——
        判据退回项目自己的 `layeredimage`（`app.beats.attr_slot_rows` 里有这一档，
        2026-10-16 实测报的"粘在一起了"就是因为从前这里直接 `return` 掉了，
        那一档永远走不到）。所以现在**总是**注入 `_for_tag`（可以是空的），
        让网格自己按"有没有引擎事实 / 有没有 layeredimage"去决定。
        """
        try:
            groups = self._session.declared_attr_groups()
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_card._sync_choice_groups.declare', e)
            groups = []
        try:
            facts = self._session.engine_facts()
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_card._sync_choice_groups.facts', e)
            facts = {}
        from storygraph.app.beats import expression_groups_for
        symbols = getattr(self._session, 'symbols', None)

        def _for_tag(tag):
            return expression_groups_for(str(tag or ''), symbols, groups, facts)

        # **把原料挂在函数上**（2026-10-12）：卡片要"一维一格"时得知道
        # 引擎的分组与候选（`app.beats.attr_slot_rows`），但网格那套注入通道
        # 只传得下一个可调用对象。挂属性 = **不新增一个"要记得转发"的方法** ——
        # 转发层漏一个方法的代价是模态弹窗（见 `main_window.set_choice_groups_provider`
        # 的注释，那个坑真踩过）。
        _for_tag.attr_groups = list(groups or ())
        _for_tag.engine_facts = facts if isinstance(facts, dict) else {}
        #: ⚠ 2026-10-16：把**项目根**也挂上 —— 没有引擎事实时，`attr_slot_rows`
        #:   要退回项目自己的 `layeredimage`（它自己按项目根建/读模型缓存）。
        _for_tag.project_root = getattr(self._session, 'project_root', None)

        # 现取（不缓存）：同一个词在不同角色下的显示名可能不同
        self._panel.set_choice_groups_provider(_for_tag)
        self._warn_engine_facts_once()

    def _warn_engine_facts_once(self):
        """
        引擎级告警**只在打开项目时说一次**（N2/N5：项目覆盖了内置语句）。

        为什么不塞进卡片那条 note：note 是"**这一拍**"的提示，跟着翻页刷新 ——
        把项目级的告警放进去，就会每翻一页重复喊一遍，人很快就不看了。
        """
        if getattr(self, '_facts_warned', False):
            return
        self._facts_warned = True
        try:
            from storygraph.app import notify
            for msg in self._session.engine_facts_warnings():
                notify.warn(msg)
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_card._warn_engine_facts_once', e)
    def _sync_graph_highlight(self):
        """卡片编辑器正开着的那一章 → 图上对应模块标成"当前"并居中。"""
        if self.graph is None or not self._session.script_path:
            return
        target = Path(self._session.script_path).resolve()
        focus = str(getattr(self._session, 'focus_label', '') or '')
        candidates = []
        for node in (self.graph.nodes or {}).values():
            fp = getattr(node, 'file_path', None)
            if fp and Path(str(fp)).resolve() == target:
                candidates.append(node)
        if not candidates:
            return
        # 同一个文件里多个 label：**优先高亮当前 label**，不再永远命中第一个。
        node = next((n for n in candidates
                     if str(getattr(n, 'label_name', '') or '') == focus),
                    candidates[0])
        self._canvas.highlight_node(node.id)
        self._canvas.select_node(node.id)
        self._on_canvas_select(node.id)
    def _sync_label_combo(self):
        """
        把"这个文件里有哪些 label"填进左栏下拉，并选中当前 label。

        `blockSignals` 很重要：载入时 `addItems` 会发 `currentIndexChanged`，
        不挡住就会在 `_show_index` 之前又跳一次。
        """
        combo = getattr(self, '_label_combo', None)
        if combo is None:
            return
        labels = self._session.label_names()
        current = str(getattr(self._session, 'focus_label', '') or '')
        combo.blockSignals(True)
        try:
            combo.clear()
            for name in labels:
                combo.addItem(name)
            idx = combo.findText(current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.setEnabled(len(labels) > 1)
            combo.setToolTip(
                f'这个 .rpy 里有 {len(labels)} 个 label；选一个就跳到它的第一拍'
                if len(labels) > 1 else '这个 .rpy 里只有一个 label')
        finally:
            combo.blockSignals(False)
    def _on_label_changed(self, index: int):
        """左栏 label 下拉：切到那个 label 的第一拍（先提交当前卡片的改动）。"""
        combo = getattr(self, '_label_combo', None)
        if combo is None or index < 0:
            return
        label = combo.itemText(index)
        target = self._session.index_of_label(label)
        if target < 0 or target == self._loaded_idx:
            return
        self.apply_current_beat()
        self._show_index(target)
        self._sync_graph_highlight()
        self._status(f'已切到 label「{label}」的第一拍（第 {target + 1} 拍）。')
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
        item = QListWidgetItem(self._row_text(self._session.list_entry(i)))
        item.setData(Qt.UserRole, i)
        return item

    @staticmethod
    def _row_text(e: dict) -> str:
        """
        左边列表那一行的**字**（2026-10-18：语句拍不再显示成"（无台词）"）。

        一条语句拍显示成

             3 ◆ play_music · 播放音乐  track=bgm file="bgm/theme.ogg"

        而不是「3 ：（无台词）」—— 后者让所有非台词的拍在列表里长得一样，
        人得一张张点进去才知道哪条是哪条。

        ⚠ 记号用 `◆` 而不是 `⚙`（2026-10-18 截图当场看出来的）：`⚙`(U+2699)
          在微软雅黑里没有字形，界面上是个豆腐块 `□`。
        """
        head = str(e.get('statement_head') or '')
        flow_type = str(e.get('flow_type') or '')
        if flow_type:
            arrow = '↳' if flow_type == 'call' else '→'
            verb = '调用' if flow_type == 'call' else '跳转'
            return (f"{int(e.get('index', 0)) + 1:>3} "
                    f"{arrow} {verb} {e.get('flow_target') or '（未指定）'}")
        if head:
            name = str(e.get('statement') or '')
            rest = head[len(name):].strip() if head.startswith(name) else head
            label = str(e.get('statement_label') or '')
            tail = f' · {label}' if label else ''
            return f"{int(e.get('index', 0)) + 1:>3} ◆ {name}{tail}  {rest}"
        return (f"{int(e.get('index', 0)) + 1:>3} "
                f"{e['prefix']}{e['who']}：{e['text'][:24]}")
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
            item.setText(self._row_text(e))
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

    def insert_flow_card(self, after: bool = True):
        """
        在当前拍后插入一张 `call` / `jump` 控制流卡（2026-10-22，P0-4a）。

        这是"大 label 里 call 小 label 再回来"的**入口**：不用回源文件手写，
        选类型 + 目标即可。插入的是内存里的拍，导出时才落盘。
        """
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('还没有载入章节，没法插入控制流卡。')
            return
        self.apply_current_beat()
        labels = list(self._session.label_names())
        for node in ((self.graph.nodes or {}).values()
                     if self.graph is not None else ()):
            name = str(getattr(node, 'label_name', '') or '').strip()
            if name and name not in labels:
                labels.append(name)
        from storygraph.ui_qt.dialogs import FlowCardDialog
        dlg = FlowCardDialog(self, labels=labels)
        if dlg.exec() != QDialog.Accepted:
            return
        res = self._session.insert_flow_beat(
            idx, after=after, flow_type=dlg.flow_type, target=dlg.target)
        if not res.get('ok'):
            self._status(res.get('error') or '插入控制流卡失败')
            return
        beat = self._session.beats[res['index']]
        from storygraph.story.flow import flow_info
        info = flow_info(beat, res['index'], self._session.beats)
        edge_note = self._sync_flow_edge(beat, None, info)
        self._reload_list()
        self._show_index(res['index'])
        self._sync_structural_buttons()
        self._log(f'[控制流] 插入 {dlg.flow_type} {dlg.target}'
                  f'（第 {res["index"] + 1} 拍）'
                  + (f'；{edge_note}' if edge_note else ''))
        self._status(f'✔ 已插入 {dlg.flow_type} {dlg.target}'
                     f'（第 {res["index"] + 1} 拍）'
                     + (f'。{edge_note}' if edge_note else ''))

    def delete_card(self):
        """删掉当前这一拍（**先问一声**：它没有多级撤销，只有单级恢复）。"""
        idx = self._loaded_idx
        if not self._session.has(idx):
            self._status('还没有载入章节，没有可删的卡片。')
            return
        beat = self._session.beats[idx]
        from storygraph.story.flow import flow_card_text, parse_flow
        flow = parse_flow(beat)
        who = '控制流' if flow else (
            str(getattr(beat, 'speaker', '') or '') or '（旁白）')
        text = (flow_card_text(flow) if flow
                else (str(getattr(beat, 'text', '') or '')[:60]
                      or '（没有台词）'))
        # **删掉的是几行？**（用户 2026-10-21：源码塞在「代码」那一格里，
        # Excel 只有一行高，人看不见自己删了 7 行代码 + 1 句台词）
        preview = self._delete_preview(idx)
        if preview and preview.get('ok') is False \
                and not preview.get('can_cascade'):
            # 删不了（只剩一拍 / 这一拍不存在）：**别弹那个"要删几行"的框**，
            # 否则用户确认完才被告知删不了。
            self._status(preview.get('error') or '这一拍删不了。')
            return
        if QMessageBox.question(
                self, '删除这张卡片',
                f'删掉第 {idx + 1} 拍？\n\n{who}：{text}'
                + self._delete_line_note(preview) +
                f'\n\n（只改内存里的章节；删完可以点「↩ 撤销结构操作」放回来，'
                f'导出前不落盘）'
        ) != QMessageBox.Yes:
            return
        res = self._session.delete_beat(idx)
        if not res.get('ok') and res.get('can_cascade'):
            # 块头（菜单/循环/选项体）带着块体：先问"要不要连块体一起删"。
            n = int(res.get('blocked_count') or 0)
            wide = self._delete_preview(idx, cascade=True)
            if QMessageBox.question(
                    self, '删除整块',
                    f'{res.get("error") or "这是块的开头"}'
                    f'\n\n要连着块体一起删掉吗？（共 {n + 1} 拍）'
                    + self._delete_line_note(wide)
            ) != QMessageBox.Yes:
                return
            preview = wide
            res = self._session.delete_beat(idx, cascade=True)
        if not res.get('ok'):
            self._status(res.get('error') or '删除失败')
            return
        self._reload_list()
        self._show_index(res['index'])
        self._sync_structural_buttons()
        lines = int(preview.get('lines') or 0)
        tail = f'，产物里少了 {lines} 行（{self._lines_brief(preview)}）' \
            if lines > 1 else ''
        self._log(f'[卡片] 已删除第 {idx + 1} 拍（{who}：{text}）{tail} ——'
                  f'还剩 {res["count"]} 拍；点「↩ 撤销结构操作」可以放回来')
        self._status(f'🗑 已删除第 {idx + 1} 拍{tail}（还剩 {res["count"]} 拍）。')

    # ---------------- 删除前"要丢几行" ----------------

    def _delete_preview(self, idx: int, cascade: bool = False) -> dict:
        """问会话"删这一拍会让产物少什么"；算不出来就返回空（不拦操作）。"""
        try:
            return self._session.delete_preview(idx, cascade=cascade) or {}
        except Exception as e:                    # noqa: BLE001 - 预览失败不拦删除
            swallow('ui_qt.window_card._delete_preview', e)
            return {}

    @staticmethod
    def _lines_brief(preview: dict) -> str:
        """`前两行…` 的短版本（状态栏／控制台用，一行说完）。"""
        head = [str(x) for x in (preview.get('head') or ())]
        if not head:
            return '多行'
        brief = ' ／ '.join(head[:2])
        if int(preview.get('lines') or 0) > len(head):
            brief += ' …'
        return brief

    def _delete_line_note(self, preview: dict) -> str:
        """
        确认框里那段"要丢几行"。

        单行拍的多数情况返回空串（不打扰）；多行拍把**前两行原文**摆出来 ——
        "8 行"是数字，`$ renpy.block_rollback()` 才是人能认出来的东西。
        """
        lines = int(preview.get('lines') or 0)
        if lines <= 1:
            return ''
        head = [str(x) for x in (preview.get('head') or ())]
        more = lines - len(head)
        body = '\n'.join('    ' + h for h in head)
        tail = f'\n    …（其余 {more} 行）' if more > 0 else ''
        # ⚠ 这里**不用 Markdown 的 `**`**：`QMessageBox` 只认纯文本/HTML，
        # 星号会原样显示（截图里就是 `**8 行**`），比不加粗更难看。
        return f'\n\n⚠ 这一拍在产物里是 {lines} 行（不是 1 行）：\n{body}{tail}'

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
    def next_dialogue(self):
        """
        「下一句台词 ▶」—— 用户 2026-09-28 要的**只改台词**快捷路径（本轮补做）。

        用户要的是"快速过一遍台词"：一句一句跳，不必每次都在列表里找。
        ⚠ **不做列表过滤**（把没有台词的拍藏起来）：卡片下标要映射到
        `session.beats[idx]`，一旦过滤，下标与实际拍就对不上 ——
        那是"存错拍"级别的隐患，比"找不到"严重得多。
        所以这里只**移动选中**，列表一个都不藏。
        """
        beats = getattr(self._session, 'beats', None) or []
        if not beats:
            self._status('这一章还没载入拍')
            return
        # ⚠ 别用 `... or -1`：`self._idx == 0` 是**合法的第一拍**，
        #   而 `0` 是 falsy —— 会被悄悄换成 -1（判据当场抓到，害我少跳一拍）。
        cur = getattr(self, '_idx', -1)
        start = max(int(-1 if cur is None else cur) + 1, 0)
        for i in range(start, len(beats)):
            if self._has_dialogue(beats[i]):
                self._show_index(i)
                self._focus_dialogue()
                self._status(f'第 {i + 1} 句台词（共 {len(beats)} 拍）')
                return
        # 后面没有了 → **说清楚**，不许默默不动（能点就必须有反应）
        self._status('后面没有台词了 —— 这是最后一句')

    @staticmethod
    def _has_dialogue(beat) -> bool:
        """这一拍有没有台词（谁说了句话）。"""
        if beat is None:
            return False
        kind = getattr(beat, 'kind', '')
        name = getattr(kind, 'name', '') or str(kind)
        text = str(getattr(beat, 'text', '') or '').strip()
        if not text:
            return False
        return name.upper() in ('SAY', 'EXTEND') or bool(
            str(getattr(beat, 'speaker', '') or '').strip())

    def _focus_dialogue(self):
        """把光标放进台词框（跳过去就能直接打字）。"""
        panel = getattr(self, '_panel', None)
        for name in ('text', '_text', 'text_edit'):
            widget = getattr(panel, name, None) if panel is not None else None
            if widget is not None and hasattr(widget, 'setFocus'):
                widget.setFocus()
                return

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
        # 当前 label 跟着**实际显示的那一拍**走：翻到别的 label 时，
        # 面包屑 / 下拉 / 图上的高亮都应当一起变。
        self._session.focus_label = str(
            getattr(self._session.beats[idx], 'label', '') or '')
        self._panel.load_fields(self._session.fields(idx),
                                self._session.cards(idx))
        self._sync_spec_args(idx)
        self._panel.set_speakers(self._session.characters(),
                                 self._session.beats[idx].speaker or '')
        self.refresh_derived_panels(idx)
        self._list.blockSignals(True)
        self._list.setCurrentRow(idx)
        self._list.blockSignals(False)
        self._sync_label_combo()
        self._status_bar.set_dirty(False, self._file_path)
        self._update_nav()
        self._sync_undo_button()
        self._sync_structural_buttons()
        # 相邻几拍的立绘先解进缓存（翻卡片不空等；见 `_prefetch_neighbours`）
        self._prefetch_neighbours(idx)

    def _sync_spec_args(self, idx: int):
        """
        把这一拍的**语句参数**（列 + 当前值 + 候选）推给卡片那一组控件。

        语义在 `app.beats.spec_arg_*`（与 Excel 的列**同源**），这里只递数据 ——
        前端不认识"哪些语句有参数"这件事。

        取注册表失败（非项目目录 / 读不成）时**不炸**：退化成"只有内置形状"
        （`camera` / `with` / `pause` / `window` 由工具自己声明，不需要项目声明）。
        """
        from storygraph.app import beats as _beats
        try:
            beat = self._session.beats[idx]
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_card._sync_spec_args.beat', e)
            return
        statements = None
        try:
            root = getattr(self._session, 'project_root', None)
            if root:
                from storygraph.story.statement_registry import StatementRegistry
                statements = StatementRegistry.for_project(str(root))
        except Exception as e:                  # noqa: BLE001 - 没声明也要能显示内置形状
            swallow('ui_qt.window_card._sync_spec_args.registry', e)
            statements = None
        try:
            cols, values = _beats.spec_arg_fields_for(beat, statements)
            registry = getattr(getattr(self._session, 'symbols', None),
                               'registry', None)
            choices = _beats.spec_arg_choices_for(beat, statements,
                                                  registry=registry)
            self._panel.set_spec_args(
                cols, values, choices,
                statement=_statement_of(beat, statements),
                loose=_loose_lines(beat))
        except Exception as e:                  # noqa: BLE001 - 参数组刷不动不该拦住卡片
            swallow('ui_qt.window_card._sync_spec_args', e)
        self._sync_flow_card(beat, idx)

    def _sync_flow_edge(self, beat, old, new) -> str:
        """
        控制流目标 / 类型改了之后，把剧情图上对应的边一起改掉。

        第一阶段只同步**两端节点都已在图上**的情况（最常见的"同一文件里
        大 label call 小 label"）。目标还没导入时不硬造节点，返回一句提示；
        第二阶段图边从 flow 拍派生时，这一段会被替换掉。
        """
        if self.graph is None or not new:
            return ''
        old = dict(old or {})
        new_type = str(new.get('type') or new.get('FlowType') or '')
        new_target = str(new.get('target') or new.get('FlowTarget') or '')
        old_type = str(old.get('type') or old.get('FlowType') or '')
        old_target_name = str(old.get('target') or old.get('FlowTarget') or '')
        if old_type == new_type and old_target_name == new_target:
            return ''
        nodes = list((self.graph.nodes or {}).values())
        script = str(getattr(self._session, 'script_path', '') or '')

        def _same_file(value) -> bool:
            try:
                return Path(str(value)).resolve() == Path(script).resolve()
            except (OSError, ValueError):
                return str(value) == script

        src = next((n for n in nodes
                    if str(getattr(n, 'label_name', '') or '')
                    == str(getattr(beat, 'label', '') or '')
                    and _same_file(getattr(n, 'file_path', ''))),
                   None)
        if src is None:
            return ''
        target = next((n for n in nodes
                       if str(getattr(n, 'label_name', '') or '')
                       == new_target), None)
        if target is None:
            return (f'目标「{new_target}」还不在剧情图上 —— '
                    f'边没有同步；先导入它所在的章节。')
        old_target = next((n for n in nodes
                           if str(getattr(n, 'label_name', '') or '')
                           == old_target_name), None)
        edge = None
        if old_target is not None:
            edge = next((e for e in (self.graph.edges or {}).values()
                         if e.from_id == src.id and e.to_id == old_target.id
                         and e.type == old_type), None)
        from_clause = (str(old.get('from_clause') or '')
                       if new_type == 'call' else '')
        try:
            if edge is not None:
                from storygraph.graph.commands import RetargetEdgeCommand
                ok = self._run_command(RetargetEdgeCommand(
                    self, edge.id, to_id=target.id, etype=new_type,
                    from_clause=from_clause))
            else:
                # 图上已经有同一条边（同一个 label 里多次 call 同一目标）：
                # 不重复画一条 —— 第一阶段先保证不出现"两条一模一样的线"。
                same = any(e.from_id == src.id and e.to_id == target.id
                           and e.type == new_type
                           for e in (self.graph.edges or {}).values())
                if same:
                    return ''
                from storygraph.graph.commands import AddEdgeCommand
                ok = self._run_command(AddEdgeCommand(
                    self, src.id, target.id, new_type,
                    str(getattr(beat, 'condition', '') or ''), '',
                    from_clause=from_clause))
        except Exception as e:                    # noqa: BLE001 - 同步失败不该拦保存
            swallow('ui_qt.window_card._sync_flow_edge', e)
            return f'图上的边没有同步（{e}）'
        return '' if ok else '图上的边没有同步（命令没有记进图历史）'

    def _sync_flow_card(self, beat, idx: int):
        """
        把这一拍是不是 `call` / `jump`、目标与返回拍推给控制流条。

        候选 label 先放**本文件**里的（最常用），再补图上的其它节点
        （跨文件 call 也常见）；控件可编辑，手写照样能过。
        """
        try:
            from storygraph.story.flow import flow_info
            info = flow_info(beat, idx, self._session.beats)
            names = list(self._session.label_names())
            for node in ((self.graph.nodes or {}).values()
                         if self.graph is not None else ()):
                name = str(getattr(node, 'label_name', '') or '').strip()
                if name and name not in names:
                    names.append(name)
            self._panel.set_flow(info, names)
        except Exception as e:                  # noqa: BLE001 - 控制流条失败不拦卡片
            swallow('ui_qt.window_card._sync_flow_card', e)

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
            # **N3 同组冲突**也在这里说（`show lan happy sad` 这种 —— lint 实测不报）。
            # 文案整体由 `app.beats.card_note` 拼（前端只负责显示，不自己凑句子）。
            facts = {}
            try:
                facts = self._session.engine_facts()
            except Exception as e:            # noqa: BLE001
                swallow('ui_qt.window_card.refresh_derived_panels.facts', e)
            self._panel.set_note(_beats.card_note(
                beat, self._session.symbols, facts,
                project_root=getattr(self._session, 'project_root', None)))
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
        old_flow = (dict(self._panel._flow)
                    if getattr(self._panel, '_flow', None) else None)
        beat = self._session.apply(
            self._loaded_idx,
            self._panel.collect_fields(),
            self._panel.sprite_cards(),
            advanced_open=self._panel.is_advanced_open(),
            # 载入时那一格的原文：会话靠它判断"用户手改了 Stage 没有"
            stage_loaded=self._panel.stage_loaded())
        self._sync_kind_field(beat)
        # 翻页 / 预览 / 导出前的"顺带提交"也把控制流边同步掉：
        # 否则卡片上改了目标、一翻页，图还指着旧目标。
        if old_flow:
            from storygraph.story.flow import flow_info
            new_flow = flow_info(beat, self._loaded_idx, self._session.beats)
            if new_flow:
                note = self._sync_flow_edge(beat, old_flow, new_flow)
                if note:
                    self._log(f'[控制流] {note}')
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
    def _too_long_fields(self) -> list:
        """
        卡片上有哪些格超过 Excel 单元格上限（32767）？`[(字段, 字数), …]`

        **在输入端就拦**（2026-10-18，用户拍板"改回 excel 的单元格上限"）：
        等导出时才报错，用户已经在卡片里写了一万字、还以为是编辑器卡了。
        这里给的是同一份判据（`core.cell_limits`），只是更早、更近地说。
        """
        from storygraph.core.cell_limits import CELL_TEXT_LIMIT

        out = []
        for name, value in self._panel.collect_all_fields().items():
            if isinstance(value, str) and len(value) > CELL_TEXT_LIMIT:
                out.append((name, len(value)))
        return out

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
        flow_change = self._panel.flow_change()
        if flow_change:
            from storygraph.story.flow import is_valid_label
            if not is_valid_label(flow_change['target']):
                self._status('目标 label 不合法：只能字母/数字/下划线，'
                             '可用点号分段；没有保存。')
                return None
            if flow_change['call_to_jump']:
                if QMessageBox.question(
                        self, '把「调用」改成「跳转」？',
                        f'目标：{flow_change["target"]}\n\n'
                        f'call 会执行目标 label，遇到 return 回到这里继续；\n'
                        f'jump 不会返回 —— 这一章会在跳转处结束。\n\n'
                        f'确定改成 jump 吗？'
                ) != QMessageBox.Yes:
                    self._status('已取消：控制流类型仍是 call。')
                    return None
        over = self._too_long_fields()
        if over:
            from storygraph.core.cell_limits import CELL_TEXT_LIMIT

            names = '、'.join(f'「{n}」{k} 字' for n, k in over[:4])
            QMessageBox.warning(
                self, '这一格装不进 Excel',
                f'{names}\n\n'
                f'Excel 一个单元格最多 {CELL_TEXT_LIMIT} 字，超了会被静默截断，'
                f'所以工具不会替你存。\n'
                f'把这一句拆成几拍（比如按段落切成两张卡），再保存。')
            self._status(f'没有保存：{len(over)} 格超过单元格上限 '
                         f'{CELL_TEXT_LIMIT} 字')
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
        edge_note = (self._sync_flow_edge(res['beat'], flow_change['old'],
                                          flow_change['new'])
                     if flow_change else '')
        self._sync_flow_card(res['beat'], idx)
        self._panel.reset_baseline()
        self.refresh_list_row(idx)
        # 只读派生面板也要跟着变（当前舞台 / 继承提示 / 警告条）——
        # 用户 2026-10-05："我已经保存该卡片了，可是当前舞台变化没有生效"。
        self.refresh_derived_panels(idx)
        self._sync_undo_button()
        self._status_bar.set_dirty(True, self._file_path)

        if res['changed']:
            self._status(f'✔ 已保存到这一拍（{res["line_id"]}）。'
                         + (f'{edge_note} ' if edge_note else '')
                         + f'改动在内存里，点「⤓ 导出 .rpy」时才落盘。')
            if flow_change:
                self._log(
                    f'[控制流] {flow_change["old"]["type"]} '
                    f'{flow_change["old"]["target"]} → '
                    f'{flow_change["type"]} {flow_change["target"]}'
                    + (f'；{edge_note}' if edge_note else '；图上边已同步'))
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
