# 项目记忆：RenPy 剧情编辑器（storygraph）· 索引页

> 这里只放**最容易踩的坑**与**指针**；易变事实（用例数/行数/性能）读代码或跑测试。
> 详情：全部契约 `docs/契约速查_storygraph.md`（31 万字符，按需 grep）｜逐日经过 `docs/开发日志.md`（13.7 万）｜路线图 `docs/路线图_长期目标_2026-10-08.md`｜历史稿 `docs/archive/`｜竞争格局 `docs/竞争格局分析_2026-10-23.md`｜短板评估 `docs/短板可行性评估_2026-10-23.md`。
> ⚠ 引用任何状态前先实测（跑 `tools/hell_*.py` / 对应 pytest），别信文档里的 ✅/❌。

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

## 二、引擎事实（最终结论，细节见契约速查 §六~§十二）

- `lint` **会**查图像/属性引用与资源存在性；**默认退 0，必须 `--error-code`**。
- `has_image()` 不是合法性判据；`parse error` → 探针也拿不到事实 → `probe_complete=false` 当**硬失败**。
- 事实文件 `<项目>/storygraph.engine_facts.json`：每条带 `source`，两源不一致记 `conflicts`（不静默挑一个）。
- **覆盖内置语句 = 完全替换**；**fork 引擎新增语句对工具完全隐形**；`test` 在 headless 不可用。
- 真机基准（sr_general）：images 317 / tags 235 / transforms 155 / labels 106。

## 三、最高频的实操坑

- **两个 registry 不是一回事**：`session.registry()`=资产表；语句表是 `StatementRegistry.for_project(root)`。混用 → Qt 槽吞异常 → "点了毫无反应"。
- **`EditorSession` 有 `__len__`** → 没载入章节时 falsy。判空用 `is not None`，**绝不用 `if self._session:`**。
- **默认值兜底不要用 `or`**：`int(x or -1)` 把第 0 拍当"没载入"。
- **全量测试只用 `python tools/fasttest/run.py`**（并行 + 每轮独立 basetemp + 运行锁；详见 `tools/fasttest/README.md`）。裸 `pytest` 串行 ~20 分钟，且两个并发会互删 `.pip-tmp/pytest`（D6）；fasttest 自带锁，但**裸 pytest 不受锁保护**。`.pip-tmp/` 会累积到 GB 级、别用 `du/find` 扫；fasttest 会自动清 >7 天旧目录（`--clean-temp` 清 >24h）。现在每 15s 心跳，连续 300s 无输出会退出 **124** 并打印正在跑的 nodeid；旧 tmp 先 rename 到 `.fasttest-trash/` 后台删，启动不再被几分钟的删除卡住；`--retry-failed` 只复跑上一轮纯全量失败（无失败直接退出，不会偷偷跑全量）。
- **全量耗时由少数真机金标主导（scan/validate/patch/describe ~730s）**，连跑方差 ±40% —— 别当回归，先看 `--durations`。fasttest 会把重文件排到队首，且 `test_disk_hygiene` 固定第一个跑（它递归扫 `.pip-tmp`，晚跑会从秒级变几分钟）。
- **pytest-xdist 默认 `--loadscope-reorder`**：按"文件里有多少条用例"重排 work unit，会**覆盖**上面的重文件优先，把只有 2 条的 scan/hydrate/patch 金标踢到 99%。fasttest 已显式传 `--no-loadscope-reorder`；手写 `pytest -n` 时别漏（2026-10-26 实测 7m14s → 4m36s）。
- **无 `QApplication` 就建 Qt 控件 = 进程 abort**（无 traceback）。Qt 测试类要 `qapp` autouse。
- **面板少一个转发方法** = `AttributeError` → 模态框 → 测试挂死。守卫 `test_every_panel_method_the_window_calls_really_exists`。
- **构造期 `_refresh_preview` 早于控件创建** → `getattr` 守卫 + 建好后重刷。
- **QSS 吞 `QTreeView` 展开三角** → 自己画 `▾/▸` + `setRootIsDecorated(False)`。
- **判 dock 开没开用 `isHidden()`**；`saveState()` 要求工具栏有 `objectName`。
- **中文源码别过 shell 文本管道**（`Get-Content|Set-Content` 会乱码），只用 edit/write。
- **模态打桩要打全**（question/warning/information 都是）；`QMenu.exec` 打桩不可靠 → 菜单建成纯函数再测"挂了哪些动作"。
- **删卡三选一 / 块头级联确认是真 `QMessageBox.exec()`**（2026-10-26）：只 patch `QMessageBox.question` 不够，`delete_card()` 会卡在 `_ask_delete_mode` 的 `box.exec()`，xdist 其余 worker 全空闲、整体 0 CPU，看起来像"卡死 20 分钟"。测 `delete_card` 必须同时 patch `_ask_delete_mode`（返回 `'whole'` / `''`）和 `QMessageBox.question`；fasttest 的 124 看门狗会点名 nodeid。
- `QLabel`/`QCheckBox` 不渲染 Markdown；`Statement.source` 是 `(路径, 行号)` 元组。
- **Qt 槽里异常只打 stderr = 界面上"点了没反应"**（2026-10-26）：`QMessageBox.addButton(文本, 角色)`
  的第二参数是 **ButtonRole**；传 `QMessageBox.Cancel/Ok/Yes`（StandardButton）当场 TypeError。
  真机事故：无源节点的四个修复入口一起静默失效。菜单/右栏按钮动作一律过
  `window_nodes._menu_action` 安全壳；**有模态框的动作必须有"真建框"判据**
  （只打桩函数会漏掉"框根本建不出来"这一类）。静态守卫 `test_ui_reaction_contract.py`。
