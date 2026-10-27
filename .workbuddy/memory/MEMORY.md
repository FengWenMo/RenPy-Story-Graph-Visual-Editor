# 项目记忆：RenPy 剧情编辑器（storygraph）· 索引页

> 这里只放**最容易踩的坑**与**指针**；易变事实（用例数/行数/性能）读代码或跑测试。
> 详情：全部契约 `docs/契约速查_storygraph.md`（31 万字符，按需 grep）｜逐日经过 `docs/开发日志.md`（13.7 万）｜路线图 `docs/路线图_长期目标_2026-10-08.md`｜历史稿 `docs/archive/`｜竞争格局 `docs/竞争格局分析_2026-10-23.md`｜短板评估 `docs/短板可行性评估_2026-10-23.md`。
> ⚠ 引用任何状态前先实测（跑 `tools/hell_*.py` / 对应 pytest），别信文档里的 ✅/❌。
> 逐日全文：`.workbuddy/memory/2026-10-1*.md`（10-14~10-26，细节全在里面）。

## 一、十条铁律（违反任一条都会静默毁掉用户项目）

1. **`.rpy` 唯一真源；不归己的行逐字节不动**。只逐行配对替换真改过的行，绝不整份重生成。
2. **要么全对、要么完全不动**。任一行配不上 → 整份放弃 + 报错，不产出"半对"。
3. **认不出就原样抄**。未知块走"原样保留"，不补 `$`、不包 `python:`、不改缩进。
4. **猜一个名字比说"不知道"坏得多**。只认字面量，不模糊匹配。
5. **绝不静默全量覆盖**。落盘过门禁；`HANDWRITTEN_TARGET` 拒写；改用户 `.rpy` 先出 diff，未确认零写。
6. **教它升级，不许改字节**。教会自定义语法后首次导出必须与教会前逐字节相同。
7. **不许静默丢行**（txt 导入/表格/补丁三条路）。判据要"整段相等"。
8. **一个落盘点一个登记**（`WRITERS`，守卫 `test_single_write_entry.py`）。走 `write_text_atomic`/`write_generation` 的**不该**登录。
9. **本工具不猜语义**。意思由人标注，工具只发现/展示/检索。
10. **含中文/转义的复现一律写 `.py`** —— `python -c` 经 Bash 会改坏样本（曾"复现"出不存在的 P0）。

## 二、引擎事实（细节见契约速查 §六~§十二）

- `lint` 会查图像/属性引用与资源存在性；**默认退 0，必须 `--error-code`**。
- `has_image()` 不是合法性判据；`parse error` → 探针拿不到事实 → `probe_complete=false` 当**硬失败**。
- 事实文件 `<项目>/storygraph.engine_facts.json`：每条带 `source`，两源不一致记 `conflicts`（不静默挑一个）。
- **覆盖内置语句 = 完全替换**；**fork 引擎新增语句对工具完全隐形**；`test` 在 headless 不可用。
- 真机基准（sr_general）：images 317 / tags 235 / transforms 155 / labels 106。

## 三、最高频的实操坑

