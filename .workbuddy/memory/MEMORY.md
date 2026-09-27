# 项目长期记忆：RenPy 剧情树可视化编辑器

## 交付目标（项目所有者设定）

小团队（约 50 人：文案 / 策划 / 美术 / 音乐 / 程序）用的 Ren'Py 演出编排工具：

- 用户**不写代码**，对着 Excel 就能精确到句子级配置演出（图片、音效、角色、位置变换）
- 表格之间相互引用联动
- 程序那边敲代码要能自动反映到表格 —— **Excel ↔ .rpy 双向联通**
- 需要支持自定义 transform 变换
- 要考虑多语言复用（未来可能迁 Unity），保持工业化能力

## 架构约定

- 源码是**扁平导入**，不是包结构；`core/` 是唯一子包且不反向依赖项目模块
- **活跃入口 = `main.py` + `graph_view.py`**；`RenPy_Story_Graph_Visual_Editor.py`
  是遗留单文件版（自带 GraphView/菜单、无资产与剧本表集成），改 UI 别改错文件
- 运行方式：项目根目录执行 `python -m pytest`（846 用例 / ~25 秒）；
  CLI 入口 `python cli.py validate|build|table|compile|collab`
- **三层架构（2026-09-27 落地，务必保持）**：
  `单元格 DSL 人话` →(dsl.parse)→ `结构化字段` →(dsl.compile + 符号表)→ `Ren'Py 代码`。
  数据层越像人话，中间层（`dsl.py`）承担的复杂度就越多，表现层才干净。
  禁止把"人类语言 / 程序代码 / 资源索引"揉回同一张表
- 管线分层（务必保持单向）：`ParseResult(Statement 流)` 是唯一中间层 →
  图（GraphBuilder.build_from_parse_result）与剧本表（beats_from_statements）
  都从它派生；禁止再写第二套 AST walk
- 派生视图都放在 Excel 里做成**独立工作表**（舞台快照 / Candidates_* /
  协作信息），Beats 表只放用户要编辑的东西
- Pillow 是**可选**依赖（已装进隔离 venv）：只影响 jpg/webp 预览，
  PNG/GIF 走 tk.PhotoImage 也能显示，代码必须有降级路径
- `pytest.ini` 把 SyntaxWarning 升级为失败
- 统一 IR：`core.Statement`（含 `statement_labels` 平行数组）是 .rpy↔Excel 中枢；
  `script_table.beats_from_statements` 把语句流折叠成 Beat

## 关键不变量（改动前先想清楚）

- `core.statement._compute_content_hash` 的输出必须与
  `renpy_parser.TranslationUnitGenerator` 逐字节一致（menu_option 用
  前缀 `'menu'` 是历史兼容，不是笔误）
- `core/uid.py` 是 uid 拼接的唯一真源，禁止各处自己拼 f-string
- `GraphBuilder.build(clear_graph=True)` 会删除不在本次 AST 里的 label 节点；
  部分文件重解必须传 `clear_graph=False`
- `Statement.__hash__` 必须与 `__eq__` 同源（递归 `_freeze(payload)`），**不能**
  用 content_hash 当哈希键：show/scene 的 content_hash 恒为空，会把不同 show
  判成同哈希。`validate_hash_contract()` 自检工厂字段覆盖
- `script_table.BEAT_FIELDS` 是 Excel 列顺序唯一真源（**20 列**：LineId/Seq/
  Label/Kind/Speaker/Comment/Text/Scene/Show/At/With/Hide/BGM/BGMParams/
  SFX/SFXParams/Voice/Stage/CodeBlock/ContentHash）；`Beat.to_row/from_row`
  必须逐列对应，`len(to_row()) == len(BEAT_FIELDS)` 有测试锁死。
  **用户 2026-09-28 明确拍板保留这套"结构化下拉列"**（曾短暂出现过 3 列
  DSL 版 Stage/BGM/SFX 与 16 列版，都不要再改回去）；`Condition` / `Notes` /
  `Transform` / `Raw` / `Music*` / `Sound*` 已收敛掉，读老表时自动迁移
  （Notes 并进 Comment，绝不丢字）
- 20 列里"能下拉的格子"= `DROPDOWN_COLUMNS`（Speaker/Scene/Show/At/With/
  Hide/BGM/SFX/Voice + Kind 内联枚举）。Stage 是**高级通道**：结构化列
  表达不了的复杂指令写这里。Stage 与结构化列互为投影
  （`compose_stage` ⇄ `hydrate_from_dsl`），两条通道各出一份就会重复执行转场
- `Stage` / `BGM` / `SFX` 三列的**格内是 DSL 文本**（`[显示] 凌跃捷 开心 左 ;
  [转场] dissolve`）。方括号形式**被解析被校验**（策划用），原生形式
  （`show ling happy at left`）**原样直通**（程序用）。两句法并存是刻意的
