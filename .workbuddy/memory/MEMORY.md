# 项目记忆：RenPy 剧情编辑器（storygraph）· 索引页

> 只放**最容易踩的坑**与**指针**；易变事实（用例数/行数/耗时）读代码或跑测试，别信文档里的 ✅。
> 详情：`docs/契约速查_storygraph.md`（按需 grep）｜逐日 `.workbuddy/memory/2026-*.md` + `docs/开发日志.md`｜真源与层序：`docs/真源与优先级_设计决策_2026-10-31.md`｜超清 CG/BG 零侵入：`docs/超清CG与BG_零侵入导入_2026-10-31.md`。

## 一、十条铁律（违反任一条都会静默毁掉用户项目）

1. **`.rpy` 唯一真源**；不归己的行逐字节不动，只逐行配对替换真改过的行，绝不整份重生成。
2. **要么全对、要么完全不动**。任一行配不上 → 整份放弃 + 报错，不产出"半对"。
3. **认不出就原样抄**。未知块原样保留，不补 `$`、不包 `python:`、不改缩进。
4. **猜一个名字比说"不知道"坏得多**。只认字面量，不模糊匹配。
5. **绝不静默全量覆盖**。落盘过门禁；`HANDWRITTEN_TARGET` 拒写；改用户 `.rpy` 先出 diff。
6. **教它升级，不许改字节**。教会自定义语法后首次导出必须与教会前逐字节相同。
7. **不许静默丢行**（txt 导入/表格/补丁三条路）。判据是"整段相等"。
8. **一个落盘点一个登记**（`WRITERS`，守卫 `test_single_write_entry.py`）。
9. **本工具不猜语义**。意思由人标注，工具只发现/展示/检索。
10. **含中文/转义的复现一律写 `.py`** —— `python -c` / heredoc 经 Bash 会改坏样本（`show` 行被吞过，曾"复现"出不存在的 P0）。

## 二、引擎事实

