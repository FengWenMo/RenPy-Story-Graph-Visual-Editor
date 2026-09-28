# 项目记忆：RenPy 剧情树编辑器（storygraph）

## 怎么用这份记忆

**易变事实一律不写这里**——列数、用例数、性能数字、文件行数，去读代码或跑测试。
写进来的每轮都会过期，逼得每次开工都要"重新读代码确认"（09-26/27/28 三天里，
"记忆说的"和"代码里的"对不上过三次）。这里只放：契约、稳定事实、待办缺口。

## 一、当前契约（不许违反）

1. `core.statement` 是唯一 IR；图与剧本表都从它派生，禁止第二套 AST walk
2. 资产引用判定唯一真源 = `assets.asset_registry.resolve_references`
   （循环里必须传 `AssetIndex`，否则 O(引用×资产)）
3. 共享词表真源 = `core.vocabulary`；补全打分排序真源 =
   `assets.asset_completion.score_candidates`（UI/面板两套补全共用它）
4. Excel 安全写单元格唯一实现 = `excel.cells`
5. `dsl.normalize_text` 只归一化分隔符，绝不动 `，（）`（它们是文件名内容）
6. 编译路径不校验：缺资源写 `# TODO` 且不生成该语句；体检是独立命令（不写盘）
7. **落盘一律过生成门禁** `story.generation_guard.write_generation`：block 级
   问题不写目标路径、改写 `<stem>.blocked.rpy`。`LABEL_COLLISION` 只在产物
   **真会落进 `<项目根>/game/`** 时判（否则会误拦正常构建）
8. 校验分级：该类资产有表却查不到 → 错误；压根没建表 → 警告。宁少报不刷屏
9. `beat_model.BEAT_FIELDS` 是列顺序唯一真源（含隐藏 StageOrder/OpOrder）
10. `Stage`/`BGM`/`SFX` 三列格内是 DSL：方括号被解析校验，原生写法直通
11. 唯一保真后门是 `CodeBlock`；同一语句绝不许走两条通道各输出一次
12. `import_beats_from_excel` 一律 hydrate，否则快照/校验看到空舞台
13. `LineId` 是协作主键；`ContentHash` 每次按当前内容重算
14. **依赖方向单向**：core ← parsing/excel ← assets/story ← pipeline ← ui。
    story 层不许顶层 import pipeline（需要时走 script_table 的 `__getattr__` 转发）
15. **整行注释必须保真**：lexer 存的是**含缩进**的原文
    （`_render_block_source` 按 `text[indent:]` 还原块体；存 stripped 会
    双重去缩进，把注释啃成非法代码——真实项目踩过 4 条 transform）
16. **transform 的 `ATL`/`Params`/`Doc` 以脚本为权威**：脚本改了定义，资源表
    要跟着更新（`append_rows_to_table(update_keys=...)`）；人写的
    `Category`/`Notes` 一律不动
17. 改中文 `.py` 只能用编辑工具或 Python 脚本（PowerShell 会 GBK 往返毁文件）

## 二、仓库与运行（稳定事实）

- 包：`storygraph/{core, excel, parsing, assets, story, pipeline, graph, ui, collab}`
- `story/` 已拆分：beat_model（**加列改这里**）/ beat_build / beat_render
  （**改生成代码**）/ excel_export（**改 Excel 长相**）/ excel_import /
  generation_guard（**落盘**）/ paths；`script_table.py` 是**兼容门面**
- 入口：根 `main.py` / `cli.py` 是垫片 → `storygraph.main` / `storygraph.cli`
- 命令：`pip install -r requirements.txt`；`python -m pytest`；
  `python demo/make_demo.py`；`python main.py`
- CLI 退出码 0/1/2（CI 依赖）；`validate` 不写盘
- 依赖只有 openpyxl（必需）+ Pillow（可选，有降级）；`legacy/` 不参与构建
- 测试：无头环境用 conftest 的 tkinter 桩；**扫源码用
  `tests/_source_paths.module_source()`**，别硬编码相对路径
- CI：`.github/workflows/ci.yml`（3.10 / 3.12 / 3.13 + demo 体检）
- 真实项目验收对象：`D:\千连万理\RenPyTest\sr_general`（换机即 skip）

## 三、已知缺口

1. **双轨模型**：`graph.models.Graph`（`Node.operations` 存代码字符串）与
   `core.Statement` 并存 —— 这是"图视图要展示可编辑代码"的刻意设计，不是重复；
   真要统一需单独立项
2. `asset_extensions.DependencyAnalyzer` 是引用分析的旧入口（判定已收敛）
3. **多语言链路不可用**：`parse_translation_files` 不支持现代 `tl` 格式，
   而 IR 哈希契约（menu_option 前缀 `'menu'`）正是为它做的历史兼容
4. "程序改代码 → 表格自动反映" = 1.5s 全目录轮询 + 全量重解析；
   `asset_engine._apply_rename_patch` 会**正则改写用户的 .rpy**（改名靠编辑距离推断）
5. Excel 前端上限：`Stage` 列无格级下拉；候选表整表重建；导出无增量
6. **行内注释**（`show x # 注释`）仍会丢：整行注释已保真，行内注释要给 Beat
   加一列才能存（属新增功能，别顺手在做别的改动时"顺带"塞进去）

## 四、变更历史（倒序；细节见同目录 `YYYY-MM-DD.md`）

- **2026-09-28 下半场**：拆 `script_table`（3.8k 行 → 7 模块 + 兼容门面，环清零）；
  `discover_scripts` 下移 parsing 断开门禁反向依赖；两套补全共用打分函数、
  TYPE_MAP 收敛到 vocabulary；补 CI
- **2026-09-28 晚**：真实项目实测修两个真 bug——①块体整行注释被双重去缩进、
  啃成非法 ATL（4 条 transform / 15 行，**真实项目表里的坏值已修好并备份**）；
  ②表里旧值永不更新 → "改脚本表格不反映"；补齐切分脚本丢掉的 2 个顶层语句块
  （`_HEADER_ALIASES.update` 静默丢数据、`_PILImage` NameError）；
  引入 pyflakes 清理冗余导入（565→143，其余是门面刻意 re-export）
- **2026-09-28 上半场**：扁平 33 模块 → `storygraph` 分层包（481 处 import 自动
  改写）；P0（门禁真正生效 / 覆盖 build+compile / 体检不写盘）；P1（vocabulary /
  excel.cells）；归档 legacy；清掉 9612 个误入库缓存
- **2026-09-27** 三层架构（`dsl.py`）；Beats 列收敛；舞台快照 + 动态下拉
