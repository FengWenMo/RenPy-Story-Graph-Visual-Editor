# RenPy 剧情树可视化编辑器（storygraph）

把 Ren'Py 的 `.rpy` 演出脚本变成**策划能直接编辑的 Excel**，再原样编译回
**能跑的 `.rpy`**。面向 50 人左右的小团队：文案 / 策划主导，程序辅助。

## 它解决什么

| 谁 | 痛点 | 这里的答案 |
|---|---|---|
| 文案 / 策划 | 改一句台词要读代码，怕改坏 | Excel 一行一句；资产列是**下拉框**，不用手打 ID |
| 程序 | 觉得表格不好使，只想敲代码 | 手改 `.rpy` 会反映到表格（文件监听 + 增量解析） |
| 主策 / QA | 不知道"这句台词播出来长什么样" | **舞台快照 / 演出时间轴**（派生视图，只读） |
| 制作人 | 以后要迁 Unity、要做多语言 | **引擎中立 IR** + Unity / Godot / CSV 导出器 |

## 快速开始

```bash
pip install -r requirements.txt     # 运行时依赖 openpyxl；界面用 PySide6
python -m pytest                    # 1529 个用例，约 70 秒

python demo/make_demo.py            # 一键重建示例工程（含产物与协作库）
python main.py                      # 启动图形界面（PySide6）
```

> 界面**只有 PySide6 一套**（tkinter 版已于 2026-10-01 彻底移除）。
> 没装 PySide6 时 `main.py` 会**明确报错并给出 `pip install PySide6`**
> ——不再有"悄悄退回老界面"那条路（那正是当年"我这里显示完全没用到"
> 的原因）。只跑无界面流程用 `python cli.py validate <项目根>`。

命令行（CI 友好，退出码 **0 通过 / 1 有错误 / 2 用法或环境问题**）：

```bash
python cli.py validate <项目根>              # 只体检，不写盘
python cli.py build <项目根> --engine renpy   # 体检 + 出产物（+ build_report.json）
python cli.py table rpy2xlsx <a.rpy>         # 单个 .rpy → Excel
python cli.py table xlsx2rpy <a.xlsx>        # Excel → .rpy
python cli.py compile <表.xlsx> --sheet       # 编译 + 另一张编译结果表
python cli.py collab status                   # 协作（SQLite 后端 + Excel 前端）
```

`main.py` / `cli.py` 是**根目录垫片**，真实实现在 `storygraph/` 里，逻辑只有一份。

## 目录结构

```
storygraph/
├── core/       零依赖地基
│   ├── statement.py      Statement IR（.rpy ↔ Excel 的中枢）
│   ├── uid.py            翻译单元 uid 的拼接契约
│   ├── vocabulary.py     共享词表唯一真源（资产类型/非角色 tag/内置变换）
│   └── utils.py          表达式变量抽取
├── excel/      Excel 底层设施
│   └── cells.py          安全写单元格（`=` 开头的用户文本别被当公式）
├── parsing/    .rpy 解析
│   ├── renpy_parser.py   Lexer → AST → ParseResult(Statement 流)
│   ├── discover.py       扫出该被构建的 .rpy（门禁与构建共用）
│   └── character_script_linker.py
├── assets/     资产链路
│   ├── asset_registry.py 扫描 / 资源表读写 / 引用判定唯一真源
│   ├── asset_events.py   文件监听与事件总线
│   ├── asset_engine.py   编排层：目录变化 → 重载 → 同步进表格
│   ├── asset_extensions.py / asset_completion.py
├── story/      剧本表与 DSL
│   ├── beat_model.py     Beat 数据结构 + 列契约 + 单元格编解码 ← 加列改这里
│   ├── beat_build.py     .rpy / DSL → Beat
│   ├── beat_render.py    Beat → Ren'Py 代码
│   ├── excel_export.py   Beat → Excel（主表 + 派生视图 + 图库/缩略图）
│   ├── excel_import.py   Excel → Beat
│   ├── generation_guard.py 生成安全门禁 + 落盘 ← 落盘一律过这里
│   ├── paths.py          项目路径判定（门禁与缩略图共用）
│   ├── script_table.py   **兼容门面**（re-export，老 import 不用改）
│   ├── dsl.py            单元格人话 ⇄ Ren'Py 代码（唯一翻译层）
│   ├── stage_snapshot.py 舞台快照 / 演出时间轴（派生视图）
│   ├── atl.py            项目 transform（ATL）解析 → **舞台真实位置** + 分辨率
│   └── story_ops.py      剧情变量 vs 引擎代码的区分视图
├── pipeline/   构建与校验
│   ├── build.py          collect_report（只体检）/ build_project（出产物）
│   ├── validate.py       三级校验（L1 单条 / L2 表内 / L3 全库）
│   ├── compile.py        演出表 → .rpy + 编译结果表
│   ├── engine_exporters.py 中立 IR → Unity / Godot / CSV
│   └── renpy_exporter.py 图 → .rpy（保真，不过滤）
├── app/        **应用服务层（框架无关）← 前端只跟这一层打交道**
│   ├── beats.py          编辑语义唯一真源（Beat↔字段、立绘行、表情/位置投影）
│   ├── stage.py          舞台推导（Beat 序列 → 可绘制帧 + 素材解析）
│   ├── editor.py         `EditorSession`：载入/写回/**保存/撤销**/导出/体检/分析
│   ├── notify.py         通知端口（后端只汇报，不弹框）
│   ├── picker.py         资产选择器的语义层（分组/搜索/image spec）
│   ├── thumbs.py         缩略图内核（缓存键 + 后台解码，两个前端共用）
│   ├── asset_panel.py    资产面板数据源（树/详情/搜索/统计/角色视角）
│   └── tables.py         剧本表（Excel）双向工作流 ← 含产物落点与门禁编排
├── ui_qt/      **PySide6 界面（唯一前端）**
│   ├── main_window.py    主窗口（**两页**）：第 0 页剧情结构（画布 + 右上控制台 +
│   │                     当前模块 + 主入口按钮）/ 第 1 页卡片编辑（拍列表 + 卡片）
│   ├── graph_canvas.py   剧情结构图（模块块 + 箭头/条件连线 + 分析标记；**操作照旧版**）
│   ├── sprite_grid.py    立绘网格（真缩略图 + chip）
│   ├── asset_picker.py   资产选择器（换图用）
│   ├── stage_preview.py  演出预览
│   ├── dialogs.py        加章节 / 编辑操作 / 加连线 / 变量分析
│   ├── help_dialog.py    「使用说明」窗口（操作提示不占主界面）
│   ├── status.py         状态栏 + Toast + 通知订阅
│   ├── tokens.py         设计 token 唯一真源（色值/字号/间距）
│   ├── theme.py          tokens → QSS
│   └── thumb_loader.py   QThreadPool 异步缩略图
├── graph/      图视图数据层与算法
│   ├── models.py / layout.py / commands.py / analysis.py / editor_utils.py
├── collab/     团队协作后端
│   └── collab_store.py   SQLite：行级锁 / 冲突检测 / 状态机 / 审计
├── cli.py      命令行入口
└── main.py     桌面入口（PySide6；没装就明确报错并给出安装命令）
legacy/         归档的单文件老版本（**不参与构建**，见其 README）
demo/           示例 Ren'Py 工程 + 一键重建脚本
tests/          1529 个用例
```

