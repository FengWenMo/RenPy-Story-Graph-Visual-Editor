"""
「AI 助手」面板：跟模型说话，它自己调工具干活
=========================================================================

> 用户 2026-10-11："打开之后能有一个地方点开，然后让用户输入 api key，
>  然后再让 ai 自行开始工作"。

## 三条界面纪律（沿用本仓红线）

1. **能点就必须有反应**：没填 key → 发送按钮灰化 + 状态行写清"去点那个按钮填"；
   干活时按钮变"⏳ 干活中…"并禁用（防重复点）。
2. **Qt 不渲染 Markdown** → 面板上的文案不写 `**`（工具轨迹里来自工具的字串原样显示）。
3. **写盘必须当面点头**：模型要写清单时，**弹窗给它看 diff**，
   人可以"允许写"或"先别写" —— 没点头**一个字节都不写**（闸门在 `ai/agent.py` 里）。

## 线程模型（为什么不直接在主线程跑）

网络请求会阻塞几秒到几十秒 —— 在主线程跑 = **界面假死**（本仓最忌）。
所以：`run_agent` 跑在 `QThread` 里；只有两件事回主线程：
  · **事件**（刷新轨迹/对话）；
  · **闸门询问**（要弹窗，必须主线程）—— 用 `threading.Event` 等主线程答完再继续。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from storygraph.core import ai_settings as AI
from storygraph.core.safeguard import swallow


class GateDialog(QDialog):
    """写盘前的确认窗：**把 diff 摆出来**，人再决定。"""

    def __init__(self, tool: str, preview: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle('要不要真的写进项目？')
        self.setMinimumWidth(680)
        root = QVBoxLayout(self)
        head = QLabel(f'模型想执行「{tool}」。下面只是试算，还没写盘。'
                      f'看清楚再决定。')
        head.setWordWrap(True)
        root.addWidget(head)
        diff = QPlainTextEdit(preview.get('diff') or '（这次没有内容变化）')
        diff.setReadOnly(True)
        diff.setMinimumHeight(220)
        root.addWidget(diff)
        if preview.get('warnings'):
            note = QLabel('提醒：' + '；'.join(str(w) for w in preview['warnings']))
            note.setWordWrap(True)
            root.addWidget(note)
        box = QDialogButtonBox()
        self.ok = box.addButton('允许写', QDialogButtonBox.AcceptRole)
        self.no = box.addButton('先别写', QDialogButtonBox.RejectRole)
        self.ok.setProperty('variant', 'primary')
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)


class _Worker(QThread):
    """跑一轮 agent（网络在子线程，界面不假死）。"""

    events = Signal(list)
    done = Signal(dict)
    ask = Signal(list)                    # [tool, preview] → 主线程弹窗

    def __init__(self, project_root, messages, cfg, approve_bridge,
                 transport=None, parent=None):
        super().__init__(parent)
        self._root, self._messages = project_root, messages
        self._cfg, self._approve = cfg, approve_bridge
        # ⚠ 必须传下去（我第一版忘了 → 判据注入的假 transport 没生效，
        #   于是它去打了真接口）。判据注入 transport 就等于"绝不联网"。
        self._transport = transport

    def run(self):
        from storygraph.ai import agent as A
        try:
            got = A.run_agent(self._root, self._messages, self._cfg,
                              transport=self._transport,
                              approve=self._approve,
                              on_event=lambda ev: self.events.emit([ev]))
        except Exception as e:                      # noqa: BLE001 - 线程里不许炸
            swallow('ui_qt.panel_ai.worker', e)
            got = {'ok': False, 'messages': self._messages, 'events': [],
                   'final': '', 'reason': f'干活时出错：{type(e).__name__}: {e}'}
        self.done.emit(got)


class AiPanel(QWidget):
    """对话 + 工具轨迹 + 写盘确认。**唯一会碰模型的界面**。"""

    #: 子线程要把"要不要写盘"交给**主线程**问（弹窗只能主线程）——
    #: ⚠ 这个信号必须定义在**面板**上（我第一版错定义在 worker 上，
    #: 子线程 `self.ask.emit` 直接 AttributeError）。
    ask = Signal(list)

    def __init__(self, parent=None, *, project_root=None, transport=None):
        super().__init__(parent)
        self.project_root = project_root
        self._transport = transport                # 判据注入假传输（绝不联网）
        self._messages = []
        self._worker = None
        self._gate_answer = {'value': False}
        self._gate_wait = threading.Event()
        self.ask.connect(self._on_ask)

        root = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        root.addWidget(self.log, 1)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText('跟它说要干什么，例：看看项目里有哪些我不认识的写法')
        self.input.returnPressed.connect(self.send)
        row.addWidget(self.input, 1)
        self.send_btn = QPushButton('▶ 让它干活')
        self.send_btn.setProperty('variant', 'primary')
        self.send_btn.clicked.connect(self.send)
        row.addWidget(self.send_btn)
        self.key_btn = QPushButton('🔑 填 API key')
        self.key_btn.clicked.connect(self.open_key_dialog)
        row.addWidget(self.key_btn)
        root.addLayout(row)

        self.refresh()
        self.append('这里是 AI 助手。它会自己读项目、提议怎么改；'
                    '要写盘时会先弹窗给你看 diff，你点头它才写。')

    # ---------------- 小工具 ----------------
    def append(self, text: str):
        self.log.appendPlainText(str(text))

    def refresh(self):
        """状态行 + 按钮可用性（**能点就必须有反应**）。"""
        cfg = AI.load()
        ready, why = AI.is_ready(cfg)
        if ready:
            self.status.setText(f'已配好：{cfg.get("provider")}'
                                f'（key {AI.masked(cfg.get("api_key"))}）')
        else:
            self.status.setText(f'还不能用：{why}')
        busy = self._worker is not None and self._worker.isRunning()
        self.send_btn.setEnabled(ready and not busy)
        self.send_btn.setText('⏳ 干活中…' if busy else '▶ 让它干活')

    def open_key_dialog(self):
        from storygraph.ui_qt.dialog_ai import AiKeyDialog
        AiKeyDialog(self).exec()
        self.refresh()

    # ---------------- 闸门桥（子线程 ↔ 主线程）----------------
    def _approve_bridge(self, tool, args, preview):
        """
        子线程里被 agent 调用：把问题丢给主线程弹窗，然后**等**答案。

        ⚠ 必须等（不能默认放行）—— 默认放行就等于"AI 自己决定写盘"。
        """
        self._gate_wait.clear()
        self._gate_answer['value'] = False
        self.ask.emit([tool, preview])
        if not self._gate_wait.wait(timeout=300):   # 人不理它 → 当作不批准
            return False
        return bool(self._gate_answer['value'])

    def _on_ask(self, payload):
        tool, preview = payload
        dlg = GateDialog(str(tool), preview or {}, self)
        ok = dlg.exec() == QDialog.Accepted
        self._gate_answer['value'] = ok
        self.append(('✅ 你允许了写盘。' if ok else '🛑 你选了"先别写"，没有动盘。'))
        self._gate_wait.set()

    # ---------------- 发起一轮 ----------------
    def send(self):
        text = self.input.text().strip()
        if not text:
            self.append('先说一句话吧（输入框里写要干什么）。')
            return
        cfg = AI.load()
        ready, why = AI.is_ready(cfg)
        if not ready:
            self.append(f'还不能干活：{why}')
            self.refresh()
            return
        self.input.clear()
        self._messages.append({'role': 'user', 'content': text})
        self.append(f'\n你：{text}')
        self._start_worker(cfg)

    def _start_worker(self, cfg):
        self._worker = _Worker(self.project_root, list(self._messages), cfg,
                               self._approve_bridge,
                               transport=self._transport, parent=self)
        self._worker.events.connect(self._on_events)
        self._worker.ask.connect(self._on_ask)
        self._worker.done.connect(self._on_done)
        self._worker.start()
        self.refresh()

    # ---------------- 事件渲染 ----------------
    def _on_events(self, payload):
        ev = (payload or [{}])[0]
        kind = ev.get('kind')
        if kind == 'assistant':
            self.append(f'\n它：{ev.get("text") or ""}')
        elif kind == 'tool_call':
            self.append(f'  · 调用 {str(ev.get("name")).replace("storygraph_", "")}'
                        f' {str(ev.get("arguments") or {})[:80]}')
        elif kind == 'tool_result':
            res = ev.get('result') or {}
            mark = '✅' if res.get('ok') else '⚠'
            self.append(f'    {mark} {str(res.get("reason") or "好")[:100]}')
        elif kind == 'error':
            self.append(f'⚠ {ev.get("reason")}')
        elif kind == 'done':
            self.append('（这一轮说完了）')

    def _on_done(self, got):
        self._messages = list(got.get('messages') or self._messages)
        if not got.get('ok') and got.get('reason'):
            self.append(f'⚠ 没干成：{got["reason"]}')
        self._worker = None
        self.refresh()
