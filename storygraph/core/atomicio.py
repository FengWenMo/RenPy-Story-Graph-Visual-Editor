"""
storygraph.core.atomicio
========================
**写盘只有这一条路**：原子替换、逐字节、多代备份、失败即中止。

为什么要有这个模块（2026-11-01，外部审查 P0）
--------------------------------------------
审查报告里最重的一条不是"哪个功能不好用"，而是**保护的不对称**：

    · `.rpy` 的写盘又原子又备份又过门禁；
    · Excel 的写盘是 `wb.save(path)` —— 直接覆盖、写一半崩了就是半截文件、
      没有备份、不校验后缀。整个项目 19 处 `wb.save()`，一处都没走
      `story.paths.write_text_atomic` 那条已经写好的路。

后果是实测出来的：`collab import-ir --sheet <某个.rpy>` 把一个 1488 字节的
剧本覆盖成 15481 字节的 Excel，而且**先覆盖后校验**。

所以这里把"怎么写一个文件"收敛成**一份**实现，`.rpy` 和 `.xlsx` 都走它：

  ① 同目录临时文件（`os.replace` 要求同一个卷；`uuid` 让同进程两个线程
     写同一个目标不会撞名）；
  ② 写的时候**不做任何换行翻译**（Windows 上 `newline=None` 会把每个
     `\\n` 翻成 `\\r\\n`，"逐字节不变"当场破功）；
  ③ `flush` + `os.fsync` —— 逼系统真写进盘，不停在缓冲区里；
  ④ 目标已存在且要备份 → 先备份。**备份失败就中止**，绝不"备份没成照样覆盖"
     （用户以为有退路，其实没有 —— 那是比不备份更坏的状态）；
  ⑤ `os.replace` 一步换名：要么整个旧文件，要么整个新文件。

  ⑥ **后缀白名单**：Excel 的保存只认 `.xlsx`。传一个 `.rpy` 进来直接拒绝 ——
     这是"一个参数毁掉剧本"那条路的最后一道闸。

备份为什么是多代的（与 `story.paths` 里那条一致）
--------------------------------------------------
固定名 `<名>.bak` **只留一代**：连导两次，第一代的备份被第二代盖掉 ——
而第一代往往才是**用户手写的那份原件**。所以：
  · 第一次备份用固定名 `<名>.bak`（老读者按这个名找得到）；
  · 已有 `<名>.bak` 时，新一代带时间戳 `<名>.bak-YYYYmmdd-HHMMSS-ffffff`；
  · 只保留最近 #KEEP_BACKUPS 份（含固定名那份）—— 无限长下去会把用户的
    目录塞满，那是另一种形式的破坏。
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime
from glob import escape
from itertools import count
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 备份名的进程内序号（同一微秒里的第二代、第三代靠它区分）
_BACKUP_SEQ = count(1)

#: 备份最多留几份（含固定名 `<名>.bak` 那一份）
KEEP_BACKUPS = 5

#: Excel 保存认的后缀。**只认这一个** —— 见 `save_workbook_atomic`。
WORKBOOK_SUFFIXES = ('.xlsx',)

#: `os.replace` 撞上"这一刻正被别人占着"时的重试（2026-11-01，地狱测试实测）
#:
#: 背景：同一进程里两个线程写同一个目标时，Windows 的 `MoveFileEx` 会偶发
#: `PermissionError(13)` —— 不是"文件真的被关着"，只是两条替换撞在同一毫秒。
#: 不重试的话，那次写入**静默失败**（调用方通常 `except OSError` 兜住），
#: 用户看到的只是"保存了但没变"。6 次 × 20ms = 最多 120ms：够穿过一次
#: 短兵相接，又不至于让"Excel 真开着这个文件"多等太久（它照样会失败，
#: 然后把 `human_io_error` 那句人话交出去）。
REPLACE_ATTEMPTS = 6
REPLACE_DELAY = 0.02


class RefusedWrite(ValueError):
    """
    写盘被**主动**拒绝（不是磁盘/权限问题）。

    继承 `ValueError`：调用方既有的 `except OSError` 不会被误触发，
    而 `except ValueError` / `except Exception` 都能接住它。
    """


def _tmp_beside(target: Path, kind: str = 'tmp') -> Path:
    """目标同目录的唯一临时名（同卷 + 同进程多线程不撞名）。"""
    return target.with_name(
        f'.{target.name}.{kind}-{os.getpid()}-{uuid.uuid4().hex[:8]}')


def _fsync_file(path: Path) -> None:
    """
    把已经写好的文件逼到磁盘上（打不开就抛，让调用方决定怎么报）。

    ⚠ 必须用 `'rb+'`：Windows 上**只读句柄调 `os.fsync` 会报
    `OSError: [Errno 9] Bad file descriptor`**（实测，见 tests/test_atomic_io.py）。
    刚写完的文件本来就可写，用读写句柄没有任何副作用。
    """
    with open(path, 'rb+') as fh:
        os.fsync(fh.fileno())


def _replace_with_retry(src, dst, *, attempts: int = None,
                        delay: float = None) -> None:
    """
    `os.replace`，撞上**瞬时的** `PermissionError` 就退让一下再来。

    为什么（2026-11-01，`tests/test_hell_atomic_io.py` 实测）：八个线程各写
    6 次同一个 `.rpy`，不重试时 5 次 `os.replace` 直接 `PermissionError` ——
    文件没坏，但**那一次保存没了**，而调用方多半 `except OSError` 兜住。
    "并发时偶尔丢一次保存"是最难查的那种坏。

    只对 `PermissionError` 重试：磁盘满（ENOSPC）、路径不存在（ENOENT）
    这类重试也没用，立刻抛出去，让人看见真正的原因。
    """
    n = REPLACE_ATTEMPTS if attempts is None else max(1, int(attempts))
    wait = REPLACE_DELAY if delay is None else max(0.0, float(delay))
    last = None
    for i in range(n):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            last = exc
            if i + 1 < n and wait:
                time.sleep(wait)
    raise last


def _backup_base(target: Path, where=None) -> Path:
    """
    这一代备份的**固定名**该落在哪儿。

    `where=None` → 紧挨着目标（`<名>.bak`，老读者按这个名字找）；给了
    `where` → 落到那个目录里的同名备份（2026-11-08，见 `story/version_store.py`：
    脚本原地小改频率极高，**不能**往 `game/` 里一代代堆 `.rpy.bak`，
    但旧版本又必须有 —— 于是挪到项目自己的 `.storygraph/versions/` 里）。
    """
    if where is None:
        return target.with_name(target.name + '.bak')
    return Path(where) / (target.name + '.bak')


def _unique_backup_path(target: Path, where=None) -> Path:
    """
    一个**还没被占用**的备份名：固定名 → 带时间戳 + 本进程序号。

    为什么要有序号（2026-11-01，地狱测试实测）：`datetime.now()` 的精度在
    Windows 上可能粗到一个 tick —— 同一微秒写两次，时间戳**一模一样**，
    第二次的 `os.replace` 就把第一代备份**盖掉**了。`KEEP_BACKUPS=5`
    于是名存实亡。而第一代往往才是用户手写的原件。

    序号取自**模块级计数器**：同进程里两个线程同时进来也拿不到同一个名字
    （`next()` 是原子的）；跨进程撞名还要"同一微秒 + 同一序号"，而且下面
    还有 `exists()` 兜一层。
    """
    bak = _backup_base(target, where)
    if not bak.exists():
        return bak
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    while True:
        seq = next(_BACKUP_SEQ)
        cand = bak.with_name(f'{bak.name}-{stamp}-{seq:04d}')
        if not cand.exists():
            return cand


def _backup_candidates(target: Path, where=None) -> list:
    """已有的备份，**新的在前**（固定名那份排第一 = 永远不会被裁掉）。"""
    fixed = _backup_base(target, where)
    stamped = sorted(fixed.parent.glob(escape(fixed.name) + '-*'), reverse=True)
    return ([fixed] if fixed.exists() else []) + stamped


def _age_seconds(path: Path, now: float):
    """这一代离现在多少秒（读不到 stat → `None`，调用方当"不参与台阶"）。"""
    try:
        return max(0.0, now - path.stat().st_mtime)
    except OSError:
        return None


def _kept_backups(olds, *, fixed, keep: int, keep_ages=()) -> set:
    """
    从"新的在前"的候选里挑出**该留的**那一批（其余的会被删）。

    两种规矩叠在一起：

      · 最新的 `keep` 份（含固定名那份）永远留；
      · `keep_ages` 是**时间台阶**（秒）：每个台阶留"最新的、已经比它更旧"
        的那一份。

    为什么需要台阶（2026-11-08 第十二轮，探针 `.pip-tmp/probe_r12_window.py`）：
    脚本是高频改的东西 —— 连改 30 次，5 代备份的时间跨度只有 **1.1 秒**；
    跨几天各改十次之后，"昨天关工具之前那一版"早就被裁掉了。而用户真正的
    诉求常常是"回到昨天"，不是"回到十分钟前"。

    ⚠ 台阶按 **年龄** 算，不按"日历上的哪一天"：用户昨天关工具之前那一版，
    是在**今天第一次保存时**才被写进备份的（那时它才被换掉），mtime 是今天。
    按日历分组会把"昨天那一版"算成今天的，一裁就没 —— 第一版就是这么写错的。
    """
    kept, taken = set(), 0
    for cand in olds:                       # 新的在前
        if cand == fixed or taken < keep:   # 固定名那份 = 原件，永远留
            kept.add(cand)
            taken += 1
    if keep_ages:
        now = time.time()
        # ⚠ 台阶只在**带时间戳的**那几代里挑：固定名那份永远留着，而且它
        # 一定是最老的一代 —— 让它参与挑台阶，每个台阶都会落在它身上，
        # 等于台阶全废（第一版就是这么写错的：跨天的版本一个都没留下）。
        stamps = [c for c in olds if c != fixed]
        for step in sorted(float(s) for s in keep_ages):
            for cand in stamps:             # 新的在前 → 第一个够老的正好是台阶上最新的
                age = _age_seconds(cand, now)
                if age is not None and age >= step:
                    kept.add(cand)
                    break
    return kept


def backup_file(path, *, keep: int = KEEP_BACKUPS, where=None,
                keep_ages=()) -> Path:
    """
    复制一份 `<名>.bak`（多代）；目标不存在 → 返回 `None`。

    `where` 给了就落到那个目录（**先建目录**），不给就紧挨着目标 ——
    两种落点共用同一套命名与裁剪规则，见 `_backup_base`。
    `keep_ages` 见 `_kept_backups`（脚本的历史版本用时间台阶跨天留档）。

    ⚠ **失败直接抛 `OSError`，不吞**：调用方必须据此中止整次写入。
    磁盘满 / 权限不足 / 杀毒软件占着文件时，用户以为有 `.bak`、其实没有 ——
    那正是"用户以为安全、实际不安全"的陷阱。
    """
    target = Path(path)
    if not target.is_file():
        return None
    if where is not None:
        Path(where).mkdir(parents=True, exist_ok=True)
    bak = _unique_backup_path(target, where)
    tmp = _tmp_beside(bak, 'bak')
    try:
        with open(target, 'rb') as src, open(tmp, 'wb') as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        _replace_with_retry(tmp, bak)
    finally:
        try:
            tmp.unlink()
        except OSError as exc:          # noqa: BLE001
            swallow('core.atomicio.cleanup_tmp', exc)
    _prune_backups(target, keep=keep, where=where, keep_ages=keep_ages)
    return bak


def _prune_backups(target: Path, *, keep: int = KEEP_BACKUPS,
                   where=None, keep_ages=()) -> None:
    """
    只留该留的那些。**清理失败不算写入失败**（最坏是多个备份文件）。

    固定名那份永远留着：它可能是用户手写的原件，也是"按 `.bak` 找备份"
    的人唯一认得的名字。该留几份见 `_kept_backups`。
    """
    try:
        olds = _backup_candidates(target, where)
        keepers = _kept_backups(olds, fixed=_backup_base(target, where),
                                keep=keep, keep_ages=keep_ages)
        for extra in olds:
            if extra in keepers:
                continue
            try:
                extra.unlink()
            except OSError as exc:      # noqa: BLE001
                swallow('core.atomicio.prune_backup', exc)
    except OSError as exc:                  # noqa: BLE001
        swallow('core.atomicio.prune_backups', exc)


def write_bytes_atomic(path, data: bytes, *, backup: bool = False,
                       keep: int = KEEP_BACKUPS, backup_dir=None) -> Path:
    """
    **逐字节**写 `data` 进 `path`：原子替换 + 可选备份（多代）。

    与 `write_text_atomic` 同一条路，只是不做编码 —— 备份和"原样复制"
    走这条（复制时编码/换行/BOM 一个都不该动）。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_beside(target)
    try:
        with open(tmp, 'wb') as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        # 备份在 `os.replace` **之前**：备份失败 → 异常往上抛 → 目标文件
        # 一个字节没动。反过来（先替换再备份）就等于"备份失败了也覆盖了"。
        if backup:
            backup_file(target, keep=keep, where=backup_dir)
        _replace_with_retry(tmp, target)
    except BaseException:                   # noqa: BLE001 - 清理后原样抛出
        try:
            tmp.unlink()
        except OSError as exc:          # noqa: BLE001
            swallow('core.atomicio.cleanup_tmp', exc)
        raise
    return target


