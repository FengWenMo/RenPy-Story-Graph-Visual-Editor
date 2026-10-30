# 项目记忆：RenPy 剧情编辑器（storygraph）· 索引页

> 只放**最容易踩的坑**与**指针**；易变事实（用例数/行数/性能）读代码或跑测试，别信文档里的 ✅。
> 详情：契约 `docs/契约速查_storygraph.md`（按需 grep）｜逐日 `docs/开发日志.md` + `.workbuddy/memory/2026-10-*.md`｜路线图 / 竞争格局 / 短板评估 见 `docs/`。

## 一、十条铁律（违反任一条都会静默毁掉用户项目）

1. **`.rpy` 唯一真源；不归己的行逐字节不动**。只逐行配对替换真改过的行，绝不整份重生成。
2. **要么全对、要么完全不动**。任一行配不上 → 整份放弃 + 报错，不产出"半对"。
3. **认不出就原样抄**。未知块原样保留，不补 `$`、不包 `python:`、不改缩进。
4. **猜一个名字比说"不知道"坏得多**。只认字面量，不模糊匹配。
5. **绝不静默全量覆盖**。落盘过门禁；`HANDWRITTEN_TARGET` 拒写；改用户 `.rpy` 先出 diff，未确认零写。
6. **教它升级，不许改字节**。教会自定义语法后首次导出必须与教会前逐字节相同。
7. **不许静默丢行**（txt 导入/表格/补丁三条路）。判据要"整段相等"。
8. **一个落盘点一个登记**（`WRITERS`，守卫 `test_single_write_entry.py`）；走 `write_text_atomic`/`write_generation` 的不登记。
9. **本工具不猜语义**。意思由人标注，工具只发现/展示/检索。
10. **含中文/转义的复现一律写 `.py`** —— `python -c` 经 Bash 会改坏样本（曾"复现"出不存在的 P0）。

## 二、引擎事实

