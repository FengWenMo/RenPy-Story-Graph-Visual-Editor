# 项目记忆：RenPy 剧情树编辑器（storygraph）

> **怎么用**：易变事实（用例数/行数/性能）**不写这里** → 读代码或跑测试。
> 这里只放：**状态（做完/没做/待你决定）** + **契约（不许违反 + 为什么）**。
> 细节与复现见 `.workbuddy/memory/YYYY-MM-DD.md`。
> ⚠ **2026-10-08 的教训**：这份清单曾长期滞后（两条"未修"其实早已修好）——
> **引用状态前先实测**（跑 `tools/hell_*.py` / 对应 pytest），别信记忆里的 ✅/❌。
>
> **长期目标与里程碑（M1–M5）见 `docs/路线图_长期目标_2026-10-08.md`** —— 本文只留状态速览与契约。

---

## 〇、一页速览（2026-10-08 实测校准）

### ✅ 已完成（有判据）
| 项 | 判据 |
|---|---|
| 往返幂等（`.rpy`→表→`.rpy` 逐字节） | 真机 53/53 两路；`tools/audit_idempotence.py` |
| P0 静默全量覆盖 | `tests/test_p0_no_silent_full_regen.py` |
| label 内未知块整块消失 | M1 §K；`tests/test_unknown_block_preserved.py` |
| 循环体升 T0（`while/for` 块体结构化） | M7；`tests/test_loop_model.py` |
| **解析层嵌套 bug + 嵌套菜单 1–5 层** | `tools/hell_nest_inline.py` 18 用例 + 真机 **0 失败** |
| **嵌套≥3 层全量路径** | 3 层菜单全量往返：3 个 `menu:` + deep 行 + 选项文字全在 |
| 相邻舞台指令过表翻序 | `OpOrder`+`Beat.unified_plan()` |
| **菜单标题转义自反** | `tools/hell_edit_rounds.py` 轴 A2 = **0 处** |
| **条件滚雪球（`if>menu>if`）** | 三轮往返 `r1==r2==r3`，条件不膨胀 |
| **选项文案带换行** | 多行文案 → 产物正确转义、选项体保留（不变 `pass`） |
| **多引擎接线 + 多章覆盖** | IR 加 `targets`/`nest`；`export_ir` 合并；`hell_multi_engine` **0 失败** |
| **多语言 tl 链路** | `tests/test_translation_files.py` 覆盖现代 tl（裸字符串/带说话人前缀/strings） |
| **`schema.json` 单一定义** | `storygraph/schema.py`+`docs/schema.json`；`test_schema_single_source.py` |
| 自定义结构：describe / 构建报告点名 / W120-122 / learn | 各带判据（见 §L、缺口 14） |
| **CST 有真实消费者** | 构建报告读 `cst_attached` + span 重叠门禁；describe 带原文样例 |
| **`project_conventions` 接入生产** | `unknown_statements` 修假阳性（真机 2→0）并进构建报告 |
| 静态分析：可靠性 + 精度 | 浮点不判死 / 链式比较 / De Morgan；`test_hell_static_analysis.py` |
| 块编辑器「语法检查」 | 逐行 `ast.parse`（原为恒真空转） |
| BOM 文件解析 / `find_scripts` 静默失能 | 见 §五 新增契约 |
| 资源表中文表头 + `ID` 整列隐藏 | 用户 2026-10-07 定案（**换位不做**） |
| **改名补丁确认闸门（M2/D3）** | `plan`（纯）→`apply(confirm=)`（未确认零写）→`rollback`（逐位还原）；`test_hell_rename_confirm_gate.py` 11 条 + 突变 |
| **立绘/退场摊列（M3-2/D4）** | `立绘2/立绘3…` 每格绑图像候选 DV；模型与渲染**未动**；`test_hell_show_split_columns.py` 22 条 + 真机渲染逐字不变 |
| **落盘入口唯一性守卫（M1/D2）** | `test_single_write_entry.py`（AST 登记表 + 定向判据）+ 突变验证 |
| 缩略图性能（分片缓存/字节上限/预热） | §E |

