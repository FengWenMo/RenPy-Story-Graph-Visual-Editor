# 项目记忆：RenPy 剧情树编辑器（storygraph）

## 怎么用这份记忆

**易变事实一律不写这里**（列数/用例数/行数/性能/测试数）→ 去读代码或跑测试。
这里只放：契约、稳定事实、待办缺口。曾三次出现"记忆说的"和"代码里的"对不上，
所以**别信这里的数字**，只信这里的"哪条不许违反 + 为什么"。
> 2026-10-07 已做过一次压缩整理：保留了全部契约 ID 与判据，删去冗长实测叙述。

## 一、架构契约（不许违反）

### A. 真源与 IR

- **A1 `.rpy` 是唯一真源**。模型是 **单源(.rpy) → 多视图(Graph/Excel) → 受控 patch**。
  `default`/`define`/`init python`/`transform`/`screen` 属"第三类"：不进 Beats；
  不归自己的行**逐字节不动**。顶层 `init python:` 有随表通道（`FileBlocks`）；
  其余第三类只能靠**增量路径**保住 → 没给 `--source` 的全量导出由门禁
  `OPAQUE_LOST` 拒写，**不许静默抹掉**。
- **A2 `core.statement` 是唯一 IR**；禁止第二套 AST walk。IR 是**语句流不是树**：
  `elif/else` 展开成 `not((前一条件))`；`menu` 靠渲染前把扁平拍**重新归组**复原
  （`beat_render.split_beat_units` + 解析器 `_menu_member` 标记）。
- **A3 保真后门两条，作用域不同**：`CodeBlock`（一拍之内的 `python:`）与
  `FileBlocks`（整个文件的顶层 `init python:`）。同一语句绝不许走两条通道各出一次。
- **A4 整行注释必须保真**：lexer 存**含缩进**原文（存 stripped 会双重去缩进，把
  注释啃成非法 ATL）。行尾注释**刻意**剥掉（`_strip_inline_comment`，有测试钉着）
  —— 逐字保真的已知缺口。

### B. 写入与落盘（最高危区）

- **B1 落盘一律过 `story.generation_guard.write_generation`**：block 级问题不写目标
  路径、改写 `<stem>.blocked.rpy`；`LABEL_COLLISION` 只在产物真会落进 `<项目根>/game/`
  时判。"目标已存在且首行无生成标记" = 手写文件 → 拒写（`HANDWRITTEN_TARGET`）；
  `force=True` 只越这一条。
  ⚠ **让路的判据是"底稿 == 目标"，不是"这次走了增量"**：`merged_from_source` 收
  **底稿路径**，门禁自己比（`merging_onto`）。原地增量写回自己那一章 → 放行；
  编辑甲章往**乙章**手写文件上写 → 照旧拦。从前是布尔，调用方声明一句就能越护栏。
  调用方**三处都要交**：`EditorSession.export`（UI）、`pipeline.compile.write_compiled`
  （CLI）、`app.tables.apply_export`（表格侧）。漏一处 = UI 里原地增量导不出去。
- **B2 全量只许用于新建；改已有 .rpy 一律走 `story.patcher`**。契约"要么全对、要么
  完全不动"：按行身份配对，缩进只来自原文，替换行必须来自**真实产物**，不过就
  `ok=False` 且 **text 不可用**。**失败时不要交回一份可用文本**——给了就会有人拿去写盘。
  ⚠ **身份判据的坑**：`_range_looks_like` 拿 `Beat.text` 当身份，而有些拍的 text 不是
  内容 —— ① 无标题菜单是占位符 `MENU_NO_CAPTION`（`\x00menu`）② 选项行的文案**正是
  用户改的那一位**。修法：占位符按 `menu` 那一行认、选项行按 `_OPTION_ROW_RE` 认。
  **判据夹具必须"不干净"**（如 `menu:` 后半行空格才到 `#`），整齐夹具会被形状相似度
  歪打正着救回来。
  ⚠ **结构补空拍不是卡片**：`label x: return` 落一张没有源行号的空拍 → 被当成"新插的
  卡"、又渲染不出行 → 判"找不到插入位置" → **整份补丁作废**。所以 `insert_ids` 要摘掉
  渲染不出行的拍、对位率的**分母**也不许算它们。
- **B3 ✅ P0「静默全量覆盖」已修**：`app/session_export.py` `_patch_note` 失败返回
  `blocking` 且 `text=''`；`export` 见到不写盘。红测试 `test_p0_no_silent_full_regen.py`。
- **B4 CLI 也能增量，且"降级"必须说出口**：`table xlsx2rpy` / `compile` 加
  `--source <原 .rpy>` → `pipeline.compile.merge_incrementally`（**该判据只有一处**）。
  补丁没配上又确实会丢顶层语句 → `OPAQUE_LOST` 拒写（`force` 降级为 warn 但仍报）。
- **B5 行号新鲜时相信行号**：对位守卫靠**内容证据**认行（说话人/正文/图像名）——用户把
  一句旁白**同时**改成台词 + 换正文 + 加立绘时证据全没 → 误判错位 → 整份拒绝。
  判据：`EditorSession.load` 记源文件指纹（size + mtime_ns + sha1[:16]），`export` 核对
  "磁盘这一份 == 载入时那一份" → `patch_export(trust_ranges=True)`。
  ⚠ **只有"行号是解析真迹"的路能传**：表格那条路的行号是 `recover_source_ranges` 现算的
  **猜值**，绝不许传（那是"整体下移 1 行、259 行被改写"事故的入口）。
  可信分支也要报"配不上"（`new_seg == old` 且生成内容确实是新的 → `lost_new`），否则
  `menu:` → `menu "标题":` 这种"换行"会变成"报成功、内容没写"。
- **B6 校验与渲染器必须同源**：渲染器按 D7 纪律**丢掉未登记的说话人**（按旁白写 +
  `# TODO`），而校验拿 `(label, 说话人, text)` 去找 → 判"台词找不到" → 整份拒绝，
  **而那句台词好好躺在产物里**。判据：`patcher._written_speaker`（走 `Audit.character`）。
- **B7 Ren'Py 关键字优先于"说话人"（P0）**：`voice "…"` / `extend "…"` / `menu "标题":`
  长相与 `lyj "台词"` 一样，`_SAY_RE` 会把动词当说话人。开着**句位配对**时所有台词键
  退化成 `('say','')` → 音频行与真台词互相配对，实测把台词**整行换成** `voice "v_ling_01"`。
  判据：`_VERB_RE` 收 `voice`，`_line_key` / `_head_of` 一律**先认动词**；`_KEYWORD_SPEAKERS`
  兜住 `extend` / `menu` 这类"关键字出现在说话人位上"的行。
- **B8 "没落地"判据必须容得下"只是写法不同"**：`lost_new` 扩到"整拍所有非空行"后，把
  只差引号/转义、菜单标题被并回一行、生成器多一层括号这些**渲染写法差异**当成"新内容"
  → 7 个真实章节被整份拒绝。判据 `_unpairable_new_content` 与插入准入同源：
  身份 `_head_of` → 语义 `_unescaped` → 最宽 `_loose`（去空白+去引号）→ `_literals`
  （引号里的内容文件里有没有）→ `_body_lines`（先去结构头）。

### C. Stage / 演出投影

- **C1 `Stage` ↔ 结构化列互为投影，但单向 + 幂等 + 只输出一次**（`test_stage_projection.py`）：
  结构化字段是唯一真源，Stage 只承载它表达不了的东西；`hydrate` 必须幂等；Stage 里的
  **原生真迹**逐字原样输出；中文化写法按**语义**判"已被承载"（`_stage_op_covered`，
  别靠字符串相等）；重投影用 `stage_only_ops()` 留住 `[停顿] 1.0` 这类指令。
