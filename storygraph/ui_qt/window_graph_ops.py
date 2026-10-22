"""
storygraph.ui_qt.window_graph_ops.py
====================================
`MainWindowQt` 的**剧情图那一半：结构分析 / 章节导入导出 / 布局存取 / 图级撤销。**

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

import time
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.ui_qt.qt_args import drop_qt_bool
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
from storygraph.ui_qt.flow_notes import skipped_targets
from storygraph.ui_qt.graph_canvas import GraphCanvas
from storygraph.ui_qt.sprite_grid import SpriteGridWidget
from storygraph.ui_qt.status import Notifier, Toast
from storygraph.ui_qt.thumb_loader import ThumbLoader


def format_character_ranking(rows) -> list:
    """
    【角色台词量】的行 → 人话。

    数据源（`character_script_linker`）给的是
    `{'variable_name','display_name','dialogue_count','label_count'}`，
    而旧代码读的是 `name/character/count/lines` —— 键全不存在，
    每一行都显示 `None：None`（2026-10-18 审计 P1-4）。
    """
    out = []
    for row in (rows or ()):
        if not isinstance(row, dict):
            out.append(f'  {row}')
            continue
        name = str(row.get('display_name') or row.get('name')
                   or row.get('character') or row.get('variable_name') or '?')
        var = str(row.get('variable_name') or row.get('character') or '')
        count = row.get('dialogue_count')
        if count is None:
            count = row.get('count')
        if count is None:
            count = row.get('lines', 0)
        shown = f'{name}（{var}）' if var and var != name else name
        out.append(f'  {shown}：{count}')
    return out


__all__ = ['GraphOpsMixin']


class GraphOpsMixin:
    """
    剧情图那一半：结构分析 / 章节导入导出 / 布局存取 / 图级撤销。

    它要求宿主（`MainWindowQt`）提供 `REQUIRES` 里那些成员 ——
    多半是**主类刻意留下**的：界面骨架、页切换、状态栏、
    静默失败上报、图的所有权。
    """

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    REQUIRES = ('ASSET_FIELD_KINDS', 'PAGE_GRAPH', '_base_title', '_canvas', '_console', '_diag_items', '_ensure_registry', '_file_path', '_graph_dirty', '_graph_saved_sig', '_history', '_last_layout_path', '_loader', '_log', '_panel', '_project_root', '_record_applied', '_refresh_asset_choices', '_registry_provider', '_session', '_set_graph', '_set_page', '_show_index', '_status', '_status_bar', 'adopt_loaded_session', 'apply_current_beat', 'clear_console', 'engine', 'graph', 'load_script', 'resync_after_export', 'set_assets_ready')

    def _show_analysis_text(self, text: str):
        """
        分析结论 → 控制台。

        旧版是"**先清空**再写入"（`analysis_text.delete(1.0, END)`），
        因为分析结果是"当前状态"而不是流水账；导入/项目那些是流水账。
        """
        try:
            self._console.setPlainText(str(text or ''))
            bar = self._console.verticalScrollBar()
            bar.setValue(0)
        except Exception:                    # noqa: BLE001
            swallow('main_window._show_analysis_text')
    def _edit_selected_vars(self):
        """右栏「🧩 编辑剧情变量…」：编辑当前选中模块的剧情变量。"""
        nid = self._canvas.selected_node_id()
        if nid is None:
            self._status('先在图上点一个模块，再编辑它的剧情变量。')
            return
        self.edit_node_ops(nid)
    def edit_node_ops(self, nid):
        """
        **双击模块 = 编辑剧情变量**（旧版 `on_double_click` →
        `edit_operations_dialog` 就是这个动作）。

        用户 2026-10-03：
        > 这个所谓的"技术代码"，它不是变量，是具体的实现细节！……
        > 我不要再在变量的窗口上看到任何内部技术代码的名字！编辑变量是为了
        > 让策划和编剧调整和编排剧情分支用的地方！不是拿来填细节的！！！

        所以对话框里**只有剧情变量**（`session.node_variables` 一条技术代码
        都不返回）；保存走 `session.set_node_variables`，技术代码原位保留。
        """
        from storygraph.ui_qt.dialogs import NodeOpsDialog

        info = self._session.node_variables(self.graph, nid)
        if not info.get('ok'):
            self._status('这个模块不在了（图可能刚被清空）。')
            return
        dlg = NodeOpsDialog(self, label_name=info['label'],
                            variables=info['variables'],
                            tech_count=info['tech_count'],
                            on_check=self._session.check_variable_syntax)
        if dlg.exec() != QDialog.Accepted:
            return
        before = list(getattr(self.graph.nodes.get(nid), 'operations', []) or [])
        # **必须同时改到拍上**：只改图节点的话，导出读的是 `session.beats`，
        # 用户在变量框里改的值一个字节都进不了 .rpy（P1-2 的真正缺口）。
        if info.get('file') and Path(str(info['file'])).exists():
            res = self._session.apply_node_variable_edits(
                self.graph, nid, dlg.variables)
        else:
            res = self._session.set_node_variables(
                self.graph, nid, dlg.variables)
        if not res.get('ok'):
            self._status(f'保存失败：{res.get("error")}')
            return
        # 变量改动也进历史（Ctrl+Z 能撤回来）。这里操作**已经生效**，
        # 所以只记历史、不重复 execute；改动前的文本用 `old_ops` 显式给，
        # 否则命令会把"撤销"恢复成新值。
        # **拍也要跟着退**：见 `_variable_ops_command`（2026-10-22 实测修）。
        try:
            cmd = self._variable_ops_command(nid, before, res, info['label'])
            self._record_applied(cmd)
        except Exception:                     # noqa: BLE001
            swallow('main_window.edit_node_ops')
        self._set_graph(self.graph)          # 摘要 / 连线可能变了
        if info.get('file') and self._session.script_path:
            # 变量已经写进拍 → 卡片页列表/面板跟上（见 `adopt_loaded_session`）
            try:
                self.adopt_loaded_session(info['label'])
            except Exception as e:            # noqa: BLE001 - 刷新失败不拦保存
                swallow('main_window.edit_node_ops.adopt', e)
        self._log(f'[变量] {info["label"]}：剧情变量 {res["variables"]} 条'
                  f'（另有 {res["kept_tech"]} 条实现细节原样保留）'
                  + (f'；{res["unplaced"]} 条没对上拍，导出不会写'
                     if res.get('unplaced') else ''))
        self._status(f'已保存「{info["label"]}」的剧情变量'
                     f'（{res["variables"]} 条，已写进这一章；'
                     f'点「⤓ 导出 .rpy」落盘）。')
    def _variable_ops_command(self, nid, before_ops, res, label=None):
        """
        变量改动进图历史的命令：**节点 + 拍一起撤销/重做**（2026-10-22 修）。

        为什么不能只用原来的 `UpdateNodeOperationsCommand`：它只还原
        `node.statements`，而变量工作台/变量框把同一份改动**也写进了
        `session.beats`**。于是 Ctrl+Z 之后工作台说"这条没了"、
        `compile()` 里却还在 —— 撤销是假的，导出照样把那条写出去（实测）。

        `res['beat_snapshots']` 是会话在动手**之前**留给我们的逐拍快照；
        `after` 在这里现拍（改动已生效）。撤销退 `before`、重做进 `after`，
        两边都走 `session.restore()`，拍对象身份不变（卡片页 / 章节树还指得对）。
        """
        from storygraph.graph.commands import UpdateNodeOperationsCommand

        before = dict(res.get('beat_snapshots') or {})
        after = {}
        for idx in before:
            snap = self._session.snapshot(int(idx))
            if snap is not None:
                after[int(idx)] = snap
        refresh = None
        if label:
            def refresh():                      # noqa: F811 - 有意覆盖成回调
                try:
                    self.adopt_loaded_session(label)
                except Exception as e:          # noqa: BLE001 - 刷新失败不拦撤销
                    swallow('main_window._variable_ops_command.refresh', e)
        return UpdateNodeOperationsCommand(
            self, nid, list(self.graph.nodes[nid].operations),
            old_ops=before_ops,
            beat_states={'session': self._session, 'before': before,
                         'after': after, 'refresh': refresh})

    def show_variable_workbench(self, nid=None):
        """
        「📋 变量工作台…」：变量清单 + 初值 + 每条赋值落在第几拍。

        与 `edit_node_ops` 的分工：老对话框是"整章一段 Python"，
        工作台是**按变量/按条**编辑，并且能定位到拍（P1-2）。
        两者都只改图上的节点；写盘仍走「⤓ 导出 .rpy」。
        """
        from storygraph.ui_qt.dialogs import VariableWorkbenchDialog

        if nid is None:
            nid = self._canvas.selected_node_id()
        if nid is None:
            self._status('先在图上点一个模块，再打开变量工作台。')
            return
        info = self._session.variable_workbench(self.graph, nid)
        if not info.get('ok'):
            self._status(info.get('error') or '这个模块不在了。')
            return
        dlg = VariableWorkbenchDialog(
            self, label_name=info['label'], data=info,
            on_check=self._session.check_variable_syntax)
        if dlg.exec() != QDialog.Accepted:
            return
        changed = list(dlg.variables) != list(info.get('operations') or ())
        if changed:
            before = list(getattr(self.graph.nodes.get(nid), 'operations', [])
                          or [])
            path = str(info.get('file') or '')
            if path and Path(path).exists():
                res = self._session.apply_node_variable_edits(
                    self.graph, nid, dlg.variables,
                    insert_beat=dlg.insert_beat,
                    # 新增的每条各自带拍号：一次往 if / for 两个块里插入时，
                    # 只传一个 insert_beat 会让它们全挤到同一拍。
                    anchors=getattr(dlg, 'anchors', None))
            else:
                res = self._session.set_node_variables(
                    self.graph, nid, dlg.variables)
            if not res.get('ok'):
                self._status(f'保存失败：{res.get("error")}')
                return
            try:
                cmd = self._variable_ops_command(nid, before, res,
                                                 info['label'])
                self._record_applied(cmd)
            except Exception:                 # noqa: BLE001
                swallow('main_window.show_variable_workbench.history')
            self._set_graph(self.graph)
            # 会话里的拍已经被改过 → 卡片页列表/面板要跟上（不从磁盘重读，
            # 否则刚写进拍的变量会被旧文件盖回去）。
            if path and self._session.script_path == Path(path):
                self.adopt_loaded_session(info['label'])
            self._log(f'[变量工作台] {info["label"]}：剧情变量 '
                      f'{res["variables"]} 条（技术代码 {res["kept_tech"]} 条原位保留）'
                      + self._inserted_note(res)
                      + (f'；{res["unplaced"]} 条没对上拍，导出不会写'
                         if res.get('unplaced') else ''))
            self._status(f'已保存「{info["label"]}」的 {res["variables"]} 条剧情变量；'
                         f'已写进这一章的拍，点「⤓ 导出 .rpy」落盘。')
        if dlg.jump_index is not None:
            path = str(info.get('file') or '')
            if not path or not Path(path).exists():
                self._status('这个模块没有可打开的源文件。')
                return
            if not changed:
                self.load_script(path, goto_card=True, label=info['label'])
            else:
                self.adopt_loaded_session(info['label'],
                                          index=int(dlg.jump_index),
                                          goto_card=True)
            self._show_index(int(dlg.jump_index))
            self._status(f'已跳到第 {int(dlg.jump_index) + 1} 拍。')

    @staticmethod
    def _inserted_note(res: dict) -> str:
        """新增的变量操作落在第几拍（工作台保存后写日志用）。"""
        rows = []
        for value in (res.get('inserted') or ()):
            try:
                n = int(value)
            except (TypeError, ValueError):
                continue
            if n >= 0:
                rows.append(n + 1)
        if not rows:
            return ''
        return ('；新增 ' + '、'.join(f'第 {n} 拍' for n in rows[:4])
                + ('…' if len(rows) > 4 else ''))

    def _write_analysis(self, text: str):
        """分析结论写进控制台（旧版也是"先清空再写"）。"""
        self._show_analysis_text(text)
    def _run_analysis(self) -> dict:
        if self.graph is None or not getattr(self.graph, 'nodes', None):
            self._status('剧情图还是空的 —— 先「➕ 导入章节 .rpy」加几章再分析。')
            return {}
        try:
            return self._session.analyze_graph_problems(self.graph)
        except Exception as e:               # noqa: BLE001
            self._status(f'分析失败：{e}')
            return {}
    def check_logic(self):
        """
        「🔎 检查逻辑问题」：只列问题 + 在图上标红/打虚线。

        以前这是一个**弹窗**（看完得点确定，还没法对照图）；现在结论写进
        右上控制台，且**画回图上** —— 静态分析的价值就在于"指出哪个模块有问题"，
        光给一段文字等于让人自己去几十个块里找。
        """
        res = self._run_analysis()
        if not res:
            return
        self._canvas.set_analysis(entry_id=res.get('entry_id'),
                                  problem_ids=res.get('problem_ids') or [],
                                  independent_ids=res.get('independent_ids') or [])
        warnings = [str(w) for w in res.get('warnings') or []]
        notes = [str(w) for w in res.get('notes') or []]
        lines = ['[检查逻辑问题] ' + str(res.get('summary', '')), '']
        if not warnings:
            lines.append('✔ 没有发现"从任何入口都走不到"的模块'
                         '（分支条件与入口判定都在合理范围内）。')
        else:
            lines.append('问题清单（图上已标红）：')
            lines.append('')
            lines += warnings
        if notes:
            lines.append('')
            lines.append(f'另有 {len(notes)} 个模块只是"没有入边 / '
                         f'从本入口走不到"，但从别的入口可达 —— '
                         f'多为独立章节、被菜单或 `call screen` 进入，'
                         f'图上打灰虚线，**不算问题**：')
            lines += notes[:20]
            if len(notes) > 20:
                lines.append(f'… 还有 {len(notes) - 20} 个')
        self._write_analysis('\n'.join(lines))
        n = len(res.get('problem_ids') or [])
        self._status('✔ 检查完成：' + (f'{n} 个模块真断了（已标红）'
                                      if n else '没有发现结构断裂'))
    def show_analysis(self):
        """「📊 变量分析」：逐节点区间明细（同样画回图上）。"""
        res = self._run_analysis()
        if not res:
            return
        self._canvas.set_analysis(entry_id=res.get('entry_id'),
                                  problem_ids=res.get('problem_ids') or [],
                                  independent_ids=res.get('independent_ids') or [])
        self._write_analysis(res.get('text', ''))
        self._status('✔ 分析完成（只读，没有改动任何东西）')

    def show_analysis_dialog(self):
        """
        「📊 变量分析（可过滤）…」：同样的结果，但给一个能按键字过滤的窗口。

        用户 2026-10-22："`变量分析` 输出的是原始文本，不是可操作的变量表。"
        控制台适合看流水，找"某个变量出现在哪"需要的是过滤框。
        """
        res = self._run_analysis()
        if not res:
            return
        self._canvas.set_analysis(entry_id=res.get('entry_id'),
                                  problem_ids=res.get('problem_ids') or [],
                                  independent_ids=res.get('independent_ids') or [])
        from storygraph.ui_qt.dialogs import AnalysisDialog
        dlg = AnalysisDialog(self, title='变量分析（可按变量过滤）',
                             text=res.get('text') or '')
        dlg.exec()
        self._status('✔ 分析完成（只读，没有改动任何东西）')
    def clear_analysis(self):
        """清掉图上的分析标记与控制台里的结论（回到"未分析"的样子）。"""
        self.clear_console()
        self._canvas.clear_analysis()
        self._status('已清空分析结论与图上的标记。')
    def open_project(self):
        """选项目根 → 只加载资源表（xlsx）。**不往剧情图里放任何东西。**"""
        start = str(self._project_root or Path.cwd())
        root = QFileDialog.getExistingDirectory(
            self, '选择项目根目录（含 game/ 与资源表）', start)
        if not root:
            return
        self.open_project_path(root)
    def open_project_path(self, root):
        """
        打开项目 = **只为资源管理**（用户 2026-10-03 明确）。

        > 所谓的导入项目只是为了表格的 xlsx 的资源管理系统！
        > 不是把整个项目都排在剧情图上面！！！

        所以这里只做三件事：记住项目根、读资源表、把符号表（立绘/音乐/
        角色候选）刷出来。**图一个节点都不加** —— 想让哪一章出现在剧情图上，
        用「➕ 导入章节 .rpy」。
        """
        if not root:
            return None
        self._project_root = Path(root)
        self._session.project_root = self._project_root
        self._loader.project_root = self._project_root
        # 引擎壳（`_EngineShim`）也有 `project_root`；顺手同步一下，
        # 免得它按老路径懒加载时拿不到项目（那是"添加立绘没反应"的另一半）。
        if getattr(self.engine, 'project_root', None) is None:
            try:
                self.engine.project_root = self._project_root
            except Exception:                 # noqa: BLE001
                swallow('main_window.open_project_path')
        # **先把"正在读资源表"画出来再干活**（用户 2026-10-05："给程序干未响应了"）。
        #
        # 读表 + 扫盘 + 对账是**同步**跑的（真实项目优化后 ~1.8 秒，优化前 20 秒
        # 以上）；不先让事件循环跑一圈的话，窗口在这一段里就是"未响应"的样子。
        # 这里只做两件便宜的事：写状态栏 + `processEvents()` 让系统把窗口重绘。
        try:
            self._status(f'正在读资源表：{self._project_root} …')
            app = QApplication.instance()
            if app is not None:
                app.processEvents()
        except Exception as e:                  # noqa: BLE001 - 画不出来也不该挡打开
            swallow('main_window.open_project_path.status', e)
        _t0 = time.perf_counter()
        res = self._session.load_assets()
        _dt = time.perf_counter() - _t0
        if _dt >= 0.5:                          # 慢就把耗时说出来（可诊断）
            self._log(f'[资源] 读取 + 对齐资源表用了 {_dt:.1f} 秒'
                      f'（扫描一次目录 + 读表 + 对账）')
        try:
            self._panel.set_symbols(self._session.symbols)
            self._refresh_asset_choices()
        except Exception:                       # noqa: BLE001
            swallow('main_window.open_project_path')
        # ⚠ **打开项目之后才判得了"这张立绘图在不在"**（2026-10-13）：
        #   项目根定了，`game/images/...` 那条兜底猜路才走得通；在那之前
        #   每张立绘都只会是"还没解析"，那时报"没有"就是假警报。
        #   所以**打开项目本身**（不管资源表读不读得到）就把这个事实说一声 ——
        #   用户要的是"文件在不在"，不是"资源表全不全"。
        try:
            self.set_assets_ready(True)
        except Exception as e:                  # noqa: BLE001 - 说不了也不该拦打开
            swallow('main_window.open_project_path.assets_ready', e)
        if not res.get('ok'):
            msg = (f'[项目] 读不到资源表：{res.get("error") or "未知原因"}'
                   f'（剧情图不受影响；下拉候选会是空的）')
            self._log(msg)
            self._status(msg)
            return res
        c = res['counts']
        # **"没有表 → 我当场给你建了一张"必须说出来**（用户 2026-10-05 实测报：
        # 复制出来的项目里 `game/asset_tables/` 是空目录，从前只报一句
        # "读不到资源表（xlsx）"，用户不知道该怎么办）。建表是**写用户的项目**，
        # 所以路径也要给出来 —— 他随时能去改。
        if res.get('repaired') or res.get('repair_error'):
            # **表在、但读不出来** → 已挪到一边 + 重建。用户 2026-10-05 实测报的
            # "读不到资源表…自动新建也失败了：未知原因" 就是这一支：
            # 真相是文件被 Excel 锁着/写坏了，而旧代码只说"找不到 + 未知原因"。
            self._log(
                f'[资源] ⚠ 资源表读不出来（{res.get("repair_error") or "未知原因"}）'
                f'→ 已把坏文件**改名留档**：{"、".join(res["repaired"])}'
                f'\n        （没删！想救回去就把它改名回 `images.xlsx`；'
                f'也可以从 `*.bak.xlsx` 恢复）'
                f'；并按项目内容重建了一张表。')
        if res.get('created'):
            cc = res.get('created_counts') or {}
            self._log(
                f'[项目] 没找到资源表 → 已按项目内容**新建**：'
                f'图片 {cc.get("images", 0)} · 音乐 {cc.get("music", 0)} · '
                f'音效 {cc.get("sounds", 0)} · 语音 {cc.get("voices", 0)}\n'
                f'        写入：{res["created"]}'
                f'（角色 / 变换来自脚本声明，改完表用「重读资源表」）')
        elif res.get('synced') or res.get('removed'):
            # **表已存在 → 自动对齐**（用户 2026-10-05 的目标：
            # "若表已经存在，自动更新表中的资源，确保表与资源自动一一对应"、
            # 以及后来补的那半句"我认为表应当实时反应实际资源的情况，
            # 自动更新和增删！"）
            bits = []
            if res.get('synced'):
                kinds = ' · '.join(f'{k} {v}'
                                   for k, v in (res.get('synced_by_kind') or {})
                                   .items() if v)
                bits.append(f'补登 {res["synced"]} 条（{kinds}）')
            if res.get('removed'):
                kinds = ' · '.join(f'{k} {v}'
                                   for k, v in (res.get('removed_by_kind') or {})
                                   .items() if v)
                bits.append(f'**自动删除 {res["removed"]} 条**'
                            f'（文件已经不在：{kinds}）')
            if res.get('relinked'):
                kinds = ' · '.join(f'{k} {v}'
                                   for k, v in (res.get('relinked_by_kind')
                                                or {}).items() if v)
                bits.append(f'**改指 {res["relinked"]} 条**'
                            f'（原来指向另一份副本，现在指向本项目：{kinds}）')
            if res.get('deduped'):
                kinds = ' · '.join(f'{k} {v}'
                                   for k, v in (res.get('deduped_by_kind')
                                                or {}).items() if v)
                bits.append(f'合并重复行 {res["deduped"]} 条（{kinds}）')
            self._log(
                f'[资源] 已按项目实际情况更新资源表：' + '；'.join(bits)
                + (f'；写前备份 {res["sync_backup"]}'
                   if res.get('sync_backup') else '')
                + f'\n        表：{self._project_root}/game/asset_tables/'
                  f'（表跟资源**一一对应**：盘上有的补进来、文件没了的删掉；'
                  f'想留一行"还没出图"的资源就在它的 Notes 里写「占位」）')
        # **删不掉的**（最典型：表正开在 Excel 里被锁着）才会留在这儿，
        # 那时才需要说"已标为不可用"。用户 2026-10-05：
        # > 如果不可用直接删了就行！
        bad = res.get('unusable') or {}
        if bad:
            detail = ' · '.join(f'{k} {v}' for k, v in bad.items() if v)
            self._log(
                f'[资源] ⚠ 有 {sum(bad.values())} 条指向的文件**已经不在**、'
                f'但**没能从表里删掉**（{detail}）—— 多半是这张表正开在 Excel 里'
                f'（锁住了）。\n'
                f'        在那之前它们被临时标为**不可用**（不进候选、解析不到）；'
                f'关掉 Excel 再打开一次项目就会删掉。'
                f'要留着当占位：在那一行 `Notes` 里写「占位」。')
        # 旧版的这一行原样搬回来（用户 2026-10-03："导入的时候没有反馈"）：
        # 图片/音乐/音效/语音/变换/角色的数量要看得见。
        #
        # ⚠ **数字的来源要说清**（2026-10-05 用户实测报："为何两个克隆出来的
        # 项目并不一样？每次导入的时候你的表是不是没更新？？？"）：
        # 这一行读的是**资源表**（用户可编辑的数据，工具从不自动改写），
        # 而"没找到表 → 新建"那一次读的是**刚扫出来的磁盘内容**。
        # 不说来源，用户就只能对着两个不同的数字猜。
        src = ('刚按项目内容新建' if res.get('created') else '来自资源表')
        self._log(
            f'[项目] 已加载 {self._project_root.name}：'
            f'图片 {c["images"]} · 音乐 {c["music"]} · 音效 {c["sounds"]} · '
            f'语音 {c["voices"]} · 变换 {c["transforms"]} · 角色 {c["characters"]}'
            f'（数字{src}；只影响资源表，剧情图不受影响）')
        self._report_asset_drift()
        self._status(
            (f'✔ 已打开项目 {self._project_root.name}'
             + ('（资源表是**刚新建**的）' if res.get('created') else '')
             + f' —— 图片 {c["images"]} · 音乐 {c["music"]} · '
               f'音效 {c["sounds"]} · 语音 {c["voices"]} · '
               f'角色 {c["characters"]}；'
               f'剧情图**不受影响**，要加章节请用「➕ 导入章节 .rpy」'))
        return res

    def check_asset_health(self):
        """
        「📦 资源体检」：查一遍**资源完整性**并出示报告（用户 2026-10-05）。

        > 在「变量分析」右边加一个检查资源完整性的功能，后出示检查报告

        报告本身复用变量分析那个只读对话框（可过滤、可复制）。
        """
        from storygraph.app.asset_report import format_asset_health
        from storygraph.ui_qt.dialogs import AnalysisDialog
        try:
            report = self._session.asset_health_report()
        except Exception as e:                    # noqa: BLE001 - 体检失败别崩
            swallow('ui_qt.window_graph_ops.check_asset_health', e)
            report = {'ok': False, 'error': str(e), 'problems': 0}
        # 剧本里"引擎不认的属性"汇总（路线图 §8.9 的"报出来"）。
        # ⚠ 语义在 app 层（`unknown_attrs_by_tag`，与卡片那条提示同源），
        #    这里只是把**已载入的拍 + 已采到的事实**递给它 —— 前端不拼结论。
        try:
            from storygraph.app.beats import unknown_attrs_by_tag
            facts = self._session.engine_facts()
            beats = list(getattr(self._session, 'beats', None) or ())
            if facts and report.get('ok'):
                report['engine_unknown'] = unknown_attrs_by_tag(beats, facts)
        except Exception as e:                    # noqa: BLE001 - 汇总失败不拦报告
            swallow('ui_qt.window_graph_ops.check_asset_health.unknown', e)
        text = format_asset_health(report)
        head = text.splitlines()[0] if text else ''
        self._log(f'[资源] {head}')
        try:
            dlg = AnalysisDialog(self, title='资源体检报告', text=text)
            dlg.exec()
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.window_graph_ops.check_asset_health.dialog', e)
            self._status(head)
        return report

    def _report_asset_drift(self):
        """
        打开项目时对一次账：**表里没有、磁盘上却有**的资源要说出来。

        用户 2026-10-05：

        > 你确定这对吗？为何两个克隆出来的项目并不一样？
        > 每次导入的时候你的表是不是没更新？？？

        表的策略是"**用户的数据，工具不自动改写**"，所以漂移是正常的 ——
        不正常的是**漂移看不见**：下拉候选少几条、缩略图缺几张，用户只会
        以为工具坏了。这里把差值写进控制台并给出补救动作（就是既有的
        「补登缺失资源」，写前自动备份）。
        """
        reg = self._session.assets_registry()
        if reg is None or not self._project_root:
            return
        try:
            # ⚠ **复用打开时已经算过的那次对账**（`load_assets` 里做的）。
            #
            # 这里从前自己又调一次 `compare_registry_to_disk(..., 没有 walk)` ——
            # 用户 2026-10-05 报"给程序干未响应了"：`load_assets` 那次已经走索引
            # （~0.4 秒），这次却按**老路**逐行 `resolve_thumb_source` +
            # `Path.resolve()`，真实项目实测 **12 秒**，等于把优化全白做。
            diff = {}
            try:
                diff = self._session.last_reconcile() or {}
            except Exception as e:                # noqa: BLE001
                swallow('ui_qt.window_graph_ops._report_asset_drift.cached', e)
            if not diff:
                from storygraph.assets.asset_registry import (
                    FileWalkIndex, compare_registry_to_disk)
                diff = compare_registry_to_disk(
                    self._project_root, reg,
                    walk=FileWalkIndex(self._project_root))
        except Exception as e:                    # noqa: BLE001 - 对账失败不该挡打开
            swallow('ui_qt.window_graph_ops._report_asset_drift', e)
            return
        unreg = diff.get('unregistered') or {}
        total = sum(len(v) for v in unreg.values())
        stale = int(diff.get('stale_paths') or 0)
        outside = diff.get('outside_game') or {}
        n_out = int(outside.get('count') or 0)
        if not total and not stale and not n_out:
            return
        if n_out:
            # **这条最容易被误读**（用户 2026-10-05："为何两个克隆出来的项目
            # 并不一样？"）：表里的行指到本项目 `game/` 外面 —— 实测原项目
            # 有 1386 行落在 `sr_general/**`，那是**另一份嵌套副本**。
            # 不是"文件丢了"，而是"表引用的是别的目录"，必须分开说。
            tops = '、'.join(f'{k}/ {v} 行'
                             for k, v in sorted((outside.get('by_top') or {})
                                                .items(),
                                                key=lambda kv: -kv[1])[:3])
            self._log(
                f'[资源] ⚠ 表里有 {n_out} 行指向本项目 `game/` **之外**'
                f'（{tops}）—— 那多半是**另一份副本**，或者 `tmp/` 这类'
                f'临时目录（现在扫描时已经不看这些目录了）。\n'
                f'        这些行不会自动清（删行属于你的数据）——要收拾就'
                f'手动删掉它们，或改用**当前项目里**的那一份资源。')
        bits = []
        for key in ('images', 'music', 'sounds', 'voices'):
            n = len(unreg.get(key) or [])
            if n:
                bits.append(f'{key} {n}')
        if total:
            sample = next((p for v in unreg.values() if v for p in v), '')
            self._log(
                f'[资源] ⚠ 资源表与项目内容不一致：磁盘上还有 {total} 个没登记'
                f'（{" · ".join(bits)}）'
                + (f'\n        例：{sample}' if sample else '')
                + ' —— 用工具里的「补登缺失资源」补齐（写前自动备份 *.bak.xlsx）')
        if stale:
            self._log(f'[资源] ⚠ 表里有 {stale} 条指向的文件已经不在磁盘上了'
                      f'（不影响的先放着，删行请手动改表）')
    def rescan_project(self):
        """
        重读资源表（xlsx 改过之后用）。**剧情图的节点与连线一律不动** ——
        这是"资源系统"和"剧情图"两条路的边界。
        """
        if not self._project_root:
            QMessageBox.information(self, '提示', '先选择项目根目录。')
            return
        self.open_project_path(self._project_root)
    def import_chapters(self):
        """
        「➕ 导入章节」：**多选 → 累加进剧情图**。

        ⭐ S3（2026-10-11 补做，路线图 §七）：**纯文字也能直接导入** ——
        选 `.txt` 时先转成 `.rpy`（`authoring/txt_import.convert_text`），
        再走**同一条**既有链路（落盘 → 过门禁 → 加进图）。
        用户原话："纯文字 → 直接转换 + 导入，再也不用先手工转 .rpy"。

        ⚠ 两条不许违反：
          · **未映射的角色必须报出来**（不许静默取第一个 / 不许悄悄当旁白）；
          · 转换产物写到 `game/<label>.generated.rpy`（与项目既有约定同名，
            一眼能看出这是工具生成的）。
        """
        start = str(Path(self._project_root or '.') / 'game')
        paths, _ = QFileDialog.getOpenFileNames(
            self, '选择要导入剧情图的章节（可多选）', start,
            "章节脚本或纯文字 (*.rpy *.txt);;Ren'Py 脚本 (*.rpy);;"
            "纯文字 (*.txt);;所有文件 (*)")
        if not paths:
            return
        self.import_chapter_files(paths)

    def _import_plain_text(self, files: list):
        """
        **纯文字 / Word 章节**导入（S3/S4）：调会话那条正式链路，并把结果说人话。

        ⚠ 这条路的关键语义（都在 `import_plain_text` 里，我不重写）：
          未确认角色 ⇒ **一个字都不写**（未定义变量会让 Ren'Py 崩）；
          确认的决定**写回 characters.xlsx**（下次不用再问）。
        这里只负责：把"要确认什么"摆给人看，以及把结果念出来。
        """
        def ask(proposals):
            """
            问"这些写法指哪个角色"。⚠ 先只**如实念出来**并返回 `None`
            （= 这次不决定 → 会话保证不落盘），把弹窗留给下一轮做。
            宁可不写，也不替人猜。
            """
            for item in (proposals or [])[:8]:
                token = (item.get('token') if isinstance(item, dict)
                         else str(item))
                self._log(f'[导入] 要你确认：「{token}」指哪个角色？'
                          f'（认不出就先去 characters.xlsx 加别名）')
            return None

        res = self._session.import_plain_text(files, self.graph, ask=ask)
        for msg in (res.get('messages') or [])[:8]:
            self._log(f'[导入] {msg}')
        if res.get('needs_decision'):
            self._log('[导入] ⚠ 有角色没确认 —— **这次一个字节都没写**；'
                      '在 characters.xlsx 的「别名」列补上，再导一次')
        elif res.get('ok'):
            self._log(f'[导入] 纯文字/Word：写了 {len(res.get("written") or [])} 个文件')
        else:
            self._log(f'[导入] 没成：{res.get("error") or res.get("reason") or "未知"}')
        return res

    def _character_map(self):
        """给转换器用的角色表（读不出来就给 `None` —— 那就只有大写缩写那条判据）。"""
        try:
            from storygraph.pipeline.build import load_asset_registry
            reg = load_asset_registry(self._project_root)
            return dict(getattr(reg, 'characters', {}) or {}) or None
        except Exception as e:                      # noqa: BLE001 - 没表也能转
            swallow('ui_qt.import_chapters.character_map', e)
            return None
    def import_chapter_files(self, paths) -> dict:
        """
        把选中的 `.rpy` 加进剧情图（**累加**）。语义全在
        `EditorSession.import_chapters`，这里只管把结果塞进画布**并写进控制台**。

        用户 2026-10-03："导入的时候没有反馈！根本不知道导入了什么……"
        所以这里**逐个文件**报"来了几个模块 / 几条连线"，而不是只报一个总数。
        """
        old_graph = self.graph.copy() if self.graph is not None else None
        # ⭐ S3/S4：`.txt / .docx` 走**会话里那条正式链路**
        #   `EditorSession.import_plain_text`（S3 早就实现了，**只是界面从没调它**）。
        #   ⚠ 我第一版在这儿自己写了一份 `_convert_text_chapter` —— 那是**重复实现**，
        #   而且漏了会话那条路的关键语义（未确认角色就**一个字都不写**、
        #   决定写回 characters.xlsx、过生成门禁、复用 import_chapters）。
        #   已删掉重复实现，改调正式入口（docx 也因此自动支持）。
        plain = [p for p in paths
                 if str(p).lower().endswith(('.txt', '.text', '.docx'))]
        if plain:
            paths = [p for p in paths if p not in plain]
            self._import_plain_text(plain)
        if not paths:
            return {'ok': True, 'imported': 0}
        res = self._session.import_chapters(paths, self.graph)
        if not res.get('ok'):
            msg = f'[导入] 失败：{res.get("error") or "未知原因"}'
            self._log(msg)
            self._status(msg)
            return res
        # 导入也进历史：一次导入几十个文件之后发现导错了，Ctrl+Z 就能回到
        # 导入前的图（旧版是 `ImportRenPyCommand`/`ReplaceGraphCommand`）。
        try:
            from storygraph.graph.commands import ReplaceGraphCommand
            if old_graph is None:
                self._set_graph(res['graph'])
            else:
                self._run_command(ReplaceGraphCommand(self, res['graph'],
                                                      old_graph))
        except Exception:                       # noqa: BLE001
            self._set_graph(res['graph'])
        try:
            self._panel.set_symbols(self._session.symbols)
            self._refresh_asset_choices()
        except Exception:                       # noqa: BLE001
            swallow('main_window.import_chapter_files')

        # —— 逐个文件报告（导入了什么，一眼看得见）——
        # 排版先说：这一批只有一个文件时，`set_graph` 走的是**文件顺序**
        # （一列 = 这个文件，从上到下 = 文件里的先后）。不说的话，用户会以为
        # 图上又是"按跳转排的"，然后奇怪为什么这一章的样子不对。
        if getattr(self._canvas, 'last_arrange', '') == 'file':
            self._log('[排版] 这一批只有同一个 .rpy：按**文件顺序**排好了 —— '
                      '一列就是这个文件，从上到下就是文件里的先后'
                      '（要看跳转关系：工具栏「⤢ 自动布局」；'
                      '要再按文件排回来：「▤ 按文件顺序排列」）')
        by_file: dict = {}
        for node in (res['graph'].nodes or {}).values():
            fp = getattr(node, 'file_path', None)
            if fp:
                by_file.setdefault(str(Path(str(fp))), []).append(
                    getattr(node, 'label_name', ''))
        for p in res['files']:
            labels = by_file.get(str(Path(str(p))), [])
            piece = ('、'.join(labels[:6])
                     + (f' 等 {len(labels)} 个' if len(labels) > 6 else ''))
            self._log(f'[导入] {Path(str(p)).name} → {len(labels)} 个模块'
                      + (f'（{piece}）' if piece else '（这个文件里没有 label）'))
        self._log(f'[导入] 合计：新增模块 {res["added_nodes"]} 个 / '
                  f'连线 {res["added_edges"]} 条；'
                  f'图上一共 {res["nodes"]} 个模块 · {res["edges"]} 条连线'
                  f'（已导入 {len(self._session.graph_files(res["graph"]))} 章）')
        for w in (res.get('warnings') or [])[:5]:
            self._log(f'[导入] ⚠ {w}')
        for e in (res.get('errors') or [])[:5]:
            self._log(f'[导入] ✕ {e}')

        # —— 重载对账：源里删掉的 label / call 要**看得见地**消失（P2-1）——
        edge_rebuild = res.get('edge_rebuild') or {}
        removed_labels = list(res.get('removed_nodes') or [])
        if removed_labels:
            shown = '、'.join(removed_labels[:6])
            more = f' 等 {len(removed_labels)} 个' if len(removed_labels) > 6 else ''
            self._log(f'[重载] 源文件里已不存在的模块从图上移除：{shown}{more}'
                      f'（这次导入可以 Ctrl+Z 整体撤回）')
        removed_edges = int(edge_rebuild.get('removed') or 0)
        if removed_edges:
            self._log(f'[连线] 清理了 {removed_edges} 条源里已不存在的连线')
        skipped = list(edge_rebuild.get('skipped') or ())
        if skipped:
            targets = skipped_targets(edge_rebuild)
            shown = '、'.join(f'「{n}」' for n in targets[:4])
            more = f' 等 {len(targets)} 个' if len(targets) > 4 else ''
            self._log(f'[连线] ⚠ 有 {len(skipped)} 条控制流边没画全：'
                      f'目标 {shown}{more} 还不在图上。'
                      f'先导入它所在的章节（或点工具栏「🔗 补全缺失章节…」）。')

        names = '、'.join(Path(p).name for p in res['files'][:3])
        more = f' 等 {len(res["files"])} 个文件' if len(res['files']) > 3 else ''
        extra = []
        if removed_labels:
            extra.append(f'移除 {len(removed_labels)} 个源里已不存在的模块')
        if removed_edges:
            extra.append(f'清理 {removed_edges} 条失效连线')
        if skipped:
            extra.append(f'⚠ {len(skipped)} 条边没画全（目标未导入）')
        self._status(
            f'✔ 已导入 {len(res["files"])} 章（{names}{more}）：'
            f'新增模块 {res["added_nodes"]} 个 / 连线 {res["added_edges"]} 条，'
            f'图上共 {res["nodes"]} 个模块 · {res["edges"]} 条连线'
            + (f'；{"；".join(extra)}' if extra else ''))
        # **"我连线呢？"**（用户 2026-10-03）：导入的章节之间没有跳转时，
        # 图里确实一条线都没有 —— 因为串起它们的是**另一个文件**。
        # 这里必须把"还缺哪个文件"说出来，并给出一键补全的入口。
        self._report_missing_chapters(res['graph'])
        self._set_page(self.PAGE_GRAPH)
        return res
    def import_plain_text(self):
        """
        「📝 导入纯文字章节」：`.txt`（作者写的纯文字）→ `.rpy` → 剧情图。

        两遍（**这是安全的关键**，见 `session.import_plain_text`）：
        第一遍只规划，遇到认不出的角色写法就弹「确认角色」；人点完决定
        写进 `characters.xlsx`、**重跑转换**，第二遍才落盘。
        没确认 → **一个字节都不写**（未定义变量在游戏里会直接崩）。
        """
        if not self._project_root:
            QMessageBox.information(self, '提示',
                                    '先「📁 打开项目」（章节要落进 game/ 里）。')
            return
        start = str(Path(self._project_root) / 'game')
        paths, _ = QFileDialog.getOpenFileNames(
            self, '选择纯文字章节（可多选）', start,
            '纯文字 (*.txt);;Word 文档 (*.docx);;所有文件 (*)')
        if not paths:
            return
        # —— 第一遍：规划 + 问角色（不写盘）——
        first = self._session.import_plain_text(paths, self.graph, dry_run=True)
        # ⚠ dry_run 的 `ok` 恒为 False（它按定义什么都没写）—— 判据必须是
        # "**有没有规划出文件**"，不能看 `ok`，否则这条链一次都不会往下走。
        planned = getattr(first.get('plan'), 'files', None) or []
        if not planned:
            for _p, why in (first.get('skipped') or []):
                self._log(f'[纯文字] ✕ {why}')
            self._status(first.get('summary') or '没有可导入的文件（看控制台）')
            return
        self._show_plain_text_preview(first)

        def _ask(proposals):
            from storygraph.ui_qt.dialog_chars import CharDecisionDialog
            dlg = CharDecisionDialog(self, proposals,
                                     self._session.character_rows())
            if dlg.exec() != QDialog.Accepted:
                return None                     # 用户取消 → 什么都不写
            return dlg.result_proposals()

        # ⚠ 第二遍的导入走**本窗口的** `import_chapter_files`（它负责把图装进
        # 窗口、进历史、写控制台）—— 直接调 session 只会拿到一个返回值，
        # 图不会出现在界面上（实测踩到）。
        res = self._session.import_plain_text(
            paths, self.graph, ask=_ask,
            import_chapters=self.import_chapter_files)
        self._report_plain_text(res)

    def _show_plain_text_preview(self, res):
        """
        先让人看见"要落哪几个文件、哪些写法要定、**哪些会被拦**"。

        ⚠ 目标已存在的问题必须在**问之前**说清（实机实测）：真机项目里
        `chapter0_0.rpy` 这些**已经存在**（作者手写的），全量生成会被
        `LABEL_COLLISION` 拦下 —— 只在点完之后报"被拦了"，用户会以为工具坏了。
        """
        plan = res.get('plan')
        targets = []
        if plan is not None:
            for f in plan.files:
                targets.append(f.target)
                state = '已存在（会被门禁拦下，见下）' if f.target.exists() \
                    else '新建'
                self._log(f'[纯文字] {f.source.name} → {f.target.name}'
                          f'（label {f.label}；{state}）')
        for _p, why in (res.get('skipped') or []):
            self._log(f'[纯文字] ⚠ 跳过：{why}')
        props = res.get('proposals') or []
        if props:
            self._log(f'[纯文字] 有 {len(props)} 种写法不在角色表里：'
                      + '、'.join(p.token for p in props))
        if targets and any(t.exists() for t in targets):
            self._log('[纯文字] 目标已存在的文件：全量重生成**不会覆盖非工具生成的'
                      '脚本**（B2/P0：它不认得 define/init python，覆盖会静默'
                      '毁定义）。想改已有章节请用「导入章节 .rpy」+卡片编辑器；'
                      '只想看产物可以先导出到别处。')

    def _report_plain_text(self, res):
        for msg in (res.get('messages') or []):
            self._log(f'[纯文字] {msg}')
        for path, why in (res.get('blocked') or []):
            self._log(f'[纯文字] 🚫 {Path(path).name} 被门禁拦下：{why}')
        written = res.get('written') or []
        if not written:
            why = ''
            if res.get('blocked'):
                why = ('（目标章节已经在项目里 —— 全量生成**不覆盖非工具生成的'
                       '脚本**；见控制台）')
            self._status(f'[纯文字] {res.get("summary") or "没有落盘"}{why}')
            self._log('[纯文字] 一个文件都没写 —— 上面每一条都写清了原因。')
            return
        inner = res.get('import_result') or {}
        self._set_page(self.PAGE_GRAPH)
        self._status(
            f'✔ 纯文字导入 {len(written)} 章：'
            f'新增模块 {inner.get("added_nodes", 0)} 个 / '
            f'连线 {inner.get("added_edges", 0)} 条')
        self._log(f'[纯文字] 落盘：'
                  + '、'.join(Path(p).name for p in written))
        # ⚠ 图在**窗口**这一份（`self.graph`）—— `import_chapters` 的返回体里
        # 没有 `graph` 键，别去取它（取到 None 会让下一个用图的人直接炸）。
        if self.graph is not None:
            self._report_missing_chapters(self.graph)
    def open_ai_panel(self):
        """
        「🤖 AI 助手」：跟模型说话，它自己调工具干活（用户 2026-10-11 的要求）。

        ⚠ **写盘闸门在这个面板里**：模型要写清单时，弹窗给它看 diff，
        人点头才写（闸门本体在 `ai/agent.py`，**代码强制**，不靠提示词）。
        """
        from PySide6.QtWidgets import QDialog, QVBoxLayout
        from storygraph.ui_qt.panel_ai import AiPanel
        root = getattr(self, 'project_root', '') or ''
        if not root:
            self._log('[AI] 先打开一个项目，它才知道要读哪儿')
            return
        dlg = QDialog(self)
        dlg.setWindowTitle('AI 助手')
        dlg.resize(880, 640)
        lay = QVBoxLayout(dlg)
        panel = AiPanel(dlg, project_root=root)
        lay.addWidget(panel)
        dlg.exec()
        self._log('[AI] 助手窗口已关闭')

    def open_ai_key_dialog(self):
        """
        「🔑 AI 助手 · 填 API key」：**用户自己填 key**（用户 2026-10-11 的要求）。

        > "工具是本地的，所以最好的做法是让别人跟其余用户自己填 api key，
        >  最好打开之后能有一个地方点开，然后让用户输入 api key，
        >  然后再让 ai 自行开始工作"

        为什么写进**用户级**目录（不是项目里）：key 是**这台机器这个人**的，
        跟着项目走会随 git 泄漏出去。存哪儿由 `core.ai_settings.config_path()`
        决定（`%LOCALAPPDATA%/StoryGraph/ai.json`）。
        """
        from storygraph.core import ai_settings as AI
        from storygraph.ui_qt.dialog_ai import AiKeyDialog
        dlg = AiKeyDialog(self)
        dlg.exec()
        cfg = AI.load()
        ready, why = AI.is_ready(cfg)
        if ready:
            self._log(f'[AI] 已配置 {cfg.get("provider")}'
                      f'（key {AI.masked(cfg.get("api_key"))}）—— 可以开工了')
        else:
            self._log(f'[AI] 还没配好：{why}')

    def learn_custom_statement(self):
        """
        「🧩 自定义语句」：教工具认识项目自己的写法。

        用户 2026-10-10："我并没有找到一个入口可以教这个工具学会我自定义的
        项目模块……至少我想要个能够自定义的模块入口放在最上方"。

        这条链的每一步都是既有能力，缺的只是入口：
        扫项目找未声明写法 → 在弹窗里说清它后面跟什么 → 试切 → 保存
        （`declare_statement`，唯一写入口）→ 清单缓存作废 →
        表格立刻多出参数列与候选值。

        ⚠ 2026-10-18 第六版起，**写盘在弹窗里**（见下面的分叉），这里只负责
        "它到底改了什么"的记账 + 刷新。
        """
        if not self._project_root:
            QMessageBox.information(
                self, '提示',
                '先「📁 打开项目」—— 声明要写进项目根的 '
                'storygraph.rules.json。')
            return
        from storygraph.story import statement_registry as SR
        from storygraph.ui_qt.dialog_learn import CustomStatementDialog

        root = self._project_root
        SR.clear_cache()
        discovered = SR.discover_undeclared(root)
        # ⚠ **语句注册表 ≠ 资产注册表**：`self._session.registry()` 给的是
        # `AssetRegistry`（角色/立绘/音频），而这里要 `StatementRegistry`
        # （项目声明的自定义语句）。第一版把前者喂给
        # `format_registry_report` → `TypeError: 'AssetRegistry' object is not
        # iterable` → **异常在 Qt 槽里被吞掉，症状就是"点了没反应"**（实测）。
        try:
            reg = SR.StatementRegistry.for_project(root)
        except Exception as e:                  # noqa: BLE001
            swallow('main_window.learn_custom_statement.registry', e)
            reg = None
        report = (SR.format_registry_report(reg) if reg is not None else
                  '（读不到 storygraph.rules.json —— 声明列表暂时显示不出来，'
                  '但下面的"发现"仍然可用）')

        def _on_verify(head: str) -> str:
            """
            「▶ 验证它能跑」（第三层）：**只在这里碰引擎**，界面只显示结论。

            为什么不放界面里：跑一次要拷副本、跑 `test`（几秒到几十秒），
            而"**两把锁**"（引擎 PASSED **且** 证据文件真的存在）这套判据在
            `pipeline.statement_probe.verify_head` 里 —— 前端口径必须与它一致。
            """
            from storygraph.pipeline import statement_probe as SP
            self._log(f'[验证] 开始验证「{head}」…')
            verdict = SP.verify_head(root, head)
            verified = verdict.get('verified')
            # ⚠ 三态如实报：通过 / 失败 / **没证明成**（后者绝不并进"失败"）
            if verified is True:
                head_line = f'✅ 通过：{verdict.get("summary")}'
            elif verified is False:
                head_line = f'⚠ 失败：{verdict.get("summary")}'
            else:
                head_line = f'⚠ 没证明成：{verdict.get("summary")}'
            self._log(f'[验证] {head} → {head_line}')
            try:                                   # 记进事实（报告那行会跟着动）
                facts = self._session.engine_facts()
                if facts:
                    SP.record_behavior(facts, f'sg_check_{head}', verdict)
                    from storygraph.core import engine_facts as EF
                    from storygraph.pipeline.engine_collect import write_facts
                    write_facts(EF.facts_path(root), facts)
                    self._log('[验证] 结论已记进引擎事实（体检报告那一行会跟着变）')
            except Exception:                       # noqa: BLE001 - 记不上不影响验证
                swallow('ui_qt.learn_custom_statement.record_behavior')
            lines = [head_line]
            if verdict.get('label'):
                lines.append(f'用到的章节 label：{verdict["label"]}')
            if verdict.get('evidence'):
                lines.append(f'证据文件：{verdict["evidence"]}')
            if verdict.get('reason'):
                lines.append(f'原因：{verdict["reason"]}')
            # ⭐ 把引擎说的**具体位置**抬到最前面（用户："回到 UI"）——
            #   只显示"没证明成"，人不知道该去改哪儿；引擎其实说得清清楚楚。
            where = SP.first_parse_error(' '.join(verdict.get('raw_tail') or []))
            if where and verified is not True:
                lines.append('')
                lines.append(f'引擎说这一行它不认识：{where}')
                lines.append('（先看那个文件那一行 —— 若这是你的自定义写法，'
                             '项目里得有一段 renpy.register_statement 注册它）')
            # ⚠ 验证不过时**顺手说清该怎么办**（否则用户只会看到"没证明成"）
            if verified is not True:
                lines.append('')
                lines.append('怎么办：')
                lines.append('  · 先看上面引擎原话 —— 若写着 `expected statement.`，'
                             '说明**引擎不认识这个写法**：它需要项目里有一段 '
                             '`renpy.register_statement("' + head + '", …)`。')
                lines.append('  · ⚠ 注意 `init python` 里注册**赶不上解析期**'
                             '（引擎一组解析出错、后面全不解析）—— '
                             '这就是"声明了却跑不起来"的常见原因。')
                lines.append('  · 引擎认识了、只是没跑到那一句时，'
                             '把这一句放进某一章的正文再验一次。')
            for ln in (verdict.get('raw_tail') or [])[-6:]:
                lines.append(f'  {ln}')
            return '\n'.join(lines)

        dlg = CustomStatementDialog(self, project_root=root,
                                    discovered=discovered,
                                    registry_report=report,
                                    declared_groups=(reg.attr_groups
                                                     if reg is not None else []),
                                    on_verify=_on_verify)
        self._log(f'[自定义] 扫出 {len(discovered)} 个还没声明的写法'
                  + ('：' + '、'.join(r['head'] for r in discovered[:8])
                     if discovered else ''))
        dlg.exec()
        # ⚠ 2026-10-18 第六版：**写盘在弹窗里**（`CustomStatementDialog._save_statement`）。
        #   这里只负责"它到底改了什么"的记账 + 刷新。
        #
        #   为什么改成这样：旧版是 `exec() == Accepted` 之后由这里写 ——
        #     ① 保存失败只出现在控制台，界面上"按下去好像没反应"；
        #     ② 想连着教三条要开三次窗；
        #     ③ 更要命的是这里**漏传了 `pattern`**（格子的类型/顺序/多选全丢），
        #        因为"表单 → 声明"的产物要跨文件再传一遍，迟早漏。
        #   现在只有一处写，判据也钉在那一处（`tests/test_custom_statement_manage.py`）。
        if not getattr(dlg, 'changed', False):
            self._log('[自定义] 没有改动项目清单')
            return
        records = list(getattr(dlg, 'saved', []) or [])
        for rec in records:
            kind = str(rec.get('kind') or '')
            if kind == 'statement':
                self._log(f'[自定义] {rec.get("verb") or "写入"}声明 '
                          f'{rec.get("name")}（{rec.get("columns", 0)} 个参数）'
                          f' → {rec.get("path")}')
            elif kind == 'groups':
                self._log(f'[自定义] 候选分栏 {rec.get("count", 0)} 栏'
                          f' → {rec.get("path")}')
            elif kind == 'delete':
                self._log(f'[自定义] 已从清单删除声明 {rec.get("name")}')
            elif kind == 'reset':
                self._log('[自定义] 已重置自定义声明（其它键保留）')
        self._after_learn_refresh()
        n_stmt = sum(1 for r in records if r.get('kind') == 'statement')
        n_grp = sum(1 for r in records if r.get('kind') == 'groups')
        n_del = sum(1 for r in records if r.get('kind') == 'delete')
        parts = []
        if n_stmt:
            parts.append(f'{n_stmt} 条语句')
        if n_grp:
            parts.append(f'{n_grp} 次分栏')
        if n_del:
            parts.append(f'删掉 {n_del} 条')
        self._status('✔ 项目清单已更新（' + '、'.join(parts or ['有改动']) +
                     '）—— 重新导入那几章即可看到效果')

    def _after_learn_refresh(self):
        """
        教完一条之后刷新：**清清单缓存** + 重算符号表 + 把分栏推给卡片。

        ⚠ 不清缓存的话表格/提示/分栏还是旧的 —— 用户会看到"声明了但没生效"。
        这也是 `set_choice_groups_provider` 那类方法必须存在的原因（缺一个就
        变成模态弹窗卡死，见 `test_every_panel_method_the_window_calls_really_exists`）。
        """
        from storygraph.story import statement_registry as SR
        SR.clear_cache()
        try:
            if hasattr(self._session, 'refresh_symbols'):
                self._session.refresh_symbols()
            self._refresh_asset_choices()
        except Exception:                       # noqa: BLE001
            swallow('main_window._after_learn_refresh.assets')
        for name in ('_sync_choice_groups',):
            fn = getattr(self, name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:               # noqa: BLE001
                    swallow('main_window._after_learn_refresh.groups')
    def save_layout(self, target=None):
        """
        保存布局 JSON（Ctrl+S）。没给路径就弹「另存为」。

        语义在 `EditorSession.save_layout`（格式与旧版逐字段一致）。
        """
        target = drop_qt_bool(target, context='ui_qt.save_layout')
        if target is None:
            if self.graph is None or not getattr(self.graph, 'nodes', None):
                self._status('剧情图是空的，没有布局可保存。')
                return ''
            guess = str((self._project_root or Path.cwd())
                        / 'storygraph_layout.json')
            target, _ = QFileDialog.getSaveFileName(
                self, '保存布局（JSON）', guess, 'JSON 文件 (*.json)')
            if not target:
                return ''
        res = self._session.save_layout(self.graph, target)
        if not res.get('ok'):
            self._log(f'[布局] 保存失败：{res.get("error")}')
            self._status(f'保存布局失败：{res.get("error")}')
            return ''
        name = Path(res['path']).name
        self._mark_graph_saved(str(res['path']))
        self._log(f'[布局] 已保存 {res["nodes"]} 个模块 / {res["edges"]} 条连线'
                  f' → {res["path"]}')
        self._status(f'💾 布局已保存：{name}（{res["nodes"]} 块 / '
                     f'{res["edges"]} 线）')
        return res['path']
    def load_layout(self, source=None):
        """
        加载布局 JSON（Ctrl+O）：**整图替换**成文件里的那张图。

        会先问一声 —— 图上的导入结果会被换掉（内存里，磁盘不动）。
        """
        source = drop_qt_bool(source, context='ui_qt.load_layout')
        if source is None:
            start = str(self._project_root or Path.cwd())
            source, _ = QFileDialog.getOpenFileName(
                self, '加载布局（JSON）', start, 'JSON 文件 (*.json)')
            if not source:
                return ''
        if self.graph is not None and getattr(self.graph, 'nodes', None):
            if QMessageBox.question(
                    self, '加载布局',
                    '加载布局会把当前剧情图**整张换掉**（只改内存，磁盘不动）。'
                    '\n\n确定继续？') != QMessageBox.Yes:
                return ''
        old = self.graph.copy() if self.graph is not None else None
        res = self._session.load_layout(source)
        if not res.get('ok'):
            self._log(f'[布局] 加载失败：{res.get("error")}')
            self._status(f'加载布局失败：{res.get("error")}')
            return ''
        try:
            from storygraph.graph.commands import ReplaceGraphCommand
            if old is None:
                self._set_graph(res['graph'])
            else:
                self._run_command(ReplaceGraphCommand(self, res['graph'], old))
        except Exception:                       # noqa: BLE001
            self._set_graph(res['graph'])
        # 加载布局是"我要看这张新图" —— 显式适应窗口。
        # （连线/撤销那种重建默认**保住**缩放，见 `GraphCanvas.set_graph`；
        # 这里是少数几个"换了看什么"的场景，所以主动 fit 一次。）
        try:
            self._canvas.fit_to_content()
        except Exception:                       # noqa: BLE001
            swallow('main_window.load_layout')
        name = Path(res['path']).name
        self._mark_graph_saved(str(res['path']))
        self._log(f'[布局] 已加载 {name}：{res["nodes"]} 个模块 / '
                  f'{res["edges"]} 条连线')
        self._status(f'📂 已加载布局 {name}（{res["nodes"]} 块 / '
                     f'{res["edges"]} 线）')
        return res['path']
    def arrange_by_file(self):
        """
        「▤ 按文件顺序排列」：**一个 `.rpy` 一列**，同一文件里从上到下就是
        文件里的先后（按 `line_number`，不是按导入顺序）。

        用户 2026-10-22：

        > 在一个 rpy 文件内定义多个 label……让它识别到并自动排好块逻辑
        > （现在这个流程图就是按照 label 之间的逻辑来排的，
        > 不是按照 rpy 文件的逻辑来排的）

        语义全在 `graph.layout.layout_by_file`（纯数据、可单测），这里只做
        三件事：把结果念出来、标脏（坐标变了，状态栏的"未保存"要说真话）、
        失败时保持原样。

        与「⤢ 自动布局」**不冲突、也不互相覆盖**：那条按 jump / call 分层
        （看跨章节的控制流），这条按文件与行号（看这一章在源文件里的样子）。
        """
        if self.graph is None or not getattr(self.graph, 'nodes', None):
            self._status('剧情图是空的，没有可排列的块。')
            return {}
        info = self._canvas.arrange_by_file() or {}
        if not info:
            self._status('排列失败（布局算不出来）—— 图上的位置没动。')
            return {}
        files = list(info.get('files') or [])
        loose = int(info.get('loose') or 0)
        # 坐标进了布局指纹：排完要立刻标脏，否则关窗时不会提示保存，
        # 下次打开又是旧位置（"我明明排好了"）。
        self._sync_graph_dirty()
        for col, item in enumerate(files[:6], start=1):
            labels = list(item.get('labels') or [])
            shown = '、'.join(labels[:6])
            more = f' 等 {len(labels)} 个' if len(labels) > 6 else ''
            self._log(f'[排版] 第 {col} 列 '
                      f'{Path(str(item.get("path") or "")).name}：{shown}{more}')
        if loose:
            self._log(f'[排版] 另外 {loose} 个块没有源文件（手画的占位块），'
                      f'排在最右一列')
        names = [Path(str(f.get('path') or '')).name for f in files]
        shown = '、'.join(names[:3]) + (f' 等 {len(names)} 个文件'
                                       if len(names) > 3 else '')
        self._status(
            f'▤ 已按文件顺序排好：{len(files)} 个文件 · '
            f'{int(info.get("nodes") or 0)} 个块（{shown}）—— '
            f'一列就是一个 .rpy，同一列从上到下就是文件里的先后；'
            f'要看跳转关系用「⤢ 自动布局」')
        return info
    def export_control_flow(self, target=None, *, wrapper=None):
        """
        导出 Ren'Py 控制流脚本（每个模块一个 `<label>_control`）。

        用户 2026-10-04："我的旧版本有导出控制流图.rpy文件，label末尾加
        _control 的那个……现在这个新版本呢？"

        `wrapper` 只许关键字传（`*`）：它是"要不要补 `_control`"的开关，
        和 `target` 完全是两件事，位置传参串位没有任何好处。

        ⚠ **`False` 那条老故事要更正**（2026-10-05 实测）：仓库根目录里那个
        叫 `False` 的文件**确实**是这里写出来的，但**不是**下面两个菜单项干的
        —— 菜单项写的是 `export_control_flow(wrapper=True/False)`，
        **关键字传参按名字绑定**，`wrapper` 排第几位都不可能落进 `target`。
        真凶是**按钮**那条接线（同一天修掉的）：`self._export_flow_btn.clicked.
        connect(self.export_control_flow)` —— `clicked` 会发一个 `bool`，
        它成了**第一个位置参数** → `target=False` → 下面 `if target is None:`
        判不出来（`False is None` 是 False）→ 对话框被绕过 → `False` 当路径
        送下去 → 工作目录里多出 `False` 这个文件。
        （同类的还有 `save_layout` / `load_layout` / `add_node` / `add_edge`，
        一并修了，并加了静态审计 + `drop_qt_bool` 兜底。）
        """
        if self.graph is None or not getattr(self.graph, 'nodes', None):
            self._status('剧情图是空的，没有可导出的控制流。')
            return ''
        target = drop_qt_bool(target, context='ui_qt.export_control_flow')
        if target is None:
            start_dir = (self._project_root / 'game') if self._project_root \
                else Path.cwd()
            guess = str(Path(start_dir) / 'story_graph_control.rpy')
            target, _ = QFileDialog.getSaveFileName(
                self, "导出控制流（Ren'Py）", guess, "Ren'Py 脚本 (*.rpy)")
            if not target:
                return ''
        res = self._session.export_control_flow(self.graph, target,
                                                wrapper=wrapper)
        if not res.get('ok'):
            self._log(f'[控制流] 导出失败：{res.get("error")}')
            self._status(f'导出控制流失败：{res.get("error")}')
            return ''
        name = Path(res['path']).name
        self._log(f'[控制流] 已生成 {res["count"]} 个标签'
                  f'（{res.get("mode", "")}）→ '
                  f'{res["path"]}（原 label 末尾要能正常 return）')
        self._status(f'⤓ 控制流已导出：{name}（{res["count"]} 个标签，'
                     f'{res.get("mode", "")}）')
        for w in (res.get('warnings') or []):
            self._log(f'[控制流] ⚠ {w}')
        return res['path']
    def _refresh_choices_now(self):
        """旧版「刷新下拉候选（从资源表重读）」：手动的那个入口。"""
        self._refresh_asset_choices()
        n = 0
        for field in self.ASSET_FIELD_KINDS:
            combo = self._panel._widgets.get(field)
            # 背景/音乐/语音是下拉；音效那一格是文本框（没有候选表），
            # 所以这里只统计真下拉。
            n += combo.count() if isinstance(combo, QComboBox) else 0
        self._log(f'[资源] 已从资源表重读下拉候选（背景/音乐/语音共 {n} 条）')
        self._status(f'🔄 下拉候选已刷新（共 {n} 条）')
    def _show_more_tools(self):
        """把次要入口收进一个菜单（按钮本身不动图，只是入口）。"""
        menu = QMenu(self)
        menu.addAction('⤓ 导出到其他引擎（Unity / Godot / JSON / CSV）…',
                       self.export_other_engine)
        menu.addAction('📈 资产 / 角色统计…', self.show_asset_overview)
        menu.addAction('✅ 校验当前章节（三级校验）', self.validate_current_script)
        menu.addAction('➕ 补登缺失资源（扫描磁盘 → 写进资产表）…',
                       self.register_missing_assets)
        menu.addAction('⤓ 导出控制流 .rpy（按章节图，补 _control）…',
                       lambda: self.export_control_flow(wrapper=True))
        menu.addAction('⤓ 导出控制流 .rpy（按控制流图原样导出）…',
                       lambda: self.export_control_flow(wrapper=False))
        menu.addSeparator()
        menu.addAction('👥 协作库（SQLite ↔ Excel）…', self.show_collab)
        btn = getattr(self, '_more_btn', None)
        pos = (btn.mapToGlobal(btn.rect().bottomLeft()) if btn is not None
               else self.mapToGlobal(self.rect().center()))
        menu.exec(pos)
    def export_other_engine(self, engine=None):
        """
        多引擎导出：`pipeline.engine_exporters.export_ir`。

        没给 `engine` 就先问一句（Unity / Godot / 中立 JSON / CSV / Ren'Py 清单），
        再选输出目录。
        """
        from PySide6.QtWidgets import QInputDialog
        if not self._session.beats:
            self._status('先在卡片页载入一章，再导出到其他引擎。')
            return ''
        if engine is None:
            engine, ok = QInputDialog.getItem(
                self, '导出到其他引擎', '目标格式：',
                ['unity', 'godot', 'json', 'csv', 'renpy'], 0, False)
            if not ok or not engine:
                return ''
        out_dir = QFileDialog.getExistingDirectory(
            self, '导出到哪个目录',
            str(self._project_root or Path.cwd()))
        if not out_dir:
            return ''
        res = self._session.export_ir(engine, out_dir)
        if not res.get('ok'):
            self._log(f'[导出] 其他引擎（{engine}）失败：{res.get("error")}')
            self._status(f'导出失败：{res.get("error")}')
            return ''
        names = '、'.join(Path(f).name for f in res['files'][:4])
        self._log(f'[导出] {engine}：{len(res["files"])} 个文件 → '
                  f'{res["out_dir"]}（{names}）')
        loss = res.get('loss') or {}
        count = int(loss.get('count') or 0)
        if count:
            # **损失报告**（§18）：导出成功不等于导全了。适配层没搬的字段
            # 必须在这里说一句，否则用户以为 Unity 那边和编辑器一样。
            fields = []
            for scope, rows in (loss.get('by_scope') or {}).items():
                for row in rows:
                    fields.append(f'{row.get("field")}×{row.get("count")}'
                                  f'（{scope}）')
            self._log('[导出] 损失报告：' + '、'.join(fields[:12])
                      + f'；详见 IR_LOSS-{engine}.md')
        # **自检**（§20）：IR 自己有没有矛盾（重复主键 / 统计不符 / 快照
        # 指向不存在的行 / Unity 产物里的嵌套字典）。有错误就不许只报"✔"。
        check = res.get('check') or {}
        errs = int(check.get('error_count') or 0)
        warns = int(check.get('warning_count') or 0)
        if check.get('status') == 'failed':
            # 自检**没跑成**和"零错误"必须分开说：前者什么也没保证。
            detail = str(check.get('error') or '')
            self._log(f'[导出] IR 自检没跑成：{detail}（IR_CHECK.md 写的可能是'
                      f'更早那份报告）')
            self._status(f'⚠ 已导出 {engine} 格式（{len(res["files"])} 个文件）'
                         f' · 自检没跑成，别当成"没问题"')
            return res['out_dir']
        if errs or warns:
            level = '错误' if errs else '警告'
            self._log(f'[导出] IR 自检：{errs} 个错误 / {warns} 个警告'
                      f'（详见 IR_CHECK.md）')
            for issue in (check.get('issues') or []):
                if issue.get('level') != 'error':
                    continue
                self._log(f'  · {issue.get("code")}×{issue.get("count")}：'
                          f'{issue.get("message")}')
            if errs:
                self._status(f'⚠ 已导出 {engine} 格式（{len(res["files"])} '
                             f'个文件）· 自检 {errs} 个{level}')
            else:
                self._status(f'✔ 已导出 {engine} 格式（{len(res["files"])} '
                             f'个文件）· 自检 {warns} 个{level}' + (
                                 f' · 损失 {count} 类字段' if count else ''))
        elif count:
            self._status(f'✔ 已导出 {engine} 格式（{len(res["files"])} 个文件）'
                         f' · 损失 {count} 类字段')
        else:
            self._status(f'✔ 已导出 {engine} 格式（{len(res["files"])} 个文件）')
        return res['out_dir']
    def show_asset_overview(self):
        """资产 / 角色统计（只读）→ 写进控制台 + 弹一个可滚动的明细窗。"""
        res = self._session.asset_overview()
        if not res.get('ok'):
            self._log(f'[资产] {res.get("error")}')
            self._status(res.get('error') or '拿不到资产统计')
            return
        stats = res.get('statistics') or {}
        lines = ['【资产统计】']
        for key, value in stats.items():
            lines.append(f'  {key}：{value}')
        cats = res.get('categories') or {}
        if cats:
            lines.append('【分类】')
            for key, value in list(cats.items())[:20]:
                lines.append(f'  {key}：{value}')
        dims = res.get('dimension_confidence') or {}
        if dims.get('groups'):
            lines.append(f'【维度】{dims["groups"]} 组，其中 '
                         f'{dims.get("guessed", 0)} 组是猜的（低置信度）')
        tops = res.get('top_characters') or []
        if tops:
            lines.append('【角色台词量】')
            lines.extend(format_character_ranking(tops))
        for title, key in (('未定义角色', 'undefined'),
                           ('没出过场的角色', 'unused_characters'),
                           ('未使用的资产', 'unused_assets'),
                           ('断裂引用', 'broken')):
            rows = res.get(key) or []
            lines.append(f'【{title}】{len(rows)} 条')
            for row in rows[:30]:
                if isinstance(row, dict):
                    label = (row.get('asset_id') or row.get('name')
                             or row.get('character') or row)
                    extra = row.get('category') or row.get('kind') or ''
                    lines.append(f'  {label}{("（" + str(extra) + "）") if extra else ""}')
                else:
                    lines.append(f'  {row}')
        text = '\n'.join(lines)
        self._log(f'[资产] 统计：{stats.get("images", "?")} 图 / '
                  f'{stats.get("music", "?")} 乐；未使用 '
                  f'{len(res.get("unused_assets") or [])} 条，断裂引用 '
                  f'{len(res.get("broken") or [])} 条')
        try:
            from storygraph.ui_qt.dialogs import TextViewDialog
            dlg = TextViewDialog(self, '资产 / 角色统计', text)
            dlg.exec()
        except Exception:                     # noqa: BLE001
            self._status('资产统计已写进控制台。')
        return text
    def register_missing_assets(self):
        """
        把磁盘上有、资产表里没登记的资源**补进资产表**（写前自动备份）。

        用户 2026-10-05："先查背景图为何没有被登记进去，然后，让背景图被登记进去。"
        """
        if not self._ensure_registry():
            return
        if QMessageBox.question(
                self, '补登缺失资源',
                '会扫描项目里的图片 / 音乐 / 音效，把**表里还没有的**追加进\n'
                f'{self._project_root}/game/asset_tables/（写前备份成 *.bak.xlsx，'
                '已登记的行一个都不动）。\n\n继续？') != QMessageBox.Yes:
            return
        res = self._session.register_missing_assets()
        if not res.get('ok'):
            self._log(f'[资源] 补登失败：{res.get("error")}')
            self._status(f'补登失败：{res.get("error")}')
            return
        kinds = '、'.join(f'{k} {v}' for k, v in (res.get('by_kind') or {}).items()
                          if v)
        if not res.get('added'):
            self._log('[资源] 磁盘上有的资源都已经登记过了，没有需要补的')
            self._status('没有需要补登的资源。')
            return
        self._refresh_asset_choices()
        self._log(f'[资源] 已补登 {res["added"]} 条（{kinds}）；'
                  f'备份 = {res.get("backup") or "（新建表）"}')
        self._status(f'✔ 已补登 {res["added"]} 条资源（{kinds}）')
    def validate_current_script(self):
        """
        三级校验当前章节（`pipeline.validate.validate_all`）→ 控制台。

        契约：它收的是 **beats**（不是路径），返回的 `ValidationReport` 有
        `errors` / `warnings` / `summary`；`Issue` 是 dict（level/code/
        severity/message/where/hint），**没有** `is_blocking`。
        """
        if not self._session.beats:
            self._status('还没有载入章节，没有可校验的内容。')
            return
        try:
            from storygraph.pipeline.validate import validate_all
            report = validate_all(self._session.beats,
                                  registry=self._registry_provider(),
                                  graph=self.graph,
                                  project_root=self._project_root)
        except Exception as e:                # noqa: BLE001
            self._log(f'[校验] 失败：{e}')
            self._status(f'校验失败：{e}')
            return
        errors = list(getattr(report, 'errors', []) or [])
        warnings = list(getattr(report, 'warnings', []) or [])
        name = Path(self._file_path).name if self._file_path else '（未命名）'

        def _msg(issue):
            if isinstance(issue, dict):
                return f"[{issue.get('level', '')}] {issue.get('message', '')}"
            return f'[{getattr(issue, "level", "")}] ' \
                   f'{getattr(issue, "message", issue)}'

        self._log(f'[校验] {name}：{len(errors)} 个错误 / {len(warnings)} 条提醒')
        for issue in (errors + warnings)[:20]:
            self._log(f'    · {_msg(issue)[:160]}')
        self._status(f'✅ 校验完成：{len(errors)} 个错误 / {len(warnings)} 条提醒'
                     '（明细见控制台）')
    def show_collab(self):
        """
        协作库入口（`collab.collab_store`：SQLite ↔ Excel）。

        刻意只做"登记 / 拉取 / 提交 / 看状态"四件事：冲突规则在
        `collab_store.push` 里（被锁、revision 更新一律**不静默覆盖**），
        界面不重复实现一遍。
        """
        from storygraph.ui_qt.dialogs import CollabDialog
        dlg = CollabDialog(self, session=self._session,
                           project_root=self._project_root,
                           log=self._log, status=self._status)
        dlg.exec()
        return dlg
    def _on_node_moved(self, nid, ox, oy, nx, ny):
        """
        拖完一个块 → 记进撤销栈（用户 2026-10-05："将撤销做好"）。

        模型里的坐标在拖动过程中已经被 `NodeItem.itemChange` 实时写好了，
        所以这里**不重复 execute**、只记历史（`_record_applied`）——否则
        命令的 `old_ops/old_x` 会记成拖完之后的坐标，撤销等于没动。
        """
        from storygraph.graph.commands import MoveNodeCommand
        self._record_applied(MoveNodeCommand(self, nid, ox, oy, nx, ny))
        # 拖远了就把场景矩形撑大：否则那块"看得见、到不了"
        # （平移范围由场景矩形决定），表现为"拖画布时灵时不灵"。
        try:
            self._canvas.note_content_changed()
        except Exception:                     # noqa: BLE001
            swallow('main_window._on_node_moved')
        # 拖块也是"图的改动"：布局指纹变了，状态栏要能说"还没保存"。
        self._sync_graph_dirty()
    # ------------------------------------------------------------------
    #  剧情图持久化（未保存提示 / 关窗自动保存）
    # ------------------------------------------------------------------

    def _sync_graph_dirty(self):
        """按布局指纹刷新"剧情图未保存"标记（状态栏 + 标题栏）。"""
        try:
            from storygraph.app.session_graph import layout_signature
            sig = layout_signature(self.graph)
            has_content = bool(getattr(self.graph, 'nodes', None))
            dirty = bool(has_content and sig
                         and sig != (self._graph_saved_sig or ''))
            if dirty == self._graph_dirty:
                return
            self._graph_dirty = dirty
            self.setWindowTitle(('● ' if dirty else '') + self._base_title)
            try:
                self._status_bar.set_layout_dirty(
                    dirty, str(self._last_layout_path or ''))
            except Exception as e:              # noqa: BLE001
                swallow('ui_qt.window_graph_ops.sync_graph_dirty.status', e)
        except Exception as e:                  # noqa: BLE001 - 只是提示
            swallow('ui_qt.window_graph_ops.sync_graph_dirty', e)

    def _mark_graph_saved(self, path: str = ''):
        """刚落盘 / 刚加载：把当前图记为基线，状态栏回到"已保存"。"""
        try:
            from storygraph.app.session_graph import layout_signature
            self._graph_saved_sig = layout_signature(self.graph)
            self._last_layout_path = str(path or '')
            self._graph_dirty = False
            self.setWindowTitle(self._base_title)
            self._status_bar.set_layout_dirty(False, str(path or ''))
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_graph_ops.mark_graph_saved', e)

    def _layout_save_target(self):
        """关窗保存布局的默认位置：项目根目录下的 `storygraph_layout.json`。"""
        root = getattr(self, '_project_root', None)
        if not root:
            return None
        return Path(str(root)) / 'storygraph_layout.json'

    def _save_layout_to(self, target) -> dict:
        """把当前图写到 `target`；成功才把状态栏切回"已保存"。"""
        res = self._session.save_layout(self.graph, Path(target))
        if res.get('ok'):
            self._mark_graph_saved(str(res['path']))
        else:
            self._log(f'[布局] ⚠ 保存失败：{res.get("error")}')
        return res

    def _auto_save_layout_on_close(self) -> bool:
        """
        关窗前把剧情图自动存到项目里（**没有用户可问**的那条路）。

        只在 `_confirm_close` 判定"没有可见窗口"时走：测试 / 嵌入 /
        启动失败清理，构造完就 close，弹模态框会把进程挂住。
        """
        if not self._graph_dirty:
            return True
        root = self._project_root
        nodes = len(getattr(self.graph, 'nodes', None) or {})
        if not root or not nodes:
            return True
        self._save_layout_to(Path(root) / 'storygraph_layout.json')
        return True

    def _ask_close_graph_action(self) -> str:
        """
        关窗三选一：`save` / `discard` / `cancel`。

        为什么不是"自动保存就完了"：布局只是版式，但用户可能**故意**想丢掉
        这次拖乱的结构；而"取消"必须真的能取消 —— 否则误点一次就是白改。
        """
        box = QMessageBox(self)
        box.setWindowTitle('剧情图还没保存')
        box.setIcon(QMessageBox.Question)
        box.setText('剧情图有改动，关掉软件就没了。')
        target = self._layout_save_target()
        box.setInformativeText(
            f'保存到：{target}' if target
            else '还没打开项目目录，保存时会让你选位置。')
        save = box.addButton('保存剧情图并退出', QMessageBox.AcceptRole)
        box.addButton('不保存，直接退出', QMessageBox.DestructiveRole)
        cancel = box.addButton('取消', QMessageBox.RejectRole)
        box.setDefaultButton(save)
        box.setEscapeButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return 'save'
        if clicked is None or clicked is cancel:
            return 'cancel'
        return 'discard'

    def _save_layout_for_close(self) -> bool:
        """用户选了"保存"：成功才允许关窗（失败就别关，图还在内存里）。"""
        target = self._layout_save_target()
        if target is None:
            path, _ = QFileDialog.getSaveFileName(
                self, '保存剧情图布局',
                str(Path.home() / 'storygraph_layout.json'),
                '剧情图布局 (*.json)')
            if not path:
                return False
            target = Path(path)
        res = self._save_layout_to(target)
        if not res.get('ok'):
            QMessageBox.warning(self, '剧情图保存失败',
                                f'{res.get("error")}\n\n先不关窗，图还在。')
            return False
        return True

    def _confirm_close(self) -> bool:
        """
        关窗前"还有什么没保存"：**图 / 这一章 / 当前表单**三处一起问。
        返回 `False` = **不要关**（用户点了取消）。

        为什么三处要一起问（2026-10-22 上一轮只问了图）：卡片页的改动先落在
        内存里、点「⤓ 导出 .rpy」才写文件 —— 用户改了三拍、关窗、重开，
        那三拍**一个字都没了**，而关窗时程序一句都没说。这比"图的位置没了"
        严重得多（那是版式，这是剧本）。

        只有**窗口真的显示着**才弹框：构造完就 close 的那条路（测试 / 嵌入 /
        启动失败清理）没有用户在看着，弹框会把进程挂住。
        """
        state = self._close_dirty_state()
        if not (state['graph'] or state['beats'] or state['form']):
            return True
        if not self.isVisible():
            # 图沿用"自动存一份、失败也不拦"；拍的改动没有安全落点
            # （导出要过门禁，不能替用户决定覆盖），只能放弃。
            self._auto_save_layout_on_close()
            return True
        action = self._ask_close_action(state)
        if action == 'cancel':
            self._status('已取消关闭 —— 改动都还在。')
            return False
        if action == 'discard':
            self._log('[关闭] 按你的选择：这次没导出的改动没有保存。')
            return True
        return self._save_for_close(state)

    def _confirm_close_graph(self) -> bool:
        """兼容旧名：关窗询问现在同时管"这一章"（见 `_confirm_close`）。"""
        return self._confirm_close()

    def _close_dirty_state(self) -> dict:
        """
        关窗前"还有什么没保存"：三种来源分别算。

          · `graph`：剧情图的位置 / 连线（`_graph_dirty`，既有判据）；
          · `beats`：这一章的拍与磁盘不一致（会话的 `unsaved_edits`）；
          · `form`：当前这一拍的表单改了、还没按「💾 保存这一拍」。

        三者**分开问**，因为"保存"对它们的意思不同：图存成 JSON，
        拍写回 .rpy，表单先要落到拍上。
        """
        state = {'graph': bool(getattr(self, '_graph_dirty', False)),
                 'beats': False, 'changed': 0, 'added': 0, 'removed': 0,
                 'form': False,
                 'file': str(getattr(self._session, 'script_path', '') or '')}
        try:
            if len(self._session):
                info = self._session.unsaved_edits()
                state['beats'] = bool(info.get('dirty'))
                state['changed'] = int(info.get('changed') or 0)
                state['added'] = int(info.get('added') or 0)
                state['removed'] = int(info.get('removed') or 0)
        except Exception as e:                  # noqa: BLE001 - 判不出来就别拦关闭
            swallow('ui_qt.window_graph_ops._close_dirty_state', e)
        try:
            if len(self._session):
                state['form'] = bool(self._panel.is_modified())
        except Exception as e:                  # noqa: BLE001 - 表单判不了同理
            swallow('ui_qt.window_graph_ops._close_dirty_state.form', e)
        return state

    def _beats_state_text(self, state) -> str:
        """「这一章改了几拍」那一行（数字对不上就**不报数**，不猜）。"""
        name = Path(state['file']).name if state.get('file') else '这一章'
        parts = []
        if state.get('changed'):
            parts.append(f'{state["changed"]} 拍内容改了')
        if state.get('added'):
            parts.append(f'{state["added"]} 拍是新加的')
        if state.get('removed'):
            parts.append(f'{state["removed"]} 拍删掉了')
        what = '、'.join(parts) or '有改动'
        return f'{name}：{what}，还没导出（点「⤓ 导出 .rpy」才落盘）。'

    def _ask_close_action(self, state) -> str:
        """
        关窗询问：返回 `save` / `discard` / `cancel`。

        只有图脏（老情况）时走原来那个三选一，措辞与按钮都不变 —— 那是
        用户已经熟悉的一步，不要因为多了"这一章"就把它换掉。
        """
        if state['graph'] and not (state['beats'] or state['form']):
            return self._ask_close_graph_action()
        box = QMessageBox(self)
        box.setWindowTitle('还有没保存的改动')
        box.setIcon(QMessageBox.Question)
        lines = []
        if state['beats']:
            lines.append(self._beats_state_text(state))
        if state['form']:
            lines.append('当前这一拍的表单改了，还没按「💾 保存这一拍」。')
        if state['graph']:
            lines.append('剧情图：模块位置 / 连线只在内存里。')
        box.setText('\n'.join(lines))
        box.setInformativeText('「保存并退出」会先把这一章写回原文件，再存剧情图。')
        save = box.addButton('保存并退出', QMessageBox.AcceptRole)
        box.addButton('不保存，直接退出', QMessageBox.DestructiveRole)
        cancel = box.addButton('取消', QMessageBox.RejectRole)
        box.setDefaultButton(save)
        box.setEscapeButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return 'save'
        if clicked is None or clicked is cancel:
            return 'cancel'
        return 'discard'

    def _save_for_close(self, state) -> bool:
        """
        用户选了"保存"：**先写这一章、再存图**；任一步失败就**不关窗**。

        顺序不能反：图的布局里带着"当前章节 / 节点"，而章节的拍才是剧本
        的真源 —— 先把剧本写稳，再记版式。
        """
        if state['form'] or state['beats']:
            try:
                self.apply_current_beat()
            except Exception as e:              # noqa: BLE001
                QMessageBox.warning(
                    self, '保存失败',
                    f'当前这一拍的表单没能写进去：{e}\n\n先不关窗，改动还在。')
                return False
            try:
                still = self._session.unsaved_edits()
            except Exception as e:              # noqa: BLE001 - 判不了就按"要写"处理
                swallow('ui_qt.window_graph_ops._save_for_close.check', e)
                still = {'dirty': True}
            if still.get('dirty') and not self._save_chapter_for_close():
                return False
        if state['graph']:
            return self._save_layout_for_close()
        return True

    def _save_chapter_for_close(self) -> bool:
        """
        关窗时把这一章**写回它原来的文件**（不再弹"导出到哪"）。

        为什么不在关窗路上再弹一次文件对话框：用户刚才已经在"保存并退出"
        上表过态，再叠一个对话框只会让人以为程序卡住。目标就是
        `default_output_path()`（有源文件 → 原文件；没有源 → `build/renpy/`）。

        失败（门禁拦下 / 语法错）**不关窗**：改动还在内存里，
        关掉才是真的丢。
        """
        target = self._session.default_output_path()
        try:
            res = self._session.export(target)
        except Exception as e:                  # noqa: BLE001
            QMessageBox.warning(self, '导出失败',
                                f'{e}\n\n先不关窗，改动还在。')
            return False
        if not res.get('ok'):
            blocking = res.get('blocking') or ['（未知原因）']
            QMessageBox.warning(
                self, '这一章没能写出去',
                '\n'.join(str(b) for b in blocking[:4])
                + '\n\n先不关窗 —— 改动还在内存里。')
            return False
        # 写出去了 → 内存这一刻与磁盘一致（关窗 / 状态栏的"未导出"靠这条基线）
        self._session.take_edits_baseline()
        self._status_bar.set_dirty(False, str(res.get('path') or target))
        self.resync_after_export(res)
        self._log(f'[导出] 关窗前已写回 {Path(target).name}'
                  f'（{res.get("note", "")}）')
        return True

    # ------------------------------------------------------------------
    #  诊断详情（P1-5）
    # ------------------------------------------------------------------

    def show_diagnostics(self):
        """打开诊断详情（分组 + 聚合 + 复制）。"""
        from storygraph.core import safeguard
        from storygraph.ui_qt.diagnostics import DiagnosticsDialog
        items = self._diag_items or safeguard.recent(1000)
        dlg = DiagnosticsDialog(self, items=items, total=safeguard.total())
        dlg.exec()

    def copy_diagnostics(self):
        """把最近一次诊断摘要复制到剪贴板。"""
        from storygraph.core import safeguard
        from storygraph.ui_qt.diagnostics import format_report
        try:
            text = format_report(self._diag_items or safeguard.recent(1000),
                                 safeguard.total())
            QApplication.clipboard().setText(text)
            self._status('已复制诊断摘要到剪贴板。')
        except Exception as e:                  # noqa: BLE001
            swallow('ui_qt.window_graph_ops.copy_diagnostics', e)

    def on_graph_structure_changed(self):
        """命令改完图结构后的回调（命令对象约定的视图接口之一）。"""
        self._set_graph(self.graph)
    def on_graph_content_changed(self, nid=None):
        """命令改了块内容（变量）后的回调。"""
        self._set_graph(self.graph)
    def on_current_changed(self):
        """「设为当前章节」变了之后的回调。"""
        self._set_graph(self.graph)
    def update_node_position(self, nid):
        """命令把块的坐标改了 → 画布同步（整图重建，够快也够简单）。"""
        self._set_graph(self.graph)
    def _apply_layout_data(self, data):
        """`LoadLayoutCommand` 要的接口：把布局 dict 变成当前的图。"""
        from storygraph.app.editor import graph_from_layout_dict
        self._set_graph(graph_from_layout_dict(data))
    def _run_command(self, command):
        """执行一条图命令并记进历史（所有结构改动都要走这里）。"""
        try:
            self._history.execute_command(command)
        except Exception as e:                # noqa: BLE001
            self._log(f'[撤销] 这条操作没能记进历史：{e}')
            return False
        self._sync_history_buttons()
        return True
    def undo_graph(self):
        """图级撤销（Ctrl+Z，结构页）。"""
        if not self._history.undo_stack:
            self._status('没有可撤销的图操作。')
            return
        self._history.undo()
        self._set_graph(self.graph)
        self._sync_history_buttons()
        self._log('[撤销] 已撤销上一步图操作')
        self._status('↩ 已撤销上一步图操作')
    def redo_graph(self):
        """图级重做（Ctrl+Y，结构页）。"""
        if not self._history.redo_stack:
            self._status('没有可重做的图操作。')
            return
        self._history.redo()
        self._set_graph(self.graph)
        self._sync_history_buttons()
        self._log('[重做] 已重做上一步图操作')
        self._status('↪ 已重做上一步图操作')
    def _sync_history_buttons(self):
        for name, stack in (('_undo_graph_btn', self._history.undo_stack),
                            ('_redo_graph_btn', self._history.redo_stack)):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.setEnabled(bool(stack))
    def _report_missing_chapters(self, graph):
        """把"被跳到、但文件还没导入"的章节写进控制台（并提示补全）。"""
        try:
            info = self._session.missing_chapter_files(graph)
        except Exception as e:                     # noqa: BLE001
            swallow('ui_qt.window_graph_ops._report_missing_chapters', e)
            return
        files = info.get('files') or []
        unknown = info.get('unknown') or []
        if files:
            names = '、'.join(Path(p).name for p in files[:4])
            more = f' 等 {len(files)} 个' if len(files) > 4 else ''
            self._log(f'[提示] 图里还有 {len(files)} 个章节文件没导入，'
                      f"它们和现在这些章节之间有跳转：{names}{more}"
                      f'　→　点工具栏「🔗 补全缺失章节…」一起导入，'
                      f'连线才会画出来（或右键空白 → 补全缺失章节…）')
        if unknown:
            names = '、'.join(unknown[:6])
            more = f' 等 {len(unknown)} 个' if len(unknown) > 6 else ''
            self._log(f'[提示] 有 {len(unknown)} 个跳转目标在项目里找不到'
                      f'对应文件（可能是脚本里写错了，或用了动态跳转）：'
                      f'{names}{more}')
    def complete_related_chapters(self):
        """
        「🔗 补全缺失章节…」：把"图上被跳到、但还没导入"的文件一起导进来。

        先给**预览**（会导入哪些文件、每个文件带哪些 label），确认后再导入。
        用户 2026-10-22 分不清它和"重载"：
        > 补全相关章节是根据 call/jump 抓其他剧情图？重载是让无源变有源？

        答案写进预览标题里：补全 = 找缺失文件；刷新 = 重读已有文件。
        """
        try:
            info = self._session.missing_chapter_files(self.graph)
        except Exception as e:                # noqa: BLE001
            self._status(f'查找相关章节失败：{e}')
            return
        files = list(info.get('files') or [])
        unknown = list(info.get('unknown') or [])
        if not files:
            self._status('没有需要补全的章节 —— 图上被跳到的 label 都有来源了'
                         '（或者项目里根本找不到它们）。')
            self._log('[补全] 没有找到缺失文件'
                      + (f'；这些 label 项目里也没有：{"、".join(unknown[:6])}'
                         if unknown else ''))
            return
        from storygraph.parsing.discover import labels_in_file
        from storygraph.ui_qt.dialogs import SyncPreviewDialog
        rows = []
        for p in files:
            labels = labels_in_file(p)
            rows.append(f'{Path(str(p)).name}  →  '
                        + ('、'.join(labels) if labels else '（读不出 label）')
                        + f'\n    {p}')
        detail = '\n'.join(rows)
        if unknown:
            detail += ('\n\n这些跳转目标在项目里找不到对应文件'
                       '（脚本可能写错了，或用了动态跳转）：\n  '
                       + '、'.join(unknown))
        dlg = SyncPreviewDialog(
            self, '补全缺失章节',
            f'将从项目里导入 {len(files)} 个还没进图的文件。\n'
            f'它们声明了图上被 jump / call 的 label —— 导入后连线才画得出来。',
            detail, ok_text='导入这些文件',
            warn='只补齐缺失文件；已经在图上的章节不会重新读取。'
                 '要跟上磁盘上的改动，用「刷新已导入章节…」。')
        if dlg.exec() != QDialog.Accepted:
            self._status('已取消补全（图没有改动）。')
            return
        self._log(f'[补全] 导入相关章节 {len(files)} 个…')
        self.import_chapter_files(files)
    def reload_imported_chapters(self):
        """
        「🔁 刷新已导入章节…」：重读**已导入的那些**文件（范围不变）。

        与"补全"的区别先给预览：会新增哪些 label、源里删掉的哪些 label
        会从图上移除。不是"再合并一次"。
        """
        files = self._session.graph_files(self.graph)
        if not files:
            self._status('剧情图还是空的 —— 先用「➕ 导入章节 .rpy」加几章。')
            return
        from storygraph.parsing.discover import labels_in_file
        from storygraph.ui_qt.dialogs import SyncPreviewDialog
        have: dict = {}
        for node in (getattr(self.graph, 'nodes', {}) or {}).values():
            fp = str(getattr(node, 'file_path', '') or '')
            if fp:
                have.setdefault(str(Path(fp)), set()).add(
                    str(getattr(node, 'label_name', '') or ''))
        rows = []
        added_total = removed_total = 0
        for p in files:
            key = str(Path(str(p)))
            old = set(have.get(key) or ())
            new = set(labels_in_file(p))
            if not new:
                # 读不出 label 时**不预告删除**：导入那条路在解析有错时也
                # 不删节点（宁可留幽灵，也不按半份 AST 清图）。
                rows.append(f'{Path(str(p)).name}  →  ⚠ 读不出 label，'
                            f'这次不会删任何已有模块')
                continue
            added = sorted(new - old)
            removed = sorted(old - new)
            added_total += len(added)
            removed_total += len(removed)
            if not added and not removed:
                rows.append(f'{Path(str(p)).name}  →  没有变化')
                continue
            pieces = []
            if added:
                pieces.append('新增 ' + '、'.join(added[:8])
                              + (f' 等 {len(added)} 个' if len(added) > 8 else ''))
            if removed:
                pieces.append('源里已删除 ' + '、'.join(removed[:8])
                              + (f' 等 {len(removed)} 个' if len(removed) > 8 else ''))
            rows.append(f'{Path(str(p)).name}  →  ' + '；'.join(pieces))
        dlg = SyncPreviewDialog(
            self, '刷新已导入章节',
            f'将重新读取图上已经导入的 {len(files)} 个文件。\n'
            f'预计新增 {added_total} 个 label / 移除 {removed_total} 个'
            f'（源文件里已经不存在的）。',
            '\n'.join(rows) or '（没有可预览的变化）',
            ok_text='重新读取',
            warn='只重读图上已有的文件；不会把无源节点变成有源。'
                 '要补缺失文件，用「补全缺失章节…」。')
        if dlg.exec() != QDialog.Accepted:
            self._status('已取消刷新（图没有改动）。')
            return
        self._log(f'[刷新] 重新解析已导入的 {len(files)} 个章节…')
        self.import_chapter_files(files)
    def clear_graph(self):
        """清空剧情图（**只清图**：资源表、项目根、会话都不动；可撤销）。"""
        n = len(getattr(self.graph, 'nodes', {}) or {})
        if not n:
            self._status('剧情图本来就是空的。')
            return
        from storygraph.graph.commands import ClearGraphCommand
        self._run_command(ClearGraphCommand(self))
        self._log(f'[清空] 剧情图已清空（原来 {n} 个模块）；'
                  f'资源表与项目设置没动。')
        self._status(f'已清空剧情图（{n} 个模块）。'
                     f'资源表与项目设置没动；要恢复点「↩ 撤销」或重新导入。')
    def remove_chapter_from_graph(self, nid):
        """把某个模块**所属的那一章**从图上移除（不动磁盘上的 .rpy）。"""
        node = (self.graph.nodes or {}).get(nid) if self.graph else None
        if node is None:
            return
        fp = getattr(node, 'file_path', None)
        if not fp:
            # 手工新建的块（没有源文件）→ 只删这一个（走命令，可撤销）
            from storygraph.graph.commands import RemoveNodeCommand
            self._run_command(RemoveNodeCommand(self, nid))
            self._log(f'[移除] {node.label_name}（没有源文件，只删这一个块）')
            self._status(f'已从图上移除「{node.label_name}」（它没有源文件）。')
            return
        name = Path(str(fp)).name
        siblings = sum(1 for n in (self.graph.nodes or {}).values()
                       if str(getattr(n, 'file_path', '') or '') == str(fp))
        if siblings > 1:
            if QMessageBox.question(
                    self, '从剧情图移除这一章',
                    f'「{name}」在图上有 {siblings} 个模块（同一个文件里的多个 '
                    f'label），要一起移除吗？\n\n'
                    f'（只改内存里的图，不动磁盘上的 .rpy）'
            ) != QMessageBox.Yes:
                return
        res = self._session.drop_chapters(self.graph, [fp])
        self._set_graph(self.graph)
        self._log(f'[移除] {name} → 移除 {res["removed_nodes"]} 个模块；'
                  f'图上还剩 {res["nodes"]} 个模块')
        self._status(f'已从剧情图移除「{name}」的 {res["removed_nodes"]} 个模块；'
                     f'图上还剩 {res["nodes"]} 个模块。')
