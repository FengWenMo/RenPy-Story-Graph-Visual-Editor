# 项目记忆：RenPy 剧情编辑器（storygraph）· 索引页

> **怎么用**：易变事实（用例数/行数/性能）**不写这里** → 读代码或跑测试。
> 这里只放：**状态速览** + **最关键的铁律** + **指针**。
> **全部契约（A–N + 各专项）在 `docs/契约速查_storygraph.md`** —— 引用前先读那份。
> 长期目标/里程碑：`docs/路线图_长期目标_2026-10-08.md`。说人话的设计稿：`docs/设计_让工具认识自定义写法_说人话版_2026-10-10.md`。
> **角度评判（反推 vs 定义驱动 → 结论：主干=定义驱动 / 上游=契约驱动 / 护栏=扫描）：`docs/设计_两种角度评判与第三角度_2026-10-10.md`**。
> ⭐ **该结论已被实测修正 → 见 `docs/设计_契约前提的复核与修正_2026-10-10.md`**（四级阶梯 + Ren'Py 自带属性注册表 API）。
> ⭐⭐ **再修正 → `docs/设计_引擎行为探针_获取与验证两个轴_2026-10-10.md`**（实测：**三档 oracle 分工**）；**`has_image()` 不是合法性判据（对不存在的属性也返回 True）**。
> ⭐⭐⭐ **最新（真机实测，推翻上一条的部分结论）→ `docs/设计_真机四缺口实测与四个激进想法_2026-10-10.md`**：
> **`lint` 也会校验「图像/属性引用」与「资源存在性」**（上一轮说它不查是**错的**，被 parse error 掩盖）；
> **`lint` 默认退 0，必须加 `--error-code`**；探针真机跑 **7 秒**；`--json-dump` 给 **155 transform** 但 `location.label` 恒 0。
> ⭐⭐⭐⭐ **地狱测试（17 用例）+ 终极方案定稿 → `docs/终极方案_引擎事实与三层分工_2026-10-10.md`**（证据：`docs/设计_地狱测试_魔改写法对抗_2026-10-10.md`；运行器 `tools/hell_engine_oracle.py`）。
> 四个新规则：**N1 扫描只说"我认出了 N 个"**；**N2 `register_statement(<内置名>)` 要报警（覆盖真的生效且默认无信号）**；**N3 同组属性冲突 lint 不查、须工具自查**；**N4 程序自己的 `lint=` 回调输出会进 lint 报告，要收进来**。
> **坑 4（新）**：**parse error 会让探针也拿不到事实** → `probe_complete=false` 必须当**硬失败**，绝不当"无漂移"。
> **E1 已落地**：事实文件 `<项目>/storygraph.engine_facts.json`；**每条事实带 `source`**，两来源不一致记 `conflicts`（不静默挑一个）；
> `is_collectable()` 是硬闸门；探针由工具生成、**注入→跑→连 `.rpyc` 一起删**；`errors.txt` 是**挪开→还原**（不是删）。
> 真机：`images 317 / tags 235 / transforms 155 / labels 106`；`parse_clean=True`。
> **第二轮（D1–D7）补完**：**覆盖内置语句 = 完全替换**（`pause` 标准写法直接报错；**项目能覆盖 `show`**；`name=""` 能整体替换 say）；
> **fork 引擎新增语句对工具完全隐形**（改解析器分派加 `weather6` + 项目零 CDS → 扫描器/lint/探针/json-dump 四路无信号，唯一线索是用法本身）；
> 跨文件同 tag **后者整体覆盖**（`layeredimage` 报错、普通 `image` 静默）；**孤儿 `.rpyc` 不被自动清理**；
> **`test` 在 headless(dummy) 下不可用（exit 143）→ 行为级验证不进 CI 默认路径**。规则扩到 **N1–N8**；"找程序"由两种改为**三种**。
> 细节与复现见 `.workbuddy/memory/2026-10-*.md`（按日滚动）。
> ⚠ **引用状态前先实测**（跑 `tools/hell_*.py` / 对应 pytest），别信任何文档里的 ✅/❌。
> ⚠⚠ **并发事故（2026-10-10）**：路线图 §七写完提交后在提交版里消失（并发会话整份重写过）。
> 对策：写完立刻**回读校验** → **单独提交**那个文件 → **提交后回验**；跨会话改同批文档时**追加到末尾**。