- **两个 registry 不是一回事**：`session.registry()`=资产表；语句表是 `StatementRegistry.for_project(root)`。混用 → Qt 槽吞异常 → "点了毫无反应"。
- **`EditorSession` 有 `__len__`** → 没载入章节时 falsy。判空用 `is not None`，**绝不用 `if self._session:`**。默认值兜底别用 `or`（`int(x or -1)` 把第 0 拍当"没载入"）。
- **全量测试只用 `python tools/fasttest/run.py`**（并行 + 每轮独立 basetemp + 运行锁）。裸 `pytest` 串行 ~20 分钟，且两个并发会互删 `.pip-tmp/pytest`（D6）；**裸 pytest 不受锁保护**。`.pip-tmp/` 会累积到 GB 级、别用 `du/find` 扫。fasttest 自动清 >7 天旧目录（`--clean-temp` 清 >24h）；15s 心跳 + 300s 无输出退 **124** 并打印 nodeid；`--retry-failed` 只复跑上一轮纯全量失败（无失败直接退出）。
- **全量耗时由少数真机金标主导（scan/validate/patch/describe ~730s）**，连跑方差 ±40% —— 别当回归，先看 `--durations`。`test_disk_hygiene` 固定第一个跑（晚跑会从秒级变几分钟）。
- **pytest-xdist 默认 `--loadscope-reorder`** 会**覆盖**重文件优先，把只有 2 条的 scan/hydrate/patch 金标踢到 99%。fasttest 已显式传 `--no-loadscope-reorder`；手写 `pytest -n` 时别漏（实测 7m14s → 4m36s）。
- **无 `QApplication` 就建 Qt 控件 = 进程 abort**（无 traceback）。Qt 测试类要 `qapp` autouse。
- **面板少一个转发方法** = `AttributeError` → 模态框 → 测试挂死。守卫 `test_every_panel_method_the_window_calls_really_exists`。
- **构造期 `_refresh_preview` 早于控件创建** → `getattr` 守卫 + 建好后重刷。
- **QSS 吞 `QTreeView` 展开三角** → 自己画 `▾/▸` + `setRootIsDecorated(False)`。判 dock 开没开用 `isHidden()`；`saveState()` 要求工具栏有 `objectName`。
- **中文源码别过 shell 文本管道**（`Get-Content|Set-Content` 会乱码），只用 edit/write。
- **模态打桩要打全**（question/warning/information 都是）；`QMenu.exec` 打桩不可靠 → 菜单建成纯函数再测"挂了哪些动作"。
- **删卡三选一 / 块头级联确认是真 `QMessageBox.exec()`**（2026-10-26）：只 patch `QMessageBox.question` 不够，`delete_card()` 会卡在 `_ask_delete_mode` 的 `box.exec()`，xdist 其余 worker 全空闲、整体 0 CPU，看着像卡死 20 分钟。测 `delete_card` 必须同时 patch `_ask_delete_mode`（返 `'whole'` / `''`）和 `QMessageBox.question`。
- `QLabel`/`QCheckBox` 不渲染 Markdown；`Statement.source` 是 `(路径, 行号)` 元组。
- **Qt 槽里异常只打 stderr = 界面上"点了没反应"**（2026-10-26）：`QMessageBox.addButton(文本, 角色)` 第二参数是 **ButtonRole**；传 StandardButton 当场 TypeError（真机事故：无源节点四个修复入口一起静默失效）。菜单/右栏动作一律过 `window_nodes._menu_action` 安全壳；**有模态框的动作必须有"真建框"判据**。静态守卫 `test_ui_reaction_contract.py`。
- **全局开关（记忆/自动保存）的判据要判在唯一写入口**，别放调用方（2026-10-26）：`remember_graph_files` 漏查 → 跑一轮 UI 测试往用户真实 `session.json` 写了 **849 个** tmp 项目。跑 UI 探针/截图先 `STORYGRAPH_NO_SESSION=1`；`session_store.save()` 自己判 `enabled()`，写真实表时自动剪 `.pip-tmp/` 等临时项目。
- **纯文字导入前会弹"导出到哪个文件夹"**（2026-10-26，用户："不要写入 chapter 文件夹！"）：落点由人指定（`out_dir`），**不猜**子目录；覆盖旧文件仍写那份占位 .rpy。⚠ 任何程序化调用 `import_plain_text` / `import_chapter_files([*.txt])` 的测试都要打桩 `_ask_plain_text_export_dir`，否则 offscreen 下模态目录框会**挂死测试**。
- **"连线很卡"是同一个项目被整份解析 6~8 遍**（2026-10-27）：接线窗口问"编排层是哪个 / `_control` 在不在 / 谁 jump 了这章"，每个问题都读一遍全部 `.rpy`，`detect_placement` 还是整份 AST（真机 1.2 MB → 1435 ms/次）。现在**项目级扫描一律走 `story/scan_cache.py`**（按 `mtime_ns+size` / 项目指纹失效，无 TTL），并且 `find_control_file` 在**只有一个候选**时直接返回（排序只有一个元素时结论相同）。改这块别绕过缓存，也别给它加 TTL —— 判据 `test_scan_cache.py`（含"第二次零读盘""改文件必须重新解析"）。
- **"刷新已导入章节"以前每次都全量重解**（2026-10-27）：91 章的项目按一下 3.0 秒（0 改动也一样）。现在 ①会话持有一个 `IncrementalRenPyAnalyzer`（缓存跨调用复用；**只逐出磁盘上不存在的文件**、有 8 MB 上限、**本批不淘汰**）；②`import_chapters` 记 `(mtime_ns,size)`，`stale_import_files` 决定刷新重读哪几个，**0 改动连预览框都不弹**（判据 `test_import_stamps.py`；旧用例"导入完立刻刷新也弹框"已校准为"先改文件再刷新"）。
- **卡片上的"这一拍的舞台图"**（2026-10-27）：渲染必须走 `stage_render.render_frame`（与「演出预览」**同一个函数**，别另写近似画法）；**一律后台线程**（首次碰上大盘立绘 1.3 s），防抖 120 ms + 2 线程 + 门闩；"要不要重画"看 `frame_signature`（内容指纹）——**不许**用 `id(frame)`（每刷一次都是新对象），也不许在换拍时"拿上一张顶着"（卡片是单拍快照，那等于说错话）。图在**「当前舞台」标题下面、与文字摘要并排、固定 288×162**（`compact=True`；用户 2026-10-27 两次点名"缩小！再缩小，直到能看到全貌"）；它所在的 `_stage_now` **不在可收起的 `_stage_box` 里** —— 285 拍里 210 拍那几块收起时它还在，而控制流卡（`call`/`jump`）连它一起收（判据 `test_stage_thumb.py` 24 条 + `test_ui_fit_and_flow.py`）。
- **界面上"可删的独立件"**（2026-10-27，⭐ GitHub star 徽标）：一个文件 + 一处挂载。控件/网络/离线降级全在 `ui_qt/github_star.py`，**不许** import 窗口/会话代码；挂载只有 `window_nav._build_mode_bar` 里那三行 try（徽标在**模式条上、「✎ 卡片编辑」右边**，删掉 + 删文件 = 界面复原）。点击必须立刻返回（网络在后台，`click()` 实测 21 ms）；**启动时不联网**；没 token 不开死页面、连不上只说一句"连不上 GitHub"。判据 `test_github_star.py`（19 条，含"整个 UI 包只有一处 import 它"）。
- **许可证是 GPLv3**（仓库根 `LICENSE` = GPLv3 全文，2026-10-27 从 GitHub 那条线的 `1f7153b` 恢复）：
  `README.md` / `pyproject.toml` / `packaging/make_release.py` / `packaging/交付说明.md` / `tests/test_release_manifest.py`
  必须同源（判据 `test_license_is_gpl_everywhere_it_matters`）。⚠ 10-23 那份提交（`0d32628`）
  曾把 LICENSE 写成 **MIT**（与线上 GPL 冲突），已改回 —— **别再改回去**。
