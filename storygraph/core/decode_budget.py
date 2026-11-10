# -*- coding: utf-8 -*-
"""
进程级解码闸门（2026-11-10）
===========================

**要解决的问题**：真机的一张立绘是 4320×7680（11 MB PNG），解码峰值约
265–280 MB（RGBA 133 MB + PNG 解压缓冲）；而缩略图的线程池默认同时解
**16 张**（`ui_qt.thumb_loader.default_max_in_flight` 与
`app.prewarm._default_workers` 都是 `min(16, 核数)`）。实测 12 张 8K 立绘：

    · 16 并发：峰值 **2 751 MB**
    · 4 并发：峰值 **1 105 MB**
    · 顺序解：每张仍在谷底之上（缓冲不会立刻还给系统）

用户真机就因为这个卡死过一次（2026-11-10 反馈："内存占用太大了……直接给我
电脑卡死机重启了……别什么都先存内存里"）。

**做法**：把"同时解几张"从**线程数**换成**字节预算** —— 解码前按文件头估这
一张的峰值（宽 × 高 × 4 × `PEAK_FACTOR`），向进程级闸门要额度；额度不够就
等，解完立刻还。小图（2K 背景 ≈ 33 MB）可以很多张同时过；8K 立绘自然被压到
1–2 张。所有解码入口（缩略图 `app.thumbs.decode_sync`、预热
`app.prewarm`、舞台 `app.stage_render.load_scaled`、分层合成
`app.layer_compose._load_layer_image` 的整图退路）共用**同一道**闸门 ——
不是每个面板各限各的（那样 4 个面板 = 4 倍峰值）。

**为什么不做超时**：真正的死锁只可能来自"同一线程嵌套解码"，当前没有这种
调用；超时会把"内存告急"变成"偶发解码失败"，更难查。关闭窗口时 worker 线程
被 Qt 回收，闸门里的等待也随之结束。

可调：`STORYGRAPH_DECODE_BUDGET_BYTES` / `STORYGRAPH_DECODE_BUDGET_MB`
（默认 640 MB ≈ 2 张 8K 立绘同时解；`0` = 不限制）。

**实测**（`.pip-tmp/probe_r29_decode_peak.py` / `probe_r30_prewarm_mem.py`）：

    · 12 张 8K 立绘、16 线程：峰值 2 751 MB → 闸门（640 MB）580 MB；
    · 32 张预热（两个尺寸）：640 MB 时 11.3s / 582 MB；
      调到 1 GB 时 6.4s / 1 113 MB（要快就调大，代价就是那个峰值）；
    · 预热同一张图只解一遍（两个尺寸从一次解码里缩）：解码次数减半。
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager

#: 默认预算：两张 8K 立绘（实测峰值 ~580 MB，比 16 线程的 2.75 GB 低 4.7 倍）。
#: 嫌预热慢可以调大：`STORYGRAPH_DECODE_BUDGET_MB=1024` → 三张同时解，
#: 预热快一倍、峰值回到 ~1.1 GB。内存紧就调到 384（单张大图也能过）。
DEFAULT_BUDGET_BYTES = 640 * 1024 * 1024

#: 一张图的解码峰值约为"RGBA 字节数"的几倍。实测 4320×7680（133 MB）
#: 峰值 ~270 MB → 2.02×；取 2.0（略保守的那一侧由预算兜着）。
PEAK_FACTOR = 2.0

#: 读不到尺寸时按这个算（宁可排队，别挤爆）。2K 背景的 4 倍。
FALLBACK_COST = 32 * 1024 * 1024

BYTES_ENV = 'STORYGRAPH_DECODE_BUDGET_BYTES'
MB_ENV = 'STORYGRAPH_DECODE_BUDGET_MB'

#: 文件头尺寸缓存（只读 header，~1ms/张）：`路径 → (w, h)`。
_SIZE_CACHE: dict = {}
_SIZE_LOCK = threading.Lock()
_SIZE_LIMIT = 1024


def _env_int(name: str, scale: int = 1):
    raw = (os.environ.get(name) or '').strip()
    if not raw:
        return None
    try:
        return max(0, int(float(raw) * scale))
    except (TypeError, ValueError):
        # 环境变量写坏不该炸界面；按"没设"处理（下一行的默认值兜底）。
        return None


def budget_bytes() -> int:
    """当前生效的预算（字节）；`0` = 不限制。"""
    got = _env_int(BYTES_ENV)
    if got is None:
        got = _env_int(MB_ENV, 1024 * 1024)
    return DEFAULT_BUDGET_BYTES if got is None else got


def _size_of(path):
    """`(w, h)` 或 None（只读文件头，不解整图）。"""
    key = str(path)
    with _SIZE_LOCK:
        hit = _SIZE_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        from PIL import Image
    except ImportError:                       # 没装 Pillow：闸门用不了，别拖垮调用方
        return None
    try:
        with Image.open(key) as im:
            size = (int(im.width), int(im.height))
    except (OSError, ValueError):             # 坏图 / 不是图片：当作"不知道大小"
        return None
    if size[0] > 0 and size[1] > 0:
        with _SIZE_LOCK:
            if len(_SIZE_CACHE) >= _SIZE_LIMIT:
                _SIZE_CACHE.clear()
            _SIZE_CACHE[key] = size
        return size
    return None


def cost_of(path, factor: float = None) -> int:
    """估这一张图解码要占多少字节（读不到尺寸就用 `FALLBACK_COST`）。"""
    size = _size_of(path)
    if size is None:
        return FALLBACK_COST
    mult = PEAK_FACTOR if factor is None else max(0.0, float(factor))
    return max(1, int(size[0]) * int(size[1]) * 4 * mult)


class DecodeBudget:
    """
    按字节预算放行的闸门（线程安全，可被任意 worker 线程调用）。

    公平性：**先到先得**（`Condition.wait` 的顺序由解释器保证），但没有
    "大图优先"——大图要的额度大、本来就更难凑齐，再给它插队会让小图饿死。
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._used = 0
        self._explicit = None
        self._peak = 0
        self._blocks = 0
        self._granted = 0

    # -- 配置 ----------------------------------------------------------

    def budget(self) -> int:
        with self._cond:
            return (self._explicit if self._explicit is not None
                    else budget_bytes())

    def configure(self, max_bytes=None) -> int:
        """显式设预算（`None` = 回到环境变量/默认值）；返回生效的预算。"""
        with self._cond:
            self._explicit = (None if max_bytes is None
                              else max(0, int(max_bytes)))
            self._cond.notify_all()
            return (self._explicit if self._explicit is not None
                    else budget_bytes())

    # -- 申请 / 归还 ---------------------------------------------------

    def acquire(self, cost) -> int:
        """
        等够额度再返回（返回真正记账的字节数）；`cost <= 0` 直接放行。

        规则：`used == 0` 时**任何**一张都放行（单张超预算也不许永远卡住）；
        否则必须 `used + cost <= budget`。预算 `0` = 不限。
        """
        cost = max(0, int(cost))
        if cost <= 0:
            return 0
        with self._cond:
            limit = (self._explicit if self._explicit is not None
                     else budget_bytes())
            while limit > 0 and self._used and self._used + cost > limit:
                self._blocks += 1
                self._cond.wait()
                limit = (self._explicit if self._explicit is not None
                         else budget_bytes())
            self._used += cost
            self._granted += 1
            self._peak = max(self._peak, self._used)
            return cost

    def release(self, cost) -> None:
        cost = max(0, int(cost))
        if cost <= 0:
            return
        with self._cond:
            self._used = max(0, self._used - cost)
            self._cond.notify_all()

    # -- 诊断 ----------------------------------------------------------

    def info(self) -> dict:
        with self._cond:
            limit = (self._explicit if self._explicit is not None
                     else budget_bytes())
            return {'used': self._used, 'budget': limit, 'peak': self._peak,
                    'blocks': self._blocks, 'granted': self._granted}

    def reset(self) -> None:
        """清账（测试用；正常运行时不需要）。"""
        with self._cond:
            self._used = 0
            self._peak = 0
            self._blocks = 0
            self._granted = 0
            self._cond.notify_all()


