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
pip install -r requirements.txt     # 运行时只依赖 openpyxl
python -m pytest                    # 1031 个用例，约 50 秒

python demo/make_demo.py            # 一键重建示例工程（含产物与协作库）
python main.py                      # 启动图形界面
```

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
├── story/      剧本表与 DSL（原先一个 3.8k 行模块，2026-09-28 按职责拆开）
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
│   └── story_ops.py      剧情变量 vs 引擎代码的区分视图
├── pipeline/   构建与校验
│   ├── build.py          collect_report（只体检）/ build_project（出产物）
│   ├── validate.py       三级校验（L1 单条 / L2 表内 / L3 全库）
│   ├── compile.py        演出表 → .rpy + 编译结果表
│   ├── engine_exporters.py 中立 IR → Unity / Godot / CSV
│   └── renpy_exporter.py 图 → .rpy（保真，不过滤）
├── graph/      图视图数据层与算法
│   ├── models.py / layout.py / commands.py / analysis.py
├── ui/         tkinter 界面
│   ├── graph_view.py     主窗口（剧情树画布 + 菜单）
│   ├── beat_editor.py    应用内卡片编辑器（含舞台快照预览）
│   └── asset_panel.py
├── collab/     团队协作后端
│   └── collab_store.py   SQLite：行级锁 / 冲突检测 / 状态机 / 审计
├── cli.py      命令行入口
└── main.py     桌面入口
legacy/         归档的单文件老版本（**不参与构建**，见其 README）
demo/           示例 Ren'Py 工程 + 一键重建脚本
tests/          1031 个用例
```

**依赖方向单向**：`core` ← 其它所有；`ui` 依赖全部，谁都不依赖 `ui`。
禁止反向 import，也禁止再写第二套同类实现（每个"判定"只许有一处）。

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
   ← `ui`。`story` 层**不许**顶层 import `pipeline`（历史上一度靠函数内 import
   压着环）；需要高层编排时走 `script_table` 的模块级 `__getattr__` 按需转发。

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
| 改界面 | `ui/graph_view.py`（`legacy/` 里那份**不要**动） |
| 加共享词表 | `core/vocabulary.py`（别就地再写一份） |