- `lint` 默认退 0，**必须 `--error-code`**；`has_image()` 不是合法性判据；`probe_complete=false` 当硬失败。
- 事实文件 `<项目>/storygraph.engine_facts.json`：每条带 `source`；两源不一致记 `conflicts`，不静默挑一个。
- **覆盖内置语句 = 完全替换**；**fork 引擎新增语句对工具隐形**；`test` headless 不可用。
- 真机基准（sr_general）：images 317 / tags 235 / transforms 155 / labels 106。
- 本机 SDK `D:\software\renpy-8.4.1-sdk` 实际是 **8.5.3.26051504**（看 `renpy/vc_version.py`）。
- **引擎试玩**（`app/engine_preview.py`）= 镜像 + 注入 + 隔离：镜像在 `%LOCALAPPDATA%\StoryGraph\engine_preview\`；`config.label_overrides["start"]` + `RENPY_SKIP_MAIN_MENU/SPLASHSCREEN` 进章；`--warp game/x.rpy:行` 进单拍；`--savedir` + `APPDATA/HOME` 重定向 + `RENPY_DISABLE_BACKUPS` 不碰真实目录。
- 验收截图**用引擎自己的 `renpy.screenshot`**（注入 `config.periodic_callbacks`），别用 `ImageGrab`（窗口被挡会截错窗口，误判过"引擎黑屏"）。

## 三、最高频的实操坑

**判空/真值**
- 有 `__bool__` 的对象别用真值判断：`EditorSession`（有 `__len__`）、`StatementRegistry.__bool__`（只有 `calls` 的项目 falsy）。判空一律 `is not None`；兜底别用 `or`（`int(x or -1)` 把第 0 拍当没载入）。`Statement.source` 是 `(路径,行号)`。
- **两个 registry 不是一回事**：`session.registry()`=资产表；语句表是 `StatementRegistry.for_project(root)`。混用 → Qt 槽吞异常 → "点了没反应"。

**测试**
- 全量只用 **`python tools/fasttest/run.py`**（并行 + 独立 basetemp + 运行锁）。裸 pytest 串行 ~20min，两个并发互删 `.pip-tmp/pytest`。退 0/1/2/5，**124=卡死**（打印正在跑的 nodeid → `--serial` 单跑）。`--retry-failed` 只复跑上轮纯全量失败；`--lf` 无效。8 workers ~2m40s/~8900 条；12 workers 会 `0x8007000e`；`test_disk_hygiene` 必须第一个跑；xdist 的 `--loadscope-reorder` 会覆盖重文件优先（fasttest 已传 `--no-loadscope-reorder`）。耗时由少数真机金标主导（scan/validate/patch/describe ~730s，方差 ±40%），别当回归。
- **复杂度棘轮** `tests/complexity_budget.json`（最长 40 + 最绕 40）只许降不许升；硬线 500 行 / 150 分支；`python tools/complexity_report.py`。给顶格函数加注释也会红 → 抽公共段，不改棘轮。
- **技术债棘轮** `tools/debt_report.py` 八项，`tests/tech_debt_budget.json` 快照（环按模块集合比）。⚠ 删单条边不缩小 SCC；互引对要两条都动；共享常量要随函数搬；`modules_in_cycles_all` 才是真债。
- **改这四处先跑地狱判据**：`test_hell_atomic_io.py` / `test_hell_excel_cycles.py` / `test_hell_guardrails.py` / `test_patch_line_splitting.py`。
- **变异脚本不许和全量同时跑**（2026-11-08 第十四轮真踩）：`mutation_*.py` 会**原地**
  改写生产模块再还原；同时在跑 fasttest 时，worker 会导入到变异版 —— 本轮 5 条
  判据集体红（全是 M2/M3/M9 该抓的那几条），一度以为是真失败。看到"整批同类判据
  同时红"先怀疑这个。规矩：mutation 跑完再起 fasttest。
- **搬家会把"断言 0 次调用"变成空话**：`from ... import` 是绑值。移动任何被 patch 的符号要全仓库搜字符串，**定义处 + 转发处两处都打桩**。
- **搬函数用 `ast` 取范围**（`n.lineno-1 : n.end_lineno`）；"切到下一个 def"会吞掉中间的模块常量。

**Qt**
- 无 `QApplication` 就建控件 = 进程 abort（无 traceback）→ 测试类 `qapp` autouse。
- 面板少一个转发方法 = AttributeError → 模态 → 挂死（守卫 `test_every_panel_method_the_window_calls_really_exists`）。
- **按钮连到不存在的方法 = "点了没反应"**（2026-11-07 第九轮真踩）：PySide6 里槽函数抛异常
  **只打 stderr，进程不崩、界面不变**。守卫两头都要有：`tests/test_dead_slots.py`
  （扫 `connect(lambda ...: self._x())` 的名字在包里声明过）+ **真 `btn.click()`** 的判据。
  只测"方法干了什么"（`dlg.export_archive()`）测不出按钮是死的。
- **Qt 槽异常只打 stderr = "点了没反应"**：`QMessageBox.addButton(文本, 角色)` 第二参数是 **ButtonRole**（传 StandardButton 当场 TypeError，真机四入口一起失效）。菜单/右栏动作过 `window_nodes._menu_action` 安全壳；有模态的动作要"真建框"判据。
- **模态要打全**：`_ask_delete_mode` 的 `box.exec()`、`_review_stage_suspects` 的 `dlg.exec()`；`QMenu.exec` 打桩不可靠 → 菜单建成纯函数再测。
- **`QLabel` 不渲染 markdown**：`**`、反引号会**原样显示**（第十轮踩过、第十三轮又踩一次：窗口里写着"这里定的是\*\*留多少\*\*"）。说明文字只能纯文字，路径/名字用「」圈；判据 `test_no_markdown_leaks_into_the_labels`（扫窗口里所有 `QLabel`）。
- **Toast 是一行 `wordWrap=False` 的浮层**（第十四轮踩）：超过窗口宽就被裁，用户看到
  半句话。给 `notify` 的消息要**短**（一个短句）；细节（第几行、为什么）走窗口日志
  `host._log`。窗口里拿 `Toast` 截图 = 最便宜的验收方式。
- **延迟 import 是打桩点**（`card_editor._pick_spec_value` 的 `choose_from`、`_build_toolbars` 闭包里的 `act()`），提到顶层就判据失效 → 真弹框 → 124。
- "扫源码"类判据统一走 `tests/_source_paths.main_window_source_text()`；新模块加进 `WINDOW_SIDE_MODULES`。卡片回调搬走后要 `import ... as CA` 打 `CA.*`。
- QSS 吞 `QTreeView` 展开三角 → 自绘 `▾/▸` + `setRootIsDecorated(False)`；判 dock 用 `isHidden()`；`saveState()` 要 `objectName`。
- **滚轮永远不许改值**（`ui_qt/wheel_guard.py`：`WheelSafeComboBox` + `WheelValueGuard`，`ensure_installed()` 幂等）：不改值、**显式转交**外层滚动，只有候选已弹开才放行。
- 右栏宽度由卡片自己决定：`WheelSafeComboBox` 已钉 `AdjustToMinimumContentsLengthWithIcon` + `MinimumContentsLength=4`，别改回默认；`_dirty_label` 只留「● 未保存」；`window_state.LAYOUT_VERSION=3`。
- **chip 候选圈必须含「空」**（排最后）；⚠ 空 ≠ 组里第一个词（`when` 条件不成立时整组不叠）。`resolve_layers` 没命中又没 `default` 就整组跳过 —— **这是引擎语义，不是缺兜底**（2026-11-07 第八轮核过 SDK `renpy/common/00layeredimage_ren.py`：`Attribute.default` 的文档写着"没有别的属性被选中时才是它"，`get_displayable` 只认属性集合）。**别再给它加"组里第一个"兜底**。
- 卡片标题拼代号才看得见：`SpriteCardWidget._art_code`（来源 `Term.code`，两半都查得到才显示）；右键候选挂 `A3｜base_hand_up angry`，**值仍是引擎写法**。

**落盘/全局开关**
- 写盘只有一条路 `core/atomicio.py`（临时文件 → `newline=''` → fsync → 多代备份 → `os.replace`），别用 `wb.save(path)`。三条别改回去：备份名带序号（同 tick 会盖掉上一代）｜`os.replace` 对瞬时 `PermissionError` 重试 6×20ms｜Excel 后缀白名单只认 `.xlsx`。
- **写章节脚本 / 编排层的原地小改走闸 `write_gate.commit_text`**（第十四轮）：
  `commit_text(path, base_text, draft_text)` = 读现在这份 → 判定 →（必要时）**行级
  三路合并** → 落盘（仍走 `paths.write_text_atomic`）。四态：`same`（盘上已是草稿，
  **不写**）/ `wrote` / `merged` / `conflict`（**一个字不写**）。⚠ `Commit.final_text`
  **不许**当撤销的"期望内容"（撤销比的是"盘上还是我写的那份"，拿合并结果比会放行、
  把别人那几行抹掉）。**只动一行**的那三处（接线/断线/改接线）走
  `story/control_anchor.locate`：按内容认、**只在自己那个 label 块里认**（全文找
  "唯一一处"会认到别的 `_control`）。新落盘点过不了
  `tests/test_merge_gate_coverage.py`（AST 扫，四类之一）。
- **导出那条大路也过三路合并**（第十五轮）：`_patch_note` 发现文件被外人改过时，
  把补丁打在**底稿**（`_source_baseline_text`，`load()` 解析出这些拍的那份文本）上；
  `export()` 再调 `_settle_with_disk` → `write_gate.merge_three_way`
  （与 `commit_text` **同一个判定**）。
  ⚠ `patch_export(base_text=…)` 的产物**一定缺**别人那几行，**必须接着合并**——
  否则就是拿旧底稿覆盖他的文件（守卫 `test_every_base_text_call_site_merges`）。
  ⚠ 合并**只在 `note['used_patch']` 为真时**进：补丁失败时 `text` 还是
  `result.rpy_text`（**全量重生成**），拿它合并 = 静默全量覆盖 + 把真拒因盖掉。
  ⚠ 没有底稿（手工塞 beats / 表格那条路）→ 照旧保守拒绝，别以为它自动好了。
  只合"目标就是源文件"；导出到 `build/` 是"我这一版"，不碰他的文件。
  撞车时理由要点到**第几张卡**（`card_at_lines`），用户面对的是卡片不是行号。
- **`_report_swallowed(mark)` 末尾也调 `_status`**（第十五轮真踩）：任何
  "要说一句成功"的地方都要**先报静默失败、再报成功**，否则那句成功
  **永远看不见**（实测被一条无关的 `cleanup_tmp` 盖成「⚠ 本次有 N 次失败被跳过」，
  判据全绿、截图才发现）。明细不在状态栏：左边 `_diag_bar` 永久一条 + 控制台 + 诊断面板。
- ⚠ **G1 已知缺口**：同一拍"旁白 → 台词 **+ 顺手加一张 `show`**"仍然拒
  （`1 条生成内容在补丁结果里找不到`，**没有外部改动也一样**）。根因：
  旁白被整句换掉时走 `_narration_slot_replaced`，它只做一对一替换
  （`allow_edit=False`）；能加行的 `trust_ranges` 分支在它之后。**是拒绝，不是静默丢。**
  另记一条：`trust_ranges` 真正不可替代的场景是"**连说话人都换了**"
  （`e` → `jc` + 换词）—— 只换正文靠说话人认得出，旁白被换掉有上面那条分支兜着。
- **切行只认 `\n`，一律用 `core.textlines.split_lines`**（第十五轮地狱测试）：
  `str.splitlines()` 把 **U+2028**（从 Word/网页粘台词常带）/ `\x0c` / `\x85` /
  U+2029 都当换行。谁拿它切 .rpy 行、再用 `'\n'.join()` 拼回去，那一行就被
  **撕成两行**（`"你好␨世界"` → `"你好` + `世界"`，台词没结引号），而且可能
  **静默**（合并报 ok=True）。已知踩过三处：`patcher`（早修）、
  `write_gate`（第十四轮写 merge_text 时）、`generation_guard`
  （`top_level_chunks` → 半截像顶层语句 → `OPAQUE_LOST` **假拒写**）。
  守卫：`test_hell_export_merge.py::test_no_splitlines_in_the_merge_chain`（AST 扫）+ 
  `test_patch_line_splitting.py`。
- **"紧挨着" ≠ "同一处"**（第十五轮）：他插的那一行正好贴着你改的那一行时**拒写**
  （第十四轮定的，插入点落在替换段端点上，先后说不准），但措辞必须分开
  —— `write_gate._clash_kind()` 给 `overlap` / `touching`。说成"改的正是同一处"
  时用户会去找"他什么时候改了我这句"，找不到，然后就不信这个提示了。
- **合出来那份能不能启动**（第十六轮地狱测试）：行级合并只认行、不认缩进，所以两边
  **各自合法、又不重叠**的改动合起来可以是坏的。最坏的一例：他删掉 `    menu:`、
  我改菜单项 → 合出 8 个空格悬空的 `"改过的左边":`，**两边谁都没写过**，判 OK 写盘，
  Ren'Py 一启动就报缩进错。现在 `write_gate._merge_plan` 判 `CLASH_REPARENTED`：
  **我改的每一段，合完之后必须还嵌在同一个父语句下面**；块头只是**换了个写法**
  （`menu:` → `menu 选项:`，同缩进、仍以冒号收尾）不算。父语句 = 上方最近一条
  缩进更浅的语句（`_parent_at`），**空行 / 整行注释不构成块**（否则他删个空行
  就会被判成"块头没了"）。判只在 `_merge_plan` 一处，`merge_text` 是它的薄壳
  （AST 守卫）。判据 `tests/test_hell_merge_structure.py` 22 条，变异 9/9
  （M4/M5/M6 是**误伤方向**：块头改名要照合、删注释/空行要照合）。
- **`''` 兼作"空结果"和"合不了"**（第十六轮）：两边各删一半 → 合并结果是空文件，
  从前拒写、理由却说"撞在同一处"（假话）。现在 `CLASH_ERASED` 单独一种，
  `Commit.error()` / `sentence()` / `session_export._merge_conflict_reason`
  三处各说各的实话 —— 用户找的是一个不存在的"他改了我这句"。
- **文件末尾那个换行归"收尾那一行"**（第十六轮）：基线末尾没换行、他往文件末尾
  追加一行（带换行）时，从前一律按基线的结尾拼 → 他那行的 `\n` 被吞掉，
  `git diff` 报 "No newline at end of file"。

- **探针/判据比字节时 `write_text(..., newline='')`**：Windows 上
  `Path.write_text()` 会把 `\n` 翻成 `\r\n`，拿 `read_bytes()` 跟
  `text.encode()` 比就永远"文件变了"（第十五轮探针因此报了两个假 P0）。
- **两份 JSON 清单各只有一个写入口，且都带跨进程锁**（2026-11-07 第七、八轮）：`storygraph.ext.json` → `ExtensionRegistry.save()`（三方合并）；`storygraph.rules.json` → `statement_registry._edit_manifest()`（锁里读→改→原子写；**删文件与备份也在这个出口**：`edit` 返回 3 元组 `(result, changed, delete)`，`backup=True` 备份的是**锁里读到的那份文本**，备份失败 = 整次拒写）。四个删改入口（`remove_statement` / `declare_attr_groups(replace)` / `clear_attr_groups` / `reset_declarations`）全都走它，**别再自己读一遍清单**。**别再 `path.write_text(json.dumps(...))`** —— 那会让"两个人同时存"静默丢一条。锁在 `core/filelock.py`，文件放**用户缓存**（`…/StoryGraph/locks/<sha1>.lock`），**不写进用户项目**；放锁 = 关句柄（别手删锁文件）。
- **用户级设置**（第十三轮）：`%LOCALAPPDATA%/StoryGraph/settings.json` 只有 `core.user_prefs.update(name, patch)` 一个出口（锁里读→**只改这一节**→原子写；patch 里 `None`=删键；读不懂就 `CorruptSettings` **拒写** —— 那文件人也会手改）。⚠ **写盘路每一代都要读它**（脚本留档档位），所以缺/坏时只能退化成默认，**绝不许拦写盘**；`STORYGRAPH_SETTINGS` 可改位置（测试那道 autouse 夹具靠它）。这类东西别用 `QSettings` —— core 没有（也不该有）Qt。
- 合并的"我没动"必须用**规范形**比：`base` 里读来的条目可能带 `origin` 之类的运行时键，文件里新读出来的没有 —— 带着比就全是**假冲突**（`_call_body()` / `to_dict()` 两边同一套）。
- 读 Excel 只有一条路 `excel/safety.py::open_workbook()`（AST 横扫守卫，直接 `load_workbook` 会被抓）。阈值：拒 256MB / 解开 512MB / 单条目 256MB / 2 万条目 / 声明 800 万格。
- **表读不出来时不许重建**：`snapshot_user_content` 对 `UnsafeWorkbook`/`PermissionError` **抛**，只有坏文件才 `None`。
- 来源指纹是完整 sha1（`table_meta.fingerprint_of`），比较走 `fingerprints_match()`（老表 16 位前缀兼容）。
- `patch_export(expect_sha1=…)` 读完再核对；别换成 `(size,mtime)`。
- 资产表 6 个写函数过 `_ASSET_WRITE_LOCK`（无锁 8 线程写 32 行只剩 4 行）。
- **门禁自己出错 = 不放行**（`generation_guard()` 抛 → `GUARD_ERROR` block）。
- 向上找项目根要有边界（`_NEVER_A_PROJECT_ROOT` 跳过 `.pip-tmp`/`build`/`.git`/`__pycache__`；曾让 `show` 变 `show show show`）。
- 全局开关判在唯一写入口（`remember_graph_files` 漏查写过 849 个 tmp 项目）。跑 UI 探针/截图先 `STORYGRAPH_NO_SESSION=1`。
- 纯文字导入必须给 `out_dir`；测试要打桩 `_ask_plain_text_export_dir`，否则模态挂死。
- 中文源码别过 shell 文本管道（PS 5.1 `Get-Content|Set-Content` 会乱码），只用 edit/write 或 Python 脚本。⚠ **`Set-Content -Encoding UTF8` 会写 BOM**（2026-11-08 真踩：改一行 `REQUIRES`，文件头多 3 个字节，`ast.parse` 报 `invalid non-printable character U+FEFF`）—— 内容没乱，所以更难看出来。
- **写 `.rpy` 会自动留一代旧版本**（2026-11-08 第十一/十二轮）：`story.paths.write_text_atomic` 默认 `history=True`，写之前把现在这份存进 `<项目>/.storygraph/versions/<相对 game/ 的目录>/`（`game/` 外；固定名那份永不裁 = 工具第一次动刀前的原稿；一样的内容不重复存；**备份失败就拒写**；**留多久 = 最近 5 份 + 1h/6h/1d/3d/7d/14d 时间台阶各一份**）。所以"改一条 `@at`/改源边/删一行源"这些原地小改也能在关窗之后退回去、也能退回"昨天那一版"；别绕过这个函数直写 `.rpy`（`atomicio` 直写不会进历史目录）。`backup=True` 的整份导出仍留文件旁边的 `.rpy.bak`（老机制，`dialog_cleanup` 认它），列表会把两处合并。

**扫描/刷新**
- 项目级扫描一律走 `story/scan_cache.py`（`mtime_ns+size` + 项目指纹失效，**无 TTL**）。绕过缓存 → 整份解析 6~8 遍 → "连线很卡"。
- 刷新已导入章节：`IncrementalRenPyAnalyzer`（只逐出磁盘上不存在的文件、8MB 上限）+ `import_chapters` 记 `(mtime_ns,size)`；0 改动不弹预览框。

**唯一真源（别另写一套）**
- 优先级 = 一张常量表 `core/authority.py`（engine 0 > mapping-exact 1 > declared 2 > mapping-rule 3 > registry 4 > inferred 5 > unknown 6）：**规则只补空缺，覆盖必须精确**；**投影不投票**（`*.generated.rpy` 不进统计）。守卫 `test_authority_layers.py` 比对文档 §4.3。
- 词汇表合并：`Vocabulary.merged([(层名,词条),…])` 按 `VOCAB_LAYERS`→`AUTHORITY` 层号合并；`plus()` 是"后来者优先"老路，新代码别用。
- 覆盖留痕：`core.authority.Override`（append-only，`sentence()` 唯一措辞）+ `explain()` + `labeled()` tooltip；别在 UI 另写措辞。
- 跨章继承入口状态只从 `app.stage_entry` 取：真源 `continuity.Chapter.entry`（不按文件名排）→ 缓存键 = 项目根 + `project_source_signature`（46ms），慢步在 `ui_qt.entry_warm`。
- `outfit` 代号/表情编号只数作者写的剧本：`sprite_codes._script_texts()` 走 `DERIVED_SUFFIXES`/`SKIP_DIR_PARTS`。
- "这张图是谁"：`assets/asset_identity.resolve_image`（映射精确 → 声明 → glob 规则 → 资源表 Tag → 路径段 → stem → **unknown 不猜**）。
- 舞台几何 `story/atl.py::placement_box`：无 `at`/`fit` 的大图按引擎语义 1:1 贴左上裁切。缓存按字节预算（256MB）。
- `scene`=清台、`show`=只替换同 tag → 改背景尺寸只能写 `show 图@变换`（`app/stage.py::_bg_show_edit` 里**不出现 `scene`**）。超清图 `xxx@2.png` 唯一实现 `stage_render.oversample_of`，只认文件名、永不改名。
- 背景/图层是持续状态，写回要回"写它的那一拍"：`scene_src`/`layer_src`（0 基，-1=不知道，**0 合法**）；`plan_at_edit(source_index=)`。真机 1013 个有背景的拍里 972 个是继承的。
- 生成 `sg_*` 一个入口 + 三道锁（`story/sg_transforms.py`）：`# sg-gen v1 sha256` 指纹 → 三层查重（reuse/几何等价/noop，撞名 `sg_xxx_2`）→ `missing_references()` 出 `E305`。
- 改一条 show 的 `@at` 只有 `story/beat_stage_parse.set_item_at`。坑：`show cg cg1@half:` 行尾冒号是 ATL 块，改写会吃掉冒号（已修）。
- "引擎写法 ⇄ 人话"只有 `assets/vocabulary.py::Term(kind,id,label,code,char,note)`；查不到原样显示，模糊匹配只在唯一命中时认。
- "显示值 ≠ 真值"只有 `ui_qt/labeled_choices.py`（真值进 UserRole，**真值优先**）。坑：`mapping_cached()` 返回三元组（唯一访问器 `session.vocabulary()`）；可编辑下拉 `setEditText` 后 `currentIndex` 仍指上一项。

