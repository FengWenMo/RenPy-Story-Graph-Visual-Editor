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
  · `STORYGRAPH_DEBUG=1`（或 `set_debug(True)`）时同时打到 stderr；
  · **同时进标准库 `logging`**（2026-11-01，外部审查 5.4 / P1-7）：
    从前全项目 0 处 `logging`，只有这个自造的环形缓冲 —— 于是打包后的
    无窗口版本（没有 stderr）连这一条都看不见。现在 `swallow()` 除了
    记账，还会往 `logging.getLogger('storygraph')` 记一条 DEBUG。
    默认不输出（和标准库日志一样）；`configure(debug=True)` 或
    `STORYGRAPH_DEBUG=1` 时打到 stderr，`configure(log_file=…)` 时落盘。

零依赖：只用标准库（core 层的规矩）。
"""

from __future__ import annotations

import logging
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
_debug = None                     # None = 每次读环境变量（不缓存）
#: 被挤出的 context 的失败次数**汇总**在这里：不能直接丢，否则同一 context
#: 重新出现时 `count` 从 1 开始，`report_since()` 的差值会算成"这轮没失败"
#: —— 诊断功能正好在最需要它的时候失灵（清单 §二十）。
_overflow = 0
_OVERFLOW_CONTEXT = '__overflow__'


def _debug_on() -> bool:
    """
    是否同时打到 stderr。

    `_debug is None` 时**每次读环境变量**（从前第一次调用后就把值缓存了，
    测试里改 `STORYGRAPH_DEBUG` 不生效，而且会被之前的状态污染）。
    `set_debug()` 仍然可以强制覆盖。
    """
    if _debug is not None:
        return bool(_debug)
    return str(os.environ.get('STORYGRAPH_DEBUG', '')).strip() not in \
        ('', '0', 'false', 'False')


def set_debug(on) -> None:
    """强制打开/关闭 stderr 输出；`None` = 恢复"每次读环境变量"。"""
    global _debug
    _debug = None if on is None else bool(on)


#: 标准库日志器：`swallow()` 记 DEBUG，`configure()` 决定它往哪去。
LOGGER_NAME = 'storygraph'
logger = logging.getLogger(LOGGER_NAME)


def configure(*, debug=None, log_file=None, level=None) -> logging.Logger:
    """
    把"被兜住的错误"接进标准库 `logging`。**幂等**（重复调用不会叠 handler）。

    三种去向，可以叠加：

      · `debug=True`（或环境变量 `STORYGRAPH_DEBUG=1`）→ 打到 stderr；
      · `log_file=<路径>` → 追加写进文件（GUI 崩溃日志、CI 归档都用这条）；
      · 都不给 → 只进环形缓冲（`report()` 仍能取到），终端不吵。

    为什么要它（2026-11-01，外部审查 5.4）：全项目 0 处 `logging`，而打包后
    的**无窗口版本根本没有 stderr** —— 193 处 `print` 连同 `swallow` 的
    调试输出一起消失，"双击没反应"就真的一点痕迹都不剩。
    """
    if debug is not None:
        set_debug(bool(debug))
    on = _debug_on()
    logger.setLevel(logging.DEBUG if (on or log_file) else logging.WARNING)
    # **幂等**：先把上一次挂的、带我们标记的 handler 摘掉，再按需重挂。
    for h in list(logger.handlers):
        if getattr(h, '_storygraph', False):
            logger.removeHandler(h)
            _close_quietly(h)
    if on:
        sh = logging.StreamHandler(stream=sys.stderr)
        sh.setLevel(logging.DEBUG)
        sh.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
        sh._storygraph = True
        logger.addHandler(sh)
    if log_file:
        try:
            fh = logging.FileHandler(log_file, encoding='utf-8')
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(
                '%(asctime)s [%(levelname)s] %(message)s'))
            fh._storygraph = True
            logger.addHandler(fh)
        except OSError as exc:
            # 写不了日志文件不该拖垮主流程（磁盘满 / 目录只读）。
            # 但要留痕：这是"你以为了“有日志”，其实没有"的那一格。
            logger.warning('日志文件写不了（%s）：%s', log_file, exc)
    logger.propagate = False
    return logger


def _close_quietly(handler) -> None:
    """关掉一个 handler；关不掉也不算错（最坏是它还挂着，下次配置再摘）。"""
    try:
        handler.close()
    except Exception as exc:                 # noqa: BLE001 - 关 handler 失败无关紧要
        logger.debug('关闭日志 handler 失败：%s', exc)


def summary_line(limit: int = 3) -> str:
    """
    一行摘要：「本次有 N 次被兜住的错误（--debug 看明细）」。

    给 CLI 在**命令成功之后**打印 —— "退出码 0、但中途有东西被吞掉"
    正是最该被看见的那一格（外部审查 5.4 的那张表全是这种形态）。
    没有记录时返回空串。
    """
    if not _records:
        return ''
    names = '、'.join(k for k, _n, _l in recent(limit))
    tail = f'；加 --debug 看明细（首条：{names}）' if names else ''
    return f'注意：本次运行有 {_total} 次错误被兜住、没有中断{tail}'


def swallow(context: str, exc: BaseException = None) -> None:
    """
    记一笔"这里出过错，但按设计继续往下走"。

    `context` 用 `'模块.函数'` 这种能一眼定位的写法；
    `exc` 给异常对象（拿不到就不给）。**永远不抛异常** ——
    它自己就是错误处理路径上的东西。
    """
    global _total, _overflow
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
                _old_key, old = _records.popitem(last=False)
                # 不能直接丢：把失败次数汇总进 `__overflow__`。
                # 否则同一 context 之后重新出现时 count 从 1 开始，
                # `report_since()` 的差值会把它算成"没失败"。
                _overflow += int(old.get('count') or 0)
        rec['count'] += 1
        rec['last'] = detail
        if detail and detail not in rec['samples']:
            rec['samples'].append(detail)
            del rec['samples'][SAMPLES_PER_CONTEXT:]
        _total += 1
        # 标准库日志（`configure()` 决定去向；没配过就是"不输出"）。
        # DEBUG 级：它不是错误，是"被兜住了"，默认不该污染终端。
        logger.debug('%s: %s', key, detail or '(no message)')
        if _debug_on():
            print(f'[swallow] {key}: {detail}', file=sys.stderr)
    except Exception:                 # noqa: BLE001 - 记录失败也不许影响主流程
        # **这里必须是空实现**：`swallow` 自己就在错误处理路径上，
        # 再调一次自己、万一又失败就是无限递归。
        # （全项目唯一允许的 `except …: pass`，见 tests/test_no_silent_except.py）
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


def snapshot() -> dict:
    """
    当前每种 context 的次数（`{context: count}`）。

    `_swallow_mark()` 用它取"操作前"的基线：只报**这一次操作新增加**的
    那些。从前标记只有一个总数，同一批 173 张缩略图会在每次操作后
    重复打印一遍（用户看到 212 条红字的一大半就是这么来的）。

    `__overflow__` 是被环形缓冲挤掉的 context 的**合计次数**。把它放进
    快照，`report_since()` 才能看见"这轮新增的失败里有 N 次属于已被挤出
    明细的 context"，而不是当成没发生。
    """
    out = {k: int(v.get('count') or 0) for k, v in _records.items()}
    if _overflow:
        out[_OVERFLOW_CONTEXT] = int(_overflow)
    return out


def records() -> list:
    """全部 context 的明细：`[{'context','count','last','samples'}, …]`。"""
    return [{'context': k, 'count': int(v.get('count') or 0),
             'last': str(v.get('last') or ''),
             'samples': list(v.get('samples') or ())}
            for k, v in _records.items()]


def report_since(before=None, limit: int = 8) -> str:
    """
    只报"相对 `before` 新增/增加"的静默失败（诊断面板与状态栏用）。

    `before` 是 `snapshot()` 的结果；给 `None` 时退回全量 `report()`。
    返回空串 = 这次操作没有新的失败。
    """
    if before is None:
        return report(limit)
    now = snapshot()
    delta = [(ctx, now[ctx] - int(before.get(ctx, 0)))
             for ctx in now if now[ctx] > int(before.get(ctx, 0))]
    if not delta:
        return ''
    total = sum(n for _ctx, n in delta)
    lines = [f'静默失败 {total} 次（{len(delta)} 处）：']
    for ctx, count in delta:
        last = str((_records.get(ctx) or {}).get('last') or '')
        tail = f' —— {last}' if last else ''
        lines.append(f'  · {ctx} ×{count}{tail}')
    if len(delta) > limit:
        lines.append(f'  …还有 {len(delta) - limit} 处')
    return '\n'.join(lines)


def clear() -> None:
    """清空记录（测试用；也留给"我看过了"这种交互）。"""
    global _total, _overflow
    _records.clear()
    _total = 0
    _overflow = 0


__all__ = ['LOGGER_NAME', 'MAX_CONTEXTS', 'clear', 'configure', 'contexts',
           'logger', 'records', 'recent', 'report', 'report_since',
           'set_debug', 'snapshot', 'summary_line', 'swallow', 'total']
