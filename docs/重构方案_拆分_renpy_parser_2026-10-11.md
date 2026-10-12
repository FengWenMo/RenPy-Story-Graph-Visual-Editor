# 重构方案：拆分两个大模块（`renpy_parser.py` / `asset_registry.py`）

> `tools/health_check.py` 长期报"最大模块 > 800 行 / 最大类 > 40 方法"。
> 这份文档把**已做的、量出来的、下一步怎么做**钉死，
> 让后续每次都是**机械劳动**而不是重新设计。
>
> **进度一览（2026-10-11）**
>
> | 目标 | 之前 | 现在 | 状态 |
> |---|---|---|---|
> | `renpy_parser.py` | 4422 行 | **3444 行** | ✅ 第①②步做完 |
> | `RenPyParser` 类 | 50 方法（榜首） | 不在前三 | ✅ |
> | `asset_registry.py` | 3614 行 | **3512 行** | 🟡 第①步（常量层）做完 |
> | `health_check` 当前榜首 | — | 模块 3614（改写后为 3512）/ 类 49（`GraphOpsMixin`） | 🟡 继续 |
>
> ⚠ **没有调过 `health_check` 的阈值** —— 指标没全绿就是没全绿（改阈值是指标作弊）。

## 现状（量出来的，不是估的）

| 项 | 数字 |
|---|---|
| `storygraph/parsing/renpy_parser.py` | **4422 行** |
| 最重的类 `RenPyParser` | **50 个方法**（第 1345–3017 行） |

方法分成三族（`tools` 里那两个一次性脚本量的）：

| 族 | 个数 | 行数 | 说明 |
|---|---|---|---|
| `_stmt_*`（语句筛查：show/hide/say/audio/define/jump…） | **27** | **544** | 最干净的一族，聚类明确 |
| `_parse_*` / `_scan_*`（块、菜单、show/scene 词法、角色参数） | ~14 | ~800 | 与 `_stmt_*` 互相调用 |
| 入口与基础设施（`__init__` / `parse` / `source_texts` / `_parse_block` / `_raw_block_lines`…） | ~9 | ~300 | 不动 |

## ⚠ 为什么没有当场拆（实测的拦路虎）

`_stmt_*` 那一族引用了 **26 个模块级名字**（`ShowNode` / `HideNode` / `SayNode` …
`_pick_str` / `_scan_balanced_close` / `_scan_toplevel_colon` /
`split_string_speaker` / `_AUDIO_PARAM_WORDS`），而这些**全部定义在
`renpy_parser.py` 自己里面**：

```
ShowNode              <- 本模块定义
_pick_str             <- 本模块定义
split_string_speaker  <- 本模块定义
…（26 个全是）
```

⇒ 直接建一个 `renpy_parser_stmts.py` 去 import 它们 = **循环 import**
（`renpy_parser` 要 import 那个 mixin）。

它还用 **11 个同类方法**（`self._parse_label` / `self._parse_menu` …）与
**32 个 `self._RE_*` + `self.errors/warnings`** ——
后两类不是问题（实例属性/继承都能拿到），**问题只在模块级名字**。

## 正确的拆法（三步，每步都能跑全量判据）

> **2026-10-11 进度**：**① ② 已完成**（提交 `665d748` / 见下一次提交）。
> 结果：`RenPyParser` 从"最重类榜首（50 方法）"**消失**（已不在前三），
> `renpy_parser.py` **4422 → 3444 行**（把"最长模块"让给了 `asset_registry.py` 3614）。
> 全量判据与**解析金标指纹**全程不变。

1. ✅ **先搬数据模型**：节点类与纯 helper 搬进 **`parsing/renpy_nodes.py`**
   （实际搬了 **34 个定义 / 471 行**；`renpy_parser.py` 改为从那 import）。
   ⚠ 实测两个坑：**(a)** 插入 import 的位置**要用 AST 找顶层 import** ——
   按字面找会命中**模块文档字符串里**的示例（语法错误）；
   **(b)** 依赖要**求传递闭包** —— `_skip_string` 还依赖 `_find_triple_end`，
   只量一层就会 `NameError`。
2. ✅ **再搬一族方法**：`StatementScreens` mixin 进
   `parsing/renpy_parser_stmts.py`（27 方法 / 544 行，逐字照搬）；
   `class RenPyParser(StatementScreens):`。
   ⚠ 实测：`self._RE_*` 是 `RenPyParser` 的**类属性**，实例查找会沿 MRO 找到 ——
   所以 mixin 里照写即可，**不必**把正则也搬过去。
   ⚠ 提取脚本必须**先做闭包检查**（方法里引用的名字要么在 `renpy_nodes`、
   要么是已 import 的模块），否则会产出坏文件；判断"模块级名字"要**按作用域**
   （Load 减去该方法的绑定名，绑定名要含**嵌套函数的 `ast.arg`** ——
   `_num(pat)` 的 `pat` 就是这么漏的）。
   ⚠ 还有一处低级但致命的坑：**别把 `'=' * 72` 混进相邻字面量拼接**
   （`'a' '=' * 72` 先拼成 `'a='` 再乘 72 → 整段重复）。
