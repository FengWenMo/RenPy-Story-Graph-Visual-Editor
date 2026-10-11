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
from storygraph.ui_qt.graph_canvas import GraphCanvas
from storygraph.ui_qt.sprite_grid import SpriteGridWidget
from storygraph.ui_qt.status import Notifier, Toast
from storygraph.ui_qt.thumb_loader import ThumbLoader


__all__ = ['GraphOpsMixin']


class GraphOpsMixin:
    """
    剧情图那一半：结构分析 / 章节导入导出 / 布局存取 / 图级撤销。

    它要求宿主（`MainWindowQt`）提供 `REQUIRES` 里那些成员 ——
    多半是**主类刻意留下**的：界面骨架、页切换、状态栏、
    静默失败上报、图的所有权。
    """

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    REQUIRES = ('ASSET_FIELD_KINDS', 'PAGE_GRAPH', '_canvas', '_console', '_ensure_registry', '_file_path', '_history', '_loader', '_log', '_panel', '_project_root', '_record_applied', '_refresh_asset_choices', '_registry_provider', '_session', '_set_graph', '_set_page', '_status', 'clear_console', 'engine', 'graph')

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
        res = self._session.set_node_variables(self.graph, nid, dlg.variables)
        if not res.get('ok'):
            self._status(f'保存失败：{res.get("error")}')
            return
        # 变量改动也进历史（Ctrl+Z 能撤回来）。这里操作**已经生效**，
        # 所以只记历史、不重复 execute；改动前的文本用 `old_ops` 显式给，
        # 否则命令会把"撤销"恢复成新值。
        try:
            from storygraph.graph.commands import UpdateNodeOperationsCommand
            cmd = UpdateNodeOperationsCommand(
                self, nid, list(self.graph.nodes[nid].operations),
                old_ops=before)
            self._record_applied(cmd)
        except Exception:                     # noqa: BLE001
            swallow('main_window.edit_node_ops')
        self._set_graph(self.graph)          # 摘要 / 连线可能变了
        self._log(f'[变量] {info["label"]}：剧情变量 {res["variables"]} 条'
                  f'（另有 {res["kept_tech"]} 条实现细节原样保留）')
        self._status(f'已保存「{info["label"]}」的剧情变量'
                     f'（{res["variables"]} 条）。')
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
        """「➕ 导入章节 .rpy」：多选 → 累加进剧情图。"""
        start = str(Path(self._project_root or '.') / 'game')
        paths, _ = QFileDialog.getOpenFileNames(
            self, '选择要导入剧情图的章节脚本（可多选）', start,
            "Ren'Py 脚本 (*.rpy);;所有文件 (*)")
        if not paths:
            return
        self.import_chapter_files(paths)
    def import_chapter_files(self, paths) -> dict:
        """
        把选中的 `.rpy` 加进剧情图（**累加**）。语义全在
        `EditorSession.import_chapters`，这里只管把结果塞进画布**并写进控制台**。

        用户 2026-10-03："导入的时候没有反馈！根本不知道导入了什么……"
        所以这里**逐个文件**报"来了几个模块 / 几条连线"，而不是只报一个总数。
        """
        old_graph = self.graph.copy() if self.graph is not None else None
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

        names = '、'.join(Path(p).name for p in res['files'][:3])
        more = f' 等 {len(res["files"])} 个文件' if len(res['files']) > 3 else ''
        self._status(
            f'✔ 已导入 {len(res["files"])} 章（{names}{more}）：'
            f'新增模块 {res["added_nodes"]} 个 / 连线 {res["added_edges"]} 条，'
            f'图上共 {res["nodes"]} 个模块 · {res["edges"]} 条连线')
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
    def learn_custom_statement(self):
        """
        「🧩 自定义模块 / 语句」：教工具认识项目自己的写法。

        用户 2026-10-10："我并没有找到一个入口可以教这个工具学会我自定义的
        项目模块……至少我想要个能够自定义的模块入口放在最上方"。

        这条链的每一步都是既有能力，缺的只是入口：
        扫项目找未声明写法 → 填声明 → `declare_statement`（唯一写入口）→
        清单缓存作废 → 表格立刻多出参数列与候选值。
        """
        if not self._project_root:
            QMessageBox.information(
                self, '提示',
                '先「📁 打开项目」—— 声明要写进项目根的 '
                'storygraph.rules.json。')
            return
        from storygraph.authoring.learn_statement import declaration_preview
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
        if dlg.exec() != QDialog.Accepted:
            return
        # —— 词组（可选）：与语句声明一起写 ——
        wrote_group = False
        group_rows = dlg.groups_to_write()
        if group_rows:
            gres = SR.declare_attr_groups(root, group_rows)
            if gres.get('ok'):
                wrote_group = True
                self._log(f'[自定义] 词组 {gres["count"]} 组 → {gres["path"]}')
            else:
                self._log(f'[自定义] ✕ 词组没写：{gres.get("reason")}')
        built = dlg.result_declaration()
        if not built.get('ok'):
            for w in built.get('warnings') or []:
                self._log(f'[自定义] ✕ {w}')
            if wrote_group:
                self._status('词组写好了（语句声明没写：看控制台）')
                self._after_learn_refresh()
            else:
                self._status('没写：' + '；'.join(built.get('warnings') or ['?']))
            return
        res = SR.declare_statement(root, built['name'], label=built['label'],
                                   columns=built['columns'],
                                   block=built['block'], docs=built['docs'])
        if not res.get('ok'):
            self._log(f'[自定义] ✕ 没写进清单：{res.get("reason")}')
            self._status(f'没写：{res.get("reason")}')
            return
        for w in built.get('warnings') or []:
            self._log(f'[自定义] ⚠ {w}')
        verb = '更新' if res.get('action') == 'updated' else '新增'
        self._log(f'[自定义] {verb}声明 {built["name"]}（'
                  f'{len(built["columns"])} 个参数）→ {res.get("path")}')
        self._log(declaration_preview(built, path=str(res.get('path') or '')))
        self._after_learn_refresh()
        self._status(f'✔ {verb}声明 {built["name"]}：'
                     f'参数 {len(built["columns"])} 个已进表格列'
                     + ('；词组也写好了（立绘候选会分栏）' if wrote_group else '')
                     + '（重新导入那几章即可看到效果）')

    def _after_learn_refresh(self):
        """
        教完一条之后刷新：**清清单缓存** + 重算符号表 + 推词组给卡片。

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
        self._log(f'[布局] 已加载 {name}：{res["nodes"]} 个模块 / '
                  f'{res["edges"]} 条连线')
        self._status(f'📂 已加载布局 {name}（{res["nodes"]} 块 / '
                     f'{res["edges"]} 线）')
        return res['path']
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
        tops = res.get('top_characters') or []
        if tops:
            lines.append('【角色台词量】')
            for row in tops:
                if isinstance(row, dict):
                    lines.append(f'  {row.get("name") or row.get("character")}：'
                                 f'{row.get("count") or row.get("lines")}')
                else:
                    lines.append(f'  {row}')
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
                      f'　→　点工具栏「🔗 补全相关章节」一起导入，'
                      f'连线才会画出来（或右键空白 → 补全相关章节）')
        if unknown:
            names = '、'.join(unknown[:6])
            more = f' 等 {len(unknown)} 个' if len(unknown) > 6 else ''
            self._log(f'[提示] 有 {len(unknown)} 个跳转目标在项目里找不到'
                      f'对应文件（可能是脚本里写错了，或用了动态跳转）：'
                      f'{names}{more}')
    def complete_related_chapters(self):
        """
        「🔗 补全相关章节」：把"图上被跳到、但还没导入"的文件一起导进来。

        这样"导入 → 看到线"就闭环了：用户导入 `chapter1_1/1_2/1_3` 后，
        一点就把 `chapter_controller.rpy` 补进来，9 条连线立刻出现。
        """
        try:
            info = self._session.missing_chapter_files(self.graph)
        except Exception as e:                # noqa: BLE001
            self._status(f'查找相关章节失败：{e}')
            return
        files = list(info.get('files') or [])
        if not files:
            self._status('没有需要补全的章节 —— 图上被跳到的 label 都有来源了'
                         '（或者项目里根本找不到它们）。')
            return
        self._log(f'[补全] 导入相关章节 {len(files)} 个…')
        self.import_chapter_files(files)
    def reload_imported_chapters(self):
        """重新解析**已导入的那些**文件（范围不变，用于跟上磁盘上的改动）。"""
        files = self._session.graph_files(self.graph)
        if not files:
            self._status('剧情图还是空的 —— 先用「➕ 导入章节 .rpy」加几章。')
            return
        self._log(f'[重载] 重新解析已导入的 {len(files)} 个章节…')
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