**依赖方向单向**：`core` ← 其它所有；`app` 依赖后端但**禁止**依赖任何
UI 框架；`ui_qt` 依赖全部，谁都不依赖它。
禁止反向 import，也禁止再写第二套同类实现（每个"判定"只许有一处）。

> **tkinter 已彻底移除（2026-10-01）**。`storygraph/ui/` 整个包已删除，
> 界面只剩 `ui_qt/`（PySide6）。原来混在界面里的编辑语义全部搬进了
> `app/`，所以仓库里**不可能**再出现"两套前端各写一套语义"。
> `tests/test_ui_integration.py::test_whole_codebase_is_tkinter_free`
> 是这条的守门测试（AST 扫描 + `sys.modules` 检查）。

## 数据流

```
.rpy ──RenPyLexer──► LogicalLine 树 ──RenPyParser──► AST
                                              │
                                              ▼
                         ParseResult（Statement 流，唯一中间层）
                          │           │              │
                          ▼           ▼              ▼
                      Graph 节点    剧本表 Beat     引用分析
                                  │        │
                    人话 DSL  ◄───┘        │
                    （策划写）             ▼
                          编译器 ──► .rpy（能跑）+ 编译结果表（同源）
```

两张表三个视图：**Beats 表**给人编辑，**资源图库 / 舞台快照 / 演出时间轴**
是只读派生视图。派生视图改它没用（重算即覆盖），别再往 Beats 里加派生列。

## 关键不变量（改动前必读）

1. **`Statement` 是唯一中间层**：图与剧本表都从它派生，禁止再写第二套 AST walk。
2. **引用判定只许有一处实现**：`asset_registry.resolve_references`
   （编译器、校验、抽取、导出全走它）。曾经各写一套导致 27 条假 TODO。
3. **`dsl.normalize_text` 只归一化分隔符**，绝不能动 `，（）`——
   它们是台词与素材文件名里的**内容**（`bgm/夜晚，断电之后….mp3`）。
4. **编译路径不做全局校验**：找不到资源就写 `# TODO: 缺少资源 …` 并**不生成**
   那条非法语句。体检是独立工具（`cli.py validate`）。
5. **生成门禁是最后一道闸**：`write_generation` / `write_beats_to_rpy`。
   命中 block 级问题（中文图像名、产物落进 `game/` 时 label 重名）就**不写目标
   路径**，改写 `<stem>.blocked.rpy`。所有落盘路径都必须过它。
6. **校验分级要克制**：某类资产**已有资源表**而引用不在其中 → 错误；
   项目压根没建这类表 → 警告。宁可少报，也不能刷屏（曾一次报 1073 条）。
7. **`BEAT_FIELDS` 是列顺序唯一真源**（22 列，含隐藏的 `StageOrder` / `OpOrder`），
   `Beat.to_row/from_row` 必须逐列对应。
8. **改中文 `.py` 只能用编辑器工具或 Python 脚本**（`encoding='utf-8'`）。
   PowerShell 的 `Set-Content` 会按 GBK 往返，把整个文件写坏且**不可逆**。
9. **依赖方向单向**：`core` ← `parsing` / `excel` ← `assets` / `story` ← `pipeline`
   ← `app` ← `ui_qt`。**后端与 `app/` 一律不许 import 任何 UI 框架**，
   `app/` 也不许 import `storygraph.ui_qt*`（历史上后端为了弹一个框而
   `import tkinter`，导致无头环境直接跑不起来）。
   需要提示用户时走 `storygraph.app.notify`（后端只汇报，前端订阅后决定怎么显示）。
   注意判据要精确：`storygraph.ui_qt` 以 `storygraph.ui` 开头，
   用 `startswith('storygraph.ui')` 会把 Qt 前端自己判成违规。
10. **编辑语义只有一处**：Beat ↔ 卡片字段、立绘行、表情/位置投影全在
    `app/beats.py`；舞台推导全在 `app/stage.py`；Excel 双向工作流全在
    `app/tables.py`。前端（`ui_qt/`）只做"摆控件 / 读表单 / 写表单"。
11. **前端只碰 `EditorSession`**：载入 / 投影 / 写回 / **保存 / 撤销** /
    导出 / 体检 / 分析都在 `app/editor.py`。窗口里**不许**出现
    `beat.xxx = ...`，也不许跨对象摸 `_` 开头的成员。
12. **`Stage` 是投影，不是输入**（除非用户手改那一格）：把显示值原样写回再
    `hydrate` 就是"自己吃自己"，实测 `hide cg03` 会静默变成 `cg03; cg03`。
    判据是 `typed != loaded`（**不是**"格子非空"），而**空值永远走重新投影**
    —— 清空一个框不该抹掉这一拍的 scene/show/hide。见
    `EditorSession._apply_stage`。
13. **保存 / 撤销的语义在 `app/editor.py`**：`save()` 用**保存前后两份快照的
    指纹**判"改没改"（**不能**用 `Beat.content_hash` —— 那是持久化字段，
    编辑后还是旧值，会把"改了"判成"没变"）；`undo()` 是**逐拍单级**撤销
    （用户按的是"这张卡片"的撤销，回退整章会抹掉别人的改动）。
14. **界面主次不能反**：第一屏是**剧情结构图**（剧情树 + 模块图 + 静态分析），
    卡片编辑器是"点进一个模块"才出现的一页，**不是常驻侧栏**。
    键盘快捷键按页分流（结构页回车 = 进这一张卡片；卡片页回车 = 下一拍）。
    见 `ui_qt/graph_canvas.py` 的模块文档与
    `tests/test_ui_qt.py::TestKeyboardIsPageScoped`。
