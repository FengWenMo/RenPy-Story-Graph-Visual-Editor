# 项目记忆：RenPy 剧情树编辑器（storygraph）

## 怎么用这份记忆

**易变事实一律不写这里**（列数/用例数/行数/性能/测试数）→ 去读代码或跑测试。
这里只放：契约、稳定事实、待办缺口。09-26/27/28 三天里"记忆说的"和"代码里的"
对不上过三次，所以别信这里的数字。

## 一、架构契约（不许违反）

### A. 真源与 IR

- **A1 `.rpy` 是唯一真源**（2026-10-03 定论）。模型是
  **单源(.rpy) → 多视图(Graph/Excel) → 受控 patch**，不是"多源→生成"。
  `default`/`define`/`init python`/`transform`/`screen` 属"第三类"：谁都不管、
  不进 Beats → **全量重建必然抹掉它们**。不归自己的行**逐字节不动**。
- **A2 `core.statement` 是唯一 IR**；图与剧本表都从它派生，禁止第二套 AST walk。
  IR 是**语句流不是语句树**（15 kind 无一能带子语句）：`elif/else` 展开成
  `not((前一条件))`；`menu` 靠**渲染前把扁平拍重新归组**复原（`beat_render.py:675`
  + 解析器 `_menu_member` 标记），**不是**改 IR 形状。
- **A3 唯一保真后门是 `CodeBlock`**；同一语句绝不许走两条通道各输出一次。
- **A4 整行注释必须保真**：lexer 存**含缩进**的原文（`_render_block_source` 按
  `text[indent:]` 还原；存 stripped 会双重去缩进，把注释啃成非法 ATL）。

### B. 写入与落盘（最高危区）

- **B1 落盘一律过 `story.generation_guard.write_generation`**：block 级问题不写目标
  路径、改写 `<stem>.blocked.rpy`；`LABEL_COLLISION` 只在产物真会落进 `<项目根>/game/`
  时判。⚠ **它不检查"目标已存在"**（实测）：覆盖已有 .rpy 会把
  `default`/`init python`/`transform`/原 label/台词**全抹成 `label start: pass`**。
- **B2 全量生成只许用于新建文件；改已有 .rpy 一律走 `story.patcher`**。契约"要么
  全对、要么完全不动"：一对一替换、**行数永不变化**（按行身份配对：台词↔台词且说话人
  一致、立绘↔同 tag、音频↔同通道，其余只与逐字相同行配对）；缩进只来自原文；替换行
  必须来自**真实产物**；逐拍对位守卫 + 整体校验 → 不过就 `ok=False`，**此时 text
  不可用**。`recover_source_ranges()` 按内容对齐找回行号；**加行/删行不支持**。
- **B3 ⚠ 已知 P0（未修，用户自己来）**：`app/editor.py:1426 _patch_note` 在 patcher
  失败或抛异常时**静默退回全量文本**，1421 行照写给 `write_generation` → 毁文件。
  CLI 同样中招（`cli.py:115` / `cli.py:290`）。正解：**失败即拒绝写入 + 报给人**，
  而非退回全量；B1 的存在性检查只是第二道防线。`patcher.py:109` docstring 那句
  "退化成全量更稳"是错误假设，要改。报告 `docs/P0_静默全量覆盖_2026-10-03.md`；
  红测试 `tests/test_p0_no_silent_full_regen.py`，基线 **6 red / 6 green / 4 skip**
  （绿的 6 条是"别误伤"护栏，修完必须保持绿）。

### C. Stage / 演出投影

- **C1 `Stage` ↔ 结构化列互为投影，但单向 + 幂等 + 只输出一次**
  （`tests/test_stage_projection.py`）：结构化字段是唯一真源，Stage 只承载它表达不了
  的东西；`hydrate` **必须幂等**；Stage 里的**原生真迹**由尾段**逐字原样**输出、结构化
  列那份只是影子（`_stage_native_*`）不进渲染；中文化写法按**语义**判"已被承载"
  （`_stage_op_covered`，复用片段编码，别靠字符串相等）；重投影用 `stage_only_ops()`
  留住 `[停顿] 1.0` 这类只能写在 Stage 里的指令。