---

## 一、十条铁律（违反任何一条都会静默毁掉用户项目）

1. **`.rpy` 唯一真源；不归己的行逐字节不动**。工具从不"重新生成整份文件"，只逐行配对替换真改过的行。（A1/R2）
2. **要么全对、要么完全不动**。任一行配不上 → 整份放弃 + 报错，绝不产出"半对"的产物。（B2）
3. **认不出就原样抄**。未知块走"原样保留"通道，**不补 `$`、不包 `python:`、不改缩进** —— 凭推断补东西 = 改剧情。（K1）
4. **猜一个名字比说"不知道"坏得多**。只认字面量；认不出就报出来，绝不模糊匹配。（L2/M4）
5. **绝不对用户文件静默全量覆盖**。落盘一律过门禁；`HANDWRITTEN_TARGET` 拒写；改用户 `.rpy` 的操作**先出 diff 让你确认，未确认零写**。（B1/M2）
6. **教它升级，不许改字节**。从"不认识"教成"认识"后，第一次导出的产物必须与教之前**逐字节相同**（唯一差别是少一条告警）。（M2）
7. **不许静默丢行**（txt 导入/表格/补丁三条路都算）。判据要"整段相等"，"行在产物里"太松。（K6/T1）
8. **一个落盘点一个登记**。全包所有写盘必须在 `WRITERS` 登记表里说清写什么；新增落盘点直接变红。（守卫 `test_single_write_entry.py`）
9. **本工具不猜语义**。"这句话是什么意思"永远由人标注；工具只做发现、展示、检索。（M 不变量）
10. **含中文/转义的复现一律写 `.py`** —— `python -c` 经 Bash 传参会改坏样本（曾"复现"出不存在的 P0）。（H4）

## 二、状态速览（2026-10-10）

**最近 7 个工作片（都已完成并提交）**
| 片 | 内容 | 判据 |
|---|---|---|
| ⭐ **E1 引擎事实采集** | **一次运行**（`lint --error-code` + `--json-dump` + 注入探针）产出 `engine_facts.json`；纯层 `core/engine_facts.py` + 采集器 `pipeline/engine_collect.py` + CLI `engine-facts` | `tests/test_engine_facts.py` **42 条**；**真机 15/15 验收**（12 秒） |
| 纯文字导入 S1~S4 | `.txt`/`.docx` → `.rpy`（纯函数，真机 5 章产物与 `render_beats_to_rpy` **逐字节一致**） | `test_txt_import*.py` 60 条 + 金标 |
| 角色**别名列** | `characters.xlsx` 新增「别名」（多值 `MOM\|母亲`）；四层匹配 L0变量→L1归一→L2显示名→L3别名→L4报出来 | `test_character_aliases.py` 22 条 |
| 角色决策层 | 未映射写法 → 弹窗问"是不是已定义的这个？"；`LJK` 那格**必须拒绝** | `test_char_decisions` 28 + `test_dialog_chars` 11 |
| 自定义模块入口 | 工具栏最上方「🧩 自定义模块」+ 发现未声明写法 + 填声明 → 写 `rules.json` | `test_custom_statement_entry.py` 26 条 |
| 词组分组 | 项目用 `attr_groups` 声明"有哪几组词"；工具**不写死组名**；没声明=完全平铺 | `test_spec_groups.py` 20 条 |
| **V2-1 闸门（当前 HEAD `d107208`）** | 内置语句（`with/window/pause`）不再被当成"工具还不认识"；已声明的内置语句可**一键移除** | `test_discover_gates.py` 30 条，真项目 3→0 |

**进行中**：V2 设计稿 `docs/设计_自定义动作界面_v2_编剧版_2026-10-10.md`（V2-1 ✅ / V2-2 反推核心已通 / V2-3 弹窗重做、V2-4 动作清单页、V2-5 卡片主路径 **⏸ 等用户看设计稿**）。
**本轮新增**：`docs/设计_让工具认识自定义写法_说人话版_2026-10-10.md`（说人话的总体设计稿）+ `docs/契约速查_storygraph.md`（契约分册）。

