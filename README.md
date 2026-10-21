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
python -m pytest                    # 5501 个用例；全量约 21 分钟（4 条真机金标占大头）

python demo/make_demo.py            # 一键重建示例工程（含产物与协作库）
python main.py                      # 启动图形界面（PySide6）
```

> 界面**只有 PySide6 一套**（tkinter 版已于 2026-10-01 彻底移除）。
> 没装 PySide6 时 `main.py` 会**明确报错并给出 `pip install PySide6`**
> ——不再有"悄悄退回老界面"那条路（那正是当年"我这里显示完全没用到"
> 的原因）。只跑无界面流程用 `python cli.py validate <项目根>`。

命令行（CI 友好，退出码 **0 通过 / 1 有错误 / 2 用法或环境问题**；`python cli.py -h` 看全部）：

```bash
python cli.py validate <项目根>              # 只体检，不写盘
python cli.py build <项目根> --engine renpy   # 体检 + 出产物（+ build_report.json）
python cli.py table rpy2xlsx <a.rpy>         # 单个 .rpy → Excel
python cli.py table xlsx2rpy <a.xlsx>        # Excel → .rpy（加 --source 可增量）
python cli.py compile <表.xlsx> --sheet       # 编译 + 另一张编译结果表
python cli.py describe <项目根>               # 摊开"工具以为的自定义结构"（只读、可 diff）
python cli.py learn <项目根>                  # 把一条自定义写法"教给"工具（写进 rules.json）
python cli.py engine-facts <项目根>           # 采集引擎事实（需 Ren'Py SDK）
python cli.py collab status                   # 协作（SQLite 后端 + Excel 前端）
python cli.py selfcheck                       # 主链路自检（打包后排查用）
```

`main.py` / `cli.py` 是**根目录垫片**，真实实现在 `storygraph/` 里，逻辑只有一份。

## 目录结构

```
storygraph/
├── core/       零依赖地基
│   ├── statement.py      Statement IR（.rpy ↔ Excel 的中枢）
│   ├── cst.py            带 span/trivia 的 CST（字节级保真的底座）
│   ├── capability.py     元模型唯一真源（State/Provenance/Transition/Coverage/Escape）
│   ├── engine_facts.py   引擎事实（图像/标签/变换…）的纯数据层
│   ├── vocabulary.py     共享词表唯一真源（资产类型/非角色 tag/内置变换）
│   ├── session_store.py  会话记忆（上次项目/章节/布局）
│   ├── safeguard.py      "被吞掉的异常"统一记账（不许 `except: pass`）
│   └── utils.py          表达式变量抽取
├── excel/      Excel 底层设施
│   └── cells.py          安全写单元格（`=` 开头的用户文本别被当公式）
├── parsing/    .rpy 解析
│   ├── renpy_parser.py       Lexer → AST → ParseResult(Statement 流)
│   ├── renpy_nodes.py / renpy_parser_stmts.py  拆出的节点类与 `_stmt_*` 族
│   ├── discover.py           扫出该被构建的 .rpy（门禁与构建共用）
│   └── character_script_linker.py
├── assets/     资产链路
│   ├── asset_registry.py   扫描 / 资源表读写 / 引用判定唯一真源
│   ├── asset_registry_*.py 拆出的常量与扫描族
│   ├── asset_events.py     文件监听与事件总线
│   ├── asset_engine.py     编排层：目录变化 → 重载 → 同步进表格
│   └── asset_extensions.py / asset_completion.py
├── story/      剧本表与 DSL
│   ├── beat_fields.py      **字段契约唯一真源**（FieldSpec：列/快照/指纹/协作 payload）
│   ├── beat_model.py       Beat 数据结构 + 单元格编解码（列定义从 beat_fields 派生）
│   ├── chapter.py          章节文档（拍 + 顶层保真块）+ Seq 排序权威
│   ├── beat_build.py       .rpy / DSL → Beat
│   ├── beat_render.py      Beat → Ren'Py 代码（代码块族在 beat_render_code.py）
│   ├── excel_export.py     Beat → Excel（主表 + 派生视图 + 图库/缩略图）
│   ├── excel_import.py     Excel → Beat
│   ├── generation_guard.py 生成安全门禁 + 落盘 ← 落盘一律过这里
│   ├── dsl.py              单元格人话 ⇄ Ren'Py 代码（唯一翻译层）
│   ├── statement_registry.py 自定义语句三来源（rules.json > register_statement > 形状）
│   ├── spec_args.py        自定义语句"每参数一列 + 原生下拉"的唯一真源
│   ├── layered_model.py    合成立绘（layeredimage）模型 + 条件求值
│   ├── cds_registry.py     CDS（register_statement）扫描
│   ├── atl.py              项目 transform（ATL）解析 → 舞台真实位置 + 分辨率
│   ├── patcher.py          原地补丁：按行身份配对替换，要么全对要么不动
│   ├── stage_snapshot.py   舞台快照 / 演出时间轴（派生视图）
│   └── story_ops.py        剧情变量 vs 引擎代码的区分视图
├── pipeline/   构建与校验
│   ├── build.py            collect_report（只体检）/ build_project（出产物）
│   ├── validate.py         三级校验（L1 单条 / L2 表内 / L3 全库）
│   ├── compile.py          演出表 → .rpy + 编译结果表
│   ├── engine_collect.py   引擎事实采集（lint + json-dump + 注入探针）
│   ├── statement_probe.py  把不认识的写法交给引擎探针
│   ├── isolated_project.py 能编译的最小工程（给探针用）
│   ├── engine_exporters.py 中立 IR → Unity / Godot / CSV
│   └── renpy_exporter.py   图 → .rpy（保真，不过滤）
├── app/        **应用服务层（框架无关）← 前端只跟这一层打交道**
│   ├── beats.py            编辑语义唯一真源（Beat↔字段、立绘行、表情/位置投影）
│   ├── stage.py            舞台推导（Beat 序列 → 可绘制帧 + 素材解析）
│   ├── editor.py           `EditorSession`：载入/写回/保存/撤销/导出/体检/分析
│   ├── tables.py           剧本表（Excel）双向工作流 ← 含产物落点与门禁编排
│   ├── asset_panel.py / picker.py / thumbs.py  资产面板 / 选择器语义 / 缩略图内核
│   └── session_*.py        编辑器会话按职责拆出的片段
├── ui_qt/      **PySide6 界面（唯一前端）**
│   ├── main_window.py      主窗口：第 0 页剧情结构（画布 + 控制台）/ 第 1 页卡片编辑
│   ├── graph_canvas.py     剧情结构图（模块块 + 连线 + 分析标记）
│   ├── window_*.py         各面板（资产 / 卡片 / 导出 / 图操作 / 键盘 / 节点状态）
│   ├── param_editor.py / column_editor.py / flow.py  自定义动作弹窗（v4，折行布局）
│   ├── dialog_learn.py / dialog_chars.py / dialog_ai.py  弹窗（教写法 / 角色 / 对话）
│   ├── panel_ai.py         AI 助手聊天面板
│   ├── sprite_grid.py / asset_picker.py / stage_preview.py  立绘网格 / 资产选择 / 演出预览
│   ├── tokens.py / theme.py 设计 token 唯一真源 → QSS
│   └── thumb_loader.py     QThreadPool 异步缩略图
├── authoring/  纯函数写作层（纯文字导入 / 教工具认写法）
│   ├── txt_import.py / docx_import.py / txt_pipeline.py  纯文字、Word 导入
│   ├── learn_statement.py  把界面填的表单变成声明
│   ├── spec_groups.py      候选分栏（项目声明"有哪几组词"）
│   └── char_decisions.py   角色决策（问"是不是这个已定义的角色"）
├── ai/         工具内 agent
│   ├── client.py           LLM 客户端（零新依赖；传输层可注入 → 判据全脱网）
│   └── agent.py            agent 循环（工具描述与 MCP 同源）
├── mcp/        MCP 服务端（把工具能力翻成模型能用的动作）
├── graph/      图视图数据层与算法（models / layout / commands / analysis / editor_utils）
├── collab/     团队协作后端（SQLite：行级锁 / 冲突检测 / 状态机 / 审计）
├── cli.py      命令行入口
└── main.py     桌面入口（PySide6；没装就明确报错并给出安装命令）
legacy/         归档的单文件老版本（**不参与构建**，见其 README）
demo/           示例 Ren'Py 工程 + 一键重建脚本
tests/          5501 个用例（253 个测试文件）
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