- 唯一的"保真后门"是 `CodeBlock`：里面有东西就原封不动抄进脚本；`raw`
  （`with dissolve`/`pause`/`jump`）也归到它，**绝不允许同一行语句走两条通道
  各输出一次**（会重复执行转场）
- 编译路径**不做全局校验**：`compile.py` 找不到资源只写
  `# TODO: 缺少资源 <类型> '<名字>'` 且**不生成**那条非法语句；
  `build_project(strict=False)` 默认不因校验失败而失败；体检是可选工具
  (`cli.py validate`)。校验侧另加去重 + 每 code 上限 40 条（`_dedupe_and_cap`）
- `Beat.to_row/from_row` 里的 stage/bgm/sfx 是"原文"，`show/hide/at/music/...`
  是**派生字段**：由 `hydrate_from_dsl(beats, symbols)` 从原文解析而来。
  `import_beats_from_excel` **一律** hydrate（含 registry=None 的情况），
  否则舞台快照/校验/多引擎导出会看到"空舞台"
- `dsl.normalize_text` **只动分隔符**（`；`/`【】`/零宽字符）。绝不能归一化
  `，`/`（`/`）`——它们是台词与素材文件名里的**内容**
  （`bgm/夜晚，断电之后….mp3` 就是这么被改坏的）
- `dsl.stage_word_if_lossless` 必须**自证无损**：翻成人话后立刻用编译器翻回去
  逐字比对，不一致就返回 `''`（退回原生写法）。表情靠"语义核心词 + 模糊匹配"
  落地（开心→happy→`close_happy`），猜错一个表情就是演出事故
- 反向取词（属性→中文）按 `-len(mapped)` **稳定排序**，所以 `happy` 优先还原成
  先声明的「开心」而不是「高兴」；反推不出的属性**原样留在原位**，项目自定义
  变换用 `at f11` 留在人话句法里
- `Symbols.has(kind)` 决定放行还是核对：**该类资产表为空 → 任何引用放行**；
  有表却查不到 → TODO。新项目因此不会一编译就满屏 TODO
- 旧 26 列表（Show/Hide/At/With/Transform/Music/…）在 `Beat.from_row` 里自动
  迁移到当前 20 列（`LEGACY_STAGE_COLUMNS` / `LEGACY_AUDIO_COLUMNS`）；旧表
  `At` 属于 **scene** 不是 show，迁移时要保持该语义
- 编译结果表（`Export_RenPy`）与生成的 `.rpy` **同源**：都来自
  `render_beat_to_rpy(..., trace=[])` 的同一趟渲染，不是"另写一套再对一遍"。
  默认写到**独立工作簿** `<表名>.export.xlsx`，`--in-place-sheet` 才并入原簿，
  且绝不修改 Beats 表
- `StageOrder` 记录舞台指令源顺序；`hide X`+`show X`（重置立绘）与反序语义相反，
  渲染必须按 StageOrder，不能按固定 Scene→Show→Hide
- **资产引用判定唯一真源 = `asset_registry.resolve_references`**（多值返回；
  图片走别名/前缀，音频剥 `bgm_`/`sfx_`/`v_` 前缀与路径 stem）。
  `analyze_references` / `analyze_statement_references` /
  `find_unregistered_assets` 都走它；不要再各处自写"空格↔下划线归一 + 白名单"
- **编译器（`dsl.Symbols.audio_row`）也必须走那条解析链**。它曾经自己写了一套
  "ID + 剥前缀别名"比对，漏了 `AssetIndex.resolve` 的**路径逐级退化**，
  于是脚本写 `play music "bgm/bgm02.mp3"`（项目惯例）时，明明文件存在、
  也登记过的音乐被判成 `# TODO: 缺少资源`——sr_general 全项目 47 条音乐
  TODO 里 27 条是这么来的假警报。判定引用只许有一处实现
- `dsl.Symbols.audio_ref` 必须用 `renpy_asset_path()` 削掉**项目前缀**再进脚本：
  资源表 Path 的基准是扫描根（`sr_general/game/bgm/x.mp3`、`game/images/x.png`、
  `images/x.png` 三种都有），而 Ren'Py 素材路径一律相对 `game/`。规则只削
  `game/` 之前那段，没有 `game/` 段就**原样保留**（`bgm/spring.mp3` 本来就合法，
  猜错前缀比多留一个目录更糟）
- 候选表结构固定：第 1 行来源说明 / 第 2 行 `ID | 说明 | 参数·路径` /
  第 3 行起候选；**下拉区间永远是 A 列 `$A$3:$A$N`**，加说明列不能动 A 列
