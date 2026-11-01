# storygraph 契约速查（不许违反 + 为什么）

> 本文是 `.workbuddy/memory/MEMORY.md` 的**详细契约分册**。
> MEMORY.md 只留"状态速览 + 最关键铁律 + 指针"，**全部契约在此**。
> 每条约会写"为什么"，因为**只写"不许"而不知道理由，下一个人会把它当教条绕过**。
> 引用状态前先实测（跑 `tools/hell_*.py` / 对应 pytest），别信任何文档里的 ✅/❌。
> **附录 A**（已知边界）与 **附录 B**（关键不变量 1–88 全文）原在 README，2026-10-13 移到此。

---

## A. 真源与 IR
- **A1** `.rpy` 唯一真源；单源→多视图→受控 patch。`default/define/init python/transform/screen` 不进 Beats；不归己的行逐字节不动。顶层 `init python:` 走 `FileBlocks`；其余第三类只能走增量，无 `--source` 的全量导出被 `OPAQUE_LOST` 拒写。
- **A2** `core.statement` 唯一 IR；禁第二套 AST walk。IR 是语句流非树：`elif/else` 展开成 `not(前一条件)`；`menu` 渲染前重新归组复原。
- **A3** 保真后门两条（scope 不同）：`CodeBlock`（一拍内 `python:`）/`FileBlock`（顶层 `init python:`）。同一语句绝不两条通道各出一次。
- **A4** 整行注释必须保真：lexer 存**含缩进**原文（存 stripped 会双重去缩进→非法 ATL）。行尾注释**刻意**剥掉。
- **A5** **真源与优先级的层序**唯一真源=`core/authority.py`（`AUTHORITY` / `IDENTITY_ORDER` / `SOURCE_LAYER` / `DOMAINS`），决策全文 `docs/真源与优先级_设计决策_2026-10-31.md`。三条硬规矩：① 任何"谁覆盖谁"**查表**，不许按调用顺序/目录远近/文件格式临时排；② **规则只补空缺，覆盖必须精确**（`mapping-rule` 不许改 `declared`；要改就写 `mapping-exact`，且只对点名的这一个）；③ **投影不投票**（工具自己的产物 `*.generated.rpy` 等不进任何统计）。层序：engine 0 > mapping-exact 1 > declared 2 > mapping-rule 3 > registry 4 > inferred 5 > unknown 6。守卫 `tests/test_authority_layers.py`（同时比对本文档 §4.3 的层序表）。

## B. 写入与落盘（最高危）
- **B1** 落盘一律过 `generation_guard.write_generation`。`LABEL_COLLISION` 只在产物真落进 `<根>/game/` 时判。`HANDWRITTEN_TARGET`=目标存在且首行无生成标记→拒写，`force` 只越这条。让路判据=底稿==目标。三处调用方：`EditorSession.export` / `pipeline.compile.write_compiled` / `app.tables.apply_export`。
- **B2** 全量只许新建；改已有 `.rpy` 走 `story.patcher`。**"要么全对要么完全不动"**：按行身份配对，缩进只来自原文，替换行必须来自真实产物，失败 `ok=False` 且 **text 不可用**。身份判据坑：无标题菜单占位符按 `menu` 行认；选项行文案正是用户改的那位按 `_OPTION_ROW_RE` 认。夹具必须不干净。结构补空拍不是卡片。
- **B4** CLI 也能增量：`table xlsx2rpy`/`compile` 加 `--source`→`merge_incrementally`。补丁没配上又丢顶层语句→`OPAQUE_LOST` 拒写。
- **B5** 行号新鲜时信行号：`EditorSession.load` 记指纹（size+mtime_ns+sha1[:16]），export 核对磁盘==载入那份→`patch_export(trust_ranges=True)`。表格路 `recover_source_ranges` 的猜值**绝不许传**（"整体下移 1 行 259 行被改写"事故入口）。可信分支也要报配不上。
- **B6** 校验与渲染器必须同源（`patcher._written_speaker` 走 `Audit.character`）。
- **B7** Ren'Py 关键字优先于说话人（P0）：`voice/extend/menu"标题":` 长相同 `lyj"台词"`。判据 `_VERB_RE` 收 `voice`；`_line_key`/`_head_of` 先认动词；`_KEYWORD_SPEAKERS` 兜 `extend/menu`。
- **B8** "没落地"判据容得下"只是写法不同"：`_head_of`→`_unescaped`→`_loose`→`_literals`→`_body_lines`。

## C. Stage / 演出投影
- **C1** `Stage`↔结构化列互为投影，单向+幂等+只输出一次。结构化字段唯一真源，Stage 只承载表达不了的。`hydrate` 幂等；中文化按语义判（`_stage_op_covered` 别靠字符串相等）；重投影用 `stage_only_ops()` 留住 `[停顿] 1.0`。
- **C2** `beat.at` 是整拍兜底位置：只有所有立绘位置一致才能同步它。
- **C3** `compose_stage` 必须与 `_stage_lines` 同源；顺序列补齐规矩只一处=`Beat.stage_verbs`。`sync_stage_order` 只追加；指纹存归一化值；派生面板保存后必须重算。
- **C4** 裸词位置归一：`角色 表情 左` 的"左"是位置不是表情。中文名先 `actor_image_tag` 归一成英文 tag 查表；查图按 Tag 收窄。
- **C5** 卡片往返不许改 `beat.show` 一个字节。多属性立绘最阴（归一+`attr_for` 子串兜底会把属性合并成重复→图像名被改写）。判据 `normalize_expression_words`：归一升级档不许两词指向同一条属性。
- **C6** `kind` 是说话人单向投影：填说话人不自动降级 `say`→`narration`。`menu/menu_option/extend` 类型不动。
- **C7** 表情/位置候选与显示=项目真实名字（项目词表声明优先）。内置词表只是输入别名。候选项目名排最前不许被 limit 截断；当前值必须在候选里。

## D. 资产
- **D0** 资源表"没有就当场建"：`ensure_asset_tables()` 幂等；引擎转发传 `with_scripts=False`。
- **D0c** 表盘对账唯一真源 `compare_registry_to_disk()`（只读）`{disk,table,unregistered,missing,stale_paths,outside_game}`。`outside_game` 最值钱。音频目录按目录名任意深度认（含 `sfx`/`se`）。
- **D0d** 打开项目=自动对齐 `sync_asset_tables()`（`*.bak.xlsx` 备份；已有行不动）。判重按解析后真实文件。删行/改已有字段仍要用户明确动作。
- **D0e** 归类只认"目录段"：词元精确+少量词尾+最近目录优先+目录压过文件名+`images/<tag>/x.png`；`other` 不编 tag。`scan_assets` 不许第二套 tag 逻辑。跳过 `tmp/build/.git/__pycache__/saves`。
- **D0g/D0j** 缺文件行不可用：侧车 `_autoscan.json` 记工具加过的 Path→没了就删；人工/占位不动。后收紧：缺文件直接删（写前备份），例外=人工占位（Notes/Doc 有 placeholder/TODO/WIP）。删不掉（Excel 锁）退回 `registry.unusable`。
- **D0f** 音频行"作者写法优先"：配对永不改写音频行；按 (动词,通道) 条数容忍校验。
- **D0h** "读不到资源表"给真原因：`is_real_table_file()` 排除 `.bak`/`~$`/`.broken-`；解析失败改名留档 `*.broken-<ts>.xlsx`（不删）→重建（不许整份重写已有表）。
- **D0i** 打开项目"一次遍历+字典查找"（`FileWalkIndex`）；整条路共用一份索引（`walk=`）；UI 复用 `session.last_reconcile()`。不许"同名文件兜底"。
- **D0k** 📦资源体检 `asset_health_report()`+`app/asset_report.format_asset_health()`（纯函数结论第一行）。占位行不算缺失。
- **D0l/D0m/D0n** 自动对齐四步顺序（删缺失→删指向嵌套副本→改指→合并重复→补登）；**嵌套副本绝不能当项目资源扫**；`append_rows_to_table` 遇 ID 撞车但文件不同另起 ID，不许静默跳过。
- **D0o/D0q/D0r/D0t** 合成立绘：`preferred_image_attrs` 身体件垫底；`layered_model` 通用模型+条件求值（认不出→None）；`AlphaMask` 必须真应用；空姿势改回普通必须 `hide`（只报告，或清单声明 `hide_before_pose_switch` 才补）。`lookup_image_id` 给不到退"这个 tag 第一张"**绝不跨角色**。
- **D0p** 手输拍号立刻跳：`setKeyboardTracking(False)`+`valueChanged`+`editingFinished`，`_on_jump` 幂等。
- **D0b** 缩略图预热尺寸必须与界面常量一致（缓存键含 `WxH`）。
- **D1** 引用判定唯一真源=`asset_registry.resolve_references`（循环里必须传 `AssetIndex`）。
- **D2** 词表真源=`core.vocabulary`；补全打分真源=`assets.asset_completion.score_candidates`。
- **D3** transform 的 `ATL`/`Params`/`Doc` 以脚本为权威（`update_keys=`）；人写 `Category`/`Notes` 不动。
- **D4** 注册表图片 Path 两套基准：扫描行相对项目根，脚本 `image` 声明行相对 `game/`。
- **D5/D6/D7** 缺资源→`# TODO` + 建议；校验分级（有表查不到→错误；没建表→警告宁少报）；编译路径不校验。
- **D8** **"这张图是谁"的判定顺序**=`core.authority.IDENTITY_ORDER`：`mapping-exact`（images 表点名）→ `declared`（`.rpy` 里的字面量）→ `mapping-rule`（glob）→ `registry`（Tag 列）→ `inferred`（路径/stem）→ `unknown`。**glob 规则不许改项目声明**（2026-10-31 实测：一条 `game/**` 能把项目自定义图像名整片改掉）。`Identity.source` 的每个取值必须在 `core.authority.SOURCE_LAYER` 登记（不登记就红）；声明过的行走 `source='declared'`。
- **D9** **代号/编号的统计只数真源**：`assets/sprite_codes._script_texts()` 复用 `parsing.discover.DERIVED_SUFFIXES`/`SKIP_DIR_PARTS`，`*.generated.rpy` / `*.blocked.rpy` / `*.bak.rpy` / `tl/` 不进证据。实测：副本让同一句 `show` 数两遍，`xing` 的 B 号服装 1 票→2 票，「证据太弱留空」静默变成「B」（两个近乎克隆项目给出不同代号表）。守卫 `test_sprite_codes`。

## E. 性能 / 缩略图
- **E1** 图片路径基准唯一真源=`story.excel_export.resolve_thumb_source`。
- **E2** 缩略图缓存=两级分片+有界自清（扫描预算 2s / 每次删除上限 3000 / 一进程只排一次）。
- **E3** 内存缓存按**字节**（`CACHE_BYTES_LIMIT=64MB`）。
- **E4** 赢面四条：绝不重复解码、并行、只解看得见、大图 `reduce()`(BOX)。Qt `setScaledSize` 更慢→主路留 PIL。
- **E5** 缩略图可切暗底（选择器与卡片同步同一偏好）；偏好存储留注入点。

## F. Excel / 表格
- **F1** 安全写单元格唯一实现=`excel.cells`。
- **F2** 列顺序唯一真源=`story/beat_fields.py` 的 `FieldSpec` 表（2026-10-17 收敛）。`BEAT_FIELDS`/`BEAT_HEADERS`/`HIDDEN_BEAT_COLUMNS`/`SINGLE_LINE_BEAT_FIELDS`/`SNAPSHOT_FIELDS`/`FINGERPRINT_FIELDS`/`schema.py` 全由它派生；新增字段=在表里加一行（标好 excel/collab/snapshot/fingerprint）+ 同步 `to_row`/`from_row` 的编解码。守卫 `tests/test_beat_field_contract.py`：Beat 上多一个字段没登记就红。
- **F3** `Stage`/`BGM`/`SFX` 格内是 DSL：方括号原生写法直通。
- **F4/F5/F6** `import_beats_from_excel` 一律 hydrate；`LineId` 是协作主键；`ContentHash` 每次重算；`dsl.normalize_text` 只归一分隔符绝不动 `，（）`。
- **F7** 单行列单元格不许有换行（P0）：`SINGLE_LINE_BEAT_FIELDS`+`sanitize_single_line_cell` 读写两侧都过。多行列（台词/注释/Stage/Code/FileBlocks）刻意豁免。守卫 `test_cell_control_chars.py`。
- **F8** **章节级顶层块**（`init python`/`default`/`define`…）真源=`story.chapter.ChapterDocument.file_blocks`；拍上的只是传输副本。删/插/恢复/撤销结构都过 `_sync_chapter()`（幂等）。守卫 `test_chapter_document.py`（删第一拍不许丢 `init python`）。
   **整章结构树**（2026-10-18）：`ChapterDocument.tree`（`story.chapter_tree`）把场景→if/循环/菜单/选项体→拍建成真源；`beats` 是 `tree.flatten()` 的投影。增删移拍走树：块头带块体时默认**拒绝**，必须 `cascade=True` 才删整块；删完 `undo_structural()` 能按原 `nest_path` 放回。守卫 `test_chapter_tree.py`。
- **F9** **排序权威=`Seq`**（P0-3）：Excel 导入按 Seq 重排（只排正号、缺号行留在原位、重复不猜），并在第一拍留 `runtime_notes` → validate 报 W203。渲染/导出仍按列表顺序；要改剧情顺序请改「序号」列。守卫 `test_chapter_document.py::TestExcelImportUsesSeq`。

## G. UI
- **G1** 界面只从 `ui.tokens` 取色值/字号/间距；缩略图一律 `ui.thumb_loader`（禁 UI 线程同步解码）。
- **G2** 只读 tkinter `Text` 静默吞程序化写入→要灰显别用 `state='disabled'`。
- **G3/G4** 翻页纯导航（只提交当前拍）；辅助功能不许改用户已有数据。
- **G5/G6** 预览贴图回调只替换那一块；落盘时机界面说清。
- **G7** 写路径前先认路径（不是 `str`/`Path` 就 `ok=False`）；开关型参数 keyword-only。守卫 `test_control_flow_target.py`。

## H. 架构 / 依赖 / 环境
- **H1** 依赖单向 `core ← parsing/excel ← assets/story ← pipeline ← ui`；`story` 不许顶层 import `pipeline`。守卫 `test_package_layering.py`。
- **H2** 改中文 `.py` 只能用编辑工具或 Python 脚本（PowerShell 会 GBK 往返毁文件）。
- **H3** Git Bash heredoc 会做 MSYS 路径转换；探针脚本一律用编辑工具写文件。
- **H4** **含中文/转义的复现别用 `python -c`** —— 经 Bash 传参**会改坏样本**。一律写 `.py`。

## I. 渲染输出硬约束
- **I1** `_quote` 必须转义 `\\` `"` 和 `\n`/`\r`/`\t`。守卫 `test_hell_findings.py::TestQuoteEscapesControlChars`。
- **I2** label 是硬边界：换 label 先 `flush_stage_only()`；配套 `_fill_empty_labels`。
- **I3** 不许凭空造 label（纯定义文件产物不许出现 `label start:`）。

## K. 自定义块（M1）
- **K1** 未知块语句一律走 `Beat.opaque`（走 raw/`CodeBlock` 会推断补 `$`/包 `python:`→改剧情）。
- **K2** 台词不许带块：`mycue "闪电" intensity=3:` 与 `lyj"台词"` 长相同；判据子块有非注释内容→`_NO_MATCH` 交未建模兜底。
- **K2b** 台词**只到引号为止**（2026-10-18，P0）：引号串之后只允许空白 / 行尾注释 / 四种合法 say 子句（`(kwargs)` `with` `id` `nointeract`，见 `_say_clause`）。别的都说明"这不是台词"→ `_NO_MATCH` → 未建模（逐字保真）+ 若声明过则认领成 `spec_name`。
  - 从前的伤口：`_RE_SAY` 只 `match()` 到引号，`styled "红色" "#ff0000" 32` → 颜色/字号**静默消失**；`e "嗨" with dissolve` 同理。金标 `test_parse_golden` 当时把这堆垃圾一起冻住了。
  - 合法子句**跟着走**：`SayNode.tail` → `Statement.payload['tail']`（非空才写，否则全项目 say 的 content_hash 都会变）→ `Beat.say_tail`（隐藏列 `SayTail`，追加在 `CameraHeader` 之后）→ 渲染拼在引号后面。真机 `script_pomo.rpy` 的 `jc "…" (no_skip=True)` 因此才第一次逐字节往返。
- **K3** 取块按原文物理行（`_raw_block_lines`）；行号对不上退回 AST 重建；`parse()` 开头清 `_src_cache`。
- **K4** 门禁两码都 warn 不拦导出：`UNMANAGED_BLOCK`/`KNOWN_UNMODELED_BLOCK`；分类真源=`core.vocabulary.is_known_flow_block()`。
- **K5** 门禁结论**逐条**印（`BuildResult.summary()`）；落盘三处调用方都要交 `beats`。
- **K6** 判据必须**整段相等**+禁 `$ `/`python:`（"行在产物里"太松）。守卫 `test_unknown_block_preserved.py`。

## L. 自定义语句管理等级（M2/M3/M5）
- **L1** 三来源优先级唯一真源=`story/statement_registry.py`：`rules.json`（declared，覆盖）> `register_statement`（宿主）> 形状识别（不做）。
- **L2** 只认字面量，认不出跳过（`name=`/第一个位置参数）。⚠ 猜一个名字比说不知道坏得多。
- **L3** `SpecName`（隐藏单行）=被谁声明过；`Opaque` 存原文。⚠ 叶形态自定义语句也必须走 `opaque`。
- **L4** 标注后置（`annotate_spec_statements`，beat_build 出拍前）；项目根从 `statement.source` 反推并按根缓存。
- **L5** 门禁三桶 `block_buckets`：declared/custom/standard。⚠ "已内置建模块不进任何桶"每加一种块类型都要跟着加（`while/for`、`camera` 各漏过一次）。
- **L5b** "项目根"严格：有 `game/` 或有 `storygraph.rules.json`，否则 `''`。
- **L6** 词表唯一真源=`core/vocabulary`（5 张表）。⚠ 不许并成一个集合（动词表必须排除 `if/elif/else/while/for`）。
- **L7** 「每参数一列+原生下拉」唯一真源=`story/spec_args.py`。动态列名=`语句名·参数名`（位置参数多 `@下标`）。标准形状自声明（`camera`/`with`/`pause`/`window`）。⚠ 叶语句走**代码列**且必须同步 `ordered`；⚠ 值没变头行逐字节原样；⚠ 引号里 `a=b` 不是参数。
- **Lb** `camera` 是流程块（头行结构化+块体逐字 T0b）；`coverage_of_statements` 须显式认 `st.kind=='camera'`。

## M. 元模型 + CST
- **M0** 等级 `T0/T0b/T1a/T1b/T2/T3`；`T0b` 不许并进 `T0`；只读=`READONLY_LEVELS={T2,T3}`。
- **M1** 元模型唯一真源=`core/capability.py`（State/Provenance/Transition/Coverage/Escape）；判定顺序即优先级。
- **M2** 三条硬不变量（逐条有测试）：降级不改字节/精化不改字节/T0 不降级。⚠ 判据必须同时断言等级真的动了。
- **M3** T1 编辑面=`SpecHead`+`Opaque`；"没改就不动字节"靠 `emit_opaque` 比对头行（必须 `strip()`）。
- **M4** 候选不许猜：`spec_arg_choices()` 没声明返回空。
- **M5** CST 唯一真源=`core/cst.py`（`Span`+`raw_text`+`Trivia` 动态属性，不进相等/哈希）。Span 归属：到下一条语句前一行或第一个顶格行。⚠ 一次 analyze 只读一次盘。
- **M6** 不许 `except...:pass`（`test_code_health.py` 抓）；一律 `safeguard.swallow(...)`。
- **M7** 循环块=`StatementKind.LOOP`+`LoopHeader`+作用域；头行结构化、块体进语句流→T0。⚠ 老形态永远要能读；结构位丢→显式 `# TODO`；`W113` 排除 `loop_header/camera_header/opaque`。
- **M8** `W120`(缺必需)/`W121`(值不在 choices)。⚠ 头行末尾 `:` 扫描参数前先剥。

## N. 往返幂等
- **N1** 守卫=`tools/audit_idempotence.py`：`t2==t1` 且 `t3==t2` 逐字节。单元判据 `test_roundtrip_idempotent.py`。
- **N2** 已修：生成标记重复/括号续行缩进无界/被注释整块推一层/块后顶格注释缩一层/相邻舞台指令翻序。

## J. 测试纪律
- **J1** 必须测往返不变式。**J2** 变异验证：改回旧实现测试必须变红。**J3** 判据写错比缺陷更常见→先怀疑判据（`tools/probe_*.py`）。**J4** 假阳性比漏报更贵。**J5** 含中文复现写 `.py`（见 H4）。**J6** 跑测试一律 `--basetemp=<全新目录>`。

---

## 四、工程口径（别改回去）
- **源文件读取吃 BOM**：`parsing.read_source_text()`（开头剥一个 `\ufeff`），4 处读盘点统一。
- **静态分析：非整数常量不施加约束** + 保留浮点值。铁律——**有取值能满足的条件绝不许判"不可能"**。
- **`check_variable_syntax` = 逐行 `ast.parse`**（只查语法不查语义）。
- **`cds_registry.find_scripts` 只看"扫描根之内"的目录段**（`relative_to(root)`）。
- **`pytest` 默认会炸**：不带 `--basetemp` 时 `addopts` 指向仓库内 `.pip-tmp/pytest`，复用即撞沙箱 → **~1589 ERROR**。（是否改配置 → D6）
- **golden 重基线**：`tests/snapshots/editor_session.json`；改行为后 `STORYGRAPH_UPDATE_SNAPSHOTS=1` 跑一次，只改有意的那几条。

## 五、纯文字导入 · 契约（2026-10-10）
判据：`test_txt_import{,_hell,_golden}.py` + `test_character_aliases.py` + `test_char_decisions.py`。
- **T1 归一化只能"找候选"，不许单独定论。** 同层大小写撞车→该键进 `_shadowed`→精确写法各归各家；不规范写法返回 `Ambiguity`（产物保留原文+报出来）。**跨层**撞车（别名撞显示名）按"**强层赢**"+记 `Conflict`（显示名>别名）。
- **T2 未映射 token = 产物里的未定义变量 = Ren'Py 运行期必崩。** 每个未映射 token 必须额外报一条"会因未定义变量崩"。口径（用户拍板）：**保留原写法 + 告警**，不自动注入 `define`。
- **T3 说话人形状判据**：只认"归一命中"或"角色表非空 + `^[A-Z][A-Z0-9]{0,7}$`"。不含下划线、≤8 字符。取舍：认 `OK/USA`（进未映射清单，可修）优于当旁白。
- **T4 整段 `.rpy` 被当纯文字**：≥3 行命中代码形状 → `looks_like_script` + 告警（只提示不改产物）。
- **T5 读文本先看 BOM。** `utf-8 → gbk` 链会把 UTF-16-LE 的 `J\x00C\x00` 解成 GBK → 整章乱码且零告警。BOM 优先（UTF-16/32）→ utf-8 解出 NUL 视为失败 → 全失败抛 `UnicodeDecodeError`。
- **T6 候选不许被单字符键污染**：单字符键不参与前缀命中。
- **T7 改这条路的判据 = "产物一字未动 + 突变验证"**（`tools/probe_txt_import_no_product_change.py` / `mut_txt_case_collision.py`）。
- **C9/L2 "显示名对得上就是同一个人"一直生效**：txt 里写 `妈妈"…"`/`「蒋忱」"…"` → 自动映射 `mm`/`jc`，**不进弹窗**。⚠ 与"`MOM`↔`妈妈` 算不出来"是**两件事**（后者是英文字母缩写，只能靠人点）。
- **C13 `.docx` 用标准库** `zipfile`+`xml.etree`（零新依赖）；段落→一行、`<w:br/>`→一行、表格每格成行、**超链接与文本框里的文字也要抽**；**不做语义猜测**。`.docx` 与 `.txt` 同一条链路（判据：同文字两路产物逐字节相同）。
- **C11 S3 入口**：`EditorSession.import_plain_text` + 工具栏「📝 导入纯文字章节」。**两遍**：规划（不写盘）→问角色→决定回表→重跑→过门禁落盘→导图。**未确认角色 → 一个字节都不写**。UI 那一步必须传**窗口自己的** `import_chapter_files`。

## 六、自定义语句入口 · 契约（2026-10-10）
- **发现** `statement_registry.discover_undeclared(root)`（纯、只读）。⚠ 判据照抄 `coverage_of_statements`：`raw_kind` 在 `Statement.payload` 里（**不是 beat 属性**）。
- **V2-1 闸门**：`_is_builtin_head()`（词表已知 ∪ 容器头 ∪ `LEAF_ARG_HEADS`(`with/pause/window`)）→ **不列**；语句头须合法标识符；`_looks_like_dialogue()` **先问角色表**（`l oh "3"` 是台词不是"你写错了"）；`declared_builtins()` 指认"内置语句不用声明"；`remove_statement()` **先备份**再移除。真项目"待认识"列表 **3 → 0**。
- **表单纯函数** `authoring/learn_statement.build_declaration()`：`intensity=3`→按 `intensity` 处理并报出；中文/非法参数名→**丢掉并报**；重复项只留一次；没勾"带块"不写 `block` 键。
- ⚠ **两个 registry 不是一回事**：`session.registry()`→`AssetRegistry`（资产），语句注册表是 `StatementRegistry.for_project(root)`。混用 → Qt 槽吞异常 → "点了毫无反应"。
- **候选分栏**（`authoring/spec_groups.py`，`rules.json` 的 `attr_groups`；**界面上不叫"词组"**）：逐字 / **整段词序列**（`swim_suit_glad` 归"服装"）/ **前缀通配 `swim_*`** / 兜底组。⚠ **不做子串包含**（`unhappy` 不含 `happy`）。⚠ **没声明 = 完全平铺**（通用性底线）。⚠ 立绘属性的分栏来自**引擎**（`layeredimage` 的 group），声明只当改名层。
- **服装三条正解**：**A（推荐）** 服装=立绘**属性**（图登记进 `images.xlsx`，`Tag=角色`/`Attributes=swim_suit` → 与"表情"共用下拉池；想分开显示用 `attr_groups`）；**A+** 卡片上**一维一格**（一个引擎分组一个 chip，2026-10-12，见 §八）；**B** `swim_suit lyj "泳装" intensity=3` 是自定义指令 → 声明参数 → 卡片一组控件 + 表格动态列（`swim_suit·pose`）。⚠ `fold_spec_arg_columns` 的键是**整列名**。
- ⚠ **面板少一个转发方法 = `AttributeError` → `QMessageBox` 模态 → 测试挂死 / 用户看到"卡住"**。守卫 `test_every_panel_method_the_window_calls_really_exists`。
- ⚠ **构造期 `_refresh_preview` 早于控件创建** → 一律 `getattr` 守卫 + 建好后重刷。

### 六·补：两条**界面红线**（2026-10-11，都是用户实测踩出来的）
1. **界面必须能干活，不能只"看着有"** —— 判据要**真的 `action.trigger()` 一次**，
   并断言控制台不许出现"失败/没写/Traceback"。**"存在 ≠ 能跑"**
   （用户原话："按钮无法点击！点击了也没有反应！"）。
2. **能点就必须有反应**：卡死了就 `setEnabled(False)` + 灰化 + tooltip 写清原因，
   **绝不允许"点下去什么都不发生"**。
3. **用词说人话，一个东西只有一个名字**（2026-10-18 第六版重定，**取代**原来那份
   "动作名 / 要填什么"的说法）。术语表（界面上一律用右列，代码里可以保留左列）：

   | 代码 | 界面 |
   |---|---|
   | statement | **语句** |
   | head | **开头词** |
   | slot / param | **格**（第 N 格） |
   | column | **列名**（"表格里这一列叫什么"） |
   | choices | **候选值** |
   | attr_groups | **候选分栏**（键名 / 显示名 / 候选值） |
   | manifest | **项目清单**（项目根的 `storygraph.rules.json`） |

   **界面上不许再出现**：`动作名` / `写法` / `模块`（指语句时）/ `词组` /
   `实现者词汇`（`describe`/`T1`/`两把锁`）/ `写进 storygraph.rules.json`。
   判据：`tests/test_custom_statement_ux6.py` 里那两条"术语"判据（扫控件文字）。
   ⚠ **Qt 的 QLabel/QCheckBox 不渲染 Markdown** —— 文案里写 `**…**` 会原样显示成星号。
   （2026-10-11 补了**两条自动判据**防再犯：控件级扫弹窗里所有控件的文字；
   源码级扫 `dialog_learn.py` 里**非 docstring** 的字符串字面量。
   两层都要 —— 控件级**会漏**"暂时不显示"的文案。）

### 六·补2：行为级验证（`test`）的**操作红线**（2026-10-11 真机实测）
> 细节与证据在 `docs/archive/终极方案_引擎事实与三层分工_2026-10-10.md` **§十二**。

1. **`test` 跑完不会自己退出** —— 拿 `subprocess.run(timeout=)` 等它 = 误判成"卡住"。
   正确做法：**边读输出边看 `[rpytest] Status:`**，见到就收进程。
2. **`test` 需要显示后端**（`uses_display=True`）：无头会真卡住 ⇒ **不进 CI 默认路径**。
3. **要拷 `gui/` 与 `fonts/`**（起界面就崩）。⚠ 这两处**必须同一个函数里做**
   （`behavior_copy`）—— 漏一处就永远"没证明成"（我漏过 `gui/`，真机抓到）。
4. **判定是"两把锁"**：① 引擎报 `Status: PASSED`；② **证据文件真的存在**。
   ⚠⚠ `assert eval` **不是硬断言**（假命题也 PASSED、加 timeout 也没牙）——
   只看 `Status` 就是假绿。**没有可观察证据 → `verified=None`（没证明成），
   绝不退回"只信 Status"。**
5. `run` 收**可调用的 Python 表达式**（`run Jump("label")`）；
   `assert` 要**先挂等待、再触发**（写在 `run` 之后会错过那一刻）。
6. **Windows 路径的反斜杠**会毁掉 `assert eval` 里的字符串 → 生成探针时统一转前斜杠。

### 六·补3：⭐ 程序侧的**成本最低**契约（2026-10-11 用户定的框架）
> 总纲："工具的价值不在于'不依赖程序'，而在于**'依赖程序时成本最低'**。"
> 详情在 `docs/archive/终极方案_引擎事实与三层分工_2026-10-10.md` **§十三**。

程序在**项目根**或 `game/` 放一个 `storygraph.host.py`，按需定义：

```python
def declarations():                    # 额外声明（省掉手写 rules.json）
    return [{'name': 'weather', 'label': '天气'}]

def validate_statement(head, text):    # True / (False, 原因) / None(我不管)
    ...
```

三条不许破的约束：
1. **程序不给就不发生**（没有文件 → 行为与以前一模一样）；
2. **程序写的代码不许弄坏工具**（异常只记进 `notes`，工具照常干活）；
3. **`None`（程序不管）≠ 程序说通过** —— 分不开就是假绿。
4. **默认不执行项目代码**（2026-10-17 补，`exec` 不是沙箱）：这是任意代码
   执行入口。授权三选一：调用参数 `trust=True` / 环境变量
   `STORYGRAPH_TRUST_HOST_HOOKS=1` / 项目 `storygraph.rules.json` 写
   `{"host_hooks": {"trust": true}}`。未授权 → `available=false, denied=true`
   + 说清怎么授权（不是"程序没提供"）。守卫 `tests/test_host_hook_trust.py`。
5. **授权后也在子进程里跑**（2026-10-18 补）：`host_worker.py` 用
   `python -I` 起独立进程；加载/`declarations`/`validate` 各有超时（20/20/10s），
   单行响应上限 8 MiB、stderr 只留最后 64 KiB。超时/崩溃/洪水输出只杀子进程，
   工具拿到 `None` + `notes`，**绝不假装通过**。守卫 `test_host_hook_isolation.py`。
   ⚠ 这是稳定性隔离，**不是安全沙箱**（子进程仍有当前用户的文件/网络权限）。

⚠ **接线要求**：工具的形状判据**认不出的行**（`mycue "打雷"` 与台词同形），
**也必须先问程序的验证函数**（否则那函数永远问不到，等于白写）。

⚠ **一条边界（信息不足，不是缺陷）**：`mycue "闪电"` 与「角色 mycue 说『闪电』」
字面同形 —— 扫描器**不猜**；判据是引擎的 `expected statement` 或程序的验证函数。

### 六·补4：两套项目（改扫描器前先想到它们）
· **地狱项目**：`tools/make_hell_project.py`（借真规模 + 13 个边界攻击）→
  `tools/attack_hell_project.py`（真机探照灯）+ `tests/test_hell_project_attack.py`（CI 常驻）；
· **友好项目**：`tests/test_friendly_project.py`（程序写一行 → 工具自动跟上）。
⚠ 造地狱项目**必须一起拷 `asset_tables/`（人物表）**，否则会报出假缺陷
（我踩过：把台词的说话人当成"未知写法"）。**地狱项目造假 → 攻击结论也假。**

### 六·补5：⭐ **工具认识 ≠ 引擎认识**（2026-10-11，最容易教错人的一条）

- `storygraph.rules.json` 里的声明只让**我们的工具**认识这个写法；
- **引擎**要认识它，项目里必须有 `renpy.register_statement`；
- ⚠⚠ 而 **`init python` 里注册赶不上解析期** —— 引擎源码 `script.py:447-452`：
  **一组解析出错，后面所有组都不解析**，`init` 属于后面的组。
  ⇒ 这就是"声明了却跑不起来"的常见原因，**界面必须当面说清**。

## 六b、⭐ 候选值的正解：Ren'Py 自带属性注册表（2026-10-10 实测）
**别让人手填候选** —— 引擎自己会枚举。真机 `sr_general` **无魔改引擎、无 CDS**；
`show lan clothes2 happy at i21` 是**标准**写法，`clothes2` 是 `layeredimage` 声明的**属性**。
| API | 给什么 |
|---|---|
| `renpy.list_images()` / `get_available_image_tags()` | 全部图像 / tag |
| **`renpy.get_ordered_image_attributes(tag, attributes=(), sort=None)`** | **按展示顺序排好、且与给定属性兼容的属性列表** = 候选值的真源 |
| `renpy.has_image(name, exact=False)` | 合法性判定（oracle） |
| `renpy.get_registered_image(tag).attributes` | 每个属性带 `.group`/`.attribute` → **分组名也在引擎里** |
| `renpy.sh lint --error-code` | 官方静态检查，退出码 1（文档明说 useful for CI） |
- **四级阶梯**（选来源的顺序）：① 引擎原生可枚举（图片/属性/tag/变换/角色/标签，零配合、不漂移）
  → ② CDS 注册 → ③ 引擎补丁即契约 / 就近声明 → ④ 归纳+`lint` 当裁判+人确认（只当发现与报警，**永不当真源**）。
- **优先级写死**：**形状→引擎；语义（中文名/分组/例子）→人；内容→`.rpy`**。**对不上 = 报错，不是"谁赢"。**
- 落地建议：一次性 dump 脚本 → `storygraph.engine_facts.json`（可 diff、进版本库）。**全案见 `docs/archive/设计_契约前提的复核与修正_2026-10-10.md`**。

## 六c、⭐⭐⭐ 引擎 oracle 分工（2026-10-10 **真机实测 `sr_general`**，SDK 8.5.3）
> 命令：`SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy renpy.py <基于> lint --error-code`
> ⚠ **`lint` 默认退出码是 0；必须加 `--error-code` 才会在有 finding 时退 1**（否则 CI 永远绿）。

| 要问什么 | 谁来答 | 真机实测证据 |
|---|---|---|
| **语句/语法**成立吗 | **`lint`** | `weather 雨 0.5` → `expected statement.` 退 1 |
| **图像/属性引用**合法吗 | **`lint`（也答！）** | `'lan 完全不存在' is not an image.` 退 1 |
| **资源文件**存在吗 | **`lint`（也答！）** | `'bgm/lyj01.mp3' is not loadable` |
| 组合**兼容**吗 | `get_ordered_image_attributes(tag, 已选)` | `ling` 30 →（给 `sigh`）3 |
| 属性词汇 + **分组** | `get_registered_image(tag).attributes` → `.group`/`.attribute` | `ling`: `outfit:[base_swimsuit,base_arms_akimbo]` / `expressions:[…]` |
| 变换 / 界面清单 | **`--json-dump`** | **155 transform** / 87 screen / 386 define |
| label 清单 | `renpy.get_all_labels()` | **106**（⚠ `--json-dump` 的 `location.label` = **0**） |
| ~~`renpy.has_image()`~~ | ❌ **不是合法性判据** | 对**不存在**的属性组合也返回 `True` |

⚠⚠ **操作铁律：`lint` 没报 ≠ 没问题。必须先确认「没有 parse error」。**
一个语法错误（`expected statement.`）会让**后面全部语义检查（图像/属性/资源）根本不跑**
—— 我上一轮据此误判成"lint 不查属性"，**已被实测推翻**。
⚠ `--json-dump` **需脚本无错误**（有错时 `location` 全空）；`location.label` 恒为 0。
⚠ **`get_available_image_tags()` 不是干净 tag 列表**（235 项里混着 `_hd_cut`/`afraid` 等）。
⭐ **"服装/表情分组"直接从引擎读**：`layeredimage` 的 group 名（`outfit` / `expressions`）——
人工 `attr_groups` 只应作为"改名/合并"的覆盖层。
⭐ 探针实现：**工具生成探针 `.rpy` → 注入项目副本 → 跑 `lint`（init 会执行）→ 收割 JSON**（零配合、不污染引擎）。
**全案：`docs/archive/设计_真机四缺口实测与四个激进想法_2026-10-10.md`**。

## 七、2026-10-10 顺带抓到的既有真缺陷（已修）
- **U1** `patcher._is_new_content_row` 用了签名里没有的 `old_lines` → `NameError`。已补参数 + 调用点补传。
- **U2** `layered_model._expand_node` 给 `_expand_composite_call` 传了不存在的 `idx` → 任何 `Composite(...)` 都 `NameError`。已删参数 + 顺带修两处静默丢图层；签名按 Ren'Py 8.5 官方文档核对。⚠ 真机 `sr_general` 的 `Composite` 0 次 → 属"换项目就炸"的既有炸弹。
- `tests/test_no_undefined_names.py` 是抓到它们的那张网 —— **别让它变红还放着不管**。

## 八、立绘卡"一维一格"· 契约（2026-10-12）
用户现场：`show ye base_handdown gratified`（服装 + 表情两个词）。

- **病根**：卡片只有**一个**表情格；当前值（两词）不在候选表（单词）里 →
  点 chip 走"固定当前值"那一支 → **点了没反应**；从下拉挑一个词 →
  `tag + 那个词` 重拼 → **服装被静默吃掉**（`show ye gratified`）。
- **语义层（纯函数，`app/beats.py`）**：
  · `attr_slot_rows(spec, symbols, groups, facts)` → 一维一行
    （`{group,label,value,display,options,other,extras}`）；
  · `apply_attr_slot(spec, group, value, …)` → **只换那一维的词**，其余原样；
  · ⚠ **可操作维度 < 2 → 返回 `[]`**（`show ye happy` 这类走老路，行为一字不变
    —— 通用性底线，与 `group_choices` 同规矩）；
  · ⚠ `extras`：不属于任何引擎组的词（`base_handdown_mask`/`_hd_cut`）**原样留着**。
- **分组来源**：引擎事实（`grouped_choices`）→ 没有就现读 `layeredimage`
  （`layered_model`）；`rules.json` 的 `attr_groups` **只当改名层**。
- **提示**：`silent_default_attrs(beat, only_groups=…)` —— 同组写了两个词时，
  说清**哪一个没生效**（`attrs_chosen` 才是"真正采纳"的依据，`attrs_used` 是输入回显）。
  ⚠ `only_groups` 为空**直接返回 `[]`**：没同组冲突的拍**一次磁盘都不摸**
  （`card_note` 先问 N3 再决定要不要读项目）。
- **模型缓存**：`story.layered_model.load_layer_model_cached`（**文件指纹**失效；
  真机建一次 0.28 s、卡片每翻一页都要问，所以缓存必须能失效）。`ui_qt` 那份已收敛到它。
- **判据**：`tests/test_sprite_slots.py`（卡片 9 条）+ `tests/test_engine_choices.py`
  的 `TestAttrSlotRows` / `TestApplyAttrSlot` / `TestSilentDefaultAttrs`。

### 八·补：弹窗里的按键归弹窗（2026-10-12，用户报"选一次就回车……按了没反应"）
- **现场**：`ChoicePickerDialog(parent=卡片)` 的 `parentWidget()` 是主窗口；
  `MainWindowQt._event_belongs_here` 那段"往上找 `parentWidget`"于是把**对话框
  以及它里面的筛选框**都认成"本窗口的控件树" → 主窗口的键盘过滤器把弹窗里的
  **回车**当成"下一拍 / 进卡片"吃掉 → 弹窗一声不吭。
- **修法**：`_belongs_to_this_input_tree()` —— 问 `w.window()` 是不是本窗口；
  是别人的窗口就**一个键都不许抢**（空格 / Ctrl+S / Esc / 回车一视同仁）。
- ⚠ **判定只能用 `windowType()`**：`windowFlags() & Qt.Popup` 对普通 `QDialog`
  **也返回真**（`Qt.WindowType` 的枚举值与 `Qt.WindowTypeMask` 不能按位混用），
  而组合框下拉的 `QFrame` 也必须继续算"本窗口的弹出件"（否则下拉里按方向键就翻拍）。
- ⚠ **判据必须真开弹窗、真发按键**：从前那 4 条把 `choose_from` 整个 monkeypatch 掉，
  **弹窗根本没开** —— 这个缺陷一辈子测不到。见
  `tests/test_choice_picker.py::TestDialogKeysAreNotStolen`（真主窗口 + 真 `exec()`）
  与 `TestInputTreeRule`（三条边界：子控件 / 挂在本窗口下的对话框 / 组合框下拉）。

## 九、自定义动作弹窗 v3 · 布局契约（2026-10-12）
用户现场："**前面那个选的一多呀……就把后面那个变量挤到窗口外去了！而且你这个窗口还不能全屏**"。
设计文档：`docs/archive/设计_自定义动作界面_v3_重做_2026-10-12.md`（含研究来源与逐条判据）。

- **四条硬规矩**（改这个弹窗前先读它）：
  1. **单列堆叠**：参数卡里一个字段一块（标签在上、控件在下），**不用多列网格**
     —— 实测 v2 的 6 列网格让一张空卡的最小宽 = **480 px**，v3 是 **188 px**；
  2. **会变长的东西必须折行**：候选值方块走 `param_cards.FlowLayout` / `_FlowRow`，
     `minimumSizeHint` = **最宽那一项的下限**（⚠ 不是 `QLayout.minimumSize()` ——
     那个含 `sizeHint` 宽，实测把右栏顶到 466 px）；
  3. **横向滚动 = `AsNeeded` + 把最小宽真的做小**：不要用 `AlwaysOff` 掩盖大最小宽
     （等于承诺"一定放得下"，实际是静默裁掉）；
  4. **动作条在滚动区外**（滚多远都点得到），只有中部内容滚。
- ⚠ **`QGroupBox` 的标题会顶宽整块**（实测：长标题 → 整块最小宽 **466 px**；
  空标题只要 60 px）。标题只写短名，**长解释放进框内的 `QLabel`**（它折行）。
- **窗口**：`WindowMinMaxButtonsHint | WindowSystemMenuHint` + `setSizeGripEnabled(True)`；
  最小 `760×480`（再小就是假承诺 —— 底部按钮条自己就要 454 px）；
  初始尺寸走 `_fit_to_screen()`（不超过屏幕可用区）。
- **判据**：`tests/test_custom_statement_entry.py` ⑦ 节（V1~V9），
  其中 V9 `test_no_horizontal_scrollbar_at_the_minimum_size` 是那句用户原话的总判据。

### 九·补：两个"读了却没用上"的真 bug（2026-10-12 用户追问挖出）
- **① 位置参数没进卡片格子**：声明里的 `pattern`（有序格子）**只在磁盘上**、
  没进 `RegisteredStatement` → "第几格 = 第几个位置参数"在读取侧丢失 →
  `swim_suit lyj "泳装" mood=happy` 在卡片编辑器里 `角色`/`服装` **两格是空的**。
  修：`pattern` 进注册表 + `spec_columns._declared_positionals()` 按格子顺序补
  `positional`（名字对上优先，对不上按 1:1 顺序兜底；`keyword`/`optional` 不占位）。
  ⚠ 判据 `test_positional_parameters_are_read_into_their_own_cells` +
  `test_editing_a_positional_cell_folds_back_into_the_head`（读→改→折回→渲染闭环）。
- **② 词组填了，参数卡上却是空下拉**：`_sync_groups_to_slots()` 在"词组还没 load
  进来"时就调过一次，load 之后再没人刷新 → 打开弹窗看到一个**空下拉**。
  修：load 之后补一次同步 + 词组表一变**自动**同步（`set_groups_changed`）；
  并且**下拉撤掉**，改成**卡头自动标注**（用户："就在原地，就在旁边标注"）。
- ⚠ 两条都属于同一类毛病：**后端实现了，界面没接线**。判据要**端到端**（真开窗口、
  真改控件、真看结果），只 mock 掉中间层是永远测不到的。

## 十、自定义语句弹窗 · 布局契约（v4 重写 → **v6 信息架构重做**，2026-10-18）
用户第三条退回意见（看完 v3 截图）：
> 这两个滚动条是何意味啊？？？还有你这个词组栏不也还是打勾下拉吗？全都再改！
> 不要再在原来的地方缝补了！整个删了重写！……你这不应该叫词组！你换个名字！

- **文件级重写**：`ui_qt/param_cards.py` **已删** →
  `ui_qt/param_editor.py`（`FieldCard`/`FieldList`/`ValueChips`）+
  `ui_qt/column_editor.py`（`ColumnCard`/`ColumnList`）+
  `ui_qt/flow.py`（`FlowLayout`/`FlowRow`，两处共用）；`dialog_learn.py` 重写。
- **一屏只有一条滚动区**（结构保证）：字段列表与分栏列表都是**纯 QWidget**
  （它们自己不再是 `QScrollArea`）。判据钉"同屏可见滚动区 ≤ 1 个"。
- **改名**：界面上一律 **候选分栏**（旧名"词组"彻底不用）：
  `键名`（程序用的键）/ `显示名`（编剧看到的字）/ `这一栏的候选值`（留空=兜底）。
- ⚠ **QWidget 当布局容器必须自己喂 `sizeHint`/`heightForWidth`**：
  `ValueChips` 第一版没喂 → `sizeHint()` 是 0×0 → **方块加进去了却一个都看不见**。
- ⚠ **别用 PowerShell `Set-Content -Encoding UTF8` 改源码**：它写 BOM（`U+FEFF`），
  Python 3 直接 `SyntaxError`（实测：21 failed + 18 errors）。要批处理就用
  `[System.IO.File]::WriteAllBytes` 且不带 BOM；最好直接用编辑工具改。
- **入口名统一成「🧩 自定义动作」**（工具栏 + 弹窗标题 + 文档三处一致，
  判据 `tests/test_usage_doc_matches_ui.py` 钉着）。

### 十·补：v6 —— **页签换成"列表 → 编辑"两页**（2026-10-18）
用户："运用UI设计和用户体验的角度重构一下自定义模块的UI界面，去除令人疑惑的表述，
保证策划/程序逻辑清晰明了，操作方便，层次合理……不再旧有基础上改。"

**旧四页签的病**：① 认识新动作 / ② 填一条声明 / ③ 候选分栏 / ④ 已有声明 ——
编号暗示"四步"，实际 ① 和 ② 是一件事的两半（选词 → 描述）、③ 是 ② 的一个属性、
④ 是管理；点 ① 的行会**自动跳** ②，编号更假。同一个东西还有四个名字
（动作 / 写法 / 语句 / 模块），主按钮直接把文件名写上去。

- **两页 + 两个项目级页**：`PAGE_LIST`（哪些还不认识 / 已经认识哪些）→
  `PAGE_FORM`（下钻编辑，左上角「← 返回列表」）；`PAGE_GROUPS`（候选分栏）与
  `PAGE_MANIFEST`（项目清单）从列表页底部进。`dlg.pages` 是 `QStackedWidget`，
  下标常量 `PAGE_*` 在 `dialog_learn` 顶部。
- **一处写盘**（P0）：保存由弹窗自己做（`_save_statement` / `_save_columns_now` /
  `_delete_current_statement`），`saved` 记账、主窗口只负责刷新与写日志。
  ⚠ 旧链路 `declare_statement(...)` 在 `window_graph_ops` 里调用时**漏传 `pattern`**
  —— 格子的类型/顺序/多选/可不填**全丢**，只有 `columns` 活下来。
  判据 `test_保存这条语句会把格子的类型和顺序一起写下去` 直接读磁盘上的清单。
- **类型下拉曾经是孤儿控件**（P0）：`FieldCard._kind` 从来**没被加进任何布局**，
  于是「固定的字 / 从候选值里选 / 可选段」在界面上**选不了**（判据一直用
  `add_param(kind=…)` 直接塞，所以从没红过）。现在它在卡内第一行，
  判据从**控件树**上守（`test_类型下拉必须真的在界面上`）。
- **"从候选值里选"就该看见候选值**：旧版还要再勾「编剧只能从下面这些里选」
  才露出输入框 —— 而那两件事不是一回事（候选池 vs 允不允许写池外的词）。
- **卡头带序号**（第 N 格）—— 试切报的"第 2 格"要能一眼对上。
- **离开未保存的编辑页会问一句**（保存 / 不保存 / 留在这里）；窗口没显示时不问
  （那时没有"人"能回答，模态框会让调用方永远卡住）。
- 判据：`tests/test_custom_statement_ux6.py`（一处写盘 / 层次 / 术语 / 滚动区）。

## 十一、会话记忆（`core/session_store.py` + `ui_qt/window_state.py`，2026-10-13）

用户："再给这个工具加个'记忆'功能，当第二次打开时自动读取之前打开过的那个项目，
如果项目内部有保存布局，一并读取。"

- **存哪儿**：`%LOCALAPPDATA%/StoryGraph/session.json`（`STORYGRAPH_SESSION_CONFIG`
  可覆盖）—— 与 `ai.json` / `crash.log` 同一处约定。
  ⚠ **工具自己的记忆不写进项目仓库**（否则进 git diff、多人协作互相打架；判据
  `test_it_does_not_write_into_the_project_repo` 扫项目目录证明"一个新文件都没加"）。
- **项目里那份**：`<项目根>/storygraph.session.json` —— `layout` 段**只读**，
  字段**覆盖**用户级那份（团队可以放一份布局一起评审）。这就是用户说的
  "项目内部有保存布局，一并读取"。
  **唯一的写入口**是 `remember_cards()`（2026-10-22，折叠状态"跟项目走"）：
  只改 `cards.folded` 里那一个键，读-改-写 + 原子替换，读不懂那份就**拒写**
  （那份文件人也会手改）。键是**项目内相对路径**，所以整目录搬走仍然对得上；
  `[]`（折过又都展开了）与"没有这个键"是两件事。
  落盘守卫里登记为独立写入点：`tests/test_single_write_entry.py`。
- **记什么**：上次项目 / 上次章节（绝对 + **相对项目根**，项目搬家也找得回来）/
  页号 / 拍号 / 两处分栏宽度 / 窗口几何 / 最近 8 个项目。
- **三条硬规矩**：读不回来当没有（启动路径上绝不抛）；项目目录不在了**不自动打开**
  （记录留着）；`STORYGRAPH_NO_SESSION=1` / 菜单勾选框能整趟关掉。
- ⚠ **只有命令行"明确给的"项目根才顶掉记忆**：`main_qt._guess_project_root`
  现在返回 `(root, script, source)`，`source` 区分 `ROOT_FROM_ARG`（命令行给的）
  与 `ROOT_FROM_CWD`（从当前工作目录猜的）。**猜出来的要让位** ——
  否则双击 exe（cwd 常常正是某个项目根）会让记忆永远用不上（实测抓到的 bug）。
- ⚠ **测试隔离是 autouse 夹具**（`tests/conftest.py::_no_real_session_memory`）：
  窗口在 `closeEvent` 落一次记忆，而测试会造几百个临时项目 ——
  没有这道闸，跑一次全量测试就会把你真实的最近项目表灌满 `tmp_path`。

## 十二、内置语句的参数格（2026-10-13）

用户："连 show 这种内置语句也能在卡片上长出参数格……本来就默认存在且有的，
那就保留，只改后面加进去的，加了什么，就长个什么参数，没必要全部都更新。"

- **合并而不是替换**：`statement_registry.arg_columns` 现在
  `merge_builtin_columns(builtin, declared)` —— 内置形状（`camera` 层名 /
  `with` 转场 / `pause` 秒数 / `window` 动作）**一列不动**，声明的同名列**就地改**、
  新名字**追加**。修之前是"有声明就整个换成声明的"（加一列顺手删几列，界面不提醒）。
- **`show` 现在真的能用**：`_HEAD_FIELDS` 加了 `'show'`（它的行住在 `Beat.show`，
  不在 `opaque`/`spec_head` 里 → 以前 `_beat_statement_head` 恒为空 → 一个格子都不长）。
  `Beat._HEAD_FIELDS` / `statement_head()` 同步认这个字段，并**把动作名补回去**
  （`'lan swim_suit happy'` → `'show lan swim_suit happy'`），否则格子长出来是**空的**。
- ⚠⚠ **折回的时机被挪到 `EditorSession.apply` 的最末尾**：
  动态列原来折在 `cards_to_show(cards)` / `hydrate_from_dsl` **之前**，
  于是立绘卡重建 `Beat.show` 时把它**盖回原样** ——
  "卡片上能改、保存成功、值没生效"。这是"单元对了、集成错了"的典型，
  所以判据必须走**真窗口**（`tests/test_builtin_statement_columns.py` 最后一条）。
- ⚠ **多值拍（`show a; show b` → `Beat.show = 'a; b'`）不给参数格**：
  一格对多行，怎么映射都是猜；按单行折会**静默改数据**
  （实测 `'lan swim_suit happy; ling uniform calm'` 改第一格后第二行的 `ling` 没了）。
  `Beat.statement_head()` 对这种拍返回空，`_beat_statement_head` 跟着返回空。
- ⚠ **`positional_tokens` 总是跳过第一个词**（当语句头）：所以折 `show` 时
  base 必须用 `statement_head()`（带 `show ` 前缀），不能用字段原文 ——
  用字段原文会跳掉真正的第一个参数，整排左移、少一个词。
- ⚠ **教训：`__pycache__` 会骗人**。改了源码后用 `python -c` 排查，
  可能加载的是**旧 `.pyc`**（实测：源码已改、脚本里行为照旧，
  白追了十几分钟）。排查这类"明明改了却不变"的问题，先
  `Remove-Item -Recurse storygraph/**/__pycache__`。

## 十三、候选 / 限死 / 多值（2026-10-13 用户第二次实测退回）

用户："这个仍然切不上！有一个巨大的 bug……" —— 底下是三件独立的事，全是真 bug：

- ⚠⚠ **"填了候选"不等于"限死"**（"切不上"的真凶）：`pattern` 的 `choice`
  只要候选非空就要求词必须在候选里 —— 于是用户贴了一串角色名之后，
  剧本里任何**没列进去**的角色都判切不上。现在加 `limit`（缺省 `False`）：
  **只有显式勾了"编剧只能从下面这些里选"才封闭**。存量声明没有这个键
  → 一律按"候选只是建议"（**不因为存量声明判人写错**）。
- ⚠⚠ **列名空着不等于这格要丢**：`learn_statement.build_declaration` 只认 `name`
  （旧形状的"剧本里怎么写"），而界面把列名放在 `label` → **整列被静默丢掉**
  （用户："我明明定义了三个参数……它反馈出来的只有一个参数"）。现在 `name` 空就用
  `label`；两个都空才丢，而且**必须报出第几格**。
- **`multi`：A 段可以有多个**（用户："A 是默认的，也就是可填可不填的……
  但是它不是一段，它允许有多个！"）。真机依据 `show lan happy sad`：
  同一格吃多个词。`pattern` 加 `multi`（缺省 `False`），`match_line` 里
  **贪心到底 + 回溯到 0 个词**（否则会抢走后面那格的词）。
- ⚠ **字段卡只剩一个名字**：`"剧本里怎么写"` **删掉**（用户："在编剧眼里……
  剧本就是'角色'……你让我填根本就不合理"）。现在只有
  `这一列叫什么（编剧看到的）`；`_key` / `_fixed_text` 只在读存量时保留。
- ⚠ **候选必须按"真实有的资源"给**：新增 `learn_statement.layeredimage_groups`
  读项目里的 `layeredimage`，**按立绘分开**给来源
  （`lan 的 outfit` = `clothes2` / `base_hand_up`；`hanfu_base` 属于 `xing`）——
  用户："lan 实际上没有 hanfu_base 这种选项……必须以真实的有的资源为准"。
  合并的那份（全部角色）保留但排后面；`<tag>_cc` 与本体完全相同时不重复列。
- ⚠ **记忆没有 UI**：用户否掉了第一版的菜单（"我让你自动打开！而不是加个 UI……
  更不需要我手动点击说打开上次项目！那跟我手动打开项目有什么区别？？？"）——
  现在读到记录就自动打开并在**控制台打一行**"已自动读取项目：…"，
  没有就空白打开、一个字都不多说。

---

## 附录 A · 已知边界（诚实清单）

> 这一节原在 README，2026-10-13 移到这里；描述工具**确实还做不到**的地方。

## 三条已知边界（2026-10-05 已拓展）

2026-10-04 的交付评审（`tools/journey_check.py` 端到端实测）列出三条**必须让
用户知道**的边界。它们没有消失，但每一条都被往外推了一格 —— 下面是
"当时是什么样 / 现在是什么样 / 拿什么守"。

### ① `build` 在真实项目上"必然失败" → 现在出得来产物

**当时**：`cli.py build <真实项目>` 必然 `exit=1`，报 **10 条
`PYTHON_SYNTAX_ERROR`**，产物被门禁拦在门外。好处是安全（没写出坏脚本），
坏处是构建这条路**不可用**。

**根因不是产物坏了，是门禁自己假阳性**：`generation_guard._python_syntax_issues`
把 `python:` 块体**一行一行**丢给编译器 ——

```python
positions = [                    # 单看这行：'[' was never closed
    (200, 200), (600, 200),
]                                # 单看这行：unmatched ']'
for i, digit in enumerate(x):    # 单看这行：expected an indented block
```

而那 10 条逐条核对**全是假阳性**：把产物里 7 个 `python:` 块整块 `dedent`
后编译，**一个都不错**。假阳性比漏报更坏 —— 它会让人照着假线索去改本来
正确的代码。

**现在**：块体**整块**编译、`$` 语句**整条**（括号平衡分组）编译。
`cli.py build` 在真实项目上 `exit=0` 并写出 `story.generated.rpy`
（`tools/journey_check.py` 第 5 步）。
守卫：`tests/test_python_syntax_guard_granularity.py`（12 条假阳性形态 +
5 条真错必须仍拦下），并用"打回逐行编译"变异验证过会红。

### ② 第三类只在增量路径保住 → 命令行也能增量了

**当时**：CLI 的 `table xlsx2rpy` 输入只有 `.xlsx`、**没有源 `.rpy`** →
patcher 无源 → **永远全量** → 只能用于**新建**；改已有章节必须开 UI
卡牌编辑器（它有 `script_path`）。

**现在**：加 `--source <这一章原来的 .rpy>`，命令行与编辑器**等价** ——
原文逐字节保留，只替换改过的那几行：

```bash
python cli.py table xlsx2rpy ch01.xlsx --source game/chapter/ch01.rpy --out game/chapter/ch01.rpy
# 增量补丁：改动 1 行，原文其余部分（含 default / init python / transform）逐字节保留
```

**但"能增量"只解决一半**。补丁对不上时若静默退回全量，`--source X --out 新路径`
的目标是**新的**、P0 那条手写护栏不适用 → 源里的 `define` / `init python`
在产物里没了而退出码是 0（真实项目 `pomodorro.rpy` 实测丢 **37 处**）。
所以新增门禁 `OPAQUE_LOST`：**顶层语句没进产物就拒写**，并把丢的
行号 + 语句名列出来；`--force` 可越过（降级为 warn，**仍然报出来**）。

守卫：`tests/test_cli_incremental_source.py`。

### ③ 顶层内容整块静默消失 → **整个顶层区间**随表走，回到文件级

**当时**：顶层（`label` 之外）的东西**不属于任何"拍"** —— `screen` / `style` /
`init:` / `translate` 连 AST 节点都不产生（`_stmt_unmodeled` 直接跳过 +
一条 warning），`define` / `default` / `image` / `transform` 虽然进了 AST，
却只留下结构化字段、**原文已经不在**了。于是"表 → 新文件"这条纯全量路径上
它们**整块消失，而且不报错**：

```
whackamole.rpy  原文 2 个顶层 `init python:` / 56 行游戏逻辑
            → 产物 0 行（`def mole_click` / `mole_tick` / `mole_start` … 全没了）
            而产物语法完全合法、门禁拦不住、游戏能跑但小游戏废了
```

这比 ① 危险得多：① 至少把人拦住了，③ 是**静默改数据**（契约 H1）。

**现在**：解析器按**原文**把整个顶层区间收进 `ParseResult.file_blocks`
（`TopLevelRegionNode`，不做建模、逐字带走），它随**第一张拍**走进 Excel，
渲染时在**所有 label 之前**原样写回。
载体是**隐藏表**「文件级保真」（`beat_model.FILE_BLOCK_SHEET`），
不是隐藏列 —— 原因很实在：Excel 单元格上限 **32767 字符**，而真实文件的
顶层区间轻松超过它（`ball_moving_game.rpy` 38492、`my_perferences.rpy` 50426），
塞进单元格会被**静默截断**；表是一行一格存的，没有这个限制。

真实项目实测（`tools/probe_opaque_fidelity.py`，**40 个文件逐个走纯全量**）：
**顶层语句丢失 0 处**（改之前是 4 个文件 / 219 处）。`tools/journey_check.py`
第 8 步现在两条路都报 `[PASS] … 丢 0/13 处`（改之前"全量 无 --source"丢 11/13）。

守卫：`tests/test_file_level_opaque_blocks.py`（含"超长顶层区间往返不许被截断"、
"块体为空不许写出半截 `init python:`"、"注释夹在缩进块中间时缩进不许算错"、
"同一块被复制到两张拍上只写一次"、"编辑器导出必须指名道姓说出丢了哪一行"）。

### 还剩什么（诚实清单）

| 项 | 现状 |
|---|---|
| **行尾注释**（`x = 1  # 备注`） | **已修**（C 阶段，见不变量 78）：单行语句的注释随隐藏列 `Inline` 走、贴回原行，真实项目 label 内 4/4 保住。只剩"**多行逻辑语句**里的注释"仍不保（说不清挂哪一行） |
| 单行 `python:` 块被规范成 `$ x = 1` | 语义等价，产物形状变了（真实项目 1 处） |
| ~~不给 `--source` 时的全量导出会丢顶层语句~~ | **已消除**（2026-10-05）：整个顶层区间随表保真，全量那条路实测 40 个文件丢 0 处；`OPAQUE_LOST` 门禁保留为**兜底**（哪条路又漏了会指名道姓） |
| ~~`screen` / `style` / `default` 只有 python 类随表走~~ | **已消除**（同上）：`screen` / `style` / `init:` / `translate` / `define` / `default` / `image` / `transform` / 文件头注释全都逐字带走 |
| **产物内部**的重复 label | 门禁只查"产物 vs 项目其他 .rpy"（`LABEL_COLLISION`），**同一个产物内部**的重名它看不出来。真实项目实测 46 个 label / 0 处重名（`tools/check_duplicate_labels.py`），但这是一条**已知缺口** |

---

## 附录 B · 关键不变量全文（编号 1–88）

> 这一节原在 README 的「改动前必读」，2026-10-13 移到这里，**编号保持不变** ——
> 代码注释与其它文档里的「见不变量 N」指的就是这里的编号。
> 它与上面的 A–N 是同一套规矩的两种组织：A–N 按主题速查，这一份按时间累积。

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
   **落盘原语=`core.atomicio`**（2026-11-01 收口；`story.paths.write_text_atomic`
   只是旧名转发）：同目录临时文件 → `newline=''` 写（**不许把 LF 翻成 CRLF**）→
   fsync → 多代 `.bak` → `os.replace`。`.rpy` 与 `.xlsx` **共用这一条路**
   （`save_workbook_atomic` 多一道后缀白名单：只认 `.xlsx`，传 `.rpy` 直接
   `RefusedWrite`，绝不覆盖）。禁用 `Path.write_text` / `wb.save(path)` 直接写。
   守卫：`test_atomic_write.py` + `test_atomic_io.py` + `test_hell_atomic_io.py`。
   两条地狱测试换来的硬规矩（别改回去）：
   · **备份名必须唯一**：`<名>.bak` 之后是 `<名>.bak-<时间戳>-<序号>` ——
     Windows 时钟精度可能粗到一个 tick，只按时间戳命名会让第二代盖掉第一代；
   · **`os.replace` 对瞬时 `PermissionError` 重试**（6×20ms）：并发写同一目标
     时两条替换会撞车，不重试就是"保存了但没变"；真被 Excel 占着时照样失败，
     由 `human_io_error` 给人话。
   **读 Excel 也只有一条路=`storygraph/excel/safety.py`**（2026-11-01）：
   `safety.open_workbook()` 先看 zip 目录（解压炸弹 / 声明维度炸弹 / 宏 /
   外链 / 嵌入对象 / 加密 / 老 .xls），`hard` → `UnsafeWorkbook`（说人话），
   `soft` → `swallow` 留痕。**不许再写 `openpyxl.load_workbook(`**
   （AST 横扫判据在 `test_excel_safety.py`；写侧同规矩：除 `atomicio` /
   `xlsx_preserve` 外不许 `wb.save(`）。同批三条硬规矩：
   · 表的来源指纹存**完整 sha1**，比较走 `table_meta.fingerprints_match()`
     （老表 16 位按前缀兼容 —— 直接比 40 位会让每张老表都报"旧了"）；
   · `patch_export(expect_sha1=…)` **读完再核对**：关掉"先查后读"的窗口，
     窗口里被改过就拒绝增量（退回全量是安全方向）；
   · 资产表 6 个写函数过 `asset_registry._ASSET_WRITE_LOCK`（可重入的进程内锁；
     无锁实测 8 线程写 32 行只剩 4 行）。
   **结构与债的纪律**（2026-11-01 起）：两套棘轮，**只许降不许升** ——
   · `tests/complexity_budget.json`（"最长 40 + 最绕 40"的并集；新函数硬线
     500 行 / 150 分支）；改完跑 `python tools/complexity_report.py` 看榜；
   · `tests/tech_debt_budget.json`（超大函数数 / 高分支数 / print / swallow /
     宽泛 except / TODO / 超长文件 / **导入环**）；改完跑
     `python tools/debt_report.py`。⚠ 环按**模块集合**比，别只看个数：
     两个小环并成一个大环时个数会变少、债却更重。
   · 拆函数的安全姿势：**整段搬出 + 原位置留一行调用 + 缩进不变**；
     新方法只吃显式参数（别读上一段的局部变量），每搬一段跑该模块的测试。
   · 清债之后重生成快照：`python tools/complexity_budget.py` /
     `python tools/debt_budget.py`。
   · **`print` 分两档量**（2026-11-01 第六段）：`print_calls` 是总量
     （含 `cli.py` 等 6 个呈现层模块与 `if __name__ == '__main__'` 演示 ——
     那部分**必须留 stdout**，改成日志等于把 CLI 弄瞎）；`print_library`
     才是真债，现在 = **3**（`preflight` 的 stderr 兜底 ×2 + `safeguard`
     的 `--debug` 通道 ×1，都是给人看的）。**库里不许新加 `print`** →
     走 `swallow('模块.函数', e)`。
   · **两个"解环叶子"不许长回来**（2026-11-01）：`parsing/continuation.py`
     （`shift_continuations`，`renpy_parser` / `renpy_parser_stmts` 共用）与
     `story/file_facts.py`（`FileFacts` / `scan_file`，`placement` 与
     `scan_cache` 共用）只许导 `core.*`；**不许**反过来导入上层模块 ——
     一旦导入，环就原样长回来（`debt_report --cycles` 会当场红）。
     同一批还有 `ui_qt/window_layout.py`（六个窗口/卡片数字）与
     `ui_qt/text_html.py`（`renpy_text_to_html`）：`main_window` 与
     `window_state` / `stage_preview` 各指它们，原来那个五模块环
     （`main_window → stage_thumb → stage_preview → main_window` +
     `main_window ↔ window_state`）就是这么断的。
6. **校验分级要克制**：某类资产**已有资源表**而引用不在其中 → 错误；
   项目压根没建这类表 → 警告。宁可少报，也不能刷屏（曾一次报 1073 条）。
7. **`BEAT_FIELDS` 是列顺序唯一真源**（31 列 = 21 列编辑面 + 10 列隐藏元数据），
   定义在 `story/beat_fields.py`（不再手写第二份），`Beat.to_row/from_row`
   必须逐列对应。`Condition` 是**语义列**（不是排版列）：
   它 2026-10-05 从"已被收敛掉的旧列"里**加回来**了 —— 没有它，
   `Beat.condition` 在进出 Excel 时没有任何出口，所有 `if` / 菜单条件会在
   往返里被静默抹平（实测真实项目一条章节 7 条条件 → 0 条）。
   隐藏列放**最后**、整列收起（用户 2026-10-05：「新增的列放远点，甚至可默认
   隐藏，因为事实上策划和文案不需要」）：`StageOrder` / `OpOrder` /
   `CodeBlocks`。隐藏 ≠ 没有这一列 —— 数据照旧读写，只是不占策划的视线。
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
    **按"行的身份"配对替换**（台词配同说话人、`show ling` 配同 tag、
    代码行配同目标、其余只与逐字相同者配）——"什么都没改 → 逐字节等于
    原文"就是这么来的。**删行会破坏"行数不变"**，所以必须用与"插入"同一套纪律：
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
    214→213 拍。
    **补（不变量 62）**："行数不变"到第十五轮已解除（拍内也能增删整行），
    但纪律没变：只在**平整区间**、只对**舞台/音频/`$` 代码**行、身份不重复时才动。
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
60. **剧情图的"操作"是结构化语句，不再是裸字符串**（用户 2026-10-05：
    "开始迁移，处理结构债"）。审计那条"双 IR 并存"的地基在这一轮换掉：

        node.statements : list[Statement]   ← 结构化真源（含出处 / 条件）
        node.operations : list[str]         ← 兼容视图（property，派生）

    迁移按"**新老并存 → 只读比对 → 写路径切换**"三步走，每一步单独可验：
      · **并存**：`operations` 变成 property（读=取每条语句的源码文本、
        写=重建语句表），二十来处老调用点一行没改；`Graph.add_node` 不再
        共享调用方的 list（顺手消掉旧版"已知问题 #6"）；
      · **比对**：真实项目 51 个节点 / 368 条代码语句 —— 与"老算法会算出的
        那串"**逐字节一致**、往返幂等、控制流导出与迁移前产物**逐字节一样**
        （723 行）；
      · **切换**：解析器把**整条语句**塞进节点（`add_statement`），于是
        `source`（文件:行号）**368/368 都留住了**、`condition`（这条代码原本
        在哪个 `if` 里）也留住了（8 条）；导出 / 分析 / 抽变量 / 变量合并都改
        读语句表 —— 变量编辑器保存一次不再把技术代码的出处吃掉。
    **踩过的坑写进守卫**：属性返回的是**新列表**，所以 `node.operations.append(x)`
    会静默失效 → 全项目改用 `add_operation` / `set_operations` /
    `clear_operations`，`tests/test_graph_statements.py` 用 AST 盯着；
    老布局 `.json`（只有 `operations`）照样能读，新文件**多写**一个
    `statements` 键（旧读者忽略它即可）。
61. **拍的行区间是增量导出的地基：不许交叠、台词必须在自己区间里**
    （用户 2026-10-05："开始下一轮修改"）。这一轮把"注释存活率"从
    **16/32 提到 31/32** —— 真实 7 章 × 4 种操作（不动 / 改台词 / 插卡 / 删卡），
    退化成全量重生成（= 手写注释全丢）的场景从 16 个降到 1 个。
    修掉的三处：

      · **行号被上一拍抢走**：`scene` 撞 `scene` / `play` 撞 `play` 这类
        "先落拍"的分支，在落拍**之前**就把当前语句的行号记进了上一拍。
        真实 chapter1_1 的 ppt 段（`bg1_2 / pause / bg6_1 / pause / bg3_1`）
        于是成了"第 18 拍 31-32、第 19 拍 33-33"：补丁把 32 行写成上一拍的
        背景，`scene bg bg3_1` **无处可放**（产物里背景是错的），校验发现后
        整章退回全量。修法：行号**只记"真被这一拍吸收"的那条**
        （`absorbed` 标志 + 循环末尾统一记）。
      · **`extend` 拍的行区间和下一拍交叠**：`extend` 自成一张卡却不带走
        pending 里攒的舞台/代码行 → 那些行号被**下一句台词**那一拍吞掉
        （真实 test.rpy：extend 拍 18-18 落在下一拍 16-23 里面），补丁遇到
        交叠只能跳过后拍 → 对位率 71% → 又退全量。修法：`extend` 与 `SAY`
        同规矩，**把它前面的 `show/transition/注释`一起带走**。
      · **删除检测认错人**：编辑器那条路的拍本来就带**准确行号**，却还拿
        "label/类型/说话人/舞台指纹"（不含正文）去重新对齐 —— 旁白连成一片时
        `SequenceMatcher` 会挑错元素：用户删的是 152 行那一拍，它报"156 行那一拍
        被删了"，而那一拍明明还在 → "与在册拍交叠" → 判删除不安全 → 退全量。
        修法：**按行区间对身份**（源拍的行区间还有人认领就是还在）；
        但加了保险：只有"少数源拍没人认领"（≤20%）才信行号 —— 行号整体过期
        （源被手工改过）时退回内容对齐，否则会误判成"用户删了整章"。
      · 顺带：`_verify_patch` 与 `_replace_matched` 现在**容忍空白写法差异**
        （原文 `yhj"结。"` + 行尾注释 vs 生成 `yhj "结。"`）——以前按逐字节比
        会判"生成内容找不到"（真实 chapter3_1 整章退全量），或者反过来
        "没改却动了文件"（每个说话人行都成一个 diff 块）。
62. **增量补丁能增删整行了**（用户 2026-10-05："继续实现，清除结构债，
    并确保没有bug产生"）。结构债就是那条"**行数永不变化**"的硬约束，
    它的代价是两个真问题：**清掉一条 `show` 删不掉**（补丁报成功、产物里
    sprite 还在 = 静默错产物）与**给控制流卡新填台词填不进去**（整章退回全量、
    注释全丢）。现在增删发生在**平整区间**里，靠三条准入线保证不产生新 bug：

      · **区间平整**：无块头（`menu:` / `if` / `else:` / `python:` …）、无块首
        （行尾 `:`）、只有一种缩进 —— `if` / 菜单里**永远不增删**；
      · **配对顺序单调**：渲染顺序与源码顺序不一致时退回一对一（绝不重排原文）；
      · **身份准入**：要增/删的那一行，其"身份"（`_head_of`：台词按正文、
        舞台按动词+目标、代码按目标）在对面必须不存在，且不是"同一句话的
        另一种写法"（去空白后互相包含也算同一条），**整份原文里也不能已经有
        这一行** —— 这三条专治"写出重复行"。

    这一轮同时抓到并修掉两个**会改坏作者文件**的真 bug：

      · `# TODO: 缺少资源 …` 是**说明**不是内容：`pipeline/compile.py` 写明的
        约定是"源文件里原有的行按原样保留"。不做这条，"什么都不改"的导出会把
        12~16 行 `play music "bgm/…" fadein 1.0` 变成 TODO 注释（**整章音乐没了**）；
      · 原文 `'file "c:\\a.py", line 24'`（单引号、内部双引号不转义）与生成器
        `"file \"c:\\\\a.py\"…"` 只认双引号时"身份"不同 → 同一句话被**插成两行**；
        现在按引号/转义归一配对，并保留原文那一行（没改就不动文件）。

    另外：**旁白被整句换掉**以前会被判"源被手工改过"而跳过（新台词不落地 →
    整章退全量）。现在有一条窄口子 —— 纯台词拍、新旧非空行数相同、旧段里正好
    一个台词槽、**左右邻居各自都自洽**才认（源文件整体错位时邻居这关过不去，
    所以不会写错行），且这条路上只做一对一替换。

    实测（真实 7 章 × 5 种操作 + 不动点复检）：
      · **不动 → 逐字节等于原文**：8/8 章通过（这条是底线，上一版被 TODO 顶行破坏）；
      · 每种操作都走增量（对位率都在门槛之上），注释零丢失；
      · **产物再导一次逐字节不变**（不动点：插重复 / 漏写都会在这里露馅）：全部通过。
63. **`menu:` 是块，拍是扁平的 —— 渲染前必须重新归组**（评估报告 2026-10-02 的
    P0-1，2026-10-05 修）。这是"用扁平语句流表达树形剧本"这个选择的代价兑现处：

        menu:                    逐拍渲染（修复前的实际产物）
            "选项A":                 "选项A"        ← 变成裸旁白
                jump b               jump b         ← 无条件执行
            "选项B":                 "选项B"
                jump c               jump c         ← 永远不可达

    `menu:` 整块消失、第一个分支无条件跳转、第二个分支是死代码 ——
    **游戏能启动、剧情全错、不报任何错**（真实项目 7 处菜单全是这种写法，
    20 个选项 100% 丢失）。修复分四层，每一层都单独可测：

      · **解析器**：裸 `menu:`（没有 caption）也要产出 `MENU` 语句
        （以前只在 `if node.caption:` 里产出 —— 无标题菜单在语句流里
        **根本不存在**，下游无从知道"这里有个菜单"）；同时给菜单块内的
        语句标一个结构位 `Statement._menu_member`（`_walk` 是唯一还看得见
        嵌套的地方）；
      · **拍构建**：菜单拍 / 选项拍一律落拍（空 caption 用 `MENU_NO_CAPTION`
        占位，**不写进 Excel** —— openpyxl 拒收控制字符）；并把交错的
        "选项头 vs 块体"归一成"选项 → 它的块体"（`order_menu_bodies`：
        解析器先收全部选项头再下钻块体，扁平序列里两者是错开的）；
      · **渲染**：`split_beat_units` 把拍重新按**组**切分，`render_menu_block`
        生成 `menu:` / `"选项":` / 缩进块体，空块补 `pass`（空块是语法错误）；
      · **写表**：`stamp_menu_bodies` 给选项体里**没有条件**的拍补恒真的
        `True` —— Excel 里存不下结构位，而"选项无条件 + 块体只写跳转 +
        块外紧跟一句台词"三个条件都是空串，**只能靠这一位区分**。

    边界判据（踩过两次，都写进 docstring）：`_menu_member` 优先；
    下一张 `menu` 拍断开；再看条件（累积条件 / 恒真 `True`）。
    关掉这一条的后果不是"少生成几行"，是**静默改剧情**。
64. **「代码」列是"保真后门"，但后门也有三条硬规则**（2026-10-05 续修，
    评估报告 P0 之后把剩下的往返丢失逐条清掉）。这一格装两类东西 ——
    原生 Ren'Py 语句与 python 代码，混在一起时**任何一条分类错误都是
    静默的产物损坏**。实测踩到的三条：

      · **`python:` 块的层级必须活过 Excel**。块结构在单元格里只体现为
        **缩进**，而 `_split_code_cell` 原先把它 `strip` 掉了 → 整块被拆成
        一条条 `$` 语句（`mini_game1.rpy` 一个块拆出 53 条），`for` / `if`
        的嵌套体全被压到同一层。现在：
          – 每行的**相对物理缩进**记进 `beat.ordered` 的第三位
            （`(kind, line, indent)`，`_relative_indents` 算：块层级归一成
            4 的倍数，括号续行保留原对齐）；
          – 渲染时 `_python_block_indent` 认出"有人真的进了下一层"
            （用**括号平衡**区分续行与真块层级）就出 `python:` 块，
            **块内原有缩进保留**（那是嵌套层级）；
          – 判定"这不是 python 块"必须用**Ren'Py 专有**语句头
            （`_is_native_renpy_line`）：`_looks_like_renpy_statement` 的表里
            含 `for` / `if` / `while` / `with` / `pass`，拿它一票否决会把
            真块体全拦下来，而把 `window hide` 认成 python 又会让整段演出
            消失（`mini_game_dfinder.rpy` 实测 7 条）。
      · **多行语句不许被拆开**：`show screen prompt("…", act=[ Call(…), ])`
        是一条语句，逐行补 `$` 会生成 `$ Call("…"),` —— 语法错误。
        `_group_code_statements` 按括号平衡把续行粘回一条，`$` 只加在首行。
      · **一行里不许出现两个 `$`**：`_split_code_cell` 现在把 `$` 前缀
        剥干净（紧凑写法用 `core.statement.MARKER` 承载，`$ x` 存裸文本），
        渲染时按 `$` / `$ ` 两种写法还原。
      · 顺带修掉一个**隐形杀手**：`_split_lines` 现在把 CRLF 归一成 LF ——
        Windows 写出去的单元格行尾带 `\r`，会让缩进计算、`$` 判定、
        按行比对全部悄悄错位（实测就是它让"续行缩进"算成 0）。
65. **可保真的东西必须保真：`call … from` / `show expression` / 空台词**
    （同上）。这一轮清掉的三条都不是"少几个字符"：

      · **`call X from Y`**：`from` 是 Ren'Py 的**存档点标识**。拼 Raw 文本
        时漏掉它，往返后就退化成裸 `call` —— 照样跳得过去，但**老存档里的
        `_call_xxx` 全找不到，玩家读档会落到别处**（`chapter_controller.rpy`
        实测 7 条）。`_call_from_suffix` 补上（`renpy_exporter` 那条控制流
        路径早就补过，这是章节路径的同一修）。
      · **`show expression X`**：图像名是一段 **Python 代码**
        （`holo("lan base")`），带空格/括号/引号，装不进 `图像@位置` 那种
        紧凑编码。现在它编码成**自描述子句** `expression X as q at i`
        （`_expression_clause`，`show_item_part` 与 `compose_show_cell` 共用
        一份），`_parse_show_expression` 按**跳过引号**的关键词扫描读回来 ——
        位置与别名都不丢（`script.rpy` 实测 5 条）。
      · **空台词不产出语句**：`who` 与 `what` 都为空是解析噪声（来自空行与
        **三引号文档字符串**，`eyes_tracker.rpy` 实测 3 条全在 `"""…"""` 里）。
        留着它的代价不是"多一行"：`Beat.is_empty()` 会把那张拍丢掉，
        整个文件因此退化成 `label start: pass`，而这张拍在往返里必然对不上。

    **量化**（`tools/audit_roundtrip.py`，真实项目 41 文件 / 3800 语句）：
    丢失从 **76 条（2.0%）降到 6 条（0.16%）**，`menu` / `menu_option` /
    `hide` / `scene` / `play` / `comment` / `extend` / `jump` / `return` /
    `show` 全部 **0 丢失**；凭空新增也从 99 条降到 35 条
    （新增主要是"一个 `python:` 块被拆成几条 `$`"这类**语义等价**的写法差异）。

    审计还有一条独立口径：**产物里的 Python 语法是否正确**
    （`_unparsable_python` 用 `ast.parse` 逐段过）。这条口径是必要的——
    实测有一个"丢失 0 / 新增 0"却让产物跑不起来的缺陷
    （`at Transform(xysize=(1920, 1080))` 的逗号被吃掉）。

    另见 `tools/audit_branch_scope.py`：**分支内舞台指令**是否留在自己的
    分支里（这类错误产物能跑、不报错，但演出已经错了）。
66. **`python:` 块的边界由一个隐藏列说了算**（用户 2026-10-05：「新增的列
    放远点，甚至可默认隐藏，因为事实上策划和文案不需要」）。

    「代码」列一个格子装的是**多行文本**，而 `python:` 块的边界在纯文本里
    **表达不出来**（`python:` 关键字在单元格里已经被剥掉）：

        代码格内容                     块边界是哪几行？
        $ a = 1                        ← 不是块
        # 注释                          ← ？注释后面就是块
        for i in range(2):             ← 块从这里开始（第 3 行）
            a += i                     ← 块体
        python:（第二个块）             ← 又一个块（第 5 行）
        for j in range(2):
            a -= j

    只按缩进推断时，"两个块的首行都在第 0 列"完全分不出来 —— 猜错的代价是
    **静默改语义**（两块并成一个，`for` 的体跑到另一个块的变量上；
    `mini_game1.rpy` 实测一个块拆出 53 条 `$`，`for` 里的 `if` 变成顺序执行）。

    落地方案分两半：

      · **单元格里打标记**：`compose_code` 给每个块体行加行首标记
        （`CODE_BLOCK_OPEN` / `CODE_BLOCK_BODY`，两个私用区字符）。
        它是**权威**说法，读回来（`_split_code_cell`）不再猜缩进；
        写进 Excel 前由 `compose_code_cell` 剥掉 —— 单元格里只留纯文本。
      · **隐藏列 `CodeBlocks`**：块在格子里的**行区间**（`1-11 13-17`，
        1-based 闭区间，**人能读懂**）。标记丢了（老表、手工复制行、
        有人清空那一列）时由它兜底（`from_row` 把区间摊成行号集合交给
        `_split_code_cell`，`for i in range(2):` 这种"一行两解"的写法就
        不再靠猜）；再没有就退回"按缩进推断"——

    三层兜底的顺序是刻意的：**最坏是块结构没保住，而不是产物改坏**。
    `Beat.code_blocks` 是这份数据的载体，不进 `ContentHash`
    （它描述排版，不描述内容）。

    分类时**成组判断、逐行输出**：`for i in range(2):` 单独一行时"是
    Ren'Py 还是 Python"无从判断，但它的**下一行更深**就说明它是个块头；
    而 `ordered` 必须逐物理行记（`_ordered_code_lines` 是按行去
    `raw`/`code` 两个池子里取用的）。两条都要满足，缺一个都会退回
    "raw 在前 code 在后"的兜底顺序。

67. **顶层（label 之外）的东西也要跟着表走 —— 哪怕它不属于任何"拍"**
    （2026-10-05，拓展交付评审的边界③）。

    "表"的单位是**拍**，而 `init python:` / `default` / `screen` 全在
    **顶层**：它们不进 Beats，于是"表 → 新文件"这条纯全量路径上
    没有任何东西记得它们。症状是本仓库最贵的那一类 ——

        whackamole.rpy  2 个顶层 `init python:` / 56 行游戏逻辑 → 产物 0 行，
                        产物语法完全合法、门禁拦不住、游戏能跑但小游戏废了

    （增量补丁那条路能保住，因为它"不动顶层"；但纯表路径没有源可补。）

    落法是三层，**每一层都只做一件事**（2026-10-05 晚升级：从"只有
    `python:` 类"扩到"**整个顶层区间**"）：

      · `parsing`：`_top_level_region` 按**原文**收下 label 之外的一切
        （`define` / `default` / `image` / `transform` / `screen` / `style` /
        `init:` / `translate` / `init python` / 文件头注释 …），
        包成 `TopLevelRegionNode` 追加进 AST —— 顶层行**不进 statements**
        （进了就会变成一张"标签为空"的拍，被渲染进 `label start:` 里）；
        分流靠"在不在 label 里"，不靠"这条语句建不建模"；
      · `story`：`attach_file_blocks` 把它们挂到**第一张拍**上
        （`Beat.file_blocks`）；纯定义文件（没有拍）会**造一张空拍**来携带 ——
        `Beat.is_empty()` 认它算有内容，否则"刷新候选表"会静默删掉它；
      · `beat_render`：`render_beats_to_rpy` 在**所有 label 之前**原样写出，
        内容相同的块只写一次（重复定义会让后者覆盖前者）。

    ⚠ **载体是隐藏表，不是隐藏列**（`FILE_BLOCK_SHEET`，2026-10-05 实测改的）：
    Excel 单元格上限 **32767 字符**，而真实文件的顶层区间轻松超过它
    （`ball_moving_game.rpy` 38492、`my_perferences.rpy` 50426），塞进列里
    会被**静默截断**、还不报错 —— 而那正是这一条要消灭的东西。
    表按"一行一格"存：**整段**没有上限（6000 行照样写），**单行**仍受
    32767 约束（`CellTextTooLong`，一行三万字的 `init python` 该拆成多行）。
    老表的隐藏列仍然**只读**兼容。

    两个容易写错的地方（都被门禁/用例抓过，别再踩）：
      · **顶格注释可以出现在缩进块中间**（Ren'Py/Python 都忽略整行注释的缩进，
        真实项目 `ball_moving_game.rpy` 就是 `init python:` / `# 类/对象设置` /
        `    class GridCell:`）→ 它必须**并入当前段**，否则块头丢了、产物里
        出现悬空缩进；
      · **基准缩进只看结构性行**（注释不算）→ 否则被那条顶格注释拉到 0，
        去缩进失效，渲染后 `import random` 变 8 格 → **unexpected indent**。
        这一条是 `PYTHON_SYNTAX_ERROR` 门禁拦下来的（`journey_check` 第 2b/5 步）。

    为什么不是"全丢但报一声"：那是**二选一里较差的那个**，而保真是能做到的。
    `OPAQUE_LOST` 门禁因此从"每次全量都拦人"退化成**兜底**：哪条路又漏了
    才会响，并列出丢了哪几行。实测：`tools/probe_opaque_fidelity.py`
    40 个真实文件走纯全量，顶层语句丢 **0 处**（改之前 4 个文件 / 219 处）。

    **还剩 3 条**（用 `tools/diagnose_losses.py` 逐条定位，带对家与 diff），
    **全部已定性为"工具判据的假阳性"，不是产物缺陷**：

      · 3 条 `python:` 块内**续行（括号内换行）的缩进**比原文大一档 ——
        原文 12 空格、产物 20。括号内换行的缩进在 Python 里**完全自由**，
        语义一模一样（`_unparsable_python` 全程 0 处错误）；差别只是
        审计按**逐行文本**比对，把"同一段代码缩进不同"算成一丢一增。

        试过三种改法（基准改成从非续行取 / 只给续行减一档 /
        `_indent_block_body` 不叠基准），**每一种都让全项目丢失数变多**
        （3 → 7），或让解析异常从 0 涨到 5。所以**明确不动**，
        并在 `beat_render._indent_block_body` / `_indent_beat_lines`
        的 docstring 里写清"为什么修不动、别再试"。

    已经修掉的两条（`say` 丢失率因此降到 0.0%）：
    台词**首空格**被 `from_row` 的 `lstrip()` 吃掉（`beat.text` 是字面量
    **内容**，空格就是内容，不是排版）；台词里的**反斜杠/引号没有反转义**
    （`_pick_str` 返回源码形态，渲染又转义一次 → 每往返一次翻倍）。

    另外这一轮从上面那 3 条里**拆出一个真 bug 并修掉了**：
    `python:` 块**内部**的空行会丢（`mini_game_puzzle.rpy` 的多行调用参数表
    中间就有一个）。两处吃它：`_render_marked_code_block` 收块体时遇到
    空行就收尾（块标记是行首字符，空行没有），`render_beats_to_rpy` 收尾时
    把整个 label 的空行都扔了。两处都改成"只去首尾/往前看"，
    回归用例 `test_blank_line_inside_a_python_block_survives`，
    并用"还原这两处"变异验证过它会红。

68. **label 是硬边界：产物里的 label 集合必须与源一致**
    （2026-10-05，组合面地狱测试 `tools/hell_test.py` 抓到）。

    症状是本仓库最贵的那一类 —— **能跑、不报错、但游戏逻辑断了**：

        label a:                 →      label start:
            $ focusmode = False          pass
            show screen home             return
        label b:
            e "后一句"

    "孤儿指令段"（`$` / `show screen` / `pause` 这些**不落拍**的语句）在换
    label 时没有落拍，被吸进**下一个 label** 的第一张拍 → label a 名下一张拍
    都没有 → 渲染按拍分组时它**根本不出现**，渲染层退回 `label start:`。

    真实项目实测（`tools/probe_label_fate_real.py`）：**51 个 label 丢 4 个**
    —— beginstory / location / pomo_start / sr_about_render_test，其中
    `location` 带 `call screen map`，是主循环节点。后果有两层：

      · 所有 `jump X` **静默失效**；
      · 产物里凭空多出一个 `label start`，而 `start` 是 Ren'Py 的游戏入口
        → 合并进 `game/` 就是**重复 label，游戏起不来**。

    修在三处，**缺一处都不行**：

      · `beat_build.add()`：label 切换时先 `flush_stage_only()`（内容归属正确）；
      · `_fill_empty_labels` / `_fill_labels_without_beats`：给"一张拍都没有"
        的 label 补空拍（名字不丢；覆盖"只有 `return`"和"连语句都没有"两种）；
      · `Beat.is_empty()`：把"只有 label 的空拍"当**结构锚点**（与 menu 锚点
        同理）—— 否则它在 xlsx 往返里被 `_read_beats_from_ws` 当空行丢掉。

    ⚠ **同一枚硬币的另一面**：不许**凭空造** label。修完上面之后又跑了一档
    更严的验证（逐文件走完整 xlsx 往返），结果是 51 → **57**：6 个**纯定义
    文件**（`transform.rpy` / `whackamole_images.rpy` / `generated_ui.rpy` /
    `plottree.rpy` / `script0.rpy` / `script1.rpy`）—— 它们解析出 0 条语句、
    0 个文件级块，**源文件里根本没有 label**，产物却各多一个
    `label start: pass return`。根因是 `render_beats_to_rpy` 末尾的兜底
    `if not emitted_labels and not file_texts`，已收紧成
    `not emitted_labels and beats and any(not carrier)`：**有真拍却一个
    label 都没输出**才兜底。

    判据：`tools/probe_label_fate_xlsx.py`（**逐文件 + 完整 xlsx 往返**），
    真实项目 **51 → 51，丢 0、多 0**；`probe_label_fate_real.py`
    （不走 xlsx、全项目合并）同样是 51 → 51。

    ⚠ 这一条的**变异验证**给了一课：最初写的两条空 label 用例在"改回旧实现"
    时**全绿** —— 它们直接调渲染器、**不走 Excel**，而 `is_empty` 只在读表时
    生效。**测试绿了不等于缺陷被钉住，必须走缺陷真正发生的那条路径。**

69. **写字符串字面量时，控制字符也是要转义的**（2026-10-05）。

    `Beat.text` 里存的是**真的**换行符（解析时把 `\n` 还原了），所以只转义
    `\\` 和 `"` 会写出**物理换行**：

        e "路径 C:\new\test"     →     e "路径 C:
                                       ew\test"

    产物**不可解析**，而且门禁看不出来（它不是 python 块）。Ren'Py 里 `\n`
    是写台词的常用手法（强制换行），不是边缘情况。

    修：`beat_render._quote` 补 `\n` / `\r` / `\t`（顺序是**先翻 `\\`
    再转控制字符**），menu option 那条分支也从"手包一对引号"改成走 `_quote`。
    回归：`tests/test_hell_findings.py::TestQuoteEscapesControlChars`。

    ⚠ 配套教训：新加的"单行引号配平"检查把真实项目 14 条**三引号 docstring**
    报成语法坏 —— 判据要先排除 `"""`。**假阳性比漏报更贵**：它会让一条检查
    永远没法用来验收（这是第二次踩到，第一次是 `build` 的门禁粒度）。

70. **单行列的单元格值必须是一行 —— 控制字符在进产物之前就压掉**
    （2026-10-05，是上面第 69 条的**同一类、另一个出口**）。

    第 69 条修的是"**字符串字面量**"那条出口（台词走 `_quote` 转义）。
    但下面这些列是**裸 token** 出口 —— 值被逐字拼进一行代码，不经过转义：

        Speaker 列 = 'ling<真换行>X'     →  ling
                                           X "第一句"        ← **产物不可解析**
        At      列 = 'left<真换行>right' →  show ling at left
                                           right              ← 同上
        Show    列 = 'ling<真换行>happy' → 被拆成 `show ling` + `show happy`
                                           —— 能解析，但**立绘变成两张**

    单元格里出现真换行是**真实可达**的：Excel 的 Alt+Enter、从别处粘一段
    多行文本、脚本写表，都会留下它。**后两种最阴**：产物"能解析"，中途断行
    产生的是一个看起来正常的短行，门禁（只查 Python 语法 / 图像名合法性 /
    label 重名）拦不住。

    处置：列契约本来就是"一行一个值"，所以**读写两侧**都过
    `beat_model.sanitize_single_line_cell`：

      · 控制字符（C0 + DEL）→ 一个空格；
      · `Label` 额外去掉空白与冒号 —— 标签名里不允许它们，只压成空格会写出
        `label a b:` 这个**语法错的标签头**（比断行更难查）。

    清单是 `SINGLE_LINE_BEAT_FIELDS`（就写在 `BEAT_FIELDS` 旁边，**加列时
    必须显式选一边**，由分区用例逼着做决定）。多行列 —— `Text` 台词 /
    `Comment` 注释 / `Stage` 舞台 / `CodeBlock` 代码 —— **刻意不在清单里**：
    它们的换行是**内容**（台词里 `\n` 是强制换行、Stage 一格多指令、
    代码块多行），由 `_quote` 转义或按行渲染。
    （文件级保真块原来也在这里，2026-10-05 挪去隐藏表了，见不变量 67。）

    为什么写那一侧（`to_row`）也要压：**幂等**（`Notes` 那次不对称的坑），
    以及 `openpyxl` 保存时会对 `\x0b` 这类控制字符直接抛
    `IllegalCharacterError`（一个畸形值让"导出 Excel"整个崩掉，指不到哪一格）。

    ⚠ **压平本身就是"改了用户的数据"**（`ling\nX` → `ling X`）：
    产物确实能跑了，可值跟他在表里写的不一样了。本项目的立场是
    **静默改数据比报错坏得多**（P0 就是这么定义的）——"救了用户却没告诉他"
    同样属于这一条。所以清洗**必须留痕**：`Beat.sanitized_cells` 记下哪几格
    被改过，然后三条路各说各的：

      · `validate` → **W114**（聚合**一条**，不逐行刷屏）：体检面板与
        `validate_all` 的调用方都能看到；
      · `CompileResult.summary()` → 导出时一句话（`cli.py compile` /
        `table xlsx2rpy` 那条命令行）；
      · `EditorSession.export` 的 `note` → UI 那条路（"打开表 → 导出、
        从不体检"的人只有这里会看到）。

    ⚠ **留痕要放在所有 `g()` 之后**：结构化列（Scene / Show / At / With /
    Hide / BGM / SFX / StageOrder / OpOrder / CodeBlocks 共 12 列）是在
    `cls(...)` **之后**才读的。第一版把 `sanitized_cells` 赋在构造函数后面
    那一行，这 12 列的留痕**全丢** —— 而 W114 只在"有留痕"时才报，
    于是恰好是**最常被粘脏的那几列**（立绘 / 位置 / 音乐）静默无声。
    抓它的是**参数化用例**（`test_cleaning_is_recorded[Show]` 等 12 条红）。

    判据：`tests/test_cell_control_chars.py`（**115 条**）。用例表**由
    `SINGLE_LINE_BEAT_FIELDS` 反推**，不手抄一份 —— 手抄的话"在清单里"
    ≠"测过"（有人把某列挪出清单，测试不会红；现在参数化用例与分区用例会
    **同时**红）。变异入口 `tools/mut_cell_sanitizer.py`（关掉清洗 →
    **64 条红**）。真实项目探针 `tools/probe_cell_control_chars.py`
    （逐列塞控制字符，走完整 xlsx 往返，产物必须能解析回来）。

71. **原地导出成功之后，内存 := 磁盘**（2026-10-05，交付审查第 2 条缺口）。

    `EditorSession.export` 写回的如果**就是它载入的那个文件**，就必须
    重新解析它、用解析结果替换内存里的拍（`_reload_after_inplace_write`）。

    为什么非做不可 —— 这是单人最常用的那条循环，而它曾经在第二次断掉：

        改一句 → 导出 → 再改一句 → 再导出   ← `PATCH_FAILED：对位率不足`

    根因：第一次写回之后**磁盘上是新内容**（补丁插了行、首行多了生成标记），
    而内存拍的 `src_first/src_last` 还是**改动前**那份文件的；`_recover_ranges`
    见"拍上已经有行号"就跳过重算 → 对位率掉到 0~50% → P0 那条"目标就是源文件"
    的护栏拒写。用户看到的只有一句"对位率不足"，没有任何提示说"重载一下就好"。

    为什么用"重新解析"而不是"按内容猜行号"（**先走过那条弯路**）：
    第一版用 `recover_source_ranges`（解析磁盘 + difflib 对拍）贴行号，
    它在最需要它的场景上**是错的** —— 解析器把"注释 / `call screen` +
    紧随其后的台词"吸成**一张**拍，而内存里是**两张** → 对齐给出**交叠区间**
    → 对位率 50%（whackamole 实测）。合并**本来就知道**每一拍写到了哪几行，
    但把它报出来要动 `patch_export` 里"替换 / 插入 / 删除"三套坐标系的换算
    （全仓最危险的一段），代价与风险都不划算。而"内存 := 解析(磁盘)"是
    **零启发**的：磁盘就是真源（契约 A1），解析器怎么读、内存就怎么存。

    代价（明确接受）：拍的**粒度**可能变 —— 刚插的那张"只有注释/代码"的卡
    会并进后面那张。**那正是重新打开这个文件会看到的**，只是把"下次打开
    才发生的事"提前到现在；界面侧由 `resync_after_export` 跟着刷新列表
    （拍数没变时不动，不打扰最常见的"改一句再导出"）。

    判据：`tests/test_export_acceptance.py::test_7_two_in_place_exports_in_a_row`
    （变异：去掉重新解析 → 产物丢掉 `default score` / `transform my_tf:`）+
    `tests/test_qt_signal_wiring.py` 的两条列表跟随用例。

72. **交付审查的三条缺口都以"让软件自己解决"收尾**（2026-10-05）。

      · ① UI 导出说不出"丢了哪几行" → 把源文本交给 `OPAQUE_LOST` 门禁
        （判据 3 条，含**反面对照**：保真时不许拦）；
      · ② 只有注释/代码的拍对不上位 → 见不变量 71（零启发重对齐）；
      · ③ 顶层内容只有 `python:` 类随表走 → 见不变量 67（整个顶层区间 + 隐藏表）。

    共同点：**都不是"加一句更清楚的警告"**，而是把会出问题的那件事从根上
    消掉；警告退到兜底。理由是本项目的既有立场 —— 提醒会被人习惯性忽略，
    而"静默改数据"不能靠提醒兜住。

73. **Qt 的 `bool` 不许被当成槽的参数 —— 接线、审计、兜底三层都要有**
    （2026-10-05，追查根目录那个名叫 `False` 的文件）。

    信号签名是 `clicked(bool checked = False)` / `triggered(bool checked = False)`
    —— **槽的第一个位置参数**收到的就是这个 `False`。而这些槽的第一个参数
    从来不是布尔（路径 / 画布坐标 / 节点 id）。后果分两种，**都真实发生过**：

      · **静默走错分支**：`if target is None:`（不是 `if not target:`）判不出
        `False` → "没给路径就弹另存为"被**绕过** → `False` 当路径送下去。
        2026-10-04 就是这么在仓库根目录留下一个 178 字节、名叫 `False`
        的控制流脚本（`docs/archive/交付审查_2026-10-05.md` §8）；
      · **当场炸**：`add_node(at=False)` → `float(False[0])` 抛 `TypeError`。

    三层，缺一不可：

      · **接线**：`clicked.connect(lambda: self.槽())`；工具栏统一走
        `act()`（它内部包了一层 lambda）。**这一层是根本**；
      · **审计**：`tools/audit_qt_signal_slots.py` —— 静态判据"不许裸
        `connect(self.槽)`"，跨文件解析（槽常在别的 mixin 里），`act()`
        那条包装也一起认。跑在 `tests/test_qt_signal_wiring.py` 里；
      · **兜底**：`ui_qt.qt_args.drop_qt_bool(value, context=…)` —— 槽的入口
        把 bool 当"没给"（**自愈**：该弹对话框就弹），并记一笔 `swallow`
        （有痕迹、不打扰用户）。只丢**布尔**：别的错类型照旧往下走、该炸就炸
        （会话层另有 `isinstance(target, (str, Path))` 硬护栏，绝不拿非路径碰磁盘）。

    ⚠ **别把原因写成"`wrapper` 是第二个位置参数、它落进了 `target`"**
    （有个报告和一份测试 docstring 都这么写过）：菜单项的调用是
    `export_control_flow(wrapper=True/False)`，**关键字传参按名字绑定**，
    位置排第几都不相关；那两行一直是对的。`wrapper` 改成关键字专用只是加固。

    ⚠ 改签名要**扫全部调用方**：那次加固就把黄金快照驱动
    （`_public(win, 'export_control_flow', str(cf), None)`）打断了，
    `TypeError: takes from 1 to 2 positional arguments but 3 were given`。

    判据：`tests/test_qt_signal_wiring.py::TestQtBoolIsNeverAnArgument`（5 条，
    变异去掉兜底 → 4 红）+ `tests/test_control_flow_target.py`（判据层/签名层）
    + 探针 `tools/probe_flow_export_bool.py`。

74. **结构位（菜单选项体）对不上时**必须报出来 —— 但**只 warn、不拦**
    （2026-10-05，交付审查 A 档；方案见
    `docs/archive/嵌套与行尾注释_修复方案_2026-10-05.md`）。

    **病**：全量重生成会把"选项体里有第二条语句 / 嵌套块"的菜单**压平** ——

        menu:                        menu:
            "选项":          →           "选项":
                if flag:                     pass        ← 选项体被掏空
                    e "选了"         if flag:           ← 提到 label 级：
                e "收尾"                 e "选了"         **选没选都会执行**
                                     if True:
                                         e "收尾"

    产物语法合法、能跑、**`validate` 一句话都不说**（2026-10-05 实测 `exit=0`）。
    根子是"这条拍属于哪个选项"**没进表**：`_menu_member` 只活在 .rpy 侧，
    表里只有 `stamp_menu_bodies` 打的 `Condition = True` 这一个 **1 bit** 的信号
    （分不出哪个选项、也分不出多深）。

    **判据**（`generation_guard.menu_structure` / `structure_loss`）：源与产物
    都用现成的 `RenPyLexer` 解析，逐 label 比"**每个菜单的每个选项体形状**"
    `(语句数, 块数)`。
      · **只比菜单**：纯 `if` 嵌套走"条件合取"近似（`if a:` 套 `if b:` →
        `if (a) and (b):`）是**设计内**的，真实项目 109 处 —— 比它会刷屏；
      · **两个数一起看**：语句数变 = 内容被搬走（`pass`），块数变 = 嵌套被压平；
      · 文本改写不影响它们 → 改台词不会误报。

    **为什么是 warn 不是 block**（这条取舍别改回去）："结构变了"有两种可能 ——
    被压平（缺陷）、或**人有意**在表里/卡片里给某个选项加了内容（正常操作）。
    只比"源 vs 产物"分不出这两者，而"假阳性比漏报更贵"——拦错一次，人就会
    学会忽略这条警告。等结构位进了表（方案 B），判据才能变成"产物 vs 表里说的
    结构"，那时才可以升成 block。

    **生效范围**（老实说）：只在"**有源可比**"的路上判 —— CLI `--source`、
    编辑器（`script_path` 存在）。纯"表 → 新文件"没有参照，它判不了；
    这条缺口要等方案 B 才真正修掉。

    判据：`tests/test_structure_gate.py`（12 条：三个最小形状、**纯 if 近似
    不许报**、缺 label 不重复报、warn 之后照写、没源不判、CLI summary 里看得见、
    真实项目"该报的两处报、其余安静"）。
    现场证据：`tools/probe_structure_gate_cli.py`（真 CLI：给源导出时打出
    `[warn] STRUCTURE_LOST … 原文各选项体 [((6, 2),)] / 产物 [((4, 1),)]`，
    产物照写）；`tools/probe_nesting_min.py`（三个最小复现）。

75. **结构位（`Nest` 列）随表走 —— 阶段一只"双写 + 对拍"，不切换**
    （2026-10-05，B 阶段一；方案见
    `docs/archive/嵌套与行尾注释_修复方案_2026-10-05.md` §3）。

    **病**（与不变量 74 同一件事的两个面）：`menu` 在 Ren'Py 里是**嵌套块**，
    而拍是扁平的。"这条拍属于哪个选项"以前只有两个信息源 ——
    `_menu_member`（`.rpy` 侧，**进不了 Excel**）与 `Condition` 串 + 行号
    （表格侧，**只有 1 bit**：在选项体里 / 不在；分不出**哪个**选项、
    也分不出多深，选项体一复杂信号就塌）。

    **这一版做了什么**（`SECOND` 让"结构位"成为第三个、也是最完整的那个）：

      · 解析器在每个 `Statement` 上写一条**块路径**（动态属性，不是 dataclass
        字段 —— Statement 的相等/哈希是"内容指纹"契约的一部分）；
      · DSL 唯一定义在 `core/nest_path.py`（解析与渲染都要用，跨包不许 import
        私有名）：`''` 顶层 / `if:1` / `if:1>menu:2>A>if:3`，选项体用**位置键**
        `A`/`B`（用位置而不是文案：改文案不该算"结构变了"）；
      · 它随隐藏列 `Nest` 进出 Excel（**人可读**，出问题能人工核对）；
      · `beat_build` 把它转发到 `Beat.nest_path`；`None` = **不知道**
        （老表 / 手工行），`''` = 顶层 —— 两者**必须分得开**，否则对拍会把
        "不知道"当成"在顶层"，凡是老表都天天假报；
      · **对拍器** `beat_render.nest_placement_conflicts`：结构列说的位置 vs
        老推断**实际**摆的位置（与渲染同源：`split_beat_units` +
        `_menu_segments`，嵌套菜单递归数序号），不一致报到**拍级**；
      · 结论走门禁通道：`NEST_CONFLICT`（**warn**）→ `CompileResult.guard_warnings`
        → CLI summary；编辑器那条接进 `export` 的 `note` +
        `structure_conflicts` 计数。

    ⚠ **阶段一是"只双写、不切换"**：渲染**仍然**按老推断走，只是"把话说出来"
    —— 刚验收过的往返契约不该在同一轮里被换掉。**阶段二（不变量 76）已经切了**。
    实测（`tools/render_hash.py`，40 个真实文件、两条路各打一份哈希）：
    改前 / 改后**逐字节一致**（直接渲染 `3a8e365eaf99`、过表渲染
    `66a0d82e34ac`）。

    **阶段一的验收判据**（`tests/test_nest_column.py::TestRealProjectAgrees`）：
    40 个真实文件里，**老推断与结构列处处一致**、且"有源行号的拍都有结构位"
    —— 实测两者都是 **0**。全绿 → 阶段二（让渲染改用结构列）才安全。

    判据：`tests/test_nest_column.py`（13 条：路径的文档序 / 选项行与会话体的
    分界 / 嵌套菜单的外层链 / 不是 dataclass 字段 / 列的三种取值与往返 /
    脏值算"不知道"且留痕 / 一致时不报 / 老表整份跳过 / 谎报报到拍级 /
    往返之后老那位没了仍一致 / 真实项目 0 处）。变异：`body_chain` 恒返回
    `()` → **3 条红**。

76. **渲染器优先用结构列建树，缺信息就整份退回老推断**
    （2026-10-05，B 阶段二；接不变量 75）。

    **怎么判**（`split_beat_units` / `_menu_segments`）：

      · 某个 label 的拍**结构位齐全** → 菜单组边界 = "块路径在同一棵子树里"，
        选项归属 = 块路径里的**位置键**（`_take_menu_run_by_path` /
        `_menu_segments_by_path`）。`menu:n` 的 `n` 是 label 内**第几张
        `KIND_MENU` 拍**（与解析器 `_walk` 的文档序计数同一套）；
      · **有一条 `nest_path is None`**（老表 / 手工加的行）→ **那一份整份**
        退回老推断。粒度是 label，不是单拍 —— 混着用会出现"半截菜单"，
        而多吞/少吞一拍都是静默的剧情错误。

    ⚠ **两处最容易写错、都踩过**：

      · `_menu_segments_by_path` 必须**在开头整组查 `None`**。第一版只在
        "算前缀"那一趟里查，后面的落拍循环把 `None` 走成 `str(None)` =
        字面量 `'None'` → **静默跳过那一拍**（
        `test_inserting_a_beat_between_options_keeps_the_menu` 当场抓到）；
      · 写"同类连续成段"的渲染循环时，累加变量**别叫 `run`** —— 那是
        `_render_menu_segments` 的参数（整个菜单组），同名会把它覆盖成空列表，
        下面 `run[0]` 直接 `IndexError`。

    **顺带修掉一个换序**：选项体原本是**两趟**渲染（先把"条件是累积条件"的拍
    整批铺平，再补"真嵌套 if"），交错时会把 `if` 挪到块体末尾（改剧情）。
    现在按**源码顺序**逐拍渲染（同类连续成段交给 `_render_unit_lines`）。
    真实项目实测（`tools/probe_menu_order.py`）：20 个选项体里**交错 0 个**
    —— 所以这一改**不影响任何真实产物**（哈希不变）。

    **对拍器要显式走老路**：`split_beat_units(..., use_paths=False)` /
    `_menu_segments(..., use_paths=False)`。`nest_placement_conflicts` 用它算
    "老推断本来会怎么摆" —— 不这么做就成了拿路径比路径、永远 0 冲突，
    对拍器**失去意义**（`test_a_lying_column_is_reported_per_beat` 当场抓到）。

    **验收**：
      · **零行为变化**：`tools/render_hash.py` 对 HEAD（`876bcc2`）比对，
        40 个文件、两条路**逐字节一致**（`3a8e365eaf99` / `66a0d82e34ac`）；
      · **老推断摆错的地方修对了**：`menu>if`（选项体里第二条语句 / 嵌套块）
        以前产出 `"选项": pass` + 内容提到菜单外（**选没选都会执行**），
        现在内容留在选项体里、顺序与源码一致；`if>menu` 的那句也不再被吸进
        选项体（`tools/probe_b2_shapes.py` 的三个形状，源与过表两条路都验）；
      · 测试 **2347 passed / 2 skipped**；真实项目体检（`journey_check` /
        顶层保真 / label 往返 / 单人工作流）全部通过。

    （阶段三已把 `stamp_menu_bodies` 退掉，见不变量 77。）

    判据：`tests/test_nest_phase2.py`（9 条：按路径切真的生效 / 一条不知道
    退回老路且**不许丢拍** / 三个坏形状修对且顺序对 / 过表之后也对 /
    抹掉结构位退回老形状（不假装能修）/ `use_paths=False` 等价于"全都没有" /
    对拍器仍能看出"表里说的不对"）。变异：关掉按路径切 → **过表那条红**
    （正是老推断丢信息的地方）。

77. **不再给菜单选项体补 `True` 条件**（B 阶段三，2026-10-05）

    从前 `excel_export` 写表前会调 `stamp_menu_bodies`：把**选项体里条件为空**
    的拍补成恒真 `True`。原因是**表里没有"这拍在菜单块里"这一位**，只能拿
    `Condition` 记 1 bit（`True` = 在某个选项体里 / 空 = 块外），
    否则回导之后选项体分不出块内块外（P0-1 的最后一环）。

    块路径（隐藏列 `Nest`）随表走、渲染器也按它建树（不变量 75/76）之后，
    这一位**多余**了；留着还有两个坏处：

      · 表里多出一堆 `True` —— 策划看见会以为是自己写的；
      · 产物里多出一层 `if True:`（语义等价但难读），而且让"过表"与
        "直接渲染"两份产物**对不上**。

    现在删掉函数本体与调用点。三条结论都实测过（`tools/probe_b3.py`）：

      · 表里 `Condition` 列**一个 `True` 都没有**（新表）；
      · **产物零变化**：`tools/render_hash.py` 对阶段二比对，40 个文件、
        两条路**逐字节一致**（`3a8e365eaf99` / `66a0d82e34ac`）——
        因为那层 `if True:` 本来就等于"累积条件"、渲染时会被剥掉；
      · **老表照样能读**：把 `Nest` 列删掉、按老口径补 `True`（只补选项体里
        条件为空的那些）→ 渲染仍然正确（内容在选项体里、菜单之后那句在菜单外）。

    **顺带补上一个阶段二漏掉的坑**（`test_deleting_an_option_keeps_the_rest`
    当场抓到）：选项体与选项行的配对**不能按键的字母**（`A`/`B`…）——
    键是解析器按"当时第几个选项"写进表里的，而**表可以被编辑**：
    删掉选项 A 之后，存下来的键还是 `B`，可它现在排第 1 位。
    现在按**出现顺序**配对（键对不上就找第一个还没认领的段，实在放不下
    才退回老推断）。这也是"结构位"设计的边界：**键表示"当时的位置"，
    不表示"永久身份"**。

    判据：`tests/test_nest_phase3.py`（7 条：不再打标 / 产物里没有 `if True:` /
    **过表产物 == 直接渲染** / A 档门禁在过表产物上不再响 / 连写两次表收敛 /
    老表（无 `Nest` + 有 `True`）仍渲染正确）。

78. **行尾注释随表走**（隐藏列 `Inline`）—— 见不变量 75/76 之后的 C 阶段
    （2026-10-05）。

    **病根与"嵌套"那条不同**：`RenPyLexer._strip_inline_comment` 在**词法器**
    就把注释扔了 —— IR 里根本没有这个字段，下游怎么改都没用。
    这不是"表里没地方放"，是"**源头没留下**"。

    现在四层都接上（都只加、不动既有语义）：

      · **词法器**：`LogicalLine.inline_comment` —— **只认单行逻辑语句**；
      · **语句**：`Statement.inline_comment`（动态属性，与 `nest_path` 同款：
        Statement 的相等/哈希是"内容指纹"契约的一部分，不许进去）；
      · **拍 + 表**：`Beat.inline_comment` + 隐藏列 `Inline`
        （`BEAT_FIELDS` 25 → 26；在 `SINGLE_LINE_BEAT_FIELDS` 里 ——
        注释原文会被压成一行并**留痕**，被清洗过就**丢掉**这条注释）；
      · **渲染**：贴回**原来那一行**。

    **规矩是"说不清就不贴"**（贴错比不贴更坏）：

      · 多行逻辑语句（括号续行 / 反斜杠续行）**不采集** —— 注释挂在哪一行说不清；
      · 一拍渲染出多行时，**只有"前面几行都是块头（以 `:` 收尾）"才贴最后一行**
        （条件拍 `if cond:` + 台词 就是这一形态，真实项目那 1 条正是它）；
        别的多行形态（舞台指令 + 台词、多行代码）不贴；
      · 单元格被清洗过就丢掉。

    **实测**：
      · 真实项目 label 内"带注释的拍" **4/4 全保住**（改之前过表必丢、改过的行也丢）；
      · `tools/probe_nesting_inline.py` 的全量产物里，那两条注释（台词 + 选项行）
        **都贴回了原行**；
      · 原地增量：没改的行由源文件原样保留、改过的行由生成器写出 →
        **每条注释恰好出现一次**（`TestPatchPath` 钉着）。

    判据：`tests/test_inline_notes.py`（15 条：词法器单行/多行/不动 `text`、
    台词/跳转/选项行都带上、列的往返与"清洗过就丢"、贴在正确行、
    多行拍不贴、**过表产物 == 直接渲染**、渲染两遍不翻倍、原地增量不重复、
    真实项目 4/4）。

    **收尾第 2 批**（2026-10-05，用户："这几个剩下的一并修复了吧"）又补了三处，
    这时候 `python:` 块体/续行/条件行也都保住了：

      · **`python:` 块体里逐行**的注释：块体文字是拿 `.text`（注释已被剥）拼的
        → 拼装时把每行自己的那份带上（块体里每行都是**独立逻辑行**）。
        真实项目实测 7 行 → 0 行；
      · **括号续行**里的注释（`positions = [` … `]` 会被并成**一条**逻辑行）：
        `LogicalLine.inline_comments` 是一张**逐物理行**对齐的表，
        块体拼装时按行贴回去（行数对不上就不贴）。真实项目 4 行 → 0 行；
      · **`if / elif / else` 头那一行**的注释：`if` 不是拍、它是拍上的**条件**，
        所以另开一个槽 `Beat.cond_comment`（`IfNode.branch_comments` 按分支记），
        渲染时贴到这一拍产出的 `if …:` 那一行。两处注释共用一个 `Inline` 单元格，
        用私有区分隔 + `@if ` 前缀消歧（自己的注释一定以 `#` 开头，不会撞）。

    **反斜杠续行**呢？它是用**空格**拼成一条逻辑行的 —— 逐行表用不上，但拼出来
    的那一行本身就是单行，注释跟着它走，所以那一条注释照样保住
    （真正会丢的只有"一条逻辑行挂了**多**条注释"，反斜杠拼法做不到）。

    ⚠ **剩下的已知边界**（写清楚，别当成 bug）：真实项目里 `if` 头注释共 4 处，
    **全都在顶层 `screen` 块里** —— 那些本来就由文件级逐字保真扛着，产物里一条
    都没丢；所以这一条通道的真实暴露面是 **0**，补它是为了把这一类关掉
    （label 里的 `if` 头注释以前会丢）。

79. **结构编辑可以多级撤销**（插卡 / 删卡，2026-10-05）

    改之前：`delete_beat` 只留 `_last_removed`（**单级**），而任何一次结构编辑都会
    `forget_undo()`（内容撤销栈作废）—— 连插两张卡再想退回第一步就没路了。

    现在会话里有一个**有界**（50 步）的结构撤销栈 `_struct_undo`，记的是
    "**做了什么**"而不是快照：

      · 插卡 → `('insert', 落点)`，撤销 = 删掉那一拍；
      · 删卡 → `('delete', 原拍号, Beat)`，撤销 = 原位插回；
      · 「恢复删除」本身也是一次结构操作，所以它也能被再撤销（可连按）；
      · 结构编辑**不**清这个栈（拍号没变，索引依然有效）；内容编辑也不动它；
      · `load()`（换文件 / 原地导出后重新对齐）**清空** —— 索引全变了。

    ⚠ **Ctrl+Z 刻意不接它**：一个键盘动作顺手把卡片删掉/放回来太意外
    （`test_main_window_snapshot` 第 56 步当场抓到"连按撤销凭空多一张卡"）。
    结构撤销只走「↩ 撤销结构操作」那颗按钮（按钮可用性 = 结构栈非空或刚删过东西）。

    判据：`tests/test_struct_undo.py`（10 条：连插两次逐级撤、连删两次按逆序撤、
    插删混用、栈有界、重载清栈、内容编辑不动它、老的单级 API 仍可用、
    「恢复删除」可再撤、按钮走结构栈、Ctrl+Z 不动结构）。

## 写者约定（并发）

这个工具是**单写者**模型：`.rpy` 是唯一真源，导出是"写整个文件"或"原地补丁"。
所以：

  · **同时开着两个实例改同一章** → 后写的那次会盖掉前一次（没有锁，也没有合并）；
    要并行改不同章节时，各开各的**文件**，别交叉；
  · **原地导出之后必须重新读盘**（`_reload_after_inplace_write`）：内存里的拍
    已经被重排/重编号，不重读就会用旧的拍号去补丁下一刀；
  · 编辑器里**不要手工改生成物**的头两行标记 —— 那是"这是生成物"的判据，
    删了会被当成手写文件重新猜结构。

80. **没证据就别改：图像名与舞台顺序**（2026-10-05，用户实测报到后修）

    这两条是同一类错误的两个实例 —— **"降级"其实是在破坏**。

    **(a) 图像名不许"空格换下划线"**（`beat_render._emit_image_name`）

    从前 `resolver is None` 时返回 `name.replace(' ', '_')`，注释里写的是
    "保持旧行为，兼容单测与无资源表场景"。**它不等价**：真实项目
    `definitions.rpy:111` 定义的是 `image bg bg1_1`（Ren'Py 原生
    **tag + attribute**），全工程 `grep bg_bg1_1` **0 命中** —— 于是
    `scene bg bg1_1` 过一趟表变成 `scene bg_bg1_1`，**运行时找不到图**
    （实测 7 个文件 17 处）。而且它会跟 `with` 抢词：
    `scene bg bg1_1 with dissolve` 再解析一轮只剩 `scene bg_bg1_1`。

    现在的规矩：**没给 resolver → 一个字都不改**（作者写的就是工程在用的）；
    给了 resolver → 优先作者形态，解析不了才试下划线形态。
    "该提醒"的是"资源表里找不到这个名字"，那件事由 `audit.image()` 负责，
    与这里不重复 —— 改成原样保留之后这条路**没有发生降级**，不需要提醒。

    **(b) `StageOrder` 不许被 `Stage` 列推导的顺序覆盖**（`beat_build._hydrate_stage`）

    `Stage` 列里**没有** `scene` 那条（`scene` 存在 `Scene` 列），拿它推导顺序
    必然少一个 `scene` → 渲染时 `with` 从句就挂到 scene 行上（语义不一样：
    `scene X with D` 与 `show X with D` 是两种转场）。修法：**已有的顺序保留**，
    只把 DSL 里新出现、原顺序里没有的 verb 补在后面（老表没有这一列时行为不变）。

    **(c) 原生舞台尾段已经带了同一个转场时，不许再挂一次**（`_stage_lines`）

    作者写在 `Stage` 列的那一行会**逐字**输出一次，而 `beat.with_` 同时也等于
    那个转场 —— 再按"有背景就挂背景"挂一次，产物就成了同一拍两条转场
    （`scene … with D` + `show … with D`）。判据用**原文**（`beat.stage`）。

    **验收**：`tools/hell_nest_inline.py` 的 `scene_show_tag_attr` 用例
    与**真实项目全部 40 个文件**由 FAIL 转 ok（那 17 处就是这三条）。
    改掉的旧契约测试见 `test_fidelity_bytewise::test_L3_1_image_form_is_preserved_without_registry`
    （原名叫 `..._differs_without_registry`，钉的正是这个"差异"）。

    ⚠ **欠账**（同日记录，未修）：`tools/hell_nest_inline.py` 的嵌套菜单用例
    （菜单套在选项体里）**渲染**仍然不对 —— 结构位（`Nest`）已经正确、
    选项行也不再丢，但那一层菜单会被渲染成外层缩进/变成一句台词。
    真实项目 **0 个嵌套菜单**，所以没有实机影响；诊断见
    `beat_render._menu_segments` 那支"带嵌套菜单的选项体"上面的 `KNOWN` 注释。

81. **嵌套菜单（菜单套在选项体里）能渲染了，且要几层有几层**
    （2026-10-05；用户："我要的不只是对拍，而是实际生成的无限嵌套可行"）

    **以前这条路整条是坏的**（`tests/` 里一条嵌套用例都没有，所以一直没暴露；
    真实项目 **0 个嵌套菜单**，真实数据也回归不到）。三个缺陷叠在一起：

      1. **选项行被静默丢掉**：`_menu_segments_by_path` 第二趟写的是
         "凡 `KIND_MENU_OPTION` 一律跳过" —— 嵌套菜单的选项行（路径
         `menu:1>A>menu:2`）因此消失，菜单头还在、选项行没了，那层塌进外层体；
      2. **扁平渲染**：选项体把"非记账条件"的拍一律交给 `_indent_beat_lines`
         （逐拍），嵌套菜单的头/选项行永远走不到递归的 `render_menu_block`
         —— 于是渲染成一句台词（`"菜单"` / `"内"`）；
      3. **编号对不上**：`_nest_plan` 用"数第几张 `KIND_MENU`"编号，可它会
         被递归调用在**子列表**上（选项体那一段），局部次序与 label 文档序
         不一致 → 切组只切到"菜单头"一拍（`tools/probe_nested_render.py`
         的调用轨迹原样抓到：`render_menu_block(indent=8 拍数=1)`）。

    **怎么修的**（都在 `storygraph/story/beat_render.py`）：

      · `_nest_plan` 的菜单编号改成**从块路径推**（`_menu_number_from_path`：
        头后面第一条以 `头路径>menu:` 开头的拍，取那一段的 `n`）——
        label 级唯一，所以子列表里也对；
      · 选项体只有**一条**渲染路：把"累积条件记账"那一层剥掉，**整段**
        交给递归渲染器（`split_beat_units` → `render_menu_block`），
        认不出归属的拍在原位置插一句显式 TODO；
      · 缩进口径统一：选项体自己按 `inner` 补前缀，菜单块用
        `render_menu_block(group, indent='    ')` 渲染成相对形式 ——
        于是"菜单头 / 选项 / 选项体"永远是 `inner` / `inner+4` / `inner+8`，
        **嵌套任意深**；
      · 对拍器（`nest_placement_conflicts`）**按 label 作用域**跳过有嵌套菜单的
        那一份：老推断只有"在不在选项体里"1 bit，遇到嵌套会**整份**摆错
        （连嵌套菜单**之后**的拍都算进假选项里）。那不是"结构位说谎"，
        是它没有这个能力 —— 假阳性比漏报更贵。真实项目 40 个文件全是单层，
        判据在那里照旧严格。

    **验收**（`tests/test_nested_menu_render.py`，16 条）：

      · 两层最小复现**逐字**比期望产物；两条路（直接渲染 / 过表）逐字节一致；
      · **深度 2/3/4/5/6 层**参数化：每层选项行比上一层深 8 格、菜单头深 4 格、
        最里面那句最深 —— 这就是"**要几层有几层**"的判据；
      · 深 3/5 层是**不动点**（产物再解析再渲染逐字节不变）；
      · 与 `if` 交错（`if → menu → 选项 → if → menu`）：两级都在、缩进对、
        两条路一致；菜单整块挂 `if` 时 `if` 在 `menu:` **外面**（浅 4 格）；
      · 真实项目产物**逐字节不变**（`tools/render_hash.py` 对前一版比对：
        `212a48c5b565` / `f8f75139b8e4`）—— 因为真实项目没有嵌套菜单。
        ⚠ 那两个值是**当时**的；同日晚些时候修完不变量 82 之后，
        它们变成 `8eea612fe151` / `8eea612fe151`（两条路终于完全相等）。

    **条件不许翻倍**（同日修掉，N2 不动点）：解析器把外层条件 `_combine` 进了
    里层每一拍，而外层那个 `if:` 往往**已经由结构写出来了**（菜单块自己的条件
    写在 `menu:` 外面）。里层再写一遍 `(flag0) and (flag2)`，产物再解析就会被
    **再累加**一次 → `(flag0) and ((flag0) and (flag2))`，每轮更长。

    修法：`strip_condition_context(cond, context)` —— 选项体里"要保留自己条件"
    的那些拍，渲染前**减去已经写在结构上的那部分上下文**（按合取项比，两边都
    脱掉包裹括号再比字面量）；减不干净就**原样返回**（宁可多写一层，也不许
    漏掉一个合取项 —— 漏了就是"该满足条件才播的变成无条件播"）。
    只剩一项时脱掉包裹括号，让"渲染 → 解析 → 渲染"**收敛**（`_combine` 每轮
    都会再包一层，不脱就永远多一层括号）。

    实测：`if flag0:` → `menu:` → 选项体里 `if flag2:` 的产物现在是
    `if flag0:` / `if flag2:`（从前里层写成 `(flag0) and (flag2)`），
    R1 == R2 **逐字节**；真实项目产物**不变**（`212a48c5b565` / `f8f75139b8e4`，
    同日晚些时候修完不变量 82 之后变成 `8eea612fe151` / `8eea612fe151`），
    hell 失败 11 → **8**，全量 2406 → **2425**（新增 19 条）。

    ⚠ **当时还没修的三条**（同日记录）→ **已全部修完**，见不变量 82：
    `empty_option_body`、`depth5_imimi`、`chapter1_2.rpy` 的 N3；
    并且"5 处 harness 过严"在校准判据之后查出**其中一处是真缺陷**
    （注释改写它所搭载那一拍的结构位）。

82. **三条收尾缺陷修完 + harness 判据校准**（2026-10-05，接不变量 81 的欠账）

    上一轮留了三条（① `depth5_imimi` 的 N2 不动点、② `empty_option_body` 的
    结构位多一条、③ `chapter1_2.rpy` 的舞台指令顺序），并把真实项目 5 处
    N1 归给"判据过严"。这一轮三条都修完了，而且**校准判据之后发现
    "过严"里藏着一处真缺陷**。五件事：

    **(a) 舞台尾段排在代码块之前**（`beat_render.render_beat_to_rpy`）

      `chapter1_2.rpy` 拍 136/152 的原文是
      `hide ye` / `show cg cg02_2 at cg38402160 behind ye` / `$ persistent…`。
      从 .rpy 直接渲染时那条复杂子句 `show` 由**结构化列**出（在代码之前），
      过一趟表之后 `hydrate_from_dsl` 把它标成"原生真迹的影子"、结构化通道
      跳过它，改由**舞台尾段**出 —— 而尾段当时排在 `emit_code()` **之后**，
      于是 `$` 跑到了 `show` 前面。**条数一模一样，只有顺序变了**，
      与"没有顺序信息"那条路（`emit_stage()` → `emit_code()`）也不自洽。

      修法两处：尾段回归舞台一侧（`emit_stage_tail()` 提到 `emit_code()`
      之前）；"代码块跟在第一条 `scene` 之后"的触发条件从"**token 名叫
      `scene`**"改成"**这一支真的往产物里加过行**" —— `tokens` 会被补齐
      （`missed_stage` 把没记的 `scene/show/hide` 一律追加到末尾），
      于是**没有背景的拍**也会走过 `scene` 那一支，代码块被提前到一个
      **没出任何行**的位置。

      判据：真实项目 40 个文件两条路**逐字节相等**（`tools/render_hash.py`：
      直接 = 过表 = `8eea612fe151`；从前是 `212a48c5b565` / `f8f75139b8e4`）；
      `tests/test_fidelity_bytewise.py::TestCodeBlockStaysAfterTheWholeStage`。

    **(b) 空选项体不再让两个选项体对调**
        （`core.nest_path.option_index` + `beat_render._menu_segments_by_path`）

      `menu:` 里第一个选项体是空的时候（真实项目 `credits_screen.rpy` /
      `mini_game_puzzle.rpy` 同型），块体的位置键会"缺一个"，而"键 → 选项"
      从前**只按出现顺序**配（`[B]` → 第 1 段）→

          "空的":   e "x"      ← 本该属于「有内容」
          "有内容": pass       ← 本该属于「空的」

      **选"空的"播了台词、选"有内容"什么都不做**（静默改剧情）。
      修法：位置键本身就是位置（`option_key` 的逆函数 `option_index`）——
      **全部**键都能解成"落在选项数之内、互不重复"的序号时按键配对；
      只要有一个解不出/越界/撞车（表被人工编辑过、选项被删过）就整体退回
      顺序配对（`test_deleting_an_option_keeps_the_rest` 那一档照旧）。

      判据：`tests/test_option_body_attribution.py`（空体在前/中/后 ×
      直渲/过表 × 两条路逐字节 + 键与逆函数 + 越界退回顺序）。

    **(c) 注释不许越过分支、更不许改写结构位**（`beat_build._emit_comment`）

      注释从前是唯一**不过 `branch_guard`** 的语句，于是顶层注释会被吸进
      上一张条件拍的 pending 里：那一拍的 `Nest` 被注释的路径**覆盖**
      （条件还是分支的、位置成了顶层），注释在产物里还被写到 `if …:`
      **前面**（从"分支之后"漂到"分支之前"），产物再解析时又被拆成单独一拍
      —— 这一条正是真实项目 N1 里最后一处"菜单口径也解释不掉"的差异
      （`mini_game1.rpy` 拍 12）。

      修法：注释也过 `branch_guard`（**条件不同的语句不许并进同一拍**）。

    **(d) 条件减法要认括号**（`beat_render.strip_condition_context`）

      条件是被 `_combine` **层层包起来**的（`((flag0) and (flag2)) and (flag4)`），
      而"减掉外层已经写过的合取项"从前用字面 `split(' and ')` 切 —— 会切进
      括号里面，两个后果：

        · 永远减不掉 → 条件每轮翻倍（`depth5_imimi` 的 N2 不动点过不去）；
        · 拼回去**括号不配对**：实测产出过 `'(((flag0) and (flag4))'` ——
          那玩意儿直接写进 `if …:` 就是产物语法错。

      修法：`_split_conjuncts` 只切**顶层**（括号深度 0）的 `and`；
      `_bare_condition` 只脱"配对右括号就在末尾"的整层括号；
      `_condition_atoms` 递归拆括号、但把 `not (…)` / `(…) or (…)` 当
      **一个**原子项；残余项在合取式里**该带括号就带**（`x and (a or b)`
      不许被拉平成 `x and a or b`，那等于 `(x and a) or b`）。
      顺带把**两份各自递归的 `_condition_atoms` 合成一份** —— 其中一份遇到
      `not (a and b)` 会**无限递归**（`RecursionError`）。

      判据：`tests/test_nested_menu_render.py::TestConditionContextIsNotRepeated`
      （嵌套合取、`not`/`or` 原子、括号平衡、无限递归回归、5 层不动点）。

    **(e) harness 判据校准：N1 只比"菜单口径"**
        （`tools/hell_nest_inline.py`）

      上一轮把 5 处真实项目 N1 归给"判据过严"，方向对了一半 ——
      校准之后是**两处过严 + 一处真缺陷**（就是上面那条注释改写结构位）：

        · **`if:<n>` 的文档序不进判据**（§有意为之 5）：那个 `n` 是"这一份
          文本里第几个 if 块"的序号，而 `elif/else` 展平成多个 `if`
          （§有意为之 1）之后块数必然变；`if:` 段**不参与渲染**
          （`if` 从 `Condition` 列来），它的保真由 N2/N3 逐字节盯着；
        · **空块补的 `pass` 两边都不计**（§有意为之 4）：它在产物里是一张
          普通拍（`raw='pass'`），从前正好把 (b) 那处**真的**错位
          **掩盖**掉了 —— 源里 `menu:1>B`（台词）与产物里 `menu:1>B`（pass）
          在多重集里互相抵消，只剩一条"多出来的 `menu:1>A`"；
        · 校准后仍然只有**菜单/选项体归属**是硬判据：那才是承载语义、
          也是 `STRUCTURE_LOST` 门禁守的东西。

      校准后：合成用例 **17/17** 绿、真实项目 **40 个文件全绿**；
      另外把"`if` 文档序变了但菜单归属一致"的文件**单独提示出来**
      （不计失败），免得真出了 if 结构问题、被当成"这条判据本来就不看 if"。

    **仍然存在的已知边界**（都是**文本形状**差异，语义等价，不是数据丢失）：

      · 相邻同条件拍在产物里写成**多个** `if <同一条件>:` 块（原文是
        一个块体里的两张拍）；合并它们属于排版优化，不在这一轮；
      · **同类内实例级**交错仍然丢失（`op_order` 是 verb **集合**）：
        `show a / play music m / show b` 里 `play` 的位置还原不了。
        `tools/probe_stage_code_source_order.py` 实测真实项目另有
        **35 张拍**属于这一类（见不变量 81 的
        `test_audio_between_two_shows_is_a_known_gap`）；
      · `python:` 块**续行**的缩进每往返一轮大一档（纯排版，
        见 `_indent_beat_lines` 的说明）。

    **这一轮的取证工具**（都不进 CI，按需跑）：
    `tools/probe_remaining3.py`（三条的回归钉 —— 已从"诊断"改成"断言"，
    退出码 0/1）、`tools/probe_chapter_stage_loss.py`（③ 的字段级取证）、
    `tools/probe_beat_order.py`（同一张拍在两种身份下为什么排出不同顺序）、
    `tools/probe_depth5_cond.py`（① 的条件逐轮长大）、
    `tools/probe_n1_paths.py`（N1 两边逐条对照）、
    `tools/probe_stage_code_source_order.py`（源码顺序 vs 渲染顺序的分布）、
    `tools/snapshot_beat_render.py`（改渲染前 dump 逐拍产物、改完 diff）。

83. **编辑序列（第三轮地狱）× 三条编码缺陷**（2026-10-05）

    第三轮地狱测试换了轴（`tools/hell_edit_rounds.py`）：前两轮都是"读一次、
    渲染一次、比一次"，这一轮打**人在反复改**（原地导出 10 轮、内容步子只许动
    那一两行、最后"不改任何东西再导出必须逐字节相同"）× **工具自己的编码**
    （`Inline` 的 `\ue004` 分隔符 / `@if ` 前缀 / `Nest` 的 `>`）。
    它打出三条，全部修完：

    **(a) 菜单标题的转义必须自反**（`renpy_parser._scan_menu_header`）

      `_quote(caption)` 会转义（真换行 → `\n`、`\` → `\\`），而标题那条路
      **原样切片**：`menu "第一行\n第二行":` 读回来成了**字面量反斜杠 + n**，
      再导出又转义一层 —— `\n` → `\\n` → `\\\\n`，**每渲染一次翻一倍**。
      台词那条路一直是对的（`_pick_str` 内部调 `unescape_renpy_string`），
      所以只有**标题**不对称（选项文案走 `_parse_menu_option` 的 `_pick_str`，
      本来没问题）。影响不只是显示：标题里的 `\n` 强制换行退化成字面量，
      而且那一行**每轮都变** → 增量的对位每轮都失败（注释丢失的上游）。

      判据：`tests/test_renpy_parser.py` 两条（标题反转义）；
      `tests/test_roundtrip_invariants.py::test_menu_caption_escapes_do_not_accumulate`
      （往返 + **第二轮逐字节不动点**，三种转义形态）。

    **(b) 位置不同的语句不许并进同一拍**
        （`beat_build._BeatBuilder.struct_guard`）

      这是这一轮最贵的一条。报到的形态是"选项文案带换行 → 第二轮选项体变
      `pass`、丢内容"，探针（`tools/probe_body_leak.py`）打出的是同根因的更狠形态：

          label a:
              menu:
                  "选项":
                      show bg room      ← 路径 `menu:1>A`（选中才播）
              e "体"                    ← 路径 `''`
              return

      两句**条件都是空串**，而 `branch_guard` 只比条件 → 并成**一张拍**；
      一张拍只有一个结构位，`mark_struct` 写的是**最后那条**语句的路径 →
      整拍变成顶层 → 产物成了

          menu:
              "选项":
                  pass              ← 选项体被掏空
          show bg room              ← **无条件执行**（选没选都会换背景）

      对拍器**看不见**这一类：`_menu_member` 与 `Nest` 两份载体都被同一条语句
      覆盖，两边"自洽"地说了同一个谎 —— 所以它只能由构建层挡。

      修法：`struct_guard` —— 语句的块路径与当前拍不一致就**先落拍**
      （与 `branch_guard` 同源；两边任一为 `None`（不知道）就不比）。
      真实项目实测：守卫命中 **29 次**（都是"本来就该落拍"的边角，例如
      选项行/分支头），**产物逐字节不变**（`render_hash` 8eea612fe151）
      —— 它是安全网：专治真实数据里还没出现、但合成用例一打就中的形态。

      判据：`tests/test_nest_column.py::TestAStatementCannotStealTheStructure`
      （4 条：结构位、产物里仍在体内且无 `pass`、过表一致、
      空体补的 `pass` 不许漏出去）。

    **(c) `Inline` 分隔符只在 `SEP + '@if '` 组合处切**
        （`beat_model.parse_inline_cell`）

      `compose_inline_cell` 写出来的分隔符后面**永远紧跟** `@if `，所以
      "组合"就是自反的判据；正文里**孤立**的 `\ue004`（粘贴带进来的）因此变成
      **惰性字符**。从前只要见到分隔符就切 → 注释被劈成两半，后半还被当成
      条件注释**贴到 `if` 行上**（错位，并污染下一格）。

      ⚠ 仍然无解的一种：正文里带着**完整组合** `\ue004@if ` 且后面还有内容 ——
      那与工具自己写出来的形状逐字相同，无从区分（U+E004 打不出来，
      只有粘贴**本工具自己的单元格内容**才可能造出来）。

      判据：`tests/test_inline_notes.py::test_a_lone_separator_in_the_body_is_inert`。

    验收：`tools/hell_edit_rounds.py` **7 → 0**（A 对抗编码 3→0、A2 转义自反
    4→0、B 连续编辑 10 轮 0、C 真实项目编辑 0）；全量 pytest **2433 → 2442**
    （新增 9 条，**9 条在改前全红** —— 用 HEAD worktree 逐条验过，不是"写完就绿"）；
    `tools/hell_nest_inline.py` 合成 17/17 + 真实项目 40 文件全绿；
    `render_hash` 两条路仍逐字节相等（`8eea612fe151` / `8eea612fe151`）；
    `audit_roundtrip` / `audit_real_project` / `acceptance_workflow` /
    `torture_test` 与上一版**逐行一致**，`journey_check` 只差临时目录名。

    取证工具：`tools/probe_round_escapes.py`（转义逐轮 `repr` 打出来）、
    `tools/probe_body_leak.py`（漏出菜单的三种形态 + 第二轮多出来的行）。

84. **字符串说话人（P0）：`"角色名" "台词"` 的后半句不许丢**（2026-10-05）

    Ren'Py 的 say 语句允许 `who` 是**字符串字面量**（一次性角色名）：

        "系统" "你已解锁角色【万理】的人物介绍！"

    而 `_RE_SAY` 的 who 组只认标识符（`[A-Za-z_][\w.]*`），于是这种写法被
    当成"旁白 `"系统"`" —— **真正的台词整句丢掉**：

        speaker='' + text='系统'          ← 后半句根本没进模型

    **为什么是 P0**（不是"改那一句才丢"）：模型里没有那句台词，所以
    **这一章只要动过任何一处**，增量补丁就会把那一行改写成残缺的 `"系统"`。
    真实项目 `character_gallery.rpy` 2 处（两个示例 label）：用户改同一章
    第 110 行的 `call screen`，两句系统台词的正文一起没了。

    修法三处（缺一不可）：

      · **解析**（`parsing/renpy_parser.split_string_speaker`）：`who` 存
        **带引号的原文切片**（`'"系统"'`，转义原样保留）、`what` 取第二个
        字符串（反转义）。带引号存有两个好处：渲染时逐字写回去，以及
        **与角色变量名天然分得开**；
      · **渲染**（`beat_render.render_beat_to_rpy` 的台词那一段）：字面量
        说话人**逐字输出**，并且**跳过角色表审计** —— 不跳的话会写成
        `# TODO: 缺少资源 角色 '"系统"'`，把整句台词换成一句 TODO
        （比原来的缺陷还狠）；
      · **校验**（`pipeline/validate` 的 E102/W110、`story/excel_import` 的
        角色登记建议）：字面量说话人**不查角色表**（它不是变量名），
        否则每处这种写法都会报一条"说话人不在资源表角色里"的假警，
        或者被当成"未登记的角色"。

    判据：`tools/hell_cross_file.py` 的 **A8**（解析层"模型里必须有那句正文" +
    文件层"改同一章另一行、正文不许消失"）**2 → 0**；
    `tests/test_incremental_export.py::TestStringSpeakerSay`（3 条：模型里有正文、
    改别处那一行逐字节还在、没改就逐字节不变）；
    `tests/test_roundtrip_invariants.py::TestStringSpeakerSay`（4 条：四种形态
    —— 裸引号 / 转义引号 / 反斜杠 / 单引号 —— 的语句多重集 + 两轮不动点，
    外加"单字符串仍是旁白"这条边界）。最小复现：
    `tools/probe_gallery_tail.py`（真实文件只读）。

85. **增量对位：六个"走不成增量"的病因，以及"报成功≠改到了"**（2026-10-05，O1/O2/O4）

    线索是一句话：**真实项目 40 个文件里，6 个"只改一句台词"会退回全量**
    （58～425 行）。方向是安全的（都报出来了），但六分之一的路走不成增量，
    而且顺着这条线索又抓到**两个"报成功却什么都没改"的静默缺陷**。

    逐条病因与修法（每条都有能单独变红的回归钉）：

      · **邻居判据太宽**（`patcher._narration_slot_replaced`）："整句换写的
        旁白"那条兜底要求**文件里所有别的拍**都认得出自己 —— 任何一块认不出
        （菜单头那一拍的行区间只有 `menu:` 一行，本来就认不出）就整份否掉。
        收窄成**紧邻两块**：这条判据的本意是"整体错位时邻居会一起失守"，
        而错位是整体性的，紧邻两块一样过不去。
      · **菜单拍的单拍渲染吐占位符**：`render_beat_to_rpy` 里 `KIND_MENU`
        那一支接在 `elif beat.text:` **后面** —— 占位符 `MENU_NO_CAPTION`
        （`'\x00menu'`）是个真值字符串，于是永远走不到，产物里出现字面量
        `"\x00menu"`，与整份渲染的 `menu:` 对不上（**死代码 + 不一致**）。
        修法：那一支提到 `elif beat.text:` 之前，并且用 `menu_caption_text`
        出 `menu "标题":` / `menu:`。
      · **`lost_new` 只比"内容行"**：改了菜单标题时，`menu "新标题":` 既不是
        台词也不是舞台/音频 → 判据看不见 → **补丁报成功、标题没改**
        （真实 `script_pomo.rpy`：`ok=True`、`changed=0`）。扩到**整拍所有
        非空行**。
      · **代码行的行尾注释被顺手删掉**：`$ time = 1#1500` 一拍渲染多行时
        贴不回注释（不变量 78 的"有意为之"），而"代码行按目标换正文"那条
        通道会拿生成结果去改这一行 → 作者的注释没了。判据：原文去掉行尾
        注释后与生成结果相同 → **保留原文**（真实 `pomodorro.rpy` 改一句
        台词动了 6 行，多出来的就是这三处）。
      · **选项的引号与冒号要一起归一**：源 `'第一章':`（单引号 + 选项冒号）、
        生成 `"第一章"` —— 去冒号与去引号各自独立地算，两个一起出现就对不上，
        整章被判"生成内容丢了"（真实 `script.rpy` 7 条）。`_keys` 改成
        **先 `rstrip(':')` 再 `_unescaped`**。
      · **`if` 体的块头不是拍的内容**：条件拍渲染成 `if …:` + 块体两行，
        块头那一行不在拍的行区间里 —— 比行数就成了"生成 2 行、区间 1 行"，
        对位守卫判"这一段不像这一拍"（真实 `mini_game_dfinder.rpy`）。
        比行数前先去掉**开头的结构行**（`_body_lines`；只认 `if/elif/else`，
        `menu "标题":`、`python:` 是拍自己的内容，不能去）。

    顺着这条线抓到的**两个静默缺陷**（比"退回全量"坏得多）：

      · **`_align_key` 缺条件**：同一章两个分支的旁白拍在"类型/说话人/舞台"
        上完全一样，删掉一个之后 `difflib` 会把剩下一拍配到**另一个分支**
        的行上 → 台词换个分支还报成功（`if` 体 → `else` 体）。条件是对位里
        **稳定又区分分支**的特征（改台词不会动它），必须进键。
      · **改「条件」列静默不落**：条件在产物里是 `if …:` 那一行，而块头
        不属于拍的行区间 → "生成内容都在产物里"那条判据看不见它；又因为
        `changed_lines=0`，整体校验被整段跳过 → `ok=True`、条件没改。
        补一条**语义判据** `_verify_conditions`（按 label + 说话人 + 台词
        配对比较条件），并且"一个字都没改也要查"。
        按内容身份配对是刻意的：全局多重集会被"源里还留着的其它拍"误伤
        （控制流文件里未参与编辑的 `jump` 拍、同文案出现在两个分支里）。

    **O2（顺序）**：一拍里"多条 `show` + 后面还有 `hide`"过表后 `hide` 跑到
    第二条 `show` 前面（该淡出的人还站在台上）。机制：`Stage` 单元格只把
    **最后一条 show** 当"原生"，渲染时它被整块拖到舞台尾段 → 排到 `hide`
    之后。修法：`beat_build._hydrate_stage` 记 `_stage_native_raw`
    （`{(verb, part): 原文}`），`beat_render._stage_lines` 按**结构化条目的
    位置**逐字出。判据：`probe_stage_order2.py` **0/8 个形状翻序**。

    **O4（两套生成器）**：控制流导出（`pipeline.renpy_exporter`）自己手写
    `f'"{opt}"'` —— 选项文案带 `"` 时写出 `"他说："你好"":`（语法错）、
    带换行时写出**跨物理行字符串**（产物不可解析）。章节那条路一直是对的。
    修法：`beat_render.quote_string` 公开成**跨包契约**（与
    `parsing/image_path_from_expr` 同一套做法），`_option_line` 统一走它；
    再加一条可执行契约 —— **同一段文本，两条路必须给出同一个字面量，且产物
    必须能被自己的解析器吃回去**。两套实现的真正区别是**输入**（图 vs 拍），
    不是规则。

    判据汇总：`hell_cross_file.py --real` / `hell_edit_rounds.py` 退回全量
    **6 → 1**（剩的那个是设计边界：源文件把菜单标题写在 `menu:` 的下一行，
    产物并进同一行 —— 2 行 → 1 行的合并超出"一对一替换"的契约）；
    `render_hash.py` **未变**（`d9b0f2de26b5`，本轮没动真实项目的渲染）；
    `tests/test_incremental_export.py::TestO1AlignmentFixes`（9 条）、
    `tests/test_fidelity_bytewise.py::TestANativeStageLineKeepsItsVerbPosition`
    （2 条）、`tests/test_exporter.py::TestStringRulesAreShared`（2 条）——
    13 条里有 12 条在 `fb73bff` 上变红（剩下那条是"永不许静默成功"的守卫）。

    验收：全量 pytest **2442 → 2449**（新增 7 条；其中**钉这条 P0 的 4 条改前全红**
    —— 用 HEAD worktree 逐条验过，另 3 条是"两轮不动点 / 单字符串仍是旁白 /
    标识符说话人不算字面量"的守卫，改前本来就绿）；
    `render_hash` 两条路仍逐字节相等（`8eea612fe151` →
    **`d9b0f2de26b5`**，差异**只有**那两句被救回来的台词，逐行核对过）；
    `hell_cross_file` / `hell_edit_rounds` / `hell_nest_inline` / `hell_test` /
    `probe_remaining3` 全绿；`journey_check` 的"文本"字数 29660 → **29686**
    （+26 字 = 那两句正文），`audit_roundtrip` / `audit_real_project` /
    `acceptance_workflow` / `torture_test` 逐行一致。

    **仍然存在的已知边界**（同属"say 尾部"，`tools/probe_say_tail.py` 量过，
    真实项目 **0 处**）：`e "x" with dissolve` / `… id line_001` 这类**尾从句**
    既不入模型、也不进 raw，全量路径上会丢；`menu:` 里若写 `"系统" "提示"`
    （当标题行用），模型里菜单标题没有 `who` 这一位，只保住可见文案（第二段）、
    **角色名会丢** —— 真实项目同样 0 处。

86. **菜单行与行尾注释：配对键必须"认得出自己"**（2026-10-05，清单 O6）

    清单 O6 记的是"裸 `menu:  # 备注` 那一章每次导出都退回全量"，报出来的
    理由是 `1 句台词在补丁结果里找不到（例：'"\x00menu"'）`，并建议在自检里
    跳过 `MENU_NO_CAPTION`。**按那个建议改会掩盖真病**：把产物摊开就能看到
    多出一条**没有冒号的重复选项行**

        menu:  # 备注：菜单
            "一"  # 备注：左选项      ← 多出来的（新增通道插的）
            "一":  # 备注：左选项      ← 原来那一行（"台词不许删"留着）

    而占位符那条判据正是**抓住**它的网：多出来的行落在 `menu:` 后面 →
    重新解析时变成了菜单**标题** → 占位符对不上 → 整份补丁被否。
    跳过判据 = 把网撤掉，补丁反而会"成功"地写下重复行。

    真病根是**配对键**（`patcher._line_key`）：源里写 `"选项":` —— 冒号是
    `menu:` 块的**结构语法**，不是内容 —— 而单拍渲染（`render_beat_to_rpy`
    的选项那一支）只写 `"选项"`。两个形状不同键 → 生成行走"新增内容"通道
    被插进去。同一个病根还有第二张脸：**小幅改选项文案**（`"一"` → `"一。"`，
    相似度过得了对位守卫）时配对失败 → 既不落也不报 → `ok=True`、
    `changed=0`、**文案没变**（静默 no-op）。

    修法（两面都要，缺一不可）：

      · **A 面 · 键对得上**：单拍渲染的选项行**带上冒号**（与
        `render_menu_block` 的 `label += ':'` 一致）；`_line_key` 用
        `_OPTION_ROW_RE` 把 `"选项":`（**可带行尾注释**）认成"没有说话人的
        台词行"，与 `"选项"` 同键。`_rewrite` 里再补一条：两个都是选项行时
        **只比引号里的正文** —— 正文没变就保留原文那一行，别把作者的
        `'第一章':` 顺手改成双引号（实测：改一句旁白动了 8 行）。
      · **B 面 · 网留着**：`_verify_patch` 的"每一句台词都要在产物里"那条
        **原样不动**（占位符仍然要求对上）—— 它就是抓"多出来的行变成标题"的网。

    同一轮还连带抓到一条（`hell_edit_rounds` 轴 B 第 7 轮）：**带行尾注释的
    台词行认不出自己是台词** —— 渲染器把注释贴回同一行（`e "台词"  # 备注`），
    而 `_SAY_RE` 要求整行恰好是 `说话人 "正文"`，于是改正文时同样被插成两行。
    判据：**身份一律在去掉行尾注释之后算**（`patcher._content_of`，复用同包的
    `atl._strip_comment`，引号感知）；认不出形状的行仍然逐字比（保持保守）。

    判据汇总：`tools/probe_placeholder_leak.py` 中招形状 **1 → 0**；
    `hell_edit_rounds.py` 轴 B **4 处红 → 0**；
    `tests/test_incremental_export.py::TestMenuRowsAndTheirNotes`（4 条，3 条
    在 `008b1fa` 上变红）＋ 更新后的
    `tests/test_script_table.py::TestRender::test_menu_option_renders_as_string`
    （选项行 = `"选项A":`）；`render_hash.py` **未变**（`d9b0f2de26b5`）；
    全量 pytest **2462 → 2466**。

---

## §单源：一行词 → 每格占哪几个词（`assign_tokens`）· 2026-10-15

> 抽它的理由：这件事此前有**三套并行实现** —— `pattern.match_line` 的递归回溯、
> `story/spec_args._claim` 的单趟认领、`story/spec_args.slot_value_map` 又把
> `_claim` 抄了一遍（换返回形状）。规则已经漂移过，用户看到的那种
> **"试切说切上了、卡片读出来不一样"** 就是它。

**铁律**：分配规则**只有一份** —— `authoring/pattern.assign_tokens(tokens, slots,
*, groups=None)`。`match_line`（试切）与 `_claim`（读值那一侧）都只是它的
**薄转发**；`slot_value_map` 不再自己走一遍格子。

- 返回：`{'spans': {第几格: (词起点, 词终点)}, 'used', 'bad_kw', 'rejected',
  'unfilled', 'leftover', 'values', 'exact'}` —— `spans` 里**没有**的格子 =
  没认领到（调用方显示空格子）。
- 规则：`keyword` 逐字相等（否则记 `bad_kw`）；`optional` 出现就吃两个词
  （固定字前面夹的垃圾词可以跳过）；`multi` 吃一串**连续的自家人**，
  **一个都不吃**也是合法解；`choice` 勾了 `limit` 就只认候选（否则记
  `rejected`）；其余吃一个词。**所有取值格**都可以跳过前面那些
  **谁也不认识的词**去认自己的候选（代价 `SKIP_JUNK + SKIP_WORD`）。
- 代价表（`_tok_cost`）：自己的候选 0 / 随便写的格子吃自由文本 2 /
  随便写的格子**借**别人写明的候选 8 / 闭枚举格吃候选外的词 5（"值不在候选里"，
  界面把那一格标黄）/ 闭枚举格**抢**别人写明的候选 20（比"空着 7 + 晾着 3"
  贵，于是**永远不会**被选中）/ 必填格空着 7 / 晾着一个词 3。
  `multi` 从第 2 个词起每个 +2。
- **失败分四类**，一律"报出来"而不是"猜"：`bad_kw`（关键字对不上）/
  `rejected`（勾了限死来了外词）/ `unfilled`（该吃词却没词）/ 没人认领的词
  （`leftover`）。
- **分岔为零**（2026-10-17 第二轮攻击删掉 `POLICIES`）：以前试切用
  `order-first`、读值用 `candidates`，同一行能给出**不同的格子值**；
  现在"候选跳过"进了代价表，两处**一个答案**。引号也在更早就统一到了词法层。
- 判据：`tests/test_assign_tokens_single_source.py`（两侧逐格相同 + `_claim`
  逐字等于分配器）、`tests/test_cut_tokenizer_attack2.py`（第二轮攻击：
  不许偷词 / 不许两侧分岔 / 写回补引号 / 格子 `ok` 按候选算 /
  脏 `word_offset` 不许炸）。

### 空引号 = 占位 + 输入错误（2026-10-18 第三轮攻击）

**铁律两条**：

1. `""` / `''` / `"   "` **不是**"这一格留空"的合法写法 —— Ren'Py 里它是
   **空属性名**，跑不起来。想留空就别写那个词（`show lan happy` → 服装空）。
2. 但它**占住那个位置**：`_atoms` 产出 `{'empty': True}` 的词，词流里是
   `pattern.EMPTY_TOKEN`（`'\x00'`），于是后面的值**一格都不许前移**。

- 以前 `_atoms` 里 `if text:` 把它连同毛病一起丢掉 → `show lan "" base_swimsuit
  happy` 变成 3 个词对 3 个值格 → `服装=base_swimsuit`、`表情=happy`，**还报绿灯**。
- 分配：空引号**不许跳过**（跳过就是前移），认领它代价 0 = "这一格这一行没有值"。
  `assign_tokens` 两趟：第一趟问清空引号落在哪一格；那一格标 **dead**（不吃词、
  不占位、`exact` 永远为假），它的候选词对别人不再是"抢"（`_tok_cost` 的 `dead`）
  但**谁都不许跳过** → `base_swimsuit` 按位置落到「表情」、`happy` 算多出来的。
- **谁死由位置定，不由代价定**（2026-10-18 第四轮攻击修）：空引号对谁都免费、
  `multi` 格"不吃"也免费，于是 DP 会把它**推给下一格** —— 只「服装」multi 时
  `show lan "" base_swimsuit happy` 排成"表情死、服装=base_swimsuit"，
  **值整体左移一格**。现在轮到取值格而下一个词是空引号时，`_spans` 里这一格
  **只有"吃掉它"一个分支**；`keyword` / `optional` 不强制（空引号不是关键字匹配，
  `at 位置` 本来能整段缺席）。判据：`tests/test_cut_dead_positions.py`。
- **死格的候选词是"没人要"的**（同上）：`_tok_cost` 对"只属于死格"的词返回
  `COST_UNKNOWN`（2）而不是 `COST_OFFLIST`（5）—— 否则必填格接得住（5 < 7+3）、
  多值格接不住（5 > 0+3），同一句话在"服装 multi / 不 multi"下给出两组格子值。
  仍然属于某个**活格**的词照旧算"抢"（20）。
  判据：`test_cut_dead_positions.py::test_死格的候选词对别人是自由文本_不是候选外`。
- 读值/写回同一份：`_claim` 给 `{服装: ''}`；`slot_span` 用
  `pattern.token_spans` 落到那对引号上，改那一格 = **换掉它**
  （`show lan "" happy` → `show lan base_swimsuit happy`，不再把 `""` 留在行里）。
  ⚠ 值没变仍然**逐字节原样**（清空一个已经是空引号的格子不会顺手删掉它）。
- 报错：`match_line` 的 `reason` 先点名"空引号「…」—— 引号里没有值，
  Ren'Py 会把它当成空属性名；删掉它，或者把它改成那一格真正的值"，
  并给出 `lex_problem` 供界面把它**排在第一位**（它是病根，"哪个词没人认领"
  只是后果）。
- **词号按人敲的空格数**：`_tokens_meta` 给每个词带上 `words`（人敲的第几个词）
  与 `n_words`。以前拿词的下标当词号，`show 小明泳装` 会报"第 4 个词"
  （这一行只有 2 个词）。
- 判据：`tests/test_cut_empty_quote.py`（16 条）、`tests/test_cut_tokenizer_edge.py`
  （空引号那条口径 2026-10-18 改为"占位"，旧版钉的是"必须消失"）、
  `tests/test_cut_dead_positions.py`（9 条：谁是 dead 由位置定 / 死格候选词
  对别人是自由文本 / 三种 multi 形状与参照系逐格相同）。

### 属性格「可填可不填」（2026-10-18 用户现场）

**事实**（`sr_general` 的 `layeredimage lan` + 真实剧本）：`show` 是
`show <tag> [属性…]`，**属性全是可选的** —— `show ye happy`（没写服装）出现 11 次、
`show ling at bounce_once:` 一个属性都没有。编辑器把
`show ye base_handdown happy` 拆成「角色｜服装｜表情」，于是"没写服装"被当成
"少了一个词"，试切报"第 2 格该有一个词" —— 对着一条**完全合法**的行让人改。

- **格子级开关**：`pattern` 的 `required`（缺省 `True`，老声明一个字不变）。
  `required=False` → `_spans` 里"一个都不吃"免费（同 `multi`），`_report` 也不把它
  算进 `unfilled`。
- **默认从语句头来**：`pattern.with_optional_defaults(slots, head)` ——
  `show/scene/hide` 的**属性格**（第一个取值格之后的取值格）补 `required=False`；
  第一个取值格是立绘 tag，仍然必填；别的语句**一个字都不改**（自定义语句的参数
  可能是必填的）。只补缺：格子自己写明了 `required` 就照它。
- **"只补缺"的判据是 `_required_set`，不是"键在不在"**（2026-10-18 第五轮）：
  `parse_pattern` 会给每格补 `required`（缺省 `True`），于是"人说的 True"与
  "补出来的 True"长得一样 —— 以前用 `'required' not in slot` 判，结果默认值
  **跟着入口顺序变**：传原始 dict 补得上，传 `parse_pattern` 过的补不上，而
  读盘/卡片/`match_line` 拿到的**都是** parse 过的（等于默认从没生效过）。
  `_required_set` 只记"这句话是谁说的"，不参与分配，落盘时摘掉
  （清单里存的是具体值，读回来"键在 = 说过的"，语义自动成立）。
- **界面**：卡片上「这一格可以不填（不写就用默认）」；**只有人动过**这个勾，
  `data()` 才写出 `required`（"没说" ≠ "说了必填"）。预览里显示成
  `从候选里选，可不填`。
- 判据：`tests/test_cut_optional_slots.py`（19 条：默认写法能切上 / 不许因此抢词 /
  必填的照旧报 / 三种立绘语句 / 自定义语句不动 / 空引号仍按位置 dead / 界面往返）。

### 批量导入入口不再跟着"可以有多个"藏（2026-10-18）

用户原话：

> 为什么只有可以选多个时才让我批量选择？明明从这些里选也应当有批量导入按钮

「📋 批量粘贴一大段…」现在**只要这一格能列候选就有**；`multi` 只决定
**收词口径**（空格算不算分隔符、占位提示），不再决定入口在不在。
批量弹窗默认按"一大段清单"收（空格也算分隔），要保留「洛丽塔 长裙」这种
值里带空格的候选，把那个勾去掉。
判据：`tests/test_custom_statement_entry.py::test_从这些里选就有批量导入_多选只换收词口径`。

### 角色的三种写法：候选必须是**剧本真源**（2026-10-18）

用户原话（截图里试切报"第 1 格「角色」的 28 个候选里没有「lan」"）：

> 啧……还是有问题！明明它只是换成变量名又不认得了……

**事实**：角色表里同一个人有三种写法，**用途各不相同**：

| 写法 | 例子 | 出现在哪 |
|---|---|---|
| 变量名（`ID`） | `l` | 自定义语句的参数（`swim_suit l "泳装"`）、角色表的主键 |
| 显示名（`Name`） | `蓝` | 编剧眼前（Excel、下拉、芯片） |
| 立绘 tag（`ImageTag`） | `lan` | `show / scene / hide` 里**唯一能写**的那个词 |

**铁律**：声明里的 `choices`（候选）只能是**剧本里真要写的那个词**（tag 或
变量名），显示名**只是标签**（芯片显示 `蓝（lan）`、写盘只有 `lan`）。

为什么以前只错了一半、后果却比"切不上"更重：候选会进卡片的**下拉**
（`app.beats.spec_arg_choices_for` 直接读 `columns[].choices`），于是从下拉里
挑一个就把**显示名**写进了脚本（`show 蓝 …` —— Ren'Py 里没有这个 tag）。

- **导入**（`choice_sources` / `suggest_shape`）：`show/scene/hide` 给 tag、
  其它语句给变量名，显示名降级为 `labels`（`90f6328` 起）。
- **载入存量声明**（`dialog_learn._fill_params` → `learn_statement.
  role_candidates_for`）：旧版写进去的显示名在这里**换回真源**，并**明说**
  （`_role_fix_note`：哪一格、换了几条、举一例、"保存才写回清单"）。
  不换就是两处错：试切切不上 + 下拉把显示名写进脚本。
- **只查表、不猜**：认不出是角色的值一个字都不动（「服装」「表情」的候选
  与角色表无关）；已经是真源的值也不动（不制造"我什么都改了"的假象）。
- **去重有据**：两个人共用一个 tag（真机 `narrator` 与 `n` 都是 `narrator`）
  时只留第一个，丢掉的记进 `dropped`。
- **换不掉要说清**（`explain_role_spelling`）：人自己手打的显示名候选走到
  `rejected` 时，`_why_not` 会说"「lan」是「蓝」的立绘 tag，而候选里写的是他的
  显示名「蓝」"——而不是把人往"改顺序"上带。
- **成本**：读角色表要读整张资产表（真机 ~600 ms 首次 / ~240 ms 之后）——
  弹窗里 `role_table(root)` **读一次缓存**（`_roles_cached`），
  `role_candidates_for` / `explain_role_spelling` 都收 `roles=` 复用。
- 判据：`tests/test_role_candidates_script_truth.py`（16 条：三种写法归一 /
  自定义语句给变量名 / 非角色候选不动 / 没表不动 / 共用 tag 去重 / 书名号 /
  载入后试切切得上 / 保存写回真源 / 换不掉时说清是同一个人）。

---

## §单源：`show` / `scene` / `hide` 子句关键字（2026-10-15）

**铁律**：这批词**只有一份** —— `core.vocabulary`：

- `SHOW_CLAUSE_KEYWORDS = {'at','as','with','behind','onlayer','zorder'}`
  （依据官方语法 `show_statement -> "show" "expression" simple_expression
  ( "as" name )? ( "onlayer" name )? ( "at" … )? ( "behind" … )? ( "with" … )?`
  ＋ 同族 `zorder`；docs 明说 **scene 取同一批 properties**）
- `SHOW_INTRO_KEYWORDS = {'expression','screen','layer'}`
  （**引入型**：替换图像名，后面跟的不是图名）

从前这批词在**五处**各写一份、口径已经不同（`excel_export` 有 `on` 却**漏了
`onlayer`**；`renpy_parser` 的 scene 集更窄；`learn_statement` 手抄一份还缺 `zorder`）。
现在各站只写"**内核 ± 显式增减**"，差异本身成为可读、有注释的事实：

| 站点 | 现在 |
|---|---|
| `parsing/renpy_parser._SHOW_KEYWORDS` | `SHOW_CLAUSE_KEYWORDS` |
| `parsing/renpy_parser._SCENE_KEYWORDS` | `SHOW_CLAUSE_KEYWORDS - {as,behind,zorder}` |
| `story/beat_stage_parse._SHOW_ARG_KEYWORDS` | `SHOW_CLAUSE_KEYWORDS` |
| `story/excel_export._SHOW_KEYWORDS` | `SHOW_CLAUSE_KEYWORDS \| {on}` |
| `assets/asset_registry_constants._SHOW_STOP_WORDS` | `SHOW_CLAUSE_KEYWORDS \| {expression}` |
| `story/generation_guard` stopwords | `SHOW_CLAUSE_KEYWORDS - {at}` |
| `authoring/learn_statement._NOT_A_VALUE` | `SHOW_CLAUSE_KEYWORDS \| SHOW_INTRO_KEYWORDS \| {True,False,None}` |

⚠ **没动的三处口径分歧**（都写在站点注释里，要动得先拍板 —— 它们都是"改口径"）：
`_SCENE_KEYWORDS` 比 show 窄（`scene bg behind x` 会把 `behind x` 当图像名）；
`screen`/`layer` 没进 assets 的 stop words（`show screen foo` 会把 `screen` 当图像名）；
`patcher._KEYWORD_SPEAKERS` 是**另一类**重复（"不可能当说话人的语句关键字"，
与 `RENPY_STATEMENT_VERBS` 也重叠）。


---

## 附：2026-10-18「产物正确性」四道闸门（体检报告结案时立的）

> 体检的结论是"骨架对、纪律好，但**产物正确性这一环没有闸门**"。
> 这一节把补上的判据写死，免得下一次又靠"测试全绿"自欺。

### 闸门 ①：逐字节保真（往返一个字节都不动）

判据（**唯一口径**，`tools/batch_eval.py`）：

```
rpy → xlsx → rpy --source
产物剥掉「首行 GENERATOR_MARKER + 它后面那一个空行」后，与原文逐行相等
```

- 真机基线：`sr_general` **55/55**、官方 `tutorial` **234/234**（写盘文件）。
- **载体风格是内容的一部分**：BOM 与 CRLF 由 `story/source_style.py` 读时记住、
  写盘还原。`Path.read_text()` 的换行翻译、`write_text()` 的平台换行都**不许**
  出现在落盘路径上（`write_text_atomic(newline='')` 是唯一写口）。
- **没有可编辑内容的文件（纯 define/screen/init）→ 原样透传**
  （`PatchResult.passthrough=True`）：不写、不加标记、一个字节都不动。
  绝不许"退回全量重生成" —— 那会把顶层块重排。

### 闸门 ②：引擎 lint（产物引擎读不读得了）

`pipeline/engine_lint.lint_text()`：产物 → 最小工程 → SDK `lint`。

- **只把语法错误当阻塞**；语义错（缺图/缺角色）只 warn —— 它需要完整项目上下文。
- 项目声明的自定义语句要进 `ignore_heads`（临时工程没有 `register_statement`，
  否则 `mycue …` 会被误判成语法错）。
- **没有 SDK 必须如实报"没跑"**（`LintReport.ran=False`），绝不假装通过。
- 默认开启的入口：`cli build`、`cli table/compile`、界面导出。

### 闸门 ③：菜单标题 / 块结构（语法域）

- `menu` 头**只接受** `menu [label] [(args)]:` —— **不接受字符串**。
  标题是块里的一行裸字符串；带说话人的标题（`e "…"`）整行进 `extras`。
- **注释不关闭块**：词法建树时注释不参与缩进比较（永不压栈），归属由
  "下一句所在的块 / 自己的缩进"决定。
- `order_menu_bodies` 的菜单组**不许跨 label**（跨了就会把别的章节的拍卷进来
  重排，产物里同一个 label 出现多次）。

### 闸门 ④：表格之间的"引用"（不是"候选"）

「资源引用（活）」表：除行号外**每一格是公式**（`VLOOKUP` / `COUNTIF`）。

- **Beats 主表不许出现公式格**：引用是旁路视图；公式格被手改一次，
  连接就没了，而且没人会知道。
- 新增落盘点必须在 `tests/test_single_write_entry.py` 的 `WRITERS` 里登记
  并写清"写的是什么"；写 `.rpy` 的只许走 `generation_guard.write_generation`。

### 闸门 ⑤：菜单头 `menu [名] [(参数)]:`（2026-10-19 收尾 P1-3）

引擎的权威语法（`renpy/parser.py: menu_statement`）：

```python
menu_statement(l, loc):
    label = l.label_name()          # ← 「名」，可以没有
    l.set_global_label(label)
    arguments = parse_arguments(l)  # ← 「(参数)」，可以没有
    l.require(":")
```

所以四种合法形态是 `menu:` / `menu 名:` / `menu (args):` / `menu 名 (args):`。
关键事实：**「名」是一条真 label**（`menu_statement` 会为它产出 `ast.Label`），
`jump 名` / `call 名` 都跳得进来 —— 它不是装饰，丢了就是"跳转目标没了"。

- 模型：`MenuNode.name/args` → `Statement.payload['name'/'args']`（非空才写）→
  `Beat.menu_name/menu_args`（隐藏列 `MenuName` / `MenuArgs`，追加在 `LabelHeader` 之后）。
- **不进 `content_hash`**（`_HASH_FIELDS[MENU]` 只含 caption，`extras` 同理）：
  换写法不该让全项目菜单的 hash 一起变；但**进拍指纹**
  （`BEAT_FIELD_SPECS` 里两列都 `fingerprint=True`）—— 那才是"要重写文件"的判据。
- 头行**只有一份拼法**：`beat_model.menu_head_text()`。整份渲染
  （`_render_menu_segments`）与单拍渲染（`_emit_text_line`）都必须用它 ——
  两处各写一份正是 P0-2 的来源。
- **唯一一处不逐字节**：紧贴写法 `menu 名(x):` 会规范化成一个空格
  （引擎两种都收，我们从不生成紧贴形态）；其余四种形态逐字节相同。
- 标签语义不变：`menu 名:` **不**替代 `label`，它只是多了一条可跳转入口。
- 判据：`tools/probe_menu_head.py`（结构 / 头行逐字节 / 幂等 / **真 SDK lint**
  五形态全过）、`tests/test_menu_head_modeling.py`。
- 卡片层两条配套（2026-10-19）：`menu` / `menu_option` 拍的「台词」格**不是
  台词**（是标题 / 选项文案），所以 `speaker_missing_warning` 对这两类**不报**
  "没有说话人 → 会变旁白"；高级区标题里要写出「菜单头」，否则没人知道
  那两格能改。
- **`image 名:`（ATL / 块形式）也进资源表**（2026-10-19）：顶层区间扫块头
  （`_RE_IMAGE_ATL_HEAD`，与 `RenPyParser._RE_IMAGE_ATL` 同一份正则），
  登记 `{name, path:'', atl:True}`；`apply_script_definitions` 落成
  `Path=''` + `_atl=True` 的资源行（`Notes` 注明"贴图写在块里"）。
  理由：不进表 = 下拉里选不到、`show 名` 被报缺资源。`image 名 = 表达式`
  那条路不受影响（先登记的赢）。

### 闸门 ⑥：P1 收尾的四条硬判据（2026-10-19）

体检 §10.2 那轮核销留下 2 条"部分" + 1 条"无效" + 1 条"未验"。这一节把它们
变成可复跑的判据 —— **判据不看提交信息，只看行为**。

1. **不给 `--source` 也不许重排整份文件。**
   `rpy → xlsx` 时把源路径写进隐藏表「表信息」（`story/table_meta.py`），
   `compile_table` 读不到 `--source` 就自动接上它走增量。
   - 判据：`tools/audit_full_path.py`（刻意不给 `--source`）——
     `sr_general` 55/55、`the_question` 5/5、`tutorial` 24/24 逐字节相同。
   - 只认**真实存在**的来源文件；表搬走 / 源被删 → 当没有来源（行为与从前一致）。
   - 不用文档属性：Excel 另存与我们自己的"刷新候选"都会重写工作簿。

2. **「代码」格读回来时，台词行是 raw 不是 python。**
   `s "…"` / `"旁白"` 是 say 语句；旧判据只认语句头白名单，于是菜单块里
   "带说话人的标题"被判成 python、`Beat.raw` 变空、**整行消失**
   （真机 `the_question/script.rpy:99`）。判据：`_looks_like_say_line`
   （引号紧跟可选名字，尾巴只允许 `with` / `id` / `(` / `nointeract` / 注释）。
   `print("x")` / `a = "x"` / `"a" + b` 仍判 code。

3. **写完必须重载；写回目标必须是人指认过的那一章。**
   - `export_table(source_rpy=…)` 指名了底稿 → 默认产物就是它；
     "表旁边恰好有同名 .rpy"不算指名，产物仍落 `build/renpy/`。
   - 写回原文件时 `evaluate_generation(source_text=…)` 必须拿到原文，
     否则补丁没配上时 `default` / `init python` 会静默消失。
   - 写成功后 `session.load()` + 重建卡片列表（不重载 = 下一次编辑拿过期行号）。

4. **报告里的字段不许反推。**
   `engine_lint.ran` 取 `LintReport.ran`，不许用"有没有 ENGINE_ issue"反推 ——
   通过时没有 issue，反推会写 `ran=false`（闸门开着、报告说没跑）。

### 闸门 ⑥ 的两条配套（同一批）

- **`init -N python:` 也是 python 块**：`init [N]` 的 N 允许负数和浮点
  （`init -1 python:` 是常见写法）。只认 `\d+` 会让它变成
  `UnknownBlockNode` —— 字节保真没问题，但构建报告会假报"工具不认识的语句头"。
- **「只看有变化的拍」的判据是 `StageFrame.changes`**（与「舞台快照」的
  「本拍变化」同一份文字），不是 `frame_summary`（后者永远至少含
  `#N · line_id` → 恒真，等于没有这个开关）。

### 三条"平台期"约束（改代码前先读）

1. **判据要对着引擎**：工具自己前后一致 ≠ 产物能跑。任何"生成 .rpy 的新路径"
   都要能被闸门 ② 覆盖。
2. **写回的区间/行号只有一份真源**：`code_blocks` 用"raw + code 池"计行，
   `ordered` 里还有 `opaque`（不进 `compose_code`）—— 混用就是块被劈开。
3. **"看起来像语句"不是语句**：`menu = nvl_menu` 是 python 赋值；
   判据在 `beat_model.is_python_assignment`（渲染层与构建层共用一份）。


### 闸门 ⑦：六条收尾（2026-10-19，A1 → B1 → C → D → E2）

用户定的顺序，一条一节；每条都给**可复跑的判据**（`tests/` 里那几个文件就是判据本体）。

1. **ATL 块只读定位（A1）**：`image 名:` + 块体不进表、**不可编辑**。资源表那一行
   只有名字（`_atl`）和 `_source_file` / `_source_line`；块体在 `.rpy` 里是唯一真源，
   界面只做「读出来 + 打开源文件」（`assets/atl_block.py`）。
   - 「缩进更深」是块体边界唯一的判据；文件不在 / 行号过期 / 没有块体，一律给
     `reason`（**不猜、不给空文本**）。
   - 卡片：ATL 行不再报"没有图片文件"（它本来就没有贴图），改说「（ATL 定义，无贴图）」，
     详情给块体 + `文件:行号`，「打开源文件」只在定位得到时可点。
   - 为什么不给编辑框：会立刻造出**第二个真源**（资源表 vs 源文件）。

2. **位置是猜的，就要说（B1）**：`parse_transforms` 记 `parallel` / 关键帧 / 动画块，
   `Placement.uncertain` 带着那句人话，预览横条显示
   `⚠ 这个 transform 含 parallel/关键帧，位置是猜的`。
   - **有把握时一个字都不许多**（到处标警告 = 没警告）。
   - `on show:` + `linear` 的末态是明确的（P1-4 那条不能退）；`on show: repeat` 才算不确定。

3. **push = 字段级三方合并（C1）**：base = **拉取那一刻这一行的 payload**，
   存在「协作信息」表的 `BasePayload` 列（装进**文件**而不是库：每个人的"那时"不同，
   复制/改名/另存都不该丢）。
   - 规则：`m==t → m`；`m==b → t`；`t==b → m`；否则冲突。
   - **不同字段静默合并**；真冲突带着 `(field, base, mine, theirs)` **按字段**报。
   - 写库带 `WHERE revision=?` CAS；合出来等于库里那份 → `unchanged`（不涨 revision）。
   - 「自动合并了 N 行 / M 处」必须报（`auto_merge_summary`）。
   - 没有 base（老表 / 旧版 Excel 另存丢了列）→ **降级两方比较**，并明说
     "只能比谁新、不能自动合并"；库里没动过时仍允许写（那是唯一改动）。

4. **冲突界面用编剧的话（C2）**：两栏（你写的 / TA 写的）+ 三个按钮
   （用我的 / 用 TA 的 / 两句都留 ▾），**不显示 base**，不用"冲突"二字。
   - 默认选 TA（较新的那份），一路回车能过；「自己拼一版」**永远预填**。
   - 「这次先跳过」= **部分提交**：写库只碰被解决的行，同时刷新本地行与
     协作信息的 `Revision` / `BasePayload`（不刷新 → 下次 push 又报同一条）。

5. **结构冲突单独分类（C3）**：
   - 我删、TA 改 → **保留 TA 的**，报「你删过这句」；
   - 旧表（我没改过）里的删除行 → **不复活**（`skipped_stale`）；
   - 两边同位置各插一行 → `insert_same_slot`，「两句都留」= `insert_both`
     （原行留一份，另一份作新行排旁边，后续 Seq 顺延）；
   - 我改 Seq、TA 改内容 → 自动合并（Seq 我的、内容 TA 的）。

6. **派生视图必须说自己是哪一刻的（D1/D2）**：「舞台快照」「演出时间轴」表头右侧写
   `生成于 …（点「刷新派生视图」重算）`；显式入口 `excel_export.refresh_stage_views`
   + 工具栏「🔄 刷新派生视图」。
   - 重算**先删同名表** —— 否则 `create_sheet` 会造出「舞台快照1」「演出时间轴1」。
   - 时间轴的「本拍变化」与快照同一行那份文字（递推点只有一份推导）。

7. **改名传播接进界面（E2）**：资源表改了 ID 之后，脚本里的旧名字靠
   「✏️ 资源改名」传播：**预览**（不写盘）→ **应用**（先备份 `.bak`）→ **回滚**
   （逐位还原，含 CRLF）。
   - 词边界匹配（`hero` 不动 `hero_happy`）；读文件用 `newline=''`，
     否则 Windows 上整份文件换行被翻、diff 满天飞。

8. **`menu 名(x):` 紧贴写法不修**（已知边界）：引擎两种都收、工具从不生成紧贴形态、
   只影响"手写 + 全量路径"。修它要给 Beat 加一列记"名字后面有没有空格"，收益接近零。

9. **32767 是硬上限，全工具一个数、一种处置（2026-10-18）**：
   `core.cell_limits.CELL_TEXT_LIMIT = 32767`（`beat_model` 再导出同一个名字）。
   - 写表侧：`excel.cells.set_text` **写之前就查**，超了抛 `CellTextTooLong`，
     信息点名「行 · 字段 · 多少字 · 多出多少字」，**半个文件都不留**。
   - 不做"搬进隐藏表「长文本保真」"：那条路把一行 payload 撑到 64KB，
     协作的 `BasePayload` 抄进单元格照样被截成 32767（JSON 断在 char 592），
     第二次协作起字段级合并全部退化。用户拍板改回单元格上限。
   - 老表里那张「长文本保真」**只读兼容**（`_read_long_text` / `_restore_long_text`）：
     读得回来，但再导出会被闸门拦住 → 那句该拆成几拍。
   - 卡片编辑器在**保存那一刻**就拦（`CardPageMixin._too_long_fields` + 字数条提示），
     不等导出才打回。
   - 判决网：`tests/test_cell_limit_alignment.py`（13 条，含边界 32767/32768）、
     `tests/test_hell_carrier_attack.py::test_超长台词不再静默截断_而是明确报错`。

10. **文件级保真块住独立表，不进行 payload（schema 2，2026-10-18）**：
    `file_blocks(label, text, updated_by, updated_at)` —— **整库一份**
    （键 = `LIBRARY_BLOCKS`，空串；`label` 列留着兼容早期按章节分的行）。
    - 根因：`FileBlocks` 曾经塞进"第一拍"的 payload，一行 64KB →
      `BasePayload` 被 Excel 截断 → 第二次协作起字段级合并失效。
    - 为什么不是"一章一份"：Excel 的「文件级保真」表里只有 `块号 + 行文本`，
      没有章节信息，读回来只能挂"第一张拍"；而 pull 出去的拍序与送回来的
      可能不同 → 同一段被登记到两个章节名下 → 再渲染就是**两份 init python**
      （真机当天就踩到；`test_two_pushes_do_not_duplicate_the_blocks` 守着）。
    - `pull` / `export_ir_from_db` 把它**挂回第一拍**（渲染读拍列表）；
      `push` 空文本不动库里那份（真删顶层块请改 `.rpy`）。
    - 老库自动迁移：`connect()` 里一次性搬走 payload 里的 `FileBlocks`，
      `meta.file_blocks_split` 记住"搬过了"。
    - 判决网：`tests/test_collab_second_round.py`（11 条，第 2、3 次协作的字段级合并）。

11. **`BasePayload` 永远塞得进一个单元格（2026-10-18）**：
    `_base_cell_text()` 先试原文 JSON；超过预算（`CELL_TEXT_LIMIT - 1500`）
    就改存**每字段 sha1 摘要**（`{'__hashes__': {field: '\ue006…'}}`）。
    - 三方合并只问"这一格还是我拉取时那样吗"—— 相等性判断，摘要就够，
      合并语义**一字不差**；冲突界面本来就不显示 base。
    - 连摘要都放不下 → 写空 + 明确报"这份表没有基线"，**绝不写半个 JSON**。

---

# §15 增量路径：两条 P0 + 三条 P1 的修正（2026-10-19）

> 出处：`docs/P0保真测试_增量路径_2026-10-19.md`（真机 `sr_general`，55 脚本 /
> 20,568 行）。**改口径的三条**（P0-1 / P1-1 / P1-3）都在这里写明为什么。

## 15.1 P0-1 只换说话人也要落地（`patcher._speaker_swap_ok`）

- **现状**：`_line_key` 的台词键是 `('say', 说话人)`，"说话人一样才允许换正文"。
  表格里把 `jc` 改成 `yhj` → 键不同 → 配不上 → 原文一字不动、报告"改动 0 行"、
  退出码 0（4/4 复现）。
- **修法**：配对加**第二轮**：正文（引号里那一串）逐字相同、只有 `who` 不同
  → 认定"同一句话换了说话人"，允许替换。`_same_thing_unless_rewritten` 同步放行。
- **边界**（都必须拒）：菜单选项行（`"选项":` 的正文是选项文案）、
  关键字行（`extend` / `menu` 的说话人位不是角色名）、正文有一字之差。
- **为什么不用 `trust_ranges`**：表格那条路的行号是 `recover_source_ranges`
  猜出来的（契约 B5）。"正文逐字相同"是比行号更强的证据，不需要动那条纪律。
- **出声**：`_lost_content` —— 生成的内容行既没落地、**整份原文里也没有**
  → `lost_new` → 退回全量并说明。比对用整份文件（不是这一拍的区间）：
  菜单标题一类会被解析器并进 `menu:` 头，按区间比会把零改动往返判成丢失。

## 15.2 P0-2 引擎闸门要有项目上下文（`engine_lint.declaration_context`）

- **现状**：最小工程只有产物本身。`screens.rpy` 的 `style …: language gui.language`
  / `properties gui.button_properties(…)` 在 init 阶段读 `gui.*` —— 那些值在
  `gui.rpy` 的 `define` 里。缺了它们引擎抛 `NameError`，闸门判"引擎读不了"，
  而产物内容 100% 等于原文（3938→3940 行，0 删除，只多 2 行标记）。
- **修法**：扫 `game/**.rpy` 的**顶格 `define`**（右值必须是合法 Python 表达式），
  拼成 `init -100 python:`，每条一个 `try/except`。为什么 try：
  `define x = ParallaxTransform(…)` 依赖项目 `init python` 里的名字，骨架不该
  因此把整次 lint 拖崩。
- **骨架永不背锅**：报错行落在 `00_sg_context.rpy` 上 → 不算产物的错；
  骨架把 init 弄崩（帧全在骨架里）→ 去掉骨架重跑，用重跑的结论并把
  "这次没有上下文"写进 `LintReport.context_note`。
- **要排掉被 lint 的那份源文件**（`exclude=source_path`）：否则它自己的
  `define` 会替它把"丢失的声明"补上，等于掩盖真问题。
- **`transform` 不能抽**：`transform x:` 必须有块体，单独一行就是语法错。
- 代价：一次骨架构建 = 读一遍项目的 `.rpy`（真机 54 文件，毫秒级）；lint 本身
  耗时不变（实测 8.0s vs 8.9s）。

## 15.3 P1-1 零改动往返不写盘（`compile.write_compiled` / `session_export.export`）

- **现状**：增量结果与源文逐字节相同，但仍走 `ensure_generator_marker` →
  文件头多 2 行（标记 + 空行），"零改动逐字节还原"永远差 2 行（22/55 文件）。
- **修法**：`inc.text == 源文` → **目标就是源文件时连写都不写**（mtime 不动）；
  目标在别处时按源文载体风格**原样复制**，同样不加标记。
- **护栏配套**（`generation_guard._copy_of_source`）：没有标记的复制品，下一次
  真有改动再导到同一路径时会被手写护栏拒写。所以"目标已存在、内容与
  `source_text` 逐字节相同"也放行 —— 它是一份复制品，不是别人的手写文件；
  用户在复制品上手改过 → 内容不再相等 → 照旧拦。
- 报告口径：`CompileResult.untouched` → CLI 说"0 行改动，原文逐字节相同
  （文件没有被改动）"，而不是"只改了 0 行"。

## 15.4 P1-2 `类型` 列非法值要点名（`beat_model.unknown_kinds` / `kind_raw`）

- **现状**：`Beat.from_row` 把不认识的 `类型` 一律收成 `say`，于是「旁白」
  既不报错也不落地，报告只说"改动 0 行"。
- **修法**：`Beat.kind_raw` 留原值（运行时元数据：不入表、不进快照/指纹/协作库），
  `compile_beats` 用 `unknown_kinds` 收集 → `CompileResult.invalid_kinds` →
  报告点名 `[行号]「原值」` 并列出合法值。UI 导出走 `_with_sanitize_note` 同一句话。
- **不做的**：不替用户翻译（「旁白」→ `narration`）。契约 M 不变量：工具不猜语义。

## 15.5 P1-3 删空块的计划阶段拦截（`patcher._deletion_empties_block`）

- **现状**：删掉 `if breakline == 1:` 体里唯一那一拍 → 产物 `expected statement`
  （`pomodorro.rpy` 第 232 行）。门禁拦住了，但表已改、文件没改，用户不知道怎么办。
- **修法**：删除计划里多加一条准入：上一行（跳过空行与纯注释）是块头、
  且删完之后块体里**没有一行活着** → 判"这刀不敢删" → `ok=False` →
  调用方退回全量重生成（产物有效，改动照样落地）。
- 空行与纯注释**救不了空块**（Ren'Py 眼里它们不是语句），扫描时视为透明。
- 门禁拦下时的说法也要给退路：CLI 与 `CompileResult.summary` 都写明
  "源文件没有被改动：它仍是导出前的样子" + "改好表再导一次 / 撤销这次改动再导"。

## 15.6 顺带修掉：多 label 章节的插入点下标

`_recover_ranges_for_groups` 把**组内下标**当全局下标塞进 `inserted_out`。
`pomodorro.rpy` 有 10 个 label：插入一拍时插入点指到别的组的拍上 → 新卡片
走"替换"通道、把原文那一行顶掉 → `_verify_patch` 报"某句台词在补丁结果里
找不到" → 退回全量（实测 60 段）。现在用 `{id(beat): 全局下标}` 映射。

## 15.7 判决网

- `tests/test_incremental_p0_fixes.py`（10 条：说话人 / 删除拒绝 / 全局下标 /
  非法类型 / 骨架 / 零改动往返）。
- 改口径的两条既有用例已同步：`test_incremental_export.py` 的
  `test_deleting_the_only_body_of_a_branch_is_refused` / `test_deleted_beat_is_reported`，
  `test_export_acceptance.py::test_4_re_exporting_the_same_path_is_idempotent`
  （从前断言首行必须有生成标记，现在断言逐字节等于源文）。

---

# §16 三件地基：资产维度 / NEST 收口 / 协作三件小事（2026-10-19）

> 用户排期：①资产维度结构化 → ②`NEST_CONFLICT` 收口 → ③协作三件小事；
> 多引擎继续降权。本节的每条都配了可复跑的判据。

## 16.1 资产维度结构化（`assets/asset_dimensions.py`）

- **数据模型**：图片表新增两列（追加在**末尾**，老列位置不动）：
  `Group`（维度，如 `outfit` / `expressions`）与 `Value`（该维度下的一个属性，
  如 `base_swimsuit`）。`Attributes` 保留为**兼容列**：老表、老代码、
  "一行登记多个属性"的脚本声明照样能读。
- **结构来源三档**：引擎事实（`storygraph.engine_facts.json` 的
  `attributes`）→ 项目 `layeredimage`（延迟 import，不走反向顶层依赖）→
  认不出就留空。**不写死组名**，也不猜。
- **不再无脑拆词**：`base_handdown` 是一个属性；只按空白/逗号/顿号/分号/
  竖线分词。平铺文件名只在"剥完的前缀确实是已知属性"时才去 tag 前缀
  （`eileen_happy` → `happy`；`lan_angry` 整体已知就保留）。
- **回填只填空白**：`backfill_dimension_columns` 给老表追加两列并填空格，
  写前 `*.bak.xlsx`；人写过的值、`Attributes`、其它列一个字节不动。
- **下游接线**：
  - `Symbols.dimensions_of_tag` / `dimension_groups_of_tag`；
  - 立绘卡第四档结构来源（没有引擎事实也能一维一格，≥2 个维度才接管）；
  - 下拉（`expression_groups_for`）按维度分栏；
  - 校验新增 **W115**：同一维度同时选两个值（引擎 lint 不管这类）；
  - 多引擎 IR 的资产清单带 `{维度: 值}`；同组多个值时第一个进 `attributes`、
    其余进 `attribute_values`（绝不静默丢词）。
- 判据：`tests/test_asset_dimensions.py`（22 条）。

## 16.2 `NEST_CONFLICT` 收口：**留结构位、删对拍器**

- **现场**：`the_question/script.rpy:28-37` 的选项体只有一条 `jump`。
  表格往返后 `_menu_member` 没了、`Condition` 也是空串，老推断把 `jump`
  摆到**顶层**；结构列写的是 `menu:1>B`。实测渲染：结构列对（`jump` 在
  选项体里，且缩进正确）。
- **定案**：`Nest`（结构位）是**唯一结构真源**。渲染在路径齐全时直接按
  路径建树；老推断只在"整份 label 都没有结构位"（老表）时兜底。
- **删掉的东西**：`beat_render.nest_placement_conflicts`、`_actual_chains`、
  `core.nest_path.body_chain`、`generation_guard.nest_conflict_warnings`、
  `compile.py` 的对拍调用、`session_export` 的 `structure_conflicts` 字段与
  说明。测试里凡是"对拍器应报/不应报"的断言都改成"路径权威"的断言。
- 判据：`tests/test_nest_column.py::TestPathAuthority`（`jump` 选项体往返
  仍在体内；对拍器已不存在）、`tests/test_nest_phase2.py`。

## 16.3 协作三件小事（`collab/collab_store.py`，schema 3）

- **WAL + busy_timeout**：连接统一 `PRAGMA journal_mode=WAL`、
  `busy_timeout=30000`、`synchronous=NORMAL`、`foreign_keys=ON`；
  写操作（push / lock / 状态机 / 资产）用 `connect(write=True)`，
  进入即 `BEGIN IMMEDIATE` —— "读一下再写"的 CAS 不再被两个进程同时读到
  旧 revision。WAL 在网络盘/只读介质失败时**不炸连接**（退回默认 journal）。
- **锁租约**：`beats.locked_until`（schema 3 新增），默认 15 分钟。
  `lock(ttl=)` / `renew_lock` / `locks()` 返回 `expired` 与 `expires_in`；
  别人的锁**过期后可接手**（`lock_takeover_expired` 审计），没过期照旧拒；
  push 越过过期锁时记 `push_lock_expired`，不当成抢锁。
- **成员权限**：`users.role`（schema 3 新增，默认 `editor`）三档
  `viewer` / `editor` / `owner`：只读成员 pull/status 照常，但 push、锁、
  改状态一律拒；owner 额外能改角色。老库没有 owner 时允许**第一次认领**
  （否则迁移后权限死锁）。CLI 新增 `collab members` / `collab role` /
  `collab renew`，`collab init --owner`、`--ttl`。
- 判据：`tests/test_collab_lease_roles.py`（15 条）；
  既有 `test_collab_merge` / `roundtrip` / `second_round` 全绿。

## 16.4 并行协作纪律（给同时改 bug 的另一个 AI 留位置）

- 本轮只动"三件地基"相关文件；`docs` 只**追加**本节，不重写既有章节。
- 提交按文件分组（资产 / NEST / 协作 / 文档），不 `git add -A`；
  提交前回读 diff，避免把并行会话的未完成改动卷进来。

## 16.5 跨 Unity 第一片（IR schema 1.1）

- 中立 IR 的 `lines[]` 新增：
  - `structure = {path, kind, known}`：块路径 + `if/menu/loop/option`；
    `known=false` 表示老表不知道，**别当顶层**；
  - `timeline = [{op,target,at,clause,transition,action,params,order}]`：
    `scene/show/hide/with/music/sound/voice/at/transform` 的**作者顺序**，
    `order` 从 0 递增；没被顺序 token 点到的字段补在末尾，绝不丢。
- Unity 适配摊平成 `structurePath` / `structureKind` / `structureKnown`
  + `timeline[]`（JsonUtility 的"标量 + 数组"约束不变）；Godot 保留嵌套。
- `UNITY_IMPORT.md` 与 `docs/unity/StoryIr.cs` 是同一份字段契约。
- **不是播放器**：条件求值、菜单 UI、立绘合成、资源地址映射仍归 Unity 工程。
- 判据：`tests/test_unity_timeline.py`（5 条）、
  `tools/hell_multi_engine.py`（五引擎/多文件/真实项目 0 处）。


# §17 舞台预览：按项目 gui / 字体 / ATL 真实排版（2026-10-20，B2）

用户 2026-10-20：

> 舞台的预览效果必须更真实！最好跟真正编译的一模一样……目前先做 Renpy 的吧！

这一节记的是"预览凭什么说跟编译出来的一样"，以及**实测数字**。
结论先写：`the_question` 的两个场景（原样 gui / 改过 gui）里，
立绘位置 Δ0px、台词起点 Δ0px、名字起点 Δ0~1px；只有字宽差 1~3%
（引擎在 2x 下排版再缩回，字形舍入不同，不是排版错）。

## 17.1 结构：Qt 只负责贴图

- `storygraph/story/renpy_gui.py`：读项目 `gui.rpy` / `screens.rpy` /
  角色定义 → `GuiProfile`（分辨率、文本框高度、名条/台词坐标与宽度、
  字号、颜色、描边、字体、文本框底图、角色名/前后缀）。
- `storygraph/app/stage_render.py`：**纯 Pillow 离屏合成整帧** ——
  背景 → 立绘（`story.atl.Placement.box` 的真实几何）→ 图层 →
  文本框底图 → 名条底图 + 角色名 → 台词（换行/颜色/描边/`{tag}`）。
  不 import Qt，可无头测试。
- `ui_qt/stage_preview.py`：`StageCanvas` 在后台线程调 `render_frame`，
  拿到一整张 RGBA 图后等比贴到控件上（letterbox，与全屏播放一致）。
  没渲染完**不贴上一帧**；缺图/缺字体/位置是猜的，全部进横条提示。

## 17.2 真机对照工具（不是 pytest）

`tools/renpy_stage_compare.py`：

1. 临时复制 SDK 自带的 `the_question`，写一段固定脚本；
2. 用 Ren'Py 自带的 testcase 跑真引擎、`screenshot`；
3. 用 `stage_render` 画同一帧；
4. **逐项量**：立绘顶边/左边缘、名字文字框、台词文字框（x/y/w/h）；
5. 任一项超容差 → 退出码 3。产物 `report.txt` + `compare-<场景>.png`。

两个场景，第二个专治"写死"：

| 场景 | gui | transform | 台词 |
|---|---|---|---|
| `default` | 原样（文本框 185 / 名条 240 / 台词 268,50,744 / 22,30 号） | 项目里的 `sg_center` | 单行 |
| `custom` | 文本框 260 / 名条 600 / 台词 620,60,560 / 28,34 号 + 粗体 | 内置 `left` | 长句换行 |

实测（`report.txt`）：

```
default  立绘 (24, 509) 引擎=编辑器        台词起点 (270, 589) Δ0
         名字 (247, 545)    Δ0/1px          台词换行：1 行
custom   立绘 (24, 36)    Δ0               台词起点 (621/622, 525) Δ1
         名字 (608, 471)   Δ0/1px          台词 4 行，断行位置一致，高 126 = 126
```

容差规则：**位置 2px 严格**；**字宽/字高 4%** —— 引擎截图是 2x 再缩回，
同一句 236 vs 243px 属于字形舍入，不是排版错。判断"写死"看的是位置，
判断"字号读错"看的是字高（4 行 = 126px，字号错一档就破）。

跑法（Windows，每次约 8 秒 ×2，CI 不跑）：

```bat
python tools/renpy_stage_compare.py --sdk D:\...\storygraph_fork_sdk
```

## 17.3 内置 transform 按 SDK 源码抄（三个错）

`_BUILTIN_PROPS` 直接照 `renpy/common/00definitions.rpy`：

```renpy
transform left:          xpos 0.0 xanchor 0.0 ypos 1.0 yanchor 1.0
transform right:         xpos 1.0 xanchor 1.0 ypos 1.0 yanchor 1.0
transform center:        xpos 0.5 xanchor 0.5 ypos 1.0 yanchor 1.0
transform truecenter:    xpos 0.5 xanchor 0.5 ypos 0.5 yanchor 0.5
transform offscreenleft: xpos 0.0 xanchor 1.0 ypos 1.0 yanchor 1.0
transform default:       … xpos 0.5 xanchor 0.5 ypos 1.0 yanchor 1.0
```

旧实现错在三处，都是"看起来差不多、真比就差一截"：

1. `left/right/center` 只抄了 x，没抄 `ypos 1.0 yanchor 1.0` ——
   立绘是**贴底站**的。700 高的图只差 20px，400 高的图差 320px；
2. `offscreenleft/right` 用了 `xalign -0.5/1.5`（半张图还在屏内），
   正确是 `xpos 0 xanchor 1` / `xpos 1 xanchor 0`（整张在图外）；
3. `show x`（没有 `at`）是 `config.default_transform`，即**居中贴底**，
   旧实现按左上角画。

真机探针（同一张 334×700 立绘，720 高的窗口）：

| 写法 | 引擎实测顶边 | 说明 |
|---|---|---|
| `show sylvie`（无 at） | 20px | default transform：贴底 |
| `at top` | 0px | |
| `at left` | 20px，左边 0 | left 也贴底 |
| `at sg_x`（项目里只写 `xalign 0.5`） | 0px | **自带 transform 不继承 default** |

最后一行是关键：`at_list` 里的 transform 继承的是**子节点**的位置，
不是 `default_transform`。所以 `_from_props_ordered` 的兜底仍是 0/0，
只有"没有 at"才用 default —— 这两条现在都有测试钉着。

## 17.4 字体：按引擎的搜索顺序

旧实现最后才找 SDK，先扫 `C:\Windows\Fonts`，于是 `DejaVuSans.ttf`
落到了微软雅黑上：字号一样、**字不是那个字**（"Sylvie" 82px vs 引擎 88px）。

新顺序：项目内相对路径 → 项目里同名文件 → **SDK 的
`renpy/common` / `gui` / `sdk-fonts`** → Windows 系统字体。
SDK 用"项目所在 SDK"优先（`<sdk>/the_question` 的祖父目录），
再退回 `engine_collect.find_sdk()`。

替换要**说清楚**：

- 同名（项目没带、SDK 有 `DejaVuSans.ttf`）→ 不算替换，不报警；
- 真的换了字（想要 `fonts/x.ttf`，用了 `msyh.ttc`）→ `warnings` +
  `GuiProfile.fonts_substituted = ["x.ttf → msyh.ttc"]`，预览横条显示。

修完之后同一个 "Sylvie"：引擎 87px / 编辑器 86px（Δ1）。

## 17.5 回归线（不依赖 SDK）

- `tests/test_stage_render.py`（19 条）：文本框/台词/名条的几何来自
  `gui.*`；名字用角色色；没台词不画窗；长台词换行不出框；未知 tag 不漏源码；
  立绘框 = `Placement.box`；zoom 改尺寸；缺图/缺背景进 warnings。
- `tests/test_stage_fidelity_golden.py`（8 条）：把 17.2 量到的**引擎数字**
  写成断言（文本框 (0,535,1280,185)、立绘 (473,20,334,700)、
  名字起点 (247,545)、台词起点 (270,589)），换一套 gui 后全部跟着走。
- `tests/test_stage_geometry_exact.py`：内置 transform 逐条对 SDK 源码
  （贴底、`top`、`offscreen*`、无 at、自带 transform 不继承 default）。
- `tests/test_renpy_gui_profile.py`：字体搜索顺序三条（SDK 同名优先、
  同名不算替换、项目自带字体最优先）。

## 17.6 还没做的（边界写在前面）

- **快捷菜单**（Back/History/Skip/…）引擎每帧都画，预览没有；
  它是另一个 screen + hbox 样式，下一步按 `screens.rpy` 解析；
- **HiDPI**：引擎按物理倍率排版（本次截图是 2x 缩回），预览按 1x 排版，
  字宽因此有 0~3% 的舍入差；
- **ATL 动画**只取最终态（`linear`/关键帧/`parallel`），
  预览横条已标"位置是猜的"（B1），不假装知道中间帧；
- **转场中间帧**不做：`with dissolve` 只影响预览的交叉淡入；
- **自定义 say screen**（bubble / nvl / 完全重写布局）不在第一版范围，
  `GuiProfile.screen_say_custom` 会记下"这项目改过 say"，但只按标准模板画。


# §18 多引擎损失报告：导出成功 ≠ 导全了（2026-10-20）

用户本轮目标里有一句"增强多引擎 IR 与适配（校验 / **损失报告** / 更多引擎）"。

§16.5 把 Unity 第一片做出来了，但导出回执只说"成功"。一份
`story_ir.json` 的行级字段有三十来个，Unity 适配层有 8 个没搬
（DSL 源码 / 原始行 / 策划备注 / 立绘站位 / 转场参数 / DSL 舞台原文……）
—— 用户根本不知道没过去，接 Unity 的人会以为"跟编辑器里看到的一样"。

## 18.1 只做对账，不改数据

`storygraph/pipeline/ir_loss.py`：

- 中立 IR **一个字段都不删**（它仍是全量事实）；
- 适配层 `to_unity()` / `to_godot()` 实际带走的字段写在
  `LINE_FIELD_MAP` / `ASSET_FIELD_MAP` 里（`''` = 明确不带）；
- 对账结果 → `FieldLoss(scope, field, label, count, examples)`；
- 报告落盘到导出目录：`IR_LOSS-unity.md` / `IR_LOSS-godot.md`
  （`LOSS_REPORT_NAME`），`expected_files()` 同步声明这两个文件名。

> **对账的前提是"同一份 IR"**：多章导同一目录时文件名是固定的，
> 合并规则（同一个 `line_id` **以最后一次导出为准**，同一章里被删掉的行
> 也要跟着消失）见 §22.1。那一层错了，这份报告会对着一份旧数据算得很干净。

报告的骨架：

```
# IR 损失报告 · unity
- 行：128，资源：340
| 字段 | 含义 | 数量 | 例子 |
| `code` | DSL 源码 | 128 | a.0001、a.0002 |
| `attributes` | 维度结构（{组: 值}） | 340 | e happy |
## 提醒（不算字段丢失）
- 17 行台词带 Ren'Py 标签（{color=…} / {w}），IR 只保留原文…
- 3 行的嵌套位置未知（老表没有结构位）…
## 怎么补
（改 to_<engine>() 之后同步映射表）
```

## 18.2 当前实测：两个引擎各丢什么

| 字段 | Unity | Godot | 说明 |
|---|---|---|---|
| `code` / `raw` / `comment` | 丢 | 丢 | DSL 源码、原始行、策划备注 |
| `at` / `transform` | 丢 | 丢 | 立绘站位（只有 `poses` 顺序） |
| `stage`（DSL 原文） | 丢 | 丢 | 快照已经算好，原文仍在中立 IR |
| `music_params` / `sound_params` | 丢 | 丢 | `fadein 1.0` 这类参数 |
| `attributes` / `attribute_values` | 丢 | **带** | §16.1 的维度结构化成果；Godot 原样带 assets |
| `loop` / `volume` | 丢 | 带 | 资源行上的播放属性 |

顺手补了一条真缺陷：Godot 的 `stage` 里原来没有 `sfx_action`（Unity 有
`sfxAction`），同一个项目两边行为不一致 —— 现在补上并有测试。

**下一步（本轮没做，先在报告里点名）**：把资源的 `attributes` 接进 Unity。
JsonUtility 不支持 `Dictionary`，但支持结构体数组，所以形状应该是
`AttributePair[] { group, value }`；同时要改 `docs/unity/StoryIr.cs` 与
`UNITY_IMPORT.md`（同一份字段契约）。在那之前，报告会一直列着这一条 ——
这就是它存在的意义：不假装导全了。

## 18.3 回执与 UI 也要说

- `Session.export_ir()` 返回值多一个 `loss = {count, by_scope, notes}`；
- 「更多 → 导出到其他引擎」的状态栏写 `✔ 已导出 unity 格式（4 个文件）· 损失 4 类字段`，
  控制台逐条列 `code×128（line）` 并指向 `IR_LOSS-unity.md`；
- `UNITY_IMPORT.md` 的产物清单里加了这条报告，写明"接之前先看它"。

## 18.4 判据（`tests/test_ir_loss.py`，13 条）

- 适配器没带的字段**必须**出现在报告里（`code/raw/comment/at/transform/
  music_params/sound_params/stage`）；
- 适配器带了的字段**不许**误报（`structure` 被摊成三个标量，算带过去）；
- **漂移守卫**：IR 里每个非空字段，要么在报告里，要么在 `to_unity()`
  的产物里真能找到；适配器加了字段却忘改映射表 → 测试红；
- 资源维度：Unity 丢 `attributes` 必须点名，Godot 不丢；
- `renpy` / `json` / `csv` 直接用中立 IR → 没有适配层，报告为空；
- 导出目录里真的有 `IR_LOSS-unity.md`，且 `expected_files()` 与产物集合完全一致。

## 18.5 边界

- 报告说的是"**适配层没带**"，不是"目标引擎做不到"：Unity 当然可以自己
  解析 DSL，只是这份产物没给；
- 报告不做"重要性排序"（都是没带，谈不上谁更重要）；
- 不改 `to_unity()` 的既有字段结构（JsonUtility 的约束见 §16.5）。


# §19 资产维度再细一层：来源 + 置信度 + 分组元数据（2026-10-20）

§16.1 把 `{维度: 值}` 做出来了，但它有一个软肋：**猜出来的维度和表里写死的
维度长得一模一样**。`ling_happy` 剥掉前缀得到 `expressions=happy`，和作者
亲手在 `Group`/`Value` 里写的 `expressions=happy`，下游（立绘卡 / 校验 /
多引擎）分不出来。用户本轮目标点的"置信度"就是这件事。

## 19.1 每一维都带来源

`assets/asset_dimensions.py` 新增 `explain_dimensions()`，
`dimensions_of_row(..., meta=True)` 转调它：

| `source` | `confidence` | 什么时候 |
|---|---|---|
| `explicit` | 1.00 | 表里 `Group` + `Value` 都写了（人写的） |
| `value` | 0.95 | `Value` 是人写的，维度由引擎事实分组 |
| `fact` | 0.90 | 维度来自 `storygraph.engine_facts.json` |
| `layeredimage` | 0.80 | 维度来自项目 `layeredimage` |
| `unknown` | 0.15 | 认不出（`group` 为空 —— **仍然不猜**） |

**剥过 tag 前缀的再减 0.2**（最低 0.1）：`ling_happy` → `happy` 是一次
推断，"剥对了吗"本身要打折。`meta=False` 时返回的还是 `{group, value}`，
老调用方一行都不用改。

数字不是概率，是**排序刻度**：1.0 / 0.9 / 0.8 只回答"谁更可信"。

## 19.2 分组元数据：整组按最弱的一维算

`group_metadata(rows)` → `{tag: {group: {values, sources, confidence, guessed}}}`：

- `values` 这一组出现过的值（保序去重）；
- `sources` 这一组用过哪些来源；
- `confidence` = 组内**最低**的那一维（有一维是猜的，整组不可全信）；
- `guessed` = 有维度 `unknown` / 剥过前缀 / 置信度 < 0.7。

只收认得出的组；认不出的值仍在 `attribute_values` 里，不在这里"消失"。

## 19.3 往下游接

- **IR**：图片资源条目新增 `attribute_meta: [{group,value,source,confidence}]`
  （`pipeline/engine_exporters._asset_manifest`）。Unity 适配层目前不带它 ——
  §18 的损失报告会列出来（`attribute_meta` 与 `attributes` 并列）。
- **概览**：`Session.asset_overview()` 多一个
  `dimension_confidence={groups, guessed}`；「资产统计」窗口多一行
  `【维度】N 组，其中 M 组是猜的（低置信度）`。
- **缓存**：`enrich_image_rows` 顺手存 `_dim_meta`，下游不用重算。

## 19.4 判据（`tests/test_asset_dimensions.py` 新增 13 条）

- 五种来源各自的 `source` / `confidence`；
- 剥前缀 → `stripped=True` 且 0.7；
- 认不出 → `group=''`、`unknown`、0.15；
- `meta=False` 的返回形状不变（老调用方不受影响）；
- `group_metadata` 的组级置信度取最弱、`guessed` 传导；
- IR 的 `attribute_meta` 真的带出 `source/confidence`。

## 19.5 边界

- 置信度**只用于排序和提示**，不参与"自动改表"：低置信度不等于错；
- UI 目前只显示"多少组是猜的"；立绘卡按置信度分色是下一步；
- Unity 侧要拿到这些元数据，得按 §18.2 的下一步做 `AttributePair[]`
  （JsonUtility 不支持 Dictionary，但支持结构体数组）。


# §20 中立 IR 自检：导出的这一份自己对不对（2026-10-20）

用户本轮目标里"校验"两个字，§18 只兑现了一半：损失报告说的是
**"目标引擎少拿了什么"**，不是**"交出去的这一份本身有没有坏"**。
一份 `story_ir.json` 有三十来个行级字段，下游（Unity 导入器、翻译公司、
埋点脚本、存档迁移）只认 `line_id` 和 `stats` 这类"看起来像真值"的东西。
它们一旦自相矛盾，**没有任何一处会报错**：

| 坏数据 | 真实后果 |
|---|---|
| `line_id` 重复 | 存档/埋点按 id 存，两个不同的拍被当成同一句 |
| `stats.lines` 与 `lines` 长度不符 | 产品按统计做进度条，差一截 |
| `stage[]` 指向不存在的 `line_id` | 导入器要么跳过要么崩，都不说 |
| `timeline[].order` 不是 0..n-1 | 适配器按 order 排序，演出顺序全乱 |
| `structure.known=false` 却带 `path` | 两处互相打脸，重建菜单树时信谁？ |
| `actors` 里挤着 `a; b` | 一张立绘一个字符串才对，挤在一起两边都画错 |

这些**不是目标引擎的损失**（§18 的字段对账看不见它们），而是"这份 IR
生成完就坏了"。所以放在导出之前跑，产出 `IR_CHECK.md`，并把错误/警告数
写进导出回执与状态栏。

## 20.1 检查清单

`storygraph/pipeline/ir_validate.py`，`check_ir(ir)` 返回聚合后的
`Issue(level, code, message, scope, count, examples)`。模块共 **41 个代码**
（含"不是数组/不是对象"这类形状检查），下表列主要的：

| 代码 | 级别 | 查什么 |
|---|---|---|
| `E-IR-SCHEMA` | 错 | 没有 `renpy-story-ir/<版本>` |
| `W-IR-SCHEMA-VERSION` | 警 | 版本比本工具认识的新 |
| `E-IR-EMPTY` | 错 | `lines` 为空（导的是空章节） |
| `E-IR-ID-EMPTY` / `E-IR-ID-DUP` | 错 | 主键为空 / 重复（重复按"几行共用"计数） |
| `E-IR-LINE-KEYS` / `E-IR-LINE-SHAPE` | 错 | 缺下游要用的键 / 不是对象 |
| `E-IR-STATS` | 错 | `lines/labels/characters/spoken_lines/chars` 与实际不符 |
| `E-IR-STAGE-ORPHAN` / `E-IR-STAGE-DUP` | 错 | 快照指向不存在的行 / 同一拍两份快照 |
| `W-IR-STAGE-MISSING` | 警 | 有拍没有快照（跨章合并时正常） |
| `E-IR-TIMELINE-ORDER` | 错 | `order` 不是 0..n-1 |
| `E-IR-TIMELINE-VALUE` | 错 | 事件里出现非标量（适配器只认标量） |
| `E-IR-STRUCTURE` | 错 | `known=false` 却带 `path` |
| `W-IR-STRUCTURE-UNKNOWN` | 警 | 这些拍不知道嵌套位置，目标引擎只能当顶层 |
| `E-IR-ACTOR-JOINED` | 错 | `actors` 里有多张图挤在一个字符串 |
| `W-IR-ACTOR-UNKNOWN` | 警 | 立绘 tag 在资源清单里找不到 |
| `W-IR-SPEAKER-UNKNOWN` | 警 | 角色变量不在 `characters` 里（跨章正常，但要说） |
| `W-IR-MARKUP` | 警 | 台词里还有 `{color=…}` / `[name]`（IR 只保留原文） |
| `E-IR-ASSET-DUP` / `E-IR-ASSET-ID` | 错 | 资源 id 重复 / 为空 |
| `W-IR-DIM-NO-META` | 警 | 有维度结构却没有来源/置信度 |
| `W-IR-DIM-GUESSED` | 警 | 维度是猜的（`unknown` 或置信度 < 0.7，§19） |
| `E-AD-UNITY-NESTED` | 错 | Unity 产物里出现嵌套字典（JsonUtility 反序列化不了） |
| `E-AD-UNITY-KEYS` | 错 | Unity line 缺 `lineId` / `speakerId` / `text` |

计数规则：同代码聚合成一行，`count` 是**受影响的条目数**（重复主键按
"几行共用这个 id"算），例子最多留 8 个 —— 报告是给人扫的，不是日志。

## 20.2 落在导出的哪个位置

- `export_ir()` 在写完适配产物**之后**写 `IR_CHECK.md`（每个引擎都写：
  中立 IR 是每次导出都有的产物；放最后才能连适配器约束一起查）；
- `expected_files()` 同步声明这个文件名 —— 五个引擎的"产物集合"测试
  逐条对账，多一个少一个都会红；
- `Session.export_ir()` 回执多一段
  `check={error_count, warning_count, by_code, issues}`；
- 「更多 → 导出到其他引擎」：有错误时状态栏是 **⚠**（不是 ✔），控制台
  逐条列 `代码×数量：说明`，并指向 `IR_CHECK.md`；只有警告时 ✔ 后加
  `· 自检 N 个警告`；
- 导出目录里同时有两份报告，分工写清楚在各自的抬头：
  `IR_CHECK.md` = 数据自己对不对；`IR_LOSS-<engine>.md` = 目标引擎少拿了什么。

## 20.3 Unity 的硬约束单独查

`to_unity()` 的产物要能被 `JsonUtility.FromJson<T>` 直接吃，而它
**不支持 Dictionary**：`{"stage": {"background": "..."}}` 会让整份数据
解析失败。所以 `structure` 被摊成三个标量、`timeline` 用扁平对象数组
（§16.5）。`check_adapter('unity', payload)` 递归找 `{键: {…}}` 形态
（数组里的结构体是允许的），命中就报 `E-AD-UNITY-NESTED` 并给出路径
（`line[0].stage`）—— 以后谁再往里塞嵌套字典，导出当场就有结论，
而不是等 Unity 那边报"JSON 解析失败"。

只有 Unity 有这条规则；Godot 的 `JSON.parse` 吃嵌套，`check_adapter`
对它返回空列表（不为对称而编规则）。

## 20.4 判据（`tests/test_ir_validate.py`，36 条）

- 干净 IR **零错误零警告**（含"什么字段都有"的那份）—— 否则检查在制造噪音；
- 上面每一行坏数据都有对应代码，且 `count` / `examples` 对得上；
- `W-IR-SPEAKER-UNKNOWN` 只在**有角色表**时出现（空角色表不该刷屏）；
- 报告抬头有行数/资源数/错误警告数，正文有代码与"自查了什么"；
- 坏数据（`lines` / `assets` 不是数组）下报告也能生成，不在报告里再崩；
- 五个引擎都写 `IR_CHECK.md`，且产物集合与 `expected_files()` 一致；
- 真 `build_neutral_ir()` 出来的 IR **零错误**；
- Unity 产物嵌套字典/缺键被抓到，真 `to_unity()` 产物通过；
- **漂移守卫**：`KNOWN_SCHEMA_VERSIONS` 必须含 `engine_exporters.SCHEMA_VERSION`
  —— 加了 IR 版本却忘了自检，测试红。

## 20.5 边界

- **非阻塞**：错误只报告、不拦导出 —— 用户可能就是要拿这份数据去排查；
  但回执写"自检 N 个错误"，不会让它悄悄出去；
- 只查**结构自洽**，不查剧情语义：label 是否存在、`jump` 目标是否可达、
  条件表达式是否合法，仍是 `pipeline/validate.py` 的活（三级校验）；
- 不做 JSON Schema 式的强类型校验（字段类型宽松是中立 IR 的刻意选择：
  Unity 的 `JsonUtility` 要求标量，Godot 允许嵌套，两边的约束不同）；
- `W-IR-MARKUP` 是**提醒**不是错：IR 保留台词原文，目标引擎自己决定
  怎么处理 Ren'Py 标记。

# §21 交付自检的"导出主链路"按两种形态判（2026-10-20）

`python cli.py selfcheck` 在 HEAD 上原本是红的：

```
[失败] 导出主链路（多引擎 + 落盘 + 门禁）| 产物缺少生成标记（首行：default score = 0）
```

**不是导出坏了，是判据与合同脱节**：`_check_export` 要求产物首行含
`# 由剧本表自动生成…`，而 P1-1（§15）起"零改动往返"**刻意不插**标记 ——
自检的样例章节一个字都没改，走的正是那条路，产物按合同就该是逐字节副本。

## 21.1 判据拆成两档

| 导出回执 | 判据 | 为什么 |
|---|---|---|
| `untouched=True` | 与源文**逐字节相同** | 零改动给的保证比"有标记"更强；标不标记是 P1-1 的选择 |
| 其余（真生成了内容） | 首行 == `GENERATOR_MARKER` | 护栏靠它认自家产物，"再导出一次"不许被拦 |

这不是放宽：第二种形态（改过一章）仍是硬要求。`tests/test_delivery_selfcheck.py`
三条分别钉住 —— 自检这一项真跑必须返回空串；改过一章 → 首行必须是标记；
没改 → 副本逐字节等于源文且**没有**标记。

## 21.2 为什么它能红着存在

pytest 里**没有一条用例执行 `selfcheck`**（`test_single_write_entry.py`
只扫它的写盘入口，不跑）。而它在三处被当真：`README` 的打包排查、
`tools/attack_hell_project.py`、`docs/路线图_长期目标_2026-10-08.md`
的"③ `cli selfcheck` 全过"。一条没人跑的门禁等于没有 —— 所以这次
补的是**执行**，不是措辞。

## 21.3 现跑

```
[通过] 依赖模块可导入 —— 全部 24 个模块通过
[通过] 导出主链路（多引擎 + 落盘 + 门禁）
[通过] CLI build 流水线
[通过] Qt 界面可构建
结论：全部通过     exit=0
```

# §22 一轮"报成功但没写进去"的清扫（2026-10-20）

用户拿真机 `chapter0_0.rpy`（245 拍）跑了一遍导出，报了 8 条。它们的共同点
不是"崩了"，而是**每一处都说成功**：回执 `ok: true`、状态栏 ✔、
`IR_CHECK.md` 写着"0 错 0 警告"、损失报告也正常 —— 而内容没写进去。

| 级别 | 问题 | 症状 |
|---|---|---|
| P0 | 改一句再导同一目录，IR / Unity 里还是旧句 | 回执成功、自检 0 错 |
| P1 | 自检在坏 IR 上先崩，`IR_CHECK.md` 出不来 | `TypeError: string indices…` |
| P1 | 损失报告对 `assets` 形状不防御 | `AttributeError: 'list'…` |
| P1 | 回执分不清"自检没跑成"和"零错误" | `error_count: 0` |
| P2 | 单值 `Attributes` 与 `Value` 不一致时被静默丢弃 | `{Value:happy, Attributes:angry}` → 只剩 `happy` |
| P2 | 多值行上显式 `Group` 整列失效 | `outfit=base` 掉到 0.15（unknown） |
| P3 | stage 覆盖率漏报 | 孤儿 id 被算进"已覆盖" |
| P3 | `role_of` 认不出角色时兜底成 editor | fail-open |
| P3 | 字体 / 图片索引缓存不含 mtime | 换字体、加图要重启 |

## 22.1 P0：合并的规则是"同 key 用旧值"

`_merge_by_key` 从前是"老的先占位、同 key 的新条目 `continue` 跳过"。
而 `line_id` 是 `label.序号`（`beat_model.assign_identity`）——**改一句台词
不会换 id**，于是"改完再导一次"恰好落在"用旧值"上。

更坏的是它**自洽**：`story_ir.json` 旧 → `UnityDialogueData.json` 从它生成
也旧 → `IR_CHECK.md` 对这份旧数据算 0 错 → 状态栏 ✔。链路上没有任何一处
会说"你刚才那一改没生效"。

修法两条（缺一不可）：

| 规则 | 为什么 |
|---|---|
| 同 key 留**新**那一份（原地替换，位置不变） | "再导一次"= 按现在的表重写这一章 |
| `new` 里出现的 `label` 就是"这一章被重导了"：那一章里**新一份没有的 `line_id` 删掉** | 只按 id 合并只能"改"不能"删"；删掉的台词会永远留在 IR（和目标引擎）里，而编辑器里早没了 |

合并单位是**章**（`label`），靠的是 `line_id = label.序号` 这个既有约定。
别的章原样保留（多章导同一目录不丢章节，M8 那条老判据不回退）。

边界：**换 label 名**（重命名章节）之后，旧章的行会被当成"另一章"留着 ——
这需要项目级的全量同步，不在这一层。判据见
`tests/test_reexport_same_dir.py`（9 条：改句 / 原地替换 / 删行 / 两章累积 /
只重导一章不动别章 / 同份导出内重复 id / 磁盘上坏 IR / 回执 2 条）。

## 22.2 P1：自检在坏数据上必须先诊断、不许先崩

`ir_validate` 的存在意义是"诊断坏 IR"，但它的适配分支调 `to_unity(ir)`，
而适配器直接 `l['line_id']`：`lines` 是字符串时抛 `TypeError`。它在
`export_ir` 的写盘流程里 —— 于是**坏 IR 让整次导出失败**，`IR_CHECK.md`
根本落不下来，而这份报告正是"告诉用户哪里坏了"的唯一出口。

现在三道：

1. 适配器是**总函数**（`_rows` / `_scalar` / `_strings` / `_mapping`）：
   形状不对收敛成空值，让自检去报 `E-IR-LINE-SHAPE` / `E-AD-UNITY-KEYS`；
2. 损失报告的 `assets` 走 `_asset_rows`：不是对象 → 空，不再 `.values()`；
3. `export_ir` 写报告这一步单独兜底：连自检自己崩了也写一份
   `crash_report`（代码 `E-IR-CHECK-CRASH`），报告第一句是
   "**自检没跑成**，别把这份当成零问题"。

## 22.3 P1：回执里的"自检"栏现在有状态

`session_export.export_ir` 外面包着 `try`，异常一吞，回执里
`error_count` 就是 0 —— "没跑成"和"干净"长得一模一样。现在回执多两个键：

```
"check": {"status": "ok" | "failed", "error": "…", "error_count": …, …}
```

`status != "ok"` 时状态栏说的是"⚠ 已导出 · 自检没跑成，别当成没问题"，
不再是 ✔。

## 22.4 P2：两列打架不许静默丢；多值行也不许丢显式维度

① `row_values` 把"单个词的 `Attributes`"当 `Value` 的兼容镜像整格跳过。
镜像和真值不一致时它就不是镜像了：`{Value:'happy', Attributes:'angry'}`
里 `angry` 一个字不提。判据换成**同名才算镜像**：不同名一律留着，
并标 `source="conflict"`（置信度 0.5），`IR_CHECK.md` 报
`W-IR-DIM-CONFLICT`（**不**混进"这些维度是猜的"）。

② `explain_dimensions` 只在 `len(raw_values)==1` 时才认显式 `Group`：
`{Group:'outfit', Value:'base', _attrs:['happy']}` 里人写的 `outfit=base`
整列失效、掉到 0.15。现在显式 `Group` 只对**人写的那一对**（`Group`+`Value`）
负责：`base` 仍是 `explicit` 1.0，脚本带出来的 `happy` 仍按引擎事实查
（**不**硬塞进 `outfit`），认不出的词照旧 `unknown`。

## 22.5 P3：三条小的

| 位置 | 从前 | 现在 |
|---|---|---|
| `ir_validate._check_stage` | `len(seen) < len(line_ids)`，孤儿也算一"拍" | 只数**落在 `line_ids` 里**的快照；缺的 id 写进例子 |
| `collab_store.role_of` | `role if role in ROLES else ROLE_EDITOR` | 认不出 → `viewer`（最小权限）；**空串**（未登记）不变，`members()` 显示同口径 |
| `stage.image_index` / `renpy_gui._CACHE` | 按项目根永久缓存 | 目录指纹 + 复核节流（1.5s）/ 字体探测路径复核：加图、换字体不必重启 |

`role_of` 那条的方向：**认不出就少给**。空串（本地单人第一次打开 → editor）
是另一条口子，和"库里有这条人但角色是乱码"完全不是一回事 ——
两者从前都落在 editor 上，这才是漏洞的形状。

## 22.6 现跑

```
P0 探针（真机 chapter0_0.rpy，245 拍）
  第一遍  AI真的拥有自我意识吗？
  改成    【改过的台词】→ 再导同一目录
  第二遍  story_ir.json      = 【改过的台词】
          UnityDialogueData.json = 【改过的台词】      结论：通过

P1 探针（8 种坏形状 × 3 个引擎 × 2 份报告）
  0 例崩溃；4 个引擎导出后 IR_CHECK.md 都在；磁盘上放坏 IR 再导也不崩
```

新增判据 35 条：`test_reexport_same_dir.py` 9 条、`test_ir_validate.py` 10 条、
`test_asset_dimensions.py` 7 条、`test_collab_lease_roles.py` 4 条、
`test_stage_preview.py` 2 条、`test_renpy_gui_profile.py` 3 条。
真实项目多引擎（54 个 `.rpy` × 3 引擎 = 162 次导出）：丢文本 0、丢跳转 0。

---

# 23. 「导出」提到最上面那一栏；引擎排版的预览不再黑屏（2026-10-21）

用户两条 UI 反馈：

> 1. 导出应当放在最上面那一栏……因为太常用了
> 2. 预览窗口需要优化：使用引擎预览时，一拍一拍渲染太慢了！需要优化，
>    现在很难加载（尤其面对连续播放时速度不够常常变黑屏）

## 23.1 入口：两页顶部都有「⤓ 导出」，并且排在前面

`_build_toolbars` 里它同时进两条顶部工具栏（结构页 + 卡片页），点的是
**同一个回调** `export_script` —— 导的是"已载入的那一章"，跟在哪一页无关。

位置也是判据的一部分：这条工具栏在 1500px 的真机窗口里就**装不下**
（需要 2349px，多出来的折进右端「»」菜单）。所以：

| 页 | 位置 |
|---|---|
| 剧情结构 | 第 2 个（「🧩 自定义语句」之后 —— 那个是用户 2026-10-10 点名要的"最上方"，不许被挤掉） |
| 卡片编辑 | 第 3 个（「← 返回」「💾 保存这一拍」之后） |

没载入章节时点它，说的是"还没有载入章节：先在图上双击一个模块……"，
不是原来那句"没有可导出的内容"（现在结构页上也能点到，得说清差哪一步）。

判据 `tests/test_toolbar_export_entry.py`（4 条）：两页都有 / 排在前 3–4 /
**1280px 窄窗口里仍然可见**（`widgetForAction(...).isVisible()` —— 溢出进「»」
的就是 False）/ 点下去真的走到导出那条路（换掉文件对话框，不写盘）。

## 23.2 预览：三条机制叠起来，才叫"不黑屏"

一台真机上合成一帧 `1920×1080` 是 25~300ms（首次碰到大盘立绘要解码缩图，
1 秒级）。从前每一拍现合成、还把上一张清掉 —— 播放就是"黑一下、图一下"。

| 机制 | 常量 | 作用 |
|---|---|---|
| 提前合成 | `ENGINE_AHEAD = 4` | 翻到第 i 拍时把 i+1…i+4 一起排进队列；播放时轮到它只是贴上去（4 × 600ms = 2.4 秒余量） |
| 帧缓存 | `FRAME_CACHE_SIZE = 32` | 按 `id(frame)` 存 `(pixmap, warnings, missing)`；来回翻、重播 0 延迟 |
| 不黑屏 | `_last_good_pix` + `BADGE_DELAY = 0.25` | 这一拍没好时画上一张顶着；等超过 0.25s 才出"正在渲染"角标（快机器不闪） |

另外两条容易漏的：

- **过期任务不算**。每次换拍 `_render_token += 1`、`gate.gen` 跟着改、
  `pool.clear()` 丢掉还没开跑的任务；工作线程开跑前先看 `_Gate`（纯 Python
  对象，不碰 `QWidget`），过期就直接返回。连翻 10 拍最多算 4 张整帧。
- **渲染队列独占一个 `QThreadPool`（2 线程）**，不再挤
  `QThreadPool.globalInstance()`（那里还有缩略图/预取）；引擎模式下
  `prefetch_frame` 直接返回 —— 预解素材不会让合成变快，只会抢 CPU。

`Signal('qint64')`：`key` 是 `id(frame)`，64 位。写成 `Signal(int, …)` 时
PySide 按 32 位检查，**每一次 emit 都 OverflowError**（只在 stderr 留一行
RuntimeWarning），表现为"画面永远停在正在渲染" —— 最难查的那种。

## 23.3 渲染器本身：三处白读/白刷

| 位置 | 从前 | 现在 |
|---|---|---|
| 文本框底图 | 每拍 `Image.open` + `convert('RGBA')` 一次整屏宽底图 | `_image_cached`（`lru_cache`，键 `(路径, mtime)`，改图即失效） |
| 名条九宫格 | 每拍切一次 | `_nine_slice_cached`（键里带 `w/h/borders`） |
| 整屏底色 | `Image.new(整屏)` + `alpha_composite` | `_fill`：**不透明**色走 `paste`（逐像素直写），半透明仍混合 |

实测（真机 `seiran_project-2`，267 拍，`1920×1080`）：

```
改前  冷缓存 平均 24.3ms / 中位 21.9 / 最慢 164
改后  冷缓存 平均  9.8ms / 中位  7.8 / 最慢 112      ≈ 2.5×
热缓存 26.2 → 8.0ms
像素指纹（80 帧 × 2 个真实项目，sha256(整帧 RGBA)）：**逐字节一致**
```

⚠ `_fill` 第一版只认 Ren'Py 字符串（`_color` 内部 `str(value)`），
传元组会落到白色 fallback —— **黑场变白屏**，不报错、不崩，只是"看起来
这章就是白底"。判据直接钉住它：`test_fill_with_a_tuple_never_turns_black_into_white`。

## 23.4 现跑

```
探针 tools/probe_preview_speed.py（seiran_project-2，267 拍，offscreen 真窗口）
  首帧出图 497ms（含建窗）
  顺序翻 30 拍：黑屏 0 次；每拍等到出图 平均 38ms、最慢 145ms
  回翻 30 拍：缓存命中 30 次，合计 11ms
  播放采样 40×60ms：黑屏 0 次
```

新增判据 12 条：`test_stage_preview_speed.py` 8 条（颜色 2 / 缓存 2 /
不黑屏 1 / 提前合成 1 / 过期不算 1 / 播放不被卡 1）、
`test_toolbar_export_entry.py` 4 条。`test_ui_qt.py` 里那两条老判据按新语义改写
（"合成期间不许贴上一帧" → "`_rendered_pix` 只代表这一拍；画面靠
`_last_good_pix` 顶着，判据是**像素**不是标志位"）。

**已知边界**：缓存只留 32 帧（一张 896×504 ≈ 1.8MB），跳得远会重算；
"真实引擎截图校准"仍是另一条路（那要起 Ren'Py 本体），这里优化的是
`app.stage_render` 的离屏合成。

---

# 24. 删一拍要看得见"少了几行"；`role_of` 收口；无头也能验真实画面（2026-10-21）

用户拿真机跑了四个场景（`D:\tmp\p0\scope_*.py`），结论是这两个取舍都对，
并且把**真正该改的地方**指了出来：

> 决策 1 的真风险：不在作用域，在**粒度可见性**……
> breakend.0027 这一拍包含 7 行代码 + 1 句台词……数据是对的，问题是人看不见。

## 24.1 删除报告：从"谁：台词"到"产物里少 8 行"

一拍可能渲染成多行（真机 `sr_general/game/pomodorro`：45 拍里 **32 拍**多于
1 行，最多 17 行）。那些行都塞在表的「代码」/「原文」那一格里，Excel 单元格
只有一行高 —— 人看不见自己删了什么。

会话层新增两个方法（`app/session_table.py`）：

| 方法 | 作用 |
|---|---|
| `rendered_lines_of(beat)` | 这一拍在产物里是**哪些行**（`deepcopy` 后 `hydrate_from_dsl` + `render_beat_to_rpy`，与导出同一条路，**不改用户的拍**） |
| `delete_preview(idx, cascade=False)` | 试删一次（在**副本**文档上），返回 `{ok, count, lines, head, beats, blocked, can_cascade}` |

界面三处（`ui_qt/window_card.py` + `main_window.py`）：

1. **确认框**：多行拍会写「⚠ 这一拍在产物里是 **8 行**（不是 1 行）」+
   前两行原文 + 「…（其余 N 行）」；单行拍**不加噪音**；
2. **删完的日志/状态栏**：「已删除第 27 拍（jc：…），产物里少了 8 行
   （$ renpy.block_rollback() ／ hide screen hidden_timer …）」；
3. **面包屑**：选中一拍时写「（产物 N 行）」（一拍 ~0.3ms，不缓存）。

判据 `tests/test_delete_visibility.py`（8 条）：会话层算得对/无副作用/块头、
确认框真的写了、单行不加噪音、删完日志留着数字、面包屑显示/不显示。

## 24.2 `role_of` 收口：未登记不再是"空串 + 各处自己补"

用户的诊断（一字不差地照着改了）：

> `role_of` 用空串表示"未登记"，而"空串 = editor"这个决定散在三处兜底：
> `_can()` 里 `role or ROLE_EDITOR`、两处调用点 `role_of(...) or ROLE_EDITOR`、
> `ensure_user` 的 `role if role in ROLES else ROLE_EDITOR`。

现在：

```python
ROLE_UNREGISTERED = 'unregistered'      # 不在 ROLES 里 → 拿去 _can 就是只读
def role_of(conn, user_id, *, default): ...   # default 必填（漏了 TypeError）
def _can(role, action): return action in _ROLE_ACTIONS.get(str(role or ''), frozenset())
```

- "未登记算什么人"只能写在**调用点**（两处都是 `default=ROLE_EDITOR`，
  本地单人那条老口子）；
- `default` 本身认不出 → `viewer`（调用方写错也不放行）；
- `members()` 里 `role or ROLE_EDITOR` 也改了：库里 `role=''` 是**脏值**，
  清单显示"只读"，与拦截口径一致（从前显示"编辑"、实际按只读拦）。

判据：`role_of` 必填参数（TypeError）、未登记给 default、default 非法 → viewer、
空串角色在清单里显示只读。

## 24.3 「无 owner 可认领」：只用一次 + 留痕 + 说得出来

用户的前提摆得很清楚（工具**没有认证**）：`--user` 默认 `anonymous`
（`cli.py:879`）、`ensure_user` 默认 `editor`（`collab_store.py:283`）——
所以"陌生人第一次 push 就是 editor"不是漏洞，是**没有身份**这个前提的结果。
能做的只有三件：

1. **只可能一次**：认领后 `_has_owner` 恒真，那条路自动关闭；
2. **留痕**：`set_role` 的审计 detail 里带 `claimed: true`（谁、给谁、何时）；
3. **说出来**：回执带 `claimed=True`；CLI `role` 打印
   「⚠ 这是本库**第一次**认领所有者」；新增 `collab owner` 动作 + `status`
   里一行「所有者：**还没有**（任何成员可认领一次）」。

`owner_state(db) -> {'has_owner','owner','members'}` 是那个"事实"的查询接口。

## 24.4 无头 / CI：真实画面能自动验了

用户："「引擎排版」在无头环境出不了图，一直被'正在渲染…'占住。"

根因**不是渲染坏了**，是等法不对：正常播放走后台线程 + Qt 信号，结果要
事件循环转起来才回到 GUI 线程；"构造完立刻断言"的无头测试当然永远没有图。
同步那条路是 `StageCanvas.render_now(frame, idx)` —— 已在模块 docstring 里写明，
并用 `TestHeadlessRendering` 钉住：不转事件循环也能出图，`grab()` 后的像素
不是底板色，缺图 warnings 也拿得到。

## 24.5 记一笔（没改的）

`表信息` **只记来源文件、不记覆盖了哪些 label**。现在是"读源文件现算"
（实测四个场景都成立）。已在 `story/table_meta.py` 的 docstring 里写明：
别把它优化成"按记录集合拼产物"，源文件的 label 会被手工改；真要那样做，
前提是先对账"记录的集合 vs 文件里真实集合"，对不上必须**说出来**。

## 25. 卡片折叠状态跟项目走 + 按文件顺序排列（2026-10-22）

用户："继续完成折叠状态跟项目走"；"在一个 rpy 文件内定义多个 label……
让它识别到并自动排好块逻辑（现在流程图是按 label 之间的逻辑排的，
不是按 rpy 文件的逻辑）"。

### 25.1 折叠状态的存储口径

- 写**项目根** `storygraph.session.json` 的 `cards.folded`，键是
  **项目内相对路径**（`game/script/ch01.rpy`）—— 项目整目录搬走 / 换机器仍对得上。
  旧实现（偏好里按绝对路径）在搬目录、换机器时必然失效，这就是"不跟项目走"的根。
- **单一口径**：文件在项目根里 → 只认项目那份；不在 / 没项目 → 只认用户偏好。
  不再"两处各存一份"（那只会制造冲突源）。
- **`[]` ≠ 没有键**：`[]` = 折过又都展开了；没有键 = 从没折过（才允许去翻旧偏好
  做一次迁移）。写空表时**保留键**。
- **读-改-写 + 原子替换**：只替换那一个键，`layout` 一字不动；键没变就不写盘
  （翻页 / 展开不产生写盘）。
- **读不懂就不覆盖**：这份文件人也会手改，解析失败时写回"只有 cards 的新文件"
  等于删掉别人的内容 → 直接失败、退回偏好。
- 落盘守卫里必须登记（`tests/test_single_write_entry.py::WRITERS`）——
  它是本模块唯一写进项目目录的地方，全量跑出来才发现漏登记。

### 25.2 两种排法（互补，不互相覆盖）

| 排法 | 回答的问题 | 依据 |
|---|---|---|
| `layout_graph`（⤢ 自动布局） | 谁跳到谁（跨章节控制流） | `jump` / `call` / 菜单边 |
| `layout_by_file`（▤ 按文件顺序排列） | 这一章在源文件里是怎么写的 | `file_path` + `line_number` |

- 文件顺序：一个文件一列、列内按行号（不是导入顺序）、文件之间按路径、
  没有源文件的块排最后；每列垂直居中。
- **自动**只在"有新块还没摆过"时发生：全图同源一个 `.rpy` → 文件顺序；
  跨文件 / 有手画占位块 → 按跳转分层。只要每块都有各自坐标就**一律保留**
  （撤销 / 连线 / 改内容都不重排用户拖好的位置）。
- 排完必须**标脏**（坐标进布局指纹），否则关窗不提示保存。

### 25.3 顺延不画线（刻意）

label 结尾没有 `jump` / `return` 时 Ren'Py 会接着执行文件里的下一个 label。
图上的线只画**显式**跳转/调用 —— 要画顺延就得在模型里造假边：它会被
"重载删幽灵"当幽灵清掉，或被导出写成真的 `jump`。宁可不画，
写在「使用说明 → 剧情图」里（同列相邻、中间没有线，就是它）。

判据：`tests/test_feedback_p5_fold_project.py`（14 条）+
`tests/test_feedback_p5_file_layout.py`（12 条）。

## 26. `python:` 块 → `$` 行（2026-10-22，方向性的一刀）

用户："正确的方向不是'工具去适应 python 块'，是'工具帮用户把语句从 python
块里搬出来'"。

- **边界不变**：工具对块本身仍然"读进来、原样保留、逐字节写回"（K1/K6）；
  不解析、不改写、不拆调用/嵌套。这一条只是给用户一条**出口**。
- **只搬"`$` 就写得出来"的行**（`story/python_lift.analyze_block`，纯函数）：
  块体顶层 / 单行 / `name = …` 或 `name op= …` / 目标是一个普通变量名 /
  不以 `_` 开头；并且搬完不能把块拆出"只有注释的块"（`python:` 里只有注释
  是语法错 → 那种行会退回去陪注释）。
- **顺序不变**是全部理由：搬出来的行留在原来的行位置（块体前半 → `$` →
  块体后半），所以不做依赖分析；中间那条也能搬（拆成两个块，都有语句即可）。
- **落地三处一起改**：`Beat.code_blocks`（导出真相；`Beat.code` 一个字符
  不改）、`Node.statements`（拆成逐条、`source` 按块头 + 偏移推）、
  撤销历史（`LiftPythonStatementsCommand`：整份语句表 + 逐拍快照）。
  ⚠ 不能复用 `UpdateNodeOperationsCommand`：它按字符串重建语句，
  `source` / `condition` 会丢。
- **不自动搬**：不扫描全项目、不在导入/导出时顺手改 —— 每一行是"演出变量"
  还是"引擎状态"只有作者知道。
- 判据：`tests/test_python_lift.py`（23 条，含"同一拍两个块"与"两块文本相同
  要按顺序配对"两条被真实数据教育出来的用例）。


## 27. 自动保存 / 崩溃恢复（2026-10-23，体验报告 P0-2）

用户："拍的改动没有自动保存 —— 关窗会问，崩了就三拍没了。50 人 × 每天几小时，
这件事一定会发生。"

- **存哪儿**：`%LOCALAPPDATA%/StoryGraph/autosave/<项目键>/<章节键>.json`
  （`core/autosave.py`；`STORYGRAPH_AUTOSAVE_DIR` 换目录、`STORYGRAPH_NO_AUTOSAVE`
  一键关）。与 `session_store` / `ai_settings` / `crashlog` 同一处约定：
  **绝不写进项目仓库**（那份要进 git、要评审）。
- **存什么**：**整章**所有拍（`dataclasses.asdict`，含 `line_id` / `src_first`）
  + 章节顶层块（`file_blocks`，走 `write(extra=…)` 一条原子写）+ 源文件指纹
  （sha1）。284 拍 ≈ 270 KB / 3 ms（`tools/_probe_autosave.py`）。
  **不做差量**：差量要靠 `seq`/`line_id` 定位，而插删卡最先变的就是它们。
- **三条硬规矩**：写不进去只记台账（不拦编辑）；`*.tmp` + `os.replace` 原子替换；
  读不懂（版本更新 / 半截 JSON）→ `ok=False` 且**不删文件**。
- **恢复不是"已保存"**：`autosave_restore` **不** `take_edits_baseline()` ——
  恢复完 `has_unsaved_edits()` 仍为真、状态栏仍是「● 未导出」，
  必须点「⤓ 导出 .rpy」才落盘（`tests/test_autosave.py` 钉着）。
- **源文件变过 → 行号清零**：`stale_reason()` 比对快照里的 `source.hash` 与
  现在的文件；不一样就把 `src_first/src_last` 归零，交给 `patcher._fill_missing_src`
  按内容重新对位。拿旧行号硬写 = 把改动写到别人的行上（且会报"成功"）。
- **叫得准**：恢复后停在**第一处改动**那一拍（`_first_change_index`，
  difflib 对齐 `beat_edits_print` 序列）。
- **两次防假警报**：① 快照与磁盘逐拍相同（`_recovery_is_noop`：崩溃发生在导出
  成功之后 / 改完又撤销再崩）→ 不问，直接清；② 定时点发现"已经干净了"
  （撤销回原样 / 刚导出）→ 收掉快照。
- **谁写**：`window_recover.RecoverMixin` 30 秒一跳，**只在可见窗口 + 脏**时写；
  成功在状态栏显示钟点（`AppStatusBar.set_autosave`），**失败必须显示原因**
  （悄悄失败的自动保存比没有更坏）。

## 28. 协作锁接线（2026-10-23，体验报告 P0-3）

用户："locks 实测在'只有测试在用'清单里 → 能力悬空。"

- **规则一个字没改**：租约 / 过期可接手 / 只读成员不能锁 / 强制解锁只有所有者
  能做，全在 `collab.collab_store`（`lock` / `renew_lock` / `unlock` /
  `lock_range` / `locks`）。这一轮只是把入口接上。
- **四层**：`session_collab.collab_lock/_unlock/_lock_range/_locks`（薄壳）
  → `ui_qt/dialog_locks.py`（列表 + 锁当前那一拍 + 锁整章 + 续租 + 解锁 +
  强制解锁）→ `_more_tools_menu()` 的「🔒 协作锁」+ `show_collab_locks()`。
- `collab_lock_summaries`（`collab_store.lock_summaries`）给列表带**台词摘要**：
  只显示 `ch01.0007` 人认不出来；`locks()` 的形状不动（CLI / 导出还在用）。
- ⚠ **读锁列表不许建库**：`collab_store.connect()` 会建目录 + 建库 + 跑 DDL，
  所以 `session.collab_locks` 先判 `is_file()` —— 否则手填错一个路径，工具就
  在那儿悄悄造一个空协作库，界面显示"没人锁"。
- ⚠ 两个实现陷阱（都有用例）：`if self._session:` 遇到 `EditorSession.__len__`
  （没载入章节 = 空 = falsy）；`int(getattr(self, '_loaded_idx', -1) or -1)`
  把**第 0 拍**当成"没载入"。

## 29. 资源改名的两个补丁（2026-10-23）

D3'（plan → apply(confirm) → rollback）**早已接进 Qt**（`ui_qt/dialog_asset_rename.py`
+ `window_assets.open_asset_rename_dialog`，2026-10-19 的 `53ecc24`）。这一轮补两个洞：

- **顺序闸**：这一章还有未导出的改动时**拒绝改名**（`_block_if_unsaved`）。
  反过来的后果是"先改名、再导出 → 会话把旧名字写回文件"，且导出报"成功"。
  只在"这次改名确实会改到手上这一章"时拦（`_touches_current_chapter`）。
- **写盘后重读**：`on_applied` → `window_assets._after_asset_rename` →
  若当前章在 `modified` 里就 `load_script(…, goto_card=False)`。
  不重读的话，卡片页捧着改名前的拍，下次导出同样把旧名字写回去。

## 30. 三栏骨架：章节导航 / 剧本行 / 属性表（2026-10-23，照参考界面）

用户给了一张参考截图："我看类似于这样的 UI 布局就非常不错！参考学习调整 UI 方面的内容！"
那一版骨架是"顶部模式标签 / 左侧章节树+搜索 / 中间带行号的正文 / 右栏检查器"。
我们照**骨架**改，编辑语义一行没动。

- **顶部模式条**（`window_nav._build_mode_bar`）：`QTabBar`（🗺 剧情结构 / ✎ 卡片编辑）
  放进一条单独的工具栏。与 `_set_page` **双向**同步（`blockSignals` 防环；
  `_set_page` 是唯一改页号的地方）。为什么不给每页再挂快捷键：`QAction` 的快捷键
  不受"当前在哪页"约束（见 `_build_toolbars.act` 的注释）。
- **左侧章节导航**（`ui_qt/navigator.py`，`QDockWidget`，`objectName=chapterNavDock`）：
  数据来自 `app/chapter_index.build_index()`（项目 → 文件 → `label` + 行号）。
  **不属于任何一页** —— 切页不丢，这正是它最大的用处（在卡片页也想跳去别的章）。
  - 双击 / 回车才打开（单击只选中）：它紧贴拍列表，手一滑就换章 = 重读磁盘；
  - `●` = 这个 label 已在剧情图里（`_sync_nav_marks` 由 `_set_graph` 唯一同步点驱动）；
  - 搜索按"文件路径 / 文件名 / label"过滤，大小写不敏感；搜文件命中时带**全部** label；
  - 折叠过的文件刷新后保持折着（`_collapsed`），搜索时一律展开。
- **剧本行**（`ui_qt/shot_list.py`，`QStyledItemDelegate`）：**只换画法不换控件** ——
  模型、`Qt.UserRole` 里的拍号、`item.text()`、`setCurrentRow` 全原样
  （几十条既有用例按这些判过）。行号 `003` + 类型牌（对白/旁白/分支/选项/语句/调用/跳转）
  + 加粗的说话人。类型判据与 `window_card._row_text` 同源。
- **右栏属性表**（`ui_qt/inspector.py`，`PropertyStrip`）：文件 / label / 第几拍 /
  类型 / 源位置（`.rpy 第 217–219 行`）/ 说话人。数据由 `window_nav._sync_inspector`
  从会话取，`_show_index` 里刷新。空值那一行不显示；长值中略 + tooltip 全文 + 可选中复制。
- ⚠ **同一文件里换 label = 跳拍，不重读**（`open_chapter_at` 先 `_same_file`）。
  重读会把没导出的改动冲掉。
- ⚠ **换章前先抄快照**（`autosave_now()`）：不弹窗问"要不要保存"（每次翻章都问，
  三天就会被闭眼点"否"），但丢掉的那一份必须能捡回来 —— 快照按章存，
  「🧰 更多工具 → 🕘 上次没导出的改动…」里还在。
- **布局记忆多了 `dock_state`**（`QMainWindow.saveState()` 的 base64）：
  存 dock 的位置 / 宽度 / 浮出 / 关掉，只当字符串存、不解释格式
  （认不出时 `restoreState` 返回 False，就当没记）。`saveState` 要求工具栏有
  `objectName`（`pageBarGraph` / `pageBarCard` / `modeBar`），否则 Qt 直接不存。
- ⚠ 判 dock 开没开用 `isHidden()`，**不是** `isVisible()`：恢复布局时窗口通常还没
  `show()`，`isVisible()` 一律 False → 工具条上那个勾会被莫名去掉。

## 31. 拍列表的"行号 == 拍号"还成立吗（2026-10-23）

成立，但**只在列表里**：`_beat_rows[拍号] = 行号` 仍由 `_reload_list` 维护，
分组标题行（`Qt.UserRole == -1`）不占拍号。delegate 画的 `003` 是 `拍号 + 1`，
不是行号 —— 两者在"有 label 分组标题"时会差几行，别混着用。

## 32. 状态流（2026-10-23，体验报告 P1-5）

`collab_store.set_status` 早就有（draft / in_review / approved，含权限与审计），
界面一直没有入口。这一轮接上，**形状是按章**：

- `collab_store.set_status_range(db, label, status, actor)`：
  只动这一章、只动 `deleted=0`；已经是该状态的行**不算 changed**；
  **不碰锁**（锁管内容，状态是审阅元数据）；权限与 `set_status` **同一条**
  （`_can(role, 'approve')`，别另写）；一条 `status_range` 审计（章 / 状态 /
  改了几行 / 共几行）；`STATUS_LABELS` 是唯一的中文名来源。
- `session_collab.collab_status_overview(db)`：`status_summary` 的薄壳。
  ⚠ **库不存在就直接说，不去碰盘** —— `connect()` 会建库 + 跑 DDL
  （与 `collab_locks` 同一条）。
- 界面不新开窗：放进已有的「🔒 协作锁 / 状态」对话框（同一协作库、同一用户名）。
  上半锁表，下半**状态表**（章 / 行数 / 草稿 / 待审 / 定稿 / 锁着）+
  三个按钮；没选章时作用于**当前这一章**（打开时把当前 label 传进去并自动选中）。
- 判定：`tests/test_collab_status_ui.py`（20 条）。

## 33. 保真证据链：什么叫"逐字节相同"（2026-10-23）

**一句话**：`identical` 才算数；`layout` 只证明"内容没丢"，不证明"没碰用户的字节"。

判定（唯一实现 `tools/fidelity_report.py::classify`，用例与工具共用）：

| 判定 | 定义 | 什么时候是问题 |
|---|---|---|
| `identical` | 剥掉生成标记（+紧跟的空行）后**字节相同** | —— |
| `layout` | 只剩**空白**差异：空行 / 行尾空格 / 换行符 / 末尾换行 | 补丁没跑起来时出现 → 查"为什么退回全量" |
| `content` | 行级内容不同（丢 / 多 / 改，带行号） | **真 bug**，必须红 |

- `layout` 的归一**只去行尾空白与空行**；不去注释、不去大小写 —— 那已经不是排版。
  别拿"反正只是排版"当挡箭牌：补丁退回全量时，文件头连续注释之间会被插空行，
  用户的字节确实变了（只是内容没丢）。
- 退出码：`0` = 全 identical（`--allow-layout` 时 layout 也算过）/ `1` = 有 content
  或 error / `2` = 用法错。CI 里用 `--allow-layout` 是为了不让**已知的排版差异**
  把 job 判红；`tests/test_patch_menu_condition.py` 那个 xfail（条件选项改文案）
  仍然会走全量 —— 它由用例钉着，不靠 CI 的退出码兜。

覆盖范围的边界（写死，别扩读）：

- 只覆盖 **`--source` 那条写回路径**。不给源 / 目标是新路径 → 走全量重生成，
  **不承诺逐字节**，只承诺"顶层语句不丢"（`opaque_loss`）。
- 逐字节相同**不证明表是对的** —— 铁律 9：语义永远由人标注。
- 真机字节的审计**只在本机**（55 个文件、体积与路径依赖）；仓库里的样本是
  **结构**，不是真机快照。两把尺，别混：CI 绿 ≠ 真机绿。

样本契约（`tests/fixtures/fidelity/`）：

- 每份文件头两行：`# 它测什么：…` + `# 来源：…`（结构取自哪类真机文件）。
- 台词一律 `台词A/B/C…` 占位；**仓库里不许出现真机台词**（`git grep` 不到）。
- 加样本**必须同时加自检正则**（`tests/test_fidelity_structure.py::CASES`）：
  样本被谁顺手简化成简单对白时，自检先红 —— 否则用例会一直绿着骗人。
- 已知缺口**如实钉住**当前行为，并写清"修好之后哪两条会红、该怎么改"。
  目前**只剩一条**：改一句**条件选项的文案**（配对键会变 → 退回全量），
  在 `tests/test_patch_menu_condition.py` 里以 `xfail(strict=True)` 钉着。
  （"条件选项本身"已于 2026-10-23 修好：`patcher._menu_condition_view` /
  `_menu_shaped_lines` 把单拍渲染的行摆回源文件形状 —— 见
  [`审计_保真证据链_2026-10-23.md`](审计_保真证据链_2026-10-23.md) §五-A。）

⚠ **比较两份文本之前先统一载体口径**：读源要走 `source_style.read_text`
（剥 BOM + 换行归一），否则带 BOM 的文件第一行永远"对不上" ——
`probe_opaque_fidelity` 就吃过这个假阳性（5 个文件各误报丢 1 处，2026-10-23 修）。

## 34. 窗口宽度与"放不下"（2026-10-23）

用户报"界面太宽、全屏都看不全、文字跑到界面外"之后定的三条口径：

- **窗口尺寸**：任何时候都不得大于屏幕可用区 —— `window_state.fit_to_available`
  （四周留 48px），`__init__` 与**记忆恢复**两处都要过它；恢复位置还要夹回屏内。
  `QMainWindow.sizeHint()` 会把工具栏整宽算进去（实测 2720），**不能**拿它当开窗尺寸。
- **单行文字**：可能变长的标签一律用 `ui_qt.elide.ElidedLabel`（右略/中略），
  它把 `minimumSizeHint()` 压到 24px。**不许用普通 `QLabel` 装长句** ——
  它的最小宽 = 整句宽（实测 468px），会把同一行的按钮挤出窗口。
- **工具栏（已知限制，2026-10-23）**：动作放不下时 Qt 收进 `qt_toolbar_ext_button`，
  它在本项目 QSS 下是**看不见的 12px 空白**。**不要试图改它** —— 实测三条路都不通：
  加宽 → 它伸出工具栏（Qt 钉住 x，不随宽度移动）；手工 `setGeometry()` 摆回 →
  与布局互相触发 **resize ↔ 摆放无限循环**（3 秒事件循环烧 4.4 秒 CPU）；
  写文字 / 塞图标 → 都不画。**正确做法（下一轮）**：把低频动作移进显式的
  「🛠 更多」菜单按钮，不依赖扩展按钮；那要同步改 `test_custom_statement_entry` /
  `test_qt_signal_wiring` / `test_toolbar_export_entry` 三处"工具栏动作清单"判据。

- **2026-10-30 ⑤（UI 宽窄可变）**：默认窗口 **1600×950 → 1360×900**（当晚又降到 **1200×800**，见 ⑥）；窗口最小宽
  **1142 → 738**；卡片最小宽 **380 → 296**（右栏硬下限 `CARD_COLUMN_MIN_W=340`
  ⇒ **右栏不会出现横向滚动条**，逐拍压到 340 断言 hbar 不可见）；立绘网格列数
  随宽度自适应 **1~4 列**；判据 `tests/test_ui_width.py`（**30 条**：原有 22 − 4 条菜单判据 + 6 条按钮回条 + 6 条记忆迁移 = 30）。

- **2026-10-30 ⑥（用户否掉「⋯ 结构」菜单 + 默认窗口再降到 1200×800）**：5 个结构按钮**回到动作条上**
  （顺序：插入卡片 / 插到前面 / 插入控制流卡 / 插入选择点 / ↩ 撤销结构操作 /
  删除这张卡片）。与 ⑤ 的"窗口可缩窄"同时成立靠 `CardActionRow`（`FlowRow`
  子类，`main_window.py` 模块级）：
  · `sizeHint()` = 一行全排开（宽窗口下 = 从前的样子，一条都不折）；
  · `minimumSizeHint()` = 最宽那一颗（≈113px）⇒ 窗口最小宽仍是 **738**；
  · 窄到放不下时 `FlowLayout` **折行**，高度经 `hasHeightForWidth` 传到
    动作条 → 页面根布局。**一颗都不藏、也不裁到窗口外**（判据量的是每颗按钮
    矩形是否落在动作条内，不看具体像素）。
  · 已删除：`_structural_btn` / `_structural_menu` / `_structural_actions` /
    `_structural_buttons_host` / 模块级 `sync_structural_menu()`。**不要再加回来**。

- **2026-10-30 ⑥（老记忆迁移，`window_state.narrow_layout`）**：布局记忆里新增
  `layout_version`（当前 `2`，`current_layout()` 写入）。读记忆时版本 **< 2**
  就压一次：窗口宽/高 ≤ `DEFAULT_WINDOW_SIZE`（=**1200×800**）；卡片页右栏占比 > 60% → 回到
  `CARD_SPLIT_DEFAULT`。**为什么**：真机 `session.json` 存的是
  `geometry=[1440,830]`、`split_card=[402,813]`（卡片 67%）—— 记忆优先于默认值，
  不迁移的话"默认改窄"对老用户**完全不可见**（用户原话："并没有做到缩小"）。
  迁移只做一次；之后用户拖多大就记多大。

- **2026-10-30 ⑦（右栏：横条没了 / 不出框 / 卡片栏真的窄了）**：用户第 7 条
  「右窗口不得有横向滚动条！所有图案不得超出右边框！」+「都说了要变窄一些」。
  根因**不在**滚动区，在**卡片自己**：`QComboBox.minimumSizeHint()` 默认按
  **最长条目**算宽（真机背景候选 40 多字 → 单个下拉 **1025px**，三个资产下拉
  把卡片地板顶到 **1077px**）。四条改动：
  · `ui_qt/wheel_guard.py::WheelSafeComboBox` 起手就
    `setSizeAdjustPolicy(AdjustToMinimumContentsLengthWithIcon)` +
    `setMinimumContentsLength(4)`（**最小宽按 4 个字符算，实际宽度仍由布局拉伸**；
    2 个字符行「音乐 + 语音」实测 208px）。**不许改回默认策略**。
  · 卡片滚动区 `setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)`（结构性兜底，
    与左边剧本列表同一条纪律）；真正的保证是"卡片最小宽 ≤ 右栏"，判据见下。
  · 未保存提示 9 字 →「● 未保存」：它和两个按钮同一行，实测 117+89+99+间距 = **321**
    ⇒ 卡片 345（右栏下限 340 放不下）。完整说明进 tooltip。
  · `window_state.LAYOUT_VERSION` **2 → 3**，`LEGACY_CARD_SHARE` **0.60 → 0.45**：
    用户记忆里是 `[583,584]`（对半 50%），卡在旧门槛下面 ⇒ 上一轮**一次都没迁移**，
    所以"还是一模一样"。现在 ≥45% 的旧摆法一律压回 `CARD_SPLIT_DEFAULT`；
    ≤45% 的窄比例照旧按比例还原（`_scale_sizes` 不砍右栏）。
  实测（真机 `sr_general`，285 拍）：窗口 1200 → 右栏 **386**（原 500）、
  卡片 374 = 视口宽、卡片最小宽 **296**、hbar 不可见、越界控件 **0**；
  1440 → 右栏 481 / 卡片 469，同样不越界。判据 `tests/test_ui_width.py`
  （30 → **38 条**：长图名不许顶宽 / 逐拍量右边界 / hbar 策略 / 对半记忆要被压）。

## 35. 控制流卡（2026-10-23 修正）

- **目标候选**必须来自 `_flow_target_labels()`：本文件 → 图上节点 → **项目目录**
  （`app.chapter_index`）。不许只取 `session.label_names()` —— 那只有**当前文件**，
  真机 `chapter0_0.rpy` 里只有 1 项，下拉等于没有。
- **插入对话框**：目标默认空（不预选，否则容易插一张 `call <这一章自己>`）；
  `jump` 禁用参数与 `from`；参数/`from` 用**渲染 → 再解析**校验
  （`render_flow_line` 只拼字符串，`call side(a` 它照拼）。
  `session.insert_flow_beat` 有同一道闸门 —— 别的调用方绕不过去。
- **控制流卡没有台词与演出**：`set_flow(info)` 收起 `_dialogue_box` 与舞台三件套；
  回到普通卡时按这一拍自己的内容恢复（舞台显隐仍只归 `_apply_stage_visibility`，
  不许另写一套）。
- 判据：`tests/test_ui_fit_and_flow.py`（21 条）。

## 36. 行身份（LineId）—— 规则在单独一份（2026-10-23）

规则表、不变量 I1–I5、反例与边界全在 [`契约_行身份.md`](契约_行身份.md)；
这里只留**改代码时必须记住**的四条：

- 发号**唯一实现** `storygraph.story.line_identity.allocate`（max+1、跳过已用）。
  **不许**再写 `f'{label}.{seq:04d}'` —— 会话层按位置重算就是 2026-10-23 之前的状态
  （插一张卡，后面每一行都换身份）。
- `EditorSession._renumber` 只重排 `seq`，**只给没有身份的行发号**；
  不许重写别人的 ID（R5/R6/R7 用例钉着）。
- `beat_fingerprint` 才是**内容哈希**；`line_id` 与内容无关
  （改台词 / 改条件 / 改舞台都不换 —— 这是与 Ren'Py 官方 translation 的根本区别）。
- 重新摊表覆盖已有表时走 `script_table._reuse_existing_ids`（两轮匹配、不猜）；
  判据 `tests/test_line_identity_contract.py`（用例名就是规则）。

## 37. 图的编辑边界（2026-10-23，用户纠正后写）

- 图**是** label 级结构编辑器：增删节点、加/删/改连线（jump / call / menu，
  带条件与菜单项文字）、移动、自动布局、撤销/重做
  （`window_nodes.add_node/add_edge` + `graph/commands.py`）。
  **它不是"只读诊断视图"** —— 上一轮这样描述是错的
  （见 [`调查_图编辑器的定位与回写_2026-10-23.md`](调查_图编辑器的定位与回写_2026-10-23.md)）。
- 两种边必须分清：`origin='source'` 是**从拍派生**的（`story.flow.flow_edges`
  是唯一真源）；`origin='manual'` 是**草图**。
  ⚠ **2026-10-24（P0）之后，"新建接线 = 只画草图"已作废**：接线默认写进剧本
  （章末或选中的第 N 拍），只有写不进 / 条件分支时才存草图；
  草图必须标「草图」，右键可「写入剧本…」转正（按它记下的位置写）。
  日志与状态栏要说出这一次到底是"写"还是"草图"（别删这句话）。
- 写回 `.rpy` 的唯一通道仍然是 **`Beat.raw` → 受控补丁**：接线走
  `session.insert_flow_beat` 插一张 flow 拍（`source` 边按 `line_id` 找到那一拍；
  `manual` 边转正时按 `Edge.position_line_id` 找锚点）—— 图**不许直接改文件**。
- 图不编辑 `menu` / `if` 的**块体**（台词与演出归卡片），也不做"一个块 = 一拍"的画布。









---

## §38 新增一拍的落点：`nest_path` 说了算（2026-10-23）

- **判据**：新拍在模型里的落点是 `nest_path`（`menu:1>A` = 第一个菜单的第一个选项体）。
  **写回必须与它一致** —— 不一致的产物可以语法合法、语义已经改了（`call` 从分支里
  跑到 menu 之外，`patch_export` 还报 `ok=True`）。
- **两种"块头锚点"要分开**（2026-10-23 实测，真机形状的 `menu`）：
  - **选项体的兄弟卡**（`nest_path` 为空）→ 退到**最外层包围块之后**、与块头同级
    （`_insert_anchor`，2026-10-05 为解决"选项下面紧跟一句台词 → 语法坏"而设）；
  - **放进选项体的卡**（`nest_path` 含 `>`）→ **紧贴块头插、缩进深一级**
    （`_insert_points_for` 标出这些锚点 → `_apply_sequence_edits(..., into_body=…)`）。
- `menu:1`（菜单层、还不属于任何选项）**不在此列**：这种状态下整份渲染自己就会写出
  `# TODO` + 非法行，补丁保持"退到块外"，至少产物能读。
- 守卫：`tests/test_patch_option_body_insert.py`（先红：`call roof` 跑到下一个选项之后）；
  老用例 `TestInsertIntoBlockStaysStructurallyValid`（退到块外）必须同时绿 —— 两类不能混。

## §39 拍位置与 `call from`：可选，但只能选真的能写的位置（2026-10-24，P1）

- `Edge.position` 有三个值：`chapter_end`（默认）/ `beat` / `control_end`；
  `beat` 必须同时有 `position_line_id`（`Beat.line_id`）。源边的真实位置仍由
  `line_id` 派生，`position` 是"打算写在哪"的声明（草图转正靠它）。
  `control_end` 只跟 `origin='control'` 一起出现（见 §40）。
- **唯一判据**是 `window_nodes._beat_position_plan`：
  - `return` → 插在它**前面**（`after=False`）；
  - 块体里的拍（`nest_path` 非空）→ 拒绝；
  - **有真身拍的块头**（`menu`/`loop`/`camera`/选项头，`session.block_head_line_ids()`）
    → 拒绝（`after` 会掉进块体）；
  - 已有 `jump`/`call` 的拍 → 拒绝（后面接不上）；
  - 其余顶层拍 → 接在它后面。
  下拉框与 `plan_wire_landing(position='beat', …)` 共用这一份，**不许**出现
  "下拉框能选、点了写不进"。
- 章末已有 `jump`/`menu` 时，**选一个前面的拍**仍然能写 —— 这是 P1 的意义：
  歧义只堵"章末"这一个落点，不堵整条线。
- `call X from Y` 的 `from`：可选，只对 `call` 有效（`jump` 没有）。自动命名
  `_call_<目标>_<n>` 会扫全项目的显式 `from` **以及每个目标被 call 了几次**
  （Ren'Py 会给裸 call 自动生成同形名字），避开两类占用。**2026-10-24 晚起，
  接线/编辑窗口不再放这一格**（用户要求恢复旧版面）；已有源边的 `from` 仍会
  被读取/保留，底层自动命名也还在。
- `AddEdgeCommand` / 描边 / 版式都要带 `position`、`position_line_id`、`from_clause`；
  线外标签只放条件/菜单项（§42），草图只多一句「草图」，位置在
  tooltip 和「编辑连线」窗口里。
- 守卫：`tests/test_edge_position_p1.py`（27 条：计划、下拉框、对话框、草图转正、
  from 命名/校验、图上文字）；P0 的 `test_edge_position_p0.py` 必须同时绿。

## §40 章末写编排层、草图改落点、跨章删源边（2026-10-24，P2）

### 40.1 有独立控制流文件时，章末**不许**写章节剧本

判据不是"更优雅"，是**不写会跑两遍**：

```
X_control: call X → X 里 jump Y → Y return → 回到 X_control
           → X_control 又 jump Y_control → Y 跑第二遍
```

- `plan_wire_landing(position='chapter_end')` 先算 `story.control_route`；
  有 `X_control` / `Y_control` 就返回 `kind='control'`，落点
  `position='control_end'`，**章节源文件一个字节不改**。
- 找不到源/目标 `_control`、或最后一条是 `menu`/`if`/动态跳转 → 返回
  `control_blocked`，**不退回源文件**（那正是会跑两遍的写法）。
- `jump`：目标 `Y_control`；最后是 `return` → 替换成 `jump`（不留死代码）；
  最后是另一条 `jump` → **改接**，但这是语义变化，不能替人拍板；
  必须进接线窗口让人看见"原来去 C，现在改成去 B"。
- `call`：目标 `Y`（章节本身），插在最后一条路由**之前**；子过程返回后继续原路。
  导出器对 `origin='control'` 的 call 写 `call Y`，并在没有后续路由时补 `return`
  （否则会掉进下一个 label）。
- 只打一个补丁：`story.control_route` 只替换/插入**一行**，保留 BOM/CRLF/别的
  label；写盘前核对锚点行，文件被改过就拒绝（`Ctrl+Z` 也不覆盖新改动）。
- 图上的边：`origin='control'`、`position='control_end'`。它不是草图（不写
  "草图"，见 §42）；删它只删 `X_control` 那一行，必要时补 `return`，
  同样可撤销。

### 40.2 草图改落点

- 右键草图 →「改落点…」：章末 / 第 N 拍；不能接的拍置灰，当前拍不在了要明说。
- 只改 `Edge.position/position_line_id`，**不碰剧本**；进图级撤销栈
  （`SetEdgePositionCommand`）。要写成剧本仍走「写入剧本…」。

### 40.3 删跨章源边

- 当前编辑器开着 A、图的源边来自 B 时，不再要求先切到 B：
  `app.source_line_delete` 用临时会话读 B，按 `line_id` 找到拍，用
  `src_first/src_last` 只删物理行；不导出、不重生成、不加生成标记。
- 找不到行身份（B 在导入后改过）→ 拒绝，**不按目标名猜一行**；写盘前再核对
  全文，变了就拒写。撤销把旧全文逐字节写回并把图边放回。
- 同一文件里找不到行身份仍然拒绝（那不是"另一章"，是身份过期）。

### 40.4 守卫

`tests/test_control_route.py`（33 条：计划/落盘/BOM+CRLF/撤销/删除/图边/版式往返/
导出重建 call）、`tests/test_edge_position_p2.py`（11 条：对话框/命令/不改剧本）、
`tests/test_cross_chapter_edge_delete.py`（7 条：计划/删除/取消/过期/逐字节撤销）。
P0/P1 的 `test_edge_position_p0.py`、`test_edge_position_p1.py`、
`test_wire_landing.py` 必须同时绿。

## §41 补目标章的 `_control` 包装（2026-10-24，P2.5）

> ⚠ **41.1 第 2 条与 41.3 第 3 条的"按钮文字"已被 §66 取代**（2026-10-27）：
> 源章缺 `X_control` 时**也补**（"到不了"不是"写不进"），确定键统一是
> 「创建」；补包装改由 `apply_init_chain` 一次补两头的。下面保留原文只为
> 记录当时的判据演变。

### 41.1 只有"有人进来"的包装才可以补

- **目标章**缺 `B_control`：正在写的 `jump B_control` 就是第一个入口 →
  可以补；接线窗口显式显示「补包装并接线」，不是静默可写。
- **源章**缺 `X_control`：它现在被 `jump X` / `call X` 直接进入，补出来
  没人跳进来，出口是死代码 → `ok=False`，并列出要先接管的入口。
- **项目里一个 `*_control` 都没有**：不新建控制流文件（没有挂载点，
  同上）；返回"先导出一次控制流 / 手写一个入口"。

### 41.2 补的形状（必须是这个）

```renpy
label B_control:
    call B
    return
```

`jump B_control` → `call B`（B 的 `return` 回到这里）→ `return`，与原来
`jump B` 落到同一个调用帧。**不许**写成 `label B_control: jump B`：下一步
`plan_control_route` 会把最后一条 `jump` 当"已有出口"改接掉，B 就不再执行。

- 追加前核对全文（不是锚点行）；BOM / CRLF 走 `source_style` 还原。
- 目标章 label 带参数、找不到声明、label 名不是 `_control` 源 → 拒绝，不猜。

### 41.3 撤销与失败

- `ControlRouteCommand(init_plan=…)`：先补包装 → **重算**路由（行号按新文件）
  → 写路由；`old_text` 存**补之前**的全文，一次 Ctrl+Z 全部回滚。
- 补完包装但路由算不出来 / 写失败 → 回滚包装（只回滚自己刚写的那一份，
  文件被别人改过就不覆盖），不留半个改动。
- 对话框：`control_init` 的确定键是「补包装并接线」；填了条件/菜单项仍只能
  草图；落点栏要显示补在哪个文件、哪两行、以及"另有 N 处直接 jump 不经过
  这个包装"。

### 41.4 守卫

`tests/test_control_init.py`（17 条：目标/已有/无层/缺章/带参数/源章直入/
断掉的 `jump X_control`/直接入口提醒、BOM+CRLF、全文过期、一键补+一次撤销、
重做、对话框文案、无 init 回归）。`test_control_route.py`、`test_edge_position_p0/p1.py`、
`test_wire_landing.py` 必须同时绿。

## §42 「编辑连线」窗口与线外信息（2026-10-24 晚）

### 42.1 双击一条线 = 编辑这条线；新接线一律先弹窗

- 新接线（Shift + 拖 / 右键「添加连线」）**每次都弹**「添加连线」：
  从 / 到 / 类型 / 插入位置 / 条件 / 菜单项，左下角「草稿」勾选。
  **不许**为了少一次弹窗省掉它——弹窗不必多，但接线这一步是必要的
  （用户 2026-10-24 晚明确纠正：线画上了，人却不知道它插在哪）。
- `GraphCanvas.edge_edit_requested` → `NodeEditMixin.edit_edge` →
  `dialogs.EditEdgeDialog`。保存分三种（§42.2）：草图走
  `UpdateEdgeCommand`（只改图），源边改写剧本那一行，编排线改写
  `X_control` 那一行。`from` 这一格已从窗口移除（§39），已有源边的值仍保留。
- **不是**"跳到那一拍"：那仍是右键菜单「定位到这一拍」（`_open_edge_in_card`）。
- 线/线的标签双击都必须命中这条：`EdgeItem.mouseDoubleClickEvent` 发
  `edge_edit_requested`；`GraphCanvas.mouseDoubleClickEvent` 在块与空白之间
  再查一次 `_edge_at(pos)`，否则双击标签会落进「添加新章节」。
- 编排线（`origin='control'`）可以在窗口里直接改**类型 / 到**：保存改写
  `X_control` 那一行（立刻落盘、一次 Ctrl+Z 逐字节写回）；「打开编排层文件」
  仍在，留给要改条件 / 分支的人。

### 42.2 窗口里必须有的字段

从 / 到、类型（jump/call）、**插入位置**（章末 / 第 N 拍）、条件、
菜单项。版面就是旧版「添加连线」那三样（类型、菜单文本、条件）+ 插入位置，
左下角加一个默认不勾的「草稿」；不是另起一张表单。

- 从（起点章）只读：改起点等于删一条再加一条。
- 到：草图只读；源边 / 编排线可以改（保存会改写那一行）。

- 插入位置默认「章末」；下拉框精确到第 N 拍（`edge_position_choices`），
  不能接的拍置灰。
- 勾「草稿」：只画线（虚线 + 「草图」），**不写 `.rpy`**；位置仍记在
  `position/position_line_id`，以后右键「写入剧本…」可以转正。默认不勾，
  不勾就按插入位置直接写。
- 草图的位置在窗口里的下拉框直接改（复用 `edge_position_choices` 的判据，
  不能接的置灰；当前拍不在列表里补一条只读项，不许静默改成章末）；
  源边的落点由 `line_id` 决定，只显示、不可改（改它是删行/插行）。
- **源边 / 编排线：类型和「到」可以直接改，保存真的改那一行**（2026-10-24
  用户追问"我希望我能够就这样直接编辑它"）：

  | 来源 | 改类型 / 到 | 写到哪里 | 什么时候落盘 |
  |---|---|---|---|
  | 源边 `origin='source'` | 改写剧本里那一行（`jump B` ↔ `call B`） | 本章的拍（内存） | 导出 `.rpy` 时 |
  | 编排线 `origin='control'` | 改写 `X_control` 那一行 | 控制流文件 | 立刻 |

  `jump → call` 且它原来是这一段最后一条时，自动补一行 `return`（call 会
  回来，没有下一句会掉进下一个 label）；`call → jump` 后面还有路由时**拒绝**
  （后面的路由会变成永远不执行的死代码）。新目标缺 `_control` 包装时先补
  包装、再改这一行，一次撤销全回去。
- **源边的「插入位置」也能改**（2026-10-24 夜）：下拉框列本章顶层拍，选中
  另一拍 = 把那一行挪过去（`session.move_beat`，块结构由树维护，撤销走结构
  撤回栈）。跨 label / 跨章不在这里做 —— 那会改 `from`，是另一条边。
- 条件 / 菜单项：**编排线可编辑**（§43：按 `# storygraph-edge:<id>` 标记段
  换，或把普通路由换成条件分支）；源边那两样挂在它所在的 `if` / `menu` 上，
  不是这一行能改的 —— 窗口里必须写清原因，不许给一个"填了也改不到剧本"的
  格子。
- 起草图 / 源边 / 编排线都要能把**起点章**载进来：改源边时按需载入（换章
  前先抄未导出快照），不要求用户先手动打开那一章。

### 42.3 线外只留条件 / 菜单项

`edge_label_text`：

```
「菜单项」 / if 条件 / 草图 ·「菜单项」
```

位置不挂在线外，只在 tooltip 与编辑窗口里；**单击边只选中，不弹窗**，
右键菜单也不把"第几拍"顶在开头。线型：实线 `jump`、虚线 `call`、
**虚线 + 「草图」** = 草图（不再用点划线）。

### 42.4 「插入位置」属于**起点章**，不属于"现在打开的那一章"

用户 2026-10-24 抓到的 bug：开着 `chapter0_0` 时，从 `chapter0_0` 拖线能选
第 N 拍；从 `chapter1_2` 拖线只剩「章末」。同一张窗口两种表现，不是功能。

- `edge_position_choices` / `plan_wire_landing` 取拍的顺序：**会话里就是这
  一章** → 用会话的拍（尊重没导出的改动）；否则**只读解析**起点章的 `.rpy`
  （`_source_beats`，按 mtime/size 缓存，不切章、不弹恢复问句）。
- 解析出来的计划带 `needs_load=<源文件>`；真正写回时 `_apply_wire` 先
  `_ensure_chapter_loaded`（换章前 `autosave_now` 抄一份未导出快照，
  `goto_card=False` 不跳页），再按同一个 `line_id` 重算一次落点。
- 不许再出现"因为当前打开的是别的章，所以这个下拉框只剩章末"。

### 42.5 守卫

`tests/test_edge_edit_dialog.py`（字段回显 / 源边只改自己那一行 / 编排线改
类型和目标 / 草图位置下拉 / 标签双击不落新建 / 单击只选中 / 右键菜单不带
位置 / 窗口层命令）；
`tests/test_edge_edit_writeback.py`（编排线改那一行 + 撤销 / jump→call 补
return / call→jump 拒绝死代码 / 源边按需载入并改写 / 起点章没打开也能列拍
并写进去）；
`test_wire_landing.py`（草稿勾选默认关闭、勾了不写、位置仍记到拍、只导入
也能列出第 1 拍）；`test_script_entry.py`（三个入口直接当脚本跑）；
`test_edge_beat_badge.py`、`test_edge_position_p0/p1/p2.py`、
`test_control_init.py`、`test_graph_canvas.py`、`test_ui_qt.py`、
`test_main_window_mixins.py` 必须同时绿。



## §43 条件 / 菜单分支写编排层 + 接管进入点（2026-10-24 夜）

### 43.1 分支不进章节源文件

条件 / 菜单项回答的是"这一章**跑完之后**去哪"，可源文件里"跑完之后"不一定
有可插入的位置：章末是 `menu:` / `if:` 时，往块体里插一行会破坏缩进与分支
语义；`return` 之后插又永远不执行。所以分支段只写编排层：

    label X_control:
        call X
        # storygraph-edge:12
        if score > 3:
            jump Y_control
        return

`call X` 在 X 的 `return` 之后回到这里，因此 X 以 `menu` / `if` / `return`
收尾都不影响路由。**源剧本一个字都不加**（用户 2026-10-24："对源剧本 rpy
文件本身要做到最小的插入原则……尽量放控制流那里"）。

### 43.2 标记段：改 / 删不认行号

每段第一行是 `# storygraph-edge:<edge.id>`。改 / 删按它找（`find_branch_range`），
**不按行号** —— 行号会漂，标记不会。编辑时保留原缩进，段外字节原样不动；
`apply_control_branch` 写前核对"文件仍是计划时那一份"，被别处改过就拒绝写。

菜单项不能各写一个 `menu:`（第一个选项无条件先执行，第二个菜单永远轮不到）。
同一章源的所有菜单项住在**一个**组里：

    # storygraph-menu:ch01
    menu:
        # storygraph-edge:12
        "去屋顶":
            jump Y_control
        # storygraph-edge:13
        "留在教室" if has_key:
            jump Z_control

组标记定位 menu，`# storygraph-edge:<id>` 定位选项；增 / 改 / 删都只动那一个
选项，最后一个选项删掉时整个 menu 组一起走。`if` 分支不能静默改成菜单
（那会多一次玩家选择）—— 拒绝，让人删了重接。

### 43.3 接管进入点（ControlTakeover）

`X_control` 要有入口才执行。项目原来写 `jump X` 时，补一个没人进来的
`X_control` 只是死代码。接管一次做两件事：

  1. 补 `label X_control: call X; return`（没有才补）；
  2. 全项目 `jump X` → `jump X_control`。

`call X` **不改**：包装可能 `jump` 走，调用点永远回不来。`apply_takeover`
先核对所有文件，再逐个写；中途失败按 `old_text` 回滚；`TakeoverCommand`
撤销时逐字节写回。UI 先弹 `TakeoverDialog` 显示多文件 unified diff。

### 43.4 章末是 `jump` 时拒写分支

`call X` 永远不返回，写进 `X_control` 的分支一次都不会执行。`plan_control_branch`
扫源章最后一条顶层语句，是 `jump`（且不是 `jump X_control`）就**拒绝**并
说清，先让人改收尾。

### 43.5 跨章删源边：块体只剩它时删整块

一条 `jump` 是某个选项体 / `if` 体的唯一内容时，只删这一行会留下空块体
（Ren'Py 解析不了）。`plan_source_line_delete` 现在按缩进找块：

  · 块里只有它 → 连整块删（`menu:` 只剩一个选项时连 menu 一起）；
  · 块里还有别的语句 → 只删这一行；
  · `if` 后面紧跟 `elif` / `else` → 不删整块（`elif` 不能没有 `if`），补 `pass`；
  · 整块删完这一章一拍不剩 → 不删整块，补 `pass` 保住 label。

### 43.6 守卫

`tests/test_control_branch.py`（插入 / 编辑 / 删除 / 段外不动 / 写前核对 /
章末 jump 拒绝）；`tests/test_control_branch_export.py`（全条件分支与全条件
菜单后必须 `return`，否则掉进下一个 label）；`tests/test_control_takeover.py`
（多文件 / `call` 不改 / 写前核对 / 撤销逐字节 / CRLF+BOM / 中文 label）；
`tests/test_control_branch_ui.py`（窗口字段 / 拍位置拒绝 / 真窗口写分支 /
真窗口接管预览+撤销）；`tests/test_source_block_delete.py`（选项 / menu / if
整块删除与 pass 兜底）；`tests/test_edge_edit_dialog.py`（源边挪位置）；
`tests/test_edge_edit_writeback.py`（源边 move 命令 + 撤销）。

## §44 卡片写回的四条硬判据 + 控制流手写内容守门（2026-10-24 深夜）

### 44.1 每一格写回：**行数不许变**

卡片上每一格改一次，产物只许"换掉那一行"——不光要比"新值在不在"，
还要比**行数**。`Voice` 曾经插一条新的、旧的不删（同拍两条 `voice`），
只断言"新值在"是抓不到的。判据在
`test_writeback_fidelity.py::TestEveryEditableFieldTouchesOnlyItsLine`。

实现侧的三条身份键（`story/patcher.py`）：

- 音频行（`play` / `queue` / `stop`）：身份 = **动词 + 通道**，同一通道
  允许换目标（换 BGM / 音效就是这一列的功能）；
- `voice`：身份 = **关键字本身**（一圈只有一条），资产路径是内容；
- 注释：身份仍是**逐字**（`('other', 原文)`）。只改一个字的注释走
  **槽位配对**，且只在 `trust_ranges`（磁盘那份 == 载入时那份）并且
  两边各恰好一条注释时生效。无条件按"注释槽位"配会把整体错位一行的
  源文件改错行（`test_stale_ranges_never_change_the_file` 守着）。

### 44.2 Stage 格：完整投影 + 表达不了的原文

`app.beats.stage_cell` 的返回值必须让用户看到**全部**舞台指令：

    Stage 格 = compose_stage(beat) + stage_only_ops(beat.stage)

`beat.stage` 按设计只装"结构化列表达不了"的复杂子句；格子里若只有它，
用户改一格就会丢掉同拍的普通 show / hide（审计 P1-2）。

`compose_stage` 自己也要无损：`show expression …` 必须带 `expression`
关键字（与「立绘」列共用 `_expression_clause`）；复杂子句项不许再被
整拍 `with` 贴第二遍。

手改 Stage 的写回必须"清空 → 立即 hydrate → 归一"三步：
hydrate 把整格能认出的场景/立绘/退场/转场全部重建，归一后的
`beat.stage` 只剩 `[停顿]` / 原生语句这类表达不了的部分。不归一会在
渲染时和结构化字段双写。

### 44.3 控制流第四道守门：`_control` 块里的手写行

`plan_control_write` 除了"缺章 / 外来 label"，还要扫同名 `*_control` 块：

- 行形状不像跳转/菜单/条件/注释；
- 且这次要生成的同名块里**没有这一行**（逐字）→ 判为手写内容。

命中即 `ok=False`，`plan.manual_lines` 带 `label / 行号 / 原文`。
`forceable` 只在"唯一拦截就是它"时为真；`export_control_flow(allow_overwrite=True)`
只越过这一条，缺章 / 外来 label 永远不越过。界面必须先把行列给用户看，
默认按钮是「否」。

控制流图模式下 `$` / `show` 是图里节点的操作、会出现在生成文本里，
不算手写内容。

### 44.4 明确不做的两条（审计 P1-3 / P1-4）

- 首次导出不插生成标记：标记是 `write_generation` 认自家产物的唯一凭据，
  去掉会让"第二次导出"把自家文件判成手写文件。**保留。**
- 翻页即提交：G1/G2 修好后不再破坏内容，`test_clean_sample_roundtrip`
  的 12 个样本逐字节相同。**保留。**

### 44.5 守卫

`tests/test_writeback_fidelity.py` **54** 条（每格一行 / 行数不变 /
Stage 手改不丢同拍内容 / expression 关键字）；`tests/test_control_write.py`
**13** 条（手写内容识别 / 生成操作不误报 / 缺章不可 force / 确认后覆盖）；
控制流面 103、舞台面 195、UI 面 246 全绿。全量 **6494 passed / 1 failed**
（唯一红是真机素材新增的 `test_scan_golden`，代码未动）。

## §45 第二轮 P0：控制流 / 重排 / 三个高级区字段（2026-10-24 深夜）

### 45.1 补丁的身份键（`story/patcher.py::_line_key`）—— 现在的全表

| 行形状 | 身份 | 允许换什么 |
|---|---|---|
| 台词 `e "…"` / 旁白 `"…"` | `('say', 说话人)` | 正文（说话人一样/句位配对时说话人也能换） |
| **带尾巴的台词** `e "…" (id=…)` / `… with …` / `… # 备注` | `('say', 说话人)` ← 新 | 正文 + 尾巴（P0-8） |
| `show/hide/scene` | `(动词, 图像名)` | 属性 / 位置（词数不许变） |
| `play/queue/stop` | `(动词, 通道)` | 目标（只在行号新鲜时） |
| `voice` | `('voice', '')` | 目标 |
| **`call X …` / `jump X`** | `('flow', 动词)` ← 新 | 目标 / 参数 / `from`（同类型）；类型互换只在行号新鲜或 1:1 槽位（P0-5） |
| **`menu …:`** | `('menu', '')` ← 新 | 名字 / 参数 / 标题（P0-8） |
| `$ code` | `('code', 目标)` | 正文 |
| 注释 | `('other', 逐字)` | 只走槽位配对 / 注释块整段换 |
| 其它 | `('other', 逐字)` | 不换 |

`jump expression …` / `call screen …` 仍是 `('other', 原文)`：它们的目标不是
label，改了不该被"同类型"放行。

### 45.2 控制流写回：**一格改，只动那一行**

`session_table.apply` 里 `update_beat_flow` 之后必须做"代码格同步"：
若 `fields['CodeBlock']` 仍等于**旧的**那一行控制流，就把它改成新行。
不这么做，`CodeBlock != compose_code(b)` 会把 raw 清空、降级成 code ——
**改了等于没改，还 ok=True**（审计 P0-6，UI 从前靠 `_flow_code_synced`
私下绕开，脚本/表格/AI 全都踩）。

判据：`test_p0_round2.py::TestFlowWriteBack` 六条（API / UI / call→jump /
jump 改目标 / 改参数 / 只翻页不动）。

### 45.3 重排（P0-7）：整段按内存顺序重写

`_plan_reorders` 的准入（任一不过 → 不猜，`ok=False` 说原因）：

1. 同一 `label` 内，拍的行号序列出现**逆序对**；
2. 逆序对涉及的那些拍的区间合起来是一段连续区间；
3. 每一拍用**精确区间**认得出自己（`strict_of`）；
4. 段内不与别的拍交叠；空隙行只许空行/注释；
5. 段内不跨 `label` 头。

写法：这段当成一排**槽位**（源码顺序），槽位 k 填"内存顺序第 k 拍"的渲染
结果，缩进借该槽位第一行，空隙行原地不动。

**明确拒绝**：跨 label 的重排；同一章里"又移动又增删"（行号会漂）。

### 45.4 Label / MenuName / SayTail 写回

- `label 名:` 是块头、**不属任何一拍的行区间** → 逐拍对位永远看不见它。
  `_plan_label_renames` 按 `src_first` 最小的拍往上找最近一行 `label 旧名…:`，
  只改名字（参数表原样保留）；一个块头被改成两个不同名字 → 不猜，跳过。

  ⚠ **改名不追引用**：`jump 旧名` / `call 旧名` 不会被自动改写（它们可能在
  别的章里）。所以"改 Label"是给"这个 label 只有自己知道"的场合用的；
  有外部引用的 label 要先在控制流图上看依赖，或改完自己修引用。
- 会话层 `_apply_label`：改一格 = 给整章那个 label **改名**（同 label 的拍
  一起改）；新名字已被别的 label 占用时**不合并**（那是结构改动，交给导出校验）。
- `menu 名(args):` 的身份是"这是一个菜单头"，名字/参数/标题都属内容。

### 45.5 注释块：整段换，不是只加

一段区间里**恰好一处**连续注释块（两边都是）且行号新鲜 → 按用户写的那几行
整段替换。只在 1:1 时配对的话，1 行写成 2 行会变成"插进去 + 旧的留着"
（越改越多，审计 P1）。

⚠ 两条边界（真机金标抓出来的，别放宽）：

- 生成注释的正文如果**已经是这一段里某行的行尾注释**，丢掉它 —— 渲染器会把
  `show ling close_happy  # 27无图片…` 的行尾注释提到块首，照抄就写出重复两条
  （`chapter1_4.rpy` / `screens.rpy` 各一处）；
- 判断"行尾注释"时**内容部分必须非空**：纯注释行的内容就是空串，否则它会把
  自己的注释当成别人的行尾注释滤掉 —— 整段注释凭空消失。

### 45.6 守卫

`tests/test_p0_round2.py` **20** 条。金标 `test_patch_golden` 重录一次
（`script_pomo.rpy [edited]`：旧产物把"改过的台词（带 `(no_skip=True)`）"
和原句都留着，新产物只剩改过的那行 —— 理由写在测试文件里）。

## §46 第三轮 P0：菜单标题的行区间 / 删分支 / 移动与 `return`（2026-10-24 深夜）

### 46.1 菜单标题**行**属于菜单拍

`menu:` 的标题写在块里的下一行（Ren'Py **唯一**合法写法）：

```rpy
    menu:                        ← 拍的行区间从前只有这一行
        "是时候和青一起学习了."   ← 标题正文，从前不属任何一拍
        "开始":
```

从前改标题 → 生成的那一行落不进任何拍的区间 → `_lost_content` 报
"新增内容没能落进产物" → 整份拒绝；**零改动往返也会被拒**（同一处）。

- `MenuNode.caption_lines`（解析器记行号）→ `Statement.caption_lines`
  （**运行期动态属性**，与 `nest_path` / `_menu_member` 同类：不入表、
  不进内容指纹、不参与相等）→ `beat_build._emit_menu` 把 `src_last`
  推到标题末行；
- 此后配对成立：标题行与生成的那一行都是 `('say', '')` 键；
- 标题**多行**也认（Ren'Py 允许块里写两行标题）：行数不变走普通配对，
  行数变了（2→1 / 1→2）走 `_replace_matched` 里那条窄分支 ——
  只认"第一行是同一个不带标题的菜单头、其余全是裸字符串"，整段换成用户写的
  那几行（否则多出来的旧标题行没有对手、留在原地）。

### 46.2 标题在头行（`menu "标题":`）：保真，但不写坏盘

`menu "标题":` **不是 Ren'Py 语法**（`menu` 头只接受 `menu [名] [(参数)]:`；
`engine_lint` 实测 `expected ':' not found`）。但解析器认它，所以：

- 补丁层 `_menu_head_caption_merge`：源文件那一行是 `menu "…": [注释]` 时，
  把渲染出来的两行**合并回头行**（`pre + 引号标题 + post`）→ 零改动往返
  逐字节相同、改标题只动那一行；
- 行尾注释**不许照抄**（渲染器会把这一拍的注释贴到标题行上，照抄写出两份）；
- 多行标题 / 头行还带菜单名或参数 → **不合并**（形状对不上，不猜）；
- 写盘仍由引擎闸门把关：这种写法引擎读不了 → 拒绝并说"引擎读不了"，
  不静默写出一份跑不起来的脚本。

### 46.3 删掉一条分支：**邻分支的结构行一个字节不动**

删一拍、而它恰好是某分支唯一一条语句时，分支头必须跟着走（空块语法错）。
规矩：

- 删 `[分支头 .. 拍末行]`；**后面兄弟分支的头不动**；
- 唯一例外：被删的是链头 `if` → 紧跟的 `elif b:` 提升成 `if b:`
  （只换一个词，条件正文不动）；
- 链头后面只剩 `else:`（要整块降缩进）→ **拒绝**；
- 后面那支的第一条不是拍（是嵌套 `if` 头）→ 条件重编号算不出来 → **拒绝**。

为什么从前会把邻居改写成 `if (…) and not (…)`：拍上存的条件是**展平后的
累积形式**，删掉一支后解析器从头累加，与拍上存的对不上 → 校验判"条件改动
走全量"。现在文件侧不动，**模型侧对账**：`_chain_renumber` 按解析器口径
算出后面每支应有的条件，交给 `_verify_patch(renumber=…)` 作为**可接受的
重编号**（只容忍这一处、且要求完全相等）。

### 46.4 移动拍不许越过 `return`

`ChapterTree.move_beat` → `_guard_return`（先 `_slot_at` 算落点，再问）：

- 挪的是 `return` 那一拍 → 它必须是这一段的**最后**一条；
- 挪的是别的拍 → 不能落在某个 `return` 之后。

拒绝时**恢复原树**（重新插回原位 + 还原 `nest_path` / `condition` / `label`）
—— "不猜"不能留下半截结构。判据复用渲染层的 `_is_return_beat`。

合法：`return` 在分支体里是**那一段**的最后一条；把拍移进别的块看的是新那一段
的兄弟列表。新插卡不走这条守卫（UI 的落点逻辑本来就把卡插在 `return` 前面）。

### 46.5 派生两格（`Position` / `Expression`）在 API 侧也要写回

`beat_to_fields` 会算这两格，可 `session.apply` 从前不读它们（只活在旧的
`fields_to_beat`）→ "改这两格 + apply" 是静默 no-op。现在 `cards is None` 时写回：

- 只在新值**非空且与当前投影不同**时写（空串 = "位置不一致、没有统一值"，
  拿它去写会抹掉位置）；
- 只有**没有立绘卡**时才写（卡片是真源）；
- 位置两种存法（拍级 `b.at` / 立绘项 `@X`）**哪种在用改哪种**。

### 46.6 守卫

`tests/test_p0_round3.py` **23** 条（块内标题 ×7（含多行标题 2→1 / 1→2）、
头行标题 ×3、删分支 ×6 含真机 pomo、派生两格 ×3、越界移动 ×4）。
`test_incremental_export.py` 里那条"菜单标题配不上就该拒"的旧用例**改判**成
"必须落地、且只动一行"（拒绝不再是正确答案）；`test_incremental_branch_delete.py` /
`test_incremental_p0_fixes.py` 里两条按旧行为写的用例也一并改判
（后面兄弟分支的结构行不许再被展平 / 删分支不再退全量）。

**两种写法引擎不收、但保真照做**（零改动往返逐字节相同；一改就由引擎闸门
拒绝并说"引擎读不了"）：`menu "标题":`（头行标题）、`menu:` 块里的
`$ x = 1`（`extras`，实测 `expected menuitem`）。

## §47 第四轮：原生语句的身份 / 标题从无到有 / 改条件 / 回执分级（2026-10-25 上午）

### 47.1 一拍里的"原生语句行"与"台词行"各有身份，互不牵连

一拍可以是「工具不认识的语句 + 一句台词」（`mycue a=1` 与 `e "台词"`
会被解析成**同一张拍**）。判据：

- 台词行照常按 `('say', 说话人)` 配对 —— 改台词只动台词那一行，
  旁边的语句行**逐字节不动**；
- 原生语句行（`pause` / `window` / `with` / 自定义语句 …）的身份是
  **首词**（`patcher._statement_verb`），不是整行原文：`pause 0.1` →
  `pause 0.2` 是"同一条语句改正文"，不是"新增了一条语句"。
  从前按整行原文配 → 配不上 → 新行被**插入**、旧行留着 → 两行并存
  （`$ renpy.pause` 两遍、`pause` 停两次），还报成功；
- 首词相同的两条普通语句在 `_replace_matched` 的 ⑤ 里 1:1 配对，
  `_rewrite_line` 在同一判据下允许换正文；
- **不算**普通语句的（各有各的通道，不走这条）：台词 / 菜单头 / 选项行 /
  `$` 代码 / 注释 / 以冒号结尾的块头 / 结构关键字
  （`if` `elif` `else` `return` `label` `jump` `call` `menu` `while` …）/
  `show` `hide` `scene` `play` `stop` `queue` `voice`。

### 47.2 菜单标题"从无到有 / 从有到无"

菜单标题行归菜单拍（§46.1），行数变化走同一条窄分支，**允许任意一边没有
标题行**：

```rpy
    menu:                     →   menu:
         "新标题"                   "新标题"     ← 缩进借块体第一行（`_body_pad`）
         "开始":                    "开始":
```

- 缩进：源里有标题行就借它；从无到有借**块体第一行**的缩进
  （`_child_pad_at`），拿不到才退回"块头 + 一级"；
- **空白标题（`" "`）不许被当成"用户删了标题"**：`menu_caption_text` 会
  strip，渲染结果天然少这一行；只有旧标题有实质内容时才允许删
  （`_caption_is_substantive`）。

### 47.3 改「条件」：链头能增量，其余三类说清理由

- 可增量：`if …:` 这一行是**这一拍自己的块头**、且上面除了 `label` 没有别的
  块（`_has_outer_head` / `_beat_owns_head`）→ 整行换成 `if <新条件>:`
  （`_plan_condition_rewrites`，与 label 改名同一类：块头单独改）；
- 链头一改，**兄弟分支的累积条件要跟着重算**（`rechain_condition` +
  `apply` 的 `_rechain_siblings`）：`elif` 的守卫是 `(b) and not (a)`，
  `(a)` 过期了就会让"增量产物"与"全量重写"写出两种剧情；
- 改不了的三类（`_condition_hint`，一条都不静默）：`elif`/`else` 那一支
  （累积条件一行写不下）、嵌套 `if`（累积条件含外层）、清空条件
  （= 拆掉这一层 `if`）。

### 47.4 移动拍

- 普通拍（区间之间只有空行/注释）→ 整段按新顺序重写，能落盘；
- **跨过块头**（`if` / `elif` / `else` / `menu:` …）→ 拒绝，理由点名那一行：
  台词一旦离开分支，条件就不再生效（或换了判定顺序）= 改剧情；
- **带条件的拍换位置** → 拒绝，理由说"条件由**位置**决定"。

### 47.5 回执分级：`blocking` / `warnings` / `empty_beats`

- `blocking`（+`codes`）只放**拦下这次写盘**的原因；
- `warnings` 放提示（`OUTSIDE_BUILD_DIR` / 未建模的自定义块 / 缺资源 …），
  成功路径也带（UI 写日志，失败时弹窗单列一段"不是这次被拦的原因"）；
- `PatchResult.empty_beats`：新插入、一个字都没有的拍数 →
  回执说"另有 N 张空卡片还没有任何内容（不会产生行）"。
- `$` 语句编译不过、而首词是这一章某拍的**语句名**时，拒因补一句
  "这是自定义语句不是 Python，参数请填参数格（Excel 里是 `名·参数` 列）"。

## §48 落盘安全：载体风格 / 非 UTF-8 / 引擎闸门（2026-10-25 中午）

### 48.1 「原地改写」一律走出入对称：读时记风格、写前还原

凡是"读回来 → 改 → 写回去"的落盘点，都必须用
`source_style.detect()` / `style.apply()`（BOM + 换行），并且**读的时候
就决定用哪种解码**：

- 要"逐字节保留"的（选择点追加、控制流、ladder）：**严格 UTF-8 解码**
  （`read_bytes().decode('utf-8')`）。`source_style.read_text` 用的是
  `errors='replace'`，适合给解析器看，**不适合**读回来再写回去 ——
  坏字节会被换成 `\ufffd`，那就是改坏用户文件；
- 只给校验当参照的（`_source_text_for_guard`）：用
  `source_style.read_text`（剥 BOM、统一 LF）——**不许**用
  `Path.read_text`（BOM 留在第 1 行，`OPAQUE_LOST` 会把每份 BOM 章节误拒）。

### 48.2 非 UTF-8 文件：载入就报错，不许静默 0 拍

`EditorSession.load` 先按 `utf-8-sig` 严格解一遍；失败抛
`ValueError`（"这个文件不是 UTF-8（Ren'Py 只认 UTF-8）：… 请另存为 UTF-8"）。
Ren'Py 只认 UTF-8，所以"这一章是空的"那种界面状态是**假话**。

### 48.3 「表 → .rpy」的三条出口必须同一套闸门

`cli table xlsx2rpy` / `build` / 卡片页导出 / **UI 表格导出**
（`app/tables.apply_export`）都要 `engine_lint=True` +
`lint_ignore_heads=declared_heads_near(src)`。少传一处，就等于"界面这条
路写出去的产物引擎读不了也没人管"。

### 48.4 工具独占文件要带生成标记

`*_choices.rpy` 这类"工具独占、别人不碰"的文件，创建时就要
`ensure_generator_marker`（与 `control_takeover` 一致）——否则将来任何
"重算"流程走 `write_generation`，第一步就被 `HANDWRITTEN_TARGET` 拒写。

## §49 改名补丁 / 换行 / 资产表的四条硬判据（2026-10-25 下午）

### 49.1 计划 → 应用之间，文件可能被改过：必须复读比对

凡是"算出 `new_text` 再落盘"的两段式操作（改名补丁是唯一一处），
`apply` 必须**写前复读全文**并与计划里的 `old_text` 比对；对不上就**拒写该
文件**（其余照写），把文件列进 `changed_since_plan`，`ok=False`。
理由：计划里那份全文是**快照**，中间任何一方（IDE / 另一个实例 / git /
用户自己）改了文件，拿快照覆盖就是**静默吞掉别人的改动**。

配套两条：`ok = not failed and not changed_since_plan`（P2-1：从前
`swallow` 之后仍报 `ok=True`）；**备份失败也不写**（`.bak` 是"可回滚"的
另一半契约，写成功而备份失败 = 用户再也拿不回原名）。

### 49.2 改名只改"资产引用位"，判据在 `assets/rename_scope.py`

整份词边界 `sub` 会改到代码 / 注释 / 字符串。逐行判据（`analyse_line`）：

- 注释行、`$`、`python`/`init python` 块体、`label`/`jump`/`call` 行 → **整行不改**；
- 已知语句首词（`show` `scene` `define` `image` `with` …）→ 参数位从首词**之后**开始
  （资产 id 叫 `show` 时不改关键字）；未知首词（自定义语句、`meiko "hello"`）→
  第一个词就是参数位，**可改**（角色改名靠这条）；
- 引号里：只有音频/语音语句**且**是一整段路径（`"bgm_main.ogg"`）才改；
  `"bg 音乐.mp3"` 里那个 `bg` 不改；
- 行尾注释（引号外的 `#` 之后）不改。

被拒的命中**必须报出来**（`plan['rejected']`：行号 / 那一行 / 原因），
读不了的文件进 `skipped_files` —— 改名"少改一处"在游戏里就是"找不到图"。

### 49.3 换行：能逐行还原就逐行还原，`apply(text, source=路径)`

`detect()` 只给出**主导**风格，混合换行文件里 50/50 时不存在主导 → 整份归一
会静默改掉没碰过的行。凡是写回**已存在的文件**，都要传 `source=`：
逐行对齐原文、原样那一行用它自己的 EOL、插入行借前一行。单一风格的文件走
老路径（逐字节等价），所以这条对老文件是**零风险**的。

### 49.4 资产表的"文件在不在"只有一个答案

- `FileWalkIndex.match()` 对绝对路径与 `_SKIP_SCAN_DIRS`（`tmp/` `cache/`
  `build/` …）**必然**返回 None —— 那是"索引不覆盖"，**不是**"文件不存在"；
- 所以 `_missing_paths` 只对**索引管得着**的路径判缺失（`_walk_covers`），
  管不着的走 `_probe_row_file` 磁盘探测；探不到也**不删**，进
  `_unjudged_paths`（只报不删，`unjudged_by_kind` 进回执）；
- `_row_file_exists` 与 `_missing_paths` 必须同一套判据（从前两套 → 同一行
  被判"缺失删掉"又在别处算"存在"）；
- 表备份固定名 `*.bak.xlsx`，**上一代**归档成 `*.bak-<时间戳>.xlsx`。

## §50 第三轮：编码腐蚀 / 逐行还原的调用点 / 金标怎么改（2026-10-25 下午）

### 50.1 "读回来再写回去"必须**严格解码**，一个例外都没有

`source_style.read_text` 用 `errors='replace'`（给解析器看的口径）。凡是
"读已有文件 → 改 → 写回同一路径"的地方，都要先 `read_bytes().decode('utf-8')`
探一次，解不开就**拒写并说清第几字节**，字节一个不动：

- `choice_point` 的追加路径（§二十五 §3）；
- `_write_new_chapter` 往已有 `.rpy` 追加 label（第三轮 P1-A —— 从前坏字节
  被换成 U+FFFD 写回，无备份、无声）。

BOM 不受影响：严格 UTF-8 把 BOM 当合法字符，`detect()` 照旧记着它。

### 50.2 `apply(text, source=…)` 的调用点：凡是"原地改已有文件"就要传

已传：`write_generation` / `compile`(×2) / `choice_point` / `control_route`(×4) /
`control_branch` / `control_takeover`(×2) / `source_line_delete` / `window_nodes` /
`session_export`。

**有意不传**：`graph/commands` 的 undo/redo。理由：那条路的 `old_text` 在读取时
已被 `read_text` 统一成 LF，每行 EOL 在内存里本来就不存在。要逐字节还原 undo，
得在计划链里保存原始 EOL —— 那是设计题（新存储），不是"补一个参数"。
新加原地写盘点时照这条清单自检：不传就得能说出"我为什么没有 EOL 信息"。

### 50.3 金标（golden）变了怎么办

先证明"变的是哪一部分"，再改金标：

1. 找出差异的来源（例：新加了 `unjudged*` 两个键）；
2. **把它摘掉重算**，如果指纹回到旧值，说明行为只多了这部分（`sync` 金标就
   是这么核的：`94579ad7…`）；
3. 更新金标 + 在测试 docstring 里写清"为什么变"，提交信息同写。

反过来：如果摘掉新东西也算不出旧值 → 那是**行为回归**，不许改金标。

## §51 导入侧的"认不出来"必须出声（2026-10-25 下午，P1-C）

### 51.1 两种说话人形状，两张网同口径

`txt_import` 认 `小雨：台词`（冒号）与 `小雨"台词"`（引号）两种形状。**认不出来**
的行（narration 且形如"短名字 + 台词标记"）都要进 `suspect_speakers`
（带 `shape` 字段）+ 一条人话警告：

> 看着像谁在说话，但这个名字不在角色表里（它们被当成了旁白）……要不要把它当
> 成角色？要认：去角色表补别名（重导就认）；不认：确认它是旁白即可。

**只报不猜**：判读与产物**一个字不改**（仍是旁白），不自动 `define`、
不自动建角色、不进 `needs_decision`、不拦落盘。与冒号网完全同口径。

### 51.2 引号网的边界（宁少报不乱报）

引号在中文旁白里到处都是，所以引号形状额外要求：

- **引号成对且闭引号就是行尾**（括号注释先剥：`小雨"好。"（笑）` 算）；
- 通过同一道名字闸门（≤6 字符、2–4 汉字或像变量名；数字开头 / 标点 /
  「说曰道问答笑消息的是了吗呢啊哦嗯」一律排除）。

**故意不覆盖**：引号没闭合（真机唯一命中是贴进正文的报错栈
`File "C:\admin_script.py", …`）、闭引号后还有正文（`突然"砰"的一声`）——
扩到它们会让整片真旁白进提案，噪声比漏报更糟。

### 51.3 改回执措辞会打金标

`tests/golden_txt_import_fingerprint.txt` 钉住了**真机 5 章的 `warnings` 全文**。
动警告措辞前先想清楚：要么保持原有句子逐字节不变、只**追加**新句子（P1-C 就是
这么做的：只在确实有引号形状命中时追加），要么同时更新金标并说明理由。

## §52 章节名（label）的唯一来源与"撞名"口径（2026-10-26 晚，真机）

### 52.1 数字链**段数不截断**

`txt_import.chapter_id_from_filename(filename)` 是**唯一实现**（`^(\d+(?:[\-_]\d+)*)`），
`label_from` 的回退 2 与 `txt_pipeline.target_stem` 都调它，**不许再抄一份正则**。
作者的命名是"章-节-段"：`2-1-2凌跃捷线-定稿.txt` → `chapter2_1_2`
（从前只取两段 → `chapter2_1`，同一节四个片段抢一个 label）。

`_CHAPTER_ID_RE`（正文里的 `chapter2-1-2`）与 `_HEADER_RE` 的编号前缀
（`2.1.2 chapter2-1-2 标题`）同样放开段数；三段章节头从前认不出来，
会掉进正文变成一句旁白。

### 52.2 撞名：文件名与产物里的 label **必须一起改**

`build_plan` 同批撞名 → 目标加 `_2` 之后，`label` 要重新取 `target.stem`
（label 与文件名同源），并记进 `PlannedFile.renamed_from`、在 `run().messages`
里**说出口**。

只改文件名会造出两份声明同一 label 的产物：第一份落进 `game/` 后，第二份必被
`LABEL_COLLISION` 拦下，而作者看到的是"我的 `chapter2_1_2` 被拦了"。
改名可以，静默改名不行。

### 52.3 `LABEL_COLLISION` 必须点名**哪个文件**占着 label

`generation_guard.label_defined_in(name, project_root, exclude)` +
`_relative_hint()`：消息尾巴带"（就定义在 `game\chapter\chapter3_1.rpy`）"。
扫描口径与 `occupied_labels` 同一份（`discover_scripts`，排除
`tl/` 与 `*.generated.rpy` / `*.blocked.rpy` / `*.bak.rpy`）。

找不到（没 project_root / 临时目录）就**不加这句**，不编路径。

---

## §53 导入冲突预检（"先问后写"）：三种结局 / 零写取消 / 清理残留（2026-10-26 晚）

用户原话："当检测到有 label 重复的时候，能不能不要擅自不落盘，或者擅自全量
替换呀……能不能给出一个 UI 提醒，让我能够对着 txt 换 label？如果撞 label，
显示新内容与旧内容对比，再决定是否强制更换（可以全部强制更换，也可以一个个
文件分……）"

实现分层（改之前先看这张表，别在弹窗里重算判据）：

| 层 | 位置 | 管什么 |
|---|---|---|
| 预检/决策 | `authoring/import_review.py`（纯，只读） | `preflight(plan, root) -> Review`；`apply_decisions(plan, review, decisions, rows)` |
| 流水线 | `authoring/txt_pipeline.run(..., resolve=)` | 写前问；取消零写；不给 `resolve` 时老行为不变 |
| 会话 | `app/session_graph.import_plain_text(..., resolve=)` | 透传 + 返回 `review` / `needs_review` |
| 界面 | `ui_qt/dialog_import_review.py` | 左表三选一 + 右栏新旧对比；只产出 `choices()`，**不碰盘** |
| 清理 | `authoring/import_cleanup.py` + `ui_qt/dialog_cleanup.py` | 扫/删 `.rpy.bak`、`.rpy.bak-<时间戳>`、`.blocked.rpy`、`.bak.rpy` |

### 53.1 预检只判两件"当场能确定"的事（与门禁分工）

1. 计划里的 `label` 是否被**别的** .rpy 定义（`generation_guard.label_owners()`，
   **同一份**扫描口径给 `occupied_labels` / `label_defined_in` 用；"被 ≥2 个文件
   定义"= 项目本来就重名，覆盖救不了）；
2. 计划落点是否**手写**（`generation_guard.is_handwritten_script()` —— 与
   `write_generation` 的 `HANDWRITTEN_TARGET` 同一份判据，不许各写一套）。

真正的落盘判据仍是 `write_generation`（语法 / 引擎 lint / 编码载体 / 顶层块
丢失……）。**预检不替门禁放行**：人的决定若仍不合法，门禁照旧拦。

### 53.2 三种结局的落点语义（判据钉在 `tests/test_import_review.py`）

| 动作 | 常量 | 落点 | 备份 |
|---|---|---|---|
| 覆盖旧文件 | `ACT_REPLACE` | **写进那个占位的 .rpy**（不是计划落点，不另造撞名产物） | `write_generation(force=True)` + `.bak`；旧文件全文作 `source_text` 喂门禁算"丢哪些顶层块" |
| 改个名字写入 | `ACT_RENAME` | 写到**旧章节旁边的目录**（`item.writable_dir` = 占位文件父目录），label 重跑 `convert_text` | 不动旧文件 |
| 这次跳过 | `ACT_SKIP` | 不写，**不产生** `.blocked.rpy` | — |

**没有"覆盖"选项的情形**：项目本来就重名（≥2 个 owner）、或既有 label 冲突
目标又是手写文件（两边都占着，说不清该覆盖谁）→ 只给改名/跳过（宁可少一个
选项，不猜）。默认值恒为"不改变现状"：占位的是工具生成 → 预选 replace；
手写 / 重名 → 预选 skip。

### 53.3 `resolve` 契约（`session.import_plain_text` 与 `TP.run` 同形）

```
resolve: callable(review) -> [{'source': str, 'action': 'replace'|'rename'|'skip',
                               'label': str(仅 rename)}] | None
```

- 返回 `None`（界面点"取消"）→ `needs_review=True`，**零写盘、零副本**；
- 返回列表 → 逐份应用；**没给结论的文件按 skip 处理**（默认不能是"覆盖"）；
- **不传 `resolve`**（CLI / 单测）→ 行为与 2026-10-26 之前**完全一致**：
  照旧写、照旧被门禁拦、照旧留 `build/review/*.blocked.rpy`。老调用方的判据
  钉着这条（`test_without_resolve_the_old_behaviour_is_kept`）。

改名合法性只有一套理由：`why_rename_failed()`（落盘层）与
`Review.rename_problem()`（弹窗即时校验）都基于 `valid_label` + 当前占用集合；
两处各写一套早晚会漂成"弹窗说行、落地说不行"。

### 53.4 幂等：内容一致**不重写**（也就不会多 `.bak`）

`txt_pipeline._identical_to_disk()`：目标已存在且读出的文本 == 产物文本 →
`written=True, unchanged=True`，**不碰文件**（不备份、不刷 mtime）。
这一条专治"反复重导同一批 txt 把 `.bak` 越攒越多"（用户实机抱怨的一半）。
它是"读来比较"，不是"读来写回"——后者必须走 `source_style` 的严格解码（P0-B）。

### 53.5 对比数据（`compare_texts`）：CRLF 要在展示层去掉 `\r`

`split_lines()` 只按 `\n` 切（P0-A：Ren'Py 只认 `\n`），所以 CRLF 旧文件切出来
每行带尾 `\r` → 对比会把**每一行**标成"改"。展示层 `rstrip('\r')` 只影响
对比，**不影响落盘口径**（换行由 `source_style.apply(source=)` 逐行还原）。

### 53.6 清理残留的安全红线（`import_cleanup`）

- 只认四类**完整后缀**：`.rpy.bak` / `.rpy.bak-<数字>` / `.blocked.rpy` /
  `.bak.rpy`；**绝不宽匹配 `*.bak`** —— `*.bak.xlsx`（资源表备份，用户唯一的
  表备份）既不许列、也不许删；
- 只在 `<根>/game/`（递归）与 `<根>/build/review/` 工作；项目不存在 / 没有
  `game/` → `[]`，不抛异常；
- `delete` **不信任调用方**：每条再验命名 / 所在目录 / 不是链接 / 是普通文件，
  不匹配进 `failed` 带中文原因；单条失败不影响其它；`unlink(missing_ok=False)`
  （扫描后消失要报出来，不许假装删成功）；
- 界面默认勾选：时间戳备份 / 对照副本 / 历史 `.bak.rpy`；**`.rpy.bak` 默认不勾**
  （可能是覆盖手写文件前的唯一旧版本），弹窗里写明理由。

---

## §54 Qt 槽异常不许静默 / 记忆门禁必须在唯一写入口（2026-10-26 深夜）

用户原话："当块状编辑器没有源文件的时候……我点击这个块，右键的所有栏都没反应
……点击导入源文件也没有反应……"

### 54.1 `QMessageBox.addButton(text, role)` 的第二个参数是 **ButtonRole**

- ✅ `QMessageBox.AcceptRole` / `RejectRole` / `DestructiveRole` / `ActionRole`
- ❌ `QMessageBox.Ok` / `Cancel` / `Yes` / `No` …… —— 这些是 **StandardButton**
  （`Cancel` 的值是 4194304）。传进去 PySide6 直接

  ```
  TypeError: addButton(str, StandardButton)
  ```

  而异常在 Qt 槽里**只打到 stderr**，界面表现就是"点了没反应"。
  真事故：`window_nodes._repair_choice` 把 `QMessageBox.Cancel` 当角色传，
  无源节点的四个修复入口（右栏主按钮 / 右键「新建 / 绑定源文件…」/
  「导入并绑定源文件…」/ 源文件丢失的询问框）**全部静默失效**。
- 守卫：`tests/test_ui_reaction_contract.py::test_no_standard_button_is_passed_as_a_button_role`
  （AST 扫 `storygraph/ui_qt/**/*.py`，`addButton(文本, X.<标准按钮>)` 一律红）。
- 要标准按钮请用 `setStandardButtons(...)`；要自定义文字请用角色。

### 54.2 菜单项 / 右栏按钮的动作必须过安全壳 `_menu_action`

`NodeEditMixin._menu_action(name, fn)`：异常时 ① `swallow` 台账 ② 状态栏
③ 控制台（还顺手吃掉 `QAction.triggered(bool)` 的 bool）。
`build_node_menu()` 里每个动作、右栏 `_open_script_btn` 等都必须用它。
判据：给 `_on_node_open` 塞必抛桩 → 状态栏必须出现"出错：RuntimeError"。

**任何"有模态框的动作"都要有一条真建框的判据**（把 `QMessageBox.exec`
换成"记录按钮后立刻返回"）。只打桩 `_repair_choice` 会漏掉"框根本建不出来"
这一类 —— 本次事故就是这么躲过全部测试的。

### 54.3 全局开关（记忆/自动保存）必须判在**唯一写入口**里

反例（2026-10-26 P0）：记忆开关 `enabled()` 只被 `closeEvent` 那一侧检查，
而 `remember_graph_files()`（UI 导入章节时调）直连
`remember_project → save`、**一路没人查** —— 跑一轮 UI 测试就往用户真实的
`%LOCALAPPDATA%/StoryGraph/session.json` 里写 **849 个** `tmp_path` 项目。
测试全绿、用户下次启动可能自动打开一个早被删掉的临时项目。

现在：

- `session_store.save()` 里判 `enabled()`（❌ 不给 `path=`、❌ 环境开关关掉时直接不写）；
- `remember_cards()` **刻意不判**：它写的是**项目里**那份
  `storygraph.session.json`（折叠状态跟着项目走），不是用户级记忆表 ——
  "关掉这台机器的记忆"不该把项目内数据也关掉
  （`test_feedback_p5_fold_project.py` 8 条钉着这个语义）；
- 判据 `tests/test_session_memory.py::test_the_switch_gates_every_write_entry_not_just_the_window`
  钉住用户级写入口（`remember_project` / `remember_graph_files` /
  `set_auto_open` / `forget`）。
- **教训**：开关放在"调用方"就等于没放 —— 漏一个调用点就是静默污染，
  而且症状只在用户下次开工具时才出现。

### 54.4 "真实记忆表"vs"测试记忆表"：临时项目只许剪真实表

- `_is_real_table(path)`：`path is None` **且** `STORYGRAPH_SESSION_CONFIG`
  没把位置指走 → 才算用户真实表；
- 真实表：写时 `prune_temporary()` 剪掉含 `.pip-tmp/`、`/pytest-`、
  `/popen-gw`、`/fasttest/runs/` 的路径；读时 `last_project()` /
  `recent_projects()` 也忽略它们（旧版本写坏的表现在还在硬盘上）；
- 显式 `path=` / 环境变量指走的表：**不剪不滤** —— 测记忆的用例本来就在
  `tmp_path` 里造项目（否则全部用例都会挂）。
- 跑 UI 探针 / 截图脚本时同理：设 `STORYGRAPH_NO_SESSION=1`（或
  `SS.set_enabled(False)`），否则脚本会把自己造的临时项目记进**真**记忆表。

---

## §55 纯文字导入的**导出文件夹** / 顶部工具栏顺序（2026-10-26 深夜，真机）

用户原话："不要写入 chapter 文件夹！因为很多项目根本没有这个文件夹！我们必须
保证通用性和泛用性！！！你可以给一个 rpy 的导出路径，问一问导出到哪个文件夹
就行！" + "上头的 UI 应当整理：章节导航最左、然后是打开项目、自定义模块、
AI 助手；先导入、后导出、最后刷新和清理"。

### 55.1 `out_dir`：**导出文件夹由人指定，工具不猜子目录**

| 环节 | 契约 |
|---|---|
| 界面 | `window_graph_ops._ask_plain_text_export_dir()`：`QFileDialog` 目录模式，默认 `<根>/game/`（存在的话）；**取消 = 本次导入零写盘**，状态栏说清 |
| 会话 | `EditorSession.import_plain_text(..., out_dir=Path)` → `txt_pipeline.run(out_dir=)` |
| 规划 | `build_plan(root, files, rows, out_dir=)` → `plan_target(..., base_dir=)` |
| 预检 | `import_review.preflight(plan, root, out_dir=)` → `ReviewItem.writable_dir`（弹窗显示与实际落点**必须同源**） |
| 落盘 | "改个名字写入" / 新文件 → `out_dir`；"覆盖旧文件" → 那份已占位的 `.rpy`（覆盖不能换地方） |
| 越界 | `out_dir` 不在 `<根>/game/` 里 → 控制台警告"Ren'Py 不会加载这些章节"（允许导，不让人白导） |

- **禁止**再写"跟着旧章节所在目录走"（`owner.parent`）—— 那是把某个项目的
  `game/chapter/` 当通用规则；用户 2026-10-26 明确否掉。
- 不传 `out_dir`（CLI / 旧测试）行为不变：`<根>/game/`（没有 `game/` 就项目根）。
- 判据：`tests/test_import_review.py::test_the_export_dir_is_where_new_files_go`、
  `tests/test_txt_pipeline.py::test_the_export_folder_is_chosen_by_the_user_not_guessed`、
  `tests/test_txt_import_session.py` 的"选 chapter/ 就写 chapter/"与"取消=零写"。

### 55.2 顶部工具栏顺序（用户 2026-10-26 点名）

```
▥ 章节导航 → 📁 打开项目 → 🧩 自定义语句 → 🤖 AI 助手 →
➕ 导入章节 .rpy → 📝 导入纯文字章节 → ⤓ 导出 .rpy →
🔁 刷新已导入章节 → 🧹 清理导入残留 → （补全缺失 / 清空剧情图 / 其它）
```

- **章节导航**是两页工具栏的第一个动作（同一个 checkable `QAction` 挂两个
  toolbar，勾选状态 Qt 自动同步；`window_state._restore_docks` 仍按
  `self._nav_action` 摆回）。模式条只剩"两页标签 + 位置信息"。
- 这条顺序**取代**两条旧契约：2026-10-10「自定义语句排第一」与
  2026-10-21「导出排最前」。判据改为
  `tests/test_custom_statement_entry.py::test_the_toolbar_order_matches_the_user_spec`
  与 `tests/test_toolbar_export_entry.py::test_export_sits_between_imports_and_refresh`。
- 窄窗口里靠后的动作会进「»」是**预期**（用户把导入排在导出前面之后，不可能
  全塞进 1280px）；宽窗口（≥2400px）必须全部可见。

### 55.3 「填 API key」归入 AI 助手面板

工具栏**不再有**独立的「AI 助手 · 填 API key」按钮；入口在
`ui_qt/panel_ai.AiPanel.key_btn`（点它开 `dialog_ai.AiKeyDialog`）。
判据：`tests/test_ai_key_dialog.py::test_the_entry_lives_inside_the_ai_panel`
（工具栏里出现独立 key 按钮会红）。

## §56 接线/探测的**读盘成本**：项目级扫描走 `story.scan_cache`（2026-10-27 凌晨）

用户原话："目前每一次连线都很卡……我希望优化一下算法，让它减少等待时间"。
根因不是画线，是**同一个项目在一次操作里被整份解析了 6~8 遍**（真机
`sr_general` 91 文件 / 1.2 MB：`detect_placement` 1435 ms、
`find_control_file` 1498 ms、无包装章的 `plan_control_route` 3389 ms）。

### 56.1 谁能缓存、谁不许

| 函数 | 契约 |
|---|---|
| `story.scan_cache.read_text(path)` | 同 `source_style.read_text`；**只读**，按 `(mtime_ns, size)` 失效。写盘路径仍必须走 `SourceStyle.apply(..., source=路径)`（严格解码 + 核对原文），**不许**把缓存里的 text 直接回写 |
| `scan_cache.labels_of(path)` | `label 名字:` 列表（与 `control_route` 同一条正则） |
| `scan_cache.file_facts(path, root)` | `placement.scan_file` 的缓存版（整份解析的结果），返回**拷贝** |
| `scan_cache.discover(root)` | `discover_scripts` 的缓存版；失效看**目录 mtime**（新建/删除/改名）+ 缓存里的文件是否还在 |
| `scan_cache.fingerprint(root)` | 项目指纹 `((路径, mtime_ns, size), ...)`，所有"项目级结论"的失效判据（91 个文件 ≈ 2 ms） |
| `scan_cache.memo(name, root, extra, fp, compute)` | 项目级结论缓存；指纹不同才重算。放进去的 `compute` 必须**纯读** |

- **不做 TTL**：TTL 会让"刚改完源文件就接线"读到旧结论，并让"写完文件立刻
  断言"的用例变成随机失败。内容变了 `mtime_ns` 就变，本工具自己的
  `write_text_atomic` 也会让指纹失效。
- 缓存**有上限**（`MAX_ENTRIES = 512`，LRU 挤压）；开一百个项目也不会撑爆。
- 判据：`tests/test_scan_cache.py`（单候选不排序 / 多候选排序不变 /
  加删 label 立刻生效 / 第二次零读盘零解析 / 改过的文件必须重新解析 / 有上限）。

### 56.2 `find_control_file` 的**单候选短路**（结论不变）

候选 = "文件里有 `*_control` label"。排序的三个判据（精确 `{src}_control` /
`detect_placement` 认的纯控制流文件 / 文件名带 control）**都只在多候选时
才有意义**。所以：

```python
if len(with_control) == 1:
    return with_control[0][0]      # 排序只有一个元素时结论相同
```

这一条是"算法"层面的改动（不是缓存），把真机上 1498 ms 直接变成 ~2 ms；
判据要求：把 `placement.detect_placement` 换成一个**会抛异常**的函数，
`find_control_file` 仍要给出正确答案（见 `test_scan_cache.py`）。

### 56.3 画布重建：每个块只排一次文字

`GraphCanvas.set_graph` 是重建的唯一入口；`NodeItem` 的 `mark`（分析结论）
必须在**构造时**传入，`_apply_marks` 只在 `mark` 真的变了时 `refresh()`。
理由：`NodeItem.__init__` 已经 refresh 过，再刷一遍只是把同一段文字排版两次
（120 块白花 15 ms，视觉零变化）。
判据：`tests/test_graph_canvas.py::TestRebuildIsNotWasteful`。

## §57 分析器缓存 / 刷新只读改过的文件（2026-10-27 凌晨，第二轮性能）

用户原话："剩下的一些流程也需要优化一下……就做那些会几秒以上卡顿的"。
真机上过一秒的只剩三处：**打开项目 4.0 s**、**导入 91 章 2.8 s**、
**刷新已导入章节 3.0 s**（哪怕 0 改动）。这一节管后两个里"重复劳动"的那部分。

### 57.1 `IncrementalRenPyAnalyzer` 的缓存**跨调用**复用

| 规矩 | 判据 / 说明 |
|---|---|
| 会话持有一个分析器 | `EditorSession.import_chapters` 用 `self._analyzer`（懒建）；**不许**每次 `new` 一个 —— 那样缓存只活一次调用，刷新等于全量重解 |
| 只逐出**磁盘上不存在**的文件 | 以前"这一批之外"全删，等于把"没参与这一批"当"被删了"；先导 3 章再补 2 章会把前 3 章扔掉 |
| 有上限（`max_source_bytes`，默认 8 MB 源码） | 按插入顺序淘汰；**本批正在用的一个都不淘汰**（否则同一次导入解析两遍）。AST 约占源码的十几倍内存：1.2 MB ≈ 40 MB |
| 复用必须产出相同结果 | 同一批 AST 连续 build 两次，statements / beats / nodes / edges 逐项相同，且 AST 节点不会被打上属性。这是"跨批次复用 AST"的前提，判据 `test_rebuilding_from_the_cache_is_identical` |
| `(mtime, size)` 是唯一失效判据 | 与 `scan_cache` 同一套；同长度改写靠 mtime 抓到（有专门用例） |

### 57.2 「🔁 刷新已导入章节」只重读**真的改过**的文件

`import_chapters` 会把这一批导入时的 `(mtime_ns, size)` 记进会话
（`remember_import_stamps`）；`session.stale_import_files(files)` 返回"与记录不同
或没有记录"的那些文件。界面据此分两档：

| 情况 | 契约 |
|---|---|
| 一个都没变 | **不弹预览框、不重解**；状态栏写明"这 N 章的磁盘文件都没有改动（mtime + 大小都没变）"，并提示"确实要强制重读就用「➕ 导入章节 .rpy」重选一次" |
| 变了 N 个 | 预览只列这 N 个（其余报"跳过 N 个"），只把它们交给 `import_chapter_files` |

- 语义**没有变**：磁盘上改过的一个都不会漏（同长度改写也抓得到）；
  删 label / 加 label / 改正文都照旧由导入那条路处理（`_prune_missing_source_nodes`
  / `rebuild_source_edges` 只作用于这一批文件）。
- 没有导入记录的文件（图从别处塞进来的）一律当"变了" —— 宁可多读，不许糊弄。
- ⚠ 判据校准：`test_feedback_p1_workbench_sync.py::test_reload_shows_preview_before_importing`
  改成"先改源文件再刷新"（"导入完立刻刷新"现在属于"没有改动"那一档）。
- 判据：`tests/test_import_stamps.py`（11 条，分析器 / 会话 / 界面三层）。

### 57.3 量了、但**故意不动**的

- 打开项目 4.0 s = 资源表 1.34 s + 恢复上次剧情图 1.63 s + 导航/候选 0.23 s：
  都是一次性首读；再快只能落盘 AST / 图快照，涉及"快照与源不一致"的失效与
  保真（图的 `statements` 是提示/分析的真源），不做。
- 导入 91 章 2.8 s：解析器本身的成本（函数级剖析是平坦的，没有重复劳动可删）。
- 贴版式 0.24 s / 找缺失章 0.19 s / 重画画布 0.20 s：都在"几百毫秒"档，
  按用户口径（只做几秒级）不做。


## §58 卡片上的「这一拍的舞台图」（2026-10-27 傍晚）

用户原话："每一拍的舞台信息那里……可以除文字描述之外，给一张这一拍的图片吗？
就放在文字描述之前的上面（与舞台预览一致的图片，但只有一拍，每当这一排有
修改时，重新刷新这一拍的图）"。

### 58.1 一个入口、一条渲染链

| 规矩 | 判据 / 说明 |
|---|---|
| 只算一拍：`app.stage.build_frame_at(beats, i, registry=, transform_index=, screen=)` | 与 `build_frames(beats)[i]` **逐字段相同**（`tests/test_stage_preview.py::TestBuildFrameAt`）。实现上只喂 `beats[:i+1]` —— 快照是前缀递推，截断安全。真机 2.2 ms（整章 `build_frames` 82 ms） |
| 渲染只有一份：`app.stage_render.render_frame(...)` | 卡片与「🎬 演出预览」调**同一个函数**、同一份 `load_gui_profile` / `transform_index` / `screen`。**不许**在卡片里另写一套"近似画法"（那就是"预览在骗人"） |
| 越界 / 空章 → `None` | 调用方据此显示"没有画面"并说原因；**不许编一帧假的** |
| 缩放（PIL，工作线程）到控件尺寸 | 不许把 1920×1080 搬过线程边界再让 Qt 缩（`QImage` 拷贝 17 ms，首调更贵）。目标宽 = 控件宽 × dpr，夹在 [480, 1280] |

### 58.2 `ui_qt.stage_thumb.BeatStageThumb` 的四条不变量

1. **换拍立刻清掉上一张**。不许学预览的"上一张顶着 + 角标"：卡片是"第 N 拍的
   快照"，画着第 N-1 拍就是错的信息。只有两个状态：这一拍 / 明说"正在渲染"。
2. **指纹决定重画**（`frame_signature`：index、line_id、label、speaker（含显示名
   与颜色）、text、scene（含 at）、立绘（图像名 / at / x / 是否在说话 / transform
   几何）、layers、music、sound、voice、with_、pause）。同一拍同内容**不重渲**；
   `id(frame)` 不能用（每次都是新对象）。
3. **迟到的结果只进缓存**（`_Gate` + 缓存键比对）：绝不许把上一拍的图贴到
   这一拍上；也不许为"已经翻走的那一拍"白算（工作线程开跑前先看门闩）。
4. **没有画面必须说原因**（`note()` 永远非空）：空会话 / 没选中 / 越界 /
   没装 Pillow / 渲染失败，各有各的话。缺素材时右下角「⚠ 缺 N 个素材」+
   tooltip 列名单。

异步/性能约定：**防抖 120 ms**、**两个渲染线程**、**LRU 12 拍**、
缓存命中时停掉排队中的防抖任务。真机：后台一拍 78~217 ms（首次大盘立绘
0.5~1.3 s），`_show_index` 不含等图（143~193 ms，与改动前同档）。

### 58.3 位置与"收起"的取舍（位置在 §60 改过一次，以 §60 为准）

- 图在「当前舞台（这一拍生效）」标题**下面**、与文字摘要**并排**（图在左，
  固定 288×162）；它所在的 `_stage_now` 区块**永远可见**（`_stage_box` 收起时
  也在，见 §60.1）。理由：真机一章 285 拍里 210 拍没有舞台内容（那几块收起），
  而那些拍的背景/立绘全是从上文**继承**来的 —— 收起时放弃这张图等于
  "最该看的时候没有"。
  判据 `test_it_stays_when_the_stage_block_is_collapsed` /
  `test_it_sits_under_the_current_stage_heading`。
- 「当前舞台」那行**文字**不变（还是 `stage_digest_text`），图在它**左边**；
  两者**同一时刻**刷新（`window_card.refresh_derived_panels` 里一次调两个），
  不许出现"图是新的、字是旧的"。
- 尺寸：`compact=True` → 固定 288×162（`COMPACT_W/COMPACT_H`）；`compact=False`
  才是"高度跟着卡片宽走"（16:9，`MIN_H 132` / `MAX_H 420`）。宽了留黑边，
  不许把图拉变形。
- 卡片面板的公共接口：`show_stage_frame(idx, session)` / `clear_stage_frame()` /
  `stage_thumb()` / `shutdown_stage_thumb()`（窗口关闭时清线程池）。

## §59 「可整块删掉的独立件」：顶部模式条的 ⭐ GitHub Star（2026-10-27 晚）

用户原话："再在（卡片编辑器）的稍稍右上方空白处去做一个 github 的快速 star 呢？……
我希望它是一个独立于整体界面的代码块，到时候方便取下来，不要耽误别人到时候对这个
界面的改造"。

这一节把"独立件"写成契约 —— 以后任何**加在界面上、但不属于产品主体**的东西
（star 徽标、赞助入口、公告条…）都按这套来，别人才敢改界面。

### 59.1 三条硬规矩

| 规矩 | 判据 |
|---|---|
| **一个文件**：控件 + 网络 + 降级全在 `ui_qt/github_star.py` | 它只许 `import` Qt / 标准库 / `ui_qt.tokens`（纯色表）；**不许** import `main_window` / `window_card` / `app.*` / `story.*` |
| **一处挂载**：`window_nav._build_mode_bar()` 里那三行 try 块 | 整个 `storygraph/ui_qt/` 里除它自己，只有 `window_nav.py` 出现 `github_star`；删掉那三行 + 那个文件 = 界面完全恢复（`test_only_the_mode_bar_mounts_it`） |
| **挂不上也不拦界面** | 挂载处 `try/except` + `swallow`。老 Qt / 颜色表缺 / 线程池起不来，最多少一个徽标 |

判据：`tests/test_github_star.py::TestItStaysDetachable`。

### 59.2 点击语义（每种出口都说人话；2026-10-30 按真机反馈改过）

| 情况 | 行为 |
|---|---|
| 有 token | `PUT https://api.github.com/user/starred/<owner>/<repo>`，`204` = 成功（**已标过也回 204**，幂等，不许当失败） |
| 没 token | **点一下立刻打开仓库页**（本机动作、不联网、不等探测）；后台自检只决定按钮补哪句提示 |
| 探测没通过 | 页面**照样打开**（浏览器走系统代理/PAC，往往能开），按钮补一句"网络自检没通过，浏览器里若打不开多半是代理/网络"。**探测结果不许挡着开页面** |
| 自动 star 失败（401/403/404/离线/限流） | 按钮上写一句原因（"token 无效或已过期" / "被 GitHub 限流了" / "仓库名对不上" / "连不上（…）"），并**退回打开仓库页**，几秒后自己恢复 |

- **探测判据**：拿到**任何 HTTP 响应**都算网络通（404/403/429/5xx 也算 —— 那是
  GitHub 自己回的话，说明 DNS/TCP/TLS/代理都活着）；超时 7 s、失败重试一次
  （同一条网络实测 5 次里有 1 次 >6 s，v1 的 2.5 s 必然误报）。
- token 来源优先级：`STORYGRAPH_GITHUB_TOKEN` → `GITHUB_TOKEN` → `GH_TOKEN` →
  `%LOCALAPPDATA%\StoryGraph\github.json` 的 `{"token": "…"}`（与 AI key 同一约定：
  不进项目、不进 git）。都没有 → 只开网页，**一次网络都不发**。
- 网络**一律在后台线程**（`QThreadPool`；API 8 s / 探测 7 s 超时）；`click()` 必须
  立刻返回（实测 21 ms），忙碌期间按钮置灰防连点；**没 token 时开页面在 `click()`
  里同步做完**，不等探测。
- **启动时不联网**：不探测、不拉 star 数、不留后台心跳 —— 只有人点才发请求。
  离线时它就是一块静默的装饰。

### 59.3 位置（第二版，用户当场纠正过）

**顶部模式条**（`QToolBar`，`objectName='modeBar'`）上、紧挨「✎ 卡片编辑」标签
**右边**（`tb.addWidget(badge)`，见 `window_nav._build_mode_bar`）。两页都看得见
—— 它属于"这个工具本身"，不属于卡片页的编辑区。

第一版挂在卡片页右栏布局第 0 行，被用户否掉了：
> 还有，github的star我不是叫你放卡片编辑的旁边吗？你应当放第四张图这里，
> 卡片编辑右边！放错位置啦！！！

判据：`test_the_badge_sits_right_of_the_card_tab`（模式条动作序列里
`徽标 = 标签 + 1`）+ `test_it_is_not_in_the_card_column`（`卡片页` 不是它的祖先）。

### 59.4 顺带记：这一轮**没能**推送 GitHub

远端 `origin` = `FengWenMo/RenPy-Story-Graph-Visual-Editor`，本地领先
`origin/main`（`45a2c79`）**88 个提交**；仓库本身已清到"可推"（1169 个文件 /
23.3 MB，最大单文件 1.29 MB，无 exe/zip/pyc）。推不上去的原因是这台机器的
`hosts` 把 `github.com` / `api.github.com` / `raw.githubusercontent.com` /
`*.github.io` 全指到了 `127.0.0.1`（868 条 `#S302` 屏蔽项），直连真实 IP 也超时。
应急件：`.pip-tmp/StoryGraph-main-2026-10-27.bundle`（`git bundle`，自带全部历史）。

### 59.5 许可证：**GPLv3**（三处必须同源）

仓库根 `LICENSE` 是 **GPLv3 全文**（与 GitHub 上 2026-05 那条线的初始提交
`1f7153b` 同一份 blob：35149 字节）。⚠ 2026-10-23 的 `0d32628` 曾把它写成
MIT（写错了，用户 2026-10-27 当场指出："不是 GPL 协议的吗？"），已改回。

| 位置 | 必须写 |
|---|---|
| `LICENSE` | GPLv3 全文（`GNU GENERAL PUBLIC LICENSE` + `Version 3`） |
| `pyproject.toml` | `license = { text = "GPL-3.0-only" }` + `license-files = ["LICENSE"]` |
| `README.md` | `## License` 段写 GPLv3，并说明 Qt 是 LGPLv3（**与 GPLv3 兼容**） |
| `packaging/交付说明.md` / `packaging/make_release.py` | 清单表里写 GPLv3（给人看的） |
| `tests/test_release_manifest.py` | `test_license_is_gpl_everywhere_it_matters` 守"三件一致" |

要换成 "或更高版本" 就统一写 `GPL-3.0-or-later`（README 那句也一起改）。



## §60 卡片右栏的**竖向配额**：定位折叠 / 288×162 的图 / 徽标在模式条（2026-10-27 深夜）

用户看完第一版截图后一次点了三处（原话）：

> 不对！！！你这个不能放卡片界面啊！！！应该缩小放舞台界面！当前舞台下面那一栏……
> 缩小！再缩小，直到能看到全貌！不然你现在显示都显示不全何来调整啊？？？
> 还有就是这个定位能不能折叠一下（卡片界面才是最主要的！！！定位默认折叠起来，
> 卡片界面必须留几乎右边全屏）……留给我的可编辑窗口太小了！我看不过来！

这三句其实是同一条规则的三种说法：**右边这一栏的竖向空间，优先给"能改的东西"**。
§60 把这条写成可判的配额，以后往右栏加东西先算这笔账。

> **2026-10-30 ⑤ 追加**：卡片里那张小图收窄到 **240×135**
> （`main_window.CARD_STAGE_THUMB`，仍是 16:9；`stage_thumb.COMPACT_W/H` 组件
> 自身仍是 288×162），立绘网格列数改为随宽度自适应 **1~4 列** —— 下面
> 60.1 / 60.2 / 60.3 里写的 288×162 是当时的形状，数值以这句为准。

### 60.1 三条配额

| 谁 | 占多少 | 能不能收 | 判据 |
|---|---|---|---|
| 「▸ 定位（这一拍在文件里的位置）」 | 收起时 **1 行**（一个 chip），展开时 6~7 行 | **默认收起**，点一下展开；`PropertyStrip` 一直在、照旧同步 | `test_location_block_starts_collapsed` / `test_toggling_it_shows_the_real_rows` |
| 「当前舞台」+ 这一拍的图 | 标题 1 行 + **一行**（图 288×162 在左、文字摘要在右） | 普通拍**一直可见**；**控制流卡收起**（`call` / `jump` 没有演出） | `test_it_sits_under_the_current_stage_heading` / `test_the_picture_is_a_small_whole_frame` / `test_ui_fit_and_flow.py::TestFlowCardHidesDialogue` |
| 立绘网格 / 背景 / 音乐 / 语音（`_stage_box`） | **一直显示**（2026-10-29 起，见 §71.1） | 普通拍**常显**（这一拍没有舞台改动也不收）；**控制流卡收起**（`call`/`jump` 没有演出） | `test_a_beat_without_stage_content_still_shows_the_stage_block` |

**为什么"定位"能收、"当前舞台"不能**：定位是"我在文件的哪儿"，回答一次就够
（模式条右边还一直写着 `ch01.rpy · ch01 · 第 1 拍`）；而"这一拍台上是谁 / 在哪 /
什么曲子"是**每拍都要重新看**的东西，且 285 拍里 210 拍没有舞台改动 ——
它一收，那些拍就什么都没有了。唯一的例外是**控制流卡**（`call` / `jump`）：
那里既没有台词也没有演出，图和摘要一起收起（`set_flow` 里显隐）。

### 60.2 卡片根布局的顺序（改顺序要同时改这三处的判据）

```
语句条 / 参数
说话人 · 台词 · 字数 · 富文本预览 · 台词后缀
当前舞台（这一拍生效）
├─ 标题
└─ [ 288×162 的图 | 文字摘要（stage_digest_text） ]     ← `_stage_now`（控制流卡收起）
立绘网格 · 背景 · 音乐 · 语音                            ← `_stage_box`，普通拍常显（§71.1）
备注 · 条件 · 高级区 · 保存/撤销 · 提示条
```

- 图在**摘要左边**、同一行：摘要本来就要占那几行，图因此几乎不吃额外高度；
- 图是 **288×162 固定小图**（`stage_thumb.COMPACT_W/COMPACT_H`），
  整帧一眼看得完 —— "看得全"比"看得大"重要，因为看它是为了**决定改什么**；
- 点开「定位」不会把卡片挤走：它插在卡片上方的 `rv` 里，卡片自己在一个
  `QScrollArea` 里（`stretch=1`），高度不够时滚的是卡片，不是窗口。

### 60.3 实测（1760×1000，真机 `sr_general/chapter1_4.rpy` 285 拍）

- 定位收起：右栏从「▸ 定位」一行直接接「卡片界面（这一拍）」，卡片一屏装下
  说话人 / 台词 / 当前舞台（图+摘要）/ 立绘 / 背景 / 音乐 / 备注 / 条件 / 高级区 /
  保存撤销，不必滚；
- 图 288×162，渲染目标仍是 480×270（`MIN_TARGET_W` 下限），后台 674 ms 首帧；
- 模式条那一行：`🗺 剧情结构 │ ✎ 卡片编辑 │ ★ Star on GitHub`，右边
  `chapter1_4.rpy · chapter1_4 · 第 1 拍`。

### 60.4 判据清单

- `tests/test_stage_thumb.py::TestCardWiring`：图在「当前舞台」标题下面、
  摘要左边；是 288×162 的小图；舞台块收起时它还在；翻拍 / 保存 / 空会话照旧；
- `tests/test_github_star.py::TestModeBarWiring`：徽标在模式条上、紧跟
  「卡片编辑」标签（动作序列 +1）；不在卡片页里；删掉它之后界面照常；
- `tests/test_ui_nav_layout.py::TestWindowWiring::test_inspector_shows_where_this_shot_lives`
  （收起与否，`PropertyStrip` 的内容一直是这一拍的）；

## §61 三条"静默失败"的老口子（2026-10-27 深夜，审计复验后修）

三处都是同一类病：**"没做"与"做了、结果是没有"在产物里长得一样**。
这一节把修法与判据写下来，避免下一个人再按老形状加代码。

### 61.1 重号只许动重复的那一条（`line_identity.unique_ids`）

| 规矩 | 判据 |
|---|---|
| 发号底座 = **整批已有的 ID + 本次已发出的**，不是"已经扫过的那几条" | `test_the_duplicate_does_not_steal_a_later_rows_number`：`[0001, 0001, 0002]` 必须得到 `[0001, 0003, 0002]` |
| **输入里本来就唯一**的 ID，一个都不许换（R5/R6/R7 的"其余行不变"） | `test_renumbering_never_cascades_to_innocent_rows`（5 种顺序） |
| 别为了"不级联"退化成同号 | `test_a_duplicate_still_gets_a_number_nobody_uses` |

> 为什么以前没抓到：唯一的重号用例把重复放在了**最后一行**（`['a.0001','a.0002','a.0001']`），
> 级联恰好不发生。**判据的输入顺序也是判据的一部分。**

### 61.2 `derived` 段必须有交代（`core/engine_facts` + `pipeline/engine_collect`）

`facts` 段早就有三态：**有值（带 source）/ 缺口（gaps 带 reason）/ 结构不合格**。
`derived` 段现在一样：

| 状态 | 文件里长什么样 | 判据 |
|---|---|---|
| 算出来了 | `derived: {overridden_builtins: […], undeclared_heads: […], scan_disclaimer: …}` | `TestDerivedSection` |
| **没算出来** | `gaps: [{'key': 'derived.overridden_builtins', 'reason': '扫描器出错…'}]` | `test_a_gap_explains_the_missing_key` / `test_scanner_crash_becomes_a_gap_not_an_empty_list` |
| 说采到了却空着 | `collection.parse_clean/probe_complete = True` + `derived: {}` | `test_missing_derived_keys_are_reported_when_collected`（**报**） |

- `DERIVED_KEYS` 只许列**采集器真的会写**的键。`group_conflicts` 曾在这里，但它是
  查询（`group_conflicts(facts, tag, attrs)` 现算），已删 —— 声明里挂一个永远不写的键，
  等于教人去找不存在的东西。
- `derived` 里出现未知键 → **报**（与 `facts` 段同一条规矩）。
- 派生段的缺口与 facts 的缺口**共用 `gaps` 一张表**，键写成 `derived.<名字>`
  （`EF.derived_gap_key()`）；`_derived_*` 两个函数返回 `(rows, problem)`，
  `problem` 非空就写缺口 —— **一半结果照样写出去**，只把"没算完"说出来。

### 61.3 写入口探针：两种 `open` 写法都要看见

| 规矩 | 判据 |
|---|---|
| `open(p, 'w')` 与 `open(p, mode='w')` 都算落盘 | `test_the_probe_sees_both_spellings_of_a_write` |
| **写进流不算落盘**：`fileobj=` / `file=`（如 `tarfile.open(fileobj=gz, mode='w')`，打包进 BytesIO） | 同一条用例的"必须不抓"那一半 |
| 新增落盘点必须登记 + 写清写的是什么 | `test_every_write_site_is_accounted_for` |

### 61.4 `_FACTORY_FIELDS` 必须含**可选** payload 键

只在"真有"时才进 payload 的键（`say.tail`、`menu.extras/name/args`、`call.args`、
`return.value`）也要声明，样例必须**把可选参数传上** —— 否则"漏声明"永远不会变红：
`test_optional_payload_fields_are_declared_too` + `_samples()` 的注释。

## §62 发布包：**"能打开" ≠ "看得见"**（2026-10-27，第 N 次重打包）

这一节管的是"发给别人的那个 zip 长什么样"。判据都在
`tests/test_release_manifest.py`（10 条），清单在 `packaging/make_release.py`。

### 62.1 清单：少一样就构建失败（不是警告）

| 必须有 | 为什么 |
|---|---|
| `StoryGraph.exe` / `StoryGraph-CLI.exe` + `_internal\` | 双击就能开；CLI 给批量 / CI |
| `剧本空表格.xlsx` | 空 Beats 表 + 「列说明」页；**必须走真实导出器**生成（自己拼表头 = 填了也导不回去） |
| `demo\`（**不带** `collab.db` / `_import_tmp` / `build\`） | 第一次打开"有东西可开"；本机数据与产物不进包 |
| `快速上手.md` | 1 页速查（`docs/快速上手_3分钟.md` 的发布版） |
| `新手快速操作手册.md` + `manual_img\` | 完整手册 + 它引用的两张截图 |
| `LICENSE` | GPLv3（见 §59.5） |

⚠ **手册里的截图也得进包**：md 用相对路径 `manual_img/xx.png` 引用，包内目录布局
与 `docs/` 一致。少了图，用户看到的是两个破图 —— 比没有手册更糟，所以
`collect_extras` 直接 `FileNotFoundError`（`test_missing_manual_shots_fail_loudly`）。

### 62.2 demo 的图片必须是**真 PNG**（2026-10-27 实测的 P0）

以前 `demo/make_demo.py` 写的是 **48 字节假 PNG**（`\x89PNG` 文件头 + 40 个 0）。
资源表、体检、导出全绿 —— 因为它们只问"文件在不在"；而**任何读像素的地方**
都读不出：卡片页舞台图一片黑 +「⚠ 缺 1 个素材」、立绘卡写「读不出」、
演出预览降级成方块。第一次打开 demo 的人看到的就是这些。

| 规矩 | 判据 |
|---|---|
| `game/images/**` 的图必须是能解码的真 PNG | `demo/make_art.py`（Pillow 画占位图：背景 + 立绘，带名字，明确写"不是美术资源"） |
| 改 `demo/` 的素材生成方式时，**资源表不用重扫**（表里没有尺寸/哈希列），但**包里的图要重新生成** | `make_release` 的 `REQUIRED` 里有 `demo/game/images/bg/bg_classroom.png` |
| 占位音频仍是假字节（只查存在性、不播） | 真 ogg 要编码器，不在生成脚本的职责里 |

> 教训：判据问的是"存在"，用户看的是"画面"。凡是**给第一次打开的人看的东西**，
> 判据要问"能不能渲染出来"。

### 62.3 截图脚本的三条硬规矩（`tools/_shot_manual.py`）

| 规矩 | 为什么 |
|---|---|
| `QT_QPA_PLATFORM=offscreen` + `STORYGRAPH_NO_SESSION=1` | 无显示器也能跑；**不写**用户的 `session.json` |
| 把 `demo/` 拷到临时目录再打开 | `📁 打开项目` 会往项目根写 `storygraph.session.json` —— 截图不能有副作用 |
| **不许 `win.close()`**，结尾 `os._exit(0)` | 导入过章节 = 有"未导出"改动 → 关窗弹「保存 / 不保存 / 取消」**模态框**，无人点击就永远卡住（实测 45 s 超时） |

截图内容：`01_structure.png`（剧情结构页，200% + 居中，别用 `fit_to_content` ——
只有一个块时会放大到 300%+，一块顶满屏幕看不出这是画布）、
`02_card.png`（卡片页第一拍，舞台小图要 `_pump` 等后台线程画完）。

## §63 「接线」只有一个"能不能写"的判据（2026-10-27 真机）

### 63.1 一条线要过两个判据 —— 它们必须同源

| 位置 | 作用 | 从前 | 现在 |
|---|---|---|---|
| `dialogs.AddEdgeDialog._on_accept` | 决定「创建」按下去是"写入"还是"画草图" | `ok or kind == 'control_init'` | `wire_plan_writable(plan)` |
| `window_nodes._write_dialog_edge` | 真正执行写入 | **只看 `ok`** ← P0 | `wire_plan_writable(plan)` |
| `window_nodes.write_manual_edge` | 草图右键「写入剧本…」转正 | 只看 `ok` | `wire_plan_writable(plan)` |
| `window_nodes.build_edge_menu` | 右键「写入剧本…」是否可点 | 只看 `ok` | `wire_plan_writable(plan)` |

`wire_plan_writable()` 在 `storygraph/story/control_route.py`，是**纯函数、无 Qt**：

```python
wire_plan_writable(plan)  ⇔  plan['ok'] or plan['kind'] == 'control_init'
```

| kind | `ok` | 能写 | 写到哪 |
|---|---|---|---|
| `control` | True | ✔ | 编排层 `X_control`（替换 `return` / `jump`） |
| `chapter_end` / `beat` | True | ✔ | 章节源文件（内存；导出才落盘） |
| `control_init` | **False** | ✔ | 先补目标章 `B_control` 包装，再把路由接上去 |
| `control_blocked` / `not_loaded` / `bad_position` / `stale_position` / `ambiguous` / `cannot` | False | ✘ | 退回草图，或当场说明原因 |

⚠ `control_init` 的 `ok=False` **是有意的**（它还没写），不是"写不进"。
任何把 `ok` 当"能不能写"的新代码都是同一个 bug 的第 N 次复发。

### 63.2 判据

| 规矩 | 判据 |
|---|---|
| `control_init` 算能写（真值表） | `tests/test_control_init.py::test_wire_plan_writable_treats_control_init_as_writable` |
| 对话框说能写 ⇒ 窗口必须真写出边 + 包装 + 路由 | `test_creating_a_line_to_a_wrapperless_chapter_really_writes` |
| 真写不进去时：弹框 + 不加边（不许静默假完成） | `test_write_dialog_edge_still_refuses_a_blocked_plan` |

### 63.3 失败的**可见性**：状态栏不够

`_write_dialog_edge` 失败时必须 `QMessageBox.warning`。理由是这次真机现场：
对话框正常关闭、剧本没改、图上没线，只有状态栏一行字 —— 用户连续失败十几次
都没意识到"每次都没成功"，反而以为"线画上了但看不见"。

### 63.4 空图必须解释自己（`_report_no_flow`）

| 情况 | 说什么 |
|---|---|
| 图上有 ≥2 个块、**0 条线**、项目有编排层且未导入 | 点名那个 `.rpy`，给出「补全缺失章节…」+「Shift+左键自己接」两条路 |
| 同上，但编排层已导入 | 说"它的 `_control` label 和图上章节对不上"，不重复建议导入 |
| 项目没有编排层 | 只给"自己接"那条路，**不许**编一个文件名出来 |
| 图上有线 / 只有 1 个块 | **一句都不说** |

判据：`tests/test_graph_no_flow_hint.py`（5 条）。
为什么需要它：`_report_missing_chapters` 只找"被 jump 到却没导入"的文件；
章节之间**根本没有 jump/call** 时它无话可说，用户只能猜（真机 7 章全是 `return`）。

## §64 画布的"手势状态"：光标、自动滚（2026-10-27）

画布上有三种"正在进行的操作"：**平移**（空格/中键）、**连线**（Shift+拖）、
**拖块**（左键拖）。它们在拖动中会改光标、会滚视口，所以状态必须"进得去、出得来"。

### 64.1 光标：只有一个写入口，且有自愈

| 规矩 | 为什么 |
|---|---|
| 只许 `GraphCanvas._set_cursor(shape)`，它同时写 `viewport()` 与 `self` | `QAbstractScrollArea` 的显示面是 viewport；只写外层在部分平台/版本上只生效一半 |
| 每个手势出口都调 `_restore_cursor()` | 出口 = `mouseReleaseEvent` / `_finish_connect` / `cancel_interaction` / `keyReleaseEvent` |
| `_idle_cursor()` = 空格按着 ? 张手 : 箭头 | 空格还按着时恢复成箭头，等于"提示用户空格还按着"这件事没了 |
| `_heal_cursor()` 只在**无手势**且光标是 `ClosedHand` / `Cross` 时复原 | 用户 2026-10-27："拖动结束后鼠标没有变回来"。释放事件可能根本到不了画布；状态与光标不一致时，鼠标下次经过就修好 |
| 悬停连线手型（`PointingHand`）**永远不碰** | 那是 `QGraphicsScene` 设的；碰了就等于"线点不动" |
| `set_space_down` 不做"状态没变就早退" | 它是唯一知道空格按着没有的地方；早退会让状态与光标越差越远 |

判据：`TestCursorNeverSticks`（5 条，含"release 丢了也能自愈"）。

### 64.2 拖块贴边 → 视口自动滚

| 参数 | 值 | 说明 |
|---|---|---|
| `DRAG_SCROLL_MARGIN` | 44 px | 光标进入视口四边这么多就开始滚 |
| `DRAG_SCROLL_INTERVAL` | 16 ms | ≈60fps |
| `DRAG_SCROLL_MIN` / `MAX` | 2 / 22 px 每拍 | 速度 = `max(MIN, MAX * (depth/margin)^2)` |

两条硬规矩：

1. **块要跟着视图走**：滚动条走 `N` 像素 = 场景走 `N/scale`，块按同样的场景距离挪。
   判据不是"滚动条动了没有"，而是**块在屏幕上的位置不变**（<=2 px）——
   符号写反、或忘记除 scale，滚动条都会动，但块会飞出去。
2. **滚不动就不滚，不许撑舞台**：`_extend_scene_rect()` 会 `centerOn` 重排视口，
   缩到 0.43 时实测把画面顶跑 376 px。舞台由拖完之后的
   `_on_node_moved → note_content_changed()` 按内容撑大。

停表条件：松开（`end_node_drag`）、移出边缘（`note_drag_cursor` 里 delta 归零）、
换图（`set_graph`）、窗口失活（`cancel_interaction`）、滚到边界（`moved == 0`）。

判据：`TestDragNearTheEdgeAutoScrolls`（7 条，含放大 200% 与"没得滚时不许动"）。

## §65 编排层以「终局语句」收尾 ⇒ **改接**，不是画草图（2026-10-27 真机）

真机原话："不对！为什么只能生成草图……？我明明没有用草稿"。
起因是 `chapter_controller.rpy` 里最后一章的包装（旧版编辑器写的兜底）：

```
label chapter1_4_control:
    call chapter1_4 from _call_chapter1_4
    $ renpy.full_restart()
```

### 65.1 判据表（`story.control_route.plan_control_route`，`jump` 边）

| 编排层块内最后一条 | 计划 | 说明 |
|---|---|---|
| `return` | `replace` | 换成 `jump B_control`（一直如此） |
| `jump X` | `replace` | 改接（一直如此） |
| `call X` | 插入在它之后 / 前面按 etype | 一直如此 |
| `menu:` / `if …` / `else:` | **blocked** | 分支归卡片，不往块体里插 |
| `jump expression …` / `$ renpy.jump(x)` | **blocked** | 动态跳转，判不出目标 |
| **`$ renpy.full_restart()` / `$ renpy.quit()` / `$ renpy.jump_out_of_context(…)`** | **`replace`** | **终局语句**：它自己就是"到此结束"，插什么都执行不到 → 只能替掉 |
| 别的 `$ …`（如 `$ persistent.seen = True`） | **blocked** | 替掉它 = 删掉作者写的一行；仍"判不出安全的插入点" |

`call` 边不替换：插在终局语句**之前**（call 完回来，再结束，语义不变）。

### 65.2 "终局语句"只有一个定义

`story.exits.terminating_call(code)`（= `_terminate_code` 的公开别名，认
`renpy.full_restart(` / `renpy.quit(` / `renpy.jump_out_of_context(`）。
`control_route._terminating_of(line)` 只负责把行首 `$` 剥掉再问它。

**不许**在接线这边另抄一份前缀表：`exits.py` 已经把这些归成 `EXIT_TERMINATE`
（"没有下一步"），两处不一致就会出现"出口分析说是终点、接线说是判不出"这种自相矛盾
—— 那正是这次 bug 的成因。

### 65.3 对话框：按钮文字 = 按下去会发生什么

`AddEdgeDialog._sync_ok_button()`，每次落点变化（含勾/取消「草稿」）都重算：

| 情况 | 按钮 | 悬停提示 |
|---|---|---|
| `wire_plan_writable(plan)` 为真 | 「创建」 | 按「插入位置」真的写进剧本 |
| 为假 | 「只画草图」 | `写不进剧本：<plan.reason>` |
| 勾了「草稿」 | 「画草稿」 | 只画线、不写剧本，以后可转正 |

另外：`plan` 为空（`plan_fn` 抛异常 / 没给）时 `plan_hint_text()` **不再返回空串**，
而是明说"算不出这一条会落在哪里 —— 按「创建」只会画一条草图"；改接 `jump X` /
终局语句时提示里点名"原来那一条「…」会被换掉（Ctrl+Z 可放回）"（`return` 不提示，
那是常规操作）。

判据：`tests/test_control_route_terminal.py`（12 条）。

## §66 「到不了」≠「写不进」：断裂的局部也必须能连（2026-10-27 真机）

真机原话（同一句抱怨第二次）：

> 2_4 到 2_4_1，我明明没有勾选草图！为什么还是只能画草图？？？再查！
> ……总之我要 45 章全部都能连起来（就算是断裂的局部也能不画草图直接连）
> ……不需要你替我连，但你要能做到这个效果！草图想什么时候用就什么时候用。

### 66.1 唯一的判据：**能原子地写一行，就写**

接一条线（章末 → 编排层）会不会落成草图，只看"这一行写不写得进去"。
**不看**这一章在游戏里到不到得了 —— 那是两件事：

| 条件 | 结果 |
|---|---|
| 源章 / 目标章缺 `_control` | **写**：先补包装（`apply_init_chain`，两头都缺也一次补完），再写路由 |
| 源章现在**没有任何地方进入** | **写**，`reachable=False` + `notes` 说明"暂时到不了，接上入口就生效" |
| 源章现在是被 `jump X` **直接**进入 | **写**，`notes` 说明"要把那些入口改接到 `X_control` 才会执行" |
| 源/目标 label 不存在 | 不写（`找不到 label X`）—— 没有 label 就没有地方挂路由 |
| `label X(params)` 带参数 | 不写 —— `call X(...)` 的参数不能猜 |
| 项目里一个 `_control` 都没有 | 不写 —— 那是"新建编排层"，归「接管进入点」，不是接一条线 |

最后三条是**真·写不进**；"暂时到不了"从来不是。

### 66.2 一次落盘：`apply_init_chain(plan, replan)`

`story.control_route.apply_init_chain` —— 补包装 → `replan()` → 再补 → 直到
`plan.ok`（最多 `INIT_CHAIN_ROUNDS=3` 轮）。返回
`{(ok, plan, error, snapshots=[(path, 补之前, 补之后)])}`；回滚走
`rollback_wrapper_writes(snapshots)`（只退"还是自己刚写的那一版"的文件）。

`plan.init` 的语义统一为"**接线前要先落地的那一步**"：源章缺包装时它是
`src_init`（`role='source'`），目标章缺时是 `role='target'`。命令层不再分两套。

⚠ `ControlRouteCommand.execute()` 开头必须**按同一份输入重算计划**：redo 时
`self.plan` 是上一次"补完包装"的结果（`ok=True`），而文件已经被 undo 回原样 ——
照旧计划写会跳到根本没补出来的 `_control`。

### 66.3 界面必须说清"写了，但现在没通"

| 位置 | 文案 |
|---|---|
| 对话框 `plan_hint_text()`（`kind='control_init'`，`role='source'`） | "先给「A」补编排入口 `A_control`（`call A` / `return`），再把出口接到「B」" + "⚠ 这一章现在还没有入口 …等接上就生效 —— 现在写进去，不用先画草图占位" |
| 状态栏（`_apply_control_route`） | `✔ 已接到编排层（…章节剧本一个字未改；顺手补了 `A_control` 包装）；Ctrl+Z 可撤回` + `\n⚠ <init.notes[0]>` |
| 按钮（`_sync_ok_button`） | 见 §65.3；没勾「草稿」且能写 ⇒ **「创建」** |

「草稿」复选框仍是**唯一**的"只画线"入口。

### 66.4 分支（条件 / 菜单项）走另一条路

`plan_control_branch` 里源章缺包装仍然 `blocked` —— 但调用方
`_add_control_branch` 会先弹「接管进入点」（`plan_takeover`：补包装 + 把全项目
`jump X` 改接 `jump X_control`，后者连"一个入口都没找到"都只是补包装 + 一行
note）。别把这两条路合并：分支要的是"分支真的会被执行"，接管才是那个修法。

判据：`tests/test_wire_disconnected.py`（13 条）。真机副本扫地：43/43 能当源、
43/43 能当目标、编排层里已有的 18 个 `_control` 18/18 能改接。
## §67 布局要折进**舞台** —— 但两种折法（2026-10-27，两轮）

用户原话（第一轮）：

> 自动布局的逻辑不太行……仍然需要修改（因为现在有些太长了，你应该使得
> 尽可能多的标签整齐排列在舞台内部），而不是一路长排下去……

第一轮把两条路都折成了"格子"，第二轮被否掉一半：

> 这个自动布局不好！！！因为无法体现结构……你还是改回去吧（原来那个比现在
> 要好得多，因为展现了结构流程图）我的意思其实是：能不能在保持尽量横向编辑
> 的流程的同时，自动实现感知到头就拐弯呢……？（分叉一类的还是并排放）

### 67.0 一句话：舞台有边，折行必须有；但"怎么折"取决于那一列代表什么

| 那条路 | 一列代表 | 折法 |
|---|---|---|
| `layout_by_file`（▤ 按文件顺序排列） | 一个 `.rpy` 里的**一段** | **像排字**：排满一列换右边、排满一页换下面（`_flow_into_pages`） |
| `layout_graph`（⤢ 自动布局） | **一层**（"谁跳到谁"的那一层） | **层还是左右排开**，排到右边才拐到下面一排、下一排反着走（`_flow_layers_into_bands`） |

为什么自动布局不能按格子平铺：**一列 = 一层是这个视图的全部意思**。平铺之后
"谁跟谁是同一层"看不出来 —— 真机 15 层被摊成 6 行 × 3 列，用户当场否掉。

### 67.1 舞台 = 画布可视区，是布局的**输入**

`GraphCanvas._stage_box()` 返回 `(宽, 高)`（场景像素，100% 缩放下 1:1），
两个排版入口都把它传给 `layout_graph(..., stage=…)` / `layout_by_file(..., stage=…)`：

| 取什么 | 为什么 |
|---|---|
| `viewport().rect()` | 回答的是"一屏摆得下多少块"。`sceneRect` 是无边界的（无限舞台按需生长），拿它当舞台等于没边 |
| 太小（`< 320×240`）→ `DEFAULT_STAGE`（1280×720） | 还没 `show()` 过 / 被压扁时 `viewport()` 是脏值；无界面测试也靠这条有确定舞台 |

`stage=None`（默认）**不折行** —— 老行为逐字节不变，纯数据调用与老用例走这条。
两者是两件事：`stage=None` = "我不要折行"，`stage=(0,0)` = "盒子不合法"（退回默认）。

### 67.2 自动布局：**一层一列，到头拐弯**（`_flow_layers_into_bands`）

`cols = 舞台宽 // 层距`（至少 1），层按 `0,1,2,…` 依次放进"排"：

| 规则 | 结果 |
|---|---|
| 排满 `cols` 层 | 换到**下面一排**，方向**反过来**（第 2 排列 3、4、5 从右往左） |
| 每排 | 自己**竖直居中**（和老排法同一条规矩：同一层共用一条竖线，分叉汇合对称） |
| 排高 | 这一排里最高那一层（层高 = `(块数-1) * 行距`），排间留 `BAND_GAP_RATIO=1.5 × 行距` |
| 最后一排不满 | **按 `cols` 定位**，不按 `len(band)` —— 否则单层那排被摆到最左边，拐弯那条边横跨整排（实测 layer 14→15 从 x=564 甩回 x=0） |

**蛇形不是装饰**：上一排最后一层和这一排第一层落在**同一列**，拐弯那一步就是
一小段竖线；换成"回车式"（每排都从左往右）的话，每次换排都要横跨整排再折回来。

返回值 `info = {'bands','cols','layers','tail'}`；`describe_bands(info)` 念成
"15 层排成 5 排（每排 3 层，排到右边就拐下来，下一排从右往左）"。

### 67.2b 按文件顺序：**像排字**（`_flow_into_pages`）

`_stage_grid` 算出 `cols = 宽 // col_gap`、`rows = 高 // row_gap`（各至少 1），然后：

| 规则 | 结果 |
|---|---|
| 一列排满 `rows` 块 | 接着排右边一列 |
| 右边排满 `cols` 列 | 换到**下面一页**（页高 = `rows` 行，所以往下滚就是下一页） |
| 一列比一页还高 | `_split_strips` 切成 ≤ `rows` 行的**段**，接着排下一列（真机 `chapter_controller.rpy` 22 个 label） |
| 奇数列 | **蛇形**：段**自下而上**堆（上一列结尾落在这一列结尾旁边）；**段内仍然从上往下** |

⚠ 段内顺序不许翻：整列连内容一起翻，一个 3 个 label 的文件在奇数列就读成倒序 ——
而"从上到下就是这个文件里的先后"是「按文件顺序排列」唯一的承诺。

返回值 `info`：`{'grid': (cols, rows), 'pages': N, 'strips': M, 'tail': (x, y)}`；
`describe_wrap(info)` 把它念成"每列最多 6 块、一页 3 列，共 5 页（排满往右、再满了
往下）"。`layout_by_file` 的摘要多这三个键（`columns` 仍然是**文件数**）。

### 67.3 界面上要看得到的三件事

| 位置 | 行为 |
|---|---|
| 状态栏（自动布局） | `GraphCanvas.layout_arranged(str)` 信号 → `MainWindowQt._status`：`⤢ 已按跳转重排：15 层排成 5 排（每排 3 层，排到右边就拐下来，下一排从右往左）` |
| 状态栏（按文件顺序） | `▤ 已按文件顺序排好：… —— 列内从上到下就是这个文件里的先后；每列最多 N 块…` |
| 控制台 | 不再说"第 N 列"（折行后一个文件不一定占一整列），改说 `1. xxx.rpy：…` |

### 67.4 装不下时从**左上角**开始看

`_reset_view`：装得下（缩放 ≥ `FIT_MIN_SCALE=0.70`）就 `fitInView`；装不下就
100%，并且

- 算过入口（`_entry_id` 有值）→ `center_on_node(entry)`（"从哪开始"更重要）；
- 没算过 → `_align_top_left()`：把**内容左上角**对齐到视口左上角（留
  `FLUSH_MARGIN=32`）。

为什么改：折行之后入口块本来就在左上角，摆在正中等于上面空掉半屏（实测
990×847 空掉 340 px，一屏只看到 4 排）。

### 67.5 判据

`tests/test_layout_stage_wrap.py`（30 条）：

- **按文件顺序**：一页排满才换页 / 页往下不往右 / 同文件的 label 同列正序 /
  22 个 label 分段后按列读仍是文件顺序；
- **自动布局**：宽不超舞台 / 一层仍是一列 / 分叉同列且相邻 / 拐弯两边落同一列 /
  最后一排不满也对齐 / 排间留得比列内宽 / 舞台越宽排数越少；
- **两条路共有**：`stage=None` 保持老布局（含竖直居中）/ 坏盒子退回默认 /
  块不重叠 / 画布真的把可视区传下去 / 装不下时对齐左上角 / 两套摘要文案。

## §68 Excel 装不下的控制字符：**剥掉、但留痕**（2026-10-28）

xlsx 是 XML 1.0，有一批字符**根本存不进去**：`U+0000~U+0008`、`U+000B`、
`U+000C`、`U+000E~U+001F`（`\t` `U+0009` / `\n` `U+000A` / `\r` `U+000D` 合法）。
`openpyxl` 遇到它们抛 `IllegalCharacterError`，而那**不是"这一格写不进去"** ——
是**整份工作簿都写不出来**。

真机（2026-10-28）：用户项目 `chapter3_3_3.rpy` 第 79 行的台词里有两个
`U+0005`，于是整个项目的剧本表**一行都导不出来**，用户看到的是 openpyxl 的
英文 traceback。五万行里的一格，把项目卡死。

**判据（唯一一份实现在 `storygraph/excel/cells.py::xml_safe`）：**

1. 只剥上面那批**规范明令禁止**的字符；`\t` `\n` `\r` **必须原样保留**
   （多行台词与缩进靠它们，剥了就是毁内容）；
2. 落点是 `set_text` —— 所有写表路径的最后一公里（`append_text_row` /
   `set_row` / 各导表函数都过它），不是某一处的补丁；
3. **每剥一次都进 `core.safeguard`，并带上坐标**（`Beats!R61C7`）——
   诊断面板的「静默失败」看得见。剥是让步（Excel 没有它的表示法），
   但"悄悄改用户的数据"仍然不允许；
4. 非字符串（数字 / `None`）原样返回，不参与。

判据：`tests/test_excel_illegal_chars.py`（19 条：哪些剥、哪些不剥、留痕坐标、
端到端能导出能读回、`\n\t` 往返不变）。

> 顺带一条**金标口径**：金标钉在活的真机工程上，"红了"先跑
> `tools/golden_baseline.py --check` 分清"项目变了"还是"工具变了"。
> 项目侧真变了（2026-10-28：`.rpy` 55 → 96）时，**逐条确认**新输出无误后
> `--save` 重生基线、再用对应的 `tools/probe_*_fingerprint.py --save` 刷新金标。

## §69 跨章连续性：后继单入边就判（2026-10-28，两轮澄清）

**上一章留下的背景 / 立绘 / CG / BGM，下一章第一句台词时还在不在？**
单章预览与项目体检都看不见这一类问题（各自只看一章/只看语法），所以单起
一个**只读**分析。最终判据（用户第二轮澄清）：

> 本意就是只要节点"单入边就判、不管前驱出边"。

### 69.1 判据（最大程度判，但不出错）

| 结构 | 判不判 |
|---|---|
| 线性章节（单入边） | ✅ 判 |
| 分支点（多出边） | ✅ 判它**自己的入口**（若单入边）；从它分出去的**每个分支章**（单入边）也判 —— 继承同一份章末状态 |
| 分支后的第一章 | ✅ 判（理由同上：分支点出口状态是确定的） |
| 汇聚点（多入边） | ❌ 不判汇进来的接缝；各前驱章末状态**列事实**，不合并/不排序/不猜 |
| 环 / 动态跳转 / Python 音频 / 未建模声道 / 旁支 / 无台词 | ❌ 不判，进「不判定（不猜）」清单 |

状态推导与预览/快照**同源**：`story.stage_snapshot.stage_exit_state(beats,
initial_state)`；`initial_state['unknown']` 集合表示"不知道"，与"空串 = 静音 /
空背景"严格区分。汇聚点**不做 join**（哪怕各前驱恰好一样也按未知）。

文本报告里"同一来源 + 同类问题"的分支子章会压成一行"细节同上"；
JSON / 可判接缝清单里**逐条都在**（最大程度保留详细信息）。

### 69.2 API（`storygraph.pipeline.continuity`，纯逻辑、不 import Qt/图）

| 符号 | 说明 |
|---|---|
| `analyze_project(root, files=None)` | 整项目只读分析 → `ContinuityReport`；坏文件进 `errors` 不中断 |
| `analyze_parse_result(parse_result, project_root='', files=None)` | 吃已解析 IR（与表/图同一份），测试用 |
| `analyze_files(paths, project_root='')` | 指定文件分析（CLI `--file` / 测试） |
| `project_source_signature(root)` / `source_signature(paths)` | 源文件指纹（mtime_ns + size），UI 缓存失效判据 |
| `entry_by_file(report)` / `entry_for_file(report, path)` | 每个 `.rpy` **开头那一章**的入口状态（`ChapterEntry`：`state` + `why` + `unknown`）；结果缓存在报告对象上（2026-10-31） |
| `ContinuityReport` | `.to_dict()` / `.render_text()` / `.stats()` / `.chapter_map()` / `.judged_seams()` |

`ContinuityReport.chapters`（`Chapter`）：`entry/exit`（状态 + `unknown` 集合）、
`in_edges/out_edges`、`root/side/cycle/chain_end`、计数（音乐/舞台/台词）、
`role`（链根/分支/汇聚/链尾/旁支）。
`Seam`：`judged` + `reason` + `outcomes`（`ok/leak/short/unknown/uncertain/n/a`）。
`Finding`：`code`（M0/M1/S0/S1/C0/C1/G0/G1/L1/L2）、`level`（warn/info）、
`chapters`、`suggestion`。
`Undetermined`：`kind`（branch/merge/cycle/dynamic/python_audio/unmodeled_channel/
side_branch/entry_unknown/condition/internal_flow/no_dialogue/…）。

### 69.3 结构识别与边界

- **章节**：内容 label（有 `say/extend/show/scene/hide/play/voice`，或带标题的
  `menu`）被"编排层"（无内容语句的 label）`call`/`jump` 到 → 章节；
- 没有编排层的项目退回"入口 + 被跳转到的内容"；
- **入口只用 `start`**（没有才用全部 `ENTRY_LABELS`）。否则同项目里的独立小应用
  （番茄钟的 `beginstory` 等）会被当成第二条主线 —— 实测会把
  `pomo_script2 → location` 拉进跨章报告；
- 编排层 `menu` 每个选项都 `jump/return` → **不算落空**（`_tail_terminates_in_menu`）；
- 只读：不写 `.rpy/.xlsx/.rpyc`、不自动补 stop；分支汇聚处不猜。

### 69.4 入口

- CLI：`python cli.py continuity <项目根> [--json] [--strict] [--file …]`
  （`--strict`：有"漏章"级问题或读不出来的文件才退 1）；
- UI：剧情结构页「🧵 跨章连续性」→ `ui_qt/continuity_dialog.py`
  （`QThread` 后台分析 + 按源文件指纹缓存 + 重新分析/复制报告）；
- **不进「🩺 项目体检」**：体检是构建门禁语言，跨章是审稿语言。

### 69.5 判据

`tests/test_continuity.py`（25 条：线性漏章/首拍就换/第二拍才换、分支子章照判
（单入边）、汇聚不 join、动态跳转、环、旁支、Python 音频、未建模声道、
JSON、`stage_exit_state` 状态推导、真机只读 mtime 不变）；
`tests/test_continuity_cli_ui.py`（10 条：CLI/`--strict`/`--file`、报告窗口
后台线程 + 缓存失效、工具栏入口）。

## §70 注释的 `#` 补全：产物层补，格内文字不动（2026-10-28）

两条通道，两条边界：

  · **产物（`.rpy`）必须合法**：写进脚本的注释行一定以 `#` 开头；
  · **格内文字必须原样回来**（P0 数据安全）：Excel / 卡片里作者打的字
    不因为"补 `#`"被偷改。

### 70.1 「注释」列 / 卡片备注

- 渲染层**本来就有**：`render_beat_to_rpy` ① 对 `beat.comment` 逐行补
  `# `（已有 `#` 的行逐字不动，`#bg1.2` 紧凑写法保留）；
- 表格 / 卡片里存的是作者的原话，**不在读写边界补 `#`**
  （`test_hell_carrier_attack` 钉着"格内文字必须原样回来"）。

### 70.2 「舞台（高级）」列：**代码，一个字都不改**（2026-10-29 改）

**这一列是代码，不是提示**：结构化列（背景/立绘/位置/转场）装不下的舞台指令
写在这里，导出时**原样接进 `.rpy`**，占一行、会执行。跟「备注」分工很清楚：
备注 → `# …` 注释行；舞台 → 一条语句。

- `dsl.parse_stage` 里**裸文本一律 `_native_op` 直通**，**绝不补 `#`**。
  这是 2026-10-29 用户拍板（原话："我的补#号只能发生在备注上……高级区 stage
  不应当补#"）。
- ⚠ **历史**：2026-10-28 曾用 `dsl._looks_like_stage_note` 自动补 `#`，
  次日撤掉。原因只有一个：**判据只能看字面，而字面同构**——
  `bg1.2 bgm13`（备忘）与 `weather6 rain`（fork 引擎的自定义语句）长得一样，
  补 `#` 就把真语句注释掉了，而卡片上看着还是原文，只有导出后才变
  （静默丢行为，最难查的一类）。
- **改由"问人"兜底**（不改字节）：
  - `story/stage_suspect.py::why_suspect(chunk, known_heads)` → 空串 = 像代码；
    非空 = 一句人话的原因。**判据宽进**（用户："宁错报，不要漏报"）：
    错报只是多问一次，漏报是"导出成功、游戏里那句没生效"。
  - 放行只看**硬信号**：`#` / `[` / `$` 开头；第一个词是内置语句头
    （`RENPY_KNOWN_HEADS`）或**项目登记过的**自定义语句头
    （`StatementRegistry.for_project`，即 `rules.json` / CDS）；
    含代码标点（半角引号/括号/`=`/`[]`/`{}`/`<>`/反引号/`@`/`\`）；
    第二个词起有子句关键字（`at`/`with`/`as`/`behind`/`onlayer`/`fadeout`/`loop`…）；
    或"动词+参数"形状且每个词都是合法标识符/数字。
    **全角标点一律不算代码标点**（`（淡出）` 是中文备忘的写法）。
  - `scan()` **只读**：不碰 `beat.stage`、不碰文件。
  - 界面：导出前弹 `ui_qt/dialog_stage_suspect.py`，默认「原样导出」；
    人勾选后点「移到备注」才调 `move_to_comment`（**只挪可疑那几条**，
    同格其它语句原地保留）。点「取消」= 一个字节都不写。
  - 命令行：`cli compile` 只往 stderr 打提示，**不阻塞、不改表**（CI 友好）。
- **不动**：`[显示] …` DSL、`show/hide/scene/pause/with/play/stop…` 原生写法、
  自定义语句、已有 `#` 的注释；`Beat.from_row` / `to_row` / 卡片保存
  不重写舞台格内容（同 70.1 的边界）。
- **真机实测**（2026-10-29，`sr_general`）：95 个 `.rpy` / 8088 拍，
  Stage 非空 9 格（全是 `show cg … at … behind …` 这类复杂子句），
  判据报 **0 条**（零打扰）；真实章节 `chapter1_2.rpy`（427 行 / 22363 B）
  零改动往返 **逐字节相同**。

### 70.3 判据

- `tests/test_comment_hash.py`：备注列渲染逐行补 `#` / 不双写；
  **舞台列一律不补**（含 `bg1.2 bgm13` / `白色转场` / `weather6 rain`）；
  格内文字原样回来（含多行）；Excel 往返后两列各归各位。
- `tests/test_stage_suspect.py`：判据（代码形状 19 条不许报 / 备忘形状 9 条
  必须报 / 登记过的头不报）、`scan` 只读、`move_to_comment` 只挪可疑的、
  弹窗真建框（默认全勾、默认决定是「原样」、取消是 `Rejected`）、
  接线（导出流程里"先问后 compile"、CLI 提示不改表）。

---

## §71 卡片右栏三处（2026-10-29 深夜）：舞台块常显 / 滚轮不换值 / 缩略图文字可读

用户一次报三条（第四条"立绘匹配"见 §71.4，本轮未动）：

> 不要让滚轮上下滑动调整背景，音乐和语音这三个东西……太容易误触了！！！
> 找不到入口！！！不要这个 + 改舞台/音乐这个了！直接完整显示……因为这是基本
> 信息！默认不应当收起……
> 还有用户表示渲染存在问题！修！！！

### 71.1 立绘 / 背景 / 音乐 / 语音（`_stage_box`）**常显**

- **普通卡**：一直完整显示（没有舞台改动也显示），**没有**"收起成一行"这个
  状态，也没有 `_stage_toggle` / `_stage_empty` 这两个控件（2026-10-29 删）。
  理由：用户找不到入口；且"这一拍没换景/人/曲"本身是信息（沿用上一拍）。
- **控制流卡**（`call` / `jump`）：照旧整体收起（`set_flow(info)` 里显隐；
  它们没有台词与演出）；`set_flow(None)` 回到普通卡时放出来。
- `load_fields()` 每次载入普通卡都确保 `_stage_box` / `_stage_now` 可见
  （上一拍可能是控制流卡）。
- 「当前舞台」那一行（图 + 摘要，`_stage_now`）与 `_stage_box` 仍**分成两块**：
  一个是"看"（只读、常显），一个是"改"（输入）。
- 判据：`test_hell_custom_card_layout.py::test_a_beat_without_stage_content_still_shows_the_stage_block`
  / `…_with_stage_content_…` / `test_ui_fit_and_flow.py::TestFlowCardHidesDialogue`
  / `test_stage_thumb.py::test_it_stays_when_the_beat_has_no_stage_change`。

### 71.2 背景 / 音乐 / 语音三格：**滚轮不改值**（`WheelSafeComboBox`）

- ⚠ **规则已被 2026-10-30 收紧，现行版本见 §74.1**："有焦点就放行"这条口子
  删了（那正是"滚动会导致选择切换"复发的原因）。这里只留历史记录。
- 旧规则：点进去（有焦点）滚轮才换值；没焦点不换值，并把滚轮**显式转交**给
  父级 `QAbstractScrollArea` 的 viewport（滚页面照常滚）。
- 实现：`ui_qt/wheel_guard.py`；`main_window._asset_row` 里三格用它
  （**不是裸 `QComboBox`** —— 裸 combo 默认会吃滚轮，实测 1→2 档）。
- 判据：`tests/test_wheel_guard.py`（无焦点不改值 / 滚轮转交滚动区 /
  有焦点才换值 / 三格真用了这个类）。

### 71.3 compact 缩略图（288×162）：名字/台词按**缩略图字号**重画

- 只对 `BeatStageThumb(compact=True)` 生效：整帧照旧 `render_frame`（1920×1080，
  真实排版），`scale_result_image` 缩到 target 后，由
  `stage_render.draw_readable_say(img, result, frame, profile)` 重画名字/台词。
  舞台预览窗口、卡片整宽图**行为不变**。
- 字号：`MIN_READABLE_PX`（name 11 / dialogue 9）+ `READABLE_PX_RATIO`
  （288 宽 → name ≈12 / dialogue ≈10）+ `MAX_READABLE_PX`；按图宽比例算，
  所以换 target 宽度/设备像素比后"在 288 控件上看到的字号"稳定。
- 铺底：条带（`textbox_rect`）与名条（`name_tag`）**各自用区域平均色、不透明**
  铺平，再画字；名字**名条底跟着字长**（`_fit_name_tag`：左/顶对齐引擎
  `name_rect`，只往右/下长；长到画面边缘才逐级缩到 9px + 「…」）。
- 字体：`profile.font_for(...)` 取项目字体；项目字体不含汉字时回退
  `_cjk_font_path()`（雅黑/黑体/宋体/Noto/文泉驿），**不许**用 Pillow 默认字体
  画中文；`info['font_fallback']` 可见。字号 <16px **不描边**（描边吃笔画）。
- 重画失败（异常）：`stage_thumb._picture()` `swallow` 并退回纯缩放的整帧图。
- 返回 `info`（测试用）：`drawn` / `name_px` / `dialogue_px` / `name_lines` /
  `dialogue_lines` / `ellipsis` / `font_fallback` / `name_box`（引擎原框）/
  `name_tag`（实际铺底+画字的框）/ `name_text_box` / `strip` / `covered`。
- 已知取舍：compact 图里项目文本框**装饰**（圆角/爱心/虚线）被铺平盖掉
  （可读优先；名条位置保留"有颜色的小标签"观感）；名条可能向下贴到框顶。
- 判据：`tests/test_stage_thumb_readable.py`（30 条）。

### 71.4 未闭环：立绘"只有文件名是角色名的可以匹配"

用户原话："只有文件名是角色名的可以匹配，不能直接放立绘图，直接放角色那个
头像图才能动。" 2026-10-29 追问三个方向（选择器挑图认不出角色 / 卡片显示成
头像特写 / 舞台缩略图显示错图），用户答**都不是**、会补截图与步骤。
**在拿到复现材料前不要动这块**（方向猜错比不改更贵）。

> **2026-10-30 已闭环**：用户补了材料（"立绘怎么一堆单独的分类，什么 relaxing，
> resigned" + 选择器实测），根因是**没有"图像 → 角色/属性"的唯一真源**，
> 不是显示问题。修法与判据见 §72.1 / §72.2 / §72.3。

---

## §72 二轮反馈：给"认得出 / 画得真 / 演得出"各立一个**唯一真源**（2026-10-30）

用户二轮试用的六条反馈 + 两个直接问题。规划与实测数字：
`docs/规划_二轮反馈修复路径_2026-10-30.md`；逐日叙事：`docs/开发日志.md` §52。
**这一节只钉接口、判定顺序、边界**——以后改这四块先看这里，别看细报告。

> 共同形状：三件事过去都不缺"判断"，缺的是**一个所有路径都走的真源**。
> 身份解析（哪张图是谁）、`placement_box`（舞台几何）、`transition_catalog`
> （过场长什么样）都是这么立起来的；然后卡片 / 预览 / 导出 / Excel 全走它。

### 72.1 资产身份解析：`assets/asset_identity.py`（纯函数，禁止 import UI/Qt）

- **为什么有它**：分类只看资源表「标签」列 + 空 Tag 行拿资产 ID 当 tag
  ⇒ 真机 **145 个分组里 137 个是「立绘：」、110 个分组只有 1 张图**；
  `cc_preview/<角色>/<表情>.png` 预览副本和真立绘混在一起。
- 接口（契约 §1.1）：
  `Identity(kind, tag, attrs, display, source, showable)`、
  `normalize_relpath`、`resolve_image(row, *, relpath, known_tags, known_chars, mapping)`、
  `find_real_sprite`、`spec_for_identity`、`asset_id_of`、`project_context(registry, root)`。
- **判定顺序（只认字面量，不模糊匹配）**：
  ⓪ 项目自己声明过的名字（ATL / 脚本 `define … image=` / 语句表）→
  ① 映射 `images` 精确覆盖 → ② 映射 `rules`（按数组顺序，先中先得）→
  ③ 资源表「Tag」列（**且 tag 是已知角色 tag 才算 sprite**）→
  ④ 路径目录段命中已知角色 tag（`.../<tag>/<stem>.png`，stem 作 attrs）→
  ⑤ 文件 stem 恰等于角色 tag → ⑥ **unknown**。
- `kind ∈ sprite / preview / avatar / bg / cg / ui / other / unknown`；
  `showable` 只对"真图层"（sprite/bg/cg/ui）为真 —— 选择器/卡片据此决定能不能写 `show`。
- **通用"副本检测"**：同 tag + 同 stem **另有一行 Tag 非空** ⇒ 这一行是 `preview`。
  **不是**认 `cc_preview` 这个目录名（任何项目都能用）。
- **不猜**：unknown 就 `showable=False`，卡片/选择器明说，导出门禁报出来。
- 判据：`tests/test_asset_identity.py`（59 条）。

### 72.2 项目级映射：`assets/asset_mapping.py` + `assets/mapping_excel.py` + `ui_qt/mapping_dialog.py`

- 文件名与位置：**`storygraph.mapping.json`**（schema `storygraph.mapping/1`），
  项目根或 `game/` 下；跟着项目走、可提交、可 diff（与 `storygraph.rules.json` 同一模式）。
- 四个键：`characters` / `rules` / `images` / `attributes`。边界：
  **未知键只警告不失败**；`images` 精确覆盖**优先于** `rules`；`rules` 按数组顺序；
  glob 占位 `<tag>` / `<stem>`；坏 JSON 抛 `MappingError`（降级成 warnings 一句话）。
- 接口（契约 §1.2）：`find_mapping_path` / `load_mapping` / `mapping_cached`
  （按 mtime 缓存）/ `resolve_with_mapping` / `draft_mapping`（从**实际目录**汇总，
  不写死任何项目路径，导出后能被 `load_mapping` 原样读回）/ `save_mapping`
  （原子写 + 旧文件 `.bak`，已登记 `test_single_write_entry` 的唯一写入口）。
- **第二条定义途径：Excel**（用户 2026-10-30「映射表除了 json 文件外，添加 excel
  表格的定义途径」）——`storygraph.mapping.xlsx`，代码在 `assets/mapping_excel.py`：
  · **一张表 = 一个顶层键**：「映射」/「角色」/「规则」/「图片」/「属性」
    （表名、列名中英文都认，`人物`/`characters` 都行；表头可不在第 1 行）；
  · **行序 = 数组顺序**（`rules` 先中先得，Excel 里没有下标列，顺序只能靠行位置）；
  · **空单元格 = 不写这个键**；`备注/说明/note` 列刻意忽略；`kind` 列有下拉 +
    冻结首行；`13.2` 这类数字 id 按文本读回；
  · 结构校验仍走 `validate_mapping(data, where=…)`：同一句话在 JSON 里是
    `images['x'] kind 不认识`，在 Excel 里是 `「图片」表第 12 行：kind 不认识`；
  · **xlsx 只读**：工具从不回写人做的表（`load`→`save` 一轮会丢图/图表/条件格式）；
    `write_workbook` 只用于**新建**（导出初稿 / 空白模板），
    「保存到项目根」写的是**同目录的 JSON 快照**（`write_target` 把 `.xlsx` → `.json`）。
- **优先级**：同一目录 `xlsx` **优先于** `json`，目录远近先于格式（项目根的 JSON
  胜过 `game/` 的 Excel）。`mapping_sources()` 出全部候选，`mapping_cached()` 在
  两份都在时追加一条"当前以哪份为准、改别的那份不会生效"，`status_text()` 写
  `（Excel）`——**生效源每处都说得出来**，不许靠猜。
- 工具**没有映射也能干活**（按目录/文件名推断）；映射只做**覆盖 / 纠正**。
- UI：菜单栏「**🗂 资源映射**」五个动作 —— `载入映射…`（JSON / Excel 都吃）/ `重读` /
  `导出映射初稿…`（存 `.json` 或 `.xlsx`，xlsx 带表头+下拉+说明页）/ `保存到项目根`
  （**先看 unified diff 再确认写**）/ `资源对照表`（路径/推断/tag/属性/依据/能否 show，
  可只看未识别）。打开项目自动加载项目根或 `game/` 下的映射。
  **第三个入口是表格**：窗口第一个页签「逐条编辑」= 同一份映射的表，见 §75。
- 发人样例：`examples/mapping/sr_general.mapping.json`（45 characters / 61 rules /
  40 精确覆盖，命中 91.8%，与"无映射推断"不一致 **0**），以及同名的
  `.xlsx`（同一份内容的 Excel 版；**45/61/40 往返一字不差**，可当填表模板看）；
  给别的项目开发者的格式说明：`docs/资源映射_给项目开发者.md`（含 Excel 填表规矩）。
- 判据：`tests/test_asset_mapping.py`（40 条）、`tests/test_mapping_dialog.py`（33 条）、
  `tests/test_mapping_excel.py`（40 条：往返 / 人写的表 / 定位警告 / `.bak` /
  被 Excel 占用说人话 / 优先级 / `draft_mapping`→Excel→判定结论不变）、
  `tests/test_mapping_edit.py` + `tests/test_mapping_editor_ui.py`（表格页，见 §75）。

### 72.3 选图判定与卡片提示：`app/picker.py::resolve_pick` + `ui_qt/asset_picker.py`

- `PickResult(spec, identity, ok, replaced_from, replaced_to, message)`；
  `resolve_pick(asset_id, row, **project_context)` 是**唯一判定**：
  · 能上舞台 → 直接给 spec；
  · 预览图 / 头像 → `find_real_sprite` 找到真立绘 ⇒ `ok=True` + `replaced_*` +
    `message='已用对应的立绘 xxx'`（真机 158 张预览图 **100% 换到**）；
    换不到 / 认不出 ⇒ `ok=False` + 一句原因，「确定」置灰。
- `image_spec_of` 原签名/行为**不许变**（既有调用方还在用）。
- 选择器：左栏**角色优先分组**（`立绘：凌跃捷（ling）` 放该角色全部表情；预览图/头像
  单独组；背景/CG/界面各自成组）；顶部**用途过滤**（全部/立绘/背景/CG/界面/角色预览图/其他）
  判据是 `Identity.kind`，**不按分组名/路径猜**；条目人话名 + tooltip 原始 ID/路径。
- 卡片：`sprite_grid` 的 `show_pick_hint` 提示条（换图 / 警告 / 「去『资源映射』修」）；
  首选回调 `on_pick_image_result`（带结论），老 `on_pick_image` 只作降级 ——
  **两个回调的优先级是钉死的**（只打桩老回调会被新回调短路，offscreen 下真弹模态框，
  2026-10-30 全量就卡在这条上）。
- 导出前门禁：`app.stage_identity_guard.scan_stage_identity` 逐拍看 `show` 的 spec 首词
  能不能上舞台，接入 `window_export._review_stage_suspects` 既有的「舞台可疑行」弹窗
  （**可仍然导出 / 取消 / 去修**，不静默拦不静默放）；拿不到资源表时**不判**（`degraded`）。
- 判据：`tests/test_picker_and_refresh.py`、`tests/test_asset_picker_resolve.py`（47 条）、
  `tests/test_stage_identity_guard.py`（43 条）。

### 72.4 舞台几何：`story/atl.py::placement_box`（**唯一几何实现**）

- `Placement` 增 `fit / xsize / ysize / nearest`；`fit` 五种值照 SDK 文档
  （`contain / cover / fill / scale-down / None`）；**认不出的 fit 值按没有 fit 处理**（不猜）。
  无 fit 时结果与原实现**逐位相同**（`Placement.box()` 委托 `placement_box`）。
- `story/stage_snapshot.py`：state 加 `scene_at`（**持续状态**：scene 写入、继承到下次 scene）
  与 `layer_at`（show 记、hide 清）；**只加 state 内部键**，显示列/变化文字不变，
  缺键默认空串（老调用方不炸）。
- `app/stage_render.py`：背景/图层/立绘统一走 `placement_box`；**无 at 的大图按引擎语义
  1:1 贴左上裁切**，不做"预览自动 fit"（用户拍板"预览必须真实"），旁边写人话提示。
- 内存：`_load_scaled` 解码按**可见区域**封顶 + 缓存改**字节预算 LRU**
  （默认 256MB，`STORYGRAPH_STAGE_CACHE_BYTES` / `_MB` 可调），替换 `lru_cache(48 张)`。
- 人工写 at（用户拍板"不自动写、经人手可以"）：`at_transform_candidates()` /
  `plan_at_edit()` → `AtEdit`；**只引用项目已有 transform**、只改内存 `Beat`、
  apply→revert 逐字还原、落盘仍走既有 diff 门禁；`stage_preview` 有「尺寸变换」下拉 + 撤销。
- 判据：`tests/test_stage_render_scale.py`（31 条，像素采样）、
  `test_stage_snapshot_scene_at.py`（14 条）、`test_stage_render_memory.py`（11 条）。

#### 72.4.1 「改图片大小」是**三件事**（2026-10-31，用户亲纠）

| 操作 | 语句 | 副作用 | 工具里的唯一实现 |
|---|---|---|---|
| 换景 | `scene bg x` | **清空该层所有 displayable**（立绘全没） | `app/stage.py::plan_scene_change` + 尺寸面板背景行的「换景」（见 72.4.1d；**只有人按下才写**） |
| 改这一景的尺寸 | `show bg x at …` | 只替换**同 tag** | `app/stage.py::_bg_show_edit`（函数体里**不出现 `scene`**）→ 改写本体 `beat_stage_parse.set_item_at` |
| 合并回源 | 改那几处的 `at`（**不增删行**） | 整景统一成源那个值 | `story/at_merge.py::plan_at_merge` + `beat_stage_parse.set_item_at`（见 72.4.1f） |
| 超清图 | 文件名 `xxx@2.png` | 引擎按 1/N 显示 | `app/stage_render.py::oversample_of / display_size`（**只认文件名，不自动改名**） |

- **`scene` 清的是整个 master 层**（2026-10-31 修）：从前 `stage_snapshots` 只清
  `actors`、`layers` 原样留着 → **产物里已经没了的 CG，预览/卡片还在画**。真机
  副本 `sr_general` 61 个 `scene` 拍里 **40 个**是这种"看到的 ≠ 生成的"。现在
  `state['layers'] / layer_at / layer_src` 一起清，并写一条「× 清空图层（N 个）」。
  判据 `test_stage_snapshot_scene_at.py::TestSceneClearsTheWholeMasterLayer`（4 条，
  含"同拍 scene 之后又 show 的还在"这条正面对照）。

- **出处（provenance）**：`stage_snapshot` 的 state 加 `scene_src` / `layer_src`
  （0 基拍号，-1 = 不知道；**0 合法，别用 `int(x or -1)`**），`StageFrame` 带出；
  `plan_at_edit(source_index=)` 在本拍没写 `scene`/这条 `show` 时改**来源那一拍**；
  `AtEdit.requested_index` / `affects` 说明"你在第 N 拍点的、影响第 a–b 拍"；
  `AtEdit.advice` 说"本景后面还有几处单独改写、改源到第几拍失效"（见 72.4.1e）。
  真机：1013 个有背景的拍里 972 个是继承的（旧行为 0 可写 → 修后 673 可写）。
- **`mode='override'`**（"从这一拍起"）：只写 `show`，复杂子句拒绝；本拍自己就是
  换景拍时改这一行的 `scene_at`（不新增 show）。
- **超采样**：`@N` 后缀 → 显示尺寸 = 文件尺寸 / N；`@0` / `@x` 不认（不猜）。
  尺寸提示行写两种形状：`800×600@2（引擎按 400×300 画）` / `800×600`。
- **令牌没点到的 show/hide 条目**：渲染器 `_emit_leftover_stage` 会补在末尾，
  `stage_snapshots` 从前直接丢（"产物两句 show、预览一个人"）；现在末尾按同一条
  规矩补齐。`AtEdit.extra_token/token_field` 是写入口那一侧的自保（插入新 show
  补 `show#N`，verb 级令牌不补）。
- 判据：`tests/test_stage_at_source.py`（36 条，含"改完立绘还在"与"写 scene 就清空"
  的正反对照、"快照显示的人数 == 渲染器吐出的 show 行数"）、
  `test_stage_render_oversample.py`（8 条）。

#### 72.4.1b 尺寸预设：人话 → 项目已有 transform（2026-10-31 傍晚）

- `story/size_presets.py`：`PRESET_ORDER = as_is / cover / contain / half`。
  `target_box(key, natural, screen)` 用**同一个** `atl.placement_box` 算目标框，
  位置一律左上角 `(0,0)`（引擎默认摆法，不替人决定居中；要居中是 `xalign` 的事）。
- `match_preset(key, natural, screen, index)`：把项目每条 transform 套在这张图 +
  这个屏幕上算框，与目标框**逐像素**比（容差 1.5px）—— **只看效果，不看名字**。
  ⚠ 语义先于名字：4K 图放 1080p 屏时「50%」与「铺满」的框完全相同，纯按名字会
  把 `fit "cover"` 当 50%；`_preference` 先按语义（cover/contain 看 `fit`，
  half/custom 看 `zoom`）再按名字。
- **排除**：带动画/事件块的（`atl.is_static_transform`，顺调用链看一层）、
  改透明度/旋转的、几何算不出的。一条都没有 → `None`，由下一层决定"生成"还是
  "明说没有"（见 72.4.1c）。
- `atl_body(key, zoom=)` 是生成 `sg_*` 的**唯一出处**（`fit "cover"` / `zoom 0.5`）；
  「原样」没有体 —— 它是**去掉 at**，不是套空 transform。
- `app/stage_render.size_rows(frame, …, beats=None)`：背景 + 各 CG/UI（≤3）各一行，
  `note` 复用 `_size_note`（与预览提示行同一份措辞）+ 出处 `（第 N 拍设的）`；
  `items` = 四条预设 + 「自定义倍数」（见 72.4.1i）+ `pick`（项目已有 transform）；
  不可用项 `ok=False` + `why`；
  `advice` = 本景"还有几处单独改写"（见 72.4.1e；不给 `beats` 就是 `None`）。
  缺图/自动命名的项目走 `resolve_stage_asset(..., index=image_index(root))`。
- `app/stage.plan_size_edit(beats, index, row, key=, at=)`：`as_is` →
  `plan_at_edit(mode='clear')`（`scene_at=''` / 删 `@at`；本来没有 at 就拒绝）；
  其余 → `mode='source'`（改**最初写下这一行的那一拍**）。背景若是 `show bg X`
  换的（没有 `scene_at`）→ 明确说"请在高级区 Stage 改那条 show 的 at"。
  **`source < 0`（跨章继承）** → 两条口径都换：`mode='chapter_first'`
  （见 72.4.1g）；「原样」直接拒（上一章的 at 看不到，补裸 show 可能白做）。
- 卡片：`ui_qt/size_panel.py::SizePanel`（4 槽，`WheelSafeComboBox` +
  `NarrowWrapLabel`）只发 `size_requested`；窗口层 `apply_size_from_card`
  走**与演出预览相同的** `_confirm_at_plan` / `_after_preview_beat_edit`。
  写回前先 `apply_current_beat()`；改的是本拍 → `_show_index` 重载，否则只刷派生面板。
- 判据：`test_size_presets.py`（27）、`test_stage_size_rows.py`（28）、
  `test_size_panel.py`（38）、`test_size_generate.py`（8）、
  `test_size_custom.py`（46，见 72.4.1i）。
- `NarrowWrapLabel` 已搬到 `ui_qt/wrap_label.py`（`main_window` 仍导出同名）。

#### 72.4.1c 生成 `sg_*`：三条护栏（2026-10-31 夜）

用户拍板："选 1（允许生成 sg），但要加三个前提条件……一条都别省"。

- **文件**：`game/sg_transforms.rpy`（`story/sg_transforms.py::FILE_NAME`）。头两行是
  人话叮嘱，第三行是判据：`# sg-gen v<版本> sha256 <64 位十六进制>`（`MARKER_RE`）。
  **sha256 是标记行之后那段正文的指纹**（UTF-8、LF 归一 —— 线尾改成 CRLF 不算手改）。
- **① 手改就 abort**：`inspect()` → `SgFile.state` ∈ `missing / ok / foreign /
  hand_edited / bad_version / unreadable`，只有前两种可写。`SgPlan.apply()` 写盘前
  **再看一眼**磁盘（确认框隔着一次人机交互），状态变了或条目对不上 → 抛 `RuntimeError`，
  不覆盖。版本列表 `MARKER_VERSIONS = (1,)`（认旧版本，不认识的版本也不动）。
- **② 生成前查重**：`plan_define` 依次判 —— 同名同内容 → `reuse`；**逐字相同的
  ATL 体**（换个名字写的一样命令）→ `reuse`；几何能匹配到项目里已有 transform
  （`size_presets.match_preset`；拿不到图尺寸时倍数类预设走
  `match_without_natural`，见 72.4.1i）→ `reuse`；文件里已经有这一条 → `noop`；
  名字被别人的内容占了 → 退到 `sg_xxx_2`（**不改别人的定义**，`note` 说清）。
  只有都不过才 `create` / `append`。`size_rows` 里那一层**只算不写**。
- **命名**：`sg_fit_cover` / `sg_fit_contain` / `sg_zoom_050`（百分比三位补零，
  `0.333` → `sg_zoom_0p333`）。名字**不带图名** —— 同一个"铺满"对所有图都成立。
- **几何自证**：`transform_def(name, key)` 从**生成的文本再解析一次**（`parse_transforms`），
  计划里算的框因此必然等于文件里写的东西（判据拿 `placement_box` 逐像素比对）。
- **写回顺序**（`main_window.apply_size_from_card`）：① 两件事摊在**同一张**确认框
  （脚本 diff + 文件 diff，`_confirm_at_plan(extra=)`）；② 先写文件（护栏过了才写），
  再 `.apply()` 内存里的拍；③ `session.refresh_transform_index()` 刷缓存，否则卡片
  还显示"项目里没有"。取消 / 被拒 → 文件和拍**一个字节都不动**。
- **③ 引用存在性**：`sg_transforms.missing_references(root)`（`scan()` 一次读完全部
  `.rpy`，同一次扫描里收 transform 定义）→ `pipeline.validate.validate_sg_transforms`
  出 `E305`（L3 / **error**，`where=文件:行号`），进项目体检 / `cli.py validate`；
  打开项目时另由 `ui_qt.window_graph_ops.report_missing_sg_transforms`（挂在
  `QTimer.singleShot(0)` 上）在控制台点名 —— 干净项目一个字都不说。
  **只查 `sg_` 命名空间**：人自己的 transform 扫不全（动态定义 / include / 别的目录），
  在那里报就是噪音。台词里 `"meet me at sg_home"` 不算引用（只看 `scene/show/add/
  camera` 语句的 `at` 子句，`with/behind/onlayer/zorder/as` 之后截断）。
- 真机：`sr_general` 与 `renpytest/sr_general` 两个项目 **0 引用 / 0 漏报**，
  单遍扫描 246 / 273 ms（只跑在体检上）。

#### 72.4.1d 显式「换景」：`scene` 只在人按下时写（2026-10-31）

- **入口**：卡片「尺寸」一格**背景行**多一个「换景」（`ui_qt/size_panel.py` →
  信号 `scene_requested`；主窗口 `main_window.apply_scene_from_card`）。
  面板只看"当前是哪张图 + 下拉里选中的预设"，**不数人、不弹框**。
  挑图走卡片「背景」那一格**同一个**资产选择器（`_pick_asset_for('Scene', …)`），
  保证"从这里能选的、从那里也选得到"。
- **计划**：`app/stage.py::plan_scene_change(beats, index, image, at=…)` → `SceneEdit`
  （`AtEdit` 的子类）。写**请求的那一拍**（换景是**事件**，不是持续状态；
  尺寸才是持续状态，那一条写"最初写下它的那一拍"）。
- **确认框写清代价**（用户原话）：`SceneEdit.diff_text()` 先写"这一拍本来有没有
  scene / 换到哪张图 / **会清掉台上 N 个立绘（名字）** / 还会清掉几个 CG·UI 图层"，
  再写 diff。`cleared_sprites` / `cleared_layers` 来自 `stage_before(beats, index)`
  —— **这一拍开始前**的舞台状态（与预览同一份推导，不是另数一遍）。
  算不出来（快照炸了）→ **拒绝**，不当"台上没人"。
- **顺序令牌**（`_scene_token_insert`）：本来没有 scene 的拍要把 `scene` 令牌
  插在**第一条舞台指令之前**。不插的话 `op_tokens_full()` 把缺的 verb 补在
  **末尾** → `show → scene` = 人刚上台又被清掉（与换景意图相反）。
  没有顺序信息（两张列都空）→ 什么都不补（渲染器本来就按 scene→show→hide 出）。
- **同一拍的背景 `show`**：简单子句（`bg x` / `bg x@at`）**去掉**（它排在 scene
  之后会把新背景盖回旧那张）；复杂子句（`behind/with/expression`）→ **拒绝**，
  让人先去高级区改。别的 tag（cg/ui）不动（它们排在 scene 之后，照常存活）。
- **拒绝清单**：图名空 / 带 `@` / 项目里找不到这张图（`script_image` +
  `image_index` 都认不出 → 写进脚本会 NameError）/ transform 项目里没有 /
  scene 已经是这一景 / 台上没人且背景没变（"什么都不会改"）。
- **另一条会写 `scene` 的路**：卡片「背景」那一格（从"没有背景"变成"有背景"）。
  它**不弹框**（填背景是最常做的编辑），但保存成功后由
  `ui_qt.window_card.note_scene_added` **立刻点名**被带走的立绘/图层
  （控制台 `[换景]` + 状态栏那句"…被清掉（可撤销）"）；台上没人时一个字不说。
  要**事前**确认，用上面的「换景」入口。
- **入口的边界**：尺寸面板在"这一拍看不到背景、也没有 CG/UI 图层"时**整格收起**
  （`set_stage_size([])`），所以**空台新建一景**没有「换景」按钮 —— 那种情形用
  卡片「背景」那一格（上面那条事后点名）。「换景」按钮只负责"已经有景，换一张"。
- 判据：`tests/test_scene_change.py`（39 条：计划 / 清台计数 / 令牌位置 /
  同拍 show 去除 / 同一张背景只清台 / 确认框正文 / 面板只给背景行 /
  窗口端到端 / 卡片那一格不静默）。

#### 72.4.1e 本景"还有几处单独改写"（2026-10-31 夜，只读）

- **它回答什么**："改最初那一行（源）之后，本景后面还有谁又 `show` 了一次这个
  对象、把这次改动盖回去"。尺寸面板改的是**源**那一行，而 `show` 会**重建**
  displayable ⇒ 源里的 `at` 从那一拍起失效。用户改完看的是当时那一拍，于是
  "改好了"和"只改到第 12 拍"长得一样。
- **纯函数**：`story/override_advice.py`
  - `override_map(beats, start)` → `{key: (Site…)}`：背景进 `'scene'` 桶
    （三种 tag 同一个桶），CG/UI 按自己的 tag 分桶。边界只有一条：**遇到自己
    写了 `scene` 的拍就停**。只数 `show`（`hide` 既不变成一处、也不结束扫描）。
  - `override_advice(beats, source, *, kind, tag, prefetched)` → `OverrideAdvice`
    （`level` / `sites` / `count` / `beats` / `ends_at` / `same_at` /
    `conflicts(new_at)` / `agreeing(new_at)` / `text(new_at, label)` / `short(label)`）。
  - **档位**（`docs/超清CG与BG_零侵入导入_2026-10-31.md` §5.5）：0 处 `none`
    （不显示）/ 1–2 处 `hint` / ≥3 处（`NOTICE_AT`）`notice`。**算不出来** →
    `hint` + `unknown=True` + `reason`（降档，但绝不当 0 处）。`source < 0`
    （跨章继承）→ `none` + `reason`（这一行本来就改不了，不冒充 0 处）。
  - `prefetched` = 面板一帧 4 行共用的一次扫描（起点取各行 `source` 的最小值，
    逐行再按自己的 `source` 切片）。
- **口径只有一份**：生效的 `at` 走 `story/beat_stage_parse.py::item_at(item, beat)`
  （item 的 `at` > 拍级 `at` > 拍级 `transform`；`stage_snapshot._apply_show_verb`
  也改调它）；"是不是同一个图层"走 `core/vocabulary.py::same_layer_tag`
  （`app.stage._same_layer_tag` 转调它）。
- **接线**：`app/stage_render.size_rows(..., beats=None)` 每行多一个 `advice`
  （不给 `beats` → `advice=None`，**不显示**，不是"0 处"）；
  `plan_at_edit` 把同一结论放进 `AtEdit.advice`，`AtEdit.diff_text()` 在 **diff
  之前**说这句话（坐标写"第 12、19 拍"，不写"若干处"）。
- **面板**：`ui_qt/size_panel.py` 每个槽多一个 `advice` 标签 —— 一行短的 + 完整
  句子在 tooltip；`hint` 用 `role="note"`、`notice` 用 `role="warn"`（琥珀），
  `none` / `None` 隐藏。**不拦任何操作**。
- **边界**：这个模块只报数、不替人做决定。合并有了入口，但是**另一档**（见
  72.4.1f）：人按按钮 + 逐处勾选，而且做法是**改 `@at`**，不是删行。
- 判据：`tests/test_override_advice.py`（37 条：扫描边界 / 三种背景写法同桶 /
  图层分桶 / `hide` 口径 / 三档 / 降档 / 措辞数字 == 处数 / `new_at` 只点会盖回的 /
  `short()` 单行 / 面板与确认框接线 / **与舞台快照逐拍一致**）+
  `tests/test_size_panel.py` 里 8 条（显隐 / 颜色 / tooltip / 不顶宽 / 窗口级确认框）。

#### 72.4.1f 合并回源：把那几处改成同一个值（2026-10-31 深夜）

- **一个必须先说清的语义**：合并不是"把那几处 `show … at …` 删掉"。
  `show` 会**重建** displayable ⇒ 删掉 `at` = 回到**原样**（不是继承源的 `at`）；
  而且那几条 `show` 往往**同时换了图**。所以合并 = **只改那几条的 `@at`**：
  行数、图名、`behind/with/as` 一个字都不动。
- **目标值**：`story/at_merge.py::source_at_of(beats, index, kind=, tag=)` ——
  **源那一行自己写着的** `at`（该拍有这条 `show` 就用它的 `item_at`，否则
  `scene_at`）。**不能**拿 `frame.scene_at`：那一拍可能已经在某处改写**之后**，
  拿它回填会把源一起改掉。
- **计划**：`plan_at_merge(beats, source, *, kind, tag, label)` → `MergePlan`
  （`ok` / `count` / `beats` / `steps` / `skipped` / `apply(selected=)` /
  `revert()` / `diff_text()`，接口与 `AtEdit` 对齐，多拍所以没有 `beat_index`）。
  - 处数**不自己扫**：直接吃 `override_advice(...).sites` ⇒ 恒有
    `len(steps) + len(skipped) == advice.count`（面板说 2 处，这里改的就是那 2 拍）；
  - `site.at == 目标值` 的**不列**（已经一样，不是"改不动"）；
  - `skipped` 带原因（`complex` 复杂子句 / `expression` / `blocked` 拍级 at 会顶上 /
    `missing` 找不到那条 `show`）—— **绝不静默跳过**；
  - 计划拒绝（`reason` 非空）：源越界 / 没有 tag / 源里的 `at` 带 `@` /
    没有可合并的 / 全改不了 / **统计不出**。
- **改写本体**：`story/beat_stage_parse.py::set_item_at(text, tag, at, beat=None)`
  → `ItemAtRewrite(status=ok|noop|missing|complex|expression|blocked)`。
  **这是"改一条 show 的 `@at`"的唯一实现**：`app.stage._layer_show_edit` /
  `_bg_show_edit` 都转调它（补条目 / 补 `show#N` 令牌仍是 `_bg_show_edit` 特有）。
  三条规矩：① 同 tag 多条时改**最后一条**（Ren'Py 侧后 show 的赢）；
  ② **绝不新增/删除子句**；③ 行尾冒号（ATL 块）剥下来再拼回去 —— 旧实现会
  吃掉它（`show cg cg1@half:` → `show cg cg1@fit` + 无主缩进，引擎报
  `expects a non-empty block`，真机 35 处）。`show_part_info` 同时搬到这里，
  面板与合并因此判同一件事。
- **L4 门禁**（`docs/超清CG与BG_零侵入导入_2026-10-31.md` §5.5：confirm、永远、
  逐处）：入口 = 尺寸面板每行的「一并改成一样（N 处）」（`merge_requested`）→
  `ui_qt/merge_dialog.py::MergeDialog` / `ask_merge`：**一处一个勾**（默认全勾，
  勾掉的一个字不动）、「全不选」≠ 取消（返回 `set()`，调用方说"一处都没勾"）、
  改不了的单独列出 → `main_window.apply_merge_from_panel` 只改内存里的 Beat。
  多拍**没有全局撤销栈**（会话是逐拍单级 `undo(idx)`），文案说的是真实的门禁：
  导出前还有一次逐行 diff。
- **显隐**：`advice.agreed`（那几处都写着源那个值）→ **按钮不出现**（点了只会
  "没有要改的"）；`unknown` / `source < 0` / 0 处同样不出现。
  `override_advice(..., source_at=)` 新增 `agreed`，面板那句在这种状态下**降档
  换口气**："本景还有 2 处写着同一个 at（第 943、952 拍）：再改源只到第 943 拍
  有效" —— 陷阱还在（源下次再改仍只到第一处），所以不消失、只不上琥珀色。
- 真机（只读，`tools/_probe_merge_real.py`）：`sr_general` 8045 拍 / 副本 8343 拍，
  两边都 **58 行有单独改写，且全部是 `agreed`**（重复写同一个 `@cg38402160`）——
  计划一致拒绝，两个项目数字一字不差。截图 `tools/_merge_panel.png` /
  `tools/_merge_dialog.png`（面板最小宽仍 166）。
- 判据：`tests/test_at_merge.py`（35：改写本体 13 / 目标值 5 / 计划 8 /
  勾选与撤销 5 / **跨层两拍一致** 2）、`tests/test_merge_dialog.py`（11）、
  `tests/test_size_panel.py` 里 6 条（显隐 5 + 端到端 5 —— 合计见该文件）。

#### 72.4.1g 跨章继承的一行：本章第一拍补一条 `show`（2026-10-31 深夜）

- **前提（先说没接上的那一半）**：`scene_src` / `layer_src < 0` = 出处**不在
  这本剧本里**（上一章留下的）。卡片/预览建帧从来没传 `initial_state`，所以
  真机上这种行在卡片里是 **0 个**（`tools/_probe_entry_real.py`：两个项目都是 0）。
  `snapshot_rows(beats, initial_state=)` / `build_frames(..., initial_state=)` /
  `build_frame_at(..., initial_state=)` 这一轮已经收它，**喂什么**还没接
  （必须走 `pipeline/continuity` 的链条：`chapter2_1.rpy` 整章 130 拍没有
  scene，"上一个文件的出口"是空的）。
- **`plan_at_edit(mode='chapter_first', scene_image=图名)`**：出处不在本剧本
  ⇒ 没有"那一行"可改，写 `scene` 又会清台 ⇒ 只能在**第 0 拍**补一条
  `show 图@变换`。产物**只多一行**：行数 +1、别的条目与图名逐字不动。
  `AtEdit.inserted=True`（`diff_text()` 说"补到第 1 拍"、`target_label` 说
  "本章第一拍补一条 show，不清立绘"）。
- **落点恒为第 0 拍**（背景/图层从本章第一拍就在台上）。接不住的一律**带
  原因拒绝**，绝不静默换拍：第一拍带 `condition` / 在 `menu`·循环块
  （`nest_path`）/ 行尾冒号（ATL 块）/ 同 tag 已 `show` / 图名带 `@` / 图名
  与 tag 对不上 / 预设是**「原样」**（上一章的 at 看不到，补裸 show 可能白做）。
- **补条目本体**：`app.stage._append_show_item(beat, image, transform)` ——
  与「从这一拍起改这一景」（`_bg_show_edit`）**共用**，含 `show#N` 令牌
  （`_show_token_append`：`op_order` 有逐条令牌时不补 = 快照看不见新条目）。
- **两条出口一份判断**：`app.stage.is_unknown_source(value)`（公开名；**`0` 是
  合法出处**）供 `plan_size_edit` 与 `ui_qt.main_window._plan_preview_at` 共用
  —— 尺寸面板与演出预览都走这一条，不再"面板补一条、预览拒绝"。
- **行的 `why` 两件事都写**：`stage_render._row_why(image_why, src)`。从前是
  `why or _unknown_source_why(src)` —— 图片解析不出来时"这一行是上一章留下的"
  会被**吞掉**，人按了「改」才发现落点跑到第 1 拍。
- 真机（`tools/_probe_chapter_first_real.py`）：91/92 个章节的第一拍可当落点
  （1 个因 ATL 块拒绝）；`chapter1_1→chapter1_2` 继承 `bg bg9_2`（无 at）→
  补 `show bg bg9_2 at bgfit`，影响 1–1 拍（那处接缝本就是 S0）；
  `chapter1_4→chapter2_1` 继承 `black`（纯色，算不出自然尺寸）→ 只有「自定义」。
- 判据：`tests/test_chapter_first.py`（40：落点与产物 / 跨层快照 / 六种守卫 /
  两条出口 / 面板措辞 / 前提）；旧措辞改 3 条（`test_stage_size_rows.py`：
  "跨章 = 拒绝" → "跨章 = 第 1 拍补一条"）。

#### 72.4.1h 把入口状态喂给卡片：跨章继承闭环（2026-10-31 深夜）

上一节那条"前提"现在接上了 —— 卡片**真的**看得见"上一章留下的背景"。

- **真源只有一份**：入口状态 = `continuity.Chapter.entry`（`stage_exit_state`
  的产物）。`ChapterEntry`（`label/file/state/why`）只补 `Chapter` 上读不到的
  两件事：`why`（链根 / 环 / 汇聚 / `接上一章 X 的收尾`）与 `unknown` 集合；
  `note()` 逐通道说"确定 / 不知道"，`describe()` = `why；note`。
- **不许按文件名猜**：`chapter2_1.rpy` 整章 130 拍没有一句 scene，
  "上一个文件的出口"是空的；那 4 处 `2_1 → 2_1_1~4` 只能沿链条
  （chapter1_4 → chapter2_1 → 2_1_1）推。`entry_by_file(report)` 取**文件开头
  那一章**（同文件多章取 `order` 最小），一条报告只算一次（挂在报告对象上）。
- **贵的那一步只在后台**：整本 `analyze_project` 真机 **2.2–2.8 s**（解析 1.6 +
  分析 0.5），指纹 `project_source_signature` **46 ms**。所以
  `app/stage_entry.py` 只做"缓存 + 只读查询"（键 = 项目根 + 指纹；
  `entry_for()` 没命中就 `None` + `why_not()`，**不在查询里跑分析**），
  分析由 `ui_qt/entry_warm.py::StageEntryWarmer` 丢进 `QThreadPool`
  （同一项目只跑一份，`request()` → `cached/queued/running/skipped`）。
- **四条消费线**都从 `session.stage_initial_state()` 取（唯一口子）：
  `stage_digest_text`（`stage_digest(initial_state=, entry_note=, entry_why=)`）/
  `stage_size_rows`（`build_frame_at(initial_state=)` → `source == -1` 的行）/
  卡片小图 `stage_thumb.show_beat` / 演出预览（`session.frames()` +
  `open_preview(initial_state=)`，**含写回 at 之后重算帧**）。
  `EditorSession.load()` 清入口状态（换文件 = 换了上一章）。
- **`unknown` ≠ 空**：卡片写 `（不知道：上游没定下来，不是「没有」）`；
  没有入口状态时第 1 拍另说 `跨章：上一章留了什么 —— 还不知道（原因）`
  （只在第 1 拍说；单文件模式不传 —— 跨章本来不存在，老行为逐字节不变）。
- **`StageFrame.entry_state`**（新字段，默认 False）：这一帧是不是**接着上一章
  演**算出来的。手工拼的帧 `scene_src=-1` **不是**跨章 —— 跨章那一行的
  「原样」要写「原样（跨章这一行补不了）」（上一章的 at 看不到），
  本地行仍写「原样（已经是原样）」。
- 真机（`tools/_probe_entry_card.py`，offscreen 真窗口只读）：43 章里 **6 章**
  入口有确定背景（`chapter1_2 ← bg9_2`；`chapter2_1 / 2_1_1~4 ← black`）=
  跨章体检报的那 6 处接缝。截图 `tools/_entry_card.png`。
- 判据：`tests/test_stage_entry.py`（37：`ChapterEntry` 措辞 / 文件→入口 /
  会话四条线 / 缓存失效 / 后台只算一次 / 卡片只重画派生面板 / 预览重算帧）。

#### 72.4.1i 「自定义倍数」：人填的那个数（2026-10-31 深夜）

`50%` 只是 `0.5` 的特例；用户要的是能填 `0.75` / `1.25`。这一条**不新开一条
机制**：仍然是"先在项目里找效果一样的，找不到才生成"，只是值改由人给。

- **判据只有一份**：`size_presets.parse_zoom(text)` → `(zoom, 错误句)`，
  `zoom_error(value)` → `''`／原因；范围 `ZOOM_MIN = 0.05` … `ZOOM_MAX = 5`
  （`0` 看不见；`100` 倍会把 4K 背景解成 30 万像素宽）。**越界就拒绝，绝不夹**
  —— 夹了人以为自己填的是那个值。认 `0.75` / `0.75x` / `0.75×` / `0.75倍` /
  `75%`；`75`（不带百分号）**不认**（那是 75 倍，但人多半想写 75%），
  错误句里两种写法都给。值统一 `round(…, 3)`（`0.750` 与 `0.75` 必须同一个数，
  否则 transform 名会分成两条）。
- **可生成的预设集合**：`size_presets.GENERATABLE = cover / contain / half / custom`
  （`PRESET_ORDER` 仍是下拉里固定那四项 —— `custom` 的值列不完）。
  `sg_transforms.plan_define(key='custom', zoom=)` 先 `zoom_error`，不过就
  `refuse`（`自定义倍数没填好：…`），过了才 `sg_zoom_075` / `zoom 0.75`。
- **行与面板**：`size_rows` 在四条预设后面、`pick` 前面固定插一条
  `{'key':'custom', 'at':'', 'zoom':None, 'generate':True, 'ok':False}`；
  面板按输入框里的值换成**算好的副本**（不动 `rows` 里的字典 —— 那是会话缓存），
  条目文字 `自定义倍数 0.75×（zoom 0.75）`，选中时才露输入框那一行。
  填错了条目**仍然可选**（不然人看不见自己错在哪），灰的是「改」按钮；
  填过的字按 `kind+tag` 记着（**原样存**，翻拍回来还是它）。请求多一个
  `'zoom'`（别的条目是 `None`），`at` 仍是 `''` —— 名字由窗口层定。
  输入框用 `QLineEdit` 而不是 `QDoubleSpinBox`：数值框吃滚轮（靠全局
  `wheel_guard` 兜），输入框根本不响应滚轮。
- **两条新查重**（护栏 ② 的补丁，真机暴露的）：
  ① **逐字相同的 ATL 体也算等价**（不看名字：真机 `sr_general` 的 `cover`
   → 已有 `bgfit`）；② **拿不到图尺寸也能查倍数类预设** ——
  `match_without_natural(key, screen, index, zoom=)`：纯缩放（没有 `fit` /
  `xsize` / `ysize`）的框是图尺寸的一次函数，在 `_REF_NATURALS`
  两个参考尺寸上框都相同 ⇒ 任何图尺寸都相同；**位置参与比较**
  （`zoom 0.6` 但居中放的不冒充左上角）。于是 `zoom 0.5; anchor (0,0)`
  （真机叫 `cg38402160`）在"图找不到"时也能被判等价。
- 真机（`tools/_probe_custom_zoom.py`，只读）：92 文件 / **1627** 个尺寸行全都
  带这一条；`sr_general` 有 **116** 条 zoom 变换（0.2 / 0.3 / 0.36 / 0.4 / 0.5 /
  0.6 / 1.2 / 1.5 / 2），所以「0.5」会复用 `cg38402160`、「0.75」才会同一条
  `sg_zoom_075`。铺满/适应/50% 有一条能匹配现成 transform 的行 1536；
  三条**全都只能生成**的 **91** 行全是 `black`（纯色，没有图 → 算不出尺寸），
  那种行只有「原样」和「自定义倍数」能落地（倍数不需要知道图多大）。
  截图 `tools/_custom_panel.png`。
- 判据：`tests/test_size_custom.py`（46）。生成那半的旧判据仍有效
  （`test_sg_transforms.py` / `test_size_generate.py` 一条不改）。

### 72.4.2 优先级三件事：显式层 / 覆盖留痕 / 三态（2026-10-31）

- **词汇表显式层（P0-2）**：`assets/vocabulary.py::VOCAB_LAYERS`（层名 → `AUTHORITY`
  层）+ `Vocabulary.merged([(层名, 词条), …])`：**按层号合并，顺序无关**；
  `plus()` 只是兼容（后来者优先，新代码别用）。同一层里的段次序由
  `_SEGMENT_ORDER` 固定（文件内部的事，不是优先级）。判据
  `tests/test_vocabulary_layers.py`（三层全排列结果相同 / 未知层名 `ValueError`）。
- **覆盖留痕（P0-3）**：`core/authority.py::Override`（append-only，`sentence()`
  是唯一措辞）+ `describe()`。命名域在 `Vocabulary.merged()` 里记
  （**值不同**才记），`Vocabulary.explain()` 说出来，`labeled_choices.labeled()`
  把它填进下拉 tooltip；身份域在 `Identity.overrides`，`resolve_image` 在
  "精确覆盖赢 + 这一行声明过"时留痕，选择器 tooltip / 详情面板显示「覆盖了：…」。
- **三态判定**：`story/write_gate.py::classify(disk_bytes, base_sha1, draft_text,
  base_text=None)` → `ADOPT` / `REBASE` / `CONFLICT`（`Verdict.ok` = 前两态）。
  会话侧入口 `EditorSession.source_state()`；`source_unchanged()` **只比内容**
  （touch 不算冲突）。`base_text` 给了才有行级合并候选（`merge_text`），
  有候选也仍是 `CONFLICT`。判据 `tests/test_write_gate.py`（17 条）。

### 72.5 自定义过场：`transition_expr` → `transition_catalog` → `transition_fx`

- **为什么有它**：项目里 303 处 `$ renpy.transition(...)` 是 **Python 语句**（`Beat.code`），
  而工具只有 `Beat.with_` 一条过场通道 ⇒ 卡片看不见、预览硬切、Excel「转场」列空。
- `story/transition_expr.py`：**安全字面量递归下降解析器**（无 `eval`、无
  `ast.literal_eval`；禁 f-string/运算/推导式）。认 `$ renpy.transition(EXPR, layer=…)`、
  独立 `with EXPR`、行内 `with`；多行/注释/字符串里的括号/中文标识符都能处理；
  **认不出 → 空列表，不猜**。`beat_transitions(beat)` 纯读。
  已知限制：`hide X with Y` 不派生（上游把 `with` 丢了，如实不猜）。
- `story/transition_catalog.py`：扫项目 `define NAME = <expr>`（含多行）建目录，
  递归解析 `Dissolve / Fade / Pixellate / ImageDissolve / MultipleTransition /
  Pause / Solid`，名字引用带循环保护；`MoveTransition / CropMove / ComposeTransition`
  **如实 `unknown`**。`Effect(kind, duration, color, ramp, ramplen, segments, label)`；
  `effect_of(call, catalog)` / `describe_effect(effect)`。
- `app/transition_fx.py`：Effect → **中间帧**（dissolve 交叉 / fade 按 out·hold·in /
  pixellate 块化 / imagedissolve 用 ramp 遮罩 / multiple 分段 / pause·solid / unknown 空帧）。
  **认不出 / 缺遮罩就如实降级 + 标注「无法精确预览」，绝不假装淡入。**
- 接线：`StageFrame.transitions`（默认空 = 向后兼容）；卡片「当前舞台」与演出预览共用
  `stage_render.transition_summary_lines()`（`过场：Dissolve 0.1（master）`，
  一拍多条只读、不编号）；预览按 `Effect` 演中间帧（20fps，帧数封顶）。
- 判据：`test_transition_expr.py`（29 条）、`test_transition_catalog.py`（21 条）、
  `test_transition_fx.py`（35 条）、`test_stage_transition_integration.py`（24 条）。

### 72.6 通用性与纪律（这一轮的四条硬规矩）

1. **项目知识只能来自数据**：代码里**不许**出现 `sr_general` / `cc_preview` / `ling`
   这类项目专有常量作为逻辑分支；唯一载体是资源表 / 目录结构 / 映射文件 / 引擎事实。
2. **认不出就说认不出**：unknown / degraded / 无法精确预览都有明确出口，**不猜**。
3. **UI 只编排**：`assets/*`、`story/*` 新模块纯函数无 Qt；判定全在 `app/*`。
4. **真机 `sr_general` 全程只读**：这一轮没有写用户项目一个字节（探针全部写临时副本）。

## §73 自定义模块 v7：**复杂子句**与**技术调用**（2026-10-30）

> 用户："自定义模块一定要支撑起复杂的转场调用一类的。复杂子句平时可以不用，
> 但也要能依靠自定义模块来定义它！"
> 结论：语句声明多了 `clauses` 段；清单多了 `calls` 段；两者都遵循同一条
> "**改值才写、只动那一段、认不出不猜**"。

### 73.1 语句的复杂子句（`rules.json` 的 `statements[].clauses`）

```json
{"name": "mycue",
 "pattern": [{"kind": "keyword", "text": "mycue"},
             {"kind": "variable", "column": "标题"}],
 "clauses": [
   {"keyword": "at",    "name": "位置", "kind": "words"},
   {"keyword": "behind","name": "挡住", "kind": "word"},
   {"keyword": "with",  "name": "转场", "kind": "transition"},
   {"keyword": "zorder","name": "叠序", "kind": "number"}]}
```

- **为什么不能塞进 `pattern`**：子句由关键字引入、**可以不出现、顺序可变**，
  值还可能是一段带空格/括号/引号的表达式（`Dissolve( 0.3 )`、
  `MultipleTransition([...])`）。`pattern` 是"第 N 个词"的平铺序列，装不下。
- `kind` 白名单（`story/spec_clauses.py::CLAUSE_KINDS`）：
  `word` / `words` / `expr` / `transition` / `number`。
  `transition` = 同 `expr`，但**演出预览会按它演**；其余只做展示/编辑。
- **读值** `clause_values(head, clauses)`：只认**顶层、完整词**的关键字
  （引号/括号/注释里的 `at` 不算）；值从关键字之后到下一个**已声明**关键字
  （或块冒号/行尾注释）为止。`at`/`behind` 可以是一串（逗号原样保留）。
- **写回** `set_clause_value(head, clauses, name, value)`：
  * 值没变 → **原样返回入参字符串**（逐字节相同）；
  * 有这一句 → 只换"值"那一段，别处的空格/引号/其它子句一个字节不动；
  * 没有这一句、人填了值 → 按声明顺序插在下一个出现过的子句前面，都没出现
    就插行尾（块语句插在结尾 `:` 之前）；
  * 清空 → 删关键字 + 值，只吃一个分隔空格；实际不存在时清空是 no-op；
  * 声明里没有的名字 → **原样返回**（绝不拼一个项目不认识的参数）。
- **动态列**：列名是 `head·列名~关键字`（`mycue·位置~at`），`~` 是保留分隔符。
  `Beat.spec_arg_cells` 用**全部声明子句**做边界（只给一句时 `at` 会把后面的
  `with …` 吞进值里 —— 已用判据钉住）；`fold_dynamic_columns` 认 `~` 后走
  `set_clause_value`，不写成 `位置=…`（那对自定义语句是语法错）。
- **Excel 没有项目根也能改**：列名里的 `~at` 就是最小声明；**插新子句**需要
  声明顺序，只有一格时按当前这一句处理（能从行里认出的其他关键字仍按边界
  切开，见 `spec_clauses` 的扫描规则）。
- **预览**：`app.stage._declared_transition_calls` 读 `spec_head` + 声明，
  只消费 `kind='transition'` 的子句；`TransitionCall.source='declared-clause'`。
- **零改动**：`set_clause_value` 的等式守卫在"值相同"时返回原串，所以
  "教会之前 / 之后"导出逐字节相同（铁律 #6）。

### 73.2 技术调用（`rules.json` 的 `calls`）

```json
{"calls": [
  {"head": "my_wipe", "label": "擦除",
   "columns": [{"name": "transition", "label": "转场",
                "position": 0, "kind": "transition"},
               {"name": "layer", "label": "层", "kw": "layer"}],
   "effect": "Dissolve", "seconds_arg": 1}]}
```

- 适用对象：`$ my_wipe("left", 0.6)` / `$ renpy.transition(...)` 这类 **Python
  代码里的一次函数调用**，不是 Ren'Py 语句；它们住在 `Beat.code`。
- **内置**：`renpy.transition` 不需要声明（`spec_args.BUILTIN_CALL_SCHEMAS`），
  自带两列：`transition@0`（转场表达式）与 `layer@kw`（关键字 `layer=`）；
  这是 B1"过场可编辑"的默认形状。
- `story/call_expr.py`：只按顶层逗号切实参；`read_arg` / `set_arg`；
  **同一 head 出现两次 / 多行调用 / 括号配不平 → 读空、写原样**，绝不挑一条改；
  值没变返回原串；清空关键字参数会连 `name=` 与一个逗号一起删。
- **列名**：位置参数 `head·列名@0`；关键字参数 `head·列名@kw`（`@kw` 是
  `parse_dynamic_full` 认的保留尾巴）。读表那侧不需要查声明。
- **折回**：`fold_spec_arg_columns` 只在这一拍**真的**有该 head 的调用时改
  `Beat.code` 那一行，并同步 `ordered`（与 raw 叶语句同一套"池行 == 记录行"
  纪律）；`python:` 块/多条同名调用不建可编辑列。
- **预览**：`calls[].effect`（可空）+ `seconds_arg` 才演；没写 effect 仍会把
  调用列出来，但 `transition_catalog` 如实标 `unknown`，**不假装淡入**。
  `TransitionCall.source='declared-call'`。
- 写入口：`statement_registry.declare_calls(project_root, calls, replace=...)`
  （坏 JSON 拒绝覆盖；`replace=True` = 整段替换，`False` = 按 head 合并）。

### 73.3 UI

- `ui_qt/dialog_learn.py` 第 3 块「复杂子句（可选，高级）」：表格四列
  关键字 / 列名 / 值类型 / 可省略；`built()` 的 `clauses` 与 `pattern` 一起
  过 `build_declaration`，保存调 `declare_statement(..., clauses=…)`。
  ⚠ `[]` 也算"这次编辑过"：人删光子句再保存，清单里就该没有它们。
- 头部「技术调用…」→ `ui_qt/dialog_calls.py`：调用名 / 等价效果 / 时长参数 /
  参数列（`转场@0; 层=layer`）。保存写 `calls` 段。
- 判据：`tests/test_spec_clauses.py`（38 条：声明/读值/写回/动态列/Excel 无
  注册表/技术调用/预览/两个 UI 小窗）；`tests/test_stage_complex_clause_at.py`
  （7 条）与 `test_stage_snapshot_scene_at.py`（19 条）覆盖 `show … at … behind`
  的 at 不再丢、跨章 `scene_at`/`layer_at` 继承。

### 73.4 已知边界

- 同一子句关键字**出现两次**：读值只取第一条，写回只改第一条（不猜哪条算数）。
- 只声明了 `at`、而行里还有 `with`：`at` 会读到行尾 —— 这是"只声明了一部分"
  的必然结果；要么把全部子句声明齐，要么别用列编辑（`.rpy` 原文仍逐字保留）。
- 自定义调用的**返回值/副作用**工具不知道；`effect`/`seconds_arg` 是项目
  自己声明的"等价预览"，不是对函数体的推断。


## §74 第 6 条反馈：滚轮永不改值 / 打开就窄 / 分栏按比例（2026-10-30 夜）

用户原话：

> 现在仍然是滚动会导致选择切换！！！不要这个！另外，现在打开已经打开过的项目还是这么大！

### 74.1 滚轮**永远**不改值（`wheel_guard`，全项目生效）

- **规则只有一条，没有例外**：任何 `QComboBox` / `QAbstractSpinBox` /
  `QSlider` / `QDial`，滚轮都不许改值；那一格滚轮**显式转交**给外层
  `QAbstractScrollArea` 的 viewport（页面照常滚）。唯一放行的是
  **候选列表已经弹开**（`QComboBox.view().isVisible()`）—— 那时滚的是列表本身。
- 为什么删掉"有焦点就放行"（2026-10-29 的旧规则）：用户刚点过「说话人」，
  焦点还在它身上，滚页面时鼠标划过它就换了人 —— 这正是"滚动会导致选择切换"。
- 为什么还要全局兜底：裸 `QComboBox`（**可编辑**时连焦点都不用）就会
  被滚轮换值，并且**把滚动吃掉**（offscreen 实测：index 1→2、滚动条 0→0）。
  全项目 60+ 个组合框，靠"记得用子类"必漏。
- 实现：`ui_qt/wheel_guard.py`
  · `WheelSafeComboBox`：滚轮永不改值（卡片「说话人 / 背景 / 音乐 / 语音」四格用它）；
  · `WheelValueGuard` + `ensure_installed()`：挂在 `QApplication` 上的事件过滤器
    （幂等，靠 `app._storygraph_wheel_guard` 去重），在 `MainWindowQt.__init__`
    里装一次，**所有窗口**（含对话框）都受保护。
- 判据：`tests/test_wheel_guard.py`（无焦点不改值 / 滚轮转交滚动区 /
  **有焦点也不改值** / 四格真用了这个类 / 裸 combo 被全局守卫拦住 /
  幂等 / 弹开的列表放行）。

### 74.2 窗口大小：**每次恢复都压**到 `DEFAULT_WINDOW_SIZE`（1200×800）

- 现象：真机 `session.json` 里 `geometry=[1392,804]` 且 `layout_version=2` ——
  上一轮的一次性迁移**跑过了**，但用户随后把窗口拖大/最大化，退出时又把大尺寸
  存了回去（版本号已经是 2），下一次启动"又这么大"。
- 规则：`window_state.narrow_layout` 对 **geometry 不再看版本号**，
  每次恢复都 `min(记忆, DEFAULT_WINDOW_SIZE)`（**只压大的，小的照旧**）。
  记忆只用来把窗口**记小**，不用来记大。卡片栏迁移（`LAYOUT_VERSION=3`、
  `LEGACY_CARD_SHARE=0.45`：右栏 ≥45% → 默认窄栏）仍然只做一次。
  ⚠ 门槛从 0.60 降到 0.45 是因为**对半（50%）的旧摆法卡在旧门槛下面、
  一次都没迁移过**（用户："还是一模一样……都说了要变窄一些"）。见 §34 的 ⑦ 条。
- `reopen_project` 的顺序固定为：**`_restore_to_screen` → `_restore_docks` →
  开项目/载章节/切页 → `_restore_splitters`（并 `singleShot(0)` 再补一次）**。
  在页面还藏着、窗口还没 resize 的时候摆分栏，量到的是"最小宽兜底"。
- 判据：`tests/test_ui_width.py::TestLegacyLayoutMigration`
  （老 1440/813 被压；**带 version=2 的对半也被压**；更小的窗口不被放大；
  窄比例（30%/40%）原样保留；端到端 `reopen_project` 两条；`big_screen` fixture
  把 offscreen 的 800×800 换成用户那块 1440×852，否则 1200 会被屏幕压成 752，
  判据假绿）。

### 74.3 分栏按**比例**摆，不按绝对像素（`WindowStateMixin._scale_sizes`）

- 记忆里的 `split_card=[583,584]` 是"当时那个 1392 窗口"的像素；窗口被压到
  1200 后分栏只有 ~906 宽，`setSizes([583,584])`（和 1167）会被 Qt **从右边硬砍**：
  实测 `[545,357]` / `[172,340]`，用户摆的对半完全失真。
- 现在先按当前分栏宽度等比缩（`_scale_sizes`，四舍五入零头补最后一栏），
  再 `setSizes` —— 实测 906 宽摆出来 `[451,451]`。

### 74.4 章节**绝对不能跨项目**载入（`session_store.resolve_chapter`）

- 真机两个同名项目（`...\RenPyTest\sr_general` 与 `...\sr_general`）共用一份
  `last_chapter` 绝对路径 → 打开 A 项目却载入 B 项目的章节，屏幕内容和
  "打开的项目"对不上，改起来还会写回 B。
- 规则：绝对路径必须**在项目根里面**（`_inside`，`Path.resolve().relative_to`）
  才认，否则退回 `last_chapter_rel`（相对项目根那份）；两份都不成立 → `''`
  （只开项目、不载章节）。
- 判据：`tests/test_session_memory.py::test_a_chapter_from_another_project_is_never_loaded`。

## §75 第 8 条反馈："JSON 能不能显式化" —— 映射的**表格页**（2026-10-30 深夜）

用户原话：

> json的自定义能不能用工具显式化……？本质上它就是键值对嘛！也就是类似于之前
> 探测未定义角色名称的样子，弄一个显化的UI，然后点击保存的话再存为json……？
> 因为实际上使用json定义对非程序员门槛有点高。

### 75.1 定位：JSON 是**产出格式**，不是人的输入方式

- 新增 `ui_qt/mapping_editor.py`（表，只摆和收）+ `assets/mapping_edit.py`
  （纯逻辑：读成行 / 改一格 / 增删移 / 校验 / `to_data`）。判定仍是
  `assets/asset_identity.resolve_image` 的事，这里不重写第二套。
- 页签顺序固定：**逐条编辑 / 映射文件 / 资源对照表**，下标用
  `mapping_dialog.TAB_EDIT / TAB_FILE / TAB_TABLE`（**不许写魔法数字**）。
- 表**只改内存里的工作副本**；写盘仍然只有「保存到项目根」这一条路
  （`prepare_save` 出 diff → `confirm_save` 落盘 + `.bak`）。表**不 import**
  `save_mapping`，它没有写文件的能力。

### 75.2 四段 = 四张表（`mapping_edit.SPECS`）

| 段 | 形状 | 第一列 | 其余列 | 顺序有意义 |
|---|---|---|---|---|
| `characters` | `{tag: {...}}` | 角色 tag | 显示名 / 别名（数组）/ 头像 | — |
| `images` | `{id 或相对路径: {...}}` | 这张图 | 是什么（下拉）/ 角色 / 属性 | — |
| `rules` | `[{...}]` | 第几条 | 路径通配 / 是什么 / 角色 / 属性 | **是**（先中先得，能上移下移） |
| `attributes` | `{维度: {label}}` | 维度 | 显示名 | — |

- `tag` 是 `char` 的旧写法、`attrs` 是 `attr` 的旧写法：改这一格**改原来那个键**，
  不另加一个（否则一条里两个意思，谁赢看读取顺序）。
- `aliases` 用 `|` / 逗号 / 顿号 / 分号分隔，存回时是**数组**。

### 75.3 三条硬规矩（都为了"别改坏人的文件"）

1. **只动它认识的键**：工作副本是原文件的深拷贝，不认识的顶层键 / 条目字段
   原样保留；没改过的条目 `to_data()` 逐字节等价 ⇒ diff 里只会有人真改的行。
2. **空行不落盘**：默认键（`新角色` / `新图片` / `新分组`）+ 一个字段都没有
   ⇒ 丢掉，`blank_rows()` 报给界面。**人改过键的空行不丢**（那是他的决定）。
   人把某段条目删光 ⇒ 该段的键整个去掉；**原本就空**的段（`"images": {}`）保留。
3. **不偷偷补 schema**：`to_data()` 只在"确有内容但没有 schema"时补
   `storygraph.mapping/1`；空表不补。

### 75.4 改一格**不许重建表**

`itemChanged` 里重建会在人打字打到一半时把焦点抢走。规则：
**改一格 → 只改模型 + 重算问题条**；只有加 / 删 / 改键 / 上下移才重建。
判据 `test_typing_does_not_rebuild_the_table`（同一个 `QTableWidgetItem` 还在）。

### 75.5 界面细节（踩过的）

- **列宽定死 + 末尾一条空列吃满剩余宽度**：伸缩有内容的列 = 窗口一宽就有一列
  被拉成六七百像素的空格子；规则段伸缩第一列更糟（`#` 被拉成 380px）。
- **中文 UI 文案里不许出现 markdown**：QLabel 是纯文本，`**粗体**` 和 `` `代码` ``
  会**原样显示**（第一次截图就看见了）。
- 「是什么」列是下拉，值不在 `KINDS` 里时**原样显示**（人手改的 JSON 不许被
  悄悄改成第一项）；`角色` 列是可输入 + 有建议（也允许 `<tag>` 占位符）。
- 「资源对照表」多了 **「补进映射…」**：按选中的行填 `kind` / `char` / `attr`，
  键 = 资产 ID（**唯一**时）否则相对路径；同一张图点两次是**并进**不是复制。
- 生效源是 Excel ⇒ 表头一条横幅（"你改的是 JSON，保存只写快照"）；
  文件读不出来 ⇒ **整页禁用**（表是空的，一存就把文件盖成空白）。
  两条都由 `_load_editor` 一处决定。

### 75.6 判据

- `tests/test_mapping_edit.py`（43 条）：行/列、改一格、旧写法、重命名、移动、
  空壳、未知键、schema、问题分级、引用计数、写出去能被 `asset_mapping` 读回。
- `tests/test_mapping_editor_ui.py`（27 条）：页签顺序、四段切换、打字改数据、
  不重建表、JSON 预览跟随、保存写的是表、未确认不写、对照表补进来、
  Excel 横幅、坏文件禁用、重读丢弃改动并说出来、表底那个保存按钮走同一段。

## §76 选择器左栏的第二种口径：**按文件夹**（2026-10-30 深夜）

用户原话：

> 文件筛选与分类方面……要提供两种分类方式，一种就是按文件夹分类排序，
> 另一种就是原来的按图片，背景，UI 等那种分类排序……这两种方式要能够随时切换

### 76.1 两套口径并列，谁也不是默认偏好

| 口径 | 回答 | 唯一实现 | 界面 |
|---|---|---|---|
| 按分类 | "这是什么"（立绘 / 背景 / UI…） | `app.beats.categorize_assets`（身份层） | 左栏组名照旧，**一个字没改** |
| 按文件夹 | "它在哪个文件夹" | `app.picker.folder_groups` | 左栏缩进树 + 每层张数 |

开关是 `ui_qt.asset_picker.AssetPickerDialog._group_by_box`（`GROUP_MODES =
('按分类', '按文件夹')`）。**不写进 QSettings**：这一眼怎么找不是长期偏好，
记住它 = 下次打开莫名其妙是一屏目录。判据
`test_the_choice_is_not_written_into_the_prefs`。

### 76.2 `folder_of_row(row, project_root)` 的三种返回

| 返回 | 意思 | 落进哪一组 |
|---|---|---|
| `'images/ling'` | 正常文件夹（相对 `game/`、posix 分隔符） | 同名组 |
| `''` | 文件**直接躺在 `game/` 下** | `（根目录）` |
| `None` | 这一行**没写文件路径**（角色 / 变换 / ATL 块） | `（没有路径）` |

归一规则（**全部字符串操作，不 stat 磁盘**）：

- `game/images/x.png` 与 `images/x.png` → 同一个文件夹（后者按 Ren'Py 约定
  相对 `game/`，见 `asset_identity.normalize_relpath` 的 docstring）；
- `sr_general/game/...`（带项目目录名，真项目 74 行）→ 由 `normalize_relpath` 去掉；
- 反斜杠 / 绝对路径（在项目根下）都认；路径列名认
  `FOLDER_PATH_KEYS = ('Path', 'path', 'File', 'file', 'Filename', 'filename')`。

### 76.3 `folder_groups(registry, project_root, *, types, limit, include_subfolders)`

- 返回 `{文件夹: [(asset_type, asset_id), …]}`（累加口径：**每一层都含它下面
  所有子文件夹的图**）。`include_subfolders=False` 才是"只看这一层"。
- 排序：`（根目录）` 最前 → 逐段小写比名字（父在子前）→ `（没有路径）` 垫底。
- 大小写不拆行（Windows 上 `GUI/` 与 `gui/` 同一个目录）。
- `limit` 与 `groups()` 同义（防极端项目）。
- 面板那套两级树（`assets.asset_tree.group_assets(group_by='folder')`）**不改**：
  它是"文件夹 → Category"的浏览视图，key 不归一；两边的口径别混着改。

### 76.4 切口径时"别把人甩走"（`_match_group`）

老组那批图 → 新口径里**交集最大**的组；交集一样大取**更具体**的那个
（`pomodorro/images` 优于 `pomodorro`）。一条都对不上 ⇒ 回「全部」，
不硬凑空组。

### 76.5 左栏文字（`_folder_labels`）

- 缩进画层级 + `（张数）`；
- 叶子名撞车时补**能区分的最短一段父目录**：`overlay（3） ← dutui` /
  `overlay（2） ← phone`（真项目 `gui/dutui/overlay` 与 `gui/phone/overlay` 同名）；
  不撞名不加尾巴；完整路径在 tooltip。
- 分类口径不给组名加数字（`立绘：凌跃捷（ling）` 原样，别动既有界面）。

### 76.6 判据

- `tests/test_picker_folders.py`（23 条）：三种返回、四种 Path 写法归一、
  累加口径 / 只看这一层、排序、大小写、`types` / `limit`、空注册表、不 stat 磁盘、
  两套口径同时可用。
- `tests/test_asset_picker_folders_ui.py`（18 条）：默认按分类且组名不变、不写偏好、
  目录树与张数、父目录看得见子目录、`（没有路径）` 可达、空桶不占行、
  同名叶子能分清、切过去/切回来停在同一批图、用途过滤在两种口径下都生效、选音乐时也有文件夹（没有「用途」那一档）。
- 既有选择器测试（`test_asset_picker_resolve.py` / `test_asset_picker_ui.py` /
  `test_picker_and_refresh.py`）不改一行仍全绿。

## §77 映射表格页的"人话"口径：显示值 / 真值 / 文案唯一真源（2026-10-30 深夜）

用户原话：

> 资源映射这张表格不能够太学术化啊！tag 一类的……谁知道你是怎么叫的呢……？？？
> 我需要一个编剧跟文案都能看明白的表！而不是什么 tag 什么值一类的……

### 77.1 屏幕上说什么（旧名一律退役）

| 位置 | 屏幕上的字 |
|---|---|
| 页签 | `逐条编辑` / `原始文件` / `工具怎么认的`（唯一真源：`mapping_dialog.TAB_TITLES`） |
| 左栏四段 | `角色` / `单张图片` / `一类图片` / `表情怎么叫` |
| 第一列 | 角色段 `剧本里的写法`、图片段 `是哪张图`、规则段 `顺序`、属性段 `分组（工具内部）` |
| 其余列 | `名字` / `别的写法` / `头像图` · `它是哪种图` / `属于谁` / `表情／状态` · `文件在哪儿` · `界面上叫它` |
| 认图结果页 | `文件` / `工具认为它是` / `角色` / `表情` / `凭什么这么判` / `能直接写进剧本吗` |

**不许再出现在屏幕上**的词（`tests/test_mapping_edit.py::test_no_jargon_on_screen` 钉着）：
`tag`、`ID`、`glob`、`kind`、`attr`、`schema`、`键`、`值`、`通配`、`维度`、
`资产`、`精确覆盖`。机器那边叫什么写在 `Field.tech` / `Section.tech`，
**只进 tooltip 第二行**（`技术写法（给开发者）：…`）。
`<tag>` / `<stem>` 这类**要照抄的写法**不算行话（扫之前先摘掉）。

### 77.2 一格两个值：显示值 vs 真值

- `QTableWidgetItem.text()` = 人话（`背景`、`立绘`、`角色预览图`）；
- `Qt.UserRole` = 写回 JSON 的真值（`bg`、`sprite`、`preview`）。

`MappingEditor.cell_text()` 读人话、`cell_value()` 读真值；
`set_cell()` 传的是**真值**（和文件一个口径），显示由界面翻
（`KIND_TEXT` / `KIND_BY_TEXT` 由 `KIND_CHOICES` 派生）。

⚠ `item.setData(UserRole, …)` **也会发 `itemChanged`** —— 程序同步格子
（`set_cell` / `_revert`）必须整段压在 `_filling` 底下，否则 `_on_item_changed`
会拿旧文字把模型写回去（实测：返回值成功、数据没变）。

### 77.3 文案住在 `assets/mapping_edit.SPECS`

`Section.label / hint / key_label / empty / tech`、
`Field.label / hint / tech`、`KIND_CHOICES = (短名, 真值, 悬停长解释)`。
界面代码里**不许**再硬写表头（加一段只改 SPECS + `SECTIONS`）。

- `empty`：这一段 0 条时显示的"怎么开始"，摆在表**上方**（真机项目常常一份
  映射都没有，空表格什么都不说 = 门槛）；
- 表头 / 左栏 / 每一格都有悬停：一句人话 + 一行技术写法；
- 问题条（`MappingEdit.problems()`）也是人话：「没选『它是哪种图』—— 工具会按
  文件夹和文件名自己猜。」

### 77.4 判据

- `test_no_jargon_on_screen` / `test_every_section_explains_how_to_start_when_empty`
  （`tests/test_mapping_edit.py`）
- `test_no_jargon_in_the_tab_names` / `test_the_page_says_what_it_is_for` /
  `test_the_headers_explain_themselves` / `test_an_empty_section_tells_you_how_to_start`
  （`tests/test_mapping_editor_ui.py`）
- `test_switching_sections_changes_the_columns`：同一格 `cell_text == '立绘'` 且
  `cell_value == 'sprite'` —— 两个都得对，才算既好看又没写错文件。

### 77.5 从卡片警告一步落到那一行（闭环）

- `sprite_grid._open_mapping()` 把 `item['image']`（`ling happy`）交给注入的回调；
  回调收不收参数由 `_accepts_one_argument(fn)` 按签名判 —— 无参回调照旧可用，
  **不许用 `try: fn(spec) except TypeError: fn()`**（可能执行两遍）。
- `main_window.open_mapping_from_card(win, spec='')` → `open_mapping_dialog(...,
  focus_image=spec)` → `MappingDialog(..., focus_image=...)`。
- `MappingDialog.focus_image(spec)` 的匹配顺序：**资产 ID → 相对路径 → tag+属性**；
  表里没有、资源表里有 ⇒ 按 `_mapping_row_for()` 先补一条并选中；
  两边都没有 ⇒ 落到「单张图片」+ 一句人话（不静默、不瞎猜）。
- 选中一行 ≠ 当前行：`_rebuild(select=[…])` 里补 `setCurrentCell(last, 1)`，
  否则 `currentRow()` 是 -1，键盘和焦点都不在那儿。
- 判据：`TestFocusFromTheCard` / `TestTheCardButtonCarriesTheImage`
  （`tests/test_mapping_editor_ui.py`）。

## §78 立绘代号：服装字母 + 表情编号（2026-10-31 凌晨）

用户原话：

> 我总觉得他们其实不是这个意思……你看看真实项目之中的注释：#B7 这些注释，
> B 对应的是服装，而 7 对应的是这个角色的第 7 号表情，那请问我该如何通过你这个
> 映射表把 lan swim_suit angry 映射为 lan B 7 呢……？不太方便吧？？？

### 78.1 唯一真源

| 问题 | 真源 | 模块 |
|---|---|---|
| 第 7 号表情是什么 | 角色文件夹里的 `对应.txt`（有几份读几份） | `sprite_codes.parse_expression_line` |
| B 是哪身衣服 | 剧本注释 `# 表情B7` + 紧跟的那条 `show` | `sprite_codes.scan_script_text` |
| 哪些属性是表情 / 服装 | `layeredimage <角色>` 的 `group` 归类 | `sprite_codes.parse_layeredimage` |
| 两段怎么落盘 | `outfit_codes` / `expression_codes`（顶层数组） | `mapping_edit.SPECS` |
| 换算 | 查表（表错 → 换算错，不回退猜测） | `sprite_codes.translate` |

### 78.2 剧本注释的读法（三条规矩）

1. **一组一个属性**：`show ye hi` 只替换表情组，服装保持上一条
   （`# 表情B7` 说的就是"上一身衣服 + `hi`"）；`hide` 才清空。
2. **注释取最后一个代号**：`# 表情B6换到表情B7` → B7。
3. **跨角色声明过的属性不算服装标记**：`show ye uh` 里的 `uh` 是 lan 的表情
   （手误），不是 ye 的服装。只有"谁都没声明过"的（`aa`）才当服装标记。

### 78.3 `对应.txt` 的三种真实写法（都认）

```
7 不在意-indifferent          # 中文在前（lan / ye）
2 close_happy-闭眼微笑        # 属性在前（ling）
表情1-哈哈 — haha             # 带「表情」前缀 + 破折号（xing）
```

判据：按破折号切开，**纯 ASCII 标识符**那半是属性，另一半是中文名。
两份表的编号不一致时**不裁决**：用"和剧本注释对得更多"的那份，并说出来。

### 78.4 自动建表的边界（不许做的事）

- 只出现过一次的字母 → **留空**并说"证据太弱"（真实项目有 `show lan p happy`）；
- 剧本里没出现过的服装 → 出一行**空代号**，说明"照美术需求表补"；
- 同一个字母指向两个属性 → 按票数留一个，另一个留空并说一句；
- 编号表里没有、注释里出现 ≥2 次的号 → 补一行并提醒（可能是表漏了）；
- 已有的行**一条都不改**（自动填只填空表；点按钮也只补缺的）。

### 78.5 Excel / JSON / 界面

- Excel：两张表 `服装代号` / `表情编号`（`SHEET_SPECS` 认中英文别名），
  空白模板 `blank_template()` 里六个键都在 → 「存一份空模板…」导出的 .xlsx
  六张表都有表头；
- 界面：「服装代号 / 表情编号」两段上方有换算框（两种写法都认，缺什么说什么），
  旁边「按项目自动填」；
- 打开映射窗时若两段都空，**自动建表**并在提示条报数（真机 0.2 s / 103 行）。

### 78.6 判据

- `tests/test_sprite_codes.py`（41 条）：三种编号写法 / `attribute x default:` /
  一组一个属性 / 跨角色手误 / 两份表对不上 / 证据弱就留空 / 预览变体不算角色 /
  两个方向的换算 / 缺角色缺字母缺编号时都要说人话。
- `tests/test_mapping_editor_ui.py`：`TestCodesPage` / `TestAutoBuildTable` /
  `TestTemplateExport`（12 条）。
- `tests/test_mapping_excel.py`：两张新表的往返（`GOOD` 里带上了这两段）。

### 78.7 写出去的表头是中文

`mapping_excel.HEADERS` 是写表表头的唯一真源（`路径 / 类型 / 角色 / 属性 /
能否 show / 显示名 / 代号 / 编号 / 剧本里怎么写 / 说明`）；读的一侧 `FIELDS`
的别名两套都认，所以人做的英文表头和工具写的英文/中文表头都能读回来。
判据：`test_表头写人话_冻结_下拉`（旧的英文表头断言已随之更新）。

> **第四种写法（2026-10-31 补）**：`lan` 的 `中英对应.txt` 每行末尾还带一张图的
> 路径（`1 闭眼笑-close_happy images\characters\lan\close_happy.png`）。第一版
> 没认出来 → lan 的两份编号表只剩一份，"两份对不上"这件真事就**看不到了**，
> 于是 `# 表情A9`（脚本说 `angry`）会被算成 `stare`。现在行尾的图片路径先摘掉，
> 再判哪半是属性；真机结果：lan 用 `中英对应.txt`（对上 23 处，`对应.txt` 7 处）。

## §79 词汇表：引擎写法 ⇄ 人话（2026-10-31 凌晨）

### 79.1 唯一真源

`storygraph/assets/vocabulary.py`

| 东西 | 在哪 |
|---|---|
| 有哪几类 | `KIND_SPECS`（位置 / 音乐 / 音效 / 语音 / 背景 / 角色 / 过场 / 服装 / 表情） |
| 一条长什么样 | `Term(kind, id, label, code, char, note)`，`id` 是**写进剧本的真值** |
| 从映射拼表 | `from_mapping(data)`（`vocabulary` 段 + 服装/表情两段投影） |
| 两个方向 | `label()`（写法→人话）/ `ident()`（人话→写法） |
| 搜 | `search()`（中文 / 编号 / 原名都命中） |
| 唯一匹配 | `_unique()`：归一后命中 ≥2 条就**不猜** |
| 学编号 | `learn()`：`# bgm18` + 下一条 `play music`（背景 / 音效同） |

### 79.2 三条规矩

1. `id` 永远是真值（屏幕上换名字不改剧本）；
2. 查不到**原样显示**（词汇表是补充，不是白名单）；
3. 模糊匹配只在**唯一**命中时认。

### 79.3 存储与界面

- JSON：顶层 `vocabulary`（数组）；Excel：`中文叫法` 表（`是什么/原名/叫什么/
  编号/角色/备注`，`是什么` 写中文也认）；
- 界面：`mapping_edit.SPECS['vocabulary']`，`是什么` 是**自带选项的下拉**
  （`Field.choices`，这轮新加的通用能力）；
- 卡片：`sprite_grid` 用 `provider.vocabulary`（窗口挂在 `_for_tag` 上，
  与 `engine_facts` / `layer_model` 同一条通道）；位置 chip = `transform`，
  服装/表情 chip = `costume`/`expression`（按 tag 查，带角色作用域）；
- chip 文字最多 10 字 + `…`，全文在 tooltip（右栏 340px 硬预算）。

### 79.4 还没接的

（2026-10-31 第二轮已全部接上，见 §80。此条留作历史。）

---

## §80 显示值 ≠ 真值：所有下拉 / 列表的唯一一层（2026-10-31）

### 80.1 一个原语、一条规矩

`storygraph/ui_qt/labeled_choices.py`

    QComboBox.addItem(显示文本, 真值)      # 真值进 UserRole

> **一个下拉/列表的"真值"永远优先取 `UserRole` 里的数据；没有数据才用显示的文本。**

| 函数 | 干什么 |
|---|---|
| `Choice(value, display, tip)` | 一个候选（`text` = 显示，`value` = 真值） |
| `as_choices(items)` | 归一（见 80.2） |
| `fill_combo(combo, items, current, blank=, display=)` | 重填下拉，保留手写值 |
| `combo_value(combo)` | 读**真值**（可编辑下拉的关键，见 80.3） |
| `select_value(combo, value)` | 按真值选中（屏幕显示 display；不在候选里就挂"当前值"那一行） |
| `set_display(combo, fn)` / `show_current(combo, value)` | 挂在控件上的"这串字该叫什么" + **复合值**那一行 |
| `say(vocab, kind, ident)` | 查词汇表（`kind` 可传一串，按顺序问） |
| `labeled(items, vocab, kind)` | 给一批候选补显示名 |
| `pairs(values, {值: 显示})` | 值序列 + 名字表 → `[Choice]` |
| `legacy_pairs(items)` | **老形状 `(值, 说明)` → `Choice(值, '', 说明)`**（全库唯一一处） |
| `session_vocabulary(session)` | 从会话取词汇表（拿不到 → 空表） |

**复合值**（`[音乐] night fadein 1.0`）：`Vocabulary.label_in_text()` 只翻壳里
那个名字（`[音乐] 18 夜 fadein 1.0`），壳与参数原样；专门用于显示，写回用原串。

### 80.2 形状（**一个形状一种意思**）

    'bgm/a.ogg'                      → 显示 = 真值（老调用方一字不变）
    ('bgm/a.ogg', '18 嗯？')          → (值, 显示)
    ('bgm/a.ogg', '18 嗯？', '说明')  → (值, 显示, 提示→tooltip)
    Choice(值, 显示, 提示)            → 显式

`set_asset_choices(field, choices)` 的老契约 `[(值, 说明)]` **照旧**，
它在自己那一处调 `legacy_pairs()` 换算 —— 所以 `labeled_choices` 里
二元组只有"值 + 显示"一种解释。

### 80.3 `combo_value` 的判定顺序（别改）

1. 当前项有 `UserRole` 字符串 **且输入框里还是它那句显示文本** → 就是它；
2. 输入框里的字恰好等于某一项的显示文本**且唯一** → 认那一项；
3. 都没有 → 输入框里那串（手写的自由值必须能过）。

⚠ 第 1 步**必须同时比文本**：可编辑下拉在改字之后 `currentIndex` 仍可能
指着上一项（`setEditText` 实测如此），只看 index 会把用户刚打的
`bgm/别的.ogg` 读成旧值 —— 屏幕上是他的字，改错的是剧本。

### 80.4 谁在用（四处）

| 面 | 入口 | 词汇表 kind |
|---|---|---|
| 背景 / 音乐 / 语音 / 音效那几格 | `window_assets._refresh_asset_choices` → `labeled(legacy_pairs(…))` | `VOCAB_KIND`（**image → bg**，其余同名） |
| 说话人下拉 + 参与角色列表 | `window_card._show_index` → `pairs(chars, labels)`；`_fill_speaker` 读 `UserRole` | `character` |
| 参数格（`with` 的「转场」等） | `window_card._sync_spec_args` → `labeled(…, arg_kind_of(col))` | **`choices_from` 就是 kind**；`transform` → `('transform','cue')` |
| 立绘 chip 的右键候选 / 参数格「⋯」 | `choose_from(..., [Choice])`（筛选中英文两栏都命中） | `transform` / `cue` |

`session.vocabulary()` 是**唯一访问器**：映射 + `rules.json` 的
`calls[].label`（映射优先）。谁也别再自己 `mapping_cached()` ——
那个函数返回 `(data, warnings, path)` **元组**，当对象用不会抛，
只会永远拿到空表（§80.5）。

⚠ 转场那一格的**值是整个调用表达式**：`spec_args.positional_tokens` 括号内
不切（`with Fade(0.2, 0.0, 0.2)` 是一个位置参数，不是三个）。没有这一条，
那格读出来是半截 `Fade(0.2,`，认不出、也显示不出中文。

### 80.5 四个坑（判据钉着）

1. `mapping_cached()` 是元组不是对象：把它当 `state.data` 用 →
   **永远空表且不抛**（真机上"一个中文都没有"）。
   判据：`test_vocabulary_wiring.py::test_会话读得到项目里的映射`（走真文件）。
2. 可编辑下拉改字不改 `currentIndex` → 见 80.3。
3. 2 元组两种含义 → 见 80.2。
4. 背景的值是**图像 spec**（`bg classroom`）：`Vocabulary.label()` 整串
   查不到时再试最后一个词；`ident()`（反查）**不许**这么做（会吃掉 `bg`）。
5. `dynamic_spec_columns` 从前丢了 `choices_from` → `with` 的「转场」一格
   **没有任何候选**。补回去了，判据
   `test_转场候选里有过场调用`。

### 80.6 一条边界（刻意的）

**能改的格子翻译，摘录保持原文**：左栏摘要（`DY：台词`）、卡片里
「当前舞台（这一拍生效）」那几行、代码块 —— 都是**剧本本身的摘录**，
与 `at i11` 一样原样显示；只有"能选的格子"（下拉 / 候选列表 / chip）
才翻成中文。两边混着翻的话，人对不上自己写的那行。

### 80.7 判据

`tests/test_vocabulary_wiring.py`（25 条，含复合值与"当前值"那一行）；
老判据全绿（`test_choice_picker` 的纯函数口径、`test_spec_groups` 的组标题、
`test_sprite_slots` 的完整路径候选都没动）。

## §81 资源映射的「角色」段：空 = 不覆盖，不是没读到（2026-11-01）

### 81.1 一句话

映射的 `characters` 是**覆盖层**，不是户口本：

    留空  → 名字照资源表 asset_tables/characters.xlsx（ImageTag / Name / Aliases）
    写了  → 以映射为准（资源表再改，界面不跟）

判据在 `asset_identity._display_of()` / `identity_of()`：`mapping={}` 时
`ling_happy` 照样给 `display='凌跃捷'`。所以编辑页显示「角色 0」的准确含义是
**"没有覆盖"**。

### 81.2 为什么代号两段会自己长出来、角色段不会

| 段 | 信息的家 | 开窗自动建表 |
|---|---|---|
| 角色 | `characters.xlsx` | ✗ 抄了会盖住表（改表不生效） |
| 单张图片 / 一类图片 | 路径 + Tag 列推断 | ✗ |
| 服装代号 / 表情编号 | 只在 `对应.txt` + 剧本注释 `# 表情B7` | ✓ `apply_codes()` |
| 中文叫法 / 表情怎么叫 | 只在映射 / 有默认名 | ✗ |

**能抄的只有"别处没有"的那两段。**

### 81.3 入口与判据

- 角色段空着时，`mapping_editor._empty_text()` 说清"表里有 N 个角色
  （举几个名字）+ 空着就行 + 写了以这里为准"；名字按**资源表顺序**取
  （字母序会把 `ALL / CAT / DOG` 排到句首）。
- 「把资源表里的角色写进来」按钮（`MappingEditor.import_characters()`）：
  **只加不改**、名字等于写法的跳过、别名写成数组、顺序照表；底部提示
  必须说"还没保存 + 抄进来以后这一段优先"。
- `mapping_dialog._known_char_entries()` = `known_characters(registry)`
  （缓存）；`_known_chars()` 只返回 `{tag: 显示名}`（**不许再 `str(dict)`**）。

判据 `tests/test_mapping_chars_from_table.py`（12 条），重点两条：
`test_existing_rows_are_never_overwritten`、`test_copying_says_it_is_not_saved_yet_and_who_wins`。

### 81.4 文案的真名（别再写错）

页签叫「工具怎么认的」，按钮叫「加进表里…」——
`ME.SPECS['images'].empty` 从前写的是「工具认成了什么」「补进映射…」，
人都找不到那个入口。判据
`test_the_other_sections_explain_why_zero_is_fine`。

## §82 随包的两份预设：真项目扫出来的（2026-11-01）

### 82.1 三件套

`storygraph/presets/` 里的文件既**随包发**（`packaging/make_release.py` 的
`extras` + `REQUIRED_BASE`；演出专用版走 `make_stage_release.PRESET_FILES`
→ `项目预设/`），也是 `app/stage_edition.py` **补缺**时的来源。三件：

| 文件 | 内容 | 会自动套进项目吗 |
|---|---|---|
| `storygraph.rules.json` | `show` 的 角色/服装/表情 三列候选 | 会（演出专用版，缺才补） |
| `storygraph.mapping.json` | 立绘代号表：服装字母 + 表情编号 | **不会**（映射写的是"这个项目的哪张图是谁"） |
| `layout.json` | 剧情图节点坐标 | 会（同上） |

### 82.2 值从哪来：`tools/build_presets_real.py`

一条命令从真项目重建前两份（`--dry-run` 只打印）。四条真源，一条都不猜：

1. 表情编号 ← `game/images/characters/<tag>/对应.txt`（四种写法都认；
   两份都在时由剧本注释投票挑一份）；
2. 服装字母 ← 注释 `# 表情B7` 后面那条 `show` 的服装属性（证据 <2 处留空）；
3. 中文名 ← 项目自己的 `charactercenter.rpy`（`CC_OUTFIT_LABEL` /
   `CHARACTER_NAMES`）与 `definitions.rpy`（`default x_name` +
   `define x = DynamicCharacter(..., image='tag')`）；
4. 角色候选 ← 剧本里真的 `show` 过的**角色 tag**（有
   `images/characters/<tag>/` 或声明过 layeredimage 的才算；
   `show bgoceanblur` 那种"一张图一个字"不是角色）。

**服装 A/B 不是按 `CC_OUTFIT_CYCLE` 的顺序排的**：那是界面上的轮换顺序
（ling 循环是 `aa / 泳装 / 叉腰`，剧本里 `B` 却是叉腰）。唯一的判据是注释。

### 82.3 `角色` 那格不封闭（`limit: false`）

`show` 的第一个词是**图片 tag**：真项目里有 `show bgoceanblur` / `show icon` /
`show cg cg01`。封闭枚举会把它们判成非法 —— 与 `pattern.py`
"候选是下拉里的备选，不是必填项"是同一条规矩。`服装` 本来就不封闭，
`表情` 的 68 个枚举与真项目逐一比对是完整的，维持封闭。

### 82.4 只剩一份编号表也可能错（`scan_project` 的新提示）

以前只在**两份表打架**时说话。真项目只剩一份时它一声不响，而
`sr_general` 的 lan 正是这种：`对应.txt` 重新编过号，剧本注释还按老编号写
（26 处证据只对上 5 处）。现在证据 ≥2 且对上不到一半就记一句
"编号表「X」和剧本注释只对上 N/M"——**一行数据都不改**，只把数字交出去
（工具不替人裁决；判据 `test_sprite_codes.py` 两条：只剩一份表也要说 /
对得上就不吵）。

### 82.5 判据

`tests/test_presets_real.py`（14 条）：随包映射能被 `load_mapping` 读进来
（0 警告）、两段形状、**证据不足的字母必须是空的**（xing / clothes2 /
泳装 / 汉服）、映射里的每个属性都在 rules 的候选里、角色候选含 5 个真 tag、
角色格不封闭，最后用两条真题收口：`lan B7 → show lan base_hand_up indifferent`、
`ye A1 → show ye aa close_happy`。
