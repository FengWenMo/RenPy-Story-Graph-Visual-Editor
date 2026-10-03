"""
storygraph.app.notify
=====================
**兼容转发**：通知端口的实现在 `storygraph.core.notify`。

为什么搬下去（2026-10-05，风险清单 P2-5）
----------------------------------------
这个模块**只依赖 `storygraph.core.safeguard`** —— 它本来就是后端的一部分，
只是当年放在了 `app` 包里。于是 `graph` 与 `pipeline` 为了用它，
不得不顶层 `import storygraph.app.notify`：

    graph/commands.py:4           from storygraph.app import notify
    graph/editor_utils.py:7       from storygraph.app import notify
    pipeline/renpy_exporter.py:2  from storygraph.app.notify import info, warn

`app` 是**最上层**（比 graph / pipeline 都高），这三行就是三条反向依赖，
也是"包间依赖环"里 `app↔graph`、`app↔pipeline` 两条环的**全部来源**。
把实现搬到 `core`（最底层）之后，环直接消失，不需要任何延迟 import。

这个文件保留为**转发**，是为了不让外部（含测试与第三方脚本）因为搬家而
`ImportError`。新代码请直接从 `storygraph.core.notify` 导入。
"""

from __future__ import annotations

from storygraph.core.notify import (  # noqa: F401
    ERROR,
    INFO,
    LEVELS,
    WARN,
    error,
    info,
    notify,
    subscribers,
    subscribe,
    unsubscribe,
    warn,
)

__all__ = ['INFO', 'WARN', 'ERROR', 'LEVELS', 'subscribe', 'unsubscribe',
           'subscribers', 'notify', 'info', 'warn', 'error']
