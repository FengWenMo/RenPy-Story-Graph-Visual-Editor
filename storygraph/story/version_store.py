# -*- coding: utf-8 -*-
"""
storygraph.story.version_store
==============================
**脚本（`.rpy`）的旧版本**：不留进 `game/`，而留进项目自己的
`.storygraph/versions/`。

## 为什么需要它（2026-11-08 第十一轮，探针 `.pip-tmp/probe_r11_rpy_backup.py`）

工具改 `.rpy` 有**两条**写路，退路一直不对称：

  · 整份导出（`generation_guard.write_generation`）→ `backup=True` →
    在文件旁边留 `<名>.rpy.bak`，最多 5 代。**有退路**。
  · 原地小改（改一条 `show` 的 `@at`、改一条源边、删一行源、换编排层…）
    → `write_text_atomic(…)` 不带备份。**一个字节的退路都没有**：
    内存里那套撤销一关窗就没了。

探针把这件事量了出来：同一个 `.rpy`，整份导出后目录里多出 `.rpy.bak`；
走原地小改那条路，目录里**一个备份都不多**。而原地小改恰恰是"拿刀切
手写剧本"的那条路 —— 改错一句，用户手里什么都没有。

## 为什么不干脆也给原地小改加 `backup=True`

因为原地小改**频率高得多**：一张卡改五次就是三代备份。堆在 `game/` 里
就是几十上百个 `*.rpy.bak`（`authoring/import_cleanup.py` 那个"清理导入残留"
入口就是为这种堆积写的）。所以：

  · **落点**：`<项目>/.storygraph/versions/<脚本相对 game/ 的目录>/`。
    `game/` 之外 —— Ren'Py 不扫，清理残留也不列它；
  · **命名**：还是 `atomicio` 那套（固定名 `<名>.bak` 是第一代、之后带
    时间戳 + 序号，最多 `KEEP_BACKUPS` 代），固定名那份永远不裁 ——
    它可能是"工具第一次动这份手写稿之前"的原件；
  · **自忽略**：目录里放一个 `.gitignore`（内容 `*`），用户 `git status`
    不会看见几千个备份。**不动**用户自己的 `.gitignore`；
  · **去重**：现在这份如果和手上最新的一代**逐字节一样**，就不再存 ——
    重写同一份内容不该烧掉一代（与 `ext.registry.save` 的 `unchanged`
    同一条口径）。

## 一条闸没变

放回旧版本走 `core.versions.restore`（锁里比对 `expect_current`、
写前先备份现在这份）。这个模块只负责"世代放哪儿"，不另写一套闸。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from storygraph.core import atomicio
from storygraph.core import versions as V
from storygraph.story.roots import project_root_of

#: 项目里放工具元数据的那一级（`layout.json` 也在里面）
HISTORY_ROOT = '.storygraph'
#: 旧版本放在元数据目录下的这一级
VERSIONS_DIR = 'versions'
#: 目录里那个自忽略文件的内容（让备份永远不进 `git status`）
IGNORE_TEXT = '*\n'

#: 只给脚本留旧版本：`.json` 清单的世代在文件旁边（第七~十轮已定），
#: `.xlsx` 是 `save_workbook_atomic` 自己的事。
SCRIPT_SUFFIX = '.rpy'

#: 除了"最近几代"，还按**年龄**留档：每个台阶留"最新的、已经比它更旧"的那一份。
#:
#: 为什么（2026-11-08 第十二轮，探针 `.pip-tmp/probe_r12_window.py`）：
#: 脚本是高频改的东西 —— 连改 30 次之后 5 代备份的时间跨度只有 **1.2 秒**；
#: 跨几天各改十次之后，"昨天关工具之前那一版"已经不在了。而用户真正的
#: 诉求往往是"回到昨天"，不是"回到十分钟前"。纯按次数裁剪对剧本不够用。
#:
#: ⚠ 为什么按**年龄**而不是"日历上的哪一天"：昨天关工具之前那一版，是在
#: **今天第一次保存时**才被写进备份的（那时它才被换掉），mtime 是今天。
#: 按日历分组会把"昨天那一版"算成今天的，一裁就没 —— 第一版就是这么写错的。
#:
#: 6 个台阶 → 每份脚本最多多 6 个备份文件（每个几十 KB），95 个脚本约 25 MB。
KEEP_AGES = (3600, 6 * 3600, 86400, 3 * 86400, 7 * 86400, 14 * 86400)


def _root_of(path: Path, project_root=None):
    """项目根（含 `game/` 的那一级）；推不出来 → `None`。"""
    if project_root:
        return Path(project_root)
    return project_root_of(path)


def history_dir(path, *, project_root=None):
    """
    这份脚本的旧版本该落在哪个目录；不是"项目 `game/` 下的 `.rpy`" → `None`。

    判据是**路径关系**，不是文件名猜测：必须能 `relative_to(<根>/game)`。
    于是 `build/` 里的产物、临时目录里的试写、Excel/JSON 都不会留下历史。
    """
    p = Path(str(path))
    if p.suffix.lower() != SCRIPT_SUFFIX:
        return None
    root = _root_of(p, project_root)
    if root is None:
        return None
    try:
        rel = p.resolve().relative_to((Path(root) / 'game').resolve())
    except (OSError, ValueError):
        return None
    return Path(root) / HISTORY_ROOT / VERSIONS_DIR / rel.parent


def ensure_ignored(where) -> bool:
    """
    往历史目录里放一个 `.gitignore`（`*`）—— 备份不打扰用户的 `git status`。

    已经有就不动（用户可能自己写过内容）；返回"这次是不是新写的"。
    `.gitignore` 忽略自己也是 `*` 的既定效果，不用特别处理。
    """
    path = Path(where) / '.gitignore'
    if path.is_file():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    atomicio.write_text_atomic(path, IGNORE_TEXT)
    return True


def _digest(path: Path) -> str:
    """文件的 sha1（读不出来 → `''`，让调用方当"不一样"处理）。"""
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except OSError:
        return ''       # 读不出来就当"不一样" → 宁多存一代，不少存


def _already_stored(src: Path, where) -> bool:
    """
    现在这份是不是**已经有某一代存着**（逐字节）。

    比的是**全部**世代（历史目录 + 文件旁边的老 `.rpy.bak`）而不是只看最新
    那一代：整份导出留下的 `.bak` 也是同一份内容，那就不必再存一遍。
    """
    want = _digest(src)
    if not want:
        return False
    for ver in versions_of(src, where=where):
        if ver.size == src.stat().st_size and _digest(ver.path) == want:
            return True
    return False


def _same_text(path: Path, text: str) -> bool:
    """
    磁盘上那份和 `text` 是不是**逐字节**一样（读不出来 → 当"不一样"）。

    单独一个函数是为了让"读不出来"这条分支写成 `return False` 而不是
    `pass` —— 全项目有条守卫专抓"`except: pass` 式静默吞异常"。
    """
    try:
        return atomicio.read_text_exact(path) == text
    except (OSError, UnicodeDecodeError):
        return False    # 读不出来就照常存一代（宁多不缺）


def snapshot(path, *, project_root=None, keep: int = atomicio.KEEP_BACKUPS,
             incoming=None):
    """
    把**现在这份**原话复制成新一代（在写盘之前调用）。

    返回新备份的路径；`None` = 不需要（不是项目里的 `.rpy`、文件还不存在、
    内容已经存过、或者 `incoming` 说明**这次写的就是现在这份**）。

    `incoming` 是"马上要写进去的文本"：给了就先比一下 —— 逐字节一样时
    这次写盘本来就是空转，不该白烧一代备份（`q4_after_identical` 那条）。

    **失败直接抛 `OSError`** —— 与 `atomicio` 同一条规矩：备份没成就不许
    覆盖（"用户以为有退路、其实没有"更坏）。
    """
    src = Path(str(path))
    where = history_dir(src, project_root=project_root)
    if where is None or not src.is_file():
        return None
    if incoming is not None and _same_text(src, incoming):
        return None
    if _already_stored(src, where):
        return None
    ensure_ignored(where)
    return atomicio.backup_file(src, keep=keep, where=where,
                                keep_ages=KEEP_AGES)


def versions_of(path, *, project_root=None, where=None) -> list:
    """
    这份脚本的**全部**旧版本，新的在前。

    两个来源都要列：历史目录（原地小改留下的）+ 文件旁边的老 `.bak`
    （整份导出 / 早期导入留下的）。少列一个来源，"我那份 `.rpy.bak` 呢"
    就会变成一次实地翻目录。
    """
    src = Path(str(path))
    here = history_dir(src, project_root=project_root) if where is None else where
    out = []
    if here is not None:
        out.extend(V.backups_of(src, where=here))
    out.extend(V.backups_of(src))
    out.sort(key=lambda v: (v.written, v.name), reverse=True)
    return out


def summarize_script(text) -> dict:
    """
    脚本某一代的"内容摘要"（历史版本列表那一列）：几行几字。

    `text is None` 只有一种情况 —— 这一代是"当时这份文件还不存在"。
    """
    if text is None:
        return {'ok': True, 'count': 0, 'missing': True,
                'note': '（这一版是空的：当时没有这份文件）'}
    lines = text.count('\n') + (0 if (not text or text.endswith('\n')) else 1)
    return {'ok': True, 'count': int(lines), 'missing': False,
            'note': f'{lines} 行 · {len(text)} 字'}


def retention_hint() -> str:
    """一句人话：脚本的旧版本到底留多少（界面与文档共用这一句）。"""
    steps = '、'.join(_age_label(s) for s in KEEP_AGES)
    return (f'最近 {atomicio.KEEP_BACKUPS} 次改动，'
            f'外加 {steps} 各留一版')


def _age_label(seconds: float) -> str:
    """台阶 → 人话（`3600 → 1 小时前`）。"""
    s = float(seconds)
    if s < 3600:
        return f'{int(s / 60)} 分钟前'
    if s < 86400:
        return f'{s / 3600:g} 小时前'
    return f'{s / 86400:g} 天前'


def history_root(project_root) -> Path:
    """这个项目的历史版本目录（**不管它在不在**）。"""
    return Path(project_root) / HISTORY_ROOT / VERSIONS_DIR


def _history_files(project_root) -> list:
    """
    历史目录里的**版本文件**（不含 `.gitignore`、不含符号链接、不含目录）。

    符号链接要跳过：删它没事，但如果它指向项目外面，`is_file()` 会跟进去 ——
    那就会把"清历史版本"变成"删别处的东西"。
    """
    base = history_root(project_root)
    try:
        if not base.is_dir():
            return []
    except OSError:
        return []
    out = []
    for path in sorted(base.rglob('*')):
        try:
            if path.is_symlink() or not path.is_file():
                continue
        except OSError:
            continue
        if path.name == '.gitignore':
            continue
        out.append(path)
    return out


def project_history(project_root) -> dict:
    """
    这个项目的历史版本**占了多少** → `{dir, files, bytes, scripts, days,
    oldest, newest}`。

    界面上要能说清"占多大、几个脚本、最早到哪天" —— 说不清，用户就只能
    自己去翻一个隐藏目录（而"隐藏"正是这套东西被设计成不打扰人的代价）。
    """
    base = history_root(project_root)
    files = _history_files(project_root)
    total, oldest, newest, scripts, days = 0, None, None, set(), set()
    for path in files:
        try:
            st = path.stat()
        except OSError:
            continue
        total += st.st_size
        oldest = st.st_mtime if oldest is None else min(oldest, st.st_mtime)
        newest = st.st_mtime if newest is None else max(newest, st.st_mtime)
        scripts.add((str(path.parent), path.name.split('.bak')[0]))
        days.add(V.stamp_text(st.st_mtime)[:10])
    return {'dir': str(base), 'files': len(files), 'bytes': total,
            'scripts': len(scripts), 'days': len(days),
            'oldest': oldest, 'newest': newest}


def clear_history(project_root) -> dict:
    """
    把这个项目的旧版本**全删掉** → `{removed, bytes, failed, dir}`。

    删之前要做的事（都是"不静默"那一条的延伸）：
      · 只删 `history_root()` 里认得的版本文件（符号链接、目录、`.gitignore` 都不动）；
      · 删完把空目录收掉，但 `.storygraph/` 本身留着（`layout.json` 在里面）；
      · 单个文件删不掉（被占用 / 只读）→ 记进 `failed`，不当中断。

    ⚠ 这一步是**单向**的：那些版本没有别处再存一份。界面必须先问过。
    """
    base = history_root(project_root)
    removed, total, failed = 0, 0, []
    for path in _history_files(project_root):
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        try:
            path.unlink()
        except OSError as e:
            failed.append((str(path), e))
            continue
        removed += 1
        total += size
    # 空目录收掉（从最深的开始），`history_root` 本身也收，但 `.storygraph` 留着
    for folder in sorted((p for p in base.rglob('*') if p.is_dir()),
                         key=lambda p: len(p.parts), reverse=True):
        _rmdir_if_empty(folder)
    _rmdir_if_empty(base)
    return {'removed': removed, 'bytes': total, 'failed': failed,
            'dir': str(base)}


def _rmdir_if_empty(folder: Path) -> bool:
    """空的就删掉这个目录；不空 / 删不掉都返回 False（**不是错误**）。"""
    try:
        if any(folder.iterdir()):
            return False
        folder.rmdir()
        return True
    except OSError:
        return False


def restore_version(path, version, *, expect_current=V.UNCHECKED,
                    project_root=None, lock_timeout=None) -> dict:
    """
    把某一代放回去；**新一代备份统一进历史目录**。

    为什么"统一"而不是"跟着这一代原来待的地方"：老机制在文件旁边留的
    `.rpy.bak` 是**只读的来源**（导入 / 整份导出留下的那几代）。放回它
    之后如果新一代又落回 `game/`，用户每放回一次，`game/` 就多一个
    `.bak` —— 那正是这个模块要避免的堆积。所以：**读**可以来自两处，
    **写**永远只进 `.storygraph/versions/`。
    """
    kwargs = {'expect_current': expect_current}
    if lock_timeout is not None:
        kwargs['lock_timeout'] = lock_timeout
    src = Path(str(path))
    here = history_dir(src, project_root=project_root)
    if here is not None:
        kwargs['where'] = here
    return V.restore(src, version, **kwargs)


#: 脚本那一套（列：历史目录 + 文件旁边；放回：新一代进历史目录）
SCRIPT_STORE = V.Store(versions_of, restore_version)


__all__ = ['HISTORY_ROOT', 'IGNORE_TEXT', 'KEEP_AGES', 'SCRIPT_STORE',
           'SCRIPT_SUFFIX', 'VERSIONS_DIR', 'clear_history', 'ensure_ignored',
           'history_dir', 'history_root', 'project_history', 'restore_version',
           'retention_hint', 'snapshot', 'summarize_script', 'versions_of']