15. **节点尺寸与层间距是一份数据**：`ui_qt/graph_canvas._layout_gaps()` 由
    `NODE_W/NODE_H` 算出间距再传给 `graph/layout.py`。两处各写一个数必然
    漂移（历史上节点放大到 208×104、间距还是 120×60 时代的 180/60，
    同层块垂直重叠 36px，"结构化排版"直接变成一堵糊在一起的方块墙）。
16. **资源表 ≠ 剧情图**（两条路不许混）：剧情图里**只放用户自己导入的章节**
    （`app/editor.import_chapters`，累加；可整章移除），而
    「打开项目」只加载资源表 xlsx（`app/editor.load_assets`，**签名里没有
    graph** —— 想灌也灌不进去）。绝不能用"项目里有什么"推出"图上画什么"：
    那会把 40 个脚本一次画成 51 个块。连线要两端都在图里才画得出，
    只有一端时后端会建占位块。
17. **鼠标/键盘操作以 tkinter 旧版为准**（用户 2026-10-03："你要遵照我旧版
    的操作来！不要乱改"）：Shift+左键拖=连线、左键选中/拖动、**双击块=编辑
    剧情变量**（不是进卡片）、双击空白=新建、**滚轮直接缩放**（不用 Ctrl）、
    空格+左键/中键拖动=平移。契约写在 `ui_qt/graph_canvas.py` 的类文档里，
    由 `tests/test_graph_canvas.py::TestOldInteractionModel` 用**真鼠标事件**
    钉住。⚠ 事件处理器里抛异常只有真发事件才测得出（`Qt.SpaceModifier`
    在 PySide6 里不存在，曾让**左键点块/拖块全废**）。
18. **变量视图里只放剧情变量**（用户 2026-10-03："技术代码不是变量，是具体的
    实现细节"）：变量对话框、块上摘要、鼠标提示、变量分析文本一律走
    `story_ops.narrative_operations`；技术代码**只报条数、不给名字**，
    保存时由 `story_ops.merge_narrative_operations` **原位保留** ——
    直接把整份 `operations` 换成编辑器内容的话，"改一个 `love += 1`"
    会顺手删掉整章的 `renpy.transition(...)`，而用户看不见也就发现不了。
19. **右上角必须有控制台**（用户 2026-10-03："导入的时候没有反馈！"）：
    导入 / 打开项目 / 重载 / 清空 / 移除 / 载入 / 导出 / 体检 / 变量保存都
    `_log()` 一行；分析结论按旧版"先清空再写"（它是当前状态，不是流水账）。
20. **`QDialog.Accepted` 是类属性，`dlg.Accepted` 会抛 `AttributeError`** ——
    而异常抛在 Qt 槽里是**静默失效**（只在 stderr 留 traceback）。
    曾经的后果：「连线」「新章节」「编辑变量」三个动作**从来没生效过**，
    用户"连了没反应"。判据两道：AST 守卫（禁止实例取 `Accepted`/`Rejected`）+
    **真跑一遍**的用例（patch 掉 `dialog.exec`，断言图里真的多了边/节点/变量）。
21. **分支条件要能推出正确分支**：`if a / elif b / else` 的第 2 条边条件是
    `b 且 not a`（`else` 是 `not (a or b)`）；`menu` 选项的**文字**进
    `option_text`、**条件**进 `condition`。AST 与 Statement 两条构图路径
    必须同一套规则，并由区间分析验证
    （`test_pipeline_batch.py::TestBranchConditionsAreLogicallyCorrect`）。
22. **异步结果必须核对 key**：缩略图的 `slot` 只标识"哪张卡"，同一张卡换
    表情会连发多个请求 —— 不核对 key 就会出现"旧结果顶掉新回调，还把旧图
    缓存到新键上"（表现：换了内容图不出来）。
23. **缩略图解码走共享内核**：`ui_qt/thumb_loader` 必须用
    `app.thumbs.decode_sync`（解码器级降采样 + 磁盘缓存）；自己
    `QImageReader.read()` 整图再缩是**慢一个数量级**的那条路。
24. **图片路径两级查找**：资源表（`session.path_of_image`）→ 目录约定
    （`game/images/<tag>/<attrs>.png`）。CG 常常**不在资源表里**
    （实测 2828 条无一条 cg），只查一层就会出现"CG 永远不显示"。
25. **图里没有线时要说清"还缺哪个文件"**：`missing_chapter_files` 同时查
    **两个方向** —— 占位块（被跳到、文件没导入）谁声明、以及**谁引用了我
    已导入的章节**（导入的章节自己不含跳转时，串起它们的是另一个文件）。
    只查一个方向时，`chapter1_1/1_2/1_3` 这种情况会**什么都提示不出来**。
26. **资产注册表只有一个来源**：`_registry_provider()` 必须先取
    `session.assets_registry()`（「打开项目」装进来的那份）再回落引擎 ——
    只问引擎时，真实应用里它永远是 None，于是 `pick_image(registry=None)`
    **静默返回 `''`**，用户看到的只是"按钮没反应"。
    任何"拿不到资源表"的路径都必须**说出原因**，不许静默。
27. **卡片三格（背景/音乐/语音）的候选从资源表来**（`session.asset_choices`，
    与 Excel 候选表同源）+ 每格「选择…」；**手写的自定义值不许被候选清空**。
    候选值是"脚本里该写的那个"：image → image spec；音频 → 资产 ID
    （渲染器负责翻成真实路径，查不到写 `# TODO: 缺少资源`）。
28. **显示用的值 ≠ 写回的值**：立绘位置若来自继承（Ren'Py 的 `at` 是持续
    状态），卡片要显示 `i11（继承）`，但**保存时不许把它物化**到这一拍；
    点一下 chip 才算"用户改了"。同理适用于任何"从上一拍继承"的持续状态。
29. **结构编辑要能收回来**：插入/删除卡片后 `seq`/`line_id` 必须重排；
    新卡字段**全空**（复制上一拍会把持续状态写死）、`label` 沿用邻拍；
    删除**先确认**、可「↩ 撤销删除」、且一章至少留一拍。
30. **连线的观感约定**（用户 2026-10-03）：端点取**块中心**（块不透明、
    边 z 更低 → 线从块里连出来）、线走**直线**、**箭头画在中点**。