- **全局开关（记忆/自动保存）的判据要判在唯一写入口**，别放调用方（2026-10-26）：
  `remember_graph_files` 漏查 → 跑一轮 UI 测试往用户真实 `session.json` 写了 **849 个**
  tmp 项目。跑 UI 探针 / 截图脚本先 `STORYGRAPH_NO_SESSION=1`；`session_store.save()`
  现在自己判 `enabled()`，写真实表时会自动剪 `.pip-tmp/` 等临时项目。
- `tools/audit_real_project.py` 是坏脚本，别信输出。
- **并发写入常态**：动手前 `git status` + 时间戳；文档只追加；提交按文件分组；写完**回读校验**。

## 四、最近的片（状态与判据）

| 片 | 一句话 | 判据 |
|---|---|---|
| E1/E2/E3 引擎事实 | 一次运行产 facts（候选由引擎枚举）+ 漂移三档退出码 | `test_engine_facts.py` / `test_engine_choices.py` |
| 纯文字导入 S1~S4 | `.txt`/`.docx` → `.rpy` 纯函数，真机 5 章逐字节一致 | `test_txt_import*.py` + 金标 |
| 自定义动作弹窗 v4 | `param_editor.py` + `column_editor.py` + `flow.py`，三页签单滚动区 | `test_custom_statement_entry.py` |
| 资产维度结构化 | 图片表 `Group`/`Value`；`Attributes` 降兼容列 | `test_asset_dimensions.py` |
| 立绘卡"一维一格" | 服装/表情各占一格，点一下只换那一维 | `test_sprite_slots.py` |
| S1 删边=删拍 | `edge_delete_plan`(纯)/执行分开；草图边只删图不动剧本 | `test_edge_delete_source.py` |
| S2 选择点 | menu 住工具独占的 `<章>_choices.rpy`；剧本只多一行 `call` | `test_choice_point.py` |
| S3 控制流守门 | `plan_control_write` 拦"只导 2 章 → 抹掉第 3 章 `_control`" | `test_control_write.py` |
| L7 版式恢复 | 记住图由哪些文件组成 → 图空才重建 → 贴版式 | `test_graph_restore.py` |
| 导入冲突预检 | 撞 label / 盖手写先在弹窗里"覆盖/改名/跳过"（取消=零写），另有残留清理入口 | `test_import_review.py` / `test_dialog_import_review.py` / `test_import_cleanup.py` / `test_dialog_cleanup.py` |
| 点不动修复 + 记忆门禁 | 无源节点修复入口（QMessageBox 角色装错）静默失效；记忆写入口收门禁 + 剪测试污染 | `test_ui_reaction_contract.py` / `test_session_memory.py` |
| E1/E3+D3 | 拖线落点由结构决定；过滤只改 visible 不删边 | `test_wire_landing.py` / `test_edge_beat_badge.py` |

