# 项目记忆：RenPy 剧情树编辑器（storygraph）

## 怎么用这份记忆

**易变事实一律不写这里**（列数/用例数/行数/性能/测试数）→ 去读代码或跑测试。
这里只放：契约、稳定事实、待办缺口。曾三次出现"记忆说的"和"代码里的"对不上，
所以**别信这里的数字**，只信这里的"哪条不许违反 + 为什么"。

## 一、架构契约（不许违反）

### A. 真源与 IR

- **A1 `.rpy` 是唯一真源**。模型是 **单源(.rpy) → 多视图(Graph/Excel) →
  受控 patch**，不是"多源→生成"。`default`/`define`/`init python`/`transform`/
  `screen` 属"第三类"：不进 Beats。不归自己的行**逐字节不动**。
  ⚠ 修正一档：**顶层 `init python:` 已有随表通道**（`FileBlocks`，见 A3）；
  其余第三类只能靠**增量路径**保住 → 没给 `--source` 的全量导出由门禁
  `OPAQUE_LOST` 拒写（见 B4），**不许静默抹掉**。
- **A2 `core.statement` 是唯一 IR**；禁止第二套 AST walk。IR 是**语句流不是
  语句树**：`elif/else` 展开成 `not((前一条件))`；`menu` 靠**渲染前把扁平拍重新
  归组**复原（`beat_render.split_beat_units` + 解析器 `_menu_member` 标记）。
- **A3 保真后门两条，作用域不同**：`CodeBlock`（一拍之内的 `python:`）与
  `FileBlocks`（整个文件的顶层 `init python:`）。同一语句绝不许走两条通道各出一次。
- **A4 整行注释必须保真**：lexer 存**含缩进**的原文（存 stripped 会双重去缩进，
  把注释啃成非法 ATL）。⚠ 行尾注释**刻意**剥掉（`_strip_inline_comment`，有测试钉着）
  —— 逐字保真的已知缺口。

### B. 写入与落盘（最高危区）

- **B1 落盘一律过 `story.generation_guard.write_generation`**：block 级问题不写
  目标路径、改写 `<stem>.blocked.rpy`；`LABEL_COLLISION` 只在产物真会落进
  `<项目根>/game/` 时判。**"目标已存在且首行无生成标记"= 手写文件 → 拒写**
  （`HANDWRITTEN_TARGET`）；`force=True` 只越这一条，不越"产物自己坏了"。
- **B2 全量只许用于新建；改已有 .rpy 一律走 `story.patcher`**。契约"要么全对、
  要么完全不动"：按行身份配对，缩进只来自原文，替换行必须来自**真实产物**，
  不过就 `ok=False` 且 **text 不可用**。
  教训（写进契约）：**失败时不要交回一份可用文本**——给了就会有人拿它去写盘。
- **B3 ✅ P0「静默全量覆盖」已修**（用户自己修的）：`app/session_export.py`
  `_patch_note` 失败返回 `blocking` 且 `text=''`；`export` 见到不写盘。
  红测试 `tests/test_p0_no_silent_full_regen.py` 已绿。
- **B4 CLI 也能增量，且"降级"必须说出口**：`table xlsx2rpy` / `compile` 加
  `--source <原 .rpy>` → `pipeline.compile.merge_incrementally`（**该判据只有一处**）。
  补丁没配上又确实会丢顶层语句 → `OPAQUE_LOST` 拒写（`force` 降级为 warn 但仍报）。
  不加 `--source` 就永远走全量 → 只能新建。

### C. Stage / 演出投影

- **C1 `Stage` ↔ 结构化列互为投影，但单向 + 幂等 + 只输出一次**
  （`tests/test_stage_projection.py`）：结构化字段是唯一真源，Stage 只承载它表达
  不了的东西；`hydrate` 必须幂等；Stage 里的**原生真迹**逐字原样输出；中文化写法
  按**语义**判"已被承载"（`_stage_op_covered`，别靠字符串相等）；重投影用
  `stage_only_ops()` 留住 `[停顿] 1.0` 这类只能写在 Stage 里的指令。
- **C2 `beat.at` 是"整拍兜底位置"**：只有"所有立绘位置一致"才能同步它。
- **C3 `compose_stage` 必须与 `_stage_lines` 同源**——不一致就是"看到的 ≠ 生成的"。
- **C4 裸词位置要归一**：`角色 表情 左` 的"左"是位置不是表情（不认会静默丢位置）。
  立绘中文名先经 `symbols.actor_image_tag` 归一成英文 tag 再查表；**查图必须按
  Tag 收窄**（真实项目 `lan`/`ling` 都有 `oh`）。