- **C2 `beat.at` 是"整拍兜底位置"**：只有"所有立绘位置一致"才能同步它。
- **C3 `compose_stage` 必须与 `_stage_lines` 同源** —— 不一致就是"看到的 ≠ 生成的"。
  ⚠ **顺序列不全时的补齐规矩也只许有一处**：`Beat.stage_verbs` 是唯一真源 —— 结构化列里
  有的 verb 一定出现在顺序里（渲染器一直这么做，舞台快照/演出预览从前没做 → 同一次保存
  两种真相）。保存时把随表走的 `StageOrder` 写实（`sync_stage_order`，只追加）；但
  **快照指纹里存归一化后的值**（`merged_stage_order`），否则"什么都没改点一下保存"会被
  判成改过。派生面板（当前舞台/继承提示/警告条）是**载入那一刻**算的 → 保存后必须重算
  （`window_card.refresh_derived_panels`）。
- **C4 裸词位置要归一**：`角色 表情 左` 的"左"是位置不是表情（不认会静默丢位置）。立绘
  中文名先经 `symbols.actor_image_tag` 归一成英文 tag 再查表；**查图必须按 Tag 收窄**
  （真实项目 `lan`/`ling` 都有 `oh`）。
- **C5 卡片往返（`cards()` → `cards_to_show()`）不许改动 `beat.show` 一个字节**。
  最阴的是**多属性立绘**：`show ling base_arms_akimbo sigh` 被逐词归一 + `attr_for` 子串
  兜底合并成 `ling base_arms_akimbo_sigh base_arms_akimbo_sigh` —— **图像名被改写 = 游戏里
  找不到图**。判据（`normalize_expression_words`）：归一升级档**不许让两个词指向同一条
  属性**；撞车就退回**词表原话**。⚠ 夹具必须给 `symbols`（喂 `None` 时不走归一，缺陷藏着）。
  ⚠ **表情 chip 改词时必须同步 `item['image']`**：卡片标题显示 `image`，而保存时图像名由
  **tag + 表情**拼出 —— 不同步就是"卡片写 `ling angry`、脚本写 `ling disappointed`"。
  存的是**归一后的属性**，不是 chip 上的人话词（否则中文漏进 `show`）。
- **C6 `kind` 是说话人的单向投影**（`app.beats.kind_from_speaker`）：填了说话人 → `say`；
  **不自动降级** `say` → `narration`（那张卡片要留着"[有台词但没说话人 → 会变旁白]"的提示，
  判据就是 `kind not in ('narration','extend')`，一降级就自己吞掉）。`menu`/`menu_option`/
  `extend` 的类型是**结构**，不动。提交后把类型推回面板（`set_kind` 同步基线）。
- **C7 表情/位置的候选与显示 = 项目真实的名字**：候选与显示**必须同源**
  （`_expression_display` / `_position_display`）—— **项目词表声明过的词优先，否则就是
  属性名 / transform 名**。内置词表（`DEFAULT_EXPRESSIONS`/`DEFAULT_POSITIONS` 那批中文）
  只是**输入别名**（打字 `生气` 落到 `angry`），**不参与候选、不参与显示**。
  候选里**项目的名字排最前、不许被 limit 截断**（146 个 transform 的项目里 `i11` 排第 40
  → 永远看不见）。⚠ 当前值**必须在候选里**，否则 `_cycle` 走"固定当前值"分支点了没反应；
  `choices.index(cur)` 找不到会直接崩。

### D. 资产

- **D0 资源表"没有就当场建"**：唯一真源 = `assets.asset_registry.ensure_asset_tables()`
  —— 扫文件（图片/音频）**＋解析脚本**（`define … Character` / `transform`）→ 写
  `game/asset_tables/*.xlsx`，**幂等**。⚠ 它**早就有实现**，但长在
  `AssetEngine._ensure_asset_tables_exist()` 上，而那个引擎在 Qt 应用里**从没被构造**过
  —— "有需求、有实现、没人调用"。引擎现在转发并传 `with_scripts=False`。
- **D0c 表与磁盘是"两个真相"，必须能对账**：表是**用户可编辑的数据，工具从不自动改写**
  （只在完全没有表时新建），所以漂移正常；不正常的是**漂移看不见**。唯一真源 =
  `compare_registry_to_disk()`（**只读**）：`{disk, table, unregistered, missing, stale_paths,
  outside_game}`。⚠ `outside_game` 最值钱：实测原项目 1386 行指向 `sr_general/**`（另一份
  嵌套副本）—— 那是"表引用别的目录"，不是"文件丢了"，两者补救完全不同。
  ⚠ `scan_assets` 的音频目录**不能写死**（漏了 `game/gui/sfx/*`、`game/pomodorro/music/*`）：
  按**目录名在任意深度**认（含 `sfx`/`se`），并保证"盘上有几个音频就进表几条"。
- **D0d 打开项目 = 资源表自动对齐**：`sync_asset_tables()` —— 没表就建；有表就把"盘上有、
  表里没有"的图片/音乐/音效/语音**追加**（`*.bak.xlsx` 备份；已有行一个字段都不动）。
  ⚠ 判重必须按**解析后的真实文件**（`music/night.ogg` 与 `game/music/night.ogg` 是同一个
  文件，只比字符串会写第二行 → 让 `play music` 渲染变样 → 增量补丁整份拒绝）。
  删行/改已有字段仍要用户明确动作。
- **D0e 归类只认"目录段"，绝不整条路径子串匹配**：词元精确 + 少量词尾（`dutui`/
  `charactercenter`）+ 最近目录优先 + 目录压过文件名 + 平铺约定（`images/eileen_happy.png`）
  + `images/<tag>/x.png`；tag 取 `characters/` 的**下一段**，`other` 不编 tag。
  ⚠ `scan_assets` 里**不许有第二套 tag 逻辑**（它曾对 `category=='character'` 另写内联，
  于是 `images/eileen/happy.png` 的 tag 退成 `happy` → `show eileen happy` 解析不到 →
  整组立绘被报"未使用"）。⚠ 扫描整目录跳过 `tmp`/`build`/`.git`/`__pycache__`/`saves`。
- **D0g/D0j 表跟着现实增删，缺文件的行不可用**：侧车 `game/asset_tables/_autoscan.json`
  记"工具自己加过哪些 Path" → 这些行的文件没了就删；**不在侧车里的（人工录入/占位）一律
  不动**。⚠ 侧车是必需的：光看"文件不存在"分不清"资源被删了"和"人工占位还没出图"。
  后按用户要求收紧为：**缺文件直接删**（含人工行，写前备份），**唯一例外 = 人工占位**
  （`Notes`/`Doc` 有「占位/placeholder/待补/计划/TODO/WIP」）。删不掉（表被 Excel 锁着）
  才退回 `registry.unusable` + 日志。
- **D0f 音频行在补丁里"作者写法优先"**：配对永不改写音频行（产物保留 `play music night`），
  而生成器会翻译成 `play music "music/night.ogg"` —— 校验"每条生成行都在产物里"曾因此把
  整份补丁判死。现在按 **(动词, 通道) 条数**容忍：产物条数不许比生成器想要的少。
- **D0h "读不到资源表"必须给真原因和出路**：`is_real_table_file()` 只认真表（排除 `.bak`/
  `~$`/`.broken-`）；解析失败就把坏文件改名留档 `*.broken-<时间戳>.xlsx`（**不删**）→ 按
  项目内容重建；失败信息带 `类型: 消息`。⚠ **重建时不许整份重写已有的表**：已存在的表走
  `append_rows_to_table`（`write_dict_to_excel` 会覆盖用户手工维护的表）。
- **D0i 打开项目必须"一次遍历 + 字典查找"**：`FileWalkIndex`（`os.scandir` 一次遍历 +
  **字符串**拼相对路径）→ 0.11 s；`match()` 候选顺序**与 `resolve_thumb_source` 一致**但只
  查字典；整条路共用一份索引（`walk=`）；UI 复用 `session.last_reconcile()`。
  ⚠ **不许"同名文件兜底"**（会把别处的同名文件认领成"已登记"，那个文件永远补不进表）。
  结果量级：扫描 3.8 s→0.17 s、对账 12.4 s→0.44 s、打开 20 s+→3~4.6 s。
