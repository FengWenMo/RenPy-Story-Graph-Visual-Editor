"""
storygraph.ui_qt.qt_args
========================
**Qt 的信号会把一个 `bool` 当第一个位置参数喂进槽** —— 这里只做一件事：
在槽的入口把它认出来、按"没给"处理，并留一笔痕迹。

信号签名是这样的（Qt 设计如此）：

    QPushButton.clicked(bool checked = False)
    QAction.triggered(bool checked = False)

于是 `btn.clicked.connect(self.save_layout)` 实际调的是
`save_layout(False)` —— `target` 收到**布尔**。而这些槽的第一个参数
**从来不是布尔**（路径 / 画布坐标 / 节点 id）。

后果分两种，**都真实发生过**：

  · **静默走错分支**：这些槽的判据是 `if target is None:`，而 `False is None`
    是 `False` → "没给路径就弹另存为"那条分支**走不到**，`False` 被当成路径
    一路送下去。2026-10-04 就是这么在仓库根目录留下一个名叫 `False` 的文件
    （178 字节的控制流脚本；`docs/archive/交付审查_2026-10-05.md` 里有它的内容与时间戳）；
  · **当场炸**：`add_node(at=False)` → `float(False[0])` 抛 `TypeError`；
    `Path(False).is_file()` 同样抛。

**预防**是 `tools/audit_qt_signal_slots.py`（静态判据：不许裸
`connect(self.槽)`，跨文件解析，`act()` 那条统一入口也一起管），
**兜底**是这里 —— 两者都要有：静态判据防的是"新写的接线"，
这里防的是"任何一条路径把 bool 送进来"（包括以后新加的 entry point）。

只丢**布尔**：别的类型照旧往下走。真的传错一个 `int`/`list` 属于编程错误，
该炸就炸（会话层还有 `isinstance(target, (str, Path))` 那道硬护栏），
不该被这里悄悄吞掉 —— 那样反而会把 bug 藏起来。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow


def drop_qt_bool(value, *, context: str):
    """
    `False` / `True` 在这些槽的参数上不是合法值 → 当成"没给"（返回 `None`）。

    `context` 用 `'ui_qt.<方法>'` 这种能一眼定位的写法（进 `safeguard` 的环形缓冲，
    `report()` / 体检能看到）。**不弹任何对话框、不打扰用户** ——
    这只是接线出错的痕迹，用户的正常流程要照走。
    """
    if isinstance(value, bool):
        swallow(context, TypeError(
            f'Qt 的 clicked/triggered(bool) 把这个值当第一个位置参数喂进来了：'
            f'{value!r} —— 已按"没给"处理（见 ui_qt/qt_args.py）'))
        return None
    return value
