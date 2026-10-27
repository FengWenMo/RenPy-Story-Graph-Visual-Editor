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
                      ui_qt (PySide6)        ← 只摆控件、读表单、写表单

（2026-10-01：`storygraph/ui`（tkinter）已整个删除，前端只剩一套。）

模块
----
主要入口（按使用频率）：

`beats`        剧本卡片编辑语义（Beat↔字段、立绘行、表情/位置投影、资产检索）
`stage`        舞台演出推导（Beat 序列 → 可绘制帧 + 素材解析）
`stage_render` 把 `StageFrame` 画成像素（PIL）—— 卡片缩略图与「🎬 演出预览」共用
`editor`       编辑器会话门面（前端只需认识这一个对象）
`notify`       通知/确认端口（后端不弹窗，改为发消息给前端）
`picker`       资产选择器语义（分组 / 搜索 / 详情 / 缩略图路径）
`thumbs`       缩略图内核（缓存键 + 后台解码）
`asset_panel`  资产面板数据源（树 / 详情 / 搜索 / 统计 / 角色视角）
`asset_report` 资源体检报告（`asset_health_report()` 的结果 → 给人看的文本）
`tables`       剧本表（Excel）双向工作流

`EditorSession` 的分块（mixin，在 `editor.py` 里拼成一个类）：
`session_table` / `session_graph` / `session_assets` / `session_collab` /
`session_export` / `session_recover`

其余：`chapter_index`（项目里有哪些章可打开 = 左侧导航的数据源）/
`layout_store`（版式的存取：只记"谁摆在哪"）/ `prewarm`（缩略图预热）/
`layer_compose`（把分离的立绘层合成一张图）/ `beat_clear`（清空一张卡片）/
`source_line_delete`（删一条没打开的源边：只动源文件那一行）

> ⚠ 这份清单靠人维护，历史上漂过（2026-10-27 审计：这里写着 8 个、包里有 22 个）。
> 加新模块时顺手加一行；实在懒得写描述，也把名字补上 —— 名单不全比没有更误导。
"""

__all__ = ['beats', 'stage', 'notify', 'editor', 'picker', 'thumbs',
           'asset_panel', 'tables']