**真缺口**：D4 Excel 前端上限｜拍级三路合并｜M5 多引擎深度（运行时组件未做）｜G1 行为级契约进 CI（21 个 `skipif(not REAL)` 在 Linux 必 skip，含全部 8 个 golden）｜G2 引擎版本适配层（支持矩阵 + 探针降级闸门）｜许可证 Proprietary 无 LICENSE。

**2026-10-25 不安全审计（三轮闭环，详见 `docs/P0审计_不安全因素_2026-10-25.md` + `_第二轮_` + `_第三轮_2026-10-25.md` + 当日日志）**：
第一轮五条 + P0-3/P1-4/P1-5/P1-6/P1-A/P1-C/P1-5b **全部已修且探针复测通过**（P0-3 写前复读拒写；P1-4 rename_scope 只改引用位；P1-5 `apply(text, source=)` difflib 逐行还原；P1-6 `_walk_covers`/`_unjudged_paths`；P1-A 严格解码拒写带坐标；P1-C 引号说话人警告网；P1-5b 补传 source=）。
⚠ 审计期并行提交：`8ef4e1a`→`90ba338`→`d4a60d0`（含把我的探针提交进 git + 重录 sync/main_window golden）。
⚠ **scan golden 仍红 = 真机内容漂移非回归**（lint_proj 10-22 21:58 才建，golden 录于当天 09:36）→ 待用户/并行工作者重录，别擅改。
⚠ 探针期望值也会错（本轮三次：+2 标记位移/引号/BOM）——判据要「构造期望全文」别用同索引 diff；探针拷真项目必须 rmtree 收摊（disk_hygiene 守卫）。
⚠ 两个 pytest 并发跑会互清 `.pip-tmp/pytest` → 大规模 ERROR 是干扰非回归（踩过两次）。

**待用户决定**：D1 树/表归属｜D2 两套生成器收敛｜D3 rename patch 接 Qt｜D4 Stage 格级下拉｜D5 坏味道课题｜D6 pytest.ini basetemp｜D7 多引擎加深｜D8 提交策略。

**2026-10-25 第五轮（载体三轴，见 `docs/P0审计_不安全因素_第五轮_2026-10-25.md`）**：
四条 P0 已修 + 判据 `tests/test_p0_round5_carrier.py`（32 条）+ 探针 `tools/_p0_audit_round5_{primitive,paths,stale}.py`。
① **P0-A 行边界**：`str.splitlines()` 认 `U+2028/U+2029/NEL/VT/FF/\x1c-\x1e` 为换行（Ren'Py 只认 `\n`）→
真 `\n` 被顶掉或软回车变真换行。新增 `core/textlines.py`（`split_lines/split_eols/join_lines`），
**改写/切行一律用它**；`renpy_parser` 也换了（否则行号与改写点差开 → 删错行）。
② **P0-B 编码**：非 UTF-8 文件「读回来再写回去」把坏字节换成 `EF BF BD`。修法**不是**在每个调用点预检
（第三轮那样修必漏），而是 `SourceStyle.apply(source=)` 严格解码抛 `SourceNotUtf8` —— 它是所有落盘点的唯一末班车。
③ **P0-C**：`commands.py` 9 处 `apply` 补 `source=`（混合换行撤销不再整份归一化）。
④ **P0-D**：`patcher.removal_note()` 唯一实现（表过期删行三个出口同一句话），此前编辑器 `_patch_note` 一字不提。
⚠ 真机 312 个 `.rpy` 三轴全 0 —— 这四条是**地雷不是事故**，触发源是作者粘台词 / 拷来的 ANSI 文件。
⚠ 教训：**"同一件事不同出口两种待遇"是最有效的找 P0 的探针**（本轮两条由此挖出）；探针判据要写"目标行为"，
不是"腐蚀存在"。

