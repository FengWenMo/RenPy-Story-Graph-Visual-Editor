# 项目长期记忆：RenPy 剧情树可视化编辑器

## 交付目标（项目所有者设定）

小团队（约 50 人：文案 / 策划 / 美术 / 音乐 / 程序）用的 Ren'Py 演出编排工具：

- 用户**不写代码**，对着 Excel 就能精确到句子级配置演出（图片、音效、角色、位置变换）
- 表格之间相互引用联动
- 程序那边敲代码要能自动反映到表格 —— **Excel ↔ .rpy 双向联通**
- 需要支持自定义 transform 变换
- 要考虑多语言复用（未来可能迁 Unity），保持工业化能力

## 架构约定

- 源码是**扁平导入**，不是包结构；`core/` 是唯一子包且不反向依赖项目模块
- **活跃入口 = `main.py` + `graph_view.py`**；`RenPy_Story_Graph_Visual_Editor.py`
  是遗留单文件版（自带 GraphView/菜单、无资产与剧本表集成），改 UI 别改错文件
- 运行方式：项目根目录执行 `python -m pytest`（774 用例 / ~22 秒）；
  CLI 入口 `python cli.py validate|build|table|collab`
- 管线分层（务必保持单向）：`ParseResult(Statement 流)` 是唯一中间层 →
  图（GraphBuilder.build_from_parse_result）与剧本表（beats_from_statements）
  都从它派生；禁止再写第二套 AST walk
- 派生视图都放在 Excel 里做成**独立工作表**（舞台快照 / Candidates_* /
  协作信息），Beats 表只放用户要编辑的东西
- Pillow 是**可选**依赖（已装进隔离 venv）：只影响 jpg/webp 预览，
  PNG/GIF 走 tk.PhotoImage 也能显示，代码必须有降级路径
- `pytest.ini` 把 SyntaxWarning 升级为失败
- 统一 IR：`core.Statement`（含 `statement_labels` 平行数组）是 .rpy↔Excel 中枢；
  `script_table.beats_from_statements` 把语句流折叠成 Beat

## 关键不变量（改动前先想清楚）

- `core.statement._compute_content_hash` 的输出必须与
  `renpy_parser.TranslationUnitGenerator` 逐字节一致（menu_option 用
  前缀 `'menu'` 是历史兼容，不是笔误）
- `core/uid.py` 是 uid 拼接的唯一真源，禁止各处自己拼 f-string
- `GraphBuilder.build(clear_graph=True)` 会删除不在本次 AST 里的 label 节点；
  部分文件重解必须传 `clear_graph=False`
- `Statement.__hash__` 必须与 `__eq__` 同源（递归 `_freeze(payload)`），**不能**
  用 content_hash 当哈希键：show/scene 的 content_hash 恒为空，会把不同 show
  判成同哈希。`validate_hash_contract()` 自检工厂字段覆盖
- `script_table.BEAT_FIELDS` 是 Excel 列顺序唯一真源；`Beat.to_row/from_row`
  必须逐列对应（加列要同改三处：字段声明、to_row、from_row）
- Beat 的"保真三列" `Comment` / `Raw` / `CodeBlock` 必须原样回写——这是不丢信息的关键
- `StageOrder` 记录舞台指令源顺序；`hide X`+`show X`（重置立绘）与反序语义相反，
  渲染必须按 StageOrder，不能按固定 Scene→Show→Hide
- **资产引用判定唯一真源 = `asset_registry.resolve_references`**（多值返回；
  图片走别名/前缀，音频剥 `bgm_`/`sfx_`/`v_` 前缀与路径 stem）。
  `analyze_references` / `analyze_statement_references` /
  `find_unregistered_assets` 都走它；不要再各处自写"空格↔下划线归一 + 白名单"
- 候选表结构固定：第 1 行来源说明 / 第 2 行 `ID | 说明 | 参数·路径` /
  第 3 行起候选；**下拉区间永远是 A 列 `$A$3:$A$N`**，加说明列不能动 A 列
- `LogicalLine.text` **含行首缩进**；还原块体源码要 `text[indent:]` 再补相对缩进
- 脚本注入的 transform / 角色（`_from_script`）不参与"被删除"确认弹窗
- `AssetEngine._sync_transform_definitions` 把脚本 transform 合并进
  `registry.transforms`（同名已登记的不覆盖），这是"表格里能选到自定义变换"的前提
