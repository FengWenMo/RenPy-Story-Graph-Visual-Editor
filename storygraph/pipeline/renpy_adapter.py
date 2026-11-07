"""
Ren'Py 适配器 —— **薄包装**，行为零变化
======================================

> 2026-11-05（P0-3）。设计见 `docs/设计_引擎无关IR与退化链_2026-11-05.md` §4。

## 为什么它这么薄

以后的 L2（`story_ir`）要支持多个引擎（Unity / 别的），那时 `FactBatch` 会
真的被多个 adapter 产出。**现在只有一个**，所以这一层只做两件事：

1. 把 `engine_collect.collect()` 的产出**转成信封视图**（给 UI / 报告判断档位）；
2. 给出 adapter 的身份（`id` / `version`），让 facts 里那句
   `{"adapter": {"id": "renpy", "version": 1}}` 有个出处。

**不搬逻辑**：采集、探针、lint、合并全在 `engine_collect` 里，这里一行都不复制。
复制就会分叉 —— 而"两套口径"是这个仓库里反复出现的事故（见 `_SAY_RE` 的注释）。

## 为什么不放在 `storygraph/engine/renpy.py`

`engine/` 是**叶子**（只许 stdlib）。这个文件要 import `pipeline.engine_collect`
（而后者又依赖 `assets` / `core`）—— 放进 `engine/` 就等于让叶子长出根，
`import_cycles_all`（预算 0）当场红。位置由依赖方向决定，不由"名字像不像"决定。
"""

from __future__ import annotations

#: adapter 的身份（写进 facts 的 `adapter` 段）
ADAPTER_ID = 'renpy'
ADAPTER_VERSION = 1

#: 信封里的四段（`facts` 本体不在其中 —— 那是采集层的事）
ENVELOPE_KEYS = ('schema', 'adapter', 'capabilities', 'degraded', 'freshness')


def collect(project_root, sdk=None, *, mode='in-place', out_path=None,
            timeout=None, ask: bool = False, ask_limit: int = 12) -> dict:
    """
    采一次 → `engine_collect.collect()` 的原样结果（**行为零变化**）。

    存在的意义只有一个：调用方不必知道"采集实现在 pipeline 的哪个模块"。
    参数逐个转发，不加工、不兜底、不改默认值 —— 一旦这里做了任何"聪明事"，
    就会与直接调 `engine_collect.collect()` 产生两套行为。
    """
    from storygraph.pipeline import engine_collect as EC

    kw = {}
    if timeout is not None:
        kw['timeout'] = timeout
    return EC.collect(project_root, sdk, mode=mode, out_path=out_path,
                      ask=ask, ask_limit=ask_limit, **kw)


def envelope(facts) -> dict:
    """
    从一份 facts 里取**信封四段**（+ schema），给"要不要降级"的判断用。

    只读，不改。v1 文件没有这四段 → 给空壳（`capabilities={}`），
    让 `decide.rung` 走"老 facts = R1"那条分支（它认这个形状）。
    """
    data = facts if isinstance(facts, dict) else {}
    out = {'schema': data.get('schema')}
    for key in ENVELOPE_KEYS:
        if key == 'schema':
            continue
        val = data.get(key)
        out[key] = dict(val) if isinstance(val, dict) else {}
    return out


def identity() -> dict:
    """`{'id': 'renpy', 'version': 1}` —— 写进 facts 的那一段。"""
    return {'id': ADAPTER_ID, 'version': ADAPTER_VERSION}


__all__ = ['ADAPTER_ID', 'ADAPTER_VERSION', 'ENVELOPE_KEYS', 'collect',
           'envelope', 'identity']