**映射（Excel/JSON 双写法）**
- 同目录 Excel 优先：`storygraph.mapping.xlsx`（`assets/mapping_excel.py`：一表=一顶层键、空格子不写、`备注` 列忽略）。**xlsx 只读，绝不回写人做的表**；生效源每处都要说出来。
- 表格编辑 `ui_qt/mapping_editor.py` + `assets/mapping_edit.py`（`SPECS` 六段），**JSON 是唯一产出**：深拷贝 + 只改认识的键；空壳行不落盘；表不许 import `save_mapping`。坑：`itemChanged` 里不许重建表；列宽不许伸缩有内容的列。
- **「角色 0」= 没有覆盖，不是没读到**；编辑页当场说明 + 给「把资源表里的角色写进来」按钮（只加不改），不许开窗自动抄。
- **删立绘卡 = "这一拍不要让 ta 在台上"**（2026-11-08）：沿用卡（`inherited`）点 ✕ 不删卡，改成「退场卡」（`hide: True`，↺ 撤销）；保存走 `table_edits._apply_hide_cards` 并进 `beat.hide`（**只加不减**，`cg@dissolve` 这类看不懂的条目一个字不动）。退场卡**不跨重开回填**（存完就是一条 `hide`，改回来去表格「退场」列）。自己的 `show` 卡删掉只是删 show —— 人还在台上时网格会把 ta 以「沿用」摆回来，再点一次 ✕ 才 hide。
- 编号表**不只角色文件夹**（2026-11-08）：`assets/code_tables.py` 读**任何**目录里的 `对应.txt`（`1——Empt1y`；`8——` 空号也算读出来），名字去尾部 `（备注）`/`……` 后**只认唯一命中**（两个都像就报 ambiguous，不猜）；产出 `vocabulary` 行（`kind/id/code/label/note`），目录里有、表里没号的文件也出一行空号，缺口进 `notes`（剧本在用却空号 → 预留空号 → 对不上文件 → 同曲两号 → 没号文件）。接在「按项目自动填」：`autofill_codes(codes=, vocabulary=)` 两段**分开判**、只加不改。⚠ 这里三处 `except OSError` 是窄捕获、不调 `swallow`（技术债棘轮 `swallow_calls` 上限 1293，加一个就红）。