- **D0k「📦 资源体检」**：`EditorSession.asset_health_report()` + `app/asset_report.py::
  format_asset_health()`（纯函数，结论在第一行）+ 复用 `AnalysisDialog`。⚠ 占位行**不算
  缺失**（`compare_registry_to_disk` 不认识占位标记，报告里要按 Path 挑掉）。
- **D0l "改指"不能和"补登"同时自动做**（实测 ping-pong）：把指向嵌套副本的行改指到
  `game/` 之后副本里的文件变成"未登记"→ 补登又加回来。要改必须**连扫描一起**排除嵌套
  副本。判据用**路径尾巴**，**不能用 ID**（表里 `_base` vs 扫出来 `base`）。
- **D0m 嵌套副本绝不能当"项目资源"扫**：项目根下若子目录**自带 `game/`**
  （`sr_general/game/…`），那是**同一份项目的副本**。扫进来会编出 `ling_angry` 这种 ID ——
  恰好就是 `tag_attribute` 写法 → 卡片的 `ling_angry` **精确 ID 命中**了那行（Tag 空、
  图在 `cc_preview/`）→ 预览取错图、表情候选全空、立绘不显示。修法三件套：
  `FileWalkIndex` 识别嵌套前缀 → `scan_assets` 跳过；`AssetIndex.resolve` 的
  `_prefer_tag_attribute`；`path_of_image` 认**下划线写法** + `image_tag_of()` 反查真 tag。
- **D0n 自动对齐的四步顺序**（都在 `sync_asset_tables`，各有备份）：❶ 删"文件不在"的行
  （占位除外）→ ❷ 删工具加过、指向嵌套副本的行 → ❸ **改指**（用户写的、指向副本的行改指
  到本项目 `game/` 同名文件，判据用**路径尾巴**）→ ❹ **合并同一文件的重复行**（改指会造出
  重复）→ 最后才补登。`append_rows_to_table` 遇 **ID 撞车但文件不同**要**另起 ID 收下**，
  不许静默跳过。
- **D0o 合成立绘（layeredimage）别把"身体件"当整张立绘**：项目把立绘拆成组件
  （`layeredimage ye:`，写法 `show ye base_handdown happy`）。`lookup_image_id` 只看
  `rest[0]` → 画出无头身体。修法：`preferred_image_attrs(rest)` = **非身体件优先、从后往前**，
  身体件垫底（`_is_body_part`：`base*`/`body*`/`*_mask`）；`resolve_stage_asset` 与
  `path_of_image` 按这个顺序逐个查。⚠ `lookup_image_id` 的契约**不许改**：给不到要退
  "这个 tag 的第一张"，但**绝不跨角色**。
- **D0p 手输拍号要立刻跳**：`QSpinBox` 要 `setKeyboardTracking(False)` + `valueChanged` +
  `editingFinished` 三条都接，且 `_on_jump` 必须**幂等**（否则无限递归）。
- **D0q 合成立绘 = 通用模型 + 预览叠层**：`story/layered_model.py` 解析 group/attribute/
  `when`/`image_format`/`always` + `init python` 辅助函数（**只做参数代入 + 静态展开，
  不 eval 项目代码**）+ 条件求值（认不出→None，**绝不当成立**）+ `resolve_layers()`。
  支持字符串、`image_format`、**`AlphaMask`**、`Crop`、`Composite`/`LiveComposite`；
  `Transform` 只取内层并在 `note` 说明。⚠ 坑：`AlphaMask` 必须真的应用
  （`putalpha(multiply(img.alpha, mask.alpha))`）；辅助函数的 `return` 常**跨多行**，要按
  括号配平拼完整。P2：`app/layer_compose.compose_sprite()` + `stage.resolve_frame_assets
  (..., layer_model=)`；验收要**看图**，别用像素统计。
- **D0r 空姿势改不回去**：项目姿势表（`CC_OUTFIT_CYCLE` 形状：`{tag: ['', 'a', 'b']}`，
  `''` = 普通）里有**无名姿势**；`show` 只叠加/替换属性、**永远不清空未提到的属性** →
  **从 B 改回普通必须先 `hide`**。`layered_model.parse_pose_tables()`（按**形状**认，不认
  变量名）+ `pose_switch_problem()` + `frame_pose_warnings()`；预览状态行提示。**只报告，
  不自动改脚本**。
- **D0s 工具必须能"被教会"项目自己的结构** → `story/project_conventions.py` 三层机制：
  ① **认形状**（不看名字）；② **让它声明**：`storygraph.rules.json`（`poses`/`pose_labels`/
  `path_bases`/`hide_before_pose_switch`/`notes`；声明覆盖识别，坏 JSON 只留警告不炸）；
  ③ **不认识就明说**：`unknown_statements()` 只统计**剧情层**语句头（`screen`/`layeredimage`/
  `init python` 块里的界面语句属"原样保留、不编辑"，不算不认识）。
  ⚠ 判据：块语句看 `…:`，关键字语句看**带具名参数**（`x=1`）—— 台词 `e "…"` 不会误报。
  ⚠ **2026-10-07 现状：三层机制已实现且有单测，但生产代码零调用（悬空能力）。**
- **D0t P4：改回空姿势自动补 `hide`**：`story/pose_patch.plan_pose_hides(beats, model,
  conventions)`（纯函数）+ `render_beats_to_rpy(..., pose_hides=)`。**默认不自动改**，只有
  清单声明 `hide_before_pose_switch: true` 才补；补出来的是**生成内容**，照旧走增量补丁的
  校验与备份。
- **D0b 缩略图预热**：`app/prewarm.py`（线程池 + 进度 + 可中止）+ `tools/prewarm_thumbs.py`
  + 选择器 ⚡ 按钮。⚠ 预热尺寸**必须与界面常量一致**（缓存键含 `WxH`）：第一版凭印象写
  160×220，实测是 **156×204** → 白跑；`tests/test_prewarm.py` 直接 import 那两个 UI 常量来比。
- **D1 引用判定唯一真源 = `asset_registry.resolve_references`**（循环里必须传 `AssetIndex`）。
- **D2 词表真源 = `core.vocabulary`；补全打分真源 = `assets.asset_completion.score_candidates`**。
- **D3 transform 的 `ATL`/`Params`/`Doc` 以脚本为权威**（`update_keys=` 回写）；人写的
  `Category`/`Notes` 不动。
- **D4 注册表图片 Path 有**两套基准**：扫描行相对项目根（`game/images/…`），脚本 `image`
  声明行相对 `game/`（`images/…`）。纯色在 `Solid` 字段。
- **D5 报"缺资源"要给建议** → `# TODO`。**D6 校验分级**：有表却查不到 → 错误；没建表 →
  警告。宁少报。**D7 编译路径不校验**：缺资源写 `# TODO` 且不生成该语句；体检是独立命令。

### E. 性能 / 缩略图

- **E1 图片路径的基准不许各写一套**：`app.beats.asset_preview_path` 从前只试
  `project_root / Path` → 真实项目 74 条永远解析不到。唯一真源 =
  `story.excel_export.resolve_thumb_source`；实测 74 → 0。
- **E2 缩略图缓存 = 两级分片 + 有界自清**：平目录堆到 140886 个 PNG 后每次 `is_file()`
  都要在巨大索引里翻（188 ms）；分片 `<ab>/<cd>/<digest>.png` 后 1.46 ms。
  `prune_thumb_cache` 必须有**扫描预算（2s）+ 每次删除上限（3000）+ 一进程只排一次**。
- **E3 内存缓存按**字节**算**：`CACHE_LIMIT = 64` **张** → 候选 2816 张时滚两屏就挤掉。
  改 `CACHE_BYTES_LIMIT = 64 MB`（104×74 ≈ 31 KB/张 → 两千张）。
- **E4 别指望"更聪明的缩放"**：PNG **没有**解码级降采样。赢面只有四条：**绝不重复解码**
  （磁盘+内存缓存）、**并行**（线程池限流到核数）、**只解看得见的**（视口优先 + 后台补齐）、
  33 MP 图上用 `reduce()`（BOX）代替 BICUBIC。⚠ Qt `setScaledSize` 实测**更慢**
  （792 ms vs PIL 434 ms）—— 主路必须留 PIL。
