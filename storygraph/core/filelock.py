# -*- coding: utf-8 -*-
"""
storygraph.core.filelock
========================
**跨进程**的"这份文件现在归我改"—— 一把按路径派生的咨询锁。

## 为什么需要它（2026-11-07 第七轮）

`ext.registry.save()` 已经改成三方合并：装载时记下 `base`，落盘前读一次
`theirs`，按 id 判"谁改过"。但那仍然有**一个人眼看不见的空隙**：

    进程甲：读 theirs ────────────────► 写
    进程乙：      读 theirs ────────────────► 写
                   ↑ 两边都以为"别人没动"，后写的把先写的抹掉

空袭很短（读+合并+写是毫秒级），但"两个人同时点保存"是真会发生的
（两个窗口、两个 agent、编辑器插件 + 图形界面）。合并把窗口从"打开到保存"
缩到"读→写"，锁把最后这点也盖住。

## 协议（就这么几条）

  · **锁文件的位置**：用户缓存目录 `…/StoryGraph/locks/<sha1(路径)>.lock`，
    **不写进用户项目**（项目里多一个 `.lock` 会被当成"工具留下的垃圾"）；
  · **锁是操作系统级的**（Windows `msvcrt.locking` / POSIX `flock`）：
    进程崩了、被 kill 了，锁由系统释放 —— **没有"僵尸锁"要人手动删**；
  · 拿不到就**等**（默认 5 秒，每 20ms 试一次），等不到抛 `LockTimeout`；
    调用方负责翻成人话（"另一个进程正在写，请稍后再存"），**不许静默跳过**；
  · 锁的粒度是**一个文件**，不是一把全局锁：`ext.json` 与 `rules.json`
    互不相干，两边的写者不该互相排队。

## 不做什么

  · 不做"读也加锁"：读者只会读到旧版或新版（写是原子的），不会读到半截；
  · 不做"锁文件清理"：锁文件是常驻的、空的，删它反而会制造竞态
    （甲持有、乙删掉、丙建了新的 → 甲和丙同时"持有"）；
  · 不保证跨机器（网络盘上的 byte-range 锁语义各家不同）—— 那是另一个协议，
    本项目只承诺**同一台机器**。
"""
from __future__ import annotations

import hashlib
import os
import time
from contextlib import contextmanager
from pathlib import Path

#: 环境变量：换锁目录（测试用来隔离；生产不用设）。
ENV_LOCK_DIR = 'STORYGRAPH_LOCK_DIR'

#: 默认等多久（秒）。写一份 JSON 是毫秒级的事，5 秒足够排上队。
DEFAULT_TIMEOUT = 5.0

#: 重试间隔（秒）。20ms ≈ 一次 Windows 文件操作的开销，再小只是空转。
POLL_INTERVAL = 0.02


class LockTimeout(OSError):
    """等锁超时。`path` 是被锁**那个文件**（不是锁文件）。"""

    def __init__(self, path, waited: float):
        self.path = str(path)
        self.waited = float(waited)
        super().__init__(f'另一个进程正在写 {Path(str(path)).name}'
                         f'（等了 {self.waited:.1f}s）')


def lock_dir() -> Path:
    """
    锁文件放哪：`STORYGRAPH_LOCK_DIR` → `%LOCALAPPDATA%` → `XDG_CACHE_HOME`
    → `~/.cache`，再拼 `StoryGraph/locks`。

    与 `assets.engine_images.cache_dir()` 同一套来源（用户级缓存，不进项目）。
    """
    override = str(os.environ.get(ENV_LOCK_DIR) or '').strip()
    if override:
        return Path(override)
    base = (os.environ.get('LOCALAPPDATA')
            or os.environ.get('XDG_CACHE_HOME')
            or str(Path.home() / '.cache'))
    return Path(base) / 'StoryGraph' / 'locks'


def lock_path_for(target) -> Path:
    """
    被锁文件 → 锁文件路径（**唯一映射**：同一个文件永远同一个锁）。

    键用**绝对路径 + 大小写归一**（Windows 不区分大小写：`C:\\A.json` 与
    `c:\\a.json` 必须是同一把锁）；盘符/路径分隔符先归一，再取 sha1。
    """
    text = str(Path(str(target)).expanduser().resolve())
    key = text.replace('\\', '/').lower() if os.name == 'nt' else text
    digest = hashlib.sha1(key.encode('utf-8')).hexdigest()[:24]
    return lock_dir() / f'{digest}.lock'


def _try_lock(handle) -> bool:
    """试一次（非阻塞）。拿不到返回 False，不抛。"""
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


@contextmanager
def file_lock(target, *, timeout: float = DEFAULT_TIMEOUT):
    """
    `with file_lock(某个文件):` —— 期间同一个文件只许本进程改。

    拿不到（等到 `timeout` 秒）→ 抛 `LockTimeout`。**不静默放行**：
    放行的代价是两个进程都以为自己写成功了，而文件里只剩一份。

    **放锁 = 关句柄**（Windows `msvcrt` 与 POSIX `flock` 都是：关闭文件 /
    进程结束时由系统释放）。所以 `finally` 只做 `handle.close()` —— 显式
    `LK_UNLCK` 是再走一遍同一条路，却要一个宽泛 except 去兜（又一笔静默吞）。
    """
    path = lock_path_for(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    # `a+b`：文件不存在就建（锁文件永远是空的），存在就打开、绝不截断
    handle = open(path, 'a+b')                # noqa: SIM115 - 要跨 yield 持有
    started = time.monotonic()
    try:
        deadline = started + max(0.0, float(timeout))
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise LockTimeout(target, time.monotonic() - started)
            time.sleep(POLL_INTERVAL)
        yield path
    finally:
        # 关句柄 = 放锁（见上面的说明）。**先结束 `with` 再关**：
        # 调用方还在缩进里的时候，锁必须仍然归它。
        handle.close()


__all__ = ['DEFAULT_TIMEOUT', 'ENV_LOCK_DIR', 'LockTimeout', 'POLL_INTERVAL',
           'file_lock', 'lock_dir', 'lock_path_for']