31. **布局 JSON 的格式与旧版逐字段一致**（`EditorSession.save_layout`：
    nodes/edges/next_ids，含 `is_current`、`operations`、`file_path`）。
    换格式会让用户以前手排的版式**全部作废**，所以只加不改；
    `load_layout` 与 Qt 的 `_apply_layout_data` 共用
    `app.editor.graph_from_layout_dict`（两份转换逻辑一漂移，"撤销后加载的图"
    就和"直接加载的图"不一样）。
32. **控制流导出复用 `pipeline.renpy_exporter.export_to_renpy`**（每个模块
    一个 `<label>_control`）：它自己吞掉写盘异常只发 `notify`，所以
    `export_control_flow` 要**回头验文件在不在**才算成功。
33. **图级撤销/重做必须真的接上**：命令对象（`graph/commands.py`）要一套
    "视图接口"——`graph` / `selected_node` / `selected_edge` /
    `on_graph_structure_changed` / `on_graph_content_changed` /
    `on_current_changed` / `update_node_position` / `_apply_layout_data`。
    增删块、增删连线、改变量、导入、清空、加载布局**都要走命令**；
    "先走语义层再记历史"的场景（变量编辑要保留技术代码）用
    `_record_applied` 记，别重复 execute（否则 `old_ops` 会记成新值）。
    Ctrl+Z / Ctrl+Y **按页生效**：结构页 = 图，卡片页 = 这一拍。
34. **资产选择器的列表必须固定网格**：`setGridSize` + `setUniformItemSizes`
    + 统一 `iconSize` + `setWordWrap(False)` + `ElideMiddle`。
    图片尺寸不齐时，IconMode 的默认配置必然错位（大图挤小图、文字叠图）。
35. **迁移时按 UI 入口清单对照**：`保存布局 / 加载布局 / 导出控制流 /
    图级撤销 / Ctrl+S·O·A` 这些在 Qt 版曾整批消失，而后端与测试一直都在 ——
    "核心功能能跑"不等于"入口齐全"。同类的还有：多引擎导出
    （`engine_exporters`）、资产·角色统计（`app.asset_panel`）、协作库
    （`collab_store`）、单章三级校验（`validate_all`）——都在「🧰 更多工具…」里。
36. **增量导出（patcher）两条硬规则**：
    ① **不许用"当前值"去校验原文** —— `_range_looks_like` 曾用 `beat.text`
       判断"这段原文属于这一拍"，用户刚改过台词时必然失败 →
       **改了旁白导出后那句台词原样不动**。旁白（无说话人、无舞台）要用
       "与**新生成**段的行数 + 相似度"兜底，且比的是**精确区间**而非带余量的窗口。
    ② **`src_first/src_last` 是运行期元数据** —— 任何"只在这两个字段存在时"
       才跑的检查（比如"表里删了拍"的 `dropped`），在编辑器那条路上会被
       **静默跳过**（载入章节 → 删卡片 → 导出 = 删掉那拍还在文件里）。
       删拍检测必须每次导出都做（`recover_source_ranges(apply_ranges=False)`）。
37. **增量退回全量是安全方向，但有代价**：整体校验判"生成内容有行落不进原文
    → 退回全量"是对的（绝不写半对的脚本），但全量会带上 transform 定义与
    自己的文件头、并归一化空白 —— 对"定义放在 definitions.rpy"的项目，
    退回全量意味着把定义重复写进章节文件。所以导出状态里的
    「增量补丁 / 全量重生成（原因）」必须让用户看得见。
38. **新入口要连数据源一起接**：`AssetPanelData` 要 `engine.linker`（没有它
    角色台词量永远是 0），`ValidationReport.summary` 是**方法**不是属性 ——
    "接上了"不等于"有数据 / 没把方法对象打出来"。
39. **新增/删除卡片的"对照"**（用户 2026-10-05 追问）：
    · **删除**：`dropped` 必须每次导出都查（见 36②）；
    · **新增**：新拍没有行号，补丁的 `usable` 会把它整个过滤掉 → 内容
      静默丢失。要给锚点（前一拍末行之后 / 后一拍起始行之前）、按**插入**
      处理、缩进借锚点行（`render_beat_to_rpy` 返回的是不带基础缩进的相对行；
      锚点是 `label`/注释这类顶层行时改用下一行的缩进，否则顶格写会读坏文件）；
    · 对位率的**分母要扣掉新增的拍**（它们本来就没有源行可对位，否则
      "4 拍文件插 1 拍"永远过不了 90% 门槛）。
40. **选择器的条目矩形必须等于格子**（`it.setSizeHint(CELL)`）：`setGridSize`
    只管格子间距 —— `uniformItemSizes(True)` 时 Qt 拿**第一条的 sizeHint** 当
    全网格尺寸，而它是在"缩略图还没异步到"时算出来的，只有文字那么大
    （实测 44×19，格子是 130×118）。用户 2026-10-05 一次报的两个毛病
    **是同一个根因**：
      · 图片画在条目矩形**外** → 点在图片上 `indexAt` 命中无效 → "想要选中图片，
        点击后没反应，显示并没有选中任何一条"；
      · 可见范围按格子高算、实际每行只有 19px → 真正看得见的格子永远不被请求
        → "部分照片未加载，缺口大"。
    显式设 sizeHint 之后两条一起好。可见范围仍用"滚动位置 ÷ 格子高"算
    （不依赖"视图已经布局完"，首屏 `_load_items` 时还没画过），那时它才是准的。
41. **键盘状态要在窗口级跟踪**：画布的 `keyPressEvent` 只在它有焦点时收到，
    而用户按空格时焦点常在工具栏/右栏 → "空格+左键平移"没反应。
    窗口 `eventFilter` 里跟踪并推给画布；**文本框里要放行**
    （否则空格打不出来）—— 判定同时看**事件接收者**与 `focusWidget()`。
42. **全量产物不许重复写 transform 定义**：项目里 `definitions.rpy` 已声明过的，
    生成物再写一遍就是重复定义（Ren'Py 后者覆盖前者 → 章节文件被悄悄改语义）。
    `EditorSession.should_write_transforms()` 扫一遍项目其它 .rpy 决定；判定只针对
    **资源表里带 ATL 的变换**（`used` 里混着 `left`/`dissolve` 这类内置变换，
    一起判会永远得出"要写"）。实测 `chapter1_1` 差异 151→127 行、`chapter1_2` 67→49 行。