- `LogicalLine.text` **含行首缩进**；还原块体源码要 `text[indent:]` 再补相对缩进
- 脚本注入的 transform / 角色（`_from_script`）不参与"被删除"确认弹窗
- `AssetEngine._sync_transform_definitions` 把脚本 transform 合并进
  `registry.transforms`（同名已登记的不覆盖），这是"表格里能选到自定义变换"的前提
- `Beat.LineId`（`label.序号`）是**位置稳定**的协作主键；`ContentHash`
  每次都要**按当前内容重算**——沿用表里带来的旧值会让 push 把"改了内容"
  判成"未变"而静默丢改动
- `render_transform_definitions` 必须先去掉 ATL 的**公共缩进**再统一缩进 4；
  直接 `.strip()` 会吃掉首行缩进、生成语法错误的 transform
- 生成脚本时图像名**保留作者写法**（`show eileen happy` 不要改成
  `eileen_happy`）：有资源表时以"能否解析"为准（`_emit_image_name`）；
  无资源表才退回下划线形态
- 舞台快照：同 tag 的 `show` 是换表情（原地替换）不是叠人；bg/cg/ui 等
  非角色 tag 不算立绘；顺序一律按 StageOrder
- 校验分级要克制：某类资产**已有资源表**而引用不在其中 → 错误；
  项目压根没建这类表 → 警告（否则新项目 build 直接失败）
- `cli.py` 退出码约定：0 通过 / 1 有错误 / 2 用法或环境问题（CI 依赖它）
- 解析/校验的**性能红线**：`resolve_references` 必须传 `AssetIndex`
  （循环里不传就是 O(引用×资产)，真实项目一次体检 268 秒 → UI 假死）
- `RenPyLexer._strip_inline_comment` **按行**剥离；括号续行拼接必须用 `'\n'`
  （用空格拼 + 整段截断 = 从第一个 `#` 吃掉整个文件，所有 label 消失）
- 角色类型判定按"以 `Character` 结尾"（`DynamicCharacter`/`ADVCharacter`…），
  DynamicCharacter 的第一个位置参数是**名字变量**，显示名取
  `default <var> = "…"`（`ParseResult.string_defaults`）
- 图像权威清单来自脚本声明：`image x = …` 与 `layeredimage x:`（含 attribute），
  按文件名扫描只是补充；校验按"tag 存在 + 属性都声明过"判定
- 脚本定义（角色/变换/图片声明）合并进 registry 后必须**落盘**到资源表
  （`append_rows_to_table`，只追加不覆盖），否则用户永远看不到
- 操作列表要分两类：`story_ops.is_narrative_code` 区分剧情变量与引擎代码；
  UI/分析走过滤视图，**导出（renpy_exporter）必须保真不过滤**
- tkinter 里所有重活（打开项目 / 体检 / 大表写出）必须走
  `_run_async`（后台线程 + result_queue 回主线程），否则窗口"未响应"
- 校验分级原则：**该类资产表为空 → 警告；有表却查不到 → 错误**。
  宁可少报也不能刷屏（真实项目曾一次报 1073 条）

## 已修坑（别再踩回去）

- **`dsl.Symbols.audio_row` 必须走 `AssetIndex.resolve`**（2026-09-28）。它原来
  只手写"ID + 剥 bgm_/sfx_ 别名"比对，漏了路径退化 → 脚本按项目惯例写
  `play music "bgm/bgm02.mp3"` 时被判 `# TODO`，而文件其实存在且已登记。
  sr_general 音乐 TODO 47 → **20**（剩下 20 条是真坏引用）
- **`audio_ref` 必须削项目前缀**：表里 Path 基准不统一（`sr_general/game/bgm/x.mp3`
  / `game/images/x.png` / `images/x.png`），直接抄进脚本会生成
  `play music "sr_general/game/bgm/bgm02.mp3"` 这种必死引用。用
  `dsl.renpy_asset_path()`，只削 `game/` 之前那段
- **UI 上挂在 `command=` 的 `self.xxx` 必须真的存在**：`graph_view` 的
  「批量导入章节」曾指向不存在的 `self.batch_import_chapters`（函数住在
  `renpy_parser` 里），点一下 AttributeError，而且**没有任何测试覆盖 UI 接线**。
  已补 `batch_import_chapters`（多选 .rpy → 累加导入 → `ReplaceGraphCommand`
  可 Ctrl+Z）＋ `test_ui_integration.TestUiWiring` 静态检查（AST 扫
  `command=` 目标 vs 类里定义的方法），无头环境也能挡住这类悬空回调
- `analysis.analyze_reachability` 必须有 widening，否则带循环的图不终止
- 可达性不能从区间是否为空反推（无变量图会全误判），要用独立 reached 集合
- `_handle_table_changed` 必须**先 diff 再同步脚本角色定义**，否则 Excel 改名被掩盖
- 音频 ID 前缀不能重复拼接
- Excel 路径类 key 统一用 posix 分隔符（团队跨平台）
- `add_asset_to_registry` 查文件名必须用 registry 属性名 `_STANDARD_TABLES.get(attr)`
  而非 `get(asset_type)`，否则 `voice`（单数）查不到 `voices.xlsx`；追加行按文件
  真实表头对齐