- `lint` 默认退 0，**必须 `--error-code`**；`has_image()` 不是合法性判据；`probe_complete=false` 当硬失败。
- 事实文件 `<项目>/storygraph.engine_facts.json`：每条带 `source`；两源不一致记 `conflicts`，不静默挑一个。
- **覆盖内置语句 = 完全替换**；**fork 引擎新增语句对工具隐形**；`test` headless 不可用。
- 真机基准（sr_general）：images 317 / tags 235 / transforms 155 / labels 106。
- 本机 SDK：`D:\software\renpy-8.4.1-sdk` 实际是 **8.5.3.26051504**（看 `renpy/vc_version.py`，别信目录名）；演出专用版随包的就是它（`renpy_sdk/`，去 `vscode/`+`tmp/`）。
- **引擎试玩**（`app/engine_preview.py`）走"镜像 + 注入 + 隔离"：镜像在 `%LOCALAPPDATA%\StoryGraph\engine_preview\`；`config.label_overrides["start"]` + `RENPY_SKIP_MAIN_MENU/SPLASHSCREEN` 直接进章；`--warp game/x.rpy:行` 进单拍；`--savedir`+`APPDATA/HOME` 重定向 + `RENPY_DISABLE_BACKUPS` 保证不碰项目、不污染用户目录。
- 验收截图**用引擎自己的 `renpy.screenshot`**（注入 `config.periodic_callbacks`），别用 `PIL.ImageGrab` 截窗口 —— 窗口被挡时截到的是挡在前面的窗口（2026-10-28 误判过"引擎黑屏"）。

## 三、最高频的实操坑

- **两个 registry 不是一回事**：`session.registry()`=资产表；语句表是 `StatementRegistry.for_project(root)`。混用 → Qt 槽吞异常 → "点了没反应"。
- **`EditorSession` 有 `__len__`** → 没载入章节时 falsy。判空用 `is not None`，绝不用 `if self._session:`；默认值兜底别用 `or`（`int(x or -1)` 把第 0 拍当没载入）。同类：`Statement.source` 是 `(路径,行号)` 元组。
- **全量测试只用 `python tools/fasttest/run.py`**（并行 + 独立 basetemp + 运行锁）。裸 pytest 串行 ~20 min 且两个并发互删 `.pip-tmp/pytest`；`.pip-tmp/` 会到 GB 级，别用 du/find 扫。15s 心跳、300s 无输出退 **124** 并打印 nodeid；`--retry-failed` 只复跑上轮纯全量失败。
- **耗时由少数真机金标主导**（scan/validate/patch/describe ~730s），方差 ±40%，别当回归。`test_disk_hygiene` 必须第一个跑。**pytest-xdist 默认 `--loadscope-reorder` 会覆盖重文件优先**（fasttest 已传 `--no-loadscope-reorder`；手写 `-n` 别漏，实测 7m14s→4m36s）。
- **12 workers 会 `0x8007000e` 全体起不来**（内存不够 import PySide6）→ 降 `--workers 6`。
- **无 `QApplication` 就建 Qt 控件 = 进程 abort**（无 traceback）。Qt 测试类要 `qapp` autouse。
- **面板少一个转发方法** = AttributeError → 模态框 → 挂死。守卫 `test_every_panel_method_the_window_calls_really_exists`。构造期 `_refresh_preview` 早于控件创建 → `getattr` 守卫 + 建好后重刷。
- **QSS 吞 `QTreeView` 展开三角** → 自绘 `▾/▸` + `setRootIsDecorated(False)`；判 dock 开没开用 `isHidden()`；`saveState()` 要求工具栏有 `objectName`。
- **Qt 槽里异常只打 stderr = "点了没反应"**：`QMessageBox.addButton(文本, 角色)` 第二参数是 **ButtonRole**，传 StandardButton 当场 TypeError（真机事故：无源节点四个修复入口一起失效）。菜单/右栏动作一律过 `window_nodes._menu_action` 安全壳；有模态框的动作必须有"真建框"判据（`test_ui_reaction_contract.py`）。
- **模态打桩要打全**：`delete_card` 走 `_ask_delete_mode` 的 `box.exec()`，只 patch `QMessageBox.question` 会卡死（xdist 全空闲、整体 0 CPU，看着像卡 20 分钟）。`QMenu.exec` 打桩不可靠 → 菜单建成纯函数再测"挂了哪些动作"。
- **全局开关（记忆/自动保存）的判据要判在唯一写入口**：`remember_graph_files` 漏查曾往用户真实 `session.json` 写入 **849 个** tmp 项目。跑 UI 探针/截图先 `STORYGRAPH_NO_SESSION=1`；`session_store.save()` 自己判 `enabled()` 并剪 `.pip-tmp/`。
- **纯文字导入前必问"导出到哪个文件夹"**：落点由人指定（`out_dir`），不猜子目录。任何程序化调用 `import_plain_text` / `import_chapter_files([*.txt])` 的测试都要打桩 `_ask_plain_text_export_dir`，否则 offscreen 模态目录框会挂死。
- **项目级扫描一律走 `story/scan_cache.py`**（按 `mtime_ns+size` / 项目指纹失效，**无 TTL**）；`find_control_file` 单候选直接返回。绕过缓存 → "连线很卡"（同一项目整份解析 6~8 遍，`detect_placement` 1435 ms/次）。别给缓存加 TTL（判据 `test_scan_cache.py`）。
- **刷新已导入章节**：会话持有 `IncrementalRenPyAnalyzer`（只逐出磁盘上不存在的文件、8 MB 上限、本批不淘汰）+ `import_chapters` 记 `(mtime_ns,size)`，`stale_import_files` 决定重读哪几个；0 改动连预览框都不弹（`test_import_stamps.py`）。
- **卡片上的单拍舞台图**：渲染必须走 `stage_render.render_frame`（与「演出预览」同一函数）；一律后台线程（首次大盘立绘 1.3 s），防抖 120 ms + 2 线程 + 门闩；重画判据用 `frame_signature` 内容指纹，**不许** `id(frame)`、不许换拍时拿上一张顶。图在「当前舞台」标题下、与摘要并排、固定 288×162（`compact=True`）；`_stage_now`（只看）与 `_stage_box`（能改）是两块，**普通卡都常显**，控制流卡才收起。compact 小图的**名字/台词要按缩略图字号重画**（`stage_render.draw_readable_say`，2026-10-29）：整帧缩小后字只剩 3~4px 是"方块"；铺底要**不透明**且**名条也要铺**（名条可能在文本框上方），名字字号**不许**被引擎名条宽度卡死（名条底跟着字长长）。
- **滚轮永远不许改值**（2026-10-30 二次收紧，`ui_qt/wheel_guard.py`）：`WheelSafeComboBox`（卡片「说话人/背景/音乐/语音」四格）+ `WheelValueGuard`（挂 `QApplication`，`ensure_installed()` 幂等，兜住所有裸 combo/数值框/滑块）。滚轮一律不改值、**显式转交**外层滚动区；只有**候选列表已弹开**才放行。别再写"有焦点就放行"——那正是"滚动导致选择切换"复发的原因（可编辑裸 combo 连焦点都不用就会换值并吃掉滚动）。
- **右栏宽度不是"滚动区的事"，是"卡片自己多宽"**（2026-10-30 第 7 条，`QComboBox` 是最常见的地板杀手）：`QComboBox.minimumSizeHint()` 默认按**最长条目**算宽（真机 40 多字图名 → 单个下拉 1025px，卡片地板 1077px，右栏 357 ⇒ 横向滚动条 + 全部控件越界）。`WheelSafeComboBox` 里已钉 `AdjustToMinimumContentsLengthWithIcon` + `MinimumContentsLength=4`，**别改回默认策略**；卡片滚动区 `ScrollBarAlwaysOff` 只是兜底，真正的判据是"卡片最小宽 ≤ 右栏"与"逐拍量右边界"。同族：`_dirty_label` 的 9 字文案（321px 那一行）也顶宽过卡片，只能留「● 未保存」；`window_state.LAYOUT_VERSION=3` + `LEGACY_CARD_SHARE=0.45`（对半的旧记忆等于 `CARD_SPLIT_DEFAULT`）。
- **中文源码别过 shell 文本管道**（`Get-Content|Set-Content` 乱码），只用 edit/write。
- **"这张图是谁"只有唯一真源**（2026-10-30）：`assets/asset_identity.resolve_image`（判定顺序：项目自声明名字 → 映射精确覆盖 → 映射规则 → 资源表 Tag 列（须是已知角色 tag）→ 路径目录段 → 文件 stem → **unknown 不猜**）。`assets/asset_mapping.py` 的 `storygraph.mapping.json` 只做覆盖/纠正，没它也能按目录推断；**不许**在 UI/其他模块按路径或文件名另写一套猜法（`cc_preview` / `sr_general` / `ling` 这类项目专名**不许**进逻辑分支）。
- **映射有两种等价写法，同目录 Excel 优先**（2026-10-30）：`storygraph.mapping.json` / `storygraph.mapping.xlsx`（`assets/mapping_excel.py`：一张表=一个顶层键、行序=数组顺序、空格子=不写这个键、`备注` 列刻意忽略、表头可不在第 1 行）。**xlsx 只读，工具绝不回写人做的表**（load→save 会丢图/图表）；「保存到项目根」在生效源是 Excel 时写**同目录 JSON 快照**。生效源必须每处都说出来（`status_text` 的 `（Excel）` + `mapping_cached` 的"还有另一份"警告 + `mapping_sources` 的固定顺序），**不许靠猜**。
- **舞台几何只有唯一实现**（2026-10-30）：`story/atl.py::placement_box`（`Placement.fit/xsize/ysize/nearest`）。无 `at`/`fit` 的大图按引擎语义 **1:1 贴左上裁切**，**不**做"预览自动 fit"（用户拍板：预览必须真实）；`stage_snapshot` 的 `scene_at`（持续、继承）/`layer_at`（show 记 hide 清）只进 state 内部键，显示列不变；缓存按**字节预算**（默认 256MB，`STORYGRAPH_STAGE_CACHE_BYTES`），不是张数。
- **过场是项目自定义的，不是"淡入"**（2026-10-30）：`$ renpy.transition(...)` 是 Python 语句（`Beat.code`），要 `story/transition_expr`（安全字面量解析，无 eval）→ `story/transition_catalog`（扫项目 `define` 出 `Effect`）→ `app/transition_fx`（中间帧）。**认不出/缺遮罩如实降级 + 标「无法精确预览」，绝不假装淡入**。
- **"两个回调"的件，测试打桩要打全**（2026-10-30，全量卡死级）：`sprite_grid` **优先** `on_pick_image_result`（生产接 `main_window.pick_sprite_result` → 真弹资产选择器），老 `on_pick_image` 只作降级；只打桩老回调会被新回调短路 → offscreen 真弹模态框 → fasttest 连续 300s 无输出判 **124 卡死**（`test_ui_qt` 4 条都踩）。同族：导出的「舞台可疑行 / 非法 show」弹窗在 `_review_stage_suspects` 里 `dlg.exec()`，**没有桩就别走到它**。
- **复杂子句靠"声明"，不靠把 `pattern` 拉长**（2026-10-30）：`rules.json` 的 `statements[].clauses`（`story/spec_clauses`），`kind ∈ word/words/expr/transition/number`；列名 `head·列名~关键字`（`~` 保留）；读值时**必须拿全部声明子句当边界**（只给 `at` 一句会把后面的 `with …` 吞进值里）；写回只换值那一段，值没变逐字节原样。关键字只在**顶层完整词**认（引号/括号/注释里不算）。
- **`$ 函数(...)` 是技术调用，不是语句**（2026-10-30）：`story/call_expr` 只按顶层逗号切实参、不 eval；内置 `renpy.transition`（`spec_args.BUILTIN_CALL_SCHEMAS`）+ 项目 `rules.json` 的 `calls`；动态列 `head·参数@0` / `head·参数@kw`；同一 head 两次/多行/`python:` 块体内**不建列、不写**；改值只动 `Beat.code` 那一行并同步 `ordered`。预览只按声明里的 `effect`/`seconds_arg` 演，没写就如实 unknown。
- **有 `__bool__` 的对象别用真值判断**（2026-10-30 又踩一次）：`StatementRegistry.__bool__` 判的是**语句表**，只有 `calls` 的项目是 falsy —— `dialog_calls._load` 因此读成空表。判空一律 `is not None`。
- **复杂子句 `show … at … behind …` 的 at 在 `classify_show_words` 里是 list**（`at`/`behind` 都是多目标；`with`/`as` 才是 str）：`beat_stage_parse._clause_values` 用 `', '.join` 归一，单目标给 str；`stage_snapshot` 的 `layer_at`/actors 才能拿到变换。
- **跨章状态现在含 `scene_at`/`layer_at`**（2026-10-30）：`stage_snapshot.EXIT_STATE_KEYS` 加了这两把钥匙，`pipeline/continuity` 用 `JUDGED_STATE_KEYS` 显式只判 scene/actors/layers/music/voice（内部几何键不进 outcomes）；`_state_to_dict` 对 `layer_at` 输出 dict 副本，不能走通用 `str()`。

- **界面上"可删的独立件"** 范式（如 `ui_qt/github_star.py`）：一个文件 + 一处 try 挂载，不 import 窗口/会话代码，点击立刻返回（网络后台），启动不联网。判据 `test_github_star.py`。
- **许可证 GPLv3 必须四处同源**：`LICENSE` / `pyproject.toml` / `README.md` / 随包 `快速上手.md`（最后一处最容易漏）。10-23 曾误写 MIT，**别改回去**。
- `tools/audit_real_project.py` 是坏脚本，别信输出。
- **并发写入常态**：动手前 `git status` + 时间戳；文档只追加；提交按文件分组；写完回读校验。

## 四、当前的片（判据见同名测试）

E1/E2/E3 引擎事实｜纯文字导入 S1~S4（`test_txt_import*` + 金标）｜自定义动作弹窗 v4｜资产维度结构化｜立绘卡"一维一格"｜S1 删边=删拍｜S2 选择点（menu 住 `<章>_choices.rpy`，剧本只多一行 `call`）｜S3 控制流守门（`plan_control_write`）｜L7 版式恢复｜导入冲突预检（覆盖/改名/跳过，取消=零写）｜点不动修复 + 记忆门禁｜导出落点 + 工具栏顺序｜接线不卡｜刷新不白付钱｜卡片单拍舞台图｜右栏竖向配额（「定位」默认折叠）｜重号不级联（发号底座=整批已有+本次已发）｜derived 段要交代｜写入口探针两种 open｜GitHub star 徽标｜E1/E3+D3 拖线落点由结构决定｜**🎮 引擎试玩 + 演出专用版**（`engine_preview` / `stage_edition` / `packaging/make_stage_release.py`）｜**舞台（高级）可疑行 → 导出前确认**（`story/stage_suspect.py`，撤回自动补 `#`）｜**卡片三修**（`_stage_box` 常显 / `WheelSafeComboBox` 滚轮防误触 / compact 缩略图文字可按读）｜**二轮反馈五件套（2026-10-30）**：②⁴ 资产身份解析 + 角色优先分组 + 项目级映射（`assets/asset_identity` / `asset_mapping` / `ui_qt/mapping_dialog` / `app/picker.resolve_pick`）｜⑦ 真实舞台几何 fit/at/黄切 + 内存封顶（`atl.placement_box` / `stage_snapshot.scene_at` / `stage_render`）｜③ 自定义过场解析 + 预览演效（`story/transition_*` / `app/transition_fx`）｜⑤ UI 宽窄（默认 1360×900、最小 738、右栏 340、网格自适应）｜导出前「非法 show」门禁（`app/stage_identity_guard`）｜GitHub Star 探测误报修复（任何 HTTP 响应算网络通、点一下立刻开页）｜**自定义模块 v7（2026-10-30）**：复杂子句 `clauses`（`story/spec_clauses` + 弹窗第 3 块）｜技术调用 `calls`（`story/call_expr` + `ui_qt/dialog_calls` + 内置 `renpy.transition` 可编辑列）｜复杂 `show … at … behind` 的 at 进快照（`beat_stage_parse._clause_values`）｜跨章 `scene_at`/`layer_at` 继承（`EXIT_STATE_KEYS` + continuity `JUDGED_STATE_KEYS`）｜**UI 第 6 条（2026-10-30 晚，"不要那个结构"）**：删掉「⋯ 结构」菜单、6 颗结构按钮回动作条 + `CardActionRow`（`FlowRow`）折行（窗口最小宽仍 738）｜老记忆一次性迁移 `window_state.narrow_layout` + `layout_version`（窗口 1440→1360、卡片栏 813→640:420）｜**UI 第 6 条（2026-10-30 深夜，"滚动会导致选择切换 / 打开还是这么大"）**：滚轮永不改值（全局 `WheelValueGuard`，有焦点也不放行）｜窗口大小**每次**恢复都压到默认 1200×800（`narrow_layout` 对 geometry 不看版本号）｜分栏按**比例**摆（`_scale_sizes`）+ 恢复顺序 resize→dock→切页→分栏→`singleShot(0)` 补摆｜`resolve_chapter` 不再跨项目载入章节（绝对路径必须在项目根内）。