- `tools/audit_real_project.py` 是坏脚本，别信输出。
- **并发写入常态**：动手前 `git status` + 时间戳；文档只追加；提交按文件分组；写完**回读校验**。

## 四、当前的片（状态与判据）

| 片 | 一句话 | 判据 |
|---|---|---|
| E1/E2/E3 引擎事实 | 一次运行产 facts + 漂移三档退出码 | `test_engine_facts.py` / `test_engine_choices.py` |
| 纯文字导入 S1~S4 | `.txt`/`.docx` → `.rpy` 纯函数，真机 5 章逐字节一致 | `test_txt_import*.py` + 金标 |
| 自定义动作弹窗 v4 | `param_editor.py` + `column_editor.py` + `flow.py` | `test_custom_statement_entry.py` |
| 资产维度结构化 | 图片表 `Group`/`Value`；`Attributes` 降兼容列 | `test_asset_dimensions.py` |
| 立绘卡"一维一格" | 服装/表情各占一格 | `test_sprite_slots.py` |
| S1 删边=删拍 | `edge_delete_plan`(纯)/执行分开 | `test_edge_delete_source.py` |
| S2 选择点 | menu 住工具独占 `<章>_choices.rpy`；剧本只多一行 `call` | `test_choice_point.py` |
| S3 控制流守门 | `plan_control_write` 拦"只导 2 章 → 抹掉第 3 章 `_control`" | `test_control_write.py` |
| L7 版式恢复 | 记住图由哪些文件组成 → 图空才重建 → 贴版式 | `test_graph_restore.py` |
| 导入冲突预检 | 撞 label / 盖手写先在弹窗里"覆盖/改名/跳过"（取消=零写） | `test_import_review.py` / `test_dialog_import_review.py` / `test_import_cleanup.py` / `test_dialog_cleanup.py` |
| 点不动修复 + 记忆门禁 | QMessageBox 角色装错静默失效；记忆写入口收门禁 + 剪测试污染 | `test_ui_reaction_contract.py` / `test_session_memory.py` |
| 导出落点 + 顶部顺序 | `.rpy` 导出文件夹**由人指定**；工具栏顺序：导航→打开项目→自定义→AI→导入→导出→刷新→清理 | `test_toolbar_export_entry.py` / `test_txt_pipeline.py` |
| 接线不卡 | 项目级扫描进 `scan_cache`（内容驱动失效）+ 单候选不排序 + 画布每块只 refresh 一次 | `test_scan_cache.py` / `test_graph_canvas.py::TestRebuildIsNotWasteful` |
| 刷新不白付钱 | 分析器缓存跨调用复用 + 刷新只重读 `(mtime,size)` 变过的文件（0 改动 = 4 ms） | `test_import_stamps.py` |
| 卡片单拍舞台图 | `app.stage.build_frame_at` + `ui_qt.stage_thumb`（后台渲染 / 内容指纹 / 防抖 / LRU）；**在「当前舞台」标题下面、与摘要并排、固定 288×162**（`compact=True`）；`_stage_now` 永远可见（舞台块收起时也在） | `test_stage_thumb.py` / `test_stage_preview.py::TestBuildFrameAt` |
| 右栏竖向配额 | 「定位」默认折叠（chip，点开才展开，`PropertyStrip` 一直同步）；卡片页一屏装得下 | `test_ui_nav_layout.py::TestWindowWiring` |
| 重号不级联 | `unique_ids` 的发号底座 = **整批已有 ID + 本次已发**，不是"已经扫过的"；输入里唯一的 ID 一个都不许换；`[0001,0001,0002] → [0001,0003,0002]` | `test_line_identity_contract.py` |
| derived 段要交代 | 说"采到了"就必须有 `derived` 三个键；没算出来写 `gaps: derived.<名字>`；`DERIVED_KEYS` 只列采集器真写的键（`group_conflicts` 是查询，已删） | `test_engine_facts.py::TestDerivedSection` |
| 写入口探针两种 open | `open(p, mode='w')` 也算落盘；`fileobj=`/`file=`（写流）不算 | `test_single_write_entry.py` |
| GitHub star 徽标 | 独立件 `ui_qt/github_star.py`（**模式条上、「✎ 卡片编辑」右边**；一处挂载 `window_nav`；后台点击；离线只说一句） | `test_github_star.py` |
| E1/E3+D3 | 拖线落点由结构决定；过滤只改 visible 不删边 | `test_wire_landing.py` / `test_edge_beat_badge.py` |