### ❌ 未做（真缺口；里程碑与验收见 `docs/路线图_长期目标_2026-10-08.md`）
1. ~~两套 `.rpy` 生成器并存（D2，M1）~~ **已完成（2026-10-09）**：控制流导出改走 `write_generation(ensure_generator_marker(text), …)`；两个模块的 docstring 各自写明 ownership（控制流骨架 vs 完整剧本）+ 共用规则（`render_code_block`/`quote_string`）；新增 **"唯一写盘入口"守卫** `tests/test_single_write_entry.py`（AST 扫全包 14 处落盘点，每个必须在 `WRITERS` 登记表里说清写什么；突变验证 `tools/mut_write_entry.py`）。
2. ~~`asset_engine._apply_rename_patch` 正则改写用户 `.rpy`~~ **已修（M2，2026-10-08）**：两段式契约 `plan_rename_patch`（纯，算 diff）→ `apply_rename_patch(confirm=…)`（**未确认零写**）→ `rollback_rename_patch`（逐位还原）；唯一真源在 `assets/asset_extensions.py`；引擎"会改脚本的改名"**一律**进批量确认。判据 `tests/test_hell_rename_confirm_gate.py`（11 条 + 突变验证）。⚠ 按 D3' **未接 Qt**。
3. **Excel 前端上限（D4，M3 🔵 第一切片✅）**：`Stage` 是刻意多行列且不在 `DROPDOWN_COLUMNS`。**方案 B 已定并已落地第一切片**：
   · **`Show`/`Hide` 摊列**：原列只放**第 1 项**，第 2+ 项各占 `立绘2/立绘3…`、`退场2…`（追加末尾 + 每格绑图像候选 DV）。
   · ⚠ **模型与渲染不动** —— `beat.show`/`beat.hide` 仍是 `;` 字符串，`_stage_lines`/`compose_stage` 一行未改 → 渲染风险 0。
   · 判据 `tests/test_hell_show_split_columns.py`（22 条）+ `tools/probe_show_split_real.py`；验收：真机渲染逐字不变 / `audit_idempotence` 53/53 / `hell_edit_rounds` 0 失败 / 回归 594 passed。
   · ⚠ 两个**静默缺陷**已修：项数>格数时**放不下就不摊**（否则丢项）；**不摊时不许写摊列**（否则读回来重复）。
   · 审查另推翻两处旧假设：`Stage` **已是派生视图**（`compose_stage`）；⚠ `pause`/`with X`/`jump` 走 `raw` 通道**刻意不进 Stage**；顺序载体是 **`OpOrder`**（带 `show#0` 逐次序号）。
4. **坏味道（D5，M4 🔵 已完成 4 个）**：`sync_asset_tables` **133→28**、`render_beat_to_rpy` **81→47**、`scan_assets` **74→8**、`renpy_parser._walk` **72→12**（都已从 health_check 头部消失，阈值=50）。手法=**先录金标 → 纯结构拆分**（sync=表逐格+返回 dict；渲染=53 文件产物 hash；扫描=4 组合结果集；解析=**整份 ParseResult**）。剩：`validate_level1`(63) / `patcher._replace_matched`(62) / `beat_build._hydrate_stage`(59) / `patch_export`(57)；模块体积 `renpy_parser` 4422 行。⚠ 教训：金标要**归一化**+**能定位到文件**；**splice 后旧下标失效**（重定位；函数结束行用 AST `end_lineno`）；**换名要跳过关键字参数名**、**只改代码不碰注释/字符串**；**"重命名"与"搬运"分两步**（先统一成 `box.` 再 dedent 提方法，风险趋零）；抽出后**复查死代码**。
5. **拍级三路合并**（设计文档 §5 第 6 步，明确"按需"）。
6. **多引擎适配深度（M5 ⚪）**：接线/结构位已修；运行时组件、资源映射、运行期验证未做。

### ❓ 待你决定
- **D1** 缺口"嵌套≥3 层"的旧定案「树归 Graph 画布 / 句子级内容归 Excel」是否**撤销**（嵌套已修）。
- **D2** 两套生成器：**现在收敛**还是继续记账？（§未做 1）
- **D3** `_apply_rename_patch`：改成"**提出改动 → 你确认**"还是继续自动改写（有 backup）？（§未做 2）
- **D4** `Stage` 列**格级下拉/拆分**：做不做（工作量大，但"对着表格编演出"的核心）？（§未做 3）
- **D5** 坏味道重构：是否进入"**练习课题**"节奏（你亲自写一版）？（§未做 4）
- **D6** `pytest.ini` 的 `--basetemp` 是仓库内目录 → 裸 `pytest` 必炸 **~1589 ERROR**。要不要改成不炸（影响全队工作流）？（§五）
- **D7** 多引擎（Unity/Godot）要不要**加深**？你画像里说过可能迁 Unity，但也说过单用户时降权多引擎 —— 现在接线已修，是否值得继续。
- **D8** **提交策略**：工作树大量未提交 + 并发会话在改；要不要我帮你按主题拆提交（或你先自己收口）。