**真缺口**：D4 Excel 前端上限｜拍级三路合并｜G1 行为级契约进 CI（`skipif(not REAL)` 在 Linux 必 skip，含全部 8 个 golden）｜G2 引擎版本适配层｜**从当前拍 warp 的近似性**（前情 `$` 变量缺，UI 已明说，未做"补前情状态"）｜引擎试玩只支持 Windows 源生 SDK 运行（不做 build 分发）。

**待用户决定**：D1 树/表归属｜D2 两套生成器收敛｜D3 rename patch 接 Qt｜D4 Stage 格级下拉｜D5 坏味道课题｜D6 pytest.ini basetemp｜D7 多引擎加深｜D8 提交策略。

## 五、P0 审计（六轮，细节见 `docs/P0审计_*.md`）

- **最高频 P0 = 两边口径不一致**（老表英文键 vs 中文表头 → `_canon_field`/`BEAT_HEADERS` 两向都要报）；**最有效探针 = "同一件事不同出口两种待遇"**。
- 载体三轴（真机 312 个 `.rpy` 全 0）：行边界（`core/textlines.py`，Ren'Py 只认 `\n`）｜编码（`SourceStyle.apply(source=)` 严格解码抛 `SourceNotUtf8`，所有落盘点唯一末班车）｜`commands.py` 各处补 `source=`。
- Excel 是第二份源码：`story/xlsx_preserve.py`（备份 3 代 + 用户整页带走 + 自加列按 **LineId** 回填）；`removed_count` 必须真算。xlsx 存不下控制字符会让**整份表**导不出 → `excel/cells.py::xml_safe` 剥掉留痕。
- 新功能先跑 `tools/_p0_audit_round6_stress.py`（19 条难缠工程）；真机 `sr_general` 太干净。`test_scan_golden` 红先看"少了 []"。

