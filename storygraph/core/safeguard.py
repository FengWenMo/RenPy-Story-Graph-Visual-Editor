"""
storygraph.core.safeguard
=========================
**静默失败不再无声**：把"`except: pass`"换成"记一笔"。

用户 2026-10-05 拿审计报告来问：

> 错误处理靠压制：321 个 except 里 99 个 body 只有 pass，全项目 196 处
> # noqa: BLE001

实测数字一模一样。`except: pass` 本身**不都是错的**（清理、Qt 控件已销毁、
可选缓存写不进去……这些确实只该忽略），但它有两个真实代价：

  1. **出事时没有任何痕迹** —— 用户看到的是"功能没反应"，我们看不到原因；
  2. **真 bug 会被它吃掉** —— 审计时就有一条：`apply` 里吞掉异常导致
     改动静默丢失（见 `app/editor.py` 的 `swallow(...)` 调用点）。

所以这一版**不删异常处理**（那会改变行为），只把"什么都不说"换成
`swallow(context, exc)`：

  · 记进一个有界环形缓冲（默认 200 条，按 context 聚合计数）；
  · `report()` / `recent()` 给控制台、测试、体检用；
  · `STORYGRAPH_DEBUG=1`（或 `set_debug(True)`）时同时打到 stderr。

零依赖：只用标准库（core 层的规矩）。
"""

from __future__ import annotations

import os
import sys
from collections import OrderedDict

#: 环形缓冲上限：只留最近这么多**种** context（同类只累加计数）
MAX_CONTEXTS = 200
#: 每种 context 保留最近的几条样本
SAMPLES_PER_CONTEXT = 3

#: `{context: {'count': n, 'last': str, 'samples': [str, …]}}`（插入顺序 = 首次出现）
_records: "OrderedDict[str, dict]" = OrderedDict()
_total = 0
_debug = None                     # None = 看环境变量


def _debug_on() -> bool:
    global _debug
    if _debug is None:
        _debug = str(os.environ.get('STORYGRAPH_DEBUG', '')).strip() not in \
            ('', '0', 'false', 'False')
    return bool(_debug)


def set_debug(on) -> None:
    """强制打开/关闭 stderr 输出（测试用）。"""
    global _debug
    _debug = bool(on)


def swallow(context: str, exc: BaseException = None) -> None:
    """
    记一笔"这里出过错，但按设计继续往下走"。

    `context` 用 `'模块.函数'` 这种能一眼定位的写法；
    `exc` 给异常对象（拿不到就不给）。**永远不抛异常** ——
    它自己就是错误处理路径上的东西。
    """
    global _total
    try:
        key = str(context or '?')
        detail = ''
        if exc is not None:
            detail = f'{type(exc).__name__}: {exc}'
            detail = detail[:200]
        rec = _records.get(key)
        if rec is None:
            rec = {'count': 0, 'last': '', 'samples': []}
            _records[key] = rec
            while len(_records) > MAX_CONTEXTS:
                _records.popitem(last=False)
        rec['count'] += 1
        rec['last'] = detail
        if detail and detail not in rec['samples']:
            rec['samples'].append(detail)
            del rec['samples'][SAMPLES_PER_CONTEXT:]
        _total += 1
        if _debug_on():
            print(f'[swallow] {key}: {detail}', file=sys.stderr)
    except Exception:                 # noqa: BLE001 - 记录失败也不许影响主流程
        # **这里必须是空实现**：`swallow` 自己就在错误处理路径上，
        # 再调一次自己、万一又失败就是无限递归。
        # （全项目唯一允许的 `except …: pass`，见 tests/test_code_health.py）
        pass


def total() -> int:
    """累计记录条数（含重复）。"""
    return _total


def recent(limit: int = 20) -> list:
    """最近出过错的 context：`[(context, 次数, 最后一条详情), …]`（新的在后）。"""
    items = list(_records.items())
    return [(k, v['count'], v['last']) for k, v in items[-max(1, int(limit)):]]


def report(limit: int = 8) -> str:
    """给人看的一段话（控制台/体检用）；没有任何记录时返回空串。"""
    if not _records:
        return ''
    lines = [f'静默失败 {_total} 次（{len(_records)} 处）：']
    for ctx, count, last in recent(limit):
        tail = f' —— {last}' if last else ''
        lines.append(f'  · {ctx} ×{count}{tail}')
    if len(_records) > limit:
        lines.append(f'  …还有 {len(_records) - limit} 处')
    return '\n'.join(lines)


def contexts() -> list:
    """只列出出过错的 context（测试断言用）。"""
    return list(_records.keys())


def clear() -> None:
    """清空记录（测试用；也留给"我看过了"这种交互）。"""
    global _total
    _records.clear()
    _total = 0


__all__ = ['MAX_CONTEXTS', 'clear', 'contexts', 'recent', 'report',
           'set_debug', 'swallow', 'total']