### ▶ 下一步建议（我的排序与理由）
1. **先提交/冻结当前状态** —— 未提交 + 并发会话持续制造"记忆 vs 代码"漂移（这一轮就发现 5 条 stale）。不冻结，后面每轮都要重新实测。
2. **D2 两套生成器（§未做 1）** —— 唯一卡住**所有导出改动**的架构债：任何导出修复都要问"改哪一套"。
3. **D3 rename patch（§未做 2）** —— 风险最高的写操作（正则改用户脚本）；与 §B 的"要么全对要么完全不动"哲学不符。
4. **D4 Stage 格级下拉（§未做 3）** —— 直接决定"策划对着 Excel 编演出"的天花板。
5. **D5 坏味道（§未做 4）** —— 你的练习课题；建议挑 `sync_asset_tables`(133) 当第一个。
6. **制度化**：状态清单独立成 `docs/状态与路线_YYYY-MM-DD.md`，MEMORY.md 只留契约 + 一页速览（本次已把**状态前置**，抗截断）。

---

## 一、架构契约（不许违反）

### A. 真源与 IR
- **A1** `.rpy` 唯一真源；单源→多视图→受控 patch。`default/define/init python/transform/screen` 不进 Beats；不归己的行逐字节不动。顶层 `init python:` 走 `FileBlocks`；其余第三类只能走增量，无 `--source` 的全量导出被 `OPAQUE_LOST` 拒写。
- **A2** `core.statement` 唯一 IR；禁第二套 AST walk。IR 是语句流非树：`elif/else` 展开成 `not(前一条件)`；`menu` 渲染前重新归组复原。
- **A3** 保真后门两条（scope 不同）：`CodeBlock`（一拍内 `python:`）/`FileBlock`（顶层 `init python:`）。同一语句绝不两条通道各出一次。
- **A4** 整行注释必须保真：lexer 存**含缩进**原文（存 stripped 会双重去缩进→非法 ATL）。行尾注释**刻意**剥掉。

### B. 写入与落盘（最高危）
- **B1** 落盘一律过 `generation_guard.write_generation`。`LABEL_COLLISION` 只在产物真落进 `<根>/game/` 时判。`HANDWRITTEN_TARGET`=目标存在且首行无生成标记→拒写，`force` 只越这条。让路判据=底稿==目标。三处调用方：`EditorSession.export`/`pipeline.compile.write_compiled`/`app.tables.apply_export`。
- **B2** 全量只许新建；改已有 `.rpy` 走 `story.patcher`。**"要么全对要么完全不动"**：按行身份配对，缩进只来自原文，替换行必须来自真实产物，失败 `ok=False` 且 **text 不可用**。身份判据坑：无标题菜单占位符按 `menu` 行认；选项行文案正是用户改的那位按 `_OPTION_ROW_RE` 认。夹具必须不干净。结构补空拍不是卡片。
- **B4** CLI 也能增量：`table xlsx2rpy`/`compile` 加 `--source`→`merge_incrementally`。补丁没配上又丢顶层语句→`OPAQUE_LOST` 拒写。
- **B5** 行号新鲜时信行号：`EditorSession.load` 记指纹（size+mtime_ns+sha1[:16]），export 核对磁盘==载入那份→`patch_export(trust_ranges=True)`。表格路 `recover_source_ranges` 的猜值**绝不许传**（"整体下移 1 行 259 行被改写"事故入口）。可信分支也要报配不上。
- **B6** 校验与渲染器必须同源（`patcher._written_speaker` 走 `Audit.character`）。
- **B7** Ren'Py 关键字优先于说话人（P0）：`voice/extend/menu"标题":` 长相同 `lyj"台词"`。判据 `_VERB_RE` 收 `voice`；`_line_key`/`_head_of` 先认动词；`_KEYWORD_SPEAKERS` 兜 `extend/menu`。
- **B8** "没落地"判据容得下"只是写法不同"：`_head_of`→`_unescaped`→`_loose`→`_literals`→`_body_lines`。