45. **`call X from Y` 的 `from` 必须一路保住**（用户 2026-10-05："先修复子句
    会丢的问题"）：它是 Ren'Py 的**存档点标识** —— 同一个 label 被多处 call 时
    靠它区分存档属于哪一处，丢了就是"语义等价但老存档回滚"。
    要动的地方：`Edge.from_clause`（+ `add_edge` + `__deepcopy__`）、
    `CallNode.from_clause`（解析时抓）、`Statement.make_call(from_clause=)`
    （AST 与 Statement 两条路径都要）、导出器写 `from`、布局 JSON 存它
    （布局 JSON 唯一新增字段就是它，用 `.get` 读，**旧文件照样能加载**）。
    **去重键不要带 `from_clause`** —— 同一个 (from,to,type,cond,option) 就是
    同一条边，带上它会让两条解析路径各建一条（实测边数翻倍）；正确做法是
    "基础键去重 + 谁抓到 `from` 就补到那条边上"。
43. **增量删行（2026-10-05 已实现）**：核心不变量是 `_replace_matched` 的
    **一对一替换、行数永不变化**（按"行的身份"配对：台词配同说话人、
    `show ling` 配同 tag、其余只与逐字相同者配）——"什么都没改 → 逐字节等于
    原文"就是这么来的。**删行会破坏这条不变量**，所以必须用与"插入"同一套纪律：
    ① 对齐交出**被删源拍本身**（`recover_source_ranges(dropped_out=…)`），
       据此算出要删的精确区间；
    ② **不安全的删除一律放弃**（整章退回全量）：区间越界/为空、
       含块头（`menu:`/`if`/`elif`/`else`/`python:`/`transform`…）、
       与**仍在的拍**区间交叠（说明那几行不只属于被删的拍）、
       或"新插了一拍"被误认成"删一拍+插一拍"（`dropped_src` 要一起清掉）；
    ③ 删除与插入**一起按位置从后往前**做（先动后面的行，前面行号才不受影响）；
    ④ 改完仍要过全局校验（label/return 计数、台词多重集、能重新解析），
       任一条不过 → 退回全量。
    实测（真实项目 `chapter1_2`）：删一张纯台词卡 → 增量 ✓ 删 1 行、差异 1 行、
    214→213 拍；删到"与邻拍共用同一行"的旁白 → **拒绝删**（交叠）→ 退回全量。
44. **只差空白的行不许被替换**：原文 `jc  "…"`（两个空格）、生成器 `jc "…"` ——
    Ren'Py 两者等价，改它没有任何好处，却让每次导出的 diff 多出一堆
    "看起来没改"的行。`_replace_matched` 里加一条 `_collapse(body) ==
    _collapse(pick)` → **保留原文那一行**。实测：`chapter0_0`（满篇双空格）
    从"每次导出 10+ 行差异（还会因退回全量丢注释）"变成
    **原文不动=0 行差异、改一拍=正好那一行**。
46. **图片要快 = 四件事一起做**（用户 2026-10-05："卡牌界面图片加载慢"）：
    真实立绘是 **4320×7680 / 9.4 MB** 的 PNG —— 冷解码 ≈ 600 ms、
    磁盘缓存命中 ≈ 1 ms，所以"慢"全在**第一次**和**线程池排队**上：
      · **解码"先粗后精"**（`app.thumbs._downscale`）：已经是 RGBA 就不
        `convert`（省一份 133 MB 缓冲拷贝），先用 `reduce()`（BOX 均值）砍掉
        一个整数倍、再 `thumbnail()` 收边。实测 562 ms → 349 ms，缩略图
        肉眼无差（逐通道平均差 1.2/255）。
      · **第一次请求不等防抖**：防抖只该合并"同一个 slot 被连续改"（chip 连点）；
        每个 slot 都先白等一个周期（默认 120ms）= 一屏 N×120ms。
      · **相邻几拍预取**（`ThumbLoader.prefetch` + `SpriteGridWidget.prefetch`，
        由 `MainWindowQt._prefetch_neighbours` 在 `_show_index` 里触发）：
        预取只填缓存、不回调；键必须与真正的请求**完全一致**（同一条路径解析
        `resolve_sprite_thumb_path` + 同一个尺寸 `sprite_thumb_size()`），
        否则预取等于白做。实测真实项目 155→156 拍（`ye happy` → `ye hi`）：
        冷 408 ms → 预取命中 **10 ms**。张数有上限（默认 8 张），
        不许把线程池占了、反把当前这一拍挤慢。
      · **骨架占位走独立回调**（`request(..., placeholder=)`）：`apply(None)`
        在立绘卡里的含义是"这张图读不出来" —— 拿它当骨架，图还在解，
        卡片上就已经写着"可能损坏"了。
47. **空格平移的判定不许把只读控件算成"文本框"**（用户 2026-10-05：
    "拖拽画布的修复有一个bug……时灵时不灵"）：控制台是**只读**
    `QPlainTextEdit`，用户在它里面划一下选文字，焦点就落在它身上 ——
    此后"空格 + 左键拖"一律不平移，点一下画布又好了（"时灵时不灵"）。
    `_is_text_widget` 必须看 `isReadOnly()`。配套两条：
      · **窗口失活（`WindowDeactivate`）要清掉 `_space_down` 与进行中的手势**：
        Alt+Tab 出去时空格抬起与鼠标释放都收不到，状态会卡住 ——
        卡 True：之后每次左键拖块都变成平移（块拖不动、点选像没反应）；
        卡 False：空格平移彻底没反应。画布侧兜底 `cancel_interaction()`。
      · **焦点跟着页走**（`_focus_into_card_page`）：焦点留在已隐藏的控件上时，
        敲字没反应，`_text_input_has_focus` 也会判错对象。
      · 块被拖远时 `note_content_changed()` 要把**场景矩形**撑大 ——
        可平移范围由它决定，否则那块"看得见、到不了"。
48. **图重建要保住缩放**（用户 2026-10-05："连线之后，请保持原来的缩放……
    现在每连一个线，缩放就变"）：图的重建只有一个入口
    （`MainWindowQt._set_graph` → `GraphCanvas.set_graph`），连线 / 撤销 /
    改内容 / 加删块都走它。默认**保住变换矩阵 + 视口中心**
    （`_view_state()` / `_restore_view()`）；只有两种情况复位：有**没摆过的**
    新块（要重排，必须重新适应）或调用方显式 `keep_view=False` ——
    加载布局 JSON 属于后者（那是"我要看这张新图"，主窗口顺手
    `fit_to_content()`）。