- **C2 `beat.at` 是"整拍兜底位置"**：渲染时套给**所有没写 `@` 的 show** → 只有"所有
  立绘位置一致"才能同步它，否则给别的立绘凭空套位置（`uniform_sprite_position`）。
- **C3 `compose_stage` 必须与 `_stage_lines` 同源**（背景用 `scene_at`、立绘位置有
  `beat.at` 兜底、没背景时转场挂最后一条立绘）——不一致就是"看到的 ≠ 生成的"。
- **C4 裸词位置要归一**：`角色 表情 左` 的"左"是位置不是表情（不认会**静默丢位置**），
  结构化列统一写 `@位置`。立绘**中文名先经 `symbols.actor_image_tag` 归一成英文 tag**
  再查表；**查图必须按 Tag 收窄**（真实项目 `lan`/`ling` 都有 `oh`，裸属性名会串图）。

### D. 资产

- **D1 引用判定唯一真源 = `assets.asset_registry.resolve_references`**（循环里必须传
  `AssetIndex`，否则 O(引用×资产)）。
- **D2 词表真源 = `core.vocabulary`；补全打分真源 =
  `assets.asset_completion.score_candidates`**（UI/面板两套补全共用）。
- **D3 transform 的 `ATL`/`Params`/`Doc` 以脚本为权威**：脚本改了要
  `append_rows_to_table(update_keys=...)` 回写；人写的 `Category`/`Notes` 不动。
- **D4 注册表图片 Path 有**两套基准**：扫描行相对项目根（`game/images/…`），脚本
  `image` 声明合并行相对 `game/`（`images/…`）——只认一套就"图在却解析不出来"
  （`stage_preview._path_from_rel`）。纯色颜色在 **`Solid`** 字段。
- **D5 报"缺资源"要给建议**：真实项目大量缺资源是**文件名被手写截断**，
  `_suggest_missing_ref` 给唯一前缀匹配建议写进 `# TODO`。
- **D6 校验分级**：有表却查不到 → 错误；压根没建表 → 警告。宁少报不刷屏。
- **D7 编译路径不校验**：缺资源写 `# TODO` 且不生成该语句；体检是独立命令（不写盘）。

### E. Excel / 表格

- **E1 安全写单元格唯一实现 = `excel.cells`**
- **E2 `beat_model.BEAT_FIELDS` 是列顺序唯一真源**（含隐藏 StageOrder/OpOrder）
- **E3 `Stage`/`BGM`/`SFX` 格内是 DSL**：方括号被解析校验，原生写法直通
- **E4 `import_beats_from_excel` 一律 hydrate**，否则快照/校验看到空舞台
- **E5 `LineId` 是协作主键**；`ContentHash` 每次按当前内容重算
- **E6 `dsl.normalize_text` 只归一化分隔符**，绝不动 `，（）`（它们是文件名内容）

### F. UI

- **F1 界面只从 `ui.tokens` 取色值/字号/间距**，禁止写死。立绘是**网格卡片**（加列改
  `beat_model`，卡内交互改 `sprite_card`，缩略图一律走 `ui.thumb_loader`——禁止 UI
  线程同步解码）。
- **F2 只读 tkinter `Text` 会静默吞掉程序化写入**（不报错、内容仍空）→ "看着空的"会被
  写回当成"清空"。要灰显别用 `state='disabled'` 建字段；`_wset` 会临时放开再还原。
- **F3 翻页是纯导航**：`next_beat`/`prev_beat` **只提交当前拍**，绝不许碰别的拍；任何
  便利功能都不许挂在导航上自动改数据（要改就做成显式动作）。`↑`/`↓`/`Enter`/`Alt+↑↓`
  都是翻页，但 `↓` 在**多行台词**里光标未到末行时让位给光标。
- **F4 辅助功能不许改用户已有数据**：轮转说话人只能"补空缺"（曾把第 45 拍 `lyj`
  静默改成 `dhy`，用户报为最严重问题）。
- **F5 演出预览贴图回调只替换那一块**，绝不许"重画整帧"（会清掉刚存的图片引用）。
- **F6 落盘时机要在界面说清**：卡片改动先提交到**内存这一拍**（`save_current_beat`
  / Ctrl+S），**导出 .rpy 才落盘**。

### G. 架构 / 依赖