### C. Stage / 演出投影
- **C1** `Stage`↔结构化列互为投影，单向+幂等+只输出一次。结构化字段唯一真源，Stage 只承载表达不了的。`hydrate` 幂等；中文化按语义判（`_stage_op_covered` 别靠字符串相等）；重投影用 `stage_only_ops()` 留住 `[停顿] 1.0`。
- **C2** `beat.at` 是整拍兜底位置：只有所有立绘位置一致才能同步它。
- **C3** `compose_stage` 必须与 `_stage_lines` 同源；顺序列补齐规矩只一处=`Beat.stage_verbs`。`sync_stage_order` 只追加；指纹存归一化值；派生面板保存后必须重算。
- **C4** 裸词位置归一：`角色 表情 左` 的"左"是位置不是表情。中文名先 `actor_image_tag` 归一成英文 tag 查表；查图按 Tag 收窄。
- **C5** 卡片往返不许改 `beat.show` 一个字节。多属性立绘最阴（归一+`attr_for` 子串兜底会把属性合并成重复→图像名被改写）。判据 `normalize_expression_words`：归一升级档不许两词指向同一条属性。
- **C6** `kind` 是说话人单向投影：填说话人不自动降级 `say`→`narration`。`menu/menu_option/extend` 类型不动。
- **C7** 表情/位置候选与显示=项目真实名字（项目词表声明优先）。内置词表只是输入别名。候选项目名排最前不许被 limit 截断；当前值必须在候选里。

### D. 资产
- **D0** 资源表"没有就当场建"：`ensure_asset_tables()` 幂等；引擎转发传 `with_scripts=False`。
- **D0c** 表盘对账唯一真源 `compare_registry_to_disk()`（只读）`{disk,table,unregistered,missing,stale_paths,outside_game}`。`outside_game` 最值钱。音频目录按目录名任意深度认（含 `sfx`/`se`）。
- **D0d** 打开项目=自动对齐 `sync_asset_tables()`（`*.bak.xlsx` 备份；已有行不动）。判重按解析后真实文件。删行/改已有字段仍要用户明确动作。
- **D0e** 归类只认"目录段"：词元精确+少量词尾+最近目录优先+目录压过文件名+`images/<tag>/x.png`；`other` 不编 tag。`scan_assets` 不许第二套 tag 逻辑。跳过 `tmp/build/.git/__pycache__/saves`。
- **D0g/D0j** 缺文件行不可用：侧车 `_autoscan.json` 记工具加过的 Path→没了就删；人工/占位不动。后收紧：缺文件直接删（写前备份），例外=人工占位（Notes/Doc 有占位/placeholder/TODO/WIP）。删不掉（Excel 锁）退回 `registry.unusable`。
- **D0f** 音频行"作者写法优先"：配对永不改写音频行；按 (动词,通道) 条数容忍校验。
- **D0h** "读不到资源表"给真原因：`is_real_table_file()` 排除 `.bak`/`~$`/`.broken-`；解析失败改名留档 `*.broken-<ts>.xlsx`（不删）→重建（不许整份重写已有表）。
- **D0i** 打开项目"一次遍历+字典查找"（`FileWalkIndex`）；整条路共用一份索引（`walk=`）；UI 复用 `session.last_reconcile()`。不许"同名文件兜底"。
- **D0k** 📦资源体检 `asset_health_report()`+`app/asset_report.format_asset_health()`（纯函数结论第一行）。占位行不算缺失。
- **D0l/D0m/D0n** 自动对齐：四步顺序（删缺失→删指向嵌套副本→改指→合并重复→补登）；**嵌套副本绝不能当项目资源扫**；`append_rows_to_table` 遇 ID 撞车但文件不同另起 ID，不许静默跳过。
- **D0o/D0q/D0r/D0t** 合成立绘：`preferred_image_attrs` 身体件垫底；`layered_model` 通用模型+条件求值（认不出→None）；`AlphaMask` 必须真应用；空姿势改回普通必须 `hide`（只报告，或清单声明 `hide_before_pose_switch` 才补）。`lookup_image_id` 给不到退"这个 tag 第一张"**绝不跨角色**。
- **D0p** 手输拍号立刻跳：`setKeyboardTracking(False)`+`valueChanged`+`editingFinished`，`_on_jump` 幂等。
- **D0b** 缩略图预热尺寸必须与界面常量一致（缓存键含 `WxH`）。
- **D1** 引用判定唯一真源=`asset_registry.resolve_references`（循环里必须传 `AssetIndex`）。
- **D2** 词表真源=`core.vocabulary`；补全打分真源=`assets.asset_completion.score_candidates`。
- **D3** transform 的 `ATL`/`Params`/`Doc` 以脚本为权威（`update_keys=`）；人写 `Category`/`Notes` 不动。
- **D4** 注册表图片 Path 两套基准：扫描行相对项目根，脚本 `image` 声明行相对 `game/`。
- **D5/D6/D7** 缺资源→`# TODO` + 建议；校验分级（有表查不到→错误；没建表→警告宁少报）；编译路径不校验。

