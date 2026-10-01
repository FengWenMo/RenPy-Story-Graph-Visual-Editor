"""
storygraph.main
===============
桌面应用入口（**默认走 Qt 版**）。

运行方式（三种等价，逻辑只有这一份）：
    python main.py                 # 默认：PySide6 在就开 Qt 版编辑器
    python main.py --tk            # 强制开旧的 tkinter 图视图（**已废弃**）
    python -m storygraph.main      # 同上

历史（2026-09-30 用户反馈："我这里显示完全没用到"）：
Qt 版做出来之后只挂在 `python -m storygraph.main_qt` 上，而用户平时是
用 `main.py` 启动的 —— 于是看到的还是 tkinter 老界面，以为没生效。

关于 `--tk`（2026-10-01）：
tkinter 版已**无用户可见入口**，语义也全部搬进了框架无关的
`storygraph.app.*`。这里保留 `--tk` 只为过渡（万一 Qt 装不上还有个
退路），并且在启用时**明确告诉用户它已废弃**。
"""

from __future__ import annotations

import sys

#: 过渡期兼容开关。正式下线时把这一组和下面的 tkinter 分支一起删掉，
#: 同时把 `storygraph/ui/*.py` 挪进 `legacy/`（见
#: `docs/前端迁移方案_qt.md` 第六节）。
_TK_FLAGS = ('--tk', '--legacy', '--tkinter')

_DEPRECATION = (
    '[已废弃] tkinter 界面仅为过渡保留：它的编辑语义已全部搬到 '
    'storygraph.app（两套前端共用）。请改用默认的 Qt 界面（python main.py）。'
)


def main(argv=None) -> int:
    """启动图形界面，返回进程退出码。"""
    argv = list(argv if argv is not None else sys.argv[1:])
    force_tk = any(a in _TK_FLAGS for a in argv)
    argv = [a for a in argv if a not in _TK_FLAGS]

    if not force_tk:
        try:
            import PySide6                              # noqa: F401
        except ImportError as e:
            # PySide6 没装：给出**可操作**的提示，再退回 tkinter。
            print(f'[提示] 没找到 PySide6（{e}）。')
            print('       装一下就能用新界面：  pip install PySide6')
            print('       现在先用 tkinter 版打开。')
        else:
            # 只有"装没装"这一件事决定走哪条路；Qt 前端自身的异常
            # **不吞**（否则会退到 tkinter 并让真正的 bug 消失）。
            from storygraph.main_qt import main as qt_main
            return qt_main(argv)

    print(_DEPRECATION)

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