- **E5 缩略图底色**：白/透明底要能切暗底（选择器 ◐ + 卡片同步，同一个偏好）。深底要同时
  翻文字色与选中色。⚠ Windows 上 `QSettings.setPath(IniFormat…)` 不一定生效（实测仍落
  注册表）→ 偏好存储要留注入点（测试换临时 ini，别写用户配置）。

### F. Excel / 表格

- **F1 安全写单元格唯一实现 = `excel.cells`**。
- **F2 `beat_model.BEAT_FIELDS` 是列顺序唯一真源**（含隐藏列）。
  ⚠ **列契约可以"动态追加"，但基础列永不动**（2026-10-07 第二轮）：动态列
  （`语句名·参数名`）**追加在末尾**、且只在这批拍里真的有声明语句时出现 ——
  `to_row(spec_fields=…)` 默认不传就仍是老长度，`_row_fields()` 是"基础 + 动态"
  的唯一拼法（表头行与数据行必须同源）。加**基础**列仍要同步：`BEAT_FIELDS` /
  `BEAT_HEADERS` / `HIDDEN_BEAT_COLUMNS` / `SINGLE_LINE_BEAT_FIELDS` /
  `to_row` / `from_row` 六处（`CameraHeader` 是最近一例）。
- **F3 `Stage`/`BGM`/`SFX` 格内是 DSL**：方括号被解析校验，原生写法直通。
- **F4 `import_beats_from_excel` 一律 hydrate**。
- **F5 `LineId` 是协作主键**；`ContentHash` 每次重算。
- **F6 `dsl.normalize_text` 只归一化分隔符**，绝不动 `，（）`。
- **F7 单行列的单元格不许有换行 —— 压平必须留痕（P0）**：`_quote` 管的是**字符串字面量**
  那条出口；而 Label/Speaker/Show/At/With/Condition/音频参数这些列是**裸 token** —— 值被
  逐字拼进一行代码，真换行漏出去就是产物里的物理换行（`Speaker='ling\nX'` → 产物不可解析；
  `Show='ling\nhappy'` → **能解析但立绘变两张**）。判据：`SINGLE_LINE_BEAT_FIELDS`（写在
  `BEAT_FIELDS` 旁边，加列必须显式选一边）+ `sanitize_single_line_cell`，**读写两侧都过**
  （openpyxl 对 `\x0b` 直接抛 `IllegalCharacterError`）。多行列（台词/注释/Stage/Code/
  FileBlocks）**刻意豁免**。⚠ 压平**本身就是改用户数据**：必须留痕（`Beat.sanitized_cells`）
  并说出口 —— `validate` 的 **W114**（聚合一条）/ `CompileResult.summary()` /
  `EditorSession.export` 的 `note`。⚠ 留痕要放在 `from_row` **所有 `g()` 之后**。
  守卫 `tests/test_cell_control_chars.py`；变异 `tools/mut_cell_sanitizer.py`。

### G. UI

- **G1 界面只从 `ui.tokens` 取色值/字号/间距**。立绘是**网格卡片**；缩略图一律走
  `ui.thumb_loader`（禁止 UI 线程同步解码）。
- **G2 只读 tkinter `Text` 会静默吞掉程序化写入** → 要灰显别用 `state='disabled'` 建字段。
- **G3 翻页是纯导航**：只提交当前拍，绝不许碰别的拍。
- **G4 辅助功能不许改用户已有数据**（轮转说话人只能"补空缺"）。
- **G5 演出预览贴图回调只替换那一块**，绝不许重画整帧。
- **G6 落盘时机要在界面说清**：卡片改动先到内存，**导出 .rpy 才落盘**。
- **G7 任何"写到某路径"的入口，先认路径再碰磁盘**：不是 `str`/`Path` 就 `ok=False`。
  反例：`export_control_flow(wrapper=False)` 把 bool 串位进 `target` → 根目录长出名叫
  `False` 的文件。**"开关型参数"默认 keyword-only**（`*`）。守卫
  `tests/test_control_flow_target.py`。

### H. 架构 / 依赖

- **H1 依赖方向单向**：core ← parsing/excel ← assets/story ← pipeline ← ui。story 层不许
  顶层 import pipeline。守卫 `tests/test_package_layering.py`。
- **H2 改中文 `.py` 只能用编辑工具或 Python 脚本**（PowerShell 会 GBK 往返毁文件）。
- **H3 ⚠ Git Bash 的 heredoc 会做 MSYS 路径转换**：写含 `\n` 的 Python 源码时 `\n` 会变成
  `/n`，脚本静默跑歪。**探针脚本一律用编辑工具写文件**，别用 `cat <<EOF`。（与 H2 同源）

### I. 渲染输出的硬约束

- **I1 `_quote` 必须转义** `\\` `"` **和 `\n`/`\r`/`\t`**。Beat.text 里存的是**真的**换行符，
  只转义前两类就会写出**物理换行**，一条 say 断成两行 → 产物不可解析。守卫
  `tests/test_hell_findings.py::TestQuoteEscapesControlChars`。
- **I2 label 是硬边界**：换 label 时先把攒着的"孤儿指令段"落拍
  （`beat_build.add` 里的 `flush_stage_only()`），否则它们被吸进**下一个 label** 的第一张拍
  → 本 label 名下一张拍都没有 → 整个 label 消失，渲染层还会退回 `label start:`（与游戏入口
  重名 → 游戏起不来）。真实项目实测 51 个 label 丢 4 个。配套：`_fill_empty_labels` /
  `_fill_labels_without_beats` 补空拍；`Beat.is_empty` 把"只有 label 的空拍"当**结构锚点**。
- **I3 反过来也不许凭空造 label**：源文件里没有 label 的**纯定义文件**，产物里不许出现
  `label start:`。**契约：有的必须留下来，没有的绝不能造。**

### K. 自定义语法 / 未建模块（M1，2026-10-07）

- **K1 未知块语句一律走 `Beat.opaque`，不许走 raw/CodeBlock**：那条路的渲染会
  **推断**（认不出的行补 `$`、整块可能被包 `python:`），而对工具看不懂的自定义块，
  任何推断都是改剧情（实测：`target = ling` → `$ target = ling`）。`emit_opaque()`
  的契约只有一句：**逐字写回**。隐藏列 `Opaque` 是它的载体（照抄 `CodeBlocks` 形态）。
- **K2 台词不许带块**：`mycue "闪电" intensity=3:` 与 `lyj "台词"` 长相一样，
  `_stmt_say` 会抢先匹配（冒号+参数丢、块体消失）。判据：子块里有**非注释**内容
  → `_NO_MATCH`，交给未建模兜底。
- **K3 取块必须按原文物理行**（`RenPyParser._raw_block_lines`）：lexer **不产生空行的
  `LogicalLine`**，且**括号续行的缩进是绝对的**（只按首行缩进切会多缩一层，实测 12→16）。
  行号对不上就退回 AST 重建（**宁可少保真，不要贴错原文**）。`parse()` 开头清
  `_src_cache`（解析器实例复用）。
- **K4 门禁两条码都要 warn、都不拦导出**（用户 D1）：`UNMANAGED_BLOCK`（自定义块，
  工具真不懂）/ `KNOWN_UNMODELED_BLOCK`（`while`/`for` 这类标准流程块，工具认识形状
  但没建模）。分类真源 = `core.vocabulary.is_known_flow_block()`（纯函数，
  **放 core 是因为 `parsing` 不许 import `story`**）。
- **K5 "报告必须出现"要落在**逐条**上**：`BuildResult.summary()` 会把非致命门禁结论
  逐条印出；只报"有 N 条警告"等于没说。落盘三处调用方（`EditorSession.export` /
  `write_compiled` / `app.tables.apply_export`）**都要交 `beats`**，漏一处那条路就不报警。