**真缺口**：D4 Excel 前端上限｜拍级三路合并｜M5 多引擎深度（运行时组件未做）｜G1 行为级契约进 CI（21 个 `skipif(not REAL)` 在 Linux 必 skip，含全部 8 个 golden）｜G2 引擎版本适配层（支持矩阵 + 探针降级闸门）。

**待用户决定**：D1 树/表归属｜D2 两套生成器收敛｜D3 rename patch 接 Qt｜D4 Stage 格级下拉｜D5 坏味道课题｜D6 pytest.ini basetemp｜D7 多引擎加深｜D8 提交策略。

## 五、六轮 P0 审计结论（细节见 `docs/P0审计_不安全因素_*_2026-10-25.md` + 当日日志）

- 已修并探针复测通过：P0-3 写前复读拒写｜P1-4 rename_scope 只改引用位｜P1-5 `apply(text, source=)` difflib 逐行还原｜P1-6 `_walk_covers`/`_unjudged_paths`｜P1-A 严格解码拒写带坐标｜P1-C 引号说话人警告网。
- **第五轮载体三轴**：P0-A 行边界（`str.splitlines()` 认 U+2028 等，Ren'Py 只认 `\n` → 新增 `core/textlines.py`，改写/切行一律用它）｜P0-B 编码（非 UTF-8 写回变 U+FFFD → `SourceStyle.apply(source=)` 严格解码抛 `SourceNotUtf8`，它是所有落盘点的唯一末班车）｜P0-C `commands.py` 9 处补 `source=`｜P0-D `patcher.removal_note()` 唯一实现。真机 312 个 `.rpy` 三轴全 0 —— **地雷不是事故**。
- **第六轮 Excel 是第二份源码**：P0-E `export_beats_to_excel` 整份重建 → 新 `story/xlsx_preserve.py`（备份 3 代 + 用户整页带走 + 自加列按 **LineId** 回填）｜P0-F `write_dict_to_excel` 同样问题 → 直接保住（行按 `编号`，认不出按 `真实文件`）｜P1-H `removed_count` 少算"拍内删行" → **回执里的每个数必须真算**｜点号 label 导出 100% 失败（`_LABEL_LINE_RE` 的 `\w` 不含点）已修。
- **教训**：① **"同一件事不同出口两种待遇"是最有效的找 P0 探针**；② **两边口径不一致是最高频 P0**（老表英文键 vs 中文表头 → `_canon_field`/`BEAT_HEADERS` 两个方向都要报）；③ 探针判据要写"目标行为"别写"腐蚀存在"；④ 判据要「构造期望全文」别用同索引 diff。
- **新功能先跑 `tools/_p0_audit_round6_stress.py`（19/19 难缠工程矩阵）**，真机 `sr_general` 太干净。
- ⚠ `test_scan_golden` 红先看"少了[]"：只有新增 = 真机加东西；有删 = 可能真回归。