3. ⏳ **顺手拆第二族**（可选）：`_parse_show_scene` / `_tokenize_show` /
   `_scan_arg_value` / `_parse_character_args` 等词法族进 `renpy_parser_lex.py`。
   ⇒ 拆完这一族，`renpy_parser.py` 还剩 ~3000 行，可继续按同样办法分批。

⚠ **还剩的账**：`health_check` 的"最大类 > 40"现在报的是 `GraphOpsMixin`（49），
"最大模块 > 800"报的是 `asset_registry.py`（3614）—— 同一个办法可以继续用在它们身上。

## 纪律（别在拆的时候改逻辑）

- **搬家不改字**：连注释、连空行都照搬 —— 这样 `git diff` 一眼能看出"只是搬家"；
- 每步跑一次**全量**判据（4216 条），**一条都不许变**；
- 判据之外还要跑一次真机解析（`probe_parse_*` 那种金标指纹），
  确保"产物逐字节不变"；
- ❌ **不要**为了让指标变绿去调 `health_check` 的阈值 —— 那是指标作弊
  （本仓已有明说：不许为了让门禁变绿而改判据）。

## 何时做

放**单独一轮**（预计一次搬家 + 两次全量判据）。它与"给用户加能力"无关，
所以优先级低于用户可见的缺口 —— 但比"一直挂着不管"好：方案在这儿，
下次谁做都是机械劳动。

## 附：`asset_registry.py` 的实测闭包（决定搬法）

`asset_registry.py` 3614 行的构成（量出来的）：类只占 368 行，
**模块级函数 2711 行 / 87 个**，常量 145 行。所以该搬的是**函数族**。

先用 `.pip-tmp/closure_asset_registry.py` 量三个候选族的**传递闭包**：

| 候选族 | 初选 | 闭包大小 | 闭包行数 |
|---|---|---|---|
| xlsx 表读写族（`parse_asset_tables` / `append_rows_to_table` / `write_asset_excel` / `ensure_asset_tables` / `sync_asset_tables` / `register_missing_assets`） | 6 | **100 个定义** | 2352 |
| 扫描族（`scan_assets` / `compare_registry_to_disk` / `_detect_category` / `_detect_image_tag` / `FileWalkIndex`） | 5 | 49 个定义 | 979 |
| 语句引用族（`references_from_statements` / `describe_asset` / `register_missing_assets`） | 3 | 55 个定义 | 989 |

⇒ **三个族的闭包都被同一批常量拉大**（`SUPPORTED_*` / `_BG_*` / `_CG_*` /
`CATEGORY_KEYWORDS` / `ASSET_FIELD_*` …）。所以顺序是：

1. ✅ **先搬常量层** → `assets/asset_registry_constants.py`
   （**37 个常量 / 141 行**；搬完 3614 → **3512 行**）。
   ⚠ 两个实测坑：**(a)** 判据要严 —— 引用的名字只能是"另一个常量 / import 来的 /
   内置"，我第一版只查"是否引用本模块函数"，于是 `_asset_set_text = set_text`
   （`set_text` 是 **import 来的**）被误搬 → `NameError`；
   **(b)** 新模块**必须自带那条 import**，而且要用**换行 join**
   （`''.join(...)` 会把两条 import 挤成一行 `...set_textimport re`）。
   ⚠ 剩下 3 个"看着像常量其实不是"的留在原地并在脚本里点名：
   `ASSET_FIELD_ALIASES`（推导式里用了 `field`/`label`）、
   `_is_real_table_file`（= 函数）、`_norm_asset_path`（= 函数）。
2. ⏳ **再搬扫描族**（闭包 49 个定义 / 979 行 —— 常量搬走后会变小）。
3. ⏳ 最后搬 xlsx 表读写族。

## 附：继续往下拆的顺序建议（按"闭包 / 风险"排序）

| 模块 | 行数 | 建议 |
|---|---|---|
| `assets/asset_registry.py` | 3512 | 按上面 2 → 3 继续（机械劳动） |
| `story/beat_render.py` | 3388 | 同法：先量闭包，先搬常量/叶子层 |
| `story/beat_model.py` | 3115 | 同上 |
| `ui_qt/window_graph_ops.py`（`GraphOpsMixin` 49 方法） | — | ⚠ Qt mixin，风险比纯函数高：先确认契约判据（`test_main_window_mixins` 已在管重名），再一族一族搬 |
