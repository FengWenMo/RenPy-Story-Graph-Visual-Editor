# RenPy 剧情树可视化编辑器 — 测试集合

## 运行

```bash
# 依赖：pytest + openpyxl
pip install pytest openpyxl
# 项目根目录执行
python -m pytest            # 全部用例（1732 条，约 53 秒）
python -m pytest tests/test_renpy_parser.py -v
```
`pytest.ini` 已把 `SyntaxWarning` 提升为失败——源码里若再写出非法转义会直接红灯。`tests/conftest.py`
负责：
1. 把项目根目录与 `tests/` 加入 `sys.path`
2. 提供 `notify_calls`（捕获后端通知端口的断言夹具）
3. 提供 Qt 夹具 `qapp` / `ui_project` / `ui_window`（offscreen 跑真 Qt）

> **tkinter 桩已删除（2026-10-01）**：以前这里塞了 80 行 tkinter 桩，
> 因为后端（`commands` / `renpy_exporter`）为了弹一个提示框而 `import tkinter`，
> 无头环境连 import 都过不去。现在后端只走 `storygraph.app.notify`，
> 界面只剩 `ui_qt/`（PySide6），桩没有存在意义。
> **故意不留**"以防万一"的兜底：真有人再往核心里塞 UI 依赖，那里就该当场炸。

---

## 项目结构梳理

> **2026-09-28 变更**：原先 33 个模块**扁平铺在项目根**，已装进
> `storygraph/` 包并按功能分层。测试里凡是要"扫源码"的地方，
> 一律走 `tests/_source_paths.py` 的 `module_source('storygraph.x.y')`，
> 不要再硬编码相对路径（当天就有 12 个用例因此失效）。

```
main.py / cli.py            根目录垫片（真实实现在 storygraph/ 里，逻辑一份）
storygraph/
├─ core/                    零依赖地基
│   statement.py            Statement IR（.rpy ↔ Excel 中枢）
│   uid.py                  翻译单元 uid 拼接唯一真源
│   vocabulary.py           共享词表唯一真源（资产类型/非角色 tag/内置变换）
│   utils.py                变量提取（AST）
│
├─ excel/
│   cells.py                安全写单元格（`=` 开头当文本，别被 Excel 当公式）
│
├─ parsing/
│   renpy_parser.py         Lexer → AST → ParseResult(Statement 流) / 翻译单元
│   discover.py             扫出该被构建的 .rpy（门禁与构建共用）
│   character_script_linker.py  角色 ↔ 剧本 ↔ 翻译 三层索引
│
├─ assets/
│   asset_registry.py       扫描 / 资源表读写 / 引用判定唯一真源
│   asset_events.py         事件总线 + 文件监听 + 确认闸门
│   asset_engine.py         编排层：脏标记 → poll → 变更 → 脚本补丁
│   asset_extensions.py     快照 / 变更追踪 / 依赖分析 / 重命名补丁 / 补全(缓存版)
│   asset_completion.py     上下文感知补全（实时版；打分排序与上面共用）
│
├─ story/                   （原 3.8k 行的 script_table 按职责拆成 7 个模块）
│   beat_model.py           Beat 数据结构 + 列契约 + 单元格编解码
│   beat_build.py           .rpy / DSL → Beat
│   beat_render.py          Beat → Ren'Py 代码
│   excel_export.py         Beat → Excel（主表 + 派生视图 + 图库/缩略图）
│   excel_import.py         Excel → Beat
│   generation_guard.py     生成安全门禁 + 落盘
│   paths.py                项目路径判定（门禁与缩略图共用）
│   script_table.py         **兼容门面**（re-export，老 import 不用改）
│   dsl.py                  单元格人话 ⇄ Ren'Py 代码（唯一翻译层）
│   stage_snapshot.py       舞台快照 / 演出时间轴（派生视图）
│   story_ops.py            剧情变量 vs 引擎代码
│
├─ pipeline/
│   build.py                体检（collect_report）/ 构建（build_project）
│   validate.py             三级校验（L1 单条 / L2 表内 / L3 全库）
│   compile.py              演出表 → .rpy + 编译结果表（同源）
│   engine_exporters.py     中立 IR → Unity / Godot / CSV
│   renpy_exporter.py       Graph → .rpy（保真，不过滤）
│
├─ graph/                   Node/Edge/Graph、Command、布局、区间分析
├─ app/                     应用服务层（框架无关）：beats / stage / editor /
notify /
│                           picker / thumbs / asset_panel / tables
├─ ui_qt/                   PySide6 界面（唯一前端；tkinter 版已删除）
└─ collab/collab_store.py   SQLite 协作后端（行锁/冲突/状态机/审计）legacy/
归档的单文件老版本（不参与构建）
```数据流：