### D. 资产

- **D1 引用判定唯一真源 = `asset_registry.resolve_references`**（循环里必须传
  `AssetIndex`）。
- **D2 词表真源 = `core.vocabulary`；补全打分真源 =
  `assets.asset_completion.score_candidates`**。
- **D3 transform 的 `ATL`/`Params`/`Doc` 以脚本为权威**（`update_keys=` 回写）；
  人写的 `Category`/`Notes` 不动。
- **D4 注册表图片 Path 有**两套基准**：扫描行相对项目根（`game/images/…`），脚本
  `image` 声明行相对 `game/`（`images/…`）。纯色在 `Solid` 字段。
- **D5 报"缺资源"要给建议**（真实项目大量缺资源是文件名被手写截断）→ `# TODO`。
- **D6 校验分级**：有表却查不到 → 错误；没建表 → 警告。宁少报。
- **D7 编译路径不校验**：缺资源写 `# TODO` 且不生成该语句；体检是独立命令。

### E. Excel / 表格

- **E1 安全写单元格唯一实现 = `excel.cells`**
- **E2 `beat_model.BEAT_FIELDS` 是列顺序唯一真源**（含隐藏列）
- **E3 `Stage`/`BGM`/`SFX` 格内是 DSL**：方括号被解析校验，原生写法直通
- **E4 `import_beats_from_excel` 一律 hydrate**
- **E5 `LineId` 是协作主键**；`ContentHash` 每次重算
- **E6 `dsl.normalize_text` 只归一化分隔符**，绝不动 `，（）`
- **E7 单行列的单元格不许有换行 —— 压平必须留痕**（2026-10-05，P0-1 的
  **另一个出口**）：`_quote` 管的是**字符串字面量**那条出口；而
  Label/Speaker/Show/At/With/Condition/音频参数这些列是**裸 token** ——
  值被逐字拼进一行代码，真换行漏出去就是产物里的物理换行
  （`Speaker='ling\nX'` → 产物不可解析；`Show='ling\nhappy'` →
  **能解析但立绘变两张**，门禁拦不住后者）。单元格里有真换行是真实可达的
  （Excel 的 Alt+Enter / 粘贴 / 脚本写表）。
  判据：`SINGLE_LINE_BEAT_FIELDS`（**写在 `BEAT_FIELDS` 旁边**，加列必须
  显式选一边，有测试守）+ `sanitize_single_line_cell`，**读写两侧都过**
  （只修一头会像 `Notes` 那样往返不幂等；且 openpyxl 保存时对 `\x0b`
  直接抛 `IllegalCharacterError`）。多行列（台词/注释/Stage/Code/
  FileBlocks）**刻意豁免** —— 它们的换行是内容。
  ⚠ 压平**本身就是改用户数据**：必须留痕（`Beat.sanitized_cells`）并说出口
  —— `validate` 的 **W114**（聚合一条）/ `CompileResult.summary()` /
  `EditorSession.export` 的 `note`。
  ⚠ 留痕要放在 `from_row` **所有 `g()` 之后**：结构化列在 `cls(...)` 之后
  才读，放前面会丢掉 12 列的留痕（恰好是最常粘脏的立绘/位置/音乐）。
  守卫 `tests/test_cell_control_chars.py`（**用例表由清单反推**）；
  变异 `tools/mut_cell_sanitizer.py`（关掉清洗 → 64 条红）。

### F. UI

- **F1 界面只从 `ui.tokens` 取色值/字号/间距**。立绘是**网格卡片**；缩略图一律走
  `ui.thumb_loader`（禁止 UI 线程同步解码）。
- **F2 只读 tkinter `Text` 会静默吞掉程序化写入** → "看着空的"会被写回当成"清空"。
  要灰显别用 `state='disabled'` 建字段。
- **F3 翻页是纯导航**：只提交当前拍，绝不许碰别的拍；便利功能不许挂在导航上。
- **F4 辅助功能不许改用户已有数据**（轮转说话人只能"补空缺"）。
- **F5 演出预览贴图回调只替换那一块**，绝不许重画整帧。
- **F6 落盘时机要在界面说清**：卡片改动先到内存，**导出 .rpy 才落盘**。
- **F7 任何"写到某路径"的入口，先认路径再碰磁盘**（2026-10-05）：不是
  `str`/`Path` 就 `ok=False`。反例：`export_control_flow(wrapper=False)`
  把 bool 串位进 `target` → `str(False)` 当路径 → 根目录长出名叫 `False`
  的文件。**"开关型参数"默认 keyword-only**（`*`），位置传错不报错正是
  这类 bug 的温床。守卫 `tests/test_control_flow_target.py`。

