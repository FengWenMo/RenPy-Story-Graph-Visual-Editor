"""
storygraph.app.notify
=====================
**通知端口** —— 让后端能"告诉用户一件事"，而**不必知道界面框架存在**。

问题（拆分前）
--------------
`storygraph/graph/commands.py`、`graph/editor_utils.py`、
`pipeline/renpy_exporter.py` 里各有一句 `import tkinter.messagebox`，
只为了弹一个"没有可撤销的操作"。后果有三：

  1. **依赖方向反了**：后端（`graph`/`pipeline`）依赖前端（tkinter）；
  2. 无头环境（CI、CLI、服务器）跑不了 —— tkinter 起不来就 ImportError，
     所以测试里得给 tkinter 打桩（`tests/conftest.py` 那 80 行桩就是证据）；
  3. **换前端时后端要改**：Qt 版想接管提示就得改后端，这正是"两套前端
     互相纠缠"的根源。

现在只有一个方向
----------------
后端**只汇报事实**：

    notify.info('没有可撤销的操作', title='提示')
    notify.error(f'导出失败：{e}')
    notify.warn('资源缺失，已降级')

前端**订阅后才决定怎么显示**：

    · Qt：`app/notify` → 状态栏 + Toast/QMessageBox；
    · tkinter：`app/notify` → messagebox（迁移期兼容）；
    · CLI：`app/notify` → stderr；
    · 测试 / 无头：**默认什么也不弹**（零副作用）。

订阅者签名
----------
    listener(level: str, message: str, title: str) -> None
    level ∈ {'info', 'warn', 'error'}

**禁止**在本模块里 import 任何 UI 框架 —— 它是后端的一部分。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow
from typing import Callable, List

#: 通知级别
INFO = 'info'
WARN = 'warn'
ERROR = 'error'

LEVELS = (INFO, WARN, ERROR)

#: 订阅者列表。默认**空** —— 没人订阅就什么也不发生（无头安全）。
_listeners: List[Callable[[str, str, str], None]] = []


def subscribe(listener: Callable[[str, str, str], None]) -> Callable:
    """
    注册一个通知接收者，返回**取消订阅**的函数。

    前端在启动时订阅；测试在用例里订阅并断言。
    """
    _listeners.append(listener)

    def _unsubscribe():
        try:
            _listeners.remove(listener)
        except ValueError as e:
            swallow('notify._unsubscribe', e)

    return _unsubscribe


def unsubscribe(listener) -> None:
    try:
        _listeners.remove(listener)
    except ValueError as e:
        swallow('notify.unsubscribe', e)


def subscribers() -> list:
    """当前订阅者（只读快照；主要给测试用）。"""
    return list(_listeners)


def notify(level: str, message: str, title: str = '') -> str:
    """
    发出通知。**永不抛异常** —— 提示失败绝不该让业务逻辑跟着炸。

    返回归一化后的 level（便于测试与链式判断）。
    """
    level = level if level in LEVELS else INFO
    message = str(message or '')
    title = str(title or '')
    for listener in list(_listeners):
        try:
            listener(level, message, title)
        except Exception as e:                    # noqa: BLE001 - 一个坏订阅者不拖垮其余
            swallow('core.notify.notify', e)
            continue
    return level


def info(message: str, title: str = '') -> str:
    return notify(INFO, message, title)


def warn(message: str, title: str = '') -> str:
    return notify(WARN, message, title)


def error(message: str, title: str = '') -> str:
    return notify(ERROR, message, title)


__all__ = ['INFO', 'WARN', 'ERROR', 'LEVELS', 'subscribe', 'unsubscribe',
           'subscribers', 'notify', 'info', 'warn', 'error']
