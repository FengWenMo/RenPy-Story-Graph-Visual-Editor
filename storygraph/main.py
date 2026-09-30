"""
storygraph.main
===============
桌面应用入口（**默认走 Qt 版**）。

运行方式（三种等价，逻辑只有这一份）：
    python main.py                 # 默认：PySide6 在就开 Qt 版编辑器
    python main.py --tk            # 强制开旧的 tkinter 图视图（迁移期保留）
    python -m storygraph.main      # 同上

为什么改默认（2026-09-30 用户反馈："我这里显示完全没用到"）：
Qt 版做出来之后只挂在 `python -m storygraph.main_qt` 上，而**用户平时是
用 `main.py` 启动的** —— 于是看到的还是 tkinter 老界面，以为没生效。
现在 `main.py` 默认 Qt；**PySide6 不可用时也照样能开**（退回 tkinter）。
"""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    """启动图形界面，返回进程退出码。"""
    argv = list(argv if argv is not None else sys.argv[1:])
    force_tk = any(a in ('--tk', '--legacy', '--tkinter') for a in argv)
    argv = [a for a in argv
            if a not in ('--tk', '--legacy', '--tkinter')]

    if not force_tk:
        try:
            from storygraph.main_qt import main as qt_main
            return qt_main(argv)
        except Exception as e:            # noqa: BLE001 - Qt 不可用就退回
            print(f'[提示] Qt 前端不可用（{e}），退回 tkinter 版。')

    import tkinter as tk

    from storygraph.graph.models import Graph
    from storygraph.ui.graph_view import GraphView

    root = tk.Tk()
    graph = Graph()
    GraphView(root, graph)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