49. **舞台是无限的**（用户 2026-10-05："这个拖不动是因为你的这个舞台有限……
    我的 tkinter 做的是无限的舞台……目前我不能整个平移"）：旧版 tkinter 是
    "世界坐标 + offset"自己画的，**没有边界**；Qt 版用 `QGraphicsView`，
    可平移范围被 `sceneRect` 框死（内容 + 60% 余量就是尽头）。
    现在**可见区离边界近了就把矩形往外长一截**（`_extend_scene_rect`，
    每边至少 700 场景像素、或可视尺寸的 60%），并且：
      · 长的时候用 `centerOn(视口中心)` 把画面**摁住不动**（否则滚动条重算会跳）；
      · 平移时**先撑再滚**（`mouseMoveEvent` 里先 `_extend_scene_rect()`）——
        到了边界再 `setValue` 会被夹住，那一下位移就丢了；
      · 缩小（可视区变大）后也撑一次（`zoom_by`），否则缩得越小越拖不动。
    实测（真实控制流图）：连拖 10 次累计 3686 场景像素、**一次都没卡住**，
    `sceneRect` 自己从 1409×1305 长到 5466×4348。
50. **缩略图是优先级队列，不是先进先出**（用户 2026-10-05："这个选择器内图片的
    加载还是不够迅速，优先加载目前在窗口内的图片！"）：以前每个请求直接丢进
    `QThreadPool`，而它是**先进先出**的 —— 滚过去的那几十上百张排在前面，
    现在看得见的那一屏要等它们全解完（实测那一屏出图 **4.28 s**）。
    `ThumbLoader` 现在自己调度：
      · `_want`（想要什么，最新一次为准）+ `_running`（同时最多
        `max_in_flight` 张）+ `_pump()`（谁空出来按 (优先级, 先来后到) 补一张）——
        **池子里永远只有我们当下最想要的那几张**；
      · 优先级 `PRIORITY_FOCUS`（窗口内可见）> `NORMAL`（卡片上的立绘）>
        `BACKGROUND`（预取、滚过去的）—— 预取**永远不许挡路**；
      · 选择器每次算完可见范围就 `focus(窗口内那一屏的 slot)`。
    ⚠ **坑（实测踩到）**：`focus()` 必须把"窗口内、还没有图"的**全部**格子提上来，
    不能只提"这一次新请求的" —— 因为每次都会多留"上 1 行、下 3 行"，
    所以滚到下一屏时那一屏**早就排过队了**，只提新请求的等于 `focus([])`
    （谁都没提）。修好之后：那一屏 **1.76 s**（= 4.28 s 的 2.4× 快；
    换真实 9.6 MB 立绘分组做 A/B：**4.48 s → 2.24 s**）。
51. **演出预览的立绘按"项目 transform 的真实几何"画**（用户 2026-10-05：
    "我觉得这个 transform 未对应到舞台真实位置"）：以前只在资源表那一行里找
    `xpos` 字面量，找不到就按首字母猜（`i/f/l/r → 0.35/0.65/0.25/0.75`）——
    而真实项目是**链式调用**：

        transform f21:
            focus(320)
        transform focus(x=640, z=0.30):
            xcenter x xoffset 400 yoffset 1200 yanchor 1.0 ypos 1.03 zoom z*1.00

    于是 `f21`/`f22` 都算成 0.65（叠在一起）、`zoom 0.30` 完全没体现。
    现在 `story/atl.py` 扫项目源码、**跟调用链、绑参数**、解释 ATL 属性，
    取"最终静止态"（`on show:` 那些事件块不算），`app/stage.sprite_box` 再按
    **原图尺寸 × zoom + 锚点/偏移**算出屏幕矩形。三条硬规则：
      · **整数 = 像素，小数 = 屏幕比例**（`ypos 1.03` 是 103% 屏高，不是 1.03 像素）；
        链路绑定拿到的是 float，代回源码时必须还原成 `320`（见 `_fmt`），
        否则像素值会被误判成比例；
      · **原图尺寸必须参与**（`app.thumbs.natural_size` 只读文件头，~1ms）：
        真实立绘 4320×7680、`zoom 0.30` → 1296×2304，比 1080 高的屏幕还高；
      · 算不出来就**退回老示意图**（`from_atl` 为假），不硬画。
    实测 `f21`→x=0.375 / `f22`→0.708 / `i11`→0.542、zoom 0.30，与手算一致；
    与游戏存档里的真实截图对比，取景一致（角色大、底部被对话框切掉）。
52. **预览要真的出声**（用户 2026-10-05："第一个是 bgm 未被实际播放"）：
    `ui_qt.stage_preview.StageAudio` 用 QtMultimedia 放 BGM / 音效 / 语音。
    语义照 Ren'Py：
      · **BGM 是持续状态** —— 同一首不重放（靠 `_music_path` 判），换曲才
        `stop + setSource + play`（`setLoops(Infinite)` 循环）；
      · **音效 / 语音一次性** —— 换到那一拍响一次；**同一帧重画不重喊**
        （切「真实贴图」开关会重画，靠 `key=(line_id, idx)` 判）；
      · 素材解析 `app.stage.resolve_audio_path`：资源表 `Path`（含"带项目目录
        前缀"那种写法，**先按相对当前项目解释**）→ 路径 → `game/bgm|audio|…`
        按名字找；**每一步都验文件存在**，绝不返回不存在的路径；
      · 没装 QtMultimedia / 没有音频设备 → 退化成静音 + 在界面上说明原因
        （`note()`），绝不因为放不出声把预览搞崩；播放器可注入（测试用假播放器）。
    UI 上有「🔊 声音」开关（默认开）；**关窗必须停声**（否则 BGM 一直响）。
