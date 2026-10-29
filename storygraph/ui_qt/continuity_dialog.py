# -*- coding: utf-8 -*-
"""
storygraph/ui_qt/continuity_dialog.py
=====================================
「🧵 跨章连续性」报告窗口 —— **只读**，分析在后台线程跑。

为什么要单独一个窗口（2026-10-28，B 方案）：

  · 报告是"审稿语言"（哪个接缝、哪一句台词、该看哪里），不是"门禁语言"；
    塞进 `QMessageBox` 会被截断，塞进控制台会跟导出日志混在一起；
  · 真实项目整本解析约 2~3 秒 —— 在 GUI 线程同步跑会**冻住窗口**，
    所以走 `QThread`，窗口先显示"分析中…"，结果回来再渲染；
  · 同一份源文件不重复分析：按 `项目根 + 源文件指纹`（mtime+size）缓存
    最近几份报告，`重新分析`按钮可以强制重跑。

⚠ 这个模块只**展示**：不写盘、不改剧本。分析本身在
`storygraph.pipeline.continuity`（纯逻辑、可单测、不 import Qt）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

from storygraph.core.safeguard import swallow
from storygraph.ui_qt import tokens as T


#: `(项目根, 源文件指纹) → ContinuityReport`：同一份文件不重复分析。
_CACHE: dict = {}
_CACHE_LIMIT = 6


def _remember(root: str, signature: str, report) -> None:
    """记住一份报告（超过上限就丢最早的那份 —— 内存里 6 份报告很小）。"""
    if not signature:
        return
    _CACHE[(str(root), str(signature))] = report
    while len(_CACHE) > _CACHE_LIMIT:
        _CACHE.pop(next(iter(_CACHE)))


def cached_report(root: str, signature: str):
    """缓存查询（单独暴露是为了测试能验证"没改文件不重新分析"）。"""
    return _CACHE.get((str(root), str(signature)))


def clear_cache() -> None:
    _CACHE.clear()


class _AnalysisThread(QThread):
    """后台跑整本分析（只读）。"""

    done = Signal(object, str)          # (ContinuityReport | None, error)

    def __init__(self, project_root: str, parent=None):
        super().__init__(parent)
        self._root = str(project_root)

    def run(self):                      # noqa: D401 - QThread 入口
        try:
            from storygraph.pipeline.continuity import analyze_project
            report = analyze_project(self._root)
            self.done.emit(report, '')
        except Exception as e:          # noqa: BLE001 - 分析失败要说人话
            swallow('ui_qt.continuity_dialog.analyze', e)
            self.done.emit(None, str(e))


class ContinuityDialog(QDialog):
    """
    跨章连续性报告（只读）。

    `report` 非空 = 直接用（缓存命中）；空 = 开窗后自动分析。
    """

    #: 分析完成（报告对象）—— 主窗口拿它往控制台写一行摘要。
    report_ready = Signal(object)

    def __init__(self, parent, project_root: str, report=None):
        super().__init__(parent)
        self._root = str(project_root or '')
        self._report = report
        self._signature = ''
        self._thread = None
        self._closed = False
        self._build()
        if report is not None:
            self._render(report)
        else:
            self._load()

    # ---------------- 构建 ----------------
    def _build(self):
        self.setWindowTitle(f'跨章连续性 —— {self._root}')
        self.resize(980, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        self._status = QLabel('准备分析…')
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._status)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._text.setPlaceholderText('分析中…')
        # 报告是"表"：等宽字体才能让章节名的空格对齐（Consolas 没有汉字字形，
        # Qt 会自动回退到正文字体渲染中文）。符号一律用纯文本标记，
        # 不用 ⚠ / ℹ / ♪ 这类在部分机器上缺字形的字符。
        mono = T.FONTS.get('mono') or ('Consolas', 9)
        self._text.setFont(QFont(str(mono[0]), int(mono[1]) + 1))
        layout.addWidget(self._text, 1)

        row = QHBoxLayout()
        self._re_btn = QPushButton('重新分析')
        self._re_btn.setToolTip('文件改过之后强制重跑（默认会按文件指纹复用上次结果）')
        self._re_btn.clicked.connect(self._reanalyze)
        row.addWidget(self._re_btn)
        self._copy_btn = QPushButton('复制报告')
        self._copy_btn.clicked.connect(self._copy)
        row.addWidget(self._copy_btn)
        row.addStretch(1)
        close = QPushButton('关闭')
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)

    # ---------------- 分析 / 缓存 ----------------
    def _load(self):
        signature = ''
        try:
            from storygraph.pipeline.continuity import project_source_signature
            signature = project_source_signature(self._root)
        except Exception as e:                      # noqa: BLE001
            swallow('ui_qt.continuity_dialog.signature', e)
        self._signature = signature
        hit = cached_report(self._root, signature) if signature else None
        if hit is not None:
            self._status.setText('（这份文件上次分析过，直接用缓存；'
                                 '文件一改就会重新分析）')
            self._render(hit)
            return
        self._start()

    def _start(self):
        if self._thread is not None and self._thread.isRunning():
            return
        self._status.setText('分析中…（只读；整本解析约 2~3 秒）')
        self._text.setPlainText('分析中…')
        self._re_btn.setEnabled(False)
        thread = _AnalysisThread(self._root, self)
        thread.done.connect(self._on_done)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        thread.start()

    def _reanalyze(self):
        self._signature = ''
        self._start()

    def _on_done(self, report, error: str):
        self._thread = None
        self._re_btn.setEnabled(True)
        if self._closed:
            return
        if error or report is None:
            self._status.setText('分析失败（报告没生成；项目文件没有被改动）')
            self._text.setPlainText(f'分析失败：\n{error}')
            return
        signature = self._signature
        if not signature:
            try:
                from storygraph.pipeline.continuity import (
                    project_source_signature)
                signature = project_source_signature(self._root)
            except Exception as e:                  # noqa: BLE001
                swallow('ui_qt.continuity_dialog.signature_after', e)
                signature = ''
            self._signature = signature
        _remember(self._root, signature, report)
        self._render(report)
        self.report_ready.emit(report)

    # ---------------- 展示 ----------------
    def _render(self, report):
        self._report = report
        st = report.stats()
        self._status.setText(
            f"章节 {st['chapters']} · 可判接缝 {st['judged_seams']}"
            f"（有状态 {sum(1 for s in report.judged_seams() if any(v not in ('unknown', 'n/a') for v in s.outcomes.values()))}）"
            f" · 分支点 {st['branch_nodes']} / 汇聚点 {st['merge_nodes']}"
            f" · 需要人看 {st['warnings']} · 不判定 {st['undetermined']}"
            f" · 用时 {report.elapsed:.1f}s（只读，未改任何文件）")
        self._text.setPlainText(report.render_text())
        self._text.moveCursor(QTextCursor.MoveOperation.Start)

    def _copy(self):
        QApplication.clipboard().setText(self._text.toPlainText())
        self._status.setText('报告已复制到剪贴板（只读文本）。')

    # ---------------- 生命周期 ----------------
    def _shutdown(self):
        """
        关窗前**等后台分析跑完**（只读、最多几秒）。

        不等的话，窗口/对话框销毁时 QThread 可能还在跑 —— Qt 会报
        `QThread: Destroyed while thread is still running`，最坏在退出时崩。
        """
        self._closed = True
        thread = self._thread
        self._thread = None
        if thread is not None and thread.isRunning():
            thread.wait(10000)

    def done(self, result):                      # noqa: N802 - Qt 接口
        """Esc / 关闭按钮 / accept 都会走这里：先收线程再真正关闭。"""
        self._shutdown()
        super().done(result)

    def closeEvent(self, event):                 # noqa: N802 - Qt 接口
        self._shutdown()
        super().closeEvent(event)


__all__ = ['ContinuityDialog', 'cached_report', 'clear_cache']
