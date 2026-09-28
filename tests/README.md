# RenPy 剧情树可视化编辑器 — 测试集合

## 运行

```bash
# 依赖：pytest + openpyxl
pip install pytest openpyxl

# 项目根目录执行
python -m pytest            # 全部用例（500 条，约 8 秒）
python -m pytest tests/test_renpy_parser.py -v
```

`pytest.ini` 已把 `SyntaxWarning` 提升为失败——源码里若再写出非法转义会直接红灯。

`tests/conftest.py` 负责两件事：

1. 把项目根目录加入 `sys.path`（源码是扁平导入，不是包结构）
2. 在无 GUI 环境（无 tkinter）下注入 tkinter 桩，使 `commands` / `renpy_exporter` /
   `asset_panel` 等模块可以正常导入

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
├─ ui/                      tkinter 层（graph_view / beat_editor / asset_panel）
└─ collab/collab_store.py   SQLite 协作后端（行锁/冲突/状态机/审计）

legacy/                     归档的单文件老版本（不参与构建）
```

数据流：

```
.rpy ──parse──▶ AST ──▶ ParseResult(Statement 流) ──┬──▶ Graph ──▶ layout/analysis/export
                                                    └──▶ Beat ──▶ Excel ⇄ DSL 编译 ──▶ .rpy