- **G1 依赖方向单向**：core ← parsing/excel ← assets/story ← pipeline ← ui。story 层
  不许顶层 import pipeline（走 `script_table` 的 `__getattr__` 转发）。守卫
  `tests/test_package_layering.py`（方向/无环/延迟 import 白名单）。
- **G2 改中文 `.py` 只能用编辑工具或 Python 脚本**（PowerShell 会 GBK 往返毁文件）。

### H. 测试

- **H1 必须测"往返不变式"，不能只测半程**（1731 测试全绿却漏掉 menu 100% 丢失与
  condition 100% 丢失，因为用例都断言到 `beats_from_parse_result()` 就停了）。任何
  "保存/同步"改动都要断言 `原文 → 载体 → 原文'` 的**多重集相等**
  （`tools/audit_roundtrip*.py`）。已补 `tests/test_roundtrip_invariants.py`，并用
  **变异测试**验证（把 `to_row` 的 condition 写空 → 9 例立刻变红）。
- **H2 ⚠ 含中文的复现别用 `python -c`**（Git Bash 下编码毁样本、解析出 0 条，会误判
  "解析器退化"）——一律写成 .py 脚本再跑。

## 二、仓库与运行（稳定事实）

- 包：`storygraph/{core, excel, parsing, assets, story, pipeline, graph, ui, collab}`
- `story/` 已拆分：beat_model（**加列改这里**）/ beat_build / beat_render（**改生成
  代码**）/ excel_export（**改 Excel 长相**）/ excel_import / generation_guard
  （**落盘**）/ paths；`script_table.py` 是**兼容门面**
- 入口：根 `main.py` / `cli.py` 垫片 → `storygraph.main` / `storygraph.cli`
- 命令：`pip install -r requirements.txt`；`python -m pytest`；
  `python demo/make_demo.py`；`python main.py`
- CLI 退出码 0/1/2（CI 依赖）；`validate` 不写盘
- 依赖只有 openpyxl（必需）+ Pillow（可选，有降级）；`legacy/` 不参与构建
- 测试：无头环境用 conftest 的 tkinter 桩；**扫源码用
  `tests/_source_paths.module_source()`**，别硬编码相对路径
- CI：`.github/workflows/ci.yml`（3.10 / 3.12 / 3.13 + demo 体检）
- 真实项目验收对象：`D:\千连万理\RenPyTest\sr_general`（换机即 skip），
  章节表在 `game/chapter/*.xlsx`
- **改 UI 必须用真实 tkinter + 真实数据冒烟**：`D:\Python\python.exe`（有 tkinter）+
  venv 的 site-packages（拿 openpyxl）→ 真实例化面板。**假数据会掩盖 bug**

## 三、已知缺口

1. **P0 未修**：静默全量覆盖（见 B3）。当前唯一活跃的高危缺陷。
2. **不支持任意层嵌套（有意为之，有数据）**：演出流深度 1=122(81.3%)、2=25(16.7%)、
   3=3(2.0%)、4=0；**真实项目 0 例 `if>menu>if`**。→ 扁平设计本身合理，真正的问题是
   **它没守住自己的边界**：现在会静默降级，应改成 validate **显式报错**（fail fast
   优于 silent degradation）。分工：**树归 Graph 画布，句子级内容归 Excel 表**。
3. **解析层两个真 bug**（丢失发生在 `beats_from_parse_result`，连 Excel 都没到）：
   ① `if` 内嵌 `menu` 整体降级（条件/caption/jump 全丢，选项变裸旁白）；② 分支内
   "show+say 混合" → 后续 elif 分支整体消失。另：menu 两选项间插一拍 → 该拍被吸进
   menu 组变成第三个选项。
4. **能力边界（可写给用户看）**：内容保真 ≈100%（3000 句/100 选项/50 label 零丢失、
   五次幂等、真实项目变异 30 次不崩）；结构**可靠到 2 层**，3 层降级但会留
   `# TODO: 这一拍不确定属于哪个选项`——**这个降级设计要保留**。残留：`with_` 跨相邻
   show 泄漏、同拍重复舞台指令被去重。
