"""
退化链：引擎读不到时，工具该说**"无法判定"**
============================================

> 2026-11-05（L1-1）。设计见 `docs/设计_引擎无关IR与退化链_2026-11-05.md` §3.3。

## 五个档位（从硬到软）

| 档 | 名字 | 手里有什么 |
|---|---|---|
| R0 | `runtime_full` | 运行期事实**全采到**（探针 + dump + lint 都有） |
| R1 | `runtime_partial` | 有运行期事实，但**某项没采到**（或数据过期） |
| R2 | `declared_only` | 只有**声明**（`layeredimage` / `rules.json` / 表），没有运行期证据 |
| R3 | `usage_only` | 只有**用法**（剧本里 `show` 过什么） |
| R4 | `files_only` | 只有**文件**（连声明都没有） |

## 三条不变量（改这里之前先读一遍）

1. **退化不改判**：R0 能判"错"的，R1/R2 只能 `unknown`，**不许**变成 `ok`；
2. **退化必须留痕**：返回的档位要能追到 `degraded.reason`；
3. **不许升级**：`source='declared'` 的事实**永远不能**因为"没冲突"被当成
   运行期事实。

## 为什么 `collectable` 要调用方传进来

`is_collectable()` 在 `core.engine_facts` 里，而 `engine` 层**不许 import
`core`**（§2 的依赖方向）。所以这里收一个布尔，由 `pipeline` / `app` 算好传
进来 —— 少一个 import，换掉一整条依赖环。
"""

from __future__ import annotations

# ---- 档位 ----------------------------------------------------------------

R0 = 'runtime_full'
R1 = 'runtime_partial'
R2 = 'declared_only'
R3 = 'usage_only'
R4 = 'files_only'

#: 从硬到软（顺序有意义：`min()` / 比较用）
RUNGS = (R0, R1, R2, R3, R4)

#: 每个档位一句话（报告/CLI 直接引用，别在别处另写措辞）
RUNG_NOTES = {
    R0: '运行期事实全采到（探针 + dump + lint）',
    R1: '有运行期事实，但某项没采到或数据可能过期',
    R2: '只有声明（layeredimage / rules.json / 资源表）',
    R3: '只有用法（剧本里 show 过什么）',
    R4: '只有文件（连声明都没有）',
}

#: **只有运行期才拿得到**的能力 —— R2 及以下一律没有这些（`allows` 用）。
#: 放在这里而不是 `capabilities` 里：那是"令牌"，这是"档位归属"。
RUNTIME_ONLY = frozenset({
    'CAP_STATEMENT_REGISTRY',
    'CAP_LINT_DIAGS',
    'CAP_LABELS',
})


def rung(facts, *, collectable: bool = True, freshness: str = '') -> str:
    """
    这份事实能支撑到哪一档（纯函数）。

    判定顺序是**固定**的，不要调换（每一步都有实测理由）：

    ```
    没有 facts / schema 不认识      -> R4（连结构都不认，只剩文件）
    collectable=False               -> R2（静态仍在；**不要**因为探针失败
                                        就说"没数据"）
    capabilities 有 None / degraded -> R1
    freshness == 'stale'            -> R1（数据在，但可能过期）
    全部 True                       -> R0
    """
    if not isinstance(facts, dict) or not facts:
        return R4
    if 'schema' not in facts:
        return R4
    if not collectable:
        # 静态解析是**基线**，不是 fallback：探针没跑成也有声明可用。
        return R2
    caps = facts.get('capabilities')
    deg = facts.get('degraded')
    if not isinstance(caps, dict) or not caps:
        # 老 facts（v1）没有 capabilities 段：只有"探针跑通了"这一个信号
        return R1
    # ⚠ **只有 `None`（这次没采到）才降级**（2026-11-05 实现 P0-3 时定的）：
    # `False` 是"这个引擎结构上就没有这项"（比如 P0 的
    # `CAP_STORE_SNAPSHOT` / `CAP_LAYERED_CONDITIONALS`）—— 那是**能力清单的
    # 事实**，不是"这次失败了"。把它算成降级会让 R0 永远不可达。
    if any(val is None for val in caps.values()):
        return R1
    if isinstance(deg, dict) and deg.get('missing'):
        return R1
    if str(freshness or '').lower() == 'stale':
        return R1
    return R0


def allows(rung_name: str, need, caps=None) -> tuple:
    """
    这个档位能不能支撑 `need` 里这些能力 → `(能不能, 缺哪些)`。

    · `R0` → 全都能（`caps` 给了就仍按实际值核一遍，防止"标了 R0 但缺项"）；
    · `R1` → 看 `caps`：`None` / `False` 的都算缺；
    · `R2` 及以下 → **运行期能力一个都没有**（`RUNTIME_ONLY`），静态能力照给。
    """
    wanted = [str(n) for n in (need or ()) if str(n)]
    if not wanted:
        return True, []
    if rung_name == R0:
        if not isinstance(caps, dict):
            return True, []
        miss = [n for n in wanted if caps.get(n) is not True]
        return (not miss), miss
    if rung_name == R1:
        if not isinstance(caps, dict):
            return False, wanted            # 说 R1 却没给能力表 → 不许假设
        miss = [n for n in wanted if caps.get(n) is not True]
        return (not miss), miss
    # R2 / R3 / R4：运行期能力一律没有
    miss = [n for n in wanted if n in RUNTIME_ONLY]
    return (not miss), miss


def describe(rung_name: str, facts=None) -> str:
    """一句人话（报告/CLI）：`R2 declared_only —— 只有声明…（因为 probe_failed）`。"""
    name = str(rung_name or '')
    note = RUNG_NOTES.get(name, '（未知档位）')
    reason = ''
    if isinstance(facts, dict):
        deg = facts.get('degraded')
        if isinstance(deg, dict):
            reason = str(deg.get('reason') or '')
    base = f'{name} —— {note}'
    return f'{base}（因为 {reason}）' if reason else base


def is_weaker_than(left: str, right: str) -> bool:
    """`left` 比 `right` 更软吗（`R2` 比 `R0` 软）。未知档位按最软算。"""
    def idx(name):
        return RUNGS.index(name) if name in RUNGS else len(RUNGS)
    return idx(str(left)) > idx(str(right))


__all__ = [
    'R0', 'R1', 'R2', 'R3', 'R4', 'RUNG_NOTES', 'RUNGS', 'RUNTIME_ONLY',
    'allows', 'describe', 'is_weaker_than', 'rung',
]