**2026-10-25 第六轮（Excel 是第二份源码，见 `docs/P0审计_第六轮_Excel与难缠工程_2026-10-25.md`）**：
· **P0-E**：`export_beats_to_excel` 用 `Workbook()` 整份重建 → 用户自加的页/列/批注/底色/冻结窗格全没、零备份。
  新 `story/xlsx_preserve.py`（备份留 3 代 + 用户整页带走 + Beats 自加列按 **LineId** 回填 + 人标格子按 **LineId+表头**）。
  **凡是"整份新建再 save 覆盖"的写入点都要按这条审** —— xlsx 也是用户资产。
· **P1-H 最值钱**：`removed_count` 恒少算"拍内删行"（取的是从不累加的局部变量）→ 回执靠预览兜底才说对，
  而任何只看 count 的判据（`staleness_note`）**静默失效**。教训：**回执里的每个数必须真算**。
· 点号 label（`main.route`）导出 100% 失败（`patcher._LABEL_LINE_RE` 的 `\w` 不含点）→ 已修；
  同一正则另有三处：`statement_probe` 早就对，`choice_point` 前缀校验（已统一），`flow._LABEL_RE`（已支持）。
· 隐藏表新增「来源指纹」（sha1 前 16 位）：只用来**解释**"表比源码旧"，**不 block**（工具自己写回后指纹立刻变旧，
  真拦会逼用户闭眼点"仍要覆盖"，更危险）。
· 难缠工程矩阵 `tools/_p0_audit_round6_stress.py` **19/19**（menu 条件项 / if-elif / call screen / 重复台词 / 点号 label /
  撤销逐字节还原 / **连续两次原地导出**）。
  真机 `sr_general` 太干净，**以后新功能先跑这个矩阵**。
· **P0-F 资源表同一刀**：`write_dict_to_excel` 也是"整份新建再 save"（docstring 自己写着"2828 行会掉到 349 行"）。
  改为**直接保住**（用户否掉了隔离备份："隔离解决不了根本问题"）：行按 `编号`，认不出就按 `真实文件`；
  孤儿行只在"记了文件且文件真不在"时删；人的列/页/批注带走 + 回执。判据 `test_p0_round6_asset_preserve.py`（9 条）。
· **最容易复发的坑 = 两边口径不一致**（本轮三次全中）：老表表头是**英文键**（`ID/Name/Color`），
  拿中文标签比 → 同一字段两列 → 读回取空列。**凡是"按表头名比对/映射"的地方都要先规范键**
  （`_canon_field` / `BEAT_HEADERS` 两个方向都要报）。
· `test_scan_golden` 已按用户确认重录（`模拟操作/lint_proj` 是刻意保留的真机残留）。
  **红了先看"少了[]"**：只有新增 = 真机加东西；有删 = 可能真回归。

**2026-10-26 menu_brief 接到控制流条**（用户："menu……没显示内容是什么、又跳去哪儿"）：
· `story/flow.menu_brief`/`menu_brief_text` 接到 `window_card._flow_target_brief` → `main_window._refresh_flow_text`；
  跳转/调用目标是**有名 menu** 时，条上整段摆出选项各自去哪（`_flow_detail`）。判据 `tests/test_menu_brief.py`（16 条）。
· **menu_brief 本身修了 4 个真 bug**（tester 探针 `tools/_multiagent_probe_menu_brief.py` 抓的）：
  ① 嵌套 menu 串台（外层选项吞内层 jump、查内层报外层选项）→ 块号用 **N 反查**（本 label 第几个 menu），
     不再"扫到第一个选项就当块号 / split('>') 取首段"；② `startswith('screen')` 误杀 `jump screen_map`/`call screenplay`
     → 复用 `parse_flow_line`（只认紧跟关键字 `call screen`）；③ `call ch02(x,y) from _f` 把 args/from 拼进 target
     → 同走 `parse_flow_line`（target/args/from 分家）；④ 孤儿选项（删拍残留、别家块号）被吞 → 同 ①。
  **教训**：跳转口径**必须复用 `parse_flow_line`**，别另写 `raw.split()` 猜 —— 两边口径不一致是本仓库最高频 P0。