```
.rpy ──parse──▶ AST ──▶ ParseResult(Statement 流) ──┬──▶ Graph ──▶
layout/analysis/export                                                    └──▶
Beat ──▶ Excel ⇄ DSL 编译 ──▶ .rpy.xlsx ──parse──▶ AssetRegistry ──diff──▶
变更 ──补丁──▶ .rpy
```

---

## 测试文件与职责

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| `test_core.py` | 32 | Statement 哈希/相等/序列化/校验、uid 拼接 |
| `test_utils.py` | 23 | 变量提取规则、图级变量收集、**`declared_variables`（只认被赋值的名字：引擎模块 / 循环临时变量不许写 `default`）** |
| `test_models_commands.py` | 20 | Graph 增删改、Command 撤销/重做、历史栈上限 |
| `test_renpy_parser.py` | 92 | Lexer、各类语句、show/scene、GraphBuilder、翻译单元、增量缓存、tl 文件 |
| `test_analysis.py` | 64 | 区间运算、条件求解、可达性、循环收敛 |
| `test_layout.py` | 16 | 空图/环/自环/断连图不崩且全覆盖；**间距由调用方给**、自环不许推偏节点 |
| `test_exporter.py` | 18 | 控制流生成：菜单、条件、python 块、缺目标 |
| `test_asset_registry.py` | 35 | 扫描分类、Excel 增量合并、解析校验、引用行号 |
| `test_asset_events.py` | 26 | 事件总线容错、稳定性检查、强制提交、确认闸门 |
| `test_asset_extensions.py` | 60 | 快照、变更追踪、依赖分析、补全、面板模型、工作区 |
| `test_asset_engine.py` | 48 | 路径判定、脏标记路由、poll、查询、重命名补丁与回滚 |
| `test_character_linker.py` | 23 | 未定义/未使用角色、翻译完成度、Excel 行导出、diff |
| `test_ui_models.py` | 36 | 面板数据源、上下文感知补全 |
| `test_graph_canvas.py` | 46 | **剧情结构图**：**旧版操作（真鼠标事件）**——Shift+左键拖连线 / 左键选中拖动 / 双击=剧情变量 / 滚轮缩放 / **中键平移**；间距按节点尺寸、任意两块不相交、**端点取块中心 + 箭头在中点 + 直线**、连线语义标签、自环、累加导入后重排、**块上不出现技术代码**、**重建保住缩放**、**手势可被打断（失活不卡状态）**、**无限舞台（连拖不撞墙 / 撑大不跳屏 / 缩小后也能拖）** |
| `test_app_layer.py` | 85 | `app.thumbs` / `app.asset_panel` / `app.tables` / **保存撤销** / **结构分析三类分类** / **剧情图的范围** / **舞台摘要（含 CG 图层）+ 缺章提示（两个方向）** / **缩略图解码："先粗后精"不放大不破比例 + 磁盘缓存命中** |
| `test_ui_qt.py` | 184 | Qt 主窗口：两页导航、剧情图范围、控制台反馈、右键菜单、变量只改变量、**图编辑动作真的生效（P0 回归）**、**资产选取（provider / 候选 / 没资源表时说清）**、**插入·删除·恢复卡片（插入的产物真的在当前拍之后 / 两个按钮结果不同）**、真按键、卡片保存撤销、**逐条位置往返不许被吃掉（`ye happy@i11; ye care`）**、**选择器格子可点/可见项全加载/窗口内优先（含"早就排过队"那个坑）**、**空格平移不看焦点脸色（只读控制台 / 失活 / 中键）**、**连线与撤销后缩放位置不变**、**相邻拍预取**、**预览：加载中是低调占位（不是亮蓝框）/ 缺图与缺音频写在横条上 / 下一两拍预取且不插队**、**卡片缩略图也认脚本里的别名** |
| `test_ui_cards.py` | 67 | 立绘分组/chip、token、**缩略图：CG 目录约定兜底 / 加载中可见（骨架不说"坏了"）/ 异步结果不串（key 核对）**、**首请求立刻开工 / 限流 / 优先级（窗口内最先、预取最后）/ 预取填缓存后 0 延迟**、**`clear_pending(keep=…)` 只丢旧帧、预取留着**、**别的 slot 解好了就直接回填（同一张图不解两遍）**、**位置显示（继承 i11）**、解析不跨角色串图 |
| `test_atl.py` | 22 | **transform → 舞台真实位置**：链式调用（`f21: focus(320)`）与参数绑定、`on show:` 事件块不算、整数=像素/小数=比例、表达式白名单求值（不许跑项目代码）、走不到就老实退回启发式 |
| `test_stage_preview.py` | 43 | 预览语义同源（帧 = `snapshot_rows`）、说话人高亮、素材解析（表里没有但磁盘上有 / 纯色不算缺图）、**脚本 `image` 声明（`bg bg11_0` 这种别名只写在脚本里 / 注释与缩进块不算 / 纯色声明 / 按项目缓存）**、**立绘真实几何 `sprite_box`（原图 × zoom + 锚点偏移）** |
| `test_stage_audio.py` | 27 | 预览的声音：素材名 → 真实文件（含"表里带项目目录前缀"与目录兜底）、**缺文件不许静默：公共前缀近似匹配 + 标出真名（并列第一不猜 / 同名副本与 mp3+wav 算同一首 / 严格模式不近似）**、BGM 持续 / 音效语音一次性、同一帧重画不重喊、关声音停干净 |
| `test_pipeline_batch.py` | 102 | 两条构图路径同源、舞台快照、多引擎导出、三级校验、协作、**分支条件逻辑（elif 排除前分支 / menu 选项文字与条件分开 / 区间分析验证分支）** |
| `test_graph_statements.py` | 12 |
| `test_beat_ranges.py` | 7 | **拍的行区间**（增量导出的地基）：区间不许交叠、每句台词必须在自己区间里、`extend` 带走它前面的`show/transition/注释`、ppt 段（scene 撞 scene）的行号归属、真实项目全章扫描（`STORYGRAPH_REAL_PROJECT`） | **结构债迁移**：`statements`（结构化真源）与 `operations`（兼容视图）**逐字节往返**（紧凑 `$x` / 多行 / 空串 / Unicode）、视图是新列表（`append` 会静默失效 → AST 守卫）、出处与条件留得住、变量保存不吃技术代码出处、老布局 `.json` 照旧能读 |
| `test_patch_grow_shrink.py` | 16 | **增量补丁增删整行**（清掉 `show` 要跟着删、控制流卡新填台词要插得进去、代码行改写）：三条准入线（平整区间 / 单调 / 身份不重复）、块结构里永不增删、`# TODO: 缺少资源` 不许顶掉作者的行、单引号台词不许插成两遍、**不动必须逐字节相同**、**产物是不动点** |
| `test_code_health.py` | 10 | **审计守卫**：全项目不许再有 `except …: pass`（唯一豁免 `safeguard.py` 的记录失败路径）、`swallow()` 真的记下来了（context/次数/详情/报告/有界）、**两个 .rpy 生成器共用规则不许再分叉**（op 渲染 / `default` 名单 / 标记只有一处） |
| `test_entry_points.py` | 22 | 入口可达性：悬空回调、`act(...)` 文案与实现、**两条路分开**（资源表 vs 剧情图）、启动不再扫全项目 |
| `test_roundtrip.py` | 10 | `.rpy → Graph → .rpy` 与 `.rpy ↔ Excel` 往返一致性 |