### G. 架构 / 依赖

- **G1 依赖方向单向**：core ← parsing/excel ← assets/story ← pipeline ← ui。
  story 层不许顶层 import pipeline。守卫 `tests/test_package_layering.py`。
- **G2 改中文 `.py` 只能用编辑工具或 Python 脚本**（PowerShell 会 GBK 往返毁文件）。
- **G3 ⚠ Git Bash 的 heredoc 会做 MSYS 路径转换**：写含 `\n` 的 Python 源码时
  `\n` 会变成 `/n`，脚本静默跑歪（2026-10-05 实测踩到，白白查了 20 分钟）。
  **探针脚本一律用编辑工具写文件**，别用 `cat <<EOF`。（与 H2 同源）

### H. 渲染输出的硬约束（2026-10-05 补，两个 P0 的沉淀）

- **H1 `_quote` 必须转义** `\\` `"` **和 `\n`/`\r`/`\t`**。Beat.text 里存的是
  **真的**换行符（解析时已还原转义），只转义前两类就会写出**物理换行**，
  一条 say 断成两行 → 产物不可解析。Ren'Py 里 `\n` 是写台词的常用手法。
  守卫：`tests/test_hell_findings.py::TestQuoteEscapesControlChars`。
- **H2 label 是硬边界**：换 label 时必须先把攒着的"孤儿指令段"落拍
  （`beat_build.add` 里的 `flush_stage_only()`），否则它们会被吸进**下一个 label**
  的第一张拍 → 本 label 名下一张拍都没有 → 渲染时**整个 label 消失**，
  渲染层还会退回 `label start:`（与游戏入口重名 → 游戏起不来）。
  真实项目实测 51 个 label 丢 4 个（含带 `call screen map` 的 `location`）。
  配套两处：`_fill_empty_labels`（有语句但不落拍）/ `_fill_labels_without_beats`
  （连语句都没有）补空拍；`Beat.is_empty` 把"只有 label 的空拍"当**结构锚点**
  （与 menu 锚点同理），否则它在 xlsx 往返里被当空行丢掉。
- **H3 反过来也不许凭空造 label**：源文件里没有 label 的**纯定义文件**，
  产物里不许出现 `label start:`（`render_beats_to_rpy` 末尾的兜底已收紧成
  "有真拍却一个 label 都没输出"才补）。真实项目 6 个文件中招过。
  **完整的契约是：有的必须留下来，没有的绝不能造。** 只做前一半会造出重名。

### I. 测试

- **I1 必须测"往返不变式"，不能只测半程**（曾 1731 测试全绿却漏掉 menu 100% 丢失）。
  任何"保存/同步"改动要断言 `原文 → 载体 → 原文'` 的**多重集相等**。
- **I2 变异验证是硬要求**：改回旧实现，测试必须变红。⚠ 且必须走**缺陷真正发生
  的那条路径** —— 曾写出"不走 Excel 往返"的空 label 用例，变异下**全绿**，
  因为 `is_empty` 只在 `_read_beats_from_ws` 里生效。**测试绿 ≠ 缺陷被钉住**。
  ⚠ **一次修了两件事，就要做两次变异**：曾同时修"清洗 + 留痕"，只验"关掉
  清洗"的话，"留痕"能悄悄退化而**测试全绿**（清洗照生效、产物照干净）。
  分辨方法：改回旧实现后是不是**该红的都红**了 —— 只红一半就是另一半没钉住。
- **I3 判据写错比缺陷更常见**：同一轮里判错过三次（`a.notes == b.notes`、
  "自称测增量其实只调了 compile"、把 `elif` 展开 / 资产引用去引号当成丢失）。
  **先怀疑判据**：拿原文 vs 产物逐行摆出来再下结论（`tools/probe_*.py`）。
- **I4 假阳性比漏报更贵**：三引号 docstring 会被"单行引号配平"判成语法坏
  （真实项目 14 条）；门禁把块体逐行丢给 `compile()` 曾让 `build` 对真实项目
  永远失败。**检查器本身也要能被真实项目跑通**。
- **I5 含中文的复现别用 `python -c`**（编码毁样本，会误判"解析器退化"）→ 写 .py。
- **I6 跑测试一律加 `--basetemp=C:/Users/28749/AppData/Local/Temp/xxx`**：
  `pytest.ini` 的 `addopts = --basetemp=.pip-tmp/pytest` 在仓库内，沙箱
  `safe-delete` 守卫会拦它的清理 → **所有带 `tmp_path` 的用例全部 ERROR**，
  而报错是 `assert not self._finalizers`（看不出真因）。

