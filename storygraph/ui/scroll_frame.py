"""
storygraph.ui.scroll_frame —— **可滚动的垂直容器**（Canvas + 内嵌 Frame）

为什么需要它（2026-09-30，用户实测）
------------------------------------
卡片面板把「这一拍 / 立绘 / 舞台 / 高级」一路堆下来，实测

    面板请求高度 923px  >  可视高度 813px

多出来的内容**直接被裁掉**，而且没有任何提示 —— 用户原话：

> 那个卡片模块右侧栏太长了！！！起码给我个滚动条，不然我根本就看不完全

tkinter 没有内置的滚动 Frame（`Listbox` / `Text` / `Canvas` 才有），
所以要自己搭：`Canvas` 里塞一个 `create_window` 的 Frame，靠 `scrollregion`
把它变成可滚动内容。

三个必须做对的地方（踩过就知道）
--------------------------------
1. **内嵌 Frame 的宽度要跟画布走**：不跟的话，`grid` 会按内容的"请求宽度"
   排版，出现右侧莫名空白或内容被横向裁掉（`Web` 开发里 `width:100%` 的同款问题）。
2. **滚轮不能绑 `bind_all`**：那会全局生效，把左边列表自己的滚轮抢掉。
   tkinter 的事件本身会沿 bindtags 冒泡（widget → class → **toplevel** → all），
   所以绑在**顶级窗口**上就够覆盖所有子孙控件（包括运行时才建出来的立绘卡），
   再用屏幕坐标做一次命中判定即可。
3. **浮层（Toast）要贴"视口"底部，不是"内容"底部**：面板被包进滚动内容后，
   它自己的高度等于全部内容高度，`place(rely=1.0)` 会落在可视区**下方**
   （等于提示永远看不见）。所以本类把自己暴露成 `viewport`，让调用方把
   浮层贴在这上面。
"""
from __future__ import annotations

import tkinter as tk

from storygraph.ui import tokens as T