## 六、协作与定位

- 单人开发：AI 写代码，用户设计/测试/验收。多讲"为什么这么设计、否决了哪个备选"；用户写代码时别抢答；**接受判据校准**。
- 说人话（编剧也看得懂）、每个观点要"为什么"、给指标/表格/P0-P1 标签。
- 定位：**已有项目的外科手术刀 + 资产治理层 + 跨引擎逃生舱**；三条护栏 = 不改引擎 / 不改用户字节 / 能带走。
- 图管"章与章怎么串"，卡片管"这章怎么演"，桥只有一行 `call`。menu 的家 = 编排层文件，**永不写进剧本**。
- 引擎对坏写法沉默（落空/章末 jump/menu 嵌 if，lint 全过）→ 发现是工具的活（`story/exits.py`，七档出口）。**"不能写剧本" ≠ "不给你连线"**：算不出就画草图 + 状态栏说清，静默是最坏反馈。

## 七、发布包

- 清单（`packaging/make_release.py::REQUIRED`，缺一样报错）：两个 exe + `_internal\`、`剧本空表格.xlsx`、`demo\`（带真 PNG，`demo/make_art.py`）、`快速上手.md`、`新手快速操作手册.md`、`manual_img\`、`LICENSE`。判据 `test_release_manifest.py`。
- 顺序：fasttest 全绿 → `pyinstaller packaging/storygraph.spec --noconfirm` → `StoryGraph-CLI.exe selfcheck` → `make_release.py --zip`。⚠ PowerShell `2>&1 | Select-Object` 会把 stderr 变 NativeCommandError 假 exit 1，用 `*> .pip-tmp/pyi.log`。
- 手册截图 `tools/_shot_manual.py`：`offscreen` + `STORYGRAPH_NO_SESSION=1`；**不许 `win.close()`**（未导出改动弹模态框永卡住，结尾 `os._exit(0)`）；结构页用 `zoom_reset→zoom_by(2.0)→center_on_node`，别 `fit_to_content()`。
- 真机工程会自己长大（`sr_general` 55→96 章）：金标红先跑 `tools/golden_baseline.py --check`，确认是项目变了再 `--save`。
- **演出专用版**（`packaging/make_stage_release.py`，2026-10-29）：`--dist dist/release --sdk D:\software\renpy-8.4.1-sdk --out dist/stage --zip`。包内 = 标准版全部 + `renpy_sdk/`（完整 SDK 去 `vscode/`+`tmp/`，约 330 MB）+ `项目预设/`（真实 rules + 43 章布局）+ `演出专用版.txt` + `演出专用说明.md`；13 项 REQUIRED、3 项 FORBIDDEN、zip ≤ 4 GiB。**先跑 `make_release.py --stage-only` 铺出带 extras 的 `dist/release`，再拿它当 `--dist`**（`dist/StoryGraph` 只有两个 exe）。
- 验收：`StoryGraph.exe --selfcheck`（演出专用版会多一条"引擎 + 预设"，含随包项目探测）；解压后把 `项目/` 放 exe 旁边再启动，应自动打开项目并补 `storygraph.rules.json` / `.storygraph/layout.json`（判据：这两个文件出现）。