## 二、仓库与运行（稳定事实）

- 包：`storygraph/{core, excel, parsing, assets, story, pipeline, graph, ui, collab}`
- `story/`：beat_model（**加列**）/ beat_build（**构建拍**）/ beat_render（**生成
  代码**）/ excel_export / excel_import / generation_guard（**落盘**）/ paths；
  `script_table.py` 是兼容门面
- 入口：根 `main.py` / `cli.py` → `storygraph.main` / `storygraph.cli`
- 依赖只有 openpyxl（必需）+ Pillow（可选）；`legacy/` 不参与构建
- **静态体检入口 = `tools/health_check.py`**（规模/复杂度头部/最长模块/坏味道/
  依赖方向/死代码，10 秒出表）。动态：往返 `tools/audit_roundtrip.py`、
  刁钻 `torture_test.py`、变态 `brutal_test.py`、**组合面 `tools/hell_test.py`**、
  契约 `pytest`（全量 ~12 分钟，后台跑）。
- **交付就绪检查 = `tools/journey_check.py`**：subprocess 调 **CLI**（交付用的是
  命令），真实项目走 validate / build / table / compile + 产物可解析。
- ⚠ **`tools/audit_real_project.py` 是坏脚本，别信它的输出**（`AssetRegistry`
  早已没有 `rows` → 异常被吞 → 全线降级 → 假警报）。
  **甄别：两个脚本结论矛盾时先怀疑脚本。**
- CI：`.github/workflows/ci.yml`（3.10 / 3.12 / 3.13 + demo 体检）
- 真实项目验收对象：`D:\千连万理\RenPyTest\sr_general`（换机 skip）
- **label 计数有两个口径，别混**：`rglob('*.rpy')` = **51**（含
  `game/chapter/chapter1_4.generated.rpy` 里的 `chapter1_4_build`）；
  合并构建走 `discover_scripts`，**排除 `*.generated.rpy`** → **50**。
  差 1 不是丢失 —— 那个文件是工具上一轮的产物，本来就该排除。
- ⚠ **2026-10-05 出现过并发写入**：另一会话同时改 README /
  beat_model / beat_render / cli / patcher / parser。动手前先看
  `git status` + 文件时间戳；README 表格行补写会因冲突失败。
  两个会话结论不一致时以**最后一次全量运行**为准。
- **改 UI 必须用真实 tkinter + 真实数据冒烟**（`D:\Python\python.exe` 有 tkinter）

## 三、已知缺口

1. ~~P0 静默全量覆盖~~ 已修（B3）
2. **嵌套 ≥3 层：全量路径静默产出坏结构**（2026-10-05 实测修正 —— 此前记的
   "validate 会显式报错"**是错的**：对含 `if>menu>if` 与 `if>menu>if>menu` 的
   文件跑 `validate .`，**0 错 0 警**，报的全是"角色表空/资源未登记"）。
   病根不是"深度"，是**结构位不进表**：`_menu_member` 只活在 .rpy 侧，
   全量时只能靠 Condition 串 + 行号猜块边界 → 选项体变 `pass`、
   分支被提到 `menu:` 外面、缩进全错。
   ⚠ **增量路径"看起来对"是假象**：`改动 0 行` = 逐字节保留原文，
   没走模型。改过嵌套深处的拍会触"退回全量"兜底（未实测其后果）。
   分工不变：**树归 Graph 画布，句子级内容归 Excel**。
3. 解析层两个真 bug：`if` 内嵌 `menu` 整体降级；分支内 "show+say 混合" → 后续
   elif 分支消失。另：menu 两选项间插一拍 → 该拍被吸进 menu 组。
   ⚠ **延伸**（2026-10-05 地狱二轮实测）：**菜单套在选项体里再嵌一层菜单时，
   内层 `menu:` 被渲染成裸字符串 `" menu"`（`MENU_NO_CAPTION` 占位符漏进产物），
   内层选项体被抬到外层 → 玩家看不到选择、两条分支都执行**（静默改剧情）。
   最小复现 `tools/hell_nest_inline.py --only minimal_nested_menu -v`（8 行）。
   `if>menu>if>menu` 更有 40-56 处对拍冲突。真实项目最深 2 层、0 处嵌套菜单
   → 暂时打不到线上。**对拍器 `nest_placement_conflicts` 全部在报**（它是好的）。
3b. **条件滚雪球**（2026-10-05 地狱二轮实测）：`if>menu>if` 往返一轮之后
   `if (flag0) and (flag2):` 变成 `if (flag0) and ((flag0) and (flag2)):` ——
   真值表等价，但**逐轮膨胀**，且让增量对位永远失败（每轮都有一行"变了"）。
   渲染深处条件时应只写**增量条件**，不是整条累积链。
