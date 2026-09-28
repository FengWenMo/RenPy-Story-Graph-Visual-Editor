"""
storygraph.main
===============
桌面应用入口：建 tk 根窗口 → 建空图 → 挂 GraphView。

运行方式（两种等价，逻辑只有这一份）：
    python main.py                 # 根目录垫片，兼容既有习惯
    python -m storygraph.main
"""
from __future__ import annotations

import tkinter as tk

from storygraph.graph.models import Graph
from storygraph.ui.graph_view import GraphView


def main() -> int:
    """启动图形界面，返回进程退出码。"""
    root = tk.Tk()
    graph = Graph()
    GraphView(root, graph)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
