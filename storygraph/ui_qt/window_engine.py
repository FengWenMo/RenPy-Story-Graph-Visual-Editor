"""
storygraph.ui_qt.window_engine
==============================
`MainWindowQt` 的**「🎮 引擎试玩」入口**：把真 Ren'Py 引擎点起来（2026-10-29）。

为什么要有这个入口
------------------
`ui_qt.stage_preview`（🎬 演出预览）是 Pillow 重画的近似帧：改台词够快，
但**不是引擎** —— `with dissolve`、ATL 动画、screen 特效、项目自定义 UI、
小游戏、菜单交互它都不会真跑。文案和策划要"看到跟游戏里一模一样"的演出，
只能让引擎自己跑。本模块只做"点一下 → 起引擎 → 状态栏汇报"，真正干活
（镜像 / 注入入口 / 隔离用户目录）全在 `storygraph.app.engine_preview`。

三个入口（都在演示专用版里才有意义；标准版会提示"请用演出专用版"）：

  · 卡片页「🎮 引擎试玩」      → 从**这一章开头**跑（最常用）；
  · 卡片页「🎞 从这一拍试玩」  → `--warp` 落到**当前这一拍**（近似：
    前情的 `$` 赋值没执行，变量可能缺；界面会明说）；
  · 结构图「🎮 引擎试玩」      → 选中模块那一章。

线程模型
--------
镜像同步 / 起进程要几百毫秒到几秒（第一次复制 ~2 MB 脚本 + 建 junction），
**不能占着 GUI 线程** —— 否则点一下窗口就白屏。所以启动走
`QThreadPool`（`_LaunchTask`），结果经信号回主线程；起来之后用一个
`QTimer` 每 0.8 秒问一次"还活着吗 / 窗口出来了吗 / 退出码是多少"。
引擎停掉的唯一出口是 `shutdown_engine_preview()`（`closeEvent` 里调）。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from storygraph.core.safeguard import swallow
from storygraph.app import engine_preview as EP


__all__ = ['WindowEngineMixin']


class _LaunchSignals(QObject):
    """后台启动任务 → GUI 线程的两个信号（成功带 `EngineRun`，失败带人话）。"""

    ok = Signal(object)
    fail = Signal(str)


class _LaunchTask(QRunnable):
    """
    在后台线程里 `EP.launch()`。

    为什么失败也要 emit 而不是 raise：`QRunnable.run` 里抛出的异常进不了
    事件循环，表现是"点了按钮没有任何反应"（本项目最熟悉的那类症状）。
    所以两种结果都走信号，由 GUI 线程弹框 / 写状态栏。
    """

    def __init__(self, target):
        super().__init__()
        self.target = target
        self.signals = _LaunchSignals()

    def run(self) -> None:                      # noqa: D401 - Qt 线程入口
        try:
            run = EP.launch(self.target)
        except EP.EnginePreviewError as e:
            self.signals.fail.emit(e.describe())
        except Exception as e:                  # noqa: BLE001 - 打包后才可能出现的怪错
            self.signals.fail.emit(f'启动引擎失败：{e}')
        else:
            self.signals.ok.emit(run)


class WindowEngineMixin:
    """
    「🎮 引擎试玩」：起真引擎、报状态、退出时停干净。

    它要求宿主（`MainWindowQt`）提供 `REQUIRES` 里那些成员 ——
    多半是**主类刻意留下**的：界面骨架、页切换、状态栏、
    静默失败上报、图的所有权。
    """

    #: 本 mixin 要求宿主提供的成员（由 `tests/test_main_window_mixins.py` 守）。
    REQUIRES = ('_file_path', '_loaded_idx', '_log', '_project_root',
                '_selected_node', '_session', '_status', 'apply_current_beat')

    #: 引擎启动后的轮询间隔（毫秒）。0.8 秒够快，又不至于每秒叫醒 CPU。
    ENGINE_POLL_MS = 800

    # ------------------------------------------------------------------
    #  三个入口
    # ------------------------------------------------------------------

    def engine_preview_chapter(self):
        """卡片页「🎮 引擎试玩」：**从这一章开头**跑真正的引擎。"""
        target = self._engine_target_from_session('chapter')
        self._launch_engine_target(target)

    def engine_preview_from_beat(self):
        """
        卡片页「🎞 从这一拍试玩」：`--warp` 落到当前这一拍。

        近似预览：跳过了前情，前面几章用 `$` 设的变量没有值 —— 脚本读它们
        会当场报错（引擎的报错窗口也是真演出的一部分）。所以先问一句
        "要不要从章首跑"，把选择权交给用户，而不是让人以为工具坏了。
        """
        target = self._engine_target_from_session('beat')
        if target is None:
            return
        target = self._engine_confirm_beat_mode(target)
        if target is not None:
            self._launch_engine_target(target)

    def engine_preview_selected(self):
        """结构图「🎮 引擎试玩」：选中模块那一章（没选中就退化成当前章）。"""
        target = None
        node = None
        try:
            node = self._selected_node()
        except Exception as e:                  # noqa: BLE001 - 没有选中不是错误
            swallow('ui_qt.window_engine.selected', e)
        if node is not None:
            try:
                project = self._project_root or getattr(self._session,
                                                        'project_root', None)
                target = EP.make_target(
                    project,
                    label=str(getattr(node, 'label_name', '') or ''),
                    file_path=getattr(node, 'file_path', None),
                    mode='chapter')
            except EP.EnginePreviewError as e:
                self._engine_report_error(e)
                return
        if target is None:
            target = self._engine_target_from_session('chapter')
        self._launch_engine_target(target)

    # ------------------------------------------------------------------
    #  目标解析（纯逻辑，失败说人话）
    # ------------------------------------------------------------------

    def _engine_project_root(self):
        """当前项目根（卡片页打开过项目 / 会话自己记着，都算）。"""
        root = getattr(self, '_project_root', None)
        if root:
            return root
        return getattr(self._session, 'project_root', None)

    def _engine_target_from_session(self, mode: str):
        """
        从**当前会话**（这一章 / 这一拍）解析出预览目标；解析不出来返回 None。

        这里只负责"拿线索 + 把错误说清楚"，label 是否真的存在由
        `engine_preview.make_target` 判（它读项目目录，不靠猜）。
        """
        session = self._session
        try:
            loaded = len(session)
        except (TypeError, AttributeError):
            loaded = 0
        if not loaded:
            self._status('先载入一章：卡片页点「✎ 直接编辑单章」，'
                         '或在结构图里双击一个模块。')
            return None
        project = self._engine_project_root()
        if not project:
            self._status('先打开一个项目（📁 打开项目，或把项目文件夹拖到窗口里）。')
            return None
        label = str(getattr(session, 'focus_label', '') or '')
        if not label:
            for beat in (session.beats or ()):
                if str(getattr(beat, 'label', '') or ''):
                    label = str(beat.label)
                    break
        line = 0
        if mode == 'beat':
            try:
                idx = int(self._loaded_idx if self._loaded_idx is not None else 0)
            except (TypeError, ValueError):
                idx = 0
            beats = list(getattr(session, 'beats', None) or ())
            if not (0 <= idx < len(beats)):
                self._status('当前没有停留在一拍上（先在卡片列表里点一张卡片）。')
                return None
            line = int(getattr(beats[idx], 'src_first', 0) or 0)
            if line <= 0:
                self._status('这一拍在源文件里没有行号（可能是新插入、还没导出）——'
                             '先导出 .rpy，再从章首试玩。')
                return None
        try:
            return EP.make_target(project, label=label,
                                  file_path=getattr(self, '_file_path', None),
                                  line=line, mode=mode)
        except EP.EnginePreviewError as e:
            self._engine_report_error(e)
            return None

    def _engine_confirm_beat_mode(self, target):
        """
        「从这一拍」的确认框：**默认从章首**（安全的那条路）。

        为什么要问：warp 进去时前面几章 `$` 设的变量都没有值，脚本里
        `if love > 3` 这种一读就是 NameError —— 引擎会弹错误窗口。那对
        "想看这一拍演出长什么样"的人是噪音，所以先把选项摆出来。
        """
        if target is None or target.mode != 'beat':
            return target
        box = QMessageBox(self)
        box.setWindowTitle('从这一拍试玩（近似）')
        box.setIcon(QMessageBox.Question)
        box.setText('「从这一拍」是**近似**预览：跳过了前情，'
                    '前几章用 $ 设过的变量没有值，脚本一读到就会报错。')
        box.setInformativeText('从章首跑才是完整的这一章。要选哪个？')
        first = box.addButton('从这一拍（近似）', QMessageBox.AcceptRole)
        chapter = box.addButton('从章首（推荐）', QMessageBox.AcceptRole)
        box.setDefaultButton(chapter)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is chapter:
            try:
                return EP.make_target(target.project_root,
                                      label=target.chapter_label or target.label,
                                      mode='chapter')
            except EP.EnginePreviewError as e:
                self._engine_report_error(e)
                return None
        if clicked is first:
            return target
        return None

    # ------------------------------------------------------------------
    #  启动 / 轮询 / 报错
    # ------------------------------------------------------------------

    def _launch_engine_target(self, target):
        """真正启动（有未导出改动先提醒；引擎没装就说清怎么办）。"""
        if target is None:
            return
        if self._engine_has_unsaved_edits():
            if not self._engine_confirm_stale_disk():
                return
        ok, message = EP.preview_supported()
        if not ok:
            box = QMessageBox(self)
            box.setWindowTitle('引擎试玩')
            box.setIcon(QMessageBox.Information)
            box.setText('这台电脑上没有可用的 Ren\'Py 引擎，暂时不能用引擎试玩。')
            box.setInformativeText(message)
            box.exec()
            self._log('[引擎] 没有可用引擎：' + message)
            return
        live = EP.live_runs()
        if live:
            self._status('已经开着一个引擎窗口了 —— 先关掉它再开新的'
                         '（同一个游戏同时跑两份会抢存档）。')
            return
        try:
            self.apply_current_beat()
        except Exception as e:                  # noqa: BLE001 - 表单提交失败不拦预览
            swallow('ui_qt.window_engine.apply_current_beat', e)
        self._set_engine_busy(True)
        self._status(f'正在准备引擎镜像…（{target.describe()}）')
        self._log(f'[引擎] 准备启动：{target.describe()}')
        task = _LaunchTask(target)
        task.signals.ok.connect(self._engine_launched)
        task.signals.fail.connect(self._engine_launch_failed)
        self._engine_task = task                # 防 GC：QRunnable 交给线程池后不持有引用
        QThreadPool.globalInstance().start(task)

    def _engine_launched(self, run):
        """后台启动成功（GUI 线程）：开轮询、报状态、写日志。"""
        self._set_engine_busy(False)
        self._engine_run = run
        self._engine_announced = False
        self._log(f'[引擎] 已启动：{run.target.describe()}'
                  f' · {run.engine.describe()}')
        self._log(f'[引擎] 镜像：{run.mirror_root}')
        self._status('引擎正在启动…（第一次加载要几秒；窗口出来后直接玩）')
        timer = getattr(self, '_engine_timer', None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(self.ENGINE_POLL_MS)
            timer.timeout.connect(self._engine_poll)
            self._engine_timer = timer
        timer.start()

    def _engine_launch_failed(self, message: str):
        """后台启动失败（GUI 线程）：把原因摆出来，别只喊"失败了"。"""
        self._set_engine_busy(False)
        self._engine_run = None
        self._log('[引擎] 启动失败：' + str(message))
        self._status('引擎没起来：' + str(message).splitlines()[0])
        box = QMessageBox(self)
        box.setWindowTitle('引擎没起来')
        try:
            box.setIcon(QMessageBox.Warning)
        except Exception as e:                  # noqa: BLE001 - 图标设不上也要弹框
            swallow('ui_qt.window_engine.failed_icon', e)
        box.setText('引擎没起来。下面是它自己说的话：')
        box.setDetailedText(str(message))
        box.exec()

    def _engine_poll(self):
        """每 0.8 秒问一次引擎：活着吗 / 窗口出来了吗 / 退出了吗。"""
        run = getattr(self, '_engine_run', None)
        timer = getattr(self, '_engine_timer', None)
        if run is None:
            if timer is not None:
                timer.stop()
            return
        if not run.running():
            if timer is not None:
                timer.stop()
            self._engine_run = None
            self._set_engine_busy(False)
            code = run.poll()
            if code not in (0, None):
                report = run.error_report()
                self._log(f'[引擎] 退出了（码 {code}）：'
                          + (report or '（没有错误文件）'))
                self._status(f'引擎退出了（码 {code}）—— 详情见控制台。')
                if report and code not in (0, None):
                    box = QMessageBox(self)
                    box.setWindowTitle('引擎退出了')
                    box.setText('引擎没跑完就退出了。它留下的最后一句话：')
                    box.setDetailedText(report[-2000:])
                    box.exec()
            else:
                self._log('[引擎] 窗口已正常关闭。')
                self._status('引擎窗口已关闭（改动已在工具里，记得导出）。')
            return
        if not getattr(self, '_engine_announced', False) and run.ready():
            self._engine_announced = True
            self._log('[引擎] 窗口已出现，直接玩；关掉窗口就回工具。')
            self._status('🎮 引擎已启动 —— 关掉游戏窗口就回到工具')

    def _engine_has_unsaved_edits(self) -> bool:
        """这一章有改动还没导出成磁盘上的 `.rpy` 吗（引擎只认磁盘）。"""
        try:
            return bool(self._session.has_unsaved_edits())
        except Exception as e:                  # noqa: BLE001 - 判不出来不拦
            swallow('ui_qt.window_engine.unsaved', e)
            return False

    def _engine_confirm_stale_disk(self) -> bool:
        """有未导出改动时的提醒：默认"取消（先导出）"，别让引擎静默跑旧内容。"""
        answer = QMessageBox.question(
            self, '这一章有没导出的改动',
            '引擎跑的是**磁盘上的 .rpy**。这一章还有改动没导出，'
            '现在试玩看到的是旧版本。\n\n'
            '先点「⤓ 导出 .rpy」再试玩比较好。要**仍然用旧版本试玩**吗？',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def _engine_report_error(self, error):
        """`EnginePreviewError` → 状态栏 + 控制台 +（一次性）对话框。"""
        message = error.describe() if hasattr(error, 'describe') else str(error)
        self._status('引擎试玩：' + str(error).splitlines()[0])
        self._log('[引擎] ' + message)
        box = QMessageBox(self)
        box.setWindowTitle('引擎试玩')
        box.setIcon(QMessageBox.Information)
        box.setText(str(error))
        hint = getattr(error, 'hint', '')
        if hint:
            box.setInformativeText(hint)
        box.exec()

    def _set_engine_busy(self, busy: bool) -> None:
        """启动中把入口置灰（防连点两下起两份引擎、抢存档）。"""
        self._engine_busy = bool(busy)
        for name in ('_engine_action', '_engine_beat_action',
                     '_engine_graph_action'):
            action = getattr(self, name, None)
            if action is not None:
                try:
                    action.setEnabled(not busy)
                except Exception as e:          # noqa: BLE001
                    swallow('ui_qt.window_engine.busy', e)

    def shutdown_engine_preview(self):
        """关窗时把引擎停掉（`closeEvent` 调；不留孤儿游戏进程）。"""
        timer = getattr(self, '_engine_timer', None)
        if timer is not None:
            try:
                timer.stop()
            except Exception as e:              # noqa: BLE001
                swallow('ui_qt.window_engine.shutdown.timer', e)
        run = getattr(self, '_engine_run', None)
        self._engine_run = None
        if run is not None:
            try:
                run.stop()
            except Exception as e:              # noqa: BLE001 - 退出路径不抛
                swallow('ui_qt.window_engine.shutdown.run', e)