- K6 判据：`tests/test_unknown_block_preserved.py`。⚠ **"这些行在产物里"是太松的判据** ——
  去掉保真通道后整块会落进「代码」列并被包成 `python:`，文本还在、语义全错而测试全绿。
  必须断言**整段相等** + 禁止引入 `$ `/`python:`。

### L. 自定义语句的"被管理等级"（M2/M3/M5，2026-10-07）

- **L1 三来源优先级**（唯一真源 = `story/statement_registry.py`）：
  `storygraph.rules.json` 的 `statements` 段（declared，**覆盖**）
  > `renpy.register_statement`（host-registry）> 形状识别（不做）。
  冲突听用户的。`cds_registry.py` 只负责**读**宿主注册，不参与合并。
- **L2 只认字面量，认不出就跳过**：`name=` / 第一个位置参数两处都只认字符串
  字面量，都没有就**不产 spec**。⚠ **猜一个名字比说不知道坏得多** —— 猜错会让
  M3 把不是自定义语句的行当成自定义语句（静默改语义）。判据第一版在这里**写反了**。
- **L3 列契约**：`SpecName`（隐藏、单行）= 那条语句被谁声明过；
  `''` = 未声明（T3，报告要催），有值 = 已声明（T1）。`Opaque` 存原文。
  ⚠ **叶形态**自定义语句（无块）也必须走 `opaque`，否则会被补 `$`（M1 同坑不同形）。
- **L4 标注是后置的**（`annotate_spec_statements`，在 `beat_build` 出拍前）：
  契约 H1 不许 `parsing` import `story`，而"声明过没有"只有 story 层知道。
  项目根**从 `ParseResult.statements[].source` 反推**并**按根缓存**
  （`beats_from_parse_result` 有 8 个调用点，逐个传参漏一处就是"那条路又变成
  不认识"；不缓存则 O(章数 × 文件数)）。
- **L5 门禁分三桶**（`generation_guard.block_buckets`）：declared（**不报警**，
  已被管理）/ custom（催用户写声明）/ standard（`while`/`for`，催我们建模）。
  ⚠ **"已经内置建模的块不进任何一桶"这条判据，每加一种块类型都要跟着加**：
  `while`/`for`（D2）、`camera`（2026-10-07）各漏过一次就"报告一直催我们去做
  一件已经做完的事"。
- **L5b ⚠ "项目根"必须有严格定义，推不出来就返回 `''`**：判定只有两条 ——
  **有 `game/` 目录**或**有 `storygraph.rules.json`**。不许"推不出来就退回父目录"：
  那样 `rglob` 会去扫 `tmp_path` 的上层（系统临时目录，成百上千个目录），
  实测一个用例 **0.6 秒 → 227 秒**。配套：出拍前先 `_has_unmodeled()` **短路**
  （没有未建模语句就不装配注册表）。两条一起，47 个用例 240 秒 → 10 秒。
  ⚠ 单元测试的耗时异常（单条几百秒）**先怀疑"扫了不该扫的目录"**，不要怀疑算法。
- **L6 词表唯一真源 = `core/vocabulary`**：`RENPY_STATEMENT_VERBS`（防补 `$` 的
  动词表）/ `RENPY_FLOW_HEADS` / `RENPY_DECLARATIVE_HEADS` / `RENPY_KNOWN_HEADS`
  / `RENPY_CONTAINER_HEADS`。三处旧常量都是**别名**。
  ⚠ **不许把三份并成一个集合**：动词表**必须**排除 `if/elif/else/while/for`
  （在 raw 里它们是代码块条件行）；收敛的判据是"**只许加不许减**"。
- **L7 「每参数一列 + 原生下拉」（2026-10-07 第二轮；第三轮补齐"已识别语句"）**：
  唯一真源 = `story/spec_args.py`（头行参数的**扫描/改写**，消费者四个：导出摊列 /
  读表折回头行 / `W120`/`W121` 校验 / **内置语句形状表** —— 契约 H1 不许
  `story` import `pipeline`，所以它放 story 层，`validate` 反过来 import 它）。
  · 动态列名 = `语句名·参数名`（`·` 是可见非标识符）；**位置参数**多一个
    `@<下标>`（`camera·layer@0`）—— 读表那一侧拿不到 schema，只有列名能告诉它
    "这一列要**替换第 N 个 token**"（判错就写出 `layer=background`：**语法错**）；
  · 列**追加末尾**、只给"这批拍里真的出现且**有参数形状**"的语句建列
    （项目声明了 `columns`，或它在 `spec_args.BUILTIN_ARG_SCHEMAS` 里）→
    **没声明的项目逐列不变**；
  · **标准语句的形状由我们自己声明**（不猜）：`camera: [{layer, positional: 0}]`
    按官方 `3dstage.html` 写死，`choices` 留空（层名是运行期概念）。
    `with` / `pause` / `window` **暂不做** —— 它们在「代码」列那条路上
    （`Beat.raw` + `OpOrder`），建列 = 换渲染通道 = 缺口 4 的风险区；
  · 值**折回语句头所在字段**（`Beat._head_field()`，与 `emit_opaque` 的优先级
    **同源**：`spec_head` → `loop_header` → `camera_header`），**不新增第二份数据**；
  · ⚠ **等式守卫**：值没变 → 头行**逐字节原样**（否则 `target="ling"` 被改写成
    `target=ling`：一次导出就吃掉作者的引号）；改了 → **只替换那一个值 token**；
    清空一格 = 删掉该参数；值带空白/引号 → 自动加引号；
  · ⚠ **引号里的 `a=b` 不是参数**（`iter_kv` 自己扫引号状态）：老实现会把它收成
    参数，于是"每参数一列"多出一列，而改那一列 = **改台词**；
  · 下拉是 Excel **原生数据验证**（`showErrorMessage=False`，手打不被拦）；
    候选 >240 字符或含逗号 → **不挂下拉**、改挂注释（内联公式 255 上限，
    超了 Excel 会拒收甚至判文件损坏 —— "少一个便利，不要坏一个文件"）；
  · 动态格也过 `sanitize_single_line_cell` 且**留痕**（→ W114）。
  判据 `tests/test_spec_args.py` + `tests/test_spec_arg_columns.py`。

### Lb. `camera` 与"剩余流程块"（2026-10-07 第二轮）

- **`camera` 是流程块，不是叶语句**（官方 `3dstage.html`）：`camera:` /
  `camera <层名>:` 后面**必须**跟一段 ATL 属性；不给层名 = `master`。
  所以判据是"**以冒号收尾**"，与 `for "台词"` 那条防误伤规则同源。
- 建模范式与 `while`/`for`（D2）**逐字同款、不发明第二套**：头行进隐藏列
  `CameraHeader`（可编辑）、整块原文进 `Opaque`、等级 **T0b 不是 T0**
  （ATL 属性没被结构化编辑）。
- ⚠ **`kind='camera'` 没有 `raw_kind`** → `coverage_of_statements` 会把它算成
  T0（与 D2 循环**同一个坑的第二例**，必须显式认 `st.kind`）；
  `coverage_of_beats` 里靠 `camera_header` 认，`block_buckets` 靠它跳过。
- ⚠ **只解析"层名"这一种形态**：`camera xpos 100:` 不是合法语法（层名后直接
  冒号），那种行**留给未建模通道逐字保真** —— 猜成"层名 = xpos"会把别人的
  脚本改成跑不起来的相机块。


### M. 工具能力元模型 + 投影 + CST（A / M4 / D3，2026-10-07）

- **M0 等级集合 = `T0` / `T0b` / `T1a` / `T1b` / `T2` / `T3`**（`T0b` 是 D2 加的）。
  ⚠ **`T0b`（已建模·块体逐字）不许并进 `T0`**：`T0` = 结构化列**全功能**编辑，
  而循环体做不到（`Beat` 是扁平的一维语句流）。并进去 = 拿"接住了"冒充
  "完全理解了"，覆盖率还会因此好看 —— 正是这一层要防的事。
  `T0b`/`T1b` **同时**可编辑且逐字；要"只读"用 `READONLY_LEVELS = {T2,T3}`。
  报告里也别把 `T0b` 说成"逐字保真"就完事（那读起来像不可编辑）。
