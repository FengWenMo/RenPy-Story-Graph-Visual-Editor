# 重构方案：把 `renpy_parser.py` 拆开（4422 行 / 50 方法的类）

> `tools/health_check.py` 长期报："最大模块 4422 行 > 800 ｜ 最大类 50 方法 > 40"。
> 2026-10-11 量过依赖，**没有在这轮硬拆**（理由写在最后）——
> 这份文档把方案钉死，让下一次是**机械劳动**而不是重新设计。

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

1. **先搬数据模型**：把节点类（`ShowNode`/`HideNode`/`SayNode`/…，约 20 个）
   与那几个纯 helper 搬进 **`parsing/renpy_nodes.py`**；
   `renpy_parser.py` 改为 `from storygraph.parsing.renpy_nodes import *`（显式列出）。
   ✅ 这一步**不改任何逻辑**，全量判据应当**一条不变**地过。
2. **再搬一族方法**：`_StatementScreens` mixin 进 `parsing/renpy_parser_stmts.py`，
   `from renpy_nodes import …`（此时**不再是循环**）；
   `class RenPyParser(_StatementScreens):`。27 个方法**逐字照搬**（连注释）。
   ✅ 判据仍应一条不变；`RenPyParser` 方法数降到 ~23（**低于 40**）。
3. **顺手拆第二族**（可选）：`_parse_show_scene` / `_tokenize_show` /
   `_scan_arg_value` / `_parse_character_args` 等词法族进 `renpy_parser_lex.py`。

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
