"""
storygraph.ui_qt.window_export.py
=================================
`MainWindowQt` 的**导出 / 体检 / 舞台预览 / 剧本表 —— 与外部世界打交道的那一批。**

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


__all__ = ['WindowExportMixin', 'overwritable']


def overwritable(res: dict) -> bool:
    """
    这次 block **可以**让用户选择"仍然覆盖"吗？

    只有一条可以：`HANDWRITTEN_TARGET`（产物没问题，只是目标是人手写的）。
    其余 block（`PYTHON_SYNTAX_ERROR` / `CJK_IN_IMAGE_TAG` /
    `LABEL_COLLISION` …）是**产物本身坏了** —— 覆盖只会把坏脚本写进
    `game/`，让游戏起不来。那几条一律不给逃生舱。

    判据放在这里而不是写死在对话框里：`codes` 是 `EditorSession.export`
    的公开契约，将来新增 block 代码时，"能不能覆盖"必须是个明确决定，
    不能靠默认放行。

    ⚠ **为什么从这个模块搬进来**（2026-10-05 用户实测报的"导出没反应"）：
    它原来住在 `main_window.py`，而 P3-14 那次拆分把 `export_script`
    搬到了本模块 —— 自由函数**不在** `REQUIRES` 契约里（那个守卫只静态扫
    `self.*`），于是本模块引用了一个自己看不见的名字：

        NameError: name '_overwritable' is not defined

    触发条件恰好是"导出被拦下"这一条路（`if not res.get('ok') and
    _overwritable(res)`）—— 而测试只直接调过那个函数，从没走这条分支。
    症状是最糟的那种：**点了导出什么都不发生**（没有对话框、没有文件、
    状态栏也不说话）。
    """
    return 'HANDWRITTEN_TARGET' in (res.get('codes') or [])


class WindowExportMixin:
    """
    导出 / 体检 / 舞台预览 / 剧本表 —— 与外部世界打交道的那一批。

    它要求宿主（`MainWindowQt`）提供 `REQUIRES` 里那些成员 ——
    多半是**主类刻意留下**的：界面骨架、页切换、状态栏、
    静默失败上报、图的所有权。
    """

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    REQUIRES = ('_canvas', '_log', '_project_root', '_registry_provider', '_report_swallowed', '_session', '_status', '_status_bar', '_swallow_mark', '_table_mtimes', 'apply_current_beat', 'graph', 'resync_after_export')

    def export_script(self):
        """导出这一章（编译 + 增量补丁 + 安全门禁，全在会话里）。"""
        if not len(self._session):
            self._status('没有可导出的内容。')
            return
        mark = self._swallow_mark()
        self.apply_current_beat()
        try:
            self._session.compile()
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '编译失败', str(e))
            self._report_swallowed(mark)
            return

        default = self._session.default_output_path()
        try:
            default.parent.mkdir(parents=True, exist_ok=True)
        except Exception:                       # noqa: BLE001
            swallow('main_window.export_script')
        path, _ = QFileDialog.getSaveFileName(self, '导出 .rpy',
                                              str(default), "Ren'Py (*.rpy)")
        if not path:
            return
        try:
            res = self._session.export(path)
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '导出失败', str(e))
            self._report_swallowed(mark)
            return
        if not res.get('ok') and overwritable(res):
            # **手写文件才给逃生舱**（方案 3，2026-10-05）。
            #
            # 为什么单独这一条：其余 block（图像名非法 / label 重名 /
            # Python 语法错误）是**产物本身坏了**，覆盖只会把坏脚本
            # 写进 game/，所以那些一律不给 force。
            # 而"目标是你手写的文件"性质不同 —— 产物没问题，
            # 只是工具不敢替你决定要不要丢掉 `default` / `init python`。
            #
            # 默认按钮是**取消**（`QMessageBox.Cancel` 作 default），
            # 因为这一刀不可逆。
            res = self._confirm_force_overwrite(
                path, used_patch=bool(res.get('used_patch'))) or res
        if not res.get('ok'):
            blocking = res.get('blocking') or ['（未知原因）']
            QMessageBox.warning(self, '生成被门禁拦截', '\n'.join(blocking))
            self._report_swallowed(mark)
            return
        self._status_bar.set_dirty(False, str(res.get('path') or path))
        # **原地导出之后内存会重新对齐到磁盘**（见
        # `session_export._reload_after_inplace_write`）：拍的粒度可能变
        # （只有注释/代码的卡会并进后面那张）→ 卡片列表要跟着刷新，
        # 否则界面还显示旧的拍、点哪张都对不上。
        self.resync_after_export(res)
        todos = res.get('todos') or 0
        warn = f'，缺资源 {todos} 处（见 TODO）' if todos else ''
        self._log(f'[导出] 已写入 {Path(path).name}'
                  f'（{res.get("note", "")}{warn}）')
        self._status(f'✔ 已导出 {Path(path).name}（{res.get("note", "")}{warn}）')
        self._report_swallowed(mark)
    def _confirm_force_overwrite(self, path, used_patch=False):
        """
        「目标是你手写的文件，真的要覆盖吗？」—— 用户点"覆盖"才返回结果。

        **必须说清这一次到底会丢什么**（2026-10-05 实测修正）：

          · `used_patch=True`（增量对上了）→ **什么都不会丢**：产物是在原文上
            合并出来的（只换掉你改过的那几行），`default` / `init python` /
            `transform` 逐字保留。唯一的变化是多一行生成标记。
            旧文案在这里说"这些内容会全部消失"是**假话**，而它恰好出现在
            最常见的那条路上（改自己章节里的一句台词、原地导出）。
          · `used_patch=False`（退回全量重生成）→ 旧文案才是真的，照原样说。

        默认按钮仍然是**取消**：目标终究是工具"不拥有"的手写文件，
        "默认安全、明确覆盖"是用户定过的口径（P0 文档 §11），这里不改它，
        只把话说准。
        """
        name = Path(path).name
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle('覆盖手写文件？')
        box.setText(f'{name} 看起来是**手写**的 .rpy（没有生成标记）。')
        if used_patch:
            box.setInformativeText(
                '但**这一次是增量**：产物是在原文上合并出来的，只替换你改过的'
                '那几行 ——\n`default` / `define` / `init python` / `transform`'
                ' / `screen` 这些不进表的内容**逐字保留**，不会丢。\n\n'
                '唯一的区别：写回去之后首行会多一条生成标记（工具据此认出'
                '"这是我写的文件"，以后不再拦你）。\n\n'
                '想更稳一点：先导出到 build/renpy/ 下对比一眼再覆盖。')
            box.button_text = '写入（增量，不会丢东西）'
        else:
            box.setInformativeText(
                '继续导出会用"全量生成"重写它，而全量只认得剧本表里的拍 ——\n'
                '`default` / `define` / `init python` / `transform` / `screen`\n'
                '这些**不进表**的内容会全部消失，游戏可能起不来。\n\n'
                '建议：改导出到 build/renpy/ 下的新路径，或先备份。')
            box.button_text = '仍然覆盖'
        box.setStandardButtons(QMessageBox.Cancel | QMessageBox.Yes)
        box.setDefaultButton(QMessageBox.Cancel)
        box.button(QMessageBox.Yes).setText(box.button_text)
        box.button(QMessageBox.Cancel).setText('取消（推荐）')
        if box.exec() != QMessageBox.Yes:
            self._status('已取消导出（目标是你手写的文件）。')
            return None
        try:
            return self._session.export(path, force=True)
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '导出失败', str(e))
            return None
    def open_node_in_excel(self, nid=None):
        """
        右键章节模块 → 用它对应的 .rpy 生成演出表（同名 .xlsx）并打开。

        这是"用表格编演出"的入口：表里一行一句台词，立绘/背景/音乐/
        变换都是下拉框，候选直接来自资产表。

        **逻辑全在 `app.tables`**（那是从 tkinter 的 141 行 UI 方法里
        搬出来的，语义一条没改），这里只挑路径 + 展示结果。
        """
        from storygraph.app import tables
        from storygraph.graph.editor_utils import open_with_system_default

        nid = nid if nid is not None else self._canvas.selected_node_id()
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        script = getattr(node, 'file_path', None) if node else None
        if not script:
            script, _ = QFileDialog.getOpenFileName(
                self, '选择要生成演出表的 .rpy',
                str(Path(self._project_root or '.') / 'game'),
                "Ren'Py 脚本 (*.rpy)")
            if not script:
                return

        res = tables.generate_table(script, registry=self._registry_provider(),
                                    project_root=self._project_root)
        if not res['ok']:
            QMessageBox.critical(self, '生成剧本表失败', res['error'])
            return
        table_path = res['table_path']
        tables.remember_table_mtime(self._table_mtimes, table_path)

        if not open_with_system_default(table_path):
            QMessageBox.information(
                self, '已生成剧本表',
                f'未能自动调用 Excel，请手动打开：\n{table_path}')
        self._status(f'✔ 已生成 {table_path.name}'
                     f'（改完保存，再用「⤒ 从剧本表写回 .rpy」）')
    def export_from_table(self):
        """
        选定一张剧本表 → 写回 .rpy（导入 + 增量补丁 + 安全门禁）。

        顺序刻意是**先算、再问、最后写** —— 旧实现就是这么做的：
        不在用户点头前落任何盘，被门禁拦下时目标文件一个字节都不动。
        """
        from storygraph.app import tables

        start = str(Path(self._project_root or '.') / 'game')
        path, _ = QFileDialog.getOpenFileName(
            self, '选择剧本表（.xlsx）', start,
            'Excel 工作簿 (*.xlsx);;所有文件 (*)')
        if not path:
            return
        table_path = Path(path)

        # 这张表被外部改过（另存/换机器/Excel 保存）→ 先自动刷一次候选，
        # 让人不必记得点「刷新候选」。失败只提示，不打断导出。
        if tables.table_changed_since(self._table_mtimes, table_path):
            self._refresh_table_candidates(table_path)
            tables.remember_table_mtime(self._table_mtimes, table_path)

        try:
            res = tables.export_table(
                table_path, registry=self._registry_provider(),
                project_root=self._project_root,
                source_rpy=self._source_rpy_for_table(table_path))
        except Exception as e:                    # noqa: BLE001
            QMessageBox.critical(self, '读取剧本表失败', str(e))
            return

        if res.get('error'):
            QMessageBox.warning(self, '无法导出', res['error'])
            return

        if res['unregistered']:
            self._show_unregistered(res['unregistered'])

        if res['blocking']:
            # 拒写，但产物已另存一份供对比（`app.tables` 负责写副本）
            review = res.get('review_path')
            QMessageBox.critical(
                self, '拒绝导出：生成的脚本会让游戏跑不起来',
                f'发现 {res["blocking_total"]} 条致命问题，**没有写入目标文件**'
                f'（原文与演出表都没动）。\n\n'
                + '\n'.join('· ' + m for m in res['blocking'])
                + (f'\n\n产物已另存一份供对比：\n{review}' if review
                   else '\n\n（产物副本也没能写出，请检查目录权限）'))
            self._status(f'⛔ 导出被拦下（{res["blocking_total"]} 条致命问题）')
            return

        tip = (f'将生成 {res["beats"]} 个演出单元到：\n{res["out_path"]}\n')
        tip += f'\n{res["note"]}\n' if res.get('note') else ''
        if res['warnings']:
            tip += ('\n提醒（不阻止导出）：\n'
                    + '\n'.join('· ' + m for m in res['warnings']) + '\n')
        else:
            tip += '\n生成安全门禁：无问题。\n'
        tip += '\n继续？'
        if QMessageBox.question(self, '确认导出', tip) != QMessageBox.Yes:
            return

        done = tables.apply_export(res, project_root=self._project_root)
        if not done.get('ok'):
            QMessageBox.critical(
                self, '导出失败',
                done.get('error') or f'写不出去：\n{done.get("out_path")}')
            return
        self._log(f'[剧本表] 已生成脚本 {Path(done["out_path"]).name}'
                  f'（{done["beats"]} 个演出单元）')
        self._status(f'✔ 已由剧本表写回 {Path(done["out_path"]).name}'
                     f'（{done["beats"]} 个演出单元）')
    def _source_rpy_for_table(self, table_path):
        """
        增量补丁的底稿 .rpy。

        优先用**画布上当前章节**的源文件（最准：表可能被另存到别处）；
        其次让 `app.tables` 退回"表旁边同名 .rpy"。
        """
        nid = self._canvas.selected_node_id()
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        fp = getattr(node, 'file_path', None) if node else None
        if fp and Path(fp).exists():
            return Path(fp)
        return None
    def _refresh_table_candidates(self, table_path):
        """
        资源表变了 → 把这张演出表的候选下拉刷一遍，不动 Beats 里的用户数据。

        失败只提示、不打断：刷候选是辅助动作，不该拦住导出主流程。
        """
        registry = self._registry_provider()
        if registry is None:
            return
        try:
            from storygraph.story.excel_export import refresh_candidate_sheets
            refresh_candidate_sheets(table_path, registry)
            self._status(f'已刷新 {Path(table_path).name} 的下拉候选。')
        except Exception as e:                    # noqa: BLE001
            self._status(f'刷新候选失败（不影响导出）：{e}')
    def _show_unregistered(self, unregistered):
        """演出表里引用了、但资源表未登记的资产 —— 提示文案去登记。"""
        from storygraph.ui_qt.dialogs import AnalysisDialog
        lines = [f'{cat}：{aid}' for cat, aid in list(unregistered)[:80]]
        more = (f'\n… 还有 {len(unregistered) - 80} 条'
                if len(unregistered) > 80 else '')
        AnalysisDialog(
            self, '演出表里引用了、但资源表未登记的资产',
            f'共 {len(unregistered)} 条。它们**不影响导出**，'
            f'但建议补进资源表（否则下拉里选不到）：\n\n'
            + '\n'.join(lines) + more).exec()
    def show_health(self):
        if not self._project_root:
            self._status('先打开一个项目。')
            return
        try:
            r = self._session.health(limit=30)
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '体检失败', str(e))
            return
        if r.get('error'):
            self._status(r['error'])
            return
        lines = [f'项目：{self._project_root}',
                 f'问题 {r["total"]} 条（前 {len(r["issues"])}）', '']
        lines += [f'  · {m}' for m in r['issues']]
        self._log(f'[体检] 问题 {r["total"]} 条'
                  + (f'：{r["issues"][0][:80]}' if r['issues'] else '（干净）'))
        QMessageBox.information(self, '项目体检', '\n'.join(lines))
    def open_stage_preview(self):
        if not len(self._session):
            self._status('先载入一章。')
            return
        self.apply_current_beat()
        try:
            from storygraph.ui_qt.stage_preview import open_preview
            open_preview(self, self._session.frames(),
                         registry=self._registry_provider(),
                         project_root=self._project_root)
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '无法打开预览', str(e))
    def show_help(self):
        from storygraph.ui_qt.help_dialog import show_help
        return show_help(self)