---

## 发现并修复的缺陷

### P0 — 分析会卡死 UI

`analysis.analyze_reachability` 在有循环的图上**永不终止**。`x += 1` 的回边让
`entry_states` 每次迭代都抬高上界（0→1→2→…），工作列表算法没有收敛机制。剧本里出现「好感度循环」这类常见写法时，点击「分析」会直接卡死。修复：加入
widening（加宽）——每个节点入口状态更新超过 4 次后，把本轮变大的一侧直接推到
±∞。同时加了迭代上限兜底。

### P0 — 不可达节点检测整体失效

可达性判断复用了 `VariableState.is_bottom()`，而后者:
1. 用 `all()` 判断——只有一个变量被约束到空集时仍算可达
2. 图中没有任何变量时 `ranges` 为空，`all()` 返回 False 却仍被当成可达结果是：**脚本里不含
`$` 变量的图，所有孤立节点都不会被报告**。修复：`is_bottom` 改为 `any()` 语义；可达性改为独立的
`reached` 集合记账，不再从区间是否为空反推。另外修了一处会连带失效的工作列表 bug：只有「区间发生变化」才继续传播，导致无变量图在起点就停下——首次到达必须无条件入队。

### P1 — Excel 里改角色名，脚本不跟随

这条链路原本完全不通。`_handle_table_changed` 的顺序是「先同步脚本 define → 再
diff」，于是：
- 脚本里的旧 `define e` 被塞回 registry
- Excel 端的新 ID `eileen` 看起来只是「新增」
- 旧 ID 看起来「没变」
- 结果：registry 里新旧共存，`e "hi"` 一句都没改而且 `characters.xlsx` 没有 `Path`
列，重命名识别依赖路径的那套逻辑对角色表完全用不上。修复：
- 把 diff 提前到同步之前
- 新增 `_infer_character_renames`：对无 Path 的角色表，用 `Name/Color/ImageTag/WhatColor`
这些身份字段推断「哪个消失的 ID 变成了哪个新 ID」，歧义时用编辑距离取最近者
- 补丁 → `_load_scripts` → 再同步，此时脚本已是新 ID，不会冲突

