# fork 模拟补丁（地狱测试用）

## 这是什么

`weather6_dispatch.py` 会被 `tools/build_fork_sdk.py` **原样插进** SDK 的
`renpy/parser.py`，位置在 `parse_statement()` 里「拿到 `loc`」之后、
「分派给 `statements.parse`」之前。

插进去之后，引擎就**自带宽了一条语句 `weather6`** —— 项目里**不需要写任何 CDS**。
这模拟的是最极端的一类魔改：**程序直接改了解析器的分派逻辑**。

## 为什么要造它

地狱测试里有一类用例（`D4a`/`D4b`）要回答一个问题：

> 引擎会接受一个我完全不认识的语句，而项目里没有任何声明 ——
> **工具链（扫描器 / lint / 探针 / `--json-dump`）能不能察觉？**

实测答案：**察觉不到，四路全无信号**，唯一的线索是"用法本身"。
这正是"**扫描做发现**"这条线**不可被取代**的实证。

## 怎么用

```bash
python tools/build_fork_sdk.py        # 造到 .pip-tmp/fork_sdk_full（在 .gitignore 里）
python tools/hell_engine_oracle.py D4a D4b
```

`D4a`（fork SDK）应当**接受** `weather6 雨 0.5`；
`D4b`（官方 SDK，同一工程）应当**报 `expected statement.`**。
两者一起才证明"这条语句确实来自 fork，而不是标准语法"。

## 两个必须遵守的实现细节（都踩过）

1. **`l.advance()` 必须调用。** 不推进 lexer，`parse_block` 的
   `while not l.eob` 会**死循环** → `MemoryError`（实测到过）。
2. **必须返回真实 AST 节点**（`renpy.ast.Pass`），不能返回 `None`。

## 为什么 SDK 本身不进版本库

它是 **1.4GB 的第三方软件树**（有自己的 LICENSE）；提交进 git 会让仓库
永久背上这个体积，且每次 SDK 升级再翻一倍。
所以版本库里只放**"怎么造出来"**（本目录 + `build_fork_sdk.py`），
造出来的东西落在 `.pip-tmp/`（已被 `.gitignore` 忽略）。
