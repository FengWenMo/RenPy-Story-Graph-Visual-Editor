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
- **搬家会把"断言 0 次调用"变成空话**：`from ... import` 是绑值。移动任何被 patch 的符号要全仓库搜字符串，**定义处 + 转发处两处都打桩**。
- **搬函数用 `ast` 取范围**（`n.lineno-1 : n.end_lineno`）；"切到下一个 def"会吞掉中间的模块常量。

**Qt**
- 无 `QApplication` 就建控件 = 进程 abort（无 traceback）→ 测试类 `qapp` autouse。
- 面板少一个转发方法 = AttributeError → 模态 → 挂死（守卫 `test_every_panel_method_the_window_calls_really_exists`）。
- **Qt 槽异常只打 stderr = "点了没反应"**：`QMessageBox.addButton(文本, 角色)` 第二参数是 **ButtonRole**（传 StandardButton 当场 TypeError，真机四入口一起失效）。菜单/右栏动作过 `window_nodes._menu_action` 安全壳；有模态的动作要"真建框"判据。
- **模态要打全**：`_ask_delete_mode` 的 `box.exec()`、`_review_stage_suspects` 的 `dlg.exec()`；`QMenu.exec` 打桩不可靠 → 菜单建成纯函数再测。
- **延迟 import 是打桩点**（`card_editor._pick_spec_value` 的 `choose_from`、`_build_toolbars` 闭包里的 `act()`），提到顶层就判据失效 → 真弹框 → 124。
- "扫源码"类判据统一走 `tests/_source_paths.main_window_source_text()`；新模块加进 `WINDOW_SIDE_MODULES`。卡片回调搬走后要 `import ... as CA` 打 `CA.*`。
- QSS 吞 `QTreeView` 展开三角 → 自绘 `▾/▸` + `setRootIsDecorated(False)`；判 dock 用 `isHidden()`；`saveState()` 要 `objectName`。
- **滚轮永远不许改值**（`ui_qt/wheel_guard.py`：`WheelSafeComboBox` + `WheelValueGuard`，`ensure_installed()` 幂等）：不改值、**显式转交**外层滚动，只有候选已弹开才放行。
- 右栏宽度由卡片自己决定：`WheelSafeComboBox` 已钉 `AdjustToMinimumContentsLengthWithIcon` + `MinimumContentsLength=4`，别改回默认；`_dirty_label` 只留「● 未保存」；`window_state.LAYOUT_VERSION=3`。
- **chip 候选圈必须含「空」**（排最后）；⚠ 空 ≠ 组里第一个词（`when` 条件不成立时整组不叠）。`resolve_layers` 只认显式 `default`、无"组里第一个"兜底（`layered_model.py:1075`，未修）。
- 卡片标题拼代号才看得见：`SpriteCardWidget._art_code`（来源 `Term.code`，两半都查得到才显示）；右键候选挂 `A3｜base_hand_up angry`，**值仍是引擎写法**。

**落盘/全局开关**
- 写盘只有一条路 `core/atomicio.py`（临时文件 → `newline=''` → fsync → 多代备份 → `os.replace`），别用 `wb.save(path)`。三条别改回去：备份名带序号（同 tick 会盖掉上一代）｜`os.replace` 对瞬时 `PermissionError` 重试 6×20ms｜Excel 后缀白名单只认 `.xlsx`。
- **两份 JSON 清单各只有一个写入口，且都带跨进程锁**（2026-11-07 第七轮）：`storygraph.ext.json` → `ExtensionRegistry.save()`（三方合并）；`storygraph.rules.json` → `statement_registry._edit_manifest()`（锁里读→改→原子写）。**别再 `path.write_text(json.dumps(...))`** —— 那会让"两个人同时存"静默丢一条。锁在 `core/filelock.py`，文件放**用户缓存**（`…/StoryGraph/locks/<sha1>.lock`），**不写进用户项目**；放锁 = 关句柄（别手删锁文件）。
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
- 中文源码别过 shell 文本管道（PS 5.1 `Get-Content|Set-Content` 会乱码），只用 edit/write 或 Python 脚本。

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

真缺口：D4 Excel 前端上限｜拍级三路合并｜G1 行为级契约进 CI（`skipif(not REAL)` 在 Linux 必 skip）｜G2 引擎版本适配层｜引擎试玩只支持 Windows 源生 SDK｜`resolve_layers` 无"组里第一个"兜底｜`declare_attr_groups`/`remove_statement` 的读还没进锁｜`field` 加新列的导出口径未定。

## 六、协作 / 发布

- 单人开发：AI 写代码，用户设计/测试/验收。多讲"为什么这么设计、否决了哪个备选"；**接受判据校准**；说人话（编剧也看得懂）、给指标/表格/P0-P1。
- 定位：**已有项目的外科手术刀 + 资产治理层 + 跨引擎逃生舱**；三条护栏 = 不改引擎 / 不改用户字节 / 能带走。
- 发布 `packaging/make_release.py`（`test_release_manifest.py`）：fasttest 全绿 → pyinstaller → `StoryGraph-CLI.exe selfcheck`（`REQUIRED_MODULES` 39 个）→ make_release → `tools/_probe_release_zip.py`。⚠ PowerShell `2>&1 | Select-Object` 会把 stderr 变假 exit 1，用 `*> 日志`。
- 许可证 GPLv3 四处同源（`LICENSE`/`pyproject.toml`/`README.md`/`快速上手.md`）。`tools/audit_real_project.py` 是坏脚本，别信。
- **并发写入常态**：动手前 `git status` + 时间戳；文档只追加；提交按文件分组；写完回读校验。
