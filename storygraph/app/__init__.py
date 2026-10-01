"""
storygraph.app —— **应用服务层**（框架无关）。
=============================================

这一层是前端与后端之间的**深模块边界**：接口是几个纯函数/小对象，
实现里装着编辑器的全部业务不变量。

为什么需要它
------------
历史上编辑语义（Beat ↔ 卡片字段、立绘行、表情/位置投影、舞台推导）
住在 `storygraph/ui/` 里 —— 那是 tkinter 的地盘。后果：

  · 想用这些函数就必须 `import tkinter`（后端被迫依赖前端，方向反了）；
  · Qt 前端只能偷偷 `from storygraph.ui.beat_editor import ...`，
    于是"两套前端"变成"两套前端 + 一个共享的 UI 目录"的三角关系；
  · 无头测试与 CLI 也被拖进 GUI 依赖。

现在依赖方向是单向的：

    core / parsing / excel / assets / story / pipeline
                        ↓
                    app  ← 只依赖上面这些（**不许 import 任何 UI 框架**）
                        ↓
              ui (tkinter) / ui_qt (PySide6)   ← 只摆控件、读表单、写表单

模块
----
`beats`   剧本卡片编辑语义（Beat↔字段、立绘行、表情/位置投影、资产检索）
`stage`   舞台演出推导（Beat 序列 → 可绘制帧 + 素材解析）
`notify`  通知/确认端口（后端不弹窗，改为发消息给前端）
`editor`  编辑器会话门面（前端只需认识这一个对象）
"""

__all__ = ['beats', 'stage', 'notify', 'editor']