**舞台/卡片/过场**
- 卡片单拍舞台图走 `stage_render.render_frame`；后台线程 + 防抖 120ms + 2 线程 + 门闩；重画判据用 `frame_signature` 内容指纹（不许 `id(frame)`）。
- **过场是项目自定义的**：`$ renpy.transition(...)` → `story/transition_expr`（安全字面量解析，无 eval）→ `transition_catalog` → `app/transition_fx`。**认不出就降级并标「无法精确预览」，绝不假装淡入**。
- 带 ATL 块的 `show`：判据是"节点有没有块体载体"（`_BLOCK_CARRYING_NODES`）；`ln.text` 自带缩进；`LogicalLine.subblock` 没有空行（按行号从源取）；opaque 令牌排在 `mark_op('show')` 之后。
- 归属审计：`_HANDLER_OF` 是"每种语句去哪"的唯一清单，`_BeatBuilder.unassigned` 断言既不进 pending 也不建拍的语句。

**CI/环境**
- 本机 3.12、CI 最低 3.10：用 `ruff check --target-version py310`，判据 `invalid-syntax`（E999 已删）。
- Windows 专属调用在 POSIX 上要退化（`cmd.exe`+`mbcs` 抛 `LookupError` 不是 `OSError`）。
- `SSL certificate problem` 但 `Invoke-WebRequest` 200 ⇒ `git config --local http.sslbackend schannel`。
- 别用 `rebase -i` + 编辑器脚本改提交信息（会静默丢提交）；reword 用 `git commit --amend -F 文件`，rebase 后 `git log --oneline -N` 核对。