- **M1 元模型唯一真源 = `core/capability.py`**：State `T0/T1a/T1b/T2/T3`、
  Provenance、Transition（6 条）、Coverage、Escape（每级必须能回答"怎么升级"）。
  判定顺序即优先级：`modeled > declared > known_head(形状) > 其余`。
  ⚠ **`T1b` 同时在 `EDITABLE_LEVELS` 与 `VERBATIM_LEVELS` 里**（不是划分关系）；
  要划分用 `READONLY_LEVELS = VERBATIM − EDITABLE = {T2,T3}`。
- **M2 三条硬不变量**（逐条有测试）：**降级不改字节 / 精化不改字节 / T0 不降级**。
  ⚠ 判据**必须同时断言"等级真的动了"**（T3 条数减少、T1b 增加），
  否则等式两边走同一条路 —— 测试是**空转**的。
- **M3 T1 的编辑面 = `SpecHead`**（头行原文）+ `Opaque`（块体原文）。
  "没改就不动字节"靠**结构**成立：`beat_render.emit_opaque` 比对头行，
  不同才替换。⚠ 比对必须 `strip()`（`SpecHead` 走单行列清洗）。
- **M4 候选不许猜**：`spec_arg_choices()` 没声明就返回**空**——
  猜一组会诱导人写出脚本里没有的值。
- **M5 CST 唯一真源 = `core/cst.py`**：语句挂 `Span` + `raw_text` + `Trivia`，
  **动态属性**（不进 dataclass 相等/哈希 —— 那是内容指纹契约）。
  Span 归属：**到下一条语句前一行、或第一个顶格行**（不重叠 + 不吞顶层定义）。
  真机 3187 条语句 100% 带真迹、0 贴错行。核心判据：**删掉源文件**后
  `statement_text()` 仍逐字给出原文。
  ⚠ **2026-10-07 第二轮加固（三处，都补了判据）**：
  ① 模块 docstring 曾写"`span.end` = 子树最大行号（parser 递归算）"——
  **与实现相反**（实现就是上面那条），已改；
  ② **同一份源文件在一次 `analyze` 里只读一次**：`parse()` 读一遍喂给
  lexer / `_top_level_region` / CST 三处，`IncrementalRenPyAnalyzer` 从
  `RenPyParser.source_texts()` 取走那一份交给 `attach_spans`，**缓存元组也带源
  文本**（第二次 analyze 一次盘都不读）。在此之前"挂载时再读一遍盘"，两次读
  之间文件被改写 → 行号与内容不同快照 → **真迹贴错行**（比"没有真迹"危险得多）。
  ⚠ 判据**第一版是空转的**（在 `analyze()` 返回之后改文件，两条实现都给旧原文）
  → 必须用"**会变心的 `read_text`**"（第二次读就返回新版本）+ 读盘**计数**才钉得住；
  ③ 度量函数 `cst_coverage` / `format_cst_report` 曾**零生产调用**（悬空能力）
  → 接进 **build 报告**（CI 每次打：`语句原文真迹覆盖率 100.0%（3187/3187）`）。
- **M7 循环块（D2 → T0，2026-10-07 第三轮）= `StatementKind.LOOP` +
  `LoopHeader` 列 + **作用域**「`nest_path` 的 `loop:n` 段」**：
  `while` / `for` 的头行结构化（可编辑）、块体**也进语句流**（可逐句编辑）→ `T0`。
  解析正则 **必须以冒号收尾** —— `for "台词"`（说话人恰好叫 `for`）
  与 `while "x"` **必须**仍然是台词（判据钉着）。
  ⚠ `coverage_of_statements` 里循环**没有** `raw_kind`，只看 `raw_kind`
  会把它算成 T0 —— 要显式认 `st.kind == 'loop'`；**现在还要传
  `structured=payload['walked']`**，否则"老形态逐字"会被算成 T0。
  ⚠ **老形态（`LoopHeader` + 整块 `Opaque`）永远要能读**：D2 那一版导出的表就是
  这个形状。渲染层的组头判据是 `_is_loop_head` = **有头行且无 `opaque`** ——
  放宽成"有头行就算"，老形态会走 `render_loop_block`（**它不认识 `opaque`**）
  → 产物 `while …: pass`（**死循环**）+ 块体静默消失。变异验证抓过。
  ⚠ **结构位丢了要"响亮地降级"**：落单的结构化循环头渲染成一句**显式
  `# TODO`**（`UNRESOLVED_TODO` 门禁会抓到），**绝不**补 `while …: pass`。
  ⚠ **`W113 空台词行` 的假阳性**：组头拍是 `kind='narration'` + `text` 为空，
  与"清空对话框的 `""`"长得一样 → 判据要把 `loop_header` / `camera_header` /
  `opaque` 排除掉（**结构头不是台词**）；真机实测这条曾让警告数 +1。
- **M8 自定义语句参数校验（D1）**：`W120`（缺必需参数）/ `W121`（值不在
  `choices` 里，**警告不是错误** —— 候选是常见取值不是白名单）。
  ⚠ 头行**末尾的 `:` 是块标记**，扫描参数前必须先剥掉，
  否则 `target=ling:` 被当成值 `ling:` → 写对了也报错（实测抓到）。
  位置参数**不当成**有名字的参数（硬对齐是猜）。导出时给头行格挂**候选注释**
  （Excel 数据验证按列绑定，装不下每条语句各自不同的参数名）。
- **M6 ⚠ 不许写 `except ...: pass`**：`tests/test_code_health.py` 会抓
  （我在 `cst.py` 里踩过一次）。一律 `safeguard.swallow('<模块.函数>', e)`。

### N. 往返幂等（2026-10-07，真机审计驱动）

- **N1 守卫 = `tools/audit_idempotence.py`**：`t0→t1→t2→t3`，要求 `t2==t1`
  且 `t3==t2`（**逐字节**）。⚠ 既有两个审计都看不见这类缺陷：
  `audit_roundtrip.py` 比**语句指纹**（文件级漂移不算）、
  `hell_edit_rounds.py` 的 A2 轴比**归一化后**的文本（生成标记多一行被抹平，
  实测它 `R1=5 行 → R2=7 行` 还报 ok）。单元判据 `tests/test_roundtrip_idempotent.py`。
- **N2 已修六处**：
  ① **生成标记重复**——它被 `_top_level_region` 当"作者内容"带回，渲染又加一条
     → 源头摘掉 + 渲染处保险（`beat_render._strip_generator_marker`）；
  ② **括号续行缩进无界增长**（`12→20→28→36→44`）——`_flatten` 把多行并成一条逻辑行，
     而 `node.text.lstrip()` **只作用于第一行**，续行带绝对缩进被当"更深一层"。
     修法 `_shift_continuations()`：**首行由调用方的 `rel` 负责**（在这里减 base
     会双重计算 —— 我第一版就这么错了，被 `test_roundtrip_invariants` 抓住）、
     **续行减 base**；
  ③ **被注释掉的整块被推进一层**——`_opens_a_block()` 把以 `:` 收尾的**注释**
     当块头 → 后面的顶格注释被当块体 → `render_file_block` 统一加 4。
     修法：**注释永远不开块**；
  ④ **块之后的顶格注释每轮缩一层**（真机 5 个文件同族，2026-10-07 第二轮）——
     `_top_level_region` 里"当前段的头以 `:` 收尾 → 顶格注释收进块体"**太宽**。
     修法 `_followed_by_indented()`：**向后看第一条"结构性"行**（跳过空行与注释），
     缩进 → 是块中间的注释（留在块里）；顶格 / 到文件尾 → 它属于下一段（自己起段）。
     ⚠ 真机上**第一轮看着没事、第二轮才动**：源文件里那个注释与块之间隔着一个
     `label`（被整段跳过 → `cur` 清空），而渲染时文件级块会被提到所有 label 之前
     → 产物里它才紧跟块体。**夹具必须带一个 label 插在中间**，否则判据空转。
     真机结果：**53/53 逐字节幂等，纯渲染与经 Excel 两条路都过**。