53. **图像名要照脚本认，不只是照表认**（用户 2026-10-05：
    "你确定 bgm 导入了吗？背景导入了吗？是否正确呢？查询一下"）：
    预览第 63 拍的 `bg bg11_0` **一片黑**，而资源表里只有磁盘扫描出来的 `bg11`
    ——"`bg11_0` = `bg11.png`"这条对应关系**只写在脚本里**：

        definitions.rpy:146   image bg bg11_0 = "images/bg/bg11.png"

    Ren'Py 是照脚本认的（`scene`/`show` 查的就是这张声明表），所以预览也必须
    照脚本认。`app/stage.script_images` 只扫 `*.rpy` 的顶层 `image` 行
    （实测真实项目 41 个文件 / 104 条声明 / 70ms，按项目缓存），
    解析顺序变成**三级**（每级都验文件真的存在）：① 资源表 → ② 脚本声明
    （含 `image black = "#000000"` 这种纯色）→ ③ 磁盘按名字回退。
    实测真实项目 **1537 拍里有背景的，修前 349 拍是黑的（`bg11_0` 这类别名
    全落空），修后 0 拍**；卡片上的缩略图走同一条兜底（`path_of_image`）。
54. **"没有声音 / 没有画面"不许静默 —— 缺什么要写在脸上**（同上那次排查）：
    实测真实项目 21 处音频引用里 **20 处的文件在磁盘上不存在**：

        chapter1_4.rpy:169   play music "bgm/夜晚，断电之后…….mp3"

    磁盘上真名是 `夜晚，断电之后，不存在的教室.mp3`（作者当初把长名字省略着写，
    后来文件改名，脚本没跟着改 —— **游戏自己也会找不到**）。所以：
      · `resolve_audio_path_info` 精确解析失败后按**公共前缀**找唯一近似
        （`MIN_APPROX_PREFIX=4`；并列第一且**不是同一首**就不猜）；
      · 同一个文件名的多份（真实项目有一层重复嵌套的 `sr_general/` 目录）
        或 mp3/wav 两种格式 → 算**同一首**，取离项目根最近的那份；
      · 近似只用于**播放**：`resolve_audio_path` 默认仍然只认精确命中
        （"这个名字到底有没有文件"是能写进报告的事实，不许被近似污染）；
      · 缺文件 / 近似匹配 / 缺图都写在预览横条上（`stage_audio_cues` 的
        `notes`/`missing` + `StageCanvas.missing_assets`）。
55. **加载中的占位要低调，缺图的占位要显眼**（用户 2026-10-05："每次加载时……
    总会有一个蓝色框当占位符一闪而过，影响观感"）：
      · 解析到路径、只是在解码 → `stage.loading`（深色 + 几乎没有的 1px 边，
        不写字），图到了自然顶上来；
      · 磁盘上**找不到**那张图 → 老那套亮蓝块 + 名字 + 站位（这是要用户去修的）；
      · `StageCanvas._request` **必须给 `placeholder`**：不给的话加载器会拿
        `apply(None)` 当"骨架"，而这里的 `apply(None)` 含义是"解完了但没图"，
        加载中的标记会瞬间被清掉（实测踩到）；
      · 翻页时预取下一两拍（`PREFETCH_AHEAD=2`，`PRIORITY_BACKGROUND` 不许插队），
        换帧只丢**别的帧**的排队项、**预取要留着**（`clear_pending(keep=…)`）；
        `ThumbLoader._pump` 还会先查内存缓存 —— 预取和当前帧要的是同一张图时，
        不查就会解两遍。实测"每拍都换图"的冷缓存回放：**最久 780ms → 16ms**，
        平均 268ms → 14ms（`3/3` 拍翻到就出图）。
56. **逐条位置不许被"卡片 → show"的往返吃掉**（用户 2026-10-05：学姐第一次
    出场"原本应该是 `show ye happy at i11`，但新文件里变成了 `show ye happy`"）：
    一拍里同一角色先 `show ye happy at i11` 再换表情 `show ye care` 时，前一条
    被聚合成卡片的 `extra_rows` —— `app/beats.cards_to_show` 原先只把 `image`
    带回、**把 `at` 丢了**。触发点极隐蔽：**翻页 / 保存 / 导出 / 增删卡**都会先
    `apply_current_beat()` 提交当前拍，所以只要在那一拍上待过，位置就悄悄没了。
    现在 `extra_rows` 的逐条位置一起带回（`ye happy@i11; ye care`），并有一条
    "beat → cards → show 一个字都不许变"的回归用例。真实项目复刻用户那两步
    （删掉 `cg cg03` 那张卡 + 在那一拍上提交）后，产物与用户交的对比**只差这一行**。
57. **插入的落点必须结构合法，而且不许"报告成功、东西没了"**（用户 2026-10-05：
    "插入功能却永远只插到前面"）：
      · **块头之后要退到块外**（`patcher._insert_anchor`）：锚点行是
        `menu:` / `"选项":` / `if …:` 这类以 `:` 结尾的行时，紧贴着插会把新台词
        写进块里 —— 实测产物变成 `"去屋顶看看":` 下面同缩进的 `"新台词"`，
        Ren'Py 直接不认。现在退到**最外层包围块**（`menu:`）之后、与块头同级；
        `label …:` 例外（插在它后面正是"这一章第一拍"，不能一路退到几百行外）。
      · **表格那条路的新增行交给插入通道**（`recover_source_ranges(inserted_out=…)`）：
        原先对位给的"插入点"被当**替换**处理，插入点正好是 `label …:` 那一行时
        对位守卫判"不像这一拍"就跳过，而"什么都没改"又跳过了整体校验 →
        导出报告增量成功、产物里没有那句台词。现在有一条"新增的拍一行都没落地
        → 退回全量"的兜底，宁可重生成，也绝不报告成功却丢内容。
      · **插入的拍不许把锚点写进 `src_first`**（`insert_anchor`，运行期字段）：
        写了之后它就不再是"没有行号的拍"，**下一次导出**会被当成"替换一行" →
        实测（真实 chapter1_3）插一张卡、连导两次：第二次报告
        "增量补丁：对位 … 改动 0 行"，产物里那句**没了**。现在锚点存运行期字段、
        `src_first` 保持 0，每次导出都按"插入"重算；同时加了一条
        "**原文里根本没有的内容**没能落地 → 退回全量"的兜底（这类内容
        一动不动就是丢失，绝不算成功）。