## 四、通用性（用户质疑过"只服务我的项目"）

- 逻辑层**没有按项目名分支**（AST 扫非 docstring 字符串，21 处命中全是示例文案）。
- 三处预设了项目形态：① `sprite_codes._EXPR_GROUPS`/`_OUTFIT_GROUPS` 是白名单猜测（dress/costume/wardrobe/armor 认不出 → 留空并点名）；② `vocabulary` kind 同义表同样漏；③ `examples/mapping/sr_general.*` 是真实项目导出（隐私），应换 `demo/` 自造小项目。
- UI 全中文，但表头/别名配英文（`tag/display/glob/kind` 都认）。

## 五、当前的片 / 真缺口

已完成（判据见同名测试）：E1/E2/E3 引擎事实｜纯文字导入 S1~S4｜自定义动作弹窗 v4｜资产维度结构化｜立绘卡一维一格｜S1 删边=删拍｜S2 选择点｜S3 控制流守门｜L7 版式恢复｜导入冲突预检｜记忆门禁｜卡片单拍舞台图｜右栏竖向配额｜引擎试玩｜舞台可疑行确认｜资产身份解析｜自定义模块 v7｜尺寸面板 + `sg_*` 三护栏｜显式换景｜本景单独改写计数｜合并回源｜跨章继承闭环｜自定义倍数｜卡片代号拼接。

- **扩展平台 v2（2026-11-07 第二轮）**：`ext.bridge` 把扩展点接进 `StatementRegistry`
  （窗口/插件里**新建**的写法与调用真的生效，来源记得住）+ 字段词汇统一成解析器的格类型
  （`field_of_slot` / `slot_of_field` 互逆）+ 批处理开关过 `batch_edit.parse_rules`
  + 值格吞多词（`multi` 默认 + 自由文本计价）。判据 `tests/test_ext_runtime.py`（18 条），
  详见 `docs/设计_扩展平台_2026-11-07.md` §九。