def write_text_atomic(path, text: str, *, encoding: str = 'utf-8',
                      backup: bool = False, keep: int = KEEP_BACKUPS,
                      backup_dir=None, keep_ages=()) -> Path:
    """
    把文本**逐字节**写进 `path`（`newline=''`，不翻译换行）。

    `backup_dir`：这一代备份落到**别的目录**（默认紧挨着目标）。脚本的
    原地小改走这条 —— 见 `story/version_store.py` 里的理由。
    `keep_ages`：时间台阶（见 `_kept_backups`），只有脚本的历史版本用。

    细节（为什么不能直接 `Path.write_text`）见模块文档与
    `story.paths.write_text_atomic` 的历史注释。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_beside(target)
    try:
        with open(tmp, 'w', encoding=encoding, newline='') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if backup:
            backup_file(target, keep=keep, where=backup_dir,
                        keep_ages=keep_ages)
        _replace_with_retry(tmp, target)
    except BaseException:                   # noqa: BLE001 - 清理后原样抛出
        try:
            tmp.unlink()
        except OSError as exc:          # noqa: BLE001
            swallow('core.atomicio.cleanup_tmp', exc)
        raise
    return target


def read_text_exact(path, *, encoding: str = 'utf-8'):
    """
    文件里的**原话**（`newline=''`：换行一个字节都不翻译）。不存在 → `None`。

    `write_text_atomic` 的对称另一半：写不翻译换行，读也不翻译。用
    `Path.read_text()` 读、再经 `write_text_atomic` 写回去，CRLF 文件会被
    悄悄换成 LF —— 那就不叫"原样"了（用户拿 `git diff` 一眼就能看见满屏改动）。

    ⚠ **除"不存在"以外的读失败往上抛**：把读不出来的文件当成"空文件"，
       就是在静默丢掉它（调用方应当据此**拒绝覆盖**）。
    """
    try:
        with open(path, 'r', encoding=encoding, newline='') as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def save_workbook_atomic(wb, path, *, backup: bool = True,
                         allowed_suffixes=WORKBOOK_SUFFIXES,
                         keep: int = KEEP_BACKUPS) -> Path:
    """
    Excel 保存：**先校验后缀，再备份，再原子替换**。

    为什么必须是这一个函数（审查 P0-2）：
      · `wb.save(path)` 直接覆盖 —— 写一半崩了就是**半截 xlsx**，
        用户下次打开只会看到"文件已损坏"，而原件已经没了；
      · 不校验后缀 —— 传一个 `.rpy` 进来，剧本就被 Excel 字节覆盖
        （实测：1488 字节的 rpy → 15481 字节的 xlsx，退回不去）；
      · 不备份 —— 覆盖前没有任何退路。

    `allowed_suffixes=None` 表示**不校验**（只给"这一层已经自己确认过、
    或写的是临时文件"的调用方用；默认永远校验）。
    """
    target = Path(path)
    if allowed_suffixes is not None:
        suffix = target.suffix.lower()
        allowed = tuple(s.lower() for s in allowed_suffixes)
        if suffix not in allowed:
            raise RefusedWrite(
                f'拒绝把 Excel 写成 {target.name!r}：'
                f'只允许 {" / ".join(allowed)}。'
                f'（这里传进来的路径不是表格 —— 覆盖它会把原来的文件毁掉）')
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_beside(target, 'xlsx')
    try:
        wb.save(str(tmp))
        _fsync_file(tmp)
        if backup:
            backup_file(target, keep=keep)
        _replace_with_retry(tmp, target)
    except BaseException:                   # noqa: BLE001 - 清理后原样抛出
        try:
            tmp.unlink()
        except OSError as exc:          # noqa: BLE001
            swallow('core.atomicio.cleanup_tmp', exc)
        raise
    return target


def human_io_error(exc, path) -> str:
    """
    `OSError` → 一句编剧也看得懂的中文（表还在，一个字没动）。

    为什么放在 core：`.rpy` 和 `.xlsx` 两条写盘路都要用同一套说法，
    各写一份必然漂移（"这个说被占用、那个说权限不足"）。
    """
    p = Path(path)
    base = f'写不进 {p.name}：'
    if isinstance(exc, PermissionError):
        return (base + '这个文件可能正被 Excel / WPS 打开着（也可能是只读或被别的'
                       '程序占用）。关掉那个窗口再试一次 —— 表还在，一个字没动。')
    text = str(exc)
    low = text.lower()
    if 'no space' in low or 'space' in low:
        return base + '磁盘满了。表还在，一个字没动。'
    if 'read-only' in low or 'readonly' in low:
        return base + '文件或目录是只读的。改成可写（或换一个位置）再试。'
    return f'{base}{text}（表还在，一个字没动）'


__all__ = ['KEEP_BACKUPS', 'REPLACE_ATTEMPTS', 'REPLACE_DELAY', 'RefusedWrite',
           'WORKBOOK_SUFFIXES', 'backup_file', 'human_io_error',
           'save_workbook_atomic', 'write_bytes_atomic', 'write_text_atomic']
