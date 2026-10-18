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
  ⚠ **绝不写进项目仓库**（否则进 git diff、多人协作互相打架；判据
  `test_it_does_not_write_into_the_project_repo` 扫项目目录证明"一个新文件都没加"）。
- **项目里那份**：`<项目根>/storygraph.session.json` —— 工具**只读不写**，
  字段**覆盖**用户级那份（团队可以放一份布局一起评审）。这就是用户说的
  "项目内部有保存布局，一并读取"。
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
   **落盘原语=`story.paths.write_text_atomic`**（2026-10-17，P1-2）：同目录临时
   文件 → `newline=''` 写（**不许把 LF 翻成 CRLF**）→ fsync → `.bak` →
   `os.replace`。禁用 `Path.write_text` 直接写 `.rpy`。守卫 `test_atomic_write.py`。
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
    表按"一行一格"存，没有长度上限。老表的隐藏列仍然**只读**兼容。

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

### 三条"平台期"约束（改代码前先读）

1. **判据要对着引擎**：工具自己前后一致 ≠ 产物能跑。任何"生成 .rpy 的新路径"
   都要能被闸门 ② 覆盖。
2. **写回的区间/行号只有一份真源**：`code_blocks` 用"raw + code 池"计行，
   `ordered` 里还有 `opaque`（不进 `compose_code`）—— 混用就是块被劈开。
3. **"看起来像语句"不是语句**：`menu = nvl_menu` 是 python 赋值；
   判据在 `beat_model.is_python_assignment`（渲染层与构建层共用一份）。