### E. 性能 / 缩略图
- **E1** 图片路径基准唯一真源=`story.excel_export.resolve_thumb_source`。
- **E2** 缩略图缓存=两级分片+有界自清（扫描预算 2s / 每次删除上限 3000 / 一进程只排一次）。
- **E3** 内存缓存按**字节**（`CACHE_BYTES_LIMIT=64MB`）。
- **E4** 赢面四条：绝不重复解码、并行、只解看得见、大图 `reduce()`(BOX)。Qt `setScaledSize` 更慢→主路留 PIL。
- **E5** 缩略图可切暗底（选择器与卡片同步同一偏好）；偏好存储留注入点。

### F. Excel / 表格
- **F1** 安全写单元格唯一实现=`excel.cells`。
- **F2** `beat_model.BEAT_FIELDS` 是列顺序唯一真源。动态列（`语句名·参数名`）追加末尾。`_row_fields()` 是"基础+动态"唯一拼法。加基础列同步六处（`BEAT_FIELDS`/`BEAT_HEADERS`/`HIDDEN_BEAT_COLUMNS`/`SINGLE_LINE_BEAT_FIELDS`/`to_row`/`from_row`）。
- **F3** `Stage`/`BGM`/`SFX` 格内是 DSL：方括号原生写法直通。
- **F4/F5/F6** `import_beats_from_excel` 一律 hydrate；`LineId` 是协作主键；`ContentHash` 每次重算；`dsl.normalize_text` 只归一分隔符绝不动 `，（）`。
- **F7** 单行列单元格不许有换行（P0）：`SINGLE_LINE_BEAT_FIELDS`+`sanitize_single_line_cell` 读写两侧都过。多行列（台词/注释/Stage/Code/FileBlocks）刻意豁免。守卫 `test_cell_control_chars.py`。

### G. UI
- **G1** 界面只从 `ui.tokens` 取色值/字号/间距；缩略图一律 `ui.thumb_loader`（禁 UI 线程同步解码）。
- **G2** 只读 tkinter `Text` 静默吞程序化写入→要灰显别用 `state='disabled'`。
- **G3/G4** 翻页纯导航（只提交当前拍）；辅助功能不许改用户已有数据。
- **G5/G6** 预览贴图回调只替换那一块；落盘时机界面说清。
- **G7** 写路径前先认路径（不是 `str`/`Path` 就 `ok=False`）；开关型参数 keyword-only。守卫 `test_control_flow_target.py`。

### H. 架构 / 依赖
- **H1** 依赖单向 `core ← parsing/excel ← assets/story ← pipeline ← ui`；`story` 不许顶层 import `pipeline`。守卫 `test_package_layering.py`。
- **H2** 改中文 `.py` 只能用编辑工具或 Python 脚本（PowerShell 会 GBK 往返毁文件）。
- **H3** Git Bash heredoc 会做 MSYS 路径转换；探针脚本一律用编辑工具写文件。
- **H4**（2026-10-08）**含中文/转义的复现别用 `python -c`** —— 经 Bash 传参**会改坏样本**（曾"复现"出一个不存在的 P0）。一律写 `.py`。

### I. 渲染输出硬约束
- **I1** `_quote` 必须转义 `\\` `"` 和 `\n`/`\r`/`\t`。守卫 `test_hell_findings.py::TestQuoteEscapesControlChars`。
- **I2** label 是硬边界：换 label 先 `flush_stage_only()`；配套 `_fill_empty_labels`。
- **I3** 不许凭空造 label（纯定义文件产物不许出现 `label start:`）。