3c. **无资源表时图像名被合成**：`beat_render._emit_image_name(name, resolver)`
   在 `resolver is None` 时按旧行为"空格换下划线" → `scene bg bg1_1`
   变成 `scene bg_bg1_1`，而真实工程定义的是 `image bg bg1_1`（tag+属性），
   `bg_bg1_1` **全工程 0 命中**。真实项目 7 个文件命中。
   这是**有意写的降级**（代码注释写着"兼容单测与无资源表场景"），
   但后果是产物里出现不存在的图像名 —— 需要拍：无 resolver 时该不该改写。
4. 残留小洞：`with_` 跨相邻 show 泄漏、同拍重复舞台指令被去重、
   **行尾注释**（要加列才能存 = 新功能，别顺手塞进修 bug）。
   ⚠ 实测修正：它在**未改动的行**上一直活着（增量逐字节保留，不是真的
   被存下来了）；`test_incremental_export.test_inline_comment_survives`
   钉的正是"没改的行"。**全量路径必丢**，改过的那一行也丢。
5. **两套 .rpy 生成器并存**（`renpy_exporter` + `render_beats_to_rpy`）
   ——ownership 没显式化。方向：merge(Graph 骨架, Excel 内容, CodeBlock)，
   连接键已有 `LineId`。
6. 多语言链路不可用（`parse_translation_files` 不支持现代 `tl`）。
7. "程序改代码 → 表格自动反映" = 1.5s 轮询 + 全量重解析；
   `asset_engine._apply_rename_patch` 会正则改写用户的 .rpy。
8. Excel 前端上限：`Stage` 列无格级下拉；候选表整表重建；导出无增量。
9. 坏味道（用户练习课题）：`noqa: BLE001` 那批；两套生成器收敛（缺口 5）；
   上帝对象（`GraphCanvas`/`RenPyParser`/`AssetEngine`）。
   ~~`EditorSession` 拆分~~ 已完成（5 mixin），四个复杂度头部也已拆。
   ⚠ 复杂度/行数具体数字一律跑 `tools/health_check.py` 取。
10. **`Notes` 字段已定案删除**（选"删"）。底层理由（约束未来所有"不进产物的
    元数据"需求）："Notes 想不进产物" 与 A1 **根本冲突** —— 不进产物 → 从 .rpy
    重建必为空；要保真只能进 .rpy 注释 → 注定变成 `# 注释`。
    **所以"独立私人备注"在本架构下不存在**；既要又要只能做带标记注释
    （`# NOTE:` 前缀），那是新功能。

## 四、变更历史（倒序；细节见同目录 `YYYY-MM-DD.md`）

- **10-05**：组合面地狱测试 `tools/hell_test.py`（对抗台词 × 对抗结构 × 顶层块
  × 增量/全量两条路径）+ triage。从 54 处报警里分出**两个真 P0**：
  `_quote` 不转义控制字符（产物语法坏）、label 不是硬边界（真实项目丢 4 个 label）。
  修完 + 写 `tests/test_hell_findings.py`（13 条，含真实项目 2 条）并做变异验证。
  判据教训见 I2/I3/I4。
- **10-04**：P3-12 收口后通用检查；新增 `tools/health_check.py`；发现
  `audit_real_project.py` 是坏脚本；`Notes` 定案；交付评审（三条边界）。
- **10-03**：三轮严苛测评；修 menu 丢失 / condition 不落表；补往返不变式 +
  变异测试；抓出 P0 静默全量覆盖（交红测试给本人修）。
- **09-30**：以"逐句校对产物必须逐字节不变"为体检项，堵掉 5 条静默改数据通道；
  卡片交互重构。
- **09-29 / 09-28 / 09-27**：卡片改版（网格卡片 + chip + 异步缩略图）；
  `script_table` 拆 7 模块；扁平模块 → 分层包；三层架构 + 舞台快照。

## 五、协作方式（用户本人确认，影响后续所有协作）

- **单人开发**：代码**主要由 AI 写**，用户负责**设计、测试、验收**
- 目标不是急着交付，而是**积累经验 + 学会改进 AI 写的代码**
- 因此：多解释**"为什么这么设计 / 权衡了什么 / 否决了哪个备选"**；
  他要的是**决策过程**；他写代码时**不要抢着给答案**
- 验收一律靠真实项目 + 全量测试，**让数据打脸**
- 练习要落在"关掉 AI 自己写一版"，而不是读代码
