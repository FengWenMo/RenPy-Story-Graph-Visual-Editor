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
    return atomicio.backup_file(src, keep=keep, where=where)


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


__all__ = ['HISTORY_ROOT', 'IGNORE_TEXT', 'SCRIPT_STORE', 'SCRIPT_SUFFIX',
           'VERSIONS_DIR', 'ensure_ignored', 'history_dir', 'restore_version',
           'snapshot', 'summarize_script', 'versions_of']
