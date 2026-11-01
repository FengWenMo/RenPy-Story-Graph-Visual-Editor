# 项目记忆：RenPy 剧情编辑器（storygraph）· 索引页

> 只放**最容易踩的坑**与**指针**；易变事实（用例数/行数/性能）读代码或跑测试，别信文档里的 ✅。
> 详情：契约 `docs/契约速查_storygraph.md`（按需 grep）｜逐日 `docs/开发日志.md` + `.workbuddy/memory/2026-*.md`｜**真源与优先级（四域 + 层序常量 + 准入四问）**：`docs/真源与优先级_设计决策_2026-10-31.md`｜**超清 CG/BG 零侵入导入（L0–L4 阶梯）**：`docs/超清CG与BG_零侵入导入_2026-10-31.md`。

## 一、十条铁律（违反任一条都会静默毁掉用户项目）

1. **`.rpy` 唯一真源；不归己的行逐字节不动**。只逐行配对替换真改过的行，绝不整份重生成。
2. **要么全对、要么完全不动**。任一行配不上 → 整份放弃 + 报错，不产出"半对"。
3. **认不出就原样抄**。未知块原样保留，不补 `$`、不包 `python:`、不改缩进。
4. **猜一个名字比说"不知道"坏得多**。只认字面量，不模糊匹配。
5. **绝不静默全量覆盖**。落盘过门禁；`HANDWRITTEN_TARGET` 拒写；改用户 `.rpy` 先出 diff。
6. **教它升级，不许改字节**。教会自定义语法后首次导出必须与教会前逐字节相同。
7. **不许静默丢行**（txt 导入/表格/补丁三条路）。判据是"整段相等"。
8. **一个落盘点一个登记**（`WRITERS`，守卫 `test_single_write_entry.py`）。
9. **本工具不猜语义**。意思由人标注，工具只发现/展示/检索。
10. **含中文/转义的复现一律写 `.py`** —— `python -c` 经 Bash 会改坏样本（曾"复现"出不存在的 P0）。

## 二、引擎事实