## 去哪看细节

| 想看什么 | 去哪 |
|---|---|
| **技术契约 + 全部不变量**（改动前必读） | [docs/契约速查_storygraph.md](docs/契约速查_storygraph.md) |
| 长期目标 / 里程碑进度 | [docs/路线图_长期目标_2026-10-08.md](docs/路线图_长期目标_2026-10-08.md) |
| 这工具到底要做成什么（产品定位） | [docs/定位重审.md](docs/定位重审.md) |
| 怎么用（AI 助手 / 自定义动作 / 纯文字导入） | [docs/使用_AI助手与自定义模块_2026-10-11.md](docs/使用_AI助手与自定义模块_2026-10-11.md) |
| 每天做了什么 | [docs/开发日志.md](docs/开发日志.md) |
| 历史设计稿 / 旧问题清单 | [docs/archive/](docs/archive/) |

## 常见改动落点

| 想做什么 | 改哪里 |
|---|---|
| 给剧本表加一列 | `story/beat_model.py` 的 `BEAT_FIELDS` + `Beat.to_row/from_row` |
| 改导出的生成代码 | `story/beat_render.py`（`render_beat_to_rpy`） |
| 改**拍内顺序**（舞台尾段 / 代码块 / 首个 `scene` 之后插代码） | `story/beat_render.render_beat_to_rpy` 的 `emit_stage_tail()` 位置 + `len(lines) > mark` 那条判据（见不变量 82a） |
| 改**选项体归属**（哪个选项拿哪段块体） | `story/beat_render._menu_segments_by_path` + `core/nest_path.option_index`（键=位置；越界才退回顺序配对，见不变量 82b） |
| 改**注释落在哪张拍上** | `story/beat_build._emit_comment`（必须过 `branch_guard`，见不变量 82c） |
| 改**哪些语句能并进同一拍** | `story/beat_build._BeatBuilder` 的 `branch_guard`（条件不同）/ `struct_guard`（**位置**不同，见不变量 83b） |
| 改**说话人怎么写回产物**（字符串说话人 `"系统"` / 角色变量 `e`） | `parsing/renpy_parser.split_string_speaker` + `story/beat_model.is_literal_speaker` + `beat_render` 台词那一段（见不变量 84） |
| 改**字符串转义**（渲染转义 / 解析反转义） | 渲染 `story/beat_render._quote`；解析 `parsing/renpy_parser.unescape_renpy_string`（`_pick_str` 会自动调，**手写切片的地方要自己调**，见不变量 83a） |
| 改**条件减法**（里层 `if` 该不该再写外层条件） | `story/beat_render.strip_condition_context` + `_split_conjuncts` / `_bare_condition` / `_condition_atoms`（见不变量 82d） |
| 改**嵌套/注释 harness 的判据** | `tools/hell_nest_inline.py`（N1 只比"菜单口径"，`§有意为之` 那份清单是口径真源） |
| 改**菜单块（`menu:`）怎么生成** | `story/beat_render.py` 的 `split_beat_units` / `_take_menu_run` / `render_menu_block`（见不变量 60）；拍序归位在 `story/beat_build.order_menu_bodies` |
| 改**菜单行 / 选项行的配对身份**（`"选项":`、行尾注释） | `story/patcher._line_key` + `_OPTION_ROW_RE` + `_content_of`；渲染侧 `story/beat_render.render_beat_to_rpy` 的选项那一支（**冒号必须带**，见不变量 86） |
| 改**「代码」列怎么写/怎么读**（`python:` 块、多行语句、`$` 前缀） | `story/beat_model.py` 的 `_split_code_cell` / `_relative_indents` / `compose_code` / `mark_code_blocks` + `story/beat_render.render_code_block` / `_render_marked_code_block` / `_python_block_indent`（见不变量 64/66） |
| 改**隐藏列**（`StageOrder` / `OpOrder` / `CodeBlocks`） | `BEAT_FIELDS` 尾部 + `HIDDEN_BEAT_COLUMNS` + `Beat.to_row/from_row`（见不变量 66） |
| 改**文件级保真块**（顶层 `define` / `screen` / `init python` / … 怎么带走） | `parsing/renpy_parser.py` 的 `_top_level_region` / `_region_chunk` / `TopLevelRegionNode` + `story/beat_model.py` 的 `FILE_BLOCK_SHEET` / `file_blocks_to_rows` / `file_blocks_from_rows` + `story/excel_export._write_file_blocks_sheet` + `story/excel_import._read_file_blocks` + `story/beat_build.attach_file_block_text`（见《契约速查》附录 A 边界③） |
| 改**顶层块的渲染形状**（块头 + 缩进 4） | `story/beat_model.render_file_block` / `join_file_blocks` + `story/beat_render._collect_file_blocks` |
| 改**`call … from` / `show expression` / 空台词**的处理 | `story/beat_build._call_from_suffix` / `story/beat_model._parse_show_expression` / `parsing/renpy_parser.add_stmt`（见不变量 65） |
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
| Qt 的 `clicked/triggered` 把 `bool` 喂进槽（路径/坐标/id 串位） | 预防：`tools/audit_qt_signal_slots.py`（不许裸 `connect(self.槽)`，`act()` 统一入口）+ 兜底：`ui_qt/qt_args.drop_qt_bool`（见不变量 73） |
| 改**"被吞掉的错误"怎么记** | `core/safeguard.py`（`swallow(context, exc)` → 控制台/`report()`；见不变量 58） |
| 改 **python op 怎么渲染成源码** | `story/beat_render.render_code_block`（两个生成器共用；标记只有 `core/statement.MARKER` 一处） |
| 改 **哪些变量写 `default x = 0`** | `core/utils.declared_variables*`（见不变量 59） |
| 改 **Qt 界面（页结构 / 工具栏 / 剧情树 / 分析面板）** | `ui_qt/main_window.py`（`legacy/` 那份**不要**动） |
| 改**配色 / 字号 / 间距** | `ui_qt/tokens.py`（唯一真源）→ `ui_qt/theme.py` 出 QSS |
| 加共享词表 | `core/vocabulary.py`（别就地再写一份） |
