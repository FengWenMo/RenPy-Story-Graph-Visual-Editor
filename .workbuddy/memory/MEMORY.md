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
19. **裸词位置要归一**：`show` / `Stage` 里 `角色 表情 左` 的"左"是位置不是表情
    （不认它会**静默丢掉位置**）；结构化列统一写 `@位置`。
    另：立绘的**中文名要先用 `symbols.actor_image_tag` 归一成英文 tag** 再查资源表；
    **查图必须按 Tag 收窄**（真实项目 `lan`/`ling` 都有 `oh` 表情，用裸属性名会串图）
20. **辅助功能不许改用户已有数据**：翻页轮转说话人只能"补空缺"，绝不覆盖已写的
    值（2026-09-30：第 45 拍本来 `lyj`，被轮转静默改成 `dhy`，用户报为最严重问题）
18. **界面代码只从 `ui.tokens` 取色值/字号/间距**，禁止写死（改主题/查对比度
    都靠它）；立绘是**网格卡片**：加列改 `beat_model`，卡内交互改 `sprite_card`，
    缩略图一律走 `ui.thumb_loader`（禁止在 UI 线程同步解码）
20. **`Stage` ↔ 结构化列：互为投影，但单向 + 幂等 + 只输出一次**
    （2026-09-30 用户批准重构，契约测试 `tests/test_stage_projection.py`）：
    · 结构化字段是唯一真源；Stage 只承载"结构化列表达不了"的东西
    · `hydrate` **必须幂等**（`_join_hide_items` 曾漏去重 → `hide cg03`
      被吸两次变 `cg03; cg03`；show 那条路本来就有）
    · Stage 里的**原生真迹**（作者手写）由 Stage 尾段**逐字原样**输出，
      结构化列里那份只是影子（`beat._stage_native_show/hide/scene`），
      `_stage_lines` 跳过它；中文化写法按**语义**判"已被承载"
      （`_stage_op_covered`，复用同一套片段编码，别靠字符串相等）
    · 编辑器重投影 `beat.stage` 时用 `stage_only_ops()` 留住 `[停顿] 1.0`
      这类只能写在 Stage 里的指令
21. **`beat.at` 是"整拍兜底位置"**：渲染时套给**所有没写 `@` 的 show**
    （`beat_render._stage_lines`）。所以只有"所有立绘位置一致"才能同步它，
    否则会**给别的立绘凭空套上位置**（立绘错位）。见 `uniform_sprite_position`
22. **只读的 tkinter `Text` 会静默吞掉程序化写入**（不报错、内容仍空）——
    界面里"看着是空的"会被写回当成"清空"。要灰显别用 `state='disabled'`
    去建字段；`_wset` 会临时放开 state 写完再还原
23. **翻页是纯导航**（用户 2026-09-30 拍板："回车是下一张卡片的意思"）：
    `next_beat` / `prev_beat` **只提交当前拍**，绝不许碰别的拍。
    任何"便利功能"都不许挂在导航路径上自动改数据——要改就做成显式动作
    （如「参与角色」双击填进当前拍）。`↑`/`↓`/`Enter`/`Alt+↑↓` 都是翻页，
    但 `↓` 在**多行台词**里光标未到末行时让位给光标
24. **`compose_stage` 必须与 `_stage_lines` 同源**：背景变换是 `scene_at`
    （不是立绘的 `beat.at`）、立绘位置有 `beat.at` 兜底、没背景时转场挂
    最后一条立绘——两边不一致就是"表里看到的 ≠ 生成的"
25. **增量补丁（`story.patcher`）会改用户的 .rpy，契约是"要么全对，要么完全不动"**：
    · **一对一替换、行数永不变化**（按"行身份"配对：台词配台词且说话人一致、
      立绘配同 tag、音频配同通道；其余只和逐字相同的行配对）
    · **缩进只来自原文**（替换 = 原文缩进 + 生成内容；结构不是补丁的事）
    · 替换行必须来自**真实产物**（同一套 registry/symbols），不许单拍重渲染
    · 逐拍**对位守卫** + 整体校验（可解析 / 台词都在 / 生成器要的内容行都在 /
      label·return 不少）→ 不过就 `ok=False`，调用方退回全量；**`ok=False` 时 text 不可用**
    · `recover_source_ranges()`：表里的拍没有行号（元数据不入表）→ 解析源 .rpy
      按内容对齐找回；加行/删行**不支持**（拒绝，退回全量）