- **扩展平台 v6（2026-11-07 第六轮）**：`ExtensionRegistry.save` 改成**三方合并**
  （`base`=装载时文件原话 / `mine` / `theirs`=此刻磁盘，按 id 判；同一条两边都改 →
  默认**不写盘** + 返回 `conflicts`，窗口当面问）。文件里别人的顶层键、认不出的条目
  原样写回；被插件盖住的项目声明不再"下次保存顺手删掉"；**开关跟着 id 走**
  （`enabled/batchable/batch` 在插件之后再贴一遍，不只 override 记录）。
  判据 `tests/test_ext_merge.py`（24 条），详见同文档 §十五。

- **收口（2026-11-07 22:40）**：七天的未提交工作清进库（9 个提交
  `1c8c75c`…`0c144b2`，239 文件）。并行工作留下的 7 条红逐条判断后
  修掉 / 改判据 / 更新金标，全量 **10085 passed / 0 failed**。
  ⚠ 老病复发：`assets/engine_images.py` 里 `:\n` 被写成了 `://n` ——
  生成非法 `.rpy`，而解析只认另一个文件的错误 → 真机"引擎裁决"静默全绿。
  提交前 `grep -rn '://n\|//n'`（详见当日记忆「收口」）。

- **扩展平台 v7（2026-11-07 第七轮）**：① 声明可移植档案
  `ext/archive.py` + 窗口「导出…/导入…」（`*.sg-ext.json`，只带
  `project`/`plugin:` 来源，冲突两方判定，坏一处整份拒绝）；② **跨进程锁**
  `core/filelock.py`（用户缓存里的 OS 级字节锁；`ext.save()` 与
  `_edit_manifest` 的"读→合并→写"整段在锁里）；③ `rules.json` 写路收成
  `_edit_manifest` + `core/threeway.merge_by_key`（与 ext 同一张决策表），
  `save_calls(base=…)` 不再删别人新加的调用；④ `placeholder` 一路串到
  卡片参数格。判据 4 个新文件 40 条，变异 5/5 抓住，全量 **10125 passed**。
  ⚠ `field` 扩展点**加新列**仍没做 —— 卡在"这一列导出成什么"（文档 §16.6）。

- **扩展平台 v8（2026-11-07 第八轮）**：清单的**删**与**备份**也进锁。
  `_edit_manifest` 多了 `delete` 出口（`edit` 返回 3 元组）与 `backup=`
  （备份**锁里读到的那份文本**，失败即拒写）；`remove_statement` /
  `declare_attr_groups(replace)` / `clear_attr_groups` / `reset_declarations`
  四条路的"读→判断→备份→删/写"全在锁里，`_write_manifest_after_removal` 删掉。
  对外返回键不变、界面未改。判据 `tests/test_manifest_locking.py` 7 条
  （含三条真并发），变异 4/4 抓住，全量 **10132 passed**。

- **扩展平台 v9（2026-11-07 第九轮）**：① **两个死按钮**修好 —— 上一轮
  「导出… / 导入…」连的是 `self._export_archive()` / `self._import_archive()`
  （类里没有这两个名字），PySide6 槽里抛异常**只打 stderr**，用户看到的就是
  "点了没反应"；上一轮判据直接调方法、没一条真点按钮，所以全绿。
  现在 `tests/test_ext_carry.py` 有两条**真点击**，`tests/test_dead_slots.py`
  （2 条）扫 `connect(lambda ...: self._x())` 的名字必须在包里声明过
  （⚠ 只挡"名字整个不存在"，跨模块 mixin 靠 `def` 同名蒙过）。
  ② **导入的退路**：`save()` 多返一个 `undo = {path, before, after}`
  （`before` = **锁里用 `newline=''` 读到的原话**，`None` = 保存前没有这份文件；
  **一个字节没改就返回 None**，连点两次保存不该假装能退）；新函数
  `ext.registry.restore_manifest(undo)` **锁里比对**：磁盘上还是 `after` 才
  整份写回 `before`（`write_text_atomic`，`backup=True`）/ 删文件；
  别人改过或删了 → **一个字不写**，`changed: True`。窗口里
  「撤销上次保存」按钮（`_undo_save`），快照放**窗口**不放注册表
  （`_reread()` 每次保存后换新注册表对象）。判据 `tests/test_ext_undo.py`
  14 条（CRLF 逐字节 / 双进程同刻撤销恰好一个成功 / 别人持锁就拒绝），
  变异 5/5 抓住，全量 **10150 passed**。

- **扩展平台 v10（2026-11-07 第十轮）**：① **关窗之后也能退** ——
  `core/versions.py`（通用：列 `.bak` 世代 / 读某一版 / **锁里放回**）+
  扩展窗口「历史版本…」按钮 + `ui_qt/dialog_versions.py`。放回三道闸：
  逐字节（`read_text_exact`）、`expect_current` 不符就**一个字不写**、
  写前先备份现在这份（**不是单向门**）；`None` 是**合法期望值**（当时文件
  不存在），"不校验"另用哨兵 `_UNCHECKED`。第 5 代上限来自
  `atomicio.KEEP_BACKUPS`。② 顺手修 `save()`：**内容一样也重写**（白烧一代
  备份、mtime 跳，`txt_pipeline` 2026-10-26 就立过"一样就不碰"的规矩）——
  现在 `unchanged: True` + **不碰文件**。③ 顺手修 `_reread()` 之后的**死表单**：
  `_current` 置 None 但右栏还显示旧那格，接着改会被 `_save` 里那句
  `if self._current is not None` **静默跳过**（上一轮撤销就踩过）——现在要么
  重选同一条、要么 `_detach_form()` 停用清空，开窗没选中也停用。
  判据 `tests/test_versions.py` 23 条 + `tests/test_ext_stale_form.py` 4 条，
  变异 9/9 抓住，全量 **10181 / 10177 passed**。
  顺带：`atomicio.read_text_exact` 提为公开（与 `write_text_atomic` 对称），
  `registry._read_text_exact` 改成转发 —— 换行翻译差一个字节，"逐字节放回"就破功。

