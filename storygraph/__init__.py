"""
storygraph —— Ren'Py 剧情树可视化编辑器。

分层（依赖方向自上而下，禁止反向）：
  core      零依赖：Statement IR / uid / 共享词表
  parsing   .rpy 解析
  assets    资产注册与资产引擎
  story     剧本表 / DSL / 舞台快照
  pipeline  构建 / 校验 / 编译 / 多引擎导出
  graph     图视图数据层与算法
  ui        tkinter 界面
  collab    协作后端
"""
__version__ = "1.0.0"