26. **注册表里的图片 Path 有**两套基准**：扫描出来的行相对项目根
    （`game/images/…`），脚本 `image` 声明合并进来的行相对 `game/`
    （`images/…`）。只认一套就会出现"图明明在、却一张都解析不出来"。见
    `stage_preview._path_from_rel`。纯色的颜色在行的 **`Solid`** 字段
27. **演出预览画的是"游戏画面"**：真实贴图走 `ui.thumb_loader`（可**按请求**
    指定尺寸）、缺图/纯色逐项降级、色值全走 `ui.tokens`（`stage.*`）。
    贴图回调**只替换那一块**，绝不许"重画整帧"——那会把刚存的图片引用清掉
28. **报"缺资源"要给"是不是想写这个"**：真实项目里大量缺资源不是"没资产"，
    而是**脚本里的文件名被手写截断了**（`bgm/夜晚，断电之后…….mp3` ↔
    盘上 `bgm/夜晚，断电之后，不存在的教室.mp3`）。`_suggest_missing_ref`
    给**唯一前缀匹配**的建议（并列/对不上就不猜），写进 `# TODO` 注释 →
    导出对话框直接能看到，人一眼就能改对
29. **编辑器的落盘时机要在界面上说清**：卡片改动先提交到**内存里的这一拍**
    （`save_current_beat` / Ctrl+S），**导出 .rpy 才落盘**。以前只有翻页/导出
    顺带提交，改完停在原地没有落点（用户以为"保存不了"）
30. **IR 是"语句流"不是"语句树"，无法表达嵌套**（2026-10-02 实测确认）：
    15 个 kind 没有一个能带子语句。已兑现的代价——① **`menu` 块往返后整体
    消失**（`beat_render.py:314` 把 menu_option 渲染成裸台词，第二分支成死代码）
    ② `elif`/`else` 被展开成 `not((前一条件))` 合取。改这个要动 IR 形状，
    别在渲染层打补丁
31. **测试必须测"往返不变式"，不能只测半程**（2026-10-02 教训）：
    1731 个测试全绿，却漏掉 menu 100% 丢失与 condition 100% 丢失，因为
    `test_script_table.py` 里 condition/menu 的用例都断言到
    `beats_from_parse_result()` 就停了，**没导出 Excel、没回导、没重编译**。
    任何"保存/同步"类改动，都要断言 `原文 → 载体 → 原文'` 的多重集相等
    （脚本见 `tools/audit_roundtrip.py`）

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
- 真实项目验收对象：`D:\千连万理\RenPyTest\sr_general`（换机即 skip）；
  章节表在 `game/chapter/*.xlsx`
- **改 UI 必须用真实 tkinter + 真实数据冒烟**：`D:\Python\python.exe`（系统
  Python 有 tkinter）+ venv 的 site-packages（拿 openpyxl）→ 真实例化面板，
  查 `winfo_reqwidth()` / `grid_info()`。**假数据会掩盖 bug**（2026-09-30：
  假 engine 返回空串，正好绕过 `Path.strip()`，真实数据一跑就崩）

## 三、已知缺口

1. ~~**双轨模型**~~ **已收敛**（2026-10-02 git "修复双IR问题"）：
   `Node.statements: list[Statement]` 是结构化真源，`Node.operations` 降为派生
   兼容视图；`python_operation` / `operation_source` 是唯一转换点，
   行首标记常量收敛到 `core.statement.MARKER`
2. `asset_extensions.DependencyAnalyzer` 是引用分析的旧入口（判定已收敛）
3. **多语言链路不可用**：`parse_translation_files` 不支持现代 `tl` 格式，
   而 IR 哈希契约（menu_option 前缀 `'menu'`）正是为它做的历史兼容
4. "程序改代码 → 表格自动反映" = 1.5s 全目录轮询 + 全量重解析；
   `asset_engine._apply_rename_patch` 会**正则改写用户的 .rpy**（改名靠编辑距离推断）
