# -*- coding: utf-8 -*-
"""
storygraph.core.versions
========================
**一份文件的自动备份世代**：看得见、挑得中、放得回。

## 为什么要有它（2026-11-07 第十轮）

`core.atomicio` 从第一天起就在每次覆盖前留一份 `<名>.bak`（多代，最多
`KEEP_BACKUPS` 份）。可是"留着"和"用得上"是两件事 —— 探针
`.pip-tmp/probe_r10_versions.py` 量到的现实是：

  · 那几代备份**确实**是每次保存之前的逐字节原话（连 CRLF 都对）；
  · 但界面上没有任何一处读得到它们（`ui_qt/` 里出现的 `.bak` 全是提示文字）；
  · 于是"退回去"只能靠人打开资源管理器、挑一个 `.bak`、整份复制过去 ——
    而**整份复制不产生备份**：刚被顶掉的那一版当场没了（单向门）。

第九轮给扩展窗口做了「撤销上次保存」，只管**本窗口这一次**，关窗即失效。
这个模块补的是另一半：不管窗口在不在，**磁盘上那几代都能翻、能预览、
能放回来**，而且放回来之前那份**自动进新一代备份**（所以放错了还能再放回去）。

## 三件事，三处真源

  · 有哪些世代     → 目录里 `<名>.bak` 与 `<名>.bak-*`（**不猜**命名，也不
    自己造备份 —— 造备份是 `atomicio` 的事，这里只读它留下的）；
  · 某一代是什么   → `read_text_exact`（**逐字节**；换行不翻译，否则
    "放回原样"只是看起来像）；
  · 放回去怎么写   → `write_text_atomic(backup=True)`（原子 + 先把现在这份
    备份掉），并且**整段在 `core.filelock` 里**：读现在 → 比对 → 写回是一件事。

## 一道闸：`expect_current`

浏览器打开时读到的"现在这份"由调用方记着，放回时原样传回来。文件在那之后
被别的窗口 / 别的进程改过 → **一个字都不写**，如实说"文件变了"。这是同一个
老道理：拿一份旧文本盖掉别人的改动，正是这个工具一直在防的那件事。
（`_UNCHECKED` 是"不校验"的哨兵 —— 不能用 `None` 表示，因为"当时文件不存在"
本身就是一个合法的期望值。）
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from glob import escape
from pathlib import Path

from storygraph.core.atomicio import KEEP_BACKUPS, read_text_exact, \
    write_text_atomic
from storygraph.core.filelock import DEFAULT_TIMEOUT, LockTimeout, file_lock

#: "不校验现在这份"的哨兵。`None` 是**合法期望值**（= 当时文件不存在）。
_UNCHECKED = object()

#: 备份名里"这一代"的两种长相（见 `core.atomicio` 的模块文档）：
#: 固定名 `<名>.bak` 是第一代（老读者按它找），之后的带时间戳与序号。
KIND_FIXED = 'fixed'
KIND_STAMPED = 'stamped'


@dataclass(frozen=True)
class Version:
    """磁盘上的一代备份。`written` = 这份内容**被换掉的那一刻**（mtime）。"""

    path: Path
    kind: str
    written: float
    size: int

    @property
    def name(self) -> str:
        return self.path.name


def _stamped_paths(target: Path) -> list:
    """`<名>.bak-<时间戳>-<序号>` 这些（**不含**固定名那一代）。

    `glob.escape`：文件名里带 `[` `*` 时，`glob` 会把它当通配符 ——
    那会把**别人的**文件当成这一份的备份列出来。
    """
    return list(target.parent.glob(f'{escape(target.name)}.bak-*'))


def backups_of(path, *, limit: int = 0) -> list:
    """
    这份文件的备份世代 → `[Version]`，**新的在前**。

    只认两种名字（`<名>.bak` / `<名>.bak-*`），按 **mtime** 排 —— 名字里的
    时间戳只有"带时间戳那几代"才有，而固定名那一份可能比谁都新（第一代）
    也可能最老（原件），按名字排会把顺序弄反。mtime 相同（同一 tick 写两份）
    时按名字兜一层，保证顺序是确定的。

    `limit > 0` 时只回前 `limit` 代。
    """
    target = Path(path)
    fixed = target.with_name(target.name + '.bak')
    cands = ([fixed] if fixed.is_file() else []) + _stamped_paths(target)
    out = []
    for cand in cands:
        try:
            st = cand.stat()
        except OSError:
            continue                        # 刚刚被删 / 无权读：跳过，不炸
        if not cand.is_file():
            continue
        out.append(Version(path=cand,
                           kind=KIND_FIXED if cand == fixed else KIND_STAMPED,
                           written=st.st_mtime, size=st.st_size))
    out.sort(key=lambda v: (v.written, v.name), reverse=True)
    return out[:limit] if limit and limit > 0 else out


def stamp_text(written: float) -> str:
    """mtime → 人看的时刻（本地时间，到分钟就够）。"""
    try:
        return datetime.fromtimestamp(float(written)).strftime('%Y-%m-%d %H:%M')
    except (OverflowError, OSError, ValueError):
        return '?'


def generations_hint() -> str:
    """一句人话：工具最多留几代（界面提示与文档共用这一句）。"""
    return f'工具每次写盘前自动留一份备份，最多留 {KEEP_BACKUPS} 代'


def restore(target, version, *, expect_current=_UNCHECKED,
            lock_timeout: float = DEFAULT_TIMEOUT) -> dict:
    """
    把 `version` 那一代的**原话**放回 `target`（放回前先把现在这份备份掉）。

    返回 `{'ok','reason','path','version','same','changed','locked','written'}`：

      · `same: True` —— 那一代和现在**逐字节一样**，什么都没写（也不产生新
        备份：不制造噪音，`unchanged` 与 `ext.registry.save` 同一条口径）；
      · `changed: True` —— 现在这份和调用方看到的那份不一样（别人改过 / 删了），
        **一个字没写**；
      · `locked: True`  —— 别的进程正拿着锁，这次**没写**，稍后再试。

    `expect_current` 给 `_UNCHECKED`（默认）= 不校验现在这份；给字符串 / `None`
    = 必须是那副样子才动手（`None` 表示"当时这个文件不存在"）。
    """
    target_path, version_path = Path(target), Path(version)
    try:
        with file_lock(target_path, timeout=lock_timeout):
            return _restore_locked(target_path, version_path, expect_current)
    except LockTimeout as e:
        return {'ok': False, 'path': str(target_path),
                'version': str(version_path), 'same': False, 'changed': False,
                'locked': True, 'written': False,
                'reason': f'{target_path.name} 现在由另一个进程在写（{e}），'
                          f'这次**没有放回**，请稍后再试'}


def _refuse(target: Path, version: Path, reason: str, **flags) -> dict:
    """统一的"没放回"回执（`ok: False` + 一句人话）。"""
    return {'ok': False, 'path': str(target), 'version': str(version),
            'same': False, 'changed': False, 'locked': False,
            'written': False, 'reason': reason, **flags}


def _restore_locked(target: Path, version: Path, expect_current) -> dict:
    """`restore()` 的主体（**已经在锁里**：读现在 → 比对 → 写回）。"""
    try:
        current = read_text_exact(target)
    except OSError as e:
        return _refuse(target, version,
                       f'{target.name} 读不出来（{e}），这次**没有放回**')
    if expect_current is not _UNCHECKED and current != expect_current:
        return _refuse(target, version,
                       f'{target.name} 在你打开这份清单之后又被改过（或删了），'
                       f'为了不覆盖别人，这次**没有放回**', changed=True)
    try:
        old = read_text_exact(version)
    except OSError as e:
        return _refuse(target, version,
                       f'那一版读不出来（{e}），这次**没有放回**')
    if old is None:
        return _refuse(target, version,
                       f'那一版已经不在磁盘上了（{version.name}），'
                       f'这次**没有放回**')
    if old == current:
        return {'ok': True, 'path': str(target), 'version': str(version),
                'same': True, 'changed': False, 'locked': False,
                'written': False,
                'reason': f'那一版和现在**逐字节一样**，什么都没改'}
    try:
        write_text_atomic(target, old, backup=True)
    except (OSError, ValueError) as e:
        return _refuse(target, version,
                       f'{target.name} 写不回去（{e}），这次**没有放回**')
    return {'ok': True, 'path': str(target), 'version': str(version),
            'same': False, 'changed': False, 'locked': False, 'written': True,
            'reason': ''}


__all__ = ['KIND_FIXED', 'KIND_STAMPED', 'Version', 'backups_of',
           'generations_hint', 'restore', 'stamp_text']