58. **"静默失败"要看得见，而且不许再新增**（用户 2026-10-05 拿审计报告来问：
    "321 个 except 里 99 个 body 只有 pass，全项目 196 处 # noqa: BLE001 —— 是否属实"）：
    数字**逐条核过，属实**。`except: pass` 不都是错的（清理 / Qt 控件已销毁 /
    可选缓存写不进去），但它有两个真实代价：出事时**没有痕迹**、**真 bug 被吃掉**。
    所以**不删异常处理**（那会改行为），把"什么都不说"换成
    `core.safeguard.swallow('<模块.函数>', e)`（有界环形缓冲 + `report()`），
    并在界面上露出来：载入 / 导出的收尾会把**本次新增**的静默失败写进控制台
    （`[静默失败] …` + 状态栏 `⚠ 本次有 N 处错误被跳过`）。
    实测：**99 处 → 1 处**（唯一豁免是 `safeguard.py` 自己的记录失败路径 ——
    再调一次自己就是无限递归），`swallow` 调用点 99 个；
    `tests/test_code_health.py` 两条守卫盯着"不许再出现 `except …: pass`"
    与"调用点不许被回退"。
59. **两个 .rpy 生成器可以各写各的产物，但共用约定只能有一份**（同上审计：
    "`pipeline/renpy_exporter.export_to_renpy` 与 `story/beat_render.render_beats_to_rpy`
    规则链完全独立"）：属实，而且**已经各错一半** —— 用真实项目（51 个节点）
    导出控制流实测到两个会让产物跑不起来的 bug：
      · `beat_render` 认得行首 `\x01`（= 原文紧凑写 `$code`），`renpy_exporter`
        自己拼 `f"    $ {op}"` 不认得 → 产物里出现 `$ ^Apersistent.lock_lst[…]`；
      · `renpy_exporter` 对**所有出现过的名字**写 `default x = 0` →
        `default renpy = 0` / `default config = 0` / `default persistent = 0` /
        `default time = 0`（把引擎模块遮蔽成 int → 整章跑不起来），还有
        `default i = 0` 这类循环临时变量。
    修法：**约定收进唯一真源** ——
      · 标记只在 `core/statement.MARKER` 定义一处（`is_tight_dollar` / `strip_marker`），
        `beat_model` / `beat_render` / `story_ops` / `renpy_exporter` 全部走它；
      · "python op → 源文本"只有 `story.beat_render.render_code_block`
        （导出那条路传 `multiline='python'` 保持它原本的 `python:` 块形状）；
      · "哪些名字配 `default`"只有 `core.utils.declared_variables*`
        （只认**被赋值的名字**，剔掉引擎/标准库模块与循环临时变量）。
    实测修后：产物里没有裸 `\x01`、`default` 行里没有引擎名；
    `tests/test_code_health.py` 锁住"这三条规则不许再分叉"。

## 常见改动落点

| 想做什么 | 改哪里 |
|---|---|
| 给剧本表加一列 | `story/beat_model.py` 的 `BEAT_FIELDS` + `Beat.to_row/from_row` |
| 改导出的生成代码 | `story/beat_render.py`（`render_beat_to_rpy`） |
| 改 Excel 长什么样 | `story/excel_export.py` |
| 加一条门禁规则 | `story/generation_guard.py` |
| 让人话多认一个词 | `story/dsl.py` 的 `DEFAULT_POSITIONS` / `DEFAULT_EXPRESSIONS` / `VERB_ALIAS` |
| 加一条校验规则 | `pipeline/validate.py`（选好 L1/L2/L3 与 severity） |
| 加一个导出目标 | `pipeline/engine_exporters.py`（先扩中立 IR，再写适配器） |
| 改**编辑语义**（卡片怎么读写 Beat） | `app/beats.py`（唯一真源）—— `cards_to_show` 必须带回 `extra_rows` 的**逐条位置**（见不变量 56） |
| 改**插入 / 删除 / 增量补丁怎么落行** | `story/patcher.py`（`_insert_anchor` 定位 + `patch_export` 的插入/删除通道，见不变量 57） |
| 改**舞台推导 / 预览语义** | `app/stage.py` |
| 改**图像名怎么解析**（资源表 → 脚本 `image` 声明 → 磁盘回退） | `app/stage.py` 的 `resolve_stage_asset` / `script_images`（见不变量 53） |
| 改**预览的加载占位 / 预取 / 缺什么写在横条上** | `ui_qt/stage_preview.py`（`StageCanvas._draw_block` / `prefetch_frame` / `_summary_text`，见不变量 55） |
| 改**transform 怎么算成舞台位置** | `story/atl.py`（解析 + 求值）/ `app/stage.py:sprite_box`（原图尺寸 × zoom + 锚点） |
| 改**预览的声音**（BGM/音效/语音） | `app/stage.py` 的 `resolve_audio_path_info`/`stage_audio_cues` + `ui_qt/stage_preview.py` 的 `StageAudio` |
| 改**载入/写回/保存/撤销/导出/体检/分析的编排** | `app/editor.py`（`EditorSession`） |
| 改**结构图的画法 / 排版 / 分析标记** | `ui_qt/graph_canvas.py`（间距真源 `_layout_gaps`）+ `graph/layout.py`（只管分层） |
| 改**模块在图上显示什么** | `story/story_ops.py`（剧情变量 vs 技术代码的过滤，图与列表共用） |
| 改 **Excel 双向工作流**（生成表 / 写回 .rpy / 落点 / 门禁顺序） | `app/tables.py` |
| 改**资产面板 / 选择器的数据口径** | `app/asset_panel.py` / `app/picker.py` |
| 改**选择器格子的几何**（缩略图/文字/命中区） | `ui_qt/asset_picker.py` 的 `THUMB` + `CELL` —— **条目 sizeHint 必须一起设**（见不变量 40） |
| 改**缩略图缓存或解码** | `app/thumbs.py`（内核）/ `ui_qt/thumb_loader.py`（Qt 调度） |
| 加一个"要告诉用户的事" | `app/notify.py`（别在后端弹框） |
| 改**"被吞掉的错误"怎么记** | `core/safeguard.py`（`swallow(context, exc)` → 控制台/`report()`；见不变量 58） |
| 改 **python op 怎么渲染成源码** | `story/beat_render.render_code_block`（两个生成器共用；标记只有 `core/statement.MARKER` 一处） |
| 改 **哪些变量写 `default x = 0`** | `core/utils.declared_variables*`（见不变量 59） |
| 改 **Qt 界面（页结构 / 工具栏 / 剧情树 / 分析面板）** | `ui_qt/main_window.py`（`legacy/` 那份**不要**动） |
| 改**配色 / 字号 / 间距** | `ui_qt/tokens.py`（唯一真源）→ `ui_qt/theme.py` 出 QSS |
| 加共享词表 | `core/vocabulary.py`（别就地再写一份） |
