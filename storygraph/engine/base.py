"""
一次采集的**信封**（纯 dict 约定，不碰 `core`）
=============================================

> 2026-11-05（P0-1）。字段定义见 `docs/设计_引擎无关IR与退化链_2026-11-05.md` §3.2。

## 为什么不是 dataclass

`engine` 层**不许 import 任何 `storygraph.*`**（连 `core` 都不行）。而 facts
最终要写成 JSON、被 `core.engine_facts.validate_facts()` 校验 —— 那里认的是
**纯 dict**。所以这里只提供"形状 + 构造 + 自检"，不引入第二种表示。

多一种表示就要多一层转换，转换就会漏字段（这个仓库里"漏一个字段 = 静默丢数据"
的教训已经够多了）。

## 信封长什么样

```json
{
  "schema": 2,
  "adapter": {"id": "renpy", "version": 1},
  "capabilities": {"CAP_IMAGE_REGISTRY": true, "CAP_LABELS": null,
                    "CAP_STORE_SNAPSHOT": false},
  "degraded": {"reason": "probe_partial", "missing": ["CAP_LABELS"]},
  "freshness": {"verdict": "fresh", "checked_at": "…", "rpy_stamp": "…"}
}
```

四段是**一次采集**的属性，不是某条事实的属性 —— 所以它们挂在顶层，不塞进
`facts[key]` 里（`facts[key]` 的 `{value, source}` 形状一个字节都不改，
`value_of()` / `source_of()` 的老消费者不受影响）。
"""

from __future__ import annotations

from storygraph.engine import capabilities as C

#: 信封的结构版本（与 facts 的 `schema` 同一个数字，同步升）
BATCH_VERSION = 2


def empty_batch(adapter_id: str = 'renpy', adapter_version: int = 1) -> dict:
    """
    一次"什么都没采到"的信封（**采集失败时也要写** —— 让"没采到"可见）。

    注意 `capabilities` 里全部填 `None`（= 这次没采到），**不是** `False`
    （= 引擎根本没有）。这个区分是这一层的全部价值。
    """
    return {
        'schema': BATCH_VERSION,
        'adapter': {'id': str(adapter_id or ''), 'version': int(adapter_version)},
        'capabilities': {name: C.UNKNOWN for name in C.ALL_CAPABILITIES},
        'degraded': {'reason': 'not_collected', 'missing': list(C.ALL_CAPABILITIES)},
        'freshness': {'verdict': 'unknown', 'checked_at': '', 'rpy_stamp': ''},
    }


def batch(*, adapter_id: str = 'renpy', adapter_version: int = 1,
          capabilities=None, degraded=None, freshness=None) -> dict:
    """
    造一封采集信封（缺什么补什么，**不猜值**）。

    `capabilities` 里没给的令牌填 `None`（"这次没采到"），不是 `False`。
    """
    out = empty_batch(adapter_id, adapter_version)
    if isinstance(capabilities, dict):
        for name in C.ALL_CAPABILITIES:
            if name in capabilities:
                out['capabilities'][name] = C.normalize(capabilities.get(name))
        # 未登记的令牌**原样保留**（让 adapter 自己解释；core 保持哑，不报错）
        for name, val in capabilities.items():
            if str(name) not in C.ALL_CAPABILITIES:
                out['capabilities'][str(name)] = C.normalize(val)
    if isinstance(degraded, dict):
        out['degraded'] = {
            'reason': str(degraded.get('reason') or ''),
            'missing': [str(x) for x in (degraded.get('missing') or ())],
        }
    elif degraded is None:
        # 没有降级原因且能力都采到 → 干净
        miss = C.missing_tokens(out['capabilities'])
        out['degraded'] = ({'reason': '', 'missing': []} if not miss
                           else {'reason': 'partial', 'missing': miss})
    if isinstance(freshness, dict):
        out['freshness'] = {
            'verdict': str(freshness.get('verdict') or 'unknown'),
            'checked_at': str(freshness.get('checked_at') or ''),
            'rpy_stamp': str(freshness.get('rpy_stamp') or ''),
        }
    return out


def problems(batch_dict) -> list:
    """
    自检一封信封 → 问题清单（空 = 通过）。**纯结构检查，不猜内容**。

    与 `core.engine_facts.validate_facts()` 的分工：那边校验整份 facts（含
    `facts` / `gaps` / `derived`），这边只管信封四段 —— 采集端写盘前先自检，
    能早一步发现"忘了填 degraded"。
    """
    out = []
    data = batch_dict if isinstance(batch_dict, dict) else {}
    if not data:
        return ['信封不是 dict 或为空']
    if data.get('schema') != BATCH_VERSION:
        out.append(f"schema 应为 {BATCH_VERSION}，实际 {data.get('schema')!r}")
    ad = data.get('adapter')
    if not isinstance(ad, dict) or not ad.get('id'):
        out.append('adapter 段缺 id')
    caps = data.get('capabilities')
    if not isinstance(caps, dict):
        out.append('capabilities 段不是 dict')
    else:
        for name in C.ALL_CAPABILITIES:
            if name not in caps:
                out.append(f'capabilities 缺 {name}（要么给值，要么给 None）')
            elif C.normalize(caps.get(name)) != caps.get(name):
                out.append(f'capabilities.{name} 不是三态之一：'
                           f'{caps.get(name)!r}')
        for name in C.unknown_tokens(caps):
            out.append(f'capabilities 里有未登记的令牌：{name}（请登记到 '
                       f'engine.capabilities.ALL_CAPABILITIES）')
    deg = data.get('degraded')
    if not isinstance(deg, dict):
        out.append('degraded 段不是 dict')
    else:
        has_missing = bool(deg.get('missing'))
        if has_missing and not str(deg.get('reason') or ''):
            out.append('degraded.missing 非空但没有 reason —— 降级必须能追到原因')
        if not has_missing and str(deg.get('reason') or ''):
            out.append('degraded.reason 有值但 missing 为空 —— 两处对不上')
    fr = data.get('freshness')
    if not isinstance(fr, dict):
        out.append('freshness 段不是 dict')
    elif fr.get('verdict') not in ('fresh', 'stale', 'unknown'):
        out.append(f"freshness.verdict 不合法：{fr.get('verdict')!r}"
                   '（fresh / stale / unknown）')
    return out


__all__ = ['BATCH_VERSION', 'batch', 'empty_batch', 'problems']