5. **两套 .rpy 生成器并存**（`renpy_exporter.export_to_renpy(graph)` +
   `beat_render.render_beats_to_rpy(beats)`）——ownership 没显式化。解法方向：
   **merge(Graph 骨架, Excel 内容, CodeBlock)** 而非 `render(beats)`；连接键已有
   `LineId`。分步：① 两生成器各司其职（不改数据结构）② line_id 接入 patcher 作配对键
   ③ `Condition` 列降级为只读投影（改逻辑去 Graph）。
6. **多语言链路不可用**：`parse_translation_files` 不支持现代 `tl` 格式。
7. "程序改代码 → 表格自动反映" = 1.5s 全目录轮询 + 全量重解析；
   `asset_engine._apply_rename_patch` 会**正则改写用户的 .rpy**。
8. Excel 前端上限：`Stage` 列无格级下拉；候选表整表重建；导出无增量。
9. **行内注释**（`show x # 注释`）仍会丢——要给 Beat 加一列才能存，属新增功能，
   别顺手塞进别的改动。
10. 坏味道（用户练习课题，由易到难）：201 处 `noqa: BLE001`；两套生成器/双 IR 收敛；
    `EditorSession` 70 方法拆分（**拆前必须先把快照测试换成行为断言**，否则拆一步快照
    全变、人眼审不动）。复杂度头部：`layout_graph` cc=116、`patch_export` cc=84、
    `beats_from_statements` cc=81。
11. 真实数据体检手法（比读代码有效）：**全章逐句 `apply_current_beat`，再比对
    `render_beats_to_rpy` 产物**；最强一条是 xlsx 渲染 vs 源 .rpy 舞台指令逐条比对。
    本机 3~4 个删文件用例会被 `safe-delete` 守卫拦下（`SystemExit`，非断言失败），
    跑全量时忽略；`--basetemp` 要换个干净目录。

## 四、变更历史（倒序；细节见同目录 `YYYY-MM-DD.md`）

- **10-03**：三轮严苛测评。修完 menu 丢失 / condition 不落表 / 尾部空格；补往返不变式
  测试并做变异测试；抓出 P0 静默全量覆盖（未修，已交报告 + 红测试给本人修）。
  方法论：含中文复现别用 `python -c`。
- **09-30**：以"逐句校对产物必须逐字节不变"为体检项，堵掉 5 条**静默改数据**通道
  （轮转写进旁白/纯舞台拍、`beat.at` 位置泄漏、Stage 投影回灌、多属性立绘被压成一个
  属性）；修主区四项收起高级区时失效、高级区永远空白、chip 换表情后名字/图脱钩；
  加 `ui/scroll_frame.py`。下半场**删除**回车自动填说话人（改「参与角色」双击显式
  填）、加 `↑`/`↓` 翻页、**重构 Stage 投影语义**。
- **09-29**：卡片编辑器改版——立绘改**网格卡片**（`ui/sprite_card.py`）、表情/位置改
  卡内 chip、按 tag 聚合、缩略图异步加载（`ui/thumb_loader.py`）、删除可撤销；
  新增 `ui/tokens.py`。
- **09-28**：拆 `script_table`（3.8k 行 → 7 模块 + 门面，环清零）；实测修两个真 bug
  （块体整行注释被双重去缩进啃成非法 ATL；表里旧值永不更新）；补 CI；清冗余导入。
  上半场：扁平 33 模块 → `storygraph` 分层包（481 处 import 自动改写）。
- **09-27**：三层架构（`dsl.py`）；Beats 列收敛；舞台快照 + 动态下拉。

## 五、协作方式（用户本人 2026-10-02 确认，影响后续所有协作）

- **单人开发**：工具从头到尾只有用户一人；代码**主要由 AI 写**，用户负责**设计、
  测试、验收**（README 57 条不变量，背后都是他踩出来的真实 bug）
- **目标不是急着交付，而是积累开发经验 + 学会改进 AI 写的代码**
- 因此：多解释**"为什么这么设计 / 权衡了什么 / 否决了哪个备选"**，少直接甩成品；
  他要的是**决策过程**，不是结论；他写代码时**不要抢着给答案**
- 验收一律靠真实项目（`D:\千连万理\RenPyTest\sr_general`，88 个 .rpy）+ 全量测试，
  **让数据打脸**，别靠口头判断
- 练习要落在"关掉 AI 自己写一版"，而不是读代码