- `lint` 默认退 0，**必须 `--error-code`**；`has_image()` 不是合法性判据；`probe_complete=false` 当硬失败。
- 事实文件 `<项目>/storygraph.engine_facts.json`：每条带 `source`；两源不一致记 `conflicts`，不静默挑一个。
- **覆盖内置语句 = 完全替换**；**fork 引擎新增语句对工具隐形**；`test` headless 不可用。
- 真机基准（sr_general）：images 317 / tags 235 / transforms 155 / labels 106。
- 本机 SDK：`D:\software\renpy-8.4.1-sdk` 实际是 **8.5.3.26051504**（看 `renpy/vc_version.py`）。
- **引擎试玩**（`app/engine_preview.py`）= "镜像 + 注入 + 隔离"：镜像在 `%LOCALAPPDATA%\StoryGraph\engine_preview\`；`config.label_overrides["start"]` + `RENPY_SKIP_MAIN_MENU/SPLASHSCREEN` 直接进章；`--warp game/x.rpy:行` 进单拍；`--savedir` + `APPDATA/HOME` 重定向 + `RENPY_DISABLE_BACKUPS` 保证不碰项目/用户目录。
- 验收截图**用引擎自己的 `renpy.screenshot`**（注入 `config.periodic_callbacks`），别用 `PIL.ImageGrab`（窗口被挡会截到前面的窗口，误判过"引擎黑屏"）。

## 三、最高频的实操坑

**判空/真值**
- **有 `__bool__` 的对象别用真值判断**：`EditorSession`（有 `__len__`，没载入章节时 falsy）、`StatementRegistry.__bool__`（只有 `calls` 的项目 falsy，`dialog_calls._load` 曾因此读成空表）。判空一律 `is not None`；默认值兜底别用 `or`（`int(x or -1)` 把第 0 拍当没载入）。`Statement.source` 是 `(路径,行号)` 元组。
- **两个 registry 不是一回事**：`session.registry()`=资产表；语句表是 `StatementRegistry.for_project(root)`。混用 → Qt 槽吞异常 → "点了没反应"。

**测试入口**
- **全量测试只用 `python tools/fasttest/run.py`**（并行 + 独立 basetemp + 运行锁）。裸 pytest 串行 ~20 min，两个并发互删 `.pip-tmp/pytest`；`.pip-tmp/` 会到 GB 级，别用 du/find 扫。15s 心跳、300s 无输出退 **124** 并打印 nodeid；`--retry-failed` 只复跑上轮纯全量失败；`--lf` 无效（每轮新 basetemp）。8 workers 实测 2m40s 跑完 8799 条。
- **复杂度棘轮**：`tests/complexity_budget.json` 是"最长 40 + 最绕 40"的上限快照，`test_complexity_budget.py` 判"只许降、不许升"（新函数硬线 500 行 / 150 分支）。改完代码跑 `python tools/complexity_report.py` 看榜；**故意拆小**后不必改快照（只比"不超过"），要接纳新的大函数才用 `tools/complexity_budget.py` 重生成。
- **拆函数的安全姿势**（2026-11-01 拆了 3 处，零回归）：只做"整段搬到新方法 + 原位置留一行调用"，**缩进不变**（class 方法体本来就是 8 空格）；新方法一律 `self, root` 这种显式依赖，别读上一段的局部变量；每搬一段就跑该模块的测试。判据：`_build` 450→272 行、244 条 UI 测试全过。
- **改这四处先跑"地狱"判据**：`test_hell_atomic_io.py`（写盘故障注入 / 并发）/ `test_hell_excel_cycles.py`（往返收敛 / 反复导出）/ `test_hell_guardrails.py`（日志、preflight、解析统计）/ `test_patch_line_splitting.py`（切行）。它们问的是"承诺还在不在"，不是覆盖率。
- **技术债棘轮**：`tools/debt_report.py` 量八项（超大函数 / 高分支 / print / swallow / 宽泛 except / TODO / 超长文件 / **导入环**），`tests/tech_debt_budget.json` 是上限快照，`test_tech_debt_budget.py` 判"只许降"（环按**模块集合**比，别只看个数）。现状（2026-11-01 第五段）：超大函数 **92** / 高分支 **236** / 顶层导入环 **0** / 含函数内 import 的环 **3 个 / 37 模块**（29 模块大环 + `ui_qt` 5 个 + `assets` 3 个）——**环全藏在函数体里的 import**，要解就从那里下手。已解的两个环各留一个叶子：`parsing/continuation.py`（`_shift_continuations`）、`story/file_facts.py`（`FileFacts`/`scan_file`，`placement` 仍 re-export）。清债后重生成：`python tools/debt_budget.py`。
- **拆大函数别把债平移**：拆完要立刻看 `complexity_report` —— 拆出来某个 helper 若仍 >120 行，棘轮数一个不降（`size_rows` 338→71 那轮，`_size_preset_items` 正好 121 行，又把名额吃了回去；再抽 `_size_custom_items` 才落回 93→92）。
- 耗时由少数真机金标主导（scan/validate/patch/describe ~730s），方差 ±40%，别当回归。`test_disk_hygiene` 必须第一个跑。**pytest-xdist 默认 `--loadscope-reorder` 会覆盖重文件优先**（fasttest 已传 `--no-loadscope-reorder`；手写 `-n` 别漏，7m14s→4m36s）。**12 workers 会 `0x8007000e` 全体起不来** → `--workers 6`；**8 workers 偶发 `worker 'gwN' crashed`（Qt 段错误）**，单跑即过 —— 别当回归，重跑一遍或降到 6。

**Qt**
- **无 `QApplication` 就建 Qt 控件 = 进程 abort**（无 traceback）。Qt 测试类要 `qapp` autouse。
- **面板少一个转发方法** = AttributeError → 模态框 → 挂死（守卫 `test_every_panel_method_the_window_calls_really_exists`）；构造期 `_refresh_preview` 早于控件创建 → `getattr` 守卫 + 建好后重刷。
- **Qt 槽里异常只打 stderr = "点了没反应"**：`QMessageBox.addButton(文本, 角色)` 第二参数是 **ButtonRole**，传 StandardButton 当场 TypeError（真机事故：无源节点四个修复入口一起失效）。菜单/右栏动作一律过 `window_nodes._menu_action` 安全壳；有模态框的动作必须有"真建框"判据（`test_ui_reaction_contract.py`）。
- **模态打桩要打全**：`delete_card` 走 `_ask_delete_mode` 的 `box.exec()`，只 patch `QMessageBox.question` 会卡死（xdist 全空闲、0 CPU）；`QMenu.exec` 打桩不可靠 → 菜单建成纯函数再测。**"两个回调"的件**：`sprite_grid` 优先 `on_pick_image_result`（生产接真资产选择器），只打桩老回调会被短路 → 真弹模态 → fasttest 判 124（`test_ui_qt` 4 条都踩）。导出的「舞台可疑行 / 非法 show」弹窗在 `_review_stage_suspects` 里 `dlg.exec()`，没桩别走到它。
- **QSS 吞 `QTreeView` 展开三角** → 自绘 `▾/▸` + `setRootIsDecorated(False)`；判 dock 开没开用 `isHidden()`；`saveState()` 要求工具栏有 `objectName`。
- **滚轮永远不许改值**（`ui_qt/wheel_guard.py`）：`WheelSafeComboBox`（说话人/背景/音乐/语音）+ `WheelValueGuard`（挂 `QApplication`，`ensure_installed()` 幂等，兜所有裸 combo/数值框/滑块）。一律不改值、**显式转交**外层滚动；只有候选列表已弹开才放行。别写"有焦点就放行"。
- **右栏宽度取决于"卡片自己多宽"**（`QComboBox` 是地板杀手）：`minimumSizeHint()` 按最长条目算（40 多字图名 → 下拉 1025px → 卡片 1077px，右栏 357 ⇒ 横向滚动条 + 全部越界）。`WheelSafeComboBox` 已钉 `AdjustToMinimumContentsLengthWithIcon` + `MinimumContentsLength=4`，**别改回默认**。同族：`_dirty_label` 只能留「● 未保存」；`window_state.LAYOUT_VERSION=3` + `LEGACY_CARD_SHARE=0.45`。

**落盘/全局开关**
- **写盘只有一条路**：`core/atomicio.py`（`write_text_atomic` / `save_workbook_atomic`）。`.rpy` 与 `.xlsx` 同一条：临时文件 → `newline=''` → fsync → 多代备份 → `os.replace`。**别再用 `wb.save(path)`**（`test_single_write_entry.py` 会抓）。三条地狱测试换来的规矩，**别改回去**：① 备份名必须带序号（`<名>.bak-<戳>-<序号>`；Windows 时钟粗到一个 tick，只按时间戳命名会**盖掉上一代**）；② `os.replace` 对**瞬时** `PermissionError` 重试 6×20ms（并发写同一目标时会撞车，不重试 = 一次保存**静默消失**）；③ Excel 后缀白名单只认 `.xlsx`（传 `.rpy` → `RefusedWrite`，实测事故是 1488 字节剧本被 15481 字节 xlsx 覆盖）。
- **`open_rpy_as_table` 自己验路径**：不存在 / 是目录 / 后缀不对 → 抛（`FileNotFoundError` / `IsADirectoryError` / `ValueError`）。CLI 那道闸只管 CLI，界面与 MCP 不会走它。
- **读 Excel 只有一条路**：`storygraph/excel/safety.py` 的 `open_workbook()`。它先看 zip 目录（解压炸弹 / 声明维度炸弹 / 宏 / 外链 / 嵌入对象 / 加密 / 老 .xls），`hard` → `UnsafeWorkbook`（人话），`soft` → `swallow` 留痕。**直接写 `openpyxl.load_workbook(` 会被 `test_excel_safety.py` 的 AST 横扫抓住**（与 `wb.save(` 那条同理）。阈值：拒绝 256 MB / 解开 512 MB / 单条目 256 MB / 2 万条目 / 声明 800 万格；提醒线 200 万格、压缩比 200。
- **表读不出来时不许重建**：`xlsx_preserve.snapshot_user_content` 对 `UnsafeWorkbook` / `PermissionError` **抛**（从前一律 `None` → 调用方当成"没有用户内容" → 整份覆盖，用户自加的页/列全没）；只有坏文件才 `None`。`export_beats_to_excel` 把这两类**放行上去**，不吞。
- **来源指纹是完整 sha1**（`table_meta.fingerprint_of`），比较走 `fingerprints_match()`（老表 16 位按前缀兼容）。**别改成直接比 40 位** —— 每张老表都会报"旧了"。
- **`patch_export(expect_sha1=…)` 读完再核对**：会话先 `source_unchanged()`（查）再让 patcher 读，中间那窗口里被改过 → 拒绝增量。别把 `expect_sha1` 换成 `(size, mtime)`：2026-10-31 的三态判据刻意"只看内容"（touch 不算冲突）。
- **资产表 6 个写函数过 `_ASSET_WRITE_LOCK`**（可重入）：无锁实测 8 线程写 32 行**只剩 4 行**（读表→改→整份保存互相盖）。判据 `test_asset_registry.py::TestConcurrentWritesDoNotLoseRows`。
- **门禁自己出错 = 不放行**：`write_generation` 里 `generation_guard()` 抛异常 → `GUARD_ERROR` 判 block（不写目标、留副本）。"没资格说没发现问题"。
- **向上找项目根要有边界**：`statement_registry._NEVER_A_PROJECT_ROOT` 跳过 `.pip-tmp` / `build` / `.git` / `__pycache__`。事故形态：`.pip-tmp/storygraph.rules.json` 让**每一条** tmp 路径都反推出 `.pip-tmp`，把那份清单套到所有用例上（Beats 多 3 列、`show` 变 `show show show`，32 条红）。
- **全局开关的判据要判在唯一写入口**：`remember_graph_files` 漏查曾往用户真实 `session.json` 写入 **849 个** tmp 项目。跑 UI 探针/截图先 `STORYGRAPH_NO_SESSION=1`；`session_store.save()` 自己判 `enabled()` 并剪 `.pip-tmp/`。
- **纯文字导入前必问"导出到哪个文件夹"**（`out_dir`），不猜子目录。程序化调用 `import_plain_text` / `import_chapter_files([*.txt])` 的测试都要打桩 `_ask_plain_text_export_dir`，否则 offscreen 模态目录框挂死。
- **中文源码别过 shell 文本管道**（`Get-Content|Set-Content` 乱码），只用 edit/write。

**扫描/刷新**
- **项目级扫描一律走 `story/scan_cache.py`**（按 `mtime_ns+size` / 项目指纹失效，**无 TTL**）；`find_control_file` 单候选直接返回。绕过缓存 → "连线很卡"（同一项目整份解析 6~8 遍，`detect_placement` 1435 ms/次）。别给缓存加 TTL（`test_scan_cache.py`）。
- **刷新已导入章节**：会话持 `IncrementalRenPyAnalyzer`（只逐出磁盘上不存在的文件、8 MB 上限）+ `import_chapters` 记 `(mtime_ns,size)`，`stale_import_files` 决定重读哪几个；0 改动不弹预览框（`test_import_stamps.py`）。

**唯一真源（别另写一套）**
- **优先级 = 一张常量表**：`core/authority.py`（mapping-exact 1 > declared 2 > mapping-rule 3 > registry 4 > inferred 5；engine 0 是天花板，unknown 6 = "没结论"）。规矩：**规则只补空缺，覆盖必须精确**；**投影不投票**（`*.generated.rpy` 等工具产物不进任何统计）。决策全文 `docs/真源与优先级_设计决策_2026-10-31.md`；守卫 `tests/test_authority_layers.py` **同时比对文档 §4.3**（两源不一致当场红）。
- **词汇表合并不看顺序**：`Vocabulary.merged([(层名, 词条), …])` 按 `VOCAB_LAYERS` → `AUTHORITY` 的层号合并；`plus()` 是"后来者优先"的老路，**新代码别用**（写反了"映射优先"就静默反过来）。同一层里的段次序由 `_SEGMENT_ORDER` 固定。
- **覆盖要留痕**：`core.authority.Override`（append-only，`sentence()` 唯一措辞）。命名域在 `Vocabulary.merged()` 里记（值不同才记）+ `explain()` + `labeled()` 填进 tooltip；身份域 `Identity.overrides`（仅"精确覆盖赢 + 声明过"时记）+ 选择器 tooltip「覆盖了：」。**别在 UI 里另写一句措辞**。
- **跨章继承的入口状态只许从 `app.stage_entry` 取**：真源是 `continuity.Chapter.entry`（**不按文件名排**：`chapter2_1` 整章没 scene）；整本分析 2.2–2.8 s → 查询**只读缓存**（键 = 项目根 + `project_source_signature`，46 ms），慢的那步在 `ui_qt.entry_warm` 后台。四条线都走 `session.stage_initial_state()`：`stage_digest_text` / `stage_size_rows` / 卡片小图 / 演出预览（**写回 at 后重算帧也要带**，否则第 1 拍背景消失）。`unknown` ≠ 空（写"不知道"）；手工拼的帧 `scene_src=-1` **不是**跨章 —— 看 `StageFrame.entry_state`。
- **「自定义倍数」：人填的数也是预设**（2026-10-31，队列第 8 项）：`50%` 只是 `0.5` 的特例，要填 0.75 / 1.25 就得给输入框。判据只有 `size_presets.parse_zoom(text)` / `zoom_error(value)`（认 `0.75` / `0.75×` / `75%`；`75` **不认**；范围 `ZOOM_MIN 0.05`–`ZOOM_MAX 5`，**越界拒绝不夹**）。可生成集合 `size_presets.GENERATABLE` 多了 `custom`（`PRESET_ORDER` 不动）；`sg_transforms.plan_define(key='custom', zoom=)` → `sg_zoom_075` / `zoom 0.75`。面板：下拉固定一条 + 选中才露的一行 **`QLineEdit`**（数值框吃滚轮），条目填错**仍可选**（灰的是「改」），按 `kind+tag` 记**原样的字**；请求多一个 `zoom`，`at` 仍 `'`'`，名字由窗口层定。两条查重补丁（真机逼出来的）：**逐字相同的 ATL 体**也算等价（`cover` → 已有 `bgfit`）、**拿不到图尺寸也能查倍数类**（`match_without_natural`：纯缩放的框是图尺寸的一次函数，两个参考尺寸同框 ⇒ 任何图尺寸同框；位置参与比较）。换景那条**不传旧图的 `natural`**（新图还没量）。真机 `sr_general`：1627 个尺寸行全都带这一条，116 条 zoom 变换（0.5 → 复用 `cg38402160`；0.75 → 才生成），91 行三条预设全都只能生成（全是 `black` 纯色，倍数不需要知道图多大）。判据 `tests/test_size_custom.py`（46）。
- **冲突三态**：`story/write_gate.classify(disk_bytes, base_sha1, draft_text, base_text=None)` → `ADOPT`（盘上已是草稿）/ `REBASE`（内容==基线，只是被 touch）/ `CONFLICT`。`source_unchanged()` **只比内容**（touch 不算冲突，行号继续可信）。`base_text` 给了才有行级合并候选，**有候选也仍是 CONFLICT**（合不合并由人定）。
- **"这张图是谁"**：`assets/asset_identity.resolve_image`（映射精确覆盖 → 项目声明 → 映射 glob 规则 → 资源表 Tag 列 → 路径目录段 → 文件 stem → **unknown 不猜**）。`storygraph.mapping.json` 只做覆盖/纠正；**不许**在 UI/其他模块按路径或文件名另写猜法（`cc_preview`/`sr_general`/`ling` 这类专名**不许**进逻辑分支）。声明过的行 `source='declared'`；每个 source 必须在 `authority.SOURCE_LAYER` 登记。
- **`outfit` 代号/表情编号的证据只数作者写的剧本**：`sprite_codes._script_texts()` 必须走 `parsing.discover.DERIVED_SUFFIXES`/`SKIP_DIR_PARTS`。实测：`chapter1_4.generated.rpy` 让同一句 `show` 数两遍 → `xing` 的 B 从"留空"变「B」→ 两个近乎克隆的项目给出不同代号表。**别用 `game.rglob('*.rpy')` 自己数**。
- **base 快照：先不做**（用户 2026-10-31 拍板）。表里只存哈希（`table_meta` 的 `来源指纹`）；三态判定（采纳 / rebase / 真分叉）的三处接缝与"什么时候重启"写死在决策文档 §八。**别顺手往 `.rpy` 里写元数据，也别存原文快照**。
- **舞台几何**：`story/atl.py::placement_box`。无 `at`/`fit` 的大图按引擎语义 **1:1 贴左上裁切**，**不**做"预览自动 fit"；`stage_snapshot` 的 `scene_at`（持续继承）/`layer_at`（show 记 hide 清）只进 state 内部键；缓存按**字节预算**（默认 256MB，`STORYGRAPH_STAGE_CACHE_BYTES`），不是张数。
- **"改图片大小"是三件事，不是一件**（2026-10-31 用户亲纠）：`scene` = **清台**（该层 displayable 全没），`show` = 只替换同 tag。所以改背景尺寸只能写 `show 图@变换` —— `app/stage.py::_bg_show_edit` 的函数体里**不会出现 `scene`**（判据 `test_stage_at_source.py::TestOverrideWritesShowNeverScene`，含"改完立绘还在"与"写 scene 就清空"的正反对照）。**超清图另有官方解法**：文件名 `xxx@2.png`（oversampling），引擎按 1/N 显示；唯一实现 `stage_render.oversample_of/display_size`，**只认文件名、永不自动改名**（图 ID 来自文件名）。
- **背景/图层是持续状态，写回要回"写它的那一拍"**：快照 state 有 `scene_src`/`layer_src`（0 基拍号，-1=不知道；**0 是合法值，别用 `int(x or -1)`**），`StageFrame` 带出，`plan_at_edit(source_index=)` 改来源拍；`AtEdit.requested_index`/`affects` 负责"你在第 200 拍点的、影响 41–266 拍"。真机实测：1013 个有背景的拍里 **972 个是继承的**（旧行为 0 可写 → 修后 673 可写）。
- **令牌没点到的 show/hide 条目，快照必须补**：渲染器 `_emit_leftover_stage` 一直会补，`stage_snapshots` 从前直接丢 → 卡片加第二个立绘 = "产物两句 show、预览一个人"（合成用例 `show='ling a; ye b'` + `op_order='show#0'` 复现；真机两项目 0 触发）。`AtEdit.extra_token/token_field` 是写入口那一侧的自保（插入新 show 补 `show#N`）。
- **尺寸面板 = 人话 → 项目已有 transform**（2026-10-31）：`story/size_presets.py`（`target_box` 用**同一个** `placement_box`；`match_preset` **只看效果**、容差 1.5px、位置一律左上角；**语义先于名字** —— 4K 放 1080p 时「50%」与「铺满」框相同，别把 `fit "cover"` 当 50%；带动画/改 alpha/旋转的**不算**尺寸变换；匹配不到 → `None`，**绝不编**）。行由 `stage_render.size_rows` 出（`_size_note` 同一份措辞 + 出处 `（第 N 拍设的）`），写回走 `stage.plan_size_edit`（`as_is` = `plan_at_edit(mode='clear')`；其余 `mode='source'` 改出处那一拍）。卡片 `ui_qt/size_panel.py` 只发 `size_requested`，窗口层 `apply_size_from_card` 复用演出预览的确认/脏标记门禁。`NarrowWrapLabel` 已搬到 `ui_qt/wrap_label.py`。
- **生成 `sg_*` 只有一个入口 + 三道锁**（2026-10-31，用户拍板）：`story/sg_transforms.py` 写 `game/sg_transforms.rpy`。**①指纹**：第 3 行 `# sg-gen v1 sha256 <正文指纹>`（UTF-8、**LF 归一**，CRLF 不算手改）；`SgFile.state` ∈ `missing/ok/foreign/hand_edited/bad_version/unreadable`，**只有前两种可写**；`SgPlan.apply()` 写盘前**再看一眼**磁盘（状态变了/条目对不上 → 抛错不写）。**②查重**（三层，命中任一就不写）：同名同内容 → `reuse`；`match_preset` 几何等价 → 用现成名字；文件里已有 → `noop`；名字被别人占了 → `sg_xxx_2`，**绝不顶掉别人的定义**（Ren'Py 同名后定义者赢）。**③引用存在性**：`missing_references()` 一次扫描（`scan()` 同时收 `at` 引用与定义）→ `pipeline.validate.E305`（L3 error，`文件:行号`，进体检 / CLI）；**只查 `sg_` 命名空间**，台词里的 "meet me at …" 不算。面板那条只是**提议**（`items.generate`），确认后**同一张框**里既有脚本 diff 又有文件 diff，顺序 = 写文件 → `session.refresh_transform_index()` → 改内存拍。名字/体/几何同源：`atl_body` 出体，`transform_def` 把生成的文本**再解析一次**算框。
- **跨章状态含 `scene_at`/`layer_at`**：`EXIT_STATE_KEYS` 两把钥匙；`pipeline/continuity` 用 `JUDGED_STATE_KEYS` 只判 scene/actors/layers/music/voice；`_state_to_dict` 对 `layer_at` 输出 dict 副本。
- **"本景还有几处单独改写"只报数，不代你删**（2026-10-31，队列第 4 项）：`story/override_advice.py`（`override_map(beats, start)` → `{桶: (Site…)}`；`override_advice(beats, source, kind=, tag=, prefetched=)` → `OverrideAdvice`）。为什么要有：尺寸面板改的是**源**那一行，而本景后面只要有谁又 `show` 了同一个对象，Ren'Py 就**重建** displayable ⇒ 源里的 `at` 从那一拍起失效；人改完看的是当时那一拍，于是"改好了"和"只改到第 12 拍"长得一模一样。规矩抄 `docs/超清CG与BG_零侵入导入_2026-10-31.md` §5.5：**只读**（↔ 文档 §一"能靠读解决的绝不写"）；0 处 `none` / 1–2 处 `hint` / ≥3 处 `notice`；**算不出来降档到 `hint` 并注明，绝不当 0 处**；`source<0`（跨章继承）→ `none` + `reason`（这一行本来就改不了，不冒充 0）。**口径只许一份**：生效 `at` 走 `beat_stage_parse.item_at(item, beat)`（`stage_snapshot._apply_show_verb` 也改调它）、"是不是同一图层"走 `core.vocabulary.same_layer_tag`（`app.stage._same_layer_tag` 转调它）—— 报的处数必须等于实际会盖回去的处数。接线：`size_rows(..., beats=None)` 每行 `advice`（**不给拍表 = `None`，不是"0 处"**）、`AtEdit.advice` → `diff_text()` 在 diff **之前**说这句话、面板一行短的 + 完整句进 tooltip（`hint` 灰 / `notice` 琥珀）。**合并回源（把那几处删掉）是错的** —— `show` 会重建 displayable，删 `at` 等于回到原样；正确做法见下一条。
- **合并回源 = 把那几处改成同一个值，不是删行**（2026-10-31，队列第 5 项）：`story/at_merge.py`（`plan_at_merge(beats, source, kind=, tag=)` → `MergePlan`：`steps` / `skipped` / `apply(selected=)` / `revert()` / `diff_text()`；`source_at_of(beats, i, kind=, tag=)`）。三条硬道理：① `show` 重建 displayable ⇒ 删 `at` = 回到**原样**（不是继承源的），而且那几条 show 常同时换图；② 目标值必须是**源那一行自己写着的** `at`，不是 `frame.scene_at`（那一拍可能已在某处改写之后）；③ 处数**不自己扫**，直接吃 `override_advice(...).sites`（`steps + skipped == advice.count`）。L4 门禁（文档 §5.5）：入口是人按的按钮「一并改成一样（N 处）」（`ui_qt/size_panel.py` `merge_requested`）→ `ui_qt/merge_dialog.py` **一处一个勾**（默认全勾；勾掉的一个字不动；改不了的列在下面带原因）→ 只改内存 Beat。**那几处已经和源一样时按钮不出现**（`OverrideAdvice.agreed`；真机 58 行全是这种，点了只会"没有要改的"）。提示行在 `agreed` 时**降档换口气**（"写着同一个 at…再改源只到第 X 拍有效"），不消失。
- **"改一条 show 的 `@at`"只有一个实现**：`story/beat_stage_parse.set_item_at(text, tag, at, beat=None)` → 机器码 `ok/noop/missing/complex/expression/blocked`（**措辞由调用方给**；面板说"高级区 Stage 改"，合并说"这一处要你自己改"）。`app.stage._layer_show_edit` / `_bg_show_edit` 都转调它（补条目/补令牌仍是 `_bg_show_edit` 特有的）。顺带修的老坑：`show cg cg1@half:`（行尾冒号 = ATL 块）过去改写会**吃掉冒号**，产物语法错；现在 `show_part_info` 剥进 `block`、改写完拼回（真机 35 处）。`show_part_info` 也从 `app.stage` 搬到了 `story`（面板与合并必须判同一件事）。
- **跨章继承的一行只能"在本章第一拍补一条 show"**（2026-10-31，队列第 6 项）：`scene_src/layer_src < 0` = 出处不在这本剧本里（本工具一次只载入一章）⇒ 没有"那一行"可改，写 `scene` 又会清台。`plan_at_edit(mode='chapter_first', scene_image=图名)` 把 `图@变换` 补到**第 0 拍**（`AtEdit.inserted=True`；产物只多一行），守卫各带原因：第一拍带 `condition` / 在 `menu`·循环块（`nest_path`）/ 行尾冒号 ATL 块 / 同 tag 已 show / 图名带 `@` 或与 tag 对不上 / 预设是「原样」（上一章的 at 看不到，补裸 show 可能白做）。补条目本体 = `app.stage._append_show_item`（与「从这一拍起改这一景」共用，含 `show#N` 令牌）；"出处是不是不知道" = `app.stage.is_unknown_source`（尺寸面板与演出预览共用，**`0` 是合法出处**）。**前提还没接线**：卡片/预览建帧都没传 `initial_state`，所以真机上卡片里 `source<0` 的行是 **0 个**（探针 `tools/_probe_entry_real.py`）；`build_frame_at/build_frames/snapshot_rows` 已收 `initial_state`，下一步要喂的是 **continuity 链条算出来的入口**（不是"上一个文件名"：`chapter2_1.rpy` 整章 130 拍没有 scene，上一文件的出口是空的）。真机：`chapter1_1→chapter1_2` 继承 `bg bg9_2`（无 at）→ 补 `show bg bg9_2 at bgfit` 到第 1 拍，影响 1–1 拍（那处接缝本来就是 S0：只影响第 1 句台词）。
- **"引擎写法 ⇄ 人话"只有一层**：`assets/vocabulary.py` 的 `Term(kind,id,label,code,char,note)`；`id` 是写进剧本的真值；查不到**原样显示**（补充不是白名单）；模糊匹配只在唯一命中时认；服装/表情**不搬家**（`outfit_codes` 投影进来）。
- **"显示值 ≠ 真值"只有一层**：`ui_qt/labeled_choices.py`（原语 `addItem(显示, 真值)`，真值进 `UserRole`，**真值优先**）。坑：① `mapping_cached()` 返回**三元组**，唯一访问器是 `session.vocabulary()`；② 可编辑下拉 `setEditText` 后 `currentIndex` 仍指上一项，`combo_value` 必须同时比文本；③ `dynamic_spec_columns` 曾丢 `choices_from` → `with` 的「转场」一个候选都没有。

**映射（Excel/JSON 双写法）**
- 同目录 **Excel 优先**：`storygraph.mapping.xlsx`（`assets/mapping_excel.py`；一张表=一个顶层键、行序=数组序、空格子=不写这个键、`备注` 列忽略、表头可不在第 1 行）。**xlsx 只读，绝不回写人做的表**；「保存到项目根」在生效源是 Excel 时写**同目录 JSON 快照**。生效源每处都要说出来。
- 表格编辑入口：`ui_qt/mapping_editor.py`（表）+ `assets/mapping_edit.py`（`SPECS` 六段），**JSON 仍是唯一产出**。三条规矩：① 深拷贝 + 只改认识的键（`to_data` 对没碰过的条目逐字节等价）；② 空壳行不落盘、删光的段整键去掉、原本空的段保留；③ 表**不许 import `save_mapping`**。坑：`itemChanged` 里**不许重建表**（抢焦点）；列宽**不许伸缩有内容的列**（末列「属性」760px、首列 `#` 380px），用"每列定宽 + 末尾空列吃满"。QLabel 是纯文本，中文文案里 `**`/反引号会原样显示；要 `<b>` 必须显式 `setTextFormat(RichText)`。
- **`B7` = 服装字母 + 表情编号**：`assets/sprite_codes.py` 是唯一真源（`scan_project` 建表 / `translate` 双向换算）；`outfit_codes`/`expression_codes` 两段、Excel 两张同名表、「按项目自动填」「存空模板」都挂在它上面。铁律：**一组一个属性**（`show ye hi` 不撤销服装）；**注释取最后一个代号**；**跨角色声明过的属性不算服装标记**。数不出来就**留空并点名**，不按顺序硬凑；已有行一条都不改。
- **「角色 0」= 没有覆盖，不是没读到**（用户 2026-11-01 截图问"为什么角色是0"）：`characters` 段空着时名字照 `characters.xlsx` 来（`asset_identity._display_of`，映射为空给 `display='凌跃捷'`）。所以编辑页空表要**当场说清**"表里有 N 个角色 / 空着就行"，并给「把资源表里的角色写进来」按钮（`mapping_editor.import_characters`，**只加不改**）。**不许开窗自动抄** —— 抄进映射就盖住资源表，改 xlsx 名字不生效。判据 `test_mapping_chars_from_table.py`。

**舞台/卡片/过场**
- **卡片单拍舞台图**：渲染走 `stage_render.render_frame`（与「演出预览」同一函数）；一律后台线程（首次大盘立绘 1.3 s），防抖 120 ms + 2 线程 + 门闩；重画判据用 `frame_signature` 内容指纹（**不许** `id(frame)`、不许换拍拿上一张顶）。固定 288×162（`compact=True`）；`_stage_now`（只看）与 `_stage_box`（能改）是两块，普通卡常显，控制流卡收起。compact 小图的**名字/台词要按缩略图字号重画**（`draw_readable_say`）：铺底要**不透明**且**名条也要铺**，名字字号不许被引擎名条宽度卡死。
- **过场是项目自定义的**：`$ renpy.transition(...)` 是 Python 语句（`Beat.code`），走 `story/transition_expr`（安全字面量解析，无 eval）→ `transition_catalog`（扫 `define`）→ `app/transition_fx`。**认不出/缺遮罩如实降级 + 标「无法精确预览」，绝不假装淡入**。

- **带 ATL 块的 `show`（2026-10-31 打通，五条坑连着踩）**：`show lan happy at right:` + 缩进块现在是 `kind=show` + `payload['atl']`（块体原文）→ `Beat.opaque` → `emit_opaque` 逐字吐。要改就得同时记住：
  ① **判据是"节点有没有块体载体"，不是"是不是 show"** —— `_parse_statement` 的带块守卫（`_BLOCK_CARRYING_NODES`）会把没有载体的节点整条打回 `unmodeled`。给 `SceneNode` 加载体才允许它进这张表，别只加个类名。
  ② **`ln.text` 自带缩进**（实测 `'        zoom 0.15'` 且 `indent=8`）。按 `ln.indent - head.indent` 补空格会变成双份缩进（8+4）；正确做法是 `_strip_leading_width(raw, base)` **只剥掉首行那一段**。
  ③ **`LogicalLine.subblock` 里没有空行**（空行不生成节点）。纯靠 subblock 重建会吃掉块体中间的空行 → 改别人排版。必须按 `(首行行号+1 .. 末条 subblock 行号)` 从**源文件**取（`self._source_text(path)`）。
  ④ **opaque 的 `ordered` 令牌必须排在 `mark_op('show')` 之后** —— 先记就吐在 `show …:` **前面**，产物直接语法错。
  ⑤ **行尾冒号要走"编解码往返"**：`_split_show_items` 把它剥成 `item['block']=True`（否则 `at` 值变成 `right:`，变换查不到），`show_item_part` 再拼回去；`render` 那边的 `emit_opaque` 还有一道兜底（块体首行缩进 → 给上一行 `show/scene` 补 `:`）。
- **ATL 那一刀还欠 11 条红（2026-10-31 13:42 全量实测）**：`ShowNode.atl` 没进 `_FACTORY_FIELDS`（`test_kind_declaration_sync` / `test_field_reconciliation`）、`show … as title:` 的冒号被误当块首（`test_show_clause_words`）、块体承载标记（`test_leaf_blocks_are_not_swallowed`）、`test_loop_model` T0/T3、以及 6 份金标没刷新（`golden_baseline.json` 停在 10-30 22:59）。**先修代码再刷金标**，别反着来。定位：`python tools/fasttest/run.py --retry-failed`。
- **归属审计（P1，2026-10-31）**：`_HANDLER_OF`（模块级）是"每种语句去哪"的**唯一清单**；`_BeatBuilder.unassigned` 记"既没进 pending 也没建拍"的语句，`beats_from_parse_result(..., report=[])` 取出来断言。判据是**结构**（不靠语法白名单），新加写法自动落进 attach。它当场抓到 `StatementKind.UNKNOWN` **一个 handler 都没有**（读新版表/手改 JSON 时静默丢语句）——已补 `_emit_unknown`，整条原文进 opaque。
- **多行源码别走 shell 管道**（2026-10-31 又踩）：`python - <<'PY'` 里塞带 `show ...` 的测试源码，`show` 行会被吞掉，于是"复现"出"show 没了"的假回归。探针要写成 `.py` 文件再跑（铁律 10 的同族）。
- **本机没装 pytest 且 pip 无网**（`from versions: none`，换镜像也一样）：验证只能跑 `tools/_*.py` 探针；`tests/` 里的用例照写（用户环境有 pytest）。`tests/test_hell_atl_mapping.py` 末尾自带标准库运行器，`python tests/test_hell_atl_mapping.py` 可直接跑。

**自定义语法**
- **复杂子句靠"声明"**：`rules.json` 的 `statements[].clauses`（`story/spec_clauses`），`kind ∈ word/words/expr/transition/number`；列名 `head·列名~关键字`；读值**必须拿全部声明子句当边界**（只给 `at` 会把后面的 `with …` 吞进值里）；写回只换值那一段。关键字只在**顶层完整词**认。
- **`$ 函数(...)` 是技术调用，不是语句**：`story/call_expr` 只按顶层逗号切实参、不 eval；内置 `renpy.transition` + 项目 `rules.json` 的 `calls`；动态列 `head·参数@0` / `head·参数@kw`；同一 head 两次/多行/`python:` 块体内**不建列不写**；改值只动 `Beat.code` 并同步 `ordered`。
- **`show … at … behind …` 的 at 在 `classify_show_words` 里是 list**（`at`/`behind` 多目标；`with`/`as` 才 str）：`beat_stage_parse._clause_values` 用 `', '.join` 归一，单目标给 str，否则 `layer_at`/actors 拿不到变换。

- **界面上"可删的独立件"** 范式（如 `ui_qt/github_star.py`）：一个文件 + 一处 try 挂载，不 import 窗口/会话代码，点击立刻返回，启动不联网。判据 `test_github_star.py`。
- **许可证 GPLv3 必须四处同源**：`LICENSE` / `pyproject.toml` / `README.md` / 随包 `快速上手.md`。10-23 曾误写 MIT，**别改回去**。
- `tools/audit_real_project.py` 是坏脚本，别信输出。
- **并发写入常态**：动手前 `git status` + 时间戳；文档只追加；提交按文件分组；写完回读校验。

## 四、通用性（2026-10-31 审计，用户质疑"只服务我的项目"）

- **结论：逻辑层没有按项目名分支**（AST 扫过非 docstring 字符串常量，21 处命中全是**给用户看的示例文案**；`pipeline` 里 `cc_preview` 只出现在"不拷这些重目录"的 SKIP 列表，无害）。
- **但有三处"预设了某类项目形态"**，改的时候按这个清单：
  1. `assets/sprite_codes.py` 的 `_EXPR_GROUPS` / `_OUTFIT_GROUPS` **是白名单猜测**（`outfit/clothes/body/服装/服`、`expr/face/eme/表情`）。实测：`group dress` / `costume` / `wardrobe` / `armor` 全认不出（留空并点名，不会硬凑，但"按项目自动填"在英文项目上等于失效）。爆炸半径只有 `scan_project` + 「按项目自动填」；**立绘卡分格走 `asset_dimensions`，不写死组名**。要改成"从 `layeredimage` 的 `group` 全量列出，由人勾哪组是服装"。
  2. `assets/vocabulary.py` 的 kind 同义表（`outfit/costume/clothes → costume`）—— 只归一**人写的** kind，可接受，但同样漏 `dress/wardrobe`。
  3. `examples/mapping/sr_general.mapping.{json,xlsx}` 是**真实项目导出**（45 角色 / 61 条 `game/cc_preview/...` 规则）。没进发行包（`dist*` 里没有），但在仓库里 = 观感问题 + 隐私问题。应换成 `demo/` 那种自造小项目。
- **`outfit` 出现在表里的原因**（用户问的那个）：它不是被"注册"的变量。`draft_mapping()` 自动初稿里 `attributes` 恒 `{}`、`outfit_codes` 恒 `[]` —— 不会自己长出 `outfit`。表里出现只有两条路：① 项目自己写了 `group outfit`，「按项目自动填」照实记；② 人在「属性」表填了维度 `outfit → 服装`（改名层，`beats.group_labels` 只当显示名覆盖）。仓库内 10 个 xlsx 实测**没有任何一个含 outfit 字符串**。
- 中文界面 vs 英文项目：UI 全中文（团队自己用没问题），但表头/别名都配了英文（`FIELDS` 里 `tag/display/glob/kind` 都认），英文项目能落表。

## 五、当前的片（判据见同名测试）

E1/E2/E3 引擎事实｜纯文字导入 S1~S4｜自定义动作弹窗 v4｜资产维度结构化｜立绘卡"一维一格"｜S1 删边=删拍｜S2 选择点（menu 住 `<章>_choices.rpy`，剧本只多一行 `call`）｜S3 控制流守门｜L7 版式恢复｜导入冲突预检（取消=零写）｜记忆门禁｜导出落点｜卡片单拍舞台图｜右栏竖向配额｜重号不级联｜derived 段要交代｜写入口探针｜GitHub star｜E1/E3+D3 拖线落点由结构决定｜🎮 引擎试玩 + 演出专用版｜舞台可疑行导出前确认｜卡片三修｜二轮反馈五件套（资产身份解析 / 舞台几何 / 自定义过场 / UI 宽窄 / 非法 show 门禁）｜自定义模块 v7（复杂子句 / 技术调用 / 跨章 scene_at）｜UI 第 6 条（删「⋯ 结构」、滚轮永不改值、分栏按比例、`resolve_chapter` 不跨项目）｜资产选择器左右栏两种口径｜词汇表 + `labeled_choices` 四处下拉｜尺寸面板（铺满/适应/50% → 项目已有 transform，匹配不到才生成 `sg_*`）｜生成 `sg_*` 三护栏（sha256 标记 / 写前查重 / 引用存在性）｜**显式「换景」**（`scene` 只在人按下时写，确认框报"会清掉 N 个立绘"；顺带修了"scene 不清 CG 图层"——真机 40/61 个拍预览在骗人）｜**本景单独改写计数**（`override_advice`：只报数、三档提示、算不出来降档，面板/确认框同一份措辞）｜**合并回源**（`at_merge` + 一处一个勾的确认框：改的是 `@at` 不是删行；已一致时按钮不出现；顺手修掉 ATL 冒号被吃掉的老坑）｜**跨章继承闭环**（入口状态 = `continuity.Chapter.entry`，`app/stage_entry.py` 按项目指纹缓存 + `ui_qt/entry_warm.py` 后台算一次；卡片「当前舞台」/「尺寸」/小图/演出预览四条线都接上了 → 「本章第一拍补一条 show」真按得到）｜「自定义倍数」输入框（`parse_zoom` 唯一判据；面板一行 `QLineEdit`；`match_without_natural` 让图找不到也能查重；真机 0.5 复用已有 `zoom 0.5`、0.75 才生成）｜**随包两份预设改成真项目扫出来的**（`tools/build_presets_real.py`：92 文件 / 312 条 show / 服装 12 行 / 表情 91 行；A/B 只认注释证据，xing 各 1 处 → 留空；`角色` 候选不封闭；`scan_project` 新增"表和注释只对上 N/M"提示）。

**真缺口**：D4 Excel 前端上限｜拍级三路合并｜G1 行为级契约进 CI（`skipif(not REAL)` 在 Linux 必 skip）｜G2 引擎版本适配层｜从当前拍 warp 的近似性｜引擎试玩只支持 Windows 源生 SDK。
**待用户决定**：D1 树/表归属｜D2 两套生成器收敛｜D3 rename patch 接 Qt｜D4 Stage 格级下拉｜D5 坏味道课题｜D6 pytest.ini basetemp｜D7 多引擎加深｜D8 提交策略。

## 六、P0 审计（六轮，细节见 `docs/P0审计_*.md`）

- **最高频 P0 = 两边口径不一致**（老表英文键 vs 中文表头 → `_canon_field`/`BEAT_HEADERS` 两向都要报）；**最有效探针 = "同一件事不同出口两种待遇"**。
- 载体三轴（真机 312 个 `.rpy` 全 0）：行边界（`core/textlines.py`，Ren'Py 只认 `\n`）｜编码（`SourceStyle.apply(source=)` 严格解码抛 `SourceNotUtf8`，所有落盘点唯一末班车）｜`commands.py` 各处补 `source=`。
- Excel 是第二份源码：`story/xlsx_preserve.py`（备份 3 代 + 用户整页带走 + 自加列按 **LineId** 回填）；`removed_count` 必须真算；`excel/cells.py::xml_safe` 剥控制字符留痕。
- 新功能先跑 `tools/_p0_audit_round6_stress.py`（19 条难缠工程）；真机 `sr_general` 太干净。`test_scan_golden` 红先看"少了 []"。

## 七、协作与定位

- 单人开发：AI 写代码，用户设计/测试/验收。多讲"为什么这么设计、否决了哪个备选"；用户写代码时别抢答；**接受判据校准**。
- 说人话（编剧也看得懂）、每个观点要"为什么"、给指标/表格/P0-P1 标签。
- 定位：**已有项目的外科手术刀 + 资产治理层 + 跨引擎逃生舱**；三条护栏 = 不改引擎 / 不改用户字节 / 能带走。
- 图管"章与章怎么串"，卡片管"这章怎么演"，桥只有一行 `call`。menu 的家 = 编排层文件，**永不写进剧本**。
- 引擎对坏写法沉默（落空/章末 jump/menu 嵌 if，lint 全过）→ 发现是工具的活（`story/exits.py`，七档出口）。**"不能写剧本" ≠ "不给你连线"**：算不出就画草图 + 状态栏说清。

## 八、发布包

- 清单（`packaging/make_release.py::REQUIRED`）：两个 exe + `_internal\`、`剧本空表格.xlsx`、`demo\`（带真 PNG）、`快速上手.md`、`新手快速操作手册.md`、`manual_img\`、`storygraph.rules.json`、`storygraph.mapping.json`、`LICENSE`。判据 `test_release_manifest.py`。
- 两份预设（`storygraph/presets/`）**是真项目扫出来的**，重建脚本 `tools/build_presets_real.py`（`--dry-run` 只打印）；映射与 rules 同源同发，但**不自动套进项目**（映射写的是"这个项目的哪张图是谁"），见契约 §82。
- 顺序：fasttest 全绿 → `pyinstaller packaging/storygraph.spec --noconfirm` → `StoryGraph-CLI.exe selfcheck` → `make_release.py --zip` → **`python tools/_probe_release_zip.py <zip>` 开箱核对**（顶层只有 `StoryGraph/`、清单齐、`demo` 与 `.bak`/`__pycache__` 为 0）。⚠ PowerShell `2>&1 | Select-Object` 会把 stderr 变假 exit 1，用 `*> .pip-tmp/pyi.log`。
- `selfcheck` 的 `REQUIRED_MODULES`（2026-11-01 起 **30** 个）要跟着新模块涨：`core.atomicio` / `core.preflight` / `excel.safety` 这类"函数体内 import / 启动早期 import"正是打包后最容易静默缺失的；「系统 DLL」项**缺 icuuc.dll 会红**（GUI 起不来就是失败，不许假绿）。
- 手册截图 `tools/_shot_manual.py`：`offscreen` + `STORYGRAPH_NO_SESSION=1`；**不许 `win.close()`**（未导出改动弹模态框永卡），结尾 `os._exit(0)`；结构页用 `zoom_reset→zoom_by(2.0)→center_on_node`。
- 真机工程会长大（`sr_general` 55→96 章）：金标红先 `tools/golden_baseline.py --check`，确认项目变了再 `--save`。基线**别把工具副产物算成输入**（按 `resolve()` 去重、不收 `~$*` 与 `*.bak*`）。
- **演出专用版**（`packaging/make_stage_release.py`）：`--dist dist/release --sdk <SDK> --out dist/stage --zip`。包内 = 标准版 + `renpy_sdk/`（约 330 MB）+ `项目预设/` + 两个说明；13 项 REQUIRED、3 项 FORBIDDEN、zip ≤ 4 GiB。**先 `make_release.py --stage-only` 铺带 extras 的 `dist/release` 再当 `--dist`**。
- 2026-11-01 第六段（§81）：patch_export 469→90（照 docstring 的十步表拆成 _patch_step_* + _PatchRun）、_replace_matched 407→105（照 P0–P7 表拆成 _pair_* + _Pairing）；ui_qt 五模块环清零（新叶子 ui_qt/window_layout.py 六个数字、ui_qt/text_html.py 转换器，两处都 re-export 老名字）；print 分两档量（print_calls 240 总量 / **print_library 3**）：库内 12 处里 9 处换成 swallow。⚠ 拆函数最容易死在原来是覆盖还是累加（pre_changed 就是覆盖语义，写成 += 会把改动行数报成两倍）。