### K. 自定义块（M1）
- **K1** 未知块语句一律走 `Beat.opaque`（走 raw/`CodeBlock` 会推断补 `$`/包 `python:`→改剧情）。`Opaque` 是载体。
- **K2** 台词不许带块：`mycue "闪电" intensity=3:` 与 `lyj"台词"` 长相同；判据子块有非注释内容→`_NO_MATCH` 交未建模兜底。
- **K3** 取块按原文物理行（`_raw_block_lines`）；行号对不上退回 AST 重建；`parse()` 开头清 `_src_cache`。
- **K4** 门禁两码都 warn 不拦导出：`UNMANAGED_BLOCK`/`KNOWN_UNMODELED_BLOCK`；分类真源=`core.vocabulary.is_known_flow_block()`。
- **K5** 门禁结论**逐条**印（`BuildResult.summary()`）；落盘三处调用方都要交 `beats`。
- **K6** 判据必须**整段相等**+禁 `$ `/`python:`（"行在产物里"太松）。守卫 `test_unknown_block_preserved.py`。

### L. 自定义语句管理等级（M2/M3/M5）
- **L1** 三来源优先级唯一真源=`story/statement_registry.py`：`rules.json`（declared，覆盖）> `register_statement`（host）> 形状识别（不做）。
- **L2** 只认字面量，认不出跳过（`name=`/第一个位置参数）。⚠ 猜一个名字比说不知道坏得多。
- **L3** `SpecName`（隐藏单行）=被谁声明过；`Opaque` 存原文。⚠ 叶形态自定义语句也必须走 `opaque`。
- **L4** 标注后置（`annotate_spec_statements`，beat_build 出拍前）；项目根从 `statement.source` 反推并按根缓存。
- **L5** 门禁三桶 `block_buckets`：declared/custom/standard。⚠ "已内置建模块不进任何桶"每加一种块类型都要跟着加（`while/for`、`camera` 各漏过一次）。
- **L5b** "项目根"严格：有 `game/` 或有 `storygraph.rules.json`，否则 `''`（不许退回父目录）。
- **L6** 词表唯一真源=`core/vocabulary`（5 张表）。⚠ 不许并成一个集合（动词表必须排除 `if/elif/else/while/for`）。
- **L7** 「每参数一列+原生下拉」唯一真源=`story/spec_args.py`。动态列名=`语句名·参数名`（位置参数多 `@下标`）。标准形状自声明（`camera`/`with`/`pause`/`window`）。⚠ 叶语句走**代码列**且必须同步 `ordered`；⚠ 值没变头行逐字节原样；⚠ 引号里 `a=b` 不是参数。
- **Lb** `camera` 是流程块（头行结构化+块体逐字 T0b）；`coverage_of_statements` 须显式认 `st.kind=='camera'`。

### M. 元模型 + CST
- **M0** 等级 `T0/T0b/T1a/T1b/T2/T3`；`T0b` 不许并进 `T0`；只读=`READONLY_LEVELS={T2,T3}`。
- **M1** 元模型唯一真源=`core/capability.py`（State/Provenance/Transition/Coverage/Escape）；判定顺序即优先级。
- **M2** 三条硬不变量（逐条有测试）：降级不改字节/精化不改字节/T0 不降级。⚠ 判据必须同时断言等级真的动了。
- **M3** T1 编辑面=`SpecHead`+`Opaque`；"没改就不动字节"靠 `emit_opaque` 比对头行（必须 `strip()`）。
- **M4** 候选不许猜：`spec_arg_choices()` 没声明返回空。
- **M5** CST 唯一真源=`core/cst.py`（`Span`+`raw_text`+`Trivia` 动态属性，不进相等/哈希）。Span 归属：到下一条语句前一行或第一个顶格行。⚠ 一次 analyze 只读一次盘。✅ 已有消费者（构建报告 + describe 样例 + 重叠门禁）。
- **M6** 不许 `except...:pass`（`test_code_health.py` 抓）；一律 `safeguard.swallow(...)`。
- **M7** 循环块=`StatementKind.LOOP`+`LoopHeader`+作用域；`while/for` 头行结构化、块体进语句流→T0。⚠ 老形态永远要能读；结构位丢→显式 `# TODO`，绝不补 `while…:pass`；`W113` 判据排除 `loop_header/camera_header/opaque`。
- **M8** `W120`(缺必需)/`W121`(值不在 choices)。⚠ 头行末尾 `:` 扫描参数前先剥。