- **扩展平台 v11（2026-11-08 第十一轮）**：**「整章应用」真的进导出了**。
  `batchable` 那个开关从前只喂「批处理窗口 / CLI batch」（v3 接的那两条），
  `session_export.export()` 压根不读 `rules_from_extensions` —— 用户配得再对，
  导出也不会有那行代码；而且他那次存进 `ext.json` 的 `insert.text` 是**空串**
  （界面上打了字、存的时候没带上），引擎判不合法，**没有人说出来**。
  现在：导出落盘后 `_batch_after_write` 按声明补行（计划/落盘全用
  `app.batch_edit`，只吃扩展声明，`storygraph.batch.json` 仍归批处理窗口）；
  **零改动早退那条路也跑**（2-1-3 正是 0 改动）；补完若动的是载入的文件就重读；
  不合法/算不出/写不进 → `warnings` 最前（"导出没效果"时最该看到的那句）。
  窗口保存时勾了批处理但代码框为空 → 状态栏当场点破。判据
  `tests/test_export_extension_batch.py` 8 条，全量 **10430 passed**。

- **脚本 v11（2026-11-08 第十一轮）**：**剧本也有磁盘退路了**。上一轮只覆盖
  `ext.json`；这一轮补"真正值钱的文件"。量到的事实：整份导出（`write_generation`）
  一直在文件旁边留 `.rpy.bak`，而**原地小改家族**（`story.paths.write_text_atomic`
  不带 `backup=`：`graph/commands` 改 `@at`/换表情/改源边、`control_*`、`choice_point`、
  `source_line_delete`、`sg_transforms`）**一个备份都不留**；真项目 95 个 `.rpy`
  里 `*.rpy.bak*` 共 0 个。做法：`story/version_store.py` 把旧版本存进
  `<项目>/.storygraph/versions/<相对 game/ 的目录>/`（`game/` 外，引擎不扫、
  `git status` 看不见 —— 目录里有自忽略的 `.gitignore`，**不动**用户的），
  命名/裁剪沿用 `atomicio`（固定名那份永不裁 = 原稿），**去重比全部世代**、
  **空转写盘不烧一代**、**备份失败就拒写**。界面：菜单「🕘 这个脚本的旧版本…」
  复用 `dialog_versions`，注入 `store`（一个壳：怎么列 + 怎么放回）与
  `before_restore`（**有没导出的改动就不放回**）；放回后 `load_script` 按磁盘重读。
  `story/roots.py` 抽出 `project_root_of` 断掉 `paths ↔ version_store` 的导入环。
  判据 `tests/test_version_store.py` 29 条（含一条走**真命令**
  `source_line_delete` 的、一条**真 `trigger()`** 菜单项的），变异 15/15，
  全量 **10210 / 10206 passed**。详见 `docs/设计_脚本历史版本_2026-11-08.md`。

- **脚本 v12（2026-11-08 第十二轮，同日）**：**留多久 / 占多少 / 清掉**。
  第十一轮直接沿用了 `KEEP_BACKUPS=5`，探针一量：连改 30 次只剩 5 代、
  跨度 **1.1 秒**，跨三天各改 10 次之后"昨天那一版"**一个不剩**。补法：
  `atomicio` 的裁剪多一层**时间台阶**（`keep_ages`，只给脚本用；`.json`/`.xlsx`
  那条老路一个字节没变）—— 固定名（原件）+ 最近 5 份 + `1h/6h/1d/3d/7d/14d`
  各留一份"最新的、已经比它更旧的"，每份脚本最多 12 个文件。
  ⚠ 两个坑：① 台阶按**年龄**不按日历（昨天那一版是**今天第一次保存**时才写进
  备份的，mtime 是今天，按日历分组会把它算成今天的、一裁就没）；② 固定名
  **必须排除在台阶之外**（它永远留着且通常最老，否则每个台阶都命中它 = 台阶全废）。
  界面上：历史版本窗口状态栏加了合计（`共 585 B`）、顶部说明加了"留多少"一句；
  菜单多一条「🧹 历史版本（占多少 / 清掉）…」（先摊账：文件数/大小/脚本数/
  最早到哪天，再问，点「全部删掉」才删；符号链接不跟，`.storygraph/` 留着）。
  判据 46 条，变异 11/11，全量 **10227 / 10223 passed**。

- **脚本 v13（2026-11-08 第十三轮，同日）**：**留多久可以改**（上一轮的代数 /
  台阶是模块常量）。探针 `.pip-tmp/probe_r13_settings.py`：真项目（95 个 `.rpy` /
  1.26 MB）默认档每份最多 **11** 个（固定名含在 5 代里 + 6 台阶）≈ **13.9 MB**；
  读一次设置 **0.2 ms** → **不加缓存**（每代都读，改完立刻生效）。
  做法：`core/user_prefs.py`（**用户级** `settings.json`，`STORYGRAPH_SETTINGS`
  可覆盖；`read()` 缺/坏 → 全默认、**绝不拦写盘**；`update()` 锁里读→只改这一节→
  原子写，`None`=删键；文件坏了 → `CorruptSettings` **一个字不写**）+
  `version_store.PRESETS`（`lean` 3 / `default` 5+6 台阶 / `more` 8+9 台阶，
  **界面不提供"一个都不留"**；手写 `keep: 0` = 只留原件，窗口标"自定义"且不可选）+
  `retention()`（`problems` 点名看不懂的值）/ `save_retention()` / `max_files()` /
  `project_scripts()` + 窗口 `ui_qt/dialog_retention.py` + 菜单
  「🗂 历史版本留多久（占多少 / 改小改大）…」。**改小下一次写盘才裁、改大不补回**
  —— 窗口里写着这两句（判据也钉住）。`tests/test_retention_settings.py` 42 条，
  变异 **12/12**，全量 **10269 / 10265 passed / 0 failed**（2m15s，12 进程；
  上一轮 10227，差 42 = 新判据）。`tests/conftest.py` 多了 autouse
  `_isolated_user_prefs`（不然本机档位会让旧判据集体变红）。
  详见 `docs/设计_脚本历史版本_2026-11-08.md` §十四~十七。

