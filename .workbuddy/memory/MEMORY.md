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
- **pytest 必须带全新 `--basetemp`**（裸 `pytest` 必炸 ~1589 ERROR，D6 未决）；**两个 pytest 不能同时跑**（同名目录互删）。`.pip-tmp/` 会累积到 GB 级、别用 `du/find` 扫。
- **全量耗时由 4 条真机金标主导（~730s）**，连跑方差 ±40% —— 别当回归，先看 `--durations`。
- **无 `QApplication` 就建 Qt 控件 = 进程 abort**（无 traceback）。Qt 测试类要 `qapp` autouse。
- **面板少一个转发方法** = `AttributeError` → 模态框 → 测试挂死。守卫 `test_every_panel_method_the_window_calls_really_exists`。
- **构造期 `_refresh_preview` 早于控件创建** → `getattr` 守卫 + 建好后重刷。
- **QSS 吞 `QTreeView` 展开三角** → 自己画 `▾/▸` + `setRootIsDecorated(False)`。
- **判 dock 开没开用 `isHidden()`**；`saveState()` 要求工具栏有 `objectName`。
- **中文源码别过 shell 文本管道**（`Get-Content|Set-Content` 会乱码），只用 edit/write。
- **模态打桩要打全**（question/warning/information 都是）；`QMenu.exec` 打桩不可靠 → 菜单建成纯函数再测"挂了哪些动作"。
- `QLabel`/`QCheckBox` 不渲染 Markdown；`Statement.source` 是 `(路径, 行号)` 元组。
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
· 难缠工程矩阵 `tools/_p0_audit_round6_stress.py` **16/16**（menu 条件项 / if-elif / call screen / 重复台词 / 点号 label / 撤销逐字节还原）。
  真机 `sr_general` 太干净，**以后新功能先跑这个矩阵**。

## 五、协作与定位

- 单人开发：AI 写代码，用户设计/测试/验收。多讲"为什么这么设计、否决了哪个备选"；用户写代码时别抢答。**接受判据校准**。
- 说人话（编剧也看得懂）、每个观点要"为什么"、给指标/表格/P0-P1 标签；写作侧排斥 AI 味。
- 定位：**已有项目的外科手术刀 + 资产治理层 + 跨引擎逃生舱**；三条护栏 = 不改引擎 / 不改用户字节 / 能带走。
- 图管"章与章怎么串"，卡片管"这章怎么演"，桥只有一行 `call`。menu 的家 = 编排层文件（`_control` 或工具独占的 `_choices.rpy`），**永不写进剧本文件**。
- 引擎对坏写法沉默（落空/章末 jump/menu 嵌 if，lint 全过）→ 发现是工具的活（`story/exits.py`）。七档出口，`call` 后无 `return` 一样落空。
- **"不能写剧本" ≠ "不给你连线"**：算不出来就退回画草图 + 状态栏说清，静默是最坏的反馈（E1 事故）。
