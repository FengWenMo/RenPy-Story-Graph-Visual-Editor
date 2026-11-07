"""
引擎协议层（**纯 stdlib，零 storygraph 依赖**）
=============================================

> 2026-11-05，见 `docs/设计_引擎无关IR与退化链_2026-11-05.md` 与
> `docs/交接_P0与L1_引擎协议层_2026-11-05.md`。

这一层只做三件事：

1. **说清"引擎能回答什么"** —— `capabilities.py` 的三态令牌；
2. **说清"这次回答算不算数"** —— `decide.py` 的退化链（R0–R4）；
3. **说清"这是哪个引擎版本"** —— `detect.py`（只读，不 import 引擎代码）。

## ⚠ 这个文件是空的，这是故意的

`__init__.py` **不许 re-export 任何实现**：

- 一旦这里 `from .renpy import ...`，`import storygraph.engine` 就会拖进
  `pipeline`，而 `pipeline` 又依赖 `assets` / `core` —— 依赖环立刻回来，
  `tools/debt_budget.py` 的 `import_cycles_all` 预算（**0**）当场红；
- 引擎适配器（`renpy_adapter.py`）放在 `pipeline/` 而不是这里，也是同一个理由。

依赖方向（必须保持）：

```
pipeline ──► engine ──► stdlib          # engine 是叶子，谁也不依赖
pipeline ──► core
core     ──✗──► engine                   # 禁止
engine   ──✗──► pipeline / assets / core # 禁止（连 core 都不要）
```
"""