- **别人也在改这个脚本（2026-11-08 第十四轮）**：上一轮补"退回去"，这轮补
  **写进去的那一刻**。探针量到：`.rpy` 落盘点 **18 处**，"内容变了就拒写"的闸
  17 处有、1 处没有、**三路合并 0 处** —— 而 `write_gate.merge_text` 2026-10-31
  就写好了，**生产代码零调用方**（"已实现没接线"）。做法：
  `write_gate.commit_text(path, base, draft)` 四种结局
  `same`（盘上已是草稿→**不写**，不烧历史版本）/ `wrote`（=从前逐字节）/
  `merged`（两边不重叠→都落上，`kept` 说清保住谁那几段）/ `conflict`（撞同一处→
  一个字不写）；写盘仍只走 `paths.write_text_atomic`。
  `story/control_anchor.py`（新）管"只动一行"那三处：**按内容找回锚点**，
  而且**只在自己那个 label 块里认**（全文找"唯一一处"会把 `ch02_control` 的
  `return` 认成 `ch01_control` 的，接线写错章）。
  ⚠ **`Commit.final_text` 不许当撤销的期望内容**（撤销比的是"盘上还是我写的那份"，
  拿合并结果比会放行、把别人那几行抹掉）；回执里 `old_text/new_text` 仍是计划那两份。
  ⚠ **Toast 是一行 `wordWrap=False`** → 短句进 notify、长句（第几行 + "要重新载入"）
  进窗口日志（第一版 110 字被裁在句子中间，**截图才发现**）。
  判据 `tests/test_merge_commit.py` 28 + `tests/test_merge_gate_coverage.py` 7
  （后者用 `ast` 扫"真调了 `write_text_atomic` 的函数"，每处必须落进
  过闸/认锚点/撤销/例外 四类之一），变异 **16/16**，全量 **10307 / 10303 passed**。
  ⚠ **变异脚本与全量并发 = 假失败**：mutation 会原地改写生产模块再还原，
  同时在跑 fasttest 会让 worker 导入到变异版（本轮 5 条判据集体红，以为天塌了）。
  详见 `docs/设计_别人也在改_三路合并_2026-11-08.md`。

- **同一个判定得两边都看（2026-11-08 第十六 / 十七轮）**：第十六轮给合并加了
  "合出来那份能不能启动"的闸（父语句被改掉就拒写 / 合起来变空文件 / 末尾换行
  归收尾那一行），但**只查我这一侧**。第十七轮把另一面补上 —— 实测同一个例子：
  A 方向（盘上=他删了块头）拒写，B 方向（盘上=我改的、我这一刀删块头）**合上了**，
  写出 8 个空格悬空的选项。**同一个剧本、同两个人，谁先导出决定这份文件坏不坏。**
  做法：`_parents()`（单调栈一次扫描，2 万行仍是 O(n)）+ `_home_of()`（两边坐标
  映回基线 → "父语句是同一条"按**基线身份**判，"块头换了个写法"自然算同一条，
  取代了原来按缩进/冒号的形状豁免）+ `_orphan()`（**两边改过的行都查**）。
  新原因 `CLASH_REPARENTED_OTHER`（他那几行会被挪），措辞三处各说各的实话
  （`Commit.error()` / `sentence()` / 导出那条路 `_merge_conflict_reason()`，
  **这一处最容易漏**：漏了就是兜底那句"改的正是同一处"的假话）。
  顺带补"末尾换行归谁"的另一半：收尾行谁都没动时按"谁改了文件末尾"定，
  只看盘上那份会又变成"取决于谁是盘上"。
  ⚠ **已知不拦**：谁都没动过的那几行不查（块头被删、块里的行两边都没碰 →
  合并会把它们留着悬空）。要拦得先判断"块头是被删了还是被换了写法" = 块树对位。
  判据 `tests/test_hell_merge_structure.py` 30 条（新 8 条），变异 **9/9**，
  全量 **10373 / 10377 passed / 0 failed**（2m21s）。
  详见 `docs/设计_导出这条大路_三路合并_2026-11-08.md` §四·七 / §四·八。

真缺口：拍级三路合并（**导出那条大路**仍拒写：改一句台词→导出，被别人动过就退回）｜**块树对位**（"谁都没动过的那几行"不查，第十七轮记的）｜相邻冲突没有"人来点"的界面｜`batch_edit` 的合并（要先让预演显示合并进来哪几行）｜`control_takeover` 多文件合并｜G1 行为级契约进 CI（`skipif(not REAL)` 在 Linux 必 skip）｜G2 引擎版本适配层｜引擎试玩只支持 Windows 源生 SDK｜`field` 加新列的导出口径未定｜撤销不做重做（卡片页/表页那边，图级有 `redo()`）｜D4 Excel 前端上限。（`resolve_layers` 那条老账第八轮核掉了：引擎本来就没有"组里第一个"兜底。）

## 六、协作 / 发布

- 单人开发：AI 写代码，用户设计/测试/验收。多讲"为什么这么设计、否决了哪个备选"；**接受判据校准**；说人话（编剧也看得懂）、给指标/表格/P0-P1。
- 定位：**已有项目的外科手术刀 + 资产治理层 + 跨引擎逃生舱**；三条护栏 = 不改引擎 / 不改用户字节 / 能带走。
- 发布 `packaging/make_release.py`（`test_release_manifest.py`）：fasttest 全绿 → pyinstaller → `StoryGraph-CLI.exe selfcheck`（`REQUIRED_MODULES` 39 个）→ make_release → `tools/_probe_release_zip.py`。⚠ PowerShell `2>&1 | Select-Object` 会把 stderr 变假 exit 1，用 `*> 日志`。
- 许可证 GPLv3 四处同源（`LICENSE`/`pyproject.toml`/`README.md`/`快速上手.md`）。`tools/audit_real_project.py` 是坏脚本，别信。
- **并发写入常态**：动手前 `git status` + 时间戳；文档只追加；提交按文件分组；写完回读校验。
