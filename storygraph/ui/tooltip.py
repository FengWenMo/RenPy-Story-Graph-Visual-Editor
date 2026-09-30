"""
storygraph.ui.tooltip —— 轻量 tooltip（tkinter 没有原生的）

用在哪里（设计规格 §3.10）
--------------------------
  · **立绘卡**：卡片上的名字会被截断（超 12 字），悬停看全名；
    顺带说明"预览还在加载"还是"找不到图片文件"
  · **禁用的翻页按钮**：说明为什么不能点（"已经是第一句"）——
    禁用态如果没有解释，用户会以为"点了没反应"
  · 图标按钮（🔍 / ✕）的文字说明

实现说明：`Toplevel(overrideredirect=True)` 画一个无边框小窗，鼠标进入后
延迟出现、离开即消失。延迟是为了避免鼠标划过时满屏闪提示。
"""
from __future__ import annotations

import tkinter as tk

from storygraph.ui import tokens as T


class Tooltip:
    """
    给一个控件挂提示。

    用法::

        tip = Tooltip(card)                       # 动态文本
        tip.set_provider(lambda: card.image_var.get())

        Tooltip(btn, '已经是第一句')               # 静态文本
    """

    def __init__(self, widget, text='', delay=None, wraplength=340):
        self.widget = widget
        self._text = str(text or '')
        self._provider = None
        #: 延迟出现：鼠标划过时不该满屏闪提示
        self._delay = 500 if delay is None else int(delay)
        self._wraplength = wraplength
        self._after_id = None
        self._win = None
        for seq, handler in (('<Enter>', self._schedule),
                             ('<Leave>', self.hide),
                             ('<Button-1>', self.hide),
                             ('<Button-3>', self.hide),
                             ('<Destroy>', self.hide)):
            try:
                widget.bind(seq, handler, add='+')
            except Exception:                # noqa: BLE001 - 测试桩
                pass

    # ---------- 对外 ----------

    def set_text(self, text):
        """设置静态文本；**空字符串表示不显示**（用于禁用态的解释）。"""
        self._text = str(text or '')
        self._provider = None
        if self._win is not None:
            self._render()

    def set_provider(self, fn):
        """设置动态文本（悬停时才求值，避免频繁计算）。"""
        self._provider = fn
        self._text = ''

    def hide(self, _event=None):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:                # noqa: BLE001
                pass
            self._after_id = None
        win, self._win = self._win, None
        if win is not None:
            try:
                win.destroy()
            except Exception:                # noqa: BLE001
                pass

    # ---------- 内部 ----------

    def _schedule(self, _event=None):
        self.hide()
        try:
            self._after_id = self.widget.after(self._delay, self._show)
        except Exception:                    # noqa: BLE001 - 桩环境没有事件循环
            self._after_id = None

    def _content(self) -> str:
        if self._provider is not None:
            try:
                return str(self._provider() or '')
            except Exception:                # noqa: BLE001
                return ''
        return self._text

    def _show(self):
        self._after_id = None
        text = self._content()
        if not text or self._win is not None:
            return
        try:
            win = tk.Toplevel(self.widget)
            win.overrideredirect(True)
            win.attributes('-topmost', True)
        except Exception:                    # noqa: BLE001
            return
        self._win = win
        try:
            tk.Label(win, text=text, justify='left',
                     background=T.t('toast.bg'), foreground=T.t('toast.fg'),
                     font=T.FONTS['caption'], wraplength=self._wraplength,
                     padx=T.SPACE['sm'], pady=T.SPACE['xs'],
                     borderwidth=0).pack()
            x = self.widget.winfo_pointerx() + 14
            y = self.widget.winfo_pointery() + 18
            win.geometry(f'+{int(x)}+{int(y)}')
        except Exception:                    # noqa: BLE001
            self.hide()

    def _render(self):
        """提示正显示时内容变了 → 就地更新（不闪烁）。"""
        win = self._win
        if win is None:
            return
        try:
            text = self._content()
            if not text:
                self.hide()
                return
            for child in win.winfo_children():
                child.config(text=text)
        except Exception:                    # noqa: BLE001
            self.hide()


def attach(widget, text='', provider=None):
    """便捷函数：挂一个 tooltip 并返回它（调用方可留着 set_text）。"""
    tip = Tooltip(widget, text)
    if provider is not None:
        tip.set_provider(provider)
    return tip


__all__ = ['Tooltip', 'attach']