## 六、2026-10-26 两片

**menu_brief 接到控制流条**：`story/flow.menu_brief` → `window_card._flow_target_brief` → `main_window._refresh_flow_text`；跳转目标是**有名 menu** 时整段摆出选项各自去哪。`menu_brief` 修了 4 个真 bug（嵌套 menu 串台 / `startswith('screen')` 误杀 `jump screen_map` / `call ch02(x,y) from _f` args 拼进 target / 孤儿选项被吞）—— **跳转口径必须复用 `parse_flow_line`，别另写 `raw.split()` 猜**。判据 `tests/test_menu_brief.py`（16 条）。未修：§1.12 `_RE_MENU_NAME` 不吃点号 menu 名；§2.8 `parse_flow` 用 `str.splitlines()` 数行（P0-A 同源，待排期）。

**导入冲突预检"先问后写"**：新 `authoring/import_review.py`（纯读预检 + 决策落地）、`authoring/import_cleanup.py`、`ui_qt/dialog_import_review.py`、`ui_qt/dialog_cleanup.py`；接线在 `txt_pipeline.run(resolve=)` / `session.import_plain_text(resolve=)` / `window_graph_ops`。**不给 `resolve` 时行为与从前一字不改**；取消 = 零写盘零副本。三种结局：覆盖 = 写进占位的那份 `.rpy`；改名 = 写到旧章节旁边；跳过 = 什么都不产。**预检与门禁故意不共用结论**（预检只判"谁占着 label"，拦不拦还是 `write_generation`）。幂等：产物与磁盘逐字节一致 → 不重写（不产 `.bak`、不刷 mtime）。清理红线：只认 `.rpy.bak`/`.rpy.bak-<数字>`/`.blocked.rpy`/`.bak.rpy`；**`.bak.xlsx` 不列不删**；`.rpy.bak` 默认不勾。对比展示：CRLF 旧文件要在展示层 `rstrip('\r')`。

## 七、协作与定位

- 单人开发：AI 写代码，用户设计/测试/验收。多讲"为什么这么设计、否决了哪个备选"；用户写代码时别抢答。**接受判据校准**。
- 说人话（编剧也看得懂）、每个观点要"为什么"、给指标/表格/P0-P1 标签；写作侧排斥 AI 味。
- 定位：**已有项目的外科手术刀 + 资产治理层 + 跨引擎逃生舱**；三条护栏 = 不改引擎 / 不改用户字节 / 能带走。
- 图管"章与章怎么串"，卡片管"这章怎么演"，桥只有一行 `call`。menu 的家 = 编排层文件（`_control` 或工具独占的 `_choices.rpy`），**永不写进剧本文件**。
- 引擎对坏写法沉默（落空/章末 jump/menu 嵌 if，lint 全过）→ 发现是工具的活（`story/exits.py`）。七档出口，`call` 后无 `return` 一样落空。
- **"不能写剧本" ≠ "不给你连线"**：算不出来就退回画草图 + 状态栏说清，静默是最坏的反馈（E1 事故）。