class ScrollFrame(tk.Frame):
    """
    竖向可滚动容器。把内容放进 `self.body`，其余交给它。

    用法::

        scroller = ScrollFrame(parent)
        scroller.pack(fill='both', expand=True)
        panel = MyPanel(scroller.body)      # ← 内容进 body
        panel.pack(fill='both', expand=True)
        scroller.attach_wheel()             # 鼠标滚轮（可选，但强烈建议）
    """

    def __init__(self, parent, background=None, **kw):
        super().__init__(parent, **kw)
        bg = background or T.t('bg.surface')
        self._bg = bg

        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0,
                                background=bg,
                                yscrollincrement=T.SCROLL['unit'])
        self.vbar = tk.Scrollbar(self, orient='vertical',
                                 width=T.SCROLL['w'],
                                 command=self.canvas.yview,
                                 troughcolor=T.t('scroll.trough'),
                                 background=T.t('scroll.thumb'),
                                 activebackground=T.t('scroll.thumbHover'),
                                 relief='flat', bd=0,
                                 highlightthickness=0)
        self.canvas.configure(yscrollcommand=self._on_scroll_set)
        # 滚动条**常驻不隐藏**：禁用态也看得见，位置不会跳
        # （与"翻页到头显示禁用而不是隐藏"同一条原则）。
        self.canvas.pack(side='left', fill='both', expand=True)
        self.vbar.pack(side='right', fill='y')

        self.body = tk.Frame(self.canvas, background=bg)
        self._win = self.canvas.create_window((0, 0), window=self.body,
                                              anchor='nw')
        self._bind_internal()
        self._wheel_bound = False

    # ---------- 对外 ----------

    @property
    def viewport(self):
        """
        承载**浮层**的控件：浮层要贴在可视区底部，而不是内容的底部。

        （面板自带 Toast；内容一旦长过一屏，`rely=1.0` 贴的就是内容底，
          浮层会落在可视区外面 —— 等于提示永远看不见。）
        """
        return self

    def scroll_to_top(self):
        try:
            self.canvas.yview_moveto(0.0)
        except Exception:                    # noqa: BLE001 - 桩环境
            pass

    def attach_wheel(self, toplevel=None):
        """
        装鼠标滚轮支持。

        `toplevel` 默认取自己的顶级窗口。绑在顶级窗口而不是 `bind_all`：
        前者只在**本窗口**生效（不会污染其它 Tk 应用/窗口），
        又照样能收到所有子孙控件的滚轮事件（事件沿 bindtags 冒泡）。
        """
        if self._wheel_bound:
            return
        top = toplevel
        if top is None:
            try:
                top = self.winfo_toplevel()
            except Exception:                # noqa: BLE001 - 桩环境
                return
        for seq in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            try:
                top.bind(seq, self._on_wheel, add='+')
            except Exception:                # noqa: BLE001
                pass
        self._wheel_bound = True

    # ---------- 内部 ----------

    def _bind_internal(self):
        for widget, ev, handler in (
                (self.body, '<Configure>', self._on_body_configure),
                (self.canvas, '<Configure>', self._on_canvas_configure)):
            try:
                widget.bind(ev, handler)
            except Exception:                # noqa: BLE001
                pass

    def _on_scroll_set(self, first, last):
        """画布滚动位置 → 滚动条。内容装得下时把滑块铺满（一眼看出不用滚）。"""
        try:
            self.vbar.set(first, last)
        except Exception:                    # noqa: BLE001
            pass

    def _on_body_configure(self, _event=None):
        try:
            region = self.canvas.bbox('all')
            if region:
                self.canvas.configure(scrollregion=region)
        except Exception:                    # noqa: BLE001
            pass

    def _on_canvas_configure(self, event):
        """
        画布尺寸变化 → 内嵌 Frame 的**宽度跟着走**（高度不锁，内容说了算）。

        不这么做，`grid` 会按内容的请求宽度排版：窄窗口下右侧内容被裁掉
        且没有横向滚动条（本容器只做竖向滚动）。
        """
        try:
            self.canvas.itemconfigure(self._win, width=event.width)
        except Exception:                    # noqa: BLE001
            pass

    def _pointer_inside(self) -> bool:
        """指针是否落在本容器内（屏幕坐标比对，避开 tk 的坐标换算坑）。"""
        try:
            x, y = self.winfo_pointerxy()
            x0, y0 = self.winfo_rootx(), self.winfo_rooty()
            return (x0 <= x < x0 + self.winfo_width()
                    and y0 <= y < y0 + self.winfo_height())
        except Exception:                    # noqa: BLE001
            return False

    def _wheel_steps(self, event) -> int:
        """把各平台/各控件的滚轮事件折算成"上下几格"。"""
        num = getattr(event, 'num', None)
        if num == 4:                         # X11：上滚
            return -T.SCROLL['notch']
        if num == 5:                         # X11：下滚
            return T.SCROLL['notch']
        delta = getattr(event, 'delta', 0) or 0
        if not delta:
            return 0
        if abs(delta) >= 120:                # Windows：一格 120
            return int(-delta / 120) * T.SCROLL['notch']
        return int(-delta)                   # macOS：连续小值

    def _on_wheel(self, event):
        if not self._pointer_inside():
            return None                      # 指针不在本容器：放行给别的控件
        try:
            first, last = self.canvas.yview()
        except Exception:                    # noqa: BLE001
            return None
        if first <= 0.0 and last >= 1.0:
            return None                      # 内容装得下：不吃这个事件
        steps = self._wheel_steps(event)
        if not steps:
            return None
        try:
            self.canvas.yview_scroll(steps, 'units')
        except Exception:                    # noqa: BLE001
            return None
        return 'break'                       # 已经滚了，别再让父级也滚一次


__all__ = ['ScrollFrame']