.xlsx ──parse──▶ AssetRegistry ──diff──▶ 变更 ──补丁──▶ .rpy
```

---

## 测试文件与职责

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| `test_core.py` | 32 | Statement 哈希/相等/序列化/校验、uid 拼接 |
| `test_utils.py` | 14 | 变量提取规则、图级变量收集 |
| `test_models_commands.py` | 20 | Graph 增删改、Command 撤销/重做、历史栈上限 |
| `test_renpy_parser.py` | 92 | Lexer、各类语句、show/scene、GraphBuilder、翻译单元、增量缓存、tl 文件 |
| `test_analysis.py` | 64 | 区间运算、条件求解、可达性、循环收敛 |
| `test_layout.py` | 12 | 空图/环/自环/断连图不崩且全覆盖 |
| `test_exporter.py` | 15 | 控制流生成：菜单、条件、python 块、缺目标 |
| `test_asset_registry.py` | 35 | 扫描分类、Excel 增量合并、解析校验、引用行号 |
| `test_asset_events.py` | 26 | 事件总线容错、稳定性检查、强制提交、确认闸门 |
| `test_asset_extensions.py` | 60 | 快照、变更追踪、依赖分析、补全、面板模型、工作区 |
| `test_asset_engine.py` | 48 | 路径判定、脏标记路由、poll、查询、重命名补丁与回滚 |
| `test_character_linker.py` | 23 | 未定义/未使用角色、翻译完成度、Excel 行导出、diff |
| `test_ui_models.py` | 36 | 面板数据源、上下文感知补全 |
| `test_roundtrip.py` | 10 | `.rpy → Graph → .rpy` 与 `.rpy ↔ Excel` 往返一致性 |

---

## 发现并修复的缺陷

### P0 — 分析会卡死 UI

`analysis.analyze_reachability` 在有循环的图上**永不终止**。

`x += 1` 的回边让 `entry_states` 每次迭代都抬高上界（0→1→2→…），
工作列表算法没有收敛机制。剧本里出现「好感度循环」这类常见写法时，
点击「分析」会直接卡死。

修复：加入 widening（加宽）——每个节点入口状态更新超过 4 次后，
把本轮变大的一侧直接推到 ±∞。同时加了迭代上限兜底。

### P0 — 不可达节点检测整体失效

可达性判断复用了 `VariableState.is_bottom()`，而后者:

1. 用 `all()` 判断——只有一个变量被约束到空集时仍算可达
2. 图中没有任何变量时 `ranges` 为空，`all()` 返回 False 却仍被当成可达

结果是：**脚本里不含 `$` 变量的图，所有孤立节点都不会被报告**。

修复：`is_bottom` 改为 `any()` 语义；可达性改为独立的 `reached` 集合记账，
不再从区间是否为空反推。

另外修了一处会连带失效的工作列表 bug：只有「区间发生变化」才继续传播，
导致无变量图在起点就停下——首次到达必须无条件入队。

### P1 — Excel 里改角色名，脚本不跟随

这条链路原本完全不通。`_handle_table_changed` 的顺序是
「先同步脚本 define → 再 diff」，于是：

- 脚本里的旧 `define e` 被塞回 registry
- Excel 端的新 ID `eileen` 看起来只是「新增」
- 旧 ID 看起来「没变」
- 结果：registry 里新旧共存，`e "hi"` 一句都没改

而且 `characters.xlsx` 没有 `Path` 列，重命名识别依赖路径的那套逻辑对角色表
完全用不上。

修复：

- 把 diff 提前到同步之前
- 新增 `_infer_character_renames`：对无 Path 的角色表，用 `Name/Color/ImageTag/WhatColor`
  这些身份字段推断「哪个消失的 ID 变成了哪个新 ID」，歧义时用编辑距离取最近者
- 补丁 → `_load_scripts` → 再同步，此时脚本已是新 ID，不会冲突

### P1 — 资产 ID 前缀重复

`scan_assets` 无条件拼接前缀：`bgm_theme.ogg` → `bgm_bgm_theme`。
同样 `sfx_click.wav` → `sfx_sfx_click`。
用户的素材明明已经按 `bgm_` 命名，生成的表里却出现双前缀，
跟脚本里写的引用对不上。

修复：文件名已经带前缀时不再重复添加。

### P1 — 语法错误的语句 «静悄悄»

`apply_operation` 遇到无法解析的语句时，靠 `ast` 提取变量名——但既然
`ast.parse` 都失败了，提取结果必然是空集，于是原状态原样返回。
后面拿着过期区间去断言「某分支不可达」，给出的是错误结论。

修复：兜底用正则粗提取标识符，把涉及的变量一律置为 TOP。

### P2 — folder 分组跨平台不一致

`AssetPanelModel.get_tree(group_by='folder')` 和 `AssetPanelData.get_tree`
都用 `str(Path(p).parent)`，Windows 上得到 `images\bg`、Linux 上得到 `images/bg`。
同一份资产表在不同部门（50 人团队必然跨平台）会长出不同的树。

修复：改用 `PurePosixPath`。

### P2 — 分支判断写错了对象

`IncrementalRenPyAnalyzer.analyze` 里 `elif Graph is not None:` 检查的是
**Graph 类能否导入**，实际想判断的是「调用方有没有传 graph」。
两者在正常路径下巧合等价，但 `models` 导入失败时会返回 `(result, None)`。

修复：改为 `if graph is None:`。

### P2 — 模块 docstring 触发 SyntaxWarning

`renpy_parser.py` 的模块 docstring 里有 `r'[^\w]'`（非 raw docstring），
每次导入都会报 `SyntaxWarning: invalid escape sequence`。

修复：docstring 改为 raw string，并在 `pytest.ini` 把 SyntaxWarning 提升为失败。

---

## 已知问题（未修，建议后续处理）

1. **image tag 与资产 ID 语义不匹配，导致「未使用资产」100% 误报。**
   脚本里写 `show bg room`（tag=bg, attr=room），而 `analyze_references` 抽取的是
   `bg`，Excel 里的 ID 却是 `bg_room`。两边对不上，于是每张图都会被判为「未被引用」。
   建议：建立 tag ↔ asset ID 的映射规则，或让 ID 生成遵循 Ren'Py 的 image 命名约定。

2. **现代 Ren'Py 翻译文件格式不支持。**
   `parse_translation_files` 只认 `# old "x"` / `new "y"` 这种旧格式。
   Ren'Py 7.4+ / 8.x 的 tl 文件长这样：

   ```
   translate chinese start_abc123:
       # script.rpy:10
       e "翻译文本"
   ```

   没有 old/new 对，当前解析器一个翻译单元都取不到，多语言功能形同虚设。

3. **同一份逻辑存在两套实现。**
   `asset_completion.AssetCompletion` 与 `asset_extensions.AssetCompletionProvider`
   是两个几乎一样的补全器；`asset_panel.AssetPanelData` 与
   `asset_extensions.AssetPanelModel` 也一样（且行为已经不一致：前者把角色按活跃度
   分组，后者没有）。建议收敛成一个，UI 层只做适配。

4. **`behind` 多目标解析丢失分组信息。**
   `show e behind bg a, bg b` 会被扁平化成 `['bg','a','bg','b']`，
   与 `show e behind bg a bg b` 无法区分。多词 image name 的场景会错。

5. **`AssetWorkspace.load()` 没传 `project_root` 给 `load_hooks`**，
   与 `AssetEngine._initial_load()` 的钩子查找路径不一致。

6. **`Graph.add_node` 直接保存调用方传入的 operations 列表引用**，
   外部改动会穿透到节点内部。当前测试已固化该行为，若要修需一并改调用点。