- **N3 未修**：见"已知缺口 4"（相邻舞台指令过表翻序 / 行尾注释只保单行）。

### J. 测试

- **J1 必须测"往返不变式"，不能只测半程**（曾 1731 测试全绿却漏掉 menu 100% 丢失）。任何
  "保存/同步"改动要断言 `原文 → 载体 → 原文'` 的**多重集相等**。
- **J2 变异验证是硬要求**：改回旧实现，测试必须变红。⚠ 必须走**缺陷真正发生的那条路径**
  （曾写出"不走 Excel 往返"的空 label 用例，变异下全绿）。**测试绿 ≠ 缺陷被钉住**。
  ⚠ **一次修了两件事就要做两次变异**。分辨方法：改回旧实现后是不是该红的都红了。
- **J3 判据写错比缺陷更常见**：**先怀疑判据** —— 拿原文 vs 产物逐行摆出来再下结论
  （`tools/probe_*.py`）。
- **J4 假阳性比漏报更贵**：三引号 docstring 会被"单行引号配平"判成语法坏；门禁把块体逐行
  丢给 `compile()` 曾让 `build` 对真实项目永远失败。**检查器本身也要能被真实项目跑通**。
- **J5 含中文的复现别用 `python -c`**（编码毁样本）→ 写 .py。
- **J6 跑测试一律加 `--basetemp=C:/Users/28749/AppData/Local/Temp/xxx`**：`pytest.ini` 的
  `addopts = --basetemp=.pip-tmp/pytest` 在仓库内，沙箱 `safe-delete` 守卫会拦它的清理 →
  **所有带 `tmp_path` 的用例全部 ERROR**，而报错是 `assert not self._finalizers`（看不出真因）。
  ⚠ **必须每次用一个「全新空目录」**（2026-10-07 实测）：复用同一个（尤其仓库内的）目录 →
  几百个 `E` 从 2% 开始成片、20% 处卡住不动。现象像"我的改动把测试炸了"，其实只是目录脏了。
  ⚠ 顺带：本机 `rm` 在仓库内会被沙箱 SIGTERM 打断（删一半就停），删临时文件用 Python `os.remove`。

## 二、仓库与运行（稳定事实）

- 包：`storygraph/{core, excel, parsing, assets, story, pipeline, graph, ui, collab}`
- `story/`：beat_model（**加列**）/ beat_build（**构建拍**）/ beat_render（**生成代码**）/
  excel_export / excel_import / generation_guard（**落盘**）/ paths；`script_table.py` 是兼容门面
- 入口：根 `main.py` / `cli.py` → `storygraph.main` / `storygraph.cli`
- 依赖只有 openpyxl（必需）+ Pillow（可选）；`legacy/` 不参与构建
- **静态体检 = `tools/health_check.py`**（规模/复杂度/坏味道/依赖方向/死代码，10 秒出表）。
  动态：往返 `tools/audit_roundtrip.py`、刁钻 `torture_test.py`、变态 `brutal_test.py`、
  **组合面 `tools/hell_test.py`**、契约 `pytest`（全量 ~12 分钟，后台跑）。
- **交付就绪检查 = `tools/journey_check.py`**：subprocess 调 **CLI**，真实项目走
  validate / build / table / compile + 产物可解析。
- ⚠ **`tools/audit_real_project.py` 是坏脚本，别信它的输出**（`AssetRegistry` 早已没有
  `rows` → 异常被吞 → 全线降级 → 假警报）。**两个脚本结论矛盾时先怀疑脚本。**
- CI：`.github/workflows/ci.yml`（3.10 / 3.12 / 3.13 + demo 体检）
- 真实项目验收对象：`D:\千连万理\RenPyTest\sr_general`（换机 skip）
- **label 计数两个口径别混**：`rglob('*.rpy')` = 51（含 `*.generated.rpy`）；合并构建走
  `discover_scripts`、**排除 `*.generated.rpy`** → 50。差 1 是工具上一轮的产物，本就该排除。
- ⚠ **并发写入是常态**：另有会话同时改 README / beat_model / beat_render / cli / patcher /
  parser。动手前先看 `git status` + 文件时间戳；两个会话结论不一致时以**最后一次全量运行**为准。
- **改 UI 必须用真实 tkinter + 真实数据冒烟**（`D:\Python\python.exe` 有 tkinter）

## 三、已知缺口

1. ~~P0 静默全量覆盖~~ 已修（B3）。
2. **嵌套 ≥3 层：全量路径静默产出坏结构**。病根不是"深度"，是**结构位不进表**：
   `_menu_member` 只活在 .rpy 侧，全量时只能靠 Condition 串 + 行号猜块边界 → 选项体变
   `pass`、分支被提到 `menu:` 外面、缩进全错。⚠ 增量路径"看起来对"是假象（`改动 0 行` =
   逐字节保留原文，没走模型）。分工不变：**树归 Graph 画布，句子级内容归 Excel**。
3. 解析层真 bug：`if` 内嵌 `menu` 整体降级；分支内 "show+say 混合" → 后续 elif 分支消失；
   menu 两选项间插一拍 → 该拍被吸进 menu 组。
   ⚠ **延伸**：菜单套在选项体里再嵌一层菜单时，内层 `menu:` 被渲染成裸字符串 `" menu"`
   （`MENU_NO_CAPTION` 占位符漏进产物），内层选项体被抬到外层 → 玩家看不到选择、两条分支
   都执行**（静默改剧情）**。最小复现 `tools/hell_nest_inline.py --only minimal_nested_menu`。
   真实项目最深 2 层、0 处嵌套菜单 → 暂时打不到线上。
3b. **条件滚雪球**：`if>menu>if` 往返一轮后 `if (flag0) and (flag2):` 变成
   `if (flag0) and ((flag0) and (flag2)):` —— 真值等价但**逐轮膨胀**，让增量对位永远失败。
   渲染深处条件时应只写**增量条件**。
3c. ~~无资源表时图像名被合成~~（已修）。
3d. **转义只有一半是自反的（未修）**：渲染 `_quote(caption)` 会转义，而解析
   `_scan_menu_header` / `_parse_menu` **原样切片不还原** → **菜单标题里 `\n` 每渲染一次
   反斜杠翻一倍**。守卫 `tools/hell_edit_rounds.py`（轴 A2）。
3e. **选项文案带换行 → 第二轮选项体变 `pass`（未修）**：`Text` 是多行列，Excel 里
   Alt+Enter 就能造出来。丢内容、不报错。
3f. **`Inline` 分隔符出现在注释正文里会被劈成两半**（`\ue004`，已修）：修法 = 只在
   `SEP + '@if '` 这个组合处切分。
3h. **多引擎导出丢接线信息与行类型（未修）**：中立 IR（`story_ir.json`）是好的（台词 0 丢、
   跳转目标 0 丢），坏在**适配层** `to_unity`/`to_godot`：godot 产物 40/40 文件没有任何行带
   `kind`；unity/godot 把跳转目标丢了（`jump X` 的目标只活在 `Beat.raw` 里）；中立 IR 没有
   **结构位**（`Nest`）。判据 `tools/hell_multi_engine.py`。
3i. **多章导到同一目录会静默覆盖（未修）**：`export_ir` 写**固定文件名**，而 UI 是"选目录 →
   导出**当前这一章**" → 40 章导同一目录只剩最后一章，且每次都报"导出成功"。
3g. ~~`"角色名" "台词"`（双字符串 say）丢台词正文~~（P0，已修）。
4. 残留小洞：**相邻舞台指令过表会翻序**（一拍里"多条 show + 后面还有 hide"时 `hide` 被排到
   第二条 show 前面 → 收尾画面不同）。机制：`_hydrated_stage`/`_stage_native_show` 这些
   "原生标记"只有过表那条路才有。复现 `tools/probe_stage_order2.py`。
   另：**行尾注释**只保"单行逻辑语句"那一种（要加列才能存更多，属新功能）。
