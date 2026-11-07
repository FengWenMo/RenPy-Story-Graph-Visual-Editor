# -*- coding: utf-8 -*-
"""
storygraph.story.roots
======================
**从一个路径反推项目根**（含 `game/` 的那一级）—— 全仓库只有这一份实现。

## 为什么单独一个模块（2026-11-08 第十一轮）

这两个函数原本住在 `story/paths.py`。第十一轮给脚本加"磁盘旧版本"时，
`story/version_store.py` 也需要"这个文件属于哪个项目"，于是它去 import
`paths` —— 而 `paths.write_text_atomic` 又要在写盘前调
`version_store.snapshot`，**两个模块互相 import，卷成一个环**
（`tools/debt_report.py` 当场报 `含函数内 import 的环：1 个`）。

搬到这里之后依赖是单向的：

    paths ──► version_store ──► roots
      └──────────────────────► roots

`paths` 里那两个名字保留成**转发**（老调用点的写法一个不用改）：
`project_root_of` 是公开名，`_guess_project_root` 是旧私有约定
（`generation_guard` / `excel_export` / `script_table` 都按这个名字取）。
"""

from __future__ import annotations

from pathlib import Path


def project_root_of(path):
    """
    从一个**产物路径**反推项目根（含 `game/` 的那一级）—— 公开入口。

    与 `guess_project_root` 是同一份实现：那个名字是**旧调用点的私有约定**
    （`story.excel_export` / `story.generation_guard` 按 `_guess_project_root`
    取它；`pipeline.engine_lint` 要做"项目声明过的自定义语句"豁免也要它），
    所以这里给一个**不带下划线**的公开名，而不是让别的包去取私有名
    （`tests/test_package_layering.py` 就是拦这个的）。
    """
    return guess_project_root(path)


def guess_project_root(out_path=None):
    """
    从一个**产物路径**反推项目根（含 `game/` 的那一级）。

    两处用途都要求它真的推得出来：
      · 缩略图定位（`export_beats_to_excel` 里给资源图库用）；
      · **生成门禁的 label 重名检查**（`write_beats_to_rpy` → `occupied_labels`）。

    2026-09-28 修：这个函数以前**恒返回 None**，于是 `occupied_labels(...)`
    永远拿到空集 —— `LABEL_COLLISION` 这条 block 级门禁**形同虚设**。
    它要拦的是"生成的 label 与项目其他 .rpy 重名"：Ren'Py 把 game/ 下
    所有 .rpy 当同一个脚本，重名 label 会让游戏直接起不来（真实事故形态）。

    推不出来（临时目录、产物不在任何含 game/ 的项目里）就返回 None：
    缩略图退回"多基准尝试"，门禁退回"不检查"——两者都安全，宁可少报。
    """
    if not out_path:
        return None
    try:
        path = Path(out_path).resolve()
    except (OSError, TypeError):
        return None
    for parent in path.parents:
        try:
            if (parent / 'game').is_dir():
                return parent
        except OSError:
            continue
    return None


__all__ = ['guess_project_root', 'project_root_of']