5. Excel 前端上限：`Stage` 列无格级下拉；候选表整表重建；导出无增量
6. **行内注释**（`show x # 注释`）仍会丢：整行注释已保真，行内注释要给 Beat
   加一列才能存（属新增功能，别顺手在做别的改动时"顺带"塞进去）
7. **Stage ↔ 结构化列的投影契约已重构收敛**（2026-09-30）：原生真迹走 Stage、
   影子不进渲染、中文化写法按语义去重。仍待观察的是 `_stage_op_covered`
   对"编译后写法与结构化渲染差异较大"的指令的判定（目前认不出就不跳过，
   宁多勿吞）
8. 真实数据体检手法（比读代码有效）：**全章逐句 `apply_current_beat`，
   再比对 `render_beats_to_rpy` 的产物**——产物有差就是"看一眼就改数据"。
   最强的一条是 **xlsx 渲染 vs 源 .rpy 的舞台指令逐条比对**
   （chapter1_2 71/71、chapter1_3 89/89，0 丢 0 多）。
   本机 3~4 个删文件类用例会被工作区 `safe-delete` 守卫拦下（`SystemExit`，
   非断言失败），跑全量时忽略；`--basetemp` 要换个干净目录

## 四、变更历史（倒序；细节见同目录 `YYYY-MM-DD.md`）

- **2026-09-30**：拿"逐句校对产物必须逐字节不变"当体检项，一次堵掉 5 条
  **静默改数据**的通道（轮转写进旁白/纯舞台拍、`beat.at` 位置泄漏、
  Stage 投影回灌、多属性立绘被压成一个属性）；顺带修好主区四项
  （Scene/BGM/Voice/Comment）**收起高级区时改了不生效**、高级区
  **永远空白且展开即清空**、chip 换表情后**卡片名字/图与数据脱钩**；
  右侧卡片面板加 `ui/scroll_frame.py`（滚动条 + 滚轮 + 浮层贴视口）。
  下半场按用户要求：**删除**回车自动填说话人（改「参与角色」双击显式填当前拍）、
  加 `↑`/`↓` 翻页（与光标移动共存）、并**重构 Stage 投影语义**
  （hydrate 幂等 + 原生真迹原样输出 + 影子不进渲染 + 认不出不跳过）
- **2026-09-29**：卡片编辑器界面改版——立绘从"一行一图"改为**网格卡片**
  （`ui/sprite_card.py`）、表情/位置改在卡内 chip（1 次点击）、按 tag 聚合
  （同角色换表情 = 一张卡，数据不丢）、缩略图**异步加载**（`ui/thumb_loader.py`）、
  翻页上移顶部 + 首末禁用、删除可撤销（Toast）；新增 `ui/tokens.py` 统一 token
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

## 五、协作方式（用户本人 2026-10-02 确认，影响后续所有协作）

- **单人开发**：工具从头到尾只有用户一人；代码**主要由 AI 写**，用户负责
  **设计、测试、验收**（README 里 57 条不变量，背后都是他踩出来的真实 bug）
- **目标不是急着交付或面试，而是积累开发经验 + 学会改进 AI 写的代码**
- 因此协作模式相应调整：
  · 多解释**"为什么这么设计 / 权衡了什么 / 否决了哪个备选"**，少直接甩成品
  · 他要的是**决策过程**，不是结论
  · 鼓励"他先动手写、AI 当评审"——他写代码时**不要抢着给答案**
  · 验收一律靠真实项目（`D:\千连万理\RenPyTest\sr_general`，88 个 .rpy）
    + 全量 1673 测试，让数据打脸，别靠口头判断
- 他不缺工程判断力，缺的是**手写代码的肌肉记忆**；练习要落在
  "关掉 AI 自己写一版"这一步上，而不是读代码
- 已知适合当练习课题的三处真实坏味道（由易到难）：
  ① 196 处 `noqa: BLE001`（该不该吞这个异常）② 双 IR / 两套 .rpy 生成器
  收敛（架构）③ `app/editor.EditorSession` 70 方法拆职责