_GATE = DecodeBudget()


def info() -> dict:
    """当前闸门状态（诊断/自检用）：`used/budget/peak/blocks/granted`。"""
    return _GATE.info()


def configure(max_bytes=None) -> int:
    """显式设预算（`None` = 回到环境变量/默认值）；测试与"省内存模式"用。"""
    return _GATE.configure(max_bytes)


def reset() -> None:
    """清账 + 回到默认预算（测试用）。"""
    _GATE.configure(None)
    _GATE.reset()


@contextmanager
def decoding(cost):
    """`with decoding(cost): …` —— 拿到额度才解码，出块立刻归还。"""
    got = _GATE.acquire(cost)
    try:
        yield
    finally:
        _GATE.release(got)


def acquire(cost) -> int:
    """直接要额度（配 `release` 用；异常分支多、不好整段包进 `with` 的地方）。"""
    return _GATE.acquire(cost)


def release(cost) -> None:
    """归还 `acquire` 要到的额度（`finally` 里调，绝不漏）。"""
    _GATE.release(cost)


__all__ = ['BYTES_ENV', 'DEFAULT_BUDGET_BYTES', 'DecodeBudget', 'FALLBACK_COST',
           'MB_ENV', 'PEAK_FACTOR', 'acquire', 'budget_bytes', 'configure',
           'cost_of', 'decoding', 'info', 'release', 'reset']