**❌ 真缺口**（里程碑与验收见路线图）
1. ~~两套 `.rpy` 生成器并存~~ **已完成（10-09）**：统一走 `write_generation` + 「唯一写盘入口」守卫。
2. ~~`asset_engine._apply_rename_patch` 正则改写用户脚本~~ **已修（M2）**：`plan`(纯)→`apply(confirm=)`(未确认零写)→`rollback`（逐位还原）。⚠ 按 D3' **未接 Qt**。
3. **Excel 前端上限（D4）**：`Show`/`Hide` **摊列**已落地；模型与渲染**未动**。判据 `test_hell_show_split_columns.py` 22 条。
4. **坏味道（D5）**：**复杂度头部已清空**（顶格 47）。剩两类：最大模块 >800（`renpy_parser` 4422 / `asset_registry` 3572 / `beat_render` 3388 / `beat_model` 3115）、最大类 >40 方法（`RenPyParser` 50）。脚手架 `tools/_extract.py`。
5. **拍级三路合并**（按需）。6. **多引擎适配深度（M5 ⚪）**：接线/结构位已修；运行时组件未做。

**❓ 待用户决定**：D1 旧定案"树归 Graph / 句子归 Excel"是否撤销｜D2 两套生成器是否收敛｜D3 rename patch 是否接 Qt｜D4 Stage 格级下拉做不做｜D5 坏味道是否进"练习课题"｜D6 `pytest.ini` `--basetemp` 改成不炸（裸 `pytest` 必炸 ~1589 ERROR）｜D7 多引擎是否加深｜D8 提交策略。

## 三、仓库与运行
- 包：`storygraph/{core,excel,parsing,assets,story,pipeline,graph,ui_qt,app,collab,authoring}`
- 入口：根 `main.py`/`cli.py` → `storygraph.main`/`storygraph.cli`（`cli.py selfcheck` 走主链路）
- 依赖：openpyxl（必需）+ Pillow（可选）+ PySide6（UI）；`legacy/` 不参与构建
- 工具：静态体检 `tools/health_check.py`；幂等审计 `tools/audit_idempotence.py`；动态 `tools/hell_*.py`；交付就绪 `tools/journey_check.py`
- ⚠ `tools/audit_real_project.py` 是坏脚本别信输出
- CI：`.github/workflows/ci.yml`（3.10/3.12/3.13 + demo 体检）
- 真机验收对象：`D:\千连万理\RenPyTest\sr_general`（换机 skip）
- ⚠ **并发写入常态**：动手前 `git status` + 文件时间戳。改 UI 用真实 Qt（`D:\Python\python.exe` 有 PySide6），offscreen 可无头起窗口。

## 四、协作方式（用户确认）
- 单人开发：代码主要由 AI 写；用户负责设计/测试/验收，目标是**积累经验 + 学会改进 AI 代码**。
- 多解释"为什么这么设计 / 权衡了什么 / 否决了哪个备选"；用户写代码时别抢答。
- 验收靠**真实项目 + 全量测试**让数据打脸；练习落"关掉 AI 自己写一版"。
- **接受判据校准**：当 AI 的测试判据本身有误（非产品 bug），承认并重校准。
- **用户偏好**：**说人话**（不要技术名词堆砌，编剧也要看得懂）、每个观点要"为什么"、要具体指标/表格/P0-P1 标签；写作侧排斥 AI 味。

## 五、几条最容易踩的坑（实操备忘）
- **两个 registry 不是一回事**：`session.registry()`→资产注册表；语句注册表是 `StatementRegistry.for_project(root)`。混用 → Qt 槽吞异常 → "点了毫无反应"。
- **面板少一个转发方法** = `AttributeError` → `QMessageBox` 模态 → 测试挂死 / 用户看到"卡住"。守卫 `test_every_panel_method_the_window_calls_really_exists`。
- **构造期 `_refresh_preview` 早于控件创建** → 一律 `getattr` 守卫 + 建好后重刷（同一类坑第 3 次）。
- **Qt 的 `QLabel`/`QCheckBox` 不渲染 Markdown**（`**…**` 会显示成星号）。
- **`Statement.source` 是 `(路径, 行号)` 元组**，按纯路径取会得到垃圾样例。
- **pytest 必须带 `--basetemp=<全新目录>`**，否则复用仓库内目录 → ~1589 ERROR（是否改配置 → D6）。
- 其余全部见 **`docs/契约速查_storygraph.md`**。