### N. 往返幂等
- **N1** 守卫=`tools/audit_idempotence.py`：`t2==t1` 且 `t3==t2` 逐字节。单元判据 `test_roundtrip_idempotent.py`。
- **N2** 已修：生成标记重复/括号续行缩进无界/被注释整块推一层/块后顶格注释缩一层/相邻舞台指令翻序。
- **N3** 历史未修项（3d/3e）**2026-10-08 实测已修**，见 §〇。

### J. 测试纪律
- **J1** 必须测往返不变式（不能只测半程）。
- **J2** 变异验证：改回旧实现测试必须变红；必须走缺陷真正发生那条路径。
- **J3** 判据写错比缺陷更常见 → 先怀疑判据（`tools/probe_*.py`）。
- **J4** 假阳性比漏报更贵：检查器本身要能被真实项目跑通。
- **J5** 含中文复现写 `.py`（见 **H4**）。
- **J6** 跑测试一律 `--basetemp=<全新目录>`（见 §五 与 **D6**）。

---

## 二、仓库与运行
- 包：`storygraph/{core,excel,parsing,assets,story,pipeline,graph,ui_qt,app,collab}`
- 入口：根 `main.py`/`cli.py` → `storygraph.main`/`storygraph.cli`（`cli.py selfcheck` 走主链路）
- 依赖：openpyxl（必需）+ Pillow（可选）；`legacy/` 不参与构建
- 静态体检 `tools/health_check.py`；动态 `tools/audit_roundtrip.py`/`torture_test.py`/`brutal_test.py`/`tools/hell_*.py`；交付就绪 `tools/journey_check.py`
- ⚠ `tools/audit_real_project.py` 是坏脚本别信输出
- CI：`.github/workflows/ci.yml`（3.10/3.12/3.13 + demo 体检）
- 真机验收对象：`D:\千连万理\RenPyTest\sr_general`（换机 skip）
- ⚠ **并发写入常态**：另会话同时改 beat_model/beat_render/cli/patcher/parser/statement_registry。动手前 `git status`+文件时间戳。
- 改 UI 必须用真实 Qt（`D:\Python\python.exe` 有 PySide6）；offscreen 可无头起窗口。

## 三、协作方式（用户确认）
- 单人开发：代码主要由 AI 写；用户负责设计/测试/验收，目标是**积累经验 + 学会改进 AI 代码**。
- 多解释"为什么这么设计 / 权衡了什么 / 否决了哪个备选"；用户写代码时别抢答。
- 验收靠**真实项目 + 全量测试**让数据打脸；练习落"关掉 AI 自己写一版"而非读代码。
- **接受判据校准**：当 AI 的测试判据本身有误（非产品 bug），承认并重校准（本会话 `test_pipeline_batch` 一例）。

## 四、2026-10-08 新增契约（别改回去）
- **源文件读取吃 BOM**：`parsing.read_source_text()`（只在开头剥一个 `\ufeff`），4 处读盘点统一。带 BOM 的 `.rpy` 原会解析出 **0 条语句**。
- **静态分析：非整数常量不施加约束**（`_int_literal_value` 返回 None）+ 保留浮点值。铁律——**有取值能满足的条件绝不许判"不可能"**。
- **`check_variable_syntax` = 逐行 `ast.parse`**（只查语法不查语义；字符串输入按 `splitlines()` 拆）。
- **`cds_registry.find_scripts` 只看"扫描根之内"的目录段**（`relative_to(root)`）——否则祖先目录带 `.` 时整项目脚本被跳过。
- **`pytest` 默认会炸**：不带 `--basetemp` 时 `addopts` 指向仓库内 `.pip-tmp/pytest`，复用即撞沙箱 → **~1589 ERROR**。（是否改配置 → **D6**）
- **golden 重基线**：`tests/snapshots/editor_session.json`；改行为后 `STORYGRAPH_UPDATE_SNAPSHOTS=1` 跑一次，只改有意的那几条。
- **走不动的探针**：`tools/probe_cst_snapshot.py`/`resolve_audio_path` 等一批"只有测试在用"的函数见 `health_check` 的死代码段。