· **越界未修（非 menu_brief，如实记）**：§1.12 `menu main.route:` 解析器 `_RE_MENU_NAME` 只吃 `identifier/.identifier`，
  点号 menu 名整块掉 opaque → `menu_brief` 按"认不出就少显示"返 `{}`（要改解析器 + Ren'Py 语义判断，不猜）；
  §2.8 `parse_flow` 用 `str.splitlines()` 数行 → U+2028 软回车被当换行（P0-A：Ren'Py 只认 `\n`），
  独立于 menu_brief、未动。二者都待排期。
· ⚠ `test_ui_qt` 本轮机器被并行 agent 占满（121/195 后停住、裸 pytest 又撞 D6 `.pip-tmp` 批量删守卫）→ 未跑完；
  定向跑 `test_menu_brief`16 / `test_flow_target_brief`10 / `test_flow_card`40 / `test_package_layering`5 全绿。

**2026-10-26 晚 导入冲突预检（"先问后写"）**（用户："我一个个删 .bak 和 blocked
老狼狈了……不要擅自不落盘/擅自全量替换……让我能对着 txt 换 label"）：
· 新 `authoring/import_review.py`（纯读预检 + 决策落地）、`authoring/import_cleanup.py`
  （残留扫描/删除）、`ui_qt/dialog_import_review.py`（左表三选一 + 右栏新旧对比）、
  `ui_qt/dialog_cleanup.py`；接线在 `txt_pipeline.run(resolve=)` /
  `session.import_plain_text(resolve=)` / `window_graph_ops`。
· **不给 `resolve` 时行为与从前一字不改**（CLI / 单测 / 老判据靠这条）；取消 =
  **零写盘零副本**（`needs_review=True`）。
· 三种结局的落点：**覆盖 = 写进占位的那份 .rpy**（不是另造撞名产物）；**改名 =
  写到旧章节旁边**（`game/chapter/` 跟着走）；跳过 = 什么都不产。
· **预检与门禁故意不共用结论**：预检只判"谁占着 label / 目标是否手写"，真正
  拦不拦还是 `write_generation`（人选的覆盖若仍不合法，门禁照旧拦）。
· 幂等：产物与磁盘逐字节一致 → **不重写**（不产 `.bak`、不刷 mtime）。
· 清理红线：只认 `.rpy.bak` / `.rpy.bak-<数字>` / `.blocked.rpy` / `.bak.rpy`；
  **`.bak.xlsx` 不列不删**；`.rpy.bak` 默认**不勾**（可能是手写原件唯一旧版本）。
· 对比展示：CRLF 旧文件要在**展示层** `rstrip('\r')`，否则每行都标"改了"。
· 真机探针（副本上）：43 份 txt 对 `sr_general` → ch1（手写）默认跳过、ch2~ch4
  默认覆盖；40 份"全部覆盖"→ 0 blocked、0 新增 `game/` 根产物、40 份 `.bak`。

## 五、协作与定位

- 单人开发：AI 写代码，用户设计/测试/验收。多讲"为什么这么设计、否决了哪个备选"；用户写代码时别抢答。**接受判据校准**。
- 说人话（编剧也看得懂）、每个观点要"为什么"、给指标/表格/P0-P1 标签；写作侧排斥 AI 味。
- 定位：**已有项目的外科手术刀 + 资产治理层 + 跨引擎逃生舱**；三条护栏 = 不改引擎 / 不改用户字节 / 能带走。
- 图管"章与章怎么串"，卡片管"这章怎么演"，桥只有一行 `call`。menu 的家 = 编排层文件（`_control` 或工具独占的 `_choices.rpy`），**永不写进剧本文件**。
- 引擎对坏写法沉默（落空/章末 jump/menu 嵌 if，lint 全过）→ 发现是工具的活（`story/exits.py`）。七档出口，`call` 后无 `return` 一样落空。
- **"不能写剧本" ≠ "不给你连线"**：算不出来就退回画草图 + 状态栏说清，静默是最坏的反馈（E1 事故）。