### P1 — 资产 ID 前缀重复

`scan_assets` 无条件拼接前缀：`bgm_theme.ogg` → `bgm_bgm_theme`。同样 `sfx_click.wav`
→ `sfx_sfx_click`。用户的素材明明已经按 `bgm_` 命名，生成的表里却出现双前缀，跟脚本里写的引用对不上。修复：文件名已经带前缀时不再重复添加。

### P1 — 语法错误的语句 «静悄悄»

`apply_operation` 遇到无法解析的语句时，靠 `ast` 提取变量名——但既然`ast.parse`
都失败了，提取结果必然是空集，于是原状态原样返回。后面拿着过期区间去断言「某分支不可达」，给出的是错误结论。修复：兜底用正则粗提取标识符，把涉及的变量一律置为
TOP。

### P2 — folder 分组跨平台不一致

`AssetPanelModel.get_tree(group_by='folder')` 和 `AssetPanelData.get_tree`都用
`str(Path(p).parent)`，Windows 上得到 `images\bg`、Linux 上得到 `images/bg`。同一份资产表在不同部门（50
人团队必然跨平台）会长出不同的树。修复：改用 `PurePosixPath`。

### P2 — 分支判断写错了对象

`IncrementalRenPyAnalyzer.analyze` 里 `elif Graph is not None:` 检查的是**Graph
类能否导入**，实际想判断的是「调用方有没有传 graph」。两者在正常路径下巧合等价，但
`models` 导入失败时会返回 `(result, None)`。修复：改为 `if graph is None:`。

### P2 — 模块 docstring 触发 SyntaxWarning

`renpy_parser.py` 的模块 docstring 里有 `r'[^\w]'`（非 raw docstring），每次导入都会报
`SyntaxWarning: invalid escape sequence`。修复：docstring 改为 raw string，并在
`pytest.ini` 把 SyntaxWarning 提升为失败。

---

## 已知问题（未修，建议后续处理）

1. **image tag 与资产 ID 语义不匹配，导致「未使用资产」100% 误报。**   脚本里写
`show bg room`（tag=bg, attr=room），而 `analyze_references` 抽取的是   `bg`，Excel
里的 ID 却是 `bg_room`。两边对不上，于是每张图都会被判为「未被引用」。   建议：建立
tag ↔ asset ID 的映射规则，或让 ID 生成遵循 Ren'Py 的 image 命名约定。
2. **现代 Ren'Py 翻译文件格式不支持。**   `parse_translation_files` 只认 `# old "x"`
/ `new "y"` 这种旧格式。   Ren'Py 7.4+ / 8.x 的 tl 文件长这样：   ```   translate chinese start_abc123:       # script.rpy:10       e "翻译文本"   ```
没有 old/new 对，当前解析器一个翻译单元都取不到，多语言功能形同虚设。
3. **同一份逻辑存在两套实现。**   `asset_completion.AssetCompletion` 与 `asset_extensions.AssetCompletionProvider`
  是两个几乎一样的补全器；`asset_panel.AssetPanelData` 与   `asset_extensions.AssetPanelModel`
也一样（且行为已经不一致：前者把角色按活跃度   分组，后者没有）。**2026-10-01
部分收敛**：`AssetPanelData` 已从 `ui/` 搬到   `app/asset_panel.py`（框架无关），但两个实现仍在，建议继续合并。
4. **`behind` 多目标解析丢失分组信息。**   `show e behind bg a, bg b` 会被扁平化成
`['bg','a','bg','b']`，   与 `show e behind bg a bg b` 无法区分。多词 image name
的场景会错。
5. **`AssetWorkspace.load()` 没传 `project_root` 给 `load_hooks`**，   与 `AssetEngine._initial_load()`
的钩子查找路径不一致。
6. ~~**`Graph.add_node` 直接保存调用方传入的 operations 列表引用**~~ **已修（2026-10-05 结构债迁移）**：真源改成结构化语句表，`operations` 只是派生视图，调用方那个 list 与节点不再共享。