- `Beat.LineId`（`label.序号`）是**位置稳定**的协作主键；`ContentHash`
  每次都要**按当前内容重算**——沿用表里带来的旧值会让 push 把"改了内容"
  判成"未变"而静默丢改动
- `render_transform_definitions` 必须先去掉 ATL 的**公共缩进**再统一缩进 4；
  直接 `.strip()` 会吃掉首行缩进、生成语法错误的 transform
- 生成脚本时图像名**保留作者写法**（`show eileen happy` 不要改成
  `eileen_happy`）：有资源表时以"能否解析"为准（`_emit_image_name`）；
  无资源表才退回下划线形态
- 舞台快照：同 tag 的 `show` 是换表情（原地替换）不是叠人；bg/cg/ui 等
  非角色 tag 不算立绘；顺序一律按 StageOrder
- 校验分级要克制：某类资产**已有资源表**而引用不在其中 → 错误；
  项目压根没建这类表 → 警告（否则新项目 build 直接失败）
- `cli.py` 退出码约定：0 通过 / 1 有错误 / 2 用法或环境问题（CI 依赖它）

## 已修坑（别再踩回去）

- `analysis.analyze_reachability` 必须有 widening，否则带循环的图不终止
- 可达性不能从区间是否为空反推（无变量图会全误判），要用独立 reached 集合
- `_handle_table_changed` 必须**先 diff 再同步脚本角色定义**，否则 Excel 改名被掩盖
- 音频 ID 前缀不能重复拼接
- Excel 路径类 key 统一用 posix 分隔符（团队跨平台）
- `add_asset_to_registry` 查文件名必须用 registry 属性名 `_STANDARD_TABLES.get(attr)`
  而非 `get(asset_type)`，否则 `voice`（单数）查不到 `voices.xlsx`；追加行按文件
  真实表头对齐
- 音频引用含 `/` 或非 ASCII 必须加引号，否则 `play music bgm/夜晚.mp3` 非法
- 解析器对 label 内未建模的**叶**语句（`with`/`pause`/`window hide`/`call screen`/
  动态 jump）一律产 `RawNode` 保真；带子块的未建模语句跳过（避免只留头丢体）
- 音频分类不能只看路径关键词：`game/audio/night.ogg` 会被判成音效 →
  Music 下拉为空。现按 目录语义 > 文件名线索（音效线索优先）> 兜底 music；
  音频/语音目录探测必须带上 `game/` 前缀（`game/voice/`、`game/music/`）
- 图片 tag 必须在扫描时算出来：`images/eileen/happy.png` 的 tag = `eileen`，
  背景/CG/UI 才是固定 `bg`/`cg`/`ui`。tag 为空 → 脚本 `show eileen happy`
  与资产 ID `happy` 对不上，整库立绘误报"未使用"
- 正则兜底路径曾**完全没抽 `voice`** → 语音资产永远显示未使用；`_RE_VOICE_LINE`
  要同时收字符串路径与裸变量（`voice v_001`）

## 待办优先级建议

1. 音频"引用名 vs 内部 ID"分离：扫描器给的 `bgm_night` 不是合法 Ren'Py 引用，
   选中后生成 `play music bgm_night` 需要项目另定义 audio 别名。候选表第 3 列
   已带真实 Path 供人工判断，彻底解决要拆两列
2. 支持现代 Ren'Py tl 翻译格式（当前多语言链路不可用）；行级 LineId 已就位，
   可直接对接翻译表
3. 收敛重复的补全器 / 面板模型实现
4. `collab_store` 的资产表目前只是单向 upsert，需要"资源表 ↔ DB"双向
   编辑与锁（本轮只见了 `upsert_assets`）
5. Excel 动态下拉在 WPS 上的兼容性实测（已做写名失败的回退）
6. 多引擎导出的 Unity/Godot 侧**真机验证**（当前只保证 JSON 契约与文档）

（已完成：image tag ↔ 资产 ID 映射；统一资产引用抽取；一.1 图吃 Statement；
二 舞台快照 + 动态下拉；五 多引擎导出器；七 三级校验 + build + CLI；
八 协作后端 SQLite + Excel 前端）