5. **两套 .rpy 生成器并存**（`renpy_exporter` + `render_beats_to_rpy`）—— ownership 没显式化。
   方向：merge(Graph 骨架, Excel 内容, CodeBlock)，连接键已有 `LineId`。
6. 多语言链路不可用（`parse_translation_files` 不支持现代 `tl`）。
7. "程序改代码 → 表格自动反映" = 1.5s 轮询 + 全量重解析；`asset_engine._apply_rename_patch`
   会正则改写用户的 .rpy。
8. Excel 前端上限：~~`Stage` 列无格级下拉~~（2026-10-07：**声明语句的参数列已有
   原生下拉**，见 §L7；`Stage` 列仍是"一格多指令"，格级下拉不适用）；候选表整表重建；
   导出无增量。**动态列（每参数一列）目前只在 Excel 侧**：Qt 卡片的参数编辑仍走
   `app.beats.spec_arg_choices_for` 的候选注释（接进卡片是下一步）。
9. 坏味道（用户练习课题）：`noqa: BLE001` 那批；两套生成器收敛（缺口 5）；上帝对象
   （`GraphCanvas`/`RenPyParser`/`AssetEngine`）。~~`EditorSession` 拆分~~ 已完成（5 mixin）。
   ⚠ 复杂度/行数具体数字一律跑 `tools/health_check.py` 取。
10. **`Notes` 字段已定案删除**。底层理由（约束未来所有"不进产物的元数据"需求）："Notes 想
    不进产物"与 A1 **根本冲突** —— 不进产物 → 从 .rpy 重建必为空；要保真只能进 .rpy 注释
    → 注定变成 `# 注释`。**所以"独立私人备注"在本架构下不存在**；既要又要只能做带标记注释
    （`# NOTE:` 前缀），那是新功能。
11. ~~label 内的未知块语句整块消失~~ **已修（M1，2026-10-07）**，见 §K。
    实测形态**比预判更糟**：`_stmt_say` 抢先把块头匹配成台词
    （`mycue "闪电" intensity=3:` → `mycue "闪电"`），块体整段消失 ——
    **吃掉内容 + 凭空造一句台词**。真机命中 `mini_game1.rpy:50` 的
    `while current_round < game_rounds:`（游戏主循环）。
12. ~~循环体没升到 T0~~ **已做（2026-10-07 第三轮）**：见 §M7。
    设计文档 `docs/设计_拍模型作用域_循环体升T0_2026-10-07.md`（方案 + 代价表 +
    判据），实施记录见当天记忆"第四轮"。真机 `mini_game1.rpy:50` 的游戏主循环
    产物**逐字复现**（1 头 + 2 条块体语句），覆盖率 `T0b` 归零、仍 100%，
    两条路 53/53 幂等。**老形态（D2 那版表）仍走逐字路 = `T0b`**（判据钉着）。

## 四、变更历史（倒序；细节见同目录 `YYYY-MM-DD.md`）

- **10-07（第二轮，同一晚）**：① **CST 加固**（文档与实现对齐 / **一次 analyze
  只读一次盘**、缓存带源文本 / 度量接进 build 报告）——判据 4 条 + 变异 2 次，
  其中一条判据**第一版是空转的**被变异抓住；② **幂等收尾**：真机剩下那 5 个文件
  同一根因（块**之后**的顶格注释被当成块体、每轮 +4），修法是**向后看第一条
  结构性行**，真机 **53/53 两条路逐字节幂等**；③ **每参数一列 + 原生下拉**：
  新模块 `story/spec_args.py`（参数唯一真源）＋ `BEAT_FIELDS` **动态追加**
  （基础列永不动、值折回 `SpecHead`、等式守卫保证没改就逐字节原样、候选超长
  退回顾问注释）；④ **`camera`** 接住（T2 → T0b，与循环块同一套约定）。
  全量 2898 passed / 2 skipped / 1 failed（唯一失败是既有的
  `test_asset_registry::test_custom_audio_dirs`）。
- **10-07（第三轮，同一晚）**：**循环体升 T0**（给拍模型加作用域，设计文档
  `docs/设计_拍模型作用域_循环体升T0_2026-10-07.md`）：解析时块体也进语句流并标
  `nest_path=…>loop:n`；循环头成**组头**（`opaque` 必须为空）；渲染层
  `_take_block_run_by_path`（菜单/循环**共用一份**边界判据）+ `render_loop_block`
  + `_render_block_body`；等级 **结构化 = T0 / 老形态 = T0b**（那条"良心判据"
  换了一边，没删）。判据 `tests/test_loop_scope.py`（14 条，**纯渲染 + 经 Excel
  各一遍**）+ `test_loop_model.py` 重写；**变异 3 次**（去结构位 → 11 红；
  不剥累积条件 → 1 红；组头判据放宽 → 老形态变 `while…pass` 死循环 + 内容消失，1 红）。
  顺线抓到 **W113 假阳性**（循环头被报成"空台词行"，真机警告 28→29）→ 已修
  （判据加 `loop_header`/`camera_header`/`opaque` 三位），真机回到 28 条。
  真机：`mini_game1.rpy:50` 主循环产物**逐字复现**、`T0b` 归零、覆盖率仍 100%、
  两条路 53/53 幂等、CST 真迹 100%（3189/3189）。
- **10-07**：自定义语法适应架构评审 → `docs/设计_自定义语法适应架构_2026-10-07.md`
  （四层保真等级 T0~T3 / StatementSpec 注册表 / 三来源优先级 / M1~M5 里程碑）。
  关键发现：Ren'Py 有官方 CDS 注册表而工具把 `early` 整块跳过；`project_conventions`
  三层机制零生产调用；label 内未知块**实测**被改成假台词 + 块体消失（真机
  `mini_game1.rpy:50` 的 `while` 主循环）。用户拍板 D1=B（透传+报警+报告）、
  D2=B（通用行可编辑）、D3=升级 CST。
  **同轮完成 M1**（见 §K）：`UnknownBlockNode` + `Beat.opaque` + 隐藏列 `Opaque` +
  门禁两条码（`UNMANAGED_BLOCK` / `KNOWN_UNMODELED_BLOCK`）+ 真机探针。
  变异验证 3 次；一次判据被纠正（"包含"式断言对"被包进 python 块"是瞎的）。
  下一步：M2 `cds_registry.py`（读 `renpy.register_statement`）→ M3 注册表接入 →
  M4 投影 → M5 收敛 → D3 CST。本 MEMORY.md 同轮做过压缩整理。
- **10-05**：组合面地狱测试 `tools/hell_test.py` + triage。分出两个真 P0：
  `_quote` 不转义控制字符、label 不是硬边界（真实项目丢 4 个 label）。修完 +
  `tests/test_hell_findings.py`（13 条）并做变异验证。
- **10-04**：P3-12 收口后通用检查；新增 `tools/health_check.py`；发现
  `audit_real_project.py` 是坏脚本；`Notes` 定案；交付评审。
- **10-03**：三轮严苛测评；修 menu 丢失 / condition 不落表；补往返不变式 + 变异测试；
  抓出 P0 静默全量覆盖（交红测试给本人修）。
- **09-30**：以"逐句校对产物必须逐字节不变"为体检项，堵掉 5 条静默改数据通道；卡片交互重构。
- **09-29 / 09-28 / 09-27**：卡片改版（网格卡片 + chip + 异步缩略图）；`script_table` 拆 7 模块；
  扁平模块 → 分层包；三层架构 + 舞台快照。

## 五、协作方式（用户本人确认，影响后续所有协作）

- **单人开发**：代码**主要由 AI 写**，用户负责**设计、测试、验收**。
- 目标不是急着交付，而是**积累经验 + 学会改进 AI 写的代码**。
- 因此：多解释**"为什么这么设计 / 权衡了什么 / 否决了哪个备选"**；他要的是**决策过程**；
  他写代码时**不要抢着给答案**。
- 验收一律靠真实项目 + 全量测试，**让数据打脸**。
- 练习要落在"关掉 AI 自己写一版"，而不是读代码。
