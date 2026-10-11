# storygraph 契约速查（不许违反 + 为什么）

> 本文是 `.workbuddy/memory/MEMORY.md` 的**详细契约分册**。
> MEMORY.md 只留"状态速览 + 最关键铁律 + 指针"，**全部契约在此**。
> 每条约会写"为什么"，因为**只写"不许"而不知道理由，下一个人会把它当教条绕过**。
> 引用状态前先实测（跑 `tools/hell_*.py` / 对应 pytest），别信任何文档里的 ✅/❌。

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
- **F2** `beat_model.BEAT_FIELDS` 是列顺序唯一真源。动态列（`语句名·参数名`）追加末尾。`_row_fields()` 是"基础+动态"唯一拼法。加基础列同步六处（`BEAT_FIELDS`/`BEAT_HEADERS`/`HIDDEN_BEAT_COLUMNS`/`SINGLE_LINE_BEAT_FIELDS`/`to_row`/`from_row`）。
- **F3** `Stage`/`BGM`/`SFX` 格内是 DSL：方括号原生写法直通。
- **F4/F5/F6** `import_beats_from_excel` 一律 hydrate；`LineId` 是协作主键；`ContentHash` 每次重算；`dsl.normalize_text` 只归一分隔符绝不动 `，（）`。
- **F7** 单行列单元格不许有换行（P0）：`SINGLE_LINE_BEAT_FIELDS`+`sanitize_single_line_cell` 读写两侧都过。多行列（台词/注释/Stage/Code/FileBlocks）刻意豁免。守卫 `test_cell_control_chars.py`。

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

## 六、自定义模块入口 · 契约（2026-10-10）
- **发现** `statement_registry.discover_undeclared(root)`（纯、只读）。⚠ 判据照抄 `coverage_of_statements`：`raw_kind` 在 `Statement.payload` 里（**不是 beat 属性**）。
- **V2-1 闸门**：`_is_builtin_head()`（词表已知 ∪ 容器头 ∪ `LEAF_ARG_HEADS`(`with/pause/window`)）→ **不列**；语句头须合法标识符；`_looks_like_dialogue()` **先问角色表**（`l oh "3"` 是台词不是"你写错了"）；`declared_builtins()` 指认"内置语句不用声明"；`remove_statement()` **先备份**再移除。真项目"待认识"列表 **3 → 0**。
- **表单纯函数** `authoring/learn_statement.build_declaration()`：`intensity=3`→按 `intensity` 处理并报出；中文/非法参数名→**丢掉并报**；重复项只留一次；没勾"带块"不写 `block` 键。
- ⚠ **两个 registry 不是一回事**：`session.registry()`→`AssetRegistry`（资产），语句注册表是 `StatementRegistry.for_project(root)`。混用 → Qt 槽吞异常 → "点了毫无反应"。
- **词组分组**（`authoring/spec_groups.py`，`rules.json` 的 `attr_groups`）：逐字 / **整段词序列**（`swim_suit_glad` 归"服装"）/ **前缀通配 `swim_*`** / 兜底组。⚠ **不做子串包含**（`unhappy` 不含 `happy`）。⚠ **没声明 = 完全平铺**（通用性底线）。
- **服装两条正解**：**A（推荐）** 服装=立绘**属性**（图登记进 `images.xlsx`，`Tag=角色`/`Attributes=swim_suit` → 与"表情"共用下拉池；想分开显示用 `attr_groups`）；**B** `swim_suit lyj "泳装" intensity=3` 是自定义指令 → 声明参数 → 卡片一组控件 + 表格动态列（`swim_suit·pose`）。⚠ `fold_spec_arg_columns` 的键是**整列名**。
- ⚠ **面板少一个转发方法 = `AttributeError` → `QMessageBox` 模态 → 测试挂死 / 用户看到"卡住"**。守卫 `test_every_panel_method_the_window_calls_really_exists`。
- ⚠ **构造期 `_refresh_preview` 早于控件创建** → 一律 `getattr` 守卫 + 建好后重刷。

### 六·补：两条**界面红线**（2026-10-11，都是用户实测踩出来的）
1. **界面必须能干活，不能只"看着有"** —— 判据要**真的 `action.trigger()` 一次**，
   并断言控制台不许出现"失败/没写/Traceback"。**"存在 ≠ 能跑"**
   （用户原话："按钮无法点击！点击了也没有反应！"）。
2. **能点就必须有反应**：卡死了就 `setEnabled(False)` + 灰化 + tooltip 写清原因，
   **绝不允许"点下去什么都不发生"**。
3. **用词说人话**（用户："我不会用！！！你更别提编剧了"）：界面文案里不许出现
   `语句名`/`参数`/`候选值`/`describe`/`T1`/`rules.json` 这类实现者词汇；
   一律走"动作名 / 要填什么 / 只能填这些 / 就这样，教会它"。
   ⚠ **Qt 的 QLabel/QCheckBox 不渲染 Markdown** —— 文案里写 `**…**` 会原样显示成星号。
   （2026-10-11 补了**两条自动判据**防再犯：控件级扫弹窗里所有控件的文字；
   源码级扫 `dialog_learn.py` 里**非 docstring** 的字符串字面量。
   两层都要 —— 控件级**会漏**"暂时不显示"的文案。）

### 六·补2：行为级验证（`test`）的**操作红线**（2026-10-11 真机实测）
> 细节与证据在 `docs/终极方案_引擎事实与三层分工_2026-10-10.md` **§十二**。

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
> 详情在 `docs/终极方案_引擎事实与三层分工_2026-10-10.md` **§十三**。

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
- 落地建议：一次性 dump 脚本 → `storygraph.engine_facts.json`（可 diff、进版本库）。**全案见 `docs/设计_契约前提的复核与修正_2026-10-10.md`**。

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
**全案：`docs/设计_真机四缺口实测与四个激进想法_2026-10-10.md`**。

## 七、2026-10-10 顺带抓到的既有真缺陷（已修）
- **U1** `patcher._is_new_content_row` 用了签名里没有的 `old_lines` → `NameError`。已补参数 + 调用点补传。
- **U2** `layered_model._expand_node` 给 `_expand_composite_call` 传了不存在的 `idx` → 任何 `Composite(...)` 都 `NameError`。已删参数 + 顺带修两处静默丢图层；签名按 Ren'Py 8.5 官方文档核对。⚠ 真机 `sr_general` 的 `Composite` 0 次 → 属"换项目就炸"的既有炸弹。
- `tests/test_no_undefined_names.py` 是抓到它们的那张网 —— **别让它变红还放着不管**。
