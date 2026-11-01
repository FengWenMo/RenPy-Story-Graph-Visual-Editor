"""
storygraph.ui_qt.window_export.py
=================================
`MainWindowQt` 的**导出 / 体检 / 舞台预览 / 剧本表 —— 与外部世界打交道的那一批。**

**为什么是 mixin、当初怎么拆的**：`docs/代码地图.md` §三·五 讲一次，
不再在 6 个 `window_*.py` 里各抄一遍（从前这 22 行在 6 个文件里逐字重复）。

与主类的隐式依赖
----------------
见每个类的 `REQUIRES`：那是**实测**（静态扫 `self.*`）出来的，
由 `tests/test_main_window_mixins.py` 守着。
"""
from __future__ import annotations

from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.app import export_plan
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

    ⚠ 判据本身搬去 `app.export_plan.overridable`（2026-11-01，服务化第三
    刀）：`window_graph_ops` 里**又手写过一遍**同一句话，两处漂移就会
    "同一个被拦下的导出，一个入口给逃生舱、另一个不给"。这里只剩转发。
    """
    return export_plan.overridable(res)


def _scan_stage_identity(host, beats):
    """
    导出前的**身份门禁**：这几条 `show` 写的名字，项目里真的有吗？

    判据在 `app.stage_identity_guard`（只读、不猜）：
      · 已知角色 tag（角色表「立绘 tag」/ 映射 `characters` / 引擎事实）；
      · 项目 `.rpy` 里声明过的图像名（`_from_script` / ATL / layeredimage）；
      · `bg`/`cg`/`ui` 这类真图层名，以及 `show screen` 这种目标关键字。
    不是这些 → 报出来让人看（`show refusing` 就是这一类：预览图/文件 stem
    被当成名字写了进去，游戏里没有这个图像）。

    拿不到资源表 / 角色表时**不判**（返回 `degraded` 空结果）—— 白名单残缺时
    报出来的大半是假警报，那比不报更坏。任何异常也一律当"这次判不了"：
    **检查失败不拦导出**（与舞台那条同一个口径），绝不因为一次检查把导出弄挂。

    ⚠ **为什么是模块级函数而不是 mixin 方法**：`tests/test_stage_suspect.py`
    把 `_review_stage_suspects` 当未绑定函数、拿一个**裸 QWidget** 当 self 调
    （那个测试只验证"问没问、改没改"）。写成 `self._scan_stage_identity(...)`
    的话裸 QWidget 上没有这个方法，测试替身当场 AttributeError —— 而那和线上
    无关。模块级函数让"宿主只提供属性"的老式替身继续能用。
    """
    try:
        from storygraph.assets import asset_identity as AI
        from storygraph.app import stage_identity_guard as SG

        provider = getattr(host, '_registry_provider', None)
        registry = provider() if callable(provider) else None
        root = str(getattr(host, '_project_root', '') or '')
        ctx = AI.project_context(registry, root)
        result = SG.scan_stage_identity(
            beats, project_root=root,
            known_tags=ctx.get('known_tags') or frozenset(),
            known_chars=ctx.get('known_chars') or {},
            mapping=ctx.get('mapping') or {},
            all_rows=ctx.get('rows') or {})
        log = getattr(host, '_log', None)
        if callable(log):
            if result.degraded:
                log('[导出] 身份检查跳过：没拿到角色表/资源表，'
                    '无法判断 show 的名字是否存在')
            elif result:
                log(f'[导出] 身份检查：{len(result)} 条 show 的名字'
                    f'项目里找不到（共看 {result.checked} 条）')
        return result
    except Exception as e:                      # noqa: BLE001 - 检查失败不拦导出
        swallow('main_window._scan_stage_identity', e)
        return None


class WindowExportMixin:
    """
    导出 / 体检 / 舞台预览 / 剧本表 —— 与外部世界打交道的那一批。

    它要求宿主（`MainWindowQt`）提供 `REQUIRES` 里那些成员 ——
    多半是**主类刻意留下**的：界面骨架、页切换、状态栏、
    静默失败上报、图的所有权。
    """

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    #:
    #: `_reload_list`（2026-10-19，审计 P1-7 收尾）：从剧本表写回**本章**之后
    #: 要把左边卡片列表重建 —— 内存已经从磁盘重载，列表还挂着旧的行。
    REQUIRES = ('PAGE_CARD', '_canvas', '_log', '_project_root',
                '_registry_provider', '_reload_list', '_report_swallowed',
                '_session', '_status', '_status_bar', '_swallow_mark',
                '_table_mtimes', 'apply_current_beat', 'autosave_clear',
                'current_page', 'graph', 'resync_after_export')

    def export_script(self):
        """导出这一章（编译 + 增量补丁 + 安全门禁，全在会话里）。"""
        if not len(self._session):
            # 这个动作现在两页顶部都有（用户 2026-10-21），所以**结构页上点了
            # 也要说清"差哪一步"** —— 不能只有一句"没有可导出的内容"。
            self._status('还没有载入章节：先在图上双击一个模块进卡片页'
                         '（或点「✎ 直接编辑单章」）载入一章，再导出。')
            return
        mark = self._swallow_mark()
        # **只在卡片页提交表单**（2026-10-23）：结构页按 Ctrl+S 也会走到这里，
        # 而卡片页隐藏时的"当前表单"可能是上一张卡的旧内容 —— 那会变成
        # 一次用户看不见的写入（正是 `test_card_actions_are_disabled_...`
        # 要防的那类事故）。
        if self.current_page() == self.PAGE_CARD:
            self.apply_current_beat()
        # **导出前问一句**（2026-10-29）：Stage 列里看着不像 Ren'Py 语句的行，
        # 要不要挪到「备注」列。人点「取消」= 一个字节都不写（返回 False）。
        if not self._review_stage_suspects():
            return
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
            # 告警（产物写在 game/ 里之类）**不是**这次拒写的原因 ——
            # 单列一段，别让人以为"挪个位置就好了"（审计第四轮 P2）。
            warns = res.get('warnings') or []
            msg = '\n'.join(blocking)
            if warns:
                # ⚠ `QMessageBox` 不渲染 Markdown —— 别在这里写 `**粗体**`
                # （同一类坑见 `_confirm_force_overwrite` 的注释）。
                msg += ('\n\n以下是提示，不是这次被拦的原因：\n'
                        + '\n'.join('· ' + w for w in warns))
            QMessageBox.warning(self, '生成被门禁拦截', msg)
            self._report_swallowed(mark)
            return
        # 写出去了 → 内存这一刻与磁盘一致（关窗 / 状态栏的"未导出"靠这条基线）。
        # 原地导出会在 `export()` 里重载内存（那时基线已随 `load` 更新过），
        # 导出到别的路径则不重载 —— 两种都在这里对齐一次。
        try:
            self._session.take_edits_baseline()
        except Exception as e:                  # noqa: BLE001 - 记不上基线不影响导出
            swallow('main_window.export_script.baseline', e)
        self._status_bar.set_dirty(False, str(res.get('path') or path))
        # **原地导出之后内存会重新对齐到磁盘**（见
        # `session_export._reload_after_inplace_write`）：拍的粒度可能变
        # （只有注释/代码的卡会并进后面那张）→ 卡片列表要跟着刷新，
        # 否则界面还显示旧的拍、点哪张都对不上。
        self.resync_after_export(res)
        # 写进文件了 → 自动保存那份快照已经没有可恢复的东西（2026-10-23）。
        self.autosave_clear()
        todos = res.get('todos') or 0
        warn = f'，缺资源 {todos} 处（见 TODO）' if todos else ''
        self._log(f'[导出] 已写入 {Path(path).name}'
                  f'（{res.get("note", "")}{warn}）')
        self._status(f'✔ 已导出 {Path(path).name}（{res.get("note", "")}{warn}）')
        # **写成功了也把提示说出来**（审计第四轮 P2）：`OUTSIDE_BUILD_DIR`
        # 这类 warn 从前只在"拒写"时混进 blocking 列表出现 —— 写成功时
        # 它干脆消失了，而"产物放在 game/ 里会和原文抢 label"恰恰是
        # 用户**下次**才会踩到的事。这里至少让它留在日志里。
        _warns = res.get('warnings') or []
        if _warns:
            self._log('[导出] 提示：' + '；'.join(str(w) for w in _warns[:4]))
        self._report_swallowed(mark)

    def _review_stage_suspects(self) -> bool:
        """
        导出前查一遍「舞台（高级）」和「立绘」列，返回 `False` = 人点了取消（零写）。

        两类问题（判据各自在模块里，一行都不在这里）：

          · `story.stage_suspect` —— 舞台列里"看着不像 Ren'Py 语句"的行
            （宽进：宁可多问一次，不要漏一句），可以挪到「备注」列；
          · `app.stage_identity_guard` —— `show` 写的名字项目里根本找不到
            （2026-10-30 用户：**不许静默写出 `show refusing`**），只能改名字。

        两类**并排摆进同一个弹窗**（同一个流程、同一个"原样导出 / 取消"决定），
        别另造一套框 —— 一次导出最多问一次，问完就写。

        为什么**默认原样导出**而不是默认处理：挪走/改名都会改用户填的格子。
        工具不该把"改用户的表"当默认动作 —— 要改得人自己点。
        """
        from storygraph.story import stage_suspect as SS

        beats = getattr(self._session, 'beats', None)
        if not beats:
            return True
        try:
            res = SS.scan(beats,
                          project_root=getattr(self, '_project_root', '') or '')
        except Exception as e:                  # noqa: BLE001 - 检查失败不拦导出
            swallow('main_window._review_stage_suspects', e)
            res = SS.ScanResult()
        ident = _scan_stage_identity(self, beats)
        if not res and not ident:
            return True

        from PySide6.QtWidgets import QDialog
        from storygraph.ui_qt import dialog_stage_suspect as D

        dlg = D.StageSuspectDialog(self, res or SS.ScanResult(), identity=ident)
        if dlg.exec() != QDialog.Accepted:
            self._status('已取消导出 —— 什么都没写')
            self._log('[导出] 已取消（未写任何文件）')
            return False
        if dlg.decision == D.MOVE:
            picked = dlg.selected()
            n = SS.move_to_comment(beats, picked)
            self._log(f'[导出] 把 {n} 行舞台内容移到「备注」列（原格内容不丢）')
            self._status(f'已把 {n} 行移到备注，接着导出…')
            try:
                # 内存改了、磁盘还没写 → 状态栏要显示"未导出"，
                # 否则人以为已经存过（这一条与导出成功后的 set_dirty(False) 配对）。
                self._status_bar.set_dirty(True, '')
            except Exception as e:              # noqa: BLE001
                swallow('main_window._review_stage_suspects.dirty', e)
        return True

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
        # ⚠ `QMessageBox` 只认纯文本 / HTML，**不渲染 Markdown** —— 写成
        # `**手写**` 用户看到的就是四个星号（同一类坑上一轮在删除确认框里
        # 踩过）。强调改用中文引号。
        box.setText(f'{name} 看起来是「手写」的 .rpy（没有生成标记）。')
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
    def _review_table_stage_suspects(self, table_path) -> bool:
        """
        从表导出前的同一道检查，返回 `False` = 人点了取消（零写）。

        与 `_review_stage_suspects` 的唯一差别：**不给"移到备注"这个动作**。
        这条路的真源是那张 `.xlsx`，工具在导出前顺手改用户的工作簿风险更大
        （还有别的 sheet、格式、公式）—— 把"哪几拍、原文是什么"说清楚，
        让人回 Excel 自己挪。

        身份门禁（`show` 的名字项目里找不到）也在**同一个**弹窗里查 ——
        从表导回同样会写出非法 `show`，不能因为换了条路就少一道。
        """
        from storygraph.story import stage_suspect as SS

        try:
            from storygraph.story.excel_import import import_beats_from_excel

            beats = import_beats_from_excel(table_path)
        except Exception as e:                  # noqa: BLE001 - 检查失败不拦导出
            swallow('main_window._review_table_stage_suspects', e)
            return True
        try:
            res = SS.scan(beats,
                          project_root=getattr(self, '_project_root', '') or '')
        except Exception as e:                  # noqa: BLE001
            swallow('main_window._review_table_stage_suspects', e)
            res = SS.ScanResult()
        ident = _scan_stage_identity(self, beats)
        if not res and not ident:
            return True

        from PySide6.QtWidgets import QDialog
        from storygraph.ui_qt import dialog_stage_suspect as D

        dlg = D.StageSuspectDialog(self, res or SS.ScanResult(),
                                   allow_move=False, identity=ident)
        if dlg.exec() != QDialog.Accepted:
            self._status('已取消导出 —— 什么都没写')
            self._log('[导出] 已取消（未写任何文件）')
            return False
        return True

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

        # 引擎事实（可选）：给了才在「立绘」格挂"引擎没认的属性"批注。
        # 取不到**不该让导出失败** —— 批注只是附加信息，空 dict = 一个都不挂。
        facts = {}
        try:
            facts = self._session.engine_facts()
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.window_export.engine_facts', e)

        res = tables.generate_table(script, registry=self._registry_provider(),
                                    project_root=self._project_root,
                                    facts=facts)
        if not res['ok']:
            QMessageBox.critical(self, '生成剧本表失败', res['error'])
            return
        table_path = res['table_path']
        tables.remember_table_mtime(self._table_mtimes, table_path)

        # **重建覆盖了旧表 → 把人手写的东西保住了多少说出来**（P0-E）。
        # 这张表就是策划的工作台，一句"我都留着"比任何文档都能让人安心往里写。
        if res.get('overwrote'):
            kept = res.get('preserved') or {}
            bits = []
            if kept.get('sheets'):
                bits.append(f'{kept["sheets"]} 张你自己加的页')
            if kept.get('columns'):
                bits.append(f'{kept["columns"]} 列你自己加的列')
            if kept.get('formats'):
                bits.append(f'{kept["formats"]} 处底色/批注')
            if bits:
                self._log('[剧本表] 已保住你在表里手写的内容：'
                          + '、'.join(bits)
                          + (f'；旧表备份在 {Path(kept["backup"]).name}'
                             if kept.get('backup') else ''))
            if kept.get('unmatched'):
                self._log(f'[剧本表] 有 {kept["unmatched"]} 行在你自加的列里'
                          f'找不到对应拍了（那几拍已不在表里），值留空')

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

        # **导出前先看一眼**（2026-10-29，同 `export_script`）。
        # 这条路和卡片页的差别：真源是那张 `.xlsx`，工具不在这里改用户的工作簿
        # （还有别的 sheet / 格式），只把"第几拍、原文是什么"指出来让人自己挪。
        if not self._review_table_stage_suspects(table_path):
            return

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
        # **写回闭环**（2026-10-19，审计 P1-7 收尾）：现在默认写回的就是
        # 这一章自己的 .rpy，写完之后内存必须重新对齐到刚写出的那份文件 ——
        # 否则界面上还是旧内容，下一次编辑又拿着过期行号去对位
        # （那正是"补丁对不上、退回全量、注释全丢"的起点）。
        try:
            cur = getattr(self._session, 'script_path', None)
            if cur and Path(done['out_path']).resolve() == Path(cur).resolve():
                self._session.load(cur)
                self._reload_list()
                self._status(f'✔ 已写回并重新载入 {Path(cur).name}'
                             f'（{done["beats"]} 个演出单元）')
        except Exception as e:                    # noqa: BLE001 - 同步失败不影响已写出的文件
            swallow('ui_qt.window_export.reload_after_write', e)
    def _source_rpy_for_table(self, table_path):
        """
        增量补丁的底稿 .rpy —— **只看这张表自己**，不看画布选中态。

        ⚠ 2026-10-23（清单 §十二）：从前这里优先用"画布上当前章节"的
        `file_path`，而 `table_path` 参数一次都没读。于是用户选了 ch01.xlsx、
        画布上恰好选中 ch02 的节点，写回目标就成了 `ch02.rpy`；报错还说的是
        ch01 的 label —— 两个 UI 状态之间没有任何一致性检查，用户只能猜。

        现在落点规则只有一条：**表旁边同名 `.rpy`**（`ch01.xlsx` →
        `ch01.rpy`）。没有就返回 None（`app.tables` 会退回 `build/renpy/`，
        不会往 game/ 里凭空多写一份）。用户想写别处，自己指定 `out_path`。
        """
        table_path = Path(table_path)
        cand = table_path.with_suffix('.rpy')
        if cand.exists() and not cand.name.endswith('.generated.rpy'):
            return cand
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

    def refresh_stage_views(self):
        """
        「刷新派生视图」：只重算「舞台快照」「演出时间轴」，不动 Beats。

        用户 2026-10-19（D1）：递推型视图不该公式化，但**必须**有生成时间和
        显式重算入口 —— 否则表里写的东西看起来和真值一样权威，其实只是
        导出那一刻的推导结果。
        """
        from storygraph.app import tables
        start = str(Path(self._project_root or '.') / 'game')
        path, _ = QFileDialog.getOpenFileName(
            self, '选择要重算派生视图的剧本表（.xlsx）', start,
            'Excel 工作簿 (*.xlsx);;所有文件 (*)')
        if not path:
            return
        try:
            from storygraph.story.excel_export import refresh_stage_views
            registry = None
            try:
                registry = self._registry_provider()
            except Exception as e:                # noqa: BLE001 - 没有资源表也能重算
                swallow('ui_qt.window_export.refresh_stage_views.registry', e)
            refresh_stage_views(path, registry)
            tables.remember_table_mtime(self._table_mtimes, Path(path))
            self._status('已重算「舞台快照」「演出时间轴」（表头写着生成时间）。')
        except Exception as e:                    # noqa: BLE001
            QMessageBox.warning(self, '刷新派生视图失败', str(e))
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
            # **从当前那一拍开始**（用户 2026-10-22 连说两次：
            # "我在修改 200 拍的时候，按演出预览，出来的是第一拍，
            #   需要手动跳到第 200 拍"）。`frames` 与 `beats` 一一对应
            # （`build_frames` 就是 `zip(beats, rows)`），拍索引直接可用。
            open_preview(self, self._session.frames(),
                         registry=self._registry_provider(),
                         project_root=self._project_root,
                         start_index=getattr(self, '_loaded_idx', 0),
                         # 跨章继承（2026-10-31）：第 1 拍的背景可能来自上一章。
                         initial_state=self._session.stage_initial_state())
        except Exception as e:                  # noqa: BLE001
            QMessageBox.critical(self, '无法打开预览', str(e))
    def show_continuity(self):
        """
        「🧵 跨章连续性」：上一章留下的背景 / 立绘 / CG / BGM，下一章
        第一句台词时还在不在？（只读）

        B 方案（2026-10-28）。判据（用户第二轮澄清的最终口径）：

            **下一章只有一条入边就判继承**（不看前一章有几条出边 ——
            分支点自己的章末状态是确定的，每个分支章继承同一份状态）；
            分支汇聚不判（不猜"反正一样"）。

        分析在后台线程跑（真实项目约 2~3 秒），窗口不会冻住；结果按
        "项目根 + 源文件指纹"缓存，文件没改再点开是瞬时的。
        """
        if not self._project_root:
            self._status('先打开一个项目。')
            return
        from storygraph.ui_qt.continuity_dialog import ContinuityDialog
        dialog = ContinuityDialog(self, self._project_root)
        dialog.report_ready.connect(self._log_continuity_summary)
        dialog.exec()

    def _log_continuity_summary(self, report):
        """把"这次分析看到了什么"写一行到控制台（报告窗口里是全文）。"""
        try:
            st = report.stats()
            self._log(f"[跨章连续性] 章节 {st['chapters']} · 可判接缝 "
                      f"{st['judged_seams']} · 需要人看 {st['warnings']} · "
                      f"不判定 {st['undetermined']} · "
                      f"{report.elapsed:.1f}s（只读）")
            first = next((f for f in report.findings
                          if f.level == 'warn'), None)
            if first is not None:
                self._log(f'  ⚠ {first.title}')
        except Exception as e:                      # noqa: BLE001 - 摘要不该拦住报告
            swallow('ui_qt.window_export.continuity_summary', e)

    def show_help(self):
        from storygraph.ui_qt.help_dialog import show_help
        return show_help(self)
