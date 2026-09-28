# legacy/ —— 归档代码（不参与构建）

## RenPy_Story_Graph_Visual_Editor.py

最初的**单文件**版剧情树可视化编辑器。它自带一整套 GraphView / 菜单 / 解析器，
但**没有**后来的这些能力：资产表、剧本表、DSL 编译、舞台快照、三级校验、
多引擎导出、协作后端。

### 为什么归档而不是删掉

- 它是 2026-09-26 之前唯一的入口，历史文档与截图可能仍指向它；
- 里面的 UI 细节（右键菜单结构、布局算法）仍有参考价值。

### 为什么不要再改它

- 它与 `storygraph/` 是**两套事实来源**：同名方法的行为已经不同
  （`layout_graph` 两边各有一份，389 行 vs 352 行）；
- 新人（或 AI）很容易改错文件——历史上真实发生过。

### 现在的入口

```bash
python main.py                # 根目录垫片 → storygraph.main
python cli.py validate <项目>  # 根目录垫片 → storygraph.cli
```

`tests/test_ui_integration.py::TestUiWiring` 仍会静态检查它的 `command=` 接线：
万一有人从这里复制代码回去，悬空回调会被当场发现。