- 音频引用含 `/` 或非 ASCII 必须加引号，否则 `play music bgm/夜晚.mp3` 非法
- 解析器对 label 内未建模的**叶**语句（`with`/`pause`/`window hide`/`call screen`/
  动态 jump）一律产 `RawNode` 保真；带子块的未建模语句跳过（避免只留头丢体）
- 音频分类不能只看路径关键词：`game/audio/night.ogg` 会被判成音效 →
  Music 下拉为空。现按 目录语义 > 文件名线索（音效线索优先）> 兜底 music；
  音频/语音目录探测必须带上 `game/` 前缀（`game/voice/`、`game/music/`）
- 图片 tag 必须在扫描时算出来：`images/eileen/happy.png` 的 tag = `eileen`，
  背景/CG/UI 才是固定 `bg`/`cg`/`ui`。tag 为空 → 脚本 `show eileen happy`
  与资产 ID `happy` 对不上，整库立绘误报"未使用"
- 正则兜底路径曾**完全没抽 `voice`** → 语音资产永远显示未使用；`_RE_VOICE_LINE`
  要同时收字符串路径与裸变量（`voice v_001`）
- `normalize_text` 归一化 `，`→`,` 会把素材路径改坏（`bgm/夜晚，断电之后….mp3`
  生成的引用指向不存在的文件）。**只归一化分隔符**
- `queue music/sound` 经 DSL 往返会变成 `[音效(queue)]`，`parse_audio` 必须认这个
  `(queue)` 后缀，`compile_audio` 必须输出 `queue` 而不是 `play`
- 原生 `hide lan with dissolve` 不能直接丢给 `_split_hide_items`（它只认
  `image@with`，会把 `lan with dissolve` 整体当图像名）→ 要先用
  `_parse_native_stage_tail` 拆出 spec/with
- 环境坑：工作区曾被一次 `git stash -u`（`baseline-check`）整体回退，
  未跟踪文件（`dsl.py` / `compile.py`）直接消失、已跟踪文件回到旧版。
  恢复：`git checkout stash@{0} -- .` 再 `git checkout stash@{0}^3 -- .`（补未跟踪）；
  之后**逐个 grep 复核**关键函数是否真的在文件里，别信"写入成功"的返回

## 待办优先级建议

1. **音频"路径纠错"助手**：路径退化匹配已经修好（文件的 stem 能对上资产就算
   命中），现在剩下的 20 条音乐 TODO 是**脚本里的文件名和磁盘上的真名不一样**
   —— 脚本写 `bgm/无法言说的事情…….mp3`（用 `……` 省略），磁盘上是
   `无法言说的事情，让人心神不宁.mp3`。这种只能靠**模糊匹配给建议**
   （按前缀/子串找最像的真文件），而不是让人肉比对；建议应写进 TODO 注释里
2. `配置_词表`（类别|词|映射为）目前只有内置词表 + `配置_词表` 的读取接口，
   **还没有从工作簿读词表的入口**（`Symbols(keywords=…)` 已就位，缺 Excel 侧）
3. 支持现代 Ren'Py tl 翻译格式（当前多语言链路不可用）；行级 LineId 已就位
4. `collab_store` 的资产表目前只有单向 upsert，需要"资源表 ↔ DB"双向编辑与锁
5. Excel 动态下拉在 WPS 上的兼容性实测（已做写名失败的回退）
6. 多引擎导出的 Unity/Godot 侧**真机验证**（当前只保证 JSON 契约与文档）
7. **`Stage` 列没有格级下拉**：Excel 数据验证只能约束"整格"，做不到
   "角色/表情/位置各自下拉"。当前用"词表 sheet + 编译期 TODO"补足约束力。
   注意 20 列里的 Scene/Show/At/With/Hide/BGM/SFX/Voice **已经是格级下拉**
   （见 `DROPDOWN_COLUMNS`），只有 Stage 这一格是全自由的

（已完成：26 列 → 当前 20 列结构化下拉列 + Stage/BGM/SFX DSL 高级通道 +
编译器（无全局校验、TODO 兜底）+ 编译结果表；image tag ↔ 资产 ID 映射；
统一资产引用抽取（含编译器侧）；一.1 图吃 Statement；二 舞台快照 + 动态下拉；
五 多引擎导出器；七 三级校验 + build + CLI；八 协作后端 SQLite + Excel 前端；
transform 块解析（名字/参数/ATL/注释）、角色定义解析、脚本定义合并进 registry、
候选表说明列 + 变换注释解释、资产说明面板 + 缩略图预览、批量导入章节）
